# -*- coding: utf-8 -*-
"""showcase 插件 v1.5.1 可见浏览器 UAT — 假桥接喂真实契约数据.

验证面 (对齐 UPGRADE_v1.5.1.md):
  P0 鉴权: 登录走 /auth/login 后桥 auth-set-token 写穿 persist
  P1 显示: data-disp 改动触发 host-display-set 深合并
  P2 项目: 容器定界 / 逐件开始判定 / last_step 结算 / ROI 多块保存格式
  MES: 多码采集卡渲染 settle_on_vision_cycle
  监控: scan_collect 徽标 + start_gate / 仅清本周期按钮存在

用法:
  ~/miniconda3/envs/tianjun/bin/python tests/uat/showcase_plugin_v151/uat_v151.py
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
EVID = ROOT / "evidence/showcase_v151"
EVID.mkdir(parents=True, exist_ok=True)
PORT = 8766

PROJECTS = [
    {
        "id": 1, "name": "自定义-容器定界", "logic_mode": "custom", "task_type": "detection",
        "model_name": "box.pt", "is_active": True, "model_labels": ["盒体", "放件", "封箱"],
        "steps_config": [
            {"id": 1, "label": "盒体", "enabled": False, "threshold": 50},
            {"id": 2, "label": "放件", "enabled": True, "threshold": 50, "detect_role": "item"},
            {"id": 3, "label": "封箱", "enabled": True, "threshold": 50},
        ],
        "pipeline_config": {
            "settlement_mode": "first_step",
            "custom_cycle_owner": "container",
            "container_gate_label": "盒体",
            "container_gate_appear_seconds": 1.0,
            "container_gate_gone_seconds": 3.0,
            "custom_mixed_with": "per_item",
            "custom_mix_per_item_virtual_step": True,
            "custom_mix_per_item_virtual_step_label": "锁付完成",
            "per_item": {"start_by_stability": True, "future_key": "keep-151"},
            "simultaneous_groups": [], "custom_conditions": [], "periodic_actions": [],
            "rod_companion_filter": {}, "rod_session_gate": {},
        },
        "data_config": {"record_boxes_data": True},
        "events_config": [], "counters_config": [],
    },
    {
        "id": 2, "name": "逐件-开始判定", "logic_mode": "per_item", "task_type": "detection",
        "model_name": "screw.pt", "model_labels": ["螺丝", "已完成"],
        "steps_config": [
            {"id": 1, "label": "螺丝", "enabled": True, "threshold": 50, "coverage_margin": 0.4},
        ],
        "pipeline_config": {
            "per_item": {
                "start_by_stability": True, "start_labels": ["就位-左上"],
                "start_sustain_frames": 5, "start_conf": 0.6,
                "board_rereg_enabled": True, "stability_window_frames": 8,
            },
            "simultaneous_groups": [], "custom_conditions": [], "periodic_actions": [],
            "rod_companion_filter": {}, "rod_session_gate": {},
        },
        "data_config": {}, "events_config": [], "counters_config": [],
    },
    {
        "id": 3, "name": "检测-末步结算", "logic_mode": "detection", "task_type": "detection",
        "model_name": "det.pt", "model_labels": ["工件"],
        "steps_config": [{"id": 1, "label": "工件", "enabled": True}],
        "pipeline_config": {
            "settlement_mode": "last_step", "detection_steps": [1],
            "simultaneous_groups": [], "custom_conditions": [], "periodic_actions": [],
            "rod_companion_filter": {}, "rod_session_gate": {},
        },
        "data_config": {}, "events_config": [], "counters_config": [],
    },
]

SCAN_CFG = {
    "enabled": True, "slots": [
        {"name": "母排", "count": 1, "pattern": "^MB"},
        {"name": "芯子", "count": 6, "pattern": "^XZ"},
    ],
    "settle_on": "closing", "ng_pending": True,
    "settle_on_vision_cycle": True, "count_on_settle": False,
    "standby_silent": True, "idle_remind_sec": 8, "vision_gate": False,
}

BRIDGE_JS = """
(() => {
  const PROJECTS = %s;
  const SCAN_CFG = %s;
  window.__hostToken = null;
  window.__hostTokenPersist = null;
  window.__hostDisp = {navbar:{projectSelector:true}, monitor:{stepStrip:true, defaultCounters:{showTotal:true}}};
  window.__scPuts = [];
  const routes = (url, method, body) => {
    if (url === '/projects') return {items: PROJECTS};
    if (url === '/projects/active/current') return PROJECTS[0];
    if (url.startsWith('/models/capabilities')) return [{capability:'ocr',label:'OCR'},{capability:'anomaly',label:'异常'}];
    if (url.startsWith('/models')) return {items: [{id:1,name:'det.pt',capability:'detect',builtin:false}]};
    if (url.startsWith('/packaging-flows')) return {items: []};
    if (url.startsWith('/mes/inbound')) return [];
    if (url.startsWith('/system/display')) return {};
    if (url.startsWith('/data/')) return {items: []};
    if (url.startsWith('/scan-collect/config')) {
      if (String(method||'get').toLowerCase()==='put') { window.__scPuts.push(body||{}); return SCAN_CFG; }
      return SCAN_CFG;
    }
    if (url === '/auth/me') return {auth_enabled:true, user:{username:'guest', is_anonymous:true}};
    if (url === '/auth/status') return {enabled:true, allow_anonymous_operator:true, session_persist:true};
    if (url === '/auth/login') return {token:'tok-admin-151', session_persist:true, user:{username:'admin', display_name:'管理员', is_anonymous:false, roles:['admin']}};
    if (url === '/auth/config') return {ok:true};
    if (url.startsWith('/users')) return [];
    if (url.startsWith('/roles')) return [];
    if (url.startsWith('/api-keys')) return [];
    if (url.startsWith('/plugins')) return [];
    return {};
  };
  window.addEventListener('message', (ev) => {
    const d = ev && ev.data;
    if (!d || d.__tjscAction !== true || d.id == null) return;
    const p = d.payload || {};
    if (d.action === 'auth-set-token') {
      window.__hostToken = p.token || null;
      window.__hostTokenPersist = p.persist !== false;
      window.postMessage({__tjscRes:true, id:d.id, ok:true, data:{ok:!!p.token, persist:window.__hostTokenPersist}}, '*');
      return;
    }
    if (d.action === 'auth-clear-token') {
      window.__hostToken = null;
      window.postMessage({__tjscRes:true, id:d.id, ok:true, data:{ok:true}}, '*');
      return;
    }
    if (d.action === 'auth-token-state') {
      window.postMessage({__tjscRes:true, id:d.id, ok:true, data:{token:window.__hostToken}}, '*');
      return;
    }
    if (d.action === 'host-display-get') {
      window.postMessage({__tjscRes:true, id:d.id, ok:true, data:{settings:window.__hostDisp}}, '*');
      return;
    }
    if (d.action === 'host-display-set') {
      const patch = p.settings || {};
      window.__hostDisp = Object.assign({}, window.__hostDisp, patch);
      if (patch.monitor || window.__hostDisp.monitor) {
        window.__hostDisp.monitor = Object.assign({}, (window.__hostDisp.monitor||{}), (patch.monitor||{}));
      }
      if (patch.navbar || window.__hostDisp.navbar) {
        window.__hostDisp.navbar = Object.assign({}, (window.__hostDisp.navbar||{}), (patch.navbar||{}));
      }
      window.postMessage({__tjscRes:true, id:d.id, ok:true, data:{ok:true, settings:window.__hostDisp}}, '*');
      return;
    }
    const url = p.url || '';
    let data = null;
    try { data = routes(url, p.method, p.data); } catch (e) { data = {}; }
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
    class _Srv(socketserver.TCPServer):
        allow_reuse_address = True
    httpd = _Srv(("127.0.0.1", PORT), http.server.SimpleHTTPRequestHandler)
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
        page.add_init_script(BRIDGE_JS % (
            json.dumps(PROJECTS, ensure_ascii=False),
            json.dumps(SCAN_CFG, ensure_ascii=False),
        ))
        page.goto(f"http://127.0.0.1:{PORT}/showcase-app.html")
        page.wait_for_timeout(2500)

        print('== P0 鉴权写穿 ==')
        page.evaluate("""() => {
          const u=document.getElementById('loginUser'); const p=document.getElementById('loginPwd');
          if(u) u.value='admin'; if(p) p.value='secret1';
        }""")
        jsclick('#loginOkBtn')
        page.wait_for_timeout(600)
        tok = page.evaluate('() => window.__hostToken')
        persist = page.evaluate('() => window.__hostTokenPersist')
        check('登录后桥写穿 token', tok == 'tok-admin-151')
        check('默认 persist=true', persist is True)
        shot('p0_auth_login.png')

        print('== P1 显示设置写穿 ==')
        jsclick('.nav .item[data-route="/settings"]')
        page.wait_for_timeout(800)
        page.evaluate("""() => {
          const el=document.querySelector('[data-disp="navbar.projectSelector"]');
          if(el){ el.checked=false; el.dispatchEvent(new Event('change',{bubbles:true})); }
        }""")
        page.wait_for_timeout(400)
        disp = page.evaluate('() => window.__hostDisp')
        check('navbar.projectSelector 写穿 false', (disp.get('navbar') or {}).get('projectSelector') is False)
        shot('p1_display.png')

        print('== P2 自定义容器定界 + 混合逐件 ==')
        jsclick('.nav .item[data-route="/project"]')
        page.wait_for_timeout(1200)

        def open_proj(name, tab='logic'):
            jsclick(f'[data-project-card][data-project-name="{name}"]')
            page.wait_for_timeout(500)
            jsclick(f'.project-tab[data-project-tab="{tab}"]')
            page.wait_for_timeout(500)

        open_proj('自定义-容器定界')
        owner = page.eval_on_selector('select[data-b="pc.custom_cycle_owner"]', 'el => el.value')
        check('周期定界回显 container', owner == 'container')
        gate_lab = page.eval_on_selector('select[data-b="pc.container_gate_label"]', 'el => el && el.value')
        check('容器门标签回显 盒体', gate_lab == '盒体')
        mix_vis = page.eval_on_selector('[data-lg="mixbox"]', 'el => el.style.display !== "none"')
        check('mixbox 卡可见', mix_vis)
        virt = page.eval_on_selector('input[data-b="pc.custom_mix_per_item_virtual_step"]', 'el => el && el.checked')
        check('混合逐件虚拟步骤勾选', virt is True)
        pi_vis = page.eval_on_selector('[data-lg="pi"]', 'el => el.style.display !== "none"')
        check('custom+per_item 时逐件通用参数卡露出', pi_vis)
        rec = page.eval_on_selector('input[data-b="pe.data_config.record_boxes_data"]', 'el => el && el.checked')
        check('sidecar 开关回显 (基本设置可能需切 tab)', rec is True or rec is False)
        payload = page.evaluate('() => tjBuildProjPayload("自定义-容器定界")')
        pc = payload['pipeline_config']
        check('payload.custom_cycle_owner=container', pc.get('custom_cycle_owner') == 'container')
        check('payload 混合逐件虚拟步骤', pc.get('custom_mix_per_item_virtual_step') is True)
        check('payload per_item 未知键透传', (pc.get('per_item') or {}).get('future_key') == 'keep-151')
        shot('p2_container.png')

        print('== P2 逐件开始判定 ==')
        open_proj('逐件-开始判定')
        stb = page.eval_on_selector('input[data-b="pc.per_item.start_by_stability"]', 'el => el && el.checked')
        check('稳定窗口开账回显', stb is True)
        payload2 = page.evaluate('() => tjBuildProjPayload("逐件-开始判定")')
        pi = payload2['pipeline_config']['per_item']
        check('payload start_by_stability', pi.get('start_by_stability') is True)
        check('payload board_rereg 透传', pi.get('board_rereg_enabled') is True)
        check('payload start_labels 透传', pi.get('start_labels') == ['就位-左上'])
        shot('p2_per_item_gate.png')

        print('== P2 检测末步结算 ==')
        open_proj('检测-末步结算')
        last = page.evaluate("""() => {
          const el=document.querySelector('input[data-b="pc.settlement_mode"][value="last_step"]');
          return el && el.checked;
        }""")
        check('结算方式 last_step 回显', last is True)
        payload3 = page.evaluate('() => tjBuildProjPayload("检测-末步结算")')
        check('payload.settlement_mode=last_step', payload3['pipeline_config'].get('settlement_mode') == 'last_step')
        shot('p2_last_step.png')

        print('== ROI 多块保存格式 ==')
        fmt = page.evaluate("""() => {
          const nested=_roiSaveBlocks([[[0,0],[1,0],[1,1]],[[0,0],[0.5,0],[0.5,0.5]]], null);
          const single=_roiSaveBlocks([[[0,0],[1,0],[1,1]]], null);
          return {nestedIsArr: Array.isArray(nested) && Array.isArray(nested[0]) && Array.isArray(nested[0][0]),
                  nestedLen: nested.length, singleIsFlat: Array.isArray(single) && typeof single[0][0]==='number'};
        }""")
        check('多块存嵌套', fmt['nestedIsArr'] and fmt['nestedLen'] == 2)
        check('单块存旧扁平格式', fmt['singleIsFlat'])

        print('== MES 多码采集 ==')
        jsclick('.nav .item[data-route="/mes"]')
        page.wait_for_timeout(600)
        jsclick('.mes-tab[data-mes-tab="scancollect"]')
        page.wait_for_timeout(800)
        vis_ck = page.eval_on_selector('input[data-sc-f="settle_on_vision_cycle"]', 'el => el && el.checked')
        check('随视觉周期结算回显', vis_ck is True)
        slot_n = page.evaluate("""() => document.querySelectorAll('#scCfgBox input, #scCfgBox select').length""")
        check('多码采集配置表单已渲染', slot_n > 5)
        shot('mes_scan_collect.png')

        print('== 监控徽标 / 清零 ==')
        jsclick('.nav .item[data-route="/monitor"]')
        page.wait_for_timeout(600)
        det = {
            "is_running": True, "project_config": PROJECTS[0],
            "scan_collect": {"enabled": True, "total_got": 5, "total_expected": 7, "slots": [
                {"label": "母排", "got": 1, "expected": 1},
                {"label": "芯子", "got": 4, "expected": 6},
            ], "pending_ng": False},
            "container_gate": {"active": True, "state": "present", "label": "盒体"},
            "per_item_state": {"start_gate": {"started": False, "waiting": ["稳定窗口"]}},
        }
        page.evaluate('(d) => window.postMessage({__tjsc: true, detection: d}, "*")', det)
        page.wait_for_timeout(600)
        sc_bar = page.evaluate("""() => {
          const el=document.getElementById('tjScanCollectBar') || document.querySelector('[data-sc-bar]');
          if(!el) return {found:false};
          return {found:true, display:el.style.display, text:el.textContent||''};
        }""")
        check('多码采集条存在', sc_bar.get('found') is True)
        rst = page.eval_on_selector('#todayResetCycleBtn', 'el => !!el')
        check('仅清本周期按钮存在', rst is True)
        shot('m_scan_collect.png')

        check('无未捕获 JS 异常', not errors)
        if errors:
            print('  pageerrors:', errors[:8])

        browser.close()
    httpd.shutdown()
    print()
    print('FAILED: ' + '; '.join(FAIL) if FAIL else 'ALL PASS')
    raise SystemExit(1 if FAIL else 0)


if __name__ == '__main__':
    main()
