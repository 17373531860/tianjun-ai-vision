# 03 — 现有扩展点完整清单（核心文档）

> 适用版本：v3.6.0（HEAD `5c97791`）
> 本文目的：把"插件系统能复用的所有扩展点"按**接口契约级别**列全。每一项都给出：现有机制 / 已有内部用例 / 给客户插件的复用方式 / 风险 / 改造代价 / 推荐等级。
>
> 阅读顺序：第一节"分类索引" → 按需要看 A/B/C/D 各类详情 → 第六节"插件系统设计建议"。
>
> 配套：`01_module_map.md`（依赖拓扑）/ `02_data_flow.md`（业务流插入点 P1~P13）。

---

## 一、分类索引（30 个扩展点）

按"**插件想做什么**"→"**用哪类扩展点**"的反向索引：

| 类型 | 数量 | 特点 | 改造代价均值 |
|---|---|---|---|
| **A. 配置型扩展** | 18 个 | 无需改代码，加 JSON 配置即生效 | 极低 |
| **B. 注册型扩展** | 7 个 | 改代码改成 registry 模式，加新组件不动现有代码 | 低～中 |
| **C. Hook/事件型扩展** | 9 个 | 业务流的事件中心，挂 listener | 中 |
| **D. 前端扩展点** | 8 个 | UI 注入 / 主题 / i18n / 动态组件 | 中～高 |

下表是**索引 + 推荐度**（详情见后文）：

| ID | 名称 | 类型 | 现有 | 推荐档位 | 推荐度 | 改造代价 |
|---|---|---|---|---|---|---|
| A1 | `Project.pipeline_config` JSON | 配置 | ✅ 已有 | 3 | ★★★★★ | 0 |
| A2 | `Project.steps_config` JSON | 配置 | ✅ 已有 | 3 | ★★★★★ | 0 |
| A3 | `Project.events_config` JSON | 配置 | ✅ 已有 | 3 | ★★★★★ | 0 |
| A4 | `Project.counters_config` JSON | 配置 | ✅ 已有 | 3 | ★★★★ | 0 |
| A5 | `Project.alarm_config` JSON | 配置 | ✅ 已有 | 3 | ★★★★ | 0 |
| A6 | `Project.detection_config` JSON | 配置 | ✅ 已有 | 1 | ★★★★ | 0 |
| A7 | `Project.data_config` JSON | 配置 | ✅ 已有 | 3 | ★★★ | 0 |
| A8 | `SystemConfig` KV 表 | 配置 | ✅ 已有 | 1/2/3 | ★★★★★ | 0 |
| A9 | `ScannerDevice.broadcast_channels` | 配置 | ✅ 已有 | 3 | ★★ | 0 |
| A10 | `ScannerDevice.scan_d_*` (D 模式) | 配置 | ✅ 已有 | 3 | ★★ | 0 |
| A11 | `ExternalDevice` 配对 | 配置 | ✅ 已有 | 3 | ★★ | 0 |
| A12 | `WorkOrder.binding_scope` | 配置 | ✅ 已有 | 3 | ★★ | 0 |
| A13 | `MESConnection.bound_channels` + `extra_fields_schema` | 配置 | ✅ 已有 | 3 | ★★★ | 0 |
| A14 | `ClusterConfig.channel_station_map` | 配置 | ✅ 已有 | 3 | ★★ | 0 |
| A15 | `data_export_settings` 9 个开关 | 配置 | ✅ 已有 | 3 | ★★ | 0 |
| A16 | 自定义导出模板（v3.5.0） | 配置 | ✅ 已有 | 3 | ★★★★★ | 0 |
| A17 | 实时导出规则（v3.5.0） | 配置 | ✅ 已有 | 3 | ★★★★ | 0 |
| A18 | `periodic_actions`（pipeline_config 子键） | 配置 | ✅ 已有 | 3 | ★★★ | 0 |
| B1 | MES Adapter `_REGISTRY` | 注册 | ✅ 已开放 (`register_adapter`) | 3 | ★★★★★ | 0 |
| B2 | Detect Runners (yolo/track/segment) | 注册 | ❌ 硬编码 | 3 | ★★★ | 中 |
| B3 | Scanner 协议 (LON/WMax/Virtual) | 注册 | ❌ 硬编码 | 3 | ★★★ | 中 |
| B4 | External Device 协议 | 注册 | ❌ 半硬编码 | 3 | ★★ | 中 |
| B5 | Export Field Registry（308 字段） | 注册 | ⚠️ 半开放 | 3 | ★★★★ | 低 |
| B6 | Export Renderer（5 格式） | 注册 | ❌ 硬编码 | 3 | ★★★ | 低 |
| B7 | Realtime trigger（cycle/session/box） | 注册 | ⚠️ todo 标记 | 3 | ★★★★ | 低 |
| C1 | `_trigger_event` 事件中心 | Hook | ❌ 无 listener | 3 | ★★★★★ | 低 |
| C2 | MES Hook 入队前过滤 | Hook | ❌ 无 | 3 | ★★★ | 低 |
| C3 | `_handle_cycle_end` 8 phase | Hook | ❌ 硬编码顺序 | 3 | ★★★★★ | 中 |
| C4 | `cluster_collector._on_box_complete` | Hook | ❌ 无 | 3 | ★★★ | 低 |
| C5 | `alarm_router.trigger_alarm` 拦截 | Hook | ❌ 无 | 3 | ★★★ | 低 |
| C6 | 启动/关机生命周期 | Hook | ❌ 无插件接口 | 3 | ★★★★★ | 低 |
| C7 | Scanner `_on_data_received` 后置 | Hook | ❌ 无 | 3 | ★★ | 低 |
| C8 | Workpiece set_result 前后 | Hook | ❌ 无 | 3 | ★★ | 低 |
| C9 | FastAPI 动态 router | Hook | ⚠️ 未做热卸载 | 3 | ★★★★★ | 中 |
| D1 | Vue Router `addRoute` | 前端 | ❌ 静态数组 | 2 | ★★★★★ | 中 |
| D2 | Layout 菜单注入 | 前端 | ❌ 硬编码 | 2 | ★★★★★ | 中 |
| D3 | CSS 变量主题 | 前端 | ❌ 无主题机制 | 1 | ★★★★ | 中 |
| D4 | i18n `mergeLocaleMessage` | 前端 | ⚠️ API 已有 | 1 | ★★★★ | 极低 |
| D5 | Pinia 动态 store | 前端 | ⚠️ 默认支持 | 2 | ★★★ | 极低 |
| D6 | `/detection/results` 字段扩展 | 前端 | ❌ 字段硬编码 | 2 | ★★★ | 中 |
| D7 | Settings 页插件管理区 | 前端 | ❌ 不存在 | 1 | ★★★★★ | 低（新建即可） |
| D8 | Element Plus 主题色覆盖 | 前端 | ❌ 无 | 1 | ★★ | 中 |

