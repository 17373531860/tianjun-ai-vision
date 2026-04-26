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


# ========== HCNetSDK import ==========
HCNET_SDK_AVAILABLE = False
HCNetSession = None
try:
    from backend.hcnetsdk.wrapper import HCNetSession as _HCNetSession
    if _HCNetSession.sdk_available():
        HCNetSession = _HCNetSession
        HCNET_SDK_AVAILABLE = True
        print("[HCNetSDK] DLL available")
    else:
        print("[HCNetSDK] DLL not found (Windows deploy only)")
except Exception as _e:
    print(f"[HCNetSDK] load failed: {_e}")

# ========== 海康工业相机 SDK 导入 ==========
import sys
HIK_SDK_AVAILABLE = False
MvCamera = None
MV_CC_DEVICE_INFO_LIST = None
MV_CC_DEVICE_INFO = None
MV_USB_DEVICE = None
MV_GIGE_DEVICE = None

# ========== 调试日志开关 ==========
HIK_DEBUG = False  # 海康SDK详细日志（仅调试时开启）
FREEZE_DEBUG = True  # 卡死调试日志

def debug_log(msg, category="MAIN"):
    """调试日志 - 默认关闭，需要时手动开启 FREEZE_DEBUG"""
    if FREEZE_DEBUG:
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        print(f"[{timestamp}] [DEBUG/{category}] {msg}", flush=True)

def hik_log(msg, level="INFO"):
    """海康相机调试日志 - 默认关闭，需要时手动开启 HIK_DEBUG"""
    if HIK_DEBUG or level in ("ERROR", "WARN", "SUCCESS"):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        print(f"[{timestamp}] [海康SDK/{level}] {msg}", flush=True)

try:
    _mv_import_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "MvImport")
    _mv_lib_dir = os.path.join(_mv_import_dir, "lib")
    
    if _mv_import_dir not in sys.path:
        sys.path.insert(0, _mv_import_dir)
    
    from backend.api.MvImport.MvCameraControl_class import MvCamera
    from backend.api.MvImport.CameraParams_header import (
        MV_CC_DEVICE_INFO_LIST, 
        MV_CC_DEVICE_INFO,
        MV_FRAME_OUT_INFO_EX,
        MVCC_INTVALUE,
        MV_TRIGGER_MODE_OFF,
        MV_CC_PIXEL_CONVERT_PARAM
    )
    from backend.api.MvImport.CameraParams_const import (
        MV_USB_DEVICE, 
        MV_GIGE_DEVICE,
        MV_ACCESS_Exclusive
    )
    from backend.api.MvImport.PixelType_header import (
        PixelType_Gvsp_Mono8,
        PixelType_Gvsp_BayerRG8,
        PixelType_Gvsp_RGB8_Packed,
        PixelType_Gvsp_BGR8_Packed,
        PixelType_Gvsp_YUV422_Packed,
        PixelType_Gvsp_YUV422_YUYV_Packed
    )
    
    HIK_SDK_AVAILABLE = True
    
except Exception:
    pass


def _decode_hik_string(ctypes_char_array):
    """从 ctypes 字符数组解码为字符串，用于海康设备名称/序列号"""
    if ctypes_char_array is None:
        return ""
    try:
        byte_str = memoryview(ctypes_char_array).tobytes()
        null_index = byte_str.find(b'\x00')
        if null_index != -1:
            byte_str = byte_str[:null_index]
        for enc in ('utf-8', 'gbk', 'latin-1'):
            try:
                return byte_str.decode(enc).strip()
            except UnicodeDecodeError:
                continue
        return byte_str.decode('latin-1', errors='replace').strip()
    except Exception:
        return ""


def get_hikvision_device_list():
    """
    枚举当前可用的海康工业相机（USB + GigE），返回 [{"index": 设备索引, "name": 显示名称}, ...]
    未安装 SDK 或枚举失败时返回空列表
    """
    if not HIK_SDK_AVAILABLE:
        return []
    try:
        device_list = MV_CC_DEVICE_INFO_LIST()
        tlayer_type = MV_USB_DEVICE | MV_GIGE_DEVICE
        ret = MvCamera.MV_CC_EnumDevices(tlayer_type, device_list)
        if ret != 0 or device_list.nDeviceNum == 0:
            return []
        
        result = []
        for i in range(device_list.nDeviceNum):
            st_dev = cast(device_list.pDeviceInfo[i], POINTER(MV_CC_DEVICE_INFO)).contents
            name = ""
            if st_dev.nTLayerType == MV_USB_DEVICE:
                model = _decode_hik_string(st_dev.SpecialInfo.stUsb3VInfo.chModelName)
                serial = _decode_hik_string(st_dev.SpecialInfo.stUsb3VInfo.chSerialNumber)
                name = f"USB [{model}] {serial}" if serial else f"USB [{model}]"
            elif st_dev.nTLayerType == MV_GIGE_DEVICE:
                model = _decode_hik_string(st_dev.SpecialInfo.stGigEInfo.chModelName)
                ip = st_dev.SpecialInfo.stGigEInfo.nCurrentIp
                ip_str = "%d.%d.%d.%d" % (
                    (ip >> 24) & 0xff, (ip >> 16) & 0xff,
                    (ip >> 8) & 0xff, ip & 0xff
                )
                name = f"GigE [{model}] {ip_str}"
            if not name.strip():
                name = f"海康相机 {i}"
            result.append({"index": i, "name": name})
        return result
    except Exception as e:
        hik_log(f"枚举设备失败: {e}", "ERROR")
        return []


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
class FFmpegRecorder:
    """
    使用 FFmpeg 进程进行视频录制
    通过管道发送帧数据，完全独立于 Python/CUDA，避免卡死
    """
    # 录制分辨率上限（降低分辨率大幅减少 FFmpeg 内存）
    MAX_RECORD_WIDTH = 640
    MAX_RECORD_HEIGHT = 360
    
    def __init__(self, filepath: str, width: int, height: int, fps: int = 25):
        self.filepath = filepath
        # 限制录制分辨率，保持宽高比
        if width > self.MAX_RECORD_WIDTH or height > self.MAX_RECORD_HEIGHT:
            scale = min(self.MAX_RECORD_WIDTH / width, self.MAX_RECORD_HEIGHT / height)
            self.width = int(width * scale) // 2 * 2  # 确保偶数
            self.height = int(height * scale) // 2 * 2
        else:
            self.width = width
            self.height = height
        self.input_width = width
        self.input_height = height
        self.fps = fps
        self.process = None
        self._lock = threading.Lock()
        self._is_open = False
        self._frame_count = 0
    
    def open(self) -> bool:
        """启动 FFmpeg 进程"""
        try:
            ffmpeg_path = get_cached_ffmpeg_path()
            
            cmd = [
                ffmpeg_path,
                '-y',
                '-f', 'rawvideo',
                '-vcodec', 'rawvideo',
                '-pix_fmt', 'bgr24',
                '-s', f'{self.width}x{self.height}',
                '-r', str(self.fps),
                '-i', 'pipe:0',
                '-c:v', 'libx264',
                '-preset', 'ultrafast',
                '-tune', 'zerolatency',  # 禁用前瞻缓冲，大幅减少内存
                '-threads', '1',  # 单线程编码，减少内存
                '-crf', '28',  # 略降质量换取更小的编码缓冲
                '-pix_fmt', 'yuv420p',
                '-movflags', '+faststart',
                self.filepath
            ]
            
            self.process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                bufsize=10**6
            )
            self._is_open = True
            self._frame_count = 0
            print(f"[FFmpeg录制] 已启动: {os.path.basename(self.filepath)}")
            return True
        except Exception as e:
            print(f"[FFmpeg录制] 启动失败: {e}")
            self._is_open = False
            return False
    
    def write(self, frame) -> bool:
        """写入一帧（非阻塞，失败时静默）"""
        if not self._is_open or self.process is None:
            return False
        
        try:
            with self._lock:
                if self.process.poll() is not None:
                    self._is_open = False
                    return False
                
                # 缩放到录制分辨率
                if frame.shape[1] != self.width or frame.shape[0] != self.height:
                    frame = cv2.resize(frame, (self.width, self.height), interpolation=cv2.INTER_AREA)
                
                self.process.stdin.write(frame.tobytes())
                self._frame_count += 1
                return True
        except (BrokenPipeError, OSError):
            self._is_open = False
            return False
        except Exception:
            return False
    
    def release(self):
        """关闭录制器"""
        with self._lock:
            if self.process is not None:
                try:
                    if self.process.stdin:
                        self.process.stdin.close()
                except Exception:
                    pass
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    try:
                        self.process.kill()
                        self.process.wait(timeout=2)
                    except Exception:
                        pass
                except Exception:
                    try:
                        self.process.kill()
                    except Exception:
                        pass
                finally:
                    self.process = None
            self._is_open = False
            print(f"[FFmpeg录制] 已停止: {os.path.basename(self.filepath)}, 共 {self._frame_count} 帧")
    
    def isOpened(self) -> bool:
        """检查是否正在录制"""
        return self._is_open and self.process is not None and self.process.poll() is None


# ========== 卡尔曼滤波器类 ==========
class KalmanFilter2D:
    """
    2D 卡尔曼滤波器，用于平滑检测框位置
    状态向量: [x, y, w, h, vx, vy, vw, vh] (位置 + 速度)
    """
    def __init__(self, initial_state, process_noise=0.03, measurement_noise=0.1):
        """
        初始化卡尔曼滤波器
        
        Args:
            initial_state: [x, y, w, h] 初始位置
            process_noise: 过程噪声 Q (越小越平滑，越大响应越快)
            measurement_noise: 观测噪声 R (越大越平滑，对突变不敏感)
        """
        # 状态向量 [x, y, w, h, vx, vy, vw, vh]
        self.state = np.array([
            initial_state[0], initial_state[1], initial_state[2], initial_state[3],
            0, 0, 0, 0  # 初始速度为0
        ], dtype=np.float64)
        
        # 状态转移矩阵 (假设匀速运动)
        self.F = np.array([
            [1, 0, 0, 0, 1, 0, 0, 0],  # x = x + vx
            [0, 1, 0, 0, 0, 1, 0, 0],  # y = y + vy
            [0, 0, 1, 0, 0, 0, 1, 0],  # w = w + vw
            [0, 0, 0, 1, 0, 0, 0, 1],  # h = h + vh
            [0, 0, 0, 0, 1, 0, 0, 0],  # vx = vx
            [0, 0, 0, 0, 0, 1, 0, 0],  # vy = vy
            [0, 0, 0, 0, 0, 0, 1, 0],  # vw = vw
            [0, 0, 0, 0, 0, 0, 0, 1],  # vh = vh
        ], dtype=np.float64)
        
        # 观测矩阵 (只能观测位置，不能直接观测速度)
        self.H = np.array([
            [1, 0, 0, 0, 0, 0, 0, 0],
            [0, 1, 0, 0, 0, 0, 0, 0],
            [0, 0, 1, 0, 0, 0, 0, 0],
            [0, 0, 0, 1, 0, 0, 0, 0],
        ], dtype=np.float64)
        
        # 过程噪声协方差矩阵
        self.Q = np.eye(8, dtype=np.float64) * process_noise
        
        # 观测噪声协方差矩阵
        self.R = np.eye(4, dtype=np.float64) * measurement_noise
        
        # 估计误差协方差矩阵
        self.P = np.eye(8, dtype=np.float64)
        
    def predict(self):
        """预测步骤"""
        # 状态预测
        self.state = self.F @ self.state
        # 协方差预测
        self.P = self.F @ self.P @ self.F.T + self.Q
        return self.state[:4]  # 返回 [x, y, w, h]
    
    def update(self, measurement):
        """更新步骤"""
        z = np.array(measurement, dtype=np.float64)
        
        # 卡尔曼增益
        S = self.H @ self.P @ self.H.T + self.R
        K = self.P @ self.H.T @ np.linalg.inv(S)
        
        # 状态更新
        y = z - self.H @ self.state  # 测量残差
        self.state = self.state + K @ y
        
        # 协方差更新
        I = np.eye(8)
        self.P = (I - K @ self.H) @ self.P
        
        return self.state[:4]  # 返回 [x, y, w, h]
    
    def get_position(self):
        """获取当前位置估计"""
        return self.state[:4]


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


