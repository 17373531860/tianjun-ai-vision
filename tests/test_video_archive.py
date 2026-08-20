"""录像归档规则引擎 (v3.53 一期) 单元测试。

覆盖：
  1. 目录护栏: 盘根/系统目录/相对路径/录像目录自身 一律拒绝
  2. 规则匹配: 结果筛选 (ng_only/ok_only/all) x 通道过滤 x 项目过滤
  3. 归档执行: 文件名模板渲染 (字段中央仓库上下文) + .tmp 原子落位 + 台账
  4. 重名策略: rename 追加序号 / overwrite 覆盖 / skip 跳过
  5. spool 落盘 + 重放 (断点续传语义)
  6. API CRUD 往返 + 护栏 400 + test-run 端点
  7. 零开销守门: 无启用规则时 notify 不入队
"""
import os
import time
from datetime import datetime

import pytest

from backend.db.database import SessionLocal
from backend.models.models import DetectionCycle, DetectionSession
from backend.models.archive_models import VideoArchiveLog, VideoArchiveRule
from backend.services import video_archive as va


# ============================================================
# 工具
# ============================================================

def _mk_cycle(db, tmp_path, *, is_good=False, channel_id=0, suffix=""):
    """造一个带真实录像文件的 (session, cycle)。"""
    now = datetime.now()
    sess = DetectionSession(
        session_uuid=f"va-s-{time.time_ns()}{suffix}",
        start_time=now, channel_id=channel_id,
    )
    db.add(sess)
    db.flush()
    src = os.path.join(str(tmp_path), f"cycle_src_{time.time_ns()}{suffix}.mp4")
    with open(src, "wb") as f:
        f.write(b"FAKE_MP4_CONTENT" * 64)
    cyc = DetectionCycle(
        cycle_uuid=f"va-c-{time.time_ns()}{suffix}",
        session_id=sess.id,
        start_time=now, end_time=now,
        is_good=is_good,
        video_path=src,
    )
    db.add(cyc)
    db.commit()
    db.refresh(cyc)
    return cyc, src


def _mk_rule(db, dest_dir, **kw):
    defaults = dict(
        name="测试归档规则", enabled=True, result_filter="all",
        dest_dir=str(dest_dir), subdir_by_date=False,
        filename_template="{{ cycle.id }}_{{ 'OK' if cycle.is_good else 'NG' }}.mp4",
        overwrite_policy="rename",
    )
    defaults.update(kw)
    r = VideoArchiveRule(**defaults)
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


@pytest.fixture()
def db():
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _clean_rules_and_spool():
    """每个用例前后清掉规则/台账/spool, 防串扰。"""
    def _wipe():
        s = SessionLocal()
        try:
            s.query(VideoArchiveRule).delete()
            s.query(VideoArchiveLog).delete()
            s.commit()
        finally:
            s.close()
        try:
            if os.path.exists(va._SPOOL_FILE):
                os.remove(va._SPOOL_FILE)
        except OSError:
            pass
        va.refresh_rules_cache()
    _wipe()
    yield
    va.stop_worker(timeout=2)
    _wipe()


# ============================================================
# 1. 目录护栏
# ============================================================

class TestDestDirGuard:
    def test_reject_empty(self):
        assert va.validate_dest_dir("") is not None
        assert va.validate_dest_dir("   ") is not None

    def test_reject_relative(self):
        assert "绝对路径" in va.validate_dest_dir("foo/bar")

    def test_reject_blacklist_roots(self):
        for bad in ["/", "/etc", "/tmp", "C:\\Windows", "c:\\users", "D:"]:
            assert va.validate_dest_dir(bad) is not None, bad

    def test_reject_recordings_self_copy(self):
        rec = os.path.join(va.DATA_DIR, "recordings", "cycles")
        assert "自拷贝" in va.validate_dest_dir(rec)

    def test_accept_normal_abs_dir(self, tmp_path):
        assert va.validate_dest_dir(str(tmp_path)) is None


# ============================================================
# 2. 规则匹配
# ============================================================

