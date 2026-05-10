from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from backend.models.plugin_models import PluginAuditLog, PluginRecord, PluginState
from backend.plugin_system.verifier import plugins_root


@dataclass
class LoadedPlugin:
    customer_code: str
    manifest: Dict[str, Any]
    module: Optional[ModuleType] = None


class PluginManager:
    """单 active 插件加载骨架。

    本期只在启动时尝试加载 active 插件；加载失败写入状态，不影响主程序。
    """

    def __init__(self) -> None:
        self.loaded: Optional[LoadedPlugin] = None

    def load_active(self, db: Session) -> Optional[LoadedPlugin]:
        record = db.query(PluginRecord).filter(PluginRecord.is_active == True).first()
        if not record:
            return None

        try:
            manifest = json.loads(record.manifest_json)
            install_dir = Path(record.install_path)
            if not install_dir.exists():
                raise FileNotFoundError(f"插件目录不存在: {install_dir}")

            module = self._load_backend_module(record.customer_code, install_dir)
            self.loaded = LoadedPlugin(record.customer_code, manifest, module=module)
            self._set_state(db, record.customer_code, "loaded", "ok", None, None)
            self._audit(db, record.customer_code, "startup_load", "success", "插件已加载")
            db.commit()
            return self.loaded
        except Exception as exc:
            self.loaded = None
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

    def _load_backend_module(self, customer_code: str, install_dir: Path) -> Optional[ModuleType]:
        entry = install_dir / "backend" / "__init__.py"
        if not entry.exists():
            return None

        module_name = f"tianjun_plugin_{customer_code.replace('-', '_')}"
        spec = importlib.util.spec_from_file_location(module_name, entry)
        if spec is None or spec.loader is None:
            raise RuntimeError("无法构建插件后端模块")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        register = getattr(module, "register_plugin", None)
        if callable(register):
            register({"plugin_dir": str(install_dir), "customer_code": customer_code})
        return module

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


def load_active_plugin_on_startup() -> None:
    from backend.db.database import SessionLocal

    plugins_root().mkdir(parents=True, exist_ok=True)
    db = SessionLocal()
    try:
        plugin_manager.load_active(db)
    finally:
        db.close()
