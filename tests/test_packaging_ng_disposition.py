"""包装线 NG 处置闭环 (v3.44) — 先红后绿复现 + 回归护栏.

客户报障 (2026-07-21, 上银 SY3):
  1. NG 弹人工确认框时箱账已先落 (记 NG + 翻页), 点"重做"重做的其实是下一箱;
  2. "少装等补做"挂起永远不触发 (容器混合把数量不足合成进整体 NG,
     旧入口条件却要求整体合格 → 死路);
  3. 箱数凑满工单静默收尾并从内存移除, 前端工单号/箱进度全部消失
     (感知 = "信息全被删了").

期望契约 (本文件锁定):
  A. NG 周期若配了人工确认 (hold_for_ack=True), 箱账挂起 (pending_box,
     reason=ng_ack), 箱号不翻页; 由 补齐(判OK)/输实际数(照实)/重做本箱 三个
     既有端点推进.
  B. 步骤侧合格仅数量不足 (steps_ok=True) 且项目开了补数量策略 → 挂起
     (reason=short_sliders), 不再依赖整体 is_good.
  C. 工单完成/作废后展示态 (get_display_state, /state API 同源) 仍返回收尾快照
     (order_no / final_result / box_details), 直到下一张工单开工才被覆盖;
     get_state 内部语义 (None=无在途) 保持不变.
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
    rows = mes_mock.lookup(order_no)
    return rows[0] if rows else None


def _setup(client, **over):
    payload = {
        "name": "__ngdisp_pkg",
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


REM = {"enabled": True, "allow_step": True, "allow_count": True}


# =============================================================
# A. NG + 人工确认 → 箱账挂起, 不翻页 (复现"重做的是下一箱")
# =============================================================

def test_ng_with_ack_holds_box_booking_same_box_redo(client):
    """NG 周期带人工确认: 箱挂起不落账不翻页; 重做后下一周期结算落到同一箱."""
    coord, cid = _setup(client)
    _scan(client, "ORD-NONEXACT")            # 250 滑块 → 3 箱 (96/96/58)
    db = SessionLocal()

    # 箱1 NG (缺步骤), 事件配了需人工确认 → 账务必须挂起
    coord.on_cycle_settled(0, 1, False, db, slider_count=96,
                           remediation=REM, steps_ok=False, hold_for_ack=True)
    st = coord.get_state(cid)
    assert st["status"] == "pending_remediation", "NG+人工确认应挂起而非直接落账"
    assert st["pending_box"]["reason"] == "ng_ack"
    assert st["box_done"] == 0, "确认前箱账不得落"
    assert st["current_box_index"] == 1, "确认前箱号不得翻页"

    # 工人选"重做本箱" → 回 running, 同一箱等重测
    assert coord.redo_pending(cid, db) is True
    st = coord.get_state(cid)
    assert st["status"] == "running" and st["current_box_index"] == 1

    # 重做后的周期 OK → 落到第 1 箱
    coord.on_cycle_settled(0, 2, True, db, slider_count=96)
    st = coord.get_state(cid)
    assert st["box_done"] == 1 and st["box_ng"] == 0
    assert st["box_details"][0]["result"] == "OK" and st["box_details"][0]["box"] == 1


def test_ng_with_ack_confirm_as_ng_books_and_advances(client):
    """挂起后工人认 NG (输实际数=当前数) → 照实落 NG 箱并翻页."""
    coord, cid = _setup(client)
    _scan(client, "ORD-NONEXACT")
    db = SessionLocal()

    coord.on_cycle_settled(0, 1, False, db, slider_count=90,
                           remediation=REM, steps_ok=False, hold_for_ack=True)
    assert coord.get_state(cid)["status"] == "pending_remediation"

    # 输实际数 90 (< 目标 96) = 认 NG 落账
    assert coord.supplement_sliders(cid, db, target_count=90, operator="op1") is True
    st = coord.get_state(cid)
    assert st["box_done"] == 1 and st["box_ng"] == 1
    assert st["box_details"][0]["result"] == "NG"
    assert st["current_box_index"] == 2, "认 NG 后翻到下一箱"


# =============================================================
# B. 少装 (步骤侧合格仅数量不足) → 挂起等补做 (修死路)
# =============================================================

def test_short_sliders_with_steps_ok_enters_pending(client):
    """容器混合把数量不足合成进整体 NG; steps_ok=True 时仍应走少装挂起."""
    coord, cid = _setup(client)
    _scan(client, "ORD-NONEXACT")
    db = SessionLocal()

    # 整体 is_good=False (数量不足合成), 但步骤侧 OK → 应挂起而非直接 NG
    coord.on_cycle_settled(0, 1, False, db, slider_count=72,
                           remediation=REM, steps_ok=True)
    st = coord.get_state(cid)
    assert st["status"] == "pending_remediation", "少装(仅数量不足)应挂起等补做"
    assert st["pending_box"]["reason"] == "short_sliders"
    assert st["box_done"] == 0

    # 补齐到目标 → OK 落账
    assert coord.supplement_sliders(cid, db, target_count=None, operator="op1") is True
    st = coord.get_state(cid)
    assert st["box_done"] == 1 and st["box_ng"] == 0
    assert st["box_details"][0]["result"] == "OK"
    assert st["box_details"][0].get("remediated"), "补做必须留痕"


def test_short_sliders_without_remediation_books_ng(client):
    """没开补数量策略: 少装照旧直接落 NG (老行为保持)."""
    coord, cid = _setup(client)
    _scan(client, "ORD-NONEXACT")
    db = SessionLocal()

    coord.on_cycle_settled(0, 1, False, db, slider_count=72,
                           remediation={"enabled": False}, steps_ok=True)
    st = coord.get_state(cid)
    assert st["status"] == "running"
    assert st["box_done"] == 1 and st["box_ng"] == 1


# =============================================================
# C. 工单收尾后信息保留 (复现"信息全被删了")
# =============================================================

def test_completed_run_state_retained(client):
    """做满收尾后 get_state 仍返回收尾快照, 工单号/结果/箱明细不消失."""
    coord, cid = _setup(client)
    _scan(client, "ORD-NONEXACT")            # 3 箱 (96/96/58)
    db = SessionLocal()
    coord.on_cycle_settled(0, 1, True, db, slider_count=96)
    coord.on_cycle_settled(0, 2, True, db, slider_count=96)
    coord.on_cycle_settled(0, 3, True, db, slider_count=58)

    # 内部语义不变: 无在途 → None (扫码/结算判定依赖)
    assert coord.get_state(cid) is None
    # 展示态 (前端 /state API 同源): 收尾快照保留
    st = coord.get_display_state(cid)
    assert st is not None, "工单收尾后展示态不得消失"
    assert st["status"] == "completed"
    assert st["order_no"] == "ORDNONEXACT"
    assert st["final_result"] == "OK"
    assert len(st["box_details"]) == 3

    # HTTP 面上同样拿得到收尾快照
    r = client.get(f"/api/v1/packaging-flows/{cid}/state")
    assert r.status_code == 200 and r.json()["state"]["status"] == "completed"

    # 扫新单后被覆盖为新工单 (sliders 口径开单即开第 1 箱 → running)
    _scan(client, "ORD-EXACT")
    st = coord.get_display_state(cid)
    assert st["order_no"] == "ORDEXACT" and st["status"] in ("order_loaded", "running")


def test_all_ng_completion_retains_info_not_wipe(client):
    """客户复现路径: 箱全 NG 做满 → 收尾快照保留 (而不是内存移除后一片空白)."""
    coord, cid = _setup(client)
    _scan(client, "ORD-SINGLE")              # 50 滑块 → 1 箱
    db = SessionLocal()
    coord.on_cycle_settled(0, 1, False, db, slider_count=40,
                           remediation={"enabled": False}, steps_ok=True)

    st = coord.get_display_state(cid)
    assert st is not None, "NG 收尾后工单信息不得消失"
    assert st["status"] == "completed" and st["final_result"] == "NG"
    assert st["order_no"] == "ORDSINGLE"
