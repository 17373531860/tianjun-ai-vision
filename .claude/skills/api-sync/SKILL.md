---
name: api-sync
description: "前后端 API 对齐检查（v3.5.x 主线）：19 组 /api/v1/* 路由前缀、前端 16 个 axios 客户端、字段命名、已知不一致与修复流程。怀疑前后端数据不通或新增 API 后必读。"
argument-hint: "[具体的 API 对齐问题，或 'full-check' 做全量检查]"
model: opus
effort: high
allowed-tools: "Read, Grep, Glob, Bash, Agent, mcp__context7"
---

# api-sync — 前后端 API 对齐（v3.5.x）

事实源：`AGENTS.md` 第五节（19 组路由前缀）+ `backend/main.py` 末尾的 `app.include_router(...)` 块 + `frontend/src/api/*.js`。
本 skill 只列**真相**，不要凭记忆改路径。

需求：$ARGUMENTS

---

## 0. 不变量（先读这条再做任何事）

1. **后端所有路由前缀都是 `/api/v1/`，不是 `/api/`**。
   - 客户/旧 PR/旧 hotfix 看到 `/api/detection/*` 一律视作过期（v2.7.x 已删 `backend/api/detection.py`）。
   - 等价端点在 `/api/v1/source/detection/*`（`source_routes.py`）。
2. **前端 axios 实例在 `frontend/src/api/index.js`**：`baseURL = http://localhost:8001/api/v1`。
   所有 `api.get('/foo')` 实际打 `http://localhost:8001/api/v1/foo`，**前端写路径时不要再加 `/api/v1`**。
3. **MJPEG / 备份下载走 `getBackendHost()`**（不带 `/api/v1`）：例如 `${getBackendHost()}/video_feed`、`${getBackendHost()}/api/v1/data/videos/{id}`。
4. **新增端点必须挂到下面 19 组前缀之一**；如果没有合适的，先回到 `add-api-endpoint` skill 决定挂哪儿。

---

## 1. 19 组路由前缀（v3.5.x 真相表）

`backend/main.py` 第 772–793 行直接 `include_router`；`backend/api/__init__.py` 通过 `api_router` 聚合 9 个子模块。前缀全部在 `/api/v1/` 下。

| `/api/v1/` 后的前缀 | 后端文件 | 行数 | 一句话 |
|---|---|---|---|
| `/source/*` | `backend/api/source.py`（router 容器）+ `source_routes.py` | 1573 + **1279** | 视频源采集 + 推理状态机的核心 API（42 个端点） |
| `/data/*` | `sessions.py` + `sessions_export.py` + `sessions_stats.py` + `sessions_maintenance.py` | 1004+527+185+403 ≈ **2119** | session/cycle/step + CSV 导出 + 数据维护 |
| `/projects/*` | `projects.py` | **334** | 项目 CRUD + 激活 + `/active/current` |
| `/models/*` | `models.py` | **792** | 模型上传/转换/标签解析 |
| `/tasks/*` | `tasks.py` | **192** | 离线推理任务（前端 `task.js` 已 dead，见 §3） |
| `/reports/*` | `reports.py` | **344** | 趋势/日报/导出（前端 `report.js` 仅在 dead 视图引用） |
| `/cameras/*` | `cameras.py` | **156** | **旧式相机表**，与 `/source/*` 并存；新代码不要往这写 |
| `/system/*` | `system_display.py` | **135** | KV 配置 + license 缓存（`/display`、`/license-cache`） |
| `/alarm/*` | `alarm.py` | **1008** | 灯塔 / 蜂鸣器 / 共享灯柱（`AlarmRouter`） |
| `/workstations/*` | `channel_manager.py` | **373** | 多工位 + GPU 分配 + `channel-config` |
| `/scanner/*` | `scanner.py` | **532** | 扫码器 CRUD + scan_pair + disable-toggle |
| `/scanner/wmax/*` | `wmax.py` | **672** | WMax 三端口协议（35+ 端点） |
| `/external-devices/*` | `external_device.py` | **365** | 称重器/串口外设（注意尾部斜杠） |
| `/cluster/*` | `cluster.py` | **295** | 集群主从 + 心跳 + box 聚合 |
| `/mes/*` | `mes.py` | **673** | 工单/工件/缺陷/缺陷码 |
| `/mes/gateway/*` | `mes_gateway.py` | **450** | 外部 MES 推送连接 + 测试 + 额外字段 |
| `/operators/*` | `operators.py` | **213** | 操作员（无 token，落盘 `current_operator.json`） |
| `/export/*` | `export_custom.py` + `export_realtime.py` | 526+305 ≈ **831** | v3.5.0 自定义导出（模板 + 实时规则共用前缀） |
| `/debug/*` | `debug.py` | **118** | 通道诊断 / 集群流测试 |

