from __future__ import annotations

import importlib.util
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from backend._version import get_main_version
from backend.models.plugin_models import PluginAuditLog, PluginRecord, PluginState
from backend.plugin_system.registry import PluginHost, PluginRegistry
from backend.plugin_system.verifier import plugins_root, read_license_payload
from backend.plugin_system.version_check import check_main_version_compat


log = logging.getLogger("tianjun.plugin")


@dataclass
class LoadedPlugin:
    customer_code: str
    manifest: Dict[str, Any]
    module: Optional[ModuleType] = None
    registry: Optional[PluginRegistry] = None


class PluginManager:
    """单 active 插件加载与运行时状态。

    G1 改造:
    - load_active() 接受 app 引用，让 register_plugin 能拿到 4 参 (app/registry/license/host)
    - 加载成功后 registry.snapshot() 写入 audit log，便于诊断
    - 加载失败不影响主程序（main.py 调用方已经包了 try/except 兜底，再加一层日志）
    """

    def __init__(self) -> None:
        self.loaded: Optional[LoadedPlugin] = None
        self.registry: Optional[PluginRegistry] = None
        self._app = None  # 保留 app 引用，方便后续 plugin 反查

    def load_active(self, db: Session, app=None, main_version: Optional[str] = None) -> Optional[LoadedPlugin]:
        record = db.query(PluginRecord).filter(PluginRecord.is_active == True).first()
        if not record:
            return None

        # 主程序版本号: 默认从 electron/package.json 读 (唯一权威源).
        # 显式传值仅用于测试场景 (mock 不同版本验证 main_version_min/max 行为).
        effective_version = main_version if main_version is not None else get_main_version()
        # v3.15.4: 显式打印生效版本号 — 之前 0.0.0 误拒事故的直接定位点
        # (0.0.0 = 没读到 TIANJUN_APP_VERSION env, 打包后 package.json 进了 asar).
        log.info(
            "[Plugin][%s] 开始 startup_load: 主程序版本=%s, install_path=%s",
            record.customer_code, effective_version, record.install_path,
        )

        try:
            manifest = json.loads(record.manifest_json)
            install_dir = Path(record.install_path)
            if not install_dir.exists():
                raise FileNotFoundError(f"插件目录不存在: {install_dir}")

            # v3.13: main_version_min/max 强制校验.
            # manifest schema 早就有这两个字段 + min 是 required, 但 v3.7.0~v3.12.0
            # 加载流程一直没读, 改了底层后旧插件会带病加载. 这里在 register_plugin
            # 之前先把不兼容的拦截掉, 写 audit + state=incompatible_version.
            compatible, reason = check_main_version_compat(manifest, effective_version)
            if not compatible:
                self._set_state(
                    db,
                    record.customer_code,
                    "failed",
                    "error",
                    "PLUGIN_INCOMPATIBLE_VERSION",
                    reason,
                )
                self._audit(db, record.customer_code, "startup_load", "rejected", reason)
                db.commit()
                log.warning(
                    "[Plugin][%s] 拒绝加载 (版本不兼容): %s",
                    record.customer_code, reason,
                )
                return None

            license_payload = read_license_payload(db) if db is not None else {}
            # v3.13 M1.3a: 把 manifest.capabilities 传给 PluginHost, 主动 API
            # (trigger_alarm / mes_push / write_system_config) 在调用时强制校验.
            host = PluginHost(
                customer_code=record.customer_code,
                plugin_dir=str(install_dir),
                main_version=effective_version,
                capabilities=manifest.get("capabilities", []) or [],
            )

            module, registry = self._load_backend_module(
                customer_code=record.customer_code,
                install_dir=install_dir,
                app=app,
                license_payload=license_payload,
                host=host,
            )
            self.loaded = LoadedPlugin(record.customer_code, manifest, module=module, registry=registry)
            self.registry = registry
            self._app = app

            snapshot = registry.snapshot() if registry else {}
            self._set_state(db, record.customer_code, "loaded", "ok", None, None)
            self._audit(
                db,
                record.customer_code,
                "startup_load",
                "success",
                f"插件已加载: routes={len(snapshot.get('routes', []))} "
                f"hooks={len(snapshot.get('hooks', []))} "
                f"tables={len(snapshot.get('tables', []))}",
            )
            db.commit()
            log.info("[Plugin][%s] startup_load OK: %s", record.customer_code, snapshot)
            return self.loaded
        except Exception as exc:
            log.exception("[Plugin][%s] startup_load 失败: %s", record.customer_code, exc)
            self.loaded = None
            self.registry = None
            self._set_state(
                db,
                record.customer_code,
                "failed",
                "error",
                "PLUGIN_LOAD_FAILED",
                str(exc),
            )
            self._audit(db, record.customer_code, "startup_load", "failed", str(exc))
            db.commit()
            return None

    def _load_backend_module(
        self,
        customer_code: str,
        install_dir: Path,
        app,
        license_payload: Dict[str, Any],
        host: PluginHost,
    ) -> tuple[Optional[ModuleType], Optional[PluginRegistry]]:
        entry = install_dir / "backend" / "__init__.py"
        if not entry.exists():
            return None, None
        if app is None:
            raise RuntimeError("PluginManager.load_active 必须传入 FastAPI app 才能加载后端插件")

        module_name = f"tianjun_plugin_{customer_code.replace('-', '_')}"
        # 让 from .hooks import ... / from .models import ... 这种相对 import 能工作
        plugin_backend_dir = install_dir / "backend"
        if str(install_dir) not in sys.path:
            sys.path.insert(0, str(install_dir))

        spec = importlib.util.spec_from_file_location(
            module_name,
            entry,
            submodule_search_locations=[str(plugin_backend_dir)],
        )
        if spec is None or spec.loader is None:
            raise RuntimeError("无法构建插件后端模块")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        register = getattr(module, "register_plugin", None)
        if not callable(register):
            log.info("[Plugin][%s] 后端 __init__ 没有 register_plugin, 跳过 registry 注册", customer_code)
            return module, None

        from backend.db.database import engine

        registry = PluginRegistry(app=app, engine=engine, customer_code=customer_code)
        register(app, registry, license_payload, host)
        return module, registry

    @staticmethod
    def _set_state(
        db: Session,
        customer_code: str,
        runtime_status: str,
        health: str,
        error_code: Optional[str],
        error_message: Optional[str],
    ) -> None:
        state = db.query(PluginState).filter(PluginState.customer_code == customer_code).first()
        if not state:
            state = PluginState(customer_code=customer_code)
            db.add(state)
        state.runtime_status = runtime_status
        state.health = health
        state.last_error_code = error_code
        state.last_error_message = error_message

    @staticmethod
    def _audit(db: Session, customer_code: str, action: str, status: str, message: str) -> None:
        db.add(PluginAuditLog(
            customer_code=customer_code,
            action=action,
            status=status,
            message=message,
        ))


plugin_manager = PluginManager()


def load_active_plugin_on_startup(app=None) -> None:
    """供 main.py 启动时调用。

    `app` 在 main.py 创建完所有内置 router 之后传入；不传则插件后端不会加载
    （仅保留 DB 状态，避免在 app 还没就绪时崩主程序）。
    """
    from backend.db.database import SessionLocal

    plugins_root().mkdir(parents=True, exist_ok=True)
    db = SessionLocal()
    try:
        plugin_manager.load_active(db, app=app)
    finally:
        db.close()
