"""可见浏览器 UAT: 每日短信日报 (v3.46, 统一短信通道版)。

覆盖 (T4 真浏览器 + T5 UI→后端双向验证):
- 报警页「短信通知」卡: 选阿里云通道 → 填 AK/SK/签名/模板 → 保存 →
  GET /sms/config 验证落入共享 sms_config.json (与 NG 通知同一份)
- 数据中心出现「短信日报（每天发到手机）」入口
- 日报对话框「发送通道」tab: 只读展示当前通道 = 阿里云短信
- 新建日报规则: 名称/手机号/勾选指标/计数器当日增量/正文模板 → 保存 → GET /sms-report/rules 验证落库
- 预览: 弹出变量表 (不发送)
- 模拟试发: mock 通道 → GET logs 验证 SmsSendLog 落库 success=True

端口可用环境变量覆盖（main 吸收后默认 6001/8001）:
  UAT_FE_URL / UAT_API_URL
headless=False 真开浏览器；截图 + 视频 + run.log 落盘。
"""
import os
import time
import re
import requests
from pathlib import Path
from playwright.sync_api import sync_playwright

FE = os.environ.get("UAT_FE_URL", "http://localhost:6001")
API = os.environ.get("UAT_API_URL", "http://localhost:8001/api/v1")
TS = time.strftime("%Y%m%d_%H%M%S")
OUT = Path(os.environ.get("UAT_OUT_DIR", f"tests/uat/sms_report_{TS}"))
VIDEO_DIR = Path(os.environ.get("UAT_VIDEO_DIR", f"/tmp/uat_video/sms_report_{TS}"))
OUT.mkdir(parents=True, exist_ok=True)
VIDEO_DIR.mkdir(parents=True, exist_ok=True)
RULE_NAME = f"UAT短信日报_{int(time.time())}"
PHONE = "13800000000"
results = []


def step(name, ok, extra=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" :: {extra}" if extra else ""))
    results.append((name, ok))


def shot(page, f):
    path = OUT / f
    page.screenshot(path=str(path), full_page=False)
    print(f"  shot -> {path}")


def maybe_login(page):
    try:
        pw = page.locator("input[type=password]")
        if pw.count() > 0 and pw.first.is_visible():
            page.locator("input:not([type=password])").first.fill("admin")
            pw.first.fill("admin123")
            page.get_by_role("button", name=re.compile("登录|登 录|login", re.I)).first.click()
            time.sleep(2.5)
            print("  已登录 admin")
    except Exception as e:
        print(f"  (登录跳过: {str(e)[:80]})")


def ensure_project():
    """main 开发库可能空项目；没有则建 __uat_ 并激活，返回 (id, created)。"""
    r = requests.get(f"{API}/projects", timeout=10)
    r.raise_for_status()
    items = r.json().get("items") or []
    if items:
        pid = items[0]["id"]
        requests.post(f"{API}/projects/{pid}/activate", timeout=10).raise_for_status()
        print(f"  复用已有项目 id={pid}")
        return pid, False
    name = f"__uat_sms_{int(time.time())}"
    payload = {
        "name": name,
        "task_type": "detection",
        "logic_mode": "sequential",
        "pipeline_config": {},
        "steps_config": [
            {"id": 1, "label": "step_a", "name": "步骤A", "enabled": True},
        ],
        "events_config": [
            {"id": 1, "name": "合格", "type": "ok", "enabled": True},
            {"id": 2, "name": "NG", "type": "ng", "enabled": True},
        ],
        "counters_config": [],
        "alarm_config": {},
        "detection_config": {},
        "data_config": {},
        "default_model_id": None,
        "model_format": "pytorch_fp32",
    }
    cr = requests.post(f"{API}/projects", json=payload, timeout=10)
    cr.raise_for_status()
    pid = cr.json()["id"]
    requests.post(f"{API}/projects/{pid}/activate", timeout=10).raise_for_status()
    print(f"  已创建并激活项目 {name} id={pid}")
    return pid, True


def select_project_if_needed(page):
    """数据中心需要先选中一个项目才渲染主体, 用顶栏项目下拉选第一个。"""
    try:
        if page.get_by_text("请先选择一个项目").count() == 0:
            return
        page.locator(".el-select").first.click()
        time.sleep(0.8)
        page.get_by_role("option").first.click()
        time.sleep(0.5)
        page.get_by_role("button", name=re.compile("选\\s*择")).first.click()
        time.sleep(2.5)
        print("  已选择项目")
    except Exception as e:
        print(f"  (选项目失败: {str(e)[:100]})")


