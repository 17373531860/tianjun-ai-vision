"""VideoSourceManager 状态字段初始化 (P7 第十刀)

历史问题：
  VSM._init_inference_vars 是个 194 行的"字段堆砌大方法", 一口气声明 ~80 个
  状态字段, 主类阅读体验极差. 这些字段全部是 VSM 自身状态 (而非组件状态),
  所以不能拆成 has-a 组件, 但可以做"物理外移": 把 init 代码搬到独立模块,
  按职责分组, 用多个小函数处理.

设计原则：
  - 全部函数都接受 host (VideoSourceManager 实例), 直接 setattr 到 host
  - 字段所有权仍归 VSM, 这只是把"初始化代码"模块化
  - 保留原始字段名, 现有读写代码零修改
  - 按主题切分: 推理线程 / 健康检查 / 步骤状态 / Tracking / 录制 / ...

效果：
  - VSM 类内 _init_inference_vars 从 194 行 → 1 行 (单纯转发)
  - 各 init helper 集中在本文件, 按主题维护更清晰
"""
from __future__ import annotations

import queue
import threading
import time

from backend.api.rod_filter import RodSessionGate, read_rod_filter_config

from backend.api.source_drawer import Drawer
from backend.api.source_counters import Counters
from backend.api.source_inference_executor import InferenceExecutor
from backend.api.source_sequence_labels import SequenceLabels


def _init_inference_threading(h):
    """推理双线程架构相关字段"""
    h._inference_thread = None
    h._inference_running = False
    h._latest_frame_for_inference = None
    h._latest_frame_original_size = None
    # v2.7.14: 推理用原图, stats/screenshot 用显示帧 → 两份分开存
    h._latest_display_small_for_stats = None
    h._inference_frame_lock = threading.Lock()
    h._confirmed_detections = []
    h._confirmed_detections_lock = threading.Lock()


def _init_health_and_timeout(h):
    """线程心跳 + 推理超时保护"""
    h._last_inference_heartbeat = time.time()
    h._last_capture_heartbeat = time.time()
    h._health_check_interval = 5.0
    h._thread_timeout_threshold = 10.0
    h._inference_timeout = 10.0
    h._inference_timeout_count = 0
    h._max_consecutive_timeouts = 5
    h._last_successful_inference = time.time()


def _init_components(h):
    """所有 P7 组合组件 (Drawer/Counters/InferenceExecutor/SequenceLabels)"""
    h.inference_exec = InferenceExecutor()
    h.sequence_labels = SequenceLabels(host=h)
    h.drawer = Drawer()
    h.counters_mgr = Counters(host=h)


def _init_fps_stats(h):
    """采集线程 / 推理线程 FPS + 帧序号统计"""
    h.fps_actual = 0
    h.fps_inference = 0
    h.latency = 0
    h._fps_counter = 0
    h._fps_time = time.time()
    h._fps_inference_counter = 0
    h._fps_inference_time = time.time()
    h._frame_seq = 0


def _init_step_state(h):
    """步骤检测状态 (截图 / 计数 / 时间窗 / 帧确认 / 静态步骤 / 替补 / 同时出现组)"""
    h.step_screenshots = {}
    h.step_counts = {}
    h.step_last_seen = {}
    h.step_start_time = {}
    h.step_time_config = {}

    # 项目配置 + 检测阈值
    h.project_config = None
    h.settlement_mode = 'first_step'  # 'first_step' or 'last_step'
    h.idle_timeout_seconds = 0
    h.cycle_max_duration = 0
    h.step_conf_thresholds = {}

    # 传动杆误判过滤 (默认全关, 从 project_config 动态读)
    h._rod_filter_cfg = read_rod_filter_config(None)
    h._rod_gate = RodSessionGate(
        rod_label=h._rod_filter_cfg["gate_rod_label"],
        gate_labels=h._rod_filter_cfg["gate_labels"],
    )

    # 帧确认相关
    h.step_min_frames = {}
    h.step_consecutive_frames = {}
    h.step_frame_confirmed = {}
    h.step_gap_tolerance = {}
    h._step_gap_count = {}

    # 静态步骤
    h.step_detection_type = {}  # 'dynamic' / 'static'
    h.step_static_config = {}
    h.step_static_triggered = {}

    # 同时出现组
    h._simultaneous_groups = []
    h._sim_group_buffers = {}

    # 替补步骤 + 严格顺序 + 单次接收
    h.step_backup_map = {}
    h.step_primary_to_backup = {}
    h.backup_steps_seen_in_cycle = set()
    h.step_strict_order = {}
    h.step_accept_once = {}


