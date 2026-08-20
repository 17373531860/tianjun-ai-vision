"""scan_pair 连续三窗金标准剧本（v3.49 WS3 新码先上屏 回归钉子）。

捷昌现场的最小可复现模型：产线连续过三个箱子，每箱扫一次码——
  扫 A(开窗) → 检测 → 扫 B(结算 A + B 上屏) → 检测 → 扫 C(结算 B + C 上屏)
  → 停止收尾(结算 C)。

金标准断言（每一窗都要成立，一个都不能少）：
  1. 身份链条：settle 的 prev_wp_id 恒等于"被结算那一窗"的 wp，绝不漂到新码
  2. 上屏时序（新序默认开）：settle 发生时 inspecting 已经是新码——
     现场观感即"扫码后新码立即上来，合格结果稍后补上，不再倒序"
  3. 归因：每次 settle 的 reason 挂着触发它的新码
  4. 收尾：停止路径把最后一窗按自己的身份结算，链条封口

双通道广播（捷昌工位2 拓扑：扫码器绑主 ch、broadcast 到兄弟 ch）：
  scanner 按 broadcast_channels 顺序对每个 ch 各 register 一个 wp 再调
  _handle_scan_pair_event；主 ch(broadcast_chs[0]) 做完整 settle+开窗，
  兄弟 ch 只 promote。金标准要求两通道各自的身份链条互不串扰。

与 test_scan_pair_window_rotation.py 的分工：那边钉单次换窗的取放契约，
这里钉"连续多窗"的身份链条与新旧序对照——回归捷昌反馈的完整剧本。
"""
from unittest.mock import MagicMock


# ---------------------------------------------------------------- 剧本装备

def _make_mgr(monkeypatch, broadcast_map=None):
    """裸 MESHookManager：掐掉真 settle / 定时器 / 落库副作用，记录 settle 流水。

    broadcast_map: {ch: [broadcast_chs]}，None 时各通道独立（返回 [ch]）。
    """
    from backend.services.mes_hooks import MESHookManager
    mgr = MESHookManager()
    mgr.enabled = True

    if broadcast_map is None:
        monkeypatch.setattr(
            mgr, "_scan_pair_resolve_broadcast_channels", lambda ch: [ch])
    else:
        monkeypatch.setattr(
            mgr, "_scan_pair_resolve_broadcast_channels",
            lambda ch: list(broadcast_map.get(ch, [ch])))

    monkeypatch.setattr(mgr, "_arm_scan_pair_timer", lambda ch: None)
    monkeypatch.setattr(mgr, "_cancel_scan_pair_timer", lambda ch: None)
    monkeypatch.setattr(mgr, "_scan_pair_emit_dup_toast", lambda ch, sn: None)

    settles = []

    def _fake_settle(channel_id, *, force_ng, reason,
                     prev_wp_id=None, prev_scanned_at=None):
        settles.append({
            "ch": channel_id,
            "force_ng": force_ng,
            "reason": reason,
            "prev_wp_id": prev_wp_id,
            "prev_scanned_at": prev_scanned_at,
            "inspecting_at_settle": dict(mgr._inspecting_workpiece),
        })
        return 1

    monkeypatch.setattr(mgr, "_dispatch_scan_pair_settle", _fake_settle)
    mgr._workpiece_svc = MagicMock()
    return mgr, settles


def _scan(mgr, ch, serial, wp_id):
    """模拟 scanner 对单个 ch 的一次扫码：register wp(置 pending) + 事件入口。"""
    mgr._pending_workpiece[ch] = wp_id
    mgr._handle_scan_pair_event(MagicMock(), ch, serial, wp_id)


# ---------------------------------------------------------------- 单通道三窗

