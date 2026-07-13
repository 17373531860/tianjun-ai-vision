#!/usr/bin/env python
"""sensor-clean 插件主环境实测: 播客户视频 → 读插件总产量。

用法: python _sc_live_repro.py <small|normal> [speed] [conf]
流程: 停旧检测/视频 → ch0 播视频(倍速) → 启动检测(视角1模型) → 重置插件计数
      → 轮询到视频播完 → 打印最终总产量。
对齐基准 (detect9(1).py demo): normal=38, small=2。
"""
import sys, time, requests

API = "http://127.0.0.1:8001/api/v1"
SW = f"{API}/plugins/sensor-clean/swab"
BASE = "/home/qianqian/文档/xwechat_files/wxid_9j6tgdyqgpon22_030a/msg/file/2026-07/sensor清洁"
MODEL = f"{BASE}/视角1.pt"
VIDEOS = {
    "normal": (f"{BASE}/视角1-正常.mp4", 202.6),
    "small": (f"{BASE}/视角1-正常(小幅度移动不计数).mp4", 128.3),
}

which = sys.argv[1] if len(sys.argv) > 1 else "small"
speed = float(sys.argv[2]) if len(sys.argv) > 2 else 0.6
conf = float(sys.argv[3]) if len(sys.argv) > 3 else 0.25
video, length = VIDEOS[which]

requests.post(f"{API}/source/detection/stop?channel=0", timeout=15)
requests.post(f"{API}/source/video/stop?channel=0", timeout=15)
time.sleep(2)

r = requests.post(f"{API}/source/video/start?channel=0",
                  json={"file_path": video, "speed": speed}, timeout=15)
print("video/start:", r.status_code, r.text[:100])
r = requests.post(f"{API}/source/detection/start?channel=0",
                  json={"conf": conf, "iou": 0.45, "model_path": MODEL}, timeout=120)
print("detection/start:", r.status_code, r.text[:120])
requests.post(f"{SW}/reset-counts", timeout=10)

dur = length / speed + 180
print(f"video={which} speed={speed} conf={conf} 上限 {dur:.0f}s, 播完即止 ...", flush=True)
t0 = time.time()
last = -1
while time.time() - t0 < dur:
    time.sleep(10)
    st = requests.get(f"{SW}/state", timeout=10).json()
    if st["total_products"] != last:
        last = st["total_products"]
        print(f"  t+{time.time()-t0:.0f}s 总产量={last}", flush=True)
    src = requests.get(f"{API}/source/status?channel=0", timeout=10).json()
    if not src.get("is_running"):
        print(f"  t+{time.time()-t0:.0f}s 视频播放结束", flush=True)
        break

st = requests.get(f"{SW}/state", timeout=10).json()
print(f"\n===== {which} speed={speed}: 最终总产量={st['total_products']} "
      f"NG={st.get('ng_count')} =====")
requests.post(f"{API}/source/detection/stop?channel=0", timeout=15)
requests.post(f"{API}/source/video/stop?channel=0", timeout=15)
