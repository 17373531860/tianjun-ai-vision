# language: zh-CN
功能: 会话标识与 session_end 自动导出
  作为现场操作员
  我希望给每次开机会话起一个业务标识 (英文/批次号 等)
  以便在导出文件名 / 数据中心列表 / MES 接口里能直接认出
  并且会话结束时能自动按规则出整次会话的报告

  场景: 启动检测时不填会话标识 → DB 中 name 为 null
    假如 后端测试模式已就绪
    并且 已停止任何残留检测
    当 客户端用 POST /api/v1/source/detection/start 启动检测但不传 session_name
    那么 响应状态应为 200 或 400
    并且 如果创建了会话 它的 name 字段应为 null

  场景: 启动检测时填合法会话标识 → DB 中 name 写入
    假如 后端测试模式已就绪
    并且 已停止任何残留检测
    当 客户端用 POST /api/v1/source/detection/start 启动检测并传 session_name "LINE3-NIGHT-20260510"
    那么 响应状态应为 200 或 400
    并且 如果创建了会话 它的 name 字段应为 "LINE3-NIGHT-20260510"

  场景: 启动检测时填非法字符 → 400
    假如 后端测试模式已就绪
    当 客户端用 POST /api/v1/source/detection/start 启动检测并传 session_name "bad/name"
    那么 响应状态应为 400

  场景: 启动检测时填星号通配符 → 400
    假如 后端测试模式已就绪
    当 客户端用 POST /api/v1/source/detection/start 启动检测并传 session_name "abc*def"
    那么 响应状态应为 400

  场景: PATCH /sessions/{id}/name 重命名一次会话
    假如 后端测试模式已就绪
    并且 数据库里至少有一个会话存在
    当 客户端用 PATCH 给该会话设置标识 "BATCH-A1234"
    那么 响应状态应为 200
    并且 响应里 name 字段应为 "BATCH-A1234"

  场景: 重命名为空 → 标识被清空
    假如 后端测试模式已就绪
    并且 数据库里至少有一个会话存在
    当 客户端用 PATCH 给该会话传 name=null
    那么 响应状态应为 200
    并且 响应里 name 字段应为 null

  场景: 重命名为非法字符 → 400
    假如 后端测试模式已就绪
    并且 数据库里至少有一个会话存在
    当 客户端用 PATCH 给该会话传 name="path/like"
    那么 响应状态应为 400

  场景: 自动导出规则可以选 session_end 触发
    假如 后端测试模式已就绪
    当 客户端用 POST /api/v1/export/realtime-rules 创建一个 trigger_event=session_end 的规则
    那么 响应状态应为 200 或 201

  场景: 不存在的 session_id 调用重命名 → 404
    假如 后端测试模式已就绪
    当 客户端用 PATCH /api/v1/data/sessions/999999/name 传任意 name
    那么 响应状态应为 404

  # ==========================================================================
  # 客户反馈 v3.6.2: session 范围导出, stats.cycles[*].steps 必须有内容
  #   客户模板 {{ cycle.steps|map(attribute='label')|join(' -> ') }} 之前拿到空,
  #   step_by_label['拿取'].duration 全部 0 — bug.
  # ==========================================================================

  场景: session 范围导出 stats.cycles 里每个 cycle 都带 steps 列表 + 客户惯用别名
    假如 后端测试模式已就绪
    并且 数据库里至少有一个会话存在
    当 客户端用 POST /api/v1/export/preview 以 session 范围渲染 jinja 模板
    那么 响应状态应为 200
    并且 响应 context 里 stats.cycles 每条都应含字段 "steps"
    并且 响应 context 里 stats.cycles 每条都应含字段 "interval"
    并且 响应 context 里 stats.cycles 每条都应含字段 "event"
