---
name: debug-export
description: "诊断 v3.5.0+ 自定义导出与实时规则：模板 CRUD、Jinja2 渲染、5 种输出格式（txt/csv/docx/xlsx/pdf）、3 种触发器（cycle_end/session_end/box_complete）、字段中央仓库（308 字段）。当客户使用自定义导出导出失败、模板渲染异常、实时规则不触发、字段缺失时使用。"
argument-hint: "[问题现象]"
model: opus
effort: high
allowed-tools: "Read, Grep, Glob, Bash, mcp__filesystem, mcp__sequential-thinking"
---

# debug-export: 自定义导出与实时规则诊断

> 自定义导出是 v3.5.0 新增的子系统，独立于"CSV 导出（quick export）"。本 skill 专注于自定义导出。
> 如果是 `/api/v1/data/export/csv` 那条线，请用 `debug-session` skill。

问题现象: $ARGUMENTS

---

## 一、子系统全貌

| 角色 | 文件 | 行数 | 端点数 | 职责 |
|---|---|---:|---:|---|
| API（CRUD + 渲染） | `backend/api/export_custom.py` | 526 | 12 | 模板 CRUD / preview / render / 字段树 |
| API（实时规则） | `backend/api/export_realtime.py` | 305 | 9 | 实时规则 CRUD / test-run / run-logs |
| 渲染主入口 | `backend/services/export_renderer.py` | 438 | — | 选择器：txt/csv → 直接拼字符串；docx/xlsx/pdf → 委派子模块 |
| docx 渲染 | `backend/services/export_renderer_docx.py` | 163 | — | python-docx + docxtpl Jinja2 占位符 |
| xlsx 渲染 | `backend/services/export_renderer_xlsx.py` | 159 | — | openpyxl + Jinja2 |
| pdf 渲染 | `backend/services/export_renderer_pdf.py` | 176 | — | reportlab |
| 上下文构造 | `backend/services/export_context.py` | 1131 | — | `build_cycle_context` / `build_range_context` / `build_system_context` |
| 字段中央仓库 | `backend/services/export_field_registry.py` | 815 | — | **308 字段元数据 + 分类树**（前端拖拽用） |
| 内置模板种子 | `backend/services/export_seed.py` | — | — | 启动时若 `export_templates` 表为空，写入内置模板（如 `builtin_cycle_simple_txt`） |
| ORM | `backend/models/export_models.py` | 190 | — | `ExportTemplate` / `ExportRealtimeRule` / `ExportRunLog` 三张表 |
| 实时规则后台分发 | `backend/services/export_realtime.py` | — | — | hook 监听 cycle_end → render_to_file → ExportRunLog 写库 |
| 集成入口 | `backend/services/mes_hooks.py` 中 `dispatch_cycle_end_export` | — | — | cycle_end 时调用 |

> **路由前缀全部 `/api/v1/export/`**（在 `backend/main.py` 显式 include_router，custom + realtime 共用前缀）

---

## 二、关键概念

### 2.1 三种上下文（context）类型

| 类型 | 构造函数 | 描述 | 用途 |
|---|---|---|---|
| **cycle** | `build_cycle_context(cycle, db)` | 单个工件周期的全部数据 | cycle_end 触发 / 客户拿单工件出文件 |
| **range** | `build_range_context(start, end, ...)` | 一段时间范围的聚合 | 日报 / 周报 / 月报 |
| **system** | `build_system_context()` | 系统层级数据（设备号、工厂名、license、版本等） | 文件头部 / 共用 |

每种 context 都是一个嵌套 dict，模板里用 Jinja2 占位符 `{{ cycle.duration }}` / `{{ system.device_number }}` 等访问。

### 2.2 308 字段中央仓库

- 文件：`backend/services/export_field_registry.py`（815 行）
- 结构：每个字段都是 dict，含 `key`（点路径如 `cycle.steps[0].confidence_max`）/ `label`（中文展示名）/ `category`（分类）/ `example`
- 前端 Project / Data 页拖拽用：调 `GET /api/v1/export/fields` 拿到分类树
- **改字段时同步**：源数据来自 `export_context.py` 的 build_*；新增 / 修改字段必须**同时改两个文件**，否则前端能拖但渲染报 KeyError

