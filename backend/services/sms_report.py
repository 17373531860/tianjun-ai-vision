"""
每日短信日报 — 聚合 + 调度 + 发送 (v3.46+)

链路:
  APScheduler cron → _run_rule(rule_id)
    → 数据窗口 (today / yesterday)
    → counter_daily.flush_now() 把内存日桶算进台账
    → 汇总模式一个 scope / 分工位模式每工位一个 scope
    → 每 scope: build_range_context 按日聚合 + counters_daily 台账 → 模板变量 dict
    → fire_plugin_hook("daily_report_before_send") (returnable: 改写变量/手机号/跳过)
    → sms_adapters 发送 (失败重试 MAX_RETRY 次) → SmsSendLog 落库
    → 更新 rule.last_run_*

硬约束:
  - 任何异常不得抛回调度线程外, 更不得影响检测主流程
  - 国内云短信 = 审核模板 + 变量, metrics 勾选字段映射成模板变量
"""
import json
import threading
import time
from datetime import datetime, timedelta, date as _date
from typing import Dict, List, Optional, Any

from backend.db.database import SessionLocal
from backend.models.notify_models import SmsReportRule, SmsSendLog
from backend.models.models import SystemConfig, DetectionSession

_LOCK = threading.RLock()
_SCHEDULER = None
_JOB_PREFIX = "sms_report_"

MAX_RETRY = 2          # 首发 + 2 次重试
RETRY_INTERVAL_S = 5

# ============================================================
# 服务商配置 (SystemConfig KV)
# ============================================================

KEY_PROVIDER = "sms.provider"
KEY_CONFIG = "sms.config"

_SECRET_FIELDS = ("access_key_secret", "relay_token")
SECRET_MASK = "******"


def get_provider_config(db) -> dict:
    """读服务商配置 (原文, 内部用)。"""
    provider_row = db.query(SystemConfig).filter(SystemConfig.key == KEY_PROVIDER).first()
    config_row = db.query(SystemConfig).filter(SystemConfig.key == KEY_CONFIG).first()
    try:
        config = json.loads(config_row.value) if (config_row and config_row.value) else {}
    except Exception:
        config = {}
    return {
        "provider": (provider_row.value if provider_row else "") or "",
        "config": config,
    }


def get_provider_config_masked(db) -> dict:
    """读服务商配置 (SK 脱敏, 给前端)。"""
    data = get_provider_config(db)
    config = dict(data["config"])
    for f in _SECRET_FIELDS:
        if config.get(f):
            config[f] = SECRET_MASK
    data["config"] = config
    return data


def set_provider_config(db, provider: str, config: dict) -> None:
    """写服务商配置。config 里 SK 值为脱敏占位时保留旧值 (前端回显不改密钥场景)。"""
    old = get_provider_config(db)["config"]
    merged = dict(config or {})
    for f in _SECRET_FIELDS:
        if merged.get(f) == SECRET_MASK and old.get(f):
            merged[f] = old[f]

    for key, value, desc in (
        (KEY_PROVIDER, (provider or "").strip(), "短信日报服务商 (aliyun|tencent)"),
        (KEY_CONFIG, json.dumps(merged, ensure_ascii=False), "短信日报服务商配置 JSON"),
    ):
        row = db.query(SystemConfig).filter(SystemConfig.key == key).first()
        if row:
            row.value = value
        else:
            db.add(SystemConfig(key=key, value=value, description=desc))
    db.commit()


# ============================================================
# 数据窗口
# ============================================================

def compute_window(rule_window_type: str, now: Optional[datetime] = None):
    """返回 (stat_date, start_date_str, end_date_str)。"""
    now = now or datetime.now()
    if (rule_window_type or "today") == "yesterday":
        d = (now - timedelta(days=1)).date()
    else:
        d = now.date()
    s = d.isoformat()
    return d, s, s


# ============================================================
# 指标聚合 → 模板变量
# ============================================================

def _resolve_path(ctx: dict, path: str):
    """从 build_range_context 的 ctx dict 里按点路径取值 (不支持 [*] 数组展开)。"""
    node = ctx
    for part in path.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return None
    return node


def _format_value(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.2f}".rstrip("0").rstrip(".")
    return str(v)


