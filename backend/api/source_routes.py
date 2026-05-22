"""
输入源 HTTP 路由层（从 backend/api/source.py 拆分而来）

历史背景：
  source.py 长 9898 行，里面 8358 行属于 VideoSourceManager 主类，
  最后 1000 多行是 FastAPI router 端点。两者强耦合性差，但堆在同一个
  文件里导致改动 router 时 IDE 极慢、回归风险大。

本文件职责：
  - 所有 source 域 router 端点（/cameras, /gpu/*, /camera/*, /rtsp/*,
    /hikvision/*, /hcnetsdk/*, /video/*, /image/*, /detection/*, /status,
    /health, /transform/*, /kalman/*, /stream/*）
  - 这些端点专属的 Pydantic Request 模型
  - 摄像头探测函数（_detect_cameras_linux/_detect_cameras_windows）
  - 摄像头列表内存缓存（_cameras_cache）

兼容约定：
  - router 实例直接复用 source.py 里的同一个 APIRouter()，
    所以 main.py 的 `from backend.api.source import router as source_router`
    无需修改。
  - 注册时机：source.py 文件末尾会 `import backend.api.source_routes`，
    Python 副作用执行装饰器，把端点挂到共享 router 上。
"""
import os
import shutil
import time
import uuid
from typing import Any, Dict, List, Optional

from fastapi import File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from backend.core.config import settings

# 复用 source.py 已经创建的 router 实例 + VideoSourceManager 相关符号
# 注意：这里产生 "subordinate" 循环引用——source.py 末尾才 import 本模块，
# 所以本模块 import 时 source 已经把所有需要的符号定义完毕。
from backend.api.source import (
    HCNET_SDK_AVAILABLE,
    HIK_SDK_AVAILABLE,
    _get_mgr,
    get_hikvision_device_list,
    hik_log,
    router,
    video_manager,
)

# 内部摄像头依赖 cv2（用于 windows DirectShow 探测 / linux v4l2 探测）
import cv2  # noqa: E402


# ============================================================
# Pydantic Request 模型
# ============================================================
class CameraStartRequest(BaseModel):
    device_index: int = 0
    width: int = 1280
    height: int = 720
    fps: int = 60
    auto_exposure: bool = True
    exposure_value: float = -6.0


class VideoStartRequest(BaseModel):
    file_path: str
    speed: float = 1.0


class VideoSpeedRequest(BaseModel):
    speed: float


class VideoProgressRequest(BaseModel):
    progress: float


class ImageSetRequest(BaseModel):
    file_path: str


class ModelSpec(BaseModel):
    """单个 slot 的多模型加载参数 (Step 6 feat/multi-model-roi-link).

    name='main' 表示主模型 (与老链路 model_path/conf/iou 等价);
    name 任意字符串则为副模型 slot, 独立 conf/iou/roi/schedule/class_filter.
    """
    name: str = "main"
    model_path: str
    conf: Optional[float] = None
    iou: Optional[float] = None
    roi: Optional[List[List[float]]] = None  # 归一化多边形顶点 [[x,y], ...]
    schedule: Optional[Dict[str, Any]] = None  # {type, n, events}
    class_filter: Optional[List[str]] = None
    priority: Optional[int] = None
    display_color: Optional[str] = None
    use_half: Optional[bool] = None
    original_pt_path: Optional[str] = None


class DetectionStartRequest(BaseModel):
    # 老字段 (单模型, 向后兼容): 仍是必填语义但允许 None 表示 "走多模型 payload"
    model_path: Optional[str] = None
    conf: float = 0.25
    iou: float = 0.45
    session_name: Optional[str] = None  # 客户自定义会话标识 (英文/数字/中文皆可, ≤ 64)
    # Step 6 新字段: 多模型 payload, 非空时优先级高于老字段
    models: Optional[List[ModelSpec]] = None


class StreamConfigRequest(BaseModel):
    frame_limit_enabled: bool = False
    target_stream_fps: int = 30
    use_half: bool = False
    mediapipe_enabled: bool = False
    mediapipe_pose: bool = True
    mediapipe_hands: bool = True
    mediapipe_confidence: float = 0.7
    mediapipe_interval: int = 2
    # v3.8.0 mp.solutions.hands 调优 (朋友程序密码 = complexity=1 + det_conf=0.5)
    # default None: 老前端不带字段时跳过覆盖, 保留 host 当前值
    mediapipe_model_complexity: Optional[int] = None
    mediapipe_track_confidence: Optional[float] = None
    # v3.8.0 二段 pipeline: 配 hand-detector .pt 即启用 (空 = 走 baseline)
    mediapipe_hand_detector_path: Optional[str] = ""
    mediapipe_hand_detector_kind: Optional[str] = "v8"
    mediapipe_hand_detector_conf: Optional[float] = 0.25
    mediapipe_hand_detector_iou: Optional[float] = 0.45
    mediapipe_hand_detector_imgsz: Optional[int] = 640
    mediapipe_hand_detector_class: Optional[int] = -1
    mediapipe_hand_roi_pad: Optional[float] = 0.3
    mediapipe_landmarker_task_path: Optional[str] = ""


class DeviceConfigRequest(BaseModel):
    device: str = 'auto'


class TransformConfigRequest(BaseModel):
    rotation: Optional[int] = 0
    flip_h: Optional[bool] = False
    flip_v: Optional[bool] = False


class KalmanConfigRequest(BaseModel):
    enabled: Optional[bool] = None
    process_noise: Optional[float] = None
    measurement_noise: Optional[float] = None
    max_missing_frames: Optional[int] = None


class RtspStartRequest(BaseModel):
    url: str
    fps: int = 25


class HikvisionStartRequest(BaseModel):
    device_index: int = 0
    width: int = 1280
    height: int = 720
    fps: int = 30


class HCNetSDKStartRequest(BaseModel):
    ip: str
    port: int = 8000
    username: str = "admin"
    password: str = ""
    channel: int = 1
    stream_type: int = 1
    fps: int = 25


class ProjectConfigRequest(BaseModel):
    project_id: int
    name: str
    task_type: str = 'detection'
    logic_mode: str = 'detection'
    steps_config: list = Field(default_factory=list)
    pipeline_config: dict = Field(default_factory=dict)
    events_config: list = Field(default_factory=list)
    counters_config: list = Field(default_factory=list)
    data_config: dict = Field(default_factory=dict)


# ============================================================
# 摄像头探测（Linux v4l2 + Windows DirectShow）
# ============================================================
_cameras_cache = {
    "cameras": [],
    "last_update": 0,
    "cache_duration": 60,
}


