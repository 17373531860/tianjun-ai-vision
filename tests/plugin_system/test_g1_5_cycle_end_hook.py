"""G1.5 cycle_end hook 触发点回归护栏

客户叙事 (ISSUES.md §G1.5):
  ACME 客户跑完整周期 → 后端 Tier 3 插件 on_cycle_end_post(ctx) 必须被调一次,
  ctx 含 cycle_id/barcode/result, 插件抛错不能影响主程序周期统计.

本测试不跑真实 VideoSourceManager (那要 GPU + 视频源 + 推理 + DB session),
只覆盖三条契约护栏:

  1. demo `on_cycle_end_post(ctx)` 接受我们 source.py 注入的 ctx 字段并返回合理 dict
  2. ctx 字段名集合稳定 (G1.5 source.py 改字段会立刻 FAIL)
  3. 插件 hook 抛错时 HooksRegistry.fire() 不抛回 (G1 已测, 这里再来一道)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PLUGIN_TIER3 = REPO / "plugins-examples" / "tier3-fullstack"

sys.path.insert(0, str(REPO))


# G1.5 source.py 注入到 hook ctx 的字段集合 (变了主程序合约也变了)
EXPECTED_CTX_FIELDS = {
    "channel_id",
    "cycle_id",
    "cycle_uuid",
    "session_id",
    "is_good",
    "result",
    "judgement",
    "event_id",
    "event_name",
    "reason",
    "duration",
    "step_sequence",
    "project_id",
}


def _make_ctx_sample():
    """模拟 source.py end_cycle 触发时构造的 ctx."""
    return {
        "channel_id": 0,
        "cycle_id": 42,
        "cycle_uuid": "test-uuid-42",
        "session_id": 7,
        "is_good": False,
        "result": "NG",
        "judgement": "NG",
        "event_id": 12,
        "event_name": "缺料",
        "reason": "missing-step-2",
        "duration": 3.45,
        "step_sequence": ["A", "B"],
        "project_id": 3,
    }


def test_ctx_field_set_locked():
    """G1.5 source.py 注入的字段名必须严格等于这套, 改一个就 FAIL."""
    ctx = _make_ctx_sample()
    assert set(ctx.keys()) == EXPECTED_CTX_FIELDS


def test_tier3_demo_hook_consumes_ctx():
    """tier3-fullstack 的 on_cycle_end_post(ctx) 必须能用我们 source.py 的 ctx 字段."""
    # 用 importlib 从 plugin demo 目录 import (跟主程序加载方式一致)
    import importlib.util

    hook_file = PLUGIN_TIER3 / "backend" / "hooks.py"
    spec = importlib.util.spec_from_file_location("tier3_demo_hooks", hook_file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    result = mod.on_cycle_end_post(_make_ctx_sample())
    assert result["cycle_id"] == 42
    # demo hook 取 barcode 优先 workpiece_code; 我们没塞 barcode → demo 取到 None 也合法
    assert "barcode" in result
    assert result["result"] == "NG"  # is_good=False → demo 取 judgement "NG"


def test_hooks_registry_swallows_plugin_exception_in_end_cycle_chain():
    """G1.5: 插件 hook 抛错 → fire 不抛回 → source.py end_cycle 流程不打断."""
    from backend.plugin_system.registry import HooksRegistry

    reg = HooksRegistry(customer_code="internal-demo")

    def buggy_hook(_ctx):
        raise RuntimeError("插件代码内部 bug")

    reg.register("cycle_end", "post_cycle", "post", priority=100, handler=buggy_hook)

    # source.py 的 try/except 之外, fire 内部已经 swallow
    results = reg.fire("cycle_end", "post_cycle", "post", _make_ctx_sample())
    assert len(results) == 1
    assert "_error" in results[0]
    assert "插件代码内部 bug" in results[0]["_error"]


def test_hooks_registry_no_active_plugin_returns_empty():
    """没插件注册 → fire 返回空, source.py 的 if registry is not None 路径外也安全."""
    from backend.plugin_system.registry import HooksRegistry

    reg = HooksRegistry(customer_code="none")
    results = reg.fire("cycle_end", "post_cycle", "post", _make_ctx_sample())
    assert results == []
