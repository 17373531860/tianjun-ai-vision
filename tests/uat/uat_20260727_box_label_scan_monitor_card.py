# -*- coding: utf-8 -*-
"""UAT: 箱标签扫码授权 — 监控页包装卡片运行时形态真浏览器验证 (v3.45).

流程: 建启用配置(等扫箱标签+取量) → HTTP 扫码入口喂裸工单码 →
     卡片出「等扫箱标签」琥珀横幅 → 喂标签二维码(数量19) →
     横幅消失/状态装箱中/本箱目标=19 → 清理.

证据: /home/qianqian/uat_box_label_scan/monitor_*.png + run2.log
"""
import sys
import time
from pathlib import Path

import requests

API = "http://localhost:8001"
WEB = "http://localhost:6001"
OUT = Path("/home/qianqian/uat_box_label_scan")
NAME = "__uat_label_runtime"
ORDER = "JOB260600040-67"
LABEL = "ORD260300050-2|JOB260600040-67|19.00|A1B2C3"

_log_fh = None


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    if _log_fh:
        _log_fh.write(line + "\n")
        _log_fh.flush()


def cleanup():
    for it in requests.get(f"{API}/api/v1/packaging-flows", timeout=10).json().get("items", []):
        if it["name"] == NAME:
            requests.put(f"{API}/api/v1/packaging-flows/{it['id']}",
                         json={"enabled": False}, timeout=10)
            requests.delete(f"{API}/api/v1/packaging-flows/{it['id']}", timeout=10)


_disabled_ids = []


def _park_conflicting(channel_id=0):
    """临时停用占用同工位的真实配置 (跑完恢复), 不动其任何字段."""
    for it in requests.get(f"{API}/api/v1/packaging-flows", timeout=10).json().get("items", []):
        if it["enabled"] and int(it.get("channel_id") or 0) == channel_id and it["name"] != NAME:
            requests.put(f"{API}/api/v1/packaging-flows/{it['id']}",
                         json={"enabled": False}, timeout=10)
            _disabled_ids.append(it["id"])
            log(f"临时停用同工位配置: {it['name']} (id={it['id']}), 结束后恢复")


def _restore_conflicting():
    for i in _disabled_ids:
        requests.put(f"{API}/api/v1/packaging-flows/{i}", json={"enabled": True}, timeout=10)
        log(f"已恢复配置 id={i} 启用")


def main():
    global _log_fh
    OUT.mkdir(parents=True, exist_ok=True)
    _log_fh = open(OUT / "run2.log", "w", encoding="utf-8")
    cleanup()
    _park_conflicting(0)

    r = requests.post(f"{API}/api/v1/packaging-flows", json={
        "name": NAME, "enabled": True, "channel_id": 0,
        "count_unit": "sliders", "on_mes_fail": "offline",
        "items_per_box_source": "config", "items_per_box_fixed": 24,
        "composite_label_enabled": True, "composite_delimiter": "|",
        "composite_pick_mode": "prefix", "composite_prefix": "JOB",
        "box_label_scan_required": True, "label_qty_enabled": True,
        "label_qty_segment": 3,
    }, timeout=10)
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    log(f"配置已建并启用: id={cid}")

    # 扫裸工单码开工单 → 第 1 箱进「等扫箱标签」
    st = requests.post(f"{API}/api/v1/packaging-flows/scan",
                       json={"code": ORDER, "channel_id": 0}, timeout=10).json()["state"]
    assert st["status"] == "waiting_label", st
    log(f"扫工单后: status={st['status']} box={st['current_box_index']}")

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        page = browser.new_context(viewport={"width": 1600, "height": 950}).new_page()
        page.goto(f"{WEB}/#/monitor", wait_until="domcontentloaded", timeout=20000)
        time.sleep(4)
        banner = page.get_by_text("等扫箱标签", exact=False)
        assert banner.count() >= 1, "监控卡片未出现「等扫箱标签」横幅"
        page.screenshot(path=str(OUT / "monitor_01_waiting_label.png"), full_page=True)
        log("监控卡片「等扫箱标签」横幅已出现 ✓")

        # 扫标签二维码 (数量 19) → 放行, 目标=19
        st = requests.post(f"{API}/api/v1/packaging-flows/scan",
                           json={"code": LABEL, "channel_id": 0}, timeout=10).json()["state"]
        assert st["status"] == "running" and st["current_box_scan_qty"] == 19, st
        log(f"扫标签后: status={st['status']} scan_qty={st['current_box_scan_qty']}")
        time.sleep(3)  # 等卡片轮询刷新
        page.screenshot(path=str(OUT / "monitor_02_authorized_19.png"), full_page=True)
        assert page.get_by_text("装箱中").count() >= 1, "状态未切到装箱中"
        assert page.get_by_text("/ 19").count() >= 1, "本箱目标未按标签数量 19 显示"
        log("卡片状态=装箱中, 本箱目标=19 ✓")
        browser.close()

    cleanup()
    _restore_conflicting()
    log("PASS: 运行时形态验证全通过")
    _log_fh.close()
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        cleanup()
        _restore_conflicting()
        raise
