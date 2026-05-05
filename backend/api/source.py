"""
输入源管理 API
支持 USB 摄像头、本地视频文件、本地图片、海康工业相机
集成 YOLO 模型推理
集成会话和周期记录
"""
from fastapi import APIRouter, HTTPException, UploadFile, File, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import cv2
import os
import shutil
import uuid
import threading
import time
import numpy as np
import subprocess
from datetime import datetime
from ctypes import *
from PIL import Image, ImageDraw, ImageFont
from backend.core.config import settings, DATA_DIR
import json as _json
from backend.db.database import SessionLocal
from backend.models.models import DetectionSession, DetectionCycle, StepRecord, VideoClip, DataExportSetting, Project
from backend.api.rod_filter import (
    filter_rod_by_companion,
    RodSessionGate,
    read_rod_filter_config,
)

router = APIRouter()


def _read_engine_metadata_imgsz(engine_path: str) -> Optional[int]:
    """直接解析 .engine 文件头部 Ultralytics 嵌入的 JSON metadata，读取 imgsz。

    无需触发 AutoBackend 实例化（YOLO('xxx.engine') 构造时尚未创建 AutoBackend，
    binding 信息要等 predict 第一次调用），所以这是最早能拿到 engine 真实输入尺寸的途径。

    Ultralytics 写入格式（autobackend.py 第 376~377 行）：
        meta_len = int.from_bytes(f.read(4), byteorder="little")
        metadata = json.loads(f.read(meta_len).decode("utf-8"))

    返回 max(imgsz) 或 None（解析失败 / 无 metadata 时）。
    """
    try:
        import json
        import ast
        with open(engine_path, 'rb') as f:
            head = f.read(4)
            if len(head) != 4:
                return None
            meta_len = int.from_bytes(head, byteorder='little')
            # Ultralytics metadata 一般几百字节，限制 64KB 防止误判普通 engine 文件
            if meta_len <= 0 or meta_len > 65536:
                return None
            try:
                meta_str = f.read(meta_len).decode('utf-8')
                metadata = json.loads(meta_str)
            except Exception:
                return None
            if not isinstance(metadata, dict):
                return None
            imgsz = metadata.get('imgsz')
            if isinstance(imgsz, str):
                try:
                    imgsz = ast.literal_eval(imgsz)
                except Exception:
                    return None
            if isinstance(imgsz, (list, tuple)) and len(imgsz) >= 1:
                return int(max(imgsz))
            if isinstance(imgsz, int):
                return imgsz
        return None
    except Exception:
        return None


# ========== 调试日志 + HCNetSDK + 海康工业相机 SDK (P7 第八刀: 全部迁至 source_sdk_loader) ==========
# 通过 re-export 兼容历史调用: `from backend.api.source import debug_log / HIK_SDK_AVAILABLE / ...`
from backend.api.source_sdk_loader import (  # noqa: E402,F401
    # 调试日志
    debug_log,
    hik_log,
    HIK_DEBUG,
    FREEZE_DEBUG,
    # HCNetSDK (NVR / 网络硬盘录像机)
    HCNET_SDK_AVAILABLE,
    HCNetSession,
    # 海康工业相机 SDK
    HIK_SDK_AVAILABLE,
    MvCamera,
    MV_CC_DEVICE_INFO_LIST,
    MV_CC_DEVICE_INFO,
    MV_FRAME_OUT_INFO_EX,
    MVCC_INTVALUE,
    MV_TRIGGER_MODE_OFF,
    MV_CC_PIXEL_CONVERT_PARAM,
    MV_USB_DEVICE,
    MV_GIGE_DEVICE,
    MV_ACCESS_Exclusive,
    PixelType_Gvsp_RGB8_Packed,
    # 工具函数
    _decode_hik_string,
    get_hikvision_device_list,
)


# ========== FFmpeg 路径查找 ==========
def get_ffmpeg_path():
    """
    获取 FFmpeg 可执行文件路径
    优先查找打包的 FFmpeg，然后查找系统 FFmpeg
    """
    import sys
    
    # 可能的打包路径（Electron 打包后）
    possible_paths = []
    
    # 获取当前脚本所在目录
    if getattr(sys, 'frozen', False):
        # 打包环境
        base_dir = os.path.dirname(sys.executable)
        possible_paths.append(os.path.join(base_dir, 'resources', 'ffmpeg', 'ffmpeg.exe'))
        possible_paths.append(os.path.join(base_dir, 'resources', 'ffmpeg', 'ffmpeg'))
        possible_paths.append(os.path.join(base_dir, '..', 'resources', 'ffmpeg', 'ffmpeg.exe'))
        possible_paths.append(os.path.join(base_dir, '..', 'resources', 'ffmpeg', 'ffmpeg'))
    
    # 开发环境 - 项目根目录
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    possible_paths.append(os.path.join(project_root, 'ffmpeg', 'ffmpeg.exe'))
    possible_paths.append(os.path.join(project_root, 'ffmpeg', 'ffmpeg'))
    
    # 检查打包路径
    for path in possible_paths:
        if os.path.isfile(path):
            print(f"[FFmpeg] 使用打包的 FFmpeg: {path}")
            return path
    
    # 回退到系统 PATH
    print("[FFmpeg] 使用系统 FFmpeg")
    return 'ffmpeg'

# 缓存 FFmpeg 路径
_FFMPEG_PATH = None

def get_cached_ffmpeg_path():
    global _FFMPEG_PATH
    if _FFMPEG_PATH is None:
        _FFMPEG_PATH = get_ffmpeg_path()
    return _FFMPEG_PATH


# ========== FFmpeg 录制器类（替代 OpenCV VideoWriter，更稳定） ==========


# ========== 卡尔曼滤波器类 ==========