> 推荐度：★★★★★ = 插件系统几乎一定要用 / ★★★★ = 高度推荐 / ★★★ = 适合特定场景 / ★★ = 偶尔用 / ★ = 慎用。

---

## 二、A 类：配置型扩展（18 个）

这一类的特点：**Project / SystemConfig 已经把配置抽象成 JSON / KV，无需改代码就能扩**。插件系统应**优先在这一层扩**而不是另起炉灶。

### A1. `Project.pipeline_config` JSON

**ORM**：`backend/models/models.py:12`
```python
pipeline_config = Column(JSON, nullable=True)  # 步骤序列、推理模式、容器分组、周期性强制动作
```

**典型结构**（基于 `source_project_config_apply.py` + `pipeline_config` 实际用法）：
```json
{
  "logic_mode": "sequential",
  "settle_mode": "auto",
  "settle_dedup": false,
  "ng_cycle_protect_seconds": 0,
  "cycle_timeout_seconds": 0,
  "idle_timeout_seconds": 0,
  "inference_mode": "detection",
  "tracking_enabled": false,
  "container_grouping": { ... },
  "concurrent_groups": [...],
  "periodic_actions": [
    {"interval": 10, "event_id": 2, "run_on_start": false}
  ]
}
```

**应用位置**：`source_project_config_apply.apply_project_config(host, config)` (在 `set_project_config` 调用)

**插件复用方式**：插件想给客户加"每 N 个 cycle 强制 NG 一次"——不用改代码，直接在 Project 编辑里加 `periodic_actions` 配置即可。**或更激进**：插件给 Project 注入新的子键 `pipeline_config.plugin.<customer_code>`，在 `apply_project_config` 末尾遍历该 key 调插件回调。

**风险**：JSON 无 schema 校验，前端 Project 页是 2925 行手工拼的 UI（**不能动态扩展**），所以新增 key 等于"配置项隐藏在数据库里，没 UI 改"。

**改造代价**：0（直接用）/ 给插件提供 UI 的话另算（见 D1~D7）

**推荐档位**：3（全栈插件）

---

### A2. `Project.steps_config` JSON

**ORM**：`backend/models/models.py:15`

**典型结构**（基于 `_apply_steps_config`）：
```json
[
  {
    "label": "缺角",
    "displayLabel": "缺角检测",
    "enabled": true,
    "threshold": 70,            // 百分比 (前端) → 0.70 (后端)
    "min_duration": 0.3,
    "max_duration": 5.0,
    "max_interval": 1.0,
    "disappear_delay": 0,
    "timeout_ng": false,
    "min_frames": 5,
    "gap_tolerance": 2,
    "strict_order": false,
    "accept_once": false,
    "detection_type": "dynamic",     // dynamic / static
    "static_trigger_frames": 30,
    "join_cycle": true,
    "triggerEvent": "ng-1",
    "backup_label": "缺角-备用",
    "primary_label": null
  },
  ...
]
```

**应用位置**：`source_project_config_apply._apply_steps_config(host, steps_config)`

**插件复用方式**：客户特殊步骤逻辑——直接编辑 steps_config，**或**新增 `step.plugin_handler: "<customer_code>.my_handler"` 让插件接管该 step 判定。

**风险**：步骤判定与结算耦合很紧（详见 02 文档第七节），**插件只能做"配置改"，不能加自定义判定逻辑**——除非走 C1 (`_trigger_event` hook)。

**改造代价**：0

**推荐档位**：3

---

### A3. `Project.events_config` JSON

**ORM**：`backend/models/models.py:16`

**典型结构**：
```json
[
  {
    "id": "ng-1",
    "name": "缺角 NG",
    "type": "ng",            // ok / ng / warning / custom
    "audio_url": "/uploads/audio/ng-1.mp3",
    "audio_text": "缺角不良",
    "toast": true,
    "alarm_event": "event2",  // 联动 alarm_config.triggers.event2
    "color": "#FF5722"
  },
  ...
]
```

**应用位置**：被 `Counters.handle_cycle_end(is_good, event_name)` 引用 + 前端 Monitor toast 显示

**插件复用方式**：客户加"客户码-缺陷码-自家枚举"映射，可以在 events_config 里加自定义 type，前端 toast 自动 fallback 到 default 样式。

**改造代价**：0

**推荐档位**：3

---

### A4. `Project.counters_config` JSON

**ORM**：`backend/models/models.py:17`

**典型结构**：
```json
[
  {"name": "总数", "increment_event": "*"},
  {"name": "良品", "increment_event": "ok"},
  {"name": "不良", "increment_event": "ng-*"},
  {"name": "缺角", "increment_event": "ng-1"}
]
```

**应用位置**：`Counters.__init__` 解析 + `handle_cycle_end` 用 fnmatch 匹配 event_name

**插件复用方式**：客户要加"班次产量"——加一项即可。

**改造代价**：0

**推荐档位**：3

---

### A5. `Project.alarm_config` JSON

**ORM**：`backend/models/models.py:18`（v3.x 加进 Project 而不是单独表）

**典型结构**：
```json
{
  "device": "/dev/ttyUSB0",
  "baudrate": 9600,
  "shared_with": [1, 2, 3],     // 多工位共享灯柱
  "triggers": {
    "ok":     {"command": "01 06 ...", "duration": 0.5},
    "event1": {"command": "01 06 ...", "duration": 1.0},
    "event2": {"command": "01 06 ...", "duration": 1.0},
    "ng":     {"command": "01 06 ...", "duration": 3.0},
    "weight_no_barcode": {"command": "...", "duration": 5.0}
  },
  "idle_light": {"command": "...", "blink_interval": 0.5},
  "all_off":   {"command": "..."}
}
```

**应用位置**：`AlarmRouter._load_all` + `AlarmManager.set_shared_mode`

**插件复用方式**：插件加自家"灯柱 / 蜂鸣器 / 客户专属信号塔"——直接配 alarm_config，**配合 C5 hook 拦截 trigger_alarm 自定义实现**。

**改造代价**：0（配置层）；要新协议就得 C5 + 改 AlarmManager（中等）

**推荐档位**：3

---

### A6. `Project.detection_config` JSON

**ORM**：`backend/models/models.py:19`

**典型结构**（detection 视觉效果配置）：
```json
{
  "box_style": "cyberpunk",          // simple / cyberpunk
  "kalman_enabled": true,
  "kalman_process_noise": 0.01,
  "kalman_measurement_noise": 0.1,
  "show_confidence": true,
  "show_label": true,
  "font_color": "#FFFFFF",
  "font_size": 18,
  "max_missing_frames": 5,
  "track_history_frames": 30,
  "mediapipe_enabled": false,
  "mediapipe_pose": true,
  "mediapipe_hands": false,
  "mediapipe_confidence": 0.5
}
```

**应用位置**：`Drawer` + `MediaPipeOverlay` 实例字段

**插件复用方式**：白标插件覆盖默认样式（档位 1）。

