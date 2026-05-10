---
name: add-api-endpoint
description: "新增后端API端点的完整流程：路由注册、Schema定义、DB操作、router挂载、前端API封装、视图集成。当需要添加新的后端接口时使用。"
argument-hint: "[端点描述，如: GET /api/v1/xxx 用于xxx]"
model: opus
effort: high
allowed-tools: "Read, Grep, Glob, Bash, Agent, Edit, Write, mcp__context7"
---

# add-api-endpoint: 新增后端API端点

你正在帮用户为天军AI视觉检测系统添加新的后端API端点。

需求: $ARGUMENTS

## 新增端点标准流程

### 第1步: 确定归属模块

| 功能领域 | 后端文件（行数 / 端点数） | 路由前缀 | 前端 API |
|----------|----------|----------|---------|
| 视频源 + 检测控制 | `api/source_routes.py` (1279) + 14 个 `source_*_mixin.py` | `/api/v1/source` | `scanner.js` / `detection.js` |
| 会话列表/详情 | `api/sessions.py` | `/api/v1/data` | `data.js` |
| 会话导出 (CSV) | `api/sessions_export.py` | `/api/v1/data` | `data.js` |
| 会话统计 | `api/sessions_stats.py` | `/api/v1/data` | `data.js` |
| 数据维护 (清理/备份) | `api/sessions_maintenance.py` | `/api/v1/data` | `data.js` |
| 项目管理 | `api/projects.py` (334 / 7) | `/api/v1/projects` | `project.js` |
| 模型管理 | `api/models.py` (792 / 14) | `/api/v1/models` | `model.js` |
| 离线推理任务 | `api/tasks.py` (192 / 7) | `/api/v1/tasks` | `task.js` (dead) |
| 报表（已部分弃用） | `api/reports.py` (344 / 5) | `/api/v1/reports` | `report.js` (dead) |
| 旧式相机表 | `api/cameras.py` (156 / 8) | `/api/v1/cameras` | `camera.js` (dead, 与 source 并存别误用) |
| 报警 | `api/alarm.py` (1008 / 12) | `/api/v1/alarm` | Alarm/index.vue 直接 axios |
| 多工位 / 通道 | `api/channel_manager.py` (373 / 6) | `/api/v1/workstations` | `scanner.js` |
| MES 管理 | `api/mes.py` (673 / 26) | `/api/v1/mes` | `mes.js` |
| 扫码器 | `api/scanner.py` (532 / 17) | `/api/v1/scanner` | `scanner.js` |
| WMax 扫码器协议 | `api/wmax.py` (672 / 36 ⚠️ 最大) | `/api/v1/scanner/wmax` | `wmax.js` |
| 外部设备 | `api/external_device.py` (365 / 10) | `/api/v1/external-devices` | `external_device.js` |
| 集群（主从汇总） | `api/cluster.py` (295 / 10) | `/api/v1/cluster` | `cluster.js` |
| MES 网关 | `api/mes_gateway.py` (450) | `/api/v1/mes/gateway` | `gateway.js` |
| 操作员 | `api/operators.py` (213 / 6) | `/api/v1/operators` | `operators.js` |
| 系统显示设置 + License 缓存 | `api/system_display.py` (135 / 4) | `/api/v1/system` | `useSystemStore.js` |
| 自定义导出（v3.5.0） | `api/export_custom.py` + `api/export_realtime.py` | `/api/v1/export` | `data.js` |
| 通道诊断 | `api/debug.py` (118 / 2) | `/api/v1/debug` | 调试用 |

**❌ 已删除归属（不要往这些文件加）：**
- `api/detection.py` — 死路由，v2.7.x 删除（前端不再调用，等价端点已迁到 `/api/v1/source/`）
- `services/detector.py` — 死实现类，v2.7.x 删除
- `api/websocket.py` — 不存在（CI build.yml 列了但实际没有，是已知 CI bug）

**⚠️ Dead 前端 API 客户端（不要参考）：**
- `frontend/src/api/task.js` / `camera.js` / `report.js` — 前端已没视图调用，仅文件保留

### 第2步: 定义 Schema（如需要）

位置: `backend/schemas/` 对应模块

