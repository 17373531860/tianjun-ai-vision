"""WebSocket 推送加速器 (M8, RFC 15 §10.2 P1)。

架构纪律 (RFC 既定): **轮询是真相源, WS 只是加速器**。
- 服务端只推「有变化」的提示帧 {"topic": "wall"|"events"}, 不带业务载荷
  —— 前端收到即触发一次既有 refresh(), 零状态同步复杂度。
- WS 断了体验自动退化回纯轮询 (2s), 不丢任何功能; 前端静默指数退避重连。
- 鉴权: 浏览器 WebSocket API 无法带 Authorization 头, token 走查询参数
  (?token=), 复用登录 token 体系; 无效即 4401 关闭。
"""
import asyncio
import time
from typing import Dict, Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from hub.backend import db as hubdb
from hub.backend.models import HubToken
from hub.backend.security import hash_token

router = APIRouter()


class WsHub:
    """连接管理 + 提示广播 (每 create_app 一份, 挂 app.state.ws_hub)。

    notify() 从 poller 的 asyncio 循环里调; 每 topic 0.3s 节流合并
    (多节点同轮变化只发一帧), 发送失败即摘除连接 (下轮轮询兜底)。
    """

    THROTTLE_S = 0.3

    def __init__(self):
        self.clients: Set[WebSocket] = set()
        self._last_sent: Dict[str, float] = {}
        self._pending: Set[str] = set()
        self._flush_task: asyncio.Task | None = None

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.clients.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self.clients.discard(ws)

    async def notify(self, topic: str) -> None:
        """广播提示帧 (节流合并; 无连接零开销)。"""
        if not self.clients:
            return
        now = time.monotonic()
        if now - self._last_sent.get(topic, 0.0) < self.THROTTLE_S:
            # 节流窗内: 记 pending, 由补发任务在窗口结束时合并发出,
            # 保证"最后一次变化"不会被节流吞掉 (事件戛然而止也能推到)
            self._pending.add(topic)
            if self._flush_task is None or self._flush_task.done():
                self._flush_task = asyncio.create_task(self._flush_later())
            return
        self._last_sent[topic] = now
        await self._broadcast(topic)

    async def _flush_later(self) -> None:
        await asyncio.sleep(self.THROTTLE_S)
        pending, self._pending = self._pending, set()
        for topic in pending:
            self._last_sent[topic] = time.monotonic()
            await self._broadcast(topic)

    async def _broadcast(self, topic: str) -> None:
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_json({"topic": topic})
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)


def _token_valid(token: str) -> bool:
    """WS 握手鉴权: token → 活跃用户 (同 auth.get_current_user 口径)"""
    if not token:
        return False
    db = hubdb.SessionLocal()
    try:
        row = db.query(HubToken).filter(
            HubToken.token_hash == hash_token(token)).first()
        return bool(row and row.user and row.user.active)
    finally:
        db.close()


@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket, token: str = ""):
    if not _token_valid(token):
        await ws.close(code=4401)   # 应用层"未授权"关闭码
        return
    hub: WsHub = ws.app.state.ws_hub
    await hub.connect(ws)
    try:
        while True:
            # 客户端不需要发东西; 收帧只为感知断开 (含浏览器关页)
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        hub.disconnect(ws)
