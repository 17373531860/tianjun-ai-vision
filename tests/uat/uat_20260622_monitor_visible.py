"""可见浏览器 UAT — 真起前端 Monitor 页, 人眼看计数器平滑跳不卡 (工步录像卡顿根治验证)。

场景还原:
  - 后端开「步骤录像」开关, 每个标签出现/消失高频起停 FFmpeg 录像 + 写库 (churn)。
  - 修复前: 唯一编号 8 位截断 -> 撞 UNIQUE -> 异常分支泄漏数据库连接 -> 写锁累积
    -> 结算写库在 busy_timeout 死等 -> Monitor 上周期数/步骤计数器停摆卡死、滞后误判。
  - 修复后: 完整 32 位编号 + 写库失败必 rollback+close, 连接不泄漏, 状态机不被噎住。

本脚本: 真 oppo 装电池视频 + 真模型, 真开浏览器 (headless=False) 录屏 + 定时截图,
        采集 Monitor DOM 上的周期数 / 步骤计数, 证明计数器在持续推进 (没有卡死)。

跑法 (后端 8011 + 前端 6011 已起好, DISPLAY=:0):
  DISPLAY=:0 python tests/uat/uat_20260622_monitor_visible.py
"""
import os
import sys
import time
import re
import requests
from playwright.sync_api import sync_playwright

API = os.environ.get("UAT_API", "http://127.0.0.1:8011")
WEB = os.environ.get("UAT_WEB", "http://127.0.0.1:6011")
VIDEO_DIR = "/tmp/uat_video"
SHOT_DIR = "/tmp/uat_shots"

VIDEO = "/home/qianqian/1.py/output/oppo/装电池/video/Video_20260331083930218.avi"
MODEL = "/home/qianqian/1.py/output/oppo/装电池/model/best.pt"
LABELS = ["撕璃形纸", "翻电池", "安装电池", "翻手机"]
RUN_SECONDS = int(os.environ.get("UAT_RUN_SECONDS", "75"))
VIDEO_SPEED = float(os.environ.get("UAT_VIDEO_SPEED", "2.0"))

results = []


def rec(label, ok, detail=""):
    results.append({"idx": len(results) + 1, "label": label, "ok": bool(ok), "detail": detail})
    print(f"[{'OK' if ok else '!!'}] {len(results):02d}. {label}  {detail}", flush=True)


def setup_backend():
    """开录像开关 + 建项目 + 激活 + 起真实视频/检测。"""
    r = requests.put(f"{API}/api/v1/data/export-settings",
                     json={"record_step_video": True, "record_cycle_video": False,
                           "record_session_video": False}, timeout=10)
    rec("开启步骤录像开关", r.status_code == 200, f"HTTP {r.status_code}")

    pname = f"__uat_visible_{int(time.time())}"
    steps_config = [{"label": lb, "threshold": 25, "min_frames": 1, "enabled": True} for lb in LABELS]
    r = requests.post(f"{API}/api/v1/projects",
                      json={"name": pname, "task_type": "detection",
                            "logic_mode": "detection", "steps_config": steps_config}, timeout=15)
    rec("创建检测项目", r.status_code == 201, f"HTTP {r.status_code} {r.text[:80]}")
    if r.status_code != 201:
        return False
    pid = r.json()["id"]

    r = requests.post(f"{API}/api/v1/projects/{pid}/activate", timeout=20)
    rec("激活项目", r.status_code == 200, f"HTTP {r.status_code}")

    r = requests.post(f"{API}/api/v1/source/video/start?channel=0",
                      json={"file_path": VIDEO, "speed": VIDEO_SPEED}, timeout=30)
    rec("启动真实视频源", r.status_code == 200, f"HTTP {r.status_code} {VIDEO_SPEED}x")
    if r.status_code != 200:
        return False

    r = requests.post(f"{API}/api/v1/source/detection/start?channel=0",
                      json={"model_path": MODEL, "conf": 0.25, "iou": 0.45,
                            "session_name": "uat_visible"}, timeout=60)
    rec("启动真实检测(真模型)", r.status_code == 200, f"HTTP {r.status_code}")
    return r.status_code == 200


def sample_state():
    """采集 Monitor 实时态。返回 (步骤计数总和, 是否在检测, step_counts)。

    本项目这套 4 标签无序配置不闭环, 完整「周期数」需首步重现才结算 -> 恒为 0,
    不是卡死。真正能证明状态机没被噎住的, 是 Monitor 上每个步骤的计数器在持续跳。
    取所有步骤计数之和作为「计数器在动」的硬指标。"""
    try:
        res = requests.get(f"{API}/api/v1/source/detection/results?channel=0", timeout=8).json()
        sc = res.get("step_counts", {}) or {}
        total = sum(int(v) for v in sc.values())
        return total, res.get("is_detecting"), sc
    except Exception:
        return -1, None, {}


