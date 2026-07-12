# -*- coding: utf-8 -*-
"""可见浏览器 UAT: 展会全应用插件 v1.4.0 全量对齐主程序 v3.33~v3.35。

现场叙事: 展会讲解员打开装了 v1.4.0 插件的主程序 → 项目页区域事件规则卡出现
秒基确认/位移门槛/锚点/结算判定等全字段并可保存回读; 逻辑面板多出「同标签区域
拆分」块; 逐件参数多出重复打防护与换板兜底且落库; 步骤表多出「等待不被打断 /
外设门控」两列; 事件配置勾了人工确认后出现「断点补做」; 称重块出现前置选择
有效期与视觉料源防错; 监控页挂上重复打黄条骨架; MES 网关新建连接可切数据库
直写并隐去 HTTP 字段; 包装结算表出现「条码/收尾」编辑面板。

前置: main 栈 — 后端 8001 + 前端 6001 已启动; plugins/showcase 已同步 v1.4.0
并 dev-activate。测试项目走 API 建、跑完删, 不污染现场配置。
"""
from __future__ import annotations

import sys
import time
import uuid

import requests
from playwright.sync_api import sync_playwright

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from _common import UatRun, launch_browser, filter_console_errors  # noqa: E402

API = "http://127.0.0.1:8001/api/v1"
FRONT = "http://localhost:6001"

run = UatRun("showcase_v140_full_align")
pid = None
conn_id = None


def _plugin_frame(page, timeout_s=60):
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        try:
            h = page.query_selector("iframe[src*='showcase-app.html']")
            fr = h.content_frame() if h else None
            if fr and fr.evaluate("typeof renderRoutePage==='function' && typeof tjSelectProject==='function'"):
                return fr
        except Exception:
            pass
        time.sleep(1)
    return None


def _set(fr, selector, value):
    """给 data-b 输入设值并派发 change (走插件绑定链路, 非直改模型)。"""
    fr.evaluate(
        """([sel, v]) => { const el=document.querySelector(sel); if(!el) return false;
             if(el.type==='checkbox'){ el.checked=!!v; } else { el.value=String(v); }
             el.dispatchEvent(new Event('change',{bubbles:true})); return true; }""",
        [selector, value])


