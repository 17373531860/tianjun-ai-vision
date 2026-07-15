"""传感器清洁客户项目的「激活即默认」配置模板。

产品计数由插件按客户真值标定的 ProductCounter 算法逐帧自计算（挂主程序 detection_frame
帧级钩子），**不依赖主程序的周期结算**。项目配置只需让模型识别出对应动作标签，
计数精度全在插件侧的逐帧参数里。

默认值由 2026-07-13 三段客户真值视频与对应模型共同标定：
- 视角1正常视频 38 件，插件回放 38 件；小幅移动视频真值 2 件，插件回放 2 件。
- 视角2正常视频 16 次有效换棉签，插件回放 16 次。
- 第 11 件仍合格，第 12 件起逐件判 NG；换棉签动作按连续动作段一次性计数。

所有这些既是「激活时默认」，也能在「项目」页 / 插件配置里随时改。
"""

# 客户真值素材目录（默认模型路径；迁机后可在项目页重新指定）
_DEMO_DIR = r"D:\Tianjun\群光\sensor清洁"

# 视角1：识别两个动作标签 + 默认模型（计数在插件 frame_hook 逐帧做）
VIEW1_PROJECT = {
    "name": "传感器清洁-视角1",
    "task_type": "detection",
    "model_path": f"{_DEMO_DIR}/视角1.pt",
    "conf": 0.7,                                   # 对齐 detect7(1) DEFAULT_CONF
    "iou": 0.45,                                   # 对齐 detect7(1) DEFAULT_IOU
    "pipeline_config": {
        "logic_mode": "detection",
    },
    "steps_config": [
        {"id": 1, "label": "查看产品有无脏污", "displayLabel": "查看产品有无脏污", "order": 1, "confidence": 0.7},
        {"id": 2, "label": "擦拭产品", "displayLabel": "擦拭产品", "order": 2, "confidence": 0.7},
    ],
    # 默认带标准 OK/NG 事件; NG(id=2) 额外累加"工艺告警次数"(show_in_monitor=True 上主页)。
    # 插件三判定默认触发 event_id=2 → 联动报警(需 Alarm 页配 event2)+计数器+Toast。
    "events_config": [
        {"id": 1, "name": "合格(OK)", "color": "#10b981",
         "actions": [{"counter_name": "合格总数", "delta": 1}, {"counter_name": "总产量", "delta": 1}],
         "show_notification": True, "toast_id": "ok", "require_ack": False, "ack_timeout_sec": 0},
        {"id": 2, "name": "不良(NG)", "color": "#ef4444",
         "actions": [{"counter_name": "不良总数", "delta": 1}, {"counter_name": "总产量", "delta": 1},
                     {"counter_name": "工艺告警次数", "delta": 1}],
         "show_notification": True, "toast_id": "ng", "require_ack": False, "ack_timeout_sec": 0},
    ],
    "counters_config": [
        {"name": "合格总数", "value": 0},
        {"name": "不良总数", "value": 0},
        {"name": "总产量", "value": 0},
        {"name": "工艺告警次数", "value": 0, "show_in_monitor": True},
    ],
    "alarm_config": {},
    "detection_config": {},
    "data_config": {},
}

# 视角2：识别换棉签动作 + 默认模型（解锁判定在插件 frame_hook 用稳定窗口逐帧做）
VIEW2_PROJECT = {
    "name": "传感器清洁-视角2-换棉签",
    "task_type": "detection",
    "model_path": f"{_DEMO_DIR}/视角2.pt",
    "conf": 0.7,
    "iou": 0.45,                                   # 对齐 detect7(1) DEFAULT_IOU
    "pipeline_config": {
        "logic_mode": "detection",
    },
    "steps_config": [
        {"id": 1, "label": "更换棉签", "displayLabel": "更换棉签", "order": 1, "confidence": 0.7},
    ],
    # NG(id=2) 额外累加"离岗告警次数"(show_in_monitor=True 上主页), 供"操作员离开超时"判定联动。
    "events_config": [
        {"id": 1, "name": "合格(OK)", "color": "#10b981",
         "actions": [{"counter_name": "合格总数", "delta": 1}, {"counter_name": "总产量", "delta": 1}],
         "show_notification": True, "toast_id": "ok", "require_ack": False, "ack_timeout_sec": 0},
        {"id": 2, "name": "不良(NG)", "color": "#ef4444",
         "actions": [{"counter_name": "不良总数", "delta": 1}, {"counter_name": "总产量", "delta": 1},
                     {"counter_name": "离岗告警次数", "delta": 1}],
         "show_notification": True, "toast_id": "ng", "require_ack": False, "ack_timeout_sec": 0},
    ],
    "counters_config": [
        {"name": "合格总数", "value": 0},
        {"name": "不良总数", "value": 0},
        {"name": "总产量", "value": 0},
        {"name": "离岗告警次数", "value": 0, "show_in_monitor": True},
    ],
    "alarm_config": {},
    "detection_config": {},
    "data_config": {},
}

