---
name: debug-hub
description: "诊断 Fleet Hub 集中管控枢纽（RFC 15, v3.61）：边缘纳管失败、节点离线/心跳断、电视墙无数据、远程操作被拒/锁冲突、告警不增量、统计不聚合、用户/会话异常。当客户说'枢纽纳管不上''监控墙看不到工位''远程启停没反应''告警中心不更新''统计图空白'时使用。"
argument-hint: "[症状描述]"
---

# debug-hub: Fleet Hub 集中管控枢纽诊断（v3.61.0 首发）

> 完整设计见 `docs/plugin-system/design/15_web_hub_rfc.md`（RFC 15），部署与环境变量见 `hub/README.md`。**本 skill 只做诊断路由，细节以那两份文档 + 代码为准。**

## 一、架构一句话

独立部署的枢纽服务（`hub/` FastAPI+Vue3，专用服务器 :9100，**不 import 主程序 backend.***）通过 HTTP 轮询各工控机的边缘接入面 `/api/v1/hub/*`（`backend/api/hub_access.py`，API Key scope=hub 鉴权），聚合成电视墙/告警/统计。

## 二、两侧文件地图

| 侧 | 文件 | 职责 |
|---|---|---|
| 边缘（主程序） | `backend/api/hub_access.py` | 握手/能力档案/健康摘要端点；`hub_access.enabled` 默认关（关=全部 404 零差异） |
| 边缘 | `backend/api/api_keys.py` | API Key scope=hub |
| 枢纽 | `hub/backend/node_registry.py` | 纳管/节点表 |
| 枢纽 | `hub/backend/poller.py` + `edge_client.py` | 心跳 2s / 档案 60s / 事件 4s / 周期 5s 轮询 |
| 枢纽 | `hub/backend/twin_store.py` | 数字孪生（节点→工位快照） |
| 枢纽 | `hub/backend/wall.py` / `ops.py` / `lock_manager.py` | 电视墙聚合 / 远程操作 / 操作锁（TTL 120s） |
| 枢纽 | `hub/backend/events.py` / `stats.py` / `rollup.py` | 告警增量游标 / 统计 / 小时聚合桶 |
| 枢纽 | `hub/backend/auth.py` / `security.py` / `audit.py` | 四角色（operator/engineer/director/admin）/ 会话吊销 / 审计 |

## 三、症状→检查路由

| 症状 | 先查 |
|---|---|
| 纳管报错 | 边缘 `hub_access.enabled` 开了没（关=404）；API Key scope 是不是 hub；边缘 License 是否 valid；API 契约号是否高于枢纽上限（报错文案明示） |
| 节点离线/心跳断 | 枢纽能否 curl 通边缘 `http://<ip>:8001`；`HUB_EDGE_TIMEOUT`(5s) 内响应吗；边缘重启后 API Key 还在吗 |
| 电视墙无数据/工位缺 | poller 日志；twin_store 是否有该节点快照；边缘健康摘要端点单测 `tests/test_hub_access.py` |
| 远程操作被拒 | 角色够不够（operator 只读）；操作锁被谁持有（director 可夺锁）；审计台账查记录 |
| 告警不更新 | 事件游标（纳管前历史不回翻是设计如此）；`HUB_EVENT_INTERVAL`(4s)；本地保留上限 1 万条滚动 |
| 统计空白 | 周期通道 `HUB_CYCLE_INTERVAL`(5s) 拉取日志；rollup 脏桶重算；保留策略（明细 90 天/小时聚合 730 天） |
| 登录/会话异常 | 改密/改状态/重置密码即时吊销全部会话是设计如此；默认口令 admin123 首日必改 |

## 三.5、v3.62 增量（M6~M9）症状路由

| 症状 | 先查 |
|---|---|
| 全景网格不翻页/顺序乱 | WallView 视图模式与 N×N 规格存 localStorage（电视墙重启记忆）；摊平**保持节点原始顺序不做异常置顶**是设计如此 |
| 电视墙模式出不来/黑屏休眠 | 深链 `#/?tv=1` 直进墙态；Wake Lock 需 https 或 localhost；页面隐藏会重拿锁 |
| 节点失联/NG 升级通知不发 | `hub/backend/notify.py`：通知默认关；失联阈值默认 5min；恢复补发仅当离线侧真发过；NG 升级 `alarm_escalate_min=0`=关、冷却窗 30min 内只发一条汇总；配置在 `hub_settings` KV（`GET/PUT /notify/config`，`POST /notify/test` 逐通道试发） |
| 连接历史没记录 | `hub_node_status_events` 表：unknown→online 初始转换不记（防枢纽重启刷噪音） |
| 工位下钻实况面板空 | 边缘 `GET /api/v1/hub/live`（按需转发 `GET /nodes/{id}/stations/{ch}/live`，2s 轮询只在下钻页开着时发）；边缘版本 < v3.62 无此端点 |
| WS 不推/延迟大 | `hub/backend/ws.py`：**轮询是真相源，WS 只发提示帧**（断线自动回退轮询零功能损失）；token 查询参数鉴权，坏 token 4401；per-topic 0.3s 节流 |
| 批量操作某台报"本机无同名项目" | 按项目名跨机匹配是设计如此（各边缘 project_id 空间独立）；该台不误切，逐行失败原因悬停可见 |
| 改节点地址/换 Key 报 409 | `PUT /nodes/{id}` 先 handshake 验证目标机器 node_uid 一致才落库（防呆），409=指到了别的机器 |
| 部署/备份 | `hub/deploy/install.sh`（幂等重跑=升级）+ `backup.sh`（SQLite backup API 在线快照+Fernet 密钥+14 份轮转） |

## 四、测试矩阵

```bash
~/miniconda3/envs/tianjun/bin/python -m pytest tests/hub/ tests/test_hub_access.py -q   # v3.62 起 84+ 项, fake_edge 仿真多边缘
# M6~M9 专套: test_hub_m6_grid_e2e / m7_tvwall / m7_nodes / m7_opsalarm / m8_ws / m9_batch
# e2e: tests/e2e_browser/test_hub_access_switch.py (边缘开关切换)
# UAT: tests/uat/hub_m4/run_uat.py
```

## 五、不变量

1. 枢纽**绝不 import** 主程序 `backend.*`（无 GPU/OpenCV 依赖，纯 HTTP 对话）
2. 边缘 `hub_access.enabled=false` 时 `/api/v1/hub/*` 全 404，存量客户机零行为差异
3. 备份 = 备份 `HUB_DATA_DIR`（hub.db + Fernet 密钥）
4. 最后一个活跃 admin 不允许禁用/降级
