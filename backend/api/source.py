"""
输入源管理 API
支持 USB 摄像头、本地视频文件、本地图片、海康工业相机
集成 YOLO 模型推理
集成会话和周期记录
"""
from fastapi import APIRouter, HTTPException, UploadFile, File, Depends
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
from backend.core.config import settings
from backend.db.database import SessionLocal
from backend.models.models import DetectionSession, DetectionCycle, StepRecord, VideoClip, DataExportSetting

router = APIRouter()


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
class VideoSourceManager:
    # 配置文件路径
    CONFIG_FILE = os.path.join(os.path.dirname(__file__), '..', 'data', 'device_config.json')
    
    def __init__(self):
        self.source_type = None  # 'camera', 'video', 'image', 'hikvision'
        self.capture = None
        self.is_running = False
        self.current_frame = None
        self.frame_lock = threading.Lock()
        self.capture_lock = threading.Lock()  # 保护 capture 对象的并发访问
        self.camera_index = 0
        self.video_path = None
        self.image_path = None
        self.width = 1280
        self.height = 720
        self.fps = 30
        self._thread = None
        
        # 海康工业相机相关
        self.hik_camera = None  # MvCamera 实例
        self.hik_device_index = 0  # 海康相机设备索引
        self.hik_payload_size = 0  # 海康相机帧数据大小
        self.hik_data_buf = None  # 海康相机数据缓冲区
        self.hik_frame_info = None  # 海康相机帧信息
        
        # 帧率限制配置（用于MJPEG流）
        self.frame_limit_enabled = False  # 默认禁用节流（本地应用）
        self.target_stream_fps = 30  # 目标流帧率
        
        # 视频播放控制
        self.video_speed = 1.0  # 视频倍速
        self.video_ended = False  # 视频是否已结束
        self.video_total_frames = 0  # 视频总帧数
        self.video_current_frame = 0  # 当前帧位置
        self._progress_lock = threading.Lock()  # 防止进度设置并发调用
        self._setting_progress = False  # 正在设置进度的标志
        self._pending_progress = None  # 待处理的进度请求（记住最新值）
        
        # YOLO 模型
        self.model = None
        self.model_path = None
        self.device = 'auto'  # 推理设备: 'auto', 'cpu', 'cuda:0', 'cuda:1' 等
        self.current_device_info = None  # 当前使用的设备信息
        self.is_detecting = False
        self.current_detections = []
        self.detection_lock = threading.Lock()
        
        # 加载保存的设备配置
        self._load_device_config()
        
        # 初始化推理相关变量（必须在_load_device_config之后）
        self._init_inference_vars()
    
    def _load_device_config(self):
        """从文件加载设备配置"""
        try:
            if os.path.exists(self.CONFIG_FILE):
                import json
                with open(self.CONFIG_FILE, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    self.device = config.get('device', 'auto')
                    self.frame_limit_enabled = config.get('frame_limit_enabled', False)
                    self.target_stream_fps = config.get('target_stream_fps', 30)
                    print(f"已加载设备配置: 设备={self.device}, 帧率限制={self.frame_limit_enabled}")
        except Exception as e:
            print(f"加载设备配置失败: {e}")
    
    def _save_device_config(self):
        """保存设备配置到文件"""
        try:
            import json
            os.makedirs(os.path.dirname(self.CONFIG_FILE), exist_ok=True)
            config = {
                'device': self.device,
                'frame_limit_enabled': self.frame_limit_enabled,
                'target_stream_fps': self.target_stream_fps
            }
            with open(self.CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            print(f"设备配置已保存: {config}")
        except Exception as e:
            print(f"保存设备配置失败: {e}")
    
    def _init_inference_vars(self):
        """初始化推理相关变量（在__init__的_load_device_config之后调用）"""
        self.conf_threshold = 0.25
        self.iou_threshold = 0.45
        
        # ========== 双线程架构相关 ==========
        self._inference_thread = None  # 推理线程
        self._inference_running = False  # 推理线程运行标志
        self._latest_frame_for_inference = None  # 供推理线程使用的最新帧
        self._inference_frame_lock = threading.Lock()  # 保护推理帧的锁
        self._confirmed_detections = []  # 经过帧计数确认的检测结果
        self._confirmed_detections_lock = threading.Lock()  # 保护确认结果的锁
        
        # ========== 健康检查相关 ==========
        self._last_inference_heartbeat = time.time()  # 推理线程心跳时间
        self._last_capture_heartbeat = time.time()  # 捕获线程心跳时间
        self._health_check_interval = 5.0  # 健康检查间隔（秒）
        self._thread_timeout_threshold = 10.0  # 线程无响应阈值（秒）
        
        # ========== 推理超时保护 ==========
        self._inference_timeout = 5.0  # 单次推理超时时间（秒）
        self._inference_timeout_count = 0  # 推理超时计数
        self._max_consecutive_timeouts = 3  # 最大连续超时次数，超过后重置模型
        self._inference_executor = None  # 持久线程池（避免每帧创建新线程池导致内存泄漏）
        self._last_successful_inference = time.time()  # 最后一次成功推理的时间
        
        # ========== 卡尔曼滤波相关 ==========
        self._kalman_filters = {}  # {label: KalmanFilter} 每个目标一个滤波器
        self._kalman_enabled = True  # 是否启用卡尔曼滤波
        self._kalman_process_noise = 0.03  # 过程噪声 Q (越小越平滑，越大响应越快)
        self._kalman_measurement_noise = 0.1  # 观测噪声 R (越大越平滑)
        self._detection_history = {}  # 检测框历史 {label: last_detection}
        self._detection_missing_frames = {}  # 目标消失帧数统计 {label: count}
        self._max_missing_frames = 5  # 目标消失多少帧后移除滤波器
        
        # 统计
        self.fps_actual = 0
        self.latency = 0
        self._fps_counter = 0
        self._fps_time = time.time()
        
        # 步骤截图 {step_name: base64_image}
        self.step_screenshots = {}
        self.step_counts = {}  # {step_name: count}
        self.step_last_seen = {}  # {step_name: timestamp} 最后一次检测到的时间
        self.step_start_time = {}  # {step_name: timestamp} 步骤开始检测的时间
        self.step_time_config = {}  # {step_name: {min_duration, max_duration, max_interval}}
        self.disappear_threshold = 1.0  # 默认消失阈值秒
        
        # 项目配置
        self.project_config = None
        self.step_conf_thresholds = {}  # {step_name: threshold}
        self.step_min_frames = {}  # {step_name: min_frames} 每个步骤的最少帧数配置
        self.step_consecutive_frames = {}  # {step_name: count} 跟踪每个标签连续出现的帧数
        self.step_frame_confirmed = {}  # {step_name: bool} 标记标签是否已确认（达到最少帧数）
        
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
        self.current_cycle_steps = []  # 当前周期检测到的步骤顺序
        self.last_added_step = None  # 上一个添加到周期的步骤（用于去重判断）
        self.cycle_complete = False
        # 标记当前周期是否已经出现“最后一步”（顺序/自定义顺序模式用来防止 7 之后继续往同一轮追加 1、2、3）
        self._cycle_locked_by_last_step = False
        
        # Cycle Time 统计
        self.cycle_start_time = None  # 当前周期开始时间
        self.cycle_times = []  # 记录最近的周期时间（最多保留100个）
        self.step_detection_times = {}  # 步骤检测时间 {step_name: timestamp}
        self.step_durations = {}  # 步骤耗时 {step_name: duration_seconds}
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
        
        # 视频录制
        self.video_writer = None  # 视频录制器
        self.cycle_video_writer = None  # 周期视频录制器
        self.step_video_writers = {}  # 步骤视频录制器 {step_label: writer}
        self._step_writers_lock = threading.Lock()  # 保护 step_video_writers 的并发访问
        
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
    
    def start_session(self, project_id: int) -> dict:
        """开始新的检测会话"""
        try:
            db = self._get_db_session()
            session_uuid = str(uuid.uuid4())[:8]
            session = DetectionSession(
                session_uuid=session_uuid,
                project_id=project_id,
                start_time=datetime.now(),
                status="running"
            )
            db.add(session)
            db.commit()
            db.refresh(session)
            
            self.current_session_id = session.id
            self.current_session_uuid = session_uuid
            self.current_cycle_number = 0
            self.recording_enabled = True
            
            # 加载导出设置
            self._load_export_settings()
            
            db.close()
            print(f"检测会话已创建: {session_uuid}")
            
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
        
        try:
            db = self._get_db_session()
            session = db.query(DetectionSession).filter(
                DetectionSession.id == session_id
            ).first()
            
            if session:
                session.end_time = datetime.now()
                session.status = "completed"
                
                # 计算统计
                cycles = db.query(DetectionCycle).filter(
                    DetectionCycle.session_id == session_id
                ).all()
                
                print(f"end_session: 找到 {len(cycles)} 个周期")
                
                if cycles:
                    session.total_cycles = len(cycles)
                    session.good_cycles = len([c for c in cycles if c.is_good])
                    session.ng_cycles = session.total_cycles - session.good_cycles
                    
                    durations = [c.duration for c in cycles if c.duration]
                    if durations:
                        session.avg_cycle_time = sum(durations) / len(durations)
                        session.min_cycle_time = min(durations)
                        session.max_cycle_time = max(durations)
                
                # 保存计数器快照
                session.counters_snapshot = self.counters.copy() if self.counters else {}
                
                print(f"end_session: 保存数据 - 周期数: {session.total_cycles}, 合格: {session.good_cycles}, 不良: {session.ng_cycles}, 计数器: {session.counters_snapshot}")
                
                db.commit()
                print(f"会话已结束: {session_uuid}, 周期数: {session.total_cycles}")
            else:
                print(f"end_session: 未找到会话 ID={session_id}")
            
            db.close()
        except Exception as e:
            print(f"结束会话失败: {e}")
            import traceback
            traceback.print_exc()
        finally:
            # 停止视频录制
            self.stop_session_recording()
            self.stop_cycle_recording()
            
            self.current_session_id = None
            self.current_session_uuid = None
            self.recording_enabled = False
    
    def start_cycle(self):
        """开始新的检测周期"""
        if not self.current_session_id or not self.recording_enabled:
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
            
            cycle = DetectionCycle(
                cycle_uuid=cycle_uuid,
                session_id=self.current_session_id,
                cycle_number=self.current_cycle_number,
                start_time=now
            )
            db.add(cycle)
            db.commit()
            db.refresh(cycle)
            
            self.current_cycle_id = cycle.id
            self.current_cycle_uuid = cycle_uuid
            self.cycle_step_records = []
            self.step_order_counter = 0
            
            db.close()
            print(f"新周期开始: #{self.current_cycle_number} ({cycle_uuid})")
            
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
            
            db.close()
        except Exception as e:
            print(f"结束周期失败: {e}")
        finally:
            self.current_cycle_id = None
            self.current_cycle_uuid = None
    
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
                if last_end_time:
                    interval_to_next = start_time - last_end_time
                    # 更新数据库中的上一条步骤记录
                    last_step = db.query(StepRecord).filter(
                        StepRecord.cycle_id == self.current_cycle_id,
                        StepRecord.step_order == len(self.cycle_step_records)
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
                'end_time': end_time
            })
            
            db.close()
        except Exception as e:
            print(f"记录步骤失败: {e}")
            import traceback
            traceback.print_exc()
        
    def set_project_config(self, config: dict):
        """设置项目配置"""
        self.project_config = config
        
        # 解析步骤置信度阈值和时间配置
        self.step_conf_thresholds = {}
        self.step_time_config = {}
        self.step_min_frames = {}  # 最少帧数配置
        self.step_consecutive_frames = {}  # 重置连续帧计数
        self.step_frame_confirmed = {}  # 重置确认状态
        self.step_detection_type = {}  # 检测类型
        self.step_static_config = {}  # 静态步骤配置
        self.step_static_triggered = {}  # 静态步骤触发状态
        
        steps_config = config.get('steps_config', [])
        for step in steps_config:
            if step.get('enabled', True):
                label = step.get('label', '')
                # 前端发送的是百分比（10-100），需要转换为小数（0.1-1.0）
                threshold = step.get('threshold', 50)
                if threshold > 1:
                    threshold = threshold / 100.0  # 转换百分比为小数
                self.step_conf_thresholds[label] = threshold
                
                # 步骤时间配置
                self.step_time_config[label] = {
                    'min_duration': step.get('min_duration'),  # 最短持续时间
                    'max_duration': step.get('max_duration'),  # 最大持续时间
                    'max_interval': step.get('max_interval', 1.0)  # 去重间隔，默认1秒
                }
                
                # 最少帧数配置（默认1帧）
                min_frames = step.get('min_frames')
                self.step_min_frames[label] = min_frames if min_frames and min_frames > 0 else 1
                
                # 检测类型配置
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
        
        # 解析同时出现组配置
        pipeline_config = config.get('pipeline_config', {})
        self._simultaneous_groups = pipeline_config.get('simultaneous_groups', [])
        self._sim_group_buffers = {}
        if self._simultaneous_groups:
            print(f"同时出现组: {self._simultaneous_groups}")
        
        # 初始化计数器（确保默认计数器始终存在）
        self.counters = {}
        counters_config = config.get('counters_config', [])
        
        # 默认计数器名称列表
        DEFAULT_COUNTERS = ['总产量', '合格总数', '不良总数', 'NG步骤']
        
        # 先添加配置中的计数器
        for counter in counters_config:
            self.counters[counter.get('name', '')] = counter.get('value', 0)
        
        # 确保默认计数器存在（NG步骤 始终从0开始，不能设置默认值）
        for default_name in DEFAULT_COUNTERS:
            if default_name not in self.counters:
                # NG步骤 计数器特殊处理：始终从 0 开始
                self.counters[default_name] = 0
        
        # 重置周期状态
        self.current_cycle_steps = []
        self.last_added_step = None  # 重置上一个添加的步骤
        self.cycle_complete = False
        self.events_log = []
        self.step_start_time = {}  # 重置步骤开始时间
        
        print(f"项目配置已加载: {config.get('name', 'Unknown')}")
        print(f"步骤阈值: {self.step_conf_thresholds}")
        print(f"步骤时间配置: {self.step_time_config}")
        print(f"步骤最少帧数: {self.step_min_frames}")
        print(f"步骤检测类型: {self.step_detection_type}")
        print(f"静态步骤配置: {self.step_static_config}")
        print(f"计数器: {self.counters}")
    
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
                
                # 2. 删除模型引用
                del self.model
                self.model = None
                
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
    
    def load_model(self, model_path: str) -> bool:
        """加载 YOLO 模型"""
        try:
            from ultralytics import YOLO
            import torch
            
            # 先释放旧模型
            if self.model is not None:
                print(f"[模型加载] 释放旧模型: {self.model_path}")
                self._release_model()
            
            self.model = YOLO(model_path)
            self.model_path = model_path
            
            # 设置推理设备
            if self.device == 'auto':
                # 自动选择：优先GPU
                if torch.cuda.is_available():
                    device = 'cuda:0'
                else:
                    device = 'cpu'
            else:
                device = self.device
            
            # 将模型移动到指定设备
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
        
        while self.is_running and (self.capture is not None or self.source_type == 'hikvision'):
            try:
                loop_count += 1
                loop_start = time.time()
                
                # 每5秒打印一次详细状态
                if loop_start - last_debug_time > 5.0:
                    debug_log(f"循环#{loop_count}, 源={self.source_type}, 检测={self.is_detecting}, 运行={self.is_running}", "CAPTURE")
                    last_debug_time = loop_start
                
                # 更新捕获线程心跳
                self._last_capture_heartbeat = time.time()
                
                speed = getattr(self, 'video_speed', 1.0)
                
                # 根据输入源类型读取帧
                frame = None
                ret = False
                
                if self.source_type == 'hikvision':
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
                    original_frame = frame.copy()
                    
                    # 更新视频当前帧位置
                    if self.source_type == 'video' and self.capture is not None:
                        self.video_current_frame = int(self.capture.get(cv2.CAP_PROP_POS_FRAMES))
                    
                    # ========== 双线程架构：异步推理 ==========
                    if self.is_detecting and self.model is not None:
                        # 更新供推理线程使用的帧（非阻塞）
                        t_lock1_start = time.time()
                        with self._inference_frame_lock:
                            self._latest_frame_for_inference = original_frame.copy()
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
                    
                    # 发送原始帧（不带检测框）
                    t_lock4_start = time.time()
                    with self.frame_lock:
                        self.current_frame = original_frame
                    t_lock4_end = time.time()
                    if (t_lock4_end - t_lock4_start) > 0.1:
                        debug_log(f"!!! frame_lock 耗时: {(t_lock4_end-t_lock4_start)*1000:.1f}ms", "CAPTURE")
                    
                    # 写入视频录制队列（使用 FFmpeg 进程，不会卡死）
                    if self.is_detecting and self.recording_enabled:
                        self._enqueue_frame_for_recording(original_frame)
                    
                    # FPS 计算
                    self._fps_counter += 1
                    if time.time() - self._fps_time >= 1.0:
                        self.fps_actual = self._fps_counter
                        self._fps_counter = 0
                        self._fps_time = time.time()
                    
                else:
                    # 视频结束，停止播放（不循环）
                    if self.source_type == 'video' and self.video_path:
                        print("[Video] 视频播放完毕，已停止")
                        self.video_ended = True
                        self.is_running = False
                        # 如果正在检测，自动停止检测
                        if self.is_detecting:
                            self.stop_detection()
                        break  # 退出循环
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
                        if self.source_type == 'hikvision':
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
                            # 普通摄像头/视频：尝试重新打开
                            if self.capture is not None:
                                self.capture.release()
                            if self.source_type == 'camera':
                                self.capture = cv2.VideoCapture(self.camera_index)
                                if self.capture.isOpened():
                                    print("[捕获线程] 摄像头重新打开成功")
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
        
        print(f"自定义模式结算: 当前序列={self.current_cycle_steps}")
        
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
                    self._trigger_event(cond_event_id, f'自定义条件匹配: {cond_labels}')
                    self.current_cycle_steps = []
                    self.last_added_step = None
                    return
        
        # 没有条件匹配，回退到基础模式判定
        if custom_based_on == 'sequential':
            # 使用自定义模式独立的顺序配置
            sequence_order = pipeline_config.get('custom_sequence_order', [])
            
            if not sequence_order:
                self.current_cycle_steps = []
                self.last_added_step = None
                return
            
            # 构建期望的标签序列（只包含启用的步骤）
            expected_labels = []
            for item in sequence_order:
                step_id = item.get('step_id')
                if step_id in id_to_label and step_id in enabled_step_ids:
                    expected_labels.append(id_to_label[step_id])
            
            if not expected_labels:
                self.current_cycle_steps = []
                self.last_added_step = None
                return
            
            print(f"  期望序列({len(expected_labels)}步): {expected_labels}")
            print(f"  实际序列({len(self.current_cycle_steps)}步): {self.current_cycle_steps}")
            
            # 直接比较完整序列（支持重复标签）
            if self.current_cycle_steps == expected_labels:
                # 完全匹配
                print(f"  → 序列完全匹配 → OK")
                self._trigger_event(1, '顺序正确完成')
            elif len(self.current_cycle_steps) < len(expected_labels):
                # 序列不完整
                missing_count = len(expected_labels) - len(self.current_cycle_steps)
                print(f"  → 周期不完整，还差{missing_count}步 → NG")
                self._trigger_event(2, f'周期不完整，完成{len(self.current_cycle_steps)}/{len(expected_labels)}步')
            elif len(self.current_cycle_steps) > len(expected_labels):
                # 序列超长
                print(f"  → 序列超长 → NG")
                self._trigger_event(2, f'序列超长: {len(self.current_cycle_steps)}步 > 期望{len(expected_labels)}步')
            else:
                # 长度相同但内容不同（顺序错误）
                # 找出第一个不匹配的位置
                mismatch_idx = -1
                for i, (actual, expected) in enumerate(zip(self.current_cycle_steps, expected_labels)):
                    if actual != expected:
                        mismatch_idx = i
                        break
                if mismatch_idx >= 0:
                    print(f"  → 第{mismatch_idx+1}步顺序错误: 期望[{expected_labels[mismatch_idx]}], 实际[{self.current_cycle_steps[mismatch_idx]}] → NG")
                    self._trigger_event(2, f'第{mismatch_idx+1}步顺序错误')
                else:
                    print(f"  → 顺序错误 → NG")
                    self._trigger_event(2, '顺序错误')
        
        elif custom_based_on == 'detection':
            detection_step_ids = pipeline_config.get('custom_detection_steps', [])
            if detection_step_ids:
                detection_labels = [id_to_label.get(sid) for sid in detection_step_ids if sid in id_to_label]
            else:
                detection_labels = enabled_step_labels
            
            if detection_labels and all(label in self.current_cycle_steps for label in detection_labels):
                print(f"  → 检测完成 → OK")
                self._trigger_event(1, '检测完成')
        
        # 重置周期
        self.current_cycle_steps = []
        self.last_added_step = None
        
        # 清理 step_last_seen 中已消失的标签，防止跨周期污染
        # 对已消失但尚未被计数的步骤，先补计再删除（避免 step_counts 丢失）
        current_detected = getattr(self, '_current_detected_labels', set())
        for label in list(self.step_last_seen.keys()):
            if label not in current_detected:
                last_time = self.step_last_seen[label]
                start_time = self.step_start_time.get(label, last_time)
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
                    self.step_durations[label] = round(duration, 2)
                    print(f"  自定义周期结算补计: {label}, 耗时 {duration:.2f}s, 累计: {self.step_counts[label]}")
                
                del self.step_last_seen[label]
                if label in self.step_start_time:
                    del self.step_start_time[label]
    
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
            self.last_added_step = None
            # 周期结算后重置步骤时序状态，确保下一轮的相同步骤可被视为“新出现”
            self.step_last_seen.clear()
            self.step_start_time.clear()
            self.last_step_completed_time = None
            self._cycle_locked_by_last_step = False
            return
        
        # 获取期望的步骤标签顺序（只包含启用的步骤）
        expected_labels = []
        for item in sequence_order:
            step_id = item.get('step_id')
            if step_id in id_to_label and step_id in enabled_step_ids:
                expected_labels.append(id_to_label[step_id])
        
        if not expected_labels:
            self.current_cycle_steps = []
            self.last_added_step = None
            # 周期结算后重置步骤时序状态，确保下一轮的相同步骤可被视为“新出现”
            self.step_last_seen.clear()
            self.step_start_time.clear()
            self.last_step_completed_time = None
            self._cycle_locked_by_last_step = False
            return
        
        print(f"顺序模式结算: 期望={expected_labels}, 实际={self.current_cycle_steps}")
        
        # 检查序列长度是否超过预期（有重复步骤）
        if len(self.current_cycle_steps) > len(expected_labels):
            from collections import Counter
            step_counter = Counter(self.current_cycle_steps)
            duplicated = [s for s, cnt in step_counter.items() if cnt > 1]
            print(f"  → 序列长度({len(self.current_cycle_steps)})超过预期({len(expected_labels)})，有重复步骤 → NG")
            self._trigger_event(2, f'重复步骤: {duplicated}')
            self.current_cycle_steps = []
            self.last_added_step = None
            return
        
        # 检查是否完整（包含所有预期步骤）
        if not all(label in self.current_cycle_steps for label in expected_labels):
            missing = [l for l in expected_labels if l not in self.current_cycle_steps]
            print(f"  → 周期不完整，缺少: {missing} → NG")
            self._trigger_event(2, f'周期不完整，缺少: {missing}')
            self.current_cycle_steps = []
            self.last_added_step = None
            return
        
        # 检查顺序是否正确
        cycle_order_correct = True
        order_error_labels = []
        last_idx = -1
        prev_label = None
        for label in expected_labels:
            idx = self.current_cycle_steps.index(label)
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
        self.current_cycle_steps = []
        self.last_added_step = None
        self.last_step_completed_time = None
        self._cycle_locked_by_last_step = False
        
        # 清理 step_last_seen 中已消失的标签，防止跨周期污染
        # 对已消失但尚未被计数的步骤，先补计再删除（避免 step_counts 丢失）
        current_detected = getattr(self, '_current_detected_labels', set())
        for label in list(self.step_last_seen.keys()):
            if label not in current_detected:
                last_time = self.step_last_seen[label]
                start_time = self.step_start_time.get(label, last_time)
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
                    self.step_durations[label] = round(duration, 2)
                    print(f"  周期结算补计: {label}, 耗时 {duration:.2f}s, 累计: {self.step_counts[label]}")
                
                del self.step_last_seen[label]
                if label in self.step_start_time:
                    del self.step_start_time[label]
    
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
                             should_update_screenshot, original_frame, det_info):
        """处理单个标签的步骤逻辑：新出现判定、周期结算触发、周期记录、截图更新。
        
        从 _update_step_stats 的 for 循环体中提取，供缓冲层输出和普通标签共用。
        """
        import base64
        
        if label not in enabled_labels:
            return
        
        time_config = self.step_time_config.get(label, {})
        max_interval = time_config.get('max_interval') or 1.0
        
        if label in self.step_last_seen:
            time_since_last = current_time - self.step_last_seen[label]
            if is_seq_like:
                is_new_appearance = time_since_last > max_interval
            elif self.last_added_step is not None and self.last_added_step != label:
                is_new_appearance = True
            else:
                is_new_appearance = time_since_last > max_interval
        else:
            is_new_appearance = True
        
        logic_mode = self.project_config.get('logic_mode') if self.project_config else 'detection'
        pipeline_config = self.project_config.get('pipeline_config', {}) if self.project_config else {}
        custom_based_on = pipeline_config.get('custom_based_on')
        
        if logic_mode == 'custom' and custom_based_on == 'sequential':
            first_step_label = self._get_first_sequence_step_label()
            if first_step_label and label == first_step_label:
                if is_new_appearance and len(self.current_cycle_steps) > 0:
                    accumulate_repeats = pipeline_config.get('accumulate_repeats', False)
                    potential_sequence = self.current_cycle_steps + [label]
                    print(f"自定义模式: 第一步 [{label}] 重新出现，检查前缀: {potential_sequence}")
                    
                    if self._is_condition_prefix(potential_sequence):
                        print(f"  → 匹配条件前缀，继续累积")
                    elif accumulate_repeats:
                        print(f"  → 已开启累积重复序列，继续累积（等待最后一步判定）")
                    else:
                        print(f"  → 不匹配任何条件前缀，结算当前序列")
                        self._settle_custom_cycle()
        
        elif logic_mode == 'sequential':
            first_step_label = self._get_first_sequence_step_label()
            if first_step_label and label == first_step_label:
                if is_new_appearance and len(self.current_cycle_steps) > 0:
                    print(f"顺序模式: 第一步 [{label}] 重新出现，结算上一周期")
                    self._settle_sequential_cycle()
        
        if is_new_appearance:
            self.step_start_time[label] = current_time
            self.step_detection_times[label] = current_time
            
            if len(self.current_cycle_steps) == 0:
                self.cycle_start_time = current_time
                self.start_cycle()
            
            self.start_step_recording(label)
        
        self.step_last_seen[label] = current_time
        
        if is_new_appearance and label in enabled_labels:
            should_join_cycle = True
            if self.step_detection_type.get(label) == 'static':
                static_config = self.step_static_config.get(label, {})
                should_join_cycle = static_config.get('join_cycle', True)
            
            if should_join_cycle:
                logic_mode = self.project_config.get('logic_mode') if self.project_config else 'detection'
                if logic_mode == 'custom' or logic_mode == 'sequential':
                    should_add = True
                    pipeline_cfg = self.project_config.get('pipeline_config', {}) if self.project_config else {}
                    is_seq_mode = (logic_mode == 'sequential' or
                                   (logic_mode == 'custom' and pipeline_cfg.get('custom_based_on') == 'sequential'))
                    if is_seq_mode:
                        last_step = self._get_last_sequence_step_label()
                        if last_step and label == last_step:
                            expected = self._get_expected_sequence_labels()
                            if len(expected) >= 3:
                                second_to_last = expected[-2]
                                if second_to_last not in self.current_cycle_steps:
                                    should_add = False
                                    print(f"  ⚠ 最后一步 [{label}] 过早出现（前一步 [{second_to_last}] 未检测到），忽略不加入周期")
                    if should_add:
                        self.current_cycle_steps.append(label)
                        self.last_added_step = label
                elif label not in self.current_cycle_steps:
                    self.current_cycle_steps.append(label)
                    self.last_added_step = label
        
        if should_update_screenshot and det_info:
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
    
    def _update_step_stats(self, detections: list, original_frame: np.ndarray):
        """更新步骤统计和截图
        
        注意：置信度阈值过滤已在 _detect_only 方法中完成，
        此处收到的 detections 都是通过阈值的有效检测
        """
        import base64
        current_time = time.time()
        detected_labels = set()  # 用于统计的标签（通过阈值的）
        frame_detected_labels = set()  # 本帧通过置信度阈值的标签（用于帧数过滤）
        
        # 截图节流：最多每秒更新一次截图，避免 imencode+base64 吃满 CPU
        if not hasattr(self, '_last_screenshot_time'):
            self._last_screenshot_time = 0
        should_update_screenshot = (current_time - self._last_screenshot_time) >= 1.0
        
        for det in detections:
            label = det.get('label', '')
            confidence = det.get('confidence', 0)
            if not label:
                continue
            
            # 置信度阈值双重检查（主要过滤已在 _detect_only 完成，这里作为保险）
            if self.step_conf_thresholds:
                threshold = self.step_conf_thresholds.get(label)
                if threshold is not None and confidence < threshold:
                    continue
            
            frame_detected_labels.add(label)
        
        # 帧数过滤：更新连续帧计数
        # 对于本帧检测到的标签，增加连续帧计数
        for label in frame_detected_labels:
            if label not in self.step_consecutive_frames:
                self.step_consecutive_frames[label] = 0
            self.step_consecutive_frames[label] += 1
            
            # 检查是否达到最少帧数要求
            min_frames = self.step_min_frames.get(label, 1)
            if self.step_consecutive_frames[label] >= min_frames:
                detected_labels.add(label)
                if not self.step_frame_confirmed.get(label):
                    self.step_frame_confirmed[label] = True
        
        # 对于本帧没有检测到的标签，重置连续帧计数
        all_configured_labels = set(self.step_conf_thresholds.keys()) if self.step_conf_thresholds else set()
        for label in all_configured_labels:
            if label not in frame_detected_labels:
                self.step_consecutive_frames[label] = 0
                self.step_frame_confirmed[label] = False
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
            if should_update_screenshot and label in det_by_label:
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
        
        # 先处理缓冲层输出的有序标签（按用户配置的 priority_order）
        for label in ready_ordered:
            self._process_single_step(label, current_time, enabled_labels, _is_seq_like,
                                      should_update_screenshot, original_frame,
                                      det_by_label.get(label))
        
        # 再处理非缓冲的普通标签
        if _is_seq_like:
            _last_step_for_sort = self._get_last_sequence_step_label()
            if _last_step_for_sort and _last_step_for_sort in detected_labels:
                sorted_detected = sorted(detected_labels, key=lambda l: (0 if l == _last_step_for_sort else 1))
            else:
                sorted_detected = list(detected_labels)
        else:
            sorted_detected = list(detected_labels)

        for label in sorted_detected:
            if label in pending_labels:
                continue
            if label in ready_ordered_set:
                continue
            self._process_single_step(label, current_time, enabled_labels, _is_seq_like,
                                      should_update_screenshot, original_frame,
                                      det_by_label.get(label))
        
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
                max_interval = time_config.get('max_interval') or 1.0  # 使用去重间隔作为消失阈值
                
                if current_time - last_time > max_interval:
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
                    if not is_valid:
                        # 从 current_cycle_steps 中移除所有该步骤的出现
                        # 使用列表推导式过滤，因为可能出现多次
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
                        
                        # 记录步骤耗时
                        self.step_durations[label] = round(duration, 2)
                        
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
                        step_name = label
                        if self.project_config:
                            for step in self.project_config.get('steps_config', []):
                                if step.get('label') == label:
                                    step_name = step.get('display_name') or step.get('name') or label
                                    break
                        
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
                        self.last_added_step = None
                        return  # 匹配后不再检查其他条件和基础模式
            
            # 没有自定义条件匹配，回退到基础模式
            # 只在"最后一步"消失时才触发基础模式判定
            # 重要：必须检查消失的步骤是否属于当前周期，避免上一周期的步骤消失时错误触发判定
            if custom_based_on == 'sequential':
                last_step_label = self._get_last_sequence_step_label()
                if last_step_label and completed_step == last_step_label:
                    # 本轮的“最后一步”完成，解除锁定，准备开启下一轮
                    self._cycle_locked_by_last_step = False
                    # 检查消失的步骤是否在当前周期中
                    # 如果不在，说明这是上一个周期的步骤消失（已经在 _settle_custom_cycle 中处理过了）
                    if completed_step in self.current_cycle_steps:
                        # 最后一步消失，触发判定
                        self._check_custom_sequential_mode(pipeline_config, id_to_label)
                    else:
                        print(f"  → 步骤 [{completed_step}] 不在当前周期中，跳过判定（可能是上一周期的残留）")
            elif custom_based_on == 'detection':
                # 同样检查消失的步骤是否属于当前周期
                if completed_step in self.current_cycle_steps:
                    self._check_custom_detection_mode(pipeline_config, id_to_label, enabled_step_labels)
                else:
                    print(f"  → 步骤 [{completed_step}] 不在当前周期中，跳过判定（可能是上一周期的残留）")
            # 如果 custom_based_on 为空，则只依赖自定义条件，不做额外处理
        
        # 顺序模式：只在最后一步消失时判定（与自定义模式逻辑一致）
        elif logic_mode == 'sequential':
            last_step_label = self._get_last_sequence_step_label()
            if last_step_label and completed_step == last_step_label:
                # 本轮的“最后一步”完成，解除锁定，准备开启下一轮
                self._cycle_locked_by_last_step = False
                # 检查消失的步骤是否在当前周期中
                if completed_step in self.current_cycle_steps:
                    self._check_sequential_mode(pipeline_config, id_to_label)
                else:
                    print(f"  → 步骤 [{completed_step}] 不在当前周期中，跳过判定（可能是上一周期的残留）")
        
        # 检测模式
        elif logic_mode == 'detection':
            self._check_detection_mode(pipeline_config, id_to_label, enabled_step_labels)
        
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
            self.last_added_step = None
            # 周期结算后重置步骤时序状态，确保下一轮的相同步骤可被视为“新出现”
            self.step_last_seen.clear()
            self.step_start_time.clear()
            self.last_step_completed_time = None
            self._cycle_locked_by_last_step = False
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
            self.last_added_step = None
            # 周期结算后重置步骤时序状态，确保下一轮的相同步骤可被视为“新出现”
            self.step_last_seen.clear()
            self.step_start_time.clear()
            self.last_step_completed_time = None
            self._cycle_locked_by_last_step = False
            return
        
        last_step_label = expected_labels[-1]
        
        # ── 拆分：以 last_step 首次出现为界 ──
        if last_step_label in self.current_cycle_steps:
            split_idx = self.current_cycle_steps.index(last_step_label)
            this_cycle = self.current_cycle_steps[:split_idx + 1]
            next_carry = self.current_cycle_steps[split_idx + 1:]
        else:
            this_cycle = list(self.current_cycle_steps)
            next_carry = []
        
        self.current_cycle_steps = this_cycle
        
        # ── 补写尚未有 StepRecord 的步骤 ──
        cycle_end_time = time.time()
        if getattr(self, '_last_disappeared_step_times', None) and last_step_label in self._last_disappeared_step_times:
            cycle_end_time = self._last_disappeared_step_times[last_step_label]
        try:
            db = self._get_db_session()
            existing = db.query(StepRecord.step_label).filter(StepRecord.cycle_id == self.current_cycle_id).all()
            recorded_labels = {r.step_label for r in existing}
            max_order = db.query(StepRecord.step_order).filter(StepRecord.cycle_id == self.current_cycle_id).all()
            next_order = (max(o[0] for o in max_order) + 1) if max_order else 1
            db.close()
            for label in this_cycle:
                if label in recorded_labels:
                    continue
                start_t = self.step_start_time.get(label, cycle_end_time)
                duration = max(0, cycle_end_time - start_t)
                step_name = label
                for step in (steps_config or []):
                    if step.get('label') == label:
                        step_name = step.get('display_name') or step.get('name') or label
                        break
                self.record_step(
                    step_label=label, step_name=step_name,
                    start_time=start_t, end_time=cycle_end_time,
                    duration=duration, interval=None, step_order=next_order)
                next_order += 1
        except Exception as e:
            print(f"补写周期内缺失步骤记录失败: {e}")
        
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
        # 已通过正常消失流程计数的步骤不在 step_last_seen 中
        # 仍在 step_last_seen 中的步骤→补计后移除，下一帧重新识别为"新出现"
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
                    self.step_durations[label] = round(duration, 2)
                    print(f"  顺序判定补计: {label}, 耗时 {duration:.2f}s, 累计: {self.step_counts[label]}")
                
                # 移除跟踪，让下一帧重新识别为"新出现"，避免双重计数
                del self.step_last_seen[label]
                if label in self.step_start_time:
                    del self.step_start_time[label]
        
        # ── 重置 / 承接下一周期 ──
        if next_carry:
            self.current_cycle_steps = next_carry
            self.last_added_step = next_carry[-1]
            self.cycle_start_time = self.step_start_time.get(next_carry[0], time.time())
            self.start_cycle()
            self._cycle_locked_by_last_step = False
        else:
            self.current_cycle_steps = []
            self.last_added_step = None
            self.last_step_completed_time = None
            self._cycle_locked_by_last_step = False
    
    def _check_custom_sequential_mode(self, pipeline_config: dict, id_to_label: dict):
        """检查自定义模式（基于顺序模式）的判定
        
        与普通顺序模式的区别：
        1. 当前周期可能包含重复步骤
        2. 如果序列长度超过预期（有重复步骤）且不匹配任何自定义条件，判定为 NG
        3. 使用独立的 custom_sequence_order 配置
        """
        # 使用自定义模式独立的顺序配置
        sequence_order = pipeline_config.get('custom_sequence_order', [])
        if not sequence_order:
            self.current_cycle_steps = []
            self.last_added_step = None
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
            self.last_added_step = None
            return
        
        last_step_label = expected_labels[-1]
        
        # ── 拆分：以 last_step 首次出现为界 ──
        if last_step_label in self.current_cycle_steps:
            split_idx = self.current_cycle_steps.index(last_step_label)
            this_cycle = self.current_cycle_steps[:split_idx + 1]
            next_carry = self.current_cycle_steps[split_idx + 1:]
        else:
            this_cycle = list(self.current_cycle_steps)
            next_carry = []
        
        self.current_cycle_steps = this_cycle
        
        # ── 补写尚未有 StepRecord 的步骤 ──
        cycle_end_time = time.time()
        if getattr(self, '_last_disappeared_step_times', None) and last_step_label in self._last_disappeared_step_times:
            cycle_end_time = self._last_disappeared_step_times[last_step_label]
        try:
            db = self._get_db_session()
            existing = db.query(StepRecord.step_label).filter(StepRecord.cycle_id == self.current_cycle_id).all()
            recorded_labels = {r.step_label for r in existing}
            max_order = db.query(StepRecord.step_order).filter(StepRecord.cycle_id == self.current_cycle_id).all()
            next_order = (max(o[0] for o in max_order) + 1) if max_order else 1
            db.close()
            for label in this_cycle:
                if label in recorded_labels:
                    continue
                start_t = self.step_start_time.get(label, cycle_end_time)
                duration = max(0, cycle_end_time - start_t)
                step_name = label
                for step in (steps_config or []):
                    if step.get('label') == label:
                        step_name = step.get('display_name') or step.get('name') or label
                        break
                self.record_step(
                    step_label=label, step_name=step_name,
                    start_time=start_t, end_time=cycle_end_time,
                    duration=duration, interval=None, step_order=next_order)
                next_order += 1
        except Exception as e:
            print(f"补写周期内缺失步骤记录失败: {e}")
        
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
                    self.step_durations[label] = round(duration, 2)
                    print(f"  自定义顺序判定补计: {label}, 耗时 {duration:.2f}s, 累计: {self.step_counts[label]}")
                
                del self.step_last_seen[label]
                if label in self.step_start_time:
                    del self.step_start_time[label]
        
        # ── 重置 / 承接下一周期 ──
        if next_carry:
            self.current_cycle_steps = next_carry
            self.last_added_step = next_carry[-1]
            self.cycle_start_time = self.step_start_time.get(next_carry[0], time.time())
            self.start_cycle()
            self._cycle_locked_by_last_step = False
        else:
            self.current_cycle_steps = []
            self.last_added_step = None
            self.last_step_completed_time = None
            self._cycle_locked_by_last_step = False

    def _check_detection_mode(self, pipeline_config: dict, id_to_label: dict, enabled_step_labels: list):
        """检查检测模式"""
        detection_step_ids = pipeline_config.get('detection_steps', [])
        
        # 将步骤ID转换为标签名
        if detection_step_ids:
            detection_labels = [id_to_label.get(sid) for sid in detection_step_ids if sid in id_to_label]
        else:
            detection_labels = enabled_step_labels
        
        if not detection_labels:
            return
        
        print(f"检测模式检查: 需要={detection_labels}, 当前周期={self.current_cycle_steps}")
        
        if all(label in self.current_cycle_steps for label in detection_labels):
            self._trigger_event(1, '检测完成')  # 事件1: 合格
            self.current_cycle_steps = []
            self.last_added_step = None
    
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
        
        print(f"自定义模式（基于检测）检查: 需要={detection_labels}, 当前周期={self.current_cycle_steps}")
        
        if all(label in self.current_cycle_steps for label in detection_labels):
            self._trigger_event(1, '检测完成')  # 事件1: 合格
            self.current_cycle_steps = []
            self.last_added_step = None
    
    def _trigger_event(self, event_id, reason: str):
        """触发事件"""
        if not self.project_config:
            return
        
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
            return
        
        print(f"触发事件: {event.get('name', event_id)} - {reason}")
        
        # 判断是否为合格事件（事件ID为1或者名称包含"合格"）
        current_event_id = event.get('id')
        is_good = current_event_id == 1
        
        # 如果是事件1（合格），计算并记录周期时间
        if current_event_id == 1 and self.cycle_start_time is not None:
            cycle_time = time.time() - self.cycle_start_time
            self.cycle_times.append(cycle_time)
            # 只保留最近100个周期时间
            if len(self.cycle_times) > 100:
                self.cycle_times = self.cycle_times[-100:]
            print(f"  周期时间: {cycle_time:.2f}s, 平均: {sum(self.cycle_times)/len(self.cycle_times):.2f}s")
        
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
        
        # 执行计数器动作（支持 delta 和 value 两种字段名）
        actions = event.get('actions', [])
        for action in actions:
            counter_name = action.get('counter_name', '')
            # 兼容前端的 delta 字段和后端的 value 字段
            value = action.get('delta', action.get('value', 1))
            if counter_name in self.counters:
                self.counters[counter_name] += value
                print(f"  计数器 {counter_name} += {value} => {self.counters[counter_name]}")
        
        # 记录事件
        self.events_log.append({
            'event_id': str(event.get('id', event_id)),
            'event_name': event.get('name', ''),
            'reason': reason,
            'timestamp': time.time(),
            'show_notification': event.get('show_notification', False),
            'toast_id': event.get('toast_id', 'ok' if event.get('id') == 1 else 'ng' if event.get('id') == 2 else 'ok')
        })
        
        # 触发报警器（如果已配置）
        try:
            from backend.api.alarm import alarm_manager
            event_type = f'event{current_event_id}'
            alarm_manager.trigger_alarm(event_type)
        except Exception as e:
            print(f"触发报警失败: {e}")
    
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
            
            def run_inference():
                t_predict_start = time.time()
                # 使用 stream=True 避免 ultralytics 内部累积所有历史结果
                result = list(self.model.predict(
                    frame, 
                    conf=self.conf_threshold, 
                    iou=self.iou_threshold, 
                    imgsz=640, 
                    verbose=False, 
                    device=device,
                    stream=True
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
                    detections.append({
                        'x': float(x1 / w),
                        'y': float(y1 / h),
                        'w': float((x2 - x1) / w),
                        'h': float((y2 - y1) / h),
                        'confidence': confidence,
                        'class_id': class_id,
                        'label': class_name
                    })
        except Exception as e:
            print(f"检测错误: {e}")
            import traceback
            traceback.print_exc()
        
        return detections
    
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
        
        # 清空队列中残留的帧
        dropped = 0
        while not self._recording_queue.empty():
            try:
                self._recording_queue.get_nowait()
                dropped += 1
            except:
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
            # 缩小到录制分辨率后再入队（640×360=0.69MB vs 1280×720=2.76MB）
            max_w, max_h = FFmpegRecorder.MAX_RECORD_WIDTH, FFmpegRecorder.MAX_RECORD_HEIGHT
            h, w = frame.shape[:2]
            if w > max_w or h > max_h:
                scale = min(max_w / w, max_h / h)
                new_w = int(w * scale) // 2 * 2
                new_h = int(h * scale) // 2 * 2
                small_frame = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA)
            else:
                small_frame = frame.copy()
            
            self._recording_queue.put_nowait(small_frame)
        except:
            try:
                self._recording_queue.get_nowait()
                # 已经缩小过了，直接入队
                self._recording_queue.put_nowait(small_frame)
                self._recording_drop_count += 1
            except:
                self._recording_drop_count += 1
    
    def _write_frame_to_writers(self, frame):
        """
        实际写入帧到所有 VideoWriter（在录制线程中调用）
        """
        if frame is None:
            return
        
        try:
            # 获取帧尺寸
            h, w = frame.shape[:2]
            
            # 如果帧尺寸与预期不匹配，调整帧尺寸
            if w != self.width or h != self.height:
                frame = cv2.resize(frame, (self.width, self.height))
            
            # 确保帧是 BGR 格式（3通道）
            if len(frame.shape) == 2:
                frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
            elif frame.shape[2] == 4:
                frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
            
            # 写入会话视频
            try:
                if self.video_writer and self.video_writer.isOpened():
                    self.video_writer.write(frame)
            except Exception as e:
                print(f"[录制警告] 写入会话视频失败: {e}")
                try:
                    self.video_writer.release()
                except:
                    pass
                self.video_writer = None
            
            # 写入周期视频
            try:
                if self.cycle_video_writer and self.cycle_video_writer.isOpened():
                    self.cycle_video_writer.write(frame)
            except Exception as e:
                print(f"[录制警告] 写入周期视频失败: {e}")
                try:
                    self.cycle_video_writer.release()
                except:
                    pass
                self.cycle_video_writer = None
            
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
                t1 = time.time()
                with self._inference_frame_lock:
                    frame = self._latest_frame_for_inference
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
                original_frame = frame.copy()
                frame_count += 1
                
                # 执行推理 - 关键诊断点
                t3 = time.time()
                detections = self._detect_only(frame)
                t4 = time.time()
                detect_time = (t4 - t3) * 1000
                
                # 如果推理耗时超过200ms，记录警告（降低阈值以便更早发现问题）
                if detect_time > 200:
                    debug_log(f"!!! 推理耗时: {detect_time:.1f}ms, 检测数={len(detections) if detections else 0}", "INFERENCE")
                
                # 更新步骤统计 - 关键诊断点
                t5 = time.time()
                self._update_step_stats(detections, original_frame)
                t6 = time.time()
                update_time = (t6 - t5) * 1000
                
                # 如果步骤统计耗时超过100ms，记录警告
                if update_time > 100:
                    debug_log(f"!!! 步骤统计耗时: {update_time:.1f}ms", "INFERENCE")
                
                # 计算延迟
                self.latency = int((time.time() - t3) * 1000)
                
                # 获取已确认的检测结果（只包含通过帧计数验证的）
                # 必须先获取 confirmed，然后用它更新 current_detections
                # 这样前端显示的检测框也会经过帧数过滤
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
    
    def start_camera(self, device_index: int = 0, width: int = 1280, height: int = 720, fps: int = 30):
        """启动摄像头"""
        self.stop()
        
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
        
        self.capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.capture.set(cv2.CAP_PROP_FPS, fps)
        
        self.source_type = 'camera'
        self.camera_index = device_index
        self.width = width
        self.height = height
        self.fps = fps
        self.is_running = True
        
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        
        return True
    
    def start_hikvision_camera(self, device_index: int = 0, width: int = 1280, height: int = 720, fps: int = 30):
        """启动海康工业相机（带增强调试）"""
        hik_log(f"start_hikvision_camera 调用: device_index={device_index}, {width}x{height}@{fps}fps")
        
        if not HIK_SDK_AVAILABLE:
            hik_log("SDK 不可用", "ERROR")
            raise Exception("海康 SDK 未加载，无法使用海康相机")
        
        self.stop()
        
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
        
        self.stop()
        
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
        self.stop()
        
        if not os.path.exists(image_path):
            raise Exception(f"图片文件不存在: {image_path}")
        
        frame = cv2.imread(image_path)
        if frame is None:
            raise Exception(f"无法读取图片: {image_path}")
        
        self.source_type = 'image'
        self.image_path = image_path
        
        # 如果正在检测，对图片进行推理
        if self.is_detecting and self.model is not None:
            frame, detections = self._detect_and_draw(frame)
            with self.detection_lock:
                self.current_detections = detections
        
        with self.frame_lock:
            self.current_frame = frame
        self.is_running = True
        
        return True
    
    def start_detection(self, model_path: str = None):
        """开始检测"""
        if model_path and (self.model is None or self.model_path != model_path):
            if not self.load_model(model_path):
                raise Exception("模型加载失败")
        
        if self.model is None:
            raise Exception("未加载模型")
        
        # 如果视频源暂停（有 capture 但 is_running=False），自动恢复
        if not self.is_running and self.capture is not None and self.capture.isOpened():
            print("[自动恢复] 检测到暂停的视频源，自动恢复播放")
            # 确保旧线程已停止
            if self._thread and self._thread.is_alive():
                self._thread.join(timeout=1.0)
            # 视频从头开始
            if self.source_type == 'video':
                self.capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
                self.video_current_frame = 0
                self.video_ended = False
            self.is_running = True
            self._thread = threading.Thread(target=self._capture_loop, daemon=True)
            self._thread.start()
        
        self.is_detecting = True
        
        # 启动推理线程（如果视频已在运行，需要在这里启动）
        if self.is_running and self.model is not None:
            self._start_inference_thread()
        
        # 启动录制线程（独立于 CUDA，避免段错误）
        if self.recording_enabled:
            self._start_recording_thread()
        
        print("检测已启动")
        return True
    
    def stop_detection(self):
        """停止检测"""
        self.is_detecting = False
        
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
        with self._confirmed_detections_lock:
            self._confirmed_detections = []
        
        # 清理帧计数状态
        self.step_consecutive_frames.clear()
        self.step_frame_confirmed.clear()
        
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
        """重置统计数据（计数器、步骤计数、截图等）"""
        import gc
        
        # 重置步骤统计
        self.step_counts = {}
        self.step_screenshots = {}  # 清空截图缓存（释放内存）
        self.step_last_seen = {}
        self.step_start_time = {}  # 重置步骤开始时间
        self.step_detection_times = {}  # 重置步骤检测时间
        self.step_durations = {}  # 重置步骤耗时
        self.step_intervals = {}  # 重置步骤间隔时间
        self.last_step_completed_time = None  # 重置上一步骤完成时间
        
        # 重置计数器（保留计数器名称，值清零）
        for key in self.counters:
            self.counters[key] = 0
        
        # 重置周期状态
        self.current_cycle_steps = []
        self.last_added_step = None
        self.cycle_complete = False
        self._cycle_locked_by_last_step = False
        self.events_log = []  # 清空事件日志
        
        # 重置周期时间统计
        self.cycle_times = []
        self.cycle_start_time = None
        
        # 重置卡尔曼滤波器
        self._kalman_filters.clear()
        self._detection_missing_frames.clear()
        self._detection_history.clear()
        
        # 重置帧计数状态
        self.step_consecutive_frames.clear()
        self.step_frame_confirmed.clear()
        self.step_static_triggered.clear()
        
        # 强制垃圾回收
        gc.collect()
        
        print("统计数据已重置（含缓存清理）")
    
    # ========== 视频录制功能 ==========
    
    def start_session_recording(self):
        """开始会话视频录制（使用 FFmpeg 进程）"""
        if not self.export_settings or not self.export_settings.get('record_session_video'):
            return
        
        # 先关闭旧的录制器，防止 FFmpeg 进程泄漏
        if self.video_writer:
            try:
                self.video_writer.release()
            except:
                pass
            self.video_writer = None
        
        try:
            # 使用 .mp4 格式
            filename = f"session_{self.current_session_uuid}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
            filepath = os.path.join(settings.SESSION_VIDEO_DIR, filename)
            
            fps = min(self.export_settings.get('video_fps', 30), 25)  # 限制FPS
            
            # 确保宽高有效
            width = self.width if self.width > 0 else 1280
            height = self.height if self.height > 0 else 720
            
            # 使用 FFmpegRecorder
            self.video_writer = FFmpegRecorder(filepath, width, height, fps)
            if not self.video_writer.open():
                print(f"[录制警告] 无法创建会话视频录制器")
                self.video_writer = None
                return
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
        """停止会话视频录制"""
        if self.video_writer:
            try:
                self.video_writer.release()
                print("会话视频录制已停止")
                
                # 更新视频信息
                db = self._get_db_session()
                session = db.query(DetectionSession).filter(DetectionSession.id == self.current_session_id).first()
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
            finally:
                self.video_writer = None
    
    def start_cycle_recording(self):
        """开始周期视频录制（使用 FFmpeg 进程）"""
        if not self.export_settings or not self.export_settings.get('record_cycle_video'):
            return
        
        # 先关闭旧的周期录制器，防止 FFmpeg 进程泄漏
        if self.cycle_video_writer:
            try:
                self.cycle_video_writer.release()
            except:
                pass
            self.cycle_video_writer = None
        
        try:
            # 使用 .mp4 格式（FFmpeg H.264 编码）
            filename = f"cycle_{self.current_cycle_uuid}_{datetime.now().strftime('%H%M%S')}.mp4"
            filepath = os.path.join(settings.CYCLE_VIDEO_DIR, filename)
            
            fps = min(self.export_settings.get('video_fps', 30), 25)  # 限制FPS
            
            # 确保宽高有效
            width = self.width if self.width > 0 else 1280
            height = self.height if self.height > 0 else 720
            
            # 使用 FFmpegRecorder 替代 cv2.VideoWriter
            self.cycle_video_writer = FFmpegRecorder(filepath, width, height, fps)
            if not self.cycle_video_writer.open():
                print(f"[录制警告] 无法创建周期视频录制器")
                self.cycle_video_writer = None
                return
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
        """停止周期视频录制"""
        if self.cycle_video_writer:
            try:
                self.cycle_video_writer.release()
                print("周期视频录制已停止")
            except Exception as e:
                print(f"停止周期录制失败: {e}")
            finally:
                self.cycle_video_writer = None
    
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
                
                # 限制同时录制的步骤视频数量（1个，减少 FFmpeg 内存）
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
        if self.video_writer:
            try:
                self.video_writer.release()
            except:
                pass
            self.video_writer = None
        if self.cycle_video_writer:
            try:
                self.cycle_video_writer.release()
            except:
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
        # 先停止推理线程，避免残留
        self._stop_inference_thread()
        # 停止录制线程和 FFmpeg 进程，防止资源泄漏
        self._stop_recording_thread()
        self._close_all_writers()
        # 等待捕获线程退出
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        # 不清除 current_frame，保持画面停在当前帧
        # 不释放 capture，方便后续恢复
        with self.detection_lock:
            self.current_detections = []
        # 清理推理缓存
        self._clear_inference_caches()
        print("已暂停：画面和检测都停止")
    
    def resume(self):
        """恢复：从暂停状态恢复，重新启动视频流和推理"""
        if self.capture is None:
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

        # 显式启动推理线程（capture_loop 启动时也会检查，这里双重保证）
        if self.model is not None:
            self._start_inference_thread()

        print("已恢复：视频流和推理重新启动")
        return True
    
    def standby(self):
        """待机：只停止检测推理，画面继续播放"""
        self.is_detecting = False
        with self.detection_lock:
            self.current_detections = []
        print("已待机：检测停止，画面继续")
    
    def stop(self):
        """停止当前输入源（完全停止并释放资源）"""
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
        
        # 释放模型和 GPU 资源
        self._release_model()
        
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
        with self._confirmed_detections_lock:
            self._confirmed_detections = []
        
        # 5. 清理帧计数缓存
        self.step_consecutive_frames.clear()
        self.step_frame_confirmed.clear()
        self.step_static_triggered.clear()
        
        # 6. 限制周期时间记录（保留最近50条）
        if len(self.cycle_times) > 50:
            self.cycle_times = self.cycle_times[-50:]
        
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
    
    def generate_mjpeg(self):
        """生成 MJPEG 流（暂停时以低帧率持续输出当前帧，防止画面变黑）"""
        target_interval = 1.0 / max(self.target_stream_fps, 1)
        min_interval = 1.0 / 15
        idle_interval = 1.0  # 暂停时 1fps，节省资源
        idle_count = 0
        max_idle = 300  # 暂停状态最多保持 300 秒后才结束流
        
        while True:
            frame_start = time.time()
            
            if self.is_running:
                idle_count = 0
                frame = self.get_frame()
                if frame is not None:
                    ret, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 65])
                    if ret:
                        yield (b'--frame\r\n'
                               b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')
                    del buffer
                del frame
                
                elapsed = time.time() - frame_start
                interval = target_interval if self.frame_limit_enabled else min_interval
                sleep_time = max(0.001, interval - elapsed)
                time.sleep(sleep_time)
            else:
                # 暂停状态：低帧率输出当前帧，保持流活跃
                frame = self.get_frame()
                if frame is not None:
                    ret, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 65])
                    if ret:
                        yield (b'--frame\r\n'
                               b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')
                    del buffer
                del frame
                
                idle_count += 1
                if idle_count > max_idle:
                    break
                time.sleep(idle_interval)
    
    def get_snapshot(self):
        """获取当前帧的单张 JPEG 快照（用于前端 canvas 渲染）"""
        frame = self.get_frame()
        if frame is not None:
            ret, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
            if ret:
                return buffer.tobytes()
        return None

# 全局视频源管理器
video_manager = VideoSourceManager()


# Pydantic 模型
class CameraStartRequest(BaseModel):
    device_index: int = 0
    width: int = 1280
    height: int = 720
    fps: int = 30

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
            
            # 检查设备是否可用（尝试打开）
            # 对于虚拟摄像头和物理摄像头都尝试
            name = _get_camera_name_linux(index)
            
            # 如果是 v4l2loopback 虚拟摄像头
            if name and "Virtual" in name:
                cameras.append({"index": index, "name": f"{name} (索引 {index})"})
            elif name:
                # 物理摄像头：跳过奇数索引（通常是元数据设备）
                if index % 2 != 0:
                    continue
                cameras.append({"index": index, "name": f"{name} (索引 {index})"})
            else:
                # 没有名称的设备，尝试打开验证
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
    # 获取当前正在使用的摄像头索引
    current_camera_index = None
    if video_manager.source_type == 'camera' and video_manager.is_running:
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
        "target_stream_fps": video_manager.target_stream_fps
    }

