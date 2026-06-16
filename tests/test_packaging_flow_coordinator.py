"""包装箱结算协调器 — 状态机全流程回归护栏 (v3.21 M2/M3/M6).

把"工单→箱→托盘"三层状态机的关键路径固化成永久护栏:
  正常完成 / 漏箱 / 多箱 / 托盘不达标 / 标签归一化同号 / 停止三策略 / 待机开关 / 完成回推.

用真 DB (conftest 已隔离临时库 + 建表) 验证落库, 三个外部钩子 (拉单/报警/回推) 用 mock 注入.
每个用例前清空包装表 + 复位协调器单例, 避免 module-level 单例串污染.
"""
import pytest

from backend.services.packaging_flow_coordinator import get_coordinator
from backend.db.database import SessionLocal
from backend.models.mes_models import PackagingFlowConfig, PackagingFlowRun


@pytest.fixture(autouse=True)
def _clean_packaging(client):
    coord = get_coordinator()
    coord.cleanup_for_testing()
    db = SessionLocal()
    db.query(PackagingFlowRun).delete()
    db.query(PackagingFlowConfig).delete()
    db.commit()
    db.close()
    yield
    coord.cleanup_for_testing()


def _setup_flow(client, **cfg_over):
    """建一条启用配置 + reload 进协调器内存, 返回 (coord, config_id)."""
    payload = {
        "name": "__test_pkg",
        "enabled": True,
        "channel_id": 0,
        "trays_per_box_fixed": 2,
        "on_mes_fail": "offline",
    }
    payload.update(cfg_over)
    r = client.post("/api/v1/packaging-flows", json=payload)
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    coord = get_coordinator()
    coord.reload_configs(SessionLocal())
    return coord, cid


def test_normal_two_box_complete_ok(client):
    """两箱全做满 → 扫新工单收尾 → 上一单 completed/OK, 无漏箱/多箱报警."""
    coord, cid = _setup_flow(client)
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 2})
    alarms = []
    coord.set_alarm_sink(lambda c, k, m: alarms.append(k))
    db = SessionLocal()

    coord.on_scan("ORD1", db, channel_id=0)          # 开工单 box_total=2
    coord.on_scan("ORD1", db, channel_id=0)          # 开箱1
    coord.on_cycle_settled(0, 1, True, db)
    coord.on_cycle_settled(0, 2, True, db)           # 箱1 满 2 托盘
    coord.on_scan("ORD1", db, channel_id=0)          # 结算箱1 + 开箱2
    coord.on_cycle_settled(0, 3, True, db)
    coord.on_cycle_settled(0, 4, True, db)           # 箱2 满 2 托盘
    coord.on_scan("ORD2", db, channel_id=0)          # 结算箱2 + 完成 ORD1

    row = db.query(PackagingFlowRun).filter_by(order_no="ORD1").first()
    assert row is not None
    assert row.status == "completed"
    assert row.final_result == "OK"
    assert row.box_done == 2 and row.box_ng == 0
    assert "short_box" not in alarms and "over_box" not in alarms


def test_short_box_void_aborts(client):
    """应做 3 箱只做 1 箱就扫新标签 → 漏箱报警, void 策略下工单作废."""
    coord, cid = _setup_flow(client, on_short_box="void")
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 3})
    alarms = []
    coord.set_alarm_sink(lambda c, k, m: alarms.append(k))
    db = SessionLocal()

    coord.on_scan("ORD1", db, channel_id=0)
    coord.on_scan("ORD1", db, channel_id=0)
    coord.on_cycle_settled(0, 1, True, db)
    coord.on_cycle_settled(0, 2, True, db)
    coord.on_scan("ORD2", db, channel_id=0)          # 漏箱

    assert "short_box" in alarms
    row = db.query(PackagingFlowRun).filter_by(order_no="ORD1").first()
    assert row.status == "aborted"


