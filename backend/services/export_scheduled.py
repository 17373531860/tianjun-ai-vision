"""
v3.8.x 定时导出服务 — APScheduler 后台调度 + 数据窗口计算 + 写盘 + 日志

启动入口: start_scheduler()  在 backend/main.py 启动时调
停止入口: stop_scheduler()   在 backend/main.py 关机/lifespan shutdown 时调
配置变更入口: reload_rule(rule_id) / reload_all_rules() 在 API CRUD 后调

设计原则:
1. 后台一个 BackgroundScheduler 实例 (跑独立线程, 不阻塞 uvicorn)
2. 每条 enabled rule 注册一个 CronTrigger job, job_id = f"scheduled_export_{rule_id}"
3. 任务被触发时:
   a. 算出 (date, start_date, end_date, start_hour, end_hour) - 复用现有 sessions_export 逻辑语义
   b. 调 sessions_export.build_csv_string 拿 CSV 字符串
   c. 按 output_format 走 export_scheduled_writers 转字节流
   d. 渲染文件名 / 解析输出目录 / 处理覆盖策略 → 写盘
   e. 写 ExportRunLog + 更新 rule.last_run_*
4. CRUD API 修改规则后调 reload_rule 让 scheduler 重新读 cron 表达式
"""
from __future__ import annotations

import os
import threading
import traceback
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from backend.core.config import DATA_DIR
from backend.db.database import SessionLocal, engine
from backend.models.export_models import ExportScheduledRule, ExportRunLog


_SCHEDULER = None
_LOCK = threading.Lock()
_JOB_PREFIX = "scheduled_export_"


# ============================================================
# 默认输出目录 (全局, 来自 SystemConfig)
# ============================================================

DEFAULT_OUTPUT_DIR_KEY = "export.scheduled.default_dir"


def get_default_output_dir(db: Optional[Session] = None) -> str:
    """全局默认输出目录, 客户在 SystemConfig 里配。
    没配时回落到 DATA_DIR/exports/scheduled (确保规则能跑起来不报路径错)。
    """
    from backend.models.models import SystemConfig
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True
    try:
        cfg = db.query(SystemConfig).filter(SystemConfig.key == DEFAULT_OUTPUT_DIR_KEY).first()
        if cfg and cfg.value and cfg.value.strip():
            return cfg.value.strip()
    finally:
        if close_db:
            db.close()
    return os.path.join(DATA_DIR, "exports", "scheduled")


# ============================================================
# 数据窗口计算 — 把 rule 翻译成 sessions_export 入参三元组
# ============================================================

def compute_data_window(rule: ExportScheduledRule, now: Optional[datetime] = None) -> dict:
    """把 rule.data_window_type + data_window_config 翻成 sessions_export 期望的入参。

    返回字段名跟 build_csv_string 入参对齐 (date / start_date / end_date /
    start_hour / end_hour). 调用方按需取。

    跨日班次 (start_hour > end_hour) 走"date + 跨日 OR 查询"路径,
    sessions_export._filter_sessions_for_range / _shift_filter_cycles 已实现。
    """
    now = now or datetime.now()
    cfg = rule.data_window_config or {}
    wt = rule.data_window_type or "yesterday"

    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    today = now.strftime("%Y-%m-%d")

    if wt == "yesterday":
        return {"date": yesterday, "start_date": None, "end_date": None,
                "start_hour": None, "end_hour": None}

    if wt == "today":
        return {"date": today, "start_date": None, "end_date": None,
                "start_hour": None, "end_hour": None}

    if wt == "last_n_hours":
        n = max(1, int(cfg.get("n", 24) or 24))
        start_dt = now - timedelta(hours=n)
        return {"date": None,
                "start_date": start_dt.strftime("%Y-%m-%d"),
                "end_date": now.strftime("%Y-%m-%d"),
                "start_hour": None, "end_hour": None}

    if wt == "last_n_days":
        n = max(1, int(cfg.get("n", 7) or 7))
        start_dt = now - timedelta(days=n)
        return {"date": None,
                "start_date": start_dt.strftime("%Y-%m-%d"),
                "end_date": (now - timedelta(days=1)).strftime("%Y-%m-%d"),
                "start_hour": None, "end_hour": None}

    if wt == "shift_day_yesterday":
        # 白班: 默认 08:00 - 20:00, 同日内
        return {"date": yesterday, "start_date": None, "end_date": None,
                "start_hour": cfg.get("start_hour", "08:00"),
                "end_hour": cfg.get("end_hour", "20:00")}

    if wt == "shift_night_yesterday":
        # 夜班: 默认 20:00 - 次日 08:00, start_hour > end_hour 自动走跨日 OR 路径
        return {"date": yesterday, "start_date": None, "end_date": None,
                "start_hour": cfg.get("start_hour", "20:00"),
                "end_hour": cfg.get("end_hour", "08:00")}

    if wt == "custom_offset":
        days_off = int(cfg.get("date_offset_days", -1) or -1)
        base = (now + timedelta(days=days_off)).strftime("%Y-%m-%d")
        return {"date": base, "start_date": None, "end_date": None,
                "start_hour": cfg.get("start_hour"),
                "end_hour": cfg.get("end_hour")}

    # 兜底 → 昨天全天
    return {"date": yesterday, "start_date": None, "end_date": None,
            "start_hour": None, "end_hour": None}


