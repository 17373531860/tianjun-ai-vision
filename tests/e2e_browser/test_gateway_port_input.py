"""网关编辑弹窗·数据库端口输入框治理 E2E（2026-07 萍乡现场回归）。

现场事故：端口框太窄+带步进钮，粘贴出超长数字看不见，测试连接报
"int too large / 加密模块加载失败" 天书错误。治理后：
  - 端口框独立窄 label，输入区可见（宽度 >= 60px）
  - 无步进钮（is-without-controls）
  - 硬限 1~65535，超长粘贴钳到 65535
"""
from __future__ import annotations


def _open_db_gateway_dialog(page, base_url):
    page.goto(f"{base_url}/#/mes", wait_until="domcontentloaded", timeout=15000)
    page.wait_for_timeout(2000)
    page.locator("text=外部对接").first.click(timeout=8000)
    page.wait_for_timeout(1000)
    page.locator("button:has-text('新建连接')").first.click(timeout=5000)
    page.wait_for_timeout(600)
    page.locator(".el-dialog >> text=数据库直写").first.click(timeout=3000)
    page.wait_for_timeout(500)


def test_gateway_db_port_input_visible_and_clamped(page, base_url):
    _open_db_gateway_dialog(page, base_url)
    port_input = page.locator(".el-dialog .el-input-number input").first

    # 1) 上限 65535 已挂
    assert port_input.get_attribute("aria-valuemax") == "65535"

    # 2) 输入区肉眼可见（现场事故时只有 ~25px）
    box = port_input.bounding_box()
    assert box and box["width"] >= 60, f"端口输入框过窄: {box}"

    # 3) 无步进钮
    wrapper = page.locator(".el-dialog .el-input-number").first
    assert "is-without-controls" in (wrapper.get_attribute("class") or "")

    # 4) 超长粘贴被钳制, 正常端口原样保留
    port_input.fill("152361523615236")
    port_input.blur()
    page.wait_for_timeout(300)
    assert port_input.input_value() == "65535"
    port_input.fill("15236")
    port_input.blur()
    page.wait_for_timeout(300)
    assert port_input.input_value() == "15236"

    # 收尾: 关弹窗不留脏数据
    page.keyboard.press("Escape")
