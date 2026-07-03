"""Monitor 多通道视频卡片（ChannelVideoCard.vue 外置组件, 拆分批次 M-4）CI E2E 回归。

不变量 7 相关的高危批次, 用**真实视频流**回归, 不 mock 画面:
  1. 双工位两卡渲染 + 真视频画布非黑 + 两画布像素指纹不同(通道不串台)
  2. 注入 ch0 检测框(归一化 x/y/w/h) → ch0 覆盖画布有像素 / ch1 空白(隔离)
  3. 点卡切换选中边框

画布回注链: 组件函数 props → 父级 per-channel 画布字典 → 流绘制/检测框绘制,
本用例读 canvas 像素直接验证整条链。真实视频资产缺失时 skip。
测试收尾停视频 + 恢复原工位数。
"""
from __future__ import annotations

import json
import os
import time

import pytest
import requests


GW1_VIDEO = "/home/qianqian/1.py/output/金龙/GW1/video/064b34b762c1b4e75ad338c6e519030c.mp4"
GW2_VIDEO = "/home/qianqian/1.py/output/金龙/GW2/video/a4b8088d80fa2d7f42ada3e74220d878.mp4"

_CANVAS_PIXELS_JS = """([idx, layer]) => {
  const cards = [...document.querySelectorAll('div.relative.bg-black.border-2')]
    .filter(d => d.querySelectorAll('canvas').length >= 2);
  const card = cards[idx];
  if (!card) return null;
  const canvas = card.querySelectorAll('canvas')[layer];
  if (!canvas || !canvas.width || !canvas.height) return {nonEmpty: 0, sig: ''};
  const ctx = canvas.getContext('2d');
  const img = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
  // 密扫计比例(检测框是细线, 稀疏格点会漏), 8x8 粗格点只做指纹
  let lit = 0, total = 0;
  for (let i = 0; i < img.length; i += 16) {   // 每 4 像素采 1 点
    const r = img[i], g = img[i+1], b = img[i+2], a = img[i+3];
    total++;
    if (a > 0 && (r + g + b) > 30) lit++;
  }
  const sig = [];
  const stepY = Math.max(1, Math.floor(canvas.height / 8));
  const stepX = Math.max(1, Math.floor(canvas.width / 8));
  for (let y = 0; y < canvas.height; y += stepY) {
    for (let x = 0; x < canvas.width; x += stepX) {
      const i = (y * canvas.width + x) * 4;
      sig.push(Math.round(img[i]/32) + ',' + Math.round(img[i+1]/32) + ',' + Math.round(img[i+2]/32));
    }
  }
  return {nonEmpty: total ? lit / total : 0, sig: sig.join('|')};
}"""


def _wait_lit(page, idx, layer, timeout_s=12.0, min_ratio=0.15):
    deadline = time.time() + timeout_s
    res = None
    while time.time() < deadline:
        res = page.evaluate(_CANVAS_PIXELS_JS, [idx, layer])
        if res and res["nonEmpty"] >= min_ratio:
            return True, res
        time.sleep(0.5)
    return False, res


def test_多通道视频卡片_真流回注与隔离(page, base_url, api_url):
    if not (os.path.isfile(GW1_VIDEO) and os.path.isfile(GW2_VIDEO)):
        pytest.skip("真实视频资产缺失, 跳过 M-4 真流回归")

    inject = {"on": False}

    def handle_results(route):
        resp = route.fetch()
        if not inject["on"] or "channel=1" in route.request.url:
            route.fulfill(response=resp)
            return
        try:
            data = resp.json()
        except Exception:
            route.fulfill(response=resp)
            return
        data["detections"] = [
            {"label": "e2e_box", "confidence": 0.95, "x": 0.2, "y": 0.2, "w": 0.4, "h": 0.4},
            {"label": "e2e_box2", "confidence": 0.9, "x": 0.6, "y": 0.5, "w": 0.25, "h": 0.3},
        ]
        route.fulfill(status=200, headers={"content-type": "application/json"},
                      body=json.dumps(data))

    orig = requests.get(f"{api_url}/api/v1/workstations/", timeout=10).json().get("channel_count", 1)
    try:
        requests.post(f"{api_url}/api/v1/workstations/mode",
                      json={"channel_count": 2}, timeout=30).raise_for_status()
        requests.post(f"{api_url}/api/v1/source/video/start?channel=0",
                      json={"file_path": GW1_VIDEO, "speed": 1}, timeout=30).raise_for_status()
        requests.post(f"{api_url}/api/v1/source/video/start?channel=1",
                      json={"file_path": GW2_VIDEO, "speed": 1}, timeout=30).raise_for_status()

        page.route("**/source/detection/results*", handle_results)
        page.goto(f"{base_url}/#/monitor", wait_until="domcontentloaded", timeout=15000)
        time.sleep(3.0)

        cards = page.locator("div.relative.bg-black.border-2").filter(has=page.locator("canvas"))
        assert cards.count() == 2, f"双工位应两张卡: {cards.count()}"

        ok0, px0 = _wait_lit(page, 0, 0)
        ok1, px1 = _wait_lit(page, 1, 0)
        assert ok0, f"ch0 视频画布应有真画面: {px0}"
        assert ok1, f"ch1 视频画布应有真画面: {px1}"
        assert px0["sig"] != px1["sig"], "两画布像素指纹应不同(通道不串台)"

        inject["on"] = True
        okov, pxov = _wait_lit(page, 0, 1, min_ratio=0.003)
        assert okov, f"ch0 覆盖画布应画出检测框: {pxov}"
        pxov1 = page.evaluate(_CANVAS_PIXELS_JS, [1, 1])
        assert pxov1["nonEmpty"] < 0.003, f"ch1 覆盖画布应保持空白(隔离): {pxov1['nonEmpty']}"
        inject["on"] = False

        cards.nth(1).click()
        time.sleep(0.8)
        cls0 = cards.nth(0).get_attribute("class")
        cls1 = cards.nth(1).get_attribute("class")
        assert "border-cyan-500" in cls1 and "border-cyan-500" not in cls0, \
            f"点卡2后选中边框应切换: {cls0[-40:]} / {cls1[-40:]}"
    finally:
        page.unroute_all(behavior="ignoreErrors")
        for chn in (0, 1):
            requests.post(f"{api_url}/api/v1/source/video/stop?channel={chn}", timeout=10)
        requests.post(f"{api_url}/api/v1/workstations/mode",
                      json={"channel_count": orig}, timeout=30)