> 注：`source.py` 自身只声明 `router = APIRouter()`，所有 42 个端点在 `source_routes.py` 里挂载（`from .source import router`）。对外仍是 `/api/v1/source/*`。

---

## 2. 前端 API 客户端清单（`frontend/src/api/`）

16 个文件，**14 个对应后端前缀**，**2 个 dead**。所有文件统一 `import api from './index'`，`api.get('/xxx')` 自动加 `/api/v1` 前缀。

| 前端文件 | 行 | 对应后端前缀 | 状态 |
|---|---|---|---|
| `index.js` | 102 | (axios 实例 + `getBackendHost`) | 基础设施，不要乱改 |
| `detection.js` | 48 | `/source/*` + `/workstations/*` + `/scanner/scan-pair/*` | 在用，**含 1 处 dead 调用**：`resetDetection` 仍打 `/detection/reset`（已废弃，应删） |
| `data.js` | 223 | `/data/*` | 在用（含 v3.4.3 `/data/cycles/by-serial`、v3.5.x `pt_mode/ct_mode`） |
| `project.js` | 25 | `/projects/*`（+ `/models` 下拉） | 在用 |
| `model.js` | 52 | `/models/*` | 在用 |
| `scanner.js` | 25 | `/scanner/*` | 在用（含 v3.4.0 `check-container-mode` / v3.4.2 `disable-status` `disable-toggle`） |
| `wmax.js` | 84 | `/scanner/wmax/*` | 在用 |
| `mes.js` | 36 | `/mes/*` | 在用 |
| `gateway.js` | 20 | `/mes/gateway/*` | 在用 |
| `cluster.js` | 23 | `/cluster/*` | 在用 |
| `external_device.js` | 14 | `/external-devices/*` | 在用（注意 list/create 端点 **要尾斜杠** `/external-devices/`） |
| `operators.js` | 8 | `/operators/*` | 在用 |
| `export.js` | 160 | `/export/*` | 在用（v3.5.0 自定义导出 + 实时规则） |
| `report.js` | 25 | `/reports/*` | **半 dead**：`getSummary`、`getDailyStats`、`exportCsvReport` 仅 `views/Report/index.vue` 引用，而 `Report` 路由在 `router/index.js` 中**未注册**；其余三个函数（`getRecords`/`getTrend`/`exportPdfReport`）全前端无引用 |
| `task.js` | 25 | `/tasks/*` | **dead**，全前端无 import（保留以防外部脚本使用） |
| `camera.js` | 25 | `/cameras/*` | **dead**，全前端无 import |

清理建议（只做不删）：

- 不要主动删 `task.js` / `camera.js`：可能被某些客户脚本或 hotfix 拽过去。但**新代码不要 import 它们**。
- `detection.js:29` 的 `resetDetection` → `/detection/reset` 是真死链，建议下次清理时**删该函数**或改路由到 `/source/detection/reset-stats`。当前 Monitor 视图 `import` 了它但没调用（line 1054 import / line 3670+3725 只用 `resetDetectionStats`）。

---

## 3. 已知前后端不对齐问题（v3.5.x 现状）

### 3.1 路径前缀类（高频踩坑）

