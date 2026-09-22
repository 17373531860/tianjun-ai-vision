"""契约级假边缘机 — 只实现枢纽消费的 /api/v1/hub/* 契约面 (RFC 15 §9)。

用途: hub 前端 CI E2E 需要一个"活的"边缘节点走完 纳管 → 墙 → 工位操作台 →
写操作 全链路, 起真主程序太重 (模型/相机初始化), 且 e2e 是独立 uvicorn 进程
无法注入 ASGITransport。此假边缘与真边缘的契约一致性由 tests/test_hub_access.py
的 schema 契约测试双向钉住 (改真边缘契约必挂那边, 改这里必挂 e2e)。

状态可查可改 (state dict), e2e 用它断言"边缘真的被操作了"。
"""
from __future__ import annotations

import base64
from typing import Optional

from fastapi import FastAPI, Response
from pydantic import BaseModel

# 1x1 黑色 JPEG (E2E 里 useSnapshot 轮询用)
_JPEG_1PX = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRof"
    "Hh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwh"
    "MjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAAR"
    "CAABAAEDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAA"
    "AgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkK"
    "FhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWG"
    "h4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl"
    "5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREA"
    "AgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYk"
    "NOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOE"
    "hYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk"
    "5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwD3+iiigD//2Q==")


class OpsPayload(BaseModel):
    """模块级定义 (勿移进工厂函数): 本文件开了 future annotations, FastAPI 只在
    模块命名空间解析字符串注解, 函数内的类会被静默降级成 query 参数 → 422。"""
    action: str
    channel: int = 0
    project_id: Optional[int] = None


def create_fake_edge(node_id: str = "edge-fake01",
                     station_count: int = 1) -> FastAPI:
    app = FastAPI(title="Fake Edge (契约测试用)")

    channels = list(range(station_count))

    # 可变状态: e2e 操作后断言这里
    app.state.edge = {
        "detecting": {ch: False for ch in channels},
        "active_project_id": 1,
        "alarm_active": {ch: True for ch in channels},   # e2e 用: ack 后翻 False
        "projects": [
            {"id": 1, "name": "装配检测A", "is_active": True},
            {"id": 2, "name": "包装检测B", "is_active": False},
        ],
        "ops_log": [],
    }
    state = app.state.edge

    _IDENTITY = {
        "node_id": node_id,
        "hostname": f"FAKE-IPC-{node_id[-2:]}",
        "machine_id": f"FAKE-MACHINE-{node_id[-2:]}",
        "app_version": "3.59.0",
        "api_contract": 1,
        "license": {"state": "valid", "customer": "e2e", "expires_at": None},
    }

    def _stations_profile():
        return [{
            "channel_id": ch,
            "logic_mode": "detection",
            "bound_project_id": 1,
            "properties": [
                {"id": "detecting", "access": ["read", "write", "notify"],
                 "format": "bool"},
                {"id": "active_project_id", "access": ["read", "write", "notify"],
                 "format": "int"},
                {"id": "logic_mode", "access": ["read"], "format": "str"},
            ],
            "actions": [
                {"id": "start_detection", "label": "开始检测", "confirm": "normal"},
                {"id": "stop_detection", "label": "停止检测", "confirm": "danger"},
                {"id": "ack_alarm", "label": "消警", "confirm": "normal"},
            ],
            "events": [],
        } for ch in channels]

    def _profile():
        return {
            "profile_schema": 1,
            "identity": _IDENTITY,
            "stations": _stations_profile(),
            "node_actions": [
                {"id": "activate_project", "label": "切换项目", "confirm": "danger",
                 "params": [{"id": "project_id", "format": "int"}]},
            ],
            "profile_hash": "sha256:fake-edge-hash-0001",
        }

    @app.get("/api/v1/hub/handshake")
    def handshake():
        return {"identity": _IDENTITY,
                "profile_hash": "sha256:fake-edge-hash-0001",
                "station_count": station_count}

    @app.get("/api/v1/hub/profile")
    def profile():
        return _profile()

    @app.get("/api/v1/hub/health-summary")
    def health_summary():
        return {
            "node_id": node_id,
            "active_project_id": state["active_project_id"],
            "stations": [{
                "channel_id": ch,
                "is_running": True,
                "is_detecting": state["detecting"][ch],
                "logic_mode": "detection",
                "source_type": "synthetic",
                "fps_inference": 25.0,
            } for ch in channels],
            "resources": {"cpu": 12.0, "memory": 40.0, "disk": 55.0, "gpus": []},
            "detecting_stations": sum(1 for ch in channels
                                      if state["detecting"][ch]),
        }

    @app.post("/api/v1/hub/ops")
    def ops(payload: OpsPayload):
        state["ops_log"].append(payload.dict())
        if payload.action == "start_detection":
            state["detecting"][payload.channel] = True
            return {"ok": True, "action": payload.action,
                    "channel": payload.channel,
                    "message": f"ch{payload.channel} 检测已启动"}
        if payload.action == "stop_detection":
            state["detecting"][payload.channel] = False
            return {"ok": True, "action": payload.action,
                    "channel": payload.channel,
                    "message": f"ch{payload.channel} 检测已停止"}
        if payload.action == "ack_alarm":
            state["alarm_active"][payload.channel] = False
            return {"ok": True, "action": payload.action,
                    "channel": payload.channel,
                    "message": f"ch{payload.channel} 报警已消除"}
        if payload.action == "activate_project":
            state["active_project_id"] = payload.project_id
            for p in state["projects"]:
                p["is_active"] = p["id"] == payload.project_id
            return {"ok": True, "action": payload.action,
                    "channel": payload.channel,
                    "message": f"已激活项目 id={payload.project_id}"}
        return {"ok": False, "action": payload.action,
                "channel": payload.channel, "message": "unknown"}

    @app.get("/api/v1/hub/projects")
    def projects():
        return {"items": state["projects"]}

    # 事件通道 (P0-10): 测试用可注入事件流 —— e2e 往 state["cycle_events"]
    # append {"id","channel_id","result","event_name","reason","ts"} 即可被拉走
    state["cycle_events"] = []

    @app.get("/api/v1/hub/events")
    def events(cursor: Optional[int] = None, limit: int = 100,
               result: Optional[str] = None):
        rows = state["cycle_events"]
        if result == "ng":
            rows = [e for e in rows if e.get("result") == "NG"]
        max_id = max((e["id"] for e in state["cycle_events"]), default=0)
        if cursor is None:
            batch = rows[-limit:]
            next_cursor = max_id
        else:
            batch = [e for e in rows if e["id"] > cursor][:limit]
            next_cursor = max(cursor, batch[-1]["id"] if batch else 0, max_id
                              if len(batch) < limit else 0)
        return {"events": batch, "next_cursor": next_cursor}

    @app.get("/snapshot")
    def snapshot(channel: int = 0):
        return Response(content=_JPEG_1PX, media_type="image/jpeg")

    return app