**改造代价**：0

**推荐档位**：1（主题包）

---

### A7. `Project.data_config` JSON

**ORM**：`backend/models/models.py:20`

**典型结构**：（与 `data_export_settings` 表互补，是项目级覆盖）
```json
{
  "session_recording_enabled": true,
  "cycle_recording_enabled": true,
  "step_image_enabled": false,
  "csv_auto_export_enabled": false,
  "video_codec": "h264",
  "video_quality": 23
}
```

**应用位置**：`recording_enabled` / `step_image_enabled` 等通过 `apply_project_config` 直接 setattr 到 host

**插件复用方式**：客户要扩"非视频的工艺数据采集"——加 plugin 子键，**配合 C8 hook 在工件落库时同步写**。

**改造代价**：0

**推荐档位**：3

---

### A8. ★ `SystemConfig` KV 表（最重要）

**ORM**：`backend/models/models.py:120`
```python
class SystemConfig(Base):
    __tablename__ = "system_configs"
    id = Column(Integer, primary_key=True)
    key = Column(String(100), unique=True, nullable=False)
    value = Column(Text, nullable=True)
    description = Column(String(500), nullable=True)
```

**已有 API**：`/api/v1/system/display` (GET/PUT) + `/api/v1/system/license-cache` (GET/PUT)

**已用键**：
- `display.brand_name` / `display.app_name` / `display.inspector_name` / `display.device_number` / `display.factory_name` / `display.line_name`（v3.5.0 自定义导出场景用）
- `license.cache`（前端 IPC → 后端缓存）
- `display.monitor.ptMode` / `display.monitor.ctMode`（PT/CT 三档显示，v3.5.1）

**插件强烈推荐用法**：
```python
# 命名空间约定
"plugin.{customer_code}.{key}"

# 例
"plugin.acme.theme_color" = "#FF5722"
"plugin.acme.custom_event_handler" = "module:my_handler"
"plugin.acme.feature_toggle" = "1"
```

**已有写入工具**：`backend/api/system_display.py:_set_kv` / `_get_kv`（直接复用即可，自带 JSON 序列化）

**插件复用方式**：客户级配置全部塞这里，**避免另起一张 plugin_configs 表**。前端 `useSystemStore` 已经做了缓存 + PUT/GET 同步。

**风险**：
- 没有"配置 schema"，类型校验靠插件自查
- `useSystemStore` 现在只缓存 `display.*` 子集，**插件配置要扩 store 字段**

**改造代价**：0（直接用）/ 给插件配置加 store 缓存的话见 D7

**推荐档位**：1 / 2 / 3 通用

---

### A9. `ScannerDevice.broadcast_channels` JSON

**ORM**：`backend/models/mes_models.py: ScannerDevice.broadcast_channels`

**用途**：一台扫码器服务多个工位（如 1 台扫码枪挂在 4 工位汇总台）

**典型结构**：
```json
[0, 1, 2, 3]  // 该扫码器扫到的码同时下发给 ch0/1/2/3
```

**配套字段**：
- `broadcast_settle_mode`：'independent' / 'follow_primary'
- `primary_settle_channel`：主工位号
- `primary_settle_min_items`：主工位至少 N 件后才带其他工位结算

**插件复用方式**：客户要"一码多工位绑定"——直接配置（无需插件）。

**改造代价**：0

**推荐档位**：3

---

### A10. `ScannerDevice.scan_d_*`（D 模式）

**ORM**：v3.4.0 加的 scan_d_geometry / scan_d_line / scan_d_zone / scan_d_gone_confirm_frames

**用途**：D 模式 = 容器跨工位追踪（如盒子在传送带上经过多个工位，按几何区域判断容器到位）

**插件复用方式**：客户走传送带场景——配 line / zone JSON 即可，**无需新写代码**。

**改造代价**：0

**推荐档位**：3（特定场景）

---

### A11. `ExternalDevice` 配对配置

**关键字段**：`pairing_group`（v2.8.1+ 优先按这个匹配）/ `pairing_mode`（stable/instant）/ `weight_no_barcode_alarm_enabled` / `weight_no_barcode_alarm_delay_sec`

**插件复用方式**：客户加"称重器 + 扫码器"配对——配置即可。

**改造代价**：0

**推荐档位**：3

---

### A12. `WorkOrder.binding_scope`（v3.1.0+）

**字段**：`binding_scope` 枚举 `'project'` / `'channels'` / `'cluster'` + `target_channels` (TEXT) / `target_stations` (TEXT)

**用途**：工单按"项目级 / 工位级 / 集群级"绑定，决定哪些 cycle 计入该工单

**插件复用方式**：客户多工位多工单 → 配置 binding_scope（无需插件）。

**改造代价**：0

**推荐档位**：3

---

### A13. `MESConnection.bound_channels` + `extra_fields_schema`

**字段**：
- `bound_channels`：JSON 数组，决定该 MES 推送只服务哪些工位
- `extra_fields_schema`：JSON 数组，定义"用户自定义额外字段"列表

**典型 `extra_fields_schema`**：
```json
[
  {"key": "operator_alias", "label": "操作员别名", "required": false, "type": "string"},
  {"key": "shift", "label": "班次", "type": "select", "options": ["早","中","晚"]}
]
```

**用途**：客户的 MES 字段千差万别，留个 JSON schema 让前端动态生成 form

**插件复用方式**：客户接自家 MES → 直接定义 schema（无需插件）。

**改造代价**：0

**推荐档位**：3

---

### A14. `ClusterConfig.channel_station_map` JSON

**用途**：集群配置——把"通道号"映射到"工位 ID"，slave 上报时带 station_id

**插件复用方式**：客户多机集群 → 配置（无需插件）。

**改造代价**：0

**推荐档位**：3

---

### A15. `data_export_settings` 9 个开关

**ORM**：`models/models.py: DataExportSetting`（每行 = 一组开关，按 project_id 关联）

**字段**：
```
record_cycle_interval / export_step_duration / export_step_interval /
export_step_event / export_cycle_duration / export_cycle_interval /
export_cycle_result / export_counters / export_session_info
```

**用途**：CSV 导出哪些列

**插件复用方式**：客户要"再加一列"——这个表不够灵活，**应该改用 A16/A17 自定义导出**。

**改造代价**：0（但建议弃用此表，全走自定义导出）

**推荐档位**：（弃用）

---

### A16. ★ 自定义导出模板（v3.5.0）

**ORM**：`models/export_models.py: ExportTemplate`

**典型结构**：
```python
ExportTemplate(
  id=1,
  name="客户 A 缺陷报告",
  format="docx",  # txt / csv / docx / xlsx / pdf
  content="""
    工件: {{ workpiece.serial_no }}
    操作员: {{ operator.name }}
    缺陷:
    {% for d in defects %}
      - {{ d.label }} ({{ d.confidence }})
    {% endfor %}
  """,
  is_system=False,  # True 是系统预设, 启动时 upsert
)
```

