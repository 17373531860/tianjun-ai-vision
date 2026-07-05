---
name: modify-api
description: "安全修改后端API端点：路由前缀、Schema、业务逻辑、前端 axios 封装、视图调用点、字段对齐。修改任何后端API前先用这个skill做影响分析。"
argument-hint: "[要修改的API端点或模块名]"
model: opus
effort: high
allowed-tools: "Read, Grep, Glob, Bash, Agent, mcp__context7"
---

# modify-api: 后端 API 安全修改影响分析

你正在帮用户安全地修改后端 API 端点。计划修改：$ARGUMENTS

> 适用：改路径 / 改方法 / 增删字段 / 改 Schema / 改 Query/Body / 改业务逻辑。
> 不适用新增端点（用 `add-api-endpoint`）、改 ORM 模型（用 `modify-model`）、改 source.py 内部（用 `modify-source`）。

---

## 一、API 前缀真相（v3.5.x）

**所有业务端点全部挂在 `/api/v1/` 下**（不是 `/api/`）。前端 axios 实例在 `frontend/src/api/index.js` 里把 baseURL 设为 `${BACKEND_HOST}/api/v1`，所以前端 `api.get('/projects')` 实际打到 `/api/v1/projects`。**前端硬编码 `/api/...` 不带 v1 是 bug**。

挂载源头（OVERLAP-3 治理后**只有一处**）：
- 全部 30+ 组业务路由集中在 **`backend/api/router_manifest.py` 的 `mount_all_routers(app)`** 登记（`main.py` 只调它一次；`api/__init__.py` 刻意留空；挂载顺序即匹配顺序，`/data` 前缀有真实重叠勿乱序）
- 不在 `/api/v1/` 下的特殊端点（**改路径要单独处理**）：
  - `GET /` 欢迎页
  - `GET /health` 健康检查
  - `GET /video_feed?channel=N` MJPEG 流（前端硬编码，见下文）
  - `GET /snapshot?channel=N` 单帧快照（前端硬编码）
  - `GET /uploads/*` 与 `GET /recordings/*` StaticFiles（前端用绝对 URL 拼）

`/api/v1/source/shutdown/step/{step}` 与 `/api/v1/source/shutdown/complete` 直接挂在 `app` 上（不是 `source_router`），由 Electron 的 8 步关机流程调用。

---

## 二、路由前缀全表（→ 单点化，看 api-sync skill §1）

**完整 30 组「前缀 ↔ 后端文件 ↔ 前端 client」明细表已单点化到 `api-sync` skill 第 1 节（全仓唯一事实源），本 skill 不再维护副本。** 动手前先去那里对准目标模块。

本 skill 只保留改端点时最易踩的归属提醒：

- `/source/*` 端点实现在 `source_routes.py`（`source.py` 只是 router 容器）；前端封装在 `detection.js`（**没有 source.js**）
- `/data/*` 分散在 4 个 `sessions*.py` + `showcase_stats.py`，共用一个前缀
- `/export/*` 三个文件共用前缀（custom / realtime / scheduled）
- `/operators/*` v3.10.0 起全 410 Gone，改动需求一律去 `/users`
- v3.31 新增 `/weighing/*`（`weighing.py`，router 无自带 prefix、路径写在端点装饰器里）

> 已删（不要复活）：旧的 `/api/detection/*`（`detection_router`）已在 v2.7.x 下线，等价端点全在 `/api/v1/source/detection/*`。
> 已删：`backend/api/websocket.py` / `services/detector.py` 不存在；前端 `api/websocket.js` 也无人 import。

### 已知路由顺序冲突（FastAPI 按声明顺序匹配）

| 模块 | 风险路由 | 处理 |
|---|---|---|
| `projects.py` | `/{project_id}` vs `/active/current` | 把具体路径写在动态路径**之前**，否则 "active" 会被当 project_id |
| `cameras.py` | `/{camera_id}` vs `/default/stream` | 同上 |

---

## 三、强制分析流程（任何修改前都跑一遍）

### 第 1 步 · 锁定端点

读 `backend/main.py` 与 `backend/api/__init__.py`，确认：

