"""稳定性长跑 (Apple 芯片 MPS 满帧口径):

Phase A 双工位: 两个不同视频 + 同一模型 (2K17421, 项目3), 各 1x
Phase B 三工位: ch0/ch1 同一视频 + 同一模型 (2K17421), ch2 不同视频不同模型 (K10916, 项目7)

监控项 (每 5s 采样):
  - 每通道 fps / fps_inference / 视频位置推进
  - 后端 RSS 内存漂移
  - 结束时 ΔOK/ΔNG 合格率 vs 调参预期 (宽松阈值: 中途重启/共享 MPS 降速属正常)
  - 后端日志 Traceback / Metal 断言扫描
  - 插件 runtime health

headless 无 UI, 纯 API 面。用法: conda tianjun env 下
  python tests/uat/uat_lg_worktime_stability.py [phaseA秒 phaseB秒]
"""
import subprocess
import sys
import time
from pathlib import Path

import psutil
import requests

BACKEND = "http://localhost:8004/api/v1"
V_2K = sorted(Path("/Users/tianjun/Public/测试使用/2K17421(1号工位）视频").glob("*.avi"))
V_K1 = sorted(Path("/Users/tianjun/Public/测试使用/k10916").glob("*.avi"))
PID_2K, PID_K1 = 3, 7

PHASE_A_SEC = int(sys.argv[1]) if len(sys.argv) > 1 else 240
PHASE_B_SEC = int(sys.argv[2]) if len(sys.argv) > 2 else 480

fails = []


def ok(cond, msg):
    print(("  ✓ " if cond else "  ✗ ") + msg)
    if not cond:
        fails.append(msg)


def backend_pid():
    out = subprocess.run(["pgrep", "-f", "uvicorn.*8004"], capture_output=True, text=True)
    pids = [int(x) for x in out.stdout.split()]
    return pids[0] if pids else None


def rss_mb():
    pid = backend_pid()
    return round(psutil.Process(pid).memory_info().rss / 1e6, 1) if pid else None


def stop_all():
    for ch in range(3):
        try:
            requests.post(f"{BACKEND}/source/detection/stop", params={"channel": ch}, timeout=30)
        except Exception:
            pass
    time.sleep(1)


def set_mode(n):
    stop_all()
    requests.post(f"{BACKEND}/workstations/mode",
                  json={"channel_count": n, "channels": []}, timeout=30).raise_for_status()
    time.sleep(1)


def set_project(ch, pid):
    proj = requests.get(f"{BACKEND}/projects/{pid}", timeout=10).json()
    requests.post(f"{BACKEND}/source/detection/set-project", params={"channel": ch}, json={
        "project_id": proj["id"], "name": proj["name"],
        "task_type": proj.get("task_type") or "detection",
        "logic_mode": proj.get("logic_mode") or "sequential",
        "steps_config": proj.get("steps_config") or [],
        "pipeline_config": proj.get("pipeline_config") or {},
        "events_config": proj.get("events_config") or [],
        "counters_config": proj.get("counters_config") or [],
        "data_config": proj.get("data_config") or {},
    }, timeout=30).raise_for_status()


def model_path(pid):
    proj = requests.get(f"{BACKEND}/projects/{pid}", timeout=10).json()
    mid = proj.get("default_model_id")
    m = requests.get(f"{BACKEND}/models/{mid}", timeout=10).json()
    return m["file_path"]


def start_channel(ch, video, pid):
    requests.post(f"{BACKEND}/workstations/{ch}/gpu", json={"device": "mps"}, timeout=10).raise_for_status()
    requests.post(f"{BACKEND}/source/video/start", params={"channel": ch},
                  json={"file_path": str(video), "speed": 1.0}, timeout=60).raise_for_status()
    requests.post(f"{BACKEND}/source/video/progress", params={"channel": ch},
                  json={"progress": 0.0}, timeout=10)
    set_project(ch, pid)
    r = requests.post(f"{BACKEND}/source/detection/start", params={"channel": ch},
                      json={"model_path": model_path(pid)}, timeout=180)
    r.raise_for_status()
    print(f"  ch{ch} <- {Path(video).name} @1x mps 项目{pid}")


def counters(ch):
    # 真实字段是 counters (中文键), 不是 cycle_counters (2026-08-07 长跑读错踩坑)
    d = requests.get(f"{BACKEND}/source/detection/results", params={"channel": ch}, timeout=15).json()
    c = d.get("counters") or {}
    return int(c.get("合格总数") or 0), int(c.get("不良总数") or 0)


