"""单元测试：传动杆误判过滤（companion + session gate）
跑法: python test_rod_filter.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend.api.rod_filter import (
    filter_rod_by_companion,
    RodSessionGate,
    read_rod_filter_config,
)


def _det(label, x, y, w, h, conf=0.9, **kw):
    d = {"label": label, "x": x, "y": y, "w": w, "h": h, "confidence": conf}
    d.update(kw)
    return d


def _labels(dets):
    return [d["label"] for d in dets]


# ---------- 第 1 层: filter_rod_by_companion ----------

def test_empty_passthrough():
    assert filter_rod_by_companion([]) == []


def test_no_rod_no_filter():
    dets = [_det("大框架", 0.1, 0.1, 0.3, 0.3), _det("小框架", 0.5, 0.5, 0.1, 0.1)]
    out = filter_rod_by_companion(dets)
    assert _labels(out) == ["大框架", "小框架"]


def test_rod_no_companion_dropped():
    """同帧无任何 companion → rod 全丢"""
    dets = [_det("传动杆", 0.1, 0.1, 0.3, 0.05)]
    out = filter_rod_by_companion(dets)
    assert out == []


def test_rod_companion_far_apart_dropped():
    """有 companion 但 rod 中心不在框内 & IoU=0 → 丢"""
    dets = [
        _det("大框架", 0.0, 0.0, 0.2, 0.2),     # 左上
        _det("传动杆", 0.7, 0.7, 0.2, 0.05),    # 右下，远离
    ]
    out = filter_rod_by_companion(dets, iou_thr=0.25)
    assert _labels(out) == ["大框架"]


def test_rod_center_in_companion_kept():
    """rod 中心落在大框架 bbox 内 → 即使 IoU 小也保留"""
    dets = [
        _det("大框架", 0.1, 0.1, 0.8, 0.8),
        _det("传动杆", 0.4, 0.45, 0.2, 0.05),
    ]
    out = filter_rod_by_companion(dets, iou_thr=0.25)
    assert set(_labels(out)) == {"大框架", "传动杆"}


def test_rod_high_iou_kept():
    """IoU 达标但中心不在 bbox 内也应保留"""
    dets = [
        _det("大框架", 0.2, 0.2, 0.4, 0.4),   # xyxy: (.2,.2,.6,.6)
        _det("传动杆", 0.3, 0.3, 0.4, 0.4),   # xyxy: (.3,.3,.7,.7), center (.5,.5) 在框架内
    ]
    out = filter_rod_by_companion(dets, iou_thr=0.25)
    assert set(_labels(out)) == {"大框架", "传动杆"}


def test_rod_side_panel_companion_kept():
    """侧板也是 companion"""
    dets = [
        _det("侧板", 0.1, 0.1, 0.8, 0.8),
        _det("传动杆", 0.3, 0.3, 0.4, 0.1),
    ]
    out = filter_rod_by_companion(dets)
    assert "传动杆" in _labels(out)


def test_multiple_rods_partial_drop():
    """多个 rod，一个合法一个误判，只丢误判"""
    dets = [
        _det("大框架", 0.2, 0.2, 0.5, 0.5),     # xyxy 0.2-0.7
        _det("传动杆", 0.3, 0.4, 0.3, 0.05, conf=0.7),  # 中心 (0.45, 0.425) 在框架内 → keep
        _det("传动杆", 0.9, 0.9, 0.05, 0.02, conf=0.92),  # 右下远离 → drop
    ]
    out = filter_rod_by_companion(dets, iou_thr=0.25)
    kept_rod_confs = [d["confidence"] for d in out if d["label"] == "传动杆"]
    assert kept_rod_confs == [0.7]


def test_custom_rod_label():
    """允许自定义 rod_label 和 companion_labels"""
    dets = [
        _det("frame_big", 0.1, 0.1, 0.8, 0.8),
        _det("rod", 0.4, 0.4, 0.2, 0.05),
    ]
    out = filter_rod_by_companion(
        dets, rod_label="rod", companion_labels=("frame_big",)
    )
    assert set(_labels(out)) == {"frame_big", "rod"}


# ---------- 第 2 层: RodSessionGate ----------

def test_gate_blocks_rod_before_companion():
    g = RodSessionGate()
    dets = [_det("传动杆", 0.1, 0.1, 0.1, 0.02)]
    assert g.update_and_filter(dets) == []
    assert g.companion_seen is False


def test_gate_opens_after_companion():
    g = RodSessionGate()
    # 第 1 帧: 只有 rod, 被挡
    assert g.update_and_filter([_det("传动杆", 0.1, 0.1, 0.1, 0.02)]) == []
    # 第 2 帧: 大框架出现
    out2 = g.update_and_filter([_det("大框架", 0.1, 0.1, 0.5, 0.5)])
    assert _labels(out2) == ["大框架"]
    assert g.companion_seen is True
    # 第 3 帧: 只有 rod, 现在能过
    out3 = g.update_and_filter([_det("传动杆", 0.2, 0.2, 0.1, 0.02)])
    assert _labels(out3) == ["传动杆"]


def test_gate_reset_closes_again():
    g = RodSessionGate()
    g.update_and_filter([_det("大框架", 0.1, 0.1, 0.5, 0.5)])
    assert g.companion_seen is True
    g.reset()
    assert g.companion_seen is False
    assert g.update_and_filter([_det("传动杆", 0.1, 0.1, 0.1, 0.02)]) == []


def test_gate_side_panel_not_trigger():
    """gate 默认只认 大/小框架，不认侧板"""
    g = RodSessionGate()
    g.update_and_filter([_det("侧板", 0.1, 0.1, 0.5, 0.5)])
    assert g.companion_seen is False
    # rod 依然被挡
    assert g.update_and_filter([_det("传动杆", 0.1, 0.1, 0.1, 0.02)]) == []


# ---------- read_rod_filter_config ----------

def test_config_defaults_all_off():
    cfg = read_rod_filter_config(None)
    assert cfg["companion_enabled"] is False
    assert cfg["gate_enabled"] is False
    assert cfg["companion_iou_thr"] == 0.25
    assert "大框架" in cfg["companion_labels"]
    assert "大框架" in cfg["gate_labels"]


def test_config_partial_enables():
    cfg = read_rod_filter_config({
        "rod_companion_filter": {"enabled": True, "iou_threshold": 0.15},
    })
    assert cfg["companion_enabled"] is True
    assert cfg["companion_iou_thr"] == 0.15
    assert cfg["gate_enabled"] is False


def test_config_custom_labels():
    cfg = read_rod_filter_config({
        "rod_session_gate": {
            "enabled": True,
            "rod_label": "rod_x",
            "gate_labels": ["A", "B"],
        },
    })
    assert cfg["gate_enabled"] is True
    assert cfg["gate_rod_label"] == "rod_x"
    assert set(cfg["gate_labels"]) == {"A", "B"}


# ---------- v2.7.8: 支持从 pipeline_config 读 ----------

def test_config_read_from_pipeline_config():
    """v2.7.8 起：前端把开关写到 project.pipeline_config 里，后端要能读到"""
    cfg = read_rod_filter_config({
        "pipeline_config": {
            "rod_companion_filter": {
                "enabled": True,
                "iou_threshold": 0.3,
                "rod_label": "杆",
                "companion_labels": ["L1", "L2"],
            },
            "rod_session_gate": {
                "enabled": True,
                "rod_label": "杆",
                "gate_labels": ["G1"],
            },
        },
    })
    assert cfg["companion_enabled"] is True
    assert cfg["companion_iou_thr"] == 0.3
    assert cfg["companion_rod_label"] == "杆"
    assert set(cfg["companion_labels"]) == {"L1", "L2"}
    assert cfg["gate_enabled"] is True
    assert cfg["gate_rod_label"] == "杆"
    assert set(cfg["gate_labels"]) == {"G1"}


def test_config_pipeline_overrides_top_level():
    """pipeline_config 优先于顶层；兼顾 v2.7.6 遗留顶层写法"""
    cfg = read_rod_filter_config({
        "pipeline_config": {
            "rod_companion_filter": {"enabled": True, "iou_threshold": 0.4},
        },
        "rod_companion_filter": {"enabled": False, "iou_threshold": 0.9},
    })
    assert cfg["companion_enabled"] is True
    assert cfg["companion_iou_thr"] == 0.4


def test_config_top_level_still_works():
    """v2.7.6 的直接 POST 顶层的调用方不能挂"""
    cfg = read_rod_filter_config({
        "rod_companion_filter": {"enabled": True, "iou_threshold": 0.2},
    })
    assert cfg["companion_enabled"] is True
    assert cfg["companion_iou_thr"] == 0.2


def test_config_missing_pipeline_config_safe():
    """pipeline_config 为 None / 空 dict 都不能崩"""
    assert read_rod_filter_config({"pipeline_config": None})["companion_enabled"] is False
    assert read_rod_filter_config({"pipeline_config": {}})["gate_enabled"] is False


if __name__ == "__main__":
    import inspect
    tests = [
        (name, obj) for name, obj in globals().items()
        if name.startswith("test_") and callable(obj)
    ]
    passed = 0
    failed = []
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
            passed += 1
        except AssertionError as e:
            failed.append((name, f"assert: {e}"))
            print(f"  FAIL  {name}: {e}")
        except Exception as e:
            failed.append((name, f"error: {e}"))
            print(f"  ERROR {name}: {e}")

    print(f"\n{passed}/{len(tests)} passed")
    if failed:
        print(f"\n{len(failed)} failed:")
        for n, err in failed:
            print(f"  - {n}: {err}")
        sys.exit(1)
