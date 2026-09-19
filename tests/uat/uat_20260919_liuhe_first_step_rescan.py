"""UAT: 六和焊接组装工位 — 首步扫码抬离误判重复 + 多码采集未绑码误报 (2026-09-19).

客户现场叙事
============
1. 操作员把下层母排放进工装 → 整体拿起对着扫码枪扫母排码 (工件离开画面 3~5s) → 放回;
2. 相机视角里「下层母排」消失又重现, 期间背景里芯子料盒/母排料堆等其他标签持续可见;
3. 旧参数 (消失等待 2s + 等待可被打断): 其他标签在场 → 规则 B 把等待掐成 0 →
   下层母排立刻按消失结算, 重现被当成新周期首步 → 上一周期被结算成 NG(缺少步骤)/重复报警;
4. 每次结算还会弹「未绑定工件码」— 该工位走 v3.56 多码采集 (码由槽位状态机独占消费,
   永远不走单码绑定), 属程序误报 (v3.60.1 修复)。

两阶段对照 (先红后绿的现场版):
  --phase old  复刻客户现场参数 (下层母排 消失等待 2s / 等待不被打断 OFF, 无多码采集)
               → 预期复现: 一次装配被拆成 ≥2 个周期 / 下层母排重复入账 / 结算弹未绑码
  --phase new  整改参数 (下层母排 消失等待 5s + 等待不被打断 ON, 多码采集启用) + v3.60.1 代码修复
               → 预期: 扫码抬离被桥接, 下层母排每周期只入账一次, 结算不弹未绑码

跑法 (先起隔离后端):
  TIANJUN_DATA_DIR=/tmp/uat_liuhe_data python -m uvicorn backend.main:app --port 8011
  python tests/uat/uat_20260919_liuhe_first_step_rescan.py --base http://127.0.0.1:8011 --phase both
"""
import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

import requests

ASSET_DIR = Path("/tmp/uat_liuhe")
VIDEO = str(ASSET_DIR / "liuhe_ws1.mp4")
MODEL = str(ASSET_DIR / "liuhe_best9.pt")
DB_PATH = "/tmp/uat_liuhe/data/sql_app.db"
RUN_LOG = ASSET_DIR / "run.log"
CH = 0
VIDEO_LEN_S = 77
LABELS = ["下层母排", "芯子1", "芯子2", "芯子3", "芯子4", "芯子5", "芯子6",
          "盖母排", "模具盖板"]

_log_fh = None


def log(msg: str):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    if _log_fh:
        _log_fh.write(line + "\n")
        _log_fh.flush()


def build_steps(fixed: bool):
    steps = []
    for i, lb in enumerate(LABELS, start=1):
        s = {"id": i, "label": lb, "enabled": True, "threshold": 50,
             "min_frames": 1, "detection_type": "dynamic",
             "accept_once": True, "disappear_delay": 0.0}
        if lb == "下层母排":
            if fixed:
                # 5s: > 扫码抬离 2~4s (桥接闪断), < 工件间隔 7s+ (不吞真边界)
                s["disappear_delay"] = 5.0
                s["disappear_uninterruptible"] = True
            else:
                s["disappear_delay"] = 2.0
                s["disappear_uninterruptible"] = False
        if lb == "模具盖板":
            s["disappear_delay"] = 2.0
            s["min_frames"] = 2
        steps.append(s)
    return steps


def project_payload(name: str, fixed: bool):
    return {
        "name": name,
        "task_type": "detect",
        "logic_mode": "detection",
        "pipeline_config": {"detection_steps": list(range(1, len(LABELS) + 1))},
        "steps_config": build_steps(fixed),
        "events_config": [
            {"id": 1, "name": "合格", "show_notification": True,
             "require_ack": False, "actions": []},
            {"id": 2, "name": "不合格", "show_notification": True,
             "require_ack": False, "actions": []},
        ],
        "counters_config": [],
        "data_config": {},
    }


class Api:
    def __init__(self, base):
        self.base = base

    def call(self, method, path, **kw):
        r = getattr(requests, method)(f"{self.base}{path}", timeout=30, **kw)
        r.raise_for_status()
        return r.json()

    def try_post(self, path, **kw):
        try:
            requests.post(f"{self.base}{path}", timeout=15, **kw)
        except Exception:
            pass

    def results(self):
        try:
            r = requests.get(
                f"{self.base}/api/v1/source/detection/results?channel={CH}",
                timeout=10)
            return r.json() if r.status_code == 200 else {}
        except Exception:
            return {}


