# 05 — 技术债 / 痛点完整索引

> 适用版本：v3.31.0（2026-07 销账刷新：BUG-1 已修、BUG-3 降级孤儿文件、第九节行数实测重排）
> 本文目的：把 **AGENTS.md 第九节 + 01~04 文档发现的所有 ⚠️ ❌ + 已知历史踩坑 + 测试盲区**集中索引到一处。这是插件系统设计前必须正视的"现实地图"——很多地方现状是可工作的但脆弱，碰多了主程序会塌。
>
> 阅读顺序：第二节"按严重度分级总表" → 按需要查具体类别 → 第十二节"先做哪些再做插件"。
>
> 配套：AGENTS.md 第九节（已知架构 bug 索引） + 各 debug-* skill（具体场景的踩坑详情）。

---

## 一、统计速查

| 类别 | 项数 | 严重度分布 |
|---|---|---|
| 真 bug（应被修） | **0**（原 6：BUG-1/BUG-2 已修，BUG-3/BUG-4 随孤儿 mixin 清退关闭，BUG-5/6 随产品交接手册删除销账） | ✅ 全部销账 |
| 死代码 / 半死代码 | **10**（BUG-3 降级并入；其中 DEAD-1~4 前端四文件 2026-07 已删） | 🟢 全可清 |
| 重叠 mixin / 同名方法冲突 | **2**（原 3，BUG-3 移出） | 🟠 |
| 现状无 registry（先重构再做插件） | **8** | 🟠 |
| API 路径不一致 / 命名歧义 | **5** | 🟡 |
| 文档过时不同步 | **5** | 🟡 |
| 文件过大需重构（> 1000 行 .py / .vue） | **15**（2026-07 实测重排） | 🟡 |
| SQLite → PG 迁移痛点 | **7** | 🟠 |
| 测试覆盖盲区 | **5** | 🟠 |
| 历史 bug 高发模块 | **6** | 信息性 |
| 隐式约定 / 缺乏护栏 | **8** | 🟡 |
| **合计** | **76**（含已销账项按现分类计） | — |

🔴 严重（影响主程序稳定）/ 🟠 中（影响插件系统设计）/ 🟡 轻（妨碍维护）/ 🟢 可清理

---

## 二、按严重度分级总表

### 🔴 严重（必须先修）

| ID | 名称 | 影响 | 工时估 |
|---|---|---|---|
| ~~BUG-1~~ | ✅ **已修销账（2026-07）**：`core/config.py:_fix_db_paths` 已改用真实表名 `models`（+`video_clips`），详见第三节 BUG-1 | — | — |

### 🟠 中（影响插件系统设计）

| ID | 名称 | 影响 | 工时估 |
|---|---|---|---|
| BUG-2 | ✅ **已修（2026-07）**：CORE_FILES 白名单重审——剔除 3 个失效项、扩到 17 项（source_routes + Top5 mixin + scanner/mes_hooks/weighing_engine）、缺失文件改为硬 fail | — | — |
| BUG-3 | ✅ **已清退（2026-07）**：`source_industrial_camera_mixin.py` 孤儿文件已删除（全仓无 import，无 MRO 冲突，见 DEAD-10） | — | — |
| BUG-4 | ✅ **已清退（2026-07）**：`source_recording_mixin.py` (546 行) 孤儿文件已删除（见 DEAD-8），录像能力唯一归属拆分后两个 mixin | — | — |
| OVERLAP-1 | ~~主类继承链不含 `IndustrialCameraMixin`，但被 import~~ 已随 BUG-3 复核关闭（import 已不存在） | — | — |
| OVERLAP-2 | `_inspecting` 字典在 scan_pair race（v3.4.2 hotfix 后）可能仍有 cornercase | 工件结果错写到下一码 | 1 天（监控+测试） |
| OVERLAP-3 | API 路由两条挂载路径并存（`api_router` 聚合 9 + main.py 直挂 10） | 插件加路由时不知道走哪条 | 0.5 天（统一） |
| MIG-1 | SQLite → PG: 7 处 raw SQL 用 `PRAGMA / sqlite_master` | PG 不支持 → 必须改条件分支 | 1 天 |
| MIG-2 | `BOOLEAN DEFAULT 1` 在 PG 要改成 `DEFAULT TRUE` | 60+ ALTER TABLE 都要改 | 1 天 |
| MIG-3 | Inno Setup 内嵌 PG 静默安装 | 主包变大 + 安装时间增加 | 3 天 |
| REG-* | 8 处现状无 registry（B2/B3/B4/B6/C1/C2/C5/C7/C8） | 插件做扩展前必须先重构 | 见第六节 |
| TEST-1 | `BACKEND_SKIP_INIT=1` 测试 fixture 缺少独立 DB 隔离 | 测试可能污染开发库 | 1 天 |
| TEST-2 | 没有插件系统的回归测试套 | 插件 1.0 上线前必须搭 | 2 天 |