class VideoSourceManager(TrackingMixin, InferenceLoopMixin, StepStatsMixin, CaptureLoopMixin, EventTriggerMixin, ModelLoadMixin, CheckModesMixin, SettlementMixin, DetectRunnersMixin, CameraStartMixin, SessionLifecycleMixin):
    # 配置文件路径
    CONFIG_FILE = os.path.join(os.path.dirname(__file__), '..', 'data', 'device_config.json')
    
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
        self.mediapipe_enabled = False
        self.mediapipe_pose = True       # 显示姿态骨架
        self.mediapipe_hands = True      # 显示手部关键点
        self.mediapipe_confidence = 0.7  # 检测置信度阈值 (0.1-1.0)
        self._mp_pose = None             # lazy-loaded mediapipe Pose instance
        self._mp_hands = None            # lazy-loaded mediapipe Hands instance
        self._mp_draw = None             # mediapipe drawing utils
        self._mp_draw_styles = None
        self._mp_last_pose_results = None
        self._mp_last_hands_results = None
        self._mp_frame_counter = 0
        self._mp_process_interval = 2    # 每隔 N 帧跑一次 MediaPipe（节省性能）
        
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
        self.video_rotation = 0  # 0 / 90 / 180 / 270
        self.video_flip_h = False  # 左右镜像
        self.video_flip_v = False  # 上下镜像
        
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

    def _apply_frame_transform(self, frame):
        """按通道配置对帧做旋转 + 镜像。

        顺序：先旋转（90° 倍数），再水平镜像，再垂直镜像。
        OpenCV 原生实现，零拷贝 90°/180°/270°，极低开销。
        """
        if frame is None:
            return frame
        rot = self.video_rotation
        if rot == 90:
            frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
        elif rot == 180:
            frame = cv2.rotate(frame, cv2.ROTATE_180)
        elif rot == 270:
            frame = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
        if self.video_flip_h and self.video_flip_v:
            frame = cv2.flip(frame, -1)
        elif self.video_flip_h:
            frame = cv2.flip(frame, 1)
        elif self.video_flip_v:
            frame = cv2.flip(frame, 0)
        return frame

    def _has_display_transform(self) -> bool:
        """是否配置了任何画面变换（旋转/镜像）。无变换时走快路径跳过坐标映射。"""
        return bool(
            (self.video_rotation or 0) % 360 != 0
            or self.video_flip_h
            or self.video_flip_v
        )

    def _map_bbox_original_to_display(self, x: float, y: float, w: float, h: float):
        """把单个归一化 bbox 从原图坐标系映射到显示坐标系。

        变换顺序与 _apply_frame_transform 完全一致：先旋转, 再水平镜像, 再垂直镜像。
        坐标均为归一化值 (相对各自坐标系的宽高), 无需知道像素尺寸。
        """
        rot = (self.video_rotation or 0) % 360
        if rot == 90:
            # 顺时针 90°: 左上角 (x, y) -> (1 - y - h, x), 宽高交换
            nx, ny, nw, nh = 1.0 - y - h, x, h, w
        elif rot == 180:
            nx, ny, nw, nh = 1.0 - x - w, 1.0 - y - h, w, h
        elif rot == 270:
            # 逆时针 90°: 左上角 (x, y) -> (y, 1 - x - w), 宽高交换
            nx, ny, nw, nh = y, 1.0 - x - w, h, w
        else:
            nx, ny, nw, nh = x, y, w, h
        if self.video_flip_h:
            nx = 1.0 - nx - nw
        if self.video_flip_v:
            ny = 1.0 - ny - nh
        return nx, ny, nw, nh

    def _map_detections_original_to_display(self, detections):
        """就地把 detections 列表里每个 det 的 x/y/w/h 从原图坐标系映射到显示坐标系。

        无变换时直接返回, 零开销。归一化坐标下只做少量加减, 对上千目标也 < 1ms。
        """
        if not self._has_display_transform() or not detections:
            return detections
        for det in detections:
            if 'x' in det and 'y' in det and 'w' in det and 'h' in det:
                nx, ny, nw, nh = self._map_bbox_original_to_display(
                    float(det['x']), float(det['y']),
                    float(det['w']), float(det['h']),
                )
                det['x'], det['y'], det['w'], det['h'] = nx, ny, nw, nh
        return detections

    def _init_mediapipe(self):
        """Lazy-load MediaPipe models on first use."""
        try:
            import mediapipe as mp
            self._mp_draw = mp.solutions.drawing_utils
            self._mp_draw_styles = mp.solutions.drawing_styles
            conf = max(0.1, min(1.0, self.mediapipe_confidence))
            if self.mediapipe_pose and self._mp_pose is None:
                self._mp_pose = mp.solutions.pose.Pose(
                    static_image_mode=False,
                    model_complexity=0,
                    min_detection_confidence=conf,
                    min_tracking_confidence=0.5,
                )
                print(f"[MediaPipe] Pose 模型已加载 (confidence={conf})")
            if self.mediapipe_hands and self._mp_hands is None:
                self._mp_hands = mp.solutions.hands.Hands(
                    static_image_mode=False,
                    max_num_hands=2,
                    model_complexity=0,
                    min_detection_confidence=conf,
                    min_tracking_confidence=0.5,
                )
                print("[MediaPipe] Hands 模型已加载")
        except ImportError:
            print("[MediaPipe] 警告: mediapipe 未安装，pip install mediapipe")
            self.mediapipe_enabled = False
        except Exception as e:
            print(f"[MediaPipe] 初始化失败: {e}")
            self.mediapipe_enabled = False

    def _release_mediapipe(self):
        """Release MediaPipe resources."""
        if self._mp_pose is not None:
            try:
                self._mp_pose.close()
            except Exception:
                pass
            self._mp_pose = None
        if self._mp_hands is not None:
            try:
                self._mp_hands.close()
            except Exception:
                pass
            self._mp_hands = None
        self._mp_last_pose_results = None
        self._mp_last_hands_results = None
        self._mp_frame_counter = 0
        print("[MediaPipe] 资源已释放")

    def _apply_mediapipe_overlay(self, frame):
        """Run MediaPipe on the frame (or reuse cached results) and draw landmarks.
        
        Returns the annotated frame (modified in-place for performance).
        """
        if not self.mediapipe_enabled:
            return frame
        
        if self._mp_draw is None:
            self._init_mediapipe()
            if not self.mediapipe_enabled:
                return frame
        
        import mediapipe as mp
        
        self._mp_frame_counter += 1
        should_process = (self._mp_frame_counter % max(self._mp_process_interval, 1)) == 0
        
        if should_process:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            rgb.flags.writeable = False
            
            if self._mp_pose is not None and self.mediapipe_pose:
                try:
                    self._mp_last_pose_results = self._mp_pose.process(rgb)
                except Exception:
                    self._mp_last_pose_results = None
            else:
                self._mp_last_pose_results = None
            
            if self._mp_hands is not None and self.mediapipe_hands:
                try:
                    self._mp_last_hands_results = self._mp_hands.process(rgb)
                except Exception:
                    self._mp_last_hands_results = None
            else:
                self._mp_last_hands_results = None
        
        if self._mp_last_pose_results and self._mp_last_pose_results.pose_landmarks:
            self._mp_draw.draw_landmarks(
                frame,
                self._mp_last_pose_results.pose_landmarks,
                mp.solutions.pose.POSE_CONNECTIONS,
                landmark_drawing_spec=self._mp_draw_styles.get_default_pose_landmarks_style(),
            )
        
        if self._mp_last_hands_results and self._mp_last_hands_results.multi_hand_landmarks:
            for hand_landmarks in self._mp_last_hands_results.multi_hand_landmarks:
                self._mp_draw.draw_landmarks(
                    frame,
                    hand_landmarks,
                    mp.solutions.hands.HAND_CONNECTIONS,
                    self._mp_draw_styles.get_default_hand_landmarks_style(),
                    self._mp_draw_styles.get_default_hand_connections_style(),
                )
        
        return frame

    def _init_inference_vars(self):
        """初始化推理相关变量（在__init__的_load_device_config之后调用）"""
        self.conf_threshold = 0.25
        self.iou_threshold = 0.45
        
        # ========== 双线程架构相关 ==========
        self._inference_thread = None  # 推理线程
        self._inference_running = False  # 推理线程运行标志
        self._latest_frame_for_inference = None  # 供推理线程使用的最新帧（可能是缩小后的, 原图视角）
        self._latest_frame_original_size = None  # 原始帧尺寸 (h, w)，用于坐标还原
        # v2.7.14: 推理用原图, stats/screenshot 用显示帧 → 两份分开存
        self._latest_display_small_for_stats = None  # 推理线程做 stats 截图用的显示帧(缩小版)
        self._inference_frame_lock = threading.Lock()  # 保护推理帧的锁
        self._confirmed_detections = []  # 经过帧计数确认的检测结果
        self._confirmed_detections_lock = threading.Lock()  # 保护确认结果的锁
        
        # ========== 健康检查相关 ==========
        self._last_inference_heartbeat = time.time()  # 推理线程心跳时间
        self._last_capture_heartbeat = time.time()  # 捕获线程心跳时间
        self._health_check_interval = 5.0  # 健康检查间隔（秒）
        self._thread_timeout_threshold = 10.0  # 线程无响应阈值（秒）
        
        # ========== 推理超时保护 ==========
        self._inference_timeout = 10.0  # 单次推理超时时间（秒）
        self._inference_timeout_count = 0  # 推理超时计数
        self._max_consecutive_timeouts = 5  # 最大连续超时次数，超过后重置模型
        self._inference_executor = None  # 持久线程池（避免每帧创建新线程池导致内存泄漏）
        self._last_successful_inference = time.time()  # 最后一次成功推理的时间
        
        # ========== 卡尔曼滤波相关 ==========
        self._kalman_filters = {}  # {label: KalmanFilter} 每个目标一个滤波器
        self._kalman_enabled = False  # 是否启用卡尔曼滤波（默认关闭）
        self._kalman_process_noise = 0.03  # 过程噪声 Q (越小越平滑，越大响应越快)
        self._kalman_measurement_noise = 0.1  # 观测噪声 R (越大越平滑)
        self._detection_history = {}  # 检测框历史 {label: last_detection}
        self._detection_missing_frames = {}  # 目标消失帧数统计 {label: count}
        self._max_missing_frames = 5  # 目标消失多少帧后移除滤波器
        
        # 统计
        self.fps_actual = 0  # 采集线程的 FPS (摄像头实际读帧速度)
        self.fps_inference = 0  # 推理线程的 FPS (实际跑模型的速度)
        # v2.7.13: 跟踪/事件 帧数阈值必须按 "推理 FPS" 换算, 因为:
        #   - _update_tracking_stats / _update_step_stats 都在推理线程里累加帧数
        #   - 采集 FPS 通常 25~30, 推理 FPS 往往只有 5~8 (GPU/模型大小决定)
        #   - 如果用 fps_actual 当基准, 1 秒 = 30 帧阈值, 但推理线程 1 秒只加 5~8,
        #     实际要等 3~6 秒才到阈值, 用户感知就是 "延迟 5 倍"
        self.latency = 0
        self._fps_counter = 0
        self._fps_time = time.time()
        self._fps_inference_counter = 0
        self._fps_inference_time = time.time()
        self._frame_seq = 0
        
        # 步骤截图 {step_name: base64_image}
        self.step_screenshots = {}
        self.step_counts = {}  # {step_name: count}
        self.step_last_seen = {}  # {step_name: timestamp} 最后一次检测到的时间
        self.step_start_time = {}  # {step_name: timestamp} 步骤开始检测的时间
        self.step_time_config = {}  # {step_name: {min_duration, max_duration, max_interval, disappear_delay}}
        
        # 项目配置
        self.project_config = None
        self.settlement_mode = 'first_step'  # 'first_step' or 'last_step'
        self.idle_timeout_seconds = 0         # 0 = disabled
        self.cycle_max_duration = 0           # 0 = disabled, >0 = 周期总时长超时NG
        self.step_conf_thresholds = {}  # {step_name: threshold}
        # 传动杆误判过滤（两层，默认全关，从 project_config 读开关）
        self._rod_filter_cfg = read_rod_filter_config(None)
        self._rod_gate = RodSessionGate(
            rod_label=self._rod_filter_cfg["gate_rod_label"],
            gate_labels=self._rod_filter_cfg["gate_labels"],
        )
        self.step_min_frames = {}  # {step_name: min_frames} 每个步骤的最少帧数配置
        self.step_consecutive_frames = {}  # {step_name: count} 跟踪每个标签连续出现的帧数
        self.step_frame_confirmed = {}  # {step_name: bool} 标记标签是否已确认（达到最少帧数）
        self.step_gap_tolerance = {}  # {step_name: int} 允许的连续丢帧数，默认0
        self._step_gap_count = {}  # {step_name: int} 当前连续丢帧计数
        
        # 静态步骤配置
        self.step_detection_type = {}  # {step_name: 'dynamic'|'static'} 检测类型
        self.step_static_config = {}  # {step_name: {trigger_frames, join_cycle, trigger_event}}
        self.step_static_triggered = {}  # {step_name: bool} 静态步骤是否已触发（防止重复触发）
        
        # 同时出现组配置
        self._simultaneous_groups = []  # 配置列表
        self._sim_group_buffers = {}  # 缓冲排序器状态 {group_idx: {collecting, start_time, collected_labels, ...}}
        
        # 事件与计数器
        self.counters = {}  # {counter_name: value}
        self.events_log = []  # 事件日志
        self._event_seq = 0  # 事件唯一递增序号
        self.ng_step_cycle_counts = {}  # {step_label: count_of_ng_cycles} for NG TOP3
        self.current_cycle_steps = []  # 当前周期检测到的步骤顺序
        self.last_added_step = None  # 上一个添加到周期的步骤（用于去重判断）
        self.cycle_complete = False
        
        # Backup steps (替补步骤)
        self.step_backup_map = {}            # {backup_label: primary_label}
        self.step_primary_to_backup = {}     # {primary_label: backup_label}
        self.backup_steps_seen_in_cycle = set()
        self.step_strict_order = {}          # {label: True} only accept when predecessors done
        self.step_accept_once = {}           # {label: True} only accept once per cycle
        self._first_step_had_gap = False
        self._first_step_reconfirmed = False
        self._first_step_disappeared_at = None
        self._last_step_added_time = None
        self._step_raw_start = {}
        self._last_ng_time = 0
        self._cycle_regression = False       # A-B-A 步骤回退标记
        
        # ========== Tracking Mode (跟踪模式 — 物品清点) ==========
        self._tracking_objects = {}     # {track_id: {class_name, display_id, first_seen, last_seen, bbox, order_idx}}
        self._tracking_class_counters = {}  # {class_name: int} auto-increment per class
        self._tracking_display_map = {}  # {track_id: display_id}
        self._tracking_item_checklist = {}  # {class_name: {expected, counted, prefix}}
        self._tracking_lost_frames = {}  # {track_id: frames_missing}
        self._tracking_letter_map = {}  # {class_name: letter_prefix}
        self._tracking_letter_idx = 0
        self._tracking_order_seq = 0    # placement order counter
        self._tracking_prev_count = 0   # previous frame's tracked object count (for all_gone detection)
        self._tracking_gone_frames = 0  # consecutive frames where count <= threshold
        self._tracking_cycle_active = False  # whether a counting cycle is in progress
        self._tracking_had_roi_objects = False  # has any object been in ROI during this cycle
        self._tracking_trigger_frames = 0   # frames the trigger label has been visible
        self._tracking_recently_lost = {}   # {old_track_id: {class_name, display_id, bbox, lost_time, order_idx}}
        self._tracking_transferred_ids = {}  # {old_track_id: transfer_time} - IDs migrated to new tracks
        self._tracking_prev_positions = {}   # {display_id: (cx, cy, class_name)} for swap detection
        self._tracking_appearance = {}       # {display_id: histogram} for appearance matching
        self._tracking_stable_frames = {}    # {track_id: consecutive_seen_frames} for ID lock
        self._tracking_locked_ids = {}       # {display_id: (cx, cy)} locked IDs won't be re-matched
        self._tracking_registered_positions = {}  # {display_id: {class_name, cx, cy, stable_frames}}
        self._custom_tracker_yaml = None    # path to dynamic bytetrack config
        
        # Event counting mode (动作计数)
        self._event_counters = {}           # {class_name: completed_event_count}
        self._event_state = {}              # {class_name: 'idle'|'visible'|'gone'}
        self._event_visible_frames = {}     # {class_name: consecutive_visible_frames}
        self._event_gone_frames_count = {}  # {class_name: consecutive_gone_frames}
        self._event_first_seen = {}         # {class_name: timestamp of first event start}
        self._event_last_seen = {}          # {class_name: timestamp of last event end}

        # v2.7.4: Stack mode (堆叠模式 — 物品已放好但被堆叠/遮挡，消失再出现算下一层)
        # 仅 logic_mode=tracking + count_mode=track 时生效
        self._stack_state = {}              # {class_name: 'idle'|'visible'|'disappeared'}
        self._stack_counters = {}           # {class_name: 已计入的"层"数}
        self._stack_disappeared_at = {}     # {class_name: timestamp 进入 disappeared 的时间}
        self._stack_visible_frames = {}     # {class_name: 当前 visible 状态下连续帧数}
        
        # Container mode (box + items hierarchy)
        self._container_mode = False
        self._container_label = ''
        self._box_objects = {}       # {box_track_id: BoxState}
        self._box_counter = 0
        self._box_settled_results = []
        
        # Cycle Time 统计
        self.cycle_start_time = None  # 当前周期开始时间
        self.cycle_times = []  # 记录最近的周期时间（最多保留100个）
        self.ng_cycle_times = []  # NG cycle durations (up to 100)
        self.step_detection_times = {}  # 步骤检测时间 {step_name: timestamp}
        self.step_durations = {}  # 步骤耗时 {step_name: duration_seconds}
        self.step_durations_history = {}  # {step_name: [d1, d2, ...]} for average PT
        self.step_intervals = {}  # 步骤间隔时间 {step_name: interval_from_previous}
        self.last_step_completed_time = None  # 上一个步骤完成的时间
        
        # ========== 会话和周期记录 ==========
        self.current_session_id = None  # 当前会话ID
        self.current_session_uuid = None  # 当前会话UUID
        self.current_cycle_id = None  # 当前周期ID
        self.current_cycle_uuid = None  # 当前周期UUID
        self.current_cycle_number = 0  # 当前周期序号
        self.cycle_step_records = []  # 当前周期的步骤记录 (用于批量保存)
        self.step_order_counter = 0  # 步骤顺序计数器
        self.recording_enabled = False  # 是否启用录制
        self.export_settings = None  # 导出设置缓存
        self.last_cycle_end_time = None  # 上一周期结束时间（用于计算周期间隔）
        self._session_start_date = None  # 当前会话的开始日期（用于跨日自动拆分）
        self._session_start_shift = None  # "day" / "night" / None — 班次实时拆分
        
        # 视频录制
        self.video_writer = None  # 视频录制器
        self.cycle_video_writer = None  # 周期视频录制器
        self.step_video_writers = {}  # 步骤视频录制器 {step_label: writer}
        self._step_writers_lock = threading.Lock()  # 保护 step_video_writers 的并发访问
        self._writer_lock = threading.Lock()  # 保护 video_writer 和 cycle_video_writer
        
        # ========== 录制队列（独立线程，避免与 CUDA 冲突） ==========
        import queue
        self._recording_queue = queue.Queue(maxsize=30)  # 约1秒缓冲 @30fps（最小化内存）
        self._recording_thread = None
        self._recording_running = False
        self._recording_drop_count = 0  # 统计丢帧数
        
    def set_project_config(self, config: dict):
        """设置项目配置"""
        self.project_config = config
        
        # 刷新传动杆过滤参数 + 重建 SessionGate
        try:
            self._rod_filter_cfg = read_rod_filter_config(config)
            self._rod_gate = RodSessionGate(
                rod_label=self._rod_filter_cfg["gate_rod_label"],
                gate_labels=self._rod_filter_cfg["gate_labels"],
            )
        except Exception as _e:
            print(f"[rod_filter] read config failed, fallback to defaults: {_e}")
            self._rod_filter_cfg = read_rod_filter_config(None)
            self._rod_gate = RodSessionGate()

        # 解析步骤置信度阈值和时间配置
        self.step_conf_thresholds = {}
        self.step_time_config = {}
        self.step_min_frames = {}  # 最少帧数配置
        self.step_consecutive_frames = {}  # 重置连续帧计数
        self.step_frame_confirmed = {}  # 重置确认状态
        self.step_gap_tolerance = {}  # 重置丢帧容忍
        self._step_gap_count = {}  # 重置丢帧计数
        self.step_detection_type = {}  # 检测类型
        self.step_static_config = {}  # 静态步骤配置
        self.step_static_triggered = {}  # 静态步骤触发状态
        self.step_display_names = {}  # label -> displayLabel mapping
        self.step_backup_map = {}        # {backup_label: primary_label}
        self.step_primary_to_backup = {} # {primary_label: backup_label}
        self.backup_steps_seen_in_cycle = set()
        self.step_strict_order = {}
        self.step_accept_once = {}
        self._first_step_had_gap = False
        self._first_step_reconfirmed = False
        self._first_step_disappeared_at = None
        self._last_step_added_time = None
        self._step_raw_start = {}
        self._cycle_regression = False
        
        steps_config = config.get('steps_config', [])
        for step in steps_config:
            if step.get('enabled', True):
                label = step.get('label', '')
                # 前端发送的是百分比（10-100），需要转换为小数（0.1-1.0）
                threshold = step.get('threshold', 50)
                if threshold > 1:
                    threshold = threshold / 100.0  # 转换百分比为小数
                self.step_conf_thresholds[label] = threshold
                
                display_label = step.get('displayLabel') or step.get('display_name') or label
                if display_label != label:
                    self.step_display_names[label] = display_label
                
                # 步骤时间配置
                self.step_time_config[label] = {
                    'min_duration': step.get('min_duration'),  # 最短持续时间
                    'max_duration': step.get('max_duration'),  # 最大持续时间
                    'max_interval': step.get('max_interval', 1.0),  # 去重间隔，默认1秒
                    'disappear_delay': step.get('disappear_delay', 0),  # 消失确认延迟，默认0秒（立即确认）
                    'timeout_ng': step.get('timeout_ng', False),  # 超时自动判NG
                }
                
                # 最少帧数配置（默认1帧）
                min_frames = step.get('min_frames')
                self.step_min_frames[label] = min_frames if min_frames and min_frames > 0 else 1
                
                gap_tolerance = step.get('gap_tolerance')
                self.step_gap_tolerance[label] = gap_tolerance if gap_tolerance and gap_tolerance > 0 else 0
                
                if step.get('strict_order'):
                    self.step_strict_order[label] = True
                if step.get('accept_once'):
                    self.step_accept_once[label] = True
                
                detection_type = step.get('detection_type', 'dynamic')
                self.step_detection_type[label] = detection_type
                
                # 静态步骤配置
                if detection_type == 'static':
                    self.step_static_config[label] = {
                        'trigger_frames': step.get('static_trigger_frames', 30),
                        'join_cycle': step.get('join_cycle', True),
                        'trigger_event': step.get('triggerEvent')  # 触发的事件
                    }
                    self.step_static_triggered[label] = False
        
        # Second pass: build backup step mapping (needs all labels resolved first)
        for step in steps_config:
            if step.get('enabled', True):
                label = step.get('label', '')
                backup_for_id = step.get('backup_for')
                if backup_for_id:
                    for s in steps_config:
                        if s.get('id') == backup_for_id:
                            primary_label = s.get('label', '')
                            if primary_label:
                                self.step_backup_map[label] = primary_label
                                self.step_primary_to_backup[primary_label] = label
                            break
        
        if self.step_backup_map:
            print(f"替补步骤映射: {self.step_backup_map}")
        
        # 解析同时出现组配置
        pipeline_config = config.get('pipeline_config', {})
        self._simultaneous_groups = pipeline_config.get('simultaneous_groups', [])
        self._sim_group_buffers = {}
        if self._simultaneous_groups:
            print(f"同时出现组: {self._simultaneous_groups}")
        
        # Settlement mode: 'first_step' or 'last_step'
        self.settlement_mode = pipeline_config.get('settlement_mode', 'first_step')
        self.idle_timeout_seconds = pipeline_config.get('idle_timeout_seconds', 0)
        self.cycle_max_duration = pipeline_config.get('cycle_max_duration', 0)
        print(f"结算模式: {self.settlement_mode}, 空闲超时: {self.idle_timeout_seconds}s, 周期超时: {self.cycle_max_duration}s")
        
        # 结算步骤不允许有 strict_order，确保结算步骤始终能进入周期
        if self.settlement_mode == 'last_step':
            settle_label = self._get_last_sequence_step_label()
        else:
            settle_label = self._get_first_sequence_step_label()
        if settle_label and self.step_strict_order.get(settle_label):
            del self.step_strict_order[settle_label]
            print(f"[{self.settlement_mode}模式] 自动移除结算步骤 [{settle_label}] 的严格顺序")
        
        # 初始化计数器（确保默认计数器始终存在）
        self.counters = {}
        counters_config = config.get('counters_config', [])
        
        # 默认计数器名称列表
        DEFAULT_COUNTERS = ['总产量', '合格总数', '不良总数', 'NG步骤']
        
        # 先从项目配置添加计数器（作为默认值）
        for counter in counters_config:
            self.counters[counter.get('name', '')] = counter.get('value', 0)
        
        # 确保默认计数器存在
        for default_name in DEFAULT_COUNTERS:
            if default_name not in self.counters:
                self.counters[default_name] = 0

        # 从通道专属文件恢复持久化的值（覆盖默认值）
        project_id = config.get('id')
        if project_id:
            counter_file = os.path.join(DATA_DIR, 'counters', f'project_{project_id}_ch{self.channel_id}.json')
            if os.path.exists(counter_file):
                try:
                    with open(counter_file, 'r', encoding='utf-8') as f:
                        saved = _json.load(f)
                    for name, val in saved.items():
                        if name in self.counters:
                            self.counters[name] = val
                    print(f"[计数器] ch{self.channel_id} 从文件恢复: {self.counters}")
                except Exception as e:
                    print(f"[计数器] ch{self.channel_id} 恢复失败: {e}")
        
        # 重置周期状态
        self.current_cycle_steps = []
        self.last_added_step = None  # 重置上一个添加的步骤
        self.backup_steps_seen_in_cycle = set()
        self.cycle_complete = False
        self.events_log = []
        self.ng_step_cycle_counts = {}
        self.step_start_time = {}  # 重置步骤开始时间
        
        if config.get('logic_mode') == 'tracking':
            self._reset_counting_cycle()
            self._generate_custom_tracker_yaml(pipeline_config)
            # Container mode: activated when strategy is 'container' and label is set
            clabel = pipeline_config.get('tracking_container_label', '')
            is_container_strategy = pipeline_config.get('tracking_cycle_strategy') == 'container'
            self._container_label = clabel if is_container_strategy else ''
            self._container_mode = is_container_strategy and bool(clabel)
        
        print(f"项目配置已加载: {config.get('name', 'Unknown')}, task_type={config.get('task_type')}, logic_mode={config.get('logic_mode')}")
        print(f"步骤阈值: {self.step_conf_thresholds}")
        print(f"步骤时间配置: {self.step_time_config}")
        print(f"步骤最少帧数: {self.step_min_frames}")
        print(f"步骤丢帧容忍: {self.step_gap_tolerance}")
        print(f"步骤检测类型: {self.step_detection_type}")
        print(f"静态步骤配置: {self.step_static_config}")
        print(f"计数器: {self.counters}")
        if config.get('logic_mode') == 'tracking':
            event_labels = [s.get('label') for s in steps_config if s.get('enabled', True) and s.get('count_mode') == 'event']
            print(f"跟踪模式配置: strategy={pipeline_config.get('tracking_cycle_strategy')}, expected={pipeline_config.get('counting_expected_items')}")
            if event_labels:
                print(f"动作计数标签: {event_labels}")
    
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
    
    def _get_first_sequence_step_label(self):
        """获取顺序模式下配置的第一个启用步骤的标签（自定义模式使用custom_sequence_order）"""
        if not self.project_config:
            return None
        
        logic_mode = self.project_config.get('logic_mode', 'sequential')
        pipeline_config = self.project_config.get('pipeline_config', {})
        steps_config = self.project_config.get('steps_config', [])
        
        # 根据模式选择配置
        if logic_mode == 'custom':
            sequence_order = pipeline_config.get('custom_sequence_order', [])
        else:
            sequence_order = pipeline_config.get('sequence_order', [])
        
        if not sequence_order or not steps_config:
            return None
        
        # 创建步骤ID到标签的映射（只包含启用的步骤）
        id_to_label = {}
        enabled_step_ids = set()
        for step in steps_config:
            step_id = step.get('id')
            label = step.get('label', '')
            if step_id and label:
                id_to_label[step_id] = label
                if step.get('enabled', True):
                    enabled_step_ids.add(step_id)
        
        # 获取第一个启用步骤的标签
        for item in sequence_order:
            step_id = item.get('step_id')
            if step_id in enabled_step_ids:
                return id_to_label.get(step_id)
        
        return None
    
    def _get_last_sequence_step_label(self):
        """获取顺序模式下配置的最后一个启用步骤的标签
        
        对于自定义模式，使用独立的 custom_sequence_order 配置
        """
        if not self.project_config:
            return None
        
        logic_mode = self.project_config.get('logic_mode', 'sequential')
        pipeline_config = self.project_config.get('pipeline_config', {})
        steps_config = self.project_config.get('steps_config', [])
        
        # 根据模式选择不同的配置
        if logic_mode == 'custom':
            sequence_order = pipeline_config.get('custom_sequence_order', [])
        else:
            sequence_order = pipeline_config.get('sequence_order', [])
        
        if not sequence_order or not steps_config:
            return None
        
        # 创建步骤ID到标签的映射（只包含启用的步骤）
        id_to_label = {}
        enabled_step_ids = set()
        for step in steps_config:
            step_id = step.get('id')
            label = step.get('label', '')
            if step_id and label:
                id_to_label[step_id] = label
                if step.get('enabled', True):
                    enabled_step_ids.add(step_id)
        
        # 从后向前找第一个启用的步骤
        for item in reversed(sequence_order):
            step_id = item.get('step_id')
            if step_id in enabled_step_ids:
                return id_to_label.get(step_id)
        
        return None
    
    def _get_expected_sequence_labels(self):
        """获取当前模式下预期的步骤标签有序列表"""
        if not self.project_config:
            return []
        
        logic_mode = self.project_config.get('logic_mode', 'sequential')
        pipeline_config = self.project_config.get('pipeline_config', {})
        steps_config = self.project_config.get('steps_config', [])
        
        if logic_mode == 'custom':
            sequence_order = pipeline_config.get('custom_sequence_order', [])
        else:
            sequence_order = pipeline_config.get('sequence_order', [])
        
        if not sequence_order or not steps_config:
            return []
        
        id_to_label = {}
        enabled_step_ids = set()
        for step in steps_config:
            step_id = step.get('id')
            label = step.get('label', '')
            if step_id and label:
                id_to_label[step_id] = label
                if step.get('enabled', True):
                    enabled_step_ids.add(step_id)
        
        expected = []
        for item in sequence_order:
            step_id = item.get('step_id')
            if step_id in id_to_label and step_id in enabled_step_ids:
                expected.append(id_to_label[step_id])
        return expected
    
    def _get_detection_step_labels(self):
        """获取检测模式下需要检测的步骤标签列表（按 steps_config 配置顺序）"""
        if not self.project_config:
            return []
        
        pipeline_config = self.project_config.get('pipeline_config', {})
        steps_config = self.project_config.get('steps_config', [])
        detection_step_ids = pipeline_config.get('detection_steps', [])
        
        id_to_label = {}
        ordered_enabled = []
        for step in steps_config:
            step_id = step.get('id')
            label = step.get('label', '')
            if step_id and label and step.get('enabled', True):
                id_to_label[step_id] = label
                ordered_enabled.append((step_id, label))
        
        if detection_step_ids:
            det_set = set(detection_step_ids)
            return [label for sid, label in ordered_enabled if sid in det_set]
        else:
            return [label for _, label in ordered_enabled]
    
    def _get_first_detection_step_label(self):
        """获取检测模式下第一个步骤的标签"""
        labels = self._get_detection_step_labels()
        return labels[0] if labels else None
    
    def _get_last_detection_step_label(self):
        """获取检测模式下最后一个步骤的标签"""
        labels = self._get_detection_step_labels()
        return labels[-1] if labels else None
    
    def _is_condition_prefix(self, sequence_to_check: list) -> bool:
        """检查给定序列是否是任何自定义条件的前缀
        
        Args:
            sequence_to_check: 要检查的步骤标签序列
        
        Returns:
            如果是任何条件的前缀返回 True，否则返回 False
        """
        if not self.project_config:
            return False
        
        pipeline_config = self.project_config.get('pipeline_config', {})
        custom_conditions = pipeline_config.get('custom_conditions', [])
        steps_config = self.project_config.get('steps_config', [])
        
        if not custom_conditions:
            return False
        
        # 创建步骤ID到标签的映射，并获取启用的步骤ID集合
        id_to_label = {}
        enabled_step_ids = set()
        for step in steps_config:
            step_id = step.get('id')
            label = step.get('label', '')
            if step_id and label:
                id_to_label[step_id] = label
                if step.get('enabled', True):
                    enabled_step_ids.add(step_id)
        
        # 检查每个条件
        for cond in custom_conditions:
            cond_sequence = cond.get('sequence', [])
            if not cond_sequence:
                continue
            
            # 将条件中的步骤ID转换为标签（只包含启用的步骤）
            cond_labels = [id_to_label.get(sid) for sid in cond_sequence if sid in id_to_label and sid in enabled_step_ids]
            
            if not cond_labels:
                continue
            
            # 检查 sequence_to_check 是否是 cond_labels 的前缀
            if len(sequence_to_check) <= len(cond_labels):
                is_prefix = True
                for i, label in enumerate(sequence_to_check):
                    if label != cond_labels[i]:
                        is_prefix = False
                        break
                if is_prefix:
                    print(f"  前缀匹配成功: {sequence_to_check} 是条件 {cond_labels} 的前缀")
                    return True
        
        return False
    
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
        yaml_content = (
            f"tracker_type: bytetrack\n"
            f"track_high_thresh: 0.25\n"
            f"track_low_thresh: 0.1\n"
            f"new_track_thresh: 0.25\n"
            f"track_buffer: {track_buffer}\n"
            f"match_thresh: 0.8\n"
            f"fuse_score: true\n"
        )
        yaml_path = os.path.join(tempfile.gettempdir(), f'bytetrack_custom_{id(self)}.yaml')
        with open(yaml_path, 'w') as f:
            f.write(yaml_content)
        self._custom_tracker_yaml = yaml_path
        print(f"[Tracking] Custom tracker config: track_buffer={track_buffer} (max_lost={max_lost_sec}s, fps={fps:.0f})")

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
    
    
    def _get_inference_executor(self):
        """获取持久推理线程池（懒初始化，避免每帧创建新线程池）"""
        from concurrent.futures import ThreadPoolExecutor
        if self._inference_executor is None or self._inference_executor._shutdown:
            self._inference_executor = ThreadPoolExecutor(max_workers=1)
        return self._inference_executor
    
    def _shutdown_inference_executor(self):
        """关闭推理线程池"""
        if self._inference_executor is not None:
            try:
                self._inference_executor.shutdown(wait=False)
            except Exception:
                pass
            self._inference_executor = None
    
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
    
    # ========== 录制线程相关方法（独立于 CUDA，避免段错误） ==========
    
    def _start_recording_thread(self):
        """启动独立录制线程"""
        if self._recording_thread is not None and self._recording_thread.is_alive():
            return  # 已在运行
        
        self._recording_running = True
        self._recording_drop_count = 0
        self._recording_thread = threading.Thread(target=self._recording_loop, daemon=True)
        self._recording_thread.start()
        print("[录制线程] 已启动")
    
    def _stop_recording_thread(self):
        """停止录制线程（线程安全，可被并发调用）"""
        self._recording_running = False
        
        thread = self._recording_thread
        if thread is not None:
            self._recording_thread = None
            thread.join(timeout=5.0)
            if thread.is_alive():
                print("[警告] 录制线程未能在超时内结束")
        
        # 安全清空队列残留帧，防止线程卡住时内存泄漏
        dropped = 0
        while not self._recording_queue.empty():
            try:
                self._recording_queue.get_nowait()
                dropped += 1
            except Exception:
                break
        
        if dropped > 0:
            print(f"[录制线程] 清理了队列中 {dropped} 帧残留数据")
        
        if self._recording_drop_count > 0:
            print(f"[录制线程] 本次录制共丢弃 {self._recording_drop_count} 帧（队列满）")
        
        print("[录制线程] 已停止")
    
    def _recording_loop(self):
        """
        独立录制线程 - 从队列取帧写入 VideoWriter
        与 CUDA/推理完全隔离，避免段错误
        """
        print("[录制线程] 开始运行")
        frame_count = 0
        last_log_time = time.time()
        last_heartbeat_time = time.time()
        
        while self._recording_running or not self._recording_queue.empty():
            try:
                current_time = time.time()
                
                # 每10秒打印心跳（调试用）
                if current_time - last_heartbeat_time > 10:
                    queue_size = self._recording_queue.qsize()
                    with self._step_writers_lock:
                        step_count = len(self.step_video_writers)
                    print(f"[录制线程心跳] 帧={frame_count}, 队列={queue_size}, 步骤录制={step_count}, 丢帧={self._recording_drop_count}")
                    last_heartbeat_time = current_time
                
                # 从队列取帧（带超时，避免阻塞）
                try:
                    frame = self._recording_queue.get(timeout=0.1)
                except:
                    # 队列空，继续等待
                    continue
                
                if frame is None:
                    continue
                
                # 实际写入 VideoWriter
                self._write_frame_to_writers(frame)
                frame_count += 1
                
                # 每30秒打印一次详细状态
                if current_time - last_log_time > 30:
                    queue_size = self._recording_queue.qsize()
                    print(f"[录制线程] 已写入 {frame_count} 帧, 队列积压: {queue_size}, 丢帧: {self._recording_drop_count}")
                    last_log_time = current_time
                    
            except Exception as e:
                print(f"[录制线程] 写入错误: {e}")
                import traceback
                traceback.print_exc()
                time.sleep(0.01)
        
        print(f"[录制线程] 结束运行, 共写入 {frame_count} 帧")
    
    def _enqueue_frame_for_recording(self, frame):
        """
        将帧放入录制队列（非阻塞）
        入队前缩小帧到录制分辨率，大幅减少队列内存占用
        """
        if frame is None or not self._recording_running:
            return
        
        try:
            max_w, max_h = FFmpegRecorder.MAX_RECORD_WIDTH, FFmpegRecorder.MAX_RECORD_HEIGHT
            h, w = frame.shape[:2]
            if w > max_w or h > max_h:
                scale = min(max_w / w, max_h / h)
                new_w = int(w * scale) // 2 * 2
                new_h = int(h * scale) // 2 * 2
                small_frame = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA)
            else:
                small_frame = frame
            
            try:
                self._recording_queue.put_nowait(small_frame)
            except Exception:
                try:
                    self._recording_queue.get_nowait()
                except Exception:
                    pass
                try:
                    self._recording_queue.put_nowait(small_frame)
                except Exception:
                    pass
                self._recording_drop_count += 1
        except Exception:
            self._recording_drop_count += 1
    
    def _write_frame_to_writers(self, frame):
        """
        实际写入帧到所有 VideoWriter（在录制线程中调用）
        帧已在入队时缩小到录制分辨率，FFmpegRecorder.write() 内部会
        自行 resize 到各自目标尺寸，这里不再做冗余缩放。
        """
        if frame is None:
            return
        
        try:
            # 确保帧是 BGR 格式（3通道）
            if len(frame.shape) == 2:
                frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
            elif frame.shape[2] == 4:
                frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
            
            # 取 writer 引用时短暂加锁，write() 在锁外执行以防止管道阻塞导致死锁
            with self._writer_lock:
                session_w = self.video_writer
                cycle_w = self.cycle_video_writer
                draining_session_w = getattr(self, '_draining_session_writer', None)
                draining_cycle_w = getattr(self, '_draining_cycle_writer', None)
            
            if session_w:
                try:
                    if session_w.isOpened():
                        session_w.write(frame)
                except Exception as e:
                    print(f"[录制警告] 写入会话视频失败: {e}")
                    with self._writer_lock:
                        if self.video_writer is session_w:
                            self.video_writer = None
                    try:
                        session_w.release()
                    except Exception:
                        pass
            
            if cycle_w:
                try:
                    if cycle_w.isOpened():
                        cycle_w.write(frame)
                except Exception as e:
                    print(f"[录制警告] 写入周期视频失败: {e}")
                    with self._writer_lock:
                        if self.cycle_video_writer is cycle_w:
                            self.cycle_video_writer = None
                    try:
                        cycle_w.release()
                    except Exception:
                        pass

            if draining_cycle_w and draining_cycle_w is not cycle_w:
                try:
                    if draining_cycle_w.isOpened():
                        draining_cycle_w.write(frame)
                except Exception:
                    pass

            if draining_session_w and draining_session_w is not session_w:
                try:
                    if draining_session_w.isOpened():
                        draining_session_w.write(frame)
                except Exception:
                    pass
            
            # 写入所有活动的步骤视频（需要加锁保护）
            failed_steps = []
            with self._step_writers_lock:
                for step_label, step_info in list(self.step_video_writers.items()):
                    try:
                        writer = step_info.get('writer')
                        if writer and writer.isOpened():
                            writer.write(frame)
                    except Exception as e:
                        print(f"[录制警告] 写入步骤视频 {step_label} 失败: {e}")
                        failed_steps.append(step_label)
                
                # 清理失败的步骤录制器
                for step_label in failed_steps:
                    try:
                        step_info = self.step_video_writers.pop(step_label, None)
                        if step_info and step_info.get('writer'):
                            step_info['writer'].release()
                    except:
                        pass
                    
        except Exception as e:
            print(f"[录制线程] 写入帧异常: {e}")
    
    
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
    
    def _apply_kalman_filter(self, detections):
        """
        对检测结果应用卡尔曼滤波
        
        Args:
            detections: 原始检测结果列表
            
        Returns:
            滤波后的检测结果列表
        """
        if not self._kalman_enabled:
            return detections
        
        current_labels = set()
        smoothed = []
        
        for det in detections:
            label = det['label']
            current_labels.add(label)
            
            x, y, w, h = det['x'], det['y'], det['w'], det['h']
            measurement = [x, y, w, h]
            
            if label not in self._kalman_filters:
                # 新目标，创建滤波器
                self._kalman_filters[label] = KalmanFilter2D(
                    measurement,
                    process_noise=self._kalman_process_noise,
                    measurement_noise=self._kalman_measurement_noise
                )
                smoothed_pos = measurement
            else:
                # 已有目标，更新滤波器
                kf = self._kalman_filters[label]
                kf.predict()
                smoothed_pos = kf.update(measurement)
            
            # 重置消失计数
            self._detection_missing_frames[label] = 0
            
            # 创建平滑后的检测结果
            smoothed_det = det.copy()
            smoothed_det['x'] = float(smoothed_pos[0])
            smoothed_det['y'] = float(smoothed_pos[1])
            smoothed_det['w'] = float(smoothed_pos[2])
            smoothed_det['h'] = float(smoothed_pos[3])
            smoothed.append(smoothed_det)
        
        # 处理消失的目标
        labels_to_remove = []
        for label in list(self._kalman_filters.keys()):
            if label not in current_labels:
                self._detection_missing_frames[label] = self._detection_missing_frames.get(label, 0) + 1
                if self._detection_missing_frames[label] >= self._max_missing_frames:
                    labels_to_remove.append(label)
        
        # 移除长时间消失的目标的滤波器
        for label in labels_to_remove:
            del self._kalman_filters[label]
            if label in self._detection_missing_frames:
                del self._detection_missing_frames[label]
        
        return smoothed
    
    def update_kalman_params(self, process_noise=None, measurement_noise=None, enabled=None, max_missing_frames=None):
        """
        更新卡尔曼滤波参数
        
        Args:
            process_noise: 过程噪声 Q (0.001-0.5, 默认0.03)
                          越小 → 预测更平滑，对快速变化响应慢
                          越大 → 对快速变化响应快，但更抖动
            measurement_noise: 观测噪声 R (0.01-1.0, 默认0.1)
                              越小 → 更信任观测值，更抖动
                              越大 → 更平滑，但对快速变化响应慢
            enabled: 是否启用滤波
            max_missing_frames: 目标消失多少帧后移除滤波器
        """
        if process_noise is not None:
            self._kalman_process_noise = max(0.001, min(0.5, process_noise))
        if measurement_noise is not None:
            self._kalman_measurement_noise = max(0.01, min(1.0, measurement_noise))
        if enabled is not None:
            self._kalman_enabled = enabled
        if max_missing_frames is not None:
            self._max_missing_frames = max(1, min(30, max_missing_frames))
        
        # 清除现有滤波器，使用新参数重建
        self._kalman_filters.clear()
        self._detection_missing_frames.clear()
        
        print(f"[卡尔曼滤波] 参数更新: enabled={self._kalman_enabled}, Q={self._kalman_process_noise}, R={self._kalman_measurement_noise}, max_missing={self._max_missing_frames}")
    
    def _draw_box_simple(self, img, x1, y1, x2, y2, label, conf):
        """检测框绘制（使用 PIL 支持中文）"""
        color_bgr = (0, 255, 0)  # 绿色 BGR
        color_rgb = (0, 255, 0)  # 绿色 RGB
        thickness = 2
        
        # 绘制矩形框
        cv2.rectangle(img, (x1, y1), (x2, y2), color_bgr, thickness)
        
        # 使用 PIL 绘制中文标签
        try:
            img_pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
            draw = ImageDraw.Draw(img_pil)
            font = self._get_chinese_font(18)
            
            # 标签文字
            text = f"{label} {int(conf * 100)}%"
            
            # 计算文字大小
            try:
                bbox = draw.textbbox((0, 0), text, font=font)
                text_w = bbox[2] - bbox[0]
                text_h = bbox[3] - bbox[1]
            except:
                text_w, text_h = 100, 20
            
            # 标签背景位置
            bg_y1 = max(0, y1 - text_h - 8)
            bg_y2 = y1
            bg_x1 = x1
            bg_x2 = x1 + text_w + 10
            
            # 绘制标签背景
            draw.rectangle([bg_x1, bg_y1, bg_x2, bg_y2], fill=(0, 0, 0))
            
            # 绘制标签文字
            draw.text((x1 + 5, bg_y1 + 2), text, font=font, fill=color_rgb)
            
            # 转回 OpenCV 格式
            img = cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)
        except Exception as e:
            # 如果 PIL 失败，回退到 OpenCV（不支持中文）
            print(f"PIL 绘制失败: {e}")
            text = f"{label} {int(conf * 100)}%"
            cv2.putText(img, text, (x1 + 5, y1 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color_bgr, 2)
        
        return img
    
    def _get_chinese_font(self, size=20):
        """获取中文字体"""
        font_paths = [
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
            "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "C:/Windows/Fonts/msyh.ttc",
            "simhei.ttf"
        ]
        
        for path in font_paths:
            try:
                return ImageFont.truetype(path, size)
            except:
                continue
        
        return ImageFont.load_default()
    
    def _draw_box(self, img, x1, y1, x2, y2, label, conf):
        """绘制赛博朋克风格检测框（支持中文）"""
        color_bgr = (255, 238, 118)  # 冰蓝色 BGR
        color_rgb = (118, 238, 255)  # RGB for PIL
        line_len = min(int((x2-x1)*0.2), int((y2-y1)*0.2), 20)
        thickness = 2
        
        # 绘制四角
        cv2.line(img, (x1, y1), (x1 + line_len, y1), color_bgr, thickness)
        cv2.line(img, (x1, y1), (x1, y1 + line_len), color_bgr, thickness)
        cv2.line(img, (x2, y1), (x2 - line_len, y1), color_bgr, thickness)
        cv2.line(img, (x2, y1), (x2, y1 + line_len), color_bgr, thickness)
        cv2.line(img, (x1, y2), (x1 + line_len, y2), color_bgr, thickness)
        cv2.line(img, (x1, y2), (x1, y2 - line_len), color_bgr, thickness)
        cv2.line(img, (x2, y2), (x2 - line_len, y2), color_bgr, thickness)
        cv2.line(img, (x2, y2), (x2, y2 - line_len), color_bgr, thickness)
        
        # 使用 PIL 绘制中文标签
        img_pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
        draw = ImageDraw.Draw(img_pil)
        font = self._get_chinese_font(20)
        
        # 标签文字 (置信度百分比)
        conf_pct = int(conf * 100)
        text = f"{label} {conf_pct}%"
        
        # 计算文字大小
        try:
            bbox = draw.textbbox((0, 0), text, font=font)
            text_w = bbox[2] - bbox[0]
            text_h = bbox[3] - bbox[1]
        except:
            text_w, text_h = 100, 20
        
        # 标签背景位置
        bg_y1 = max(0, y1 - text_h - 10)
        bg_y2 = y1
        bg_x1 = x1
        bg_x2 = x1 + text_w + 10
        
        # 绘制标签背景
        draw.rectangle([bg_x1, bg_y1, bg_x2, bg_y2], fill=(0, 0, 0), outline=color_rgb)
        
        # 绘制标签文字
        draw.text((x1 + 5, bg_y1 + 2), text, font=font, fill=color_rgb)
        
        # 转回 OpenCV 格式
        img = cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)
        
        # 中心点
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        cv2.line(img, (cx - 5, cy), (cx + 5, cy), color_bgr, 1)
        cv2.line(img, (cx, cy - 5), (cx, cy + 5), color_bgr, 1)
        
        return img
    
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
                for det in detections:
                    tid = det.get('track_id', -1)
                    if tid in self._tracking_display_map:
                        det['display_id'] = self._tracking_display_map[tid]
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
    
    # ========== 视频录制功能 ==========
    
    def start_session_recording(self):
        """开始会话视频录制（使用 FFmpeg 进程）"""
        if not self.export_settings or not self.export_settings.get('record_session_video'):
            return
        
        with self._writer_lock:
            if self.video_writer:
                try:
                    self.video_writer.release()
                except Exception:
                    pass
                self.video_writer = None
        
        try:
            filename = f"session_{self.current_session_uuid}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
            filepath = os.path.join(settings.SESSION_VIDEO_DIR, filename)
            
            fps = min(self.export_settings.get('video_fps', 30), 25)
            
            width = self.width if self.width > 0 else 1280
            height = self.height if self.height > 0 else 720
            
            writer = FFmpegRecorder(filepath, width, height, fps)
            if not writer.open():
                print(f"[录制警告] 无法创建会话视频录制器")
                return
            with self._writer_lock:
                self.video_writer = writer
            print(f"开始录制会话视频: {filepath}")
            
            # 记录到数据库
            db = self._get_db_session()
            video_uuid = str(uuid.uuid4())[:8]
            video = VideoClip(
                video_uuid=video_uuid,
                clip_type='session',
                related_id=self.current_session_id,
                file_path=filepath,
                file_name=filename,
                start_time=datetime.now()
            )
            db.add(video)
            db.commit()
            
            # 更新会话的视频ID
            session = db.query(DetectionSession).filter(DetectionSession.id == self.current_session_id).first()
            if session:
                session.video_id = video_uuid
                session.video_path = filepath
                db.commit()
            
            db.close()
        except Exception as e:
            print(f"开始会话录制失败: {e}")
    
    def stop_session_recording(self):
        """停止会话视频录制 - delayed release to flush queued frames"""
        session_id = self.current_session_id

        with self._writer_lock:
            writer = self.video_writer
            self.video_writer = None
            if writer:
                self._draining_session_writer = writer

        if writer:
            def _delayed_release(w, sid):
                time.sleep(1.5)
                with self._writer_lock:
                    if getattr(self, '_draining_session_writer', None) is w:
                        self._draining_session_writer = None
                try:
                    w.release()
                    print("会话视频录制已停止(排空释放)")
                    db = self._get_db_session()
                    session = db.query(DetectionSession).filter(DetectionSession.id == sid).first()
                    if session and session.video_path:
                        video = db.query(VideoClip).filter(VideoClip.file_path == session.video_path).first()
                        if video:
                            video.end_time = datetime.now()
                            if os.path.exists(session.video_path):
                                video.file_size = os.path.getsize(session.video_path)
                            db.commit()
                    db.close()
                except Exception as e:
                    print(f"停止会话录制失败: {e}")

            import threading
            threading.Thread(target=_delayed_release, args=(writer, session_id), daemon=True).start()
    
    def start_cycle_recording(self):
        """开始周期视频录制（使用 FFmpeg 进程）"""
        if not self.export_settings or not self.export_settings.get('record_cycle_video'):
            return
        
        with self._writer_lock:
            if self.cycle_video_writer:
                try:
                    self.cycle_video_writer.release()
                except Exception:
                    pass
                self.cycle_video_writer = None
        
        try:
            filename = f"cycle_{self.current_cycle_uuid}_{datetime.now().strftime('%H%M%S')}.mp4"
            filepath = os.path.join(settings.CYCLE_VIDEO_DIR, filename)
            
            fps = min(self.export_settings.get('video_fps', 30), 25)
            
            width = self.width if self.width > 0 else 1280
            height = self.height if self.height > 0 else 720
            
            writer = FFmpegRecorder(filepath, width, height, fps)
            if not writer.open():
                print(f"[录制警告] 无法创建周期视频录制器")
                return
            with self._writer_lock:
                self.cycle_video_writer = writer
            print(f"开始录制周期视频: {filename}")
            
            # 记录到数据库
            db = self._get_db_session()
            video_uuid = str(uuid.uuid4())[:8]
            video = VideoClip(
                video_uuid=video_uuid,
                clip_type='cycle',
                related_id=self.current_cycle_id,
                file_path=filepath,
                file_name=filename,
                start_time=datetime.now()
            )
            db.add(video)
            db.commit()
            
            # 更新周期的视频ID
            cycle = db.query(DetectionCycle).filter(DetectionCycle.id == self.current_cycle_id).first()
            if cycle:
                cycle.video_id = video_uuid
                cycle.video_path = filepath
                db.commit()
            
            db.close()
        except Exception as e:
            print(f"开始周期录制失败: {e}")
    
    def stop_cycle_recording(self):
        """停止周期视频录制 - delayed release to flush queued frames"""
        with self._writer_lock:
            writer = self.cycle_video_writer
            self.cycle_video_writer = None
            if writer:
                self._draining_cycle_writer = writer

        if writer:
            def _delayed_release(w):
                time.sleep(1.5)
                with self._writer_lock:
                    if getattr(self, '_draining_cycle_writer', None) is w:
                        self._draining_cycle_writer = None
                try:
                    w.release()
                    print("周期视频录制已停止(排空释放)")
                except Exception as e:
                    print(f"停止周期录制失败: {e}")

            import threading
            threading.Thread(target=_delayed_release, args=(writer,), daemon=True).start()
    
    def start_step_recording(self, step_label: str):
        """开始步骤视频录制（使用 FFmpeg 进程）"""
        if not self.export_settings or not self.export_settings.get('record_step_video'):
            return None
        
        try:
            with self._step_writers_lock:
                # 先关闭同标签的旧 writer，防止 FFmpeg 进程泄漏
                if step_label in self.step_video_writers:
                    old_info = self.step_video_writers.pop(step_label, None)
                    if old_info and old_info.get('writer'):
                        try:
                            old_info['writer'].release()
                        except:
                            pass
                
                if len(self.step_video_writers) >= 1:
                    print(f"[录制警告] 步骤视频录制已达上限(1)，跳过: {step_label}")
                    return None
                
                video_uuid = str(uuid.uuid4())[:8]
                # 使用 .mp4 格式
                filename = f"step_{step_label}_{video_uuid}_{datetime.now().strftime('%H%M%S')}.mp4"
                filepath = os.path.join(settings.STEP_VIDEO_DIR, filename)
                
                fps = min(self.export_settings.get('video_fps', 30), 25)  # 限制FPS
                
                # 确保宽高有效
                width = self.width if self.width > 0 else 1280
                height = self.height if self.height > 0 else 720
                
                # 使用 FFmpegRecorder
                writer = FFmpegRecorder(filepath, width, height, fps)
                if not writer.open():
                    print(f"[录制警告] 无法创建步骤视频录制器: {step_label}")
                    return None
                    
                self.step_video_writers[step_label] = {
                    'writer': writer,
                    'filepath': filepath,
                    'filename': filename,
                    'video_uuid': video_uuid,
                    'start_time': datetime.now(),
                    'frame_size': (width, height)
                }
                print(f"[调试] 开始录制步骤视频: {step_label} -> {filename}")
                return video_uuid
        except Exception as e:
            print(f"开始步骤录制失败: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def stop_step_recording(self, step_label: str) -> dict:
        """停止步骤视频录制，返回视频信息"""
        step_video = None
        
        # 在锁内获取并移除 writer
        with self._step_writers_lock:
            if step_label not in self.step_video_writers:
                return None
            step_video = self.step_video_writers.pop(step_label)
        
        # 在锁外释放 writer 和写数据库（避免长时间持锁）
        try:
            writer = step_video.get('writer')
            if writer:
                writer.release()
            
            # 保存视频信息到数据库
            db = self._get_db_session()
            video = VideoClip(
                video_uuid=step_video['video_uuid'],
                clip_type='step',
                related_id=self.current_cycle_id,
                file_path=step_video['filepath'],
                file_name=step_video['filename'],
                start_time=step_video['start_time'],
                end_time=datetime.now()
            )
            db.add(video)
            db.commit()
            db.close()
            
            print(f"[调试] 步骤视频录制已停止: {step_label}")
            return {
                'video_uuid': step_video['video_uuid'],
                'filepath': step_video['filepath']
            }
        except Exception as e:
            print(f"停止步骤录制失败: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def write_frame_to_recorders(self, frame):
        """
        将帧写入录制队列（向后兼容方法）
        实际写入由独立的录制线程处理，避免与 CUDA 冲突
        """
        self._enqueue_frame_for_recording(frame)
    
    def _close_all_writers(self):
        """关闭所有 FFmpeg 录制进程，防止资源泄漏"""
        with self._writer_lock:
            if self.video_writer:
                try:
                    self.video_writer.release()
                except Exception:
                    pass
                self.video_writer = None
            if self.cycle_video_writer:
                try:
                    self.cycle_video_writer.release()
                except Exception:
                    pass
                self.cycle_video_writer = None
        with self._step_writers_lock:
            for step_label, step_info in list(self.step_video_writers.items()):
                try:
                    writer = step_info.get('writer')
                    if writer:
                        writer.release()
                except:
                    pass
            self.step_video_writers.clear()
        print("[资源清理] 所有录制器已关闭")
    
    def pause(self):
        """暂停：停止画面更新和检测，但保持当前帧"""
        self.is_running = False
        self.is_detecting = False
        # v2.7.3: 暂停也必须熄灭工作指示灯，前端 Monitor 的"停止"按钮调的是 pause
        # 之前未调用导致灯保持常亮，关软件后还亮
        try:
            from backend.api.alarm import alarm_router
            alarm_router.stop_idle_light(channel_id=self.channel_id)
        except Exception:
            pass
        # 先停止推理线程，避免残留
        self._stop_inference_thread()
        # 停止录制线程和 FFmpeg 进程，防止资源泄漏
        self._stop_recording_thread()
        self._close_all_writers()
        # 等待捕获线程退出
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        with self.detection_lock:
            self.current_detections = []
        # 清理推理缓存
        self._clear_inference_caches()

        # Camera/Hikvision: release the device so it's not locked
        # (current_frame is kept for frozen display, model stays loaded for fast resume)
        if self.source_type == 'camera' and self.capture:
            try:
                self.capture.release()
            except Exception as e:
                print(f"[pause] release camera failed: {e}")
            self.capture = None
            print("已暂停：摄像头已释放，保留模型和画面")
        elif self.source_type == 'hikvision':
            self._release_hik_camera()
            print("已暂停：海康相机已释放，保留模型和画面")
        elif self.source_type == 'hcnetsdk':
            self._release_hcnet_session()
            print("[pause] HCNetSDK released, model kept")
        else:
            print("已暂停：画面和检测都停止")
    
    def _reopen_camera(self):
        """Re-open USB camera that was released during pause"""
        import platform
        try:
            if platform.system() == "Windows":
                self.capture = cv2.VideoCapture(self.camera_index, cv2.CAP_DSHOW)
            else:
                self.capture = cv2.VideoCapture(self.camera_index)
            if not self.capture.isOpened():
                print(f"[resume] 摄像头 {self.camera_index} 打开失败")
                self.capture = None
                return False
            self.capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc('M','J','P','G'))
            self.capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            self.capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            self.capture.set(cv2.CAP_PROP_FPS, self.fps)
            print(f"[resume] 摄像头已重新打开 (MJPG): index={self.camera_index}")
            return True
        except Exception as e:
            print(f"[resume] 重新打开摄像头失败: {e}")
            return False

    def _reopen_hik_camera(self):
        """Re-open Hikvision camera that was released during pause (preserves model)"""
        if not HIK_SDK_AVAILABLE:
            print("[resume] 海康 SDK 不可用")
            return False
        try:
            self.hik_camera = MvCamera()
            device_list = MV_CC_DEVICE_INFO_LIST()
            ret = MvCamera.MV_CC_EnumDevices(MV_USB_DEVICE | MV_GIGE_DEVICE, device_list)
            if ret != 0 or device_list.nDeviceNum == 0:
                raise Exception("未发现海康相机设备")
            if self.hik_device_index >= device_list.nDeviceNum:
                raise Exception(f"设备索引 {self.hik_device_index} 无效")
            st_device_info = cast(device_list.pDeviceInfo[self.hik_device_index], POINTER(MV_CC_DEVICE_INFO)).contents
            ret = self.hik_camera.MV_CC_CreateHandle(st_device_info)
            if ret != 0:
                raise Exception(f"创建句柄失败: {hex(ret)}")
            ret = self.hik_camera.MV_CC_OpenDevice(MV_ACCESS_Exclusive, 0)
            if ret != 0:
                self.hik_camera.MV_CC_DestroyHandle()
                raise Exception(f"打开设备失败: {hex(ret)}")
            self.hik_camera.MV_CC_SetEnumValue("TriggerMode", MV_TRIGGER_MODE_OFF)
            st_param = MVCC_INTVALUE()
            memset(byref(st_param), 0, sizeof(MVCC_INTVALUE))
            ret = self.hik_camera.MV_CC_GetIntValue("PayloadSize", st_param)
            if ret != 0:
                self._release_hik_camera()
                raise Exception(f"获取 PayloadSize 失败: {hex(ret)}")
            self.hik_payload_size = st_param.nCurValue
            ret = self.hik_camera.MV_CC_StartGrabbing()
            if ret != 0:
                self._release_hik_camera()
                raise Exception(f"开始取流失败: {hex(ret)}")
            self.hik_data_buf = (c_ubyte * self.hik_payload_size)()
            self.hik_frame_info = MV_FRAME_OUT_INFO_EX()
            memset(byref(self.hik_frame_info), 0, sizeof(MV_FRAME_OUT_INFO_EX))
            print(f"[resume] 海康相机已重新打开: index={self.hik_device_index}")
            return True
        except Exception as e:
            print(f"[resume] 重新打开海康相机失败: {e}")
            self._release_hik_camera()
            return False

    def resume(self):
        """恢复：从暂停状态恢复，重新启动视频流和推理"""
        # Re-open camera if it was released during pause
        if self.capture is None and self.source_type == 'camera':
            if not self._reopen_camera():
                return False

        if self.source_type == 'hikvision' and self.hik_camera is None:
            if not self._reopen_hik_camera():
                return False

        if self.capture is None and self.source_type not in ('hikvision', 'image'):
            print("无法恢复：没有可用的视频源")
            return False

        # 确保旧捕获线程已完全停止，避免双重线程
        if self._thread and self._thread.is_alive():
            self.is_running = False
            self._thread.join(timeout=1.0)

        self.is_running = True
        self.is_detecting = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()

        if self.model is not None:
            self._start_inference_thread()

        self._ensure_session_active()

        # v2.7.3: 恢复检测时重新点亮工作指示灯（pause 已熄，否则灯不会再亮）
        try:
            from backend.api.alarm import alarm_router
            alarm_router.start_idle_light(channel_id=self.channel_id)
        except Exception as _e:
            print(f"[Alarm/Source] resume start_idle_light 失败: {_e}")

        # v2.7.5b: 从暂停恢复时同步唤醒扫码器（原代码仅在 start_detection 里调过，导致 resume 漏发 LON）
        try:
            from backend.services.scanner import get_scanner_service
            print(f"[Scanner/Source] resume ch={self.channel_id} → start_scanning")
            get_scanner_service().start_scanning(channel_id=self.channel_id)
        except Exception as _e:
            import traceback as _tb
            print(f"[Scanner/Source] resume start_scanning 失败: {_e}\n{_tb.format_exc()}")

        print("已恢复：视频流和推理重新启动")
        return True
    
    def standby(self):
        """Standby: stop inference but keep the video capture thread running."""
        self.is_detecting = False
        # v2.7.3: 待机时也熄灭工作指示灯（语义上"不在检测"就不应该亮工作灯）
        try:
            from backend.api.alarm import alarm_router
            alarm_router.stop_idle_light(channel_id=self.channel_id)
        except Exception as _e:
            print(f"[Alarm/Source] standby stop_idle_light 失败: {_e}")

        # v2.7.5b: 待机时关闭扫码器 LON
        try:
            from backend.services.scanner import get_scanner_service
            print(f"[Scanner/Source] standby ch={self.channel_id} → stop_scanning")
            get_scanner_service().stop_scanning(channel_id=self.channel_id)
        except Exception as _e:
            print(f"[Scanner/Source] standby stop_scanning 失败: {_e}")

        self._stop_inference_thread()
        self._stop_recording_thread()
        self._close_all_writers()
        with self.detection_lock:
            self.current_detections = []
        self._clear_inference_caches()
        print("已待机：检测停止，画面继续")

    def resume_inference(self):
        """Resume inference from standby (capture thread already running)."""
        if not self.is_running:
            print("[resume_inference] 视频流未运行，无法恢复推理")
            return False
        if self.model is None:
            print("[resume_inference] 模型未加载，无法恢复推理")
            return False
        self.is_detecting = True
        self._start_inference_thread()
        
        self._ensure_session_active()
        
        if self.recording_enabled:
            self._start_recording_thread()
        self.start_session_recording()

        # v2.7.3: 从待机恢复推理时重新点亮工作指示灯
        try:
            from backend.api.alarm import alarm_router
            alarm_router.start_idle_light(channel_id=self.channel_id)
        except Exception as _e:
            print(f"[Alarm/Source] resume_inference start_idle_light 失败: {_e}")

        # v2.7.5b: 从待机恢复时同步唤醒扫码器（关键修复——之前走 resume_inference 的路径永远不发 LON）
        try:
            from backend.services.scanner import get_scanner_service
            print(f"[Scanner/Source] resume_inference ch={self.channel_id} → start_scanning")
            get_scanner_service().start_scanning(channel_id=self.channel_id)
        except Exception as _e:
            import traceback as _tb
            print(f"[Scanner/Source] resume_inference start_scanning 失败: {_e}\n{_tb.format_exc()}")

        print("已从待机恢复推理")
    
    def stop(self, release_model: bool = True):
        """停止当前输入源（完全停止并释放资源）
        
        Args:
            release_model: If False, keep the YOLO model in memory for reuse
                           after switching input sources.
        """
        self.is_running = False
        self.is_detecting = False
        
        # 停止推理线程
        self._stop_inference_thread()
        
        # 关闭推理线程池
        self._shutdown_inference_executor()
        
        # 停止录制线程
        self._stop_recording_thread()
        
        # 等待捕获线程结束（多次尝试）
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
            # 如果线程还在运行，再等待一次
            if self._thread.is_alive():
                print("[警告] 捕获线程第一次超时，再次等待...")
                self._thread.join(timeout=2.0)
            # 如果还是没有结束，记录警告
            if self._thread.is_alive():
                print("[错误] 捕获线程未能结束，可能存在死锁，强制继续")
        
        # Release HCNetSDK
        if self.source_type == 'hcnetsdk':
            self._release_hcnet_session()
        
        # 释放海康相机资源
        if self.source_type == 'hikvision':
            self._release_hik_camera()
        
        # 释放摄像头/视频资源
        if self.capture:
            try:
                self.capture.release()
            except Exception as e:
                print(f"[警告] 释放摄像头时出错: {e}")
            self.capture = None
        
        if release_model:
            self._release_model()
        else:
            self._shutdown_inference_executor()
            print("[VideoManager] 保留模型，仅停止输入源")
        
        # 等待一小段时间确保资源被系统释放
        time.sleep(0.3)
        
        self.source_type = None
        self.current_frame = None
        self._thread = None
        with self.detection_lock:
            self.current_detections = []
        
        # 清理所有内存缓存
        self._clear_all_caches()
        
        print("[VideoManager] 已完全停止并释放资源")
    
    def _clear_all_caches(self):
        """清理所有内存缓存 - 防止内存泄漏"""
        import gc
        
        print("[缓存清理] 开始清理内存缓存...")
        
        # 1. 清理卡尔曼滤波器缓存
        self._kalman_filters.clear()
        self._detection_missing_frames.clear()
        self._detection_history.clear()
        
        # 2. 清理步骤截图缓存（这个可能很大！）
        screenshot_count = len(self.step_screenshots)
        self.step_screenshots.clear()
        
        # 3. 限制事件日志大小（保留最近500条）
        if len(self.events_log) > 500:
            self.events_log = self.events_log[-500:]
        
        # 4. 清理推理相关缓存
        with self._inference_frame_lock:
            self._latest_frame_for_inference = None
            self._latest_frame_original_size = None
            self._latest_display_small_for_stats = None
        with self._confirmed_detections_lock:
            self._confirmed_detections = []
        
        # 5. 清理帧计数缓存
        self.step_consecutive_frames.clear()
        self.step_frame_confirmed.clear()
        self._step_gap_count.clear()
        
        self.step_static_triggered.clear()
        
        # 6. 限制周期时间记录（保留最近50条）
        if len(self.cycle_times) > 50:
            self.cycle_times = self.cycle_times[-50:]
        if len(self.ng_cycle_times) > 50:
            self.ng_cycle_times = self.ng_cycle_times[-50:]
        for _lbl in list(self.step_durations_history.keys()):
            if len(self.step_durations_history[_lbl]) > 100:
                self.step_durations_history[_lbl] = self.step_durations_history[_lbl][-100:]
        
        # 7. 强制垃圾回收
        gc.collect()
        
        # 8. 清理 CUDA 缓存
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                allocated = torch.cuda.memory_allocated() / 1024**2
                cached = torch.cuda.memory_reserved() / 1024**2
                print(f"[缓存清理] GPU显存: 已分配={allocated:.1f}MB, 缓存={cached:.1f}MB")
        except Exception as e:
            print(f"[缓存清理] 清理 CUDA 缓存时出错: {e}")
        
        print(f"[缓存清理] 完成 - 清理了 {screenshot_count} 张截图缓存")
    
    def _save_counters_snapshot(self):
        """定期保存计数器快照到会话（防止闪退丢失数据）"""
        if not self.current_session_id or not self.counters:
            return
        
        try:
            db = self._get_db_session()
            session = db.query(DetectionSession).filter(
                DetectionSession.id == self.current_session_id
            ).first()
            
            if session:
                session.counters_snapshot = self.counters.copy()
                db.commit()
                print(f"[数据持久化] 计数器已保存: {self.counters}")
            
            db.close()
        except Exception as e:
            print(f"[数据持久化] 保存计数器失败: {e}")
    
    def _gpu_deep_cleanup(self):
        """GPU 显存深度清理 - 每10分钟执行一次，防止长时间运行显存碎片累积"""
        import gc
        try:
            import torch
            if torch.cuda.is_available():
                before_alloc = torch.cuda.memory_allocated() / 1024**2
                before_cached = torch.cuda.memory_reserved() / 1024**2
                
                gc.collect()
                torch.cuda.empty_cache()
                
                after_alloc = torch.cuda.memory_allocated() / 1024**2
                after_cached = torch.cuda.memory_reserved() / 1024**2
                freed = before_cached - after_cached
                
                if freed > 1:
                    print(f"[GPU清理] 释放显存: {freed:.1f}MB (分配: {after_alloc:.1f}MB, 缓存: {after_cached:.1f}MB)")
            else:
                gc.collect()
        except Exception as e:
            print(f"[GPU清理] 清理失败: {e}")
    
    def _periodic_cache_cleanup(self):
        """周期性缓存清理 - 在检测循环中定期调用"""
        import gc
        
        # 1. 限制事件日志大小
        if len(self.events_log) > 1000:
            self.events_log = self.events_log[-500:]
            print("[缓存清理] 事件日志已裁剪至500条")
        
        # 2. 限制截图缓存（每个步骤只保留最新截图，这里额外检查总数）
        if len(self.step_screenshots) > 100:
            # 保留最后添加的50个
            keys = list(self.step_screenshots.keys())[-50:]
            self.step_screenshots = {k: self.step_screenshots[k] for k in keys}
            print("[缓存清理] 截图缓存已裁剪至50张")
        
        # 3. 清理长时间未更新的卡尔曼滤波器
        current_time = time.time()
        stale_filters = [k for k, v in self._detection_missing_frames.items() 
                        if v > self._max_missing_frames * 2]
        for k in stale_filters:
            if k in self._kalman_filters:
                del self._kalman_filters[k]
            if k in self._detection_missing_frames:
                del self._detection_missing_frames[k]
        
        # 4. 轻量级垃圾回收
        gc.collect(generation=0)  # 只清理最年轻的一代，速度快
    
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
class CameraStartRequest(BaseModel):
    device_index: int = 0
    width: int = 1280
    height: int = 720
    fps: int = 60

class VideoStartRequest(BaseModel):
    file_path: str
    speed: float = 1.0  # 视频倍速

class VideoSpeedRequest(BaseModel):
    speed: float  # 视频倍速

class VideoProgressRequest(BaseModel):
    progress: float  # 视频进度 (0-1)

class ImageSetRequest(BaseModel):
    file_path: str

class DetectionStartRequest(BaseModel):
    model_path: str
    conf: float = 0.25
    iou: float = 0.45

class StreamConfigRequest(BaseModel):
    frame_limit_enabled: bool = False  # 是否启用帧率限制
    target_stream_fps: int = 30  # 目标流帧率
    use_half: bool = False  # FP16 半精度推理
    mediapipe_enabled: bool = False  # MediaPipe 骨架叠加
    mediapipe_pose: bool = True  # 显示姿态骨架
    mediapipe_hands: bool = True  # 显示手部关键点
    mediapipe_confidence: float = 0.7  # 检测置信度 (0.1-1.0)
    mediapipe_interval: int = 2  # MediaPipe 处理间隔（帧）

class DeviceConfigRequest(BaseModel):
    device: str = 'auto'  # 推理设备: 'auto', 'cpu', 'cuda:0', 'cuda:1' 等


# 摄像头列表缓存
_cameras_cache = {
    "cameras": [],
    "last_update": 0,
    "cache_duration": 60  # 缓存60秒
}

def _get_camera_name_linux(index):
    """Linux: 尝试获取摄像头真实名称"""
    try:
        name_path = f"/sys/class/video4linux/video{index}/name"
        if os.path.exists(name_path):
            with open(name_path, 'r') as f:
                return f.read().strip()
    except:
        pass
    return None

def _detect_cameras_linux():
    """Linux: 快速检测摄像头（通过读取 /dev/video* 设备）"""
    import glob
    cameras = []
    video_devices = glob.glob("/dev/video*")
    
    # Skip probing the device currently held by the active capture
    active_index = None
    if video_manager.source_type == 'camera' and video_manager.capture is not None:
        active_index = video_manager.camera_index
    
    # 按数字排序（video10 应该在 video2 之后）
    def extract_index(path):
        try:
            return int(path.replace("/dev/video", ""))
        except:
            return 999
    video_devices = sorted(video_devices, key=extract_index)
    
    for device in video_devices:
        try:
            index = int(device.replace("/dev/video", ""))
            
            name = _get_camera_name_linux(index)
            
            if name and "Virtual" in name:
                cameras.append({"index": index, "name": f"{name} (索引 {index})"})
            elif name:
                if index % 2 != 0:
                    continue
                cameras.append({"index": index, "name": f"{name} (索引 {index})"})
            else:
                # No sysfs name — need to probe, but skip if currently held
                if index == active_index:
                    cameras.append({"index": index, "name": f"摄像头 {index} (使用中)"})
                    continue
                cap = cv2.VideoCapture(index)
                if cap.isOpened():
                    cap.release()
                    cameras.append({"index": index, "name": f"摄像头 {index}"})
        except:
            continue
    
    return cameras

def _detect_cameras_windows():
    """Windows: 检测摄像头（减少尝试次数）"""
    cameras = []
    current_camera_index = None
    if video_manager.source_type == 'camera' and video_manager.capture is not None:
        current_camera_index = video_manager.camera_index
    
    # 只尝试前5个索引，减少等待时间
    for i in range(5):
        # 如果这个摄像头正在被使用，直接添加到列表（不尝试打开）
        if current_camera_index is not None and i == current_camera_index:
            cameras.append({
                "index": i,
                "name": f"摄像头 {i} (使用中)" if i > 0 else "默认摄像头 (索引 0, 使用中)"
            })
            continue
        
        try:
            # 设置较短的超时（Windows 上可能不生效，但尝试一下）
            cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)  # Windows 上用 DirectShow 更快
            if cap.isOpened():
                cameras.append({
                    "index": i,
                    "name": f"摄像头 {i}" if i > 0 else "默认摄像头 (索引 0)"
                })
                cap.release()
        except:
            continue
    
    return cameras