try:
    # ---- 0. 后端真值: 激活清单 = v1.4.0 ----
    m = requests.get(f"{API}/plugins/active/manifest", timeout=5).json()
    run.step("激活插件清单 = showcase v1.4.0",
             m.get("customer_code") == "showcase" and m.get("plugin_version") == "1.4.0",
             f"got {m.get('customer_code')} {m.get('plugin_version')}")

    # ---- 建测试项目 (region_events + 三步骤标签) ----
    name = f"uat_v140_{uuid.uuid4().hex[:6]}"
    r = requests.post(f"{API}/projects", json={
        "name": name, "task_type": "detection", "logic_mode": "region_events"}, timeout=5)
    r.raise_for_status()
    pid = r.json()["id"]
    requests.put(f"{API}/projects/{pid}", json={"steps_config": [
        {"id": 1, "label": "工件", "displayLabel": "工件", "enabled": True, "threshold": 50},
        {"id": 2, "label": "测硬度笔", "displayLabel": "测硬度笔", "enabled": True, "threshold": 50},
        {"id": 3, "label": "扫码枪", "displayLabel": "扫码枪", "enabled": True, "threshold": 50},
    ]}, timeout=5).raise_for_status()
    run.step("测试项目已建", True, f"pid={pid} {name}")

    with sync_playwright() as p:
        browser, ctx, page, cerrs = launch_browser(p, record_video_dir=run.video_dir)
        page.goto(FRONT, wait_until="domcontentloaded")
        fr = _plugin_frame(page)
        run.step("插件整页 iframe 已加载", fr is not None)
        if fr is None:
            raise RuntimeError("插件 iframe 未加载")
        time.sleep(3)

        # ---- A. 监控页: v3.33 重复打黄条骨架 ----
        run.step("监控页重复打黄条骨架 #piDupBar",
                 fr.evaluate("!!document.getElementById('piDupBar')"))
        run.shot(page, "01_monitor")

        # ---- B. 项目页: 选中测试项目 ----
        fr.evaluate("renderRoutePage('/project')")
        time.sleep(2)
        fr.evaluate("tjLoadProjects()")
        time.sleep(2)
        fr.evaluate(f"tjSelectProject({pid})")
        time.sleep(2)
        ok_sel = fr.evaluate(f"window.__tjSelectedPid==={pid} && !!window.__tjPE")
        run.step("测试项目已选中进编辑模型", ok_sel)

        # B1. 区域事件规则卡全字段
        fr.evaluate("document.querySelector('[data-lga=\"reAdd\"]').click()")
        time.sleep(1)
        fields = fr.evaluate("""(() => {
          const b=document.querySelector('[data-lg="regionEvents"] [data-lg-body]');
          const q=(s)=>!!b.querySelector(s);
          return {
            min_seconds:q('[data-b$=".min_seconds"]'), min_move:q('[data-b$=".min_move"]'),
            min_overlap:q('[data-b$=".min_overlap_ratio"]'), min_iou:q('[data-b$=".min_iou"]'),
            gone_seconds:q('[data-b$=".gone_seconds"]'), require_label:q('[data-b$=".require_label"]'),
            region_mode:q('[data-b$=".region_mode"]'), event_id:q('[data-b$=".event_id"]'),
            anchor:q('[data-b$=".anchor.enabled"]'), sr_add:q('[data-lga="reSRAdd"]'),
            class_conf:q('[data-b^="pc.region_events.class_conf."]'),
          };
        })()""")
        missing = [k for k, v in fields.items() if not v]
        run.step("区域规则卡 v3.32/34 全字段渲染", not missing,
                 f"缺失={missing}" if missing else "11/11")
        run.shot(page, "02_region_rule_full")

        # B2. 填秒基/位移/主体 + 结算判定, 保存落库回读
        _set(fr, '[data-b$=".subject_label"]', "测硬度笔")
        _set(fr, '[data-b$=".object_label"]', "工件")
        _set(fr, '[data-b$=".min_seconds"]', "0.3")
        _set(fr, '[data-b$=".min_move"]', "0.015")
        fr.evaluate("document.querySelector('[data-lga=\"reSRAdd\"]').click()")
        time.sleep(0.8)
        _set(fr, '[data-b="pc.region_events.settlement_rules.0.match"]', "always")
        time.sleep(0.5)
        fr.evaluate("document.getElementById('projSaveBtn').click()")
        time.sleep(3)
        after = requests.get(f"{API}/projects/{pid}", timeout=5).json()
        re_cfg = (after.get("pipeline_config") or {}).get("region_events") or {}
        rule0 = (re_cfg.get("rules") or [{}])[0]
        srs = re_cfg.get("settlement_rules") or []
        ok_db = (abs(rule0.get("min_seconds", 0) - 0.3) < 1e-6
                 and abs(rule0.get("min_move", 0) - 0.015) < 1e-6
                 and len(srs) == 1 and srs[0].get("match") == "always"
                 and srs[0].get("event_id") is not None)
        run.step("区域规则秒基/位移/结算判定落库回读", ok_db,
                 f"min_seconds={rule0.get('min_seconds')} min_move={rule0.get('min_move')} srs={srs}")

        # B3. 同标签区域拆分块 + 规则编辑与虚拟步骤同步
        has_ls = fr.evaluate("!!document.querySelector('[data-lg=\"labelSplit\"]')")
        run.step("同标签区域拆分块已挂载", has_ls)
        # 切顺序模式让拆分块可见并走保存链路
        fr.evaluate("setLogicMode('sequential')")
        time.sleep(1)
        fr.evaluate("document.querySelector('[data-lga=\"lsAdd\"]').click()")
        time.sleep(1)
        _set(fr, '[data-b="pc.label_splits.0.source_label"]', "工件")
        _set(fr, '[data-b="pc.label_splits.0.regions"]',
             '[{"name":"左位","polygon":[[0.1,0.1],[0.4,0.1],[0.4,0.5],[0.1,0.5]],"color":"#f97316"},'
             '{"name":"右位","polygon":[[0.6,0.1],[0.9,0.1],[0.9,0.5],[0.6,0.5]],"color":"#22d3ee"}]')
        # 多轮次两防护 (v3.34)
        _set(fr, '[data-b="pc.label_splits.0.rounds.enabled"]', True)
        time.sleep(0.8)
        _set(fr, '[data-b="pc.label_splits.0.rounds.trigger_label"]', "扫码枪")
        _set(fr, '[data-b="pc.label_splits.0.rounds.trigger_min_seconds"]', "0.7")
        _set(fr, '[data-b="pc.label_splits.0.rounds.trigger_conf"]', "0.4")
        run.shot(page, "03_label_split")
        fr.evaluate("document.getElementById('projSaveBtn').click()")
        time.sleep(3)
        after = requests.get(f"{API}/projects/{pid}", timeout=5).json()
        ls = ((after.get("pipeline_config") or {}).get("label_splits") or [{}])[0]
        rounds = ls.get("rounds") or {}
        vsteps = [s.get("label") for s in (after.get("steps_config") or [])
                  if s.get("split_origin")]
        exp_names = {"前罩左位", "前罩右位", "后罩左位", "后罩右位"}
        ok_ls = (ls.get("source_label") == "工件"
                 and abs(rounds.get("trigger_min_seconds", 0) - 0.7) < 1e-6
                 and abs(rounds.get("trigger_conf", 0) - 0.4) < 1e-6
                 and exp_names.issubset(set(vsteps)))
        run.step("拆分规则+两防护落库, 虚拟步骤同步", ok_ls,
                 f"rounds={rounds.get('trigger_min_seconds')}/{rounds.get('trigger_conf')} vsteps={vsteps}")

        # B4. 步骤表两新列 (顺序模式下渲染外设门控三态)
        fr.evaluate(f"tjSelectProject({pid})")  # 重新拉取, 刷步骤表
        time.sleep(2)
        thead = fr.evaluate(
            "document.querySelector('[data-steps-table=\"normal\"] thead').innerText")
        gate_sel = fr.evaluate(
            "document.querySelectorAll('[data-steps-table=\"normal\"] [data-k=\"__gate_kind\"]').length")
        run.step("步骤表两新列 + 门控三态选择器",
                 ("等待不被打断" in thead) and ("外设门控" in thead) and gate_sel > 0,
                 f"gate_cells={gate_sel}")
        run.shot(page, "04_steps_two_cols")

        # B5. 外设门控启用 → device_gate 落库 + weighing 注入 step_gate
        fr.evaluate("""(() => {
          const el=document.querySelector('[data-steps-table="normal"] [data-k="__gate_kind"]');
          el.value='tare'; el.dispatchEvent(new Event('change',{bubbles:true})); })()""")
        fr.evaluate("document.getElementById('projSaveBtn').click()")
        time.sleep(3)
        after = requests.get(f"{API}/projects/{pid}", timeout=5).json()
        g0 = next((s.get("device_gate") for s in after.get("steps_config") or []
                   if s.get("device_gate")), None)
        wcfg = (after.get("pipeline_config") or {}).get("weighing") or {}
        run.step("外设门控落库 + 融合模式注入",
                 g0 and g0.get("enabled") and g0.get("kind") == "tare"
                 and wcfg.get("drive_mode") == "step_gate",
                 f"gate={g0} drive_mode={wcfg.get('drive_mode')}")

        # B6. 称重块: 融合提示 + 前置选择有效期 + 视觉料源防错
        fr.evaluate("LGRender('weighing')")
        time.sleep(0.5)
        wtxt = fr.evaluate(
            "document.querySelector('[data-lg=\"weighing\"] [data-lg-body]').innerText")
        run.step("称重块 v3.35 三块齐 (融合/有效期/料源防错)",
                 ("融合模式" in wtxt) and ("前置选择有效期" in wtxt) and ("视觉料源防错" in wtxt))
        wvis = fr.evaluate(
            "getComputedStyle(document.querySelector('[data-lg=\"weighing\"]')).display !== 'none'")
        run.step("融合模式下称重块在顺序模式也可见", wvis)
        run.shot(page, "05_weighing_fusion")

        # B7. 逐件: 重复打防护 + 换板兜底 (切 per_item 模式)
        fr.evaluate("setLogicMode('per_item')")
        time.sleep(1)
        has_dup_ck = fr.evaluate(
            "!!document.querySelector('[data-b=\"pc.per_item.duplicate_screw_alarm\"]')")
        has_absent = fr.evaluate(
            "!!document.querySelector('[data-b=\"pc.per_item.workpiece_absent_settle_frames\"]')")
        run.step("逐件 v3.33 两块渲染 (重复打开关+换板兜底)", has_dup_ck and has_absent)
        _set(fr, '[data-b="pc.per_item.duplicate_screw_alarm"]', True)
        time.sleep(0.8)
        n_dup_fields = fr.evaluate(
            "document.querySelectorAll('[data-b^=\"pc.per_item.duplicate_\"]').length")
        run.step("重复打开关展开 4 参数", n_dup_fields >= 5, f"n={n_dup_fields}")
        _set(fr, '[data-b="pc.per_item.workpiece_absent_settle_frames"]', "20")
        fr.evaluate("document.getElementById('projSaveBtn').click()")
        time.sleep(3)
        after = requests.get(f"{API}/projects/{pid}", timeout=5).json()
        pi = (after.get("pipeline_config") or {}).get("per_item") or {}
        run.step("逐件重复打+换板兜底落库",
                 pi.get("duplicate_screw_alarm") is True
                 and pi.get("workpiece_absent_settle_frames") == 20
                 and pi.get("duplicate_release_frames") == 8,
                 f"per_item={ {k: v for k, v in pi.items() if 'dup' in k or 'absent' in k} }")
        run.shot(page, "06_per_item_dup")

        # B8. 事件配置: require_ack → ack_keep_cycle 出现并落库
        _set(fr, '[data-b="ev.0.require_ack"]', True)
        time.sleep(0.8)
        has_keep = fr.evaluate(
            "!!document.querySelector('[data-b=\"ev.0.ack_keep_cycle\"]')")
        run.step("勾人工确认后出现「断点补做」勾选", has_keep)
        _set(fr, '[data-b="ev.0.ack_keep_cycle"]', True)
        fr.evaluate("document.getElementById('projSaveBtn').click()")
        time.sleep(3)
        after = requests.get(f"{API}/projects/{pid}", timeout=5).json()
        ev0 = (after.get("events_config") or [{}])[0]
        run.step("ack_keep_cycle 落库",
                 ev0.get("require_ack") is True and ev0.get("ack_keep_cycle") is True,
                 f"ev0={ {k: ev0.get(k) for k in ('require_ack', 'ack_keep_cycle')} }")

        # ---- C. MES 页: 网关数据库直写适配器 ----
        fr.evaluate("renderRoutePage('/mes')")
        time.sleep(3)
        fr.evaluate("document.getElementById('connDialog').showModal()")
        time.sleep(0.5)
        fr.evaluate("""(() => {
          const s=document.getElementById('connAdapterSel');
          s.value='database'; s.dispatchEvent(new Event('change',{bubbles:true})); })()""")
        time.sleep(0.5)
        vis = fr.evaluate("""(() => {
          const dbShown=[...document.querySelectorAll('#connDialog .conn-db')]
            .every(x=>x.style.display!=='none');
          const httpHidden=[...document.querySelectorAll('#connDialog .conn-http')]
            .every(x=>x.style.display==='none');
          return {dbShown, httpHidden}; })()""")
        run.step("切 database 后 DB 字段显 / HTTP 字段隐",
                 vis["dbShown"] and vis["httpHidden"], str(vis))
        run.shot(page, "07_gateway_db")
        # 填库连接并保存 → 后端回读 config 键名
        fr.evaluate("""(() => {
          document.querySelector('#connDialog .form-grid input.proto-input').value='UAT_数据库直写';
          document.getElementById('connDbHost').value='192.168.9.9';
          document.getElementById('connDbPort').value='3307';
          document.getElementById('connDbUser').value='mes';
          document.getElementById('connDbPwd').value='secret';
          document.getElementById('connDbName').value='mes_db';
          document.getElementById('connDbTable').value='push_records'; })()""")
        fr.evaluate(
            "document.querySelector('#connDialog button.proto-btn.ok').click()")
        time.sleep(3)
        conns = requests.get(f"{API}/mes/gateway/connections", timeout=5).json()
        conns = conns if isinstance(conns, list) else conns.get("items", [])
        mine = [c for c in conns if c.get("name") == "UAT_数据库直写"]
        cfg = (mine[0].get("config") or {}) if mine else {}
        ok_conn = (mine and mine[0].get("adapter_type") == "database"
                   and cfg.get("db_type") == "mysql" and cfg.get("host") == "192.168.9.9"
                   and cfg.get("db_port") == 3307 and cfg.get("table") == "push_records")
        if mine:
            conn_id = mine[0].get("id")
        run.step("数据库直写连接落库 (adapter/config 键名对齐)", ok_conn,
                 f"cfg={ {k: cfg.get(k) for k in ('db_type', 'host', 'db_port', 'table')} }")

        # ---- D. MES 页: 包装 v3.35 编辑面板 (无流时验 UI 骨架) ----
        fr.evaluate("setMesTab('packaging')")
        time.sleep(3)
        pk_txt = fr.evaluate("document.getElementById('pkCfgBox').innerText")
        if "条码/收尾" in pk_txt:
            fr.evaluate("document.querySelector('[data-pk-edit]').click()")
            time.sleep(1)
            ed_txt = fr.evaluate("document.getElementById('pkEditBox').innerText")
            run.step("包装编辑面板三块齐 (复合条码/识别规则/尾箱收尾)",
                     ("复合条码取段" in ed_txt) and ("识别规则" in ed_txt) and ("放工单=收尾动作" in ed_txt))
            run.shot(page, "08_packaging_edit")
        else:
            run.step("包装编辑入口 (现场无流, 验表头列)", "复合条码" in pk_txt or "暂无包装结算流" in pk_txt,
                     pk_txt[:60])

        # ---- E. 控制台无逻辑报错 ----
        real = filter_console_errors(cerrs)
        run.step("控制台无前端逻辑报错", not real, f"真报错={real[:3]}")
        run.shot(page, "09_final")
        ctx.close()
        browser.close()
except Exception as e:  # noqa: BLE001
    run.step("XX 脚本异常中断", False, f"{type(e).__name__}: {e}")
finally:
    # 清理: 测试项目 + 测试连接
    try:
        if conn_id is not None:
            requests.delete(f"{API}/mes/gateway/connections/{conn_id}", timeout=5)
        if pid is not None:
            requests.delete(f"{API}/projects/{pid}", timeout=5)
    except Exception:
        pass

raise SystemExit(run.finish())
