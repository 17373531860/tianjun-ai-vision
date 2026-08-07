"""多工位新布局「满数据」预览截图 (路径 E: Playwright 路由注入).

目的: 用户想看"信息都弄全"(4 步骤模型 + 计数 + NG TOP3 + MES + 视频画面)时
三工位布局 / 网格总览 / 放大详情的真实观感, 评估留白。

- mock /api/v1/source/detection/results?channel=N → 完整检测 payload
- mock /api/v1/source/video_feed?channel=N → 静态工业画面 MJPEG(带检测框)
- 工位数用真实后端 API 切 (3 → 6), 结束还原

headless 截图, 不动鼠标。产物: evidence/shots_filled/*.png
"""
import base64
import json
import os
import sys
import urllib.parse

import cv2
import numpy as np
import requests
from playwright.sync_api import sync_playwright

API = "http://localhost:8002"
FRONTEND = "http://localhost:6002"
OUT_DIR = os.path.join(os.path.dirname(__file__), "shots_filled")
os.makedirs(OUT_DIR, exist_ok=True)

# ---------------------------------------------------------------- 画面生成
PALETTE = [(96, 72, 48), (48, 72, 96), (72, 96, 48), (60, 48, 88), (88, 60, 44), (44, 88, 72)]


def make_camera_frame(ch: int, steps, done_labels):
    """1280x720 模拟产线画面: 传送带 + 工件 + 检测框 OSD。"""
    h, w = 720, 1280
    img = np.full((h, w, 3), 34, np.uint8)
    # 背景渐变 + 传送带
    for y in range(h):
        img[y, :] = (34 + y * 20 // h, 36 + y * 18 // h, 38 + y * 16 // h)
    cv2.rectangle(img, (0, 430), (w, 700), (52, 56, 60), -1)
    for x in range(0, w, 90):
        cv2.line(img, (x, 430), (x - 50, 700), (70, 74, 78), 2)
    # 工件主体
    base = PALETTE[ch % len(PALETTE)]
    cv2.rectangle(img, (400, 250), (900, 560), base, -1)
    cv2.rectangle(img, (400, 250), (900, 560), tuple(c + 60 for c in base), 3)
    cv2.circle(img, (650, 400), 90, tuple(c + 40 for c in base), -1)
    cv2.circle(img, (650, 400), 90, (200, 200, 200), 2)
    for cx, cy in [(460, 300), (840, 300), (460, 510), (840, 510)]:
        cv2.circle(img, (cx, cy), 14, (140, 140, 150), -1)
        cv2.circle(img, (cx, cy), 14, (30, 30, 30), 2)
    # 检测框 (画在画面上, 模拟后端 OSD)
    boxes = [(420, 260, 180, 120), (700, 260, 190, 120), (430, 420, 200, 130), (690, 420, 200, 130)]
    for i, s in enumerate(steps):
        if i >= len(boxes):
            break
        x, y, bw, bh = boxes[i]
        color = (90, 220, 90) if s["label"] in done_labels else (200, 200, 60)
        cv2.rectangle(img, (x, y), (x + bw, y + bh), color, 3)
        tag = f"step-{i + 1} 0.9{(ch + i) % 10}"  # cv2 putText 不支持中文, OSD 用 ASCII
        (tw, th), _ = cv2.getTextSize(tag, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
        cv2.rectangle(img, (x, y - th - 10), (x + tw + 8, y), color, -1)
        cv2.putText(img, tag, (x + 4, y - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (10, 10, 10), 2, cv2.LINE_AA)
    # OSD 左上角
    cv2.putText(img, f"CAM-{ch + 1}  1920x1080  29.8fps", (18, 36),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (240, 240, 240), 2, cv2.LINE_AA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 82])
    assert ok
    return buf.tobytes(), img, boxes


def make_sop_thumb(frame_img, box):
    x, y, bw, bh = box
    crop = frame_img[max(0, y - 20):y + bh + 20, max(0, x - 20):x + bw + 20]
    crop = cv2.resize(crop, (160, 120))
    ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 80])
    assert ok
    return base64.b64encode(buf.tobytes()).decode()


