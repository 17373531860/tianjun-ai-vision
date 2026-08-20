"""v3.51 激活项目收养策略开关 (activate.adopt_unbound) 测试.

背景 (2026-08-14 捷昌 B 站): 多工位下某通道绑定被抹掉后, 全局激活会把该
无绑定通道收养进当前项目并写死绑定 → "两个工位的项目都变成小件"。

覆盖:
1. _get_adopt_unbound 默认 True / KV=0 时 False
2. 开关关时 _sync_project_config_to_channels 不收养无绑定通道
3. 开关开时保持存量收养行为 (零差异)
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ============================================================
# _get_adopt_unbound
# ============================================================


def _kv_row(value):
    row = MagicMock()
    row.value = value
    return row


def test_adopt_unbound_无KV默认开():
    from backend.api.projects import _get_adopt_unbound
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    assert _get_adopt_unbound(db) is True


@pytest.mark.parametrize("raw,expected", [
    ("1", True), ("true", True), ("", True),
    ("0", False), ("false", False), ("off", False), ("False", False),
])
def test_adopt_unbound_KV取值(raw, expected):
    from backend.api.projects import _get_adopt_unbound
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = _kv_row(raw)
    assert _get_adopt_unbound(db) is expected


def test_adopt_unbound_读取异常按默认开():
    from backend.api.projects import _get_adopt_unbound
    db = MagicMock()
    db.query.side_effect = RuntimeError("db down")
    assert _get_adopt_unbound(db) is True


# ============================================================
# _sync_project_config_to_channels 收养行为
# ============================================================


def _make_project(pid=2, name="小件-工位2"):
    p = MagicMock()
    p.id = pid
    p.name = name
    p.logic_mode = "tracking"
    p.steps_config = []
    p.pipeline_config = {}
    p.events_config = []
    p.counters_config = []
    p.data_config = {}
    return p


def _make_channel_manager(sources):
    """sources: {"0": {...}, "1": {...}} → mock channel_manager."""
    cm = MagicMock()
    cm.channels = {int(k): MagicMock() for k in sources}
    cm.get_channel_sources.return_value = sources
    return cm


def test_开关关_无绑定通道不被收养():
    """ch0 无绑定 + ch1 绑本项目 → 开关关时只同步 ch1, ch0 不动."""
    from backend.api import projects as proj_mod
    project = _make_project(pid=2)
    sources = {
        "0": {"project_id": None},   # 绑定曾被抹掉的通道
        "1": {"project_id": 2},
    }
    cm = _make_channel_manager(sources)

    with patch("backend.api.channel_manager.channel_manager", cm), \
         patch.object(proj_mod, "_get_adopt_unbound", return_value=False), \
         patch.object(proj_mod, "_channels_bound_to_other", return_value=set()):
        proj_mod._sync_project_config_to_channels(project)

    cm.channels[0].set_project_config.assert_not_called()
    cm.channels[1].set_project_config.assert_called_once()
    # 不应写任何持久化绑定 (ch1 old_pid == project.id 也不写)
    cm.save_channel_source.assert_not_called()


def test_开关开_保持存量收养行为():
    """开关开 (默认) → 无绑定 ch0 照旧被收养同步 (零差异)."""
    from backend.api import projects as proj_mod
    project = _make_project(pid=2)
    sources = {
        "0": {"project_id": None},
        "1": {"project_id": 2},
    }
    cm = _make_channel_manager(sources)

    with patch("backend.api.channel_manager.channel_manager", cm), \
         patch.object(proj_mod, "_get_adopt_unbound", return_value=True), \
         patch.object(proj_mod, "_channels_bound_to_other", return_value=set()):
        proj_mod._sync_project_config_to_channels(project)

    cm.channels[0].set_project_config.assert_called_once()
    cm.channels[1].set_project_config.assert_called_once()


def test_开关关_绑定其它项目的通道照旧被跳过():
    """开关关 + ch0 绑其它项目 → ch0 双重豁免, 只动 ch1."""
    from backend.api import projects as proj_mod
    project = _make_project(pid=2)
    sources = {
        "0": {"project_id": 7},   # 绑大件
        "1": {"project_id": 2},
    }
    cm = _make_channel_manager(sources)

    with patch("backend.api.channel_manager.channel_manager", cm), \
         patch.object(proj_mod, "_get_adopt_unbound", return_value=False), \
         patch.object(proj_mod, "_channels_bound_to_other", return_value={0}):
        proj_mod._sync_project_config_to_channels(project)

    cm.channels[0].set_project_config.assert_not_called()
    cm.channels[1].set_project_config.assert_called_once()
