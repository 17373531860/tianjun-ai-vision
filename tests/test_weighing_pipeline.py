"""v3.39 两阶段流水线称重状态机单测 (纯逻辑, 不连真秤不连主程序)。

对应《萍乡百斯特 称重×视觉联动完整方案 v1.5》:
- 3.1 节时序参数全部可配 (去皮触发源三档/延迟/稳定窗/离秤确认/清零延迟与重发)
- 4 节两种作业习惯 (秤上装料 / 拿下装料) 兼容
- 5 节两阶段生命周期: 离秤冻结结算 + 待收尾 FIFO 队列 (标签③结案/超时结案)
- 5.5 节快手兜底 (Z 未发新件已上秤 → 跳过 Z, 皮重差值折算)

喂合成重量序列 (受控时间戳) + 合成标签命中即可验全流程。
"""
import pytest

from backend.services.weighing_engine import PipelineStation, merge_config


def _cfg(**over):
    base = {
        "drive_mode": "pipeline",
        "materials": ["钢帽水泥"],
        "models": {
            "型号A": {
                "钢帽水泥": {"standard": 3.000, "low_tol": 0.100, "high_tol": 0.100},
            }
        },
        "require_operator": False,
        "require_model": True,
        "pipeline": {
            "material": "钢帽水泥",
            "label_onscale": "工件上秤",
            "label_finalize": "加钢脚水泥",
            "tare_min_kg": 0.5,
            "tare_max_kg": 5.0,
            "queue_depth": 2,
        },
        "timing": {
            "tare_trigger_source": "weight_first",
            "tare_delay_ms": 0,
            "tare_stable_ms": 400,
            "tare_stable_tol_kg": 0.005,
            "net_stable_ms": 400,
            "net_stable_tol_kg": 0.005,
            "shortage_alarm_sec": 1.0,
            "depart_confirm_ms": 300,
            "zero_delay_ms": 0,
            "zero_verify_ms": 500,
            "zero_retry": 1,
            "label1_min_frames": 2,
            "label1_fresh_sec": 3.0,
            "label3_min_frames": 2,
            "label3_cooldown_sec": 1.0,
            "fill_timeout_sec": 300,
            "finalize_timeout_sec": 60,
        },
    }
    # 深覆盖 (timing/pipeline 子键)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            base[k].update(v)
        else:
            base[k] = v
    return merge_config(base)


def _st(cfg):
    st = PipelineStation(0, weight_device_id=9)
    st.set_context(operator="张三", model_name="型号A")
    return st


def _feed_span(st, weight, cfg, t0, dur=1.0, hz=10):
    """按 hz 频率喂 dur 秒同一重量, 返回 (事件汇总, 结束时刻)。"""
    evs = []
    n = max(2, int(dur * hz))
    for i in range(n):
        evs += st.on_weight(weight, t0 + i / hz, cfg)
    return evs, t0 + (n - 1) / hz


def _acts(evs):
    return [e.get("action") for e in evs]


def _kinds(evs):
    return [e.get("kind") for e in evs if e.get("action") == "alarm"]


def _feed_until_tare(st, weight, cfg, t0, max_dur=3.0, hz=10):
    """逐帧喂毛重直到 send_tare 发出 (T 之后真秤归零, 不能再喂毛重)。"""
    evs = []
    t = t0
    for i in range(int(max_dur * hz)):
        t = t0 + i / hz
        frame = st.on_weight(weight, t, cfg)
        evs += frame
        if any(e.get("action") == "send_tare" for e in frame):
            return evs, t
    raise AssertionError(f"喂 {max_dur}s 未触发去皮, phase={st.phase}")


