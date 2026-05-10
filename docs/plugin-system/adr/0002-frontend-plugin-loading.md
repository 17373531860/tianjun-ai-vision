# ADR 0002: 前端插件使用 fetch + Blob URL + dynamic import

## 状态

Accepted — 2026-05-10

## 背景

天骏主程序生产环境运行在 Electron 里，前端资源常见来源包括：

- Vite dev server：`http://localhost:*`
- 打包后的本地文件：`file://.../dist/index.html`
- 后端静态资源接口：`http://127.0.0.1:8000/api/v1/plugins/active/assets/...`

直接 `import('/api/v1/plugins/.../index.esm.js')` 在 Electron `file://` 场景下容易遇到 CORS、base URL、相对路径解析等差异。

## 决策

前端插件入口加载流程：

```text
fetch(entryUrl)
  → response.text()
  → new Blob([source], { type: "text/javascript" })
  → URL.createObjectURL(blob)
  → dynamic import(blobUrl)
  → module.default.register(ctx)
```

核心依赖（Vue、Pinia、ElementPlus）通过 host vendor 暴露：

- 首选：`importmap`
- 备选：`window.__pluginVendor`

## 结果

- Electron `file://` 与 dev server 使用同一套 loader
- 插件 ESM 不直接依赖主程序构建产物路径
- loader 可以在 import 前做超时、审计、错误包装

## 被拒绝方案

### A. 直接 dynamic import HTTP URL

拒绝原因：Electron `file://` 下 URL 解析与 CORS 行为不稳定。

### B. SystemJS

拒绝原因：引入额外 runtime，长期维护成本高。

### C. iframe 隔离

拒绝原因：和主程序 Pinia、ElementPlus、路由、i18n 集成成本过高。

## 实施引用

- `docs/plugin-system/design/05_tier2_ui.md`
- `plugins-examples/tier2-ui/frontend/dist/index.esm.js`
