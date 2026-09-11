# -*- coding: utf-8 -*-
"""局域网工位屏支持 —— 工作站把前端 dist 通过 HTTP 送给一体机浏览器。

场景（一拖多）：
    一台工作站跑推理（海康取流 + YOLO + 周期结算 + 项目库），每个工位一台一体机，
    网线进千兆交换机。一体机不装软件，Chrome/Edge 全屏打开
    ``http://<工作站IP>:8001/#/monitor?channel=N&kiosk=1&readonly=0``。

两件事：
    1. **静态托管**：把 ``frontend/dist`` 从后端同一个端口送出去，页面与
       ``/api/v1``、``/video_feed``、``/snapshot`` 天然同源，不需要额外反代，
       也不需要一体机配后端地址。
    2. **局域网 CORS**：默认 CORS 只认 localhost/127.0.0.1，一体机用内网 IP
       打开时跨域会被拦（开发态 Vite 6001 + 后端 8001 就是跨域）。这里放开
       RFC1918 私网段，公网地址仍然不放。

设计约束：
    - dist 不存在（纯 Electron 出厂形态走 file://）→ 整个模块零动作，行为不变。
    - 前端是 hash 路由（``/#/monitor``），服务端只需要送 ``/`` 和静态资源，
      不需要 history 模式的 rewrite；仍保留 SPA 兜底以防日后改路由模式。
    - 捕获路由注册在所有业务路由之后，且 ``/api`` 等保留前缀直接 404，
      避免打错的接口地址被喂回一张 index.html（调试时极难发现）。
"""
from __future__ import annotations

import mimetypes
import os
from typing import Optional
from urllib.parse import unquote

from backend.core.config import BASE_DIR


# ==================== 局域网 CORS ====================

# 放行 localhost + RFC1918 私网段（10/8、172.16/12、192.168/16）任意端口。
# 公网地址不在内：工控机直连公网不是本项目的部署形态，放开等于白送攻击面。
# CORS_ALLOW_ORIGINS 环境变量仍然优先（见 main.py），客户要收紧/放宽都从那里走。
LAN_ORIGIN_REGEX = (
    r"^https?://("
    r"localhost"
    r"|127\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3}"
    r"|172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r")(:\d+)?$"
)


# ==================== dist 目录定位 ====================

_DIST_ENV = "TIANJUN_WEB_DIST"

# BASE_DIR = <安装根>/backend；两种形态各一个候选：
#   源码/开发：<repo>/frontend/dist
#   Electron 打包：resources/backend + resources/app/dist（见 electron/package.json
#                  extraResources: from ../frontend/dist → to app/dist）
_DIST_CANDIDATES = (
    os.path.join(BASE_DIR, os.pardir, "frontend", "dist"),
    os.path.join(BASE_DIR, os.pardir, "app", "dist"),
)


def resolve_web_dist_dir() -> Optional[str]:
    """定位前端 dist 目录；找不到或被显式关闭时返回 None。

    ``TIANJUN_WEB_DIST=off``（或 0/false/no）= 关掉局域网静态托管，
    工作站只当纯 API 服务用；其它非空值当作自定义 dist 路径。
    """
    override = (os.environ.get(_DIST_ENV) or "").strip()
    if override:
        if override.lower() in ("0", "off", "false", "no"):
            return None
        return override if _has_index(override) else None

    for candidate in _DIST_CANDIDATES:
        path = os.path.abspath(candidate)
        if _has_index(path):
            return path
    return None


def _has_index(path: str) -> bool:
    try:
        return os.path.isfile(os.path.join(path, "index.html"))
    except OSError:
        return False


def web_index_file() -> Optional[str]:
    """前端 index.html 的绝对路径；没有托管能力时返回 None。"""
    dist = resolve_web_dist_dir()
    if not dist:
        return None
    index = os.path.join(dist, "index.html")
    return index if os.path.isfile(index) else None


# ==================== 保留路径 ====================

# 这些一级路径归后端业务所有。落到捕获路由 = 地址写错了，必须照旧回 JSON 404，
# 不能兜底成 index.html（否则前端拿到 200 + 一坨 HTML，排查要绕好几个弯）。
RESERVED_PATH_HEADS = frozenset({
    "api",
    "docs",
    "redoc",
    "openapi.json",
    "health",
    "uploads",
    "recordings",
    "video_feed",
    "snapshot",
    "ws",
})


def is_reserved_path(url_path: str) -> bool:
    """判断请求路径是否属于后端保留前缀（不该由静态托管兜底）。"""
    head = (url_path or "").lstrip("/").split("/", 1)[0]
    return head.lower() in RESERVED_PATH_HEADS