# 全局变量管理视频源状态
from backend.api.source_tracking_mixin import TrackingMixin  # noqa: E402  P6 阶段一: 12 个 _tracking_* 搬到独立 mixin
from backend.api.source_inference_loop_mixin import InferenceLoopMixin  # noqa: E402  P6 阶段一: _inference_loop + 4 个 _inference_* 搬到独立 mixin
from backend.api.source_step_stats_mixin import StepStatsMixin  # noqa: E402  P6 阶段一第三刀: _update_step_stats 整体搬到独立 mixin
from backend.api.source_capture_loop_mixin import CaptureLoopMixin  # noqa: E402  P6 阶段一第四刀: _capture_loop 整体搬到独立 mixin
from backend.api.source_event_trigger_mixin import EventTriggerMixin  # noqa: E402  P6 阶段一第五刀: _trigger_event 整体搬到独立 mixin
from backend.api.source_model_load_mixin import ModelLoadMixin  # noqa: E402  P6 阶段一第六刀: load_model 整体搬到独立 mixin
from backend.api.source_check_modes_mixin import CheckModesMixin  # noqa: E402  P6 阶段一第七刀: 8 个 _check_*/_settle_box/_settle_counting/_update_container_grouping 搬到独立 mixin
from backend.api.source_settlement_mixin import SettlementMixin  # noqa: E402  P6 阶段一第八刀: 8 个 _settle_custom/_settle_detection/_settle_sequential/_process_*_step 搬到独立 mixin
from backend.api.source_detect_runners_mixin import DetectRunnersMixin  # noqa: E402  P6 阶段一第九刀: 3 个 _detect_only/_detect_and_track/_detect_segment 搬到独立 mixin
from backend.api.source_camera_start_mixin import CameraStartMixin  # noqa: E402  P6 阶段一第十刀: 10 个 start_*/release_*/reconnect/get_frame 摄像头方法搬到独立 mixin
from backend.api.source_session_lifecycle_mixin import SessionLifecycleMixin  # noqa: E402  P6 阶段一第十一刀: 16 个 session/cycle 生命周期方法搬到独立 mixin
from backend.api.source_recording_thread_mixin import RecordingThreadMixin  # noqa: E402  P6 阶段一第十二刀: 5 个 recording thread 方法搬到独立 mixin
from backend.api.source_recording_api_mixin import RecordingApiMixin  # noqa: E402  P6 阶段一第十四刀: 8 个录制公共 API (session/cycle/step) 搬到独立 mixin
from backend.api.source_lifecycle_mixin import LifecycleMixin  # noqa: E402  P6 阶段一第十五刀: 11 个 pause/resume/stop/clear_caches 控制方法搬到独立 mixin
from backend.api.source_periodic_actions_mixin import PeriodicActionsMixin  # noqa: E402  v3.5.0: 周期性强制动作 (每 N 轮做 E)
from backend.api.source_drawer import Drawer  # noqa: E402  P7 阶段一第一刀: DrawMixin 重构为 has-a 组合 (自持 kalman 状态)
from backend.api.source_mediapipe import MediaPipeOverlay  # noqa: E402  P7 第二刀: MediaPipe 子系统改组合 (自持 _mp_* 状态)
from backend.api.source_counters import Counters  # noqa: E402  P7 第三刀: 计数器子系统改组合 (自持 counters dict + 持久化)
from backend.api.source_video_transform import VideoTransform  # noqa: E402  P7 第四刀: 画面旋转/镜像/坐标映射改组合
from backend.api.source_inference_executor import InferenceExecutor  # noqa: E402  P7 第五刀: 推理线程池改组合
from backend.api.source_sequence_labels import SequenceLabels  # noqa: E402  P7 第九刀: 步骤标签查询改组合 (无状态, 仅依赖 project_config)


