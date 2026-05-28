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
from dataclasses import dataclass
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


class PluginNotImplementedError(NotImplementedError):
    """插件试图调用主程序尚未接入的 registry API.

    与原生 NotImplementedError 区分, 让外层 try/except 能精准捕获 (避免误吞
    Python 标准库其它 NotImplementedError). PluginManager 加载流程会把它转成
    audit log + state=failed, 不影响主程序启动.
    """


class PluginRuntimeError(RuntimeError):
    """插件主动 API 安全约束违反 (capability 未声明 / 命名空间越权 / 参数非法).

    与 PluginNotImplementedError 区分:
      - NotImplementedError = 主程序还没做这个能力, 等里程碑
      - RuntimeError        = 主程序做了, 但插件没遵守契约 (差声明 / 用了不该用的 key)

    设计目标: 让插件作者**在测试阶段就看到错**, 而不是在客户现场偷偷越权.
    主动 API (PluginHost.trigger_alarm / mes_push / write_system_config) 会抛这个,
    上层 try/except 应能精准捕获后写 audit log + 拒绝调用, 不影响主程序.
    """


@dataclass
class _UnimplementedRegistry:
    """尚未接入主程序的 registry — 调用即抛 ``PluginNotImplementedError``.

    背景:
      v3.7.0 ~ v3.12.0 版本 export_templates / export_fields / realtime_triggers 三个
      registry 是 "善意的谎言" 占位实现 (调用成功 / 写日志 / 但主程序根本不接).
      插件作者写代码时以为生效, 实际客户机上注册的内容对主程序导出 / 实时规则毫无
      影响 -- 这是非常严重的认知陷阱.

      v3.13 起改为 fail-fast: 调用即抛, 让插件作者立刻在加载阶段看到错, 而不是
      在客户现场跑出 "怎么这功能没生效" 的灵异 bug.

    对插件作者的迁移建议:
      - 等主程序接入 (见 docs/plugin-system/CHANGELOG.md F7/F8/F9 进度)
      - 临时用 hooks.register("cycle_end", ...) 在 hook 里做导出 / 实时推送绕路
      - 在 manifest capabilities 里**不要**声明 backend.export.* / backend.realtime.*
    """

    name: str
    customer_code: str
    not_yet_milestone: str  # 例 "F7" / "F8" / "F9", 写进异常便于溯源

    def register(self, *args, **kwargs) -> None:
        msg = (
            f"[Plugin][{self.customer_code}] registry.{self.name}.register(...) "
            f"尚未接入主程序 (等 {self.not_yet_milestone} 里程碑). "
            f"v3.7.0~v3.12.0 该接口曾是占位 (调用成功但不生效), v3.13+ 改为 fail-fast 防止"
            f"插件作者误以为生效. 临时用 hooks.register('cycle_end', ...) 绕路."
        )
        log.warning("[Plugin][%s] %s.register 被拒绝: %s", self.customer_code, self.name, msg)
        raise PluginNotImplementedError(msg)


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
        self.export_templates = _UnimplementedRegistry("export_templates", customer_code, "F7")
        self.export_fields = _UnimplementedRegistry("export_fields", customer_code, "F8")
        self.realtime_triggers = _UnimplementedRegistry("realtime_triggers", customer_code, "F9")

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
            # 三个 registry v3.13 起 fail-fast (调用即抛), 任何 manifest 显式声明
            # 用了它们的插件都会在 register_plugin 阶段被拒绝, 不会抵达 snapshot.
            "export_templates_status": "not_implemented_F7",
            "export_fields_status": "not_implemented_F8",
            "realtime_triggers_status": "not_implemented_F9",
        }


# ----------------------- PluginHost -----------------------


