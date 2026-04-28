"""
仿真验证 v3.1.0: 工单 binding_scope 三种粒度的计件链路

场景:
1. project 模式: cycle_end → increment_completed +1
2. channels 模式: 按 channel_id 白名单匹配, cycle_end +1
3. cluster 模式: cluster_collector.box_complete → +1 (1 个 box_serial = 1 件)
4. _normalize_binding 校验: 必填字段缺失抛错, scope 互斥清空
5. find_cluster_orders: 按 priority + created_at 取首条
6. cluster 模式校正重推 (is_recovery=True) 不重复 +1
7. cluster 模式工单达到 planned_qty 自动 completed

使用独立 SQLite 文件, 不污染主库.
"""
import os
import sys
import shutil
import tempfile

# ── 切到独立 DATA_DIR (在 import backend.* 之前!) ─────────────
_TMPDIR = tempfile.mkdtemp(prefix="workorder_binding_sim_")
os.environ["TIANJUN_DATA_DIR"] = _TMPDIR
for sub in ("uploads", "uploads/models", "uploads/images", "uploads/videos",
            "counters", "recordings"):
    os.makedirs(os.path.join(_TMPDIR, sub), exist_ok=True)

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backend.db.database import Base, engine, SessionLocal  # noqa: E402
from backend.models import models as _models  # noqa: E402,F401  registry 装载
from backend.models.mes_models import (  # noqa: E402
    ClusterConfig, WorkOrder,
)
from backend.models.models import Project  # noqa: E402
from backend.services.work_order import WorkOrderService  # noqa: E402
from backend.services import cluster_collector as cc_mod  # noqa: E402

# 整库重建
Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)


# ── Mock MES gateway: 不发 HTTP, 只记录 ────────────────────────
_push_log = []


class _MockGW:
    def dispatch(self, event_name, payload, channel_id=None):
        _push_log.append({
            "event": event_name,
            "overall_result": payload.get("overall_result"),
            "box_serial": payload.get("box_serial"),
        })
        return {"dispatched": True}


def _get_mock_gw():
    return _MockGW()


import backend.services.mes_gateway as mes_gw_mod  # noqa: E402
mes_gw_mod.get_mes_gateway = _get_mock_gw


# ── 测试断言工具 ───────────────────────────────────────────────
_pass = 0
_fail = 0


def _assert(cond, msg):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  [PASS] {msg}")
    else:
        _fail += 1
        print(f"  [FAIL] {msg}")
        raise AssertionError(msg)


def _section(title):
    print(f"\n=== {title} ===")


# ── 公共种子 ──────────────────────────────────────────────────
def _seed_project_and_cluster():
    db = SessionLocal()
    try:
        db.query(WorkOrder).delete()
        db.query(Project).delete()
        db.query(ClusterConfig).delete()

        db.add(Project(id=1, name="测试项目A"))
        db.add(Project(id=2, name="另一个项目"))

        db.add(ClusterConfig(
            id=1, role="master",
            expected_stations=["A", "B"],
            timeout_sec=300, timeout_push=False, enabled=True,
        ))
        db.commit()
    finally:
        db.close()


