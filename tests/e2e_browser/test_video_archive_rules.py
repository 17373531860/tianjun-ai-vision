"""录像归档 (v3.53 一期) 浏览器 E2E：Data 页归档卡 + 规则 CRUD 双向验证。

覆盖：
  1. 存储与清理 Tab 出现「录像归档」卡, 点开配置弹窗
  2. 弹窗内新建规则 → 表格出现 + 后端 GET 落库双向验证
  3. UI 开关切换 → 后端 enabled 翻转 (双向)
  4. 目录护栏: 黑名单目录被 400 拒绝, UI 弹错、规则不落库
  5. UI 删除规则 → 后端消失

依赖: backend/frontend 已启动 (默认 8001/6001, 可用 E2E_API_URL/E2E_BASE_URL 覆盖)。
"""
from __future__ import annotations

import requests
from playwright.sync_api import Page, expect

from .conftest import E2E_PREFIX

RULE_NAME = f"{E2E_PREFIX}归档规则"
DEST_DIR = "/tmp/tj_e2e_video_archive"


def _rules(api_url):
    r = requests.get(f"{api_url}/api/v1/export/video-archive/rules", timeout=5)
    assert r.status_code == 200
    return r.json()["items"]


def _open_archive_dialog(page: Page, base_url: str):
    page.goto(f"{base_url}/#/data", wait_until="domcontentloaded", timeout=15000)
    # 切到「存储与清理」Tab
    page.get_by_role("tab", name="存储与清理").click()
    card = page.locator("[data-test='video-archive-card']")
    expect(card).to_be_visible(timeout=10000)
    card.get_by_role("button", name="配置录像归档").click()
    dialog = page.locator(".va-dialog")
    expect(dialog).to_be_visible(timeout=5000)
    return dialog


def test_archive_card_and_rule_crud(page: Page, base_url, api_url):
    dialog = _open_archive_dialog(page, base_url)

    # ---- 状态条渲染 (成功/失败计数 + 队列) ----
    expect(dialog.locator(".va-status-bar")).to_contain_text("队列")

    # ---- 新建规则 ----
    dialog.get_by_role("button", name="新建规则").click()
    editor = page.locator(".el-dialog").filter(has_text="新建归档规则")
    expect(editor).to_be_visible(timeout=5000)
    editor.get_by_placeholder("如：NG 录像归档到质量部网盘").fill(RULE_NAME)
    editor.get_by_placeholder("如 D:\\NG归档 或 \\\\server\\quality\\videos").fill(DEST_DIR)
    editor.get_by_role("button", name="保存").click()
    expect(editor).to_be_hidden(timeout=5000)

    # 表格出现
    expect(dialog.locator(".el-table").first).to_contain_text(RULE_NAME, timeout=5000)

    # 后端落库双向验证 (不是只看 toast)
    rows = [x for x in _rules(api_url) if x["name"] == RULE_NAME]
    assert len(rows) == 1, "规则应落库"
    rule = rows[0]
    assert rule["enabled"] is True
    assert rule["dest_dir"] == DEST_DIR
    assert rule["result_filter"] == "ng_only"  # 默认仅 NG

    # ---- UI 开关 → 后端 enabled 翻转 ----
    row = dialog.locator(".el-table__row").filter(has_text=RULE_NAME)
    row.locator(".el-switch").click()
    page.wait_for_timeout(600)
    assert _rules(api_url)[0]["enabled"] is False or \
        [x for x in _rules(api_url) if x["name"] == RULE_NAME][0]["enabled"] is False

    # ---- UI 删除 → 后端消失 ----
    row.get_by_role("button", name="删除").click()
    box = page.locator(".el-message-box")
    box.wait_for(state="visible", timeout=5000)
    box.locator(".el-button--primary").click()
    page.wait_for_timeout(600)
    assert not [x for x in _rules(api_url) if x["name"] == RULE_NAME], "删除后不应残留"