# API 端点
@router.get("/cameras")
def list_cameras(refresh: bool = False):
    """列出可用摄像头
    
    Args:
        refresh: 是否强制刷新缓存，默认使用缓存
    """
    import platform
    print(f"[API] /cameras 被调用, refresh={refresh}, platform={platform.system()}")
    current_time = time.time()
    
    # 检查缓存是否有效
    if not refresh and _cameras_cache["cameras"] and \
       (current_time - _cameras_cache["last_update"]) < _cameras_cache["cache_duration"]:
        print(f"[API] 使用缓存, cameras={_cameras_cache['cameras']}")
        return {"cameras": _cameras_cache["cameras"], "cached": True}
    
    # 根据操作系统选择检测方法
    if platform.system() == "Linux":
        cameras = _detect_cameras_linux()
    else:
        cameras = _detect_cameras_windows()
    
    print(f"[API] 检测到USB摄像头: {cameras}")
    
    # 如果没有检测到任何摄像头，返回默认项
    if not cameras:
        cameras = [{"index": 0, "name": "默认摄像头 (索引 0)"}]
    
    # 更新缓存
    _cameras_cache["cameras"] = cameras
    _cameras_cache["last_update"] = current_time
    
    return {"cameras": cameras, "cached": False}

@router.get("/gpu/list")
def get_gpu_list():
    """获取可用的GPU设备列表"""
    import torch
    
    devices = [{"id": "cpu", "name": "CPU (中央处理器)", "type": "CPU"}]
    
    if torch.cuda.is_available():
        # 添加自动选择选项
        devices.insert(0, {"id": "auto", "name": "自动选择 (优先GPU)", "type": "AUTO"})
        
        # 添加所有可用的CUDA设备
        for i in range(torch.cuda.device_count()):
            gpu_name = torch.cuda.get_device_name(i)
            memory_total = torch.cuda.get_device_properties(i).total_memory / (1024**3)  # GB
            devices.append({
                "id": f"cuda:{i}",
                "name": f"GPU {i}: {gpu_name} ({memory_total:.1f}GB)",
                "type": "GPU",
                "index": i,
                "memory_gb": round(memory_total, 1)
            })
    
    return {
        "devices": devices,
        "cuda_available": torch.cuda.is_available(),
        "cuda_version": torch.version.cuda if torch.cuda.is_available() else None,
        "gpu_count": torch.cuda.device_count() if torch.cuda.is_available() else 0
    }

