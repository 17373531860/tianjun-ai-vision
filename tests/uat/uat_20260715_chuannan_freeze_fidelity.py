# -*- coding: utf-8 -*-
"""川南"检测框冻结"事故链 · 客户原样复现 + 治本验证 UAT（可见浏览器, 路径 H）。

现场叙事（对照 2026-07 川南反馈）:
  1. 操作员本地视频跑检测, 同时"外部对接"推送开着, 但对方接收服务没起
     (连接被拒, WinError 10061 同类)。
  2. 客户看到: 识别出某类别后框卡在画面不动, 后续类别全不识别, 周期超时 NG。
  3. 根因: 周期收尾链在推理线程写库, MES 推送线程握着写锁等 HTTP 超时,
     推理线程 commit 干等 busy_timeout。
  4. v3.38 治本后预期: 同样配置下画面/步骤统计持续推进, 零冻结;
     推送连续失败自动熔断, 端点恢复后自动探活恢复。

本脚本三证据链:
  A. API 契约 — 死端点推送开启下, 检测心跳无 >1.5s 停顿; 熔断器自动打开;
     周期/步骤照常落库。
  B. 可见浏览器 — Monitor 页真渲染, 死端点推送期间 + 第三方握写锁 8s 期间,
     页面步骤统计/计数持续变化(截图对照), 人眼可复核录像。
  C. 数据落库 — cycle/step 行数持续累积, MES 通信日志记录了失败推送。

跑法(前置: 后端 8001 RUNTIME_MODE=test + 前端 dev 6001 已起):
  cd tests/uat && python uat_20260715_chuannan_freeze_fidelity.py
"""
import json
import os
import sqlite3
import threading
import time

import requests
from playwright.sync_api import sync_playwright

from _common import UatRun, launch_browser, filter_console_errors

API = os.environ.get("UAT_API", "http://127.0.0.1:8001")
FRONT = os.environ.get("UAT_FRONT", "http://127.0.0.1:6001")
DB = os.environ.get("UAT_DB", "/tmp/uat_cn_fid/sql_app.db")
DEAD_URL = "http://127.0.0.1:59999/api/v1/task/complete"  # 无人监听 → 连接被拒

run = UatRun("chuannan_freeze_fidelity")


# ==================== 工具 ====================

def db_counts():
    con = sqlite3.connect(DB, timeout=10)
    try:
        cycles = con.execute(
            "SELECT COUNT(*) FROM detection_cycles WHERE end_time IS NOT NULL").fetchone()[0]
        steps = con.execute("SELECT COUNT(*) FROM step_records").fetchone()[0]
        return cycles, steps
    finally:
        con.close()


def heartbeat_probe(duration: float) -> float:
    """轮询检测结果接口, 返回期间检测内容签名的最大不变间隔(秒)。"""
    last_sig, last_change, max_gap = None, time.time(), 0.0
    t_end = time.time() + duration
    while time.time() < t_end:
        try:
            body = requests.get(
                f"{API}/api/v1/source/detection/results?channel=0", timeout=3).json()
            sig = json.dumps(body.get("detections"), sort_keys=True)
        except Exception:
            sig = "<err>"
        now = time.time()
        if sig != last_sig:
            max_gap = max(max_gap, now - last_change)
            last_change, last_sig = now, sig
        time.sleep(0.1)
    return max(max_gap, time.time() - last_change if last_sig is not None else 0)


def hold_write_lock(duration: float, started: threading.Event):
    con = sqlite3.connect(DB, timeout=30)
    try:
        con.execute("BEGIN IMMEDIATE")
        started.set()
        print(f"[locker] 已握写锁 {duration}s", flush=True)
        time.sleep(duration)
        con.rollback()
        print("[locker] 已放锁", flush=True)
    finally:
        con.close()


def monitor_stats_text(page) -> str:
    """抓 Monitor 页与检测进度相关的文本快照 (计数/步骤区)。"""
    return page.evaluate("document.body.innerText")


def sample_det_count(page, duration: float) -> set:
    """采样 Monitor 底栏「检测数: N」(后端 detection/results 驱动的 DOM 文本)。

    剧本目标每秒出现/消失, 后端活着时 N 应在 0/1 间跳变;
    后端冻结(客户症状)时 N 会定格 → 返回的取值集合大小=1。
    """
    import re
    seen = set()
    t_end = time.time() + duration
    while time.time() < t_end:
        txt = page.evaluate("document.body.innerText")
        m = re.search(r"检测数[:：]\s*(\d+)", txt)
        if m:
            seen.add(m.group(1))
        time.sleep(0.3)
    return seen


