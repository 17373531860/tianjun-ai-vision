"""包装箱结算协调器 — 状态机全流程回归护栏 (v3.21 M2/M3/M6).

把"工单→箱→托盘"三层状态机的关键路径固化成永久护栏:
  正常完成 / 漏箱 / 多箱 / 托盘不达标 / 标签归一化同号 / 停止三策略 / 待机开关 / 完成回推.

用真 DB (conftest 已隔离临时库 + 建表) 验证落库, 三个外部钩子 (拉单/报警/回推) 用 mock 注入.
每个用例前清空包装表 + 复位协调器单例, 避免 module-level 单例串污染.
"""
import time

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


def test_label_insert_char_reinserts_hyphen(client):
    """上银 insert_char: 扫码枪丢 '-' 扫成 'JOB1507001141', 配置符号='-' 位置=12,
    归一化补回 → 记录的 order_no = 完整 'JOB150700114-1' (查 MES 也用这个值)."""
    coord, cid = _setup_flow(client, label_match="insert_char",
                             hyphen_template="-", hyphen_pos=12)
    queried = []
    coord.set_mes_fetcher(lambda c, o: (queried.append(o), {"dispatch_qty": 2})[1])
    alarms = []
    coord.set_alarm_sink(lambda c, k, m: alarms.append(k))
    db = SessionLocal()

    coord.on_scan("JOB1507001141", db, channel_id=0)     # 丢符号码 → norm=JOB150700114-1 开工单
    state = coord.get_state(cid)
    assert state["order_no"] == "JOB150700114-1"          # 记录的是补回的完整值
    assert queried == ["JOB150700114-1"]                  # 查 MES 用完整值


def test_label_insert_char_idempotent_same_order(client):
    """insert_char 幂等: 扫到丢符号码 'JOB1507001141' 与扫到带符号码 'JOB150700114-1'
    归一化结果相同 → 视为同号 → 第二次扫开箱而非切单/报警."""
    coord, cid = _setup_flow(client, label_match="insert_char",
                             hyphen_template="-", hyphen_pos=12)
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 2})
    alarms = []
    coord.set_alarm_sink(lambda c, k, m: alarms.append(k))
    db = SessionLocal()

    coord.on_scan("JOB1507001141", db, channel_id=0)     # 丢符号 → JOB150700114-1 开工单
    coord.on_scan("JOB150700114-1", db, channel_id=0)    # 带符号 → 同样 JOB150700114-1 → 开箱1
    state = coord.get_state(cid)
    assert state["order_no"] == "JOB150700114-1"
    assert state["current_box_index"] == 1
    assert "label_mismatch" not in alarms


def test_label_insert_char_varlen_serial(client):
    """insert_char 对序号位数不固定鲁棒: 主单号定长 12, 序号 3 位 → 补在 12 位后还原."""
    coord, cid = _setup_flow(client, label_match="insert_char",
                             hyphen_template="-", hyphen_pos=12)
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 1})
    coord.set_alarm_sink(lambda c, k, m: None)
    db = SessionLocal()

    coord.on_scan("JOB150700114111", db, channel_id=0)   # 序号 111 (3 位) → JOB150700114-111
    state = coord.get_state(cid)
    assert state["order_no"] == "JOB150700114-111"


def test_composite_prefix_same_order_sy_real_label(client):
    """复合条码取段 (v3.30.1, SY 现场真实标签):
    箱标签是四段拼接 '订单|工单|数量|校验串', 按前缀 JOB 取工单段再走 insert_char.
    工单纸(丢符号) / 复合标签(带符号) / 复合标签(丢符号) 三种码归一化后同号 → 不报标签不符."""
    coord, cid = _setup_flow(client, label_match="insert_char",
                             hyphen_template="-", hyphen_pos=12,
                             composite_label_enabled=True,
                             composite_pick_mode="prefix", composite_prefix="JOB")
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 3})
    alarms = []
    coord.set_alarm_sink(lambda c, k, m: alarms.append(k))
    db = SessionLocal()

    # 工单纸直扫 (无分隔符, 枪丢 '-') → 原样通过取段, insert_char 补回 → 开工单
    coord.on_scan("JOB26060026813", db, channel_id=0)
    state = coord.get_state(cid)
    assert state["order_no"] == "JOB260600268-13"
    # 复合箱标签 (JOB 段带符号, 现场真实串) → 取段+归一化同号 → 开箱1
    coord.on_scan("ORD260300050-2|JOB260600268-13|54.00|55AEA731126D6A9FE063D521BD0A9B02",
                  db, channel_id=0)
    state = coord.get_state(cid)
    assert state["current_box_index"] == 1
    # 复合箱标签 (JOB 段丢符号) → 仍同号 → 结算箱1+开箱2
    coord.on_scan("ORD260300050-2|JOB26060026813|54.00|55AEA731126D6A9FE063D521BD0A9B02",
                  db, channel_id=0)
    state = coord.get_state(cid)
    assert state["current_box_index"] == 2
    assert "label_mismatch" not in alarms


def test_composite_index_mode_picks_nth_segment(client):
    """复合取段 index 模式: 取第 2 段; 段序号超界原样返回不吞码."""
    coord, cid = _setup_flow(client, composite_label_enabled=True,
                             composite_pick_mode="index", composite_index=2)
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 2})
    coord.set_alarm_sink(lambda c, k, m: None)
    db = SessionLocal()

    coord.on_scan("AAA|ORD9|54.00", db, channel_id=0)   # 取第2段 ORD9 开工单
    state = coord.get_state(cid)
    assert state["order_no"] == "ORD9"
    coord.on_scan("ORD9", db, channel_id=0)             # 单段码同号 → 开箱1
    state = coord.get_state(cid)
    assert state["current_box_index"] == 1


def test_composite_disabled_keeps_old_mismatch_behavior(client):
    """默认关 = 存量零差异: 不启用取段时复合串仍整串比对 → 标签不符报警 (原行为)."""
    coord, cid = _setup_flow(client, label_match="insert_char",
                             hyphen_template="-", hyphen_pos=12)
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 3})
    alarms = []
    coord.set_alarm_sink(lambda c, k, m: alarms.append(k))
    db = SessionLocal()

    coord.on_scan("JOB26060026813", db, channel_id=0)
    coord.on_scan("ORD260300050-2|JOB26060026813|54.00|55AEA731126D6A9FE063D521BD0A9B02",
                  db, channel_id=0)
    assert "label_mismatch" in alarms