@router.get("/gpu/current")
def get_current_device():
    """获取当前使用的推理设备"""
    return {
        "device": video_manager.device,
        "current_device_info": video_manager.current_device_info,
        "model_loaded": video_manager.model is not None
    }

@router.post("/gpu/set")
def set_device(req: DeviceConfigRequest):
    """设置推理设备（需要重新加载模型生效）"""
    video_manager.device = req.device
    
    # 保存设备配置到文件
    video_manager._save_device_config()
    
    # 如果模型已加载，重新加载以应用新设备
    if video_manager.model is not None and video_manager.model_path:
        success = video_manager.load_model(video_manager.model_path)
        if success:
            return {
                "status": "success",
                "message": f"已切换到 {video_manager.current_device_info['name']}",
                "device": video_manager.device,
                "current_device_info": video_manager.current_device_info
            }
        else:
            return {
                "status": "error",
                "message": "切换设备失败，模型重新加载出错"
            }
    
    return {
        "status": "success",
        "message": f"设备已设置为 {req.device}，将在下次加载模型时生效",
        "device": video_manager.device,
        "current_device_info": video_manager.current_device_info
    }

@router.get("/stream/config")
def get_stream_config():
    """获取视频流配置"""
    return {
        "frame_limit_enabled": video_manager.frame_limit_enabled,
        "target_stream_fps": video_manager.target_stream_fps,
        "use_half": video_manager.use_half,
        "mediapipe_enabled": video_manager.mediapipe_enabled,
        "mediapipe_pose": video_manager.mediapipe_pose,
        "mediapipe_hands": video_manager.mediapipe_hands,
        "mediapipe_confidence": video_manager.mediapipe_confidence,
        "mediapipe_interval": video_manager._mp_process_interval
    }

