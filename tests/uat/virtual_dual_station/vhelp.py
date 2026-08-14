"""虚拟测试公共助手."""
import json
import socket
import time

import requests

B = "http://localhost:8001/api/v1"


def api(method, path, **kw):
    r = requests.request(method, f"{B}{path}", timeout=15, **kw)
    return r


def scan(code):
    s = socket.create_connection(("127.0.0.1", 24002), timeout=5)
    s.sendall(f"SCAN {code}\n".encode())
    resp = s.recv(4096).decode()
    s.close()
    return json.loads(resp)


def scanner_state():
    s = socket.create_connection(("127.0.0.1", 24002), timeout=5)
    s.sendall(b"STATE\n")
    buf = b""
    while not buf.endswith(b"\n"):
        d = s.recv(65536)
        if not d:
            break
        buf += d
    s.close()
    return json.loads(buf.decode())


def scanner_clear():
    s = socket.create_connection(("127.0.0.1", 24002), timeout=5)
    s.sendall(b"CLEAR\n")
    s.recv(4096)
    s.close()


def det_status(ch):
    return api("GET", f"/source/detection/results?channel={ch}").json()


def start_synth(ch, timeline, fps=10, name="case"):
    return api("POST", "/test/synthetic/start", json={
        "scenario_json": {"name": f"{name}_ch{ch}", "fps": fps, "timeline": timeline},
        "channel": ch,
    }).json()


def start_det(ch):
    return api("POST", f"/source/detection/start?channel={ch}",
               json={"conf": 0.25, "iou": 0.45}).json()


def stop_all(chs=(0, 1)):
    for ch in chs:
        api("POST", f"/source/detection/stop?channel={ch}")
        api("POST", f"/test/synthetic/stop?channel={ch}")


_TRACK_ID_REGISTRY = {}


def _tid(lbl, i):
    key = (lbl, i)
    if key not in _TRACK_ID_REGISTRY:
        _TRACK_ID_REGISTRY[key] = 100 + len(_TRACK_ID_REGISTRY) * 10
    return _TRACK_ID_REGISTRY[key]


def seg(frm, to, labels, gen=0):
    """labels: list of (label, count) or label str; track_id 稳定注册表分配.
    gen: 代次 — 同标签不同 gen 分配新 track_id, 模拟"撤走后重新摆放"的新工件."""
    dets = []
    for item in labels:
        if isinstance(item, str):
            item = (item, 1)
        lbl, cnt = item
        for i in range(cnt):
            dets.append({"label": lbl, "confidence": 0.95,
                         "bbox": [0.1 + 0.15 * i, 0.2 + 0.05 * (_tid((lbl, gen), i) % 7), 0.1, 0.1],
                         "track_id": _tid((lbl, gen), i)})
    return {"from": frm, "to": to, "detections": dets}


def last_cycles(ch, n=3):
    import sqlite3
    c = sqlite3.connect("/Users/tianjun/Projects/tianjun-ai-vision/backend/sql_app.db")
    try:
        rows = c.execute("""
            select dc.id, dc.is_good, dc.result_reason, dc.group_settle_result, dc.end_time
            from detection_cycles dc join detection_sessions ds on dc.session_id=ds.id
            where ds.channel_id=? order by dc.id desc limit ?""", (ch, n)).fetchall()
        return rows
    finally:
        c.close()


def workpiece_by_sn(sn):
    import sqlite3
    c = sqlite3.connect("/Users/tianjun/Projects/tianjun-ai-vision/backend/sql_app.db")
    try:
        return c.execute(
            "select id, serial_no, status from workpieces where serial_no=? order by id desc",
            (sn,)).fetchall()
    finally:
        c.close()


RESULTS = []


def check(label, ok, detail=""):
    RESULTS.append((label, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {label}  {detail}", flush=True)


def summary():
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n===== {len(RESULTS) - len(failed)}/{len(RESULTS)} passed =====", flush=True)
    for f in failed:
        print(f"  FAIL: {f[0]} {f[2]}", flush=True)
    return len(failed)