### 🟡 轻（影响维护，不影响功能）

| ID | 名称 | 影响 |
|---|---|---|
| ~~BUG-5~~ | ✅ 销账（2026-07）：错写类名的产品交接手册已于 2026-06-26 删除（随 DOC-4），正确口径（类名 `Model` / 表名 `models`）已进 AGENTS.md 第五节 ORM 提醒 | — |
| ~~BUG-6~~ | ✅ 销账（2026-07）：同上，错误表数出处已删除；现行表清单以 `modify-model` skill 为准（47 张） | — |
| INCONSIST-* | API 路径 5 处不一致 | 插件 prefix 选择需小心 |
| ~~DOC-*~~ | ✅ 文档过时 5 处全部处理完（2026-07-05：DOC-1/2/3 注释已修，DOC-5/6 此前已校正） | — |
| SIZE-* | 9 个文件 > 1000 行 | 修改成本高 |
| ~~HIDDEN-*~~ | ✅ 8 处隐式约定 2026-07-05 全部文档化进 AGENTS.md 第八节（1/2/3/5 补充既有条目，4/6/7/8 新增第 14~17 条不变量） | — |

### 🟢 死代码（可直接清）

| ID | 名称 | 可清理度 |
|---|---|---|
| DEAD-1 | `frontend/src/views/Report/index.vue` (299) 路由未注册 | ✅ 完全可删 |
| DEAD-2 | `frontend/src/api/task.js` 全前端无 import | ✅ 完全可删 |
| DEAD-3 | `frontend/src/api/camera.js` 全前端无 import | ✅ 完全可删 |
| DEAD-4 | `api/report.js: getRecords / getTrend / exportPdfReport` 局部死代码 | ✅ 可删 |
| DEAD-5 | `/api/v1/cameras/*` 8 个后端端点 | ⚠️ 删要确认无第三方调用 |
| DEAD-6 | `/api/v1/tasks/*` 7 个端点（？） | ⚠️ 需检查 record API 是否仍用 |
| DEAD-7 | `views/Report` 内的 `views/Report/index.vue` 子调用 | ✅ 随 DEAD-1 一起 |
| DEAD-8 | `source_recording_mixin.py` 546 行（历史拆分残留） | ⚠️ 确认 MRO 后删 |
| DEAD-9 | CI `CORE_FILES` 中 3 个不存在的文件 | ✅ 删行 |

---

## 三、真 bug 详情（6 个，2026-07 全部销账/关闭）

### BUG-1 ✅ 已修（2026-07 销账） `_fix_db_paths` 用错表名

**位置**：`backend/core/config.py: _fix_db_paths`

**原现象**：函数做模型文件绝对路径迁移时 SQL 写成了不存在的 `ml_models` 表，外层
try/except 兜底导致迁移静默失效，升级客户须手动重选模型。

**现状（已核实代码）**：`_fix_db_paths` 已改为遍历 `('models', 'video_clips')` 两张
真实表，带表存在性探针 + 正/反斜杠双路径替换，源码内留有指回本条目的注释。
**本条销账保留 ID 供历史引用，不再是待修项。**

**插件系统相关**：插件如果模仿这套迁移机制，直接抄现版代码即可（表名已正确）。

---

### BUG-2 ✅ 已修（2026-07 白名单重审）：CI `CORE_FILES` 不存在的文件

**位置**：`.github/workflows/build.yml: CORE_FILES`

**原现象**：列表 11 项里 3 项不存在（`api/detection.py` / `api/websocket.py` / `services/detector.py`），
CI 静默跳过只 WARNING，实际只编译 8 个 .pyd，大量核心逻辑明文出厂。

**2026-07 修复内容**：
- 剔除 3 个失效项；白名单扩到 17 项：新纳入 `source_routes.py`、Top5 大 mixin
  （per_item / session_lifecycle / settlement / tracking / periodic_actions）、
  `services/scanner.py`、`services/mes_hooks.py`、`services/weighing_engine.py`
- 白名单文件缺失从"静默跳过"改为 **`::error` + exit 1 直接 fail**（改名/删文件必须同步 build.yml）
- 残余风险：其余小 mixin / has-a 组件 / `export_*.py` 仍源码出厂，按需扩列
- ⚠️ 待第一次 tag 构建验证：Nuitka 编译大 mixin 若失败会降级保留 .py（看 build 日志确认 .pyd 生成）

**插件系统相关**：插件代码默认走 .py 形态分发（不强制编译），但如果客户需要 .pyd 加密，需要复用 CI 流程。

