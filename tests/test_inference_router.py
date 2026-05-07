"""Step 1 单测：InferenceRouter 调度逻辑。

只测纯调度，不涉及真实 GPU/模型 — runner 在 Step 4 接入。
覆盖：
  - ModelInstance / Schedule 数据类
  - add/remove/get/main/clear/names
  - schedule_models_for_frame: every_frame / every_n_frames / on_event
  - priority 降序
  - 同帧幂等 (重复调用 schedule 不重复决策)
  - trigger_event / 事件队列消耗
  - tick_fps / reset_fps
  - stats_snapshot 字段完整性
"""
from __future__ import annotations

import time

import pytest

from backend.api.source_inference_router import (
    InferenceRouter,
    ModelInstance,
    Schedule,
)


# ============================================================
# Schedule 数据类
# ============================================================
def test_schedule_默认是_every_frame():
    s = Schedule()
    assert s.type == "every_frame"
    assert s.n == 1


def test_schedule_unknown_type_报错():
    with pytest.raises(ValueError, match="未知调度类型"):
        Schedule(type="bogus")


def test_schedule_every_n_frames_n_必须_大于_等于_1():
    with pytest.raises(ValueError, match="n 必须 ≥ 1"):
        Schedule(type="every_n_frames", n=0)


# ============================================================
# ModelInstance 字段默认值
# ============================================================
def test_model_instance_默认字段():
    mi = ModelInstance(name="main")
    assert mi.name == "main"
    assert mi.model is None
    assert mi.model_task == "detect"
    assert mi.conf == 0.25
    assert mi.iou == 0.45
    assert mi.roi is None
    assert mi.priority == 50
    assert mi.fps_inference == 0.0
    assert mi.schedule.type == "every_frame"


def test_model_instance_tick_fps_累加_后聚合():
    mi = ModelInstance(name="m")
    mi._fps_inference_time = 1000.0
    for _ in range(5):
        mi.tick_fps(1000.5)  # 同秒内累加但不聚合
    assert mi.fps_inference == 0.0
    assert mi._fps_inference_counter == 5

    mi.tick_fps(1001.5)  # 跨秒触发聚合
    assert mi.fps_inference == 6.0  # 刚才的 5 + 这次的 1
    assert mi._fps_inference_counter == 0


def test_model_instance_reset_fps():
    mi = ModelInstance(name="m")
    mi.fps_inference = 12.5
    mi._fps_inference_counter = 7
    mi.reset_fps()
    assert mi.fps_inference == 0.0
    assert mi._fps_inference_counter == 0


# ============================================================
# 注册 / 移除 / 查找
# ============================================================
def test_router_add_get_remove():
    r = InferenceRouter()
    r.add_model(ModelInstance(name="main"))
    r.add_model(ModelInstance(name="tray"))

    assert r.names() == ["main", "tray"]
    assert r.get("main").name == "main"
    assert r.get("not_exist") is None
    assert r.main().name == "main"

    removed = r.remove_model("tray")
    assert removed.name == "tray"
    assert r.names() == ["main"]


def test_router_main_无_main_时返回第一个():
    r = InferenceRouter()
    r.add_model(ModelInstance(name="alpha"))
    r.add_model(ModelInstance(name="beta"))
    assert r.main().name == "alpha"


def test_router_main_为空时返回_None():
    r = InferenceRouter()
    assert r.main() is None


def test_router_重复注册_报错():
    r = InferenceRouter()
    r.add_model(ModelInstance(name="x"))
    with pytest.raises(ValueError, match="已注册"):
        r.add_model(ModelInstance(name="x"))


def test_router_注册空_name_报错():
    r = InferenceRouter()
    with pytest.raises(ValueError, match="不能为空"):
        r.add_model(ModelInstance(name=""))


def test_router_clear_清空全部状态():
    r = InferenceRouter()
    r.add_model(ModelInstance(name="m"))
    r.trigger_event("cycle_end")
    r._dispatch_counter = 100
    r.clear()
    assert r.names() == []
    assert r._drain_events() == []
    assert r._dispatch_counter == 0


# ============================================================
# 调度：every_frame
# ============================================================
def test_every_frame_每帧都跑():
    r = InferenceRouter()
    r.add_model(ModelInstance(name="main", schedule=Schedule(type="every_frame")))

    for fid in range(1, 6):
        chosen = r.schedule_models_for_frame(fid)
        assert [m.name for m in chosen] == ["main"]


# ============================================================
# 调度：every_n_frames
# ============================================================
def test_every_n_frames_n5_前4帧不跑_第5帧跑():
    r = InferenceRouter()
    r.add_model(ModelInstance(name="tray",
                              schedule=Schedule(type="every_n_frames", n=5)))

    # 帧 1-4 不跑
    for fid in range(1, 5):
        chosen = r.schedule_models_for_frame(fid)
        assert chosen == []

    # 帧 5 跑
    chosen = r.schedule_models_for_frame(5)
    assert [m.name for m in chosen] == ["tray"]

    # 帧 6-9 不跑、帧 10 跑（计数重置）
    for fid in range(6, 10):
        assert r.schedule_models_for_frame(fid) == []
    chosen = r.schedule_models_for_frame(10)
    assert [m.name for m in chosen] == ["tray"]


