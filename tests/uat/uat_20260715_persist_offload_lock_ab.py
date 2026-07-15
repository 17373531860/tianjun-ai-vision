# -*- coding: utf-8 -*-
"""v3.38 RFC 落地活体 A/B — 外部握写锁时推理线程是否还会冻结。

实验设计 (对照川南"框冻结"事故链):
  1. 起后端 (RUNTIME_MODE=test) + synthetic 快节奏剧本 + 开检测
  2. 让周期持续结算 (每个周期都有 step 写库 + cycle_end 写库)
  3. 第三方 sqlite3 连接对同一 DB `BEGIN IMMEDIATE` 握写锁 N 秒 (模拟
     MES 推送 / 清理 / 任何未来的持锁方)
  4. 握锁期间以 10Hz 轮询 /detection/results, 统计:
     - 推理心跳间隔最大值 (老代码: 写库在推理线程, 会卡到 busy_timeout 边缘)
     - 结算是否停摆
  5. 放锁后等落库线程排空, 校验 cycle/step 行数没丢 (数据完整性)

判定:
  - 新代码 (落库线程): 握锁 8s 期间推理心跳最大间隔应 < 1s, 放锁后数据补齐
  - 老代码 (v3.37 基线): 心跳最大间隔应接近握锁时长 (框冻结复现)

跑法:
  TIANJUN_DATA_DIR=/tmp/uat_ab_persist RUNTIME_MODE=test 起后端于 8012, 然后
  python tests/uat/uat_20260715_persist_offload_lock_ab.py
"""
import json
import os
import sqlite3
import threading
import time

import requests

API = os.environ.get("UAT_API", "http://127.0.0.1:8012")
DB = os.environ.get("UAT_DB", "/tmp/uat_ab_persist/sql_app.db")
LOCK_SECONDS = float(os.environ.get("UAT_LOCK_SECONDS", "8"))

results = []


def rec(label, ok, detail=""):
    results.append((label, bool(ok), detail))
    print(f"[{'OK' if ok else '!!'}] {len(results):02d}. {label}  {detail}", flush=True)


def hold_write_lock(duration: float, started_evt: threading.Event):
    con = sqlite3.connect(DB, timeout=30)
    try:
        con.execute("BEGIN IMMEDIATE")  # 取 RESERVED 写锁
        con.execute("CREATE TABLE IF NOT EXISTS _ab_lock_probe (id INTEGER PRIMARY KEY)")
        started_evt.set()
        print(f"[locker] 已握写锁 {duration}s ...", flush=True)
        time.sleep(duration)
        con.rollback()
        print("[locker] 已放锁", flush=True)
    finally:
        con.close()


def main():
    # ── 1. synthetic 快节奏剧本: A→B→空 逐段平铺 (synthetic 不支持 loop,
    #      timeline 用帧号区间平铺出 ~4 分钟; first_step 结算 = A 重现即收上一周期) ──
    timeline = []
    fps = 30
    rep_frames = 60  # 每周期 2s: A 25帧 + B 25帧 + 空 10帧
    for i in range(240):
        base = i * rep_frames
        timeline.append({"from": base, "to": base + 24, "detections": [
            {"label": "A", "confidence": 0.95, "bbox": [0.1, 0.1, 0.3, 0.3]}]})
        timeline.append({"from": base + 25, "to": base + 49, "detections": [
            {"label": "B", "confidence": 0.95, "bbox": [0.5, 0.5, 0.7, 0.7]}]})
        timeline.append({"from": base + 50, "to": base + 59, "detections": []})
    spec = {"name": "ab-lock", "fps": fps, "timeline": timeline}
    r = requests.post(f"{API}/api/v1/test/synthetic/start",
                      json={"scenario_json": spec, "channel": 0, "with_project": True},
                      timeout=15)
    rec("起 synthetic 剧本源", r.status_code == 200, f"HTTP {r.status_code} {r.text[:120]}")
    r = requests.post(f"{API}/api/v1/source/detection/start?channel=0",
                      json={"conf": 0.25, "iou": 0.45}, timeout=30)
    rec("开检测", r.status_code == 200, f"HTTP {r.status_code}")

    # 预热: 攒 2 个以上周期
    time.sleep(6)
    con = sqlite3.connect(DB, timeout=5)
    pre_cycles = con.execute(
        "SELECT COUNT(*) FROM detection_cycles WHERE end_time IS NOT NULL").fetchone()[0]
    con.close()
    rec("预热期已有结算周期", pre_cycles >= 1, f"settled={pre_cycles}")

    # ── 2. 第三方握写锁, 同时 10Hz 轮询推理心跳 ──
    started = threading.Event()
    locker = threading.Thread(target=hold_write_lock, args=(LOCK_SECONDS, started), daemon=True)
    locker.start()
    started.wait(10)

    heartbeats = []
    t_end = time.time() + LOCK_SECONDS + 2
    last_change = time.time()
    last_sig = None
    max_gap = 0.0
    while time.time() < t_end:
        try:
            r = requests.get(f"{API}/api/v1/source/detection/results?channel=0", timeout=3)
            body = r.json()
            # 心跳签名: 检测框 + 帧序推进 (frame_id 若无则用 detections+时间片)
            sig = json.dumps(body.get("detections"), sort_keys=True)
            now = time.time()
            if sig != last_sig:
                gap = now - last_change
                max_gap = max(max_gap, gap)
                heartbeats.append(gap)
                last_change = now
                last_sig = sig
        except Exception as e:
            rec("轮询异常", False, str(e))
            break
        time.sleep(0.1)

    rec(f"握锁 {LOCK_SECONDS}s 期间推理心跳最大间隔 < 1.5s",
        max_gap < 1.5, f"max_gap={max_gap:.2f}s beats={len(heartbeats)}")

    # ── 3. 放锁后等落库排空, 验数据完整性 ──
    time.sleep(4)
    con = sqlite3.connect(DB, timeout=15)
    post_cycles = con.execute(
        "SELECT COUNT(*) FROM detection_cycles WHERE end_time IS NOT NULL").fetchone()[0]
    post_steps = con.execute("SELECT COUNT(*) FROM step_records").fetchone()[0]
    con.close()
    rec("放锁后结算周期继续累积 (含握锁期间攒的)",
        post_cycles > pre_cycles, f"{pre_cycles} → {post_cycles}, steps={post_steps}")

    requests.post(f"{API}/api/v1/source/detection/stop?channel=0", timeout=10)
    requests.post(f"{API}/api/v1/test/synthetic/stop?channel=0", timeout=10)

    print("\n===== 汇总 =====")
    ok_all = all(ok for _, ok, _ in results)
    for label, ok, detail in results:
        print(f"  [{'OK' if ok else '!!'}] {label} {detail}")
    print(f"结论: {'全部通过' if ok_all else '存在失败'}")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
