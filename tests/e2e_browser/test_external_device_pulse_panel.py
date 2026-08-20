"""外部设备「Modbus 完成脉冲」CI E2E —— 守住"后端做了、前端没入口"这条回归线。

真开浏览器走一遍现场配置动作: MES → 外部设备 → 添加设备 → 协议选「Modbus 完成脉冲」
→ 断言表单字段齐 → 保存 → 回查后端确认 protocol_config 真落库 → 卡片上「试发脉冲」
按钮真的在。任何人误删协议选项/表单段/按钮, 或改坏保存链路 → 这条用例立刻红。

需后端+前端在跑(conftest 未起会自动 skip)。
"""
import time
import uuid

import requests

DEVICE_PREFIX = "__e2e_pulse_"


def _open_panel(page, base_url):
    page.goto(f"{base_url}/#/mes", wait_until="networkidle")
    page.get_by_text("外部设备", exact=True).first.click()
    page.wait_for_selector("text=数据日志", timeout=8000)


def _select_by_label(page, label_text, option_text):
    """按 label 定位下拉并选一项。

    面板里两种排布都有: el-form-item 包着的(通信协议), 和"小标题 div + 紧邻 select"
    的紧凑网格(脉冲参数段)。先试前者, 拿不到就退到后者。
    """
    lab = page.get_by_text(label_text, exact=True).first
    fi = lab.locator("xpath=ancestor::div[contains(@class,'el-form-item')][1]")
    sel = fi.locator(".el-select").first
    if fi.count() == 0 or sel.count() == 0:
        sel = lab.locator("xpath=following-sibling::*[1]")
    sel.click()
    page.locator(f".el-select-dropdown__item:has-text('{option_text}')").first.click()


def _cleanup(api_url):
    try:
        r = requests.get(f"{api_url}/api/v1/external-devices/", timeout=10)
        if r.status_code != 200:
            return
        for dev in r.json() or []:
            if (dev.get("name") or "").startswith(DEVICE_PREFIX):
                requests.delete(
                    f"{api_url}/api/v1/external-devices/{dev['id']}", timeout=10)
    except Exception as e:
        print(f"[cleanup] 外设清理失败: {e}")


def test_pulse_form_renders_all_controls(page, base_url, api_url):
    """协议选「Modbus 完成脉冲」后，表单字段全部渲染(上次漏测 UI 的同类护栏)。"""
    _open_panel(page, base_url)
    page.get_by_role("button", name="添加设备").first.click()
    page.wait_for_selector("text=通信协议", timeout=6000)
    _select_by_label(page, "通信协议", "Modbus 完成脉冲")
    page.wait_for_selector("text=脉冲宽度 (毫秒)", timeout=6000)

    body = page.evaluate("document.body.innerText")
    required = [
        "连接方式", "从站号 (PLC 站号)", "写入类型", "地址填写方式",
        "脉冲宽度 (毫秒)", "有效值 (ON)", "复位值 (OFF)", "通讯超时 (秒)",
        "逐件覆盖（打螺丝）完成时自动发脉冲", "触发时机", "最短触发间隔 (毫秒)",
        "绑定工位",
    ]
    missing = [x for x in required if x not in body]
    assert not missing, f"完成脉冲表单缺控件: {missing}"

    # 地址预览行要能算出 M100 → 2148（现场靠这行确认点位没填错）
    assert "Modbus 地址 2148" in body, "缺少地址换算预览(台达 M100 应换算成 2148)"


