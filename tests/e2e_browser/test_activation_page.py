# -*- coding: utf-8 -*-
"""激活页 CI 回归（v3.47 授权后端后台启动交互）。

对应客户反馈: 授权导入后按钮一直转圈 (老版本把后端冷启动串在 import-license
IPC 里, 启动失败时 license-activated 永远不来, 前端无限等待)。

激活页只在 Electron 壳内出现, 这里用 add_init_script 注入 mock electronAPI
模拟主进程 v3.47 的新契约 (importLicense 立即返回验签结果, 启动结果走
license-activated / license-backend-start-failed 事件), 锁定前端三个状态:
starting 等待态 → 启动失败错误态 → 激活成功跳主页。

人眼验收版 (可见浏览器 + 视频三件套) 见
tests/uat/uat_20260807_activation_backend_async_start.py。
"""
import time

MOCK_ELECTRON_API = """
window.__cb = { activated: null, startFailed: null };
window.__licensed = false;
window.electronAPI = {
  isElectron: true,
  getAppInfo: async () => ({ version: 'e2e' }),
  getLicenseStatus: async () => ({ valid: window.__licensed, machineId: 'TJ-E2E0-MOCK-0000' }),
  importLicense: async () => {
    await new Promise(r => setTimeout(r, 200));
    window.__licensed = true;
    return { valid: true, message: 'ok' };
  },
  onLicenseActivated: (cb) => { window.__cb.activated = cb; },
  onLicenseBackendStartFailed: (cb) => { window.__cb.startFailed = cb; },
};
"""

IMPORT_BTN = "button:has-text('Import License File')"


def _open_activation(context, base_url):
    context.add_init_script(MOCK_ELECTRON_API)
    page = context.new_page()
    page.goto(f"{base_url}/#/activation", wait_until="domcontentloaded")
    page.wait_for_selector(IMPORT_BTN, timeout=10000)
    return page


def test_activation_import_enters_starting_state(context, base_url):
    """导入授权后立即进入'系统启动中'等待态, 不再依赖后端启动完成才反馈。"""
    page = _open_activation(context, base_url)
    page.locator(IMPORT_BTN).click()
    page.wait_for_timeout(1000)
    body = page.evaluate("document.body.innerText")
    assert "Activation successful" in body and "starting" in body
    assert page.locator(IMPORT_BTN).is_disabled(), "启动等待期按钮应 disabled"
    assert "#/activation" in page.url, "未收到 license-activated 前不应跳主页"


def test_activation_backend_start_failed_shows_error(context, base_url):
    """后端启动失败事件到达 → 显示错误并解除等待态 (治'无限转圈')。"""
    page = _open_activation(context, base_url)
    page.locator(IMPORT_BTN).click()
    page.wait_for_timeout(1000)
    page.evaluate("window.__cb.startFailed({ message: 'Backend startup timeout' })")
    page.wait_for_timeout(500)
    body = page.evaluate("document.body.innerText")
    assert "backend failed to start" in body
    assert "Backend startup timeout" in body
    assert page.locator(IMPORT_BTN).is_enabled(), "失败后按钮应恢复可点"


def test_activation_activated_event_navigates_home(context, base_url):
    """license-activated 事件到达 → 自动跳离激活页进主应用。"""
    page = _open_activation(context, base_url)
    page.locator(IMPORT_BTN).click()
    page.wait_for_timeout(1000)
    page.evaluate("window.__cb.activated()")
    deadline = time.time() + 8
    while time.time() < deadline and "#/activation" in page.url:
        page.wait_for_timeout(300)
    assert "#/activation" not in page.url, "license-activated 后应跳离激活页"
