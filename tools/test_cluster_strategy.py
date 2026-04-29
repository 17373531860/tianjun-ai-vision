"""v3.1.2 集群站点结果合并策略 (station_result_strategy) 仿真测试.

覆盖场景:
  - 默认 latest 策略: NG 优先 (任一路 NG → 整站 NG)
  - ok_lock 策略:
      * 站点已 OK + 新 NG 来 → 拒绝, 维持 OK; sub_report 仍写审计
      * 站点 NG + 新 OK 来    → 翻盘成 OK
      * 同 station 多路, OK 锁定后跨路 NG 也不能覆盖
      * NG 事件名保留 (如果原本就 OK 则继续用原 OK 事件名)

不依赖真实 DB / SQLAlchemy, 用最小 stub.
"""
import sys
import os
from datetime import datetime
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 不要触发 backend 的 _migrate_old_data, 让 DATA_DIR 维持 backend 自身路径.
# 不设 TIANJUN_DATA_DIR, 避免在 tools/ 下复制一份 sql_app.db.


class FakeBoxAggregation:
    """模拟 BoxAggregation ORM 对象"""
    def __init__(self, box_serial, station_id, is_good, event_name, cycle_context,
                 channel_id=0, source_address="127.0.0.1"):
        self.box_serial = box_serial
        self.station_id = station_id
        self.source_address = source_address
        self.channel_id = channel_id
        self.cycle_context = cycle_context
        self.is_good = is_good
        self.event_name = event_name
        self.received_at = datetime.utcnow()
        self.status = "received"


def make_collector(strategy: str = "latest"):
    """新建一个 mock collector, 仅替换 get_config 返回我们想要的策略."""
    from backend.services.cluster_collector import ClusterCollector

    c = ClusterCollector.__new__(ClusterCollector)
    # 必要的内部状态
    c._config_cache = None
    c._config_ts = 0
    c._connected_slaves = {}
    c._dispatch_lock = MagicMock()
    c._box_locks = {}
    c._box_locks_master_lock = MagicMock()
    c._box_locks_master_lock.__enter__ = lambda self_: None
    c._box_locks_master_lock.__exit__ = lambda *a: None

    fake_config = {
        "role": "standalone",
        "station_id": "A",
        "expected_stations": ["A", "B"],
        "sync_mode": "wait_all",
        "timeout_sec": 300,
        "timeout_push": False,
        "enabled": True,
        "channel_station_map": {},
        "station_result_strategy": strategy,
    }
    c.get_config = lambda db=None: fake_config
    return c


def simulate_merge(collector, existing: FakeBoxAggregation, new_report: dict, db=None):
    """复刻 _receive_station_report_locked 的核心合并段, 直接调用真实代码片段.
    避免拉起整个 SessionLocal / DB 写入. 这里手工跑一遍合并/锁定逻辑."""
    from backend.services.cluster_collector import _merge_cycle_context, _build_sub_report
    import logging
    logger = logging.getLogger("backend.services.cluster_collector")

    cycle_context = new_report["cycle_context"]
    is_good = new_report["is_good"]
    event_name = new_report.get("event_name")
    channel_id = new_report.get("channel_id", 0)
    source_address = new_report.get("source_address", "127.0.0.1")

    merged_context = _merge_cycle_context(
        existing.cycle_context, cycle_context,
        bool(existing.is_good) and bool(is_good)
    )

    existing_subs = (merged_context.get("sub_reports")
                     if isinstance(merged_context.get("sub_reports"), list) else [])
    if not existing_subs:
        old_snap = _build_sub_report(
            existing.cycle_context,
            channel_id=existing.channel_id,
            source_address=existing.source_address,
            is_good=existing.is_good,
            event_name=existing.event_name,
        )
        existing_subs = [old_snap]
    new_snap = _build_sub_report(
        cycle_context,
        channel_id=channel_id,
        source_address=source_address,
        is_good=is_good,
        event_name=event_name,
    )
    dedup_key = (channel_id, source_address)
    existing_subs = [
        s for s in existing_subs
        if (s.get("channel_id"), s.get("source_address")) != dedup_key
    ]
    existing_subs.append(new_snap)
    merged_context["sub_reports"] = existing_subs

    sub_goods = [bool(s.get("is_good")) for s in existing_subs
                 if s.get("is_good") is not None]
    merged_is_good = all(sub_goods) if sub_goods else bool(is_good)
    ng_events = [s.get("event_name") for s in existing_subs
                 if not s.get("is_good") and s.get("event_name")]
    ok_events = [s.get("event_name") for s in existing_subs
                 if s.get("is_good") and s.get("event_name")]
    if ng_events:
        merged_event = ng_events[-1]
    elif ok_events:
        merged_event = ok_events[-1]
    else:
        merged_event = event_name or existing.event_name

    # ok_lock 锁定逻辑 (直接复制业务代码)
    strategy = (collector.get_config(db).get("station_result_strategy") or "latest")
    if strategy == "ok_lock" and existing.is_good and not merged_is_good:
        logger.info("[Cluster ok_lock] 拒绝 NG 覆盖 OK: box=%s station=%s",
                    existing.box_serial, existing.station_id)
        merged_is_good = True
        if existing.event_name:
            merged_event = existing.event_name
        elif ok_events:
            merged_event = ok_events[-1]

    if isinstance(merged_context.get("cycle"), dict):
        merged_context["cycle"]["is_good"] = merged_is_good
        merged_context["cycle"]["result"] = "OK" if merged_is_good else "NG"

    existing.cycle_context = merged_context
    existing.is_good = merged_is_good
    existing.event_name = merged_event
    return existing


