# TianJun Fleet Hub · Web 集中管控枢纽

> 一个厂区多台工控机的**检测集群监控 + 远程运行操作**集中到局域网 Web 端。
> 设计文档：`docs/plugin-system/design/15_web_hub_rfc.md`（RFC 15）。
> 枢纽是**独立服务**：部署在专用服务器上，不装在工控机；不 import 主程序
> `backend.*`，无 GPU/OpenCV 依赖，任何能跑 Python 3.10+ 的机器都能装。

## 一、部署（三选一）

**方式 A：一键脚本（Ubuntu/Debian 服务器，推荐）**

```bash
sudo bash hub/deploy/install.sh
# 装完编辑 /etc/tianjun-hub.env 改 HUB_ADMIN_PASSWORD, 然后:
sudo systemctl restart tianjun-hub
```

脚本自动完成：前端构建（缺 dist 时）→ 系统用户 + `/opt/tianjun-hub` +
`/var/lib/tianjun-hub` → venv 依赖 → systemd 开机自启 → 健康探活。
**幂等可重跑**：重复运行 = 升级（代码覆盖 + 依赖更新 + 重启，数据不动）。

**方式 B：Docker**

```bash
cd hub && docker compose -f deploy/docker-compose.yml up -d
# 或: docker build -f deploy/Dockerfile -t tianjun-hub . && docker run ...
```

数据落 named volume `tianjun-hub-data`，镜像自带健康检查。

**方式 C：手动（开发/其他发行版）**

```bash
python -m venv /opt/tianjun-hub/venv
/opt/tianjun-hub/venv/bin/pip install -r hub/requirements.txt
cd hub/frontend && npm ci && npm run build   # 仓库不入库 dist, 必须构建
HUB_DATA_DIR=/var/lib/tianjun-hub HUB_ADMIN_PASSWORD='换成强口令' \
python -m uvicorn hub.backend.main:create_app --factory \
    --host 0.0.0.0 --port 9100
```

浏览器打开 `http://<枢纽服务器IP>:9100` → 用 `admin` + 上面设置的口令登录。

**备份与恢复**：备份 = 数据目录（`hub.db` + Fernet 密钥，密钥丢了全部边缘
要重新纳管）。`bash hub/deploy/backup.sh` 在线一致性备份（服务不停，
SQLite backup API 快照），保留最近 14 份，建议进 crontab 每日跑；
恢复方法见脚本尾注释（停服 → 解包回数据目录 → 起服）。

> ⚠️ **首日必做**：不设 `HUB_ADMIN_PASSWORD` 时默认口令是 `admin123`，
> 仅为开箱可用，**上线当天必须改掉**——登录后点顶栏「改密」即可
> （改密成功会吊销全部会话，重新登录一次）。

## 二、纳管一台工控机（边缘机）

1. **边缘机开启接入**（工控机本机操作，二选一）：
   - 设置页开启「枢纽接入」开关；或
   - `PUT /api/v1/hub/config {"enabled": true}`（需 settings.edit 权限）。
   未开启时 `/api/v1/hub/*` 全部 404，存量客户机零行为差异。
2. **边缘机签发 API Key**：系统设置 → API Key 管理 → 新建，scope 选 `hub`，
   记下明文（只显示一次）。
3. **枢纽纳管**：检测集群页右上「纳管节点」→ 填显示名 / `http://<工控机IP>:8001` /
   API Key → 提交。纳管门槛：边缘 License 有效（`valid`）且 API 契约号
   不高于枢纽支持上限，不满足会明确报错。
4. 纳管成功即自动轮询（2s 心跳 / 60s 档案校验），墙上出现该机全部工位。