def test_composite_extract_pure_edge_cases(client):
    """取段纯函数边界: 前缀多段命中取最长 / 前缀无命中原样 / index 超界原样 / 未启用原样."""
    from backend.services.packaging_flow_coordinator import PackagingFlowCoordinator as C
    base = {"composite_label_enabled": True, "composite_delimiter": "|",
            "composite_pick_mode": "prefix", "composite_prefix": "JOB"}
    # 多段命中取最长 (最具体)
    assert C._extract_composite("JOB1|JOB260600268-13|X", base) == "JOB260600268-13"
    # 前缀无命中 → 原样返回 (不吞码, 让后续按不符报警可追查)
    assert C._extract_composite("A|B|C", base) == "A|B|C"
    # index 超界 → 原样
    idx = dict(base, composite_pick_mode="index", composite_index=9)
    assert C._extract_composite("A|B", idx) == "A|B"
    # 未启用 → 原样
    assert C._extract_composite("A|B", {"composite_label_enabled": False}) == "A|B"


def test_order_code_pattern_with_run_uses_label_mismatch(client):
    """有在途工单时: 识别规则不参与, 数量码仍走原有「标签不符」逻辑."""
    coord, cid = _setup_flow(client, label_match="insert_char",
                             hyphen_template="-", hyphen_pos=12,
                             order_code_pattern="^JOB", count_unit="sliders",
                             on_mes_fail="offline", on_label_mismatch="block")
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 3})
    alarms = []
    coord.set_alarm_sink(lambda c, k, m: alarms.append((k, m)))
    db = SessionLocal()

    coord.on_scan("JOB260600151202", db, channel_id=0)
    coord.on_scan("80.00", db, channel_id=0)

    assert any(k == "label_mismatch" for k, _ in alarms)
    assert not any(k == "order_code_reject" for k, _ in alarms)
    state = coord.get_state(cid)
    assert state["order_no"] == "JOB260600151-202"


def test_order_code_pattern_blocks_first_scan_garbage(client):
    """工单号识别规则 ^JOB: 无在途工单时第一枪扫 80.00 → 拒扫, 不开出垃圾工单."""
    coord, cid = _setup_flow(client, order_code_pattern="^JOB", on_mes_fail="offline")
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 2})
    alarms = []
    coord.set_alarm_sink(lambda c, k, m: alarms.append((k, m)))
    db = SessionLocal()

    coord.on_scan("80.00", db, channel_id=0)

    assert any(k == "order_code_reject" for k, _ in alarms)
    assert coord.get_state(cid) is None


def test_order_code_pattern_disabled_zero_diff(client):
    """默认空规则 = 不过滤 (零差异): 与未加此功能前行为一致."""
    from backend.services.packaging_flow_coordinator import PackagingFlowCoordinator as C
    cfg = {"order_code_pattern": ""}
    assert C._order_code_ok("80.00", cfg) is True
    assert C._order_code_ok("JOB260600151-202", cfg) is True


def test_completed_order_rescan_blocked(client):
    """v3.42.1 完成工单重扫拦截 (开): 单箱做完 OK 后再扫同号 → 报警且不重新开单."""
    coord, cid = _setup_flow(client, count_unit="sliders",
                             items_per_box_source="config", items_per_box_fixed=2,
                             block_completed_order_rescan=True,
                             event_completed_order_rescan=3)
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 2})
    alarms = []
    coord.set_alarm_sink(lambda c, k, m: alarms.append((k, m)))
    db = SessionLocal()

    coord.on_scan("JOB1", db, channel_id=0)              # 开工单 (1 箱, 目标 2 件)
    coord.on_cycle_settled(0, 1, True, db, slider_count=2)  # 尾箱满 2 → 工单完成 OK
    row = db.query(PackagingFlowRun).filter_by(order_no="JOB1").first()
    assert row.status == "completed" and row.final_result == "OK"

    coord.on_scan("JOB1", db, channel_id=0)              # 再扫已完成 OK 的同号
    assert any(k == "completed_order_rescan" for k, _ in alarms)
    assert coord.get_state(cid) is None                  # 未重新开单
    runs = db.query(PackagingFlowRun).filter_by(order_no="JOB1").count()
    assert runs == 1                                     # 没有第二条运行记录


def test_completed_order_rescan_default_off_reopens(client):
    """默认关 = 老行为零差异: 完成 OK 后再扫同号照样重新开单."""
    coord, cid = _setup_flow(client, count_unit="sliders",
                             items_per_box_source="config", items_per_box_fixed=2)
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 2})
    alarms = []
    coord.set_alarm_sink(lambda c, k, m: alarms.append((k, m)))
    db = SessionLocal()

    coord.on_scan("JOB1", db, channel_id=0)
    coord.on_cycle_settled(0, 1, True, db, slider_count=2)
    coord.on_scan("JOB1", db, channel_id=0)              # 重扫 → 重新开单 (老行为)

    assert not any(k == "completed_order_rescan" for k, _ in alarms)
    state = coord.get_state(cid)
    assert state is not None and state["order_no"] == "JOB1"


def test_completed_order_rescan_ng_allowed(client):
    """完成但 NG 的单不拦: 允许重扫补做 (拦截只针对最近一次已完成且 OK)."""
    coord, cid = _setup_flow(client, count_unit="sliders",
                             items_per_box_source="config", items_per_box_fixed=2,
                             block_completed_order_rescan=True)
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 2})
    alarms = []
    coord.set_alarm_sink(lambda c, k, m: alarms.append((k, m)))
    db = SessionLocal()

    coord.on_scan("JOB1", db, channel_id=0)
    coord.on_cycle_settled(0, 1, False, db, slider_count=1)  # 尾箱 NG → 工单完成 NG
    row = (db.query(PackagingFlowRun).filter_by(order_no="JOB1")
           .order_by(PackagingFlowRun.id.desc()).first())
    assert row.status == "completed" and row.final_result == "NG"

    coord.on_scan("JOB1", db, channel_id=0)              # NG 单重扫 → 放行重开
    assert not any(k == "completed_order_rescan" for k, _ in alarms)
    state = coord.get_state(cid)
    assert state is not None and state["order_no"] == "JOB1"


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


