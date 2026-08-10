"""探路跑: 真模型+真视频过一遍 SY9 包装项目, 摸清步骤检出时序与逐盘记账.

不扫工单 (协调器无单可记, 零干扰), 只看检测层:
  - 各步骤计数/出现时刻 (贴标/封箱/放工单/放托盘)
  - 容器逐盘记账 (盘进箱时刻与滑块数) → 推出每箱滑块数, 供正式 UAT 配工单量
  - fps / 托盘检出健康度

跑法: python tests/uat/uat_sy9_video_explore.py --base http://127.0.0.1:8005
"""
import argparse
import json
import time
from pathlib import Path

import requests

VIDEO = str(Path(__file__).parent / "assets" / "sy9_20260805_two_boxes.mkv")
MODEL = str(Path(__file__).parent / "assets" / "sy9_packing_best.pt")
CH = 0
PROJECT_ID = 41
DUR = 155


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8005")
    ap.add_argument("--speed", type=float, default=1.0)
    args = ap.parse_args()
    api = args.base

    def call(method, path, **kw):
        r = getattr(requests, method)(f"{api}{path}", timeout=20, **kw)
        r.raise_for_status()
        return r.json()

    def results():
        try:
            r = requests.get(
                f"{api}/api/v1/source/detection/results?channel={CH}", timeout=10)
            return r.json() if r.status_code == 200 else {}
        except Exception:
            return {}

    # 清场
    for ep in ("/api/v1/source/detection/stop", "/api/v1/source/video/stop",
               "/api/v1/test/synthetic/stop"):
        try:
            requests.post(f"{api}{ep}?channel={CH}", timeout=15)
        except Exception:
            pass
    time.sleep(1.5)

    # 推项目 41 (上银SY) 给运行时 (内存副本, 不动库)
    p = call("get", f"/api/v1/projects/{PROJECT_ID}")
    call("post", f"/api/v1/source/detection/set-project?channel={CH}", json={
        "project_id": p["id"], "name": p["name"], "task_type": p["task_type"],
        "logic_mode": p["logic_mode"],
        "steps_config": p.get("steps_config") or [],
        "pipeline_config": p.get("pipeline_config") or {},
        "events_config": p.get("events_config") or [],
        "counters_config": p.get("counters_config") or [],
        "data_config": p.get("data_config") or {},
    })
    print(f"项目: {p['name']} (id={p['id']})")

    call("post", f"/api/v1/source/video/start?channel={CH}",
         json={"file_path": VIDEO, "speed": args.speed})
    call("post", f"/api/v1/source/detection/start?channel={CH}",
         json={"session_name": "sy9_explore", "models": [
             {"name": "main", "model_path": MODEL, "conf": 0.25,
              "iou": 0.45, "priority": 100}]})
    print(f"视频+检测已启动 speed={args.speed}")

    t0 = time.time()
    last_counts = {}
    last_done = 0
    last_steps = []
    fps_samples = []
    deadline = t0 + DUR / args.speed + 30
    while time.time() < deadline:
        st = results()
        el = time.time() - t0
        if (st.get("fps") or 0) > 0:
            fps_samples.append(float(st["fps"]))
        counts = st.get("step_counts") or {}
        for k, v in counts.items():
            if v != last_counts.get(k):
                print(f"t+{el:6.1f}s 步骤计数 {k}: {last_counts.get(k, 0)} -> {v}")
        last_counts = dict(counts)
        cyc = st.get("current_cycle_steps") or []
        if cyc != last_steps:
            print(f"t+{el:6.1f}s 周期步骤: {cyc}")
            last_steps = list(cyc)
        cont = (st.get("custom_mix_state") or {}).get("container") or {}
        done = cont.get("done_detail") or []
        if len(done) != last_done:
            if len(done) > last_done:
                for t in done[last_done:]:
                    print(f"t+{el:6.1f}s 盘进箱: {json.dumps(t, ensure_ascii=False)}")
            else:
                print(f"t+{el:6.1f}s 箱账清零 (周期结算翻页)")
            last_done = len(done)
        time.sleep(1.0)

    st = results()
    print("\n== 收尾 ==")
    print("counters:", json.dumps(st.get("counters") or {}, ensure_ascii=False))
    print("step_counts:", json.dumps(st.get("step_counts") or {}, ensure_ascii=False))
    if fps_samples:
        print(f"fps: avg={sum(fps_samples)/len(fps_samples):.1f} "
              f"min={min(fps_samples):.1f}")
    requests.post(f"{api}/api/v1/source/detection/stop?channel={CH}", timeout=15)
    requests.post(f"{api}/api/v1/source/video/stop?channel={CH}", timeout=15)


if __name__ == "__main__":
    main()
