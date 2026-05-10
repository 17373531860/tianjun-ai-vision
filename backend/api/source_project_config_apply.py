"""项目配置应用 (P7 第十一刀)

历史问题：
  VSM.set_project_config 是个 188 行的"配置应用大方法", 一口气解析:
    - 步骤 conf 阈值 / 时间配置 / 帧确认 / 静态步骤
    - 替补步骤映射
    - 同时出现组
    - 结算模式 / 空闲超时 / 周期超时
    - 计数器初始化 + 持久化恢复
    - tracking 模式相关初始化

  和 _init_inference_vars 一样, 字段全部归 VSM, 但应用代码可以物理外移,
  让 VSM 类只保留薄 wrapper.

设计原则：
  - 接受 host (VideoSourceManager) + config dict
  - 直接 setattr 到 host
  - 按主题切分小函数, 主入口 apply_project_config 串联调用
"""
from __future__ import annotations

import os
import json as _json

from backend.core.config import DATA_DIR
from backend.api.rod_filter import RodSessionGate, read_rod_filter_config


def _apply_rod_filter(h, config):
    """刷新传动杆过滤参数 + 重建 SessionGate"""
    try:
        h._rod_filter_cfg = read_rod_filter_config(config)
        h._rod_gate = RodSessionGate(
            rod_label=h._rod_filter_cfg["gate_rod_label"],
            gate_labels=h._rod_filter_cfg["gate_labels"],
        )
    except Exception as _e:
        print(f"[rod_filter] read config failed, fallback to defaults: {_e}")
        h._rod_filter_cfg = read_rod_filter_config(None)
        h._rod_gate = RodSessionGate()


def _reset_step_state_dicts(h):
    """重置所有步骤状态字典 (后续会按 steps_config 重新填充)"""
    h.step_conf_thresholds = {}
    h.step_time_config = {}
    h.step_min_frames = {}
    h.step_consecutive_frames = {}
    h.step_frame_confirmed = {}
    h.step_gap_tolerance = {}
    h._step_gap_count = {}
    h.step_detection_type = {}
    h.step_static_config = {}
    h.step_static_triggered = {}
    h.step_display_names = {}
    h.step_backup_map = {}
    h.step_primary_to_backup = {}
    h.backup_steps_seen_in_cycle = set()
    h.step_strict_order = {}
    h.step_accept_once = {}
    h.step_roi_polygons = {}
    h._first_step_had_gap = False
    h._first_step_reconfirmed = False
    h._first_step_disappeared_at = None
    h._last_step_added_time = None
    h._step_raw_start = {}
    h._cycle_regression = False


def _apply_steps_config(h, steps_config):
    """第一遍: 解析每个 enabled 步骤的阈值/时间/帧确认/静态步骤配置"""
    for step in steps_config:
        if not step.get('enabled', True):
            continue
        label = step.get('label', '')
        # 前端发送的是百分比 (10-100), 需要转换为小数 (0.1-1.0)
        threshold = step.get('threshold', 50)
        if threshold > 1:
            threshold = threshold / 100.0
        h.step_conf_thresholds[label] = threshold

        display_label = step.get('displayLabel') or step.get('display_name') or label
        if display_label != label:
            h.step_display_names[label] = display_label

        h.step_time_config[label] = {
            'min_duration': step.get('min_duration'),
            'max_duration': step.get('max_duration'),
            'max_interval': step.get('max_interval', 1.0),
            'disappear_delay': step.get('disappear_delay', 0),
            'timeout_ng': step.get('timeout_ng', False),
        }

        min_frames = step.get('min_frames')
        h.step_min_frames[label] = min_frames if min_frames and min_frames > 0 else 1

        gap_tolerance = step.get('gap_tolerance')
        h.step_gap_tolerance[label] = gap_tolerance if gap_tolerance and gap_tolerance > 0 else 0

        if step.get('strict_order'):
            h.step_strict_order[label] = True
        if step.get('accept_once'):
            h.step_accept_once[label] = True

        detection_type = step.get('detection_type', 'dynamic')
        h.step_detection_type[label] = detection_type

        if detection_type == 'static':
            h.step_static_config[label] = {
                'trigger_frames': step.get('static_trigger_frames', 30),
                'join_cycle': step.get('join_cycle', True),
                'trigger_event': step.get('triggerEvent'),
            }
            h.step_static_triggered[label] = False

        # 逐步骤 ROI (顺序 / 检测 / 自定义 / tracking 共用): 归一化多边形 ≥3 点
        roi_raw = step.get('roi')
        if roi_raw and isinstance(roi_raw, list) and len(roi_raw) >= 3:
            ok = True
            parsed = []
            for p in roi_raw:
                if not isinstance(p, (list, tuple)) or len(p) < 2:
                    ok = False
                    break
                try:
                    parsed.append([float(p[0]), float(p[1])])
                except (TypeError, ValueError):
                    ok = False
                    break
            if ok:
                h.step_roi_polygons[label] = parsed
            else:
                h.step_roi_polygons.pop(label, None)
        else:
            h.step_roi_polygons.pop(label, None)


