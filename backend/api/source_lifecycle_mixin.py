"""生命周期控制 + 资源清理 (v2.7.16 P6 阶段一第十五刀)。

把 11 个公共控制 API + 资源清理方法集中到一处 (合计 ~410 行):
  pause / resume / standby / resume_inference / stop  : 公共控制 API
  _reopen_camera / _reopen_hik_camera                 : 重连辅助
  _clear_all_caches / _save_counters_snapshot         : 资源/状态清理
  _gpu_deep_cleanup / _periodic_cache_cleanup         : GPU 内存维护

依赖宿主 (VideoSourceManager):
  - 状态: is_running / is_detecting / cap / hcnet_session / hik_camera /
          source_type / model / counters / current_session_id / etc.
  - 方法: _close_all_writers / _stop_inference_thread / _stop_recording_thread /
          _release_hcnet_session / _release_hik_camera / debug_log

v3.13 M1.1 末项 (2026-05-28): source_status_change hook 通过 _track_status_change
contextmanager 接入 — 5 个 lifecycle 公共方法 + capture_loop 3 处异常中断点都会
fire. 仅在 (is_running, is_detecting) 真发生变化时 fire, 无变化静默 (防同状态再
赋值 / early return False 干扰).
"""
import os
import gc
import time
import json
import threading
import traceback
from contextlib import contextmanager
from ctypes import POINTER, byref, c_ubyte, cast, memset, sizeof

import cv2

from backend.api.source_camera_start_mixin import (
    _apply_exposure_setting,
    _camera_backend_info,
)

# v3.8.x: _reopen_hik_camera 用到的海康 SDK 名字 ── 历史遗漏 import 导致
# "name 'HIK_SDK_AVAILABLE' is not defined" NameError, 海康相机用户每次 pause→resume
# (前端"停止→开始"按钮) 都报"启动检测失败"。客户报障: 只能去"输入源"页面重启才能恢复。
# 同名方法在 IndustrialCameraMixin (line ~397) 也有一份, 但该 mixin 未在 VideoSourceManager
# 继承列表里, 实际生效的是本文件这份, 必须补全 import。
from backend.api.source_sdk_loader import (
    HIK_SDK_AVAILABLE,
    MV_ACCESS_Exclusive,
    MV_CC_DEVICE_INFO,
    MV_CC_DEVICE_INFO_LIST,
    MV_FRAME_OUT_INFO_EX,
    MV_GIGE_DEVICE,
    MV_TRIGGER_MODE_OFF,
    MV_USB_DEVICE,
    MVCC_INTVALUE,
    MvCamera,
)
from backend.core import debug_center


