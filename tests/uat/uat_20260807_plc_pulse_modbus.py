"""可见浏览器 UAT: 逐件覆盖完成 → Modbus 完成脉冲 → PLC 控气阀。

不需要真台达 PLC —— 脚本内置一个「假 PLC」(手写 Modbus TCP 从站, 只认写单个线圈
FC05 / 写单个寄存器 FC06, 按协议原样回帧), 把收到的每一帧连时间戳记下来。
所以本 UAT 能拿到真凭实据: 界面点一下「试发脉冲」, 假 PLC 就该收到
「线圈 ON → 约 pulse_ms 毫秒后 → 线圈 OFF」两帧。

覆盖:
  1. MES → 外部设备 → 添加设备, 协议下拉有「Modbus 完成脉冲 (PLC 控气阀)」
  2. 表单填 PLC 地址/点位/脉冲宽度/触发时机, 保存 → 后端 protocol_config 真落库
  3. 卡片上「试发脉冲」按钮 → 成功 toast + 假 PLC 真收到 ON/OFF 两帧, 间隔 ≈ 脉冲宽度
  4. 设备状态卡片 pulse_count 累加 + 右侧数据日志出现 PULSE 记录
  5. per_item 自动触发链路(A/B 边沿/冷却)由 tests/test_external_device_modbus_pulse.py
     覆盖 —— 那部分不需要真视频/真模型, 本 UAT 只管"人手配 + 真发出去"这一段

前置: 后端 8001 + 前端 6001 已启动。headless=False 真开浏览器, 截图/日志存本目录。
"""
import json
import socket
import struct
import threading
import time

import requests
from playwright.sync_api import sync_playwright

FE = "http://localhost:6001"
API = "http://localhost:8001/api/v1"
OUT = "tests/uat"
FAKE_PLC_PORT = 5020
PULSE_MS = 1200                  # 拉长便于观察 ON→OFF 两帧间隔
DEV_NAME = f"UAT气阀PLC_{int(time.time())}"

results = []


def step(name, ok, extra=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" :: {extra}" if extra else ""))
    results.append((name, ok, extra))


def shot(page, f):
    page.screenshot(path=f"{OUT}/{f}", full_page=False)
    print(f"  shot -> {OUT}/{f}")


# ==================== 假 PLC (Modbus TCP 从站) ====================
class FakePLC:
    """只实现 FC05(写单线圈)/FC06(写单寄存器): 收到即记录, 按协议原样回帧。"""

    def __init__(self, port: int):
        self.port = port
        self.frames: list = []      # [(ts, fc, address, value)]
        self._stop = threading.Event()
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", port))
        self._srv.listen(4)
        self._srv.settimeout(0.5)
        self._t = threading.Thread(target=self._serve, daemon=True)

    def start(self):
        self._t.start()
        print(f"  假 PLC 已监听 127.0.0.1:{self.port}")

    def stop(self):
        self._stop.set()
        try:
            self._srv.close()
        except Exception:
            pass

    def _serve(self):
        while not self._stop.is_set():
            try:
                cli, _addr = self._srv.accept()
            except (socket.timeout, OSError):
                continue
            threading.Thread(target=self._handle, args=(cli,), daemon=True).start()

    def _handle(self, cli):
        cli.settimeout(5.0)
        try:
            while not self._stop.is_set():
                head = cli.recv(12)
                if not head or len(head) < 12:
                    return
                tid, _pid, _ln, unit, fc, addr, val = struct.unpack(">HHHBBHH", head)
                self.frames.append((time.time(), fc, addr, val))
                print(f"  [假PLC] unit={unit} fc={fc} addr={addr} value=0x{val:04X}")
                # FC05/FC06 的正常响应 = 原样回请求
                cli.sendall(head)
        except Exception:
            return
        finally:
            try:
                cli.close()
            except Exception:
                pass


# ==================== 页面工具 ====================
def open_panel(page):
    page.goto(f"{FE}/#/mes", wait_until="networkidle")
    page.get_by_text("外部设备", exact=True).first.click()
    page.wait_for_selector("text=数据日志", timeout=10000)


