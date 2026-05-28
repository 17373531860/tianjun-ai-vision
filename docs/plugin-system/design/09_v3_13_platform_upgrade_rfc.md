# 09 — 插件平台 v3.13 升级 RFC（M1 / M2 / M3）

> 适用版本：基于 v3.12.x 主线 + `feat/plugin-config` 已落地的 G1 注册中心
> 本文目的：把这次客户需求引出的"插件能力缺口"梳理清楚，给 M1（业务流程双向打通）/ M2（UI 扩展平台化）/ M3（配置扩展系统）三个里程碑划下设计骨架，并为主程序原生的"工位组"概念预留插件 hook 接口。
>
> 阅读前置：`design/00_overview.md`（三档边界）、`design/06_tier3_fullstack.md`（Tier3 现状）。
>
> 本 RFC 的写作约束：
> - 只描述"应该怎么设计"，不引入虚构的现有机制
> - 对应代码改动的范围、签名、契约写到能直接立项编码的颗粒度
> - 不写实现细节（行级伪代码），让后续每个 milestone 拆出独立任务卡再下钻

---

## 一、本次需求引出的能力缺口

这次客户提了 4 个需求：

| # | 需求 | 归位 | 依赖能力 |
|---|---|---|---|
| 1 | 步骤耗时三档显示（OK / 警告 / NG），警告档黄色 | 客户专属插件 | M2 视图覆盖 + M3 步骤配置扩展 |
| 2 | 隐藏步骤级 NG 红色显示，只在 cycle 级判定红 | 客户专属插件 | M2 主题增强（视图节点显隐） |
| 3 | 双工位 UI 重排成"左右各占一半 + 共用底栏" | 客户专属插件 | M2 视图覆盖（局部 layout 替换） |
| 4 | 双工位（多工位）联动结算：A 站 NG 同步 B 站 NG | 主程序原生 + 留 hook | 工位组（ChannelGroup）原生概念 + M1 hook 留口 |

**结论**：需求 1/2/3 走客户专属插件即可解决，但都依赖 M1+M2+M3 中尚未落地的能力面；需求 4 是基础设施，进主程序，并给 M1 留对应 hook 让后续工位组的客户级变种走插件。

**当前插件平台缺的能力**（按这次需求倒推）：

1. **业务事件 hook 不够 + 单向**：现有 `cycle_end / pre_cycle_end / session_end / box_complete` 只能"通知"，不能"决策"（如 hook 内决定是否阻断写库、是否改写结算结果）；缺 `step_change / event_fire / cycle_start` 等更细粒度事件。
2. **插件不能主动调主程序能力**：`PluginHost` 现在只暴露只读 `query_*` facade，缺 `trigger_alarm / mes_push / write_step_record / cluster_broadcast` 等"主动 API"。
3. **UI 扩展只有理论 Tier1/Tier2 边界，没有落地实现**：`design/04_tier1_theme.md` / `05_tier2_ui.md` 已写设计，但前端 bundle 加载、视图 slot、主题 slot 的运行时机制还没接进 Vue 主程序。
4. **配置扩展系统是空的**：插件想给 `Project.steps_config` 加自家字段（如"步骤警告耗时阈值"）、想给 `SystemConfig` 占命名空间、想注入 Settings/Project 页 tab —— 三件事都没有正式接入面。

这份 RFC 就是补这四块。

---

## 二、设计目标 + 反目标

### 2.1 设计目标

| 目标 | 说明 |
|---|---|
| **能落地这单客户的全部 3 条插件需求** | 不主程序硬编码、不 monkey-patch |
| **保留 G1 已建立的契约不破坏** | manifest schema / customer_code 隔离 / hook fail-fast / `_UnimplementedRegistry` 三档 / `query_*` facade 全保留 |
| **错误隔离底线不动摇** | 插件挂不让主程序起不来；插件 UI 崩不让主程序 UI 崩 |
| **签名与版本契约延伸** | 新增能力面纳入 `main_version_min/max` 校验；plugin SDK 升版与新 hook 同步 |
| **给工位组留 hook，不在 RFC 内做工位组实现** | 工位组进度由主程序里程碑跑，本 RFC 只规定接口 |

### 2.2 反目标

| 反目标 | 原因 |
|---|---|
| ❌ 不在本 RFC 落工位组业务逻辑 | 工位组属主程序原生改造，独立 RFC（编号 10 待写） |
| ❌ 不允许插件覆盖主程序核心视图（Monitor 主面板 / Project 主表 / Data 主表） | 维持 design/00 §二的"档位 2 不能改主视图功能"边界 |
| ❌ 不引入插件双向同步 / 协同 | 单 active 插件契约不变 |
| ❌ 不做插件市场 / 远程更新 | 离线工厂场景不适用 |

---

## 三、现状基线（v3.13 已落地的能力）

| 能力 | 现状 | 文件 |
|---|---|---|
| `PluginRegistry` 三大子注册中心：routes / hooks / tables | ✅ 已实现 | `backend/plugin_system/registry.py` |
| `_UnimplementedRegistry`（export_templates / export_fields / realtime_triggers） | ✅ Fail-fast，调用即抛 `PluginNotImplementedError` | 同上 |
| Hook 接入点：`cycle_end / pre_cycle_end / session_end / box_complete` | ✅ 通过 `fire_plugin_hook(...)` 统一接入 | `backend/plugin_system/hook_dispatch.py` + `source_session_lifecycle_mixin.py` + `cluster_collector.py` |
| `PluginHost.query_session/cycle/step/workpiece` 只读 facade | ✅ 已实现，返回 dict 快照 | `backend/plugin_system/registry.py` |
| `main_version_min/max` 校验 | ✅ 加载阶段强制拦截 | `backend/plugin_system/version_check.py` + `manager.py` |
| 主程序版本号唯一来源 | ✅ 从 `electron/package.json` 读 | `backend/_version.py` |
| 插件 ORM 表命名空间隔离（`p_{customer_code}_*`） | ✅ TablesRegistry 校验 | 同上 |
| 主程序 UI（Vue）侧的插件加载机制 | ❌ 未实现 | — |
| 主题包 / 视图覆盖 / 配置扩展的运行时接入面 | ❌ 未实现 | — |
| Hook 双向（returnable）契约 | ❌ 仅单向 swallow | — |
| `PluginHost` 主动 API（trigger_*/write_*/broadcast_*） | ❌ 未实现 | — |

**这就是这份 RFC 的起点。**

---

## 四、M1：业务流程双向打通

### 4.1 目标

让插件不仅"知道发生了什么"，还能：
- 在关键决策点**返回值影响主程序后续动作**（如阻断、改写、补字段）
- **主动触发主程序能力**（推 MES、触发报警、写步骤记录、广播到其它工位）
- 监听更细粒度的业务事件（不只 cycle 级，还要 step 级 / event 级 / scan 级）

### 4.2 子任务 M1.1：扩展 hook 接入点

新增 6 个 hook 类型（在 `fire_plugin_hook` 主程序侧接入）：

