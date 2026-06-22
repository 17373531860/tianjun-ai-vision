# language: zh-CN
功能: 开机自动恢复检测开关
  作为客户工程师
  我希望能配置软件开机后是否自动恢复上次的检测
  以便有的产线开机即接着干, 有的产线开机停在待机由工人手动确认

  场景: 默认开关为开启
    当 我 GET /api/v1/workstations/auto-resume
    那么 响应状态应为 200
    并且 auto-resume 返回 enabled=true

  场景: 关闭开关后能读回关闭状态
    当 我 PUT /api/v1/workstations/auto-resume 字段 enabled=false
    那么 响应状态应为 200
    并且 GET /api/v1/workstations/auto-resume 应返回 enabled=false

  场景: 重新打开开关后能读回开启状态
    当 我 PUT /api/v1/workstations/auto-resume 字段 enabled=true
    那么 响应状态应为 200
    并且 GET /api/v1/workstations/auto-resume 应返回 enabled=true

  场景: 关闭开关时即使上次在检测也不自动开始
    假设 上次关机时通道正在检测
    并且 开机自动恢复检测开关已关闭
    当 后端执行启动自动恢复
    那么 不应有任何通道被自动开始检测

  场景: 开启开关时即使上次未在检测也无条件自动开始
    假设 上次关机时通道未在检测
    并且 开机自动恢复检测开关已开启
    当 后端执行启动自动恢复
    那么 应执行自动开始检测流程

  场景: 开启开关且上次在检测同样自动开始
    假设 上次关机时通道正在检测
    并且 开机自动恢复检测开关已开启
    当 后端执行启动自动恢复
    那么 应执行自动开始检测流程