def build_scope_params(db, rule: SmsReportRule, stat_date: _date,
                       start_date: str, end_date: str,
                       channel_id: Optional[int]) -> Dict[str, str]:
    """为一个 scope (汇总 或 单工位) 组装模板变量 dict。

    变量名取 template_param_mapping[path], 缺省用 path 最后一段。
    dict 插入顺序 = metrics 勾选顺序 (腾讯云位置参数按此对位)。
    """
    from backend.services.export_context import build_range_context
    from backend.services import counter_daily

    metrics: List[str] = list(rule.metrics or [])
    mapping: Dict[str, str] = dict(rule.template_param_mapping or {})

    needs_range_ctx = any(not m.startswith("counters_daily.") for m in metrics)
    ctx = {}
    if needs_range_ctx:
        ctx = build_range_context(
            db, start_date=start_date, end_date=end_date,
            project_id=rule.project_id, channel_id=channel_id,
        ) or {}

    daily_counters = {}
    if any(m.startswith("counters_daily.") for m in metrics):
        daily_counters = counter_daily.query_daily(
            stat_date, project_id=rule.project_id, channel_id=channel_id)

    params: Dict[str, str] = {}
    for path in metrics:
        if path.startswith("counters_daily."):
            name = path[len("counters_daily."):]
            if name == "_all":
                value = " ".join(f"{k}{v}" for k, v in daily_counters.items()) or "-"
            elif name == "_keys":
                value = ",".join(daily_counters.keys()) or "-"
            else:
                value = daily_counters.get(name, 0)
        else:
            value = _resolve_path(ctx, path)
        var_name = mapping.get(path) or path.rsplit(".", 1)[-1]
        params[var_name] = _format_value(value)

    for k, v in (rule.extra_params or {}).items():
        params.setdefault(str(k), str(v))
    return params


def _discover_channels(db, rule: SmsReportRule, start_date: str, end_date: str) -> List[int]:
    """分工位模式的工位集合: 规则指定优先, 否则取窗口内有 session 的工位。"""
    ids = [int(c) for c in (rule.channel_ids or []) if c is not None]
    if ids:
        return ids
    from sqlalchemy import func as _f
    q = db.query(DetectionSession.channel_id).filter(
        _f.date(DetectionSession.start_time) >= start_date,
        _f.date(DetectionSession.start_time) <= end_date,
    )
    if rule.project_id is not None:
        q = q.filter(DetectionSession.project_id == rule.project_id)
    return sorted({row[0] or 0 for row in q.distinct().all()})


def build_preview(db, rule: SmsReportRule) -> List[dict]:
    """预览: 返回每个 scope 的变量 dict, 不发送。"""
    stat_date, start_date, end_date = compute_window(rule.data_window_type)
    from backend.services import counter_daily
    counter_daily.flush_now()

    scopes: List[dict] = []
    if rule.group_by_channel:
        for ch in _discover_channels(db, rule, start_date, end_date):
            scopes.append({
                "channel_id": ch,
                "params": build_scope_params(db, rule, stat_date, start_date, end_date, ch),
            })
        if not scopes:
            scopes.append({"channel_id": None, "params": {},
                           "note": "窗口内没有任何工位数据"})
    else:
        # 汇总模式: channel_ids 只勾了一个时按该工位过滤, 多个/全部则跨工位求和
        only = ([int(c) for c in (rule.channel_ids or [])] or [None])
        ch = only[0] if len(only) == 1 else None
        scopes.append({
            "channel_id": ch,
            "params": build_scope_params(db, rule, stat_date, start_date, end_date, ch),
        })
    return scopes


# ============================================================
# 发送
# ============================================================

def _send_with_retry(adapter, phones, params, config, template_code) -> dict:
    result = {"success": False, "error": "未执行", "response": None}
    retry = 0
    for attempt in range(1 + MAX_RETRY):
        result = adapter.send(phones, params, config, template_code=template_code)
        if result.get("success"):
            break
        retry = attempt
        if attempt < MAX_RETRY:
            time.sleep(RETRY_INTERVAL_S)
    result["retry_count"] = retry
    return result


