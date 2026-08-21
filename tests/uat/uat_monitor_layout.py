# -*- coding: utf-8 -*-
"""检测主页自定义布局 (v3.54) — 可见浏览器 UAT（路径 H, 金标准全链路）。

现场叙事:
  产线组长嫌默认排版不合本工位习惯, 在系统设置「显示设置」点「自定义编辑
  主页」→ 跳到检测主页进入布局编辑态 (工具条+各区块选框) → 把视频画面拖到
  别处、把步骤表拉大 → 点保存 → 退出编辑, 主页立即按新排版渲染 → 刷新页面
  排版仍在 (落库不是内存态) → 回显示设置看到「单工位 · 标准」已定制标签 →
  点全部恢复默认 → 主页回出厂排版。

运行前提:
  - backend 8002 (RUNTIME_MODE=test 非必需, 本 UAT 不跑检测)
  - frontend dev 6002

用法:
  ~/miniconda3/envs/tianjun/bin/python tests/uat/uat_monitor_layout.py

证据产出: tests/uat/artifacts/monitor-layout/ (截图 + 录屏 + run.log)
"""
import os
import sys
import time
from datetime import datetime

import requests
from playwright.sync_api import sync_playwright, expect

API = os.environ.get("UAT_API", "http://localhost:8002/api/v1")
FRONT = os.environ.get("UAT_FRONT", "http://localhost:6002")

ART_DIR = os.path.join(os.path.dirname(__file__), "artifacts", "monitor-layout")
PROJECT_NAME = f"__uat_layout_{int(time.time())}"

_log_lines = []
_assert_count = 0


def log(msg):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    _log_lines.append(line)


def ok(msg):
    global _assert_count
    _assert_count += 1
    log(f"✅ [{_assert_count:02d}] {msg}")


def die(msg):
    log(f"❌ FAIL: {msg}")
    _flush_log()
    sys.exit(1)


def _flush_log():
    os.makedirs(ART_DIR, exist_ok=True)
    with open(os.path.join(ART_DIR, "run.log"), "w", encoding="utf-8") as f:
        f.write("\n".join(_log_lines) + "\n")


def seed_backend():
    """清残留布局 + 建激活项目 (让主页有 SOP/步骤表可排)"""
    requests.delete(f"{API}/system/monitor-layouts", timeout=5)
    payload = {
        "name": PROJECT_NAME,
        "task_type": "detection",
        "logic_mode": "sequential",
        "pipeline_config": {},
        "steps_config": [
            {"id": 1, "label": "step_a", "name": "步骤A", "enabled": True},
            {"id": 2, "label": "step_b", "name": "步骤B", "enabled": True},
        ],
        "events_config": [
            {"id": 1, "name": "合格", "type": "ok", "enabled": True},
            {"id": 2, "name": "NG", "type": "ng", "enabled": True},
        ],
        "counters_config": [],
        "alarm_config": {},
        "detection_config": {},
        "data_config": {},
        "default_model_id": None,
        "model_format": "pytorch_fp32",
    }
    r = requests.post(f"{API}/projects", json=payload, timeout=10)
    r.raise_for_status()
    pid = r.json()["id"]
    requests.post(f"{API}/projects/{pid}/activate", timeout=10).raise_for_status()
    log(f"已创建并激活项目 {PROJECT_NAME} (id={pid})")
    return pid


def cleanup_backend(pid):
    requests.delete(f"{API}/system/monitor-layouts", timeout=5)
    if pid:
        requests.delete(f"{API}/projects/{pid}", timeout=5)


def slot_pos(page, family, slot):
    return page.evaluate(
        """([family, slot]) => {
             const canvas = document.querySelector(`[data-layout-canvas='${family}']`);
             const el = canvas && canvas.querySelector(`[data-layout-slot='${slot}']`);
             if (!el) return null;
             const c = canvas.getBoundingClientRect();
             const r = el.getBoundingClientRect();
             return { x: (r.left - c.left) / c.width,
                      y: (r.top - c.top) / c.height,
                      w: r.width / c.width, h: r.height / c.height,
                      applied: el.dataset.layoutApplied === '1' };
           }""",
        [family, slot],
    )


