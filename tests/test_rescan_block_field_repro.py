"""现场复现: 已完成(OK)工单重扫拦截 — 上银现场同款配置组合.

2026-07-27 现场反馈: 开关已开, 工单 4/4 箱 96/96 全 OK 放工单收尾后,
重扫同号不报警. 本文件按现场同款配置 (sliders 96/箱 + 放工单=工单收尾 +
箱标签扫码授权 + 复合条码取段 + 连字符还原) 复现完整闭环, 验证拦截判据.
定位后此文件保留为回归护栏.
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


FIELD_CFG = dict(
    name="现场同款-上银SY包装线",
    enabled=True,
    channel_id=0,
    count_unit="sliders",
    items_per_box_source="config",
    items_per_box_fixed=96,
    box_count_source="field",
    box_count_field="dispatch_qty",
    trays_per_box_fixed=4,
    label_match="insert_char",
    hyphen_template="-",
    hyphen_pos=12,
    composite_label_enabled=True,
    composite_delimiter="|",
    composite_pick_mode="prefix",
    composite_prefix="JOB",
    tail_paper_order_required=True,
    tail_paper_step_label="放工单",
    tail_paper_as_close_action=True,
    oil_nozzle_required=False,
    block_completed_order_rescan=True,
    event_completed_order_rescan=3,
    box_label_scan_required=True,
    label_qty_enabled=True,
    label_qty_segment=3,
    on_mes_fail="offline",
)

RAW_ORDER = "JOB026070019 9242"  # 占位, 真实号在下面组装
ORDER_BARE = "JOB0260700199242"          # 一维裸码 (无分隔符)
BOX_LABEL = "ORD0001|{}|96.00|ABCD12"    # 箱标签复合 QR: ORD|JOB|数量|哈希


def _setup(client, **over):
    payload = dict(FIELD_CFG)
    payload.update(over)
    r = client.post("/api/v1/packaging-flows", json=payload)
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    coord = get_coordinator()
    coord.reload_configs(SessionLocal())
    return coord, cid


def _run_full_order(coord, cid, db, alarms):
    """扫工单 → 4 箱 (扫标签+周期OK) → 放工单收尾 → 断言完成 OK."""
    coord.on_scan(ORDER_BARE, db, channel_id=0)   # 扫工单 (一维裸码)
    st = coord.get_state(cid)
    assert st is not None, "工单未开: %s" % [a for a in alarms]
    order_no = st["order_no"]
    for _ in range(4):
        coord.on_scan(BOX_LABEL.format(ORDER_BARE), db, channel_id=0)  # 扫箱标签授权
        coord.on_cycle_settled(0, 1, True, db, slider_count=96)
    st = coord.get_state(cid)
    if st is not None:
        # 尾箱落账后挂"等放工单收尾" → 检测层通知放工单出现
        assert st.get("awaiting_paper"), f"未进等放工单态: {st.get('status')}"
        coord.on_step_detected(0, "放工单")
    assert coord.get_state(cid) is None, "工单未收尾"
    row = (SessionLocal().query(PackagingFlowRun)
           .filter_by(order_no=order_no)
           .order_by(PackagingFlowRun.id.desc()).first())
    assert row is not None and row.status == "completed", "完成未落库"
    assert (row.final_result or "").upper() == "OK", f"final={row.final_result}"
    return order_no


def test_现场同款__放工单收尾后重扫同一维码_必须拦截报警(client):
    coord, cid = _setup(client)
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 4})
    alarms = []
    coord.set_alarm_sink(lambda c, k, m: alarms.append((k, m)))
    db = SessionLocal()

    _run_full_order(coord, cid, db, alarms)
    alarms.clear()

    coord.on_scan(ORDER_BARE, db, channel_id=0)   # 重扫同号 (一维裸码)
    assert any(k == "completed_order_rescan" for k, _ in alarms), \
        f"重扫未拦截! alarms={alarms} state={coord.get_state(cid)}"
    assert coord.get_state(cid) is None, "重扫竟重新开单"


def test_现场同款__重扫扫的是箱标签复合码_也必须拦截(client):
    """现场工人重扫时可能扫的是箱标签 QR (复合码) 而非工单一维码 —
    复合取段后同号, 拦截判据应同样命中."""
    coord, cid = _setup(client)
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 4})
    alarms = []
    coord.set_alarm_sink(lambda c, k, m: alarms.append((k, m)))
    db = SessionLocal()

    _run_full_order(coord, cid, db, alarms)
    alarms.clear()

    coord.on_scan(BOX_LABEL.format(ORDER_BARE), db, channel_id=0)  # 重扫箱标签
    assert any(k == "completed_order_rescan" for k, _ in alarms), \
        f"箱标签重扫未拦截! alarms={alarms} state={coord.get_state(cid)}"
    assert coord.get_state(cid) is None


def test_现场同款__配置重建后config_id变化_重扫拦截失效复现(client):
    """怀疑点: 现场打补丁后如果删除重建过包装配置, 完成记录挂在旧 config_id 下,
    判据按新 config_id 查库查无此单 → 不拦. 本例复现该场景以确认行为."""
    coord, cid = _setup(client)
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 4})
    alarms = []
    coord.set_alarm_sink(lambda c, k, m: alarms.append((k, m)))
    db = SessionLocal()

    order_no = _run_full_order(coord, cid, db, alarms)

    # 删除配置重建 (id 变化), 完成记录还挂在旧 id 下
    r = client.put(f"/api/v1/packaging-flows/{cid}", json={"enabled": False})
    assert r.status_code == 200, r.text
    r = client.delete(f"/api/v1/packaging-flows/{cid}")
    assert r.status_code in (200, 204), r.text
    # 占位配置抬高自增, 确保重建配置拿到新 id (模拟现场"新增一条旧的弃用")
    r = client.post("/api/v1/packaging-flows", json=dict(
        FIELD_CFG, name="占位", enabled=False, channel_id=7))
    assert r.status_code == 201, r.text
    coord2, cid2 = _setup(client)
    assert cid2 != cid, f"id 未变化 ({cid2}), 场景未成立"
    coord2.set_mes_fetcher(lambda c, o: {"dispatch_qty": 4})
    alarms2 = []
    coord2.set_alarm_sink(lambda c, k, m: alarms2.append((k, m)))

    coord2.on_scan(ORDER_BARE, db, channel_id=0)
    blocked = any(k == "completed_order_rescan" for k, _ in alarms2)
    reopened = coord2.get_state(cid2) is not None
    print(f"[复现] 配置重建后: blocked={blocked} reopened={reopened}")
    # 当前实现按 config_id 过滤 → 预期不拦 (这正是失效场景之一)
    assert not blocked and reopened, "若此断言失败说明实现已改为跨配置查号"
