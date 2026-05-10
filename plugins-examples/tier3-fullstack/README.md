# Tier 3 Full-stack 示例

这个示例展示全栈插件最小形态：

- `backend/routes.py` 提供一个插件路由
- `backend/hooks.py` 注册 `cycle_end/post_cycle/post` hook
- `backend/models.py` 声明插件自有表 `p_internal_demo_notes`
- `templates/cycle_summary.txt` 作为导出模板示例

打包：

```bash
python scripts/plugin/pack-plugin.py --src plugins-examples/tier3-fullstack --out /tmp/tjv-out --skip-build
```

注意：当前只是示例骨架，真实加载需要主程序完成 `PluginManager` 与 registry API。