# ==================== Phase 0: 环境自检 + 客户配置复刻 ====================

def phase0_setup():
    r = requests.get(f"{API}/api/v1/projects", timeout=10)
    run.step("后端 8001 就绪", r.status_code == 200, f"HTTP {r.status_code}")
    r = requests.get(FRONT, timeout=10)
    run.step("前端 6001 就绪", r.status_code == 200, f"HTTP {r.status_code}")

    # 复刻客户配置: 外部推送连接指向"没起服务"的端点, 周期结束即推
    conns = requests.get(f"{API}/api/v1/mes/gateway/connections", timeout=10).json()
    for c in conns:
        if c["name"].startswith("__uat_cn_dead"):
            requests.delete(f"{API}/api/v1/mes/gateway/connections/{c['id']}", timeout=10)
    r = requests.post(f"{API}/api/v1/mes/gateway/connections", json={
        "name": "__uat_cn_dead_endpoint",
        "adapter_type": "rest",
        "enabled": True,
        "config": {"url": DEAD_URL, "method": "POST",
                   "connect_timeout": 2, "read_timeout": 2},
        "push_events": ["cycle_end"],
        "retry_count": 1,
        "retry_interval_sec": 1,
    }, timeout=10)
    ok = r.status_code == 200
    run.step("建死端点推送连接(复刻客户'接收服务未起')", ok, f"HTTP {r.status_code}")
    return r.json()["id"] if ok else None


# ==================== Phase A: 起检测 + 死端点推送下的心跳契约 ====================

def phase_a(conn_id):
    # synthetic 剧本: A(0.8s)→B(0.8s)→空(0.4s) 平铺 10 分钟, 每 2s 一周期
    timeline = []
    for i in range(300):
        base = i * 60
        timeline.append({"from": base, "to": base + 24, "detections": [
            {"label": "A", "confidence": 0.95, "bbox": [0.1, 0.1, 0.3, 0.3]}]})
        timeline.append({"from": base + 25, "to": base + 49, "detections": [
            {"label": "B", "confidence": 0.95, "bbox": [0.5, 0.5, 0.7, 0.7]}]})
        timeline.append({"from": base + 50, "to": base + 59, "detections": []})
    r = requests.post(f"{API}/api/v1/test/synthetic/start", json={
        "scenario_json": {"name": "cn-fidelity", "fps": 30, "timeline": timeline},
        "channel": 0, "with_project": True}, timeout=15)
    run.step("起 synthetic 剧本(等价客户本地视频)", r.status_code == 200, f"HTTP {r.status_code}")
    r = requests.post(f"{API}/api/v1/source/detection/start?channel=0",
                      json={"conf": 0.25, "iou": 0.45}, timeout=30)
    run.step("开检测", r.status_code == 200, f"HTTP {r.status_code}")

    time.sleep(6)  # 预热攒周期(每周期都会对死端点推一次并失败)
    c0, s0 = db_counts()
    run.step("预热期已有结算周期(死端点推送已在打)", c0 >= 1, f"cycles={c0} steps={s0}")

    # 客户症状观察窗: 死端点推送持续打的 20s 里, 检测心跳不许停 >1.5s
    gap = heartbeat_probe(20)
    run.step("死端点推送期间检测心跳最大停顿 <1.5s (客户症状=8~15s冻结)",
             gap < 1.5, f"max_gap={gap:.2f}s")

    # 熔断器: 连续失败(默认阈值5)后应自动打开
    conns = requests.get(f"{API}/api/v1/mes/gateway/connections", timeout=10).json()
    cn = next((c for c in conns if c["id"] == conn_id), {})
    circ = cn.get("circuit", {})
    run.step("推送熔断器已自动打开(不再对死端点空耗)",
             circ.get("open") is True, f"circuit={circ}")

    c1, s1 = db_counts()
    run.step("周期/步骤持续落库(数据零丢失)", c1 > c0 and s1 > s0,
             f"cycles {c0}→{c1}, steps {s0}→{s1}")
    return c1


# ==================== Phase B: 可见浏览器 Monitor 人眼复核 ====================