**渲染上下文 308 字段**：见 `services/export_field_registry.py: ALL_FIELDS`

**已有 API**：`/api/v1/export/templates/*` (CRUD)

**插件复用方式**：客户写自家 docx/xlsx 模板，**完全不动代码** → 上传即用。

**Sandbox**：用 Jinja2 SandboxedEnvironment，**不会污染主程序**。

**风险**：
- 字段命名"`{{ workpiece.serial_no }}`"是 Jinja2 dot path，渲染上下文必须先 build
- 5 种格式各走独立 renderer（B6 注册型扩展）

**改造代价**：0

**推荐档位**：3 ★★★★★

---

### A17. ★ 实时导出规则（v3.5.0）

**ORM**：`models/export_models.py: ExportRealtimeRule`

**典型结构**：
```python
ExportRealtimeRule(
  id=1,
  name="客户 A NG 实时导出",
  template_id=5,
  trigger_event="cycle_end",       # cycle_end ✅ / session_end [todo] / box_complete [todo]
  filter_config={
    "channels": [0, 1],
    "project_ids": [3],
    "is_good": [false],            # 只导 NG
    "event_names": ["缺角", "破损"]
  },
  is_enabled=True
)
```

**触发位置**：`services/mes_hooks._handle_cycle_end` 末尾 `dispatch_cycle_end_export(...)`

**插件复用方式**：客户要"NG 实时落 docx"——配置即可。

**风险**：
- `trigger_event` 只 `cycle_end` 接了，`session_end` / `box_complete` 标 [todo]（见 B7）
- filter_config 是 dict，无 schema

**改造代价**：0（cycle_end 场景）/ 低（补 session_end / box_complete）

**推荐档位**：3 ★★★★

---

### A18. `periodic_actions`（pipeline_config 子键，v3.5.0）

**位置**：`Project.pipeline_config.periodic_actions` (list)

**典型结构**：
```json
{
  "periodic_actions": [
    {"interval": 10, "event_id": 2, "run_on_start": false, "name": "每 10 轮强制 NG"},
    {"interval": 100, "event_id": 4, "run_on_start": true, "name": "每 100 轮强制自定义事件 4"}
  ]
}
```

**触发位置**：`PeriodicActionsMixin._maybe_run_periodic_action`

**插件复用方式**：客户走"产线抽检"逻辑——配置即可。

**改造代价**：0

**推荐档位**：3

---

## 三、B 类：注册型扩展（7 个）

这一类是"加新组件"——比 A 类多一步代码改动，但改完后客户插件可以注册新组件而不用改主代码。

### B1. ★ MES Adapter `_REGISTRY`（已开放）

**位置**：`backend/services/mes_adapters/__init__.py:13`
```python
_REGISTRY: dict[str, type[BaseAdapter]] = {
    "rest": RESTAdapter,
    "form-data": FormDataAdapter,
    "form-urlencoded": FormUrlencodedAdapter,
    "query-string": QueryStringAdapter,
    "modbus_rtu": ModbusRTUAdapter,
}

def register_adapter(name: str, cls: type[BaseAdapter]):
    _REGISTRY[name] = cls
```

**接口契约**（BaseAdapter，`backend/services/mes_adapters/base.py:57`）：
```python
class BaseAdapter:
    def build_payload(self, context: dict, config: dict) -> Any:
        """根据模板配置 + 上下文数据构建请求体（默认走 render_template）"""
        ...

    def send(self, payload: Any, config: dict) -> dict:
        """发送数据到外部 MES, 返回 {status_code, body, success, error}"""
        raise NotImplementedError

    def check_response(self, response: dict, config: dict) -> bool:
        """检查响应是否表示成功（默认 status_code in 200/201, 或按 success_check 配置）"""
        ...
```

**模板语法**（已有 `render_template` 引擎，**不是 Jinja2**）：
```python
"{workpiece.serial_no}"      # 取嵌套字段
"{extra.weight}"             # 取额外字段
{"_array_source": "steps", "_item_template": {...}}  # 数组展开
```

**插件复用方式**：客户协议（MQTT / OPC UA / 客户专属 RPC）：
```python
# 插件 register.py
from backend.services.mes_adapters import register_adapter
from backend.services.mes_adapters.base import BaseAdapter

class MQTTAdapter(BaseAdapter):
    def send(self, payload, config):
        client = mqtt.Client(...)
        client.connect(config["broker"], config["port"])
        client.publish(config["topic"], json.dumps(payload))
        return {"status_code": 200, "body": {}, "success": True, "error": None}

register_adapter("mqtt", MQTTAdapter)
```

**前端 Gateway 配置页**（`views/MES/GatewayPanel.vue`）已有"adapter_type 下拉"——插件注册后，**前端要补 i18n + 下拉枚举**才能让客户在 UI 上选（前端档位 2）。

**风险**：
- 已注册的 5 种 adapter 在导入时静态写入 _REGISTRY，**插件如果晚于 connection 创建时机才注册，已有 connection 用旧的 adapter_type 不会自动迁移**
- 没有"卸载 adapter"接口（`del _REGISTRY[name]` 可以但不优雅）

**改造代价**：0（已开放）

**推荐档位**：3 ★★★★★

---

### B2. Detect Runners (yolo / track / segment)

**位置**：`backend/api/source_detect_runners_mixin.py`
```python
class DetectRunnersMixin:
    def _detect_only(self, frame): ...
    def _detect_and_track(self, frame): ...
    def _detect_segment(self, frame): ...
```

**调用方**：`InferenceLoopMixin._inference_loop` 按 `pipeline_config.inference_mode` 选 runner

**现状**：硬编码 if/elif，**不是 registry**。

**改造方案（提议）**：
```python
RUNNERS: dict[str, callable] = {
    "detection": _detect_only,
    "track": _detect_and_track,
    "segment": _detect_segment,
}

def register_runner(name: str, fn: callable):
    RUNNERS[name] = fn

# inference_loop:
runner = RUNNERS.get(self.project_config['pipeline_config'].get('inference_mode', 'detection'))
results = runner(self, frame)
```

**插件复用方式**：客户走非 YOLO 模型（PaddleDetection / 自家 SDK）：
```python
@register_runner("paddle")
def my_paddle_runner(host, frame):
    results = paddle_model.predict(frame)
    return [{"label": r.label, "confidence": r.score, "bbox": r.bbox, "bbox_norm": ...} for r in results]
```

**风险**：
- 当前 runner 与 `Drawer` / Kalman / step 判定耦合很紧（输出格式必须严格符合 02 文档第六节"detection_results 格式"）
- TRT engine `imgsz` 探测在 ModelLoadMixin 里，runner 不能完全自治

**改造代价**：中（需要先把 `_detect_*` 的形参 self 抽出来作为参数）

**推荐档位**：3 ★★★

---

### B3. Scanner 协议 (LON / WMax / Virtual)