# 插件耗材约束 + 逐帧计数默认配置（detect9 业务语义 + 客户真值标定）
SWAB_CONFIG = {
    "_config_revision": 2,                     # v1.4.3 换棉签真值门槛配置版本
    "count_channels": [0],                     # 视角1 计数通道
    "count_anchor_label": "查看产品有无脏污",    # 视角1 锚动作标签（detect9 cls0）
    # v1.4.0 双类别计数许可（detect9(1) _both_seen 语义）：非空时该标签出现过即
    # 解锁计数（不必与锚框同帧，两类交替出现也能计）；计到一件/锚跟踪销毁重置，
    # 下一件需再见许可标签。空 = 不启用（v1.2.0 行为）。新模型标签体系
    # （正常产品/脏污产品）的现场把锚标签配"正常产品"、本键配"脏污产品"。
    # v1.4.2 起默认开启（客户真值视频标定出厂值）。
    "count_require_label": "擦拭产品",
    # v1.4.0 按标签 ROI（归一化多边形 [[x,y],...]，中心点在内才算数；空=不限制）
    # key: count_anchor / count_require / swap / fake_wipe
    "label_rois": {},
    "swap_channel": 1,                         # 视角2 换棉签通道
    "swap_label": "更换棉签",                   # 视角2 换棉签动作标签
    "max_uses_per_swab": 11,                   # 第 K 件 OK，第 K+1 件起逐件判 NG
    # 视角2 换棉签解锁判定（独立于视角1 计数）。不用 detect7 移动即计数（真值证明对小幅
    # 换棉签动作严重漏检），改"出现稳定 + 持续时长"判定，更贴真实换棉签次数。
    "swab_lock_time": 0.25,                    # 相邻独立动作最小间隔（秒）；连续动作由动作段状态机去重
    "swab_min_sustain_sec": 0.12,             # 动作段最短持续（秒），客户视角2真值标定值
    "swab_gap_sec": 0.2,                       # 动作段内允许丢框间隔（秒），超过算新段
    "alarm_event": "",                         # （兼容老配置）锁定时直接触发的报警事件类型（空=不触发）
    # —— v1.1.0 三判定 → 主程序事件（host.trigger_event 联动报警/计数器/Toast，不结算周期）——
    "swab_over_limit_event_id": 2,             # 判定1: 棉签寿命超限 → 事件 id（默认 2=NG）
    "fake_wipe_event_id": 2,                   # 判定2: 假擦拭 → 事件 id（0/空=关）
    "fake_wipe_label": "擦拭产品",              # 假擦拭追踪标签（视角1 cid1）
    "fake_wipe_still_time": 2.0,               # 静止超时秒（detect7(1) SWAB_STILL_TIME）
    "fake_wipe_still_disp": 0.0058,            # 位移阈值 归一化（detect7(1) 10px / 1728）
    "operator_absent_enabled": False,          # 判定3 开关（默认关，不误报）
    "operator_absent_event_id": 2,             # 判定3: 操作员离开超时 → 事件 id
    "operator_absent_timeout_sec": 600,        # 离开超时秒（detect7(1) 默认 600=10min）
    "normal_count_event_id": 1,                # 正常计件 → 合格 OK（可改 NG 或 0 关闭）
    "suppress_main_settle_alarm": True,        # 抑制主程序并行周期结算塔灯
    # —— 逐帧计数参数（移动即计数 + 帧硬锁）。v1.4.0 对齐 detect9(1)：demo 在
    # 960 宽显示帧上算像素距离，折算归一化 = 像素/960。客户视频真值回放
    # （视角1-正常.mp4 demo=38 件、小幅度移动视频=2 件）逐件对齐。——
    "move_threshold": 0.02083,                 # 移动判定 = detect9(1) 20px / 960
    "lock_spatial": 0.02604,                   # 位置锁 = detect9(1) 25px / 960
    "lock_time": 3.0,                          # 位置锁 / 计数冷却（秒）
    "move_confirm_frames": 3,                  # 连续 N 帧位移超阈值才确认移动
    "lost_frame_thresh": 5,                    # 连续丢失 N 帧确认产品离开
    # 纵向位移权重：demo 像素域欧氏距离折算归一化域时纵向乘（高/宽）。
    # 1728x1080 现场=0.625（真值标定），16:9 现场=0.5625，1.0=等权老行为
    "dist_y_weight": 0.625,
    # 帧数制回退值；生产默认由 force_lock_sec 按真实时间控制，避免处理帧率改变语义。
    "force_lock_frames": 40,
    # —— v1.4.0 时间制阈值（>0 启用并替代对应帧数制；0=帧数制老行为）。
    # 主程序实时推理丢帧时帧数制跟踪存活过久 → 小幅度移动多计（真值 2 实测 4~5）；
    # v1.4.3 按视频真实时间轴重标，正常=38、小幅度=2；不依赖特定播放速度。——
    "lost_gone_sec": 0.15,                     # 锚缺席 >= N 秒确认离开（0=帧数制）
    "force_lock_sec": 1.4,                     # 计数后强锁 N 秒（0=帧数制）
    # 插件内置信度地板（detect9(1) CONF_THRES=0.7）：主程序监控页滑条低于本值时,
    # 低置信度误检不进计数/许可/换棉签判定；0=不过滤跟随滑条
    "min_confidence": 0.7,
}


def get_templates():
    return {
        "view1_project": VIEW1_PROJECT,
        "view2_project": VIEW2_PROJECT,
        "swab_config": SWAB_CONFIG,
    }
