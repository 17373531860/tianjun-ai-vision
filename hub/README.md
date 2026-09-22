# TianJun Fleet Hub · Web 集中管控枢纽

> 一个厂区多台工控机的**监控墙 + 远程运行操作**集中到局域网 Web 端。
> 设计文档：`docs/plugin-system/design/15_web_hub_rfc.md`（RFC 15）。
> 枢纽是**独立服务**：部署在专用服务器上，不装在工控机；不 import 主程序
> `backend.*`，无 GPU/OpenCV 依赖，任何能跑 Python 3.10+ 的机器都能装。

## 一、五分钟部署（专用服务器）

```bash
# 1. 依赖 (建议独立 venv)
python -m venv /opt/tianjun-hub/venv
/opt/tianjun-hub/venv/bin/pip install -r hub/requirements.txt

# 2. 前端 (仓库已含构建产物 hub/frontend/dist 时跳过)
cd hub/frontend && npm ci && npm run build

# 3. 启动 (前端 dist 存在时同端口分发, 浏览器直接访问 9100)
HUB_DATA_DIR=/var/lib/tianjun-hub \
HUB_ADMIN_PASSWORD='换成强口令' \
python -m uvicorn hub.backend.main:create_app --factory \
    --host 0.0.0.0 --port 9100
```

浏览器打开 `http://<枢纽服务器IP>:9100` → 用 `admin` + 上面设置的口令登录。

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
3. **枢纽纳管**：监控墙右上「纳管节点」→ 填显示名 / `http://<工控机IP>:8001` /
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

- 枢纽强制登录，无匿名档；所有写操作走唯一网关：权限 → 操作锁 → 能力档案
  白名单 → 转发 → 审计（谁/何时/哪机哪工位/动作/结果/来源 IP，只增不改）。
- 枢纽 → 边缘认证走 M2M API Key（scope=hub），Fernet 加密落枢纽 DB。
- **传输现状（诚实）**：枢纽 → 边缘为局域网明文 HTTP + API Key（边缘后端
  无 TLS 栈，P1 评估）；浏览器 → 枢纽建议放反向代理（nginx/caddy）终结 HTTPS。
- 边缘自治不变量：枢纽下线不影响工控机生产；本机操作员永远优先，
  枢纽侧冲突通过操作锁与孪生对账暴露。

## 五、systemd 示例（Ubuntu 服务器）

```ini
# /etc/systemd/system/tianjun-hub.service
[Unit]
Description=TianJun Fleet Hub
After=network.target

[Service]
Environment=HUB_DATA_DIR=/var/lib/tianjun-hub
WorkingDirectory=/opt/tianjun-hub/app
ExecStart=/opt/tianjun-hub/venv/bin/python -m uvicorn \
    hub.backend.main:create_app --factory --host 0.0.0.0 --port 9100
Restart=on-failure
User=tianjun-hub

[Install]
WantedBy=multi-user.target
```

Windows 服务器：用 NSSM 或计划任务包同一条 uvicorn 命令即可（无其他依赖）。

## 六、测试与验收

```bash
# 后端 + 契约 + 多边缘 (无浏览器)
pytest tests/hub/ tests/test_hub_access.py -q

# 可见浏览器 UAT (4 机 12 工位客户拓扑还原, 证据落 test-results/uat_hub_m4/)
python tests/uat/hub_m4/run_uat.py
```
