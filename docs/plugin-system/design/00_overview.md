# 00 — 插件系统总体设计骨架

> 适用版本：基于 v3.6.0 + `feat/plugin-config` 分支
> 本文目的：在动手写代码前，把"插件系统应该长什么样"的设计骨架定下来——包括三档边界、整体架构、技术决策、命名空间约定、加载流程、错误隔离、8 步里程碑分解。**故意不下钻具体字段**——具体定义留给 01~08 设计文档。
>
> 阅读顺序：第二节"三档边界" → 第三节"整体架构图" → 第十一节"开放问题"是必看，看完后再下钻具体章节。
>
> 配套：`inventory/01~05.md`（梳理产出）/ `design/01~08.md`（细化文档，待写）。

---

## 一、设计目标 + 反目标

### 1.1 设计目标（要做什么）

| 目标 | 说明 | 量化 |
|---|---|---|
| **最小破坏主线** | 客户已装 v3.5.x，插件系统不能让升级失败 | 升级测试 100% 通过 |
| **错误隔离** | 任何插件崩了不能让主程序起不来 | 单元测试覆盖 |
| **三档分级** | 主题 / UI / 全栈 三档明确边界 | 见第二节 |
| **客户级隔离** | 一次只跑一个客户的插件，customer_code 唯一 | brief 已敲定 |
| **签名验证** | 防止客户私自改、防止串货 | 见第十节方案 |
| **简单分发** | 客户拖 .tjvplugin 到目录即生效 | 不做插件市场 |
| **复用现有扩展点** | 优先用 `Project JSON / SystemConfig / mes_adapters._REGISTRY` 等已有机制 | inventory 03 文档 30 个扩展点 |
| **错误信息友好** | 出问题客户能看懂（中文） | 提示文案统一 |

### 1.2 反目标（不做什么）

| 反目标 | 原因 |
|---|---|
| ❌ 不做 Python 沙箱 | brief 已敲定；做沙箱工作量大、性能差、且签名机制已能防大部分恶意 |
| ❌ 不做插件市场 | 客户分发走 U 盘 / 邮件，不需要 |
| ❌ 不做"同时启用多个客户插件" | brief 已敲定，customer_code 唯一，后装覆盖前装 |
| ❌ 不做插件热卸载 | FastAPI 不支持；卸载需要重启 |
| ❌ 不做插件市场化收费 / DRM | 客户已付完软件钱，插件是"客户专属定制" |
| ❌ 不做云端配置同步 | 工厂工控机普遍离线 |
| ❌ 不替换主程序 UI 核心 | Layout / Navbar / Monitor / Project 主视图不能被插件替换 |
| ❌ 不暴露主程序内部 ORM | 插件想改 DB 必须走 SystemConfig KV 或自家 `p_{customer_code}_*` 表 |

---

## 二、三档插件清晰边界

> brief 已敲定"三档全做"。本节把每档的**具体边界**画清楚。

### 档位 1：主题包（Theme Pack）

**能做什么**：
- ✅ CSS 变量覆盖（`--primary`, `--secondary`, `--bg-base` 等）
- ✅ Logo 替换（`<img src>` 路径覆盖）
- ✅ 菜单显隐（隐藏不需要的视图）
- ✅ 文案覆盖（i18n `mergeLocaleMessage`）
- ✅ 浏览器 favicon / app title 覆盖

**不能做什么**：
- ❌ 加新视图 / 新路由（那是档位 2）
- ❌ 加新 API / hook（那是档位 3）
- ❌ 改主视图的功能（只能改外观）
- ❌ 改 logic_mode / 检测逻辑（不属于皮肤范畴）

**典型客户场景**：
- "ACME 公司白标版"——把 logo / 颜色 / 标题全换成 ACME 自己的
- "把 'MES' 改名 'ERP 接口'"——文案覆盖
- "隐藏 '集群' 视图"——客户单机部署不需要

**包大小**：< 5 MB（仅 CSS + 图片 + JSON）

**实现复杂度**：低（依赖 03 文档 D3 + D4）

### 档位 2：UI 插件（UI Plugin）

**能做什么**：
- ✅ 档位 1 全部
- ✅ 加新 Vue 视图（独立页面）
- ✅ 加新路由（`/plugin/{customer_code}/{view}`）
- ✅ 加菜单项
- ✅ 自定义 Pinia store（`plugin-{customer_code}`）
- ✅ 调主程序 API（拉数据展示）
- ✅ 在 Settings 页加自定义 tab（受限：固定挂载点）

**不能做什么**：
- ❌ 加新后端 API（那是档位 3）
- ❌ 改主视图的内嵌组件（如 Monitor 的检测框、计数器面板）
- ❌ 注入自家 JS 到主视图运行时
- ❌ 用主程序未公开的内部数据结构

**典型客户场景**：
- "ACME 自家报表页"——拉主程序 API 数据 + 自家 ECharts 展示
- "客户专属操作员管理 UI"——比主程序操作员页更复杂的工作流
- "外部硬件状态监控页"——读 SystemConfig 显示

