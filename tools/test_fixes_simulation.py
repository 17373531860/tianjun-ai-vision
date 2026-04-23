"""
本会话修复点的端到端仿真验证脚本

覆盖三个本轮改动：
1. 扫码器"OK 后同码冷却"：同条码对应工件上次合格且距完成时间 < 冷却秒数 → 静默丢弃
2. 集群同站点上报"按 (通道, 副机地址) 去重最新覆盖"
3. 集群批量清理 API 新增 scope=older + before_days=N 分支

脚本用临时 SQLite DB，跑完自动清理。不依赖 HTTP/uvicorn，直接调 service 层。

运行：
    cd <项目根>
    python tools/test_fixes_simulation.py
"""
from __future__ import annotations
import os
import sys
import shutil
import tempfile
import time
from datetime import datetime, timedelta


# ---------- 初始化环境 ----------
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMPDIR = tempfile.mkdtemp(prefix="tianjun_sim_test_")
os.environ["TIANJUN_DATA_DIR"] = TMPDIR
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# 静掉一些启动日志，免得满屏
os.environ.setdefault("TIANJUN_LOG_LEVEL", "WARNING")


def _print_banner(title: str):
    print()
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)


def _assert(cond, msg, failures: list):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {msg}")
    if not cond:
        failures.append(msg)


def cleanup():
    try:
        shutil.rmtree(TMPDIR, ignore_errors=True)
    except Exception:
        pass


# ---------- 准备 DB ----------
print(f"[ENV] 临时数据目录: {TMPDIR}")
from backend.db.database import Base, engine, SessionLocal  # noqa: E402
import backend.models.models  # noqa: E402,F401  触发 ORM 注册
import backend.models.mes_models  # noqa: E402,F401
Base.metadata.create_all(engine)
print(f"[ENV] DB schema 已建好")


# ---------- 测试辅助 ----------
def make_scanner_conn(channel_id: int, cooldown_sec: int,
                      ip: str = "127.0.0.1", device_id: int = 1):
    """不走网络，直接构造一条 ScannerConnection 塞进 service 里。"""
    from backend.services import scanner as scanner_mod
    from backend.services.scanner import ScannerConnection
    svc = scanner_mod.get_scanner_service()
    conn = ScannerConnection(
        device_id=device_id,
        name=f"sim-scanner-{device_id}",
        ip=ip,
        port=55256,
        channel_id=channel_id,
        enabled=True,
        ok_rescan_cooldown_sec=cooldown_sec,
    )
    svc._connections[device_id] = conn
    return svc, conn


