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

from fastapi import Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.core.auth_deps import require_perm, get_current_user, CurrentUser
from backend.core.config import settings
from backend.db.database import get_db

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


# ==================== B6 步骤截图按内容哈希去重 (默认关) ====================
def _apply_screenshot_dedup(result: dict, known_shots: str) -> None:
    """对 /detection/results 的 step_screenshots 做内容指纹去重 (原地修改 result)。

    痛点: 该接口每 ~150ms 轮询都把每步 base64 缩略图整包回传, 而截图只在步骤切换时
          才变, 绝大多数轮询是在重发完全相同的大字符串。
    机制: 前端把它已持有的指纹以 'label:md5,label:md5' 形式经 known_shots 传入 —
          内容与客户端一致的截图从 step_screenshots 省略 (前端 Object.assign 合并会
          保留旧图), 始终回传 step_screenshot_hashes 让前端更新本地指纹。
    安全: 纯优化, 任何异常都回退为"原样不动 result", 绝不影响主数据。
    仅当路由收到 known_shots (非 None) 才调用本函数; 不传 = 与旧版字节级一致。
    """
    try:
        import hashlib
        shots = result.get('step_screenshots') or {}
        cur_hashes = {
            lbl: hashlib.md5(
                (b64 if isinstance(b64, str) else str(b64)).encode('utf-8')
            ).hexdigest()
            for lbl, b64 in shots.items()
            if b64
        }
        client_known = {}
        for pair in known_shots.split(','):
            if ':' in pair:
                k, _, v = pair.partition(':')
                k, v = k.strip(), v.strip()
                if k:
                    client_known[k] = v
        result['step_screenshots'] = {
            lbl: b64
            for lbl, b64 in shots.items()
            if b64 and cur_hashes.get(lbl) != client_known.get(lbl)
        }
        result['step_screenshot_hashes'] = cur_hashes
    except Exception as _e:
        print(f"[API] /detection/results 截图去重失败(已回退全量): {_e}")


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
    # v3.32.0 自定义纯色骨架样式 (default None: 老前端不带字段时保留 host 当前值)
    mediapipe_custom_style: Optional[bool] = None
    mediapipe_pose_color: Optional[str] = None
    # 关键点颜色与连线颜色分开配 ('' = 跟随连线颜色)
    mediapipe_pose_point_color: Optional[str] = None
    mediapipe_pose_thickness: Optional[int] = None
    mediapipe_hands_color: Optional[str] = None
    mediapipe_hands_point_color: Optional[str] = None
    mediapipe_hands_thickness: Optional[int] = None


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


@router.post("/gpu/set",
              dependencies=[Depends(require_perm("source.edit"))])
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
        # v3.32.0 自定义纯色骨架样式
        "mediapipe_custom_style": bool(getattr(video_manager, "mediapipe_custom_style", False)),
        "mediapipe_pose_color": getattr(video_manager, "mediapipe_pose_color", "#00FF00") or "#00FF00",
        "mediapipe_pose_point_color": getattr(video_manager, "mediapipe_pose_point_color", "") or "",
        "mediapipe_pose_thickness": int(getattr(video_manager, "mediapipe_pose_thickness", 2)),
        "mediapipe_hands_color": getattr(video_manager, "mediapipe_hands_color", "#00FF00") or "#00FF00",
        "mediapipe_hands_point_color": getattr(video_manager, "mediapipe_hands_point_color", "") or "",
        "mediapipe_hands_thickness": int(getattr(video_manager, "mediapipe_hands_thickness", 2)),
        # v3.8.0 二段管线运行时状态: 给前端显示"基础模式 / 已启用 / 路径无效 / 加载失败"
        "mediapipe_two_stage_status": _compute_two_stage_status(video_manager),
    }


def _sanitize_hex_color(value: str, fallback: str) -> str:
    """校验 '#RRGGBB' 格式颜色, 非法时保留原值."""
    s = (value or "").strip()
    if len(s) == 7 and s.startswith("#"):
        try:
            int(s[1:], 16)
            return s.upper()
        except ValueError:
            pass
    return fallback


def _sanitize_optional_hex_color(value: str, fallback: str) -> str:
    """同 _sanitize_hex_color, 但允许空串 ('' = 关键点跟随连线颜色)."""
    s = (value or "").strip()
    if s == "":
        return ""
    return _sanitize_hex_color(s, fallback)


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