def pick(page, label, option):
    """两种排布通吃: el-form-item 包着的 / 小标题 div + 紧邻 select。"""
    lab = page.get_by_text(label, exact=True).first
    fi = lab.locator("xpath=ancestor::div[contains(@class,'el-form-item')][1]")
    sel = fi.locator(".el-select").first
    if fi.count() == 0 or sel.count() == 0:
        sel = lab.locator("xpath=following-sibling::*[1]")
    sel.click()
    time.sleep(0.3)
    page.locator(f".el-select-dropdown__item:has-text('{option}')").first.click()
    time.sleep(0.2)


def fill_by_label(page, label, value):
    lab = page.get_by_text(label, exact=True).first
    fi = lab.locator("xpath=ancestor::div[contains(@class,'el-form-item')][1]")
    box = fi if fi.count() > 0 else lab.locator("xpath=following-sibling::*[1]")
    box.locator("input").first.fill(str(value))


def cleanup_device():
    try:
        for d in requests.get(f"{API}/external-devices/", timeout=10).json() or []:
            if (d.get("name") or "").startswith("UAT气阀PLC_"):
                requests.delete(f"{API}/external-devices/{d['id']}", timeout=10)
    except Exception as e:
        print(f"  (清理外设跳过: {e})")


# ==================== 主流程 ====================
def main():
    plc = FakePLC(FAKE_PLC_PORT)
    plc.start()
    cleanup_device()
    console_errs = []

    with sync_playwright() as p:
        b = p.chromium.launch(headless=False)
        ctx = b.new_context(viewport={"width": 1600, "height": 950},
                            record_video_dir=f"{OUT}/_video_plc_pulse")
        page = ctx.new_page()
        page.on("console",
                lambda m: console_errs.append(m.text) if m.type == "error" else None)

        # ---- 1. 打开外部设备面板 ----
        open_panel(page)
        shot(page, "plc_01_panel.png")
        step("MES → 外部设备 面板可打开", True)

        # ---- 2. 添加「Modbus 完成脉冲」设备 ----
        page.get_by_role("button", name="添加设备").first.click()
        page.wait_for_selector("text=通信协议", timeout=8000)
        fill_by_label(page, "名称", DEV_NAME)
        pick(page, "通信协议", "Modbus 完成脉冲")
        page.wait_for_selector("text=脉冲宽度 (毫秒)", timeout=8000)
        step("协议下拉存在「Modbus 完成脉冲 (PLC 控气阀)」", True)

        fill_by_label(page, "IP 地址", "127.0.0.1")
        fill_by_label(page, "端口", FAKE_PLC_PORT)
        pick(page, "地址填写方式", "Modbus 原始地址")
        fill_by_label(page, "地址", 2148)          # = 台达 M100
        fill_by_label(page, "脉冲宽度 (毫秒)", PULSE_MS)
        pick(page, "触发时机", "周期判合格时")
        pick(page, "绑定工位", "工位 1")
        shot(page, "plc_02_form.png")

        body = page.evaluate("document.body.innerText")
        step("表单字段齐 + 地址预览可见",
             "Modbus 地址 2148" in body, "预览行含换算后地址")

        page.locator(".el-dialog__footer").get_by_role("button", name="保存").first.click()
        page.wait_for_selector(".el-message--success", timeout=10000)
        time.sleep(1.5)

        # ---- 3. 后端落库校验 (UI → 后端) ----
        devs = requests.get(f"{API}/external-devices/", timeout=10).json()
        dev = next((d for d in devs if d.get("name") == DEV_NAME), None)
        step("保存后后端能查到该设备", dev is not None)
        cfg = (dev or {}).get("protocol_config") or {}
        step("protocol_config 真落库",
             cfg.get("target") == "coil" and cfg.get("address") == 2148
             and cfg.get("pulse_ms") == PULSE_MS
             and cfg.get("trigger_mode") == "cycle_ok",
             json.dumps(cfg, ensure_ascii=False))
        step("设备角色自动切 PLC", (dev or {}).get("device_role") == "plc")
        step("绑定工位已落库", (dev or {}).get("channel_id") == 0)

        # ---- 4. 点「试发脉冲」→ 假 PLC 真收到 ON/OFF ----
        page.reload(wait_until="networkidle")
        page.get_by_text("外部设备", exact=True).first.click()
        page.wait_for_selector("text=数据日志", timeout=10000)
        card = page.locator(".grid > div", has_text=DEV_NAME).first
        card.wait_for(timeout=10000)
        step("卡片出现「试发脉冲」按钮",
             card.get_by_role("button", name="试发脉冲").count() >= 1)
        shot(page, "plc_03_card.png")

        plc.frames.clear()
        card.get_by_role("button", name="试发脉冲").first.click()
        page.wait_for_selector(".el-message", timeout=15000)
        toast = page.locator(".el-message").first.inner_text()
        ok_toast = "el-message--success" in (
            page.locator(".el-message").first.get_attribute("class") or "")
        shot(page, "plc_04_pulse_toast.png")
        step("试发脉冲返回成功 toast", ok_toast, toast.strip())

        time.sleep(1.0)
        frames = list(plc.frames)
        writes = [(fc, addr, val) for _ts, fc, addr, val in frames]
        step("假 PLC 收到 ON→OFF 两帧",
             writes == [(5, 2148, 0xFF00), (5, 2148, 0x0000)], str(writes))
        if len(frames) >= 2:
            gap_ms = (frames[1][0] - frames[0][0]) * 1000
            step("ON/OFF 间隔 ≈ 配置的脉冲宽度",
                 PULSE_MS * 0.7 <= gap_ms <= PULSE_MS * 1.6 + 400,
                 f"实测 {gap_ms:.0f}ms / 配置 {PULSE_MS}ms")

        # ---- 5. 状态与日志回写 ----
        status = requests.get(f"{API}/external-devices/status", timeout=10).json()
        st = next((s for s in status if s.get("device_id") == dev["id"]), None)
        step("设备状态已连接 + pulse_count 累加",
             bool(st) and st.get("status") == "connected" and st.get("pulse_count", 0) >= 1,
             json.dumps(st, ensure_ascii=False)[:200] if st else "无状态")

        logs = requests.get(f"{API}/external-devices/logs",
                            params={"device_id": dev["id"], "limit": 10},
                            timeout=10).json()
        raws = [i.get("raw_data") or "" for i in (logs.get("items") or [])]
        step("数据日志出现 PULSE 记录",
             any("PULSE" in r for r in raws), raws[:2])

        page.reload(wait_until="networkidle")
        page.get_by_text("外部设备", exact=True).first.click()
        page.wait_for_selector("text=数据日志", timeout=10000)
        time.sleep(2)
        shot(page, "plc_05_after_pulse.png")

        # ---- 6. 断线场景: PLC 掉电时只报错不崩 ----
        plc.stop()
        time.sleep(0.5)
        card = page.locator(".grid > div", has_text=DEV_NAME).first
        card.get_by_role("button", name="试发脉冲").first.click()
        page.wait_for_selector(".el-message--error", timeout=20000)
        err_toast = page.locator(".el-message--error").first.inner_text()
        shot(page, "plc_06_plc_down.png")
        step("PLC 掉线时给出明确失败提示(不崩不卡)", True, err_toast.strip()[:120])

        health = requests.get(f"{API}/projects", timeout=10)
        step("PLC 掉线后后端仍健康", health.status_code == 200)

        step("浏览器无 console 报错",
             len([e for e in console_errs if "favicon" not in e]) == 0,
             str(console_errs[:3]))

        ctx.close()
        b.close()

    plc.stop()
    cleanup_device()

    passed = sum(1 for _n, ok, _e in results if ok)
    print("\n==================== UAT 汇总 ====================")
    for n, ok, extra in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {n}" + (f"  ({extra})" if extra else ""))
    print(f"\n  {passed}/{len(results)} 通过")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