### 2.3 五种输出格式

| 格式 | 二进制？ | Jinja2 占位符 | 备注 |
|---|---|---|---|
| `txt` | 否 | 是 | 一般用于客户 SN.txt 这类要求 |
| `csv` | 否 | 是 | 简单表格 |
| `docx` | 是 | docxtpl `{{ }}` 内嵌 | 需要 `docxtpl` Python 包 |
| `xlsx` | 是 | 是（openpyxl + 占位符替换） | 需要 `openpyxl` |
| `pdf` | 是 | 否（用 reportlab 元素） | 不支持任意 Jinja2，要在模板配置层指定字段列 |

### 2.4 三种触发器（实时规则）

| trigger_event | 触发时机 | 状态 |
|---|---|---|
| `cycle_end` | 一个工件 cycle 结束 | ✅ **唯一实际接入**，在 `mes_hooks.dispatch_cycle_end_export` 调用 |
| `session_end` | 一次开机 session 结束 | ⚠️ 标 `[todo]`（`backend/services/export_realtime.py` 第 13 行附近） |
| `box_complete` | 集群所有工位 box_serial 聚齐 | ⚠️ 标 `[todo]` |

> **常见误判**：客户配了 `session_end` 实时规则但不触发——这是已知 todo，**不是 bug**。

---

## 三、常见问题排查

### 3.1 模板渲染失败 / KeyError

**现象**：客户在 Data 页点"渲染预览"或运行实时规则失败，后端日志报 `jinja2.exceptions.UndefinedError` 或 `KeyError`。

**排查**：

1. 看用的字段 key 是否在仓库里：
   ```bash
   grep "<字段key>" backend/services/export_field_registry.py
   ```
2. 看 context 里有没有真实构造：
   ```bash
   grep "<字段key>" backend/services/export_context.py
   ```
3. **典型分裂**：仓库写了 `cycle.steps[0].confidence_max`，但 context 实际 key 是 `cycle.steps[0].max_confidence`——前端能选，但渲染报错。
   修复：以 context 为准，回写 registry。

### 3.2 SN.txt 路线 B（基于输入文件覆盖）异常

**v3.5.0 新增的"基于输入文件覆盖"流程**：
- 客户提供 `<SN>.txt` 输入文件夹（路径在模板配置里），后端读 → 渲染 → 写到输出文件夹
- 文件名也是 Jinja2 模板，如 `{{ cycle.workpiece_serial }}.txt`

**常见坑**：
- 输入路径 / 输出路径用了反斜杠（Windows 客户机）→ 后端 `os.path.normpath` 应能处理，但模板里硬编码 `\` 会被 Jinja2 当转义符吃掉
- 输入文件不存在 → `render_to_file(input_dir=..., output_dir=...)` 报 FileNotFoundError；规则会写一条失败日志到 `ExportRunLog` 表
- 文件名模板里的字段没渲染出来 → 检查模板里用的是 `{{ cycle.* }}` 还是 `{{ cycle["*"] }}`（前者更安全）

### 3.3 实时规则不触发

**前置检查**：
1. 规则启用了吗？`SELECT id, name, enabled, trigger_event FROM export_realtime_rules`
2. trigger_event 是 `cycle_end` 吗？session_end / box_complete 还是 todo
3. 通道过滤匹配吗？`channel_filter` 字段（JSON）应包含当前 channel_id
4. 有没有 `ExportRunLog` 记录？
   ```sql
   SELECT * FROM export_run_logs WHERE rule_id = ? ORDER BY id DESC LIMIT 5;
   ```
5. 后端日志里 grep `dispatch_cycle_end_export` 看是否被调用

**如果完全没调用**：
- 看 `backend/api/source_event_trigger_mixin.py` 的 cycle_end hook 是否真的触发
- 看 `mes_hooks.MESHookManager._handle_cycle_end` 是否调到 `dispatch_cycle_end_export`

### 3.4 docx/xlsx 渲染缺包

**现象**：渲染 docx/xlsx 时报 `ModuleNotFoundError: No module named 'docxtpl'` 或 `openpyxl`。

**修复**：`pip install docxtpl openpyxl reportlab`，conda 环境必须装在**后端正在用的环境**里（不是开发机的全局 Python）。

### 3.5 PDF 中文乱码 / 字体缺失

**现象**：PDF 输出中文显示为方框 / `□`。

**原因**：`reportlab` 默认字体不含中文。`export_renderer_pdf.py` 应该已经注册了项目内带的字体（如 fonts/SimSun.ttf）。

**排查**：
1. 看 `export_renderer_pdf.py` 中是否 `pdfmetrics.registerFont(...)`
2. 字体文件是否存在
3. 模板里有没有用对字体名

### 3.6 字段中央仓库与 context 不一致

**现象**：前端拖拽字段进模板没问题，但渲染时该字段没出现 / 输出 None。

**排查**：见 3.1。**强约束**：改 context 必同步改 registry，反之亦然。

---

## 四、模板生命周期

```
启动:
  export_seed.py 注入内置模板（如 builtin_cycle_simple_txt）若 export_templates 表为空