@router.post("/stream/config",
              dependencies=[Depends(require_perm("source.edit"))])
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
    # v3.32.0 自定义纯色骨架样式 (画帧时才读取, 改完即时生效, 不需要重载模型;
    # None = 老前端不带字段, 保留当前值)
    if req.mediapipe_custom_style is not None:
        video_manager.mediapipe_custom_style = bool(req.mediapipe_custom_style)
    if req.mediapipe_pose_color is not None:
        video_manager.mediapipe_pose_color = _sanitize_hex_color(
            req.mediapipe_pose_color, video_manager.mediapipe_pose_color)
    if req.mediapipe_pose_point_color is not None:
        video_manager.mediapipe_pose_point_color = _sanitize_optional_hex_color(
            req.mediapipe_pose_point_color,
            getattr(video_manager, "mediapipe_pose_point_color", ""))
    if req.mediapipe_pose_thickness is not None:
        video_manager.mediapipe_pose_thickness = max(1, min(10, int(req.mediapipe_pose_thickness)))
    if req.mediapipe_hands_color is not None:
        video_manager.mediapipe_hands_color = _sanitize_hex_color(
            req.mediapipe_hands_color, video_manager.mediapipe_hands_color)
    if req.mediapipe_hands_point_color is not None:
        video_manager.mediapipe_hands_point_color = _sanitize_optional_hex_color(
            req.mediapipe_hands_point_color,
            getattr(video_manager, "mediapipe_hands_point_color", ""))
    if req.mediapipe_hands_thickness is not None:
        video_manager.mediapipe_hands_thickness = max(1, min(10, int(req.mediapipe_hands_thickness)))

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
        "mediapipe_custom_style": video_manager.mediapipe_custom_style,
        "mediapipe_pose_color": video_manager.mediapipe_pose_color,
        "mediapipe_pose_point_color": getattr(video_manager, "mediapipe_pose_point_color", "") or "",
        "mediapipe_pose_thickness": video_manager.mediapipe_pose_thickness,
        "mediapipe_hands_color": video_manager.mediapipe_hands_color,
        "mediapipe_hands_point_color": getattr(video_manager, "mediapipe_hands_point_color", "") or "",
        "mediapipe_hands_thickness": video_manager.mediapipe_hands_thickness,
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


@router.post("/transform/config",
              dependencies=[Depends(require_perm("source.edit"))])
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


@router.post("/kalman/config",
              dependencies=[Depends(require_perm("source.edit"))])
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
@router.post("/camera/start",
              dependencies=[Depends(require_perm("source.edit"))])
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


@router.post("/camera/stop",
              dependencies=[Depends(require_perm("source.edit"))])
def stop_camera(channel: int = Query(0)):
    """停止摄像头"""
    mgr = _get_mgr(channel)
    mgr.stop()
    return {"status": "success", "message": f"摄像头已停止 (ch{channel})"}


@router.post("/rtsp/start",
              dependencies=[Depends(require_perm("source.edit"))])
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


@router.post("/hikvision/start",
              dependencies=[Depends(require_perm("source.edit"))])
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


@router.post("/hikvision/stop",
              dependencies=[Depends(require_perm("source.edit"))])
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


@router.post("/hcnetsdk/start",
              dependencies=[Depends(require_perm("source.edit"))])
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


@router.post("/hcnetsdk/stop",
              dependencies=[Depends(require_perm("source.edit"))])
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
@router.post("/video/upload",
              dependencies=[Depends(require_perm("source.edit"))])
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


@router.post("/video/start",
              dependencies=[Depends(require_perm("source.edit"))])
def start_video(req: VideoStartRequest, channel: int = Query(0)):
    """启动视频播放"""
    try:
        _get_mgr(channel).start_video(req.file_path, req.speed)
        # v3.32.1: 与 set-project 同理 — 实际播放的视频要落盘, 否则重启后
        # auto_restore 放回 workstation_config 里的旧视频, 与用户最后用的不一致。
        try:
            from backend.api.channel_manager import channel_manager
            channel_manager.save_channel_source(
                channel, {"source_type": "video", "video_file": req.file_path},
                merge=True)
        except Exception as pe:
            print(f"[API] video/start ch{channel} 源持久化失败 (不影响播放): {pe}")
        return {"status": "success", "message": f"视频已开始播放 (ch{channel})", "speed": req.speed}
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        print(f"[API] /video/start 失败 (ch{channel}): {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/video/stop",
              dependencies=[Depends(require_perm("source.edit"))])
def stop_video(channel: int = Query(0)):
    """停止视频"""
    _get_mgr(channel).stop()
    return {"status": "success", "message": f"视频已停止 (ch{channel})"}


@router.post("/video/speed",
              dependencies=[Depends(require_perm("source.edit"))])
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


@router.post("/video/progress",
              dependencies=[Depends(require_perm("source.edit"))])
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


@router.post("/image/upload",
              dependencies=[Depends(require_perm("source.edit"))])
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


@router.post("/image/set",
              dependencies=[Depends(require_perm("source.edit"))])
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
@router.post("/detection/start",
              dependencies=[Depends(require_perm("monitor.detection.control"))])
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


@router.post("/detection/stop",
              dependencies=[Depends(require_perm("monitor.detection.control"))])
def stop_detection(channel: int = Query(0)):
    """停止检测（只停止推理）并结束会话"""
    mgr = _get_mgr(channel)
    mgr.end_session()
    mgr.stop_detection()
    return {"status": "success", "message": f"检测已停止 (ch{channel})"}


@router.post("/detection/pause",
              dependencies=[Depends(require_perm("monitor.detection.control"))])
def pause_detection(channel: int = Query(0)):
    """暂停：停止画面更新和检测，画面停在当前帧"""
    _get_mgr(channel).pause()
    return {"status": "success", "message": "已暂停"}


@router.post("/detection/resume",
              dependencies=[Depends(require_perm("monitor.detection.control"))])