# ---------------------------------------------------------------- 各工位剧情
CHANNEL_SCRIPTS = [
    dict(project="电机总成-装配检测", steps=[("锁付螺丝",), ("插接线束",), ("涂密封胶",), ("贴合格证",)],
         done=2, total=1286, ok=1261, ng=25, avg_ct=12.6, last_ct=11.8, cur_ct=6.3,
         ng_map={"涂密封胶": 14, "插接线束": 8, "贴合格证": 3},
         sn="SN20260807-0451", order=("WO-20260807-012", 451, 800),
         models=[("yolov8s-motor-v5", 27.4), ("hand-detector-v2", 24.1)]),
    dict(project="端盖压装检测", steps=[("放端盖",), ("压装到位",), ("同轴度确认",)],
         done=3, total=978, ok=969, ng=9, avg_ct=9.4, last_ct=9.1, cur_ct=1.2,
         ng_map={"压装到位": 6, "同轴度确认": 3},
         sn="SN20260807-0977", order=("WO-20260807-007", 178, 500),
         models=[("yolov8n-endcap-v3", 29.1)]),
    dict(project="包装线-装箱检测", steps=[("放缓冲棉",), ("装说明书",), ("放主机",), ("封箱贴标",)],
         done=1, total=1543, ok=1490, ng=53, avg_ct=15.2, last_ct=16.0, cur_ct=3.8,
         ng_map={"装说明书": 31, "封箱贴标": 15, "放缓冲棉": 7},
         sn="SN20260807-1544", order=("WO-20260807-021", 743, 1200),
         models=[("yolov8s-pack-v7", 26.8)]),
    dict(project="接线端子锁付", steps=[("对位",), ("锁付A",), ("锁付B",), ("目检",)],
         done=4, total=2210, ok=2168, ng=42, avg_ct=8.8, last_ct=8.5, cur_ct=0.4,
         ng_map={"锁付B": 25, "锁付A": 12, "目检": 5},
         sn="SN20260807-2211", order=("WO-20260807-018", 910, 1500),
         models=[("yolov8n-terminal-v1", 30.0)]),
    dict(project="散热片涂胶检测", steps=[("清洁表面",), ("涂导热胶",), ("贴散热片",)],
         done=0, total=655, ok=641, ng=14, avg_ct=11.1, last_ct=10.7, cur_ct=0.0,
         ng_map={"涂导热胶": 9, "贴散热片": 5},
         sn="SN20260807-0656", order=("WO-20260807-030", 155, 400),
         models=[("yolov8s-thermal-v2", 28.2)]),
    dict(project="铭牌激光打标", steps=[("上料定位",), ("打标",), ("视觉复核",), ("下料",)],
         done=2, total=1830, ok=1822, ng=8, avg_ct=7.6, last_ct=7.2, cur_ct=4.1,
         ng_map={"打标": 5, "视觉复核": 3},
         sn="SN20260807-1831", order=("WO-20260807-025", 630, 2000),
         models=[("yolov8n-mark-v4", 29.6)]),
]