def _get_camera_name_linux(index):
    """Linux: 通过 sysfs 拿摄像头型号名"""
    try:
        name_path = f"/sys/class/video4linux/video{index}/name"
        if os.path.exists(name_path):
            with open(name_path, 'r') as f:
                return f.read().strip()
    except Exception:
        pass  # 设备不存在或权限不足，正常情况
    return None


def _get_video4linux_subdev_index(index):
    """Linux: 读 /sys/class/video4linux/videoN/index 拿 UVC 子设备序号.

    UVC 摄像头一般注册多个 V4L2 节点 (一个 capture, 一个 metadata 等),
    sysfs 里的 index 字段 = 0 表示主 capture 节点, >=1 是辅助节点 (metadata
    /VBI 之类不能 cv2.VideoCapture 出帧的). 老逻辑用 'V4L2 节点号 % 2 == 0'
    判主, 但 USB 热插拔后节点号可能是奇数 (如 video3+video5), 主节点会被
    错误过滤. sysfs 字段不依赖节点号顺序, 是稳定的判断依据.

    返回 None 表示读不到 (sysfs 不存在 / 权限不足), 让上层走兜底逻辑.
    """
    try:
        idx_path = f"/sys/class/video4linux/video{index}/index"
        if os.path.exists(idx_path):
            with open(idx_path, 'r') as f:
                return int(f.read().strip())
    except Exception:
        pass
    return None


def _detect_cameras_linux():
    """Linux: 通过 /dev/video* 快速检测摄像头，跳过当前正在被占用的设备"""
    import glob
    cameras = []
    video_devices = glob.glob("/dev/video*")

    active_index = None
    if video_manager.source_type == 'camera' and video_manager.capture is not None:
        active_index = video_manager.camera_index

    def extract_index(path):
        try:
            return int(path.replace("/dev/video", ""))
        except (ValueError, TypeError):
            return 999
    video_devices = sorted(video_devices, key=extract_index)

    for device in video_devices:
        try:
            index = int(device.replace("/dev/video", ""))
            name = _get_camera_name_linux(index)

            if name and "Virtual" in name:
                cameras.append({"index": index, "name": f"{name} (索引 {index})"})
            elif name:
                # v3.4.2: 用 sysfs 的 UVC 子设备 index 判定主 capture 节点
                # (=0), 不再依赖"V4L2 节点号必须偶数"的脆弱启发式. USB 热插拔
                # 后节点号可能是奇数 (如 video3+video5), 老逻辑会误过滤主节点
                # 导致前端摄像头列表少一个.
                subdev_idx = _get_video4linux_subdev_index(index)
                if subdev_idx is None:
                    # sysfs 拿不到 → 回退老的"偶数为主"启发式, 兼容老内核.
                    if index % 2 != 0:
                        continue
                elif subdev_idx != 0:
                    # 辅助节点 (metadata/VBI), 跳过
                    continue
                cameras.append({"index": index, "name": f"{name} (索引 {index})"})
            else:
                if index == active_index:
                    cameras.append({"index": index, "name": f"摄像头 {index} (使用中)"})
                    continue
                cap = cv2.VideoCapture(index)
                if cap.isOpened():
                    cap.release()
                    cameras.append({"index": index, "name": f"摄像头 {index}"})
        except Exception:
            continue

    return cameras


def _detect_cameras_windows():
    """Windows: DirectShow 探测前 5 个摄像头索引"""
    cameras = []
    current_camera_index = None
    if video_manager.source_type == 'camera' and video_manager.capture is not None:
        current_camera_index = video_manager.camera_index

    for i in range(5):
        if current_camera_index is not None and i == current_camera_index:
            cameras.append({
                "index": i,
                "name": f"摄像头 {i} (使用中)" if i > 0 else "默认摄像头 (索引 0, 使用中)"
            })
            continue

        try:
            cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
            if cap.isOpened():
                cameras.append({
                    "index": i,
                    "name": f"摄像头 {i}" if i > 0 else "默认摄像头 (索引 0)"
                })
                cap.release()
        except Exception:
            continue

    return cameras


# ============================================================
# /cameras + /gpu/*
# ============================================================
@router.get("/cameras")
def list_cameras(refresh: bool = False):
    """列出可用摄像头

    Args:
        refresh: 是否强制刷新缓存，默认使用缓存
    """
    import platform
    print(f"[API] /cameras 被调用, refresh={refresh}, platform={platform.system()}")
    current_time = time.time()

    if not refresh and _cameras_cache["cameras"] and \
       (current_time - _cameras_cache["last_update"]) < _cameras_cache["cache_duration"]:
        print(f"[API] 使用缓存, cameras={_cameras_cache['cameras']}")
        return {"cameras": _cameras_cache["cameras"], "cached": True}

    if platform.system() == "Linux":
        cameras = _detect_cameras_linux()
    else:
        cameras = _detect_cameras_windows()

    print(f"[API] 检测到USB摄像头: {cameras}")

    if not cameras:
        cameras = [{"index": 0, "name": "默认摄像头 (索引 0)"}]

    _cameras_cache["cameras"] = cameras
    _cameras_cache["last_update"] = current_time

    return {"cameras": cameras, "cached": False}


