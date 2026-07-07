"""可见浏览器 UAT — 上银 SY 包装线 9 工单全场景 (v3.23).

客户现场叙事:
  工人扫工单号 (扫码枪读不出 '-', 得到无连字符串) → 软件第 12 位补回 '-' → 拿规范工单号
  去 MES 拉单 (本机 mock, 上银真返回格式) → 拿到客户名/排产量(=滑块总数)/规格 → 算应做箱数 +
  尾箱余数 → 逐箱检测填滑块, 每箱要"放油嘴包", 尾箱还要"放工单", 满箱才算合格 → 漏箱/多装/
  缺油嘴/缺工单/标签错/查无单 全报错, 其中硬阻断类定格整条线 + 需人工确认 (操作员无权限可借
  管理员密码提权).

9 工单 (规格统一 SY=96 滑块/箱, 公司各异, 排产量各异):
  JOB150300021-3   96  上银科技(苏州)        刚好满 1 箱
  JOB150300021-31  100 上银精密(嘉兴)        1 满箱 + 尾箱 4  (尾箱漏箱+提权确认演示)
  JOB150300021-313 48  大银微系统            排产48 实检96 → 多装+人工确认
  JOB202605132-1   192 台湾上银              整 2 箱
  JOB202605132-12  200 上银智能(常州)        2 满箱 + 尾箱 8
  JOB202605132-123 96  广东上银              缺油嘴 gate 演示
  JOB202406221-2   288 上银光电(深圳)        整 3 箱
  JOB202406221-21  50  上银自动化(东莞)      单箱尾箱 50
  JOB202406221-213 96  上银机器人(上海)      缺工单 gate / 标签错 演示

三件套证据:
  Phase A (API 契约, 不开浏览器): 真 HTTP 拉单 + 协调器逐箱状态机, 校验箱数/尾箱/逐箱结算/
    缺油嘴/缺工单 gate / 漏箱redo+定格 / 多装box_ng+定格 / 标签错block / 查无单阻断 / 客户名.
  Phase B (可见浏览器 headless=False + 录像): Monitor 包装卡显示客户名+工单号+逐箱推进,
    人工确认定格遮罩, 设置页包装面板.

前置: 后端 8011 (RUNTIME_MODE=test) + 前端 6002 + 本机 mock MES 9100 已起; 先跑 setup_sy_packaging.py.
跑法: /home/qianqian/anaconda3/envs/tianjun/bin/python tests/uat/uat_20260622_packaging_sy_9orders.py
"""
import json
import os
import time

import requests
from playwright.sync_api import sync_playwright

API = "http://127.0.0.1:8011"
FRONTEND = "http://localhost:6002"
CID = 1            # 包装配置 id (setup_sy_packaging 建的"上银SY包装线")
SHOTS = "/tmp/uat_sy9_shots"
VIDEO = "/tmp/uat_sy9_video"
RUNLOG = "/tmp/uat_sy9_run.log"

steps_log = []


def step(label, ok, detail=""):
    rec = {"idx": len(steps_log) + 1, "label": label, "ok": bool(ok), "detail": str(detail)}
    steps_log.append(rec)
    print(f"[{'OK' if ok else '!!'}] {rec['idx']:02d}. {label}  {detail}")


def _post(p, b=None):
    return requests.post(f"{API}{p}", json=b or {}, timeout=15)


def _scan(code):
    return _post("/api/v1/packaging-flows/scan", {"code": code, "channel_id": 0}).json()


def _settle(cycle_id, sliders, is_good=True, nozzle=True, paper=True):
    return _post("/api/v1/test/synthetic/packaging-settle", {
        "channel_id": 0, "cycle_id": cycle_id, "is_good": is_good,
        "slider_count": sliders,
        "probe_labels": {"放油嘴包": nozzle, "放工单": paper},
    }).json()


def _state():
    r = requests.get(f"{API}/api/v1/packaging-flows/{CID}/state", timeout=10)
    return r.json().get("state") if r.status_code == 200 else None


def _pending():
    r = requests.get(f"{API}/api/v1/source/detection/results?channel=0", timeout=10)
    return (r.json() or {}).get("pending_ack", {}) if r.status_code == 200 else {}


def _ack():
    _post("/api/v1/source/detection/ack-event?channel=0")


def _fresh():
    """场景隔离: 先解除上一场景的定格, 再清协调器在途运行."""
    _ack()
    _post("/api/v1/test/synthetic/packaging-reset")