def build_channel_assets():
    assets = {}
    for ch, sc in enumerate(CHANNEL_SCRIPTS):
        steps = [{"label": name[0], "displayLabel": name[0], "enabled": True} for name in sc["steps"]]
        done_labels = [s["label"] for s in steps[: sc["done"]]]
        jpeg, frame_img, boxes = make_camera_frame(ch, steps, done_labels)
        shots = {}
        for i, s in enumerate(steps):
            if i < len(boxes):
                shots[s["label"]] = make_sop_thumb(frame_img, boxes[i])
        payload = {
            "is_running": True,
            "is_detecting": True,
            "source_type": "hikvision",
            "fps": round(27.5 + ch * 0.4, 1),
            "latency": 38 + ch * 3,
            "models": [{"name": n, "model_loaded": True, "fps_inference": f} for n, f in sc["models"]],
            "project_config": {
                "project_name": sc["project"],
                "logic_mode": "sequential",
                "steps_config": steps,
            },
            "counters": {"总产量": sc["total"], "合格总数": sc["ok"], "不良总数": sc["ng"]},
            "average_cycle_time": sc["avg_ct"],
            "last_cycle_time": sc["last_ct"],
            "current_cycle_time": sc["cur_ct"],
            "current_cycle_steps": done_labels,
            "backup_covered_labels": [],
            "step_counts": {s["label"]: sc["total"] for s in steps},
            "last_step_durations": {s["label"]: round(1.6 + i * 0.9 + ch * 0.2, 1) for i, s in enumerate(steps)},
            "avg_step_durations": {s["label"]: round(1.8 + i * 0.8 + ch * 0.2, 1) for i, s in enumerate(steps)},
            # 当前周期口径 (ptMode=current 默认读这里): 只有已完成步骤有值
            "step_durations": {s["label"]: round(1.5 + i * 0.9 + ch * 0.2, 1) for i, s in enumerate(steps) if s["label"] in done_labels},
            "step_screenshots": shots,
            "ng_step_cycle_counts": sc["ng_map"],
            "detections": [],
            "recent_events": [],
            "pending_ack": {"active": False},
            "mes": {
                "workpiece": {"serial_no": sc["sn"], "status": "inspecting"},
                "order": {"order_no": sc["order"][0], "completed_qty": sc["order"][1], "planned_qty": sc["order"][2]},
            },
        }
        boundary = b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
        mjpeg = (boundary + jpeg + b"\r\n") * 3 + b"--frame\r\n"
        assets[ch] = {"payload": payload, "mjpeg": mjpeg}
    return assets


# ---------------------------------------------------------------- 工位数切换
def get_channel_count():
    r = requests.get(f"{API}/api/v1/workstations/", timeout=5)
    r.raise_for_status()
    return r.json()["channel_count"]


def set_channel_count(n):
    r = requests.post(f"{API}/api/v1/workstations/mode", json={"channel_count": n}, timeout=15)
    r.raise_for_status()


# ---------------------------------------------------------------- 主流程
def main():
    assets = build_channel_assets()
    original = get_channel_count()
    print(f"原始工位数: {original}")

    def route_results(route):
        q = urllib.parse.urlparse(route.request.url).query
        ch = int(urllib.parse.parse_qs(q).get("channel", ["0"])[0])
        payload = assets.get(ch, assets[0])["payload"]
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

    def route_feed(route):
        q = urllib.parse.urlparse(route.request.url).query
        ch = int(urllib.parse.parse_qs(q).get("channel", ["0"])[0])
        mjpeg = assets.get(ch, assets[0])["mjpeg"]
        route.fulfill(status=200, content_type="multipart/x-mixed-replace; boundary=frame", body=mjpeg)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1920, "height": 1080})
            page.route("**/api/v1/source/detection/results*", route_results)
            page.route("**/video_feed*", route_feed)

            # ---- 三工位 ----
            set_channel_count(3)
            page.goto(f"{FRONTEND}/#/monitor", wait_until="domcontentloaded")
            page.wait_for_timeout(3500)
            page.screenshot(path=f"{OUT_DIR}/01_triple_filled.png")
            print("已截: 01_triple_filled.png")

            # ---- 六工位网格 (auto=3x3) ----
            set_channel_count(6)
            page.reload(wait_until="domcontentloaded")
            page.wait_for_timeout(3500)
            page.screenshot(path=f"{OUT_DIR}/02_grid_auto_filled.png")
            print("已截: 02_grid_auto_filled.png")

            # ---- 2x2 布局 ----
            page.click("button:has-text('2×2')")
            page.wait_for_timeout(2000)
            page.screenshot(path=f"{OUT_DIR}/03_grid_2x2_filled.png")
            print("已截: 03_grid_2x2_filled.png")

            # ---- 放大详情 (工位1) ----
            page.click("button:has-text('自动')")
            page.wait_for_timeout(1200)
            cards = page.locator(".cursor-pointer.transition-all")
            cards.first.click()
            page.wait_for_timeout(2500)
            page.screenshot(path=f"{OUT_DIR}/04_zoom_detail_filled.png")
            print("已截: 04_zoom_detail_filled.png")

            browser.close()
    finally:
        set_channel_count(original)
        print(f"已还原工位数: {original}")


if __name__ == "__main__":
    main()
