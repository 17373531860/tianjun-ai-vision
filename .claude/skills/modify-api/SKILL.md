---
name: modify-api
description: "安全修改后端API端点：路由注册、Schema定义、前端调用点、影响的视图页面。修改任何后端API前先用这个skill分析影响。"
argument-hint: "[要修改的API端点或模块名]"
model: opus
effort: high
allowed-tools: "Read, Grep, Glob, Bash, Agent"
---

# modify-api: 后端API安全修改分析

你正在帮用户安全修改后端API端点。

计划修改: $ARGUMENTS

## API 路由注册全景

### 路由挂载点 (backend/main.py)

```python
# api_router 聚合路由 (backend/api/__init__.py)
app.include_router(api_router, prefix="/api/v1")
#   → /api/v1/projects    (projects.py)
#   → /api/v1/models      (models.py)
#   → /api/v1/cameras     (cameras.py)
#   → /api/v1/tasks       (tasks.py)
#   → /api/v1/reports     (reports.py)
#   → /api/v1/alarm       (alarm.py)

# 单独挂载的路由
app.include_router(source_router,      prefix="/api/v1/source")
app.include_router(sessions_router,    prefix="/api/v1")  # sessions.py 主入口（已 v2.7.x 拆 4 个文件，但 router 仍统一注册）
app.include_router(ws_router,          prefix="")
app.include_router(workstation_router, prefix="/api/v1")
# ❌ detection_router 已在 v2.7.x 全删（detection.py / services/detector.py 死代码清除）

# MES & Scanner (v2.3.0+)
app.include_router(mes_router,     prefix="/api/v1")  # → /api/v1/mes/*
app.include_router(scanner_router, prefix="/api/v1")  # → /api/v1/scanner/*
# MES Gateway（外部 MES 适配器 / 连接 CRUD / 推送与日志）
app.include_router(mes_gateway_router, prefix="/api/v1")  # → /api/v1/mes/gateway/*

# 操作员管理（Operator CRUD + 当前操作员）
app.include_router(operators_router, prefix="/api/v1")  # → /api/v1/operators/*

# 直接在 app 上的端点
# GET  /                         -- 欢迎页
# GET  /health                   -- 健康检查
# POST /api/v1/source/shutdown/* -- 关机步骤
# GET  /video_feed?channel=N     -- MJPEG流
# GET  /snapshot?channel=N       -- 快照
```

### 已知路由冲突

| 模块 | 冲突路由 | 问题 |
|------|----------|------|
| projects.py | `/{project_id}` vs `/active/current` | "active"可能被解析为project_id |
| cameras.py | `/{camera_id}` vs `/default/stream` | "default"可能被解析为camera_id |

## 前后端API对应表

| 后端模块 | 前端API文件 | 前端视图 |
|----------|-------------|----------|
| source.py + 11 个 source_*.py mixin/组件 (source_router) | detection.js | Monitor, Source |
| sessions.py / sessions_export.py / sessions_stats.py / sessions_maintenance.py (统一 sessions_router) | data.js | Data |
| projects.py | project.js | Project, Navbar |
| models.py | model.js | Model, Project, Monitor |
| cameras.py | camera.js | (基本未直接使用) |
| reports.py | report.js | Report |
| tasks.py | task.js | (废弃，无视图使用) |
| alarm.py | 直接axios调用 | Alarm |
| websocket.py | websocket.js | (未使用，Monitor用轮询) |
| channel_manager.py | detection.js | Source, Monitor |
| mes.py (22端点) | mes.js | MES (工单/工件/缺陷/缺陷代码) |
| **v3.1.0**: `OrderCreate`/`OrderUpdate` 加 `binding_scope` + `target_channels` + `target_stations` | mes.js (createOrder/updateOrder 直传) | MES/OrderPanel.vue 顶部 radio 三选一 |
| scanner.py (8端点) | scanner.js | MES/ScannerPanel |
| mes_gateway.py (`/mes/gateway/*`) | gateway.js | MES/GatewayPanel（外部对接：连接 CRUD、测试、手动推送、额外字段、通讯日志） |
| operators.py (`/operators`, `/operators/*`) | operators.js | Settings（操作员管理卡片）、Monitor（操作员选择器）、Navbar（当前作业员/设备编号展示）、Data（按操作员筛选与列展示） |

### Operators API 端点清单 (`backend/api/operators.py`)

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/operators` | 操作员列表 |
| POST | `/api/v1/operators` | 新增操作员 |
| PUT | `/api/v1/operators/{id}` | 编辑操作员 |
| DELETE | `/api/v1/operators/{id}` | 软删除 |
| POST | `/api/v1/operators/set-current` | 设置当前操作员 |
| GET | `/api/v1/operators/current` | 获取当前操作员 |

**关联修改：** `sessions.py` 的 Session/Cycle 列表与详情响应含 `operator_id` / `operator_name`，列表接口支持 `operator_id` 查询过滤；`source.py` 的 `start_session` / `start_cycle` 写入 `operator_id`，`get_detection_results` 返回操作员信息；`mes_gateway.py` 的 `build_context_from_cycle` 上下文含 `operator.*` 字段。修改 Operators 或 Session 相关 Schema 时需一并核对上述调用链。

## 强制分析流程

### 第1步: 确认修改的端点

1. 读取后端路由定义
2. 确认 HTTP 方法、路径、请求参数、响应格式
3. 检查是否有 Schema 定义 (backend/schemas/)

### 第2步: 追踪前端调用

用 Grep 搜索端点路径在前端的调用:
```
搜索范围: frontend/src/api/*.js
搜索关键词: 端点路径的最后一段（如 "/sessions", "/detection/start"）
```

然后追踪到视图层:
```
搜索范围: frontend/src/views/**/*.vue, frontend/src/layout/*.vue
搜索关键词: API 函数名（如 "getDetectionResults", "startDetection"）
```

### 第3步: 检查DB影响

如果端点修改了数据库操作:
1. 确认涉及的 ORM 模型 (backend/models/models.py)
2. 检查是否需要 migration (main.py:migrate_database)
3. 确认 SQLite 类型兼容性

### 第4步: 检查跨模块调用

某些API端点被其他后端模块调用:
- `source.py` 的 `get_video_manager()` 被 main.py 调用
- `channel_manager.py` 的 `channel_manager` 被 main.py 调用
- 关机端点（`/api/v1/source/shutdown/*`）被 electron/main.js 8 步关机流程调用

### 第5步: 生成影响报告

```
修改的端点: [METHOD /path]
后端文件: [文件:行号]
前端API: [文件:函数名]
前端视图: [使用该API的所有视图]
DB模型: [涉及的模型]
Schema: [涉及的Schema类]
跨模块调用: [其他后端模块的调用]
Electron调用: [是否被Electron直接调用]
请求格式变化: [是/否，具体字段]
响应格式变化: [是/否，具体字段]
建议测试: [具体测试步骤]
```

## 修改原则

1. **响应格式向后兼容:** 新增字段可以，删除/改名字段不行
2. **请求参数宽容:** 新增可选参数，不改必需参数
3. **前后端同步:** 改了后端必须检查前端是否需要同步修改
4. **路由顺序:** 具体路径（如 `/active/current`）放在参数路径（如 `/{id}`）之前
5. **DB session:** 优先使用 `Depends(get_db)` 而非手动 `SessionLocal()`
6. **错误响应:** 返回有意义的错误信息，不要 bare except 吞异常