---

### BUG-3 🟢 降级：孤儿文件（2026-07 复核）同名方法"冲突"实为死文件

**位置**：
- `backend/api/source_camera_start_mixin.py: start_hcnetsdk / start_hikvision_camera`（**生效版**）
- `backend/api/source_industrial_camera_mixin.py`：同名方法的历史旧版

**复核结论（2026-07）**：`source.py` 已**不再 import** `IndustrialCameraMixin`（grep 0 命中），
继承链里也没有它——不存在 MRO 决胜问题，实际生效的一直是 `CameraStartMixin`。
该文件是历史拆分残留的**纯孤儿文件**（438 行），与 DEAD-8（`source_recording_mixin.py`）同类。

**残余影响**：grep 两个方法名仍会命中两份实现，新人可能改错文件。

**修复**：清退孤儿文件即可（走 `modify-source` skill 影响分析后删除），从"行为不可预测"
降级为"死代码待删"。

**插件系统相关**：插件加自定义视频源（add-source-type skill）时**先确认 MRO 顺序**，否则插件方法可能被覆盖。

---

### BUG-4 🟠 录制 mixin 三方重叠

**位置**：
- `source_recording_mixin.py` (546 行) — 老版合并版
- `source_recording_thread_mixin.py` — 拆分后的"线程"部分
- `source_recording_api_mixin.py` — 拆分后的"API"部分

**现象**：
```python
# source.py 继承链:
class VideoSourceManager(
    ..., RecordingThreadMixin, RecordingApiMixin, ...
    # 注意: 不含 RecordingMixin
)
```

`source_recording_mixin.py` 不在 MRO 里——但文件还在。

**影响**：
- 546 行死代码（疑似）
- 任何人改"录像逻辑"都可能误改死代码
- IP 暴露面增加

**修复**：确认无人 import → 删

**插件系统相关**：录像相关的插件改造（如改格式）必须找对地方。

---

### BUG-5 ✅ 已销账（2026-07）：类名错写的出处已删除

原指 `docs/产品交接手册.md` v2.4.0 错写"`MLModel` 类 `ml_models` 表"。该手册已于
2026-06-26 删除（见 DOC-4 撤销注），错误出处不复存在。正确口径（类名 `Model`、
表名 `models`）已固化在 AGENTS.md 第五节"ORM 提醒"，本条保留 ID 供历史引用。

---

### BUG-6 ✅ 已销账（2026-07）：表数错写的出处已删除

原指同一手册错写"22 张数据库表"。手册已删除；现行权威表清单在 `modify-model`
skill（2026-07 校正为 6 个 ORM 文件共 **47 张**，含 `weighing_records`），本条随
BUG-5 一并销账。

---

## 四、死代码索引（9 项）

### 前端死代码

| 项 | 位置 | 大小 | 状态 | 清理风险 |
|---|---|---|---|---|
| DEAD-1 | `frontend/src/views/Report/index.vue` | 299 行 | ✅ **2026-07 已删除** | 全仓核实无引用 + build 绿 |
| DEAD-2 | `frontend/src/api/task.js` | 26 行 | ✅ **2026-07 已删除** | 同上 |
| DEAD-3 | `frontend/src/api/camera.js` | 26 行 | ✅ **2026-07 已删除** | 同上 |
| DEAD-4 | `frontend/src/api/report.js`（整文件） | ~60 行 | ✅ **2026-07 已删除**（唯一消费者 DEAD-1 同批删） | 同上 |

### 后端死代码

| 项 | 位置 | 大小 | 状态 | 清理风险 |
|---|---|---|---|---|
| DEAD-5 | `backend/api/cameras.py` 8 个端点 | 152 行 | `api/camera.js` 死 → 全前端无调 | ⚠️ 第三方 / 测试可能用 |
| DEAD-6 | `backend/api/tasks.py` 7 个端点 | 193 行 | 部分死 | ⚠️ `record` 可能仍用 |
| DEAD-8 | `backend/api/source_recording_mixin.py` | 546 行 | ✅ **2026-07 已删除**（全仓无 import，import 冒烟 + 37 项录像/ROI/路由回归绿） | — |
| DEAD-10 | `backend/api/source_industrial_camera_mixin.py`（原 BUG-3 降级） | 438 行 | ✅ **2026-07 已删除**（同批核实删除，`start_hcnetsdk` 唯一实现归 `source_camera_start_mixin`） | — |

### CI 死代码

| 项 | 位置 | 状态 |
|---|---|---|
| DEAD-9 | `.github/workflows/build.yml: CORE_FILES` 中 3 个不存在文件 | 跳过 with WARNING |

