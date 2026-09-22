"""v3.60.1c 随视觉周期结算 — 真管线端到端回归 (synthetic, 无模型无硬件)。

复刻 2026-09-21 六和现场事故链 (a 补丁翻车复盘):
  · a 包时代: 扫收尾码 closing 自行结算 / 少扫 NG 挂起占组
    → 下一件母排码被「超出应扫数量」拒收 → 码与工件从此错位级联混码;
  · 且现场开了 scan_required, 周期行不创建 → cycle_end 钩子死路,
    钩子里的组收口代码永远不执行。
  · c 版语义: 开关开着时组只进码不自行结算 (closing/码齐/挂起全让位),
    视觉末步结算瞬间一刀切收口 — 码够 OK / 不够 NG, 无条件翻篇。

本文件走「真项目(detection+last_step) → 真激活 → synthetic 视觉周期
→ 真扫码 API → VSM._settle_detection_cycle → 末步锚 → 异步收口线程
→ scan-collect state/records」完整真实管线, 不打任何桩。
与 tests/test_scan_collect_vision_settle.py (引擎/结算 mixin 层单测) 互补。

时间轴 (fps=60):
  帧 0-90    芯子 出现 (0-1.5s)   ← 周期开启; 扫码都在这窗口内注入
  帧 120-210 盖板 出现 (2.0-3.5s) ← 末步; 首现时刻即组归属锚
  帧 211-900 空白                  ← 盖板消失确认(0.5s) → last_step 结算
"""
from __future__ import annotations

import time
import uuid

import pytest

CH = 0
FPS = 60

SCENARIO = {
    "name": "vision_settle_chain",
    "fps": FPS,
    "timeline": [
        {"from": 0, "to": 90, "detections": [
            {"label": "芯子", "confidence": 0.9, "bbox": [0.2, 0.2, 0.2, 0.2]},
        ]},
        {"from": 120, "to": 210, "detections": [
            {"label": "盖板", "confidence": 0.9, "bbox": [0.5, 0.5, 0.2, 0.2]},
        ]},
        {"from": 211, "to": 900, "detections": []},
    ],
}


def _project_payload(name: str) -> dict:
    steps = []
    for i, lb in enumerate(["芯子", "盖板"], start=1):
        steps.append({
            "id": i, "label": lb, "enabled": True, "threshold": 50,
            "min_frames": 1, "detection_type": "dynamic",
            "accept_once": True, "disappear_delay": 0.5,
        })
    return {
        "name": name,
        "task_type": "detect",
        "logic_mode": "detection",
        "pipeline_config": {
            "detection_steps": [1, 2],
            "settlement_mode": "last_step",
            "idle_timeout_seconds": 0,
        },
        "steps_config": steps,
        "events_config": [
            {"id": 1, "name": "合格", "show_notification": False,
             "require_ack": False, "actions": []},
            {"id": 2, "name": "不合格", "show_notification": False,
             "require_ack": False, "actions": []},
        ],
        "counters_config": [],
        "data_config": {},
    }


def _put_scan_collect(client, project_id: int):
    r = client.put(f"/api/v1/scan-collect/config?project_id={project_id}", json={
        "enabled": True,
        "slots": [
            {"key": "chip", "label": "芯子码", "count": 2,
             "regex": "^\\d{13}$"},
            {"key": "fixture", "label": "工装码", "count": 1,
             "regex": "^H-C", "role": "closing",
             "dedup_cross_group": "off"},
        ],
        "settle_on": "closing",
        "settle_on_vision_cycle": True,   # ← 被测开关
        "dedup_in_group": "ng_alarm",
        "dedup_cross_group": "off",
        "on_overflow": "reject",
        "on_unmatched": "reject",
        "timeout_sec": 0,
        "event_ok_id": 1, "event_ng_id": 2,
        "ng_pending": True,
        "idle_remind_sec": 0,
        "standby_silent": False,
        "vision_gate": False,
        "count_on_settle": False,
    })
    assert r.status_code == 200, r.text[:300]


def _scan(client, code: str):
    r = client.post("/api/v1/scanner/simulate",
                    json={"barcode": code, "channel_id": CH})
    assert r.status_code == 200, f"simulate {code}: {r.text[:200]}"


def _state(client) -> dict:
    return client.get(f"/api/v1/scan-collect/state?channel={CH}").json()


