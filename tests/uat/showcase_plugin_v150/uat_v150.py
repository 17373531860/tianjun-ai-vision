# -*- coding: utf-8 -*-
"""showcase 插件 v1.5.0 可见浏览器 UAT — 假桥接喂真实契约数据, 驱动单页真实链路.

验证面 (对齐 UPGRADE_v1.5.0.md):
  项目页: ngh 统一卡(legacy 合成) / combo 判定表 / mixbox 装箱清点 / 齐件即结算 /
          称重 pipeline 卡 / 自定义班次 / 保存 payload (ng_handling 落新块+legacy 删除,
          per_item 展开式保留未知键, combo/班次净化)
  监控页: ack 弹窗 pkg_hold 双键 + reason / 恢复扫码 chip / combo 判型 chip / 融合数值条

用法 (可见浏览器 + 截图到 evidence/showcase_v150/):
  ~/miniconda3/envs/tianjun/bin/python tests/uat/showcase_plugin_v150/uat_v150.py
"""
import http.server
import json
import os
import socketserver
import threading
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[3]
DIST = ROOT / "plugins-examples/tianjun-showcase/frontend/dist"
EVID = ROOT / "evidence/showcase_v150"
EVID.mkdir(parents=True, exist_ok=True)
PORT = 8765

# ---------- 假桥接: 独立打开时 parent===window, 自答 __tjscAction 消息 ----------
PROJECTS = [
    {
        "id": 1, "name": "顺序-NG统一模型", "logic_mode": "sequential", "task_type": "detection",
        "model_name": "demo.pt", "is_active": True, "model_labels": ["拿件", "装配", "封箱"],
        "steps_config": [
            {"id": 1, "label": "拿件", "enabled": True, "threshold": 50},
            {"id": 2, "label": "装配", "enabled": True, "threshold": 50},
            {"id": 3, "label": "封箱", "enabled": True, "threshold": 50},
        ],
        # legacy NG 键 → 验证 _peNormalize 合成 ng_handling
        "pipeline_config": {
            "settlement_mode": "first_step", "instant_ng_on_violation": True,
            "closing_guard": {"gate_enabled": True, "gate_steps": ["封箱"], "hold_enabled": True,
                              "hold_timeout_s": 90, "event_id": 2},
            "ng_remediation": {"enabled": True, "allow_step": True, "allow_count": False},
            "per_item": {"future_key_from_v360": "keep-me", "stability_window_frames": 10},
            "sequence_order": [{"step_id": 1}, {"step_id": 2}, {"step_id": 3}],
            "simultaneous_groups": [], "custom_conditions": [], "periodic_actions": [],
            "rod_companion_filter": {}, "rod_session_gate": {},
        },
        "data_config": {"shift_split_enabled": True, "day_shift_start": "08:00",
                        "night_shift_start": "20:00",
                        "shifts": [{"name": "早班", "start": "07:00"},
                                   {"name": "中班", "start": "15:00"},
                                   {"name": "夜班", "start": "23:00"}]},
        "events_config": [], "counters_config": [],
    },
    {
        "id": 2, "name": "检测-判型表", "logic_mode": "detection", "task_type": "detection",
        "model_name": "combo.pt", "model_labels": ["大孔", "小孔"],
        "steps_config": [{"id": 1, "label": "大孔", "enabled": True},
                         {"id": 2, "label": "小孔", "enabled": True}],
        "pipeline_config": {
            "detection_steps": [1, 2],
            "combo_table": {"enabled": True, "labels": ["大孔", "小孔"],
                            "rows": [{"counts": [4, 2], "verdict": "OK", "tag": "机型A"},
                                     {"counts": [2, 2], "verdict": "NG", "tag": "残次"}],
                            "count_mode": "positional",
                            "step_guard": {"enabled": True, "action": "hint"}},
            "simultaneous_groups": [], "custom_conditions": [], "periodic_actions": [],
            "rod_companion_filter": {}, "rod_session_gate": {},
        },
        "data_config": {}, "events_config": [], "counters_config": [],
    },
    {
        "id": 3, "name": "自定义-装箱清点", "logic_mode": "custom", "task_type": "detection",
        "model_name": "mix.pt", "model_labels": ["滑块", "托盘", "放托盘", "空槽"],
        "steps_config": [
            {"id": 1, "label": "滑块", "enabled": True, "detect_role": "item", "expected_count": 24},
            {"id": 2, "label": "托盘", "enabled": True},
            {"id": 3, "label": "放托盘", "enabled": True},
        ],
        "pipeline_config": {
            "custom_mixed_with": "tracking",
            "custom_mix_container_label": "托盘",
            "custom_mix_container_peak_cap": 24,
            "custom_mix_container_stable_min_frames": 5,
            "custom_mix_container_slot_check_label": "空槽",
            "custom_mix_container_slot_total": 24,
            "simultaneous_groups": [], "custom_conditions": [], "periodic_actions": [],
            "rod_companion_filter": {}, "rod_session_gate": {},
        },
        "data_config": {}, "events_config": [], "counters_config": [],
    },
    {
        "id": 4, "name": "跟踪-齐件即结算", "logic_mode": "tracking", "task_type": "detection",
        "model_name": "trk.pt", "model_labels": ["物品", "料箱"],
        "steps_config": [{"id": 1, "label": "物品", "enabled": True, "settle_confirm_frames": 4}],
        "pipeline_config": {
            "tracking_cycle_strategy": "roi_exit", "tracking_settle_on_complete": True,
            "counting_expected_items": {"物品": 6},
            "simultaneous_groups": [], "custom_conditions": [], "periodic_actions": [],
            "rod_companion_filter": {}, "rod_session_gate": {},
        },
        "data_config": {}, "events_config": [], "counters_config": [],
    },
    {
        "id": 5, "name": "称重-流水线", "logic_mode": "weighing", "task_type": "detection",
        "model_name": "wg.pt", "model_labels": ["工件上秤", "加水泥", "加钢脚水泥"],
        "steps_config": [],
        "pipeline_config": {
            "weighing": {"drive_mode": "pipeline", "operator_from_users": True,
                         "pipeline": {"material": "钢帽水泥", "tare_min_kg": 0.2},
                         "timing": {"net_stable_ms": 1500}},
            "simultaneous_groups": [], "custom_conditions": [], "periodic_actions": [],
            "rod_companion_filter": {}, "rod_session_gate": {},
        },
        "data_config": {}, "events_config": [], "counters_config": [],
    },
]

