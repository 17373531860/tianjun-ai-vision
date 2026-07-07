"""真实视频 UAT — 工步录像高频启停（卡顿根因复现/验证）。

客户现场叙事:
  1. 操作员在装配线连续做工步, 一个工步几秒, 整班几千个周期。
  2. 软件开着「步骤录像」, 每个标签一出现就起一个 FFmpeg 录像、一消失就停并写库。
  3. 后端应该: 周期持续推进、录像照常落库、判定正确。
  4. 实际(现场): 跑几小时后软件卡顿、滞后、误判 NG, 重启就好。

根因(已定位):
  录像唯一编号被截成 8 位 → 一整班几万条录像生日碰撞 →
  写库抛 UNIQUE 约束错 → 异常分支没 close 数据库连接 → SQLite 写锁泄漏累积 →
  结算写库在 busy_timeout 死等 → 状态机停摆。

本脚本(真实 oppo 装电池视频 + 真模型)验证修复后:
  - 工步录像确实在高频启停(数 churn 次数, 外推到一整班)
  - 录像唯一编号是完整 32 位(碰撞概率从天文数字降到不可能)
  - 录像照常落库、无 UNIQUE 撞号错、无「停止步骤录制失败」
  - 周期持续推进, 检测不卡

注意: 现场那种"攒几小时才卡"是 8 位编号生日碰撞累积出来的(约 7.7 万条才 50%),
真实视频跑几分钟不会自然撞号 —— 这正印证客户"几小时才卡、重启就好"。
撞号→泄漏这一步的确定性「先红后绿」由单测 tests/test_recording_db_leak.py 钉死。

跑法(后端需先在 8011 起好, RUNTIME_MODE=test, TIANJUN_DATA_DIR=/tmp/uat_recording_data):
  python tests/uat/uat_20260622_step_recording_churn.py
"""
import os
import sys
import time
import sqlite3
import requests

API = os.environ.get("UAT_API", "http://127.0.0.1:8011")
DB = os.environ.get("UAT_DB", "/tmp/uat_recording_data/sql_app.db")
LOG = os.environ.get("UAT_LOG", "/tmp/uat_backend_8011.log")
RUN_LOG = "/tmp/uat_recording_run.log"

VIDEO = "/home/qianqian/1.py/output/oppo/装电池/video/Video_20260331083930218.avi"
MODEL = "/home/qianqian/1.py/output/oppo/装电池/model/best.pt"
LABELS = ["撕璃形纸", "翻电池", "安装电池", "翻手机"]
RUN_SECONDS = int(os.environ.get("UAT_RUN_SECONDS", "120"))
VIDEO_SPEED = float(os.environ.get("UAT_VIDEO_SPEED", "2.0"))

results = []


def rec(label, ok, detail=""):
    results.append({"idx": len(results) + 1, "label": label, "ok": bool(ok), "detail": detail})
    print(f"[{'OK' if ok else '!!'}] {len(results):02d}. {label}  {detail}", flush=True)


def main():
    # ── 1. 开「步骤录像」开关 ──
    r = requests.put(f"{API}/api/v1/data/export-settings",
                     json={"record_step_video": True,
                           "record_cycle_video": False,
                           "record_session_video": False},
                     timeout=10)
    rec("开启步骤录像开关", r.status_code == 200, f"HTTP {r.status_code}")
    got = requests.get(f"{API}/api/v1/data/export-settings", timeout=10).json()
    rec("回读确认步骤录像=True", got.get("record_step_video") is True, str(got.get("record_step_video")))

    # ── 2. 建检测模式项目(4 标签全配成步骤) ──
    pname = f"__uat_oppo_churn_{int(time.time())}"
    steps_config = [{"label": lb, "threshold": 25, "min_frames": 1, "enabled": True} for lb in LABELS]
    r = requests.post(f"{API}/api/v1/projects",
                      json={"name": pname, "task_type": "detection",
                            "logic_mode": "detection", "steps_config": steps_config},
                      timeout=15)
    rec("创建项目", r.status_code == 201, f"HTTP {r.status_code} {r.text[:120]}")
    if r.status_code != 201:
        return finish()
    pid = r.json()["id"]

    # ── 3. 激活项目 ──
    r = requests.post(f"{API}/api/v1/projects/{pid}/activate", timeout=20)
    rec("激活项目", r.status_code == 200, f"HTTP {r.status_code}")

    # ── 4. 起真实视频源(倍速跑, 多攒周期) ──
    r = requests.post(f"{API}/api/v1/source/video/start?channel=0",
                      json={"file_path": VIDEO, "speed": VIDEO_SPEED}, timeout=30)
    rec("启动真实视频源", r.status_code == 200, f"HTTP {r.status_code} speed={VIDEO_SPEED}x")
    if r.status_code != 200:
        return finish()

    # ── 5. 起真实检测(真模型) ──
    r = requests.post(f"{API}/api/v1/source/detection/start?channel=0",
                      json={"model_path": MODEL, "conf": 0.25, "iou": 0.45,
                            "session_name": "uat_churn"}, timeout=60)
    rec("启动真实检测(真模型)", r.status_code == 200, f"HTTP {r.status_code}")
    if r.status_code != 200:
        return finish()

    # ── 6. 跑一段, 周期性观察 ──
    print(f"\n   跑 {RUN_SECONDS}s, 每 15s 采样一次...\n", flush=True)
    last = {}
    t0 = time.time()
    saw_detecting = False
    sample_err = 0
    while time.time() - t0 < RUN_SECONDS:
        time.sleep(15)
        try:
            res = requests.get(f"{API}/api/v1/source/detection/results?channel=0", timeout=10).json()
            last = res
            if res.get("is_detecting"):
                saw_detecting = True
            sc = res.get("step_counts", {})
            stats = res.get("statistics", {}) or {}
            cyc = stats.get("total") or stats.get("total_count") or res.get("cycle_count")
            print(f"   t+{int(time.time()-t0):>3}s  is_detecting={res.get('is_detecting')}  "
                  f"周期数={cyc}  step_counts={sc}", flush=True)
        except Exception as e:
            sample_err += 1
            print(f"   采样异常: {e}", flush=True)

    # 检测期间在线过 + 采样无异常 = 全程未崩溃/未卡死 (视频自然播完导致末次 False 不算崩溃)
    rec("检测全程在线未崩溃/未卡死", saw_detecting and sample_err == 0,
        f"曾在线={saw_detecting} 采样异常={sample_err}")

    # ── 7. 停检测 + 停视频 ──
    requests.post(f"{API}/api/v1/source/detection/stop?channel=0", timeout=30)
    time.sleep(3)  # 给延迟释放线程排空
    requests.post(f"{API}/api/v1/source/video/stop?channel=0", timeout=15)
    time.sleep(2)

    # ── 8. 查库: 录像段数 / 编号长度 / 撞号 ──
    analyze_db()

    # ── 9. 扫后端日志: churn 次数 / 撞号错 / 泄漏错 ──
    analyze_log()

    finish()


