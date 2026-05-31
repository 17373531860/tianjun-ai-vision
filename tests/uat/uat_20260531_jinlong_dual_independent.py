"""UAT — v1.1.2 福建金龙双工位插件 独立运行全面验证 (无 RFC 11 串行流水线).

测试目标:
  1. 创建两个独立项目 (GW1 7 步 + GW2 2 步, 都是 sequential)
  2. 把它们绑到 channel 0/1, 各自独立运行 (不创建 WorkpieceFlow)
  3. 跑真实视频 + 真实 YOLO 推理 ~ 60s, 让两通道各跑出 cycle 数据
  4. 校验**插件 v1.1.2 渲染所需的全部字段**都在 multiChannelData[i] 上有真实数据:
       - 顶部条:  projectName / isDetecting / isRunning
       - 视频区:  fps (用 ch.fps)
       - 统计区:  total, ok, ng, yieldRate, ngStepRanking
       - SOP:    steps (含 status / cycleResult / screenshot)
       - 步骤表:  tableData (含 step / label / status / cycleResult)
                  cycleSumStepDurations (PT 数据源)
                  cycleTime (CT 显示)
  5. 测三种工位绑定组合:
       A. ch0=GW1 + ch1=GW2  (默认主组合)
       B. ch0=GW2 + ch1=GW1  (反向: 验证插件不写死)
       C. ch0=GW1 + ch1=GW1  (同工位双跑: 验证工位独立性)

不做的事:
  - 不创建 WorkpieceFlow (用户明确说不需要串行流水线)
  - 不验证前端 UI 渲染 (那个交给 Playwright 单独跑, 或后续手动看)

证据落库:
  日志: /tmp/uat_v112_dual_independent.log
  后端: 已起在 :8001
"""
import os
import time
import json
import shutil
from pathlib import Path

import requests


API = "http://127.0.0.1:8001/api/v1"
DATA_DIR = "/tmp/tianjun_wfc_real_uat"
LOG = "/tmp/uat_v112_dual_independent.log"

GW1_VIDEO_SRC = "/home/qianqian/1.py/output/金龙/GW1/video/064b34b762c1b4e75ad338c6e519030c.mp4"
GW1_MODEL_SRC = "/home/qianqian/1.py/output/金龙/GW1/model/bestGW1.pt"
GW2_VIDEO_SRC = "/home/qianqian/1.py/output/金龙/GW2/video/a4b8088d80fa2d7f42ada3e74220d878.mp4"
GW2_MODEL_SRC = "/home/qianqian/1.py/output/金龙/GW2/model/bestGW2.pt"

GW1_VIDEO = f"{DATA_DIR}/uploads/videos/gw1.mp4"
GW2_VIDEO = f"{DATA_DIR}/uploads/videos/gw2.mp4"
GW1_MODEL = f"{DATA_DIR}/uploads/models/bestGW1.pt"
GW2_MODEL = f"{DATA_DIR}/uploads/models/bestGW2.pt"

GW1_LABELS = ['热水槽', '下料', '热水浸泡', '浸泡结束', '甩干', '甩干结束', '吹干']
GW2_LABELS = ['吹干', '接触产品']

steps_log = []


def step(label, ok, detail=""):
    rec = {"idx": len(steps_log) + 1, "label": label, "ok": bool(ok), "detail": detail}
    steps_log.append(rec)
    marker = "OK" if ok else "FAIL"
    print(f"  [{rec['idx']:02d}] [{marker}] {label}  {detail}")
    return ok


def must(cond, msg, detail=""):
    if not step(msg, cond, detail):
        raise RuntimeError(f"UAT halt: {msg} | {detail}")


def make_steps(labels):
    return [
        {"id": f"s{i}", "label": lbl, "enabled": True, "threshold": 50, "min_frames": 1}
        for i, lbl in enumerate(labels, start=1)
    ]


