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
    "conf": 0.7,                                   # 对齐 demo DEFAULT_CONF
    "iou": 0.40,
    "pipeline_config": {
        "logic_mode": "detection",
    },
    "steps_config": [
        {"id": 1, "label": "查看产品有无脏污", "displayLabel": "查看产品有无脏污", "order": 1, "confidence": 0.7},
        {"id": 2, "label": "擦拭产品", "displayLabel": "擦拭产品", "order": 2, "confidence": 0.7},
    ],
    "events_config": {},
    "counters_config": {},
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
    "iou": 0.40,
    "pipeline_config": {
        "logic_mode": "detection",
    },
    "steps_config": [
        {"id": 1, "label": "更换棉签", "displayLabel": "更换棉签", "order": 1, "confidence": 0.7},
    ],
    "events_config": {},
    "counters_config": {},
    "alarm_config": {},
    "detection_config": {},
    "data_config": {},
}

# 插件耗材约束 + 逐帧计数默认配置（demo 素材实测调优值，激活即用、全可改）
SWAB_CONFIG = {
    "count_channels": [0],                     # 视角1 计数通道
    "count_anchor_label": "查看产品有无脏污",    # 视角1 锚动作标签（demo cls0）
    "swap_channel": 1,                         # 视角2 换棉签通道
    "swap_label": "更换棉签",                   # 视角2 换棉签动作标签
    "max_uses_per_swab": 11,                   # 一根棉签擦满 K 个产品后锁定（demo K=11）
    "alarm_event": "",                         # 锁定时触发的报警事件类型（空=不触发）
    # —— 逐帧计数参数（demo 1728 宽视频实测值）——
    "move_threshold": 0.0116,                  # 移动阈值 = demo 20px / 1728
    "lock_spatial": 0.0203,                    # 位置锁 = demo 35px / 1728
    "lock_time": 2.0,                          # 时间锁 / 计数冷却（秒）
    "enter_frames": 2,                         # 进入确认帧数
    "leave_frames": 3,                         # 离开确认帧数
    "disappear_tolerance": 20,                 # 短暂消失容忍（抗实时丢帧重复计数）
}


def get_templates():
    return {
        "view1_project": VIEW1_PROJECT,
        "view2_project": VIEW2_PROJECT,
        "swab_config": SWAB_CONFIG,
    }