def test_single_channel_three_window_chain_new_first(monkeypatch):
    """新序（默认）：三窗连续轮转，身份链条 101→102→103 逐窗封口。"""
    mgr, settles = _make_mgr(monkeypatch)
    mgr._scan_pair_new_first = True
    ch = 0

    _scan(mgr, ch, "SN-A", 101)                       # 窗1开
    assert settles == []
    assert mgr._inspecting_workpiece[ch] == 101

    _scan(mgr, ch, "SN-B", 102)                       # 窗1结算 + 窗2开
    assert len(settles) == 1
    assert settles[0]["prev_wp_id"] == 101            # 结算的是 A 窗身份
    assert settles[0]["prev_scanned_at"] is not None  # 窗口时间随身份带走
    assert "SN-B" in settles[0]["reason"]             # 归因到触发它的新码
    assert settles[0]["inspecting_at_settle"][ch] == 102   # 新码已上屏才结算
    assert mgr._scan_pair_active[ch]["serial_no"] == "SN-B"

    _scan(mgr, ch, "SN-C", 103)                       # 窗2结算 + 窗3开
    assert len(settles) == 2
    assert settles[1]["prev_wp_id"] == 102
    assert "SN-C" in settles[1]["reason"]
    assert settles[1]["inspecting_at_settle"][ch] == 103
    assert mgr._inspecting_workpiece[ch] == 103

    n = mgr.settle_scan_pair_for_stop(ch, discard=False)   # 停止收尾封口
    assert n == 1
    assert len(settles) == 3
    assert settles[2]["prev_wp_id"] == 103
    assert settles[2]["reason"] == "stop_or_standby_user_settle"
    assert ch not in mgr._scan_pair_active            # 窗口清干净

    # 全链条不重不漏：三窗身份严格 101, 102, 103
    assert [s["prev_wp_id"] for s in settles] == [101, 102, 103]
    assert all(not s["force_ng"] for s in settles)


def test_single_channel_three_window_chain_legacy_order(monkeypatch):
    """旧序（开关显式关）：同剧本身份链条同样成立，仅上屏时序不同——
    settle 发生时 inspecting 仍是旧码（先结算后上屏，回退口径）。"""
    mgr, settles = _make_mgr(monkeypatch)
    mgr._scan_pair_new_first = False
    ch = 0

    _scan(mgr, ch, "SN-A", 101)
    _scan(mgr, ch, "SN-B", 102)
    _scan(mgr, ch, "SN-C", 103)
    mgr.settle_scan_pair_for_stop(ch, discard=False)

    assert len(settles) == 3
    # 旧序换码结算走隐式读（不带 prev_wp_id），settle 时 inspecting 还是旧码
    assert settles[0]["prev_wp_id"] is None
    assert settles[0]["inspecting_at_settle"][ch] == 101
    assert settles[1]["prev_wp_id"] is None
    assert settles[1]["inspecting_at_settle"][ch] == 102
    # 停止路径新旧序都显式带身份
    assert settles[2]["prev_wp_id"] == 103
    # 终态与新序一致：窗口清空、最后在屏的是最后一码
    assert ch not in mgr._scan_pair_active
    assert mgr._inspecting_workpiece[ch] == 103


def test_single_channel_timeout_closes_window_with_identity(monkeypatch):
    """超时收尾：没扫下一码时强制 NG 结算，身份仍锁定当前窗。"""
    mgr, settles = _make_mgr(monkeypatch)
    mgr._scan_pair_new_first = True
    ch = 0

    _scan(mgr, ch, "SN-A", 101)
    mgr._on_scan_pair_timeout(ch)

    assert len(settles) == 1
    assert settles[0]["force_ng"] is True
    assert settles[0]["reason"] == "scan_pair_timeout"
    assert settles[0]["prev_wp_id"] == 101
    assert ch not in mgr._scan_pair_active


# ---------------------------------------------------------------- 双通道广播

BCAST = [1, 0]   # 捷昌工位2 拓扑：主 ch=1，广播到兄弟 ch=0


def _broadcast_scan(mgr, serial, wp_ids):
    """模拟 scanner 广播循环：按 broadcast_channels 顺序逐 ch 扫入。

    wp_ids: {ch: wp_id}，各 ch register 各自的 wp（真实 scanner 行为）。
    """
    for ch in BCAST:
        _scan(mgr, ch, serial, wp_ids[ch])


