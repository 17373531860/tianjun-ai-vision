# -*- coding: utf-8 -*-
"""手部裁切副屏按工位独立开关（吉田特例，默认关）。

现场叙事：
    吉田那条线上，某个工位的一体机要再挂一块只读副屏，只显示手部检测框 + 指关节
    的裁切画面（不要整幅工位图）。手部识别是每帧的算力，所以必须逐工位开：
    别的客户、别的工位一帧都不该算，也不该因此把骨架糊到工位主画面上。

不变量：
    所有开关全关时，MediaPipe 行为与 v3.56 逐字节一致。
"""

from __future__ import annotations

import numpy as np
import pytest


class _FakeHost:
    """只带 MediaPipe 相关字段的假 host（不碰真 VideoSourceManager 的重初始化）。"""

    def __init__(self, **kwargs):
        self.channel_id = kwargs.pop("channel_id", 0)
        self.mediapipe_enabled = kwargs.pop("mediapipe_enabled", False)
        self.mediapipe_pose = kwargs.pop("mediapipe_pose", True)
        self.mediapipe_hands = kwargs.pop("mediapipe_hands", True)
        self.mediapipe_confidence = 0.7
        self.hands_aux_enabled = kwargs.pop("hands_aux_enabled", False)
        for k, v in kwargs.items():
            setattr(self, k, v)


def _overlay(**host_kwargs):
    from backend.api.source_mediapipe import MediaPipeOverlay
    return MediaPipeOverlay(host=_FakeHost(**host_kwargs))


# ============================================================
# 1. 启用判据：两个开关取"或"，全关时零差异
# ============================================================

@pytest.mark.parametrize("enabled,aux,expected", [
    (False, False, False),   # 出厂默认: 这一路一帧都不算
    (True, False, True),     # 老客户全局开着: 照旧
    (False, True, True),     # 吉田: 只靠本工位副屏开关把 MediaPipe 拉起来
    (True, True, True),
])
def test_mediapipe_active_is_or_of_global_and_aux(enabled, aux, expected):
    ov = _overlay(mediapipe_enabled=enabled, hands_aux_enabled=aux)
    assert ov._mediapipe_active() is expected


@pytest.mark.parametrize("enabled,hands,aux,expected", [
    (True, True, False, True),      # 老客户全局开手部: 照旧算
    (True, False, False, False),    # 老客户只开 pose: 不算手
    (False, True, True, True),      # 吉田: 副屏把手部算起来
    (False, False, True, True),     # 副屏开关不受全局 hands 子开关影响
])
def test_hands_compute_follows_either_switch(enabled, hands, aux, expected):
    """已在跑 MediaPipe 的前提下是否算手 —— 内部调用点已被 active 守过门。"""
    ov = _overlay(mediapipe_enabled=enabled, mediapipe_hands=hands,
                  hands_aux_enabled=aux)
    assert ov._hands_compute_wanted() is expected


@pytest.mark.parametrize("enabled,pose,aux,expected", [
    (False, True, True, False),     # 仅副屏拉起: 只要手, 不为它白算 pose
    (True, True, True, True),       # 全局也开着: pose 照旧
    (True, True, False, True),
    (True, False, False, False),
])
def test_pose_not_computed_when_only_aux_pulled_mediapipe_up(enabled, pose, aux, expected):
    ov = _overlay(mediapipe_enabled=enabled, mediapipe_pose=pose,
                  hands_aux_enabled=aux)
    assert ov._pose_compute_wanted() is expected


@pytest.mark.parametrize("enabled,hands,aux,expected", [
    (False, True, False, False),    # 出厂默认: HTTP 取 hands 快照拿不到几何
    (False, False, False, False),
    (True, True, False, True),
    (True, False, False, False),
    (False, True, True, True),
    (False, False, True, True),
])
def test_hands_geometry_available_is_the_http_gate(enabled, hands, aux, expected):
    """HTTP 端点没有前置守门，必须自己判全套（active AND 要算手）。"""
    ov = _overlay(mediapipe_enabled=enabled, mediapipe_hands=hands,
                  hands_aux_enabled=aux)
    assert ov._hands_geometry_available() is expected


