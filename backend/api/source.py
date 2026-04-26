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


class VideoSourceManager(TrackingMixin):
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
        
    def _get_db_session(self):
        """获取数据库会话"""
        return SessionLocal()
    
    def _load_export_settings(self):
        """加载导出设置"""
        try:
            db = self._get_db_session()
            setting = db.query(DataExportSetting).first()
            if not setting:
                setting = DataExportSetting()
                db.add(setting)
                db.commit()
                db.refresh(setting)
            self.export_settings = {
                'record_step_duration': setting.record_step_duration,
                'record_step_interval': setting.record_step_interval,
                'record_cycle_duration': setting.record_cycle_duration,
                'record_counters': setting.record_counters,
                'record_step_video': setting.record_step_video,
                'record_cycle_video': setting.record_cycle_video,
                'record_session_video': setting.record_session_video,
                'video_quality': setting.video_quality,
                'video_fps': setting.video_fps
            }
            db.close()
        except Exception as e:
            print(f"加载导出设置失败: {e}")
            self.export_settings = {
                'record_step_duration': True,
                'record_step_interval': True,
                'record_cycle_duration': True,
                'record_counters': True,
                'record_step_video': False,
                'record_cycle_video': False,
                'record_session_video': False,
                'video_quality': 'medium',
                'video_fps': 30
            }
    
    def _ensure_session_active(self):
        """If a project is loaded but no session is recording, auto-create one."""
        if self.recording_enabled and self.current_session_id:
            return
        project_id = self.project_config.get('id') if self.project_config else None
        if not project_id:
            return
        print(f"[自动会话] 检测到项目已加载但无活跃会话，自动创建会话 (project_id={project_id})")
        self.start_session(project_id)

    def start_session(self, project_id: int) -> dict:
        """开始新的检测会话"""
        try:
            db = self._get_db_session()
            session_uuid = str(uuid.uuid4())[:8]
            current_shift = self._get_current_shift()
            from backend.api.operators import get_current_operator_id
            current_op_id = get_current_operator_id(self.channel_id)
            session = DetectionSession(
                session_uuid=session_uuid,
                project_id=project_id,
                start_time=datetime.now(),
                status="running",
                channel_id=self.channel_id,
                shift_label=current_shift,
                operator_id=current_op_id,
            )
            db.add(session)
            db.commit()
            db.refresh(session)
            
            self.current_session_id = session.id
            self.current_session_uuid = session_uuid
            self.current_cycle_number = 0
            self.recording_enabled = True
            self._session_start_date = datetime.now().date()
            self._session_start_shift = current_shift
            
            # 加载导出设置
            self._load_export_settings()
            
            db.close()
            print(f"检测会话已创建: {session_uuid}")
            
            # MES Hook: Session 开始
            if self._mes_hook:
                try:
                    project_id = self.project_config.get('id') if self.project_config else None
                    if project_id:
                        self._mes_hook.on_session_start(
                            channel_id=self.channel_id,
                            session_id=session.id,
                            project_id=project_id,
                        )
                except Exception as e:
                    print(f"[MES] session_start hook 异常: {e}")
            
            # 启动录制线程（独立于 CUDA）
            if self.is_detecting:
                self._start_recording_thread()
            
            # 开始会话视频录制
            self.start_session_recording()
            
            return {"session_id": session.id, "session_uuid": session_uuid}
        except Exception as e:
            print(f"创建会话失败: {e}")
            return None
    
    def end_session(self):
        """结束当前检测会话"""
        if not self.current_session_id:
            print("end_session: 没有活动的会话")
            return
        
        session_id = self.current_session_id
        session_uuid = self.current_session_uuid
        print(f"end_session: 正在结束会话 {session_uuid} (ID: {session_id})")
        
        # Discard any open (unsettled) cycle before closing the session
        if self.current_cycle_id:
            print(f"end_session: discarding unsettled cycle #{self.current_cycle_number} (id={self.current_cycle_id})")
            self._discard_empty_cycle()
        
        try:
            db = self._get_db_session()
            session = db.query(DetectionSession).filter(
                DetectionSession.id == session_id
            ).first()
            
            if session:
                session.end_time = datetime.now()
                session.status = "completed"
                
                # 计算统计 — only count properly settled cycles (end_time is not None)
                cycles = db.query(DetectionCycle).filter(
                    DetectionCycle.session_id == session_id,
                    DetectionCycle.end_time != None
                ).all()
                
                print(f"end_session: 找到 {len(cycles)} 个已结算周期")
                
                if cycles:
                    session.total_cycles = len(cycles)
                    session.good_cycles = len([c for c in cycles if c.is_good])
                    session.ng_cycles = session.total_cycles - session.good_cycles
                    
                    durations = [c.duration for c in cycles if c.duration]
                    if durations:
                        session.avg_cycle_time = sum(durations) / len(durations)
                        session.min_cycle_time = min(durations)
                        session.max_cycle_time = max(durations)
                
                # Clean up any remaining orphan cycles (end_time is NULL)
                orphans = db.query(DetectionCycle).filter(
                    DetectionCycle.session_id == session_id,
                    DetectionCycle.end_time == None
                ).all()
                if orphans:
                    orphan_ids = [o.id for o in orphans]
                    print(f"end_session: removing {len(orphans)} orphan cycle(s): {orphan_ids}")
                    db.query(StepRecord).filter(StepRecord.cycle_id.in_(orphan_ids)).delete(synchronize_session=False)
                    db.query(DetectionCycle).filter(DetectionCycle.id.in_(orphan_ids)).delete(synchronize_session=False)
                
                # 保存计数器快照
                session.counters_snapshot = self.counters.copy() if self.counters else {}
                
                print(f"end_session: 保存数据 - 周期数: {session.total_cycles}, 合格: {session.good_cycles}, 不良: {session.ng_cycles}, 计数器: {session.counters_snapshot}")
                
                db.commit()
                print(f"会话已结束: {session_uuid}, 周期数: {session.total_cycles}")
            else:
                print(f"end_session: 未找到会话 ID={session_id}")
            
            db.close()
            
            # MES Hook: Session 结束
            if self._mes_hook:
                try:
                    self._mes_hook.on_session_end(
                        channel_id=self.channel_id,
                        session_id=session_id,
                    )
                except Exception as e:
                    print(f"[MES] session_end hook 异常: {e}")
        except Exception as e:
            print(f"结束会话失败: {e}")
            import traceback
            traceback.print_exc()
        finally:
            self._persist_counters()
            # 停止视频录制
            self.stop_session_recording()
            self.stop_cycle_recording()
            
            self.current_session_id = None
            self.current_session_uuid = None
            self.recording_enabled = False
            self._session_start_date = None
            self._session_start_shift = None
    
    def _get_counter_file(self) -> str:
        """返回当前通道的计数器持久化文件路径"""
        project_id = self.project_config.get('id') if self.project_config else None
        return os.path.join(DATA_DIR, 'counters', f'project_{project_id}_ch{self.channel_id}.json')

    def _persist_counters(self):
        """将当前计数器值写到通道专属文件，避免多通道竞争同一行"""
        project_id = self.project_config.get('id') if self.project_config else None
        if not project_id or not self.counters:
            return
        try:
            path = self._get_counter_file()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                _json.dump(self.counters, f, ensure_ascii=False)
        except Exception as e:
            print(f"[计数器持久化] ch{self.channel_id} 保存失败: {e}")

    def _get_current_shift(self) -> Optional[str]:
        """Return 'day' or 'night' based on current time and project data_config.
        Returns None when shift splitting is disabled."""
        if not self.project_config:
            return None
        data_cfg = self.project_config.get('data_config') or {}
        if not data_cfg.get('shift_split_enabled'):
            return None
        day_start = data_cfg.get('day_shift_start', '08:00')
        night_start = data_cfg.get('night_shift_start', '20:00')
        now_str = datetime.now().strftime('%H:%M')
        if day_start <= night_start:
            return 'day' if day_start <= now_str < night_start else 'night'
        else:
            return 'night' if night_start <= now_str < day_start else 'day'

    def _auto_split_session(self, reason: str = "date_change"):
        """自动拆分：结束旧会话，开启新会话，保持计数器不清零"""
        project_id = self.project_config.get('id') if self.project_config else None
        if not project_id:
            return
        print(f"[自动拆分] {reason}，自动结束旧会话 {self.current_session_uuid}")
        self.end_session()
        new_info = self.start_session(project_id)
        if new_info:
            print(f"[自动拆分] 新会话已创建: {new_info.get('session_uuid')}")
    
    def _force_timeout_ng(self, reason: str):
        """超时强制NG：触发NG事件并清理当前周期状态"""
        self._trigger_event(2, reason)
        self._cycle_regression = False
        self.current_cycle_steps = []
        self.backup_steps_seen_in_cycle = set()
        self.last_added_step = None
        self.step_last_seen.clear()
        self.step_start_time.clear()
        self.step_consecutive_frames.clear()
        self.step_frame_confirmed.clear()
        self._step_gap_count.clear()
        if hasattr(self, '_step_raw_start'):
            self._step_raw_start.clear()
        self._last_step_added_time = None
        self.last_step_completed_time = None

    def start_cycle(self):
        """开始新的检测周期"""
        if not self.current_session_id or not self.recording_enabled:
            return
        
        if self._mes_hook and self._mes_hook.is_scan_required(self.channel_id):
            if not self._mes_hook.has_pending_workpiece(self.channel_id):
                print(f"[扫码绑定] 工位{self.channel_id} 要求先扫码，当前无待检工件，跳过开周期")
                return
        
        if self._session_start_date and datetime.now().date() != self._session_start_date:
            self._auto_split_session(reason="日期变更")
            if not self.current_session_id:
                return
        
        current_shift = self._get_current_shift()
        if self._session_start_shift and current_shift and current_shift != self._session_start_shift:
            self._auto_split_session(reason=f"班次变更 {self._session_start_shift}->{current_shift}")
            if not self.current_session_id:
                return
        
        try:
            db = self._get_db_session()
            now = datetime.now()
            self.current_cycle_number += 1
            cycle_uuid = str(uuid.uuid4())[:8]
            
            # 更新上一周期的间隔时间
            if self.last_cycle_end_time is not None:
                interval_from_last = (now - self.last_cycle_end_time).total_seconds()
                # 查找上一周期并更新
                last_cycle = db.query(DetectionCycle).filter(
                    DetectionCycle.session_id == self.current_session_id,
                    DetectionCycle.cycle_number == self.current_cycle_number - 1
                ).first()
                if last_cycle:
                    last_cycle.interval_to_next = round(interval_from_last, 2)
                    db.commit()
                    print(f"上一周期间隔: {interval_from_last:.2f}s")
            
            from backend.api.operators import get_current_operator_id
            cycle = DetectionCycle(
                cycle_uuid=cycle_uuid,
                session_id=self.current_session_id,
                cycle_number=self.current_cycle_number,
                start_time=now,
                operator_id=get_current_operator_id(self.channel_id),
            )
            db.add(cycle)
            db.commit()
            db.refresh(cycle)
            
            self.current_cycle_id = cycle.id
            self.current_cycle_uuid = cycle_uuid
            self.cycle_step_records = []
            self.step_order_counter = 0
            
            # 新周期开始：重置传动杆 SessionGate（上一个周期见过的框架记忆不应跨周期）
            if getattr(self, "_rod_gate", None) is not None:
                try:
                    self._rod_gate.reset()
                except Exception:
                    pass

            db.close()
            print(f"新周期开始: #{self.current_cycle_number} ({cycle_uuid})")
            
            # MES Hook: Cycle 开始
            if self._mes_hook:
                try:
                    project_id = self.project_config.get('id') if self.project_config else None
                    if project_id:
                        self._mes_hook.on_cycle_start(
                            channel_id=self.channel_id,
                            cycle_id=cycle.id,
                            session_id=self.current_session_id,
                            project_id=project_id,
                        )
                except Exception as e:
                    print(f"[MES] cycle_start hook 异常: {e}")
            
            # 开始周期视频录制
            self.start_cycle_recording()
        except Exception as e:
            print(f"创建周期失败: {e}")
    
    def end_cycle(self, is_good: bool, event_id: int = None, event_name: str = None, reason: str = None):
        """结束当前检测周期"""
        if not self.current_cycle_id or not self.recording_enabled:
            return
        
        # 停止周期视频录制
        self.stop_cycle_recording()
        
        try:
            db = self._get_db_session()
            cycle = db.query(DetectionCycle).filter(
                DetectionCycle.id == self.current_cycle_id
            ).first()
            
            if cycle:
                cycle.end_time = datetime.now()
                cycle.duration = (cycle.end_time - cycle.start_time).total_seconds()
                cycle.is_good = is_good
                cycle.event_id = event_id
                cycle.event_name = event_name
                cycle.result_reason = reason
                cycle.step_sequence = self.current_cycle_steps.copy()
                
                # 记录周期结束时间，用于计算下一周期的间隔
                self.last_cycle_end_time = cycle.end_time
                
                db.commit()
                print(f"周期结束: #{self.current_cycle_number}, 结果: {'OK' if is_good else 'NG'}, 耗时: {cycle.duration:.2f}s")
                
                # MES Hook: Cycle 结束
                if self._mes_hook:
                    try:
                        project_id = self.project_config.get('id') if self.project_config else None
                        self._mes_hook.on_cycle_end(
                            channel_id=self.channel_id,
                            cycle_id=cycle.id,
                            is_good=is_good,
                            event_name=event_name,
                            result_reason=reason,
                            duration=cycle.duration,
                            step_sequence=cycle.step_sequence,
                            project_id=project_id,
                        )
                    except Exception as e:
                        print(f"[MES] cycle_end hook 异常: {e}")
            
            db.close()
        except Exception as e:
            print(f"结束周期失败: {e}")
        finally:
            self.current_cycle_id = None
            self.current_cycle_uuid = None
    
    def _discard_empty_cycle(self):
        """Discard the current cycle when step_sequence is empty after filtering."""
        if not self.current_cycle_id:
            return
        self.stop_cycle_recording()
        try:
            db = self._get_db_session()
            db.query(StepRecord).filter(
                StepRecord.cycle_id == self.current_cycle_id).delete()
            db.query(DetectionCycle).filter(
                DetectionCycle.id == self.current_cycle_id).delete()
            db.commit()
            db.close()
            print(f"Discarded empty cycle #{self.current_cycle_number}")
        except Exception as e:
            print(f"Failed to discard empty cycle: {e}")
        finally:
            self.current_cycle_id = None
            self.current_cycle_uuid = None
            if self.current_cycle_number > 0:
                self.current_cycle_number -= 1
    
    def _reconcile_step_records(self):
        """Align StepRecords with self.current_cycle_steps using a
        keep-and-fix strategy: keep existing records that match, delete
        excess ones, and create missing ones.  This avoids losing timing
        data from records already written by the disappearance handler.
        """
        if not self.current_cycle_id or not self.recording_enabled:
            return
        if not self.current_cycle_steps:
            return

        try:
            db = self._get_db_session()
            existing = db.query(StepRecord).filter(
                StepRecord.cycle_id == self.current_cycle_id
            ).order_by(StepRecord.step_order).all()

            avail = {}
            for rec in existing:
                avail.setdefault(rec.step_label, []).append(rec)

            keep_ids = set()
            missing_indices = []
            dur_fixed = 0

            for i, label in enumerate(self.current_cycle_steps):
                candidates = avail.get(label, [])
                # Prefer the candidate with the longest duration
                candidates.sort(key=lambda r: (r.duration or 0), reverse=True)
                matched = None
                for rec in candidates:
                    if rec.id not in keep_ids:
                        matched = rec
                        keep_ids.add(rec.id)
                        break
                if matched:
                    matched.step_order = i + 1
                    # Fix kept records that have very short / zero duration
                    if (matched.duration or 0) < 0.1:
                        better_dur = self.step_durations.get(label)
                        if better_dur and better_dur >= 0.1:
                            matched.duration = better_dur
                            dur_fixed += 1
                        else:
                            st = self.step_start_time.get(label)
                            et = self.step_last_seen.get(label)
                            if st and et and (et - st) >= 0.1:
                                matched.duration = round(et - st, 2)
                                dur_fixed += 1
                else:
                    missing_indices.append(i)

            for rec in existing:
                if rec.id not in keep_ids:
                    db.delete(rec)

            now = time.time()

            for idx in missing_indices:
                label = self.current_cycle_steps[idx]
                start_t = self.step_start_time.get(label)
                end_t = self.step_last_seen.get(label)
                if start_t and end_t:
                    dur = max(0, round(end_t - start_t, 2))
                else:
                    start_t = start_t or now
                    end_t = end_t or now
                    dur = max(0, round(end_t - start_t, 2))

                # Use step_durations fallback when computed duration is too small
                if dur < 0.1:
                    better = self.step_durations.get(label)
                    if better and better >= 0.1:
                        dur = better

                step_id = None
                if self.project_config:
                    for step in self.project_config.get('steps_config', []):
                        if step.get('label') == label:
                            step_id = step.get('id')
                            break

                new_rec = StepRecord(
                    record_uuid=str(uuid.uuid4())[:8],
                    cycle_id=self.current_cycle_id,
                    step_id=step_id,
                    step_label=label,
                    step_name=self.step_display_names.get(label, label),
                    step_order=idx + 1,
                    start_time=datetime.fromtimestamp(start_t),
                    end_time=datetime.fromtimestamp(end_t),
                    duration=dur,
                    is_valid=True,
                )
                db.add(new_rec)

            db.commit()
            db.close()
            print(f"[reconcile] cycle {self.current_cycle_id}: kept {len(keep_ids)}, "
                  f"deleted {len(existing) - len(keep_ids)}, created {len(missing_indices)}"
                  f"{f', dur_fixed {dur_fixed}' if dur_fixed else ''}")
        except Exception as e:
            print(f"Failed to reconcile StepRecords: {e}")
            import traceback
            traceback.print_exc()

    def record_step(self, step_label: str, step_name: str, start_time: float, end_time: float, 
                    duration: float, interval: float = None, confidence: float = None, is_valid: bool = True,
                    video_info: dict = None, step_order: int = None):
        """记录步骤信息
        
        注意：interval 现在表示"到下一步骤的间隔"，在下一步骤开始时计算并更新
        video_info: 视频信息字典，包含 video_uuid 和 filepath
        step_order: 可选，指定步骤在本周期内的顺序号（用于结算时补写缺失步骤）
        """
        if not self.current_cycle_id or not self.recording_enabled:
            return
        
        if not self.export_settings or not self.export_settings.get('record_step_duration', True):
            return
        
        db = None
        try:
            db = self._get_db_session()
            if step_order is not None:
                self.step_order_counter = max(self.step_order_counter, step_order)
            else:
                self.step_order_counter += 1
            order_to_use = step_order if step_order is not None else self.step_order_counter
            
            # 更新上一个步骤的"到下一步间隔"
            if self.cycle_step_records:
                last_record = self.cycle_step_records[-1]
                last_end_time = last_record.get('end_time')
                last_uuid = last_record.get('record_uuid')
                if last_end_time and last_uuid:
                    interval_to_next = start_time - last_end_time
                    last_step = db.query(StepRecord).filter(
                        StepRecord.record_uuid == last_uuid
                    ).first()
                    if last_step:
                        last_step.interval_to_next = round(interval_to_next, 2)
                        db.commit()
            record_uuid = str(uuid.uuid4())[:8]
            
            # 获取步骤ID（从项目配置）
            step_id = None
            if self.project_config:
                for step in self.project_config.get('steps_config', []):
                    if step.get('label') == step_label:
                        step_id = step.get('id')
                        break
            
            # 视频信息
            video_id = None
            video_path = None
            if video_info:
                video_id = video_info.get('video_uuid')
                video_path = video_info.get('filepath')
            
            record = StepRecord(
                record_uuid=record_uuid,
                cycle_id=self.current_cycle_id,
                step_id=step_id,
                step_label=step_label,
                step_name=step_name or step_label,
                step_order=order_to_use,
                start_time=datetime.fromtimestamp(start_time),
                end_time=datetime.fromtimestamp(end_time),
                duration=duration,
                interval_from_prev=interval,
                confidence=confidence,
                is_valid=is_valid,
                video_id=video_id,
                video_path=video_path
            )
            db.add(record)
            db.commit()
            
            # 保存到本地记录用于间隔计算
            self.cycle_step_records.append({
                'step_label': step_label,
                'end_time': end_time,
                'record_uuid': record_uuid
            })
        except Exception as e:
            print(f"记录步骤失败: {e}")
            import traceback
            traceback.print_exc()
        finally:
            if db:
                db.close()
        
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
    
    def load_model(self, model_path: str, original_pt_path: str = None) -> bool:
        """加载 YOLO 模型。转换模型加载失败时自动回退到原始 .pt。"""
        try:
            from ultralytics import YOLO
            import torch
            
            if original_pt_path:
                self._original_pt_path = original_pt_path
            elif model_path.endswith('.pt') or model_path.endswith('.pth'):
                self._original_pt_path = model_path
            
            # 先释放旧模型
            if self.model is not None:
                print(f"[模型加载] 释放旧模型: {self.model_path}")
                self._release_model()
            
            try:
                self.model = YOLO(model_path)
            except Exception as e:
                if self._original_pt_path and model_path != self._original_pt_path:
                    print(f"[模型加载] 转换模型加载失败 ({e})，回退到原始模型: {self._original_pt_path}")
                    self.model = YOLO(self._original_pt_path)
                    model_path = self._original_pt_path
                else:
                    raise
            self.model_path = model_path
            self.model_task = getattr(self.model, 'task', 'detect')  # 'detect' or 'segment'
            
            is_native_pytorch = model_path.endswith('.pt') or model_path.endswith('.pth')
            self._is_native_pytorch = is_native_pytorch
            
            # 设置推理设备
            if self.device == 'auto':
                if torch.cuda.is_available():
                    device = 'cuda:0'
                else:
                    device = 'cpu'
            else:
                device = self.device
            
            # .to(device) 仅对原生 PyTorch 模型有效；导出格式在 predict 时通过 device 参数指定
            if is_native_pytorch:
                self.model.to(device)
            
            # 记录当前设备信息
            if device.startswith('cuda'):
                gpu_idx = int(device.split(':')[1]) if ':' in device else 0
                gpu_name = torch.cuda.get_device_name(gpu_idx)
                self.current_device_info = {'type': 'GPU', 'name': gpu_name, 'device': device}
                print(f"模型加载成功: {model_path} -> GPU: {gpu_name}")
            else:
                self.current_device_info = {'type': 'CPU', 'name': 'CPU', 'device': 'cpu'}
                print(f"模型加载成功: {model_path} -> CPU")
            
            if hasattr(self.model, 'names'):
                print(f"类别: {list(self.model.names.values())}")
            print(f"模型任务类型: {self.model_task}")
            
            # 自动检测模型的 imgsz
            # 优先级（v2.7.3）：
            #   1) .engine 文件头部嵌入的 Ultralytics JSON metadata（最权威，无需触发 AutoBackend 实例化）
            #   2) .pt 模型 ckpt 元数据（model.overrides / model.model.args）
            #   3) AutoBackend 已实例化时的 bindings / input_shape / context.get_tensor_shape
            #   4) 默认 640（fallback）
            detected_imgsz = 640
            engine_imgsz = None

            # 方法0（v2.7.3）：直接解析 .engine 文件头部 metadata，最可靠
            if not is_native_pytorch and model_path.endswith('.engine'):
                try:
                    engine_imgsz = _read_engine_metadata_imgsz(model_path)
                    if engine_imgsz:
                        print(f"[模型加载] 从 engine 文件头 metadata 检测到 imgsz={engine_imgsz}")
                except Exception as e:
                    print(f"[模型加载] engine metadata 解析失败: {e}")

            try:
                if engine_imgsz is None and not is_native_pytorch and hasattr(self.model, 'model'):
                    inner = self.model.model
                    # 方法1: 从 bindings 读取
                    if hasattr(inner, 'bindings') and inner.bindings:
                        for b in inner.bindings.values() if isinstance(inner.bindings, dict) else inner.bindings:
                            shape = getattr(b, 'shape', None)
                            if shape and len(shape) == 4:
                                engine_imgsz = max(shape[2], shape[3])
                                print(f"[模型加载] 从引擎 bindings 检测到 imgsz={engine_imgsz}")
                                break
                    # 方法2: 从 input_shape 读取
                    if engine_imgsz is None and hasattr(inner, 'input_shape'):
                        s = inner.input_shape
                        if isinstance(s, (list, tuple)) and len(s) >= 3:
                            engine_imgsz = max(s[-2], s[-1])
                            print(f"[模型加载] 从 input_shape 检测到 imgsz={engine_imgsz}")
                    # 方法3: 从 AutoBackend 已读取的 imgsz 属性（v2.7.3）
                    if engine_imgsz is None and hasattr(inner, 'imgsz'):
                        s = inner.imgsz
                        if isinstance(s, (list, tuple)) and len(s) >= 1:
                            engine_imgsz = max(s)
                            print(f"[模型加载] 从 AutoBackend.imgsz 检测到 imgsz={engine_imgsz}")
                        elif isinstance(s, int):
                            engine_imgsz = s
                            print(f"[模型加载] 从 AutoBackend.imgsz 检测到 imgsz={engine_imgsz}")
                    # 方法4: 从 context/engine 读取 TensorRT binding shape
                    if engine_imgsz is None:
                        for attr_name in ('context', 'engine', 'runtime'):
                            ctx = getattr(inner, attr_name, None)
                            if ctx is None:
                                continue
                            if hasattr(ctx, 'get_tensor_shape'):
                                try:
                                    for i in range(10):
                                        name = ctx.get_tensor_name(i) if hasattr(ctx, 'get_tensor_name') else None
                                        if name is None:
                                            break
                                        s = ctx.get_tensor_shape(name)
                                        if len(s) == 4 and s[1] == 3:
                                            engine_imgsz = max(s[2], s[3])
                                            print(f"[模型加载] 从 TRT {attr_name}.get_tensor_shape 检测到 imgsz={engine_imgsz}")
                                            break
                                except Exception:
                                    pass
                            if engine_imgsz:
                                break
                if engine_imgsz and engine_imgsz > 0:
                    detected_imgsz = engine_imgsz
                elif hasattr(self.model, 'overrides') and 'imgsz' in self.model.overrides:
                    raw = self.model.overrides['imgsz']
                    detected_imgsz = raw if isinstance(raw, int) else max(raw)
                elif hasattr(self.model, 'model') and hasattr(self.model.model, 'args'):
                    args = self.model.model.args
                    if isinstance(args, dict) and 'imgsz' in args:
                        raw = args['imgsz']
                        detected_imgsz = raw if isinstance(raw, int) else max(raw)
            except Exception as e:
                print(f"[模型加载] 检测 imgsz 失败，使用默认 640: {e}")
            self._model_imgsz = detected_imgsz
            print(f"[模型加载] 推理分辨率 imgsz={self._model_imgsz}")
            
            if device.startswith('cuda'):
                try:
                    import numpy as np
                    _half = self.use_half if is_native_pytorch else False
                    sz = self._model_imgsz
                    print(f"[模型预热] CUDA warm-up (half={_half}, imgsz={sz})...")
                    self.model.predict(
                        np.zeros((sz, sz, 3), dtype=np.uint8),
                        conf=0.5, imgsz=sz, verbose=False, device=device,
                        half=_half
                    )
                    print("[模型预热] warm-up done")
                except AssertionError as ae:
                    import re
                    m = re.search(r'model size \(1, 3, (\d+), (\d+)\)', str(ae))
                    if m:
                        correct_sz = max(int(m.group(1)), int(m.group(2)))
                        print(f"[模型预热] TensorRT 引擎实际需要 imgsz={correct_sz}，自动修正")
                        self._model_imgsz = correct_sz
                        self.model.predict(
                            np.zeros((correct_sz, correct_sz, 3), dtype=np.uint8),
                            conf=0.5, imgsz=correct_sz, verbose=False, device=device,
                            half=_half
                        )
                        print("[模型预热] warm-up done (修正后)")
                    else:
                        print(f"[模型预热] warm-up failed: {ae}")
                except Exception as e:
                    print(f"[模型预热] warm-up failed: {e}")

            # v2.7.3: warm-up 之后 Ultralytics 才创建 AutoBackend；此时 AutoBackend.imgsz 是从 engine
            # metadata 读出来的最权威值，用它做最终修正，避免任何上游路径漏检导致 _model_imgsz 停在 640
            try:
                predictor = getattr(self.model, 'predictor', None)
                inner = getattr(predictor, 'model', None) if predictor is not None else None
                authoritative = None
                if inner is not None:
                    if hasattr(inner, 'imgsz'):
                        s = inner.imgsz
                        if isinstance(s, (list, tuple)) and len(s) >= 1:
                            authoritative = max(s)
                        elif isinstance(s, int):
                            authoritative = s
                    if authoritative is None and hasattr(inner, 'bindings') and inner.bindings:
                        for b in (inner.bindings.values() if isinstance(inner.bindings, dict) else inner.bindings):
                            shape = getattr(b, 'shape', None)
                            if shape and len(shape) == 4 and shape[1] == 3:
                                authoritative = max(shape[2], shape[3])
                                break
                if authoritative and authoritative > 0 and authoritative != self._model_imgsz:
                    print(f"[模型加载] AutoBackend 权威 imgsz={authoritative}（修正 {self._model_imgsz} → {authoritative}）")
                    self._model_imgsz = authoritative
            except Exception as e:
                print(f"[模型加载] 读取 AutoBackend 权威 imgsz 失败: {e}")

            return True
        except Exception as e:
            print(f"模型加载失败: {e}")
            import traceback
            traceback.print_exc()
            self.model = None
            self.current_device_info = None
            return False
    
    def _capture_loop(self):
        """
        摄像头/视频捕获循环（主线程）
        
        双线程架构：
        - 主线程：读帧 → 取结果 → 滤波 → 显示（不阻塞）
        - 推理线程：推理 → 帧计数 → 步骤判断 → 事件触发（独立运行）
        """
        debug_log("========== 捕获线程开始 ==========", "CAPTURE")
        print("[捕获线程] 开始运行")
        frame_start_time = time.time()
        consecutive_errors = 0  # 连续错误计数
        max_consecutive_errors = 10  # 最大连续错误次数
        last_heartbeat_log = time.time()
        loop_count = 0  # 循环计数
        last_debug_time = time.time()  # 上次调试日志时间
        
        # 如果正在检测且模型已加载，启动推理线程
        if self.is_detecting and self.model is not None:
            debug_log("启动推理线程...", "CAPTURE")
            self._start_inference_thread()
            debug_log("推理线程已启动", "CAPTURE")
        
        while self.is_running and (self.capture is not None or self.source_type in ('hikvision', 'hcnetsdk')):
            try:
                loop_count += 1
                loop_start = time.time()
                
                if loop_start - last_debug_time > 5.0:
                    last_debug_time = loop_start
                
                # 更新捕获线程心跳
                self._last_capture_heartbeat = time.time()
                
                speed = getattr(self, 'video_speed', 1.0)
                
                # 根据输入源类型读取帧
                frame = None
                ret = False
                
                if self.source_type == 'hcnetsdk':
                    # 海康设备网络SDK
                    frame = self._get_hcnetsdk_frame()
                    ret = frame is not None
                elif self.source_type == 'hikvision':
                    # 海康工业相机
                    t_hik_start = time.time()
                    frame = self._get_hikvision_frame()
                    t_hik_end = time.time()
                    ret = frame is not None
                    
                    # 如果帧获取耗时超过200ms，记录警告
                    hik_time = (t_hik_end - t_hik_start) * 1000
                    if hik_time > 200:
                        debug_log(f"!!! 海康帧获取慢: {hik_time:.1f}ms, ret={ret}", "CAPTURE")
                else:
                    # 普通摄像头或视频文件
                    # 对于视频输入源，如果倍速大于1，通过跳帧实现
                    if self.source_type == 'video' and speed > 1:
                        # 跳过一些帧来实现倍速
                        frames_to_skip = int(speed) - 1
                        for _ in range(frames_to_skip):
                            ret = self.capture.grab()  # 只抓取不解码，更快
                            if not ret:
                                break
                    
                    try:
                        t_read_start = time.time()
                        ret, frame = self.capture.read()
                        t_read_end = time.time()
                        read_time = (t_read_end - t_read_start) * 1000
                        if read_time > 200:
                            debug_log(f"!!! 帧读取慢: {read_time:.1f}ms", "CAPTURE")
                    except Exception as e:
                        debug_log(f"帧读取异常: {e}", "CAPTURE")
                        ret = False
                
                if ret and frame is not None:
                    # Single copy from OpenCV's internal buffer (which may be
                    # reused on the next capture.read()).  This copy is then
                    # shared read-only across inference, streaming, and recording
                    # threads — no further copies are needed in the capture loop.
                    raw_frame = frame.copy()

                    # ========== v2.7.14: "只翻显示, 不翻推理" ==========
                    # raw_frame 保留原始摄像头视角, 仅给模型推理使用 → 检测精度不受翻转影响;
                    # display_frame 是变换后的帧, 给 MJPEG / 录像 / 快照 / stats_screenshot;
                    # 推理输出的 bbox 会在 _inference_loop 里通过
                    # _map_detections_original_to_display 映射到显示坐标系, 下游 ROI/容器/前端
                    # 画框等全部基于显示坐标系, 完全对齐。
                    if self._has_display_transform():
                        display_frame = self._apply_frame_transform(raw_frame.copy())
                    else:
                        display_frame = raw_frame
                    # original_frame 作为历史命名保留, 一律指向 display_frame
                    # (下游大量代码用 original_frame 做 MJPEG/录像/screenshot)
                    original_frame = display_frame

                    # 更新视频当前帧位置
                    if self.source_type == 'video' and self.capture is not None:
                        self.video_current_frame = int(self.capture.get(cv2.CAP_PROP_POS_FRAMES))
                    
                    # ========== 预缩小帧：推理基于 raw_frame, 录制/stats 基于 display_frame ==========
                    small_frame = None          # 显示坐标系的缩小帧 (给录制 / stats 截图)
                    raw_small_frame = None      # 原图坐标系的缩小帧 (给模型推理)
                    if self.is_detecting and self.model is not None:
                        target_sz = getattr(self, '_model_imgsz', 640)
                        # 显示帧缩小
                        oh, ow = original_frame.shape[:2]
                        if max(oh, ow) > target_sz * 1.2:
                            scale = target_sz / max(oh, ow)
                            nw = int(ow * scale) // 2 * 2
                            nh = int(oh * scale) // 2 * 2
                            small_frame = cv2.resize(original_frame, (nw, nh), interpolation=cv2.INTER_LINEAR)
                        else:
                            small_frame = original_frame
                        # 原图帧缩小 (喂模型)
                        if self._has_display_transform():
                            rh, rw = raw_frame.shape[:2]
                            if max(rh, rw) > target_sz * 1.2:
                                scale_r = target_sz / max(rh, rw)
                                rnw = int(rw * scale_r) // 2 * 2
                                rnh = int(rh * scale_r) // 2 * 2
                                raw_small_frame = cv2.resize(raw_frame, (rnw, rnh), interpolation=cv2.INTER_LINEAR)
                            else:
                                raw_small_frame = raw_frame
                        else:
                            # 无变换时两者指向同一个缓冲, 零额外开销
                            raw_small_frame = small_frame
                    
                    # ========== 双线程架构：异步推理 ==========
                    if self.is_detecting and self.model is not None:
                        t_lock1_start = time.time()
                        with self._inference_frame_lock:
                            self._latest_frame_for_inference = raw_small_frame
                            self._latest_display_small_for_stats = small_frame
                            self._latest_frame_original_size = raw_frame.shape[:2]
                        t_lock1_end = time.time()
                        if (t_lock1_end - t_lock1_start) > 0.1:
                            debug_log(f"!!! inference_frame_lock 耗时: {(t_lock1_end-t_lock1_start)*1000:.1f}ms", "CAPTURE")
                        
                        # 获取已确认的检测结果并应用滤波（非阻塞）
                        t_lock2_start = time.time()
                        with self._confirmed_detections_lock:
                            confirmed_detections = self._confirmed_detections.copy()
                        t_lock2_end = time.time()
                        if (t_lock2_end - t_lock2_start) > 0.1:
                            debug_log(f"!!! confirmed_detections_lock 耗时: {(t_lock2_end-t_lock2_start)*1000:.1f}ms", "CAPTURE")
                        
                        # 应用卡尔曼滤波平滑
                        t_kalman_start = time.time()
                        smoothed_detections = self._apply_kalman_filter(confirmed_detections)
                        t_kalman_end = time.time()
                        if (t_kalman_end - t_kalman_start) > 0.1:
                            debug_log(f"!!! 卡尔曼滤波耗时: {(t_kalman_end-t_kalman_start)*1000:.1f}ms", "CAPTURE")
                        
                        # 更新当前检测结果（供前端获取）
                        t_lock3_start = time.time()
                        with self.detection_lock:
                            self.current_detections = smoothed_detections
                        t_lock3_end = time.time()
                        if (t_lock3_end - t_lock3_start) > 0.1:
                            debug_log(f"!!! detection_lock 耗时: {(t_lock3_end-t_lock3_start)*1000:.1f}ms", "CAPTURE")
                    
                    # 写入视频录制队列 — 用缩小帧（已接近录制分辨率）
                    if self.is_detecting and self.recording_enabled:
                        self._enqueue_frame_for_recording(small_frame if small_frame is not None else original_frame)
                    
                    # MediaPipe 骨架叠加（仅在推理时显示，不影响录制）
                    display_frame = original_frame
                    if self.mediapipe_enabled and self.is_detecting:
                        display_frame = original_frame.copy()
                        self._apply_mediapipe_overlay(display_frame)
                    
                    t_lock4_start = time.time()
                    with self.frame_lock:
                        self.current_frame = display_frame
                        self._frame_seq += 1
                    t_lock4_end = time.time()
                    if (t_lock4_end - t_lock4_start) > 0.1:
                        debug_log(f"!!! frame_lock 耗时: {(t_lock4_end-t_lock4_start)*1000:.1f}ms", "CAPTURE")
                    
                    # FPS 计算
                    self._fps_counter += 1
                    if time.time() - self._fps_time >= 1.0:
                        self.fps_actual = self._fps_counter
                        self._fps_counter = 0
                        self._fps_time = time.time()
                    
                else:
                    if self.source_type == 'rtsp':
                        consecutive_errors += 1
                        if consecutive_errors >= 5:
                            print(f"[RTSP] 连续 {consecutive_errors} 帧失败，尝试重连...")
                            try:
                                if self.capture is not None:
                                    self.capture.release()
                                time.sleep(2.0)
                                self.capture = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)
                                self.capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                                if self.capture.isOpened():
                                    print("[RTSP] 重连成功")
                                    consecutive_errors = 0
                                else:
                                    print("[RTSP] 重连失败，等待后再试...")
                                    time.sleep(3.0)
                            except Exception as e:
                                print(f"[RTSP] 重连异常: {e}")
                                time.sleep(3.0)
                        else:
                            time.sleep(0.05)
                    elif self.source_type == 'video' and self.video_path:
                        print("[Video] 视频播放完毕，已停止")
                        self.video_ended = True
                        self.is_running = False
                        if self.is_detecting:
                            self.stop_detection()
                        break
                    else:
                        time.sleep(0.01)
                
                # 计算帧处理耗时，动态调整 sleep 时间
                frame_elapsed = time.time() - frame_start_time
                target_interval = 1.0 / max(self.fps * speed, 1)
                sleep_time = max(0, target_interval - frame_elapsed)
                if sleep_time > 0:
                    time.sleep(sleep_time)
                frame_start_time = time.time()
                
                # 重置连续错误计数（成功处理一帧）
                consecutive_errors = 0
                
                # 定期打印心跳日志（每30秒）
                if time.time() - last_heartbeat_log > 30:
                    print(f"[捕获线程心跳] 运行中, FPS={self.fps_actual}, 源={self.source_type}")
                    last_heartbeat_log = time.time()
                    
            except Exception as e:
                consecutive_errors += 1
                print(f"[捕获线程] 错误 ({consecutive_errors}/{max_consecutive_errors}): {e}")
                import traceback
                traceback.print_exc()
                
                # 如果连续错误太多，尝试恢复
                if consecutive_errors >= max_consecutive_errors:
                    print(f"[捕获线程] 连续 {max_consecutive_errors} 次错误，尝试恢复...")
                    try:
                        if self.source_type == 'hcnetsdk':
                            print("[HCNetSDK] reconnecting...")
                            try:
                                self._release_hcnet_session()
                                time.sleep(2.0)
                                self._reconnect_hcnetsdk()
                                print("[HCNetSDK] reconnect OK")
                                consecutive_errors = 0
                            except Exception as re_err:
                                print(f"[HCNetSDK] reconnect failed: {re_err}")
                        elif self.source_type == 'hikvision':
                            # 海康相机：尝试重新连接
                            self._release_hik_camera()
                            time.sleep(0.5)
                            # 重新初始化海康相机
                            try:
                                self.start_hikvision_camera(
                                    device_index=self.hik_device_index,
                                    width=self.width,
                                    height=self.height,
                                    fps=self.fps
                                )
                                print("[捕获线程] 海康相机重新连接成功")
                                consecutive_errors = 0
                            except:
                                print("[捕获线程] 海康相机重新连接失败")
                        else:
                            if self.capture is not None:
                                self.capture.release()
                            if self.source_type == 'rtsp':
                                print("[RTSP] 尝试重连...")
                                time.sleep(2.0)
                                self.capture = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)
                                self.capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                                if self.capture.isOpened():
                                    print("[RTSP] 重连成功")
                                    consecutive_errors = 0
                                else:
                                    print("[RTSP] 重连失败")
                            elif self.source_type == 'camera':
                                backend = getattr(self, '_camera_backend', None)
                                if backend is not None:
                                    self.capture = cv2.VideoCapture(self.camera_index, backend)
                                elif platform.system() == "Windows":
                                    self.capture = cv2.VideoCapture(self.camera_index, cv2.CAP_DSHOW)
                                else:
                                    self.capture = cv2.VideoCapture(self.camera_index)
                                if self.capture.isOpened():
                                    self.capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc('M','J','P','G'))
                                    self.capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
                                    self.capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
                                    self.capture.set(cv2.CAP_PROP_FPS, self.fps)
                                    print(f"[捕获线程] 摄像头重新打开成功 (backend={backend})")
                                    consecutive_errors = 0
                                else:
                                    print("[捕获线程] 摄像头重新打开失败")
                            elif self.source_type == 'video' and self.video_path:
                                self.capture = cv2.VideoCapture(self.video_path)
                                if self.capture.isOpened():
                                    print("[捕获线程] 视频重新打开成功")
                                    consecutive_errors = 0
                                else:
                                    print("[捕获线程] 视频重新打开失败，停止运行")
                                    self.is_running = False
                    except Exception as recover_error:
                        print(f"[捕获线程] 恢复失败: {recover_error}")
                        self.is_running = False
                
                time.sleep(0.1)  # 错误后短暂等待
        
        print("[捕获线程] 结束运行")
        # 停止推理线程
        self._stop_inference_thread()
        # 停止录制线程
        self._stop_recording_thread()
    
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
    
    def _settle_custom_cycle(self):
        """结算自定义模式的当前周期（在第一步重新出现且不匹配任何条件前缀时调用）"""
        if not self.project_config:
            return
        
        pipeline_config = self.project_config.get('pipeline_config', {})
        steps_config = self.project_config.get('steps_config', [])
        custom_based_on = pipeline_config.get('custom_based_on')
        
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
        
        # 获取启用的步骤标签
        enabled_step_labels = [s.get('label') for s in steps_config if s.get('enabled', True)]
        
        self._supplement_step_durations()
        
        self.current_cycle_steps = self._filter_cycle_by_duration(self.current_cycle_steps)
        
        print(f"自定义模式结算: 当前序列={self.current_cycle_steps}")
        
        if not self.current_cycle_steps:
            self._discard_empty_cycle()
            self.current_cycle_steps = []
            self.backup_steps_seen_in_cycle = set()
            self.last_added_step = None
            self.step_last_seen.clear()
            self.step_start_time.clear()
            self.step_consecutive_frames.clear()
            self.step_frame_confirmed.clear()
            self._step_gap_count.clear()
            
            self.last_step_completed_time = None
            return
        
        # 先检查自定义条件（只包含启用步骤的条件）
        custom_conditions = pipeline_config.get('custom_conditions', [])
        if custom_conditions:
            sorted_conditions = sorted(custom_conditions, key=lambda c: c.get('priority', 999))
            
            for cond in sorted_conditions:
                cond_sequence = cond.get('sequence', [])
                cond_event_id = cond.get('event_id')
                
                if not cond_sequence or not cond_event_id:
                    continue
                
                # 只包含启用的步骤
                cond_labels = [id_to_label.get(sid) for sid in cond_sequence if sid in id_to_label and sid in enabled_step_ids]
                
                if self.current_cycle_steps == cond_labels:
                    print(f"  → 条件匹配！触发事件 {cond_event_id}")
                    self._reconcile_step_records()
                    self._trigger_event(cond_event_id, f'自定义条件匹配: {cond_labels}')
                    self.current_cycle_steps = []
                    self.backup_steps_seen_in_cycle = set()
                    self.last_added_step = None
                    self.step_last_seen.clear()
                    self.step_start_time.clear()
                    self.step_consecutive_frames.clear()
                    self.step_frame_confirmed.clear()
                    self._step_gap_count.clear()
                    if hasattr(self, '_step_raw_start'):
                        self._step_raw_start.clear()
                    self.last_step_completed_time = None
                    return
        
        # 没有条件匹配，回退到基础模式判定
        if custom_based_on == 'sequential':
            # 使用自定义模式独立的顺序配置
            sequence_order = pipeline_config.get('custom_sequence_order', [])
            
            if not sequence_order:
                self.current_cycle_steps = []
                self.backup_steps_seen_in_cycle = set()
                self.last_added_step = None
                self.step_last_seen.clear()
                self.step_start_time.clear()
                self.step_consecutive_frames.clear()
                self.step_frame_confirmed.clear()
                self._step_gap_count.clear()
                if hasattr(self, '_step_raw_start'):
                    self._step_raw_start.clear()
                self.last_step_completed_time = None
                return
            
            expected_labels = []
            for item in sequence_order:
                step_id = item.get('step_id')
                if step_id in id_to_label and step_id in enabled_step_ids:
                    expected_labels.append(id_to_label[step_id])
            
            if not expected_labels:
                self.current_cycle_steps = []
                self.backup_steps_seen_in_cycle = set()
                self.last_added_step = None
                self.step_last_seen.clear()
                self.step_start_time.clear()
                self.step_consecutive_frames.clear()
                self.step_frame_confirmed.clear()
                self._step_gap_count.clear()
                if hasattr(self, '_step_raw_start'):
                    self._step_raw_start.clear()
                self.last_step_completed_time = None
                return
            
            self.current_cycle_steps = self._inject_backup_steps(
                self.current_cycle_steps, expected_labels)
            self.current_cycle_steps = self._filter_cycle_by_duration(self.current_cycle_steps)
            
            if not self.current_cycle_steps:
                self._discard_empty_cycle()
                self.current_cycle_steps = []
                self.backup_steps_seen_in_cycle = set()
                self.last_added_step = None
                self.step_last_seen.clear()
                self.step_start_time.clear()
                self.step_consecutive_frames.clear()
                self.step_frame_confirmed.clear()
                self._step_gap_count.clear()
                if hasattr(self, '_step_raw_start'):
                    self._step_raw_start.clear()
                self.last_step_completed_time = None
                return
            
            self._reconcile_step_records()
            
            print(f"  期望序列({len(expected_labels)}步): {expected_labels}")
            print(f"  实际序列({len(self.current_cycle_steps)}步): {self.current_cycle_steps}")
            
            from collections import Counter
            expected_set = set(expected_labels)
            step_counter = Counter(self.current_cycle_steps)
            unexpected = [s for s in self.current_cycle_steps if s not in expected_set]
            duplicated = [s for s, cnt in step_counter.items() if cnt > 1]

            if self._cycle_regression or unexpected or duplicated:
                reasons = []
                if self._cycle_regression:
                    reasons.append(f'步骤回退: {[s for s, c in step_counter.items() if c > 1]}')
                if unexpected:
                    reasons.append(f'多余步骤: {list(dict.fromkeys(unexpected))}')
                if duplicated and not self._cycle_regression:
                    reasons.append(f'重复步骤: {duplicated}')
                reason_str = ', '.join(reasons)
                print(f"  → {reason_str} → NG")
                self._trigger_event(2, reason_str)
            elif self.current_cycle_steps == expected_labels:
                print(f"  → 序列完全匹配 → OK")
                self._trigger_event(1, '顺序正确完成')
            elif len(self.current_cycle_steps) < len(expected_labels):
                missing = [l for l in expected_labels if l not in self.current_cycle_steps]
                print(f"  → 周期不完整，缺少: {missing} → NG")
                self._trigger_event(2, f'周期不完整，缺少: {missing}')
            else:
                unique_steps = []
                for s in self.current_cycle_steps:
                    if s not in unique_steps:
                        unique_steps.append(s)
                mismatch_idx = -1
                for i, (actual, expected) in enumerate(zip(unique_steps, expected_labels)):
                    if actual != expected:
                        mismatch_idx = i
                        break
                if mismatch_idx >= 0:
                    print(f"  → 第{mismatch_idx+1}步顺序错误: 期望[{expected_labels[mismatch_idx]}], 实际[{unique_steps[mismatch_idx]}] → NG")
                    self._trigger_event(2, f'第{mismatch_idx+1}步顺序错误')
                else:
                    print(f"  → 顺序错误 → NG")
                    self._trigger_event(2, '顺序错误')
        
        elif custom_based_on == 'detection':
            detection_step_ids = pipeline_config.get('custom_detection_steps', [])
            if detection_step_ids:
                detection_labels = [id_to_label.get(sid) for sid in detection_step_ids
                                    if sid in id_to_label and sid in enabled_step_ids]
            else:
                detection_labels = enabled_step_labels
            
            self.current_cycle_steps = self._inject_backup_steps(
                self.current_cycle_steps, detection_labels)
            
            self._reconcile_step_records()
            
            present = set(self.current_cycle_steps)
            missing = [l for l in detection_labels if l not in present]
            
            from collections import Counter
            step_counts = Counter(self.current_cycle_steps)
            duplicated = [s for s, cnt in step_counts.items() if cnt > 1]
            
            ng_reasons = []
            if missing:
                ng_reasons.append(f'缺少步骤: {missing}')
            if duplicated:
                ng_reasons.append(f'重复步骤: {duplicated}')
            
            print(f"  自定义(基于检测)结算: 需要={detection_labels}, 本周期={self.current_cycle_steps}, 缺少={missing}, 重复={duplicated}")
            
            if not ng_reasons:
                print(f"  → 全部检测到，无重复 → OK")
                self._trigger_event(1, '检测完成')
            else:
                reason = '；'.join(ng_reasons)
                print(f"  → {reason} → NG")
                self._trigger_event(2, reason)
        
        # 重置周期
        self._cycle_regression = False
        self.current_cycle_steps = []
        self.backup_steps_seen_in_cycle = set()
        self.last_added_step = None
        self.step_last_seen.clear()
        self.step_start_time.clear()
        self.step_consecutive_frames.clear()
        self.step_frame_confirmed.clear()
        self._step_gap_count.clear()
        
        self.last_step_completed_time = None
    
    def _settle_detection_cycle(self):
        """结算检测模式的当前周期
        
        判定逻辑（无序）：
        - 第一步和最后一步固定，中间步骤不要求顺序
        - 所有需检测步骤都出现过 → OK (事件1)
        - 缺少步骤 → NG (事件2)，报告缺少的步骤列表
        - 重复步骤（accept_once=OFF的步骤） → NG (事件2)
        """
        if not self.project_config:
            return
        
        detection_labels = self._get_detection_step_labels()
        if not detection_labels:
            return
        
        self._supplement_step_durations()
        
        self.current_cycle_steps = self._inject_backup_steps(
            self.current_cycle_steps, detection_labels)
        self.current_cycle_steps = self._filter_cycle_by_duration(self.current_cycle_steps)
        
        if not self.current_cycle_steps:
            self._discard_empty_cycle()
            self.current_cycle_steps = []
            self.backup_steps_seen_in_cycle = set()
            self.last_added_step = None
            self._last_step_added_time = None
            self.step_last_seen.clear()
            self.step_start_time.clear()
            self.step_consecutive_frames.clear()
            self.step_frame_confirmed.clear()
            self._step_gap_count.clear()
            if hasattr(self, '_step_raw_start'):
                self._step_raw_start.clear()
            self.last_step_completed_time = None
            return
        
        self._reconcile_step_records()
        
        present = set(self.current_cycle_steps)
        missing = [l for l in detection_labels if l not in present]
        
        from collections import Counter
        step_counts = Counter(self.current_cycle_steps)
        duplicated = [s for s, cnt in step_counts.items() if cnt > 1]
        
        ng_reasons = []
        if missing:
            ng_reasons.append(f'缺少步骤: {missing}')
        if duplicated:
            ng_reasons.append(f'重复步骤: {duplicated}')
        
        print(f"检测模式结算: 需要={detection_labels}, 本周期={self.current_cycle_steps}, 缺少={missing}, 重复={duplicated}")
        
        if not ng_reasons:
            print(f"  → 全部检测到，无重复 → OK")
            self._trigger_event(1, '检测完成')
        else:
            reason = '；'.join(ng_reasons)
            print(f"  → {reason} → NG")
            self._trigger_event(2, reason)
        
        self.current_cycle_steps = []
        self.backup_steps_seen_in_cycle = set()
        self.last_added_step = None
        self._last_step_added_time = None
        self.step_last_seen.clear()
        self.step_start_time.clear()
        self.step_consecutive_frames.clear()
        self.step_frame_confirmed.clear()
        self._step_gap_count.clear()
        if hasattr(self, '_step_raw_start'):
            self._step_raw_start.clear()
        self.last_step_completed_time = None
    
    def _settle_sequential_cycle(self):
        """结算纯顺序模式的当前周期（在新周期开始前调用）
        
        与自定义模式（基于顺序）的判定逻辑一致：
        1. 检查序列长度是否超过预期（有重复步骤）
        2. 检查是否包含所有预期步骤
        3. 检查顺序是否正确
        """
        if not self.project_config:
            return
        
        pipeline_config = self.project_config.get('pipeline_config', {})
        steps_config = self.project_config.get('steps_config', [])
        
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
        
        # 顺序模式的结算逻辑
        sequence_order = pipeline_config.get('sequence_order', [])
        
        if not sequence_order or not steps_config:
            self.current_cycle_steps = []
            self.backup_steps_seen_in_cycle = set()
            self.last_added_step = None
            self.step_last_seen.clear()
            self.step_start_time.clear()
            self.step_consecutive_frames.clear()
            self.step_frame_confirmed.clear()
            self._step_gap_count.clear()
            
            self.last_step_completed_time = None
            return
        
        expected_labels = []
        for item in sequence_order:
            step_id = item.get('step_id')
            if step_id in id_to_label and step_id in enabled_step_ids:
                expected_labels.append(id_to_label[step_id])
        
        if not expected_labels:
            self.current_cycle_steps = []
            self.backup_steps_seen_in_cycle = set()
            self.last_added_step = None
            self.step_last_seen.clear()
            self.step_start_time.clear()
            self.step_consecutive_frames.clear()
            self.step_frame_confirmed.clear()
            self._step_gap_count.clear()
            
            self.last_step_completed_time = None
            return
        
        self._supplement_step_durations()
        
        self.current_cycle_steps = self._inject_backup_steps(
            self.current_cycle_steps, expected_labels)
        self.current_cycle_steps = self._filter_cycle_by_duration(self.current_cycle_steps)
        
        if not self.current_cycle_steps:
            self._discard_empty_cycle()
            self.current_cycle_steps = []
            self.backup_steps_seen_in_cycle = set()
            self.last_added_step = None
            self.step_last_seen.clear()
            self.step_start_time.clear()
            self.step_consecutive_frames.clear()
            self.step_frame_confirmed.clear()
            self._step_gap_count.clear()
            
            self.last_step_completed_time = None
            return
        
        self._reconcile_step_records()
        
        print(f"顺序模式结算: 期望={expected_labels}, 实际={self.current_cycle_steps}, 回退={self._cycle_regression}")
        
        from collections import Counter
        expected_set = set(expected_labels)
        step_counter = Counter(self.current_cycle_steps)

        unexpected = [s for s in self.current_cycle_steps if s not in expected_set]
        duplicated = [s for s, cnt in step_counter.items() if cnt > 1]
        missing = [l for l in expected_labels if l not in self.current_cycle_steps]

        if self._cycle_regression or unexpected or duplicated:
            reasons = []
            if self._cycle_regression:
                reasons.append(f'步骤回退: {[s for s, c in step_counter.items() if c > 1]}')
            if unexpected:
                reasons.append(f'多余步骤: {list(dict.fromkeys(unexpected))}')
            if duplicated and not self._cycle_regression:
                reasons.append(f'重复步骤: {duplicated}')
            reason_str = ', '.join(reasons)
            print(f"  → {reason_str} → NG")
            self._trigger_event(2, reason_str)
            self._cycle_regression = False
            self.current_cycle_steps = []
            self.backup_steps_seen_in_cycle = set()
            self.last_added_step = None
            self.step_last_seen.clear()
            self.step_start_time.clear()
            self.step_consecutive_frames.clear()
            self.step_frame_confirmed.clear()
            self._step_gap_count.clear()
            
            self.last_step_completed_time = None
            return

        if missing:
            print(f"  → 周期不完整，缺少: {missing} → NG")
            self._trigger_event(2, f'周期不完整，缺少: {missing}')
            self._cycle_regression = False
            self.current_cycle_steps = []
            self.backup_steps_seen_in_cycle = set()
            self.last_added_step = None
            self.step_last_seen.clear()
            self.step_start_time.clear()
            self.step_consecutive_frames.clear()
            self.step_frame_confirmed.clear()
            self._step_gap_count.clear()
            
            self.last_step_completed_time = None
            return
        
        # 检查顺序是否正确（去除重复项后）
        unique_steps = []
        for s in self.current_cycle_steps:
            if s not in unique_steps:
                unique_steps.append(s)
        cycle_order_correct = True
        order_error_labels = []
        last_idx = -1
        prev_label = None
        for label in expected_labels:
            if label in unique_steps:
                idx = unique_steps.index(label)
                if idx < last_idx:
                    cycle_order_correct = False
                    order_error_labels = [prev_label, label]
                    break
                last_idx = idx
            prev_label = label
        
        if cycle_order_correct:
            print(f"  → 顺序正确 → OK")
            self._trigger_event(1, '顺序正确完成')
        else:
            print(f"  → 顺序错误 → NG: {order_error_labels}")
            self._trigger_event(2, f'顺序错误，期望[{order_error_labels[0]}]在前 实际[{order_error_labels[1]}]在前')
        
        # 重置周期
        self._cycle_regression = False
        self.current_cycle_steps = []
        self.backup_steps_seen_in_cycle = set()
        self.last_added_step = None
        self.step_last_seen.clear()
        self.step_start_time.clear()
        self.step_consecutive_frames.clear()
        self.step_frame_confirmed.clear()
        self._step_gap_count.clear()
        
        self.last_step_completed_time = None
    
    def _process_simultaneous_groups(self, frame_detected_labels: set, detected_labels: set, current_time: float):
        """
        同时出现组缓冲排序层。
        
        当检测到某个组的成员时，开始收集。在时间窗口内收集到的成员按用户
        配置的优先顺序排序后输出。不区分跨周期/同周期，统一处理。
        
        返回:
            pending_labels: set  -- 正在缓冲中、本帧不应处理的标签
            ready_ordered: list  -- 缓冲完成、按配置顺序输出的标签列表
        """
        pending_labels = set()
        ready_ordered = []
        
        if not self._simultaneous_groups:
            return pending_labels, ready_ordered
        
        for idx, group in enumerate(self._simultaneous_groups):
            if not group.get('enabled', True):
                continue
            
            group_labels = set(group.get('labels', []))
            if len(group_labels) < 2:
                continue
            
            time_window = group.get('time_window', 2.0)
            priority_order = group.get('priority_order') or list(group.get('labels', [])) or list(group_labels)
            
            present_members = group_labels & frame_detected_labels
            
            buf = self._sim_group_buffers.get(idx)
            if buf is None:
                buf = {
                    'collecting': False,
                    'start_time': None,
                    'collected_labels': set(),
                    'group_labels': group_labels,
                    'time_window': time_window,
                    'priority_order': priority_order,
                }
            
            if not buf['collecting']:
                if present_members:
                    # 只在当前周期已有步骤时才启动缓冲
                    if len(self.current_cycle_steps) == 0:
                        pass
                    else:
                        # 只在有"潜在新出现"的成员时才缓冲，避免连续检测被误缓冲
                        has_potential_new = False
                        for lbl in present_members:
                            if lbl not in self.step_last_seen:
                                has_potential_new = True
                                break
                            tc = self.step_time_config.get(lbl, {})
                            mi = tc.get('max_interval') or 1.0
                            if current_time - self.step_last_seen[lbl] > mi:
                                has_potential_new = True
                                break
                        
                        if not has_potential_new:
                            pass  # 都是连续检测，不缓冲
                        elif present_members >= group_labels:
                            ordered = [l for l in priority_order if l in group_labels]
                            ready_ordered.extend(ordered)
                            print(f"[同时出现组 {idx}] 全员同帧到齐，按序输出: {ordered}")
                        else:
                            buf['collecting'] = True
                            buf['start_time'] = current_time
                            buf['collected_labels'] = set(present_members)
                            pending_labels.update(present_members)
            else:
                buf['collected_labels'].update(present_members)
                elapsed = current_time - buf['start_time']
                
                if buf['collected_labels'] >= group_labels:
                    ordered = [l for l in priority_order if l in group_labels]
                    ready_ordered.extend(ordered)
                    buf['collecting'] = False
                    buf['start_time'] = None
                    buf['collected_labels'] = set()
                    print(f"[同时出现组 {idx}] 全员在 {elapsed:.2f}s 内到齐，按序输出: {ordered}")
                elif elapsed > time_window:
                    collected = buf['collected_labels']
                    ordered = [l for l in priority_order if l in collected]
                    ready_ordered.extend(ordered)
                    buf['collecting'] = False
                    buf['start_time'] = None
                    buf['collected_labels'] = set()
                    print(f"[同时出现组 {idx}] 超时 {elapsed:.2f}s，输出已收集: {ordered}")
                else:
                    pending_labels.update(buf['collected_labels'])
            
            self._sim_group_buffers[idx] = buf
        
        return pending_labels, ready_ordered
    
    def _process_single_step(self, label, current_time, enabled_labels, is_seq_like,
                             should_update_screenshot, original_frame, det_info,
                             just_confirmed_labels=None):
        """处理单个标签的步骤逻辑：新出现判定、周期结算触发、周期记录、截图更新。
        
        从 _update_step_stats 的 for 循环体中提取，供缓冲层输出和普通标签共用。
        """
        import base64
        
        if label not in enabled_labels:
            return
        
        if self.step_strict_order.get(label):
            expected = self._get_expected_sequence_labels()
            if label in expected:
                idx = expected.index(label)
                predecessors = expected[:idx]
                cycle_set = set(self.current_cycle_steps)
                for pred in predecessors:
                    if pred in cycle_set:
                        continue
                    backup = self.step_primary_to_backup.get(pred)
                    if backup and backup in self.backup_steps_seen_in_cycle:
                        continue
                    return
        
        logic_mode = self.project_config.get('logic_mode') if self.project_config else 'detection'
        pipeline_config = self.project_config.get('pipeline_config', {}) if self.project_config else {}
        custom_based_on = pipeline_config.get('custom_based_on')
        
        # ── 截图：在任何 return 之前执行，确保 SOP 卡片始终有图 ──
        force_screenshot = just_confirmed_labels and label in just_confirmed_labels
        if (should_update_screenshot or force_screenshot) and det_info:
            x, y, w, h = det_info['x'], det_info['y'], det_info['w'], det_info['h']
            img_h, img_w = original_frame.shape[:2]
            pad = 20
            cx1 = max(0, int(x * img_w) - pad)
            cy1 = max(0, int(y * img_h) - pad)
            cx2 = min(img_w, int((x + w) * img_w) + pad)
            cy2 = min(img_h, int((y + h) * img_h) + pad)
            if cx2 > cx1 and cy2 > cy1:
                crop = original_frame[cy1:cy2, cx1:cx2]
                _, buffer = cv2.imencode('.jpg', crop, [cv2.IMWRITE_JPEG_QUALITY, 70])
                self.step_screenshots[label] = base64.b64encode(buffer).decode('utf-8')
        
        # Save previous step_last_seen BEFORE any logic, needed by both accept_once
        # and is_new_appearance calculations below.
        old_last_seen = self.step_last_seen.get(label)

        # ── accept_once 拦截 ──
        # 在 first_step 结算模式下，第一步即使设了 accept_once，真正消失后重现
        # 也必须放行以触发结算；只有连续检测（未消失）才拦截。
        if self.step_accept_once.get(label) and label in self.current_cycle_steps:
            allow_through = False
            if self.settlement_mode == 'first_step' and is_seq_like and len(self.current_cycle_steps) > 1:
                first_step_label = self._get_first_sequence_step_label()
                if first_step_label and label == first_step_label and old_last_seen is not None:
                    gap = current_time - old_last_seen
                    dedup_interval = (self.step_time_config.get(label, {}).get('max_interval')) or 1.0
                    if gap > dedup_interval:
                        allow_through = True
            if not allow_through:
                self.step_last_seen[label] = current_time
                if label not in self.step_start_time:
                    raw_start = getattr(self, '_step_raw_start', {}).get(label, current_time)
                    self.step_start_time[label] = raw_start
                return
        
        # ── 第一步重现结算（仅 first_step 结算模式） ──
        _just_settled_by_first_step = False
        if self.settlement_mode == 'first_step' and is_seq_like \
                and label in self.current_cycle_steps and len(self.current_cycle_steps) > 1:
            first_step_label = self._get_first_sequence_step_label()
            if first_step_label and label == first_step_label:
                first_start = self.step_start_time.get(label) or getattr(self, '_step_raw_start', {}).get(label)
                first_min_dur = (self.step_time_config.get(label, {}).get('min_duration')) or 0
                first_duration = (current_time - first_start) if first_start else 0
                if first_duration >= first_min_dur:
                    print(f"[第一步结算] [{label}] 再次检测到 (持续{first_duration:.2f}s >= {first_min_dur}s)，结算当前周期 (步骤数={len(self.current_cycle_steps)})")
                    if logic_mode == 'custom' and custom_based_on == 'sequential':
                        self._settle_custom_cycle()
                    elif logic_mode == 'sequential':
                        self._settle_sequential_cycle()
                    old_last_seen = None
                    _just_settled_by_first_step = True
                    if hasattr(self, '_step_raw_start'):
                        if first_min_dur and first_min_dur > 0:
                            self._step_raw_start[label] = current_time - first_min_dur
                        else:
                            self._step_raw_start.pop(label, None)
        
        # ── 检测模式：第一步重现结算 ──
        if logic_mode == 'detection' and len(self.current_cycle_steps) > 1:
            first_det_label = self._get_first_detection_step_label()
            if first_det_label and label == first_det_label and label in self.current_cycle_steps:
                first_start = self.step_start_time.get(label) or getattr(self, '_step_raw_start', {}).get(label)
                first_min_dur = (self.step_time_config.get(label, {}).get('min_duration')) or 0
                first_duration = (current_time - first_start) if first_start else 0
                if first_duration >= first_min_dur:
                    print(f"[检测模式结算] [{label}] 第一步再次出现 (持续{first_duration:.2f}s >= {first_min_dur}s)，结算当前周期 (步骤={self.current_cycle_steps})")
                    self._settle_detection_cycle()
                    old_last_seen = None
                    _just_settled_by_first_step = True
                    if hasattr(self, '_step_raw_start'):
                        if first_min_dur and first_min_dur > 0:
                            self._step_raw_start[label] = current_time - first_min_dur
                        else:
                            self._step_raw_start.pop(label, None)
        
        # Always update step_last_seen so duration calculations reflect actual last detection time
        self.step_last_seen[label] = current_time
        
        time_config = self.step_time_config.get(label, {})
        max_interval = time_config.get('max_interval') or 1.0
        
        if old_last_seen is not None:
            time_since_last = current_time - old_last_seen
            if is_seq_like:
                is_new_appearance = time_since_last > max_interval
            elif self.last_added_step is not None and self.last_added_step != label:
                is_new_appearance = True
            else:
                is_new_appearance = time_since_last > max_interval
        else:
            is_new_appearance = True
        
        if is_new_appearance:
            # 检测模式：只有第一步能开启新周期
            if len(self.current_cycle_steps) == 0 and logic_mode == 'detection':
                first_det_label = self._get_first_detection_step_label()
                if first_det_label and label != first_det_label:
                    return
            
            raw_start = getattr(self, '_step_raw_start', {}).get(label, current_time)
            self.step_start_time[label] = raw_start
            self.step_detection_times[label] = raw_start
            
            if len(self.current_cycle_steps) == 0:
                self.cycle_start_time = current_time
                self.start_cycle()
        
        if is_new_appearance and label in enabled_labels:
            should_join_cycle = True
            if self.step_detection_type.get(label) == 'static':
                static_config = self.step_static_config.get(label, {})
                should_join_cycle = static_config.get('join_cycle', True)
            
            if should_join_cycle:
                logic_mode = self.project_config.get('logic_mode') if self.project_config else 'detection'
                if logic_mode == 'custom' or logic_mode == 'sequential':
                    if len(self.current_cycle_steps) == 0:
                        self._first_step_had_gap = False
                        self._first_step_reconfirmed = False
                        self._first_step_disappeared_at = None
                        self._cycle_regression = False
                    if self.last_added_step == label:
                        pass
                    elif label in self.current_cycle_steps:
                        self._cycle_regression = True
                        self.current_cycle_steps.append(label)
                        self.last_added_step = label
                        self._last_step_added_time = current_time
                        print(f"[步骤回退] {label} 已在周期中出现过，标记回退 (当前序列: {self.current_cycle_steps})")
                    else:
                        self.current_cycle_steps.append(label)
                        self.last_added_step = label
                        self._last_step_added_time = current_time
                else:
                    if not self.step_accept_once.get(label) or label not in self.current_cycle_steps:
                        self.current_cycle_steps.append(label)
                        self.last_added_step = label
                        self._last_step_added_time = current_time
    
    def _update_step_stats(self, detections: list, original_frame: np.ndarray):
        """更新步骤统计和截图
        
        注意：置信度阈值过滤已在 _detect_only 方法中完成，
        此处收到的 detections 都是通过阈值的有效检测
        """
        import base64
        current_time = time.time()
        detected_labels = set()  # 用于统计的标签（通过阈值的）
        frame_detected_labels = set()  # 本帧通过置信度阈值的标签（用于帧数过滤）
        
        # Screenshot throttle: at most once per second to save CPU
        if not hasattr(self, '_last_screenshot_time'):
            self._last_screenshot_time = 0
        should_update_screenshot = (current_time - self._last_screenshot_time) >= 1.0
        just_confirmed_labels = set()
        
        for det in detections:
            label = det.get('label', '')
            confidence = det.get('confidence', 0)
            if not label:
                continue
            
            if self.step_conf_thresholds:
                threshold = self.step_conf_thresholds.get(label)
                if threshold is not None and confidence < threshold:
                    continue
            
            frame_detected_labels.add(label)
        
        if not hasattr(self, '_step_raw_start'):
            self._step_raw_start = {}
        
        for label in frame_detected_labels:
            self._step_gap_count[label] = 0
            prev_count = self.step_consecutive_frames.get(label, 0)
            if prev_count == 0:
                self._step_raw_start[label] = current_time
                self.start_step_recording(label)
            self.step_consecutive_frames[label] = prev_count + 1
            
            min_frames = self.step_min_frames.get(label, 1)
            if self.step_consecutive_frames[label] >= min_frames:
                detected_labels.add(label)
                if not self.step_frame_confirmed.get(label):
                    self.step_frame_confirmed[label] = True
                    just_confirmed_labels.add(label)
                    first_seq_lbl = self._get_first_sequence_step_label() if self.current_cycle_steps else None
                    if label == first_seq_lbl and label in self.current_cycle_steps:
                        self._first_step_reconfirmed = True
        
        # Backup step processing: mark seen, then remove from detected_labels
        if self.step_backup_map:
            backup_in_detected = detected_labels & set(self.step_backup_map.keys())
            for b_label in backup_in_detected:
                self.backup_steps_seen_in_cycle.add(b_label)
            detected_labels -= backup_in_detected
        
        # 对于本帧没有检测到的标签，根据 gap_tolerance 决定是否重置连续帧计数
        all_configured_labels = set(self.step_conf_thresholds.keys()) if self.step_conf_thresholds else set()
        first_seq_label = self._get_first_sequence_step_label() if self.current_cycle_steps else None
        for label in all_configured_labels:
            if label not in frame_detected_labels:
                gap_tolerance = self.step_gap_tolerance.get(label, 0)
                current_gap = self._step_gap_count.get(label, 0) + 1
                self._step_gap_count[label] = current_gap

                if current_gap > gap_tolerance:
                    was_tracking = self.step_consecutive_frames.get(label, 0) > 0
                    was_confirmed = self.step_frame_confirmed.get(label, False)
                    self.step_consecutive_frames[label] = 0
                    self.step_frame_confirmed[label] = False
                    self._step_gap_count[label] = 0
                    if was_tracking and not was_confirmed:
                        self.stop_step_recording(label)
                    if was_confirmed and label == first_seq_label and label in self.current_cycle_steps:
                        self._first_step_disappeared_at = time.time()
                # 重置静态步骤的触发状态（标签消失后可以再次触发）
                if label in self.step_static_triggered:
                    self.step_static_triggered[label] = False
        
        # 检查静态步骤是否达到触发条件
        for label in frame_detected_labels:
            if self.step_detection_type.get(label) == 'static':
                static_config = self.step_static_config.get(label, {})
                trigger_frames = static_config.get('trigger_frames', 30)
                trigger_event = static_config.get('trigger_event')
                
                # 检查是否达到静态触发帧数且未触发过
                if (self.step_consecutive_frames.get(label, 0) >= trigger_frames 
                    and not self.step_static_triggered.get(label, False)):
                    
                    self.step_static_triggered[label] = True
                    print(f"静态步骤 [{label}] 达到触发条件（{trigger_frames}帧）")
                    
                    # 触发配置的事件（如果有）
                    if trigger_event:
                        self._trigger_event(trigger_event, f'静态步骤触发: {label}')
                    
                    # 检查自定义条件中是否有匹配这个静态步骤的条件
                    self._check_static_step_conditions(label)
        
        # 获取启用的步骤标签
        enabled_labels = set()
        if self.project_config:
            steps_config = self.project_config.get('steps_config', [])
            for step in steps_config:
                if step.get('enabled', True):
                    step_label = step.get('label', '')
                    if step_label:
                        enabled_labels.add(step_label)
        
        # 构建 label -> detection_info 映射，供截图使用
        det_by_label = {}
        for det in detections:
            label = det.get('label', '')
            if label:
                det_by_label[label] = det
        
        # 存储当前帧检测到的标签（供周期结算时清理 step_last_seen）
        self._current_detected_labels = detected_labels
        
        # 调用缓冲排序层
        pending_labels, ready_ordered = self._process_simultaneous_groups(
            frame_detected_labels, detected_labels, current_time)
        ready_ordered_set = set(ready_ordered)
        
        # 缓冲中的标签：不更新 step_last_seen（保留原始值以便释放时正确判断 is_new），
        # 但仍更新截图。消失检测通过 _currently_pending_labels 跳过。
        self._currently_pending_labels = pending_labels
        for label in pending_labels:
            if (should_update_screenshot or label in just_confirmed_labels) and label in det_by_label:
                det_info = det_by_label[label]
                x, y, w, h = det_info['x'], det_info['y'], det_info['w'], det_info['h']
                img_h, img_w = original_frame.shape[:2]
                pad = 20
                cx1 = max(0, int(x * img_w) - pad)
                cy1 = max(0, int(y * img_h) - pad)
                cx2 = min(img_w, int((x + w) * img_w) + pad)
                cy2 = min(img_h, int((y + h) * img_h) + pad)
                if cx2 > cx1 and cy2 > cy1:
                    crop = original_frame[cy1:cy2, cx1:cx2]
                    _, buffer = cv2.imencode('.jpg', crop, [cv2.IMWRITE_JPEG_QUALITY, 70])
                    self.step_screenshots[label] = base64.b64encode(buffer).decode('utf-8')
        
        _logic_mode_for_sort = self.project_config.get('logic_mode') if self.project_config else 'detection'
        _pipeline_for_sort = self.project_config.get('pipeline_config', {}) if self.project_config else {}
        _is_seq_like = (_logic_mode_for_sort == 'sequential' or
                        (_logic_mode_for_sort == 'custom' and _pipeline_for_sort.get('custom_based_on') == 'sequential'))

        for label in list(detected_labels):
            if label not in self.step_start_time:
                continue
            time_cfg = self.step_time_config.get(label, {})
            _max_dur = time_cfg.get('max_duration')
            if _max_dur and (current_time - self.step_start_time[label]) > _max_dur:
                if time_cfg.get('timeout_ng') and self.current_cycle_steps:
                    elapsed = current_time - self.step_start_time[label]
                    display = self.step_display_names.get(label, label)
                    print(f"[步骤超时NG] 步骤 [{display}] 持续 {elapsed:.1f}s > {_max_dur}s，触发NG")
                    self._force_timeout_ng(f'步骤 [{display}] 超时 ({elapsed:.1f}s > {_max_dur}s)')
                    return
                print(f"[超时重置] 步骤 [{label}] 持续 {current_time - self.step_start_time[label]:.1f}s > max_duration {_max_dur}s，模拟再次出现")
                if label in self.step_last_seen:
                    del self.step_last_seen[label]
                del self.step_start_time[label]
                self.step_consecutive_frames[label] = 0
                self.step_frame_confirmed[label] = False
                self._step_gap_count[label] = 0
                if label in getattr(self, '_step_raw_start', {}):
                    del self._step_raw_start[label]

        for label in ready_ordered:
            # min_duration gate: step must be continuously present for min_duration
            # before it can enter any cycle logic. Detection box still shows.
            _min_dur_cfg = self.step_time_config.get(label, {}).get('min_duration')
            if _min_dur_cfg and _min_dur_cfg > 0:
                _raw_st = self._step_raw_start.get(label)
                if _raw_st and (current_time - _raw_st) < _min_dur_cfg:
                    continue
            self._process_single_step(label, current_time, enabled_labels, _is_seq_like,
                                      should_update_screenshot, original_frame,
                                      det_by_label.get(label), just_confirmed_labels)
        
        for label in detected_labels:
            if label in pending_labels:
                continue
            if label in ready_ordered_set:
                continue
            _min_dur_cfg2 = self.step_time_config.get(label, {}).get('min_duration')
            if _min_dur_cfg2 and _min_dur_cfg2 > 0:
                _raw_st2 = self._step_raw_start.get(label)
                if _raw_st2 and (current_time - _raw_st2) < _min_dur_cfg2:
                    continue
            self._process_single_step(label, current_time, enabled_labels, _is_seq_like,
                                      should_update_screenshot, original_frame,
                                      det_by_label.get(label), just_confirmed_labels)
        
        if should_update_screenshot:
            self._last_screenshot_time = current_time
        
        # 检查消失的步骤（完成计数）
        # 使用延迟判定机制：先记录所有消失的步骤，再统一进行事件判定
        # 这样可以确保所有步骤都被正确记录到当前周期，避免因判定触发 end_cycle 导致后续步骤记录失败
        pending_event_checks = []  # 收集需要检查事件的步骤
        
        _pending = getattr(self, '_currently_pending_labels', set())
        for label, last_time in list(self.step_last_seen.items()):
            if label in _pending:
                continue
            if label not in detected_labels:
                # 获取步骤时间配置
                time_config = self.step_time_config.get(label, {})
                disappear_delay = time_config.get('disappear_delay') or 0
                
                if current_time - last_time > disappear_delay:
                    # 计算持续时间
                    start_time = self.step_start_time.get(label, last_time)
                    duration = last_time - start_time
                    
                    # 检查持续时间是否在有效范围内
                    min_duration = time_config.get('min_duration')
                    max_duration = time_config.get('max_duration')
                    
                    is_valid = True
                    if min_duration is not None and duration < min_duration:
                        is_valid = False
                        print(f"步骤 {label} 持续时间 {duration:.2f}s 低于最短时间 {min_duration}s，忽略")
                    if max_duration is not None and duration > max_duration:
                        is_valid = False
                        print(f"步骤 {label} 持续时间 {duration:.2f}s 超过最大时间 {max_duration}s，忽略")
                    
                    # 如果持续时间无效，从周期中移除该步骤（影响周期判定）
                    # But never remove accept_once steps that are already in the cycle:
                    # they were validated during their first appearance.
                    if not is_valid:
                        if self.step_accept_once.get(label) and label in self.current_cycle_steps:
                            print(f"  → 步骤 {label} (accept_once) 本次检测无效但保留在周期中")
                        else:
                            self.current_cycle_steps = [s for s in self.current_cycle_steps if s != label]
                            print(f"  → 已从当前周期中移除步骤 {label}")
                    
                    # 清理状态
                    del self.step_last_seen[label]
                    if label in self.step_start_time:
                        del self.step_start_time[label]
                    
                    # 只有有效的检测才计数
                    if is_valid:
                        if label not in self.step_counts:
                            self.step_counts[label] = 0
                        self.step_counts[label] += 1
                        
                        rounded_dur = round(duration, 2)
                        self.step_durations[label] = rounded_dur
                        self.step_durations_history.setdefault(label, []).append(rounded_dur)
                        
                        # 计算与上一步骤的间隔时间
                        if self.last_step_completed_time is not None:
                            interval = start_time - self.last_step_completed_time
                            self.step_intervals[label] = round(interval, 2)
                        else:
                            self.step_intervals[label] = 0
                        
                        # 更新上一个步骤完成时间为当前步骤的结束时间
                        self.last_step_completed_time = last_time
                        
                        print(f"步骤完成: {label}, 耗时 {duration:.2f}s, 间隔 {self.step_intervals.get(label, 0):.2f}s, 累计: {self.step_counts[label]}")
                        
                        # ========== 记录步骤到数据库 ==========
                        # 获取步骤显示名称
                        step_name = self.step_display_names.get(label, label)
                        
                        # 停止步骤视频录制并获取视频信息
                        step_video_info = self.stop_step_recording(label)
                        
                        self.record_step(
                            step_label=label,
                            step_name=step_name,
                            start_time=start_time,
                            end_time=last_time,
                            duration=duration,
                            interval=self.step_intervals.get(label),
                            confidence=None,
                            is_valid=True,
                            video_info=step_video_info
                        )
                        
                        # 记录该步骤消失时的结束时间，供顺序模式结算时补写未“消失”的步骤记录
                        if not hasattr(self, '_last_disappeared_step_times'):
                            self._last_disappeared_step_times = {}
                        self._last_disappeared_step_times[label] = last_time
                        
                        # 收集需要检查事件的步骤（延迟判定）
                        pending_event_checks.append(label)
        
        # ========== 延迟判定阶段 ==========
        # 所有步骤记录完成后，再统一进行事件判定
        # 这样即使判定触发 end_cycle，也不会影响其他步骤的记录
        for completed_label in pending_event_checks:
            self._check_events(completed_label)
        
        # ========== 周期总时长超时NG ==========
        if (self.cycle_max_duration > 0
                and self.cycle_start_time is not None
                and self.current_cycle_steps):
            cycle_elapsed = current_time - self.cycle_start_time
            if cycle_elapsed > self.cycle_max_duration:
                print(f"[周期超时NG] 周期总时长 {cycle_elapsed:.1f}s > {self.cycle_max_duration}s，强制NG")
                self._force_timeout_ng(f'周期总时长超时 ({cycle_elapsed:.1f}s > {self.cycle_max_duration}s)')
                return

        # ========== 空闲超时结算 ==========
        if (self.idle_timeout_seconds > 0
                and self.current_cycle_steps
                and self._last_step_added_time is not None):
            idle_elapsed = current_time - self._last_step_added_time
            if idle_elapsed > self.idle_timeout_seconds:
                print(f"[空闲超时] {idle_elapsed:.1f}s > {self.idle_timeout_seconds}s，强制结算当前周期 (步骤={self.current_cycle_steps})")
                _lm = self.project_config.get('logic_mode') if self.project_config else 'detection'
                _pc = self.project_config.get('pipeline_config', {}) if self.project_config else {}
                _cbo = _pc.get('custom_based_on')
                if _lm == 'custom' and _cbo == 'sequential':
                    self._settle_custom_cycle()
                elif _lm == 'sequential':
                    self._settle_sequential_cycle()
                elif _lm == 'detection':
                    self._settle_detection_cycle()
        
    
    def _inject_backup_steps(self, this_cycle: list, expected_labels: list) -> list:
        """Inject primary step labels into this_cycle when their backup was seen but
        the primary itself is missing. Returns a new list with injections applied."""
        if not self.step_backup_map or not self.backup_steps_seen_in_cycle:
            return this_cycle
        
        for backup_label, primary_label in self.step_backup_map.items():
            if (backup_label in self.backup_steps_seen_in_cycle
                    and primary_label not in this_cycle
                    and primary_label in expected_labels):
                expected_idx = expected_labels.index(primary_label)
                insert_pos = 0
                for i, lbl in enumerate(this_cycle):
                    if lbl in expected_labels and expected_labels.index(lbl) < expected_idx:
                        insert_pos = i + 1
                this_cycle.insert(insert_pos, primary_label)
                print(f"替补注入: {backup_label} -> {primary_label} at position {insert_pos}")
        
        return this_cycle
    
    def _filter_cycle_by_duration(self, cycle_steps: list) -> list:
        """Remove steps whose duration falls outside [min_duration, max_duration].

        Only evaluates steps still tracked in step_last_seen (not yet validated
        by the normal disappearance handler).  Steps already removed from
        step_last_seen passed validation earlier; backup-injected steps have no
        tracking entry and are always kept.
        """
        filtered = []
        for label in cycle_steps:
            if label not in self.step_last_seen:
                filtered.append(label)
                continue

            if self.step_accept_once.get(label):
                filtered.append(label)
                continue

            time_config = self.step_time_config.get(label, {})
            min_dur = time_config.get('min_duration')
            max_dur = time_config.get('max_duration')

            if min_dur is None and max_dur is None:
                filtered.append(label)
                continue

            start_time = self.step_start_time.get(label, self.step_last_seen[label])
            duration = self.step_last_seen[label] - start_time

            is_valid = True
            if min_dur is not None and duration < min_dur:
                is_valid = False
            if max_dur is not None and duration > max_dur:
                is_valid = False

            if is_valid:
                filtered.append(label)
            else:
                print(f"[duration filter] {label}: {duration:.2f}s not in "
                      f"[{min_dur}, {max_dur}], removed from cycle")
        return filtered
    
    def _check_static_step_conditions(self, static_label: str):
        """静态步骤达到触发帧数后，检查自定义条件
        
        当静态步骤（如"工件堆积"）达到配置的触发帧数时，
        直接检查自定义条件中是否有匹配这个步骤的条件并触发对应事件。
        这样即使静态步骤设置为不参与周期（join_cycle=False），
        也能正确触发自定义条件中配置的事件（如NG）。
        
        Args:
            static_label: 触发的静态步骤标签
        """
        if not self.project_config:
            return
        
        logic_mode = self.project_config.get('logic_mode', 'detection')
        if logic_mode != 'custom':
            return  # 只在自定义模式下生效
        
        pipeline_config = self.project_config.get('pipeline_config', {})
        custom_conditions = pipeline_config.get('custom_conditions', [])
        steps_config = self.project_config.get('steps_config', [])
        events_config = self.project_config.get('events_config', [])
        
        if not custom_conditions:
            return
        
        # 创建步骤ID到标签的映射
        id_to_label = {}
        label_to_id = {}
        enabled_step_ids = set()
        for step in steps_config:
            step_id = step.get('id')
            label = step.get('label', '')
            if step_id and label:
                id_to_label[step_id] = label
                label_to_id[label] = step_id
                if step.get('enabled', True):
                    enabled_step_ids.add(step_id)
        
        static_step_id = label_to_id.get(static_label)
        if not static_step_id:
            return
        
        self.current_cycle_steps = self._filter_cycle_by_duration(self.current_cycle_steps)
        
        print(f"检查静态步骤 [{static_label}] 的自定义条件...")
        
        # 按优先级排序自定义条件
        sorted_conditions = sorted(custom_conditions, key=lambda c: c.get('priority', 999))
        
        for cond in sorted_conditions:
            cond_sequence = cond.get('sequence', [])
            cond_event_id = cond.get('event_id')
            
            if not cond_sequence or not cond_event_id:
                continue
            
            # 将条件中的步骤ID转换为标签（只包含启用的步骤）
            cond_labels = [id_to_label.get(sid) for sid in cond_sequence 
                          if sid in id_to_label and sid in enabled_step_ids]
            
            # 检查条件是否只包含这个静态步骤
            # 支持两种情况：
            # 1. 条件只有一个步骤，且就是这个静态步骤
            # 2. 条件的最后一个步骤是这个静态步骤（用于组合条件）
            if len(cond_labels) == 1 and cond_labels[0] == static_label:
                # 单步骤条件，直接触发
                print(f"  → 匹配单步骤自定义条件: [{static_label}]，触发事件 ID: {cond_event_id}")
                self._trigger_event(cond_event_id, f'静态步骤自定义条件触发: {static_label}')
                return  # 匹配后不再检查其他条件
            elif cond_labels and cond_labels[-1] == static_label:
                # 组合条件，检查前面的步骤是否都在当前周期中
                prefix_labels = cond_labels[:-1]
                if all(pl in self.current_cycle_steps for pl in prefix_labels):
                    print(f"  → 匹配组合自定义条件: {cond_labels}，触发事件 ID: {cond_event_id}")
                    self._trigger_event(cond_event_id, f'静态步骤自定义条件触发: {static_label}')
                    return  # 匹配后不再检查其他条件
        
        print(f"  → 未找到匹配的自定义条件")
    
    # ================================================================
    # Counting Mode (物品清点模式) — stats / cycle logic
    # ================================================================
    
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
    
    
    def _update_container_grouping(self, expected_items: dict, current_time: float,
                                    gone_confirm_frames: int = 30,
                                    cycle_strategy: str = 'all_gone'):
        """Group tracked items into their parent boxes by spatial containment.
        Per-box settlement uses the same gone-confirm logic as the cycle level.

        v2.7.7c 合并: 同一个 box 里同一 label 达到 step 配置的 max_recognized 后,
        新出现的 track_id 不再计数 (只刷新老条目的 last_seen). 这解决了帧内 top-N
        之后跨帧出现新 track_id (遮挡/重新出现) 导致的超额误判.
        """
        container_label = self._container_label
        expected_no_container = {k: v for k, v in expected_items.items() if k != container_label}

        # 从 steps_config 读每个 label 的 max_recognized 上限 (只处理 count_mode='track')
        max_recognized_per_label: dict = {}
        try:
            steps_config = (self.project_config or {}).get('steps_config', []) if self.project_config else []
            for step in steps_config:
                if not step.get('enabled', True):
                    continue
                if step.get('count_mode', 'track') != 'track':
                    continue
                lbl = step.get('label', '')
                if not lbl:
                    continue
                try:
                    mr = int(step.get('max_recognized', 0) or 0)
                    if mr > 0:
                        max_recognized_per_label[lbl] = mr
                except (TypeError, ValueError):
                    pass
        except Exception:
            pass

        # Collect active boxes and items from _tracking_objects
        active_box_dids = set()
        box_bboxes = {}  # {display_id: bbox}
        item_entries = []  # [(track_id, label, display_id, bbox)]
        
        for tid, obj in self._tracking_objects.items():
            if obj['class_name'] == container_label:
                did = obj['display_id']
                active_box_dids.add(did)
                box_bboxes[did] = obj['bbox']
                if did not in self._box_objects:
                    self._box_counter += 1
                    self._box_objects[did] = {
                        'display_id': did,
                        'bbox': obj['bbox'],
                        'first_seen': obj.get('first_seen', current_time),
                        'last_seen': current_time,
                        'gone_frames': 0,
                        'had_roi': False,
                        'items_ever_seen': {},
                        'item_class_counts': {},
                        'is_complete': False,
                    }
                else:
                    self._box_objects[did]['bbox'] = obj['bbox']
                    self._box_objects[did]['last_seen'] = current_time
            else:
                item_entries.append((
                    tid, obj['class_name'],
                    obj.get('display_id', ''), obj['bbox']
                ))
        
        # Also consider boxes in recently_lost as "still present" (within tolerance)
        for tid, obj in self._tracking_recently_lost.items():
            if obj['class_name'] == container_label:
                did = obj['display_id']
                if did in self._box_objects and did not in active_box_dids:
                    active_box_dids.add(did)
                    box_bboxes[did] = obj['bbox']
        
        # Assign items to boxes: item center inside box bbox, pick smallest box
        for item_tid, item_label, item_did, item_bbox in item_entries:
            item_cx = item_bbox['x'] + item_bbox['w'] / 2
            item_cy = item_bbox['y'] + item_bbox['h'] / 2
            
            best_box_did = None
            best_box_area = float('inf')
            for box_did, bb in box_bboxes.items():
                bx1, by1 = bb['x'], bb['y']
                bx2, by2 = bx1 + bb['w'], by1 + bb['h']
                if bx1 <= item_cx <= bx2 and by1 <= item_cy <= by2:
                    area = bb['w'] * bb['h']
                    if area < best_box_area:
                        best_box_area = area
                        best_box_did = box_did
            
            if best_box_did is not None:
                box_state = self._box_objects[best_box_did]
                if item_tid in box_state['items_ever_seen']:
                    box_state['items_ever_seen'][item_tid]['last_seen'] = current_time
                    continue

                # v2.7.7c 合并: 本 box 里该 label 已达上限 → 新 track_id 不再计数,
                # 只把最早进入的那条的 last_seen 刷新 (避免"找不到最新活跃 id"导致误判 gone)
                limit = max_recognized_per_label.get(item_label, 0)
                cur_count = box_state['item_class_counts'].get(item_label, 0)
                if limit > 0 and cur_count >= limit:
                    for _prev_tid, _info in box_state['items_ever_seen'].items():
                        if _info.get('label') == item_label:
                            _info['last_seen'] = current_time
                            break
                    continue

                box_state['items_ever_seen'][item_tid] = {
                    'label': item_label,
                    'display_id': item_did,
                    'first_seen': current_time,
                    'last_seen': current_time,
                }
                box_state['item_class_counts'][item_label] = \
                    box_state['item_class_counts'].get(item_label, 0) + 1
        
        # Recalculate completeness for all active boxes
        for box_did in active_box_dids:
            if box_did in self._box_objects:
                bs = self._box_objects[box_did]
                bs['is_complete'] = (not expected_no_container) or all(
                    bs['item_class_counts'].get(cls, 0) >= exp
                    for cls, exp in expected_no_container.items()
                )
                if cycle_strategy == 'roi_exit':
                    det = {'x': bs['bbox']['x'], 'y': bs['bbox']['y'],
                           'w': bs['bbox']['w'], 'h': bs['bbox']['h']}
                    if self._is_in_roi(det):
                        bs['had_roi'] = True
        
        # Per-box gone confirmation (same pattern as cycle-level settlement)
        for box_did in list(self._box_objects.keys()):
            bs = self._box_objects[box_did]
            box_visible = box_did in active_box_dids
            
            should_count_gone = False
            if cycle_strategy == 'roi_exit':
                should_count_gone = bs['had_roi'] and not box_visible
            else:
                should_count_gone = not box_visible
            
            if should_count_gone:
                bs['gone_frames'] = bs.get('gone_frames', 0) + 1
                if bs['gone_frames'] == 1:
                    print(f"[Container] {box_did} gone, confirming: {gone_confirm_frames} frames")
                if bs['gone_frames'] >= gone_confirm_frames:
                    print(f"[Container] {box_did} confirmed gone ({bs['gone_frames']}/{gone_confirm_frames})")
                    _sd = self.project_config.get('pipeline_config', {}).get('settle_dedup', False) if self.project_config else False
                    if _sd and not self.current_cycle_id:
                        self.start_cycle()
                    self._settle_box(box_did, expected_items)
            else:
                if bs.get('gone_frames', 0) > 0:
                    print(f"[Container] {box_did} reappeared, reset ({bs['gone_frames']}/{gone_confirm_frames})")
                bs['gone_frames'] = 0
    
    def _settle_box(self, box_display_id: str, expected_items: dict):
        """Settle a single box: record its items and completeness."""
        box_state = self._box_objects.pop(box_display_id, None)
        if box_state is None:
            return
        
        container_label = self._container_label
        expected_no_container = {k: v for k, v in expected_items.items() if k != container_label}
        
        item_counts = box_state['item_class_counts']
        missing = []
        extra = []
        for cls, exp in expected_no_container.items():
            actual = item_counts.get(cls, 0)
            if actual < exp:
                display = self.step_display_names.get(cls, cls)
                missing.append(f"{display}: {actual}/{exp}")
            elif actual > exp:
                display = self.step_display_names.get(cls, cls)
                extra.append(f"{display}: {actual}/{exp}")
        # 不在期望清单中的类别不参与判定（允许画框但不影响 OK/NG）
        
        is_ok = not missing and not extra
        
        result = {
            'display_id': box_display_id,
            'first_seen': box_state['first_seen'],
            'last_seen': box_state['last_seen'],
            'item_counts': dict(item_counts),
            'expected': dict(expected_no_container),
            'is_complete': is_ok,
            'missing': missing,
            'extra': extra,
            'items_detail': [
                {'label': v['label'], 'display_id': v['display_id']}
                for v in box_state['items_ever_seen'].values()
            ],
        }
        self._box_settled_results.append(result)
        
        status = "OK" if is_ok else "NG"
        print(f"[Container] {box_display_id} settled: {status}, "
              f"items={item_counts}, expected={expected_no_container}")

        # v2.7.14: 容器模式下也往 StepRecord 写一条一条物品, 让数据中心展开 cycle 能看到
        # ——之前只触发 _trigger_event, 没 record_step, 前端数据中心永远是空的。
        # 严格过滤: 只写在"物品清单"(expected_no_container) 里的类别。
        # 容器类(箱子)不算 item, 本身不进 StepRecord。
        current_time = time.time()

        def _in_item_checklist(cls_name: str) -> bool:
            if cls_name == container_label:
                return False
            if not expected_no_container:
                return True
            return cls_name in expected_no_container

        items_to_record = []
        for info in box_state.get('items_ever_seen', {}).values():
            if not _in_item_checklist(info.get('label', '')):
                continue
            items_to_record.append({
                'label': info.get('label', ''),
                'display_id': info.get('display_id', ''),
                'first_seen': info.get('first_seen', current_time),
                'last_seen': info.get('last_seen', current_time),
            })
        # 按 display_id 去重, 保留最早/最晚时间
        dedup = {}
        for it in items_to_record:
            did = it['display_id']
            if did not in dedup:
                dedup[did] = it
            else:
                dedup[did]['first_seen'] = min(dedup[did]['first_seen'], it['first_seen'])
                dedup[did]['last_seen'] = max(dedup[did]['last_seen'], it['last_seen'])
        items_to_record = list(dedup.values())

        # 兜底: items_ever_seen 已被清 / 丢失, 但 item_class_counts 有累计 → 虚拟补齐
        if not items_to_record and item_counts:
            virtual = []
            for cls_name, cnt in sorted(item_counts.items(), key=lambda kv: kv[0]):
                if cnt <= 0 or not _in_item_checklist(cls_name):
                    continue
                prefix = self._get_display_prefix(cls_name)
                for i in range(1, int(cnt) + 1):
                    virtual.append({
                        'label': cls_name,
                        'display_id': f"{prefix}{i}",
                        'first_seen': box_state.get('first_seen', current_time),
                        'last_seen': box_state.get('last_seen', current_time),
                    })
            if virtual:
                print(f"[Container] {box_display_id} items_ever_seen 空, "
                      f"按计数器虚拟补齐 {len(virtual)} 条")
                items_to_record = virtual

        # 维护 current_cycle_steps 供 end_cycle 写入 cycle.step_sequence
        if items_to_record:
            self.current_cycle_steps = [it['display_id'] for it in items_to_record]
            step_order = 0
            for it in items_to_record:
                step_order += 1
                dur = max(0.0, (it['last_seen'] or current_time) - (it['first_seen'] or current_time))
                step_name = it['display_id']
                self.record_step(
                    step_label=it['label'],
                    step_name=step_name,
                    start_time=it['first_seen'] or current_time,
                    end_time=it['last_seen'] or current_time,
                    duration=round(dur, 2),
                    step_order=step_order,
                    is_valid=True,
                )

        if is_ok:
            self._trigger_event(1, f'{box_display_id} OK: {item_counts}')
        else:
            reasons = []
            if missing:
                reasons.append(f'missing: {missing}')
            if extra:
                reasons.append(f'extra: {extra}')
            self._trigger_event(2, f'{box_display_id} NG: {", ".join(reasons) if reasons else "no items"}')
    
    def _rebuild_checklist(self, expected_items: dict):
        """Rebuild the item checklist from current tracking state."""
        if self._container_mode and self._container_label:
            self._rebuild_container_checklist(expected_items)
            return
        
        self._tracking_item_checklist = {}
        for cls_name, expected_count in expected_items.items():
            tracking_n = self._tracking_class_counters.get(cls_name, 0)
            event_n = self._event_counters.get(cls_name, 0)
            stack_n = self._stack_counters.get(cls_name, 0)
            # v2.7.4: 堆叠模式下取 max(tracking, stack)，避免重复计数
            actual = max(tracking_n, stack_n) + event_n
            display_name = self.step_display_names.get(cls_name, cls_name)
            prefix = self._tracking_letter_map.get(cls_name, display_name)
            self._tracking_item_checklist[cls_name] = {
                'expected': expected_count, 'counted': actual,
                'prefix': prefix, 'display_name': display_name
            }
        for cls_name, count in self._tracking_class_counters.items():
            if cls_name not in self._tracking_item_checklist:
                stack_n = self._stack_counters.get(cls_name, 0)
                actual = max(count, stack_n)
                display_name = self.step_display_names.get(cls_name, cls_name)
                prefix = self._tracking_letter_map.get(cls_name, display_name)
                self._tracking_item_checklist[cls_name] = {
                    'expected': 0, 'counted': actual,
                    'prefix': prefix, 'display_name': display_name
                }
        for cls_name, count in self._event_counters.items():
            if cls_name not in self._tracking_item_checklist:
                display_name = self.step_display_names.get(cls_name, cls_name)
                self._tracking_item_checklist[cls_name] = {
                    'expected': 0, 'counted': count,
                    'prefix': display_name, 'display_name': display_name
                }
        # v2.7.4: 仅 stack 模式（无 tracking_class_counter 也无 event_counter）的 label
        for cls_name, count in self._stack_counters.items():
            if cls_name not in self._tracking_item_checklist:
                display_name = self.step_display_names.get(cls_name, cls_name)
                prefix = self._tracking_letter_map.get(cls_name, display_name)
                self._tracking_item_checklist[cls_name] = {
                    'expected': 0, 'counted': count,
                    'prefix': prefix, 'display_name': display_name
                }
    
    def _rebuild_container_checklist(self, expected_items: dict):
        """Build per-box checklist for container mode."""
        container_label = self._container_label
        expected_no_container = {k: v for k, v in expected_items.items() if k != container_label}
        
        boxes_info = {}
        for box_did, bs in self._box_objects.items():
            items_info = {}
            for cls, exp in expected_no_container.items():
                display_name = self.step_display_names.get(cls, cls)
                actual = bs['item_class_counts'].get(cls, 0)
                items_info[cls] = {
                    'expected': exp,
                    'counted': actual,
                    'display_name': display_name,
                }
            for cls, cnt in bs['item_class_counts'].items():
                if cls not in items_info:
                    display_name = self.step_display_names.get(cls, cls)
                    items_info[cls] = {
                        'expected': 0,
                        'counted': cnt,
                        'display_name': display_name,
                    }
            boxes_info[box_did] = {
                'items': items_info,
                'complete': bs['is_complete'],
            }
        
        self._tracking_item_checklist = {
            '_container_mode': True,
            '_boxes': boxes_info,
            '_settled_count': len(self._box_settled_results),
            '_settled_ok': sum(1 for r in self._box_settled_results if r['is_complete']),
            '_settled_ng': sum(1 for r in self._box_settled_results if not r['is_complete']),
        }
    
    def _settle_counting_cycle(self, expected_items: dict, check_order: bool = False, expected_order: list = None):
        """Validate tracking-mode cycle and trigger OK or NG event."""
        print(f"[Tracking] Settling cycle: counters={self._tracking_class_counters}, events={self._event_counters}, expected={expected_items}")
        
        # In container mode, settle remaining boxes then reset (skip cycle-level count validation)
        if self._container_mode:
            box_list = list(self._box_objects.keys())
            _sd = self.project_config.get('pipeline_config', {}).get('settle_dedup', False) if self.project_config else False
            for i, box_did in enumerate(box_list):
                if _sd and i > 0 and not self.current_cycle_id:
                    self.start_cycle()
                self._settle_box(box_did, expected_items)
            total_ok = sum(1 for r in self._box_settled_results if r['is_complete'])
            total_ng = sum(1 for r in self._box_settled_results if not r['is_complete'])
            total = len(self._box_settled_results)
            print(f"[Container] Cycle end: {total} boxes settled (OK={total_ok}, NG={total_ng})")
            self._reset_counting_cycle()
            return
        
        current_time = time.time()
        
        # ===== Collect all item instances from active + recently_lost =====
        # v2.7.14: 只保留"物品清单"(expected_items) 里的类别。
        # 启用但未入清单的类(比如"箱子"、"泡沫槽") 允许模型识别/画框/跟踪, 但不写 StepRecord。
        # 仅当 expected_items 非空时过滤; 为空视为"任意类都算", 保留原行为。
        def _in_checklist(cls_name: str) -> bool:
            if not expected_items:
                return True
            return cls_name in expected_items

        all_items = []
        for tid, obj in self._tracking_objects.items():
            if not _in_checklist(obj['class_name']):
                continue
            all_items.append({
                'class_name': obj['class_name'],
                'display_id': obj['display_id'],
                'first_seen': obj.get('first_seen', 0),
                'last_seen': obj.get('last_seen', 0),
                'order_idx': obj.get('order_idx', 0),
            })
        for tid, obj in self._tracking_recently_lost.items():
            if not _in_checklist(obj['class_name']):
                continue
            all_items.append({
                'class_name': obj['class_name'],
                'display_id': obj['display_id'],
                'first_seen': obj.get('first_seen', 0),
                'last_seen': obj.get('lost_time', obj.get('first_seen', 0)),
                'order_idx': obj.get('order_idx', 0),
            })
        
        all_items.sort(key=lambda o: o['order_idx'])
        
        seen_display_ids = set()
        unique_items = []
        for item in all_items:
            if item['display_id'] not in seen_display_ids:
                seen_display_ids.add(item['display_id'])
                unique_items.append(item)

        # v2.7.14: 兜底——settle 触发时 _tracking_objects / _tracking_recently_lost 可能
        # 都已被清空(物品全离开 + 遮挡容忍短, 导致 recently_lost 过期)。此时 StepRecord
        # 会一条不写, 前端数据中心展开 cycle 全空。用 _tracking_class_counters / _event_counters
        # 里累计的数量虚拟补齐, 至少让用户看到本周期识别到了几个 A / 几个 B(没有精确时间戳)。
        if not unique_items:
            virtual_items = []
            virtual_order = 0
            for cls_name, cnt in sorted(
                self._tracking_class_counters.items(), key=lambda kv: kv[0]
            ):
                if cnt <= 0 or not _in_checklist(cls_name):
                    continue
                prefix = self._get_display_prefix(cls_name)
                for i in range(1, int(cnt) + 1):
                    virtual_order += 1
                    virtual_items.append({
                        'class_name': cls_name,
                        'display_id': f"{prefix}{i}",
                        'first_seen': self.cycle_start_time or current_time,
                        'last_seen': current_time,
                        'order_idx': virtual_order,
                    })
            if virtual_items:
                print(f"[Tracking] settle 时活动/丢失表均空, 用计数器虚拟补齐 "
                      f"{len(virtual_items)} 条: {[v['display_id'] for v in virtual_items]}")
                unique_items = virtual_items

        self.current_cycle_steps = [item['display_id'] for item in unique_items]
        
        step_order = 0
        for item in unique_items:
            start_t = item['first_seen']
            end_t = item['last_seen']
            duration = max(0, end_t - start_t) if start_t and end_t else 0
            step_name = item['display_id']
            step_label = item['class_name']
            step_order += 1
            self.record_step(
                step_label=step_label,
                step_name=step_name,
                start_time=start_t,
                end_time=end_t,
                duration=round(duration, 2),
                step_order=step_order,
                is_valid=True,
            )
        
        for cls_name, count in self._event_counters.items():
            if count > 0 and _in_checklist(cls_name):
                display_name = self.step_display_names.get(cls_name, cls_name)
                step_order += 1
                start_t = self._event_first_seen.get(cls_name, self.cycle_start_time or current_time)
                end_t = self._event_last_seen.get(cls_name, current_time)
                duration = max(0, end_t - start_t) if start_t and end_t else 0
                self.current_cycle_steps.append(f"{display_name}\u00d7{count}")
                self.record_step(
                    step_label=cls_name,
                    step_name=f"{display_name}\u00d7{count}",
                    start_time=start_t,
                    end_time=end_t,
                    duration=round(duration, 2),
                    step_order=step_order,
                    is_valid=True,
                )
        
        # ===== Validate counts (merge track counters + event counters) =====
        merged_counters = dict(self._tracking_class_counters)
        for cls_name, cnt in self._event_counters.items():
            merged_counters[cls_name] = merged_counters.get(cls_name, 0) + cnt
        
        missing = []
        extra = []
        for cls_name, exp in expected_items.items():
            actual = merged_counters.get(cls_name, 0)
            if actual < exp:
                missing.append(f"{cls_name}: {actual}/{exp}")
            elif actual > exp:
                extra.append(f"{cls_name}: {actual}/{exp}")
        # 不在期望清单中的类别不参与判定（允许画框但不影响 OK/NG）
        
        order_ok = True
        if check_order and expected_order:
            placed_classes = [item['class_name'] for item in unique_items]
            if placed_classes != expected_order:
                order_ok = False
        
        print(f"[Tracking] 判定: merged={merged_counters}, missing={missing}, extra={extra}, "
              f"cycle_id={self.current_cycle_id}, cycle_active={self._tracking_cycle_active}")
        
        if not expected_items:
            self._trigger_event(1, f'Counting complete: {dict(merged_counters)}')
        elif missing or extra:
            reasons = []
            if missing: reasons.append(f'missing: {missing}')
            if extra: reasons.append(f'extra: {extra}')
            self._trigger_event(2, ', '.join(reasons))
        elif not order_ok:
            actual_seq = [item['class_name'] for item in unique_items]
            self._trigger_event(2, f'Order wrong: expected={expected_order}, actual={actual_seq}')
        else:
            self._trigger_event(1, f'All complete: {dict(merged_counters)}')
        
        self._reset_counting_cycle()
    
    def _check_events(self, completed_step: str):
        """检查是否触发事件"""
        if not self.project_config:
            return
        
        events_config = self.project_config.get('events_config', [])
        logic_mode = self.project_config.get('logic_mode', 'detection')
        pipeline_config = self.project_config.get('pipeline_config', {})
        steps_config = self.project_config.get('steps_config', [])
        
        # 创建步骤ID到标签的映射
        id_to_label = {}
        label_to_id = {}
        for step in steps_config:
            step_id = step.get('id')
            label = step.get('label', '')
            if step_id and label:
                id_to_label[step_id] = label
                label_to_id[label] = step_id
        
        # 获取启用的步骤标签和ID集合
        enabled_step_labels = [s.get('label') for s in steps_config if s.get('enabled', True)]
        enabled_step_ids = {s.get('id') for s in steps_config if s.get('enabled', True)}
        
        # 自定义模式
        if logic_mode == 'custom':
            custom_based_on = pipeline_config.get('custom_based_on')  # 'sequential', 'detection', 或 None
            custom_conditions = pipeline_config.get('custom_conditions', [])
            
            self.current_cycle_steps = self._filter_cycle_by_duration(self.current_cycle_steps)
            
            # 先检查自定义条件（按优先级排序）
            condition_matched = False
            if custom_conditions:
                # 按优先级排序（数字越小优先级越高）
                sorted_conditions = sorted(custom_conditions, key=lambda c: c.get('priority', 999))
                
                for cond in sorted_conditions:
                    cond_sequence = cond.get('sequence', [])
                    cond_event_id = cond.get('event_id')
                    
                    if not cond_sequence or not cond_event_id:
                        continue
                    
                    # 将条件中的步骤ID转换为标签（只包含启用的步骤）
                    cond_labels = [id_to_label.get(sid) for sid in cond_sequence if sid in id_to_label and sid in enabled_step_ids]
                    
                    if not cond_labels:
                        continue
                    
                    print(f"自定义条件检查: 条件序列={cond_labels}, 当前周期={self.current_cycle_steps}")
                    
                    # 检查是否完全匹配（数量和顺序都要相同）
                    if self.current_cycle_steps == cond_labels:
                        print(f"  → 条件匹配！触发事件 {cond_event_id}")
                        self._trigger_event(cond_event_id, f'自定义条件匹配: {cond_labels}')
                        self.current_cycle_steps = []
                        self.backup_steps_seen_in_cycle = set()
                        self.last_added_step = None
                        return  # 匹配后不再检查其他条件和基础模式
            
            # 没有自定义条件匹配，回退到基础模式
            # first_step 模式下跳过"最后一步消失"触发，只由第一步重现触发结算
            if self.settlement_mode == 'first_step':
                pass
            elif custom_based_on == 'sequential':
                last_step_label = self._get_last_sequence_step_label()
                if last_step_label and completed_step == last_step_label:
                    if completed_step in self.current_cycle_steps:
                        self._check_custom_sequential_mode(pipeline_config, id_to_label)
                    else:
                        print(f"  → 步骤 [{completed_step}] 不在当前周期中，跳过判定（可能是上一周期的残留）")
            elif custom_based_on == 'detection':
                if completed_step in self.current_cycle_steps:
                    self._check_custom_detection_mode(pipeline_config, id_to_label, enabled_step_labels)
                else:
                    print(f"  → 步骤 [{completed_step}] 不在当前周期中，跳过判定（可能是上一周期的残留）")
        
        # 顺序模式
        elif logic_mode == 'sequential':
            if self.settlement_mode != 'first_step':
                last_step_label = self._get_last_sequence_step_label()
                if last_step_label and completed_step == last_step_label:
                    if completed_step in self.current_cycle_steps:
                        self._check_sequential_mode(pipeline_config, id_to_label)
                    else:
                        print(f"  → 步骤 [{completed_step}] 不在当前周期中，跳过判定（可能是上一周期的残留）")
        
        # 检测模式
        elif logic_mode == 'detection':
            if self.settlement_mode != 'first_step':
                last_det_label = self._get_last_detection_step_label()
                if last_det_label and completed_step == last_det_label:
                    if completed_step in self.current_cycle_steps:
                        self._settle_detection_cycle()
                    else:
                        print(f"  → 步骤 [{completed_step}] 不在当前周期中，跳过判定（可能是上一周期的残留）")
        
        # 检查步骤特定事件
        for step in steps_config:
            if step.get('label') == completed_step:
                trigger_event = step.get('trigger_event')
                if trigger_event:
                    self._trigger_event(trigger_event, f'步骤 {completed_step} 触发')
    
    def _check_sequential_mode(self, pipeline_config: dict, id_to_label: dict):
        """检查顺序模式（由 last_step 消失触发）
        
        关键设计：在 current_cycle_steps 中以 last_step 首次出现为界拆分，
        拆分后的前半部分为本周期判定依据，后半部分保留给下一周期。
        """
        sequence_order = pipeline_config.get('sequence_order', [])
        if not sequence_order:
            self.current_cycle_steps = []
            self.backup_steps_seen_in_cycle = set()
            self.last_added_step = None
            # 周期结算后重置步骤时序状态，确保下一轮的相同步骤可被视为“新出现”
            self.step_last_seen.clear()
            self.step_start_time.clear()
            self.step_consecutive_frames.clear()
            self.step_frame_confirmed.clear()
            self._step_gap_count.clear()
            self.last_step_completed_time = None
            return
        
        # 获取启用的步骤ID集合
        steps_config = self.project_config.get('steps_config', []) if self.project_config else []
        enabled_step_ids = {s.get('id') for s in steps_config if s.get('enabled', True)}
        
        # 将步骤ID转换为标签名（只包含启用的步骤）
        expected_labels = []
        for item in sequence_order:
            step_id = item.get('step_id')
            if step_id in id_to_label and step_id in enabled_step_ids:
                expected_labels.append(id_to_label[step_id])
        
        if not expected_labels:
            self.current_cycle_steps = []
            self.backup_steps_seen_in_cycle = set()
            self.last_added_step = None
            # 周期结算后重置步骤时序状态，确保下一轮的相同步骤可被视为“新出现”
            self.step_last_seen.clear()
            self.step_start_time.clear()
            self.step_consecutive_frames.clear()
            self.step_frame_confirmed.clear()
            self._step_gap_count.clear()
            self.last_step_completed_time = None
            return
        
        last_step_label = expected_labels[-1]
        
        # Split at last_step's first occurrence: before = this cycle, after = carry to next
        if last_step_label in self.current_cycle_steps:
            split_idx = self.current_cycle_steps.index(last_step_label)
            this_cycle = self.current_cycle_steps[:split_idx + 1]
            next_carry = self.current_cycle_steps[split_idx + 1:]
        else:
            this_cycle = list(self.current_cycle_steps)
            next_carry = []
        
        this_cycle = self._inject_backup_steps(this_cycle, expected_labels)
        this_cycle = self._filter_cycle_by_duration(this_cycle)
        self.current_cycle_steps = this_cycle
        
        if not this_cycle:
            self._discard_empty_cycle()
            self.current_cycle_steps = []
            self.backup_steps_seen_in_cycle = set()
            self.last_added_step = None
            self.step_last_seen.clear()
            self.step_start_time.clear()
            self.step_consecutive_frames.clear()
            self.step_frame_confirmed.clear()
            self._step_gap_count.clear()
            self.last_step_completed_time = None
            return
        
        self._reconcile_step_records()
        
        print(f"顺序模式检查: 期望={expected_labels}, 本周期={this_cycle}, 下周期残留={next_carry}")
        
        # ── 判定 ──
        if len(this_cycle) > len(expected_labels):
            from collections import Counter
            step_counter = Counter(this_cycle)
            duplicated = [s for s, cnt in step_counter.items() if cnt > 1]
            print(f"  → 序列长度({len(this_cycle)})超过预期({len(expected_labels)}) → NG")
            self._trigger_event(2, f'重复步骤: {duplicated}')
        elif not all(lbl in this_cycle for lbl in expected_labels):
            missing = [l for l in expected_labels if l not in this_cycle]
            print(f"  → 周期不完整，缺少: {missing} → NG")
            self._trigger_event(2, f'周期不完整，缺少: {missing}')
        else:
            cycle_order_correct = True
            order_error_labels = []
            last_pos = -1
            prev_lbl = None
            for lbl in expected_labels:
                pos = this_cycle.index(lbl)
                if pos < last_pos:
                    cycle_order_correct = False
                    order_error_labels = [prev_lbl, lbl]
                    break
                last_pos = pos
                prev_lbl = lbl
            if cycle_order_correct:
                print(f"  → 顺序正确 → OK")
                self._trigger_event(1, '顺序正确完成')
            else:
                print(f"  → 顺序错误 → NG: {order_error_labels}")
                self._trigger_event(2, f'顺序错误，期望[{order_error_labels[0]}]在前 实际[{order_error_labels[1]}]在前')
        
        # ── 补计：对本周期中尚未被计数的步骤进行补计 ──
        for label in this_cycle:
            if label in self.step_last_seen:
                start_time = self.step_start_time.get(label, self.step_last_seen[label])
                last_time = self.step_last_seen[label]
                duration = last_time - start_time
                
                time_config = self.step_time_config.get(label, {})
                min_duration = time_config.get('min_duration')
                max_duration = time_config.get('max_duration')
                
                is_valid = True
                if min_duration is not None and duration < min_duration:
                    is_valid = False
                if max_duration is not None and duration > max_duration:
                    is_valid = False
                
                if is_valid:
                    if label not in self.step_counts:
                        self.step_counts[label] = 0
                    self.step_counts[label] += 1
                    rounded_dur = round(duration, 2)
                    self.step_durations[label] = rounded_dur
                    self.step_durations_history.setdefault(label, []).append(rounded_dur)
                    print(f"  顺序判定补计: {label}, 耗时 {duration:.2f}s, 累计: {self.step_counts[label]}")
                
                del self.step_last_seen[label]
                if label in self.step_start_time:
                    del self.step_start_time[label]
        
        # ── 重置 / 承接下一周期 ──
        self.step_consecutive_frames.clear()
        self.step_frame_confirmed.clear()
        self._step_gap_count.clear()
        if next_carry:
            self.current_cycle_steps = next_carry
            self.last_added_step = next_carry[-1]
            self.cycle_start_time = self.step_start_time.get(next_carry[0], time.time())
            self._last_step_added_time = time.time()
            self.start_cycle()
        else:
            self.current_cycle_steps = []
            self.backup_steps_seen_in_cycle = set()
            self.last_added_step = None
            self._last_step_added_time = None
            self.step_last_seen.clear()
            self.step_start_time.clear()
            self.last_step_completed_time = None

    def _check_custom_sequential_mode(self, pipeline_config: dict, id_to_label: dict):
        """检查自定义模式（基于顺序模式）的判定
        
        关键设计：在 current_cycle_steps 中以 last_step 首次出现为界拆分。
        使用独立的 custom_sequence_order 配置进行判定。
        """
        # 使用自定义模式独立的顺序配置
        sequence_order = pipeline_config.get('custom_sequence_order', [])
        if not sequence_order:
            self.current_cycle_steps = []
            self.backup_steps_seen_in_cycle = set()
            self.last_added_step = None
            self.step_last_seen.clear()
            self.step_start_time.clear()
            self.step_consecutive_frames.clear()
            self.step_frame_confirmed.clear()
            self._step_gap_count.clear()
            self.last_step_completed_time = None
            return
        
        # 获取启用的步骤ID集合
        steps_config = self.project_config.get('steps_config', []) if self.project_config else []
        enabled_step_ids = {s.get('id') for s in steps_config if s.get('enabled', True)}
        
        # 将步骤ID转换为标签名（只包含启用的步骤）
        expected_labels = []
        for item in sequence_order:
            step_id = item.get('step_id')
            if step_id in id_to_label and step_id in enabled_step_ids:
                expected_labels.append(id_to_label[step_id])
        
        if not expected_labels:
            self.current_cycle_steps = []
            self.backup_steps_seen_in_cycle = set()
            self.last_added_step = None
            self.step_last_seen.clear()
            self.step_start_time.clear()
            self.step_consecutive_frames.clear()
            self.step_frame_confirmed.clear()
            self._step_gap_count.clear()
            self.last_step_completed_time = None
            return
        
        last_step_label = expected_labels[-1]
        
        # Split at last_step's first occurrence: before = this cycle, after = carry to next
        if last_step_label in self.current_cycle_steps:
            split_idx = self.current_cycle_steps.index(last_step_label)
            this_cycle = self.current_cycle_steps[:split_idx + 1]
            next_carry = self.current_cycle_steps[split_idx + 1:]
        else:
            this_cycle = list(self.current_cycle_steps)
            next_carry = []
        
        this_cycle = self._inject_backup_steps(this_cycle, expected_labels)
        this_cycle = self._filter_cycle_by_duration(this_cycle)
        self.current_cycle_steps = this_cycle
        
        if not this_cycle:
            self._discard_empty_cycle()
            self.current_cycle_steps = []
            self.backup_steps_seen_in_cycle = set()
            self.last_added_step = None
            self.step_last_seen.clear()
            self.step_start_time.clear()
            self.step_consecutive_frames.clear()
            self.step_frame_confirmed.clear()
            self._step_gap_count.clear()
            self.last_step_completed_time = None
            return
        
        self._reconcile_step_records()
        
        print(f"自定义模式（基于顺序）检查: 期望={expected_labels}, 本周期={this_cycle}, 下周期残留={next_carry}")
        
        # ── 判定 ──
        if len(this_cycle) > len(expected_labels):
            from collections import Counter
            step_counter = Counter(this_cycle)
            duplicated = [s for s, cnt in step_counter.items() if cnt > 1]
            print(f"  → 序列长度({len(this_cycle)})超过预期({len(expected_labels)}) → NG")
            self._trigger_event(2, f'重复步骤: {duplicated}')
        elif not all(lbl in this_cycle for lbl in expected_labels):
            missing = [l for l in expected_labels if l not in this_cycle]
            print(f"  → 周期不完整，缺少: {missing} → NG")
            self._trigger_event(2, f'周期不完整，缺少: {missing}')
        else:
            cycle_order_correct = True
            order_error_labels = []
            last_pos = -1
            prev_lbl = None
            for lbl in expected_labels:
                pos = this_cycle.index(lbl)
                if pos < last_pos:
                    cycle_order_correct = False
                    order_error_labels = [prev_lbl, lbl]
                    break
                last_pos = pos
                prev_lbl = lbl
            if cycle_order_correct:
                print(f"  → 顺序正确 → OK")
                self._trigger_event(1, '顺序正确完成')
            else:
                print(f"  → 顺序错误 → NG: {order_error_labels}")
                self._trigger_event(2, f'顺序错误，期望[{order_error_labels[0]}]在前 实际[{order_error_labels[1]}]在前')
        
        # ── 补计：对本周期中尚未被计数的步骤进行补计 ──
        for label in this_cycle:
            if label in self.step_last_seen:
                start_time = self.step_start_time.get(label, self.step_last_seen[label])
                last_time = self.step_last_seen[label]
                duration = last_time - start_time
                
                time_config = self.step_time_config.get(label, {})
                min_duration = time_config.get('min_duration')
                max_duration = time_config.get('max_duration')
                
                is_valid = True
                if min_duration is not None and duration < min_duration:
                    is_valid = False
                if max_duration is not None and duration > max_duration:
                    is_valid = False
                
                if is_valid:
                    if label not in self.step_counts:
                        self.step_counts[label] = 0
                    self.step_counts[label] += 1
                    rounded_dur = round(duration, 2)
                    self.step_durations[label] = rounded_dur
                    self.step_durations_history.setdefault(label, []).append(rounded_dur)
                    print(f"  自定义顺序判定补计: {label}, 耗时 {duration:.2f}s, 累计: {self.step_counts[label]}")
                
                del self.step_last_seen[label]
                if label in self.step_start_time:
                    del self.step_start_time[label]
        
        # ── 重置 / 承接下一周期 ──
        self.step_consecutive_frames.clear()
        self.step_frame_confirmed.clear()
        self._step_gap_count.clear()
        if next_carry:
            self.current_cycle_steps = next_carry
            self.last_added_step = next_carry[-1]
            self.cycle_start_time = self.step_start_time.get(next_carry[0], time.time())
            self._last_step_added_time = time.time()
            self.start_cycle()
        else:
            self.current_cycle_steps = []
            self.backup_steps_seen_in_cycle = set()
            self.last_added_step = None
            self._last_step_added_time = None
            self.step_last_seen.clear()
            self.step_start_time.clear()
            self.last_step_completed_time = None

    def _check_custom_detection_mode(self, pipeline_config: dict, id_to_label: dict, enabled_step_labels: list):
        """检查自定义模式（基于检测模式）
        
        使用独立的 custom_detection_steps 配置
        """
        detection_step_ids = pipeline_config.get('custom_detection_steps', [])
        
        # 将步骤ID转换为标签名
        if detection_step_ids:
            detection_labels = [id_to_label.get(sid) for sid in detection_step_ids if sid in id_to_label]
        else:
            detection_labels = enabled_step_labels
        
        if not detection_labels:
            return
        
        self.current_cycle_steps = self._filter_cycle_by_duration(self.current_cycle_steps)
        
        print(f"自定义模式（基于检测）检查: 需要={detection_labels}, 当前周期={self.current_cycle_steps}")
        
        if all(label in self.current_cycle_steps for label in detection_labels):
            self._trigger_event(1, '检测完成')  # 事件1: 合格
            self.current_cycle_steps = []
            self.backup_steps_seen_in_cycle = set()
            self.last_added_step = None
    
    def _trigger_event(self, event_id, reason: str) -> bool:
        """触发事件。返回 True 表示事件已触发，False 表示被抑制或失败。"""
        if not self.project_config:
            return False
        
        # 防重复结算（仅在项目配置中开启 settle_dedup 时生效）
        settle_dedup = self.project_config.get('pipeline_config', {}).get('settle_dedup', False)
        if settle_dedup and not self.current_cycle_id and self.recording_enabled:
            print(f"[_trigger_event] 防重复结算: 跳过, 当前无活跃周期 (event={event_id}, reason={reason})")
            return False
        
        # NG cycle protection: suppress rapid consecutive NG reports
        current_time = time.time()
        is_ng = (event_id == 2 or str(event_id) == '2')
        if is_ng:
            ng_protect_sec = self.project_config.get('pipeline_config', {}).get(
                'ng_cycle_protect_seconds', 0)
            if ng_protect_sec > 0:
                last_ng = getattr(self, '_last_ng_time', 0)
                if last_ng and (current_time - last_ng) < ng_protect_sec:
                    print(f"NG保护: 距上次NG仅{current_time - last_ng:.1f}s < {ng_protect_sec}s，抑制本次NG ({reason})")
                    self._discard_empty_cycle()
                    return False
            self._last_ng_time = current_time
        
        events_config = self.project_config.get('events_config', [])
        
        # 支持数字ID和字符串ID（如 1, 2 或 'event_1', 'event_2'）
        event = None
        for e in events_config:
            eid = e.get('id')
            # 匹配数字ID或字符串ID
            if eid == event_id or str(eid) == str(event_id):
                event = e
                break
            # 也支持 event_1 格式匹配 id=1
            if isinstance(event_id, str) and event_id.startswith('event_'):
                try:
                    num_id = int(event_id.split('_')[1])
                    if eid == num_id:
                        event = e
                        break
                except:
                    pass
        
        if not event:
            print(f"事件未找到: {event_id}")
            return False
        
        if settle_dedup:
            import traceback as _tb
            caller = _tb.extract_stack(limit=4)
            caller_info = ' <- '.join(f"{f.name}:{f.lineno}" for f in caller[:-1])
            print(f"触发事件: {event.get('name', event_id)} - {reason} "
                  f"[cycle_id={self.current_cycle_id}, caller={caller_info}]")
        else:
            print(f"触发事件: {event.get('name', event_id)} - {reason}")
        
        # 判断是否为合格事件（事件ID为1或者名称包含"合格"）
        current_event_id = event.get('id')
        is_good = current_event_id == 1
        
        # Record cycle time for both OK and NG cycles
        if self.cycle_start_time is not None:
            cycle_time = time.time() - self.cycle_start_time
            if current_event_id == 1:
                self.cycle_times.append(cycle_time)
                if len(self.cycle_times) > 100:
                    self.cycle_times = self.cycle_times[-100:]
                print(f"  周期时间(OK): {cycle_time:.2f}s, 平均: {sum(self.cycle_times)/len(self.cycle_times):.2f}s")
            else:
                self.ng_cycle_times.append(cycle_time)
                if len(self.ng_cycle_times) > 100:
                    self.ng_cycle_times = self.ng_cycle_times[-100:]
                print(f"  周期时间(NG): {cycle_time:.2f}s")
        
        had_workpiece = False
        if self._mes_hook:
            had_workpiece = (self.channel_id in self._mes_hook._inspecting_workpiece
                             or self.channel_id in self._mes_hook._pending_workpiece)

        # 结束当前周期并记录到数据库
        self.end_cycle(
            is_good=is_good,
            event_id=current_event_id,
            event_name=event.get('name', ''),
            reason=reason
        )
        
        # 重置周期开始时间（无论是合格还是NG，都重置）
        self.cycle_start_time = None
        
        # NG步骤 计数逻辑（仅在顺序模式或基于顺序的自定义模式中）
        # 只在检测到漏做（缺少步骤）时计数，跳步本质上也是缺少步骤导致的
        if current_event_id == 2 and 'NG步骤' in self.counters:
            logic_mode = self.project_config.get('logic_mode', 'detection') if self.project_config else 'detection'
            pipeline_config = self.project_config.get('pipeline_config', {}) if self.project_config else {}
            custom_based_on = pipeline_config.get('custom_based_on')
            
            # 仅在顺序模式或基于顺序的自定义模式中计数
            is_sequential_mode = (logic_mode == 'sequential' or 
                                  (logic_mode == 'custom' and custom_based_on == 'sequential'))
            
            if is_sequential_mode:
                # 只在缺少步骤时计数
                is_missing_step = '缺少' in reason or '周期不完整' in reason
                
                if is_missing_step:
                    import re
                    # 尝试从原因中提取缺少的步骤列表，按数量计入
                    match = re.search(r"缺少[：:]\s*\[([^\]]+)\]", reason)
                    if match:
                        missing_steps = match.group(1).split(',')
                        ng_step_count = len([s.strip() for s in missing_steps if s.strip()])
                    else:
                        ng_step_count = 1  # 默认 +1
                    
                    self.counters['NG步骤'] += ng_step_count
                    print(f"  NG步骤计数 += {ng_step_count} => {self.counters['NG步骤']} (原因: {reason})")
                    self._persist_counters()
        
        # NG TOP3: count unique cycles per step (each step counted at most once per NG cycle)
        if current_event_id == 2 and reason:
            import re
            involved = set()
            m_miss = re.search(r'缺少[：:]\s*\[?([^\]]+)\]?', reason)
            if m_miss:
                for s in re.split(r'[,，]', m_miss.group(1)):
                    n = s.strip().strip("'\" ")
                    if n:
                        involved.add(n)
            if '重复' in reason:
                m_dup = re.search(r'重复步骤[：:]\s*\[?([^\]]+)\]?', reason)
                if m_dup:
                    for s in re.split(r'[,，]', m_dup.group(1)):
                        n = s.strip().strip("'\" ")
                        if n:
                            involved.add(n)
            if '顺序错误' in reason:
                m_ord = re.search(r'期望\[(.+?)\].*实际\[(.+?)\]', reason)
                if m_ord:
                    if m_ord.group(1).strip():
                        involved.add(m_ord.group(1).strip())
                    if m_ord.group(2).strip():
                        involved.add(m_ord.group(2).strip())
            if '缺件' in reason:
                m_lack = re.search(r'缺件[：:]\s*\{?([^}]+)\}?', reason)
                if m_lack:
                    for s in re.split(r'[,，]', m_lack.group(1)):
                        n = s.strip().strip("'\" ")
                        if n:
                            involved.add(n)
            if not involved:
                steps_config = self.project_config.get('steps_config', []) if self.project_config else []
                expected = set(s.get('label') for s in steps_config if s.get('label') and not s.get('is_backup'))
                actual = set(self.current_cycle_steps)
                missing = expected - actual
                if missing:
                    involved = missing
                elif '顺序' in reason and self.current_cycle_steps:
                    involved = set(self.current_cycle_steps)
            for step_name in involved:
                self.ng_step_cycle_counts[step_name] = self.ng_step_cycle_counts.get(step_name, 0) + 1
        
        # 执行计数器动作（支持 delta 和 value 两种字段名）
        actions = event.get('actions', [])
        counters_changed = False
        for action in actions:
            counter_name = action.get('counter_name', '')
            value = action.get('delta', action.get('value', 1))
            if counter_name in self.counters:
                self.counters[counter_name] += value
                counters_changed = True
                print(f"  计数器 {counter_name} += {value} => {self.counters[counter_name]}")
        if counters_changed:
            self._persist_counters()
        
        # 记录事件
        self._event_seq += 1
        self.events_log.append({
            'seq': self._event_seq,
            'event_id': str(event.get('id', event_id)),
            'event_name': event.get('name', ''),
            'reason': reason,
            'timestamp': time.time(),
            'show_notification': event.get('show_notification', False),
            'toast_id': event.get('toast_id', 'ok' if event.get('id') == 1 else 'ng' if event.get('id') == 2 else 'ok'),
            'had_workpiece': had_workpiece,
        })
        
        # 触发报警器（如果已配置）— 按通道路由到对应工位的指示灯
        try:
            from backend.api.alarm import alarm_router
            event_type = f'event{current_event_id}'
            alarm_router.trigger_alarm(event_type, channel_id=self.channel_id)
        except Exception as e:
            print(f"触发报警失败: {e}")
        
        return True
    
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
    
    def _detect_only(self, frame: np.ndarray) -> list:
        """只执行检测，返回检测结果（不绘制检测框）- 带超时保护"""
        detections = []
        t_func_start = time.time()
        
        try:
            device = self.current_device_info.get('device', 'cpu') if self.current_device_info else 'cpu'
            
            from concurrent.futures import TimeoutError as FuturesTimeoutError
            
            _half = self.use_half and device.startswith('cuda') and self._is_native_pytorch
            _frame = frame if frame.flags['C_CONTIGUOUS'] else np.ascontiguousarray(frame)
            
            def run_inference():
                t_predict_start = time.time()
                result = list(self.model.predict(
                    _frame, 
                    conf=self.conf_threshold, 
                    iou=self.iou_threshold, 
                    imgsz=self._model_imgsz, 
                    verbose=False, 
                    device=device,
                    stream=True,
                    half=_half
                ))
                t_predict_end = time.time()
                predict_time = (t_predict_end - t_predict_start) * 1000
                if predict_time > 150:
                    debug_log(f"model.predict内部耗时: {predict_time:.1f}ms", "DETECT")
                return result
            
            t_pool_start = time.time()
            executor = self._get_inference_executor()
            future = executor.submit(run_inference)
            try:
                t_wait_start = time.time()
                results = future.result(timeout=self._inference_timeout)
                t_wait_end = time.time()
                wait_time = (t_wait_end - t_wait_start) * 1000
                if wait_time > 200:
                    debug_log(f"future.result等待耗时: {wait_time:.1f}ms", "DETECT")
                self._inference_timeout_count = 0
                self._consecutive_detect_errors = 0
                self._last_successful_inference = time.time()
            except FuturesTimeoutError:
                self._inference_timeout_count += 1
                debug_log(f"!!! 推理超时 ({self._inference_timeout}秒)，连续超时次数: {self._inference_timeout_count}", "DETECT")
                print(f"[警告] 推理超时 ({self._inference_timeout}秒)，连续超时次数: {self._inference_timeout_count}")
                
                if self._inference_timeout_count >= self._max_consecutive_timeouts:
                    debug_log(f"!!! 连续 {self._max_consecutive_timeouts} 次推理超时，尝试重置 GPU...", "DETECT")
                    print(f"[错误] 连续 {self._max_consecutive_timeouts} 次推理超时，尝试重置 GPU...")
                    self._shutdown_inference_executor()
                    self._emergency_gpu_reset()
                    self._inference_timeout_count = 0
                
                return []
            finally:
                del future
            t_pool_end = time.time()
            pool_time = (t_pool_end - t_pool_start) * 1000
            if pool_time > 300:
                debug_log(f"推理总耗时: {pool_time:.1f}ms", "DETECT")
            
            h, w = frame.shape[:2]
            
            for result in results:
                boxes = result.boxes
                if boxes is None:
                    continue
                
                # 获取启用的步骤标签（用于过滤禁用的步骤）
                enabled_labels = set()
                if self.project_config:
                    for step in self.project_config.get('steps_config', []):
                        if step.get('enabled', True):
                            step_label = step.get('label', '')
                            if step_label:
                                enabled_labels.add(step_label)
                
                for box in boxes:
                    x1, y1, x2, y2 = map(int, box.xyxy[0].cpu().numpy())
                    confidence = float(box.conf[0].cpu().numpy())
                    class_id = int(box.cls[0].cpu().numpy())
                    
                    # 获取类别名
                    if hasattr(self.model, 'names') and class_id in self.model.names:
                        class_name = self.model.names[class_id]
                    else:
                        class_name = f"class_{class_id}"
                    
                    # 跳过禁用的步骤（从推理层面就忽略，不参与任何逻辑）
                    if enabled_labels and class_name not in enabled_labels:
                        continue
                    
                    # 应用步骤特定的置信度阈值（低于阈值的检测不显示也不参与任何逻辑）
                    if self.step_conf_thresholds:
                        step_threshold = self.step_conf_thresholds.get(class_name)
                        if step_threshold is not None and confidence < step_threshold:
                            continue
                    
                    # 记录检测结果（归一化坐标）
                    det = {
                        'x': float(x1 / w),
                        'y': float(y1 / h),
                        'w': float((x2 - x1) / w),
                        'h': float((y2 - y1) / h),
                        'confidence': confidence,
                        'class_id': class_id,
                        'label': class_name
                    }
                    if class_name in self.step_display_names:
                        det['display_name'] = self.step_display_names[class_name]
                    if class_name in self.step_backup_map:
                        det['hidden'] = True
                        det['backup_for'] = self.step_backup_map[class_name]
                    detections.append(det)
        except Exception as e:
            self._consecutive_detect_errors = getattr(self, '_consecutive_detect_errors', 0) + 1
            if self._consecutive_detect_errors <= 3:
                print(f"检测错误: {e}")
                import traceback
                traceback.print_exc()
            elif self._consecutive_detect_errors == 4:
                print(f"[警告] 检测持续报错，后续相同错误将被抑制 (已连续 {self._consecutive_detect_errors} 次)")
            if self._consecutive_detect_errors > 2:
                time.sleep(0.5)
        
        return self._apply_rod_filters(detections)
    
    def _detect_and_track(self, frame: np.ndarray) -> list:
        """Execute model.track() — works for both detect and segment models.
        Returns detections with track_id.  Segment models also get 'mask' (polygon)."""
        detections = []
        try:
            device = self.current_device_info.get('device', 'cpu') if self.current_device_info else 'cpu'
            from concurrent.futures import TimeoutError as FuturesTimeoutError
            
            _tracker_cfg = self._custom_tracker_yaml or "bytetrack.yaml"
            _half = self.use_half and device.startswith('cuda') and self._is_native_pytorch
            _frame = frame if frame.flags['C_CONTIGUOUS'] else np.ascontiguousarray(frame)
            def run_tracking():
                return list(self.model.track(
                    _frame, conf=self.conf_threshold, iou=self.iou_threshold,
                    imgsz=self._model_imgsz, verbose=False, device=device,
                    stream=True, persist=True, tracker=_tracker_cfg,
                    half=_half
                ))
            
            executor = self._get_inference_executor()
            future = executor.submit(run_tracking)
            try:
                results = future.result(timeout=self._inference_timeout)
                self._inference_timeout_count = 0
                self._consecutive_detect_errors = 0
                self._last_successful_inference = time.time()
            except FuturesTimeoutError:
                self._inference_timeout_count += 1
                if self._inference_timeout_count >= self._max_consecutive_timeouts:
                    self._shutdown_inference_executor()
                    self._emergency_gpu_reset()
                    self._inference_timeout_count = 0
                return []
            finally:
                del future
            
            enabled_labels = self._get_enabled_labels()
            is_seg = (getattr(self, 'model_task', 'detect') == 'segment')
            h, w = frame.shape[:2]
            
            for result in results:
                boxes = result.boxes
                if boxes is None:
                    continue
                has_track_ids = boxes.id is not None
                has_masks = is_seg and result.masks is not None
                
                for i, box in enumerate(boxes):
                    x1, y1, x2, y2 = map(int, box.xyxy[0].cpu().numpy())
                    confidence = float(box.conf[0].cpu().numpy())
                    class_id = int(box.cls[0].cpu().numpy())
                    track_id = int(boxes.id[i].cpu().numpy()) if has_track_ids else -1
                    class_name = self.model.names[class_id] if hasattr(self.model, 'names') and class_id in self.model.names else f"class_{class_id}"
                    
                    if enabled_labels and class_name not in enabled_labels:
                        continue
                    if self.step_conf_thresholds:
                        thr = self.step_conf_thresholds.get(class_name)
                        if thr is not None and confidence < thr:
                            continue
                    
                    det = {
                        'x': float(x1 / w), 'y': float(y1 / h),
                        'w': float((x2 - x1) / w), 'h': float((y2 - y1) / h),
                        'confidence': confidence, 'class_id': class_id,
                        'label': class_name, 'track_id': track_id
                    }
                    if has_masks:
                        try:
                            mask_xy = result.masks.xyn[i]
                            det['mask'] = mask_xy.tolist()
                        except Exception:
                            pass
                    if class_name in self.step_display_names:
                        det['display_name'] = self.step_display_names[class_name]
                    detections.append(det)
        except Exception as e:
            self._consecutive_detect_errors = getattr(self, '_consecutive_detect_errors', 0) + 1
            if self._consecutive_detect_errors <= 3:
                print(f"tracking error: {e}")
                import traceback; traceback.print_exc()
            elif self._consecutive_detect_errors == 4:
                print(f"[警告] 跟踪持续报错，后续相同错误将被抑制 (已连续 {self._consecutive_detect_errors} 次)")
            if self._consecutive_detect_errors > 2:
                time.sleep(0.5)
        return self._apply_rod_filters(detections)
    
    def _detect_segment(self, frame: np.ndarray) -> list:
        """Segmentation predict (no tracking) — for seg models in non-tracking logic modes."""
        detections = []
        try:
            device = self.current_device_info.get('device', 'cpu') if self.current_device_info else 'cpu'
            from concurrent.futures import TimeoutError as FuturesTimeoutError
            _half = self.use_half and device.startswith('cuda') and self._is_native_pytorch
            _frame = frame if frame.flags['C_CONTIGUOUS'] else np.ascontiguousarray(frame)
            
            def run_inference():
                return list(self.model.predict(
                    _frame, conf=self.conf_threshold, iou=self.iou_threshold,
                    imgsz=self._model_imgsz, verbose=False, device=device, stream=True,
                    half=_half
                ))
            
            executor = self._get_inference_executor()
            future = executor.submit(run_inference)
            try:
                results = future.result(timeout=self._inference_timeout)
                self._inference_timeout_count = 0
                self._last_successful_inference = time.time()
            except FuturesTimeoutError:
                self._inference_timeout_count += 1
                if self._inference_timeout_count >= self._max_consecutive_timeouts:
                    self._shutdown_inference_executor()
                    self._emergency_gpu_reset()
                    self._inference_timeout_count = 0
                return []
            finally:
                del future
            
            enabled_labels = self._get_enabled_labels()
            h, w = frame.shape[:2]
            
            for result in results:
                boxes = result.boxes
                if boxes is None:
                    continue
                has_masks = result.masks is not None
                
                for i, box in enumerate(boxes):
                    x1, y1, x2, y2 = map(int, box.xyxy[0].cpu().numpy())
                    confidence = float(box.conf[0].cpu().numpy())
                    class_id = int(box.cls[0].cpu().numpy())
                    class_name = self.model.names[class_id] if hasattr(self.model, 'names') and class_id in self.model.names else f"class_{class_id}"
                    
                    if enabled_labels and class_name not in enabled_labels:
                        continue
                    if self.step_conf_thresholds:
                        thr = self.step_conf_thresholds.get(class_name)
                        if thr is not None and confidence < thr:
                            continue
                    
                    det = {
                        'x': float(x1 / w), 'y': float(y1 / h),
                        'w': float((x2 - x1) / w), 'h': float((y2 - y1) / h),
                        'confidence': confidence, 'class_id': class_id, 'label': class_name
                    }
                    if has_masks:
                        try:
                            det['mask'] = result.masks.xyn[i].tolist()
                        except Exception:
                            pass
                    if class_name in self.step_display_names:
                        det['display_name'] = self.step_display_names[class_name]
                    if class_name in self.step_backup_map:
                        det['hidden'] = True
                        det['backup_for'] = self.step_backup_map[class_name]
                    detections.append(det)
        except Exception as e:
            print(f"segment error: {e}")
            import traceback; traceback.print_exc()
        return self._apply_rod_filters(detections)
    
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
    
    def _inference_loop(self):
        """
        独立推理线程 - 持续对最新帧进行推理
        包含：推理 → 帧计数验证 → 步骤判断 → 事件触发
        """
        debug_log("========== 推理线程开始 ==========", "INFERENCE")
        print("[推理线程] 开始运行")
        last_frame_id = None
        frame_count = 0  # 帧计数器，用于周期性缓存清理
        last_cleanup_time = time.time()  # 上次清理时间
        cleanup_interval = 60.0  # 每60秒执行一次缓存清理
        last_gpu_cleanup_time = time.time()  # 上次 GPU 显存清理时间
        gpu_cleanup_interval = 600.0  # 每10分钟执行一次 GPU 深度清理
        last_log_time = time.time()  # 上次日志时间
        log_interval = 10.0  # 每10秒打印一次状态
        last_debug_time = time.time()  # 上次调试日志时间
        
        while self._inference_running and self.is_detecting:
            try:
                loop_start = time.time()
                
                # 更新心跳时间
                self._last_inference_heartbeat = time.time()
                
                # 每5秒打印一次详细调试状态
                if loop_start - last_debug_time > 5.0:
                    debug_log(f"帧数={frame_count}, 延迟={self.latency}ms, running={self._inference_running}, detecting={self.is_detecting}", "INFERENCE")
                    last_debug_time = loop_start
                
                # 定期打印状态（诊断用）
                if loop_start - last_log_time > log_interval:
                    print(f"[推理线程诊断] 帧数={frame_count}, 延迟={self.latency}ms, 运行中...")
                    last_log_time = loop_start
                
                # 周期性缓存清理（每60秒执行一次）
                current_time = time.time()
                if current_time - last_cleanup_time > cleanup_interval:
                    debug_log("开始周期性缓存清理...", "INFERENCE")
                    self._periodic_cache_cleanup()
                    debug_log("保存计数器快照...", "INFERENCE")
                    self._save_counters_snapshot()
                    last_cleanup_time = current_time
                    debug_log("缓存清理完成", "INFERENCE")
                
                # GPU 显存深度清理（每10分钟执行一次）
                if current_time - last_gpu_cleanup_time > gpu_cleanup_interval:
                    self._gpu_deep_cleanup()
                    last_gpu_cleanup_time = current_time
                
                # 获取最新帧
                # v2.7.14: frame = 原图小帧 (喂模型), display_small = 显示小帧 (stats 截图/画框)
                t1 = time.time()
                with self._inference_frame_lock:
                    frame = self._latest_frame_for_inference
                    display_small = self._latest_display_small_for_stats
                    frame_id = id(frame) if frame is not None else None
                t2 = time.time()
                
                # 如果获取帧耗时超过100ms，记录警告
                if (t2 - t1) > 0.1:
                    debug_log(f"!!! 获取帧锁耗时: {(t2-t1)*1000:.1f}ms", "INFERENCE")
                
                # 如果没有新帧，短暂等待
                if frame is None or frame_id == last_frame_id:
                    time.sleep(0.001)  # 1ms
                    continue
                
                last_frame_id = frame_id
                # No copy needed — capture thread creates a new array each
                # iteration and never mutates the old one after publishing.
                # original_frame 沿用历史命名, 这里指向 "显示坐标系下的缩小帧"
                # —— 下游 _update_*_stats 会用它生成步骤截图, 和前端看到的画面一致
                original_frame = display_small if display_small is not None else frame
                frame_count += 1

                # v2.7.13: 推理 FPS 统计 (每秒更新一次)
                # 跟踪/事件帧数阈值要按这个 FPS 换算, 不能用 fps_actual
                self._fps_inference_counter += 1
                if loop_start - self._fps_inference_time >= 1.0:
                    self.fps_inference = self._fps_inference_counter
                    self._fps_inference_counter = 0
                    self._fps_inference_time = loop_start
                
                # Determine task_type + logic_mode for this frame
                _task_type = self.project_config.get('task_type', 'detection') if self.project_config else 'detection'
                _logic_mode = self.project_config.get('logic_mode', 'sequential') if self.project_config else 'sequential'
                _is_tracking = (_logic_mode == 'tracking')
                _is_seg = (_task_type == 'segmentation')
                
                # 执行推理 — 4 combinations (输入 frame = 原图小帧, 输出坐标在原图坐标系)
                t3 = time.time()
                if _is_tracking:
                    detections = self._detect_and_track(frame)
                elif _is_seg:
                    detections = self._detect_segment(frame)
                else:
                    detections = self._detect_only(frame)
                t4 = time.time()
                detect_time = (t4 - t3) * 1000
                
                if detect_time > 200:
                    debug_log(f"!!! 推理耗时: {detect_time:.1f}ms, 检测数={len(detections) if detections else 0}", "INFERENCE")
                
                # v2.7.14: 把 detections 从 "原图坐标系" 映射到 "显示坐标系"
                # 让下游 ROI / 容器 / stats / 前端画框全部工作在显示坐标系, 零改动
                if detections:
                    detections = self._map_detections_original_to_display(detections)
                
                # 更新统计 (original_frame 已是 display_small, 与 detections 坐标系一致)
                t5 = time.time()
                if _is_tracking:
                    self._update_tracking_stats(detections, original_frame)
                else:
                    self._update_step_stats(detections, original_frame)
                t6 = time.time()
                update_time = (t6 - t5) * 1000
                
                if update_time > 100:
                    debug_log(f"!!! 步骤统计耗时: {update_time:.1f}ms", "INFERENCE")
                
                self.latency = int((time.time() - t3) * 1000)
                
                if _is_tracking:
                    confirmed = detections
                    for det in confirmed:
                        tid = det.get('track_id', -1)
                        if tid in self._tracking_display_map:
                            det['display_id'] = self._tracking_display_map[tid]
                else:
                    confirmed = self._get_confirmed_detections(detections)
                
                # 更新检测结果（供前端获取，使用过滤后的结果）
                t7 = time.time()
                with self.detection_lock:
                    self.current_detections = confirmed  # 使用 confirmed 而不是 detections
                t8 = time.time()
                
                # 如果获取检测锁耗时超过100ms，记录警告
                if (t8 - t7) > 0.1:
                    debug_log(f"!!! 检测结果锁耗时: {(t8-t7)*1000:.1f}ms", "INFERENCE")
                
                # 同时更新 _confirmed_detections（供捕获线程使用）
                with self._confirmed_detections_lock:
                    self._confirmed_detections = confirmed
                
                # 推理节流：每帧至少 5ms 间隔，防止推理线程吃满 CPU
                loop_elapsed = time.time() - loop_start
                min_inference_interval = 0.005
                if loop_elapsed < min_inference_interval:
                    time.sleep(min_inference_interval - loop_elapsed)
                    
            except Exception as e:
                debug_log(f"!!! 推理线程错误: {e}", "INFERENCE")
                print(f"[推理线程] 错误: {e}")
                import traceback
                traceback.print_exc()
                time.sleep(0.01)
        
        debug_log("========== 推理线程结束 ==========", "INFERENCE")
        print("[推理线程] 结束运行")
    
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
    
    def start_camera(self, device_index: int = 0, width: int = 1280, height: int = 720, fps: int = 60):
        """启动摄像头"""
        self.stop(release_model=False)
        
        # 等待一小段时间确保之前的资源已释放
        time.sleep(0.2)
        
        # 尝试打开摄像头（支持重试）
        max_retries = 3
        for attempt in range(max_retries):
            # Windows 上使用 DirectShow，Linux 上使用 V4L2
            import platform
            if platform.system() == "Windows":
                self.capture = cv2.VideoCapture(device_index, cv2.CAP_DSHOW)
            else:
                self.capture = cv2.VideoCapture(device_index)
            
            if self.capture.isOpened():
                break
            
            if attempt < max_retries - 1:
                print(f"[Camera] 打开摄像头失败，重试 {attempt + 2}/{max_retries}...")
                time.sleep(0.5)
        
        if not self.capture.isOpened():
            raise Exception(f"无法打开摄像头 {device_index}，请检查设备是否被其他程序占用")
        
        fourcc_mjpg = cv2.VideoWriter_fourcc('M', 'J', 'P', 'G')
        
        def _get_fourcc_str(cap):
            fc = int(cap.get(cv2.CAP_PROP_FOURCC))
            return "".join([chr((fc >> (8 * i)) & 0xFF) for i in range(4)])

        # v2.7.15 (A+B): _bench_fps 提到外层, 所有路径共享, 且打开后立即 bench 一次,
        # 避免"DirectShow 谎报 MJPG 但实际走 YUYV 10fps"的坑
        def _bench_fps(cap, n=10, timeout=5.0):
            """快速实测帧率，带超时防止慢摄像头阻塞过久"""
            try:
                cap.read()
                t0 = time.time()
                ok = 0
                for _ in range(n):
                    if time.time() - t0 > timeout:
                        break
                    if cap.read()[0]:
                        ok += 1
                elapsed = max(time.time() - t0, 0.001)
                return ok / elapsed
            except Exception:
                return 0

        # Strategy 1: Set FOURCC before resolution (standard approach)
        self.capture.set(cv2.CAP_PROP_FOURCC, fourcc_mjpg)
        self.capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.capture.set(cv2.CAP_PROP_FPS, fps)
        # v2.7.15 (B): 默认关小缓冲区, 减少 bench 偏差 + 降采集延迟
        try:
            self.capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass

        cc_str = _get_fourcc_str(self.capture)

        # v2.7.15 (A): 无论 FOURCC 报告如何, 都实测一次真实帧率
        # 阈值 = max(5, fps*0.6), 低于阈值就强制进入后端选优
        bench_fps_threshold = max(5.0, fps * 0.6)
        initial_bench_fps = _bench_fps(self.capture)
        print(f"[Camera] 首次实测: {cc_str} @ {initial_bench_fps:.0f}fps (阈值 {bench_fps_threshold:.0f}fps)")

        # Strategy 2: 格式非 MJPG 或 实测 FPS 低于阈值, 实测对比各后端选最快
        need_backend_probe = (cc_str != 'MJPG') or (initial_bench_fps < bench_fps_threshold)
        if need_backend_probe and platform.system() == "Windows":
            dshow_fps = initial_bench_fps
            print(f"[Camera] DirectShow({cc_str}) 采用首次实测 {dshow_fps:.0f}fps")

            # 先释放 DirectShow 再测 MSMF（某些摄像头不支持同时被两个后端打开）
            self.capture.release()
            self.capture = None
            time.sleep(0.3)

            msmf_cap = cv2.VideoCapture(device_index, cv2.CAP_MSMF)
            msmf_fps = 0
            if msmf_cap.isOpened():
                msmf_cap.set(cv2.CAP_PROP_FOURCC, fourcc_mjpg)
                msmf_cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
                msmf_cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
                msmf_cap.set(cv2.CAP_PROP_FPS, fps)
                try:
                    msmf_cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                except Exception:
                    pass
                msmf_cc = _get_fourcc_str(msmf_cap)
                msmf_fps = _bench_fps(msmf_cap)
                print(f"[Camera] MSMF({msmf_cc}) 实测 {msmf_fps:.0f}fps")
            else:
                print(f"[Camera] MSMF 无法打开摄像头 {device_index}")

            if msmf_fps > dshow_fps:
                self.capture = msmf_cap
                cc_str = _get_fourcc_str(self.capture)
                print(f"[Camera] 选择 MSMF 后端 ({msmf_fps:.0f}fps > DirectShow {dshow_fps:.0f}fps)")
            else:
                if msmf_cap.isOpened():
                    msmf_cap.release()
                # 重新打开 DirectShow
                self.capture = cv2.VideoCapture(device_index, cv2.CAP_DSHOW)
                self.capture.set(cv2.CAP_PROP_FOURCC, fourcc_mjpg)
                self.capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
                self.capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
                self.capture.set(cv2.CAP_PROP_FPS, fps)
                try:
                    self.capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                except Exception:
                    pass
                cc_str = _get_fourcc_str(self.capture)
                print(f"[Camera] 保留 DirectShow 后端 ({dshow_fps:.0f}fps >= MSMF {msmf_fps:.0f}fps)")

            # Strategy 4: 帧率极低时尝试 CAP_ANY 和降低缓冲区
            best_fps = max(dshow_fps, msmf_fps)
            if best_fps < 5:
                print(f"[Camera] ⚠ 帧率极低({best_fps:.0f}fps)，尝试 CAP_ANY 后端...")
                any_cap = cv2.VideoCapture(device_index)
                if any_cap.isOpened():
                    any_cap.set(cv2.CAP_PROP_FOURCC, fourcc_mjpg)
                    any_cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
                    any_cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
                    any_cap.set(cv2.CAP_PROP_FPS, fps)
                    try:
                        any_cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    except Exception:
                        pass
                    any_cc = _get_fourcc_str(any_cap)
                    any_fps = _bench_fps(any_cap)
                    print(f"[Camera] CAP_ANY({any_cc}) 实测 {any_fps:.0f}fps")
                    if any_fps > best_fps:
                        self.capture.release()
                        self.capture = any_cap
                        cc_str = any_cc
                        best_fps = any_fps
                        print(f"[Camera] 选择 CAP_ANY 后端 ({any_fps:.0f}fps)")
                    else:
                        any_cap.release()

                # Strategy 5: 降低分辨率减少带宽需求
                if best_fps < 5 and (width > 640 or height > 480):
                    print(f"[Camera] ⚠ 尝试降低分辨率到 640x480...")
                    self.capture.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                    self.capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                    lowres_fps = _bench_fps(self.capture)
                    print(f"[Camera] 640x480 实测 {lowres_fps:.0f}fps")
                    if lowres_fps > best_fps * 1.5:
                        print(f"[Camera] 使用低分辨率 ({lowres_fps:.0f}fps > {best_fps:.0f}fps)")
                        best_fps = lowres_fps
                    else:
                        self.capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
                        self.capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
                        print(f"[Camera] 低分辨率无改善，恢复 {width}x{height}")

                # 设置缓冲区大小为1减少延迟
                try:
                    self.capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                except Exception:
                    pass
        
        # Strategy 3: If still not MJPG on Linux, try without explicit backend
        if cc_str != 'MJPG' and platform.system() != "Windows":
            print(f"[Camera] V4L2 返回 {cc_str}，尝试重新打开...")
            self.capture.release()
            self.capture = cv2.VideoCapture(device_index, cv2.CAP_V4L2)
            if self.capture.isOpened():
                self.capture.set(cv2.CAP_PROP_FOURCC, fourcc_mjpg)
                self.capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
                self.capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
                self.capture.set(cv2.CAP_PROP_FPS, fps)
                cc_str = _get_fourcc_str(self.capture)
        
        actual_fps = self.capture.get(cv2.CAP_PROP_FPS)
        actual_w = int(self.capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(self.capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        print(f"[Camera] Capture format: {cc_str}, FPS: {actual_fps}, requested: {width}x{height}, actual: {actual_w}x{actual_h}")
        if cc_str != 'MJPG':
            print(f"[Camera] ⚠ 当前格式 {cc_str} (未压缩)，高分辨率下帧率通常只有 5-10fps")
            print(f"[Camera]   原因: 大多数 USB 摄像头在 {cc_str} 模式下硬件吞吐率有限")
            print(f"[Camera]   建议: 1) 确认摄像头支持 MJPG  2) 降低分辨率  3) 更换支持 MJPG 的摄像头")
        
        self.source_type = 'camera'
        self._camera_backend = int(self.capture.get(cv2.CAP_PROP_BACKEND)) if hasattr(cv2, 'CAP_PROP_BACKEND') else None
        self.camera_index = device_index
        self.width = width
        self.height = height
        self.fps = fps
        self.is_running = True
        
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        
        return True
    
    def start_rtsp(self, url: str, fps: int = 25):
        """启动 RTSP 网络视频流（NVR / IP Camera）"""
        self.stop(release_model=False)
        time.sleep(0.2)

        import os
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
            "rtsp_transport;tcp|analyzeduration;5000000|probesize;5000000"
        )

        safe_url = url.split("@")[-1] if "@" in url else url
        print(f"[RTSP] 正在连接: {safe_url} ...")

        max_retries = 3
        for attempt in range(max_retries):
            print(f"[RTSP] 尝试 {attempt + 1}/{max_retries} ...")
            self.capture = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
            if self.capture.isOpened():
                break
            if self.capture:
                self.capture.release()
                self.capture = None
            if attempt < max_retries - 1:
                print(f"[RTSP] 连接失败，{3}秒后重试 ...")
                time.sleep(3.0)

        if self.capture is None or not self.capture.isOpened():
            raise Exception(f"无法连接 RTSP 流: {safe_url}，请检查地址/用户名/密码/网络连通性")

        self.capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        actual_w = int(self.capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(self.capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        actual_fps = self.capture.get(cv2.CAP_PROP_FPS) or fps
        fourcc = int(self.capture.get(cv2.CAP_PROP_FOURCC))
        codec = ''.join([chr((fourcc >> 8 * i) & 0xFF) for i in range(4)]) if fourcc else "未知"

        print(f"[RTSP] 已连接: {actual_w}x{actual_h}, FPS: {actual_fps}, 编码: {codec}, URL: {safe_url}")

        # 验证能否实际读取帧（RTSP 首帧可能需要等待 I 帧）
        frame_ok = False
        for i in range(30):
            ret, frame = self.capture.read()
            if ret:
                print(f"[RTSP] 验证读帧成功 (第{i+1}次尝试), 帧尺寸: {frame.shape}")
                frame_ok = True
                break
            time.sleep(0.3)

        if not frame_ok:
            self.capture.release()
            self.capture = None
            raise Exception(
                f"RTSP 已连接但无法读取视频帧 ({safe_url})。"
                f"当前编码: {codec}。"
                f"建议在 NVR 管理页面将该通道的视频编码改为 H.264"
            )

        self.source_type = 'rtsp'
        self.rtsp_url = url
        self.width = actual_w
        self.height = actual_h
        self.fps = fps
        self.is_running = True

        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        return True

    # ========== 海康设备网络SDK (HCNetSDK) ==========

    def start_hcnetsdk(self, ip: str, port: int = 8000,
                       username: str = "admin", password: str = "",
                       channel: int = 1, stream_type: int = 1,
                       fps: int = 25):
        """
        通过海康设备网络SDK连接NVR/IP摄像头。
        使用海康私有协议，比RTSP更稳定。

        Parameters
        ----------
        ip : str          设备IP地址
        port : int        SDK端口 (默认 8000)
        username : str    用户名
        password : str    密码
        channel : int     通道号 (1-based, NVR 数字通道从 startDChan 开始)
        stream_type : int 0=主码流, 1=子码流 (子码流性能更好)
        fps : int         目标帧率
        """
        if not HCNET_SDK_AVAILABLE:
            raise Exception(
                "HCNetSDK not available. "
                "Place SDK DLLs in backend/hcnetsdk/lib/"
            )

        self.stop(release_model=False)
        time.sleep(0.2)

        print(f"[HCNetSDK] connecting {ip}:{port} ch={channel} "
              f"stream={'sub' if stream_type else 'main'} ...")

        try:
            session = HCNetSession()
            session.login(ip, port, username, password)

            ch_info = session.get_channel_info()
            actual_channel = channel
            if ch_info and ch_info['ip_channels'] > 0 and channel <= ch_info['ip_channels']:
                actual_channel = ch_info['start_digital_channel'] + (channel - 1)
                print(f"[HCNetSDK] channel map: {channel} -> {actual_channel}")

            session.start_preview(
                channel=actual_channel,
                stream_type=stream_type,
                link_mode=0,  # TCP
            )

            # 等待第一帧解码
            first_frame = None
            for i in range(50):
                first_frame = session.get_frame(timeout=0.5)
                if first_frame is not None:
                    h, w = first_frame.shape[:2]
                    print(f"[HCNetSDK] first frame OK ({w}x{h}), attempt {i+1}")
                    break

            if first_frame is None:
                session.cleanup()
                raise Exception(
                    f"HCNetSDK connected but no frames. "
                    f"Check channel={channel} or switch main/sub stream."
                )

            h, w = first_frame.shape[:2]
            self.hcnet_session = session
            self.hcnet_ip = ip
            self.hcnet_port = port
            self.hcnet_username = username
            self.hcnet_password = password
            self.hcnet_channel = channel
            self.source_type = 'hcnetsdk'
            self.width = w
            self.height = h
            self.fps = fps
            self.is_running = True

            self._thread = threading.Thread(target=self._capture_loop, daemon=True)
            self._thread.start()
            print(f"[HCNetSDK] connected: {w}x{h} fps={fps}")
            return True

        except ConnectionError as e:
            raise Exception(str(e))
        except Exception as e:
            print(f"[HCNetSDK] start failed: {e}")
            import traceback; traceback.print_exc()
            raise

    def _get_hcnetsdk_frame(self):
        """Get latest decoded frame from HCNetSDK session."""
        if self.hcnet_session is None:
            return None
        try:
            return self.hcnet_session.get_frame(timeout=1.0)
        except Exception as e:
            print(f"[HCNetSDK] frame error: {e}")
            return None

    def _release_hcnet_session(self):
        """Release HCNetSDK session resources."""
        if self.hcnet_session is not None:
            try:
                self.hcnet_session.cleanup()
            except Exception as e:
                print(f"[HCNetSDK] cleanup error: {e}")
            self.hcnet_session = None

    def _reconnect_hcnetsdk(self):
        """Reconnect HCNetSDK using saved connection params."""
        if not HCNET_SDK_AVAILABLE or not self.hcnet_ip:
            raise Exception("Cannot reconnect: SDK unavailable or no params")
        session = HCNetSession()
        session.login(self.hcnet_ip, self.hcnet_port,
                      self.hcnet_username, self.hcnet_password)
        ch_info = session.get_channel_info()
        actual_channel = self.hcnet_channel
        if ch_info and ch_info['ip_channels'] > 0:
            actual_channel = ch_info['start_digital_channel'] + (self.hcnet_channel - 1)
        session.start_preview(channel=actual_channel, stream_type=1, link_mode=0)
        frame = session.get_frame(timeout=5.0)
        if frame is None:
            session.cleanup()
            raise Exception("Reconnect OK but no frames")
        self.hcnet_session = session

    def start_hikvision_camera(self, device_index: int = 0, width: int = 1280, height: int = 720, fps: int = 60):
        """启动海康工业相机（带增强调试）"""
        hik_log(f"start_hikvision_camera 调用: device_index={device_index}, {width}x{height}@{fps}fps")
        
        if not HIK_SDK_AVAILABLE:
            hik_log("SDK 不可用", "ERROR")
            raise Exception("海康 SDK 未加载，无法使用海康相机")
        
        self.stop(release_model=False)
        
        # 等待一小段时间确保之前的资源已释放
        time.sleep(0.2)
        
        try:
            # 创建相机实例
            hik_log("创建 MvCamera 实例...")
            self.hik_camera = MvCamera()
            
            # 枚举设备
            hik_log("枚举设备...")
            device_list = MV_CC_DEVICE_INFO_LIST()
            tlayer_type = MV_USB_DEVICE | MV_GIGE_DEVICE
            ret = MvCamera.MV_CC_EnumDevices(tlayer_type, device_list)
            hik_log(f"枚举结果: ret={hex(ret)}, 设备数={device_list.nDeviceNum}")
            
            if ret != 0 or device_list.nDeviceNum == 0:
                raise Exception(f"未发现海康相机设备 (ret={hex(ret)})")
            
            if device_index < 0 or device_index >= device_list.nDeviceNum:
                raise Exception(f"无效的设备索引 {device_index}，当前共 {device_list.nDeviceNum} 个设备")
            
            # 获取选定设备信息
            st_device_info = cast(device_list.pDeviceInfo[device_index], POINTER(MV_CC_DEVICE_INFO)).contents
            hik_log(f"选择设备 {device_index}, 类型={st_device_info.nTLayerType}")
            
            # 创建句柄
            hik_log("创建句柄...")
            ret = self.hik_camera.MV_CC_CreateHandle(st_device_info)
            if ret != 0:
                raise Exception(f"创建相机句柄失败，错误码: {hex(ret)}")
            hik_log("句柄创建成功")
            
            # 打开设备（独占模式）
            hik_log("打开设备...")
            ret = self.hik_camera.MV_CC_OpenDevice(MV_ACCESS_Exclusive, 0)
            if ret != 0:
                self.hik_camera.MV_CC_DestroyHandle()
                raise Exception(f"打开相机失败，错误码: {hex(ret)}")
            hik_log("设备打开成功")
            
            # 设置为连续采集模式
            hik_log("设置触发模式...")
            ret = self.hik_camera.MV_CC_SetEnumValue("TriggerMode", MV_TRIGGER_MODE_OFF)
            if ret != 0:
                hik_log(f"设置触发模式失败: {hex(ret)}，继续运行", "WARN")
            else:
                hik_log("触发模式设置成功 (连续采集)")
            
            # 获取 PayloadSize（帧数据大小）
            hik_log("获取 PayloadSize...")
            st_param = MVCC_INTVALUE()
            memset(byref(st_param), 0, sizeof(MVCC_INTVALUE))
            ret = self.hik_camera.MV_CC_GetIntValue("PayloadSize", st_param)
            if ret != 0:
                self._release_hik_camera()
                raise Exception(f"获取 PayloadSize 失败，错误码: {hex(ret)}")
            self.hik_payload_size = st_param.nCurValue
            hik_log(f"PayloadSize = {self.hik_payload_size} bytes")
            
            # 开始取流
            hik_log("开始取流...")
            ret = self.hik_camera.MV_CC_StartGrabbing()
            if ret != 0:
                self._release_hik_camera()
                raise Exception(f"开始取流失败，错误码: {hex(ret)}")
            hik_log("取流开始成功")
            
            # 预分配缓冲区
            hik_log("分配缓冲区...")
            self.hik_data_buf = (c_ubyte * self.hik_payload_size)()
            self.hik_frame_info = MV_FRAME_OUT_INFO_EX()
            memset(byref(self.hik_frame_info), 0, sizeof(MV_FRAME_OUT_INFO_EX))
            hik_log("缓冲区分配成功")
            
            # 重置帧计数相关标志
            if hasattr(self, '_hik_first_frame_logged'):
                delattr(self, '_hik_first_frame_logged')
            if hasattr(self, '_hik_frame_error_count'):
                self._hik_frame_error_count = 0
            
            # 设置属性
            self.source_type = 'hikvision'
            self.hik_device_index = device_index
            self.width = width
            self.height = height
            self.fps = fps
            self.is_running = True
            
            hik_log(f"海康相机初始化完成: 设备{device_index}", "SUCCESS")
            
            # 启动捕获线程
            self._thread = threading.Thread(target=self._capture_loop, daemon=True)
            self._thread.start()
            hik_log("捕获线程已启动")
            
            return True
            
        except Exception as e:
            hik_log(f"启动失败: {e}", "ERROR")
            import traceback
            hik_log(traceback.format_exc(), "ERROR")
            self._release_hik_camera()
            raise Exception(f"启动海康相机失败: {str(e)}")
    
    def _release_hik_camera(self):
        """释放海康相机资源"""
        if self.hik_camera:
            try:
                self.hik_camera.MV_CC_StopGrabbing()
            except:
                pass
            try:
                self.hik_camera.MV_CC_CloseDevice()
            except:
                pass
            try:
                self.hik_camera.MV_CC_DestroyHandle()
            except:
                pass
            self.hik_camera = None
            self.hik_data_buf = None
            self.hik_frame_info = None
            print("[海康相机] 已释放")
    
    def _get_hikvision_frame(self):
        """从海康相机获取一帧图像 - 带调试日志"""
        if not self.hik_camera or not HIK_SDK_AVAILABLE:
            return None
        
        try:
            # 与参考代码完全一致的帧获取方式
            stFrameInfo = MV_FRAME_OUT_INFO_EX()
            memset(byref(stFrameInfo), 0, sizeof(stFrameInfo))
            pData = (c_ubyte * (2048 * 2048 * 3))()
            
            t_grab_start = time.time()
            ret = self.hik_camera.MV_CC_GetOneFrameTimeout(byref(pData), sizeof(pData), stFrameInfo, 1000)
            t_grab_end = time.time()
            grab_time = (t_grab_end - t_grab_start) * 1000
            
            if ret != 0:
                if grab_time > 800:  # 接近超时
                    debug_log(f"!!! 帧获取超时: ret={hex(ret)}, 耗时={grab_time:.1f}ms", "HIK")
                return None
            
            if grab_time > 100:
                debug_log(f"帧获取耗时: {grab_time:.1f}ms", "HIK")
            
            frame_width = stFrameInfo.nWidth
            frame_height = stFrameInfo.nHeight
            pixel_type = stFrameInfo.enPixelType
            
            # 首帧详细信息
            if not hasattr(self, '_hik_first_frame_logged'):
                hik_log(f"首帧: {frame_width}x{frame_height}, 像素={hex(pixel_type)}, 长度={stFrameInfo.nFrameLen}", "SUCCESS")
                self._hik_first_frame_logged = True
            
            # 与参考代码 LG.PY 第941-961行完全一致的转换逻辑
            t_convert_start = time.time()
            if pixel_type == PixelType_Gvsp_RGB8_Packed:
                frame = np.frombuffer(pData, dtype=np.uint8, count=frame_width * frame_height * 3)
                frame = frame.reshape((frame_height, frame_width, 3))
            else:
                # SDK 像素转换
                stConvertParam = MV_CC_PIXEL_CONVERT_PARAM()
                memset(byref(stConvertParam), 0, sizeof(stConvertParam))
                stConvertParam.nWidth = stFrameInfo.nWidth
                stConvertParam.nHeight = stFrameInfo.nHeight
                stConvertParam.pSrcData = cast(pData, POINTER(c_ubyte))
                stConvertParam.nSrcDataLen = stFrameInfo.nFrameLen
                stConvertParam.enSrcPixelType = stFrameInfo.enPixelType
                stConvertParam.enDstPixelType = PixelType_Gvsp_RGB8_Packed
                nConvertSize = stFrameInfo.nWidth * stFrameInfo.nHeight * 3
                pConvertData = (c_ubyte * nConvertSize)()
                stConvertParam.pDstBuffer = cast(pConvertData, POINTER(c_ubyte))
                stConvertParam.nDstBufferSize = nConvertSize
                
                t_sdk_convert_start = time.time()
                ret = self.hik_camera.MV_CC_ConvertPixelType(stConvertParam)
                t_sdk_convert_end = time.time()
                sdk_convert_time = (t_sdk_convert_end - t_sdk_convert_start) * 1000
                
                if ret != 0:
                    if not hasattr(self, '_convert_err_logged'):
                        hik_log(f"SDK转换失败: {hex(ret)}", "ERROR")
                        self._convert_err_logged = True
                    return None
                
                if sdk_convert_time > 50:
                    debug_log(f"SDK像素转换耗时: {sdk_convert_time:.1f}ms", "HIK")
                
                frame = np.frombuffer(pConvertData, dtype=np.uint8, count=nConvertSize)
                frame = frame.reshape((frame_height, frame_width, 3))
                
                # 首次转换成功时记录详细信息
                if not hasattr(self, '_hik_convert_logged'):
                    hik_log(f"SDK转换成功", "SUCCESS")
                    self._hik_convert_logged = True
            
            t_convert_end = time.time()
            total_convert_time = (t_convert_end - t_convert_start) * 1000
            if total_convert_time > 100:
                debug_log(f"!!! 整体转换耗时: {total_convert_time:.1f}ms", "HIK")
            
            # 与参考代码 LG.PY 第1576-1578行一致: RGB -> BGR
            frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
            return frame
            
        except Exception as e:
            if not hasattr(self, '_hik_err_logged'):
                hik_log(f"获取帧异常: {e}", "ERROR")
                self._hik_err_logged = True
            return None
    
    def start_video(self, video_path: str, speed: float = None):
        """启动视频文件播放"""
        # 保存当前倍速设置（如果有的话）
        current_speed = self.video_speed if self.video_speed else 1.0
        
        self.stop(release_model=False)
        
        if not os.path.exists(video_path):
            raise Exception(f"视频文件不存在: {video_path}")
        
        self.capture = cv2.VideoCapture(video_path)
        if not self.capture.isOpened():
            raise Exception(f"无法打开视频文件: {video_path}")
        
        self.source_type = 'video'
        self.video_path = video_path
        self.fps = self.capture.get(cv2.CAP_PROP_FPS) or 30
        self.width = int(self.capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.height = int(self.capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        
        # 视频帧信息
        self.video_total_frames = int(self.capture.get(cv2.CAP_PROP_FRAME_COUNT))
        self.video_current_frame = 0
        self.video_ended = False
        # 使用传入的倍速，如果没有传入则保持之前的倍速
        self.video_speed = speed if speed is not None else current_speed
        print(f"视频总帧数: {self.video_total_frames}, 倍速: {self.video_speed}x")
        
        self.is_running = True
        
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        
        return True
    
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