BRIDGE_JS = """
(() => {
  const PROJECTS = %s;
  const routes = (url) => {
    if (url === '/projects') return {items: PROJECTS};
    if (url === '/projects/active/current') return PROJECTS[0];
    if (url.startsWith('/models')) return {items: []};
    if (url.startsWith('/packaging-flows')) return {items: []};
    if (url.startsWith('/mes/inbound')) return [];
    if (url.startsWith('/system/display')) return {};
    if (url.startsWith('/data/')) return {items: []};
    return {};
  };
  window.addEventListener('message', (ev) => {
    const d = ev && ev.data;
    if (!d || d.__tjscAction !== true || d.id == null) return;
    const url = (d.payload && d.payload.url) || '';
    let data = null;
    try { data = routes(url); } catch (e) { data = {}; }
    window.postMessage({__tjscRes: true, id: d.id, ok: true, data}, '*');
  });
})();
"""

FAIL = []


def check(name, cond):
    print(('  PASS  ' if cond else '  FAIL  ') + name)
    if not cond:
        FAIL.append(name)


def main():
    os.chdir(DIST)
    httpd = socketserver.TCPServer(("127.0.0.1", PORT), http.server.SimpleHTTPRequestHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        jsclick = lambda sel: page.eval_on_selector(sel, "el => el.click()")

        cdp = page.context.new_cdp_session(page)

        def shot(name):
            try:
                import base64
                data = cdp.send('Page.captureScreenshot', {'format': 'png'})['data']
                (EVID / name).write_bytes(base64.b64decode(data))
            except Exception as e:
                print("  WARN 截图失败", name, e)
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.add_init_script(BRIDGE_JS % json.dumps(PROJECTS, ensure_ascii=False))
        page.goto(f"http://127.0.0.1:{PORT}/showcase-app.html")
        page.wait_for_timeout(2500)

        # ---------- 项目页 ----------
        jsclick('.nav .item[data-route="/project"]')
        page.wait_for_timeout(1200)

        def open_proj(name, tab='logic'):
            jsclick(f'[data-project-card][data-project-name="{name}"]')
            page.wait_for_timeout(400)
            jsclick(f'.project-tab[data-project-tab="{tab}"]')
            page.wait_for_timeout(400)

        print('== P1 顺序项目: ngh 统一卡 (legacy 合成) + 班次 ==')
        open_proj('顺序-NG统一模型')
        ngh_vis = page.eval_on_selector('[data-lg="ngh"]', 'el => el.style.display !== "none"')
        check('ngh 卡可见 (sequential)', ngh_vis)
        vio = page.eval_on_selector('[data-lg="ngh"] select[data-b="pc.ng_handling.violation"]', 'el => el.value')
        check('legacy instant_ng_on_violation 合成 violation=instant_ng', vio == 'instant_ng')
        ms = page.eval_on_selector('[data-lg="ngh"] select[data-b="pc.ng_handling.missing_step"]', 'el => el.value')
        check('legacy closing_guard.hold_enabled 合成 missing_step=hold', ms == 'hold')
        gate_on = page.eval_on_selector('[data-lg="ngh"] input[data-b="pc.ng_handling.gate_enabled"]', 'el => el.checked')
        check('legacy gate_enabled 合成勾选', gate_on)
        shot('p1_ngh_card.png')
        # 保存 payload 验证
        payload = page.evaluate('() => tjBuildProjPayload("顺序-NG统一模型")')
        pc = payload['pipeline_config']
        check('payload.ng_handling.violation=instant_ng', pc['ng_handling']['violation'] == 'instant_ng')
        check('payload.ng_handling.short_count=ng (allow_count=false)', pc['ng_handling']['short_count'] == 'ng')
        check('legacy instant_ng_on_violation 已删', 'instant_ng_on_violation' not in pc)
        check('legacy closing_guard 已删', 'closing_guard' not in pc)
        check('legacy ng_remediation 已删', 'ng_remediation' not in pc)
        check('per_item 未知键透传 (非 per_item 模式)', pc['per_item'].get('future_key_from_v360') == 'keep-me')
        check('自定义班次 3 条落 data_config.shifts', len(payload['data_config']['shifts']) == 3)
        shifts_ui = page.eval_on_selector_all('#projShiftList input[data-b$=".name"]', 'els => els.map(e => e.value)')
        check('基本设置班次列表渲染 3 行', shifts_ui == ['早班', '中班', '夜班'])
        jsclick('.project-tab[data-project-tab="basic"]')
        page.wait_for_timeout(300)
        shot('p1_shifts.png')

        print('== P2 检测项目: combo 判定表 ==')
        open_proj('检测-判型表')
        combo_rows = page.eval_on_selector_all('[data-lg="combo"] tbody tr', 'els => els.length')
        check('combo 表渲染 2 行', combo_rows == 2)
        shot('p2_combo.png')
        payload2 = page.evaluate('() => tjBuildProjPayload("检测-判型表")')
        ct = payload2['pipeline_config']['combo_table']
        check('combo 保存 counts 对齐', ct['rows'][0]['counts'] == [4, 2] and ct['rows'][1]['verdict'] == 'NG')
        check('combo v3.49 二期键 step_guard 保留', ct.get('step_guard', {}).get('enabled') is True)
        # 加一行再删掉 (结构操作)
        jsclick('[data-lga="comboRowAdd"]')
        page.wait_for_timeout(300)
        rows3 = page.eval_on_selector_all('[data-lg="combo"] tbody tr', 'els => els.length')
        check('comboRowAdd 生效', rows3 == 3)
        jsclick('[data-lg="combo"] tbody tr:last-child [data-lga="comboRowDel"]')
        page.wait_for_timeout(300)

        print('== P3 自定义项目: mixbox 装箱清点 ==')
        open_proj('自定义-装箱清点')
        mix_vis = page.eval_on_selector('[data-lg="mixbox"]', 'el => el.style.display !== "none"')
        check('mixbox 卡可见 (custom)', mix_vis)
        item_ck = page.eval_on_selector('[data-lg="mixbox"] input[data-mix-item="0"]', 'el => el.checked')
        check('物品行 detect_role=item 勾选回显', item_ck)
        slot = page.eval_on_selector('[data-lg="mixbox"] input[data-b="pc.custom_mix_container_slot_total"]', 'el => el.value')
        check('槽位总数回显 24', slot == '24')
        shot('p3_mixbox.png')

        print('== P4 跟踪项目: 齐件即结算 + 确认放入帧列 ==')
        open_proj('跟踪-齐件即结算')
        soc = page.eval_on_selector('input[data-b="pc.tracking_settle_on_complete"]', 'el => el.checked')
        check('齐件即结算开关回显', soc)
        shot('p4_tracking.png')
        jsclick('.project-tab[data-project-tab="steps"]')
        page.wait_for_timeout(300)
        scf = page.eval_on_selector('[data-steps-table="tracking"] input[data-k="settle_confirm_frames"]', 'el => el.value')
        check('步骤表确认放入帧=4', scf == '4')
        payload4 = page.evaluate('() => tjBuildProjPayload("跟踪-齐件即结算")')
        check('payload.tracking_settle_on_complete=true', payload4['pipeline_config']['tracking_settle_on_complete'] is True)
        check('payload steps settle_confirm_frames=4', payload4['steps_config'][0]['settle_confirm_frames'] == 4)

        print('== P5 称重项目: pipeline 卡 ==')
        open_proj('称重-流水线')
        dm = page.eval_on_selector('select[data-b="pc.weighing.drive_mode"]', 'el => el.value')
        check('驱动模式回显 pipeline', dm == 'pipeline')
        pipe_card = page.eval_on_selector_all('input[data-b="pc.weighing.pipeline.tare_min_kg"]', 'els => els.length')
        check('pipeline 参数卡渲染', pipe_card == 1)
        opu = page.eval_on_selector('input[data-b="pc.weighing.operator_from_users"]', 'el => el.checked')
        check('operator_from_users 回显', opu)
        shot('p5_weighing_pipeline.png')

        # ---------- 监控页浮层 ----------
        print('== M1 监控页: ack pkg_hold / 恢复扫码 / 判型 chip / 融合数值条 ==')
        jsclick('.nav .item[data-route="/monitor"]')
        page.wait_for_timeout(800)
        det = {
            "is_running": True, "project_config": PROJECTS[0],
            "pending_ack": {"active": True, "event_name": "不良(NG)", "started_at": time.time() - 12,
                            "timeout_sec": 0, "reason": "第 3 箱少装 22/24", "keeps_cycle": False,
                            "pkg_hold": {"box": 3, "sliders": 22, "target": 24, "is_tail": False}},
            "mes": {"scanner_resume_blocked": True},
            "combo_verdict": {"enabled": True, "last_tag": "机型A", "positional_counts": {"大孔": 4, "小孔": 2}},
        }
        page.evaluate('(d) => window.postMessage({__tjsc: true, detection: d}, "*")', det)
        page.wait_for_timeout(600)
        ack_shown = page.eval_on_selector('#tjAckLayer', 'el => el.classList.contains("show")')
        check('ack 阻塞层弹出', ack_shown)
        btns = page.eval_on_selector_all('#ackBtns [data-ack]', 'els => els.map(e => e.textContent)')
        check('pkg_hold 双键 (认NG落账/重做)', len(btns) == 2 and '认 NG 落账' in btns[0])
        reason_txt = page.eval_on_selector('#ackReason', 'el => el.textContent')
        check('reason 固化显示', '少装 22/24' in reason_txt and '重做不记 NG 箱' in reason_txt)
        resume_shown = page.eval_on_selector('#tjScanResume', 'el => el.classList.contains("show")')
        check('恢复扫码 chip 显示', resume_shown)
        combo_chip = page.eval_on_selector('#tjComboTag', 'el => el.textContent')
        check('判型 chip 内容', '机型A' in combo_chip and '大孔×4' in combo_chip)
        shot('m1_ack_pkg_hold.png')

        # 融合数值条: 喂 step_gate 项目帧 + 直接调 renderWgLiveBar (轮询走真 API 这里断言渲染函数)
        det2 = {"is_running": True,
                "project_config": {"logic_mode": "sequential",
                                    "pipeline_config": {"weighing": {"drive_mode": "step_gate"}}}}
        page.evaluate('(d) => window.postMessage({__tjsc: true, detection: d}, "*")', det2)
        page.wait_for_timeout(400)
        fusion = page.evaluate('() => window.__tjWgFusion === true')
        check('融合模式旗标置位 (show_monitor_weights 默认开)', fusion)

        # 无阻塞帧 → 全部浮层收回 (不误显示)
        page.evaluate('() => window.postMessage({__tjsc: true, detection: {is_running: true, mes: {}, project_config: {}}}, "*")')
        page.wait_for_timeout(400)
        ack_gone = page.eval_on_selector('#tjAckLayer', 'el => !el.classList.contains("show")')
        resume_gone = page.eval_on_selector('#tjScanResume', 'el => !el.classList.contains("show")')
        combo_gone = page.eval_on_selector('#tjComboTag', 'el => !el.classList.contains("show")')
        check('浮层随空帧全部收回', ack_gone and resume_gone and combo_gone)
        shot('m2_overlays_clear.png')

        check('无未捕获 JS 异常', not errors)
        if errors:
            print('  pageerrors:', errors[:5])

        browser.close()
    httpd.shutdown()
    print()
    print('FAILED: ' + '; '.join(FAIL) if FAIL else 'ALL PASS')
    raise SystemExit(1 if FAIL else 0)


if __name__ == '__main__':
    main()