def test_sliders_paper_gate_default_old_behavior_no_snapshot(client):
    """收尾动作模式默认关 = 老行为零差异: 拦下时不挂快照, 扫新单走漏箱处置(redo 不切单)."""
    coord, cid = _setup_flow(client, count_unit="sliders",
                             items_per_box_source="config", items_per_box_fixed=24,
                             tail_paper_order_required=True,
                             on_short_box="redo")
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 48})    # 2 箱
    alarms = []
    coord.set_alarm_sink(lambda c, k, m: alarms.append(k))
    coord.set_paper_order_probe(lambda ch, lbl: False)
    db = SessionLocal()

    coord.on_scan("ORD1", db, channel_id=0)
    coord.on_cycle_settled(0, 1, True, db, slider_count=24)     # 箱1 正常收
    coord.on_cycle_settled(0, 2, True, db, slider_count=24)     # 尾箱拦下: 只报警, 不挂等待态
    state = coord.get_state(cid)
    assert state is not None and state.get("awaiting_paper") is None
    coord.on_scan("ORD2", db, channel_id=0)                     # 老行为: 漏箱处置

    assert "short_box" in alarms
    state = coord.get_state(cid)
    assert state is not None and state["order_no"] == "ORD1"    # redo 不切单, 不判NG收尾
    row = db.query(PackagingFlowRun).filter_by(order_no="ORD1").first()
    assert row.status != "completed"


def test_sliders_awaiting_paper_box_settles_immediately_no_alarm(client):
    """v3.43 箱归周期/放工单归工单: 尾箱装满没放工单 → 箱当场落账 OK 且**不报警**,
    工单挂「等放工单收尾」; 之后放工单周期 (0 滑块) 只触发工单收尾, 不改箱成绩."""
    coord, cid = _setup_flow(client, count_unit="sliders",
                             items_per_box_source="config", items_per_box_fixed=24,
                             tail_paper_order_required=True,
                             tail_paper_as_close_action=True)
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 24})
    alarms = []
    coord.set_alarm_sink(lambda c, k, m: alarms.append(k))
    paper = {"covered": False}
    coord.set_paper_order_probe(lambda ch, lbl: paper["covered"])
    db = SessionLocal()

    coord.on_scan("ORD1", db, channel_id=0)
    coord.on_cycle_settled(0, 1, True, db, slider_count=24)     # 满箱但没放工单
    state = coord.get_state(cid)
    assert state is not None and state["status"] == "awaiting_paper"
    assert state["box_done"] == 1                               # 箱已当场落账
    assert "missing_paper" not in alarms                        # 正常流程不误报 NG

    paper["covered"] = True
    coord.on_cycle_settled(0, 2, True, db, slider_count=0)      # 放工单周期本身 0 滑块

    row = db.query(PackagingFlowRun).filter_by(order_no="ORD1").first()
    assert row.status == "completed" and row.final_result == "OK"
    assert row.box_details[-1]["sliders"] == 24                 # 箱成绩用落账那次, 不用 0
    assert row.paper_order_done is True
    assert "missing_paper" not in alarms                        # 全程零误报


def test_sliders_awaiting_paper_step_detected_closes_order(client):
    """v3.43 放工单动作出现即时通知 → 工单立即收尾, 不必等下一周期/扫码."""
    coord, cid = _setup_flow(client, count_unit="sliders",
                             items_per_box_source="config", items_per_box_fixed=24,
                             tail_paper_order_required=True,
                             tail_paper_as_close_action=True,
                             tail_paper_step_label="放工单")
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 24})
    coord.set_alarm_sink(lambda c, k, m: None)
    coord.set_paper_order_probe(lambda ch, lbl: False)
    db = SessionLocal()

    coord.on_scan("ORD1", db, channel_id=0)
    coord.on_cycle_settled(0, 1, True, db, slider_count=24)     # 挂等放工单收尾
    coord.on_step_detected(0, "放工单")                          # 动作出现

    row = db.query(PackagingFlowRun).filter_by(order_no="ORD1").first()
    assert row.status == "completed" and row.final_result == "OK"
    assert coord.get_state(cid) is None


def test_sliders_early_paper_on_non_tail_box_alarms_once(client):
    """v3.43.1 提前放工单: 3 箱工单在第 1 箱(非尾箱)就检测到放工单动作 → 当场报警
    (每箱只报一次), 箱结算不受影响; 做到尾箱后同一动作不再算提前."""
    coord, cid = _setup_flow(client, count_unit="sliders",
                             items_per_box_source="config", items_per_box_fixed=24,
                             tail_paper_order_required=True,
                             tail_paper_as_close_action=True,
                             tail_paper_step_label="放工单")
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 72})    # 3 箱
    alarms = []
    coord.set_alarm_sink(lambda c, k, m: alarms.append(k))
    paper = {"covered": False}
    coord.set_paper_order_probe(lambda ch, lbl: paper["covered"])
    db = SessionLocal()

    coord.on_scan("ORD1", db, channel_id=0)
    coord.on_cycle_settled(0, 1, True, db, slider_count=24)     # 第 1 箱落账, 开第 2 箱
    coord.on_step_detected(0, "放工单")                          # 第 2 箱期间提前放工单
    assert alarms.count("early_paper") == 1
    coord.on_step_detected(0, "放工单")                          # 同箱重复出现不轰炸
    assert alarms.count("early_paper") == 1

    state = coord.get_state(cid)
    assert state["status"] == "running"                         # 不拦结算不改状态
    assert state["box_done"] == 1

    coord.on_cycle_settled(0, 2, True, db, slider_count=24)     # 第 2 箱落账, 进尾箱
    coord.on_step_detected(0, "放工单")                          # 尾箱期间 = 正常动作
    assert alarms.count("early_paper") == 1                     # 不再算提前

    paper["covered"] = True
    coord.on_cycle_settled(0, 3, True, db, slider_count=24)     # 尾箱落账+放工单到位
    row = db.query(PackagingFlowRun).filter_by(order_no="ORD1").first()
    assert row.status == "completed" and row.final_result == "OK"
    assert "missing_paper" not in alarms


def test_sliders_early_paper_on_empty_tail_box_alarms(client):
    """v3.43.1 提前放工单·空尾箱: 上一箱落账后"当前箱"立即翻到尾箱, 但尾箱一件没装
    (实时进箱数=0) 时放工单 → 仍算提前, 报警; 尾箱装上件后 (进箱数>0) 同一动作不报;
    拿不到进箱数 (钩子缺/返回 None) 时保守放行不误报."""
    coord, cid = _setup_flow(client, count_unit="sliders",
                             items_per_box_source="config", items_per_box_fixed=24,
                             tail_paper_order_required=True,
                             tail_paper_as_close_action=True,
                             tail_paper_step_label="放工单")
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 48})    # 2 箱
    alarms = []
    coord.set_alarm_sink(lambda c, k, m: alarms.append(k))
    coord.set_paper_order_probe(lambda ch, lbl: False)
    progress = {"n": None}
    coord.set_box_progress_getter(lambda ch: progress["n"])
    db = SessionLocal()

    coord.on_scan("ORD1", db, channel_id=0)
    coord.on_cycle_settled(0, 1, True, db, slider_count=24)     # 第 1 箱落账 → 翻到尾箱

    coord.on_step_detected(0, "放工单")                          # 进箱数未知 → 保守放行
    assert alarms.count("early_paper") == 0

    progress["n"] = 8                                           # 尾箱已在装 → 正常动作
    coord.on_step_detected(0, "放工单")
    assert alarms.count("early_paper") == 0

    progress["n"] = 0
    coord.on_step_detected(0, "放工单")                          # 尾箱空箱 → 提前, 报警
    assert alarms.count("early_paper") == 1
    coord.on_step_detected(0, "放工单")                          # 同箱去重
    assert alarms.count("early_paper") == 1

    state = coord.get_state(cid)
    assert state["status"] == "running" and state["box_done"] == 1