class TestRuleMatch:
    def _rule(self, **kw):
        r = VideoArchiveRule(name="m", dest_dir="/x", **kw)
        return r

    def _cycle(self, is_good):
        c = DetectionCycle(cycle_uuid="x", session_id=1,
                           start_time=datetime.now(), is_good=is_good)
        return c

    def test_result_filter(self):
        ng_rule = self._rule(result_filter="ng_only")
        ok_rule = self._rule(result_filter="ok_only")
        all_rule = self._rule(result_filter="all")
        ng_cycle, ok_cycle = self._cycle(False), self._cycle(True)
        assert va._match_rule(ng_rule, ng_cycle, None, None)
        assert not va._match_rule(ng_rule, ok_cycle, None, None)
        assert va._match_rule(ok_rule, ok_cycle, None, None)
        assert not va._match_rule(ok_rule, ng_cycle, None, None)
        assert va._match_rule(all_rule, ng_cycle, None, None)
        assert va._match_rule(all_rule, ok_cycle, None, None)

    def test_channel_and_project_filter(self):
        r = self._rule(result_filter="all", channel_filter=[1, 2],
                       project_filter=[7])
        c = self._cycle(True)
        assert va._match_rule(r, c, 1, 7)
        assert not va._match_rule(r, c, 0, 7)
        assert not va._match_rule(r, c, 1, 8)
        # 过滤维度未知 (None) 时不拦 — 宁可多归档不漏证据
        assert va._match_rule(r, c, None, None)


# ============================================================
# 3. 归档执行 + 文件名渲染 + 台账
# ============================================================

class TestArchiveExecution:
    def test_archive_success_with_rendered_name(self, db, tmp_path):
        cyc, src = _mk_cycle(db, tmp_path, is_good=False)
        dest = tmp_path / "dest"
        rule = _mk_rule(db, dest)
        results = va.archive_cycle_now(cyc.id)
        assert len(results) == 1
        assert results[0]["status"] == "success"
        expect = dest / f"{cyc.id}_NG.mp4"
        assert expect.exists()
        # 内容一致 (真拷贝不是空壳)
        assert expect.read_bytes() == open(src, "rb").read()
        # 无 .tmp 残留
        assert not [p for p in os.listdir(dest) if ".tmp." in p]
        # 台账
        logs = db.query(VideoArchiveLog).filter(
            VideoArchiveLog.cycle_id == cyc.id).all()
        assert len(logs) == 1 and logs[0].status == "success"
        assert logs[0].dest_path == str(expect)
        # 规则计数
        db.refresh(rule)
        assert rule.success_count == 1
        assert rule.last_run_status == "success"

    def test_subdir_by_date(self, db, tmp_path):
        cyc, _ = _mk_cycle(db, tmp_path, is_good=True)
        dest = tmp_path / "dated"
        _mk_rule(db, dest, subdir_by_date=True)
        results = va.archive_cycle_now(cyc.id)
        assert results[0]["status"] == "success"
        day = datetime.now().strftime("%Y-%m-%d")
        assert (dest / day / f"{cyc.id}_OK.mp4").exists()

    def test_result_filter_skips_unmatched(self, db, tmp_path):
        cyc, _ = _mk_cycle(db, tmp_path, is_good=True)  # OK 周期
        dest = tmp_path / "ng_only_dest"
        _mk_rule(db, dest, result_filter="ng_only")
        results = va.archive_cycle_now(cyc.id)
        # enforce_filters=True (未指定 rule_id): OK 周期不命中 ng_only 规则
        assert results == []
        assert not dest.exists() or not os.listdir(dest)

    def test_bad_template_is_permanent_failure(self, db, tmp_path):
        cyc, _ = _mk_cycle(db, tmp_path)
        rule = _mk_rule(db, tmp_path / "d",
                        filename_template="{{ nonexistent.attr.deep }}.mp4")
        results = va.archive_cycle_now(cyc.id)
        # Jinja2 未定义变量渲染为空/报错 → 不能是 transient (不重试)
        assert all(not r.get("transient") for r in results)

    def test_guard_rejected_dest_fails_permanently(self, db, tmp_path):
        cyc, _ = _mk_cycle(db, tmp_path)
        rule = _mk_rule(db, tmp_path)
        # 绕过 API 校验直接把库里目录改成黑名单 (模拟旧数据/手改库)
        rule.dest_dir = "/etc"
        db.commit()
        results = va.archive_cycle_now(cyc.id)
        assert results[0]["status"] == "failed"
        assert not results[0]["transient"]
        assert "系统" in (results[0]["error"] or "")


