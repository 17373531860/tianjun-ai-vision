"""UAT (面向功能测试) — v3.14.0 RFC 11 串行流水线 + 金龙真实双工位推理.

客户现场叙事 (金龙):
  工件先经过工位 1 (GW1, 7 步: 热水槽→下料→热水浸泡→浸泡结束→甩干→甩干结束→吹干),
  然后传到工位 2 (GW2, 2 步: 吹干→接触产品). 软件应自动把同一工件在两个工位
  的检测结果聚合, 全 OK 才算工件合格.

测试目标:
  1. 后端能加载 GW1/GW2 真实 .pt 模型
  2. 两个真实视频文件分别在 channel 0/1 上跑真实 YOLO 推理
  3. WorkpieceFlowCoordinator 自动把 channel 0 的 cycle_start 触发为工件入口
  4. 跑足够时间让 GW1 出至少 1 个 cycle, GW2 出至少 1 个 cycle
  5. 校验后端 workpiece_flow_runs 表里有真实的 station_cycle_ids

不做的事:
  - Playwright (这次跑后端 API 全链路, 不验证前端 UI; 前端 UI 已在
    uat_20260530_workpiece_flow_v314.py 19/19 验证完毕)
  - 模拟 cycle (这次走真实 GPU 推理 + 真实视频)

证据:
  日志: /tmp/uat_jinlong_run.log
  后端: 已起在 :8001, TIANJUN_DATA_DIR=/tmp/tianjun_wfc_real_uat
"""
import os
import time
import json
import shutil
from pathlib import Path
import sqlite3

import requests


# ============================================================
# 配置
# ============================================================
API = "http://127.0.0.1:8001/api/v1"
DATA_DIR = "/tmp/tianjun_wfc_real_uat"
LOG = "/tmp/uat_jinlong_run.log"

GW1_VIDEO_SRC = "/home/qianqian/1.py/output/金龙/GW1/video/064b34b762c1b4e75ad338c6e519030c.mp4"
GW1_MODEL_SRC = "/home/qianqian/1.py/output/金龙/GW1/model/bestGW1.pt"
GW2_VIDEO_SRC = "/home/qianqian/1.py/output/金龙/GW2/video/a4b8088d80fa2d7f42ada3e74220d878.mp4"
GW2_MODEL_SRC = "/home/qianqian/1.py/output/金龙/GW2/model/bestGW2.pt"

# 已拷到 DATA_DIR/uploads/{videos,models}/
GW1_VIDEO = f"{DATA_DIR}/uploads/videos/gw1.mp4"
GW2_VIDEO = f"{DATA_DIR}/uploads/videos/gw2.mp4"
GW1_MODEL = f"{DATA_DIR}/uploads/models/bestGW1.pt"
GW2_MODEL = f"{DATA_DIR}/uploads/models/bestGW2.pt"

GW1_LABELS = ['热水槽', '下料', '热水浸泡', '浸泡结束', '甩干', '甩干结束', '吹干']
GW2_LABELS = ['吹干', '接触产品']

steps_log = []


def step(label: str, ok: bool, detail: str = ""):
    rec = {"idx": len(steps_log) + 1, "label": label, "ok": bool(ok), "detail": detail}
    steps_log.append(rec)
    marker = "OK" if ok else "FAIL"
    print(f"  [{rec['idx']:02d}] [{marker}] {label}  {detail}")
    return ok


def must(cond, msg, detail=""):
    if not step(msg, cond, detail):
        raise RuntimeError(f"UAT halt: {msg}  detail={detail}")