def test_every_n_frames_n1_等同_every_frame():
    r = InferenceRouter()
    r.add_model(ModelInstance(name="m",
                              schedule=Schedule(type="every_n_frames", n=1)))
    for fid in range(1, 4):
        chosen = r.schedule_models_for_frame(fid)
        assert [m.name for m in chosen] == ["m"]


# ============================================================
# 调度：on_event
# ============================================================
def test_on_event_无事件不跑():
    r = InferenceRouter()
    r.add_model(ModelInstance(
        name="qc",
        schedule=Schedule(type="on_event", events=["cycle_end"]),
    ))
    for fid in range(1, 10):
        assert r.schedule_models_for_frame(fid) == []


def test_on_event_事件触发后下一帧跑一次():
    r = InferenceRouter()
    r.add_model(ModelInstance(
        name="qc",
        schedule=Schedule(type="on_event", events=["cycle_end"]),
    ))

    # 不触发不跑
    assert r.schedule_models_for_frame(1) == []

    r.trigger_event("cycle_end")
    chosen = r.schedule_models_for_frame(2)
    assert [m.name for m in chosen] == ["qc"]

    # 事件被消耗后不再跑
    assert r.schedule_models_for_frame(3) == []


def test_on_event_未匹配的事件不触发():
    r = InferenceRouter()
    r.add_model(ModelInstance(
        name="qc",
        schedule=Schedule(type="on_event", events=["scan_done"]),
    ))
    r.trigger_event("cycle_end")  # 不在监听列表
    assert r.schedule_models_for_frame(1) == []


def test_on_event_多模型监听不同事件():
    r = InferenceRouter()
    r.add_model(ModelInstance(name="a", schedule=Schedule(type="on_event", events=["e1"])))
    r.add_model(ModelInstance(name="b", schedule=Schedule(type="on_event", events=["e2"])))

    r.trigger_event("e1")
    chosen = r.schedule_models_for_frame(1)
    assert [m.name for m in chosen] == ["a"]

    r.trigger_event("e2")
    chosen = r.schedule_models_for_frame(2)
    assert [m.name for m in chosen] == ["b"]


def test_on_event_同帧多事件_对应模型都跑():
    r = InferenceRouter()
    r.add_model(ModelInstance(name="a", schedule=Schedule(type="on_event", events=["e1"])))
    r.add_model(ModelInstance(name="b", schedule=Schedule(type="on_event", events=["e2"])))

    r.trigger_event("e1")
    r.trigger_event("e2")
    chosen = r.schedule_models_for_frame(1)
    assert {m.name for m in chosen} == {"a", "b"}


# ============================================================
# priority 降序
# ============================================================
def test_priority_降序():
    r = InferenceRouter()
    r.add_model(ModelInstance(name="low",  priority=10))
    r.add_model(ModelInstance(name="high", priority=100))
    r.add_model(ModelInstance(name="mid",  priority=50))

    chosen = r.schedule_models_for_frame(1)
    assert [m.name for m in chosen] == ["high", "mid", "low"]


def test_同_priority_保持注册顺序():
    r = InferenceRouter()
    r.add_model(ModelInstance(name="a", priority=50))
    r.add_model(ModelInstance(name="b", priority=50))
    r.add_model(ModelInstance(name="c", priority=50))

    chosen = r.schedule_models_for_frame(1)
    assert [m.name for m in chosen] == ["a", "b", "c"]


# ============================================================
# 同帧幂等
# ============================================================
def test_同_frame_id_重复调用_every_n_frames_计数不重复累加():
    """对 every_n_frames 调度，同 frame_id 重复调用不能让计数器多 +1"""
    r = InferenceRouter()
    r.add_model(ModelInstance(name="m",
                              schedule=Schedule(type="every_n_frames", n=3)))

    # 第一次帧 1：不跑（n=3, count=1）
    assert r.schedule_models_for_frame(1) == []
    assert r.get("m")._frames_since_last_run == 1

    # 同 frame_id=1 再调一次：不应再 +1（_last_seen_frame_id 幂等）
    assert r.schedule_models_for_frame(1) == []
    assert r.get("m")._frames_since_last_run == 1  # 仍然 1

    # 帧 2: count=2
    assert r.schedule_models_for_frame(2) == []
    assert r.get("m")._frames_since_last_run == 2

    # 帧 3: count=3 → 跑 + 清零
    chosen = r.schedule_models_for_frame(3)
    assert [m.name for m in chosen] == ["m"]
    assert r.get("m")._frames_since_last_run == 0


