"""验证 synthetic 切走后会恢复原项目配置（修 step_a/b/c 残留 bug）。"""
from __future__ import annotations


REAL_PROJECT_CFG = {
    "task_type": "detect",
    "logic_mode": "sequential",
    "pipeline_config": {},
    "steps_config": [
        {"label": "正面涂黑", "threshold": 0.3, "min_frames": 1},
        {"label": "翻转",     "threshold": 0.3, "min_frames": 1},
        {"label": "反面涂黑", "threshold": 0.3, "min_frames": 1},
        {"label": "放置",     "threshold": 0.3, "min_frames": 1},
    ],
    "events_config": [],
}


def _stop_all(client, ch=0):
    client.post(f"/api/v1/source/detection/stop?channel={ch}")
    client.post(f"/api/v1/test/synthetic/stop?channel={ch}")


def test_synthetic_with_project_then_stop_restores_real_project_config(client, app):
    """先模拟一个真项目已激活 → 启 synthetic + with_project → 停 synthetic → 真项目应恢复。"""
    _stop_all(client, 0)
    from backend.api.source import _get_mgr
    mgr = _get_mgr(0)

    mgr.set_project_config(REAL_PROJECT_CFG)
    real_labels = sorted(mgr.step_conf_thresholds.keys())
    assert "正面涂黑" in real_labels, f"前置失败：真项目未生效, labels={real_labels}"

    r = client.post("/api/v1/test/synthetic/start", json={
        "scenario": "ok_sequential_cycle.json",
        "channel": 0,
        "with_project": True,
    })
    assert r.status_code == 200, r.text[:300]
    synthetic_labels = sorted(mgr.step_conf_thresholds.keys())
    assert "step_a" in synthetic_labels, f"synthetic 临时项目未生效, labels={synthetic_labels}"
    assert "正面涂黑" not in synthetic_labels, "synthetic 期间不应保留真项目标签"

    r = client.post("/api/v1/test/synthetic/stop?channel=0")
    assert r.status_code == 200, r.text[:300]

    after_labels = sorted(mgr.step_conf_thresholds.keys())
    assert "step_a" not in after_labels, \
        f"BUG! synthetic 切走后仍残留 step_a/b/c, labels={after_labels}"
    assert "正面涂黑" in after_labels, \
        f"真项目应恢复, labels={after_labels}"


def test_synthetic_without_project_does_not_touch_existing_config(client):
    """启 synthetic 时不带 with_project，不应影响已有项目配置。"""
    _stop_all(client, 0)
    from backend.api.source import _get_mgr
    mgr = _get_mgr(0)

    mgr.set_project_config(REAL_PROJECT_CFG)
    before = sorted(mgr.step_conf_thresholds.keys())

    r = client.post("/api/v1/test/synthetic/start", json={
        "scenario": "smoke_static_label.json",
        "channel": 0,
        "with_project": False,
    })
    assert r.status_code == 200

    after = sorted(mgr.step_conf_thresholds.keys())
    assert after == before, f"不带 with_project 不应动配置, before={before}, after={after}"

    client.post("/api/v1/test/synthetic/stop?channel=0")
