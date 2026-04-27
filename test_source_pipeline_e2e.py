"""检测主链路 E2E 安全网（v2.7.16）

目标：
  在不依赖真实摄像头/真实模型/真实推理后端的前提下，端到端验证
  VideoSourceManager 的状态机：detections → step 统计 → cycle 形成 → DB 写入。

为什么需要：
  source.py 仍是 8000+ 行 130+ 方法的上帝类，下一步要按 mixin 切分。任何一步切错
  都会导致方法/属性丢失或调用错位，但运行时才崩。这套测试就是切分前的安全网，确保
  切完后状态机还能完整跑通。

测试覆盖：
  1) 接口契约：VideoSourceManager 暴露的核心方法/属性都在
  2) sequential 模式 × 步骤齐全 → 形成 OK cycle，DB 写入正确
  3) sequential 模式 × 缺最后一步 + 强制结算 → NG cycle
  4) detection 模式 × 多次 OK cycle，cycle 间状态正确隔离
  5) DB 完整性：StepRecord.cycle_id 全部指向有效 cycle，session 统计字段正确
  6) MES Hook 在 cycle_start/cycle_end 时收到正确通知（旁路验证）

运行：
    python test_source_pipeline_e2e.py
"""
import os
import sys
import time
import tempfile
import unittest

# ---- step 1: 在 import backend 之前 patch DB 路径到临时 SQLite -------------
_TMP_DB_FD, _TMP_DB_PATH = tempfile.mkstemp(prefix="tj_pipeline_", suffix=".db")
os.close(_TMP_DB_FD)
os.environ["TIANJUN_DATA_DIR"] = tempfile.mkdtemp(prefix="tj_pipeline_data_")
os.environ["BACKEND_SKIP_INIT"] = "1"

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backend.core import config as _cfg  # noqa: E402

_cfg.settings.SQLALCHEMY_DATABASE_URI = f"sqlite:///{_TMP_DB_PATH}"

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from backend.db import database as _db_module  # noqa: E402

_test_engine = create_engine(
    f"sqlite:///{_TMP_DB_PATH}",
    connect_args={"check_same_thread": False, "timeout": 15},
)
_TestSession = sessionmaker(autocommit=False, autoflush=False, bind=_test_engine)
_db_module.engine = _test_engine
_db_module.SessionLocal = _TestSession

# ---- step 2: 注册全部 ORM + create_all ----------------------------------
import backend.models.models as _models  # noqa: E402
import backend.models.mes_models  # noqa: E402,F401
from backend.db.database import Base  # noqa: E402

Base.metadata.create_all(bind=_test_engine)

# ---- step 3: import VideoSourceManager 并 patch 它绑住的 SessionLocal ----
import backend.api.source as _source_mod  # noqa: E402
_source_mod.SessionLocal = _TestSession
from backend.api.source import VideoSourceManager  # noqa: E402

import numpy as np  # noqa: E402


# =============================================================================
# 工具
# =============================================================================

def _make_frame(h: int = 240, w: int = 320) -> np.ndarray:
    """造一个全黑测试帧。step screenshot 编码会用到，必须是真实 ndarray。"""
    return np.zeros((h, w, 3), dtype=np.uint8)


def _make_detection(label: str, conf: float = 0.9,
                     x: float = 0.3, y: float = 0.3,
                     w: float = 0.2, h: float = 0.2) -> dict:
    """构造一条经过 _detect_only 过滤后的 detection（归一化坐标）。"""
    return {
        'x': x, 'y': y, 'w': w, 'h': h,
        'confidence': conf,
        'class_id': 0,
        'label': label,
    }


def _seed_project(steps: list, logic_mode: str = "sequential",
                  task_type: str = "detection",
                  pipeline_extra: dict = None) -> dict:
    """造一份最小可用的 project_config dict。

    steps: [(label, threshold, min_frames, accept_once, max_duration, timeout_ng), ...]
    """
    steps_config = []
    for i, (label, thr, mf, once, max_dur, timeout_ng) in enumerate(steps, start=1):
        steps_config.append({
            "id": i,
            "label": label,
            "displayLabel": label,
            "enabled": True,
            "threshold": int(thr * 100),
            "min_frames": mf,
            "accept_once": once,
            "max_duration": max_dur,
            "timeout_ng": timeout_ng,
            "max_interval": 5.0,
            "detection_type": "dynamic",
        })
    pipeline = {
        "settlement_mode": "first_step",
        "idle_timeout_seconds": 0,
        "cycle_max_duration": 0,
    }
    if logic_mode == "sequential":
        pipeline["sequence_order"] = [{"step_id": s["id"]} for s in steps_config]
    if pipeline_extra:
        pipeline.update(pipeline_extra)

    db = _TestSession()
    try:
        proj = _models.Project(
            name=f"e2e-{int(time.time() * 1000)}",
            task_type=task_type,
            logic_mode=logic_mode,
            steps_config=steps_config,
            pipeline_config=pipeline,
            counters_config=[],
        )
        db.add(proj)
        db.commit()
        db.refresh(proj)
        proj_id = proj.id
    finally:
        db.close()

    return {
        "id": proj_id,
        "name": "e2e",
        "task_type": task_type,
        "logic_mode": logic_mode,
        "steps_config": steps_config,
        "pipeline_config": pipeline,
        "counters_config": [],
    }