**位置**：`backend/services/scanner.py:1444` `_text_lon_listen_loop` + `services/wmax/` 子包

**现状**：硬编码 4 种类型（LON / WMax / Virtual / auto）

**改造方案（提议）**：
```python
SCANNER_PROTOCOLS: dict[str, type[BaseScannerProtocol]] = {
    "lon": LONProtocol,
    "wmax": WMaxProtocol,
    "virtual": VirtualProtocol,
}
```

**插件复用方式**：客户用 USB HID 扫码枪 / 蓝牙扫码枪 / 客户专属串口协议——注册新 protocol 类。

**风险**：scanner.py 是项目第二大文件（1964 行），抽接口要改的地方多。

**改造代价**：中

**推荐档位**：3 ★★★

---

### B4. External Device 协议

**位置**：`backend/services/external_device_protocols.py`（已有但**没看见明确 registry**，详细需检查）

**典型外设**：称重器（USB-Serial / TCP）、传感器、客户自家 PLC IO

**改造方案**：把外设协议从硬编码 if/elif 改成 registry。

**改造代价**：中

**推荐档位**：3 ★★

---

### B5. ★ Export Field Registry（308 字段，半开放）

**位置**：`backend/services/export_field_registry.py: ALL_FIELDS`（815 行）

**用途**：自定义导出 / 实时规则的"系统数据全集"

**典型结构**（推断）：
```python
ALL_FIELDS = {
    "workpiece.serial_no": {"type": "str", "desc": "工件条码"},
    "workpiece.status": {"type": "str", "desc": "工件状态"},
    "cycle.id": {"type": "int", "desc": "周期 ID"},
    "cycle.is_good": {"type": "bool", "desc": "是否合格"},
    ...
}
```

**插件复用方式**：客户专属字段（如客户自家 ERP 工单号、客户专属维度统计）：
```python
# 插件 register.py
from backend.services.export_field_registry import ALL_FIELDS

ALL_FIELDS.update({
    "plugin.acme.erp_order_id": {"type": "str", "desc": "ACME ERP 工单号"},
    "plugin.acme.shift_label": {"type": "str", "desc": "ACME 班次"},
})

# 同时插件要注册"字段值生成器"
from backend.services.export_context import register_field_resolver

@register_field_resolver("plugin.acme.erp_order_id")
def resolve_erp_order(db, cycle_id, channel_id):
    # 查 SystemConfig + 当前 cycle
    return f"ACME-{cycle_id}-..."
```

**风险**：
- 当前 `register_field_resolver` 这个接口**不存在**，需要先在 `export_context.py` 加（中等改造）
- 308 字段已经基本覆盖，**插件加字段需走"plugin.<customer_code>.<key>"命名空间**避免冲突

**改造代价**：低（加 register_field_resolver 接口）

**推荐档位**：3 ★★★★

---

### B6. Export Renderer（5 格式）

**位置**：
- `services/export_renderer.py`（txt / csv 主入口）
- `services/export_renderer_docx.py`
- `services/export_renderer_pdf.py`
- `services/export_renderer_xlsx.py`

**现状**：硬编码 if/elif（按 `template.format` 选 renderer）

**改造方案**：
```python
RENDERERS: dict[str, callable] = {
    "txt": render_txt,
    "csv": render_csv,
    "docx": render_docx,
    "xlsx": render_xlsx,
    "pdf":  render_pdf,
}
```

**插件复用方式**：客户要"打印小票"格式（ESC/POS）/ 自家专属二进制格式——注册新 renderer。

**风险**：低；renderer 接口已经较为干净（input: template_str, context, output_path; output: RenderResult）。

**改造代价**：低

**推荐档位**：3 ★★★

---

### B7. ★ Realtime Trigger Registry（cycle_end / session_end / box_complete）

**位置**：`backend/services/export_realtime.py:13`（注释里标 todo）

**现状**：
- `cycle_end` ✅ 已接（在 `_handle_cycle_end` 末尾调 `dispatch_cycle_end_export`）
- `session_end` ❌ todo，还没接
- `box_complete` ❌ todo，host 聚齐时本应触发

**改造方案**：
```python
TRIGGERS: dict[str, callable] = {}

def register_trigger(name: str, dispatcher: callable):
    TRIGGERS[name] = dispatcher

@register_trigger("cycle_end")
def dispatch_cycle_end_export(db, channel_id, cycle_id, ...): ...

# 然后在
#   - mes_hooks.on_session_end → 触发 TRIGGERS["session_end"]
#   - cluster_collector._on_box_complete → 触发 TRIGGERS["box_complete"]
```

**插件复用方式**：客户要新触发器（"班次结束按规则导出" / "缺陷率超阈值导出"）——注册新 trigger。

**改造代价**：低（trigger 表 + 两处调用点）

**推荐档位**：3 ★★★★

---

## 四、C 类：Hook / 事件型扩展（9 个）

这一类是"业务流程的事件挂载点"——大多数现状是**没有 listener registry**，需要添加。优先级最高的是 C1 / C3 / C6 / C9。

### C1. ★ `_trigger_event` 中心 hook（事件中心）

**位置**：`backend/api/source_event_trigger_mixin.py:19`
```python
def _trigger_event(self, event_id, reason: str) -> bool:
    """触发事件。返回 True 表示事件已触发，False 表示被抑制或失败。"""
    if not self.project_config:
        return False
    # 防重 / NG 保护 / 写库 / 触发结算
    ...
```

**调用密度**：所有事件（OK / NG / 自定义）必经此处，**整个项目最重要的 hook 点**。

**改造方案**：
```python
class EventTriggerMixin:
    _event_pre_hooks = []   # 类级
    _event_post_hooks = []

    @classmethod
    def register_pre_hook(cls, fn):
        """fn(host, event_id, reason) -> dict | None
        return {'cancel': True} 阻止触发
        return {'event_id': new_id, 'reason': new_reason} 修改后转发
        """
        cls._event_pre_hooks.append(fn)

    @classmethod
    def register_post_hook(cls, fn):
        """fn(host, event_id, reason, result: bool) -> None"""
        cls._event_post_hooks.append(fn)

    def _trigger_event(self, event_id, reason):
        for h in self._event_pre_hooks:
            result = h(self, event_id, reason)
            if result and result.get('cancel'):
                return False
            if result and 'event_id' in result:
                event_id = result['event_id']
                reason = result.get('reason', reason)
        # 原逻辑...
        ok = ...
        for h in self._event_post_hooks:
            h(self, event_id, reason, ok)
        return ok
```

**插件复用方式**：
```python
@EventTriggerMixin.register_post_hook
def my_event_logger(host, event_id, reason, ok):
    if not ok: return
    requests.post("https://my-erp.com/event", json={
        "channel": host.channel_id,
        "event": event_id,
        "reason": reason
    })
```

**风险**：
- pre_hook 改 event_id 可能导致 events_config 找不到 → 必须容错
- 同步 hook 阻塞推理线程，**慢 hook 必须 fire-and-forget 或 enqueue**