def test_dual_channel_broadcast_three_windows(monkeypatch):
    """双通道广播三窗：每窗两通道各结算一次，身份链条各走各的不串扰。"""
    bmap = {0: BCAST, 1: BCAST}
    mgr, settles = _make_mgr(monkeypatch, broadcast_map=bmap)
    mgr._scan_pair_new_first = True

    # 窗1: 扫 A → 两通道同步开窗，各 promote 各的 wp
    _broadcast_scan(mgr, "SN-A", {1: 201, 0: 101})
    assert settles == []
    assert mgr._inspecting_workpiece[1] == 201
    assert mgr._inspecting_workpiece[0] == 101
    assert mgr._scan_pair_active[0]["serial_no"] == "SN-A"
    assert mgr._scan_pair_active[1]["serial_no"] == "SN-A"

    # 窗2: 扫 B → 主 ch 对两通道各 settle 一次（A 窗），再同步开 B 窗
    _broadcast_scan(mgr, "SN-B", {1: 202, 0: 102})
    assert len(settles) == 2
    by_ch = {s["ch"]: s for s in settles}
    assert set(by_ch) == {0, 1}                       # 两通道各一次，不重不漏
    # 身份不串扰：ch1 结算 ch1 的 wp、ch0 结算 ch0 的 wp
    assert by_ch[1]["prev_wp_id"] == 201
    assert by_ch[0]["prev_wp_id"] == 101
    for s in settles:
        assert "SN-B" in s["reason"]
        # 新序：settle 时主 ch 的新码已上屏
        assert s["inspecting_at_settle"][1] == 202

    # 窗3: 扫 C → 同上，结算 B 窗
    settles.clear()
    _broadcast_scan(mgr, "SN-C", {1: 203, 0: 103})
    by_ch = {s["ch"]: s for s in settles}
    assert set(by_ch) == {0, 1}
    assert by_ch[1]["prev_wp_id"] == 202
    assert by_ch[0]["prev_wp_id"] == 102
    assert mgr._inspecting_workpiece[1] == 203
    assert mgr._inspecting_workpiece[0] == 103

    # 停止收尾：两通道各自封口最后一窗
    settles.clear()
    assert mgr.settle_scan_pair_for_stop(1, discard=False) == 1
    assert mgr.settle_scan_pair_for_stop(0, discard=False) == 1
    by_ch = {s["ch"]: s for s in settles}
    assert by_ch[1]["prev_wp_id"] == 203
    assert by_ch[0]["prev_wp_id"] == 103
    assert 0 not in mgr._scan_pair_active and 1 not in mgr._scan_pair_active


def test_dual_channel_same_code_rescan_no_settle_window_kept(monkeypatch):
    """双通道同码重扫：不触发结算、窗口不轮转（主 ch 软忽略守门）。

    钉现实契约：主 ch(broadcast_chs[0]) 同码守门生效——零 settle、窗口
    serial/wp 不动、主 ch 在屏身份不动。兄弟 ch 没有同码守门，会把重扫
    register 的新 wp promote 上屏（serial 相同、前端显示无差别，仅内部
    wp id 换新）——这是 v3.4.2 以来的既有行为，如未来给兄弟 ch 补守门，
    本断言应同步收紧为"兄弟 ch 身份也不动"。"""
    bmap = {0: BCAST, 1: BCAST}
    mgr, settles = _make_mgr(monkeypatch, broadcast_map=bmap)
    mgr._scan_pair_new_first = True

    _broadcast_scan(mgr, "SN-A", {1: 201, 0: 101})
    _broadcast_scan(mgr, "SN-A", {1: 299, 0: 199})    # 同码重扫

    assert settles == []                              # 绝不触发结算
    assert mgr._scan_pair_active[1]["wp_id"] == 201   # 窗口身份仍是首扫
    assert mgr._scan_pair_active[0]["wp_id"] == 201   # 广播窗口以主 ch wp 登记
    assert mgr._inspecting_workpiece[1] == 201        # 主 ch 在屏身份不动
    assert mgr._inspecting_workpiece[0] == 199        # 兄弟 ch 无守门(既有行为)


def test_dual_channel_discard_on_stop(monkeypatch):
    """停止弹窗选'丢弃'：不结算、窗口清空、产能不计入。"""
    bmap = {0: BCAST, 1: BCAST}
    mgr, settles = _make_mgr(monkeypatch, broadcast_map=bmap)
    mgr._scan_pair_new_first = True

    _broadcast_scan(mgr, "SN-A", {1: 201, 0: 101})
    assert mgr.settle_scan_pair_for_stop(1, discard=True) == 0
    assert mgr.settle_scan_pair_for_stop(0, discard=True) == 0
    assert settles == []
    assert 0 not in mgr._scan_pair_active and 1 not in mgr._scan_pair_active