@router.post("/stream/config")
def set_stream_config(req: StreamConfigRequest):
    """设置视频流配置（帧率限制 + FP16 + MediaPipe）"""
    video_manager.frame_limit_enabled = req.frame_limit_enabled
    video_manager.target_stream_fps = max(1, min(120, req.target_stream_fps))
    video_manager.use_half = req.use_half
    
    mp_was_enabled = video_manager.mediapipe_enabled
    old_conf = video_manager.mediapipe_confidence
    video_manager.mediapipe_enabled = req.mediapipe_enabled
    video_manager.mediapipe_pose = req.mediapipe_pose
    video_manager.mediapipe_hands = req.mediapipe_hands
    video_manager.mediapipe_confidence = max(0.1, min(1.0, req.mediapipe_confidence))
    video_manager._mp_process_interval = max(1, min(10, req.mediapipe_interval))
    
    conf_changed = abs(video_manager.mediapipe_confidence - old_conf) > 0.01
    if not req.mediapipe_enabled and mp_was_enabled:
        video_manager._release_mediapipe()
    elif conf_changed and req.mediapipe_enabled:
        video_manager._release_mediapipe()
    
    # 保存配置到文件
    video_manager._save_device_config()
    
    return {
        "status": "success",
        "frame_limit_enabled": video_manager.frame_limit_enabled,
        "target_stream_fps": video_manager.target_stream_fps,
        "use_half": video_manager.use_half,
        "mediapipe_enabled": video_manager.mediapipe_enabled,
        "mediapipe_pose": video_manager.mediapipe_pose,
        "mediapipe_hands": video_manager.mediapipe_hands,
        "mediapipe_confidence": video_manager.mediapipe_confidence,
        "mediapipe_interval": video_manager._mp_process_interval
    }


