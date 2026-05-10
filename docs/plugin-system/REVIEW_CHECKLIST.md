# 插件系统设计 Review 清单

> 整理时间：2026-05-08
> 适用范围：design/00 ~ 08（约 11000 行设计）+ inventory/01 ~ 05（约 4800 行盘点）
> 本文目的：把全套设计中**主作者必须拍板**、**团队评审**、**推迟评估**、**实施细节**四类决策点抽出来，便于一次性 review。
>
> 每一项标了优先级、影响面、当前默认值、需要确认的问题。

---

## 0. 阅读约定

- 🔴 P0：阻塞 v3.7 实施，**必须先拍板**
- 🟠 P1：影响实施方向，**强烈建议拍板**
- 🟡 P2：可在实施 PR 阶段决定
- ⚪ P3：留作 v3.8+ 再议

每项行的标记含义：

| 标 | 含义 |
|---|---|
| `[默认]` | 设计文档里**已经选了一个值**作为默认 |
| `[二选一]` | 有 A/B 两个候选，**必须选一个** |
| `[多选]` | 列了 N 项可勾选 |
| `[确认]` | 只需要"是/否" |

---

## 1. 已敲定（不需要再讨论）

### 1.1 design 00 — 8 个开放问题（用户已选方向 B = recommended 答案）

| # | 议题 | 选择 |
|---|---|---|
| Q1 | 签名机制 | ✅ Scheme C：RSA-PSS-SHA256 + 客户码 HMAC |
| Q2 | 私钥保管 | ✅ A：主作者 USB×2 + 1Password + 保险柜 |
| Q3 | 包格式 | ✅ A：ZIP 后缀 `.tjvplugin` |
| Q4 | 资源超限处理 | ✅ C：软警告（不强 kill） |
| Q5 | customer_code 注册流程 | ✅ A：仓库内 markdown + PR |
| Q6 | GPU 使用约束 | ✅ C+B：默认 CPU + manifest 声明才用 GPU |
| Q7 | source.py 主类是否开放给插件改 mixin | ✅ B：不开放（hook 即可） |
| Q8 | 加载失败的客户感知 | ✅ B：静默 + Settings 错误显示 |

### 1.2 design 03 — BUG-1 修复

- `backend/core/config.py:43` 的 `_fix_db_paths` 用了错的表名 `ml_models` → 必须改 `models`
- **v3.7 必修**

### 1.3 design 06 — 13 天工期

- v3.7 主程序前置改造 F1~F15 共 13 工作日（与 brief 原估算一致）

---

## 2. 🔴 P0 — 主作者必须拍板（阻塞 v3.7 实施）

### 2.1 公钥与密钥相关

| # | 议题 | 默认 | 需要拍板 |
|---|---|---|---|
| K1 | 私钥具体生成时机 | design 02 §8.1 一次性 | [确认] **现在** vs **进入实施前一周** |
| K2 | 私钥 PEM 密码长度 | design 02 §8.1 仅说"强密码" | [二选一] (a) 16 字符随机 (b) 24 字符随机 (c) Diceware passphrase |
| K3 | PLUGIN_SECRET 32 字节生成时机 | 主作者本地 `secrets.token_bytes(32)` | [确认] 现在生成并写到 1Password |
| K4 | PLUGIN_SECRET 在 CI 是否注入 | design 07 §6.2 用 GitHub Environment Secrets | [二选一] (a) 注入（CI 能签）(b) 不注入（CI 仅 pack 不签）|
| K5 | 公钥 valid_to 设多长 | design 02 §8.3 默认 4 年 | [二选一] (a) 4 年 (b) 7 年 |
| K6 | 应急轮换演练频率 | design 02 §九 SOP 写了但没排演 | [确认] **v3.7 上线后 1 个月内做一次**真实演练 |

### 2.2 customer_code 命名空间

