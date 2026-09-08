# -*- coding: utf-8 -*-
"""端到端 (无 NMS) ONNX 模型直推 runner — 包契约 1.1 postprocess.mode=end_to_end.

背景 (2026-09 YOLO26 批次): YOLO26 / RF-DETR 形态的模型不再输出重叠候选框
再做类别 NMS, 而是直接输出最终框。老链路 (ultralytics YOLO + 内部 NMS) 对
这类模型语义是错的——重复抑制一遍已去重的框轻则丢框, 重则按错误 iou 语义
合并相邻目标。本模块为这类模型提供独立直推路径:

  - 训练平台 .yvmodel 入库时 (package_ingest), postprocess.mode=end_to_end 的
    模型会在产物旁写 sidecar 元数据 `<file>.tjmeta.json` (preprocess/labels);
  - 模型加载层 (source_model_load_mixin) 看到 sidecar 即换用 EndToEndOnnxModel,
    未见 sidecar 的模型 0 行为变化 (仍走 ultralytics YOLO);
  - EndToEndOnnxModel 对下游伪装 ultralytics 最小接口面 (predict/names/task/
    overrides/to), DetectRunnersMixin/_detect_only 等消费方零改动。

输出布局约定 (契约 1.1): 首输出 reshape 到 (N, 6+) 后逐行 [x1,y1,x2,y2,score,cls],
坐标为 letterbox 输入空间像素, 本层负责反 letterbox 回原图像素 (与 ultralytics
Results 坐标口径一致)。score < conf 过滤; 不做任何 NMS (这正是端到端的意义)。

限制: 跟踪模式 (model.track, 装箱清点等) 暂不支持端到端模型 —— ByteTrack 依赖
ultralytics Results 流水线; track() 抛出带明确指引的错误而不是静默错跑。

Context: predict 在推理线程池内被调用 (与 YOLO.predict 同一调用位置),
ORT session 线程安全, 无共享可变状态; 不持锁不做 IO。
"""
from __future__ import annotations

import json
import os

import numpy as np

_SIDECAR_SUFFIX = ".tjmeta.json"


def sidecar_path(model_path: str) -> str:
    """模型产物旁的元数据 sidecar 路径 (package_ingest 写入 / 加载层探测)。"""
    return model_path + _SIDECAR_SUFFIX


def maybe_load_end_to_end(model_path: str):
    """加载层入口: 有 end_to_end sidecar 时返回直推 runner, 否则 None (走老链路)。

    sidecar 损坏按"没有"处理并打日志——宁可回老链路显式报错 (YOLO 加载
    e2e ONNX 会在 predict 时暴露), 也不带着坏元数据静默推理。
    """
    sp = sidecar_path(model_path)
    if not os.path.isfile(sp):
        return None
    try:
        with open(sp, encoding="utf-8") as f:
            meta = json.load(f)
    except Exception as e:
        print(f"[E2E-ONNX] sidecar 解析失败 ({sp}): {e}, 回退 ultralytics 加载")
        return None
    if (meta.get("postprocess_mode") or "").lower() != "end_to_end":
        return None
    return EndToEndOnnxModel(model_path, meta)


class _NpView:
    """torch.Tensor 最小替身: 支持 [i] 索引 + .cpu().numpy() 链 (runner 消费口径)。"""

    __slots__ = ("_a",)

    def __init__(self, a):
        self._a = np.asarray(a)

    def __getitem__(self, i):
        return _NpView(self._a[i])

    def cpu(self):
        return self

    def numpy(self):
        return self._a

    def __float__(self):
        return float(self._a)

    def __int__(self):
        return int(self._a)


class _Box:
    """ultralytics Boxes 单框替身 (xyxy/conf/cls 均为 (1,·) 形状, 下游取 [0])。"""

    __slots__ = ("xyxy", "conf", "cls", "id")

    def __init__(self, xyxy: np.ndarray, conf: float, cls: int):
        self.xyxy = _NpView(xyxy[None, :])
        self.conf = _NpView(np.array([conf], dtype=np.float32))
        self.cls = _NpView(np.array([cls], dtype=np.float32))
        self.id = None  # 端到端直推不产 track id


class _Result:
    """ultralytics Results 替身: .boxes 可迭代 + .cpu() 自返回 (MPS 搬运兼容)。"""

    __slots__ = ("boxes",)

    def __init__(self, boxes: list):
        self.boxes = boxes

    def cpu(self):
        return self