def test_同_frame_id_重复调用_event_不被重复消耗():
    """on_event 调度时，同帧重复调用不能让事件队列被多次 drain"""
    r = InferenceRouter()
    r.add_model(ModelInstance(
        name="qc",
        schedule=Schedule(type="on_event", events=["e1"]),
    ))
    r.trigger_event("e1")

    # 第一次：消耗 e1，qc 跑
    chosen = r.schedule_models_for_frame(1)
    assert [m.name for m in chosen] == ["qc"]

    # 同帧再调：不再返回 qc（事件已消耗，且本帧已决策过）
    chosen = r.schedule_models_for_frame(1)
    assert chosen == []

    # 触发新事件 + 新帧才能再跑
    r.trigger_event("e1")
    chosen = r.schedule_models_for_frame(2)
    assert [m.name for m in chosen] == ["qc"]


def test_跑过的帧重复调用不重复返回():
    """对于实际跑了模型的帧，重复 schedule 应返回空（避免重复推理）"""
    r = InferenceRouter()
    r.add_model(ModelInstance(name="m"))

    chosen1 = r.schedule_models_for_frame(7)
    assert [m.name for m in chosen1] == ["m"]

    chosen2 = r.schedule_models_for_frame(7)  # 同 frame_id 重复
    assert chosen2 == []  # 已经决策过，不重复


# ============================================================
# 综合：一个真实场景（main + tray + qc）
# ============================================================
def test_综合_main每帧_tray每5帧_qc按事件_priority正确排序():
    r = InferenceRouter()
    r.add_model(ModelInstance(
        name="main",
        schedule=Schedule(type="every_frame"),
        priority=100,
    ))
    r.add_model(ModelInstance(
        name="tray",
        schedule=Schedule(type="every_n_frames", n=5),
        priority=50,
    ))
    r.add_model(ModelInstance(
        name="qc",
        schedule=Schedule(type="on_event", events=["cycle_end"]),
        priority=30,
    ))

    # 帧 1-4: 只 main 跑
    for fid in range(1, 5):
        chosen = r.schedule_models_for_frame(fid)
        assert [m.name for m in chosen] == ["main"]

    # 帧 5: main + tray (priority 决定顺序)
    chosen = r.schedule_models_for_frame(5)
    assert [m.name for m in chosen] == ["main", "tray"]

    # 帧 6: 只 main
    chosen = r.schedule_models_for_frame(6)
    assert [m.name for m in chosen] == ["main"]

    # 触发 cycle_end → 帧 7 应跑 main + qc
    r.trigger_event("cycle_end")
    chosen = r.schedule_models_for_frame(7)
    assert [m.name for m in chosen] == ["main", "qc"]

    # 帧 8: 只 main (qc 事件已消耗)
    chosen = r.schedule_models_for_frame(8)
    assert [m.name for m in chosen] == ["main"]


# ============================================================
# stats_snapshot 字段完整性
# ============================================================
def test_stats_snapshot_字段完整():
    r = InferenceRouter()
    mi = ModelInstance(
        name="main",
        model_path="/path/m.engine",
        model_task="detect",
        conf=0.3,
        iou=0.5,
        roi=[[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]],
        schedule=Schedule(type="every_n_frames", n=3),
        class_filter={"a", "b"},
        priority=100,
        display_color="#ff0000",
    )
    mi.fps_inference = 12.5
    mi.latency = 88
    r.add_model(mi)

    snap = r.stats_snapshot()
    assert len(snap) == 1
    s = snap[0]
    assert s["name"] == "main"
    assert s["model_path"] == "/path/m.engine"
    assert s["model_task"] == "detect"
    assert s["model_loaded"] is False  # model 没 set
    assert s["conf"] == 0.3
    assert s["iou"] == 0.5
    assert s["roi"] == [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]]
    assert s["schedule"] == {"type": "every_n_frames", "n": 3, "events": []}
    assert s["class_filter"] == ["a", "b"]
    assert s["priority"] == 100
    assert s["display_color"] == "#ff0000"
    assert s["fps_inference"] == 12.5
    assert s["latency"] == 88


def test_stats_snapshot_class_filter_None_时为_None():
    r = InferenceRouter()
    r.add_model(ModelInstance(name="m"))  # class_filter 默认 None
    snap = r.stats_snapshot()
    assert snap[0]["class_filter"] is None


# ============================================================
# 锁存在性（不深入测，只验证暴露给宿主）
# ============================================================
def test_router_暴露_gpu_lock_和_warmup_lock():
    r = InferenceRouter()
    assert isinstance(r.gpu_lock, type(threading_lock()))  # 等价 threading.Lock
    assert isinstance(r.warmup_lock, type(threading_lock()))


def threading_lock():
    """构造一个 threading.Lock 实例用于类型对比"""
    import threading
    return threading.Lock()
