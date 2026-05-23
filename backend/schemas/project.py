from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

class ProjectBase(BaseModel):
    name: str
    task_type: str = "detection"
    pipeline_config: Optional[dict] = None
    logic_mode: str = "sequential"
    steps_config: Optional[List[dict]] = None
    events_config: Optional[List[dict]] = None
    counters_config: Optional[List[dict]] = None
    alarm_config: Optional[dict] = None
    detection_config: Optional[dict] = None
    data_config: Optional[dict] = None

class ProjectCreate(ProjectBase):
    default_model_id: Optional[int] = None
    model_format: str = "pytorch_fp32"

class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    task_type: Optional[str] = None
    pipeline_config: Optional[dict] = None
    default_model_id: Optional[int] = None
    model_format: Optional[str] = None
    logic_mode: Optional[str] = None
    steps_config: Optional[List[dict]] = None
    events_config: Optional[List[dict]] = None
    counters_config: Optional[List[dict]] = None
    alarm_config: Optional[dict] = None
    detection_config: Optional[dict] = None
    data_config: Optional[dict] = None
    is_active: Optional[bool] = None

class ProjectResponse(ProjectBase):
    id: int
    default_model_id: Optional[int] = None
    model_format: str = "pytorch_fp32"
    is_active: bool = False
    created_at: datetime
    updated_at: datetime
    model_name: Optional[str] = None
    model_version: Optional[str] = None
    # 模型自带的全部类别 (来自 Model.labels JSON), 给前端 Project 页展示用
    # 与 steps_config 是两个维度: model_labels 是模型推理能识别的全部类别,
    # steps_config 是项目里被纳入业务步骤的子集, 二者不必相等
    model_labels: Optional[List[str]] = None

    class Config:
        from_attributes = True

class ProjectListResponse(BaseModel):
    total: int
    items: List[ProjectResponse]
