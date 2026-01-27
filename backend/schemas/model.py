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