@router.get("/gpu/list")
def get_gpu_list():
    """获取可用的GPU设备列表"""
    import torch
    devices = [{"id": "cpu", "name": "CPU (中央处理器)", "type": "CPU"}]
    if torch.cuda.is_available():
        devices.insert(0, {"id": "auto", "name": "自动选择 (优先GPU)", "type": "AUTO"})
        for i in range(torch.cuda.device_count()):
            gpu_name = torch.cuda.get_device_name(i)
            memory_total = torch.cuda.get_device_properties(i).total_memory / (1024**3)
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
    """设置推理设备（需要重新加载模型生效）.

    Step 6 (feat/multi-model-roi-link):
      - 单模型 (router 中只有 main 且 main.model 已加载) → 走老路径 load_model
      - 多模型 (router 中除 main 外还有副 mi 加载) → 遍历每个 mi 重载到新设备
      - 任何 model 都没加载 → 仅写入设备配置, 留待下次加载
    """
    video_manager.device = req.device
    video_manager._save_device_config()

    router_obj = getattr(video_manager, '_router', None)
    loaded_specs = []
    if router_obj is not None:
        for mi in router_obj.models.values():
            if mi.model is not None and mi.model_path:
                loaded_specs.append({
                    'name': mi.name,
                    'model_path': mi.model_path,
                    'conf': mi.conf,
                    'iou': mi.iou,
                    'roi': mi.roi,
                    'schedule': {
                        'type': mi.schedule.type,
                        'n': mi.schedule.n,
                        'events': list(mi.schedule.events),
                    },
                    'class_filter': (sorted(mi.class_filter)
                                     if mi.class_filter is not None else None),
                    'priority': mi.priority,
                    'display_color': mi.display_color,
                    'use_half': mi.use_half,
                })

    if len(loaded_specs) >= 2 or (
        len(loaded_specs) == 1 and loaded_specs[0]['name'] != 'main'
    ):
        # 多模型 (含: 单纯副模型场景) → 遍历重载, 顺序保持 priority 高→低 由 router 决定
        video_manager.release_all_models()
        all_ok = True
        for spec in loaded_specs:
            ok = video_manager.load_model_into_slot(
                name=spec['name'],
                model_path=spec['model_path'],
                conf=spec['conf'],
                iou=spec['iou'],
                roi=spec['roi'],
                schedule=spec['schedule'],
                class_filter=spec['class_filter'],
                priority=spec['priority'],
                display_color=spec['display_color'],
                use_half=spec['use_half'],
                device=req.device,
            )
            all_ok = all_ok and ok
        if all_ok:
            # video_manager.current_device_info 可能是 None (例如 stub 加载或 mi
            # 加载时未更新 host 字段), 用 device 字符串兜底.
            dev_info = video_manager.current_device_info or {}
            dev_label = dev_info.get('name') or video_manager.device
            return {
                "status": "success",
                "message": f"已切换 {len(loaded_specs)} 个模型到 {dev_label}",
                "device": video_manager.device,
                "current_device_info": video_manager.current_device_info,
                "reloaded_models": [s['name'] for s in loaded_specs],
            }
        else:
            return {"status": "error", "message": "切换设备失败, 部分模型重载出错"}

    if video_manager.model is not None and video_manager.model_path:
        success = video_manager.load_model(video_manager.model_path)
        if success:
            dev_info = video_manager.current_device_info or {}
            dev_label = dev_info.get('name') or video_manager.device
            return {
                "status": "success",
                "message": f"已切换到 {dev_label}",
                "device": video_manager.device,
                "current_device_info": video_manager.current_device_info
            }
        else:
            return {"status": "error", "message": "切换设备失败，模型重新加载出错"}
    return {
        "status": "success",
        "message": f"设备已设置为 {req.device}，将在下次加载模型时生效",
        "device": video_manager.device,
        "current_device_info": video_manager.current_device_info
    }


# ============================================================
# /stream/* + /transform/* + /kalman/*
# ============================================================
@router.get("/stream/config")
def get_stream_config():
    return {
        "frame_limit_enabled": video_manager.frame_limit_enabled,
        "target_stream_fps": video_manager.target_stream_fps,
        "use_half": video_manager.use_half,
        "mediapipe_enabled": video_manager.mediapipe_enabled,
        "mediapipe_pose": video_manager.mediapipe_pose,
        "mediapipe_hands": video_manager.mediapipe_hands,
        "mediapipe_confidence": video_manager.mediapipe_confidence,
        "mediapipe_interval": video_manager._mp_process_interval,
        # v3.8.0 mp.solutions.hands 调优
        "mediapipe_model_complexity": int(getattr(video_manager, "mediapipe_model_complexity", 0)),
        "mediapipe_track_confidence": float(getattr(video_manager, "mediapipe_track_confidence", 0.5)),
        # v3.8.0 二段 pipeline
        "mediapipe_hand_detector_path": getattr(video_manager, "mediapipe_hand_detector_path", "") or "",
        "mediapipe_hand_detector_kind": getattr(video_manager, "mediapipe_hand_detector_kind", "v8") or "v8",
        "mediapipe_hand_detector_conf": float(getattr(video_manager, "mediapipe_hand_detector_conf", 0.25)),
        "mediapipe_hand_detector_iou": float(getattr(video_manager, "mediapipe_hand_detector_iou", 0.45)),
        "mediapipe_hand_detector_imgsz": int(getattr(video_manager, "mediapipe_hand_detector_imgsz", 640)),
        "mediapipe_hand_detector_class": int(getattr(video_manager, "mediapipe_hand_detector_class", -1)),
        "mediapipe_hand_roi_pad": float(getattr(video_manager, "mediapipe_hand_roi_pad", 0.3)),
        "mediapipe_landmarker_task_path": getattr(video_manager, "mediapipe_landmarker_task_path", "") or "",
        # v3.8.0 二段管线运行时状态: 给前端显示"基础模式 / 已启用 / 路径无效 / 加载失败"
        "mediapipe_two_stage_status": _compute_two_stage_status(video_manager),
    }


def _compute_two_stage_status(vm) -> Dict[str, Any]:
    """计算二段管线当前状态, 给前端显示徽章."""
    path = (getattr(vm, "mediapipe_hand_detector_path", "") or "").strip()
    if not path:
        return {"state": "baseline", "message": "未启用专用手部模型 (走基础 MediaPipe)"}
    if not os.path.exists(path):
        return {"state": "path_invalid", "message": f"模型文件不存在: {path}"}
    # path 有效, 看运行时是否真的加载成功
    overlay = getattr(vm, "mp_overlay", None)
    # _mp_draw 在 overlay.init() 首次调用时才会赋值, None = 还没尝试 init
    overlay_initialized = overlay is not None and getattr(overlay, "_mp_draw", None) is not None
    if not overlay_initialized:
        return {"state": "pending", "message": "已配置, 等待启用 MediaPipe 后首次加载 (开启检测画面后生效)"}
    if getattr(overlay, "_two_stage_active", False):
        return {"state": "active", "message": "专用手部模型已启用 (二段管线)"}
    return {"state": "load_failed", "message": "模型存在但加载失败, 已回退基础模式 (看后端日志)"}