def test_over_box_alarm(client):
    """应做 1 箱, 做满后还扫同号 → 多箱报警."""
    coord, cid = _setup_flow(client)
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 1})
    alarms = []
    coord.set_alarm_sink(lambda c, k, m: alarms.append(k))
    db = SessionLocal()

    coord.on_scan("ORD1", db, channel_id=0)
    coord.on_scan("ORD1", db, channel_id=0)          # 箱1
    coord.on_cycle_settled(0, 1, True, db)
    coord.on_cycle_settled(0, 2, True, db)
    coord.on_scan("ORD1", db, channel_id=0)          # 结算箱1 box_done=1>=1 → 多箱

    assert "over_box" in alarms


def test_tray_ng_alarm(client):
    """托盘检测不达标 (is_good=False) → 托盘报警, 不计入合格托盘."""
    coord, cid = _setup_flow(client)
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 2})
    alarms = []
    coord.set_alarm_sink(lambda c, k, m: alarms.append(k))
    db = SessionLocal()

    coord.on_scan("ORD1", db, channel_id=0)
    coord.on_scan("ORD1", db, channel_id=0)          # 箱1
    coord.on_cycle_settled(0, 1, False, db)          # NG 托盘

    assert "tray_ng" in alarms
    state = coord.get_state(cid)
    assert state["current_box_trays"] == 0           # NG 不计入


def test_label_strip_hyphen_same_order(client):
    """去连字符比对: 扫 'ORD-1' 与 'ORD1' 视为同一工单 → 第二次扫开箱而非新工单."""
    coord, cid = _setup_flow(client, label_match="strip_hyphen")
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 2})
    coord.set_alarm_sink(lambda c, k, m: None)
    db = SessionLocal()

    coord.on_scan("ORD-1", db, channel_id=0)         # norm=ORD1 开工单
    coord.on_scan("ORD1", db, channel_id=0)          # norm=ORD1 同号 → 开箱1
    state = coord.get_state(cid)
    assert state["order_no"] == "ORD1"
    assert state["current_box_index"] == 1


def test_push_on_complete_calls_pusher(client):
    """push_on_complete=True 时工单完成回推 MES, 落 mes_pushed=True."""
    coord, cid = _setup_flow(client, push_on_complete=True)
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 1})
    coord.set_alarm_sink(lambda c, k, m: None)
    pushed = []
    coord.set_mes_pusher(lambda c, r: pushed.append(r["order_no"]))
    db = SessionLocal()

    coord.on_scan("ORD1", db, channel_id=0)
    coord.on_scan("ORD1", db, channel_id=0)
    coord.on_cycle_settled(0, 1, True, db)
    coord.on_scan("ORD2", db, channel_id=0)          # 完成 ORD1 → 回推

    assert pushed == ["ORD1"]
    row = db.query(PackagingFlowRun).filter_by(order_no="ORD1").first()
    assert row.mes_pushed is True


def test_forced_stop_keep_preserves_run(client):
    """停止策略=keep: 进行中工单原样保留, 不结算不作废 (可待恢复)."""
    coord, cid = _setup_flow(client, on_forced_stop="keep")
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 2})
    coord.set_alarm_sink(lambda c, k, m: None)
    db = SessionLocal()

    coord.on_scan("ORD1", db, channel_id=0)
    coord.on_scan("ORD1", db, channel_id=0)          # 箱1
    coord.on_forced_settle_by_channel(0, db, is_standby=False)

    state = coord.get_state(cid)
    assert state is not None and state["current_box_index"] == 1


def test_label_exact_keeps_hyphen_distinct(client):
    """exact 模式不去连字符: 'ORD-1' 与 'ORD1' 视为不同号 → 第二次扫=新工单(切单)."""
    coord, cid = _setup_flow(client, label_match="exact")
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 0})
    coord.set_alarm_sink(lambda c, k, m: None)
    db = SessionLocal()

    coord.on_scan("ORD-1", db, channel_id=0)         # 开工单 order_no=ORD-1
    coord.on_scan("ORD1", db, channel_id=0)          # 不同号 → 切新工单
    state = coord.get_state(cid)
    assert state["order_no"] == "ORD1"
    assert state["current_box_index"] == 0           # 新工单刚开, 还没开箱