| 现象 | 原因 | 修法 |
|---|---|---|
| 前端 404 `/api/detection/*` | 老路由 `backend/api/detection.py` 已删 | 改前端路径到 `/source/detection/*`（去掉 `/api/v1` 前缀，因为 axios baseURL 已含） |
| 前端 404 `/api/v1/api/v1/...` | 误把 `/api/v1` 又拼到 `api.get('/...')` 里 | 前端只写 `/foo`，axios 自动加 baseURL |
| MJPEG 连不上 | 用了 `api.get('/video_feed')`（带了 `/api/v1`） | 改用 `${getBackendHost()}/video_feed` |
| 备份下载在浏览器跨域被拦 | 用 `api.get` 而不是 `window.open` | 用 `data.js::backupDatabase()`，走 `getBackendHost()` 直接 open |

### 3.2 字段命名类

后端 Pydantic 全部 `snake_case`；前端在 `data.js` / `gateway.js` 等做 camelCase ↔ snake_case 转换：

```13:35:frontend/src/api/data.js
export const getSessionsByDate = (date, projectId = null, startHour = null, endHour = null, channelId = null, shift = null, operatorId = null) => {
  const params = {};
  if (projectId) params.project_id = projectId;
  if (startHour) params.start_hour = startHour;
  if (endHour) params.end_hour = endHour;
  if (channelId !== null && channelId !== undefined) params.channel_id = channelId;
  if (shift) params.shift = shift;
  if (operatorId !== null && operatorId !== undefined) params.operator_id = operatorId;
  return api.get(`/data/sessions/by-date/${date}`, { params });
};
```

容易出错的位置：

- `channel_id` vs `channel`：query 参数有时叫 `channel`（`/source/detection/start?channel=0`），有时叫 `channel_id`（`/scanner/scan-pair/active?channel_id=0`、`/data/sessions/by-date?channel_id=0`）。**对齐看后端签名**。
- `device_id` vs `id`：扫码器和外设两边都用整数主键 `id`，但 `injectBarcode` body 里要写 `device_id`。
- `bound_channels`（gateway）/ `broadcast_channels`（scanner）/ `_disabled_channels`（mes_hooks 内部 JSON）：**三个独立概念**，不要混用。
- `pt_mode` / `ct_mode`：v3.5.x 新增，CSV 导出的"耗时显示"模式，前端是 `ptMode/ctMode`。

### 3.3 路由顺序坑（FastAPI）

具体路径**必须放在**带参数的路径**前面**，否则 FastAPI 会把静态字段当成参数：

| 文件 | 风险点 | 已修 |
|---|---|---|
| `projects.py` | `/active/current` 必须在 `/{project_id}` 前 | ✅ |
| `external_device.py` | `/logs`、`/status`、`/test` 必须在 `/{device_id}` 前 | ✅（v2.7.5 修） |
| `cluster.py` | `/boxes/{box_serial}` 必须在 `/boxes`（list） 后 | ✅ |
| `scanner.py` | 不少静态路径（`/discover`、`/simulate`、`/check-container-mode`、`/scan-pair/*`、`/disable-*`），都在 `/devices/{id}` 之前注册 | ✅ |

### 3.4 Vite 代理 vs Electron 直连

```29:51:frontend/src/api/index.js
function getBaseURL() {
  if (ENV_API_BASE_URL) return trimSlash(ENV_API_BASE_URL);
  return `${DEFAULT_BACKEND_HOST}/api/v1`;
}
export function getBackendHost() {
  if (ENV_API_BASE_URL && /^https?:\/\//i.test(ENV_API_BASE_URL)) {
    try {
      const url = new URL(ENV_API_BASE_URL);
      return `${url.protocol}//${url.host}`;
    } catch (_) {}
  }
  return isDesktopApp() ? DEFAULT_BACKEND_HOST : '';
}
```

- **API 永远直连**：`getBaseURL()` 强制 `http://localhost:8001/api/v1`，不走 Vite 代理。
- **MJPEG/视频文件**走 `getBackendHost()`：Electron 下 `'http://localhost:8001'`，浏览器下 `''`（同源 + Vite 代理把长连接给后端）。
- 如果客户机上后端不在 `localhost:8001`，必须设 `VITE_API_BASE_URL`（`AGENTS.md` §10）。

