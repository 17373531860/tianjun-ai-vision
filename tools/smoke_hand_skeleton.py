"""二段 pipeline 集成冒烟 (feat/hand-skeleton).

不启动后端, 直接构造 VideoSourceManager + MediaPipeOverlay, 验证:
1. baseline 模式 (hand_detector_path="") 是否正常加载 + 输出
2. 二段模式 (hand_detector_path=face_hand.pt) 是否正确切换 + 输出二段渲染
3. 输入坏路径 (不存在的 .pt) 是否优雅回退 baseline
4. 性能: 单帧推理耗时 (二段 vs baseline) 对比

输出:
    /home/qianqian/桌面/mediapipe_poc/smoke/smoke_baseline.jpg
    /home/qianqian/桌面/mediapipe_poc/smoke/smoke_two_stage.jpg
    /home/qianqian/桌面/mediapipe_poc/smoke/smoke_fallback.jpg
    /home/qianqian/桌面/mediapipe_poc/smoke/smoke_report.json
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "threads;1")
import cv2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.api.source_mediapipe import MediaPipeOverlay  # noqa: E402

VIDEO1 = "/home/qianqian/文档/xwechat_files/wxid_9j6tgdyqgpon22_030a/msg/file/2026-05/4.avi"
VIDEO2 = "/home/qianqian/文档/xwechat_files/wxid_9j6tgdyqgpon22_030a/msg/video/2026-05/c11a389ee019c2c2a6e57198fd9dc4ed.mp4"
HAND_DET_PT = "/home/qianqian/桌面/mediapipe_poc/models/face_hand.pt"
BAD_PT = "/home/qianqian/桌面/mediapipe_poc/models/__does_not_exist__.pt"

OUT_DIR = Path("/home/qianqian/桌面/mediapipe_poc/smoke")
OUT_DIR.mkdir(parents=True, exist_ok=True)


class FakeHost:
    """模拟 VideoSourceManager 的 host 字段集, 只挂 MediaPipe 相关字段."""

    def __init__(self, **overrides):
        # baseline 字段
        self.mediapipe_enabled = True
        self.mediapipe_pose = False  # 简化, 关 pose 只测 hands
        self.mediapipe_hands = True
        self.mediapipe_confidence = 0.3
        # v3.8.0 mp.solutions.hands 调优 (默认保守 = 0, 朋友同款 = 1)
        self.mediapipe_model_complexity = 0
        self.mediapipe_track_confidence = 0.5
        # 二段 pipeline 字段 (默认 baseline)
        self.mediapipe_hand_detector_path = ""
        self.mediapipe_hand_detector_kind = "v8"
        self.mediapipe_hand_detector_conf = 0.20
        self.mediapipe_hand_detector_iou = 0.45
        self.mediapipe_hand_detector_imgsz = 640
        self.mediapipe_hand_detector_class = -1
        self.mediapipe_hand_roi_pad = 0.5
        self.mediapipe_landmarker_task_path = ""
        # 其他通用字段
        self.device = "cuda:0"
        # 覆盖
        for k, v in overrides.items():
            setattr(self, k, v)


def grab_frame(video: str, idx: int):
    cap = cv2.VideoCapture(video)
    if not cap.isOpened():
        raise RuntimeError(f"can not open {video}")
    cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
    ok, frame = cap.read()
    cap.release()
    if not ok:
        raise RuntimeError(f"can not read frame {idx} from {video}")
    return frame


def time_apply(overlay: MediaPipeOverlay, frame, n_warmup=2, n_meas=5):
    """返回 avg_ms. 强制 interval=1 让每次都推理."""
    overlay._mp_process_interval = 1
    for _ in range(n_warmup):
        overlay.apply_overlay(frame.copy())
    samples = []
    for _ in range(n_meas):
        t0 = time.perf_counter()
        overlay.apply_overlay(frame.copy())
        samples.append((time.perf_counter() - t0) * 1000)
    return sum(samples) / len(samples)


def has_drawn_hands(frame_a, frame_b) -> bool:
    """简陋判定: 两张图差异>1万像素就算画了东西."""
    diff = (frame_a.astype(int) - frame_b.astype(int))
    return int((diff != 0).sum()) > 10000


def run_render_unit_test(report):
    """纯渲染单测 (跳过模型推理): 构造 mock landmarks 直接喂渲染函数."""
    print("=== Test 0: 纯渲染单测 (mock landmarks) ===")
    import numpy as np

    class FakeLandmark:
        def __init__(self, x, y, z=0.0):
            self.x = x; self.y = y; self.z = z

    # 在 1024x768 黑底图上模拟一只张开掌的 21 个关键点 (ROI 在 (200,150) - (500,500))
    canvas = np.zeros((768, 1024, 3), dtype=np.uint8)
    landmarks = [
        FakeLandmark(0.5, 0.95), FakeLandmark(0.4, 0.85), FakeLandmark(0.35, 0.7), FakeLandmark(0.3, 0.55), FakeLandmark(0.25, 0.45),
        FakeLandmark(0.4, 0.6),  FakeLandmark(0.4, 0.45), FakeLandmark(0.4, 0.3),  FakeLandmark(0.4, 0.2),
        FakeLandmark(0.5, 0.6),  FakeLandmark(0.5, 0.4),  FakeLandmark(0.5, 0.25), FakeLandmark(0.5, 0.15),
        FakeLandmark(0.6, 0.6),  FakeLandmark(0.6, 0.45), FakeLandmark(0.6, 0.3),  FakeLandmark(0.6, 0.2),
        FakeLandmark(0.7, 0.65), FakeLandmark(0.7, 0.5),  FakeLandmark(0.7, 0.4),  FakeLandmark(0.7, 0.3),
    ]
    host = FakeHost(mediapipe_hand_detector_path="")
    overlay = MediaPipeOverlay(host=host)
    # 直接喂缓存 (offset_xy, roi_size_wh, landmarks)
    overlay._last_two_stage_results = [((200, 150), (300, 350), landmarks)]
    overlay._two_stage_active = True
    overlay._draw_two_stage_hands(canvas)
    cv2.imwrite(str(OUT_DIR / "smoke_render_unit.jpg"), canvas)
    drew = int((canvas != 0).any(axis=-1).sum())
    print(f"  画上的像素数: {drew}  (>=21 应为画了关键点)")
    report["render_unit"] = {
        "non_black_pixels": drew,
        "ok": drew > 100,  # 至少几十个非零像素 (关键点 + 连线)
    }


def main():
    report = {}
    run_render_unit_test(report)

    # ============ Test 1: baseline (无 hand-detector) ============
    print("=== Test 1: baseline (无 hand-detector, 朋友同款参数 comp=1 conf=0.5) ===")
    host = FakeHost(
        mediapipe_hand_detector_path="",
        mediapipe_confidence=0.5,
        mediapipe_model_complexity=1,
        mediapipe_track_confidence=0.5,
    )
    overlay = MediaPipeOverlay(host=host)
    overlay.init()
    frame2 = grab_frame(VIDEO2, 120)  # 朋友 demo 完美识别的帧
    out_baseline = frame2.copy()
    avg_ms = time_apply(overlay, out_baseline)
    out_baseline = overlay.apply_overlay(out_baseline)
    cv2.imwrite(str(OUT_DIR / "smoke_baseline.jpg"), out_baseline)
    report["baseline"] = {
        "two_stage_active": overlay._two_stage_active,
        "avg_infer_ms": round(avg_ms, 2),
        "drew_something": has_drawn_hands(frame2, out_baseline),
    }
    print(f"  two_stage_active={overlay._two_stage_active}  avg={avg_ms:.1f}ms")
    overlay.release()

    # ============ Test 2: 二段 pipeline (face_hand.pt) ============
    print("=== Test 2: two-stage (face_hand.pt) ===")
    if not os.path.exists(HAND_DET_PT):
        print(f"  跳过 (找不到 {HAND_DET_PT})")
        report["two_stage"] = {"skipped": True}
    else:
        host = FakeHost(
            mediapipe_hand_detector_path=HAND_DET_PT,
            mediapipe_hand_detector_kind="v5",   # face_hand.pt 是 yolov5 格式
            mediapipe_hand_detector_class=3,     # hands 类
            mediapipe_hand_roi_pad=0.8,
            mediapipe_hand_detector_conf=0.10,
        )
        overlay = MediaPipeOverlay(host=host)
        overlay.init()
        out_two_stage = frame2.copy()
        avg_ms = time_apply(overlay, out_two_stage)
        out_two_stage = overlay.apply_overlay(out_two_stage)
        cv2.imwrite(str(OUT_DIR / "smoke_two_stage.jpg"), out_two_stage)
        # 在视频1的工业帧上也跑一下, 看二段在工业场景下表现
        frame1 = grab_frame(VIDEO1, 500)
        out_v1 = overlay.apply_overlay(frame1.copy())
        cv2.imwrite(str(OUT_DIR / "smoke_two_stage_v1.jpg"), out_v1)
        report["two_stage"] = {
            "two_stage_active": overlay._two_stage_active,
            "avg_infer_ms": round(avg_ms, 2),
            "drew_something_v2": has_drawn_hands(frame2, out_two_stage),
            "drew_something_v1": has_drawn_hands(frame1, out_v1),
        }
        print(f"  two_stage_active={overlay._two_stage_active}  avg={avg_ms:.1f}ms")
        overlay.release()

    # ============ Test 3: 坏路径 -> 优雅回退 ============
    print("=== Test 3: bad detector path -> fallback baseline ===")
    host = FakeHost(mediapipe_hand_detector_path=BAD_PT)
    overlay = MediaPipeOverlay(host=host)
    overlay.init()
    out_fallback = overlay.apply_overlay(frame2.copy())
    cv2.imwrite(str(OUT_DIR / "smoke_fallback.jpg"), out_fallback)
    report["fallback"] = {
        "two_stage_active": overlay._two_stage_active,  # 期望 False
        "graceful": (overlay._two_stage_active is False),
        "mediapipe_enabled_still_true": host.mediapipe_enabled,
    }
    print(f"  two_stage_active={overlay._two_stage_active} (期望 False)")
    overlay.release()

    with open(OUT_DIR / "smoke_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print("\n报告写入:", OUT_DIR / "smoke_report.json")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
