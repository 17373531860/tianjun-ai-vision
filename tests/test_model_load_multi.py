"""Step 3 单测: ModelLoadMixin 多模型加载 / 双向同步桥 / 多 slot 释放。

验证点
======
  - 老签名 `load_model(path)` 仍 100% 兼容, 末尾自动镜像到 main slot
  - `_release_model()` 老接口保持原行为, 末尾自动同步 main slot
  - 新签名 `load_model_into_slot(name, path, **kwargs)`:
      * name='main' 时双向同步到 host 老字段
      * name != 'main' 时不动 host 老字段, 副 slot 独立
      * mi 不存在时自动创建
      * 配置参数 (conf/iou/roi/schedule/class_filter/priority/display_color/use_half) 写入 mi
  - `_release_model_from(mi)`: 只清指定 slot, 其他不动
  - `release_all_models()`: 清所有 slot, 但壳保留
  - 双向同步桥:
      * _mirror_host_to_main: host → main
      * _mirror_main_to_host: main → host
  - 加载失败时 host + main 都被清空

测试策略
========
  - 不依赖真实 ultralytics/GPU: mock YOLO 类 + 强制 CPU 设备路径
  - 用 monkeypatch.setitem(sys.modules, 'ultralytics', fake) 注入 mock
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest


# ============================================================
# 公共 fixture: mock ultralytics.YOLO + 构造 VSM
# ============================================================
@pytest.fixture
def mock_yolo(monkeypatch):
    """让 `from ultralytics import YOLO` 返回 MagicMock 类"""
    fake_ultralytics = MagicMock()

    def _make_instance(model_path, *args, **kwargs):
        inst = MagicMock()
        inst.task = 'detect'
        inst.names = {0: 'class_a', 1: 'class_b'}
        inst.overrides = {'imgsz': 640}
        inst.predictor = None
        # 标记一下来源, 测试可以查
        inst._test_loaded_from = model_path
        return inst

    fake_yolo_class = MagicMock(side_effect=_make_instance)
    fake_ultralytics.YOLO = fake_yolo_class
    monkeypatch.setitem(sys.modules, 'ultralytics', fake_ultralytics)
    return fake_yolo_class


@pytest.fixture
def vsm():
    """构造一个独立的 VSM 实例 (强制 CPU 设备避开 cuda warmup)"""
    from backend.api.source import VideoSourceManager
    mgr = VideoSourceManager(channel_id=0)
    mgr.device = 'cpu'  # 跳过 GPU warmup 路径
    return mgr


# ============================================================
# 老签名: load_model(path)
# ============================================================
def test_load_model_老签名_成功_main_镜像同步(vsm, mock_yolo):
    ok = vsm.load_model('/fake/main_model.pt')
    assert ok is True

    # host 老字段填好了
    assert vsm.model is not None
    assert vsm.model_path == '/fake/main_model.pt'
    assert vsm.model_task == 'detect'
    assert vsm._is_native_pytorch is True
    assert vsm.current_device_info == {'type': 'CPU', 'name': 'CPU', 'device': 'cpu'}

    # main slot 也有相同值 (镜像)
    main = vsm.models['main']
    assert main.model is vsm.model
    assert main.model_path == '/fake/main_model.pt'
    assert main.model_task == 'detect'
    assert main._is_native_pytorch is True
    assert main.current_device_info == {'type': 'CPU', 'name': 'CPU', 'device': 'cpu'}


def test_load_model_老签名_失败时_main_也清空(vsm, monkeypatch):
    """YOLO 构造抛异常 → host + main 都为 None"""
    fake_ultralytics = MagicMock()
    fake_ultralytics.YOLO = MagicMock(side_effect=Exception("模型文件不存在"))
    monkeypatch.setitem(sys.modules, 'ultralytics', fake_ultralytics)

    ok = vsm.load_model('/nonexistent.pt')
    assert ok is False
    assert vsm.model is None
    assert vsm.current_device_info is None
    assert vsm.models['main'].model is None
    assert vsm.models['main'].current_device_info is None


def test_load_model_老签名_engine_后缀_走非native路径(vsm, mock_yolo):
    """.engine 模型 → _is_native_pytorch=False, _original_pt_path 不被设"""
    vsm.load_model('/fake/m.engine')
    assert vsm._is_native_pytorch is False
    assert vsm.models['main']._is_native_pytorch is False
    # _original_pt_path 仅当 path 是 .pt/.pth 才会被自动设
    assert vsm._original_pt_path is None


def test_load_model_老签名_engine_显式传_pt_fallback路径(vsm, mock_yolo):
    """.engine 加载时显式传 original_pt_path → 记录用于回退"""
    vsm.load_model('/fake/m.engine', original_pt_path='/fake/m.pt')
    assert vsm._original_pt_path == '/fake/m.pt'
    assert vsm.models['main']._original_pt_path == '/fake/m.pt'


# ============================================================
# 老签名: _release_model
# ============================================================
def test_release_model_老签名_main_同步清空(vsm, mock_yolo):
    vsm.load_model('/fake/m.pt')
    assert vsm.model is not None
    assert vsm.models['main'].model is not None

    vsm._release_model()
    assert vsm.model is None
    assert vsm.model_task == 'detect'
    assert vsm.models['main'].model is None
    assert vsm.models['main'].model_task == 'detect'


def test_release_model_空状态_不爆():
    """没加载过模型时 release 也不会 crash"""
    from backend.api.source import VideoSourceManager
    mgr = VideoSourceManager(channel_id=0)
    mgr.device = 'cpu'
    mgr._release_model()  # 不应抛异常
    assert mgr.model is None
    assert mgr.models['main'].model is None


# ============================================================
# 新签名: load_model_into_slot
# ============================================================
def test_load_model_into_slot_main_双向同步(vsm, mock_yolo):
    ok = vsm.load_model_into_slot('main', '/fake/main.pt', conf=0.3, iou=0.5)
    assert ok is True

    main = vsm.models['main']
    assert main.model is not None
    assert main.model_path == '/fake/main.pt'
    assert main.conf == 0.3
    assert main.iou == 0.5

    # host 老字段同步了 (因为 name=='main')
    assert vsm.model is main.model
    assert vsm.model_path == '/fake/main.pt'


def test_load_model_into_slot_副模型_不动host(vsm, mock_yolo):
    """name != 'main' 时, host 老字段保持原样"""
    assert vsm.model is None
    assert vsm.model_path is None

    ok = vsm.load_model_into_slot('tray', '/fake/tray.pt', conf=0.4, iou=0.6,
                                   priority=80, display_color='#ff0000')
    assert ok is True

    # tray slot 有值
    tray = vsm.models['tray']
    assert tray.model is not None
    assert tray.model_path == '/fake/tray.pt'
    assert tray.conf == 0.4
    assert tray.iou == 0.6
    assert tray.priority == 80
    assert tray.display_color == '#ff0000'

    # host 老字段没动
    assert vsm.model is None
    assert vsm.model_path is None
    assert vsm.models['main'].model is None  # main 也没动


def test_load_model_into_slot_自动创建_mi(vsm, mock_yolo):
    """slot 不存在时自动创建"""
    assert 'tray' not in vsm.models
    vsm.load_model_into_slot('tray', '/fake/tray.pt')
    assert 'tray' in vsm.models
    assert vsm.models['tray'].name == 'tray'
    # 默认 priority=50 (副模型), main 是 100
    assert vsm.models['tray'].priority == 50


def test_load_model_into_slot_配置参数_写入_mi(vsm, mock_yolo):
    """所有可选参数都正确写入 mi"""
    vsm.load_model_into_slot(
        'qc', '/fake/qc.pt',
        conf=0.45,
        iou=0.55,
        roi=[[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]],
        schedule={'type': 'on_event', 'n': 1, 'events': ['cycle_end']},
        class_filter=['ng', 'ok'],
        priority=30,
        display_color='#00aaff',
        use_half=True,
    )
    qc = vsm.models['qc']
    assert qc.conf == 0.45
    assert qc.iou == 0.55
    assert qc.roi == [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]]
    assert qc.schedule.type == 'on_event'
    assert qc.schedule.events == ['cycle_end']
    assert qc.class_filter == {'ng', 'ok'}
    assert qc.priority == 30
    assert qc.display_color == '#00aaff'
    assert qc.use_half is True


def test_load_model_into_slot_schedule_是_Schedule_对象(vsm, mock_yolo):
    """schedule 也接受已构造的 Schedule 实例"""
    from backend.api.source_inference_router import Schedule
    sched = Schedule(type='every_n_frames', n=10)
    vsm.load_model_into_slot('aux', '/fake/aux.pt', schedule=sched)
    assert vsm.models['aux'].schedule is sched


def test_load_model_into_slot_schedule_类型错误_报错(vsm, mock_yolo):
    with pytest.raises(TypeError, match="schedule 必须是 dict 或 Schedule"):
        vsm.load_model_into_slot('aux', '/fake/aux.pt', schedule="every_frame")


# ============================================================
# 新签名: _release_model_from / release_all_models
# ============================================================
def test_release_model_from_单slot_只动指定(vsm, mock_yolo):
    vsm.load_model('/fake/main.pt')
    vsm.load_model_into_slot('tray', '/fake/tray.pt')

    assert vsm.models['main'].model is not None
    assert vsm.models['tray'].model is not None

    vsm._release_model_from(vsm.models['tray'])

    # tray 清空
    assert vsm.models['tray'].model is None
    # main 不动
    assert vsm.models['main'].model is not None
    assert vsm.model is not None  # host 也不动


def test_release_model_from_main_同步到host(vsm, mock_yolo):
    vsm.load_model('/fake/main.pt')
    vsm.load_model_into_slot('tray', '/fake/tray.pt')

    vsm._release_model_from(vsm.models['main'])

    assert vsm.models['main'].model is None
    assert vsm.model is None  # host 同步清空了
    # tray 不动
    assert vsm.models['tray'].model is not None


def test_release_all_models_清空所有slot(vsm, mock_yolo):
    vsm.load_model('/fake/main.pt')
    vsm.load_model_into_slot('tray', '/fake/tray.pt')
    vsm.load_model_into_slot('qc', '/fake/qc.pt')

    vsm.release_all_models()

    for name in ('main', 'tray', 'qc'):
        assert vsm.models[name].model is None
    assert vsm.model is None  # host 同步清空


def test_release_all_models_保留_mi壳(vsm, mock_yolo):
    """release_all_models 不应删除 ModelInstance 壳, 只清 model 字段"""
    vsm.load_model_into_slot('tray', '/fake/tray.pt')
    assert 'tray' in vsm.models

    vsm.release_all_models()

    # 壳保留, model 清空
    assert 'tray' in vsm.models
    assert vsm.models['tray'].name == 'tray'
    assert vsm.models['tray'].model is None
    assert 'main' in vsm.models  # main 永远存在的契约


# ============================================================
# 双向同步桥 (单测)
# ============================================================
def test_mirror_host_to_main_镜像七字段(vsm):
    """直接调 _mirror_host_to_main, 验证所有镜像字段"""
    vsm.model = "fake_model"
    vsm.model_path = "/p.pt"
    vsm.model_task = "segment"
    vsm._original_pt_path = "/orig.pt"
    vsm._is_native_pytorch = False
    vsm._model_imgsz = 800
    vsm.current_device_info = {'type': 'GPU', 'name': 'RTX 3050', 'device': 'cuda:0'}
    vsm.use_half = True

    vsm._mirror_host_to_main()

    main = vsm.models['main']
    assert main.model == "fake_model"
    assert main.model_path == "/p.pt"
    assert main.model_task == "segment"
    assert main._original_pt_path == "/orig.pt"
    assert main._is_native_pytorch is False
    assert main._model_imgsz == 800
    assert main.current_device_info == {'type': 'GPU', 'name': 'RTX 3050', 'device': 'cuda:0'}
    assert main.use_half is True


def test_mirror_main_to_host_反向镜像(vsm):
    """直接改 main slot 后, mirror_main_to_host 把字段同步回 host"""
    main = vsm.models['main']
    main.model = "model_obj"
    main.model_path = "/q.engine"
    main.model_task = "detect"
    main._original_pt_path = "/q.pt"
    main._is_native_pytorch = False
    main._model_imgsz = 1024
    main.current_device_info = {'type': 'GPU', 'name': 'RTX 3050', 'device': 'cuda:0'}

    vsm._mirror_main_to_host()

    assert vsm.model == "model_obj"
    assert vsm.model_path == "/q.engine"
    assert vsm.model_task == "detect"
    assert vsm._original_pt_path == "/q.pt"
    assert vsm._is_native_pytorch is False
    assert vsm._model_imgsz == 1024
    assert vsm.current_device_info == {'type': 'GPU', 'name': 'RTX 3050', 'device': 'cuda:0'}


def test_mirror_无_router_时_不报错():
    """_mirror_host_to_main 在没 _router 时 (理论上不会) 也应静默"""
    from backend.api.source import VideoSourceManager
    mgr = VideoSourceManager(channel_id=0)
    # 暴力删 _router 模拟异常情况
    object.__delattr__(mgr, '_router')
    # 不应抛
    mgr._mirror_host_to_main()
    mgr._mirror_main_to_host()


# ============================================================
# bug 修复回归: _read_engine_metadata_imgsz 不再 NameError
# ============================================================
def test_engine_metadata_reader_可被调用_无NameError(vsm, mock_yolo):
    """旧代码漏 import 触发 NameError 静默退化, Step 3 修复后应能正常调"""
    from backend.api.source_model_load_mixin import _get_engine_metadata_imgsz_reader
    reader = _get_engine_metadata_imgsz_reader()
    # 是个 callable
    assert callable(reader)
    # 不存在的文件应返回 None (函数自己 try/except, 不抛)
    result = reader('/this/path/does/not/exist.engine')
    assert result is None


# ============================================================
# 加载多模型时副模型不污染 main + 反复加载稳定性
# ============================================================
def test_顺序加载_main_tray_qc_互不干扰(vsm, mock_yolo):
    vsm.load_model('/fake/main.pt')
    vsm.load_model_into_slot('tray', '/fake/tray.pt', conf=0.4)
    vsm.load_model_into_slot('qc', '/fake/qc.pt', conf=0.5)

    assert vsm.models['main'].model._test_loaded_from == '/fake/main.pt'
    assert vsm.models['tray'].model._test_loaded_from == '/fake/tray.pt'
    assert vsm.models['qc'].model._test_loaded_from == '/fake/qc.pt'

    # 各 slot 配置独立
    assert vsm.models['main'].conf == 0.25  # 默认
    assert vsm.models['tray'].conf == 0.4
    assert vsm.models['qc'].conf == 0.5


def test_重新加载_main_tray不被释放(vsm, mock_yolo):
    """主模型重新加载 (load_model 老接口) 不应误伤副模型 slot"""
    vsm.load_model('/fake/main_v1.pt')
    vsm.load_model_into_slot('tray', '/fake/tray.pt')
    tray_obj = vsm.models['tray'].model
    assert tray_obj is not None

    # 重新加载主模型 (会先释放 main, 但不应碰 tray)
    vsm.load_model('/fake/main_v2.pt')
    assert vsm.model._test_loaded_from == '/fake/main_v2.pt'
    assert vsm.models['tray'].model is tray_obj  # tray 完全没动
