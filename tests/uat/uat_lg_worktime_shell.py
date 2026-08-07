"""LG 工时看板 v1.4.1 展会壳 UAT: 检测中心全屏化 + 品牌顶栏 + 抽屉导航 + 八页互切.

断言:
  1. /monitor 覆盖容器为 fixed 全屏 (盖掉宿主导航栏): 容器 box == 整个视口
  2. 壳顶栏可见: 品牌 logo + 大标题 + 当前页面徽标 + 时钟
  3. 单/双/三工位模式壳完全一致 (topbar/nav 均在)
  4. ☰ 呼出抽屉导航 (8 项, 实时监控 active), 点「项目管理」→ 宿主路由 /project
     且展会 iframe 全屏出现; iframe 内点「实时监控」→ 回 /monitor 壳看板

用法 (dev 环境, 后端 8004 + 前端 6004 + lg-worktime v1.4.1 已激活):
    python tests/uat/uat_lg_worktime_shell.py

产出: evidence/lgwt_shell_{1,2,3}ws.png / lgwt_shell_nav.png / lgwt_shell_project.png / lgwt_shell_back.png
"""
import sys
import time
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

FRONTEND = "http://localhost:6004"
BACKEND = "http://localhost:8004/api/v1"
OUT_DIR = Path(__file__).resolve().parents[2] / "evidence"
OUT_DIR.mkdir(exist_ok=True)
VW, VH = 1680, 945


def stop_all_detection():
    for ch in range(3):
        try:
            requests.post(f"{BACKEND}/source/detection/stop", params={"channel": ch}, timeout=10)
        except Exception:
            pass


def set_channel_count(n: int):
    stop_all_detection()
    time.sleep(0.5)
    r = requests.post(f"{BACKEND}/workstations/mode", json={"channel_count": n, "channels": []}, timeout=30)
    r.raise_for_status()
    print(f"[uat] channel_count -> {n}: {r.json().get('status')}")


def goto_monitor(page, n):
    set_channel_count(n)
    page.goto(f"{FRONTEND}/monitor", wait_until="domcontentloaded")
    page.wait_for_selector(".lgwt-shell", timeout=20000)
    time.sleep(3)


def assert_shell_fullscreen(page, tag):
    box = page.locator(".tj-layout-body-override").bounding_box()
    assert box, f"{tag}: 覆盖容器不存在"
    assert abs(box["x"]) < 2 and abs(box["y"]) < 2, f"{tag}: 容器未贴左上角 {box}"
    assert abs(box["width"] - VW) < 4 and abs(box["height"] - VH) < 4, \
        f"{tag}: 容器未占满视口 {box}"
    assert page.locator(".lgwt-topbar").is_visible(), f"{tag}: 壳顶栏不可见"
    assert page.locator(".lgwt-brand-logo").is_visible(), f"{tag}: 品牌 logo 不可见"
    assert page.locator(".lgwt-apptitle h1").is_visible(), f"{tag}: 大标题不可见"
    assert "实时监控" in page.locator(".lgwt-page-kv").inner_text(), f"{tag}: 页面徽标不对"
    assert len(page.locator(".lgwt-topclock").inner_text()) >= 16, f"{tag}: 时钟未走字"
    # 宿主导航栏必须被盖住: 顶栏区域最顶层元素属于插件壳
    # (探测点取左上角 logo 区, 避开居中的 el-message 临时弹窗)
    top_el_cls = page.evaluate("document.elementFromPoint(120, 25)?.className || ''")
    assert "lgwt" in str(top_el_cls), f"{tag}: 顶部 (120,25) 最顶层不是插件壳: {top_el_cls!r}"
    print(f"[uat] {tag}: 全屏壳 OK box={box}")


