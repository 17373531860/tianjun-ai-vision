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
- mes_hooks._handle_session_end → dispatch_session_end_export(...)
- 用户手动测试 → trigger_test_run(...)

线程模型：
本模块由 MES Hook worker 线程调用（单线程顺序执行）。多规则之间也是串行；
单规则失败不影响其他规则（每条规则独立 try/except）。
"""
from __future__ import annotations

import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from backend.db.database import SessionLocal
from backend.models.export_models import ExportRealtimeRule, ExportRunLog
from backend.models.models import DetectionCycle
from backend.services.export_context import (
    build_cycle_context, build_range_context, build_system_context,
)
from backend.services.export_renderer import (
    render_to_file, RenderResult,
    latest_input_filename, latest_input_text,
)
from backend.services.export_snapshot import lookup_snapshot_from_cycle


# v3.7.2 异步重试线程池. 用于"去重命中重名 → 等下一份新文件"的轮询.
# 单 worker 串行执行就够 — 不同规则的等待相互独立, 但 MES Hook 一周期通常
# 也就触发几条规则, 不需要高并发. 用单线程更易调试 + 不抢检测主 GPU.
_RETRY_EXECUTOR: Optional[ThreadPoolExecutor] = None
_RETRY_EXECUTOR_LOCK = threading.Lock()


def _get_retry_executor() -> ThreadPoolExecutor:
    global _RETRY_EXECUTOR
    if _RETRY_EXECUTOR is None:
        with _RETRY_EXECUTOR_LOCK:
            if _RETRY_EXECUTOR is None:
                _RETRY_EXECUTOR = ThreadPoolExecutor(
                    max_workers=2, thread_name_prefix="export-retry"
                )
    return _RETRY_EXECUTOR


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
# 主入口 — session_end 事件 (v3.6.2 接通)
# ============================================================

def dispatch_session_end_export(db: Session,
                                channel_id: int,
                                session_id: int,
                                project_id: Optional[int] = None,
                                license_payload: Optional[Dict[str, Any]] = None,
                                ) -> List[Dict[str, Any]]:
    """每次 Session 结束后调用：扫描启用的 trigger_event='session_end' 规则,
    用 build_range_context(session_id) 构建上下文, 渲染落盘, 写 ExportRunLog.

    返回每条规则的执行结果摘要。失败不抛异常 — 错误吞到日志里。
    调用点: mes_hooks._handle_session_end 末尾。
    """
    results: List[Dict[str, Any]] = []
    try:
        rules = (
            db.query(ExportRealtimeRule)
            .filter(
                ExportRealtimeRule.enabled == True,  # noqa: E712
                ExportRealtimeRule.trigger_event == "session_end",
            )
            .order_by(ExportRealtimeRule.id.asc())
            .all()
        )
    except Exception as e:
        print(f"[ExportRealtime] 查询 session_end 规则失败: {e}", flush=True)
        return results

    if not rules:
        return results

    ctx = None
    ctx_err = None
    for rule in rules:
        if rule.channel_filter and channel_id not in rule.channel_filter:
            _write_skip_log(db, rule, session_id=session_id,
                            skip_reason=f"channel_filter:{channel_id}_not_in_list")
            results.append({"rule_id": rule.id, "status": "skipped",
                            "reason": "channel_filter"})
            continue

        if rule.project_filter and project_id is not None \
                and project_id not in rule.project_filter:
            _write_skip_log(db, rule, session_id=session_id,
                            skip_reason=f"project_filter:{project_id}_not_in_list")
            results.append({"rule_id": rule.id, "status": "skipped",
                            "reason": "project_filter"})
            continue

        if ctx is None and ctx_err is None:
            try:
                ctx = build_range_context(db, session_id=session_id,
                                          license_payload=license_payload)
            except Exception as e:
                ctx_err = f"{type(e).__name__}: {e}"
                print(f"[ExportRealtime] build_range_context 失败 session#{session_id}: {ctx_err}",
                      flush=True)

        if ctx is None:
            _write_failure_log(db, rule, session_id=session_id,
                               error_msg=f"context_build_failed: {ctx_err}")
            results.append({"rule_id": rule.id, "status": "failed",
                            "error": ctx_err})
            continue

        result = _execute_rule(db, rule, ctx, session_id=session_id)
        results.append({"rule_id": rule.id, "rule_name": rule.name, **result.to_dict()})

    try:
        db.commit()
    except Exception:
        db.rollback()

    return results


# ============================================================
# 单规则执行
# ============================================================

def _resolve_input_for_rule(db: Session,
                             rule: ExportRealtimeRule,
                             cycle_id: Optional[int]) -> Tuple[str, str, str]:
    """根据 rule.latest_file_strategy 决定本次渲染时用的 input filename 和 text.

    返回 (filename, text, source).
    source 用于日志: 'snapshot' / 'mtime_stable' / 'mtime' / 'none'.

    cycle_start_snapshot 优先 — cycle.external_meta 有快照就用快照, 没有则
    fallback 到 mtime (规则后建 / cycle_start 那一刻拍照失败的兜底).
    """
    strategy = rule.latest_file_strategy or "cycle_start_snapshot"
    input_dir = rule.input_dir or ""

    if strategy == "cycle_start_snapshot" and cycle_id:
        try:
            cyc = db.query(DetectionCycle).filter(
                DetectionCycle.id == cycle_id
            ).first()
            if cyc is not None:
                snap = lookup_snapshot_from_cycle(cyc.external_meta, input_dir)
                if snap and snap.get("filename"):
                    return (
                        snap.get("filename") or "",
                        snap.get("text") or "",
                        "snapshot",
                    )
        except Exception as e:
            print(f"[ExportRealtime] rule#{rule.id} 读快照异常 cycle#{cycle_id}: {e}",
                  flush=True)
        # 走到这意味着没拍到 / 规则后建 / cycle 找不到 — fallback 走 mtime

    # B / fallback: mtime_stable 或 cycle_start_snapshot 兜底
    wait_ms = int(rule.latest_file_wait_stable_ms or 0)
    max_age = int(rule.latest_file_max_age_sec or 0)
    if strategy == "mtime":
        # A 模式: 不开稳定 / 年龄保险
        wait_ms = 0
        max_age = 0
    filename = latest_input_filename(input_dir, wait_stable_ms=wait_ms,
                                      max_age_sec=max_age)
    text = latest_input_text(input_dir, wait_stable_ms=wait_ms,
                              max_age_sec=max_age) if filename else ""
    if filename:
        source = "mtime_stable" if (strategy == "mtime_stable") else "mtime"
        return filename, text, source
    return "", "", "none"


def _inject_export_ctx(ctx: Dict[str, Any],
                        rule: ExportRealtimeRule,
                        locked_filename: str,
                        locked_text: str) -> None:
    """把 rule + locked 快照塞进 ctx['export'], 让 helper 模板能拿到.

    对于策略 != cycle_start_snapshot (没有 locked 快照):
      也要把 wait_stable_ms / max_age_sec 透传到 ctx, 否则模板里
      {{ latest_input_text() }} fallback 走 mtime 时不会享受到加固保险.
    """
    ctx.setdefault("export", {})
    if not isinstance(ctx["export"], dict):
        return
    ctx["export"]["input_dir"] = rule.input_dir or ""
    ctx["export"]["output_dir"] = rule.output_dir or ""
    ctx["export"]["rule_name"] = rule.name or ""
    ctx["export"]["rule_id"] = rule.id
    # 透传 mtime 策略参数 — 哪怕策略是 mtime, 也认为 wait/max_age 都是 0
    strategy = rule.latest_file_strategy or "cycle_start_snapshot"
    if strategy == "mtime":
        ctx["export"]["wait_stable_ms"] = 0
        ctx["export"]["max_age_sec"] = 0
    else:
        ctx["export"]["wait_stable_ms"] = int(rule.latest_file_wait_stable_ms or 0)
        ctx["export"]["max_age_sec"] = int(rule.latest_file_max_age_sec or 0)
    if locked_filename or locked_text:
        ctx["export"]["locked"] = {
            "filename": locked_filename or "",
            "text": locked_text or "",
        }
    else:
        # 没快照: 显式清掉 locked, 防止上一周期的 locked 残留 (理论上不会, 但保险)
        ctx["export"].pop("locked", None)


def _execute_rule(db: Session,
                  rule: ExportRealtimeRule,
                  ctx: Dict[str, Any],
                  *,
                  cycle_id: Optional[int] = None,
                  session_id: Optional[int] = None,
                  source_type: str = "realtime") -> RenderResult:
    """执行一条规则: 策略分发 + 去重判定 + 同步渲染 / 异步重试调度."""
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

    # 解析当前 input filename / text (策略分发)
    filename, text, src = _resolve_input_for_rule(db, rule, cycle_id)

    # v3.7.2 去重: 同名时丢异步线程池轮询, 主线程立即返回 "queued"
    if rule.dedupe_same_filename and filename and rule.last_used_input_filename \
            and filename == rule.last_used_input_filename:
        print(f"[ExportRealtime] rule#{rule.id} 命中重名 {filename!r}, "
              f"丢异步线程池等待新文件 (最长 {rule.dedupe_retry_max_sec}s)", flush=True)
        # 异步线程会用新 db session 重新 lookup → 渲染 → 写日志
        _get_retry_executor().submit(
            _async_dedupe_retry,
            rule_id=rule.id,
            cycle_id=cycle_id,
            session_id=session_id,
            source_type=source_type,
            initial_filename=filename,
            max_wait_sec=int(rule.dedupe_retry_max_sec or 5),
            interval_ms=int(rule.dedupe_retry_interval_ms or 100),
        )
        # 主线程立即写一条 "queued" 日志, 不阻塞下一轮 cycle_end
        result = RenderResult(
            status="skipped",
            skip_reason=f"dedupe_queued:{filename}",
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )
        _write_log_from_result(db, rule, result, cycle_id=cycle_id,
                               session_id=session_id, source_type=source_type)
        return result

    _inject_export_ctx(ctx, rule, filename, text)

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

    # 成功落盘 → 把 filename 写回 rule.last_used_input_filename, 给下一周期去重用
    if result.status == "success" and filename and rule.dedupe_same_filename:
        try:
            rule.last_used_input_filename = filename
            db.flush()
        except Exception as e:
            print(f"[ExportRealtime] 回填 last_used_input_filename 异常 rule#{rule.id}: {e}",
                  flush=True)

    _write_log_from_result(db, rule, result, cycle_id=cycle_id,
                           session_id=session_id, source_type=source_type)
    return result


# ============================================================
# 异步去重重试 — 在独立线程 + 独立 db session 里跑
# ============================================================

def _async_dedupe_retry(rule_id: int,
                         cycle_id: Optional[int],
                         session_id: Optional[int],
                         source_type: str,
                         initial_filename: str,
                         max_wait_sec: int,
                         interval_ms: int) -> None:
    """轮询 rule.input_dir, 直到出现 != initial_filename 的新文件, 然后渲染.

    超时 → 写 SKIPPED 日志, status=dedupe_timeout (Q2: 选 b "跳过本规则").
    线程内自管 db session, 不共享主线程的 session.
    """
    db = SessionLocal()
    deadline = time.time() + max(0, max_wait_sec)
    poll_interval = max(0.01, interval_ms / 1000.0)
    try:
        rule = db.query(ExportRealtimeRule).filter(
            ExportRealtimeRule.id == rule_id
        ).first()
        if rule is None:
            print(f"[ExportRealtime/AsyncDedupe] rule#{rule_id} 已被删, 终止重试",
                  flush=True)
            return

        new_filename = ""
        new_text = ""
        while time.time() < deadline:
            # 不重读快照 — 重试是为了等扫码器写新文件, 我们要拿"当下"的状态, 不是
            # cycle_start 那一刻锁定的状态.
            wait_ms = int(rule.latest_file_wait_stable_ms or 0)
            max_age = int(rule.latest_file_max_age_sec or 0)
            cand = latest_input_filename(rule.input_dir or "",
                                          wait_stable_ms=wait_ms,
                                          max_age_sec=max_age)
            if cand and cand != initial_filename:
                new_filename = cand
                new_text = latest_input_text(rule.input_dir or "",
                                              wait_stable_ms=wait_ms,
                                              max_age_sec=max_age)
                break
            time.sleep(poll_interval)

        if not new_filename:
            # 超时 — 跳过本条 (Q2=b)
            print(f"[ExportRealtime/AsyncDedupe] rule#{rule_id} 重试超时 "
                  f"({max_wait_sec}s), initial={initial_filename!r}, "
                  f"按 [跳过本规则] 处理", flush=True)
            result = RenderResult(
                status="skipped",
                skip_reason=f"dedupe_timeout:{initial_filename}",
                duration_ms=int(max_wait_sec * 1000),
            )
            _write_log_from_result(db, rule, result, cycle_id=cycle_id,
                                   session_id=session_id, source_type=source_type)
            return

        # 重建 ctx (异步线程不能共享主线程 ctx, 主线程那个可能已经被 GC 改了)
        try:
            if cycle_id:
                ctx = build_cycle_context(db, cycle_id)
            elif session_id:
                ctx = build_range_context(db, session_id=session_id)
            else:
                ctx = build_system_context(db)
        except Exception as e:
            print(f"[ExportRealtime/AsyncDedupe] rule#{rule_id} build_ctx 异常: {e}",
                  flush=True)
            result = RenderResult(
                status="failed",
                error_msg=f"async_build_ctx: {type(e).__name__}: {e}",
                duration_ms=int((time.time() - (deadline - max_wait_sec)) * 1000),
            )
            _write_log_from_result(db, rule, result, cycle_id=cycle_id,
                                   session_id=session_id, source_type=source_type)
            return

        _inject_export_ctx(ctx, rule, new_filename, new_text)

        template = rule.template
        if template is None:
            result = RenderResult(
                status="failed",
                error_msg=f"模板 ID#{rule.template_id} 不存在",
            )
            _write_log_from_result(db, rule, result, cycle_id=cycle_id,
                                   session_id=session_id, source_type=source_type)
            return

        fmt = template.format or "txt"
        t0 = time.perf_counter()
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
                error_msg=f"async_render: {type(e).__name__}: {e}\n{traceback.format_exc()}",
                duration_ms=int((time.perf_counter() - t0) * 1000),
            )

        if result.status == "success":
            rule.last_used_input_filename = new_filename
            db.flush()
            print(f"[ExportRealtime/AsyncDedupe] rule#{rule_id} 重试成功: "
                  f"{initial_filename!r} -> {new_filename!r}", flush=True)
        _write_log_from_result(db, rule, result, cycle_id=cycle_id,
                               session_id=session_id, source_type=source_type)
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        print(f"[ExportRealtime/AsyncDedupe] rule#{rule_id} 总异常: {e}\n"
              f"{traceback.format_exc()}", flush=True)
    finally:
        try:
            db.close()
        except Exception:
            pass


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
