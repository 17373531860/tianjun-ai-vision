---
name: api-sync
description: "前后端API对齐检查：端点路径、请求/响应字段、已知不一致、URL规范。当怀疑前后端数据不对或新增API后需要同步时使用。"
argument-hint: "[具体的API对齐问题，或 'full-check' 做全量检查]"
model: opus
effort: high
allowed-tools: "Read, Grep, Glob, Bash, Agent"
---

# api-sync: 前后端API对齐检查

你正在帮用户检查和修复前后端API的一致性。

需求: $ARGUMENTS

## 已知的不一致问题

### 1. resetDetection URL 路径不一致
```
前端 detection.js: POST /detection/reset       ← 错误路径
其他检测API:       POST /source/detection/...   ← 正确路径前缀
```
**影响:** resetDetection 可能打到 detection.py 的端点而非 source.py 的端点。

### 2. 重复函数
```javascript
// detection.js 中:
getDetectionStatus(channel) → GET /source/detection/results
getSourceStatus(channel)    → GET /source/detection/results  // 完全相同
```

### 3. Vite 代理 vs 直连
```javascript
// api/index.js
getBaseURL()     → 'http://localhost:8001/api/v1'  // 所有API直连
getBackendHost() → 'http://localhost:8001' (桌面) 或 '' (浏览器, 走Vite代理)
```
MJPEG 流(`/video_feed`)走 getBackendHost()，API 走 getBaseURL()。

### 4. Electron 开发端口不匹配
```
electron/main.js:   loadURL('http://localhost:5173')  // 开发模式
vite.config.js:     server.port = 6001                // 实际端口
```

## 全量API对照表

### Source 模块 (/api/v1/source)
| 前端调用 (detection.js) | 后端端点 (source.py) | 状态 |
|------------------------|---------------------|------|
| `POST /source/detection/start` | source_router `/detection/start` | OK |
| `POST /source/detection/stop` | source_router `/detection/stop` | OK |
| `POST /source/detection/pause` | source_router `/detection/pause` | OK |
| `POST /source/detection/resume` | source_router `/detection/resume` | OK |
| `POST /source/detection/standby` | source_router `/detection/standby` | OK |
| `POST /source/detection/resume-inference` | source_router `/detection/resume-inference` | OK |
| `GET /source/detection/results` | source_router `/detection/results` | OK |
| `GET /source/status` | source_router `/status` | OK |
| `POST /source/detection/reset-stats` | source_router `/detection/reset-stats` | OK |
| **`POST /detection/reset`** | **detection_router?** | **路径不一致!** |
| `POST /source/camera/start` | source_router `/camera/start` | OK |
| `POST /source/rtsp/start` | source_router `/rtsp/start` | OK |
| `POST /source/video/upload` | source_router `/video/upload` | OK |
| `POST /source/video/start` | source_router `/video/start` | OK |
| `POST /source/image/upload` | source_router `/image/upload` | OK |
| `POST /source/image/start` | source_router `/image/start` | OK |
| `POST /source/hikvision/start` | source_router `/hikvision/start` | OK |
| `POST /source/hcnetsdk/start` | source_router `/hcnetsdk/start` | OK |

### Workstation 模块 (/api/v1/workstations)
| 前端调用 (detection.js) | 后端端点 (channel_manager.py) | 状态 |
|------------------------|-------------------------------|------|
| `GET /workstations/` | workstation_router `/workstations/` | OK |
| `POST /workstations/mode` | workstation_router `/workstations/mode` | OK |
| `POST /workstations/{id}/gpu` | workstation_router `/workstations/{id}/gpu` | OK |
| `GET /workstations/gpu-allocation` | workstation_router `/workstations/gpu-allocation` | OK |