def open_sms_report_entry(page):
    """与 e2e 同路径：数据页 → 数据导出 tab → 短信日报按钮。"""
    page.locator(".el-tabs__item:has-text('数据导出')").first.click(timeout=8000)
    time.sleep(0.5)
    btn = page.locator("button:has-text('短信日报')")
    return btn


def main():
    errs = []
    log_path = OUT / "run.log"
    created_project_id = None
    print(f"UAT FE={FE} API={API} OUT={OUT} VIDEO={VIDEO_DIR}")
    proj_id, created = ensure_project()
    if created:
        created_project_id = proj_id
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(headless=False)
            context = b.new_context(
                viewport={"width": 1440, "height": 900},
                record_video_dir=str(VIDEO_DIR),
                record_video_size={"width": 1440, "height": 900},
            )
            page = context.new_page()
            page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)

            page.goto(f"{FE}/#/data")
            time.sleep(2.5)
            maybe_login(page)

            # 1) 报警页「短信通知」卡 → 选阿里云通道 → 填凭据 → 保存 → 共享配置落库
            page.goto(f"{FE}/#/alarm")
            time.sleep(2.5)
            card = page.locator("[data-testid=sms-config-card]")
            card.scroll_into_view_if_needed()
            time.sleep(0.5)
            page.locator("[data-testid=sms-provider-aliyun]").click()
            time.sleep(0.8)
            # el-input 属性透传: data-testid 直接落在内部 <input> 上
            page.locator("[data-testid=sms-aliyun-ak]").fill("UAT_AK_ID")
            page.locator("[data-testid=sms-aliyun-sk]").fill("UAT_AK_SECRET")
            page.locator("[data-testid=sms-aliyun-sign]").fill("天军视觉")
            page.locator("[data-testid=sms-aliyun-template]").fill("SMS_UAT_001")
            shot(page, "sms_01_alarm_aliyun_form.png")
            page.locator("[data-testid=sms-save]").click()
            time.sleep(2)

            sc = requests.get(f"{API}/sms/config", timeout=5).json()
            ok = (sc.get("provider") == "aliyun"
                  and sc.get("aliyun", {}).get("access_key_id") == "UAT_AK_ID"
                  and sc.get("aliyun", {}).get("sign_name") == "天军视觉"
                  and sc.get("aliyun", {}).get("template_code") == "SMS_UAT_001")
            step("报警页保存阿里云通道 → 共享 sms_config 落库", ok, str(sc)[:160])

            # 2) 数据中心入口 + 日报对话框「发送通道」tab 只读展示
            page.goto(f"{FE}/#/data")
            time.sleep(2.5)
            select_project_if_needed(page)
            btn = open_sms_report_entry(page)
            step("数据中心出现短信日报入口", btn.count() > 0)
            if btn.count() == 0:
                shot(page, "sms_02_no_entry.png")
                raise RuntimeError("短信日报入口不存在，后续步骤中止")
            btn.first.click()
            time.sleep(1.5)
            dialog_visible = page.get_by_text("每日短信日报").first.is_visible()
            step("短信日报对话框打开", dialog_visible)
            shot(page, "sms_02_dialog_open.png")

            page.get_by_role("tab", name=re.compile("发送通道")).first.click()
            time.sleep(1)
            body = page.locator(".sms-report-dialog:visible").last.inner_text()
            ok = ("当前通道" in body and "阿里云短信" in body and "报警设置" in body
                  and "保存服务商配置" not in body)
            step("发送通道 tab 只读展示统一通道 (阿里云)", ok, body[:120].replace("\n", " "))
            shot(page, "sms_02b_channel_tab.png")

            # 3) 规则 tab → 新建规则
            page.get_by_role("tab", name=re.compile("日报规则")).first.click()
            time.sleep(0.8)
            page.get_by_role("button", name=re.compile("新建规则")).first.click()
            time.sleep(1.2)

            page.locator("input[placeholder*='每日 20 点产量日报']").fill(RULE_NAME)
            # 加一个计数器当日增量指标
            page.locator("input[placeholder*='输入项目里的计数器名']").fill("合格总数")
            page.get_by_role("button", name=re.compile("^添加$")).first.click()
            time.sleep(0.5)
            # 加手机号
            page.locator("input[placeholder*='11 位手机号']").fill(PHONE)
            page.get_by_role("button", name=re.compile("^添加$")).nth(1).click()
            time.sleep(0.5)
            # 正文模板 (内容式通道本端渲染; 云模板通道忽略)
            page.locator("textarea[placeholder*='日报: 总数']").fill("【UAT】总数${total_cycles}")
            shot(page, "sms_03_rule_form.png")
            page.get_by_role("button", name=re.compile("^保存$")).first.click()
            time.sleep(1.5)

            # T5: 后端验证规则落库
            rules = requests.get(f"{API}/sms-report/rules", timeout=5).json()["rules"]
            target = next((r for r in rules if r["name"] == RULE_NAME), None)
            ok = (target is not None
                  and PHONE in target["phone_numbers"]
                  and "counters_daily.合格总数" in target["metrics"]
                  and "stats.total_cycles" in target["metrics"]
                  and target.get("content_template") == "【UAT】总数${total_cycles}")
            step("规则落库 (名称/手机号/计数器指标/正文模板)", ok,
                 str(target)[:160] if target else "未找到规则")
            rid = target["id"] if target else None
            shot(page, "sms_04_rule_list.png")

            # 4) 预览
            if rid:
                page.get_by_role("button", name=re.compile("^预览$")).first.click()
                time.sleep(1.5)
                prev_ok = page.get_by_text("日报内容预览").first.is_visible()
                step("预览弹窗打开 (渲染变量不发送)", prev_ok)
                shot(page, "sms_05_preview.png")
                page.keyboard.press("Escape")
                time.sleep(0.8)

                # 5) 模拟试发 → 后端验证发送记录
                page.get_by_role("button", name=re.compile("模拟试发")).first.click()
                time.sleep(2.5)
                shot(page, "sms_06_after_mock_send.png")
                logs = requests.get(f"{API}/sms-report/rules/{rid}/logs", timeout=5).json()["logs"]
                ok = (len(logs) >= 1 and logs[0]["success"] is True
                      and logs[0]["provider"] == "mock"
                      and logs[0]["phone_numbers"] == [PHONE])
                step("模拟试发 SmsSendLog 落库 success", ok, str(logs[:1])[:160])

                # 发送记录 UI 可见
                page.get_by_role("button", name=re.compile("^记录$")).first.click()
                time.sleep(1.2)
                step("发送记录弹窗打开", page.get_by_text("发送记录").first.is_visible())
                shot(page, "sms_07_logs.png")
                page.keyboard.press("Escape")
                time.sleep(0.5)

                # 清理 UAT 规则
                requests.delete(f"{API}/sms-report/rules/{rid}", timeout=5)

            # 恢复短信通道为默认关闭态，避免 UAT 凭据留在开发库
            try:
                requests.put(
                    f"{API}/sms/config",
                    json={
                        "enabled": False,
                        "provider": "at_modem",
                        "at_modem": {"port": "", "baudrate": 115200, "encoding": "auto"},
                        "summary_schedule_mode": "rolling_12h",
                    },
                    timeout=5,
                )
                print("  已恢复 sms_config 默认关闭态")
            except Exception as e:
                print(f"  (恢复 sms_config 失败: {e})")

            context.close()
            b.close()
    finally:
        if created_project_id:
            try:
                requests.delete(f"{API}/projects/{created_project_id}", timeout=10)
                print(f"  已清理 UAT 项目 id={created_project_id}")
            except Exception as e:
                print(f"  (清理 UAT 项目失败: {e})")

    print("\n==== 汇总 ====")
    lines = [f"FE={FE}", f"API={API}", f"OUT={OUT}", f"VIDEO={VIDEO_DIR}"]
    for name, ok in results:
        line = f"  [{'PASS' if ok else 'FAIL'}] {name}"
        print(line)
        lines.append(line)
    fails = [n for n, ok in results if not ok]
    if errs:
        err_line = f"  console errors: {len(errs)} 条 (前3): {errs[:3]}"
        print(err_line)
        lines.append(err_line)
    summary = "ALL PASS" if not fails else f"FAIL: {fails}"
    print(summary)
    lines.append(summary)
    videos = list(VIDEO_DIR.glob("*.webm"))
    lines.append(f"videos={videos}")
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"run.log -> {log_path}")
    if videos:
        print(f"video -> {videos[0]}")
    if fails:
        raise SystemExit(summary)


if __name__ == "__main__":
    main()
