# 插件系统「管理链」UAT 证据 — 2026-05-12

按硬约束铁律 2/6 要求的「视频 + 截图 + 运行日志」三件套留底。

## 范围

| 路径 | 范围 |
|---|---|
| **A（本次）** | 后端 verifier 验签 + DB 状态机 + UI 管理链（上传/列表/激活/停用/卸载）|
| C（同时） | `docs/plugin-system/implementation/ISSUES.md` 归档 3 个运行时缺口 |
| B（后续） | 等下个迭代实现「激活后真生效」三个缺口 |

**本次 UAT 不覆盖**：激活后主题/路由/hook 是否真生效（前端运行时插件加载器未实现，后端 register_plugin 接口与 demo 不匹配，详见 ISSUES.md）。

## 环境

- 后端：`PLUGIN_SECRET_FILE=/tmp/tianjun-dev-keys/keygen/PLUGIN_SECRET.txt`，开发 master keypair fingerprint = `a24efcb5d28cdedb272ac7cb70273123`
- 前端：Vite dev server (http://localhost:6001)
- DB 临时改动：`license.cache.customer` 由 `"测试客户A"` 改为 `"internal-demo"`（已备份，UAT 结束还原）
- 主程序 plugin_public_keys.py：DEV-ONLY 注入，untracked，UAT 结束删除
- Playwright Chromium 130 headless，1280×800 录视频

## 产物清单

```
00_settings_landing.png         设置页落地
01_plugin_tab_empty.png         切到插件管理 Tab, 空列表
02_tier1_uploaded.png           Tier1 主题包上传成功, 列表出现 installed 行
03_tier1_active.png             点激活, 状态 tag 变 active, runtime=pending_restart
04_tier1_deactivated.png        点停用, is_active=False, runtime=stopped
05_tier1_removed.png            点卸载, 列表清空
06_tier2_active.png             Tier2 UI 包激活
07_tier3_active.png             Tier3 全栈包激活
08_final_clean.png              收尾清理, 列表清空, license.cache 待还原
api_state.json                  每阶段 GET /api/v1/plugins 完整快照
page@*.webm                     全程录像 (2.3 MB)
logs/
  uat_run.log                   UAT 脚本自身 stdout
  backend_uat.log               后端进程完整日志 (含 verifier 内部错误)
  backend_plugin_calls.log      只筛 /api/v1/plugins 的 18 行调用记录
```

## 关键证据：api_state.json 状态变迁

| 阶段 | items | active_customer_code | runtime_status |
|---|---:|---|---|
| 01 after_open | 0 | None | — |
| 02 tier1_uploaded | 1 | None | installed |
| 03 tier1_active | 1 | internal-demo | pending_restart |
| 04 tier1_deactivated | 1 | None | stopped |
| 05 tier1_removed | 0 | None | — |
| 06 tier2_active | 1 | internal-demo | pending_restart |
| 07 tier3_active | 1 | internal-demo | pending_restart |
| 08 final_clean | 0 | None | — |

完全符合 `docs/plugin-system/design/03_database.md` 描述的状态机。

## 后端调用证据

```
POST /api/v1/plugins/install            200 OK  (5 次, 三档各一次 + 两次清理重装)
POST /api/v1/plugins/internal-demo/activate    200 OK  (4 次)
POST /api/v1/plugins/internal-demo/deactivate  200 OK  (3 次)
DELETE /api/v1/plugins/internal-demo    200 OK  (6 次)
```

verifier 链路证明：RSA-PSS-SHA256 签名校验 + 公钥 fingerprint 匹配 + files_digest 一致性 + HMAC 客户绑定 + license customer 一致性，5 项全过。

## 还没盖到的部分（B 路径）

| 缺口 | 客户视角影响 |
|---|---|
| Tier 1 主题真生效 | 客户激活后 UI 不会变 ACME 品牌 |
| Tier 2 路由真注册 | 客户激活后 `/factory-dashboard` 仍 404 |
| Tier 3 后端真接入 | 激活时 `register_plugin()` 参数不匹配，会抛 `TypeError` |

详见 [`docs/plugin-system/implementation/ISSUES.md`](../../docs/plugin-system/implementation/ISSUES.md)。

## 复现方法

```bash
# 前置
python /tmp/tianjun-dev-keys/swap_license.py            # 切 license customer
PLUGIN_SECRET_FILE=/tmp/tianjun-dev-keys/keygen/PLUGIN_SECRET.txt ./start_backend.sh &
./start_frontend.sh &

# 跑 UAT
conda activate tianjun-runtime
PYTHONPATH=. python tests/manual_uat/plugin_management_chain_uat.py

# 收尾还原
python /tmp/tianjun-dev-keys/swap_license.py --restore
rm backend/plugin_system/plugin_public_keys.py
rm -rf /tmp/tianjun-dev-keys /tmp/tjv-out /tmp/tjv-signed
git restore plugins-examples/tier1-theme/plugin.json
```

## 留档结论

✅ 「管理链」(上传 / 验签 / 解压 / DB 状态机 / UI 列表 / 激活按钮) **端到端工作正常**。  
❌ 「运行时」(激活后插件代码真生效) **未实现**——见 ISSUES.md，列入 B 路径下个迭代修复。