def test_label_digits_only_same_order(client):
    """digits_only 模式: 'AB-12' 与 '12' 只留数字均=12 → 同号 → 第二次扫开箱."""
    coord, cid = _setup_flow(client, label_match="digits_only")
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 2})
    coord.set_alarm_sink(lambda c, k, m: None)
    db = SessionLocal()

    coord.on_scan("AB-12", db, channel_id=0)         # norm=12 开工单
    coord.on_scan("12", db, channel_id=0)            # norm=12 同号 → 开箱1
    state = coord.get_state(cid)
    assert state["order_no"] == "12"
    assert state["current_box_index"] == 1


def test_box_count_formula(client):
    """公式箱数: dispatch_qty=8 ÷ (每箱2托盘 × 每托盘2件)=4 → 应做 2 箱."""
    coord, cid = _setup_flow(client, box_count_source="formula",
                             box_count_field="dispatch_qty",
                             trays_per_box_fixed=2,
                             tray_qty_mode="fixed", tray_qty_fixed=2)
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 8})
    coord.set_alarm_sink(lambda c, k, m: None)
    db = SessionLocal()

    coord.on_scan("ORD1", db, channel_id=0)
    state = coord.get_state(cid)
    assert state["box_total"] == 2


def test_trays_per_box_by_spec_short_tray_ng(client):
    """按规格定每箱托盘数: spec=S1→需3托盘; 只装2托盘就封箱 → 该箱判 NG."""
    coord, cid = _setup_flow(client, trays_per_box_mode="by_spec",
                             trays_per_box_table={"S1": 3}, trays_per_box_fixed=4)
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 0, "spec": "S1"})
    alarms = []
    coord.set_alarm_sink(lambda c, k, m: alarms.append(k))
    db = SessionLocal()

    coord.on_scan("ORD1", db, channel_id=0)          # spec=S1 → need=3
    coord.on_scan("ORD1", db, channel_id=0)          # 箱1
    coord.on_cycle_settled(0, 1, True, db)
    coord.on_cycle_settled(0, 2, True, db)           # 仅 2 托盘
    coord.on_scan("ORD2", db, channel_id=0)          # 封箱结算 → 2/3 不足

    assert "box_ng" in alarms
    row = db.query(PackagingFlowRun).filter_by(order_no="ORD1").first()
    assert row.box_ng == 1


def test_short_box_redo_keeps_order(client):
    """漏箱策略=redo: 漏箱报警但不切单, 工单保留等工人扫回原标签补做."""
    coord, cid = _setup_flow(client, on_short_box="redo")
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 3})
    alarms = []
    coord.set_alarm_sink(lambda c, k, m: alarms.append(k))
    db = SessionLocal()

    coord.on_scan("ORD1", db, channel_id=0)          # box_total=3
    coord.on_scan("ORD1", db, channel_id=0)          # 箱1
    coord.on_cycle_settled(0, 1, True, db)
    coord.on_cycle_settled(0, 2, True, db)
    coord.on_scan("ORD2", db, channel_id=0)          # 漏箱 → redo 不切单

    assert "short_box" in alarms
    state = coord.get_state(cid)
    assert state is not None and state["order_no"] == "ORD1"   # 仍是原工单


def test_forced_stop_abort(client):
    """停止策略=abort: 进行中工单直接作废, 不结算."""
    coord, cid = _setup_flow(client, on_forced_stop="abort")
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 2})
    coord.set_alarm_sink(lambda c, k, m: None)
    db = SessionLocal()

    coord.on_scan("ORD1", db, channel_id=0)
    coord.on_scan("ORD1", db, channel_id=0)
    coord.on_forced_settle_by_channel(0, db, is_standby=False)

    row = db.query(PackagingFlowRun).filter_by(order_no="ORD1").first()
    assert row.status == "aborted"
    assert coord.get_state(cid) is None