def phase_b():
    with sync_playwright() as p:
        browser, ctx, page, console_errs = launch_browser(p, record_video_dir=run.video_dir)
        page.goto(f"{FRONT}/#/monitor", wait_until="domcontentloaded")
        time.sleep(4)
        run.shot(page, "B1_monitor_死端点推送中")
        txt1 = monitor_stats_text(page)
        run.step("Monitor 页已渲染", len(txt1) > 100, f"文本长度={len(txt1)}")

        # 「检测数」由后端 detection/results 驱动; 剧本目标每秒出现/消失,
        # 后端活着 → 取值在 0/1 间跳变; 后端冻结(客户症状) → 定格单一值。
        # 不能用整页文本比对: 「运行时间」是前端本地计时, 冻结时也会变(假绿灯)。
        seen = sample_det_count(page, 6)
        run.shot(page, "B2_monitor_6秒后_检测数在跳变")
        run.step("死端点推送期间前端「检测数」持续跳变(非冻结)",
                 len(seen) >= 2, f"取值集合={sorted(seen)}")

        # 加码: 第三方握写锁 8s, 人眼看画面照常跑
        started = threading.Event()
        t = threading.Thread(target=hold_write_lock, args=(8.0, started), daemon=True)
        t.start()
        started.wait(10)
        run.shot(page, "B3_握写锁开始_画面应仍在跑")
        seen_lock = sample_det_count(page, 7)
        run.shot(page, "B4_握写锁第7秒_检测数在跳变")
        run.step("外部握写锁 8s 期间前端「检测数」仍持续跳变(治本验证)",
                 len(seen_lock) >= 2, f"取值集合={sorted(seen_lock)}")
        t.join(timeout=10)
        time.sleep(3)
        run.shot(page, "B5_放锁后_落库线程补齐")

        real_errs = filter_console_errors(console_errs)
        run.step("浏览器控制台无前端逻辑报错", not real_errs, f"真报错={real_errs[:3]}")
        ctx.close()
        browser.close()


# ==================== Phase C: 落库终验 + 端点恢复探活 + 清理 ====================

def phase_c(conn_id, cycles_before_b):
    time.sleep(2)
    c2, s2 = db_counts()
    run.step("Phase B(含8s锁)结束后周期继续累积", c2 > cycles_before_b,
             f"cycles {cycles_before_b}→{c2}, steps={s2}")

    # MES 通信日志应记录了对死端点的失败推送 (客户排查线索完整)
    con = sqlite3.connect(DB, timeout=10)
    try:
        fails = con.execute(
            "SELECT COUNT(*) FROM mes_comm_logs WHERE success=0").fetchone()[0]
    except Exception:
        fails = -1
    finally:
        con.close()
    run.step("通信日志记录了失败推送(留证不缺账)", fails > 0, f"failed_logs={fails}")

    # 端点"恢复": 起一个临时接收服务, 手动测试连接 → 熔断复位
    import http.server, socketserver
    class _OK(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length', 0) or 0))
            self.send_response(200); self.end_headers(); self.wfile.write(b'{"code":0}')
        def log_message(self, *a):
            pass
    srv = socketserver.TCPServer(("127.0.0.1", 59999), _OK)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        r = requests.post(f"{API}/api/v1/mes/gateway/connections/{conn_id}/test",
                          json={"event_type": "cycle_end"}, timeout=15)
        ok = r.status_code == 200 and r.json().get("success") is True
        run.step("接收端恢复后'测试连接'成功", ok, f"HTTP {r.status_code}")
        conns = requests.get(f"{API}/api/v1/mes/gateway/connections", timeout=10).json()
        circ = next((c for c in conns if c["id"] == conn_id), {}).get("circuit", {})
        run.step("熔断器已复位(端点恢复自动回到正常推送)",
                 circ.get("open") is False, f"circuit={circ}")
    finally:
        srv.shutdown()

    # 清理
    requests.post(f"{API}/api/v1/source/detection/stop?channel=0", timeout=10)
    requests.post(f"{API}/api/v1/test/synthetic/stop?channel=0", timeout=10)
    requests.delete(f"{API}/api/v1/mes/gateway/connections/{conn_id}", timeout=10)
    run.step("清理完成(停检测/停剧本/删UAT连接)", True)


def main():
    conn_id = phase0_setup()
    if conn_id is None:
        return run.finish()
    cycles_a = phase_a(conn_id)
    phase_b()
    phase_c(conn_id, cycles_a)
    return run.finish()


if __name__ == "__main__":
    raise SystemExit(main())
