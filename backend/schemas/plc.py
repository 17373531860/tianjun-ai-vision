"""PLC 连接器 API Schema (RFC 13)。

points/read_rules/write_rules/options 是自由 JSON (先例: Project 7 个 JSON 字段),
结构约定见 backend/models/plc_models.py 模块注释; 点位级校验在 API 层调
point_codec.validate_point + driver 地址解析做, 不靠 Pydantic 展开嵌套模型
(驱动方言各异, 嵌套模型会把"加一种驱动"变成"改一次 Schema", 违背零代码目标)。
"""
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class PLCConnectionCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    driver: str = "s7"
    enabled: bool = False
    conn_params: Dict[str, Any] = Field(default_factory=dict)
    points: List[Dict[str, Any]] = Field(default_factory=list)
    read_rules: List[Dict[str, Any]] = Field(default_factory=list)
    write_rules: List[Dict[str, Any]] = Field(default_factory=list)
    options: Dict[str, Any] = Field(default_factory=dict)


class PLCConnectionUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=64)
    driver: Optional[str] = None
    enabled: Optional[bool] = None
    conn_params: Optional[Dict[str, Any]] = None
    points: Optional[List[Dict[str, Any]]] = None
    read_rules: Optional[List[Dict[str, Any]]] = None
    write_rules: Optional[List[Dict[str, Any]]] = None
    options: Optional[Dict[str, Any]] = None


class PLCPointWrite(BaseModel):
    point: str
    value: Any = None


class PLCEnableRequest(BaseModel):
    enabled: bool
