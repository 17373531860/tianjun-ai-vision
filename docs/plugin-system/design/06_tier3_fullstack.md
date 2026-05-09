# 06 — 档位 3：FastAPI router 自动注册 + 错误隔离（Full-stack Plugin）

> 适用版本：基于 `feat/plugin-config` v0.1
> 本文目的：把档位 3（全栈插件）的**后端加载机制**、**8 种 hook 的接入点**、**错误隔离策略**全部落到**可代码实现 + 可调用的程度**。
>
> 阅读前置：design 01（manifest）、design 02（验签）、design 03（DB）、inventory 01~03（模块依赖、数据流、扩展点）。
>
> 配套：design 07（打包）、design 08（示例）。

---

## 一、范围与目标

### 1.1 档位 3 在 1+2 之上多了什么

| 能力 | 档位 1 | 档位 2 | 档位 3 |
|---|---|---|---|
| 主题 / Logo / i18n / 隐藏菜单 | ✅ | ✅ | ✅ |
| 动态前端组件 / 路由 / store | ❌ | ✅ | ✅ |
| **FastAPI 路由注入** | ❌ | ❌ | ✅ |
| **MES Adapter 注册** | ❌ | ❌ | ✅ |
| **8 种 Hook 注册**（cycle_end/scan/event/alarm/...） | ❌ | ❌ | ✅ |
| **自家 ORM 表** | ❌ | ❌ | ✅ |
| **后台线程** | ❌ | ❌ | ✅ |
| **自定义导出 trigger / renderer** | ❌ | ❌ | ✅ |
| **自定义 export 字段 resolver** | ❌ | ❌ | ✅ |

### 1.2 典型用例

| 场景 | 复杂度 | manifest 字段 |
|---|---|---|
| 客户私有 MES 协议（MQTT 推送） | 3~5 天 | `backend.adapters` + `backend.routers` |
| ACME 自家 ERP 工单同步 + 自家报表后端 | 7~10 天 | `backend.routers` + `backend.tables` + `backend.background_threads` + `backend.hooks` |
| cycle_end 事件接收并写到自家审计日志 | 1~2 天 | `backend.hooks[type=cycle_end]` |
| scan 时校验工单是否在客户白名单 | 2~3 天 | `backend.hooks[type=scan_received]` + `backend.routers` |
| 报警时同步发钉钉 / 飞书 | 1~2 天 | `backend.hooks[type=alarm_trigger]` |

### 1.3 三条强约束

1. **不动主代码**：tier 3 插件**只能新增**——不能删除/替换/覆盖主程序的路由 / hook / adapter / 表
2. **错误必须隔离**：插件 hook 抛错 → 主调用链继续；插件路由抛 500 → 主程序 / 端口仍存活
3. **资源必须可释放**：后台线程、文件句柄、socket 等必须在卸载时 graceful 关闭

---

## 二、整体架构

### 2.1 主程序启动时序（含 tier 3 加载）

```
backend/main.py:
  1. import 全套 ORM 模块 (含 plugin_models)        ← design 03 §四
  2. Base.metadata.create_all                       ← 含 plugin 表
  3. migrate_database()                             ← 老库加列
  4. migrate_plugin_tables()                        ← 新增 4 张 plugin 表
  5. cleanup_plugin_audit_log()                     ← 90 天清理
  6. (NEW) plugin_manager = PluginManager(app, license_payload, db)
     ├─ scan_plugins_dir() 扫描 plugins/{cc}/
     ├─ verify_signatures()  ← design 02 §七
     ├─ load_active_plugin() ← 仅加载 state=active 的那一个
     │   ├─ 读 manifest
     │   ├─ tier ≥ 3 → 加载 backend.entry
     │   ├─ 调 register_plugin(app, registry, license_payload, host)
     │   ├─ register_plugin 内部:
     │   │   ├─ Base.metadata.create_all([plugin_tables])  ← design 03 §5.2
     │   │   ├─ register_adapter('plugin-acme-mqtt', MQTTAdapter)
     │   │   ├─ app.include_router(acme_router, prefix='/api/v1/plugins/acme')
     │   │   ├─ mes_hooks.register_phase_hook('cycle_end', 'after_workpiece_set_result', ...)
     │   │   ├─ start_background_threads()
     │   │   └─ apply_default_config(...)            ← design 03 §6.2
     │   └─ 写 plugin_state (registered_routes_count 等)
     └─ 持久化引用 (plugin_module 不能被 GC)

  7. include 主程序 routers (Batch 1 + Batch 2)     ← inventory 01 §3.4
     注: tier 3 路由在 step 6 已经 include_router 完成
        主程序路由这里 include 是为了顺序（先插件后主, 不冲突）
        但 swagger 文档顺序是先注册先出现
  8. uvicorn.run(app, ...)                          ← FastAPI 启动监听
```

> **顺序的关键点**：插件路由必须在主程序 ASGI 循环开始**之前**注册。FastAPI 不支持运行时 `app.include_router`（实际上能调，但 Swagger 不刷新且某些中间件状态错乱）。所以**唯一加载时机就是启动期**。

### 2.2 关机时序

```
1. SIGTERM / SIGINT / electron sendShutdownSignal
2. main.py 注册的 atexit / signal handler
3. plugin_manager.shutdown()                        ← NEW
   ├─ 调每个插件 hooks.shutdown(priority desc)
   ├─ 等待后台线程 join (最多 10 秒)
   ├─ 写 plugin_audit_log (event_type=shutdown)
   └─ 清空 plugin_module 引用 (允许 GC)
4. 主程序原有关机流程 (VideoSourceManager.stop / DB close)
5. uvicorn 关闭
```

### 2.3 与现有架构的连接点

| 连接点 | 现状（v3.6） | 插件接入方式 |
|---|---|---|
| FastAPI app | `backend/main.py` 顶层 `app = FastAPI(...)` | `register_plugin(app, ...)` 接收 |
| MES Adapter | `backend/services/mes_adapters/__init__.py` 有 `register_adapter()` | 插件直接 `from backend.services.mes_adapters import register_adapter` |
| MES Hook（scan/cycle_end 8 phases） | `backend/services/mes_hooks.py` `MESHookManager` | 加 `register_phase_hook` / `register_scan_hook` 类方法 |
| `_trigger_event` | `backend/api/source_event_trigger_mixin.py` | 加 `_event_pre_hooks` / `_event_post_hooks` 类级列表（inventory 03 §C2） |
| AlarmRouter | `backend/services/alarm_router.py` | 加 `register_alarm_hook` 类方法 |
| Detect Runners | `backend/source/mixins/detect_runners.py` | 加 runner registry（inventory 03 §B2） |
| Scanner Protocol | `backend/services/scanner.py` | 加 protocol registry（inventory 03 §B3） |
| Export Renderer | `backend/services/export.py` | 加 renderer registry（inventory 03 §B6） |
| Export Realtime Trigger | `backend/services/export_realtime.py` | 加 trigger registry（inventory 03 §B7） |
| Export Field Resolver | `backend/services/export_context.py` | 加 resolver registry（inventory 03 §B5） |
| ORM Base | `backend/db/database.py` `Base = declarative_base()` | 插件 `from backend.db.database import Base` 共享 |

> ⚠️ **inventory 03 列出的某些 registry 是"建议加"**——v3.7 必须在加载器之前先把这些 registry 加到主程序代码中。详见 §九"前置改造清单"。

---

## 三、PluginManager 完整实现

### 3.1 类骨架

