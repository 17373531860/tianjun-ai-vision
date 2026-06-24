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

        v3.7.x: "新连接上位, 旧连接让位" 防僵尸连接累积。
        Chrome keep-alive 不会立即关闭旧 MJPEG socket, 旧 generator 仍
        会 hold 住 frame_lock + thread-pool worker, 导致新连接拿不到锁
        几秒, 浏览器 <img> 收不到首帧 -> 黑屏。修复: 每条 generator 分配
        connection_id, 记录该 channel 最新 id, 旧 generator 每次 yield
        前检测自己是否过期, 是则主动 break 释放资源。同一 channel 同时
        只保留 1 条 generator, 切换 Monitor / 路由刷新都不会累积。
        """
        from backend.api.channel_manager import channel_manager
        target_interval = 1.0 / max(self.target_stream_fps, 1)
        num_ch = max(channel_manager.channel_count, 1)
        min_interval = max(0.02, 0.015 * num_ch)
        idle_count = 0
        max_idle = 600
        last_seq = -1
        ch_label = getattr(self, 'channel_index', '?')
        # Push-side throughput stats (real display fluidity, see yield site below)
        _push_t0 = time.monotonic()
        _push_frames = 0

        # v3.7.x 分配 connection id, 同 channel 后来者上位
        self._mjpeg_next_conn_id = getattr(self, '_mjpeg_next_conn_id', 0) + 1
        my_conn_id = self._mjpeg_next_conn_id
        self._mjpeg_active_conn_id = my_conn_id

        try:
            self._mjpeg_active_streams = getattr(self, '_mjpeg_active_streams', 0) + 1
            print(f"[MJPEG] new connection #{my_conn_id} ch={ch_label}, active={self._mjpeg_active_streams}")
        except Exception:
            pass

        try:
            while True:
                # 旧连接让位: 同 channel 有更新的 id 进来 -> 主动退出
                if getattr(self, '_mjpeg_active_conn_id', my_conn_id) != my_conn_id:
                    print(f"[MJPEG] connection #{my_conn_id} ch={ch_label} yields to #{self._mjpeg_active_conn_id}, exiting")
                    break

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
                        # Push-side throughput: counts only real frames actually sent
                        # to the browser -> the true display fluidity that the
                        # inference-latency log cannot show. Logged every 5s.
                        _push_frames += 1
                        _push_now = time.monotonic()
                        if _push_now - _push_t0 >= 5.0:
                            _push_el = _push_now - _push_t0
                            _push_fps = _push_frames / _push_el if _push_el > 0 else 0.0
                            print(f"[MJPEG/Push] #{my_conn_id} ch={ch_label} "
                                  f"push_fps={_push_fps:.1f} frames={_push_frames}/{_push_el:.1f}s "
                                  f"target={self.target_stream_fps}")
                            _push_t0 = _push_now
                            _push_frames = 0

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
            print(f"[MJPEG] generator #{my_conn_id} abnormal exit ch={ch_label}: {e}")
        finally:
            try:
                self._mjpeg_active_streams = max(0, getattr(self, '_mjpeg_active_streams', 1) - 1)
                print(f"[MJPEG] connection #{my_conn_id} closed ch={ch_label}, active={self._mjpeg_active_streams}")
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
