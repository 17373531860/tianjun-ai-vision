# v3.7 插件系统实施 Issue 清单

> 来源：`docs/plugin-system/design/06_tier3_fullstack.md` §九。  
> 状态：F14 已完成，F15 已验证为误报撤销。剩余 13 个实施项建议拆成独立 PR。  
> **2026-05-12 补充：在开发机做完管理链 UAT (`evidence/plugin_uat_2026-05-12/`) 后，发现 F1–F13 当前的"完成度"实际只到「管理外壳」，真正"激活后插件代码生效"的运行时实现还有 3 个硬缺口（G1/G2/G3），见末尾 §运行时缺口。**

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

### F8 导出字段 resolver registry ✅ 已落地 (2026-08-04, lg-worktime 插件配套)

- 标签：`plugin-v3.7`, `plugin-backend`
- 目标：插件能补充导出字段
- 验收：字段 key 必须 `plugin.{customer_code}.*` 前缀
- 实现：`registry.export_fields.register(fields, provider)` →
  `backend/services/export_field_registry.py` 中央仓库「插件字段」分组；
  provider(db, ctx) 在 `export_context._fill_plugin_sections` 里执行，
  值挂 `ctx["plugin"]["<cc_snake>"]`，异常隔离、缺字段静默空值；
  卸载/重注册整体替换。首个消费方：`plugins-examples/lg-worktime`（18 个 Lean 字段）。
  测试：`tests/plugin_system/test_export_fields_registry_F8.py`

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

---

## 运行时缺口（2026-05-12 UAT 暴露）

> 上下文：路径 A 用 `tests/manual_uat/plugin_management_chain_uat.py` 跑完客户管理链 UAT，证据全过（见 `evidence/plugin_uat_2026-05-12/`）。但管理链 PASS ≠ 插件功能 PASS。下面 3 个缺口是**激活成功后插件代码到底有没有真生效**这一层的硬缺口。按硬约束铁律 4「测试矩阵全绿不等于验收完成」立档。

### G1 后端 PluginManager 缺 registry，且 `register_plugin` 调用签名不匹配 demo

- 标签：`plugin-v3.7`, `plugin-backend`, `risk-high`, `test-required`, `blocker`
- 现状证据：
  - 主程序调用方 (`backend/plugin_system/manager.py:77-80`) 传 **1 个 dict**：
    ```python
    register({"plugin_dir": str(install_dir), "customer_code": customer_code})
    ```
  - 但 `plugins-examples/tier3-fullstack/backend/__init__.py:8` 期望 **4 个对象**：
    ```python
    def register_plugin(app, registry, license_payload, host):
        registry.tables.register(...)
        registry.routes.include_router(...)
        registry.hooks.register(...)
    ```
  - 全仓 `grep "PluginRegistry|registry\."` 在 `backend/` 内只命中 `manager.py` 自身，**主程序根本没有 `registry` 对象 / `routes` 接入 / `hooks` 接入 / `tables` 接入**。
- 客户视角影响：Tier 3 插件激活后即抛 `TypeError: register_plugin() missing 3 required positional arguments`，**整个 Tier 3 等于不可用**。Tier 1/2 不抛但也没用——因为它们的 demo 也都假设 registry 存在。
- 验收：
  - 在 `backend/plugin_system/` 加 `registry.py`，提供 `routes` / `hooks` / `tables` / `export_templates` / `export_fields` / `realtime_triggers` 6 个子 registry
  - `PluginManager._load_backend_module` 改成 `register_plugin(app, registry, license_payload, host)` 4 参调用
  - 跑 Tier 3 示例 → 激活 → 重启后端 → `GET /api/v1/plugins/demo/notes` 真返回 200 + 表 `p_internal_demo_notes` 真建出来 + cycle_end hook 真被触发一次
  - 三件套：视频 + 截图 + `app.log` 中 `[Plugin][internal-demo] cycle_end post_cycle 触发` 行
- 关联：F1（cycle_end phase）、F11（PluginManager）、F2–F9（hook/registry）。这条 issue 是它们的**联合验收门**。

### G2 前端没实现 ADR-0002 的运行时插件加载器

- 标签：`plugin-v3.7`, `plugin-frontend`, `risk-high`, `test-required`, `blocker`
- 现状证据：
  - ADR-0002 描述的「`fetch(entryUrl) → Blob → URL.createObjectURL → dynamic import(blobUrl) → module.default.register(ctx)`」加载流程，在 `frontend/src/**/*` 全仓 `grep` 关键字 `pluginLoader|loadPlugin|registerPlugin|__pluginVendor|active/manifest`，**全部零命中**。
  - 唯一对 `pluginStore.activeCustomerCode` 的消费在 `frontend/src/views/Settings/index.vue:1063`，仅用来在表格里显示一个绿 tag。
