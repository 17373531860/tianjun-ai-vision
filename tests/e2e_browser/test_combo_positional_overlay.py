"""Monitor 判型表 positional 锁定框可视化 CI E2E 回归 (v3.48.x)。

背景 (现场反馈): positional 位置去重计数此前只在后端算、API 透出, Monitor
零显示 —— 现场工程师看不到"锁定框"和"实时计数", 误以为功能没生效。
本回归锁定可视化三件套:
  1. 信息条「判型计数」实时上屏 (标签全集含 0 计数) + 机型 tag
  2. 叠加 canvas 画锁定 ROI 常驻框 (青色 #22d3ee) —— 空检测帧也画
  3. 无判型表配置 (combo_verdict.enabled=false) 时整条隐藏 (零差异)

注入手法与 test_region_events_monitor.py 同款: route 拦截 /source/status +
/source/detection/results, 数据走前端完整真实通路。
"""
from __future__ import annotations

import json
import time


_ROIS = [
    {"label": "区A", "box": [0.10, 0.15, 0.25, 0.30], "state": "locked", "seq": 1},
    {"label": "区A", "box": [0.55, 0.20, 0.70, 0.35], "state": "locked", "seq": 2},
    {"label": "区B", "box": [0.35, 0.60, 0.50, 0.75], "state": "pending",
     "seen": 2, "need": 3},
]

# 锁框颜色 #22d3ee (青) — 与 Monitor/index.vue COMBO_LOCK_COLOR 对齐
_HAS_CYAN_JS = """() => {
  const cv = [...document.querySelectorAll('canvas')]
    .find(c => c.width > 100 && c.height > 100);
  if (!cv) return 'no-canvas';
  const ctx = cv.getContext('2d');
  const img = ctx.getImageData(0, 0, cv.width, cv.height).data;
  for (let i = 0; i < img.length; i += 4) {
    if (img[i + 3] > 200 && Math.abs(img[i] - 34) < 30
        && Math.abs(img[i + 1] - 211) < 30 && Math.abs(img[i + 2] - 238) < 30)
      return true;
  }
  return false;
}"""


def _mk_handlers(combo_verdict):
    # 页面关闭瞬间在途请求会让 route.fulfill 抛 "response disposed",
    # 吞掉即可 (teardown 竞态, 不是被测语义)
    def handle_status(route):
        try:
            resp = route.fetch()
            try:
                data = resp.json()
            except Exception:
                route.fulfill(response=resp)
                return
            data.update({"is_running": True, "is_detecting": True,
                         "source_type": "video", "model_loaded": True})
            route.fulfill(status=200, headers={"content-type": "application/json"},
                          body=json.dumps(data))
        except Exception:
            pass

    def handle_results(route):
        try:
            resp = route.fetch()
            try:
                data = resp.json()
            except Exception:
                route.fulfill(response=resp)
                return
            data.update({"is_detecting": True, "is_running": True, "fps": 30,
                         "detections": [], "combo_verdict": combo_verdict})
            route.fulfill(status=200, headers={"content-type": "application/json"},
                          body=json.dumps(data))
        except Exception:
            pass

    return handle_status, handle_results


def _wait_text(page, text, timeout_s=10.0):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if text in page.inner_text("body"):
            return True
        time.sleep(0.3)
    return False


def _wait_cyan(page, timeout_s=10.0):
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        last = page.evaluate(_HAS_CYAN_JS)
        if last is True:
            return True, last
        time.sleep(0.3)
    return False, last


def test_判型计数条与锁定框上屏(page, base_url, api_url):
    cv = {"enabled": True, "last_tag": "机型X",
          "positional_counts": {"区A": 2}, "positional_rois": _ROIS}
    handle_status, handle_results = _mk_handlers(cv)
    page.route("**/api/v1/source/status*", handle_status)
    page.route("**/api/v1/source/detection/results*", handle_results)

    page.goto(f"{base_url}/#/monitor", wait_until="domcontentloaded")

    # 1. 信息条: 判型计数 + 机型 tag (positional_counts 只含 >0 标签,
    #    区B 0 由前端按配置标签补全 —— 无激活项目配置时回退计数字典键)
    assert _wait_text(page, "判型计数"), "信息条未出现「判型计数」"
    body = page.inner_text("body")
    assert "区A 2" in body, f"判型计数未显示区A 2: {body[:200]}"
    assert "机型X" in body, "机型 tag 未上屏"

    # 2. 叠加层: 空检测帧下锁定框 (青色) 常驻
    ok, last = _wait_cyan(page)
    assert ok, f"叠加 canvas 未画出青色锁定框 (last={last})"


