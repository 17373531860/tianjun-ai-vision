# 14 — 统一触发中心（Trigger Hub / 触发源注册表）主程序原生 RFC

> 适用版本：基于 v3.47.0 主线（RFC 13 通用 PLC 连接器已落地）
> 本文目的：把"**什么信号可以让视觉系统做事**"（触发源）做成主程序**原生的、纯配置驱动的基础设施**。目标：任何客户提出的新触发方式（遮挡画面某区域、踩脚踏板、按物理按钮盒、外部系统调一个 URL、串口来一条报文……）都通过**软件内设置**完成，**不再改代码、不再发版**。
>
> **立项背景（决策留痕）**：分析外部原型工具《智能检测监控系统》（screw_detection_qt.py，2026-08-07）时发现其"手动虚拟按钮"（画面区域颜色差分触发结算）在我们软件中无等价物。主作者点破本质："我们现在软件触发的方法并不多，其他客户会不会也想要其他的触发方式？……我们软件原则最高的是通用性。" —— 触发方式是**维度**不是怪癖，维度就该穷举内置（与 RFC 13 同一方法论）。
>
> **归位判定**（`feature-placement` A 类基础设施）：触发源是多客户通用维度、贯穿检测/结算/报警核心通路 → **主程序原生**，同时给插件平台留 `register_trigger_source` + 自定义动作 hook，真正的客户怪癖触发器走插件注册，仍不改主程序。
>
> 阅读前置：AGENTS.md 第三节产品决策原则、RFC 13（动作注册表先例）、`debug-source` skill（`_trigger_event` 中心）。

---

## 一、动机与现状

### 1.1 触发通道盘点：有 8 条，但都是"各自长出来的专用通道"

| # | 触发源 | 落点 | 能触发什么 | 局限 |
|---|---|---|---|---|
| 1 | 视觉检测类别/动作 | `_trigger_event`（`source_event_trigger_mixin.py`）+ `events_config` + v3.32 区域事件动作规则 | 事件/报警/计数/结算 | 只认 YOLO 检测结果 |
| 2 | 扫码枪（4 种用途：绑定/拉工单/**报警确认 ack**/旁路监控） | `scanner.py simulate_scan` → `mes_hooks.on_scan_received` | 绑码/开工/消警 | 只认条码报文 |
| 3 | 电子秤/外设读数 | `external_device_pipeline.feed_weight` → `weighing_engine` | 称重状态机 | 消费端焊死为"重量浮点" |
| 4 | PLC 点位边沿 | RFC 13 `point_engine` read_rules → `rule_actions` | 绑码/切项目/起停/写回/报警 | 只认 PLC 点位 |
| 5 | 界面手动按钮 | `manual_settle` API（`source_routes.py`）、人工确认弹窗 | 结算/确认 | 要人守在屏幕前 |
| 6 | MES 入站报文 | `mes_inbound.py`（开工自动切项目/自动开始检测） | 开工/完工/消警 | 报文格式面向工单语义 |
| 7 | 定时/周期性强制动作 | v3.5.x 周期性强制动作（source 内） | 周期内强制事件 | 只在检测运行中生效 |
| 8 | 插件桥接 | v3.27「插件主动触发主程序事件」 | 事件中心 | 要写插件代码 |

**问题不是数量少，是没有统一抽象**：每条通道"触发源 → 动作"都是焊死的一对一（秤只能喂称重、扫码只能走绑定链路），接一种新触发源 = 开发一条新通道。这正违反最高原则（通用性/全可配）。

### 1.2 直接诱因：两个做不到的触发场景

- **画面像素差分"虚拟按钮"**：操作员手遮画面标定区 → 触发结算。无 YOLO 类别可用（不是检测目标，是颜色变化）
- 可预见的下一批：**脚踏板 / USB 按钮盒**（都是 HID 键盘设备）、**外部系统调一个 URL 就触发**、**串口设备来一条自定义报文**、光电开关接 PLC 太贵想直接接串口……

### 1.3 能复用的资产（RFC 13 刚铺好的路）