# ========== 画面变换配置 API（按通道独立） ==========

class TransformConfigRequest(BaseModel):
    rotation: Optional[int] = 0   # 0 / 90 / 180 / 270
    flip_h: Optional[bool] = False
    flip_v: Optional[bool] = False


@router.get("/transform/config")
def get_transform_config(channel: int = 0):
    """获取指定通道的画面旋转/镜像配置"""
    mgr = _get_mgr(channel)
    return {
        "channel": channel,
        "rotation": int(mgr.video_rotation),
        "flip_h": bool(mgr.video_flip_h),
        "flip_v": bool(mgr.video_flip_v),
    }


@router.post("/transform/config")
def set_transform_config(req: TransformConfigRequest, channel: int = 0):
    """设置指定通道的画面旋转/镜像配置，立即对后续帧生效"""
    mgr = _get_mgr(channel)
    rot = req.rotation if req.rotation in (0, 90, 180, 270) else 0
    mgr.video_rotation = rot
    mgr.video_flip_h = bool(req.flip_h)
    mgr.video_flip_v = bool(req.flip_v)
    mgr._save_device_config()
    return {
        "status": "success",
        "channel": channel,
        "rotation": int(mgr.video_rotation),
        "flip_h": bool(mgr.video_flip_h),
        "flip_v": bool(mgr.video_flip_v),
    }