## 三、环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `HUB_DATA_DIR` | `hub/data/` | 数据目录（hub.db + Fernet 密钥）。**备份这个目录就是备份枢纽** |
| `HUB_ADMIN_PASSWORD` | `admin123` | 首次播种 admin 的口令（仅首次生效） |
| `HUB_PORT` | `9100` | 直跑 `python hub/backend/main.py` 时的端口 |
| `HUB_HEALTH_INTERVAL` | `2` | 心跳轮询间隔（秒） |
| `HUB_PROFILE_INTERVAL` | `60` | 能力档案 hash 校验间隔（秒） |
| `HUB_EDGE_TIMEOUT` | `5` | 访问边缘超时（秒） |
| `HUB_LOCK_TTL` | `120` | 操作锁 TTL（秒） |
| `HUB_EVENT_INTERVAL` | `4` | NG 事件增量拉取间隔（秒） |
| `HUB_EVENT_KEEP_MAX` | `10000` | 报警事件本地保留上限（超限删最老） |
| `HUB_CYCLE_INTERVAL` | `5` | 数据中心统计通道拉取间隔（秒，全量周期 OK+NG） |
| `HUB_CYCLE_PAGE` | `500` | 统计通道单次拉取页大小 |
| `HUB_CYCLE_KEEP_DAYS` | `90` | 周期明细保留天数（更久的历史只留小时聚合） |
| `HUB_ROLLUP_INTERVAL` | `20` | 小时桶脏桶重算节奏（秒） |
| `HUB_ROLLUP_KEEP_DAYS` | `730` | 小时聚合保留天数 |
| `HUB_RETENTION_INTERVAL` | `3600` | 过期数据清理节奏（秒） |
| `HUB_ENABLE_POLLER` | `1` | `0` 关轮询循环（测试用） |

## 四、角色与安全

| 角色 | 能力 |
|---|---|
| `operator` | 只读看墙 / 报警中心（不能确认处理） |
| `engineer` | 看墙 + 远程操作（启停/切项目/消警/报警确认，全部确认框 + 审计 + 操作锁） |
| `director` | 工程师全部 + 夺锁 + 审计查询 |
| `admin` | 全权（纳管节点、用户管理等） |

- **用户管理**（admin，墙顶栏入口）：新建/编辑用户、角色、启停、重置密码；
  最后一个活跃 admin 不允许禁用或降级；改状态/重置密码即时吊销该用户全部会话。
  所有人可用顶栏「改密」自助改口令（验旧密，成功后需重新登录）。
- **报警中心**（墙顶栏入口，带未处理数徽标）：枢纽每 4s 从各边缘增量拉取
  NG 周期事件（游标断点续传，纳管前历史不回翻），列表可筛「只看未处理」，
  「确认处理」记名（幂等，保留首个处理人）并入审计；本地保留上限 1 万条滚动。
- **数据中心**（墙顶栏入口，所有角色只读可看）：跨节点生产统计与趋势分析。
  枢纽独立游标每 5s 拉取全量结算周期（OK+NG）落明细（保留 90 天），后台按
  小时聚合（保留 2 年）。三个页签：**总览**（总产量/合格率/NG 数/平均耗时
  KPI 卡带环比、产量+合格率双轴趋势、NG 原因 Pareto、工位排行最差在上）、
  **NG 分析**（Pareto 点条筛选、原因×工位交叉热力表、NG 样本明细）、
  **周期明细**（逐周期证据层，可按结果过滤分页）。时间预设今天/昨天/近 7 天/
  近 30 天/自定义 × 节点 × 工位全局筛选；每页均可导出当前筛选 CSV。
  口径说明：合格率为件数加权（ΣOK/Σ总），无数据显示「—」不显示 0%；
  趋势图缺数据断线不插值（离线时段一眼可辨）。

- **电视墙 / 无人值守**（检测集群页）：顶栏走秒时钟；全景网格支持
  **自动轮巡翻页**（5~60s 可选，默认 15s，人工翻页自动重置计时）；最新未确认
  NG 以**置顶红条**上墙（点击直达工位处理）；可选**新报警提示音**（默认关，
  🔔 开关，最短间隔 10s 防疲劳）；「电视墙模式」一键全屏 + 隐藏管理入口 +
  Wake Lock 防休眠——车间大屏开机自启用 kiosk 浏览器打开
  `http://<枢纽>:9100/#/?tv=1` 即直进墙态。单格点击先放大（2fps 提帧，
  Esc 退出），放大层内再「进入工位」下钻；画面保比不拉伸。
- **工位下钻·生产实况**：视频下方实时读面（2s 轮询，页面关了边缘零开销）——
  OK/NG/总数、当前/平均/上周期节拍、步骤进度（本周期已完成 ✓ /
  进行中带 in-flight 秒数脉冲点亮）、最近事件（NG 标红带原因）、
  清点模式画面内物品与分类计数；侧栏含视频源类型与推理帧率。
