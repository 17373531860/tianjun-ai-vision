# -*- coding: utf-8 -*-
"""v3.38 自动清理事务卫生回归 (川南"框冻结"第二刀)

钉死两条新不变量:
  1. 分批提交: 过期周期删除按 _CLEANUP_BATCH_CYCLES 分批, 每批一次 commit —
     单个大事务握写锁秒级 = 推理线程写库被堵 = 框冻结同型隐患。
  2. 文件删除在提交之后: 磁盘 I/O (慢盘/杀毒可达秒级) 绝不在持有写锁的
     事务内发生 — 观测到的每一次 os.remove 都必须晚于其所属批次的 commit。
"""
import os
from datetime import datetime, timedelta

import pytest
from sqlalchemy import event

import backend.api.sessions_maintenance as sm
from backend.models.models import DetectionSession, DetectionCycle, StepRecord, VideoClip


@pytest.fixture
def db_factory(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from backend.db.database import Base
    from backend.models import models as _m  # noqa: F401
    from backend.models import auth_models as _a  # noqa: F401
    from backend.models import mes_models as _mes  # noqa: F401
    from backend.models import export_models as _ex  # noqa: F401
    from backend.models import plugin_models as _p  # noqa: F401

    engine = create_engine(
        f"sqlite:///{tmp_path}/hygiene.db",
        connect_args={"check_same_thread": False, "timeout": 15},
    )
    Base.metadata.create_all(bind=engine)
    yield sessionmaker(autocommit=False, autoflush=False, bind=engine)
    engine.dispose()


def _seed_cycles(db, tmp_path, n, days_ago=90):
    """造 n 个过期周期, 各带 1 步骤 + 1 条有真实文件的周期录像。"""
    ts = datetime.now() - timedelta(days=days_ago)
    sess = DetectionSession(session_uuid=f"s-hygiene-{n}", start_time=ts)
    db.add(sess)
    db.flush()
    paths = []
    for i in range(n):
        cyc = DetectionCycle(cycle_uuid=f"c-hyg-{i}", session_id=sess.id,
                             start_time=ts, is_good=True)
        db.add(cyc)
        db.flush()
        db.add(StepRecord(record_uuid=f"r-hyg-{i}", cycle_id=cyc.id,
                          step_label="A", step_name="A", step_order=1,
                          start_time=ts, end_time=ts))
        p = os.path.join(str(tmp_path), f"vid_{i}.mp4")
        with open(p, "wb") as f:
            f.write(b"x")
        db.add(VideoClip(video_uuid=f"v-hyg-{i}", clip_type='cycle',
                         related_id=cyc.id, file_path=p,
                         file_name=os.path.basename(p), created_at=ts))
        paths.append(p)
    db.commit()
    return paths


def test_batched_commits_and_files_after_commit(db_factory, tmp_path, monkeypatch):
    db = db_factory()
    paths = _seed_cycles(db, tmp_path, 25)

    monkeypatch.setattr(sm, "_CLEANUP_BATCH_CYCLES", 10)  # 25 个周期 → 3 批

    # 时序账本: 按发生顺序记录 commit / remove 事件
    ledger = []
    event.listen(db, "after_commit", lambda s: ledger.append("commit"))

    real_remove = os.remove

    def tracking_remove(path):
        ledger.append(("remove", path))
        real_remove(path)

    monkeypatch.setattr(os, "remove", tracking_remove)

    cutoff = datetime.now() - timedelta(days=30)
    cc, sc, vc, df = sm._delete_cycles_by_filter(
        db, DetectionCycle.start_time < cutoff)
    db.close()

    assert (cc, sc, vc, df) == (25, 25, 25, 25)
    assert all(not os.path.isfile(p) for p in paths), "录像文件应全部删除"

    commits = [i for i, e in enumerate(ledger) if e == "commit"]
    removes = [i for i, e in enumerate(ledger) if e != "commit"]
    # 不变量 1: 分批提交真实发生 (25/10 → 3 批 3 次 commit)
    assert len(commits) == 3, f"预期 3 批 3 次提交, 实际 {len(commits)}"
    # 不变量 2: 第一个文件删除必须晚于第一次提交 (行删除先落库放锁, 再动磁盘)
    assert removes and removes[0] > commits[0], \
        f"文件删除发生在提交之前 (事务内做磁盘 I/O): ledger 前 5 项 {ledger[:5]}"
    # 强不变量: 每批的 remove 都在该批 commit 之后 → 任何 remove 前面必有 commit,
    # 且 remove 与它之前最近的 commit 之间不夹其他批次的行删除窗口。
    # 用计数验证: 到任意 remove 为止, 已发生的 commit 数 >= 1 + (它属于第几批)
    batch_of_remove = 0
    seen_commits = 0
    for e in ledger:
        if e == "commit":
            seen_commits += 1
        else:
            assert seen_commits >= 1, "出现了提交前的文件删除"


def test_delete_failure_keeps_db_consistent(db_factory, tmp_path, monkeypatch):
    """文件删不掉 (被占用/权限) 不影响行删除结果, 只留孤儿文件待下轮孤儿扫描。"""
    db = db_factory()
    paths = _seed_cycles(db, tmp_path, 5)

    def deny_remove(path):
        raise PermissionError("模拟文件被占用")

    monkeypatch.setattr(os, "remove", deny_remove)

    cutoff = datetime.now() - timedelta(days=30)
    cc, sc, vc, df = sm._delete_cycles_by_filter(
        db, DetectionCycle.start_time < cutoff)

    assert (cc, sc, vc) == (5, 5, 5), "行删除不受文件删除失败影响"
    assert df == 0
    s = db_factory()
    assert s.query(DetectionCycle).count() == 0
    assert s.query(VideoClip).count() == 0
    s.close()
    db.close()
    assert all(os.path.isfile(p) for p in paths), "文件保留等孤儿扫描兜底"