```python
# backend/core/plugin_manager.py
"""插件加载与生命周期管理"""

import importlib
import importlib.util
import json
import logging
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI
from sqlalchemy.orm import Session

from backend.db.database import Base, engine, SessionLocal
from backend.models.plugin_models import (
    PluginInstall, PluginState, PluginAuditLog, PluginConfigVersion
)
from backend.core.plugin_verifier import PluginVerifier, calc_files_digest
from backend.core.plugin_registry import (
    BackendRegistry, register_phase_hook_global,
    HOOK_REGISTRY  # 见 §六
)

logger = logging.getLogger(__name__)


class PluginManager:
    """插件管理器（singleton）"""

    _instance: Optional["PluginManager"] = None

    def __init__(self, app: FastAPI, plugins_dir: Path, license_payload: dict):
        self.app = app
        self.plugins_dir = plugins_dir
        self.license_payload = license_payload
        self.verifier = PluginVerifier(public_keys=load_builtin_public_keys())

        self.active_plugin_module: Any = None
        self.active_plugin_cc: str | None = None
        self.active_registry: BackendRegistry | None = None

        self._load_lock = threading.Lock()
        self._shutdown_called = False

    @classmethod
    def get(cls) -> "PluginManager":
        if cls._instance is None:
            raise RuntimeError("PluginManager not initialized")
        return cls._instance

    @classmethod
    def init(cls, app: FastAPI, plugins_dir: Path, license_payload: dict) -> "PluginManager":
        if cls._instance is not None:
            return cls._instance
        cls._instance = cls(app, plugins_dir, license_payload)
        return cls._instance
```

### 3.2 启动 — scan + verify + load

```python
    def startup(self) -> None:
        """主程序启动时调用一次"""
        self._scan_and_register_plugins()
        self._load_active_plugin()
        self._record_audit("startup", None, {"loaded_cc": self.active_plugin_cc})

    def _scan_and_register_plugins(self) -> None:
        """扫描 plugins/ 目录, 把状态同步到 plugins 表"""
        if not self.plugins_dir.exists():
            logger.info("plugins/ 目录不存在")
            return

        with SessionLocal() as db:
            for sub in self.plugins_dir.iterdir():
                if not sub.is_dir():
                    continue
                cc = sub.name
                self._upsert_plugin_record(db, cc, sub)

    def _upsert_plugin_record(self, db: Session, cc: str, plugin_dir: Path):
        """读 manifest 并 upsert 一条 plugins 表记录"""
        manifest_path = plugin_dir / "plugin.json"
        if not manifest_path.exists():
            logger.warning(f"插件 {cc} 缺 plugin.json, 跳过")
            return

        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error(f"插件 {cc} manifest 解析失败: {e}")
            return

        # 验签 (每次启动都验)
        ok, reason = self.verifier.verify(plugin_dir, self.license_payload)
        signature_status = "ok" if ok else "invalid"

        existing = db.query(PluginInstall).filter_by(customer_code=cc).first()
        if existing:
            # 升级检测: manifest_sha256 变了或 plugin_version 变了
            new_sha = compute_manifest_sha(manifest_path)
            if existing.manifest_sha256 != new_sha:
                logger.info(f"插件 {cc} 内容变更, 重新登记")
                existing.plugin_version = manifest["plugin_version"]
                existing.manifest_json = manifest
                existing.manifest_sha256 = new_sha
                existing.signature_status = signature_status
                existing.error_msg = None if ok else reason
                existing.error_code = None if ok else reason
                # state 不重置, 让用户决定是否重新激活
        else:
            db.add(PluginInstall(
                customer_code=cc,
                name=manifest.get("name", cc),
                plugin_version=manifest["plugin_version"],
                tier=manifest["tier"],
                manifest_json=manifest,
                manifest_sha256=compute_manifest_sha(manifest_path),
                signature_status=signature_status,
                public_key_fingerprint=None,  # 来自 sig blob, 见 verifier
                signed_by=manifest.get("signed_by"),
                signed_at=manifest.get("signed_at"),
                install_path=str(plugin_dir.resolve()),
                state="installed" if ok else "failed",
                error_msg=None if ok else reason,
                error_code=None if ok else reason,
            ))
        db.commit()

    def _load_active_plugin(self) -> None:
        """加载 state=active 且 signature_status=ok 的唯一插件"""
        with self._load_lock:
            with SessionLocal() as db:
                p = db.query(PluginInstall).filter_by(state="active").first()
                if not p:
                    logger.info("无 active 插件")
                    return
                if p.signature_status != "ok":
                    logger.warning(f"插件 {p.customer_code} 验签失败, 跳过加载")
                    self._mark_failed(db, p, "PLUGIN_SIGNATURE_FAIL")
                    return

                self._do_load(db, p)

    def _do_load(self, db: Session, plugin_install: PluginInstall) -> None:
        cc = plugin_install.customer_code
        manifest = plugin_install.manifest_json
        plugin_dir = Path(plugin_install.install_path)

        t0 = time.perf_counter()
        try:
            # 1. tier=1 / tier=2: 仅前端, 后端没事可干
            if manifest["tier"] < 3:
                logger.info(f"插件 {cc} tier={manifest['tier']}, 跳过后端加载")
                self._mark_active(db, plugin_install, time.perf_counter() - t0)
                self.active_plugin_cc = cc
                return

            # 2. tier=3: 加载后端模块
            self._verify_and_install_dependencies(plugin_dir, manifest)
            plugin_module = self._import_plugin_module(plugin_dir, manifest)

            # 3. 创建 registry, 调 register_plugin
            registry = BackendRegistry(
                customer_code=cc,
                manifest=manifest,
                app=self.app,
            )
            host = self._build_host(cc, manifest)

            register_fn = getattr(plugin_module, "register_plugin", None)
            if not callable(register_fn):
                raise PluginLoadError("插件模块缺 register_plugin(app, registry, license, host) 函数")

            # 调用插件入口 (核心)
            register_fn(self.app, registry, self.license_payload, host)

            # 4. 应用 default_config (design 03 §6.2)
            apply_default_config(cc, manifest["plugin_version"],
                                 manifest.get("default_config") or {}, db)

            # 5. 标记 state=active + 持久化引用
            self.active_plugin_module = plugin_module
            self.active_plugin_cc = cc
            self.active_registry = registry
            self._mark_active(db, plugin_install,
                              load_duration_ms=int((time.perf_counter() - t0) * 1000),
                              registry=registry)

            logger.info(
                f"[Plugin] ✓ {cc} v{manifest['plugin_version']} 加载完成: "
                f"routes={registry.route_count}, "
                f"adapters={registry.adapter_count}, "
                f"hooks={registry.hook_count}, "
                f"tables={registry.table_count}"
            )

        except Exception as e:
            tb = traceback.format_exc()
            logger.error(f"[Plugin] {cc} 加载失败: {e}\n{tb}")
            self._mark_failed(db, plugin_install, str(e), tb)
            # 已部分注册的资源在这里没法回滚 — design §八卸载部分讨论
```

### 3.3 模块加载（重点：sys.modules 部分隔离）

