# -*- coding: utf-8 -*-
"""UAT 批量: v33 空槽模型 + 槽位完整性门 跑遍上银包装线全部录像 (NG + OK)。

背景: 新模型加训「凹槽」类 — 盘的物理槽位固定 24, 一帧里 货数+空槽数 恒等于 24,
不等于 24 说明这帧没看全 (手挡住 / 盘半出画 / 重复框), 该帧观察不采信。
本轮要回答三件事:
  1. 两条有标准答案的录像 (7-23 全合格 / 7-27 全短装) 开门后判定是否仍正确
  2. 开门 vs 关门 的逐盘账面差异 (证明门有用且不误伤)
  3. 其余录像逐盘记账数与「看全率」, 供现场调参参考

跑法: 真后端 8001 + 真前端 6001 + 可见浏览器 (headless=False, 全程录像)。
证据: /home/qianqian/uat_sy8_groove_batch/ (run.log + 每条截图 + 汇总 md/json)
"""
import json
import os
import sys
import time
from pathlib import Path

import requests

API = "http://localhost:8001"
WEB = "http://localhost:6001"
OUT = Path("/home/qianqian/uat_sy8_groove_batch")
CH = 0
PROJECT_ID = int(os.environ.get("UAT_PID", 42))  # 42=SY9 现场定版
MODEL = ("/home/qianqian/桌面/word/tianjun-main/backend/uploads/models/"
         "packing_v33_groove_20260805.pt")
# 托盘专用副模型: 主模型 (v31/v33 同) 在 7-23 前 40s 一个托盘都检不出 (实测),
# 全靠这个 0.98 置信度的专检模型撑住托盘识别 — 不是冗余, 摘掉整箱记 0 盘
TRAY_AUX = ("/home/qianqian/桌面/word/tianjun-main/backend/uploads/models/"
            "77cd8782355d461ba496aa94fa12ce8a_sy_v10_last.pt")

HOME = "/home/qianqian/"
WX = ("/home/qianqian/文档/xwechat_files/wxid_9j6tgdyqgpon22_030a/"
      "msg/file/2026-07/")

OLD_MAIN = ("/home/qianqian/桌面/word/tianjun-main/backend/uploads/models/"
            "740fae093b184e149e749b386965d015_sy8_packing_v31.pt")

# 第二轮 A/B: 槽位门已证实有害 (丢盘 + 短装箱误判合格), 全部关门;
# 只比新旧主模型, 判断能不能把 v33 换上现场。
CASES_AB = [
    ("AB_7-23_v31", WX + "2026-07-23 15-43-27.mkv", 265, False, "4箱满装96",
     4, 0, OLD_MAIN),
    ("AB_7-23_v33", WX + "2026-07-23 15-43-27.mkv", 265, False, "4箱满装96",
     4, 0, None),
    ("AB_7-27_v31", HOME + "2026-07-27 16-16-34.mkv", 340, False, "4箱均短装",
     0, 4, OLD_MAIN),
    ("AB_7-27_v33", HOME + "2026-07-27 16-16-34.mkv", 340, False, "4箱均短装",
     0, 4, None),
]

# 第三轮: 整改后确认 (放油嘴包最少帧数 3→2, 凹槽步骤停用, 槽位门关, 主模型 v33)
CASES_FIX = [
    ("FIX_7-23_v33", WX + "2026-07-23 15-43-27.mkv", 265, False, "4箱满装96",
     4, 0, None),
    ("FIX_7-27_v33", HOME + "2026-07-27 16-16-34.mkv", 340, False, "4箱均短装",
     0, 4, None),
]

# 第四轮: SY9 现场定版项目确认 (去重项改可配后零差异复核)
CASES_SY9 = [
    ("SY9_7-23", WX + "2026-07-23 15-43-27.mkv", 265, False, "4箱满装96",
     4, 0, None),
    ("SY9_7-27", HOME + "2026-07-27 16-16-34.mkv", 340, False, "4箱均短装",
     0, 4, None),
]

