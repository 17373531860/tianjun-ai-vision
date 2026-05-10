# Tier 1 Theme 示例

这个示例只做白标主题，不增加页面、不跑后端代码。

能力范围：

- 替换 `app_title`
- 注入 `frontend/theme.css`
- 替换 Logo / Favicon
- 合并 `zh-CN` / `en-US` i18n 文案
- 隐藏 `/alarm` 菜单

打包：

```bash
python scripts/plugin/pack-plugin.py --src plugins-examples/tier1-theme --out /tmp/tjv-out --skip-build
```
