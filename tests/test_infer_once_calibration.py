"""v3.42.0 标定用单帧推理 (infer_once_for_calibration) 单元测试.

背景: 「检测中锁菜单」×「停止/待机清空实时检测结果」叠加, 打包版里项目页
「从当前画面抓取锚点框」无路可走。修复 = 检测未运行时对当前显示帧现推一帧。

覆盖闭环:
  1. 检测运行中但无模型 (synthetic 剧本) → 透传实时检测结果
  2. 未检测 + 模型未加载 → RuntimeError (提示先启动一次检测)
  3. 有模型但无画面帧 → RuntimeError (提示先启动视频源)
  4. 有帧+有模型 (停止/待机/运行中均同) → 真跑单帧推理, 返回归一化坐标
     detections, 且不发布到实时结果、不触碰状态机 (current_detections 不动)
  5. 不过启用步骤过滤: 模型输出的标签即使不在任何步骤/拆分配置里也原样返回
     (锚点标签通常不是步骤, 实时结果里本来就看不到它 —— 这是本方法与
     _detect_only 的刻意差异, 也是「检测中抓取也走现推」的原因)
"""
import os
from unittest.mock import MagicMock

import numpy as np
import pytest

os.environ.setdefault('OPENCV_FFMPEG_CAPTURE_OPTIONS', 'threads;1')
os.environ['BACKEND_SKIP_INIT'] = '1'

from backend.api.source import VideoSourceManager  # noqa: E402


def _fake_model(boxes_spec, names):
    """构造 ultralytics 形状的假模型.

    boxes_spec: [(x1, y1, x2, y2, conf, cls_id), ...] 像素坐标
    names: {cls_id: label}
    """
    fake_boxes = []
    for (x1, y1, x2, y2, conf, cls_id) in boxes_spec:
        box = MagicMock()
        xyxy0 = MagicMock()
        xyxy0.cpu.return_value.numpy.return_value = np.array([x1, y1, x2, y2])
        box.xyxy = [xyxy0]
        conf0 = MagicMock()
        conf0.cpu.return_value.numpy.return_value = np.float32(conf)
        box.conf = [conf0]
        cls0 = MagicMock()
        cls0.cpu.return_value.numpy.return_value = np.float32(cls_id)
        box.cls = [cls0]
        fake_boxes.append(box)

    result = MagicMock()
    result.boxes = fake_boxes

    model = MagicMock()
    model.names = names
    model.predict.return_value = iter([result])
    return model


@pytest.fixture
def vsm():
    m = VideoSourceManager(channel_id=0)
    m.is_detecting = False
    return m


class TestInferOnceCalibration:

    def test_live_no_model_passthrough(self, vsm):
        """检测运行中且无模型 (synthetic 剧本) → 返回实时结果副本."""
        vsm.is_detecting = True
        vsm.model = None
        vsm.current_detections = [{'label': '工件', 'confidence': 0.9,
                                   'x': 0.1, 'y': 0.2, 'w': 0.3, 'h': 0.4}]
        out = vsm.infer_once_for_calibration()
        assert out == vsm.current_detections
        assert out is not vsm.current_detections, '应返回副本'

    def test_no_model_raises(self, vsm):
        """未检测 + 模型未加载 → RuntimeError 提示先启动检测."""
        vsm.current_frame = np.zeros((480, 640, 3), dtype=np.uint8)
        vsm.model = None
        with pytest.raises(RuntimeError, match='模型未加载'):
            vsm.infer_once_for_calibration()

    def test_no_frame_raises(self, vsm):
        """有模型但无画面帧 → RuntimeError 提示先启动视频源."""
        vsm.current_frame = None
        vsm.model = _fake_model([], names={})
        with pytest.raises(RuntimeError, match='画面帧'):
            vsm.infer_once_for_calibration()

    def test_one_shot_inference_normalized_no_side_effects(self, vsm):
        """停止态单帧推理: 归一化坐标正确 + 不发布到实时结果."""
        vsm.current_frame = np.zeros((480, 640, 3), dtype=np.uint8)
        # 160x120@(64,48) 的框 → 归一化 x=0.1 y=0.1 w=0.25 h=0.25
        vsm.model = _fake_model(
            [(64, 48, 224, 168, 0.91, 0)], names={0: '工件'})
        out = vsm.infer_once_for_calibration()
        assert len(out) == 1
        det = out[0]
        assert det['label'] == '工件'
        assert abs(det['x'] - 0.1) < 1e-3
        assert abs(det['y'] - 0.1) < 1e-3
        assert abs(det['w'] - 0.25) < 1e-3
        assert abs(det['h'] - 0.25) < 1e-3
        assert abs(det['confidence'] - 0.91) < 1e-3
        # 无副作用: 实时结果仍为空, 不影响状态机入口
        assert vsm.current_detections == []

    def test_no_enabled_labels_filter(self, vsm):
        """刻意差异: 标签未被任何步骤/拆分配置引用也要返回 (首次标定场景)."""
        vsm.current_frame = np.zeros((480, 640, 3), dtype=np.uint8)
        vsm.project_config = {
            'steps_config': [{'label': '打螺丝', 'enabled': True}],
            'pipeline_config': {},
        }
        # 「工件」不在任何步骤里 —— _detect_only 会丢, infer_once 必须保留
        vsm.model = _fake_model(
            [(0, 0, 320, 240, 0.88, 5)], names={5: '工件'})
        out = vsm.infer_once_for_calibration()
        assert [d['label'] for d in out] == ['工件']


class TestInferOnceRoute:
    """/source/detection/infer-once 路由层: 成功透传 + RuntimeError → 400。"""

    def _patch_mgr(self, monkeypatch, mgr):
        from backend.api import channel_manager as cm_mod
        monkeypatch.setattr(cm_mod.channel_manager, 'get', lambda ch: mgr)

    def test_route_success(self, vsm, monkeypatch):
        from backend.api.source_routes import detection_infer_once
        vsm.current_frame = np.zeros((480, 640, 3), dtype=np.uint8)
        vsm.model = _fake_model([(64, 48, 224, 168, 0.91, 0)], names={0: '工件'})
        self._patch_mgr(monkeypatch, vsm)
        resp = detection_infer_once(channel=0)
        assert resp['status'] == 'success' and resp['mode'] == 'one_shot'
        assert [d['label'] for d in resp['detections']] == ['工件']

    def test_route_no_model_400(self, vsm, monkeypatch):
        from fastapi import HTTPException
        from backend.api.source_routes import detection_infer_once
        vsm.current_frame = np.zeros((480, 640, 3), dtype=np.uint8)
        vsm.model = None
        self._patch_mgr(monkeypatch, vsm)
        with pytest.raises(HTTPException) as ei:
            detection_infer_once(channel=0)
        assert ei.value.status_code == 400
        assert '模型未加载' in ei.value.detail