def test_sliders_awaiting_paper_timeout_mode_judges_ng(client):
    """v3.43 时限判定 (与扫新单二选一): 到点没放工单 → 报警 + 工单终局判 NG 收尾."""
    coord, cid = _setup_flow(client, count_unit="sliders",
                             items_per_box_source="config", items_per_box_fixed=24,
                             tail_paper_order_required=True,
                             tail_paper_as_close_action=True,
                             tail_paper_scan_alarm=False,       # 时限模式
                             tail_paper_timeout_s=1)
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 24})
    alarms = []
    coord.set_alarm_sink(lambda c, k, m: alarms.append(k))
    coord.set_paper_order_probe(lambda ch, lbl: False)
    db = SessionLocal()

    coord.on_scan("ORD1", db, channel_id=0)
    coord.on_cycle_settled(0, 1, True, db, slider_count=24)     # 挂等待, 布防 1s Timer
    assert alarms.count("missing_paper") == 0
    time.sleep(1.4)                                             # Timer 到点 = 终局判定
    assert alarms.count("missing_paper") == 1
    row = db.query(PackagingFlowRun).filter_by(order_no="ORD1").first()
    assert row.status == "completed" and row.final_result == "NG"
    assert row.box_ng == 0                                      # 箱成绩不回改
    assert coord.get_state(cid) is None                         # 在途已移除


def test_sliders_awaiting_paper_timeout_mode_paper_within_limit_ok(client):
    """v3.43 时限判定: 时限内等到放工单 → 工单 OK 收尾, Timer 撤防不再误报."""
    coord, cid = _setup_flow(client, count_unit="sliders",
                             items_per_box_source="config", items_per_box_fixed=24,
                             tail_paper_order_required=True,
                             tail_paper_as_close_action=True,
                             tail_paper_scan_alarm=False,
                             tail_paper_timeout_s=2,
                             tail_paper_step_label="放工单")
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 24})
    alarms = []
    coord.set_alarm_sink(lambda c, k, m: alarms.append(k))
    coord.set_paper_order_probe(lambda ch, lbl: False)
    db = SessionLocal()

    coord.on_scan("ORD1", db, channel_id=0)
    coord.on_cycle_settled(0, 1, True, db, slider_count=24)     # 挂等待
    coord.on_step_detected(0, "放工单")                          # 时限内放了
    row = db.query(PackagingFlowRun).filter_by(order_no="ORD1").first()
    assert row.status == "completed" and row.final_result == "OK"
    time.sleep(2.4)                                             # 原时限过点
    assert alarms.count("missing_paper") == 0                   # Timer 已撤防, 零误报


def test_sliders_awaiting_paper_timeout_mode_scan_rejected(client):
    """v3.43 时限判定下扫新单不参与判定: 时限内扫新单被拒收 (旧单继续等, 新单不开),
    到点后时限终局判 NG, 再扫新单正常开."""
    coord, cid = _setup_flow(client, count_unit="sliders",
                             items_per_box_source="config", items_per_box_fixed=24,
                             tail_paper_order_required=True,
                             tail_paper_as_close_action=True,
                             tail_paper_scan_alarm=False,
                             tail_paper_timeout_s=1)
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 24})
    alarms = []
    coord.set_alarm_sink(lambda c, k, m: alarms.append(k))
    coord.set_paper_order_probe(lambda ch, lbl: False)
    db = SessionLocal()

    coord.on_scan("ORD1", db, channel_id=0)
    coord.on_cycle_settled(0, 1, True, db, slider_count=24)     # 挂等待 (1s 时限)
    coord.on_scan("ORD2", db, channel_id=0)                     # 时限内扫新单 → 拒收
    state = coord.get_state(cid)
    assert state is not None and state["order_no"] == "ORD1"    # 旧单仍在途
    assert state["status"] == "awaiting_paper"
    assert db.query(PackagingFlowRun).filter_by(order_no="ORD2").count() == 0  # 新单没开

    time.sleep(1.4)                                             # 时限到点 → 终局 NG
    row = db.query(PackagingFlowRun).filter_by(order_no="ORD1").first()
    assert row.status == "completed" and row.final_result == "NG"
    coord.on_scan("ORD2", db, channel_id=0)                     # 判定完再扫 → 正常开
    state = coord.get_state(cid)
    assert state is not None and state["order_no"] == "ORD2"


def test_sliders_awaiting_paper_scan_new_order_judges_ng(client):
    """v3.43 一直没放工单直接扫新工单 → 报警 + 旧单**工单层**判 NG 收尾 (箱成绩不回改)
    + 新单正常开. 报警只在扫新单这一个点 (挂等待时不报)."""
    coord, cid = _setup_flow(client, count_unit="sliders",
                             items_per_box_source="config", items_per_box_fixed=24,
                             tail_paper_order_required=True,
                             tail_paper_as_close_action=True)
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 24})
    alarms = []
    coord.set_alarm_sink(lambda c, k, m: alarms.append(k))
    coord.set_paper_order_probe(lambda ch, lbl: False)          # 始终没放
    db = SessionLocal()

    coord.on_scan("ORD1", db, channel_id=0)
    coord.on_cycle_settled(0, 1, True, db, slider_count=24)     # 挂等放工单收尾 (不报警)
    assert alarms.count("missing_paper") == 0
    coord.on_scan("ORD2", db, channel_id=0)                     # 没放就扫新单

    row = db.query(PackagingFlowRun).filter_by(order_no="ORD1").first()
    assert row.status == "completed" and row.final_result == "NG"
    assert row.box_done == 1 and row.box_ng == 0                # 箱本身 OK, NG 在工单层
    assert row.box_details[-1]["result"] == "OK"
    assert alarms.count("missing_paper") == 1                   # 只在扫新单时报
    state = coord.get_state(cid)
    assert state is not None and state["order_no"] == "ORD2"    # 新单已开


