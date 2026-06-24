# language: zh-CN
功能: 冷启动就绪探针与加深门槛开关
  作为客户工程师
  我希望后端提供"深度就绪"探针, 并能配置开机是否等深度就绪再放主窗进来
  以便解决"开机项目页空白 / 扫码枪不拉单"的冷启动竞态

  场景: 深度就绪探针匿名可达并返回项目数
    当 我 GET /api/v1/system/startup-ready
    那么 就绪响应状态应为 200
    并且 探针返回 ready=true
    并且 探针返回的 projects 是非负整数

  场景: 默认加深就绪门槛为关闭
    当 我 GET /api/v1/workstations/startup-ready-gate
    那么 就绪响应状态应为 200
    并且 门槛返回 enabled=false

  场景: 开启门槛后能读回开启状态
    当 我 PUT /api/v1/workstations/startup-ready-gate 字段 enabled=true
    那么 就绪响应状态应为 200
    并且 GET /api/v1/workstations/startup-ready-gate 应返回 enabled=true

  场景: 再关闭门槛后能读回关闭状态
    当 我 PUT /api/v1/workstations/startup-ready-gate 字段 enabled=false
    那么 就绪响应状态应为 200
    并且 GET /api/v1/workstations/startup-ready-gate 应返回 enabled=false
