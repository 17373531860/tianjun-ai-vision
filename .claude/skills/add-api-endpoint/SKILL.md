---
name: add-api-endpoint
description: "新增后端API端点的完整流程：路由注册、Schema定义、DB操作、router挂载、前端API封装、视图集成。当需要添加新的后端接口时使用。"
argument-hint: "[端点描述，如: GET /api/v1/xxx 用于xxx]"
model: opus
effort: high
allowed-tools: "Read, Grep, Glob, Bash, Agent, Edit, Write"
---

# add-api-endpoint: 新增后端API端点

你正在帮用户为天军AI视觉检测系统添加新的后端API端点。

需求: $ARGUMENTS

## 新增端点标准流程

### 第1步: 确定归属模块

| 功能领域 | 后端文件 | 路由前缀 | 前端API |
|----------|----------|----------|---------|
| 视频源控制 | `api/source.py` | `/api/v1/source` | `detection.js` |
| 检测控制 | `api/detection.py` | `/api/v1/detection` | `detection.js` |
| 会话数据 | `api/sessions.py` | `/api/v1` | `data.js` |
| 项目管理 | `api/projects.py` | `/api/v1/projects` | `project.js` |
| 模型管理 | `api/models.py` | `/api/v1/models` | `model.js` |
| 摄像头管理 | `api/cameras.py` | `/api/v1/cameras` | `camera.js` |
| 报表 | `api/reports.py` | `/api/v1/reports` | `report.js` |
| 报警 | `api/alarm.py` | `/api/v1/alarm` | Alarm/index.vue (直接axios) |
| 多工位 | `api/channel_manager.py` | `/api/v1/workstations` | `detection.js` |
| MES 管理 | `api/mes.py` (22端点) | `/api/v1/mes` | `mes.js` |
| 扫码器 | `api/scanner.py` (8端点) | `/api/v1/scanner` | `scanner.js` |

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
