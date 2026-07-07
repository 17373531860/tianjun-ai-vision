"""可见浏览器 UAT: SY 容器累加器「进箱确认方式」前端配置项 (custom_mix 增强)。

覆盖 (T4 看 UI + T5 落库双向验证):
- 打开 SY 项目 → 逻辑设置 → 容器装箱清点卡, 新「进箱确认方式」区块可见
- 默认: 消失满帧确认勾选 / 放托盘动作确认未勾 / 无 OR-AND 单选 (= 老行为零差异)
- 勾「放托盘动作确认」→ 放托盘标签下拉出现 → 选「放托盘」
- 两个都勾 → OR/AND 组合单选出现 → 选 AND
- 保存配置 → GET /api/v1/projects/10 复核 4 键真落库

前端 6001, 后端 8001(hash 路由)。headless=False 真开浏览器, 截图+录像存本目录。
"""
import time
import requests
from playwright.sync_api import sync_playwright

FE = "http://localhost:6001"
BE = "http://localhost:8001/api/v1"
OUT = "tests/uat"
VIDEO = "/tmp/uat_video"
PID = 10  # SY
results = []


def step(name, ok, extra=""):
    tag = "PASS" if ok else "FAIL"
    print(f"[{tag}] {name}" + (f" :: {extra}" if extra else ""))
    results.append((name, ok, extra))


def shot(page, fname):
    path = f"{OUT}/{fname}"
    page.screenshot(path=path, full_page=False)
    print(f"  shot -> {path}")


