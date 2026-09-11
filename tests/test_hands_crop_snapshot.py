"""副屏手部裁切快照的纯几何、回退与 API 合同测试。"""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest


def test_crop_rect_unions_boxes_and_landmarks_then_applies_existing_padding():
    from backend.api.source_mediapipe import _compute_hands_crop_rect, _expand_bbox

    frame_shape = (500, 800, 3)
    boxes = [(100, 100, 200, 220), (400, 150, 500, 260)]
    landmarks = [[(80, 90), (210, 230)], [(520, 270)]]

    rect = _compute_hands_crop_rect(
        frame_shape,
        boxes,
        landmarks,
        pad_ratio=0.1,
        smoothing_alpha=1.0,
    )

    required = _expand_bbox(80, 90, 520, 270, 800, 500, 0.1)
    assert rect[0] <= required[0]
    assert rect[1] <= required[1]
    assert rect[2] >= required[2]
    assert rect[3] >= required[3]
    assert abs(((rect[2] - rect[0]) / (rect[3] - rect[1])) - (16 / 9)) < 0.01


def test_crop_rect_smooths_motion_without_cutting_current_hand():
    from backend.api.source_mediapipe import _compute_hands_crop_rect

    previous = (100, 100, 500, 325)
    target_box = (300, 200, 400, 300)
    rect = _compute_hands_crop_rect(
        (600, 800, 3),
        [target_box],
        [],
        pad_ratio=0.0,
        previous_rect=previous,
        smoothing_alpha=0.25,
    )

    assert rect != previous
    assert (rect[2] - rect[0], rect[3] - rect[1]) == (400, 225)
    assert rect[0] <= target_box[0] and rect[1] <= target_box[1]
    assert rect[2] >= target_box[2] and rect[3] >= target_box[3]