**包大小**：< 50 MB（含预编译 Vue 组件 + 资源）

**实现复杂度**：中（依赖 03 文档 D1 + D2 + D5）

### 档位 3：全栈插件（Full-stack Plugin）

**能做什么**：
- ✅ 档位 1 + 档位 2 全部
- ✅ 加新 FastAPI router（`/api/v1/plugins/{customer_code}/*`）
- ✅ 注册新 MES Adapter（`mes_adapters._REGISTRY`，03 文档 B1 已开放）
- ✅ 注册启动 / 关机生命周期 hook（C6）
- ✅ 注册 `_trigger_event` listener（C1，需先加 registry）
- ✅ 加新 ORM 表（`p_{customer_code}_*` 命名）
- ✅ 注册导出字段 / renderer / trigger（B5/B6/B7）
- ✅ 拦截报警 / 扫码事件（C5/C7）
- ✅ 启自家后台线程（如轮询客户 ERP）

**不能做什么**：
- ❌ 改主程序 ORM 表 schema
- ❌ 改主程序 API 端点行为
- ❌ 直接 spawn / kill 主程序进程
- ❌ 修改 Electron 主进程
- ❌ 直接打开串口 / GPU（必须走 alarm_router / channel_manager）

**典型客户场景**：
- "对接 ACME 自家 MES（MQTT 协议）"
- "客户专属硬件信号塔（继电器 + 客户协议）"
- "客户专属计数规则（`_handle_cycle_end` phase hook）"
- "客户专属导出格式（ESC/POS 打印小票）"

**包大小**：< 200 MB（含 Python 依赖 wheels + Vue + 资源）

**实现复杂度**：高（依赖 03 文档大部分扩展点 + 完整的 hook 注册机制）

---

## 三、整体架构图

