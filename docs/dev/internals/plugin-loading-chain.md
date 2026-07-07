# 插件加载链深潜

> **类型**：explanation（internals 深潜）
> **本文不讲**：插件 manifest 字段全集（→ `docs/plugin-system/design/`）；打包签名流程（→ `scripts/plugin/`）
> **与代码冲突时**：以 `backend/plugin_system/` 与 `docs/plugin-system/` 为准。
> **读码依据**：`registry.py` / `hook_dispatch.py` / `plugins.py` / 前端 bootstrap

Disclaimer：给维护者看；不是规范；版本间会变。

## 后端生命周期

```
1. uvicorn 启动 → PluginManager 读 plugin_registrations (active)
2. 验签（RSA + manifest）→ 解压到 plugin_dir
3. import 插件模块 register_plugin(app, registry, license_payload, host)
4. registry.routes.include_router → /api/v1/plugins/{customer_code}/...
5. registry.hooks.register → 按 (hook_type, phase, when) 分桶
6. 运行时：fire_plugin_hook(type, ctx) → HooksRegistry.fire → swallow 异常
7. 停用/卸载：registry.clear() + 路由不可再达
```

**错误隔离底线**：任一步失败 → 该插件 disable，**主程序必须能启动**（AGENTS.md 不变量）。

## Hook 类型（主程序触发点）

统一入口：`backend/plugin_system/hook_dispatch.py` → `fire_plugin_hook`。

| hook_type | 典型触发位置 | returnable 字段 |
|---|---|---|
| `pre_cycle_end` | 周期结算写库前 | `override_result`, `extra_counters` |
| `cycle_end` | 周期结束后 | （观察为主） |
| `session_end` | session 结束 | |
| `event_fire` | `_trigger_event` 内 | `suppress_alarm` |
| `step_change` | 步骤切换 | `warn_threshold_violated`, `warn_label` |
| `workpiece_flow_*` | RFC11 协调器 | 见 RETURNABLE_HOOK_FIELDS |
| `startup` / `shutdown` | 插件加载/卸载 | |

完整白名单：`hook_dispatch.RETURNABLE_HOOK_FIELDS`（改字段 = 升 SDK 版本 + 契约测试）。

## PluginHost 主动 API（插件 → 主程序）

声明在 `manifest.capabilities`，未声明则 `PluginRuntimeError`：

| API | capability | 用途 |
|---|---|---|
| `query_session/cycle/step/workpiece` | （查询无需 cap） | 稳定 dict 快照，勿直接 ORM |
| `trigger_alarm` | `runtime.alarm_trigger` | 走 AlarmRouter |
| `trigger_event` | `runtime.event_trigger` | 借事件响应面，不结算周期 |
| `mes_push` | `runtime.mes_push` | Gateway 外推 |
| `read/write_system_config` | 读/写各自 cap | 写键须 `plugin_<cc>_` 前缀 |
| `write_plugin_step_field` | `runtime.step_field_write` | step_records.plugin_data JSON |

**内部 API 勿依赖**：未在上表与 `docs/plugin-system` 契约中的 public 方法，未来可改。

## 前端加载链（v3.15.4+ 三级兜底）

```
main.js bootstrap
  → waitBackendReady() 轮询 /plugins/active/manifest（最多 90s）
  → GET manifest → usePluginLoader
       1. import(blob:...) 
       2. fallback data:URL
       3. fallback 后端 http URL（file:// 打包场景必需）
  → pluginThemeStore.apply() + TjSlot 槽位渲染
```

**顺序铁律**：必须先等后端就绪再拉清单（v3.15.5）；否则 file:// 冷启动 Network Error 一次性放弃。

客户端错误：`POST /api/v1/plugins/client-log`（客户机无 F12）。

## 概念 → 文件 → 入口

| 概念 | 文件 | 入口 |
|---|---|---|
| 注册中心 | `plugin_system/registry.py` | `PluginRegistry`, `PluginHost` |
| hook 触发 | `plugin_system/hook_dispatch.py` | `fire_plugin_hook` |
| HTTP 管理 | `backend/api/plugins.py` | `/plugins/*` |
| 前端加载 | `composables/usePluginLoader.js` | `loadPluginModule` |
| bootstrap | `frontend/src/main.js` | 插件初始化段 |

## 改前必读（AGENTS.md 不变量 #12/#13）

- 禁止只依赖 blob 动态 import（打包 file:// 会静默失败）
- Vue 组件须 `markRaw` 静态 import 标记
- 完整设计：`docs/plugin-system/design/06_tier3_fullstack.md`