def test_forced_stop_partial_pass(client):
    """未满箱 + on_forced_stop_partial=pass: 停止收尾时把未满箱判合格."""
    coord, cid = _setup_flow(client, on_forced_stop="settle",
                             on_forced_stop_partial="pass")
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 1})
    coord.set_alarm_sink(lambda c, k, m: None)
    db = SessionLocal()

    coord.on_scan("ORD1", db, channel_id=0)
    coord.on_scan("ORD1", db, channel_id=0)          # 箱1
    coord.on_cycle_settled(0, 1, True, db)           # 仅 1 托盘 (需 2, 未满)
    coord.on_forced_settle_by_channel(0, db, is_standby=False)

    row = db.query(PackagingFlowRun).filter_by(order_no="ORD1").first()
    assert row.status == "completed"
    assert row.box_ng == 0                           # partial=pass → 未满箱也算 OK
    assert row.final_result == "OK"


def test_label_len_alarm(client):
    """标签长度校验: label_len=6, 扫 5 位 → label_len 报警."""
    coord, cid = _setup_flow(client, label_len=6)
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 0})
    alarms = []
    coord.set_alarm_sink(lambda c, k, m: alarms.append(k))
    db = SessionLocal()

    coord.on_scan("12345", db, channel_id=0)         # 5 位 ≠ 6
    assert "label_len" in alarms


def test_mes_fail_block_does_not_open(client):
    """拉单失败 + on_mes_fail=block: 报警 + 不开工单 (零进行中)."""
    coord, cid = _setup_flow(client, on_mes_fail="block")
    coord.set_mes_fetcher(lambda c, o: None)         # 拉单失败
    alarms = []
    coord.set_alarm_sink(lambda c, k, m: alarms.append(k))
    db = SessionLocal()

    coord.on_scan("ORD1", db, channel_id=0)
    assert "mes_fail" in alarms
    assert coord.get_state(cid) is None              # block → 没开工单


def test_mes_fail_offline_opens_with_zero_total(client):
    """拉单失败 + on_mes_fail=offline: 报警但仍开工单, 箱数未知=0(不判漏/多箱)."""
    coord, cid = _setup_flow(client, on_mes_fail="offline")
    coord.set_mes_fetcher(lambda c, o: None)
    alarms = []
    coord.set_alarm_sink(lambda c, k, m: alarms.append(k))
    db = SessionLocal()

    coord.on_scan("ORD1", db, channel_id=0)
    assert "mes_fail" in alarms
    state = coord.get_state(cid)
    assert state is not None and state["box_total"] == 0


def test_standby_skip_then_stop_settles(client):
    """待机不收尾 (forced_settle_on_standby=False): 待机保留, 停止才结算完成."""
    coord, cid = _setup_flow(client, on_forced_stop="settle",
                             forced_settle_on_standby=False)
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 1})
    coord.set_alarm_sink(lambda c, k, m: None)
    db = SessionLocal()

    coord.on_scan("ORD1", db, channel_id=0)
    coord.on_scan("ORD1", db, channel_id=0)
    coord.on_forced_settle_by_channel(0, db, is_standby=True)   # 待机不结算
    assert coord.get_state(cid) is not None
    coord.on_forced_settle_by_channel(0, db, is_standby=False)  # 停止结算
    assert coord.get_state(cid) is None


# =============================================================
# 组⑥ 标签不符判定 (on_label_mismatch off/warn/block)
# =============================================================

def test_label_mismatch_warn_switches_no_short(client):
    """开工后首个箱标签不符 (box_index==0): warn 报警 + 切新工单, 不叠加漏箱."""
    coord, cid = _setup_flow(client, on_label_mismatch="warn")
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 2})
    alarms = []
    coord.set_alarm_sink(lambda c, k, m: alarms.append(k))
    db = SessionLocal()

    coord.on_scan("ORD1", db, channel_id=0)     # 开工单 ORD1
    coord.on_scan("ORD2", db, channel_id=0)     # box_index==0 扫不同号 → label_mismatch + 切 ORD2

    assert "label_mismatch" in alarms
    assert "short_box" not in alarms            # 没做箱, 不算漏箱
    state = coord.get_state(cid)
    assert state["order_no"] == "ORD2" and state["current_box_index"] == 0


