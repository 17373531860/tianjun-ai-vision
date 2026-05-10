# language: zh-CN
功能: 扫码 → 绑工件 → MES 推送 工作流
  作为产线操作员
  我希望扫码后系统可以把当前 cycle 与工件号绑定
  并在 cycle 完成时把判定结果推到 MES
  以便 MES 系统拿到完整的工件检测记录

  背景:
    假设 后端处于测试模式
    并且 MES 子系统已经初始化

  场景: 没有 MES 配置时调 push 接口不应崩
    当 我 GET /api/v1/mes/config
    那么 响应状态应为 200

  场景: 设置一个最小 MES 配置后能取回
    当 我 PUT /api/v1/mes/config 一个最小启用配置
    那么 响应状态应为 200 或 201
    并且 GET /api/v1/mes/config 后 enabled 字段应为 True

  场景: scanner 扫码 endpoint 在没绑外设时应优雅返回
    当 我 GET /api/v1/mes/scanner/state
    那么 响应状态应为 200

  场景: 用 synthetic + 项目跑出 cycle 时 MES Hook 不应抛
    假设 加载剧本 "ok_sequential_cycle.json" 并附带最小项目配置
    当 我启动通道 0 的检测
    那么 不应在日志里看到 MES Hook 异常关键词

  场景: GET workorders 列表
    当 我 GET /api/v1/mes/workorders
    那么 响应状态应在 200/404 之中

  场景: GET workpieces 列表
    当 我 GET /api/v1/mes/workpieces?limit=1
    那么 响应状态应在 200/404 之中

  场景: 把 MES 配置 enabled=False 写回也应成功
    当 我 PUT /api/v1/mes/config 一个 disabled 配置
    那么 响应状态应为 200 或 201

  场景: GET MES adapters 列表
    当 我 GET /api/v1/mes/adapters
    那么 响应状态应在 200/404 之中

  场景: 不传 endpoint 字段的 MES 配置应被服务端校验
    当 我 PUT /api/v1/mes/config 一个不带 endpoint 的配置
    那么 响应状态应在 200/400/422 之中
