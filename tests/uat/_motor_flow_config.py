# -*- coding: utf-8 -*-
"""电机装配全流程项目配置(单一事实源) — 回放脚本与可见浏览器 UAT 共用。

客户流程 13 步(严格顺序):
  前罩螺丝1-4 → 前罩力矩 → 前罩标记 → 后罩螺丝1-4 → 后罩力矩 → 后罩标记 → 工件横放

拆分设计:
  - 打螺丝: 锚点跟随 × 4 螺丝区域 × 2 轮次(盖罩切轮) → 8 个虚拟螺丝步骤
  - 力矩/标记: 不需要空间区分, 各配一条"整幅区域"拆分规则, 只为吃到轮次
    前缀 → 前罩力矩/后罩力矩、前罩标记/后罩标记
  - 工件横放: 模型直出标签, 不拆分; min_duration=2.0 挡掉翻面瞬间(实测 148.4s
    有 0.4s 高置信横放闪现, 真实横放段 ≥6s)

参数标定依据: tests/uat/_motor_trace_95_255.json (真实模型逐帧轨迹)。
speedup: 离线加速回放时把所有时长阈值同倍缩短; 实时跑传 1.0。
"""

ANCHOR_REF = {"x": 0.355, "y": 0.362, "w": 0.277, "h": 0.638}
SCREW_REGIONS = [
    {"name": "螺丝1", "polygon": [[0.470, 0.34], [0.545, 0.34], [0.545, 0.52], [0.470, 0.52]]},
    {"name": "螺丝2", "polygon": [[0.460, 0.52], [0.580, 0.52], [0.580, 0.76], [0.460, 0.76]]},
    {"name": "螺丝3", "polygon": [[0.545, 0.38], [0.660, 0.38], [0.660, 0.52], [0.545, 0.52]]},
    {"name": "螺丝4", "polygon": [[0.380, 0.34], [0.470, 0.34], [0.470, 0.52], [0.380, 0.52]]},
]
_FULL = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]

SCREW_STEPS = [f"{p}螺丝{i}" for p in ("前罩", "后罩") for i in range(1, 5)]
# 严格顺序的 13 步流程(虚拟步骤名)
FLOW_STEPS = (["前罩螺丝1", "前罩螺丝2", "前罩螺丝3", "前罩螺丝4", "前罩力矩", "前罩标记",
               "后罩螺丝1", "后罩螺丝2", "后罩螺丝3", "后罩螺丝4", "后罩力矩", "后罩标记",
               "工件横放"])

# 步骤 id 分配: 螺丝 101-108, 力矩 121/122, 标记 131/132, 横放 140
_STEP_IDS = {name: 101 + i for i, name in enumerate(SCREW_STEPS)}
_STEP_IDS.update({"前罩力矩": 121, "后罩力矩": 122,
                  "前罩标记": 131, "后罩标记": 132, "工件横放": 140})


def _rounds(speedup: float) -> dict:
    # trigger_min 0.5s: 实测 218s 附近有 2 帧假盖罩闪现, 见帧即切会推错一拍
    # trigger_conf 0.85: 实测 224.3s 工人拿起罩子的预备动作被识别为盖罩(conf
    #   最高 0.78, 持续 1s), 会把轮次提前推一拍 → 工件2 前罩螺丝全记成后罩;
    #   真实盖上的罩子 conf 稳定 ≥0.85 —— 用切换标签专用置信度下限区分
    return {"enabled": True, "trigger_label": "盖罩", "count": 2,
            "prefixes": ["前罩", "后罩"],
            "trigger_gap_seconds": max(0.5, 3.0 / speedup),
            "trigger_min_seconds": 0.5 / speedup,
            "trigger_conf": 0.85,
            "region_overrides": {}}