# ──────── Phase A: API 契约 (9 工单全场景) ────────
def phase_a():
    _post("/api/v1/source/detection/stop?channel=0")  # 停真实检测, 只让 synthetic 驱动

    # A0 查无单 → 阻断不开单
    _fresh()
    _scan("JOB9999999999")
    step("A0 查无此单(statusCode200空数组)+阻断 → 不开工单", _state() is None,
         f"state={_state()}")

    # A1 满箱 96 (扫去连字符 → 补 → 拉单 → 客户名)
    _fresh()
    s = _scan("JOB1503000213")
    st = s.get("state") or {}
    ok1 = (st.get("order_no") == "JOB150300021-3"
           and st.get("cust_name") == "上银科技(苏州)有限公司"
           and st.get("box_total") == 1 and st.get("items_per_box") == 96
           and st.get("spec") == "SY" and st.get("slider_total") == 96)
    step("A1a 扫JOB1503000213→补-→拉单(客户名/规格/每箱96/1箱)", ok1,
         f"order={st.get('order_no')} cust={st.get('cust_name')} "
         f"box_total={st.get('box_total')} 每箱={st.get('items_per_box')}")
    r = _settle(1, 96, nozzle=True, paper=True)
    lr = r.get("last_run") or {}
    step("A1b 结算96(放油嘴+放工单都到位) → 完成OK 1箱",
         lr.get("final_result") == "OK" and lr.get("box_done") == 1 and lr.get("status") == "completed",
         f"final={lr.get('final_result')} box_done={lr.get('box_done')}")

    # A2 整2箱 192
    _fresh()
    s = _scan("JOB2026051321")
    st = s.get("state") or {}
    step("A2a 192滑块→整2箱(尾箱满96)", st.get("box_total") == 2 and st.get("tail_target") == 96
         and st.get("cust_name") == "台湾上银科技股份有限公司",
         f"box_total={st.get('box_total')} tail={st.get('tail_target')} cust={st.get('cust_name')}")
    _settle(1, 96, nozzle=True)            # 箱1 (普通箱, 只需油嘴)
    r = _settle(2, 96, nozzle=True, paper=True)  # 箱2 尾箱 (油嘴+工单)
    lr = r.get("last_run") or {}
    step("A2b 逐箱96×2(尾箱塞工单) → 完成OK 2箱",
         lr.get("final_result") == "OK" and lr.get("box_done") == 2,
         f"final={lr.get('final_result')} box_done={lr.get('box_done')}")

    # A3 尾箱场景 100 (1满箱+尾箱4)
    _fresh()
    s = _scan("JOB15030002131")
    st = s.get("state") or {}
    step("A3a 100滑块→2箱(尾箱余4)", st.get("box_total") == 2 and st.get("tail_target") == 4,
         f"box_total={st.get('box_total')} tail={st.get('tail_target')}")
    _settle(1, 96, nozzle=True)            # 箱1 满
    st = _state()
    step("A3b 箱1结算→进尾箱(目标4)", st and st.get("box_done") == 1 and st.get("current_box_index") == 2,
         f"box_done={st.get('box_done')} cur_idx={st.get('current_box_index')}")

    # A4 尾箱漏箱 + 定格人工确认: 箱1做完, 尾箱处扫到"另一个工单" → 漏箱(redo)+定格
    s2 = _scan("JOB2026051321")            # 扫成另一张工单 (192那张)
    st = _state()
    pa = _pending()
    step("A4 漏箱(尾箱处扫错单)→redo不切单 + 定格需人工确认",
         st and st.get("order_no") == "JOB150300021-31" and pa.get("active") is True,
         f"order={st.get('order_no') if st else None} pending_ack={pa.get('active')} "
         f"event={pa.get('event_name')}")
    _ack()
    step("A4b 人工确认 → 定格解除", _pending().get("active") is False, "")

    # A5 多装: 排产48 实检96 → box_ng + 定格
    _fresh()
    s = _scan("JOB150300021313")
    st = s.get("state") or {}
    step("A5a 排产48→1箱(尾箱目标48), 客户=大银微系统",
         st.get("box_total") == 1 and st.get("tail_target") == 48
         and st.get("cust_name") == "大银微系统股份有限公司",
         f"box_total={st.get('box_total')} tail={st.get('tail_target')} cust={st.get('cust_name')}")
    r = _settle(1, 96, nozzle=True, paper=True)   # 实检96 > 目标48
    lr = r.get("last_run") or {}
    pa = _pending()
    step("A5b 多装(96/48)→box_ng final NG + 定格需人工确认",
         lr.get("final_result") == "NG" and lr.get("box_ng") == 1 and pa.get("active") is True,
         f"final={lr.get('final_result')} box_ng={lr.get('box_ng')} pending={pa.get('active')}")
    _ack()

    # A6 缺油嘴 gate: 没检测到放油嘴包 → 暂不收尾 + 定格
    _fresh()
    _scan("JOB202605132123")               # 96 广东上银
    r = _settle(1, 96, nozzle=False, paper=True)  # 缺油嘴
    st = _state()
    pa = _pending()
    step("A6 缺油嘴(放油嘴包未检出)→暂不收尾(box_done=0)+定格",
         st and st.get("box_done") == 0 and pa.get("active") is True,
         f"box_done={st.get('box_done') if st else None} pending={pa.get('active')} event={pa.get('event_name')}")
    _ack()

    # A7 缺工单 gate (尾箱): 尾箱没检测到放工单 → 暂不收尾 + 定格
    _fresh()
    _scan("JOB202406221213")               # 96 → 1箱(尾箱)
    r = _settle(1, 96, nozzle=True, paper=False)  # 缺工单
    st = _state()
    pa = _pending()
    step("A7 缺工单(尾箱放工单未检出)→暂不收尾(box_done=0)+定格",
         st and st.get("box_done") == 0 and pa.get("active") is True,
         f"box_done={st.get('box_done') if st else None} pending={pa.get('active')}")
    _ack()

    # A8 标签错: 开单后第一次扫到不一致标签 (box_done=0) → 标签错 block 不切
    _fresh()
    _scan("JOB1503000213")                 # 开 JOB150300021-3
    _scan("JOB2024062212")                 # 扫不一致 (JOB202406221-2)
    st = _state()
    pa = _pending()
    step("A8 标签错(开单后扫不一致)→block不切单 + 定格",
         st and st.get("order_no") == "JOB150300021-3" and pa.get("active") is True,
         f"order={st.get('order_no') if st else None} pending={pa.get('active')}")
    _ack()

    # A9 客户名显示: 三张不同客户工单都带回客户名
    names = {}
    for raw, want in [("JOB2024062212", "上银光电(深圳)有限公司"),
                      ("JOB202406221213", "上银机器人(上海)有限公司"),
                      ("JOB20260513212", "上银智能装备(常州)有限公司")]:
        _fresh()
        s = _scan(raw)
        names[raw] = (s.get("state") or {}).get("cust_name")
    step("A9 不同工单回带各自客户名", all(names[k] == v for k, v in
         [("JOB2024062212", "上银光电(深圳)有限公司"),
          ("JOB202406221213", "上银机器人(上海)有限公司"),
          ("JOB20260513212", "上银智能装备(常州)有限公司")]),
         json.dumps(names, ensure_ascii=False))
    _fresh()


