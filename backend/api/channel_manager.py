"""
Multi-channel (workstation) manager.

Wraps multiple VideoSourceManager instances behind a unified API.
Channel 0 is the default and always exists for backward compatibility.
"""

import threading
import json
import os
from typing import Dict, Optional, List
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from backend.core.config import DATA_DIR

router = APIRouter(prefix="/workstations", tags=["workstations"])

MAX_CHANNELS = 4
_CONFIG_FILE = os.path.join(DATA_DIR, 'workstation_config.json')


class WorkstationConfig(BaseModel):
    channel_id: int = 0
    camera_type: str = "usb"           # usb, hikvision, video
    device_index: int = 0
    width: int = 1280
    height: int = 720
    fps: int = 30
    project_id: Optional[int] = None
    gpu_device: str = "auto"           # auto, cuda:0, cuda:1, cpu


class WorkstationModeRequest(BaseModel):
    channel_count: int = 1             # 1, 2, or 4
    channels: List[WorkstationConfig] = Field(default_factory=list)


class ChannelManager:
    """Manages multiple VideoSourceManager instances (one per workstation)."""

    def __init__(self):
        from backend.api.source import VideoSourceManager
        self._lock = threading.Lock()
        self.channel_count = 1
        self.channels: Dict[int, "VideoSourceManager"] = {
            0: VideoSourceManager(channel_id=0)
        }
        self._model_lock = threading.Lock()
        self._load_config()

    # ------------------------------------------------------------------
    # Channel access
    # ------------------------------------------------------------------

    def get(self, channel_id: int = 0) -> "VideoSourceManager":
        """Return the manager for a given channel, falling back to 0."""
        mgr = self.channels.get(channel_id)
        if mgr is None:
            raise ValueError(f"Channel {channel_id} not configured (active: {list(self.channels.keys())})")
        return mgr

    def get_default(self) -> "VideoSourceManager":
        return self.channels[0]

    def active_channels(self) -> List[int]:
        return sorted(self.channels.keys())

    # ------------------------------------------------------------------
    # Channel lifecycle
    # ------------------------------------------------------------------

    def set_channel_count(self, count: int):
        """Resize the number of active channels (1, 2 or 4)."""
        from backend.api.source import VideoSourceManager

        if count < 1 or count > MAX_CHANNELS:
            raise ValueError(f"channel_count must be 1-{MAX_CHANNELS}")

        with self._lock:
            # Stop and remove channels that exceed the new count
            to_remove = [cid for cid in self.channels if cid >= count]
            for cid in to_remove:
                try:
                    mgr = self.channels[cid]
                    if hasattr(mgr, 'current_session_id') and mgr.current_session_id:
                        mgr.end_session()
                    mgr.stop()
                except Exception as e:
                    print(f"[ChannelManager] Error stopping channel {cid}: {e}")
                del self.channels[cid]
                # v2.7.2: 清理 MESHook 按 channel_id 存储的残留状态
                # 避免降再升工位时新通道误判 had_workpiece=True 或工单状态错乱
                try:
                    from backend.services.mes_hooks import get_mes_hook
                    get_mes_hook().on_channel_removed(cid)
                except Exception as e:
                    print(f"[ChannelManager] Error cleaning MESHook for ch{cid}: {e}")
                # v2.7.2: 停止 AlarmRouter 对应通道，避免蜂鸣器线程残留 / 串口被占
                try:
                    from backend.api.alarm import alarm_router
                    alarm_router.on_channel_removed(cid)
                except Exception as e:
                    print(f"[ChannelManager] Error cleaning AlarmRouter for ch{cid}: {e}")

            # Create missing channels
            for cid in range(count):
                if cid not in self.channels:
                    new_mgr = VideoSourceManager(channel_id=cid)
                    if 0 in self.channels and hasattr(self.channels[0], '_mes_hook'):
                        new_mgr._mes_hook = self.channels[0]._mes_hook
                    self.channels[cid] = new_mgr

            self.channel_count = count
            self._save_config()

        print(f"[ChannelManager] Channel count set to {count}, active: {self.active_channels()}")

    # ------------------------------------------------------------------
    # Model management
    # ------------------------------------------------------------------

    def load_shared_model(self, model_path: str, device: str = "auto") -> bool:
        """Load the same model path for all channels with independent instances.

        NOTE:
        - "shared" here means shared model PATH/config, not shared runtime object.
        - Each channel keeps its own model instance to avoid cross-channel race.
        """
        with self._model_lock:
            all_ok = True
            for cid in sorted(self.channels.keys()):
                ok = self._load_model_for_channel_locked(cid, model_path, device)
                if not ok:
                    all_ok = False
                    print(f"[ChannelManager] ch{cid} 独立模型加载失败: {model_path}")
            return all_ok

    def load_model_for_channel(self, channel_id: int, model_path: str, device: str = "auto") -> bool:
        """Load a model for a specific channel with an independent instance."""
        with self._model_lock:
            return self._load_model_for_channel_locked(channel_id, model_path, device)

    def _load_model_for_channel_locked(self, channel_id: int, model_path: str, device: str = "auto") -> bool:
        mgr = self.channels.get(channel_id)
        if mgr is None:
            return False

        resolved_device = self._resolve_device(device)
        mgr.device = resolved_device
        success = mgr.load_model(model_path)
        if success:
            print(f"[ChannelManager] ch{channel_id} loaded model instance on {resolved_device}: {os.path.basename(model_path)}")
        return success

    def _propagate_model(self, channel_id: int):
        """No-op: model instances are no longer propagated/shared across channels."""
        return

    @staticmethod
    def _resolve_device(device: str) -> str:
        if device == "auto":
            try:
                import torch
                return "cuda:0" if torch.cuda.is_available() else "cpu"
            except ImportError:
                return "cpu"
        return device

    def get_gpu_allocation(self) -> dict:
        """Return {channel_id: gpu_device} for all channels."""
        result = {}
        for cid, mgr in self.channels.items():
            result[cid] = getattr(mgr, 'device', 'auto')
        return result

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _save_config(self):
        """保存工位数到配置文件。仅在 set_channel_count() 中调用，允许升级也允许降级。"""
        try:
            os.makedirs(os.path.dirname(_CONFIG_FILE), exist_ok=True)
            existing = {}
            if os.path.exists(_CONFIG_FILE):
                try:
                    with open(_CONFIG_FILE, 'r') as f:
                        existing = json.load(f)
                except Exception:
                    pass
            data = {"channel_count": self.channel_count}
            data["channels"] = existing.get("channels", {})
            with open(_CONFIG_FILE, 'w') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[ChannelManager] Failed to save config: {e}")

    def save_channel_source(self, channel_id: int, source_cfg: dict, merge: bool = True):
        """持久化单个工位的视频源配置。只写 channels 字段，不修改 channel_count。
        merge=True 时合并到现有配置，False 时替换。
        channel_count 的写入权由 set_channel_count() 独占，避免在不同上下文误覆盖。"""
        try:
            os.makedirs(os.path.dirname(_CONFIG_FILE), exist_ok=True)
            data = {}
            if os.path.exists(_CONFIG_FILE):
                try:
                    with open(_CONFIG_FILE, 'r') as f:
                        data = json.load(f)
                except Exception:
                    data = {}
            if "channel_count" not in data:
                data["channel_count"] = self.channel_count
            if "channels" not in data:
                data["channels"] = {}
            ch_key = str(channel_id)
            if merge and ch_key in data["channels"]:
                data["channels"][ch_key].update(source_cfg)
            else:
                data["channels"][ch_key] = source_cfg
            with open(_CONFIG_FILE, 'w') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[ChannelManager] 保存 ch{channel_id} 源配置失败: {e}")

    def get_channel_sources(self) -> dict:
        """读取所有工位的持久化源配置"""
        try:
            if os.path.exists(_CONFIG_FILE):
                with open(_CONFIG_FILE, 'r') as f:
                    data = json.load(f)
                return data.get("channels", {})
        except Exception as e:
            print(f"[ChannelManager] 读取源配置失败: {e}")
        return {}

    def _load_config(self):
        try:
            print(f"[ChannelManager] 配置文件路径: {_CONFIG_FILE}, 存在: {os.path.exists(_CONFIG_FILE)}")
            if os.path.exists(_CONFIG_FILE):
                with open(_CONFIG_FILE, 'r') as f:
                    data = json.load(f)
                count = data.get("channel_count", 1)
                print(f"[ChannelManager] 加载配置: channel_count={count}")
                if 1 <= count <= MAX_CHANNELS and count != self.channel_count:
                    self.set_channel_count(count)
            else:
                print("[ChannelManager] 配置文件不存在，使用默认 channel_count=1")
        except Exception as e:
            print(f"[ChannelManager] Failed to load config: {e}")

    # ------------------------------------------------------------------
    # Convenience: iterate / status
    # ------------------------------------------------------------------

    def all_status(self) -> list:
        """Return status for every active channel."""
        result = []
        for cid in sorted(self.channels.keys()):
            mgr = self.channels[cid]
            result.append({
                "channel_id": cid,
                "is_running": mgr.is_running,
                "is_detecting": mgr.is_detecting,
                "source_type": mgr.source_type,
                "model_loaded": mgr.model is not None,
                "gpu_device": getattr(mgr, 'device', 'auto'),
                "fps_actual": getattr(mgr, 'fps_actual', 0),
                "latency": getattr(mgr, 'latency', 0),
            })
        return result

    def stop_all(self):
        """Gracefully stop all channels."""
        for cid in list(self.channels.keys()):
            try:
                self.channels[cid].stop()
            except Exception as e:
                print(f"[ChannelManager] Error stopping ch{cid}: {e}")


