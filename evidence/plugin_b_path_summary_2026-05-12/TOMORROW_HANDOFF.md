# 明天 Review 接手指南 — 2026-05-12 凌晨封盘

## 当前状态

- **后端**: 已停（8001 释放，GPU 让出）
- **前端**: Vite dev server 还在 6001（你自己启的）
- **active 插件**: Tier 3 `internal-demo`（Fullstack MES Extension）已激活，等待重启生效
- **license.cache.customer**: 仍是 `internal-demo`（备份在 `/tmp/tianjun-dev-keys/license_backup.json`）
- **未提交改动**: 见 `git status`

## 推荐 review 路径（≈ 15 分钟）

### 1. 看 evidence 总览（5 分钟）
```bash
ls evidence/plugin_*_2026-05-12/
cat evidence/plugin_b_path_summary_2026-05-12/README.md
```

### 2. 跑一遍单元测试自证不破（30 秒）
```bash
PYTHONPATH=. python -m pytest tests/plugin_system/test_g1_registry.py tests/plugin_system/test_g1_5_cycle_end_hook.py -v
# 期待: 14 passed
```

### 3. 看代码改动 diff（10 分钟）
```bash
git diff --stat
# 主要文件:
#   backend/plugin_system/registry.py             (新建)
#   backend/plugin_system/manager.py              (改写)
#   backend/plugin_system/__init__.py             (扩 logger)
#   backend/main.py                               (插件加载时序修复)
#   backend/api/source_session_lifecycle_mixin.py (G1.5: cycle_end fire 触发点)
#   backend/api/test_runtime_routes.py            (G1.5: 调试 fire 端点)
#   frontend/src/main.js                          (G2+G3: 启动加载)
#   frontend/src/store/usePluginThemeStore.js     (新建)
#   frontend/src/composables/usePluginLoader.js   (新建)
#   frontend/src/layout/index.vue                 (G3+G2 菜单数据驱动)
#   frontend/src/layout/Navbar.vue                (G3 Logo/title)
#   plugins-examples/tier2-ui/frontend/dist/index.esm.js (G2 host 注入风格)
#   plugins-examples/tier1-theme/plugin.json     (pack-plugin canonical 重写, restore 即可)
#   plugins-examples/tier2-ui/plugin.json        (同上)
#   tests/plugin_system/test_g1_registry.py       (新建, 10 测)
#   tests/plugin_system/test_g1_5_cycle_end_hook.py (新建, 4 测)
#   tests/manual_uat/plugin_g3_theme_uat.py        (新建)
#   tests/manual_uat/plugin_g2_loader_uat.py       (新建)
```

### 4. 想重跑哪个 UAT（任选 / 4-10 秒一个）
```bash
# 先把后端起回来 + 装包 + 激活
export PLUGIN_SECRET_FILE=/tmp/tianjun-dev-keys/keygen/PLUGIN_SECRET.txt
./start_backend.sh &

# Tier 1 (G3): 主题钩子
curl -X POST http://localhost:8001/api/v1/plugins/internal-demo/deactivate
curl -X DELETE http://localhost:8001/api/v1/plugins/internal-demo
curl -X POST -F "file=@/tmp/tjv-signed/ACME_White_Label_Theme-1.0.0-internal-demo.tjvplugin" http://localhost:8001/api/v1/plugins/install
curl -X POST http://localhost:8001/api/v1/plugins/internal-demo/activate
PYTHONPATH=. python tests/manual_uat/plugin_g3_theme_uat.py
# 期待: Phase A ✅  Phase B ✅

# Tier 2 (G2): ESM loader
curl -X POST http://localhost:8001/api/v1/plugins/internal-demo/deactivate
curl -X DELETE http://localhost:8001/api/v1/plugins/internal-demo
curl -X POST -F "file=@/tmp/tjv-signed/Factory_Dashboard_UI-1.0.0-internal-demo.tjvplugin" http://localhost:8001/api/v1/plugins/install
curl -X POST http://localhost:8001/api/v1/plugins/internal-demo/activate
PYTHONPATH=. python tests/manual_uat/plugin_g2_loader_uat.py
# 期待: Phase A ✅  Phase B ✅

# Tier 3 (G1+G1.5): registry + hook fire (要 RUNTIME_MODE=test 重启)
curl -X POST http://localhost:8001/api/v1/plugins/internal-demo/deactivate
curl -X DELETE http://localhost:8001/api/v1/plugins/internal-demo
curl -X POST -F "file=@/tmp/tjv-signed/Fullstack_MES_Extension-1.0.0-internal-demo.tjvplugin" http://localhost:8001/api/v1/plugins/install
curl -X POST http://localhost:8001/api/v1/plugins/internal-demo/activate
# 重启后端 (test mode):
pkill -INT -f 'uvicorn backend.main' && sleep 3
export RUNTIME_MODE=test PLUGIN_SECRET_FILE=/tmp/tianjun-dev-keys/keygen/PLUGIN_SECRET.txt
./start_backend.sh &
sleep 8
# 验 router + table
curl http://localhost:8001/api/v1/plugins/internal-demo/demo/health
# 验 G1.5 hook fire
curl -X POST 'http://localhost:8001/api/v1/test/synthetic/fire-plugin-cycle-end?cycle_id=42&is_good=false'
grep '\[Plugin\]' /tmp/tianjun-dev-keys/backend_b4_v2.log | tail -5
```