| # | 议题 | 默认 | 需要拍板 |
|---|---|---|---|
| C1 | customer_code 正则 | `^[a-z][a-z0-9-]{2,19}$`（3~20 字符 / 小写） | [确认] |
| C2 | 保留 customer_code | design 08 §九"default 保留给内置示例" | [多选] (a) `default` (b) `tianjun` (c) `internal` (d) `test` |
| C3 | 客户名 → customer_code 映射 | 主作者主观取名 | [确认] 由主作者制定一份"取名 SOP"（如 acme corp → `acme`） |
| C4 | 注册表 PR 流程 | docs/plugin-system/customer-codes.md + PR | [确认] PR 必须 2 个 reviewer? |

### 2.3 Hook 接入点改造（前置 F1~F9）

| # | 议题 | 默认 | 需要拍板 |
|---|---|---|---|
| H1 | `_handle_cycle_end` 拆 8 phase 是否可接受？ | design 06 §6.4 拆函数 + 加 phase hook | [确认] 改造可能引入回归 bug，需要主作者 OK 这条路 |
| H2 | `session_end` / `box_complete` trigger 是否在 v3.7 同时实装 | design 06 §6.5/§6.6 顺便实装 | [二选一] (a) 一并实装（建议）(b) v3.8 再做 |
| H3 | `alarm_trigger` hook 支持 cancel | hook 返回 False 阻止报警 | [确认] 客户能否绕过/夜间静音，是否合规风险？ |
| H4 | 各 hook 的 `max_duration_ms` 默认 | 5000 ms（仅警告，不 kill） | [确认] |
| H5 | 后台线程崩溃**不**自动重启 | design 06 §7.4 | [确认] |

### 2.4 主程序前置改造范围

| # | 议题 | 默认 | 需要拍板 |
|---|---|---|---|
| F1 | F1~F15 共 15 项前置改造 | design 06 §九 13 工作日 | [确认] 排期与人手 |
| F2 | F10 layout 菜单数据驱动改造 | 把 `<router-link>` 硬编码改 v-for | [确认] 主菜单从 design 04 §3.4 RESERVED 集合的话语权 |
| F3 | F11 PluginManager 自身 | 3 工作日 | [确认] |
| F4 | F13 Settings 页插件管理 UI | 2 工作日 | [确认] 由谁实现？前端 1 人？ |
| F5 | F14 BUG-1 修复 | 一行字符串改 | ✅ **2026-05-09 已修复**（`backend/core/config.py:57` ml_models→models） |
| ~~F6~~ | ~~F15 mes_gateway 路径不一致~~ | ~~inventory 05 INCONSIST-2~~ | ✅ **2026-05-09 已验证为误报**，不需要修改 |

### 2.5 默认 RESERVED 不可隐藏菜单

| # | 议题 | 默认 | 需要拍板 |
|---|---|---|---|
| R1 | 不可隐藏的菜单 | design 04 §3.4 `{/monitor, /settings, /activation}` | [多选] 是否还要加 `/data`?（影响：客户能否禁用数据页） |

---

## 3. 🟠 P1 — 强烈建议拍板（影响实施方向）

### 3.1 vendor bundle 工程化（design 05 §4.4）

| # | 议题 | 默认 | 需要拍板 |
|---|---|---|---|
| V1 | vendor 共享方式 | importmap（推荐）/ window.__pluginVendor（备选）| [二选一] 两套方案各有取舍，需要工程师试跑确认 importmap 在 Electron 32 file:// 下表现 |
| V2 | vendor build 工程化 | 加一条 `vite.vendor.config.js` + 双 build | [确认] CI 改动量 1d，长期维护成本？ |
| V3 | vue / element-plus 版本对齐 | manifest 里**没有强校验** | [二选一] (a) 加 `requires.frontend_packages.vue.version` 强校验 (b) 仅文档说明，靠插件作者自觉 |

### 3.2 PG 迁移路径（design 03 §七）