**改造代价**：低

**推荐档位**：3 ★★★★★

---

### C2. MES Hook 入队前过滤

**位置**：`services/mes_hooks.py:381` `on_scan_received` / `on_cycle_end` / `on_session_*`

**改造方案**：
```python
class MESHookManager:
    _enqueue_filters = []

    def register_enqueue_filter(self, fn):
        """fn(event_type, args, kwargs) -> bool. False 阻止入队"""
        self._enqueue_filters.append(fn)

    def _enqueue(self, func, *args, critical=True, **kwargs):
        for f in self._enqueue_filters:
            if not f(func.__name__, args, kwargs):
                return  # 被插件拒绝
        # 原逻辑...
```

**插件复用方式**：客户要"测试期 / 调试期 / 特定项目不入队"——加 filter。

**改造代价**：低

**推荐档位**：3 ★★★

---

### C3. ★ `_handle_cycle_end` 8 phase 拆分

**位置**：`services/mes_hooks.py:1181`

**8 个 phase**（02 文档第九节）：
```
1. 取出 _inspecting 工件
2. Workpiece.set_result
3. DefectService.create_record
4. WorkOrderService.update_counters
5. cluster_collector.report_cycle_end (slave)
6. dispatch_cycle_end_export (实时规则)
7. mes_gateway.push (standalone/host)
8. _inspecting 清理
```

**改造方案**：把 phase 列表显式定义，phase 之间允许插件插入：
```python
CYCLE_END_PHASES = [
    "extract_inspecting",
    "workpiece_set_result",
    "defect_record",
    "work_order_update",
    "cluster_report",
    "realtime_export",
    "mes_gateway_push",
    "cleanup",
]

class MESHookManager:
    _phase_hooks: dict[str, dict[str, list]] = defaultdict(lambda: {"before": [], "after": []})

    def register_phase_hook(self, phase: str, when: str, fn):
        """when: 'before' | 'after'"""
        assert phase in CYCLE_END_PHASES
        self._phase_hooks[phase][when].append(fn)
```

**插件复用方式**：
```python
@mes_hook.register_phase_hook("workpiece_set_result", "after")
def push_to_acme_erp(db, ctx):
    requests.post(f"https://acme.com/wp/{ctx['workpiece_id']}", ...)
```

**风险**：
- 同步 hook 在 worker 线程，慢 hook 拖累整个队列
- 异常吞吐策略要明确（默认吃异常 vs 重试 vs 落 spill）

**改造代价**：中（要重写 _handle_cycle_end 的结构）

**推荐档位**：3 ★★★★★

---

### C4. `cluster_collector._on_box_complete` 后置 hook

**位置**：`services/cluster_collector.py: _on_box_complete`（在 host 聚齐时调用）

**当前现状**：硬编码推 MES Gateway，未触发实时规则（标 [todo]）

**改造方案**：补上 B7（trigger registry）+ 前置/后置 hook：
```python
self._trigger_realtime("box_complete", {"box_serial": ..., "summary": ...})
for h in self._box_complete_hooks:
    h(self, box_serial, summary)
```

**插件复用方式**：客户要在 box 聚齐时做汇总（写自家系统、生成报表）。

**改造代价**：低

**推荐档位**：3 ★★★

---

### C5. `alarm_router.trigger_alarm` 拦截

**位置**：`backend/api/alarm.py:678` `AlarmRouter.trigger_alarm`

**改造方案**：
```python
class AlarmRouter:
    _trigger_hooks = []

    def register_trigger_hook(self, fn):
        """fn(event_type, channel_id) -> dict | None
        return {'handled': True} 阻止默认 Modbus 触发
        """
        self._trigger_hooks.append(fn)

    def trigger_alarm(self, event_type, channel_id=0):
        for h in self._trigger_hooks:
            r = h(event_type, channel_id)
            if r and r.get('handled'):
                return  # 插件接管了
        self.get(channel_id).trigger_alarm(event_type, channel_id=channel_id)
```

**插件复用方式**：客户要把报警事件推钉钉 / 微信 / 短信。

**改造代价**：低

**推荐档位**：3 ★★★

---

### C6. ★ 启动 / 关机生命周期 hook

**当前现状**：`main.py:_run_startup_init` 硬编码 5 步，关机硬编码 8 步。

**改造方案**：
```python
# core/lifecycle.py (新文件)
STARTUP_HOOKS: list[callable] = []
SHUTDOWN_HOOKS: list[callable] = []

def register_startup_hook(fn, priority=100):
    """priority: 0 = 最先, 1000 = 最后"""
    STARTUP_HOOKS.append((priority, fn))
    STARTUP_HOOKS.sort(key=lambda x: x[0])

def register_shutdown_hook(fn, priority=100):
    SHUTDOWN_HOOKS.append((priority, fn))
    SHUTDOWN_HOOKS.sort(key=lambda x: x[0])

# main.py 启动末尾:
for _, fn in STARTUP_HOOKS:
    try: fn()
    except Exception: traceback.print_exc()  # 插件挂了不影响主程序
```

**插件复用方式**：插件初始化（注册 adapter / 启后台线程 / 装表）：
```python
from backend.core.lifecycle import register_startup_hook, register_shutdown_hook

@register_startup_hook(priority=500)
def init_my_plugin():
    register_adapter("mqtt", MQTTAdapter)
    register_field_resolver("plugin.acme.x", ...)
    ...

@register_shutdown_hook(priority=500)
def cleanup_my_plugin():
    ...
```

**风险**：
- **错误隔离是底线**——一个 hook 抛了不能让主程序起不来（用 try/except 兜底）
- 插件启动顺序敏感时用 priority 控制

**改造代价**：低（新建 lifecycle.py + 修改 main.py 调用点）

**推荐档位**：3 ★★★★★

---

### C7. Scanner `_on_data_received` 后置 hook

**位置**：`services/scanner.py:1719`

**改造方案**：在 `_on_data_received` 末尾调 listener。

**插件复用方式**：客户要在扫码事件触发自家逻辑（不走 MESHook）。

**改造代价**：低

**推荐档位**：3 ★★

---

### C8. WorkpieceService set_result 前后

**位置**：`services/workpiece.py:76`

**改造方案**：注册类似 `pre_set_result` / `post_set_result` 的 hook。

**插件复用方式**：客户要"工件落库前先校验自家规则"。

**改造代价**：低

**推荐档位**：3 ★★

---

### C9. ★ FastAPI 动态 router 注册（档位 3 核心）

**位置**：`backend/main.py: app.include_router(...)` × 11 次

**当前现状**：`app` 在模块级创建后，FastAPI 没原生支持热卸载 router。

**改造方案**：

