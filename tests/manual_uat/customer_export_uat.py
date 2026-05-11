"""客户老胡场景"自定义导出"真开浏览器实测

客户叙事:
  老胡用 v3.7.0, 想用「自定义导出 / 客户模板」对话框, 选某次 session,
  贴一段 Jinja2 模板, 导出一份 CSV, 里面要按 "周期序号/开始时间/结束时间/耗时/
  周期间隔/结果/事件/步骤序列/拿取/正面涂黑/翻转/反面涂黑/放置" 排版.
  之前老抱怨"步骤这些字段都没获取到".

本 UAT 的目的不是写代码 — 是模拟客户:
  1. 真打开 http://localhost:6001/data (Playwright headless)
  2. 点「自定义导出 / 客户模板」按钮
  3. 选「单 session」+ 勾「包含 cycle 明细」
  4. 选刚 seed 出来的 session_id
  5. 模板内容粘贴客户原版 (已轻微调好 None→空字符串 + 浮点 2 位小数)
  6. 截图右侧预览区
  7. 调 doDownload → 拿 CSV blob 落盘
  8. 断言: rendered/csv 同时含 "拿取" "正面涂黑" "翻转" "反面涂黑" "放置" 标题
          + 至少 1 个周期的步骤序列字段不为空
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parents[2]
EVIDENCE_DIR = REPO / "evidence" / "customer_export_2026-05-12"
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

SESSION_ID = int(sys.argv[1]) if len(sys.argv) > 1 else 843

# 客户原版模板, 仅做两处客户友好微调:
#   (1) `|default('')` → `or ''`  (Jinja default 不接 Python None)
#   (2) 数字加 '%.2f' 格式化, 避免出现 5.239999999999999 这种丑数字
CUSTOMER_TEMPLATE = """会话信息
会话ID,{{ session.id }}
开始时间,{{ session.start_time or '' }}
结束时间,{{ session.end_time or '' }}
总周期数,{{ stats.cycles|length }}
合格数,{{ stats.good_cycles or 0 }}
不良数,{{ stats.ng_cycles or 0 }}
平均周期时间(秒),{{ '%.2f' % (stats.avg_cycle_time or 0) }}

计数器统计
计数器名称,数值
合格总数,{{ stats.good_cycles or 0 }}
不良总数,{{ stats.ng_cycles or 0 }}
总产量,{{ stats.total_cycles or 0 }}