@router.post("/stream/config")
def set_stream_config(req: StreamConfigRequest):
    """设置视频流配置（帧率限制）"""
    video_manager.frame_limit_enabled = req.frame_limit_enabled
    video_manager.target_stream_fps = max(1, min(120, req.target_stream_fps))  # 限制1-120fps
    
    # 保存配置到文件
    video_manager._save_device_config()
    
    return {
        "status": "success",
        "frame_limit_enabled": video_manager.frame_limit_enabled,
        "target_stream_fps": video_manager.target_stream_fps
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
def start_camera(req: CameraStartRequest):
    """启动摄像头"""
    try:
        video_manager.start_camera(
            device_index=req.device_index,
            width=req.width,
            height=req.height,
            fps=req.fps
        )
        return {"status": "success", "message": "摄像头已启动"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/camera/stop")
def stop_camera():
    """停止摄像头"""
    video_manager.stop()
    return {"status": "success", "message": "摄像头已停止"}


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
def start_hikvision_camera(req: HikvisionStartRequest):
    """启动海康工业相机"""
    hik_log(f"API /hikvision/start 收到请求: device_index={req.device_index}, {req.width}x{req.height}@{req.fps}fps")
    try:
        video_manager.start_hikvision_camera(
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
def stop_hikvision_camera():
    """停止海康工业相机"""
    video_manager.stop()
    return {"status": "success", "message": "海康相机已停止"}

@router.get("/hikvision/status")
def get_hikvision_status():
    """获取海康相机状态"""
    return {
        "sdk_available": HIK_SDK_AVAILABLE,
        "is_connected": video_manager.source_type == 'hikvision' and video_manager.is_running,
        "device_index": video_manager.hik_device_index if video_manager.source_type == 'hikvision' else None
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
def start_video(req: VideoStartRequest):
    """启动视频播放"""
    try:
        video_manager.start_video(req.file_path, req.speed)
        return {"status": "success", "message": "视频已开始播放", "speed": req.speed}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/video/stop")
def stop_video():
    """停止视频"""
    video_manager.stop()
    return {"status": "success", "message": "视频已停止"}

@router.post("/video/speed")
def set_video_speed(req: VideoSpeedRequest):
    """设置视频播放倍速"""
    try:
        video_manager.set_video_speed(req.speed)
        return {"status": "success", "message": f"倍速已设置为 {req.speed}x", "speed": req.speed}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/video/progress")
def set_video_progress(req: VideoProgressRequest):
    """设置视频播放进度"""
    try:
        video_manager.set_video_progress(req.progress)
        return {"status": "success", "message": f"进度已设置为 {req.progress*100:.1f}%", "progress": req.progress}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/video/info")
def get_video_info():
    """获取视频播放信息"""
    info = video_manager.get_video_info()
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
def set_image(req: ImageSetRequest):
    """设置图片为输入源"""
    try:
        video_manager.set_image(req.file_path)
        return {"status": "success", "message": "图片已设置为输入源"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/detection/start")
def start_detection(req: DetectionStartRequest):
    """开始检测"""
    try:
        video_manager.conf_threshold = req.conf
        video_manager.iou_threshold = req.iou
        video_manager.start_detection(req.model_path)
        
        # 如果有项目配置，自动创建会话
        if video_manager.project_config and video_manager.project_config.get('id'):
            session_info = video_manager.start_session(video_manager.project_config['id'])
            if session_info:
                return {
                    "status": "success", 
                    "message": "检测已启动",
                    "session_id": session_info.get('session_id'),
                    "session_uuid": session_info.get('session_uuid')
                }
        
        return {"status": "success", "message": "检测已启动"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/detection/stop")
def stop_detection():
    """停止检测（只停止推理）并结束会话"""
    # 结束当前会话
    video_manager.end_session()
    video_manager.stop_detection()
    return {"status": "success", "message": "检测已停止"}

@router.post("/detection/pause")
def pause_detection():
    """暂停：停止画面更新和检测，画面停在当前帧"""
    video_manager.pause()
    return {"status": "success", "message": "已暂停"}

@router.post("/detection/resume")
def resume_detection():
    """恢复：从暂停状态恢复，重新启动视频流和检测"""
    if video_manager.resume():
        return {"status": "success", "message": "已恢复"}
    else:
        raise HTTPException(status_code=400, detail="无法恢复：没有可用的视频源")

@router.post("/detection/standby")
def standby_detection():
    """待机：只停止检测推理，画面继续播放"""
    video_manager.standby()
    return {"status": "success", "message": "已待机"}

@router.post("/detection/reset-stats")
def reset_detection_stats():
    """重置统计数据（计数器、步骤计数等）"""
    video_manager.reset_stats()
    return {"status": "success", "message": "统计数据已重置"}

@router.get("/detection/results")
def get_detection_results():
    """获取检测结果"""
    # 获取最新事件（用于显示提示框）
    recent_events = []
    if video_manager.events_log:
        # 只返回最近5秒内的事件
        current_time = time.time()
        recent_events = [
            e for e in video_manager.events_log 
            if current_time - e.get('timestamp', 0) < 5
        ]
    
    # 计算平均周期时间
    avg_cycle_time = 0
    if video_manager.cycle_times:
        avg_cycle_time = round(sum(video_manager.cycle_times) / len(video_manager.cycle_times), 2)
    
    return {
        "detections": video_manager.get_detections(),
        "fps": video_manager.fps_actual,
        "latency": video_manager.latency,
        "is_detecting": video_manager.is_detecting,
        "source_type": video_manager.source_type,
        "is_running": video_manager.is_running,
        "step_counts": video_manager.step_counts.copy(),
        "step_screenshots": video_manager.step_screenshots.copy(),
        "step_detection_times": video_manager.step_detection_times.copy(),
        "step_durations": video_manager.step_durations.copy(),
        "step_intervals": video_manager.step_intervals.copy(),
        "counters": video_manager.counters.copy(),
        "recent_events": recent_events,
        "average_cycle_time": avg_cycle_time,
        "current_cycle_steps": list(video_manager.current_cycle_steps)
    }

class ProjectConfigRequest(BaseModel):
    project_id: int
    name: str
    logic_mode: str = 'detection'
    steps_config: list = []
    pipeline_config: dict = {}
    events_config: list = []
    counters_config: list = []

@router.post("/detection/set-project")
def set_project_config(req: ProjectConfigRequest):
    """设置项目配置"""
    video_manager.set_project_config({
        'id': req.project_id,
        'name': req.name,
        'logic_mode': req.logic_mode,
        'steps_config': req.steps_config,
        'pipeline_config': req.pipeline_config,
        'events_config': req.events_config,
        'counters_config': req.counters_config
    })
    return {"status": "success", "message": "项目配置已设置"}

@router.get("/status")
def get_source_status():
    """获取当前输入源状态"""
    return {
        "is_running": video_manager.is_running,
        "is_detecting": video_manager.is_detecting,
        "source_type": video_manager.source_type,
        "width": video_manager.width,
        "height": video_manager.height,
        "fps": video_manager.fps,
        "fps_actual": video_manager.fps_actual,
        "latency": video_manager.latency,
        "model_loaded": video_manager.model is not None
    }


@router.get("/health")
def get_health_status():
    """
    获取系统健康状态
    用于监控线程运行状态和 GPU 资源使用情况
    """
    import torch
    
    current_time = time.time()
    
    # 检查线程健康状态
    inference_thread_alive = (
        video_manager._inference_thread is not None and 
        video_manager._inference_thread.is_alive()
    )
    capture_thread_alive = (
        video_manager._thread is not None and 
        video_manager._thread.is_alive()
    )
    
    # 计算线程无响应时间
    inference_idle_time = current_time - video_manager._last_inference_heartbeat
    capture_idle_time = current_time - video_manager._last_capture_heartbeat
    
    # 判断线程是否健康
    inference_healthy = not inference_thread_alive or inference_idle_time < video_manager._thread_timeout_threshold
    capture_healthy = not capture_thread_alive or capture_idle_time < video_manager._thread_timeout_threshold
    
    # GPU 信息
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
    
    return {
        "healthy": overall_healthy,
        "timestamp": current_time,
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
            "is_running": video_manager.is_running,
            "is_detecting": video_manager.is_detecting,
            "model_loaded": video_manager.model is not None,
            "fps_actual": video_manager.fps_actual,
            "latency_ms": video_manager.latency
        },
        "gpu": gpu_info
    }


def get_video_feed():
    """获取视频流（供 main.py 使用）"""
    return video_manager.generate_mjpeg()

def get_video_manager():
    """获取视频管理器实例"""
    return video_manager