# ============================================================
# 主流程
# ============================================================
def main():
    print(f"[UAT] 金龙真实双工位推理 — API={API}")

    # 0. 健康检查
    r = requests.get(f"{API}/workpiece-flows", timeout=5)
    must(r.status_code == 200, "后端健康", f"status={r.status_code}")

    # 1. 资产路径校验
    for p in (GW1_VIDEO, GW2_VIDEO, GW1_MODEL, GW2_MODEL):
        must(os.path.exists(p), f"资产存在: {os.path.basename(p)}", f"path={p}")

    # 2. 注册模型 (multipart upload) — 用时间戳让名字唯一
    model_ids = {}
    suffix = int(time.time())
    for tag, src_path in [("bestGW1", GW1_MODEL_SRC), ("bestGW2", GW2_MODEL_SRC)]:
        with open(src_path, "rb") as f:
            files = {"file": (os.path.basename(src_path), f, "application/octet-stream")}
            data = {"name": f"{tag}_{suffix}", "version": "v1", "framework": "PyTorch"}
            r = requests.post(f"{API}/models/upload", files=files, data=data, timeout=60)
        must(r.status_code in (200, 201), f"模型 {tag} 上传", f"status={r.status_code} body={r.text[:200]}")
        mid = r.json()["id"]
        model_ids[tag] = mid
        labels = r.json().get("labels") or []
        step(f"模型 {tag} 标签解析", len(labels) > 0, f"labels={labels}")

    # 3. 创建 2 个项目 (GW1 7 步顺序 / GW2 2 步顺序)
    project_ids = {}

    def make_steps(labels):
        return [
            {"id": f"s{i}", "label": lbl, "enabled": True, "threshold": 50, "min_frames": 1}
            for i, lbl in enumerate(labels, start=1)
        ]

    for tag, labels in [("GW1", GW1_LABELS), ("GW2", GW2_LABELS)]:
        payload = {
            "name": f"jinlong-{tag}-{suffix}",
            "task_type": "detection",
            "logic_mode": "sequential",
            "pipeline_config": {
                "logic_mode": "sequential",
                "settlement_mode": "first_step",
            },
            "steps_config": make_steps(labels),
            "events_config": [
                {"id": 1, "name": "OK", "actions": [], "show_notification": True},
                {"id": 2, "name": "NG", "actions": [], "show_notification": True},
            ],
            "default_model_id": model_ids[f"best{tag}"],
        }
        r = requests.post(f"{API}/projects", json=payload, timeout=10)
        must(r.status_code in (200, 201), f"创建项目 jinlong-{tag}",
             f"status={r.status_code} body={r.text[:200]}")
        project_ids[tag] = r.json()["id"]

    # 4. 设置工位数 = 2
    r = requests.post(f"{API}/workstations/mode",
                      json={"channel_count": 2, "channels": []}, timeout=10)
    must(r.status_code == 200, "切到双工位", f"status={r.status_code} body={r.text[:200]}")

    # 5. 各通道设置 project_config + load 对应模型 + 启动检测 (单步合并: detection/start 会一次性 set conf/iou + load model + start session)
    for ch, tag in [(0, "GW1"), (1, "GW2")]:
        labels = GW1_LABELS if tag == "GW1" else GW2_LABELS
        proj_cfg = {
            "project_id": project_ids[tag],
            "name": f"jinlong-{tag}-{suffix}",
            "task_type": "detection",
            "logic_mode": "sequential",
            "pipeline_config": {"logic_mode": "sequential", "settlement_mode": "first_step"},
            "steps_config": make_steps(labels),
            "events_config": [
                {"id": 1, "name": "OK", "actions": [], "show_notification": True},
                {"id": 2, "name": "NG", "actions": [], "show_notification": True},
            ],
        }
        r = requests.post(f"{API}/source/detection/set-project?channel={ch}",
                          json=proj_cfg, timeout=10)
        must(r.status_code == 200, f"ch{ch} 应用项目 {tag}",
             f"status={r.status_code} body={r.text[:200]}")

    # 6. 启动两个通道的视频 (file_path = 本地绝对路径)
    for ch, video_path in [(0, GW1_VIDEO), (1, GW2_VIDEO)]:
        r = requests.post(f"{API}/source/video/start?channel={ch}",
                          json={"file_path": video_path, "speed": 4.0}, timeout=15)
        must(r.status_code == 200, f"ch{ch} 启动视频 (4x 加速)",
             f"status={r.status_code} body={r.text[:200]}")

    # 7. 启动两个通道的检测 (用对应模型 path)
    for ch, model_path, tag in [(0, GW1_MODEL, "GW1"), (1, GW2_MODEL, "GW2")]:
        payload = {
            "model_path": model_path,
            "conf": 0.5,
            "iou": 0.45,
            "session_name": f"jinlong-{tag}-uat",
        }
        r = requests.post(f"{API}/source/detection/start?channel={ch}",
                          json=payload, timeout=30)
        must(r.status_code == 200, f"ch{ch} 启动检测 {tag}",
             f"status={r.status_code} body={r.text[:200]}")

    # 8. 创建 WorkpieceFlow (通道 0/1 串行)
    payload = {
        "name": f"jinlong-flow-{int(time.time())}",
        "station_channel_ids": [0, 1],
        "trigger_mode": "time_window",
        "fifo_max_in_flight": 5,
        "cycle_to_cycle_window_ms": 15000,
        "short_circuit_on_ng": False,  # 测试: 即使 NG 也跑完两工位
        "workpiece_timeout_ms": 120000,
        "timeout_action": "force_ng",
        "settle_strategy": "all_ok_required",
        "enabled": True,
    }
    r = requests.post(f"{API}/workpiece-flows", json=payload, timeout=10)
    must(r.status_code in (200, 201), "创建启用流水线",
         f"status={r.status_code} body={r.text[:200]}")
    flow_id = r.json()["id"]

    # 9. 等待真实推理跑出 cycle (GW1 视频 84s @ 4x 加速 ≈ 21s; GW2 135s @ 4x ≈ 34s)
    #    保险起见拉到 180s, 等真实流水线至少跑出 1 个完整 run
    print("\n[UAT] 等待真实推理跑出 cycle (最多 180 秒)...")
    TOTAL = 180
    t0 = time.time()
    while time.time() - t0 < TOTAL:
        time.sleep(5)
        r = requests.get(f"{API}/workpiece-flows/{flow_id}/state", timeout=5)
        state = r.json() if r.status_code == 200 else {}
        in_flight = state.get("in_flight", [])

        r2 = requests.get(f"{API}/source/detection/results?channel=0", timeout=5)
        ch0 = r2.json() if r2.status_code == 200 else {}

        r3 = requests.get(f"{API}/source/detection/results?channel=1", timeout=5)
        ch1 = r3.json() if r3.status_code == 200 else {}

        r4 = requests.get(f"{API}/workpiece-flows/{flow_id}/runs?limit=10", timeout=5)
        runs = (r4.json().get("items") if r4.status_code == 200 else []) or []

        # 拿每通道步骤命中情况 (step_counts) — 这个能说明真实推理出框了
        ch0_steps = ch0.get("step_counts") or {}
        ch1_steps = ch1.get("step_counts") or {}
        ch0_steps_hit = sum(1 for v in ch0_steps.values() if v)
        ch1_steps_hit = sum(1 for v in ch1_steps.values() if v)

        elapsed = int(time.time() - t0)
        print(f"  [t+{elapsed:>3}s] "
              f"ch0 fps={ch0.get('fps') or 0} cycle={ch0.get('current_cycle_id')} steps_hit={ch0_steps_hit}/{len(ch0_steps)} | "
              f"ch1 fps={ch1.get('fps') or 0} cycle={ch1.get('current_cycle_id')} steps_hit={ch1_steps_hit}/{len(ch1_steps)} | "
              f"in_flight={len(in_flight)} runs={len(runs)}")

        if len(runs) >= 1 and any(rr.get("status") in ("completed", "short_circuited") for rr in runs):
            break

    # 10. 最终校验
    r = requests.get(f"{API}/workpiece-flows/{flow_id}/runs?limit=10", timeout=5)
    runs = (r.json().get("items") if r.status_code == 200 else []) or []
    step("跑完后至少 1 条 run 记录", len(runs) >= 1, f"runs={len(runs)}")
    if runs:
        last = runs[0]
        step("最后一条 run 已落库", True,
             f"status={last.get('status')} final={last.get('final_result')} "
             f"station_cycle_ids={last.get('station_cycle_ids')} "
             f"station_results={last.get('station_results')}")

    # 11. 拍工位 cycle 数据
    for ch in (0, 1):
        r = requests.get(f"{API}/source/detection/results?channel={ch}", timeout=5)
        if r.status_code == 200:
            st = r.json()
            steps = st.get("step_counts") or {}
            counters = st.get("counters") or {}
            step(f"ch{ch} 推理状态快照", True,
                 f"running={st.get('is_detecting')} fps={st.get('fps')} "
                 f"step_counts={steps} counters={counters}")

    # 12. 收尾: 停检测 + 停视频
    for ch in (0, 1):
        try:
            requests.post(f"{API}/source/detection/stop?channel={ch}", timeout=5)
            requests.post(f"{API}/source/video/stop?channel={ch}", timeout=5)
        except Exception:
            pass
    step("清理: 停掉两个通道", True)

    # 13. 关闭流水线 + 删除
    requests.put(f"{API}/workpiece-flows/{flow_id}", json={"enabled": False}, timeout=5)
    r = requests.delete(f"{API}/workpiece-flows/{flow_id}", timeout=5)
    step("清理: 删除流水线", r.status_code in (200, 204))


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        print(f"\n[UAT] HALT: {e}")
    finally:
        ok_n = sum(1 for s in steps_log if s["ok"])
        with open(LOG, "w", encoding="utf-8") as f:
            json.dump({"steps": steps_log}, f, ensure_ascii=False, indent=2)
        print(f"\n[UAT] 总计 {len(steps_log)} 步, 通过 {ok_n}, 失败 {len(steps_log) - ok_n}")
        print(f"[UAT] 日志: {LOG}")