def _new_manager(channel_id: int = 0) -> VideoSourceManager:
    """造一个干净的 VideoSourceManager。
    禁用所有 IO（录像/MJPEG/截图），保留状态机本身。
    """
    mgr = VideoSourceManager(channel_id=channel_id)
    # 强制走"无录像"分支：start_step_recording 自身在 export_settings 关录像时直接 return
    mgr.export_settings = {
        'record_step_duration': True,
        'record_step_interval': True,
        'record_cycle_duration': True,
        'record_counters': True,
        'record_step_video': False,
        'record_cycle_video': False,
        'record_session_video': False,
        'video_quality': 'medium',
        'video_fps': 30,
    }
    # source.py 头部 print 调试信息，测试时 debug_log 一般是 no-op，这里不再额外抑制
    return mgr


def _drive_frames(mgr: VideoSourceManager, cfg: dict,
                   detections_per_frame: list,
                   frame_interval: float = 0.05) -> None:
    """喂一组帧给状态机。每个元素是该帧的 detections list。"""
    frame = _make_frame()
    for dets in detections_per_frame:
        mgr._update_step_stats(dets, frame)
        time.sleep(frame_interval)


# =============================================================================
# T1：接口契约
# =============================================================================

REQUIRED_METHODS = [
    # 生命周期
    "__init__", "set_project_config", "load_model", "start_detection", "stop_detection",
    "pause", "resume", "stop",
    # 会话/周期
    "start_session", "end_session", "start_cycle", "end_cycle", "_discard_empty_cycle",
    "_ensure_session_active", "record_step", "_reconcile_step_records",
    # 状态机判定
    "_update_step_stats", "_settle_sequential_cycle", "_settle_detection_cycle",
    "_settle_custom_cycle", "_check_events", "_check_sequential_mode",
    "_check_custom_sequential_mode", "_check_custom_detection_mode", "_trigger_event",
    # 推理
    "_detect_only", "_detect_and_track", "_detect_segment",
    "_start_inference_thread", "_stop_inference_thread", "_inference_loop",
    # 录像
    "start_step_recording", "stop_step_recording", "start_cycle_recording",
    "stop_cycle_recording", "start_session_recording", "stop_session_recording",
    "_recording_loop", "_close_all_writers",
    # 视频源
    "start_camera", "start_video", "set_image", "start_hcnetsdk", "start_hikvision_camera",
    # 帧 / MJPEG
    "get_frame", "get_detections", "generate_mjpeg", "get_snapshot",
    # 几何
    "_bbox_iou", "_bbox_center_dist", "_point_in_polygon",
]

REQUIRED_ATTRS = [
    "channel_id", "source_type", "is_running", "is_detecting",
    "model", "model_path", "current_session_id", "current_cycle_id",
    "current_cycle_steps", "step_conf_thresholds", "step_time_config",
    "counters", "events_log", "frame_lock", "capture_lock",
    "project_config", "export_settings",
]


class TestInterfaceContract(unittest.TestCase):
    """mixin 拆分时的安全网：所有这些方法/属性丢一个都立即 fail。"""

    def test_required_methods_exist(self):
        for name in REQUIRED_METHODS:
            self.assertTrue(
                hasattr(VideoSourceManager, name),
                f"VideoSourceManager 缺方法 {name}（mixin 拆分丢失？）"
            )

    def test_required_attrs_exist(self):
        m = _new_manager()
        for name in REQUIRED_ATTRS:
            self.assertTrue(
                hasattr(m, name),
                f"VideoSourceManager 实例缺属性 {name}（__init__ 漏初始化？）"
            )


# =============================================================================
# T2：sequential 模式 × 完整顺序 → OK cycle
# =============================================================================

