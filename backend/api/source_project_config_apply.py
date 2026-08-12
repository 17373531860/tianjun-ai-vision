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


# ==================== NG 判定与处置: 统一模型解析 (v3.44) ====================

def resolve_ng_handling(pipeline_config: dict) -> dict:
    """把 NG 处置配置归一成单一模型 (读兼容: 新块优先, 老键合成).

    v3.23 补做策略 / v3.32 违序提示事件 / v3.43 实时NG / v3.44 收尾防呆
    五个版本各自累加的散装开关, 收敛为一个 ``ng_handling`` 块, 四行语义:
      violation:    违规当场反应  none | hint | instant_ng (+ violation_event_id)
      missing_step: 缺步骤时处置  ng | ack | hold (+ hold_timeout_s / hold_event_id)
      short_count:  少装数量处置  ng | ack | hold (hold=挂起补数量断点重做, v3.44.1)
      gate:         数量门(事前拦截) gate_enabled / gate_steps / gate_event_id
                    + gate_escalate_steps (v3.44.4 短拦长放): 列表内的收尾步骤被门
                    拦时不再静默吞掉 — 报警提示后放行进周期, 让结算走挂起补做链.
                    适合 min_frames 较高、确认成立≈真人动作的步骤 (如封箱);
                    噪声高发步骤 (如放油嘴包 min_frames=1) 不要放进来.
    老项目没有 ng_handling → 从 legacy 键合成等价档位, 行为零差异.
    """
    def _ev(v):
        try:
            return int(v) if v else None
        except (TypeError, ValueError):
            return None

    raw = pipeline_config.get('ng_handling')
    if isinstance(raw, dict) and raw:
        src = raw
    else:
        rem = pipeline_config.get('ng_remediation', {}) or {}
        cg = pipeline_config.get('closing_guard', {}) or {}
        rem_on = bool(rem.get('enabled', False))
        src = {
            'violation': ('instant_ng' if pipeline_config.get('instant_ng_on_violation')
                          else ('hint' if pipeline_config.get('strict_order_violation_event_id')
                                else 'none')),
            'violation_event_id': pipeline_config.get('strict_order_violation_event_id'),
            'missing_step': ('hold' if cg.get('hold_enabled')
                             else ('ack' if rem_on and rem.get('allow_step', True) else 'ng')),
            'hold_timeout_s': cg.get('hold_timeout_s', 120),
            'hold_event_id': cg.get('event_id'),
            'short_count': 'ack' if rem_on and rem.get('allow_count', True) else 'ng',
            'gate_enabled': cg.get('gate_enabled', False),
            'gate_steps': cg.get('gate_steps') or [],
            'gate_event_id': cg.get('event_id'),
            'gate_escalate_steps': [],
        }
    violation = src.get('violation')
    if violation not in ('none', 'hint', 'instant_ng'):
        violation = 'none'
    missing_step = src.get('missing_step')
    if missing_step not in ('ng', 'ack', 'hold'):
        missing_step = 'ng'
    short_count = src.get('short_count')
    if short_count not in ('ng', 'ack', 'hold'):
        short_count = 'ng'
    try:
        hold_timeout_s = max(0.0, float(src.get('hold_timeout_s', 120) or 0))
    except (TypeError, ValueError):
        hold_timeout_s = 120.0
    return {
        'violation': violation,
        'violation_event_id': _ev(src.get('violation_event_id')),
        'missing_step': missing_step,
        'hold_timeout_s': hold_timeout_s,
        'hold_event_id': _ev(src.get('hold_event_id')),
        'short_count': short_count,
        'gate_enabled': bool(src.get('gate_enabled', False)),
        'gate_steps': [s for s in (src.get('gate_steps') or []) if s],
        'gate_event_id': _ev(src.get('gate_event_id')),
        'gate_escalate_steps': [s for s in (src.get('gate_escalate_steps') or []) if s],
    }


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
    h.step_box_size_limits = {}
    h.step_time_config = {}
    h.step_min_frames = {}
    h.step_consecutive_frames = {}
    h.step_frame_confirmed = {}
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

        # v3.10+ 步骤级 box 尺寸过滤 (归一化比例 0~1, 0 = 关闭).
        # 模型若把"工件整体形态" / "ROI 大区域" 误识别为某个 label, box 通常会异常宽/高.
        # 配置 box_max_width=0.85 即可在 detection 出口直接丢弃 (任意逻辑模式通用).
        try:
            box_max_w = float(step.get('box_max_width') or 0)
        except (TypeError, ValueError):
            box_max_w = 0.0
        try:
            box_max_h = float(step.get('box_max_height') or 0)
        except (TypeError, ValueError):
            box_max_h = 0.0
        if box_max_w > 0 or box_max_h > 0:
            h.step_box_size_limits[label] = (
                max(0.0, min(1.0, box_max_w)),
                max(0.0, min(1.0, box_max_h)),
            )

        display_label = step.get('displayLabel') or step.get('display_name') or label
        if display_label != label:
            h.step_display_names[label] = display_label

        # v3.8.x: 原 max_interval (去重间隔) / gap_tolerance (丢帧容忍) 已合并入
        # disappear_delay (消失等待时间). 老项目 JSON 里若仍带这两个键, 直接忽略
        # — 不影响读取, 也不强制清理 DB, 下次客户在前端保存项目时自然被覆盖.
        h.step_time_config[label] = {
            'min_duration': step.get('min_duration'),
            'max_duration': step.get('max_duration'),
            'disappear_delay': step.get('disappear_delay', 0),
            'timeout_ng': step.get('timeout_ng', False),
            # v3.34: 消失等待不被其他步骤打断 (默认 False = 老行为零差异).
            # 工具驻留画面的产线 (吹枪插在工件上、敲击/检查与之并行可见) 中,
            # "别的步骤一出现就掐掉消失等待"会把驻留步骤的闪断误判成重新出现,
            # 该开关让本步骤的消失等待时间始终按配置走完。
            'disappear_uninterruptible': bool(step.get('disappear_uninterruptible', False)),
        }

        min_frames = step.get('min_frames')
        h.step_min_frames[label] = min_frames if min_frames and min_frames > 0 else 1

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
            mi._roi_mask_transform_sig = None
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


