"""客户老胡场景 ▸ 系统预设「session 5 步产线 CSV」端到端实测

模拟客户最理想的操作流 (无须再粘任何 Jinja 代码):
  1. 打开 /data
  2. 点「自定义导出 / 客户模板」
  3. 数据范围: 单 session → 选 #843
  4. 模板下拉: 选「session 5 步产线 CSV (...)」 — 从系统预设里挑
  5. 立即下载 → 拿 CSV → 断言内容

断言点和 customer_export_uat.py 完全一致 (11 条), 但走的路径不一样:
  之前是粘贴自定义模板, 这次是选预设. 验证预设落库后客户能"一键"用上.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parents[2]
EVIDENCE_DIR = REPO / "evidence" / "customer_preset_2026-05-12"
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

SESSION_ID = int(sys.argv[1]) if len(sys.argv) > 1 else 843
PRESET_KEYWORD = "5 步产线"  # 用关键字定位预设, 跟 name 里有 "session 5 步产线 CSV (...)"


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

        print(f"[1] 打开 /data (session_id={SESSION_ID})")
        page.goto("http://localhost:6001/#/data")
        time.sleep(3.0)
        shot(page, "01_data_page")

        print("[2] 点「自定义导出 / 客户模板」")
        page.get_by_role("button", name="自定义导出 / 客户模板").click()
        time.sleep(1.5)
        shot(page, "02_dialog_open")

        print("[3] 范围: 单 session")
        page.locator(".el-dialog label.el-radio:has-text('单 session')").click()
        time.sleep(0.4)

        print(f"[4] 选 session #{SESSION_ID}")
        page.locator(".el-dialog .el-select").first.click()
        time.sleep(1.0)
        item = page.locator(
            f".el-select-dropdown__item:has-text('#{SESSION_ID}')"
        ).first
        item.scroll_into_view_if_needed(timeout=8000)
        item.click(force=True)
        time.sleep(0.8)
        shot(page, "03_session_selected")

        print(f"[5] 模板下拉: 选含 '{PRESET_KEYWORD}' 的预设")
        # 中间一列的"选择模板"下拉, 它是 dialog 里第 2 个 el-select
        template_select = page.locator(".el-dialog .el-select").nth(1)
        template_select.click()
        time.sleep(1.0)
        # 等下拉浮层出现, 找含关键字的 option
        preset_option = page.locator(
            f".el-select-dropdown__item:has-text('{PRESET_KEYWORD}')"
        ).first
        preset_option.scroll_into_view_if_needed(timeout=8000)
        preset_option.click(force=True)
        time.sleep(1.0)
        shot(page, "04_preset_selected")

        # 验一下下拉确实选中了 (visible 区或 textarea 应该已填上模板正文)
        textarea_value = page.locator(".el-dialog textarea").first.input_value()
        textarea_has_content = "周期序号" in textarea_value and "拿取" in textarea_value
        print(f"    模板内容已自动填入? {textarea_has_content}  (len={len(textarea_value)})")

        print("[6] 点「立即下载」")
        with page.expect_download(timeout=15000) as dl_info:
            page.get_by_role("button", name="立即下载").click()
        dl = dl_info.value
        downloaded_path = EVIDENCE_DIR / "downloaded.csv"
        dl.save_as(str(downloaded_path))
        shot(page, "05_after_download")
        print(f"    下载: {downloaded_path}  ({downloaded_path.stat().st_size} bytes)")

        downloaded_text = downloaded_path.read_text(encoding="utf-8-sig")

        # 客户视角断言 (跟自定义模板那次完全一致 + 多一个预设填充检查)
        cols = ["拿取", "正面涂黑", "翻转", "反面涂黑", "放置"]
        cycle_rows = [
            ln for ln in downloaded_text.splitlines()
            if ln and ln.split(",")[0].strip().isdigit()
        ]
        verdict = {
            "preset_auto_filled_into_textarea": textarea_has_content,
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
        all_pass = True
        for k, v in verdict.items():
            ok = v if isinstance(v, bool) else v > 0
            all_pass = all_pass and ok
            mark = "✅" if ok else "❌"
            print(f"  {mark} {k}: {v}")

        ctx.close()
        browser.close()
        return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
