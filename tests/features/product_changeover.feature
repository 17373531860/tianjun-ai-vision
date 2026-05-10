# language: zh-CN
功能: 换产工作流
  作为产线主管
  我希望切换项目后旧的步骤计数会清零、新项目的 steps_config 会立刻生效
  以便不同产品的检测互不污染

  背景:
    假设 后端处于测试模式

  场景: 切换 synthetic 剧本相当于一次最简换产
    假设 我用剧本 "ok_sequential_cycle.json" 启动 synthetic + 项目
    当 我切换到剧本 "ng_missing_step.json" + 项目
    那么 通道 0 的 step_counts 中不应保留旧剧本独有的标签

  场景: 设 project_config 为空 dict 应被视为清空配置
    当 我 POST /api/v1/source/project_config 一个空 dict
    那么 响应状态应在 200/400 之中

  场景: 切完项目后 detection/results 仍可正常调用
    假设 我用剧本 "smoke_static_label.json" + 项目跑通
    当 我再次启动剧本 "ok_sequential_cycle.json" + 项目
    那么 detection/results 应返回 200

  场景: 直接 GET /api/v1/projects 应能列出已有项目
    当 我 GET /api/v1/projects
    那么 响应状态应为 200

  场景: 以无效 project_id 激活应优雅失败
    当 我 POST /api/v1/projects/0/activate
    那么 响应状态应在 400/404/422/500 之中

  场景: 切回无项目 (synthetic without project) 不应崩
    假设 我用剧本 "ok_sequential_cycle.json" 启动 synthetic + 项目
    当 我用剧本 "smoke_static_label.json" 启动 synthetic 源 (不带 with_project)
    那么 响应状态应为 200

  场景: 同一个剧本连续切两次仍 OK
    当 我用剧本 "ok_sequential_cycle.json" 启动 synthetic + 项目
    并且 我再用剧本 "ok_sequential_cycle.json" 启动 synthetic + 项目
    那么 detection/results 应返回 200

  场景: 项目列表接口含工程方便排查的 fields
    当 我 GET /api/v1/projects
    那么 响应里至少应包含 1 个项目记录