| 已有能力 | 位置 | 与本 RFC 的关系 |
|---|---|---|
| **动作注册表**（bind_sn/switch_project/start·stop_detection/write_points/alarm/set_var） | `backend/services/plc/rule_actions.py` | **提升为通用注册表**，Trigger Hub 与 PLC 共用一套动作面 |
| 规则语义（when 条件/debounce/min_interval/动作序列） | RFC 13 `read_rules` schema | 触发规则 JSON 结构原样沿用，现场工程师学一遍两处通用 |
| 连接卡片 + 实时监视 + IO 日志的面板范式 | `views/MES/PlcPanel.vue` | 触发中心前端照此范式 |
| USB 键盘扫码枪 HID 监听 | v3.20 scanner 家族 | HID 按键触发源的监听先例 |
| 外设串口循环 | `external_device_protocols.py` | 串口报文触发源的线程模型先例 |
| mock 先例（mock PLC / mock_weight） | 各家族 | mock 触发源（API 注入）联调/CI 测试 |

**缺口**：像素差分取帧计算、HID 全局按键映射、通用 HTTP 触发端点、串口报文正则匹配、统一的"触发源实例"配置存储与管理面板。

---

## 二、目标与反目标

### 2.1 目标

| 目标 | 说明 |
|---|---|
| **零代码接入新触发方式** | 触发源类型、参数、条件、防抖、动作绑定全部软件内配置 |
| **触发源 × 动作全笛卡尔** | 任意触发源可以绑定动作注册表里的任意动作（结算/清除/起停/绑码/事件/报警/写 PLC…） |
| **动作面统一** | 把 RFC 13 的动作注册表提升为全局共享（`backend/services/triggers/actions.py`），PLC 规则与 Trigger Hub 引用同一套，插件注册一次两边可用 |
| **现场可联调** | 触发源实时状态、最近触发记录、手动试触发按钮、mock 注入 —— 不开代码就能通 |
| **错误隔离** | 任一触发源崩溃/设备拔线绝不拖垮检测主流程与主程序启动 |
| **既有 8 条通道不动** | 商业项目客户在用，成熟通道零改动零风险；Hub 只承接**新增**触发源类型，从此新触发一律进 Hub |

### 2.2 反目标（本 RFC 不做）

- ❌ **不收编重构既有 8 条通道**——扫码/称重/PLC/入站各自久经现场验证，强行统一是纯风险零收益。本 RFC 给它们的定位是"成熟专用通道"，Hub 是第 9 条起的"通用通道"
- ❌ 不做第二套视觉事件系统——YOLO 检测触发继续走 `events_config`/区域事件模式（那是检测状态机核心），Hub 只管**非检测信号**
- ❌ 不做视频分析算法（运动检测/越界侦测等 NVR 功能）——像素差分触发只做"标定区均值差分"这一种最简可靠形态
- ❌ 不做工控机 GPIO/DIO 直连（各品牌驱动碎片化；这类需求引导走 PLC/Modbus IO 模块，RFC 13 已覆盖）

---

## 三、方案空间穷举（核心节：每一行都是配置项，不是代码）

> 判断"考虑了所有可能"的标准：任何一种"客户想用 X 方式触发 Y 动作"的诉求，都能在下面维度里组合出来。

### 3.1 维度一：触发源类型（source 注册表）

