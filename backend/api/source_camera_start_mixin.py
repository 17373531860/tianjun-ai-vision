"""摄像头/视频源启动 + 资源释放 (v2.7.16 P6 阶段一第十刀)。

把所有 source_type-specific 的 start/release/reconnect/get_frame 方法集中在一个 mixin
(合计 ~650 行, 10 个方法):
  start_camera               : USB / V4L2 摄像头 (199L, 单方法最大)
  start_rtsp                 : RTSP 流 (71L)
  start_hcnetsdk             : 海康 SDK 直连 (87L)
  _get_hcnetsdk_frame        : SDK frame 取数 (10L)
  _release_hcnet_session     : SDK 资源释放 (9L)
  _reconnect_hcnetsdk        : SDK 断线重连 (18L)
  start_hikvision_camera     : 海康 ISAPI/RTSP 备选路径 (114L)
  _release_hik_camera        : ISAPI 资源释放 (20L)
  _get_hikvision_frame       : ISAPI frame 取数 (90L)
  start_video                : 本地视频文件 (35L)

依赖宿主 (VideoSourceManager):
  - 状态: cap / hcnet_session / hik_camera / source_type / fps / video_path /
          rtsp_url / video_speed / _running / _thread / etc.
  - 方法: stop / _capture_loop / debug_log
"""
import os
import platform
import time
import threading
import traceback
import cv2
import numpy as np

from backend.api.source_sdk_loader import debug_log, hik_log


def _v4l2_safe_bufsize_1(cap):
    """v2.7.16: 仅在 Windows 上设 BUFFERSIZE=1 减延迟.

    Linux V4L2 backend 在 BUFFERSIZE=1 下, OpenCV 的 cap.read() 必须等内核
    dequeue/queue 一个新帧, 单帧耗时从 ~33ms 飙到 ~67ms, 实际 FPS 直接腰斩
    (硬件 29fps → OpenCV 15fps). 实测见 _bench_camera.py.
    Linux 上保留默认 4 帧环形 buffer, 只要消费者跟得上, 实际拿到的也是最新帧.
    """
    if platform.system() != "Windows":
        return
    try:
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    except Exception:
        pass


