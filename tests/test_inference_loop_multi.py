"""Step 5 单测: InferenceLoopMixin 走 router.dispatch + 多模型派发 + fps 同步。

测试范围
========
  - _run_models_for_frame: router 调度 + 串行调 runner + detections 合并
  - _inference_select_and_run_model: 单模型行为等价 + 多模型 detections 来自所有 mi
  - _inference_tick_fps: host.fps_inference = main.fps_inference (Step 5 兼容)
  - 主模型按 logic_mode/task_type 选 runner; 副模型按 mi.model_task 选

测试策略
========
  - 不 mock model.predict 真实跑 (那是 Step 4 测的); 只验证调度 → 合并逻辑
  - 用 monkeypatch 把 self._detect_only/_detect_and_track/_detect_segment 替换为
    可观察的 stub, 检查调用次数 + 参数
"""
from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest

from backend.api.source_inference_router import ModelInstance, Schedule


@pytest.fixture
def vsm():
    """VSM + 强制 CPU + 默认项目 config"""
    from backend.api.source import VideoSourceManager
    mgr = VideoSourceManager(channel_id=0)
    mgr.device = 'cpu'
    mgr.project_config = {'task_type': 'detection', 'logic_mode': 'sequential',
                           'steps_config': []}
    return mgr


def _attach_fake_model(mi, name_token):
    """给 mi 装一个 mock model (不被真调, 但 mi.model is not None 让 runner 不早退)"""
    mi.model = MagicMock()
    mi.model._test_name = name_token
    mi.current_device_info = {'device': 'cpu'}
    mi._is_native_pytorch = True


def _stub_runners(mgr):
    """把 mgr 的 3 个 runner 替换为可观察 stub, 返回字典记录调用"""
    calls = {'detect_only': [], 'detect_and_track': [], 'detect_segment': []}

    def _make_stub(key, det_label):
        def _stub(frame, mi=None):
            calls[key].append({'mi_name': mi.name if mi else None, 'frame_shape': frame.shape})
            return [{
                'x': 0.1, 'y': 0.1, 'w': 0.1, 'h': 0.1,
                'confidence': 0.9, 'class_id': 0, 'label': det_label,
                'model_name': mi.name if mi else 'main',
                'display_color': mi.display_color if mi else '#10b981',
            }]
        return _stub

    mgr._detect_only = _make_stub('detect_only', 'class_a')
    mgr._detect_and_track = _make_stub('detect_and_track', 'class_b')
    mgr._detect_segment = _make_stub('detect_segment', 'class_c')
    # _map_detections_original_to_display 真实存在但需要 video_transform 状态;
    # 我们直接 stub 掉避免坐标系副作用
    mgr._map_detections_original_to_display = lambda dets: dets
    return calls


# ============================================================
# 单模型场景: 行为等价于老路径
# ============================================================
def test_单模型_main_只跑_main(vsm):
    main = vsm.models['main']
    _attach_fake_model(main, 'main')
    calls = _stub_runners(vsm)

    frame = np.full((480, 640, 3), 128, dtype=np.uint8)
    detections, is_track, is_seg, _t = vsm._inference_select_and_run_model(frame)

    assert len(calls['detect_only']) == 1
    assert calls['detect_only'][0]['mi_name'] == 'main'
    assert len(calls['detect_and_track']) == 0
    assert len(calls['detect_segment']) == 0
    assert len(detections) == 1
    assert detections[0]['model_name'] == 'main'
    assert is_track is False
    assert is_seg is False


def test_单模型_logic_mode_tracking_走_detect_and_track(vsm):
    main = vsm.models['main']
    _attach_fake_model(main, 'main')
    vsm.project_config['logic_mode'] = 'tracking'
    calls = _stub_runners(vsm)

    detections, is_track, _is_seg, _t = vsm._inference_select_and_run_model(
        np.full((480, 640, 3), 128, dtype=np.uint8))

    assert len(calls['detect_and_track']) == 1
    assert len(calls['detect_only']) == 0
    assert is_track is True


def test_单模型_task_type_segmentation_走_detect_segment(vsm):
    main = vsm.models['main']
    _attach_fake_model(main, 'main')
    vsm.project_config['task_type'] = 'segmentation'
    calls = _stub_runners(vsm)

    detections, _is_track, is_seg, _t = vsm._inference_select_and_run_model(
        np.full((480, 640, 3), 128, dtype=np.uint8))

    assert len(calls['detect_segment']) == 1
    assert len(calls['detect_only']) == 0
    assert is_seg is True