```python
    def _import_plugin_module(self, plugin_dir: Path, manifest: dict):
        """加载插件后端入口模块

        策略:
        - 单独 module name (避免与主程序冲突)
        - 但 import 主程序 backend.* 时正常用 sys.modules (共享 Base / models)
        """
        cc = manifest["customer_code"]
        entry_path = plugin_dir / manifest["backend"]["entry"]
        if not entry_path.exists():
            raise PluginLoadError(f"backend entry 不存在: {entry_path}")

        # 给插件模块取唯一名字
        mod_name = f"plugin__{cc}"

        # 添加 plugin_dir 到 sys.path (临时)
        sys.path.insert(0, str(plugin_dir))
        try:
            spec = importlib.util.spec_from_file_location(
                mod_name, str(entry_path),
                submodule_search_locations=[str(entry_path.parent)],
            )
            module = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = module
            spec.loader.exec_module(module)
            return module
        finally:
            sys.path.pop(0)
```

> ⚠️ **不做完全 sys.modules 隔离**的理由：
> - 插件需要 `from backend.db.database import Base` 共享
> - 插件需要 `from backend.services.mes_adapters import register_adapter` 调用
> - 完全隔离会让插件无法共享 ORM 元数据 + 全局服务
>
> 这意味着：插件的全局变量与主程序共享 sys.modules，**插件崩溃可能影响主程序**——错误隔离靠 §七的 try/except 在调用边界做。

### 3.4 标记状态

```python
    def _mark_active(self, db: Session, p: PluginInstall,
                     load_duration_ms: int, registry: BackendRegistry | None = None):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        p.state = "active"
        p.activated_at = p.activated_at or now
        p.last_loaded_at = now
        p.error_msg = None
        p.error_code = None

        st = db.query(PluginState).filter_by(plugin_id=p.id).first()
        if not st:
            st = PluginState(plugin_id=p.id, customer_code=p.customer_code)
            db.add(st)
        st.last_load_attempt_at = now
        st.last_load_ok_at = now
        st.load_attempt_count += 1
        st.last_load_duration_ms = load_duration_ms
        st.last_error_code = None
        st.last_error_msg = None
        if registry:
            st.registered_routes_count = registry.route_count
            st.registered_adapters_count = registry.adapter_count
            st.registered_hooks_count = registry.hook_count
            st.registered_tables_count = registry.table_count
        db.commit()

    def _mark_failed(self, db: Session, p: PluginInstall,
                     error_code: str, error_msg: str = ""):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        p.state = "failed"
        p.error_code = error_code
        p.error_msg = error_msg[:5000]

        st = db.query(PluginState).filter_by(plugin_id=p.id).first()
        if not st:
            st = PluginState(plugin_id=p.id, customer_code=p.customer_code)
            db.add(st)
        st.last_load_attempt_at = now
        st.load_attempt_count += 1
        st.load_failure_count += 1
        st.last_error_code = error_code
        st.last_error_msg = error_msg[:5000]

        # 3 次连续失败 → quarantined
        if st.load_failure_count >= 3 and st.last_load_ok_at is None:
            p.state = "quarantined"
            logger.error(f"[Plugin] {p.customer_code} 连续 3 次加载失败, 隔离")

        db.commit()
        self._record_audit("load_fail", p.customer_code, {
            "error_code": error_code,
            "error_msg": error_msg[:1000],
        })
```

### 3.5 集成到 `main.py`

```python
# backend/main.py 在 step 5 后, step 7 前
from backend.core.plugin_manager import PluginManager
from backend.core.config import settings
from pathlib import Path

# 拿当前激活的 license payload (从 Electron 缓存或本地 license.dat 解析)
license_payload = load_active_license_payload()

plugin_manager = PluginManager.init(
    app=app,
    plugins_dir=Path(settings.PLUGINS_DIR),  # config 新加, e.g. data/plugins/
    license_payload=license_payload,
)
plugin_manager.startup()
```

---

## 四、register_plugin 接口契约

### 4.1 签名

```python
def register_plugin(
    app: FastAPI,
    registry: BackendRegistry,
    license_payload: dict,
    host: PluginHost,
) -> None:
    """插件后端入口

    Args:
        app: FastAPI 主实例 (插件 app.include_router 即可)
        registry: 插件注册表 (路由 / hook / adapter / table 命名空间封装)
        license_payload: 已验过的 license, {customerName, machineId, expireAt, ...}
        host: 主程序暴露的 API (常用工具函数 / 配置访问 / DB session 工厂)

    Raises:
        任何异常 → PluginManager 标 state=failed
    """
```

### 4.2 完整 ACME 示例

```python
# plugins/acme/backend/__init__.py
from fastapi import APIRouter, FastAPI
from sqlalchemy.orm import Session

from backend.services.mes_adapters import register_adapter
from .adapters.mqtt import MQTTAdapter
from .routes import router as acme_router
from .hooks import on_cycle_end_push_to_erp, on_scan_log_to_erp, on_startup_init_acme
from .threads import erp_poller_thread
from .models import PluginAcmeShift, PluginAcmeOrderExtra  # ORM 类


def register_plugin(app: FastAPI, registry, license_payload, host):
    """ACME 插件入口"""
    # 1. 创建自家 ORM 表 (registry 里管, 不要自己 create_all)
    registry.tables.register([PluginAcmeShift, PluginAcmeOrderExtra])

    # 2. 注册 MES adapter
    registry.adapters.register("plugin-acme-mqtt", MQTTAdapter)

    # 3. 挂 FastAPI router (acme_router 用相对前缀)
    registry.routes.register(acme_router, subpath="")  # → /api/v1/plugins/acme/*

    # 4. 注册 hooks
    registry.hooks.register("startup", on_startup_init_acme, priority=500)
    registry.hooks.register("cycle_end", on_cycle_end_push_to_erp,
                            phase="after_workpiece_set_result")
    registry.hooks.register("scan_received", on_scan_log_to_erp)

    # 5. 启动后台线程
    registry.threads.register("acme-erp-poller", erp_poller_thread)

    # 6. 自定义导出字段 resolver (可选)
    registry.export.register_field("plugin.acme.erp_id",
                                   lambda ctx: ctx.workpiece.serial_no.split('-')[-1])
```

### 4.3 BackendRegistry 详细实现