1. 路由挂在哪个 router、prefix 是什么、最终全路径
2. HTTP 方法 / 路径参数 / Query / Body Schema
3. Schema 定义位置（`backend/schemas/` 或就地 `pydantic.BaseModel`）
4. 业务逻辑落在哪里：`backend/services/` / `backend/api/source*.py` mixin / `models/`

### 第 2 步 · 追踪前端调用链

按"路径段"在前端搜索，先找到 axios 封装，再找视图：

```bash
# 1) 先在 api/*.js 里搜端点路径片段（注意：前端不带 /api/v1 前缀）
rg "api\.(get|post|put|delete|patch)\(.*<path-fragment>" frontend/src/api

# 2) 再用 1) 找到的导出函数名搜视图 / store / layout
rg "<exportedFunctionName>" frontend/src/views frontend/src/layout frontend/src/store
```

特殊：`/alarm/*` 和 `/system/*` **没有** 单独的 `api/*.js`：
- `Alarm/index.vue` 直接 `import api from '@/api'` 后 `api.get('/alarm/xxx')`
- `/system/*` 当前只被后端 export 模板上下文 / Electron IPC / BDD 测试用，**没有前端封装**

### 第 3 步 · DB 影响

如果端点改了写入字段：

1. 找到 ORM 模型（`backend/models/models.py` / `mes_models.py` / `export_models.py`）
2. 字段类型对得上 SQLite？（参考 `modify-model` skill）
3. 是否要加 `migrate_database()` 里的 `ALTER TABLE`？老客户升级路径必须兼容
4. 序列化路径有几条（`to_dict()` / 列表接口 / 详情接口 / 导出 / MES Hook 上下文）

### 第 4 步 · 跨模块调用 / 启动期依赖

| 调用点 | 影响哪些端点 |
|---|---|
| `backend/main.py:get_video_manager()` | `/source/*` |
| `backend/main.py:auto_load_active_project()` / `auto_restore_video_sources()` | `/projects/*` / `/source/*` 启动期会读 |
| `backend/main.py:_init_mes_services()` | `/mes/*` / `/scanner/*` / `/cluster/*` / `/external-devices/*` |
| `electron/backend-manager.js` 8 步关机 | `/api/v1/source/shutdown/step/*` 与 `/complete`（**改路径必同改 Electron**） |
| `services/mes_hooks.py` `_handle_cycle_end` | `/mes/*` / `/export/*` 实时规则 |
| `services/cluster_collector.py` host/slave 心跳 | `/cluster/heartbeat` / `/cluster/slaves` |

### 第 5 步 · 生成影响报告（写在回复里给用户审）

```
修改的端点: [METHOD /api/v1/<path>]
后端文件:   [文件:行号]
Schema:    [类名 + 文件:行号 + 字段变化]
DB 影响:   [是 / 否；具体 ORM 模型 + ALTER TABLE]
前端 API:  [api/*.js 文件:函数名]
前端视图:  [所有 import 该函数的视图 / store / layout]
跨模块调用:[main.py / mes_hooks / cluster_collector / electron/backend-manager 等]
请求格式:  [字段增删改 + Optional 是否带默认值]
响应格式:  [字段增删改 + 老客户是否兼容]
建议测试:  [冒烟步骤 + 关键回归点]
```

---

## 四、改字段时的对齐清单

修改任意一个端点的字段（请求或响应），下列点必须**全部**核对：

1. **后端 Schema**（pydantic）字段名 = `snake_case`，新增字段必须 `Optional[...] = None` 或带默认值（避免破坏老客户端）
2. **业务逻辑**写入 / 读取该字段的位置：`api/<module>.py` + `services/*.py` + `source_*_mixin.py`
3. **ORM 字段**（如有 DB 持久化）+ `migrate_database()` 里的 `ALTER TABLE`
4. **前端 axios payload**：在 `api/*.js` 里查 axios 调用 → 翻到调用方视图 → 检查传给 axios 的对象字段名（`createOrder({ binding_scope: ... })` 这类）
5. **前端模板 / store**：渲染响应字段的 `<template>`、Pinia store 里的 ref / computed、v-model 绑定的字段名（前端必须用**和后端 Schema 一样的 snake_case**，全工程惯例如此，**不要在前端转 camelCase**）
6. **导出上下文**：如果字段会进自定义导出，看 `services/export_field_registry.py: ALL_FIELDS = 308`、`services/export_context.py`、内置模板 `services/export_seed.py`
7. **MES Gateway 模板**：`mes_gateway.py` 的 payload `{key.path}` 模板可能引用了该字段（**不是 Jinja2，是自研模板**）