def test_crop_rect_keeps_size_through_small_geometry_jitter():
    from backend.api.source_mediapipe import _compute_hands_crop_rect

    rect = None
    sizes = []
    centers = []
    for box in (
        (250, 180, 390, 320),
        (258, 176, 402, 322),
        (247, 185, 388, 326),
        (262, 181, 405, 319),
    ):
        rect = _compute_hands_crop_rect(
            (720, 1280, 3),
            [box],
            [],
            pad_ratio=0.1,
            previous_rect=rect,
            smoothing_alpha=0.25,
        )
        sizes.append((rect[2] - rect[0], rect[3] - rect[1]))
        centers.append(((rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2))

    assert len(set(sizes)) == 1
    assert sizes[0] == (384, 216)
    assert len(set(centers)) > 1


def test_crop_rect_moves_whole_fixed_window_at_frame_edge():
    from backend.api.source_mediapipe import _compute_hands_crop_rect

    previous = (320, 180, 960, 540)
    target_box = (0, 10, 180, 210)
    rect = _compute_hands_crop_rect(
        (720, 1280, 3),
        [target_box],
        [],
        pad_ratio=0.0,
        previous_rect=previous,
        smoothing_alpha=0.25,
    )

    assert (rect[2] - rect[0], rect[3] - rect[1]) == (640, 360)
    assert rect[0] == 0
    assert rect[0] <= target_box[0] and rect[1] <= target_box[1]
    assert rect[2] >= target_box[2] and rect[3] >= target_box[3]


def test_crop_rect_only_grows_when_union_no_longer_fits():
    from backend.api.source_mediapipe import _compute_hands_crop_rect

    previous = (320, 180, 960, 540)
    large_box = (100, 120, 1180, 600)
    expanded = _compute_hands_crop_rect(
        (720, 1280, 3),
        [large_box],
        [],
        pad_ratio=0.0,
        previous_rect=previous,
        smoothing_alpha=0.25,
    )
    shrunk_target = _compute_hands_crop_rect(
        (720, 1280, 3),
        [(500, 250, 700, 450)],
        [],
        pad_ratio=0.0,
        previous_rect=expanded,
        smoothing_alpha=0.25,
    )

    assert (expanded[2] - expanded[0]) > 640
    assert abs(((expanded[2] - expanded[0]) / (expanded[3] - expanded[1])) - (16 / 9)) < 0.01
    assert (shrunk_target[2] - shrunk_target[0], shrunk_target[3] - shrunk_target[1]) == (
        expanded[2] - expanded[0],
        expanded[3] - expanded[1],
    )


def test_fixed_crop_keeps_same_zoom_for_single_and_oversized_double_hands():
    from backend.api.source_mediapipe import _compute_hands_crop_rect

    frame_shape = (720, 1280, 3)
    single = _compute_hands_crop_rect(
        frame_shape,
        [(500, 250, 700, 450)],
        [],
        pad_ratio=0.1,
        fixed_width_ratio=0.5,
    )
    double = _compute_hands_crop_rect(
        frame_shape,
        [(80, 180, 360, 520), (920, 180, 1200, 520)],
        [],
        pad_ratio=0.1,
        previous_rect=single,
        fixed_width_ratio=0.5,
    )
    single_again = _compute_hands_crop_rect(
        frame_shape,
        [(500, 250, 700, 450)],
        [],
        pad_ratio=0.1,
        previous_rect=double,
        fixed_width_ratio=0.5,
    )

    assert (single[2] - single[0], single[3] - single[1]) == (640, 360)
    assert (double[2] - double[0], double[3] - double[1]) == (640, 360)
    assert (single_again[2] - single_again[0], single_again[3] - single_again[1]) == (640, 360)
    assert 600 < (double[0] + double[2]) / 2 <= 640


def test_fixed_crop_uses_padding_union_as_stationary_dead_zone():
    from backend.api.source_mediapipe import _compute_hands_crop_rect, _expand_bbox

    previous = (320, 180, 960, 540)
    hand_box = (450, 250, 650, 430)
    rect = _compute_hands_crop_rect(
        (720, 1280, 3),
        [hand_box],
        [],
        pad_ratio=0.1,
        previous_rect=previous,
        fixed_width_ratio=0.5,
    )
    required = _expand_bbox(*hand_box, 1280, 720, 0.1)

    assert rect == previous
    assert rect[0] <= required[0] and rect[1] <= required[1]
    assert rect[2] >= required[2] and rect[3] >= required[3]


def test_follow_center_moves_smoothly_without_changing_fixed_viewport_size():
    from backend.api.source_mediapipe import _compute_hands_crop_rect, _expand_bbox

    frame_shape = (720, 1280, 3)
    left = _compute_hands_crop_rect(
        frame_shape,
        [(20, 180, 220, 420)],
        [],
        pad_ratio=0.1,
        fixed_width_ratio=0.5,
        follow_center=True,
    )
    right = _compute_hands_crop_rect(
        frame_shape,
        [(1040, 180, 1240, 420)],
        [],
        pad_ratio=0.1,
        previous_rect=left,
        smoothing_alpha=0.35,
        fixed_width_ratio=0.5,
        follow_center=True,
    )

    assert (left[2] - left[0], left[3] - left[1]) == (640, 360)
    assert (right[2] - right[0], right[3] - right[1]) == (640, 360)
    left_center_x = (left[0] + left[2]) / 2
    right_center_x = (right[0] + right[2]) / 2
    assert left_center_x < right_center_x < 1140
    required = _expand_bbox(1040, 180, 1240, 420, 1280, 720, 0.1)
    assert right[0] <= required[0] and right[1] <= required[1]
    assert right[2] >= required[2] and right[3] >= required[3]


def test_center_crop_is_derived_only_from_source_frame_dimensions():
    from backend.api.source_mediapipe import _compute_center_crop_rect

    assert _compute_center_crop_rect((720, 1280, 3), width_ratio=0.5) == (
        320, 180, 960, 540,
    )
    assert _compute_center_crop_rect((480, 640, 3), width_ratio=0.5) == (
        160, 150, 480, 330,
    )


def test_fixed_center_and_follow_share_viewport_size_for_small_source():
    from backend.api.source_mediapipe import (
        _compute_center_crop_rect,
        _compute_hands_crop_rect,
    )

    frame_shape = (240, 320, 3)
    fixed = _compute_center_crop_rect(frame_shape, width_ratio=0.5)
    follow = _compute_hands_crop_rect(
        frame_shape,
        [(120, 90, 180, 150)],
        [],
        pad_ratio=0.1,
        fixed_width_ratio=0.5,
        follow_center=True,
    )

    assert (fixed[2] - fixed[0], fixed[3] - fixed[1]) == (160, 90)
    assert (follow[2] - follow[0], follow[3] - follow[1]) == (160, 90)


def test_crop_rect_without_hand_reuses_previous_rect_or_returns_none():
    from backend.api.source_mediapipe import _compute_hands_crop_rect

    previous = (12, 34, 212, 234)
    assert _compute_hands_crop_rect(
        (480, 640, 3), [], [], pad_ratio=0.3, previous_rect=previous,
    ) == previous
    assert _compute_hands_crop_rect(
        (480, 640, 3), [], [], pad_ratio=0.3, previous_rect=None,
    ) is None


def test_rendered_crop_is_fixed_640_by_360():
    import numpy as np

    from backend.api.source_mediapipe import _render_hands_crop

    frame = np.zeros((1000, 400, 3), dtype=np.uint8)
    rendered = _render_hands_crop(
        frame,
        (0, 0, 400, 1000),
        [(100, 200, 300, 800)],
        [[(200, 300), (220, 500), (180, 700)]],
        max_edge=640,
    )

    assert rendered is not None
    assert max(rendered.shape[:2]) == 640
    assert rendered.shape[:2] == (360, 640)


def test_rendered_crop_reuses_supplied_mediapipe_hand_styles():
    import numpy as np

    from backend.api.source_mediapipe import _render_hands_crop

    landmark_style = object()
    connection_style = object()
    connections = frozenset({(0, 1)})
    calls = []

    def draw_landmarks(
        image,
        landmark_list,
        supplied_connections,
        landmark_drawing_spec,
        connection_drawing_spec,
    ):
        calls.append((
            image,
            landmark_list,
            supplied_connections,
            landmark_drawing_spec,
            connection_drawing_spec,
        ))

    rendered = _render_hands_crop(
        np.zeros((360, 640, 3), dtype=np.uint8),
        (0, 0, 640, 360),
        [],
        [[(100 + index * 5, 120 + index * 2) for index in range(21)]],
        draw_landmarks=draw_landmarks,
        connections=connections,
        landmark_drawing_spec=landmark_style,
        connection_drawing_spec=connection_style,
    )

    assert rendered is not None
    assert len(calls) == 1
    assert calls[0][0] is rendered
    assert calls[0][2] is connections
    assert calls[0][3] is landmark_style
    assert calls[0][4] is connection_style


def test_main_and_aux_hands_reuse_the_same_drawing_specs():
    import numpy as np

    from backend.api.source_mediapipe import MediaPipeOverlay

    landmark_style = object()
    connection_style = object()
    draw_calls = []

    class FakeDrawingUtils:
        @staticmethod
        def draw_landmarks(*args):
            draw_calls.append(args)

    class FakeDrawingStyles:
        @staticmethod
        def get_default_hand_landmarks_style():
            return landmark_style

        @staticmethod
        def get_default_hand_connections_style():
            return connection_style

    host = SimpleNamespace(
        mediapipe_enabled=True,
        mediapipe_hands=True,
        mediapipe_custom_style=False,
        mediapipe_hand_roi_pad=0.1,
        mediapipe_hands_color="#00FF00",
        mediapipe_hands_point_color="",
        mediapipe_hands_thickness=2,
    )
    overlay = MediaPipeOverlay(host=host)
    overlay._mp_draw = FakeDrawingUtils()
    overlay._mp_draw_styles = FakeDrawingStyles()
    hand_landmarks = SimpleNamespace(
        landmark=[SimpleNamespace(x=0.25, y=0.5) for _ in range(21)],
    )
    overlay._mp_last_hands_results = SimpleNamespace(
        multi_hand_landmarks=[hand_landmarks],
    )

    overlay._draw_baseline_hands(np.zeros((360, 640, 3), dtype=np.uint8))
    overlay._publish_hand_crop_observation(
        np.zeros((360, 640, 3), dtype=np.uint8),
        [],
        [[(100 + index * 5, 120 + index * 2) for index in range(21)]],
    )
    assert overlay.get_hands_crop_snapshot() is not None

    assert len(draw_calls) == 2
    assert draw_calls[0][3] is landmark_style
    assert draw_calls[0][4] is connection_style
    assert draw_calls[1][3] is landmark_style
    assert draw_calls[1][4] is connection_style


@pytest.mark.parametrize("view_mode", ["fixed", "follow"])
def test_snapshot_holds_last_crop_when_hand_temporarily_disappears(view_mode):
    import numpy as np

    from backend.api.source_mediapipe import MediaPipeOverlay

    host = SimpleNamespace(
        mediapipe_enabled=True,
        mediapipe_hands=True,
        mediapipe_hand_roi_pad=0.2,
        mediapipe_hands_color="#00FF00",
        mediapipe_hands_point_color="",
        mediapipe_hands_thickness=2,
    )
    overlay = MediaPipeOverlay(host=host)
    frame = np.zeros((480, 800, 3), dtype=np.uint8)
    overlay._publish_hand_crop_observation(
        frame,
        [(200, 100, 500, 400)],
        [[(250 + index * 5, 150 + index * 4) for index in range(21)]],
    )
    first = overlay.get_hands_crop_snapshot(view_mode=view_mode)

    overlay._publish_hand_crop_observation(
        np.full((480, 800, 3), 255, dtype=np.uint8),
        [],
        [],
    )
    held = overlay.get_hands_crop_snapshot(view_mode=view_mode)

    assert first is not None
    assert held == first


def test_snapshot_always_uses_center_roi_across_hand_positions():
    import numpy as np

    from backend.api.source_mediapipe import MediaPipeOverlay

    host = SimpleNamespace(
        mediapipe_enabled=True,
        mediapipe_hands=True,
        mediapipe_hand_roi_pad=0.0,
        mediapipe_hands_color="#00FF00",
        mediapipe_hands_point_color="",
        mediapipe_hands_thickness=2,
    )
    overlay = MediaPipeOverlay(host=host)
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    overlay._publish_hand_crop_observation(
        frame,
        [(20, 40, 220, 300)],
        [],
    )
    overlay.get_hands_crop_snapshot(view_mode="fixed")
    left_rect = overlay._hand_crop_rect

    overlay._publish_hand_crop_observation(
        frame,
        [(1040, 400, 1260, 700)],
        [],
    )
    overlay.get_hands_crop_snapshot(view_mode="fixed")
    right_rect = overlay._hand_crop_rect

    overlay._publish_hand_crop_observation(
        frame,
        [(20, 40, 220, 300), (1040, 400, 1260, 700)],
        [],
    )
    overlay.get_hands_crop_snapshot(view_mode="fixed")
    double_rect = overlay._hand_crop_rect

    expected = (320, 180, 960, 540)
    assert left_rect == expected
    assert right_rect == expected
    assert double_rect == expected


def test_follow_snapshot_tracks_hand_union_but_keeps_same_size_as_fixed():
    import numpy as np

    from backend.api.source_mediapipe import MediaPipeOverlay

    host = SimpleNamespace(
        mediapipe_enabled=True,
        mediapipe_hands=True,
        mediapipe_hand_roi_pad=0.0,
        mediapipe_hands_color="#00FF00",
        mediapipe_hands_point_color="",
        mediapipe_hands_thickness=2,
    )
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)

    fixed_overlay = MediaPipeOverlay(host=host)
    fixed_overlay._publish_hand_crop_observation(frame, [(20, 180, 220, 420)], [])
    fixed_overlay.get_hands_crop_snapshot(view_mode="fixed")
    fixed_rect = fixed_overlay._hand_crop_rect

    follow_overlay = MediaPipeOverlay(host=host)
    follow_overlay._publish_hand_crop_observation(frame, [(20, 180, 220, 420)], [])
    follow_overlay.get_hands_crop_snapshot(view_mode="follow")
    left_rect = follow_overlay._hand_crop_rect
    follow_overlay._publish_hand_crop_observation(frame, [(1040, 180, 1240, 420)], [])
    follow_overlay.get_hands_crop_snapshot(view_mode="follow")
    right_rect = follow_overlay._hand_crop_rect

    fixed_size = (fixed_rect[2] - fixed_rect[0], fixed_rect[3] - fixed_rect[1])
    left_size = (left_rect[2] - left_rect[0], left_rect[3] - left_rect[1])
    right_size = (right_rect[2] - right_rect[0], right_rect[3] - right_rect[1])
    assert fixed_size == left_size == right_size == (640, 360)
    assert left_rect != right_rect
    assert (left_rect[0] + left_rect[2]) / 2 < (right_rect[0] + right_rect[2]) / 2
    assert right_rect[0] <= 1040 and right_rect[1] <= 180
    assert right_rect[2] >= 1240 and right_rect[3] >= 420