def shot(page, name):
    path = os.path.join(ART_DIR, name)
    page.screenshot(path=path, full_page=False)
    log(f"截图 {name}")


def main():
    os.makedirs(ART_DIR, exist_ok=True)
    pid = None
    try:
        pid = seed_backend()
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=False, slow_mo=120)
            ctx = browser.new_context(
                viewport={"width": 1600, "height": 900},
                record_video_dir=ART_DIR,
                record_video_size={"width": 1600, "height": 900},
            )
            page = ctx.new_page()

            # ---------- 1. 显示设置入口 ----------
            page.goto(f"{FRONT}/#/settings", wait_until="domcontentloaded")
            card = page.locator("[data-testid='layout-entry-card']")
            card.wait_for(state="visible", timeout=15000)
            ok("显示设置出现「检测主页自定义布局」入口卡")
            btn = page.locator("[data-testid='layout-edit-entry']")
            expect(btn).to_be_enabled()
            ok("「自定义编辑主页」按钮可用 (未开鉴权=不设限)")
            shot(page, "01-settings-entry.png")

            btn.click()
            page.wait_for_selector("[data-testid='layout-editor']", timeout=15000)
            ok("点击入口跳转检测主页并进入布局编辑态")

            # ---------- 2. 编辑器形态 ----------
            page.wait_for_selector("[data-testid='layout-box-video']", timeout=8000)
            page.wait_for_timeout(600)  # 等补测双帧
            boxes = page.locator("[data-testid^='layout-box-']").count()
            if boxes < 3:
                die(f"编辑框数量过少: {boxes}")
            ok(f"编辑覆盖层渲染 {boxes} 个区块选框 (含条件块强制显示)")
            toolbar_text = page.locator("[data-testid='layout-toolbar']").text_content()
            if "单工位" not in (toolbar_text or ""):
                die(f"工具条形态标签不对: {toolbar_text}")
            ok("工具条显示当前形态「单工位 · 标准」")
            shot(page, "02-editor-open.png")

            before = slot_pos(page, "single", "video")

            # ---------- 3. 拖动视频区块 ----------
            bb = page.locator("[data-testid='layout-box-video']").bounding_box()
            page.mouse.move(bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2)
            page.mouse.down()
            page.mouse.move(bb["x"] + bb["width"] / 2 + 150,
                            bb["y"] + bb["height"] / 2 + 80, steps=12)
            page.mouse.up()
            page.wait_for_timeout(400)
            after = slot_pos(page, "single", "video")
            if not (after["x"] > before["x"] + 0.02):
                die(f"拖动没生效: before={before} after={after}")
            ok(f"拖动视频画面生效 x {before['x']:.3f}→{after['x']:.3f} (实时预览)")

            # ---------- 4. 缩放步骤表 (右下手柄) ----------
            st_box = page.locator("[data-testid='layout-box-step-table']")
            if st_box.count():
                st_before = slot_pos(page, "single", "step-table")
                st_box.click()  # 选中后才画八向手柄
                page.wait_for_timeout(200)
                hb = st_box.locator("[data-testid='layout-handle-se']").bounding_box()
                page.mouse.move(hb["x"] + hb["width"] / 2, hb["y"] + hb["height"] / 2)
                page.mouse.down()
                page.mouse.move(hb["x"] - 80, hb["y"] - 60, steps=10)
                page.mouse.up()
                page.wait_for_timeout(400)
                st_after = slot_pos(page, "single", "step-table")
                if abs(st_after["w"] - st_before["w"]) < 0.01 and \
                   abs(st_after["h"] - st_before["h"]) < 0.01:
                    die(f"缩放没生效: {st_before} → {st_after}")
                ok(f"步骤表手柄缩放生效 w {st_before['w']:.3f}→{st_after['w']:.3f}")

            # ---------- 5. 撤销 / 重做 ----------
            undo = page.locator("[data-testid='layout-undo']")
            expect(undo).to_be_enabled()
            undo.click()
            page.wait_for_timeout(300)
            expect(page.locator("[data-testid='layout-redo']")).to_be_enabled()
            page.locator("[data-testid='layout-redo']").click()
            page.wait_for_timeout(300)
            ok("撤销 / 重做按钮工作正常")
            shot(page, "03-after-drag.png")

            # ---------- 6. 保存 → 落库 ----------
            page.locator("[data-testid='layout-save']").click()
            page.wait_for_selector(".el-message--success", timeout=8000)
            layouts = requests.get(f"{API}/system/monitor-layouts",
                                   timeout=5).json()["layouts"]
            if "single:default" not in layouts:
                die(f"后端没落库: {list(layouts)}")
            saved_video = layouts["single:default"]["slots"]["video"]
            ok(f"保存成功且后端落库 video=({saved_video['x']}, {saved_video['y']})")

            # ---------- 7. 完成退出 → 普通模式生效 ----------
            page.locator("[data-testid='layout-done']").click()
            page.wait_for_timeout(800)
            if page.locator("[data-testid='layout-editor']").count():
                die("完成后编辑器没退出")
            pos = slot_pos(page, "single", "video")
            if not pos["applied"]:
                die(f"退出编辑后自定义布局没应用: {pos}")
            ok("完成退出后主页按自定义排版渲染")
            shot(page, "04-applied-normal.png")

            # ---------- 8. 刷新持久化 ----------
            page.reload(wait_until="domcontentloaded")
            page.wait_for_selector("[data-layout-canvas='single']", timeout=15000)
            page.wait_for_timeout(1500)
            pos = slot_pos(page, "single", "video")
            if not (pos and pos["applied"] and abs(pos["x"] - saved_video["x"]) < 0.02):
                die(f"刷新后布局丢失: {pos} vs {saved_video}")
            ok("刷新页面后自定义排版仍在 (持久化于后端, 非浏览器缓存)")

            # ---------- 9. 显示设置显示已定制 + 全部恢复默认 ----------
            page.goto(f"{FRONT}/#/settings", wait_until="domcontentloaded")
            forms = page.locator("[data-testid='layout-custom-forms']")
            forms.wait_for(state="visible", timeout=15000)
            if "单工位" not in (forms.text_content() or ""):
                die(f"已定制形态列表不含单工位: {forms.text_content()}")
            ok("显示设置列出已定制形态「单工位 · 标准」")
            shot(page, "05-settings-customized.png")

            page.locator("[data-testid='layout-restore-all']").click()
            page.locator(".el-message-box").get_by_role(
                "button", name="全部恢复默认").click()
            page.wait_for_timeout(1000)
            layouts = requests.get(f"{API}/system/monitor-layouts",
                                   timeout=5).json()["layouts"]
            if layouts:
                die(f"恢复默认后后端还有残留: {list(layouts)}")
            ok("全部恢复默认: 后端布局清空")

            # ---------- 10. 主页回出厂排版 ----------
            page.goto(f"{FRONT}/#/monitor", wait_until="domcontentloaded")
            page.wait_for_selector("[data-layout-canvas='single']", timeout=15000)
            page.wait_for_timeout(1500)
            pos = slot_pos(page, "single", "video")
            if pos and pos["applied"]:
                die(f"恢复默认后主页仍在用自定义布局: {pos}")
            ok("主页回出厂排版 (元素回归自然文档流)")
            shot(page, "06-restored-default.png")

            ctx.close()
            browser.close()

        log(f"🎉 UAT 全部通过: {_assert_count} 项断言")
        _flush_log()
    except SystemExit:
        raise
    except Exception as e:
        die(f"异常: {type(e).__name__}: {e}")
    finally:
        cleanup_backend(pid)


if __name__ == "__main__":
    main()
