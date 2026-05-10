"""Step 6 单测: source_routes 多模型 HTTP 端点.

测试范围
========
  - DetectionStartRequest:
      * 老 payload (model_path / conf / iou) → 老路径不变
      * 新 payload (models[]) → 多模型路径, 每个 spec 调 load_model_into_slot
      * 同时兼容 model_path=None (req.models 优先)
  - /detection/results 注入 models 数组 (router.stats_snapshot 透出)
  - /gpu/set:
      * 单模型 (仅 main 加载) → 走老 load_model
      * 多模型 (main + 副) → 遍历 release_all_models + load_model_into_slot
      * 无模型 → 仅写设备配置

测试策略
========
  - 用 monkeypatch 把 mgr.load_model_into_slot / release_all_models / start_session
    替换为 stub, 直接验证调用参数, 不真的跑 GPU
  - 不走 TestClient (端点逻辑可直接 import 函数测试), 减少黑盒变量
  - VideoSourceManager 真实例 (channel_id=0) + router.stats_snapshot 真实跑
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from backend.api.source_inference_router import ModelInstance, Schedule
from backend.api.source_routes import (
    DetectionStartRequest,
    DeviceConfigRequest,
    ModelSpec,
    set_device,
    start_detection,
)


@pytest.fixture
def vsm(monkeypatch):
    """干净 VSM ch0; 把 channel_manager.get(0) 指向它"""
    from backend.api import channel_manager as cm_mod
    from backend.api.source import VideoSourceManager
    mgr = VideoSourceManager(channel_id=0)
    mgr.device = 'cpu'
    mgr.project_config = None  # 不触发 start_session

    def _fake_get(channel):
        assert channel == 0
        return mgr

    monkeypatch.setattr(cm_mod.channel_manager, 'get', _fake_get)
    monkeypatch.setattr('backend.api.source._get_mgr', _fake_get)
    monkeypatch.setattr('backend.api.source_routes._get_mgr', _fake_get)
    return mgr


@pytest.fixture
def stub_mgr_methods(vsm, monkeypatch):
    """把 mgr 上的真加载/启动方法替换为可观察 stub"""
    calls = {
        'load_model_into_slot': [],
        'release_all_models': 0,
        'load_model': [],
        'load_model_for_channel': [],
        'start_detection': [],
    }

    def _slot(name, model_path, **kwargs):
        calls['load_model_into_slot'].append({
            'name': name, 'model_path': model_path, **kwargs
        })
        return True

    def _release_all():
        calls['release_all_models'] += 1

    def _load(model_path):
        calls['load_model'].append(model_path)
        return True

    def _start(p):
        calls['start_detection'].append(p)

    vsm.load_model_into_slot = _slot
    vsm.release_all_models = _release_all
    vsm.load_model = _load
    vsm.start_detection = _start

    from backend.api import channel_manager as cm_mod

    def _load_for_ch(channel, mp, device):
        calls['load_model_for_channel'].append((channel, mp, device))
        return True

    monkeypatch.setattr(cm_mod.channel_manager, 'load_model_for_channel', _load_for_ch)
    monkeypatch.setattr(cm_mod.channel_manager, '_propagate_model', lambda ch: None)
    return calls


# ============================================================
# /detection/start: 老 payload 完全等价
# ============================================================
def test_start_detection_老_payload_走老路径(vsm, stub_mgr_methods):
    req = DetectionStartRequest(model_path='/tmp/main.pt', conf=0.3, iou=0.5)
    resp = start_detection(req, channel=0)

    assert resp['status'] == 'success'
    # 老路径: 不应调 release_all_models / load_model_into_slot
    assert stub_mgr_methods['release_all_models'] == 0
    assert len(stub_mgr_methods['load_model_into_slot']) == 0
    # 应调 channel_manager.load_model_for_channel
    assert len(stub_mgr_methods['load_model_for_channel']) == 1
    # mgr.start_detection(model_path) 被调用
    assert stub_mgr_methods['start_detection'] == ['/tmp/main.pt']
    # mgr.conf_threshold / iou_threshold 应该写入
    assert vsm.conf_threshold == 0.3
    assert vsm.iou_threshold == 0.5


def test_start_detection_老_payload_无_path_propagate(vsm, stub_mgr_methods):
    """model_path=None 且 mgr.model is None → 走 _propagate_model 兜底"""
    req = DetectionStartRequest(model_path=None, conf=0.25, iou=0.45)
    resp = start_detection(req, channel=0)

    assert resp['status'] == 'success'
    # 没有 path 不调 load_model_for_channel
    assert len(stub_mgr_methods['load_model_for_channel']) == 0
    assert stub_mgr_methods['start_detection'] == [None]


# ============================================================
# /detection/start: 新 models payload
# ============================================================
def test_start_detection_新_models_单_main(vsm, stub_mgr_methods):
    req = DetectionStartRequest(
        models=[ModelSpec(
            name='main', model_path='/tmp/main.pt', conf=0.3, iou=0.5,
            display_color='#10b981',
        )]
    )
    resp = start_detection(req, channel=0)

    assert resp['status'] == 'success'
    # 多模型路径必走 release_all_models 1 次 + load_model_into_slot 1 次
    assert stub_mgr_methods['release_all_models'] == 1
    assert len(stub_mgr_methods['load_model_into_slot']) == 1
    spec = stub_mgr_methods['load_model_into_slot'][0]
    assert spec['name'] == 'main'
    assert spec['model_path'] == '/tmp/main.pt'
    assert spec['conf'] == 0.3
    assert spec['iou'] == 0.5
    assert spec['display_color'] == '#10b981'
    # 老路径不应被触发
    assert len(stub_mgr_methods['load_model_for_channel']) == 0
    # start_detection 用 main 的 path
    assert stub_mgr_methods['start_detection'] == ['/tmp/main.pt']


def test_start_detection_新_models_多_slot(vsm, stub_mgr_methods):
    req = DetectionStartRequest(
        models=[
            ModelSpec(name='main', model_path='/tmp/main.pt', conf=0.3,
                      display_color='#10b981'),
            ModelSpec(name='tray', model_path='/tmp/tray.pt', conf=0.5,
                      roi=[[0.7, 0.7], [1.0, 1.0]],
                      schedule={'type': 'every_n_frames', 'n': 5},
                      class_filter=['tray_normal', 'tray_side'],
                      priority=50, display_color='#f59e0b'),
        ]
    )
    resp = start_detection(req, channel=0)
    assert resp['status'] == 'success'

    assert stub_mgr_methods['release_all_models'] == 1
    assert len(stub_mgr_methods['load_model_into_slot']) == 2
    names = [c['name'] for c in stub_mgr_methods['load_model_into_slot']]
    assert names == ['main', 'tray'], "顺序按 payload 顺序"

    tray_call = stub_mgr_methods['load_model_into_slot'][1]
    assert tray_call['conf'] == 0.5
    assert tray_call['roi'] == [[0.7, 0.7], [1.0, 1.0]]
    assert tray_call['schedule'] == {'type': 'every_n_frames', 'n': 5}
    assert tray_call['class_filter'] == ['tray_normal', 'tray_side']
    assert tray_call['priority'] == 50


def test_start_detection_新_models_只副_无_main(vsm, stub_mgr_methods):
    """req.models 没有 main → 用第一个 spec 的 path 作为 start_detection 参数"""
    req = DetectionStartRequest(
        models=[ModelSpec(name='tray', model_path='/tmp/tray.pt', conf=0.5)]
    )
    resp = start_detection(req, channel=0)
    assert resp['status'] == 'success'
    assert stub_mgr_methods['start_detection'] == ['/tmp/tray.pt']


def test_start_detection_load_失败_抛_500(vsm, stub_mgr_methods, monkeypatch):
    """有 spec load_model_into_slot 返回 False → HTTPException 500"""
    from fastapi import HTTPException

    def _slot_fail(name, model_path, **kwargs):
        return False  # tray 加载失败

    vsm.load_model_into_slot = _slot_fail

    req = DetectionStartRequest(
        models=[
            ModelSpec(name='main', model_path='/tmp/main.pt'),
            ModelSpec(name='tray', model_path='/tmp/tray.pt'),
        ]
    )

    with pytest.raises(HTTPException) as excinfo:
        start_detection(req, channel=0)
    assert excinfo.value.status_code == 500
    assert 'main' in excinfo.value.detail or 'tray' in excinfo.value.detail


# ============================================================
# /detection/results: models 数组注入
# ============================================================
def test_detection_results_注入_models_数组_单模型(vsm):
    """单模型场景, models 数组应至少有 main 一项"""
    from backend.api.source_routes import get_detection_results
    resp = get_detection_results(channel=0)

    assert 'models' in resp
    assert isinstance(resp['models'], list)
    assert len(resp['models']) >= 1
    main_entry = next((m for m in resp['models'] if m['name'] == 'main'), None)
    assert main_entry is not None
    assert main_entry['model_loaded'] is False  # 没真加载


def test_detection_results_注入_models_数组_多模型(vsm):
    """挂副 mi 后 models 数组应包含所有 slot"""
    tray = ModelInstance(name='tray', priority=50, conf=0.5,
                          display_color='#f59e0b',
                          schedule=Schedule(type='every_n_frames', n=5))
    vsm._router.add_model(tray)

    from backend.api.source_routes import get_detection_results
    resp = get_detection_results(channel=0)

    assert 'models' in resp
    names = [m['name'] for m in resp['models']]
    assert 'main' in names and 'tray' in names

    tray_entry = next(m for m in resp['models'] if m['name'] == 'tray')
    assert tray_entry['conf'] == 0.5
    assert tray_entry['display_color'] == '#f59e0b'
    assert tray_entry['schedule']['type'] == 'every_n_frames'
    assert tray_entry['schedule']['n'] == 5
    assert tray_entry['priority'] == 50


# ============================================================
# /gpu/set: 单模型 / 多模型 / 无模型 三分支
# ============================================================
@pytest.fixture
def stub_video_manager(monkeypatch):
    """patch source_routes.video_manager 为可观察 mock"""
    vm = MagicMock()
    vm.device = 'cuda:0'
    vm.model = None
    vm.model_path = None
    vm.current_device_info = {'name': 'CPU', 'device': 'cpu'}
    vm._router = MagicMock()
    vm._router.models = {}
    vm._save_device_config = lambda: None

    monkeypatch.setattr('backend.api.source_routes.video_manager', vm)
    return vm


def test_gpu_set_无模型_仅写配置(stub_video_manager):
    req = DeviceConfigRequest(device='cpu')
    resp = set_device(req)
    assert resp['status'] == 'success'
    assert '下次加载模型时生效' in resp['message']
    assert stub_video_manager.device == 'cpu'
    # 不应调 release_all_models / load_model
    stub_video_manager.release_all_models.assert_not_called()
    stub_video_manager.load_model.assert_not_called()


def test_gpu_set_单_main_走老_load_model(stub_video_manager):
    """仅 main 加载 → 走老 load_model 路径"""
    main = ModelInstance(name='main', priority=100)
    main.model = MagicMock()
    main.model_path = '/tmp/main.pt'
    stub_video_manager._router.models = {'main': main}

    stub_video_manager.model = main.model
    stub_video_manager.model_path = main.model_path
    stub_video_manager.load_model = MagicMock(return_value=True)

    req = DeviceConfigRequest(device='cuda:0')
    resp = set_device(req)
    assert resp['status'] == 'success'
    stub_video_manager.load_model.assert_called_once_with('/tmp/main.pt')


def test_gpu_set_多模型_走_遍历重载(stub_video_manager):
    """main + tray 都加载 → release_all_models + 遍历 load_model_into_slot"""
    main = ModelInstance(name='main', priority=100, conf=0.3,
                          display_color='#10b981')
    main.model = MagicMock()
    main.model_path = '/tmp/main.pt'

    tray = ModelInstance(name='tray', priority=50, conf=0.5,
                          schedule=Schedule(type='every_n_frames', n=5),
                          class_filter={'a', 'b'},
                          display_color='#f59e0b')
    tray.model = MagicMock()
    tray.model_path = '/tmp/tray.pt'

    stub_video_manager._router.models = {'main': main, 'tray': tray}
    stub_video_manager.load_model_into_slot = MagicMock(return_value=True)

    req = DeviceConfigRequest(device='cuda:1')
    resp = set_device(req)

    assert resp['status'] == 'success'
    assert resp.get('reloaded_models') == ['main', 'tray']
    stub_video_manager.release_all_models.assert_called_once()
    assert stub_video_manager.load_model_into_slot.call_count == 2

    # 检查第一个调用 (main) 的参数
    main_call = stub_video_manager.load_model_into_slot.call_args_list[0]
    assert main_call.kwargs['name'] == 'main'
    assert main_call.kwargs['model_path'] == '/tmp/main.pt'
    assert main_call.kwargs['device'] == 'cuda:1'
    assert main_call.kwargs['conf'] == 0.3
    assert main_call.kwargs['display_color'] == '#10b981'

    # 检查第二个调用 (tray) 的参数
    tray_call = stub_video_manager.load_model_into_slot.call_args_list[1]
    assert tray_call.kwargs['name'] == 'tray'
    assert tray_call.kwargs['conf'] == 0.5
    assert tray_call.kwargs['schedule'] == {'type': 'every_n_frames', 'n': 5,
                                              'events': []}
    assert tray_call.kwargs['class_filter'] == ['a', 'b']


def test_gpu_set_仅副模型_也走遍历(stub_video_manager):
    """仅副 mi 加载 (main 未加载) → 也走多模型路径"""
    tray = ModelInstance(name='tray', priority=50)
    tray.model = MagicMock()
    tray.model_path = '/tmp/tray.pt'
    stub_video_manager._router.models = {
        'main': ModelInstance(name='main', priority=100),  # main 未加载
        'tray': tray,
    }
    stub_video_manager.load_model_into_slot = MagicMock(return_value=True)
    stub_video_manager.model = None  # main 未加载

    req = DeviceConfigRequest(device='cpu')
    resp = set_device(req)
    assert resp['status'] == 'success'
    stub_video_manager.release_all_models.assert_called_once()
    assert stub_video_manager.load_model_into_slot.call_count == 1


def test_gpu_set_多模型_部分失败_返回_error(stub_video_manager):
    main = ModelInstance(name='main', priority=100)
    main.model = MagicMock()
    main.model_path = '/tmp/main.pt'
    tray = ModelInstance(name='tray', priority=50)
    tray.model = MagicMock()
    tray.model_path = '/tmp/tray.pt'
    stub_video_manager._router.models = {'main': main, 'tray': tray}
    stub_video_manager.load_model_into_slot = MagicMock(side_effect=[True, False])

    resp = set_device(DeviceConfigRequest(device='cpu'))
    assert resp['status'] == 'error'
    assert '部分模型重载出错' in resp['message']