def test_锁框显示开关关闭时不画但计数条仍在(page, base_url, api_url):
    cv = {"enabled": True, "last_tag": None, "show_lock_overlay": False,
          "positional_counts": {"区A": 2}, "positional_rois": _ROIS}
    handle_status, handle_results = _mk_handlers(cv)
    page.route("**/api/v1/source/status*", handle_status)
    page.route("**/api/v1/source/detection/results*", handle_results)

    page.goto(f"{base_url}/#/monitor", wait_until="domcontentloaded")
    # 计数条照常 (开关只管画不画锁框)
    assert _wait_text(page, "判型计数"), "开关关闭不应影响判型计数条"
    time.sleep(1.5)  # 留足几个轮询拍, 确认叠加层始终没画青色
    assert page.evaluate(_HAS_CYAN_JS) is not True, "开关关闭仍画出了锁定框"


def test_缸型未登记红色警示(page, base_url, api_url):
    """2026-08-14 现场: PLC cyl_type 读到 11, 判定表只登记 4/6, 旧版看板
    只显示裸值、数量门静默走推断兜底, 工程师无从察觉配置不匹配。
    本回归锁定: 未登记值 → 瓦片红显「未在判定表登记」; 已登记值 → 无警示。"""
    import requests as _rq
    import uuid as _uuid
    name = f"__e2e_缸型未登记_{_uuid.uuid4().hex[:8]}"
    r = _rq.post(f"{api_url}/api/v1/projects", json={
        "name": name, "task_type": "detection", "logic_mode": "detection",
        "steps_config": [
            {"id": 1, "label": "区A", "name": "区域A", "enabled": True},
            {"id": 2, "label": "区B", "name": "区域B", "enabled": True},
        ],
        "pipeline_config": {"combo_table": {
            "enabled": True, "labels": ["区A", "区B"], "count_mode": "positional",
            "rows": [
                {"counts": [2, 1], "verdict": "OK", "tag": "机型X", "plc_code": "4"},
                {"counts": [3, 1], "verdict": "OK", "tag": "机型Y", "plc_code": "6"},
            ],
            "live_display": {"enabled": True, "size": "large", "show_plc_type": True},
        }},
        "events_config": [{"id": 1, "name": "合格", "type": "ok", "enabled": True},
                          {"id": 2, "name": "NG", "type": "ng", "enabled": True}],
        "counters_config": [],
    }, timeout=10)
    r.raise_for_status()
    pid = r.json()["id"]
    _rq.post(f"{api_url}/api/v1/projects/{pid}/activate", timeout=10).raise_for_status()

    ld = {"enabled": True, "size": "large", "show_plc_type": True}
    cv_bad = {"enabled": True, "last_tag": None, "live_display": ld,
              "positional_counts": {"区A": 2, "区B": 1},
              "plc_type": {"value": 11, "tag": None}}
    handle_status, handle_results = _mk_handlers(cv_bad)
    page.route("**/api/v1/source/status*", handle_status)
    page.route("**/api/v1/source/detection/results*", handle_results)
    page.goto(f"{base_url}/#/monitor", wait_until="domcontentloaded")
    assert _wait_text(page, "判型实时看板"), "看板未渲染"
    assert _wait_text(page, "未在判定表登记"), \
        "PLC 值 11 未登记, 瓦片应显示红色警示"

    # 已登记值 (4 → 机型X) → 无警示
    cv_ok = {**cv_bad, "plc_type": {"value": 4, "tag": "机型X"}}
    page.unroute("**/api/v1/source/detection/results*")
    _, handle_results_ok = _mk_handlers(cv_ok)
    page.route("**/api/v1/source/detection/results*", handle_results_ok)
    assert _wait_text(page, "机型X"), "已登记缸型未上屏"
    time.sleep(1.0)
    assert "未在判定表登记" not in page.inner_text("body"), \
        "已登记缸型不应有未登记警示"


def test_未配判型表时整条隐藏(page, base_url, api_url):
    handle_status, handle_results = _mk_handlers({"enabled": False, "last_tag": None})
    page.route("**/api/v1/source/status*", handle_status)
    page.route("**/api/v1/source/detection/results*", handle_results)

    page.goto(f"{base_url}/#/monitor", wait_until="domcontentloaded")
    assert _wait_text(page, "检测数"), "Monitor 信息条未加载"
    time.sleep(1.0)
    assert "判型计数" not in page.inner_text("body"), "未配判型表却显示了判型计数条"
