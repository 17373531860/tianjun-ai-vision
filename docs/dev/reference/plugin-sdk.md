# 插件 SDK 能力面速查

> **类型**：reference（契约摘要；完整设计在 `docs/plugin-system/`）
> **本文不讲**：插件打包/签名/安装 CLI（→ `scripts/plugin/README.md`）
> **与代码冲突时**：以 `backend/plugin_system/registry.py` + `hook_dispatch.py` 为准。

插件域**唯一详细文档**：`docs/plugin-system/README.md`。本节只给主程序维护者速查边界。

## Hook 类型（fire_plugin_hook）

| hook_type | 触发时机 | returnable（白名单字段） |
|---|---|---|
| `pre_cycle_end` | 周期写库前 | `override_result`, `extra_counters` |
| `cycle_end` | 周期结束后 | — |
| `session_end` | session 结束 | — |
| `event_fire` | 事件响应链 | `suppress_alarm` |
| `step_change` | 步骤切换 | `warn_threshold_violated`, `warn_label` |
| `workpiece_flow_enter` | RFC11 工件入流水线 | — |
| `workpiece_flow_station_done` | 工位完成 | — |
| `workpiece_flow_completed` | 流水线完成 | `override_final_result` |
| `workpiece_flow_timeout` | 流水线超时 | `override_timeout_action` |
| `workpiece_flow_short_circuit` | 短路 | — |
| `daily_report_before_send` | 短信日报每 scope 发送前 (v3.46) | `override_params`, `override_phone_numbers`, `skip_send` |
| `startup` / `shutdown` | 插件启停 | — |

注册：`registry.hooks.register(hook_type, handler, phase=, when=pre|post, priority=)`  
白名单源码：`backend/plugin_system/hook_dispatch.py` → `RETURNABLE_HOOK_FIELDS`

## PluginHost 主动 API（需 manifest.capabilities）

| 方法 | capability | 说明 |
|---|---|---|
| `query_session(id)` | — | dict 快照，勿持 ORM |
| `query_cycle(id)` | — | 同上 |
| `query_step(id)` | — | 同上 |
| `query_workpiece(id)` | — | 同上 |
| `get_db_session()` | — | **遗留**，新插件禁用 |
| `trigger_alarm(ch, event_type, reason)` | `runtime.alarm_trigger` | AlarmRouter |
| `trigger_event(ch, event_id, reason)` | `runtime.event_trigger` | 不结算周期 |
| `mes_push(event_type, payload, channel_id)` | `runtime.mes_push` | Gateway |
| `read_system_config(key)` | `runtime.config_read` | KV 读 |
| `write_system_config(key, value, desc)` | `runtime.config_write` | 键须 `plugin_<customer_code>_` 前缀 |
| `write_plugin_step_field(step_id, key, value)` | `runtime.step_field_write` | plugin_data JSON 合并写 |

## 插件路由

- 挂载前缀：`/api/v1/plugins/{customer_code}/{subpath}/`
- 登记：`registry.routes.include_router(router, subpath, tags=...)`

## 内部 API 遮蔽（Rails :nodoc: 等价）

以下**不在上表**的 backend 模块 public 函数/类，插件**不得** import 依赖：

- `backend/api/source.py` 内部状态变量与 mixin 私有方法
- `backend/services/mes_hooks.py` 除文档化 hook 回调外的 `_handle_*`
- 任意 `backend/models` ORM 类直接查询（用 PluginHost `query_*`）

未来重构不保证兼容；仅 manifest 声明的 capability + hook 载荷为契约。

## 错误隔离契约（KEP 生产就绪四问）

| 问题 | 答案 |
|---|---|
| 怎么开关 | DB `plugin_registrations.active` + 安装/卸载 API |
| 失败了主程序怎样 | hook 异常 swallow；加载失败 disable 该插件；主程序照常启动 |
| 运维怎么知道它在工作 | `/plugins/active/manifest` + `plugin_audit_log` + client-log |
| 升级老客户怎样 | manifest `main_version_min/max`；签名体系；数据在 `plugin_data` 命名空间 |

## 深潜

[internals/plugin-loading-chain.md](../internals/plugin-loading-chain.md)

## 代码位置

- `backend/plugin_system/registry.py` — PluginRegistry, PluginHost
- `backend/plugin_system/hook_dispatch.py` — fire_plugin_hook
- `backend/api/plugins.py` — HTTP 管理面
