"""RFC 14 统一触发中心 — /api/v1/triggers/* 请求 Schema。"""
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class TriggerChannelCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    type: str = Field("pixel_region", max_length=32)
    enabled: bool = False
    params: Dict[str, Any] = Field(default_factory=dict)
    rules: List[Dict[str, Any]] = Field(default_factory=list)
    options: Dict[str, Any] = Field(default_factory=dict)


class TriggerChannelUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=64)
    type: Optional[str] = Field(None, max_length=32)
    enabled: Optional[bool] = None
    params: Optional[Dict[str, Any]] = None
    rules: Optional[List[Dict[str, Any]]] = None
    options: Optional[Dict[str, Any]] = None


class TriggerEnableRequest(BaseModel):
    enabled: bool


class TriggerMockFire(BaseModel):
    meta: Dict[str, Any] = Field(default_factory=dict)


class TriggerMockLevel(BaseModel):
    value: bool
    meta: Dict[str, Any] = Field(default_factory=dict)


class TriggerTestFire(BaseModel):
    rule_index: int = 0
