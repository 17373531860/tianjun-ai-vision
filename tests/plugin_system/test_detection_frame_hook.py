"""detection_frame hook (帧级检测广播) 契约测试.

客户视角叙事:
  sensor-clean (传感器清洁) 插件要"逐件复刻 detect6 计数精度": 主程序周期结算机制
  无法逐件对齐 detect6 的逐帧锚动作生命周期计数, 故由插件接管计数 —— 插件需要拿到
  每帧的原始检测框. 平台为此加 detection_frame 帧级 observe hook:
    - 主程序推理循环每帧 (非 tracking 路径) 把本帧检测框广播给插件;
    - ctx 携带 channel_id / frame_seq / timestamp / detections;
    - 只读 observe hook (不在 returnable 白名单), 返回值丢弃, 不改主程序状态机/计数/录像;
    - 无 active 插件时 O(1) 早退 (推理热路径零开销); handler 异常隔离不抛回热路径.

测试策略 (无法单测整个推理循环, 测 hook_dispatch 契约层 + 调用点 ctx 字段集合):
  - observe-only: 不在白名单 + 返回值整体丢弃
  - 无插件早退 / registry 层异常隔离
  - ctx 透传完整 + 调用点字段集合静态锁定 (改字段 = 升 plugin SDK)
"""
from __future__ import annotations

import re
import pathlib
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

HOOK = "detection_frame"


# ============================================================
# A. observe-only 语义 (不可改主程序状态机)
# ============================================================


def test_detection_frame_not_returnable():
    """detection_frame 必须是只读 observe hook, 不能进可写白名单."""
    from backend.plugin_system.hook_dispatch import RETURNABLE_HOOK_FIELDS

    assert HOOK not in RETURNABLE_HOOK_FIELDS, (
        "detection_frame 每帧 fire, 若进可写白名单会让插件改检测/计数/录像/状态机, "
        "违反 observe-only 契约"
    )


def test_detection_frame_return_value_discarded():
    """handler 即使返回 override 字段也整体丢弃."""
    from backend.plugin_system.hook_dispatch import _merge_handler_results

    merged = _merge_handler_results(HOOK, [
        {"override_result": "NG"},
        {"count": 44},
        {"suppress_alarm": True},
    ])
    assert merged == {}, "observe hook 的 handler 返回值必须整体丢弃"


# ============================================================
# B. 无插件早退 / 异常隔离 (推理热路径不能被插件拖垮)
# ============================================================


def test_fire_no_active_plugin_returns_empty():
    """没有 active 插件时返回空 dict, 不报错 (热路径零开销早退)."""
    from backend.plugin_system.hook_dispatch import fire_plugin_hook
    from backend.plugin_system import manager as mod

    with patch.object(mod.plugin_manager, "registry", None):
        ret = fire_plugin_hook(HOOK, "post_inference", "post", {
            "channel_id": 0, "frame_seq": 1, "timestamp": 0.0, "detections": [],
        })
        assert ret == {}


def test_fire_registry_layer_exception_isolated():
    """registry 层抛异常必须被隔离, 不抛回推理热路径."""
    from backend.plugin_system.hook_dispatch import fire_plugin_hook
    from backend.plugin_system import manager as mod

    fake = MagicMock()
    fake.hooks.fire.side_effect = RuntimeError("帧钩子爆了")
    with patch.object(mod.plugin_manager, "registry", fake):
        ret = fire_plugin_hook(HOOK, "post_inference", "post", {"detections": []})
        assert ret == {}, "registry 层异常必须隔离, 不抛回推理热路径"


def test_fire_handler_override_discarded_end_to_end():
    """有 registry + handler 返回 override 字段, 端到端仍整体丢弃 (observe 语义)."""
    from backend.plugin_system.hook_dispatch import fire_plugin_hook
    from backend.plugin_system import manager as mod

    fake = MagicMock()
    fake.hooks.fire.return_value = [{"override_result": "NG", "count": 44}]
    with patch.object(mod.plugin_manager, "registry", fake):
        ret = fire_plugin_hook(HOOK, "post_inference", "post", {"detections": []})
        assert ret == {}


# ============================================================
# C. ctx 透传 + 调用点字段集合契约
# ============================================================


def test_ctx_passthrough_complete():
    """fire_plugin_hook 把 hook 三元组 + ctx 原样透传给 registry.hooks.fire."""
    from backend.plugin_system.hook_dispatch import fire_plugin_hook
    from backend.plugin_system import manager as mod

    captured = {}

    def _capture(hook_type, phase, when, ctx):
        captured.update({"hook_type": hook_type, "phase": phase, "when": when, "ctx": ctx})
        return []

    fake = MagicMock()
    fake.hooks.fire.side_effect = _capture
    ctx = {
        "channel_id": 2, "frame_seq": 100, "timestamp": 123.4,
        "detections": [{"label": "擦拭产品", "confidence": 0.9, "bbox": [0.1, 0.1, 0.2, 0.2]}],
    }
    with patch.object(mod.plugin_manager, "registry", fake):
        fire_plugin_hook(HOOK, "post_inference", "post", ctx)

    assert captured["hook_type"] == HOOK
    assert captured["phase"] == "post_inference"
    assert captured["when"] == "post"
    assert captured["ctx"] is ctx


def test_call_site_ctx_field_set_locked():
    """推理循环调用点 ctx 字段集合契约 (source_inference_loop_mixin.py).

    改这个集合 = 改 detection_frame SDK, 必须同步 sensor-clean 插件 + changelog.
    用源码静态断言锁定字段 (无法不起推理循环拿运行时 ctx).
    """
    src = pathlib.Path("backend/api/source_inference_loop_mixin.py").read_text(encoding="utf-8")
    m = re.search(r'fire_plugin_hook\(\s*"detection_frame".*?\{(.*?)\}\s*\)', src, re.S)
    assert m, "找不到 detection_frame 调用点 (重构了? 同步本测试)"
    keys = set(re.findall(r'"(\w+)"\s*:', m.group(1)))
    expected = {"channel_id", "frame_seq", "timestamp", "detections"}
    assert keys == expected, (
        f"detection_frame 调用点 ctx 字段集合变了: {keys} != {expected}. "
        f"改字段 = 升 plugin SDK, 必须同步 sensor-clean 插件 hooks.on_detection_frame"
    )