| 新 hook | 触发位置 | `ctx` 关键字段（契约） | 用途场景 |
|---|---|---|---|
| `cycle_start` | `source_session_lifecycle_mixin.py` 中 cycle 起步落库后 | `cycle_id / cycle_uuid / session_id / channel_id / project_id / start_time` | 客户想在新周期开始时打 marker / 重置插件内部计数 |
| `step_change` | `source_session_lifecycle_mixin.py: record_step` 写库后 | `step_record_id / step_id / step_label / cycle_id / channel_id / duration / confidence / is_valid` | 需求 1 的"步骤耗时三档"判定就在这里挂 |
| `event_fire` | `source_event_trigger_mixin.py: _trigger_event` 末尾 | `event_id / event_name / event_kind (OK/NG/WARN/CUSTOM) / cycle_id / channel_id / payload` | 客户级事件后处理（语音覆盖、Toast 抑制） |
| `scan_received` | `services/scanner.py: ScannerService._on_data_received` 末尾 | `scanner_device_id / channel_id / raw / parsed / scan_kind` | 客户对接异构扫码逻辑 |
| `source_status_change` | ✅ M1.1 末项（2026-05-28）：`source_lifecycle_mixin.py` 5 个公共方法（pause / resume / standby / resume_inference / stop）+ `source_capture_loop_mixin.py` 3 处异常中断点（video_ended / reopen_failed / recover_failed）。设计选择：散落 30+ 处 `is_running` / `is_detecting` 写点（init / 各 source_type 启动）不动，仅在客户真正关心的 lifecycle API 接 hook；helper `_fire_source_status_change` 自带 dedup（无变化静默防同状态再赋值虚报）；备用 contextmanager `_track_status_change` 留作未来扩展。 | `channel_id / before:{is_running,is_detecting} / after:{is_running,is_detecting} / reason / source_type` | 客户做"工位开始/停止通知 MES" / "源断流自动报警" / "多工位 standby 同步关闭硬件" |
| `project_activated` | `api/projects.py: activate_project` 落库后 | `project_id / channel_id / activated_at` | 客户级配置二次校验 / 激活时下发外部系统 |

**接入约束**（与 G1 一致，不允许在主程序业务代码里插条件判断）：
- 每处新增的 `fire_plugin_hook(...)` 调用必须在主流程**关键副作用之后**触发（即"事件已发生"，而不是"事件可能发生")
- `ctx` 所有字段名进入 plugin SDK 契约，改名 = 升 plugin SDK 主版本
- 配套写 `tests/plugin_system/test_new_hook_points_M1.py`，每个 hook 验证 ctx 字段集合 + 触发时机

### 4.3 子任务 M1.2：returnable hook 契约（单向→双向）

> **交付进度**：
> - **M1.2a 已落地**（2026-05-28）：`fire_plugin_hook` 升签名为返回 dict + 聚合机制 + 白名单常量表。**完全向后兼容**（旧 8 处调用点语句式调用零差异）
> - **M1.2b 已落地**（2026-05-28）：业务侧消费 `pre_cycle_end` 的 `override_result` / `extra_counters` + 消费 `step_change` 的 `warn_threshold_violated` / `warn_label`；消费逻辑抽到 `_resolve_pre_cycle_end_overrides` / `_resolve_step_change_warn` 两个纯函数（同 `source_session_lifecycle_mixin.py`），31 个新单测覆盖。
> - **M1.2c 已落地**（2026-05-28）：`event_fire.suppress_alarm` 全链路接通 — `_trigger_event` 尾部重排（events_log → _pending_ack → **event_fire hook** → resolve suppress → [alarm?] → router → _last_event_time）；抽 `_resolve_event_fire_suppress_alarm` 纯函数（严格 `is True` 而非 truthy 防误识）+ `_dispatch_event_alarm` 私有方法封装 alarm 触发；白名单 `event_fire = {"suppress_alarm"}`；**19 个基线测试**守护无插件场景字节级等价 + **27 个业务消费测试**覆盖纯函数 / 白名单 / 静态扫描 / e2e 真消费 / 安全侧默认 / 签名锁定。`pre_cycle_end.extra_counters` 当前仍仅日志，等 M3.3 (cycle.plugin_data JSON 字段) 解锁后才落 DB。

现有 `HooksRegistry.fire(...)` 把所有 handler 返回值收进 `results` 列表但不让主程序使用。M1 起，**部分 hook 类型**（明确白名单）允许返回结构化"决策反馈"。

**白名单**（首批，谨慎引入）：

| hook 类型 | 决策返回字段 | 主程序行为 |
|---|---|---|
| `pre_cycle_end` | `{"override_result": "OK" \| "NG" \| None}` | ✅ M1.2b：主程序写库时用插件给的 result 覆盖默认判定，`result_reason` 追加 `[plugin override: A → B]` 痕迹；MES / 周期性强制动作 / `cycle_end` hook ctx 全部走 override 后的 final 值 |
| `pre_cycle_end` | `{"extra_counters": {key: int}}` | ⚠️ M1.2b 阶段仅日志记录（cycle 表无 `plugin_data` JSON 字段）；M3.3 落字段后才写 DB |
| `step_change` | `{"warn_threshold_violated": bool, "warn_label": str}` | ✅ M1.2b：缓存到 VSM 实例 `_plugin_step_warn_cache[step_record_id]` 字典；`start_cycle` 时清空避免跨周期串污染；落 `StepRecord.plugin_data` 等 M3.3 解锁 |
| `event_fire` | `{"suppress_alarm": True \| False}` | ✅ M1.2c：`_trigger_event` 已重排，hook 在 alarm 之前 fire；严格 `is True` 才抑制（防 `1` / `"true"` 等 truthy 误识）；handler 抛错 / 无 active 插件 / 字段缺省都走"宁可误报警也不漏报警"安全侧默认 |

**契约设计**：

```python
# backend/plugin_system/hook_dispatch.py 升级签名
def fire_plugin_hook(
    hook_type: str,
    phase: str,
    when: str,
    ctx: Dict[str, Any],
) -> Dict[str, Any]:
    """新返回值: 合并所有 handler 返回的 dict, 后注册 (priority 大) 覆盖前注册.
    主程序业务代码自行决定是否消费; 不消费的 handler 返回 dict 字段也无效.
    """
```

**安全边界**：
- 任何 handler 返回非 dict / 异常 → 视为空返回，主流程继续走默认逻辑
- 合并冲突时，**priority 大的覆盖小的**（与 fire 的执行顺序一致）
- 决策字段必须在文档里枚举，不在白名单的字段一律忽略，**禁止隐式扩展**
- 主程序业务侧消费返回值的代码路径要单独压测（防止插件返回值导致主流程死循环）

### 4.4 子任务 M1.3：`PluginHost` 主动 API

在 `PluginHost` 加一组 `trigger_*` / `write_*` / `broadcast_*` 方法，让插件能"反向调主程序"。

> **交付状态**：
> - **M1.3a 已交付**（2026-05-28）— 4 个主动 API + 2 个 stub 全部落地，33/33 测试通过。
> - **M1.3b 待做** — `write_plugin_step_field`（依赖 M3.3）+ `broadcast_to_channel_group` / `query_channel_group` / `list_channel_groups`（依赖 RFC 10）。当前调用 stub 会抛 `PluginNotImplementedError` 带清晰里程碑提示。