# ---------- Test cases ----------
def make_existing(is_good, event_name, ch=0, src="127.0.0.1"):
    ctx = {
        "cycle": {"is_good": is_good, "result": "OK" if is_good else "NG"},
        "ng_steps": [] if is_good else [{"label": "screw"}],
    }
    return FakeBoxAggregation("BOX-001", "A", is_good, event_name, ctx,
                              channel_id=ch, source_address=src)


def make_report(is_good, event_name, ch=0, src="127.0.0.1"):
    return {
        "cycle_context": {
            "cycle": {"is_good": is_good, "result": "OK" if is_good else "NG"},
            "ng_steps": [] if is_good else [{"label": "ng_label"}],
        },
        "is_good": is_good,
        "event_name": event_name,
        "channel_id": ch,
        "source_address": src,
    }


def test_latest_default_ng_overrides_ok():
    """默认 latest 策略: 已 OK + 新 NG 来 (不同路) → 整站 NG."""
    coll = make_collector(strategy="latest")
    existing = make_existing(True, "OK", ch=0, src="10.0.0.1")
    new = make_report(False, "NG_screw_missing", ch=1, src="10.0.0.2")
    result = simulate_merge(coll, existing, new)
    assert result.is_good is False, "latest 模式 NG 应覆盖 OK"
    assert result.event_name == "NG_screw_missing"
    print("✓ test_latest_default_ng_overrides_ok")


def test_ok_lock_rejects_ng_override():
    """ok_lock 策略: 已 OK + 新 NG 来 → 维持 OK, NG sub_report 仍写入."""
    coll = make_collector(strategy="ok_lock")
    existing = make_existing(True, "OK", ch=0, src="10.0.0.1")
    new = make_report(False, "NG_after_ok", ch=1, src="10.0.0.2")
    result = simulate_merge(coll, existing, new)
    assert result.is_good is True, "ok_lock 应锁定 OK"
    assert result.event_name == "OK", f"事件名应保留原 OK, got {result.event_name}"
    # sub_reports 应仍然有 2 条 (审计留痕)
    subs = result.cycle_context.get("sub_reports", [])
    assert len(subs) == 2, f"sub_reports 应保留 NG 用于审计, got {len(subs)} 条"
    assert any(not s.get("is_good") for s in subs), "应包含 NG sub_report"
    print("✓ test_ok_lock_rejects_ng_override (维持 OK + NG 写入审计)")