**清理策略**：
1. 用 `git rm` + 一个集中 commit `chore(cleanup): 清理 v3.5+ 已确认死代码`
2. 删前**先打 tag** 回滚用
3. 删后**冒烟测试**：完整启动 + 启动 detection + 看 docs 页面

---

## 五、重叠 mixin / 同名方法冲突（2 项在册，1 项已关闭）

| ID | 位置 | 现象 |
|---|---|---|
| OVERLAP-1 | ✅ 已关闭：`IndustrialCameraMixin` 孤儿文件 2026-07 已删（原 BUG-3/DEAD-10） | — |
| OVERLAP-2 | `_inspecting` 字典 race | scan_pair 模式 v3.4.2 hotfix 修了一波，但仍可能 cornercase |
| OVERLAP-3 | API 双挂载路径 | `api_router` 聚合 9 + `main.py` 直挂 10 |

**OVERLAP-3 详细**：
```python
# 路径 A: backend/api/__init__.py
api_router.include_router(projects.router, prefix="/projects")
api_router.include_router(models.router, prefix="/models")
# 9 个

# 路径 B: backend/main.py
app.include_router(source_router, prefix="/api/v1/source", tags=["source"])
app.include_router(channel_manager_router, prefix="/api/v1", tags=["workstations"])
# 10 个
```

**影响**：
- 新增路由不知道走哪个路径
- 插件系统设计**必须明确选其一**（推荐用路径 A 套路：插件 router 注册到 `api_router`，main.py 不变）

**修复建议**：把所有路径 B 的也搬到 `api_router` 集中管理。**但这是大改动**，可能与插件系统的 P3.5 同步做。

---

## 六、现状无 registry（先重构再做插件，8 项）

这一节是 **03 文档第三节"B 类注册型扩展"** 的"待重构"清单。

| ID | 项 | 现状 | 改造工时 | 必要性 |
|---|---|---|---|---|
| REG-1 | Detect Runners (yolo/track/segment) | 硬编码 if/elif | 1 天 | 客户用非 YOLO 模型时必须 |
| REG-2 | Scanner 协议 (LON/WMax/Virtual) | 硬编码 | 2 天 | 客户用其他扫码枪时必须 |
| REG-3 | External Device 协议 | 半硬编码 | 1 天 | 客户加新外设时需要 |
| REG-4 | Export Renderer (5 格式) | 硬编码 if/elif | 0.5 天 | 客户加 ESC/POS 等格式时 |
| REG-5 | `_trigger_event` listener | 无 listener | 0.5 天 | 业务流挂载点 #1，**强烈推荐做** |
| REG-6 | MES Hook 入队前 filter | 无 | 0.5 天 | 测试期 / 调试期需要 |
| REG-7 | `alarm_router.trigger_alarm` 拦截 | 无 | 0.5 天 | 客户加钉钉 / 微信报警时 |
| REG-8 | Scanner `_on_data_received` 后置 hook | 无 | 0.3 天 | 极少需要 |

**优先级建议**：
- **必做**（插件 1.0 同时做）：REG-5 / REG-6
- **应做**（如果有客户具体需求）：REG-1 / REG-2 / REG-7
- **可推迟**：REG-3 / REG-4 / REG-8

---

## 七、API 路径不一致 / 命名歧义（5 项）

### INCONSIST-1：`/api/v1/workstations/*` 实际是顶层 `/api/v1/`

```python
# main.py:
app.include_router(workstation_router, prefix=f"{settings.API_V1_STR}", tags=["workstations"])
# 即 prefix="/api/v1"

# channel_manager.py 内部:
@router.get("/")
@router.post("/mode")
@router.put("/channel-config")
```

**实际路径**：
- `GET /api/v1/` → 通道总览
- `POST /api/v1/mode` → 设置模式
- `PUT /api/v1/channel-config` → 通道配置
- `POST /api/v1/{channel_id}/gpu` → 单通道 GPU
- `GET /api/v1/gpu-allocation` → GPU 分配
- `GET /api/v1/{channel_id}/status` → 单通道状态

**问题**：
- 这 6 个端点实际占用 **顶层 `/api/v1/`**
- 插件 prefix 不能用 `/api/v1/{channel_id}/...` 模式，否则与此冲突
- AGENTS.md 写的"`/workstations/*` 前缀"实际**不准确**

**修复建议**：迁移到显式 `/api/v1/workstations/*` 前缀（兼容期同时挂两套，半年后下线旧路径）。

---

### ~~INCONSIST-2：`/api/v1/mes/gateway/*` 路径未确认~~ [VOIDED 2026-05-09]

