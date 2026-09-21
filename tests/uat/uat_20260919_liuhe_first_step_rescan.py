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
  --phase new  定案整改参数 + v3.60.1 代码修复 + 现场同源扫码流注入
               → 预期 = 工程师业务真值 1NG + 1OK:
                 · 视觉侧落库 1 周期 OK (第二件 9 步全齐; 第一件半截缺首步不开周期)
                 · 扫码侧组1 ng_missing (半截件少扫→挂起→按NG放行) + 组2 ok (码收齐)

定案整改参数 (2026-09-20 真视频实测收敛, 详见现场手册):
  · 结算方式 settlement_mode=last_step (末步「模具盖板」完成即结算本周期;
    默认 first_step 下第二件要等下一件母排才结算, 录像内永远等不到 → 0 周期漏结算)
  · 下层母排: 消失等待 5s + 等待不被打断 (桥接扫码抬离 3~4s, 治重复入账/误切周期)
  · 全步骤 单次接受 accept_once (治芯子阶段框抖动重复入账→「重复步骤」误NG)
  · 模具盖板(结算步): 最少帧数 2 + 消失等待 2s 防误触发
  · 空闲超时 0 (保持现场; 周期定界交给末步结算+首步重现兜底)
  · 多码采集: 收尾码结算 + 少扫NG挂起 + 待机静默 + 扫码结算不计数(视觉已计);
    vision_gate 关 — 末步消失结算(拿件后2s)天然晚于扫收尾码, gate 只能消费到
    上一件的视觉结果, 上件 NG 会连坐本件误判 ng_vision (本 UAT 曾实测复现)

跑法 (先起隔离后端):
  TIANJUN_DATA_DIR=/tmp/uat_liuhe_data python -m uvicorn backend.main:app --port 8011
  python tests/uat/uat_20260919_liuhe_first_step_rescan.py --base http://127.0.0.1:8011 --phase both