def _parse_combo_table(raw):
    """v3.48 计数组合判定表归一化: 非法/未启用/空表返回 None (= 功能关)。

    输入 schema (pipeline_config.combo_table):
      {enabled: bool, labels: [str], rows: [{counts: [int], verdict: "OK"|"NG", tag: str}],
       count_mode: "steps"(默认) | "positional",
       tracking: {iou, ema_alpha, min_consecutive, pending_ttl, perish_ticks, idle_reset_ticks}}
    校验规则: labels 非空去重; 行 counts 长度必须与 labels 等长 (错行丢弃并打日志),
    counts 逐项转非负 int; verdict 只认 NG (其余按 OK); 全部行非法时整表禁用。
    count_mode='positional' 时结算计数改用位置去重引擎 (IoU 追踪, 同位置返工
    不重计, 复刻外部工具算法3, 见 source_combo_positional.py); 缺省 'steps' 零差异。
    """
    if not isinstance(raw, dict) or not raw.get('enabled'):
        return None
    labels = [str(l).strip() for l in (raw.get('labels') or []) if str(l).strip()]
    labels = list(dict.fromkeys(labels))
    if not labels:
        return None
    rows = []
    for i, row in enumerate(raw.get('rows') or []):
        if not isinstance(row, dict):
            continue
        counts = row.get('counts')
        if not isinstance(counts, (list, tuple)) or len(counts) != len(labels):
            print(f"[ComboTable] 第 {i + 1} 行 counts 长度与 labels 不符, 丢弃: {counts}")
            continue
        try:
            counts = [max(0, int(c)) for c in counts]
        except (TypeError, ValueError):
            print(f"[ComboTable] 第 {i + 1} 行 counts 含非整数, 丢弃: {counts}")
            continue
        rows.append({
            'counts': counts,
            'verdict': 'NG' if str(row.get('verdict', 'OK')).upper() == 'NG' else 'OK',
            'tag': str(row.get('tag') or '').strip(),
        })
    if not rows:
        print("[ComboTable] 启用但无有效行, 整表禁用")
        return None
    mode = 'positional' if str(raw.get('count_mode') or '').lower() == 'positional' else 'steps'
    tracking = {}
    if mode == 'positional':
        tr = raw.get('tracking') if isinstance(raw.get('tracking'), dict) else {}
        def _f(key, default, lo, hi):
            try:
                return min(hi, max(lo, float(tr.get(key, default))))
            except (TypeError, ValueError):
                return default
        tracking = {
            'iou': _f('iou', 0.4, 0.05, 0.95),
            'ema_alpha': _f('ema_alpha', 0.6, 0.0, 1.0),
            'min_consecutive': int(_f('min_consecutive', 3, 1, 60)),
            'pending_ttl': int(_f('pending_ttl', 10, 1, 600)),
            'perish_ticks': int(_f('perish_ticks', 0, 0, 100000)),
            'idle_reset_ticks': int(_f('idle_reset_ticks', 0, 0, 100000)),
        }
    return {'labels': labels, 'rows': rows, 'count_mode': mode, 'tracking': tracking}