- 客户视角影响：
  - Tier 2 demo `frontend/dist/index.esm.js` 永远不会被加载
  - Tier 2 demo 声明的 `/factory-dashboard` 路由永远不会注册 → 客户激活后该 URL 仍 404
  - Tier 2 demo 的 Pinia store `plugin-internal-demo-dashboard` 永远不会进 store registry
- 验收：
  - 新增 `frontend/src/composables/usePluginLoader.js`：监听 `pluginStore.activeCustomerCode`，按 ADR-0002 流程加载 `entry.js`
  - 暴露 `window.__pluginVendor = { vue, pinia, elementPlus, ... }`
  - Tier 2 demo 激活 + 重启 → 浏览器能访问 `/#/factory-dashboard` 并看到该页面渲染出来
  - 三件套：视频显示菜单出现新项、点进新页面看到 demo UI
- 关联：F10（菜单数据驱动）、F13（Settings UI）。F10 改完是基础，G2 是真正的加载器。

### G3 Tier 1 主题前端钩子缺失

- 标签：`plugin-v3.7`, `plugin-frontend`, `test-required`
- 现状证据：
  - 全仓 `grep "css_variables|tj-primary|app_title|hidden_menus"` 在 `frontend/src/**` **零命中**
  - 即使 G2 加载器实现了，Tier 1 主题也不需要走 `dynamic import`——它是声明式资产（CSS 变量 / Logo URL / 隐藏菜单列表），需要一个**声明式应用器**：
    - 读 `active/manifest` 的 `frontend.theme.css_variables` → 写 `document.documentElement.style.setProperty(...)`
    - 读 `frontend.theme.app_title` → 写 `document.title` + Layout 标题栏
    - 读 `frontend.theme.logo` / `favicon` → 替换 `<link rel='icon'>` 和 Layout Logo `<img>`
    - 读 `frontend.theme.css` → fetch 并 `<style>` 插入
    - 读 `frontend.hidden_menus` → 影响 F10 菜单驱动器
- 客户视角影响：激活 Tier 1 白标主题后 UI **不会有任何变化**——客户付钱买的"白标交付"等于空头支票。
- 验收：
  - 新增 `frontend/src/composables/usePluginTheme.js`，启动 + activeCustomerCode 变化时拉 `active/manifest` 应用
  - Tier 1 demo 激活 → 浏览器标题栏变 "ACME AI Vision"、主色变 `#38bdf8`、`/alarm` 菜单消失、Logo 换成 ACME logo
  - 三件套：激活前后的截图对比清晰可见

### B 路径整体验收（G1+G2+G3 都做完后）

- 标签：`plugin-v3.7`, `test-required`, `meta-uat`
- 任务：把 `tests/manual_uat/plugin_management_chain_uat.py` 升级为 `plugin_runtime_chain_uat.py`，**在每个 active 阶段额外断言**：
  - Tier 1：`document.title == 'ACME AI Vision'` + 主色 RGB 命中 #38bdf8
  - Tier 2：`page.goto('/factory-dashboard')` 200 且看到 "客户看板" 文本
  - Tier 3：`requests.get('/api/v1/plugins/demo/notes') == 200` + `SELECT * FROM p_internal_demo_notes` 表存在
- 产物：`evidence/plugin_uat_<date>/` 新一轮三件套
- 这是 G1+G2+G3 全部完成的**唯一验收依据**，按铁律 6 不接受单元测试矩阵代替。

---

### 现状自评汇总表（按客户视角）

| 维度 | 实现度 | 缺口 issue |
|---|---|---|
| 主作者打包 + 签名 + 离线验签 | ✅ 完整 | — |
| 客户 UI 上传 / 列表 / 激活按钮 | ✅ 完整 | F13 已实现 |
| 后端 verifier（RSA + HMAC + digest + license） | ✅ 完整 | F11/F12 已实现 |
| DB 状态机 `installed → active → stopped/loaded/failed` | ✅ 完整 | F11/F12 已实现 |
| **Tier 1 主题真生效（改色/换 Logo/隐菜单）** | ❌ 未实现 | **G3** |
| **Tier 2 前端动态加载（路由/menu/store）** | ❌ 未实现 | **G2**, F10 |
| **Tier 3 后端 hook/router/table 接入** | ❌ 未实现 + 接口对不上 | **G1**, F1–F9 |