def _apply_backup_steps(h, steps_config):
    """第二遍: 构建 backup step 映射 (需要先 resolve 全部 label)"""
    for step in steps_config:
        if not step.get('enabled', True):
            continue
        label = step.get('label', '')
        backup_for_id = step.get('backup_for')
        if not backup_for_id:
            continue
        for s in steps_config:
            if s.get('id') == backup_for_id:
                primary_label = s.get('label', '')
                if primary_label:
                    h.step_backup_map[label] = primary_label
                    h.step_primary_to_backup[primary_label] = label
                break
    if h.step_backup_map:
        print(f"替补步骤映射: {h.step_backup_map}")


def _apply_models_config(h, pipeline_config):
    """Step 6 (feat/multi-model-roi-link): 解析 pipeline_config.models[] 配置.

    职责边界 (重要):
      - 只更新 mi 的"非模型"字段 (conf/iou/roi/schedule/class_filter/priority/
        display_color/use_half), 不真正 load YOLO 模型 (load 是耗时副作用,
        集中在 /detection/start 触发);
      - 副 mi 不存在则按配置创建空壳 (mi.model=None, 等到 /detection/start
        被加载时才有 GPU 实例);
      - 配置中没有的旧副 mi (除 main 外) 自动释放, 防止切项目后残留;
      - main mi 永远存在, 即使 pipeline_config.models 不含 main 也不删它.

    向后兼容: pipeline_config.models 不存在 / 为空 → 完全 no-op, 老链路不变.
    """
    if not hasattr(h, '_router') or h._router is None:
        return

    models_cfg = pipeline_config.get('models')
    if not models_cfg or not isinstance(models_cfg, list):
        return

    from backend.api.source_inference_router import ModelInstance, Schedule

    new_names = set()
    for m_cfg in models_cfg:
        if not isinstance(m_cfg, dict):
            continue
        name = m_cfg.get('name', 'main') or 'main'
        new_names.add(name)
        mi = h._router.get(name)
        if mi is None:
            mi = ModelInstance(
                name=name,
                priority=int(m_cfg.get('priority', 100 if name == 'main' else 50)),
            )
            h._router.add_model(mi)

        if m_cfg.get('conf') is not None:
            try:
                mi.conf = float(m_cfg['conf'])
            except (TypeError, ValueError):
                pass
        if m_cfg.get('iou') is not None:
            try:
                mi.iou = float(m_cfg['iou'])
            except (TypeError, ValueError):
                pass
        if 'roi' in m_cfg:
            roi_val = m_cfg.get('roi')
            mi.roi = list(roi_val) if roi_val else None
            # 重置 ROI 缓存 (mask 形状会随 roi 变化)
            mi._roi_mask_cache = None
            mi._roi_mask_shape = None
            mi._roi_polygon_pixels = None
        if 'schedule' in m_cfg and m_cfg.get('schedule'):
            sch = m_cfg['schedule']
            if isinstance(sch, dict):
                try:
                    mi.schedule = Schedule(
                        type=sch.get('type', 'every_frame'),
                        n=int(sch.get('n', 1)),
                        events=list(sch.get('events') or []),
                    )
                except Exception as _e:
                    print(f"[多模型] schedule 解析失败 ({name}): {_e}, 保持原值")
        if 'class_filter' in m_cfg:
            cf = m_cfg.get('class_filter')
            mi.class_filter = set(cf) if cf else None
        if m_cfg.get('priority') is not None:
            try:
                mi.priority = int(m_cfg['priority'])
            except (TypeError, ValueError):
                pass
        if m_cfg.get('display_color'):
            mi.display_color = str(m_cfg['display_color'])
        if m_cfg.get('use_half') is not None:
            mi.use_half = bool(m_cfg['use_half'])

    # 释放配置外的旧副 mi (main 永远保留, 主路径仍由老 load_model 管理)
    obsolete = [
        n for n in list(h._router.models.keys())
        if n != 'main' and n not in new_names
    ]
    for name in obsolete:
        mi = h._router.models.get(name)
        if mi is not None and hasattr(h, '_release_model_from'):
            try:
                h._release_model_from(mi)
            except Exception as _e:
                print(f"[多模型] 释放旧 slot {name} 失败: {_e}")
        h._router.remove_model(name)
        print(f"[多模型] 配置外的旧 slot 已释放: {name}")

    if new_names:
        print(f"[多模型] pipeline_config.models 配置已应用: {sorted(new_names)}")