def _apply_pipeline_config(h, config, pipeline_config):
    """同时出现组 + 结算模式 + 空闲/周期超时"""
    h._simultaneous_groups = pipeline_config.get('simultaneous_groups', [])
    h._sim_group_buffers = {}
    if h._simultaneous_groups:
        print(f"同时出现组: {h._simultaneous_groups}")

    h.settlement_mode = pipeline_config.get('settlement_mode', 'first_step')
    h.idle_timeout_seconds = pipeline_config.get('idle_timeout_seconds', 0)
    h.cycle_max_duration = pipeline_config.get('cycle_max_duration', 0)

    # ============ v3.48 计数组合判定表 (RFC 14 配套项, 纯视觉判型) ============
    # 检测模式专用: 结算时按参与标签的出现次数向量查表 → 命中行 verdict+tag,
    # 未命中一律 NG (防呆)。参与标签的缺步/重复判定让位查表 (期望数量因机型而异)。
    # 默认无配置 = None = 行为零差异。消费点: _settle_detection_cycle /
    # _maybe_instant_ng_detection_duplicate (combo 标签重复合法, 实时NG让位)。
    h._combo_table = _parse_combo_table(pipeline_config.get('combo_table'))
    h._combo_last_tag = None
    from backend.api.source_combo_positional import build_combo_positional
    h._combo_positional = build_combo_positional(h._combo_table)
    if h._combo_table:
        print(f"计数组合判定表: labels={h._combo_table['labels']} "
              f"{len(h._combo_table['rows'])} 行 (未命中一律 NG), "
              f"count_mode={h._combo_table['count_mode']}"
              + (f", tracking={h._combo_table['tracking']}"
                 if h._combo_positional else ""))

    # ==================== NG 判定与处置 (v3.44 统一模型) ====================
    # 单一块 ng_handling (新配置) 或 legacy 键合成 (老项目零差异), 展开到既有
    # runtime 属性 — 状态机侧 (settlement/event_trigger/packaging) 无需改动:
    #   violation    → instant_ng_on_violation + strict_order_violation_event_id
    #   missing_step → _settle_hold_enabled(hold 档) + _ng_remediation.allow_step(非 ng 档)
    #   short_count  → _ng_remediation.allow_count
    #   gate         → _closing_gate_enabled/_closing_gate_steps/_closing_gate_event_id
    # 语义链提醒: ack 档的"定格弹窗"由 NG 事件自身的「需人工确认」决定 (事件设置),
    # 这里只决定弹窗里给不给"补步骤/补数量"按钮; hold 档超时判 NG 后同样走该链.
    _ngh = resolve_ng_handling(pipeline_config)
    h.strict_order_violation_event_id = (
        _ngh['violation_event_id'] if _ngh['violation'] in ('hint', 'instant_ng') else None)
    h._strict_violation_throttle = {}
    h.instant_ng_on_violation = _ngh['violation'] == 'instant_ng'
    h._settle_hold_enabled = _ngh['missing_step'] == 'hold'
    h._settle_hold_timeout_s = _ngh['hold_timeout_s']
    h._settle_hold_event_id = _ngh['hold_event_id']
    h._settle_hold = None
    h._closing_gate_enabled = _ngh['gate_enabled']
    h._closing_gate_steps = set(_ngh['gate_steps'])
    h._closing_gate_event_id = _ngh['gate_event_id']
    h._closing_gate_escalate_steps = set(_ngh['gate_escalate_steps'])
    _allow_step = _ngh['missing_step'] != 'ng'
    _allow_count = _ngh['short_count'] == 'ack'
    # v3.44.1 少装挂起 (short_count=hold): 步骤全对仅数量不足 → 不判 NG, 摘下收尾
    # 步骤挂起等补数量, 补足并重做收尾后自动 OK (断点重做, 保留已对的步骤与箱账)
    h._short_count_hold = _ngh['short_count'] == 'hold'
    h._ng_remediation = {
        'enabled': _allow_step or _allow_count,
        'allow_step': _allow_step,
        'allow_count': _allow_count,
    }
    if (_ngh['violation'] != 'none' or _ngh['missing_step'] != 'ng'
            or _allow_count or _ngh['gate_enabled']):
        print(f"NG 处置: 违规当场={_ngh['violation']}(事件{_ngh['violation_event_id']}) "
              f"缺步={_ngh['missing_step']}(超时{_ngh['hold_timeout_s']}s/事件{_ngh['hold_event_id']}) "
              f"少装={_ngh['short_count']} 数量门={_ngh['gate_enabled']}"
              f"{sorted(h._closing_gate_steps)}(事件{_ngh['gate_event_id']}, "
              f"升级放行{sorted(h._closing_gate_escalate_steps)})")
    print(
        f"结算模式: {h.settlement_mode}, 空闲超时: {h.idle_timeout_seconds}s, "
        f"周期超时: {h.cycle_max_duration}s"
    )

    # 结算步骤不允许有 strict_order / accept_once, 确保结算步骤始终能进入周期
    if h.settlement_mode == 'last_first':
        # last_first 模式: 全部步骤强制非严格 (前端校验 + 后端二次兜底)
        if h.step_strict_order:
            cleared = list(h.step_strict_order.keys())
            h.step_strict_order = {}
            print(f"[last_first 模式] 自动清空全部步骤的严格顺序 ({cleared})")
        if h.step_accept_once:
            cleared_once = list(h.step_accept_once.keys())
            h.step_accept_once = {}
            print(f"[last_first 模式] 自动清空全部步骤的单次接受 ({cleared_once})")
        # last_first 也意味着 _pending_first_step 重置 (新项目从干净状态开始)
        if hasattr(h, '_pending_first_step'):
            h._pending_first_step = False
    elif h.settlement_mode == 'last_step':
        settle_label = h._get_last_sequence_step_label()
        if settle_label and h.step_strict_order.get(settle_label):
            del h.step_strict_order[settle_label]
            print(f"[{h.settlement_mode}模式] 自动移除结算步骤 [{settle_label}] 的严格顺序")
        if settle_label and h.step_accept_once.get(settle_label):
            del h.step_accept_once[settle_label]
            print(f"[{h.settlement_mode}模式] 自动移除结算步骤 [{settle_label}] 的单次接受")
    else:
        settle_label = h._get_first_sequence_step_label()
        if settle_label and h.step_strict_order.get(settle_label):
            del h.step_strict_order[settle_label]
            print(f"[{h.settlement_mode}模式] 自动移除结算步骤 [{settle_label}] 的严格顺序")
        if settle_label and h.step_accept_once.get(settle_label):
            del h.step_accept_once[settle_label]
            print(f"[{h.settlement_mode}模式] 自动移除结算步骤 [{settle_label}] 的单次接受")

    # v3.19.x: 期望序列中"连续重复"的步骤强制消失等待时间=0 (前端校验 + 后端兜底).
    # 连续 A→A 的辨认信号是"上一次出现已立刻走完消失结算后重现", 若设了等待时间,
    # 两次衔接快于等待时长时第二次会被当成第一次的延续 → 直接漏计.
    logic_mode = config.get('logic_mode', 'detection')
    is_seq_like = logic_mode == 'sequential' or (
        logic_mode == 'custom' and pipeline_config.get('custom_based_on') == 'sequential')
    if is_seq_like:
        try:
            expected_seq = h._get_expected_sequence_labels() or []
            consecutive_dup = {expected_seq[i] for i in range(1, len(expected_seq))
                               if expected_seq[i] == expected_seq[i - 1]}
            for dup_label in consecutive_dup:
                tc = h.step_time_config.get(dup_label)
                if tc and tc.get('disappear_delay'):
                    print(f"[连续重复步骤] [{dup_label}] 消失等待时间 {tc['disappear_delay']}s 强制清 0")
                    tc['disappear_delay'] = 0
        except Exception as e:
            print(f"[连续重复步骤] 消失等待时间兜底清理失败: {e}")


