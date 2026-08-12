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
from collections import OrderedDict

from backend.api.rod_filter import RodSessionGate, read_rod_filter_config

from backend.api.source_drawer import Drawer
from backend.api.source_counters import Counters
from backend.api.source_inference_executor import InferenceExecutor
from backend.api.source_inference_router import InferenceRouter, ModelInstance
from backend.api.source_sequence_labels import SequenceLabels


def _init_inference_threading(h):
    """推理双线程架构相关字段"""
    h._inference_thread = None
    h._inference_running = False
    # 2026-07 TP 频闪真因修复: 推理线程唯一性保障。
    # start 的"检查-启动"两步间无锁, 恢复播放时采集自启 + resume 两路并发调用
    # 会各起一条线程, 交替发布"有结果/空结果" → 前端标注框逐帧频闪;
    # 代数 (generation) 则兜住"假死被放弃的旧线程因运行标志重新置真而复活"。
    h._inference_start_lock = threading.Lock()
    h._inference_generation = 0
    h._latest_frame_for_inference = None
    h._latest_frame_original_size = None
    # v2.7.14: 推理用原图, stats/screenshot 用显示帧 → 两份分开存
    h._latest_display_small_for_stats = None
    h._inference_frame_lock = threading.Lock()
    h._confirmed_detections = []
    h._confirmed_detections_lock = threading.Lock()
    # SyntheticMixin: 虚拟剧本源默认状态
    h._synthetic_spec = None
    h._synthetic_timeline = []
    h._synthetic_seq = 0
    h._synthetic_last_published_idx = -1
    h._latest_synthetic_inference_idx = -1
    # SyntheticMixin: 临时项目配置切回时的还原备份
    h._pre_synthetic_project_config = None


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
    """所有 P7 组合组件 (Drawer/Counters/InferenceExecutor/SequenceLabels) + Step 2 多模型 router"""
    h.inference_exec = InferenceExecutor()
    h.sequence_labels = SequenceLabels(host=h)
    h.drawer = Drawer()
    h.counters_mgr = Counters(host=h)
    _init_inference_router(h)


def _init_inference_router(h):
    """Step 2 (feat/multi-model-roi-link): 多模型推理路由器 + 默认 main 空壳

    设计：
      - h._router 是 InferenceRouter 实例 (跨模型 GPU 串行锁 / 调度器 / 事件队列)
      - h.models 是 router.models 的直接引用 (OrderedDict[name, ModelInstance])
      - 默认创建 'main' ModelInstance 空壳 (model=None, model_path=None);
        Step 3 起 ModelLoadMixin 在 load_model 时会把老字段双写到这个壳里
      - **本步骤不动任何老字段** (self.model / self.conf_threshold 等保留原状),
        老链路 100% 不受影响

    扩展：
      Step 3+ 中, 后端代码逐步迁移到通过 h._router / h.models[name] 访问;
      最后阶段才把老字段虚拟化为 main 实例的 property (Step 9)。
    """
    h._router = InferenceRouter()
    h.models = h._router.models  # 直接共享 OrderedDict (修改 router.models 即修改 h.models)
    h._router.add_model(ModelInstance(name='main', priority=100))


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
    h.settlement_mode = 'first_step'  # 'first_step' / 'last_step' / 'last_first'(v3.8.x)
    h.idle_timeout_seconds = 0
    h.cycle_max_duration = 0
    # v3.23 NG 补做策略 (默认全关 = 零差异); 由 _apply_pipeline_config 按项目配置覆盖
    h._ng_remediation = {'enabled': False, 'allow_step': True, 'allow_count': True}
    # v3.44 收尾防呆 (数量门 + 缺步结算挂起, 默认全关 = 零差异)
    # v3.44 起归入统一模型 ng_handling, 门/挂起各配各的提示事件
    h._closing_gate_enabled = False
    h._closing_gate_steps = set()
    h._closing_gate_event_id = None
    h._closing_gate_escalate_steps = set()  # v3.44.4 短拦长放: 被门拦时报警放行的步骤
    h._settle_hold_enabled = False
    h._settle_hold_timeout_s = 120.0
    h._settle_hold_event_id = None
    h._settle_hold = None  # 挂起态: {'missing':[], 'expected':[], 'since':ts[, 'need_total','ng_reason']}
    h._short_count_hold = False  # v3.44.1 少装挂起 (数量不足不判NG, 断点重做收尾步骤)
    # v3.48 计数组合判定表 (纯视觉判型, 检测模式专用; None=关)
    h._combo_table = None
    h._combo_last_tag = None  # 最近一次命中行的机型 tag (进检测结果透出)
    h._combo_positional = None  # count_mode='positional' 的位置去重计数引擎
    h.step_conf_thresholds = {}
    # v3.10+ 步骤级 box 尺寸过滤: {label: (max_w, max_h)} 归一化比例
    # 0 / 缺省 = 关闭过滤; 用途见 source_detect_runners_mixin._passes_box_size_limit
    h.step_box_size_limits = {}

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
    # steps_config[].roi (归一化多边形): 该标签仅在 ROI 内才算检测到 (全模式 + tracking)
    h.step_roi_polygons = {}
    # v3.32 同标签区域拆分 + 工件就位提示 (pipeline_config.label_splits / placement_guide)
    # None = 未配置 → 推理热路径一次 getattr 早退, 零开销
    h._label_split_engine = None
    h._placement_guide_state = None
    # 区域事件模式判定引擎 (pipeline_config.region_events, logic_mode='region_events')
    # None = 非该模式 → 推理热路径一次 getattr 早退, 零开销
    h._region_event_engine = None
    # v3.32 严格顺序违序即时事件 (pipeline_config.strict_order_violation_event_id)
    h.strict_order_violation_event_id = None
    h._strict_violation_throttle = {}
    # v3.43 实时NG (pipeline_config.instant_ng_on_violation): 违序/前置缺失被确认
    # 的瞬间直接触发 NG 事件走完整结算, 不等周期收尾。False = 关 (零差异)。
    h.instant_ng_on_violation = False
    # v3.35 步骤外设门控 (steps_config[].device_gate): {label: gate_cfg}
    # 空 = 未配置 → _process_single_step 一次 get 早退, 零开销
    h.step_device_gates = {}