| type | 语义 | 参数（params JSON） | 首版 |
|---|---|---|---|
| `pixel_region` | 画面标定区像素差分（虚拟按钮/光电指示灯/屏幕信号灯） | 工位、区域 (x1,y1,x2,y2)、比较模式（`ref_diff` 参考帧差分 / `brightness` 亮度阈值 / `color_match` 目标色距离）、阈值、采样周期 ms、参考帧标定方式（手动一键标定/启动自动取） | ✅ |
| `hid_key` | HID 键盘类设备按键（脚踏板/USB 按钮盒/无线遥控器——本质都是键盘） | 监听设备过滤（VID/PID 可选，空=全局）、按键/组合键、长按判定 ms（区分短按长按） | ✅ |
| `http` | 外部系统调 URL 触发 | 触发 key（生成 `POST /api/v1/triggers/fire/{key}`）、共享密钥（可选）、IP 白名单（可选）、payload 变量提取（JSON path → 上下文变量） | ✅ |
| `serial_pattern` | 串口报文正则匹配（光电开关/继电器板/任意串口小设备） | 串口/波特率、行分隔符、正则 pattern、捕获组 → 上下文变量 | ✅ |
| `timer` | 定时/周期（与检测运行状态解耦的系统级定时） | interval 秒 / 每日 HH:MM / cron 式 | ✅ |
| `mock` | 联调/CI 测试注入 | 无（`POST /api/v1/triggers/{id}/mock-fire`） | ✅ |
| （插件注册） | 客户怪癖触发器（自定义硬件 SDK 等） | 插件自带 | hook 留口 |

**多实例并存**：同一类型可建多个实例（两个虚拟按钮、三个脚踏板各绑不同工位动作）。

### 3.2 维度二：触发条件与防抖（对齐 RFC 13 规则语义）

| 配置项 | 可选值 | 说明 |
|---|---|---|
| 边沿语义 | `rising`（进入触发态）/ `falling`（退出）/ `both` | pixel_region：遮挡=rising 移开=falling；hid_key：按下/松开 |
| `debounce_ms` | 信号稳定 N ms 才认 | 手遮画面抖动、按键抖动 |
| `min_interval_ms` | 两次触发最小间隔 | 防连击（对齐工具原型的"冷却时间"） |
| 生效窗口 | 时间段（班次内）/ 检测运行中才生效 / 始终 | 避免午休误触 |
| 工位过滤 | 绑定工位列表 | 动作默认落到绑定工位 |

### 3.3 维度三：动作（全局动作注册表 —— RFC 13 动作面提升）

把 `backend/services/plc/rule_actions.py` 的注册表**上移**为 `backend/services/triggers/actions.py`（PLC 包内保留薄引用，行为零变化），并补齐结算类动作：

| action | 语义 | 来源 |
|---|---|---|
| `bind_sn` / `switch_project` / `start_detection` / `stop_detection` / `write_points`(写 PLC) / `alarm` / `set_var` | 同 RFC 13 | 已有，上移 |
| `manual_settle` | 等价界面手动结算（per-item/custom_mix 手动结算 API 同款语义） | **新增**（虚拟按钮/脚踏板结算的主诉求） |
| `clear_reset` | 清除当前周期状态重来（等价换板/清零） | **新增** |
| `trigger_event` | 进 `_trigger_event` 事件中心（报警/语音/计数/Toast/插件 hook 全联动） | **新增**（信号型触发接入事件体系的正门） |
| `ack_alarm` | 消除在途报警（等价 USB 确认按钮语义） | **新增**（复用入站消警链路） |
| （插件 hook `trigger_action`） | 客户怪癖动作 | 留口，注册一次 PLC 规则同样可用 |

动作序列可组合、每步可延时、可条件守门（`only_if` 引用上下文变量）——JSON 结构与 RFC 13 `actions` 完全一致。

### 3.4 维度四：异常 / 降级策略（全部可配）

| 异常 | 可配策略 |
|---|---|
| 设备拔线（HID/串口） | 自动重连退避；断链报警级别（无/warn）；触发源卡片红标 |
| 像素源取不到帧（工位停流） | 静默挂起，恢复流后自动继续；可选报警 |
| 参考帧漂移（光照渐变导致 ref_diff 常触发） | 参考帧自动缓漂（EMA 更新，可关）；连续触发 N 次熔断 + 报警 |
| HTTP 未带密钥/IP 不在白名单 | 401/403 + 日志（默认内网免鉴权，对齐入站对接先例） |
| 动作执行失败 | 记录 + 可选报警；触发记录里标失败原因 |
| 触发源线程崩溃 | 自动重启 + 错误计数，**绝不影响主程序启动**（错误隔离底线） |

---

## 四、架构设计