def resume_detection(channel: int = Query(0)):
    """恢复：从暂停状态恢复，重新启动视频流和检测"""
    if _get_mgr(channel).resume():
        return {"status": "success", "message": "已恢复"}
    else:
        raise HTTPException(status_code=400, detail="无法恢复：没有可用的视频源")


@router.post("/detection/standby",
              dependencies=[Depends(require_perm("monitor.detection.control"))])
def standby_detection(channel: int = Query(0)):
    """待机：只停止检测推理，画面继续播放"""
    _get_mgr(channel).standby()
    return {"status": "success", "message": "已待机"}


@router.post("/detection/resume-inference",
              dependencies=[Depends(require_perm("monitor.detection.control"))])
def resume_inference(channel: int = Query(0)):
    """从待机恢复推理（画面已在播放）"""
    result = _get_mgr(channel).resume_inference()
    if result is False:
        raise HTTPException(status_code=400, detail="无法恢复推理")
    return {"status": "success", "message": "已恢复推理"}


# 清零 / 重置周期性动作 — 需更高的 monitor.detection.advanced 权限
# (operator 只能开始/停止/待机, 不能清零计数; engineer/admin 可以)
@router.post("/detection/reset-stats",
              dependencies=[Depends(require_perm("monitor.detection.advanced"))])
def reset_detection_stats(channel: int = Query(0)):
    """重置统计数据（计数器、步骤计数等），同时结束当前会话"""
    mgr = _get_mgr(channel)
    mgr.end_session()
    mgr.reset_stats()
    # v3.39 川南反馈: 上游不回推消除命令时在途报警一直挂着。入站配置
    # alarm_banner.clear_on_counter_reset 开启时 (默认关), 清零顺带清全部在途报警,
    # 方便联调; 失败不影响清零本身。
    try:
        from backend.db.database import SessionLocal
        from backend.services.mes_inbound import get_mes_inbound
        db = SessionLocal()
        try:
            cfg = get_mes_inbound().get_config(db)
            if (cfg.get("alarm_banner") or {}).get("clear_on_counter_reset", False):
                from backend.services.external_alarm import clear_all_active_alarms
                res = clear_all_active_alarms(db, clear_source="counter_reset")
                db.commit()
                if res.get("cleared"):
                    return {"status": "success",
                            "message": f"统计数据已重置; 在途报警已消除 {res['cleared']} 条"}
        finally:
            db.close()
    except Exception as e:
        print(f"[清零] 联动消除在途报警失败 (忽略): {e}")
    return {"status": "success", "message": "统计数据已重置"}


def _do_ack_pending(mgr, channel: int, action: Optional[str] = None,
                    operator: Optional[str] = None) -> dict:
    """人工确认解除阻塞的核心逻辑 (供普通确认 + 借密码提权确认复用)。

    使用场景:
        触发了 require_ack=True 的 NG / 自定义事件后, 状态机 + 推流被守门拦下,
        画面定格在事件触发瞬间. 工人在监控页点"确认重做" → 走到这里:
            1. 清阻塞态字段 (_pending_ack 等)
            2. 调 _clear_step_runtime_state 清当前周期运行时 (步骤识别 / 周期序列
               / 同时组缓冲 / 跨周期屏蔽集 / last_first 屏蔽集)
            3. 计数器 / 累计统计 / 已上报的 MES 工件 不动 (语义: 已判 NG 入库,
               工人重做这一件, 不是回滚记录)

    幂等: 不在阻塞态时直接返回 {acked: False, reason: "no pending ack"}
    """
    if not getattr(mgr, '_pending_ack', False):
        return {"status": "success", "acked": False, "reason": "no pending ack"}

    # v3.23 缺步骤延迟落账挂起: 走 resolve_step_remediation.
    #   action='supplement_step' 补做缺步→判OK / 'confirm_ng' 认NG落账 / 'redo'(缺省) 丢弃重做.
    # 缺省 redo 保持老"确认重做"按钮语义 + 避免清掉 _pending_ack 却漏清挂起快照 / 留孤儿在制行.
    if getattr(mgr, '_pending_remediation', None):
        res = mgr.resolve_step_remediation(action or 'redo', operator=operator)
        return {"status": "success", "acked": True, "remediation": res}

    ev_name = getattr(mgr, '_pending_ack_event_name', '?') or '?'
    ev_id = getattr(mgr, '_pending_ack_event_id', '') or ''
    started = getattr(mgr, '_pending_ack_started_at', 0) or 0
    waited = round(time.time() - started, 2) if started else 0
    print(f"[ack] 收到人工确认: event={ev_name} 已等待 {waited}s, 清运行时 (channel_id={channel})")

    # v3.9.x: 事件级开关 ack_resets_periodic — 默认 false (仅消除阻塞), 开了之后
    # 确认按钮等价于工人在产线上做了一次该规则配的"完成动作", 把对应的周期性强制
    # 动作规则计数器清零, 避免"确认完下个周期立刻又因累计超期再触发"的死循环.
    # 反查路径: pending_ack 的 event_id → 所有 overdue_event_id 匹配的规则 → reset.
    reset_rules: list = []
    cfg = getattr(mgr, 'project_config', None) or {}
    events_config = cfg.get('events_config', []) if isinstance(cfg, dict) else []
    matched_event = None
    for e in events_config or []:
        if str(e.get('id', '')) == str(ev_id):
            matched_event = e
            break
    should_reset = bool((matched_event or {}).get('ack_resets_periodic', False))
    if should_reset and hasattr(mgr, 'reset_periodic_counter'):
        rules = getattr(mgr, '_periodic_actions', []) or []
        for rule in rules:
            if str(rule.get('overdue_event_id', '')) == str(ev_id):
                rid = rule.get('id')
                if rid:
                    try:
                        result = mgr.reset_periodic_counter(rid)
                        for done in (result or {}).get('reset', []):
                            reset_rules.append(done)
                    except Exception as _e:
                        print(f"[ack] 规则重置失败 rule_id={rid}: {_e}")
        if reset_rules:
            print(f"[ack] 已重置周期性规则 (event_id={ev_id}): {reset_rules}")

    # v3.44 NG 处置闭环: 本次 NG 若在包装层挂了账 (箱账等处置), 确认时一并解挂 —
    # 单次确认闭环, 不再要求工人二次到包装卡操作.
    #   action='confirm_ng' → 照实落 NG 箱并翻页; 其它 (缺省重做) → 丢弃箱账同箱重测.
    pkg_resolution = _resolve_pkg_hold_on_ack(channel, action, operator)

    # v3.34: 事件配了「确认后保留周期」→ 只解除定格不清运行时, 工人从断点继续补做
    # (典型: 违序警告当场定格, 确认后接着打漏掉的那颗螺丝, 周期照常走完)。
    # 默认不勾 → 走老"确认重做"路径 (丢弃在制周期), 零差异。
    if mgr._pending_ack_keeps_cycle():
        mgr._ack_release_keep_cycle()
        print(f"[ack] 确认后保留周期 (断点补做): event={ev_name} "
              f"当前周期={getattr(mgr, 'current_cycle_steps', [])} (channel_id={channel})")
        return {
            "status": "success",
            "acked": True,
            "event_name": ev_name,
            "waited_sec": waited,
            "reset_rules": reset_rules,
            "kept_cycle": True,
            "packaging": pkg_resolution,
        }

    mgr._clear_step_runtime_state()
    return {
        "status": "success",
        "acked": True,
        "event_name": ev_name,
        "waited_sec": waited,
        "reset_rules": reset_rules,
        "packaging": pkg_resolution,
    }