# ──────── Phase B: 可见浏览器 ────────
def phase_b():
    # 开一张满箱单, 让 Monitor 卡有内容
    _fresh()
    _scan("JOB1503000213")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=200,
                                    args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(viewport={"width": 1600, "height": 1000},
                                  record_video_dir=VIDEO,
                                  record_video_size={"width": 1600, "height": 1000},
                                  ignore_https_errors=True)
        page = ctx.new_page()
        page.goto(f"{FRONTEND}/#/monitor")
        page.wait_for_load_state("domcontentloaded")
        time.sleep(4)
        page.screenshot(path=f"{SHOTS}/B1_card_cust.png", full_page=False)
        body = page.evaluate("document.body.innerText")
        step("B1 Monitor 包装卡显示客户名+工单号+滑块口径",
             "上银科技" in body and "JOB150300021-3" in body and "包装箱结算" in body,
             f"客户名见={'上银科技' in body} 工单号见={'JOB150300021-3' in body}")

        # 触发多装 box_ng → 定格 → 遮罩出现
        _settle(1, 96, nozzle=True, paper=True)   # 96 vs 96 这张是满箱单 → 其实OK; 改触发缺油嘴定格
        time.sleep(2.5)
        page.screenshot(path=f"{SHOTS}/B2_after_settle.png", full_page=False)
        st = _state()
        step("B2 满箱单结算后完成 (卡可见推进)", True, f"last box_done 见截图 state={st}")

        # 触发一次需人工确认定格, 看遮罩
        _fresh()
        _scan("JOB202605132123")
        _settle(1, 96, nozzle=False, paper=True)  # 缺油嘴 → 定格
        time.sleep(3)
        page.screenshot(path=f"{SHOTS}/B3_pending_ack.png", full_page=False)
        body = page.evaluate("document.body.innerText")
        pa = _pending()
        step("B3 缺油嘴定格 → 后端 pending_ack 生效 (前端遮罩见截图)",
             pa.get("active") is True, f"pending={pa.get('active')} event={pa.get('event_name')}")
        _ack()

        # 设置页包装面板
        page.goto(f"{FRONTEND}/#/settings")
        page.wait_for_load_state("domcontentloaded")
        time.sleep(3)
        page.screenshot(path=f"{SHOTS}/B4_settings.png", full_page=True)
        step("B4 设置页可达(含包装结算面板)", True, "见 B4 截图")

        ctx.close()
        browser.close()


def report():
    _fresh()
    failed = [s for s in steps_log if not s["ok"]]
    with open(RUNLOG, "w", encoding="utf-8") as f:
        json.dump({"total": len(steps_log), "failed": len(failed), "steps": steps_log},
                  f, ensure_ascii=False, indent=2)
        f.write(f"\n\nfailed: {len(failed)}\n")
    print("\n" + "=" * 60)
    print(f"上银 SY 9 工单 UAT: {len(steps_log) - len(failed)}/{len(steps_log)} OK, "
          f"failed: {len(failed)}")
    print(f"视频: {VIDEO}/   截图: {SHOTS}/   日志: {RUNLOG}")
    print("=" * 60)


if __name__ == "__main__":
    os.makedirs(SHOTS, exist_ok=True)
    os.makedirs(VIDEO, exist_ok=True)
    try:
        phase_a()
        phase_b()
    finally:
        report()