# ==================== 静态文件解析（防目录穿越） ====================

def resolve_static_file(dist_dir: str, url_path: str) -> Optional[str]:
    """把 URL 路径映射到 dist 内的真实文件；越界或不存在返回 None。

    穿越防护：先 unquote（挡住 ``%2e%2e%2f``）再 normpath，最后强制要求
    结果仍在 dist 根之内。绝对路径（``/etc/passwd``、``C:\\...``）会被
    ``os.path.join`` 吃掉前缀，同样被根前缀检查拦下。
    """
    if not dist_dir:
        return None
    rel = unquote(url_path or "").lstrip("/")
    if not rel:
        rel = "index.html"
    if "\x00" in rel:
        return None

    root = os.path.abspath(dist_dir)
    candidate = os.path.abspath(os.path.join(root, rel))
    if candidate != root and not candidate.startswith(root + os.sep):
        return None
    return candidate if os.path.isfile(candidate) else None


def _ensure_web_mimetypes() -> None:
    """显式登记前端资源 MIME。

    Windows 的 mimetypes 会读注册表，装过某些软件的工控机上 ``.js`` 会被映射成
    ``text/plain``，浏览器拒绝执行 ESM，表现为一体机打开一片空白。
    """
    for suffix, mime in (
        (".js", "text/javascript"),
        (".mjs", "text/javascript"),
        (".css", "text/css"),
        (".json", "application/json"),
        (".svg", "image/svg+xml"),
        (".wasm", "application/wasm"),
    ):
        mimetypes.add_type(mime, suffix)


# ==================== 挂载 ====================

def wants_html(accept_header: str) -> bool:
    """请求是不是在要一张网页（决定要不要做 SPA 兜底）。"""
    return "text/html" in (accept_header or "").lower()


def mount_web_dist(app) -> Optional[str]:
    """装上前端静态托管；返回实际托管的 dist 目录（未托管则 None）。

    实现成 **404 兜底中间件**，而不是 ``@app.get("/{path:path}")`` 捕获路由。
    捕获路由踩过两个坑，都是真回归：

    1. 它匹配一切路径，于是**运行时才注册**的路由（``mes_inbound`` 的客户自定义
       接收路径别名、运行中安装/启用的插件 router）全被它盖住 —— 那些路由在
       app 启动后才 append 到路由表，排在捕获路由后面，永远命中不到。
    2. 它只登记 GET，于是 ``POST /不存在的路径`` 从 404 变成 405（路径匹配上了、
       方法不允许），打乱了既有的错误语义。

    兜底中间件把顺序反过来：先让正常路由匹配，只有**真的 404** 才去 dist 里找
    文件。动态注册的路由天然优先，非 GET 方法的错误码也原样不动。
    """
    from fastapi.responses import FileResponse

    dist_dir = resolve_web_dist_dir()
    if not dist_dir:
        print("[WebUI] 未发现前端 dist, 跳过局域网静态托管 (纯 API 模式)")
        return None

    _ensure_web_mimetypes()
    index_path = os.path.join(dist_dir, "index.html")

    async def _drain(response) -> None:
        """兜底替换掉的那个 404 响应体要读干净，不留悬挂的流。"""
        body_iterator = getattr(response, "body_iterator", None)
        if body_iterator is None:
            return
        try:
            async for _chunk in body_iterator:
                pass
        except Exception:  # noqa: BLE001 — 丢弃响应体失败不该影响兜底
            pass

    @app.middleware("http")
    async def serve_web_dist_on_404(request, call_next):
        response = await call_next(request)
        if response.status_code != 404 or request.method not in ("GET", "HEAD"):
            return response

        path = request.url.path
        # /api、/docs、/video_feed 等归后端：地址打错了就该照旧回 JSON 404，
        # 不能兜底成 200 + 一张 index.html（那种 bug 排查起来要绕好几个弯）。
        if is_reserved_path(path):
            return response

        file_path = resolve_static_file(dist_dir, path)
        if file_path is None and wants_html(request.headers.get("accept", "")):
            # hash 路由下正常走不到这里；留着以防日后切 history 模式。
            file_path = index_path
        if file_path is None:
            return response

        await _drain(response)
        if file_path == index_path:
            # index.html 绝不缓存：升级换了带 hash 的新资源名，老 index 会一直
            # 指向已删除的旧文件，一体机表现为刷新也白屏。
            return FileResponse(
                index_path,
                media_type="text/html",
                headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
            )
        return FileResponse(file_path)

    app.state.web_dist_dir = dist_dir
    print(f"[WebUI] 局域网静态托管已启用: {dist_dir}")
    return dist_dir
