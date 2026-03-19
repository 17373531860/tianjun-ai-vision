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
from pydantic import BaseModel

router = APIRouter(prefix="/workstations", tags=["workstations"])

MAX_CHANNELS = 4
_CONFIG_FILE = os.path.join(os.path.dirname(__file__), '..', 'data', 'workstation_config.json')


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
    channels: List[WorkstationConfig] = []


class ChannelManager:
    """Manages multiple VideoSourceManager instances (one per workstation)."""

    def __init__(self):
        from backend.api.source import VideoSourceManager
        self._lock = threading.Lock()
        self.channel_count = 1
        self.channels: Dict[int, "VideoSourceManager"] = {
            0: VideoSourceManager(channel_id=0)
        }
        self._shared_model = None
        self._shared_model_path: Optional[str] = None
        self._model_lock = threading.Lock()
        self._gpu_models: Dict[str, object] = {}  # {device_str: YOLO model}
        self._gpu_model_paths: Dict[str, str] = {}  # {device_str: model file path}
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

        if count not in (1, 2, 4):
            raise ValueError("channel_count must be 1, 2, or 4")

        with self._lock:
            # Stop and remove channels that exceed the new count
            to_remove = [cid for cid in self.channels if cid >= count]
            for cid in to_remove:
                try:
                    self.channels[cid].stop()
                except Exception as e:
                    print(f"[ChannelManager] Error stopping channel {cid}: {e}")
                del self.channels[cid]

            # Create missing channels
            for cid in range(count):
                if cid not in self.channels:
                    self.channels[cid] = VideoSourceManager(channel_id=cid)

            self.channel_count = count
            self._save_config()

        print(f"[ChannelManager] Channel count set to {count}, active: {self.active_channels()}")

    # ------------------------------------------------------------------
    # Model management (shared single-GPU or multi-GPU)
    # ------------------------------------------------------------------

    def load_shared_model(self, model_path: str, device: str = "auto") -> bool:
        """Load YOLO model once and share the reference across all channels on the same GPU."""
        with self._model_lock:
            ch0 = self.channels[0]
            if device != "auto":
                ch0.device = device
            success = ch0.load_model(model_path)
            if not success:
                return False
            self._shared_model = ch0.model
            self._shared_model_path = model_path
            self._gpu_models[ch0.device] = ch0.model
            self._gpu_model_paths[ch0.device] = model_path

            for cid, mgr in self.channels.items():
                if cid != 0:
                    mgr.model = self._shared_model
                    mgr.model_path = self._shared_model_path
                    mgr.current_device_info = ch0.current_device_info

        return True

    def load_model_for_channel(self, channel_id: int, model_path: str, device: str = "auto") -> bool:
        """Load a model for a specific channel.
        Only reuses an existing GPU model if it was loaded from the SAME path."""
        mgr = self.channels.get(channel_id)
        if mgr is None:
            return False

        with self._model_lock:
            resolved_device = self._resolve_device(device)

            cached = self._gpu_models.get(resolved_device)
            cached_path = self._gpu_model_paths.get(resolved_device)
            if cached is not None and cached_path == model_path:
                mgr.model = cached
                mgr.model_path = model_path
                mgr.device = resolved_device
                ref_mgr = next((m for m in self.channels.values() if m.current_device_info), None)
                if ref_mgr:
                    mgr.current_device_info = ref_mgr.current_device_info
                print(f"[ChannelManager] ch{channel_id} reusing model on {resolved_device}")
                return True

            mgr.device = resolved_device
            success = mgr.load_model(model_path)
            if success:
                self._gpu_models[resolved_device] = mgr.model
                self._gpu_model_paths[resolved_device] = model_path
                if self._shared_model is None:
                    self._shared_model = mgr.model
                    self._shared_model_path = model_path
                print(f"[ChannelManager] ch{channel_id} loaded NEW model on {resolved_device}: {os.path.basename(model_path)}")
            return success

    def _propagate_model(self, channel_id: int):
        """Give a newly-created channel the shared model reference (same GPU)."""
        if self._shared_model is not None:
            mgr = self.channels.get(channel_id)
            if mgr and mgr.model is None:
                mgr.model = self._shared_model
                mgr.model_path = self._shared_model_path
                mgr.current_device_info = self.channels[0].current_device_info

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
        try:
            os.makedirs(os.path.dirname(_CONFIG_FILE), exist_ok=True)
            data = {"channel_count": self.channel_count}
            with open(_CONFIG_FILE, 'w') as f:
                json.dump(data, f)
        except Exception as e:
            print(f"[ChannelManager] Failed to save config: {e}")

    def _load_config(self):
        try:
            print(f"[ChannelManager] 配置文件路径: {_CONFIG_FILE}, 存在: {os.path.exists(_CONFIG_FILE)}")
            if os.path.exists(_CONFIG_FILE):
                with open(_CONFIG_FILE, 'r') as f:
                    data = json.load(f)
                count = data.get("channel_count", 1)
                print(f"[ChannelManager] 加载配置: channel_count={count}")
                if count in (1, 2, 4) and count != self.channel_count:
                    self.set_channel_count(count)
            else:
                print(f"[ChannelManager] 配置文件不存在，使用默认 channel_count=1")
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
    return {
        "channel_count": channel_manager.channel_count,
        "channels": channel_manager.all_status(),
    }


@router.post("/mode")
def set_workstation_mode(req: WorkstationModeRequest):
    """Set the number of active workstations (1, 2, or 4)."""
    try:
        channel_manager.set_channel_count(req.channel_count)
        return {"status": "success", "channel_count": req.channel_count}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


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
