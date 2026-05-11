# B 路径插件运行时完工综合验收 — 2026-05-12

> 本文件汇总 ISSUES.md §运行时缺口 G1 / G1.5 / G2 / G3 四个 PR 的客户视角验收门，
> 是 `plugin_uat_2026-05-12`（管理链 UAT 一期）之后的第二期：**插件激活后真的能用**。

## 四个缺口完工总览

| PR | 解决问题 | 单元测试 | UAT 断言 | 证据 |
|---|---|---|---|---|
| **G1** | 后端 `register_plugin` 1 参 → 4 参 + registry routes/hooks/tables | 10/10 | curl 5/5 + DB 表 ✓ | `evidence/plugin_g1_2026-05-12/` |
| **G1.5** | `source.py end_cycle` 接 `plugin_manager.registry.hooks.fire(...)` | 4/4 | end-to-end fire 日志 ✓ | `evidence/plugin_g1_5_2026-05-12/` |
| **G2** | 前端 ADR-0002 ESM loader + Tier 2 demo dist 改写 + 重 sign | （Playwright 直跑无单元层） | Phase A 7/7 + Phase B 3/3 | `evidence/plugin_g2_2026-05-12/` |
| **G3** | Tier 1 主题钩子（title / Logo / favicon / CSS 变量 / hidden_menus） | （Playwright 直跑无单元层） | Phase A 5/5 + Phase B 5/5 | `evidence/plugin_g3_2026-05-12/` |
| **合计** | | **14/14** | **30/30** | |

## 客户视角验收门 — 三 Tier 串联

### Tier 1 白标 OEM 场景（ACME 出货前换品牌）

✅ 装 ACME 白标包 → 激活 → 重启 → tab 标题变 "ACME AI Vision"、主色 `#38bdf8`、Logo 换、favicon 换、菜单隐藏「报警设置」  
✅ 停用 + 重启 → 全部回退到天均默认（title="frontend"、CSS 变量空、`/vite.svg`、报警菜单回来）

### Tier 2 UI 扩展场景（客户自定义看板）

✅ 装 Factory Dashboard 包 → 激活 → 重启 → 侧边菜单新增「客户看板」 + tab 标题变 "Factory Dashboard"  
✅ 点击「客户看板」→ `#/factory-dashboard` 路由生效 → 渲染 Dashboard 组件（h2 + 当班目标 1200 卡片 + 缺陷阈值 8% 卡片）  
✅ Pinia store `plugin-internal-demo-dashboard` 真挂上 + state `{shiftTarget: 1200, defectThreshold: 8}` 可读  
✅ 停用 + 重启 → 菜单消失 + 路由失效 + store 注销

### Tier 3 全栈场景（客户业务表 + Hook + 路由）

✅ 装 Fullstack MES Extension → 激活 → 重启 → ORM 表 `p_internal_demo_notes` 真建出来  
✅ `GET /api/v1/plugins/internal-demo/demo/health` 200 OK  
✅ `GET /api/v1/plugins/internal-demo/demo/config-preview` 200 OK  
✅ `cycle_end` 触发 → 插件 `on_cycle_end_post(ctx)` 被调一次 → 后端日志 `[Plugin][internal-demo] hook fired: cycle_end/post_cycle/post handlers=1 cycle_id=N`  
✅ 插件 hook 抛错被 swallow，主程序周期统计不受影响

## 核心架构改动一图

```
客户上传 .tjvplugin (G1.5 路径同 G1)
          │
          ▼
backend/plugin_system/verifier.py  (签名 + HMAC + manifest schema 验证)
          │
          ▼
backend/plugin_system/manager.py
  PluginManager.load_active(db, app=FASTAPI)
          │
          ├─ G1: 4 参 register_plugin(app, registry, license_payload, host)
          │      ├─ registry.routes.include_router(...)  → /api/v1/plugins/{cc}/{sub}
          │      ├─ registry.hooks.register('cycle_end', ...)
          │      └─ registry.tables.register(PluginNote)  → 真 create_all
          │
          ▼
backend/api/source_session_lifecycle_mixin.py end_cycle()
  ├─ MES Hook on_cycle_end
  ├─ 周期性强制动作
  ├─ G1.5: plugin_manager.registry.hooks.fire('cycle_end', 'post_cycle', 'post', ctx)
  │        → 调到所有 register 过的插件 hook (异常 swallow)
  ├─ Scanner resume
  └─ Container 清理

前端启动 (G2 + G3):
  main.js → usePluginThemeStore.apply()  (G3)
              ├─ GET /api/v1/plugins/active/manifest
              ├─ CSS 变量 / title / favicon / Logo / hidden_menus
              └─ fetch frontend/theme.css → <style data-plugin-theme>

         → usePluginLoader.loadActivePluginFrontend(router)  (G2)
              ├─ fetch /api/v1/plugins/active/assets/frontend/dist/index.esm.js
              ├─ Blob URL + dynamic import
              └─ register({ host: {vue, pinia, router, i18n}, registry: {routes, menus, stores} })
                     ├─ host 注入风格 demo 用 host.vue.h(...) 不 import "vue"
                     ├─ registry.routes.add(...) → router.addRoute()
                     ├─ registry.menus.add(...)  → pluginThemeStore.pluginMenus[]
                     └─ registry.stores.register(...) → useStoreFn() 让 Pinia 自然挂载
```

## 安全 / 工程约束（与原设计对齐）

- ✅ RSA-PSS 签名 + customer_code HMAC 链路前端后端通用：未签名包永远进不了主程序
- ✅ 后端插件加载失败 100% 隔离：DB 状态 + audit log + 主程序继续启动
- ✅ 前端主题/loader 静默 fallback：fetch/import/register 任意环节失败都不影响主流程
- ⚠️ 不做 SES sandbox / iframe 隔离：插件 ESM 有完全 host 访问权（设计上由签名链锚定信任）
- ⚠️ 不支持热切换：客户切插件必须重启应用（与 PluginManager `pending_restart` 一致）

## 已知遗留 / 后续 PR

- ⚠️ **F1**: cycle_end 8-phase 拆分 (G1.5 是单 hook 触发点; F1 把 pre/main/post/finalize 四阶段独立拆开)
- ⚠️ **F7/F8/F9**: 导出模板 / 字段中央仓库 / 实时触发器 — registry 已留占位但运行时未接
- ⚠️ **菜单图标动态映射**: G2 layout 用固定 `DataAnalysis`, 没接 manifest.menus.icon 字段
- ⚠️ **frontend.i18n**: manifest 含字段但 store 暂未合并到主程序 i18n 实例
- ⚠️ **synthetic 推理链路**: 跑 b4 综合 UAT 时观察到 fps_inference=0, detections=[] — 是 source.py 独立 bug 与本 PR 无关

## 收尾事项（执行前）

- ⏳ `p11_cleanup_dev_pubkey`: 删 `backend/plugin_system/plugin_public_keys.py` + `/tmp/tianjun-dev-keys/` + `/tmp/tjv-*/`
- ⏳ `p12_restore_license`: 跑 `swap_license.py --restore` 把 `license.cache.customer` 从 `internal-demo` 还原回 `测试客户A`
- ⏳ `backend/api/test_runtime_routes.py` 里的 `/fire-plugin-cycle-end` 调试端点保留（已用 `_require_test_mode()` 守门，仅 RUNTIME_MODE=test 挂载，对生产 0 风险）
- ⏳ git restore `plugins-examples/tier1-theme/plugin.json` `plugins-examples/tier2-ui/plugin.json`（pack-plugin 重写过 canonical JSON）
