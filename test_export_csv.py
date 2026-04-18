"""
v2.7.2 export_csv 全路径测试：覆盖全部导出按钮 + 筛选器组合 + 边界冲突。

测试点（对应前端 4 个按钮 + 1 个会话导出按钮）：

  [all 分支 - 对应"导出当日数据" / "导出日期范围"]
  1. 不传过滤：返回全部 session
  2. 只传 project_id
  3. 只传 channel_id
  4. project_id + channel_id 交集
  5. exportAllProjects=true 语义（project_id=None 但 channel_id=指定）
  6. 指定工位无数据 → CSV 返回头部但无会话行
  7. shift 时间过滤 + project_id + channel_id 组合

  [all 分支 - 对应"导出某周数据"]
  8. week=2026-W16 正确解析为周一到周日

  [all 分支 - 对应"导出某月数据"]
  9. month=2026-04 正确解析为整月

  [session 分支 - 对应"导出会话"]
  10. 单会话导出包含项目+工位字段

  [cycle 分支 - 旧路径不破坏]
  11. 单周期导出仍然工作

  [参数向后兼容]
  12. 旧调用不传 project_id/channel_id 返回全部（同用例1）

运行：
    python test_export_csv.py
"""

import os
import sys
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _build_temp_db():
    """搭一个临时内存 SQLite，插入两个项目、两个工位的若干会话/周期/步骤。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import backend.models.models as M

    # 用文件版 sqlite 避免多线程/会话间可见性问题（也用 StaticPool 可以，保持简单）
    tmp_path = os.path.join(ROOT, "_test_export.sqlite3")
    if os.path.exists(tmp_path):
        os.remove(tmp_path)
    engine = create_engine(f"sqlite:///{tmp_path}", connect_args={"check_same_thread": False})
    M.Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    db = SessionLocal()
    try:
        p1 = M.Project(name="项目A")
        p2 = M.Project(name="项目B")
        db.add_all([p1, p2])
        db.flush()

        base = datetime(2026, 4, 17, 9, 0, 0)
        # 4 个 session：(项目A, ch0)、(项目A, ch1)、(项目B, ch0)、(项目B, ch1)
        combos = [
            (p1.id, 0, "uuid_A0"),
            (p1.id, 1, "uuid_A1"),
            (p2.id, 0, "uuid_B0"),
            (p2.id, 1, "uuid_B1"),
        ]
        sess_list = []
        for (pid, ch, uuid) in combos:
            s = M.DetectionSession(
                session_uuid=uuid,
                project_id=pid,
                channel_id=ch,
                start_time=base,
                end_time=base + timedelta(minutes=5),
                total_cycles=2,
                good_cycles=1,
                ng_cycles=1,
                avg_cycle_time=12.34,
                status="completed",
            )
            db.add(s)
            sess_list.append(s)
        db.flush()

        for s in sess_list:
            for i in range(2):
                c = M.DetectionCycle(
                    cycle_uuid=f"{s.session_uuid}_c{i}",
                    session_id=s.id,
                    cycle_number=i + 1,
                    start_time=base + timedelta(minutes=i),
                    end_time=base + timedelta(minutes=i, seconds=10),
                    duration=10.0,
                    is_good=(i == 0),
                    event_name=None if i == 0 else "漏贴",
                    step_sequence=["S1", "S2"],
                )
                db.add(c)
        db.commit()
    finally:
        pass  # 保持 db 开着，下面用得到

    return db, SessionLocal, tmp_path


def _run_export(db, **kwargs):
    """直接调 export_csv 函数，读出 StreamingResponse body，返回 CSV 文本。
    注意：直接调函数时 FastAPI Query(None) 默认值不会被解析为 None，
    所以这里把所有可选参数都显式兜底为 None。"""
    from backend.api.sessions import export_csv
    defaults = dict(
        session_id=None, cycle_id=None,
        date=None, start_date=None, end_date=None,
        week=None, month=None,
        start_hour=None, end_hour=None,
        project_id=None, channel_id=None,
    )
    defaults.update(kwargs)
    resp = export_csv(db=db, **defaults)
    # StreamingResponse.body_iterator 是 async generator
    import asyncio

    async def _collect():
        chunks = []
        async for chunk in resp.body_iterator:
            chunks.append(chunk)
        return b"".join(chunks)

    body = asyncio.run(_collect())
    text = body.decode("utf-8-sig")
    return text


def test_all_no_filter():
    db, _, path = _build_temp_db()
    try:
        csv_text = _run_export(
            db,
            export_type="all",
            start_date="2026-04-17",
            end_date="2026-04-17",
        )
        # 应该含 4 个 session uuid
        for u in ("uuid_A0", "uuid_A1", "uuid_B0", "uuid_B1"):
            assert u in csv_text, f"[FAIL] 无过滤时 CSV 应含 {u}"
        # 元数据行说明这是"全部项目 / 全部工位"
        assert "全部项目" in csv_text, "[FAIL] 元数据应写明'全部项目'"
        assert "全部工位" in csv_text, "[FAIL] 元数据应写明'全部工位'"
        # 会话列表表头应含"工位"列
        assert "会话ID,项目,工位,开始时间" in csv_text, "[FAIL] 会话列表表头未包含'工位'列"
        # 分组标题应形如 [项目名 / 工位N]
        assert "[项目A / 工位1]" in csv_text, "[FAIL] 周期分组标题缺项目A/工位1标识"
        assert "[项目B / 工位2]" in csv_text, "[FAIL] 周期分组标题缺项目B/工位2标识"
        print("[OK] test_all_no_filter 通过")
    finally:
        db.close()
        if os.path.exists(path):
            os.remove(path)


def test_filter_by_project():
    db, _, path = _build_temp_db()
    try:
        csv_text = _run_export(
            db,
            export_type="all",
            start_date="2026-04-17",
            end_date="2026-04-17",
            project_id=_project_id(db, "项目A"),
        )
        assert "uuid_A0" in csv_text and "uuid_A1" in csv_text
        assert "uuid_B0" not in csv_text, "[FAIL] 按项目A过滤时不应包含 B 的会话"
        assert "uuid_B1" not in csv_text, "[FAIL] 按项目A过滤时不应包含 B 的会话"
        assert "项目A" in csv_text, "[FAIL] 元数据应写明'项目A'"
        print("[OK] test_filter_by_project 通过")
    finally:
        db.close()
        if os.path.exists(path):
            os.remove(path)


def test_filter_by_channel():
    db, _, path = _build_temp_db()
    try:
        csv_text = _run_export(
            db,
            export_type="all",
            start_date="2026-04-17",
            end_date="2026-04-17",
            channel_id=1,  # 只要工位 2（channel_id=1）
        )
        assert "uuid_A1" in csv_text and "uuid_B1" in csv_text
        assert "uuid_A0" not in csv_text, "[FAIL] 按工位2过滤时不应含 ch0"
        assert "uuid_B0" not in csv_text, "[FAIL] 按工位2过滤时不应含 ch0"
        assert "工位2" in csv_text, "[FAIL] 元数据应写明'工位2'"
        print("[OK] test_filter_by_channel 通过")
    finally:
        db.close()
        if os.path.exists(path):
            os.remove(path)


def test_filter_by_project_and_channel():
    db, _, path = _build_temp_db()
    try:
        csv_text = _run_export(
            db,
            export_type="all",
            start_date="2026-04-17",
            end_date="2026-04-17",
            project_id=_project_id(db, "项目A"),
            channel_id=0,
        )
        assert "uuid_A0" in csv_text
        for u in ("uuid_A1", "uuid_B0", "uuid_B1"):
            assert u not in csv_text, f"[FAIL] 项目A+工位1 过滤时不应含 {u}"
        print("[OK] test_filter_by_project_and_channel 通过")
    finally:
        db.close()
        if os.path.exists(path):
            os.remove(path)


def _project_id(db, name):
    from backend.models.models import Project
    row = db.query(Project).filter(Project.name == name).first()
    assert row is not None, f"[FAIL] 未找到项目 {name}"
    return row.id


def test_all_projects_with_channel():
    """模拟前端 exportAllProjects=true + channelFilter=工位2：
    应跨项目返回所有工位2 的 session。"""
    db, _, path = _build_temp_db()
    try:
        csv_text = _run_export(
            db,
            export_type="all",
            start_date="2026-04-17",
            end_date="2026-04-17",
            project_id=None,    # 全部项目
            channel_id=1,       # 仅工位2
        )
        assert "uuid_A1" in csv_text and "uuid_B1" in csv_text, "[FAIL] 应含 A/B 项目的工位2"
        assert "uuid_A0" not in csv_text and "uuid_B0" not in csv_text, "[FAIL] 不应含 ch0"
        assert "全部项目" in csv_text, "[FAIL] 元数据应写明'全部项目'"
        assert "工位2" in csv_text, "[FAIL] 元数据应写明'工位2'"
        print("[OK] test_all_projects_with_channel 通过")
    finally:
        db.close()
        if os.path.exists(path):
            os.remove(path)


def test_empty_result_channel():
    """指定一个无数据的工位（ch3=工位4），应返回空会话列表但不崩溃。"""
    db, _, path = _build_temp_db()
    try:
        csv_text = _run_export(
            db,
            export_type="all",
            start_date="2026-04-17",
            end_date="2026-04-17",
            channel_id=3,  # 数据库里没这个工位
        )
        # 元数据行仍应存在
        assert "数据导出报表" in csv_text, "[FAIL] 空结果也应有报表头"
        assert "工位4" in csv_text, "[FAIL] 元数据应写明'工位4'"
        # 任何 session uuid 都不应出现
        for u in ("uuid_A0", "uuid_A1", "uuid_B0", "uuid_B1"):
            assert u not in csv_text, f"[FAIL] 空结果 CSV 不应含 {u}"
        print("[OK] test_empty_result_channel 通过")
    finally:
        db.close()
        if os.path.exists(path):
            os.remove(path)


def test_shift_with_project_and_channel():
    """白班时段（08:00-20:00）+ project + channel 三重过滤。
    测试数据 start_time=09:00 落在白班内，应被命中。"""
    db, _, path = _build_temp_db()
    try:
        csv_text = _run_export(
            db,
            export_type="all",
            date="2026-04-17",
            start_hour="08:00",
            end_hour="20:00",
            project_id=_project_id(db, "项目A"),
            channel_id=0,
        )
        assert "uuid_A0" in csv_text, "[FAIL] shift+项目A+工位1 应含 uuid_A0"
        for u in ("uuid_A1", "uuid_B0", "uuid_B1"):
            assert u not in csv_text, f"[FAIL] shift+项目A+工位1 不应含 {u}"
        print("[OK] test_shift_with_project_and_channel 通过")
    finally:
        db.close()
        if os.path.exists(path):
            os.remove(path)


def test_export_by_week():
    """模拟前端"导出某周数据"按钮：传 week=2026-W16 覆盖 2026-04-13~04-19。"""
    db, _, path = _build_temp_db()
    try:
        csv_text = _run_export(
            db,
            export_type="all",
            week="2026-W16",
            project_id=_project_id(db, "项目A"),
        )
        # 2026-04-17 在 2026-W16 内
        assert "uuid_A0" in csv_text and "uuid_A1" in csv_text, "[FAIL] week=2026-W16 应覆盖 2026-04-17"
        assert "uuid_B0" not in csv_text, "[FAIL] 项目过滤生效，不应含 B"
        print("[OK] test_export_by_week 通过")
    finally:
        db.close()
        if os.path.exists(path):
            os.remove(path)


def test_export_by_month():
    """模拟前端"导出某月数据"按钮：传 month=2026-04 覆盖整个 4 月。"""
    db, _, path = _build_temp_db()
    try:
        csv_text = _run_export(
            db,
            export_type="all",
            month="2026-04",
            channel_id=0,  # 仅工位1
        )
        assert "uuid_A0" in csv_text and "uuid_B0" in csv_text, "[FAIL] 跨项目工位1 都应含"
        assert "uuid_A1" not in csv_text and "uuid_B1" not in csv_text, "[FAIL] 工位过滤应排除 ch1"
        print("[OK] test_export_by_month 通过")
    finally:
        db.close()
        if os.path.exists(path):
            os.remove(path)


def test_export_type_session():
    """模拟"导出会话"按钮：export_type=session + session_id。
    新版会话信息应包含项目名和工位号。"""
    db, _, path = _build_temp_db()
    try:
        from backend.models.models import DetectionSession
        sess = db.query(DetectionSession).filter_by(session_uuid="uuid_A1").first()
        assert sess is not None
        csv_text = _run_export(
            db,
            export_type="session",
            session_id=sess.id,
        )
        # 会话信息表头要含"项目"和"工位"列
        assert "会话ID,项目,工位,开始时间" in csv_text, \
            f"[FAIL] 会话信息表头未包含项目/工位列\n---\n{csv_text[:500]}"
        assert "项目A" in csv_text, "[FAIL] 单会话 CSV 应写出项目名"
        assert "工位2" in csv_text, "[FAIL] 单会话 CSV 应写出工位号(channel_id=1 → 工位2)"
        # 即使传了 project_id/channel_id 也不应影响 session 分支
        csv_text2 = _run_export(
            db,
            export_type="session",
            session_id=sess.id,
            project_id=999999,  # 故意给不存在的 project，验证 session 分支不过滤
            channel_id=999,
        )
        assert "uuid_A1" in csv_text2, \
            "[FAIL] session 分支不应被 project_id/channel_id 过滤覆盖"
        print("[OK] test_export_type_session 通过")
    finally:
        db.close()
        if os.path.exists(path):
            os.remove(path)


def test_export_type_cycle():
    """模拟单周期导出：export_type=cycle + cycle_id。
    不测新字段（cycle 分支未加项目/工位字段），只验证不崩溃。"""
    db, _, path = _build_temp_db()
    try:
        from backend.models.models import DetectionCycle
        cyc = db.query(DetectionCycle).filter_by(cycle_uuid="uuid_A0_c0").first()
        assert cyc is not None
        csv_text = _run_export(
            db,
            export_type="cycle",
            cycle_id=cyc.id,
        )
        assert "周期信息" in csv_text, "[FAIL] cycle 分支应输出'周期信息'"
        assert "uuid_A0_c0" in csv_text, "[FAIL] cycle CSV 应含 cycle_uuid"
        print("[OK] test_export_type_cycle 通过")
    finally:
        db.close()
        if os.path.exists(path):
            os.remove(path)


def test_backward_compat_no_new_params():
    """旧调用不传 project_id/channel_id，行为应与用例1等价（全部返回）。"""
    db, _, path = _build_temp_db()
    try:
        csv_text = _run_export(
            db,
            export_type="all",
            start_date="2026-04-17",
            end_date="2026-04-17",
            # 不传 project_id / channel_id
        )
        for u in ("uuid_A0", "uuid_A1", "uuid_B0", "uuid_B1"):
            assert u in csv_text, f"[FAIL] 向后兼容模式应含 {u}"
        print("[OK] test_backward_compat_no_new_params 通过")
    finally:
        db.close()
        if os.path.exists(path):
            os.remove(path)


def test_filename_scope_semantics():
    """前端 buildExportFilenameScope() 的纯函数语义模拟，避免 Node 环境。
    这里只是规范化文件名模式，确保后端+前端约定一致。"""
    cases = [
        # (exportAllProjects, currentProjectId, channelFilter, expected_scope)
        (False, 5, None,  "proj5_chALL"),
        (False, 5, 0,     "proj5_ch1"),
        (False, 5, 2,     "proj5_ch3"),
        (True,  5, None,  "projALL_chALL"),
        (True,  5, 1,     "projALL_ch2"),
        (False, None, 0,  "projNA_ch1"),
    ]

    def _build(all_projects, pid, ch_filter):
        proj_seg = "projALL" if all_projects else (f"proj{pid}" if pid else "projNA")
        ch_seg = "chALL" if ch_filter is None else f"ch{ch_filter + 1}"
        return f"{proj_seg}_{ch_seg}"

    for (ap, pid, ch, expected) in cases:
        got = _build(ap, pid, ch)
        assert got == expected, f"[FAIL] 文件名 scope 不符: ({ap},{pid},{ch}) → {got}, 期望 {expected}"
    print("[OK] test_filename_scope_semantics 通过")


if __name__ == "__main__":
    # --- 基础过滤（对应"导出当日 / 日期范围"按钮） ---
    test_all_no_filter()
    test_filter_by_project()
    test_filter_by_channel()
    test_filter_by_project_and_channel()
    test_all_projects_with_channel()
    test_empty_result_channel()
    test_shift_with_project_and_channel()

    # --- 周/月路径（对应"导出某周 / 某月"按钮） ---
    test_export_by_week()
    test_export_by_month()

    # --- 单会话 / 单周期（对应"导出会话"按钮） ---
    test_export_type_session()
    test_export_type_cycle()

    # --- 向后兼容 & 前端文件名契约 ---
    test_backward_compat_no_new_params()
    test_filename_scope_semantics()

    print("\n全部通过 ✅")
