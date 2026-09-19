# -*- coding: utf-8 -*-
"""检测框 sidecar (2026-09) — 周期录像旁成对记录帧号对齐的检测框数据。

设计要点 (为什么是"帧号"而不是时间戳):
- 录制队列满会丢帧, 丢帧后"视频第 N 帧"与墙钟时间漂移, 按时间戳记框
  回放会越来越错位。唯一可靠的对齐点是录制线程 **写帧成功那一刻** 的
  FFmpegRecorder._frame_count —— 视频里第 N 帧的时间恒等于 N / fps
  (rawvideo 固定 -r 输入), 所以按帧号记录天然免疫丢帧漂移。
- 检测框在采集线程入队录制时随帧快照 (同一拍的 current_detections),
  帧和框在队列里成对旅行, 录制线程写谁记谁。

文件约定 (不加 DB 列, 纯命名约定):
- sidecar 路径 = 录像路径 + ".boxes.json" (如 cycle_xx.mp4.boxes.json)
- 与录像同目录同生命周期: 清理孤儿扫描按 mtime 一起过期, 归档 worker /
  回放端点按约定路径找, 找不到 = 没开「记录检测框数据」

数据格式 (run-length 压缩: 只在检测结果变化的帧记一条, 回放/渲染时
"沿用上一条直到下一条", 周期录像典型只有几十~几百条):
{
  "version": 1,
  "fps": 25,                     # 录像标称帧率 (帧号 → 秒的换算基准)
  "video": "cycle_xx.mp4",
  "frames": [
    {"f": 0, "d": [{"label": "螺丝", "conf": 0.93,
                     "x": 0.1, "y": 0.2, "w": 0.05, "h": 0.08}, ...]},
    {"f": 37, "d": []},          # 框消失也是一条 (空列表)
    ...
  ]
}
坐标为归一化 (0-1, 左上+宽高, 显示坐标系), 与 /detection/results 同源,
分辨率无关 —— 回放叠加与烧框渲染各自按目标尺寸换算。

线程模型: observe() 仅录制线程调用 (单线程, 无锁);
flush() 由录像延迟释放线程在 release() 之后调用 (录制线程此时已不再写
该 writer, 无并发)。
"""
import json
import os
from typing import Any, Dict, List, Optional

SIDECAR_SUFFIX = ".boxes.json"

# entries 上限护栏: 正常 run-length 下远达不到; 万一检测结果每帧都在抖,
# 封顶后停止记录 (已记录的部分仍有效), 防长周期把内存吃穿
MAX_ENTRIES = 20000


def sidecar_path_for(video_path: str) -> str:
    """录像文件对应的 sidecar 路径 (纯命名约定)。"""
    return (video_path or "") + SIDECAR_SUFFIX


class BoxesSidecarCollector:
    """挂在 cycle 录像 writer 上的检测框收集器 (录制线程逐帧喂)。"""

    def __init__(self, video_path: str, fps: int):
        self.video_path = video_path
        self.fps = int(fps) if fps else 25
        self.entries: List[Dict[str, Any]] = []
        self._last_sig: Optional[tuple] = None
        self._overflowed = False

    @staticmethod
    def _compact(det: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """压成最小存储形态; 缺坐标的脏数据直接丢弃。"""
        try:
            return {
                "label": str(det.get("label", "")),
                "conf": round(float(det.get("confidence", 0) or 0), 3),
                "x": round(float(det["x"]), 4),
                "y": round(float(det["y"]), 4),
                "w": round(float(det["w"]), 4),
                "h": round(float(det["h"]), 4),
            }
        except (KeyError, TypeError, ValueError):
            return None

    def observe(self, frame_idx: int, detections: List[Dict[str, Any]]):
        """记录第 frame_idx 帧 (0-based, 已写入视频) 的检测框。

        run-length 去重: 与上一条相同则不记 (回放沿用上一条)。
        """
        if self._overflowed or frame_idx < 0:
            return
        compact = []
        for d in detections or []:
            c = self._compact(d)
            if c is not None:
                compact.append(c)
        sig = tuple(sorted(
            (c["label"], c["conf"], c["x"], c["y"], c["w"], c["h"])
            for c in compact))
        if sig == self._last_sig:
            return
        self._last_sig = sig
        self.entries.append({"f": int(frame_idx), "d": compact})
        if len(self.entries) >= MAX_ENTRIES:
            self._overflowed = True
            print(f"[BoxesSidecar] entries 达上限 {MAX_ENTRIES}, 停止记录 "
                  f"(已记录部分仍有效): {os.path.basename(self.video_path)}")

    def flush(self) -> Optional[str]:
        """落盘 sidecar JSON (tmp + 原子替换)。无任何记录时不产文件。

        返回写出的路径; 失败返回 None (只打日志, 不影响录像)。
        """
        if not self.entries:
            return None
        path = sidecar_path_for(self.video_path)
        # tmp 名保持 .json 结尾, 且不会与正式名冲突
        tmp = path + ".tmp"
        try:
            payload = {
                "version": 1,
                "fps": self.fps,
                "video": os.path.basename(self.video_path),
                "frames": self.entries,
            }
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False,
                          separators=(",", ":"))
            os.replace(tmp, path)
            return path
        except Exception as e:
            print(f"[BoxesSidecar] 落盘失败 (不影响录像): {e}")
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            return None


def load_sidecar(path: str) -> Optional[Dict[str, Any]]:
    """读 sidecar JSON; 不存在/损坏返回 None。"""
    try:
        if not path or not os.path.isfile(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not isinstance(
                data.get("frames"), list):
            return None
        return data
    except Exception as e:
        print(f"[BoxesSidecar] 读取失败: {path}: {e}")
        return None