def _run_rule(rule_id: int, source_type: str = "cron",
              force_provider: Optional[str] = None) -> dict:
    """执行一条规则。返回摘要 dict (test-send / trigger_now 消费)。"""
    db = SessionLocal()
    summary = {"rule_id": rule_id, "sent": 0, "failed": 0, "skipped": 0, "details": []}
    try:
        rule = db.query(SmsReportRule).filter(SmsReportRule.id == rule_id).first()
        if not rule:
            summary["status"] = "failed"
            summary["error"] = f"规则 #{rule_id} 不存在"
            return summary

        provider_data = get_provider_config(db)
        provider = force_provider or provider_data["provider"]
        config = provider_data["config"]
        if not provider:
            _finish_rule(db, rule, "failed", "未配置短信服务商")
            summary["status"] = "failed"
            summary["error"] = "未配置短信服务商 (sms.provider)"
            return summary

        try:
            from backend.services.sms_adapters import get_sms_adapter
            adapter = get_sms_adapter(provider)
        except Exception as e:
            _finish_rule(db, rule, "failed", str(e))
            summary["status"] = "failed"
            summary["error"] = str(e)
            return summary

        phones_default = [str(p).strip() for p in (rule.phone_numbers or []) if str(p).strip()]
        if not phones_default:
            _finish_rule(db, rule, "skipped", "未配置手机号")
            summary["status"] = "skipped"
            summary["skipped"] += 1
            summary["error"] = "未配置手机号"
            return summary

        scopes = build_preview(db, rule)
        stat_date, _, _ = compute_window(rule.data_window_type)

        for scope in scopes:
            params = scope.get("params") or {}
            phones = list(phones_default)
            skip = False

            # 插件 hook: 发送前可改写变量/手机号或跳过 (returnable)
            try:
                from backend.plugin_system.hook_dispatch import fire_plugin_hook
                decision = fire_plugin_hook(
                    "daily_report_before_send", "daily_report_before_send", "pre",
                    {
                        "rule_id": rule.id,
                        "rule_name": rule.name,
                        "provider": provider,
                        "stat_date": stat_date.isoformat(),
                        "channel_id": scope.get("channel_id"),
                        "group_by_channel": bool(rule.group_by_channel),
                        "template_params": dict(params),
                        "phone_numbers": list(phones),
                        "source_type": source_type,
                    },
                ) or {}
                if decision.get("skip_send") is True:
                    skip = True
                if isinstance(decision.get("override_params"), dict):
                    params = {str(k): str(v) for k, v in decision["override_params"].items()}
                if isinstance(decision.get("override_phone_numbers"), list):
                    phones = [str(p) for p in decision["override_phone_numbers"] if str(p).strip()]
            except Exception as e:
                print(f"[SmsReport] hook 异常(已忽略): {e}")

            if skip or not phones:
                summary["skipped"] += 1
                summary["details"].append({"channel_id": scope.get("channel_id"),
                                           "skipped": True})
                continue

            result = _send_with_retry(adapter, phones, params, config, rule.template_code)

            log = SmsSendLog(
                rule_id=rule.id, rule_name=rule.name,
                source_type=source_type, provider=provider,
                channel_id=scope.get("channel_id"),
                phone_numbers=phones,
                template_code=(rule.template_code or config.get("template_code")),
                template_params=params,
                success=bool(result.get("success")),
                retry_count=int(result.get("retry_count") or 0),
                error_msg=result.get("error"),
                provider_response=result.get("response"),
            )
            db.add(log)
            db.commit()

            if result.get("success"):
                summary["sent"] += 1
            else:
                summary["failed"] += 1
            summary["details"].append({
                "channel_id": scope.get("channel_id"),
                "success": bool(result.get("success")),
                "error": result.get("error"),
                "params": params,
            })

        if summary["failed"] == 0 and summary["sent"] > 0:
            status = "success"
        elif summary["sent"] > 0:
            status = "partial"
        elif summary["skipped"] > 0 and summary["failed"] == 0:
            status = "skipped"
        else:
            status = "failed"
        err = "; ".join(d.get("error") or "" for d in summary["details"]
                        if d.get("error")) or None
        _finish_rule(db, rule, status, err,
                     sent=summary["sent"], failed=summary["failed"])
        summary["status"] = status
        return summary
    except Exception as e:
        print(f"[SmsReport] rule#{rule_id} 执行异常: {e}")
        summary["status"] = "failed"
        summary["error"] = str(e)
        try:
            rule = db.query(SmsReportRule).filter(SmsReportRule.id == rule_id).first()
            if rule:
                _finish_rule(db, rule, "failed", str(e))
        except Exception:
            pass
        return summary
    finally:
        db.close()