def cb_checked(loc):
    return "is-checked" in (loc.get_attribute("class") or "")


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=120)
        ctx = browser.new_context(viewport={"width": 1680, "height": 950},
                                  record_video_dir=VIDEO)
        page = ctx.new_page()
        page.goto(FE, wait_until="domcontentloaded")
        time.sleep(3)

        # ───────── 打开 SY 项目 ─────────
        try:
            page.goto(f"{FE}/#/project", wait_until="domcontentloaded")
            time.sleep(2.5)
            page.get_by_placeholder("搜索项目...").fill("SY")
            time.sleep(1)
            # 精确点名为 "SY" 的那个 (避开 "SY 副本")
            page.locator("span.font-bold", has_text="SY").get_by_text("SY", exact=True).first.click()
            time.sleep(2)
            step("打开 SY 项目", True)
        except Exception as e:
            step("打开 SY 项目", False, str(e)[:160])
            shot(page, "sy_cc_err_open.png")
            browser.close()
            return

        # ───────── 进逻辑设置 tab ─────────
        try:
            page.get_by_role("tab", name="逻辑设置").click()
            time.sleep(2)
            # 容器卡可能需滚动到视野
            anchor = page.get_by_text("进箱确认方式").first
            anchor.scroll_into_view_if_needed()
            time.sleep(1)
            shot(page, "sy_cc_01_default.png")
            step("逻辑设置卡出现「进箱确认方式」区块", anchor.count() > 0)
        except Exception as e:
            step("找到「进箱确认方式」区块", False, str(e)[:160])
            shot(page, "sy_cc_err_logic.png")

        frames_cb = page.locator("label.el-checkbox", has_text="消失满帧确认").first
        action_cb = page.locator("label.el-checkbox", has_text="标签动作确认").first

        # ───────── 默认态校验 ─────────
        try:
            f_on = cb_checked(frames_cb)
            a_on = cb_checked(action_cb)
            combine_visible = page.get_by_text("组合逻辑").count() > 0
            step("默认: 消失满帧确认=勾选 / 放托盘动作确认=未勾 / 无组合单选",
                 f_on and (not a_on) and (not combine_visible),
                 f"frames={f_on} action={a_on} combine_shown={combine_visible}")
        except Exception as e:
            step("默认态校验", False, str(e)[:160])

        # ───────── 勾放托盘动作确认 → 选标签 ─────────
        try:
            action_cb.click()
            time.sleep(1)
            # Element Plus el-select 占位符是 span 不是 input placeholder, 用 el-select 包裹元素定位
            sel = page.locator(".el-select", has_text="选择动作标签").first
            step("勾「标签动作确认」后出现动作标签下拉", sel.count() > 0)
            sel.click()
            time.sleep(1)
            page.locator(".el-select-dropdown:visible .el-select-dropdown__item").get_by_text("放托盘", exact=True).first.click()
            time.sleep(1)
            shot(page, "sy_cc_02_action_label.png")
            step("动作标签下拉可选「放托盘」", True)
        except Exception as e:
            step("勾动作确认+选动作标签", False, str(e)[:160])
            shot(page, "sy_cc_err_action.png")

        # ───────── 取消帧确认 → 「消失确认帧」应隐藏 (只服务帧确认路径) ─────────
        # 注: 提示文案(text-gray-500)里也含"消失确认帧"四字, 故用标签类名 text-gray-400 精确定位行内标签
        gone_label = page.locator("span.text-gray-400", has_text="消失确认帧")
        try:
            gone_visible_before = gone_label.count() > 0
            frames_cb.click()  # 取消勾选消失满帧确认 (此时动作确认仍勾, 不会零确认)
            time.sleep(1)
            gone_visible_after = gone_label.count() > 0
            shot(page, "sy_cc_02b_gone_hidden.png")
            step("取消「消失满帧确认」后「消失确认帧」输入框隐藏",
                 gone_visible_before and not gone_visible_after,
                 f"before={gone_visible_before} after={gone_visible_after}")
            frames_cb.click()  # 复勾, 准备做 AND 组合保存
            time.sleep(1)
            step("复勾后「消失确认帧」重新出现", gone_label.count() > 0)
        except Exception as e:
            step("消失确认帧条件显隐", False, str(e)[:160])

        # ───────── 两个都勾 → 出现 OR/AND → 选 AND ─────────
        try:
            combine_visible = page.get_by_text("组合逻辑").count() > 0
            step("两个都勾后出现「组合逻辑」OR/AND 单选", combine_visible)
            and_btn = page.locator("label.el-radio-button", has_text="AND").first
            and_btn.click()
            time.sleep(0.8)
            shot(page, "sy_cc_03_combine_and.png")
            step("选中 AND 组合", "is-active" in (and_btn.get_attribute("class") or ""))
        except Exception as e:
            step("组合逻辑 OR/AND", False, str(e)[:160])

        # ───────── 保存 ─────────
        try:
            page.get_by_role("button", name="保存配置").first.click()
            time.sleep(3)
            shot(page, "sy_cc_04_saved.png")
            step("点击「保存配置」", True)
        except Exception as e:
            step("保存配置", False, str(e)[:160])

        # ───────── API 复核落库 ─────────
        try:
            pc = requests.get(f"{BE}/projects/{PID}", timeout=8).json().get("pipeline_config", {}) or {}
            by_frames = pc.get("custom_mix_container_confirm_by_frames")
            by_action = pc.get("custom_mix_container_confirm_by_action")
            a_label = pc.get("custom_mix_container_action_label")
            combine = pc.get("custom_mix_container_confirm_combine")
            ok = (by_frames is True and by_action is True
                  and a_label == "放托盘" and combine == "and")
            step("API 复核 4 键落库 (frames=T action=T label=放托盘 combine=and)", ok,
                 f"by_frames={by_frames} by_action={by_action} label={a_label!r} combine={combine!r}")
        except Exception as e:
            step("API 复核落库", False, str(e)[:160])

        time.sleep(1)
        ctx.close()
        browser.close()

    print("\n==================== UAT 汇总 ====================")
    ok = sum(1 for _, o, _ in results if o)
    failed = len(results) - ok
    for n, o, e in results:
        print(f"  {'OK ' if o else 'XX '} {n}" + (f"  ({e})" if e else ""))
    print(f"  通过 {ok}/{len(results)}  failed:{failed}")


if __name__ == "__main__":
    main()
