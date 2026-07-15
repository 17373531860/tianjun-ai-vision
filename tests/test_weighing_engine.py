"""原生称重投料引擎状态机单测 (纯逻辑, 不连真秤不连主程序)。

覆盖《萍乡百斯特项目 终验收标准》第六章核心判定:
  6.5 前置校验拦截 / 6.4 自动去皮 / 6.2 缺料+超量报警 / 6.1 逐件记录 / 6.3 视觉投错拦截。
喂合成重量序列 (+ 合成视觉标签) 即可验全流程。
"""
import time

import pytest

from backend.services.weighing_engine import (
    WeighingStation, merge_config, judge_amount, get_model_spec,
)


def _cfg(**over):
    base = {
        "materials": ["钢帽水泥", "钢脚水泥"],
        "models": {
            "型号A": {
                "钢帽水泥": {"standard": 0.500, "low_tol": 0.020, "high_tol": 0.020},
                "钢脚水泥": {"standard": 0.300, "low_tol": 0.020, "high_tol": 0.020},
            }
        },
        "tare_mode": "auto_stable",
        "tare_trigger_weight": 0.05,
        "tare_settle_samples": 3,
        "stable_tol": 0.003,
        "stable_min_samples": 3,
        "measure_min_weight": 0.005,
        "require_operator": True,
        "require_model": True,
        "material_check": "sequence",
        "auto_zero_after_done": True,
    }
    base.update(over)
    return merge_config(base)


def _feed(st, weight, cfg, n=4, ts0=None):
    """喂 n 帧同一重量, 返回所有事件汇总。"""
    evs = []
    ts0 = ts0 if ts0 is not None else time.time()
    for i in range(n):
        evs += st.on_weight(weight, ts0 + i * 0.1, cfg)
    return evs


def _actions(events):
    return [e.get("action") for e in events]


def _kinds(events):
    return [e.get("kind") for e in events if e.get("action") == "alarm"]


# ---------- 6.5 前置校验 ----------
def test_precheck_no_operator_blocks():
    cfg = _cfg()
    st = WeighingStation(0)
    st.set_context(model_name="型号A")  # 只选型号, 没选人
    events = st.start_product("SN1", cfg)
    assert _actions(events) == ["alarm"]
    assert _kinds(events) == ["precheck"]
    assert st.phase == "idle"  # 没开始


def test_precheck_no_model_blocks():
    cfg = _cfg()
    st = WeighingStation(0)
    st.set_context(operator="张三")
    events = st.start_product("SN1", cfg)
    assert _kinds(events) == ["precheck"]
    assert st.phase == "idle"


def test_precheck_pass_starts():
    cfg = _cfg()
    st = WeighingStation(0)
    st.set_context(operator="张三", model_name="型号A")
    events = st.start_product("SN1", cfg)
    assert "product_start" in _actions(events)
    assert st.phase == "await_tare"


# ---------- 6.4 自动去皮 ----------
def test_auto_tare_on_stable_load():
    cfg = _cfg()
    st = WeighingStation(0, weight_device_id=9)
    st.set_context(operator="张三", model_name="型号A")
    st.start_product("SN1", cfg)
    # 放空盆(皮重 0.2kg)稳定 → 自动去皮
    events = _feed(st, 0.20, cfg, n=4)
    assert "send_tare" in _actions(events)
    assert st.phase == "filling"
    # v3.35.1 皮重看板: 去皮那一刻的毛重被记录进快照 (工件/容器自重)
    assert st.tare_weight == 0.20
    assert st.snapshot()["tare_weight"] == 0.20


# ---------- 6.2 缺料 / 超量 / 合格 ----------
def _to_filling(st, cfg):
    st.set_context(operator="张三", model_name="型号A")
    st.start_product("SN1", cfg)
    _feed(st, 0.20, cfg, n=4)  # 去皮
    assert st.phase == "filling"


def test_shortage_alarm():
    cfg = _cfg()
    st = WeighingStation(0, weight_device_id=9)
    _to_filling(st, cfg)
    events = _feed(st, 0.45, cfg, n=4)  # 标准0.5, 下容差0.02 → 0.45<0.48 缺料
    assert "record" in _actions(events)
    assert "shortage" in _kinds(events)
    rec = [e for e in events if e["action"] == "record"][0]["result"]
    assert rec["verdict"] == "shortage" and rec["net"] == 0.45