def _wait_settled(client, timeout: float = 25.0) -> dict:
    """轮询到 last_settled 出现 (视觉末步结算 → 异步收口线程落账)。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = _state(client)
        if st.get("last_settled"):
            return st["last_settled"]
        time.sleep(0.3)
    pytest.fail(f"等 {timeout}s 组仍未被视觉周期收口: state={_state(client)}")


def _wait_settled_group(client, gid: str, timeout: float = 25.0) -> dict:
    """轮询到指定组被收口 (防上一组残留 last_settled 串台)。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        settled = _state(client).get("last_settled") or {}
        if settled.get("group_id") == gid:
            return settled
        time.sleep(0.3)
    pytest.fail(f"等 {timeout}s 组 {gid} 仍未被视觉周期收口: state={_state(client)}")


@pytest.fixture
def vision_project(client):
    """真项目 + 真激活 + 多码采集配置; 收尾停干净并删项目。"""
    name = f"__sc_vsettle_{uuid.uuid4().hex[:8]}"
    r = client.post("/api/v1/projects/", json=_project_payload(name))
    assert r.status_code in (200, 201), r.text[:300]
    pid = r.json()["id"]
    r = client.post(f"/api/v1/projects/{pid}/activate")
    assert r.status_code == 200, r.text[:300]
    _put_scan_collect(client, pid)
    # 清掉上一个用例残留的组/last_settled (引擎单例跨用例存活)
    client.post("/api/v1/scan-collect/clear", json={"channel_id": CH})

    yield pid

    client.post(f"/api/v1/source/detection/stop?channel={CH}")
    client.post(f"/api/v1/test/synthetic/stop?channel={CH}")
    client.post("/api/v1/scan-collect/clear", json={"channel_id": CH})
    client.delete(f"/api/v1/projects/{pid}")


def _start_chain(client):
    r = client.post("/api/v1/test/synthetic/start", json={
        "scenario_json": SCENARIO, "channel": CH, "with_project": False,
    })
    assert r.status_code == 200, r.text[:300]
    r = client.post(f"/api/v1/source/detection/start?channel={CH}",
                    json={"conf": 0.25, "iou": 0.45})
    assert r.status_code == 200, r.text[:300]


def test_少扫_视觉末步结算一刀切NG_收口翻篇(client, vision_project):
    """现场链 1: 只扫 1 芯子 (缺 1 芯子 + 工装码) → 组不挂起不占位,
    盖板消失结算瞬间 → 组 NG 收口, 面板翻篇 (下一件从零开始)。"""
    _start_chain(client)
    _scan(client, "9260000000001")

    st = _state(client)
    got = {s["key"]: s["got"] for s in st.get("slots", [])}
    assert got.get("chip") == 1, f"扫码未进组: {st}"
    gid = st.get("group_id")
    assert gid

    settled = _wait_settled(client)
    assert settled["is_good"] is False, settled
    assert settled["result"].startswith("ng"), settled
    assert "缺" in (settled.get("reason") or ""), settled
    assert settled.get("missing"), settled

    # 翻篇: 组已清, 新件从零开始
    st2 = _state(client)
    assert not st2.get("group_id"), f"组未翻篇: {st2}"

    # 落库: records 回填 group_result=ng*
    recs = client.get(
        f"/api/v1/scan-collect/records?group_id={gid}").json()
    assert len(recs) == 1
    assert recs[0]["group_result"].startswith("ng")


def test_码齐_收尾码不自行结算_视觉结算OK(client, vision_project):
    """现场链 2 (结算主权): 扫满 2 芯子 + 工装收尾码 —
    closing 不再当场结算 (a 包就是这里抢跑造成重复结算),
    组保持在位等视觉; 盖板消失结算瞬间 → 组 OK 收口。"""
    _start_chain(client)
    _scan(client, "9260000000001")
    _scan(client, "9260000000002")
    _scan(client, "H-C035-527-5")

    # ★ 判别断言 (a 版补丁在这必红): 收尾码扫完组必须还开着
    st = _state(client)
    gid = st.get("group_id")
    assert gid, f"closing 抢跑结算了 (a 版回归): {st}"
    leaked = (st.get("last_settled") or {}).get("group_id")
    assert leaked != gid, f"closing 抢跑结算了本组: {st}"
    got = {s["key"]: s["got"] for s in st.get("slots", [])}
    assert got == {"chip": 2, "fixture": 1}

    settled = _wait_settled_group(client, gid)
    assert settled["result"] == "ok", settled
    assert settled.get("workpiece_sn") == "H-C035-527-5", settled

    st2 = _state(client)
    assert not st2.get("group_id"), f"组未翻篇: {st2}"

    recs = client.get(
        f"/api/v1/scan-collect/records?group_id={gid}").json()
    assert len(recs) == 3
    assert all(r["group_result"] == "ok" for r in recs)