```python
# backend/core/plugin_registry.py
import threading
import logging
from typing import Callable
from fastapi import APIRouter, FastAPI

from backend.services.mes_adapters import register_adapter as _global_register_adapter
from backend.services.mes_adapters.base import BaseAdapter

logger = logging.getLogger(__name__)


class BackendRegistry:
    """档位 3 注册表

    收集插件声明的资源, 并通过命名空间约束 + 错误隔离落到主程序。
    """

    def __init__(self, customer_code: str, manifest: dict, app: FastAPI):
        self.customer_code = customer_code
        self.manifest = manifest
        self.app = app

        self._tables: list[type] = []
        self._adapters: list[str] = []
        self._routes_count = 0
        self._hooks: list[tuple[str, Callable]] = []  # (key, fn)
        self._threads: list[threading.Thread] = []
        self._field_resolvers: list[str] = []
        self._renderers: list[str] = []
        self._triggers: list[str] = []

        # 子 facade
        self.tables = TableRegistry(self)
        self.adapters = AdapterRegistry(self)
        self.routes = RouteRegistry(self)
        self.hooks = HookRegistry(self)
        self.threads = ThreadRegistry(self)
        self.export = ExportRegistry(self)

    @property
    def route_count(self): return self._routes_count
    @property
    def adapter_count(self): return len(self._adapters)
    @property
    def hook_count(self): return len(self._hooks)
    @property
    def table_count(self): return len(self._tables)


# ============ 子注册器 ============
class TableRegistry:
    def __init__(self, parent): self.parent = parent

    def register(self, classes):
        """注册自家 ORM 类列表"""
        from backend.db.database import Base, engine
        cc = self.parent.customer_code
        tables = []
        for cls in classes:
            tname = cls.__tablename__
            if not tname.startswith(f"p_{cc}_"):
                raise PluginLoadError(f"表名 {tname} 必须以 p_{cc}_ 开头")
            cname = cls.__name__
            if not cname.startswith(f"Plugin"):
                raise PluginLoadError(f"类名 {cname} 必须以 Plugin 开头")
            tables.append(cls.__table__)
            self.parent._tables.append(cls)
        Base.metadata.create_all(bind=engine, tables=tables)
        logger.info(f"[Plugin {cc}] 创建/确认 {len(tables)} 张表")


class AdapterRegistry:
    def __init__(self, parent): self.parent = parent

    def register(self, name: str, cls: type[BaseAdapter]):
        cc = self.parent.customer_code
        if not name.startswith(f"plugin-{cc}-"):
            raise PluginLoadError(f"adapter name {name} 必须以 plugin-{cc}- 开头")
        _global_register_adapter(name, cls)
        self.parent._adapters.append(name)
        logger.info(f"[Plugin {cc}] adapter {name}")


class RouteRegistry:
    def __init__(self, parent): self.parent = parent

    def register(self, router: APIRouter, subpath: str = ""):
        cc = self.parent.customer_code
        if subpath and not subpath.startswith("/"):
            subpath = "/" + subpath
        prefix = f"/api/v1/plugins/{cc}{subpath}"
        # 添加错误隔离中间件
        wrapped = self._wrap_router_errors(router, cc)
        self.parent.app.include_router(wrapped, prefix=prefix,
                                        tags=[f"plugin-{cc}"])
        # 计数 (router.routes 是 FastAPI 内部, 长度可信)
        self.parent._routes_count += len(router.routes)
        logger.info(f"[Plugin {cc}] router prefix={prefix}, routes={len(router.routes)}")

    def _wrap_router_errors(self, router: APIRouter, cc: str) -> APIRouter:
        """在 router 前面插一个 dependency, 任何异常被 plugin error reporter 捕获"""
        # 用 FastAPI 的 dependencies 添加观察者, 实际异常处理用 try/except 装饰每个 endpoint
        # 详见 §七错误隔离
        for route in router.routes:
            original_endpoint = route.endpoint
            route.endpoint = wrap_endpoint(original_endpoint, cc, route.path)
        return router


class HookRegistry:
    """注册 8 种 hook, 详见 §六"""
    HOOK_TYPES = {"startup", "shutdown", "cycle_end", "session_end",
                  "box_complete", "scan_received", "event_trigger", "alarm_trigger"}

    def __init__(self, parent): self.parent = parent

    def register(self, hook_type: str, fn: Callable, **kwargs):
        if hook_type not in self.HOOK_TYPES:
            raise PluginLoadError(f"未知 hook_type: {hook_type}")
        cc = self.parent.customer_code
        # 装饰 fn 加错误隔离 + 性能埋点
        wrapped = wrap_hook(fn, cc, hook_type, kwargs)
        # 落到对应 registry
        register_hook_to_global(hook_type, wrapped, **kwargs)
        self.parent._hooks.append((hook_type, fn))
        logger.info(f"[Plugin {cc}] hook {hook_type} {kwargs}")


class ThreadRegistry:
    def __init__(self, parent): self.parent = parent

    def register(self, name: str, target: Callable, args=(), kwargs=None):
        cc = self.parent.customer_code
        full_name = f"plugin-{cc}-{name}"
        runtime_max = self.parent.manifest.get("runtime", {}).get("background_thread_max_count", 3)
        if len(self.parent._threads) >= runtime_max:
            raise PluginLoadError(f"超过 runtime.background_thread_max_count={runtime_max}")
        # daemon=True 确保主程序退出时线程也退
        t = threading.Thread(target=wrap_thread(target, cc, full_name),
                             args=args, kwargs=kwargs or {},
                             name=full_name, daemon=True)
        t.start()
        self.parent._threads.append(t)
        logger.info(f"[Plugin {cc}] thread {full_name}")


class ExportRegistry:
    def __init__(self, parent): self.parent = parent

    def register_field(self, field: str, resolver: Callable):
        cc = self.parent.customer_code
        if not field.startswith(f"plugin.{cc}."):
            raise PluginLoadError(f"field {field} 必须以 plugin.{cc}. 开头")
        from backend.services.export_context import register_field_resolver
        register_field_resolver(field, resolver)
        self.parent._field_resolvers.append(field)

    def register_renderer(self, format: str, renderer_cls):
        cc = self.parent.customer_code
        full = f"plugin-{cc}-{format}"
        from backend.services.export import register_renderer
        register_renderer(full, renderer_cls)
        self.parent._renderers.append(full)

    def register_trigger(self, name: str, trigger_fn):
        cc = self.parent.customer_code
        full = f"plugin-{cc}-{name}"
        from backend.services.export_realtime import register_trigger
        register_trigger(full, trigger_fn)
        self.parent._triggers.append(full)
```

---

## 五、PluginHost — 主程序暴露给插件的 API

```python
# backend/core/plugin_host.py
from contextlib import contextmanager
from sqlalchemy.orm import Session

from backend.db.database import SessionLocal
from backend.models.models import SystemConfig


class PluginHost:
    """主程序暴露给插件的 API (后端侧)

    设计原则:
    - 提供少量、可控、不可变的工具
    - 插件需要的服务通过 host 访问, 不直接 import 主程序内部模块
    """

    def __init__(self, customer_code: str, manifest: dict, license_payload: dict):
        self.customer_code = customer_code
        self.manifest = manifest
        self.license_payload = license_payload

    @contextmanager
    def db_session(self):
        """获取 DB session (插件应该用这个, 不要直接 import SessionLocal)"""
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    def get_config(self, key: str) -> str | None:
        """读 SystemConfig (仅 plugin.{cc}.* 命名空间)"""
        if not key.startswith(f"plugin.{self.customer_code}."):
            raise PermissionError(f"key {key} 不在 plugin.{self.customer_code}.* 命名空间内")
        with self.db_session() as db:
            row = db.query(SystemConfig).filter_by(key=key).first()
            return row.value if row else None

    def set_config(self, key: str, value):
        if not key.startswith(f"plugin.{self.customer_code}."):
            raise PermissionError(f"...")
        # ... upsert ...

    @property
    def main_version(self) -> str:
        from backend.version import __version__
        return __version__

    def get_video_manager(self):
        """获取 VideoSourceManager (仅查询, 不要修改 mixin)"""
        from backend.api.source import get_video_manager
        return get_video_manager()

    # ⚠️ 不暴露 mes_hook_manager / scanner_service 直接引用
    # 这些通过 hook 接入, 不需要插件主动操控
```

---

## 六、8 种 Hook 的接入点详细

### 6.1 接入点总表

| Hook 类型 | 触发位置 | manifest 字段 | 详见 |
|---|---|---|---|
| `startup` | PluginManager.startup() 末尾，所有插件加载完后 | `priority` 决定顺序 | §6.2 |
| `shutdown` | 主程序关机前 | `priority` 倒序 | §6.3 |
| `cycle_end` | mes_hooks._handle_cycle_end 内 8 phases | `phase` + `when` | §6.4 |
| `session_end` | mes_hooks 增加 _handle_session_end | — | §6.5 |
| `box_complete` | mes_hooks 增加 _handle_box_complete | — | §6.6 |
| `scan_received` | mes_hooks.on_scan_received 内 | — | §6.7 |
| `event_trigger` | source_event_trigger_mixin._trigger_event 前后 | `when=pre/post` | §6.8 |
| `alarm_trigger` | alarm_router 触发前 | — | §6.9 |