class _PauseOneThreadAfterObservationLock:
    """释放真实锁后暂停指定线程，让后来的请求确定性地先进入 crop 锁。"""

    def __init__(self, paused_thread_name, observation_captured, resume):
        self._lock = threading.Lock()
        self._paused_thread_name = paused_thread_name
        self._observation_captured = observation_captured
        self._resume = resume

    def __enter__(self):
        self._lock.acquire()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self._lock.release()
        if threading.current_thread().name == self._paused_thread_name:
            self._observation_captured.set()
            assert self._resume.wait(timeout=3.0)


def _make_crop_overlay_for_concurrency_test():
    from backend.api.source_mediapipe import MediaPipeOverlay

    host = SimpleNamespace(
        mediapipe_enabled=True,
        mediapipe_hands=True,
        mediapipe_hand_roi_pad=0.2,
        mediapipe_hands_color="#00FF00",
        mediapipe_hands_point_color="",
        mediapipe_hands_thickness=2,
    )
    return MediaPipeOverlay(host=host)


def test_older_concurrent_snapshot_cannot_overwrite_newer_crop():
    import numpy as np

    overlay = _make_crop_overlay_for_concurrency_test()
    old_frame = np.zeros((480, 800, 3), dtype=np.uint8)
    new_frame = np.full((480, 800, 3), 255, dtype=np.uint8)
    geometry = (
        [(200, 100, 500, 400)],
        [[(250 + index * 5, 150 + index * 4) for index in range(21)]],
    )
    overlay._publish_hand_crop_observation(old_frame, *geometry)

    captured = threading.Event()
    resume = threading.Event()
    overlay._hand_observation_lock = _PauseOneThreadAfterObservationLock(
        "old-hands-snapshot",
        captured,
        resume,
    )
    old_result = []
    old_request = threading.Thread(
        target=lambda: old_result.append(overlay.get_hands_crop_snapshot()),
        name="old-hands-snapshot",
    )
    old_request.start()
    assert captured.wait(timeout=3.0)

    overlay._publish_hand_crop_observation(new_frame, *geometry)
    newer_jpeg = overlay.get_hands_crop_snapshot()
    resume.set()
    old_request.join(timeout=3.0)

    assert not old_request.is_alive()
    assert old_result == [newer_jpeg]
    assert overlay._hand_crop_jpeg == newer_jpeg