# ============================================================
# 2. 副屏开关不能改工位主画面
# ============================================================

def test_apply_overlay_is_noop_when_everything_off():
    """出厂默认: 一次投帧、一次绘制都不该发生。"""
    ov = _overlay()
    frame = np.zeros((8, 8, 3), dtype=np.uint8)
    ov._ensure_worker = lambda: pytest.fail("全关时不该起 MediaPipe 后台线程")

    assert ov.apply_overlay(frame) is frame


def test_aux_only_computes_geometry_without_drawing_on_main_frame(monkeypatch):
    """副屏开着、全局 MediaPipe 关着 → 投帧算几何，但主画面一笔不画。"""
    ov = _overlay(hands_aux_enabled=True)
    frame = np.zeros((8, 8, 3), dtype=np.uint8)
    started = []
    ov._ensure_worker = lambda: started.append(True)
    # _mp_draw 非空代表 init 已完成; 若走到绘制分支就会用到它
    ov._mp_draw = object()
    ov._draw_baseline_hands = lambda f: pytest.fail("副屏开关不该把手部骨架画到工位主画面")
    ov._draw_two_stage_hands = lambda f: pytest.fail("副屏开关不该把手部骨架画到工位主画面")

    out = ov.apply_overlay(frame)

    assert started, "副屏需要几何, 必须把帧投给后台推理"
    assert out is frame
    # 帧内容没被动过
    assert not out.any()


def test_global_mediapipe_still_draws_on_main_frame():
    """老客户全局开着 MediaPipe 时, 主画面照旧画骨架（行为不变）。"""
    ov = _overlay(mediapipe_enabled=True)
    frame = np.zeros((8, 8, 3), dtype=np.uint8)
    ov._ensure_worker = lambda: None
    ov._mp_draw = object()
    drawn = []
    ov._draw_baseline_hands = lambda f: drawn.append("baseline")
    ov._draw_two_stage_hands = lambda f: drawn.append("two_stage")

    ov.apply_overlay(frame)

    assert drawn, "全局开关开着时必须照旧绘制"


# ============================================================
# 3. 未启用的工位取 hands 快照 → 占位图，不得顺手拉起 MediaPipe
# ============================================================

def test_hands_snapshot_returns_placeholder_when_channel_not_enabled():
    ov = _overlay()
    data = ov.get_hands_crop_snapshot()
    assert data, "应返回占位 JPEG 而不是 None"
    assert data.startswith(b"\xff\xd8"), "占位图必须是合法 JPEG"


def test_hands_snapshot_does_not_enable_mediapipe_as_side_effect():
    """一个 GET 不该把每帧的算力打开（否则谁扫一眼接口就全线掉帧）。"""
    ov = _overlay()
    ov._ensure_worker = lambda: pytest.fail("取快照不该起 MediaPipe 后台线程")
    ov.get_hands_crop_snapshot()
    assert ov._host.mediapipe_enabled is False
    assert ov._host.hands_aux_enabled is False


# ============================================================
# 4. 配置持久化与运行态回灌
# ============================================================

@pytest.fixture
def isolated_manager(monkeypatch, tmp_path):
    import backend.api.channel_manager as module

    config_path = tmp_path / "workstation_config.json"
    monkeypatch.setattr(module, "_CONFIG_FILE", str(config_path))
    manager = object.__new__(module.ChannelManager)
    manager.channel_count = 2
    manager.channels = {}
    return module, manager, config_path


def test_hands_aux_defaults_to_off_for_every_channel(isolated_manager):
    _module, manager, _path = isolated_manager
    assert manager.get_hands_aux_config() == {"channels": {"0": False, "1": False}}


