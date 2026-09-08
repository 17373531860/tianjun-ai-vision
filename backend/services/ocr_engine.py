# -*- coding: utf-8 -*-
"""OCR 读字引擎 (2026-09 全量批次) — 序列号/铭牌/工单/屏幕文字识别。

定位: 检测模型只会画框不会认字, OCR 是与检测并列的新工种 (调研点 1-4)。
本服务是主程序原生基础设施 (多客户通用), 引擎选型 rapidocr_onnxruntime:
  - Apache-2.0 (可商用分发), PP-OCR 系模型内嵌在 pip 包内 → 可直接进离线安装包;
  - onnxruntime 后端, CPU 即可跑, 不抢检测 GPU。

设计:
  - 懒加载单例: 首次调用才初始化 (模型加载 ~1s), 启动零开销;
  - 依赖缺失优雅降级: is_available() 探测, read_text 抛 OcrUnavailable
    带安装指引, 不拖垮主程序 (错误隔离底线);
  - 线程安全: 初始化互斥锁; RapidOCR 推理本身单实例串行调用
    (识别锁), OCR 是低频操作 (扫铭牌/演示), 串行足够。

坐标口径: 返回 box 为归一化多边形 [[x,y]×4] (相对整幅输入图), 与区域事件/
ROI 一致; 传入 roi=[x,y,w,h] (归一化) 时先裁剪再识别, box 仍换算回整图坐标。
"""
from __future__ import annotations

import threading
from typing import Optional

import numpy as np

_engine = None
_engine_err: Optional[str] = None
_lock = threading.Lock()


class OcrUnavailable(RuntimeError):
    """OCR 引擎不可用 (依赖缺失/初始化失败)。message 带处置指引。"""


def is_available() -> bool:
    """依赖是否可用 (不触发模型加载)。"""
    try:
        import rapidocr_onnxruntime  # noqa: F401
        return True
    except Exception:
        return False


def _get_engine():
    global _engine, _engine_err
    if _engine is not None:
        return _engine
    with _lock:
        if _engine is not None:
            return _engine
        if _engine_err is not None:
            raise OcrUnavailable(_engine_err)
        try:
            from rapidocr_onnxruntime import RapidOCR
            _engine = RapidOCR()
            print("[OCR] rapidocr_onnxruntime 引擎初始化完成")
            return _engine
        except Exception as e:
            _engine_err = (f"OCR 引擎初始化失败: {e}; 请确认已安装 "
                           f"rapidocr_onnxruntime (pip install rapidocr_onnxruntime)")
            raise OcrUnavailable(_engine_err) from e


def engine_status() -> dict:
    """状态探针 (前端/运维消费)。"""
    return {
        "available": is_available(),
        "engine": "rapidocr_onnxruntime",
        "loaded": _engine is not None,
        "error": _engine_err,
    }


def read_text(image_bgr: np.ndarray, roi: Optional[list] = None,
              min_score: float = 0.5) -> list:
    """识别图中文字。

    Args:
        image_bgr: BGR ndarray (cv2 帧口径)。
        roi: 可选 [x, y, w, h] 归一化矩形, 先裁剪再识别 (找区域+读字范式)。
        min_score: 置信度门槛, 低于丢弃。

    Returns:
        [{'text': str, 'score': float, 'box': [[x,y]×4] 整图归一化}, ...]
        按画面从上到下排序。
    """
    if image_bgr is None or getattr(image_bgr, "size", 0) == 0:
        raise ValueError("输入图像为空")
    h0, w0 = image_bgr.shape[:2]

    off_x = off_y = 0
    img = image_bgr
    if roi:
        x, y, w, h = [float(v) for v in roi[:4]]
        x1 = max(0, min(w0 - 1, int(round(x * w0))))
        y1 = max(0, min(h0 - 1, int(round(y * h0))))
        x2 = max(x1 + 1, min(w0, int(round((x + w) * w0))))
        y2 = max(y1 + 1, min(h0, int(round((y + h) * h0))))
        img = image_bgr[y1:y2, x1:x2]
        off_x, off_y = x1, y1

    eng = _get_engine()
    with _lock:  # RapidOCR 单实例串行 (低频操作, 串行足够)
        result, _elapse = eng(img)

    out = []
    for item in result or []:
        box_px, text, score = item[0], item[1], float(item[2])
        if score < min_score or not str(text).strip():
            continue
        box = [[(float(px) + off_x) / w0, (float(py) + off_y) / h0]
               for px, py in box_px]
        out.append({"text": str(text), "score": round(score, 4), "box": box})
    out.sort(key=lambda r: min(p[1] for p in r["box"]) if r["box"] else 0)
    return out
