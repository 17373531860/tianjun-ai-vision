"""v3.6.2 会话标识 + session_end 自动导出的【真UI 点击】验收。

铁律（见 .claude/skills/run-tests/SKILL.md「带 UI 的功能改动必须走 E2E 点击验证」）：
对每一个新 UI 入口必须真实点击 → 后端 GET/DB 反映出预期变化。

覆盖：
  1. Monitor《会话 ID》输入 + 点「开始」 → 拦截 axios 请求体, 验证 session_name 字段
  2. Data 页 session-card《重命名》黄色按钮 → 弹窗 → 保存 → 后端 GET 验证 name 改变
  3. RealtimeRulesDialog《新建规则》→ trigger_event 下拉 → session_end 选项可选
  4. CustomExportDialog 范围选 Session → Session 下拉可搜可选 + label 含「标识/UUID」

要求：backend (8001) 启动时 RUNTIME_MODE=test，frontend (6001) 已 dev。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
import uuid
from pathlib import Path

import pytest
import requests

from .pages import DataPage, MonitorPage


SHOTS_DIR = Path(os.environ.get("SAT_SHOTS_DIR", "/tmp/sat_full_workflow_shots"))
E2E_PROJECT_PREFIX = "__e2e_project_"


def _post(api_url: str, path: str, **kwargs) -> requests.Response:
    return requests.post(f"{api_url}{path}", timeout=10, **kwargs)


def _get(api_url: str, path: str, **kwargs) -> requests.Response:
    return requests.get(f"{api_url}{path}", timeout=10, **kwargs)


def _delete(api_url: str, path: str, **kwargs) -> requests.Response:
    return requests.delete(f"{api_url}{path}", timeout=10, **kwargs)


def _ensure_clean(api_url: str, channel: int = 0):
    _post(api_url, f"/api/v1/source/detection/stop?channel={channel}")
    _post(api_url, f"/api/v1/test/synthetic/stop?channel={channel}")


def _start_synthetic(api_url: str, scenario: str = "ok_sequential_cycle.json",
                     channel: int = 0, project_id: int = None):
    payload = {"scenario": scenario, "channel": channel, "with_project": True}
    if project_id is not None:
        payload["project_id"] = project_id
    r = _post(api_url, f"/api/v1/test/synthetic/start", json=payload)
    assert r.status_code == 200, f"start synthetic 失败: {r.text[:300]}"


def _create_and_activate_project(api_url: str, with_default_model_id: bool = False) -> dict:
    """创建一个 __e2e_project_<uuid> 真实项目, 激活。
    with_default_model_id=True 时塞一个假的 model_id, 让 FE Monitor 启动检查放行。
    """
    name = f"{E2E_PROJECT_PREFIX}{uuid.uuid4().hex[:8]}"
    payload = {
        "name": name,
        "task_type": "detect",
        "logic_mode": "sequential",
        "pipeline_config": {},
        "steps_config": [
            {"label": "step_a", "threshold": 0.3, "min_frames": 1,
             "gap_tolerance": 5, "color": "#1976d2"},
        ],
        "events_config": [],
        "alarm_config": {},
        "detection_config": {},
        "data_config": {},
        "counters_config": [],
    }
    if with_default_model_id:
        payload["default_model_id"] = 99999
    r = _post(api_url, "/api/v1/projects", json=payload)
    assert r.status_code == 201, f"create project 失败: {r.text[:300]}"
    proj = r.json()
    r2 = _post(api_url, f"/api/v1/projects/{proj['id']}/activate")
    assert r2.status_code == 200, f"activate project 失败: {r2.text[:300]}"
    return proj


def _list_projects(api_url: str) -> list:
    r = _get(api_url, "/api/v1/projects")
    if r.status_code != 200:
        return []
    j = r.json()
    return j.get("items", []) if isinstance(j, dict) else j


def _delete_e2e_projects(api_url: str):
    try:
        for p in _list_projects(api_url):
            if (p.get("name") or "").startswith(E2E_PROJECT_PREFIX):
                _delete(api_url, f"/api/v1/projects/{p['id']}")
    except Exception as e:
        print(f"[cleanup] projects 清理失败: {e}")


def _get_active_project_id(api_url: str):
    """记下当前 is_active=True 的非 __e2e_ 真实项目 id, 测试后恢复"""
    for p in _list_projects(api_url):
        if p.get("is_active") and not (p.get("name") or "").startswith(E2E_PROJECT_PREFIX):
            return p["id"]
    return None


def _restore_active_project(api_url: str, original_id: int = None):
    """删完 __e2e_ 项目后, 把原来的 active 项目恢复; 没有的话挑任意一个非 __e2e_ 的激活,
    避免后续 Data 测试看到「请先选择一个项目」空状态"""
    try:
        if original_id:
            r = _post(api_url, f"/api/v1/projects/{original_id}/activate")
            if r.status_code == 200:
                return
        # fallback: 找首个非 __e2e_ 项目激活
        for p in _list_projects(api_url):
            if not (p.get("name") or "").startswith(E2E_PROJECT_PREFIX):
                _post(api_url, f"/api/v1/projects/{p['id']}/activate")
                return
    except Exception as e:
        print(f"[cleanup] 恢复 active project 失败: {e}")


@pytest.fixture
def runtime_mode_required(api_url):
    r = _get(api_url, "/api/v1/test/synthetic/state?channel=0")
    if r.status_code == 404:
        pytest.skip("backend 未启用 RUNTIME_MODE=test，无 synthetic 路由")


@pytest.fixture
def clean_state(api_url):
    original_active = _get_active_project_id(api_url)
    _ensure_clean(api_url)
    yield
    _ensure_clean(api_url)
    _delete_e2e_projects(api_url)
    _restore_active_project(api_url, original_active)


# ============================================================================
# 1) Monitor《会话 ID》输入框 + 真点「开始」按钮 → 拦截 axios 请求体, 验证 session_name
# 用 page.route 拦截掉模型解析和 detection/start, 这样不需要真模型, 就能验证
# UI 输入 → Vue ref → axios body 整条链路。
# ============================================================================

def test_ui_monitor_fill_session_name_then_click_start(
    page, base_url, api_url, runtime_mode_required, clean_state,
):
    SHOTS_DIR.mkdir(parents=True, exist_ok=True)
    custom_name = f"E2E-UI-{int(time.time())}"

    # 前置：建一个真项目 (带假 default_model_id) 并激活, Navbar 自动选中 → currentProject 不为空
    proj = _create_and_activate_project(api_url, with_default_model_id=True)
    proj_id = proj["id"]

    captured_starts = []  # 收集 POST /source/detection/start 的请求体

    def handle_start(route, request):
        # 复制请求体, 然后 fulfill 假成功响应
        try:
            captured_starts.append(request.post_data)
        except Exception as e:
            captured_starts.append(f"<read failed: {e}>")
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "status": "success",
                "message": "mocked by E2E",
                "session_id": 88888,
                "session_uuid": "mocked-uuid",
                "session_name": "<from_request>",
            }),
        )

    def handle_resolve(route, request):
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"path": "/tmp/__e2e_fake.pt", "fallback": False}),
        )

    def handle_model_detail(route, request):
        # getModelDetail fallback path
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "id": 99999, "name": "__e2e_fake", "version": "v0",
                "file_path": "/tmp/__e2e_fake.pt",
            }),
        )

    page.route("**/api/v1/source/detection/start**", handle_start)
    page.route("**/api/v1/models/*/resolve-path**", handle_resolve)
    # 仅匹配 /models/<id> 这种纯数字尾段的, 不匹配 /models/formats/...
    page.route("**/api/v1/models/99999", handle_model_detail)

    mp = MonitorPage(page, base_url).goto()
    mp.sleep(3.0)  # 给 Navbar loadProjects + setCurrentProject 时间

    assert mp.has_session_name_input(), "Monitor 必须有《会话 ID》输入框"

    # 真点 UI: 填名字 → 点开始
    clicked = mp.click_start_with_session_name(custom_name)
    if not clicked:
        mp.screenshot(str(SHOTS_DIR / "monitor_start_disabled.png"))
        pytest.fail(
            "「开始」按钮 disabled, 说明 currentProject 未被 Navbar 自动选中, 或 default_model_id 校验阻断。"
        )

    mp.sleep(2.0)  # 等 axios POST 发出

    mp.screenshot(str(SHOTS_DIR / "monitor_session_name_after_start.png"))

    assert captured_starts, (
        "点击「开始」后没拦到 POST /source/detection/start, 说明 FE 没真的发出请求。"
        "可能在前置校验阶段就 return 了 (modelId / sessionNameError 等)。"
    )

    body_str = captured_starts[0]
    if isinstance(body_str, bytes):
        body_str = body_str.decode("utf-8", errors="ignore")
    assert body_str, f"请求体为空: {captured_starts!r}"
    body = json.loads(body_str)
    assert body.get("session_name") == custom_name, (
        f"FE axios 提交的 session_name 不等于 UI 输入框的值！"
        f"\n期望: {custom_name!r}\n实际 body={body!r}\n"
        f"(说明 sessionName ref → apiStartDetection 第 5 个参数 → axios body 链路有 BUG)"
    )


# ============================================================================
# 2) Data 页 session-card 重命名按钮 → ElMessageBox 弹窗 → 后端 PATCH
# ============================================================================

def test_ui_data_rename_session_card(
    page, base_url, api_url, runtime_mode_required, clean_state,
):
    SHOTS_DIR.mkdir(parents=True, exist_ok=True)
    initial_name = f"INIT-{int(time.time())}"
    renamed_name = f"RENAMED-{int(time.time())}"

    # 前置: 必须激活一个项目, 否则 Data 页显示「请先选择一个项目」空状态, 不渲染 tabs/日期选择器
    proj = _create_and_activate_project(api_url)
    proj_id = proj["id"]
    # 建一个带 name 的真实 session, 关联到该项目 (否则 session.project_id=-1, 当前项目过滤就看不见)
    _start_synthetic(api_url, project_id=proj_id)
    r = _post(
        api_url,
        "/api/v1/source/detection/start?channel=0",
        json={"conf": 0.25, "iou": 0.45, "session_name": initial_name},
    )
    assert r.status_code == 200, f"start_detection 失败: {r.text[:300]}"
    body = r.json()
    session_id = body.get("session_id")
    assert session_id, f"未拿到 session_id (说明 mgr.project_config 缺 id): {body!r}"
    time.sleep(1.5)
    # 必须先停 synthetic (不然推理线程会调用 _ensure_session_active 自动新建无名 session 干扰列表)
    _post(api_url, "/api/v1/test/synthetic/stop?channel=0")
    time.sleep(0.5)
    _post(api_url, "/api/v1/source/detection/stop?channel=0")
    time.sleep(0.8)

    today = dt.date.today().isoformat()

    dp = DataPage(page, base_url).goto()
    dp.sleep(2.0)
    assert dp.has_data_center(), "Data 页应显示「数据中心」"

    dp.pick_date(today)
    dp.sleep(1.5)
    assert dp.has_session_card(), "选今日后应有 session-card"

    # 列表里可能有多个 card (我们的 + 推理线程自动 _ensure_session_active 产生的孤儿)
    # 直接通过 name 文本定位我们的那张
    assert dp.session_card_has_text(initial_name), (
        f"session-card 列表里应能看到刚建的 {initial_name}, 但找不到。"
        f"\n后端 session_id={session_id}, /sessions/{session_id} 内容=" + str(_get(api_url, f"/api/v1/data/sessions/{session_id}").json())
    )

    # 真点 UI: 在含 initial_name 的卡片上点重命名
    ok = dp.click_rename_on_session_card_with_text(initial_name)
    assert ok, f"未能在含 {initial_name!r} 的 card 上点击重命名按钮"
    dp.sleep(0.5)
    dp.fill_rename_session_dialog(renamed_name, confirm=True)
    dp.sleep(1.5)

    # 验证后端 PATCH 生效
    r2 = _get(api_url, f"/api/v1/data/sessions/{session_id}")
    assert r2.status_code == 200, f"GET session {session_id} 失败: {r2.text[:200]}"
    backend_name = r2.json().get("name")
    assert backend_name == renamed_name, (
        f"后端 session.name 没被 UI 重命名生效！期望 {renamed_name}, 实际 {backend_name!r}"
    )

    # 验证 UI 也刷新
    dp.sleep(0.8)
    assert dp.session_card_has_text(renamed_name), (
        f"UI session-card 应刷新出新标识 {renamed_name}"
    )
    dp.screenshot(str(SHOTS_DIR / "data_session_renamed.png"))


# ============================================================================
# 3) RealtimeRulesDialog 新建规则 → trigger_event 下拉 → session_end 可选
# ============================================================================

def test_ui_realtime_rules_session_end_option_enabled(
    page, base_url, api_url, runtime_mode_required, clean_state,
):
    SHOTS_DIR.mkdir(parents=True, exist_ok=True)

    # 前置: 必须激活一个项目, 否则 Data 页不渲染 tabs
    _create_and_activate_project(api_url)

    dp = DataPage(page, base_url).goto()
    dp.sleep(2.5)

    dp.switch_tab("数据导出")
    dp.sleep(0.5)

    dp.open_realtime_rules_dialog()
    dp.sleep(0.6)
    dp.click_realtime_create_rule()
    dp.sleep(0.8)

    options = dp.get_realtime_trigger_options()
    dp.screenshot(str(SHOTS_DIR / "realtime_trigger_dropdown.png"))

    cycle_keys = [k for k in options if "cycle_end" in k]
    session_keys = [k for k in options if "session_end" in k]

    assert cycle_keys, f"trigger_event 下拉必须有 cycle_end 选项, 实际 keys={list(options)!r}"
    assert session_keys, f"trigger_event 下拉必须有 session_end 选项, 实际 keys={list(options)!r}"
    for k in session_keys:
        assert options[k] is True, (
            f"session_end 选项 {k!r} 必须可选 (enabled), 但显示为 disabled。"
            f"说明 RealtimeRulesDialog.vue 的 disabled 属性没去干净！"
        )

    ok = dp.select_realtime_trigger("session_end")
    assert ok, "session_end 选项点击失败 (可能仍 disabled 或文本改了)"

    dp.screenshot(str(SHOTS_DIR / "realtime_session_end_selected.png"))

    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    page.keyboard.press("Escape")


# ============================================================================
# 4) CustomExportDialog 范围 Session → Session 下拉可搜可选
# ============================================================================

def test_ui_custom_export_session_dropdown_searchable(
    page, base_url, api_url, runtime_mode_required, clean_state,
):
    SHOTS_DIR.mkdir(parents=True, exist_ok=True)
    name_tag = f"EXPORT-PICKER-{int(time.time())}"

    # 前置: 必须激活一个项目, 否则 Data 页不渲染 tabs
    _create_and_activate_project(api_url)
    _start_synthetic(api_url)
    r = _post(
        api_url,
        "/api/v1/source/detection/start?channel=0",
        json={"conf": 0.25, "iou": 0.45, "session_name": name_tag},
    )
    assert r.status_code == 200, f"start_detection 失败: {r.text[:300]}"
    assert r.json().get("session_id"), f"未拿到 session_id: {r.json()!r}"
    time.sleep(1.5)
    _post(api_url, "/api/v1/source/detection/stop?channel=0")
    time.sleep(0.5)

    dp = DataPage(page, base_url).goto()
    dp.sleep(2.5)

    dp.switch_tab("数据导出")
    dp.sleep(0.5)

    dp.open_custom_export_dialog()
    dp.sleep(0.7)
    dp.select_custom_export_scope_session()
    dp.sleep(0.5)
    n_opts = dp.open_export_session_select()
    dp.screenshot(str(SHOTS_DIR / "export_session_dropdown_open.png"))

    assert n_opts > 0, "Session 下拉应至少有 1 个选项"

    opt_texts = dp.get_export_session_options(max_n=20)
    has_id_prefix = any(t.startswith("#") for t in opt_texts)
    assert has_id_prefix, f"Session 下拉选项 label 应以 # 开头 (含 ID), 实际: {opt_texts!r}"

    matching = [t for t in opt_texts if name_tag in t]
    assert matching, (
        f"Session 下拉里应能搜到刚建的 {name_tag}, 但前 20 项都没有: {opt_texts!r}"
    )

    n_filtered = dp.filter_export_session(name_tag)
    assert n_filtered >= 1, f"按 {name_tag} 过滤后应至少 1 项, 实际 {n_filtered}"
    dp.screenshot(str(SHOTS_DIR / "export_session_dropdown_filtered.png"))

    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    page.keyboard.press("Escape")