# (用例名, 视频, 秒长, 槽位门开?, 期望说明, 期望OK, 期望NG)  期望为 None = 探索性
CASES = [
    ("7-23合格_门关", WX + "2026-07-23 15-43-27.mkv", 265, False, "4箱满装96", 4, 0),
    ("7-23合格_门开", WX + "2026-07-23 15-43-27.mkv", 265, True, "4箱满装96", 4, 0),
    ("7-27短装_门关", HOME + "2026-07-27 16-16-34.mkv", 340, False, "4箱均短装", 0, 4),
    ("7-27短装_门开", HOME + "2026-07-27 16-16-34.mkv", 340, True, "4箱均短装", 0, 4),
    ("7-30_1_门开", HOME + "2026-07-30 14-01-12.mkv", 80, True, "探索", None, None),
    ("7-30_2_门开", HOME + "2026-07-30 14-03-43.mkv", 57, True, "探索", None, None),
    ("7-30_3_门开", HOME + "2026-07-30 14-05-34.mkv", 62, True, "探索", None, None),
    ("7-28_门开", HOME + "2026-07-28 14-53-54.mkv", 59, True, "探索", None, None),
    ("7-21_1_门开", HOME + "2026-07-21 13-49-06.mkv", 149, True, "探索", None, None),
    ("7-21_2_门开", HOME + "2026-07-21 13-51-37.mkv", 78, True, "探索", None, None),
    ("7-21_3_门开", HOME + "2026-07-21 13-53-33.mkv", 93, True, "探索", None, None),
    ("7-21_4_门开", HOME + "2026-07-21 13-55-39.mkv", 105, True, "探索", None, None),
    ("7-13_1_门开", HOME + "2026-07-13 14-33-33.mkv", 105, True, "探索", None, None),
    ("7-13_3_门开", HOME + "2026-07-13 14-42-55.mkv", 55, True, "探索", None, None),
    ("7-16正确流程_门开", HOME + "2026-07-16_数据集正确流程_3周期紧凑版.mp4", 213,
     True, "探索", None, None),
    ("7-13_2_门开", HOME + "2026-07-13 14-36-23.mkv", 318, True, "探索", None, None),
    ("7-16长录_门开", HOME + "2026-07-16 13-33-56.mp4", 693, True, "探索", None, None),
]

_log_fh = None
_t0 = None


def log(msg):
    pos = f"{time.time() - _t0:6.1f}s" if _t0 else "  --  "
    line = f"[{time.strftime('%H:%M:%S')}][{pos}] {msg}"
    print(line, flush=True)
    if _log_fh:
        _log_fh.write(line + "\n")
        _log_fh.flush()


def api(method, path, **kw):
    r = getattr(requests, method)(f"{API}{path}", timeout=20, **kw)
    r.raise_for_status()
    return r.json()


def results():
    try:
        r = requests.get(
            f"{API}/api/v1/source/detection/results?channel={CH}", timeout=10)
        return r.json() if r.status_code == 200 else {}
    except Exception:
        return {}


def push_project(gate_on):
    """把 SY8 推给运行时, 按需临时摘掉槽位门 (只改内存副本, 不动库)。"""
    p = api("get", f"/api/v1/projects/{PROJECT_ID}")
    log(f"运行项目: {p['name']}(id={p['id']})")
    pc = dict(p.get("pipeline_config") or {})
    if not gate_on:
        pc["custom_mix_container_slot_check_label"] = ""
    api("post", f"/api/v1/source/detection/set-project?channel={CH}", json={
        "project_id": p["id"], "name": p["name"], "task_type": p["task_type"],
        "logic_mode": p["logic_mode"],
        "steps_config": p.get("steps_config") or [],
        "pipeline_config": pc,
        "events_config": p.get("events_config") or [],
        "counters_config": p.get("counters_config") or [],
        "data_config": p.get("data_config") or {},
    })
    return pc


def stop_all():
    for ep in ("/api/v1/source/detection/stop", "/api/v1/source/video/stop"):
        try:
            requests.post(f"{API}{ep}?channel={CH}", timeout=15)
        except Exception:
            pass
    time.sleep(1.5)


