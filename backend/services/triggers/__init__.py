"""
统一触发中心 (RFC 14, docs/plugin-system/design/14_trigger_hub_rfc.md)

分层:
- actions.py          全局动作注册表 (自 RFC 13 plc/rule_actions.py 上移,
                      PLC 规则与触发中心共用一套动作面; 插件注册一次两边可用)
- sources/            触发源类型注册表 (pixel_region/hid_key/http/serial_pattern/
                      timer/mock, 插件可注册)
- engine.py           每触发源实例: 条件/防抖判定 → 动作队列派发
- manager.py          TriggerHubManager 单例 (配置加载/热重载/状态/触发历史)

设计不变量:
- 既有 8 条成熟触发通道 (扫码/称重/PLC/入站/手动/定时强制/视觉事件/插件桥) 零改动,
  Hub 只承接新增触发源类型
- 触发判定与动作执行全队列化, 绝不阻塞检测热路径 (不变量 15)
- 任一触发源崩溃/设备拔线不影响主程序启动与检测主流程 (错误隔离底线)
- pixel_region 取帧走快照缓存, 不进推理循环
"""