| # | 议题 | 默认 | 需要拍板 |
|---|---|---|---|
| P1 | v3.7 PG 双跑实验阶段 | 不在 v3.7 做 | [确认] |
| P2 | v3.8 引入 SQLALCHEMY_DATABASE_URI 环境变量 | 双 dialect | [确认] 时间表 |
| P3 | v4.0 PG 默认 + Inno Setup 内置 portable PG | 计划 | [确认] |
| P4 | v5.0 移除 SQLite | 计划 | [确认] |
| P5 | PG schema 隔离 plugin_acme 模式 | v5.0 才上 | [二选一] (a) v4.0 上 (b) v5.0 上（默认） |

### 3.3 错误隔离 / 错误码

| # | 议题 | 默认 | 需要拍板 |
|---|---|---|---|
| E1 | 33 错误代码用户友好解释 | design 08 §七 表格 | [确认] 是否要把这些代码 i18n 翻译到 5 种语言？ |
| E2 | 验签错误统一报 `PLUGIN_SIGNATURE_FAIL` | 不暴露细节给攻击者 | [确认] 但是给运维的 backend log 是详细的 |
| E3 | 加载超时 10 秒 | design 05 §4.5 `PLUGIN_LOAD_TIMEOUT_MS` | [确认] |

### 3.4 主程序硬编码 CSS 重构（design 04 §3.1）

| # | 议题 | 默认 | 需要拍板 |
|---|---|---|---|
| T1 | 三阶段重构计划 | v3.7 加 `--tj-*` 变量 / v3.8 渐进 / v4.0 完成 | [二选一] (a) 一次性 v3.7 重构（高风险）(b) 三阶段（默认） |
| T2 | tier 1 插件能改的颜色范围 | v3.7 阶段仅 Element Plus 跟随 | [确认] 客户对此期望管理 |

### 3.5 安装与升级

| # | 议题 | 默认 | 需要拍板 |
|---|---|---|---|
| I1 | 安装包大小限制 | 200 MB（默认）/ 500 MB（runtime.allow_large=true）| [确认] |
| I2 | 升级保留 active 状态 | design 07 §9.1 是 | [确认] 升级失败回滚策略？ |
| I3 | 降级允许 + 警告 | design 07 §9.2 是 | [确认] |

### 3.6 单插件激活 vs 多插件

| # | 议题 | 默认 | 需要拍板 |
|---|---|---|---|
| M1 | v3.7 单插件激活 | design 00 第二节确认 | [确认] |
| M2 | 多插件评估时机 | v4.5 | [二选一] (a) v4.5 (b) v5.0 |

---

## 4. 🟡 P2 — 实施 PR 阶段决定（细节级）

### 4.1 design 01 manifest 字段细节

| # | 议题 | 默认 |
|---|---|---|
| MA1 | `runtime.cpu_threshold_warn_pct` 默认 | 30%（拍脑袋） |
| MA2 | `runtime.memory_threshold_warn_mb` 默认 | 500 MB（拍脑袋） |
| MA3 | `runtime.background_thread_max_count` 默认 | 3（拍脑袋） |
| MA4 | manifest 总大小限制 | 256 KB |
| MA5 | 错误代码 33 个是否齐全 | 看实施时是否还要加 |

### 4.2 design 02 签名细节

| # | 议题 | 默认 |
|---|---|---|
| SG1 | RSA 密钥长度 | 4096 bit（生产强度）|
| SG2 | RSA-PSS salt 长度 | 32 byte（与 SHA256 输出一致）|
| SG3 | files_digest 算法 | SHA256 |
| SG4 | files_digest 排除项 | `__pycache__` / `.git` / `node_modules` / `.DS_Store` 等 |
| SG5 | signature.bin 大小 | 563 ~ 1024 byte |

### 4.3 design 04 tier 1 细节

