"""MediaPipe 骨架自定义纯色样式 E2E（v3.32.0）。

覆盖:
  1. 性能设置 Tab 有「自定义骨架样式」开关（默认关 = 官方花色老行为）
  2. UI 开启开关后出现姿态/手部各自的取色器 + 线宽输入
  3. UI 改线宽 → 后端流配置接口回读一致（UI→后端落库）
  4. 接口写颜色 → 刷新页面 UI 回读一致（后端→UI 回读）
测后还原原始配置, 不污染用户环境。
"""
from __future__ import annotations

import time

import pytest


MP_KEYS = [
    "mediapipe_enabled", "mediapipe_pose", "mediapipe_hands",
    "mediapipe_confidence", "mediapipe_interval",
    "mediapipe_custom_style", "mediapipe_pose_color", "mediapipe_pose_thickness",
    "mediapipe_hands_color", "mediapipe_hands_thickness",
    # 关键点/连线颜色分开配
    "mediapipe_pose_point_color", "mediapipe_hands_point_color",
]


@pytest.fixture
def stream_config_guard(api_helper):
    """记录并在测后还原流配置（只回写 MediaPipe 相关 + 必填字段）。"""
    orig = api_helper.get("/api/v1/source/stream/config").json()
    yield orig
    payload = {
        "frame_limit_enabled": orig["frame_limit_enabled"],
        "target_stream_fps": orig["target_stream_fps"],
        "use_half": orig["use_half"],
    }
    payload.update({k: orig[k] for k in MP_KEYS if k in orig})
    api_helper.post("/api/v1/source/stream/config", json=payload)


def _goto_performance_tab(page, base_url):
    page.goto(f"{base_url}/#/settings", wait_until="domcontentloaded", timeout=15000)
    page.wait_for_selector(".el-tabs__item", timeout=10000)
    page.click("div.el-tabs__item:has-text('性能设置')")
    page.wait_for_selector("span:has-text('MediaPipe 骨架叠加')", timeout=10000)


def _row(page, label):
    return page.locator(
        "div.flex.items-center.justify-between",
        has=page.locator("span", has_text=label),
    ).first


def _ensure_switch(page, label, on: bool):
    row = _row(page, label)
    checked = row.locator(".el-switch.is-checked").count() > 0
    if checked != on:
        row.locator(".el-switch").click()
        time.sleep(1.0)


def test_custom_style_block_present(page, base_url, api_helper, stream_config_guard):
    """开关存在; 开启后展开 6 个样式配置项（连线/关键点颜色分开配）。"""
    _goto_performance_tab(page, base_url)
    row = _row(page, "自定义骨架样式")
    assert row.count() > 0, "性能设置应有「自定义骨架样式」开关"

    _ensure_switch(page, "启用 MediaPipe 叠加", True)
    _ensure_switch(page, "自定义骨架样式", True)
    for label in ("姿态连线颜色", "姿态关键点颜色", "姿态线条粗细",
                  "手部连线颜色", "手部关键点颜色", "手部线条粗细"):
        assert page.locator("span", has_text=label).first.is_visible(), f"缺少配置项: {label}"


def test_thickness_ui_to_backend(page, base_url, api_helper, stream_config_guard):
    """UI 改姿态/手部线宽 → 后端接口回读一致。"""
    _goto_performance_tab(page, base_url)
    _ensure_switch(page, "启用 MediaPipe 叠加", True)
    _ensure_switch(page, "自定义骨架样式", True)

    for label, value in (("姿态线条粗细", 7), ("手部线条粗细", 3)):
        inp = _row(page, label).locator(".el-input-number input").first
        inp.fill(str(value))
        inp.press("Enter")
        time.sleep(1.2)

    cfg = api_helper.get("/api/v1/source/stream/config").json()
    assert cfg["mediapipe_custom_style"] is True
    assert cfg["mediapipe_pose_thickness"] == 7
    assert cfg["mediapipe_hands_thickness"] == 3