### Session 模块 (/api/v1)
| 前端调用 (data.js) | 后端端点 (sessions.py) | 状态 |
|-------------------|----------------------|------|
| `GET /sessions` | sessions_router `/sessions` | OK |
| `GET /sessions/dates` | sessions_router `/sessions/dates` | OK |
| `GET /sessions/by-date/{date}` | sessions_router `/sessions/by-date/{date}` | OK |
| `GET /sessions/{id}` | sessions_router `/sessions/{id}` | OK |
| `GET /sessions/{id}/cycles` | sessions_router `/sessions/{id}/cycles` | OK |
| `GET /cycles/{id}` | sessions_router `/cycles/{id}` | OK |
| `GET /cycles/{id}/steps` | sessions_router `/cycles/{id}/steps` | OK |
| `GET /videos/{id}` | sessions_router `/videos/{id}` | OK |
| `GET /videos` | sessions_router `/videos` | OK |
| `GET /export-settings` | sessions_router `/export-settings` | OK |
| `PUT /export-settings` | sessions_router `/export-settings` | OK |
| `GET /export/csv` | sessions_router `/export/csv` | OK |
| `GET /stats/step-averages` | sessions_router `/stats/step-averages` | OK |
| `GET /stats/cycle-averages` | sessions_router `/stats/cycle-averages` | OK |
| `GET /backup/database` | sessions_router `/backup/database` | OK |
| `DELETE /clear/all` | sessions_router `/clear/all` | OK |
| `DELETE /clear/range` | sessions_router `/clear/range` | OK |
| `GET /cleanup-settings` | sessions_router `/cleanup-settings` | OK |
| `PUT /cleanup-settings` | sessions_router `/cleanup-settings` | OK |
| `GET /storage-info` | sessions_router `/storage-info` | OK |
| `POST /cleanup/run` | sessions_router `/cleanup/run` | OK |

### Project 模块 (/api/v1/projects)
| 前端调用 (project.js) | 后端端点 (projects.py) | 状态 |
|----------------------|----------------------|------|
| `GET /projects` | projects_router `/` | OK |
| `GET /projects/{id}` | projects_router `/{id}` | OK |
| `POST /projects` | projects_router `/` | OK |
| `PUT /projects/{id}` | projects_router `/{id}` | OK |
| `DELETE /projects/{id}` | projects_router `/{id}` | OK |
| `POST /projects/{id}/activate` | projects_router `/{id}/activate` | OK |
| `GET /projects/active/current` | projects_router `/active/current` | ⚠️ 路由顺序 |

### Model 模块 (/api/v1/models)
| 前端调用 (model.js) | 后端端点 (models.py) | 状态 |
|--------------------|---------------------|------|
| `GET /models` | models_router `/` | OK |
| `GET /models/{id}` | models_router `/{id}` | OK |
| `POST /models/upload` | models_router `/upload` | OK |
| `PUT /models/{id}` | models_router `/{id}` | OK |
| `DELETE /models/{id}` | models_router `/{id}` | OK |
| `POST /models/{id}/set-active` | models_router `/{id}/set-active` | OK |
| `POST /models/{id}/parse-labels` | models_router `/{id}/parse-labels` | OK |
| `GET /models/formats/available` | models_router `/formats/available` | OK |
| `POST /models/{id}/convert` | models_router `/{id}/convert` | OK |
| `GET /models/conversions/{id}/status` | models_router `/conversions/{id}/status` | OK |
| `GET /models/{id}/conversions` | models_router `/{id}/conversions` | OK |
| `DELETE /models/conversions/{id}` | models_router `/conversions/{id}` | OK |
| `POST /models/{id}/resolve-path` | models_router `/{id}/resolve-path` | OK |

### Alarm 模块 (/api/v1/alarm)
前端 Alarm/index.vue 直接用 axios 调用，不经过封装的API文件。

### Report 模块 (/api/v1/reports)
| 前端调用 (report.js) | 后端端点 (reports.py) | 状态 |
|---------------------|---------------------|------|
| `GET /reports/summary` | reports_router `/summary` | OK |
| `GET /reports/records` | reports_router `/records` | OK |
| `GET /reports/trend` | reports_router `/trend` | OK |
| `GET /reports/daily-stats` | reports_router `/daily-stats` | OK |
| `GET /reports/export` | reports_router `/export` | OK |

## 对齐检查流程

### 快速检查（针对特定API）
1. Grep 前端调用点（api/*.js 和 views/**/*.vue）
2. Grep 后端端点定义（api/*.py）
3. 对比路径、方法、参数名、响应字段

### 全量检查（`$ARGUMENTS` = 'full-check'）
1. 读取所有前端API文件，列出所有调用
2. 读取所有后端路由定义
3. 逐条对比
4. 检查请求参数和响应字段的一致性

## 修复原则
1. **前端适配后端:** 如果后端已稳定，前端改路径
2. **后端不轻易改路由:** Electron 的关机流程也直接调用后端API
3. **路由顺序:** 具体路径放参数路径前面
4. **向后兼容:** 新增字段可以，不删旧字段