def test_over_alarm():
    cfg = _cfg()
    st = WeighingStation(0, weight_device_id=9)
    _to_filling(st, cfg)
    events = _feed(st, 0.55, cfg, n=4)  # 0.55 > 0.52 超量
    assert "over" in _kinds(events)


def test_ok_no_alarm():
    cfg = _cfg()
    st = WeighingStation(0, weight_device_id=9)
    _to_filling(st, cfg)
    events = _feed(st, 0.50, cfg, n=4)  # 正好标准
    assert "record" in _actions(events)
    assert _kinds(events) == []  # 无报警
    rec = [e for e in events if e["action"] == "record"][0]["result"]
    assert rec["verdict"] == "ok"


# ---------- 6.1 双料整件 + 逐件记录 + 完成置零 ----------
def test_two_pour_full_cycle():
    cfg = _cfg()
    st = WeighingStation(0, weight_device_id=9)
    st.set_context(operator="张三", model_name="型号A")
    st.start_product("SN1", cfg)
    # 料别0 钢帽: 去皮 + 投 0.5 ok
    _feed(st, 0.20, cfg, n=4)
    ev0 = _feed(st, 0.50, cfg, n=4)
    assert st.phase == "await_tare" and st.material_idx == 1  # 进第二道料
    # 料别1 钢脚: 去皮 + 投 0.25 缺料(标准0.3)
    _feed(st, 0.15, cfg, n=4)
    ev1 = _feed(st, 0.25, cfg, n=4)
    acts = _actions(ev1)
    assert "product_done" in acts
    assert "send_zero" in acts  # auto_zero
    assert "shortage" in _kinds(ev1)
    assert st.phase == "done"
    assert len(st.results) == 2
    assert st.results[0]["verdict"] == "ok"
    assert st.results[1]["verdict"] == "shortage"


# ---------- 6.3 视觉投错品类拦截 ----------
def test_visual_wrong_material_blocks():
    cfg = _cfg(material_check="visual")
    st = WeighingStation(0, weight_device_id=9)
    _to_filling(st, cfg)
    # 应投钢帽水泥, 但视觉识别到钢脚水泥 → 拦截
    st.set_material_label("钢脚水泥")
    events = _feed(st, 0.50, cfg, n=4)
    assert "wrong" in _kinds(events)
    assert "record" not in _actions(events)  # 拦截: 不记录
    assert st.phase == "filling"  # 停在原地等纠正


def test_visual_correct_material_passes():
    cfg = _cfg(material_check="visual")
    st = WeighingStation(0, weight_device_id=9)
    _to_filling(st, cfg)
    st.set_material_label("钢帽水泥")  # 识别正确
    events = _feed(st, 0.50, cfg, n=4)
    assert "record" in _actions(events)
    assert "wrong" not in _kinds(events)


# ---------- 型号未配标准量: 不误判 OK ----------
def test_no_spec_not_false_ok():
    cfg = _cfg()
    st = WeighingStation(0, weight_device_id=9)
    st.set_context(operator="张三", model_name="未配置的型号X")
    st.start_product("SN1", cfg)
    _feed(st, 0.20, cfg, n=4)
    events = _feed(st, 0.50, cfg, n=4)
    rec = [e for e in events if e["action"] == "record"][0]["result"]
    assert rec["verdict"] == "no_spec"  # 不是 ok
    assert _kinds(events) == []  # no_spec 不报缺料/超量


# ---------- 判定纯函数 ----------
def test_judge_amount_boundaries():
    spec = {"standard": 0.5, "low_tol": 0.02, "high_tol": 0.02}
    assert judge_amount(0.50, spec) == "ok"
    assert judge_amount(0.48, spec) == "ok"      # 边界
    assert judge_amount(0.52, spec) == "ok"      # 边界
    assert judge_amount(0.479, spec) == "shortage"
    assert judge_amount(0.521, spec) == "over"


def test_get_model_spec_missing():
    cfg = _cfg()
    assert get_model_spec(cfg, "型号A", "钢帽水泥") is not None
    assert get_model_spec(cfg, "不存在", "钢帽水泥") is None
    assert get_model_spec(cfg, "型号A", "不存在料别") is None