### 6.2 startup hook

**注入点**：`PluginManager.startup()` 末尾。

```python
# backend/core/plugin_manager.py 内
def startup(self):
    # ... scan + load ...
    self._run_startup_hooks()

def _run_startup_hooks(self):
    hooks = HOOK_REGISTRY["startup"]
    hooks.sort(key=lambda h: -h.priority)  # 数字大的先跑
    for h in hooks:
        try:
            h.fn(host=self.active_host)
        except Exception as e:
            logger.error(f"[Plugin Hook] startup {h.cc} 失败: {e}")
            self._record_audit("hook_error", h.cc,
                                {"hook_type": "startup", "err": str(e)})
            # 启动 hook 失败不阻塞 (插件状态已 active, 但功能可能不全)
```

### 6.3 shutdown hook

**注入点**：现有 `backend/main.py` 已注册 atexit / signal handler，在那里加调用。

```python
# backend/main.py
@atexit.register
def _on_shutdown():
    # 现有: vsm.stop_capture / db close
    # NEW: 先调插件 shutdown hook
    try:
        plugin_manager = PluginManager.get()
        plugin_manager.shutdown()
    except Exception as e:
        logger.warning(f"[Plugin] shutdown 阶段异常: {e}")
    # 然后才走主程序原有关机
```

```python
def shutdown(self):
    if self._shutdown_called: return
    self._shutdown_called = True
    hooks = HOOK_REGISTRY["shutdown"]
    hooks.sort(key=lambda h: h.priority)  # 倒序: 数字大的先跑
    for h in hooks:
        try:
            h.fn(host=self.active_host)
        except Exception as e:
            logger.warning(f"[Plugin Hook] shutdown {h.cc} 失败: {e}")
    # 等后台线程 (最多 10 秒)
    if self.active_registry:
        for t in self.active_registry._threads:
            t.join(timeout=10.0 / max(1, len(self.active_registry._threads)))
            if t.is_alive():
                logger.warning(f"[Plugin] 线程 {t.name} 10s 未退出, 强制终止")
```

### 6.4 cycle_end hook（8 个 phase）

> 这是最重要的 hook，对应 inventory 02 数据流第 6 步。

#### 6.4.1 v3.7 主程序改造（前置）

`backend/services/mes_hooks.py` 现有 `_handle_cycle_end` 是个长函数（约 120 行），有 8 个内嵌"工序"（phases）：

```python
# 现状: 单函数 (inventory 02 §六)
def _handle_cycle_end(self, db, channel_id, cycle_id, ...):
    # phase 1: 工件 result 写入
    # phase 2: 缺陷记录
    # phase 3: 计数器
    # phase 4: 工单计数
    # phase 5: 集群上报
    # phase 6: 实时导出
    # phase 7: MES Gateway 推送
    # phase 8: 完成统计
```

**v3.7 改造**：把这 8 个 phase 显式抽出，并在每个 phase 前后留 hook 调用点：

```python
# backend/services/mes_hooks.py (v3.7)
class MESHookManager:
    # 类级 phase hook 注册表
    _phase_hooks: dict[str, dict[str, list]] = defaultdict(
        lambda: {"before": [], "after": []}
    )

    @classmethod
    def register_phase_hook(cls, phase: str, when: str, fn: Callable):
        """注册一个 phase hook
        phase: 'extract_inspecting' / 'workpiece_set_result' / 'defect_record' /
               'counter_update' / 'order_count' / 'cluster_report' /
               'export_realtime' / 'mes_gateway_push'
        when: 'before' / 'after'
        """
        cls._phase_hooks[phase][when].append(fn)

    def _run_phase_hooks(self, phase: str, when: str, ctx: CycleEndContext):
        for fn in self._phase_hooks[phase][when]:
            try:
                fn(ctx)
            except Exception as e:
                logger.error(f"[Plugin Phase Hook] {phase} {when}: {e}")

    def _handle_cycle_end(self, db, channel_id, cycle_id, ...):
        ctx = CycleEndContext(db=db, channel_id=channel_id, cycle_id=cycle_id, ...)

        for phase, work_fn in [
            ("extract_inspecting",  self._phase_extract_inspecting),
            ("workpiece_set_result", self._phase_workpiece_set_result),
            ("defect_record",        self._phase_defect_record),
            ("counter_update",       self._phase_counter_update),
            ("order_count",          self._phase_order_count),
            ("cluster_report",       self._phase_cluster_report),
            ("export_realtime",      self._phase_export_realtime),
            ("mes_gateway_push",     self._phase_mes_gateway_push),
        ]:
            self._run_phase_hooks(phase, "before", ctx)
            try:
                work_fn(ctx)
            except Exception as e:
                logger.error(f"[MES Hook] phase {phase} 失败: {e}")
            self._run_phase_hooks(phase, "after", ctx)
```

#### 6.4.2 CycleEndContext 数据结构

```python
@dataclass
class CycleEndContext:
    db: Session
    channel_id: int
    cycle_id: int
    is_good: bool
    event_name: str | None
    serial_no: str | None
    workpiece: MESWorkpiece | None
    project_id: int
    operator_id: int | None
    counters: dict
    inspecting: dict
    timestamp: datetime
    # 通过 ctx.aux 给插件挂自家临时数据
    aux: dict = field(default_factory=dict)
```

> ⚠️ **`ctx.workpiece` 在 phase=`workpiece_set_result` 之前可能是 None**——插件 hook 要根据 phase 时序判断。

#### 6.4.3 插件接入示例

```python
# plugins/acme/backend/hooks.py
def on_cycle_end_push_to_erp(ctx: CycleEndContext):
    """工件 result 写入后, 同步推到 ACME ERP"""
    if not ctx.workpiece or not ctx.workpiece.serial_no:
        return
    erp_url = ctx.host.get_config("plugin.acme.erp_url")
    requests.post(erp_url, json={...}, timeout=2.0)
```

manifest 中：

```json
"hooks": [
  {"type": "cycle_end", "phase": "workpiece_set_result", "when": "after",
   "module": "backend.hooks", "function": "on_cycle_end_push_to_erp"}
]
```

### 6.5 session_end hook

> `session_end` 在 inventory 02 标记 `[todo]` —— 当前主程序**没有触发点**！

#### 6.5.1 v3.7 主程序改造

`backend/api/sessions.py` `stop_session` 接口在 session 结束时调（已有），但**没有发出 hook 事件**。需要加：

```python
# backend/api/sessions.py
@router.post("/{id}/stop")
def stop_session(id: int, db: Session = Depends(get_db)):
    s = db.query(DetectionSession).get(id)
    s.end_time = datetime.utcnow()
    db.commit()
    # NEW: 触发 session_end hook
    from backend.services.mes_hooks import MESHookManager
    mhm = MESHookManager.get_instance()
    ctx = SessionEndContext(db=db, session=s, ...)
    mhm._run_hooks("session_end", ctx)
```

> 此改造**也是 inventory 05 §SESSION-1 列出的 [todo]，本来就要做**——插件需要这个事件。

### 6.6 box_complete hook

类似 session_end，主程序在 `box_aggregations` 表插入完整记录时（cluster collector 的 box_complete 分支）触发：