---

## 五、改路径时的对齐清单

如果你要改路径（包括前缀、版本、子段、动态参数）：

1. **`backend/api/router_manifest.py` 里对应 `app.include_router(...)` 的 prefix**
2. **`backend/api/<module>.py`** 里的 `@router.<method>("/...")` 路径
3. **前端 `api/*.js`** 里所有 `api.<method>('/...')` / 模板字符串
4. **视图 / store** 里直接 `api.get('/xxx')`（绕过 `api/*.js`，主要在 `Alarm/index.vue`）
5. **MJPEG / 静态资源硬编码**：
   - `frontend/src/views/Monitor/index.vue` 用 `${getBackendHost()}/video_feed?channel=...`
   - `frontend/src/views/Project/index.vue` 用 `${host}/snapshot?channel=0`
   - `frontend/src/views/MES/ScannerPanel.vue` 用 `${host}/snapshot?channel=...`
   - `frontend/src/store/useSourceStore.js` 含 `streamUrl: '/video_feed'`
   - `frontend/src/api/data.js: getVideoUrl()` 拼 `${host}/api/v1/data/videos/${id}`
   - `frontend/src/api/camera.js: getDefaultStreamUrl()` 拼 `${host}/video_feed`
   - `frontend/src/api/export.js: getTemplateFileDownloadUrl()` 拼 `${host}/api/v1/export/templates/...`
6. **Electron 关机流程**：`electron/backend-manager.js` 直接拼 `/api/v1/source/shutdown/step/<name>` 与 `/api/v1/source/shutdown/complete`，改路径必须同步改
7. **License 缓存写回**：Electron 把 license payload POST 给 `/api/v1/system/license-cache`（写在 `electron/license-manager.js` 一类文件里）
8. **环境变量**：`VITE_API_BASE_URL` 默认 `http://localhost:8001/api/v1`，**不要把 v1 写进路径段**

> 当前没有 WebSocket 路由（`ws_router` / `websocket.py` 已废）。如果你要新增 WS，前端 `api/websocket.js` 是死代码可以重写。

---

## 六、常见坑（按发生频率排）

1. **加了端点忘了 `app.include_router(...)`**：新模块文件里 `router = APIRouter()` 写完了，但 `backend/api/router_manifest.py` 没登记——访问 404。所有主程序路由必须经 manifest 注册（唯一登记处）。
2. **Schema 缺字段 / 多字段 / `Optional` 漏写**：
   - 漏写 `Optional`：老客户端少传字段会 422
   - 多字段没 `Optional`：新前端不传旧字段也 422
   - 同名字段类型不一致（如后端 `Optional[int]`，前端传字符串）→ 字段被 silently 丢弃
3. **前端写死 `/api/...` 而不是 `/...`**：axios baseURL 已经含 `/api/v1`，再写 `/api/...` 会变成 `/api/v1/api/...`。所有 `api/*.js` 必须以 `/<resource>` 开头。
4. **URL 参数 vs Body / Query 弄混**：
   - `api.post('/x', data)` → data 是 **Body**
   - `api.post('/x', null, { params })` 或 `api.post('/x?a=1')` → 是 **Query**
   - `api.put('/x', data)` 中 data 是 Body，要 Query 必须用 `{ params }`
   - 后端用 `def f(a: int)`（无 `Body`/`Query`）：**类型简单时是 Query，类型是 Pydantic 时是 Body**