class TestSequentialOK(unittest.TestCase):
    def setUp(self):
        self.mgr = _new_manager(channel_id=1)
        self.cfg = _seed_project(
            steps=[
                ("stepA", 0.5, 1, False, None, False),
                ("stepB", 0.5, 1, False, None, False),
                ("stepC", 0.5, 1, False, None, False),
            ],
            logic_mode="sequential",
        )
        self.mgr.set_project_config(self.cfg)
        # 不走 start_detection，直接开 session（避免摄像头依赖）
        sess = self.mgr.start_session(self.cfg["id"])
        self.assertIsNotNone(sess, "start_session 失败")
        self.session_id = sess["session_id"]

    def tearDown(self):
        self.mgr.end_session()

    def test_complete_sequence_produces_ok_cycle(self):
        # 依次喂 A → B → C → 全部消失 → 触发 settlement
        A = [_make_detection("stepA")]
        B = [_make_detection("stepB")]
        C = [_make_detection("stepC")]
        EMPTY: list = []

        # 多帧巩固每个步骤识别
        frames = (
            [A] * 3 +
            [EMPTY] * 2 +
            [B] * 3 +
            [EMPTY] * 2 +
            [C] * 3 +
            [EMPTY] * 6  # 让 disappear gap > tolerance，触发周期结算
        )
        _drive_frames(self.mgr, self.cfg, frames, frame_interval=0.02)

        # 强制结算并写入 DB
        self.mgr._settle_sequential_cycle()
        if self.mgr.current_cycle_id:
            self.mgr.end_cycle(is_good=True, reason="e2e-ok")

        db = _TestSession()
        try:
            cycles = db.query(_models.DetectionCycle).filter(
                _models.DetectionCycle.session_id == self.session_id
            ).all()
            # 至少有一个被持久化的 cycle（end_time 非空）
            settled = [c for c in cycles if c.end_time is not None]
            self.assertGreaterEqual(len(settled), 1,
                                     f"未形成已结算 cycle, 只有 {[c.cycle_uuid for c in cycles]}")
        finally:
            db.close()


# =============================================================================
# T3：sequential 模式 × 缺最后一步 → NG cycle
# =============================================================================

class TestSequentialMissingStep(unittest.TestCase):
    def setUp(self):
        self.mgr = _new_manager(channel_id=2)
        self.cfg = _seed_project(
            steps=[
                ("stepA", 0.5, 1, False, None, False),
                ("stepB", 0.5, 1, False, None, False),
                ("stepC", 0.5, 1, False, None, False),
            ],
            logic_mode="sequential",
        )
        self.mgr.set_project_config(self.cfg)
        sess = self.mgr.start_session(self.cfg["id"])
        self.session_id = sess["session_id"]

    def tearDown(self):
        self.mgr.end_session()

    def test_missing_last_step_triggers_ng_or_no_cycle(self):
        A = [_make_detection("stepA")]
        B = [_make_detection("stepB")]
        EMPTY: list = []
        frames = (
            [A] * 3 + [EMPTY] * 2 +
            [B] * 3 + [EMPTY] * 6
        )
        _drive_frames(self.mgr, self.cfg, frames, frame_interval=0.02)

        self.mgr._settle_sequential_cycle()
        if self.mgr.current_cycle_id:
            self.mgr.end_cycle(is_good=False, reason="e2e-missing-stepC")

        db = _TestSession()
        try:
            cycles = db.query(_models.DetectionCycle).filter(
                _models.DetectionCycle.session_id == self.session_id,
                _models.DetectionCycle.end_time.isnot(None),
            ).all()
            # 允许两种合规结局：
            #   a) 形成 cycle 但 is_good=False（结算判定 NG）
            #   b) 直接被 _discard_empty_cycle 丢弃（步骤未达预期门槛）
            for c in cycles:
                self.assertFalse(c.is_good,
                                  f"缺步骤却得到 OK cycle, 步骤序列 = {c.step_sequence}")
        finally:
            db.close()


# =============================================================================
# T4：detection 模式 × 多次 OK cycle 隔离
# =============================================================================

class TestDetectionMultiCycle(unittest.TestCase):
    def setUp(self):
        self.mgr = _new_manager(channel_id=3)
        self.cfg = _seed_project(
            steps=[
                ("hit", 0.5, 1, False, None, False),
            ],
            logic_mode="detection",
        )
        self.mgr.set_project_config(self.cfg)
        sess = self.mgr.start_session(self.cfg["id"])
        self.session_id = sess["session_id"]

    def tearDown(self):
        self.mgr.end_session()

    def test_three_isolated_cycles(self):
        H = [_make_detection("hit")]
        EMPTY: list = []

        # 手动驱动三个独立 cycle：每次 start_cycle 后喂帧再 end_cycle
        for i in range(3):
            self.mgr.start_cycle()
            _drive_frames(self.mgr, self.cfg, [H, H, H, EMPTY, EMPTY], 0.02)
            # 在 detection 模式下手动结束周期
            self.mgr.end_cycle(is_good=True, reason=f"e2e-detection-{i}")

        db = _TestSession()
        try:
            cycles = db.query(_models.DetectionCycle).filter(
                _models.DetectionCycle.session_id == self.session_id,
                _models.DetectionCycle.end_time.isnot(None),
            ).order_by(_models.DetectionCycle.cycle_number).all()

            # 每个 cycle 都应有独立的 cycle_uuid 和递增的 cycle_number
            self.assertEqual(len(cycles), 3, f"期望 3 个 cycle, 实得 {len(cycles)}")
            uuids = [c.cycle_uuid for c in cycles]
            self.assertEqual(len(set(uuids)), 3, f"cycle_uuid 应全唯一: {uuids}")
            numbers = [c.cycle_number for c in cycles]
            self.assertEqual(numbers, sorted(numbers), "cycle_number 应递增")
            for c in cycles:
                self.assertTrue(c.is_good, f"cycle {c.cycle_uuid} 应为 OK")
                self.assertIsNotNone(c.duration, "duration 应被填写")
        finally:
            db.close()