def test_pulse_device_saves_and_shows_button(page, base_url, api_url):
    """填完保存 → 后端 protocol_config 真落库 → 卡片出现「试发脉冲」按钮。"""
    name = f"{DEVICE_PREFIX}{uuid.uuid4().hex[:6]}"
    _cleanup(api_url)
    try:
        _open_panel(page, base_url)
        page.get_by_role("button", name="添加设备").first.click()
        page.wait_for_selector("text=通信协议", timeout=6000)

        lab = page.get_by_text("名称", exact=True).first
        fi = lab.locator("xpath=ancestor::div[contains(@class,'el-form-item')][1]")
        fi.locator("input").first.fill(name)

        _select_by_label(page, "通信协议", "Modbus 完成脉冲")
        page.wait_for_selector("text=脉冲宽度 (毫秒)", timeout=6000)
        _select_by_label(page, "触发时机", "全部覆盖完成时")

        # IP 必填(否则设备线程起不来), 用回环地址即可, 本用例不真连 PLC
        ip_lab = page.get_by_text("IP 地址", exact=True).first
        ip_fi = ip_lab.locator("xpath=ancestor::div[contains(@class,'el-form-item')][1]")
        ip_fi.locator("input").first.fill("127.0.0.1")

        page.locator(".el-dialog__footer").get_by_role(
            "button", name="保存").first.click()
        page.wait_for_selector(".el-message--success", timeout=8000)

        # 全套并行跑时 .el-message--success 可能匹配到残留 toast, GET 会跑在落库前 →
        # 改短轮询 (最长 6s), 治全量回归下的偶发红灯; 单跑行为不变 (首轮即命中)。
        dev = None
        for _ in range(12):
            r = requests.get(f"{api_url}/api/v1/external-devices/", timeout=10)
            assert r.status_code == 200, f"读外设列表失败 http={r.status_code}"
            dev = next((d for d in r.json() if d.get("name") == name), None)
            if dev is not None:
                break
            time.sleep(0.5)
        assert dev is not None, "保存后后端查不到该设备"
        assert dev["protocol"] == "modbus_pulse"
        assert dev["device_role"] == "plc", "完成脉冲设备角色应自动切成 PLC"
        cfg = dev.get("protocol_config") or {}
        assert cfg.get("address_mode") == "delta_m", f"protocol_config 未落库: {cfg}"
        assert cfg.get("address") == 100
        assert cfg.get("pulse_ms") == 300
        assert cfg.get("target") == "coil"
        assert cfg.get("trigger_mode") == "all_covered", "触发时机没存进去"
        assert cfg.get("trigger_enabled") is True

        # 卡片上必须有「试发脉冲」入口
        card = page.locator(".grid > div", has_text=name).first
        card.wait_for(timeout=8000)
        assert card.get_by_role("button", name="试发脉冲").count() >= 1, \
            "完成脉冲设备卡片缺「试发脉冲」按钮"
    finally:
        _cleanup(api_url)


def test_pulse_config_reloads_into_edit_dialog(page, base_url, api_url):
    """编辑回填: 后端存的配置要能原样回到表单(不然客户一编辑就被默认值覆盖)。"""
    name = f"{DEVICE_PREFIX}{uuid.uuid4().hex[:6]}"
    _cleanup(api_url)
    try:
        payload = {
            "name": name, "device_role": "plc", "protocol": "modbus_pulse",
            "ip": "127.0.0.1", "port": 502, "channel_id": 0,
            "parse_mode": "direct", "data_target": "extra_fields",
            "enabled": False,        # 不启用 → 不起设备线程, 用例不依赖真 PLC
            "protocol_config": {
                "transport": "tcp", "slave_id": 3, "target": "register",
                "address_mode": "raw", "address": 4096,
                "on_value": 1, "off_value": 0, "pulse_ms": 550, "timeout": 5,
                "trigger_enabled": True, "trigger_mode": "cycle_ok",
                "cooldown_ms": 2500,
            },
        }
        r = requests.post(f"{api_url}/api/v1/external-devices/",
                          json=payload, timeout=10)
        assert r.status_code == 200, f"预置设备失败 http={r.status_code} {r.text[:200]}"

        _open_panel(page, base_url)
        card = page.locator(".grid > div", has_text=name).first
        card.wait_for(timeout=10000)
        card.get_by_role("button", name="编辑").first.click()
        page.wait_for_selector("text=脉冲宽度 (毫秒)", timeout=8000)

        body = page.evaluate("document.body.innerText")
        assert "Modbus 地址 4096" in body, f"raw 地址未回填, 预览行: {body[:0]}"

        def _num(label):
            lab = page.get_by_text(label, exact=True).first
            wrap = lab.locator("xpath=following-sibling::*[1]")
            return wrap.locator("input").first.input_value()

        assert _num("脉冲宽度 (毫秒)") == "550"
        assert _num("从站号 (PLC 站号)") == "3"
        assert _num("最短触发间隔 (毫秒)") == "2500"
    finally:
        _cleanup(api_url)
