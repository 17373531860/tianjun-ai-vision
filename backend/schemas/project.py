from pydantic import BaseModel
from typing import Optional, List, Any
from datetime import datetime

class ProjectBase(BaseModel):
    name: str
    task_type: str = "detection"
    pipeline_config: Optional[dict] = None
    logic_mode: str = "sequential"
    steps_config: Optional[List[dict]] = None
    events_config: Optional[List[dict]] = None
    counters_config: Optional[List[dict]] = None

class ProjectCreate(ProjectBase):
    default_model_id: Optional[int] = None

class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    task_type: Optional[str] = None
    pipeline_config: Optional[dict] = None
    default_model_id: Optional[int] = None
    logic_mode: Optional[str] = None
    steps_config: Optional[List[dict]] = None
    events_config: Optional[List[dict]] = None
    counters_config: Optional[List[dict]] = None
    is_active: Optional[bool] = None

class ProjectResponse(ProjectBase):
    id: int
    default_model_id: Optional[int] = None
    is_active: bool = False
    created_at: datetime
    updated_at: datetime
    model_name: Optional[str] = None

    class Config:
        from_attributes = True

class ProjectListResponse(BaseModel):
    total: int
    items: List[ProjectResponse]
