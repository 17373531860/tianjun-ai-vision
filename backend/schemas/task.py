from pydantic import BaseModel
from typing import Optional, List, Any
from datetime import datetime

class TaskCreate(BaseModel):
    project_id: int
    model_id: Optional[int] = None
    input_file: Optional[str] = None
    step_name: Optional[str] = None

class TaskResponse(BaseModel):
    id: int
    project_id: int
    model_id: Optional[int] = None
    input_file: Optional[str] = None
    result_file: Optional[str] = None
    result_data: Optional[dict] = None
    is_good: bool = True
    confidence: Optional[float] = None
    duration: int = 0
    step_name: Optional[str] = None
    timestamp: datetime
    error_msg: Optional[str] = None

    class Config:
        from_attributes = True

class TaskListResponse(BaseModel):
    total: int
    items: List[TaskResponse]
