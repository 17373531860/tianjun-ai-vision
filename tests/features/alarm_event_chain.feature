# language: zh-CN
功能: 报警事件链路
  作为质量管理员
  我希望事件触发后系统能记录 events_log，并尝试驱动报警设备
  以便现场操作员能感知到异常

  背景:
    假设 后端处于测试模式

  场景: alarm/state 接口可访问
    当 我 GET /api/v1/alarm/state
    那么 响应状态应为 200

  场景: 持续触发同一标签 (alarm_event 剧本) 不应让后端崩溃
    假设 加载剧本 "alarm_event.json" 不附带项目配置
    当 我启动通道 0 的检测
    那么 detection/results 应返回 200

  场景: 没有 alarm 配置时手动触发报警端点应优雅响应
    当 我 POST /api/v1/alarm/test (空 body)
    那么 响应状态应在 200/400/404 之中

  场景: events_log 接口在没有事件时也能返回空列表
    当 我 GET /api/v1/sessions (取最近一条)
    那么 响应状态应为 200
