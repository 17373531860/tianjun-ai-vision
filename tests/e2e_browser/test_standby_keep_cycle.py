"""待机保留在制周期 CI E2E (v3.44.1, 上银 SY3 反馈).

场景: 周期做到一半 (两步已进周期, SOP 卡冒绿光) 时点「待机」。
老行为: 待机走全清路径 — 在制周期/台账/SOP 绿光全部清零, 恢复推理后从零开始。
新契约: 待机=临时暂停 — 后端保留 current_cycle_steps, 前端 SOP 绿卡不灭;
        收工全清仍走「停止」。

依赖 RUNTIME_MODE=test 的后端 (synthetic 端点), 非 test 模式自动跳过。
"""
from __future__ import annotations

import time

import pytest
import requests


FPS = 30


def _synthetic_available(api_url: str) -> bool:
    r = requests.post(f"{api_url}/api/v1/test/synthetic/stop?channel=0", timeout=5)
    return r.status_code != 404


def _stop_all(api_url: str):
    requests.post(f"{api_url}/api/v1/source/detection/stop?channel=0", timeout=10)
    requests.post(f"{api_url}/api/v1/test/synthetic/stop?channel=0", timeout=10)


def _cycle_steps(api_url: str) -> list:
    r = requests.get(f"{api_url}/api/v1/source/detection/results?channel=0", timeout=10)
    return r.json().get("current_cycle_steps") or []


def _green_cards(page) -> int:
    return page.evaluate(
        """() => [...document.querySelectorAll('div')]
            .filter(d => d.className && String(d.className).includes('border-green-500'))
            .length"""
    )


# SOP 卡片逐张状态快照 (标签 → border 类), 用于断言"待机前后视觉状态不变"
_CARD_SNAPSHOT_JS = """() => {
  const title = [...document.querySelectorAll('span')]
    .find(s => s.textContent.trim() === 'SOP流程卡片');
  if (!title) return null;
  const panel = title.closest('.h-44') || title.closest('div');
  const out = {};
  for (const c of panel.querySelectorAll('.w-32')) {
    const label = c.innerText.split('\\n')[0];
    out[label] = String(c.className).split(' ')
      .filter(x => x.includes('border-') || x.includes('opacity')).sort().join('|');
  }
  return out;
}"""


