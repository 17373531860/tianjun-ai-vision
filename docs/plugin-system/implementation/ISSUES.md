# v3.7 插件系统实施 Issue 清单

> 来源：`docs/plugin-system/design/06_tier3_fullstack.md` §九。  
> 状态：F14 已完成，F15 已验证为误报撤销。剩余 13 个实施项建议拆成独立 PR。

## 标签建议

| 标签 | 含义 |
|---|---|
| `plugin-v3.7` | 插件系统一期 |
| `plugin-backend` | 后端加载器 / hook / API |
| `plugin-frontend` | 前端 loader / Layout / Settings |
| `plugin-db` | ORM 表 / migration |
| `risk-high` | 影响检测主链路 |
| `test-required` | 必须带自动化测试 |

## Issue 列表

### F1 拆分 `_handle_cycle_end` 为 8 个 phase

- 标签：`plugin-v3.7`, `plugin-backend`, `risk-high`, `test-required`
- 目标：把 cycle_end 主流程拆成可插 hook 的阶段：`persist`、`post_cycle`、`realtime_export`、`realtime_rule`、`cluster_aggregate`、`mes_gateway_push`、`cleanup`、`notify_done`
- 验收：
  - 原有 cycle_end 集成测试通过
  - 每个 phase 前后都能记录 hook context
  - hook 抛异常主流程继续

### F2 实装 `session_end` hook 触发点

- 标签：`plugin-v3.7`, `plugin-backend`, `test-required`
- 目标：session 结束时触发插件 hook，并与导出实时规则复用同一上下文
- 验收：session_end hook 收到 session_id / project_id / channel_id / summary

### F3 实装 `box_complete` hook 触发点

- 标签：`plugin-v3.7`, `plugin-backend`, `test-required`
- 目标：ClusterCollector 汇齐 box 后触发插件 hook
- 验收：box_complete hook 可读 aggregated payload，异常不影响 MES 推送

### F4 接入 `scan_received` hook

- 标签：`plugin-v3.7`, `plugin-backend`, `risk-high`
- 目标：扫码事件进入检测状态机前后都可观察
- 验收：hook 可拿到 scanner_type / barcode / channel_id / timestamp

### F5 接入 `event_trigger` hook

- 标签：`plugin-v3.7`, `plugin-backend`, `risk-high`
- 目标：`_trigger_event` 中心事件前后可观察
- 验收：OK/NG/WARN 自定义事件均能触发，hook 异常不影响 toast/语音/报警

### F6 接入 `alarm_trigger` hook

- 标签：`plugin-v3.7`, `plugin-backend`, `risk-high`
- 目标：报警前触发插件 hook，允许返回 `False` 取消报警
- 验收：取消行为有 audit log；默认不取消；异常时按原报警逻辑继续

### F7 导出模板 registry

- 标签：`plugin-v3.7`, `plugin-backend`
- 目标：插件能注册导出模板
- 验收：模板出现在自定义导出候选列表，卸载插件后隐藏但保留历史记录

### F8 导出字段 resolver registry

- 标签：`plugin-v3.7`, `plugin-backend`
- 目标：插件能补充导出字段
- 验收：字段 key 必须 `plugin.{customer_code}.*` 前缀

### F9 实时导出触发器 registry

- 标签：`plugin-v3.7`, `plugin-backend`, `test-required`
- 目标：插件能注册 `session_end` / `box_complete` 等触发器
- 验收：实时规则在触发器到达时执行一次且只执行一次

### F10 Layout 菜单数据驱动改造

- 标签：`plugin-v3.7`, `plugin-frontend`, `test-required`
- 目标：把 Layout 中硬编码菜单改成路由/registry 驱动
- 验收：
  - 原 8 个主菜单顺序和行为不变
  - 插件菜单可追加
  - `/monitor`、`/settings`、`/activation` 不可隐藏

### F11 `PluginManager` 后端骨架

- 标签：`plugin-v3.7`, `plugin-backend`, `plugin-db`, `test-required`
- 目标：扫描、验签、加载单个 active 插件
- 验收：
  - 启动时扫描 `DATA_DIR/plugins`
  - active 插件验签通过才加载
  - 失败写 `plugin_audit_log`

### F12 插件管理 API

- 标签：`plugin-v3.7`, `plugin-backend`, `test-required`
- 目标：安装、列表、激活、停用、查看错误
- 验收：Settings UI 所需 API 全部返回稳定 schema

### F13 Settings 插件管理 UI

- 标签：`plugin-v3.7`, `plugin-frontend`, `test-required`
- 目标：提供安装 `.tjvplugin`、激活、停用、查看错误入口
- 验收：插件加载失败时客户能在 Settings 看到原因与错误码

### F14 BUG-1 `_fix_db_paths` 表名修复

- 状态：✅ 2026-05-09 已完成
- 改动：`backend/core/config.py` 中 `ml_models` → `models`

### F15 INCONSIST-2 mes_gateway 路径问题

- 状态：✅ 2026-05-09 验证为误报并撤销
- 结论：实际路径为 `/api/v1/mes/gateway/*`，与 `AGENTS.md` 一致

## 推荐实施顺序

```text
Week 1:
  F11 PluginManager 骨架 + DB 表
  F12 插件管理 API
  F10 前端 Layout/loader 基础

Week 2:
  F1 cycle_end phase 拆分
  F2/F3/F4/F5/F6 hook 接入
  F13 Settings UI

Week 3:
  F7/F8/F9 导出 registry
  三档示例插件端到端
  回归 / 安全 / 打包测试
```