# ============================================================
# 文件名渲染 (简单占位, 非 Jinja2, 不需要沙箱)
# ============================================================

def render_filename(rule: ExportScheduledRule, window: dict, now: datetime,
                    file_ext: str) -> str:
    """{rule_name} / {date} / {datetime} / {window_start} / {window_end} / {format}"""
    tpl = rule.filename_template or "{rule_name}_{date}.{format}"
    date_str = window.get("date") or (window.get("start_date") or now.strftime("%Y-%m-%d"))
    win_start = window.get("start_date") or window.get("date") or ""
    win_end = window.get("end_date") or window.get("date") or ""
    mapping = {
        "rule_name": _sanitize_filename_part(rule.name or "scheduled"),
        "date": date_str,
        "datetime": now.strftime("%Y%m%d_%H%M%S"),
        "window_start": win_start,
        "window_end": win_end,
        "format": file_ext,
    }
    try:
        return tpl.format(**mapping)
    except KeyError as e:
        # 占位符写错时不让任务挂掉, 兜底用默认名
        print(f"[Scheduled] 文件名模板占位符 {e} 不识别, 用默认: {tpl}")
        return f"{mapping['rule_name']}_{date_str}.{file_ext}"


def _sanitize_filename_part(s: str) -> str:
    bad = '\\/:*?"<>|\n\r\t'
    for ch in bad:
        s = s.replace(ch, "_")
    return s.strip()[:120] or "scheduled"


def resolve_output_path(rule: ExportScheduledRule, filename: str,
                        db: Optional[Session] = None) -> str:
    """rule.output_dir 优先, 没填走全局默认。"""
    base = (rule.output_dir or "").strip() or get_default_output_dir(db)
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, filename)


def apply_overwrite_policy(path: str, policy: str) -> Optional[str]:
    """- overwrite (默认): 直接返回 path
    - rename: 加 _时间戳 后缀
    - skip: 文件已存在则返回 None (调用方写 skip 日志)
    """
    if not os.path.exists(path):
        return path
    policy = (policy or "overwrite").lower()
    if policy == "skip":
        return None
    if policy == "rename":
        stem, ext = os.path.splitext(path)
        ts = datetime.now().strftime("%H%M%S")
        return f"{stem}_{ts}{ext}"
    return path  # overwrite


# ============================================================
# 任务执行核心
# ============================================================