def test_sliders_awaiting_paper_scan_judge_alarm_fires_after_project_switch(client):
    """v3.43.1 扫新单判定的缺工单报警必须在开新单/切项目**之后**触发: 换项目会重载模型
    并重置检测运行时, 报警若先于切换触发, 立起的人工确认定格会被切换抹掉 (确认框闪退)."""
    coord, cid = _setup_flow(client, count_unit="sliders",
                             items_per_box_source="config", items_per_box_fixed=24,
                             tail_paper_order_required=True,
                             tail_paper_as_close_action=True,
                             auto_switch_project=True)
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 24, "spec": f"SPEC-{o}"})
    events = []                                                  # 统一时序流水
    coord.set_alarm_sink(lambda c, k, m: events.append(("alarm", k)))
    coord.set_project_activator(lambda spec, cfg: events.append(("switch", spec)) or True)
    coord.set_paper_order_probe(lambda ch, lbl: False)           # 始终没放
    db = SessionLocal()

    coord.on_scan("ORD1", db, channel_id=0)
    coord.on_cycle_settled(0, 1, True, db, slider_count=24)      # 挂等放工单收尾
    coord.on_scan("ORD2", db, channel_id=0)                      # 没放就扫新单 (带切项目)

    alarm_idx = events.index(("alarm", "missing_paper"))
    switch_idx = events.index(("switch", "SPEC-ORD2"))
    assert switch_idx < alarm_idx, f"缺工单报警必须在切项目之后: {events}"
    row = db.query(PackagingFlowRun).filter_by(order_no="ORD1").first()
    assert row.status == "completed" and row.final_result == "NG"
    state = coord.get_state(cid)
    assert state is not None and state["order_no"] == "ORD2"     # 新单已开


def test_sliders_awaiting_paper_invalid_combo_falls_back_to_scan_mode(client):
    """v3.43 兜底归一: 两个字段组合非法 (扫新单关 + 时限 0) 时按默认扫新单模式判定."""
    coord, cid = _setup_flow(client, count_unit="sliders",
                             items_per_box_source="config", items_per_box_fixed=24,
                             tail_paper_order_required=True,
                             tail_paper_as_close_action=True,
                             tail_paper_scan_alarm=False,
                             tail_paper_timeout_s=0)            # 非法组合
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 24})
    alarms = []
    coord.set_alarm_sink(lambda c, k, m: alarms.append(k))
    coord.set_paper_order_probe(lambda ch, lbl: False)
    db = SessionLocal()

    coord.on_scan("ORD1", db, channel_id=0)
    coord.on_cycle_settled(0, 1, True, db, slider_count=24)
    coord.on_scan("ORD2", db, channel_id=0)                     # 兜底走扫新单判定

    row = db.query(PackagingFlowRun).filter_by(order_no="ORD1").first()
    assert row.status == "completed" and row.final_result == "NG"
    assert alarms.count("missing_paper") == 1
    state = coord.get_state(cid)
    assert state is not None and state["order_no"] == "ORD2"


def test_sliders_awaiting_paper_scan_new_order_after_paper_ok(client):
    """v3.43 等待中现场放了工单(实时可见)再扫新单 → 旧单 OK 收尾 + 新单开."""
    coord, cid = _setup_flow(client, count_unit="sliders",
                             items_per_box_source="config", items_per_box_fixed=24,
                             tail_paper_order_required=True,
                             tail_paper_as_close_action=True)
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 24})
    coord.set_alarm_sink(lambda c, k, m: None)
    paper = {"covered": False}
    coord.set_paper_order_probe(lambda ch, lbl: paper["covered"])
    db = SessionLocal()

    coord.on_scan("ORD1", db, channel_id=0)
    coord.on_cycle_settled(0, 1, True, db, slider_count=24)     # 挂等放工单收尾
    paper["covered"] = True                                     # 现场放了工单
    coord.on_scan("ORD2", db, channel_id=0)

    row = db.query(PackagingFlowRun).filter_by(order_no="ORD1").first()
    assert row.status == "completed" and row.final_result == "OK"
    state = coord.get_state(cid)
    assert state is not None and state["order_no"] == "ORD2"


def test_sliders_awaiting_paper_same_order_rescan_waits(client):
    """v3.43 等待中同号重扫且仍没放 → 只提醒继续等, 工单保持在途 (箱已落账)."""
    coord, cid = _setup_flow(client, count_unit="sliders",
                             items_per_box_source="config", items_per_box_fixed=24,
                             tail_paper_order_required=True,
                             tail_paper_as_close_action=True)
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 24})
    alarms = []
    coord.set_alarm_sink(lambda c, k, m: alarms.append(k))
    coord.set_paper_order_probe(lambda ch, lbl: False)
    db = SessionLocal()

    coord.on_scan("ORD1", db, channel_id=0)
    coord.on_cycle_settled(0, 1, True, db, slider_count=24)     # 挂等放工单收尾
    coord.on_scan("ORD1", db, channel_id=0)                     # 同号重扫

    state = coord.get_state(cid)
    assert state is not None and state["order_no"] == "ORD1"    # 仍在途
    assert state["status"] == "awaiting_paper"                  # 工单没收尾
    assert state["box_done"] == 1                               # 箱已当场落账
    assert alarms.count("missing_paper") == 1                   # 重扫提醒这一次


def test_sliders_awaiting_paper_forced_settle_waives_gate(client):
    """v3.43 等待中管理员强制结案 → 豁免放工单, 工单按各箱成绩收尾."""
    coord, cid = _setup_flow(client, count_unit="sliders",
                             items_per_box_source="config", items_per_box_fixed=24,
                             tail_paper_order_required=True,
                             tail_paper_as_close_action=True)
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 24})
    coord.set_alarm_sink(lambda c, k, m: None)
    coord.set_paper_order_probe(lambda ch, lbl: False)
    db = SessionLocal()

    coord.on_scan("ORD1", db, channel_id=0)
    coord.on_cycle_settled(0, 1, True, db, slider_count=24)     # 挂等放工单收尾
    assert coord.force_settle_manual(cid, db, reason="现场确认已放工单", operator="主管A")

    row = db.query(PackagingFlowRun).filter_by(order_no="ORD1").first()
    assert row.status == "completed" and row.final_result == "OK"
    assert row.box_done == 1
    assert coord.get_state(cid) is None


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


# =============================================================
# 强制结案 (管理员/主管手动收尾) + 待机不结算
# =============================================================

