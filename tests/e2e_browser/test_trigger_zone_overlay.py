"""Monitor 虚拟按钮触发区域叠加 CI E2E 回归 (v3.49)。

背景 (2026-08-13 现场反馈): 触发中心 pixel_region 虚拟按钮标定完区域后,
监控画面上看不到按钮在哪 —— 操作员不知道往哪伸手。Monitor 新增常驻叠加:
启用中的 pixel_region 实例按 params.region (原始帧像素坐标) 画琥珀虚线框
(#fbbf24) + 名称, 工位不匹配 / 已禁用的实例不画。

注入手法: route 拦截三处 —— /triggers/channels (触发源清单) +
/source/status (运行态) + /video_feed (静态 JPEG, 给 <img> 一个真实
naturalWidth/Height, 叠加层按帧自然尺寸归一化坐标, 没有自然尺寸不画)。
"""
from __future__ import annotations

import json
import time


def _make_jpeg(w: int = 640, h: int = 480) -> bytes:
    import numpy as np
    import cv2
    frame = np.full((h, w, 3), 24, dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", frame)
    assert ok
    return bytes(buf)


# 琥珀 #fbbf24 = (251,191,36) — 与 Monitor/index.vue TRIGGER_ZONE_COLOR 对齐
_HAS_AMBER_JS = """() => {
  const cv = [...document.querySelectorAll('canvas')]
    .find(c => c.width > 100 && c.height > 100);
  if (!cv) return 'no-canvas';
  const ctx = cv.getContext('2d');
  const img = ctx.getImageData(0, 0, cv.width, cv.height).data;
  for (let i = 0; i < img.length; i += 4) {
    if (img[i + 3] > 200 && Math.abs(img[i] - 251) < 25
        && Math.abs(img[i + 1] - 191) < 25 && Math.abs(img[i + 2] - 36) < 40)
      return true;
  }
  return false;
}"""


def _route_all(page, triggers):
    jpeg = _make_jpeg()

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

    def handle_triggers(route):
        try:
            route.fulfill(status=200, headers={"content-type": "application/json"},
                          body=json.dumps({"triggers": triggers}))
        except Exception:
            pass

    def handle_feed(route):
        try:
            route.fulfill(status=200, headers={"content-type": "image/jpeg"},
                          body=jpeg)
        except Exception:
            pass

    page.route("**/api/v1/source/status*", handle_status)
    page.route("**/api/v1/triggers/channels", handle_triggers)
    page.route("**/video_feed*", handle_feed)


def _trigger(name="虚拟结算按钮", *, enabled=True, channel=0,
             region=(160, 120, 320, 240)):
    return {"id": 991, "name": name, "type": "pixel_region", "enabled": enabled,
            "params": {"channel": channel, "region": list(region),
                       "mode": "ref_diff", "threshold": 40},
            "rules": [], "options": {"default_channel": channel},
            "runtime": {"status": "running"}}


def _wait_amber(page, timeout_s=12.0):
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        last = page.evaluate(_HAS_AMBER_JS)
        if last is True:
            return True, last
        time.sleep(0.3)
    return False, last


def test_虚拟按钮区域上屏(page, base_url, api_url):
    _route_all(page, [_trigger()])
    page.goto(f"{base_url}/#/monitor", wait_until="domcontentloaded")
    ok, last = _wait_amber(page)
    assert ok, f"启用中的 pixel_region 触发区域未画出琥珀框 (last={last})"


def test_禁用或他工位实例不画(page, base_url, api_url):
    _route_all(page, [
        _trigger("已禁用按钮", enabled=False),
        _trigger("他工位按钮", channel=3),
    ])
    page.goto(f"{base_url}/#/monitor", wait_until="domcontentloaded")
    page.wait_for_selector("canvas", timeout=10000)
    time.sleep(2.5)  # 留足几个轮询拍, 确认叠加层始终没画
    assert page.evaluate(_HAS_AMBER_JS) is not True, \
        "禁用/他工位的触发区域不应画到本工位画面上"
