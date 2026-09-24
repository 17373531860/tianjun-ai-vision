"""showcase v1.5.1 场景化 UAT — 真实需求配置 + 配置真的能用 (E2E 真跑周期)

不是"点一下看名字"：每个输入框敲真实业务值 → 保存 → GET 逐键比对；
然后激活项目, 用 synthetic 剧本源喂真实检测流, 在插件监控页点「▶ 开始」,
验证周期按所配参数真实结算出 OK / NG(缺步原因)。

场景:
  A 电机装配 SOP (sequential, E2E 真跑): 3 步工序中文名/逐步阈值/消失等待/
    等待不被打断/最少帧数/最短持续 → c1 全序 OK, c2 缺末步 NG(下一件首步重现结算)
  B 东莞容器包装 (custom+container owner, E2E 真跑): 容器标签到位 0.8s 开周期/
    离场 1.5s 结算, 动作无序完备 → c1 OK, c2 缺动作 NG
  C 六和逐件锁付 (per_item 深度打字): 稳定窗口开账/开始标签/整板重配准/吸收窗
  D 称重配料 (weighing pipeline 深度打字): 皮重窗/料别顺序/入秤下限
  E 六和多码采集 (深度打字): 母排^MU x1 / 芯子^XZ x6 / 工装^GZ 跨件豁免 + 随视觉结算

前置: RUNTIME_MODE=test 后端 8005 (TIANJUN_DATA_DIR=/tmp/tj_uat151_data, 干净库,
      showcase 已种) + vite 6007 + ~/Downloads/best.pt
用法: UAT_FE=http://localhost:6007 UAT_BE=http://127.0.0.1:8005/api/v1 \
      ~/miniconda3/envs/tianjun/bin/python tests/uat/showcase_plugin_v151/uat_v151_scenario.py
"""
import json
import os
import sqlite3
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[3]
EVID = ROOT / "evidence/showcase_v151/scenario"
EVID.mkdir(parents=True, exist_ok=True)
FE = os.environ.get("UAT_FE", "http://localhost:6007")
BE = os.environ.get("UAT_BE", "http://127.0.0.1:8005/api/v1")
DB = os.environ.get("UAT_DB", "/tmp/tj_uat151_data/sql_app.db")
MODEL_FILE = Path.home() / "Downloads/best.pt"
SUF = str(int(time.time()))[-5:]

RESULTS = []


def record(name, ok, note=""):
    RESULTS.append((name, bool(ok), note))
    print(("  PASS  " if ok else "  FAIL  ") + name + ((" — " + str(note)[:200]) if note else ""), flush=True)


def api(path, method="GET", body=None):
    req = urllib.request.Request(BE + path, method=method)
    req.add_header("Content-Type", "application/json")
    data = json.dumps(body).encode() if body is not None else None
    with urllib.request.urlopen(req, data=data, timeout=30) as r:
        return json.loads(r.read().decode() or "null")


def api_items(path):
    d = api(path)
    if isinstance(d, list):
        return d
    return d.get("items", []) if isinstance(d, dict) else []


def api_poll(fn, tries=12, gap=1.0):
    last = None
    for _ in range(tries):
        try:
            last = fn()
            if last:
                return last
        except Exception:
            pass
        time.sleep(gap)
    return last


def db_max_cycle_id():
    c = sqlite3.connect(DB)
    try:
        return c.execute("select coalesce(max(id),0) from detection_cycles").fetchone()[0]
    finally:
        c.close()


def db_new_cycles(baseline):
    """只取 baseline 之后的新周期 (dev 拷贝库带历史周期, 必须隔离)"""
    c = sqlite3.connect(DB)
    try:
        return c.execute(
            "select id, is_good, result_reason from detection_cycles where id > ? order by id",
            (baseline,)).fetchall()
    finally:
        c.close()


def seg(frm, to, labels):
    """labels: [(label, track_id)] 帧段"""
    dets = []
    for lbl, tid in labels:
        dets.append({"label": lbl, "confidence": 0.95,
                     "bbox": [0.2 + (tid % 5) * 0.14, 0.3, 0.12, 0.12],
                     "track_id": tid})
    return {"from": frm, "to": to, "detections": dets}


