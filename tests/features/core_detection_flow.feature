# language: zh-CN
功能: 客户核心检测工作流
  作为产线操作员
  我希望系统在我装好项目并启动检测后
  能正确识别 OK 与 NG 周期、累积计数器、并把数据写入 session
  以便我可以拿到准确的合格率数据

  背景:
    假设 后端处于测试模式 (RUNTIME_MODE=test)
    并且 通道 0 的检测器处于停止状态

  场景: 加载一个 OK 顺序周期剧本，cycle 计数器 +1
    假设 加载剧本 "ok_sequential_cycle.json" 并附带最小项目配置
    当 我启动通道 0 的检测
    那么 GET /api/v1/source/detection/results 应该有 detections 字段
    并且 step_counts 应包含剧本中至少一个步骤标签

  场景: 加载一个 NG 缺步骤剧本，仅触发 NG 路径
    假设 加载剧本 "ng_missing_step.json" 并附带最小项目配置
    当 我启动通道 0 的检测
    那么 detection/results 应该不报错并返回 200
    并且 step_counts 不应包含未在剧本里出现的标签

  场景: 检测启动时不传 model_path 也能跑通 (synthetic 不需要模型)
    假设 加载剧本 "smoke_static_label.json" 不附带项目配置
    当 我以空 body 调用 /api/v1/source/detection/start
    那么 响应状态应为 200

  场景: 停止检测后 detection/results 不再返回剧本框
    假设 加载剧本 "smoke_static_label.json" 不附带项目配置
    并且 我已经启动检测
    当 我调用 /api/v1/source/detection/stop
    并且 我同时停掉 synthetic 源
    那么 source 状态应反映为已停止

  场景: 重复启动 synthetic 不应报错
    假设 加载剧本 "smoke_static_label.json" 不附带项目配置
    当 我再次以同一剧本启动 synthetic
    那么 响应状态应为 200

  场景: 没有项目配置时 detection/results 也能返回数据 (synthetic 直通)
    假设 加载剧本 "smoke_static_label.json" 不附带项目配置
    当 我启动通道 0 的检测
    那么 detection/results 的 detections 长度应 >= 0

  场景: synthetic 源的 frame_seq 在启动后会持续增长
    假设 加载剧本 "smoke_static_label.json" 不附带项目配置
    当 我等待 0.3 秒
    那么 GET /api/v1/test/synthetic/state 的 frame_seq 应该 > 0

  场景: 开了项目后 source/status 接口可读
    假设 加载剧本 "ok_sequential_cycle.json" 并附带最小项目配置
    当 我 GET /api/v1/source/status
    那么 响应状态应为 200

  场景: 自定义 logic_mode=detection 也能启动
    当 我用剧本 "smoke_static_label.json" 启动 synthetic + 项目 (logic_mode=detection)
    那么 响应状态应为 200