def run_case(page, name, video, dur, gate_on, expect_desc, exp_ok, exp_ng,
             main_model=None):
    case_dir = OUT / name
    case_dir.mkdir(parents=True, exist_ok=True)
    log(f"===== 用例 {name} | 槽位门={'开' if gate_on else '关'} | "
        f"视频 {Path(video).name} ({dur}s) | 期望 {expect_desc} =====")
    if not Path(video).exists():
        log(f"!! 视频不存在, 跳过: {video}")
        return {"case": name, "skipped": "视频不存在"}

    stop_all()
    push_project(gate_on)
    api("post", f"/api/v1/source/video/start?channel={CH}",
        json={"file_path": video, "speed": 1.0})
    api("post", f"/api/v1/source/detection/start?channel={CH}",
        json={"session_name": f"uat_groove_{name}", "models": [
            {"name": "main", "model_path": main_model or MODEL, "priority": 100},
            {"name": "tray_aux", "model_path": TRAY_AUX, "conf": 0.25,
             "iou": 0.45, "class_filter": ["托盘"], "priority": 50,
             "use_half": True,
             "schedule": {"type": "every_frame", "n": 1, "events": []}},
        ]})
    t_start = time.time()
    base = results().get("counters") or {}
    log(f"基线 counters={json.dumps(base, ensure_ascii=False)}")

    slot_ok_n = slot_bad_n = 0
    fps_samples = []
    tray_books = []          # 逐盘记账 (done_detail 增量)
    box_snapshots = []       # 每次箱账清零前的整箱账
    acks = []
    last_done_len = 0
    last_booked = -1
    shot_n = 0
    deadline = t_start + dur + 40
    while time.time() < deadline:
        st = results()
        if (st.get("fps") or 0) > 0:
            fps_samples.append(float(st["fps"]))
        cont = (st.get("custom_mix_state") or {}).get("container") or {}
        sv = cont.get("slot_view")
        if sv:
            if sv.get("ok"):
                slot_ok_n += 1
            else:
                slot_bad_n += 1
        done = cont.get("done_detail") or []
        if len(done) > last_done_len:
            for t in done[last_done_len:]:
                tray_books.append(t)
                log(f"  盘进箱: {json.dumps(t, ensure_ascii=False)} "
                    f"(本箱第 {len(done)} 盘)")
            last_done_len = len(done)
        elif len(done) < last_done_len:
            log(f"  箱结算, 账面清零 (上箱 {last_done_len} 盘)")
            last_done_len = len(done)
        booked = sum((cont.get("item_total_done") or {}).values())
        if booked != last_booked:
            if last_booked > booked and last_booked > 0:
                box_snapshots.append(last_booked)
            last_booked = booked
        pa = st.get("pending_ack") or {}
        if pa.get("active"):
            shot_n += 1
            shot = case_dir / f"{shot_n:02d}_ack_{pa.get('event_name', '')}.png"
            try:
                page.screenshot(path=str(shot), full_page=True)
            except Exception:
                pass
            log(f"  人工确认弹出: event={pa.get('event_name')} "
                f"reason={pa.get('reason')!r} → 代点确认")
            time.sleep(2.5)
            try:
                r = requests.post(
                    f"{API}/api/v1/source/detection/ack-event?channel={CH}",
                    timeout=10)
                acks.append({"t": round(time.time() - t_start, 1),
                             "event": pa.get("event_name"),
                             "reason": pa.get("reason")})
            except Exception as e:
                log(f"  确认失败: {e}")
        time.sleep(0.5)

    final = results()
    c = final.get("counters") or {}
    cont = (final.get("custom_mix_state") or {}).get("container") or {}
    if last_booked > 0:
        box_snapshots.append(last_booked)
    ok = c.get("合格总数", 0) - base.get("合格总数", 0)
    ng = c.get("不良总数", 0) - base.get("不良总数", 0)
    try:
        page.screenshot(path=str(case_dir / "99_final.png"), full_page=True)
    except Exception:
        pass
    stop_all()

    tot_sv = slot_ok_n + slot_bad_n
    rate = (slot_ok_n / tot_sv * 100) if tot_sv else 0.0
    res = {
        "case": name, "video": Path(video).name, "gate": gate_on,
        "expect": expect_desc, "ok": ok, "ng": ng,
        "exp_ok": exp_ok, "exp_ng": exp_ng,
        "tray_books": tray_books, "box_totals": box_snapshots,
        "slot_ok_samples": slot_ok_n, "slot_bad_samples": slot_bad_n,
        "slot_ok_rate": round(rate, 1), "acks": acks,
        "model": Path(main_model or MODEL).name,
        "fps_avg": round(sum(fps_samples) / len(fps_samples), 1) if fps_samples else 0,
        "fps_min": round(min(fps_samples), 1) if fps_samples else 0,
    }
    if exp_ok is not None:
        res["verdict"] = "PASS" if (ok == exp_ok and ng == exp_ng) else "FAIL"
    log(f"  结果: 合格+{ok} 不良+{ng} 逐盘={json.dumps(tray_books, ensure_ascii=False)} "
        f"整箱={box_snapshots} 看全率={rate:.0f}% ({slot_ok_n}/{tot_sv}) "
        f"确认{len(acks)}次 帧率均{res['fps_avg']}/最低{res['fps_min']} "
        f"{res.get('verdict', '')}")
    return res


