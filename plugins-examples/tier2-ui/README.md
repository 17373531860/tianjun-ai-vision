# Tier 2 UI 示例

这个示例展示 UI 插件最小形态：

- 一个动态路由 `factory-dashboard`
- 一个菜单项 `/factory-dashboard`
- 一个 Pinia store：`plugin-internal-demo-dashboard`
- 一个已构建的 ESM 入口：`frontend/dist/index.esm.js`

开发态源码放在 `frontend/src/`，真实项目中应由 Vite library mode 构建到 `frontend/dist/`。

打包：

```bash
python scripts/plugin/pack-plugin.py --src plugins-examples/tier2-ui --out /tmp/tjv-out --skip-build
```