def test_standby_keeps_cycle_and_sop_green(page, base_url, api_url):
    if not _synthetic_available(api_url):
        pytest.skip("后端非 RUNTIME_MODE=test, synthetic 端点不可用")

    import uuid
    _stop_all(api_url)
    time.sleep(1)

    # 前端 SOP 卡片来自 DB 激活项目 (不是 synthetic 临时配置) — 建真项目并激活,
    # 让 synthetic 周期步骤与页面卡片同标签
    pname = f"__e2e_standby_{uuid.uuid4().hex[:5]}"
    _projects = requests.get(f"{api_url}/api/v1/projects", timeout=10).json().get("items", [])
    orig_active = next((p["id"] for p in _projects if p.get("is_active")), None)
    pid = None
    try:
        r = requests.post(f"{api_url}/api/v1/projects", json={
            "name": pname, "task_type": "detection", "logic_mode": "sequential",
        }, timeout=10)
        r.raise_for_status()
        pid = r.json()["id"]
        requests.put(f"{api_url}/api/v1/projects/{pid}", json={
            "steps_config": [
                {"id": 1, "label": "A", "name": "A", "enabled": True},
                {"id": 2, "label": "B", "name": "B", "enabled": True},
            ],
        }, timeout=10).raise_for_status()
        requests.post(f"{api_url}/api/v1/projects/{pid}/activate", timeout=30).raise_for_status()

        # 先开页面: Navbar 冷启动发现 store 与后端激活项目不同步时会补一次
        # activate (会清运行时), 必须让它先同步完再起检测, 否则周期被它擦掉
        page.goto(f"{base_url}/#/monitor", wait_until="domcontentloaded", timeout=15000)
        time.sleep(4)

        # A (0-2s) → B (4-6s) 先后进周期, 之后长时间空白 — first_step 结算模式下
        # 周期保持在制, 正好停在"做到一半"的状态
        timeline = [
            {"from": 0, "to": FPS * 2, "detections": [
                {"label": "A", "confidence": 0.95, "bbox": [0.1, 0.1, 0.3, 0.3]}]},
            {"from": FPS * 4, "to": FPS * 6, "detections": [
                {"label": "B", "confidence": 0.95, "bbox": [0.5, 0.1, 0.3, 0.3]}]},
            {"from": FPS * 8, "to": FPS * 120, "detections": []},
        ]
        r = requests.post(f"{api_url}/api/v1/test/synthetic/start", json={
            "scenario_json": {"name": "standby-keep-e2e", "fps": FPS, "timeline": timeline},
            "channel": 0, "with_project": True, "project_steps": ["A", "B"],
            "project_id": pid}, timeout=15)
        assert r.status_code == 200, r.text
        r = requests.post(f"{api_url}/api/v1/source/detection/start?channel=0",
                          json={"conf": 0.25}, timeout=30)
        assert r.status_code == 200, r.text

        # 等两步都进周期
        deadline = time.time() + 20
        steps = []
        while time.time() < deadline:
            steps = _cycle_steps(api_url)
            if len(steps) >= 2:
                break
            time.sleep(0.5)
        assert len(steps) >= 2, f"20s 内周期未推进到两步: {steps}"

        # 等 SOP 卡片被周期数据点亮 (至少一张绿卡)
        g1 = 0
        deadline = time.time() + 10
        while time.time() < deadline:
            g1 = _green_cards(page)
            if g1 >= 1:
                break
            time.sleep(0.5)
        assert g1 >= 1, f"检测中 SOP 应至少 1 张绿卡, 实际 {g1}"
        snap1 = page.evaluate(_CARD_SNAPSHOT_JS)
        assert snap1, "SOP 面板应渲染"

        # 点「待机」(单工位主控制区)
        page.locator("button", has_text="待机").first.click(timeout=10000)
        time.sleep(3)

        steps_after = _cycle_steps(api_url)
        assert steps_after == steps, f"待机清了在制周期: {steps} -> {steps_after}"
        g2 = _green_cards(page)
        assert g2 >= g1, f"待机后 SOP 绿光丢失: {g1} -> {g2}"
        snap2 = page.evaluate(_CARD_SNAPSHOT_JS)
        assert snap2 == snap1, f"待机后 SOP 卡片视觉状态不得变化: {snap1} -> {snap2}"

        # v3.44.2 (SY3 反馈"待机后开始还是清零"): 点「开始」从待机恢复,
        # 在制周期/SOP 绿光必须原样接续 — 快速恢复路径不得重推项目配置
        # (syncProjectConfig → 后端 apply = 全量重置, 待机保留的状态会被抹掉)
        page.locator("button", has_text="开始").first.click(timeout=10000)
        deadline = time.time() + 15
        resumed = False
        while time.time() < deadline:
            r = requests.get(f"{api_url}/api/v1/source/status?channel=0", timeout=10)
            if r.ok and r.json().get("is_detecting"):
                resumed = True
                break
            time.sleep(0.5)
        assert resumed, "点「开始」后 15s 未恢复检测"
        time.sleep(2)
        steps_resumed = _cycle_steps(api_url)
        assert steps_resumed == steps, f"待机→开始清了在制周期: {steps} -> {steps_resumed}"
        g3 = _green_cards(page)
        assert g3 >= g1, f"待机→开始后 SOP 绿光丢失: {g1} -> {g3}"

        # 「停止」仍是全清语义
        requests.post(f"{api_url}/api/v1/source/detection/stop?channel=0", timeout=10)
        time.sleep(2)
        assert _cycle_steps(api_url) == [], "停止后周期应全清"
    finally:
        _stop_all(api_url)
        if orig_active:
            requests.post(f"{api_url}/api/v1/projects/{orig_active}/activate", timeout=30)
        if pid:
            requests.delete(f"{api_url}/api/v1/projects/{pid}", timeout=10)
