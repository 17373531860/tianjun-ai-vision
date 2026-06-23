# coding=utf-8
"""
High-level wrapper for HCNetSDK + PlayCtrl.
Provides login, real-time preview with decoded frames as numpy arrays.
"""
from __future__ import annotations

import os
import sys
import ctypes
import threading
import numpy as np

from .types import (
    NET_DVR_DEVICEINFO_V30,
    NET_DVR_LOCAL_SDK_PATH,
    NET_DVR_LOCAL_GENERAL_CFG,
    NET_DVR_PREVIEWINFO,
    REALDATACALLBACK,
    DECCBFUN,
    NET_DVR_SYSHEAD,
    NET_DVR_STREAMDATA,
)

_WINDOWS = sys.platform == 'win32'


def _log(msg, level="INFO"):
    from datetime import datetime
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    print(f"[{ts}] [HCNetSDK/{level}] {msg}", flush=True)


class HCNetSession:
    """
    Manages a single HCNetSDK connection to an NVR or IP camera.
    Decoded BGR frames are available via get_frame().
    """

    _sdk_dll = None
    _play_dll = None
    _sdk_initialized = False
    _init_lock = threading.Lock()

    def __init__(self, sdk_dll_dir: str = None):
        self._user_id = -1
        self._play_handle = -1
        self._play_port = ctypes.c_long(-1)
        self._channel = 1

        self._frame: np.ndarray | None = None
        self._frame_lock = threading.Lock()
        self._frame_event = threading.Event()

        self._real_data_cb_ref = None
        self._decode_cb_ref = None

        # C8: 存活标志。stop_preview 一进来先置 False, native SDK 线程上仍在飞的
        #     解码/实时数据回调据此提前返回, 不再碰已释放的 play port(防 stop 后竞态崩溃)。
        self._alive = False

        self._device_info: NET_DVR_DEVICEINFO_V30 | None = None
        self._start_chan = 0

        self._ensure_sdk_loaded(sdk_dll_dir)

    # ------------------------------------------------------------------
    # SDK / DLL loading (class-level, happens once)
    # ------------------------------------------------------------------

    @classmethod
    def _ensure_sdk_loaded(cls, sdk_dll_dir: str = None):
        with cls._init_lock:
            if cls._sdk_initialized:
                return

            dll_dir = cls._resolve_dll_dir(sdk_dll_dir)
            if dll_dir is None:
                raise RuntimeError(
                    "HCNetSDK DLL not found. "
                    "Place SDK DLLs in backend/hcnetsdk/lib/ or set HCNETSDK_DIR env var."
                )

            _log(f"Loading DLLs from: {dll_dir}")

            old_cwd = os.getcwd()
            try:
                os.chdir(dll_dir)

                if _WINDOWS:
                    os.add_dll_directory(dll_dir)
                    com_dir = os.path.join(dll_dir, "HCNetSDKCom")
                    if os.path.isdir(com_dir):
                        os.add_dll_directory(com_dir)

                    cls._sdk_dll = ctypes.CDLL(os.path.join(dll_dir, "HCNetSDK.dll"))
                    cls._play_dll = ctypes.CDLL(os.path.join(dll_dir, "PlayCtrl.dll"))
                else:
                    sdk_so = os.path.join(dll_dir, "libhcnetsdk.so")
                    play_so = os.path.join(dll_dir, "libPlayCtrl.so")
                    if not os.path.isfile(sdk_so):
                        raise FileNotFoundError(f"Not found: {sdk_so}")
                    cls._sdk_dll = ctypes.cdll.LoadLibrary(sdk_so)
                    cls._play_dll = ctypes.cdll.LoadLibrary(play_so)
            finally:
                os.chdir(old_cwd)

            sdk_path_cfg = NET_DVR_LOCAL_SDK_PATH()
            if _WINDOWS:
                sdk_path_cfg.sPath = dll_dir.encode('gbk')
            else:
                sdk_path_cfg.sPath = dll_dir.encode('utf-8')
            cls._sdk_dll.NET_DVR_SetSDKInitCfg(2, ctypes.byref(sdk_path_cfg))

            if _WINDOWS:
                crypto_names = ["libcrypto-3-x64.dll", "libcrypto-1_1-x64.dll"]
                ssl_names = ["libssl-3-x64.dll", "libssl-1_1-x64.dll"]
                for name in crypto_names:
                    p = os.path.join(dll_dir, name)
                    if os.path.isfile(p):
                        cls._sdk_dll.NET_DVR_SetSDKInitCfg(
                            3, ctypes.create_string_buffer(p.encode('gbk')))
                        break
                for name in ssl_names:
                    p = os.path.join(dll_dir, name)
                    if os.path.isfile(p):
                        cls._sdk_dll.NET_DVR_SetSDKInitCfg(
                            4, ctypes.create_string_buffer(p.encode('gbk')))
                        break
            else:
                crypto = os.path.join(dll_dir, "libcrypto.so.1.1")
                ssl_lib = os.path.join(dll_dir, "libssl.so.1.1")
                if os.path.isfile(crypto):
                    cls._sdk_dll.NET_DVR_SetSDKInitCfg(
                        3, ctypes.create_string_buffer(crypto.encode('utf-8')))
                if os.path.isfile(ssl_lib):
                    cls._sdk_dll.NET_DVR_SetSDKInitCfg(
                        4, ctypes.create_string_buffer(ssl_lib.encode('utf-8')))

            cls._sdk_dll.NET_DVR_Init()
            cls._sdk_dll.NET_DVR_SetLogToFile(3, b'./hcnetsdk_log/', False)
            cls._sdk_dll.NET_DVR_SetReconnect(10000, 1)

            gen_cfg = NET_DVR_LOCAL_GENERAL_CFG()
            gen_cfg.byNotSplitRecordFile = 1
            cls._sdk_dll.NET_DVR_SetSDKLocalCfg(17, ctypes.byref(gen_cfg))

            cls._sdk_initialized = True
            _log("SDK initialized OK", "SUCCESS")

    @classmethod
    def _resolve_dll_dir(cls, hint: str = None) -> str | None:
        candidates = []
        if hint:
            candidates.append(hint)

        env = os.environ.get("HCNETSDK_DIR")
        if env:
            candidates.append(env)

        base = os.path.dirname(os.path.abspath(__file__))
        candidates.append(os.path.join(base, "lib"))

        if _WINDOWS:
            candidates.append(r"C:\HCNetSDK\lib")
            candidates.append(os.path.join(os.environ.get("ProgramFiles", ""), "HCNetSDK", "lib"))

        sdk_name = "HCNetSDK.dll" if _WINDOWS else "libhcnetsdk.so"
        for d in candidates:
            if d and os.path.isfile(os.path.join(d, sdk_name)):
                return os.path.abspath(d)
        return None

    @classmethod
    def sdk_available(cls) -> bool:
        return cls._sdk_initialized or cls._resolve_dll_dir() is not None

    # ------------------------------------------------------------------
    # Login / Logout
    # ------------------------------------------------------------------

    def login(self, ip: str, port: int = 8000,
              username: str = "admin", password: str = ""):
        if self._user_id >= 0:
            self.logout()

        dev_info = NET_DVR_DEVICEINFO_V30()
        uid = self._sdk_dll.NET_DVR_Login_V30(
            ctypes.create_string_buffer(ip.encode()),
            port,
            ctypes.create_string_buffer(username.encode()),
            ctypes.create_string_buffer(password.encode()),
            ctypes.byref(dev_info),
        )
        if uid < 0:
            err = self._sdk_dll.NET_DVR_GetLastError()
            raise ConnectionError(
                f"Login failed ({ip}:{port}), error={err}"
            )

        self._user_id = uid
        self._device_info = dev_info
        self._start_chan = dev_info.byStartChan
        ip_chan_num = dev_info.byIPChanNum + dev_info.byHighDChanNum * 256
        _log(f"Login OK: uid={uid}, analog={dev_info.byChanNum}, "
             f"startCh={dev_info.byStartChan}, "
             f"ipCh={ip_chan_num}, startDCh={dev_info.byStartDChan}")
        return uid

    def logout(self):
        if self._user_id >= 0:
            self.stop_preview()
            self._sdk_dll.NET_DVR_Logout(self._user_id)
            _log(f"Logout uid={self._user_id}")
            self._user_id = -1
            self._device_info = None

    # ------------------------------------------------------------------
    # Preview (real-time streaming)
    # ------------------------------------------------------------------

    def start_preview(self, channel: int = 1, stream_type: int = 0,
                      link_mode: int = 0):
        if self._user_id < 0:
            raise RuntimeError("Not logged in")

        self.stop_preview()
        self._channel = channel

        if not self._play_dll.PlayM4_GetPort(ctypes.byref(self._play_port)):
            raise RuntimeError("PlayM4_GetPort failed")

        preview = NET_DVR_PREVIEWINFO()
        ctypes.memset(ctypes.byref(preview), 0, ctypes.sizeof(preview))
        preview.lChannel = channel
        preview.dwStreamType = stream_type
        preview.dwLinkMode = link_mode
        preview.hPlayWnd = 0
        preview.bBlocked = 1

        self._real_data_cb_ref = REALDATACALLBACK(self._on_real_data)

        self._play_handle = self._sdk_dll.NET_DVR_RealPlay_V40(
            self._user_id,
            ctypes.byref(preview),
            self._real_data_cb_ref,
            None,
        )
        if self._play_handle < 0:
            err = self._sdk_dll.NET_DVR_GetLastError()
            self._free_play_port()
            raise RuntimeError(f"RealPlay failed, error={err}")

        self._alive = True  # C8: 预览成功后才放行回调处理
        _log(f"Preview started: handle={self._play_handle}, ch={channel}")

    def stop_preview(self):
        # C8: 先置死, 让仍在 native 线程上飞的回调提前返回, 再去停流/放端口
        self._alive = False
        if self._play_handle >= 0:
            self._sdk_dll.NET_DVR_StopRealPlay(self._play_handle)
            self._play_handle = -1

        if self._play_port.value >= 0:
            self._play_dll.PlayM4_Stop(self._play_port)
            self._play_dll.PlayM4_CloseStream(self._play_port)
            self._free_play_port()

        self._real_data_cb_ref = None
        self._decode_cb_ref = None

    # ------------------------------------------------------------------
    # Frame access
    # ------------------------------------------------------------------

    def get_frame(self, timeout: float = 1.0) -> np.ndarray | None:
        got = self._frame_event.wait(timeout=timeout)
        if not got:
            return None
        self._frame_event.clear()
        with self._frame_lock:
            return self._frame.copy() if self._frame is not None else None

    def get_latest_frame(self) -> np.ndarray | None:
        with self._frame_lock:
            return self._frame.copy() if self._frame is not None else None

    @property
    def is_previewing(self) -> bool:
        return self._play_handle >= 0

    @property
    def logged_in(self) -> bool:
        return self._user_id >= 0

    # ------------------------------------------------------------------
    # Callbacks (called from native SDK / PlayCtrl threads)
    # ------------------------------------------------------------------

    def _on_real_data(self, play_handle, data_type, p_buffer, buf_size, p_user):
        if not self._alive:  # C8: stop 后到达的回调直接丢弃, 不碰已释放端口
            return
        try:
            if data_type == NET_DVR_SYSHEAD:
                self._play_dll.PlayM4_SetStreamOpenMode(self._play_port, 0)
                ok = self._play_dll.PlayM4_OpenStream(
                    self._play_port, p_buffer, buf_size, 1024 * 1024
                )
                if not ok:
                    _log("PlayM4_OpenStream failed", "ERROR")
                    return

                self._decode_cb_ref = DECCBFUN(self._on_decode)
                self._play_dll.PlayM4_SetDecCallBackExMend(
                    self._play_port, self._decode_cb_ref, None, 0, None
                )
                self._play_dll.PlayM4_SetDecodeEngine(self._play_port, 0)

                if not self._play_dll.PlayM4_Play(self._play_port, None):
                    _log("PlayM4_Play failed", "ERROR")

            elif data_type == NET_DVR_STREAMDATA:
                self._play_dll.PlayM4_InputData(self._play_port, p_buffer, buf_size)

        except Exception as e:
            _log(f"real_data callback error: {e}", "ERROR")

    def _on_decode(self, port, p_buf, size, p_frame_info, user, reserved):
        if not self._alive:  # C8: stop 后到达的解码回调直接丢弃
            return
        try:
            fi = p_frame_info.contents
            w, h, frame_type = fi.nWidth, fi.nHeight, fi.nType

            if frame_type not in (3, 5) or w == 0 or h == 0:
                return

            expected_size = w * h * 3 // 2
            if size < expected_size:
                return

            yuv_bytes = ctypes.string_at(p_buf, expected_size)
            yuv_array = np.frombuffer(yuv_bytes, dtype=np.uint8).reshape(
                (h * 3 // 2, w)
            )

            import cv2
            if frame_type == 3:
                bgr = cv2.cvtColor(yuv_array, cv2.COLOR_YUV2BGR_YV12)
            else:
                bgr = cv2.cvtColor(yuv_array, cv2.COLOR_YUV2BGR_I420)

            with self._frame_lock:
                self._frame = bgr
            self._frame_event.set()

        except Exception as e:
            _log(f"decode error: {e}", "ERROR")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _free_play_port(self):
        if self._play_port.value >= 0:
            self._play_dll.PlayM4_FreePort(self._play_port)
            self._play_port = ctypes.c_long(-1)

    def get_channel_info(self):
        if self._device_info is None:
            return None
        di = self._device_info
        ip_chan_num = di.byIPChanNum + di.byHighDChanNum * 256
        return {
            "analog_channels": di.byChanNum,
            "start_channel": di.byStartChan,
            "ip_channels": ip_chan_num,
            "start_digital_channel": di.byStartDChan,
        }

    def cleanup(self):
        self.stop_preview()
        self.logout()

    def __del__(self):
        try:
            self.cleanup()
        except Exception:
            pass
