"""虚拟合成视频源：按剧本帧序列注入检测结果，走完整推理线程 → 步骤统计链路。

仅在 source_type=='synthetic' 时生效；剧本为 JSON（tests/scenarios/*.json）。"""
from __future__ import annotations

import json
import os
import threading
from typing import Any, Dict, List, Optional

import cv2
import numpy as np


class SyntheticMixin:
    """由 VideoSourceManager 继承；提供 synthetic 帧生成与剧本解析。"""

    _SYNTHETIC_LOCK = threading.Lock()

    def _synthetic_scenarios_root(self) -> str:
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "tests", "scenarios"))
        return root

    def _synthetic_reset_scenario(self) -> None:
        self._synthetic_spec: Optional[Dict[str, Any]] = None
        self._synthetic_timeline: List[Dict[str, Any]] = []
        self._synthetic_seq = 0
        self._synthetic_last_published_idx = -1
        self._latest_synthetic_inference_idx = -1

    def load_synthetic_scenario_from_path(self, path: str) -> None:
        """从绝对路径或相对于 tests/scenarios 的文件名加载 JSON 剧本。"""
        if not os.path.isabs(path):
            path = os.path.join(self._synthetic_scenarios_root(), path)
        path = os.path.normpath(path)
        if not os.path.isfile(path):
            raise FileNotFoundError(f"synthetic scenario not found: {path}")
        with open(path, "r", encoding="utf-8") as f:
            spec = json.load(f)
        self._synthetic_apply_spec(spec)

    def load_synthetic_scenario_dict(self, spec: Dict[str, Any]) -> None:
        self._synthetic_apply_spec(spec)

    def _synthetic_apply_spec(self, spec: Dict[str, Any]) -> None:
        with self._SYNTHETIC_LOCK:
            self._synthetic_spec = spec
            self._synthetic_timeline = list(spec.get("timeline") or [])
            self._synthetic_seq = 0
            self._synthetic_last_published_idx = -1
            fps = spec.get("fps")
            if fps:
                try:
                    self.fps = float(fps)
                except (TypeError, ValueError):
                    pass

    def start_synthetic(
        self,
        scenario: Optional[str] = None,
        scenario_dict: Optional[Dict[str, Any]] = None,
        fps: Optional[float] = None,
    ) -> bool:
        """启动 synthetic 捕获线程（无摄像头、无模型）。scenario 为文件名或绝对路径。"""
        self.stop(release_model=False)
        self._synthetic_reset_scenario()
        if scenario_dict is not None:
            self.load_synthetic_scenario_dict(scenario_dict)
        elif scenario:
            self.load_synthetic_scenario_from_path(scenario)
        else:
            self.load_synthetic_scenario_dict({"name": "empty", "timeline": []})
        if fps is not None:
            try:
                self.fps = float(fps)
            except (TypeError, ValueError):
                pass
        self.source_type = "synthetic"
        self.capture = None
        self.video_ended = False
        self.is_running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        return True

    def stop_synthetic(self) -> None:
        """停止 synthetic 源（等价 stop，保留模型）。

        如果之前用 apply_temporary_project_config 临时改过项目，stop 时自动恢复。
        """
        self.stop(release_model=False)
        self._restore_project_config_after_synthetic()

    def apply_temporary_project_config(self, cfg: Dict[str, Any]) -> None:
        """给 synthetic 临时套一份项目配置；stop_synthetic 时会自动恢复。

        如果当前已有 project_config，第一次调用会保存到 _pre_synthetic_project_config；
        重复调用不会覆盖该备份（保护最初的"真"配置）。
        """
        if not hasattr(self, "_pre_synthetic_project_config") or self._pre_synthetic_project_config is None:
            try:
                snapshot = getattr(self, "project_config", None)
                if snapshot:
                    import copy
                    self._pre_synthetic_project_config = copy.deepcopy(snapshot)
                else:
                    self._pre_synthetic_project_config = None
            except Exception:
                self._pre_synthetic_project_config = None
        try:
            self.set_project_config(cfg)
        except Exception as e:
            print(f"[SyntheticMixin] apply_temporary_project_config 失败: {e}")
            raise

    def _restore_project_config_after_synthetic(self) -> None:
        backup = getattr(self, "_pre_synthetic_project_config", None)
        if backup is None:
            return
        try:
            self.set_project_config(backup)
            print("[SyntheticMixin] 已恢复 synthetic 之前的项目配置")
        except Exception as e:
            print(f"[SyntheticMixin] 恢复原项目配置失败: {e}")
        finally:
            self._pre_synthetic_project_config = None

    def _synthetic_next_frame(self) -> np.ndarray:
        """生成一帧纯黑画面（带帧序号 OSD），并更新 publish 索引供推理线程查询。"""
        h = int(getattr(self, "height", 720) or 720)
        w = int(getattr(self, "width", 1280) or 1280)
        frame = np.zeros((h, w, 3), dtype=np.uint8)
        idx = int(getattr(self, "_synthetic_seq", 0))
        self._synthetic_last_published_idx = idx
        self._synthetic_seq = idx + 1
        cv2.putText(
            frame,
            f"synthetic {idx}",
            (24, 48),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.0,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )
        return frame

    def _synthetic_detections_for_frame_index(self, frame_idx: int) -> List[Dict[str, Any]]:
        """按帧号从 timeline 解析 detections（归一化 xywh，与模型输出一致）。"""
        timeline = getattr(self, "_synthetic_timeline", None) or []
        out: List[Dict[str, Any]] = []
        for seg in timeline:
            try:
                a = int(seg.get("from", seg.get("frames_from", -1)))
                b = int(seg.get("to", seg.get("frames_to", -2)))
            except (TypeError, ValueError):
                continue
            if a <= frame_idx <= b:
                for d in seg.get("detections") or []:
                    label = d.get("label")
                    if not label:
                        continue
                    conf = float(d.get("confidence", 0.99))
                    bbox = d.get("bbox") or [0.1, 0.1, 0.2, 0.2]
                    if len(bbox) != 4:
                        continue
                    x, y, bw, bh = (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
                    item = {
                        "label": str(label),
                        "class_name": str(label),
                        "confidence": conf,
                        "x": x,
                        "y": y,
                        "w": bw,
                        "h": bh,
                    }
                    cid = d.get("class_id")
                    if cid is not None:
                        try:
                            item["class_id"] = int(cid)
                        except (TypeError, ValueError):
                            item["class_id"] = 0
                    else:
                        item["class_id"] = 0
                    out.append(item)
                break
        return out

    def get_synthetic_debug_state(self) -> Dict[str, Any]:
        return {
            "source_type": getattr(self, "source_type", None),
            "frame_seq": int(getattr(self, "_synthetic_seq", 0)),
            "last_published_idx": int(getattr(self, "_synthetic_last_published_idx", -1)),
            "scenario_name": (getattr(self, "_synthetic_spec", None) or {}).get("name"),
        }