```python
# backend/services/cluster_collector.py
def _on_box_complete(self, box_serial: str, ...):
    # 现有: 插入 box_aggregations + 推送 MES
    # NEW: 触发 box_complete hook
    ctx = BoxCompleteContext(box_serial=box_serial, ...)
    MESHookManager.get_instance()._run_hooks("box_complete", ctx)
```

### 6.7 scan_received hook

`backend/services/mes_hooks.py:381 on_scan_received` 已存在。改造：

```python
def on_scan_received(self, channel_id, serial_no, ...):
    # 现有逻辑
    ctx = ScanReceivedContext(channel_id=channel_id, serial_no=serial_no, ...)
    # NEW: 在主逻辑前调 pre hook
    self._run_hooks("scan_received", ctx, when="pre")

    # ... 现有逻辑 (创建 workpiece / inject scanner / ...) ...

    self._run_hooks("scan_received", ctx, when="post")
```

### 6.8 event_trigger hook

`backend/api/source_event_trigger_mixin.py:19 _trigger_event` —— 改造（inventory 03 §C2 已有详细方案）：

```python
class EventTriggerMixin:
    _event_pre_hooks: list = []
    _event_post_hooks: list = []

    @classmethod
    def register_pre_hook(cls, fn): cls._event_pre_hooks.append(fn)
    @classmethod
    def register_post_hook(cls, fn): cls._event_post_hooks.append(fn)

    def _trigger_event(self, event_id, reason: str) -> bool:
        ctx = EventTriggerContext(self, event_id, reason)
        for fn in self._event_pre_hooks:
            try:
                # pre hook 可以 cancel 事件
                result = fn(ctx)
                if result is False:
                    return False
            except Exception as e:
                logger.error(f"[Plugin Event Hook] pre: {e}")

        # 现有逻辑
        ok = self._do_trigger_event(event_id, reason)

        for fn in self._event_post_hooks:
            try:
                fn(ctx, ok)
            except Exception as e:
                logger.error(f"[Plugin Event Hook] post: {e}")
        return ok
```

### 6.9 alarm_trigger hook

`backend/services/alarm_router.py` 改造：

```python
class AlarmRouter:
    _alarm_hooks: list = []

    @classmethod
    def register_alarm_hook(cls, fn): cls._alarm_hooks.append(fn)

    def trigger_alarm(self, event_name: str, ...):
        ctx = AlarmTriggerContext(event_name=event_name, ...)
        for fn in self._alarm_hooks:
            try:
                # 插件可以 cancel 报警 (如客户夜间不报警)
                if fn(ctx) is False:
                    logger.info(f"[Plugin] 报警被插件 cancel: {event_name}")
                    return
            except Exception as e:
                logger.error(f"[Plugin Alarm Hook]: {e}")
        # 现有逻辑
```

### 6.10 全局 HOOK_REGISTRY

```python
# backend/core/plugin_registry.py
from collections import defaultdict
from typing import Callable
from dataclasses import dataclass


@dataclass
class HookEntry:
    cc: str
    hook_type: str
    fn: Callable
    priority: int = 500
    phase: str | None = None
    when: str | None = None


HOOK_REGISTRY: dict[str, list[HookEntry]] = defaultdict(list)


def register_hook_to_global(hook_type: str, fn: Callable, **kwargs):
    """统一入口, 内部分发到不同子注册表"""
    cc = kwargs.pop("_cc", "<unknown>")
    entry = HookEntry(cc=cc, hook_type=hook_type, fn=fn,
                      priority=kwargs.get("priority", 500),
                      phase=kwargs.get("phase"),
                      when=kwargs.get("when"))
    HOOK_REGISTRY[hook_type].append(entry)

    # 分发到具体子系统
    if hook_type == "cycle_end":
        from backend.services.mes_hooks import MESHookManager
        MESHookManager.register_phase_hook(entry.phase, entry.when or "after", fn)
    elif hook_type == "scan_received":
        from backend.services.mes_hooks import MESHookManager
        MESHookManager.register_scan_hook(entry.when or "post", fn)
    elif hook_type == "event_trigger":
        from backend.api.source_event_trigger_mixin import EventTriggerMixin
        if entry.when == "pre":
            EventTriggerMixin.register_pre_hook(fn)
        else:
            EventTriggerMixin.register_post_hook(fn)
    elif hook_type == "alarm_trigger":
        from backend.services.alarm_router import AlarmRouter
        AlarmRouter.register_alarm_hook(fn)
    elif hook_type == "session_end":
        from backend.services.mes_hooks import MESHookManager
        MESHookManager.register_session_end_hook(fn)
    elif hook_type == "box_complete":
        from backend.services.cluster_collector import ClusterCollector
        ClusterCollector.register_box_complete_hook(fn)
    # startup / shutdown 不分发, PluginManager 直接遍历 HOOK_REGISTRY
```

---

## 七、错误隔离

### 7.1 五层防线

```
┌──────────────────────────────────────────────────────────────┐
│ L1: 加载阶段 (_do_load 内 try/except)                        │
│  插件 import 报错 / register 报错 → state=failed             │
├──────────────────────────────────────────────────────────────┤
│ L2: 端点调用 (wrap_endpoint 装饰)                            │
│  插件路由抛错 → 返回 500 + 上报 audit, 不阻塞主程序          │
├──────────────────────────────────────────────────────────────┤
│ L3: Hook 调用 (wrap_hook 装饰)                               │
│  hook 抛错 → log + audit + 跳过, 主流程继续                  │
├──────────────────────────────────────────────────────────────┤
│ L4: 后台线程 (wrap_thread 装饰)                              │
│  线程抛错 → log + audit + 线程退出, 不影响其他              │
├──────────────────────────────────────────────────────────────┤
│ L5: 资源限制                                                 │
│  文件句柄 / 数据库 session / GPU 内存 / CPU                  │
│  软监控, 阈值告警 (design 03 §2.2 health metrics)           │
└──────────────────────────────────────────────────────────────┘
```

### 7.2 wrap_endpoint

```python
# backend/core/plugin_registry.py
import functools
import inspect
from fastapi import HTTPException


def wrap_endpoint(endpoint, cc: str, path: str):
    """包装 FastAPI endpoint 加错误隔离"""
    is_async = inspect.iscoroutinefunction(endpoint)

    if is_async:
        @functools.wraps(endpoint)
        async def async_wrapper(*args, **kwargs):
            try:
                return await endpoint(*args, **kwargs)
            except HTTPException:
                raise  # FastAPI 主动抛的, 直接传
            except Exception as e:
                logger.exception(f"[Plugin {cc}] endpoint {path} 异常: {e}")
                _record_runtime_error(cc, "endpoint", path, e)
                raise HTTPException(500, "plugin endpoint failed")
        return async_wrapper
    else:
        @functools.wraps(endpoint)
        def sync_wrapper(*args, **kwargs):
            try:
                return endpoint(*args, **kwargs)
            except HTTPException:
                raise
            except Exception as e:
                logger.exception(f"[Plugin {cc}] endpoint {path} 异常: {e}")
                _record_runtime_error(cc, "endpoint", path, e)
                raise HTTPException(500, "plugin endpoint failed")
        return sync_wrapper


def _record_runtime_error(cc, stage, target, e):
    """异步写 audit_log"""
    import threading
    def _do():
        with SessionLocal() as db:
            db.add(PluginAuditLog(
                customer_code=cc,
                event_type="runtime_error",
                event_detail={
                    "stage": stage,
                    "target": str(target),
                    "error": str(e)[:1000],
                    "stack": traceback.format_exc()[:5000],
                },
            ))
            db.commit()
    threading.Thread(target=_do, daemon=True).start()
    # 同时更新 plugin_state.hook_failure_count 等
```

