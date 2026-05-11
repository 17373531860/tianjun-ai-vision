"""插件系统「管理链」UAT — 2026-05-12 (路径 A 有限验收)

只验证以下管理链：
  1. Settings → 插件管理 Tab 可达
  2. 上传签好的 .tjvplugin 包
  3. 后端 verifier 校验通过 → 解压 → 写 PluginRecord state=installed
  4. UI 列表出现新行
  5. 点"激活" → DB is_active=True, active_customer_code 切换
  6. UI 状态 tag 由 installed 变 active
  7. 点"停用" → is_active=False
  8. 点"卸载" → 列表清空
  9. 三个 Tier 包都跑一遍（每次 setup 时先清空状态）

⚠️ 本脚本 *不* 验证：
  - 激活后 Tier 1 主题真生效（前端没消费 manifest, 见 ISSUES.md 缺口 1）
  - 激活后 Tier 2 路由/Pinia 真注册（前端没 dynamic import 加载器, 缺口 2）
  - 激活后 Tier 3 hook/router/table 真接入（后端 PluginManager 接口与 demo 不匹配, 缺口 3）

产物：
  evidence/plugin_uat_2026-05-12/
    ├── video.webm                                       (全程 1280x800)
    ├── 00_settings_landing.png
    ├── 01_plugin_tab_empty.png
    ├── 02_tier1_uploaded.png
    ├── 03_tier1_active.png
    ├── 04_tier1_deactivated.png
    ├── 05_tier1_removed.png
    ├── 06_tier2_active.png
    ├── 07_tier3_active.png
    ├── api_state.json                                   (各阶段 GET /api/v1/plugins 快照)
    └── (UAT 脚本 stdout 也 tee 到 logs/uat_run.log)

跑法：
  conda activate tianjun-runtime
  PYTHONPATH=. python tests/manual_uat/plugin_management_chain_uat.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests
from playwright.sync_api import Page, sync_playwright

REPO_ROOT = Path("/home/qianqian/桌面/word/tianjun副本")
EVIDENCE_DIR = REPO_ROOT / "evidence" / "plugin_uat_2026-05-12"
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
SIGNED_DIR = Path("/tmp/tjv-signed")

FRONTEND_URL = "http://localhost:6001"
API_URL = "http://localhost:8001"
CUSTOMER_CODE = "internal-demo"

TIERS = [
    ("tier1", SIGNED_DIR / "ACME_White_Label_Theme-1.0.0-internal-demo.tjvplugin"),
    ("tier2", SIGNED_DIR / "Factory_Dashboard_UI-1.0.0-internal-demo.tjvplugin"),
    ("tier3", SIGNED_DIR / "Fullstack_MES_Extension-1.0.0-internal-demo.tjvplugin"),
]


# ----------------- API helpers -----------------

def api_state() -> dict:
    r = requests.get(f"{API_URL}/api/v1/plugins", timeout=5)
    r.raise_for_status()
    return r.json()


def api_force_clean() -> None:
    """通过 API 把当前所有插件清干净（保证 UAT 从空状态开始/Tier 切换前）。"""
    state = api_state()
    for item in state.get("items", []):
        cc = item["customer_code"]
        if item.get("is_active"):
            requests.post(f"{API_URL}/api/v1/plugins/{cc}/deactivate", timeout=5)
        requests.delete(f"{API_URL}/api/v1/plugins/{cc}", timeout=5)


# ----------------- UI helpers -----------------

def open_plugin_tab(page: Page) -> None:
    page.goto(f"{FRONTEND_URL}/#/settings", wait_until="domcontentloaded", timeout=15000)
    page.wait_for_load_state("networkidle", timeout=10000)
    page.locator("text=插件管理").first.wait_for(state="visible", timeout=8000)
    page.locator("text=插件管理").first.click()
    time.sleep(0.8)


def upload_plugin(page: Page, plugin_path: Path) -> None:
    """el-upload 内部就是 <input type='file'>，set_input_files 直接喂。"""
    inputs = page.locator("input[type='file']")
    inputs.first.set_input_files(str(plugin_path))
    page.wait_for_function(
        "() => document.body.innerText.includes('安装成功') || document.body.innerText.includes('插件安装') || document.body.innerText.includes('安装失败')",
        timeout=10000,
    )
    time.sleep(0.8)


def click_table_button(page: Page, button_text: str) -> None:
    btn = page.locator(f"button:has-text('{button_text}')").first
    btn.wait_for(state="visible", timeout=6000)
    btn.click()
    time.sleep(0.6)


def confirm_messagebox_if_any(page: Page) -> None:
    try:
        confirm = page.locator(".el-message-box .el-button--primary").first
        if confirm.is_visible(timeout=1500):
            confirm.click()
            time.sleep(0.4)
    except Exception:
        pass


def shot(page: Page, name: str) -> Path:
    p = EVIDENCE_DIR / f"{name}.png"
    page.screenshot(path=str(p), full_page=True)
    print(f"  [SHOT] {p}")
    return p


# ----------------- main flow -----------------

def main() -> int:
    snapshots: dict[str, object] = {}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            viewport={"width": 1280, "height": 800},
            record_video_dir=str(EVIDENCE_DIR),
            record_video_size={"width": 1280, "height": 800},
        )
        page = ctx.new_page()

        try:
            print("[1] 打开设置页")
            page.goto(f"{FRONTEND_URL}/#/settings", wait_until="domcontentloaded", timeout=15000)
            page.wait_for_load_state("networkidle", timeout=10000)
            time.sleep(0.5)
            shot(page, "00_settings_landing")

            print("[2] 切到插件管理 Tab + 强制清空旧状态")
            api_force_clean()
            open_plugin_tab(page)
            shot(page, "01_plugin_tab_empty")
            snapshots["01_after_open"] = api_state()

            # ---- Tier 1 ----
            tier, path = TIERS[0]
            assert path.is_file(), f"包不存在: {path}"
            print(f"[3] 上传 {tier}: {path.name}")
            upload_plugin(page, path)
            shot(page, "02_tier1_uploaded")
            snapshots["02_tier1_uploaded"] = api_state()

            print("[4] 点激活")
            click_table_button(page, "激活")
            confirm_messagebox_if_any(page)
            time.sleep(1.5)
            shot(page, "03_tier1_active")
            snapshots["03_tier1_active"] = api_state()

            print("[5] 点停用")
            click_table_button(page, "停用")
            confirm_messagebox_if_any(page)
            time.sleep(1.2)
            shot(page, "04_tier1_deactivated")
            snapshots["04_tier1_deactivated"] = api_state()

            print("[6] 点卸载")
            click_table_button(page, "卸载")
            confirm_messagebox_if_any(page)
            time.sleep(1.2)
            shot(page, "05_tier1_removed")
            snapshots["05_tier1_removed"] = api_state()

            # ---- Tier 2 ----
            tier, path = TIERS[1]
            print(f"[7] 上传 + 激活 {tier}: {path.name}")
            api_force_clean()
            page.reload()
            time.sleep(1.0)
            open_plugin_tab(page)
            upload_plugin(page, path)
            click_table_button(page, "激活")
            confirm_messagebox_if_any(page)
            time.sleep(1.5)
            shot(page, "06_tier2_active")
            snapshots["06_tier2_active"] = api_state()

            # ---- Tier 3 ----
            tier, path = TIERS[2]
            print(f"[8] 上传 + 激活 {tier}: {path.name}")
            api_force_clean()
            page.reload()
            time.sleep(1.0)
            open_plugin_tab(page)
            upload_plugin(page, path)
            click_table_button(page, "激活")
            confirm_messagebox_if_any(page)
            time.sleep(1.5)
            shot(page, "07_tier3_active")
            snapshots["07_tier3_active"] = api_state()

            # ---- 收尾清理（避免污染开发机后续状态） ----
            print("[9] 清理 — 所有插件 deactivate + delete")
            api_force_clean()
            snapshots["08_final_clean"] = api_state()
            page.reload()
            time.sleep(1.0)
            open_plugin_tab(page)
            shot(page, "08_final_clean")

        except Exception as exc:
            print(f"[ERR] {exc}")
            shot(page, "ZZ_error_state")
            raise
        finally:
            ctx.close()
            browser.close()

    api_state_path = EVIDENCE_DIR / "api_state.json"
    api_state_path.write_text(json.dumps(snapshots, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[OK] api_state 快照 → {api_state_path}")
    print(f"[OK] 截图 + 视频 → {EVIDENCE_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
