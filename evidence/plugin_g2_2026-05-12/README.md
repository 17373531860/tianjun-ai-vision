# G2 ADR-0002 前端 ESM Loader — 完工证据

> 解决 `docs/plugin-system/implementation/ISSUES.md` §运行时缺口 G2：
> 前端没有 `frontend.entry`（`*.esm.js`）的 fetch + Blob + dynamic import 通路 —
> Tier 2/3 插件激活后菜单/路由/Pinia store 都不会生效。

## 改动

### 主程序

- **新增** `frontend/src/composables/usePluginLoader.js`：
  - `loadActivePluginFrontend(router)` — fetch `/api/v1/plugins/active/assets/{entry}` → Blob URL → dynamic import
  - host 注入：`{ vue, pinia, i18n, router, customerCode, pluginVersion }`
  - registry：
    - `routes.add(routeOpts)` / `routes.remove(name)` → `router.addRoute` / `router.removeRoute`
    - `menus.add(menuOpts)` / `menus.remove(path)` → `pluginThemeStore.pluginMenus`
    - `stores.register(id, useStoreFn)` → 调一次工厂，Pinia setup-store 自然挂载
  - 失败静默 fallback（fetch/import/register 任一抛错都不影响主程序）

- **扩 `frontend/src/store/usePluginThemeStore.js`**：
  - `state.pluginMenus` / `state.pluginRoutes`
  - `addPluginMenu` / `removePluginMenu` / `addPluginRoute` / `removePluginRoute`
  - getter `sortedPluginMenus`
  - `_resetTheme()` 时连带清空

- **改 `frontend/src/main.js`**：Pinia + router 都 ready 后调 `loadActivePluginFrontend(router)`

- **改 `frontend/src/layout/index.vue`**：在 8 个硬编码 router-link 后渲染 `<router-link v-for="m in pluginTheme.sortedPluginMenus">`（图标统一用 `DataAnalysis`，后续可按 manifest.menus.icon 动态映射）

### 插件 demo

- **改写 `plugins-examples/tier2-ui/frontend/dist/index.esm.js`**：从 `import "vue"/"pinia"` bare specifier 风格 → **host 注入** 风格（`{ defineComponent, h, ref } = host.vue` / `{ defineStore } = host.pinia`），dev/prod 都能用
- 重 pack + 重 sign（新 files_digest `sha256:93018127...`）

## 客户视角验收（Phase A 7/7 + Phase B 3/3 = 10/10 ✅）

### Phase A 激活 Tier 2 + reload

| 断言 | 期望 | 实际 | 结果 |
|---|---|---|---|
| `document.title` | `"Factory Dashboard"` | `"Factory Dashboard"` | ✅ |
| 侧边菜单含「客户看板」(`.plugin-nav-item`) | yes | 9 项, 第 9 项 plugin=true | ✅ |
| 点击「客户看板」→ `#/factory-dashboard` | yes | `current_path=#/factory-dashboard` | ✅ |
| Dashboard 组件渲染 (`section.plugin-dashboard`) | rendered=true | rendered=true | ✅ |
| h2 = `"客户看板"` | match | match | ✅ |
| 卡片「当班目标」= `"1200"` | match | match | ✅ |
| 卡片「缺陷阈值」= `"8%"` | match | match | ✅ |
| Pinia store `plugin-internal-demo-dashboard` 存在 + 值 `shiftTarget=1200/defectThreshold=8` | yes | yes | ✅ |

证据：`01_loaded_tier2.png` / `02_sidebar_with_plugin_menu.png` / `03_dashboard_rendered.png` / `verdict_phase_a.json`

### Phase B 停用 + reload

| 断言 | 期望 | 实际 | 结果 |
|---|---|---|---|
| 侧边菜单不含 `.plugin-nav-item` | yes | yes | ✅ |
| 菜单 text 中不含「客户看板」 | yes | yes | ✅ |
| Pinia store `plugin-internal-demo-dashboard` 消失 | yes | yes | ✅ |

证据：`04_after_deactivate.png` / `05_after_deactivate_sidebar.png` / `verdict_phase_b.json`

## 安全 & 工程约束

- ⚠️ **没做 SES sandbox / iframe 隔离** — 插件代码有完全 host 访问权（含 Pinia state / router）。
  与后端 RSA-PSS 签名 + customer_code HMAC 校验配套：未签名的 ESM 永远进不了主程序 → 信任 chain 锚在签名上而非 sandbox。
- ⚠️ **不支持热切换/热卸载** — 客户切插件必须重启应用（与后端 PluginManager `pending_restart` 一致）。
- ⚠️ **图标固定 `DataAnalysis`** — manifest.menus.icon 字段名读取后还没接 element-plus icon component map，后续 PR 加。
- ⚠️ **没接 manifest.frontend.i18n** — locale 文件合并机制 Phase 2。

## **不**在 G2 范围（明确）

- ❌ **Tier 3 全栈插件的前端入口**：现 demo 没有 `frontend/dist/*.esm.js`，纯后端 register_plugin 已由 G1 处理。
- ❌ **G1.5 cycle_end hook 触发点**：hook 已能 register（G1），但 `source.py` 还没接 `plugin_manager.registry.hooks.fire(...)` — 独立 risk-high PR

## 复现

```bash
# 装 + 激活 Tier 2 包
curl -X POST -F "file=@/tmp/tjv-signed/Factory_Dashboard_UI-1.0.0-internal-demo.tjvplugin" \
  http://localhost:8001/api/v1/plugins/install
curl -X POST http://localhost:8001/api/v1/plugins/internal-demo/activate

# 跑 UAT
PYTHONPATH=. python tests/manual_uat/plugin_g2_loader_uat.py
# 期待: Phase A ✅  Phase B ✅, 退出码 0
```