### 3.5 已删 / 已停用（看到引用立即清理）

| 资源 | 状态 | 替代 |
|---|---|---|
| `backend/api/detection.py` | **已删**（v2.7.x） | `source_routes.py` 内 `/source/detection/*` |
| `backend/services/detector.py` | **已删** | `VideoSourceManager` 内置推理 |
| `POST /api/v1/detection/reset` | **404**（路由不存在） | `POST /api/v1/source/detection/reset-stats` |
| `frontend/src/api/task.js` | dead | 离线任务已不在前端入口；只剩 `/tasks/*` 后端在线 |
| `frontend/src/api/camera.js` | dead | 视频源 CRUD 走 `/source/*` 和 sourceStore（前端 `useSourceStore.js`） |
| `frontend/src/views/Report/index.vue` | 路由未挂载 | `views/Data/index.vue` 接管报表展示 |

---

## 4. 关键端点速查（最常对齐的 30 个）

挑前端实际频繁调用、且字段最容易错的端点。完整列表用 §5 的检查命令拉。

### 4.1 source（`/api/v1/source/*`）

| 方法 | 路径 | query 关键字段 | body 关键字段 |
|---|---|---|---|
| GET | `/source/cameras` | — | — |
| POST | `/source/camera/start` | `channel` | `camera_index` 等 |
| POST | `/source/rtsp/start` | `channel` | `rtsp_url` |
| POST | `/source/video/upload` | `channel` | (multipart) |
| POST | `/source/video/start` | `channel` | `video_path` |
| POST | `/source/hcnetsdk/start` | `channel` | NVR 参数 |
| POST | `/source/detection/start` | `channel` | `model_path` `conf` `iou` |
| POST | `/source/detection/stop` `pause` `resume` `standby` `resume-inference` `reset-stats` | `channel` | — |
| POST | `/source/detection/set-project` | `channel` | `pipeline_config` 等 7 个 JSON |
| POST | `/source/detection/rebind` | `channel` | 手动重绑工件 |
| POST | `/source/detection/clear_pending_scan` | `channel` | — |
| POST | `/source/detection/recording-failures/clear` | `channel` | — |
| GET | `/source/detection/results` | `channel` | — （返回 `mes` 子树含 `scan_event`/`rebind_prompt`/`warn_no_barcode`/`recording_failures`） |
| GET/POST | `/source/transform/config` | `channel` | `rotation` `flip_h` `flip_v` |
| GET/POST | `/source/stream/config` `/kalman/config` `/gpu/*` | `channel` | — |

### 4.2 data（`/api/v1/data/*`，前端 `data.js`）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/data/sessions` | list（`project_id` `channel_id` `operator_id` `shift` `start_hour` `end_hour`） |
| GET | `/data/sessions/dates` | 有数据的日期 |
| GET | `/data/sessions/by-date/{date}` | 当日 overview |
| GET | `/data/sessions/{id}` | 单条 |
| GET | `/data/sessions/{id}/cycles` | 周期列表（`skip` `limit`） |
| GET | `/data/cycles/{id}` `/cycles/{id}/steps` | 周期详情 + 步骤 |
| GET | `/data/cycles/by-serial/{serial_no}` | v3.4.3 工件全局检索 |
| GET | `/data/videos` `/videos/{id}` | 录像列表/单条流（`{id}` 走 MJPEG/MP4） |
| GET/PUT | `/data/export-settings` | 导出开关 |
| GET | `/data/export/csv` | `export_type=session\|cycle\|all`，`pt_mode`/`ct_mode`/`project_id`/`channel_id`/`start_date`/`end_date`/`week`/`month` |
| GET | `/data/stats/step-averages` `/stats/cycle-averages` | 聚合统计（v3.5.0 含置信度） |
| GET/PUT | `/data/cleanup-settings` | 自动清理配置 |
| POST | `/data/cleanup/run` | 手触清理 |
| GET | `/data/storage-info` | 磁盘 |
| DELETE | `/data/clear/all` | 全清 |
| DELETE | `/data/clear/range` | 按 body 日期段清（注意 `data:` 不是 `params:`） |
| GET | `/data/backup/database` | 整库下载（前端用 `window.open`） |