def test_archive_dest_dir_guard_rejects_blacklist(page: Page, base_url, api_url):
    dialog = _open_archive_dialog(page, base_url)
    dialog.get_by_role("button", name="新建规则").click()
    editor = page.locator(".el-dialog").filter(has_text="新建归档规则")
    expect(editor).to_be_visible(timeout=5000)
    bad_name = f"{E2E_PREFIX}坏目录规则"
    editor.get_by_placeholder("如：NG 录像归档到质量部网盘").fill(bad_name)
    editor.get_by_placeholder("如 D:\\NG归档 或 \\\\server\\quality\\videos").fill("/etc")
    editor.get_by_role("button", name="保存").click()

    # UI 弹出护栏错误, 编辑器不关闭
    expect(page.locator(".el-message--error")).to_contain_text("禁止", timeout=5000)
    expect(editor).to_be_visible()

    # 后端确实没落库
    assert not [x for x in _rules(api_url) if x["name"] == bad_name], "黑名单目录不应落库"


def test_archive_evidence_and_remote_dest(page: Page, base_url, api_url):
    """二~四期 UI: 证据能力开关 + 远端 FTP 目的地, UI 保存 → 后端落库双向验证
    (密码密文入库、API 回显打码)。"""
    name = f"{E2E_PREFIX}证据远端规则"
    dialog = _open_archive_dialog(page, base_url)
    dialog.get_by_role("button", name="新建规则").click()
    editor = page.locator(".el-dialog").filter(has_text="新建归档规则")
    expect(editor).to_be_visible(timeout=5000)
    editor.get_by_placeholder("如：NG 录像归档到质量部网盘").fill(name)

    # 目的地类型切 FTP → 动态连接字段出现
    editor.locator(".el-form-item", has_text="目的地类型").locator(".el-select").click()
    page.locator(".el-select-dropdown__item", has_text="FTP").first.click()
    editor.get_by_placeholder("192.168.1.100").first.fill("10.0.0.9")
    pwd = editor.locator(".el-form-item", has_text="密码").locator("input").first
    pwd.fill("e2e_secret")

    # 证据能力: 附带关键帧 + 事件切片
    editor.locator(".el-form-item", has_text="附带 NG 关键帧快照").locator(".el-switch").click()
    editor.locator(".el-form-item", has_text="录像变换").locator(".el-select").click()
    page.locator(".el-select-dropdown__item", has_text="事件切片").first.click()

    editor.get_by_role("button", name="保存").click()
    expect(editor).to_be_hidden(timeout=5000)

    # 后端双向验证
    rows = [x for x in _rules(api_url) if x["name"] == name]
    assert len(rows) == 1, "规则应落库"
    rule = rows[0]
    try:
        assert rule["dest_type"] == "ftp"
        assert rule["dest_config"]["host"] == "10.0.0.9"
        assert rule["dest_config"]["password"] == "******", "API 回显必须打码"
        assert rule["attach_keyframe"] is True
        assert rule["transform"] == "clip_tail"
        # 表格里目的地列显示 ftp 类型标签 + 关键帧/切片徽标
        row = dialog.locator(".el-table__row").filter(has_text=name)
        expect(row).to_contain_text("ftp")
        expect(row).to_contain_text("关键帧")
    finally:
        requests.delete(
            f"{api_url}/api/v1/export/video-archive/rules/{rule['id']}",
            timeout=5)


def test_archive_status_endpoint_reachable(api_url):
    """状态端点回 200 且带核心观测字段 (Data 页卡片数据源)。"""
    r = requests.get(f"{api_url}/api/v1/export/video-archive/status", timeout=5)
    assert r.status_code == 200
    body = r.json()
    for key in ("worker_alive", "queue_depth", "spool_depth",
                "success", "failed", "rules_total", "rules_enabled"):
        assert key in body, f"status 缺字段 {key}"