| # | 议题 | 默认 |
|---|---|---|
| TH1 | 客户 CSS 大小限制 | 200 KB |
| TH2 | Logo 文件大小限制 | 50 MB（assets 端点）|
| TH3 | theme.css 是否支持 `@import` | 不支持（单文件交付）|
| TH4 | 主题是否支持 dark/light 双图 | 不支持（单图，暗色为底）|

### 4.4 design 05 tier 2 细节

| # | 议题 | 默认 |
|---|---|---|
| UI1 | 加载超时 | 10 秒 |
| UI2 | host 暴露的 API 表面 | systemStore / projectStore / navigate / electron / getConfig / setConfig（design 05 §3.3）|
| UI3 | 是否支持插件路由懒加载 | v3.7 不（直接 component） |
| UI4 | 是否支持菜单子菜单（嵌套） | v3.7 不（一层菜单） |
| UI5 | Navbar/BottomBar 插槽 | v3.7 不做 |

### 4.5 design 06 tier 3 细节

| # | 议题 | 默认 |
|---|---|---|
| BE1 | 模块隔离方式 | 部分（独立 mod_name + 共享 sys.modules）|
| BE2 | 是否提供 axios-like HTTP client 给插件 | 不（用 stdlib `requests`）|
| BE3 | 是否做插件 GPU 监控 | v3.7 不 |
| BE4 | 是否支持插件间通信 | v3.7 不（单插件激活）|
| BE5 | 是否记录 hook 调用栈 | 仅错误时（性能）|
| BE6 | 后台线程数上限 | 3（manifest.runtime 可调）|
| BE7 | quarantined 自动触发 | 3 次连续失败 |

### 4.6 design 07 工具链细节

| # | 议题 | 默认 |
|---|---|---|
| TL1 | 工具脚本目录 | `scripts/plugin/`（与现有 build 脚本分开）|
| TL2 | dev mode 双开关 | `DEBUG_MODE=1` + `PLUGIN_DEV_MODE=1`，Nuitka build 移除 |
| TL3 | customer-codes.md 注册检查 | 仅警告（小规模 ≤ 50 客户）|

---

## 5. ⚪ P3 — 推迟项（v3.8+ 再议）

### 5.1 设计层面留下的开放点

| # | 议题 | 来源 | 推迟到 |
|---|---|---|---|
| L1 | manifest_version=2 升级时机 | design 01 §八 | v4.0 评估 |
| L2 | sig_metadata 是否签进 RSA | design 02 S7 | v2 manifest |
| L3 | 多签名 multi-signer | design 02 S2 | v5.0+ |
| L4 | TSA 时间戳服务 | design 02 S3 | 不做 |
| L5 | 公钥列表运行时增量加载 | design 02 S5 | 不做（hotfix 替代）|
| L6 | 每机独立 PLUGIN_SECRET | design 02 S6 | 不做 |
| L7 | 替换主程序路由 | design 05 U4 | v4.0 |
| L8 | 多插件共存 | design 05 U5 | v4.5 |
| L9 | 热卸载（不重启）| design 05 U6 | v4.5 评估（i18n 限制） |
| L10 | tier 1 主题运行时切换 | design 04 §11 | v3.8 评估 |
| L11 | Splash 替换 | design 04 §3.2.3 | 不做 |

### 5.2 主程序工程债

| # | 议题 | 来源 | 推迟到 |
|---|---|---|---|
| D1 | style.css 硬编码颜色完全替换 var | design 04 §3.1 | v4.0 |
| D2 | 大文件拆分（Monitor/index.vue 等）| inventory 05 §九 | v3.8+ |
| D3 | 5 项 API 路径不一致清理 | inventory 05 §五 | v3.8 |
| ~~D4~~ | ~~mes_gateway 路径不一致~~ | ✅ 2026-05-09 已验证为误报 | — |

---

## 6. 📋 review 时建议的会议节奏

### 第一轮（2 小时）— P0 决策 + 实施前置

