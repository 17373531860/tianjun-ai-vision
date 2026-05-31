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


# v3.13 M3.1: 插件命名空间数据补丁
# 给客户专属插件在 Project 的 7 个 JSON 配置字段下精准写
# *.plugin_data.<customer_code> 子树, 不动其它字段.
class ProjectPluginDataPatch(BaseModel):
    """精准 PATCH 单个 JSON 字段下的 plugin_data.<customer_code> 子树.

    路径定位:
      - scope ∈ {"pipeline_config", "alarm_config", "detection_config", "data_config"}:
        目标 = ``<scope>.plugin_data.<customer_code>``, index 必须为 None
      - scope ∈ {"steps_config", "events_config", "counters_config"}:
        目标 = ``<scope>[index].plugin_data.<customer_code>``, index 必须 >= 0

    合并语义:
      - 目标 dict 不存在 → 初始化为空 dict
      - 浅合并 data 进去 (新 key 覆盖旧 key, 未提及的旧 key 保留)
      - 其它客户的 plugin_data 子键 (例: `plugin_data.other_customer`) 完全不动
    """
    customer_code: str
    scope: str
    index: Optional[int] = None
    data: dict
