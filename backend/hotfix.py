"""
热补丁 v2.0.5 — RTSP H.265 + 摄像头低帧率修复 + generate_frames 崩溃修复
部署: 复制到 resources\\backend\\hotfix.py，重启软件
"""
import os
import time
import threading


def apply(app=None):
    """Apply all runtime patches."""
    _patch_start_rtsp()
    _patch_start_camera()
    if app is not None:
        _patch_video_feed(app)
        _add_debug_route(app)
    print("[Hotfix v2.0.5] 补丁已应用")


def _patch_start_camera():
    from backend.api.source import VideoSourceManager
    import cv2

    original_start_camera = VideoSourceManager.start_camera

    def patched_start_camera(self, device_index: int = 0, width: int = 1280, height: int = 720, fps: int = 60):
        self.stop(release_model=False)
        time.sleep(0.2)

        import platform
        max_retries = 3
        for attempt in range(max_retries):
            if platform.system() == "Windows":
                self.capture = cv2.VideoCapture(device_index, cv2.CAP_DSHOW)
            else:
                self.capture = cv2.VideoCapture(device_index)
            if self.capture.isOpened():
                break
            if attempt < max_retries - 1:
                print(f"[Camera] 打开摄像头失败，重试 {attempt + 2}/{max_retries}...")
                time.sleep(0.5)

        if not self.capture.isOpened():
            raise Exception(f"无法打开摄像头 {device_index}，请检查设备是否被其他程序占用")

        fourcc_mjpg = cv2.VideoWriter_fourcc('M', 'J', 'P', 'G')

        def _get_fourcc_str(cap):
            fc = int(cap.get(cv2.CAP_PROP_FOURCC))
            return "".join([chr((fc >> (8 * i)) & 0xFF) for i in range(4)])

        def _bench_fps(cap, n=10, timeout=5.0):
            try:
                cap.read()
                t0 = time.time()
                ok = 0
                for _ in range(n):
                    if time.time() - t0 > timeout:
                        break
                    if cap.read()[0]:
                        ok += 1
                elapsed = max(time.time() - t0, 0.001)
                return ok / elapsed
            except Exception:
                return 0

        self.capture.set(cv2.CAP_PROP_FOURCC, fourcc_mjpg)
        self.capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.capture.set(cv2.CAP_PROP_FPS, fps)

        cc_str = _get_fourcc_str(self.capture)

        if cc_str != 'MJPG' and platform.system() == "Windows":
            dshow_fps = _bench_fps(self.capture)
            print(f"[Camera] DirectShow({cc_str}) 实测 {dshow_fps:.0f}fps")

            # 先释放 DirectShow 再测 MSMF（某些摄像头不支持同时被两个后端打开）
            self.capture.release()
            self.capture = None
            time.sleep(0.3)

            msmf_cap = cv2.VideoCapture(device_index, cv2.CAP_MSMF)
            msmf_fps = 0
            if msmf_cap.isOpened():
                msmf_cap.set(cv2.CAP_PROP_FOURCC, fourcc_mjpg)
                msmf_cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
                msmf_cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
                msmf_cap.set(cv2.CAP_PROP_FPS, fps)
                msmf_cc = _get_fourcc_str(msmf_cap)
                msmf_fps = _bench_fps(msmf_cap)
                print(f"[Camera] MSMF({msmf_cc}) 实测 {msmf_fps:.0f}fps")
            else:
                print(f"[Camera] MSMF 无法打开摄像头 {device_index}")

            if msmf_fps > dshow_fps:
                self.capture = msmf_cap
                cc_str = _get_fourcc_str(self.capture)
                print(f"[Camera] 选择 MSMF 后端 ({msmf_fps:.0f}fps > DirectShow {dshow_fps:.0f}fps)")
            else:
                if msmf_cap.isOpened():
                    msmf_cap.release()
                # 重新打开 DirectShow
                self.capture = cv2.VideoCapture(device_index, cv2.CAP_DSHOW)
                self.capture.set(cv2.CAP_PROP_FOURCC, fourcc_mjpg)
                self.capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
                self.capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
                self.capture.set(cv2.CAP_PROP_FPS, fps)
                cc_str = _get_fourcc_str(self.capture)
                print(f"[Camera] 保留 DirectShow 后端 ({dshow_fps:.0f}fps >= MSMF {msmf_fps:.0f}fps)")

            best_fps = max(dshow_fps, msmf_fps)

            if best_fps < 5:
                print(f"[Camera] ⚠ 帧率极低({best_fps:.0f}fps)，尝试 CAP_ANY 后端...")
                any_cap = cv2.VideoCapture(device_index)
                if any_cap.isOpened():
                    any_cap.set(cv2.CAP_PROP_FOURCC, fourcc_mjpg)
                    any_cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
                    any_cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
                    any_cap.set(cv2.CAP_PROP_FPS, fps)
                    try:
                        any_cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    except Exception:
                        pass
                    any_cc = _get_fourcc_str(any_cap)
                    any_fps = _bench_fps(any_cap)
                    print(f"[Camera] CAP_ANY({any_cc}) 实测 {any_fps:.0f}fps")
                    if any_fps > best_fps:
                        self.capture.release()
                        self.capture = any_cap
                        cc_str = any_cc
                        best_fps = any_fps
                        print(f"[Camera] 选择 CAP_ANY 后端 ({any_fps:.0f}fps)")
                    else:
                        any_cap.release()

                if best_fps < 5 and (width > 640 or height > 480):
                    print(f"[Camera] ⚠ 尝试降低分辨率到 640x480...")
                    self.capture.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                    self.capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                    lowres_fps = _bench_fps(self.capture)
                    print(f"[Camera] 640x480 实测 {lowres_fps:.0f}fps")
                    if lowres_fps > best_fps * 1.5:
                        print(f"[Camera] 使用低分辨率 ({lowres_fps:.0f}fps > {best_fps:.0f}fps)")
                        best_fps = lowres_fps
                    else:
                        self.capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
                        self.capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
                        print(f"[Camera] 低分辨率无改善，恢复 {width}x{height}")

                try:
                    self.capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                except Exception:
                    pass

        cc_str = _get_fourcc_str(self.capture)

        if cc_str != 'MJPG' and platform.system() != "Windows":
            print(f"[Camera] V4L2 返回 {cc_str}，尝试重新打开...")
            self.capture.release()
            self.capture = cv2.VideoCapture(device_index, cv2.CAP_V4L2)
            if self.capture.isOpened():
                self.capture.set(cv2.CAP_PROP_FOURCC, fourcc_mjpg)
                self.capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
                self.capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
                self.capture.set(cv2.CAP_PROP_FPS, fps)
                cc_str = _get_fourcc_str(self.capture)

        actual_fps = self.capture.get(cv2.CAP_PROP_FPS)
        actual_w = int(self.capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(self.capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        print(f"[Camera] Capture format: {cc_str}, FPS: {actual_fps}, requested: {width}x{height}, actual: {actual_w}x{actual_h}")
        if cc_str != 'MJPG':
            print(f"[Camera] ⚠ 当前格式 {cc_str} (未压缩)，高分辨率下帧率通常只有 5-10fps")
            print(f"[Camera]   原因: 大多数 USB 摄像头在 {cc_str} 模式下硬件吞吐率有限")
            print(f"[Camera]   建议: 1) 确认摄像头支持 MJPG  2) 降低分辨率  3) 更换支持 MJPG 的摄像头")

        self.source_type = 'camera'
        self._camera_backend = int(self.capture.get(cv2.CAP_PROP_BACKEND)) if hasattr(cv2, 'CAP_PROP_BACKEND') else None
        self.camera_index = device_index
        self.width = width
        self.height = height
        self.fps = fps
        self.is_running = True

        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        return True

    VideoSourceManager.start_camera = patched_start_camera
    print("[Hotfix] start_camera 已替换 (低帧率 CAP_ANY/降分辨率 修复)", flush=True)


def _patch_video_feed(app):
    """修复 generate_frames 中 cv2.putText 在 OpenCV 4.11 下崩溃的问题"""
    import cv2
    import numpy as np
    from fastapi.responses import StreamingResponse

    for route in app.routes:
        if hasattr(route, 'path') and route.path == '/video_feed':
            original_endpoint = route.endpoint

            def patched_video_feed(channel: int = 0):
                from backend.api.channel_manager import channel_manager
                vm = channel_manager.get(channel)

                if vm.is_running or vm.source_type:
                    return StreamingResponse(
                        vm.generate_mjpeg(),
                        media_type="multipart/x-mixed-replace; boundary=frame"
                    )

                def generate_frames():
                    black_frame = np.zeros((480, 640, 3), dtype=np.uint8)
                    try:
                        cv2.putText(black_frame, f"Ch{channel} - No Source", (180, 240),
                                   cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
                    except cv2.error:
                        pass
                    ret, buffer = cv2.imencode('.jpg', black_frame)
                    if ret:
                        frame_data = buffer.tobytes()
                        while True:
                            yield (b'--frame\r\n'
                                   b'Content-Type: image/jpeg\r\n\r\n' + frame_data + b'\r\n')
                            time.sleep(0.1)

                return StreamingResponse(
                    generate_frames(),
                    media_type="multipart/x-mixed-replace; boundary=frame"
                )

            route.endpoint = patched_video_feed
            print("[Hotfix] video_feed 已替换 (cv2.putText 崩溃修复)", flush=True)
            break


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