**方式 1（推荐）**——启动时扫描注册：
```python
# main.py 启动末尾（在 _init_mes_services 之后）:
def _load_plugin_routers():
    plugin_dir = os.path.join(DATA_DIR, "plugins")
    if not os.path.isdir(plugin_dir): return
    for entry in os.listdir(plugin_dir):
        plugin_path = os.path.join(plugin_dir, entry)
        manifest = _load_manifest(plugin_path)  # 验签 + 解析
        if not manifest: continue
        try:
            module = _load_plugin_module(plugin_path, manifest)
            if hasattr(module, "router"):
                app.include_router(
                    module.router,
                    prefix=f"/api/v1/plugins/{manifest['customer_code']}",
                    tags=[manifest["customer_code"]]
                )
                print(f"[Plugin] 注册路由: {manifest['name']}")
        except Exception as e:
            print(f"[Plugin] {entry} 加载失败: {e}")
            # 错误隔离: 单个插件挂了不影响主程序
```

**方式 2（高级）**——运行期热挂载（不推荐，FastAPI 不支持热卸载）：
```python
@app.post("/admin/plugins/reload")
def reload_plugins():
    # 重启整个 uvicorn 进程
    os.execv(sys.executable, [sys.executable] + sys.argv)
```

**插件复用方式**：插件包 `routes.py` 导出 `router = APIRouter()` 即可。

**风险**：
- 路由前缀冲突（必须强制 `/api/v1/plugins/{customer_code}/*`）
- 插件 import 期间崩溃整个 uvicorn 不能起 → **错误隔离必须严格**
- 卸载需要重启

**改造代价**：中

**推荐档位**：3 ★★★★★

---

## 五、D 类：前端扩展点（8 个）

### D1. ★ Vue Router `addRoute`（档位 2 核心）

**当前现状**：`frontend/src/router/index.js` 是静态 routes 数组（见 01 文档第四节）

**改造方案**：
```javascript
// router/index.js 末尾
async function loadPluginRoutes() {
  try {
    const res = await fetch('/api/v1/plugins/manifests');
    const plugins = await res.json();
    for (const p of plugins) {
      if (p.routes) {
        for (const route of p.routes) {
          router.addRoute('Layout', {
            path: `plugin/${p.customer_code}/${route.path}`,
            name: `Plugin-${p.customer_code}-${route.name}`,
            component: () => loadPluginComponent(p.customer_code, route.component),
          });
        }
      }
    }
  } catch (e) {
    console.error('[Plugin] 路由加载失败:', e);
  }
}

// main.js 启动末尾调用
await loadPluginRoutes();
```

**`loadPluginComponent` 实现思路**：
```javascript
async function loadPluginComponent(customerCode, componentName) {
  // 方案 A: import map (Vite 5+ 支持)
  return import(`/plugins/${customerCode}/components/${componentName}.js`);
  // 方案 B: HTTP 拉取 + new Function() (有沙箱风险)
  // 方案 C: 提前打包到 main 包 (失去动态)
}
```

**风险**：
- Vite 编译期看不到插件代码（关键点）
- 插件包必须是预打包的 ES Module（`vite build --lib`）
- HMR 不工作（开发模式难调）

**改造代价**：中（Vite 配置 + 动态 import）

**推荐档位**：2 ★★★★★

---

### D2. ★ Layout 菜单注入

**当前现状**：`frontend/src/layout/Navbar.vue`（550 行）+ Layout 菜单是硬编码的（看着这两个文件就知道每个菜单项是 `<el-menu-item index="/monitor">检测</el-menu-item>` 这种死写）

**改造方案**：
```vue
<!-- Navbar.vue / Layout/index.vue -->
<el-menu>
  <!-- 现有静态菜单 -->
  <el-menu-item index="/monitor">{{ $t('menu.monitor') }}</el-menu-item>
  ...
  <!-- 插件菜单 -->
  <el-menu-item v-for="m in pluginMenus" :key="m.path" :index="m.path">
    {{ $t(m.label) || m.label }}
  </el-menu-item>
</el-menu>

<script setup>
import { ref, onMounted } from 'vue';
const pluginMenus = ref([]);
onMounted(async () => {
  const res = await fetch('/api/v1/plugins/manifests');
  const plugins = await res.json();
  pluginMenus.value = plugins.flatMap(p => p.menus || []);
});
</script>
```

**插件 menu 定义**（manifest 字段）：
```json
{
  "menus": [
    {"label": "ACME 报表", "path": "/plugin/acme/report", "icon": "DataLine", "order": 100}
  ]
}
```

**改造代价**：中（Navbar 550 行需小心，i18n 联动 + ACL）

**推荐档位**：2 ★★★★★

---

### D3. CSS 变量主题（档位 1 主题包）

**当前现状**：项目用 Tailwind + Element Plus，**目前没有 CSS 变量主题**。每个组件直接用 Tailwind 类（`text-blue-500`） + Element Plus 内置色板。

**改造方案**：

**Step 1**：把所有"客户可定制色"提到 `:root`：
```css
/* main.css */
:root {
  --primary: #2196F3;
  --secondary: #FF5722;
  --bg-base: #1A1A2E;
  --text-base: #FFFFFF;
  /* ... */
}

/* 替换 Tailwind / inline 颜色 */
.btn-primary { color: var(--primary); }
```

**Step 2**：插件主题包：
```javascript
// plugin theme.js
const ACME_THEME = {
  '--primary': '#FFEB3B',
  '--secondary': '#212121',
  '--logo-url': 'url("/plugins/acme/logo.svg")',
};

// main.js 启动:
async function applyTheme() {
  const themes = await fetch('/api/v1/plugins/active-theme').then(r => r.json());
  for (const [k, v] of Object.entries(themes.vars)) {
    document.documentElement.style.setProperty(k, v);
  }
}
```

**风险**：
- 改造工作量大，**~50+ 个 .vue 都要替**
- Tailwind 是编译期生成，运行时变颜色受限
- Element Plus 主题色要走 SCSS 变量（不是 CSS 变量），可能要 patch

**改造代价**：中（前期重构 ~3 天）

**推荐档位**：1 ★★★★

---

### D4. i18n `mergeLocaleMessage`（档位 1）

**当前现状**：`frontend/src/main.js:43` 用 `createI18n` + 5 个语言包静态加载。`vue-i18n` 提供 `i18n.global.mergeLocaleMessage(locale, messages)` API。

**改造方案**：
```javascript
// main.js 启动末尾:
async function mergePluginI18n() {
  const res = await fetch('/api/v1/plugins/i18n');
  const pluginI18n = await res.json();
  // pluginI18n = { 'zh-CN': {...}, 'en-US': {...} }
  for (const [locale, messages] of Object.entries(pluginI18n)) {
    i18n.global.mergeLocaleMessage(locale, messages);
  }
}
```

**插件 i18n 文件**：
```json
// plugin/acme/i18n/zh-CN.json
{
  "menu": {"acme_report": "ACME 报表"},
  "plugin.acme.title": "ACME 自定义视图"
}
```

**风险**：极低，API 已经支持。

**改造代价**：极低

**推荐档位**：1 ★★★★

---

