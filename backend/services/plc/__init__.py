"""
通用 PLC 连接器 (RFC 13, docs/plugin-system/design/13_plc_connector_rfc.md)

分层:
- drivers/          协议驱动注册表 (s7/modbus/mc/fins/ethernet_ip/opcua/mock, 插件可注册)
- point_codec.py    点位类型/字节序/编码/缩放 编解码 (纯函数)
- point_engine.py   每连接一线程: 批量轮询 → 值缓存 → 边沿检测 → 触发规则
- rule_actions.py   动作注册表 (bind_sn/switch_project/write_points/..., 插件可注册)
- write_dispatcher.py 事件写回 (cycle_end 等 → 写点位, 队列化不阻塞热路径)
- manager.py        PLCConnectorManager 单例 (配置加载/热重载/状态/手动写/IO 日志)

设计不变量:
- 检测主流程永不直接碰 PLC socket (读走值缓存, 写走队列)
- 任何 driver 库缺失/崩溃不影响主程序启动 (惰性 import + 异常兜底)
- 产品号注入统一走扫码链路 simulate_scan (去重/防呆/绑定/MES 全继承)
"""