def _get_pkg_hold_safe(channel: int) -> Optional[dict]:
    """v3.44: 查通道包装层 NG 挂账 (状态面板用). 异常隔离返回 None."""
    try:
        from backend.services.packaging_flow_coordinator import get_coordinator as _pkg_coord
        return _pkg_coord().get_channel_hold(channel)
    except Exception:
        return None


def _resolve_pkg_hold_on_ack(channel: int, action: Optional[str],
                             operator: Optional[str]) -> Optional[dict]:
    """v3.44: 人工确认时联动解除包装层 NG 挂账 (无挂账 / 通道不参与包装 → None).

    任何异常隔离 — 包装联动失败绝不能卡死人工确认解除阻塞的主链路.
    """
    try:
        from backend.services.packaging_flow_coordinator import get_coordinator as _pkg_coord
        coord = _pkg_coord()
        if coord.get_channel_hold(channel) is None:
            return None
        from backend.db.database import SessionLocal as _SL
        db = _SL()
        try:
            res = coord.resolve_channel_hold_on_ack(
                channel, action or 'redo', db, operator=operator)
        finally:
            db.close()
        print(f"[ack] 包装挂账联动解除: channel={channel} -> {res}")
        return res
    except Exception as e:
        print(f"[ack] 包装挂账联动解除失败 (隔离, 不影响确认): {e}")
        return None


# v3.23: 人工确认 NG 拆出独立权限 monitor.detection.ack。
# 默认 operator 不含该权限 → 一线操作员确认时被 403, 前端弹"借管理员密码授权一次"
# 提权窗 (走下方 ack-event-elevated)。要让某条产线的操作员也能直接确认, 管理员到
# 角色设置给 operator 勾上 monitor.detection.ack 即可。engineer/admin 默认就有 (monitor.*)。
@router.post("/detection/ack-event",
             dependencies=[Depends(require_perm("monitor.detection.ack"))])
def ack_pending_event(channel: int = Query(0),
                      action: Optional[str] = Query(
                          None,
                          description="缺步骤挂起时的解析动作: supplement_step / confirm_ng / redo(缺省)"),
                      user: CurrentUser = Depends(get_current_user)):
    """工人确认 — 解除 require_ack 触发的阻塞态 (需 monitor.detection.ack 权限)。

    v3.23: 若本次是"缺步骤延迟落账"挂起 (pending_remediation), action 决定怎么收尾:
      - supplement_step: 信任补做, 缺的步骤补进序列 → 判 OK 落账
      - confirm_ng     : 认这个 NG → 现在才落账 NG
      - redo (缺省)    : 丢弃在制周期重检 (= 老"确认重做"语义)
    普通 require_ack 事件 (无 remediation) action 被忽略, 行为与改前一致。
    """
    return _do_ack_pending(_get_mgr(channel), channel, action=action,
                           operator=getattr(user, 'username', None))


