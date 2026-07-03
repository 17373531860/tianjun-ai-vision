# -*- coding: utf-8 -*-
"""可见浏览器 UAT: 多通道单工位视频卡片外置 ChannelVideoCard.vue（拆分批次 M-4, 高危压轴）。

现场叙事: 双工位客户在 Monitor 看两张视频卡片——每张卡下层画布放本工位的
实时视频流、上层画布叠检测框, 左上角标工位号+项目名、右上角标运行状态,
底部一条统计(总/OK/NG/FPS); 点哪张卡哪张亮青边(选中工位)。四工位则是
2x2 紧凑小卡。拆分只动"卡片壳", 视频流机制(fetch-MJPEG 解析/framePump
背压/paintToCanvas)与检测框绘制全留父级——不变量 7 直接相关, 因此本 UAT
用**真实视频流**验证, 不 mock 画面。

验证手法:
  - 双工位: ch0/ch1 各启一路**真实视频**(金龙 GW1/GW2 两段不同内容的视频),
    读两块视频画布的像素: 都要有非黑画面(画布回注链真通), 且两画布像素
    指纹不同(通道不串台 = 不变量 7 的 state 隔离)。
  - 检测框: 拦截 ch0 的结果轮询注入归一化 bbox → ch0 覆盖画布出现非透明
    像素, ch1 覆盖画布保持空白(覆盖画布回注链 + 隔离双验证)。
  - 状态角标: 视频已启动未开检 → 两卡都该显示"待机"(真实后端状态, 无 mock)。
  - 选中态: 点卡 2 → 卡 2 青边 & 卡 1 灰边; selectedChannel 联动详情区。
  - 四工位: 切 4 工位 → 2x2 紧凑卡 4 张(compact 档角标"工位N"无空格)。

覆盖:
  A. 双工位: 两卡渲染 + 角标 + 待机状态 + 底部统计条
  B. 真实视频流: 两画布非黑 + 像素指纹不同(不串台)
  C. 检测框注入: ch0 覆盖画布有像素 / ch1 无(隔离)
  D. 点击选卡: 边框高亮切换
  E. 四工位 compact: 4 张紧凑卡渲染
  F. 无 console 错误

前置: 后端 8001 + 前端 6001。收尾停视频 + 恢复原工位数。
"""
from __future__ import annotations

import json
import sys
import time

import requests
from playwright.sync_api import sync_playwright

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from _common import UatRun, launch_browser, filter_console_errors  # noqa: E402

API = "http://127.0.0.1:8001/api/v1"
FRONT = "http://localhost:6001"

GW1_VIDEO = "/home/qianqian/1.py/output/金龙/GW1/video/064b34b762c1b4e75ad338c6e519030c.mp4"
GW2_VIDEO = "/home/qianqian/1.py/output/金龙/GW2/video/a4b8088d80fa2d7f42ada3e74220d878.mp4"

run = UatRun("channel_video_card")

inject_dets = {"on": False}


def _handle_results(route):
    """仅对 channel=0 注入检测框, ch1 保持原样 → 顺带验证覆盖画布隔离"""
    resp = route.fetch()
    if not inject_dets["on"] or "channel=1" in route.request.url:
        route.fulfill(response=resp)
        return
    try:
        data = resp.json()
    except Exception:
        route.fulfill(response=resp)
        return
    # 多通道画框读归一化 x/y/w/h 单值字段 (clipNormalizedBox), 不是 bbox 数组
    data["detections"] = [
        {"label": "uat_box", "confidence": 0.95, "x": 0.2, "y": 0.2, "w": 0.4, "h": 0.4},
        {"label": "uat_box2", "confidence": 0.9, "x": 0.6, "y": 0.5, "w": 0.25, "h": 0.3},
    ]
    route.fulfill(status=200, headers={"content-type": "application/json"},
                  body=json.dumps(data))