# ---------- 用例 1：扫码器"OK 后同码冷却" ----------
def test_ok_rescan_cooldown():
    _print_banner("用例 1: 扫码器 OK 后同码冷却")
    failures: list = []

    from backend.services.mes_hooks import MESHookManager
    from backend.models.mes_models import Workpiece, ScanLog
    from backend.models.models import Project

    # 1.1 造一个最小 Project
    db = SessionLocal()
    proj = Project(name="sim-proj")
    db.add(proj)
    db.commit()
    db.refresh(proj)
    project_id = proj.id
    db.close()
    print(f"  [SETUP] project_id={project_id}")

    # 1.2 造一个扫码器连接 (channel=0, cooldown=5s)
    make_scanner_conn(channel_id=0, cooldown_sec=5, device_id=101)
    print(f"  [SETUP] 扫码器：channel=0, cooldown=5s")

    mes = MESHookManager()
    serial = "SIM-BARCODE-001"

    # 1.3 第一次扫码 → 正常登记 + 模拟检测流程 OK
    db = SessionLocal()
    try:
        mes._handle_scan(db, channel_id=0, serial_no=serial,
                         raw_data=serial, project_id=project_id, device_id=101)
        wp = (db.query(Workpiece)
              .filter(Workpiece.serial_no == serial,
                      Workpiece.project_id == project_id).first())
        _assert(wp is not None, "第一次扫码：工件登记成功", failures)
        wp_id = wp.id
        # 模拟开始检测
        mes._workpiece_svc.mark_inspecting(db, wp_id)
        # 模拟检测结束 → OK
        mes._workpiece_svc.set_result(db, wp_id, is_good=True, cycle_id=None)
        db.commit()
    finally:
        db.close()

    # 1.4 冷却期内再扫同条码 → 应被丢弃（不新建扫码日志成功项，state 不变）
    db = SessionLocal()
    try:
        ok_logs_before = db.query(ScanLog).filter(
            ScanLog.parsed_serial == serial, ScanLog.success == True
        ).count()
        err_logs_before = db.query(ScanLog).filter(
            ScanLog.parsed_serial == serial, ScanLog.success == False
        ).count()

        mes._handle_scan(db, channel_id=0, serial_no=serial,
                         raw_data=serial, project_id=project_id, device_id=101)
        db.commit()

        ok_logs_after = db.query(ScanLog).filter(
            ScanLog.parsed_serial == serial, ScanLog.success == True
        ).count()
        err_logs_after = db.query(ScanLog).filter(
            ScanLog.parsed_serial == serial, ScanLog.success == False
        ).count()

        _assert(ok_logs_after == ok_logs_before,
                f"冷却期内重复扫：success 扫码日志不增加 ({ok_logs_before}→{ok_logs_after})", failures)
        _assert(err_logs_after == err_logs_before + 1,
                f"冷却期内重复扫：产生 1 条失败日志 ({err_logs_before}→{err_logs_after})", failures)

        # 工件状态不应被改回 queued
        wp = db.query(Workpiece).filter(
            Workpiece.serial_no == serial,
            Workpiece.project_id == project_id).first()
        _assert(wp.status == "ok",
                f"冷却期内重复扫：工件状态保持 'ok' (实际={wp.status})", failures)
    finally:
        db.close()

    # 1.5 NG 工件不受冷却限制 → 另建一个条码模拟
    serial_ng = "SIM-BARCODE-NG-001"
    db = SessionLocal()
    try:
        mes._handle_scan(db, channel_id=0, serial_no=serial_ng,
                         raw_data=serial_ng, project_id=project_id, device_id=101)
        wp_ng = db.query(Workpiece).filter(
            Workpiece.serial_no == serial_ng,
            Workpiece.project_id == project_id).first()
        mes._workpiece_svc.mark_inspecting(db, wp_ng.id)
        mes._workpiece_svc.set_result(db, wp_ng.id, is_good=False, cycle_id=None)
        db.commit()

        # 立即再扫 → 应放行 (NG 可复检)
        mes._handle_scan(db, channel_id=0, serial_no=serial_ng,
                         raw_data=serial_ng, project_id=project_id, device_id=101)
        db.commit()
        wp_ng2 = db.query(Workpiece).filter(
            Workpiece.serial_no == serial_ng,
            Workpiece.project_id == project_id).first()
        _assert(wp_ng2.status == "queued",
                f"NG 工件立即重扫：状态被重置为 queued (实际={wp_ng2.status})", failures)
    finally:
        db.close()

    # 1.6 OK 工件过了冷却时间 → 放行
    db = SessionLocal()
    try:
        wp_ok = db.query(Workpiece).filter(
            Workpiece.serial_no == serial,
            Workpiece.project_id == project_id).first()
        # 手工把 last_inspect_at 改成 10 秒前，模拟过了冷却
        wp_ok.last_inspect_at = datetime.now() - timedelta(seconds=10)
        db.flush()
        db.commit()

        mes._handle_scan(db, channel_id=0, serial_no=serial,
                         raw_data=serial, project_id=project_id, device_id=101)
        db.commit()
        wp_ok2 = db.query(Workpiece).filter(
            Workpiece.serial_no == serial,
            Workpiece.project_id == project_id).first()
        _assert(wp_ok2.status == "queued",
                f"OK 工件过了冷却期重扫：放行，状态 queued (实际={wp_ok2.status})", failures)
    finally:
        db.close()

    # 1.7 cooldown=0 (关闭) → 永远放行
    make_scanner_conn(channel_id=1, cooldown_sec=0, device_id=102)
    serial2 = "SIM-BARCODE-002"
    db = SessionLocal()
    try:
        mes._handle_scan(db, channel_id=1, serial_no=serial2,
                         raw_data=serial2, project_id=project_id, device_id=102)
        wp2 = db.query(Workpiece).filter(
            Workpiece.serial_no == serial2,
            Workpiece.project_id == project_id).first()
        mes._workpiece_svc.mark_inspecting(db, wp2.id)
        mes._workpiece_svc.set_result(db, wp2.id, is_good=True, cycle_id=None)
        db.commit()
        # 立即再扫
        mes._handle_scan(db, channel_id=1, serial_no=serial2,
                         raw_data=serial2, project_id=project_id, device_id=102)
        db.commit()
        wp2b = db.query(Workpiece).filter(
            Workpiece.serial_no == serial2,
            Workpiece.project_id == project_id).first()
        _assert(wp2b.status == "queued",
                f"cooldown=0 时 OK 工件立即重扫：放行 (实际状态={wp2b.status})", failures)
    finally:
        db.close()

    return failures


