"""监控页 E2E — 容器计数卡「预计进箱」值 (v3.44.6).

背景: 开了"动作前稳定计数"后, 进箱记账值 = 稳定快照(优先)/峰值(兜底),
不再恒等于卡片大数字(峰值) — 操作员无法预判进箱结果。本用例固化:
  1. 状态接口 current_tray_items 下发 book_preview 字段
  2. 监控页卡片渲染「预计进箱」文案

前置: 后端 8001 + 前端已起; 依赖本机 7-28 现场视频 + v31 模型 (缺则 skip)。
"""
import time
from pathlib import Path

import pytest
import requests

VIDEO = "/home/qianqian/2026-07-28 14-53-54.mkv"
MODEL = ("/home/qianqian/桌面/word/tianjun-main/backend/uploads/models/"
         "740fae093b184e149e749b386965d015_sy8_packing_v31.pt")
CH = 0


def _sy8(api_url):
    r = requests.get(f"{api_url}/api/v1/projects", timeout=10).json()
    items = r["items"] if isinstance(r, dict) and "items" in r else r
    return next((p for p in items if p.get("name") == "SY8"), None)


def test_book_preview_in_state_and_ui(page, base_url, api_url):
    if not Path(VIDEO).exists() or not Path(MODEL).exists():
        pytest.skip("本机缺 7-28 现场视频或 v31 模型")
    sy8 = _sy8(api_url)
    if sy8 is None:
        pytest.skip("本机无 SY8 项目")
    p = requests.get(f"{api_url}/api/v1/projects/{sy8['id']}", timeout=10).json()

    for ep in ("detection/stop", "video/stop"):
        requests.post(f"{api_url}/api/v1/source/{ep}?channel={CH}", timeout=10)
    time.sleep(1)
    try:
        requests.post(f"{api_url}/api/v1/source/detection/set-project?channel={CH}",
                      json={"project_id": p["id"], "name": p["name"],
                            "task_type": p["task_type"], "logic_mode": p["logic_mode"],
                            "steps_config": p["steps_config"],
                            "pipeline_config": p["pipeline_config"],
                            "events_config": p["events_config"],
                            "counters_config": p["counters_config"],
                            "data_config": p["data_config"]}, timeout=15)
        requests.post(f"{api_url}/api/v1/source/video/start?channel={CH}",
                      json={"file_path": VIDEO, "speed": 1.0}, timeout=15)
        requests.post(f"{api_url}/api/v1/source/detection/start?channel={CH}",
                      json={"session_name": "__e2e_book_preview", "models": [
                          {"name": "main", "model_path": MODEL, "priority": 100}]},
                      timeout=30)

        # 状态接口契约: book_preview 字段随托盘出现下发
        preview_seen = None
        for _ in range(20):
            st = requests.get(
                f"{api_url}/api/v1/source/detection/results?channel={CH}",
                timeout=10).json()
            items = ((st.get("custom_mix_state") or {}).get("container")
                     or {}).get("current_tray_items") or []
            if items and (items[0].get("book_preview") or 0) > 0:
                preview_seen = items[0]
                break
            time.sleep(1)
        assert preview_seen is not None, "20s 内状态接口未下发正值 book_preview"

        # UI 契约: 监控页卡片渲染「预计进箱」
        # conftest 每用例激活临时顺序模式项目, 容器卡只在自定义模式项目下渲染
        requests.post(f"{api_url}/api/v1/projects/{p['id']}/activate", timeout=10)
        page.goto(f"{base_url}/#/monitor", wait_until="domcontentloaded",
                  timeout=20000)
        # 小视口下卡片可能在折叠区外, 等挂载不等可见
        page.locator("text=预计进箱").first.wait_for(state="attached",
                                                    timeout=15000)
    finally:
        for ep in ("detection/stop", "video/stop"):
            requests.post(f"{api_url}/api/v1/source/{ep}?channel={CH}", timeout=10)