# 卡片定位: 多通道卡片根节点 = .relative.bg-black.border-2 (双层 canvas 在内)
_CARDS_JS = """() => {
  const cards = [...document.querySelectorAll('div.relative.bg-black.border-2')]
    .filter(d => d.querySelectorAll('canvas').length >= 2);
  return cards.map(c => ({
    text: c.innerText.replace(/\\s+/g, ' ').trim(),
    cls: c.className,
  }));
}"""

# 读第 idx 张卡片的某层画布像素: layer 0=视频 1=覆盖
# 返回 {nonEmpty: 非透明/非黑像素比例, sig: 下采样像素签名}
_CANVAS_PIXELS_JS = """([idx, layer]) => {
  const cards = [...document.querySelectorAll('div.relative.bg-black.border-2')]
    .filter(d => d.querySelectorAll('canvas').length >= 2);
  const card = cards[idx];
  if (!card) return null;
  const canvas = card.querySelectorAll('canvas')[layer];
  if (!canvas || !canvas.width || !canvas.height) return {nonEmpty: 0, sig: ''};
  const ctx = canvas.getContext('2d');
  const img = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
  let lit = 0, total = 0;
  const sig = [];
  const stepY = Math.max(1, Math.floor(canvas.height / 8));
  const stepX = Math.max(1, Math.floor(canvas.width / 8));
  for (let y = 0; y < canvas.height; y += stepY) {
    for (let x = 0; x < canvas.width; x += stepX) {
      const i = (y * canvas.width + x) * 4;
      const r = img[i], g = img[i+1], b = img[i+2], a = img[i+3];
      total++;
      if (a > 0 && (r + g + b) > 30) lit++;
      sig.push(Math.round(r/32) + ',' + Math.round(g/32) + ',' + Math.round(b/32));
    }
  }
  return {nonEmpty: total ? lit / total : 0, sig: sig.join('|')};
}"""


def _wait_canvas_lit(page, idx, layer, timeout_s=12.0, min_ratio=0.15):
    deadline = time.time() + timeout_s
    res = None
    while time.time() < deadline:
        res = page.evaluate(_CANVAS_PIXELS_JS, [idx, layer])
        if res and res["nonEmpty"] >= min_ratio:
            return True, res
        time.sleep(0.5)
    return False, res


orig_channels = requests.get(f"{API}/workstations/", timeout=10).json().get("channel_count", 1)