def test_label_mismatch_block_keeps_order(client):
    """block 模式: 报警且不切, 丢弃本次扫码; 扫回正确标签后正常开箱."""
    coord, cid = _setup_flow(client, on_label_mismatch="block")
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 2})
    alarms = []
    coord.set_alarm_sink(lambda c, k, m: alarms.append(k))
    db = SessionLocal()

    coord.on_scan("ORD1", db, channel_id=0)
    coord.on_scan("ORD2", db, channel_id=0)     # 不符 → 报警 + 不切
    assert "label_mismatch" in alarms
    assert coord.get_state(cid)["order_no"] == "ORD1"

    coord.on_scan("ORD1", db, channel_id=0)     # 扫回正确标签 → 开箱1
    assert coord.get_state(cid)["current_box_index"] == 1


def test_label_mismatch_off_legacy(client):
    """off 模式: 不判标签不符, 扫不同号走旧逻辑 (box_done<total → 漏箱, redo 不切)."""
    coord, cid = _setup_flow(client, on_label_mismatch="off", on_short_box="redo")
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 2})
    alarms = []
    coord.set_alarm_sink(lambda c, k, m: alarms.append(k))
    db = SessionLocal()

    coord.on_scan("ORD1", db, channel_id=0)
    coord.on_scan("ORD2", db, channel_id=0)     # off: 不报 label_mismatch, 走漏箱判定
    assert "label_mismatch" not in alarms
    assert "short_box" in alarms
    assert coord.get_state(cid)["order_no"] == "ORD1"   # redo 不切


# =============================================================
# 组⑥ 异常 → 项目事件路由 (_real_alarm_sink)
# =============================================================

def test_real_alarm_sink_routes_to_configured_event(monkeypatch):
    """配了 event_id → 借 VSM.fire_external_event_response 触发该事件, 不走默认报警."""
    from backend.services import packaging_flow_coordinator as pkg

    fired = []

    class _FakeVSM:
        def fire_external_event_response(self, eid, reason, source="external"):
            fired.append((eid, source))
            return True

    class _FakeMgr:
        channels = {0: _FakeVSM()}

    monkeypatch.setattr(
        "backend.api.channel_manager.get_channel_manager", lambda: _FakeMgr())

    cfg = {"channel_id": 0, "event_short_box": 7}
    pkg._real_alarm_sink(cfg, "short_box", "漏箱")
    assert fired == [(7, "packaging")]


def test_real_alarm_sink_falls_back_to_default(monkeypatch):
    """没配 event_id → 退回默认通用报警 (alarm_router.trigger_alarm event2)."""
    from backend.services import packaging_flow_coordinator as pkg

    triggered = []
    monkeypatch.setattr(
        "backend.api.alarm.alarm_router",
        type("_R", (), {"trigger_alarm": lambda self, et, channel_id=0: triggered.append((et, channel_id))})(),
    )

    cfg = {"channel_id": 0}   # 未配任何 event 字段
    pkg._real_alarm_sink(cfg, "short_box", "漏箱")
    assert triggered == [("event2", 0)]


# =============================================================
# v3.22 上银 MES 闭环: sliders 计数口径 + 尾箱 + 反向目标 + 塞工单 gate + 自动切项目
# =============================================================

def test_compute_box_plan_pure():
    """尾箱纯算法: 非整除取余数 / 整除也标尾箱(目标=每箱数) / 单箱 / 非法兜底."""
    from backend.services.packaging_flow_coordinator import compute_box_plan
    assert compute_box_plan(250, 96) == (3, 58)    # 非整除 → 尾箱余数
    assert compute_box_plan(240, 24) == (10, 24)   # 整除 → 10 箱, 尾箱仍 24
    assert compute_box_plan(96, 96) == (1, 96)      # 单箱
    assert compute_box_plan(30, 24) == (2, 6)        # 尾箱 6
    assert compute_box_plan(0, 96) == (0, 0)         # 滑块总数缺失兜底
    assert compute_box_plan(96, 0) == (0, 0)         # 每箱数缺失兜底