def _run_cycle(st, cfg, t0, tare=1.0, net=3.0, onscale_fill=True):
    """跑完一件的秤上阶段: 上秤→去皮(秤归零)→装料→稳定→离秤结算。返回 (事件, 结束时刻)。"""
    all_evs, t = _feed_until_tare(st, tare, cfg, t0)
    assert st.phase == "filling", f"应已去皮进装料, 实际 {st.phase}"
    # T 之后秤显示归零
    evs, t = _feed_span(st, 0.0, cfg, t + 0.1, dur=0.3)
    all_evs += evs
    if not onscale_fill:
        # 拿下装料习惯: 先离秤 (读数=-皮重), 再带料回秤
        evs, t = _feed_span(st, -tare, cfg, t + 0.1, dur=0.6)
        all_evs += evs
        assert st.phase == "filling" and st._offscale
    evs, t = _feed_span(st, net, cfg, t + 0.1, dur=1.0)
    all_evs += evs
    # 离秤: 读数跌到 -皮重
    evs, t = _feed_span(st, -tare, cfg, t + 0.1, dur=1.0)
    all_evs += evs
    return all_evs, t


# ==================== 秤上阶段: 去皮 ====================
def test_tare_on_stable_weight_sends_T():
    cfg = _cfg()
    st = _st(cfg)
    evs, _ = _feed_span(st, 1.0, cfg, 100.0, dur=1.2)
    assert "send_tare" in _acts(evs)
    assert st.phase == "filling"
    assert st.tare_weight == pytest.approx(1.0)


def test_tare_out_of_range_alarms_no_T():
    cfg = _cfg()
    st = _st(cfg)
    evs, _ = _feed_span(st, 8.0, cfg, 100.0, dur=1.5)   # 超皮重上限 5.0
    assert "send_tare" not in _acts(evs)
    assert "tare_range" in _kinds(evs)


def test_no_model_vetoes_tare_and_alarms():
    cfg = _cfg()
    st = PipelineStation(0)
    st.set_context(operator="张三")   # 不选型号
    evs, _ = _feed_span(st, 1.0, cfg, 100.0, dur=1.5)
    assert "send_tare" not in _acts(evs)
    assert "precheck" in _kinds(evs)


def test_tare_delay_configurable():
    cfg = _cfg(timing={"tare_delay_ms": 800})
    st = _st(cfg)
    # 前 0.6s 满足稳定但延迟没到 → 不发 T
    evs, t = _feed_span(st, 1.0, cfg, 100.0, dur=0.6)
    assert "send_tare" not in _acts(evs)
    evs, _ = _feed_span(st, 1.0, cfg, t + 0.1, dur=1.0)
    assert "send_tare" in _acts(evs)


def test_dual_confirm_requires_label():
    cfg = _cfg(timing={"tare_trigger_source": "dual_confirm"})
    st = _st(cfg)
    evs, t = _feed_span(st, 1.0, cfg, 100.0, dur=1.5)
    assert "send_tare" not in _acts(evs)   # 没标签不发
    # 标签①连续 2 帧确认 → 新鲜期内重量条件已满足 → 发 T
    st.feed_labels(True, False, t, cfg)
    st.feed_labels(True, False, t + 0.05, cfg)
    evs, _ = _feed_span(st, 1.0, cfg, t + 0.1, dur=0.5)
    assert "send_tare" in _acts(evs)


def test_label_accelerates_weight_first_tare():
    """weight_first 模式: 标签①佐证到位 → 稳定窗减半提前放行。"""
    cfg = _cfg(timing={"tare_stable_ms": 1000})
    t0 = 100.0
    # 无标签: 0.7s 时窗口 (1.0s) 未覆盖 → 不发
    st1 = _st(cfg)
    evs, _ = _feed_span(st1, 1.0, cfg, t0, dur=0.7)
    assert "send_tare" not in _acts(evs)
    # 有标签: 减半到 0.5s → 0.7s 时已放行
    st2 = _st(cfg)
    st2.feed_labels(True, False, t0, cfg)
    st2.feed_labels(True, False, t0 + 0.05, cfg)
    evs, _ = _feed_span(st2, 1.0, cfg, t0, dur=0.7)
    assert "send_tare" in _acts(evs)