def test_clear_barrier_rejects_request_that_already_captured_old_observation():
    import numpy as np

    overlay = _make_crop_overlay_for_concurrency_test()
    geometry = (
        [(200, 100, 500, 400)],
        [[(250 + index * 5, 150 + index * 4) for index in range(21)]],
    )
    overlay._publish_hand_crop_observation(
        np.zeros((480, 800, 3), dtype=np.uint8),
        *geometry,
    )

    captured = threading.Event()
    resume = threading.Event()
    overlay._hand_observation_lock = _PauseOneThreadAfterObservationLock(
        "pre-clear-hands-snapshot",
        captured,
        resume,
    )
    old_result = []
    old_request = threading.Thread(
        target=lambda: old_result.append(overlay.get_hands_crop_snapshot()),
        name="pre-clear-hands-snapshot",
    )
    old_request.start()
    assert captured.wait(timeout=3.0)

    overlay._clear_hand_crop_state()
    resume.set()
    old_request.join(timeout=3.0)

    assert not old_request.is_alive()
    assert old_result == [overlay._no_hands_jpeg]
    assert overlay._hand_crop_jpeg is None


def test_two_stage_keeps_legacy_result_shape_and_exposes_raw_box_geometry():
    import numpy as np

    from backend.api.source_mediapipe import MediaPipeOverlay

    class FakeDetector:
        @staticmethod
        def predict(_frame):
            return [(10, 20, 110, 120)]

    class FakeLandmarker:
        @staticmethod
        def detect(_roi):
            return [[SimpleNamespace(x=0.5, y=0.5, z=0.0) for _ in range(21)]]

    overlay = MediaPipeOverlay(host=SimpleNamespace(mediapipe_hand_roi_pad=0.1))
    overlay._hand_detector = FakeDetector()
    overlay._hand_landmarker_tasks = FakeLandmarker()

    boxes, landmark_groups = overlay._run_two_stage_hands(
        np.zeros((200, 300, 3), dtype=np.uint8),
        collect_crop_geometry=True,
    )

    assert boxes == [(10, 20, 110, 120)]
    assert len(overlay._last_two_stage_results) == 1
    assert len(overlay._last_two_stage_results[0]) == 3
    assert len(landmark_groups) == 1
    assert landmark_groups[0][0] == (60.0, 70.0)