def _run_rule(rule_id: int, source_type: str = "scheduled") -> dict:
    """执行一条规则。

    返回: {"status": "success"|"failed"|"skipped"|"no_data", "file": ..., "error": ...}
    被 APScheduler 直接调度 + 也供 /test-run 立即调用。
    """
    from backend.api.sessions_export import build_csv_string
    from backend.api.sessions import _get_step_order_map
    from backend.services.export_scheduled_writers import (
        csv_string_to_format_bytes, FILE_EXTENSIONS,
    )

    started = datetime.now()
    result = {"status": "failed", "file": None, "error": None, "size": 0}

    db = SessionLocal()
    try:
        rule = db.query(ExportScheduledRule).filter(ExportScheduledRule.id == rule_id).first()
        if not rule:
            return {"status": "failed", "file": None, "error": f"rule {rule_id} not found", "size": 0}

        if not rule.enabled and source_type == "scheduled":
            return {"status": "skipped", "file": None,
                    "error": "rule disabled", "size": 0}

        window = compute_data_window(rule, now=started)

        # ---- 取 CSV 字符串 (复用标准日报的列结构) ----
        csv_str = build_csv_string(
            db, _get_step_order_map,
            export_type="all",
            session_id=None, cycle_id=None,
            date=window["date"], start_date=window["start_date"], end_date=window["end_date"],
            week=None, month=None,
            start_hour=window["start_hour"], end_hour=window["end_hour"],
            project_id=rule.project_id, channel_id=rule.channel_id,
            pt_mode=rule.pt_mode, ct_mode=rule.ct_mode,
        )

        # 仅含 BOM 或 1 行表头说明无数据 → 记 no_data 不写盘
        body = csv_str.lstrip("\ufeff").strip()
        if not body or body.count("\n") < 1:
            result.update({"status": "no_data", "file": None})
        else:
            fmt = (rule.output_format or "csv").lower()
            file_ext = FILE_EXTENSIONS.get(fmt, fmt)
            filename = render_filename(rule, window, started, file_ext)
            full_path = resolve_output_path(rule, filename, db=db)
            real_path = apply_overwrite_policy(full_path, rule.overwrite_policy)
            if real_path is None:
                result.update({"status": "skipped", "file": full_path,
                               "error": "file exists, overwrite_policy=skip"})
            else:
                body_bytes = csv_string_to_format_bytes(csv_str, fmt)
                # 文本格式 (csv/txt) 走 encoding 配置 (utf-8-sig / gbk / ...)
                if fmt in ("csv", "txt") and rule.encoding and rule.encoding != "utf-8":
                    try:
                        body_bytes = csv_str.encode(rule.encoding, errors="replace")
                    except LookupError:
                        pass
                with open(real_path, "wb") as f:
                    f.write(body_bytes)
                result.update({"status": "success", "file": real_path,
                               "size": len(body_bytes)})

        # ---- 更新 rule 运行状态 ----
        rule.last_run_time = started
        rule.last_run_status = result["status"]
        rule.last_run_error = result["error"]
        rule.last_output_file = result["file"]
        if result["status"] == "success":
            rule.success_count = (rule.success_count or 0) + 1
        elif result["status"] == "failed":
            rule.failed_count = (rule.failed_count or 0) + 1
        elif result["status"] == "skipped":
            rule.skipped_count = (rule.skipped_count or 0) + 1
        # 计算下次触发时间 (croniter 算)
        try:
            from croniter import croniter
            it = croniter(rule.cron_expression or "0 0 * * *", started)
            rule.next_run_time = it.get_next(datetime)
        except Exception:
            rule.next_run_time = None

        # ---- 写日志表 ----
        duration_ms = int((datetime.now() - started).total_seconds() * 1000)
        log = ExportRunLog(
            rule_id=None,  # ExportRunLog.rule_id 是 FK 指向 ExportRealtimeRule, 定时规则借用日志表但不绑外键
            template_id=rule.template_id,
            source_type=source_type,
            cycle_id=None, session_id=None, box_serial=None,
            triggered_at=started,
            status=result["status"],
            output_file=result["file"],
            file_size=result["size"] or None,
            duration_ms=duration_ms,
            error_msg=result["error"],
            skip_reason=("scheduled_rule_id=" + str(rule.id)) if result["status"] in ("skipped", "no_data") else None,
        )
        db.add(log)
        db.commit()

    except Exception as e:
        traceback.print_exc()
        err = f"{type(e).__name__}: {e}"
        result.update({"status": "failed", "error": err})
        try:
            rule = db.query(ExportScheduledRule).filter(ExportScheduledRule.id == rule_id).first()
            if rule:
                rule.last_run_time = started
                rule.last_run_status = "failed"
                rule.last_run_error = err[:2000]
                rule.failed_count = (rule.failed_count or 0) + 1
                duration_ms = int((datetime.now() - started).total_seconds() * 1000)
                db.add(ExportRunLog(
                    rule_id=None, template_id=rule.template_id, source_type=source_type,
                    triggered_at=started, status="failed",
                    duration_ms=duration_ms, error_msg=err[:2000],
                    skip_reason=f"scheduled_rule_id={rule.id}",
                ))
                db.commit()
        except Exception:
            db.rollback()
    finally:
        db.close()

    return result


# ============================================================
# Scheduler 管理
# ============================================================