def _apply_label_splits(h, pipeline_config):
    """同标签区域拆分 + 工件就位提示 (v3.32, RFC 同标签区域拆分).

    注意调用顺序: 必须在 _apply_steps_config 之后 (引擎要拿虚拟步骤的
    display_name 映射)。无配置时引擎/状态为 None, 热路径零开销。
    """
    from backend.api.source_label_split import (
        parse_label_splits, parse_placement_guide,
        LabelSplitEngine, PlacementGuideState,
    )
    try:
        rules = parse_label_splits(pipeline_config)
        h._label_split_engine = (
            LabelSplitEngine(rules, display_names=dict(h.step_display_names))
            if rules else None
        )
        if rules:
            summary = {r.source_label: [n for n, _ in r.regions] for r in rules}
            print(f"[LabelSplit] 拆分规则已应用: {summary}")
    except Exception as e:
        h._label_split_engine = None
        print(f"[LabelSplit] 解析拆分规则失败: {e}")
    try:
        guide_cfg = parse_placement_guide(pipeline_config)
        h._placement_guide_state = PlacementGuideState(guide_cfg) if guide_cfg else None
        if guide_cfg:
            print(f"[PlacementGuide] 就位提示已启用: 锚点={guide_cfg['anchor_label']} 档位={guide_cfg['mode']}")
    except Exception as e:
        h._placement_guide_state = None
        print(f"[PlacementGuide] 解析就位提示失败: {e}")