def test_sliders_box_plan_and_open_first_box(client):
    """sliders 口径扫工单: 拉滑块总数 → 算箱数/尾箱, 立即开第 1 箱, 字段落库."""
    coord, cid = _setup_flow(client, count_unit="sliders",
                             items_per_box_source="config", items_per_box_fixed=24,
                             slider_total_field="dispatch_qty")
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 250})   # 250/24 → 11 箱, 尾箱 10
    coord.set_alarm_sink(lambda c, k, m: None)
    db = SessionLocal()

    coord.on_scan("ORD1", db, channel_id=0)
    state = coord.get_state(cid)
    assert state["count_unit"] == "sliders"
    assert state["box_total"] == 11
    assert state["tail_target"] == 10
    assert state["items_per_box"] == 24
    assert state["current_box_index"] == 1            # 立即开第 1 箱
    row = db.query(PackagingFlowRun).filter_by(order_no="ORD1").first()
    assert row.count_unit == "sliders"
    assert row.slider_total == 250 and row.tail_target == 10 and row.items_per_box == 24


def test_sliders_normal_complete_and_box_target_hook(client):
    """sliders 两箱全做满 → completed/OK; 反向钩子按"普通箱→尾箱"依次设当前箱目标."""
    coord, cid = _setup_flow(client, count_unit="sliders",
                             items_per_box_source="config", items_per_box_fixed=24)
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 48})    # 2 箱, 尾箱 24
    coord.set_alarm_sink(lambda c, k, m: None)
    targets = []
    coord.set_box_target_setter(lambda ch, t: targets.append((ch, t)))
    db = SessionLocal()

    coord.on_scan("ORD1", db, channel_id=0)                     # 开箱1, 设目标24
    coord.on_cycle_settled(0, 1, True, db, slider_count=24)     # 箱1满 → 开箱2(尾箱)设目标24
    coord.on_cycle_settled(0, 2, True, db, slider_count=24)     # 尾箱满 → 完成

    row = db.query(PackagingFlowRun).filter_by(order_no="ORD1").first()
    assert row.status == "completed" and row.final_result == "OK"
    assert row.box_done == 2 and row.box_ng == 0
    assert targets == [(0, 24), (0, 24)]              # 普通箱24 → 尾箱24


def test_sliders_tail_uses_remainder_target(client):
    """尾箱目标 = 余数: 总数30/每箱24 → 尾箱目标6, 反向钩子设到6, 进箱6判OK."""
    coord, cid = _setup_flow(client, count_unit="sliders",
                             items_per_box_source="config", items_per_box_fixed=24)
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 30})    # 2 箱, 尾箱 6
    coord.set_alarm_sink(lambda c, k, m: None)
    targets = []
    coord.set_box_target_setter(lambda ch, t: targets.append(t))
    db = SessionLocal()

    coord.on_scan("ORD1", db, channel_id=0)
    coord.on_cycle_settled(0, 1, True, db, slider_count=24)     # 箱1普通满 → 开尾箱设目标6
    coord.on_cycle_settled(0, 2, True, db, slider_count=6)      # 尾箱进6 = 目标6 → OK

    row = db.query(PackagingFlowRun).filter_by(order_no="ORD1").first()
    assert row.status == "completed" and row.final_result == "OK" and row.box_ng == 0
    assert targets == [24, 6]


def test_sliders_box_ng_on_wrong_slider_count(client):
    """进箱滑块数 ≠ 当前箱目标 → 该箱判 NG + box_ng 报警."""
    coord, cid = _setup_flow(client, count_unit="sliders",
                             items_per_box_source="config", items_per_box_fixed=24)
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 24})    # 1 箱(=尾箱), 目标24
    alarms = []
    coord.set_alarm_sink(lambda c, k, m: alarms.append(k))
    db = SessionLocal()

    coord.on_scan("ORD1", db, channel_id=0)
    coord.on_cycle_settled(0, 1, True, db, slider_count=20)     # 20 ≠ 24 → NG

    row = db.query(PackagingFlowRun).filter_by(order_no="ORD1").first()
    assert row.status == "completed" and row.box_done == 1
    assert row.box_ng == 1 and row.final_result == "NG"
    assert "box_ng" in alarms