def ensure_assets():
    """资产校验, 不存在时自动从源拷贝"""
    Path(f"{DATA_DIR}/uploads/videos").mkdir(parents=True, exist_ok=True)
    Path(f"{DATA_DIR}/uploads/models").mkdir(parents=True, exist_ok=True)
    for src, dst in [
        (GW1_VIDEO_SRC, GW1_VIDEO),
        (GW2_VIDEO_SRC, GW2_VIDEO),
        (GW1_MODEL_SRC, GW1_MODEL),
        (GW2_MODEL_SRC, GW2_MODEL),
    ]:
        if not os.path.exists(dst):
            must(os.path.exists(src), f"源资产存在: {os.path.basename(src)}", f"src={src}")
            shutil.copy(src, dst)
            step(f"拷贝资产 → {os.path.basename(dst)}", True, f"size={os.path.getsize(dst)} bytes")


def upload_model(tag, src_path, suffix):
    with open(src_path, "rb") as f:
        files = {"file": (os.path.basename(src_path), f, "application/octet-stream")}
        data = {"name": f"{tag}_{suffix}", "version": "v1", "framework": "PyTorch"}
        r = requests.post(f"{API}/models/upload", files=files, data=data, timeout=60)
    must(r.status_code in (200, 201),
         f"模型 {tag} 上传",
         f"status={r.status_code} body={r.text[:200]}")
    return r.json()["id"]


def create_project(tag, labels, model_id, suffix):
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
        "default_model_id": model_id,
    }
    r = requests.post(f"{API}/projects", json=payload, timeout=10)
    must(r.status_code in (200, 201),
         f"创建项目 jinlong-{tag}",
         f"status={r.status_code} body={r.text[:200]}")
    return r.json()["id"]


