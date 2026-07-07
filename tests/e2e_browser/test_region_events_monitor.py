"""Monitor 区域事件模式步骤面板（SOP 卡片 + 步骤统计）CI E2E 回归（v3.32）。

背景（客户报障）: 区域事件模式此前不喂 in-flight PT / 周期 PT 合并 / 截图 / 间隔,
且"进行中"判定拿动作名去和画面类别名硬匹配（永远对不上）→ SOP 卡片与步骤统计
全程空白。本回归锁定打通后的三个关键语义:
  1. 步骤行 = 动作规则名（测硬度）而非模型类别名（测硬度笔）
  2. "进行中"点亮看后端 in-flight PT（动作 episode 命中累计中）
  3. 动作确认即 OK; 同动作重复出现（复检序列）不做"重复=NG"的顺序推断标红

注入手法与 test_sop_step_panel.py 同款: route 拦截 /source/status +
/source/detection/results, 数据走前端完整真实通路, 不 mock 前端内部状态。
"""
from __future__ import annotations

import json
import time
import uuid

import requests


_CARD_CLASS_JS = """(label) => {
  const title = [...document.querySelectorAll('span')]
    .find(s => s.textContent.trim() === 'SOP流程卡片');
  if (!title) return null;
  const panel = title.closest('.h-44');
  if (!panel) return null;
  const card = [...panel.querySelectorAll('.w-32')]
    .find(c => c.innerText.includes(label));
  return card ? card.className : null;
}"""

AB_RECT = [[0.5, 0.4], [0.9, 0.4], [0.9, 1.0], [0.5, 1.0]]
C_RECT = [[0.85, 0.0], [1.0, 0.0], [1.0, 1.0], [0.85, 1.0]]


def _wait_card_class(page, label, predicate, timeout_s=8.0):
    deadline = time.time() + timeout_s
    cls = None
    while time.time() < deadline:
        cls = page.evaluate(_CARD_CLASS_JS, label)
        if cls is not None and predicate(cls):
            return True, cls
        time.sleep(0.3)
    return False, cls


def test_区域事件模式_sop卡片进行中与确认点亮(page, base_url, api_url):
    phase = {"n": 0}

    def handle_status(route):
        resp = route.fetch()
        try:
            data = resp.json()
        except Exception:
            route.fulfill(response=resp)
            return
        data.update({"is_running": True, "is_detecting": True,
                     "source_type": "video", "model_loaded": True})
        route.fulfill(status=200, headers={"content-type": "application/json"},
                      body=json.dumps(data))

    def handle_results(route):
        resp = route.fetch()
        try:
            data = resp.json()
        except Exception:
            route.fulfill(response=resp)
            return
        data.update({"is_detecting": True, "is_running": True, "fps": 30})
        if phase["n"] == 1:
            # 动作 episode 命中累计中 (还没确认): 只有 in-flight, 无周期序列
            data.update({
                "current_cycle_steps": [],
                "step_inflight_durations": {"测硬度": 1.2},
            })
        elif phase["n"] == 2:
            # 复检式序列: 扫码出现 2 次 (合法), 两动作均已闭合 (有权威 PT)
            data.update({
                "current_cycle_steps": ["测硬度", "扫码", "扫码"],
                "current_cycle_id": 12345,
                "step_counts": {"测硬度": 1, "扫码": 2},
                "cycle_sum_step_durations": {"测硬度": 2.0, "扫码": 1.0},
                "step_intervals": {"扫码": 1.5},
                "step_inflight_durations": {},
            })
        route.fulfill(status=200, headers={"content-type": "application/json"},
                      body=json.dumps(data))

    pname = f"__e2e_rem_{uuid.uuid4().hex[:5]}"
    _projects = requests.get(f"{api_url}/api/v1/projects", timeout=10).json().get("items", [])
    orig_active = next((p["id"] for p in _projects if p.get("is_active")), None)
    pid = None
    try:
        r = requests.post(f"{api_url}/api/v1/projects", json={
            "name": pname, "task_type": "detection", "logic_mode": "region_events",
        }, timeout=10)
        r.raise_for_status()
        pid = r.json()["id"]
        requests.put(f"{api_url}/api/v1/projects/{pid}", json={
            "steps_config": [
                {"id": 1, "label": "工件", "enabled": True},
                {"id": 2, "label": "测硬度笔", "enabled": True},
                {"id": 3, "label": "扫码枪", "enabled": True},
            ],
            "pipeline_config": {
                "region_events": {
                    "enabled": True,
                    "rules": [
                        {"id": "r1", "name": "测硬度", "type": "overlap",
                         "subject_label": "测硬度笔", "object_label": "工件",
                         "region": AB_RECT, "region_mode": "and", "min_frames": 15},
                        {"id": "r2", "name": "扫码", "type": "overlap",
                         "subject_label": "扫码枪", "object_label": "工件",
                         "region": AB_RECT, "region_mode": "or", "min_frames": 10},
                        {"id": "r3", "name": "下工件", "type": "region_exit",
                         "subject_label": "工件", "region": C_RECT,
                         "min_frames": 3, "gone_frames": 8, "settle": True},
                    ],
                },
            },
        }, timeout=10).raise_for_status()
        requests.post(f"{api_url}/api/v1/projects/{pid}/activate", timeout=30).raise_for_status()

        page.route("**/source/status*", handle_status)
        page.route("**/source/detection/results*", handle_results)
        page.goto(f"{base_url}/#/monitor", wait_until="domcontentloaded", timeout=15000)
        time.sleep(3.0)

        # 1) 步骤行 = 动作规则名, 不出现模型类别名
        body = page.evaluate("document.body.innerText")
        assert "SOP流程卡片" in body, "SOP 面板应渲染"
        for name in ("测硬度", "扫码", "下工件"):
            assert name in body, f"动作规则名 {name} 应成为步骤行"
        cls0 = page.evaluate(_CARD_CLASS_JS, "扫码")
        assert cls0 and "opacity-60" in cls0, f"初始应 pending 置灰: {cls0}"

        # 2) in-flight 驱动"进行中"点亮 (动作名 ≠ 画面类别名, 不能靠 detections 匹配)
        phase["n"] = 1
        ok1, cls1 = _wait_card_class(
            page, "测硬度", lambda c: "border-cyan-500" in c)
        assert ok1, f"in-flight 中的动作卡片应青色点亮: {cls1}"

        # 3) 确认即 OK + 重复动作不标红 (复检序列合法, 结算判定归后端)
        phase["n"] = 2
        ok2, cls2 = _wait_card_class(
            page, "测硬度", lambda c: "border-green-500" in c)
        assert ok2, f"已确认动作卡片应绿色: {cls2}"
        ok3, cls3 = _wait_card_class(
            page, "扫码",
            lambda c: "border-green-500" in c and "border-red-500" not in c)
        assert ok3, f"重复动作 (复检) 不应被前端标红: {cls3}"
        cls4 = page.evaluate(_CARD_CLASS_JS, "下工件")
        assert cls4 and "opacity-60" in cls4, f"未做动作应保持置灰: {cls4}"
        assert "1.5s" in page.evaluate("document.body.innerText"), "动作间隔 1.5s 应上屏"
    finally:
        page.unroute_all(behavior="ignoreErrors")
        if orig_active:
            requests.post(f"{api_url}/api/v1/projects/{orig_active}/activate", timeout=30)
        if pid:
            requests.delete(f"{api_url}/api/v1/projects/{pid}", timeout=10)
