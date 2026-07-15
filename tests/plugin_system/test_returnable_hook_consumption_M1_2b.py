"""M1.2b 业务侧消费 returnable hook 返回值

客户视角叙事:
  M1.2a 让 fire_plugin_hook 能返回 dict, 但主程序业务侧没消费 → 等于白做.
  ACME 客户需求 "插件能强制改判 OK→NG (override_result)" / "插件能给步骤打 warn 标签
  (warn_threshold_violated)" 这两条, 必须等 end_cycle / record_step 真消费这些返回值
  才算落地.

  改进前 (M1.2a, v3.13 中间状态):
    fire_plugin_hook 返回 dict 但 end_cycle 直接丢弃 — 插件 override_result="NG"
    返回了, cycle 还是按主程序判定写库, 客户检查 DB 发现"插件白挂".

  改进后 (M1.2b, 本测试守护):
    - end_cycle 调 _resolve_pre_cycle_end_overrides 算出 final_is_good / final_reason
      / plugin_extra_counters; 写库 + MES + 周期性 + cycle_end hook ctx 全部走 final_*
    - record_step 调 _resolve_step_change_warn 解出 warn_violated / warn_label,
      落到 self._plugin_step_warn_cache (按 step_record_id 字典)
    - start_cycle 清 _plugin_step_warn_cache 避免跨周期串污染

测试策略:
  把消费逻辑抽到纯函数 (_resolve_pre_cycle_end_overrides /
  _resolve_step_change_warn), 测试直接调它们验证消费契约;
  end_cycle / record_step 整体行为由"静态扫描调用了这两个纯函数 + 用 final_* 写库"
  双重护栏.

如果有人:
  - 删 _resolve_* 纯函数 → import 失败 FAIL
  - end_cycle 改回直接用 is_good 入参 → 静态扫描 FAIL
  - 改纯函数语义 (例如 override 不再覆盖) → 单元测试 FAIL
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


REPO_ROOT = Path(__file__).resolve().parents[2]
LIFECYCLE = REPO_ROOT / "backend" / "api" / "source_session_lifecycle_mixin.py"


# ============================================================
# A. _resolve_pre_cycle_end_overrides 纯函数契约
# ============================================================


def _resolve_pre():
    from backend.api.source_session_lifecycle_mixin import _resolve_pre_cycle_end_overrides
    return _resolve_pre_cycle_end_overrides


def test_pre_cycle_end_no_plugin_result_returns_original():
    """plugin_result 是空 dict (没 active 插件 / 没声明) → 原值."""
    fn = _resolve_pre()
    fig, fr, ec = fn(True, "ok-reason", {})
    assert fig is True
    assert fr == "ok-reason"
    assert ec is None


def test_pre_cycle_end_non_dict_plugin_result_returns_original():
    """plugin_result 不是 dict (异常路径 / 旧 hook 返回 None) → 原值."""
    fn = _resolve_pre()
    fig, fr, ec = fn(False, "ng-reason", None)
    assert fig is False
    assert fr == "ng-reason"
    assert ec is None
    fig, fr, ec = fn(True, None, "not-a-dict")
    assert fig is True
    assert fr is None


def test_pre_cycle_end_override_ok_to_ng():
    """override_result="NG" → 把 OK 改成 NG, reason 追加 override 痕迹."""
    fn = _resolve_pre()
    fig, fr, ec = fn(True, "原本 OK", {"override_result": "NG"})
    assert fig is False
    assert "[plugin override: OK → NG]" in fr
    assert "原本 OK" in fr
    assert ec is None


def test_pre_cycle_end_override_ng_to_ok():
    """override_result="OK" 把 NG 改成 OK."""
    fn = _resolve_pre()
    fig, fr, ec = fn(False, "原本 NG", {"override_result": "OK"})
    assert fig is True
    assert "[plugin override: NG → OK]" in fr
    assert "原本 NG" in fr


def test_pre_cycle_end_override_with_none_reason():
    """原 reason 是 None → final_reason 只是 tag, 不带 'None'."""
    fn = _resolve_pre()
    fig, fr, ec = fn(True, None, {"override_result": "NG"})
    assert fig is False
    assert fr == "[plugin override: OK → NG]"


def test_pre_cycle_end_override_same_value_no_changes():
    """override_result 与原判定一致 (OK→OK / NG→NG) → 不改 reason, 不留 tag.

    避免无意义的"override 痕迹"污染客户的 result_reason.
    """
    fn = _resolve_pre()
    fig, fr, ec = fn(True, "原本 OK", {"override_result": "OK"})
    assert fig is True
    assert fr == "原本 OK"
    assert "override" not in (fr or "")


def test_pre_cycle_end_override_invalid_value_ignored():
    """override_result 不是 "OK"/"NG" → 忽略 (大小写敏感, 防误用)."""
    fn = _resolve_pre()
    for invalid in ["ok", "ng", "Ok", "", "TRUE", True, 1, None]:
        fig, fr, ec = fn(True, "r", {"override_result": invalid})
        assert fig is True, f"override_result={invalid!r} 不该被识别"
        assert fr == "r"


def test_pre_cycle_end_extra_counters_dict_passed_through():
    """extra_counters 是非空 dict → 返回."""
    fn = _resolve_pre()
    counters = {"plugin_acme_ng_count": 3, "plugin_acme_warn_count": 1}
    fig, fr, ec = fn(True, "r", {"extra_counters": counters})
    assert ec == counters


def test_pre_cycle_end_extra_counters_non_dict_ignored():
    """extra_counters 不是 dict / 空 dict → None."""
    fn = _resolve_pre()
    for invalid in [None, [], "x", 0, {}]:
        _, _, ec = fn(True, "r", {"extra_counters": invalid})
        assert ec is None, f"extra_counters={invalid!r} 不该被识别为有效"


def test_pre_cycle_end_override_and_extra_counters_combine():
    """同时给 override + extra_counters → 两者都生效."""
    fn = _resolve_pre()
    fig, fr, ec = fn(True, "原本 OK", {
        "override_result": "NG",
        "extra_counters": {"k": "v"},
    })
    assert fig is False
    assert "override" in fr
    assert ec == {"k": "v"}


# ============================================================
# B. _resolve_step_change_warn 纯函数契约
# ============================================================


def _resolve_step():
    from backend.api.source_session_lifecycle_mixin import _resolve_step_change_warn
    return _resolve_step_change_warn


def test_step_change_no_plugin_result():
    """plugin_result 空 dict → (False, "")."""
    fn = _resolve_step()
    assert fn({}) == (False, "")


def test_step_change_non_dict():
    """plugin_result 不是 dict → (False, "")."""
    fn = _resolve_step()
    assert fn(None) == (False, "")
    assert fn("xxx") == (False, "")
    assert fn(42) == (False, "")


def test_step_change_warn_violated_with_label():
    """warn_threshold_violated=True + warn_label → (True, label)."""
    fn = _resolve_step()
    assert fn({"warn_threshold_violated": True, "warn_label": "yellow"}) == (True, "yellow")


def test_step_change_warn_violated_no_label():
    """warn_threshold_violated=True 但 warn_label 缺 → (True, "")."""
    fn = _resolve_step()
    assert fn({"warn_threshold_violated": True}) == (True, "")


def test_step_change_warn_label_without_violation_ignored():
    """warn_label 有值但 warn_threshold_violated=False → (False, "").

    避免插件作者只设 label 不设 flag 时误以为生效.
    """
    fn = _resolve_step()
    assert fn({"warn_label": "yellow"}) == (False, "")
    assert fn({"warn_threshold_violated": False, "warn_label": "yellow"}) == (False, "")


def test_step_change_warn_label_coerced_to_string():
    """warn_label 非 str → 强转 str."""
    fn = _resolve_step()
    assert fn({"warn_threshold_violated": True, "warn_label": 123}) == (True, "123")


def test_step_change_warn_label_none_becomes_empty():
    """warn_label=None → ""."""
    fn = _resolve_step()
    assert fn({"warn_threshold_violated": True, "warn_label": None}) == (True, "")


# ============================================================
# C. end_cycle 静态扫描 — 必须用 final_* 而不是 is_good 入参
# ============================================================


def _end_cycle_body() -> str:
    src = LIFECYCLE.read_text(encoding="utf-8")
    m = re.search(
        r'def end_cycle\(self, is_good: bool.*?\n(.+?)(?=\n    def )',
        src,
        re.DOTALL,
    )
    assert m is not None, "end_cycle 方法消失"
    return m.group(1)


def test_end_cycle_calls_resolve_pre_cycle_end_overrides():
    """end_cycle 必须调 _resolve_pre_cycle_end_overrides (M1.2b 消费的核心)."""
    body = _end_cycle_body()
    assert "_resolve_pre_cycle_end_overrides(" in body, (
        "end_cycle 没调 _resolve_pre_cycle_end_overrides — M1.2b 消费契约被破"
    )


def test_end_cycle_writes_final_is_good_to_db():
    """cycle.is_good 必须赋 final_is_good 而不是 is_good 入参."""
    body = _end_cycle_body()
    # 必须有 cycle.is_good = final_is_good
    assert re.search(r'cycle\.is_good\s*=\s*final_is_good', body), (
        "cycle.is_good 没用 final_is_good — M1.2b override 不生效"
    )
    # 不能有 cycle.is_good = is_good (老代码残留)
    # 但允许 "= is_good_xxx" 之类作为 final_is_good 的别名
    bad = re.findall(r'cycle\.is_good\s*=\s*is_good\b', body)
    assert not bad, f"cycle.is_good 仍然直接用 is_good 入参: {bad}"


def test_end_cycle_writes_final_reason_to_db():
    """cycle.result_reason 必须赋 final_reason."""
    body = _end_cycle_body()
    assert re.search(r'cycle\.result_reason\s*=\s*final_reason', body), (
        "cycle.result_reason 没用 final_reason — M1.2b override 痕迹丢失"
    )


def test_end_cycle_mes_hook_uses_final_is_good():
    """MES on_cycle_end 必须传 final_is_good 而不是 is_good 入参."""
    body = _end_cycle_body()
    # 在 on_cycle_end(...) 调用块内找 is_good= 参数
    m = re.search(r'on_cycle_end\(([^)]+)\)', body, re.DOTALL)
    assert m, "MES on_cycle_end 调用消失"
    call_args = m.group(1)
    assert "is_good=final_is_good" in call_args, (
        f"MES on_cycle_end 没用 final_is_good — 客户 MES 拿到的判定与 DB 不一致! 实际: {call_args!r}"
    )
    assert "result_reason=final_reason" in call_args, (
        "MES on_cycle_end 没用 final_reason"
    )


def test_end_cycle_periodic_actions_uses_final_is_good():
    """周期性强制动作 _check_periodic_actions 必须用 final_is_good."""
    body = _end_cycle_body()
    m = re.search(r'_check_periodic_actions\(\s*(.+?)\)', body, re.DOTALL)
    assert m, "_check_periodic_actions 调用消失"
    args = m.group(1)
    assert "final_is_good" in args, (
        f"_check_periodic_actions 没用 final_is_good — 周期性强制动作判定错误! 实际: {args!r}"
    )


def test_end_cycle_post_cycle_end_hook_ctx_uses_final_values():
    """cycle_end/post_cycle/post hook ctx 必须传 final_* — 让 post 阶段插件看到 override 后的结果."""
    body = _end_cycle_body()
    # 找 plugin_ctx = {...} 块
    m = re.search(r'plugin_ctx\s*=\s*\{(.+?)\n\s*\}', body, re.DOTALL)
    assert m, "cycle_end hook plugin_ctx 字面量找不到"
    ctx_block = m.group(1)
    # is_good 项必须用 final_is_good
    assert re.search(r'"is_good":\s*bool\(final_is_good\)', ctx_block), (
        "cycle_end hook ctx.is_good 没用 final_is_good"
    )
    assert re.search(r'"result":\s*"OK"\s*if\s*final_is_good\s*else\s*"NG"', ctx_block), (
        "cycle_end hook ctx.result 没用 final_is_good"
    )
    assert re.search(r'"reason":\s*final_reason', ctx_block), (
        "cycle_end hook ctx.reason 没用 final_reason"
    )


# ============================================================
# D. record_step 静态扫描 — 必须用 _resolve_step_change_warn
# ============================================================


def _record_step_body() -> str:
    src = LIFECYCLE.read_text(encoding="utf-8")
    m = re.search(
        r'def record_step\(self.*?\n(.+?)(?=\n    def )',
        src,
        re.DOTALL,
    )
    assert m, "record_step 方法消失"
    return m.group(1)


def test_record_step_calls_resolve_step_change_warn():
    """record_step 必须调 _resolve_step_change_warn 消费 step_change returnable."""
    body = _record_step_body()
    assert "_resolve_step_change_warn(" in body, (
        "record_step 没调 _resolve_step_change_warn — step_change warn 消费契约被破"
    )


def test_record_step_writes_warn_to_cache():
    """warn_violated → 必须写 VSM 的 _plugin_step_warn_cache[<record id>].

    v3.38 RFC 后写库在落库作业闭包里跑, self 以 vsm 快照进入闭包、record.id
    以 record_id 快照进入 — 正则同时接受新旧两种等价写法。
    """
    body = _record_step_body()
    assert re.search(
        r'(self|vsm)\._plugin_step_warn_cache\b',
        body,
    ), "record_step 没写 _plugin_step_warn_cache"
    assert re.search(
        r'cache\[record(\.id|_id)\]\s*=',
        body,
    ), "record_step 没按 record id 索引 warn cache"


# ============================================================
# E. start_cycle 静态扫描 — 必须清 warn cache
# ============================================================


def test_start_cycle_clears_warn_cache():
    """start_cycle 必须清 _plugin_step_warn_cache 防止跨周期串污染."""
    src = LIFECYCLE.read_text(encoding="utf-8")
    m = re.search(
        r'def start_cycle\(self\):(.+?)(?=\n    def )',
        src,
        re.DOTALL,
    )
    assert m, "start_cycle 方法消失"
    body = m.group(1)
    # 必须有 self._plugin_step_warn_cache = {} 或类似清空操作
    assert re.search(
        r'self\._plugin_step_warn_cache\s*=\s*\{\}',
        body,
    ), "start_cycle 没清 _plugin_step_warn_cache — 跨周期 warn 会串污染"


# ============================================================
# F. 函数签名稳定性 (防止改签名破坏调用方)
# ============================================================


def test_resolve_pre_signature_locked():
    """_resolve_pre_cycle_end_overrides 签名锁定."""
    import inspect
    fn = _resolve_pre()
    sig = inspect.signature(fn)
    params = list(sig.parameters.keys())
    assert params == ["original_is_good", "original_reason", "plugin_result"], (
        f"_resolve_pre_cycle_end_overrides 签名变了: {params}"
    )


def test_resolve_step_signature_locked():
    """_resolve_step_change_warn 签名锁定."""
    import inspect
    fn = _resolve_step()
    sig = inspect.signature(fn)
    params = list(sig.parameters.keys())
    assert params == ["plugin_result"], (
        f"_resolve_step_change_warn 签名变了: {params}"
    )


# ============================================================
# G. 端到端: 通过 fire_plugin_hook 走真注册 handler → 消费一致
# ============================================================


class _FakePluginRegistry:
    """模仿 PluginRegistry 仅暴露 .hooks 即可让 fire_plugin_hook 工作.

    真 PluginRegistry 还有 routes/tables/export_* 等子 registry, 但 fire_plugin_hook
    只读 registry.hooks, 不需要全套.
    """
    def __init__(self, hooks_registry):
        self.hooks = hooks_registry


def _install_fake_registry(monkeypatch, hooks_registry):
    """把 fake registry 装到 plugin_manager.registry 让 fire_plugin_hook 走真 reg."""
    from backend.plugin_system import manager as mgr_mod

    class _FakePM:
        registry = _FakePluginRegistry(hooks_registry)
    monkeypatch.setattr(mgr_mod, "plugin_manager", _FakePM())


def test_e2e_fire_plugin_hook_then_resolve_override_ok_to_ng(monkeypatch):
    """注册 handler 返回 override_result="NG" → fire 拿到 → resolve 改判."""
    from backend.plugin_system.registry import HooksRegistry
    from backend.plugin_system import hook_dispatch as hd

    reg = HooksRegistry(customer_code="acme-e2e")
    reg.register(
        "pre_cycle_end", "pre_cycle", "pre",
        priority=100,
        handler=lambda ctx: {"override_result": "NG"},
    )
    _install_fake_registry(monkeypatch, reg)

    plugin_result = hd.fire_plugin_hook("pre_cycle_end", "pre_cycle", "pre", {
        "channel_id": 0,
        "cycle_id": 1,
        "is_good": True,
    })
    assert plugin_result == {"override_result": "NG"}

    fn = _resolve_pre()
    fig, fr, ec = fn(True, "原本 OK", plugin_result)
    assert fig is False
    assert "[plugin override: OK → NG]" in fr


def test_e2e_two_handlers_priority_override_wins(monkeypatch):
    """两个 handler, priority 大的覆盖小的, resolve 看最终聚合值."""
    from backend.plugin_system.registry import HooksRegistry
    from backend.plugin_system import hook_dispatch as hd

    reg = HooksRegistry(customer_code="acme-prio")
    reg.register(
        "pre_cycle_end", "pre_cycle", "pre",
        priority=10,
        handler=lambda ctx: {"override_result": "NG"},
    )
    reg.register(
        "pre_cycle_end", "pre_cycle", "pre",
        priority=100,  # 大覆盖小
        handler=lambda ctx: {"override_result": "OK"},
    )
    _install_fake_registry(monkeypatch, reg)

    result = hd.fire_plugin_hook("pre_cycle_end", "pre_cycle", "pre", {})
    assert result == {"override_result": "OK"}

    fn = _resolve_pre()
    fig, _, _ = fn(False, "原本 NG", result)
    assert fig is True


def test_e2e_step_change_warn_through_fire_plugin_hook(monkeypatch):
    """step_change handler 返回 warn → fire 拿到 → resolve 解析."""
    from backend.plugin_system.registry import HooksRegistry
    from backend.plugin_system import hook_dispatch as hd

    reg = HooksRegistry(customer_code="acme-warn")
    reg.register(
        "step_change", "post_step", "post",
        priority=100,
        handler=lambda ctx: {
            "warn_threshold_violated": True,
            "warn_label": "yellow",
        },
    )
    _install_fake_registry(monkeypatch, reg)

    result = hd.fire_plugin_hook("step_change", "post_step", "post", {
        "channel_id": 0,
        "step_record_id": 99,
        "duration": 5.5,
    })
    assert result == {"warn_threshold_violated": True, "warn_label": "yellow"}

    fn = _resolve_step()
    warn, label = fn(result)
    assert warn is True
    assert label == "yellow"