| API | 作用 | 安全约束 | 状态 |
|---|---|---|---|
| `trigger_alarm(channel_id, event_type, reason)` | 触发主程序 AlarmRouter 的报警 | 需声明 `runtime.alarm_trigger`；`event_type` 是主程序 `alarm.config` 已配置的字符串（"event1"/"event2" 等） | ✅ M1.3a |
| `mes_push(event_type, payload, channel_id=None)` | 走主程序 MES Gateway 推送 | 需声明 `runtime.mes_push`；`event_type` 必须 `plugin_<customer_code>_` 前缀；`payload` 必须是 dict | ✅ M1.3a |
| `read_system_config(key)` | 读 SystemConfig KV 值 | **不**需声明（只读无副作用）；高频不写 audit | ✅ M1.3a |
| `write_system_config(key, value, description=None)` | upsert SystemConfig KV | 需声明 `runtime.system_config_write`；`key` 必须 `plugin_<customer_code>_` 前缀 | ✅ M1.3a |
| `write_plugin_step_field(step_record_id, key, value)` | JSON 合并写入 `step_records.plugin_data` 字段；不删其它 key（含其他客户的 key） | 需声明 `runtime.step_field_write`；`key` 必须 `plugin_<customer_code>_` 前缀；`value` 必须可 JSON 序列化（lambda / 自定义类直接被拒） | ✅ M3.3（2026-05-28） |
| `broadcast_to_channel_group(group_id, message)` | 给工位组内其它通道发广播 | 只 active 工位组成员才生效 | ⏸️ stub → M1.3b（依赖 RFC 10） |

**M1.3a 实现要点**（已落地）：
- 安全模型四层防护：capabilities 声明 + 命名空间隔离 + audit log 落库 + 错误隔离
- `_require_capability` / `_require_plugin_namespace` / `_audit_log` 三个内部辅助，复用度高
- 新增 `PluginRuntimeError`（RuntimeError 子类），与 `PluginNotImplementedError` 区分；前者是"做了但你越权"，后者是"还没做"
- audit log 走独立 SessionLocal session，audit 失败不拖垮主动 API
- 主动 API 内部异常一律 swallow + 返 False + audit 落 failed，**不上抛**（避免拖垮 hook handler 链）
- `manifest.capabilities` 词汇表扩展 3 个新枚举：`runtime.alarm_trigger` / `runtime.mes_push` / `runtime.system_config_write`（见 `design/01_manifest_schema.md` §3.4.1）
- 测试覆盖：33 个新单测分 8 个维度（异常类 / capabilities / 命名空间 / 实际效果 / audit / 错误隔离 / stub / 向后兼容）

**M1.3b 进度**：
- ✅ `write_plugin_step_field` — M3.3 已落地（v3.13, 2026-05-28），见 §6.4
- ⏸ `broadcast_to_channel_group` / `query_channel_group` / `list_channel_groups` ← RFC 10 工位组主程序原生落地后

### 4.5 验收标准（M1）

| # | 验证 |
|---|---|
| AC-M1-1 | 新 6 个 hook 在主程序对应位置触发，ctx 字段集合与文档一致；用 `pytest tests/plugin_system/test_new_hook_points_M1.py` 跑通 |
| AC-M1-2 | `pre_cycle_end` 返回 `{"override_result": "NG"}` 后，DB 中该 cycle 的 `is_good=False`、`result_reason` 含 `[plugin override: OK → NG]` 痕迹 ✅ M1.2b |
| AC-M1-3 | `event_fire` 返回 `{"suppress_alarm": True}` 后，AlarmManager 不联动 ✅ M1.2c（`test_e2e_suppress_alarm_true_alarm_router_not_called` 端到端覆盖） |
| AC-M1-4 | `PluginHost.trigger_alarm` 触发后，AlarmRouter 真实输出（用 mock serial 验证） ✅ M1.3a |
| AC-M1-5 | `PluginHost.mes_push` 在 manifest 没声明 capabilities 时被拒绝（抛 `PluginRuntimeError`） ✅ M1.3a |
| AC-M1-6 | 任何 returnable hook handler 抛异常时，主流程 fallback 到默认逻辑、不抛回 |

---

## 五、M2：UI 扩展平台化

### 5.1 目标

把 `design/04_tier1_theme.md` 和 `design/05_tier2_ui.md` 的设计落到运行时：让客户插件能：
- **覆盖主程序的局部视图节点**（不替换主视图，但允许在预留 slot 注入组件）
- **声明显隐**（隐藏某段 UI、改某段顺序）
- **加自家页面**（注入新路由，挂在主程序导航树某个节点下）
- **加自家配置面板 tab**（注入 Settings / Project 页的子 tab）

### 5.2 子任务 M2.1：插件前端 bundle 加载机制

**约束**：
- 插件前端打包**必须**输出 `dist/plugin.umd.js` + `dist/plugin.css` 两个文件
- 主程序 Vite 在 dev / prod 启动时去 `installed/{customer_code}/frontend/` 目录扫描这两个文件
- 通过 `<script>` 动态注入；插件 UMD 的全局名固定为 `window.__tianjun_plugin_{customer_code}`
- 加载时机：**主程序首屏渲染前**（License 验证后、router 初始化前）

**UMD 入口契约**：

```typescript
// 插件 UMD 默认导出对象
interface TianjunPlugin {
  customerCode: string;
  pluginVersion: string;
  mainVersionMin: string;
  mainVersionMax?: string;

  // 注册项
  routes?: PluginRouteDef[];        // 新路由
  slotOverrides?: PluginSlotDef[];  // 视图 slot 覆盖
  themeOverrides?: ThemeDef;         // 主题包覆盖
  uiHidden?: string[];               // 显隐控制（按"slot key"隐藏）
  settingsTabs?: PluginTabDef[];    // 注入 Settings 页 tab
  projectTabs?: PluginTabDef[];      // 注入 Project 页 tab

  // 生命周期
  onActivated?: (ctx: PluginActivationCtx) => void;
  onDeactivated?: () => void;
}
```

**安全约束**：
- 插件 UMD 加载失败 → 主程序 UI 继续渲染，仅在右下角弹一个非阻塞 Toast（"客户插件 UI 加载失败，仅核心功能可用"）
- 插件 UMD 在加载阶段抛异常 → catch 后降级，进 audit log
- 主程序 Vue 实例 / Pinia / Router **不暴露**给插件，仅通过 `PluginHost.frontend` 提供受控 API

### 5.3 子任务 M2.2：主程序 UI Slot 化改造

主程序 Vue 视图必须**先把"可被插件覆盖的位置"显式声明为 slot**。这次客户需求驱动以下 slot 落地：

| slot key | 位置 | 默认内容 | 客户用例 |
|---|---|---|---|
| `monitor.step-cell.duration` | 步骤表格"耗时"列单元格 | 灰色文字 | 需求 1：换三档颜色显示 |
| `monitor.step-cell.status` | 步骤表格"状态"列单元格 | 红绿点 | 需求 2：隐藏步骤级红色 |
| `monitor.layout.body` | Monitor 主区域容器 | 默认 layout | 需求 3：双工位左右各占一半 |
| `monitor.layout.footer` | Monitor 底部状态栏 | 默认底栏 | 需求 3：双工位共用底栏 |
| `cycle-result.indicator` | cycle 级判定红绿块 | 默认绿/红 | （保留作业空间） |
| `settings.tab` | Settings 页 tab 容器 | 内置 tabs | 客户级配置面板 |
| `project.tab` | Project 页 tab 容器 | 内置 tabs | 客户级 project 字段 |