# ==================== 秤上阶段: 装料判定 ====================
def test_full_cycle_ok_settles_on_departure():
    cfg = _cfg()
    st = _st(cfg)
    evs, _ = _run_cycle(st, cfg, 100.0, tare=1.0, net=3.0)
    acts = _acts(evs)
    assert "record" in acts and "product_settled" in acts and "send_zero" in acts
    rec = next(e for e in evs if e.get("action") == "record")["result"]
    assert rec["verdict"] == "ok"
    assert rec["net"] == pytest.approx(3.0)
    assert rec["tare"] == pytest.approx(1.0)
    assert len(st.pending) == 1
    assert st.phase == "empty"


def test_offscale_fill_habit_compatible():
    """拿下装料习惯: 离秤(-皮重)不结算, 带料回秤后正常判定。"""
    cfg = _cfg()
    st = _st(cfg)
    evs, _ = _run_cycle(st, cfg, 100.0, tare=1.0, net=3.0, onscale_fill=False)
    rec = next(e for e in evs if e.get("action") == "record")["result"]
    assert rec["verdict"] == "ok"
    assert len(st.pending) == 1


def test_shortage_alarm_needs_sustained_low():
    cfg = _cfg(timing={"shortage_alarm_sec": 1.0})
    st = _st(cfg)
    _, t = _feed_until_tare(st, 1.0, cfg, 100.0)
    assert st.phase == "filling"
    # 缺料 (2.5 < 3.0-0.1): 稳定后还需持续 1s 才报警 (防瞬时误报)
    evs, t = _feed_span(st, 2.5, cfg, t + 0.1, dur=0.6)
    assert "shortage" not in _kinds(evs)
    evs, _ = _feed_span(st, 2.5, cfg, t + 0.1, dur=1.0)
    assert "shortage" in _kinds(evs)


def test_over_alarm_immediate_on_stable():
    cfg = _cfg()
    st = _st(cfg)
    _, t = _feed_until_tare(st, 1.0, cfg, 100.0)
    evs, _ = _feed_span(st, 3.5, cfg, t + 0.1, dur=1.0)
    assert "over" in _kinds(evs)


def test_ng_departure_settles_ng():
    cfg = _cfg()
    st = _st(cfg)
    evs, _ = _run_cycle(st, cfg, 100.0, tare=1.0, net=2.5)   # 缺料
    rec = next(e for e in evs if e.get("action") == "record")["result"]
    assert rec["verdict"] == "shortage"
    assert st.pending[0]["verdict"] == "shortage"


# ==================== 清零调度 ====================
def test_zero_delay_configurable():
    cfg = _cfg(timing={"zero_delay_ms": 2000})
    st = _st(cfg)
    evs, t = _run_cycle(st, cfg, 100.0)
    assert "send_zero" not in _acts(evs)   # 延迟 2s 未到
    evs, _ = _feed_span(st, -1.0, cfg, t + 0.1, dur=2.5)
    assert "send_zero" in _acts(evs)


def test_zero_verify_retry_then_residue_alarm():
    cfg = _cfg(timing={"zero_verify_ms": 400, "zero_retry": 1})
    st = _st(cfg)
    all_evs, t = _run_cycle(st, cfg, 100.0)
    # Z 已发 (delay=0), 但读数一直不归零 (残留 -1.0) → 验证超时重发 1 次 → 仍不归零报残留
    evs, t2 = _feed_span(st, -1.0, cfg, t + 0.1, dur=1.5)
    all_evs += evs
    assert _acts(all_evs).count("send_zero") == 2   # 首发 + 重发 1 次
    assert "residue" in _kinds(all_evs)
    # 接受漂移为新零点后, 新件仍能正常去皮 (基线折算: -1 基线下读 0.0 = 自重 1.0)
    evs, _ = _feed_span(st, 0.0, cfg, t2 + 1.0, dur=1.5)
    assert "send_tare" in _acts(evs)
    assert st.tare_weight == pytest.approx(1.0)