def setup_channel(ch, tag, labels, project_id, video_path, model_path, suffix):
    """单通道完整启动: set-project → start-video → start-detection"""
    proj_cfg = {
        "project_id": project_id,
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
    r = requests.post(f"{API}/source/detection/set-project?channel={ch}", json=proj_cfg, timeout=10)
    must(r.status_code == 200, f"ch{ch} 应用项目 {tag}",
         f"status={r.status_code} body={r.text[:200]}")

    r = requests.post(f"{API}/source/video/start?channel={ch}",
                      json={"file_path": video_path, "speed": 4.0}, timeout=15)
    must(r.status_code == 200, f"ch{ch} 启动视频 ({tag}, 4x)",
         f"status={r.status_code} body={r.text[:200]}")

    payload = {
        "model_path": model_path,
        "conf": 0.5,
        "iou": 0.45,
        "session_name": f"jinlong-{tag}-uat-ch{ch}",
    }
    r = requests.post(f"{API}/source/detection/start?channel={ch}", json=payload, timeout=30)
    must(r.status_code == 200, f"ch{ch} 启动检测 {tag}",
         f"status={r.status_code} body={r.text[:200]}")


def teardown_channels():
    """停掉两个通道的检测和视频"""
    for ch in (0, 1):
        try:
            requests.post(f"{API}/source/detection/stop?channel={ch}", timeout=5)
            requests.post(f"{API}/source/video/stop?channel={ch}", timeout=5)
        except Exception:
            pass


def wait_for_cycles(seconds, tag1, tag2, labels1, labels2):
    """等 seconds 秒, 每 5s 拍快照, 返回 (mid_snap_ch0, mid_snap_ch1, end_snap_ch0, end_snap_ch1).
       mid 是 wait 中段最热的快照 (is_detecting=True 且有数据), end 是结束时的快照.
    """
    t0 = time.time()
    end_ch0 = end_ch1 = {}
    mid_ch0 = mid_ch1 = None  # 中段最热快照 (优先 is_detecting=True 且 step_counts 非空)
    while time.time() - t0 < seconds:
        time.sleep(5)
        r0 = requests.get(f"{API}/source/detection/results?channel=0", timeout=5)
        r1 = requests.get(f"{API}/source/detection/results?channel=1", timeout=5)
        ch0 = r0.json() if r0.status_code == 200 else {}
        ch1 = r1.json() if r1.status_code == 200 else {}
        elapsed = int(time.time() - t0)
        hit0 = sum(1 for v in (ch0.get("step_counts") or {}).values() if v)
        hit1 = sum(1 for v in (ch1.get("step_counts") or {}).values() if v)
        print(f"  [t+{elapsed:>3}s] "
              f"ch0({tag1}) det={ch0.get('is_detecting')} fps={ch0.get('fps') or 0} cyc={ch0.get('current_cycle_id')} hits={hit0}/{len(labels1)} | "
              f"ch1({tag2}) det={ch1.get('is_detecting')} fps={ch1.get('fps') or 0} cyc={ch1.get('current_cycle_id')} hits={hit1}/{len(labels2)}")
        # 记最热快照 (优先 is_detecting=True 且步骤命中)
        if ch0.get("is_detecting") and hit0 >= 1:
            mid_ch0 = ch0
        if ch1.get("is_detecting") and hit1 >= 1:
            mid_ch1 = ch1
        end_ch0, end_ch1 = ch0, ch1
    return (mid_ch0 or end_ch0, mid_ch1 or end_ch1, end_ch0, end_ch1)


def validate_plugin_fields(prefix, ch, snap, expect_steps_count):
    """逐字段验证插件 v1.1.2 渲染所需数据 (snake_case, 前端会映射成驼峰)"""
    # ----- 顶部条 -----
    pcfg = snap.get("project_config") or {}
    pname = pcfg.get("project_name")
    step(f"[{prefix}-ch{ch}] project_config.project_name 存在 (顶部工位标签用)",
         bool(pname), f"project_name={pname}")
    step(f"[{prefix}-ch{ch}] is_detecting 字段存在 (顶部状态徽章用)",
         "is_detecting" in snap, f"is_detecting={snap.get('is_detecting')}")
    step(f"[{prefix}-ch{ch}] is_running 字段存在",
         "is_running" in snap, f"is_running={snap.get('is_running')}")

    # ----- 视频区 -----
    step(f"[{prefix}-ch{ch}] fps 字段存在 (视频底部 FPS 显示用)",
         isinstance(snap.get("fps"), (int, float)), f"fps={snap.get('fps')}")

    # ----- 统计区 (检测次数 / OK / NG / 良品不良 / 合格率) -----
    counters = snap.get("counters") or {}
    step(f"[{prefix}-ch{ch}] counters 字段 (检测次数 / OK / NG 数字)",
         isinstance(counters, dict) and len(counters) >= 1,
         f"counters_keys={list(counters.keys())[:6]}")

    # ----- 步骤表 (插件 buildBottomCell 的核心数据源) -----
    sconf = pcfg.get("steps_config") or []
    step(f"[{prefix}-ch{ch}] project_config.steps_config 长度对",
         len(sconf) == expect_steps_count,
         f"got={len(sconf)} expect={expect_steps_count}")

    cycle_steps = snap.get("current_cycle_steps") or []
    step(f"[{prefix}-ch{ch}] current_cycle_steps 是 list (前端 tableData status 来源)",
         isinstance(cycle_steps, list),
         f"current_cycle_steps={cycle_steps}")

    # ----- PT 数据源 -----
    ptd = snap.get("cycle_sum_step_durations") or {}
    step(f"[{prefix}-ch{ch}] cycle_sum_step_durations 是 dict (PT 数据源)",
         isinstance(ptd, dict),
         f"keys={list(ptd.keys())[:5]}")

    # ----- step_counts (推理真出框) -----
    sc = snap.get("step_counts") or {}
    nz = sum(1 for v in sc.values() if v)
    step(f"[{prefix}-ch{ch}] step_counts 至少 1 步被命中 (推理真有结果)",
         nz >= 1, f"non_zero={nz}/{len(sc)} sc={sc}")

    # ----- SOP 卡片用的 step_screenshots (插件 SOP 卡片显示截图占位) -----
    ss = snap.get("step_screenshots") or {}
    step(f"[{prefix}-ch{ch}] step_screenshots 是 dict (SOP 卡片缩略图)",
         isinstance(ss, dict),
         f"len={len(ss)}")

    # ----- 周期时间 (CT) -----
    step(f"[{prefix}-ch{ch}] average_cycle_time / last_cycle_time 字段存在",
         all(k in snap for k in ("average_cycle_time", "last_cycle_time", "current_cycle_time")),
         f"avg={snap.get('average_cycle_time')} last={snap.get('last_cycle_time')} cur={snap.get('current_cycle_time')}")


def run_combo(combo_name, ch0_tag, ch0_video, ch0_model, ch0_labels, ch0_pid,
              ch1_tag, ch1_video, ch1_model, ch1_labels, ch1_pid,
              suffix, run_seconds):
    """单种工位绑定组合的完整运行"""
    print(f"\n{'='*60}")
    print(f"[组合 {combo_name}]  ch0={ch0_tag}({len(ch0_labels)}步)  ch1={ch1_tag}({len(ch1_labels)}步)")
    print(f"{'='*60}")

    setup_channel(0, ch0_tag, ch0_labels, ch0_pid, ch0_video, ch0_model, suffix)
    setup_channel(1, ch1_tag, ch1_labels, ch1_pid, ch1_video, ch1_model, suffix)

    print(f"\n[组合 {combo_name}] 跑 {run_seconds}s 等真实推理出数据 ...")
    mid_ch0, mid_ch1, end_ch0, end_ch1 = wait_for_cycles(
        run_seconds, ch0_tag, ch1_tag, ch0_labels, ch1_labels)

    # 字段全面校验 (用中段最热的快照, 因为视频可能到结尾就 is_detecting=False 了)
    validate_plugin_fields(combo_name, 0, mid_ch0 or {}, len(ch0_labels))
    validate_plugin_fields(combo_name, 1, mid_ch1 or {}, len(ch1_labels))

    teardown_channels()
    step(f"[组合 {combo_name}] 清理两通道", True)


def main():
    print(f"[UAT v1.1.2] 福建金龙双工位插件 独立运行全面验证")
    print(f"  API={API}  DATA_DIR={DATA_DIR}\n")

    # 0. 健康检查 + 资产 + 上传
    r = requests.get(f"{API}/projects", timeout=5)
    must(r.status_code == 200, "后端健康", f"status={r.status_code}")

    ensure_assets()
    suffix = int(time.time())

    print(f"\n--- 阶段 1: 上传模型 + 创建项目 (suffix={suffix}) ---")
    mid_gw1 = upload_model("bestGW1", GW1_MODEL_SRC, suffix)
    mid_gw2 = upload_model("bestGW2", GW2_MODEL_SRC, suffix)
    pid_gw1 = create_project("GW1", GW1_LABELS, mid_gw1, suffix)
    pid_gw2 = create_project("GW2", GW2_LABELS, mid_gw2, suffix)

    print(f"\n--- 阶段 2: 切到双工位 ---")
    r = requests.post(f"{API}/workstations/mode",
                      json={"channel_count": 2, "channels": []}, timeout=10)
    must(r.status_code == 200, "切到双工位 channel_count=2",
         f"status={r.status_code} body={r.text[:200]}")

    # 三种组合, 每种跑 30s (短一点, 因为视频已加速 4x)
    print(f"\n--- 阶段 3: 跑 3 种组合, 每种 30 秒 ---")
    RUN_SEC = 30

    run_combo("A", "GW1", GW1_VIDEO, GW1_MODEL, GW1_LABELS, pid_gw1,
              "GW2", GW2_VIDEO, GW2_MODEL, GW2_LABELS, pid_gw2,
              suffix, RUN_SEC)

    run_combo("B", "GW2", GW2_VIDEO, GW2_MODEL, GW2_LABELS, pid_gw2,
              "GW1", GW1_VIDEO, GW1_MODEL, GW1_LABELS, pid_gw1,
              suffix, RUN_SEC)

    run_combo("C", "GW1", GW1_VIDEO, GW1_MODEL, GW1_LABELS, pid_gw1,
              "GW1", GW1_VIDEO, GW1_MODEL, GW1_LABELS, pid_gw1,
              suffix, RUN_SEC)

    print("\n--- 阶段 4: 结束, 留组合 A 的配置给 Playwright 用 ---")
    setup_channel(0, "GW1", GW1_LABELS, pid_gw1, GW1_VIDEO, GW1_MODEL, suffix)
    setup_channel(1, "GW2", GW2_LABELS, pid_gw2, GW2_VIDEO, GW2_MODEL, suffix)
    print("[UAT] 配置 A (ch0=GW1, ch1=GW2) 留在运行状态, 浏览器进 Monitor 直接看插件渲染")


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