### D5. Pinia 动态 store（档位 2）

**当前现状**：4 个 Pinia store 静态注册，但 Pinia 本身支持动态创建：
```javascript
import { defineStore } from 'pinia';
const usePluginStore = defineStore('plugin-acme', { state: () => ({...}) });
```

**插件复用方式**：插件包带自己的 store 文件，模块级 import 即生效。

**风险**：
- store ID 冲突（必须强制 `plugin-{customer_code}` 前缀）
- 与 `useSystemStore` 跨 store 状态同步要小心

**改造代价**：极低

**推荐档位**：2 ★★★

---

### D6. `/detection/results` 字段扩展（前端面板）

**位置**：02 文档 P13 已说明（后端字段装配 + 前端字段处理）

**改造方案**（后端）：
```python
# api/source_routes.py
@router.get("/detection/results")
def get_detection_results(channel: int = Query(0)):
    base_data = {...}  # 现有字段
    # 让插件附加字段
    for ext_fn in PLUGIN_DETECTION_RESULT_EXTENDERS:
        try:
            base_data.update(ext_fn(channel) or {})
        except Exception: traceback.print_exc()
    return base_data
```

**改造方案**（前端）：把 Monitor/index.vue 的字段处理改成"按 plugin section 渲染面板"。

**改造代价**：中（前端改 Monitor 4051 行 ⚠️ 项目最大文件）

**推荐档位**：2 ★★★

---

### D7. ★ Settings 页插件管理区（新建）

**当前现状**：`views/Settings/index.vue` (1441 行 ⚠️) 没有插件管理区域。

**改造方案**：在 Settings 加一个 tab "插件管理"，包含：
- 插件列表（已安装 / 启用 / 禁用 / 客户码 / 版本）
- 上传 .tjvplugin 包
- 详情 / 卸载
- 实时启停开关

**API**：
- `GET /api/v1/plugins` 列表
- `POST /api/v1/plugins/upload` 上传
- `POST /api/v1/plugins/{id}/enable` / `disable`
- `DELETE /api/v1/plugins/{id}`

**改造代价**：低（新增组件 + 新增后端 API）

**推荐档位**：1 ★★★★★（必做）

---

### D8. Element Plus 主题色覆盖

**当前现状**：项目用 Element Plus 默认色板。Element Plus 支持通过 SCSS 变量覆盖主题色。

**改造方案**（`vite.config.js`）：
```javascript
import { defineConfig } from 'vite'
export default defineConfig({
  css: {
    preprocessorOptions: {
      scss: {
        additionalData: `@use "@/styles/element/index.scss" as *;`,
      },
    },
  },
})
```

**插件主题包覆盖**：要做"运行时切换 Element Plus 主题色"得用 `@element-plus/theme-chalk/src/index.scss` 重新编译——**不可能在客户机做**。

**替代方案**：用 CSS 变量（D3）覆盖 Element Plus 关键色（v2.4+ Element Plus 已部分用 CSS 变量）。

**改造代价**：中

**推荐档位**：1 ★★

---

## 六、插件系统设计建议（基于本节扩展点全集）

### 6.1 推荐"必加"扩展点（按工时排序）

| 序 | 扩展点 | 类 | 工时估 | 备注 |
|---|---|---|---|---|
| 1 | C6（启动/关机生命周期 hook） | 后端 | 0.5 天 | 所有插件的注册时机 |
| 2 | A8 + D7（SystemConfig 命名空间 + Settings 管理 UI） | 后/前 | 1 天 | 客户配置存哪 |
| 3 | C9（FastAPI 动态 router） | 后端 | 1 天 | 档位 3 核心 |
| 4 | C1（`_trigger_event` listener） | 后端 | 0.5 天 | 业务流挂载点 #1 |
| 5 | C3（`_handle_cycle_end` phase 拆分） | 后端 | 1.5 天 | 业务流挂载点 #2 |
| 6 | D1 + D2（Vue 动态路由 + Layout 菜单） | 前端 | 2 天 | 档位 2 核心 |
| 7 | B7（Realtime trigger registry） | 后端 | 0.5 天 | 顺便补 todo |
| 8 | B5（Export field resolver） | 后端 | 0.5 天 | 客户专属字段 |
| 9 | D4（i18n merge） | 前端 | 0.2 天 | 简单 |
| 10 | D3（CSS 变量主题） | 前端 | 3 天 | 档位 1 核心，工程量大 |

**合计：约 11 天**——和 brief 里"插件系统 11 天工时基线"对得上。

### 6.2 推荐"暂不加"的扩展点

| 扩展点 | 原因 |
|---|---|
| B2 / B3 / B4（runner / scanner / extdev 协议 registry） | 改造代价高，客户需求出现再做 |
| C2 / C7 / C8（次要 hook） | 用 C1 / C3 / C6 已能覆盖 90% 需求 |
| D6（/detection/results 扩展） | 改 Monitor 4051 行风险高 |
| D8（Element Plus 主题） | D3 已经覆盖 |

### 6.3 命名空间约定（强制）

```
配置 KV:        plugin.{customer_code}.{key}
路由前缀:        /api/v1/plugins/{customer_code}/*
前端路由 name:   Plugin-{customer_code}-{view}
Pinia store id: plugin-{customer_code}
i18n 键:        plugin.{customer_code}.*
导出字段:        plugin.{customer_code}.{field}
菜单 path:       /plugin/{customer_code}/{view}
DB 表前缀:       p_{customer_code}_{table}    (如果插件加表)
```

**`customer_code` 规则**：小写字母+数字+连字符，长度 ≤ 20，全局唯一。

### 6.4 错误隔离边界（再次强调）

> **AGENTS.md 第三节明确写**："插件挂了不能让主程序起不来 → 错误隔离是底线"

实施细则：
1. **所有插件回调统一 try/except**，异常吞到日志，不抛出
2. **启动 hook 失败不阻塞**——单个插件 import 失败不影响其他插件 + 主程序
3. **同步 hook 不能阻塞主线程**——慢操作必须 enqueue 到 worker 线程
4. **plugin module 加载用独立 namespace**（`importlib.util.spec_from_file_location`）避免 import 污染
5. **未签名 / 签名失败的插件不加载**（直接拒绝 + 日志报警）
6. **manifest `main_version_min/max` 不匹配的插件不加载**

---

## 七、TODO / 后续文档配套

- [ ] `04_io_boundaries.md` — 把扩展点 A1~D8 涉及的对外 API + 硬件 IO 整理成完整清单（含端口号、协议、超时）
- [ ] `05_tech_debt.md` — 把本文标 ❌（硬编码无 registry）的项目集中到"需要先重构再做插件"列表

---

**本文最后更新**：2026-05-08
**事实校验**：基于 v3.6.0 源码（含 mes_adapters / system_display / source_project_config_apply / models / scanner.py / mes_hooks.py）+ AGENTS.md 第七节扩展点速查 + 02 文档 P1~P13
