"""
视频录制 + 轨迹平滑：从 source.py 拆分而来。

包含两个互不相关、与 VideoSourceManager 没有 self 耦合的纯类：
  - FFmpegRecorder: 用 FFmpeg 子进程做硬编码录像，避免 OpenCV VideoWriter 在 H.264
    编码时的 CUDA 卡死问题
  - KalmanFilter2D: 8 维 (x,y,w,h,vx,vy,vw,vh) 卡尔曼滤波器，用于平滑检测框抖动

之所以拆出来：
  - 这两个类在 source.py 里完全独立（不依赖任何模块级状态/全局），抽出来纯收益
  - 单元测试可以直接 import，不用拖整个 VideoSourceManager
  - 减少 source.py 体积约 200 行
"""
import os
import subprocess
import threading

import cv2
import numpy as np


def _get_ffmpeg_path_cached():
    """延迟 import 拿 ffmpeg 路径，避免循环依赖（source.py 顶部还在初始化）"""
    from backend.api.source import get_cached_ffmpeg_path
    return get_cached_ffmpeg_path()


def remux_to_faststart(filepath: str, timeout: float = None) -> bool:
    """把 fragmented MP4 收尾整理成 faststart 常规 MP4 (流拷贝, 不转码)。

    v3.54 长录像治理: 录制中用 fMP4 (随时可播/断电不废), 收尾后 remux 回
    标准 mp4 —— moov 前置秒开、时长元数据精确、老播放器全兼容。
    tmp + 原子替换; 任何失败都保留原 fMP4 (照样能播), 绝不越修越坏。

    Returns: True = 已替换为 faststart mp4; False = 保留原文件。
    """
    try:
        if not os.path.isfile(filepath):
            return False
        size = os.path.getsize(filepath)
        if size <= 0:
            return False

        # 磁盘余量护栏: remux 期间新旧两份并存, 余量不足宁可不整理
        import shutil
        free = shutil.disk_usage(os.path.dirname(filepath) or ".").free
        if free < size * 1.3 + 200 * 1024 * 1024:
            print(f"[FFmpeg录制] 磁盘余量不足, 跳过收尾整理: {os.path.basename(filepath)}")
            return False

        if timeout is None:
            # 流拷贝按磁盘速度走, 每 GB 给 2 分钟预算, 上限 15 分钟
            timeout = min(900.0, 60.0 + size / (1024 ** 3) * 120.0)

        ffmpeg_path = _get_ffmpeg_path_cached()
        # tmp 后缀刻意不带 .mp4, 避免被清理/孤儿扫描当成录像; 用 -f mp4 显式指格式
        tmp_path = filepath + ".remux.tmp"
        cmd = [ffmpeg_path, "-y", "-i", filepath,
               "-c", "copy", "-movflags", "+faststart",
               "-f", "mp4", tmp_path]
        proc = subprocess.run(cmd, stdin=subprocess.DEVNULL,
                              capture_output=True, timeout=timeout)
        if proc.returncode == 0 and os.path.isfile(tmp_path) \
                and os.path.getsize(tmp_path) > 0:
            os.replace(tmp_path, filepath)
            print(f"[FFmpeg录制] 收尾整理完成(faststart): {os.path.basename(filepath)}")
            return True
        err = (proc.stderr or b"")[-300:].decode("utf-8", "ignore")
        print(f"[FFmpeg录制] 收尾整理失败(保留 fMP4 可播): rc={proc.returncode} {err}")
    except Exception as e:
        print(f"[FFmpeg录制] 收尾整理异常(保留 fMP4 可播): {e}")
    finally:
        try:
            _tmp = filepath + ".remux.tmp"
            if os.path.exists(_tmp):
                os.remove(_tmp)
        except OSError:
            pass
    return False