```python
# backend/schemas/xxx.py
from pydantic import BaseModel
from typing import Optional, List

class XxxRequest(BaseModel):
    field1: str
    field2: Optional[int] = None

class XxxResponse(BaseModel):
    id: int
    field1: str
    
    class Config:
        from_attributes = True  # 原 orm_mode = True
```

### 第3步: 实现端点

```python
# 在对应的 api/xxx.py 中
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from backend.db.database import get_db

router = APIRouter()  # 如果文件已有router则不用新建

@router.get("/your-path")
def your_endpoint(
    param1: str,
    param2: int = 10,
    db: Session = Depends(get_db)  # 使用依赖注入！
):
    # 实现逻辑
    return {"result": "..."}
```

**注意:** 本项目很多地方用 `SessionLocal()` 手动创建DB session，但**新代码应该用 `Depends(get_db)`**。

### 第4步: 注册路由

检查路由是否已自动注册:
- 如果端点加在**已有router**的文件中 → 自动生效
- 如果创建**新文件** → 需要在 `api/__init__.py` 或 `main.py` 中注册

```python
# api/__init__.py (用于标准CRUD模块)
from backend.api.new_module import router as new_router
api_router.include_router(new_router, prefix="/new-module", tags=["new-module"])

# 或 main.py (用于特殊路由前缀)
from backend.api.new_module import router as new_router
app.include_router(new_router, prefix="/api/v1/new-prefix")
```

### 第5步: 前端API封装

```javascript
// frontend/src/api/xxx.js
import api from './index'

export function yourFunction(params) {
    return api.get('/your-path', { params })
}

export function yourPostFunction(data) {
    return api.post('/your-path', data)
}
```

### 第6步: 视图集成

在需要的Vue组件中引入并调用:
```javascript
import { yourFunction } from '@/api/xxx'

const result = await yourFunction(params)
```

## 路由命名规范

```
GET    /resources          -- 列表
GET    /resources/{id}     -- 详情
POST   /resources          -- 创建
PUT    /resources/{id}     -- 更新
DELETE /resources/{id}     -- 删除
POST   /resources/{id}/action  -- 特殊操作
```

**路由顺序注意:**
- 具体路径（如 `/active/current`）必须放在参数路径（如 `/{id}`）之前
- 否则 FastAPI 会把 "active" 当作 id 解析

## 检查清单

- [ ] Schema 定义（如需要）
- [ ] 端点实现（用 Depends(get_db)）
- [ ] 路由注册（确认前缀正确）
- [ ] 前端 API 函数
- [ ] 视图集成
- [ ] 错误处理（HTTPException，不要 bare except）
- [ ] 确认不与现有路由冲突

## v3.5.0 新增的 API 子系统

### 自定义导出 / 实时规则 (`backend/api/export_custom.py` + `export_realtime.py`)

- 路由前缀: `/api/v1/export/`
- 模板 CRUD: `templates`, `templates/{id}`, `templates/{id}/upload-template-file` (路线 B 占位符上传)
- 渲染: `POST /preview` (Jinja2 字符串预览), `POST /render` (二进制返回 txt/csv/docx/xlsx/pdf)
- 实时规则 CRUD: `realtime-rules`, `realtime-rules/{id}/test-run`, `realtime-rules/{id}/run-logs`
- **路由位置 / 注册**: 在 `backend/main.py` 显式 `include_router`. 新增同类 API 时**继承同样的前缀和 tag**
- **依赖**:
  - 渲染层: `services/export_renderer.py` 主入口 → `_docx.py / _xlsx.py / _pdf.py` 三种二进制扩展
  - 上下文构造: `services/export_context.py` (cycle/range/system 三种), 修改字段时同步 `export_field_registry.py` (308 字段中央仓库)
  - ORM: `models/export_models.py` (ExportTemplate, ExportRealtimeRule, ExportRunLog)
- **测试**: `tests/step_defs/test_custom_export.py` + `test_realtime_rules.py` + `tests/test_pairwise_export.py`

### System 显示字段 (`backend/api/system_display.py`)

- 路由前缀: `/api/v1/system/`
- `GET/PUT /display` — brand_name/app_name/inspector_name/device_number/factory_name/line_name 等 KV
- `POST /license-cache` — 前端 Electron IPC 推 license 信息进来, 后续模板用 `{{ license.* }}`
- ORM: `SystemConfig` (KV 表), 在 `backend/main.py::migrate_database()` 加表
