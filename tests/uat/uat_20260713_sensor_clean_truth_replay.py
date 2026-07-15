#!/usr/bin/env python
"""传感器清洁插件三段真值视频离线回放。

用途：
1. 用客户真实模型逐帧提取检测框并缓存；
2. 把完全相同的检测时间线喂给 sensor-clean 计数器；
3. 校验视角1正常=38、小幅度=2、视角2换棉签=16。

缓存默认落在系统临时目录，不进入仓库。可用环境变量覆盖：
  SENSOR_CLEAN_ASSETS     真值视频/模型目录
  SENSOR_CLEAN_CACHE_DIR  检测时间线缓存目录

示例：
  python tests/uat/uat_20260713_sensor_clean_truth_replay.py
  python tests/uat/uat_20260713_sensor_clean_truth_replay.py --case swap --refresh
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import time
from pathlib import Path

# 项目不变量：必须早于任何可能间接 import cv2 的模块。
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "threads;1")

import cv2
from ultralytics import YOLO


REPO = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO / "plugins-examples" / "sensor-clean" / "backend"
ASSET_DIR = Path(os.environ.get("SENSOR_CLEAN_ASSETS", r"D:\Tianjun\群光\sensor清洁"))
CACHE_DIR = Path(os.environ.get(
    "SENSOR_CLEAN_CACHE_DIR",
    str(Path(tempfile.gettempdir()) / "sensor_clean_truth_cache"),
))

CASES = {
    "normal": {
        "video": "视角1-正常.mp4",
        "model": "视角1.pt",
        "kind": "product",
        "expected": 38,
    },
    "small": {
        "video": "视角1-正常(小幅度移动不计数).mp4",
        "model": "视角1.pt",
        "kind": "product",
        "expected": 2,
    },
    "swap": {
        "video": "视角2-正常.avi",
        "model": "视角2.pt",
        "kind": "swap",
        "expected": 16,
    },
}


def _load_backend(package_name="sensor_clean_truth_replay"):
    """按真实 package 结构加载插件 backend，支持相对 import。"""
    for name in list(sys.modules):
        if name == package_name or name.startswith(package_name + "."):
            del sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        package_name,
        BACKEND_DIR / "__init__.py",
        submodule_search_locations=[str(BACKEND_DIR)],
    )
    package = importlib.util.module_from_spec(spec)
    sys.modules[package_name] = package
    spec.loader.exec_module(package)
    return sys.modules[package_name + ".hooks"], sys.modules[package_name + ".counter"]


def _cache_path(case_name: str, video: Path, model: Path) -> Path:
    stat_parts = [
        case_name,
        str(video.resolve()), str(video.stat().st_size), str(video.stat().st_mtime_ns),
        str(model.resolve()), str(model.stat().st_size), str(model.stat().st_mtime_ns),
        "conf=0.7", "iou=0.45", "imgsz=640",
    ]
    digest = hashlib.sha256("|".join(stat_parts).encode("utf-8")).hexdigest()[:16]
    return CACHE_DIR / f"{case_name}_{digest}.jsonl.gz"


def _video_meta(video: Path) -> dict:
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise RuntimeError(f"视频无法打开: {video}")
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
    frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    cap.release()
    if fps <= 0 or frames <= 0:
        raise RuntimeError(f"视频元数据异常: fps={fps}, frames={frames}, path={video}")
    return {"fps": fps, "frames": frames, "width": width, "height": height}


def _extract(case_name: str, *, refresh=False) -> tuple[Path, dict]:
    case = CASES[case_name]
    video = ASSET_DIR / case["video"]
    model_path = ASSET_DIR / case["model"]
    if not video.is_file() or not model_path.is_file():
        raise FileNotFoundError(f"真值资产不完整: {video} / {model_path}")
    meta = _video_meta(video)
    cache_path = _cache_path(case_name, video, model_path)
    if cache_path.is_file() and cache_path.stat().st_size > 64 and not refresh:
        print(f"[{case_name}] 复用缓存: {cache_path}", flush=True)
        return cache_path, meta

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    model = YOLO(str(model_path))
    print(
        f"[{case_name}] 提取检测: {video.name}, {meta['frames']}帧, "
        f"{meta['fps']:.3f}fps, {meta['width']}x{meta['height']}",
        flush=True,
    )
    started = time.time()
    seen = 0
    batch_size = 16
    tmp_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise RuntimeError(f"视频无法打开: {video}")
    with gzip.open(tmp_path, "wt", encoding="utf-8", newline="\n") as out:
        out.write(json.dumps({"meta": meta, "names": model.names}, ensure_ascii=False) + "\n")
        while True:
            frames = []
            for _ in range(batch_size):
                ok, frame = cap.read()
                if not ok:
                    break
                frames.append(frame)
            if not frames:
                break
            results = model.predict(
                source=frames, batch=len(frames), stream=False,
                conf=0.7, iou=0.45, imgsz=640, device=0, verbose=False,
            )
            for result in results:
                frame_idx = seen
                dets = []
                if result.boxes is not None:
                    xywhn = result.boxes.xywhn.cpu().tolist()
                    confs = result.boxes.conf.cpu().tolist()
                    classes = result.boxes.cls.cpu().tolist()
                    for (cx, cy, width, height), conf, cls_id in zip(xywhn, confs, classes):
                        dets.append({
                            "label": result.names[int(cls_id)],
                            "confidence": float(conf),
                            "x": float(cx - width / 2),
                            "y": float(cy - height / 2),
                            "w": float(width),
                            "h": float(height),
                        })
                out.write(json.dumps({
                    "seq": frame_idx,
                    "ts": frame_idx / meta["fps"],
                    "detections": dets,
                }, ensure_ascii=False, separators=(",", ":")) + "\n")
                seen += 1
            if seen % 1000 < batch_size:
                print(
                    f"[{case_name}] {seen}/{meta['frames']}帧 "
                    f"({time.time() - started:.1f}s)",
                    flush=True,
                )
    cap.release()
    tmp_path.replace(cache_path)
    print(f"[{case_name}] 检测缓存完成: {seen}帧 → {cache_path}", flush=True)
    return cache_path, meta


def _iter_cache(cache_path: Path):
    with gzip.open(cache_path, "rt", encoding="utf-8") as src:
        next(src)  # meta
        for line in src:
            yield json.loads(line)


class _FakeHost:
    def __init__(self, config):
        self._raw = json.dumps(config, ensure_ascii=False)
        self.events = []

    def read_system_config(self, _key):
        return self._raw

    def write_system_config(self, _key, value, description=""):
        self._raw = value
        return True

    def trigger_event(self, channel_id, event_id, reason=""):
        self.events.append((channel_id, int(event_id), reason))
        return True

    def trigger_alarm(self, channel_id, event_type, reason=""):
        return True


def _replay_product(cache_path: Path) -> tuple[int, list[float]]:
    hooks, _counter = _load_backend()
    config = dict(hooks.DEFAULT_CONFIG)
    config.update({
        "normal_count_event_id": 0,
        "swab_over_limit_event_id": 0,
        "fake_wipe_event_id": 0,
        "max_uses_per_swab": 999,
    })
    hooks.set_host(_FakeHost(config))
    hooks.reload_config()
    hit_times = []
    last = 0
    for frame in _iter_cache(cache_path):
        hooks.on_detection_frame({
            "channel_id": 0,
            "timestamp": frame["ts"],
            "frame_seq": frame["seq"],
            "detections": frame["detections"],
        })
        total = hooks.get_state()["total_products"]
        if total > last:
            hit_times.append(frame["ts"])
            last = total
    return last, hit_times


def _replay_swap(cache_path: Path) -> tuple[int, list[float]]:
    hooks, counter = _load_backend()
    config = dict(hooks.DEFAULT_CONFIG)
    window = counter.SwabChangeWindow(
        lock_time=float(config["swab_lock_time"]),
        min_sustain_sec=float(config["swab_min_sustain_sec"]),
        gap_sec=float(config["swab_gap_sec"]),
    )
    hit_times = []
    for frame in _iter_cache(cache_path):
        has_change = any(
            det.get("label") == config["swap_label"]
            and float(det.get("confidence", 0)) >= float(config["min_confidence"])
            for det in frame["detections"]
        )
        if window.feed(has_change, frame["ts"]):
            hit_times.append(frame["ts"])
    return len(hit_times), hit_times


def main():
    parser = argparse.ArgumentParser(description="sensor-clean 三视频真值回放")
    parser.add_argument(
        "--case", choices=["all", *CASES], default="all",
        help="只跑某段视频，默认 all",
    )
    parser.add_argument("--refresh", action="store_true", help="忽略缓存重新跑模型")
    args = parser.parse_args()

    selected = list(CASES) if args.case == "all" else [args.case]
    results = []
    for case_name in selected:
        cache_path, _meta = _extract(case_name, refresh=args.refresh)
        case = CASES[case_name]
        if case["kind"] == "product":
            actual, hit_times = _replay_product(cache_path)
        else:
            actual, hit_times = _replay_swap(cache_path)
        ok = actual == case["expected"]
        results.append({
            "case": case_name,
            "expected": case["expected"],
            "actual": actual,
            "passed": ok,
            "hit_times": [round(value, 3) for value in hit_times],
        })
        print(json.dumps(results[-1], ensure_ascii=False), flush=True)

    failed = [item for item in results if not item["passed"]]
    print(json.dumps({"failed": len(failed), "results": results}, ensure_ascii=False), flush=True)
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