class LifecycleMixin:
    # ============================================================
    # v3.13 M1.1 末项: source_status_change hook 基础设施
    # ============================================================

    def _fire_source_status_change(
        self,
        before_running: bool,
        before_detecting: bool,
        reason: str,
    ) -> None:
        """触发 source_status_change hook (仅在状态真变化时 fire).

        语义: 检测/采集状态机的转换通知. 插件可挂在这里做:
          - 工位"开始/停止"通知 (推送给客户 MES)
          - 视频流断开后联动报警
          - 多工位 standby 时同步关闭对应硬件

        Args:
            before_running: 调用前的 is_running 值
            before_detecting: 调用前的 is_detecting 值
            reason: 触发源, 枚举:
                'pause' / 'resume' / 'standby' / 'resume_inference' / 'stop'
                / 'capture_loop_video_ended' / 'capture_loop_reopen_failed'
                / 'capture_loop_recover_failed'

        无变化静默: before == after 时不 fire (防 pause 内"已经停了再 pause" /
        resume early return False 时虚报状态变化).

        异常隔离: hook fire 异常被 swallow, 主流程继续 (lifecycle 不能因插件 bug 卡死).
        """
        after_running = bool(self.is_running)
        after_detecting = bool(self.is_detecting)
        before_running = bool(before_running)
        before_detecting = bool(before_detecting)

        if before_running == after_running and before_detecting == after_detecting:
            return

        try:
            from backend.plugin_system.hook_dispatch import fire_plugin_hook
            fire_plugin_hook("source_status_change", "post_status_change", "post", {
                "channel_id": self.channel_id,
                "before": {
                    "is_running": before_running,
                    "is_detecting": before_detecting,
                },
                "after": {
                    "is_running": after_running,
                    "is_detecting": after_detecting,
                },
                "reason": reason,
                "source_type": self.source_type,
            })
        except Exception as e:
            print(f"[Plugin] source_status_change hook error (isolated, main flow continues): {e}")

    @contextmanager
    def _track_status_change(self, reason: str):
        """contextmanager 备用方案 — finally 自动 fire source_status_change.

        本批次 (v3.13 M1.1 末项) 5 个 lifecycle 公共方法采用更轻量的 begin/end
        模式 (开头录 before, 出口手动调 helper); 本 contextmanager 留作:
          - 未来新增 lifecycle 方法的备用接入方式
          - 第三方代码 (如 hotfix / 客户定制) 需要包装含异常路径的状态变更时使用

        语义保证:
          - finally 路径 fire: 包括正常返回 / early return / 抛异常 → 都会 fire
          - 状态无变化静默 (helper 内置 dedup)
          - 异常不被 swallow: 与 hook fire 解耦, 异常仍上抛
        """
        before_running = bool(self.is_running)
        before_detecting = bool(self.is_detecting)
        try:
            yield
        finally:
            self._fire_source_status_change(before_running, before_detecting, reason)

    def pause(self):
        """暂停：停止画面更新和检测，但保持当前帧"""
        _before_running, _before_detecting = self.is_running, self.is_detecting
        debug_center.dbg("backend.source", "pause 入口", f"channel={self.channel_id} source_type={self.source_type} running={_before_running}→False detecting={_before_detecting}→False")
        self.is_running = False
        self.is_detecting = False
        # v2.7.3: 暂停也必须熄灭工作指示灯，前端 Monitor 的"停止"按钮调的是 pause
        # 之前未调用导致灯保持常亮，关软件后还亮
        try:
            from backend.api.alarm import alarm_router
            alarm_router.stop_idle_light(channel_id=self.channel_id)
        except Exception:
            pass
        # 先停止推理线程，避免残留
        self._stop_inference_thread()
        # 停止录制线程和 FFmpeg 进程，防止资源泄漏
        self._stop_recording_thread()
        self._close_all_writers()
        # 等待捕获线程退出
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        with self.detection_lock:
            self.current_detections = []
        self._video_hold = False   # v3.44.2: 暂停即收工路径, 不留待机播放冻结
        # 清理推理缓存
        self._clear_inference_caches()

        # Camera/Hikvision: release the device so it's not locked
        # (current_frame is kept for frozen display, model stays loaded for fast resume)
        if self.source_type == 'camera' and self.capture:
            try:
                self.capture.release()
            except Exception as e:
                print(f"[pause] release camera failed: {e}")
                debug_center.dbg("backend.source", "pause 释放摄像头异常", f"channel={self.channel_id} err={e}")
            self.capture = None
            print("[Pause] camera released, keeping model and frame")
        elif self.source_type == 'hikvision':
            self._release_hik_camera()
            print("[Pause] Hikvision camera released, keeping model and frame")
        elif self.source_type == 'hcnetsdk':
            self._release_hcnet_session()
            print("[pause] HCNetSDK released, model kept")
        else:
            print("[Pause] frame and detection both stopped")
        self._fire_source_status_change(_before_running, _before_detecting, "pause")
    
    def _reopen_camera(self):
        """Re-open USB camera that was released during pause"""
        import platform
        try:
            saved_backend = getattr(self, '_camera_backend', None)
            if platform.system() == "Windows":
                backend = saved_backend if saved_backend is not None else cv2.CAP_DSHOW
                self.capture = cv2.VideoCapture(self.camera_index, backend)
            else:
                self.capture = cv2.VideoCapture(self.camera_index)
            if not self.capture.isOpened():
                print(f"[resume] camera {self.camera_index} open failed")
                self.capture = None
                return False
            self.capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc('M','J','P','G'))
            self.capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            self.capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            self.capture.set(cv2.CAP_PROP_FPS, self.fps)
            _apply_exposure_setting(
                self.capture,
                getattr(self, '_auto_exposure', True),
                getattr(self, '_exposure_value', -6.0),
                context='resume_reopen',
            )
            actual_backend, backend_name = _camera_backend_info(self.capture)
            self._camera_backend = actual_backend
            print(
                f"[resume] camera reopened: index={self.camera_index} "
                f"backend={backend_name}({actual_backend}) "
                f"auto_exposure={getattr(self, '_auto_exposure', True)} "
                f"exposure={getattr(self, '_exposure_value', -6.0)}"
            )
            return True
        except Exception as e:
            print(f"[resume] reopen camera failed: {e}")
            debug_center.dbg("backend.source", "resume 重开摄像头异常", f"channel={self.channel_id} index={self.camera_index} err={e}")
            return False

    def _reopen_hik_camera(self):
        """Re-open Hikvision camera that was released during pause (preserves model)"""
        if not HIK_SDK_AVAILABLE:
            print("[resume] Hikvision SDK unavailable")
            return False
        try:
            self.hik_camera = MvCamera()
            device_list = MV_CC_DEVICE_INFO_LIST()
            ret = MvCamera.MV_CC_EnumDevices(MV_USB_DEVICE | MV_GIGE_DEVICE, device_list)
            if ret != 0 or device_list.nDeviceNum == 0:
                raise Exception("未发现海康相机设备")
            if self.hik_device_index >= device_list.nDeviceNum:
                raise Exception(f"设备索引 {self.hik_device_index} 无效")
            st_device_info = cast(device_list.pDeviceInfo[self.hik_device_index], POINTER(MV_CC_DEVICE_INFO)).contents
            ret = self.hik_camera.MV_CC_CreateHandle(st_device_info)
            if ret != 0:
                raise Exception(f"创建句柄失败: {hex(ret)}")
            ret = self.hik_camera.MV_CC_OpenDevice(MV_ACCESS_Exclusive, 0)
            if ret != 0:
                self.hik_camera.MV_CC_DestroyHandle()
                raise Exception(f"打开设备失败: {hex(ret)}")
            self.hik_camera.MV_CC_SetEnumValue("TriggerMode", MV_TRIGGER_MODE_OFF)
            st_param = MVCC_INTVALUE()
            memset(byref(st_param), 0, sizeof(MVCC_INTVALUE))
            ret = self.hik_camera.MV_CC_GetIntValue("PayloadSize", st_param)
            if ret != 0:
                self._release_hik_camera()
                raise Exception(f"获取 PayloadSize 失败: {hex(ret)}")
            self.hik_payload_size = st_param.nCurValue
            ret = self.hik_camera.MV_CC_StartGrabbing()
            if ret != 0:
                self._release_hik_camera()
                raise Exception(f"开始取流失败: {hex(ret)}")
            self.hik_data_buf = (c_ubyte * self.hik_payload_size)()
            self.hik_frame_info = MV_FRAME_OUT_INFO_EX()
            memset(byref(self.hik_frame_info), 0, sizeof(MV_FRAME_OUT_INFO_EX))
            print(f"[resume] Hikvision camera reopened: index={self.hik_device_index}")
            return True
        except Exception as e:
            print(f"[resume] reopen Hikvision camera failed: {e}")
            debug_center.dbg("backend.source", "resume 重开海康相机异常", f"channel={self.channel_id} index={self.hik_device_index} err={e}")
            self._release_hik_camera()
            return False

    def resume(self):
        """恢复：从暂停状态恢复，重新启动视频流和推理"""
        _before_running, _before_detecting = self.is_running, self.is_detecting
        debug_center.dbg("backend.source", "resume 入口", f"channel={self.channel_id} source_type={self.source_type} running={_before_running} detecting={_before_detecting}")
        # Re-open camera if it was released during pause
        if self.capture is None and self.source_type == 'camera':
            if not self._reopen_camera():
                self._fire_source_status_change(_before_running, _before_detecting, "resume_failed")
                return False

        if self.source_type == 'hikvision' and self.hik_camera is None:
            if not self._reopen_hik_camera():
                self._fire_source_status_change(_before_running, _before_detecting, "resume_failed")
                return False

        if self.capture is None and self.source_type not in ('hikvision', 'image', 'synthetic'):
            print("[resume] cannot resume: no available video source")
            debug_center.dbg("backend.source", "resume 失败", f"channel={self.channel_id} source_type={self.source_type} 无可用视频源")
            self._fire_source_status_change(_before_running, _before_detecting, "resume_failed")
            return False

        # 确保旧捕获线程已完全停止，避免双重线程
        if self._thread and self._thread.is_alive():
            self.is_running = False
            self._thread.join(timeout=1.0)

        self.is_running = True
        self.is_detecting = True
        debug_center.dbg("backend.source", "resume 状态翻转", f"channel={self.channel_id} running/detecting → True")
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()

        if self.model is not None or self.source_type == 'synthetic':
            self._start_inference_thread()

        self._ensure_session_active()

        # v2.7.3: 恢复检测时重新点亮工作指示灯（pause 已熄，否则灯不会再亮）
        try:
            from backend.api.alarm import alarm_router
            alarm_router.start_idle_light(channel_id=self.channel_id)
        except Exception as _e:
            print(f"[Alarm/Source] resume start_idle_light failed: {_e}")

        # v2.7.5b: 从暂停恢复时同步唤醒扫码器（原代码仅在 start_detection 里调过，导致 resume 漏发 LON）
        try:
            from backend.services.scanner import get_scanner_service
            print(f"[Scanner/Source] resume ch={self.channel_id} → start_scanning")
            get_scanner_service().start_scanning(channel_id=self.channel_id)
        except Exception as _e:
            import traceback as _tb
            print(f"[Scanner/Source] resume start_scanning failed: {_e}\n{_tb.format_exc()}")

        # v3.5.2: 周期性强制动作 — 开机首检规则在每次"开始/恢复检测"时触发
        try:
            if hasattr(self, '_run_periodic_actions_on_start'):
                self._run_periodic_actions_on_start()
        except Exception as _e:
            print(f"[PeriodicActions] resume run_on_start trigger failed: {_e}")

        print("[resume] resumed: video stream and inference restarted")
        self._fire_source_status_change(_before_running, _before_detecting, "resume")
        return True
    
    def standby(self):
        """Standby: stop inference but keep the video capture thread running."""
        _before_running, _before_detecting = self.is_running, self.is_detecting
        debug_center.dbg("backend.source", "standby 入口", f"channel={self.channel_id} source_type={self.source_type} detecting={_before_detecting}→False")
        self.is_detecting = False
        # v2.7.3: 待机时也熄灭工作指示灯（语义上"不在检测"就不应该亮工作灯）
        try:
            from backend.api.alarm import alarm_router
            alarm_router.stop_idle_light(channel_id=self.channel_id)
        except Exception as _e:
            print(f"[Alarm/Source] standby stop_idle_light failed: {_e}")

        # v2.7.5b: 待机时关闭扫码器 LON
        try:
            from backend.services.scanner import get_scanner_service
            print(f"[Scanner/Source] standby ch={self.channel_id} → stop_scanning")
            get_scanner_service().stop_scanning(channel_id=self.channel_id)
        except Exception as _e:
            print(f"[Scanner/Source] standby stop_scanning failed: {_e}")

        # v3.21: 包装结算 — 待机时按策略收尾 (受 forced_settle_on_standby 控制,
        # 有的现场待机只是暂停画面不该结算). 无配置/无进行中工单时静默, 零差异.
        try:
            from backend.services.packaging_flow_coordinator import get_coordinator as _pkg_coord
            from backend.db.database import SessionLocal as _PkgSession
            _pkg_db = _PkgSession()
            try:
                _pkg_coord().on_forced_settle_by_channel(self.channel_id, _pkg_db, is_standby=True)
            finally:
                _pkg_db.close()
        except Exception as _e:
            print(f"[PackagingFlow/Source] standby cleanup failed (isolated): {_e}")

        self._stop_inference_thread()
        self._stop_recording_thread()
        self._close_all_writers()
        with self.detection_lock:
            self.current_detections = []
        # v3.44.1: 待机=临时暂停, 保留在制周期/箱内台账/挂起态, 只清帧级缓存;
        # 收工全清走「停止」(pause/stop_detection)。
        self._clear_inference_caches(keep_cycle=True)
        # v3.44.2: 视频源待机同时冻结播放位置 — 否则待机期间视频剧情被静默
        # 消耗, 恢复后"装盘早就播完了"检测线却全程没看见 (SY3 实测 3/4 盘全丢)。
        if self.source_type == 'video':
            self._video_hold = True
            print("[standby] video playback held at current position")
        print("[standby] detection stopped, frame continues (cycle kept)")
        self._fire_source_status_change(_before_running, _before_detecting, "standby")

    def resume_inference(self):
        """Resume inference from standby (capture thread already running)."""
        _before_running, _before_detecting = self.is_running, self.is_detecting
        debug_center.dbg("backend.source", "resume_inference 入口", f"channel={self.channel_id} source_type={self.source_type} running={_before_running} detecting={_before_detecting}→True")
        if not self.is_running:
            print("[resume_inference] video stream not running, cannot resume inference")
            self._fire_source_status_change(_before_running, _before_detecting, "resume_inference_failed")
            return False
        if self.model is None and self.source_type != 'synthetic':
            print("[resume_inference] model not loaded, cannot resume inference")
            self._fire_source_status_change(_before_running, _before_detecting, "resume_inference_failed")
            return False
        self._video_hold = False   # v3.44.2: 解除待机的视频播放冻结, 从停住的帧继续
        self.is_detecting = True
        self._start_inference_thread()
        
        self._ensure_session_active()
        
        if self.recording_enabled:
            self._start_recording_thread()
        self.start_session_recording()

        # v2.7.3: 从待机恢复推理时重新点亮工作指示灯
        try:
            from backend.api.alarm import alarm_router
            alarm_router.start_idle_light(channel_id=self.channel_id)
        except Exception as _e:
            print(f"[Alarm/Source] resume_inference start_idle_light failed: {_e}")

        # v2.7.5b: 从待机恢复时同步唤醒扫码器（关键修复——之前走 resume_inference 的路径永远不发 LON）
        try:
            from backend.services.scanner import get_scanner_service
            print(f"[Scanner/Source] resume_inference ch={self.channel_id} → start_scanning")
            get_scanner_service().start_scanning(channel_id=self.channel_id)
        except Exception as _e:
            import traceback as _tb
            print(f"[Scanner/Source] resume_inference start_scanning failed: {_e}\n{_tb.format_exc()}")

        # v3.5.2: 周期性强制动作 — 开机首检规则在每次"开始/恢复检测"时触发
        try:
            if hasattr(self, '_run_periodic_actions_on_start'):
                self._run_periodic_actions_on_start()
        except Exception as _e:
            print(f"[PeriodicActions] resume_inference run_on_start trigger failed: {_e}")

        print("[resume_inference] resumed inference from standby")
        self._fire_source_status_change(_before_running, _before_detecting, "resume_inference")
    
    def stop(self, release_model: bool = True):
        """停止当前输入源（完全停止并释放资源）
        
        Args:
            release_model: If False, keep the YOLO model in memory for reuse
                           after switching input sources.
        """
        _before_running, _before_detecting = self.is_running, self.is_detecting
        debug_center.dbg("backend.source", "stop 入口", f"channel={self.channel_id} source_type={self.source_type} release_model={release_model} running={_before_running}→False")
        self.is_running = False
        self.is_detecting = False
        
        # 停止推理线程
        self._stop_inference_thread()
        
        # 关闭推理线程池
        self._shutdown_inference_executor()
        
        # 停止录制线程
        self._stop_recording_thread()
        
        # 等待捕获线程结束（多次尝试）
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
            # 如果线程还在运行，再等待一次
            if self._thread.is_alive():
                print("[WARN] capture thread first timeout, waiting again...")
                self._thread.join(timeout=2.0)
            # 如果还是没有结束，记录警告
            if self._thread.is_alive():
                print("[错误] 捕获线程未能结束，可能存在死锁，强制继续")
        
        # Release HCNetSDK
        if self.source_type == 'hcnetsdk':
            self._release_hcnet_session()
        
        # 释放海康相机资源
        if self.source_type == 'hikvision':
            self._release_hik_camera()
        
        # 释放摄像头/视频资源
        if self.capture:
            try:
                self.capture.release()
            except Exception as e:
                print(f"[WARN] error releasing camera: {e}")
            self.capture = None
        
        if release_model:
            self._release_model()
        else:
            self._shutdown_inference_executor()
            print("[VideoManager] keeping model, only stopping input source")
        
        # 等待一小段时间确保资源被系统释放
        time.sleep(0.3)
        
        self.source_type = None
        self.current_frame = None
        self._thread = None
        with self.detection_lock:
            self.current_detections = []
        
        # 清理所有内存缓存
        self._clear_all_caches()
        
        print("[VideoManager] fully stopped and released resources")
        self._fire_source_status_change(_before_running, _before_detecting, "stop")
    
    def _clear_all_caches(self):
        """清理所有内存缓存 - 防止内存泄漏"""
        import gc
        
        print("[CacheClean] starting memory cache cleanup...")
        
        # 1. 清理卡尔曼滤波器缓存
        self._kalman_filters.clear()
        self._detection_missing_frames.clear()
        self._detection_history.clear()
        
        # 2. 清理步骤截图缓存（这个可能很大！）
        screenshot_count = len(self.step_screenshots)
        self.step_screenshots.clear()
        
        # 3. 限制事件日志大小（保留最近500条）
        if len(self.events_log) > 500:
            self.events_log = self.events_log[-500:]
        
        # 4. 清理推理相关缓存
        with self._inference_frame_lock:
            self._latest_frame_for_inference = None
            self._latest_frame_original_size = None
            self._latest_display_small_for_stats = None
            self._latest_synthetic_inference_idx = -1
        with self._confirmed_detections_lock:
            self._confirmed_detections = []
        
        # 5. 清理帧计数缓存
        self.step_consecutive_frames.clear()
        self.step_frame_confirmed.clear()
        self.step_static_triggered.clear()
        
        # 6. 限制周期时间记录（保留最近50条）
        if len(self.cycle_times) > 50:
            self.cycle_times = self.cycle_times[-50:]
        if len(self.ng_cycle_times) > 50:
            self.ng_cycle_times = self.ng_cycle_times[-50:]
        for _lbl in list(self.step_durations_history.keys()):
            if len(self.step_durations_history[_lbl]) > 100:
                self.step_durations_history[_lbl] = self.step_durations_history[_lbl][-100:]
        # v3.5.x: 同步封顶 PT 合并档 history
        for _lbl in list(self.step_cycle_durations_history.keys()):
            if len(self.step_cycle_durations_history[_lbl]) > 100:
                self.step_cycle_durations_history[_lbl] = self.step_cycle_durations_history[_lbl][-100:]
        
        # 7. 强制垃圾回收
        gc.collect()
        
        # 8. 清理 GPU 缓存 (CUDA / MPS)
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                allocated = torch.cuda.memory_allocated() / 1024**2
                cached = torch.cuda.memory_reserved() / 1024**2
                print(f"[CacheClean] GPU VRAM: allocated={allocated:.1f}MB, cached={cached:.1f}MB")
            else:
                from backend.core.torch_device import empty_mps_cache
                empty_mps_cache()
        except Exception as e:
            print(f"[CacheClean] error clearing GPU cache: {e}")
        
        print(f"[CacheClean] done - cleared {screenshot_count} screenshot caches")
    
    # _save_counters_snapshot 已迁至 source_counters.py (P7 第三刀)
    # 历史调用 self._save_counters_snapshot() 通过 VSM.__getattr__ 转发

    
    def _gpu_deep_cleanup(self):
        """GPU 显存深度清理 - 每10分钟执行一次，防止长时间运行显存碎片累积"""
        import gc
        try:
            import torch
            if torch.cuda.is_available():
                before_alloc = torch.cuda.memory_allocated() / 1024**2
                before_cached = torch.cuda.memory_reserved() / 1024**2
                
                gc.collect()
                torch.cuda.empty_cache()
                
                after_alloc = torch.cuda.memory_allocated() / 1024**2
                after_cached = torch.cuda.memory_reserved() / 1024**2
                freed = before_cached - after_cached
                
                if freed > 1:
                    print(f"[GPUClean] freed VRAM: {freed:.1f}MB (allocated: {after_alloc:.1f}MB, cached: {after_cached:.1f}MB)")
            else:
                gc.collect()
                from backend.core.torch_device import empty_mps_cache
                empty_mps_cache()
        except Exception as e:
            print(f"[GPUClean] cleanup failed: {e}")
    
    def _periodic_cache_cleanup(self):
        """周期性缓存清理 - 在检测循环中定期调用"""
        import gc
        
        # 1. 限制事件日志大小
        if len(self.events_log) > 1000:
            self.events_log = self.events_log[-500:]
            print("[CacheClean] event log trimmed to 500 entries")
        
        # 2. 限制截图缓存（每个步骤只保留最新截图，这里额外检查总数）
        if len(self.step_screenshots) > 100:
            # 保留最后添加的50个
            keys = list(self.step_screenshots.keys())[-50:]
            self.step_screenshots = {k: self.step_screenshots[k] for k in keys}
            print("[CacheClean] screenshot cache trimmed to 50")
        
        # 3. 清理长时间未更新的卡尔曼滤波器
        current_time = time.time()
        stale_filters = [k for k, v in self._detection_missing_frames.items() 
                        if v > self._max_missing_frames * 2]
        for k in stale_filters:
            if k in self._kalman_filters:
                del self._kalman_filters[k]
            if k in self._detection_missing_frames:
                del self._detection_missing_frames[k]
        
        # 4. 轻量级垃圾回收
        gc.collect(generation=0)  # 只清理最年轻的一代，速度快
    