def _apply_region_events(h, config, pipeline_config):
    """区域事件模式 (logic_mode='region_events'): 重建判定引擎。

    非该模式 / 无有效规则 / 配置非法时引擎置 None (热路径零开销);
    引擎整体重建 (引用替换), 与 label_split 同款线程安全模式。
    """
    from backend.api.source_region_events import parse_region_events, RegionEventEngine
    if config.get('logic_mode') != 'region_events':
        h._region_event_engine = None
        return
    try:
        cfg = parse_region_events(pipeline_config)
        h._region_event_engine = RegionEventEngine(cfg) if cfg else None
        if h._region_event_engine is not None:
            # 引擎是本模式唯一判定门: 清掉步骤级 conf/ROI 二次过滤, 防止
            # 模型标签自动生成的步骤(默认阈值 50%)在检测出口误杀低阈值小目标
            # (如 conf=0.25 的测硬度笔)。每类置信度由 region_events.class_conf 管。
            h.step_conf_thresholds = {}
            h.step_roi_polygons = {}
        if cfg:
            print(f"[RegionEvents] 引擎已应用: 规则={[r.name for r in cfg.rules]}, "
                  f"每类conf={cfg.class_conf}, 中断容忍={cfg.gap_tolerance}帧, "
                  f"顺序校验={'开' if cfg.seq_enabled else '关'}")
        else:
            print("[RegionEvents] logic_mode=region_events 但无有效规则配置, 引擎未启用")
    except Exception as e:
        h._region_event_engine = None
        print(f"[RegionEvents] 解析配置失败, 引擎未启用: {e}")


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
    """重置当前周期状态 (新配置 → 新周期开始)

    不清 ng_step_cycle_counts —— 与 step_counts / cycle_times 同属累计统计,
    只在 reset_stats (Monitor「清零」) 或切换项目 (_reset_cumulative_step_stats) 时归零。
    待机 → 再开始会 syncProjectConfig, 若在这里清 TOP3 会把当天 NG 步骤排名抹掉。
    """
    h.current_cycle_steps = []
    h.last_added_step = None
    h.backup_steps_seen_in_cycle = set()
    h.cycle_complete = False
    h.events_log = []
    h.step_start_time = {}