> ❌ **本条已撤销**——经代码核实，`mes_gateway.py:15` 自带 `APIRouter(prefix="/mes/gateway")`，main.py:789 又叠 `/api/v1`，**实际就是 `/api/v1/mes/gateway/*`**，与 AGENTS.md 一致。
>
> 当初定位失误的根本原因：盘点时只 grep 了 main.py 的 `include_router` 行和 mes_gateway.py 的 `@router.get/post` 行，**漏看了 APIRouter 构造时的 prefix 参数**。后续盘点 API 路径时必须 grep `APIRouter\(prefix=` 一并核对。

**修订后的"教训"**：

| 启示 | 适用范围 |
|---|---|
| FastAPI APIRouter 可以自带 prefix，main.py 的 include_router 还会叠 | 所有路由文件均要双层确认 |
| AGENTS.md 的 19 个 API 前缀对照表应该是权威 | 后续盘点以 AGENTS.md 为基准做差异检查，而不是从代码反推 |

---

### INCONSIST-3：API 命名不规则

| 路径 | 风格 |
|---|---|
| `/api/v1/external-devices/*` | 短横连接 |
| `/api/v1/mes-gateway/*` (?) | 短横 |
| `/api/v1/scan-pair/*` | 短横 |
| `/api/v1/scanner/wmax/*` | 嵌套 |
| `/api/v1/data/cycles/by-serial/*` | 路径段语义化 |

不统一但还能用。插件系统建议统一用 `/api/v1/plugins/{customer_code}/{path}` 风格，**不要复制现有不规则**。

---

### INCONSIST-4：MJPEG / snapshot 不在 `/api/v1` 下

```
/video_feed?channel=N    ← 直接挂在 root, 历史原因
/snapshot?channel=N      ← 同上
/uploads/*               ← StaticFiles 在 root
/recordings/*            ← StaticFiles 在 root
/health                  ← root
```

**插件系统 MJPEG 流如果要复用**：必须用 `<img src="http://localhost:8001/video_feed?channel=0">`，**不能放在 `/api/v1/...` 子路径下**。

---

### INCONSIST-5：前端 `api/detection.js` 命名与后端 `/source/*` 路径不对应

- 前端 `api/detection.js` 调的是 `/api/v1/source/detection/*`
- 命名"detection" 但路径是 "source/detection"
- 历史原因：原来有 `api/detection.py` 路由（已删），前端 axios 文件名留下来了

**插件系统**：注意这种历史遗留，**不要按文件名猜路径**。

---

## 八、文档过时不同步（5 项）

| ID | 位置 | 说什么 | 真相 |
|---|---|---|---|
| ~~DOC-1~~ | ✅ 已修（2026-07-05）：`frontend/src/api/export.js` 注释 | ~~`fmt: 'txt'\|'csv'`~~ | 已补全 5 种 (`txt/csv/docx/xlsx/pdf`) |
| ~~DOC-2~~ | ✅ 已修（2026-07-05）：`backend/services/export_context.py` 5 处注释 | ~~"302 字段骨架"~~ | 硬编码数字移除，改为指向 `export_field_registry.ALL_FIELDS`（v3.31 实测 308） |
| ~~DOC-3~~ | ✅ 已修（2026-07-05）：`backend/services/mes_gateway.py` 注释 | ~~提及 "Jinja" 过滤器~~ | 已注明 Gateway 模板是自研 `{key.path}` 占位符替换、非 Jinja2 |
| DOC-5 | `AGENTS.md` 第七节 | "`MES Adapter 注册`" 部分 | 已注释明确是 `_REGISTRY`，但没说 `register_adapter` 函数已暴露 |
| DOC-6 | brief 给 AI 的交接说明 | 23 个 skill | 实际 27 个（详见首轮分歧汇报） |

> 注：原 DOC-4（指向 `docs/产品交接手册.md` 与「AGENTS.md 第十四节」）已于 2026-06-26 移除——该手册已删除、AGENTS.md 瘦身后无第十四节，债项不复存在。ID 不重排以免破坏外部引用。

**修复建议**：
- ~~DOC-1 / DOC-2 / DOC-3~~：✅ 2026-07-05 注释已修
- DOC-5：03 文档已修正（B1 写明）
- DOC-6：本系列文档已校对

---

## 九、文件过大需重构（行数 2026-07 实测刷新）

按行数排序（>= 1000 行；旧表行数普遍低估 40-60%，本次全部重测）：