### 7.3 wrap_hook（含超时检测）

```python
def wrap_hook(fn, cc: str, hook_type: str, opts: dict):
    """包装 hook 函数加错误隔离 + 超时监控"""
    max_ms = opts.get("max_duration_ms") or 5000

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        t0 = time.perf_counter()
        try:
            result = fn(*args, **kwargs)
            elapsed_ms = (time.perf_counter() - t0) * 1000
            if elapsed_ms > max_ms:
                logger.warning(
                    f"[Plugin {cc}] hook {hook_type} 耗时 {elapsed_ms:.0f}ms 超阈值 {max_ms}ms"
                )
            _update_hook_metrics(cc, hook_type, elapsed_ms, ok=True)
            return result
        except Exception as e:
            logger.exception(f"[Plugin {cc}] hook {hook_type} 失败: {e}")
            _record_runtime_error(cc, "hook", hook_type, e)
            _update_hook_metrics(cc, hook_type, 0, ok=False)
            # **关键: 不抛出异常** —— 主流程继续
            return None
    return wrapper
```

### 7.4 wrap_thread

```python
def wrap_thread(target, cc: str, name: str):
    """包装后台线程, 顶层异常捕获 + 重启策略"""
    @functools.wraps(target)
    def wrapper(*args, **kwargs):
        try:
            target(*args, **kwargs)
        except Exception as e:
            logger.exception(f"[Plugin {cc}] thread {name} 异常退出: {e}")
            _record_runtime_error(cc, "thread", name, e)
            # **不重启** — 让插件作者处理崩溃逻辑
    return wrapper
```

> ⚠️ 不做"thread 自动重启"。原因：自动重启的副作用太多（资源泄漏 / 死循环 / 串行 IO 互斥），交给插件 hook 自己重新启动逻辑或 supervisor 模式。

### 7.5 性能监控

`plugin_state` 表 `hook_invocation_count` / `hook_failure_count` / `hook_avg_duration_ms` 由 `_update_hook_metrics` 异步更新。

Settings 页可看：

```
[Plugin acme] 30 days metrics
  Hooks invoked:   124,830
  Hooks failed:    12 (0.01%)
  Avg duration:    23 ms
  Threads alive:   2/3
  CPU last 1min:   8%
  Memory:          312 MB
```

### 7.6 软告警阈值（design 01 §3.9）

PluginManager 启一个后台监控线程（或复用 `cycle_end` hook），每分钟采样一次：

```python
def _sample_plugin_health(self):
    if not self.active_registry: return
    cc = self.active_plugin_cc
    threads = self.active_registry._threads
    alive = sum(1 for t in threads if t.is_alive())
    runtime = self.active_registry.manifest.get("runtime", {})
    if alive < len(threads):
        logger.warning(f"[Plugin {cc}] 后台线程 {len(threads) - alive} 个已退出")
    # 不强 kill, 仅写 plugin_state.background_thread_count
```

---

## 八、卸载 / 隔离 / 重新加载

### 8.1 卸载策略

> **核心限制**：FastAPI 不支持动态删除 routes（要重建 app）。所以"卸载"在主程序运行期**不真正释放路由**。

| 操作 | 行为 |
|---|---|
| 用户在 Settings 点"禁用" | 改 plugins.state=disabled，**提示重启**应用 |
| 用户点"卸载" | 删 plugins/{cc}/ 目录 + 改 state=installed (如保留)，**提示重启** |
| 用户点"卸载并清空数据" | 上 + DROP TABLE p_{cc}_*，**提示重启** |
| 主程序进程内立即效果 | 路由仍在，但 `state≠active` 时 Settings 页只显示"已禁用"，前端不再加载该插件 UI |

### 8.2 卸载实现

```python
# PluginManager
def disable_plugin(self, cc: str):
    """禁用插件 — 路由仍在但前端不会加载"""
    with SessionLocal() as db:
        p = db.query(PluginInstall).filter_by(customer_code=cc).first()
        if p:
            p.state = "disabled"
            db.commit()
        self._record_audit("disable", cc, {})

def uninstall_plugin(self, cc: str, drop_tables: bool = False):
    # 先 disable
    self.disable_plugin(cc)
    # 调 shutdown hook (如果是 active)
    if self.active_plugin_cc == cc:
        self._run_shutdown_hooks_for(cc)
    # 删自家表 (design 03 §5.4)
    with SessionLocal() as db:
        from backend.core.plugin_migrate import drop_plugin_tables
        if drop_tables:
            drop_plugin_tables(db, cc)
        # 删 plugin.{cc}.* SystemConfig
        # 删 plugins / plugin_state 行
    # 删目录
    plugin_dir = self.plugins_dir / cc
    if plugin_dir.exists():
        import shutil
        shutil.rmtree(plugin_dir, ignore_errors=True)
    self._record_audit("uninstall", cc, {"drop_tables": drop_tables})
```

### 8.3 隔离（quarantined）

3 次连续加载失败自动进入 quarantined：

```python
# _mark_failed 内
if st.load_failure_count >= 3 and st.last_load_ok_at is None:
    p.state = "quarantined"
```

quarantined 状态下：
- 启动时**不再尝试加载**
- Settings 页显示"已隔离 — 错误代码 + 详情"
- 用户手动点"重置隔离"恢复成 installed 状态

```python
def unquarantine_plugin(self, cc: str):
    with SessionLocal() as db:
        p = db.query(PluginInstall).filter_by(customer_code=cc).first()
        if p and p.state == "quarantined":
            p.state = "installed"
            st = db.query(PluginState).filter_by(plugin_id=p.id).first()
            if st:
                st.load_failure_count = 0
            db.commit()
    self._record_audit("unquarantine", cc, {})
```

---

## 九、主程序前置改造清单（v3.7 必做）

> 这些改造**必须在 PluginManager 上线之前完成**，否则插件无法接入。

| # | 改造 | 文件 | 工作量 |
|---|---|---|---|
| F1 | `_handle_cycle_end` 拆 8 phase + register_phase_hook | `backend/services/mes_hooks.py` | 1.5d |
| F2 | session_end 触发点 | `backend/api/sessions.py` + `mes_hooks.py` | 0.5d |
| F3 | box_complete 触发点 | `backend/services/cluster_collector.py` + `mes_hooks.py` | 0.5d |
| F4 | scan_received pre/post hook | `backend/services/mes_hooks.py:381` | 0.5d |
| F5 | event_trigger pre/post hook | `backend/api/source_event_trigger_mixin.py` | 0.5d |
| F6 | alarm_trigger hook | `backend/services/alarm_router.py` | 0.5d |
| F7 | export_realtime 自定义 trigger registry | `backend/services/export_realtime.py` | 0.5d |
| F8 | export 自定义 renderer registry | `backend/services/export.py` | 0.5d |
| F9 | export_context 自定义 field resolver registry | `backend/services/export_context.py` | 0.5d |
| F10 | router/Layout 接入 (design 05) | frontend | 1d |
| F11 | PluginManager 自身 + plugin_registry | `backend/core/*.py` 新增 | 3d |
| F12 | 插件 API 端点 (manifest / assets / runtime-error) | `backend/api/plugins.py` 新增 | 1d |
| F13 | Settings 页插件管理 UI | `frontend/src/views/Settings/index.vue` | 2d |
| F14 | BUG-1 修复（_fix_db_paths 表名）| `backend/core/config.py:43` | ✅ 2026-05-09 完成（0.05d 实际） |
| ~~F15~~ | ~~INCONSIST-2 修复（mes_gateway 路径）~~ | ✅ 2026-05-09 验证为误报 | 0d |

