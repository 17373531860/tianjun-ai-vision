"""RFC 14 内置方案模板库 — 载入后改工位/区域/按键即用 (纯数据, 新范式在此沉淀)。"""

BUILTIN_TEMPLATES = [
    {
        "key": "PIXEL_SETTLE_BUTTON",
        "label": "虚拟按钮 → 手动结算 (遮挡画面标定区)",
        "description": "操作员手遮画面角落标定区即触发当前工位手动结算 (per_item)。"
                       "载入后: 到实时监视里框选区域并标定参考帧, 按现场光照调阈值。",
        "config": {
            "name": "虚拟结算按钮",
            "type": "pixel_region",
            "enabled": False,
            "params": {"channel": 0, "region": [40, 40, 160, 160],
                       "mode": "ref_diff", "threshold": 40, "sample_ms": 150},
            "rules": [{
                "name": "遮挡结算",
                "when": [{"trigger": "rising"}],
                "debounce_ms": 300, "min_interval_ms": 3000,
                "channel": 0,
                "actions": [{"do": "manual_settle"}],
            }],
            "options": {"default_channel": 0},
        },
    },
    {
        "key": "FOOT_PEDAL_SETTLE",
        "label": "脚踏板/按钮盒 → 手动结算 (HID 按键)",
        "description": "USB 脚踏板/按钮盒 (本质 HID 键盘) 按下即结算。"
                       "载入后改 key 为踏板实际输出键 (可用触发历史观察按到的键)。",
        "config": {
            "name": "脚踏板结算",
            "type": "hid_key",
            "enabled": False,
            "params": {"key": "f9"},
            "rules": [{
                "name": "踩踏结算",
                "when": [{"trigger": "pulse"}],
                "min_interval_ms": 2000,
                "channel": 0,
                "actions": [{"do": "manual_settle"}],
            }],
            "options": {"default_channel": 0},
        },
    },
    {
        "key": "HTTP_START_DETECTION",
        "label": "外部系统调 URL → 开始检测",
        "description": "外部系统 POST /api/v1/triggers/fire/start-line 即拉起检测。"
                       "可配共享密钥与 IP 白名单; payload 变量可提进动作模板。",
        "config": {
            "name": "HTTP 开工触发",
            "type": "http",
            "enabled": False,
            "params": {"key": "start-line", "secret": "", "ip_allow": []},
            "rules": [{
                "name": "开始检测",
                "when": [{"trigger": "pulse"}],
                "min_interval_ms": 1000,
                "channel": 0,
                "actions": [{"do": "start_detection"}],
            }],
            "options": {"default_channel": 0},
        },
    },
    {
        "key": "SERIAL_SENSOR_EVENT",
        "label": "串口报文 → 事件中心 (光电/继电器板)",
        "description": "串口设备来一条匹配正则的报文即进事件中心 (报警/计数/语音联动)。"
                       "捕获组可提为变量进动作模板。",
        "config": {
            "name": "串口光电触发",
            "type": "serial_pattern",
            "enabled": False,
            "params": {"port": "COM3", "baudrate": 9600,
                       "pattern": "^TRIG", "vars": {}},
            "rules": [{
                "name": "光电事件",
                "when": [{"trigger": "pulse"}],
                "min_interval_ms": 500,
                "channel": 0,
                "actions": [{"do": "trigger_event", "event": "event3",
                             "reason": "串口光电触发"}],
            }],
            "options": {"default_channel": 0},
        },
    },
    {
        "key": "DAILY_CLEAR",
        "label": "每日定点 → 清零 (统计重置)",
        "description": "每天班前定点自动清零 (结束会话 + 重置计数)。",
        "config": {
            "name": "每日清零",
            "type": "timer",
            "enabled": False,
            "params": {"daily": ["07:30"]},
            "rules": [{
                "name": "班前清零",
                "when": [{"trigger": "pulse"}],
                "channel": 0,
                "actions": [{"do": "clear_reset"}],
            }],
            "options": {"default_channel": 0},
        },
    },
    {
        "key": "MOCK_DEMO",
        "label": "虚拟触发源 (联调/演示)",
        "description": "无硬件全链路联调: mock-fire 注入脉冲 → 触发事件中心。"
                       "验证动作链路后把 type 换成真实触发源即可。",
        "config": {
            "name": "虚拟触发演示",
            "type": "mock",
            "enabled": False,
            "params": {},
            "rules": [{
                "name": "演示规则",
                "when": [{"trigger": "pulse"}],
                "min_interval_ms": 500,
                "channel": 0,
                "actions": [{"do": "trigger_event", "event": "event3",
                             "reason": "mock 触发演示"}],
            }],
            "options": {"default_channel": 0},
        },
    },
]
