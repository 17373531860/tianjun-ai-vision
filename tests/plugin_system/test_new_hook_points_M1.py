"""M1.1 护栏: v3.13 RFC 09 M1.1 新增 5 个 hook 接入点 ctx 字段契约

客户视角叙事:
  这次客户提了 4 个需求, 其中 3 个 (步骤耗时三档颜色 / 隐藏步骤级 NG / 双工位左右半屏)
  需要插件能拿到主程序的 step 级事件 / event 级事件 / scan 级事件 / project 激活事件.
  G1 (v3.12) 阶段只有 4 个 hook 接入点 (cycle_end / pre_cycle_end / session_end /
  box_complete), 全部是 cycle 级 / box 级粗粒度, 步骤 / 事件 / 扫码维度的 hook 完全缺位.

  改进前 (v3.7.0~v3.12.0):
    全代码库只有 4 个 fire_plugin_hook 调用点 (3 处 lifecycle + 1 处 cluster).
    step_change / event_fire / cycle_start / scan_received / project_activated 都没接.

  改进后 (v3.13 M1.1, 本测试守护):
    - source_session_lifecycle_mixin.start_cycle() cycle 落库 + MES 通知后 fire cycle_start
    - source_session_lifecycle_mixin.record_step() step_record 落库 + 本地缓存后 fire step_change
    - source_event_trigger_mixin._trigger_event() end_cycle + 副作用全完成后 fire event_fire
    - services/scanner.py ScannerService._on_data_received() 主路径完成后 fire scan_received
    - api/projects.py activate_project() 项目激活 + 模型重载 + MES 清理后 fire project_activated
    全部走统一 backend.plugin_system.hook_dispatch.fire_plugin_hook()

  注: RFC 09 原列 6 个 hook, source_status_change 因主程序没有统一 status setter
      (是 GET endpoint 拼出来的视图) 暂缓, 等真客户场景再做侵入式重构.

如果有人删 hook 接入点 / 改 ctx 字段名, 这条测试会立刻 FAIL.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


REPO_ROOT = Path(__file__).resolve().parents[2]


# ----------------------- ctx 字段集合契约 -----------------------


CYCLE_START_FIELDS = {
    "channel_id",
    "cycle_id",
    "cycle_uuid",
    "session_id",
    "cycle_number",
    "project_id",
    "start_time",
}

STEP_CHANGE_FIELDS = {
    "channel_id",
    "cycle_id",
    "step_record_id",
    "record_uuid",
    "step_id",
    "step_label",
    "step_name",
    "step_order",
    "duration",
    "interval_from_prev",
    "confidence",
    "is_valid",
}

EVENT_FIRE_FIELDS = {
    "channel_id",
    "cycle_id",
    "event_id",
    "event_name",
    "event_kind",
    "reason",
    "had_workpiece",
    "should_warn_no_barcode",
    "require_ack",
}

SCAN_RECEIVED_FIELDS = {
    "scanner_device_id",
    "scanner_name",
    "scanner_ip",
    "serial_no",
    "raw",
    "broadcast_channel_ids",
}

PROJECT_ACTIVATED_FIELDS = {
    "project_id",
    "project_name",
    "project_version",
    "active_channel_ids",
    "model_name",
    "model_version",
}


# ----------------------- 工具 -----------------------


def _scan_ctx_fields(src_path: Path, hook_type: str) -> set:
    """从源文件抓 fire_plugin_hook("<hook_type>", ...) 调用的 ctx dict 字面量字段名集合.

    用静态扫法（不导入运行时模块）以避免触发完整 backend 启动副作用.
    """
    src = src_path.read_text(encoding="utf-8")
    pattern = rf'fire_plugin_hook\("{re.escape(hook_type)}".*?\{{(.+?)\}}\)'
    m = re.search(pattern, src, re.DOTALL)
    assert m is not None, (
        f"未找到 fire_plugin_hook('{hook_type}', ...) 调用 — "
        f"hook 接入点被删? 文件: {src_path}"
    )
    body = m.group(1)
    return set(re.findall(r'"([a-z_][a-z0-9_]*)":', body))


def _assert_fields_match(found: set, expected: set, hook_type: str) -> None:
    missing = expected - found
    extra = found - expected
    assert not missing, (
        f"{hook_type} ctx 缺字段: {missing} — "
        f"违反 RFC 09 M1.1 契约 (要删字段请同步更新本测试 + plugin SDK 文档)"
    )
    assert not extra, (
        f"{hook_type} ctx 多字段: {extra} — "
        f"违反 RFC 09 M1.1 契约 (新增字段视为升 plugin SDK, 要同步更新本测试 + RFC)"
    )


# ----------------------- 5 个 hook ctx 字段集合稳定性 -----------------------


def test_cycle_start_ctx_field_set_locked():
    """cycle_start hook ctx 字段集合契约."""
    src = REPO_ROOT / "backend" / "api" / "source_session_lifecycle_mixin.py"
    found = _scan_ctx_fields(src, "cycle_start")
    _assert_fields_match(found, CYCLE_START_FIELDS, "cycle_start")


def test_step_change_ctx_field_set_locked():
    """step_change hook ctx 字段集合契约 — 客户需求 1 (步骤耗时三档) 依赖."""
    src = REPO_ROOT / "backend" / "api" / "source_session_lifecycle_mixin.py"
    found = _scan_ctx_fields(src, "step_change")
    _assert_fields_match(found, STEP_CHANGE_FIELDS, "step_change")


def test_event_fire_ctx_field_set_locked():
    """event_fire hook ctx 字段集合契约 — 客户需求 2 (步骤级 NG 抑制) 依赖.

    特别注意: event_kind 三档 (OK/NG/CUSTOM) 是契约字段, 不在主程序原 event_id 数字范畴内.
    """
    src = REPO_ROOT / "backend" / "api" / "source_event_trigger_mixin.py"
    found = _scan_ctx_fields(src, "event_fire")
    _assert_fields_match(found, EVENT_FIRE_FIELDS, "event_fire")


def test_scan_received_ctx_field_set_locked():
    """scan_received hook ctx 字段集合契约."""
    src = REPO_ROOT / "backend" / "services" / "scanner.py"
    found = _scan_ctx_fields(src, "scan_received")
    _assert_fields_match(found, SCAN_RECEIVED_FIELDS, "scan_received")


def test_project_activated_ctx_field_set_locked():
    """project_activated hook ctx 字段集合契约."""
    src = REPO_ROOT / "backend" / "api" / "projects.py"
    found = _scan_ctx_fields(src, "project_activated")
    _assert_fields_match(found, PROJECT_ACTIVATED_FIELDS, "project_activated")


# ----------------------- 接入点位置稳定性 -----------------------


def test_cycle_start_hook_after_mes_hook_before_recording():
    """cycle_start 必须在 MES Hook 之后, start_cycle_recording 之前 fire.

    位置约束: cycle 已落库 + MES 已通知 = "新周期开始"事件完整发生; 录像启动失败
    不影响 cycle 已开始的事实, 所以放 hook 之后.
    """
    src = (REPO_ROOT / "backend" / "api" / "source_session_lifecycle_mixin.py").read_text(
        encoding="utf-8"
    )
    # 找 start_cycle 方法体
    m = re.search(
        r'def start_cycle\(self\):(.+?)(?=\n    def )',
        src,
        re.DOTALL,
    )
    assert m is not None, "start_cycle 方法未找到"
    body = m.group(1)

    mes_idx = body.find("on_cycle_start(")
    hook_idx = body.find('fire_plugin_hook("cycle_start"')
    rec_idx = body.find("start_cycle_recording()")

    assert mes_idx > 0, "start_cycle 中 MES on_cycle_start 调用消失"
    assert hook_idx > 0, "start_cycle 中 fire_plugin_hook 调用消失"
    assert rec_idx > 0, "start_cycle 中 start_cycle_recording 调用消失"

    assert mes_idx < hook_idx < rec_idx, (
        "cycle_start hook 必须在 MES Hook 之后 / start_cycle_recording 之前. "
        f"实际位置: MES={mes_idx} hook={hook_idx} recording={rec_idx}"
    )


def test_event_fire_hook_after_end_cycle_uses_cached_cycle_id():
    """event_fire hook 必须用缓存的 cycle_id (而非 self.current_cycle_id).

    原因: end_cycle 内部会把 self.current_cycle_id 清成 None. 如果 hook 直接读
    self.current_cycle_id, 拿到的永远是 None, 客户做"按 cycle 维度聚合"会全失效.
    """
    src = (REPO_ROOT / "backend" / "api" / "source_event_trigger_mixin.py").read_text(
        encoding="utf-8"
    )
    # 必须有 _event_cycle_id 局部变量缓存
    assert "_event_cycle_id = self.current_cycle_id" in src, (
        "缺 _event_cycle_id 缓存 — event_fire ctx.cycle_id 会拿到 None"
    )
    # fire_plugin_hook("event_fire", ...) 的 ctx 必须用 _event_cycle_id 而非 self.current_cycle_id
    fire_block = re.search(
        r'fire_plugin_hook\("event_fire".*?\{(.+?)\}\)', src, re.DOTALL
    )
    assert fire_block is not None
    body = fire_block.group(1)
    assert "_event_cycle_id" in body, (
        "event_fire ctx 必须用 _event_cycle_id 缓存变量"
    )
    assert "self.current_cycle_id" not in body, (
        "event_fire ctx 不能直接读 self.current_cycle_id (此时已被 end_cycle 清零)"
    )


def test_scan_received_hook_not_in_early_return_paths():
    """scan_received hook 只在主路径 fire, 早返回路径不 fire.

    背景: dedup / 解析失败 / external_only / test 期间这些路径都是 "扫码事件未完成接收",
    不应该让插件以为扫码成功了.
    """
    src = (REPO_ROOT / "backend" / "services" / "scanner.py").read_text(encoding="utf-8")
    # _on_data_received 方法体
    m = re.search(
        r'def _on_data_received\(self.*?\n(?=    def |\nclass )',
        src,
        re.DOTALL,
    )
    assert m is not None, "_on_data_received 方法未找到"
    body = m.group(0)

    # 该方法应只有 1 处 fire_plugin_hook("scan_received", ...) 调用
    count = len(re.findall(r'fire_plugin_hook\("scan_received"', body))
    assert count == 1, (
        f"_on_data_received 中 fire_plugin_hook('scan_received', ...) 调用次数 = {count}, "
        f"应只在主路径 fire 一次"
    )


# ----------------------- 全部 5 个 hook 都用 fire_plugin_hook 统一入口 -----------------------


def test_all_m11_hooks_go_through_fire_plugin_hook():
    """5 个新 hook 必须走 hook_dispatch.fire_plugin_hook, 不准直接调 registry.hooks.fire.

    用统一入口的好处: rg fire_plugin_hook 一次列全所有接入点 (审计友好);
    异常隔离 + 日志格式一致.
    """
    hook_files_and_types = [
        ("backend/api/source_session_lifecycle_mixin.py", ["cycle_start", "step_change"]),
        ("backend/api/source_event_trigger_mixin.py", ["event_fire"]),
        ("backend/services/scanner.py", ["scan_received"]),
        ("backend/api/projects.py", ["project_activated"]),
    ]
    for rel_path, hook_types in hook_files_and_types:
        src = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
        for ht in hook_types:
            assert f'fire_plugin_hook("{ht}"' in src, (
                f"{rel_path} 中找不到 fire_plugin_hook('{ht}', ...) 调用"
            )
        # 不允许出现 registry.hooks.fire("<hook_type>" 这种直接调用
        # 注: source_session_lifecycle_mixin.py 历史 cycle_end 接入点用的就是
        # plugin_manager.registry.hooks.fire, 是 G1.5 阶段债务, 不强制本测试拦截.
        # 但 M1.1 新增的 5 个 hook 必须走 fire_plugin_hook.
