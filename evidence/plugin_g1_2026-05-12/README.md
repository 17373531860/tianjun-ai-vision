# G1 后端 PluginRegistry + 4参 register_plugin — 完工证据

> 解决 `docs/plugin-system/implementation/ISSUES.md` §运行时缺口 G1：
> 后端 `PluginManager._load_backend_module` 用 1 参 dict 调 `register_plugin`，
> 但 demo `register_plugin(app, registry, license_payload, host)` 要 4 参 — Tier 3 激活后必抛 TypeError。

## 改动

- **新增** `backend/plugin_system/registry.py`：`PluginRegistry` + 三个真接的子 registry (`routes` / `hooks` / `tables`) + 三个占位 (`export_templates` / `export_fields` / `realtime_triggers` 等 F7/F8/F9)
- **新增** `backend/plugin_system/registry.PluginHost`：暴露给插件代码的 host facade（`get_db_session()` / `customer_code` / `plugin_dir`）
- **改写** `backend/plugin_system/manager.py`：
  - `_load_backend_module` 用 4 参 `register_plugin(app, registry, license_payload, host)`
  - `load_active(db, app=None)` 必须传 app；否则保护性 RuntimeError
  - 加载成功把 `registry.snapshot()` 写 audit log
  - 异常 swallow 在 `load_active` 外层（main.py 还包一层兜底）
- **改 main.py**：把 `load_active_plugin_on_startup()` 从 `_run_startup_init`（app 创建前）移到所有 `app.include_router` 与 static mount 完成后的新触发点 `_load_active_plugin_after_app()`
- **新增** `backend/plugin_system/__init__.py` 给 `tianjun.plugin` logger 挂 stdout handler（之前 logger 冒泡到 root WARNING 被吞）
- **新增** `tests/plugin_system/test_g1_registry.py`：10 个单元测试

## 客户视角验收（按硬约束铁律 6）

| 断言 | 结果 | 证据文件 |
|---|---|---|
| `GET /api/v1/plugins/internal-demo/demo/health` 200 OK | ✅ | `tier3_router_health_200.json` |
| `GET /api/v1/plugins/internal-demo/demo/config-preview` 200 OK | ✅ | `tier3_router_config_preview_200.json` |
| ORM 表 `p_internal_demo_notes` 真建出来 | ✅ | `db_tables_check.txt` |
| 后端启动日志含 `[Plugin][internal-demo] router mounted / table created / hook registered / startup_load OK` | ✅ | `backend_startup_plugin_log.txt` |
| Plugin DB 状态: `runtime_status=loaded, health=ok, last_error_code=null` | ✅ | `plugins_state_after_g1.json` |
| 10 个单元测试（registry/manager 接口契约护栏）全过 | ✅ | `unit_tests_result.txt` |

## **不**在 G1 范围（明确）

- ❌ **cycle_end hook 真被触发**：hook **注册**成功（snapshot 显示 `hooks: [{cycle_end, post_cycle, post, 100}]`），但触发点（source.py 的 cycle_end）还**没接** — 留 G1.5 / F1（risk-high，要单独 PR）
- ❌ **G2 前端 ADR-0002 loader**：本 PR 不动前端
- ❌ **G3 Tier 1 主题钩子**：本 PR 不动前端

## 复现

```bash
# 前置（同 plugin_uat_2026-05-12）
python /tmp/tianjun-dev-keys/swap_license.py
PLUGIN_SECRET_FILE=/tmp/tianjun-dev-keys/keygen/PLUGIN_SECRET.txt ./start_backend.sh &

# 装 + 激活 Tier 3
curl -X POST -F "file=@/tmp/tjv-signed/Fullstack_MES_Extension-1.0.0-internal-demo.tjvplugin" \
  http://localhost:8001/api/v1/plugins/install
curl -X POST http://localhost:8001/api/v1/plugins/internal-demo/activate

# 重启后端让 PluginManager.load_active 跑
pkill -INT -f 'uvicorn backend.main:app' && sleep 3
PLUGIN_SECRET_FILE=/tmp/tianjun-dev-keys/keygen/PLUGIN_SECRET.txt ./start_backend.sh &

# 验证
curl http://localhost:8001/api/v1/plugins/internal-demo/demo/health        # 200
curl http://localhost:8001/api/v1/plugins/internal-demo/demo/config-preview # 200
sqlite3 backend/database.db '.tables p_*'                                   # p_internal_demo_notes

# 单元测试
PYTHONPATH=. python -m pytest tests/plugin_system/test_g1_registry.py -v   # 10/10
```
