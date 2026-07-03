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

**完整 30 组「前缀 ↔ 后端文件 ↔ 前端 client」明细表已单点化到 `api-sync` skill 第 1 节（全仓唯一事实源），先去那里选定归属模块，本 skill 不再维护副本。**

选归属时的判定要点：

- 与视频源/检测状态机相关 → `/source/*`（实现写 `source_routes.py`，前端封装 `detection.js`）
- 与 session/cycle/step 数据相关 → `/data/*`（4 个 `sessions*.py` 按职责选文件）
- 与外设读数/协议相关 → `/external-devices/*`；与称重投料业务相关 → `/weighing/*`（v3.31）
- 用户/权限/API Key → `/auth` `/users` `/roles` `/api-keys`（v3.10.0，前端统一 `auth.js`）
- 找不到合适前缀才新建路由组（在 `main.py` 或 `api/__init__.py` 挂载，并**同步更新 api-sync §1 + AGENTS.md 第五节**）

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

---

## v3.12.0 新增 API 参考样板

### per_item 手动控制 (`backend/api/source_routes.py` 内)

- 路径: `POST /api/v1/source/detection/per-item-control`
- 入参: `{ "action": "settle" | "force_start" }`
- 模式守门: 当前激活项目的 `logic_mode != 'per_item'` → 400; 未知 action → 400
- 业务实现: `source_per_item_mixin.py: per_item_manual_settle / per_item_manual_force_start`
- 测试样板: `tests/test_per_item_v310_features.py::test_per_item_control_rejects_when_not_in_per_item_mode`

**做"按模式生效"类端点时参考这套模板**:
1. 路由层先取 mgr / project_config
2. 校验 logic_mode 匹配 → 不匹配 400 + 明确 message
3. 校验 action 枚举（不要直接 dispatch 字符串到方法名）
4. 调对应 mixin 方法，返回当前状态片段（让前端拿到执行结果）
5. 写 3 个测试: 模式守门 / action 枚举 / 正路径
