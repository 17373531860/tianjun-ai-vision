from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

class ModelBase(BaseModel):
    name: str
    description: Optional[str] = None
    version: Optional[str] = None
    framework: str = "PyTorch"

class ModelCreate(ModelBase):
    project_id: Optional[int] = None

class ModelUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    version: Optional[str] = None
    project_id: Optional[int] = None
    status: Optional[str] = None

class ModelResponse(ModelBase):
    id: int
    project_id: Optional[int] = None
    file_path: str
    file_name: str
    file_size: int
    labels: Optional[List[str]] = None
    status: str = "idle"
    upload_time: datetime

    class Config:
        from_attributes = True

class ModelListResponse(BaseModel):
    total: int
    items: List[ModelResponse]


class ConversionRequest(BaseModel):
    format: str
    project_id: Optional[int] = None

class ConversionResponse(BaseModel):
    id: int
    model_id: int
    format: str
    file_path: str
    file_size: int = 0
    gpu_name: Optional[str] = None
    gpu_arch: Optional[str] = None
    status: str
    error_msg: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True

class ConversionStatusResponse(BaseModel):
    id: int
    status: str
    error_msg: Optional[str] = None
    file_path: Optional[str] = None
    file_size: int = 0

    class Config:
        from_attributes = True

class FormatInfo(BaseModel):
    key: str
    name: str
    extension: str
    description: str
    tag: Optional[str] = None
    available: bool = True
    unavailable_reason: Optional[str] = None

class FormatsAvailableResponse(BaseModel):
    formats: List[FormatInfo]
    recommended: Optional[str] = None
    gpu_name: Optional[str] = None
    gpu_available: bool = False