**Slot 契约**：
- 主程序所有 slot 用 `<TjSlot name="...">默认内容</TjSlot>` 包裹
- 插件覆盖时通过 `slotOverrides: [{ name: "monitor.step-cell.duration", component: MyDurationCell }]` 声明
- 主程序运行时 `TjSlot` 组件检查是否有插件注册了同名覆盖 → 有则渲染插件组件 + 把默认 slot 的 props 透传 → 没有则渲染默认内容
- **插件组件必须是函数式或 `defineComponent` 返回值**，不能用 SFC 编译时依赖
- 插件组件 props 字段集合是契约，主程序不能随意删字段（升 plugin SDK 主版本才允许）

**显隐控制（uiHidden）**：

```typescript
plugin.uiHidden = [
  'navbar.menu.report',  // 隐藏 Navbar 的 Report 菜单项
  'monitor.step-cell.status',  // 隐藏步骤状态列（满足需求 2 兜底，不写覆盖也能藏）
];
```

主程序 `TjSlot` 拿到 `uiHidden` 列表后，匹配的 slot 不渲染（连默认内容也不渲染）。

### 5.4 子任务 M2.3：插件路由与 tab 注入

**新路由**：
- 插件可以声明 `routes: [{ path: '/plugin/foo', component: PluginFooView, navParent: 'mes' }]`
- 主程序 router 在初始化时扫描所有插件 routes，把 path 强制 prefix 成 `/plugin/{customer_code}/...`，避免冲突
- `navParent` 决定挂到主程序导航树哪个节点下（`monitor` / `mes` / `settings` / `data` / `top-level`）

**Tab 注入**：
- `settingsTabs: [{ key: 'plugin-foo', label: '客户专属', component: FooSettingTab }]`
- 主程序 Settings 页用 `<TjSlot name="settings.tab">` 接收，按 `label` 渲染
- 同理 `projectTabs` 注入 Project 编辑页

### 5.5 子任务 M2.4：主题增强（slot 化主题）

`design/04_tier1_theme.md` 已敲 CSS 变量 + 文案 + 菜单显隐三类。M2 把它接进来：
- CSS 变量覆盖通过插件 UMD 在 `onActivated` 钩子里 `document.documentElement.style.setProperty(...)` 设置
- 文案覆盖通过 `i18n.mergeLocaleMessage(locale, messages)`
- 菜单显隐统一走 `uiHidden`（主题包也用同一个 slot 体系）

### 5.6 验收标准（M2）

| # | 验证 |
|---|---|
| AC-M2-1 | 插件 UMD 加载失败时，主程序首屏照常渲染（用 mock 一个抛错的 plugin.umd.js 验证） |
| AC-M2-2 | 插件 `slotOverrides` 注入的组件能拿到 props，且默认内容被替换 |
| AC-M2-3 | `uiHidden: ['monitor.step-cell.status']` 后，DOM 里该 cell 完全不渲染 |
| AC-M2-4 | 插件路由 `/plugin/foo` 自动 prefix 成 `/plugin/{customer_code}/foo`，访问 `/plugin/foo` 直接 404 |
| AC-M2-5 | 双工位场景下，`monitor.layout.body` 被插件覆盖成左右半屏 layout，且底栏 slot 单独覆盖一次 |
| AC-M2-6 | 插件组件抛 Vue runtime error，主程序 ErrorBoundary 拦截，仅这一个 slot 显示降级文本，其它 UI 不受影响 |

---

## 六、M3：配置扩展系统

### 6.1 目标

让插件能"加配置字段、加配置面板、读写客户级配置"，而不污染主程序 Project / SystemConfig schema。

三件事：
1. **Project JSON 字段扩展点**：插件能给 `Project.steps_config[i]` / `pipeline_config` 等加自家字段
2. **SystemConfig 命名空间**：插件读写 `SystemConfig` 时强制走 `plugin_<customer_code>_*` 前缀
3. **客户配置面板**：插件能在 Project / Settings 页注入 tab，读写自家字段

### 6.2 子任务 M3.1：Project JSON 字段扩展点 ✅（已交付 2026-05-28）

**交付状态**:
- ✅ 透传守护测试：覆盖 7 个 JSON 字段下 `plugin_data` 子键经 POST/GET/PUT/`_apply_pipeline_config` 不丢
- ✅ 新端点 `PUT /api/v1/projects/{id}/plugin-data`：精准 PATCH `<scope>.plugin_data.<customer_code>` 子树，浅合并、不动其它客户/主程序字段
- ✅ scope 白名单 + customer_code 字符校验（只允许 alnum/_/-）+ list/dict 字段 index 互斥校验
- ✅ 激活项目时 `_sync_project_config_to_channels` 自动透传新 plugin_data 到运行时 VSM
- ✅ 15 个新单测，回归 319/319 通过

**问题**：客户需求 1 要在每个步骤上配"警告耗时阈值"。直接改 `Project.steps_config[i]` schema 让所有客户都看到这字段，是污染。

**方案**：

- Project 的 7 个 JSON 字段（`pipeline_config / steps_config / events_config / counters_config / alarm_config / detection_config / data_config`）每个加一个**保留键** `plugin_data: dict`
- 插件读写自家字段都走 `plugin_data.<customer_code>.<field>` 路径
- 主程序业务代码忽略 `plugin_data`，前端默认 UI 也不展示
- 插件的配置面板 tab 自己读写 `plugin_data`，渲染自家 UI

**示例（客户 hb-foo 加步骤警告阈值）**：

```json
{
  "steps_config": [
    {
      "step_id": 1,
      "step_label": "step_1",
      "max_frames": 100,
      "plugin_data": {
        "hb_foo": { "warn_duration_ms": 800 }
      }
    }
  ]
}
```

插件在前端通过 `projectTab` 渲染"步骤警告阈值"输入；插件后端在 `step_change` hook 里读 `plugin_data.hb_foo.warn_duration_ms` 决定要不要返回 `warn_threshold_violated=True`。

**约束**：
- `plugin_data` 字段不进 `modify-project-config` skill 的全链路对齐检查（视为客户专属）
- 主程序 API 序列化时**保留** `plugin_data` 透传，但不做 schema 校验
- 客户卸载插件时，`plugin_data.<customer_code>` 可选清理（走 plugin_uninstall hook）

### 6.3 子任务 M3.2：SystemConfig 命名空间 ✅（已交付 2026-05-28）

主程序 `system_configs` 表已经是 KV 结构。M3 起：
- ✅ 软规则：插件写入的 key 必须 `plugin_<customer_code>_*` 前缀（M1.3a 已落地）
- ✅ `PluginHost.write_system_config(key, value)` 强制校验前缀（不符则抛 `PluginRuntimeError`）（M1.3a 已落地）
- ✅ 主程序读自家配置时不动这层（默认行为）
- ✅ 卸载插件时可选清理（v3.13 M3.2）：
  * `DELETE /api/v1/plugins/{customer_code}` 加 `purge_data: bool = False` query param
  * 默认 `false` 保留业务数据（与 v3.10 起的"卸载不动数据"语义兼容）
  * `true` 时清理两层：(a) `system_configs` 中 `plugin_<cc>_*` 全部 KV（用 ESCAPE 防 SQL LIKE `_` 通配符误伤）；(b) Project 7 个 JSON 字段下的 `plugin_data.<cc>` 子键（dict 字段 + list 字段每项都扫）
  * **不**清理 `step_records.plugin_data`（已结束周期视为业务历史；真要清手动 SQL）
  * audit log 记录清理统计（`system_configs=N, projects=M`）

