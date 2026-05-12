"""复现客户报的 v3.7.1 卡住 bug.

客户叙事:
  "视频检测停止再打开的时候 检测卡片就会全绿, 然后不动了, 终端在走画面不显示"
  追问触发条件: "改动了参数之类回来就会卡住"
  追问改的哪页:  "项目管理"

复现假设:
  Monitor 跑着 → 切到 Project 页改某步骤阈值保存 →
  projectStore.setCurrentProject 替换 currentProject →
  切回 Monitor → 组件重 mount + watch(currentProject, deep:true, immediate:true) 立刻触发 →
  并发: onMounted 起 polling + watch 重建 steps.value + forceReconnectStream →
  某条路径 race / 异常, 画面黑屏 + UI 冻结.

本脚本动作:
  1. 备份当前 active project id
  2. 激活 id=6 "test" (sequential, 6 steps, 不动用户在用的项目)
  3. headless=False 打开 :6001 → Monitor → 点开始
  4. 5 秒基线: 截图 + 抓 console.error 数 + 读 streamSrc / steps 卡颜色 / pollingTimer
  5. 切到 项目管理 → 选中 test → 改第一个 step.conf → 保存
  6. 切回 Monitor
  7. 10 秒观察: 截图 + console.error + streamSrc / steps / polling 状态
  8. 还原 active project
  9. 落地 evidence/bug_freeze_2026-05-13/ + verdict.json

跑法:
  cd /home/qianqian/桌面/word/tianjun副本
  python tests/manual_uat/bug_freeze_repro_uat.py
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parents[2]
EVIDENCE = REPO / "evidence" / "bug_freeze_2026-05-13"
EVIDENCE.mkdir(parents=True, exist_ok=True)

BACKEND = "http://localhost:8001"
FRONTEND = "http://localhost:6001"
TEST_PROJECT_ID = 6


def shot(page, name: str) -> None:
    path = EVIDENCE / f"{name}.png"
    try:
        page.screenshot(path=str(path), full_page=True)
        print(f"  📸 {path.name}")
    except Exception as e:
        print(f"  ⚠️ screenshot {name} failed: {e}")


def snapshot_state(page, label: str) -> dict:
    """读 Vue 内部状态: streamSrc / steps colors / polling alive."""
    js = """
    () => {
      const result = {
        streamSrc0_present: false,
        streamSrc0_src: '',
        streamSrc0_natural_w: 0,
        streamSrc0_natural_h: 0,
        step_cards_count: 0,
        step_cards_classes: [],
        is_running_btn_text: '',
        polling_likely_alive: false,
        page_url: location.href,
      };
      const imgs = Array.from(document.querySelectorAll('img'));
      const mjpeg = imgs.find(i => (i.src||'').includes('/video_feed') || (i.src||'').includes('/api/v1/source/video_feed'));
      if (mjpeg) {
        result.streamSrc0_present = true;
        result.streamSrc0_src = mjpeg.src;
        result.streamSrc0_natural_w = mjpeg.naturalWidth;
        result.streamSrc0_natural_h = mjpeg.naturalHeight;
      }
      const stepCards = Array.from(document.querySelectorAll('[class*="step-card"], [class*="step_card"], .step-item, .step'));
      result.step_cards_count = stepCards.length;
      result.step_cards_classes = stepCards.slice(0, 20).map(c => (c.className||'').toString().slice(0,200));
      const startBtn = Array.from(document.querySelectorAll('button')).find(b => /开始|停止|启动/.test(b.textContent||''));
      if (startBtn) result.is_running_btn_text = (startBtn.textContent||'').trim();
      result.polling_likely_alive = !!(window.__lastDetectionPollAt && (Date.now() - window.__lastDetectionPollAt) < 2000);
      return result;
    }
    """
    try:
        return page.evaluate(js)
    except Exception as e:
        return {"error": str(e), "label": label}


def main() -> int:
    print(f"[init] evidence dir: {EVIDENCE}")
    verdict = {
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "test_project_id": TEST_PROJECT_ID,
        "phases": {},
        "console_errors": [],
        "console_warnings": [],
        "pageerrors": [],
        "verdict": "unknown",
    }

    backup_active = None
    try:
        r = requests.get(f"{BACKEND}/api/v1/projects", timeout=5)
        if r.ok:
            for p in r.json().get("items", []):
                if p.get("is_active") or p.get("active"):
                    backup_active = p.get("id")
                    break
        print(f"[init] backup active project: {backup_active}")
    except Exception as e:
        print(f"[init] cannot detect active project: {e}")

    try:
        r = requests.post(f"{BACKEND}/api/v1/projects/{TEST_PROJECT_ID}/activate", timeout=10)
        print(f"[init] activate test project -> {r.status_code}")
        verdict["activate_status"] = r.status_code
    except Exception as e:
        print(f"[init] activate failed: {e}")
        verdict["activate_error"] = str(e)

    try:
        r = requests.get(f"{BACKEND}/api/v1/projects/{TEST_PROJECT_ID}", timeout=5)
        project_data = r.json() if r.ok else {}
        steps_config_before = project_data.get("steps_config", [])
        print(f"[init] test project has {len(steps_config_before)} steps")
        verdict["steps_count_before"] = len(steps_config_before)
    except Exception as e:
        steps_config_before = []
        print(f"[init] fetch project detail failed: {e}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, args=["--no-sandbox"])
        ctx = browser.new_context(
            viewport={"width": 1600, "height": 1000},
            accept_downloads=True,
            record_video_dir=str(EVIDENCE),
        )
        page = ctx.new_page()
        page.set_default_timeout(15000)

        page.on("console", lambda msg: (
            verdict["console_errors"].append({"text": msg.text[:500], "loc": str(msg.location)})
            if msg.type == "error"
            else verdict["console_warnings"].append({"text": msg.text[:300]})
            if msg.type == "warning"
            else None
        ))
        page.on("pageerror", lambda exc: verdict["pageerrors"].append({"msg": str(exc)[:500]}))

        page.add_init_script("""
          (() => {
            const origFetch = window.fetch;
            window.fetch = function(...args) {
              const url = (args[0]||'').toString();
              if (url.includes('/source/detection_results') || url.includes('/detection_results')) {
                window.__lastDetectionPollAt = Date.now();
              }
              return origFetch.apply(this, args);
            };
          })();
        """)

        try:
            print("\n[1] 打开 Monitor 页")
            page.goto(f"{FRONTEND}/", wait_until="domcontentloaded")
            time.sleep(1.5)
            try:
                page.locator("a[href='#/monitor'], a[href='/monitor']").first.click(timeout=3000)
            except Exception:
                try:
                    page.get_by_text("监控", exact=False).first.click(timeout=3000)
                except Exception:
                    page.goto(f"{FRONTEND}/#/monitor", wait_until="domcontentloaded")
            time.sleep(2.0)
            shot(page, "01_monitor_landing")
            verdict["phases"]["monitor_landing"] = snapshot_state(page, "monitor_landing")

            print("\n[2] 点 '开始' 按钮")
            start_btn = None
            for sel in ["button:has-text('开始')", "button:has-text('启动')"]:
                try:
                    el = page.locator(sel).first
                    if el.is_visible(timeout=2000):
                        start_btn = el
                        break
                except Exception:
                    continue
            if start_btn is not None:
                start_btn.click()
                print("  ✓ 点了开始")
            else:
                print("  ⚠️ 找不到开始按钮 (可能已在运行)")
            time.sleep(5.0)
            shot(page, "02_after_start")
            verdict["phases"]["after_start_5s"] = snapshot_state(page, "after_start")
            verdict["console_errors_after_start"] = len(verdict["console_errors"])

            print("\n[3] 切到 项目管理")
            try:
                page.locator("a[href='#/project'], a[href='/project']").first.click(timeout=3000)
            except Exception:
                page.goto(f"{FRONTEND}/#/project", wait_until="domcontentloaded")
            time.sleep(2.0)
            shot(page, "03_project_page_open")

            print("\n[4] 选中 test 项目 (id=6)")
            try:
                page.get_by_text("test", exact=True).first.click(timeout=5000)
            except Exception as e:
                print(f"  ⚠️ 点 test 失败: {e}")
            time.sleep(1.5)
            shot(page, "04_project_test_selected")

            print("\n[5] 改第一个步骤的置信度 (找 input type=number) → 改成 0.6")
            changed = False
            try:
                inputs = page.locator("input[type='number']").all()
                print(f"  number inputs found: {len(inputs)}")
                if inputs:
                    first = inputs[0]
                    first.scroll_into_view_if_needed(timeout=3000)
                    cur = first.input_value()
                    print(f"  first input current value: {cur!r}")
                    new_val = "0.6" if cur != "0.6" else "0.55"
                    first.fill(new_val)
                    page.keyboard.press("Tab")
                    print(f"  改为 {new_val}")
                    changed = True
            except Exception as e:
                print(f"  ⚠️ 改参数失败: {e}")
            time.sleep(1.0)
            shot(page, "05_param_changed")
            verdict["param_changed"] = changed

            print("\n[6] 点 '保存' 按钮")
            try:
                save_btn = page.locator("button:has-text('保存')").first
                save_btn.scroll_into_view_if_needed(timeout=3000)
                save_btn.click(timeout=5000)
                print("  ✓ 点了保存")
            except Exception as e:
                print(f"  ⚠️ 点保存失败: {e}")
            time.sleep(2.0)
            shot(page, "06_after_save")

            print("\n[7] 切回 Monitor")
            try:
                page.locator("a[href='#/monitor'], a[href='/monitor']").first.click(timeout=3000)
            except Exception:
                page.goto(f"{FRONTEND}/#/monitor", wait_until="domcontentloaded")
            time.sleep(1.5)
            shot(page, "07_back_to_monitor_immediate")
            verdict["phases"]["back_immediate"] = snapshot_state(page, "back_immediate")

            print("\n[8] 观察 10 秒 (关键 — 看是否黑屏 + 卡片冻结)")
            for i, t in enumerate([3, 3, 4]):
                time.sleep(t)
                shot(page, f"08_observe_{i+1}_{(i+1)*3}s")
            verdict["phases"]["back_after_10s"] = snapshot_state(page, "back_after_10s")
            verdict["console_errors_final"] = len(verdict["console_errors"])

            print("\n[9] 二次循环: 再切回 Project 再改一次再回来 (有些 bug 第二次才出)")
            try:
                page.locator("a[href='#/project'], a[href='/project']").first.click(timeout=3000)
                time.sleep(2.0)
                page.get_by_text("test", exact=True).first.click(timeout=3000)
                time.sleep(1.0)
                inputs = page.locator("input[type='number']").all()
                if inputs:
                    inputs[0].fill("0.5")
                    page.keyboard.press("Tab")
                    time.sleep(0.5)
                    page.locator("button:has-text('保存')").first.click(timeout=3000)
                    time.sleep(2.0)
                page.locator("a[href='#/monitor'], a[href='/monitor']").first.click(timeout=3000)
                time.sleep(2.0)
                shot(page, "09_after_second_cycle")
                time.sleep(6.0)
                shot(page, "10_after_second_cycle_6s")
                verdict["phases"]["second_cycle_done"] = snapshot_state(page, "second_cycle")
            except Exception as e:
                print(f"  ⚠️ 第二次循环失败: {e}")
                verdict["second_cycle_error"] = str(e)

        except Exception as e:
            traceback.print_exc()
            verdict["fatal_error"] = str(e)
            shot(page, "ZZ_fatal")
        finally:
            verdict["finished_at"] = datetime.now().isoformat(timespec="seconds")
            try:
                ctx.close()
            except Exception:
                pass
            browser.close()

    if backup_active and backup_active != TEST_PROJECT_ID:
        try:
            r = requests.post(f"{BACKEND}/api/v1/projects/{backup_active}/activate", timeout=10)
            print(f"\n[restore] reactivate id={backup_active} -> {r.status_code}")
        except Exception as e:
            print(f"[restore] failed: {e}")

    errs_after_start = verdict.get("console_errors_after_start", 0)
    errs_final = verdict.get("console_errors_final", 0)
    new_errs = errs_final - errs_after_start
    back = verdict["phases"].get("back_after_10s", {})
    streamSrc = back.get("streamSrc0_src", "")
    nat_w = back.get("streamSrc0_natural_w", 0)

    if new_errs > 0:
        verdict["verdict"] = f"REPRODUCED: {new_errs} new console errors after param change roundtrip"
    elif streamSrc and nat_w == 0:
        verdict["verdict"] = "REPRODUCED: stream <img> has src but natural size 0 (likely black)"
    elif not streamSrc:
        verdict["verdict"] = "REPRODUCED: stream <img> disappeared after roundtrip"
    else:
        verdict["verdict"] = "NOT REPRODUCED (dev env)"

    print("\n" + "=" * 60)
    print(f"VERDICT: {verdict['verdict']}")
    print(f"  console errors total: {errs_final}")
    print(f"  pageerrors: {len(verdict['pageerrors'])}")
    print(f"  stream src after roundtrip: {streamSrc[:80]}")
    print(f"  stream natural size: {nat_w}x{back.get('streamSrc0_natural_h',0)}")
    print("=" * 60)

    (EVIDENCE / "verdict.json").write_text(json.dumps(verdict, ensure_ascii=False, indent=2))
    print(f"\nverdict.json written: {EVIDENCE / 'verdict.json'}")
    print(f"evidence dir: {EVIDENCE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