def main():
    fails = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page(viewport={"width": VW, "height": VH})
        try:
            # ---- 三种工位数, 壳一致; single/focus 日志列下挂设备状态卡 ----
            for n in (1, 2, 3):
                goto_monitor(page, n)
                assert_shell_fullscreen(page, f"{n}工位")
                if n in (1, 3):
                    assert page.locator(".lgwt-live-col .lgwt-card-oplog").count() == 1, \
                        f"{n}工位: 操作日志应独立成列"
                    assert page.locator(".lgwt-live-col .lgwt-card-device").count() == 1, \
                        f"{n}工位: 日志列下应有设备状态卡"
                    assert page.locator(".lgwt-dev-cam img").first.is_visible(), \
                        f"{n}工位: 设备卡相机图应可见"
                    # 列内固定 2:1 且铺满 (与日志条数无关, 底部不留空)
                    col = page.locator(".lgwt-live-col").bounding_box()
                    lg = page.locator(".lgwt-live-col .lgwt-card-oplog").bounding_box()
                    dv = page.locator(".lgwt-live-col .lgwt-card-device").bounding_box()
                    ratio = lg["height"] / (lg["height"] + dv["height"])
                    assert 0.62 < ratio < 0.72, \
                        f"{n}工位: 操作日志应占列高 2/3 (实际 {ratio:.0%})"
                    gap = (col["y"] + col["height"]) - (dv["y"] + dv["height"])
                    assert abs(gap) < 8, f"{n}工位: 设备卡应贴列底 (距底 {gap:.0f}px)"
                    assert page.locator(".lgwt-rail .lgwt-card-oplog").count() == 0, \
                        f"{n}工位: 右栏不应再有操作日志"
                else:
                    assert page.locator(".lgwt-rail .lgwt-card-oplog").count() == 1, \
                        "2工位: 操作日志仍在右栏"
                page.screenshot(path=str(OUT_DIR / f"lgwt_shell_{n}ws.png"))

            # ---- 抽屉导航: ☰ 呼出, 8 项, 实时监控 active ----
            page.locator(".lgwt-nav-toggle").click()
            page.wait_for_selector(".lgwt-nav.open", timeout=5000)
            time.sleep(0.6)  # 等滑入动画 (0.25s transition) 完成再截图
            items = page.locator(".lgwt-nav-item")
            nav_box = page.locator(".lgwt-nav.open").bounding_box()
            assert nav_box and nav_box["x"] >= -2, f"抽屉未滑入到位: {nav_box}"
            assert items.count() == 8, f"导航应 8 项, 实为 {items.count()}"
            assert "实时监控" in page.locator(".lgwt-nav-item.active").inner_text()
            page.screenshot(path=str(OUT_DIR / "lgwt_shell_nav.png"))
            print("[uat] 抽屉导航 OK")

            # ---- 点「项目管理」→ 宿主 /project + 展会 iframe 全屏 ----
            items.nth(1).click()
            page.wait_for_selector("iframe[src*='showcase-app']", timeout=15000)
            time.sleep(2.5)
            assert "/project" in page.url, f"路由未切到 /project: {page.url}"
            fbox = page.locator("iframe[src*='showcase-app']").bounding_box()
            assert fbox and abs(fbox["width"] - VW) < 4, f"展会 iframe 未全屏: {fbox}"
            page.screenshot(path=str(OUT_DIR / "lgwt_shell_project.png"))
            print("[uat] 项目管理页 = 展会 iframe OK")

            # ---- iframe 内点「实时监控」→ 回 /monitor 壳看板 ----
            frame = page.frame_locator("iframe[src*='showcase-app']")
            frame.locator("#navToggle").click()
            time.sleep(0.6)
            frame.locator(".nav .item[data-route='/monitor']").click()
            page.wait_for_selector(".lgwt-shell", timeout=15000)
            time.sleep(2.5)
            assert "/monitor" in page.url, f"未回到 /monitor: {page.url}"
            assert_shell_fullscreen(page, "回程")
            page.screenshot(path=str(OUT_DIR / "lgwt_shell_back.png"))
            print("[uat] 展会 iframe → 检测中心回程 OK")
        except AssertionError as e:
            fails.append(str(e))
            page.screenshot(path=str(OUT_DIR / "lgwt_shell_FAIL.png"))
        except Exception as e:  # noqa: BLE001
            fails.append(f"异常: {e}")
            try:
                page.screenshot(path=str(OUT_DIR / "lgwt_shell_FAIL.png"))
            except Exception:
                pass
        finally:
            browser.close()

    if fails:
        print(f"[uat] FAIL: {fails}")
        sys.exit(1)
    print("[uat] 展会壳 UAT 全部通过")


if __name__ == "__main__":
    main()
