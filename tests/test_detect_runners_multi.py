"""Step 4 单测: DetectRunnersMixin 多模型 / ROI / class_filter / display_color 注入。

测试策略
========
不依赖真实 GPU/torch — 通过给 mi.model 设置一个 mock YOLO 实例 (predict 返回
mock results), 验证 runner 的过滤 + 注入逻辑.

mock results 模拟 ultralytics 的 YOLO 输出:
  result.boxes.xyxy  / .conf / .cls / .id  (.id 仅 tracking)
  result.masks.xyn   (仅 segmentation)
  model.names {class_id: name}
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest

from backend.api.source_inference_router import ModelInstance, Schedule


# ============================================================
# Helper: 构造 mock YOLO 实例 + mock results
# ============================================================
def _make_mock_model(class_names=None, detections_per_call=None):
    """返回一个 mock YOLO 对象, predict() 返回模拟的 result list.

    detections_per_call: list of (xyxy, conf, cls_id) tuples for one frame
    """
    if class_names is None:
        class_names = {0: 'class_a', 1: 'class_b'}
    if detections_per_call is None:
        detections_per_call = [(100, 100, 200, 200, 0.9, 0)]

    model = MagicMock()
    model.names = class_names
    model.task = 'detect'

    # 构造 boxes mock
    n = len(detections_per_call)
    if n == 0:
        boxes = None
    else:
        boxes = MagicMock()
        # xyxy: shape (N, 4)
        xyxy_arr = np.array([[d[0], d[1], d[2], d[3]] for d in detections_per_call], dtype=float)
        conf_arr = np.array([d[4] for d in detections_per_call], dtype=float)
        cls_arr = np.array([d[5] for d in detections_per_call], dtype=int)

        def _box_at(i):
            b = MagicMock()
            b.xyxy = [_make_tensor(xyxy_arr[i:i+1].flatten())]
            b.conf = [_make_tensor([conf_arr[i]])]
            b.cls = [_make_tensor([cls_arr[i]])]
            return b

        boxes_list = [_box_at(i) for i in range(n)]
        boxes.__iter__ = lambda self: iter(boxes_list)
        boxes.id = None  # 默认无 tracking id

    result = MagicMock()
    result.boxes = boxes
    result.masks = None
    model.predict = MagicMock(return_value=[result])
    model.track = MagicMock(return_value=[result])
    return model


def _make_tensor(arr):
    """模拟 torch tensor 的 .cpu().numpy() 接口"""
    t = MagicMock()
    t.cpu = MagicMock(return_value=t)
    t.numpy = MagicMock(return_value=np.asarray(arr, dtype=float))
    return t


@pytest.fixture
def vsm():
    """构造 VSM, 强制 CPU 设备, 允许直接调 _detect_only 而不依赖真模型加载流程"""
    from backend.api.source import VideoSourceManager
    mgr = VideoSourceManager(channel_id=0)
    mgr.device = 'cpu'
    # 给 inference_executor 一个真实可用的 ThreadPoolExecutor
    # (mgr.inference_exec 默认是懒初始化, _get_inference_executor() 触发)
    mgr.project_config = {'task_type': 'detection', 'logic_mode': 'sequential',
                           'steps_config': []}
    return mgr


def _frame():
    """640x480 BGR 测试帧 (灰)"""
    return np.full((480, 640, 3), 128, dtype=np.uint8)


# ============================================================
# 老调用兼容: 不传 mi → 自动用 main
# ============================================================
def test_detect_only_老调用_不传_mi_自动用_main(vsm):
    main = vsm.models['main']
    main.model = _make_mock_model(detections_per_call=[(100, 100, 200, 200, 0.9, 0)])
    main.current_device_info = {'type': 'CPU', 'name': 'CPU', 'device': 'cpu'}
    main._is_native_pytorch = True

    detections = vsm._detect_only(_frame())
    assert len(detections) == 1
    assert detections[0]['label'] == 'class_a'
    assert detections[0]['model_name'] == 'main'  # 注入了
    assert detections[0]['display_color'] == '#10b981'  # 默认色
    assert main.model.predict.called


def test_detect_only_显式传_mi(vsm):
    """显式传 副 mi 时, 用 mi 的字段而非 host"""
    aux = ModelInstance(name='tray', display_color='#ff0000', priority=50)
    aux.model = _make_mock_model(detections_per_call=[(50, 50, 150, 150, 0.85, 1)])
    aux.current_device_info = {'type': 'CPU', 'name': 'CPU', 'device': 'cpu'}
    aux._is_native_pytorch = True
    vsm._router.add_model(aux)

    detections = vsm._detect_only(_frame(), mi=aux)
    assert len(detections) == 1
    assert detections[0]['model_name'] == 'tray'
    assert detections[0]['display_color'] == '#ff0000'
    assert detections[0]['label'] == 'class_b'


# ============================================================
# 显式 mi 时 main 不被读 (显式 > 默认)
# ============================================================
def test_显式_mi_不会_fallback_到_main(vsm):
    """显式传副 mi 时, 不应该误读 main 的状态"""
    main = vsm.models['main']
    main.conf = 0.9  # 极高 main 阈值
    aux = ModelInstance(name='aux', conf=0.1)  # 极低 aux 阈值
    aux.model = _make_mock_model(detections_per_call=[(0, 0, 100, 100, 0.5, 0)])
    aux.current_device_info = {'device': 'cpu'}
    aux._is_native_pytorch = True
    vsm._router.add_model(aux)

    detections = vsm._detect_only(_frame(), mi=aux)
    assert len(detections) == 1  # 用 aux.conf=0.1 通过, 不是 main.conf=0.9
    # 验证 model.predict 被调用时用的是 aux.conf, 不是 main.conf
    args, kwargs = aux.model.predict.call_args
    assert kwargs['conf'] == 0.1


# ============================================================
# ROI 裁剪
# ============================================================
def test_detect_only_有_ROI_中心点在外的检测被过滤(vsm):
    """ROI 中心区域. 检测框中心 (0.05, 0.05) 在 ROI 外应被过滤."""
    aux = ModelInstance(name='m', roi=[[0.25, 0.25], [0.75, 0.25], [0.75, 0.75], [0.25, 0.75]])
    # bbox xyxy=(0,0,64,48) → 中心像素 (32, 24) → 归一化 (0.05, 0.05) 在 ROI 外
    aux.model = _make_mock_model(detections_per_call=[(0, 0, 64, 48, 0.9, 0)])
    aux.current_device_info = {'device': 'cpu'}
    aux._is_native_pytorch = True
    vsm._router.add_model(aux)

    detections = vsm._detect_only(_frame(), mi=aux)
    # 中心点 (0.05, 0.05) 不在 ROI [0.25-0.75] 内 → 被过滤
    assert len(detections) == 0


def test_detect_only_有_ROI_中心点在内的检测保留(vsm):
    aux = ModelInstance(name='m', roi=[[0.25, 0.25], [0.75, 0.25], [0.75, 0.75], [0.25, 0.75]])
    # bbox xyxy=(280, 200, 360, 280) → 中心 (320, 240) → 归一化 (0.5, 0.5) 在 ROI 内
    aux.model = _make_mock_model(detections_per_call=[(280, 200, 360, 280, 0.9, 0)])
    aux.current_device_info = {'device': 'cpu'}
    aux._is_native_pytorch = True
    vsm._router.add_model(aux)

    detections = vsm._detect_only(_frame(), mi=aux)
    assert len(detections) == 1


def test_detect_only_有_ROI_缓存了_polygon(vsm):
    aux = ModelInstance(name='m', roi=[[0.25, 0.25], [0.75, 0.25], [0.75, 0.75], [0.25, 0.75]])
    aux.model = _make_mock_model(detections_per_call=[])
    aux.current_device_info = {'device': 'cpu'}
    aux._is_native_pytorch = True
    vsm._router.add_model(aux)

    vsm._detect_only(_frame(), mi=aux)
    # ROI 缓存被建立
    assert aux._roi_mask_cache is not None
    assert aux._roi_polygon_pixels is not None
    assert aux._roi_mask_shape == (480, 640)


# ============================================================
# class_filter 过滤
# ============================================================
def test_detect_only_class_filter_白名单_只放过指定类别(vsm):
    aux = ModelInstance(name='m', class_filter={'class_b'})  # 只允许 class_b
    aux.model = _make_mock_model(detections_per_call=[
        (0, 0, 100, 100, 0.9, 0),  # class_a → 应过滤
        (200, 200, 300, 300, 0.9, 1),  # class_b → 通过
    ])
    aux.current_device_info = {'device': 'cpu'}
    aux._is_native_pytorch = True
    vsm._router.add_model(aux)

    detections = vsm._detect_only(_frame(), mi=aux)
    assert len(detections) == 1
    assert detections[0]['label'] == 'class_b'


def test_detect_only_class_filter_None_不限():
    """class_filter=None 表示不限. 默认 main 就是 None"""
    from backend.api.source import VideoSourceManager
    mgr = VideoSourceManager(channel_id=0)
    mgr.device = 'cpu'
    mgr.project_config = {'task_type': 'detection', 'logic_mode': 'sequential', 'steps_config': []}
    main = mgr.models['main']
    main.model = _make_mock_model(detections_per_call=[
        (0, 0, 100, 100, 0.9, 0),
        (200, 200, 300, 300, 0.9, 1),
    ])
    main.current_device_info = {'device': 'cpu'}
    main._is_native_pytorch = True
    assert main.class_filter is None

    detections = mgr._detect_only(_frame())
    assert len(detections) == 2


# ============================================================
# enabled_labels 项目级过滤 + class_filter 协同
# ============================================================
def test_detect_only_enabled_labels_与_class_filter_协同():
    """两个过滤器都应通过才保留"""
    from backend.api.source import VideoSourceManager
    mgr = VideoSourceManager(channel_id=0)
    mgr.device = 'cpu'
    # 项目只启用 class_a
    mgr.project_config = {
        'task_type': 'detection',
        'logic_mode': 'sequential',
        'steps_config': [{'enabled': True, 'label': 'class_a'}],
    }

    aux = ModelInstance(name='m', class_filter={'class_a', 'class_b'})  # mi 两个都允许
    aux.model = _make_mock_model(detections_per_call=[
        (0, 0, 100, 100, 0.9, 0),  # class_a → 两边都过
        (200, 200, 300, 300, 0.9, 1),  # class_b → 项目层过滤掉
    ])
    aux.current_device_info = {'device': 'cpu'}
    aux._is_native_pytorch = True
    mgr._router.add_model(aux)

    detections = mgr._detect_only(_frame(), mi=aux)
    assert len(detections) == 1
    assert detections[0]['label'] == 'class_a'


# ============================================================
# 模型为空时早退
# ============================================================
def test_detect_only_main_无model_返回空():
    """main slot 没加载模型 → 应返回 []"""
    from backend.api.source import VideoSourceManager
    mgr = VideoSourceManager(channel_id=0)
    mgr.device = 'cpu'
    assert mgr.models['main'].model is None

    detections = mgr._detect_only(_frame())
    assert detections == []


# ============================================================
# detect_and_track / detect_segment 也能接 mi
# ============================================================
def test_detect_and_track_接受_mi(vsm):
    aux = ModelInstance(name='m', display_color='#abcdef')
    aux.model = _make_mock_model(detections_per_call=[(100, 100, 200, 200, 0.9, 0)])
    aux.model.task = 'detect'
    aux.current_device_info = {'device': 'cpu'}
    aux._is_native_pytorch = True
    vsm._router.add_model(aux)

    detections = vsm._detect_and_track(_frame(), mi=aux)
    assert len(detections) >= 1
    assert detections[0]['model_name'] == 'm'
    assert detections[0]['display_color'] == '#abcdef'


def test_detect_segment_接受_mi(vsm):
    aux = ModelInstance(name='seg', display_color='#123456')
    aux.model = _make_mock_model(detections_per_call=[(100, 100, 200, 200, 0.9, 0)])
    aux.model.task = 'segment'
    aux.model_task = 'segment'
    aux.current_device_info = {'device': 'cpu'}
    aux._is_native_pytorch = True
    vsm._router.add_model(aux)

    detections = vsm._detect_segment(_frame(), mi=aux)
    assert len(detections) >= 1
    assert detections[0]['model_name'] == 'seg'
    assert detections[0]['display_color'] == '#123456'


# ============================================================
# GPU lock 占位 (单模型场景下 lock 不竞争, 不阻塞)
# ============================================================
def test_runner_使用_router_gpu_lock(vsm):
    """验证 model.predict 调用在 router.gpu_lock 上下文中 (Step 5 多模型并发时关键)"""
    main = vsm.models['main']
    main.model = _make_mock_model(detections_per_call=[(100, 100, 200, 200, 0.9, 0)])
    main.current_device_info = {'device': 'cpu'}
    main._is_native_pytorch = True

    # 替换 gpu_lock 为可观察的 mock
    lock = MagicMock()
    lock.__enter__ = MagicMock(return_value=None)
    lock.__exit__ = MagicMock(return_value=False)
    vsm._router.gpu_lock = lock

    vsm._detect_only(_frame())
    assert lock.__enter__.called  # GPU lock 进入了
    assert lock.__exit__.called   # 也退出了


# ============================================================
# 推理参数从 mi 读 (而不是 host)
# ============================================================
def test_推理参数_从_mi_读_而不是_host(vsm):
    main = vsm.models['main']
    main.model = _make_mock_model(detections_per_call=[])
    main.current_device_info = {'device': 'cpu'}
    main._is_native_pytorch = True
    main.conf = 0.42       # mi 上的值
    main.iou = 0.55
    main._model_imgsz = 800
    # host 老字段不一致 (Step 3 双写如果不工作时会暴露)
    vsm.conf_threshold = 0.99  # 完全不该被读
    vsm.iou_threshold = 0.01
    vsm._model_imgsz = 320

    vsm._detect_only(_frame())
    args, kwargs = main.model.predict.call_args
    assert kwargs['conf'] == 0.42  # mi 的, 不是 host 的 0.99
    assert kwargs['iou'] == 0.55
    assert kwargs['imgsz'] == 800