@router.post("/stream/config")
def set_stream_config(req: StreamConfigRequest):
    """设置视频流配置（帧率限制 + FP16 + MediaPipe）"""
    video_manager.frame_limit_enabled = req.frame_limit_enabled
    video_manager.target_stream_fps = max(1, min(120, req.target_stream_fps))
    video_manager.use_half = req.use_half

    mp_was_enabled = video_manager.mediapipe_enabled
    old_conf = video_manager.mediapipe_confidence
    old_complexity = int(getattr(video_manager, "mediapipe_model_complexity", 0))
    old_track_conf = float(getattr(video_manager, "mediapipe_track_confidence", 0.5))
    old_detector_path = getattr(video_manager, "mediapipe_hand_detector_path", "") or ""
    old_detector_kind = getattr(video_manager, "mediapipe_hand_detector_kind", "v8") or "v8"
    video_manager.mediapipe_enabled = req.mediapipe_enabled
    video_manager.mediapipe_pose = req.mediapipe_pose
    video_manager.mediapipe_hands = req.mediapipe_hands
    video_manager.mediapipe_confidence = max(0.1, min(1.0, req.mediapipe_confidence))
    video_manager._mp_process_interval = max(1, min(10, req.mediapipe_interval))
    # v3.8.0 mp.solutions.hands 调优 (老前端不发这俩字段时保留当前值, 不强制重置 default)
    if req.mediapipe_model_complexity is not None:
        video_manager.mediapipe_model_complexity = max(0, min(1, int(req.mediapipe_model_complexity)))
    if req.mediapipe_track_confidence is not None:
        video_manager.mediapipe_track_confidence = max(0.05, min(0.95, float(req.mediapipe_track_confidence)))
    # v3.8.0 二段 pipeline 字段, 都加 sanity clip
    new_path = (req.mediapipe_hand_detector_path or "").strip()
    new_kind = (req.mediapipe_hand_detector_kind or "v8").strip()
    if new_kind not in ("v5", "v8"):
        new_kind = "v8"
    video_manager.mediapipe_hand_detector_path = new_path
    video_manager.mediapipe_hand_detector_kind = new_kind
    video_manager.mediapipe_hand_detector_conf = max(0.05, min(0.95, float(req.mediapipe_hand_detector_conf or 0.25)))
    video_manager.mediapipe_hand_detector_iou = max(0.1, min(0.9, float(req.mediapipe_hand_detector_iou or 0.45)))
    video_manager.mediapipe_hand_detector_imgsz = max(160, min(1280, int(req.mediapipe_hand_detector_imgsz or 640)))
    video_manager.mediapipe_hand_detector_class = int(req.mediapipe_hand_detector_class if req.mediapipe_hand_detector_class is not None else -1)
    video_manager.mediapipe_hand_roi_pad = max(0.0, min(2.0, float(req.mediapipe_hand_roi_pad or 0.3)))
    video_manager.mediapipe_landmarker_task_path = (req.mediapipe_landmarker_task_path or "").strip()

    conf_changed = abs(video_manager.mediapipe_confidence - old_conf) > 0.01
    complexity_changed = video_manager.mediapipe_model_complexity != old_complexity
    track_changed = abs(video_manager.mediapipe_track_confidence - old_track_conf) > 0.01
    detector_changed = (new_path != old_detector_path) or (new_kind != old_detector_kind)
    if not req.mediapipe_enabled and mp_was_enabled:
        video_manager._release_mediapipe()
    elif (conf_changed or complexity_changed or track_changed or detector_changed) and req.mediapipe_enabled:
        # 任一参数变化 -> 释放重载
        video_manager._release_mediapipe()

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
        "mediapipe_interval": video_manager._mp_process_interval,
        "mediapipe_model_complexity": video_manager.mediapipe_model_complexity,
        "mediapipe_track_confidence": video_manager.mediapipe_track_confidence,
        "mediapipe_hand_detector_path": video_manager.mediapipe_hand_detector_path,
        "mediapipe_hand_detector_kind": video_manager.mediapipe_hand_detector_kind,
        "mediapipe_hand_detector_conf": video_manager.mediapipe_hand_detector_conf,
        "mediapipe_hand_detector_iou": video_manager.mediapipe_hand_detector_iou,
        "mediapipe_hand_detector_imgsz": video_manager.mediapipe_hand_detector_imgsz,
        "mediapipe_hand_detector_class": video_manager.mediapipe_hand_detector_class,
        "mediapipe_hand_roi_pad": video_manager.mediapipe_hand_roi_pad,
        "mediapipe_landmarker_task_path": video_manager.mediapipe_landmarker_task_path,
    }


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


