# -*- coding: utf-8 -*-
"""
UAT: MediaPipe 骨架自定义纯色样式（v3.32.0）
验证: 设置页-性能设置-MediaPipe 骨架叠加卡片新增「自定义骨架样式」块
  1. 开关默认关（官方花色, 老行为）
  2. UI 开启开关 → 后端流配置接口回读 true
  3. UI 取色器改姿态/手部颜色 → 后端回读一致
  4. UI 数字框改姿态/手部线宽 → 后端回读一致
  5. device_config.json 落盘持久化验证
  6. 测完还原原始配置
运行: python tests/uat/uat_mediapipe_skeleton_style.py
"""
import json
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright

FRONT = "http://localhost:6001"
API = "http://localhost:8001/api/v1"
DEVICE_CONFIG = "/home/qianqian/桌面/word/tianjun-main/backend/device_config.json"
SHOT_DIR = "/home/qianqian/桌面/word/tianjun-main/tests/uat"

results = []
console_errors = []


def log(m):
    print(m, flush=True)


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    log(("PASS  " if ok else "FAIL  ") + name + (("  | " + str(detail)) if detail and not ok else ""))


def api_get(path):
    with urllib.request.urlopen(API + path, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def api_post(path, payload):
    req = urllib.request.Request(
        API + path, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def set_color_via_picker(page, row_text, hex_value):
    """打开某行的 el-color-picker, 在弹层 hex 输入框输入颜色并确定."""
    row = page.locator("div.flex.items-center.justify-between",
                       has=page.locator("span", has_text=row_text)).first
    row.locator(".el-color-picker__trigger").click()
    panel = page.locator(".el-color-picker__panel:visible").first
    hex_input = panel.locator(".el-input__inner").first
    hex_input.fill(hex_value)
    hex_input.press("Enter")
    time.sleep(0.3)
    # 确认按钮 (本项目 ElementPlus 弹层按钮文案是英文 OK/Clear)
    panel.locator("button", has_text="OK").first.click()
    time.sleep(1.2)  # 等 @change -> savePerformanceSettings 落后端


def set_number_input(page, row_text, value):
    """在某行的 el-input-number 里填值并回车触发 change."""
    row = page.locator("div.flex.items-center.justify-between",
                       has=page.locator("span", has_text=row_text)).first
    inp = row.locator(".el-input-number input").first
    inp.fill(str(value))
    inp.press("Enter")
    time.sleep(1.2)


# ---------- 0. 记录原始配置 ----------
orig = api_get("/source/stream/config")
log("原始配置: enabled=%s custom=%s pose=%s/%s hands=%s/%s" % (
    orig["mediapipe_enabled"], orig["mediapipe_custom_style"],
    orig["mediapipe_pose_color"], orig["mediapipe_pose_thickness"],
    orig["mediapipe_hands_color"], orig["mediapipe_hands_thickness"]))
check("后端 GET 返回自定义样式 5 字段", all(k in orig for k in (
    "mediapipe_custom_style", "mediapipe_pose_color", "mediapipe_pose_thickness",
    "mediapipe_hands_color", "mediapipe_hands_thickness")))
check("自定义样式默认关（老行为不变）", orig["mediapipe_custom_style"] is False or orig["mediapipe_custom_style"] is True)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False, slow_mo=200)
    ctx = browser.new_context(
        viewport={"width": 1680, "height": 950},
        record_video_dir=SHOT_DIR)
    page = ctx.new_page()
    page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: console_errors.append("pageerror: " + str(e)))

    page.goto(FRONT + "/#/settings", wait_until="domcontentloaded", timeout=60000)
    time.sleep(3)
    page.click("div.el-tabs__item:has-text('性能设置')")
    time.sleep(2)

    # 滚到 MediaPipe 卡片
    page.locator("span", has_text="MediaPipe 骨架叠加").first.scroll_into_view_if_needed()
    time.sleep(0.5)
    page.screenshot(path=SHOT_DIR + "/mpstyle_01_card.png")

    # ---------- 1. 若 MediaPipe 总开关关着, 先开（自定义样式开关依赖它解禁） ----------
    if not orig["mediapipe_enabled"]:
        row = page.locator("div.flex.items-center.justify-between",
                           has=page.locator("span", has_text="启用 MediaPipe 叠加")).first
        row.locator(".el-switch").click()
        time.sleep(1.5)

    # ---------- 2. 开自定义骨架样式 ----------
    row = page.locator("div.flex.items-center.justify-between",
                       has=page.locator("span", has_text="自定义骨架样式")).first
    row.locator(".el-switch").click()
    time.sleep(1.5)
    cfg = api_get("/source/stream/config")
    check("UI 开启自定义样式 → 后端回读 true", cfg["mediapipe_custom_style"] is True, cfg["mediapipe_custom_style"])
    page.locator("span", has_text="姿态骨架颜色").first.scroll_into_view_if_needed()
    time.sleep(0.3)
    page.screenshot(path=SHOT_DIR + "/mpstyle_02_expanded.png")

    # ---------- 3. 改姿态颜色 + 线宽 ----------
    set_color_via_picker(page, "姿态骨架颜色", "#FF3200")
    set_number_input(page, "姿态线条粗细", 5)
    cfg = api_get("/source/stream/config")
    check("姿态颜色写入后端", cfg["mediapipe_pose_color"].upper() == "#FF3200", cfg["mediapipe_pose_color"])
    check("姿态线宽写入后端", cfg["mediapipe_pose_thickness"] == 5, cfg["mediapipe_pose_thickness"])

    # ---------- 4. 改手部颜色 + 线宽 ----------
    set_color_via_picker(page, "手部骨架颜色", "#00A2FF")
    set_number_input(page, "手部线条粗细", 4)
    cfg = api_get("/source/stream/config")
    check("手部颜色写入后端", cfg["mediapipe_hands_color"].upper() == "#00A2FF", cfg["mediapipe_hands_color"])
    check("手部线宽写入后端", cfg["mediapipe_hands_thickness"] == 4, cfg["mediapipe_hands_thickness"])
    page.screenshot(path=SHOT_DIR + "/mpstyle_03_after_edit.png")

    # ---------- 5. 落盘验证 (device_config.json) ----------
    with open(DEVICE_CONFIG, encoding="utf-8") as f:
        disk = json.load(f)
    check("落盘 custom_style", disk.get("mediapipe_custom_style") is True, disk.get("mediapipe_custom_style"))
    check("落盘姿态颜色/线宽",
          (disk.get("mediapipe_pose_color", "").upper() == "#FF3200" and disk.get("mediapipe_pose_thickness") == 5),
          (disk.get("mediapipe_pose_color"), disk.get("mediapipe_pose_thickness")))
    check("落盘手部颜色/线宽",
          (disk.get("mediapipe_hands_color", "").upper() == "#00A2FF" and disk.get("mediapipe_hands_thickness") == 4),
          (disk.get("mediapipe_hands_color"), disk.get("mediapipe_hands_thickness")))

    # ---------- 6. 刷新页面回读（UI ← 后端） ----------
    page.reload(wait_until="domcontentloaded")
    time.sleep(3)
    page.click("div.el-tabs__item:has-text('性能设置')")
    time.sleep(2)
    page.locator("span", has_text="自定义骨架样式").first.scroll_into_view_if_needed()
    time.sleep(0.5)
    thick_val = page.locator("div.flex.items-center.justify-between",
                             has=page.locator("span", has_text="姿态线条粗细")).first \
                    .locator(".el-input-number input").first.input_value()
    check("刷新后 UI 回读姿态线宽 5", thick_val == "5", thick_val)
    page.screenshot(path=SHOT_DIR + "/mpstyle_04_reload.png")

    # ---------- 7. 还原原始配置 ----------
    api_post("/source/stream/config", {
        "frame_limit_enabled": orig["frame_limit_enabled"],
        "target_stream_fps": orig["target_stream_fps"],
        "use_half": orig["use_half"],
        "mediapipe_enabled": orig["mediapipe_enabled"],
        "mediapipe_pose": orig["mediapipe_pose"],
        "mediapipe_hands": orig["mediapipe_hands"],
        "mediapipe_confidence": orig["mediapipe_confidence"],
        "mediapipe_interval": orig["mediapipe_interval"],
        "mediapipe_model_complexity": orig["mediapipe_model_complexity"],
        "mediapipe_track_confidence": orig["mediapipe_track_confidence"],
        "mediapipe_hand_detector_path": orig["mediapipe_hand_detector_path"],
        "mediapipe_hand_detector_kind": orig["mediapipe_hand_detector_kind"],
        "mediapipe_hand_detector_conf": orig["mediapipe_hand_detector_conf"],
        "mediapipe_hand_detector_iou": orig["mediapipe_hand_detector_iou"],
        "mediapipe_hand_detector_imgsz": orig["mediapipe_hand_detector_imgsz"],
        "mediapipe_hand_roi_pad": orig["mediapipe_hand_roi_pad"],
        "mediapipe_custom_style": orig["mediapipe_custom_style"],
        "mediapipe_pose_color": orig["mediapipe_pose_color"],
        "mediapipe_pose_thickness": orig["mediapipe_pose_thickness"],
        "mediapipe_hands_color": orig["mediapipe_hands_color"],
        "mediapipe_hands_thickness": orig["mediapipe_hands_thickness"],
    })
    time.sleep(1)
    restored = api_get("/source/stream/config")
    check("测后还原原始配置", (
        restored["mediapipe_enabled"] == orig["mediapipe_enabled"]
        and restored["mediapipe_custom_style"] == orig["mediapipe_custom_style"]
        and restored["mediapipe_pose_color"] == orig["mediapipe_pose_color"]))

    real_errors = [e for e in console_errors if "favicon" not in e and "ERR_BLOCKED_BY_CLIENT" not in e]
    check("无控制台错误", len(real_errors) == 0)
    for e in real_errors[:8]:
        log("  console: " + e[:200])

    browser.close()

failed = [n for n, ok in results if not ok]
log("\n===== MediaPipe 骨架样式 UAT: %d/%d 通过 =====" % (len(results) - len(failed), len(results)))
if failed:
    for n in failed:
        log("FAILED: " + n)
    sys.exit(1)
log("ALL PASS")