class EndToEndOnnxModel:
    """端到端 ONNX 模型的 onnxruntime 直推封装 (无 NMS)。

    对下游伪装 ultralytics 最小接口: predict()/names/task/overrides/to()。
    imgsz 以 ONNX 静态输入形状为最权威, 其次 sidecar preprocess 声明, 再缺省 640。
    """

    task = "detect"

    def __init__(self, model_path: str, meta: dict):
        import onnxruntime as ort  # 缺依赖时在加载期显式失败, 不留空壳模型

        so = ort.SessionOptions()
        so.log_severity_level = 3
        providers = ["CPUExecutionProvider"]
        if "CUDAExecutionProvider" in ort.get_available_providers():
            providers.insert(0, "CUDAExecutionProvider")
        self.session = ort.InferenceSession(model_path, sess_options=so,
                                            providers=providers)
        inp = self.session.get_inputs()[0]
        self._input_name = inp.name

        pp = meta.get("preprocess") or {}
        lb = pp.get("letterbox") or {}
        imgsz = None
        shape = inp.shape
        if isinstance(shape, (list, tuple)) and len(shape) == 4:
            h, w = shape[2], shape[3]
            if isinstance(h, int) and isinstance(w, int):
                imgsz = max(h, w)  # 静态输入形状最权威
        if imgsz is None:
            try:
                imgsz = int(pp.get("imgsz") or lb.get("size") or 640)
            except (TypeError, ValueError):
                imgsz = 640
        self._imgsz = imgsz
        try:
            self._pad_value = int(lb.get("padValue", 114))
        except (TypeError, ValueError):
            self._pad_value = 114

        labels = meta.get("labels") or []
        self.names = {i: str(n) for i, n in enumerate(labels)}
        self.overrides = {"imgsz": imgsz}  # _detect_model_imgsz 消费口径
        self.model_path = model_path
        print(f"[E2E-ONNX] 端到端模型已加载 (无 NMS 直推): {model_path} "
              f"imgsz={imgsz} providers={self.session.get_providers()}")

    # ---------- ultralytics 接口面 ----------
    def to(self, device):
        """导出格式无 .to 语义 (与 YOLO 导出模型行为一致), no-op。"""
        return self

    def predict(self, frame: np.ndarray, conf: float = 0.25, **_kw) -> list:
        """单帧直推: letterbox → session.run → conf 过滤 → 反 letterbox 回原图像素。

        iou/half/device/stream 等参数按端到端语义忽略 (无 NMS; 设备由 ORT
        provider 决定); 返回 [_Result] 供 `for result in results` 消费。
        """
        blob, ratio, (pad_x, pad_y) = self._letterbox(frame)
        out = self.session.run(None, {self._input_name: blob})[0]
        dets = np.asarray(out, dtype=np.float32)
        if dets.ndim >= 2:
            dets = dets.reshape(-1, dets.shape[-1])
        else:
            dets = dets.reshape(0, 6)

        h0, w0 = frame.shape[:2]
        boxes = []
        for row in dets:
            if row.shape[0] < 6:
                continue
            x1, y1, x2, y2, score, cls = row[:6]
            if float(score) < conf:
                continue
            x1 = (x1 - pad_x) / ratio
            x2 = (x2 - pad_x) / ratio
            y1 = (y1 - pad_y) / ratio
            y2 = (y2 - pad_y) / ratio
            x1 = max(0.0, min(float(w0), float(x1)))
            x2 = max(0.0, min(float(w0), float(x2)))
            y1 = max(0.0, min(float(h0), float(y1)))
            y2 = max(0.0, min(float(h0), float(y2)))
            if x2 <= x1 or y2 <= y1:
                continue
            boxes.append(_Box(np.array([x1, y1, x2, y2], dtype=np.float32),
                              float(score), int(cls)))
        return [_Result(boxes)]

    def track(self, *_a, **_kw):
        raise RuntimeError(
            "端到端 (end_to_end) 模型暂不支持跟踪模式 (装箱清点/ByteTrack); "
            "跟踪场景请使用 class_nms 形态模型")

    # ---------- 预处理 ----------
    def _letterbox(self, frame: np.ndarray):
        """标准 YOLO letterbox (等比缩放 + 居中补边): 返回 (blob, ratio, (pad_x, pad_y))。"""
        import cv2

        h0, w0 = frame.shape[:2]
        s = self._imgsz
        ratio = min(s / h0, s / w0)
        nh, nw = int(round(h0 * ratio)), int(round(w0 * ratio))
        img = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_LINEAR)
        top = (s - nh) // 2
        left = (s - nw) // 2
        canvas = np.full((s, s, 3), self._pad_value, dtype=np.uint8)
        canvas[top:top + nh, left:left + nw] = img
        # BGR→RGB, HWC→NCHW, /255 (契约 1.1 letterbox 缺省口径)
        blob = canvas[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255.0
        return np.ascontiguousarray(blob), ratio, (left, top)