# ============================================================
# 多模型场景: 主 + 副每帧都跑
# ============================================================
def test_多模型_main_tray_每帧都跑(vsm):
    main = vsm.models['main']
    _attach_fake_model(main, 'main')

    tray = ModelInstance(name='tray', priority=50, display_color='#ff0000')
    _attach_fake_model(tray, 'tray')
    vsm._router.add_model(tray)

    calls = _stub_runners(vsm)

    detections, _, _, _ = vsm._inference_select_and_run_model(
        np.full((480, 640, 3), 128, dtype=np.uint8))

    assert len(calls['detect_only']) == 2  # main + tray 都走 detect_only
    assert {c['mi_name'] for c in calls['detect_only']} == {'main', 'tray'}
    # detections 合并了两个模型的输出
    assert len(detections) == 2
    model_names = {d['model_name'] for d in detections}
    assert model_names == {'main', 'tray'}


def test_多模型_main每帧_tray_n5(vsm):
    """tray every_n_frames=5: 前 4 帧不跑, 第 5 帧跑"""
    main = vsm.models['main']
    _attach_fake_model(main, 'main')

    tray = ModelInstance(name='tray', priority=50, schedule=Schedule(type='every_n_frames', n=5))
    _attach_fake_model(tray, 'tray')
    vsm._router.add_model(tray)

    calls = _stub_runners(vsm)

    # 5 帧, frame_id 必须不同, 用 5 个独立 ndarray
    for i in range(5):
        frame = np.full((480, 640, 3), 100 + i, dtype=np.uint8)
        vsm._inference_select_and_run_model(frame)

    main_calls = [c for c in calls['detect_only'] if c['mi_name'] == 'main']
    tray_calls = [c for c in calls['detect_only'] if c['mi_name'] == 'tray']
    assert len(main_calls) == 5  # main 每帧跑
    assert len(tray_calls) == 1  # tray 仅第 5 帧跑


def test_多模型_副模型_segment_走_detect_segment(vsm):
    """副模型 mi.model_task='segment' 时走 _detect_segment 而非 detect_only"""
    main = vsm.models['main']
    _attach_fake_model(main, 'main')

    seg_aux = ModelInstance(name='aux_seg', priority=40)
    _attach_fake_model(seg_aux, 'aux_seg')
    seg_aux.model_task = 'segment'  # 关键: 副模型自己声明 segment
    vsm._router.add_model(seg_aux)

    calls = _stub_runners(vsm)

    vsm._inference_select_and_run_model(
        np.full((480, 640, 3), 128, dtype=np.uint8))

    # main 走 detect_only (项目 logic_mode=sequential)
    assert any(c['mi_name'] == 'main' for c in calls['detect_only'])
    # aux_seg 走 detect_segment
    assert any(c['mi_name'] == 'aux_seg' for c in calls['detect_segment'])
    # aux_seg 不应进 detect_only
    assert not any(c['mi_name'] == 'aux_seg' for c in calls['detect_only'])


def test_多模型_副模型_不跟随主模型_tracking_模式(vsm):
    """主模型 logic_mode=tracking 时, 主走 _detect_and_track,
       副模型即使是 detect 仍走 _detect_only (不参与 tracking)"""
    main = vsm.models['main']
    _attach_fake_model(main, 'main')
    vsm.project_config['logic_mode'] = 'tracking'

    aux = ModelInstance(name='aux', priority=50)
    _attach_fake_model(aux, 'aux')
    vsm._router.add_model(aux)

    calls = _stub_runners(vsm)

    vsm._inference_select_and_run_model(
        np.full((480, 640, 3), 128, dtype=np.uint8))

    # main 走 detect_and_track
    assert any(c['mi_name'] == 'main' for c in calls['detect_and_track'])
    # aux 走 detect_only (不是 track)
    assert any(c['mi_name'] == 'aux' for c in calls['detect_only'])
    assert not any(c['mi_name'] == 'aux' for c in calls['detect_and_track'])


