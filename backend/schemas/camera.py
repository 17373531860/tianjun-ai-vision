from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class CameraBase(BaseModel):
    name: str
    source: str
    camera_type: str = "usb"
    resolution_width: int = 1280
    resolution_height: int = 720
    fps: int = 30
    exposure: Optional[float] = None

class CameraCreate(CameraBase):
    pass

class CameraUpdate(BaseModel):
    name: Optional[str] = None
    source: Optional[str] = None
    camera_type: Optional[str] = None
    resolution_width: Optional[int] = None
    resolution_height: Optional[int] = None
    fps: Optional[int] = None
    exposure: Optional[float] = None
    is_active: Optional[bool] = None

class CameraResponse(CameraBase):
    id: int
    is_active: bool = False
    status: str = "offline"
    created_at: datetime

    class Config:
        from_attributes = True
