"""NG 步骤 TOP3 累计统计 — 待机/重开 sync 配置不应清零, 仅 reset_stats / 切项目清零."""
from __future__ import annotations


def _project_cfg(project_id: int, name: str):
    return {
        "id": project_id,
        "name": name,
        "task_type": "detect",
        "logic_mode": "sequential",
        "pipeline_config": {},
        "steps_config": [
            {"label": "步骤A", "threshold": 0.3, "min_frames": 1},
            {"label": "步骤B", "threshold": 0.3, "min_frames": 1},
        ],
        "events_config": [],
        "counters_config": [],
    }


def test_ng_top3_survives_reapply_same_project(client):
    """模拟 Monitor「待机 → 开始」前的 syncProjectConfig: 同项目重复 apply 不清 TOP3."""
    from backend.api.source import _get_mgr

    mgr = _get_mgr(0)
    cfg = _project_cfg(88001, "ng-top3-persist")
    mgr.set_project_config(cfg)
    mgr.ng_step_cycle_counts = {"步骤A": 4, "步骤B": 2}

    mgr.set_project_config(cfg)

    assert mgr.ng_step_cycle_counts == {"步骤A": 4, "步骤B": 2}


def test_ng_top3_cleared_on_reset_stats(client):
    from backend.api.source import _get_mgr

    mgr = _get_mgr(0)
    mgr.set_project_config(_project_cfg(88002, "ng-top3-reset"))
    mgr.ng_step_cycle_counts = {"步骤A": 3}

    mgr.reset_stats()

    assert mgr.ng_step_cycle_counts == {}


def test_ng_top3_cleared_on_project_switch(client):
    from backend.api.source import _get_mgr

    mgr = _get_mgr(0)
    mgr.set_project_config(_project_cfg(88003, "proj-a"))
    mgr.ng_step_cycle_counts = {"步骤A": 7}

    mgr.set_project_config(_project_cfg(88004, "proj-b"))

    assert mgr.ng_step_cycle_counts == {}