- **节点与审计**（墙顶栏入口，admin/director）：节点管理表（在线状态/主机/
  版本/工位数/**资源占用 CPU·内存·盘**（≥90% 标红）/License/最近异常，
  集群内**版本不一致自动标黄**）；编辑节点（改名即时生效；改地址/换 API Key
  会先连边缘握手验证、目标机器身份不符拒绝保存）；立即轮询与移除纳管
  （边缘机自身不受影响）。**审计台账** Tab：全部写操作留痕可查（时间/用户/
  动作/对象/变更/来源 IP/结果），按节点与动作类型筛选，50 条分页。
  **连接历史** Tab：每次节点离线/恢复自动记账（恢复行带离线时长），
  近 7 日断连次数与累计离线时长按节点汇总。
- **离线告警外推**（「通知设置」Tab，admin）：节点失联超过 N 分钟（默认 5，
  可 0=立即）向配置的通道推送告警，恢复后补发恢复通知（仅当告警真的发过，
  阈值内闪断一条不发防噪音）。通道支持**通用 Webhook / 钉钉群机器人
  （支持加签）/ 企业微信群机器人**，可多通道并发；「发测试消息」逐通道
  验证连通性。默认全关零骚扰；告警权威仍以墙上 NG 置顶条为准。
- **报警升级**（同「通知设置」Tab）：NG 事件在报警中心**无人确认**超过
  N 分钟（默认 0=关）→ 向同一批通道推送升级汇总（"未处理 NG x 条，最老已
  挂 y 分钟"），冷却窗（默认 30 分钟）内最多一条防通知疲劳；有人确认即清账。
  适合"值班没人盯墙 → 升级通知主任"的场景。
- **批量操作**（墙顶栏入口，engineer+）：跨机批量**开始/停止检测**（选中
  节点全部工位）与**批量切项目**——切项目按**项目名**逐台匹配本机项目
  （各机项目 id 空间独立，名字才是跨机通货），本机无同名项目该台明确报错
  不误切、已激活自动跳过。执行前有预览清单（逐工位/逐机一行），执行中逐行
  实时打 ✓/✗ 与失败原因，完成给成功/失败汇总。批量不引入任何新写路径：
  本质是代替人手逐台调用同一个写网关，操作锁/白名单/审计逐条照常生效。
- 枢纽强制登录，无匿名档；所有写操作走唯一网关：权限 → 操作锁 → 能力档案
  白名单 → 转发 → 审计（谁/何时/哪机哪工位/动作/结果/来源 IP，只增不改）。
- 枢纽 → 边缘认证走 M2M API Key（scope=hub），Fernet 加密落枢纽 DB。
- **传输现状（诚实）**：枢纽 → 边缘为局域网明文 HTTP + API Key（边缘后端
  无 TLS 栈，P1 评估）；浏览器 → 枢纽建议放反向代理（nginx/caddy）终结 HTTPS。
- 边缘自治不变量：枢纽下线不影响工控机生产；本机操作员永远优先，
  枢纽侧冲突通过操作锁与孪生对账暴露。
- **实时性**：墙/报警中心走「轮询真相源 + WebSocket 加速器」双通道——
  工位状态翻转、新 NG 事件由 WS 提示帧准实时上墙（`/api/v1/ws?token=`），
  WS 断了自动退化回 2~3s 轮询，零功能损失，无需任何配置。

## 五、部署资产清单（`hub/deploy/`）

| 文件 | 用途 |
|---|---|
| `install.sh` | Ubuntu/Debian 一键安装（幂等，重跑=升级） |
| `tianjun-hub.service` | systemd unit（含安全收敛：ProtectSystem/只写数据目录） |
| `hub.env.example` | 环境配置样例 → 安装为 `/etc/tianjun-hub.env` |
| `backup.sh` | 在线一致性备份 + 14 份轮转（crontab 每日跑） |
| `Dockerfile` / `docker-compose.yml` | 容器化路径（多阶段构建，自带健康检查） |

常用运维命令：`journalctl -u tianjun-hub -f`（看日志）、
`systemctl restart tianjun-hub`（改配置后重启）、
`curl http://127.0.0.1:9100/health`（探活）。

Windows 服务器：用 NSSM 或计划任务包同一条 uvicorn 命令即可（无其他依赖）。

## 六、测试与验收

```bash
# 后端 + 契约 + 多边缘 (无浏览器)
pytest tests/hub/ tests/test_hub_access.py -q

# 可见浏览器 UAT (4 机 12 工位客户拓扑还原, 证据落 test-results/uat_hub_m4/)
python tests/uat/hub_m4/run_uat.py
```