def _init_event_and_cycle_state(h):
    """事件 / 周期 / 第一步重确认 / NG 周期统计"""
    h.events_log = []
    h._event_seq = 0
    h.ng_step_cycle_counts = {}
    h.current_cycle_steps = []
    h.last_added_step = None
    h.cycle_complete = False

    h._last_step_added_time = None
    h._step_raw_start = {}
    h._last_ng_time = 0
    h._last_event_time = 0.0  # v3.10.x 防重复结算时间窗口锚
    h._cycle_regression = False  # A-B-A 步骤回退标记

    # v3.8.x 跨周期同时出现组 (类二):
    # 被屏蔽标签集合: 这些标签当前不参与状态机 (不更新 step_last_seen / 不加入 cycle_steps /
    # 不进同时组缓冲), 直到出现"非屏蔽、非组内、对周期有意义"的标签时一次性解除.
    h._blocked_labels = set()
    # 跨周期等待状态: { group_idx: {'phase': 'waiting', 'first_member': label,
    #                                'wait_start_time': float, 'time_window': float,
    #                                'group_labels': set} }
    # phase 仅 'waiting' 一个值, 占位是为以后好扩展.
    h._cross_cycle_waiting = {}

    # v3.8.x last_first 结算模式专属:
    #   _pending_first_step: D 锚结算后置 True, 等待首步开新周期; 首步 / 顶替步进入 cycle_steps 后置 False
    #   仅在 settlement_mode == 'last_first' 时被读写; 其他模式下永远保持 False
    # D 残影屏蔽集合复用现有 _blocked_labels (last_first 与跨周期同时出现组互斥, 不会冲突)
    h._pending_first_step = False

    # v3.9.x 事件人工确认阻塞态:
    #   触发 require_ack=True 的事件后, 主循环 + 推流 + 状态机全停, 直到工人调
    #   /api/v1/source/detection/ack-event 主动确认才解除. 设计为通道级隔离,
    #   多通道场景某个工位阻塞不影响其他工位.
    #
    # 字段语义:
    #   _pending_ack             : True 即阻塞态
    #   _pending_ack_started_at  : 阻塞起始时间戳 (用于超时计算)
    #   _pending_ack_event_id    : 触发阻塞的事件编号 (前端可用于关联提示框)
    #   _pending_ack_event_name  : 触发阻塞的事件名 (前端展示)
    #   _pending_ack_timeout_sec : 超时阈值秒数 (0 = 永不超时, 必须人工确认)
    #   _pending_ack_reason      : 触发原因全文 (v3.43.1 固化进阻塞态; 之前前端从
    #                              recent_events 捞, 事件超 30s 滚出窗口后弹窗原因变 "—")
    h._pending_ack = False
    h._pending_ack_started_at = None
    h._pending_ack_event_id = None
    h._pending_ack_event_name = None
    h._pending_ack_timeout_sec = 0
    h._pending_ack_reason = None
    # v3.44.4: 已结算落账的 NG 定格, 确认释放时强制清运行时 (即使配了保留周期) —
    # 见 _finalize_settle_hold_ng / _ack_release_keep_cycle
    h._ack_clear_runtime_after = False

    # v3.23 NG 补做 (缺步骤延迟落账):
    #   缺步骤 NG 且项目开了 _ng_remediation.allow_step 时, 不立刻 end_cycle / 计数 /
    #   推 MES, 而是把"本该 NG 的这个周期"挂起 (报警+提示工人), 等人工:
    #     - 补步骤 (resolve_step_remediation('supplement_step')) → 信任补做, 直接判 OK 落账
    #     - 认 NG  (resolve_step_remediation('confirm_ng'))      → 现在才落账 NG
    #     - 重做   (resolve_step_remediation('redo'))            → 丢弃在制周期重检
    #   _pending_remediation: None=无挂起; dict={kind/missing/reason/event_id/cycle_id/...}
    #   _remediation_bypass : 一次性旁路标志, confirm_ng 重发 NG 事件时绕开 defer 守门防自锁
    h._pending_remediation = None
    h._remediation_bypass = False


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
    # v3.3.0 scan_pair: 跨帧 sticky '曾齐过' flag, 由 _update_tracking_stats 维护,
    # _reset_counting_cycle 清空. settle_for_scan_pair 调 _settle_counting_cycle 时
    # 通过 _scan_pair_settle_hint 让本字段决定 OK/NG.
    h._tracking_was_complete = False
    h._scan_pair_settle_hint = False

    # v3.4.0 scan_mode='D' (容器跨线/区域触发扫码) 状态机:
    # _scan_d_armed_box: 当前正在等扫码的 box display_id (None = 没有)
    # _scan_d_box_states: {box_did: {"side": -1/0/1, "in_zone": bool, "armed": bool,
    #                                "scanned": bool, "gone_frames": int}}
    #   side: 上一帧 box 中心点位于线的哪一侧 (line 模式), 0=未知/在线上
    h._scan_d_armed_box = None
    h._scan_d_box_states = {}
    h._tracking_recently_lost = {}
    h._tracking_transferred_ids = {}
    h._tracking_prev_positions = {}
    h._tracking_appearance = {}
    h._tracking_stable_frames = {}
    h._tracking_locked_ids = {}
    h._tracking_registered_positions = {}
    h._custom_tracker_yaml = None

    # v3.50 齐件即结算 (pipeline_config.tracking_settle_on_complete, 默认关):
    # _tracking_entry_pending: {track_id: {'label','frames','ts'}} 新物品"确认放入帧数"
    #   待入账缓冲 — 连续 N 帧被看见才真正分配 display_id / 计数 (_reset_counting_cycle 清).
    # _settle_complete_exempt: {track_id: {'ts','bbox'}} 凑齐即结算时在场物品的豁免名单,
    #   离场前不计入下一周期 (防"结算后同帧连环开假周期"). 注意: 故意**不**在
    #   _reset_counting_cycle 里清 — 结算本身就会触发 reset, 清了豁免就失效;
    #   过期由 _update_tracking_stats 按 max_lost_sec 自然回收.
    h._tracking_entry_pending = {}
    h._settle_complete_exempt = {}