class PluginHost:
    """暴露给插件代码的"主程序 host"对象。

    设计目的: 避免插件直接 ``import backend.models.*`` 或玩主程序内部 ORM,
    给一个**稳定的查询 facade**. 主程序改 ORM 字段时, 这层 facade 负责把变化
    吸收掉 (映射到稳定字段名), 插件不感知; 真正不能吸收的字段变化, 改 facade
    本身的契约 + 升 plugin SDK 版本, 走 main_version_min/max 拦截.

    G1 阶段 (v3.7.0~v3.12.0) 暴露:
    - get_db_session(): 返回受控 SQLAlchemy session（插件用完关）
    - customer_code:    当前插件 customer_code
    - plugin_dir:       插件解压目录
    - main_version:     主程序版本号

    v3.13 起新增稳定查询 API (返回 dict snapshot, 不是 ORM 对象):
    - query_session(session_id): session 元数据快照
    - query_cycle(cycle_id):     cycle 元数据快照
    - query_step(step_id):       step 元数据快照
    - query_workpiece(wp_id):    workpiece 元数据快照

    v3.13 M1.3a 起新增主动 API (插件 → 主程序方向):
    - trigger_alarm(channel_id, event_type, reason):  调用主程序 AlarmRouter
    - mes_push(event_type, payload, channel_id):      走主程序 MES Gateway 推送
    - read_system_config(key):                        读主程序 SystemConfig 表
    - write_system_config(key, value, description):   写主程序 SystemConfig 表

    M1.3a stub (实现等里程碑):
    - write_plugin_step_field(...):   等 M3.3 (step_records.plugin_data JSON 字段)
    - broadcast_to_channel_group(...): 等 RFC 10 (工位组主程序原生)

    为何返回 dict 而不是 ORM 对象:
    1. ORM 对象绑定 session — 插件 close session 后再访问字段会炸 DetachedInstanceError
    2. ORM 字段改名/删字段时插件不感知, dict facade 可以做兼容映射
    3. 强制插件以"数据传输对象 (DTO)"思路写代码, 不要把主程序数据库结构当 SDK

    get_db_session() 保留**不删** (向后兼容, 老插件可能在用), 但**新插件不应再用**.
    docs/plugin-system/design/06_tier3_fullstack.md 已加迁移指南.

    主动 API 安全模型 (M1.3a 契约):
    1. **capabilities 声明门槛**: 写动作 / 外推动作必须在 manifest.capabilities 声明
       对应 capability, 否则抛 PluginRuntimeError. 让插件作者明确说出"我要做什么",
       避免 hooks 里偷偷调主程序高危 API.
    2. **命名空间隔离**: write_system_config / mes_push 的 key/event_type 必须用
       `plugin_<customer_code>_` 前缀, 防止插件踩主程序保留 key (如 license-cache).
    3. **审计落库**: 所有写 / 外推动作都进 plugin_audit_log, 客户现场翻日志可追溯.
    4. **错误隔离**: 主动 API 抛 PluginRuntimeError 时, 上层 hook handler 已经被
       fire_plugin_hook 包了 try/except, 主程序流程不会被插件越权操作连带断裂.
    """

    def __init__(
        self,
        customer_code: str,
        plugin_dir: str,
        main_version: str = "3.7.x",
        capabilities: Optional[List[str]] = None,
    ) -> None:
        self.customer_code = customer_code
        self.plugin_dir = plugin_dir
        self.main_version = main_version
        # capabilities = manifest.capabilities 列表的拷贝, 默认空 (=没声明任何能力).
        # 未声明能力 → 调对应主动 API 抛 PluginRuntimeError.
        # 兼容: G1 期不接收 capabilities 的老调用 (test / 极旧 manager) → 等价空列表.
        self.capabilities = list(capabilities or [])

    def get_db_session(self):
        """返回原始 SQLAlchemy session.

        ⚠️ 历史 API, 向后兼容保留. 新插件请改用 query_* facade 系列.
        直接用 session 的风险: 主程序 ORM 字段改名 → 插件代码运行时炸.
        """
        from backend.db.database import SessionLocal

        return SessionLocal()

    # ============================================================
    # v3.13: 稳定查询 facade
    # 字段集合是契约的一部分, 改字段名 = 升 plugin SDK 版本 (走 main_version_min/max 拦截)
    # ============================================================

    def query_session(self, session_id: int) -> Optional[Dict[str, Any]]:
        """查 DetectionSession 快照, 找不到返回 ``None``.

        稳定字段 (v3.13 契约):
            id / session_uuid / name / project_id / start_time / end_time /
            status / total_cycles / good_cycles / ng_cycles /
            avg_cycle_time / min_cycle_time / max_cycle_time / counters_snapshot
        """
        from backend.db.database import SessionLocal
        from backend.models.models import DetectionSession

        db = SessionLocal()
        try:
            row = db.query(DetectionSession).filter(DetectionSession.id == session_id).first()
            if row is None:
                return None
            return {
                "id": row.id,
                "session_uuid": row.session_uuid,
                "name": getattr(row, "name", None),
                "project_id": row.project_id,
                "start_time": row.start_time.isoformat() if row.start_time else None,
                "end_time": row.end_time.isoformat() if row.end_time else None,
                "status": row.status,
                "total_cycles": row.total_cycles,
                "good_cycles": row.good_cycles,
                "ng_cycles": row.ng_cycles,
                "avg_cycle_time": row.avg_cycle_time,
                "min_cycle_time": row.min_cycle_time,
                "max_cycle_time": row.max_cycle_time,
                "counters_snapshot": dict(row.counters_snapshot) if row.counters_snapshot else {},
            }
        finally:
            db.close()

    def query_cycle(self, cycle_id: int) -> Optional[Dict[str, Any]]:
        """查 DetectionCycle 快照, 找不到返回 ``None``.

        稳定字段 (v3.13 契约):
            id / cycle_uuid / cycle_number / session_id / start_time / end_time /
            duration / is_good / event_id / event_name / result_reason /
            step_sequence
        """
        from backend.db.database import SessionLocal
        from backend.models.models import DetectionCycle

        db = SessionLocal()
        try:
            row = db.query(DetectionCycle).filter(DetectionCycle.id == cycle_id).first()
            if row is None:
                return None
            return {
                "id": row.id,
                "cycle_uuid": row.cycle_uuid,
                "cycle_number": row.cycle_number,
                "session_id": row.session_id,
                "start_time": row.start_time.isoformat() if row.start_time else None,
                "end_time": row.end_time.isoformat() if row.end_time else None,
                "duration": row.duration,
                "is_good": row.is_good,
                "event_id": row.event_id,
                "event_name": row.event_name,
                "result_reason": row.result_reason,
                "step_sequence": list(row.step_sequence) if row.step_sequence else [],
            }
        finally:
            db.close()

    def query_step(self, step_id: int) -> Optional[Dict[str, Any]]:
        """查 StepRecord 快照, 找不到返回 ``None``.

        稳定字段 (v3.13 契约):
            id / record_uuid / cycle_id / step_id / step_label / step_name /
            step_order / start_time / end_time / duration / interval_to_next /
            is_valid / confidence
        """
        from backend.db.database import SessionLocal
        from backend.models.models import StepRecord

        db = SessionLocal()
        try:
            row = db.query(StepRecord).filter(StepRecord.id == step_id).first()
            if row is None:
                return None
            return {
                "id": row.id,
                "record_uuid": row.record_uuid,
                "cycle_id": row.cycle_id,
                "step_id": row.step_id,
                "step_label": row.step_label,
                "step_name": row.step_name,
                "step_order": row.step_order,
                "start_time": row.start_time.isoformat() if row.start_time else None,
                "end_time": row.end_time.isoformat() if row.end_time else None,
                "duration": row.duration,
                "interval_to_next": row.interval_to_next,
                "is_valid": row.is_valid,
                "confidence": row.confidence,
            }
        finally:
            db.close()

    def query_workpiece(self, workpiece_id: int) -> Optional[Dict[str, Any]]:
        """查 Workpiece 快照, 找不到返回 ``None``.

        稳定字段 (v3.13 契约):
            id / serial_no / raw_barcode / order_id / batch_id / project_id /
            channel_id / status / final_result / inspection_count / operator /
            registered_at / first_inspect_at / last_inspect_at /
            created_at / updated_at

        注: ``final_result`` 是 ORM 真实字段名 (workpieces.final_result), 不要在
        facade 里改成 ``result`` — 与 hook ctx 中的 ``result`` (= "OK"/"NG"
        cycle 级判定) 是两回事, 改名会让插件作者混淆.
        """
        from backend.db.database import SessionLocal
        from backend.models.mes_models import Workpiece

        db = SessionLocal()
        try:
            row = db.query(Workpiece).filter(Workpiece.id == workpiece_id).first()
            if row is None:
                return None
            return {
                "id": row.id,
                "serial_no": row.serial_no,
                "raw_barcode": row.raw_barcode,
                "order_id": row.order_id,
                "batch_id": row.batch_id,
                "project_id": row.project_id,
                "channel_id": row.channel_id,
                "status": row.status,
                "final_result": row.final_result,
                "inspection_count": row.inspection_count,
                "operator": row.operator,
                "registered_at": row.registered_at.isoformat() if row.registered_at else None,
                "first_inspect_at": row.first_inspect_at.isoformat() if row.first_inspect_at else None,
                "last_inspect_at": row.last_inspect_at.isoformat() if row.last_inspect_at else None,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }
        finally:
            db.close()

    # ============================================================
    # v3.13 M1.3a: 主动 API (插件 → 主程序)
    # 安全模型: capabilities 声明 + 命名空间隔离 + audit log + 错误隔离
    # ============================================================

    # ---------- 内部辅助 ----------

    def _require_capability(self, cap: str) -> None:
        """检查插件 manifest.capabilities 是否声明了该能力.

        未声明 → PluginRuntimeError + audit log.
        让插件作者**显式声明**才能调危险 API, 避免在 hook 里偷偷越权.
        """
        if cap not in self.capabilities:
            self._audit_log(
                action=f"capability_check.{cap}",
                status="rejected",
                message=f"未声明 capability {cap}, 拒绝调用",
            )
            raise PluginRuntimeError(
                f"[Plugin][{self.customer_code}] 拒绝调用: 未在 manifest.capabilities "
                f"声明 '{cap}'. 在 manifest.json 的 capabilities 数组里加上 '{cap}' 后重启."
            )

    def _require_plugin_namespace(self, key: str, field_name: str = "key") -> None:
        """检查 key 是否以 `plugin_<customer_code>_` 前缀开头.

        防止插件踩主程序保留 key (例 license-cache / display.monitor.ptMode),
        也防止两个客户插件互相覆盖配置.
        """
        expect_prefix = f"plugin_{self.customer_code.replace('-', '_')}_"
        if not isinstance(key, str) or not key.startswith(expect_prefix):
            self._audit_log(
                action="namespace_check",
                status="rejected",
                message=f"{field_name}={key!r} 不在命名空间 {expect_prefix}* 内",
            )
            raise PluginRuntimeError(
                f"[Plugin][{self.customer_code}] 拒绝调用: {field_name}={key!r} "
                f"必须以 '{expect_prefix}' 开头 (命名空间隔离)."
            )

    def _audit_log(self, action: str, status: str, message: str) -> None:
        """主动 API 审计落库 (plugin_audit_log 表).

        独立 session (insert + commit + close) — 不依赖调用方 session.
        任何异常 swallow + 打日志, 不让 audit 失败拖垮主流程.
        """
        try:
            from backend.db.database import SessionLocal
            from backend.models.plugin_models import PluginAuditLog

            db = SessionLocal()
            try:
                db.add(PluginAuditLog(
                    customer_code=self.customer_code,
                    action=action,
                    status=status,
                    message=message,
                ))
                db.commit()
            finally:
                db.close()
        except Exception as exc:
            # audit 失败不影响主动 API 本身的返回值 / 异常
            log.warning(
                "[Plugin][%s] audit_log 写库失败 (已隔离): action=%s err=%s",
                self.customer_code, action, exc,
            )

    # ---------- 主动 API ----------

    def trigger_alarm(self, channel_id: int, event_type: str, reason: str = "") -> bool:
        """触发主程序报警 (走 AlarmRouter).

        参数:
            channel_id: 工位通道号 (0 起)
            event_type: 报警事件类型, 必须是主程序 alarm.config 已配置的字符串
                        (常见: "event1"="OK光", "event2"="NG警报" 等).
                        ⚠️ event_type **不**强制 plugin_ 前缀 — 因为报警事件类型是
                        主程序级 (灯柱配置), 插件只能复用, 不能定义新 event_type.
            reason:     reason 字符串, 仅用于 audit log, 不影响实际报警动作.

        返回:
            True  = 已成功调用 alarm_router (实际硬件动作由 AlarmManager 异步执行).
            False = 调用过程异常 (audit 已写, 不抛, 让 hook handler 继续).

        安全约束:
            - 需要 manifest.capabilities 声明 'runtime.alarm_trigger'.
            - 多工位场景 channel_id 必须在 ChannelManager 已注册的范围内
              (主程序 alarm_router.get(channel_id) 会自动按工位号路由共享灯柱).
        """
        self._require_capability("runtime.alarm_trigger")
        try:
            from backend.api.alarm import alarm_router
            alarm_router.trigger_alarm(event_type, channel_id=channel_id)
            self._audit_log(
                action="trigger_alarm",
                status="success",
                message=f"channel_id={channel_id} event_type={event_type} reason={reason!r}",
            )
            return True
        except Exception as exc:
            self._audit_log(
                action="trigger_alarm",
                status="failed",
                message=f"channel_id={channel_id} event_type={event_type} err={exc}",
            )
            log.warning(
                "[Plugin][%s] trigger_alarm 异常 (已 swallow): %s",
                self.customer_code, exc,
            )
            return False

    def mes_push(
        self,
        event_type: str,
        payload: Dict[str, Any],
        channel_id: Optional[int] = None,
    ) -> bool:
        """走主程序 MES Gateway 推送一条消息.

        参数:
            event_type: 事件类型, 必须以 `plugin_<customer_code>_` 前缀
                        (命名空间隔离, 不能用主程序保留 event_type 如 cycle_end / box_complete).
                        主程序 MESGateway.dispatch 按 event_type 路由到所有匹配的 mes_connection.
            payload:    数据字典, 必须可 JSON 序列化 (内部走 mes_adapters render_template).
            channel_id: 可选, 让 Gateway 按工位过滤 connections; None = 全广播.

        返回:
            True  = dispatch 已调用成功 (实际外推由 Gateway 后台).
            False = 异常被 swallow, audit 已写.

        安全约束:
            - 需要 manifest.capabilities 声明 'runtime.mes_push'.
            - event_type 必须命名空间前缀.
            - payload 必须是 dict (其它类型直接抛 TypeError).
        """
        self._require_capability("runtime.mes_push")
        # event_type 命名空间校验 (主程序 cycle_end / box_complete 等不能被插件触发)
        self._require_plugin_namespace(event_type, field_name="event_type")
        if not isinstance(payload, dict):
            raise TypeError(
                f"[Plugin][{self.customer_code}] mes_push.payload 必须是 dict, 实际 {type(payload).__name__}"
            )
        try:
            from backend.services.mes_gateway import get_mes_gateway
            gateway = get_mes_gateway()
            gateway.dispatch(event_type=event_type, context=payload, channel_id=channel_id)
            # payload 摩要: 只取 key 列表, 避免敏感数据进 audit
            keys_brief = sorted(payload.keys())[:10]
            self._audit_log(
                action="mes_push",
                status="success",
                message=f"event_type={event_type} channel_id={channel_id} payload_keys={keys_brief}",
            )
            return True
        except Exception as exc:
            self._audit_log(
                action="mes_push",
                status="failed",
                message=f"event_type={event_type} err={exc}",
            )
            log.warning(
                "[Plugin][%s] mes_push 异常 (已 swallow): %s",
                self.customer_code, exc,
            )
            return False

    def read_system_config(self, key: str) -> Optional[str]:
        """读 SystemConfig 表的 KV 值.

        参数:
            key: 配置项 key, 推荐用 `plugin_<customer_code>_` 前缀; 主程序 key
                 (例 license-cache / display.monitor.ptMode) 也允许读但**不允许写**.

        返回:
            value 字符串 (SystemConfig.value 是 TEXT); 不存在返回 None.

        安全约束:
            - **不要求**声明 capabilities (只读无副作用, 用于跨插件查主程序状态).
            - 不写 audit (高频 read 不应淹没 audit log).
        """
        try:
            from backend.db.database import SessionLocal
            from backend.models.models import SystemConfig

            db = SessionLocal()
            try:
                row = db.query(SystemConfig).filter(SystemConfig.key == key).first()
                if row is None:
                    return None
                return row.value
            finally:
                db.close()
        except Exception as exc:
            log.warning(
                "[Plugin][%s] read_system_config(%s) 异常: %s",
                self.customer_code, key, exc,
            )
            return None

    def write_system_config(
        self,
        key: str,
        value: Optional[str],
        description: Optional[str] = None,
    ) -> bool:
        """写 SystemConfig 表的 KV 值 (upsert).

        参数:
            key:         **必须**以 `plugin_<customer_code>_` 前缀 (命名空间隔离,
                         不允许覆盖主程序保留 key).
            value:       配置值 (任意字符串, None 等价空字符串落库).
            description: 可选, 写入 SystemConfig.description (前端显示用).

        返回:
            True  = upsert 成功 + audit 写入.
            False = 异常被 swallow.

        安全约束:
            - 需要 manifest.capabilities 声明 'runtime.system_config_write'.
            - key 必须命名空间前缀.
            - value / description 强转 str (None → "").
        """
        self._require_capability("runtime.system_config_write")
        self._require_plugin_namespace(key, field_name="key")
        try:
            from backend.db.database import SessionLocal
            from backend.models.models import SystemConfig

            db = SessionLocal()
            try:
                row = db.query(SystemConfig).filter(SystemConfig.key == key).first()
                value_str = "" if value is None else str(value)
                if row is None:
                    row = SystemConfig(key=key, value=value_str, description=description)
                    db.add(row)
                else:
                    row.value = value_str
                    if description is not None:
                        row.description = description
                db.commit()
                self._audit_log(
                    action="write_system_config",
                    status="success",
                    message=f"key={key} value_len={len(value_str)}",
                )
                return True
            finally:
                db.close()
        except Exception as exc:
            self._audit_log(
                action="write_system_config",
                status="failed",
                message=f"key={key} err={exc}",
            )
            log.warning(
                "[Plugin][%s] write_system_config(%s) 异常 (已 swallow): %s",
                self.customer_code, key, exc,
            )
            return False

    # ---------- M1.3a stub (实现等里程碑) ----------

    def write_plugin_step_field(self, step_record_id: int, key: str, value: Any) -> bool:
        """[STUB] 在 step_records 落插件命名空间字段.

        实现等 M3.3 (step_records.plugin_data JSON 字段). 当前抛
        ``PluginNotImplementedError``, 让插件作者明确知道这能力还没接.

        未来契约 (M3.3 落地后):
        - step_record_id 必须是当前 channel 已结束的 step record id
        - key 必须 `plugin_<customer_code>_` 前缀
        - value 必须可 JSON 序列化
        - 写入 step_records.plugin_data[<key>] = value (JSON 合并, 不删其它 key)
        """
        raise PluginNotImplementedError(
            f"[Plugin][{self.customer_code}] write_plugin_step_field(...) "
            f"尚未接入主程序 (等 M3.3 里程碑: step_records.plugin_data JSON 字段). "
            f"临时替代: 用 backend.tables registry 注册自家表 + hook step_change 时落自家表."
        )

    def broadcast_to_channel_group(
        self,
        group_id: int,
        message: Dict[str, Any],
    ) -> bool:
        """[STUB] 给工位组内其它通道发广播.

        实现等 RFC 10 (工位组主程序原生 + ChannelGroupCoordinator). 当前抛
        ``PluginNotImplementedError``.

        未来契约 (RFC 10 落地后):
        - group_id 必须是 channel_groups 表已存在的工位组 id
        - 消息 dispatch 给同组其它 channel 的 VideoSourceManager (走主程序内存 channel)
        - 不跨机器 (跨机器走 cluster_collector 已有路径)
        """
        raise PluginNotImplementedError(
            f"[Plugin][{self.customer_code}] broadcast_to_channel_group(...) "
            f"尚未接入主程序 (等 RFC 10 里程碑: 工位组主程序原生). "
            f"临时替代: 用 hooks.register('cycle_end', ...) 在主程序 cycle_end 时各自处理."
        )