```
backend/services/triggers/
├── actions.py            # ★ 全局动作注册表（自 plc/rule_actions.py 上移；PLC 薄引用保持兼容）
├── sources/              # 触发源类型注册表（对照 plc/drivers）
│   ├── base.py           # BaseTriggerSource: start/stop/snapshot（每实例一线程）
│   ├── pixel_region.py   # 帧快照差分（从 VideoSourceManager 帧缓存取快照，不挂推理循环）
│   ├── hid_key.py        # HID 按键监听（复用 v3.20 键盘扫码枪监听形态）
│   ├── http_source.py    # 无线程，注册 fire key 由 API 层调入
│   ├── serial_pattern.py # 串口循环 + 正则（对照 external_device_protocols）
│   ├── timer_source.py   # 定时
│   ├── mock.py           # 测试注入
│   └── __init__.py       # _REGISTRY + register_trigger_source(插件口)
├── engine.py             # 实例生命周期 + 条件/防抖判定 + 动作派发（复用队列化执行线程）
└── manager.py            # TriggerHubManager 单例：加载/热重载/状态/触发历史（无启用实例零开销）
```

**关键设计决策**：

1. **配置存储**：新表 `trigger_channels`（一行一触发源实例）：`name/type/enabled/params(JSON)/rules(JSON)/options(JSON)`——结构对照 `plc_connections`，纯新表 `create_all` 自建无迁移。
2. **动作执行队列化**：触发判定线程只 enqueue，动作在共享执行线程跑（对齐不变量 15；`manual_settle` 等要进 source 的动作经既有 API 路径，不直捅状态机内部）。
3. **pixel_region 不碰推理热路径**：按采样周期（默认 150ms）从帧缓存取快照算区域均值，CPU 开销微不足道；工位停流时挂起。
4. **API**：`/api/v1/triggers/*`（实例 CRUD、`/fire/{key}` HTTP 触发入口、`/{id}/mock-fire`、`/{id}/test` 手动试触发、`/{id}/history` 最近触发记录、`/{id}/calibrate` 像素参考帧标定、导入导出）。`router_manifest.py` 登记。
5. **前端**：系统设置页新增「触发中心」tab（`views/Settings` 内分区或独立 `TriggerPanel.vue`，复用 PlcPanel 卡片+实时状态+日志范式）：实例卡片（类型徽标/启停/最近触发时间/触发计数）+ 规则编辑（条件下拉+动作拼装）+ 像素按钮**画面标定器**（点开工位画面框选区域，一键存参考帧）+ 触发历史。
6. **方案模板库**：内置预设（「虚拟按钮→手动结算」「脚踏板→结算」「HTTP→开始检测」「定时→清零」），载入改参数即用。
7. **工位清理**：实例绑定工位挂 `on_channel_removed`（不变量 4）。
8. **插件 hook**：`register_trigger_source(type, cls)` + `trigger_action`（动作，与 PLC 共享）+ 触发历史进 `plugin_data` 可查。

### 原型工具"虚拟按钮"实例 = 一份配置（示意）

```json
{
  "name": "1号工位虚拟结算按钮", "type": "pixel_region", "enabled": true,
  "params": {"channel": 0, "region": [1180, 620, 1260, 700],
              "mode": "ref_diff", "threshold": 40, "sample_ms": 150},
  "rules": [{
    "when": [{"trigger": "rising"}],
    "debounce_ms": 300, "min_interval_ms": 3000,
    "actions": [
      {"do": "manual_settle", "channel": 0},
      {"do": "trigger_event", "event": "event3", "message": "虚拟按钮结算"}
    ]
  }]
}
```

客户换成脚踏板：`type` 改 `hid_key`、params 换按键——**动作一个字不改**。

---

## 五、实施分期与工作量估算