class FFmpegRecorder:
    """使用 FFmpeg 进程进行视频录制

    通过管道发送帧数据，完全独立于 Python/CUDA，避免卡死。
    """
    MAX_RECORD_WIDTH = 640
    MAX_RECORD_HEIGHT = 360

    def __init__(self, filepath: str, width: int, height: int, fps: int = 25):
        self.filepath = filepath
        # 限制录制分辨率，保持宽高比
        if width > self.MAX_RECORD_WIDTH or height > self.MAX_RECORD_HEIGHT:
            scale = min(self.MAX_RECORD_WIDTH / width, self.MAX_RECORD_HEIGHT / height)
            self.width = int(width * scale) // 2 * 2
            self.height = int(height * scale) // 2 * 2
        else:
            self.width = width
            self.height = height
        self.input_width = width
        self.input_height = height
        self.fps = fps
        self.process = None
        self._lock = threading.Lock()
        self._is_open = False
        self._frame_count = 0
        self.last_error = ""
        # v3.1.2: 保留 ffmpeg stderr 最后 20 行, 出错时能看到原因 (避免之前那种全静默)
        self._stderr_lines: list = []
        self._stderr_thread = None

    def open(self) -> bool:
        """启动 FFmpeg 进程"""
        try:
            ffmpeg_path = _get_ffmpeg_path_cached()

            cmd = [
                ffmpeg_path,
                '-y',
                '-f', 'rawvideo',
                '-vcodec', 'rawvideo',
                '-pix_fmt', 'bgr24',
                '-s', f'{self.width}x{self.height}',
                '-r', str(self.fps),
                '-i', 'pipe:0',
                '-c:v', 'libx264',
                '-preset', 'ultrafast',
                '-tune', 'zerolatency',
                '-threads', '1',
                '-crf', '28',
                '-pix_fmt', 'yuv420p',
                # 固定 GOP=125 帧 (25fps 下 5s): fMP4 每个关键帧收一个 fragment
                # 落盘, 崩溃丢失粒度确定为 ≤5s (默认 keyint 250 是 10s)
                '-g', '125',
                # v3.54 长录像治理: 录制中写 fragmented MP4 —— 每个关键帧起一个
                # fragment 落盘, 文件任意时刻可播; 断电/强杀只丢最后一个片段
                # (≤~10s), 不再像 +faststart 那样收尾重写 moov 失败就整段全废
                # (24h 会话录像的"视频加载失败"根因)。收尾由 release() 异步
                # remux 回 faststart 常规 mp4 (见 remux_to_faststart)。
                '-movflags', '+frag_keyframe+empty_moov+default_base_moof',
                # 每包即时刷盘: 不加的话输出滞留在 ffmpeg ~32KB IO 缓冲里,
                # 高压缩画面可能几分钟不落盘, 强杀丢失窗口不可控
                '-flush_packets', '1',
                self.filepath
            ]

            self.process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                bufsize=10**6
            )
            self._is_open = True
            self._frame_count = 0
            self.last_error = ""
            self._stderr_lines = []
            self._stderr_thread = threading.Thread(
                target=self._drain_stderr, daemon=True, name=f"ffmpeg-stderr-{os.path.basename(self.filepath)}"
            )
            self._stderr_thread.start()
            print(f"[FFmpeg录制] 已启动: {os.path.basename(self.filepath)}")
            return True
        except Exception as e:
            print(f"[FFmpeg录制] 启动失败: {e}")
            self.last_error = f"open_failed: {e}"
            self._is_open = False
            return False

    def write(self, frame) -> bool:
        """写入一帧（非阻塞，失败时静默）"""
        if not self._is_open or self.process is None:
            return False

        try:
            with self._lock:
                if self.process.poll() is not None:
                    self.last_error = f"ffmpeg_exited: code={self.process.returncode}"
                    self._is_open = False
                    return False

                if frame.shape[1] != self.width or frame.shape[0] != self.height:
                    frame = cv2.resize(frame, (self.width, self.height), interpolation=cv2.INTER_AREA)

                self.process.stdin.write(frame.tobytes())
                self._frame_count += 1
                return True
        except (BrokenPipeError, OSError):
            self.last_error = "pipe_broken_or_os_error"
            self._is_open = False
            return False
        except Exception as e:
            self.last_error = f"write_exception: {e}"
            return False

    def _drain_stderr(self):
        """后台线程: 持续读 ffmpeg stderr, 只保留最后 20 行用于诊断."""
        proc = self.process
        if proc is None or proc.stderr is None:
            return
        try:
            for raw in iter(proc.stderr.readline, b''):
                if not raw:
                    break
                try:
                    text = raw.decode('utf-8', errors='replace').rstrip()
                except Exception:
                    text = str(raw)
                if text:
                    self._stderr_lines.append(text)
                    if len(self._stderr_lines) > 20:
                        del self._stderr_lines[:-20]
        except Exception:
            pass

    def release(self, remux: bool = True):
        """关闭录制器

        fMP4 收尾无 moov 重写, 子进程正常在 1s 内退出; 超时兜底 kill 也
        只丢最后一个片段, 文件仍可播。之后异步 remux 成 faststart 常规 mp4
        (守护线程, 不阻塞调用方; 进程退出中断 remux 也只是保留 fMP4)。
        """
        with self._lock:
            if self.process is not None:
                try:
                    if self.process.stdin:
                        self.process.stdin.close()
                except Exception:
                    pass
                try:
                    self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    try:
                        self.process.kill()
                        self.process.wait(timeout=2)
                    except Exception:
                        pass
                except Exception:
                    try:
                        self.process.kill()
                    except Exception:
                        pass
                finally:
                    self.process = None
            self._is_open = False
            print(f"[FFmpeg录制] 已停止: {os.path.basename(self.filepath)}, 共 {self._frame_count} 帧")
            # v3.1.2: 0 帧时打印 ffmpeg stderr, 便于现场排查 (只在异常情况下打, 正常录制不刷屏)
            if self._frame_count == 0 and self._stderr_lines:
                print(f"[FFmpeg录制] !!! 0 帧异常, ffmpeg stderr 末尾 {len(self._stderr_lines)} 行:")
                for line in self._stderr_lines:
                    print(f"  | {line}")

        # 锁外起收尾整理线程 (remux 可能秒级~分钟级, 不能占 _lock)
        if remux and self._frame_count > 0:
            threading.Thread(
                target=remux_to_faststart, args=(self.filepath,), daemon=True,
                name=f"ffmpeg-remux-{os.path.basename(self.filepath)}"
            ).start()

    def isOpened(self) -> bool:
        """检查是否正在录制"""
        return self._is_open and self.process is not None and self.process.poll() is None