| 文件 | 行数 | 类型 | 重构难度 | 重构建议 |
|---|---|---|---|---|
| `frontend/src/views/Monitor/index.vue` | **5867** | Vue | 极高 | 拆 panel 子组件（RFC 同 Project 页）。M-1 录像异常遮罩 4 处同构块 → `RecordingFailureOverlay.vue`（2026-07-03，6293→6091 行）；M-2 单工位 SOP 流程卡片条 → `SopStepPanel.vue`（2026-07-03，6091→6002 行，滚动驱动走 defineExpose）；M-3 混合物品校验看板 → `CustomMixItemPanel.vue`（2026-07-03，6002→5905 行，三形态+computed 随迁）；M-4 多通道视频卡片壳 → `ChannelVideoCard.vue`（2026-07-03，5905→5867 行，流机制/framePump/画框全留父级，canvas 走函数 props 回注，真流 UAT 验证通道不串台，⚠️ 待主作者复核）。M 系列四批全部完成；后续瘦身空间在单工位模板与轮询处理函数（另立批次再议） |
| `frontend/src/views/Project/index.vue` | **2704** | Vue | ✅ 拆分完成 | 逐批拆 Tab（RFC: docs/rfc/Monitor_Project巨型视图拆分_立项方案.md）。P-1 称重配置 Tab → `WeighingConfigTab.vue`；P-2 四对话框 → `CreateProjectDialog` / `ModelSelectDialog` / `FormatSelectDialog` / `RoiEditorDialog` + `modelFormats.js`；P-3 事件设置 Tab → `EventsConfigTab.vue`；P-4 逻辑设置 Tab → `LogicConfigTab.vue` + `perItemLabel.js`；P-5 步骤/物品设置 Tab → `StepsConfigTab.vue` + `mixItemDefaults.js` / `stepEnabled.js`（均 2026-07-03，累计 5971→2704 行，P-1~P-5 全部完成）。剩余 2704 行为基础设置 Tab + 保存/加载/模型编排等父级职责，暂不再拆 |
| `frontend/src/views/Settings/index.vue` | 2617 | Vue | 中 | 8 个 tab 拆成子组件 |
| `frontend/src/views/Data/index.vue` | 2258 | Vue | 高 | 表格 / 导出对话框拆 |
| `backend/api/source_routes.py` | 2178 | Py | 高 | 按端点域分组拆文件 |
| `backend/api/source.py` | 2054 | Py | 极高 | 已 mixin 化但主类还在 |
| `backend/services/scanner.py` | 2003 | Py | 高 | LON/WMax/Virtual 拆 protocol 子模块（同时做 REG-2） |
| `backend/services/mes_hooks.py` | 1739 | Py | 高 | phase 拆分（做 03 文档 C3） |
| `backend/api/source_per_item_mixin.py` | 1621 | Py | 高 | v3.8+ 逐件覆盖，判定/挂起/结算可分组 |
| `backend/api/source_session_lifecycle_mixin.py` | 1608 | Py | 中 | 16 个生命周期方法可分组 |
| `frontend/src/views/MES/ScannerPanel.vue` | 1492 | Vue | 中 | LON / WMax 拆开 |
| `frontend/src/views/MES/GatewayPanel.vue` | 1395 | Vue | 中 | adapter 配置 form 拆 |
| `backend/services/cluster_collector.py` | 1205 | Py | 中 | host / slave 路径拆分 |
| `backend/api/sessions.py` | 1071 | Py | 低 | 已经在拆（已含子 router 挂载） |
| `backend/api/alarm.py` | 1067 | Py | 中 | AlarmManager / AlarmRouter 拆分 |

**插件系统视角**：
- 插件**不要直接改这些大文件**
- 拓展应通过 hook / registry 等接口
- 如果不得不改（如 D2 Layout 菜单注入），尽量插入而不重写

---

## 十、SQLite → PG 迁移痛点（7 项，brief 第二件大事）

| ID | 项 | 工时 | 说明 |
|---|---|---|---|
| MIG-1 | SQLAlchemy URI 切到 `postgresql+psycopg://...` | 0.5 天 | 简单 |
| MIG-2 | 7 处 raw SQL 改条件分支 | 1 天 | `PRAGMA` / `sqlite_master` / `ALTER TABLE` 语法不同 |
| MIG-3 | 60+ ALTER TABLE 默认值兼容（`DEFAULT 1` → `DEFAULT TRUE`） | 1 天 | 全在 `main.py:migrate_database` |
| MIG-4 | 老客户数据迁移脚本（pgloader 或手写） | 2 天 | 绝不能丢数据 |
| MIG-5 | Inno Setup 内嵌 PostgreSQL 静默安装 | 3 天 | **最大头**，安装包大小 +200MB |
| MIG-6 | Electron 启动顺序 + PG service 健康检查 | 1 天 | spawn 后端前要等 PG 起 |
| MIG-7 | 备份/恢复脚本重写（`pg_dump` 替代文件拷贝） | 0.5 天 | `/data/backup/database` 端点要换 |

**合计：约 9 天**（与 brief 工时基线吻合）。

**特别警示**（来自 AGENTS.md 隐含约定）：
- **老客户数据无损升级**是底线 → MIG-4 必须有迁移脚本 + 回滚
- **客户机是 Windows 工控机，老板出差现场维护** → PG 静默安装失败要有 fallback 路径

