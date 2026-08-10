"""
内置方案模板库 — 一键载入改地址即用。新客户接入优先从模板起步, 不从零配。

模板即一份 PLCConnectionCreate 载荷 (enabled 恒 False, 载入后现场核对再启用)。
新增客户范式 → 往 BUILTIN_TEMPLATES 加一项 (纯数据, 不涉及代码逻辑)。
"""

BUILTIN_TEMPLATES = [
    {
        "template_id": "s7_db_handshake",
        "template_name": "西门子 S7-300/400 DB 交互 (天永机加线范式, 协议 V0.2)",
        "template_desc": (
            "按《PLC对接协议_S7-300_DB交互_V0.2》一比一: 读区 DB1200 (心跳 DBX0.0 / "
            "读完成 DBX0.2 / 在位 DBX0.3 / 产品号 STRING[28]@2 / 缸型 DBW32); "
            "写区 DB1201 (视觉心跳 / 数据已接收 / 检测完成 / 视觉报警 / 结果码 DBW2 / "
            "产品号回传 STRING[28]@4)。读完成上升沿绑码切型+回执, 无码置视觉报警, "
            "在位下降沿清回执, 检测完成/结果保持至下一件读完成时清零。"),
        "config": {
            "name": "S7 机加线 PLC",
            "driver": "s7",
            "enabled": False,
            "conn_params": {"ip": "172.20.11.111", "rack": 0, "slot": 2,
                            "port": 102, "poll_interval_ms": 100},
            "points": [
                # ---- 读取区 DB1200 (PLC→视觉, 协议第二节) ----
                {"key": "plc_heartbeat", "addr": "DB1200.DBX0.0", "type": "bool", "dir": "read"},
                {"key": "read_done", "addr": "DB1200.DBX0.2", "type": "bool", "dir": "read"},
                {"key": "in_position", "addr": "DB1200.DBX0.3", "type": "bool", "dir": "read"},
                {"key": "product_no", "addr": "DB1200.STRING@2", "type": "string_s7",
                 "length": 28, "encoding": "ascii", "strip": True, "dir": "read"},
                {"key": "cyl_type", "addr": "DB1200.DBW32", "type": "int16", "dir": "read"},
                # ---- 反馈区 DB1201 (视觉→PLC, 协议第四节) ----
                {"key": "vis_heartbeat", "addr": "DB1201.DBX0.0", "type": "bool", "dir": "write"},
                {"key": "data_received", "addr": "DB1201.DBX0.1", "type": "bool", "dir": "write"},
                {"key": "detect_done", "addr": "DB1201.DBX0.2", "type": "bool", "dir": "write"},
                {"key": "vis_alarm", "addr": "DB1201.DBX0.3", "type": "bool", "dir": "write"},
                {"key": "result_code", "addr": "DB1201.DBW2", "type": "int16", "dir": "write"},
                {"key": "product_no_echo", "addr": "DB1201.STRING@4", "type": "string_s7",
                 "length": 28, "encoding": "ascii", "dir": "write"},
            ],
            "read_rules": [
                # 协议§4: 检测完成/检测结果保持至下一工件「读完成」触发时清零
                {
                    "name": "读完成→清上件结果",
                    "when": [{"point": "read_done", "trigger": "rising"}],
                    "debounce_ms": 50,
                    "ack_mode": "none",
                    "actions": [{"do": "write_points", "writes": [
                        {"point": "detect_done", "value": 0},
                        {"point": "result_code", "value": 0},
                    ]}],
                },
                # 协议§3 步骤3-4: 取数→绑码→按缸型切方案→回执(仅供 PLC 监视)
                {
                    "name": "读完成(有码)→绑码切型回执",
                    "when": [
                        {"point": "read_done", "trigger": "rising"},
                        {"point": "product_no", "trigger": "not_equals", "value": ""},
                    ],
                    "debounce_ms": 50,
                    "min_interval_ms": 500,
                    "ack_mode": "none",         # 读完成随工件离站 PLC 自复位
                    "stuck_timeout_s": 30,
                    "stuck_action": "alarm",
                    "actions": [
                        {"do": "bind_sn", "template": "{{product_no}}", "channel": 0},
                        {"do": "switch_project", "source": "cyl_type",
                         "mapping": {"1": None, "11": None}, "unknown": "alarm"},
                        {"do": "write_points", "writes": [
                            {"point": "data_received", "value": 1},
                            {"point": "product_no_echo", "value": "{{product_no}}"},
                        ]},
                    ],
                },
                # 协议§3 异常约定①: 读完成置1但产品号空串 → 无码工件 + 视觉报警
                {
                    "name": "读完成(无码)→视觉报警",
                    "when": [
                        {"point": "read_done", "trigger": "rising"},
                        {"point": "product_no", "trigger": "equals", "value": ""},
                    ],
                    "debounce_ms": 50,
                    "ack_mode": "none",
                    "actions": [
                        {"do": "write_points",
                         "writes": [{"point": "vis_alarm", "value": 1}]},
                        {"do": "alarm", "event": "event2"},
                    ],
                },
                # 协议§3 步骤7: 工件在位下降沿 → 复位「数据已接收」(报警位一并清)
                {
                    "name": "工件离站→复位回执",
                    "when": [{"point": "in_position", "trigger": "falling"}],
                    "debounce_ms": 50,
                    "ack_mode": "none",
                    "actions": [{"do": "write_points", "writes": [
                        {"point": "data_received", "value": 0},
                        {"point": "vis_alarm", "value": 0},
                    ]}],
                },
            ],
            "write_rules": [
                {"on": "heartbeat", "period_ms": 1000,
                 "writes": [{"point": "vis_heartbeat", "value": "toggle"}]},
                # detect_done/result_code 不定时复位 — 保持至下一件读完成清零(上面规则1)
                {"on": "cycle_end", "writes": [
                    {"point": "result_code", "source": "result",
                     "value_map": {"OK": 1, "NG": 2}, "default": 0},
                    {"point": "detect_done", "value": 1},
                ]},
            ],
            "options": {
                "default_channel": 0,
                # 协议§3 异常约定②: PLC 心跳(1s翻转)停超 10s → 提示通讯中断
                "heartbeat_watch": {"point": "plc_heartbeat", "timeout_s": 10,
                                    "action": "alarm", "event": "event2"},
                "on_disconnect": {"alarm_event": "event2"},
            },
        },
    },
    {
        "template_id": "modbus_generic",
        "template_name": "Modbus TCP 通用 (台达/汇川/信捷等)",
        "template_desc": (
            "保持寄存器交互: 触发字上升沿绑定编号 + 周期结束回写结果字。"
            "地址方言 hr:N / ir:N / co:N / di:N (0 基)。"),
        "config": {
            "name": "Modbus PLC",
            "driver": "modbus_tcp",
            "enabled": False,
            "conn_params": {"ip": "192.168.1.10", "port": 502, "unit_id": 1,
                            "poll_interval_ms": 100},
            "points": [
                {"key": "trigger", "addr": "hr:0", "type": "bool", "bit": 0, "dir": "read"},
                {"key": "product_no", "addr": "hr:10", "type": "string_fixed",
                 "length": 20, "encoding": "ascii", "dir": "read"},
                {"key": "result_code", "addr": "hr:100", "type": "int16", "dir": "write"},
                {"key": "detect_done", "addr": "co:0", "type": "bool", "dir": "write"},
            ],
            "read_rules": [{
                "name": "触发→绑码",
                "when": [{"point": "trigger", "trigger": "rising"}],
                "debounce_ms": 50,
                "ack_mode": "self_clear",
                "actions": [
                    {"do": "bind_sn", "template": "{{product_no}}", "channel": 0},
                ],
            }],
            "write_rules": [
                {"on": "cycle_end", "writes": [
                    {"point": "result_code", "source": "result",
                     "value_map": {"OK": 1, "NG": 2}, "default": 0},
                    {"point": "detect_done", "value": 1, "reset_after_ms": 500},
                ]},
            ],
            "options": {"default_channel": 0},
        },
    },
    {
        "template_id": "mock_demo",
        "template_name": "虚拟 PLC (联调/演示)",
        "template_desc": "无硬件跑通全链路: mock-set 置触发位/产品号 → 触发规则 → 写回可查。",
        "config": {
            "name": "虚拟 PLC",
            "driver": "mock",
            "enabled": False,
            "conn_params": {"store_id": "demo", "poll_interval_ms": 50},
            "points": [
                {"key": "read_done", "addr": "m0", "type": "bool", "dir": "read"},
                {"key": "product_no", "addr": "m1", "type": "string_fixed",
                 "length": 20, "dir": "read"},
                {"key": "cyl_type", "addr": "m2", "type": "int16", "dir": "read"},
                {"key": "data_received", "addr": "m3", "type": "bool", "dir": "write"},
                {"key": "result_code", "addr": "m4", "type": "int16", "dir": "write"},
            ],
            "read_rules": [{
                "name": "读完成→绑码切型",
                "when": [{"point": "read_done", "trigger": "rising"}],
                "debounce_ms": 0,
                "ack_mode": "none",
                "actions": [
                    {"do": "bind_sn", "template": "{{product_no}}", "channel": 0},
                    {"do": "write_points", "writes": [
                        {"point": "data_received", "value": 1, "reset_after_ms": 500},
                    ]},
                ],
            }],
            "write_rules": [
                {"on": "cycle_end", "writes": [
                    {"point": "result_code", "source": "result",
                     "value_map": {"OK": 1, "NG": 2}, "default": 0},
                ]},
            ],
            "options": {"default_channel": 0},
        },
    },
]
