# 插件示例目录

本目录放三档插件的最小可运行骨架，和 `docs/plugin-system/design/08_examples.md` 对应。

| 目录 | 档位 | 用途 |
|---|---:|---|
| `tier1-theme/` | 1 | 白标主题：CSS、Logo、Favicon、i18n、隐藏菜单 |
| `tier2-ui/` | 2 | 前端扩展：动态路由、菜单、Pinia store、UI 页面 |
| `tier3-fullstack/` | 3 | 全栈扩展：FastAPI router、hook、ORM 表、导出模板 |

所有示例都使用 `internal-demo` 或 `internal-test` 这类内部客户码。真实客户发版前必须在 `docs/plugin-system/customer-codes.md` 注册客户码，并由主作者重新签名。

## 本地打包

```bash
python scripts/plugin/pack-plugin.py --src plugins-examples/tier1-theme --out /tmp/tjv-out --skip-build
python scripts/plugin/pack-plugin.py --src plugins-examples/tier2-ui --out /tmp/tjv-out --skip-build
python scripts/plugin/pack-plugin.py --src plugins-examples/tier3-fullstack --out /tmp/tjv-out --skip-build
```

`--skip-build` 仅用于当前骨架示例；真正的 Tier 2/Tier 3 前端包应先由 Vite 构建出 `frontend/dist/index.esm.js`。
