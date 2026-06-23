"""
A5 回归测试: 导出文件清理（三层）。

客户诉求(2026-06): 即使客户改了导出地址也要能清(客户自己准备导出地址);
最好数据中心能配置导出位置, 这样不会错误清理。

定稿三层:
  层1(零风险): 按导出台账 ExportRunLog 精准清 —— 无论地址改到哪, 只删自己产出、
               过期、且确认非活跃(无近期台账引用 + 文件 mtime 也过期)的导出文件。
  层2: 数据中心登记"导出根目录"配置 + 黑名单校验(禁盘根/系统目录)。
  层3(默认关): 对登记目录托底扫描, 仅删导出格式扩展名 + mtime 过期文件, 不递归不删目录。

先红后绿: 把层1 的 os.remove 短路, 旧导出文件不删 → 断言 FAIL; 恢复后 PASS。
"""
import os
from datetime import datetime, timedelta


def _set_mtime(path, days_ago):
    ts = (datetime.now() - timedelta(days=days_ago)).timestamp()
    os.utime(path, (ts, ts))


def _reset(db):
    from backend.models.export_models import ExportRunLog
    from backend.api.sessions_maintenance import _set_system_config
    db.query(ExportRunLog).delete(synchronize_session=False)
    _set_system_config(db, "retention_days", "30", "")
    _set_system_config(db, "auto_cleanup", "true", "")
    _set_system_config(db, "export_retention_days", "0", "")
    _set_system_config(db, "export_cleanup_scan_dir", "false", "")
    _set_system_config(db, "export_cleanup_dir", "", "")
    db.commit()


def test_a5_layer1_ledger_deletes_old_export_files(tmp_path):
    from backend.db.database import SessionLocal
    from backend.models.export_models import ExportRunLog
    from backend.api.sessions_maintenance import _perform_auto_cleanup

    old_file = tmp_path / "old_export.csv"
    new_file = tmp_path / "new_export.csv"
    old_file.write_text("old"); new_file.write_text("new")
    _set_mtime(str(old_file), 40)   # 文件本身也过期
    _set_mtime(str(new_file), 1)

    db = SessionLocal()
    try:
        _reset(db)
        db.add(ExportRunLog(source_type="realtime", status="success",
                            output_file=str(old_file),
                            triggered_at=datetime.now() - timedelta(days=40)))
        db.add(ExportRunLog(source_type="realtime", status="success",
                            output_file=str(new_file),
                            triggered_at=datetime.now() - timedelta(days=1)))
        db.commit()
    finally:
        db.close()

    _perform_auto_cleanup()

    assert not old_file.exists(), "过期导出文件未被清理"
    assert new_file.exists(), "近期导出文件被误删"
    db = SessionLocal()
    try:
        files = {r.output_file for r in db.query(ExportRunLog).all()}
        assert str(old_file) not in files, "过期台账未清"
        assert str(new_file) in files, "近期台账被误删"
    finally:
        _reset(db); db.close()


def test_a5_layer1_overwrite_active_file_protected(tmp_path):
    """overwrite 同名覆盖: 旧台账过期但文件仍被近期台账引用且 mtime 新 → 文件必须保留。"""
    from backend.db.database import SessionLocal
    from backend.models.export_models import ExportRunLog
    from backend.api.sessions_maintenance import _perform_auto_cleanup

    active_file = tmp_path / "fixed_name.txt"
    active_file.write_text("latest")
    _set_mtime(str(active_file), 0)  # 刚更新

    db = SessionLocal()
    try:
        _reset(db)
        # 旧台账(过期) + 新台账(近期) 都指向同一文件
        db.add(ExportRunLog(source_type="realtime", status="success",
                            output_file=str(active_file),
                            triggered_at=datetime.now() - timedelta(days=40)))
        db.add(ExportRunLog(source_type="realtime", status="success",
                            output_file=str(active_file),
                            triggered_at=datetime.now() - timedelta(days=1)))
        db.commit()
    finally:
        db.close()

    _perform_auto_cleanup()

    assert active_file.exists(), "活跃(overwrite同名)文件被误删"
    db = SessionLocal()
    try:
        cnt = db.query(ExportRunLog).filter(
            ExportRunLog.output_file == str(active_file)).count()
        assert cnt == 1, "应只保留近期台账, 旧台账应清"
    finally:
        _reset(db); db.close()


def test_a5_layer3_scan_dir_extension_and_mtime_guard(tmp_path):
    """层3 托底: 只删导出格式 + mtime 过期; 非导出格式 / 近期文件一律保留。"""
    from backend.db.database import SessionLocal
    from backend.api.sessions_maintenance import _perform_auto_cleanup, _set_system_config

    scan = tmp_path / "export_root"
    scan.mkdir()
    old_csv = scan / "old.csv"; old_csv.write_text("x"); _set_mtime(str(old_csv), 40)
    old_jpg = scan / "photo.jpg"; old_jpg.write_text("x"); _set_mtime(str(old_jpg), 40)
    new_csv = scan / "today.csv"; new_csv.write_text("x"); _set_mtime(str(new_csv), 1)

    db = SessionLocal()
    try:
        _reset(db)
        _set_system_config(db, "export_cleanup_scan_dir", "true", "")
        _set_system_config(db, "export_cleanup_dir", str(scan), "")
        db.commit()
    finally:
        db.close()

    _perform_auto_cleanup()

    assert not old_csv.exists(), "过期导出格式文件未被托底清理"
    assert old_jpg.exists(), "非导出格式文件被误删(护栏失效)"
    assert new_csv.exists(), "近期文件被误删"
    db = SessionLocal()
    try:
        _reset(db)
    finally:
        db.close()


def test_a5_layer2_blacklist_rejects_dangerous_dir():
    """数据中心登记导出目录时, 盘根/系统目录必须被拒绝。"""
    from backend.api.sessions_maintenance import _norm_cleanup_dir, _CLEANUP_DIR_BLACKLIST
    for danger in ["/", "/home", "/etc", "/usr", "C:\\", "C:\\Windows", "D:\\"]:
        assert _norm_cleanup_dir(danger) in _CLEANUP_DIR_BLACKLIST, f"危险目录未被黑名单覆盖: {danger}"
    # 正常导出目录不在黑名单
    assert _norm_cleanup_dir("/data/exports") not in _CLEANUP_DIR_BLACKLIST
    assert _norm_cleanup_dir("D:\\客户导出") not in _CLEANUP_DIR_BLACKLIST
