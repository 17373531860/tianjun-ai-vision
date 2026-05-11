# G3 Tier 1 主题钩子 — 完工证据

> 解决 `docs/plugin-system/implementation/ISSUES.md` §运行时缺口 G3：
> 前端没有任何代码在消费 active 插件的 manifest，所以 Tier 1 白标包激活后
> 主程序外观完全没变化（仍是天均默认色 + 默认 Logo + 显示「报警设置」菜单）。

## 改动

- **新增** `frontend/src/store/usePluginThemeStore.js`：Pinia store + composable，
  - `apply()` 拉 `GET /api/v1/plugins/active/manifest`
  - 应用 `frontend.theme.css_variables` → `document.documentElement.style`
  - 应用 `frontend.theme.app_title` → `document.title`
  - 应用 `frontend.theme.favicon` → `<link rel='icon'>`
  - 应用 `frontend.theme.logo` → 暴露 `logoUrl` 给 Navbar `<img>`
  - fetch `frontend.theme.css` 文本 inject `<style data-plugin-theme>` 到 head
  - 暴露 `isMenuHidden(path)` 给 Layout
  - 没 active 插件 / 失败 → 静默 fallback，不影响主程序
- **改 `frontend/src/main.js`**：Pinia 注册后 dynamic import + `themeStore.apply()` fire-and-forget
- **改 `frontend/src/layout/Navbar.vue`**：Logo `<img>` + 标题用 `pluginTheme.appTitle || store.display.brandName`
- **改 `frontend/src/layout/index.vue`**：8 个 router-link 全加 `v-if="!pluginTheme.isMenuHidden(path)"`

## 客户视角验收（按硬约束铁律 6）

### Phase A — 激活 Tier 1 主题包，重启后验证

| 断言 | 期望 | 实际 | 结果 |
|---|---|---|---|
| `document.title` | `"ACME AI Vision"` | `"ACME AI Vision"` | ✅ |
| `--tj-primary` | `#38bdf8` | `#38bdf8` | ✅ |
| `--tj-primary-hover` | `#0ea5e9` | `#0ea5e9` | ✅ |
| `--tj-bg-page` | `#020617` | `#020617` | ✅ |
| `--tj-text-main` | `#e2e8f0` | `#e2e8f0` | ✅ |
| favicon href | `/plugins/active/assets/.../favicon.svg` | ✓ | ✅ |
| Logo `<img>` 出现 | 插件 logo.svg, naturalWidth=240 | ✓ | ✅ |
| 菜单不含「报警设置」 | hiddenMenus=`["/alarm"]` 生效 | 7 项 (原 8 项) | ✅ |

证据：`01_loaded_with_theme.png` / `02_sidebar_open.png` / `verdict.json`

### Phase B — 停用插件 + reload，验证完全回退

| 断言 | 期望 | 实际 | 结果 |
|---|---|---|---|
| title 回退 | 不是 `"ACME AI Vision"` | `"frontend"` (默认) | ✅ |
| `--tj-primary` 回退 | 不是 `#38bdf8` | `""` (默认空) | ✅ |
| favicon 回退 | 不指向 `/plugins/active/assets` | `/vite.svg` | ✅ |
| Logo 不再显示插件 logo | header 内无 plugins/active/assets `<img>` | ✓ | ✅ |
| 「报警设置」菜单回来 | menu 含「报警」 | ✓ (8 项全显示) | ✅ |

证据：`03_after_deactivate_reload.png` / `04_after_deactivate_sidebar.png` / `verdict_unload.json`

## **不**在 G3 范围（明确）

- ❌ **G2 ADR-0002 前端 loader**：Tier 2/3 的 `frontend/dist/index.esm.js` 动态 import 没接 — 这是另一个大工程，单独 PR
- ❌ **生产打包验证**：仅 Vite dev server 验证；Nuitka/Electron 打包后路径替换 / iframe 嵌入策略另议
- ❌ **国际化文案合并 (`frontend.i18n`)**：manifest 含字段但 store 暂未合并 (Phase 2)

## 复现

```bash
# 前置: 装 + 激活 Tier 1 包 (后端不需重启)
curl -X POST -F "file=@/tmp/tjv-signed/ACME_White_Label_Theme-1.0.0-internal-demo.tjvplugin" \
  http://localhost:8001/api/v1/plugins/install
curl -X POST http://localhost:8001/api/v1/plugins/internal-demo/activate

# 跑 UAT
PYTHONPATH=. python tests/manual_uat/plugin_g3_theme_uat.py
# 期待: Phase A ✅  Phase B ✅, 退出码 0
```
