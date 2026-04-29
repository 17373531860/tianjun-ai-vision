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
from typing import Optional

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


class DetectionStartRequest(BaseModel):
    model_path: str
    conf: float = 0.25
    iou: float = 0.45


class StreamConfigRequest(BaseModel):
    frame_limit_enabled: bool = False
    target_stream_fps: int = 30
    use_half: bool = False
    mediapipe_enabled: bool = False
    mediapipe_pose: bool = True
    mediapipe_hands: bool = True
    mediapipe_confidence: float = 0.7
    mediapipe_interval: int = 2


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
                # 物理摄像头通常成对出现，只取偶数索引避免重复
                if index % 2 != 0:
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
    """设置推理设备（需要重新加载模型生效）"""
    video_manager.device = req.device
    video_manager._save_device_config()
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