```
1. K1~K6   公钥/密钥系列          (30 min)
2. C1~C4   customer_code 命名      (15 min)
3. H1~H5   Hook 接入改造           (30 min)
4. F1~F6   主程序前置改造排期      (30 min)
5. R1      RESERVED 不可隐藏菜单   (15 min)

输出: 一份 P0 决策记录 → 推到 design/REVIEW_DECISION.md
```

### 第二轮（1.5 小时）— P1 实施方向

```
1. V1~V3   vendor bundle 工程化    (30 min)
2. P1~P5   PG 迁移路线             (30 min)
3. T1~T2   主程序 CSS 重构         (15 min)
4. I1~I3   安装升级策略            (15 min)
```

### 第三轮（异步评审，1 周）— P2 + P3

```
- 全员阅读各章节"待确认开放点"
- GitHub PR 上 inline 评论
- 主作者汇总→拍板→更新 design/*.md
```

---

## 7. ✅ 实施触发条件

只要 §2 P0 项**全部拍板**，就可以开始实施 v3.7。

实施第一周建议任务：

```
Day 1  ─ F14 BUG-1 修复 (一行改, 单独 commit)
        ─ 主作者本地生成 RSA 密钥对 + PLUGIN_SECRET (K1, K3)
        ─ 写 docs/plugin-system/customer-codes.md (C2, C3)
Day 2  ─ F11 PluginManager 骨架 (无加载, 仅扫描+verify)
        ─ design 03 §十 ORM 表落库
Day 3  ─ F1 _handle_cycle_end 拆 8 phase
        ─ unit test
Day 4  ─ F2 session_end 触发点
        ─ F3 box_complete 触发点
Day 5  ─ F4~F6 scan/event/alarm hook 接入
Day 6  ─ F7~F9 export 三件套 registry
Day 7  ─ F10 Layout 菜单 + main.js + i18n key 补
Day 8  ─ F12 plugins API 端点
Day 9  ─ F13 Settings 插件 UI
Day 10 ─ tier1-acme-theme demo
Day 11 ─ tier2-acme-ui demo
Day 12 ─ tier3-acme-fullstack demo
Day 13 ─ 集成测试 + 渗透测试 + 文档
```

---

## 8. 一份"我现在就需要主作者回的 5 个问题"

如果你觉得 §2 太多，**最影响进度的 5 个问题**（按依赖关系排序）：

```
[P0] K1   什么时候生成主签名密钥? — 现在 / 实施前一周 / 实施第 5 天?
[P0] C3   customer_code 第一批 (default + tianjun + internal + test 等) 现在写下来?
[P0] H1   _handle_cycle_end 拆 8 phase, 主作者是否担心回归 bug, 有没有更安全的路?
[P0] F5   BUG-1 修复 (ml_models → models) 是否今天就独立 commit?
[P1] V1   vendor bundle 用 importmap 还是 window 全局, 让工程师 1 天跑通其中一个?
```

---

## 9. 附录 — design 文档章节速查

如果你看到某个标题想跳到原文：

```
K1~K6  → design/02_signature.md §八/§九
C1~C4  → design/01_manifest_schema.md §3.1, design/00_overview.md Q5
H1~H5  → design/06_tier3_fullstack.md §六
F1~F15 → design/06_tier3_fullstack.md §九
R1     → design/04_tier1_theme.md §3.4
V1~V3  → design/05_tier2_ui.md §4.4
P1~P5  → design/03_database.md §七
E1~E3  → design/08_examples.md §七, design/02_signature.md §七
T1~T2  → design/04_tier1_theme.md §3.1
I1~I3  → design/07_distribution.md §五/§九
M1~M2  → design/00_overview.md 第二节
L1~L11 → 各 design 章节 "开放点 / 推迟项"
```

---

**本文最后更新**：2026-05-08
**Review 完后**：把决策结果写到 `docs/plugin-system/REVIEW_DECISION.md`，并把对应 design/*.md 中的"待确认 / 默认"标记替换为最终值