### 4.3 scanner / wmax / mes / gateway

- 所有 scanner 状态查询（`/status` `/latest/{ch}` `/logs` `/check-container-mode` `/scan-pair/*` `/disable-*`）都在静态路径区；具体设备 CRUD 走 `/devices/{id}`。
- `/mes/gateway/connections/by-channel?channel=N` 是 v2.6.0 新增，按通道查绑定的连接。
- `/mes/orders/{id}/extra-data`（PUT）— 工单自定义字段，前端 `mes.js::updateOrderExtraData`。

### 4.4 export（v3.5.0+）

挂在 `/api/v1/export/*` 下，前端只有 `export.js` 一个文件：

- `GET /export/fields?flat=false` — 308 字段树（`export_field_registry.py`）
- `GET/POST /export/templates` `/templates/{id}` `/templates/{id}/clone` `/templates/{id}/upload-template-file`
- `POST /export/preview` `/export/render` — 预览/下载（`fmt=txt|csv|docx|xlsx|pdf`，**前端 `export.js` 注释只列了 txt/csv，是过时的**）
- `GET/POST/PUT/DELETE /export/realtime-rules` `/realtime-rules/{id}/toggle` `/test-run` `/logs`
- `GET /export/run-logs` — 全部触发日志

---

## 5. 检查方法（手动对齐）

### 5.1 快速检查（针对单个 API）

```bash
# 1) 前端调用点（路径 + 字段名）
rg -n "api\.(get|post|put|delete)\([\"\\\`']/source/detection" frontend/src

# 2) 后端端点定义
rg -n "@router\.(get|post|put|delete)\(\"/detection/" backend/api/source_routes.py

# 3) Pydantic 字段（snake_case）
rg -n "class .*Request|class .*Create|class .*Update" backend/api/source_routes.py
```

### 5.2 全量检查（`$ARGUMENTS = full-check`）

```bash
# 后端：所有 @router 装饰器（按文件分组）
rg -n "@router\.(get|post|put|delete|patch)\(" backend/api/

# 前端：所有 axios 调用（按文件分组）
rg -n "api\.(get|post|put|delete)\(" frontend/src/api/ frontend/src/views/

# 找前后端不同名的 query 参数
rg -n "channel_id=|channel=" frontend/src/api/*.js backend/api/*.py
```

### 5.3 烟测工具

`tools/smoke_all_routes.py`（v2.7.x 新增）— TestClient 跑全部 FastAPI 路由，结果写 `smoke_report.txt`。已加 `SKIP_PATHS`：`/scanner/wmax/auto-discover`、`/video_feed`、`/snapshot`、`/source/shutdown/complete`、`/system/restart`（这些一跑就摸真硬件，不能 smoke）。

`tools/check_imports.py` — 静态扫 `backend/api`、`backend/services` 找漏 import 的 `os/cv2/threading/uuid/datetime` 等（`source.py` mixin 拆分后 `NameError` 高发）。

---

## 6. 修复原则

1. **前端适配后端**：后端路由稳定后，前端改路径；不要轻易改后端路由（Electron 关机 / 客户脚本可能直连）。
2. **新增字段可以，不删旧字段**：保持向后兼容；老安装包升级时 `backend/main.py: migrate_database()` 会跑 60+ ALTER TABLE。
3. **路由顺序**：静态路径放参数路径之前（§3.3）。
4. **删端点先打废弃日志**：连续 1–2 个版本返回 410 + `Deprecation` 头，再彻底移除。
5. **新增端点必读 `add-api-endpoint`**：含路由注册、Schema、router 挂载、前端 `api/*.js` 封装、视图集成的全链路 checklist。
6. **碰 ORM 字段必读 `modify-model`**：改完字段必须在 `migrate_database()` 加 ALTER TABLE，前端 axios 调用点同步改。