class _ElevatedAckInput(BaseModel):
    username: str
    password: str
    action: Optional[str] = None  # v3.23 缺步骤挂起解析动作 (借权时同样可选补步骤/认NG/重做)


# 运行时借权确认: 操作员无 ack 权限时, 输入管理员账号密码"借权一次"解除阻塞。
# 只验证这一次账密 + 权限, 不创建登录会话、不改当前登录身份 (确认完仍是该操作员)。
# 发起本接口只需 monitor.detection.control —— 操作员就能发起提权请求。
@router.post("/detection/ack-event-elevated",
             dependencies=[Depends(require_perm("monitor.detection.control"))])
def ack_pending_event_elevated(body: _ElevatedAckInput, channel: int = Query(0),
                               db: Session = Depends(get_db)):
    from backend.core.auth import verify_password
    from backend.core.permissions import match_permission
    from backend.models.auth_models import User

    username = (body.username or "").strip()
    user = db.query(User).filter(
        User.username == username, User.active == True  # noqa: E712
    ).first()
    if not user or not verify_password(body.password or "", user.password_hash):
        raise HTTPException(status_code=401, detail="账号或密码错误")

    perms: list = []
    for ur in user.user_roles:
        if ur.role:
            for p in (ur.role.permissions or []):
                if p and p not in perms:
                    perms.append(p)
    if not match_permission("monitor.detection.ack", perms):
        raise HTTPException(
            status_code=403,
            detail=f"账号 {username} 没有人工确认 NG 的权限",
        )

    result = _do_ack_pending(_get_mgr(channel), channel,
                             action=body.action, operator=username)
    result["authorized_by"] = username
    print(f"[ack] 借权确认: 由 {username} 授权解除 (channel_id={channel}, action={body.action or 'redo'})")
    return result


@router.post("/detection/reset-periodic",
              dependencies=[Depends(require_perm("monitor.detection.advanced"))])
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


class InferOnceResponse(BaseModel):
    status: str = Field(..., description="固定 success")
    mode: str = Field(..., description="固定 one_shot（区别于 /detection/results 的实时结果）")
    detections: List[Dict[str, Any]] = Field(
        ..., description="检测框列表, 与 /detection/results 同构: 归一化坐标 x/y/w/h + label/confidence/class_id")


@router.post("/detection/infer-once", summary="标定用单帧推理",
             response_model=InferOnceResponse)
def detection_infer_once(channel: int = Query(0)):
    """标定用单帧推理 (v3.42.0): 对当前画面帧现推一帧, 返回原始检测列表。

    给项目页「从当前画面抓取锚点框」兜底: 检测中锁菜单 + 停止/待机清空实时
    结果, 导致打包版里抓取无路可走。本端点对当前显示帧现推一帧 (模型需在
    显存, 即本次运行启动过检测), 不过步骤过滤 —— 锚点标签通常不是步骤,
    实时结果里本来就看不到它。检测运行中同样可调 (推理池串行, 无竞争)。
    不发布结果、不触碰状态机, 无副作用。

    - 模型未加载 / 无画面帧 / 单帧推理超时 → 400, detail 为中文提示。
    - 通道对象不支持单帧推理 (异常热更场景) → 500。
    """
    mgr = _get_mgr(channel)
    if not hasattr(mgr, 'infer_once_for_calibration'):
        raise HTTPException(status_code=500, detail="该通道不支持单帧推理")
    try:
        dets = mgr.infer_once_for_calibration()
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        "status": "success",
        "mode": "one_shot",
        "detections": dets,
    }