def test_baseline_only_publishes_clean_frame_after_hands_crop_is_requested():
    import time

    import numpy as np

    from backend.api.source_mediapipe import MediaPipeOverlay

    results = SimpleNamespace(
        multi_hand_landmarks=[SimpleNamespace(
            landmark=[SimpleNamespace(x=0.25, y=0.5) for _ in range(21)],
        )],
    )

    class FakeHands:
        @staticmethod
        def process(_rgb):
            return results

    host = SimpleNamespace(mediapipe_pose=False, mediapipe_hands=True)
    overlay = MediaPipeOverlay(host=host)
    overlay._mp_hands = FakeHands()
    clean_frame = np.zeros((200, 400, 3), dtype=np.uint8)

    overlay._run_inference(clean_frame)
    assert overlay._hand_crop_observation is None
    assert overlay._mp_last_hands_results is results

    overlay._hand_crop_requested_until = time.monotonic() + 1.0
    overlay._run_inference(clean_frame)
    _seq, published_frame, boxes, landmark_groups = overlay._hand_crop_observation

    assert published_frame is clean_frame
    assert boxes == ()
    assert landmark_groups[0][0] == (100.0, 100.0)


def test_snapshot_view_hands_is_isolated_from_default_snapshot(client, monkeypatch):
    import backend.main as main_module

    calls = []

    class FakeOverlay:
        def get_hands_crop_snapshot(self, view_mode="follow"):
            calls.append(("hands", view_mode))
            return b"hands-jpeg"

    class FakeManager:
        mp_overlay = FakeOverlay()

        def get_snapshot(self):
            calls.append("default")
            return b"default-jpeg"

    manager = FakeManager()
    monkeypatch.setattr(main_module, "get_video_manager", lambda channel=0: manager)

    default_response = client.get("/snapshot?channel=3")
    hands_response = client.get("/snapshot?channel=3&view=hands")
    follow_response = client.get(
        "/snapshot?channel=3&view=hands&crop_mode=follow",
    )
    fixed_response = client.get(
        "/snapshot?channel=3&view=hands&crop_mode=fixed",
    )
    invalid_mode_response = client.get(
        "/snapshot?channel=3&view=hands&crop_mode=zoom",
    )

    assert default_response.status_code == 200
    assert default_response.content == b"default-jpeg"
    assert hands_response.status_code == 200
    assert hands_response.content == b"hands-jpeg"
    assert follow_response.status_code == 200
    assert follow_response.content == b"hands-jpeg"
    assert fixed_response.status_code == 200
    assert fixed_response.content == b"hands-jpeg"
    assert invalid_mode_response.status_code == 200
    assert invalid_mode_response.content == b"hands-jpeg"
    assert calls == [
        "default",
        ("hands", "follow"),
        ("hands", "follow"),
        ("hands", "fixed"),
        ("hands", "follow"),
    ]
    assert default_response.headers["cache-control"] == "no-cache, no-store, must-revalidate"
    assert hands_response.headers["cache-control"] == "no-cache, no-store, must-revalidate"
    assert follow_response.headers["cache-control"] == "no-cache, no-store, must-revalidate"
    assert fixed_response.headers["cache-control"] == "no-cache, no-store, must-revalidate"
    assert invalid_mode_response.headers["cache-control"] == "no-cache, no-store, must-revalidate"