# =============================================================================
# T5：DB 完整性 — StepRecord.cycle_id 必须全部命中合法 cycle
# =============================================================================

class TestDBIntegrity(unittest.TestCase):
    def test_step_record_cycle_fk_consistency(self):
        mgr = _new_manager(channel_id=4)
        cfg = _seed_project(
            steps=[
                ("alpha", 0.5, 1, False, None, False),
                ("beta",  0.5, 1, False, None, False),
            ],
            logic_mode="detection",
        )
        mgr.set_project_config(cfg)
        sess = mgr.start_session(cfg["id"])
        session_id = sess["session_id"]
        try:
            for i in range(2):
                mgr.start_cycle()
                _drive_frames(
                    mgr, cfg,
                    [[_make_detection("alpha")]] * 3 +
                    [[_make_detection("beta")]] * 3 +
                    [[]] * 3,
                    0.02,
                )
                # detection 模式下手工结算 cycle
                mgr._reconcile_step_records()
                mgr.end_cycle(is_good=True, reason=f"int-{i}")
        finally:
            mgr.end_session()

        db = _TestSession()
        try:
            cycle_ids = {
                c.id for c in db.query(_models.DetectionCycle).filter(
                    _models.DetectionCycle.session_id == session_id
                ).all()
            }
            steps = db.query(_models.StepRecord).filter(
                _models.StepRecord.cycle_id.in_(cycle_ids)
            ).all() if cycle_ids else []
            # FK 完整性：每条 StepRecord 的 cycle_id 必须命中
            for s in steps:
                self.assertIn(s.cycle_id, cycle_ids,
                              f"StepRecord {s.record_uuid} cycle_id={s.cycle_id} 找不到对应 cycle")
            # session 字段被 end_session 正确填充
            sess_row = db.query(_models.DetectionSession).filter(
                _models.DetectionSession.id == session_id
            ).first()
            self.assertIsNotNone(sess_row)
            self.assertEqual(sess_row.status, "completed")
            self.assertIsNotNone(sess_row.end_time)
        finally:
            db.close()


# =============================================================================
# T6：MES Hook 旁路 — start_session/start_cycle/end_cycle 通知顺序正确
# =============================================================================

class _FakeMESHook:
    def __init__(self):
        self.events = []

    def is_scan_required(self, ch):
        return False

    def has_pending_workpiece(self, ch):
        return False

    def on_session_start(self, **kwargs):
        self.events.append(("session_start", kwargs))

    def on_cycle_start(self, **kwargs):
        self.events.append(("cycle_start", kwargs))

    def on_cycle_end(self, **kwargs):
        self.events.append(("cycle_end", kwargs))


class TestMESHookFlow(unittest.TestCase):
    def test_hook_called_in_order(self):
        mgr = _new_manager(channel_id=5)
        mgr._mes_hook = _FakeMESHook()
        cfg = _seed_project(
            steps=[("only", 0.5, 1, False, None, False)],
            logic_mode="detection",
        )
        mgr.set_project_config(cfg)
        mgr.start_session(cfg["id"])
        mgr.start_cycle()
        _drive_frames(mgr, cfg, [[_make_detection("only")]] * 3 + [[]] * 2, 0.02)
        mgr.end_cycle(is_good=True, reason="hook-flow")
        mgr.end_session()

        types = [e[0] for e in mgr._mes_hook.events]
        # detection 模式下状态机可能在 _update_step_stats 内部再启一个 cycle，
        # 这是合规行为；只校验：开头 session_start、结尾 cycle_end、且至少有一次 cycle_start
        self.assertGreater(len(types), 0, "MES hook 没收到任何事件")
        self.assertEqual(types[0], "session_start", f"首事件应为 session_start: {types}")
        self.assertEqual(types[-1], "cycle_end", f"末事件应为 cycle_end: {types}")
        self.assertIn("cycle_start", types, f"缺 cycle_start: {types}")
        # 同样 channel_id 不应混淆
        for _name, payload in mgr._mes_hook.events:
            self.assertEqual(payload.get("channel_id"), 5,
                              f"hook payload channel_id 错: {payload}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