def test_color_backend_to_ui(page, base_url, api_helper, stream_config_guard):
    """接口写颜色 → 页面加载后线宽/开关回读后端真值。"""
    orig = stream_config_guard
    payload = {
        "frame_limit_enabled": orig["frame_limit_enabled"],
        "target_stream_fps": orig["target_stream_fps"],
        "use_half": orig["use_half"],
        "mediapipe_enabled": True,
        "mediapipe_pose": orig["mediapipe_pose"],
        "mediapipe_hands": orig["mediapipe_hands"],
        "mediapipe_confidence": orig["mediapipe_confidence"],
        "mediapipe_interval": orig["mediapipe_interval"],
        "mediapipe_custom_style": True,
        "mediapipe_pose_color": "#AB12CD",
        "mediapipe_pose_point_color": "#FF8800",
        "mediapipe_pose_thickness": 6,
        "mediapipe_hands_color": "#12CDAB",
        "mediapipe_hands_point_color": "#0088FF",
        "mediapipe_hands_thickness": 9,
    }
    r = api_helper.post("/api/v1/source/stream/config", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["mediapipe_pose_color"] == "#AB12CD"
    assert body["mediapipe_pose_point_color"] == "#FF8800"
    assert body["mediapipe_hands_color"] == "#12CDAB"
    assert body["mediapipe_hands_point_color"] == "#0088FF"

    _goto_performance_tab(page, base_url)
    time.sleep(1.5)  # 等 loadPerformanceSettings 回读
    pose_thick = _row(page, "姿态线条粗细").locator(".el-input-number input").first.input_value()
    hands_thick = _row(page, "手部线条粗细").locator(".el-input-number input").first.input_value()
    assert pose_thick == "6", f"姿态线宽 UI 回读应为 6, 实际 {pose_thick}"
    assert hands_thick == "9", f"手部线宽 UI 回读应为 9, 实际 {hands_thick}"


def test_invalid_color_rejected(api_helper, stream_config_guard):
    """非法颜色值不落库（保留原值）, 线宽越界被钳制到 1-10。"""
    orig = stream_config_guard
    base = {
        "frame_limit_enabled": orig["frame_limit_enabled"],
        "target_stream_fps": orig["target_stream_fps"],
        "use_half": orig["use_half"],
        "mediapipe_enabled": orig["mediapipe_enabled"],
        "mediapipe_pose": orig["mediapipe_pose"],
        "mediapipe_hands": orig["mediapipe_hands"],
        "mediapipe_confidence": orig["mediapipe_confidence"],
        "mediapipe_interval": orig["mediapipe_interval"],
    }
    before = api_helper.get("/api/v1/source/stream/config").json()
    r = api_helper.post("/api/v1/source/stream/config", json={
        **base,
        "mediapipe_pose_color": "not-a-color",
        "mediapipe_pose_thickness": 99,
        "mediapipe_hands_thickness": 0,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["mediapipe_pose_color"] == before["mediapipe_pose_color"], "非法颜色应保留原值"
    assert body["mediapipe_pose_thickness"] == 10, "线宽上限钳制 10"
    assert body["mediapipe_hands_thickness"] == 1, "线宽下限钳制 1"


def test_point_color_optional_semantics(api_helper, stream_config_guard):
    """关键点颜色: 合法值落库 / 空串 = 跟随连线颜色 / 非法值保留原值。"""
    orig = stream_config_guard
    base = {
        "frame_limit_enabled": orig["frame_limit_enabled"],
        "target_stream_fps": orig["target_stream_fps"],
        "use_half": orig["use_half"],
        "mediapipe_enabled": orig["mediapipe_enabled"],
        "mediapipe_pose": orig["mediapipe_pose"],
        "mediapipe_hands": orig["mediapipe_hands"],
        "mediapipe_confidence": orig["mediapipe_confidence"],
        "mediapipe_interval": orig["mediapipe_interval"],
    }
    # 合法值落库
    body = api_helper.post("/api/v1/source/stream/config", json={
        **base, "mediapipe_hands_point_color": "#FFEE00",
    }).json()
    assert body["mediapipe_hands_point_color"] == "#FFEE00"
    # 非法值保留原值
    body = api_helper.post("/api/v1/source/stream/config", json={
        **base, "mediapipe_hands_point_color": "banana",
    }).json()
    assert body["mediapipe_hands_point_color"] == "#FFEE00", "非法关键点颜色应保留原值"
    # 空串 = 显式清除 (回到跟随连线颜色)
    body = api_helper.post("/api/v1/source/stream/config", json={
        **base, "mediapipe_hands_point_color": "",
    }).json()
    assert body["mediapipe_hands_point_color"] == "", "空串应清除关键点颜色"