def _get_order(order_id):
    db = SessionLocal()
    try:
        o = db.query(WorkOrder).filter_by(id=order_id).first()
        if not o:
            return None
        return {
            "id": o.id, "order_no": o.order_no,
            "binding_scope": o.binding_scope,
            "project_id": o.project_id,
            "target_channels": o.target_channels,
            "target_stations": o.target_stations,
            "status": o.status,
            "planned_qty": o.planned_qty,
            "completed_qty": o.completed_qty,
            "good_qty": o.good_qty, "ng_qty": o.ng_qty,
        }
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────
# Step 1: _normalize_binding 校验
# ─────────────────────────────────────────────────────────────
def test_normalize_binding():
    _section("Step 1: _normalize_binding 校验")
    svc = WorkOrderService()

    # 1.1 project 模式必须带 project_id
    try:
        svc._normalize_binding({"binding_scope": "project"})
        _assert(False, "project 模式无 project_id 应抛 ValueError")
    except ValueError as e:
        _assert("project_id 必填" in str(e), f"project 模式无 pid 抛错: {e}")

    # 1.2 project 模式正常
    out = svc._normalize_binding({"binding_scope": "project", "project_id": 1})
    _assert(out["binding_scope"] == "project", "scope=project")
    _assert(out["project_id"] == 1, "project_id=1")
    _assert(out["target_channels"] is None, "target_channels 强制清空")
    _assert(out["target_stations"] is None, "target_stations 强制清空")

    # 1.3 channels 模式必须带 target_channels
    try:
        svc._normalize_binding({"binding_scope": "channels"})
        _assert(False, "channels 模式无 target_channels 应抛错")
    except ValueError as e:
        _assert("target_channels" in str(e), f"channels 模式无白名单抛错: {e}")

    # 1.4 channels 模式正常 + project_id 强制清空
    out = svc._normalize_binding({
        "binding_scope": "channels",
        "project_id": 999,
        "target_channels": [0, 1],
    })
    _assert(out["target_channels"] == [0, 1], "target_channels=[0,1]")
    _assert(out["project_id"] is None, "channels 模式强制清空 project_id")

    # 1.5 cluster 模式不再要求 target_stations
    out = svc._normalize_binding({"binding_scope": "cluster"})
    _assert(out["binding_scope"] == "cluster", "cluster scope OK")
    _assert(out["target_stations"] is None, "cluster 默认 target_stations=None")

    # 1.6 cluster 模式有 target_stations 时保留 (高级位)
    out = svc._normalize_binding({
        "binding_scope": "cluster",
        "target_stations": ["A", "B"],
    })
    _assert(out["target_stations"] == ["A", "B"], "cluster 显式 target_stations 保留")

    # 1.7 非法 scope
    try:
        svc._normalize_binding({"binding_scope": "lol"})
        _assert(False, "非法 scope 应抛错")
    except ValueError:
        _assert(True, "非法 scope 抛错")