def ensure_scanner(api: Api):
    """确保系统里有一台在册扫码器 (usb_hid + 无码告警开) → has_any_scanner_present=True.

    这是「未绑码」断言有区分度的前提: 没有扫码器在册时后端本来就不弹, 测不出修复。
    """
    devs = api.call("get", "/api/v1/scanner/devices")
    items = devs.get("items", devs) if isinstance(devs, dict) else devs
    for d in items or []:
        if d.get("name") == "__uat_usb_gun":
            api.call("put", f"/api/v1/scanner/devices/{d['id']}", json={
                "enabled": True, "warn_no_barcode": True,
                "parse_config": {"usb": {"usage": "bind"}}})
            return d["id"]
    d = api.call("post", "/api/v1/scanner/devices", json={
        "name": "__uat_usb_gun", "ip": "", "port": 0,
        "device_type": "usb_hid", "enabled": True,
        "warn_no_barcode": True, "channel_id": CH,
        # usage=bind 才参与「未绑码」闸门 (拉工单/确认按钮用途的枪不拦周期)
        "parse_config": {"usb": {"usage": "bind"}}})
    return d.get("id")


def put_scan_collect(api: Api, project_id: int):
    api.call("put", f"/api/v1/scan-collect/config?project_id={project_id}", json={
        "enabled": True,
        "slots": [
            {"key": "busbar", "label": "母排码", "count": 1, "regex": "^BB.*"},
            {"key": "chip", "label": "芯子码", "count": 6, "regex": "^CH.*"},
            {"key": "fixture", "label": "工装码", "count": 1,
             "regex": "^FX.*", "role": "closing",
             "dedup_cross_group": "off"},
        ]})


def db_dump_last_session():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    sess = con.execute(
        "select id, name from detection_sessions order by id desc limit 1"
    ).fetchone()
    if not sess:
        con.close()
        return None, []
    cycles = []
    for c in con.execute(
            "select id, cycle_number, is_good, event_name, result_reason "
            "from detection_cycles where session_id=? order by id", (sess["id"],)):
        steps = [r["step_label"] for r in con.execute(
            "select step_label from step_records where cycle_id=? "
            "order by step_order, id", (c["id"],))]
        cycles.append({"cycle": c["cycle_number"], "is_good": bool(c["is_good"]),
                       "event": c["event_name"], "reason": c["result_reason"],
                       "steps": steps})
    con.close()
    return dict(sess), cycles