### 6.4 子任务 M3.3：StepRecord 加 `plugin_data` 字段 ✅（已交付 2026-05-28）

需求 1 的"步骤警告"要把"是否触发警告 + 警告标签"落库（前端通过 query 接口拿到），但加专属字段会污染 `step_records` schema。

**方案**：
- ✅ `step_records.plugin_data: Column(JSON, nullable=True)`（`backend/models/models.py`）+ `migrate_database()` 加 `("step_records", "plugin_data", "JSON")` 探针式 ALTER TABLE
- ✅ M1.3b 的 `PluginHost.write_plugin_step_field(step_record_id, key, value)` 真实现：
  * 需声明 `runtime.step_field_write` capability（新增枚举，见 manifest schema §3.4）
  * key 命名空间隔离 `plugin_<customer_code>_*`（与 `write_system_config` 一致）
  * value 必须可 JSON 序列化（lambda / 自定义类拒绝并 audit rejected，不抛）
  * step_record_id 不存在 → 返 False + audit
  * **JSON 合并**写入（浅拷 + 整字段重赋）避免 SQLAlchemy mutation 检测不到导致不 UPDATE
- ✅ 主程序 `sessions_export.py` 默认 CSV 列硬拼，**不**暴露 `plugin_data`（设计目标满足）；自定义导出模板可显式取 `{step.plugin_data.<plugin_namespaced_key>}`
- ✅ 18 个新单测覆盖 capability / 命名空间 / JSON 校验 / 合并语义 / audit / 错误隔离 / 签名锁定

### 6.5 子任务 M3.4：配置面板 tab 注入

M2 已经做了 `projectTabs / settingsTabs` 的前端注入；M3 把"读写后端 plugin_data"的 helper 配上：
- `PluginHost.frontend.useProjectPluginData(projectId)` 返回当前 Project 的 `plugin_data.<customer_code>` 子树（响应式）
- `PluginHost.frontend.saveProjectPluginData(projectId, data)` 提交到 `PUT /api/v1/projects/{id}/plugin-data`（新端点）
- 新端点限定只能写 `plugin_data.<active_customer_code>` 子树，不能动其它字段

### 6.6 验收标准（M3）

| # | 验证 |
|---|---|
| AC-M3-1 | 插件 `PUT /projects/{id}/plugin-data` 写 `plugin_data.hb_foo.warn_duration_ms`，DB 中 `steps_config` 透传保留 |
| AC-M3-2 | 主程序 GET 项目详情时 `plugin_data` 字段透明返回，主程序 UI 不展示也不报错 |
| AC-M3-3 | `PluginHost.write_system_config('foo')` 没有 `plugin_<cc>_` 前缀时抛 `PluginRuntimeError` |
| AC-M3-4 | 卸载插件后 `system_configs` 中 `plugin_<cc>_*` 全部清理，`plugin_data.<cc>` 子树清理 |
| AC-M3-5 | `step_records.plugin_data` 字段写入后，自定义导出模板能引用 `{step.plugin_data.hb_foo.warn_label}` |

---

## 七、工位组（主程序原生）— hook 留口设计

> 工位组（ChannelGroup）作为主程序原生概念**独立 RFC**（编号 10 待写）。本节只规定它对插件平台的**留口接口**。

### 7.1 主程序原生改造概要（仅纲要）

- 加表 `channel_groups`：`id / name / member_channel_ids JSON / settle_strategy enum`
- `pipeline_config.channel_group_id` 关联当前通道所在工位组
- `settle_strategy = "synchronized"` 时：组内任一通道 cycle 结算后，广播给其它成员同步结算（满足需求 4）
- `settle_strategy = "independent"`（默认）：保持现有独立结算行为

### 7.2 给插件留的 hook（M1 hook 集合的扩展）

| 新 hook | 触发位置 | ctx 关键字段 | 用途 |
|---|---|---|---|
| `channel_group_settle_start` | 工位组同步结算 broadcast 之前 | `group_id / trigger_channel_id / trigger_cycle_id / member_channel_ids` | 客户在结算前补字段 / 发通知 |
| `channel_group_settle_done` | 所有成员结算落库后 | `group_id / cycle_ids[] / aggregated_result` | 客户做组级 MES 推送 / 报警 |

**Returnable 部分**：
- `channel_group_settle_start` 接受 `{"override_strategy": "synchronized" | "independent"}` 临时改变本次结算策略（罕见场景；默认不开放，需要 manifest 显式声明 `capabilities.override_channel_group_strategy`）

### 7.3 给插件留的主动 API

- `PluginHost.broadcast_to_channel_group(group_id, message)` 已经在 M1.3 列出，工位组实现时正式接入
- `PluginHost.query_channel_group(group_id)` 查工位组配置 + 当前成员状态

### 7.4 工位组与插件的边界

| 谁负责 | 范围 |
|---|---|
| **主程序** | 工位组 CRUD、`settle_strategy` 业务逻辑、广播分发、跨通道事务一致性 |
| **客户专属插件** | 工位组级别的客户定制（如"组内全 OK 才推 MES"、"组内 NG 计入特殊 counter"），通过 hook + 主动 API 实现 |

---

## 八、兼容性与迁移

### 8.1 G1 现有契约保护

| 契约 | M1/M2/M3 影响 | 处理 |
|---|---|---|
| `manifest.main_version_min` 校验 | 新接入面要求新 manifest 版本 → 现有插件 `main_version_min < 3.13.0` 时 fail-fast | 老 demo 插件改 manifest 即可 |
| `_UnimplementedRegistry` 的 export_templates / export_fields / realtime_triggers | 不变，继续 fail-fast | F7/F8/F9 独立里程碑落地，不进本 RFC |
| `query_*` facade 字段集合 | 不删字段，新增字段 = 加字段 | M3 加的 `plugin_data` 在 `query_step` 输出里追加字段 |
| `fire_plugin_hook` 返回 None | 升级签名为返回 dict | 老调用方收到 dict 也兼容（dict 实现 `if x is None` 不成立但不影响业务） |
| 主程序 Vue 视图渲染 | M2 改 slot 化时，凡是没插件的通道 / 客户，slot 渲染默认内容，**视觉零差异** | 现有自动化测试 + 截图对比验证 |

### 8.2 老插件的迁移指南

| 老用法 | 新建议 | 必须迁移？ |
|---|---|---|
| `PluginHost.get_db_session()` 直接玩主程序 ORM | 改用 `query_*` + 主动 API | 不必须，但建议（避免 ORM 改字段时插件挂） |
| 直接 `import backend.api.alarm.AlarmManager` | 用 `PluginHost.trigger_alarm(...)` | M1 上线后强制（之前的 import 从 v3.13 起会被 fail-fast 检查器拦截） |
| 在主程序业务代码里 monkey-patch 函数 | 改用 hook + returnable hook | 必须（v3.13 起 audit log 会扫 monkey-patch 痕迹） |

### 8.3 主程序代码侧迁移点