5. **路由顺序冲突**：`/{id}` 写在 `/active/current` 之前，"active" 被当 id → 404 或 422
6. **改了响应字段名但忘了改前端模板/store**：表面接口正常，但 `<el-table-column prop="xxx">` 不显示
7. **`/scanner/*` 与 `/scanner/wmax/*`**：两个 router 都挂 `/scanner` 前缀，子路径**不能撞**
8. **`/mes/*` 与 `/mes/gateway/*`**：同上，新增 `/mes/<x>` 前必须查 `mes_gateway.py` 是否已经占用
9. **`/export/*` 双 router**：`export_custom.py`（模板 / 渲染）+ `export_realtime.py`（实时规则）共用前缀，新增子路径要看两边
10. **死代码引用**：以下文件有路径但**没人用**——改时不必跟，留时不要再扩
    - `frontend/src/api/task.js`：全工程**无 import**，可整文件删
    - `frontend/src/api/camera.js`：除 `getDefaultStreamUrl()` 外**无视图 import**
    - `frontend/src/api/report.js`：`getRecords` / `getTrend` / `exportPdfReport` 局部死代码（`Report/index.vue` 路由未注册）
    - `frontend/src/api/websocket.js`：无人 import
    - `detection.js: resetDetection`（路径 `/detection/reset` 已无对应后端，老前端兼容残留）

---

## 七、修改原则（硬性）

1. **响应格式向后兼容**：可加字段，**不可删 / 改名**字段；类型扩展时（如 int → Union[int, str]）要前端能解析
2. **请求参数宽容**：新增 `Optional` 参数 OK，**改必填或重命名必填**会破坏老前端
3. **前后端必须同 PR 同步**：改了后端必须把所有 `api/*.js` + 视图 + store 一并改完
4. **路由顺序**：具体路径（`/active/current`、`/default/stream`、`/by-date/{date}`）放在动态参数（`/{id}`）**之前**
5. **DB session**：优先 `Depends(get_db)`，避免手动 `SessionLocal()` 漏 close
6. **错误响应**：返回 `HTTPException(status_code=4xx, detail="...")`，不要 bare `except`，不要 `return {"error": ...}`（前端拦截器按 status 分类处理）
7. **改路径必同改 Electron**：`/api/v1/source/shutdown/*` 和 MJPEG `/video_feed` / `/snapshot` 在 Electron 流程里**写死**

---

## 八、上线前自检清单

- [ ] `backend/api/router_manifest.py` 里 `include_router` 挂上了
- [ ] 前缀确实是 `/api/v1/<sub>`（不是 `/api/<sub>`）
- [ ] 路由声明顺序：具体 path 在动态 path 之前
- [ ] Schema：新增字段都是 `Optional[...]` + 默认值；删字段确认无前端引用
- [ ] DB：ORM 字段、`migrate_database()` 里 ALTER、序列化路径全部对齐
- [ ] 前端 axios：`api/*.js` 里路径正确（不要带 `/api/v1`）；payload 字段名 = 后端 snake_case
- [ ] 前端视图 / store：模板字段引用、Pinia state、v-model 都改过
- [ ] 跨模块：`mes_hooks` / `cluster_collector` / `export_context` / `electron/backend-manager` 是否需要联动
- [ ] 死代码：不要给 `task.js` / `camera.js` / `report.js` 死方法 / 已删 `detection_router` 续命
- [ ] 冒烟：启动后端 → `/docs` 看到端点 → curl 走通 → 前端页面手测一次 → `lint 0 错`
- [ ] **模式守门**：跟特定 logic_mode 绑定的端点（典型如 v3.12.0 `/per-item-control` 只在 `per_item` 模式生效），非该模式时**必须 400 拒绝**，参考 `tests/test_per_item_v310_features.py::test_per_item_control_rejects_when_not_in_per_item_mode`

> 完成修改后**主动汇报变更影响**给用户，不要 commit / push（等用户确认）。

---

## 九、v3.12.0 新增 API 速查

| 路径 | 方法 | 用途 | 守门 |
|---|---|---|---|
| `/api/v1/source/detection/per-item-control` | POST | per_item 模式手动控制（`{action: "settle"\|"force_start"}`）| 非 per_item 模式 → 400；未知 action → 400 |

**body schema**:

```json
{ "action": "settle" }
```

或

```json
{ "action": "force_start" }
```

**关键实现位置**：`backend/api/source_routes.py` 路由 + `backend/api/source_per_item_mixin.py: per_item_manual_settle / per_item_manual_force_start`

**新增同类端点时套用同样模式**：路由层做 `logic_mode` 守门 + action 枚举校验 + 调 mixin 方法 + 返回当前状态。
