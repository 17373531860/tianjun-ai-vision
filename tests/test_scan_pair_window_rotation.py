"""OVERLAP-2 回归：scan_pair 窗口轮转中「在检工件」映射的取-放配对契约。

背景（技术债 05_tech_debt.md §五 OVERLAP-2）：
v3.4.2 hotfix 后 scan_pair race 仍被标注"可能有 cornercase"——工件结果错写到下一码。
本文件把窗口轮转的可观测契约钉死成确定性回归（不模拟真线程竞态，竞态防御路径
用"残留注入"方式确定性覆盖），配合 AGENTS.md 不变量 #14（取放严格配对）：

  1. 扫 A 开窗 → promote：pending 清空、inspecting == A
  2. 扫 B 换窗 → 先 settle A 的窗口、后 promote B：inspecting 只会是 B，绝不残留 A
  3. 同码二次扫 → 软忽略：不 settle、窗口不动
  4. 异常路径残留的 inspecting（settle 没清干净）→ promote 强制清（v3.4.2 防御线）
  5. 降工位 on_channel_removed → scan_pair 窗口 / 定时器 / 各 dict 全部清干净
"""
from unittest.mock import MagicMock


def _make_mgr(monkeypatch):
    """裸 MESHookManager：掐掉 broadcast 解析 / 定时器 / 真 settle / 落库副作用。"""
    from backend.services.mes_hooks import MESHookManager
    mgr = MESHookManager()
    mgr.enabled = True

    monkeypatch.setattr(mgr, "_scan_pair_resolve_broadcast_channels", lambda ch: [ch])

    timers = {"armed": [], "canceled": []}
    monkeypatch.setattr(mgr, "_arm_scan_pair_timer", lambda ch: timers["armed"].append(ch))
    monkeypatch.setattr(mgr, "_cancel_scan_pair_timer", lambda ch: timers["canceled"].append(ch))

    settles = []

    def _fake_settle(channel_id, *, force_ng, reason,
                     prev_wp_id=None, prev_scanned_at=None):
        settles.append({"ch": channel_id, "force_ng": force_ng, "reason": reason,
                        "prev_wp_id": prev_wp_id,
                        "inspecting_at_settle": mgr._inspecting_workpiece.get(channel_id)})
        # v3.49 WS3 新序: settle 前 inspecting 已被摘下(进 prev_wp_id)或已是新码,
        # 真实 settle 只读不 pop——不再模拟"settle 清 inspecting"。
        return 1

    monkeypatch.setattr(mgr, "_dispatch_scan_pair_settle", _fake_settle)

    toasts = []
    monkeypatch.setattr(mgr, "_scan_pair_emit_dup_toast",
                        lambda ch, serial: toasts.append((ch, serial)))
    mgr._workpiece_svc = MagicMock()
    return mgr, settles, toasts, timers


def test_scan_a_opens_window_and_promotes(monkeypatch):
    mgr, settles, _toasts, timers = _make_mgr(monkeypatch)
    ch = 0
    mgr._pending_workpiece[ch] = 101

    mgr._handle_scan_pair_event(MagicMock(), ch, "SN-A", 101)

    assert settles == []                                   # 首扫无可结算窗口
    assert mgr._scan_pair_active[ch]["serial_no"] == "SN-A"
    assert mgr._inspecting_workpiece[ch] == 101            # promote 生效
    assert ch not in mgr._pending_workpiece                # pending 已被取走（配对）
    assert timers["armed"] == [ch]


def test_scan_b_settles_a_then_promotes_b(monkeypatch):
    """核心契约：换码时 settle 上一窗口、inspecting 只会落在新码上。

    v3.49 WS3 新序 (默认): 先 promote 新码上屏、后 settle——settle 时
    inspecting 已是新码 102, 旧码身份 101 由 prev_wp_id 显式带给 settle。
    """
    mgr, settles, _toasts, _timers = _make_mgr(monkeypatch)
    ch = 0
    mgr._pending_workpiece[ch] = 101
    mgr._handle_scan_pair_event(MagicMock(), ch, "SN-A", 101)

    mgr._pending_workpiece[ch] = 102
    mgr._handle_scan_pair_event(MagicMock(), ch, "SN-B", 102)

    assert len(settles) == 1
    assert settles[0]["ch"] == ch
    assert "SN-B" in settles[0]["reason"]                  # 结算归因到触发它的新码
    assert settles[0]["prev_wp_id"] == 101                 # 旧码身份显式钳制
    assert settles[0]["inspecting_at_settle"] == 102       # settle 时新码已上屏
    assert mgr._scan_pair_active[ch]["serial_no"] == "SN-B"
    assert mgr._inspecting_workpiece[ch] == 102            # 绝不能还是 101
    assert ch not in mgr._pending_workpiece


def test_same_code_rescan_soft_ignored(monkeypatch):
    mgr, settles, toasts, _timers = _make_mgr(monkeypatch)
    ch = 0
    mgr._pending_workpiece[ch] = 101
    mgr._handle_scan_pair_event(MagicMock(), ch, "SN-A", 101)

    mgr._pending_workpiece[ch] = 103                       # 同码重扫会 register 新 wp
    mgr._handle_scan_pair_event(MagicMock(), ch, "SN-A", 103)

    assert settles == []                                   # 不触发结算
    assert mgr._scan_pair_active[ch]["serial_no"] == "SN-A"
    assert mgr._inspecting_workpiece[ch] == 101            # 窗口与在检工件都不动
    assert toasts == [(ch, "SN-A")]


def test_promote_force_clears_stale_inspecting(monkeypatch):
    """v3.4.2 防御线：settle 异常路径没清掉的旧码 inspecting，promote 时强制清。"""
    mgr, _settles, _toasts, _timers = _make_mgr(monkeypatch)
    ch = 0
    mgr._inspecting_workpiece[ch] = 999                    # 模拟异常残留（上一码）
    mgr._pending_workpiece[ch] = 102

    mgr._scan_pair_promote_pending(MagicMock(), ch, 102, "SN-B")

    assert mgr._inspecting_workpiece[ch] == 102            # 残留 999 被强制顶掉
    assert ch not in mgr._pending_workpiece


def test_channel_removed_cleans_scan_pair_state(monkeypatch):
    """AGENTS.md 不变量 #14 取出点之一：降工位必须清干净 scan_pair 全部状态。"""
    mgr, _settles, _toasts, timers = _make_mgr(monkeypatch)
    ch = 0
    mgr._pending_workpiece[ch] = 101
    mgr._handle_scan_pair_event(MagicMock(), ch, "SN-A", 101)
    assert ch in mgr._scan_pair_active

    mgr.on_channel_removed(ch)

    assert ch not in mgr._scan_pair_active
    assert ch not in mgr._inspecting_workpiece
    assert ch not in mgr._pending_workpiece
    assert ch in timers["canceled"]