class CameraStartMixin:
    def start_camera(self, device_index: int = 0, width: int = 1280, height: int = 720, fps: int = 60):
        """启动摄像头"""
        self.stop(release_model=False)
        
        # 等待一小段时间确保之前的资源已释放
        time.sleep(0.2)
        
        # 尝试打开摄像头（支持重试）
        max_retries = 3
        for attempt in range(max_retries):
            # Windows 上使用 DirectShow，Linux 上使用 V4L2
            import platform
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

        # v2.7.15 (A+B): _bench_fps 提到外层, 所有路径共享, 且打开后立即 bench 一次,
        # 避免"DirectShow 谎报 MJPG 但实际走 YUYV 10fps"的坑
        def _bench_fps(cap, n=10, timeout=5.0):
            """快速实测帧率，带超时防止慢摄像头阻塞过久"""
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

        # Strategy 1: Set FOURCC before resolution (standard approach)
        self.capture.set(cv2.CAP_PROP_FOURCC, fourcc_mjpg)
        self.capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.capture.set(cv2.CAP_PROP_FPS, fps)
        # v2.7.16 (Linux fix): Windows 用 BUFSZ=1 减延迟; Linux V4L2 上 BUFSZ=1
        # 反而把 cap.read() 拖到 ~67ms, 实际 FPS 腰斩. helper 内部按平台分流.
        _v4l2_safe_bufsize_1(self.capture)

        cc_str = _get_fourcc_str(self.capture)

        # v2.7.15 (A): 无论 FOURCC 报告如何, 都实测一次真实帧率
        # 阈值 = max(5, fps*0.6), 低于阈值就强制进入后端选优
        bench_fps_threshold = max(5.0, fps * 0.6)
        initial_bench_fps = _bench_fps(self.capture)
        print(f"[Camera] 首次实测: {cc_str} @ {initial_bench_fps:.0f}fps (阈值 {bench_fps_threshold:.0f}fps)")

        # Strategy 2: 格式非 MJPG 或 实测 FPS 低于阈值, 实测对比各后端选最快
        need_backend_probe = (cc_str != 'MJPG') or (initial_bench_fps < bench_fps_threshold)
        if need_backend_probe and platform.system() == "Windows":
            dshow_fps = initial_bench_fps
            print(f"[Camera] DirectShow({cc_str}) 采用首次实测 {dshow_fps:.0f}fps")

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
                _v4l2_safe_bufsize_1(msmf_cap)
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
                _v4l2_safe_bufsize_1(self.capture)
                cc_str = _get_fourcc_str(self.capture)
                print(f"[Camera] 保留 DirectShow 后端 ({dshow_fps:.0f}fps >= MSMF {msmf_fps:.0f}fps)")

            # Strategy 4: 帧率极低时尝试 CAP_ANY 和降低缓冲区
            best_fps = max(dshow_fps, msmf_fps)
            if best_fps < 5:
                print(f"[Camera] ⚠ 帧率极低({best_fps:.0f}fps)，尝试 CAP_ANY 后端...")
                any_cap = cv2.VideoCapture(device_index)
                if any_cap.isOpened():
                    any_cap.set(cv2.CAP_PROP_FOURCC, fourcc_mjpg)
                    any_cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
                    any_cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
                    any_cap.set(cv2.CAP_PROP_FPS, fps)
                    _v4l2_safe_bufsize_1(any_cap)
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

                # Strategy 5: 降低分辨率减少带宽需求
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

                _v4l2_safe_bufsize_1(self.capture)
        
        # Strategy 3: If still not MJPG on Linux, try without explicit backend
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
    
    def start_rtsp(self, url: str, fps: int = 25):
        """启动 RTSP 网络视频流（NVR / IP Camera）"""
        self.stop(release_model=False)
        time.sleep(0.2)

        import os
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
            "rtsp_transport;tcp|analyzeduration;5000000|probesize;5000000"
        )

        safe_url = url.split("@")[-1] if "@" in url else url
        print(f"[RTSP] 正在连接: {safe_url} ...")

        max_retries = 3
        for attempt in range(max_retries):
            print(f"[RTSP] 尝试 {attempt + 1}/{max_retries} ...")
            self.capture = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
            if self.capture.isOpened():
                break
            if self.capture:
                self.capture.release()
                self.capture = None
            if attempt < max_retries - 1:
                print(f"[RTSP] 连接失败，{3}秒后重试 ...")
                time.sleep(3.0)

        if self.capture is None or not self.capture.isOpened():
            raise Exception(f"无法连接 RTSP 流: {safe_url}，请检查地址/用户名/密码/网络连通性")

        self.capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        actual_w = int(self.capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(self.capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        actual_fps = self.capture.get(cv2.CAP_PROP_FPS) or fps
        fourcc = int(self.capture.get(cv2.CAP_PROP_FOURCC))
        codec = ''.join([chr((fourcc >> 8 * i) & 0xFF) for i in range(4)]) if fourcc else "未知"

        print(f"[RTSP] 已连接: {actual_w}x{actual_h}, FPS: {actual_fps}, 编码: {codec}, URL: {safe_url}")

        # 验证能否实际读取帧（RTSP 首帧可能需要等待 I 帧）
        frame_ok = False
        for i in range(30):
            ret, frame = self.capture.read()
            if ret:
                print(f"[RTSP] 验证读帧成功 (第{i+1}次尝试), 帧尺寸: {frame.shape}")
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

        self.source_type = 'rtsp'
        self.rtsp_url = url
        self.width = actual_w
        self.height = actual_h
        self.fps = fps
        self.is_running = True

        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        return True

    # ========== 海康设备网络SDK (HCNetSDK) ==========

    def start_hcnetsdk(self, ip: str, port: int = 8000,
                       username: str = "admin", password: str = "",
                       channel: int = 1, stream_type: int = 1,
                       fps: int = 25):
        """
        通过海康设备网络SDK连接NVR/IP摄像头。
        使用海康私有协议，比RTSP更稳定。

        Parameters
        ----------
        ip : str          设备IP地址
        port : int        SDK端口 (默认 8000)
        username : str    用户名
        password : str    密码
        channel : int     通道号 (1-based, NVR 数字通道从 startDChan 开始)
        stream_type : int 0=主码流, 1=子码流 (子码流性能更好)
        fps : int         目标帧率
        """
        if not HCNET_SDK_AVAILABLE:
            raise Exception(
                "HCNetSDK not available. "
                "Place SDK DLLs in backend/hcnetsdk/lib/"
            )

        self.stop(release_model=False)
        time.sleep(0.2)

        print(f"[HCNetSDK] connecting {ip}:{port} ch={channel} "
              f"stream={'sub' if stream_type else 'main'} ...")

        try:
            session = HCNetSession()
            session.login(ip, port, username, password)

            ch_info = session.get_channel_info()
            actual_channel = channel
            if ch_info and ch_info['ip_channels'] > 0 and channel <= ch_info['ip_channels']:
                actual_channel = ch_info['start_digital_channel'] + (channel - 1)
                print(f"[HCNetSDK] channel map: {channel} -> {actual_channel}")

            session.start_preview(
                channel=actual_channel,
                stream_type=stream_type,
                link_mode=0,  # TCP
            )

            # 等待第一帧解码
            first_frame = None
            for i in range(50):
                first_frame = session.get_frame(timeout=0.5)
                if first_frame is not None:
                    h, w = first_frame.shape[:2]
                    print(f"[HCNetSDK] first frame OK ({w}x{h}), attempt {i+1}")
                    break

            if first_frame is None:
                session.cleanup()
                raise Exception(
                    f"HCNetSDK connected but no frames. "
                    f"Check channel={channel} or switch main/sub stream."
                )

            h, w = first_frame.shape[:2]
            self.hcnet_session = session
            self.hcnet_ip = ip
            self.hcnet_port = port
            self.hcnet_username = username
            self.hcnet_password = password
            self.hcnet_channel = channel
            self.source_type = 'hcnetsdk'
            self.width = w
            self.height = h
            self.fps = fps
            self.is_running = True

            self._thread = threading.Thread(target=self._capture_loop, daemon=True)
            self._thread.start()
            print(f"[HCNetSDK] connected: {w}x{h} fps={fps}")
            return True

        except ConnectionError as e:
            raise Exception(str(e))
        except Exception as e:
            print(f"[HCNetSDK] start failed: {e}")
            import traceback; traceback.print_exc()
            raise

    def _get_hcnetsdk_frame(self):
        """Get latest decoded frame from HCNetSDK session."""
        if self.hcnet_session is None:
            return None
        try:
            return self.hcnet_session.get_frame(timeout=1.0)
        except Exception as e:
            print(f"[HCNetSDK] frame error: {e}")
            return None

    def _release_hcnet_session(self):
        """Release HCNetSDK session resources."""
        if self.hcnet_session is not None:
            try:
                self.hcnet_session.cleanup()
            except Exception as e:
                print(f"[HCNetSDK] cleanup error: {e}")
            self.hcnet_session = None

    def _reconnect_hcnetsdk(self):
        """Reconnect HCNetSDK using saved connection params."""
        if not HCNET_SDK_AVAILABLE or not self.hcnet_ip:
            raise Exception("Cannot reconnect: SDK unavailable or no params")
        session = HCNetSession()
        session.login(self.hcnet_ip, self.hcnet_port,
                      self.hcnet_username, self.hcnet_password)
        ch_info = session.get_channel_info()
        actual_channel = self.hcnet_channel
        if ch_info and ch_info['ip_channels'] > 0:
            actual_channel = ch_info['start_digital_channel'] + (self.hcnet_channel - 1)
        session.start_preview(channel=actual_channel, stream_type=1, link_mode=0)
        frame = session.get_frame(timeout=5.0)
        if frame is None:
            session.cleanup()
            raise Exception("Reconnect OK but no frames")
        self.hcnet_session = session

    def start_hikvision_camera(self, device_index: int = 0, width: int = 1280, height: int = 720, fps: int = 60):
        """启动海康工业相机（带增强调试）"""
        hik_log(f"start_hikvision_camera 调用: device_index={device_index}, {width}x{height}@{fps}fps")
        
        if not HIK_SDK_AVAILABLE:
            hik_log("SDK 不可用", "ERROR")
            raise Exception("海康 SDK 未加载，无法使用海康相机")
        
        self.stop(release_model=False)
        
        # 等待一小段时间确保之前的资源已释放
        time.sleep(0.2)
        
        try:
            # 创建相机实例
            hik_log("创建 MvCamera 实例...")
            self.hik_camera = MvCamera()
            
            # 枚举设备
            hik_log("枚举设备...")
            device_list = MV_CC_DEVICE_INFO_LIST()
            tlayer_type = MV_USB_DEVICE | MV_GIGE_DEVICE
            ret = MvCamera.MV_CC_EnumDevices(tlayer_type, device_list)
            hik_log(f"枚举结果: ret={hex(ret)}, 设备数={device_list.nDeviceNum}")
            
            if ret != 0 or device_list.nDeviceNum == 0:
                raise Exception(f"未发现海康相机设备 (ret={hex(ret)})")
            
            if device_index < 0 or device_index >= device_list.nDeviceNum:
                raise Exception(f"无效的设备索引 {device_index}，当前共 {device_list.nDeviceNum} 个设备")
            
            # 获取选定设备信息
            st_device_info = cast(device_list.pDeviceInfo[device_index], POINTER(MV_CC_DEVICE_INFO)).contents
            hik_log(f"选择设备 {device_index}, 类型={st_device_info.nTLayerType}")
            
            # 创建句柄
            hik_log("创建句柄...")
            ret = self.hik_camera.MV_CC_CreateHandle(st_device_info)
            if ret != 0:
                raise Exception(f"创建相机句柄失败，错误码: {hex(ret)}")
            hik_log("句柄创建成功")
            
            # 打开设备（独占模式）
            hik_log("打开设备...")
            ret = self.hik_camera.MV_CC_OpenDevice(MV_ACCESS_Exclusive, 0)
            if ret != 0:
                self.hik_camera.MV_CC_DestroyHandle()
                raise Exception(f"打开相机失败，错误码: {hex(ret)}")
            hik_log("设备打开成功")
            
            # 设置为连续采集模式
            hik_log("设置触发模式...")
            ret = self.hik_camera.MV_CC_SetEnumValue("TriggerMode", MV_TRIGGER_MODE_OFF)
            if ret != 0:
                hik_log(f"设置触发模式失败: {hex(ret)}，继续运行", "WARN")
            else:
                hik_log("触发模式设置成功 (连续采集)")
            
            # 获取 PayloadSize（帧数据大小）
            hik_log("获取 PayloadSize...")
            st_param = MVCC_INTVALUE()
            memset(byref(st_param), 0, sizeof(MVCC_INTVALUE))
            ret = self.hik_camera.MV_CC_GetIntValue("PayloadSize", st_param)
            if ret != 0:
                self._release_hik_camera()
                raise Exception(f"获取 PayloadSize 失败，错误码: {hex(ret)}")
            self.hik_payload_size = st_param.nCurValue
            hik_log(f"PayloadSize = {self.hik_payload_size} bytes")
            
            # 开始取流
            hik_log("开始取流...")
            ret = self.hik_camera.MV_CC_StartGrabbing()
            if ret != 0:
                self._release_hik_camera()
                raise Exception(f"开始取流失败，错误码: {hex(ret)}")
            hik_log("取流开始成功")
            
            # 预分配缓冲区
            hik_log("分配缓冲区...")
            self.hik_data_buf = (c_ubyte * self.hik_payload_size)()
            self.hik_frame_info = MV_FRAME_OUT_INFO_EX()
            memset(byref(self.hik_frame_info), 0, sizeof(MV_FRAME_OUT_INFO_EX))
            hik_log("缓冲区分配成功")
            
            # 重置帧计数相关标志
            if hasattr(self, '_hik_first_frame_logged'):
                delattr(self, '_hik_first_frame_logged')
            if hasattr(self, '_hik_frame_error_count'):
                self._hik_frame_error_count = 0
            
            # 设置属性
            self.source_type = 'hikvision'
            self.hik_device_index = device_index
            self.width = width
            self.height = height
            self.fps = fps
            self.is_running = True
            
            hik_log(f"海康相机初始化完成: 设备{device_index}", "SUCCESS")
            
            # 启动捕获线程
            self._thread = threading.Thread(target=self._capture_loop, daemon=True)
            self._thread.start()
            hik_log("捕获线程已启动")
            
            return True
            
        except Exception as e:
            hik_log(f"启动失败: {e}", "ERROR")
            import traceback
            hik_log(traceback.format_exc(), "ERROR")
            self._release_hik_camera()
            raise Exception(f"启动海康相机失败: {str(e)}")
    
    def _release_hik_camera(self):
        """释放海康相机资源"""
        if self.hik_camera:
            try:
                self.hik_camera.MV_CC_StopGrabbing()
            except:
                pass
            try:
                self.hik_camera.MV_CC_CloseDevice()
            except:
                pass
            try:
                self.hik_camera.MV_CC_DestroyHandle()
            except:
                pass
            self.hik_camera = None
            self.hik_data_buf = None
            self.hik_frame_info = None
            print("[海康相机] 已释放")
    
    def _get_hikvision_frame(self):
        """从海康相机获取一帧图像 - 带调试日志"""
        if not self.hik_camera or not HIK_SDK_AVAILABLE:
            return None
        
        try:
            # 与参考代码完全一致的帧获取方式
            stFrameInfo = MV_FRAME_OUT_INFO_EX()
            memset(byref(stFrameInfo), 0, sizeof(stFrameInfo))
            pData = (c_ubyte * (2048 * 2048 * 3))()
            
            t_grab_start = time.time()
            ret = self.hik_camera.MV_CC_GetOneFrameTimeout(byref(pData), sizeof(pData), stFrameInfo, 1000)
            t_grab_end = time.time()
            grab_time = (t_grab_end - t_grab_start) * 1000
            
            if ret != 0:
                if grab_time > 800:  # 接近超时
                    debug_log(f"!!! 帧获取超时: ret={hex(ret)}, 耗时={grab_time:.1f}ms", "HIK")
                return None
            
            if grab_time > 100:
                debug_log(f"帧获取耗时: {grab_time:.1f}ms", "HIK")
            
            frame_width = stFrameInfo.nWidth
            frame_height = stFrameInfo.nHeight
            pixel_type = stFrameInfo.enPixelType
            
            # 首帧详细信息
            if not hasattr(self, '_hik_first_frame_logged'):
                hik_log(f"首帧: {frame_width}x{frame_height}, 像素={hex(pixel_type)}, 长度={stFrameInfo.nFrameLen}", "SUCCESS")
                self._hik_first_frame_logged = True
            
            # 与参考代码 LG.PY 第941-961行完全一致的转换逻辑
            t_convert_start = time.time()
            if pixel_type == PixelType_Gvsp_RGB8_Packed:
                frame = np.frombuffer(pData, dtype=np.uint8, count=frame_width * frame_height * 3)
                frame = frame.reshape((frame_height, frame_width, 3))
            else:
                # SDK 像素转换
                stConvertParam = MV_CC_PIXEL_CONVERT_PARAM()
                memset(byref(stConvertParam), 0, sizeof(stConvertParam))
                stConvertParam.nWidth = stFrameInfo.nWidth
                stConvertParam.nHeight = stFrameInfo.nHeight
                stConvertParam.pSrcData = cast(pData, POINTER(c_ubyte))
                stConvertParam.nSrcDataLen = stFrameInfo.nFrameLen
                stConvertParam.enSrcPixelType = stFrameInfo.enPixelType
                stConvertParam.enDstPixelType = PixelType_Gvsp_RGB8_Packed
                nConvertSize = stFrameInfo.nWidth * stFrameInfo.nHeight * 3
                pConvertData = (c_ubyte * nConvertSize)()
                stConvertParam.pDstBuffer = cast(pConvertData, POINTER(c_ubyte))
                stConvertParam.nDstBufferSize = nConvertSize
                
                t_sdk_convert_start = time.time()
                ret = self.hik_camera.MV_CC_ConvertPixelType(stConvertParam)
                t_sdk_convert_end = time.time()
                sdk_convert_time = (t_sdk_convert_end - t_sdk_convert_start) * 1000
                
                if ret != 0:
                    if not hasattr(self, '_convert_err_logged'):
                        hik_log(f"SDK转换失败: {hex(ret)}", "ERROR")
                        self._convert_err_logged = True
                    return None
                
                if sdk_convert_time > 50:
                    debug_log(f"SDK像素转换耗时: {sdk_convert_time:.1f}ms", "HIK")
                
                frame = np.frombuffer(pConvertData, dtype=np.uint8, count=nConvertSize)
                frame = frame.reshape((frame_height, frame_width, 3))
                
                # 首次转换成功时记录详细信息
                if not hasattr(self, '_hik_convert_logged'):
                    hik_log(f"SDK转换成功", "SUCCESS")
                    self._hik_convert_logged = True
            
            t_convert_end = time.time()
            total_convert_time = (t_convert_end - t_convert_start) * 1000
            if total_convert_time > 100:
                debug_log(f"!!! 整体转换耗时: {total_convert_time:.1f}ms", "HIK")
            
            # 与参考代码 LG.PY 第1576-1578行一致: RGB -> BGR
            frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
            return frame
            
        except Exception as e:
            if not hasattr(self, '_hik_err_logged'):
                hik_log(f"获取帧异常: {e}", "ERROR")
                self._hik_err_logged = True
            return None
    
    def start_video(self, video_path: str, speed: float = None):
        """启动视频文件播放"""
        # 保存当前倍速设置（如果有的话）
        current_speed = self.video_speed if self.video_speed else 1.0
        
        self.stop(release_model=False)
        
        if not os.path.exists(video_path):
            raise Exception(f"视频文件不存在: {video_path}")
        
        self.capture = cv2.VideoCapture(video_path)
        if not self.capture.isOpened():
            raise Exception(f"无法打开视频文件: {video_path}")
        
        self.source_type = 'video'
        self.video_path = video_path
        self.fps = self.capture.get(cv2.CAP_PROP_FPS) or 30
        self.width = int(self.capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.height = int(self.capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        
        # 视频帧信息
        self.video_total_frames = int(self.capture.get(cv2.CAP_PROP_FRAME_COUNT))
        self.video_current_frame = 0
        self.video_ended = False
        # 使用传入的倍速，如果没有传入则保持之前的倍速
        self.video_speed = speed if speed is not None else current_speed
        print(f"视频总帧数: {self.video_total_frames}, 倍速: {self.video_speed}x")
        
        self.is_running = True
        
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        
        return True
    