@router.get("/detection/results")
def get_detection_results(
    channel: int = Query(0),
    known_shots: Optional[str] = Query(
        None,
        description=(
            "B6 截图去重(默认关): 前端已持有的步骤截图指纹, 形如 "
            "'label1:hash1,label2:hash2'. 传入即开启去重: 后端对内容未变的"
            "截图从 step_screenshots 中省略(前端走 Object.assign 合并保留旧图), "
            "并始终回传 step_screenshot_hashes 供前端更新。不传 = 行为与旧版字节级一致。"
        ),
    ),
):
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
    # v3.10.x: 完整分段历史 (前端 sum/max/first 三档现算)
    step_cycle_segments = {
        k: list(v) for k, v in getattr(mgr, 'step_cycle_segments', {}).items()
    }
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
        _live_start_fr = getattr(mgr, 'step_start_frame_pos', None) or {}
        _live_last_fr = getattr(mgr, 'step_last_frame_pos', None) or {}
        # v3.9.x: in-flight 起点也走 _resolve_step_pt_anchor (与权威 PT 同口径).
        # 否则严格顺序步骤被提前识别时, in-flight 按"跨度"持续涨到很大,
        # 步骤一旦完成 fallback 切到权威值 (修正后小很多), 前端 PT 列会
        # "啪一下从大数字跳到小数字" → 客户报的"PT 闪一下 + OK 跟着改变"。
        # 这里走同一份 anchor, 让 in-flight 起点 ≥ 上一步完成时刻, 与权威值连续过渡.
        # v3.10.x B方案v2: 视频源走帧号差/fps 算耗时, 跟权威 PT 同口径.
        _anchor = getattr(mgr, '_resolve_step_pt_anchor', None)
        _anchor_fr = getattr(mgr, '_resolve_step_pt_anchor_frame_pos', None)
        _compute_dur = getattr(mgr, '_compute_duration_sec', None)
        for _lbl, _last_t in _live_last.items():
            if _lbl not in _live_cycle:
                continue
            _start_t = _live_start.get(_lbl, _last_t)
            if _anchor is not None:
                try:
                    _start_t = _anchor(_lbl, _start_t)
                except Exception:
                    pass
            _start_fr = _live_start_fr.get(_lbl, 0)
            _last_fr = _live_last_fr.get(_lbl, 0)
            if _anchor_fr is not None:
                try:
                    _start_fr = _anchor_fr(_lbl, _start_fr)
                except Exception:
                    pass
            if _compute_dur is not None:
                _live_dur = _compute_dur(
                    _start_fr, _last_fr,
                    fallback_start_wall=_start_t, fallback_end_wall=_last_t,
                )
            else:
                _live_dur = _last_t - _start_t
            if _live_dur <= 0:
                continue
            step_inflight_durations[_lbl] = round(_live_dur, 2)
    except Exception as _e:
        print(f"[API] in-flight PT 收集异常 (ch{channel}): {_e}")

    # v3.32: 区域事件模式的 in-flight ── 该模式不走 step_last_seen/step_start_time
    # (那是步骤状态机的字典), "动作进行中"以引擎 episode 为准: 命中累计中即在场,
    # 时长 = now - episode 起点。不设 current_cycle_steps 门槛: 动作确认前
    # (min_frames 累计期) 就该让前端点亮"进行中", 与顺序模式"步骤刚进画面即 active"对齐。
    _region_snapshot = None
    _region_engine = getattr(mgr, '_region_event_engine', None)
    if _region_engine is not None:
        try:
            _region_snapshot = _region_engine.snapshot()
            _now_ts = time.time()
            for _r in _region_snapshot.get('rules', []):
                _ep_start = _r.get('episode_start_ts')
                if _r.get('in_progress') and _ep_start:
                    _live_dur = _now_ts - _ep_start
                    if _live_dur > 0:
                        step_inflight_durations[_r['name']] = round(_live_dur, 2)
        except Exception as _e:
            print(f"[API] region_events in-flight 收集异常 (ch{channel}): {_e}")

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
            # v3.10.x B方案v2: 视频源用 (当前帧号 - cycle 开始帧号) / 视频原 fps 算 CT,
            # 跟客户机解码速度完全解耦. 实时摄像头仍走 wall-clock.
            _ct_start_fr = getattr(mgr, 'cycle_start_frame_pos', None) or 0
            _ct_end_fr = 0
            try:
                _ct_end_fr = int(getattr(mgr, 'video_current_frame', 0) or 0)
            except Exception:
                _ct_end_fr = 0
            _compute_dur = getattr(mgr, '_compute_duration_sec', None)
            if _compute_dur is not None:
                current_cycle_time = round(_compute_dur(
                    _ct_start_fr, _ct_end_fr,
                    fallback_start_wall=mgr.cycle_start_time,
                    fallback_end_wall=time.time(),
                ), 2)
            else:
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
        # v3.10.x: 当前周期内每步的分段时长数组 (供前端 sum/max/first 三档现算)
        "step_cycle_segments": step_cycle_segments,
        "last_cycle_sum_step_durations": last_cycle_sum_step_durations,
        "avg_cycle_sum_step_durations": avg_cycle_sum_step_durations,
        # v3.9.x D 方案: 累计可见时长 ── 标签每帧"在画面里"时长之和 (秒).
        # 后端永远算永远透出, 前端"显示设置 → PT 计算口径"是纯展示档:
        #   span (默认) → 用 step_durations / cycle_sum_step_durations (现行行为)
        #   visible     → 用本字典的累计值 (规避"标签持续被识别拖长 PT"的问题)
        # 周期开始时清零, 同周期内累加. 详见 source_state_init.py 同名字段注释.
        "step_visible_seconds": {
            k: round(float(v), 2)
            for k, v in (getattr(mgr, 'step_visible_seconds', {}) or {}).items()
        },
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
        ],
        # v3.9.x 事件人工确认阻塞态 (前端用此判断是否弹"确认重做"模态框).
        # active=False 时其他字段无意义; active=True 时前端应:
        #   1. 弹模态框 (覆盖在原 OK/NG toast 上, 锁定操作)
        #   2. 显示 event_name + reason + 已等待秒数 (started_at = epoch 秒)
        #   3. timeout_sec > 0 时倒计时显示 "X 秒后自动确认"
        #   4. 工人点"确认重做"按钮 → POST /detection/ack-event
        "pending_ack": {
            "active": bool(getattr(mgr, '_pending_ack', False)),
            "event_id": getattr(mgr, '_pending_ack_event_id', None),
            "event_name": getattr(mgr, '_pending_ack_event_name', None),
            "started_at": getattr(mgr, '_pending_ack_started_at', None),
            "timeout_sec": int(getattr(mgr, '_pending_ack_timeout_sec', 0) or 0),
            # v3.43.1 触发原因固化进阻塞态 (recent_events 只留 30s, 超窗后前端捞不到原因)
            "reason": getattr(mgr, '_pending_ack_reason', None),
            # v3.43.1 确认后的处置方式 (事件级 ack_keep_cycle 配置), 前端弹窗据此
            # 明示工人: True=保留周期断点续做 / False=清运行时整件重做
            "keeps_cycle": bool(
                mgr._pending_ack_keeps_cycle()
                if hasattr(mgr, '_pending_ack_keeps_cycle') else False),
            # v3.44: 本次 NG 在包装层挂账等处置 (箱账未落) → 前端弹窗加"认NG落账"
            # 选项 + 明示"确认重做不记 NG 箱". None=通道不参与包装/无挂账.
            "pkg_hold": _get_pkg_hold_safe(channel),
        },
        # v3.23 NG 补做策略 (前端确认弹窗据此决定是否展示"补步骤/补数量"按钮)
        "ng_remediation": getattr(mgr, '_ng_remediation', None)
        or {'enabled': False, 'allow_step': True, 'allow_count': True},
        # v3.23 缺步骤延迟落账挂起明细 (None=无挂起; 前端 ack 窗据此展示缺项 + "补步骤"按钮)
        "pending_remediation": getattr(mgr, '_pending_remediation', None),
        # v3.48 计数组合判定表 (纯视觉判型): enabled=项目配了表, last_tag=最近命中机型;
        # count_mode='positional' 时另透出本周期位置去重实时计数 (未结算也可读)
        "combo_verdict": {
            "enabled": bool(getattr(mgr, '_combo_table', None)),
            "last_tag": getattr(mgr, '_combo_last_tag', None),
            **({"positional_counts": mgr._combo_positional.counts()}
               if getattr(mgr, '_combo_positional', None) is not None else {}),
        },
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

    # v3.19.x: 自定义模式混合子状态机运行时状态 (物品计数/唯一ID统计).
    # 未启用混合时返回 None, 前端按 None 处理即可.
    try:
        _mix = getattr(mgr, '_custom_mix', None)
        result['custom_mix_state'] = _mix.to_state() if _mix is not None else None
    except Exception as _e:
        print(f"[API] /detection/results 取 custom_mix_state 失败: {_e}")
        result['custom_mix_state'] = None

    # v3.32: 区域事件引擎快照 (Monitor 步骤面板 in-flight 佐证 + 调试用).
    # 非该模式返回 None. 快照在上方 in-flight 收集时已取, 这里直接复用.
    result['region_events'] = _region_snapshot

    # v3.32: 工件就位提示运行态 (Monitor 提示条 + 引导框着色用).
    # 未启用时返回 None, 前端按 None 处理即可.
    try:
        _pg = getattr(mgr, '_placement_guide_state', None)
        result['placement_guide'] = _pg.snapshot() if _pg is not None else None
    except Exception as _e:
        print(f"[API] /detection/results 取 placement_guide 失败: {_e}")
        result['placement_guide'] = None

    # v3.32: 多轮次拆分当前轮次 (Monitor 轮次角标 + 区域名前缀用). 无多轮规则为 None.
    try:
        _ls = getattr(mgr, '_label_split_engine', None)
        result['label_split_rounds'] = _ls.snapshot_rounds() if _ls is not None else None
    except Exception as _e:
        print(f"[API] /detection/results 取 label_split_rounds 失败: {_e}")
        result['label_split_rounds'] = None

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
            # v3.32: 多工位 Monitor 画拆分区域/引导框叠加层用 (每通道独立项目配置)
            'label_splits': _pcfg.get('label_splits', []),
            'placement_guide': _pcfg.get('placement_guide', {}),
            'hide_boxes_outside_step_roi': _pcfg.get('hide_boxes_outside_step_roi', False),
            # v3.32: 区域事件模式多工位建步骤行用 (只带规则身份, 不带区域多边形等重载字段)
            'region_events': {
                'rules': [
                    {'id': _rr.get('id'), 'name': _rr.get('name')}
                    for _rr in ((_pcfg.get('region_events') or {}).get('rules') or [])
                    if isinstance(_rr, dict)
                ],
            } if _pcfg.get('region_events') else {},
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

    # v3.50: resume_on='ok_only' 下 NG 保持灭灯时置 True, 前端监控页据此
    # 露出"恢复扫码"按钮 (人工出口).
    try:
        from backend.services.scanner import get_scanner_service as _get_scan_svc
        if _get_scan_svc().is_resume_blocked(mgr.channel_id):
            mes_data['scanner_resume_blocked'] = True
    except Exception:
        pass

    if mes_data:
        result['mes'] = mes_data

    # v3.10+ 阶段 4: 当前活跃用户 (字段名 operator/employee_no 保留兼容外部脚本; 值改填 User)
    try:
        from backend.core.auth import get_current_user_id
        u_id = get_current_user_id()
        if u_id:
            from backend.db.database import SessionLocal
            from backend.models.auth_models import User
            _db = SessionLocal()
            try:
                u = _db.query(User).filter(User.id == u_id).first()
                if u:
                    result['operator'] = {
                        "id": u.id,
                        "name": u.display_name or u.username,
                        "employee_no": u.username,
                    }
            finally:
                _db.close()
    except Exception:
        pass

    # B6 截图按内容哈希去重 (默认关, 仅当前端传 known_shots 时启用; 不传则 result 原样返回)
    if known_shots is not None:
        _apply_screenshot_dedup(result, known_shots)

    return result