# ============================================================
# mi.model is None 时跳过 (Step 6 配置 apply 后才加载)
# ============================================================
def test_mi未加载_跳过不调用runner(vsm):
    main = vsm.models['main']
    _attach_fake_model(main, 'main')

    aux = ModelInstance(name='aux')
    # 故意不给 aux 装 model → mi.model is None
    vsm._router.add_model(aux)

    calls = _stub_runners(vsm)

    vsm._inference_select_and_run_model(
        np.full((480, 640, 3), 128, dtype=np.uint8))

    # main 跑了, aux 因 model is None 跳过
    main_calls = [c for c in calls['detect_only'] if c['mi_name'] == 'main']
    aux_calls = [c for c in calls['detect_only'] if c['mi_name'] == 'aux']
    assert len(main_calls) == 1
    assert len(aux_calls) == 0


# ============================================================
# fps tick: per-mi tick_fps + host 同步
# ============================================================
def test_run_models_for_frame_对每个跑过的mi_tick_fps(vsm):
    main = vsm.models['main']
    _attach_fake_model(main, 'main')

    tray = ModelInstance(name='tray', priority=50)
    _attach_fake_model(tray, 'tray')
    vsm._router.add_model(tray)

    _stub_runners(vsm)

    main._fps_inference_counter = 0
    tray._fps_inference_counter = 0

    vsm._inference_select_and_run_model(
        np.full((480, 640, 3), 128, dtype=np.uint8))

    assert main._fps_inference_counter == 1
    assert tray._fps_inference_counter == 1


def test_inference_tick_fps_同步_main_fps_to_host(vsm):
    """每秒一次 host.fps_inference = main.fps_inference"""
    main = vsm.models['main']
    _attach_fake_model(main, 'main')
    main.fps_inference = 25.0  # 假设 main 已稳定 25 fps

    # 第一次 tick: 立刻同步 (假设跨过了 1 秒边界)
    vsm._fps_inference_time = 0.0
    vsm._fps_inference_counter = 30
    vsm._inference_tick_fps(loop_start=2.0)

    assert vsm.fps_inference == 25.0  # 从 main 读
    assert vsm._fps_inference_counter == 0  # 重置
    assert vsm._fps_inference_time == 2.0


def test_inference_tick_fps_无main_fallback老逻辑(vsm):
    """没 main slot 时 fallback 到老的循环次数

    注: tick 顶部 self._fps_inference_counter += 1, 所以预设 17 后
        聚合时拿到的是 18 (本次也算)
    """
    vsm._router.models.clear()

    vsm._fps_inference_time = 0.0
    vsm._fps_inference_counter = 17  # 17 + 本次 1 = 18
    vsm._inference_tick_fps(loop_start=2.0)

    assert vsm.fps_inference == 18


def test_inference_tick_fps_main_未加载_fallback老逻辑(vsm):
    """main 存在但 model is None 也 fallback (避免误报 0 fps)"""
    assert vsm.models['main'].model is None

    vsm._fps_inference_time = 0.0
    vsm._fps_inference_counter = 33  # 33 + 本次 1 = 34
    vsm._inference_tick_fps(loop_start=2.0)

    assert vsm.fps_inference == 34


# ============================================================
# 调度幂等: 同 frame 重复调用不重复跑
# ============================================================
def test_同frame_重复调用_router幂等_不重复跑():
    """同 frame_id 两次调 → router 第二次返回 [], 实际不重复推理"""
    from backend.api.source import VideoSourceManager
    mgr = VideoSourceManager(channel_id=0)
    mgr.device = 'cpu'
    mgr.project_config = {'task_type': 'detection', 'logic_mode': 'sequential',
                           'steps_config': []}
    main = mgr.models['main']
    _attach_fake_model(main, 'main')
    calls = _stub_runners(mgr)

    frame = np.full((480, 640, 3), 100, dtype=np.uint8)
    mgr._inference_select_and_run_model(frame)
    mgr._inference_select_and_run_model(frame)  # 同 frame_id

    # 第二次 schedule 返回 [], 不重复跑
    main_calls = [c for c in calls['detect_only'] if c['mi_name'] == 'main']
    assert len(main_calls) == 1


# ============================================================
# 老 fallback 路径 (router 不存在 / 空 router)
# ============================================================
def test_router_为空_fallback老detect_only(vsm):
    """router 没任何模型 → fallback 调老 _detect_only(frame) 不传 mi"""
    vsm._router.models.clear()  # 清空所有 mi
    calls = _stub_runners(vsm)

    detections, _, _, _ = vsm._inference_select_and_run_model(
        np.full((480, 640, 3), 128, dtype=np.uint8))

    # 走的是 _detect_only(frame) (不传 mi), 老 fallback 路径
    assert len(calls['detect_only']) == 1
    assert calls['detect_only'][0]['mi_name'] is None  # mi 没传