**合计**：约 13 天（与 design 00 milestone 估算一致）

---

## 十、API 端点（插件管理）

```python
# backend/api/plugins.py 完整路由
GET    /api/v1/plugins/                 列出所有插件 (Settings 页用)
GET    /api/v1/plugins/{id}             单个插件详情
POST   /api/v1/plugins/{id}/activate    激活插件 (state→active, 提示重启)
POST   /api/v1/plugins/{id}/disable     禁用 (state→disabled)
POST   /api/v1/plugins/{id}/unquarantine 解除隔离
DELETE /api/v1/plugins/{id}             卸载 (?drop_tables=true)
POST   /api/v1/plugins/install          上传 .tjvplugin (multipart)
GET    /api/v1/plugins/{id}/audit       审计日志 (90 天)

GET    /api/v1/plugins/active/manifest  当前激活插件 manifest (前端启动用, design 04)
GET    /api/v1/plugins/active/assets/{path:path}  静态资源代理 (design 04)
POST   /api/v1/plugins/runtime-error    前端错误上报 (design 05)
```

详细实现 design 07 § 上传插件流程会写。

---

## 十一、测试策略

### 11.1 单元测试

| 测试 | 覆盖 |
|---|---|
| PluginManager.startup 无插件 | OK 不报错 |
| PluginManager.startup 有 1 个 tier=1 | 加载 OK, state=active |
| PluginManager.startup 有 1 个 tier=3 + register_plugin OK | OK |
| PluginManager.startup 有 1 个 tier=3 + register_plugin 抛错 | state=failed |
| BackendRegistry.adapters.register 命名空间不对 | 抛 PluginLoadError |
| BackendRegistry.tables.register 表名前缀不对 | 抛 PluginLoadError |
| BackendRegistry.routes.register 路由前缀拼装 | 正确 |
| HOOK_REGISTRY 注册多个 startup hook | 按 priority 排序 |
| wrap_hook 时长超阈值 → 警告 | 是 |
| wrap_hook 抛错 → 返回 None | 是 |
| wrap_endpoint 抛 HTTPException → 透传 | 是 |
| wrap_endpoint 抛普通异常 → 500 + audit | 是 |
| wrap_thread 抛错 → 线程退出 + audit | 是 |
| 3 次连续加载失败 → quarantined | 是 |

### 11.2 集成测试

| 场景 | 期望 |
|---|---|
| 完整 ACME tier=3 插件加载 | adapter / router / hook / table 全注册 OK |
| 同 ACME 卸载 | DB 清, 路由保留, 重启后真正消失 |
| 重新启动加载 | OK 持久化 |
| ACME hook 抛错 | cycle_end 主流程不影响, 日志有 |
| ACME router 抛错 | 该端点 500, 其他主程序端点正常 |
| ACME 后台线程崩溃 | 日志, 主程序继续, 其他线程不受影响 |
| 加载未签名插件 | 拒绝 |
| 加载 customer_code 与 license 不一致 | 拒绝 |

### 11.3 性能基准

| 场景 | 期望 |
|---|---|
| PluginManager.startup（无插件）| ≤ 50 ms |
| PluginManager.startup（tier=3 完整 ACME）| ≤ 1 s |
| 单个 cycle_end + 3 个插件 hook | ≤ 50 ms（hook 本身的开销 ≤ 5 ms） |
| 单个 scan_received + 1 个插件 hook | ≤ 20 ms |
| FastAPI endpoint 经 wrap_endpoint | overhead ≤ 1 ms |

---

## 十二、与现有架构的兼容承诺

### 12.1 现有 mes_hooks.py 的 API 不破坏

`MESHookManager.on_scan_received` / `_handle_cycle_end` 等公共方法**签名不变**。新加的 `register_phase_hook` / `register_scan_hook` 是**额外**类方法。

### 12.2 现有 mes_adapters/__init__.py 不变

`register_adapter` 函数已存在（line 29~30），插件**直接 import 用**：

```python
from backend.services.mes_adapters import register_adapter
register_adapter("plugin-acme-mqtt", MQTTAdapter)
```

### 12.3 ORM Base 共用

```python
# 插件代码:
from backend.db.database import Base  # ← 与主程序同一个 Base

class PluginAcmeShift(Base):
    __tablename__ = "p_acme_shifts"
    ...
```

主程序 `Base.metadata.create_all` 会**看到**插件表（registry.tables.register 内调用 create_all 时已注册）。

---

## 十三、待确认与开放点

| 议题 | 当前态度 |
|---|---|
| **B1** 主程序前置改造 F1 拆 phase 是否破坏现有 cycle_end | 不破坏（外部行为一致），需要测试覆盖 |
| **B2** export_realtime 的 session_end / box_complete trigger | inventory 02 标 [todo]，本节顺便实装 |
| **B3** 是否给插件提供 axios-like HTTP client | 不（让插件用 stdlib `requests`） |
| **B4** 是否做插件 GPU 资源监控 | 不（v3.7），软警告即可 |
| **B5** 是否支持插件→插件之间通信 | 不（v3.7 单插件激活，无意义） |
| **B6** 是否记录 hook 调用栈 | 不（性能开销大，仅错误时 capture） |
| **B7** 卸载是否要清理插件创建的临时文件 | 由插件 shutdown hook 自己处理（提供 host.plugin_data_dir） |
| **B8** 是否限制插件后端 ImportError 类型 | 不（导入失败就是失败，不细分） |

---

## 十四、本文决策摘要

| 决策点 | 值 |
|---|---|
| PluginManager 单例 | ✅ |
| 加载时机 | startup（main.py 步骤 5/6） |
| 关机时机 | atexit / signal 注册的 shutdown hook |
| 模块隔离方式 | 部分（独立 mod_name + 共享 sys.modules） |
| ORM Base | 共用主程序 `Base` |
| 路由注册前缀 | `/api/v1/plugins/{cc}` |
| Hook 注入点（8 种） | startup/shutdown/cycle_end(8 phases)/session_end/box_complete/scan_received(pre+post)/event_trigger(pre+post)/alarm_trigger |
| Hook 错误隔离 | wrap_hook 装饰，抛错→log+audit+return None |
| 端点错误隔离 | wrap_endpoint，HTTPException 透传，普通异常→500+audit |
| 线程错误隔离 | wrap_thread，抛错→log+audit+thread 退出，**不自动重启** |
| 3 次失败隔离 | state=quarantined 自动 |
| FastAPI 不支持动态删 route | 卸载策略：标 disabled，提示重启 |
| 软资源限制 | 警告级（不强 kill） |
| 主程序前置改造工作量 | 12.5 天（INCONSIST-2 误报撤销 -0.5d；BUG-1 已修） |
| Adapter 注册 | 复用现有 `register_adapter`（已存在）|
| Hook timeout 默认 | 5000 ms（仅警告） |

---

**本文最后更新**：2026-05-08
**事实校验**：基于 `backend/services/mes_hooks.py` / `mes_adapters/__init__.py` / `source_event_trigger_mixin.py` / `main.py` 现状
**下一文档**：design/07_distribution.md（pack-plugin.py + sign-plugin.py + 安装包流程）
