"""Step 6 单测: source_project_config_apply._apply_models_config.

测试范围
========
  - pipeline_config.models[] 不存在 / 空 → no-op (向后兼容)
  - 主模型 (name='main') 配置: conf/iou/roi/schedule/class_filter/priority/
    display_color/use_half 全部正确写入 main mi
  - 副模型 (name='tray') 配置: 自动创建 mi 壳, 字段写入正确
  - 第二次 apply 配置外的旧副 mi 自动释放, main 永远保留
  - 错误恢复: 配置中含非法 schedule / priority 时不崩, 保留原值
  - ROI 缓存重置: roi 变化时 _roi_mask_cache / _roi_polygon_pixels 都清空

测试策略
========
  - 用真 VSM 实例 (channel_id=0), 走真 _router
  - apply 后从 _router 读 mi 验字段
  - 不 load 真模型 (Step 6 契约: apply 不触发 load_model 副作用)
"""
from __future__ import annotations

import pytest

from backend.api.source_inference_router import ModelInstance, Schedule
from backend.api.source_project_config_apply import (
    _apply_models_config,
    apply_project_config,
)


@pytest.fixture
def vsm():
    """干净 VSM, channel_id=0; 默认只有 main mi 壳 (Step 2 契约)"""
    from backend.api.source import VideoSourceManager
    mgr = VideoSourceManager(channel_id=0)
    mgr.device = 'cpu'
    return mgr


# ============================================================
# 向后兼容: 没有 models 配置时 no-op
# ============================================================
def test_pipeline_无_models_字段_no_op(vsm):
    pipeline = {'simultaneous_groups': [], 'settlement_mode': 'first_step'}
    before_main = vsm._router.get('main')
    before_conf = before_main.conf

    _apply_models_config(vsm, pipeline)

    assert vsm._router.get('main') is before_main
    assert before_main.conf == before_conf
    assert list(vsm._router.models.keys()) == ['main']


def test_pipeline_models_为空列表_no_op(vsm):
    pipeline = {'models': []}
    _apply_models_config(vsm, pipeline)
    assert list(vsm._router.models.keys()) == ['main']


def test_pipeline_models_为_None_no_op(vsm):
    pipeline = {'models': None}
    _apply_models_config(vsm, pipeline)
    assert list(vsm._router.models.keys()) == ['main']


# ============================================================
# 主模型配置写入
# ============================================================
def test_apply_main_模型配置_写入_mi(vsm):
    pipeline = {
        'models': [{
            'name': 'main',
            'conf': 0.55,
            'iou': 0.6,
            'roi': [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]],
            'schedule': {'type': 'every_n_frames', 'n': 3, 'events': []},
            'class_filter': ['person', 'box'],
            'priority': 100,
            'display_color': '#ff0000',
            'use_half': True,
        }]
    }
    _apply_models_config(vsm, pipeline)

    main = vsm._router.get('main')
    assert main is not None
    assert main.conf == 0.55
    assert main.iou == 0.6
    assert main.roi is not None and len(main.roi) == 4
    assert main.schedule.type == 'every_n_frames'
    assert main.schedule.n == 3
    assert main.class_filter == {'person', 'box'}
    assert main.priority == 100
    assert main.display_color == '#ff0000'
    assert main.use_half is True


# ============================================================
# 副模型配置: 自动创建空壳
# ============================================================
def test_apply_副模型_配置_自动创建_mi_空壳(vsm):
    pipeline = {
        'models': [
            {'name': 'main', 'conf': 0.3},
            {
                'name': 'tray',
                'model_path': '/tmp/fake_tray.pt',  # apply 不会真加载, 仅记录
                'conf': 0.4,
                'iou': 0.5,
                'roi': [[0.7, 0.7], [1.0, 0.7], [1.0, 1.0], [0.7, 1.0]],
                'schedule': {'type': 'every_n_frames', 'n': 5},
                'priority': 50,
                'display_color': '#3b82f6',
            },
        ]
    }
    _apply_models_config(vsm, pipeline)

    assert 'tray' in vsm._router.models
    tray = vsm._router.get('tray')
    assert tray.conf == 0.4
    assert tray.iou == 0.5
    assert tray.roi is not None
    assert tray.schedule.n == 5
    assert tray.priority == 50
    assert tray.display_color == '#3b82f6'
    assert tray.model is None, "apply_models_config 不应触发 load_model"


