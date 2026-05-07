# language: zh-CN
功能: 周期性强制动作
  作为质量管理员
  我希望系统能在每做完 N 轮主流程后强制执行某个保养动作
  以便在客户产线上自动追踪清洁/上油/校准等周期性任务

  背景:
    假设 一个 PeriodicActionsMixin 实例已经初始化

  场景: 不配置任何规则时不影响 cycle 流程
    假设 项目配置不包含 periodic_actions
    当 我应用项目配置
    那么 解析后的规则列表应为空
    并且 调用 _check_periodic_actions 不应抛任何异常

  场景: counter 在没做 trigger step 时正常累加
    假设 配置一条规则 interval=3 trigger=E
    当 我连续完成 2 个不含 E 的 cycle
    那么 该规则的 counter 应为 2

  场景: counter 到达阈值时触发 due_warning 事件
    假设 配置一条规则 interval=3 trigger=E due_warning_event_id=10
    并且 events_config 包含 id=10 名称为"该清洁了"的事件
    当 我连续完成 3 个不含 E 的 cycle
    那么 events_log 应记录到 id 为 10 的事件
    并且 该规则的 counter 应为 3

  场景: counter 超过阈值时按 every_cycle 频率触发 overdue
    假设 配置一条规则 interval=2 trigger=E overdue_event_id=20 overdue_repeat=every_cycle
    并且 events_config 包含 id=20 名称为"清洁超期"的事件
    当 我连续完成 5 个不含 E 的 cycle
    那么 id=20 的事件应被触发至少 3 次

  场景: cooldown:N 频率下两次 overdue 之间间隔不少于 N 轮
    假设 配置一条规则 interval=2 trigger=E overdue_event_id=20 overdue_repeat=cooldown:3
    并且 events_config 包含 id=20 名称为"清洁超期"的事件
    当 我连续完成 8 个不含 E 的 cycle
    那么 id=20 的事件触发次数应不超过 3 次

  场景: always 重置策略下任意时刻做 E 都重置 counter
    假设 配置一条规则 interval=5 trigger=E reset_policy=always
    当 我完成 2 个不含 E 的 cycle
    并且 我完成 1 个含 E 的 cycle
    那么 该规则的 counter 应为 0

  场景: only_when_due 策略下未到期做 E 不重置
    假设 配置一条规则 interval=5 trigger=E reset_policy=only_when_due
    当 我完成 2 个不含 E 的 cycle
    并且 我完成 1 个含 E 的 cycle
    那么 该规则的 counter 应为 3

  场景: count_basis=good_only 时 NG cycle 不计数
    假设 配置一条规则 interval=3 trigger=E count_basis=good_only
    当 我完成 1 个 NG 的 cycle
    并且 我完成 1 个 OK 的 cycle
    那么 该规则的 counter 应为 1

  场景: channel_filter 限定时其他通道完全不计数
    假设 配置一条规则 interval=3 trigger=E channel_filter=[1,2]
    并且 当前通道是 0
    当 我完成 5 个不含 E 的 cycle
    那么 该规则的 counter 应为 0

  场景: 持久化文件能正确读写并跨实例恢复
    假设 配置一条规则 interval=10 trigger=E
    当 我完成 4 个不含 E 的 cycle
    并且 我新建一个 mixin 实例并应用相同配置
    那么 新实例上该规则的 counter 应为 4

  场景: 引用不存在的 event_id 时安全降级不崩溃
    假设 配置一条规则 interval=2 trigger=E overdue_event_id=999 overdue_repeat=every_cycle
    并且 events_config 中不包含 id=999
    当 我连续完成 3 个不含 E 的 cycle
    那么 应不抛任何异常
    并且 该规则的 counter 应为 3

  场景: get_periodic_actions_status 返回结构化进度
    假设 配置一条规则 interval=4 trigger=E
    当 我完成 5 个不含 E 的 cycle
    那么 status 应返回 state=overdue remaining=-1 counter=5

  # v3.5.2: 清零计数器同步重置 periodic counter
  场景: reset_stats 时 periodic counter 应被同步清零
    假设 配置一条规则 interval=5 trigger=E
    当 我完成 3 个不含 E 的 cycle
    并且 我调用 reset_stats
    那么 该规则的 counter 应为 0

  场景: reset_stats 时 cooldown 状态也被清掉
    假设 配置一条规则 interval=2 trigger=E overdue_event_id=20 overdue_repeat=once
    并且 events_config 包含 id=20 名称为"清洁超期"的事件
    当 我连续完成 4 个不含 E 的 cycle
    并且 我调用 reset_stats
    并且 我连续完成 3 个不含 E 的 cycle
    那么 id=20 的事件应被触发至少 2 次

  # v3.5.2: 开机首检 (run_on_start) — 启动检测时静默推 counter, 等第一轮自然判定
  场景: run_on_start=true 时启动检测把 counter 推到 interval (静默, 不立即 emit 事件)
    假设 配置一条规则 interval=10 trigger=E run_on_start=true due_warning_event_id=30
    并且 events_config 包含 id=30 名称为"开机首检"的事件
    当 我调用 _run_periodic_actions_on_start
    那么 该规则的 counter 应为 10
    并且 events_log 不应记录到 id 为 30 的事件

  场景: run_on_start 后首个完成步骤 = trigger_step → 静默 reset, 不触发任何事件
    假设 配置一条规则 interval=5 trigger=E run_on_start=true due_warning_event_id=30 overdue_event_id=40 reset_policy=always
    并且 events_config 包含 id=30 名称为"到期"的事件
    并且 events_config 包含 id=40 名称为"超期"的事件
    当 我调用 _run_periodic_actions_on_start
    并且 完成步骤 E
    那么 该规则的 counter 应为 0
    并且 events_log 不应记录到 id 为 30 的事件
    并且 events_log 不应记录到 id 为 40 的事件

  场景: run_on_start 后首个完成步骤 != trigger_step → 立即 emit overdue 事件
    假设 配置一条规则 interval=5 trigger=E run_on_start=true overdue_event_id=40
    并且 events_config 包含 id=40 名称为"超期"的事件
    当 我调用 _run_periodic_actions_on_start
    并且 完成步骤 A
    那么 该规则的 counter 应为 5
    并且 events_log 应记录到 id 为 40 的事件

  场景: run_on_start 首检判定一次性, 第二个步骤不再 emit
    假设 配置一条规则 interval=5 trigger=E run_on_start=true overdue_event_id=40
    并且 events_config 包含 id=40 名称为"超期"的事件
    当 我调用 _run_periodic_actions_on_start
    并且 完成步骤 A
    并且 完成步骤 B
    那么 id=40 的事件触发次数应不超过 1 次

  场景: run_on_start=false 时启动检测不变更 counter
    假设 配置一条规则 interval=10 trigger=E run_on_start=false
    当 我调用 _run_periodic_actions_on_start
    那么 该规则的 counter 应为 0

  场景: run_on_start 不会重复推 — 已经在到期状态保持不变
    假设 配置一条规则 interval=5 trigger=E run_on_start=true due_warning_event_id=30
    并且 events_config 包含 id=30 名称为"开机首检"的事件
    当 我调用 _run_periodic_actions_on_start
    并且 我调用 _run_periodic_actions_on_start
    那么 该规则的 counter 应为 5