客户操作:
  Data 页 / Project 页拖拽字段 → POST /api/v1/export/templates → ExportTemplate 入库
  POST /api/v1/export/preview     → 字符串预览
  POST /api/v1/export/render      → 二进制下载（txt/csv/docx/xlsx/pdf）

实时规则:
  Project 页配置触发器 → POST /api/v1/export/realtime-rules → ExportRealtimeRule 入库
  cycle_end → dispatch_cycle_end_export → 后台 worker 渲染 → ExportRunLog
```

---

## 五、调试入口（按问题类型）

| 现象 | 第一步看 | 第二步看 |
|---|---|---|
| 模板渲染 500 | 后端日志 traceback | grep 字段 key 在 context 是否存在 |
| 渲染但内容空 | export_run_logs.error_message | 实际 cycle 数据是否完整 |
| 实时规则不发 | export_run_logs 是否有记录 | dispatch_cycle_end_export 调用链 |
| 文件输出但格式错 | render_to_file 调用参数 | template_format 字段 |
| docx/xlsx 报 ModuleNotFoundError | conda 环境包 | 后端 systemd / electron spawn 用的 python |
| PDF 中文乱码 | reportlab 字体注册 | 字体文件是否打包进发布 |

---

## 六、关键文件速查

```
backend/api/export_custom.py        模板 CRUD + 渲染 (526)
backend/api/export_realtime.py       规则 CRUD (305)
backend/services/export_renderer.py  主渲染入口 (438)
backend/services/export_renderer_docx.py  (163)
backend/services/export_renderer_xlsx.py  (159)
backend/services/export_renderer_pdf.py   (176)
backend/services/export_context.py   上下文构造 (1131)
backend/services/export_field_registry.py 308 字段 (815)
backend/services/export_seed.py     内置模板种子
backend/services/export_realtime.py 实时规则 hook
backend/models/export_models.py     3 张表 (190)
frontend/src/views/Data/index.vue   导出 UI 入口
frontend/src/api/data.js            前端 API 客户端
```

---

## 七、相关 skill

- `add-api-endpoint` — 加新的 export 端点
- `modify-model` — 改 export_models.py 三张表
- `debug-mes` — cycle_end hook 来自 mes_hooks，那边的链路问题来这里之前先看那个
- `debug-session` — quick export（CSV）那条线在那里

---

## 八、已知坑

1. **session_end / box_complete 触发器是 todo**：客户配了不会触发，文档里要写明
2. **字段仓库与 context 容易分裂**：改字段时必须双改
3. **docx/xlsx 二进制返回**：FastAPI 返回 `Response(content=bytes, media_type=...)`，前端必须 `responseType: 'blob'`
4. **renderer 主入口 `render_to_file` 参数**：用 `output_dir` + `input_dir`，不是 `output_path` + `input_path`（旧测试用例错过这个）
5. **实时规则的 `channel_filter` JSON 要严格匹配**：`[0]` 和 `["0"]` 不一样，前端表单要保证类型