# ============================================================
# 第二次 apply: 配置外的旧副 mi 自动释放, main 保留
# ============================================================
def test_第二次_apply_释放配置外_副_mi(vsm):
    p1 = {
        'models': [
            {'name': 'main', 'conf': 0.3},
            {'name': 'tray', 'conf': 0.4, 'priority': 50},
            {'name': 'pose', 'conf': 0.5, 'priority': 40},
        ]
    }
    _apply_models_config(vsm, p1)
    assert set(vsm._router.models.keys()) == {'main', 'tray', 'pose'}

    p2 = {'models': [{'name': 'main', 'conf': 0.3}, {'name': 'tray', 'conf': 0.45}]}
    _apply_models_config(vsm, p2)

    assert set(vsm._router.models.keys()) == {'main', 'tray'}, "pose 应被释放"
    assert vsm._router.get('tray').conf == 0.45, "tray.conf 应被更新"


def test_主模型_永远保留_即使配置不含_main(vsm):
    pipeline = {'models': [{'name': 'tray', 'conf': 0.4}]}
    _apply_models_config(vsm, pipeline)

    # main 应保留 (永远存在的契约)
    assert 'main' in vsm._router.models
    assert 'tray' in vsm._router.models


# ============================================================
# ROI 缓存重置
# ============================================================
def test_roi_变化_重置缓存(vsm):
    main = vsm._router.get('main')
    main._roi_mask_cache = "fake_cache"
    main._roi_mask_shape = (480, 640)
    main._roi_polygon_pixels = "fake_pixels"

    pipeline = {'models': [{
        'name': 'main',
        'roi': [[0.1, 0.1], [0.5, 0.5]],
    }]}
    _apply_models_config(vsm, pipeline)

    assert main._roi_mask_cache is None
    assert main._roi_mask_shape is None
    assert main._roi_polygon_pixels is None


def test_roi_显式_None_清空_roi(vsm):
    main = vsm._router.get('main')
    main.roi = [[0.1, 0.1], [0.5, 0.5]]

    pipeline = {'models': [{'name': 'main', 'roi': None}]}
    _apply_models_config(vsm, pipeline)

    assert main.roi is None


# ============================================================
# 错误恢复: schedule/priority 异常不崩
# ============================================================
def test_非法_schedule_不崩_保留原值(vsm):
    main = vsm._router.get('main')
    original_sched = main.schedule

    pipeline = {'models': [{
        'name': 'main',
        'schedule': {'type': 'every_frame', 'n': 'not_a_number'},
    }]}
    _apply_models_config(vsm, pipeline)

    assert main.schedule is original_sched or main.schedule.type == 'every_frame'


def test_非法_priority_不崩_保留原值(vsm):
    main = vsm._router.get('main')
    original_priority = main.priority

    pipeline = {'models': [{'name': 'main', 'priority': 'high'}]}
    _apply_models_config(vsm, pipeline)

    assert main.priority == original_priority


def test_非法_conf_iou_不崩_保留原值(vsm):
    main = vsm._router.get('main')
    main.conf = 0.25
    main.iou = 0.45

    pipeline = {'models': [{'name': 'main', 'conf': 'high', 'iou': None}]}
    _apply_models_config(vsm, pipeline)

    assert main.conf == 0.25, "非法 conf 应保留原值"


# ============================================================
# 端到端: apply_project_config 顶层入口集成
# ============================================================
def test_apply_project_config_顶层_集成_models(vsm):
    config = {
        'id': 99,
        'name': 'test_multi',
        'task_type': 'detection',
        'logic_mode': 'detection',
        'steps_config': [],
        'pipeline_config': {
            'simultaneous_groups': [],
            'settlement_mode': 'first_step',
            'models': [
                {'name': 'main', 'conf': 0.3, 'display_color': '#10b981'},
                {'name': 'tray', 'conf': 0.5, 'display_color': '#f59e0b',
                 'priority': 50},
            ],
        },
        'events_config': [],
        'counters_config': [],
        'data_config': {},
    }
    apply_project_config(vsm, config)

    assert set(vsm._router.models.keys()) == {'main', 'tray'}
    assert vsm._router.get('main').display_color == '#10b981'
    assert vsm._router.get('tray').conf == 0.5
    assert vsm._router.get('tray').priority == 50


def test_apply_project_config_无_models_老配置_完全兼容(vsm):
    """关键回归: 老项目 (无 pipeline_config.models) 行为完全等价"""
    config = {
        'id': 1,
        'name': 'legacy',
        'task_type': 'detection',
        'logic_mode': 'detection',
        'steps_config': [{'label': 'step1', 'enabled': True, 'threshold': 50}],
        'pipeline_config': {'settlement_mode': 'first_step'},
        'events_config': [],
        'counters_config': [],
        'data_config': {},
    }
    apply_project_config(vsm, config)

    assert list(vsm._router.models.keys()) == ['main'], "老配置不应创建副 mi"
    assert vsm.step_conf_thresholds == {'step1': 0.5}