# 把项目配置推到运行时 VSM, 是"开始检测"流程的必经前置(前端 startDetection 每次都调),
# 不修改/激活项目本身。故权限与启停检测一致(monitor.detection.control), 而非 project.activate
# —— 否则只有启停权限的操作员一点"开始检测"就在这步 403, 检测根本起不来。
@router.post("/detection/set-project",
              dependencies=[Depends(require_perm("monitor.detection.control"))])
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
        # v3.32.1: 运行时项目与持久化绑定必须同源 — 此前这里只改运行时,
        # workstation_config.json 里该通道的 project_id 保持旧值, 重启后
        # auto_load_active_project 按旧绑定恢复"另一个项目+另一个模型",
        # 用户感知为"显示启用 A 项目, 实际用的是 B 的模型"。
        # 用 merge=True 只动 project_id 一个键 (分段写入权不变量)。
        if req.project_id and req.project_id > 0:
            try:
                from backend.api.channel_manager import channel_manager
                channel_manager.save_channel_source(
                    channel, {"project_id": req.project_id}, merge=True)
            except Exception as pe:
                print(f"[API] set-project ch{channel} 绑定持久化失败 (不影响运行时): {pe}")
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
                         ng_cycle_duration: float = Query(9.3),
                         awaiting: bool = Query(False)):
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
        # 待补态注入 (dev 调样: 把"离场判 NG 挂起待补"这一态点亮给前端看)
        if awaiting:
            sess.awaiting_remediation = True
            sess.await_since = now
            inject_ng = True  # 待补横幅靠 last_ng_detail 的 missing_total/漏点展示

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
            'awaiting_remediation': bool(awaiting),
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
# v3.10.2+: per_item 手动周期控制
#
# 设计原则: 手动只代替"时机判定", 不代替"结果判定". 不可人为伪造生产记录.
#   - settle      手动触发结算 (= finish_label 那一刻). OK/NG 由真实覆盖状态判
#   - force_start 手动开始周期 (= 画面稳定锁定那一刻). 后续覆盖/超时/NG 全真实跑
# 不提供 "强制 OK / 强制 NG / 取消周期" 接口.
# ============================================================
@router.post("/detection/per-item-control")
def per_item_control(channel: int = Query(0),
                     action: str = Query(..., description="settle | force_start | confirm_ng | judge")):
    """手动控制 per_item 周期时机.

    action:
        settle       手动触发当前周期结算; OK/NG 由系统按真实覆盖状态判
        force_start  强制开启一个新周期 (用于自动稳定判定迟迟不成时)
        confirm_ng   人工确认当前待补态 NG 落账 (离场判 NG 后的待补/待确认态);
                     仍按真实覆盖状态判, 不伪造结果
        judge        手动判定 (判定时机=manual 时用): 立刻拍覆盖快照亮绿/红, 不落账;
                     OK/NG 由真实覆盖判定, 落账仍由结算时机触发
    """
    mgr = _get_mgr(channel)
    if not getattr(mgr, '_per_item_config', None):
        raise HTTPException(status_code=400, detail="当前 channel 未启用 per_item 模式")

    action = (action or '').strip().lower()
    if action == 'settle':
        ret = mgr.per_item_manual_settle()
    elif action == 'force_start':
        ret = mgr.per_item_manual_force_start()
    elif action == 'confirm_ng':
        ret = mgr.per_item_confirm_ng()
    elif action == 'judge':
        ret = mgr.per_item_judge_now()
    else:
        raise HTTPException(status_code=400, detail=f"未知 action: {action} (允许: settle | force_start | confirm_ng | judge)")

    if not ret.get('ok'):
        raise HTTPException(status_code=400, detail=ret.get('msg', '操作失败'))

    snapshot = mgr.get_per_item_state() if hasattr(mgr, 'get_per_item_state') else None
    return {"status": "success", "action": action, "result": ret, "snapshot": snapshot}


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
@router.post("/detection/rebind",
              dependencies=[Depends(require_perm("monitor.detection.control"))])
def resolve_rebind(action: str = "new", channel: int = 0):
    """manual rebind 模式：用户选择继续当前工件(continue)或扫新工件(new)"""
    from backend.api.channel_manager import channel_manager
    mgr = channel_manager.get(channel)
    if mgr._mes_hook:
        mgr._mes_hook.resolve_rebind(mgr.channel_id, action)
        return {"status": "ok", "action": action}
    return {"status": "error", "message": "MES 未启用"}


@router.post("/detection/clear_pending_scan",
              dependencies=[Depends(require_perm("monitor.detection.control"))])
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


@router.post("/detection/recording-failures/clear",
              dependencies=[Depends(require_perm("monitor.detection.control"))])
def clear_recording_failures(channel: int = 0):
    """清空该工位录像异常详情列表（仅影响前端详情展示，不影响业务数据）。"""
    mgr = _get_mgr(channel)
    try:
        count = mgr.clear_recording_failures()
        return {"status": "ok", "cleared_count": count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
