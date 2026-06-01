"""UAT — 福建金龙插件 C 双工位布局 (monitor.layout.body slot 覆盖).

客户现场叙事:
  1. 操作员把工位数从 1 切到 2
  2. 检测中心主页应变成「左右/上下双工位视频 + 各工位独立统计卡 +
     SOP 流程卡片(GW1/GW2 分组) + 双工位合并步骤统计 + 共用控制底栏」
  3. 1 工位时插件提示「切到 2 工位双布局才生效」, 切 2 后该提示消失

验证形态 (synthetic 环境, 无需真双工位视频/模型):
  - 后端 8011 RUNTIME_MODE=test + 金龙插件 active
  - 切 channel_count=2 → Playwright 进 Monitor → 验插件双工位 DOM + 截图

起后端:
  TIANJUN_DATA_DIR=/tmp/uat_jinlong_data RUNTIME_MODE=test ENABLE_DEV_MOCKS=1 \
    python -m uvicorn backend.main:app --host 127.0.0.1 --port 8011
  (装插件: POST /api/v1/plugins/install 上传签名包 → activate → 重启后端)
起前端:
  frontend/.env.uat 写 VITE_API_BASE_URL=http://localhost:8011/api/v1
  cd frontend && ./node_modules/.bin/vite --mode uat --port 6011 --strictPort

证据: /tmp/uat_jinlong/shots/C_dual_station.png
"""
import time
import sys
import requests
from playwright.sync_api import sync_playwright

API = "http://127.0.0.1:8011/api/v1"
FE = "http://127.0.0.1:6011"
SHOTS = "/tmp/uat_jinlong/shots"
logs = []


def step(label, ok, detail=""):
    logs.append((label, bool(ok), detail))
    print(f"[{'OK' if ok else '!!'}] {label}  {detail}", flush=True)


def main():
    r = requests.post(f"{API}/workstations/mode", json={"channel_count": 2, "channels": []}, timeout=15)
    step("切换为 2 工位", r.json().get("channel_count") == 2, str(r.json()))
    time.sleep(1.5)

    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu",
                                                   "--disable-blink-features=AutomationControlled"])
        ctx = b.new_context(viewport={"width": 1600, "height": 1000},
                            record_video_dir="/tmp/uat_jinlong/video")
        pg = ctx.new_page()
        pg.set_default_timeout(60000)
        pg.goto(f"{FE}/", wait_until="commit", timeout=90000)
        reloaded = False
        for i in range(12):
            time.sleep(4)
            blen = pg.evaluate("document.body ? document.body.innerText.length : 0")
            if blen and blen > 50:
                break
            if not reloaded and i >= 2:
                try:
                    pg.reload(wait_until="commit", timeout=90000)
                except Exception:
                    pass
                reloaded = True
        time.sleep(3)
        pg.screenshot(path=f"{SHOTS}/C_dual_station.png", full_page=True)
        body = pg.evaluate("document.body.innerText")

        # 1 工位时插件会提示"切换为 2 工位"; 切到 2 后该提示消失 = 双工位布局真生效
        hint_gone = "切换为 2" not in body and "当前为 1 工位" not in body
        step("插件双工位布局已生效(1工位提示消失)", hint_gone)

        # 双工位时每工位一组统计卡 → "检测次数" 应出现 >=2 次 (工位1数据 + 工位2数据)
        det_cards = pg.locator("text=检测次数").count()
        step("双工位各自一组统计卡(检测次数≥2)", det_cards >= 2, f"检测次数卡={det_cards}")

        # SOP 流程卡片应含 GW1/GW2 双工位分组
        sop_dual = ("GW1" in body and "GW2" in body) or "工位切换" in body
        step("SOP 流程卡片含双工位分组", sop_dual)

        ctx.close()
        b.close()

    requests.post(f"{API}/workstations/mode", json={"channel_count": 1, "channels": []}, timeout=15)
    failed = [x for x in logs if not x[1]]
    print(f"\nfailed: {len(failed)}", flush=True)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
