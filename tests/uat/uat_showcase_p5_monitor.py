# -*- coding: utf-8 -*-
"""
UAT 阶段5: 监控页真数据化 — 项目下拉接真/切换激活、班次配置、平均节拍文案、
运行指标雷达五维、3D 绑状态、随机预警池删除验证。
运行: python tests/uat/uat_showcase_p5_monitor.py
"""
import sys, time, json
import urllib.request
from playwright.sync_api import sync_playwright

FRONT = "http://localhost:6001"
API = "http://localhost:8001/api/v1"
results = []
console_errors = []


def log(m):
    print(m, flush=True)


def api_get(path):
    with urllib.request.urlopen(API + path, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(viewport={"width": 1680, "height": 950})
    page = ctx.new_page()
    page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: console_errors.append("pageerror: " + str(e)))
    page.goto(FRONT, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_selector("iframe[src*='showcase-app']", timeout=120000, state="attached")
    frame = None
    deadline = time.time() + 90
    while time.time() < deadline:
        el = page.query_selector("iframe[src*='showcase-app']")
        f = el.content_frame() if el else None
        if f:
            try:
                if f.evaluate("typeof renderRoutePage === 'function' && typeof tjLoadStripProjects === 'function'"):
                    frame = f
                    break
            except Exception:
                pass
        time.sleep(1.5)
    if not frame:
        log("FAIL: 插件 iframe 未就绪")
        sys.exit(1)
    log("OK: iframe 就绪")
    time.sleep(3)  # 等首批真值帧 + tjLoadStripProjects(800ms 定时)

    def check(name, expr):
        try:
            ok = bool(frame.evaluate(expr))
        except Exception as e:
            ok = False
            log("  exception: " + str(e)[:200])
        results.append((name, ok))
        log(("PASS  " if ok else "FAIL  ") + name)

    # ---------- 1. 顶栏项目下拉接真 ----------
    projects = api_get("/projects").get("items", [])
    active = None
    try:
        active = api_get("/projects/active/current")
    except Exception:
        pass
    check("项目下拉已替换为真项目列表", "document.getElementById('stripProj').__tjReal === true")
    check(
        "下拉选项数等于真项目数",
        f"document.getElementById('stripProj').options.length === {len(projects)}",
    )
    if active and active.get("id"):
        check(
            "下拉选中项 = 激活项目",
            f"document.getElementById('stripProj').value === '{active['id']}'",
        )

    # ---------- 2. 切换激活: 选另一个项目并验证后端激活态 ----------
    other = None
    if active:
        for pj in projects:
            if pj["id"] != active.get("id"):
                other = pj
                break
    if other:
        frame.evaluate(
            f"""(() => {{
              const sel = document.getElementById('stripProj');
              sel.value = '{other['id']}';
              sel.dispatchEvent(new Event('change'));
            }})()"""
        )
        time.sleep(2.5)
        now_active = api_get("/projects/active/current")
        results.append(("下拉切换真实激活项目", now_active.get("id") == other["id"]))
        log(("PASS  " if results[-1][1] else "FAIL  ") + "下拉切换真实激活项目")
        # 还原原激活项目
        frame.evaluate(f"tjApi.post('/projects/{active['id']}/activate')")
        time.sleep(1.5)
        restored = api_get("/projects/active/current")
        results.append(("激活项目已还原", restored.get("id") == active.get("id")))
        log(("PASS  " if results[-1][1] else "FAIL  ") + "激活项目已还原")
    else:
        log("SKIP  仅一个项目, 跳过切换激活用例")

    # ---------- 3. 班次配置 ----------
    check(
        "班次配置已从激活项目读取",
        "window.__tjscShiftCfg === null || (typeof window.__tjscShiftCfg === 'object' && 'day' in window.__tjscShiftCfg)",
    )
    check(
        "班次显示为白班或夜班",
        "['白班','夜班'].includes(document.getElementById('stripShift').textContent)",
    )

    # ---------- 4. 平均节拍文案 ----------
    check(
        "顶栏节拍文案为平均节拍",
        "document.querySelector('.strip .beat').textContent.includes('平均节拍')",
    )

    # ---------- 5. 运行指标雷达 ----------
    check(
        "雷达面板标题为运行指标雷达",
        "[...document.querySelectorAll('.panel .ph')].some(e => e.textContent.includes('运行指标雷达'))",
    )
    check(
        "雷达指标轴为五维真指标",
        """(() => {
          const c = echarts.getInstanceByDom(document.getElementById('radar'));
          if (!c) return false;
          const names = (c.getOption().radar[0].indicator || []).map(i => i.name);
          return JSON.stringify(names) === JSON.stringify(['合格率','推理FPS','节拍稳定度','预警密度','步骤完成度']);
        })()""",
    )
    check(
        "雷达系列名为运行指标且数值合法",
        """(() => {
          const c = echarts.getInstanceByDom(document.getElementById('radar'));
          if (!c) return false;
          const s = c.getOption().series[1];
          const d = s.data && s.data[0];
          return d && d.name === '运行指标' && d.value.length === 5 && d.value.every(v => v >= 0 && v <= 100);
        })()""",
    )

    # ---------- 6. 随机预警池已删除 ----------
    check("随机预警文案池已删除", "typeof alertPool === 'undefined' && typeof addAlert === 'undefined'")
    check(
        "预警列表无随机演示文案",
        """(() => {
          const banned = ['装电池节拍达标','压合动作过快','电池对准耗时偏长','设备速度正常','工件装配合格'];
          return [...document.querySelectorAll('.alerts .list .msg')].every(e => !banned.includes(e.textContent));
        })()""",
    )

    # ---------- 7. 3D 场景绑运行态 ----------
    check(
        "3D 场景挂真实运行态 class",
        """(() => {
          const sc = document.querySelector('.preview3d .scene');
          if (!sc) return false;
          const det = window.__tjscLastDet;
          if (!det) return true; // 无真值帧时不判
          const running = !!(det.is_running || det.is_detecting);
          return sc.classList.contains('tj-idle') === !running;
        })()""",
    )
    check(
        "3D 工位标签为真实通道+状态",
        """(() => {
          const lab = document.querySelector('.twin-mini-label');
          if (!lab || !window.__tjscLastDet) return true;
          return /工位\\d{2} · (运行中|待机|NG 告警)/.test(lab.textContent);
        })()""",
    )
    check(
        "顺序模式装配标签绑真实步骤态",
        """(() => {
          const st = document.querySelector('.assembly-action .step-tag');
          if (!st || !window.__tjscLastDet) return true;
          if (['trk','box','peritem'].includes(window.__tjscMode)) return true;
          return /^(步骤\\d+ |周期完成|待机中)/.test(st.textContent);
        })()""",
    )

    # ---------- 8. 模式切换直驱布局 (不再借道下拉框) ----------
    check("布局切换函数已外露", "typeof window.__tjscApplyLayout === 'function'")
    check(
        "布局切换不污染项目下拉值",
        """(() => {
          const sel = document.getElementById('stripProj');
          const before = sel.value;
          window.__tjscApplyLayout('trk');
          const ok = sel.value === before;
          window.__tjscApplyLayout('seq');
          return ok;
        })()""",
    )

    # ---------- 汇总 ----------
    real_errors = [e for e in console_errors if "favicon" not in e and "ERR_BLOCKED_BY_CLIENT" not in e]
    results.append(("无控制台错误", len(real_errors) == 0))
    log(("PASS  " if results[-1][1] else "FAIL  ") + "无控制台错误")
    if real_errors:
        for e in real_errors[:8]:
            log("  console: " + e[:200])

    browser.close()

failed = [n for n, ok in results if not ok]
log("\n===== 阶段5 UAT 结果: %d/%d 通过 =====" % (len(results) - len(failed), len(results)))
if failed:
    for n in failed:
        log("FAILED: " + n)
    sys.exit(1)
log("ALL PASS")
