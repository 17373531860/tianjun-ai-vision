"""
热补丁 v2.7.7c — 基于 v2.0.5 + WMax 扫码器 text_lon 自动升级 + 容器最大识别数周期累计修复
                + WMax trigger/test_connection 自动重连 + 诊断日志
                + WMax 连接后自动 activate_rpt_reporting (让扫码器持续识别上报条码)
部署: 复制到 resources\\backend\\hotfix.py，重启软件
"""
import os
import time
import threading


def apply(app=None):
    """Apply all runtime patches."""
    _patch_start_rtsp()
    _patch_start_camera()
    _patch_scanner_text_lon_upgrade()
    _patch_wmax_trigger_and_test()
    _patch_container_max_recognized()
    if app is not None:
        _patch_video_feed(app)
        _add_debug_route(app)
    print("[Hotfix v2.7.7c] 补丁已应用")


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


def _patch_scanner_text_lon_upgrade():
    """v2.7.7a: 把所有 device_type='text_lon' 的扫码器自动视为 'auto'

    v2.7.7 前端 ScannerPanel.vue 把所有新增扫码器默认写成 text_lon,
    导致数据库里积累的老记录无法走 WMax 三端口协议. 本补丁:
    1. 替换 ScannerService._start_device: 收到 text_lon 自动转 auto
    2. 启动后 3s 扫一遍已建立的 connections, 把 text_lon 的重启为 auto
    3. 触发 _auto_discover_wmax_bg 让 WMax 三端口连接起来
    """
    try:
        from backend.services.scanner import ScannerService
    except ImportError as e:
        print(f"[Hotfix] scanner 模块导入失败, 跳过补丁: {e}", flush=True)
        return

    original_start_device = ScannerService._start_device

    _wmax_discover_pending = {'flag': False, 'lock': threading.Lock()}

    def _trigger_wmax_discover_once(svc):
        """合并多次触发, 2 秒内只跑一次 _auto_discover_wmax_bg"""
        with _wmax_discover_pending['lock']:
            if _wmax_discover_pending['flag']:
                return
            _wmax_discover_pending['flag'] = True

        def _run():
            time.sleep(2.0)
            try:
                svc._auto_discover_wmax_bg()
                print("[Hotfix] 已触发 WMax 三端口自动发现 (来自 text_lon 升级)", flush=True)
            except Exception as e:
                print(f"[Hotfix] 触发 WMax 发现失败: {e}", flush=True)
            finally:
                with _wmax_discover_pending['lock']:
                    _wmax_discover_pending['flag'] = False

        threading.Thread(target=_run, daemon=True, name="hotfix-wmax-discover").start()

    def patched_start_device(self, dev):
        upgraded = False
        try:
            raw = getattr(dev, 'device_type', None)
            if raw == 'text_lon':
                dev.device_type = 'auto'
                upgraded = True
                print(f"[Hotfix] 扫码器 '{dev.name}' device_type=text_lon -> auto", flush=True)
        except Exception as e:
            print(f"[Hotfix] _start_device 拦截异常: {e}", flush=True)

        result = original_start_device(self, dev)

        if upgraded:
            try:
                new_conn = self._connections.get(dev.id)
                if new_conn is not None:
                    from backend.services.wmax.manager import get_wmax_manager
                    from backend.services.wmax.device import DEFAULT_PORT as WMAX_CMD_PORT
                    mgr = get_wmax_manager()
                    existing_dev = mgr.get_device(new_conn.ip, WMAX_CMD_PORT)
                    if existing_dev is not None and getattr(existing_dev.state, 'connected', False):
                        new_conn.status = "connected"
                        new_conn.last_error = ""
                        print(f"[Hotfix] '{new_conn.name}' WMax 管理连接已存在, conn.status 直接标为 connected", flush=True)
            except Exception as e:
                print(f"[Hotfix] 同步 conn.status 失败 (不影响后续发现): {e}", flush=True)

            _trigger_wmax_discover_once(self)
        return result

    ScannerService._start_device = patched_start_device
    print("[Hotfix] ScannerService._start_device 已替换 (text_lon -> auto, 升级后自动触发 WMax 发现)", flush=True)

    def _upgrade_running_connections():
        time.sleep(3.0)
        try:
            from backend.services.scanner import get_scanner_service
            svc = get_scanner_service()
            if svc is None:
                print("[Hotfix] scanner service 未就绪, 放弃升级", flush=True)
                return

            affected = []
            auto_wmax_present = False
            for conn in list(svc._connections.values()):
                dt = getattr(conn, 'device_type', None)
                if dt == 'text_lon':
                    affected.append(conn)
                elif dt in ('auto', 'wmax', 'wmax_scan'):
                    auto_wmax_present = True

            if affected:
                print(f"[Hotfix] 发现 {len(affected)} 个 text_lon 扫码器, 开始升级为 auto", flush=True)
                for conn in affected:
                    try:
                        svc._stop_connection(conn)
                    except Exception as e:
                        print(f"[Hotfix]   停止 {conn.name} 失败: {e}", flush=True)
                    conn.device_type = 'auto'
                    conn.status = 'pending_wmax'
                    conn.last_error = ''
                    try:
                        conn._stop_event.clear()
                    except Exception:
                        pass
                    try:
                        new_thread = threading.Thread(
                            target=svc._listen_loop, args=(conn,),
                            daemon=True, name=f"scanner-{conn.device_id}-hotfix"
                        )
                        conn._thread = new_thread
                        new_thread.start()
                        auto_wmax_present = True
                        print(f"[Hotfix]   '{conn.name}' ({conn.ip}) 已升级为 auto", flush=True)
                    except Exception as e:
                        print(f"[Hotfix]   重启 {conn.name} 监听循环失败: {e}", flush=True)
            else:
                print("[Hotfix] 没有 text_lon 扫码器需要升级", flush=True)

            if auto_wmax_present:
                try:
                    svc._auto_discover_wmax_bg()
                    print("[Hotfix] 已触发 WMax 三端口自动发现 (auto/wmax 扫码器已存在, 需要建立管理连接)", flush=True)
                except Exception as e:
                    print(f"[Hotfix] 触发 WMax 发现失败: {e}", flush=True)
            else:
                print("[Hotfix] 无 auto/wmax 扫码器, 跳过 WMax 发现触发", flush=True)
        except Exception as e:
            print(f"[Hotfix] 升级已连接扫码器异常: {e}", flush=True)

    threading.Thread(
        target=_upgrade_running_connections,
        daemon=True,
        name="hotfix-scanner-upgrade"
    ).start()
    print("[Hotfix] 扫码器升级后台线程已启动 (3s 后执行)", flush=True)