def run_phase(api: Api, phase: str):
    fixed = phase == "new"
    tag = "整改参数(5s+不被打断)" if fixed else "现场旧参数(2s+可打断)"
    log(f"===== phase={phase} {tag} =====")

    # 清场
    for ep in ("/api/v1/source/detection/ack-event",
               "/api/v1/source/detection/stop",
               "/api/v1/source/video/stop"):
        api.try_post(f"{ep}?channel={CH}", json={})
    time.sleep(1.5)

    # 项目
    name = f"__uat_liuhe_{phase}_{time.strftime('%H%M%S')}"
    proj = api.call("post", "/api/v1/projects", json=project_payload(name, fixed))
    pid = proj["id"]
    api.call("post", f"/api/v1/projects/{pid}/activate")
    log(f"项目 {name} (id={pid}) 已创建并激活")

    if fixed:
        put_scan_collect(api, pid)
        log("多码采集配置已启用 (母排×1 / 芯子×6 / 工装收尾×1)")

    # 视频 + 检测
    api.call("post", f"/api/v1/source/video/start?channel={CH}",
             json={"file_path": VIDEO, "speed": 1.0})
    api.call("post", f"/api/v1/source/detection/start?channel={CH}",
             json={"session_name": f"uat_liuhe_{phase}", "models": [
                 {"name": "main", "model_path": MODEL, "conf": 0.25,
                  "iou": 0.45, "priority": 100}]})
    log("视频 + 真模型检测已启动")

    # 跟踪
    t0 = time.time()
    seen_seq = set()
    events = []
    busbar_present = None
    while time.time() - t0 < VIDEO_LEN_S + 30:
        el = round(time.time() - t0, 1)
        res = api.results()
        labels_now = {d.get("label") for d in (res.get("detections") or [])}
        if busbar_present is None or ("下层母排" in labels_now) != busbar_present:
            busbar_present = "下层母排" in labels_now
            log(f"t+{el:6.1f}s 下层母排 {'出现' if busbar_present else '离场'} "
                f"(帧内: {sorted(labels_now)})")
        for e in (res.get("recent_events") or []):
            key = (e.get("seq"), e.get("timestamp"))
            if key in seen_seq:
                continue
            seen_seq.add(key)
            events.append(e)
            log(f"t+{el:6.1f}s 事件: {e.get('event_name')} | {e.get('reason')} "
                f"| warn_no_barcode={e.get('should_warn_no_barcode')}")
        if not res.get("is_detecting") and el > 10:
            log(f"t+{el:6.1f}s 检测已停止 (视频 EOF)")
            break
        time.sleep(0.8)

    for ep in ("/api/v1/source/detection/stop", "/api/v1/source/video/stop"):
        api.try_post(f"{ep}?channel={CH}", json={})
    time.sleep(2.0)

    sess, cycles = db_dump_last_session()
    log(f"落库 session={sess} 周期数={len(cycles)}")
    busbar_total = 0
    for c in cycles:
        n_busbar = c["steps"].count("下层母排")
        busbar_total += n_busbar
        log(f"  周期#{c['cycle']} {'OK' if c['is_good'] else 'NG'} "
            f"event={c['event']} reason={c['reason']} steps={c['steps']}")

    settle_events = [e for e in events
                     if str(e.get("event_id")) in ("1", "2")]
    warned = [e for e in settle_events if e.get("should_warn_no_barcode")]

    checks = []

    def check(name, ok, detail=""):
        checks.append((name, ok))
        log(f"  {'OK ' if ok else '!! '}{name} {detail}")

    if phase == "old":
        dup_repro = (len(cycles) >= 2 or busbar_total >= 2
                     or any((c["reason"] or "").find("缺少") >= 0
                            or (c["reason"] or "").find("重复") >= 0
                            for c in cycles))
        check("A1 旧参数复现: 一次装配被拆成多周期/重复入账/缺步NG", dup_repro,
              f"(cycles={len(cycles)}, 下层母排入账={busbar_total})")
        check("A2 旧参数+无多码采集: 结算照弹未绑码 (警告链路本身活着)",
              len(settle_events) > 0 and len(warned) == len(settle_events),
              f"({len(warned)}/{len(settle_events)} 条结算事件带警告)")
    else:
        per_cycle_dup = any(c["steps"].count("下层母排") > 1 for c in cycles)
        # 注: 视频片段掐头去尾 (起于装配中/止于装配中), 缺步 NG 是片段边界的正常
        # 产物, 不列为失败; 客户症状是「重复动作报警」→ 只盯 重复步骤 型 NG。
        ng_by_dup = any(not c["is_good"] and "重复" in (c["reason"] or "")
                        for c in cycles)
        check("B1 整改参数: 下层母排无重复入账", not per_cycle_dup,
              f"(各周期下层母排次数={[c['steps'].count('下层母排') for c in cycles]})")
        check("B2 整改参数: 无「重复步骤」型 NG", not ng_by_dup,
              f"(cycles={[(c['cycle'], c['is_good'], c['reason']) for c in cycles]})")
        check("B3 多码采集通道: 全部结算事件不弹未绑码", len(warned) == 0,
              f"({len(warned)}/{len(settle_events)} 条误弹)")
        # v3.60.1 代码修复前: 扫码抬离回位触发两次首步重现结算, 片段落库 2 个
        # 碎片周期 ([下层母排,盖母排] / [下层母排,芯子1])。修复后应为 0:
        # 片段起于装配中 (母排已被芯子盖住, 周期里无母排), 片内唯一一次母排
        # 放置属第二件且扫码闪断被桥接为一次连续出现 (~9.7s), EOF 前无第三件
        # → 片段内不存在真实工件边界, 不该有任何结算。
        check("B4 v3.60.1: 扫码闪断不再切周期 (结算周期数=0, 修复前=2)",
              len(cycles) == 0, f"(cycles={len(cycles)})")

    return checks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8011")
    ap.add_argument("--phase", choices=["old", "new", "both"], default="both")
    args = ap.parse_args()

    global _log_fh
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    _log_fh = open(RUN_LOG, "a", encoding="utf-8")
    log(f"== 六和工位一 UAT 开跑 base={args.base} phase={args.phase} ==")

    api = Api(args.base)
    projs = api.call("get", "/api/v1/projects")
    log(f"后端就绪 (现有项目 {len(projs) if isinstance(projs, list) else '?'} 个)")
    ensure_scanner(api)
    log("扫码器在册守门 OK (__uat_usb_gun usb_hid warn_no_barcode=on)")

    all_checks = []
    phases = ["old", "new"] if args.phase == "both" else [args.phase]
    for ph in phases:
        all_checks += run_phase(api, ph)

    failed = [c for c in all_checks if not c[1]]
    log(f"== 汇总: {len(all_checks) - len(failed)}/{len(all_checks)} 项通过, "
        f"failed: {len(failed)} ==")
    for name, ok in all_checks:
        log(f"  {'✅' if ok else '❌'} {name}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