# ============================================================
# /camera/* + /rtsp/*
# ============================================================
@router.post("/camera/start")
def start_camera(req: CameraStartRequest, channel: int = Query(0)):
    """启动摄像头"""
    try:
        mgr = _get_mgr(channel)
        mgr.start_camera(
            device_index=req.device_index,
            width=req.width,
            height=req.height,
            fps=req.fps,
            auto_exposure=req.auto_exposure,
            exposure_value=req.exposure_value,
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


# ============================================================
# /hikvision/* + /hcnetsdk/*
# ============================================================
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
        return {"cameras": cameras, "available": True, "count": len(cameras)}
    except Exception as e:
        hik_log(f"枚举海康相机失败: {e}", "ERROR")
        import traceback
        hik_log(traceback.format_exc(), "ERROR")
        return {"cameras": [], "available": False, "message": str(e)}


@router.post("/hikvision/start")
def start_hikvision_camera(req: HikvisionStartRequest, channel: int = Query(0)):
    """启动海康工业相机"""
    hik_log(f"API /hikvision/start 收到请求: device_index={req.device_index}, "
            f"{req.width}x{req.height}@{req.fps}fps, ch={channel}")
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


# ============================================================
# /video/* + /image/*
# ============================================================
@router.post("/video/upload")
async def upload_video(file: UploadFile = File(...)):
    """上传视频文件（同名文件自动覆盖，清理旧的重复副本）"""
    allowed_ext = {'.mp4', '.avi', '.mov', '.mkv'}
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in allowed_ext:
        raise HTTPException(status_code=400, detail="不支持的视频格式")

    original_name = file.filename
    file_path = os.path.join(settings.VIDEO_UPLOAD_DIR, original_name)

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

    return {"status": "success", "file_path": file_path, "file_name": original_name}


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


# ============================================================
# /detection/*
# ============================================================
@router.post("/detection/start")
def start_detection(req: DetectionStartRequest, channel: int = Query(0)):
    """开始检测.

    Step 6 (feat/multi-model-roi-link):
      - 优先解析 req.models[]: 多模型 payload, 逐个 load_model_into_slot
      - 否则走老的单模型路径 (req.model_path)
    v3.7.0 (session-id): req.session_name 用户自定义会话标识, 经 _clean_session_name 校验.
    """
    from backend.api.source_session_lifecycle_mixin import _clean_session_name
    try:
        cleaned_session_name = _clean_session_name(req.session_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        mgr = _get_mgr(channel)
        from backend.api.channel_manager import channel_manager
        device = getattr(mgr, 'device', 'auto') or 'auto'

        if req.models:
            # 多模型路径: 释放所有旧 slot (含 main), 再按列表逐个加载
            mgr.release_all_models()
            failed = []
            for spec in req.models:
                ok = mgr.load_model_into_slot(
                    name=spec.name,
                    model_path=spec.model_path,
                    conf=spec.conf,
                    iou=spec.iou,
                    roi=spec.roi,
                    schedule=spec.schedule,
                    class_filter=spec.class_filter,
                    priority=spec.priority,
                    display_color=spec.display_color,
                    use_half=spec.use_half,
                    device=device,
                    original_pt_path=spec.original_pt_path,
                )
                if not ok:
                    failed.append(spec.name)
            if failed:
                raise HTTPException(
                    status_code=500,
                    detail=f"加载模型失败: {failed}",
                )
            # 主模型 model_path 作为 start_detection 的 path 参数 (向后兼容签名)
            main_spec = next(
                (s for s in req.models if s.name == 'main'),
                req.models[0] if req.models else None,
            )
            main_path = main_spec.model_path if main_spec else None
        else:
            # 老路径 (单模型, 完全等价于 Step 6 之前)
            mgr.conf_threshold = req.conf
            mgr.iou_threshold = req.iou
            if req.model_path:
                channel_manager.load_model_for_channel(channel, req.model_path, device)
            elif mgr.model is None and getattr(mgr, 'source_type', None) != 'synthetic':
                channel_manager._propagate_model(channel)
            main_path = req.model_path

        mgr.start_detection(main_path)

        if mgr.project_config and mgr.project_config.get('id'):
            session_info = mgr.start_session(mgr.project_config['id'], name=cleaned_session_name)
            if session_info:
                return {
                    "status": "success",
                    "message": f"检测已启动 (ch{channel})",
                    "session_id": session_info.get('session_id'),
                    "session_uuid": session_info.get('session_uuid'),
                    "session_name": session_info.get('session_name'),
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


@router.post("/detection/reset-periodic")
def reset_periodic_action(
    channel: int = Query(0),
    rule_id: Optional[str] = Query(None, description="规则 id；不传则重置所有"),
):
    """单独重置周期性强制动作计数器，不动产量统计 / cycle 状态。

    任何时候都可调用（检测运行中也可）。rule_id 不传 = 重置该通道全部规则。
    """
    mgr = _get_mgr(channel)
    if not hasattr(mgr, 'reset_periodic_counter'):
        raise HTTPException(status_code=500, detail="该通道不支持周期性强制动作")
    result = mgr.reset_periodic_counter(rule_id)
    return {"status": "success", "reset": result.get('reset', [])}


@router.get("/detection/results")
def get_detection_results(channel: int = Query(0)):
    """获取检测结果（含 MES/操作员/tracking 等聚合信息）"""
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
    last_step_durations = {}
    for lbl, hist in mgr.step_durations_history.items():
        if hist:
            avg_step_durations[lbl] = round(sum(hist) / len(hist), 2)
            last_step_durations[lbl] = hist[-1]

    # v3.5.x: PT 合并档（同 label 同周期多次出现的 SUM）
    cycle_sum_step_durations = getattr(mgr, 'step_cycle_durations', {}).copy()
    avg_cycle_sum_step_durations = {}
    last_cycle_sum_step_durations = {}
    for lbl, hist in getattr(mgr, 'step_cycle_durations_history', {}).items():
        if hist:
            avg_cycle_sum_step_durations[lbl] = round(sum(hist) / len(hist), 2)
            last_cycle_sum_step_durations[lbl] = hist[-1]

    # v3.8.x: in-flight 实时 PT ── 让前端 PT 列在步骤还在画面里时就能显示渐增数字,
    # 而不是等到 "步骤消失 + disappear_delay 超时" 写入权威值才显示。
    #
    # 关键设计 (v3.8.x 修订): in-flight 走**独立字段** step_inflight_durations,
    # **不污染** step_durations / cycle_sum_step_durations 这些"权威完成值"。
    # 前端语义因此清晰:
    #   - 步骤在画面里, 没真完成 → step_inflight_durations 有值, cycle_sum_step_durations 无值
    #     → PT 列 fallback 显示 in-flight (渐增), 状态 = '待检测'(不标 OK)
    #   - 步骤离开画面 + disappear_delay 写入权威值 → cycle_sum_step_durations 有值
    #     → PT 列显示权威值 (停止涨), 状态 = '已检测' / OK
    #
    # 历史版本曾把 in-flight 直接覆盖到 step_durations / cycle_sum 字段, 导致
    # "PT 出现 = 标 OK" 而 PT 还在涨, 客户投诉 "PT 没稳定就标合格了"。
    step_inflight_durations = {}
    try:
        _live_last = getattr(mgr, 'step_last_seen', None) or {}
        _live_start = getattr(mgr, 'step_start_time', None) or {}
        _live_cycle = getattr(mgr, 'current_cycle_steps', None) or []
        for _lbl, _last_t in _live_last.items():
            if _lbl not in _live_cycle:
                continue
            _start_t = _live_start.get(_lbl, _last_t)
            _live_dur = _last_t - _start_t
            if _live_dur <= 0:
                continue
            step_inflight_durations[_lbl] = round(_live_dur, 2)
    except Exception as _e:
        print(f"[API] in-flight PT 收集异常 (ch{channel}): {_e}")

    last_cycle_time = mgr.cycle_times[-1] if mgr.cycle_times else 0
    last_cycle_time_with_ng = 0
    _all_ct_for_last = []
    if mgr.cycle_times:
        _all_ct_for_last.append(mgr.cycle_times[-1])
    if mgr.ng_cycle_times:
        _all_ct_for_last.append(mgr.ng_cycle_times[-1])
    if _all_ct_for_last:
        last_cycle_time_with_ng = max(_all_ct_for_last)

    current_cycle_time = 0
    if getattr(mgr, 'cycle_start_time', None) and getattr(mgr, 'is_detecting', False):
        try:
            current_cycle_time = round(time.time() - mgr.cycle_start_time, 2)
        except Exception:
            current_cycle_time = 0

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
        # v3.8.x: in-flight 实时 PT (步骤还在画面里, 持续涨), 独立字段, 不污染权威值。
        # 前端用法: PT 列 fallback 显示, 状态判定不取这个 (否则"没稳定就标 OK")。
        "step_inflight_durations": step_inflight_durations,
        "avg_step_durations": avg_step_durations,
        "last_step_durations": last_step_durations,
        # v3.5.x: PT 合并档 — 当前周期内 SUM / 最近一周期 SUM / 历史周期 SUM 平均
        "cycle_sum_step_durations": cycle_sum_step_durations,
        "last_cycle_sum_step_durations": last_cycle_sum_step_durations,
        "avg_cycle_sum_step_durations": avg_cycle_sum_step_durations,
        "step_intervals": mgr.step_intervals.copy(),
        "counters": mgr.counters.copy(),
        "recent_events": recent_events,
        "ng_step_cycle_counts": mgr.ng_step_cycle_counts.copy(),
        "average_cycle_time": avg_cycle_time,
        "average_cycle_time_with_ng": avg_cycle_time_with_ng,
        "last_cycle_time": last_cycle_time,
        "last_cycle_time_with_ng": last_cycle_time_with_ng,
        "current_cycle_time": current_cycle_time,
        "current_cycle_steps": list(mgr.current_cycle_steps),
        # v3.8.x: 透 cycle_id 给前端做"周期切换"边界检测信号。
        # 用途: 前端 polling 收到 cycle_id 变化 (上一周期 → null → 下一周期 / 直接换号)
        # 就立刻清掉步骤表 status/cycleResult/cycle_sum 残留, 解决客户反馈
        # "上一周期 OK 显示在新周期前 1-2 步做完之前一直挂着" 的视觉残留 bug。
        # 周期间隙 (end_cycle 完成、start_cycle 未触发) current_cycle_id 为 None,
        # 前端识别 None → 新值 也按"周期切换"处理。
        "current_cycle_id": getattr(mgr, 'current_cycle_id', None),
        "current_cycle_uuid": getattr(mgr, 'current_cycle_uuid', None),
        "backup_covered_labels": [
            mgr.step_backup_map[b]
            for b in mgr.backup_steps_seen_in_cycle
            if b in mgr.step_backup_map
        ]
    }

    result['model_task'] = getattr(mgr, 'model_task', 'detect')

    # Step 6 (feat/multi-model-roi-link): 透出多模型快照 (前端 Monitor 用 display_color
    # 给检测框上色 / 用 fps_inference / latency 渲染 per-model 性能).
    # 单模型场景仍会返回 [{'name':'main', ...}] 一项 (main slot 永远存在).
    try:
        if hasattr(mgr, '_router') and mgr._router is not None:
            result['models'] = mgr._router.stats_snapshot()
    except Exception as _e:
        print(f"[API] /detection/results 取 models 快照失败: {_e}")
        result['models'] = []

    # v3.6.x: per_item 模式运行时状态 (含个体覆盖率, 给前端 Monitor 可视化).
    # 非 per_item 项目时返回 None, 前端按 None 处理即可.
    try:
        if hasattr(mgr, 'get_per_item_state'):
            result['per_item_state'] = mgr.get_per_item_state()
    except Exception as _e:
        print(f"[API] /detection/results 取 per_item_state 失败: {_e}")
        result['per_item_state'] = None

    # 多通道场景下前端不能用 currentProject (顶部下拉框单一值) 兜底,
    # 必须每帧带上 tracking 过滤所需的字段, 否则容器模式表格里"箱子"行
    # 过滤不掉 (前端 Monitor/index.vue 的 _trkExpectedLabels 依赖这里).
    _pcfg = (mgr.project_config.get('pipeline_config', {}) or {}) \
        if mgr.project_config else {}
    result['project_config'] = {
        'project_id': mgr.project_config.get('id') if mgr.project_config else None,
        'project_name': mgr.project_config.get('name', '') if mgr.project_config else '',
        'logic_mode': mgr.project_config.get('logic_mode', 'detection') if mgr.project_config else 'detection',
        'steps_config': mgr.project_config.get('steps_config', []) if mgr.project_config else [],
        'pipeline_config': {
            'counting_expected_items': _pcfg.get('counting_expected_items', {}),
            'tracking_container_label': _pcfg.get('tracking_container_label', ''),
        },
    }

    logic_mode = mgr.project_config.get('logic_mode') if mgr.project_config else None

    # v3.5.0: tracking 子树始终返回完整字段（非 tracking 模式下大多为空 dict / None），
    # 给自定义导出系统的 live.tracking.* 字段提供数据源。
    # 旧字段位置不变，前端 Monitor 现有读取逻辑兼容。
    tracked_objs = {}
    try:
        for tid, obj in (mgr._tracking_objects or {}).items():
            tracked_objs[str(tid)] = {
                'class_name': obj.get('class_name'),
                'display_id': obj.get('display_id'),
                'bbox': obj.get('bbox'),
                'order_idx': obj.get('order_idx', 0),
            }
    except Exception:
        tracked_objs = {}

    tracking_data = {
        # 全局开关
        'cycle_active': bool(getattr(mgr, '_tracking_cycle_active', False)),
        'container_mode': bool(getattr(mgr, '_container_mode', False)),
        # Stack 模式（堆叠）
        'stack_states': dict(getattr(mgr, '_stack_state', {}) or {}),
        'stack_counters': dict(getattr(mgr, '_stack_counters', {}) or {}),
        # Tracking 模式（物品清点）
        'active_count': len(getattr(mgr, '_tracking_objects', {}) or {}),
        'locked_count': len(getattr(mgr, '_tracking_locked_ids', {}) or {}),
        'lost_count': len(getattr(mgr, '_tracking_recently_lost', {}) or {}),
        'prev_count': int(getattr(mgr, '_tracking_prev_count', 0) or 0),
        'was_complete': bool(getattr(mgr, '_tracking_was_complete', False)),
        'class_counters': dict(getattr(mgr, '_tracking_class_counters', {}) or {}),
        'tracked_objects': tracked_objs,
        'item_checklist': dict(getattr(mgr, '_tracking_item_checklist', {}) or {}),
        # Event Counter 模式（动作计数）
        'event_counters': dict(getattr(mgr, '_event_counters', {}) or {}),
        'event_states': dict(getattr(mgr, '_event_state', {}) or {}),
        # Container 模式（容器结算）
        'box_counter': int(getattr(mgr, '_box_counter', 0) or 0),
        'boxes': {},
        'settled_boxes': 0,
        'settled_ok': 0,
        'settled_ng': 0,
        # Scan-D 模式
        'scan_d_armed_box': getattr(mgr, '_scan_d_armed_box', None),
    }

    if tracking_data['container_mode']:
        try:
            for box_did, bs in (mgr._box_objects or {}).items():
                tracking_data['boxes'][str(box_did)] = {
                    'bbox': bs.get('bbox'),
                    'is_complete': bs.get('is_complete'),
                    'item_counts': dict(bs.get('item_class_counts', {}) or {}),
                }
            tracking_data['settled_boxes'] = len(mgr._box_settled_results or [])
            tracking_data['settled_ok'] = sum(
                1 for r in (mgr._box_settled_results or []) if r.get('is_complete')
            )
            tracking_data['settled_ng'] = (
                tracking_data['settled_boxes'] - tracking_data['settled_ok']
            )
        except Exception:
            pass

    result['tracking'] = tracking_data

    # v3.5.0: 周期性强制动作进度（前端 Monitor 显示"距下次清洁还有 X 轮"）
    try:
        if hasattr(mgr, 'get_periodic_actions_status'):
            pa_status = mgr.get_periodic_actions_status()
            if pa_status:
                result['periodic_actions'] = pa_status
    except Exception:
        pass

    # MES 实时数据 + 录像异常详情 (录像异常不依赖 MES 开关)
    mes_data = {}
    try:
        recording_failures = []
        try:
            recording_failures = mgr.get_recording_failures(limit=20)
        except Exception:
            recording_failures = []
        if recording_failures:
            mes_data['recording_failures'] = recording_failures
    except Exception:
        pass

    if mgr._mes_hook and mgr._mes_hook.enabled:
        try:
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
            # v3.4.2 hotfix: 把"扫码禁用"状态推到前端, 让 Pinia store 自动同步.
            # 否则 backend reload (从 disk 恢复 _disabled_channels) / 多终端联动
            # 时, 前端 store 不知道, 守门失效, 误弹"未绑码" / 显示信息条等.
            mes_data['scan_disabled'] = bool(
                mgr._mes_hook.is_channel_scan_disabled(mgr.channel_id)
            )
        except Exception:
            pass

    # v3.5.2: 透出"系统是否注册任何扫码器(含虚拟)"标记 - 放在 MES Hook
    # 启用判断之外, 让前端任何模式下都能据此屏蔽"⚠ 未绑码"信息条/toast.
    try:
        if mgr._mes_hook is not None:
            mes_data['scanner_present'] = bool(
                mgr._mes_hook.has_any_scanner_present()
            )
    except Exception:
        pass

    if mes_data:
        result['mes'] = mes_data

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


# ============================================================
# v3.8+: per_item dev-only mock 注入 (前端可视化调样用, 真模型上线后可删)
#
# 安全守门 (与项目现有 ENABLE_API_DOCS 同语义, 默认"关")
#   ENABLE_DEV_MOCKS 未设 或 ≠ '1' → 端点返回 403, 注入失败
#   ENABLE_DEV_MOCKS = '1'        → 允许注入
#
# 启动示例:
#   ENABLE_DEV_MOCKS=1 python -m uvicorn backend.main:app --reload --port 8001
# ============================================================
def _dev_mocks_enabled() -> bool:
    """开发 mock 守门: 出厂版默认关闭, 防止客户机器被意外注入假数据."""
    return os.environ.get("ENABLE_DEV_MOCKS", "0") == "1"


@router.post("/detection/per-item-mock")
def inject_per_item_mock(channel: int = Query(0),
                         total_items: int = Query(12),
                         covered_items: int = Query(5),
                         cycle_age_seconds: float = Query(3.5),
                         ok_count: int = Query(23),
                         ng_count: int = Query(3),
                         inject_stats: bool = Query(True),
                         inject_ng: bool = Query(False),
                         ng_cycle_duration: float = Query(9.3)):
    """开发用: 给当前 mgr 注入一个激活态 per_item_state + 配套统计 mock.

    ⚠ 必须用 ENABLE_DEV_MOCKS=1 启动后端, 否则端点 403.
    必须先 /detection/set-project 把 logic_mode 设为 per_item, 才能注入成功.

    参数:
        total_items: 当前周期物件总数
        covered_items: 已覆盖件数
        cycle_age_seconds: 周期已运行秒数
        ok_count / ng_count: 历史合格/不良总数 (供右上角统计卡片 / 饼图 / 仪表盘消费)
        inject_stats: 是否同时写入 counters/cycle_time 等统计数据 (默认 True)
        inject_ng: 是否同时注入一条"上次 NG"详情 (供 PerItemPanel 底栏红条展示, 默认 False)
        ng_cycle_duration: 模拟的上次 NG 周期耗时 (秒, 仅 inject_ng=true 时生效)
    """
    if not _dev_mocks_enabled():
        raise HTTPException(
            status_code=403,
            detail="dev mock 端点已禁用. 启动后端时设置 ENABLE_DEV_MOCKS=1 才能调用 (出厂版必须保持禁用).",
        )
    import time
    mgr = _get_mgr(channel)
    if not getattr(mgr, '_per_item_config', None):
        raise HTTPException(status_code=400, detail="当前 channel 未启用 per_item 模式, 请先 set-project")
    if not getattr(mgr, '_per_item_steps', None):
        raise HTTPException(status_code=400, detail="per_item 步骤列表为空, 请检查 steps_config[i].per_item")

    step = mgr._per_item_steps[0]
    # 清空旧状态
    step.items.clear()
    step.next_item_id = 1

    # 造 N 颗"螺丝", 排成 3 行 × ceil(N/3) 列网格, 模拟工件上的螺丝阵列
    import math
    cols = max(1, math.ceil(total_items / 3))
    cell_w = 0.95 / cols
    cell_h = 0.85 / 3
    box_w = cell_w * 0.55
    box_h = cell_h * 0.55

    now = time.time()
    cycle_start = now - max(0.0, cycle_age_seconds)
    from backend.api.source_per_item_mixin import _PerItemItemState

    for idx in range(total_items):
        row = idx // cols
        col = idx % cols
        cx = 0.05 + (col + 0.5) * cell_w
        cy = 0.10 + (row + 0.5) * cell_h
        x = max(0.0, cx - box_w / 2)
        y = max(0.0, cy - box_h / 2)
        bbox = (x, y, box_w, box_h)
        iid = step.next_item_id
        step.next_item_id += 1
        st = _PerItemItemState(iid, bbox, frame_id=120, ts=now)
        if idx < covered_items:
            st.covered = True
            st.first_covered_at = cycle_start + (idx + 1) * 0.4
            st.consecutive_overlap_frames = step.sustain_frames
        step.items[iid] = st

    step.locked_count = total_items
    step.completed = (covered_items >= total_items)

    sess = getattr(mgr, '_per_item_session', None)
    if sess is not None:
        sess.cycle_active = True
        sess.cycle_start_time = cycle_start
        sess.cycle_start_frame_id = 60
        sess.frame_id = 120
        sess.stability_buffer.clear() if hasattr(sess.stability_buffer, 'clear') else None

    # ──── 配套统计 mock (右上角计数器 / 饼图 / 仪表盘 / 历史 cycle 时间) ────
    stats_injected = {}
    if inject_stats:
        total_count = ok_count + ng_count
        # 三个内置计数器, 前端 BUILTIN_THREE 直接消费
        mgr.counters['合格总数'] = ok_count
        mgr.counters['不良总数'] = ng_count
        mgr.counters['总产量'] = total_count

        # 历史 cycle 时间 (后端 detection/results 从 mgr.cycle_times 算 avg/last)
        try:
            import random
            random.seed(42)
            if hasattr(mgr, 'cycle_times') and hasattr(mgr.cycle_times, 'clear'):
                mgr.cycle_times.clear()
                for _ in range(min(max(1, ok_count), 30)):
                    mgr.cycle_times.append(round(7.0 + random.random() * 2.5, 2))
            if hasattr(mgr, 'ng_cycle_times') and hasattr(mgr.ng_cycle_times, 'clear'):
                mgr.ng_cycle_times.clear()
                for _ in range(min(max(1, ng_count), 10)):
                    mgr.ng_cycle_times.append(round(4.5 + random.random() * 3.5, 2))
        except Exception as _e:
            print(f"[per-item-mock] 注入 cycle_times 失败: {_e}")

        stats_injected = {
            'ok_count': ok_count,
            'ng_count': ng_count,
            'total_count': total_count,
            'yield_rate': round(ok_count / max(1, total_count) * 100, 1),
        }

    # ──── NG 详情 mock (PerItemPanel 底栏红条 / 右侧"逐件实时反馈"用) ────
    ng_injected = None
    if inject_ng:
        # 用当前 step 的未覆盖件作为漏件清单, 自然贴合视觉
        steps_failed = []
        missing_total = 0
        for step in mgr._per_item_steps:
            missing_ids = [iid for iid, st in step.items.items() if not st.covered]
            if missing_ids:
                steps_failed.append({
                    'step_label': step.step_label,
                    'display_label': step.display_label,
                    'covered_count': step.covered_count(),
                    'total': len(step.items),
                    'missing_item_ids': missing_ids,
                })
                missing_total += len(missing_ids)
        reason = '; '.join(
            f"[{d['display_label']}] 未完成({d['covered_count']}/{d['total']})"
            for d in steps_failed
        ) or '逐件覆盖未完成'
        mgr._per_item_last_ng_detail = {
            'reason_summary': reason,
            'cycle_duration_sec': round(float(ng_cycle_duration), 2),
            'settled_at': now,
            'missing_total': missing_total,
            'steps_failed': steps_failed,
        }
        ng_injected = {
            'missing_total': missing_total,
            'cycle_duration_sec': round(float(ng_cycle_duration), 2),
            'steps_failed_count': len(steps_failed),
        }
    else:
        # 没要求注入 NG 时清掉旧痕迹, 避免污染下次 OK 流程的视觉
        if hasattr(mgr, '_per_item_last_ng_detail'):
            mgr._per_item_last_ng_detail = None

    return {
        "status": "success",
        "channel": channel,
        "injected": {
            "total": total_items,
            "covered": covered_items,
            "cycle_age_seconds": cycle_age_seconds,
            "stats": stats_injected,
            "ng": ng_injected,
        },
        "snapshot": mgr.get_per_item_state(),
    }


# ============================================================
# /status + /health
# ============================================================
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
    """获取系统健康状态：用于监控线程运行状态和 GPU 资源使用情况"""
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


# ============================================================
# MES 配套：rebind 选择 / 清除扫码
# ============================================================
@router.post("/detection/rebind")
def resolve_rebind(action: str = "new", channel: int = 0):
    """manual rebind 模式：用户选择继续当前工件(continue)或扫新工件(new)"""
    from backend.api.channel_manager import channel_manager
    mgr = channel_manager.get(channel)
    if mgr._mes_hook:
        mgr._mes_hook.resolve_rebind(mgr.channel_id, action)
        return {"status": "ok", "action": action}
    return {"status": "error", "message": "MES 未启用"}


@router.post("/detection/clear_pending_scan")
def clear_pending_scan(channel: int = 0, force: bool = False):
    """清除该工位的"待检/最近扫码"状态，让工人重新扫码。

    v2.7.16: 配合前端 Monitor "未扫码/扫到码" 卡片右侧的"清除本次扫码"按钮。

    - force=False (默认): 仅清未绑入 cycle 的扫码状态；
      已经绑到 cycle 的工件不动，返回 status=warn 提示。
    - force=True: 连同当前正在检测的工件一并作废，
      工件 status 回退到 queued，cycle 仍会自然结算但不计入 MES。
    """
    from backend.api.channel_manager import channel_manager
    mgr = channel_manager.get(channel)
    if not mgr._mes_hook:
        return {"status": "error", "message": "MES 未启用"}
    cleared = mgr._mes_hook.clear_pending_scan(mgr.channel_id, force=force)
    # v2.7.16: 同时清扫码器本身的去重缓存 (last_scan / last_scan_time),
    # 否则用户点了清除后重扫同码会被 scanner.dedup_interval_sec 静默吞掉.
    try:
        from backend.services.scanner import get_scanner_service
        scanners_cleared = get_scanner_service().clear_last_scan(mgr.channel_id)
        if scanners_cleared:
            cleared["scanner_dedup_reset"] = scanners_cleared
            print(f"[Scanner] clear_pending_scan(ch={channel}) 同步重置去重缓存: {scanners_cleared}",
                  flush=True)
    except Exception as e:
        print(f"[Scanner] clear_pending_scan 重置 last_scan 失败: {e}", flush=True)
    if cleared.get("force_race_lost"):
        return {
            "status": "warn",
            "message": "操作来不及：本次工件已经结算完成，已计入 MES/工单",
            "cleared": cleared,
        }
    if cleared.get("force_canceled_inspecting"):
        return {
            "status": "ok",
            "message": "已作废本次工件检测，cycle 结束后不会计入 MES/工单",
            "cleared": cleared,
        }
    if not force and cleared.get("inspecting_workpiece_id"):
        return {
            "status": "warn",
            "message": "本次工件已开始检测；如需作废请确认后强制清除",
            "cleared": cleared,
        }
    return {"status": "ok", "cleared": cleared}


@router.post("/detection/recording-failures/clear")
def clear_recording_failures(channel: int = 0):
    """清空该工位录像异常详情列表（仅影响前端详情展示，不影响业务数据）。"""
    mgr = _get_mgr(channel)
    try:
        count = mgr.clear_recording_failures()
        return {"status": "ok", "cleared_count": count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
