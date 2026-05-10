"""推理线程主循环 + 4 个辅助方法 (v2.7.16 P6 阶段一从 source.py 抽出)。

职责: 从 capture 线程派发的最新帧上跑模型 → 提取 confirmed → 发布到 detection_lock。
宿主必须提供: self.detection_enabled / running / model / project_config / fps_inference
          self.detection_lock / _confirmed_detections_lock / latest_frame / _frame_lock 等"""
from __future__ import annotations

import time
import traceback

from backend.api.source_sdk_loader import debug_log


class InferenceLoopMixin:
    # ===================== _inference_loop 拆分（v2.7.16 P5b） =====================
    # 原 161 行单体, 拆成主循环 + 4 个辅助方法:
    #   _inference_grab_latest_frame    : 取 capture 派发的最新帧 (含 frame_id 去重)
    #   _inference_select_and_run_model : 按 logic_mode 跑 detect_only / detect_and_track / detect_segment
    #   _inference_publish_detections   : 提取 confirmed → 发布到 detection_lock 和 _confirmed_detections_lock
    #   _inference_tick_fps             : 每秒更新一次 推理 FPS
    def _inference_grab_latest_frame(self, last_frame_id):
        """从 capture 线程派发的最新帧中取一份 (无新帧返回 (None, None, last_frame_id))。

        v2.7.14: frame = 原图小帧 (喂模型), display_small = 显示小帧 (stats 截图/画框)。
        无需 copy —— capture 线程每次迭代都新建 ndarray, 派发后不再变更。
        """
        t1 = time.time()
        with self._inference_frame_lock:
            frame = self._latest_frame_for_inference
            display_small = self._latest_display_small_for_stats
            frame_id = id(frame) if frame is not None else None
        t2 = time.time()
        if (t2 - t1) > 0.1:
            debug_log(f"!!! 获取帧锁耗时: {(t2-t1)*1000:.1f}ms", "INFERENCE")

        if frame is None or frame_id == last_frame_id:
            return (None, None, last_frame_id)
        return (frame, display_small, frame_id)

    def _inference_select_and_run_model(self, frame):
        """按 router 调度跑多模型 + 合并 detections + 坐标系映射.

        Step 5 (feat/multi-model-roi-link) 改造:
          - 老路径单模型时 router 只含 main, 行为完全等价
          - 多模型时 router.schedule_models_for_frame(frame_id) 决定本帧应跑哪些 mi
          - 主模型 (name='main') 按 project_config 的 task_type/logic_mode 选 runner
          - 副模型按 mi.model_task ('detect'/'segment') 选最简 runner (不参与 tracking)
          - detections 合并, 每条带 model_name/display_color (Step 4 注入)
          - is_tracking/is_seg 由主模型决定 (供下游 _update_tracking_stats / _update_step_stats)
          - per-mi fps_inference 累计 (mi.tick_fps), host fps_inference 在 _inference_tick_fps
            中同步为 main.fps_inference (老 UI/导出兼容)
        """
        t_start = time.time()
        if getattr(self, 'source_type', None) == 'synthetic':
            idx = int(getattr(self, '_latest_synthetic_inference_idx', -1))
            detections = self._synthetic_detections_for_frame_index(idx)
            detect_time = (time.time() - t_start) * 1000
            if detect_time > 200:
                debug_log(f"!!! synthetic 推理耗时: {detect_time:.1f}ms, 检测数={len(detections)}", "INFERENCE")
            if detections:
                detections = self._map_detections_original_to_display(detections)
            # 剧本注入固定走非跟踪 / 非分割路径（与 _update_step_stats 对齐）
            return (detections, False, False, t_start)

        # 主模型的 task_type / logic_mode (对副模型不适用)
        _task_type = self.project_config.get('task_type', 'detection') if self.project_config else 'detection'
        _logic_mode = self.project_config.get('logic_mode', 'sequential') if self.project_config else 'sequential'
        is_tracking = (_logic_mode == 'tracking')
        is_seg = (_task_type == 'segmentation')

        detections = self._run_models_for_frame(frame, t_start, is_tracking, is_seg)

        detect_time = (time.time() - t_start) * 1000
        if detect_time > 200:
            debug_log(f"!!! 推理耗时: {detect_time:.1f}ms, 检测数={len(detections) if detections else 0}", "INFERENCE")

        # v2.7.14: 把 detections 从 "原图坐标系" 映射到 "显示坐标系"
        # 让下游 ROI / 容器 / stats / 前端画框全部工作在显示坐标系
        if detections:
            detections = self._map_detections_original_to_display(detections)

        return (detections, is_tracking, is_seg, t_start)

    def _run_models_for_frame(self, frame, loop_start: float,
                               main_is_tracking: bool, main_is_seg: bool) -> list:
        """Step 5: 按 router 调度跑多模型, 串行调 runner, 合并 detections.

        参数:
          loop_start    : time.time() 刻度, 给每个跑过的 mi.tick_fps 用
          main_is_tracking / main_is_seg : 主模型 (name='main') 的项目级模式
        返回:
          detections list, 每条 dict 带 model_name + display_color (Step 4 注入)
        """
        router = getattr(self, '_router', None)
        if router is None or not router.models:
            # 极端 fallback: 没 router 时走老路径 (理论上 Step 2 之后不会到这里)
            if main_is_tracking:
                return self._detect_and_track(frame)
            if main_is_seg:
                return self._detect_segment(frame)
            return self._detect_only(frame)

        frame_id = id(frame)
        chosen = router.schedule_models_for_frame(frame_id)
        if not chosen:
            # 本帧所有 mi 都不应跑 (极小概率: 全是 every_n_frames + on_event 都未触发)
            return []

        all_detections = []
        for mi in chosen:
            if mi.model is None:
                continue  # mi 还没加载就跳过 (Step 6 配置 apply 后才加载)

            # 主模型: 走项目 logic_mode/task_type
            # 副模型: 按 mi.model_task 选最简 runner (不参与 tracking)
            if mi.name == 'main':
                if main_is_tracking:
                    dets = self._detect_and_track(frame, mi=mi)
                elif main_is_seg:
                    dets = self._detect_segment(frame, mi=mi)
                else:
                    dets = self._detect_only(frame, mi=mi)
            else:
                if mi.model_task == 'segment':
                    dets = self._detect_segment(frame, mi=mi)
                else:
                    dets = self._detect_only(frame, mi=mi)

            mi.tick_fps(loop_start)
            if dets:
                all_detections.extend(dets)

        return all_detections

    def _inference_publish_detections(self, detections, is_tracking):
        """提取 confirmed 检测结果并发布给前端 + 捕获线程 (双锁更新)。"""
        if is_tracking:
            confirmed = detections
            for det in confirmed:
                tid = det.get('track_id', -1)
                if tid in self._tracking_display_map:
                    det['display_id'] = self._tracking_display_map[tid]
        else:
            confirmed = self._get_confirmed_detections(detections)

        t7 = time.time()
        with self.detection_lock:
            self.current_detections = confirmed
        t8 = time.time()
        if (t8 - t7) > 0.1:
            debug_log(f"!!! 检测结果锁耗时: {(t8-t7)*1000:.1f}ms", "INFERENCE")

        # 同时更新 _confirmed_detections (供捕获线程使用)
        with self._confirmed_detections_lock:
            self._confirmed_detections = confirmed

    def _inference_tick_fps(self, loop_start):
        """更新推理 FPS 计数 (每秒一次)。

        v2.7.13 注: 跟踪/事件帧数阈值要按 fps_inference 换算, 不能用 fps_actual,
        因为 _update_tracking_stats / _update_step_stats 都在推理线程里累加帧数。

        Step 5 (feat/multi-model-roi-link):
          - per-mi fps_inference 由 mi.tick_fps 在 _run_models_for_frame 内累计
          - host.fps_inference 改为每秒同步为 main.fps_inference (兼容老 UI/导出)
          - host._fps_inference_counter 仍累 (语义: 主循环迭代次数, 用于双保险),
            如果 router 中没 main 时 fallback 到该计数
        """
        self._fps_inference_counter += 1
        if loop_start - self._fps_inference_time >= 1.0:
            router = getattr(self, '_router', None)
            main = router.get('main') if router is not None else None
            if main is not None and main.model is not None:
                # main 已加载: host.fps_inference 反映 main 真实 fps
                self.fps_inference = main.fps_inference
            else:
                # 没 main 或 main 没加载: fallback 老逻辑 (循环次数)
                self.fps_inference = self._fps_inference_counter
            self._fps_inference_counter = 0
            self._fps_inference_time = loop_start

    def _inference_loop(self):
        """独立推理线程 (v2.7.16 P5b 拆分版)。

        包含: 取帧 → 推理 → 帧计数验证 → 步骤判断 → 事件触发。
        本方法只剩调度骨架 + 周期清理 + 心跳, 实际工作分摊在 4 个辅助方法。
        """
        debug_log("========== 推理线程开始 ==========", "INFERENCE")
        print("[推理线程] 开始运行")
        last_frame_id = None
        frame_count = 0
        last_cleanup_time = time.time()
        cleanup_interval = 60.0          # 每 60 秒缓存清理
        last_gpu_cleanup_time = time.time()
        gpu_cleanup_interval = 600.0     # 每 10 分钟 GPU 深度清理
        last_log_time = time.time()
        log_interval = 10.0              # 每 10 秒打印诊断状态
        last_debug_time = time.time()    # 每 5 秒打印 debug log

        while self._inference_running and self.is_detecting:
            try:
                loop_start = time.time()
                self._last_inference_heartbeat = loop_start

                # 周期性 debug / 诊断日志
                if loop_start - last_debug_time > 5.0:
                    debug_log(f"帧数={frame_count}, 延迟={self.latency}ms, running={self._inference_running}, detecting={self.is_detecting}", "INFERENCE")
                    last_debug_time = loop_start
                if loop_start - last_log_time > log_interval:
                    print(f"[推理线程诊断] 帧数={frame_count}, 延迟={self.latency}ms, 运行中...")
                    last_log_time = loop_start

                # 周期性缓存清理 (60s) + GPU 深度清理 (600s)
                if loop_start - last_cleanup_time > cleanup_interval:
                    debug_log("开始周期性缓存清理...", "INFERENCE")
                    self._periodic_cache_cleanup()
                    debug_log("保存计数器快照...", "INFERENCE")
                    self._save_counters_snapshot()
                    last_cleanup_time = loop_start
                    debug_log("缓存清理完成", "INFERENCE")
                if loop_start - last_gpu_cleanup_time > gpu_cleanup_interval:
                    self._gpu_deep_cleanup()
                    last_gpu_cleanup_time = loop_start

                # 取最新帧 + 去重
                frame, display_small, frame_id = self._inference_grab_latest_frame(last_frame_id)
                if frame is None:
                    time.sleep(0.001)
                    continue
                last_frame_id = frame_id
                # original_frame 沿用历史命名, 指向 "显示坐标系下的缩小帧" —— 下游
                # _update_*_stats 用它生成步骤截图, 和前端看到的画面一致。
                original_frame = display_small if display_small is not None else frame
                frame_count += 1

                self._inference_tick_fps(loop_start)

                # 推理 + 坐标系映射
                detections, is_tracking, _is_seg, t_detect_start = self._inference_select_and_run_model(frame)

                # 更新步骤/跟踪统计 (original_frame 已是 display_small, 与 detections 坐标系一致)
                t5 = time.time()
                if is_tracking:
                    self._update_tracking_stats(detections, original_frame)
                else:
                    self._update_step_stats(detections, original_frame)
                update_time = (time.time() - t5) * 1000
                if update_time > 100:
                    debug_log(f"!!! 步骤统计耗时: {update_time:.1f}ms", "INFERENCE")

                self.latency = int((time.time() - t_detect_start) * 1000)

                # 发布 confirmed 结果
                self._inference_publish_detections(detections, is_tracking)

                # 推理节流: 每帧至少 5ms, 防止推理线程吃满 CPU
                loop_elapsed = time.time() - loop_start
                min_inference_interval = 0.005
                if loop_elapsed < min_inference_interval:
                    time.sleep(min_inference_interval - loop_elapsed)

            except Exception as e:
                debug_log(f"!!! 推理线程错误: {e}", "INFERENCE")
                print(f"[推理线程] 错误: {e}")
                import traceback
                traceback.print_exc()
                time.sleep(0.01)

        debug_log("========== 推理线程结束 ==========", "INFERENCE")
        print("[推理线程] 结束运行")