| 期 | 内容 | 估算 |
|---|---|---|
| M1 | 表 + 动作注册表上移（PLC 兼容引用 + 回归）+ sources 框架 + `pixel_region`/`http`/`timer`/`mock` + engine/manager + 新增 4 个动作 | 3~4 天 |
| M2 | API `/triggers/*` + 前端触发中心面板（CRUD/规则/画面标定器/触发历史/模板库） | 2~3 天 |
| M3 | `hid_key` + `serial_pattern`（监听形态复用既有先例） | 2 天 |
| M4 | 测试（mock 源端到端 + pixel 差分单测 + CI e2e UI + 可见浏览器 UAT）、文档（操作手册 + debug-triggers skill + 本 RFC 收尾） | 2 天 |

M1 的动作注册表上移是全局收益：此后插件注册一个动作，PLC 规则与触发中心同时可用。

### 实施状态（2026-08-09 · M1~M4 全部完成）

| 层 | 落点 | 状态 |
|---|---|---|
| ORM | `backend/models/trigger_models.py`（`trigger_channels` 表，create_all 自建无迁移） | ✅ |
| 全局动作注册表 | `backend/services/triggers/actions.py`（RFC 13 六件套上移 + 新增 manual_settle/clear_reset/trigger_event/ack_alarm；`plc/rule_actions.py` 改薄兼容垫片，PLC 39 项回归零改动全绿） | ✅ |
| 触发源框架 | `backend/services/triggers/sources/`（base + 注册表惰性 import + `register_trigger_source` 插件口） | ✅ |
| 六种触发源 | `pixel_region`（ref_diff/brightness/color_match + EMA 缓漂 + 一键标定）/ `hid_key`（pynput 全局钩子 + 长按判定 + last_seen_key 探针）/ `http`（key 路由 + 密钥/IP 白名单 + payload 变量提取）/ `serial_pattern`（正则 + 命名捕获组 + 退避重连）/ `timer`（interval + daily）/ `mock` | ✅ |
| 引擎 | `backend/services/triggers/engine.py`（电平防抖/边沿首锁存/脉冲、min_interval、生效窗口 only_detecting + 时间段跨零点、触发历史、鸭子接口对齐 PLC 引擎） | ✅ |
| 管理器 | `backend/services/triggers/manager.py`（单例、热重载、共享动作线程、`on_channel_removed` 挂入 channel_manager、main.py 启停接线） | ✅ |
| API | `backend/api/triggers.py` + `schemas/triggers.py`（CRUD/启停/live/history/logs/test/mock-fire/mock-level/calibrate/fire/{key}/导入导出），`router_manifest.py` 登记 `/api/v1/triggers` | ✅ |
| 模板库 | `backend/services/triggers/presets.py`（虚拟按钮→结算 / 脚踏板→结算 / HTTP→开工 / 串口→事件 / 每日清零 / mock 演示） | ✅ |
| 前端 | `frontend/src/api/triggers.js` + `views/Settings/TriggerPanel.vue`（卡片+实时状态+历史+日志+试触发+mock 注入+**画面标定器**拖拽框选），系统设置页「触发中心」tab；轮询默认 trigger_status/trigger_live + 日志条数 trigger 三处登记 | ✅ |
| 依赖 | `pynput>=1.7.6` 进 requirements（惰性 import，缺库只影响 hid_key） | ✅ |
| 测试 | `tests/test_trigger_hub.py` 13 项（参数校验/注册表共享/脉冲/边沿+min_interval/试触发/HTTP 鉴权/导入导出）+ `tests/e2e_browser/test_trigger_panel.py` 3 项 CI e2e + `tests/manual_uat/trigger_hub_uat.py` 可见浏览器 UAT 11 步全过 | ✅ |
| 文档 | 操作手册 §4.7「触发中心」+ `debug-triggers` skill + api-sync 35 组登记 + AGENTS.md 路由/触发表 | ✅ |

第六节「计数组合判定表」已于 2026-08-09 落地（见第六节实施记录）。

---

## 六、配套项：计数组合判定表（纯视觉判型，独立小项）✅ 已落地 2026-08-09

原型工具的另一缺口"三区计数 (5,5,4)→判 4 缸"本质是**结算判定器**不是触发源，不进 Hub，但同属"全可配"补账：