class KalmanFilter2D:
    """2D 卡尔曼滤波器，用于平滑检测框位置

    状态向量: [x, y, w, h, vx, vy, vw, vh] (位置 + 速度)
    """
    def __init__(self, initial_state, process_noise=0.03, measurement_noise=0.1):
        """
        Args:
            initial_state: [x, y, w, h] 初始位置
            process_noise: 过程噪声 Q (越小越平滑，越大响应越快)
            measurement_noise: 观测噪声 R (越大越平滑，对突变不敏感)
        """
        self.state = np.array([
            initial_state[0], initial_state[1], initial_state[2], initial_state[3],
            0, 0, 0, 0
        ], dtype=np.float64)

        # 状态转移矩阵 (假设匀速运动)
        self.F = np.array([
            [1, 0, 0, 0, 1, 0, 0, 0],
            [0, 1, 0, 0, 0, 1, 0, 0],
            [0, 0, 1, 0, 0, 0, 1, 0],
            [0, 0, 0, 1, 0, 0, 0, 1],
            [0, 0, 0, 0, 1, 0, 0, 0],
            [0, 0, 0, 0, 0, 1, 0, 0],
            [0, 0, 0, 0, 0, 0, 1, 0],
            [0, 0, 0, 0, 0, 0, 0, 1],
        ], dtype=np.float64)

        # 观测矩阵 (只能观测位置，不能直接观测速度)
        self.H = np.array([
            [1, 0, 0, 0, 0, 0, 0, 0],
            [0, 1, 0, 0, 0, 0, 0, 0],
            [0, 0, 1, 0, 0, 0, 0, 0],
            [0, 0, 0, 1, 0, 0, 0, 0],
        ], dtype=np.float64)

        self.Q = np.eye(8, dtype=np.float64) * process_noise
        self.R = np.eye(4, dtype=np.float64) * measurement_noise
        self.P = np.eye(8, dtype=np.float64)

    def predict(self):
        """预测步骤"""
        self.state = self.F @ self.state
        self.P = self.F @ self.P @ self.F.T + self.Q
        return self.state[:4]

    def update(self, measurement):
        """更新步骤"""
        z = np.array(measurement, dtype=np.float64)

        # 卡尔曼增益
        S = self.H @ self.P @ self.H.T + self.R
        K = self.P @ self.H.T @ np.linalg.inv(S)

        # 状态更新
        y = z - self.H @ self.state
        self.state = self.state + K @ y

        # 协方差更新
        I = np.eye(8)
        self.P = (I - K @ self.H) @ self.P

        return self.state[:4]

    def get_position(self):
        """获取当前位置估计"""
        return self.state[:4]