def write_summary(all_res):
    (OUT / "summary.json").write_text(
        json.dumps(all_res, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = ["# SY8 空槽模型 + 槽位门 全录像 UAT 汇总", "",
             "项目 SY8(41) · 真后端 8001 + 前端 6001 · 主模型 + 托盘副模型",
             "", "| 用例 | 视频 | 主模型 | 槽位门 | 合格 | 不良 | 期望 | 判定 | 逐盘记账 | 整箱 | 看全率 | 帧率 |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in all_res:
        if r.get("skipped"):
            lines.append(f"| {r['case']} | - | - | - | - | - | - | 跳过 | - | - | - | - |")
            continue
        books = " / ".join(
            str(sum(t.values())) for t in r["tray_books"]) or "-"
        lines.append(
            f"| {r['case']} | {r['video']} | {r.get('model', '')} | "
            f"{'开' if r['gate'] else '关'} | "
            f"{r['ok']} | {r['ng']} | {r['expect']} | {r.get('verdict', '探索')} | "
            f"{books} | {r.get('box_totals')} | {r['slot_ok_rate']}% | "
            f"{r.get('fps_avg', 0)} |")
    (OUT / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    global _log_fh, _t0
    OUT.mkdir(parents=True, exist_ok=True)
    _log_fh = open(OUT / "run.log", "a", encoding="utf-8")
    _t0 = time.time()
    argv = sys.argv[1:]
    cases = CASES
    if argv and argv[0] == "ab":
        cases, argv = CASES_AB, argv[1:]
    elif argv and argv[0] == "fix":
        cases, argv = CASES_FIX, argv[1:]
    elif argv and argv[0] == "sy9":
        cases, argv = CASES_SY9, argv[1:]
    only = argv or None

    from playwright.sync_api import sync_playwright
    all_res = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        # 浏览器录像很吃 GPU, 实测会把推理帧率瞬时压到 1~5 帧 → 结果抖动;
        # 需要人眼录像证据时传 UAT_REC=1 打开
        kw = {"viewport": {"width": 1600, "height": 900}}
        if os.environ.get("UAT_REC") == "1":
            kw["record_video_dir"] = str(OUT / "browser_video")
        ctx = browser.new_context(**kw)
        page = ctx.new_page()
        page.goto(f"{WEB}/#/monitor", wait_until="domcontentloaded", timeout=30000)
        time.sleep(4)
        for case in cases:
            (name, video, dur, gate, desc, eo, en) = case[:7]
            main_model = case[7] if len(case) > 7 else None
            if only and not any(k in name for k in only):
                continue
            try:
                all_res.append(
                    run_case(page, name, video, dur, gate, desc, eo, en,
                             main_model))
            except Exception as e:
                log(f"!! 用例 {name} 异常: {type(e).__name__}: {e}")
                all_res.append({"case": name, "skipped": f"异常 {e}"})
            write_summary(all_res)
        ctx.close()
        browser.close()

    write_summary(all_res)
    fails = [r for r in all_res if r.get("verdict") == "FAIL"]
    log(f"全部完成: {len(all_res)} 例, 有标准答案的失败 {len(fails)} 例")
    _log_fh.close()
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
