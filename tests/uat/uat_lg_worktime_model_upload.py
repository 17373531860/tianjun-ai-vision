"""LG 工时看板 UAT: 模型上传全链路 (showcase 模型仓库页真 UI 上传).

现场叙事:
  1. 师傅打开「模型仓库」(展会 iframe 页) → 点「＋ 上传新模型」→ 填名称
     → 选 2K17421.pt → 开始上传 → 看到「✓ 上传完成」+ 长气泡
  2. 后端落库: /models 列表出现新模型, labels 自动解析出 7 个中文步骤类
  3. 其余 4 个模型 (3K11311/3K30919/5K15111/K10916) 走同一 /models/upload
     API 直传, 逐一验证 201 + labels 解析非空
  4. 模型列表 UI 刷新后能看到新模型卡片

用法 (dev: 后端 8004 + 前端 6004 + lg-worktime 插件已激活):
    python tests/uat/uat_lg_worktime_model_upload.py

产出: evidence/lgwt_upload_dialog.png / lgwt_upload_done.png / lgwt_upload_list.png
"""
import sys
import time
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

FRONTEND = "http://localhost:6004"
BACKEND = "http://localhost:8004/api/v1"
SRC = Path("/Users/tianjun/Public/测试使用")
OUT = Path(__file__).resolve().parents[2] / "evidence"
OUT.mkdir(exist_ok=True)
fails = []
front_errors = []

UI_MODEL = "2K17421"                       # 走真 UI 上传
API_MODELS = ["3K11311", "3K30919", "5K15111", "K10916"]  # 走 API 直传


def ok(cond, msg):
    print(("  ✓ " if cond else "  ✗ ") + msg)
    if not cond:
        fails.append(msg)


def hook_frontend(page):
    page.on("console", lambda m: front_errors.append(f"console.{m.type}: {m.text}")
            if m.type == "error" and "favicon" not in m.text else None)
    page.on("pageerror", lambda e: front_errors.append(f"pageerror: {e}"))
    page.on("response", lambda r: front_errors.append(f"HTTP {r.status} {r.url[-80:]}")
            if r.status >= 500 else None)


def models_by_name():
    r = requests.get(f"{BACKEND}/models", timeout=10).json()
    items = r if isinstance(r, list) else r.get("items", [])
    return {m["name"]: m for m in items}


def cleanup(names):
    """幂等: 删掉旧的同名 UAT 模型, 让上传向导不撞唯一约束."""
    for name, m in models_by_name().items():
        if name in names:
            requests.delete(f"{BACKEND}/models/{m['id']}", timeout=10)


def main():
    cleanup([UI_MODEL] + API_MODELS)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page(viewport={"width": 1680, "height": 945})
        hook_frontend(page)

        # ---- 1. 真 UI 上传 2K17421.pt ----
        print("[uat] === showcase 模型仓库页真 UI 上传 ===")
        page.goto(f"{FRONTEND}/#/model", wait_until="domcontentloaded")
        page.wait_for_selector("iframe[src*='showcase-app']", timeout=20000)
        time.sleep(3)
        fr = page.frame_locator("iframe[src*='showcase-app']")
        fr.locator("button[data-open-dialog='uploadModelDialog']").first.click()
        fr.locator("#uploadModelDialog[open]").wait_for(timeout=5000)
        fr.locator("#modelName").fill(UI_MODEL)
        fr.locator("#modelDesc").fill("UAT 真 UI 上传 (工位1 装配模型)")
        fr.locator("#modelFileInput").set_input_files(str(SRC / f"{UI_MODEL}.pt"))
        picked = fr.locator("#modelDropText").inner_text()
        ok("已选" in picked and f"{UI_MODEL}.pt" in picked,
           f"拖拽区应显示已选文件 (实际: {picked})")
        page.screenshot(path=str(OUT / "lgwt_upload_dialog.png"))
        fr.locator("#modelUploadBtn").click()
        # 等成功提示 (本地上传快, 但桥端超时给到 5 分钟)
        fr.locator("#modelDropText").filter(has_text="上传完成").wait_for(timeout=120000)
        page.screenshot(path=str(OUT / "lgwt_upload_done.png"))
        print("  ✓ UI 显示「✓ 上传完成」")

        # 落库 + 标签双向验证
        time.sleep(1)
        got = models_by_name().get(UI_MODEL)
        ok(got is not None, "上传后 /models 应有 2K17421")
        if got:
            labels = got.get("labels") or []
            ok(len(labels) == 7 and "拿取产品" in labels and "放计数板" in labels,
               f"labels 应解析出 7 个中文步骤类 (实际 {len(labels)}: {labels})")
            ok((got.get("file_size") or 0) > 10 * 1024 * 1024,
               f"file_size 应 >10MB (实际 {got.get('file_size')})")

        # ---- 2. 其余 4 个模型 API 直传 ----
        print("[uat] === 其余 4 模型 API 直传 ===")
        for name in API_MODELS:
            fp = SRC / f"{name}.pt"
            with open(fp, "rb") as f:
                r = requests.post(f"{BACKEND}/models/upload",
                                  files={"file": (fp.name, f, "application/octet-stream")},
                                  data={"name": name, "framework": "PyTorch",
                                        "description": "UAT 批量上传"},
                                  timeout=300)
            ok(r.status_code == 201, f"{name} 上传应 201 (实际 {r.status_code}: {r.text[:120]})")
            if r.status_code == 201:
                labels = r.json().get("labels") or []
                ok(len(labels) >= 2, f"{name} labels 应解析非空 (实际 {len(labels)}: {labels})")

        # ---- 3. UI 列表刷新可见 ----
        print("[uat] === 模型列表 UI 可见 ===")
        fr2 = None
        try:
            fr.locator("#mdlRefreshRow").click(timeout=3000)
        except Exception:
            page.reload(wait_until="domcontentloaded")
            page.wait_for_selector("iframe[src*='showcase-app']", timeout=20000)
        time.sleep(3)
        fr2 = page.frame_locator("iframe[src*='showcase-app']")
        body_text = fr2.locator("body").inner_text()
        missing = [n for n in [UI_MODEL] + API_MODELS if n not in body_text]
        ok(not missing, f"模型页应展示全部 5 个新模型 (缺: {missing})")
        page.screenshot(path=str(OUT / "lgwt_upload_list.png"))

        real_errors = [e for e in front_errors if "ResizeObserver" not in e]
        ok(not real_errors, f"前端应无异常 (收集到 {len(real_errors)} 条)")
        for e in real_errors[:10]:
            print("   前端异常:", e)

        browser.close()

    if fails:
        print(f"[uat] FAIL ({len(fails)}): {fails}")
        sys.exit(1)
    print("[uat] 模型上传 UAT 全部通过")


if __name__ == "__main__":
    main()
