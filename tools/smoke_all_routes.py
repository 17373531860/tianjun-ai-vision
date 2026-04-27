"""全量后端路由冒烟测试 (写入 smoke_report.txt, 不依赖 stdout)"""
import sys, os, traceback
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

REPORT_PATH = os.path.join(ROOT, "smoke_report.txt")
_log_lines = []


def log(msg=""):
    _log_lines.append(msg)
    # 立即追加到文件, 即使中途崩溃也保留
    with open(REPORT_PATH, "a", encoding="utf-8") as f:
        f.write(msg + "\n")


# 清空报告
open(REPORT_PATH, "w").close()

from fastapi.routing import APIRoute  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from backend.main import app  # noqa: E402


def fill_path_params(path: str, samples: dict) -> str:
    out = path
    for k, v in samples.items():
        out = out.replace("{" + k + "}", str(v))
    return out


def collect_routes():
    rows = []
    for r in app.routes:
        if not isinstance(r, APIRoute):
            continue
        for m in sorted(r.methods):
            if m == "HEAD":
                continue
            rows.append((m, r.path))
    rows.sort(key=lambda x: (x[1], x[0]))
    return rows


def discover_sample_ids(client):
    samples = {
        "session_id": 1, "cycle_id": 1, "step_id": 1, "video_id": 1,
        "project_id": 1, "model_id": 1, "operator_id": 1, "device_id": 1,
        "scanner_id": 1, "ws_id": 0, "channel_id": 0, "mode_id": 1,
        "gpu_id": 0, "id": 1, "step_label": "step1",
        "date": "2026-01-01", "name": "default", "key": "test",
    }
    try:
        r = client.get("/api/v1/projects")
        if r.status_code == 200 and r.json():
            samples["project_id"] = r.json()[0].get("id", 1)
    except Exception:
        pass
    try:
        r = client.get("/api/v1/models")
        if r.status_code == 200 and r.json():
            samples["model_id"] = r.json()[0].get("id", 1)
    except Exception:
        pass
    try:
        r = client.get("/api/v1/data/sessions?limit=1")
        if r.status_code == 200:
            data = r.json()
            arr = data if isinstance(data, list) else data.get("sessions", [])
            if arr:
                samples["session_id"] = arr[0].get("id", 1)
    except Exception:
        pass
    return samples


# 这些会真改硬件 / 删数据, 跳过
SKIP_PATHS = {
    "/api/v1/source/detection/start",
    "/api/v1/source/detection/stop",
    "/api/v1/source/detection/standby",
    "/api/v1/source/detection/pause",
    "/api/v1/source/detection/resume",
    "/api/v1/source/detection/resume-inference",
    "/api/v1/source/detection/reset-stats",
    "/api/v1/source/detection/clear_pending_scan",
    "/api/v1/source/detection/rebind",
    "/api/v1/data/clear/all",
    "/api/v1/data/clear/range",
    "/api/v1/data/cleanup/run",
    "/api/v1/data/backup/database",
    "/api/v1/source/start",
    "/api/v1/source/stop",
    "/api/v1/source/start_camera",
    "/api/v1/source/start_rtsp",
    "/api/v1/source/start_video",
    "/api/v1/source/start_hcnetsdk",
    "/api/v1/source/start_hikvision",
    "/api/v1/source/start_industrial",
    "/api/v1/source/hikvision/start",
    "/api/v1/source/hcnetsdk/start",
    # 真实硬件 IO / 阻塞型探测
    "/api/v1/scanner/wmax/auto-discover",
    "/api/v1/scanner/discover",
    "/api/v1/scanner/devices/test",
    "/api/v1/scanner/trigger",
    "/api/v1/external_devices/test",
    "/api/v1/external_devices/{device_id}/test",
    "/api/v1/alarm/test",
    "/api/v1/alarm/buzzer/test",
    "/api/v1/alarm/light/test",
    "/api/v1/mes/hooks/test",
    "/api/v1/mes/test_connection",
    "/api/v1/voice/play",
    "/api/v1/voice/test",
    "/api/v1/source/snapshot",
    "/api/v1/source/snapshot/all",
    "/api/v1/source/restart",
    "/api/v1/source/reload",
    "/api/v1/system/restart",
    "/api/v1/system/shutdown",
    # MJPEG 流是 chunked 长连接, 不能用普通请求
    "/video_feed",
    "/snapshot",
    # 关机流程
    "/api/v1/source/shutdown/complete",
    "/api/v1/source/shutdown/step/{step}",
}


