"""边缘机 HTTP 客户端 — 枢纽访问边缘的唯一出口。

- 收口在这一层的原因 (RFC 15 §14 "审计被内部调用绕过"): M3 写转发也走这里,
  在单一出口挂审计/超时/重试纪律。
- transport 可注入: 测试用 httpx.ASGITransport 直连主程序 app (进程内, 无网络),
  生产为 None 走真实 TCP。
"""
from typing import Any, Callable, Dict, Optional

import httpx

from hub.backend.config import EDGE_TIMEOUT_S

# 测试钩子: (base_url) -> httpx.BaseTransport | None
_transport_factory: Optional[Callable[[str], Optional[httpx.BaseTransport]]] = None


def set_transport_factory(fn) -> None:
    """测试注入 ASGITransport / MockTransport; 传 None 复位为真实网络。"""
    global _transport_factory
    _transport_factory = fn


class EdgeError(Exception):
    """边缘访问失败 (网络/超时/非 2xx), 携带可展示 detail"""

    def __init__(self, detail: str, status_code: Optional[int] = None):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


class EdgeClient:
    """一台边缘机的会话客户端。用完调 aclose() (或 async with)。"""

    def __init__(self, base_url: str, api_key: Optional[str] = None,
                 timeout: float = EDGE_TIMEOUT_S):
        transport = _transport_factory(base_url) if _transport_factory else None
        headers = {"X-API-Key": api_key} if api_key else {}
        self._client = httpx.AsyncClient(
            base_url=base_url, headers=headers,
            timeout=timeout, transport=transport)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.aclose()

    async def aclose(self):
        await self._client.aclose()

    async def _request(self, method: str, path: str,
                       json: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        try:
            r = await self._client.request(method, path, json=json)
        except httpx.HTTPError as e:
            raise EdgeError(f"边缘机无法访问: {e.__class__.__name__}: {e}") from e
        if r.status_code == 404:
            raise EdgeError("边缘机未开启枢纽接入 (hub_access.enabled=false) 或版本过低",
                            status_code=404)
        if r.status_code in (401, 403):
            raise EdgeError(f"边缘机拒绝访问 (HTTP {r.status_code}): API Key 无效或 scope 不足",
                            status_code=r.status_code)
        if r.status_code >= 400:
            # 业务拒绝 (如 409 检测中/模型未就绪): 透传边缘 detail 给枢纽 UI 原样展示
            detail = None
            try:
                detail = r.json().get("detail")
            except Exception:
                pass
            raise EdgeError(detail or f"边缘机响应异常 (HTTP {r.status_code})",
                            status_code=r.status_code)
        return r.json()

    async def _get(self, path: str) -> Dict[str, Any]:
        return await self._request("GET", path)

    # -------- 边缘 hub/* 端点 (M0 已落地三件) --------

    async def handshake(self) -> Dict[str, Any]:
        return await self._get("/api/v1/hub/handshake")

    async def profile(self) -> Dict[str, Any]:
        return await self._get("/api/v1/hub/profile")

    async def health_summary(self) -> Dict[str, Any]:
        return await self._get("/api/v1/hub/health-summary")

    async def ops(self, action: str, channel: int = 0,
                  project_id: Optional[int] = None) -> Dict[str, Any]:
        """写操作转发 (M3): start_detection / stop_detection / activate_project。

        边缘业务拒绝 (409 等) 以 EdgeError(status_code, detail) 抛出, 由 op_gateway
        决定透传; 网络失败同样 EdgeError (status_code=None)。
        """
        body: Dict[str, Any] = {"action": action, "channel": channel}
        if project_id is not None:
            body["project_id"] = project_id
        return await self._request("POST", "/api/v1/hub/ops", json=body)

    async def list_projects(self) -> Dict[str, Any]:
        """精简项目列表 (id/name/is_active), 枢纽切项目下拉用。"""
        return await self._get("/api/v1/hub/projects")

    async def events(self, cursor: Optional[int] = None, limit: int = 100,
                     result: Optional[str] = None) -> Dict[str, Any]:
        """周期结算事件增量拉取 (P0-10)。cursor=None 表示首拉 (从现在订阅)。"""
        params = [f"limit={limit}"]
        if cursor is not None:
            params.append(f"cursor={cursor}")
        if result:
            params.append(f"result={result}")
        return await self._get(f"/api/v1/hub/events?{'&'.join(params)}")

    async def live(self, channel: int = 0) -> Dict[str, Any]:
        """工位实时投影 (M7.5 值班读面): 计数/步骤/周期/近期事件的白名单裁剪。"""
        return await self._get(f"/api/v1/hub/live?channel={channel}")

    # -------- 边缘复用端点 (RFC 15 §9.2, 零改动消费) --------

    async def snapshot(self, channel: int) -> bytes:
        """单帧 JPEG (边缘根路径 /snapshot, 无源时边缘自己画黑帧仍回 200)"""
        try:
            r = await self._client.get("/snapshot", params={"channel": channel})
        except httpx.HTTPError as e:
            raise EdgeError(f"边缘机快照失败: {e.__class__.__name__}: {e}") from e
        if r.status_code >= 400:
            raise EdgeError(f"边缘机快照响应异常 (HTTP {r.status_code})",
                            status_code=r.status_code)
        return r.content