- `source_session_lifecycle_mixin.py` 中 cycle 起步 / 结束 / session 结束的写库点：增加 hook fire 调用（M1.1 列表）
- `source_session_lifecycle_mixin.py: record_step` 写库后：增加 `step_change` hook
- `source_event_trigger_mixin.py: _trigger_event`：增加 `event_fire` hook
- `services/scanner.py: ScannerService._on_data_received`：增加 `scan_received` hook
- `source_lifecycle_mixin.py` 5 个 lifecycle 公共方法 + `source_capture_loop_mixin.py` 3 处异常中断点：增加 `source_status_change` hook（M1.1 末项已交付）
- `api/projects.py: activate_project`：增加 `project_activated` hook
- `models/models.py: StepRecord`：加 `plugin_data: JSON` 字段 + ALTER TABLE 迁移
- `models/models.py: Project.steps_config / pipeline_config / ...`：默认结构里加 `plugin_data: {}` 占位（不强制）
- `frontend/` 需加 `<TjSlot>` 组件 + 主视图改造为 slot 化（M2.2 列表）

---

## 九、测试策略

### 9.1 测试层级

| 层级 | 工具 | 范围 |
|---|---|---|
| 单元测试 | `pytest` | hook ctx 字段集合、`PluginHost` 主动 API 安全约束、`plugin_data` 命名空间校验 |
| 集成测试 | `pytest-bdd` | 插件加载 → 注册 hook → fire → 返回值合并 → 主程序消费 完整链路 |
| 前端单测 | `vitest` | `<TjSlot>` 渲染逻辑 / uiHidden 列表 / 插件 UMD 加载失败降级 |
| 前端 E2E | `playwright` | 双工位 layout 替换、步骤耗时三档颜色、Settings tab 注入 |
| 冒烟 | 后端启动 + Electron 启动 | 没有 active 插件时主程序行为零差异 |

### 9.2 关键测试用例

- **空插件场景**：DB 中无 active 插件 → 所有 `fire_plugin_hook` 静默返回；所有 `<TjSlot>` 渲染默认内容；主程序行为与无插件时位级一致
- **失败插件场景**：插件后端 register 抛异常 / 前端 UMD 抛异常 / hook handler 抛异常 → 主程序业务流程不中断
- **决策返回值场景**：returnable hook 返回字段冲突时 priority 大的胜出
- **命名空间越权场景**：插件试图写 `plugin_<其它客户>_*` key → 抛 `PluginRuntimeError`
- **版本不兼容场景**：M3 新加 `plugin_data` 字段后，老插件 `main_version_min < 3.13.0` 时被 `manager.load_active` 拒绝

---

## 十、风险与权衡

| 风险 | 缓解 |
|---|---|
| Returnable hook 让插件能改主程序业务结果 → 出问题难溯源 | `PluginAuditLog` 记录所有决策返回值；返回字段集合白名单封闭 |
| 前端 slot 化改造工作量大 → 容易把现有视图改坏 | 分批落 slot（先 monitor 主区域，再 settings/project tab，再细粒度 cell），每批独立 PR + 回归 |
| `plugin_data` 在 Project JSON 散落 → 插件升级 / 卸载时清理逻辑复杂 | 维护一份 `plugin_data` 路径清单（每个插件 manifest 声明），卸载时按清单清理 |
| 插件 UMD 全局名 `__tianjun_plugin_{cc}` 可能被恶意脚本利用 | 主程序加载 UMD 后立即 `delete window.__tianjun_plugin_*`，不长期暴露全局名 |
| 同一 customer_code 装两次（历史残留 + 新版） | `manager.load_active` 已经只取一条 `is_active=True`；安装侧用 customer_code 唯一索引 |

---

## 十一、实施里程碑与排期

> 排期是经验估算，不绑死。每个里程碑结束做一次客户场景演练。

| 里程碑 | 工作量（人天） | 交付 |
|---|---|---|
| **M1.1** 6 个新 hook 接入点 | 3 | ✅ 已交付（5 个首批 hook + `source_status_change` 末项, 2026-05-28）。代码 + `test_new_hook_points_M1.py` + `test_source_status_change_hook_M1_1.py` |
| **M1.2** Returnable hook 契约 | 4 | `fire_plugin_hook` 签名升级 + 4 类返回字段消费方接入 + 单测 |
| **M1.3** PluginHost 主动 API（5 个方法） | 5 | API 实现 + audit log + capabilities 校验 + 单测 |
| **M2.1** 前端 UMD 加载机制 | 4 | Vite 集成 + UMD 接口契约 + 失败降级 |
| **M2.2** Slot 化主视图（首批 7 个 slot） | 6 | `<TjSlot>` 组件 + 主视图改造 + 截图回归 |
| **M2.3** 插件路由与 tab 注入 | 3 | router prefix 强制 + Settings/Project tab slot |
| **M2.4** 主题增强 | 2 | i18n merge / CSS var 注入 / uiHidden 联动 |
| **M3.1** Project plugin_data 透传 | 3 | API 层透传 + 前端不展示 + 卸载清理 |
| **M3.2** SystemConfig 命名空间 | 2 | 写校验 + 卸载清理 |
| **M3.3** StepRecord plugin_data 字段 | 2 | ALTER TABLE 迁移 + write_plugin_step_field 接入 |
| **M3.4** 配置面板 tab 注入与 helper | 3 | 前端 helper + 后端新端点 |
| **工位组留口** | 2 | hook 名 + ctx 契约写入 + 占位（不接入业务，等工位组主 RFC） |

合计约 **39 人天**。客户当前需求最少需要 M1.1 + M1.2（部分）+ M2.1 + M2.2 + M3.1 + M3.3 才能闭环（约 22 人天）。

---

## 十二、开放问题

1. **Vue 版本与插件 SFC 兼容**：插件 UMD 用主程序的 Vue 实例还是自带？倾向"主程序提供 vue / pinia 全局，插件 UMD 通过 `external` 取" — 待 PoC 验证。
2. **`plugin_data` 在多客户先后装时的迁移**：客户 A 装完卸载，再装客户 B，A 留在 Project JSON 里的 `plugin_data.a` 该自动清还是保留？倾向"卸载时按 manifest 路径清单清理，不留残值"。
3. **Returnable hook 的优先级竞争**：当前规定"priority 大覆盖"，但插件作者可能误用——是否要在 audit log 里加"被覆盖的字段"提示？倾向加。
4. **工位组与 per_item 模式的交互**：per_item 模式下结算节奏和工位组 synchronized 策略冲突时怎么办？目前先**互斥**（`pipeline_config` 强制校验：进 channel_group 时禁用 per_item），等真出客户场景再放开。
5. **前端 slot 命名规范**：`monitor.step-cell.duration` 这种点分命名是否要 freeze？倾向 freeze，加进 plugin SDK 契约文档。

---

## 附录 A：本 RFC 不做的事

| 项 | 原因 |
|---|---|
| ❌ 实际写工位组业务代码 | 独立 RFC 编号 10 |
| ❌ F7/F8/F9（导出模板 / 导出字段 / 实时规则的插件接入） | 已规划独立里程碑，不在 M1/M2/M3 |
| ❌ 插件市场 / 远程更新 / 多 active 插件 | 反目标，长期不做 |
| ❌ 给老 demo 插件做自动迁移工具 | 老 demo 改 manifest 即可，工作量不值 |

---

---

## 附录 B：`PluginHost.frontend` 前端 host 对象定义

> RFC 主体 §5.4 / §6 多处引用了 `PluginHost.frontend.useProjectPluginData(...)` 这类调用，本附录补齐定义。

### B.1 前端 host 是什么