def test_sliders_box_ng_on_step_fail(client):
    """滑块数对但检测步骤 NG (is_good=False) → 该箱仍判 NG."""
    coord, cid = _setup_flow(client, count_unit="sliders",
                             items_per_box_source="config", items_per_box_fixed=24)
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 24})
    alarms = []
    coord.set_alarm_sink(lambda c, k, m: alarms.append(k))
    db = SessionLocal()

    coord.on_scan("ORD1", db, channel_id=0)
    coord.on_cycle_settled(0, 1, False, db, slider_count=24)    # 步骤 NG

    row = db.query(PackagingFlowRun).filter_by(order_no="ORD1").first()
    assert row.box_ng == 1 and row.final_result == "NG"
    assert "box_ng" in alarms


def test_sliders_short_box_redo_keeps_order(client):
    """sliders 漏箱: 应做3箱只做1箱就扫新工单 → 漏箱报警, redo 不切单."""
    coord, cid = _setup_flow(client, count_unit="sliders",
                             items_per_box_source="config", items_per_box_fixed=24,
                             on_short_box="redo")
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 72})    # 3 箱
    alarms = []
    coord.set_alarm_sink(lambda c, k, m: alarms.append(k))
    db = SessionLocal()

    coord.on_scan("ORD1", db, channel_id=0)
    coord.on_cycle_settled(0, 1, True, db, slider_count=24)     # 箱1满 → 开箱2
    coord.on_scan("ORD2", db, channel_id=0)                     # box_done=1<3 → 漏箱

    assert "short_box" in alarms
    state = coord.get_state(cid)
    assert state is not None and state["order_no"] == "ORD1"    # redo 不切单


def test_sliders_tail_paper_order_gate(client):
    """尾箱塞工单 gate: 没检测到放工单 → 不收尾 + missing_paper; 放了工单再来 → 收尾完成."""
    coord, cid = _setup_flow(client, count_unit="sliders",
                             items_per_box_source="config", items_per_box_fixed=24,
                             tail_paper_order_required=True)
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 24})    # 1 箱(=尾箱)
    alarms = []
    coord.set_alarm_sink(lambda c, k, m: alarms.append(k))
    paper = {"covered": False}
    coord.set_paper_order_probe(lambda ch, lbl: paper["covered"])
    db = SessionLocal()

    coord.on_scan("ORD1", db, channel_id=0)
    coord.on_cycle_settled(0, 1, True, db, slider_count=24)     # gate 未过 → 不收尾
    assert "missing_paper" in alarms
    state = coord.get_state(cid)
    assert state is not None and state["box_done"] == 0         # 没收尾

    paper["covered"] = True
    coord.on_cycle_settled(0, 2, True, db, slider_count=24)     # 放了工单 → 收尾完成
    row = db.query(PackagingFlowRun).filter_by(order_no="ORD1").first()
    assert row.status == "completed" and row.box_done == 1
    assert row.paper_order_done is True


def test_sliders_auto_switch_project_invoked(client):
    """auto_switch_project + 拿到规格 → 调 project_activator 切项目."""
    coord, cid = _setup_flow(client, count_unit="sliders",
                             items_per_box_source="config", items_per_box_fixed=24,
                             auto_switch_project=True,
                             spec_to_project={"S1": 5})
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 24, "spec": "S1"})
    coord.set_alarm_sink(lambda c, k, m: None)
    switched = []
    coord.set_project_activator(lambda spec, cfg: (switched.append(spec), True)[1])
    db = SessionLocal()

    coord.on_scan("ORD1", db, channel_id=0)
    assert switched == ["S1"]


def test_sliders_auto_switch_project_fail_continues(client):
    """切项目失败 → 报警但仍开工单 (用当前项目继续)."""
    coord, cid = _setup_flow(client, count_unit="sliders",
                             items_per_box_source="config", items_per_box_fixed=24,
                             auto_switch_project=True,
                             spec_to_project={"S1": 5})
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 24, "spec": "S1"})
    alarms = []
    coord.set_alarm_sink(lambda c, k, m: alarms.append(k))
    coord.set_project_activator(lambda spec, cfg: False)        # 切失败
    db = SessionLocal()

    coord.on_scan("ORD1", db, channel_id=0)
    assert "mes_fail" in alarms                                 # 报警
    assert coord.get_state(cid) is not None                    # 仍开了工单