def run_phase(label, channels, seconds, min_inf_fps):
    print(f"\n[稳定性] === {label} ({seconds}s) ===")
    base = {ch: counters(ch) for ch in channels}
    rss0 = rss_mb()
    stats = {ch: {"inf": [], "pos": []} for ch in channels}
    stuck = {ch: 0 for ch in channels}
    videos = dict(getattr(run_phase, "_videos", {}))
    pids = dict(getattr(run_phase, "_pids", {}))
    t_start = time.time()
    t_end = t_start + seconds
    n = 0
    while time.time() < t_end:
        it0 = time.time()
        for ch in channels:
            try:
                d = requests.get(f"{BACKEND}/source/detection/results",
                                 params={"channel": ch}, timeout=15).json()
                stats[ch]["inf"].append(float(d.get("fps_inference") or 0))
                v = requests.get(f"{BACKEND}/source/video/info",
                                 params={"channel": ch}, timeout=15).json()
                pos = float(v.get("current_time") or 0)
                if stats[ch]["pos"] and abs(pos - stats[ch]["pos"][-1]) < 0.01:
                    stuck[ch] += 1
                else:
                    stuck[ch] = 0
                stats[ch]["pos"].append(pos)
                # 视频放完会自停检测 (playback finished → Detection stopped):
                # 只 seek 不重启检测会空转, 必须整套重拉 (视频+项目+检测)
                if stuck[ch] >= 4:
                    st = requests.get(f"{BACKEND}/source/status",
                                      params={"channel": ch}, timeout=15).json()
                    if not st.get("is_detecting") and ch in videos:
                        print(f"  [{int(time.time() - t_start)}s] ch{ch} 视频放完, 重拉一轮")
                        start_channel(ch, videos[ch], pids[ch])
                    else:
                        requests.post(f"{BACKEND}/source/video/progress",
                                      params={"channel": ch}, json={"progress": 0.0}, timeout=15)
                    stuck[ch] = 0
            except Exception as e:
                fails.append(f"{label} ch{ch} 采样异常: {e}")
        n += 1
        it_cost = time.time() - it0
        if n % 12 == 0 or it_cost > 10:
            print(f"  [{int(time.time() - t_start)}s] rss={rss_mb()}MB 采样耗时={it_cost:.1f}s " +
                  " ".join(f"ch{c}:inf={stats[c]['inf'][-1]:.1f}" for c in channels if stats[c]["inf"]))
        time.sleep(5)

    rss1 = rss_mb()
    print(f"  RSS: {rss0}MB -> {rss1}MB")
    ok(rss1 is not None and rss0 is not None, f"{label} 后端进程存活")
    if rss0 and rss1:
        ok(rss1 < rss0 * 1.6 + 500, f"{label} 内存无失控漂移 ({rss0}->{rss1}MB)")
    for ch in channels:
        inf = [x for x in stats[ch]["inf"] if x > 0]
        avg_inf = sum(inf) / len(inf) if inf else 0
        moved = max(stats[ch]["pos"]) - min(stats[ch]["pos"]) if stats[ch]["pos"] else 0
        o, g = counters(ch)
        d_ok, d_ng = o - base[ch][0], g - base[ch][1]
        total = d_ok + d_ng
        rate = (100.0 * d_ok / total) if total else 0
        print(f"  ch{ch}: 平均推理 {avg_inf:.1f}fps | 视频推进 {moved:.0f}s | "
              f"ΔOK={d_ok} ΔNG={d_ng} 合格率 {rate:.0f}%")
        ok(avg_inf >= min_inf_fps, f"{label} ch{ch} 平均推理帧率 {avg_inf:.1f} >= {min_inf_fps}")
        ok(moved > 30, f"{label} ch{ch} 视频在推进 ({moved:.0f}s)")
        ok(total >= 3, f"{label} ch{ch} 有周期产出 (Δ={total})")
    return stats


print("[稳定性] 后端 PID:", backend_pid(), "RSS:", rss_mb(), "MB")
log_mark = subprocess.run(["wc", "-l", "/tmp/tianjun_backend_8004.log"],
                          capture_output=True, text=True).stdout.split()[0]

# ---- Phase A: 双工位, 两个不同视频 + 同一模型 ----
set_mode(2)
start_channel(0, V_2K[0], PID_2K)
start_channel(1, V_2K[1], PID_2K)
run_phase._videos = {0: V_2K[0], 1: V_2K[1]}
run_phase._pids = {0: PID_2K, 1: PID_2K}
run_phase("PhaseA 双工位(异视频同模型)", [0, 1], PHASE_A_SEC, min_inf_fps=12)

# ---- Phase B: 三工位, ch0/ch1 同视频同模型 + ch2 异视频异模型 ----
set_mode(3)
start_channel(0, V_2K[0], PID_2K)
start_channel(1, V_2K[0], PID_2K)
start_channel(2, V_K1[0], PID_K1)
run_phase._videos = {0: V_2K[0], 1: V_2K[0], 2: V_K1[0]}
run_phase._pids = {0: PID_2K, 1: PID_2K, 2: PID_K1}
run_phase("PhaseB 三工位(2同+1异)", [0, 1, 2], PHASE_B_SEC, min_inf_fps=8)

# ---- 收尾核查 ----
print("\n[稳定性] === 收尾核查 ===")
new_log = subprocess.run(
    ["tail", "-n", f"+{int(log_mark) + 1}", "/tmp/tianjun_backend_8004.log"],
    capture_output=True, text=True).stdout
tb = [l for l in new_log.splitlines() if "Traceback" in l or "IOGPUMetal" in l]
ok(not tb, f"长跑期间无 Traceback / Metal 断言 (found={len(tb)})")

health = requests.get(f"{BACKEND}/plugins/lg-worktime/status", timeout=5).json()["plugin"]
ok(health["runtime_status"] == "loaded" and health["health"] == "ok",
   f"插件 runtime {health['runtime_status']}/{health['health']}")

stop_all()
set_mode(1)
print("\n结果:", "稳定性长跑全部通过" if not fails else f"失败 {len(fails)} 项: {fails}")
sys.exit(1 if fails else 0)
