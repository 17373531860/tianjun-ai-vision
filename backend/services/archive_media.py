# -*- coding: utf-8 -*-
"""归档媒体处理 (v3.53 二期): NG 关键帧快照 / 事件切片 / 证据包 zip。

关键帧机制 (照 interconnect sampler 的"结算点抽帧"思路):
- NG 结算瞬间由 VSM 侧调 save_ng_keyframe() 把当前带框显示帧存 JPEG
  ({DATA_DIR}/recordings/keyframes/<date>/cycle_<uuid>.jpg, 命名可反查)
- 归档 worker 用 find_keyframe(cycle_uuid) 按约定路径找图 (今天/昨天两个
  日期目录, 跨午夜安全), 找到就跟随录像一起交付
- 是否抽帧由 video_archive.keyframe_wanted() 守门: 没有启用 attach_keyframe
  的规则时一帧不抽 (零开销)
"""
import os
import subprocess
import zipfile
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from backend.core.config import DATA_DIR

KEYFRAME_DIR = os.path.join(DATA_DIR, "recordings", "keyframes")


def keyframe_path_for(cycle_uuid: str, when: Optional[datetime] = None) -> str:
    day = (when or datetime.now()).strftime("%Y-%m-%d")
    return os.path.join(KEYFRAME_DIR, day, f"cycle_{cycle_uuid}.jpg")


def save_ng_keyframe(frame, cycle_uuid: str, channel_id: int = 0,
                     serial_no: Optional[str] = None,
                     watermark: bool = True) -> Optional[str]:
    """把 NG 结算瞬间的显示帧存 JPEG。frame 为 BGR ndarray (调用方已拷贝)。
    失败只打日志不抛 (证据是增值品, 不能影响结算热路径)。"""
    if frame is None or not cycle_uuid:
        return None
    try:
        import cv2
        path = keyframe_path_for(cycle_uuid)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        img = frame
        if watermark:
            img = frame.copy()
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            text = f"NG {ts} ch{channel_id}"
            if serial_no:
                text += f" SN:{serial_no}"
            # 黑底白字, 左上角, 不遮画面主体
            (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
            cv2.rectangle(img, (6, 6), (14 + tw, 18 + th), (0, 0, 0), -1)
            cv2.putText(img, text, (10, 12 + th), cv2.FONT_HERSHEY_SIMPLEX,
                        0.6, (255, 255, 255), 2, cv2.LINE_AA)
        # tmp 名必须保留 .jpg 结尾: imwrite 按扩展名选编码器, ".tmp" 结尾必失败
        tmp = path + ".tmp.jpg"
        ok = cv2.imwrite(tmp, img, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
        if not ok:
            return None
        os.replace(tmp, path)
        return path
    except Exception as e:
        print(f"[VideoArchive] NG 关键帧保存失败 (不影响结算): {e}")
        return None


def save_keyframe_async(frame, detections, pending: Dict[str, Any],
                        drawer=None):
    """推理循环消费 _archive_ng_frame_pending 的入口:
    调用方只付一次 frame.copy() 的代价, 画框 + JPEG 编码在独立线程做
    (NG 结算才发生, 频率极低, 不占推理热路径)。

    detections: [{x, y, w, h (归一化, 左上+宽高), label, confidence}, ...]
    drawer: VSM 的 source_drawer (画角框+中文标签); 传 None 则存无框原帧。
    """
    import threading
    if frame is None or not pending or not pending.get("cycle_uuid"):
        return
    img = frame.copy()
    dets = [dict(d) for d in (detections or [])]
    cycle_uuid = pending["cycle_uuid"]
    channel_id = pending.get("channel_id") or 0

    def _work():
        try:
            canvas = img
            if drawer is not None and dets:
                h, w = canvas.shape[:2]
                for d in dets:
                    try:
                        x1 = int(float(d.get("x", 0)) * w)
                        y1 = int(float(d.get("y", 0)) * h)
                        x2 = x1 + int(float(d.get("w", 0)) * w)
                        y2 = y1 + int(float(d.get("h", 0)) * h)
                        canvas = drawer.draw_box(
                            canvas, x1, y1, x2, y2,
                            str(d.get("label", "")),
                            float(d.get("confidence", 0) or 0))
                    except Exception:
                        continue  # 单框画失败不影响其余框
            from backend.services.video_archive import keyframe_watermark_wanted
            save_ng_keyframe(canvas, cycle_uuid, channel_id,
                             watermark=keyframe_watermark_wanted())
        except Exception as e:
            print(f"[VideoArchive] 关键帧异步落盘失败: {e}")

    threading.Thread(target=_work, daemon=True,
                     name="va-keyframe-save").start()


def find_keyframe(cycle_uuid: str) -> Optional[str]:
    """按约定路径找关键帧 (今天/昨天两个日期目录, 跨午夜安全)。"""
    now = datetime.now()
    for when in (now, now - timedelta(days=1)):
        p = keyframe_path_for(cycle_uuid, when)
        if os.path.isfile(p):
            return p
    return None


# ---------------------------------------------------------------------------
# 事件切片 (FFmpeg 尾段)
# ---------------------------------------------------------------------------

def clip_tail(src_path: str, out_path: str, seconds: int,
              timeout: float = 120.0) -> str:
    """切录像最后 N 秒 (NG 发生在周期结算瞬间, 尾段即事件段)。

    用 -sseof -N: FFmpeg 从文件尾倒退 N 秒开始拷贝流 (不转码, -c copy 秒切)。
    坑: -sseof 必须放在 -i 之前; copy 模式起点会对齐到最近关键帧,
    实际时长可能略长于 N 秒 (可接受, 宁多勿少)。源比 N 秒短时全量拷贝。
    """
    from backend.api.source import get_cached_ffmpeg_path
    ffmpeg = get_cached_ffmpeg_path()
    cmd = [ffmpeg, "-y", "-sseof", f"-{max(1, int(seconds))}", "-i", src_path,
           "-c", "copy", "-movflags", "+faststart", out_path]
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    if proc.returncode != 0 or not os.path.isfile(out_path) \
            or os.path.getsize(out_path) <= 0:
        err = (proc.stderr or b"")[-300:].decode("utf-8", "ignore")
        raise RuntimeError(f"事件切片失败 (ffmpeg rc={proc.returncode}): {err}")
    return out_path


# ---------------------------------------------------------------------------
# 证据包 zip
# ---------------------------------------------------------------------------

def build_evidence_zip(out_path: str, entries: List[Dict[str, Any]]) -> str:
    """打证据包。entries: [{"arcname": 包内路径, "path": 磁盘文件} |
    {"arcname": ..., "data": bytes/str}]。缺失文件跳过不报错 (尽力打包)。"""
    tmp = out_path + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        for e in entries:
            arc = e["arcname"]
            if "data" in e:
                data = e["data"]
                if isinstance(data, str):
                    data = data.encode("utf-8")
                zf.writestr(arc, data)
            elif e.get("path") and os.path.isfile(e["path"]):
                zf.write(e["path"], arc)
    os.replace(tmp, out_path)
    return out_path
