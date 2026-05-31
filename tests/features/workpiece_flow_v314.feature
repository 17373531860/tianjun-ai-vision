# language: zh-CN
功能: v3.14 串行流水线 (Workpiece Flow Coordinator) - 端到端集成
  作为产线技术员
  我希望同一工件依次走过 N 个工位都 OK 才算合格
  以便单机内多工位串行装配场景能自动汇总结果

  背景:
    假设 后端处于测试模式 (RUNTIME_MODE=test)
    并且 当前工位已无任何 in-flight 工件

  场景: 福建金龙 TimeWindowTrigger demo - 工件 cycle_start 自动入队, 三工位全 OK 合格
    假设 我创建名为 "fujian-jinlong-line-A" 的流水线, 工位顺序 "0,1,2", 时间窗 FIFO 模式
    并且 我启用该流水线
    当 工位 0 开始一个 cycle 序号 100
    并且 工位 0 结算 cycle 100 OK
    并且 工位 1 开始一个 cycle 序号 101
    并且 工位 1 结算 cycle 101 OK
    并且 工位 2 开始一个 cycle 序号 102
    并且 工位 2 结算 cycle 102 OK
    那么 该流水线的 in-flight 工件数应该为 0
    并且 该流水线的最近一次 run 应该是 COMPLETED_OK

  场景: 短路结算 - 某工位 NG 立即结束, 后续工位不再追
    假设 我创建名为 "short-circuit-line" 的流水线, 工位顺序 "10,11,12", 时间窗 FIFO 模式
    并且 我启用该流水线
    当 工位 10 开始一个 cycle 序号 200
    并且 工位 10 结算 cycle 200 OK
    并且 工位 11 开始一个 cycle 序号 201
    并且 工位 11 结算 cycle 201 NG
    那么 该流水线的 in-flight 工件数应该为 0
    并且 该流水线的最近一次 run 应该是 SHORT_CIRCUITED

  场景: 删除前必须禁用 - 已启用流水线删除应被拒绝
    假设 我创建名为 "no-delete-while-enabled" 的流水线, 工位顺序 "20,21", 时间窗 FIFO 模式
    并且 我启用该流水线
    当 我尝试通过 API 删除该流水线
    那么 应该收到 409 错误响应

  场景: 与工位组互斥 - 同一通道不能既属于工位组又属于流水线
    假设 已存在一个工位组占用通道 "30,31"
    当 我尝试创建工位顺序为 "30,32" 的流水线
    那么 应该收到 400 错误响应并携带提示 "工位组"