# ─────────────────────────────────────────────────────────────
# Step 2: project 模式 — get_active_order + increment_completed
# ─────────────────────────────────────────────────────────────
def test_project_mode():
    _section("Step 2: project 模式计件")
    svc = WorkOrderService()
    db = SessionLocal()
    try:
        order = svc.create_order(db, {
            "order_no": "WO-PRJ-001",
            "product_name": "工件A",
            "binding_scope": "project",
            "project_id": 1,
            "planned_qty": 3,
        })
        order_id = order.id
        svc.change_status(db, order_id, "pending")
        svc.change_status(db, order_id, "in_progress")
        db.commit()

        # 拿活跃工单 (project_id=1, channel_id=0)
        active = svc.get_active_order(db, project_id=1, channel_id=0)
        _assert(active is not None and active.id == order_id,
                f"project=1 ch=0 应找到 WO-PRJ-001 (实际: {active and active.order_no})")

        # 项目不匹配 → 找不到
        active = svc.get_active_order(db, project_id=999, channel_id=0)
        _assert(active is None, "project=999 应找不到")

        # 模拟 cycle_end +1 (一个 OK + 一个 NG)
        svc.increment_completed(db, order_id, is_good=True)
        svc.increment_completed(db, order_id, is_good=False)
        db.commit()

        snap = _get_order(order_id)
        _assert(snap["completed_qty"] == 2, f"completed_qty=2 (实际 {snap['completed_qty']})")
        _assert(snap["good_qty"] == 1, "good_qty=1")
        _assert(snap["ng_qty"] == 1, "ng_qty=1")
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────
# Step 3: channels 模式 — 按工位白名单匹配
# ─────────────────────────────────────────────────────────────
def test_channels_mode():
    _section("Step 3: channels 模式计件")
    svc = WorkOrderService()
    db = SessionLocal()
    try:
        order = svc.create_order(db, {
            "order_no": "WO-CH-001",
            "product_name": "工件B",
            "binding_scope": "channels",
            "target_channels": [1],   # 只绑工位 1
            "planned_qty": 5,
        })
        order_id = order.id
        svc.change_status(db, order_id, "pending")
        svc.change_status(db, order_id, "in_progress")
        db.commit()

        # 工位 1 上 → 应该拿到这条工单 (即使 project_id 不匹配, 因为 channels 模式不看 pid)
        active = svc.get_active_order(db, project_id=999, channel_id=1)
        _assert(active is not None and active.id == order_id,
                "channels 模式 ch=1 应匹配 (不看 project_id)")

        # 工位 0 上 → 找不到
        active = svc.get_active_order(db, project_id=999, channel_id=0)
        _assert(active is None, "channels 模式 ch=0 不在白名单")

        # 工位 0 上 + project_id=1 → 也不应该拿到 (这条是 channels 模式)
        # 但因为 Step 2 的 WO-PRJ-001 还在跑, 所以会拿到它. 我们要确认拿到的是 WO-PRJ-001 不是 WO-CH-001
        active = svc.get_active_order(db, project_id=1, channel_id=0)
        _assert(active is not None and active.order_no == "WO-PRJ-001",
                f"ch=0 + project=1 应拿到 project 模式工单 (实际 {active and active.order_no})")
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────
# Step 4: cluster 模式 — find_cluster_orders + cluster_collector +1
# ─────────────────────────────────────────────────────────────
def test_cluster_mode():
    _section("Step 4: cluster 模式计件 (端到端)")
    svc = WorkOrderService()
    db = SessionLocal()
    try:
        order = svc.create_order(db, {
            "order_no": "WO-CLU-001",
            "product_name": "工件C",
            "binding_scope": "cluster",
            "planned_qty": 3,
        })
        order_id = order.id
        svc.change_status(db, order_id, "pending")
        svc.change_status(db, order_id, "in_progress")
        db.commit()

        # 4.1 find_cluster_orders 默认匹配 (target_stations=None)
        orders = svc.find_cluster_orders(db)
        _assert(len(orders) == 1 and orders[0].id == order_id,
                f"无 station 过滤应找到 1 条 (实际 {len(orders)})")

        # 4.2 cluster 工单不应该被 get_active_order 返回
        active = svc.get_active_order(db, project_id=1, channel_id=0)
        _assert(active is None or active.id != order_id,
                "cluster 模式工单不应进 get_active_order")
    finally:
        db.close()

    # 4.3 端到端: 集群上报 box_complete → +1
    collector = cc_mod.ClusterCollector()
    box = "BOX-WO-001"

    # 上报 A + B 两个站 → 齐了 → push
    push_count_before = len(_push_log)
    snap_before = _get_order(order_id)

    collector.receive_station_report(
        station_id="A", box_serial=box,
        cycle_context={"cycle": {"is_good": True}, "workpiece": {"serial_no": box}},
        source_address="A-local", channel_id=0, is_good=True, event_name="ok",
    )
    collector.receive_station_report(
        station_id="B", box_serial=box,
        cycle_context={"cycle": {"is_good": True}, "workpiece": {"serial_no": box}},
        source_address="B-local", channel_id=0, is_good=True, event_name="ok",
    )

    snap_after = _get_order(order_id)
    pushes_now = len(_push_log) - push_count_before

    _assert(pushes_now == 1, f"应推 1 次 box_complete (实际 {pushes_now})")
    _assert(snap_after["completed_qty"] == snap_before["completed_qty"] + 1,
            f"completed_qty 应 +1 (从 {snap_before['completed_qty']} → {snap_after['completed_qty']})")
    _assert(snap_after["good_qty"] == snap_before["good_qty"] + 1, "good_qty +1")

    # 4.4 校正重推 (recovery) 不重复 +1
    snap_before = _get_order(order_id)
    push_count_before = len(_push_log)

    # 同一个 box 重报 B = NG (会触发校正重推)
    collector.receive_station_report(
        station_id="B", box_serial=box,
        cycle_context={"cycle": {"is_good": False},
                       "workpiece": {"serial_no": box},
                       "ng_steps": [{"label": "缺件X"}]},
        source_address="B-local", channel_id=0, is_good=False, event_name="ng",
    )

    snap_after = _get_order(order_id)
    pushes_now = len(_push_log) - push_count_before
    _assert(pushes_now == 1, f"校正应再推 1 次 MES (实际 {pushes_now})")
    _assert(snap_after["completed_qty"] == snap_before["completed_qty"],
            f"recovery 不应重复 +1 (前 {snap_before['completed_qty']} 后 {snap_after['completed_qty']})")