def _reset_cumulative_step_stats(h):
    """项目切换时清空累计统计 (步骤计数/截图/耗时/PT 分段/周期节拍).

    背景: 这些字段按设计归 reset_stats 管 (用户手动"清零"才触发), 但"切换激活项目"
    这条路径两边都不清 —— step_counts 等 dict 以步骤 label 为 key, 旧项目的标签会
    一直挂在 detection/results 返回里, Monitor 步骤统计表持续显示上一个项目的步骤名;
    cycle_times 同理会把两个项目的节拍混在一起拉歪均值。
    只在项目身份变化 (id 不同) 时调用; 同一项目运行中保存配置不走这里, 当天统计不丢。
    线程安全: 与 _reset_step_state_dicts 同款"整体重新赋值"模式 (单写多读, 引用替换)。
    """
    h.step_counts = {}
    h.step_screenshots = {}
    h.step_detection_times = {}
    h.step_durations = {}
    h.step_intervals = {}
    h.step_durations_history = {}
    h.step_cycle_durations = {}
    h.step_cycle_durations_history = {}
    h.step_cycle_segments = {}
    if hasattr(h, 'step_visible_seconds'):
        h.step_visible_seconds = {}
    h.cycle_times = []
    h.ng_cycle_times = []
    h.ng_step_cycle_counts = {}
    h.last_step_completed_time = None


