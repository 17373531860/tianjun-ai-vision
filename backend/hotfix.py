"""
热补丁 v2.0.4b — 仅修复 RTSP H.265 连接问题 + 诊断接口
部署: 复制到 D:\\tianjunkeji\\tianjun-ai-vision\\resources\\backend\\hotfix.py
"""
import os
import time
import threading


def apply(app=None):
    """Apply all runtime patches."""
    _patch_start_rtsp()
    if app is not None:
        _add_debug_route(app)
    print("[Hotfix v2.0.4b] 补丁已应用")


def _patch_start_rtsp():
    from backend.api.source import VideoSourceManager
    import cv2

    def patched_start_rtsp(self, url: str, fps: int = 25):
        self.stop(release_model=False)
        time.sleep(0.2)

        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
            "rtsp_transport;tcp|analyzeduration;5000000|probesize;5000000"
        )

        safe_url = url.split("@")[-1] if "@" in url else url
        print(f"[RTSP] 正在连接: {safe_url} ...", flush=True)

        max_retries = 3
        for attempt in range(max_retries):
            print(f"[RTSP] 尝试 {attempt + 1}/{max_retries} ...", flush=True)
            self.capture = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
            if self.capture and self.capture.isOpened():
                break
            if self.capture:
                self.capture.release()
                self.capture = None
            if attempt < max_retries - 1:
                print("[RTSP] 连接失败，3 秒后重试 ...", flush=True)
                time.sleep(3.0)

        if self.capture is None or not self.capture.isOpened():
            raise Exception(
                f"无法连接 RTSP 流: {safe_url}，请检查地址/用户名/密码/网络连通性"
            )

        self.capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        actual_w = int(self.capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(self.capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        actual_fps = self.capture.get(cv2.CAP_PROP_FPS) or fps
        fourcc = int(self.capture.get(cv2.CAP_PROP_FOURCC))
        codec = (
            "".join([chr((fourcc >> 8 * i) & 0xFF) for i in range(4)])
            if fourcc else "未知"
        )

        print(
            f"[RTSP] 已连接: {actual_w}x{actual_h}, FPS: {actual_fps}, "
            f"编码: {codec}, URL: {safe_url}", flush=True,
        )

        frame_ok = False
        for i in range(30):
            ret, frame = self.capture.read()
            if ret:
                print(
                    f"[RTSP] 验证读帧成功 (第{i+1}次尝试), "
                    f"帧尺寸: {frame.shape}", flush=True,
                )
                frame_ok = True
                break
            time.sleep(0.3)

        if not frame_ok:
            self.capture.release()
            self.capture = None
            raise Exception(
                f"RTSP 已连接但无法读取视频帧 ({safe_url})。"
                f"当前编码: {codec}。"
                f"建议在 NVR 管理页面将该通道的视频编码改为 H.264"
            )

        self.source_type = "rtsp"
        self.rtsp_url = url
        self.width = actual_w
        self.height = actual_h
        self.fps = fps
        self.is_running = True

        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        return True

    VideoSourceManager.start_rtsp = patched_start_rtsp
    print("[Hotfix] start_rtsp 已替换 (FFmpeg H.265 优化)", flush=True)


def _add_debug_route(app):
    @app.get("/api/v1/debug/channels")
    def debug_channels():
        from backend.api.channel_manager import channel_manager
        result = {"channel_count": channel_manager.channel_count, "channels": {}}
        for cid in channel_manager.active_channels():
            try:
                mgr = channel_manager.get(cid)
                result["channels"][str(cid)] = {
                    "source_type": mgr.source_type,
                    "is_running": mgr.is_running,
                    "is_detecting": mgr.is_detecting,
                    "fps_actual": mgr.fps_actual,
                    "model_loaded": mgr.model is not None,
                }
            except Exception as e:
                result["channels"][str(cid)] = {"error": str(e)}
        return result

    print("[Hotfix] 诊断接口 /api/v1/debug/channels 已注册", flush=True)