def _init_event_and_cycle_state(h):
    """事件 / 周期 / 第一步重确认 / NG 周期统计"""
    h.events_log = []
    h._event_seq = 0
    h.ng_step_cycle_counts = {}
    h.current_cycle_steps = []
    h.last_added_step = None
    h.cycle_complete = False

    h._first_step_had_gap = False
    h._first_step_reconfirmed = False
    h._first_step_disappeared_at = None
    h._last_step_added_time = None
    h._step_raw_start = {}
    h._last_ng_time = 0
    h._cycle_regression = False  # A-B-A 步骤回退标记


def _init_tracking_state(h):
    """Tracking 模式 (物品清点) 全部状态"""
    h._tracking_objects = {}
    h._tracking_class_counters = {}
    h._tracking_display_map = {}
    h._tracking_item_checklist = {}
    h._tracking_lost_frames = {}
    h._tracking_letter_map = {}
    h._tracking_letter_idx = 0
    h._tracking_order_seq = 0
    h._tracking_prev_count = 0
    h._tracking_gone_frames = 0
    h._tracking_cycle_active = False
    h._tracking_had_roi_objects = False
    h._tracking_trigger_frames = 0
    h._tracking_recently_lost = {}
    h._tracking_transferred_ids = {}
    h._tracking_prev_positions = {}
    h._tracking_appearance = {}
    h._tracking_stable_frames = {}
    h._tracking_locked_ids = {}
    h._tracking_registered_positions = {}
    h._custom_tracker_yaml = None


def _init_event_counter_state(h):
    """Event counting 模式 (动作计数) 状态"""
    h._event_counters = {}
    h._event_state = {}
    h._event_visible_frames = {}
    h._event_gone_frames_count = {}
    h._event_first_seen = {}
    h._event_last_seen = {}


def _init_stack_state(h):
    """v2.7.4 Stack 模式 (堆叠模式) 状态"""
    h._stack_state = {}
    h._stack_counters = {}
    h._stack_disappeared_at = {}
    h._stack_visible_frames = {}


def _init_container_state(h):
    """Container 模式 (box + items) 状态"""
    h._container_mode = False
    h._container_label = ''
    h._box_objects = {}
    h._box_counter = 0
    h._box_settled_results = []


def _init_cycle_time_state(h):
    """周期 / 步骤耗时统计"""
    h.cycle_start_time = None
    h.cycle_times = []
    h.ng_cycle_times = []
    h.step_detection_times = {}
    h.step_durations = {}
    h.step_durations_history = {}
    h.step_intervals = {}
    h.last_step_completed_time = None


def _init_session_state(h):
    """会话 / 周期 ID + 录制开关 + 班次拆分"""
    h.current_session_id = None
    h.current_session_uuid = None
    h.current_cycle_id = None
    h.current_cycle_uuid = None
    h.current_cycle_number = 0
    h.cycle_step_records = []
    h.step_order_counter = 0
    h.recording_enabled = False
    h.export_settings = None
    h.last_cycle_end_time = None
    h._session_start_date = None
    h._session_start_shift = None  # 'day' / 'night' / None


def _init_recording_state(h):
    """视频录制器 + 录制队列 (独立线程)"""
    h.video_writer = None
    h.cycle_video_writer = None
    h.step_video_writers = {}
    h._step_writers_lock = threading.Lock()
    h._writer_lock = threading.Lock()
    h._recording_queue = queue.Queue(maxsize=30)  # ~1s 缓冲 @30fps
    h._recording_thread = None
    h._recording_running = False
    h._recording_drop_count = 0


# ============================================================
# 顶层入口: 一口气调用所有 init helper
# ============================================================
def init_state(h):
    """初始化 VideoSourceManager 全部推理/状态字段

    在 __init__ 的 _load_device_config 之后调用. 替代历史的
    `VideoSourceManager._init_inference_vars` 大方法 (194 行).
    """
    h.conf_threshold = 0.25
    h.iou_threshold = 0.45

    _init_inference_threading(h)
    _init_health_and_timeout(h)
    _init_components(h)
    _init_fps_stats(h)
    _init_step_state(h)
    _init_event_and_cycle_state(h)
    _init_tracking_state(h)
    _init_event_counter_state(h)
    _init_stack_state(h)
    _init_container_state(h)
    _init_cycle_time_state(h)
    _init_session_state(h)
    _init_recording_state(h)