---

## 7. 历史变更速查（按版本）

- **v2.7.x**：删 `backend/api/detection.py`；source 拆分为 `source.py`(1573) + 11 个 mixin；sessions 拆分为 4 个文件；`/api/v1/reports/daily-stats` SQLite 兼容；CORS 加 `localhost:6001` regex。
- **v2.7.2**：`/data/export/csv` 新增 `project_id` `channel_id` query；`MESHookManager.on_channel_removed` + `AlarmRouter.on_channel_removed` 公共接口。
- **v2.7.5**：`/source/transform/config` 画面变换；`external_device` 路由顺序 + 错误返回 200+warning。
- **v3.0.0**：多通道 + ChannelManager + `/scanner/simulate` + `/external-devices/simulate`。
- **v3.1.3**：FFmpeg 单线程保命（不影响 API）。
- **v3.3.0**：scan_pair 状态机 + `/scanner/scan-pair/active` `/stop`。
- **v3.4.0**：LON D 模式 + `/scanner/check-container-mode`。
- **v3.4.2**：`/scanner/disable-status` `disable-toggle`；reload 时序修复。
- **v3.4.3**：`/data/cycles/by-serial/{serial_no}` 工件追溯。
- **v3.5.0**：`/export/*` 整组（自定义导出 + 实时规则 + 字段树）；`source.py` 周期性强制动作；session/cycle 置信度聚合（`/data/stats/*`）。
- **v3.5.1（线上）**：CSV 导出加 `pt_mode/ct_mode`；操作手册补完。
- **v3.5.2（未发）**：扫码器列表为空时静默"⚠ 未绑码"；海康相机 NameError；检测框三层 clip。

---

## 8. 排错速查表

| 症状 | 先查 | 大概率原因 |
|---|---|---|
| 前端调用 404 | 浏览器 Network 面板 → 看完整 URL | 路径少 `/api/v1`、或路径写到了 axios baseURL 里又拼一次、或调用了已删端点（`/detection/*`） |
| 422 Unprocessable Entity | 后端 Pydantic Schema | camelCase 没转 snake_case，或 query/body 位置错（`channel` 应在 query 还是 body 看签名） |
| 路径里数字被当字符串报 `int_parsing` | 路由顺序 | 静态路径写在了 `/{id}` 后面 |
| 多通道乱串 | `channel` 参数 | 前端拿固定 `channel=0` 调，后端按 `ChannelManager` 分发；多工位下要循环每个 ch |
| MJPEG 渲染不出 | 用 `api.get` 取 stream | 用 `getBackendHost()` 拼裸地址，img.src 直接挂 |
| Electron 桌面壳里 API 全挂 | URL 协议 `file://` 检测 | `index.js::isDesktopApp()` 误判，看 `VITE_API_BASE_URL` 是否在打包时被注入 |
| 新加字段读不到 | ORM + Schema + 前端三处 | `models.py` 加列 + `migrate_database()` ALTER + Pydantic Response 加字段 + 前端解构同步 |

---

## 9. 与其他 skill 的边界

- 改后端 API 端点（增/改）：**先**读 `add-api-endpoint` 或 `modify-api`，再回这里做对齐。
- 改 ORM 字段：**先**读 `modify-model`，本 skill 只做"前后端字段名/类型"对齐部分。
- 改前端组件 / store：**先**读 `modify-frontend`。
- 项目配置 7 个 JSON 字段流转：`modify-project-config` 包含 source.py 状态机 ↔ Project 表的对齐细节，本 skill 不重复。

完成对齐检查后，**列出**：(1) 哪些路径前后端一致 ✓；(2) 哪些不一致 ✗；(3) 修复在前端还是后端，对应改哪个文件、哪一行；(4) 有无破坏向后兼容的风险。
