"""对齐 demo 的「激活即默认」配置模板（视角1 计数 / 视角2 换棉签 + 模型 + 计数参数）。

产品计数由插件按 demo 的 ProductCounter 算法逐帧自计算（挂主程序 detection_frame
帧级钩子），**不依赖主程序的周期结算**。项目配置只需让模型识别出对应动作标签，
计数精度全在插件侧的逐帧参数里。

下面的默认值是在 demo 素材（1728x1080 视频 + best.pt）上实测调优后的「开箱即用」值：
- 真跑对比 demo 离线基准 35 件，本套参数把主程序+插件压到 34~35 件（实时丢帧 ±1 极限）。
- 模型/视频换了、客户机丢帧率不同时，按下述说明改对应字段即可（全部可调）：
    * move_threshold / lock_spatial 是「归一化」阈值 = demo 像素阈值 / 视频宽
      （demo 20px、35px @1728 宽 → 0.0116、0.0203）
    * disappear_tolerance 抗实时丢帧重复计数，按现场丢帧率调（demo 离线=0，本机实时=20）
    * 模型路径 model_path 指向客户自己的权重

所有这些既是「激活时默认」，也能在「项目」页 / 插件配置里随时改。
"""

# demo 素材目录（默认模型路径；客户部署时改成自己的权重路径）
_DEMO_DIR = "/home/qianqian/文档/xwechat_files/wxid_9j6tgdyqgpon22_030a/msg/file/2026-06/sensor清洁项目"

# 视角1：识别两个动作标签 + 默认模型（计数在插件 frame_hook 逐帧做）
VIEW1_PROJECT = {
    "name": "传感器清洁-视角1",
    "task_type": "detection",
    "model_path": f"{_DEMO_DIR}/视角1/best.pt",   # 客户改成自己的视角1权重
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
    "model_path": f"{_DEMO_DIR}/视角2/更换棉签.pt",  # 客户改成自己的视角2权重
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

# 插件耗材约束 + 逐帧计数默认配置（detect6 算法参数，激活即用、全可改）
SWAB_CONFIG = {
    "count_channels": [0],                     # 视角1 计数通道
    "count_anchor_label": "查看产品有无脏污",    # 视角1 锚动作标签（detect6 cls0）
    # v1.4.0 双类别计数许可（detect9(1) _both_seen 语义）：非空时该标签出现过即
    # 解锁计数（不必与锚框同帧，两类交替出现也能计）；计到一件/锚跟踪销毁重置，
    # 下一件需再见许可标签。空 = 不启用（v1.2.0 行为）。新模型标签体系
    # （正常产品/脏污产品）的现场把锚标签配"正常产品"、本键配"脏污产品"。
    "count_require_label": "",
    # v1.4.0 按标签 ROI（归一化多边形 [[x,y],...]，中心点在内才算数；空=不限制）
    # key: count_anchor / count_require / swap / fake_wipe
    "label_rois": {},
    "swap_channel": 1,                         # 视角2 换棉签通道
    "swap_label": "更换棉签",                   # 视角2 换棉签动作标签
    "max_uses_per_swab": 11,                   # 一根棉签擦满 K 个产品后锁定（K）
    # 视角2 换棉签解锁判定（独立于视角1 计数）。不用 detect7 移动即计数（真值证明对小幅
    # 换棉签动作严重漏检），改"出现稳定 + 持续时长"判定，更贴真实换棉签次数。
    "swab_lock_time": 2.0,                     # 两次解锁最小间隔（秒），防一次动作重复解锁
    "swab_min_sustain_sec": 0.0,              # 动作段最短持续（秒），默认0=照搬 demo 不防抖；现场要滤瞬时误检调大（如0.12），用秒跨帧率鲁棒
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
    # 计数后强制锁定帧数（detect6 原值 40）。detect6 基准 44 已确认；当源帧率 <= 主程序
    # 推理速度(约 56fps)时几乎不丢帧、实时时钟=视频时间, 40 即对齐 44。
    # 若视频源帧率高于推理速度(如 60fps test.mp4)会丢帧, 需按现场丢帧率调大本值。
    "force_lock_frames": 40,
    # —— v1.4.0 时间制阈值（>0 启用并替代对应帧数制；0=帧数制老行为）。
    # 主程序实时推理丢帧时帧数制跟踪存活过久 → 小幅度移动多计（真值 2 实测 4~5）；
    # 时间制按真实缺席时长判离场，跨帧率语义一致。真值回放 0.15s/1.6s 下
    # 小幅度视频全帧/丢帧都=2，正常视频 37/36（demo=38）。——
    "lost_gone_sec": 0.15,                     # 锚缺席 >= N 秒确认离开（0=帧数制）
    "force_lock_sec": 1.6,                     # 计数后强锁 N 秒（0=帧数制）
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