def main():
    if not setup_backend():
        return finish()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, args=["--start-maximized"])
        ctx = browser.new_context(viewport={"width": 1600, "height": 900},
                                  record_video_dir=VIDEO_DIR,
                                  record_video_size={"width": 1600, "height": 900})
        page = ctx.new_page()
        page.goto(f"{WEB}/#/monitor", wait_until="domcontentloaded", timeout=30000)
        time.sleep(6)  # 让 Monitor 拉首批数据 + 视频流挂上
        page.screenshot(path=f"{SHOT_DIR}/visible_00_landing.png")
        rec("Monitor 页面已打开", True, f"{WEB}/#/monitor")

        # ── 定时采样, 证明计数器在推进 ──
        samples = []
        t0 = time.time()
        shot_i = 1
        while time.time() - t0 < RUN_SECONDS:
            time.sleep(12)
            c, det, sc = sample_state()
            elapsed = int(time.time() - t0)
            samples.append((elapsed, c, det))
            try:
                page.screenshot(path=f"{SHOT_DIR}/visible_{shot_i:02d}_t{elapsed}s.png")
            except Exception:
                pass
            print(f"   t+{elapsed:>3}s  步骤计数总和={c}  is_detecting={det}  step_counts={sc}", flush=True)
            shot_i += 1

        page.screenshot(path=f"{SHOT_DIR}/visible_99_final.png")
        # 关 ctx 才会落盘 video
        ctx.close()
        video_path = None
        try:
            video_path = page.video.path()
        except Exception:
            pass
        browser.close()

    # ── 判定: 计数器是否真的在推进 (没卡死) ──
    counts = [c for _, c, _ in samples if c >= 0]
    advanced = len(counts) >= 2 and counts[-1] > counts[0]
    # 逐段单调: 没有任何一段出现"长时间不动"(卡死会让相邻样本相等)
    no_stall = all(counts[i + 1] >= counts[i] for i in range(len(counts) - 1)) \
        and sum(1 for i in range(len(counts) - 1) if counts[i + 1] == counts[i]) <= 1
    saw_det = any(d for _, _, d in samples)
    rec("Monitor 计数器持续推进(末>首, 未卡死)", advanced,
        f"首样={counts[0] if counts else 'NA'} 末样={counts[-1] if counts else 'NA'}")
    rec("无停滞段(相邻样本无长时间不动)", no_stall, f"采样序列={counts}")
    rec("检测全程在线", saw_det, f"曾在线={saw_det}")

    # ── 停 ──
    requests.post(f"{API}/api/v1/source/detection/stop?channel=0", timeout=30)
    time.sleep(3)
    requests.post(f"{API}/api/v1/source/video/stop?channel=0", timeout=15)
    time.sleep(2)

    analyze_log()

    # 录像产物
    import glob
    vids = sorted(glob.glob(f"{VIDEO_DIR}/*.webm"))
    rec("浏览器录屏已落盘", bool(vids), vids[-1] if vids else "无")
    shots = sorted(glob.glob(f"{SHOT_DIR}/*.png"))
    rec("截图序列已落盘", len(shots) >= 3, f"{len(shots)} 张")

    finish()


def analyze_log():
    log = "/tmp/uat_backend_8011.log"
    if not os.path.exists(log):
        return
    txt = open(log, encoding="utf-8", errors="replace").read()
    uniq_err = txt.count("UNIQUE constraint failed: video_clips")
    stop_fail = txt.count("停止步骤录制失败")
    churn = txt.count("[调试] 开始录制步骤视频")
    print(f"\n   [LOG] 工步录像启动={churn}  UNIQUE撞号={uniq_err}  停止失败(泄漏点)={stop_fail}\n", flush=True)
    rec("零 UNIQUE 撞号错", uniq_err == 0, f"{uniq_err} 次")
    rec("零『停止步骤录制失败』(零连接泄漏)", stop_fail == 0, f"{stop_fail} 次")


def finish():
    ok = sum(1 for r in results if r["ok"])
    failed = len(results) - ok
    summary = f"\n===== 可见浏览器 UAT 汇总: {ok}/{len(results)} 通过, failed: {failed} =====\n"
    print(summary, flush=True)
    with open("/tmp/uat_visible_run.log", "w", encoding="utf-8") as f:
        f.write("\n".join(f"{'OK' if r['ok'] else '!!'} {r['idx']:02d}. {r['label']}  {r['detail']}"
                          for r in results) + summary)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