def test_fast_hand_skips_zero_delta_tare():
    """快手兜底: Z 延迟未发时新件已上秤 → 取消 Z, 皮重按差值折算。"""
    cfg = _cfg(timing={"zero_delay_ms": 5000})
    st = _st(cfg)
    _, t = _run_cycle(st, cfg, 100.0, tare=1.0, net=3.0)
    # Z 还没发 (延迟 5s), 空秤显示 -1.0; 新件 (自重 1.2) 上秤 → 显示 0.2
    evs, _ = _feed_span(st, 0.2, cfg, t + 0.5, dur=1.2)
    acts = _acts(evs)
    assert "send_zero" not in acts          # Z 被取消
    assert "send_tare" in acts              # T 重新归零
    assert st.tare_weight == pytest.approx(1.2)   # 差值折算: 0.2 - (-1.0)


# ==================== 阶段二: 待收尾队列 ====================
def test_finalize_label_closes_fifo_head():
    cfg = _cfg()
    st = _st(cfg)
    _, t = _run_cycle(st, cfg, 100.0)
    sn1 = st.pending[0]["sn"]
    # 冷却期 (1s) 内的收尾动作不受理
    evs = st.feed_labels(False, True, t + 0.2, cfg)
    evs += st.feed_labels(False, True, t + 0.3, cfg)
    assert not [e for e in evs if e.get("action") == "finalize"]
    # 冷却期后受理
    evs = st.feed_labels(False, True, t + 1.5, cfg)
    evs += st.feed_labels(False, True, t + 1.6, cfg)
    fins = [e for e in evs if e.get("action") == "finalize"]
    assert len(fins) == 1
    assert fins[0]["status"] == "label"
    assert fins[0]["entry"]["sn"] == sn1
    assert not st.pending


def test_finalize_timeout_closes_with_alarm():
    cfg = _cfg(timing={"finalize_timeout_sec": 10})
    st = _st(cfg)
    _, t = _run_cycle(st, cfg, 100.0)
    assert len(st.pending) == 1
    evs = st.on_weight(0.0, t + 11.0, cfg)
    fins = [e for e in evs if e.get("action") == "finalize"]
    assert len(fins) == 1 and fins[0]["status"] == "timeout"
    assert "finalize_timeout" in _kinds(evs)
    assert not st.pending


def test_two_pieces_in_flight_fifo_order():
    """双件并行: 件1待收尾期间件2上秤称重; 收尾标签按 FIFO 结案件1。"""
    cfg = _cfg()
    st = _st(cfg)
    _, t = _run_cycle(st, cfg, 100.0, tare=1.0, net=3.0)
    sn1 = st.pending[0]["sn"]
    # 秤已清零 (delay=0, 喂 0 确认归零), 件2 上秤走完
    _feed_span(st, 0.0, cfg, t + 0.2, dur=0.3)
    _, t2 = _run_cycle(st, cfg, t + 1.0, tare=1.1, net=3.05)
    assert len(st.pending) == 2
    evs = st.feed_labels(False, True, t2 + 1.5, cfg)
    evs += st.feed_labels(False, True, t2 + 1.6, cfg)
    fins = [e for e in evs if e.get("action") == "finalize"]
    assert fins and fins[0]["entry"]["sn"] == sn1   # 先进先出: 结案件1
    assert len(st.pending) == 1


def test_queue_depth_guard_alarms():
    cfg = _cfg(pipeline={"queue_depth": 1})
    st = _st(cfg)
    _, t = _run_cycle(st, cfg, 100.0)
    _feed_span(st, 0.0, cfg, t + 0.2, dur=0.3)
    evs, _ = _run_cycle(st, cfg, t + 1.0)
    assert "takt" in _kinds(evs)   # 第二件入队超深度


def test_reset_keeps_pending_queue():
    cfg = _cfg()
    st = _st(cfg)
    _, t = _run_cycle(st, cfg, 100.0)
    assert len(st.pending) == 1
    st.reset()
    assert len(st.pending) == 1   # 已冻结事实不随复位丢
    assert st.phase == "empty"


# ==================== 快照 ====================
def test_snapshot_exposes_pipeline_fields():
    cfg = _cfg()
    st = _st(cfg)
    _run_cycle(st, cfg, 100.0)
    snap = st.snapshot()
    assert snap["drive_mode"] == "pipeline"
    assert snap["settled_count"] == 1
    assert len(snap["pending"]) == 1
    assert snap["pending"][0]["verdict"] == "ok"