def build_flow_config(speedup: float = 1.0) -> dict:
    """返回 steps_config / pipeline_config / events_config / counters_config 四段。"""
    s = speedup

    def _virtual(name, split_id, min_dur, dis_delay):
        return {"id": _STEP_IDS[name], "label": name, "displayLabel": name,
                "enabled": True, "threshold": 50, "min_frames": 1,
                "min_duration": min_dur / s, "disappear_delay": dis_delay / s,
                "accept_once": True, "strict_order": True,
                "split_origin": split_id}

    steps = [
        # 三个原始标签只做拆分源, 本身不进步骤
        {"id": 100, "label": "打螺丝", "displayLabel": "打螺丝",
         "enabled": False, "threshold": 50},
        {"id": 120, "label": "力矩", "displayLabel": "力矩",
         "enabled": False, "threshold": 50},
        {"id": 130, "label": "标记", "displayLabel": "标记",
         "enabled": False, "threshold": 50},
    ]
    # 螺丝: 接触段 1.0~2.8s, 批头滑过邻区误命中 ≤0.24s → 时长门 0.6
    steps += [_virtual(n, "ls_motor", 0.6, 1.5) for n in SCREW_STEPS]
    # 力矩: 真实段 ≥11s → 时长门 1.0 富余
    steps += [_virtual(n, "ls_torque", 1.0, 1.5) for n in ("前罩力矩", "后罩力矩")]
    # 标记: 真实段 ≥5s; 后罩标记与横放后的收尾标记(间隔1.7s)靠 accept_once 去重
    steps += [_virtual(n, "ls_mark", 1.0, 1.5) for n in ("前罩标记", "后罩标记")]
    # 横放: 真实段 ≥6s; 2.0 时长门挡掉翻面瞬间的 0.4s 高置信闪现
    steps += [{"id": _STEP_IDS["工件横放"], "label": "工件横放",
               "displayLabel": "工件横放", "enabled": True, "threshold": 50,
               "min_frames": 1, "min_duration": 2.0 / s,
               "disappear_delay": 1.5 / s, "accept_once": True,
               "strict_order": True}]

    pipeline = {
        "sequence_order": [{"step_id": _STEP_IDS[n]} for n in FLOW_STEPS],
        "settlement_mode": "first_step",
        "settle_dedup": False,
        "strict_order_violation_event_id": 3,
        "label_splits": [
            {"id": "ls_motor", "enabled": True, "source_label": "打螺丝",
             "mode": "anchor", "anchor_label": "工件",
             "anchor_ref": ANCHOR_REF, "anchor_hold_seconds": 5 / s,
             "unmatched": "drop", "regions": SCREW_REGIONS,
             "rounds": _rounds(s)},
            {"id": "ls_torque", "enabled": True, "source_label": "力矩",
             "mode": "fixed", "unmatched": "drop",
             "regions": [{"name": "力矩", "polygon": _FULL}],
             "rounds": _rounds(s)},
            {"id": "ls_mark", "enabled": True, "source_label": "标记",
             "mode": "fixed", "unmatched": "drop",
             "regions": [{"name": "标记", "polygon": _FULL}],
             "rounds": _rounds(s)},
        ],
        "placement_guide": {"enabled": True, "anchor_label": "工件",
                            "polygon": [[0.30, 0.45], [0.75, 0.45],
                                        [0.75, 0.95], [0.30, 0.95]],
                            "mode": "hint", "display": "fade_on_ready"},
    }

    # show_notification: 结算/违序时监控页弹提示框+语音播报(不配就只涨计数器, 无感)
    events = [
        {"id": 1, "name": "合格(OK)", "toast_id": "ok", "show_notification": True,
         "actions": [{"counter_name": "合格总数", "delta": 1},
                     {"counter_name": "总产量", "delta": 1}]},
        {"id": 2, "name": "不合格(NG)", "toast_id": "ng", "show_notification": True,
         "actions": [{"counter_name": "不良总数", "delta": 1},
                     {"counter_name": "总产量", "delta": 1}]},
        {"id": 3, "name": "违序警告", "toast_id": "ng", "show_notification": True,
         "actions": [{"counter_name": "违序次数", "delta": 1}]},
    ]
    counters = [
        {"name": "合格总数", "value": 0, "show_in_monitor": True},
        {"name": "不良总数", "value": 0, "show_in_monitor": True},
        {"name": "违序次数", "value": 0, "show_in_monitor": True},
    ]
    return {"steps_config": steps, "pipeline_config": pipeline,
            "events_config": events, "counters_config": counters}