---

## 十一、测试覆盖盲区（5 项）

### TEST-1 🟠 测试 fixture 未独立 DB

**现象**：v3.5.0 加了 BDD 测试框架，但 fixture 直接用 `sql_app.db` 跑 → 测试可能污染开发库。

**修复**：fixture 用独立 DB（`tmpdir/test.db`），每个 fixture 用 unique uuid。

### TEST-2 🟠 没有插件系统的回归测试

**现象**：当前测试覆盖率不明（应跑 coverage），但**插件系统的边界测试 0 行**——因为还没做。

**修复**：插件 1.0 上线前必须有：
- 单元：插件 manifest 解析 / 签名验证 / registry 注册
- 集成：插件加载/卸载/启停
- 端到端：1 个示例插件覆盖档位 1/2/3

### TEST-3 🟠 关机 8 步流程没自动化测试

**现象**：每次发版都靠手动测，曾经多次因为关机灯柱不灭、串口不释放出问题。

**修复**：写 Electron e2e 测试模拟"启动 → 跑 1 个 cycle → 关机"全流程。

### TEST-4 🟡 集群主从没沙箱

**现象**：测试集群只能搭真实 2 机环境。

**修复**：写"单机模拟主从"的 fixture（用 mock socket 替代）。

### TEST-5 🟡 模型转换 / TRT engine 测试用真 GPU

**现象**：CI 没 GPU runner，模型转换测试只能在本地跑。

**修复**：跑 ONNX 部分（CPU 可达）+ 标记 GPU 测试为 `@pytest.mark.gpu`。

---

## 十二、历史 bug 高发模块（信息性，6 处）

来自 AGENTS.md 第十二节"历史 bug 全档"统计：

| 模块 | 历史 bug 条数 | 风险等级 |
|---|---|---|
| `debug-mes` skill 范围（mes_hooks/scanner/gateway/cluster） | **61** | 🔴 改前必读对应 skill |
| `modify-frontend` 范围 | 45 | 🟠 |
| `modify-source` 范围 | 40 | 🟠 |
| `debug-source` 范围 | 24 | 🟠 |
| `add-api-endpoint` 范围 | 22 | 🟡 |
| `debug-video` 范围 | 19 | 🟡 |

**插件系统视角**：插件触达 mes_hooks / scanner 时，**先读 debug-mes skill**——里面的 61 条历史 bug 是项目最大踩坑区。

---

## 十三、隐式约定 / 缺乏护栏（8 项 — ✅ 2026-07-05 全部文档化销账）

这一节列出"AGENTS.md 第八节'关键不变量'**没全部覆盖**"的隐式约定。
**2026-07-05 处理完毕**：HIDDEN-1/2/3/5 以补充说明并入既有不变量第 10/4/9/7 条，
HIDDEN-4/6/7/8 新增为不变量第 14/15/16/17 条。以下保留原始条目供历史引用。

### HIDDEN-1：`OPENCV_FFMPEG_CAPTURE_OPTIONS` 必须在 cv2 import 前

✅ 已在 AGENTS.md 第八节标。但**没标"插件如果在 conftest.py 之前 import cv2 也会塌"**。

### HIDDEN-2：`channel_manager.set_channel_count` 配套清理

✅ 已在 AGENTS.md 第八节。但**没标"plugin 加 channel-aware 状态时也得 hook on_channel_removed"**——插件系统设计时要补这条。

### HIDDEN-3：bat 热补丁必须 CRLF 换行

✅ 已在 AGENTS.md 第八节。但**没标"插件分发包 manifest.json 也建议 LF（标准 JSON）但 README.bat 必须 CRLF"**。

### HIDDEN-4：`_inspecting[channel_id]` 取-放时序

⚠️ 部分在 02 文档第十二节标了。但**没全集中**。

### HIDDEN-5：MJPEG 双缓冲交换间隔

⚠️ 在 Monitor/index.vue:3085 标 `STREAM_SWAP_INTERVAL = 600`（5 min），但**没文档化为什么是 5 min**——是 Chromium 解码器内存增长经验值。

### HIDDEN-6：MES Hook 队列 critical=True 含义

⚠️ 在 `mes_hooks.py:348` 注释里有，但**没文档化"哪些事件该 critical / 哪些不该"**。

### HIDDEN-7：报警事件优先级

⚠️ 在 alarm.py 内部 `_recompose_and_apply` 里有优先级算法，但**没明确文档化优先级表**。

### HIDDEN-8：`workstation_config.json` schema

⚠️ 04 文档第七节列了样例，但**没强制 schema**——`channel_manager.save_channel_source(merge=False)` 时整个被覆盖，新人误删 key 会丢配置。