```
┌─────────────────────────────────────────────────────────────────────────┐
│ 客户机文件系统                                                            │
│                                                                         │
│ %APPDATA%/tianjun-ai-vision/data/                                       │
│ ├── plugins/                            ← 插件根目录                       │
│ │   └── {customer_code}/                ← 当前激活插件 (一次只一个)         │
│ │       ├── manifest.json               ← 插件元数据 + 签名                │
│ │       ├── signature.bin               ← RSA-SHA256 签名                  │
│ │       ├── backend/                    ← Python 后端代码 (档位 3)         │
│ │       │   ├── __init__.py             ← register_plugin(app, hooks)     │
│ │       │   ├── routes.py               ← FastAPI router (可选)            │
│ │       │   ├── hooks.py                ← 各种 hook 注册                   │
│ │       │   ├── adapters.py             ← MES Adapter 注册 (可选)          │
│ │       │   └── models.py               ← ORM 表定义 (可选, p_xxx_*)       │
│ │       ├── frontend/                   ← 前端资源 (档位 1/2)              │
│ │       │   ├── theme.css               ← CSS 变量覆盖                     │
│ │       │   ├── i18n/                   ← 多语言文案                       │
│ │       │   │   ├── zh-CN.json                                            │
│ │       │   │   └── en-US.json                                            │
│ │       │   ├── components/             ← 预编译 Vue 组件 (.js ESM)        │
│ │       │   ├── views/                  ← 视图组件 (.js ESM)               │
│ │       │   └── assets/                 ← 图片 / 字体 / 音频                │
│ │       ├── templates/                  ← 自定义导出模板 (可选)             │
│ │       │   └── *.docx / *.xlsx                                          │
│ │       └── README.md                   ← 客户文档                         │
│ ├── plugin_state.json                   ← 插件运行时状态 (启停 / 错误日志)   │
│ └── ...其他用户数据                                                       │
└─────────────────────────────────────────────────────────────────────────┘
                              │ 启动时扫描 + 验签 + 加载
                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 后端 Python 进程 (uvicorn 8001)                                          │
│                                                                         │
│ backend/main.py                                                         │
│   ├─ 启动序列 1~12 (现有)                                                 │
│   ├─ ★ NEW: 启动序列 13: PluginManager.load_active_plugin()             │
│   │    │                                                                │
│   │    ├─ 扫 plugins/ 找当前激活插件 (一次一个, 由 plugin_state.json 决定) │
│   │    ├─ 验签 (RSA-SHA256, 公钥嵌主程序)                                │
│   │    ├─ 校验 customer_code 与 license.customerName 匹配                │
│   │    ├─ 校验 main_version_min/max                                     │
│   │    ├─ importlib.util 独立 namespace 加载 backend/__init__.py        │
│   │    ├─ 调 register_plugin(app, hooks) 完成注册:                       │
│   │    │    - app.include_router(...)            (档位 3)               │
│   │    │    - mes_adapters.register_adapter(...) (档位 3)               │
│   │    │    - _trigger_event.register_post_hook(...)                    │
│   │    │    - mes_hook.register_phase_hook(...)                         │
│   │    │    - 启动后台线程 (插件 main thread)                            │
│   │    └─ 错误时落 plugin_state.json + 不阻塞主启动                      │
│   │                                                                     │
│   └─ 现有路由 + 插件路由统一暴露                                          │
└─────────────────────────────────────────────────────────────────────────┘
                              │ HTTP /api/v1/plugins/{customer_code}/*
                              │ + 新菜单 / 新视图 (前端启动时拉 manifest)
                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 前端 Renderer (Vue3 + Vite build)                                       │
│                                                                         │
│ frontend/src/main.js                                                    │
│   ├─ createApp() + 现有插件注册                                            │
│   ├─ ★ NEW: await PluginLoader.loadActivePlugin()                       │
│   │    │                                                                │
│   │    ├─ fetch /api/v1/plugins/active/manifest                          │
│   │    ├─ 拉 frontend/i18n/{lang}.json → i18n.mergeLocaleMessage        │
│   │    ├─ 拉 frontend/theme.css → <link rel="stylesheet">                │
│   │    ├─ 注册插件路由 (router.addRoute)                                 │
│   │    │   ├─ 动态 import frontend/views/*.js                            │
│   │    │   └─ 异常时显示"插件视图加载失败"占位                              │
│   │    ├─ 注册菜单 (Pinia plugin store)                                  │
│   │    └─ 错误时落 sessionStorage 错误日志 + 不阻塞主前端启动              │
│   │                                                                     │
│   └─ app.mount('#app')                                                  │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 四、技术决策树

### 4.1 brief 已敲定的（不再讨论）

| 决策 | 值 |
|---|---|
| 三档插件 | 全做 |
| Python 沙箱 | 不做 |
| 同时激活的插件数 | 1（customer_code 唯一） |
| 后装策略 | 后装覆盖前装 |
| 分发方式 | U 盘 / 邮件，不做市场 |
| 部署 | Inno Setup 主包不动，客户拖 `.tjvplugin` 到 `plugins/` |
| 版本兼容 | manifest 写 main_version_min/max，加载时校验 |
| 数据库 | 迁 PG（与本系统并行） |
| 加密签名 | HMAC + 私钥（详见第十节技术澄清） |

### 4.2 本设计敲定的（基于 brief + inventory）

| 决策 | 值 | 理由 |
|---|---|---|
| 路由前缀 | `/api/v1/plugins/{customer_code}/*` | 避开现有路由（详见 inventory 04 INCONSIST-1） |
| 插件目录 | `%APPDATA%/.../data/plugins/{customer_code}/` | 与现有 `data/` 同级，不污染主程序 |
| 一次激活的插件数 | 1（强约束） | 多个会带来命名冲突 + 性能开销 |
| 配置存储 | `SystemConfig` KV，命名 `plugin.{customer_code}.{key}` | 复用现有机制（inventory 03 A8） |
| 插件 ORM 表前缀 | `p_{customer_code}_{table}` | 避免与主程序表冲突 |
| 错误隔离粒度 | 单个 hook 失败不影响其他 hook、整个插件失败不影响主程序 | inventory 03 第 6.4 节 |
| 加载时机 | 主程序启动序列末尾 (在 _init_mes_services 之后) | 主服务先就绪再加插件 |
| 加载顺序 | 启动 hook 按 priority（0=最先 / 1000=最后） | 同 inventory 03 C6 |
| 卸载策略 | 不支持热卸载，需重启主程序 | FastAPI 限制 |
| 启停切换 | `plugin_state.json` 标记，下次启动生效 | 简单 |
| 签名算法 | RSA-2048 + SHA256（与 License 一致） | 复用 License 的密钥基础设施 |
| 客户码绑定 | manifest 内含 `customer_code`，加载时与 license.customerName 校验 | brief 防串货要求 |

### 4.3 待用户拍板的（详见第十一节"开放问题"）

| 待澄清 | 选项 | 推荐 |
|---|---|---|
| 签名算法的"HMAC + 私钥" | A. 双重签名 / B. 仅 RSA / C. RSA + 客户码 HMAC | C |
| 私钥保管 | A. 主作者私人保管 / B. 仓库加密保管 / C. 硬件 HSM | A（暂时） |
| 插件包格式 | A. ZIP / B. tar.gz / C. 自定义二进制 | A（最简） |
| 插件后台线程是否走 GIL 同进程 | A. 同进程 / B. subprocess | A（性能） |
| 前端动态 import 方案 | A. import map（Vite 5+） / B. URL 拼接 / C. 预打包 lib | C（最稳） |

---

## 五、目录约定与命名空间

> **强制规则**——违反即不加载。

```
customer_code 规则:
  - 小写英文字母 + 数字 + 连字符
  - 长度 3 ≤ len ≤ 20
  - 必须以字母开头
  - 全局唯一 (主作者维护一个 customer_code 注册表)
  - 例: acme / acme-tj / xyz-2025
```

| 资源 | 命名空间 | 示例 |
|---|---|---|
| 插件目录 | `plugins/{customer_code}/` | `plugins/acme/` |
| HTTP 路由 | `/api/v1/plugins/{customer_code}/*` | `/api/v1/plugins/acme/report` |
| 前端路由 path | `/plugin/{customer_code}/{view}` | `/plugin/acme/report` |
| 前端路由 name | `Plugin-{customer_code}-{view}` | `Plugin-acme-report` |
| Vue 组件 prefix | `Plugin{CustomerCode}{View}` | `PluginAcmeReport` |
| Pinia store id | `plugin-{customer_code}` 或 `plugin-{customer_code}-{slot}` | `plugin-acme` |
| i18n 键根 | `plugin.{customer_code}.*` | `plugin.acme.title` |
| SystemConfig 键 | `plugin.{customer_code}.{key}` | `plugin.acme.theme_color` |
| ORM 表名 | `p_{customer_code}_{table}` | `p_acme_orders_extra` |
| ORM 类名 | `Plugin{CustomerCode}{Class}` | `PluginAcmeOrderExtra` |
| 导出字段 | `plugin.{customer_code}.{field}` | `plugin.acme.erp_id` |
| MES Adapter 注册名 | `plugin-{customer_code}-{proto}` | `plugin-acme-mqtt` |
| 菜单 path | `/plugin/{customer_code}/{view}` | 同前端路由 |
| Python module 前缀 | `tianjun_plugin_{customer_code}` | `tianjun_plugin_acme` |

**违规例子**：
- ❌ `plugins/ACME/`（大写）
- ❌ `plugins/acme!/`（特殊字符）
- ❌ `plugins/ab/`（< 3 字符）
- ❌ `/api/v1/plugins/acme/orders` 内部又写 `/api/v1/orders`（污染顶层）

**例外** — `customer_code = "default"` 保留给"主程序自带的内置示例插件"，用于演示和测试。

---

## 六、加载流程时序

### 6.1 主程序启动时

```
backend/main.py
  ├─ 现有启动序列 1~12 (见 inventory 02 阶段 0)
  ├─ ★ NEW: PluginManager.load_active_plugin() (新加 1 步)
  │    ├─ 1. 读 plugin_state.json 找当前激活插件
  │    │    if not enabled: return  (无插件就跳过)
  │    ├─ 2. 读 plugins/{customer_code}/manifest.json
  │    │    if 文件缺失 / JSON 解析失败: 标记 error + 跳过
  │    ├─ 3. 验签 (signature.bin + manifest.json)
  │    │    if 签名不通过: 标记 error + 跳过 + 日志报警
  │    ├─ 4. 校验 customer_code = license.customerName
  │    │    if 不匹配: 标记 error + 跳过 (防串货)
  │    ├─ 5. 校验 main_version_min ≤ v3.6.0 ≤ main_version_max
  │    │    if 不匹配: 标记 error + 跳过
  │    ├─ 6. 校验依赖 (Python wheel / 系统库)
  │    ├─ 7. 校验 customer_code 命名规范
  │    ├─ 8. importlib.util.spec_from_file_location 隔离加载
  │    │    backend/__init__.py
  │    ├─ 9. 调 register_plugin(app, registry) 钩子点
  │    │    - app.include_router(...) 注册路由
  │    │    - mes_adapters.register_adapter(...) 注册 adapter
  │    │    - lifecycle.register_startup_hook(...)
  │    │    - mes_hook.register_phase_hook("workpiece_set_result", "after", ...)
  │    │    - _trigger_event.register_post_hook(...)
  │    │    - 启动插件自家后台线程
  │    │    - 注册自家 ORM 表 (Base.metadata.create_all)
  │    ├─ 10. 调用插件的 startup hook (priority 排序)
  │    │     errors are isolated, 单个 hook 失败不影响其他
  │    └─ 11. plugin_state.json 写 status='loaded'
  └─ 现有启动序列继续 (uvicorn 起 server)

异常路径: 任何步骤失败
  → log.error + plugin_state.json 写 status='error', error_msg=...
  → continue 主启动 (插件挂了不影响主程序起来 ★ AGENTS.md 第三节硬性要求)
```

### 6.2 前端启动时

```
frontend/src/main.js
  ├─ createApp() + 现有插件注册 (Pinia / Router / ElementPlus / i18n)
  ├─ ★ NEW: await PluginLoader.loadActivePlugin()
  │    ├─ 1. fetch /api/v1/plugins/active/manifest
  │    │    if 404 (无插件): return
  │    │    if 500 (后端报错): 静默 + log
  │    ├─ 2. 拉 theme.css 并 inject 到 <head>
  │    │    <link rel="stylesheet" href="/api/v1/plugins/{cc}/static/theme.css">
  │    ├─ 3. 拉 i18n/{当前 locale}.json
  │    │    i18n.global.mergeLocaleMessage(locale, messages)
  │    ├─ 4. 注册路由
  │    │    for route in manifest.routes:
  │    │      router.addRoute('Layout', {
  │    │        path: `plugin/${cc}/${route.path}`,
  │    │        name: `Plugin-${cc}-${route.name}`,
  │    │        component: () => loadPluginComponent(cc, route.component)
  │    │      })
  │    ├─ 5. 注册菜单 (Pinia plugin store 暴露 pluginMenus)
  │    └─ 6. 注册 Logo / Favicon 覆盖 (如果 manifest.theme.logo)
  ├─ app.mount('#app')

异常路径: 任何步骤失败
  → console.error
  → sessionStorage 写错误日志 (用户在 Settings 插件页能看)
  → 主前端继续 mount, 不阻塞
```

---

## 七、插件生命周期 + 状态机

```
                      ┌──────────────┐
            手动上传    │  installed   │  插件包在 plugins/{cc}/, plugin_state 标 enabled=False
                ──────►│  (disabled)  │
                      └──────┬───────┘
                             │ 用户在 Settings 启用
                             │ (单插件约束: 启用前先 disable 其他)
                             ▼
                      ┌──────────────┐
                      │  activated   │  下次启动会被加载
                      │  (pending    │
                      │   load)      │
                      └──────┬───────┘
                             │ 主程序重启
                             ▼
        ┌─────────────────┐  │
        │ load 流程 6.1   │◄─┘
        └────┬────────┬───┘
   verify  │        │ verify
   pass    ▼        ▼  fail
       ┌────────┐ ┌──────────────┐
       │ loaded │ │ error        │  manifest 写 error_msg
       │        │ │              │  Settings 页能看到
       └────┬───┘ └──────┬───────┘
            │            │ 用户上传新版本 / 修复
            │            │
            │            ▼
            │     ┌──────────────┐
            │     │ installed    │ (reset, 重启可重试)
            │     └──────────────┘
            │
            │ 用户禁用
            ▼
       ┌────────────┐
       │ disabling  │  下次启动不加载
       │ (pending   │
       │  unload)   │
       └────────────┘
            │ 主程序重启
            ▼
       ┌────────────┐
       │ disabled   │  插件文件还在, 但不加载
       └────────────┘
            │
            │ 用户卸载 (彻底删除)
            ▼
       ┌────────────┐
       │ uninstalled│  插件目录被删
       └────────────┘
```

---

## 八、错误隔离总策略

### 8.1 五层防御

```
[1] 加载层    : 验签失败 / 版本不符 / customer_code 不匹配
              ↓ 拒绝加载, 不影响主程序

[2] 注册层    : import 时崩溃 / 调 register_plugin 抛异常
              ↓ try/except 包住, 标记 error 状态, 跳过该插件

[3] 调用层    : 插件 hook 函数被调用时抛异常
              ↓ 单个 hook 调用失败不影响其他 hook、不影响主流程
              ↓ 落日志到 plugin_state.json

[4] 资源层    : 插件后台线程占用过多 / 阻塞 GIL
              ↓ 监控 hook 执行时长, 超 5s 报警 (慢 hook 警告)
              ↓ 插件后台线程标 daemon=True, 主程序退出时自动清

[5] 数据层    : 插件写 SystemConfig / 插件表 出错
              ↓ DB 事务回滚, 不污染主程序数据
```

### 8.2 异常吞吐策略

| 场景 | 默认 | 备选 |
|---|---|---|
| `register_plugin` 抛异常 | 标插件 error，跳过加载 | 不可重试 |
| 插件 startup_hook 抛异常 | 单 hook 跳过，其他 hook 继续 | 不可重试 |
| 插件 cycle_end_hook 抛异常 | 静默吞，错误率超阈值后禁用插件 | 落日志 |
| 插件 router 端点抛异常 | FastAPI 默认 500，不影响主程序 | 同主程序 |
| 插件后台线程抛异常 | 线程内自捕获，循环继续 | 标插件 unhealthy |
| 插件 ORM 写库失败 | 事务回滚，错误标记 | 不可重试 |

### 8.3 监控与可观察性

```
plugin_state.json:
{
  "active_customer_code": "acme",
  "plugins": {
    "acme": {
      "status": "loaded",          // installed | loaded | error | disabling | disabled
      "version": "1.2.3",
      "loaded_at": "2026-05-08T05:00:00Z",
      "main_version_min": "3.6.0",
      "main_version_max": "3.6.x",
      "tier": 3,
      "error_msg": null,
      "stats": {
        "hooks_called": 1234,
        "hooks_errors": 5,
        "last_error": "2026-05-08T04:55:00Z: ...",
        "router_requests": 100,
        "background_threads": 1
      }
    }
  }
}
```

**前端 Settings 插件页**：
- 当前激活插件名称 + 版本 + 状态
- 错误次数（今日 / 累计）
- 最近 10 条错误日志
- 启停按钮 / 卸载按钮 / 上传新版本

---

## 九、版本兼容性

### 9.1 版本号规范

主程序：`v{major}.{minor}.{patch}`，遵循 SemVer。

插件：`v{major}.{minor}.{patch}` 独立，不与主程序绑定。

### 9.2 manifest 中的版本字段

```json
{
  "main_version_min": "3.6.0",     // 兼容的最低主程序版本
  "main_version_max": "3.x",       // 兼容的最高主程序版本（可省略=无上限）
  "plugin_version": "1.2.3",
  "manifest_version": 1            // manifest schema 版本（向后兼容）
}
```

### 9.3 版本不匹配时的行为

| 情况 | 行为 |
|---|---|
| 主程序版本 < `main_version_min` | 拒绝加载（提示用户升级主程序） |
| 主程序版本 > `main_version_max` | 拒绝加载（提示用户更新插件） |
| 主程序版本在范围内 | 加载 |
| `manifest_version` 高于主程序支持的最高版本 | 拒绝加载（提示主程序太旧） |

### 9.4 主程序升级时的兼容性保证

- **patch 版本升级**（v3.6.0 → v3.6.1）：保证插件 ABI 不变，已加载插件继续生效
- **minor 版本升级**（v3.6.x → v3.7.0）：可能加新 hook 点，老插件继续生效
- **major 版本升级**（v3.x → v4.0）：可能 break ABI，老插件需 main_version_max 校验拒绝加载

---

## 十、签名 / 客户码绑定方案（推荐）

### 10.1 brief 原文 + 技术澄清

brief 写："**HMAC + 私钥签名, manifest 绑客户码防串货**"

技术澄清：HMAC 和私钥签名是两种不同机制，brief 真实意图猜测有 3 种可能：

| 方案 | 解释 | 评估 |
|---|---|---|
| A. 双重签名（HMAC + RSA） | 主程序内嵌 HMAC 共享密钥 + RSA 公钥；插件包同时附 HMAC 摘要 + RSA 签名 | 过度复杂，HMAC 共享密钥嵌入主程序就等于公开 |
| B. 仅 RSA 私钥签名 | 主作者用 RSA 私钥签名 manifest + 文件摘要；主程序用公钥验签 | 简单，与 License 系统一致 |
| C. RSA + 客户码 HMAC（**推荐**） | RSA 验官方身份；HMAC 用客户码 + machineId 派生密钥校验"插件 + 客户匹配" | 防串货 + 简单 |

### 10.2 推荐方案 C 详解

**签名生成端**（主作者打包工具）：

```python
# pack-plugin.py + sign-plugin.py

# 1. 计算文件摘要
files_digest = sha256_of_all_files_in_plugin_dir()

# 2. 计算 manifest digest (含 customer_code, files_digest)
manifest_with_digest = {**manifest, "files_digest": files_digest}
manifest_bytes = json.dumps(manifest_with_digest, sort_keys=True).encode()

# 3. RSA 私钥签名 manifest
rsa_signature = rsa_sign(manifest_bytes, PRIVATE_KEY)

# 4. 客户码 HMAC (派生 key 防串货)
hmac_key = hmac.new(
    key=CUSTOMER_HMAC_MASTER_KEY,    # 主作者保管的主密钥
    msg=customer_code.encode(),
    digestmod=hashlib.sha256
).digest()
hmac_signature = hmac.new(
    hmac_key, manifest_bytes, hashlib.sha256
).hexdigest()

# 5. 写入 plugin/signature.bin
with open("signature.bin", "wb") as f:
    f.write(rsa_signature)
    f.write(b"\n")
    f.write(hmac_signature.encode())
```

**签名验证端**（主程序加载时）：

```python
# 主程序启动时:
def verify_plugin(plugin_dir, license_customer_name):
    # 1. 读 manifest + signature
    manifest = load_json(plugin_dir / "manifest.json")
    signature_data = (plugin_dir / "signature.bin").read_bytes()
    rsa_sig, hmac_sig = signature_data.split(b"\n", 1)

    # 2. 重算 files_digest
    files_digest = sha256_of_all_files_in_plugin_dir()
    if files_digest != manifest["files_digest"]:
        raise PluginVerifyError("文件被篡改")

    # 3. 重算 manifest_bytes
    manifest_bytes = json.dumps(manifest, sort_keys=True).encode()

    # 4. RSA 验签
    if not rsa_verify(manifest_bytes, rsa_sig, PUBLIC_KEY_EMBEDDED_IN_MAIN):
        raise PluginVerifyError("签名验证失败")

    # 5. customer_code 与 license 匹配
    if manifest["customer_code"] != license_customer_name:
        raise PluginVerifyError(f"插件不匹配本机授权 (license={license_customer_name}, plugin={manifest['customer_code']})")

    # 6. HMAC 验证 (主程序也保管 CUSTOMER_HMAC_MASTER_KEY 但**不**直接暴露)
    expected_hmac_key = hmac.new(
        CUSTOMER_HMAC_MASTER_KEY_EMBEDDED, manifest["customer_code"].encode(), hashlib.sha256
    ).digest()
    expected_hmac = hmac.new(
        expected_hmac_key, manifest_bytes, hashlib.sha256
    ).hexdigest()
    if expected_hmac != hmac_sig.decode():
        raise PluginVerifyError("HMAC 验证失败")

    return True
```

**复杂度 vs 安全性权衡**：

| 攻击场景 | 方案 B (仅 RSA) | 方案 C (RSA + HMAC) |
|---|---|---|
| 客户私自改 manifest | ✅ 阻止 | ✅ 阻止 |
| 黑客伪造插件 | ✅ 阻止（无私钥） | ✅ 阻止 |
| 客户 A 把插件给客户 B | ❌ 无法阻止（manifest 不含 license 校验）| ✅ 阻止（HMAC 派生失败 + customer_code 校验）|
| 客户提取主程序内嵌的 RSA 私钥 | RSA 私钥不在主程序里（只有公钥），无法 | 同 |
| 客户提取主程序内嵌的 HMAC master key | N/A | 风险存在；可用 .pyd 编译 + 字符串混淆缓解 |

**核心结论**：方案 C 在不显著提升复杂度的前提下，多了一道"客户码绑定"防线，**推荐采用**。

### 10.3 私钥保管

| 方案 | 优 | 劣 |
|---|---|---|
| A. 主作者私人保管（USB / 加密文件） | 简单 | 单点丢失 |
| B. 仓库加密保管（git-crypt / age） | 团队协作 | 复杂度高 |
| C. 硬件 HSM | 最安全 | 成本高 |

**当前推荐 A**——主作者保管 RSA 私钥 + HMAC master key 两份，加密存储 + 异地备份。

---

## 十一、★ 8 个里程碑工时分解（基于 brief）

| 里程碑 | brief 步骤 | 设计文档 | 实现文件 | 工时 | 难点 |
|---|---|---|---|---|---|
| **M1** 紧急修缮 | 阶段 0 | inventory 05 | BUG-1 + 死代码清理 | 2 天 | 升级测试 |
| **M2** 设计完整 | brief #1~2 | design 00~02 | 文档 | 1 天 | 决策对齐 |
| **M3** 数据库准备 | brief #3 | design 03 | `models/plugin_models.py` 新表 | 1 天 | PG / SQLite 双跑 |
| **M4** 后端核心 | brief #6 | design 06 | `core/plugin_manager.py` + `core/lifecycle.py` | 2 天 | 错误隔离 |
| **M5** 前端档位 1 主题 | brief #4 | design 04 | `frontend/src/plugin-loader/` + CSS 重构 | 2 天 | CSS 变量重构 |
| **M6** 前端档位 2 UI | brief #5 | design 05 | 路由动态加载 + Layout 菜单注入 | 2 天 | Vite 动态 import |
| **M7** 客户分发工具 | brief #7 | design 07 | `tools/pack-plugin.py` + `sign-plugin.py` | 1 天 | 私钥管理 |
| **M8** 兼容测试 + 示例 + 文档 | brief #8 | design 08 | 1 个示例插件覆盖三档 + 用户手册 | 2 天 | 端到端测试 |
| | | | **合计** | **13 天** | |

> brief 工时基线"插件系统 11 天"——本设计**多 2 天**，差异在 M1（紧急修缮）和 M4（错误隔离实施）。M1 是 inventory 05 发现的紧急 bug 修复，必须做；M4 是错误隔离的端到端测试，规模比 brief 估算大。

---

## 十二、★ 需要用户拍板的开放问题

> 这一节是本文最重要的产出。**先回答这 8 个问题再开工**。

### Q1：签名机制选哪个方案？

A. 双重签名 / B. 仅 RSA / **C. RSA + 客户码 HMAC（推荐）**

**影响**：M2 设计 + M7 工具 + M4 加载器都依赖此决策。

### Q2：私钥保管如何？

**A. 主作者私人保管（USB + 加密文件 + 异地备份） / B. 仓库 git-crypt / C. HSM**

**推荐 A，等团队 > 2 人时再升级 B**。

### Q3：插件包格式是 .zip 还是其他？

**A. .zip（推荐）**：跨平台、客户机普遍支持
B. .tar.gz：客户机 Windows 也能解，但要装额外工具
C. 自定义 `.tjvplugin`（实质是改后缀的 .zip）

**推荐 A，文件后缀用 `.tjvplugin` 但内部就是 ZIP**。

### Q4：是否给插件后台线程做资源限制？

A. 不做（默认） / B. 监控 CPU / 内存 / 线程数 / **C. 软警告**

**推荐 C**：监控并打警告，超阈值禁用插件，但**不强行 kill**（插件可能在做关键任务）。

### Q5：`customer_code` 注册表谁维护？

A. 主作者人工维护一个 markdown 表 / B. 后端 DB 表 / C. GitHub Issue 申请

**推荐 A**：项目仓库内一个 `CUSTOMER_CODES.md`（`docs/plugin-system/customer-codes.md`），新客户走 PR 加。

### Q6：插件能否使用 GPU？

A. 完全开放（共享主程序 GPU 资源） / B. 通过 `channel_manager.allocate_gpu(plugin_id)` 申请 / **C. 默认 CPU，需声明才用 GPU**

**推荐 C + B**：插件 manifest 声明 `requires.gpu: true`，加载时申请到才能跑。

### Q7：插件能否注册 source_*_mixin 类型的扩展？

A. 完全允许（接管检测核心） / **B. 不允许**（插件只能挂 hook，不能改主推理路径）

**推荐 B**：source.py 主类太核心，插件直接挂进去风险大。如果客户真要新视频源 / runner，走主程序的"add-source-type" / "add-detection-mode" skill 流程合并，而不是走插件。

### Q8：插件加载失败是否给用户弹 modal？

A. 静默 + log（不打扰客户） / **B. 静默启动主程序 + Settings 插件页显示错误**

**推荐 B**：错误隔离的同时让用户能看到状态，不强弹 modal 影响正常使用。

---

## 十三、与现有系统的接合点 / 不接合点

### 13.1 必接合的点（插件系统设计绕不开）

| 点 | 必须 | 设计文档 |
|---|---|---|
| `License.customerName` ←→ `manifest.customer_code` | 客户码绑定 | design 02 |
| `mes_adapters._REGISTRY` | 档位 3 客户协议接入 | design 06 |
| 主程序 `app.include_router` | 档位 3 路由注册 | design 06 |
| `frontend/src/router/index.js` 静态数组 → 改造支持 `addRoute` | 档位 2 | design 05 |
| `frontend/src/layout/Navbar.vue` 硬编码菜单 → 改造支持动态注入 | 档位 2 | design 05 |
| `useSystemStore` 277 行 → 加 `plugin.*` KV 缓存 | 档位 1/2/3 | design 00 |
| `services/mes_hooks.py` 1505 行 `_handle_cycle_end` → phase 拆分 | 档位 3 业务 hook | design 06 |
| `source_event_trigger_mixin.py` `_trigger_event` → 加 listener | 档位 3 事件 hook | design 06 |
| `core/lifecycle.py`（新建）启动/关机 hook | 档位 3 | design 06 |

### 13.2 不接合的点（明确避开）

| 点 | 不接 | 理由 |
|---|---|---|
| Electron 主进程 | 不动 | spawn / kill 后端 / License 验证不能让插件触达 |
| `source.py` 主类 ORM 字段 | 不动 | 影响主程序检测核心 |
| 现有 31 张 ORM 表 schema | 不动 | 升级风险高 |
| `OPENCV_FFMPEG_CAPTURE_OPTIONS` 等永久不变量 | 不动 | inventory 05 第 13 节 |
| `channel_manager` 单例 | 不替换，仅读 | 多通道协调核心 |
| 关机 8 步流程 | 不动 | 客户机串口资源管理关键 |
| 主程序 i18n 5 个语言包静态加载 | 保留，仅 merge | vue-i18n 限制 |
| `_inspecting[channel_id]` race 修复 | 不动（已修） | 历史 hotfix |

---

## 十四、下一步交付物（按本文档）

确认本文档方向 OK 后，按 brief 顺序产出：

| 顺序 | 文档 | 主要内容 |
|---|---|---|
| 1 | `design/01_manifest_schema.md` | manifest.json 完整字段定义（必填/可选/类型/示例） |
| 2 | `design/02_signature.md` | 签名机制完整实现（含 pack/sign/verify 伪码 + 密钥管理流程） |
| 3 | `design/03_database.md` | plugins 表 + plugin_state 表 + customer_codes 注册表（PG/SQLite 双跑设计） |
| 4 | `design/04_tier1_theme.md` | 主题包加载器（CSS 变量重构 + i18n merge + Logo 替换） |
| 5 | `design/05_tier2_ui.md` | 动态路由 + Layout 菜单注入（Vite 动态 import 方案 + Pinia plugin store） |
| 6 | `design/06_tier3_fullstack.md` | 后端 PluginManager + lifecycle + 错误隔离（含全部 hook 改造） |
| 7 | `design/07_distribution.md` | pack-plugin.py + sign-plugin.py + 客户分发流程 + 验签工具 |
| 8 | `design/08_examples.md` | 1 个示例插件覆盖三档 + 用户文档 + 测试方案 |

---

## 十五、版本历史 + 校对

| 版本 | 日期 | 变更 |
|---|---|---|
| v0.1 | 2026-05-08 | 首版，基于 inventory 01~05 + brief 提炼 |

**事实校验**：
- inventory 01~05 文档 4757 行
- brief 12 节决策（已敲定 9 / 待澄清 3）
- AGENTS.md 第八节关键不变量 10 条
- 现有 License 系统（`electron/license-manager.js` 203 行）作为签名机制参考

**遗漏的事**（待补）：
- 测试方案（如何端到端测插件 + 主程序）—— 留给 design 08
- 客户文档（README 模板）—— 留给 design 08

---

**本文最后更新**：2026-05-08
**维护者**：本分支负责人