def test_set_hands_aux_persists_only_that_channel(isolated_manager):
    import json

    _module, manager, config_path = isolated_manager
    config_path.write_text(json.dumps({
        "channel_count": 2,
        "channels": {"0": {"source_type": "camera", "device_index": "usb_0"}},
        "multi_monitor": {"enabled": True, "readonly": True, "mapping": {}},
    }), encoding="utf-8")

    result = manager.set_hands_aux_enabled(0, True)

    assert result == {"channels": {"0": True, "1": False}}
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    # 同工位别的字段不能被抹
    assert saved["channels"]["0"]["source_type"] == "camera"
    assert saved["channels"]["0"]["hands_aux_enabled"] is True
    # 别的顶层段不能被抹 (不变量 17)
    assert saved["multi_monitor"]["enabled"] is True
    assert saved["channel_count"] == 2


def test_set_hands_aux_applies_to_live_channel(isolated_manager):
    _module, manager, _path = isolated_manager
    released = []

    class _Mgr:
        def __init__(self):
            self.hands_aux_enabled = False
            self.mediapipe_enabled = False

        def _release_mediapipe(self):
            released.append(True)

    mgr0, mgr1 = _Mgr(), _Mgr()
    manager.channels = {0: mgr0, 1: mgr1}

    manager.set_hands_aux_enabled(0, True)
    assert mgr0.hands_aux_enabled is True
    assert mgr1.hands_aux_enabled is False, "不能牵连别的工位"
    assert not released

    manager.set_hands_aux_enabled(0, False)
    assert mgr0.hands_aux_enabled is False
    assert released, "关掉且没开全局 MediaPipe 时要释放该工位的模型"


def test_disabling_does_not_release_when_global_mediapipe_in_use(isolated_manager):
    """全局 MediaPipe 是检测逻辑在用（如电机装配线），不能被副屏开关连坐释放。"""
    _module, manager, _path = isolated_manager
    released = []

    class _Mgr:
        def __init__(self):
            self.hands_aux_enabled = True
            self.mediapipe_enabled = True

        def _release_mediapipe(self):
            released.append(True)

    manager.channels = {0: _Mgr()}
    manager.set_hands_aux_enabled(0, False)
    assert not released


def test_apply_hands_aux_reinstates_flags_after_channel_count_change(isolated_manager):
    import json

    _module, manager, config_path = isolated_manager
    config_path.write_text(json.dumps({
        "channel_count": 2,
        "channels": {"1": {"hands_aux_enabled": True}},
    }), encoding="utf-8")

    class _Mgr:
        hands_aux_enabled = False

    mgr0, mgr1 = _Mgr(), _Mgr()
    manager.channels = {0: mgr0, 1: mgr1}

    assert manager.apply_hands_aux_to_channels() == {"0": False, "1": True}
    assert mgr1.hands_aux_enabled is True
    assert mgr0.hands_aux_enabled is False


# ============================================================
# 5. 端点契约
# ============================================================

def test_hands_aux_endpoints_roundtrip(client):
    resp = client.get("/api/v1/workstations/hands-aux")
    assert resp.status_code == 200
    original = resp.json()["channels"]
    assert original, "至少要报 0 号工位"
    assert all(isinstance(v, bool) for v in original.values())

    try:
        put = client.put("/api/v1/workstations/hands-aux",
                         json={"channel_id": 0, "enabled": True})
        assert put.status_code == 200
        assert put.json()["channels"]["0"] is True
        assert client.get("/api/v1/workstations/hands-aux").json()["channels"]["0"] is True
    finally:
        client.put("/api/v1/workstations/hands-aux",
                   json={"channel_id": 0, "enabled": original.get("0", False)})


def test_hands_aux_put_rejects_negative_channel(client):
    resp = client.put("/api/v1/workstations/hands-aux",
                      json={"channel_id": -1, "enabled": True})
    assert resp.status_code == 422