def test_force_settle_manual_records_reason(client):
    """强制结案: 收尾未满箱 + 完成工单, 理由/授权人落库审计."""
    coord, cid = _setup_flow(client)
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 2})
    db = SessionLocal()

    coord.on_scan("ORD1", db, channel_id=0)      # 开工单 box_total=2
    coord.on_scan("ORD1", db, channel_id=0)      # 开箱1
    coord.on_cycle_settled(0, 1, True, db)       # 箱1 仅 1/2 托盘 (没满)

    ok = coord.force_settle_manual(cid, db, reason="产线临时停线收工", operator="supervisor_li")
    assert ok is True
    assert coord.get_state(cid) is None          # 工单已结案弹出内存

    row = db.query(PackagingFlowRun).filter_by(order_no="ORD1").first()
    assert row.status == "completed"
    assert row.forced_reason == "产线临时停线收工"
    assert row.forced_by == "supervisor_li"


def test_force_settle_manual_overrides_keep(client):
    """配置 on_forced_stop=keep 时, 自动收尾不动工单; 但手动强制结案仍强制收尾."""
    coord, cid = _setup_flow(client, on_forced_stop="keep")
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 2})
    db = SessionLocal()
    coord.on_scan("ORD1", db, channel_id=0)
    coord.on_scan("ORD1", db, channel_id=0)

    # 自动收尾 (keep) → 不动
    assert coord.on_forced_settle(cid, db) is False
    assert coord.get_state(cid) is not None

    # 手动强制结案 → 忽略 keep, 仍收尾
    assert coord.force_settle_manual(cid, db, reason="卡单强收", operator="admin") is True
    assert coord.get_state(cid) is None
    row = db.query(PackagingFlowRun).filter_by(order_no="ORD1").first()
    assert row.status == "completed" and row.forced_reason == "卡单强收"


def test_force_settle_no_active_order_returns_false(client):
    """无进行中工单时强制结案返回 False (API 会转 409)."""
    coord, cid = _setup_flow(client)
    db = SessionLocal()
    assert coord.force_settle_manual(cid, db, reason="x", operator="admin") is False


def test_standby_no_settle_when_disabled(client):
    """待机也收尾=关: 待机不收尾 (工单保留); 停止才收尾."""
    coord, cid = _setup_flow(client, forced_settle_on_standby=False)
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 2})
    db = SessionLocal()
    coord.on_scan("ORD1", db, channel_id=0)
    coord.on_scan("ORD1", db, channel_id=0)      # 开箱1

    # 待机 → 不结算, 工单仍在
    coord.on_forced_settle_by_channel(0, db, is_standby=True)
    assert coord.get_state(cid) is not None

    # 停止 (非待机) → 按策略收尾
    coord.on_forced_settle_by_channel(0, db, is_standby=False)
    assert coord.get_state(cid) is None


def test_standby_settles_when_enabled(client):
    """待机也收尾=开 (默认): 待机即收尾工单."""
    coord, cid = _setup_flow(client, forced_settle_on_standby=True)
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 2})
    db = SessionLocal()
    coord.on_scan("ORD1", db, channel_id=0)
    coord.on_scan("ORD1", db, channel_id=0)

    coord.on_forced_settle_by_channel(0, db, is_standby=True)
    assert coord.get_state(cid) is None          # 待机已收尾


# =============================================================
# v3.23 NG 补做: 少装挂起 + 补滑块 / 重做
# =============================================================

_REM_ON = {"enabled": True, "allow_step": True, "allow_count": True}


def test_short_sliders_holds_when_remediation_on(client):
    """开了补数量策略: 少装(步骤齐) → 挂起 pending_remediation, 不立即落账 NG."""
    coord, cid = _setup_flow(client, count_unit="sliders",
                             items_per_box_source="config", items_per_box_fixed=24)
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 24})   # 1 箱, 目标 24
    db = SessionLocal()
    coord.on_scan("ORD1", db, channel_id=0)
    coord.on_cycle_settled(0, 1, True, db, slider_count=20, remediation=_REM_ON)

    st = coord.get_state(cid)
    assert st is not None                                       # 工单未收尾
    assert st["status"] == "pending_remediation"
    assert st["box_done"] == 0 and st["box_ng"] == 0           # 未落账
    assert st["pending_box"]["sliders"] == 20 and st["pending_box"]["target"] == 24


def test_short_sliders_legacy_ng_when_remediation_off(client):
    """没开策略(默认): 少装 → 原行为立即判 NG, 零差异."""
    coord, cid = _setup_flow(client, count_unit="sliders",
                             items_per_box_source="config", items_per_box_fixed=24)
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 24})
    db = SessionLocal()
    coord.on_scan("ORD1", db, channel_id=0)
    coord.on_cycle_settled(0, 1, True, db, slider_count=20)    # 不带 remediation

    row = db.query(PackagingFlowRun).filter_by(order_no="ORD1").first()
    assert row.status == "completed" and row.box_ng == 1 and row.final_result == "NG"


def test_supplement_auto_completes_to_target(client):
    """补滑块(自动): 补齐到目标 → 本箱 OK 落账, 工单 completed/OK."""
    coord, cid = _setup_flow(client, count_unit="sliders",
                             items_per_box_source="config", items_per_box_fixed=24)
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 24})
    db = SessionLocal()
    coord.on_scan("ORD1", db, channel_id=0)
    coord.on_cycle_settled(0, 1, True, db, slider_count=20, remediation=_REM_ON)

    ok = coord.supplement_sliders(cid, db, target_count=None, operator="admin")
    assert ok is True
    row = db.query(PackagingFlowRun).filter_by(order_no="ORD1").first()
    assert row.status == "completed" and row.box_done == 1
    assert row.box_ng == 0 and row.final_result == "OK"


def test_supplement_manual_count_below_target_still_ng(client):
    """补滑块(手动指定仍不足目标): 落账但本箱 NG (诚实记录实际数)."""
    coord, cid = _setup_flow(client, count_unit="sliders",
                             items_per_box_source="config", items_per_box_fixed=24)
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 24})
    db = SessionLocal()
    coord.on_scan("ORD1", db, channel_id=0)
    coord.on_cycle_settled(0, 1, True, db, slider_count=20, remediation=_REM_ON)

    ok = coord.supplement_sliders(cid, db, target_count=22, operator="op1")
    assert ok is True
    row = db.query(PackagingFlowRun).filter_by(order_no="ORD1").first()
    assert row.status == "completed" and row.box_ng == 1 and row.final_result == "NG"