def _patch_wmax_trigger_and_test():
    """v2.7.7b: 给 WMax 触发 / 测试按钮 加详细诊断日志 + 自动重连 + 强制打光日志.

    问题现象:
      - 检测开始/测试按钮 → 扫码器完全无反应 (没亮、没扫码)
      - 日志只看到 `[Scanner] start_scanning(ch=0) → ['扫码器-...[type=auto]']`
        但 WMax 命令是否真的发出去了, dev 是否 state.connected, 全都无日志可查.

    根因:
      - `_wmax_trigger` 在 dev is None 或 dev.state.connected=False 时 silently return,
        没有任何日志.
      - `dev.trigger_on()` 用 logger.info, 但 uvicorn 默认日志级别 WARNING, 全部吞掉.
      - 即使 TCP socket 之前连上过, WMax 设备可能主动断开空闲连接, 下次 trigger 就失败.

    修复:
      1. 替换 ScannerService._wmax_trigger:
         - 所有分支都用 print 输出, 看清每一步.
         - 发现 dev 未连接时, 主动 mgr.connect() 重连一次再发命令.
         - 命令发送后 print 确认.
      2. 替换 ScannerService.test_connection (测试按钮) 中 WMax 分支的打光逻辑,
         增加 print 诊断 (flash 前后时间戳, 发的命令数).
    """
    try:
        from backend.services.scanner import ScannerService
        from backend.services.wmax.manager import get_wmax_manager
        from backend.services.wmax.device import DEFAULT_PORT as WMAX_CMD_PORT
    except ImportError as e:
        print(f"[Hotfix] scanner/wmax 模块导入失败, 跳过 trigger 补丁: {e}", flush=True)
        return

    _rpt_activated_devs: set = set()
    _rpt_activate_lock = threading.Lock()

    def _activate_rpt_once(dev, ip, cause=""):
        """连接后自动发 GetConfigOpt + TurnOnOffVideo(on=True) 激活条码上报.

        注: 这是让扫码器"持续识别并把条码推到 RPT 端口"的关键 handshake.
        官方 IDManager 工具也是这么做的 (连上就推条码, 不亮红光).
        之前 v2.7.7 把它删掉, 是为了避免扫码器"一直闪"——但那是基于错误观察.
        实际上用户反馈: 官方工具也不闪, 但能扫到码. 说明 activate 本身不会让它闪.
        """
        key = id(dev)
        with _rpt_activate_lock:
            if key in _rpt_activated_devs:
                return
            _rpt_activated_devs.add(key)

        def _run():
            import asyncio as _aio
            try:
                loop = _aio.new_event_loop()
                _aio.set_event_loop(loop)
                try:
                    print(f"[Hotfix/WMax] {ip} 激活 RPT 上报 (cause={cause}) ...",
                          flush=True)
                    ok = loop.run_until_complete(dev.activate_rpt_reporting())
                    print(f"[Hotfix/WMax] {ip} activate_rpt_reporting 返回 {ok} "
                          f"(True=成功, False=扫码器未响应 TurnOnOffVideo)",
                          flush=True)
                finally:
                    loop.close()
            except Exception as e:
                import traceback as tb
                print(f"[Hotfix/WMax] {ip} activate_rpt_reporting 异常: "
                      f"{e}\n{tb.format_exc()}", flush=True)
                with _rpt_activate_lock:
                    _rpt_activated_devs.discard(key)

        threading.Thread(target=_run, daemon=True,
                         name=f"hotfix-rpt-activate-{ip}").start()

    def _ensure_wmax_connected(ip, cause=""):
        """确保 ip:55266 有一个已连接的 WMaxDevice, 返回 dev 或 None.
        连接成功后会自动异步激活 RPT 上报 (同一 dev 对象只激活一次)."""
        try:
            mgr = get_wmax_manager()
            dev = mgr.get_device(ip, WMAX_CMD_PORT)
            if dev is not None and getattr(dev.state, 'connected', False):
                _activate_rpt_once(dev, ip, cause=f"{cause}/already_connected")
                return dev
            print(f"[Hotfix/WMax] {ip} 未连接 (cause={cause}), 尝试重连...", flush=True)
            result = mgr.connect(ip, WMAX_CMD_PORT)
            if result.get('success'):
                dev = mgr.get_device(ip, WMAX_CMD_PORT)
                if dev is not None and getattr(dev.state, 'connected', False):
                    print(f"[Hotfix/WMax] {ip} 重连成功", flush=True)
                    _activate_rpt_once(dev, ip, cause=f"{cause}/reconnect")
                    return dev
            print(f"[Hotfix/WMax] {ip} 重连失败: {result}", flush=True)
            return None
        except Exception as e:
            print(f"[Hotfix/WMax] {ip} 重连异常: {e}", flush=True)
            return None

    try:
        from backend.services.wmax.device import WMaxDevice
        _orig_disconnect = WMaxDevice.disconnect

        def patched_disconnect(self):
            try:
                _rpt_activated_devs.discard(id(self))
            except Exception:
                pass
            return _orig_disconnect(self)

        WMaxDevice.disconnect = patched_disconnect
        print("[Hotfix] WMaxDevice.disconnect 已替换 (清理 RPT 激活缓存)", flush=True)
    except Exception as e:
        print(f"[Hotfix] WMaxDevice.disconnect 补丁失败 (不影响主流程): {e}", flush=True)

    def patched_wmax_trigger(self, conn, on):
        if conn.device_type not in ("auto", "wmax", "wmax_scan"):
            print(f"[Hotfix/WMax] skip trigger_{('on' if on else 'off')} '{conn.name}' "
                  f"(device_type={conn.device_type})", flush=True)
            return
        try:
            dev = _ensure_wmax_connected(conn.ip,
                                          cause=f"trigger_{('on' if on else 'off')}")
            if dev is None:
                print(f"[Hotfix/WMax] trigger_{('on' if on else 'off')} '{conn.name}' "
                      f"SKIPPED: 无法建立 WMax 连接 ({conn.ip}:{WMAX_CMD_PORT})", flush=True)
                return

            print(f"[Hotfix/WMax] trigger_{('on' if on else 'off')} '{conn.name}' "
                  f"({conn.ip}) → 发送 TurnOnOffVideo + Trigger + LON/LOFF ...", flush=True)
            if on:
                dev.trigger_on()
            else:
                dev.trigger_off()
            print(f"[Hotfix/WMax] trigger_{('on' if on else 'off')} '{conn.name}' "
                  f"命令已发送 (fire-and-forget)", flush=True)
        except Exception as e:
            import traceback as tb
            print(f"[Hotfix/WMax] trigger_{('on' if on else 'off')} '{conn.name}' "
                  f"异常: {e}\n{tb.format_exc()}", flush=True)

    ScannerService._wmax_trigger = patched_wmax_trigger
    print("[Hotfix] ScannerService._wmax_trigger 已替换 (详细日志 + 自动重连)", flush=True)

    import asyncio as _asyncio
    import socket as _socket

    original_test_connection = ScannerService.test_connection

    def patched_test_connection(self, ip, port, timeout=3.0):
        print(f"[Hotfix/WMax] test_connection({ip}:{port}) 开始 ...", flush=True)
        try:
            dev = _ensure_wmax_connected(ip, cause="test_connection")
            if dev is not None:
                print(f"[Hotfix/WMax] test_connection({ip}) → "
                      f"activate_rpt(含GetConfigOpt) + 收码 5s ...", flush=True)

                collected = []
                seen = set()
                orig_cb = dev.on_code_received

                def _tap(code_info):
                    try:
                        for c in code_info.get("codes", []):
                            raw = (c.get("data") or "").strip()
                            if raw and raw not in seen:
                                seen.add(raw)
                                collected.append(raw)
                                print(f"[Hotfix/WMax] test_connection({ip}) "
                                      f"收到条码: {raw}", flush=True)
                    except Exception:
                        pass
                    if orig_cb is not None:
                        try:
                            orig_cb(code_info)
                        except Exception:
                            pass

                dev.on_code_received = _tap
                loop = _asyncio.new_event_loop()
                _asyncio.set_event_loop(loop)
                try:
                    try:
                        ok = loop.run_until_complete(dev.activate_rpt_reporting())
                        print(f"[Hotfix/WMax] test_connection({ip}) "
                              f"activate_rpt_reporting={ok}", flush=True)
                    except Exception as e:
                        print(f"[Hotfix/WMax] test_connection({ip}) "
                              f"activate_rpt 异常: {e}", flush=True)
                    loop.run_until_complete(_asyncio.sleep(5.0))
                finally:
                    try:
                        dev.on_code_received = orig_cb
                    except Exception:
                        pass
                    loop.close()

                print(f"[Hotfix/WMax] test_connection({ip}) 收码结束, "
                      f"采集 {len(collected)} 条码", flush=True)
                if collected:
                    return {
                        "success": True,
                        "message": f"WMax {ip} 5 秒内扫到 {len(collected)} 条: "
                                   f"{', '.join(collected[:5])}",
                        "device_type": "wmax",
                        "scanned": collected,
                    }
                return {
                    "success": True,
                    "message": f"WMax {ip} 已激活扫码 5 秒, 未收到条码 "
                               f"(请在扫描视野内放置条码再试)",
                    "device_type": "wmax",
                    "scanned": [],
                }
            print(f"[Hotfix/WMax] test_connection({ip}) WMax 路径不通, 降级文本 LON/LOFF",
                  flush=True)
        except Exception as e:
            import traceback as tb
            print(f"[Hotfix/WMax] test_connection({ip}) WMax 分支异常, 降级文本: "
                  f"{e}\n{tb.format_exc()}", flush=True)

        try:
            sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.connect((ip, port))

            def _flash_and_stop():
                try:
                    sock.sendall(b"LON\r\n")
                    time.sleep(5)
                    sock.sendall(b"LOFF\r\n")
                    time.sleep(0.2)
                except OSError:
                    pass
                finally:
                    sock.close()

            threading.Thread(target=_flash_and_stop, daemon=True).start()
            print(f"[Hotfix/WMax] test_connection({ip}) 文本模式 LON 已发送",
                  flush=True)
            return {
                "success": True,
                "message": f"连接 {ip}:{port} 成功 (闪灯 5 秒, 文本模式)",
                "device_type": "text_lon",
            }
        except Exception as e:
            print(f"[Hotfix/WMax] test_connection({ip}) 文本模式也失败: {e}",
                  flush=True)
            return {"success": False, "message": str(e)}

    ScannerService.test_connection = patched_test_connection
    print("[Hotfix] ScannerService.test_connection 已替换 (WMax 自动重连 + 诊断日志)",
          flush=True)