# ============================================================
# 4. 重名策略
# ============================================================

class TestOverwritePolicy:
    def _run_twice(self, db, tmp_path, policy):
        cyc, _ = _mk_cycle(db, tmp_path)
        dest = tmp_path / f"dest_{policy}"
        _mk_rule(db, dest, overwrite_policy=policy,
                 filename_template="fixed_name.mp4")
        r1 = va.archive_cycle_now(cyc.id)
        r2 = va.archive_cycle_now(cyc.id)
        return dest, r1, r2

    def test_rename_appends_suffix(self, db, tmp_path):
        dest, r1, r2 = self._run_twice(db, tmp_path, "rename")
        assert r1[0]["status"] == "success" and r2[0]["status"] == "success"
        assert (dest / "fixed_name.mp4").exists()
        assert (dest / "fixed_name_1.mp4").exists()

    def test_overwrite_keeps_single(self, db, tmp_path):
        dest, r1, r2 = self._run_twice(db, tmp_path, "overwrite")
        assert r2[0]["status"] == "success"
        assert os.listdir(dest) == ["fixed_name.mp4"]

    def test_skip_second_run(self, db, tmp_path):
        dest, r1, r2 = self._run_twice(db, tmp_path, "skip")
        assert r1[0]["status"] == "success"
        assert r2[0]["status"] == "skipped"
        assert os.listdir(dest) == ["fixed_name.mp4"]


# ============================================================
# 5. spool 落盘 + 重放
# ============================================================

class TestSpool:
    def test_spool_roundtrip(self):
        t1 = {"kind": "cycle", "cycle_uuid": "u1", "filepath": "/a.mp4",
              "channel_id": 0, "attempts": 1}
        t2 = {"kind": "cycle", "cycle_uuid": "u2", "filepath": "/b.mp4",
              "channel_id": 1, "attempts": 2}
        va._spool_append(t1)
        va._spool_append(t2)
        assert va._spool_depth() == 2
        tasks = va._spool_take_all()
        assert [t["cycle_uuid"] for t in tasks] == ["u1", "u2"]
        assert va._spool_depth() == 0  # 取空后文件删除

    def test_spool_skips_corrupt_lines(self):
        va._spool_append({"kind": "cycle", "cycle_uuid": "good",
                          "filepath": "/a.mp4", "attempts": 0})
        with open(va._SPOOL_FILE, "a", encoding="utf-8") as f:
            f.write("NOT_JSON{{{\n")
        tasks = va._spool_take_all()
        assert len(tasks) == 1 and tasks[0]["cycle_uuid"] == "good"

    def test_transient_failure_goes_to_spool_then_succeeds(self, db, tmp_path):
        """目标目录不可写 → 任务进 spool; 修复后重放成功 (断点续传语义)。"""
        cyc, src = _mk_cycle(db, tmp_path, is_good=False)
        dest = tmp_path / "flaky_dest"
        dest.mkdir()
        _mk_rule(db, dest)
        va.refresh_rules_cache()

        # 制造瞬态故障: 目录只读
        os.chmod(dest, 0o500)
        try:
            task = {"kind": "cycle", "cycle_uuid": cyc.cycle_uuid,
                    "filepath": src, "channel_id": 0, "attempts": 0}
            va._run_task(task)
            assert va._spool_depth() == 1, "瞬态失败应落 spool"
        finally:
            os.chmod(dest, 0o700)

        # 目录恢复可写 → 重放成功
        for task in va._spool_take_all():
            va._run_task(task)
        assert va._spool_depth() == 0
        assert (dest / f"{cyc.id}_NG.mp4").exists()

    def test_max_attempts_drops_task(self, db, tmp_path):
        cyc, src = _mk_cycle(db, tmp_path)
        _mk_rule(db, tmp_path / "d2")
        va.refresh_rules_cache()
        os.remove(src)  # 源文件没了且 attempts>0 → 永久失败记账
        task = {"kind": "cycle", "cycle_uuid": cyc.cycle_uuid,
                "filepath": src, "channel_id": 0,
                "attempts": va._MAX_ATTEMPTS - 1, "cycle_db_id": cyc.id}
        va._run_task(task)
        assert va._spool_depth() == 0  # 不再回 spool
        logs = SessionLocal().query(VideoArchiveLog).filter(
            VideoArchiveLog.status == "failed").all()
        assert any("不存在" in (l.error or "") for l in logs)


