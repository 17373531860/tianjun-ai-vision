"""推理线程主循环 + 4 个辅助方法 (v2.7.16 P6 阶段一从 source.py 抽出)。

职责: 从 capture 线程派发的最新帧上跑模型 → 提取 confirmed → 发布到 detection_lock。
宿主必须提供: self.detection_enabled / running / model / project_config / fps_inference
          self.detection_lock / _confirmed_detections_lock / latest_frame / _frame_lock 等"""
from __future__ import annotations

import time
import traceback

from backend.api.source_sdk_loader import debug_log
from backend.core import debug_center


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

    def _ack_freeze_active(self) -> bool:
        """v3.44.2 人工确认定格是否要求跳过推理 (True = 本帧不跑模型不出框)。

        仅步骤状态机路径生效: tracking / 区域事件模式没有 require_ack 定格语义
        (它们的统计函数里没有阻塞门), 跳过推理反而会改变其既有行为。
        """
        if not getattr(self, '_pending_ack', False):
            return False
        if getattr(self, '_region_event_engine', None) is not None:
            return False
        return (self.project_config or {}).get('logic_mode') != 'tracking'

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
        # 频闪诊断: 每帧重置原始置信度侧信道 (runner 在步骤阈值过滤前往里写最高 conf)
        self._diag_raw_conf = {}
        if getattr(self, 'source_type', None) == 'synthetic':
            idx = int(getattr(self, '_latest_synthetic_inference_idx', -1))
            detections = self._synthetic_detections_for_frame_index(idx)
            detect_time = (time.time() - t_start) * 1000
            if detect_time > 200:
                debug_log(f"!!! synthetic 推理耗时: {detect_time:.1f}ms, 检测数={len(detections)}", "INFERENCE")
            if detections:
                detections = self._map_detections_original_to_display(detections)
            # 剧本注入固定走非跟踪 / 非分割路径（与 _update_step_stats 对齐）
            # v3.32: synthetic 也过标签区域拆分层 → 全链路可用剧本回归
            detections = self._apply_label_splits(detections)
            return (detections, False, False, t_start)

        # 主模型的 task_type / logic_mode (对副模型不适用)
        _task_type = self.project_config.get('task_type', 'detection') if self.project_config else 'detection'
        _logic_mode = self.project_config.get('logic_mode', 'sequential') if self.project_config else 'sequential'
        is_tracking = (_logic_mode == 'tracking')
        is_seg = (_task_type == 'segmentation')

        # v3.19.x: 自定义模式混合跟踪 (custom_mixed_with='tracking') 时,
        # runner 仍走 _detect_and_track 让 detections 带 track_id (物品唯一计数用),
        # 但 is_tracking 保持 False — 下游统计/发布仍走 _update_step_stats 步骤路径。
        _runner_tracking = is_tracking
        if not is_tracking and _logic_mode == 'custom' and self.project_config:
            _pipeline = self.project_config.get('pipeline_config', {}) or {}
            if _pipeline.get('custom_mixed_with') == 'tracking':
                _runner_tracking = True

        detections = self._run_models_for_frame(frame, t_start, _runner_tracking, is_seg)

        detect_time = (time.time() - t_start) * 1000
        if detect_time > 200:
            debug_log(f"!!! 推理耗时: {detect_time:.1f}ms, 检测数={len(detections) if detections else 0}", "INFERENCE")

        # v2.7.14: 把 detections 从 "原图坐标系" 映射到 "显示坐标系"
        # 让下游 ROI / 容器 / stats / 前端画框全部工作在显示坐标系
        if detections:
            detections = self._map_detections_original_to_display(detections)

        # v3.32: 同标签区域拆分（虚拟步骤）——在显示坐标系上按区域改写标签,
        # 下游状态机/画框/MES 全部见到的是虚拟步骤标签
        detections = self._apply_label_splits(detections)

        return (detections, is_tracking, is_seg, t_start)

    def _apply_label_splits(self, detections):
        """v3.32 检测出口标签改写层: 同标签区域拆分 + 工件就位状态刷新.

        引擎/状态由 apply_project_config 按 pipeline_config.label_splits /
        placement_guide 构建; 未配置时均为 None → 一次 getattr 早退零开销。
        详见 backend/api/source_label_split.py 与对应 RFC。
        """
        guide = getattr(self, '_placement_guide_state', None)
        engine = getattr(self, '_label_split_engine', None)
        if guide is None and engine is None:
            return detections
        now = time.time()
        if guide is not None:
            try:
                guide.update(detections, now)
            except Exception as e:
                debug_log(f"!!! placement_guide 更新失败: {e}", "INFERENCE")
        if engine is None:
            return detections
        try:
            # cycle_len: 周期已结算且切换标签离场时轮次归零(下一工件从第1轮起)
            cycle_len = len(getattr(self, 'current_cycle_steps', None) or [])
            out = engine.apply(detections, now, cycle_len=cycle_len)
            # 逐帧改写轨迹(仅结算调试开关打开时): 排查"虚拟步骤时断时续"时,
            # 这里是拆分层出口的唯一真相 —— 上游看模型, 下游看状态机
            if debug_center.is_on("backend.settlement"):
                # 只在"标签集合发生变化"的帧打点: 既能还原步骤出现/消失的精确
                # 时间线(排查"虚拟步骤时断时续"的唯一真相), 又不会以推理帧率
                # 刷爆调试环形缓冲
                sig = tuple(sorted(d.get('label', '') for d in (out or [])))
                if sig != getattr(self, '_dbg_split_last_sig', None):
                    self._dbg_split_last_sig = sig
                    raw = [f"{d.get('label')}:{d.get('confidence', 0):.2f}"
                           for d in (detections or [])]
                    pairs = [f"{d.get('label')}:{d.get('confidence', 0):.2f}"
                             for d in (out or [])]
                    debug_center.dbg(
                        "backend.settlement", "SplitOut",
                        f"ch{self.channel_id} in[{' '.join(raw)}] out[{' '.join(pairs)}]")
            return out
        except Exception as e:
            # 拆分层故障不允许拖垮检测主链路: 打日志后原样放行
            debug_log(f"!!! label_split 改写失败: {e}", "INFERENCE")
            return detections

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

    def _inference_loop(self, my_gen: int = None):
        """独立推理线程 (v2.7.16 P5b 拆分版)。

        包含: 取帧 → 推理 → 帧计数验证 → 步骤判断 → 事件触发。
        本方法只剩调度骨架 + 周期清理 + 心跳, 实际工作分摊在 4 个辅助方法。

        my_gen: 本线程的代数 (2026-07 频闪修复)。与宿主当前代数对不上时自行退出,
                保证任意时刻至多一条推理线程在发布结果 — 双线程交替发布
                "有结果/空结果"就是前端标注框频闪的真因。None = 兼容旧调用方。
        """
        debug_log("========== 推理线程开始 ==========", "INFERENCE")
        print(f"[推理线程] 开始运行 (gen={my_gen})")
        last_frame_id = None
        frame_count = 0
        last_cleanup_time = time.time()
        cleanup_interval = 60.0          # 每 60 秒缓存清理
        last_gpu_cleanup_time = time.time()
        gpu_cleanup_interval = 600.0     # 每 10 分钟 GPU 深度清理
        last_log_time = time.time()
        log_interval = 10.0              # 每 10 秒打印诊断状态
        last_debug_time = time.time()    # 每 5 秒打印 debug log
        # backend.detection 逐帧性能摘要 (每 2s): 推理fps vs 采集fps / 空结果占比 / 跳帧
        perf_win_start = time.time()
        perf_frames = 0          # 本窗口实际推理帧数
        perf_empty = 0           # 本窗口检测结果为空的帧数 (前端叠加层会清框 → 闪烁元凶)
        perf_grab_miss = 0       # 本窗口"无新帧可推"的空转次数 (推理快于采集)
        # v3.7.4: 周期性强制动作的"时间维度"触发节流 — 每 5 秒检查一次,
        # 即使生产停了 (没有新 cycle_end), 只要 detection 在跑就会主动报警.
        last_periodic_time_check = time.time()
        # v3.8.x: 从 5.0s 降到 1.0s — 让 'continuous:N' 模式 N=1~4 也能按预期触发.
        # 检查本身只是 dict 遍历 + 简单条件判断, 1Hz 开销可忽略 (相比每帧推理).
        # 实际触发频率仍由 _should_trigger_overdue_v2 节流, 这里只决定"检查间隔上限".
        periodic_time_check_interval = 1.0

        while self._inference_running and self.is_detecting:
            if my_gen is not None and my_gen != getattr(self, '_inference_generation', my_gen):
                print(f"[推理线程] 代数过期退出 (gen={my_gen}, 当前={self._inference_generation})")
                return
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

                # v3.7.4: 周期性强制动作"时间维度"主动检查 (每 5s 节流)
                if loop_start - last_periodic_time_check > periodic_time_check_interval:
                    if hasattr(self, '_check_periodic_actions_time_only'):
                        try:
                            self._check_periodic_actions_time_only(loop_start)
                        except Exception as _e:
                            debug_log(f"!!! periodic_actions_time_only 异常: {_e}", "INFERENCE")
                    last_periodic_time_check = loop_start

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

                # backend.detection 逐帧性能摘要 (每 2s 节流, 关闭时仅 dict 查询)
                if loop_start - perf_win_start >= 2.0:
                    from backend.core import debug_center as _dc
                    if _dc.is_on("backend.detection"):
                        _win = loop_start - perf_win_start
                        _infer_fps = perf_frames / _win if _win > 0 else 0
                        _empty_pct = (perf_empty / perf_frames * 100) if perf_frames else 0
                        debug_log(
                            f"性能摘要: 推理={_infer_fps:.1f}fps 采集={getattr(self, 'fps_actual', '?')}fps "
                            f"空结果={perf_empty}/{perf_frames}帧({_empty_pct:.0f}%) "
                            f"无新帧空转={perf_grab_miss}次 "
                            f"(推理<<采集 或 空结果高 → 前端叠加层频繁清框 = 画面闪烁)",
                            "PERF")
                    perf_win_start = loop_start
                    perf_frames = 0
                    perf_empty = 0
                    perf_grab_miss = 0

                # ── v3.44.2 人工确认定格 = 检测线整体停摆 (上银现场诉求) ──
                # 确认框弹出到工人点确认前, 工人要执行"取出错盘/放回"等整改动作,
                # 这些动作绝不能被识别成放托盘/收尾步骤。老实现只冻结状态机
                # (帧仍推理+出框), 整改动作的余波会在解除定格瞬间被状态机看到;
                # 现在直接跳过模型推理并清空已发布检测框 (画面无框 = 一眼可见
                # "线已定格")。超时自动确认仍由 _update_step_stats 的阻塞门驱动
                # (喂空检测, 阻塞中它在门口即返回, 不推进任何状态)。
                # ⚠️ 必须在取帧之前: 视频源定格期间播放位置冻结、无新帧,
                # 放在取帧后会因 frame=None 短路, 超时自动确认永远轮不到。
                if self._ack_freeze_active():
                    self._update_step_stats([], None)
                    self._inference_publish_detections([], False)
                    time.sleep(0.02)
                    continue

                # 取最新帧 + 去重
                frame, display_small, frame_id = self._inference_grab_latest_frame(last_frame_id)
                if frame is None:
                    perf_grab_miss += 1
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
                perf_frames += 1
                if not detections:
                    perf_empty += 1

                # 更新步骤/跟踪统计 (original_frame 已是 display_small, 与 detections 坐标系一致)
                t5 = time.time()
                if is_tracking:
                    self._update_tracking_stats(detections, original_frame)
                elif getattr(self, '_region_event_engine', None) is not None:
                    # 区域事件模式: 时序+空间规则引擎替代步骤状态机
                    self._update_region_events(detections, original_frame)
                else:
                    self._update_step_stats(detections, original_frame)
                update_time = (time.time() - t5) * 1000
                if update_time > 100:
                    debug_log(f"!!! 步骤统计耗时: {update_time:.1f}ms", "INFERENCE")

                self.latency = int((time.time() - t_detect_start) * 1000)

                # 发布 confirmed 结果
                self._inference_publish_detections(detections, is_tracking)
                # 本轮走通 → 连续异常计数归零 (见 except 分支的"框卡死"自愈)
                self._infer_consec_errors = 0

                # 帧级检测钩子 (observe-only): 把本帧检测框逐帧广播给插件,
                # 让需要"逐帧生命周期计数"的插件 (如耗材约束按 demo ProductCounter
                # 算法自计数) 拿到原始检测框。不在 RETURNABLE_HOOK_FIELDS 白名单,
                # 返回值丢弃, 不改主程序状态机。无 active 插件时 fire_plugin_hook
                # O(1) 早退 (热路径零开销); tracking 路径自带计数, 不走本钩子。
                if not is_tracking:
                    try:
                        from backend.plugin_system.hook_dispatch import fire_plugin_hook
                        fire_plugin_hook("detection_frame", "post_inference", "post", {
                            "channel_id": self.channel_id,
                            "frame_seq": frame_count,
                            "timestamp": loop_start,
                            "detections": detections or [],
                        })
                    except Exception as _e:
                        debug_log(f"!!! detection_frame hook 触发异常 (已隔离): {_e}", "INFERENCE")

                # v3.47 训练平台互连帧采样: 按置信度带/未检出/NG 事件把现场帧回传
                # 训练平台做数据集增量。配置关闭时 O(1) 早退 (一个模块级 bool);
                # 命中才做 JPEG 编码且受最小间隔+每小时上限双限流; 任何异常隔离,
                # 绝不影响检测主链路。
                try:
                    from backend.services.interconnect.sampler import maybe_sample_frame
                    maybe_sample_frame(self, original_frame, detections)
                except Exception as _e:
                    debug_log(f"!!! interconnect 采样异常 (已隔离): {_e}", "INFERENCE")

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
                # v3.37 "框卡死"防线 (川南反馈): 推理循环若每帧都异常, 发布点永远走不到,
                # 上一次发布的检测框会一直留在画面上 — 前端表现为"框冻结 + 后续类别
                # 全不识别 + 周期超时 NG", 现场极易误判为模型问题。这里连续异常达阈值时
                # 主动清空已发布结果 (框消失, 一眼看出是检测链路故障) 并打调试中心留证。
                # 单帧偶发异常 (阈值内) 行为与老版完全一致。
                self._infer_consec_errors = getattr(self, '_infer_consec_errors', 0) + 1
                if self._infer_consec_errors == 30:
                    try:
                        self._inference_publish_detections([], False)
                        from backend.core import debug_center as _dc
                        _dc.dbg("backend.detection", "推理连续异常已清空画面框",
                                f"ch{getattr(self, 'channel_id', 0)} 连续{self._infer_consec_errors}帧推理异常, "
                                f"已清空画面检测框防误读; 最后错误: {e}")
                    except Exception:
                        pass
                time.sleep(0.01)

        debug_log("========== 推理线程结束 ==========", "INFERENCE")
        print("[推理线程] 结束运行")