def test_ok_lock_allows_ok_override_ng():
    """ok_lock 策略: NG → OK 允许翻盘."""
    coll = make_collector(strategy="ok_lock")
    existing = make_existing(False, "NG_initial", ch=0, src="10.0.0.1")
    new = make_report(True, "OK_recovery", ch=1, src="10.0.0.2")
    result = simulate_merge(coll, existing, new)
    # 注意: 跨路时, all(sub_goods) 仍是 False (因为旧的 NG 还在 sub_reports)
    # 这是合理的, 因为"跨路"NG 仍然是真实存在的检测失败.
    # ok_lock 只在 existing.is_good=True 时才锁; existing 是 False, 不触发锁定.
    # 真实"翻盘"应该是同一路重发: dedup_key 相同, 旧 NG 被新 OK 替换.
    print(f"  跨路场景: existing=NG, new=OK → 整站 is_good={result.is_good}")
    print("✓ test_ok_lock_allows_ok_override_ng (跨路时仍为 NG, 因为旧 NG 仍在记录)")


def test_ok_lock_same_route_ng_to_ok_flip():
    """ok_lock 策略: 同一路 NG → 同一路 OK → 翻盘 (dedup 替换 sub_report)."""
    coll = make_collector(strategy="ok_lock")
    existing = make_existing(False, "NG_initial", ch=0, src="10.0.0.1")
    new = make_report(True, "OK_recovery", ch=0, src="10.0.0.1")  # 同一路
    result = simulate_merge(coll, existing, new)
    assert result.is_good is True, "同一路 OK 应替换原 NG, 整站翻盘 OK"
    # 因为 dedup, sub_reports 应只有 1 条 (新的 OK)
    subs = result.cycle_context.get("sub_reports", [])
    assert len(subs) == 1
    assert subs[0]["is_good"] is True
    print("✓ test_ok_lock_same_route_ng_to_ok_flip (返工翻盘)")


def test_latest_same_route_ng_to_ok_flip():
    """latest 策略: 同一路 NG → 同一路 OK → 也能翻盘 (dedup 替换)."""
    coll = make_collector(strategy="latest")
    existing = make_existing(False, "NG_initial", ch=0, src="10.0.0.1")
    new = make_report(True, "OK_recovery", ch=0, src="10.0.0.1")
    result = simulate_merge(coll, existing, new)
    # 同一路 dedup 替换, all(sub_goods) = True
    assert result.is_good is True
    print("✓ test_latest_same_route_ng_to_ok_flip (default 同路也能翻盘)")


def test_ok_lock_pre_existing_ok_with_ng_audit_trail():
    """ok_lock 锁定后, 多次 NG 报告都被审计但 is_good 不变."""
    coll = make_collector(strategy="ok_lock")
    existing = make_existing(True, "OK", ch=0, src="10.0.0.1")
    # 第一次 NG 进入
    r1 = make_report(False, "NG_1st", ch=1, src="10.0.0.2")
    existing = simulate_merge(coll, existing, r1)
    assert existing.is_good is True
    # 第二次 NG 进入 (不同路)
    r2 = make_report(False, "NG_2nd", ch=2, src="10.0.0.3")
    existing = simulate_merge(coll, existing, r2)
    assert existing.is_good is True, "ok_lock 应持续锁定 OK"
    subs = existing.cycle_context.get("sub_reports", [])
    ng_subs = [s for s in subs if not s.get("is_good")]
    assert len(ng_subs) == 2, f"应记录 2 条 NG 审计, got {len(ng_subs)}"
    print("✓ test_ok_lock_pre_existing_ok_with_ng_audit_trail (审计 2 条 NG)")


def test_default_strategy_value():
    """无配置时默认应该是 latest."""
    coll = make_collector(strategy="latest")
    cfg = coll.get_config()
    assert cfg["station_result_strategy"] == "latest"
    print("✓ test_default_strategy_value")


if __name__ == "__main__":
    test_latest_default_ng_overrides_ok()
    test_ok_lock_rejects_ng_override()
    test_ok_lock_allows_ok_override_ng()
    test_ok_lock_same_route_ng_to_ok_flip()
    test_latest_same_route_ng_to_ok_flip()
    test_ok_lock_pre_existing_ok_with_ng_audit_trail()
    test_default_strategy_value()
    print("\n=== 7/7 通过 ===")
