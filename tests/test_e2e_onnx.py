# -*- coding: utf-8 -*-
"""端到端 (无 NMS) ONNX 直推 runner 单元测试 — backend/api/source_e2e_onnx.

用 onnx 构造一个常量输出的假模型 (输出布局 [1,N,6] = x1,y1,x2,y2,score,cls,
坐标在 letterbox 输入空间), 验证:
  - sidecar 探测: 无 sidecar / class_nms sidecar / 坏 sidecar → 回老链路 (None)
  - 解码: conf 过滤、反 letterbox 回原图像素、names 映射
  - 无 NMS: 两个高度重叠的框都保留 (端到端语义, 与 class_nms 的根本差异)
  - ultralytics 接口面: overrides.imgsz / task / to() / track() 明确拒绝
"""
from __future__ import annotations

import json

import numpy as np
import pytest

from backend.api.source_e2e_onnx import (
    EndToEndOnnxModel,
    maybe_load_end_to_end,
    sidecar_path,
)

# 常量输出: 3 个框 (letterbox 64x64 输入空间像素)
#   A (10,20,30,40) conf .9 cls 0  — 与 B 高度重叠
#   B (12,22,32,42) conf .8 cls 1  — class_nms 语义下会被 A 抑制, 端到端必须保留
#   C (50,10,60,20) conf .1 cls 0  — 低于 conf 门槛, 应被过滤
_DETS = np.array([[[10, 20, 30, 40, 0.9, 0],
                   [12, 22, 32, 42, 0.8, 1],
                   [50, 10, 60, 20, 0.1, 0]]], dtype=np.float32)


@pytest.fixture(scope="module")
def e2e_model_path(tmp_path_factory):
    """构造静态输入 [1,3,64,64]、常量检测输出的最小 ONNX 模型。"""
    import onnx
    from onnx import TensorProto, helper

    const_node = helper.make_node(
        "Constant", [], ["dets"],
        value=helper.make_tensor("dets_v", TensorProto.FLOAT,
                                 _DETS.shape, _DETS.flatten()))
    id_node = helper.make_node("Identity", ["images"], ["passthrough"])
    graph = helper.make_graph(
        [const_node, id_node], "e2e_test",
        [helper.make_tensor_value_info("images", TensorProto.FLOAT, [1, 3, 64, 64])],
        [helper.make_tensor_value_info("dets", TensorProto.FLOAT, list(_DETS.shape)),
         helper.make_tensor_value_info("passthrough", TensorProto.FLOAT, [1, 3, 64, 64])])
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)])
    model.ir_version = 10
    path = tmp_path_factory.mktemp("e2e") / "fake_e2e.onnx"
    onnx.save(model, str(path))
    return str(path)


def _write_sidecar(model_path, mode="end_to_end", labels=("工件", "缺陷")):
    with open(sidecar_path(model_path), "w", encoding="utf-8") as f:
        json.dump({"postprocess_mode": mode,
                   "preprocess": {"letterbox": {"padValue": 114}},
                   "labels": list(labels)}, f, ensure_ascii=False)


# ============================================================
# sidecar 探测 (加载层分流开关)
# ============================================================

def test_no_sidecar_returns_none(e2e_model_path):
    assert maybe_load_end_to_end(e2e_model_path) is None


def test_class_nms_sidecar_returns_none(e2e_model_path):
    _write_sidecar(e2e_model_path, mode="class_nms")
    assert maybe_load_end_to_end(e2e_model_path) is None


def test_broken_sidecar_returns_none(e2e_model_path):
    with open(sidecar_path(e2e_model_path), "w") as f:
        f.write("not a json {{{")
    assert maybe_load_end_to_end(e2e_model_path) is None


def test_e2e_sidecar_returns_runner(e2e_model_path):
    _write_sidecar(e2e_model_path)
    m = maybe_load_end_to_end(e2e_model_path)
    assert isinstance(m, EndToEndOnnxModel)
    # ultralytics 接口面
    assert m.task == "detect"
    assert m.names == {0: "工件", 1: "缺陷"}
    assert m.overrides["imgsz"] == 64  # 以 ONNX 静态输入形状为权威
    assert m.to("cpu") is m


# ============================================================
# 直推解码
# ============================================================

@pytest.fixture(scope="module")
def runner(e2e_model_path):
    _write_sidecar(e2e_model_path)
    return maybe_load_end_to_end(e2e_model_path)


def test_predict_decode_and_no_nms(runner):
    """32x64 原图 → letterbox 64x64 (ratio=1, pad_y=16): 反 letterbox y-16;
    重叠双框都保留 (无 NMS); 低 conf 框被过滤。"""
    frame = np.zeros((32, 64, 3), dtype=np.uint8)
    results = list(runner.predict(frame, conf=0.25, iou=0.45,
                                  imgsz=64, verbose=False, device="cpu",
                                  stream=True, half=False))
    assert len(results) == 1
    boxes = results[0].boxes
    assert len(boxes) == 2, "重叠双框应都保留 (端到端不做 NMS), 低 conf 框被过滤"

    # 消费口径与 DetectRunnersMixin._detect_only 完全一致
    x1, y1, x2, y2 = map(int, boxes[0].xyxy[0].cpu().numpy())
    assert (x1, y1, x2, y2) == (10, 4, 30, 24), "反 letterbox 应减去 pad_y=16"
    assert abs(float(boxes[0].conf[0].cpu().numpy()) - 0.9) < 1e-6
    assert int(boxes[0].cls[0].cpu().numpy()) == 0
    x1b, y1b, x2b, y2b = map(int, boxes[1].xyxy[0].cpu().numpy())
    assert (x1b, y1b, x2b, y2b) == (12, 6, 32, 26)
    assert int(boxes[1].cls[0].cpu().numpy()) == 1
    # MPS 搬运兼容: result.cpu() 自返回
    assert results[0].cpu() is results[0]


def test_predict_conf_gate(runner):
    frame = np.zeros((32, 64, 3), dtype=np.uint8)
    boxes = runner.predict(frame, conf=0.85)[0].boxes
    assert len(boxes) == 1 and int(boxes[0].cls[0].cpu().numpy()) == 0


def test_track_rejected_with_clear_message(runner):
    with pytest.raises(RuntimeError, match="端到端"):
        runner.track(np.zeros((32, 64, 3), dtype=np.uint8))