def test_supplement_no_pending_returns_false(client):
    """无挂起箱时补滑块 → False (接口层据此回 409)."""
    coord, cid = _setup_flow(client, count_unit="sliders",
                             items_per_box_source="config", items_per_box_fixed=24)
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 24})
    db = SessionLocal()
    coord.on_scan("ORD1", db, channel_id=0)
    assert coord.supplement_sliders(cid, db) is False


def test_redo_pending_back_to_running(client):
    """重做挂起箱: 转回 running 等下一周期, 同一箱号重测后可补做成 OK."""
    coord, cid = _setup_flow(client, count_unit="sliders",
                             items_per_box_source="config", items_per_box_fixed=24)
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 24})
    db = SessionLocal()
    coord.on_scan("ORD1", db, channel_id=0)
    coord.on_cycle_settled(0, 1, True, db, slider_count=20, remediation=_REM_ON)

    assert coord.redo_pending(cid, db, operator="admin") is True
    st = coord.get_state(cid)
    assert st["status"] == "running" and st["pending_box"] is None
    assert st["box_done"] == 0 and st["current_box_index"] == 1   # 同一箱等重测

    # 重测这次刚好 24 → 直接 OK 完成
    coord.on_cycle_settled(0, 2, True, db, slider_count=24, remediation=_REM_ON)
    row = db.query(PackagingFlowRun).filter_by(order_no="ORD1").first()
    assert row.status == "completed" and row.final_result == "OK" and row.box_ng == 0


def test_pending_ignores_new_cycle(client):
    """挂起期间又来检测周期 → 忽略, 不覆盖挂起态 (由人工补做/重做推进)."""
    coord, cid = _setup_flow(client, count_unit="sliders",
                             items_per_box_source="config", items_per_box_fixed=24)
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 24})
    db = SessionLocal()
    coord.on_scan("ORD1", db, channel_id=0)
    coord.on_cycle_settled(0, 1, True, db, slider_count=20, remediation=_REM_ON)
    coord.on_cycle_settled(0, 2, True, db, slider_count=24, remediation=_REM_ON)  # 应被忽略

    st = coord.get_state(cid)
    assert st["status"] == "pending_remediation"
    assert st["pending_box"]["sliders"] == 20                  # 仍是第一次的快照


# ============================================================
# v3.45 组⑧ 箱标签扫码授权 + 标签取本箱数量 (逐箱可变数量)
# ============================================================

_LABEL = "ORD260300050-2|JOB260600040-67|{qty}.00|A1B2C3"     # 现场箱标签二维码复合串
_ORDER = "JOB260600040-67"                                    # 工单条形码 (裸码)


def _setup_label_flow(client, **over):
    """启用箱标签扫码授权 + 取量的 sliders 口径配置 (上银 SY 现场形态)."""
    base = dict(count_unit="sliders",
                items_per_box_source="config", items_per_box_fixed=24,
                composite_label_enabled=True, composite_delimiter="|",
                composite_pick_mode="prefix", composite_prefix="JOB",
                box_label_scan_required=True, label_qty_enabled=True,
                label_qty_segment=3)
    base.update(over)
    return _setup_flow(client, **base)


def test_label_scan_gate_waits_then_qty_authorizes(client):
    """扫工单开箱后进「等扫箱标签」态; 扫到带数量的标签二维码 → 授权开做,
    本箱目标 = 标签数量 (覆盖工单级计划), 检测层反向钩子同步覆写."""
    coord, cid = _setup_label_flow(client)
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 91})   # 4 箱, 尾箱 19
    coord.set_alarm_sink(lambda c, k, m: None)
    targets = []
    coord.set_box_target_setter(lambda ch, t: targets.append(t))
    db = SessionLocal()

    coord.on_scan(_ORDER, db, channel_id=0)                    # 开工单+开箱1
    st = coord.get_state(cid)
    assert st["status"] == "waiting_label"
    assert st["current_box_index"] == 1
    assert targets == [24]                                     # 等扫期间先按计划兜底下发

    coord.on_scan(_LABEL.format(qty=19), db, channel_id=0)     # 本箱标签: 19 只
    st = coord.get_state(cid)
    assert st["status"] == "running"
    assert st["current_box_scan_qty"] == 19
    assert targets == [24, 19]                                 # 标签数量覆写目标

    coord.on_cycle_settled(0, 1, True, db, slider_count=19)    # 按标签目标 19 判 OK
    st = coord.get_state(cid)
    assert st["box_done"] == 1 and st["box_ng"] == 0
    assert st["box_details"][0]["result"] == "OK"
    assert st["box_details"][0]["target"] == 19
    assert st["box_details"][0]["scan_qty"] == 19              # 声明量留痕
    assert st["status"] == "waiting_label"                     # 下一箱重新等扫
    assert st["current_box_scan_qty"] == 0


def test_label_scan_bare_code_alarms_rescan_qr(client):
    """等扫标签时扫到裸条形码 (取不出数量) → 报警"请扫二维码", 继续等, 不放行."""
    coord, cid = _setup_label_flow(client)
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 48})
    alarms = []
    coord.set_alarm_sink(lambda c, k, m: alarms.append(k))
    db = SessionLocal()

    coord.on_scan(_ORDER, db, channel_id=0)                    # 开工单+开箱1(等扫)
    coord.on_scan(_ORDER, db, channel_id=0)                    # 又扫了裸工单条形码
    assert "label_qty_missing" in alarms
    assert coord.get_state(cid)["status"] == "waiting_label"   # 仍在等

    # 数量段非法 (非数字) 同样拒绝
    coord.on_scan("ORD1|JOB260600040-67|abc|X", db, channel_id=0)
    assert alarms.count("label_qty_missing") == 2
    assert coord.get_state(cid)["status"] == "waiting_label"


def test_label_scan_work_start_alarm_once_per_box(client):
    """未扫标签就开始作业 (周期开始通知) → 当场报警, 每箱只报一次."""
    coord, cid = _setup_label_flow(client)
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 48})
    alarms = []
    coord.set_alarm_sink(lambda c, k, m: alarms.append(k))
    db = SessionLocal()

    coord.on_scan(_ORDER, db, channel_id=0)
    coord.on_cycle_started(0)                                  # 没扫标签就开做
    coord.on_cycle_started(0)                                  # 同箱再通知不重复报
    assert alarms.count("box_not_scanned") == 1

    coord.on_scan(_LABEL.format(qty=24), db, channel_id=0)     # 授权后
    coord.on_cycle_started(0)                                  # 正常开做不报
    assert alarms.count("box_not_scanned") == 1