# ---------- 用例 2：集群合并去重 ----------
def test_cluster_sub_report_dedup():
    _print_banner("用例 2: 集群同路上报最新覆盖、异路保留")
    failures: list = []

    from backend.services.cluster_collector import ClusterCollector
    from backend.models.mes_models import BoxAggregation

    collector = ClusterCollector()

    def _report(box, station, channel, source, is_good, event="cycle_end"):
        ctx = {
            "cycle": {
                "result": "OK" if is_good else "NG",
                "is_good": is_good,
                "ng_reason": None if is_good else f"ch{channel}-miss",
                "duration": 10.0,
            },
            "device_role": "vision",
        }
        return collector._receive_station_report_locked(
            station_id=station, box_serial=box,
            cycle_context=ctx, source_address=source,
            channel_id=channel, is_good=is_good, event_name=event,
        )

    box = "SIM-BOX-001"

    # 2.1 同路重复上报（channel=0, source=10.0.0.1）NG -> NG -> NG
    _report(box, "A", 0, "10.0.0.1", False)
    _report(box, "A", 0, "10.0.0.1", False)
    _report(box, "A", 0, "10.0.0.1", False)

    db = SessionLocal()
    agg = db.query(BoxAggregation).filter(
        BoxAggregation.box_serial == box, BoxAggregation.station_id == "A"
    ).first()
    subs = (agg.cycle_context or {}).get("sub_reports", [])
    _assert(len(subs) == 1, f"同路 NG 重复 3 次 → sub_reports=1 条 (实际 {len(subs)})", failures)
    _assert(agg.is_good is False, "整站 is_good=False", failures)
    db.close()

    # 2.2 同路再上报 OK（覆盖）→ 还是 1 条
    _report(box, "A", 0, "10.0.0.1", True)
    db = SessionLocal()
    agg = db.query(BoxAggregation).filter(
        BoxAggregation.box_serial == box, BoxAggregation.station_id == "A"
    ).first()
    subs = (agg.cycle_context or {}).get("sub_reports", [])
    _assert(len(subs) == 1, f"同路最新结果 OK → sub_reports=1 条 (实际 {len(subs)})", failures)
    _assert(agg.is_good is True, f"整站 is_good 更新为 True (实际 {agg.is_good})", failures)
    _assert(subs[0].get("is_good") is True,
            f"sub_reports[0].is_good=True (实际 {subs[0].get('is_good')})", failures)
    db.close()

    # 2.3 新来一路不同 channel（channel=1, 同 source）→ sub_reports=2 条
    _report(box, "A", 1, "10.0.0.1", False)
    db = SessionLocal()
    agg = db.query(BoxAggregation).filter(
        BoxAggregation.box_serial == box, BoxAggregation.station_id == "A"
    ).first()
    subs = (agg.cycle_context or {}).get("sub_reports", [])
    _assert(len(subs) == 2, f"多一路 channel=1 → sub_reports=2 条 (实际 {len(subs)})", failures)
    _assert(agg.is_good is False,
            f"任一路 NG → 整站 is_good=False (实际 {agg.is_good})", failures)
    db.close()

    # 2.4 新来一路不同 source（channel=0, 不同副机）→ sub_reports=3 条
    _report(box, "A", 0, "10.0.0.2", True)
    db = SessionLocal()
    agg = db.query(BoxAggregation).filter(
        BoxAggregation.box_serial == box, BoxAggregation.station_id == "A"
    ).first()
    subs = (agg.cycle_context or {}).get("sub_reports", [])
    _assert(len(subs) == 3, f"不同副机同通道 → sub_reports=3 条 (实际 {len(subs)})", failures)
    # 此时 channel 1 那路还是 NG，整站仍 NG
    _assert(agg.is_good is False, "channel=1 那路仍 NG → 整站 NG", failures)
    db.close()

    # 2.5 把那路 NG 改成 OK → 整站变 OK，sub_reports 仍 3 条
    _report(box, "A", 1, "10.0.0.1", True)
    db = SessionLocal()
    agg = db.query(BoxAggregation).filter(
        BoxAggregation.box_serial == box, BoxAggregation.station_id == "A"
    ).first()
    subs = (agg.cycle_context or {}).get("sub_reports", [])
    _assert(len(subs) == 3,
            f"覆盖后 sub_reports 仍 3 条 (实际 {len(subs)})", failures)
    _assert(agg.is_good is True,
            f"全部路都 OK → 整站 is_good=True (实际 {agg.is_good})", failures)
    # 检查各路 key 唯一
    keys = {(s.get("channel_id"), s.get("source_address")) for s in subs}
    _assert(keys == {(0, "10.0.0.1"), (1, "10.0.0.1"), (0, "10.0.0.2")},
            f"三路 key 都在 (实际 {keys})", failures)
    db.close()

    return failures