# --------------- Module-level singleton ---------------
channel_manager = ChannelManager()


def get_channel_manager() -> ChannelManager:
    return channel_manager


# --------------- REST endpoints ---------------

@router.get("/")
def list_workstations():
    """Return status of all active workstations/channels."""
    saved = channel_manager.get_channel_sources()
    return {
        "channel_count": channel_manager.channel_count,
        "channels": channel_manager.all_status(),
        "source_configs": saved,
    }


@router.post("/mode")
def set_workstation_mode(req: WorkstationModeRequest):
    """Set the number of active workstations and optionally apply per-channel config."""
    try:
        channel_manager.set_channel_count(req.channel_count)
        applied = []
        for ch_cfg in req.channels:
            try:
                mgr = channel_manager.get(ch_cfg.channel_id)
                if ch_cfg.gpu_device and ch_cfg.gpu_device != "auto":
                    mgr.device = ch_cfg.gpu_device
                applied.append(ch_cfg.channel_id)
            except ValueError:
                pass
        return {"status": "success", "channel_count": req.channel_count,
                "applied_configs": applied}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/channel-config")
def save_channel_config(body: dict):
    """持久化单个工位的视频源配置（接受任意字段）"""
    ch_id = body.pop("channel_id", 0)
    channel_manager.save_channel_source(ch_id, body, merge=False)
    return {"status": "success", "channel_id": ch_id, "config": body}