## Commit 时再走（review 完）

### 收尾 p11 — 删开发期密钥/包/manifest 重写
```bash
# 删 dev 公钥（不能进 git）
rm backend/plugin_system/plugin_public_keys.py

# 还原 pack-plugin 重写过的 manifest（保持源文件干净）
git restore plugins-examples/tier1-theme/plugin.json
git restore plugins-examples/tier2-ui/plugin.json
# (注意: plugins-examples/tier2-ui/frontend/dist/index.esm.js 是 G2 真正改动, 不要 restore)

# 删 /tmp 临时
rm -rf /tmp/tianjun-dev-keys/ /tmp/tjv-out/ /tmp/tjv-signed/
```

### 收尾 p12 — 还原 license.cache
```bash
# 后端必须停
pkill -INT -f 'uvicorn backend.main' && sleep 3
# 还原
PYTHONPATH=. python /tmp/tianjun-dev-keys/swap_license.py --restore
# 哦… 上面 p11 已经把 /tmp/tianjun-dev-keys/ 删了。
# 备选: 直接 sqlite3 改, 备份值在 license_backup.json (执行 p11 前先备份这俩文件:)
cp /tmp/tianjun-dev-keys/license_backup.json ~/license_backup_2026_05_12.json
cp /tmp/tianjun-dev-keys/swap_license.py    ~/swap_license_2026_05_12.py
# 然后再删 /tmp 也行
```

⚠️ **顺序很关键**: 先 p12 还原 license, 再 p11 删 /tmp; **或者** 备份 swap_license.py + license_backup.json 到 ~/ 再删。

### Commit 建议

**单 commit 大提交**（最稳，可后续 reset 后拆 PR）:
```bash
git add backend/plugin_system/ backend/api/source_session_lifecycle_mixin.py backend/api/test_runtime_routes.py backend/main.py \
        frontend/src/main.js frontend/src/store/usePluginThemeStore.js frontend/src/composables/usePluginLoader.js \
        frontend/src/layout/index.vue frontend/src/layout/Navbar.vue \
        plugins-examples/tier2-ui/frontend/dist/index.esm.js \
        tests/plugin_system/test_g1_registry.py tests/plugin_system/test_g1_5_cycle_end_hook.py \
        tests/manual_uat/plugin_g3_theme_uat.py tests/manual_uat/plugin_g2_loader_uat.py \
        evidence/plugin_g1_2026-05-12 evidence/plugin_g1_5_2026-05-12 evidence/plugin_g2_2026-05-12 evidence/plugin_g3_2026-05-12 evidence/plugin_b_path_summary_2026-05-12 \
        docs/plugin-system/implementation/ISSUES.md
git commit -m "feat(plugin): B 路径插件运行时全套完工 (G1+G1.5+G2+G3)

- G1 后端 PluginRegistry + 4 参 register_plugin (routes/hooks/tables 真生效)
- G1.5 source.py end_cycle 接 plugin_manager.registry.hooks.fire 触发点
- G2 前端 ADR-0002 ESM loader + host 注入 + Tier2 demo 重打包
- G3 Tier 1 主题钩子 (title/Logo/favicon/CSS/hidden_menus)

单元测试 14/14 + Playwright UAT 30/30, evidence 全套落档."
```

**拆 4 PR**（review 友好）: 见 `evidence/plugin_b_path_summary_2026-05-12/README.md` 的四个 PR 文件清单分别 add + commit.