"""
import argparse
import json
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path

import requests

ASSET_DIR = Path("/tmp/uat_liuhe")
VIDEO = str(ASSET_DIR / "liuhe_ws1.mp4")
# new 阶段用补尾版: 录像 t76.2 截断早于末步消失等待走完 (t74.5 拿件 + 2s),
# 现场相机帧流不断不存在此事; 补 8s 静止尾帧模拟画面延续, 让末步结算自然发生。
VIDEO_PADDED = str(ASSET_DIR / "liuhe_ws1_pad.mp4")
MODEL = str(ASSET_DIR / "liuhe_best9.pt")
# 直读后端落库: 必须与被测后端的 TIANJUN_DATA_DIR 一致 (可用环境变量覆盖)
DB_PATH = os.environ.get("UAT_LIUHE_DB", "/tmp/uat_liuhe/data/sql_app.db")
RUN_LOG = ASSET_DIR / "run.log"
CH = 0
VIDEO_LEN_S = 77
LABELS = ["下层母排", "芯子1", "芯子2", "芯子3", "芯子4", "芯子5", "芯子6",
          "盖母排", "模具盖板"]

# 现场同源模拟码 (匹配现场正则: 母排 ^M.{29,33}$ / 工装 ^H-C / 芯子 ^\d{13}$)
BUSBAR_CODE = "M10200519A100005036272508230057"
FIXTURE_CODE = "H-C88001"           # 循环工装码, 跨工件重复豁免
CHIP_CODES = [f"92600002450{i:02d}" for i in range(1, 7)]


def ensure_padded_video():
    if Path(VIDEO_PADDED).exists():
        return
    import cv2
    src = cv2.VideoCapture(VIDEO)
    fps = src.get(cv2.CAP_PROP_FPS)
    w, h = int(src.get(3)), int(src.get(4))
    out = cv2.VideoWriter(VIDEO_PADDED, cv2.VideoWriter_fourcc(*"mp4v"),
                          fps, (w, h))
    last = None
    while True:
        ok, f = src.read()
        if not ok:
            break
        out.write(f)
        last = f
    for _ in range(int(fps * 8)):
        out.write(last)
    out.release()

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
    pipeline = {"detection_steps": list(range(1, len(LABELS) + 1))}
    if fixed:
        # 定案: 末步完成结算 (v3.60.1 前端露出入口)。默认 first_step 下
        # 本件要等下一件母排重现才结算 → 录像内第二件永不落库。
        pipeline["settlement_mode"] = "last_step"
        pipeline["idle_timeout_seconds"] = 0   # 保持现场: 不用空闲超时定界
    return {
        "name": name,
        "task_type": "detect",
        "logic_mode": "detection",
        "pipeline_config": pipeline,
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
    """现场同源多码采集配置 (照 2026-09-19 现场截图), 仅 vision_gate 改关 (定案)。"""
    api.call("put", f"/api/v1/scan-collect/config?project_id={project_id}", json={
        "enabled": True,
        "slots": [
            {"key": "busbar", "label": "母排码", "count": 1,
             "regex": "^M.{29,33}$"},
            {"key": "fixture", "label": "工装码", "count": 1,
             "regex": "^H-C", "role": "closing",
             "dedup_cross_group": "off"},   # 循环治具跨工件重复豁免
            {"key": "chip", "label": "芯子码", "count": 6,
             "regex": "^\\d{13}$"},
        ],
        "settle_on": "closing",            # 扫收尾码(工装码)结算
        "dedup_in_group": "ng_alarm",
        "dedup_cross_group": "off",
        "on_overflow": "reject",
        "on_unmatched": "reject",
        "timeout_sec": 0,
        "event_ok_id": 1, "event_ng_id": 2,
        "ng_pending": True,                # 少扫NG挂起等补扫/人工放行
        "idle_remind_sec": 30,
        "standby_silent": True,
        "count_on_settle": False,          # 视觉周期已计数, 扫码结算只借灯/语音
        # 定案: vision_gate 关 — 末步消失结算晚于扫收尾码, gate 只能拿到
        # 上一件的视觉结果, 上件 NG 会连坐本件 ng_vision (实测复现过)
        "vision_gate": False,
    })


def inject_scan_timeline(api: Api, t0: float):
    """按录像时间线注入现场扫码流 (相对视频 t0, speed=1.0 实时对齐):
    t26  第一件收尾扫工装码 → 组1 少扫 (母排/芯子码在录像开始前) → NG 挂起
    t31  操作员按「NG放行」→ 组1 按 NG 结算 (= 业务真值的 1NG)
    t46  第二件母排码 / t50~60 芯子码×6 / t75 收尾工装码 → 组2 码齐 OK
    """
    plan = [(26.0, "scan", FIXTURE_CODE), (31.0, "resolve", None),
            (46.0, "scan", BUSBAR_CODE)]
    plan += [(50.0 + 2 * i, "scan", c) for i, c in enumerate(CHIP_CODES)]
    plan += [(75.0, "scan", FIXTURE_CODE)]
    for at, kind, code in plan:
        dt = t0 + at - time.time()
        if dt > 0:
            time.sleep(dt)
        el = round(time.time() - t0, 1)
        if kind == "scan":
            api.try_post("/api/v1/scanner/simulate",
                         json={"barcode": code, "channel_id": CH})
            log(f"t+{el:6.1f}s 注入扫码 {code}")
        else:
            api.try_post("/api/v1/scan-collect/resolve-ng",
                         json={"channel_id": CH})
            log(f"t+{el:6.1f}s 按「NG放行」(半截件少扫挂起 → NG 结算)")


def db_new_scan_groups(min_record_id: int):
    """本阶段新增的多码采集分组结算结果 (按 record id 水位过滤)。"""
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    groups, order = {}, []
    for r in con.execute(
            "select id, group_id, slot_key, code, group_result "
            "from scan_collect_records where id > ? order by id",
            (min_record_id,)):
        if r["group_id"] not in groups:
            groups[r["group_id"]] = {"result": r["group_result"], "codes": []}
            order.append(r["group_id"])
        groups[r["group_id"]]["codes"].append(f"{r['slot_key']}:{r['code'][-6:]}")
        groups[r["group_id"]]["result"] = r["group_result"]
    con.close()
    return [(g, groups[g]) for g in order]


def db_max_scan_record_id():
    con = sqlite3.connect(DB_PATH)
    try:
        row = con.execute("select coalesce(max(id),0) from scan_collect_records").fetchone()
        return row[0]
    except sqlite3.OperationalError:
        return 0
    finally:
        con.close()


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

    scan_thread = None
    scan_watermark = db_max_scan_record_id()
    if fixed:
        put_scan_collect(api, pid)
        log("多码采集配置已启用 (现场同源: 收尾码结算+少扫挂起+不计数, vision_gate关)")
        ensure_padded_video()

    # 视频 + 检测 (new 阶段用补尾版视频)
    video_path = VIDEO_PADDED if fixed else VIDEO
    api.call("post", f"/api/v1/source/video/start?channel={CH}",
             json={"file_path": video_path, "speed": 1.0})
    t_video0 = time.time()
    api.call("post", f"/api/v1/source/detection/start?channel={CH}",
             json={"session_name": f"uat_liuhe_{phase}", "models": [
                 {"name": "main", "model_path": MODEL, "conf": 0.25,
                  "iou": 0.45, "priority": 100}]})
    log("视频 + 真模型检测已启动")
    if fixed:
        scan_thread = threading.Thread(
            target=inject_scan_timeline, args=(api, t_video0), daemon=True)
        scan_thread.start()
        log("现场扫码流注入线程已启动 (收尾码×2 / NG放行 / 母排码 / 芯子码×6)")

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

    if scan_thread:
        scan_thread.join(timeout=10)
        time.sleep(4)   # 留出末步消失等待/收尾码结算余量
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
        ng_by_dup = any(not c["is_good"] and "重复" in (c["reason"] or "")
                        for c in cycles)
        check("B1 整改参数: 下层母排无重复入账", not per_cycle_dup,
              f"(各周期下层母排次数={[c['steps'].count('下层母排') for c in cycles]})")
        check("B2 整改参数: 无「重复步骤」型 NG", not ng_by_dup,
              f"(cycles={[(c['cycle'], c['is_good'], c['reason']) for c in cycles]})")
        check("B3 多码采集通道: 全部结算事件不弹未绑码", len(warned) == 0,
              f"({len(warned)}/{len(settle_events)} 条误弹)")
        # ---- 业务真值 1NG + 1OK (工程师口径: 录像半中间开始, 一个半周期) ----
        # 视觉侧: 第一件半截缺首步「下层母排」→ 检测模式不开周期 (非首步不开
        # 周期是模式既有语义), 只有第二件落库; last_step 下模具盖板完成即结算,
        # 9 步全齐 → 1 周期 OK。扫码抬离 (t46~50) 被 5s 不打断桥接, 不切周期。
        check("B4 视觉侧: 落库恰 1 周期且判 OK (第二件 9 步全齐)",
              len(cycles) == 1 and cycles[0]["is_good"]
              and set(cycles[0]["steps"]) == set(LABELS),
              f"(cycles={[(c['cycle'], c['is_good'], c['reason']) for c in cycles]})")
        # 扫码侧: 组1 半截件少扫 → 挂起 → 放行判 NG; 组2 码收齐 → OK
        scan_groups = db_new_scan_groups(scan_watermark)
        for gid, g in scan_groups:
            log(f"  扫码组 {gid[:8]} result={g['result']} codes={g['codes']}")
        results_seq = [g["result"] for _gid, g in scan_groups]
        check("B5 扫码侧: 组1 少扫NG(放行) + 组2 码齐OK = 业务真值 1NG 1OK",
              results_seq == ["ng_missing", "ok"],
              f"(实际 {results_seq}, 期望 ['ng_missing', 'ok'])")

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