def main():
    client = TestClient(app)
    samples = discover_sample_ids(client)
    log(f"[INFO] sample IDs: {samples}")

    routes = collect_routes()
    log(f"[INFO] total routes (excl. HEAD): {len(routes)}")

    bucket = defaultdict(list)
    server_errors = []

    origin = "http://localhost:6001"

    for idx, (method, path) in enumerate(routes):
        url = fill_path_params(path, samples)

        if path in SKIP_PATHS:
            bucket["SKIP"].append((method, path))
            continue

        log(f"[{idx:03d}] -> {method:<7} {path}")
        try:
            if method == "GET":
                r = client.get(url, headers={"Origin": origin}, timeout=8)
            elif method == "OPTIONS":
                r = client.options(url, headers={"Origin": origin}, timeout=4)
            elif method == "DELETE":
                # DELETE 多带路径参数, 不传 body
                r = client.delete(url, headers={"Origin": origin}, timeout=8)
            else:
                r = client.request(method, url, json={}, headers={"Origin": origin}, timeout=8)
        except Exception as e:
            bucket["EXC"].append((method, path, repr(e)[:200]))
            continue

        sc = r.status_code
        if 200 <= sc < 300:
            bucket["2xx"].append((method, path, sc))
        elif 300 <= sc < 400:
            bucket["3xx"].append((method, path, sc))
        elif 400 <= sc < 500:
            bucket["4xx"].append((method, path, sc))
        else:
            bucket["5xx"].append((method, path, sc))
            try:
                body = r.text[:500]
            except Exception:
                body = "(no body)"
            server_errors.append((method, path, sc, body))

    log("")
    log("=" * 80)
    log(f"  TOTAL  : {len(routes)}")
    log(f"  v 2xx  : {len(bucket['2xx'])}")
    log(f"  o 3xx  : {len(bucket['3xx'])}")
    log(f"  o 4xx  : {len(bucket['4xx'])}  (大多缺参/路径不匹配, 正常)")
    log(f"  X 5xx  : {len(bucket['5xx'])}  <- 必修")
    log(f"  X EXC  : {len(bucket['EXC'])}  <- 必修")
    log(f"  - SKIP : {len(bucket['SKIP'])}  (危险副作用, 单独验证)")
    log("=" * 80)

    if bucket["5xx"]:
        log("")
        log(">>> 5xx 详细 (必修): <<<")
        for m, p, sc, body in server_errors:
            log(f"  X {sc} {m:<6} {p}")
            if body:
                log(f"     body: {body}")

    if bucket["EXC"]:
        log("")
        log(">>> 异常详细: <<<")
        for m, p, e in bucket["EXC"]:
            log(f"  X {m:<6} {p}  ->  {e}")

    if bucket["4xx"]:
        log("")
        log(">>> 4xx 列表 (供肉眼复核, 多为正常缺参): <<<")
        for m, p, sc in bucket["4xx"]:
            log(f"  o {sc} {m:<6} {p}")

    if bucket["2xx"]:
        log("")
        log(">>> 2xx 列表: <<<")
        for m, p, sc in bucket["2xx"]:
            log(f"  v {sc} {m:<6} {p}")

    return 0 if (not bucket["5xx"] and not bucket["EXC"]) else 1


if __name__ == "__main__":
    rc = 2
    try:
        rc = main()
    except SystemExit as e:
        log(f"[FATAL-SYSEXIT] code={e.code}")
        log(traceback.format_exc())
    except BaseException:
        log("[FATAL] " + traceback.format_exc())
    log(f"[DONE] exit={rc}")
    os._exit(rc)