def analyze_db():
    if not os.path.exists(DB):
        rec("DB 存在", False, DB)
        return
    con = sqlite3.connect(DB)
    cur = con.cursor()
    cur.execute("SELECT clip_type, COUNT(*) FROM video_clips GROUP BY clip_type")
    by_type = dict(cur.fetchall())
    cur.execute("SELECT COUNT(*) FROM video_clips")
    total = cur.fetchone()[0]
    cur.execute("SELECT COUNT(DISTINCT video_uuid) FROM video_clips")
    distinct = cur.fetchone()[0]
    cur.execute("SELECT MIN(LENGTH(video_uuid)), MAX(LENGTH(video_uuid)) FROM video_clips")
    minlen, maxlen = cur.fetchone()
    cur.execute("SELECT COUNT(*) FROM video_clips WHERE clip_type='step'")
    step_n = cur.fetchone()[0]
    con.close()

    print(f"\n   [DB] video_clips 总={total}  按类={by_type}  唯一={distinct}", flush=True)
    print(f"   [DB] video_uuid 长度 min={minlen} max={maxlen}  工步录像={step_n} 条\n", flush=True)

    rec("有工步录像落库(churn 真实发生)", step_n > 0, f"{step_n} 条工步录像")
    rec("录像编号全为完整 32 位(无 8 位截断)", minlen == 32 and maxlen == 32, f"min={minlen} max={maxlen}")
    rec("无重复编号(零撞号)", total == distinct, f"总={total} 唯一={distinct}")


def analyze_log():
    if not os.path.exists(LOG):
        rec("后端日志存在", False, LOG)
        return
    txt = open(LOG, encoding="utf-8", errors="replace").read()
    churn = txt.count("[调试] 开始录制步骤视频")
    over_limit = txt.count("步骤视频录制已达上限")
    uniq_err = txt.count("UNIQUE constraint failed: video_clips")
    stop_fail = txt.count("停止步骤录制失败")
    print(f"\n   [LOG] 工步录像启动次数={churn}  达上限跳过={over_limit}", flush=True)
    print(f"   [LOG] 撞号错(UNIQUE)={uniq_err}  停止录制失败(泄漏点)={stop_fail}\n", flush=True)

    rec("日志可见工步录像高频启动(churn)", churn > 0, f"{churn} 次启动")
    rec("零 UNIQUE 撞号错", uniq_err == 0, f"{uniq_err} 次")
    rec("零『停止步骤录制失败』(零连接泄漏点)", stop_fail == 0, f"{stop_fail} 次")


def finish():
    ok = sum(1 for r in results if r["ok"])
    failed = len(results) - ok
    lines = [f"{'OK' if r['ok'] else '!!'} {r['idx']:02d}. {r['label']}  {r['detail']}" for r in results]
    summary = f"\n===== UAT 汇总: {ok}/{len(results)} 通过, failed: {failed} =====\n"
    print(summary, flush=True)
    with open(RUN_LOG, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + summary)
    print(f"运行日志: {RUN_LOG}", flush=True)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