def getpath(obj, dotted):
    cur = obj
    for k in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def main():  # noqa: C901
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        ctx = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = ctx.new_page()
        page.set_default_timeout(20000)
        page.on("dialog", lambda d: d.accept())
        console_errors = []
        page.on("pageerror", lambda e: console_errors.append("PAGEERROR " + str(e)))

        def shot(name):
            try:
                page.screenshot(path=str(EVID / name), timeout=8000)
            except Exception:
                pass

        print("== 0. 打开宿主, 等插件覆盖层 ==")
        page.goto(FE)
        page.wait_for_selector("iframe", timeout=120000)
        page.wait_for_timeout(4000)
        fr = page.frame_locator("iframe").first

        def jsclick(sel):
            fr.locator(sel).first.evaluate("el => el.click()")

        def go(route, wait=1200):
            jsclick(f'.nav .item[data-route="{route}"]')
            page.wait_for_timeout(wait)

        def frev(js):
            return fr.locator("body").first.evaluate(f"() => {{ {js} }}")

        def close_dialogs():
            frev("document.querySelectorAll('dialog[open]').forEach(d=>{try{d.close()}catch(e){}}); return 0;")

        def open_proj(pid, tab="logic"):
            jsclick(f'[data-project-card][data-project-id="{pid}"]')
            page.wait_for_timeout(700)
            jsclick(f'.project-tab[data-project-tab="{tab}"]')
            page.wait_for_timeout(700)

        def save_proj(pid):
            jsclick("#projSaveBtn")
            page.wait_for_timeout(2500)
            return api(f"/projects/{pid}")

        def set_pc(key, val):
            """真实用户输入: 找 [data-b=key] 控件, 按类型 打字/勾选/选值 + change"""
            vj = json.dumps(val)
            r = frev(f"""
              var el=document.querySelector('[data-b="{key}"]');
              if(!el) return 'no-el';
              var v={vj};
              if(el.type==='checkbox'){{ if(el.checked!==!!v){{ el.checked=!!v; }} }}
              else {{ el.value=(v==null?'':String(v)); }}
              el.dispatchEvent(new Event('input',{{bubbles:true}}));
              el.dispatchEvent(new Event('change',{{bubbles:true}}));
              return 'ok';""")
            page.wait_for_timeout(350)
            return r

        def set_step(i, k, val):
            """步骤表 normal 行 i 的 data-k 控件设值 (打字/勾选)"""
            vj = json.dumps(val)
            r = frev(f"""
              var tr=document.querySelector('[data-steps-table="normal"] table tbody tr[data-si="{i}"]');
              if(!tr) return 'no-row';
              var el=tr.querySelector('[data-k="{k}"]');
              if(!el) return 'no-el';
              var v={vj};
              if(el.type==='checkbox'){{ el.checked=!!v; }}
              else {{ el.value=(v==null?'':String(v)); }}
              el.dispatchEvent(new Event('input',{{bubbles:true}}));
              el.dispatchEvent(new Event('change',{{bubbles:true}}));
              return 'ok';""")
            page.wait_for_timeout(200)
            return r

        def mkproj(name, mode):
            jsclick('[data-open-dialog="createProjectDialog"]')
            page.wait_for_timeout(400)
            fr.locator("#newProjName").fill(name)
            fr.locator("#newProjMode").select_option(mode)
            jsclick("#newProjCreateBtn")
            page.wait_for_timeout(2000)
            hit = api_poll(lambda: [p for p in api_items("/projects") if p.get("name") == name],
                           tries=8)
            return hit[0]["id"] if hit else None

        def bind_model(pid, model_id):
            open_proj(pid, tab="basic")
            jsclick("#projPickModelBtn")
            page.wait_for_timeout(1200)
            jsclick(f'[data-model-pick-id="{model_id}"]')
            page.wait_for_timeout(4000)
            return api(f"/projects/{pid}")

        def activate_ui(pid):
            go("/project", 1000)
            jsclick(f'[data-project-card][data-project-id="{pid}"]')
            page.wait_for_timeout(600)
            frev("""var b=Array.from(document.querySelectorAll('button.proto-btn'))
                 .find(x=>x.offsetParent && x.textContent.indexOf('启用当前项目')>=0);
                 if(b) b.click(); return !!b;""")
            page.wait_for_timeout(2500)
            cur = api_poll(lambda: (api("/projects/active/current") or {}).get("id") == pid and pid,
                           tries=10)
            return cur == pid

        # ============================================================
        print("== 1. 模型上传 (UI) → 拿真实标签 ==")
        model_id = None
        if not MODEL_FILE.exists():
            record("模型文件存在", False, "缺 ~/Downloads/best.pt")
            browser.close()
            raise SystemExit(1)
        go("/model", 1500)
        jsclick('[data-open-dialog="uploadModelDialog"]')
        page.wait_for_timeout(500)
        fr.locator("#modelFileInput").set_input_files(str(MODEL_FILE), timeout=60000)
        page.wait_for_timeout(400)
        fr.locator("#modelName").fill("scn-" + SUF)
        fr.locator("#modelVersion").fill("V1.0")
        jsclick("#modelUploadBtn")
        hit = api_poll(lambda: [m for m in api_items("/models") if ("scn-" + SUF) in (m.get("name") or "")],
                       tries=150, gap=1.0)
        record("模型上传入库", bool(hit))
        model_id = hit[0]["id"] if hit else None
        close_dialogs()
        if not model_id:
            browser.close()
            raise SystemExit(1)

        # ============================================================
        print("== 2. 场景A 电机装配 SOP (sequential): 真实步骤参数打字配置 ==")
        go("/project", 1200)
        pa = mkproj(f"scnA-电机装配SOP-{SUF}", "sequential")
        record("A 建项目", bool(pa))
        p = bind_model(pa, model_id)
        steps = p.get("steps_config") or []
        labels = [s.get("label") for s in steps]
        record("A 绑模型步骤重建", len(steps) >= 3, f"labels={labels[:4]}...共{len(labels)}")
        L1, L2, L3 = labels[0], labels[1], labels[2]

        # 步骤表真实编辑: 中文工序名 / 逐步阈值 / 消失等待+不被打断 / 最少帧数 / 最短持续
        jsclick('.project-tab[data-project-tab="steps"]')
        page.wait_for_timeout(800)
        plan = {
            0: {"displayLabel": "上料就位", "threshold": 62, "disappear_delay": 1.5,
                "min_frames": 2, "disappear_uninterruptible": True},
            1: {"displayLabel": "锁付作业", "threshold": 58, "min_frames": 3},
            2: {"displayLabel": "成品下线", "threshold": 66, "min_duration": 0.5},
        }
        miss = []
        for i in range(len(steps)):
            r = set_step(i, "enabled", i < 3)
            if r != "ok":
                miss.append(f"{i}.enabled={r}")
        for i, kv in plan.items():
            # 消失等待须先于"等待不被打断"设置 (解锁禁用态需重渲, 先存值)
            for k, v in kv.items():
                if k == "disappear_uninterruptible":
                    continue
                r = set_step(i, k, v)
                if r != "ok":
                    miss.append(f"{i}.{k}={r}")
        # 等待不被打断: disappear_delay>0 后该格是锁定占位, 需保存重渲后再勾
        record("A 步骤表控件齐全", not miss, str(miss[:6]))
        p = save_proj(pa)
        got = p.get("steps_config") or []

        def stepv(i, k):
            return got[i].get(k) if i < len(got) else None

        checks = [
            ("displayLabel0", stepv(0, "displayLabel") == "上料就位"),
            ("displayLabel1", stepv(1, "displayLabel") == "锁付作业"),
            ("displayLabel2", stepv(2, "displayLabel") == "成品下线"),
            ("threshold0=62", stepv(0, "threshold") in (62, 0.62)),
            ("threshold1=58", stepv(1, "threshold") in (58, 0.58)),
            ("threshold2=66", stepv(2, "threshold") in (66, 0.66)),
            ("disappear_delay0=1.5", stepv(0, "disappear_delay") == 1.5),
            ("min_frames1=3", stepv(1, "min_frames") == 3),
            ("min_duration2=0.5", stepv(2, "min_duration") == 0.5),
            ("step3+ disabled", all(s.get("enabled") is False for s in got[3:])),
            ("step0~2 enabled", all(s.get("enabled") is not False for s in got[:3])),
        ]
        bad = [n for n, ok in checks if not ok]
        record("A 步骤表 11 项真实值逐键落库", not bad, str(bad) or f"{len(checks)}键全对")
        # 保存后重渲, 消失等待已>0 → 勾"等待不被打断"再存
        jsclick('.project-tab[data-project-tab="steps"]')
        page.wait_for_timeout(600)
        r = set_step(0, "disappear_uninterruptible", True)
        p = save_proj(pa)
        record("A 等待不被打断(先配等待后解锁)落库",
               (p.get("steps_config") or [{}])[0].get("disappear_uninterruptible") is True, r)
        shot("a_steps.png")

        # ============================================================
        print("== 3. 场景A E2E: 激活 → synthetic 剧本 → 插件监控页点「▶ 开始」→ 周期按配置结算 ==")
        record("A UI 启用项目", activate_ui(pa))
        fps = 10
        # synthetic 从 start 即播帧, 而插件「▶ 开始」(set-project+resume) 要几秒;
        # 剧本整体后移 200 帧 (20s 留白) 保证开演时检测已就绪
        OF = 200
        tl = [
            # c1 全序: L1 → L2 → L3 (各 3s, 间隔 2s)
            seg(OF + 10, OF + 40, [(L1, 11)]), seg(OF + 60, OF + 90, [(L2, 12)]),
            seg(OF + 110, OF + 140, [(L3, 13)]),
            # c2 首步重现→结算 c1 OK; 本件缺 L3
            seg(OF + 170, OF + 200, [(L1, 21)]), seg(OF + 220, OF + 250, [(L2, 22)]),
            # c3 首步重现→结算 c2 NG(缺步)
            seg(OF + 300, OF + 330, [(L1, 31)]), seg(OF + 350, OF + 380, [(L2, 32)]),
            seg(OF + 400, OF + 430, [(L3, 33)]),
            seg(OF + 431, OF + 470, []),
        ]
        base_a = db_max_cycle_id()
        api("/test/synthetic/start", method="POST",
            body={"scenario_json": {"name": "scnA", "fps": fps, "timeline": tl}, "channel": 0})
        go("/monitor", 1500)
        jsclick("#detStartBtn")
        page.wait_for_timeout(4000)
        st = api_poll(lambda: (api("/source/detection/results?channel=0") or {}).get("is_detecting"),
                      tries=30, gap=1.0)
        record("A 插件「▶ 开始」真启动检测", bool(st))
        # 等 2 个新周期结算 (c2 首步 37s, c3 首步 50s; 冗余到 90s)
        api_poll(lambda: len(db_new_cycles(base_a)) >= 2, tries=95, gap=1.0)
        shot("a_monitor_running.png")
        cys = db_new_cycles(base_a)
        record("A 产生 ≥2 个真实新周期", len(cys) >= 2, f"{len(cys)}个")
        if len(cys) >= 2:
            c1, c2 = cys[0], cys[1]
            record("A c1 全序完成 → OK", c1[1] == 1, f"is_good={c1[1]} reason={c1[2]}")
            record("A c2 缺「成品下线」→ NG 且原因指明缺步",
                   c2[1] == 0 and (L3 in (c2[2] or "") or "成品下线" in (c2[2] or "")),
                   f"is_good={c2[1]} reason={c2[2]}")
        # 监控页真实显示 OK/NG 计数 (KPI 卡)
        cnt = frev("""var o=document.getElementById('kpiOk'), n=document.getElementById('kpiNg');
             return JSON.stringify({ok:o?o.textContent.trim():null, ng:n?n.textContent.trim():null});""")
        cj = json.loads(cnt or "{}")
        record("A 监控页 KPI 计数显示 OK/NG", bool(cj.get("ok")) and bool(cj.get("ng")), cnt)
        shot("a_done.png")
        jsclick("#detStopBtn")
        page.wait_for_timeout(2000)
        api("/test/synthetic/stop?channel=0", method="POST")

        # ============================================================
        print("== 4. 场景B 容器定界包装 (custom+container): 真实参数打字 + E2E ==")
        go("/project", 1200)
        pb = mkproj(f"scnB-容器包装-{SUF}", "custom")
        record("B 建项目", bool(pb))
        p = bind_model(pb, model_id)
        bl = [s.get("label") for s in (p.get("steps_config") or [])]
        C, A1, A2 = bl[3], bl[4], bl[5]
        open_proj(pb, tab="logic")
        set_pc("pc.custom_based_on", "detection")
        set_pc("pc.custom_cycle_owner", "container")
        page.wait_for_timeout(800)
        r1 = set_pc("pc.container_gate_label", C)
        r2 = set_pc("pc.container_gate_appear_seconds", 0.8)
        r3 = set_pc("pc.container_gate_gone_seconds", 1.5)
        record("B 容器门三参数控件在", r1 == r2 == r3 == "ok", f"{r1},{r2},{r3}")
        # 步骤表: 容器行禁用(不参与完备), 动作 A1/A2 启用, 其余禁用
        jsclick('.project-tab[data-project-tab="steps"]')
        page.wait_for_timeout(800)
        for i in range(len(bl)):
            set_step(i, "enabled", i in (4, 5))
        set_step(4, "displayLabel", "装入内衬")
        set_step(5, "displayLabel", "贴合格证")
        p = save_proj(pb)
        pc = p.get("pipeline_config") or {}
        okb = (pc.get("custom_cycle_owner") == "container"
               and pc.get("container_gate_label") == C
               and pc.get("container_gate_appear_seconds") == 0.8
               and pc.get("container_gate_gone_seconds") == 1.5
               and pc.get("custom_based_on") == "detection")
        record("B 容器门真实参数逐键落库", okb,
               json.dumps({k: pc.get(k) for k in ("custom_cycle_owner", "container_gate_label",
                                                  "container_gate_appear_seconds",
                                                  "container_gate_gone_seconds")}, ensure_ascii=False))
        sc = p.get("steps_config") or []
        record("B 步骤角色落库 (容器禁用/动作启用/改名)",
               sc[3].get("enabled") is False and sc[4].get("enabled") is not False
               and sc[4].get("displayLabel") == "装入内衬" and sc[5].get("displayLabel") == "贴合格证")
        shot("b_config.png")

        record("B UI 启用项目", activate_ui(pb))
        # synthetic 帧段命中即 break: 重叠段只取第一段, 剧本必须分段互斥
        tl2 = [
            # c1: 容器到位常驻, 两动作乱序完成 (先贴合格证), 容器离场 → OK
            seg(OF + 10, OF + 39, [(C, 41)]),
            seg(OF + 40, OF + 70, [(C, 41), (A2, 42)]),
            seg(OF + 71, OF + 89, [(C, 41)]),
            seg(OF + 90, OF + 120, [(C, 41), (A1, 43)]),
            seg(OF + 121, OF + 150, [(C, 41)]),
            # c2: 容器再到位, 只做 A1, 缺 A2 → NG
            seg(OF + 200, OF + 229, [(C, 51)]),
            seg(OF + 230, OF + 260, [(C, 51), (A1, 52)]),
            seg(OF + 261, OF + 320, [(C, 51)]),
            seg(OF + 321, OF + 380, []),
        ]
        base_b = db_max_cycle_id()
        api("/test/synthetic/start", method="POST",
            body={"scenario_json": {"name": "scnB", "fps": fps, "timeline": tl2}, "channel": 0})
        go("/monitor", 1200)
        jsclick("#detStartBtn")
        page.wait_for_timeout(4000)
        api_poll(lambda: len(db_new_cycles(base_b)) >= 2, tries=95, gap=1.0)
        cys = db_new_cycles(base_b)
        record("B 容器定界产生 2 个新周期", len(cys) >= 2, f"{len(cys)}个")
        if len(cys) >= 2:
            b1, b2 = cys[0], cys[1]
            record("B c1 容器离场结算 → OK (动作乱序宽容)", b1[1] == 1, f"reason={b1[2]}")
            record("B c2 缺「贴合格证」→ NG",
                   b2[1] == 0 and (A2 in (b2[2] or "") or "缺" in (b2[2] or "")),
                   f"reason={b2[2]}")
        shot("b_done.png")
        jsclick("#detStopBtn")
        page.wait_for_timeout(2000)
        api("/test/synthetic/stop?channel=0", method="POST")

        # ============================================================
        print("== 5. 场景C 六和逐件锁付 (per_item): 深度打字配置 ==")
        go("/project", 1200)
        pcid = mkproj(f"scnC-逐件锁付-{SUF}", "per_item")
        record("C 建项目", bool(pcid))
        bind_model(pcid, model_id)
        open_proj(pcid, tab="logic")
        vals = {
            "pc.per_item.start_by_stability": True,
            "pc.per_item.start_sustain_frames": 6,
            "pc.per_item.start_conf": 0.45,
            "pc.per_item.board_rereg_enabled": True,
            "pc.per_item.absorb_new_items_sec": 2.5,
        }
        rr = {k: set_pc(k, v) for k, v in vals.items()}
        page.wait_for_timeout(600)
        # start_by_stability 勾上后重渲露出开始标签输入
        r_lb = set_pc("pc.per_item.start_labels", "就位-左上, 就位-右上")
        p = save_proj(pcid)
        pi = (p.get("pipeline_config") or {}).get("per_item") or {}
        okc = (pi.get("start_by_stability") is True
               and pi.get("start_sustain_frames") == 6
               and pi.get("start_conf") == 0.45
               and pi.get("board_rereg_enabled") is True
               and pi.get("absorb_new_items_sec") == 2.5
               and pi.get("start_labels") == ["就位-左上", "就位-右上"])
        record("C 逐件开账闸门 6 项真实值逐键落库", okc,
               json.dumps(pi, ensure_ascii=False)[:180] + f" ctl={rr},{r_lb}")
        shot("c_per_item.png")

        # ============================================================
        print("== 6. 场景D 称重配料 (weighing pipeline): 深度打字配置 ==")
        go("/project", 1000)
        pd = mkproj(f"scnD-称重配料-{SUF}", "weighing")
        record("D 建项目", bool(pd))
        open_proj(pd, tab="logic")
        set_pc("pc.weighing.drive_mode", "pipeline")
        page.wait_for_timeout(800)
        dv = {
            "pc.weighing.materials": "钢帽水泥, 钢脚水泥",
            "pc.weighing.pipeline.tare_min_kg": 0.8,
            "pc.weighing.pipeline.tare_max_kg": 1.6,
            "pc.weighing.pipeline.queue_depth": 3,
            "pc.weighing.measure_min_weight": 0.05,
        }
        rd = {k: set_pc(k, v) for k, v in dv.items()}
        p = save_proj(pd)
        w = (p.get("pipeline_config") or {}).get("weighing") or {}
        pl = w.get("pipeline") or {}
        okd = (w.get("drive_mode") == "pipeline"
               and w.get("materials") == ["钢帽水泥", "钢脚水泥"]
               and pl.get("tare_min_kg") == 0.8 and pl.get("tare_max_kg") == 1.6
               and pl.get("queue_depth") == 3
               and w.get("measure_min_weight") == 0.05)
        record("D 称重流水线 6 项真实值逐键落库", okd,
               json.dumps({"drive": w.get("drive_mode"), "mats": w.get("materials"),
                           "pipeline": pl, "min_w": w.get("measure_min_weight")},
                          ensure_ascii=False)[:200] + f" ctl={rd}")
        shot("d_weighing.png")

        # ============================================================
        print("== 7. 场景E 六和多码采集: 真实正则/数量门打字配置 ==")
        go("/mes", 1200)
        jsclick('.mes-tab[data-mes-tab="scancollect"]')
        # 等异步 GET 完成把 __tjScForm 装好再操作 (监听闭包捕获该对象, 早操作会写进旧对象丢失)
        form_ready = api_poll(
            lambda: frev("""return !!(window.__tjScForm
                 && document.querySelector('#scCfgBox [data-sc-f="enabled"]'));"""),
            tries=15, gap=1.0)
        record("E 多码采集表单就绪", bool(form_ready))

        def set_scf(k, val):
            return frev(f"""var e=document.querySelector('[data-sc-f="{k}"]');
                 if(!e) return 'no-el';
                 if(e.type==='checkbox'){{ e.checked={json.dumps(bool(val))}; }}
                 else {{ e.value={json.dumps(val)}; }}
                 e.dispatchEvent(new Event('change',{{bubbles:true}})); return 'ok';""")

        def set_slot(i, k, val):
            return frev(f"""var e=document.querySelector('[data-sc-slot="{i}"][data-sk="{k}"]');
                 if(!e) return 'no-el'; e.value={json.dumps(str(val))};
                 e.dispatchEvent(new Event('input',{{bubbles:true}}));
                 e.dispatchEvent(new Event('change',{{bubbles:true}})); return 'ok';""")

        r_en = set_scf("enabled", True)
        page.wait_for_timeout(800)
        # 加满 3 个槽位
        for _ in range(3):
            n = frev("return document.querySelectorAll('[data-sc-slot][data-sk=\"label\"]').length;")
            if n >= 3:
                break
            jsclick("#scAddSlotBtn")
            page.wait_for_timeout(500)
        slots_plan = [
            ("母排码", "busbar", 1, "^MU\\d{8}$"),
            ("芯子码", "core", 6, "^XZ\\d{6}$"),
            ("工装码", "fixture", 1, "^GZ"),
        ]
        miss_e = []
        for i, (lb, key, cnt, rx) in enumerate(slots_plan):
            for k, v in (("label", lb), ("key", key), ("count", cnt), ("regex", rx)):
                r = set_slot(i, k, v)
                if r != "ok":
                    miss_e.append(f"{i}.{k}={r}")
        r_v = set_scf("settle_on_vision_cycle", True)
        r_g = set_scf("vision_gate", False)
        # 保存前确认输入已写进活表单对象 (保存读 window.__tjScForm)
        form_now = frev("return JSON.stringify(window.__tjScForm||{});")
        record("E 输入写进活表单", '"busbar"' in (form_now or "") and "MU" in (form_now or ""),
               (form_now or "")[:160])
        jsclick("#scCfgSaveBtn")
        page.wait_for_timeout(2000)
        active = api("/projects/active/current") or {}
        sc = api_poll(lambda: api(f"/scan-collect/config?project_id={active.get('id')}"), tries=6) or {}
        cfg = sc.get("config", sc) or {}
        slots = cfg.get("slots") or []

        def slot_by_key(k):
            return next((s for s in slots if s.get("key") == k), {})

        oke = (cfg.get("enabled") is True
               and cfg.get("settle_on_vision_cycle") is True
               and slot_by_key("busbar").get("regex") == "^MU\\d{8}$"
               and slot_by_key("busbar").get("count") == 1
               and slot_by_key("core").get("regex") == "^XZ\\d{6}$"
               and slot_by_key("core").get("count") == 6
               and slot_by_key("fixture").get("regex") == "^GZ")
        record("E 多码采集 3 槽位真实正则/数量门/随视觉结算逐键落库", oke,
               f"ctl={r_en}/{r_v}/{r_g} miss={miss_e[:4]} cfg=" + json.dumps(cfg, ensure_ascii=False)[:200])
        shot("e_scan.png")

        # ============================================================
        print()
        print("==== 汇总 ====")
        nfail = 0
        for n, ok, note in RESULTS:
            if not ok:
                nfail += 1
            print(("  PASS  " if ok else "  FAIL  ") + n + ((" — " + str(note)[:200]) if note else ""))
        pe = [e for e in console_errors]
        if pe:
            print("---- pageerrors ----")
            for e in pe[:8]:
                print("  ", e[:160])
        print(f"TOTAL {len(RESULTS)}  FAIL {nfail}")
        browser.close()
        raise SystemExit(1 if nfail else 0)


if __name__ == "__main__":
    main()