# ========== 卡尔曼滤波配置 API ==========

class KalmanConfigRequest(BaseModel):
    enabled: Optional[bool] = None
    process_noise: Optional[float] = None  # Q: 0.001-0.5
    measurement_noise: Optional[float] = None  # R: 0.01-1.0
    max_missing_frames: Optional[int] = None  # 1-30

@router.get("/kalman/config")
def get_kalman_config():
    """获取卡尔曼滤波配置"""
    return {
        "enabled": video_manager._kalman_enabled,
        "process_noise": video_manager._kalman_process_noise,
        "measurement_noise": video_manager._kalman_measurement_noise,
        "max_missing_frames": video_manager._max_missing_frames
    }

@router.post("/kalman/config")
def set_kalman_config(req: KalmanConfigRequest):
    """设置卡尔曼滤波参数"""
    video_manager.update_kalman_params(
        process_noise=req.process_noise,
        measurement_noise=req.measurement_noise,
        enabled=req.enabled,
        max_missing_frames=req.max_missing_frames
    )
    return {
        "status": "success",
        "enabled": video_manager._kalman_enabled,
        "process_noise": video_manager._kalman_process_noise,
        "measurement_noise": video_manager._kalman_measurement_noise,
        "max_missing_frames": video_manager._max_missing_frames
    }


@router.post("/camera/start")
def start_camera(req: CameraStartRequest, channel: int = Query(0)):
    """启动摄像头"""
    try:
        mgr = _get_mgr(channel)
        mgr.start_camera(
            device_index=req.device_index,
            width=req.width,
            height=req.height,
            fps=req.fps
        )
        return {"status": "success", "message": f"摄像头已启动 (ch{channel})"}
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        print(f"[API] /camera/start 失败 (ch{channel}): {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/camera/stop")
def stop_camera(channel: int = Query(0)):
    """停止摄像头"""
    mgr = _get_mgr(channel)
    mgr.stop()
    return {"status": "success", "message": f"摄像头已停止 (ch{channel})"}


# ========== RTSP 网络视频流 API 端点 ==========
class RtspStartRequest(BaseModel):
    url: str
    fps: int = 25

