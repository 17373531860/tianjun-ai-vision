# -*- coding: utf-8 -*-
"""v3.47 训练平台互连 UAT 截图脚本 (headless, 不占用系统鼠标).

验证矩阵:
1. /#/interconnect 页面渲染 (连接配置 / 采样规则 / 运行状态三块)
2. 测试连接按钮 → 对端 health 探活回显 (指向本机 8003 自证连通)
3. UI 改采样配置 → 保存 → 后端 GET /interconnect/config 落库双向验证 (T5)
4. /#/model 模型卡片显示「训练平台」来源标 + 训练分析按钮
5. 训练分析弹窗: 指标格 + ECharts 训练曲线渲染 (canvas 非空)

用法:
  E2E_BASE_URL=http://localhost:6003 E2E_API_URL=http://localhost:8003 \
    python tests/uat/uat_interconnect_screenshots.py
"""
from __future__ import annotations

import os
import sys
import time

import requests
from playwright.sync_api import sync_playwright

BASE = os.environ.get("E2E_BASE_URL", "http://localhost:6003")
API = os.environ.get("E2E_API_URL", "http://localhost:8003")
OUT = "/tmp/uat_interconnect"
os.makedirs(OUT, exist_ok=True)

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def main():
    # 基线钉死: 采样关 (UI 拨开后验证落库), 保证脚本幂等可重跑
    base_cfg = requests.get(f"{API}/api/v1/interconnect/config", timeout=5).json()
    base_cfg["sampling"]["enabled"] = False
    base_cfg["sampling"]["no_detection_enabled"] = False
    requests.put(f"{API}/api/v1/interconnect/config", json=base_cfg, timeout=5)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        js_errors = []
        page.on("pageerror", lambda exc: js_errors.append(str(exc)))

        # ---------- 1. 互连设置页 ----------
        page.goto(f"{BASE}/#/interconnect", wait_until="domcontentloaded")
        page.wait_for_selector("text=训练平台互连 (YoloVision)", timeout=15000)
        page.wait_for_timeout(1200)
        check("互连页-连接配置卡", page.locator("text=连接配置").count() > 0)
        check("互连页-采样规则卡", page.locator("text=现场帧采样回传").count() > 0)
        check("互连页-运行状态卡", page.locator("text=运行状态").count() > 0)
        # 后端已启用 → 状态区应显示「已启用」
        check("互连页-状态显示已启用", page.locator("text=已启用").count() > 0)
        # v1.1 新增
        check("互连页-模型分发卡", page.locator("text=模型分发 — 定时拉取").count() > 0)
        check("互连页-设备ID展示", page.locator("text=tj-").count() > 0)
        check("互连页-检出闪断项", page.locator("text=检出闪断采样").count() > 0)
        check("互连页-未检出仅周期内", page.locator("text=仅周期内").count() > 0)
        page.screenshot(path=f"{OUT}/01_interconnect_page.png", full_page=True)

        # ---------- 1.5 立即拉取 (对端未实现 1.1 时优雅报错) ----------
        page.locator("button:has-text('立即拉取')").click()
        page.wait_for_timeout(1500)
        check("立即拉取-有结果回显",
              page.locator("text=拉取失败").count() > 0
              or page.locator("text=本轮入库").count() > 0)
        page.screenshot(path=f"{OUT}/06_pull_now.png")

        # ---------- 2. 测试连接 (指向本机后端自证连通) ----------
        url_input = page.locator("input[placeholder*='192.168']")
        url_input.fill(API)
        page.locator("button:has-text('测试连接')").click()
        page.wait_for_selector("text=连通:", timeout=10000)
        conn_text = page.locator("span.text-green-400").first.inner_text()
        check("测试连接-回显对端产品", "tianjun-ai-vision" in conn_text, conn_text)
        page.screenshot(path=f"{OUT}/02_test_connection.png")

        # ---------- 3. UI 改采样配置 → 保存 → 后端落库验证 (T5) ----------
        # 打开「未检出帧采样」这个之前是关的开关 + 启用采样总开关
        sampling_card = page.locator("div", has=page.locator("h3:has-text('现场帧采样回传')")).last
        # 采样总开关 (卡片头部唯一带 active-text 的大开关)
        page.locator(".el-switch", has_text="启用采样").locator("span.el-switch__core").click()
        row = page.locator("div.flex.items-center", has_text="未检出帧采样").first
        row.locator("span.el-switch__core").first.click()
        page.locator("button:has-text('保存配置')").click()
        page.wait_for_selector("text=互连配置已保存", timeout=8000)
        time.sleep(0.5)
        cfg = requests.get(f"{API}/api/v1/interconnect/config", timeout=5).json()
        check("T5-采样总开关落库", cfg["sampling"]["enabled"] is True)
        check("T5-未检出采样落库", cfg["sampling"]["no_detection_enabled"] is True)
        check("T5-平台地址落库", cfg["platform_url"].rstrip("/") == API,
              cfg["platform_url"])
        st = requests.get(f"{API}/api/v1/interconnect/status", timeout=5).json()
        check("T5-状态端点采样激活", st["sampling_active"] is True)
        check("T5-uploader worker 已拉起", st["uploader"]["worker_running"] is True)
        page.screenshot(path=f"{OUT}/03_config_saved.png", full_page=True)

        # ---------- 4. 模型仓库来源标 ----------
        page.goto(f"{BASE}/#/model", wait_until="domcontentloaded")
        page.wait_for_selector("text=UAT演示-划痕检测", timeout=15000)
        page.wait_for_timeout(800)
        check("模型页-训练平台来源标", page.locator(".el-tag:has-text('训练平台')").count() > 0)
        check("模型页-训练分析按钮", page.locator("button:has-text('训练分析')").count() > 0)
        page.screenshot(path=f"{OUT}/04_model_page.png", full_page=True)

        # ---------- 5. 训练分析弹窗 + ECharts ----------
        page.locator("button:has-text('训练分析')").first.click()
        page.wait_for_selector(".el-dialog:has-text('训练分析')", timeout=8000)
        page.wait_for_timeout(1500)  # 等 ECharts 渲染
        dlg = page.locator(".el-dialog", has_text="训练分析").first
        check("分析弹窗-mAP@50 指标", dlg.locator("text=mAP@50").count() > 0)
        canvas = dlg.locator("canvas")
        check("分析弹窗-ECharts canvas", canvas.count() > 0)
        if canvas.count() > 0:
            # canvas 有非透明像素 = 曲线真的画出来了
            painted = page.evaluate(
                """() => {
                    const c = document.querySelector('.el-dialog canvas');
                    if (!c) return false;
                    const ctx = c.getContext('2d');
                    const d = ctx.getImageData(0, 0, c.width, c.height).data;
                    for (let i = 3; i < d.length; i += 400) { if (d[i] > 0) return true; }
                    return false;
                }"""
            )
            check("分析弹窗-曲线已绘制", bool(painted))
        page.screenshot(path=f"{OUT}/05_analysis_dialog.png", full_page=True)

        # ---------- JS 异常检查 ----------
        fatal = [e for e in js_errors if "ResizeObserver" not in e]
        check("全程无 JS 异常", not fatal, "; ".join(fatal[:3]))

        browser.close()

    failed = [r for r in results if not r[1]]
    print(f"\n===== {len(results) - len(failed)}/{len(results)} PASS =====")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