def _init_event_counter_state(h):
    """Event counting 模式 (动作计数) 状态"""
    h._event_counters = {}
    h._event_state = {}
    h._event_visible_frames = {}
    h._event_gone_frames_count = {}
    h._event_first_seen = {}
    h._event_last_seen = {}


def _init_stack_state(h):
    """v2.7.4 Stack 模式 (堆叠模式) 状态; v3.19.x 批层语义扩展"""
    h._stack_state = {}
    h._stack_counters = {}
    h._stack_disappeared_at = {}
    h._stack_visible_frames = {}
    # v3.19.x 批层语义 (layer_min_count): 层内达标连续帧 / 本层已闩锁 / 层内峰值 / 未达标层明细
    h._stack_sat_frames = {}
    h._stack_latched = {}
    h._stack_phase_peak = {}
    h._stack_partials = {}


def _init_container_state(h):
    """Container 模式 (box + items) 状态"""
    h._container_mode = False
    h._container_label = ''
    h._box_objects = {}
    h._box_counter = 0
    h._box_settled_results = []
    # v3.50 齐件即结算 (容器): "已结算等离开"的箱子 {box_track_id: {'ts','bbox','gone_frames'}}
    # — 凑齐即结算后箱子还在画面里, 离场前禁止重建 ledger / 再入账 (照抄 scan_d
    # "已扫码等离场"思路, 但按 track_id 键控, 防止 display_id 跨周期复用串台).
    # _container_entry_pending: {(box_did, item_tid): {'frames','ts'}} 箱内物品
    # "确认放入帧数"待入账缓冲.
    h._box_settled_waiting_exit = {}
    h._container_entry_pending = {}
    # v3.1.2: 多工位广播结算联动 - 标识当前是否处于"被联动强制结算"中,
    # 用于阻断 _settle_box → end_cycle → notify_cycle_settled → 又回到本工位的循环.
    h._force_settling_in_progress = False
    # v3.4.2 race-condition fix: _settle_counting_cycle 重入锁. 推理线程 (settle
    # confirmed) 与 mes_hooks 线程 (settle_for_scan_pair) 可能并行调本函数, 同一
    # cycle 被 trigger NG 两次 → 计数 +1+1, NG 多算. RLock 让同线程递归调用 OK
    # (settle_for_scan_pair 会嵌套调 _settle_counting_cycle), 跨线程则后到的
    # try-acquire 失败 → 静默跳过.
    import threading as _threading
    h._settle_lock = _threading.RLock()


