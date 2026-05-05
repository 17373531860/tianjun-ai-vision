# language: zh-CN
功能: 实时导出规则
  作为客户工程师
  我希望在每个检测周期结束时自动按规则生成 SN.txt 文件
  以便对接客户产线的扫码即写入流程

  背景:
    假设 已有一个自建模板用于实时规则 名称="rt模板" format=txt content="SN={{ cycle.id }}"

  场景: 创建实时规则成功
    当 我创建实时规则 名称="自动SN输出"
    那么 响应状态应为 200
    并且 规则 enabled 应为 true
    并且 规则 trigger_event 应为 cycle_end

  场景: 列出实时规则能拿到刚建的
    假设 已经创建一条实时规则 名称="规则A"
    当 我请求 GET /api/v1/export/realtime-rules
    那么 响应状态应为 200
    并且 规则列表应包含名称="规则A"

  场景: 启停切换 enabled 状态
    假设 已经创建一条实时规则 名称="切换测试"
    当 我对该规则执行 toggle
    那么 响应状态应为 200
    并且 规则 enabled 应为 false

  场景: 删除实时规则后 GET 返回 404
    假设 已经创建一条实时规则 名称="待删除规则"
    当 我删除该实时规则
    那么 响应状态应为 200
    并且 该规则再次 GET 应返回 404

  场景: test-run 在没有任何 cycle 的情况下也能跑（用 system 上下文）
    假设 已经创建一条实时规则 名称="testrun规则"
    当 我对该规则执行 test-run
    那么 响应状态应为 200
    并且 test-run 结果 status 应为 success
    并且 该规则的 logs 应至少有 1 条