try:
    requests.post(f"{API}/workstations/mode", json={"channel_count": 2}, timeout=30).raise_for_status()
    requests.post(f"{API}/source/video/start?channel=0",
                  json={"file_path": GW1_VIDEO, "speed": 1}, timeout=30).raise_for_status()
    requests.post(f"{API}/source/video/start?channel=1",
                  json={"file_path": GW2_VIDEO, "speed": 1}, timeout=30).raise_for_status()
    print(">>> 双工位 + 两路真实视频已启动")

    with sync_playwright() as p:
        browser, ctx, page, cerrs = launch_browser(p, record_video_dir=run.video_dir)
        page.route("**/source/detection/results*", _handle_results)

        page.goto(f"{FRONT}/#/monitor", wait_until="domcontentloaded")
        time.sleep(4.0)

        # ---- A. 两卡渲染 + 角标 + 状态 + 统计条 ----
        cards = page.evaluate(_CARDS_JS)
        run.step("A1 双工位两张视频卡片渲染", len(cards) == 2, f"n={len(cards)}")
        run.step("A2 工位角标 1/2 各就位",
                 len(cards) == 2 and "工位 1" in cards[0]["text"] and "工位 2" in cards[1]["text"],
                 f"texts={[c['text'][:40] for c in cards]}")
        # 状态角标经 150ms 轮询异步刷新("停止"→"待机"), 轮询等待不单点采样
        ok_standby = False
        for _ in range(20):
            cards = page.evaluate(_CARDS_JS)
            if len(cards) == 2 and all("待机" in c["text"] for c in cards):
                ok_standby = True
                break
            time.sleep(0.5)
        run.step("A3 真实后端状态→待机角标", ok_standby,
                 f"texts={[c['text'][:25] for c in cards]}")
        run.step("A4 底部统计条(总/OK/NG/FPS)",
                 all(all(k in c["text"] for k in ("总:", "OK:", "NG:", "FPS:")) for c in cards))
        run.shot(page, "01_dual_cards")

        # ---- B. 真实视频流进画布 + 不串台 ----
        ok0, px0 = _wait_canvas_lit(page, 0, 0)
        ok1, px1 = _wait_canvas_lit(page, 1, 0)
        run.step("B1 工位1视频画布有真画面", ok0, f"lit={px0 and px0['nonEmpty']:.2f}")
        run.step("B2 工位2视频画布有真画面", ok1, f"lit={px1 and px1['nonEmpty']:.2f}")
        run.step("B3 两画布像素指纹不同(通道不串台)",
                 bool(px0 and px1 and px0["sig"] != px1["sig"]))
        run.shot(page, "02_real_streams")

        # ---- C. 检测框覆盖画布 + 隔离 ----
        inject_dets["on"] = True
        okov, pxov = _wait_canvas_lit(page, 0, 1, min_ratio=0.01)
        run.step("C1 ch0 覆盖画布画出检测框", okov, f"lit={pxov and pxov['nonEmpty']:.3f}")
        time.sleep(1.0)
        pxov1 = page.evaluate(_CANVAS_PIXELS_JS, [1, 1])
        run.step("C2 ch1 覆盖画布保持空白(隔离)",
                 pxov1 is not None and pxov1["nonEmpty"] < 0.005,
                 f"lit={pxov1 and pxov1['nonEmpty']:.3f}")
        run.shot(page, "03_overlay_isolated")
        inject_dets["on"] = False

        # ---- D. 点击选卡 ----
        cards0 = page.evaluate(_CARDS_JS)
        run.step("D1 初始卡1选中(青边)",
                 "border-cyan-500" in cards0[0]["cls"] and "border-cyan-500" not in cards0[1]["cls"],
                 f"cls0={cards0[0]['cls'][-40:]}")
        page.locator("div.relative.bg-black.border-2").nth(1).click()
        time.sleep(0.8)
        cards1 = page.evaluate(_CARDS_JS)
        run.step("D2 点卡2后选中态切换",
                 "border-cyan-500" in cards1[1]["cls"] and "border-cyan-500" not in cards1[0]["cls"],
                 f"cls1={cards1[1]['cls'][-40:]}")
        run.shot(page, "04_select_switch")

        # ---- E. 四工位 compact ----
        page.goto("about:blank")
        time.sleep(0.6)
        requests.post(f"{API}/workstations/mode", json={"channel_count": 4}, timeout=30).raise_for_status()
        page.goto(f"{FRONT}/#/monitor", wait_until="domcontentloaded")
        time.sleep(3.0)
        cards4 = page.evaluate(_CARDS_JS)
        run.step("E1 四工位 2x2 紧凑卡 4 张", len(cards4) == 4, f"n={len(cards4)}")
        run.step("E2 compact 角标(工位3 无空格样式)",
                 len(cards4) == 4 and "工位3" in cards4[2]["text"])
        run.shot(page, "05_quad_compact")

        errs = filter_console_errors(cerrs)
        run.step("F1 无前端 console 错误", len(errs) == 0, f"errs={errs[:3]}")

        page.unroute_all(behavior="ignoreErrors")
        ctx.close()
        browser.close()
finally:
    try:
        for chn in (0, 1):
            requests.post(f"{API}/source/video/stop?channel={chn}", timeout=10)
        requests.post(f"{API}/workstations/mode", json={"channel_count": orig_channels}, timeout=30)
        print(f">>> 已停视频并恢复工位数={orig_channels}")
    except Exception as e:
        print(f"!!! 收尾恢复失败, 请手动检查: {e}")
    run.finish()