# ============================================================
# 6. 零开销守门
# ============================================================

class TestZeroOverheadGate:
    def test_notify_short_circuits_without_rules(self):
        va.refresh_rules_cache()  # 规则已被 fixture 清空
        assert va._rules_active is False
        before = va._task_queue.qsize()
        va.notify_cycle_video_ready(0, "some-uuid", "/nonexistent.mp4")
        assert va._task_queue.qsize() == before

    def test_notify_enqueues_with_rules(self, db, tmp_path):
        _mk_rule(db, tmp_path)
        va.refresh_rules_cache()
        assert va._rules_active is True
        va.stop_worker(timeout=2)  # 停 worker 防止队列被立刻消费
        va.notify_cycle_video_ready(0, "some-uuid", "/nonexistent.mp4")
        # 入队成功 (可能已被残存 worker 取走一格, 允许 qsize>=0 但 enqueued 计数必增)
        assert va._stats["enqueued"] >= 1


# ============================================================
# 7. API CRUD + 护栏 + test-run
# ============================================================

class TestArchiveApi:
    def test_crud_roundtrip(self, client, tmp_path):
        payload = {
            "name": "API规则", "enabled": True, "result_filter": "ng_only",
            "dest_dir": str(tmp_path / "api_dest"),
            "filename_template": "{{ cycle.id }}.mp4",
            "overwrite_policy": "rename", "subdir_by_date": False,
        }
        r = client.post("/api/v1/export/video-archive/rules", json=payload)
        assert r.status_code == 200, r.text
        rid = r.json()["id"]

        r = client.get("/api/v1/export/video-archive/rules")
        assert any(x["id"] == rid for x in r.json()["items"])

        r = client.put(f"/api/v1/export/video-archive/rules/{rid}",
                       json={"result_filter": "all"})
        assert r.json()["result_filter"] == "all"

        r = client.post(f"/api/v1/export/video-archive/rules/{rid}/toggle")
        assert r.json()["enabled"] is False

        r = client.get("/api/v1/export/video-archive/status")
        assert r.status_code == 200
        assert r.json()["rules_total"] >= 1

        r = client.delete(f"/api/v1/export/video-archive/rules/{rid}")
        assert r.json()["ok"] is True

    def test_create_rejects_blacklist_dir(self, client):
        r = client.post("/api/v1/export/video-archive/rules", json={
            "name": "坏目录", "dest_dir": "/etc",
        })
        assert r.status_code == 400
        assert "禁止" in r.json()["detail"]

    def test_create_rejects_relative_dir(self, client):
        r = client.post("/api/v1/export/video-archive/rules", json={
            "name": "相对路径", "dest_dir": "foo/bar",
        })
        assert r.status_code == 400

    def test_test_run_endpoint(self, client, db, tmp_path):
        cyc, _ = _mk_cycle(db, tmp_path, is_good=False)
        dest = tmp_path / "testrun_dest"
        _mk_rule(db, dest)
        r = client.post("/api/v1/export/video-archive/test-run",
                        json={"cycle_id": cyc.id})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["cycle_id"] == cyc.id
        assert body["results"][0]["status"] == "success"
        assert (dest / f"{cyc.id}_NG.mp4").exists()
        # 台账端点能查到
        r = client.get("/api/v1/export/video-archive/logs",
                       params={"status": "success"})
        assert any(x["cycle_id"] == cyc.id for x in r.json()["items"])