def _patch_container_max_recognized():
    """v2.7.7a: 容器模式下"最大识别数" 改为"一个容器周期内累计不超过 N 个"

    v2.7.4 实现只做帧内 top-N 合并, 不管跨帧累计:
      - T1 帧屏幕看到 2 个 pillar → 合并成 1 个 (OK)
      - T2 帧 keeper 被遮挡, 看到另一个位置的 pillar → ByteTrack 给了新 track_id
      - Container 按 unique track_id 累加 → items_ever_seen 记下 2 个
      - 结账时 item_class_counts[pillar] = 2 > 期望 1 → NG

    新语义: 每个 box 里同一 label 最多只记录 N 个不同的 track_id,
            超出的新 track_id 只更新最早那个的 last_seen, 不增加 count.
    """
    try:
        from backend.api.source import VideoSourceManager
    except ImportError as e:
        print(f"[Hotfix] source 模块导入失败, 跳过补丁: {e}", flush=True)
        return

    if not hasattr(VideoSourceManager, '_update_container_grouping'):
        print("[Hotfix] VideoSourceManager 没有 _update_container_grouping, 跳过补丁", flush=True)
        return

    def patched_update_container_grouping(self, expected_items, current_time,
                                          gone_confirm_frames=30,
                                          cycle_strategy='all_gone'):
        container_label = self._container_label
        expected_no_container = {k: v for k, v in expected_items.items() if k != container_label}

        max_recognized_per_label = {}
        try:
            steps_config = (self.project_config or {}).get('steps_config', []) if self.project_config else []
            for step in steps_config:
                if not step.get('enabled', True):
                    continue
                if step.get('count_mode', 'track') != 'track':
                    continue
                lbl = step.get('class_name') or step.get('label', '')
                if not lbl:
                    continue
                try:
                    mr = int(step.get('max_recognized', 0) or 0)
                    if mr > 0:
                        max_recognized_per_label[lbl] = mr
                except (TypeError, ValueError):
                    pass
        except Exception:
            pass

        active_box_dids = set()
        box_bboxes = {}
        item_entries = []

        for tid, obj in self._tracking_objects.items():
            if obj['class_name'] == container_label:
                did = obj['display_id']
                active_box_dids.add(did)
                box_bboxes[did] = obj['bbox']
                if did not in self._box_objects:
                    self._box_counter += 1
                    self._box_objects[did] = {
                        'display_id': did,
                        'bbox': obj['bbox'],
                        'first_seen': obj.get('first_seen', current_time),
                        'last_seen': current_time,
                        'gone_frames': 0,
                        'had_roi': False,
                        'items_ever_seen': {},
                        'item_class_counts': {},
                        'is_complete': False,
                    }
                else:
                    self._box_objects[did]['bbox'] = obj['bbox']
                    self._box_objects[did]['last_seen'] = current_time
            else:
                item_entries.append((
                    tid, obj['class_name'],
                    obj.get('display_id', ''), obj['bbox']
                ))

        for tid, obj in self._tracking_recently_lost.items():
            if obj['class_name'] == container_label:
                did = obj['display_id']
                if did in self._box_objects and did not in active_box_dids:
                    active_box_dids.add(did)
                    box_bboxes[did] = obj['bbox']

        for item_tid, item_label, item_did, item_bbox in item_entries:
            item_cx = item_bbox['x'] + item_bbox['w'] / 2
            item_cy = item_bbox['y'] + item_bbox['h'] / 2

            best_box_did = None
            best_box_area = float('inf')
            for box_did, bb in box_bboxes.items():
                bx1, by1 = bb['x'], bb['y']
                bx2, by2 = bx1 + bb['w'], by1 + bb['h']
                if bx1 <= item_cx <= bx2 and by1 <= item_cy <= by2:
                    area = bb['w'] * bb['h']
                    if area < best_box_area:
                        best_box_area = area
                        best_box_did = box_did

            if best_box_did is None:
                continue
            box_state = self._box_objects[best_box_did]

            if item_tid in box_state['items_ever_seen']:
                box_state['items_ever_seen'][item_tid]['last_seen'] = current_time
                continue

            limit = max_recognized_per_label.get(item_label, 0)
            cur_count = box_state['item_class_counts'].get(item_label, 0)
            if limit > 0 and cur_count >= limit:
                for prev_tid, info in box_state['items_ever_seen'].items():
                    if info.get('label') == item_label:
                        info['last_seen'] = current_time
                        break
                continue

            box_state['items_ever_seen'][item_tid] = {
                'label': item_label,
                'display_id': item_did,
                'first_seen': current_time,
                'last_seen': current_time,
            }
            box_state['item_class_counts'][item_label] = \
                box_state['item_class_counts'].get(item_label, 0) + 1

        for box_did in active_box_dids:
            if box_did in self._box_objects:
                bs = self._box_objects[box_did]
                bs['is_complete'] = (not expected_no_container) or all(
                    bs['item_class_counts'].get(cls, 0) >= exp
                    for cls, exp in expected_no_container.items()
                )
                if cycle_strategy == 'roi_exit':
                    det = {'x': bs['bbox']['x'], 'y': bs['bbox']['y'],
                           'w': bs['bbox']['w'], 'h': bs['bbox']['h']}
                    if self._is_in_roi(det):
                        bs['had_roi'] = True

        for box_did in list(self._box_objects.keys()):
            bs = self._box_objects[box_did]
            box_visible = box_did in active_box_dids

            should_count_gone = False
            if cycle_strategy == 'roi_exit':
                should_count_gone = bs['had_roi'] and not box_visible
            else:
                should_count_gone = not box_visible

            if should_count_gone:
                bs['gone_frames'] = bs.get('gone_frames', 0) + 1
                if bs['gone_frames'] == 1:
                    print(f"[Container] {box_did} gone, confirming: {gone_confirm_frames} frames")
                if bs['gone_frames'] >= gone_confirm_frames:
                    print(f"[Container] {box_did} confirmed gone ({bs['gone_frames']}/{gone_confirm_frames})")
                    _sd = False
                    try:
                        _sd = self.project_config.get('pipeline_config', {}).get('settle_dedup', False) if self.project_config else False
                    except Exception:
                        pass
                    if _sd and not self.current_cycle_id:
                        self.start_cycle()
                    self._settle_box(box_did, expected_items)
            else:
                if bs.get('gone_frames', 0) > 0:
                    print(f"[Container] {box_did} reappeared, reset ({bs['gone_frames']}/{gone_confirm_frames})")
                bs['gone_frames'] = 0

    VideoSourceManager._update_container_grouping = patched_update_container_grouping
    print("[Hotfix] _update_container_grouping 已替换 (max_recognized 改为周期累计语义)", flush=True)