@router.post("/rtsp/start")
def start_rtsp(req: RtspStartRequest, channel: int = Query(0)):
    """启动 RTSP 网络视频流"""
    try:
        mgr = _get_mgr(channel)
        mgr.start_rtsp(url=req.url, fps=req.fps)
        return {"status": "success", "message": f"RTSP 流已启动 (ch{channel})"}
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        print(f"[API] /rtsp/start 失败 (ch{channel}): {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# ========== 海康工业相机 API 端点 ==========
class HikvisionStartRequest(BaseModel):
    device_index: int = 0
    width: int = 1280
    height: int = 720
    fps: int = 30

@router.get("/hikvision/cameras")
def list_hikvision_cameras():
    """列出可用的海康工业相机"""
    hik_log(f"API /hikvision/cameras 被调用, HIK_SDK_AVAILABLE={HIK_SDK_AVAILABLE}")
    if not HIK_SDK_AVAILABLE:
        hik_log("SDK不可用，返回空列表", "WARN")
        return {
            "cameras": [],
            "available": False,
            "message": "海康 SDK 未加载，无法使用海康相机功能"
        }
    
    try:
        cameras = get_hikvision_device_list()
        hik_log(f"检测到海康相机: {cameras}", "SUCCESS" if cameras else "WARN")
        return {
            "cameras": cameras,
            "available": True,
            "count": len(cameras)
        }
    except Exception as e:
        hik_log(f"枚举海康相机失败: {e}", "ERROR")
        import traceback
        hik_log(traceback.format_exc(), "ERROR")
        return {
            "cameras": [],
            "available": False,
            "message": str(e)
        }

@router.post("/hikvision/start")
def start_hikvision_camera(req: HikvisionStartRequest, channel: int = Query(0)):
    """启动海康工业相机"""
    hik_log(f"API /hikvision/start 收到请求: device_index={req.device_index}, {req.width}x{req.height}@{req.fps}fps, ch={channel}")
    mgr = _get_mgr(channel)
    try:
        mgr.start_hikvision_camera(
            device_index=req.device_index,
            width=req.width,
            height=req.height,
            fps=req.fps
        )
        hik_log("API /hikvision/start 成功", "SUCCESS")
        return {"status": "success", "message": "海康相机已启动"}
    except Exception as e:
        hik_log(f"API /hikvision/start 失败: {e}", "ERROR")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/hikvision/stop")
def stop_hikvision_camera(channel: int = Query(0)):
    """停止海康工业相机"""
    _get_mgr(channel).stop()
    return {"status": "success", "message": "海康相机已停止"}

@router.get("/hikvision/status")
def get_hikvision_status():
    """获取海康相机状态"""
    return {
        "sdk_available": HIK_SDK_AVAILABLE,
        "is_connected": video_manager.source_type == 'hikvision' and video_manager.is_running,
        "device_index": video_manager.hik_device_index if video_manager.source_type == 'hikvision' else None
    }


# ========== HCNetSDK API endpoints ==========

class HCNetSDKStartRequest(BaseModel):
    ip: str
    port: int = 8000
    username: str = "admin"
    password: str = ""
    channel: int = 1
    stream_type: int = 1  # 0=main, 1=sub
    fps: int = 25

@router.post("/hcnetsdk/start")
def start_hcnetsdk(req: HCNetSDKStartRequest, channel: int = Query(0)):
    """Connect to NVR/IP camera via HCNetSDK."""
    try:
        mgr = _get_mgr(channel)
        mgr.start_hcnetsdk(
            ip=req.ip,
            port=req.port,
            username=req.username,
            password=req.password,
            channel=req.channel,
            stream_type=req.stream_type,
            fps=req.fps,
        )
        return {
            "status": "success",
            "message": f"HCNetSDK connected {req.ip}:{req.port} ch{req.channel} (ws{channel})",
            "resolution": f"{mgr.width}x{mgr.height}",
        }
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        print(f"[API] /hcnetsdk/start failed (ch{channel}): {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/hcnetsdk/stop")
def stop_hcnetsdk(channel: int = Query(0)):
    """Stop HCNetSDK connection."""
    _get_mgr(channel).stop()
    return {"status": "success", "message": "HCNetSDK stopped"}

@router.get("/hcnetsdk/status")
def get_hcnetsdk_status():
    """Get HCNetSDK connection status."""
    return {
        "sdk_available": HCNET_SDK_AVAILABLE,
        "is_connected": video_manager.source_type == 'hcnetsdk' and video_manager.is_running,
        "ip": video_manager.hcnet_ip if video_manager.source_type == 'hcnetsdk' else None,
        "channel": video_manager.hcnet_channel if video_manager.source_type == 'hcnetsdk' else None,
    }


@router.post("/video/upload")
async def upload_video(file: UploadFile = File(...)):
    """上传视频文件（同名文件自动覆盖，清理旧的重复副本）"""
    allowed_ext = {'.mp4', '.avi', '.mov', '.mkv'}
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in allowed_ext:
        raise HTTPException(status_code=400, detail="不支持的视频格式")

    original_name = file.filename
    file_path = os.path.join(settings.VIDEO_UPLOAD_DIR, original_name)

    # 清理同名旧副本（之前用 uuid_原名 格式保存的）
    if os.path.isdir(settings.VIDEO_UPLOAD_DIR):
        for existing in os.listdir(settings.VIDEO_UPLOAD_DIR):
            if existing.endswith(f"_{original_name}") or existing == original_name:
                old_path = os.path.join(settings.VIDEO_UPLOAD_DIR, existing)
                if old_path != file_path:
                    try:
                        os.remove(old_path)
                    except Exception:
                        pass

    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    return {
        "status": "success",
        "file_path": file_path,
        "file_name": original_name
    }

@router.post("/video/start")
def start_video(req: VideoStartRequest, channel: int = Query(0)):
    """启动视频播放"""
    try:
        _get_mgr(channel).start_video(req.file_path, req.speed)
        return {"status": "success", "message": f"视频已开始播放 (ch{channel})", "speed": req.speed}
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        print(f"[API] /video/start 失败 (ch{channel}): {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/video/stop")
def stop_video(channel: int = Query(0)):
    """停止视频"""
    _get_mgr(channel).stop()
    return {"status": "success", "message": f"视频已停止 (ch{channel})"}

@router.post("/video/speed")
def set_video_speed(req: VideoSpeedRequest, channel: int = Query(0)):
    """设置视频播放倍速"""
    try:
        _get_mgr(channel).set_video_speed(req.speed)
        return {"status": "success", "message": f"倍速已设置为 {req.speed}x", "speed": req.speed}
    except HTTPException:
        raise
    except Exception as e:
        print(f"[API] /video/speed 失败 (ch{channel}): {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/video/progress")
def set_video_progress(req: VideoProgressRequest, channel: int = Query(0)):
    """设置视频播放进度"""
    try:
        _get_mgr(channel).set_video_progress(req.progress)
        return {"status": "success", "message": f"进度已设置为 {req.progress*100:.1f}%", "progress": req.progress}
    except HTTPException:
        raise
    except Exception as e:
        print(f"[API] /video/progress 失败 (ch{channel}): {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/video/info")
def get_video_info(channel: int = Query(0)):
    """获取视频播放信息"""
    info = _get_mgr(channel).get_video_info()
    if info is None:
        return {"status": "not_video", "message": "当前不是视频输入源"}
    return {"status": "success", **info}

@router.post("/image/upload")
async def upload_image(file: UploadFile = File(...)):
    """上传图片文件"""
    allowed_ext = {'.jpg', '.jpeg', '.png', '.bmp'}
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in allowed_ext:
        raise HTTPException(status_code=400, detail="不支持的图片格式")
    
    unique_name = f"{uuid.uuid4().hex}_{file.filename}"
    file_path = os.path.join(settings.IMAGE_UPLOAD_DIR, unique_name)
    
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    
    return {
        "status": "success",
        "file_path": file_path,
        "file_name": file.filename,
        "url": f"/uploads/images/{unique_name}"
    }

@router.post("/image/set")
def set_image(req: ImageSetRequest, channel: int = Query(0)):
    """设置图片为输入源"""
    try:
        _get_mgr(channel).set_image(req.file_path)
        return {"status": "success", "message": f"图片已设置为输入源 (ch{channel})"}
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        print(f"[API] /image/set 失败 (ch{channel}): {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/detection/start")
def start_detection(req: DetectionStartRequest, channel: int = Query(0)):
    """开始检测"""
    try:
        mgr = _get_mgr(channel)
        mgr.conf_threshold = req.conf
        mgr.iou_threshold = req.iou

        from backend.api.channel_manager import channel_manager
        device = getattr(mgr, 'device', 'auto') or 'auto'
        if req.model_path:
            channel_manager.load_model_for_channel(channel, req.model_path, device)
        elif mgr.model is None:
            channel_manager._propagate_model(channel)

        mgr.start_detection(req.model_path)
        
        if mgr.project_config and mgr.project_config.get('id'):
            session_info = mgr.start_session(mgr.project_config['id'])
            if session_info:
                return {
                    "status": "success", 
                    "message": f"检测已启动 (ch{channel})",
                    "session_id": session_info.get('session_id'),
                    "session_uuid": session_info.get('session_uuid')
                }
        
        return {"status": "success", "message": f"检测已启动 (ch{channel})"}
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        print(f"[API] /detection/start 失败 (ch{channel}): {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/detection/stop")
def stop_detection(channel: int = Query(0)):
    """停止检测（只停止推理）并结束会话"""
    mgr = _get_mgr(channel)
    mgr.end_session()
    mgr.stop_detection()
    return {"status": "success", "message": f"检测已停止 (ch{channel})"}

@router.post("/detection/pause")
def pause_detection(channel: int = Query(0)):
    """暂停：停止画面更新和检测，画面停在当前帧"""
    _get_mgr(channel).pause()
    return {"status": "success", "message": "已暂停"}

@router.post("/detection/resume")
def resume_detection(channel: int = Query(0)):
    """恢复：从暂停状态恢复，重新启动视频流和检测"""
    if _get_mgr(channel).resume():
        return {"status": "success", "message": "已恢复"}
    else:
        raise HTTPException(status_code=400, detail="无法恢复：没有可用的视频源")

@router.post("/detection/standby")
def standby_detection(channel: int = Query(0)):
    """待机：只停止检测推理，画面继续播放"""
    _get_mgr(channel).standby()
    return {"status": "success", "message": "已待机"}

@router.post("/detection/resume-inference")
def resume_inference(channel: int = Query(0)):
    """从待机恢复推理（画面已在播放）"""
    result = _get_mgr(channel).resume_inference()
    if result is False:
        raise HTTPException(status_code=400, detail="无法恢复推理")
    return {"status": "success", "message": "已恢复推理"}

@router.post("/detection/reset-stats")
def reset_detection_stats(channel: int = Query(0)):
    """重置统计数据（计数器、步骤计数等），同时结束当前会话"""
    mgr = _get_mgr(channel)
    mgr.end_session()
    mgr.reset_stats()
    return {"status": "success", "message": "统计数据已重置"}

@router.get("/detection/results")
def get_detection_results(channel: int = Query(0)):
    """获取检测结果"""
    mgr = _get_mgr(channel)
    recent_events = []
    if mgr.events_log:
        current_time = time.time()
        recent_events = [
            e for e in mgr.events_log 
            if current_time - e.get('timestamp', 0) < 30
        ]
    
    avg_cycle_time = 0
    if mgr.cycle_times:
        avg_cycle_time = round(sum(mgr.cycle_times) / len(mgr.cycle_times), 2)

    avg_cycle_time_with_ng = 0
    all_ct = mgr.cycle_times + mgr.ng_cycle_times
    if all_ct:
        avg_cycle_time_with_ng = round(sum(all_ct) / len(all_ct), 2)

    avg_step_durations = {}
    for lbl, hist in mgr.step_durations_history.items():
        if hist:
            avg_step_durations[lbl] = round(sum(hist) / len(hist), 2)

    result = {
        "channel_id": channel,
        "detections": mgr.get_detections(),
        "fps": mgr.fps_actual,
        "fps_inference": mgr.fps_inference,
        "latency": mgr.latency,
        "is_detecting": mgr.is_detecting,
        "source_type": mgr.source_type,
        "is_running": mgr.is_running,
        "step_counts": mgr.step_counts.copy(),
        "step_screenshots": mgr.step_screenshots.copy(),
        "step_detection_times": mgr.step_detection_times.copy(),
        "step_durations": mgr.step_durations.copy(),
        "avg_step_durations": avg_step_durations,
        "step_intervals": mgr.step_intervals.copy(),
        "counters": mgr.counters.copy(),
        "recent_events": recent_events,
        "ng_step_cycle_counts": mgr.ng_step_cycle_counts.copy(),
        "average_cycle_time": avg_cycle_time,
        "average_cycle_time_with_ng": avg_cycle_time_with_ng,
        "current_cycle_steps": list(mgr.current_cycle_steps),
        "backup_covered_labels": [
            mgr.step_backup_map[b]
            for b in mgr.backup_steps_seen_in_cycle
            if b in mgr.step_backup_map
        ]
    }
    
    result['model_task'] = getattr(mgr, 'model_task', 'detect')

    result['project_config'] = {
        'project_id': mgr.project_config.get('id') if mgr.project_config else None,
        'project_name': mgr.project_config.get('name', '') if mgr.project_config else '',
        'logic_mode': mgr.project_config.get('logic_mode', 'detection') if mgr.project_config else 'detection',
        'steps_config': mgr.project_config.get('steps_config', []) if mgr.project_config else [],
    }

    logic_mode = mgr.project_config.get('logic_mode') if mgr.project_config else None
    if logic_mode == 'tracking':
        tracked_objs = {}
        for tid, obj in mgr._tracking_objects.items():
            tracked_objs[str(tid)] = {
                'class_name': obj['class_name'],
                'display_id': obj['display_id'],
                'bbox': obj.get('bbox'),
                'order_idx': obj.get('order_idx', 0),
            }
        tracking_data = {
            'tracked_objects': tracked_objs,
            'class_counters': dict(mgr._tracking_class_counters),
            'item_checklist': dict(mgr._tracking_item_checklist),
            'cycle_active': mgr._tracking_cycle_active,
            'container_mode': mgr._container_mode,
        }
        if mgr._container_mode:
            box_status = {}
            for box_did, bs in mgr._box_objects.items():
                box_status[box_did] = {
                    'bbox': bs.get('bbox'),
                    'is_complete': bs['is_complete'],
                    'item_counts': dict(bs['item_class_counts']),
                }
            tracking_data['boxes'] = box_status
            tracking_data['settled_boxes'] = len(mgr._box_settled_results)
            tracking_data['settled_ok'] = sum(1 for r in mgr._box_settled_results if r['is_complete'])
            tracking_data['settled_ng'] = sum(1 for r in mgr._box_settled_results if not r['is_complete'])
        result['tracking'] = tracking_data
    
    # MES 实时数据 (扫码状态 + 当前工件 + 工单进度)
    if mgr._mes_hook and mgr._mes_hook.enabled:
        try:
            mes_data = {}
            wp_info = mgr._mes_hook.get_current_workpiece(mgr.channel_id)
            if wp_info:
                mes_data['workpiece'] = wp_info
            order_info = mgr._mes_hook.get_active_order(mgr.channel_id)
            if order_info:
                mes_data['order'] = order_info
            if mgr.is_detecting and mgr._mes_hook.is_warn_no_barcode(mgr.channel_id):
                if not mgr._mes_hook.has_pending_workpiece(mgr.channel_id) \
                   and mgr.channel_id not in mgr._mes_hook._inspecting_workpiece:
                    mes_data['warn_no_barcode'] = True
            scan_evt = mgr._mes_hook.get_last_scan_event(mgr.channel_id)
            if scan_evt:
                mes_data['scan_event'] = scan_evt
            rebind = mgr._mes_hook.get_rebind_prompt(mgr.channel_id)
            if rebind:
                mes_data['rebind_prompt'] = rebind
            if mes_data:
                result['mes'] = mes_data
        except Exception:
            pass

    # 当前操作员
    try:
        from backend.api.operators import get_current_operator_id
        op_id = get_current_operator_id(mgr.channel_id)
        if op_id:
            from backend.db.database import SessionLocal
            from backend.models.models import Operator
            _db = SessionLocal()
            try:
                op = _db.query(Operator).filter(Operator.id == op_id).first()
                if op:
                    result['operator'] = {"id": op.id, "name": op.name, "employee_no": op.employee_no}
            finally:
                _db.close()
    except Exception:
        pass

    return result

class ProjectConfigRequest(BaseModel):
    project_id: int
    name: str
    task_type: str = 'detection'
    logic_mode: str = 'detection'
    steps_config: list = []
    pipeline_config: dict = {}
    events_config: list = []
    counters_config: list = []
    data_config: dict = {}

@router.post("/detection/set-project")
def set_project_config(req: ProjectConfigRequest, channel: int = Query(0)):
    """设置项目配置"""
    try:
        _get_mgr(channel).set_project_config({
            'id': req.project_id,
            'name': req.name,
            'task_type': req.task_type,
            'logic_mode': req.logic_mode,
            'steps_config': req.steps_config,
            'pipeline_config': req.pipeline_config,
            'events_config': req.events_config,
            'counters_config': req.counters_config,
            'data_config': req.data_config,
        })
        return {"status": "success", "message": f"项目配置已设置 (ch{channel})"}
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        print(f"[API] /detection/set-project 失败 (ch{channel}): {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/status")
def get_source_status(channel: int = Query(0)):
    """获取当前输入源状态"""
    mgr = _get_mgr(channel)
    from backend.api.channel_manager import channel_manager
    return {
        "channel_id": channel,
        "channel_count": channel_manager.channel_count,
        "is_running": mgr.is_running,
        "is_detecting": mgr.is_detecting,
        "source_type": mgr.source_type,
        "width": mgr.width,
        "height": mgr.height,
        "fps": mgr.fps,
        "fps_actual": mgr.fps_actual,
        "fps_inference": mgr.fps_inference,
        "latency": mgr.latency,
        "model_loaded": mgr.model is not None
    }


@router.get("/health")
def get_health_status(channel: int = Query(0)):
    """
    获取系统健康状态
    用于监控线程运行状态和 GPU 资源使用情况
    """
    import torch
    mgr = _get_mgr(channel)
    
    current_time = time.time()
    
    inference_thread_alive = (
        mgr._inference_thread is not None and 
        mgr._inference_thread.is_alive()
    )
    capture_thread_alive = (
        mgr._thread is not None and 
        mgr._thread.is_alive()
    )
    
    inference_idle_time = current_time - mgr._last_inference_heartbeat
    capture_idle_time = current_time - mgr._last_capture_heartbeat
    
    inference_healthy = not inference_thread_alive or inference_idle_time < mgr._thread_timeout_threshold
    capture_healthy = not capture_thread_alive or capture_idle_time < mgr._thread_timeout_threshold
    
    gpu_info = None
    if torch.cuda.is_available():
        try:
            gpu_info = {
                "device_name": torch.cuda.get_device_name(0),
                "memory_allocated_mb": round(torch.cuda.memory_allocated() / 1024**2, 1),
                "memory_reserved_mb": round(torch.cuda.memory_reserved() / 1024**2, 1),
                "memory_total_mb": round(torch.cuda.get_device_properties(0).total_memory / 1024**2, 1)
            }
        except Exception as e:
            gpu_info = {"error": str(e)}
    
    overall_healthy = inference_healthy and capture_healthy
    
    from backend.api.channel_manager import channel_manager
    return {
        "healthy": overall_healthy,
        "timestamp": current_time,
        "channel_id": channel,
        "channel_count": channel_manager.channel_count,
        "threads": {
            "inference": {
                "alive": inference_thread_alive,
                "healthy": inference_healthy,
                "idle_seconds": round(inference_idle_time, 2) if inference_thread_alive else None
            },
            "capture": {
                "alive": capture_thread_alive,
                "healthy": capture_healthy,
                "idle_seconds": round(capture_idle_time, 2) if capture_thread_alive else None
            }
        },
        "detection": {
            "is_running": mgr.is_running,
            "is_detecting": mgr.is_detecting,
            "model_loaded": mgr.model is not None,
            "fps_actual": mgr.fps_actual,
            "fps_inference": mgr.fps_inference,
            "latency_ms": mgr.latency
        },
        "gpu": gpu_info
    }


@router.post("/detection/rebind")
def resolve_rebind(action: str = "new", channel: int = 0):
    """manual rebind 模式：用户选择继续当前工件(continue)或扫新工件(new)"""
    from backend.api.channel_manager import channel_manager
    mgr = channel_manager.get(channel)
    if mgr._mes_hook:
        mgr._mes_hook.resolve_rebind(mgr.channel_id, action)
        return {"status": "ok", "action": action}
    return {"status": "error", "message": "MES 未启用"}


def get_video_feed(channel: int = 0):
    """获取视频流（供 main.py 使用）"""
    from backend.api.channel_manager import channel_manager
    mgr = channel_manager.get(channel)
    return mgr.generate_mjpeg()

def get_video_manager(channel: int = 0):
    """获取视频管理器实例"""
    from backend.api.channel_manager import channel_manager
    return channel_manager.get(channel)