后端的 `PluginHost`（`backend/plugin_system/registry.py`）是 Python 类，**不会**穿越到前端。前端 host 是**主程序前端**在加载插件 UMD 之前实例化的、**与后端 host 同名同感觉**的 JS 对象：

```typescript
// 主程序在 frontend/src/plugin/host.ts (新文件) 创建
export const tianjunPluginHost = {
  customerCode: '<from-license>',
  pluginVersion: '<from-manifest>',
  mainVersion: '<from-package.json>',

  // 主程序内部 axios 实例的受控代理（不直接暴露 axios）
  api: {
    get<T>(path: string, params?: any): Promise<T>,
    post<T>(path: string, body?: any): Promise<T>,
    put<T>(path: string, body?: any): Promise<T>,
    delete<T>(path: string): Promise<T>,
  },

  // 受控 i18n 引擎
  i18n: {
    mergeLocaleMessage(locale: string, messages: Record<string, any>): void,
    t(key: string, params?: any): string,
  },

  // 受控 router（仅添加路由，不暴露主程序路由表）
  router: {
    addRoute(route: PluginRouteDef): void,
  },

  // 项目 plugin_data 读写 helper（与 M3.1 / M3.4 联动）
  useProjectPluginData(projectId: number): {
    data: Ref<Record<string, any>>,      // 当前 customer_code 子树
    save: (newData: Record<string, any>) => Promise<void>,
    loading: Ref<boolean>,
    error: Ref<string | null>,
  },

  // SystemConfig plugin_<cc>_* 命名空间读写
  systemConfig: {
    read(key: string): Promise<any>,    // 自动 prefix plugin_<cc>_
    write(key: string, value: any): Promise<void>,
  },

  // 触发主程序级 Toast（不暴露 ElMessage）
  toast: {
    info(msg: string): void,
    warn(msg: string): void,
    error(msg: string): void,
  },
};
```

### B.2 挂载方式

- 主程序 `frontend/src/main.js` 在 `createApp(App)` 之前实例化 `tianjunPluginHost`
- 通过 `window.__tianjun_host = tianjunPluginHost`（**仅供插件 UMD 读取一次**，加载完立即 `delete window.__tianjun_host` 防长期暴露）
- 插件 UMD 入口约定：
  ```js
  (function(host) {
    // 插件代码: host 即 tianjunPluginHost
  })(window.__tianjun_host);
  ```

### B.3 安全约束

- `tianjunPluginHost.api` 内部用主程序的 axios 实例，**强制限定** `baseURL = /api/v1`，插件不能访问主程序内部端点（路径不在 `/api/v1/plugins/{cc}/*` 外的写操作会被前端拦截弹错）
- `tianjunPluginHost.router.addRoute` 内部强制 `path` prefix 成 `/plugin/{cc}/`，与 §5.4 一致
- `useProjectPluginData` 内部调 `PUT /api/v1/projects/{id}/plugin-data`（M3.4 新端点），**只能写**自家 `plugin_data.<customer_code>` 子树

### B.4 类型声明（plugin SDK 提供）

`@tianjun/plugin-sdk` 这个 npm 包（**新建**）提供 `TianjunPluginHost` 的 TypeScript 类型定义，插件开发者可 `npm install --save-dev @tianjun/plugin-sdk` 拿类型提示。SDK 主版本号与主程序大版本绑定（如 `@tianjun/plugin-sdk@3.13.x` 对应主程序 `v3.13.x`）。

---

## 附录 C：里程碑依赖图

```
┌──────────────────────────────────────────────────────────┐
│                    RFC 09 + RFC 10 依赖图                  │
└──────────────────────────────────────────────────────────┘

    [M1.1 新 hook 接入点]              ← 起点，无依赖
           │
           ├──→ [M1.2 Returnable hook 契约]
           │              │
           │              ├──→ RFC10 (CG.6 工位组 hook 衔接)
           │              │
           │              └──→ [M2 客户级 UI 用 returnable 改 cycle 结果]
           │
           └──→ [M1.3 PluginHost 主动 API]
                          │
                          ├──→ [M3.3 StepRecord plugin_data 字段]
                          │            │  (依赖关系：M1.3.write_plugin_step_field 没字段写不了)
                          │            ↓
                          │     [必须先做 M3.3 才能做 M1.3 的 write_plugin_step_field]
                          │
                          └──→ RFC10 (CG.7 工位组主动 API)


    [M2.1 UMD 加载机制]                ← 与 M1 平行，无依赖
           │
           ├──→ [M2.2 Slot 化主视图]
           │              │
           │              └──→ [M2.3 插件路由 + tab 注入]
           │                             │
           │                             └──→ [M2.4 配置面板 helper]
           │                                          ↑
           │                                          │ 依赖
           │                                          │
           └──→ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─[M3.1 Project plugin_data 透传]
                                                     ↑
                                                     │
    [M3.1 Project plugin_data 透传]                   │
           │                                          │
           ├──→ [M3.2 SystemConfig 命名空间]           │
           │                                          │
           ├──→ [M3.3 StepRecord plugin_data]         │
           │                                          │
           └──→ [M3.4 配置面板新端点 + 前端 helper]────┘
                          │
                          └──→ [M2.4 配置面板 tab 注入]

────────────────────────────────────────────────────────────

RFC 10 (工位组) 依赖路径:

    [M1.1 hook 接入点] + [M1.3 PluginHost 主动 API]
                  │
                  └──→ [CG.1 channel_groups 表]
                              │
                              └──→ [CG.2 Coordinator 单线程协调器]
                                            │
                                            └──→ [CG.3 4 种 settle_strategy]
                                                          │
                                                          └──→ [CG.4 VSM end_cycle 接入]
                                                                          │
                                                                          ├──→ [CG.6 hook 衔接 (依赖 M1.1)]
                                                                          ├──→ [CG.7 PluginHost 工位组 API (依赖 M1.3)]
                                                                          ├──→ [CG.5 API + 前端 Settings tab]
                                                                          ├──→ [CG.8 cluster 互操作]
                                                                          └──→ [CG.9 测试 + CG.10 文档]
```

**关键依赖（不能反向）**：

| 后置任务 | 前置必须完成 |
|---|---|
| M1.3 的 `write_plugin_step_field` | M3.3（StepRecord plugin_data 字段） |
| M2.4 的配置面板 helper | M3.4（新端点）+ M3.1（plugin_data 透传） |
| RFC 10 全部 | RFC 09 的 M1.1 + M1.3 + M3.3 |

**可并行**：M2.1 与 M1 完全并行；M3.1/M3.2/M3.3 互不依赖可并行做。

---

## 附录 D：客户需求 → AC 反向追溯表

| 客户需求 | 通过哪些 AC 验收 | 闭环 RFC |
|---|---|---|
| **#1 步骤耗时三档显示（OK/警告/NG）** | AC-M1-1（step_change hook 触发）+ AC-M3-1（plugin_data 写入步骤阈值）+ AC-M3-5（导出能引用 warn_label）+ AC-M2-2（slot 覆盖 step-cell.duration） | RFC 09 |
| **#2 隐藏步骤级 NG 红色，只 cycle 级红** | AC-M2-3（uiHidden 隐藏 step-cell.status）+ AC-M1-3（event_fire 返回 suppress_alarm 阻断步骤级 alarm） | RFC 09 |
| **#3 双工位左右半屏 + 共用底栏** | AC-M2-5（layout slot 双工位覆盖）+ AC-M2-1（UMD 加载失败兜底） | RFC 09 |
| **#4 双工位 A NG → B NG 联动** | AC-CG-1（synchronized_any_ng 触发）+ AC-CG-2（group_settled_with 字段）+ AC-CG-5（超时 fallback）+ AC-CG-10（零差异回归） | RFC 10 |

