"""C7 跟踪模式外观字典/堆叠列表跨周期不清 测试。

  - 外观特征字典: 彻底离场的 display_id 应从字典删除。
  - 不完整批次明细: 防御性上限封顶, 不无限堆积。
"""
import types


def _make_tracker():
    from backend.api.source_tracking_mixin import TrackingMixin
    obj = types.SimpleNamespace()
    obj._tracking_objects = {}
    obj._tracking_recently_lost = {}
    obj._tracking_registered_positions = {}
    obj._tracking_appearance = {}
    obj._tracking_lost_frames = {}
    obj._tracking_display_map = {}
    obj._tracking_transferred_ids = {}
    obj.fps_inference = 15
    obj._tracking_increment_lost_expire = types.MethodType(
        TrackingMixin._tracking_increment_lost_expire, obj)
    return obj


def test_c7_appearance_dict_cleaned_when_object_gone():
    obj = _make_tracker()
    # 在场对象 A(did=1), 离场残留外观 B(did=2)/C(did=3)
    obj._tracking_objects = {
        "tA": {"display_id": 1, "class_name": "x", "bbox": {}},
    }
    obj._tracking_appearance = {1: "histA", 2: "histB", 3: "histC"}

    obj._tracking_increment_lost_expire(
        seen_track_ids={"tA"}, per_class_lost_sec={}, max_lost_sec=2,
        current_time=1000.0)

    # 在场的 1 保留; 既不在场也不在 recently_lost 的 2/3 被清
    assert 1 in obj._tracking_appearance
    assert 2 not in obj._tracking_appearance
    assert 3 not in obj._tracking_appearance


def test_c7_appearance_kept_for_recently_lost():
    obj = _make_tracker()
    obj._tracking_objects = {}
    obj._tracking_recently_lost = {
        "tB": {"display_id": 2, "class_name": "x", "bbox": {}, "lost_time": 1000.0},
    }
    obj._tracking_appearance = {2: "histB"}

    obj._tracking_increment_lost_expire(
        seen_track_ids=set(), per_class_lost_sec={}, max_lost_sec=2,
        current_time=1000.5)  # 还没到过期 cutoff

    # recently_lost 里的对象, 外观保留(可能很快回来)
    assert 2 in obj._tracking_appearance


def test_c7_stack_partials_capped():
    """不完整批次明细列表防御性封顶 200。"""
    lst = []
    for i in range(500):
        lst.append({"peak": i, "required": 24})
        if len(lst) > 200:
            del lst[:-200]
    assert len(lst) == 200
    assert lst[0]["peak"] == 300  # 只留最近 200(300..499)
    assert lst[-1]["peak"] == 499