class VideoSourceManager(TrackingMixin, InferenceLoopMixin, StepStatsMixin, CaptureLoopMixin, EventTriggerMixin, ModelLoadMixin, CheckModesMixin, SettlementMixin, DetectRunnersMixin, CameraStartMixin, SessionLifecycleMixin, RecordingThreadMixin, RecordingApiMixin, LifecycleMixin, PeriodicActionsMixin):
    """主管理器 (P7 进行中: DrawMixin 已改组合 → self.drawer)"""

    # ===== P7 兼容层: 把已迁移到组件的属性/方法名映射回组件实例 =====
    # 形如 self._kalman_enabled / self._mp_pose / self._draw_box(...) 的历史调用
    # 通过 __getattr__/__setattr__ 自动转发到 self.drawer / self.mp_overlay,
    # 现有调用代码无需修改. 各组件拥有自己的状态字段, 数据所有权清晰.

    # Drawer (kalman + draw_box) 接管的字段
    _DRAWER_FIELDS = {
        '_kalman_filters', '_kalman_enabled',
        '_kalman_process_noise', '_kalman_measurement_noise',
        '_detection_history', '_detection_missing_frames', '_max_missing_frames',
    }
    _DRAWER_METHOD_ALIASES = {
        '_apply_kalman_filter': 'apply_kalman_filter',
        'update_kalman_params': 'update_kalman_params',
        '_draw_box': 'draw_box',
        '_draw_box_simple': 'draw_box_simple',
        '_get_chinese_font': 'get_chinese_font',
    }

    # MediaPipeOverlay 接管的字段 (注意 mediapipe_enabled / pose / hands /
    # confidence 4 个 public 配置字段保留在 VSM 上, 不在此列表)
    _MP_FIELDS = {
        '_mp_pose', '_mp_hands', '_mp_draw', '_mp_draw_styles',
        '_mp_last_pose_results', '_mp_last_hands_results',
        '_mp_frame_counter', '_mp_process_interval',
    }
    _MP_METHOD_ALIASES = {
        '_init_mediapipe': 'init',
        '_release_mediapipe': 'release',
        '_apply_mediapipe_overlay': 'apply_overlay',
    }

    # Counters 接管的字段 + 方法名 (P7 第三刀)
    _COUNTERS_FIELDS = {'counters'}
    _COUNTERS_METHOD_ALIASES = {
        '_persist_counters': 'persist',
        '_get_counter_file': 'get_file_path',
        '_save_counters_snapshot': 'save_snapshot_to_db',
    }

    # VideoTransform 接管的字段 + 方法名 (P7 第四刀)
    _VT_FIELDS = {'video_rotation', 'video_flip_h', 'video_flip_v'}
    _VT_METHOD_ALIASES = {
        '_apply_frame_transform': 'apply_to_frame',
        '_has_display_transform': 'has_transform',
        '_map_bbox_original_to_display': 'map_bbox_to_display',
        '_map_detections_original_to_display': 'map_detections_to_display',
    }

    # InferenceExecutor 接管的字段 + 方法名 (P7 第五刀)
    _IE_FIELDS = {'_inference_executor'}
    _IE_METHOD_ALIASES = {
        '_get_inference_executor': 'get',
        '_shutdown_inference_executor': 'shutdown',
    }

    # SequenceLabels 接管的方法 (P7 第九刀): 无状态, 7 个标签查询函数
    _SEQ_FIELDS = set()
    _SEQ_METHOD_ALIASES = {
        '_get_first_sequence_step_label': 'get_first_step_label',
        '_get_last_sequence_step_label':  'get_last_step_label',
        '_get_expected_sequence_labels':  'get_expected_labels',
        '_get_detection_step_labels':     'get_detection_labels',
        '_get_first_detection_step_label': 'get_first_detection_label',
        '_get_last_detection_step_label':  'get_last_detection_label',
        '_is_condition_prefix':           'is_condition_prefix',
    }

    # P7 兼容层路由表: 字段集 → 组件实例属性名, 方法别名 → 组件实例属性名
    # 单一来源, __getattr__/__setattr__ 共享, 加新组件只需扩这张表
    _COMPONENT_ROUTES = (
        # (component_attr, fields_set_name, method_aliases_name)
        ('drawer',          '_DRAWER_FIELDS',   '_DRAWER_METHOD_ALIASES'),
        ('mp_overlay',      '_MP_FIELDS',       '_MP_METHOD_ALIASES'),
        ('counters_mgr',    '_COUNTERS_FIELDS', '_COUNTERS_METHOD_ALIASES'),
        ('video_transform', '_VT_FIELDS',       '_VT_METHOD_ALIASES'),
        ('inference_exec',  '_IE_FIELDS',       '_IE_METHOD_ALIASES'),
        ('sequence_labels', '_SEQ_FIELDS',      '_SEQ_METHOD_ALIASES'),
    )

    def __getattr__(self, name):
        # __getattr__ 仅在常规查找未命中时触发
        cls = type(self)
        component_attrs = {r[0] for r in cls._COMPONENT_ROUTES}
        if name in component_attrs:
            raise AttributeError(name)
        d = self.__dict__
        for comp_attr, fields_name, methods_name in cls._COMPONENT_ROUTES:
            if comp_attr not in d:
                continue
            comp = d[comp_attr]
            if name in getattr(cls, fields_name):
                return getattr(comp, name)
            method_aliases = getattr(cls, methods_name)
            if name in method_aliases:
                return getattr(comp, method_aliases[name])
        raise AttributeError(
            f"{type(self).__name__!r} object has no attribute {name!r}"
        )

    def __setattr__(self, name, value):
        # 极少数路由/历史代码直接赋值组件字段, 拦截转发到对应组件
        cls = type(self)
        d = self.__dict__
        for comp_attr, fields_name, _ in cls._COMPONENT_ROUTES:
            if comp_attr in d and name in getattr(cls, fields_name):
                setattr(d[comp_attr], name, value)
                return
        super().__setattr__(name, value)

    # 配置文件路径
    CONFIG_FILE = os.path.join(DATA_DIR, 'device_config.json')
    
    def __init__(self, channel_id: int = 0):
        self.channel_id = channel_id  # workstation/channel index (0-based)
        self.source_type = None  # 'camera', 'video', 'image', 'hikvision', 'rtsp', 'hcnetsdk'
        self.capture = None
        self.is_running = False
        self.current_frame = None
        self.frame_lock = threading.Lock()
        self.capture_lock = threading.Lock()  # 保护 capture 对象的并发访问
        self.camera_index = 0
        self.video_path = None
        self.rtsp_url = None
        self.image_path = None
        self.width = 1280
        self.height = 720
        self.fps = 60
        self._thread = None
        
        # 海康工业相机相关
        self.hik_camera = None  # MvCamera 实例
        self.hik_device_index = 0  # 海康相机设备索引
        self.hik_payload_size = 0  # 海康相机帧数据大小
        self.hik_data_buf = None  # 海康相机数据缓冲区
        self.hik_frame_info = None  # 海康相机帧信息
        
        # HCNetSDK state
        self.hcnet_session = None  # HCNetSession 实例
        self.hcnet_ip = None
        self.hcnet_port = 8000
        self.hcnet_username = None
        self.hcnet_password = None
        self.hcnet_channel = 1
        
        # 帧率限制配置（用于MJPEG流）
        self.frame_limit_enabled = False  # 默认禁用节流（本地应用）
        self.target_stream_fps = 30  # 目标流帧率
        self.use_half = False  # FP16 半精度推理（默认关闭，用户可在设置中开启）
        
        # MediaPipe overlay (纯视觉叠加，默认关闭)
        # ===== MediaPipeOverlay 组件 (P7 第二刀) =====
        # 4 个 public 用户配置字段保留在 VSM 上 (routes 直接读写)
        self.mediapipe_enabled = False
        self.mediapipe_pose = True
        self.mediapipe_hands = True
        self.mediapipe_confidence = 0.7
        # 8 个内部 _mp_* 状态字段移至组件, __getattr__/__setattr__ 透明转发
        self.mp_overlay = MediaPipeOverlay(host=self)
        
        # 视频播放控制
        self.video_speed = 1.0  # 视频倍速
        self.video_ended = False  # 视频是否已结束
        self.video_total_frames = 0  # 视频总帧数
        self.video_current_frame = 0  # 当前帧位置
        self._progress_lock = threading.Lock()  # 防止进度设置并发调用
        self._setting_progress = False  # 正在设置进度的标志
        self._pending_progress = None  # 待处理的进度请求（记住最新值）

        # 画面几何变换（每通道独立；0°/90°/180°/270° + 水平/垂直镜像）
        # v2.7.14: "只翻显示, 不翻推理" —— 模型推理使用原图（raw_frame）保持训练时的视角，
        # MJPEG/录像/快照/step_screenshot 使用 display_frame（翻转后）保证显示观感。
        # 推理输出的 bbox（原图归一化坐标）在 _inference_loop 里立即通过
        # _map_detections_original_to_display 映射到显示坐标系, 下游 ROI/容器/步骤/前端画框
        # 全部基于显示坐标系运行, 无需二次适配。
        # P7 第四刀: 旋转/镜像/坐标映射归 self.video_transform 组件,
        # 通过 __getattr__/__setattr__ 兼容层让 self.video_rotation 等透明转发
        self.video_transform = VideoTransform()
        
        # YOLO 模型
        self.model = None
        self.model_path = None
        self._original_pt_path = None  # 原始 .pt 路径，用于转换模型加载失败时回退
        self._is_native_pytorch = True  # 是否为原生 .pt 模型（导出格式不支持 .to() 和 half）
        self._model_imgsz = 640  # 模型推理分辨率，TensorRT 模型会自动检测
        self.model_task = 'detect'  # 'detect' or 'segment', updated on load_model
        self.device = 'auto'  # 推理设备: 'auto', 'cpu', 'cuda:0', 'cuda:1' 等
        self.current_device_info = None  # 当前使用的设备信息
        self.is_detecting = False
        self.current_detections = []
        self.detection_lock = threading.Lock()
        
        # MES Hook (延迟注入, 由 main.py 启动时设置)
        self._mes_hook = None
        
        # 加载保存的设备配置
        self._load_device_config()
        
        # 初始化推理相关变量（必须在_load_device_config之后）
        self._init_inference_vars()
    
    def _load_device_config(self):
        """从文件加载设备配置

        全局字段（device/mediapipe/frame_limit 等）所有通道共用；
        per_channel[str(channel_id)] 保存每通道独立的画面变换（rotation/flip）。
        """
        try:
            if os.path.exists(self.CONFIG_FILE):
                import json
                with open(self.CONFIG_FILE, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    self.device = config.get('device', 'auto')
                    self.frame_limit_enabled = config.get('frame_limit_enabled', False)
                    self.target_stream_fps = config.get('target_stream_fps', 30)
                    self.use_half = config.get('use_half', False)
                    self.mediapipe_enabled = config.get('mediapipe_enabled', False)
                    self.mediapipe_pose = config.get('mediapipe_pose', True)
                    self.mediapipe_hands = config.get('mediapipe_hands', True)
                    self.mediapipe_confidence = config.get('mediapipe_confidence', 0.7)
                    self._mp_process_interval = config.get('mediapipe_interval', 2)

                    per_ch = (config.get('per_channel') or {}).get(str(self.channel_id), {})
                    rot = per_ch.get('rotation', 0)
                    self.video_rotation = rot if rot in (0, 90, 180, 270) else 0
                    self.video_flip_h = bool(per_ch.get('flip_h', False))
                    self.video_flip_v = bool(per_ch.get('flip_v', False))

                    print(f"已加载设备配置: 设备={self.device}, 帧率限制={self.frame_limit_enabled}, "
                          f"FP16={self.use_half}, MediaPipe={self.mediapipe_enabled}, "
                          f"ch{self.channel_id} rot={self.video_rotation} "
                          f"flip_h={self.video_flip_h} flip_v={self.video_flip_v}")
        except Exception as e:
            print(f"加载设备配置失败: {e}")

    def _save_device_config(self):
        """保存设备配置到文件（保留其它通道的 per_channel 设置不被覆盖）"""
        try:
            import json
            os.makedirs(os.path.dirname(self.CONFIG_FILE), exist_ok=True)

            existing = {}
            if os.path.exists(self.CONFIG_FILE):
                try:
                    with open(self.CONFIG_FILE, 'r', encoding='utf-8') as f:
                        existing = json.load(f) or {}
                except Exception:
                    existing = {}

            per_channel = existing.get('per_channel') or {}
            per_channel[str(self.channel_id)] = {
                'rotation': int(self.video_rotation) if self.video_rotation in (0, 90, 180, 270) else 0,
                'flip_h': bool(self.video_flip_h),
                'flip_v': bool(self.video_flip_v),
            }

            config = {
                'device': self.device,
                'frame_limit_enabled': self.frame_limit_enabled,
                'target_stream_fps': self.target_stream_fps,
                'use_half': self.use_half,
                'mediapipe_enabled': self.mediapipe_enabled,
                'mediapipe_pose': self.mediapipe_pose,
                'mediapipe_hands': self.mediapipe_hands,
                'mediapipe_confidence': self.mediapipe_confidence,
                'mediapipe_interval': self._mp_process_interval,
                'per_channel': per_channel,
            }
            with open(self.CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            print(f"设备配置已保存: {config}")
        except Exception as e:
            print(f"保存设备配置失败: {e}")

    # _apply_frame_transform / _has_display_transform / _map_bbox_original_to_display /
    # _map_detections_original_to_display 已迁至 source_video_transform.py (P7 第四刀)
    # 历史调用通过 VSM.__getattr__ 转发到 self.video_transform


    def _init_inference_vars(self):
        """初始化推理相关变量 (在 __init__ 的 _load_device_config 之后调用)

        实现已迁至 source_state_init.py (P7 第十刀), 按主题切分为 13 个 init helper.
        本方法保留为薄 wrapper 以保持原 API 不变.
        """
        from backend.api.source_state_init import init_state
        init_state(self)

        
    def set_project_config(self, config: dict):
        """设置项目配置 → 应用全部步骤/周期/计数器状态

        实现已迁至 source_project_config_apply.py (P7 第十一刀),
        按主题切分为 8 个 helper. 本方法保留为薄 wrapper.
        """
        from backend.api.source_project_config_apply import apply_project_config
        apply_project_config(self, config)

    
    def _release_model(self):
        """释放模型和 GPU 资源"""
        try:
            import torch
            import gc
            
            # 先关闭推理线程池
            self._shutdown_inference_executor()
            
            if self.model is not None:
                print("[资源释放] 开始释放模型资源...")
                
                # 1. 等待 CUDA 操作完成
                if torch.cuda.is_available():
                    torch.cuda.synchronize()
                
                del self.model
                self.model = None
                self.model_task = 'detect'
                
                # 3. Python 垃圾回收
                gc.collect()
                
                # 4. 清理 CUDA 缓存
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                    # 获取显存使用情况
                    allocated = torch.cuda.memory_allocated() / 1024**2
                    cached = torch.cuda.memory_reserved() / 1024**2
                    print(f"[资源释放] 显存状态: 已分配={allocated:.1f}MB, 缓存={cached:.1f}MB")
                
                print("[资源释放] 模型资源已释放")
        except Exception as e:
            print(f"[资源释放] 释放模型时出错: {e}")
    
    def _supplement_step_durations(self):
        """Supplement step_durations for steps still being tracked at settle time.
        Must be called BEFORE clearing step_last_seen / step_start_time."""
        for label, last_time in list(self.step_last_seen.items()):
            start_time = self.step_start_time.get(label, last_time)
            duration = last_time - start_time
            if duration >= 0:
                time_config = self.step_time_config.get(label, {})
                min_dur = time_config.get('min_duration')
                max_dur = time_config.get('max_duration')
                is_valid = True
                if min_dur is not None and duration < min_dur:
                    is_valid = False
                if max_dur is not None and duration > max_dur:
                    is_valid = False
                if is_valid:
                    rounded_dur = round(duration, 2)
                    self.step_durations[label] = rounded_dur
                    self.step_durations_history.setdefault(label, []).append(rounded_dur)
                    if label not in self.step_counts:
                        self.step_counts[label] = 0
                    self.step_counts[label] += 1
                    if self.last_step_completed_time is not None:
                        interval = start_time - self.last_step_completed_time
                        self.step_intervals[label] = round(interval, 2)
                    else:
                        self.step_intervals[label] = 0
                    self.last_step_completed_time = last_time
    
    def _backfill_step_records(self):
        """Backfill StepRecords for steps in current_cycle_steps that were not
        yet recorded via the normal disappearance handler.  Called from both
        _settle_sequential_cycle and _settle_custom_cycle so that the DB always
        has a complete set of step records matching current_cycle_steps."""
        if not self.current_cycle_id or not self.recording_enabled:
            return
        try:
            db = self._get_db_session()
            existing = db.query(StepRecord.step_label).filter(
                StepRecord.cycle_id == self.current_cycle_id).all()
            recorded_labels = {r.step_label for r in existing}
            max_order = db.query(StepRecord.step_order).filter(
                StepRecord.cycle_id == self.current_cycle_id).all()
            next_order = (max(o[0] for o in max_order) + 1) if max_order else 1
            db.close()

            cycle_end_time = time.time()
            if getattr(self, '_last_disappeared_step_times', None):
                last_step_label = self.current_cycle_steps[-1] if self.current_cycle_steps else None
                if last_step_label and last_step_label in self._last_disappeared_step_times:
                    cycle_end_time = self._last_disappeared_step_times[last_step_label]

            for label in self.current_cycle_steps:
                if label in recorded_labels:
                    continue
                start_t = self.step_start_time.get(label, cycle_end_time)
                last_t = self.step_last_seen.get(label, cycle_end_time)
                duration = max(0, last_t - start_t)

                time_cfg = self.step_time_config.get(label, {})
                min_dur = time_cfg.get('min_duration')
                if min_dur is not None and min_dur > 0 and duration < min_dur:
                    print(f"[backfill skip] {label}: duration {duration:.3f}s < min_duration {min_dur}s")
                    continue
                if min_dur is None and duration < 0.1:
                    print(f"[backfill skip] {label}: duration {duration:.3f}s < 0.1s (no min_duration configured)")
                    continue

                step_name = self.step_display_names.get(label, label)
                step_video_info = self.stop_step_recording(label)
                self.record_step(
                    step_label=label, step_name=step_name,
                    start_time=start_t, end_time=last_t,
                    duration=duration,
                    interval=self.step_intervals.get(label),
                    confidence=None, is_valid=True,
                    video_info=step_video_info,
                    step_order=next_order)
                next_order += 1
        except Exception as e:
            print(f"补写步骤记录失败: {e}")
            import traceback
            traceback.print_exc()
    
    def _generate_custom_tracker_yaml(self, pipeline_config: dict):
        """Generate a custom bytetrack.yaml with track_buffer synced to max_lost_seconds."""
        import tempfile, os
        max_lost_sec = 0.0
        for step in self.project_config.get('steps_config', []):
            if step.get('enabled', True) and step.get('tracking_max_lost_seconds') is not None:
                max_lost_sec = max(max_lost_sec, step['tracking_max_lost_seconds'])
        if max_lost_sec <= 0:
            max_lost_sec = 5.0
        # v2.7.13: ByteTrack 每次推理 step 一次, 所以 track_buffer 要按 推理 FPS 算
        # 生成时机比推理线程先启动, fps_inference 可能还是 0, 用 10 作为默认估算
        fps = max(self.fps_inference, 10)
        track_buffer = max(30, int(max_lost_sec * fps))

        # v3.1.2: match_thresh 暴露到项目设置 (容器/多目标场景下放低能减少 ID 漂移,
        #         默认 0.8 是 ByteTrack 官方默认, 现场可调到 0.5~0.95 之间)
        try:
            match_thresh = float(pipeline_config.get('tracking_match_thresh', 0.8))
        except (TypeError, ValueError):
            match_thresh = 0.8
        match_thresh = max(0.1, min(0.99, match_thresh))

        yaml_content = (
            f"tracker_type: bytetrack\n"
            f"track_high_thresh: 0.25\n"
            f"track_low_thresh: 0.1\n"
            f"new_track_thresh: 0.25\n"
            f"track_buffer: {track_buffer}\n"
            f"match_thresh: {match_thresh}\n"
            f"fuse_score: true\n"
        )
        yaml_path = os.path.join(tempfile.gettempdir(), f'bytetrack_custom_{id(self)}.yaml')
        with open(yaml_path, 'w') as f:
            f.write(yaml_content)
        self._custom_tracker_yaml = yaml_path
        print(f"[Tracking] Custom tracker config: track_buffer={track_buffer} match_thresh={match_thresh} "
              f"(max_lost={max_lost_sec}s, fps={fps:.0f})")

    @staticmethod
    def _bbox_iou(a: dict, b: dict) -> float:
        """Compute IoU between two {x,y,w,h} bounding boxes (normalized coords)."""
        ax1, ay1, ax2, ay2 = a['x'], a['y'], a['x'] + a['w'], a['y'] + a['h']
        bx1, by1, bx2, by2 = b['x'], b['y'], b['x'] + b['w'], b['y'] + b['h']
        ix1, iy1 = max(ax1, bx1), max(ay1, by1)
        ix2, iy2 = min(ax2, bx2), min(ay2, by2)
        inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
        area_a = a['w'] * a['h']
        area_b = b['w'] * b['h']
        union = area_a + area_b - inter
        return inter / union if union > 0 else 0.0

    @staticmethod
    def _bbox_center_dist(a: dict, b: dict) -> float:
        """Euclidean distance between bbox centers (normalized coords)."""
        acx, acy = a['x'] + a['w'] / 2, a['y'] + a['h'] / 2
        bcx, bcy = b['x'] + b['w'] / 2, b['y'] + b['h'] / 2
        return ((acx - bcx) ** 2 + (acy - bcy) ** 2) ** 0.5

    def _try_reid_match(self, label: str, new_bbox: dict, candidate_bbox: dict,
                        max_size_ratio: float = 3.0) -> bool:
        """Check if new_bbox likely matches candidate_bbox for re-ID.
        Uses IoU > 0.2 OR center distance < max(w,h) of the larger bbox."""
        iou = self._bbox_iou(candidate_bbox, new_bbox)
        if iou > 0.2:
            return True
        dist = self._bbox_center_dist(candidate_bbox, new_bbox)
        ref_size = max(candidate_bbox['w'], candidate_bbox['h'],
                       new_bbox['w'], new_bbox['h'])
        if ref_size > 0 and dist < ref_size * 1.5:
            w_ratio = max(candidate_bbox['w'], new_bbox['w']) / max(min(candidate_bbox['w'], new_bbox['w']), 1e-6)
            h_ratio = max(candidate_bbox['h'], new_bbox['h']) / max(min(candidate_bbox['h'], new_bbox['h']), 1e-6)
            if w_ratio < max_size_ratio and h_ratio < max_size_ratio:
                return True
        return False

    def _boost_score_with_appearance(self, iou_score: float, display_id: str,
                                       new_bbox: dict, frame) -> float:
        """Boost matching score using stored appearance histogram."""
        if frame is None or display_id not in self._tracking_appearance:
            return iou_score
        try:
            import cv2
            h_img, w_img = frame.shape[:2]
            x1 = max(0, int(new_bbox['x'] * w_img))
            y1 = max(0, int(new_bbox['y'] * h_img))
            x2 = min(w_img, int((new_bbox['x'] + new_bbox['w']) * w_img))
            y2 = min(h_img, int((new_bbox['y'] + new_bbox['h']) * h_img))
            if x2 - x1 < 5 or y2 - y1 < 5:
                return iou_score
            crop = frame[y1:y2, x1:x2]
            hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
            hist = cv2.calcHist([hsv], [0, 1], None, [16, 16], [0, 180, 0, 256])
            cv2.normalize(hist, hist)
            stored = self._tracking_appearance[display_id]
            similarity = cv2.compareHist(stored, hist, cv2.HISTCMP_CORREL)
            return iou_score * 0.5 + max(0, similarity) * 0.5
        except Exception:
            return iou_score

    def _get_display_prefix(self, class_name: str) -> str:
        """Get display name prefix for tracking IDs (e.g. '立柱' instead of 'A')."""
        if class_name not in self._tracking_letter_map:
            display_name = self.step_display_names.get(class_name, class_name)
            self._tracking_letter_map[class_name] = display_name
            self._tracking_letter_idx += 1
        return self._tracking_letter_map[class_name]
    
    def _reset_counting_cycle(self):
        """Clear all tracking-mode state for the next cycle."""
        self._tracking_objects.clear()
        self._tracking_class_counters.clear()
        self._tracking_display_map.clear()
        self._tracking_lost_frames.clear()
        self._tracking_letter_map.clear()
        self._tracking_letter_idx = 0
        self._tracking_item_checklist.clear()
        self._tracking_order_seq = 0
        self._tracking_prev_count = 0
        self._tracking_gone_frames = 0
        self._tracking_cycle_active = False
        self._tracking_had_roi_objects = False
        self._tracking_trigger_frames = 0
        # v3.3.0 scan_pair: 清掉 sticky 'was_complete' 防止跨周期串台
        self._tracking_was_complete = False
        self._tracking_recently_lost.clear()
        self._tracking_transferred_ids.clear()
        self._tracking_prev_positions.clear()
        self._tracking_appearance.clear()
        self._tracking_stable_frames.clear()
        self._tracking_locked_ids.clear()
        self._tracking_registered_positions.clear()
        self.current_cycle_steps = []
        self.last_added_step = None
        self.step_last_seen.clear()
        self.step_start_time.clear()
        self.step_consecutive_frames.clear()
        self.step_frame_confirmed.clear()
        self._step_gap_count.clear()
        
        self.last_step_completed_time = None
        
        # Event counting mode reset
        self._event_counters.clear()
        self._event_state.clear()
        self._event_visible_frames.clear()
        self._event_gone_frames_count.clear()
        self._event_first_seen.clear()
        self._event_last_seen.clear()

        # v2.7.4: Stack mode reset
        self._stack_state.clear()
        self._stack_counters.clear()
        self._stack_disappeared_at.clear()
        self._stack_visible_frames.clear()
        
        # Container mode reset
        self._box_objects.clear()
        self._box_counter = 0
        self._box_settled_results = []
    
    def _is_in_roi(self, det: dict) -> bool:
        """Check if detection center falls within the configured ROI polygon (ray-casting)."""
        if not self.project_config:
            return True
        roi = self.project_config.get('pipeline_config', {}).get('tracking_roi')
        if not roi or not roi.get('enabled'):
            return True
        polygon = roi.get('polygon', [])
        if len(polygon) < 3:
            return True
        cx = det['x'] + det['w'] / 2
        cy = det['y'] + det['h'] / 2
        return self._point_in_polygon(cx, cy, polygon)
    
    @staticmethod
    def _point_in_polygon(px: float, py: float, polygon: list) -> bool:
        """Ray-casting algorithm for point-in-polygon test. polygon = [[x,y], ...]"""
        n = len(polygon)
        inside = False
        j = n - 1
        for i in range(n):
            xi, yi = polygon[i]
            xj, yj = polygon[j]
            if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / (yj - yi) + xi):
                inside = not inside
            j = i
        return inside
    
    
    # _get_inference_executor / _shutdown_inference_executor 已迁至
    # source_inference_executor.py (P7 第五刀), 历史调用通过 __getattr__ 转发
    
    def _apply_rod_filters(self, detections: list) -> list:
        """统一应用两层传动杆过滤：companion 空间共现 + session gate。
        两层独立开关，全在 project_config 里配置；默认全关，老项目零影响。
        """
        if not detections:
            return detections
        cfg = getattr(self, "_rod_filter_cfg", None) or {}
        try:
            if cfg.get("companion_enabled"):
                detections = filter_rod_by_companion(
                    detections,
                    iou_thr=cfg.get("companion_iou_thr", 0.25),
                    rod_label=cfg.get("companion_rod_label", "传动杆"),
                    companion_labels=cfg.get("companion_labels", ("大框架", "小框架", "侧板")),
                )
            if cfg.get("gate_enabled") and getattr(self, "_rod_gate", None) is not None:
                detections = self._rod_gate.update_and_filter(detections)
        except Exception as _e:
            # 过滤失败绝不挡推理主流程
            print(f"[rod_filter] apply failed: {_e}")
        return detections

    def _get_enabled_labels(self) -> set:
        """Helper: collect enabled step labels from project config."""
        labels = set()
        if self.project_config:
            for step in self.project_config.get('steps_config', []):
                if step.get('enabled', True):
                    lbl = step.get('label', '')
                    if lbl:
                        labels.add(lbl)
        return labels
    
    def _emergency_gpu_reset(self):
        """紧急 GPU 重置 - 当推理持续超时时调用"""
        try:
            import torch
            import gc
            
            print("[GPU重置] 开始紧急 GPU 重置...")
            
            # 1. 同步 CUDA
            if torch.cuda.is_available():
                torch.cuda.synchronize()
            
            # 2. 清理缓存
            gc.collect()
            
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                
            # 3. 重置 CUDA 设备（谨慎使用）
            if torch.cuda.is_available():
                # 获取当前设备
                current_device = torch.cuda.current_device()
                # 重置设备
                torch.cuda.reset_peak_memory_stats(current_device)
                
            print("[GPU重置] 紧急 GPU 重置完成")
            
        except Exception as e:
            print(f"[GPU重置] 重置失败: {e}")
    
    # ========== 推理线程相关方法 ==========
    
    def _start_inference_thread(self):
        """启动独立推理线程"""
        if self._inference_thread is not None and self._inference_thread.is_alive():
            return  # 已在运行
        
        self._inference_running = True
        self._inference_thread = threading.Thread(target=self._inference_loop, daemon=True)
        self._inference_thread.start()
        print("[推理线程] 已启动")
    
    def _stop_inference_thread(self):
        """停止推理线程"""
        self._inference_running = False
        thread = self._inference_thread
        if thread is not None:
            thread.join(timeout=2.0)
            if thread.is_alive():
                print("[警告] 推理线程未能在超时内结束，可能存在死锁")
        self._inference_thread = None
        
        # CUDA 同步确保所有 GPU 操作完成
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.synchronize()
        except Exception as e:
            print(f"[警告] CUDA 同步失败: {e}")
        
        print("[推理线程] 已停止")
    
    def _get_confirmed_detections(self, detections):
        """
        获取已确认的检测结果（通过帧计数验证的）
        """
        confirmed = []
        for det in detections:
            label = det['label']
            # 只有已确认的标签才加入
            if self.step_frame_confirmed.get(label, False):
                confirmed.append(det)
        return confirmed
    
    def set_video_speed(self, speed: float):
        """设置视频播放倍速"""
        if speed < 0.25 or speed > 16:
            raise ValueError("倍速必须在 0.25 到 16 之间")
        self.video_speed = speed
        print(f"[Video] 倍速已设置为: {speed}x")
    
    def set_video_progress(self, progress: float):
        """设置视频播放进度 (0-1) - 修复版：记住最新请求"""
        if self.source_type != 'video':
            raise Exception("当前不是视频输入源")
        
        if progress < 0 or progress > 1:
            raise ValueError("进度必须在 0 到 1 之间")
        
        # 使用锁防止并发调用（拖动进度条可能触发多次请求）
        if not self._progress_lock.acquire(blocking=False):
            # 记住最新的进度请求，等当前处理完后执行
            self._pending_progress = progress
            debug_log(f"记住待处理进度: {progress*100:.1f}%", "PROGRESS")
            return
        
        try:
            self._setting_progress = True
            self._pending_progress = None  # 清除待处理请求
            self._do_set_video_progress(progress)
            
            # 检查是否有待处理的进度请求
            while self._pending_progress is not None:
                pending = self._pending_progress
                self._pending_progress = None
                debug_log(f"处理待处理进度: {pending*100:.1f}%", "PROGRESS")
                self._do_set_video_progress(pending)
        
        finally:
            self._setting_progress = False
            self._progress_lock.release()
    
    def _do_set_video_progress(self, progress: float):
        """实际执行进度设置"""
        # 1. 记住当前状态
        was_running = self.is_running
        was_detecting = self.is_detecting

        # 2. 停止线程
        self.is_running = False
        self._inference_running = False

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.5)
        if self._inference_thread and self._inference_thread.is_alive():
            self._inference_thread.join(timeout=0.3)
            self._inference_thread = None

        # 3. 设置视频位置
        with self.capture_lock:
            if self.capture is None or not self.capture.isOpened():
                if self.video_path and os.path.exists(self.video_path):
                    self.capture = cv2.VideoCapture(self.video_path)

            if self.capture and self.capture.isOpened():
                target_frame = int(self.video_total_frames * progress)
                self.capture.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
                self.video_current_frame = target_frame
                self.video_ended = False

                # 暂停状态下读一帧用于预览，让用户看到拖动后的画面
                if not was_running:
                    ret, frame = self.capture.read()
                    if ret:
                        with self.frame_lock:
                            self.current_frame = frame
                        self.capture.set(cv2.CAP_PROP_POS_FRAMES, target_frame)

        # 4. 只有之前在运行状态时才重新启动线程
        if was_running:
            self.is_running = True
            self._thread = threading.Thread(target=self._capture_loop, daemon=True)
            self._thread.start()

            if was_detecting and self.model is not None:
                self.is_detecting = True
                self._start_inference_thread()

        print(f"[Video] 进度: {progress*100:.1f}%, was_running={was_running}, was_detecting={was_detecting}")
    
    def get_video_info(self):
        """获取视频播放信息"""
        if self.source_type != 'video':
            return None
        
        return {
            "total_frames": self.video_total_frames,
            "current_frame": self.video_current_frame,
            "progress": self.video_current_frame / max(self.video_total_frames, 1),
            "speed": self.video_speed,
            "ended": self.video_ended,
            "fps": self.fps,
            "duration": self.video_total_frames / max(self.fps, 1),
            "current_time": self.video_current_frame / max(self.fps, 1)
        }
    
    def set_image(self, image_path: str):
        """设置图片为输入源"""
        self.stop(release_model=False)
        
        if not os.path.exists(image_path):
            raise Exception(f"图片文件不存在: {image_path}")
        
        frame = cv2.imread(image_path)
        if frame is None:
            raise Exception(f"无法读取图片: {image_path}")
        
        self.source_type = 'image'
        self.image_path = image_path
        
        with self.frame_lock:
            self.current_frame = frame
            self._frame_seq += 1
        self.is_running = True
        
        if self.is_detecting and self.model is not None:
            self._run_image_inference(frame)
        
        return True
    
    def _run_image_inference(self, frame: np.ndarray):
        """Run inference on a single image frame and update detection results."""
        try:
            _task_type = self.project_config.get('task_type', 'detection') if self.project_config else 'detection'
            _logic_mode = self.project_config.get('logic_mode', 'sequential') if self.project_config else 'sequential'
            _is_tracking = (_logic_mode == 'tracking')
            _is_seg = (_task_type == 'segmentation')
            
            if _is_tracking:
                detections = self._detect_and_track(frame)
            elif _is_seg:
                detections = self._detect_segment(frame)
            else:
                detections = self._detect_only(frame)
            
            if _is_tracking:
                # 关键: yolo tracker 会**回收**消失对象的 track_id, 让新出现的不同类
                # 物体复用同一个 id. 此时 _tracking_display_map[tid] 还是旧类的 display_id,
                # 直接赋给新 det 会出现 label='线槽' 但 display_id='泡沫槽2' 的错位.
                # 这里在赋 display_id 之前先校验 class_name 一致性, 不一致就丢弃旧映射,
                # 让 _detect_and_track 下一帧重新走 phase2 分配新 display_id.
                for det in detections:
                    tid = det.get('track_id', -1)
                    if tid in self._tracking_display_map:
                        cur_obj = self._tracking_objects.get(tid)
                        if cur_obj is None or cur_obj.get('class_name') == det.get('label'):
                            det['display_id'] = self._tracking_display_map[tid]
                        else:
                            # 类别变了 → 老 display_id 报废, 不输出错位的 display
                            self._tracking_display_map.pop(tid, None)
                            self._tracking_objects.pop(tid, None)
                            self._tracking_lost_frames.pop(tid, None)
                confirmed = detections
            else:
                confirmed = detections
            
            with self.detection_lock:
                self.current_detections = confirmed
            with self._confirmed_detections_lock:
                self._confirmed_detections = confirmed
            
            print(f"[Image] 图片推理完成, 检测到 {len(confirmed)} 个目标")
        except Exception as e:
            print(f"[Image] 图片推理失败: {e}")
            import traceback; traceback.print_exc()
    
    def start_detection(self, model_path: str = None):
        """开始检测"""
        if model_path and (self.model is None or self.model_path != model_path):
            if not self.load_model(model_path):
                raise Exception("模型加载失败")
        
        if self.model is None:
            raise Exception("未加载模型")
        
        # Camera released during pause → re-open before starting
        if not self.is_running and self.capture is None and self.source_type == 'camera':
            if not self._reopen_camera():
                raise Exception("摄像头打开失败，请检查设备")
        
        if not self.is_running and self.source_type == 'hikvision' and self.hik_camera is None:
            if not self._reopen_hik_camera():
                raise Exception("海康相机打开失败，请检查设备")
        
        if not self.is_running and self.source_type == 'hcnetsdk' and self.hcnet_session is None:
            try:
                self._reconnect_hcnetsdk()
            except Exception as e:
                raise Exception(f"HCNetSDK reconnect failed: {e}")
        
        # 如果视频源暂停（有 capture 但 is_running=False），自动恢复
        if not self.is_running and (
            (self.capture is not None and self.capture.isOpened()) or
            (self.source_type == 'hikvision' and self.hik_camera is not None) or
            (self.source_type == 'hcnetsdk' and self.hcnet_session is not None)
        ):
            print("[自动恢复] 检测到暂停的视频源，自动恢复播放")
            if self._thread and self._thread.is_alive():
                self._thread.join(timeout=1.0)
            if self.source_type == 'video':
                self.capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
                self.video_current_frame = 0
                self.video_ended = False
            self.is_running = True
            self._thread = threading.Thread(target=self._capture_loop, daemon=True)
            self._thread.start()
        
        self.is_detecting = True
        
        try:
            from backend.services.scanner import get_scanner_service
            print(f"[Scanner/Source] start_detection ch={self.channel_id} → start_scanning")
            get_scanner_service().start_scanning(channel_id=self.channel_id)
        except Exception as _e:
            import traceback as _tb
            print(f"[Scanner/Source] start_scanning 失败: {_e}\n{_tb.format_exc()}")
        
        try:
            from backend.api.alarm import alarm_router
            alarm_router.start_idle_light(channel_id=self.channel_id)
        except Exception as _e:
            print(f"[Alarm/Source] start_idle_light 失败: {_e}")
        
        if self.source_type == 'image':
            frame = self.get_frame()
            if frame is not None and self.model is not None:
                self._run_image_inference(frame)
            print("图片检测已启动")
            return True
        
        if self.is_running and self.model is not None:
            self._start_inference_thread()
        
        self._ensure_session_active()
        
        if self.recording_enabled:
            self._start_recording_thread()
        
        print("检测已启动")
        return True
    
    def stop_detection(self):
        """停止检测"""
        self.is_detecting = False
        
        # 停止检测 → 重置 RodSessionGate，避免下次开机复用残留记忆
        if getattr(self, "_rod_gate", None) is not None:
            try:
                self._rod_gate.reset()
            except Exception:
                pass

        try:
            from backend.services.scanner import get_scanner_service
            print(f"[Scanner/Source] stop_detection ch={self.channel_id} → stop_scanning")
            get_scanner_service().stop_scanning(channel_id=self.channel_id)
        except Exception as _e:
            import traceback as _tb
            print(f"[Scanner/Source] stop_scanning 失败: {_e}\n{_tb.format_exc()}")
        
        try:
            from backend.api.alarm import alarm_router
            alarm_router.stop_idle_light(channel_id=self.channel_id)
        except Exception as _e:
            print(f"[Alarm/Source] stop_idle_light 失败: {_e}")
        
        # 停止推理线程
        self._stop_inference_thread()
        
        # 关闭推理线程池
        self._shutdown_inference_executor()
        
        # 停止录制线程
        self._stop_recording_thread()
        
        with self.detection_lock:
            self.current_detections = []
        
        # 清理推理相关缓存
        self._clear_inference_caches()
        
        # v2.7.2: 清空事件日志与序号，避免下次启动时 30 秒窗口内的旧事件被前端当成新事件推送
        # 症状：停止→切换工位/模型→重新开始后，Monitor 同时弹出历史 OK+NG+未扫码 toast
        self.events_log = []
        self._event_seq = 0
        
        print("检测已停止")
    
    def _clear_inference_caches(self):
        """清理推理相关缓存 - 停止检测时调用"""
        import gc
        
        # 清理卡尔曼滤波器
        self._kalman_filters.clear()
        self._detection_missing_frames.clear()
        
        # 清理推理帧缓存
        with self._inference_frame_lock:
            self._latest_frame_for_inference = None
            self._latest_frame_original_size = None
            self._latest_display_small_for_stats = None
        with self._confirmed_detections_lock:
            self._confirmed_detections = []
        
        # 清理帧计数状态
        self.step_consecutive_frames.clear()
        self.step_frame_confirmed.clear()
        self._step_gap_count.clear()
        
        
        # 重置同时出现组状态
        self._sim_group_buffers = {}
        
        # 限制事件日志大小
        if len(self.events_log) > 500:
            self.events_log = self.events_log[-500:]
        
        # 轻量级垃圾回收
        gc.collect(generation=0)
        
        # 清理 CUDA 缓存
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except:
            pass
        
        print("[缓存清理] 推理缓存已清理")
    
    def reset_stats(self):
        """Full detection state reset (everything except video source, model and project config)."""
        import gc
        
        # Step statistics
        self.step_counts = {}
        self.step_screenshots = {}
        self.step_last_seen = {}
        self.step_start_time = {}
        self.step_detection_times = {}
        self.step_durations = {}
        self.step_intervals = {}
        self.last_step_completed_time = None
        
        # Counters (preserve names, zero values)
        for key in self.counters:
            self.counters[key] = 0
        self._persist_counters()
        
        # Cycle state
        self.current_cycle_steps = []
        self.backup_steps_seen_in_cycle = set()
        self.last_added_step = None
        self.cycle_complete = False
        self.events_log = []
        self._event_seq = 0
        self.ng_step_cycle_counts = {}
        
        # Cycle timing
        self.cycle_times = []
        self.ng_cycle_times = []
        self.cycle_start_time = None
        self.step_durations_history = {}
        
        # Settlement / first-step tracking flags
        self._first_step_had_gap = False
        self._first_step_reconfirmed = False
        self._first_step_disappeared_at = None
        self._last_step_added_time = None
        self._cycle_regression = False
        self._step_raw_start = {}
        self._last_ng_time = 0
        if hasattr(self, '_last_disappeared_step_times'):
            self._last_disappeared_step_times = {}
        
        # Simultaneous-group buffer layer
        self._sim_group_buffers = {}
        self._currently_pending_labels = set()
        
        # Kalman tracking filters
        self._kalman_filters.clear()
        self._detection_missing_frames.clear()
        self._detection_history.clear()
        
        # Frame-counting state
        self.step_consecutive_frames.clear()
        self.step_frame_confirmed.clear()
        self._step_gap_count.clear()
        self.step_static_triggered.clear()
        
        # Tracking-mode (counting) state
        if hasattr(self, '_tracking_objects'):
            self._reset_counting_cycle()
        
        gc.collect()
        
        print("统计数据已完全重置（含所有检测状态）")
    
    def get_frame(self):
        """获取当前帧"""
        with self.frame_lock:
            if self.current_frame is not None:
                return self.current_frame.copy()
        return None
    
    def get_detections(self):
        """获取当前检测结果"""
        with self.detection_lock:
            return self.current_detections.copy()
    
    def _encode_and_yield(self, frame):
        """Encode a frame to JPEG and return the MJPEG chunk bytes."""
        ret, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 65])
        if ret:
            data = (b'--frame\r\n'
                    b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')
            del buffer
            return data
        del buffer
        return None

    def _get_placeholder_frame(self):
        """Return a small black placeholder frame for when no real frame is available."""
        import numpy as np
        black = np.zeros((480, 640, 3), dtype=np.uint8)
        return black

    def generate_mjpeg(self):
        """Generate MJPEG stream.  Only encodes and sends when a genuinely new
        frame is available from the capture thread, so CPU is never wasted on
        duplicate JPEG encodes.

        v2.7.15 (C): try/finally + 异常捕获, 客户端断开时立即释放资源,
        并维护 _mjpeg_active_streams 计数方便诊断"连接是否累积"。
        """
        from backend.api.channel_manager import channel_manager
        target_interval = 1.0 / max(self.target_stream_fps, 1)
        num_ch = max(channel_manager.channel_count, 1)
        min_interval = max(0.02, 0.015 * num_ch)
        idle_count = 0
        max_idle = 600
        last_seq = -1

        # v2.7.15 (C): 活跃 MJPEG 连接计数
        try:
            self._mjpeg_active_streams = getattr(self, '_mjpeg_active_streams', 0) + 1
            print(f"[MJPEG] 新连接 ch={getattr(self, 'channel_index', '?')}, 活跃连接={self._mjpeg_active_streams}")
        except Exception:
            pass

        try:
            while True:
                if self.is_running:
                    idle_count = 0
                    frame = None
                    with self.frame_lock:
                        seq = self._frame_seq
                        if seq != last_seq and self.current_frame is not None:
                            frame = self.current_frame.copy()
                            last_seq = seq

                    if frame is None:
                        time.sleep(0.005)
                        continue

                    chunk = self._encode_and_yield(frame)
                    del frame
                    if chunk:
                        yield chunk

                    if self.frame_limit_enabled:
                        time.sleep(max(min_interval, target_interval))
                    else:
                        time.sleep(min_interval)
                else:
                    frame = self.get_frame()
                    if frame is None:
                        frame = self._get_placeholder_frame()
                    chunk = self._encode_and_yield(frame)
                    del frame
                    if chunk:
                        yield chunk

                    idle_count += 1
                    if idle_count > max_idle:
                        break
                    # 短间隔检查，以便 is_running 变 True 时快速恢复
                    for _ in range(10):
                        if self.is_running:
                            break
                        time.sleep(0.1)
        except (GeneratorExit, ConnectionResetError, BrokenPipeError):
            # 客户端断开, 正常退出
            pass
        except Exception as e:
            print(f"[MJPEG] generator 异常退出 ch={getattr(self, 'channel_index', '?')}: {e}")
        finally:
            try:
                self._mjpeg_active_streams = max(0, getattr(self, '_mjpeg_active_streams', 1) - 1)
                print(f"[MJPEG] 连接关闭 ch={getattr(self, 'channel_index', '?')}, 活跃连接={self._mjpeg_active_streams}")
            except Exception:
                pass
    
    def get_snapshot(self):
        """获取当前帧的单张 JPEG 快照（用于前端 canvas 渲染）"""
        frame = self.get_frame()
        if frame is not None:
            ret, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
            if ret:
                return buffer.tobytes()
        return None

# Global video_manager is now a property of the ChannelManager singleton.
# Kept here for backward compatibility — always points to channel 0.
def _get_default_manager():
    from backend.api.channel_manager import channel_manager
    return channel_manager.get_default()

class _VideoManagerProxy:
    """Lazy proxy so existing code using module-level `video_manager` keeps working."""
    def __getattr__(self, name):
        return getattr(_get_default_manager(), name)
    def __setattr__(self, name, value):
        setattr(_get_default_manager(), name, value)

video_manager = _VideoManagerProxy()


def _get_mgr(channel: int = 0):
    """Resolve a VideoSourceManager by channel id. Used by API endpoints."""
    from backend.api.channel_manager import channel_manager
    try:
        return channel_manager.get(channel)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# Pydantic 模型









# 摄像头列表缓存
_cameras_cache = {
    "cameras": [],
    "last_update": 0,
    "cache_duration": 60  # 缓存60秒
}




# API 端点







# ========== 画面变换配置 API（按通道独立） ==========







# ========== 卡尔曼滤波配置 API ==========








# ========== RTSP 网络视频流 API 端点 ==========



# ========== 海康工业相机 API 端点 ==========






# ========== HCNetSDK API endpoints ==========






























def get_video_feed(channel: int = 0):
    """获取视频流（供 main.py 使用）"""
    from backend.api.channel_manager import channel_manager
    mgr = channel_manager.get(channel)
    return mgr.generate_mjpeg()

def get_video_manager(channel: int = 0):
    """获取视频管理器实例"""
    from backend.api.channel_manager import channel_manager
    return channel_manager.get(channel)

# ============================================================
# 路由注册 (P7 第六刀): routes 已迁至 source_routes.py
# 通过 import 触发装饰器副作用, 把所有端点挂到共享 router 上
# ============================================================
import backend.api.source_routes  # noqa: E402,F401
