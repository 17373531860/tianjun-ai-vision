"""
v3.5.0 自定义导出 — 实时规则调度器

职责：
1. cycle_end / session_end / box_complete 事件来时，扫描所有启用的 ExportRealtimeRule
2. 按 channel_filter / project_filter 过滤
3. 构建 cycle / session / box 上下文（复用 export_context.build_*）
4. 调用 export_renderer.render_to_file() 落盘（三种 input_file_mode 已实现）
5. 写 ExportRunLog 日志 + 更新 ExportRealtimeRule 累计统计

调用方：
- mes_hooks._handle_cycle_end → dispatch_cycle_end_export(...)
- mes_hooks._handle_session_end → dispatch_session_end_export(...) [todo]
- 用户手动测试 → trigger_test_run(...)

线程模型：
本模块由 MES Hook worker 线程调用（单线程顺序执行）。多规则之间也是串行；
单规则失败不影响其他规则（每条规则独立 try/except）。
"""
from __future__ import annotations

import time
import traceback
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from backend.models.export_models import ExportRealtimeRule, ExportRunLog
from backend.services.export_context import (
    build_cycle_context, build_range_context, build_system_context,
)
from backend.services.export_renderer import render_to_file, RenderResult


# ============================================================
# 主入口 — cycle_end 事件
# ============================================================

def dispatch_cycle_end_export(db: Session,
                              channel_id: int,
                              cycle_id: int,
                              project_id: Optional[int] = None,
                              license_payload: Optional[Dict[str, Any]] = None,
                              ) -> List[Dict[str, Any]]:
    """每个 cycle 结束后调用：扫描启用规则、按过滤器匹配、渲染落盘、写日志。

    返回每条规则的执行结果摘要（用于排查）。失败不抛异常 — 所有错误吞到日志里。

    调用点：mes_hooks._handle_cycle_end 末尾。
    """
    results: List[Dict[str, Any]] = []
    try:
        rules = (
            db.query(ExportRealtimeRule)
            .filter(
                ExportRealtimeRule.enabled == True,  # noqa: E712
                ExportRealtimeRule.trigger_event == "cycle_end",
            )
            .order_by(ExportRealtimeRule.id.asc())
            .all()
        )
    except Exception as e:
        print(f"[ExportRealtime] 查询规则失败: {e}", flush=True)
        return results

    if not rules:
        return results

    # 共享 ctx — 多条规则用同一个 cycle，避免重复 build
    ctx = None
    ctx_err = None
    for rule in rules:
        # 通道过滤
        if rule.channel_filter and channel_id not in rule.channel_filter:
            _write_skip_log(db, rule, cycle_id=cycle_id,
                            skip_reason=f"channel_filter:{channel_id}_not_in_list")
            results.append({"rule_id": rule.id, "status": "skipped",
                            "reason": "channel_filter"})
            continue

        # 项目过滤
        if rule.project_filter and project_id is not None \
                and project_id not in rule.project_filter:
            _write_skip_log(db, rule, cycle_id=cycle_id,
                            skip_reason=f"project_filter:{project_id}_not_in_list")
            results.append({"rule_id": rule.id, "status": "skipped",
                            "reason": "project_filter"})
            continue

        # 懒加载 ctx
        if ctx is None and ctx_err is None:
            try:
                ctx = build_cycle_context(db, cycle_id, license_payload=license_payload)
            except Exception as e:
                ctx_err = f"{type(e).__name__}: {e}"
                print(f"[ExportRealtime] build_cycle_context 失败 cycle#{cycle_id}: {ctx_err}",
                      flush=True)

        if ctx is None:
            _write_failure_log(db, rule, cycle_id=cycle_id,
                               error_msg=f"context_build_failed: {ctx_err}")
            results.append({"rule_id": rule.id, "status": "failed",
                            "error": ctx_err})
            continue

        # 渲染 + 写文件
        result = _execute_rule(db, rule, ctx, cycle_id=cycle_id)
        results.append({"rule_id": rule.id, "rule_name": rule.name, **result.to_dict()})

    # 防止 worker 线程持有未提交事务（write_log 会 commit，但保险起见）
    try:
        db.commit()
    except Exception:
        db.rollback()

    return results


# ============================================================
# 单规则执行
# ============================================================