class GpuAssignRequest(BaseModel):
    device: str = "auto"  # auto, cuda:0, cuda:1, cpu


@router.post("/{channel_id}/gpu")
def set_channel_gpu(channel_id: int, req: GpuAssignRequest):
    """Assign a GPU device to a specific workstation/channel."""
    try:
        mgr = channel_manager.get(channel_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    mgr.device = req.device
    return {"status": "success", "channel_id": channel_id, "device": req.device}


@router.get("/gpu-allocation")
def get_gpu_allocation():
    """Return GPU device assignment for all channels."""
    return {
        "channel_count": channel_manager.channel_count,
        "allocation": channel_manager.get_gpu_allocation(),
    }


@router.get("/{channel_id}/status")
def workstation_status(channel_id: int):
    """Return detailed status for a single workstation."""
    try:
        mgr = channel_manager.get(channel_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {
        "channel_id": channel_id,
        "is_running": mgr.is_running,
        "is_detecting": mgr.is_detecting,
        "source_type": mgr.source_type,
        "width": mgr.width,
        "height": mgr.height,
        "fps": mgr.fps,
        "fps_actual": getattr(mgr, 'fps_actual', 0),
        "latency": getattr(mgr, 'latency', 0),
        "model_loaded": mgr.model is not None,
        "gpu_device": getattr(mgr, 'device', 'auto'),
    }