周期序号,开始时间,结束时间,耗时(秒),周期间隔(秒),结果,事件,步骤序列,拿取,正面涂黑,翻转,反面涂黑,放置
{% for cycle in stats.cycles -%}
{% set step_by_label = {} -%}
{% for s in cycle.steps -%}
{% set _ = step_by_label.update({s.label: s}) -%}
{% endfor -%}
{{ cycle.cycle_number }},{{ cycle.start_time or '' }},{{ cycle.end_time or '' }},{{ '%.2f' % (cycle.duration or 0) }},{{ '%.2f' % (cycle.interval or 0) if cycle.interval else '' }},{{ '合格' if cycle.is_good else '不良' }},{{ cycle.event or '' }},{{ cycle.steps|map(attribute='label')|join(' -> ') }},{{ '%.2f' % (step_by_label['拿取'].duration if '拿取' in step_by_label else 0) }},{{ '%.2f' % (step_by_label['正面涂黑'].duration if '正面涂黑' in step_by_label else 0) }},{{ '%.2f' % (step_by_label['翻转'].duration if '翻转' in step_by_label else 0) }},{{ '%.2f' % (step_by_label['反面涂黑'].duration if '反面涂黑' in step_by_label else 0) }},{{ '%.2f' % (step_by_label['放置'].duration if '放置' in step_by_label else 0) }}
{% endfor %}"""


def shot(page, name: str) -> Path:
    path = EVIDENCE_DIR / f"{name}.png"
    page.screenshot(path=str(path), full_page=True)
    print(f"  📸 {path.name}")
    return path


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            viewport={"width": 1600, "height": 1000},
            accept_downloads=True,
        )
        page = ctx.new_page()
        page.set_default_timeout(15000)

        print(f"[1] 打开数据管理页 (session_id={SESSION_ID})")
        page.goto("http://localhost:6001/#/data")
        time.sleep(3.0)
        shot(page, "01_data_page")

        print("[2] 点击「自定义导出 / 客户模板」按钮")
        # 按钮文案在 Data/index.vue 是 "自定义导出 / 客户模板"
        page.get_by_role("button", name="自定义导出 / 客户模板").click()
        time.sleep(1.5)
        shot(page, "02_dialog_open")

        print("[3] 选「单 session」(点 label 文字, 避开 ElementPlus radio 拦截 bug)")
        page.locator(".el-dialog label.el-radio:has-text('单 session')").click()
        time.sleep(0.4)

        print(f"[4] 选 session #{SESSION_ID}")
        # 先打开下拉
        page.locator(".el-dialog .el-select").first.click()
        time.sleep(1.0)
        # 找到含 #{SESSION_ID} 的项, 强制滚到可见再点
        item = page.locator(
            f".el-select-dropdown__item:has-text('#{SESSION_ID}')"
        ).first
        item.scroll_into_view_if_needed(timeout=8000)
        item.click(force=True)
        time.sleep(0.8)
        shot(page, "03_session_selected")

        # 包含 cycle 明细 (session 模式默认无该 checkbox 在左列; 实际它在 range 模式里).
        # 后端 _build_context 对 session 范围, 永远走 build_range_context, include_cycles
        # 默认 True 由前端 form.include_cycles=true 决定. 我们不勾它依然 True.

        print("[5] 粘客户模板到模板内容编辑器")
        # 模板内容是 textarea
        textarea = page.locator(".el-dialog textarea").first
        textarea.click()
        page.keyboard.press("Control+A")
        page.keyboard.press("Delete")
        time.sleep(0.3)
        # textarea fill 比 keyboard.type 快很多, 也不会被 IME 拦
        textarea.fill(CUSTOMER_TEMPLATE)
        time.sleep(0.5)

        print("[6] 触发右侧预览刷新")
        # 找预览刷新按钮, 文案是"刷新"
        refresh_btn = page.locator(".el-dialog button:has-text('刷新')")
        if refresh_btn.count() > 0:
            refresh_btn.first.click()
            time.sleep(1.5)
        else:
            print("  (没找到刷新按钮, 预览应该会自动 watch 触发)")
            time.sleep(2.0)
        shot(page, "04_after_paste_template")

        print("[7] 抓预览区文本 (跳过 textarea, 它是模板原文不是渲染结果)")
        # 渲染后的预览在 pre 标签或 monospace 容器中, 跳过 textarea
        preview_text = page.evaluate(
            """() => {
              const dialog = document.querySelector('.custom-export-dialog');
              if (!dialog) return { tag: 'NO_DIALOG', text: '' };
              // 跳过 textarea (那是用户输入区), 找 pre / 包含渲染结果的容器
              const candidates = dialog.querySelectorAll('pre, .preview-content, .preview-area, .rendered');
              for (const el of candidates) {
                const t = (el.textContent || '').trim();
                if (t.includes('会话ID,') || /^\\d+,2026/m.test(t)) {
                  return { tag: el.tagName + (el.className ? '.' + el.className : ''), text: t };
                }
              }
              // 兜底: 把整个 dialog text 抓出来看看
              return { tag: 'FALLBACK_DIALOG', text: dialog.innerText.slice(0, 5000) };
            }"""
        )
        print(f"    preview tag = {preview_text.get('tag')}")
        print(f"    preview text length = {len(preview_text.get('text', ''))}")

        rendered = preview_text.get("text", "")
        (EVIDENCE_DIR / "preview_rendered.txt").write_text(rendered, encoding="utf-8-sig")

        print("[8] 点「立即下载」")
        with page.expect_download(timeout=15000) as dl_info:
            page.get_by_role("button", name="立即下载").click()
        dl = dl_info.value
        downloaded_path = EVIDENCE_DIR / "downloaded.csv"
        dl.save_as(str(downloaded_path))
        shot(page, "05_after_download")
        print(f"    下载到: {downloaded_path}  ({downloaded_path.stat().st_size} bytes)")

        downloaded_text = downloaded_path.read_text(encoding="utf-8-sig")

        # ----------------- 客户视角断言 (以下载 CSV 为准) -----------------
        cols = ["拿取", "正面涂黑", "翻转", "反面涂黑", "放置"]
        cycle_rows = [
            ln for ln in downloaded_text.splitlines()
            if ln and ln.split(",")[0].strip().isdigit()
        ]
        verdict = {
            "downloaded_size_bytes": downloaded_path.stat().st_size,
            "downloaded_has_session_header": "会话信息" in downloaded_text,
            "downloaded_cycle_table_header": "周期序号" in downloaded_text,
            "downloaded_columns_all_present": all(c in downloaded_text for c in cols),
            "downloaded_has_arrow_sequence": "->" in downloaded_text,
            "downloaded_cycle_row_count": len(cycle_rows),
            "downloaded_expected_cycle_row_count_8": len(cycle_rows) == 8,
            "downloaded_has_ng_row": "不良" in downloaded_text,
            "downloaded_has_event_name": "缺步骤NG" in downloaded_text,
            "downloaded_no_None_literal": "None" not in downloaded_text,
            "downloaded_has_two_decimal_duration": "0.22" in downloaded_text,
        }
        (EVIDENCE_DIR / "verdict.json").write_text(
            json.dumps(verdict, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        print("\n[verdict]")
        for k, v in verdict.items():
            mark = "✅" if (v if isinstance(v, bool) else v > 0) else "❌"
            print(f"  {mark} {k}: {v}")

        passed = all(v if isinstance(v, bool) else v > 0 for v in verdict.values())
        ctx.close()
        browser.close()
        return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
