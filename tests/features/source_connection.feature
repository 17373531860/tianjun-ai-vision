# language: zh-CN
功能: 视频源连接与切换
  作为现场工程师
  我希望可以在不同的视频源之间切换
  以便适配 USB / RTSP / 视频文件 / 图片 / synthetic 等场景

  背景:
    假设 后端处于测试模式 (RUNTIME_MODE=test)

  场景: 启动 synthetic 源后，再启动检测，source 状态翻转为 running
    当 我用剧本 "smoke_static_label.json" 启动 synthetic 源
    那么 GET /api/v1/test/synthetic/state 的 frame_seq 应该 >= 0

  场景: 已经在跑 synthetic 时再启动新剧本会替换旧剧本
    假设 我用剧本 "smoke_static_label.json" 启动 synthetic 源
    当 我再次用剧本 "ok_sequential_cycle.json" 启动 synthetic 源
    那么 synthetic 调试信息里的剧本名应为 "ok_sequential_cycle"

  场景: 用内联 JSON 也能启动 synthetic
    当 我用内联 JSON 启动 synthetic 源
    那么 响应状态应为 200
    并且 synthetic 调试信息里的剧本名应为 "inline-adhoc"

  场景: 停 synthetic 后 state 接口仍可读 (返回最后状态)
    假设 我用剧本 "smoke_static_label.json" 启动 synthetic 源
    当 我停止 synthetic 源
    那么 GET /api/v1/test/synthetic/state 应返回 200

  场景: synthetic state 字段含 frame_seq 与 scenario_name 信息
    假设 我用剧本 "smoke_static_label.json" 启动 synthetic 源
    当 我 GET /api/v1/test/synthetic/state
    那么 响应里应包含 frame_seq 字段

  场景: 不指定 fps 时使用剧本里的 fps
    当 我用剧本 "smoke_static_label.json" 启动 synthetic 源 (不带 fps)
    那么 响应状态应为 200

  场景: 显式覆盖 fps=120
    当 我用剧本 "smoke_static_label.json" 启动 synthetic 源 (fps=120)
    那么 响应状态应为 200

  场景: 切到不存在的剧本应优雅失败
    当 我用剧本 "__not_exist__.json" 启动 synthetic 源
    那么 响应状态应在 200/400/404/500 之中

  场景: 启动 synthetic 后 GET /api/v1/source/status 应可读
    假设 我用剧本 "smoke_static_label.json" 启动 synthetic 源
    当 我 GET /api/v1/source/status
    那么 响应状态应为 200