def _apply_tracking_mode(h, config, pipeline_config):
    """tracking 模式: 重置计数 + 生成 custom tracker yaml + container 配置

    v3.19.x: 自定义混合跟踪 (custom + custom_mixed_with='tracking') 同样要
    重置计数状态 + 生成 tracker yaml (遮挡容忍/匹配阈值才能生效, 且不被
    上一个独立跟踪项目的旧 yaml 污染); 但容器分组归周期结算管, 混合下
    周期主权在步骤侧, 强制关闭。
    """
    is_tracking = config.get('logic_mode') == 'tracking'
    is_mixed_tracking = (
        config.get('logic_mode') == 'custom'
        and pipeline_config.get('custom_mixed_with') == 'tracking')
    if not (is_tracking or is_mixed_tracking):
        return
    h._reset_counting_cycle()
    # v3.50 齐件即结算: 换项目/重载配置时豁免名单与等待离场状态整体作废
    # (它们故意不随 _reset_counting_cycle 清, 这里是唯一的整体清空点)
    h._settle_complete_exempt = {}
    h._box_settled_waiting_exit = {}
    h._generate_custom_tracker_yaml(pipeline_config)
    if is_mixed_tracking:
        h._container_label = ''
        h._container_mode = False
        return
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
    # 项目身份变化检测必须在覆盖 h.project_config 之前取旧 id
    _old_project_id = (h.project_config or {}).get('id') if hasattr(h, 'project_config') else None
    _new_project_id = config.get('id')
    h.project_config = config

    _apply_rod_filter(h, config)
    _reset_step_state_dicts(h)

    steps_config = config.get('steps_config', [])
    _apply_steps_config(h, steps_config)
    _apply_backup_steps(h, steps_config)

    pipeline_config = config.get('pipeline_config', {})
    _apply_pipeline_config(h, config, pipeline_config)
    _apply_models_config(h, pipeline_config)
    _apply_label_splits(h, pipeline_config)
    _apply_region_events(h, config, pipeline_config)

    _apply_counters(h, config)
    _reset_cycle_state(h)

    # v3.17.x: 切换到不同项目时清空累计统计, 防止旧项目标签残留在步骤统计表
    # (同一项目重复应用配置 —— 如运行中保存阈值 —— 不清, 保住当天统计)
    if _old_project_id is not None and _new_project_id is not None \
            and _old_project_id != _new_project_id:
        _reset_cumulative_step_stats(h)
        print(f"[项目切换] {_old_project_id} → {_new_project_id}, 累计步骤统计已清零")

    # v3.8.x: 切换项目后统一清空步骤运行时状态.
    # _reset_step_state_dicts / _reset_cycle_state 只重建了"配置类字典 + 周期序列",
    # 没清"最后看见时间戳 / 同时出现组待挂队列 / 上次消失时间戳"等运行时残影.
    # 这里补一刀, 确保切换项目时上次运行的所有残影完全归零, 不会被新项目同名标签接续.
    if hasattr(h, '_clear_step_runtime_state'):
        h._clear_step_runtime_state()

    _apply_tracking_mode(h, config, pipeline_config)

    # v3.5.0: 周期性强制动作（每 N 轮做 E 否则告警）
    if hasattr(h, '_apply_periodic_actions'):
        try:
            h._apply_periodic_actions(config)
        except Exception as e:
            print(f"[PeriodicActions] 应用配置失败: {e}")

    # v3.6.x: per_item 逐件覆盖模式 (logic_mode='per_item')
    # 非 per_item 项目时 _per_item_apply_config 返回 False, 不影响任何状态
    if hasattr(h, '_per_item_apply_config'):
        try:
            h._per_item_apply_config(config)
        except Exception as e:
            print(f"[per_item] 应用配置失败: {e}")
            import traceback
            traceback.print_exc()

    # v3.19.x: 自定义模式混合子状态机 (custom_mixed_with = per_item/tracking)
    # 每次应用配置都重建 — 非 custom / 未混合 / 无物品行时为 None (零差异)
    try:
        from backend.api.source_custom_mix import build_custom_mix
        h._custom_mix = build_custom_mix(config)
    except Exception as e:
        h._custom_mix = None
        print(f"[CustomMix] 构建混合子状态机失败: {e}")
    # 混合跟踪: 周期主权归步骤侧, 真跟踪机械的自动开周期被此标志守门跳过。
    # 独立 tracking 项目恒为 False — 行为零差异。
    h._tracking_external_cycle = bool(
        h._custom_mix is not None and h._custom_mix.mix_type == 'tracking')

    # 原生称重投料模式: 登记/注销本通道到称重引擎。两种登记来源:
    # - logic_mode='weighing'                      : 秤驱动 (v3.31, drive_mode 默认 scale)
    # - 其它模式 + weighing.drive_mode='step_gate' : v3.35 融合 — 视觉顺序 SOP 为周期主线,
    #   秤读数只做"步骤完成门控" (钢帽放秤→去皮门控 / 称重→标准量判定门控)
    # 都不是则注销, 切回别的模式零残留。
    h._weighing_visual_feed = False
    try:
        from backend.services.weighing_engine import get_weighing_engine
        ch_id = getattr(h, 'channel_id', 0)
        _weighing_cfg = pipeline_config.get('weighing') or {}
        _weighing_active = (config.get('logic_mode') == 'weighing'
                            or _weighing_cfg.get('drive_mode') == 'step_gate')
        if _weighing_active:
            get_weighing_engine().set_channel_config(ch_id, _weighing_cfg)
            # 视觉料源防错开启 → 推理热路径每帧喂检测结果给守卫 (flag 守门零差异)
            # v3.39 pipeline 模式: 标签①/③驱动去皮加速与 FIFO 结案, 必须喂
            h._weighing_visual_feed = bool(
                (_weighing_cfg.get('visual_guard') or {}).get('enabled')
                or _weighing_cfg.get('drive_mode') == 'pipeline')
        else:
            get_weighing_engine().set_channel_config(ch_id, None)
    except Exception as e:
        print(f"[Weighing] 登记通道配置失败: {e}")

    # v3.35 步骤外设门控 (steps_config[].device_gate): {label: gate_cfg}
    # 默认无任何步骤配置 → 空 dict → _process_single_step 一次 get 早退, 零差异。
    h.step_device_gates = {}
    try:
        for _step in steps_config:
            _dg = _step.get('device_gate')
            if isinstance(_dg, dict) and _dg.get('enabled') and _step.get('label'):
                h.step_device_gates[_step['label']] = _dg
        if h.step_device_gates:
            print(f"[Weighing] ch{getattr(h, 'channel_id', 0)} 步骤外设门控: "
                  f"{ {k: v.get('kind', 'tare') for k, v in h.step_device_gates.items()} }")
    except Exception as e:
        print(f"[Weighing] 解析步骤门控配置失败: {e}")
        h.step_device_gates = {}

    _print_summary(h, config, steps_config, pipeline_config)
