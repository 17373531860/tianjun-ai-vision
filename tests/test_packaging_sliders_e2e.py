"""上银包装线 MES 闭环 — sliders 口径端到端集成 (v3.22).

走真实链路: 扫码 HTTP 入口 (/packaging-flows/scan) → 协调器状态机 → DB.
MES 拉单用 tools/mes_mock 的查单逻辑接成 fetcher (等价于真连 mock 服务, 不起 HTTP).
检测周期 (检测层驱动, 无 HTTP 入口) 直接调 coord.on_cycle_settled 模拟一个箱完成.

覆盖组合: 非整除尾箱全流程完成 / 整除尾箱 / 漏箱换单 / MES 查不到阻断 / 自动切项目失败兜底.
每用例前清表 + 复位单例, 防 module-level 单例串污染.
"""
import pytest

from backend.services.packaging_flow_coordinator import get_coordinator
from backend.db.database import SessionLocal
from backend.models.mes_models import PackagingFlowConfig, PackagingFlowRun
from tools import mes_mock


@pytest.fixture(autouse=True)
def _clean(client):
    coord = get_coordinator()
    coord.cleanup_for_testing()
    db = SessionLocal()
    db.query(PackagingFlowRun).delete()
    db.query(PackagingFlowConfig).delete()
    db.commit()
    db.close()
    yield
    coord.cleanup_for_testing()


def _mock_fetcher(cfg, order_no):
    """把 MES mock 的查单逻辑接成协调器 fetcher (返回首条原始记录 / None)."""
    rows = mes_mock.lookup(order_no)
    return rows[0] if rows else None


def _setup(client, **over):
    payload = {
        "name": "__e2e_pkg",
        "enabled": True,
        "channel_id": 0,
        "count_unit": "sliders",
        "items_per_box_source": "config",
        "items_per_box_fixed": 96,
        "slider_total_field": "dispatch_qty",
        "on_mes_fail": "offline",
    }
    payload.update(over)
    r = client.post("/api/v1/packaging-flows", json=payload)
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    coord = get_coordinator()
    coord.reload_configs(SessionLocal())
    coord.set_mes_fetcher(_mock_fetcher)
    coord.set_alarm_sink(lambda c, k, m: None)
    return coord, cid


def _scan(client, code):
    return client.post("/api/v1/packaging-flows/scan",
                       json={"code": code, "channel_id": 0}).json()


def test_e2e_nonexact_tail_full_flow(client):
    """扫工单(HTTP) → mock 拉 250 滑块 → 3 箱(尾箱58) → 逐箱检测周期 → 完成 OK."""
    coord, cid = _setup(client, items_per_box_fixed=96)
    targets = []
    coord.set_box_target_setter(lambda ch, t: targets.append(t))

    res = _scan(client, "ORD-NONEXACT")
    assert res["handled"] is True
    st = res["state"]
    assert st["box_total"] == 3 and st["tail_target"] == 58 and st["count_unit"] == "sliders"

    db = SessionLocal()
    coord.on_cycle_settled(0, 1, True, db, slider_count=96)   # 箱1
    coord.on_cycle_settled(0, 2, True, db, slider_count=96)   # 箱2
    coord.on_cycle_settled(0, 3, True, db, slider_count=58)   # 尾箱58

    row = db.query(PackagingFlowRun).filter_by(order_no="ORDNONEXACT").first()
    assert row is not None
    assert row.status == "completed" and row.final_result == "OK"
    assert row.box_done == 3 and row.box_ng == 0
    assert targets == [96, 96, 58]            # 普通箱96×2 → 尾箱58


def test_e2e_exact_tail_full_flow(client):
    """整除: 240 滑块 / 每箱24 → 10 箱, 第10箱尾箱仍满24, 全做完 OK."""
    coord, cid = _setup(client, items_per_box_fixed=24)
    res = _scan(client, "ORD-EXACT")
    st = res["state"]
    assert st["box_total"] == 10 and st["tail_target"] == 24

    db = SessionLocal()
    for cyc in range(1, 11):
        coord.on_cycle_settled(0, cyc, True, db, slider_count=24)

    row = db.query(PackagingFlowRun).filter_by(order_no="ORDEXACT").first()
    assert row.status == "completed" and row.final_result == "OK" and row.box_done == 10


def test_e2e_short_box_rescan_redo(client):
    """漏箱: 10 箱只做 1 箱就扫另一工单 → 漏箱报警, redo 不切单."""
    coord, cid = _setup(client, items_per_box_fixed=24)
    alarms = []
    coord.set_alarm_sink(lambda c, k, m: alarms.append(k))

    _scan(client, "ORD-EXACT")                # 10 箱
    db = SessionLocal()
    coord.on_cycle_settled(0, 1, True, db, slider_count=24)   # 箱1
    _scan(client, "ORD-SPEC2")                # 换单 → 漏箱 redo

    assert "short_box" in alarms
    state = coord.get_state(cid)
    assert state is not None and state["order_no"] == "ORDEXACT"   # redo 不切


def test_e2e_mes_not_found_block(client):
    """MES 查不到 + on_mes_fail=block: 报警 + 不开工单 (零进行中)."""
    coord, cid = _setup(client, items_per_box_fixed=96, on_mes_fail="block")
    alarms = []
    coord.set_alarm_sink(lambda c, k, m: alarms.append(k))

    res = _scan(client, "UNKNOWN-JOB")        # mock 查不到 → None
    assert "mes_fail" in alarms
    assert coord.get_state(cid) is None       # block → 没开工单


def test_e2e_single_box_is_tail(client):
    """单箱: 50 滑块 / 每箱96 → 1 箱(即尾箱, 目标50), 进50判 OK."""
    coord, cid = _setup(client, items_per_box_fixed=96)
    targets = []
    coord.set_box_target_setter(lambda ch, t: targets.append(t))
    res = _scan(client, "ORD-SINGLE")
    st = res["state"]
    assert st["box_total"] == 1 and st["tail_target"] == 50

    db = SessionLocal()
    coord.on_cycle_settled(0, 1, True, db, slider_count=50)
    row = db.query(PackagingFlowRun).filter_by(order_no="ORDSINGLE").first()
    assert row.status == "completed" and row.final_result == "OK" and row.box_done == 1
    assert targets == [50]                    # 第1箱即尾箱, 目标=余数50