def _build_trigger(cron_expr: str):
    """从 cron 字符串构造 APScheduler 的 CronTrigger."""
    from apscheduler.triggers.cron import CronTrigger
    parts = (cron_expr or "0 0 * * *").strip().split()
    if len(parts) == 5:
        minute, hour, day, month, dow = parts
        return CronTrigger(minute=minute, hour=hour, day=day, month=month, day_of_week=dow)
    if len(parts) == 6:
        sec, minute, hour, day, month, dow = parts
        return CronTrigger(second=sec, minute=minute, hour=hour, day=day, month=month, day_of_week=dow)
    raise ValueError(f"cron_expression 必须是 5 或 6 字段, 收到: {cron_expr!r}")


def _job_id(rule_id: int) -> str:
    return f"{_JOB_PREFIX}{rule_id}"


def start_scheduler() -> None:
    """启动后台调度器, 加载所有 enabled rules. 幂等。"""
    global _SCHEDULER
    with _LOCK:
        if _SCHEDULER is not None:
            return
        try:
            from apscheduler.schedulers.background import BackgroundScheduler
        except ImportError:
            print("[Scheduled] APScheduler 未安装, 定时导出功能关闭")
            return

        _SCHEDULER = BackgroundScheduler(daemon=True)
        _SCHEDULER.start()
        print("[Scheduled] BackgroundScheduler 启动")

        _reload_all_jobs_locked()


def stop_scheduler() -> None:
    global _SCHEDULER
    with _LOCK:
        if _SCHEDULER is None:
            return
        try:
            _SCHEDULER.shutdown(wait=False)
        except Exception:
            pass
        _SCHEDULER = None
        print("[Scheduled] BackgroundScheduler 已停止")


def _reload_all_jobs_locked() -> None:
    """重新加载所有 enabled 规则。必须在 _LOCK 下调用。"""
    if _SCHEDULER is None:
        return
    db = SessionLocal()
    try:
        # 先清掉所有现有 job
        for job in list(_SCHEDULER.get_jobs()):
            if job.id.startswith(_JOB_PREFIX):
                _SCHEDULER.remove_job(job.id)
        rules = db.query(ExportScheduledRule).filter(ExportScheduledRule.enabled.is_(True)).all()
        for rule in rules:
            try:
                trigger = _build_trigger(rule.cron_expression)
            except Exception as e:
                print(f"[Scheduled] rule#{rule.id} cron 解析失败: {e}")
                continue
            _SCHEDULER.add_job(
                _run_rule, trigger=trigger, args=[rule.id],
                id=_job_id(rule.id), name=f"export-{rule.name}",
                replace_existing=True, max_instances=1, coalesce=True,
            )
            try:
                from croniter import croniter
                rule.next_run_time = croniter(rule.cron_expression, datetime.now()).get_next(datetime)
            except Exception:
                rule.next_run_time = None
        db.commit()
        print(f"[Scheduled] 加载 {len(rules)} 条 enabled 规则")
    finally:
        db.close()


def reload_all_rules() -> None:
    with _LOCK:
        _reload_all_jobs_locked()


def reload_rule(rule_id: int) -> None:
    """单条 rule 配置变了 (CRUD/toggle), 重新注册它的 job."""
    global _SCHEDULER
    with _LOCK:
        if _SCHEDULER is None:
            return
        # 先移除旧 job
        try:
            _SCHEDULER.remove_job(_job_id(rule_id))
        except Exception:
            pass
        db = SessionLocal()
        try:
            rule = db.query(ExportScheduledRule).filter(ExportScheduledRule.id == rule_id).first()
            if not rule or not rule.enabled:
                return
            try:
                trigger = _build_trigger(rule.cron_expression)
            except Exception as e:
                print(f"[Scheduled] rule#{rule.id} cron 解析失败: {e}")
                return
            _SCHEDULER.add_job(
                _run_rule, trigger=trigger, args=[rule.id],
                id=_job_id(rule.id), name=f"export-{rule.name}",
                replace_existing=True, max_instances=1, coalesce=True,
            )
            try:
                from croniter import croniter
                rule.next_run_time = croniter(rule.cron_expression, datetime.now()).get_next(datetime)
                db.commit()
            except Exception:
                pass
        finally:
            db.close()


def remove_rule_job(rule_id: int) -> None:
    """删除规则时清掉它的 job (CRUD/delete 调)."""
    with _LOCK:
        if _SCHEDULER is None:
            return
        try:
            _SCHEDULER.remove_job(_job_id(rule_id))
        except Exception:
            pass


def trigger_now(rule_id: int) -> dict:
    """立即触发一次 (/test-run API). 同步执行, 阻塞到完成 (返回结果)."""
    return _run_rule(rule_id, source_type="manual_test")