# ─────────────────────────────────────────────────────────────
# Step 5: cluster 模式达成 planned_qty 自动 completed
# ─────────────────────────────────────────────────────────────
def test_cluster_auto_complete():
    _section("Step 5: cluster 模式达成 planned_qty 自动 completed")
    svc = WorkOrderService()
    db = SessionLocal()
    try:
        order = svc.create_order(db, {
            "order_no": "WO-CLU-002",
            "product_name": "工件D",
            "binding_scope": "cluster",
            "planned_qty": 2,
        })
        order_id = order.id
        svc.change_status(db, order_id, "pending")
        svc.change_status(db, order_id, "in_progress")
        db.commit()
    finally:
        db.close()

    # 把上一条 WO-CLU-001 切到 paused, 不抢占首条
    db = SessionLocal()
    try:
        prev = db.query(WorkOrder).filter_by(order_no="WO-CLU-001").first()
        if prev:
            prev.status = "paused"
        db.commit()
    finally:
        db.close()

    collector = cc_mod.ClusterCollector()

    # 推两个 box: 第一个 OK, 第二个 NG → completed_qty=2 = planned_qty → completed
    for i, is_good in enumerate([True, False], start=1):
        box = f"BOX-AC-{i:03d}"
        for st in ("A", "B"):
            collector.receive_station_report(
                station_id=st, box_serial=box,
                cycle_context={"cycle": {"is_good": is_good},
                               "workpiece": {"serial_no": box},
                               **({"ng_steps": [{"label": "X"}]} if not is_good else {})},
                source_address=f"{st}-local", channel_id=0,
                is_good=is_good, event_name="ok" if is_good else "ng",
            )

    snap = _get_order(order_id)
    _assert(snap["completed_qty"] == 2, f"completed_qty=2 (实际 {snap['completed_qty']})")
    _assert(snap["good_qty"] == 1 and snap["ng_qty"] == 1, "good=1 ng=1")
    _assert(snap["status"] == "completed",
            f"达 planned_qty 应自动 completed (实际 {snap['status']})")


# ─────────────────────────────────────────────────────────────
# Step 6: 多条 cluster 工单 — 取首条优先级
# ─────────────────────────────────────────────────────────────
def test_cluster_priority():
    _section("Step 6: 多条 cluster 工单只取首条")
    svc = WorkOrderService()
    db = SessionLocal()
    try:
        # 把所有现存 in_progress 都先停掉, 以免干扰
        db.query(WorkOrder).filter(WorkOrder.status == "in_progress").update(
            {"status": "paused"}, synchronize_session=False
        )
        db.commit()

        # 创建两条 cluster 工单, 优先级不同
        o_low = svc.create_order(db, {
            "order_no": "WO-CLU-LOW",
            "product_name": "低优先级",
            "binding_scope": "cluster",
            "priority": 5,
            "planned_qty": 10,
        })
        o_high = svc.create_order(db, {
            "order_no": "WO-CLU-HIGH",
            "product_name": "高优先级",
            "binding_scope": "cluster",
            "priority": 1,
            "planned_qty": 10,
        })
        for oid in (o_low.id, o_high.id):
            svc.change_status(db, oid, "pending")
            svc.change_status(db, oid, "in_progress")
        db.commit()

        orders = svc.find_cluster_orders(db)
        _assert(len(orders) == 1, f"应只返回 1 条 (实际 {len(orders)})")
        _assert(orders[0].order_no == "WO-CLU-HIGH",
                f"应是高优先级 (实际 {orders[0].order_no})")
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────────────────────
def main():
    _seed_project_and_cluster()
    test_normalize_binding()
    test_project_mode()
    test_channels_mode()
    test_cluster_mode()
    test_cluster_auto_complete()
    test_cluster_priority()

    print(f"\n=== 总结: {_pass} 通过 / {_fail} 失败 ===")
    if _fail > 0:
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    finally:
        try:
            shutil.rmtree(_TMPDIR, ignore_errors=True)
        except Exception:
            pass