- 落点：~~counters_config~~ → **`pipeline_config.combo_table`**（实施修正：counters_config 是 `[{name,value}]` 数组放不下嵌套块；结算相关配置历来归 pipeline_config）。schema：`{enabled, labels: [判型标签], rows: [{counts: [5,5,4], verdict: "OK"|"NG", tag: "4缸-含挺柱"}]}`，未命中一律 NG（防呆对齐工具设计）
- 结算时若配了判定表 → 查表改判 + `tag` 进事件 reason（随导出/MES 推送/数据页 NG 原因）+ `combo_verdict.last_tag` 经 `/detection/results` 透出
- **v3.48.x 补充（2026-08-10）**：`count_mode: "steps"(默认零差异) | "positional"` —— positional 为位置去重计数（IoU 追踪，同位置返工不重计，一比一复刻对标工具算法3「IoU 目标跟踪」，`tracking: {iou, ema_alpha, min_consecutive, pending_ttl, perish_ticks, idle_reset_ticks}` 全参数可配）。引擎 `backend/api/source_combo_positional.py`，喂帧挂检测出口、结算读数+清池；实时计数经 `combo_verdict.positional_counts` 透出。与外部工具真视频逐周期对照 8 组 6 组全等（余 3 数字各差 1 = 实时采样 vs 离线逐帧的临界帧噪声）。⚠️ 前端 LogicConfigTab 开关入口待补
- 无外部机型信号的客户由此获得"纯视觉判型"；有 PLC/扫码信号的客户继续走 switch_project 正向方案（RFC 13），两者可叠加交叉校验

**实施记录（范围 = 检测模式专用）**：

| 触点 | 文件 |
|---|---|
| 解析归一化 `_parse_combo_table` + apply | `backend/api/source_project_config_apply.py` |
| 结算查表 `_apply_combo_verdict` + 三处让位 | `backend/api/source_settlement_mixin.py` |
| 状态初始化 / reset_stats 清 tag | `source_state_init.py` / `source.py` |
| 结果透出 `combo_verdict` | `backend/api/source_routes.py` |
| 前端编辑卡 + 兜底/序列化 | `LogicConfigTab.vue` / `Project/index.vue` |
| 测试 | `tests/test_combo_table.py`（10 用例）+ `tests/e2e_browser/test_logic_config_tab.py` + `tests/manual_uat/combo_table_uat.py` |

**关键设计决策——判型标签三处"让位"**（期望数量因机型而异，重复出现是合法累计）：
1. 豁免 accept_once 周期内去重（前端在检测模式给全部步骤自动置 accept_once=true，不让位则计数永远到不了 2——UAT 实测挖出）
2. 豁免"重复超次"实时NG（`_maybe_instant_ng_detection_duplicate`）
3. 豁免检测模式"首步重现结算"（判型标签重现≠新周期信号；周期边界建议用非判型标签界定）
结算时判型标签的缺步/重复判定同样让位查表；**非判型标签（流程步骤）照旧判**，流程 NG 不查表。

---

## 七、不变量自检

- 不变量 1（`/api/v1/` 前缀）✅；不变量 4（`on_channel_removed` 清理）✅ 第四节第 7 条
- 不变量 8（迁移版本化）✅ 纯新表 `trigger_channels` 由 `create_all` 自建，无 ALTER
- 不变量 15（热路径不阻塞）✅ 触发判定与动作执行全队列化；pixel 取帧走快照不进推理循环
- 不变量 16（报警归四类优先级）✅ `alarm`/`trigger_event` 走报警与事件体系，不直写串口
- 错误隔离底线：触发源全部独立线程 + 异常自动重启，缺依赖/崩溃不影响主程序启动 ✅
- 动作上移兼容：`plc/rule_actions.py` 保留同名引用，RFC 13 既有配置与测试零改动 ✅

---

**本文件最后更新**：2026-08-09（v1.1 —— M1~M4 + 配套项 combo_table 全部落地；combo 落点修正为 pipeline_config，实施记录见第六节）
**预期演进**：新触发源类型一律进 sources 注册表，不再开专用通道；新客户范式沉淀进 presets.py
