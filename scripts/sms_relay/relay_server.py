#!/usr/bin/env python3
"""
云端短信中转服务 — 配合主程序统一短信通道 generic_http 使用 (v3.46 过渡通道)

链路:
  工厂主程序 --POST /send--> 本服务(云服务器) <--GET /pull 轮询-- 手机(SIM卡真实发送)

设计约束:
  - 纯 Python 标准库 (3.6+), 云服务器上 `python3 relay_server.py` 直接跑, 零 pip 依赖
  - 任务持久化 JSON 文件, 重启不丢
  - Bearer token 鉴权 (手机端 app 不便设 header 时可用 ?token= 查询参数)

端点:
  POST /send    {phones:[...], content:"..."}   主程序推送日报 → 入队
  GET  /pull    ?limit=3                         手机轮询取待发任务 (取走即标记 dispatched)
  POST /report  {task_id, success, detail}       手机回报发送结果 (可选)
  GET  /tasks   ?limit=20                        最近任务与状态 (排查用)
  GET  /health                                   探活

启动:
  python3 relay_server.py --port 8080 --token 换成你自己的长随机串
"""
import argparse
import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler

try:
    from http.server import ThreadingHTTPServer
except ImportError:  # Python 3.6 (CentOS/Alinux 自带) 没有 ThreadingHTTPServer
    import socketserver
    from http.server import HTTPServer

    class ThreadingHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
        daemon_threads = True
from pathlib import Path
from urllib.parse import urlparse, parse_qs

QUEUE_FILE = Path(__file__).with_name("sms_relay_queue.json")
MAX_KEEP = 500  # 队列文件最多保留的历史任务数

# 语义约定 (为让手机端 MacroDroid 配置最简):
#   - /send 收到 N 个号码 → 拆成 N 条任务, 每条只含一个 phone
#   - /pull 取走即标记 dispatched 且**永不重派** → 手机端不回报也绝不会重复发送
#     (代价: 手机拉走后发送失败该条即丢, 日报场景可接受; /report 仅用于留痕)

_lock = threading.Lock()
_tasks = []  # [{id, phones, content, status, created_at, dispatched_at, reported}]
TOKEN = ""


def _load():
    global _tasks
    try:
        _tasks = json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
    except Exception:
        _tasks = []


def _save():
    try:
        QUEUE_FILE.write_text(
            json.dumps(_tasks[-MAX_KEEP:], ensure_ascii=False, indent=1),
            encoding="utf-8")
    except Exception as e:
        print(f"[relay] 持久化失败: {e}")


class Handler(BaseHTTPRequestHandler):

    # ---------- 基础 ----------

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authed(self):
        auth = self.headers.get("Authorization", "")
        if auth == f"Bearer {TOKEN}":
            return True
        qs = parse_qs(urlparse(self.path).query)
        return (qs.get("token") or [""])[0] == TOKEN

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def log_message(self, fmt, *args):
        print(f"[relay] {self.address_string()} {fmt % args}")

    # ---------- 路由 ----------

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/health":
            return self._json(200, {"ok": True, "pending": _count("pending")})
        if not self._authed():
            return self._json(401, {"success": False, "error": "unauthorized"})
        if path == "/pull":
            return self._pull()
        if path == "/tasks":
            return self._list()
        return self._json(404, {"success": False, "error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        if not self._authed():
            return self._json(401, {"success": False, "error": "unauthorized"})
        if path == "/send":
            return self._send()
        if path == "/report":
            return self._report()
        return self._json(404, {"success": False, "error": "not found"})

    # ---------- 业务 ----------

    def _send(self):
        body = self._read_body()
        phones = [str(p).strip() for p in (body.get("phones") or []) if str(p).strip()]
        content = (body.get("content") or "").strip()
        if not phones or not content:
            return self._json(400, {"success": False, "error": "phones/content 必填"})
        ids = []
        with _lock:
            for phone in phones:  # 每号码一条任务, 手机端免循环
                task = {
                    "id": uuid.uuid4().hex[:12],
                    "phone": phone,
                    "phones": [phone],  # 兼容字段
                    "content": content,
                    "status": "pending",
                    "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "dispatched_at": None,
                    "reported": None,
                }
                _tasks.append(task)
                ids.append(task["id"])
            _save()
        print(f"[relay] 入队 {len(ids)} 条 → {phones}: {content[:60]}")
        return self._json(200, {"success": True, "task_id": ids[0], "task_ids": ids})

    def _pull(self):
        qs = parse_qs(urlparse(self.path).query)
        limit = min(int((qs.get("limit") or ["1"])[0]), 10)
        picked = []
        with _lock:
            for t in _tasks:
                if len(picked) >= limit:
                    break
                if t["status"] == "pending":  # 取走即完结, 永不重派 → 绝无重复短信
                    t["status"] = "dispatched"
                    t["dispatched_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                    picked.append({"id": t["id"],
                                   "phone": t.get("phone") or (t.get("phones") or [""])[0],
                                   "phones": t.get("phones") or [],
                                   "content": t["content"]})
            if picked:
                _save()
        return self._json(200, {"tasks": picked})

    def _report(self):
        body = self._read_body()
        task_id = body.get("task_id")
        with _lock:
            for t in _tasks:
                if t["id"] == task_id:
                    t["status"] = "sent" if body.get("success") else "failed"
                    t["reported"] = {"success": bool(body.get("success")),
                                     "detail": str(body.get("detail") or "")[:200],
                                     "at": time.strftime("%Y-%m-%d %H:%M:%S")}
                    _save()
                    return self._json(200, {"success": True})
        return self._json(404, {"success": False, "error": f"task {task_id} 不存在"})

    def _list(self):
        qs = parse_qs(urlparse(self.path).query)
        limit = min(int((qs.get("limit") or ["20"])[0]), 100)
        with _lock:
            items = [{k: v for k, v in t.items() if not k.startswith("_")}
                     for t in _tasks[-limit:]]
        return self._json(200, {"total": len(_tasks), "tasks": items})


def _count(status):
    with _lock:
        return sum(1 for t in _tasks if t["status"] == status)


def main():
    global TOKEN
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--token", required=True, help="Bearer 鉴权 token, 用长随机串")
    args = ap.parse_args()
    TOKEN = args.token

    _load()
    print(f"[relay] 短信中转服务启动 :{args.port}  队列文件={QUEUE_FILE}")
    ThreadingHTTPServer(("0.0.0.0", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