def _execute_rule(db: Session,
                  rule: ExportRealtimeRule,
                  ctx: Dict[str, Any],
                  *,
                  cycle_id: Optional[int] = None,
                  session_id: Optional[int] = None,
                  source_type: str = "realtime") -> RenderResult:
    """执行一条规则：渲染 + 落盘 + 写日志 + 更新统计"""
    t0 = time.perf_counter()
    template = rule.template
    if template is None:
        result = RenderResult(
            status="failed",
            error_msg=f"模板 ID#{rule.template_id} 不存在",
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )
        _write_log_from_result(db, rule, result, cycle_id=cycle_id,
                               session_id=session_id, source_type=source_type)
        return result

    fmt = template.format or "txt"
    if fmt not in ("txt", "csv", "docx", "xlsx", "pdf"):
        result = RenderResult(
            status="failed",
            error_msg=f"unsupported_format: {fmt}",
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )
        _write_log_from_result(db, rule, result, cycle_id=cycle_id,
                               session_id=session_id, source_type=source_type)
        return result

    try:
        result = render_to_file(
            template_content=template.content or "",
            filename_template=rule.filename_template or "{{ cycle.id }}.txt",
            output_dir=rule.output_dir,
            context=ctx,
            fmt=fmt,
            input_file_mode=rule.input_file_mode or "none",
            input_dir=rule.input_dir,
            overwrite_policy=rule.overwrite_policy or "overwrite",
            encoding=rule.encoding or "utf-8",
            newline=rule.newline or "lf",
            ensure_dir=True,
            template_file_path=template.template_file_path,
        )
    except Exception as e:
        result = RenderResult(
            status="failed",
            error_msg=f"{type(e).__name__}: {e}\n{traceback.format_exc()}",
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )

    _write_log_from_result(db, rule, result, cycle_id=cycle_id,
                           session_id=session_id, source_type=source_type)
    return result


# ============================================================
# 日志 + 统计写入
# ============================================================

def _write_log_from_result(db: Session,
                           rule: ExportRealtimeRule,
                           result: RenderResult,
                           *,
                           cycle_id: Optional[int] = None,
                           session_id: Optional[int] = None,
                           source_type: str = "realtime") -> None:
    """写 ExportRunLog 一条 + 更新 rule 累计统计字段"""
    try:
        log = ExportRunLog(
            rule_id=rule.id,
            template_id=rule.template_id,
            source_type=source_type,
            cycle_id=cycle_id,
            session_id=session_id,
            status=result.status,
            output_file=result.output_path,
            file_size=result.file_size,
            duration_ms=result.duration_ms,
            error_msg=result.error_msg,
            skip_reason=result.skip_reason,
        )
        db.add(log)

        # 更新 rule 状态
        rule.last_run_time = log.triggered_at  # commit 后由 server_default 填
        rule.last_run_status = result.status
        rule.last_run_error = result.error_msg
        rule.last_output_file = result.output_path

        if result.status == "success":
            rule.success_count = (rule.success_count or 0) + 1
        elif result.status == "failed":
            rule.failed_count = (rule.failed_count or 0) + 1
        elif result.status == "skipped":
            rule.skipped_count = (rule.skipped_count or 0) + 1

        db.commit()
    except Exception as e:
        # 日志写失败不能反向影响主流程
        db.rollback()
        print(f"[ExportRealtime] 写日志失败 rule#{rule.id}: {e}", flush=True)


def _write_skip_log(db: Session, rule: ExportRealtimeRule,
                    *, cycle_id=None, session_id=None,
                    skip_reason: str) -> None:
    """规则被过滤器挡住时也写一条日志（短暂跳过，不计入失败）"""
    result = RenderResult(status="skipped", skip_reason=skip_reason, duration_ms=0)
    _write_log_from_result(db, rule, result, cycle_id=cycle_id,
                           session_id=session_id, source_type="realtime")


def _write_failure_log(db: Session, rule: ExportRealtimeRule,
                       *, cycle_id=None, session_id=None,
                       error_msg: str) -> None:
    result = RenderResult(status="failed", error_msg=error_msg, duration_ms=0)
    _write_log_from_result(db, rule, result, cycle_id=cycle_id,
                           session_id=session_id, source_type="realtime")


# ============================================================
# 手动测试入口（前端"测试触发"按钮）
# ============================================================

def trigger_test_run(db: Session,
                     rule_id: int,
                     cycle_id: Optional[int] = None,
                     session_id: Optional[int] = None,
                     license_payload: Optional[Dict[str, Any]] = None,
                     display_payload: Optional[Dict[str, Any]] = None,
                     ) -> Dict[str, Any]:
    """前端"测试触发"调用 — 走完整流程但 source_type='manual_test' 区分

    cycle_id / session_id 二选一；都不传走 build_system_context。
    """
    rule = db.query(ExportRealtimeRule).filter(
        ExportRealtimeRule.id == rule_id
    ).first()
    if rule is None:
        return {"status": "failed", "error": f"rule#{rule_id} 不存在"}

    try:
        if cycle_id:
            ctx = build_cycle_context(db, cycle_id, license_payload=license_payload)
        elif session_id:
            ctx = build_range_context(db, session_id=session_id,
                                      license_payload=license_payload)
        else:
            ctx = build_system_context(db, license_payload=license_payload)
    except Exception as e:
        return {"status": "failed",
                "error": f"build_context: {type(e).__name__}: {e}"}

    # display_payload 注入（覆盖 SystemConfig 副本）
    if display_payload and isinstance(ctx.get("display"), dict):
        for k, v in display_payload.items():
            if v is not None:
                ctx["display"][k] = v

    result = _execute_rule(db, rule, ctx,
                           cycle_id=cycle_id, session_id=session_id,
                           source_type="manual_test")
    return {"status": result.status, **result.to_dict(),
            "rule_id": rule.id, "rule_name": rule.name}
