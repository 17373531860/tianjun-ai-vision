"""MJPEG / 单帧 JPEG 推流 Mixin。

从 VideoSourceManager 抽出 5 个推流相关方法。这部分代码与帧锁 / current_frame /
frame_seq 有强读耦合，但只读不写状态机，独立性较好。

宿主类必须提供：
  - self.frame_lock (threading.Lock)
  - self.current_frame (np.ndarray | None)
  - self._frame_seq (int)
  - self.is_running (bool)
  - self.target_stream_fps (int)
  - self.frame_limit_enabled (bool)
  - self.channel_index (int)
  - self._mjpeg_active_streams (int, 可缺省，generate_mjpeg 内有 lazy init)
"""
from __future__ import annotations

import time

import cv2
import numpy as np


class StreamingMixin:
    def get_frame(self):
        """获取当前帧"""
        with self.frame_lock:
            if self.current_frame is not None:
                return self.current_frame.copy()
        return None

    def _encode_and_yield(self, frame):
        """Encode a frame to JPEG and return the MJPEG chunk bytes."""
        ret, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 65])
        if ret:
            data = (b'--frame\r\n'
                    b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')
            del buffer
            return data
        del buffer
        return None

    def _get_placeholder_frame(self):
        """Return a small black placeholder frame for when no real frame is available."""
        black = np.zeros((480, 640, 3), dtype=np.uint8)
        return black

    def generate_mjpeg(self):
        """Generate MJPEG stream.  Only encodes and sends when a genuinely new
        frame is available from the capture thread, so CPU is never wasted on
        duplicate JPEG encodes.

        v2.7.15 (C): try/finally + 异常捕获, 客户端断开时立即释放资源,
        并维护 _mjpeg_active_streams 计数方便诊断"连接是否累积"。
        """
        from backend.api.channel_manager import channel_manager
        target_interval = 1.0 / max(self.target_stream_fps, 1)
        num_ch = max(channel_manager.channel_count, 1)
        min_interval = max(0.02, 0.015 * num_ch)
        idle_count = 0
        max_idle = 600
        last_seq = -1

        # v2.7.15 (C): 活跃 MJPEG 连接计数
        try:
            self._mjpeg_active_streams = getattr(self, '_mjpeg_active_streams', 0) + 1
            print(f"[MJPEG] 新连接 ch={getattr(self, 'channel_index', '?')}, 活跃连接={self._mjpeg_active_streams}")
        except Exception:
            pass

        try:
            while True:
                if self.is_running:
                    idle_count = 0
                    frame = None
                    with self.frame_lock:
                        seq = self._frame_seq
                        if seq != last_seq and self.current_frame is not None:
                            frame = self.current_frame.copy()
                            last_seq = seq

                    if frame is None:
                        time.sleep(0.005)
                        continue

                    chunk = self._encode_and_yield(frame)
                    del frame
                    if chunk:
                        yield chunk

                    if self.frame_limit_enabled:
                        time.sleep(max(min_interval, target_interval))
                    else:
                        time.sleep(min_interval)
                else:
                    frame = self.get_frame()
                    if frame is None:
                        frame = self._get_placeholder_frame()
                    chunk = self._encode_and_yield(frame)
                    del frame
                    if chunk:
                        yield chunk

                    idle_count += 1
                    if idle_count > max_idle:
                        break
                    # 短间隔检查，以便 is_running 变 True 时快速恢复
                    for _ in range(10):
                        if self.is_running:
                            break
                        time.sleep(0.1)
        except (GeneratorExit, ConnectionResetError, BrokenPipeError):
            # 客户端断开, 正常退出
            pass
        except Exception as e:
            print(f"[MJPEG] generator 异常退出 ch={getattr(self, 'channel_index', '?')}: {e}")
        finally:
            try:
                self._mjpeg_active_streams = max(0, getattr(self, '_mjpeg_active_streams', 1) - 1)
                print(f"[MJPEG] 连接关闭 ch={getattr(self, 'channel_index', '?')}, 活跃连接={self._mjpeg_active_streams}")
            except Exception:
                pass

    def get_snapshot(self):
        """获取当前帧的单张 JPEG 快照（用于前端 canvas 渲染）"""
        frame = self.get_frame()
        if frame is not None:
            ret, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
            if ret:
                return buffer.tobytes()
        return None