# ---------- 用例 3：批量清理 API ----------
def test_cluster_clear_api():
    _print_banner("用例 3: 批量清理 API（older + before_days）")
    failures: list = []

    from backend.api.cluster import clear_boxes, delete_box
    from backend.models.mes_models import BoxAggregation, BoxSummary

    # 清场，方便计数
    db = SessionLocal()
    db.query(BoxAggregation).delete(synchronize_session=False)
    db.query(BoxSummary).delete(synchronize_session=False)
    db.commit()
    db.close()

    # 造 3 条 agg：两条老的 + 一条新的
    now = datetime.utcnow()
    db = SessionLocal()
    db.add(BoxAggregation(
        box_serial="OLD-01", station_id="A", channel_id=0,
        is_good=True, status="received",
        received_at=now - timedelta(days=30),
    ))
    db.add(BoxAggregation(
        box_serial="OLD-02", station_id="A", channel_id=0,
        is_good=True, status="received",
        received_at=now - timedelta(days=10),
    ))
    db.add(BoxAggregation(
        box_serial="NEW-01", station_id="A", channel_id=0,
        is_good=True, status="received",
        received_at=now - timedelta(hours=2),
    ))
    db.add(BoxSummary(
        box_serial="OLD-01", overall_result="OK", status="pushed",
        total_stations=1, completed_stations=1,
    ))
    db.commit()
    db.close()

    # 3.1 scope=older, before_days=14 → 只清 OLD-01 (30天前)
    resp = clear_boxes(scope="older", before_days=14)
    _assert(resp["deleted"] is True, "older 返回 deleted=True", failures)
    db = SessionLocal()
    remain = {b.box_serial for b in db.query(BoxAggregation).all()}
    _assert(remain == {"OLD-02", "NEW-01"},
            f"older(14天) 后剩 OLD-02+NEW-01 (实际 {remain})", failures)
    sum_remain = db.query(BoxSummary).count()
    _assert(sum_remain == 0, "OLD-01 对应 Summary 也被清", failures)
    db.close()

    # 3.2 scope=older, before_days=0 → 应该报错 (>0 必需)
    from fastapi import HTTPException
    try:
        clear_boxes(scope="older", before_days=0)
        failures.append("older before_days=0 应报 400 但没报")
    except HTTPException as e:
        _assert(e.status_code == 400,
                f"older before_days=0 报 HTTP400 (实际 {e.status_code})", failures)

    # 3.3 按箱号删除 NEW-01
    resp = delete_box("NEW-01")
    _assert(resp["deleted"] is True and resp["aggregations"] == 1,
            f"delete_box NEW-01: agg=1 (实际 {resp})", failures)
    db = SessionLocal()
    remain = {b.box_serial for b in db.query(BoxAggregation).all()}
    _assert(remain == {"OLD-02"}, f"剩 OLD-02 (实际 {remain})", failures)
    db.close()

    # 3.4 scope=all → 全清
    resp = clear_boxes(scope="all")
    db = SessionLocal()
    _assert(db.query(BoxAggregation).count() == 0, "all 后 agg=0", failures)
    _assert(db.query(BoxSummary).count() == 0, "all 后 sum=0", failures)
    db.close()

    return failures


# ---------- main ----------
def _run_case(name: str, fn) -> list[str]:
    import traceback
    try:
        return fn()
    except Exception as e:
        print(f"  [CRASH] 用例 {name} 抛异常: {e!r}")
        traceback.print_exc()
        return [f"用例 {name} 崩溃: {e!r}"]


def main():
    all_failures: list[tuple[str, list[str]]] = []
    try:
        all_failures.append(("扫码 OK 冷却",
                             _run_case("扫码 OK 冷却", test_ok_rescan_cooldown)))
        all_failures.append(("集群去重覆盖",
                             _run_case("集群去重覆盖", test_cluster_sub_report_dedup)))
        all_failures.append(("批量清理 API",
                             _run_case("批量清理 API", test_cluster_clear_api)))
    finally:
        # 统计
        print()
        print("=" * 70)
        print("  总结")
        print("=" * 70)
        total_fail = 0
        for name, fs in all_failures:
            if fs:
                print(f"  [FAIL] {name}: {len(fs)} 个不通过")
                for msg in fs:
                    print(f"        - {msg}")
                total_fail += len(fs)
            else:
                print(f"  [OK]   {name}: 全部通过")
        print()
        if total_fail == 0:
            print("  ✅ 所有用例通过")
        else:
            print(f"  ❌ 共 {total_fail} 个断言失败")

        cleanup()
        sys.exit(0 if total_fail == 0 else 1)


if __name__ == "__main__":
    main()