def test_unauthorized_box_settle_holds_then_redo_back_to_waiting(client):
    """未扫标签把整箱做完 (hold 档默认): 箱账挂起等人工; 重做 → 回到等扫态,
    扫标签授权后重测按标签目标正常落账."""
    coord, cid = _setup_label_flow(client)
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 48})
    alarms = []
    coord.set_alarm_sink(lambda c, k, m: alarms.append(k))
    db = SessionLocal()

    coord.on_scan(_ORDER, db, channel_id=0)
    coord.on_cycle_settled(0, 1, True, db, slider_count=24)    # 未扫标签做完一整箱
    st = coord.get_state(cid)
    assert st["status"] == "pending_remediation"
    assert st["pending_box"]["reason"] == "no_label"
    assert "box_not_scanned" in alarms
    assert st["box_done"] == 0                                 # 未落账

    assert coord.redo_pending(cid, db, operator="测试员")
    assert coord.get_state(cid)["status"] == "waiting_label"   # 重做后必须先扫标签

    coord.on_scan(_LABEL.format(qty=24), db, channel_id=0)
    coord.on_cycle_settled(0, 2, True, db, slider_count=24)
    st = coord.get_state(cid)
    assert st["box_done"] == 1 and st["box_ng"] == 0


def test_unauthorized_box_settle_book_mode(client):
    """book 档: 未扫标签做完的箱报警留痕后按计划目标照常落账 (不拦产线)."""
    coord, cid = _setup_label_flow(client, unauthorized_cycle_action="book")
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 48})
    alarms = []
    coord.set_alarm_sink(lambda c, k, m: alarms.append(k))
    db = SessionLocal()

    coord.on_scan(_ORDER, db, channel_id=0)
    coord.on_cycle_settled(0, 1, True, db, slider_count=24)
    st = coord.get_state(cid)
    assert "box_not_scanned" in alarms
    assert st["box_done"] == 1 and st["box_ng"] == 0           # 按计划目标 24 落账 OK
    assert st["box_details"][0]["target"] == 24
    assert st["status"] == "waiting_label"                     # 下一箱仍要求扫


def test_label_qty_pattern_overrides_segment(client):
    """数量段正则优先于固定段号: 段序不固定的现场只改配置不改代码."""
    coord, cid = _setup_label_flow(client, label_qty_segment=2,   # 故意配错段号
                                   label_qty_pattern=r"\d+\.\d+")
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 48})
    coord.set_alarm_sink(lambda c, k, m: None)
    db = SessionLocal()

    coord.on_scan(_ORDER, db, channel_id=0)
    coord.on_scan(_LABEL.format(qty=23), db, channel_id=0)     # 正则命中 "23.00" 段
    st = coord.get_state(cid)
    assert st["status"] == "running" and st["current_box_scan_qty"] == 23


def test_label_voucher_mode_authorizes_without_qty(client):
    """纯凭证模式 (不取量): 扫到本工单标签即放行, 目标仍按工单级计划."""
    coord, cid = _setup_label_flow(client, label_qty_enabled=False)
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 48})
    coord.set_alarm_sink(lambda c, k, m: None)
    targets = []
    coord.set_box_target_setter(lambda ch, t: targets.append(t))
    db = SessionLocal()

    coord.on_scan(_ORDER, db, channel_id=0)
    assert coord.get_state(cid)["status"] == "waiting_label"
    coord.on_scan(_ORDER, db, channel_id=0)                    # 裸码也算凭证
    st = coord.get_state(cid)
    assert st["status"] == "running"
    assert st["current_box_scan_qty"] == 0                     # 目标按计划
    assert targets == [24]                                     # 没有覆写


def test_label_rescan_update_refreshes_target(client):
    """已授权后重扫标签 (update 档): 用新扫数量更新本箱目标 (贴错标签重贴场景)."""
    coord, cid = _setup_label_flow(client, label_rescan_action="update")
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 48})
    coord.set_alarm_sink(lambda c, k, m: None)
    targets = []
    coord.set_box_target_setter(lambda ch, t: targets.append(t))
    db = SessionLocal()

    coord.on_scan(_ORDER, db, channel_id=0)
    coord.on_scan(_LABEL.format(qty=24), db, channel_id=0)     # 授权 24
    coord.on_scan(_LABEL.format(qty=19), db, channel_id=0)     # 重贴后重扫 19
    st = coord.get_state(cid)
    assert st["current_box_scan_qty"] == 19
    assert targets == [24, 24, 19]

    # 默认 ignore 档: 重扫不动目标 (回归对照)
    coord2, cid2 = _setup_label_flow(client, name="__test_pkg2", channel_id=1)
    coord2.set_mes_fetcher(lambda c, o: {"dispatch_qty": 48})
    coord2.set_alarm_sink(lambda c, k, m: None)
    coord2.on_scan(_ORDER, db, channel_id=1)
    coord2.on_scan(_LABEL.format(qty=24), db, channel_id=1)
    coord2.on_scan(_LABEL.format(qty=19), db, channel_id=1)
    assert coord2.get_state(cid2)["current_box_scan_qty"] == 24


def test_label_total_check_mismatch_alarm(client):
    """收尾对账: Σ各箱标签数量 ≠ 工单排产量 → 报警留痕 (不改成绩)."""
    coord, cid = _setup_label_flow(client, label_total_check=True)
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 48})   # 2 箱
    alarms = []
    coord.set_alarm_sink(lambda c, k, m: alarms.append(k))
    db = SessionLocal()

    coord.on_scan(_ORDER, db, channel_id=0)
    coord.on_scan(_LABEL.format(qty=24), db, channel_id=0)
    coord.on_cycle_settled(0, 1, True, db, slider_count=24)
    coord.on_scan(_LABEL.format(qty=23), db, channel_id=0)     # 标签共 47 ≠ 排产 48
    coord.on_cycle_settled(0, 2, True, db, slider_count=23)    # 按标签目标 23 本箱 OK

    row = db.query(PackagingFlowRun).first()
    assert row.status == "completed"
    assert row.box_ng == 0                                     # 箱成绩不受对账影响
    assert "label_total_mismatch" in alarms


def test_label_gate_off_zero_diff(client):
    """开关全关 (默认): 扫工单直接开箱 running, 无等扫态 — 存量行为零差异."""
    coord, cid = _setup_flow(client, count_unit="sliders",
                             items_per_box_source="config", items_per_box_fixed=24)
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 48})
    coord.set_alarm_sink(lambda c, k, m: None)
    db = SessionLocal()

    coord.on_scan("ORD1", db, channel_id=0)
    st = coord.get_state(cid)
    assert st["status"] == "running"
    assert st["label_authorized"] is True