def _init_cycle_time_state(h):
    """周期 / 步骤耗时统计"""
    h.cycle_start_time = None
    h.cycle_times = []
    h.ng_cycle_times = []
    # v3.10.x B方案v2: 视频源场景下镜像记录"帧号锚", PT/CT 用 (last - start) / fps 算耗时,
    # 跟客户机解码速度完全解耦. 状态机的 disappear_delay 等仍走 wall-clock 不动.
    # 非视频源 (实时摄像头) 这些字段写 0, 计算口会 fallback 回 wall-clock.
    h.cycle_start_frame_pos = None
    h.step_start_frame_pos = {}
    h.step_last_frame_pos = {}
    h.step_detection_times = {}
    h.step_durations = {}
    h.step_durations_history = {}
    h.step_intervals = {}
    h.last_step_completed_time = None
    # v3.5.x: PT 计算方式扩展 — 同一步骤在一个周期内可能多次连续出现,
    #   step_durations / step_durations_history 是按"段"记的(每次"出现-消失"为一段),
    #   step_cycle_durations 在本周期内对每个 label 累加 SUM, end_cycle 时快照到
    #   step_cycle_durations_history(按 label 的 list, 上限 100), 给前端"PT 合并"档使用.
    h.step_cycle_durations = {}
    h.step_cycle_durations_history = {}
    # v3.10.x: 当前周期内每步的分段时长列表 (供前端 sum/max/first_only 三档切换)
    h.step_cycle_segments = {}

    # v3.9.x D 方案 (V2 架构): 步骤"在画面里实际可见的帧时长之和"(秒).
    #
    # 现行 step_durations / step_cycle_durations 用"跨度" (last_seen - start_time)
    # 算 PT, 客户场景里某个标签从早到晚都被识别 (涂黑残留 / 翻面后还在画面 / 上一件
    # 还摆在工位等), 跨度会被拖到 18 秒, 但客户实际做这一步只用 2 秒.
    #
    # 后端的策略: 永远多算一份"累计可见时长"(每帧累加 detected_labels 里每个 label),
    # 通过 detection/results 透出. 前端"显示设置 → PT 计算口径"是纯展示档:
    # 'span'    (默认) → 用 step_durations / step_cycle_durations
    # 'visible'        → 用 step_visible_seconds
    #
    # 后端的 step_durations 等字段语义不变 (CSV 导出 / history 等下游消费者都不受影响).
    # 周期开始时清空累计字典, 步骤完成时不清 (同周期内"出现-消失-出现"段落都累加).
    # _dt 上限 1.0s 防 FPS 极低 / 暂停恢复单帧大跨度污染累计值.
    h.step_visible_seconds = {}
    h._last_frame_ts_for_visible = None


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
    h.recording_failures = []
    h._recording_failure_lock = threading.Lock()


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
