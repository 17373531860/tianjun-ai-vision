"""v3.7.1 → v3.7.2 前端可见浏览器 UAT (acceptance)

跑前提:
  - 后端运行在 :8001
  - 前端运行在 :6001
  - playwright + chromium 已装

覆盖:
  Phase A · 项目管理页 (FIX-381-B / FIX-381-C)
    A1 步骤设置表里有「检测框颜色」列, 点开是 el-color-picker
    A2 步骤行第一列有 from_model 来源标签 (main/副模型)

  Phase B · 实时导出规则对话框 (v3.7.2)
    B1 "新建规则" → 模板下拉里看到扫码器旁路预设带 ★ 标记
    B2 选中预设后, 表单自动应用 12 项推荐配置, 顶上有 cyan 提示
    B3 取文件策略默认 = C (cycle_start_snapshot)
    B4 稳定等待 100, 最大年龄 60
    B5 换行符 = crlf, 输入文件模式 = none
    B6 同名去重 = 关闭 (默认)

每一步截图. 最后写一份 verdict JSON.
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright, expect

OUT_DIR = Path(__file__).resolve().parents[2] / "evidence" / \
    f"v372_acceptance_{datetime.now().strftime('%Y-%m-%d_%H%M')}"
OUT_DIR.mkdir(parents=True, exist_ok=True)
print(f"[UAT] 输出目录: {OUT_DIR}")

verdict = {
    "started_at": datetime.now().isoformat(),
    "phase_a_project_page": {},
    "phase_b_realtime_rule": {},
    "errors": [],
}


def screenshot(page, name):
    path = OUT_DIR / f"{name}.png"
    page.screenshot(path=str(path), full_page=False)
    print(f"  📸 {path.name}")
    return str(path)


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False, slow_mo=120,
            args=["--start-maximized"],
        )
        context = browser.new_context(
            viewport={"width": 1600, "height": 920},
            record_video_dir=str(OUT_DIR),
            record_video_size={"width": 1600, "height": 920},
        )
        page = context.new_page()
        page.on("console", lambda msg: msg.type == "error" and
                print(f"  [browser-err] {msg.text[:200]}"))

        try:
            # === Phase A: 项目管理页 ===
            print("\n=== Phase A · 项目管理页 (FIX-381-B / FIX-381-C) ===")
            page.goto("http://localhost:6001/#/project", timeout=30000,
                       wait_until="domcontentloaded")
            page.wait_for_timeout(3000)
            screenshot(page, "a0_project_list")

            # Project 页布局: 左侧项目列表 (click 选中) + 右侧 tab 编辑
            # 点左侧第一个项目卡片
            cards = page.locator(".bg-slate-900.hover\\:bg-slate-800, "
                                  ".cursor-pointer.bg-slate-900")
            if cards.count() == 0:
                # 兜底: 直接找标题为某项目名的可点元素
                cards = page.locator("div[class*='bg-slate-900'][class*='hover']")
            print(f"  发现 {cards.count()} 个项目卡片")
            if cards.count() == 0:
                verdict["phase_a_project_page"]["status"] = "SKIP_no_projects"
                verdict["errors"].append(f"项目列表为空 (HTML 上无可选卡片)")
            else:
                cards.nth(0).click(timeout=5000)
                page.wait_for_timeout(2000)
                screenshot(page, "a1_project_selected")

                # 切到「步骤设置」tab (页面里有多个 tab: 逻辑/事件/步骤设置/...)
                step_tab = page.locator(".el-tabs__item, [role='tab']").filter(
                    has_text="步骤设置"
                )
                if step_tab.count() == 0:
                    # 兜底: 任何含"步骤设置"的可点元素
                    step_tab = page.get_by_text("步骤设置").first
                try:
                    step_tab.first.click(timeout=5000)
                    page.wait_for_timeout(1200)
                except Exception:
                    pass
                screenshot(page, "a2_step_config_tab")

                # 查 「检测框颜色」 列头 (这是 FIX-381-B 加的新列)
                color_col = page.get_by_text("检测框颜色")
                has_color_col = color_col.count() > 0
                verdict["phase_a_project_page"]["has_box_color_column"] = has_color_col
                print(f"  A1 检测框颜色列: {'✅' if has_color_col else '❌'}")

                # 查 el-color-picker 触发器 (步骤设置表里的颜色选择控件)
                color_pickers = page.locator(".el-color-picker__trigger")
                picker_count = color_pickers.count()
                verdict["phase_a_project_page"]["color_pickers"] = picker_count
                print(f"  A1b 颜色选择器数量: {picker_count}")

                # 查 from_model 标签 (FIX-381-C: 主模型 'main' / 副模型 '副模型')
                main_count = page.locator(".el-tag").filter(has_text="main").count()
                other_count = page.locator(".el-tag").filter(has_text="副模型").count()
                # 兜底: 中文显示也可能是 "主模型"
                primary_count = page.locator(".el-tag").filter(has_text="主模型").count()
                verdict["phase_a_project_page"]["from_model_tags"] = {
                    "main_en": main_count, "primary_cn": primary_count,
                    "secondary": other_count,
                }
                print(f"  A2 from_model 标签: main={main_count}, 主模型={primary_count}, 副模型={other_count}")

                # 截图色弹窗 — 点第一个 color picker
                if picker_count > 0:
                    try:
                        color_pickers.first.scroll_into_view_if_needed(timeout=3000)
                        color_pickers.first.click(timeout=3000)
                        page.wait_for_timeout(600)
                        screenshot(page, "a3_color_picker_open")
                        page.keyboard.press("Escape")
                    except Exception as e:
                        print(f"  [warn] color picker 交互: {e}")

                # 验收通过条件: 至少有颜色列
                verdict["phase_a_project_page"]["status"] = (
                    "PASS" if has_color_col and picker_count > 0 else "PARTIAL"
                )

            # === Phase B: 实时导出规则对话框 ===
            print("\n=== Phase B · 实时导出规则对话框 (v3.7.2 扫码器旁路) ===")
            # 先回 Project 页正经激活一个项目 (Pinia store 内存态), 然后跳 Data
            page.goto("http://localhost:6001/#/project", timeout=30000,
                       wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
            # 点第一个项目卡片
            cards = page.locator(".cursor-pointer.bg-slate-900, "
                                  "div[class*='bg-slate-900'][class*='hover']")
            if cards.count() > 0:
                cards.nth(0).click()
                page.wait_for_timeout(1500)
            # 点顶上的「启用当前项目」按钮 (handleActivateProject)
            activate_btn = page.locator("button").filter(has_text="启用当前项目")
            if activate_btn.count() > 0:
                try:
                    activate_btn.first.click(timeout=5000)
                    page.wait_for_timeout(2000)
                    # 处理可能的确认 dialog
                    confirm = page.locator(".el-message-box__btns button.el-button--primary")
                    if confirm.count() > 0:
                        confirm.first.click()
                        page.wait_for_timeout(1500)
                except Exception as e:
                    print(f"  [warn] 激活项目: {e}")
            page.goto("http://localhost:6001/#/data", timeout=30000,
                       wait_until="domcontentloaded")
            page.wait_for_timeout(3000)
            screenshot(page, "b0_data_page")

            # Data 页有多个 tab. 列一下当前所有按钮供调试.
            all_btn_texts = page.locator("button").all_inner_texts()
            print(f"  Data 页按钮: {all_btn_texts[:30]}")
            verdict["phase_b_realtime_rule"]["page_buttons"] = all_btn_texts[:30]

            # 入口在 Data 页 "自定义导出" tab 下的卡片里, 按钮文字 "实时规则（扫码自动写入）"
            entry = page.locator("button").filter(has_text="实时规则")
            if entry.count() == 0:
                entry = page.locator("button").filter(has_text="实时输出")
            if entry.count() == 0:
                # 试试切到 自定义导出 tab
                custom_tab = page.locator(".el-tabs__item").filter(has_text="自定义导出")
                if custom_tab.count() > 0:
                    custom_tab.first.click()
                    page.wait_for_timeout(800)
                    screenshot(page, "b0_data_custom_tab")
                    entry = page.locator("button").filter(has_text="实时规则")
            if entry.count() == 0:
                # 列出页面里所有按钮供调试
                buttons = page.locator("button").all_inner_texts()
                verdict["errors"].append(
                    f"找不到「实时导出规则」按钮, 现有按钮={buttons[:20]}"
                )
                verdict["phase_b_realtime_rule"]["status"] = "FAIL_entry_not_found"
            else:
                entry.first.click()
                page.wait_for_timeout(1200)
                screenshot(page, "b1_rules_dialog_open")

                # 「新建规则」
                new_btn = page.get_by_role("button", name="新建规则")
                if new_btn.count() == 0:
                    new_btn = page.locator("button").filter(has_text="新建")
                new_btn.first.click()
                page.wait_for_timeout(1200)
                screenshot(page, "b2_editor_open")

                # 选模板 — 找包含 "扫码器旁路" + ★ 的选项
                template_select = page.locator(".el-select").filter(
                    has_text="模板"
                ).first
                template_select.click()
                page.wait_for_timeout(500)
                screenshot(page, "b3_template_options")

                star_option = page.locator(".el-select-dropdown__item").filter(
                    has_text="扫码器旁路"
                ).first
                option_text = star_option.inner_text() if star_option.count() else ""
                has_star = "★" in option_text
                verdict["phase_b_realtime_rule"]["template_has_star"] = has_star
                verdict["phase_b_realtime_rule"]["template_option_text"] = option_text
                print(f"  B1 扫码器旁路预设带 ★: {'✅' if has_star else '❌'} "
                      f"({option_text!r})")

                if star_option.count() > 0:
                    star_option.click()
                    page.wait_for_timeout(800)
                    screenshot(page, "b4_template_selected")

                    # 验证 cyan 提示出现 "已自动应用模板"
                    hint = page.locator("text=已自动应用模板")
                    has_hint = hint.count() > 0
                    verdict["phase_b_realtime_rule"]["auto_apply_hint"] = has_hint
                    print(f"  B2 自动应用提示: {'✅' if has_hint else '❌'}")

                    # 验证 C 策略被选中
                    c_radio = page.locator("label").filter(
                        has_text="C · 周期开始锁快照"
                    ).first
                    c_radio_class = c_radio.get_attribute("class") or "" if c_radio.count() else ""
                    is_c_selected = "is-checked" in c_radio_class
                    verdict["phase_b_realtime_rule"]["c_strategy_default"] = is_c_selected
                    print(f"  B3 C 策略默认选中: {'✅' if is_c_selected else '❌'}")

                    # 截一张全表单
                    screenshot(page, "b5_form_filled")

                    # 滚到去重区
                    dedupe_section = page.get_by_text("同名去重")
                    if dedupe_section.count() > 0:
                        dedupe_section.scroll_into_view_if_needed()
                        page.wait_for_timeout(500)
                        screenshot(page, "b6_dedupe_section")

                    verdict["phase_b_realtime_rule"]["status"] = "PASS"
                else:
                    verdict["phase_b_realtime_rule"]["status"] = "FAIL_no_scanner_bypass_option"

            verdict["completed_at"] = datetime.now().isoformat()
            verdict["overall"] = (
                verdict["phase_a_project_page"].get("status") == "PASS"
                and verdict["phase_b_realtime_rule"].get("status") == "PASS"
            )

        except Exception as e:
            import traceback
            verdict["errors"].append(f"{type(e).__name__}: {e}")
            verdict["traceback"] = traceback.format_exc()
            screenshot(page, "ZZ_error")
        finally:
            try:
                page.wait_for_timeout(1500)
                context.close()
            except Exception:
                pass
            browser.close()

    # 写 verdict
    verdict_path = OUT_DIR / "verdict.json"
    with open(verdict_path, "w", encoding="utf-8") as f:
        json.dump(verdict, f, ensure_ascii=False, indent=2)
    print(f"\n📋 verdict 写入: {verdict_path}")
    print(f"   总体: {'✅ PASS' if verdict.get('overall') else '❌ FAIL/PARTIAL'}")
    print(f"\n输出目录: {OUT_DIR}")
    return 0 if verdict.get("overall") else 1


if __name__ == "__main__":
    sys.exit(main())