def _finish_rule(db, rule: SmsReportRule, status: str, error: Optional[str],
                 sent: int = 0, failed: int = 0) -> None:
    try:
        rule.last_run_time = datetime.now()
        rule.last_run_status = status
        rule.last_run_error = error
        rule.success_count = (rule.success_count or 0) + sent
        rule.failed_count = (rule.failed_count or 0) + failed
        try:
            from croniter import croniter
            rule.next_run_time = croniter(rule.cron_expression, datetime.now()).get_next(datetime)
        except Exception:
            pass
        db.commit()
    except Exception:
        db.rollback()


# ============================================================
# 调度器 (照搬 export_scheduled 范式)
# ============================================================

def _build_trigger(cron_expr: str):
    from apscheduler.triggers.cron import CronTrigger
    parts = (cron_expr or "0 20 * * *").strip().split()
    if len(parts) == 5:
        minute, hour, day, month, dow = parts
        return CronTrigger(minute=minute, hour=hour, day=day, month=month, day_of_week=dow)
    raise ValueError(f"cron_expression 必须是 5 字段, 收到: {cron_expr!r}")


def _job_id(rule_id: int) -> str:
    return f"{_JOB_PREFIX}{rule_id}"


def start_scheduler() -> None:
    """启动短信日报调度器, 加载所有 enabled 规则。幂等。"""
    global _SCHEDULER
    with _LOCK:
        if _SCHEDULER is not None:
            return
        try:
            from apscheduler.schedulers.background import BackgroundScheduler
        except ImportError:
            print("[SmsReport] APScheduler 未安装, 短信日报功能关闭")
            return
        _SCHEDULER = BackgroundScheduler(daemon=True)
        _SCHEDULER.start()
        print("[SmsReport] BackgroundScheduler 启动")
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
        print("[SmsReport] BackgroundScheduler 已停止")


def _reload_all_jobs_locked() -> None:
    if _SCHEDULER is None:
        return
    db = SessionLocal()
    try:
        for job in list(_SCHEDULER.get_jobs()):
            if job.id.startswith(_JOB_PREFIX):
                _SCHEDULER.remove_job(job.id)
        rules = db.query(SmsReportRule).filter(SmsReportRule.enabled.is_(True)).all()
        for rule in rules:
            try:
                trigger = _build_trigger(rule.cron_expression)
            except Exception as e:
                print(f"[SmsReport] rule#{rule.id} cron 解析失败: {e}")
                continue
            _SCHEDULER.add_job(
                _run_rule, trigger=trigger, args=[rule.id],
                id=_job_id(rule.id), name=f"sms-{rule.name}",
                replace_existing=True, max_instances=1, coalesce=True,
            )
            try:
                from croniter import croniter
                rule.next_run_time = croniter(rule.cron_expression, datetime.now()).get_next(datetime)
            except Exception:
                rule.next_run_time = None
        db.commit()
        print(f"[SmsReport] 加载 {len(rules)} 条 enabled 规则")
    finally:
        db.close()


def reload_rule(rule_id: int) -> None:
    with _LOCK:
        if _SCHEDULER is None:
            return
        try:
            _SCHEDULER.remove_job(_job_id(rule_id))
        except Exception:
            pass
        db = SessionLocal()
        try:
            rule = db.query(SmsReportRule).filter(SmsReportRule.id == rule_id).first()
            if not rule or not rule.enabled:
                return
            try:
                trigger = _build_trigger(rule.cron_expression)
            except Exception as e:
                print(f"[SmsReport] rule#{rule.id} cron 解析失败: {e}")
                return
            _SCHEDULER.add_job(
                _run_rule, trigger=trigger, args=[rule.id],
                id=_job_id(rule.id), name=f"sms-{rule.name}",
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
    with _LOCK:
        if _SCHEDULER is None:
            return
        try:
            _SCHEDULER.remove_job(_job_id(rule_id))
        except Exception:
            pass


def trigger_now(rule_id: int, force_provider: Optional[str] = None) -> dict:
    """立即执行一次 (test-send API 用)。同步阻塞返回结果。"""
    return _run_rule(rule_id, source_type="manual_test", force_provider=force_provider)