def _apply_pipeline_config(h, config, pipeline_config):
    """同时出现组 + 结算模式 + 空闲/周期超时"""
    h._simultaneous_groups = pipeline_config.get('simultaneous_groups', [])
    h._sim_group_buffers = {}
    if h._simultaneous_groups:
        print(f"同时出现组: {h._simultaneous_groups}")

    h.settlement_mode = pipeline_config.get('settlement_mode', 'first_step')
    h.idle_timeout_seconds = pipeline_config.get('idle_timeout_seconds', 0)
    h.cycle_max_duration = pipeline_config.get('cycle_max_duration', 0)
    print(
        f"结算模式: {h.settlement_mode}, 空闲超时: {h.idle_timeout_seconds}s, "
        f"周期超时: {h.cycle_max_duration}s"
    )

    # 结算步骤不允许有 strict_order, 确保结算步骤始终能进入周期
    if h.settlement_mode == 'last_step':
        settle_label = h._get_last_sequence_step_label()
    else:
        settle_label = h._get_first_sequence_step_label()
    if settle_label and h.step_strict_order.get(settle_label):
        del h.step_strict_order[settle_label]
        print(f"[{h.settlement_mode}模式] 自动移除结算步骤 [{settle_label}] 的严格顺序")


def _apply_counters(h, config):
    """初始化计数器 + 从通道专属文件恢复持久化值"""
    h.counters = {}
    counters_config = config.get('counters_config', [])

    DEFAULT_COUNTERS = ['总产量', '合格总数', '不良总数', 'NG步骤']

    for counter in counters_config:
        h.counters[counter.get('name', '')] = counter.get('value', 0)

    for default_name in DEFAULT_COUNTERS:
        if default_name not in h.counters:
            h.counters[default_name] = 0

    project_id = config.get('id')
    if not project_id:
        return
    counter_file = os.path.join(
        DATA_DIR, 'counters', f'project_{project_id}_ch{h.channel_id}.json'
    )
    if not os.path.exists(counter_file):
        return
    try:
        with open(counter_file, 'r', encoding='utf-8') as f:
            saved = _json.load(f)
        for name, val in saved.items():
            if name in h.counters:
                h.counters[name] = val
        print(f"[计数器] ch{h.channel_id} 从文件恢复: {h.counters}")
    except Exception as e:
        print(f"[计数器] ch{h.channel_id} 恢复失败: {e}")


def _reset_cycle_state(h):
    """重置当前周期状态 (新配置 → 新周期开始)"""
    h.current_cycle_steps = []
    h.last_added_step = None
    h.backup_steps_seen_in_cycle = set()
    h.cycle_complete = False
    h.events_log = []
    h.ng_step_cycle_counts = {}
    h.step_start_time = {}


def _apply_tracking_mode(h, config, pipeline_config):
    """tracking 模式: 重置计数 + 生成 custom tracker yaml + container 配置"""
    if config.get('logic_mode') != 'tracking':
        return
    h._reset_counting_cycle()
    h._generate_custom_tracker_yaml(pipeline_config)
    clabel = pipeline_config.get('tracking_container_label', '')
    is_container_strategy = pipeline_config.get('tracking_cycle_strategy') == 'container'
    h._container_label = clabel if is_container_strategy else ''
    h._container_mode = is_container_strategy and bool(clabel)


def _print_summary(h, config, steps_config, pipeline_config):
    """打印配置加载摘要 (调试用)"""
    print(
        f"项目配置已加载: {config.get('name', 'Unknown')}, "
        f"task_type={config.get('task_type')}, logic_mode={config.get('logic_mode')}"
    )
    print(f"步骤阈值: {h.step_conf_thresholds}")
    print(f"步骤时间配置: {h.step_time_config}")
    print(f"步骤最少帧数: {h.step_min_frames}")
    print(f"步骤丢帧容忍: {h.step_gap_tolerance}")
    print(f"步骤检测类型: {h.step_detection_type}")
    print(f"静态步骤配置: {h.step_static_config}")
    print(f"计数器: {h.counters}")
    if config.get('logic_mode') == 'tracking':
        event_labels = [
            s.get('label')
            for s in steps_config
            if s.get('enabled', True) and s.get('count_mode') == 'event'
        ]
        print(
            f"跟踪模式配置: strategy={pipeline_config.get('tracking_cycle_strategy')}, "
            f"expected={pipeline_config.get('counting_expected_items')}"
        )
        if event_labels:
            print(f"动作计数标签: {event_labels}")


# ============================================================
# 顶层入口: 替代历史的 VSM.set_project_config (188 行)
# ============================================================
def apply_project_config(h, config: dict):
    """应用项目配置 → 重置全部步骤/周期状态 + 计数器"""
    h.project_config = config

    _apply_rod_filter(h, config)
    _reset_step_state_dicts(h)

    steps_config = config.get('steps_config', [])
    _apply_steps_config(h, steps_config)
    _apply_backup_steps(h, steps_config)

    pipeline_config = config.get('pipeline_config', {})
    _apply_pipeline_config(h, config, pipeline_config)
    _apply_models_config(h, pipeline_config)

    _apply_counters(h, config)
    _reset_cycle_state(h)
    _apply_tracking_mode(h, config, pipeline_config)

    # v3.5.0: 周期性强制动作（每 N 轮做 E 否则告警）
    if hasattr(h, '_apply_periodic_actions'):
        try:
            h._apply_periodic_actions(config)
        except Exception as e:
            print(f"[PeriodicActions] 应用配置失败: {e}")

    _print_summary(h, config, steps_config, pipeline_config)
