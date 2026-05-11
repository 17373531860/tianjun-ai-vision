"""PluginRegistry — 插件运行时注册中心。

主程序在加载 active 插件时把 `PluginRegistry` 实例作为参数传给插件的
`register_plugin(app, registry, license_payload, host)`。插件用它把自己声明的
能力（router / hook / ORM 表 / 导出模板 等）注册到主程序。

设计原则:
- 每个子 registry 都是独立的小类，方便单元测试和单独演进
- 所有 register_* 方法都接受插件作者的对象，不做转换或包装
- 所有触发点用 `fire_*` 系列方法，异常 swallow + audit log，绝不影响主流程
- registry 实例与 active 插件强绑定，停用 / 卸载时由 PluginManager 调用 `clear()`

不在本期实现的能力（占位接口，先收集，运行时不真接到主程序）:
- export_templates: 等 F7 一起做
- export_fields:    等 F8 一起做
- realtime_triggers: 等 F9 一起做

API 与 plugins-examples/tier3-fullstack/backend/__init__.py 对齐，避免 demo 跑不通。
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, FastAPI


log = logging.getLogger("tianjun.plugin")


# ----------------------- routes -----------------------


class RoutesRegistry:
    """收集插件声明的 FastAPI router，挂到主程序 app 上。

    挂载路径约定（见 design/06_tier3_fullstack.md §四）：
        /api/v1/plugins/{customer_code}/{subpath}/...

    主程序无需关心插件路由的 path 细节，只通过 customer_code 隔离命名空间。
    """

    def __init__(self, app: FastAPI, customer_code: str, api_prefix: str = "/api/v1") -> None:
        self._app = app
        self._customer_code = customer_code
        self._api_prefix = api_prefix.rstrip("/")
        self._mounted: List[Dict[str, Any]] = []

    def include_router(self, router: APIRouter, subpath: str, tags: Optional[List[str]] = None) -> None:
        """把插件 router 挂到 `/api/v1/plugins/{cc}/{subpath}` 下。"""
        if not isinstance(router, APIRouter):
            raise TypeError("RoutesRegistry.include_router 只接受 fastapi.APIRouter 实例")
        sub = subpath.strip("/")
        prefix = f"{self._api_prefix}/plugins/{self._customer_code}/{sub}"
        tags_final = tags or [f"Plugin:{self._customer_code}"]
        self._app.include_router(router, prefix=prefix, tags=tags_final)
        self._mounted.append({"prefix": prefix, "tags": tags_final, "router_id": id(router)})
        log.info("[Plugin][%s] router mounted at %s", self._customer_code, prefix)

    def mounted(self) -> List[Dict[str, Any]]:
        return list(self._mounted)


# ----------------------- hooks -----------------------


HookHandler = Callable[[Dict[str, Any]], Any]


@dataclass
class HookEntry:
    hook_type: str
    phase: str
    when: str  # "pre" / "post"
    priority: int
    handler: HookHandler
    customer_code: str


class HooksRegistry:
    """收集插件 hook 注册，提供 fire() 触发点。

    G1 阶段只暴露 register/fire API；source.py 接入点（cycle_end/session_end/box_complete）
    留给 F1-F6 单独实施。fire() 本身已经实现「按 priority 排序 + 异常 swallow + audit log」。
    """

    def __init__(self, customer_code: str) -> None:
        self._customer_code = customer_code
        self._lock = threading.Lock()
        # key = (hook_type, phase, when) -> list[HookEntry]
        self._handlers: Dict[tuple, List[HookEntry]] = {}

    def register(
        self,
        hook_type: str,
        phase: str = "default",
        when: str = "post",
        priority: int = 100,
        handler: Optional[HookHandler] = None,
    ) -> None:
        if handler is None or not callable(handler):
            raise TypeError("HooksRegistry.register 需要 callable handler")
        entry = HookEntry(
            hook_type=hook_type,
            phase=phase,
            when=when,
            priority=priority,
            handler=handler,
            customer_code=self._customer_code,
        )
        key = (hook_type, phase, when)
        with self._lock:
            bucket = self._handlers.setdefault(key, [])
            bucket.append(entry)
            bucket.sort(key=lambda e: e.priority)
        log.info(
            "[Plugin][%s] hook registered: %s/%s/%s priority=%d",
            self._customer_code, hook_type, phase, when, priority,
        )

    def fire(self, hook_type: str, phase: str, when: str, ctx: Dict[str, Any]) -> List[Any]:
        """触发匹配的 hook，按 priority 顺序执行。

        - 每个 handler 异常被 swallow，错误写日志（不抛给上层）
        - 返回所有 handler 的返回值列表（用于 audit / 测试）
        - ctx 是引用传递，handler 可修改但**不**保证主流程使用修改
        """
        key = (hook_type, phase, when)
        with self._lock:
            entries = list(self._handlers.get(key, []))
        if not entries:
            return []
        results: List[Any] = []
        for entry in entries:
            try:
                result = entry.handler(ctx)
                results.append(result)
            except Exception as exc:
                log.warning(
                    "[Plugin][%s] hook %s/%s/%s 失败: %s",
                    entry.customer_code, entry.hook_type, entry.phase, entry.when, exc,
                )
                results.append({"_error": str(exc), "_customer_code": entry.customer_code})
        # 触发日志: cycle_id 是高基数, 只打 hook 类型 + handler 数, 避免日志洪水
        cycle_id = ctx.get("cycle_id") if isinstance(ctx, dict) else None
        log.info(
            "[Plugin][%s] hook fired: %s/%s/%s handlers=%d cycle_id=%s",
            self._customer_code, hook_type, phase, when, len(entries), cycle_id,
        )
        return results

    def list(self) -> List[HookEntry]:
        with self._lock:
            return [e for bucket in self._handlers.values() for e in bucket]


# ----------------------- tables -----------------------


class TablesRegistry:
    """收集插件声明的 ORM 表，必要时建表。

    约束 (design/06 §五):
    - 插件 ORM 类必须继承 `backend.db.database.Base`
    - `__tablename__` 必须以 `p_{customer_code}_` 开头
    - 类名建议 Plugin 前缀
    """

    def __init__(self, engine, customer_code: str) -> None:
        self._engine = engine
        self._customer_code = customer_code
        self._registered: List[str] = []
        self._tn_prefix = f"p_{customer_code.replace('-', '_')}_"

    def register(self, model_class) -> None:
        tn = getattr(model_class, "__tablename__", None)
        if not tn:
            raise ValueError("TablesRegistry.register: model 缺少 __tablename__")
        # 命名校验：tier3-fullstack demo 用 p_internal_demo_notes，与 internal-demo customer_code 对齐
        expect_prefix = self._tn_prefix
        if not tn.startswith(expect_prefix):
            log.warning(
                "[Plugin][%s] table %s 未遵循前缀 %s（demo 期可放行，生产请规范）",
                self._customer_code, tn, expect_prefix,
            )
        try:
            model_class.__table__.create(self._engine, checkfirst=True)
            self._registered.append(tn)
            log.info("[Plugin][%s] table created/ensured: %s", self._customer_code, tn)
        except Exception as exc:
            log.exception("[Plugin][%s] 建表 %s 失败: %s", self._customer_code, tn, exc)
            raise

    def registered(self) -> List[str]:
        return list(self._registered)


# ----------------------- export / realtime placeholders -----------------------


@dataclass
class _PlaceholderRegistry:
    """占位 registry，仅收集，不接到主程序。等 F7/F8/F9 接入。"""

    name: str
    customer_code: str
    items: List[Any] = field(default_factory=list)

    def register(self, *args, **kwargs) -> None:
        self.items.append({"args": args, "kwargs": kwargs})
        log.info(
            "[Plugin][%s] %s placeholder registered (count=%d) — 等 F7/F8/F9 接入",
            self.customer_code, self.name, len(self.items),
        )


# ----------------------- PluginRegistry -----------------------


class PluginRegistry:
    """单个 active 插件的 registry 总入口。"""

    def __init__(self, app: FastAPI, engine, customer_code: str) -> None:
        self.app = app
        self.engine = engine
        self.customer_code = customer_code
        self.routes = RoutesRegistry(app=app, customer_code=customer_code)
        self.hooks = HooksRegistry(customer_code=customer_code)
        self.tables = TablesRegistry(engine=engine, customer_code=customer_code)
        self.export_templates = _PlaceholderRegistry("export_templates", customer_code)
        self.export_fields = _PlaceholderRegistry("export_fields", customer_code)
        self.realtime_triggers = _PlaceholderRegistry("realtime_triggers", customer_code)

    def snapshot(self) -> Dict[str, Any]:
        """诊断快照：列出本插件注册的所有东西。"""
        return {
            "customer_code": self.customer_code,
            "routes": self.routes.mounted(),
            "hooks": [
                {"hook_type": h.hook_type, "phase": h.phase, "when": h.when, "priority": h.priority}
                for h in self.hooks.list()
            ],
            "tables": self.tables.registered(),
            "export_templates_count": len(self.export_templates.items),
            "export_fields_count": len(self.export_fields.items),
            "realtime_triggers_count": len(self.realtime_triggers.items),
        }


# ----------------------- PluginHost -----------------------


class PluginHost:
    """暴露给插件代码的"主程序 host"对象。

    设计目的：避免插件直接 import 主程序内部模块，给一个稳定的 facade。

    G1 阶段仅暴露:
    - get_db_session(): 返回受控 SQLAlchemy session（插件用完关）
    - customer_code:    当前插件 customer_code
    - plugin_dir:       插件解压目录
    - main_version:     主程序版本号
    """

    def __init__(self, customer_code: str, plugin_dir: str, main_version: str = "3.7.x") -> None:
        self.customer_code = customer_code
        self.plugin_dir = plugin_dir
        self.main_version = main_version

    def get_db_session(self):
        from backend.db.database import SessionLocal

        return SessionLocal()