---

## 十四、★ 给插件系统看的"先做什么再做什么"

按 brief 的工时基线（项目梳理 1-2 天 + 插件 11 天 + PG 9 天 = 22 天）+ 本文识别出的债，建议的**做事顺序**：

### 阶段 0（梳理 + 紧急修，2 天）

1. ✅ **本系列 5 份文档完成**（已完成）
2. 修 BUG-1（0.1 天）—— `_fix_db_paths` 改表名
3. 清死代码 DEAD-1/2/3/4/9（0.5 天）—— 用一个 commit 集中清
4. ~~修 INCONSIST-2~~ [已验证为误报, 见 §INCONSIST-2 撤销条目]
5. 决定 OVERLAP-3 路由统一方案（讨论后才做）

### 阶段 1（插件系统先决条件，3 天）

6. 实施 03 文档 6.1 节"必加"扩展点 Top 5：
   - C6（启动/关机生命周期 hook）—— 0.5 天
   - A8 + D7（SystemConfig 命名空间 + Settings 管理 UI）—— 1 天
   - C9（FastAPI 动态 router）—— 1 天
   - REG-5（C1 `_trigger_event` listener）—— 0.5 天

### 阶段 2（插件系统主体，6 天）

7. C3（`_handle_cycle_end` phase 拆分）—— 1.5 天
8. D1 + D2（前端动态路由 + 菜单）—— 2 天
9. B7（Realtime trigger registry）—— 0.5 天
10. B5（Export field resolver）—— 0.5 天
11. D4（i18n merge）—— 0.2 天
12. D3（CSS 变量主题）—— 1.3 天

### 阶段 3（PG 迁移，9 天）

13. MIG-1 ~ MIG-7 全部

### 阶段 4（兼容 + 测试，2 天）

14. TEST-1 ~ TEST-3
15. 1 个示例插件覆盖三档

**合计：22 天**——与 brief 基线吻合。

> ⚠️ **关键：阶段 0 不能跳**。死代码不清在阶段 1 改主代码时会反复挡路；BUG-1 不修在阶段 3 PG 迁移会埋雷。

---

## 十五、不在本文范围内但需提的事

| 项 | 说明 |
|---|---|
| 客户已装 v3.5.x 升级路径测试 | 没自动化，每次发版手测；插件 1.0 必须不破坏升级 |
| 海康 SDK / Cognex WMax 二进制依赖 | License 限制（不可二次分发），插件不能复制这些 DLL |
| 中文字体文件 (`source_drawer.py` 用) | 跟随安装包；插件想换字体要走 `detection_config.font_path` |
| GPU 显存共享 | 多通道时各 channel 独立模型实例，**不共享权重**——这是个性能开销，插件不该做更多副本 |
| 操作员 / License 联动 | 当前 license 缓存在 SystemConfig（`license.cache`），插件如果要"按 license features 启停"，复用这个 KV |
| 客户码 customer_code 唯一性 | brief 写"后装覆盖前装"，但**没说迁移已有数据怎么处理**——需要插件 1.0 设计时澄清 |

---

## 十六、TODO 给主作者的（如果接受本文档）

- [x] ~~BUG-1 修复~~ — 2026-07 已修（`_fix_db_paths` 真实表名）
- [x] ~~重新审计 BUG-2 CI CORE_FILES~~ — 2026-07 白名单重审完成（17 项 + 缺失硬 fail）
- [x] ~~决定 BUG-3 / BUG-4 mixin 重叠的处置方案~~ — 2026-07 两个孤儿 mixin 已清退
- [x] ~~死代码集中清理~~ — 2026-07 前端四文件已删（后端 DEAD-5/6 待核实第三方调用后另议）
- [x] ~~INCONSIST-2 紧急确认~~ — 2026-05-09 已验证为误报
- [x] ~~5 个文档过时点修正~~ — 2026-07-05 DOC-1/2/3 注释已修，DOC-5/6 此前已校正
- [ ] OVERLAP-3 路由统一（0.5 天，与插件系统 P3.5 同步）
- [x] ~~HIDDEN-* 8 项隐式约定文档化~~ — 2026-07-05 已全部写进 AGENTS.md 第八节（详见第十三节销账注）
- [ ] 插件系统设计阶段就写 TEST-2 测试套（与代码同步）

---

**本文最后更新**：2026-07-05（BUG-5/6 随产品交接手册删除销账，真 bug 类全部清零；此前 2026-07 销账刷新见文首注）
**事实校验**：基于 v3.6.0 源码全量扫描 + AGENTS.md 第九节 + 01~04 文档发现 + brief 给的工时基线；2026-07 局部复核到 v3.31.0
