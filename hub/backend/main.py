"""Fleet Hub 服务入口 (RFC 15 M1 骨架)。

独立部署形态: 单独服务器上
`python -m uvicorn hub.backend.main:create_app --factory --port 9100`。
与主程序零共享运行时 (不 import backend.*), 只通过 HTTP 访问边缘机。

环境变量:
  HUB_DATA_DIR          数据目录 (hub.db + Fernet 密钥), 默认 hub/data/
  HUB_ADMIN_PASSWORD    首次播种 admin 的口令, 默认 admin123
  HUB_ENABLE_POLLER     "0" 时不起轮询循环 (测试态), 默认开
  HUB_HEALTH_INTERVAL / HUB_PROFILE_INTERVAL / HUB_EDGE_TIMEOUT  轮询节奏
"""
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from hub.backend.config import HUB_API_PREFIX, get_data_dir
from hub.backend.db import init_db
from hub.backend.poller import HubPoller


def create_app() -> FastAPI:
    data_dir = get_data_dir()
    init_db(data_dir)

    # 播种默认管理员
    from hub.backend import db as hubdb
    from hub.backend.auth import seed_admin
    _db = hubdb.SessionLocal()
    try:
        seed_admin(_db)
    finally:
        _db.close()

    loops_enabled = os.environ.get("HUB_ENABLE_POLLER", "1") != "0"
    poller = HubPoller(loops_enabled=loops_enabled)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if loops_enabled:
            await poller.start_all()
            print(f"[Hub] 轮询器已启动 ({len(poller._tasks)} 个节点循环)")
        yield
        await poller.stop_all()

    app = FastAPI(
        title="TianJun Fleet Hub",
        description="天军 AI 视觉检测 · Web 集中管控枢纽 (RFC 15)",
        lifespan=lifespan,
    )
    app.state.poller = poller

    # M8: WS 推送加速器 (轮询真相源不变, WS 只发"有变化"提示帧)
    from hub.backend.ws import WsHub
    ws_hub = WsHub()
    app.state.ws_hub = ws_hub
    poller.ws_hub = ws_hub

    # 内网部署 + 前端独立包跨端口, M1 先全放; M4 交付前收紧到配置白名单
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"],
        allow_methods=["*"], allow_headers=["*"])

    from hub.backend.audit import router as audit_router
    from hub.backend.auth import router as auth_router
    from hub.backend.events import router as events_router
    from hub.backend.lock_manager import router as locks_router
    from hub.backend.node_registry import router as nodes_router
    from hub.backend.notify import router as notify_router
    from hub.backend.ops import router as ops_router
    from hub.backend.stats import router as stats_router
    from hub.backend.wall import router as wall_router
    app.include_router(auth_router, prefix=HUB_API_PREFIX, tags=["hub-auth"])
    app.include_router(nodes_router, prefix=HUB_API_PREFIX, tags=["hub-nodes"])
    app.include_router(wall_router, prefix=HUB_API_PREFIX, tags=["hub-wall"])
    app.include_router(locks_router, prefix=HUB_API_PREFIX, tags=["hub-locks"])
    app.include_router(ops_router, prefix=HUB_API_PREFIX, tags=["hub-ops"])
    app.include_router(events_router, prefix=HUB_API_PREFIX, tags=["hub-events"])
    app.include_router(stats_router, prefix=HUB_API_PREFIX, tags=["hub-stats"])
    app.include_router(audit_router, prefix=HUB_API_PREFIX, tags=["hub-audit"])
    app.include_router(notify_router, prefix=HUB_API_PREFIX, tags=["hub-notify"])
    from hub.backend.ws import router as ws_router
    app.include_router(ws_router, prefix=HUB_API_PREFIX)   # WS: /api/v1/ws

    @app.get("/health", summary="枢纽自身健康检查")
    def health():
        """存活探针 (部署脚本/负载均衡用)"""
        return {"ok": True, "service": "tianjun-fleet-hub"}

    # 前端构建产物随枢纽同端口分发 (hub/frontend/dist 存在时):
    # 单服务部署, 浏览器与 API 同源免 CORS
    from pathlib import Path
    dist = Path(__file__).resolve().parents[1] / "frontend" / "dist"
    if dist.is_dir():
        from fastapi.staticfiles import StaticFiles
        app.mount("/", StaticFiles(directory=str(dist), html=True),
                  name="hub-frontend")

    return app


# 生产启动 (factory 模式, 避免 import 期建 DB 的副作用):
#   python -m uvicorn hub.backend.main:create_app --factory --host 0.0.0.0 --port 9100
# 测试同样走 create_app() 显式构建, 本模块无模块级 app 实例。

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(create_app(), host="0.0.0.0",
                port=int(os.environ.get("HUB_PORT", "9100")))