**测试演练时**：按这张表反查，每个客户需求一定有 ≥2 个 AC 串起来验证；客户需求 #4 因为是主程序原生，AC 单独编号 CG-*。

---

## 附录 E：M2 Slot 化的"前端文件级"定位表

| Slot key | 前端文件 | 大致行号区间 | 现有 DOM 形态 | 改造工作量 |
|---|---|---|---|---|
| `monitor.step-cell.duration` | `frontend/src/views/Monitor/index.vue` | 行 941 附近（"已耗时 + 当前进度"块）+ 行 1772-1778（durations 数据源） | `<span>{{ duration }}</span>` 风格 | 中（需识别 5 种 duration: stepDurations / lastStepDurations / avgStepDurations / cycleSumStepDurations 等，slot 要把全部 props 透传） |
| `monitor.step-cell.status` | `frontend/src/views/Monitor/index.vue` | 行 2352 附近（颜色判定逻辑） | 红/绿/灰 indicator | 中（颜色逻辑硬编码在 computed，需先抽 props 再 slot 化） |
| `monitor.layout.body` | `frontend/src/views/Monitor/index.vue` | 行 527-941 主区域（含视频区 + 步骤表 + 实时状态） | flex layout 单列 | 大（双工位场景下 layout 完全不同，要把"通道列表 + 主区域"作为整块 slot） |
| `monitor.layout.footer` | `frontend/src/views/Monitor/index.vue` | 末尾 toast / 状态区（具体位置需 PR 时勘察） | absolute 底部 toast | 中 |
| `cycle-result.indicator` | `frontend/src/views/Monitor/index.vue` | 行 172 附近（cycle 级 toast 弹窗） | px-4 rounded toast | 小（已有清晰边界） |
| `settings.tab` | `frontend/src/views/Settings/index.vue` | 行 5 的 `<el-tabs>` 内 / 行 1449（"插件管理" tab pane 之后追加 slot） | el-tab-pane | 小（el-tabs 天然支持插槽） |
| `project.tab` | `frontend/src/views/Project/index.vue` | 文件 2925 行，需找 `<el-tabs>` 位置（PR 前再勘察） | el-tab-pane | 小 |

**每个 slot 的 PR 模板**：
1. 在对应 .vue 中识别 props 边界（哪些数据要传给插件组件）
2. 把现有 DOM 包成 `<TjSlot :name="..." :props="...">默认内容</TjSlot>`
3. 默认插槽内容保持现有行为，**不加任何 if 判断**
4. 跑视觉回归（无插件场景下截图与改前完全一致）
5. 用 mock plugin 注入覆盖，跑 vitest 验证 props 透传完整

---

## 附录 F：风险表补 4 条（与 §十 合并）

| 风险 | 缓解 |
|---|---|
| **Returnable hook 让插件改 cycle 结算结果 → 合规/审计风险**（部分客户合规要求结算结果不可被外部改） | manifest 加 `capabilities.returnable_hooks_allowed` 显式声明位；客户级配置开关 `system_configs.plugin_returnable_hooks_enabled` 全局拒绝；`PluginAuditLog` 记录每次决策返回值与覆盖前/后字段 |
| **`PluginHost.mes_push` 同步阻塞拖慢主流程** | API 内部用异步队列（与 `services/mes_hooks.py` 已有的 mes-hook-worker 线程同模式）；插件调用立即返回，实际推送走后台 |
| **插件 UMD 在 dev 模式 HMR 时重复挂载** | UMD 加载机制内部维护一个"已加载 customer_code 集合"，已存在则先调插件 `onDeactivated`、解绑路由 / slot / i18n，再重新加载 |
| **Slot 化改造后无插件场景视觉零差异靠什么保证** | 引入 `tests/visual_regression/`（playwright + 截图基线），每个 slot 改造 PR 必须挂"无插件场景截图 = 改前截图（像素级允许 ≤0.1% 差异）"作为 CI gate |

---

## 附录 G：测试金字塔的文件级估算

| 层级 | 新增/改动文件 | 估算行数 | 关键用例 |
|---|---|---|---|
| **单元测试（pytest）** | `tests/plugin_system/test_new_hook_points_M1.py`（新） | ~300 | 6 个新 hook 各 1 个 ctx 字段集合校验测试 |
| 单元测试 | `tests/plugin_system/test_returnable_hook.py`（新） | ~250 | 决策字段冲突时 priority 大覆盖小 / 异常 fallback |
| 单元测试 | `tests/plugin_system/test_active_apis.py`（新） | ~400 | 5 个主动 API 的安全约束（capabilities 校验 / 命名空间前缀） |
| 单元测试 | `tests/plugin_system/test_plugin_data_namespace.py`（新） | ~200 | Project / SystemConfig / StepRecord 三处 plugin_data 的越权拒绝 |
| **集成测试（pytest-bdd）** | `tests/features/plugin_hook_decision.feature`（新） | ~150 | pre_cycle_end 改写 result → DB 验证 |
| 集成测试 | `tests/features/plugin_active_api.feature`（新） | ~120 | mes_push / trigger_alarm 真实链路 |
| **前端单测（vitest）** | `tests/frontend/TjSlot.test.ts`（新） | ~200 | Slot 渲染 / uiHidden / 默认 fallback |
| 前端单测 | `tests/frontend/PluginUmdLoader.test.ts`（新） | ~250 | UMD 加载失败 / HMR 重复挂载 / 加载顺序 |
| **前端 E2E（playwright）** | `tests/e2e/plugin_step_cell_override.spec.ts`（新） | ~150 | 客户需求 #1 三档颜色验证 |
| 前端 E2E | `tests/e2e/plugin_dual_workstation_layout.spec.ts`（新） | ~200 | 客户需求 #3 双工位左右半屏验证 |
| 前端 E2E | `tests/e2e/plugin_hidden_step_status.spec.ts`（新） | ~100 | 客户需求 #2 步骤红色隐藏验证 |
| **视觉回归（playwright + 截图）** | `tests/visual_regression/slot_baseline/*.png`（新基线） | — | 每个 slot 改造 PR 提交无插件场景基线截图 |
| **fake plugin（仓库内 fixture）** | `plugins-examples/test-fixture/`（新） | ~500 | 提供一个"测试用客户插件"，被上述 E2E 测试 import |
| **回归** | 现有 `tests/plugin_system/*`（修改） | 微调 ~100 | 验证 G1 老契约不破 |

合计新增/改动 ~3200 行测试代码。其中 fake plugin 是关键基础设施（不写则所有 E2E 没真实插件可用），优先级最高。

---

**本文件最后更新**：2026-05-28（M1+M2+M3 设计骨架初稿 + 附录 B-G 补丁）
**作者**：项目主作者 + AI agents（基于 v3.12.x 主线 + G1 已落地能力）
**状态**：草案，待客户场景演练后定稿
**关联文档**：`docs/plugin-system/design/10_channel_group_rfc.md`（工位组主程序原生 RFC，承接客户需求 4）

