# 01 — manifest.json 完整字段定义

> 适用版本：基于 `feat/plugin-config` v0.1
> 本文目的：把 `plugin.json`（即 manifest）的**每一个字段**定义到"客户能照抄、加载器能逐字段校验、密钥工具能按字段签名"的程度。
>
> 阅读顺序：第二节字段总表 → 第三节字段详解（按大类）→ 第六节三档完整示例。
>
> 配套：`design/00_overview.md`（总体骨架，已敲定 Q1~Q8） / `design/02_signature.md`（签名格式，待写）。

---

## 一、概述

### 1.1 文件位置

```
plugins/{customer_code}/
  ├── plugin.json          ← 本文件
  ├── signature.bin        ← RSA 签名 + 客户码 HMAC（design 02 详）
  ├── backend/             ← 档位 3 后端代码
  ├── frontend/            ← 档位 1/2 前端资源
  ├── templates/           ← 自定义导出模板
  └── README.md            ← 客户文档
```

### 1.2 文件职责

`plugin.json` 同时承担三个职责：
1. **元数据**：插件名 / 版本 / 作者 / 客户码 / 兼容主程序版本
2. **资源声明**：声明插件提供哪些路由 / 菜单 / 视图 / Hook / 表 / 模板
3. **签名输入**：本文件（含 `files_digest`）经 SHA256 摘要后被 RSA 签名 + HMAC 计算

**强制要求**：
- 必须是 **UTF-8 无 BOM** JSON
- 字段顺序**必须** sort_keys=True（影响签名计算的字节级一致性）
- 不允许注释（标准 JSON）
- 总文件大小 ≤ 256 KB

### 1.3 加载器对 manifest 的处理流程

```
1. 读 plugin.json 文件 → bytes
2. JSON 解析 → dict
3. JSON Schema 校验（见第五节）
4. 业务校验（见第九节，customer_code / version / files_digest 等）
5. 重新 sort_keys=True dump → manifest_bytes
6. RSA 验签（manifest_bytes + signature.bin）
7. HMAC 校验（同上）
8. 进入加载流程（design 06）

任何步骤失败 → 标 PluginVerifyError, 落 plugin_state.json error_msg, 跳过加载
```

---

## 二、字段总表（速查）

> 字段命名规则：snake_case，避免使用 camelCase（与 Python 后端一致）

### 2.1 顶层字段（22 个）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `manifest_version` | int | ✅ | manifest schema 版本号（当前固定 `1`） |
| `name` | string | ✅ | 插件人类可读名（中文） |
| `customer_code` | string | ✅ | 客户码（小写英数 + 连字符，3~20 字符） |
| `plugin_version` | string | ✅ | 插件版本（SemVer：`1.2.3`） |
| `description` | string | ✅ | 插件简介（200 字以内） |
| `author` | string | ✅ | 作者（公司名 / 主作者邮箱） |
| `homepage` | string | ⚪ | 主页 URL（可选） |
| `license_text` | string | ⚪ | 客户协议文本（可选） |
| `tier` | int | ✅ | 档位（1 主题 / 2 UI / 3 全栈） |
| `capabilities` | array<string> | ✅ | 能力声明（详见 §3.4） |
| `main_version_min` | string | ✅ | 兼容的最低主程序版本 |
| `main_version_max` | string | ⚪ | 兼容的最高主程序版本（缺省=无上限） |
| `created_at` | string | ✅ | 打包时间（ISO 8601 UTC） |
| `signed_at` | string | ✅ | 签名时间（ISO 8601 UTC） |
| `signed_by` | string | ✅ | 签名者标识（与私钥关联） |
| `files_digest` | string | ✅ | 全文件目录摘要（SHA256 hex） |
| `frontend` | object | ⚪ | 前端资源声明（档位 1/2/3 都可有） |
| `backend` | object | ⚪ | 后端资源声明（档位 3 必有） |
| `requires` | object | ⚪ | 依赖声明 |
| `default_config` | object | ⚪ | 默认 KV 配置（写到 SystemConfig） |
| `runtime` | object | ⚪ | 运行时元信息（资源阈值等） |
| `metadata` | object | ⚪ | 自定义元数据（透传） |

✅ = 必填 / ⚪ = 可选

### 2.2 档位与字段必填关系

| 档位 | 必填子对象 | 典型用到的子字段 |
|---|---|---|
| **档位 1（主题）** | `frontend.theme` 必有 | `theme.css` / `theme.logo` / `theme.app_title` / `frontend.i18n` |
| **档位 2（UI）** | `frontend.routes` 必有 | + `frontend.menus` / `frontend.stores` / `frontend.views` |
| **档位 3（全栈）** | `backend` 必有 | + `backend.entry` / `backend.routers` / `backend.hooks` / `backend.adapters` / `backend.tables` |

> 档位 3 包含档位 1+2 全部能力（向下兼容）。

---

## 三、字段详解（按 22 个顶层 + 子对象逐个）

### 3.1 顶层基础字段（10 个）

#### `manifest_version`（int，必填）

```json
"manifest_version": 1
```

- **取值**：当前固定为 `1`
- **变更策略**：当 manifest schema 出现 break 改动（删字段 / 改字段类型）时递增
- **加载器行为**：
  - 主程序内置支持的最高版本号（v3.6.x 支持 `1`）
  - manifest_version > 主程序支持 → 拒绝加载（错误 `MANIFEST_VERSION_TOO_NEW`）
  - manifest_version < 主程序支持 → 容忍（向后兼容）
- **未来演进**：详见第八节

#### `name`（string，必填）

```json
"name": "ACME 客户定制插件"
```

- **取值**：1~100 字符 UTF-8 字符串
- **用途**：在 Settings 插件管理页 / 启动日志中显示
- **校验**：
  - 不能为空
  - 不能含换行符 `\n` / `\r`
- **错误**：`MANIFEST_NAME_INVALID`

#### `customer_code`（string，必填）★ 关键字段

```json
"customer_code": "acme"
```

- **取值规则**：
  - 正则：`^[a-z][a-z0-9-]{2,19}$`
  - 必须全小写英文字母 + 数字 + 连字符
  - 必须以字母开头
  - 长度 3~20 字符
- **唯一性**：在 `docs/plugin-system/customer-codes.md` 注册表中全局唯一（design 00 Q5）
- **校验**：
  - 加载时与 `license.customerName` 严格匹配（design 02）
  - 与 plugin 目录名严格匹配（`plugins/acme/` 必须 `customer_code = "acme"`）
- **典型例子**：`acme` / `acme-tj` / `xyz-2025` / `default`（保留给内置示例）
- **错误**：
  - `MANIFEST_CUSTOMER_CODE_FORMAT`（格式不对）
  - `MANIFEST_CUSTOMER_CODE_DIR_MISMATCH`（与目录不一致）
  - `PLUGIN_LICENSE_MISMATCH`（与 license 不一致）

#### `plugin_version`（string，必填）

```json
"plugin_version": "1.2.3"
```

- **取值**：SemVer 格式 `{major}.{minor}.{patch}`，正则 `^\d+\.\d+\.\d+$`
- **可附加 pre-release**：`1.2.3-rc.1`（正则 `^\d+\.\d+\.\d+(-[a-zA-Z0-9.-]+)?$`）
- **用途**：
  - Settings 页显示
  - 升级时新版本号必须 ≥ 旧版本号（递增）
  - signed_at 不变更但版本递增视为重打包（warning）
- **错误**：`MANIFEST_PLUGIN_VERSION_FORMAT`

#### `description`（string，必填）

```json
"description": "ACME 公司专属定制：MQTT 推送 + 自家报表 + 班次管理"
```

- **取值**：1~200 字符
- **用途**：Settings 插件页显示
- **校验**：长度限制
- **错误**：`MANIFEST_DESCRIPTION_TOO_LONG`

#### `author`（string，必填）

```json
"author": "TianJun AI Team <support@tianjun-ai.com>"
```

- **取值**：1~200 字符；推荐"组织名 <邮箱>"格式（不强制）
- **用途**：Settings 显示 / 客户问题反馈
- **错误**：`MANIFEST_AUTHOR_INVALID`

#### `homepage`（string，可选）

```json
"homepage": "https://tianjun-ai.com/plugins/acme"
```

- **取值**：HTTPS URL（出于安全要求，不允许 http://）
- **校验**：不强制可达
- **错误**：`MANIFEST_HOMEPAGE_NOT_HTTPS`

#### `license_text`（string，可选）

```json
"license_text": "本插件版权归 ACME 公司所有 ..."
```

- **取值**：客户授权协议文本
- **用途**：Settings 插件页"详情"按钮显示
- **错误**：无

#### `tier`（int，必填）★ 决定加载行为

```json
"tier": 3
```

- **取值**：`1` / `2` / `3`
- **校验联动**：
  - tier=1 时 `frontend.theme` 必有，`backend` 不能有
  - tier=2 时 `frontend.routes` 必有，`backend` 不能有
  - tier=3 时 `backend` 必有
- **错误**：
  - `MANIFEST_TIER_INVALID`（值不在 1/2/3）
  - `MANIFEST_TIER_BACKEND_FORBIDDEN`（tier 1/2 不该有 backend）
  - `MANIFEST_TIER_BACKEND_REQUIRED`（tier 3 没有 backend）

#### `capabilities`（array<string>，必填）

```json
"capabilities": [
  "frontend.theme",
  "frontend.i18n",
  "frontend.routes",
  "frontend.menus",
  "backend.routes",
  "backend.adapter.mqtt",
  "backend.hook.cycle_end",
  "backend.tables",
  "export.templates",
  "export.realtime_rules"
]
```

- **取值**：字符串数组，每项必须在"已注册能力枚举"内（详见 §3.4）
- **作用**：
  - 让加载器**一次性**校验插件**实际有的资源**和**声明能力**一致
  - 让 Settings 页一目了然显示插件能力
- **校验**：
  - 每项必须在合法枚举内
  - 必须与 `frontend.*` / `backend.*` 实际声明匹配（声明了 `backend.adapter.mqtt` 就必须在 `backend.adapters` 里有 `mqtt` 项）
- **错误**：
  - `MANIFEST_CAPABILITY_UNKNOWN`
  - `MANIFEST_CAPABILITY_INCONSISTENT`

### 3.2 版本兼容字段（3 个）

#### `main_version_min`（string，必填）

```json
"main_version_min": "3.6.0"
```

- **取值**：SemVer 格式
- **加载器行为**：
  - 主程序读 `electron/package.json` 拿 `main_version`
  - 比较：`main_version_min ≤ main_version`
  - 不满足 → 拒绝加载，错误 `PLUGIN_MAIN_VERSION_TOO_LOW`
- **典型值**：插件开发者写发版时使用的主程序版本

#### `main_version_max`（string，可选）

```json
"main_version_max": "3.9.x"
```

- **取值规则**：
  - SemVer 完整版本：`3.9.0`（精确上限）
  - SemVer 通配符：`3.x` 或 `3.9.x`（minor / patch 通配）
- **加载器行为**：
  - 缺省值 = `null`，意为"无上限"
  - 不满足时拒绝加载，错误 `PLUGIN_MAIN_VERSION_TOO_HIGH`
- **建议**：客户接收新插件时给一个比当前主程序版本宽松一档的上限（如 `3.x`）

#### `created_at` / `signed_at`（string，必填）

```json
"created_at": "2026-05-08T05:00:00Z",
"signed_at":  "2026-05-08T05:30:00Z"
```

- **取值**：ISO 8601 UTC 字符串
- **校验**：
  - 必须 UTC（带 `Z` 结尾）
  - signed_at ≥ created_at
  - signed_at ≤ 当前时间（不能是未来时间，**容忍 5 分钟时钟漂移**）
- **错误**：
  - `MANIFEST_DATE_FORMAT`
  - `MANIFEST_SIGNED_AT_FUTURE`

#### `signed_by`（string，必填）

```json
"signed_by": "tianjun-ai-master-2026"
```

- **取值**：1~64 字符；推荐"主体名 + 年份"
- **用途**：审计签名来源（多个签名者时区分）
- **未来扩展**：可与"私钥指纹"绑定查询表
- **错误**：`MANIFEST_SIGNED_BY_INVALID`

### 3.3 文件摘要字段

#### `files_digest`（string，必填）★ 防篡改核心

```json
"files_digest": "sha256:1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f1a2b"
```

- **格式**：`{algo}:{hex_digest}`，当前固定 `sha256:`
- **计算方式**（详见 design 02 第三节）：
  ```python
  import hashlib, os
  
  def calc_files_digest(plugin_dir):
      h = hashlib.sha256()
      # 收集所有文件路径（除 plugin.json + signature.bin）
      paths = []
      for root, dirs, files in os.walk(plugin_dir):
          # 跳过常见噪声目录
          dirs[:] = [d for d in dirs if d not in ('__pycache__', '.git', 'node_modules', '.DS_Store')]
          for f in files:
              if f in ('plugin.json', 'signature.bin'):
                  continue
              full = os.path.join(root, f)
              rel = os.path.relpath(full, plugin_dir).replace('\\', '/')  # 跨平台一致
              paths.append((rel, full))
      paths.sort(key=lambda x: x[0])  # 路径排序，跨 OS 一致
      
      for rel, full in paths:
          h.update(rel.encode('utf-8'))
          h.update(b'\0')
          with open(full, 'rb') as fh:
              while chunk := fh.read(8192):
                  h.update(chunk)
          h.update(b'\0')
      return f"sha256:{h.hexdigest()}"
  ```
- **作用**：
  - 防文件被篡改（客户改了 backend/__init__.py 内容 → digest 不一致 → 拒绝加载）
  - 跨平台一致（Windows / Linux 计算结果相同，关键是路径分隔符规范化 + 排序）
- **错误**：`PLUGIN_FILES_DIGEST_MISMATCH`

### 3.4 capabilities 枚举（受控词汇表）

> 每个 `capabilities` 数组项必须在下表内。

| 能力 | 含义 | 触发的 manifest 字段 |
|---|---|---|
| **frontend.theme** | 主题包（CSS 变量 / Logo / 标题） | `frontend.theme` |
| **frontend.i18n** | 多语言文案合并 | `frontend.i18n` |
| **frontend.routes** | 添加新路由 | `frontend.routes` |
| **frontend.menus** | 添加 Layout 菜单项 | `frontend.menus` |
| **frontend.stores** | 添加 Pinia store | `frontend.stores` |
| **frontend.hide_menus** | 隐藏现有菜单 | `frontend.hidden_menus` |
| **frontend.replace_logo** | 替换主程序 logo | `frontend.theme.logo` |
| **frontend.replace_app_title** | 替换 app title / favicon | `frontend.theme.app_title` |
| **backend.routes** | 添加 FastAPI 路由 | `backend.routers` |
| **backend.adapter.{name}** | 注册 MES adapter | `backend.adapters` |
| **backend.hook.cycle_end** | 挂 cycle_end hook | `backend.hooks` |
| **backend.hook.session_end** | 挂 session_end hook | `backend.hooks` |
| **backend.hook.box_complete** | 挂 box_complete hook | `backend.hooks` |
| **backend.hook.scan_received** | 挂扫码 hook | `backend.hooks` |
| **backend.hook.event_trigger** | 挂 `_trigger_event` hook | `backend.hooks` |
| **backend.hook.alarm_trigger** | 拦截报警 | `backend.hooks` |
| **backend.hook.startup** | 启动钩子 | `backend.hooks` |
| **backend.hook.shutdown** | 关机钩子 | `backend.hooks` |
| **backend.tables** | 创建自家 ORM 表 | `backend.tables` |
| **backend.background_thread** | 启自家后台线程 | `backend.background_threads` |
| **export.templates** | 自定义导出模板 | `export.templates` |
| **export.realtime_rules** | 实时导出规则 | `export.realtime_rules` |
| **export.field_resolver** | 注册自定义导出字段 | `export.field_resolvers` |
| **export.renderer.{name}** | 注册自定义 renderer | `export.renderers` |
| **export.trigger.{name}** | 注册自定义实时 trigger | `export.triggers` |
| **runtime.gpu** | 需要 GPU | `requires.gpu` |
| **runtime.alarm_trigger** | 调用 `PluginHost.trigger_alarm` 触发主程序报警 | (运行时声明, 见 §3.4.1) |
| **runtime.mes_push** | 调用 `PluginHost.mes_push` 走 MES Gateway 外推 | (运行时声明, 见 §3.4.1) |
| **runtime.system_config_write** | 调用 `PluginHost.write_system_config` 写 KV 配置 | (运行时声明, 见 §3.4.1) |
| **runtime.step_field_write** | 调用 `PluginHost.write_plugin_step_field` 写 `step_records.plugin_data` JSON 字段 | (运行时声明, 见 §3.4.1) |

**未来扩展**：新加能力时**不删旧的**，老 manifest 保持兼容。

#### 3.4.1 `runtime.*` 主动 API capabilities（v3.13 M1.3a 新增）

这一组与 §3.4 其他 capability 不同：**不与 manifest 字段绑定**，而是声明插件代码在运行时
将调用 `PluginHost` 的某个高危主动 API。`PluginHost` 在调用前对照 `manifest.capabilities`
强制校验，未声明 → 抛 `PluginRuntimeError` + 写 audit log。

| capability | 启用的 API | 说明 |
|---|---|---|
| `runtime.alarm_trigger` | `host.trigger_alarm(channel_id, event_type, reason)` | 触发主程序 `AlarmRouter`（灯柱/蜂鸣器） |
| `runtime.mes_push` | `host.mes_push(event_type, payload, channel_id)` | 走主程序 `MESGateway.dispatch` 外推（`event_type` 必须 `plugin_<cc>_` 前缀） |
| `runtime.system_config_write` | `host.write_system_config(key, value, description)` | 写 `system_configs` 表（`key` 必须 `plugin_<cc>_` 前缀） |
| `runtime.step_field_write` | `host.write_plugin_step_field(step_record_id, key, value)` | JSON 合并写入 `step_records.plugin_data`（`key` 必须 `plugin_<cc>_` 前缀，`value` 必须可 JSON 序列化）— v3.13 M3.3 |
| `runtime.channel_group_broadcast` | `host.broadcast_to_channel_group(group_id, message)` | 给工位组成员 fire `plugin_broadcast_received` hook（`message` 必须 dict + 可 JSON 序列化）— v3.13 RFC 10 CG.7 |
| `runtime.workpiece_flow_observe` | `host.list_workpiece_flows()` / `host.query_workpiece_flow_state(flow_id)` | 查 v3.14 RFC 11 串行流水线配置 + 当前 in-flight 工件状态。只读, 但因为涉及客户产线敏感数据, 声明性能力位以备审计 |

**注意**：
- `host.read_system_config(...)` **不**需要声明 capability（只读无副作用，跨插件查主程序状态是合理需求）
- 这三个 capability 是 **运行时声明**，validator 不强制 `backend.routers` / `backend.hooks` 等 manifest 字段必须存在
- 一个 `runtime.*` capability 写在 `capabilities` 数组里就够了，没有更细的子字段

### 3.5 `frontend` 子对象（档位 1/2/3）

```json
"frontend": {
  "entry": "frontend/index.js",
  "theme": { ... },
  "i18n": { ... },
  "routes": [ ... ],
  "menus": [ ... ],
  "hidden_menus": [ ... ],
  "stores": [ ... ],
  "static_dir": "frontend/static"
}
```

#### `frontend.entry`（string，可选，默认 `"frontend/index.js"`）

- **作用**：插件前端的入口 ESM 模块路径（相对插件目录）
- **加载方式**：动态 import（design 05 第三节"前端动态 import 方案"）
- **打包要求**：必须是预打包的 ES Module（用 `vite build --lib`）

#### `frontend.theme`（object，档位 1 必有）

```json
"theme": {
  "css": "frontend/theme.css",
  "logo": "frontend/assets/logo.svg",
  "favicon": "frontend/assets/favicon.png",
  "app_title": "ACME 视觉检测系统",
  "css_variables": {
    "--primary": "#FFEB3B",
    "--secondary": "#212121",
    "--bg-base": "#0F0F1F"
  }
}
```

| 子字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `css` | string | ⚪ | CSS 文件路径（相对插件目录） |
| `logo` | string | ⚪ | Logo 文件路径 |
| `favicon` | string | ⚪ | Favicon 路径 |
| `app_title` | string | ⚪ | app 标题 |
| `css_variables` | object | ⚪ | CSS 变量 inline 覆盖（与 `css` 文件二选一或共存） |

**校验**：
- `css` / `logo` / `favicon` 文件必须真实存在（加载时 `os.path.exists`）
- `css_variables` 的 key 必须以 `--` 开头
- `app_title` ≤ 100 字符

#### `frontend.i18n`（object，档位 1 可有）

```json
"i18n": {
  "zh-CN": "frontend/i18n/zh-CN.json",
  "zh-TW": "frontend/i18n/zh-TW.json",
  "en-US": "frontend/i18n/en-US.json"
}
```

- **键**：必须是主程序支持的 locale（`zh-CN` / `zh-TW` / `en-US` / `ja-JP` / `ko-KR`）
- **值**：JSON 文件路径（相对插件目录）
- **加载方式**：前端启动时拉这些文件 → `i18n.global.mergeLocaleMessage(locale, messages)`
- **错误**：
  - `MANIFEST_I18N_LOCALE_UNKNOWN`
  - `MANIFEST_I18N_FILE_NOT_FOUND`

#### `frontend.routes`（array<object>，档位 2 必有）

```json
"routes": [
  {
    "path": "report",
    "name": "report",
    "component": "frontend/views/AcmeReport.js",
    "menu_label": "ACME 报表",
    "icon": "DataLine",
    "order": 100,
    "permissions": ["operator", "admin"]
  },
  {
    "path": "shift",
    "name": "shift",
    "component": "frontend/views/AcmeShift.js"
  }
]
```

| 子字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `path` | string | ✅ | 路由 path（相对，会拼成 `/plugin/{cc}/{path}`） |
| `name` | string | ✅ | 路由 name（会拼成 `Plugin-{cc}-{name}`） |
| `component` | string | ✅ | 视图组件 ESM 文件路径 |
| `menu_label` | string | ⚪ | 菜单显示文本（如要 menu，则必填） |
| `icon` | string | ⚪ | Element Plus 图标名 |
| `order` | int | ⚪ | 菜单排序权重（默认 100） |
| `permissions` | array<string> | ⚪ | 显示权限（保留字段，当前未启用） |

**校验**：
- `path` 不能含 `/`（避免子路由）
- `name` 同正则 `^[a-z][a-z0-9-]*$`
- `component` 文件必须存在
- 同一插件内 `name` 不重复

#### `frontend.menus`（array<object>，档位 2 可有）

```json
"menus": [
  {
    "label": "ACME 报表",
    "path": "/plugin/acme/report",
    "icon": "DataLine",
    "order": 100,
    "i18n_key": "plugin.acme.menu.report"
  }
]
```

> 通常 `menus` 由 `routes[*].menu_label` 自动生成，**只有以下场景才显式写**：
> - 菜单项不对应路由（如外链、纯 popover）
> - 菜单项排序需特殊控制
> - 菜单项需 i18n（写 `i18n_key` 优先于 `label`）

#### `frontend.hidden_menus`（array<string>，档位 1 可有）

```json
"hidden_menus": ["/cluster", "/mes"]
```

- **作用**：隐藏主程序菜单项（按 path 匹配）
- **典型场景**：客户单机部署不需要"集群"菜单
- **限制**：不能隐藏 `/monitor`（核心视图，不允许隐藏）
- **错误**：`MANIFEST_HIDDEN_MENU_FORBIDDEN`

#### `frontend.ui_hidden`（array<string>，档位 2/3 可有，v3.13 M2.2a 新增）

```json
"ui_hidden": ["monitor.step-cell.status", "cycle-result.indicator"]
```

- **作用**：隐藏主程序 `<TjSlot name="...">` 槽（连默认内容都不渲染）
- **典型场景**：客户要求"隐藏步骤级红色 NG 指示，只保留 cycle 级"
- **与 `frontend.hidden_menus` 的区别**：
  * `hidden_menus` 是隐藏导航菜单项（按 path）
  * `ui_hidden` 是隐藏 slot（按 slot name）
- **slot name 词汇表**：见 RFC 09 §5.3 的 7 个槽位定义；新加 slot 由主程序在 `.vue` 文件中显式声明
- **生效时机**：主程序启动时 `usePluginThemeStore.apply()` 拉 manifest → 应用到 `store.uiHidden` → `<TjSlot>` 内部 computed 自动响应

#### `frontend.stores`（array<object>，档位 2 可有）

```json
"stores": [
  {
    "id": "plugin-acme-shift",
    "module": "frontend/stores/shift.js"
  }
]
```

- **`id`**：必须以 `plugin-{customer_code}-` 开头（design 00 第五节）
- **`module`**：Pinia store ESM 模块路径
- **加载方式**：动态 import 后 `defineStore(id, definition)`

#### `frontend.static_dir`（string，可选）

```json
"static_dir": "frontend/static"
```

- **作用**：声明前端静态资源目录（图片 / 字体 / 音频）
- **后端处理**：插件加载时 `app.mount(f"/api/v1/plugins/{cc}/static", StaticFiles(directory=plugin_dir / static_dir))`
- **典型路径**：相对插件目录的 `frontend/static/`
- **大小限制**：≤ 50 MB（档位 1） / ≤ 200 MB（档位 2/3）

### 3.6 `backend` 子对象（档位 3 必有）

```json
"backend": {
  "entry": "backend/__init__.py",
  "routers": [ ... ],
  "adapters": [ ... ],
  "hooks": [ ... ],
  "tables": [ ... ],
  "background_threads": [ ... ]
}
```

#### `backend.entry`（string，必填）

```json
"entry": "backend/__init__.py"
```

- **作用**：插件后端入口文件路径
- **加载方式**：`importlib.util.spec_from_file_location(...)` 独立 namespace
- **入口约定**：必须导出 `register_plugin(app, registry, license_payload)` 函数
- **示例 register_plugin**：
  ```python
  # plugins/acme/backend/__init__.py
  from fastapi import FastAPI
  from .routes import router as acme_router
  from .hooks import register_all_hooks
  from .adapters import MQTTAdapter
  
  def register_plugin(app: FastAPI, registry, license_payload):
      """加载器调用此函数完成全部注册"""
      app.include_router(acme_router, prefix=f"/api/v1/plugins/acme")
      registry.adapters.register("plugin-acme-mqtt", MQTTAdapter)
      register_all_hooks(registry)
      # 启动后台线程也在这里
  ```

#### `backend.routers`（array<object>，可选）

```json
"routers": [
  {
    "module": "backend.routes",
    "attr": "router",
    "subpath": ""
  }
]
```

| 子字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `module` | string | ✅ | Python module 路径（相对插件 backend 目录） |
| `attr` | string | ✅ | router 对象属性名（通常 `router`） |
| `subpath` | string | ⚪ | 子前缀（默认空，最终路径 `/api/v1/plugins/{cc}/{subpath}`） |

> 通常 `routers` 是声明式的——加载器知道该挂哪个 router；但实际挂载行为还是 `register_plugin` 内调 `app.include_router`。**这里只做声明**，让加载器能预校验。

#### `backend.adapters`（array<object>，可选）

```json
"adapters": [
  {
    "name": "plugin-acme-mqtt",
    "class_name": "MQTTAdapter",
    "module": "backend.adapters.mqtt",
    "description": "MQTT 协议推送 ACME MES"
  }
]
```

| 子字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | string | ✅ | adapter 注册名（必须以 `plugin-{cc}-` 开头） |
| `class_name` | string | ✅ | adapter 类名 |
| `module` | string | ✅ | Python module 路径 |
| `description` | string | ⚪ | 协议描述（前端 Gateway 配置页显示） |

#### `backend.hooks`（array<object>，可选）

```json
"hooks": [
  {
    "type": "startup",
    "module": "backend.hooks",
    "function": "init_acme",
    "priority": 500
  },
  {
    "type": "cycle_end",
    "phase": "after_workpiece_set_result",
    "module": "backend.hooks",
    "function": "push_to_acme_erp"
  },
  {
    "type": "scan_received",
    "module": "backend.hooks",
    "function": "log_scan_to_acme"
  },
  {
    "type": "event_trigger",
    "when": "post",
    "module": "backend.hooks",
    "function": "track_ng_events"
  },
  {
    "type": "alarm_trigger",
    "module": "backend.hooks",
    "function": "send_dingtalk_alert"
  }
]
```

| 子字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `type` | string | ✅ | `startup` / `shutdown` / `cycle_end` / `session_end` / `box_complete` / `scan_received` / `event_trigger` / `alarm_trigger` |
| `module` | string | ✅ | Python module 路径 |
| `function` | string | ✅ | 函数名 |
| `phase` | string | ⚪ | type=`cycle_end` 时指定 phase（design 06 详） |
| `when` | string | ⚪ | type=`event_trigger` 时为 `pre`/`post` |
| `priority` | int | ⚪ | type=`startup`/`shutdown` 时排序（0~1000，默认 500） |

**校验**：
- `type` 必须在合法枚举内
- `phase` 必须在 design 06 列出的 phase 名内（`extract_inspecting` / `workpiece_set_result` / ...）
- `module` + `function` 必须可解析

#### `backend.tables`（array<object>，可选）

```json
"tables": [
  {
    "name": "p_acme_orders_extra",
    "module": "backend.models",
    "class_name": "PluginAcmeOrderExtra"
  }
]
```

| 子字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | string | ✅ | 表名（必须 `p_{cc}_*` 命名空间） |
| `module` | string | ✅ | ORM 类所在 module |
| `class_name` | string | ✅ | ORM 类名（必须 `Plugin{Cc}*` 命名空间） |

**校验**：
- 表名必须 `p_{customer_code}_` 前缀
- 类名必须 `Plugin{CustomerCode}` 前缀
- 加载时调 `Base.metadata.create_all(bind=engine, tables=[Cls.__table__])`
- 升级（plugin_version 递增）时由插件自己处理 ALTER TABLE，**主程序不参与**

#### `backend.background_threads`（array<object>，可选）

```json
"background_threads": [
  {
    "name": "acme-erp-poller",
    "module": "backend.threads",
    "function": "start_erp_poller",
    "auto_start": true
  }
]
```

| 子字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | string | ✅ | 线程名（用于诊断） |
| `module` | string | ✅ | Python module |
| `function` | string | ✅ | 启动函数（应在内部 `threading.Thread(daemon=True).start()`） |
| `auto_start` | bool | ⚪ | 启动时自动起（默认 `true`） |

### 3.7 `requires` 子对象（依赖声明）

```json
"requires": {
  "gpu": false,
  "cuda": null,
  "python_version": ">=3.10",
  "python_packages": [
    {"name": "paho-mqtt", "version": ">=1.6,<2.0"},
    {"name": "openpyxl", "version": "*"}
  ],
  "system_libs": [],
  "main_features": []
}
```

| 子字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `gpu` | bool | ⚪ | 是否需要 GPU（默认 `false`） |
| `cuda` | string \| null | ⚪ | CUDA 版本约束（如 `>=11.8`，仅 gpu=true 时生效） |
| `python_version` | string | ⚪ | Python 版本约束（如 `>=3.10`） |
| `python_packages` | array<object> | ⚪ | 依赖的 Python 包 |
| `system_libs` | array<string> | ⚪ | 系统库（如 `["libusb"]`，仅警告不强校验） |
| `main_features` | array<string> | ⚪ | 主程序"特性 flag"依赖（如 `["mes-gateway"]`，未来用） |

**python_packages 处理策略**（重要）：
- 加载器**不自动 pip install**（客户工控机普遍离线）
- 插件包应**自带 wheels**到 `plugins/{cc}/wheels/` 目录
- 加载时主程序会先看 `wheels/` 是否有，再尝试 import；都没有 → 加载失败
- design 06 详细描述

**校验**：
- `gpu=true` 时，主程序检查 `torch.cuda.is_available()`，false 则拒绝
- `python_version` 用 packaging.specifiers 解析

### 3.8 `default_config` 子对象（默认 KV）

```json
"default_config": {
  "plugin.acme.theme_color": "#FFEB3B",
  "plugin.acme.erp_url": "https://acme.com/erp",
  "plugin.acme.shift_labels": ["早", "中", "晚"]
}
```

- **加载行为**：插件加载完成时遍历此对象，**only 写入**那些 SystemConfig 中**还不存在**的 key（避免覆盖客户已修改的配置）
- **键命名**：必须 `plugin.{customer_code}.*` 前缀
- **值**：JSON 可序列化的任意类型
- **错误**：
  - `MANIFEST_DEFAULT_CONFIG_KEY_INVALID`（不以 `plugin.{cc}.` 开头）

### 3.9 `runtime` 子对象（运行时元信息）

```json
"runtime": {
  "cpu_threshold_warn_pct": 30,
  "memory_threshold_warn_mb": 500,
  "max_hook_duration_ms": 5000,
  "background_thread_max_count": 3
}
```

| 子字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `cpu_threshold_warn_pct` | int | ⚪ | 30 | 单插件 CPU 占用警告阈值（百分比） |
| `memory_threshold_warn_mb` | int | ⚪ | 500 | 单插件内存警告阈值（MB） |
| `max_hook_duration_ms` | int | ⚪ | 5000 | 单 hook 调用最长时间（超过仅警告） |
| `background_thread_max_count` | int | ⚪ | 3 | 单插件最多后台线程数 |

> 这些是**软警告**阈值（design 00 Q4=C），**不强 kill**插件。

### 3.10 `metadata` 子对象（自定义透传）

```json
"metadata": {
  "build_machine": "ci-build-runner-3",
  "git_commit": "abc1234",
  "internal_ticket": "ACME-2026-501"
}
```

- **作用**：自定义元数据，主程序不解析，**仅在 Settings 详情页透传显示**
- **限制**：总大小 ≤ 4 KB

### 3.11 `export` 子对象（自定义导出资源）

```json
"export": {
  "templates": [
    {
      "name": "ACME 缺陷报告",
      "format": "docx",
      "content_path": "templates/acme-defect-report.j2",
      "is_system": false
    }
  ],
  "realtime_rules": [
    {
      "name": "ACME NG 实时导出",
      "template_name": "ACME 缺陷报告",
      "trigger_event": "cycle_end",
      "filter_config": {
        "is_good": [false]
      },
      "is_enabled": true
    }
  ],
  "field_resolvers": [
    {
      "field": "plugin.acme.erp_id",
      "module": "backend.export_resolvers",
      "function": "resolve_erp_id",
      "type": "str",
      "description": "ACME ERP 工单号"
    }
  ]
}
```

#### `export.templates`（array<object>，可选）

| 子字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | string | ✅ | 模板名（在 ExportTemplate 表中以 `Plugin-{cc}-` 前缀写入） |
| `format` | string | ✅ | `txt` / `csv` / `docx` / `xlsx` / `pdf` |
| `content_path` | string | ✅ | 模板文件路径（j2 / docx / xlsx） |
| `is_system` | bool | ⚪ | 默认 false（插件模板不算 is_system） |

**加载行为**：插件加载时 upsert 到 `export_templates` 表（按 `Plugin-{cc}-{name}` 唯一）。

#### `export.realtime_rules`（array<object>，可选）

| 子字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | string | ✅ | 规则名 |
| `template_name` | string | ✅ | 关联模板名（必须是本插件 `export.templates` 内或主程序内置模板） |
| `trigger_event` | string | ✅ | `cycle_end` / `session_end` / `box_complete`（design 06 实时 trigger registry） |
| `filter_config` | object | ⚪ | 过滤条件 JSON（同 ExportRealtimeRule） |
| `is_enabled` | bool | ⚪ | 默认 false（用户在 Settings 启用） |

#### `export.field_resolvers`（array<object>，可选）

| 子字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `field` | string | ✅ | 字段名（必须 `plugin.{cc}.*`） |
| `module` | string | ✅ | Python module |
| `function` | string | ✅ | 解析函数 |
| `type` | string | ✅ | `str` / `int` / `float` / `bool` |
| `description` | string | ⚪ | 字段描述 |

---

## 四、files_digest 算法（详）

详见 `design/02_signature.md` 第三节。本节简述：

```
1. 遍历 plugin_dir 下所有文件（排除 plugin.json + signature.bin + 噪声目录）
2. 路径规范化（用 / 分隔，相对路径，跨 OS 一致）
3. 路径排序（字典序，跨 OS 一致）
4. 顺序计算 SHA256，每个文件:
   - 写路径字节 + \0 分隔
   - 写文件内容字节 + \0 分隔
5. 输出 sha256:hex
```

**关键 corner case**：
- 空文件：仍然写入路径 + \0 + \0
- 二进制文件：直接读字节，不解码
- 符号链接：解引用一次（`os.readlink` 一次后读真实文件）
- 隐藏文件（`.xxx`）：包含
- `__pycache__/` `.git/` `node_modules/` `.DS_Store`：排除

---

## 五、JSON Schema 完整定义

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://tianjun-ai.com/schemas/plugin.json/v1",
  "title": "TianJun AI Vision Plugin Manifest",
  "type": "object",
  "required": [
    "manifest_version", "name", "customer_code", "plugin_version",
    "description", "author", "tier", "capabilities",
    "main_version_min", "created_at", "signed_at", "signed_by", "files_digest"
  ],
  "additionalProperties": false,
  "properties": {
    "manifest_version": { "type": "integer", "const": 1 },
    "name": { "type": "string", "minLength": 1, "maxLength": 100, "pattern": "^[^\\n\\r]+$" },
    "customer_code": { "type": "string", "pattern": "^[a-z][a-z0-9-]{2,19}$" },
    "plugin_version": { "type": "string", "pattern": "^\\d+\\.\\d+\\.\\d+(-[a-zA-Z0-9.-]+)?$" },
    "description": { "type": "string", "minLength": 1, "maxLength": 200 },
    "author": { "type": "string", "minLength": 1, "maxLength": 200 },
    "homepage": { "type": "string", "format": "uri", "pattern": "^https://" },
    "license_text": { "type": "string", "maxLength": 8192 },
    "tier": { "type": "integer", "enum": [1, 2, 3] },
    "capabilities": {
      "type": "array", "minItems": 1, "uniqueItems": true,
      "items": { "type": "string" }
    },
    "main_version_min": { "type": "string", "pattern": "^\\d+\\.\\d+\\.\\d+$" },
    "main_version_max": { "type": "string", "pattern": "^\\d+\\.(\\d+|x)(\\.(\\d+|x))?$" },
    "created_at": { "type": "string", "format": "date-time" },
    "signed_at": { "type": "string", "format": "date-time" },
    "signed_by": { "type": "string", "minLength": 1, "maxLength": 64 },
    "files_digest": { "type": "string", "pattern": "^sha256:[0-9a-f]{64}$" },
    "frontend": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "entry": { "type": "string" },
        "theme": {
          "type": "object",
          "additionalProperties": false,
          "properties": {
            "css": { "type": "string" },
            "logo": { "type": "string" },
            "favicon": { "type": "string" },
            "app_title": { "type": "string", "maxLength": 100 },
            "css_variables": {
              "type": "object",
              "patternProperties": { "^--[a-z][a-z0-9-]*$": { "type": "string" } },
              "additionalProperties": false
            }
          }
        },
        "i18n": {
          "type": "object",
          "patternProperties": {
            "^(zh-CN|zh-TW|en-US|ja-JP|ko-KR)$": { "type": "string" }
          },
          "additionalProperties": false
        },
        "routes": {
          "type": "array",
          "items": {
            "type": "object",
            "required": ["path", "name", "component"],
            "additionalProperties": false,
            "properties": {
              "path": { "type": "string", "pattern": "^[a-z][a-z0-9-]*$" },
              "name": { "type": "string", "pattern": "^[a-z][a-z0-9-]*$" },
              "component": { "type": "string" },
              "menu_label": { "type": "string", "maxLength": 50 },
              "icon": { "type": "string" },
              "order": { "type": "integer" },
              "permissions": { "type": "array", "items": { "type": "string" } }
            }
          }
        },
        "menus": {
          "type": "array",
          "items": {
            "type": "object",
            "required": ["label", "path"],
            "properties": {
              "label": { "type": "string" },
              "path": { "type": "string" },
              "icon": { "type": "string" },
              "order": { "type": "integer" },
              "i18n_key": { "type": "string" }
            }
          }
        },
        "hidden_menus": { "type": "array", "items": { "type": "string" } },
        "stores": {
          "type": "array",
          "items": {
            "type": "object",
            "required": ["id", "module"],
            "properties": {
              "id": { "type": "string", "pattern": "^plugin-" },
              "module": { "type": "string" }
            }
          }
        },
        "static_dir": { "type": "string" }
      }
    },
    "backend": {
      "type": "object",
      "required": ["entry"],
      "additionalProperties": false,
      "properties": {
        "entry": { "type": "string" },
        "routers": {
          "type": "array",
          "items": {
            "type": "object",
            "required": ["module", "attr"],
            "properties": {
              "module": { "type": "string" },
              "attr": { "type": "string" },
              "subpath": { "type": "string" }
            }
          }
        },
        "adapters": {
          "type": "array",
          "items": {
            "type": "object",
            "required": ["name", "class_name", "module"],
            "properties": {
              "name": { "type": "string", "pattern": "^plugin-" },
              "class_name": { "type": "string" },
              "module": { "type": "string" },
              "description": { "type": "string" }
            }
          }
        },
        "hooks": {
          "type": "array",
          "items": {
            "type": "object",
            "required": ["type", "module", "function"],
            "properties": {
              "type": {
                "type": "string",
                "enum": ["startup", "shutdown", "cycle_end", "session_end",
                         "box_complete", "scan_received", "event_trigger",
                         "alarm_trigger"]
              },
              "module": { "type": "string" },
              "function": { "type": "string" },
              "phase": { "type": "string" },
              "when": { "type": "string", "enum": ["pre", "post"] },
              "priority": { "type": "integer", "minimum": 0, "maximum": 1000 }
            }
          }
        },
        "tables": {
          "type": "array",
          "items": {
            "type": "object",
            "required": ["name", "module", "class_name"],
            "properties": {
              "name": { "type": "string", "pattern": "^p_" },
              "module": { "type": "string" },
              "class_name": { "type": "string", "pattern": "^Plugin" }
            }
          }
        },
        "background_threads": {
          "type": "array",
          "items": {
            "type": "object",
            "required": ["name", "module", "function"],
            "properties": {
              "name": { "type": "string" },
              "module": { "type": "string" },
              "function": { "type": "string" },
              "auto_start": { "type": "boolean" }
            }
          }
        }
      }
    },
    "requires": {
      "type": "object",
      "properties": {
        "gpu": { "type": "boolean" },
        "cuda": { "type": ["string", "null"] },
        "python_version": { "type": "string" },
        "python_packages": {
          "type": "array",
          "items": {
            "type": "object",
            "required": ["name"],
            "properties": {
              "name": { "type": "string" },
              "version": { "type": "string" }
            }
          }
        },
        "system_libs": { "type": "array", "items": { "type": "string" } },
        "main_features": { "type": "array", "items": { "type": "string" } }
      }
    },
    "default_config": {
      "type": "object",
      "patternProperties": {
        "^plugin\\.[a-z][a-z0-9-]*\\..+$": {}
      },
      "additionalProperties": false
    },
    "runtime": {
      "type": "object",
      "properties": {
        "cpu_threshold_warn_pct": { "type": "integer", "minimum": 1, "maximum": 100 },
        "memory_threshold_warn_mb": { "type": "integer", "minimum": 50 },
        "max_hook_duration_ms": { "type": "integer", "minimum": 100 },
        "background_thread_max_count": { "type": "integer", "minimum": 0, "maximum": 10 }
      }
    },
    "metadata": { "type": "object", "additionalProperties": true },
    "export": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "templates": { "type": "array" },
        "realtime_rules": { "type": "array" },
        "field_resolvers": { "type": "array" },
        "renderers": { "type": "array" },
        "triggers": { "type": "array" }
      }
    }
  }
}
```

完整 schema 落库到 `backend/core/plugin_schema.json`，加载器用 `jsonschema` 库校验。

---

## 六、完整示例 ×3（档位 1/2/3）

### 6.1 档位 1 示例：纯主题包

```json
{
  "manifest_version": 1,
  "name": "ACME 白标主题",
  "customer_code": "acme",
  "plugin_version": "1.0.0",
  "description": "ACME 公司白标版：色板/Logo/标题全替换",
  "author": "ACME Tech <support@acme.com>",
  "homepage": "https://acme.com",
  "tier": 1,
  "capabilities": [
    "frontend.theme",
    "frontend.replace_logo",
    "frontend.replace_app_title",
    "frontend.i18n",
    "frontend.hide_menus"
  ],
  "main_version_min": "3.6.0",
  "main_version_max": "3.x",
  "created_at": "2026-05-08T05:00:00Z",
  "signed_at": "2026-05-08T05:30:00Z",
  "signed_by": "tianjun-ai-master-2026",
  "files_digest": "sha256:1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f1a2b",
  "frontend": {
    "theme": {
      "css": "frontend/theme.css",
      "logo": "frontend/assets/acme-logo.svg",
      "favicon": "frontend/assets/acme-favicon.png",
      "app_title": "ACME 视觉检测系统",
      "css_variables": {
        "--primary": "#FFEB3B",
        "--secondary": "#212121",
        "--bg-base": "#0F0F1F"
      }
    },
    "i18n": {
      "zh-CN": "frontend/i18n/zh-CN.json",
      "en-US": "frontend/i18n/en-US.json"
    },
    "hidden_menus": ["/cluster"]
  }
}
```

### 6.2 档位 2 示例：UI 插件

```json
{
  "manifest_version": 1,
  "name": "ACME 报表 + 班次管理",
  "customer_code": "acme",
  "plugin_version": "1.5.0",
  "description": "ACME 公司专属报表页 + 班次管理 UI",
  "author": "ACME Tech <support@acme.com>",
  "tier": 2,
  "capabilities": [
    "frontend.theme",
    "frontend.routes",
    "frontend.menus",
    "frontend.stores",
    "frontend.i18n"
  ],
  "main_version_min": "3.6.0",
  "main_version_max": "3.x",
  "created_at": "2026-05-08T05:00:00Z",
  "signed_at": "2026-05-08T05:30:00Z",
  "signed_by": "tianjun-ai-master-2026",
  "files_digest": "sha256:...",
  "frontend": {
    "entry": "frontend/index.js",
    "theme": {
      "css_variables": { "--primary": "#FFEB3B" }
    },
    "routes": [
      {
        "path": "report",
        "name": "report",
        "component": "frontend/views/AcmeReport.js",
        "menu_label": "ACME 报表",
        "icon": "DataLine",
        "order": 100
      },
      {
        "path": "shift",
        "name": "shift",
        "component": "frontend/views/AcmeShift.js",
        "menu_label": "班次管理",
        "icon": "Calendar",
        "order": 110
      }
    ],
    "stores": [
      { "id": "plugin-acme-shift", "module": "frontend/stores/shift.js" }
    ],
    "i18n": {
      "zh-CN": "frontend/i18n/zh-CN.json"
    }
  }
}
```

### 6.3 档位 3 示例：全栈插件（最完整）

```json
{
  "manifest_version": 1,
  "name": "ACME MES 全栈插件",
  "customer_code": "acme",
  "plugin_version": "2.0.0",
  "description": "ACME 客户专属：MQTT 推送 + 自家报表 + 班次管理 + ERP 工单号字段",
  "author": "ACME Tech <support@acme.com>",
  "homepage": "https://acme.com/plugin",
  "license_text": "本插件版权归 ACME Inc 所有，仅限 ACME 工厂使用。",
  "tier": 3,
  "capabilities": [
    "frontend.theme",
    "frontend.routes",
    "frontend.menus",
    "frontend.i18n",
    "backend.routes",
    "backend.adapter.mqtt",
    "backend.hook.cycle_end",
    "backend.hook.scan_received",
    "backend.hook.startup",
    "backend.tables",
    "backend.background_thread",
    "export.templates",
    "export.realtime_rules",
    "export.field_resolver"
  ],
  "main_version_min": "3.6.0",
  "main_version_max": "3.x",
  "created_at": "2026-05-08T05:00:00Z",
  "signed_at": "2026-05-08T05:30:00Z",
  "signed_by": "tianjun-ai-master-2026",
  "files_digest": "sha256:...",

  "frontend": {
    "entry": "frontend/index.js",
    "theme": {
      "logo": "frontend/assets/acme-logo.svg",
      "app_title": "ACME 视觉检测系统"
    },
    "routes": [
      {
        "path": "report",
        "name": "report",
        "component": "frontend/views/AcmeReport.js",
        "menu_label": "ACME 报表",
        "icon": "DataLine",
        "order": 100
      }
    ],
    "i18n": {
      "zh-CN": "frontend/i18n/zh-CN.json"
    },
    "static_dir": "frontend/static"
  },

  "backend": {
    "entry": "backend/__init__.py",
    "routers": [
      { "module": "backend.routes", "attr": "router" }
    ],
    "adapters": [
      {
        "name": "plugin-acme-mqtt",
        "class_name": "MQTTAdapter",
        "module": "backend.adapters.mqtt",
        "description": "MQTT 协议推送 ACME MES"
      }
    ],
    "hooks": [
      {
        "type": "startup",
        "module": "backend.hooks",
        "function": "init_acme",
        "priority": 500
      },
      {
        "type": "cycle_end",
        "phase": "after_workpiece_set_result",
        "module": "backend.hooks",
        "function": "push_to_acme_erp"
      },
      {
        "type": "scan_received",
        "module": "backend.hooks",
        "function": "log_scan_to_acme"
      }
    ],
    "tables": [
      {
        "name": "p_acme_shifts",
        "module": "backend.models",
        "class_name": "PluginAcmeShift"
      },
      {
        "name": "p_acme_orders_extra",
        "module": "backend.models",
        "class_name": "PluginAcmeOrderExtra"
      }
    ],
    "background_threads": [
      {
        "name": "acme-erp-poller",
        "module": "backend.threads",
        "function": "start_erp_poller",
        "auto_start": true
      }
    ]
  },

  "requires": {
    "gpu": false,
    "python_version": ">=3.10",
    "python_packages": [
      { "name": "paho-mqtt", "version": ">=1.6,<2.0" },
      { "name": "openpyxl", "version": "*" }
    ]
  },

  "default_config": {
    "plugin.acme.theme_color": "#FFEB3B",
    "plugin.acme.erp_url": "https://acme.com/erp",
    "plugin.acme.shift_labels": ["早", "中", "晚"],
    "plugin.acme.mqtt_broker": "mqtt://acme-mqtt.local:1883"
  },

  "runtime": {
    "cpu_threshold_warn_pct": 30,
    "memory_threshold_warn_mb": 500,
    "max_hook_duration_ms": 5000,
    "background_thread_max_count": 3
  },

  "export": {
    "templates": [
      {
        "name": "ACME 缺陷报告",
        "format": "docx",
        "content_path": "templates/acme-defect-report.docx",
        "is_system": false
      }
    ],
    "realtime_rules": [
      {
        "name": "ACME NG 实时导出",
        "template_name": "ACME 缺陷报告",
        "trigger_event": "cycle_end",
        "filter_config": { "is_good": [false] },
        "is_enabled": true
      }
    ],
    "field_resolvers": [
      {
        "field": "plugin.acme.erp_id",
        "module": "backend.export_resolvers",
        "function": "resolve_erp_id",
        "type": "str",
        "description": "ACME ERP 工单号"
      }
    ]
  },

  "metadata": {
    "build_machine": "ci-build-runner-3",
    "git_commit": "abc1234",
    "internal_ticket": "ACME-2026-501"
  }
}
```

---

## 七、校验流程（加载器视角）

```python
class PluginManifestValidator:
    SCHEMA = json.load(open("plugin_schema.json"))

    def validate(self, plugin_dir: Path, license_payload: dict) -> tuple[bool, str]:
        manifest_path = plugin_dir / "plugin.json"

        # 阶段 1: 文件存在 + 大小
        if not manifest_path.exists():
            return False, "MANIFEST_NOT_FOUND"
        if manifest_path.stat().st_size > 256 * 1024:
            return False, "MANIFEST_TOO_LARGE"

        # 阶段 2: JSON 解析
        try:
            manifest_bytes = manifest_path.read_bytes()
            manifest = json.loads(manifest_bytes.decode("utf-8"))
        except UnicodeDecodeError:
            return False, "MANIFEST_NOT_UTF8"
        except json.JSONDecodeError as e:
            return False, f"MANIFEST_INVALID_JSON: {e}"

        # 阶段 3: JSON Schema 校验
        try:
            jsonschema.validate(manifest, self.SCHEMA)
        except jsonschema.ValidationError as e:
            return False, f"MANIFEST_SCHEMA_FAIL: {e.message} at {list(e.absolute_path)}"

        # 阶段 4: 业务逻辑校验
        # 4.1 customer_code 与目录名一致
        if plugin_dir.name != manifest["customer_code"]:
            return False, "MANIFEST_CUSTOMER_CODE_DIR_MISMATCH"

        # 4.2 customer_code 与 license 一致
        if manifest["customer_code"] != license_payload.get("customerName"):
            return False, "PLUGIN_LICENSE_MISMATCH"

        # 4.3 主程序版本兼容
        from packaging.version import Version
        from .version import MAIN_VERSION
        if Version(MAIN_VERSION) < Version(manifest["main_version_min"]):
            return False, "PLUGIN_MAIN_VERSION_TOO_LOW"
        if (max := manifest.get("main_version_max")) and not version_match(MAIN_VERSION, max):
            return False, "PLUGIN_MAIN_VERSION_TOO_HIGH"

        # 4.4 tier 与 backend/frontend 字段一致
        tier = manifest["tier"]
        if tier == 1 and "backend" in manifest:
            return False, "MANIFEST_TIER_BACKEND_FORBIDDEN"
        if tier == 3 and "backend" not in manifest:
            return False, "MANIFEST_TIER_BACKEND_REQUIRED"

        # 4.5 capabilities 与实际声明一致
        # ...省略细节, 详见 design 06...

        # 4.6 customer_code 命名空间检查
        cc = manifest["customer_code"]
        for key in (manifest.get("default_config") or {}):
            if not key.startswith(f"plugin.{cc}."):
                return False, "MANIFEST_DEFAULT_CONFIG_KEY_INVALID"
        for table in (manifest.get("backend") or {}).get("tables", []):
            if not table["name"].startswith(f"p_{cc}_"):
                return False, "MANIFEST_TABLE_PREFIX_INVALID"

        # 4.7 文件 digest 校验
        actual_digest = calc_files_digest(plugin_dir)
        if actual_digest != manifest["files_digest"]:
            return False, "PLUGIN_FILES_DIGEST_MISMATCH"

        # 阶段 5: 依赖校验
        if (req := manifest.get("requires")):
            if req.get("gpu") and not torch.cuda.is_available():
                return False, "PLUGIN_GPU_REQUIRED_BUT_NOT_AVAILABLE"
            # python_packages 检查在 design 06

        # 阶段 6: 签名校验（详见 design 02）
        if not verify_signature(plugin_dir, manifest_bytes):
            return False, "PLUGIN_SIGNATURE_FAIL"

        return True, "OK"
```

---

## 八、版本演进策略

`manifest_version` 的演进策略：

| 改动类型 | 是否升级 manifest_version | 例子 |
|---|---|---|
| 加新可选字段 | ❌ 不升级 | 加 `requires.docker` |
| 加新 capabilities 枚举 | ❌ 不升级 | 加 `backend.hook.workpiece_action` |
| 改字段类型 | ✅ 升级 | `tier` 从 int 变成 string |
| 删字段 | ✅ 升级 | 删 `frontend.entry` |
| 重命名字段 | ✅ 升级 | `signed_by` 改 `signer` |
| 强制要求新字段 | ✅ 升级 | 强制要求 `metadata.git_commit` |

### 8.1 manifest_version=1 的兼容窗口

- 主程序 v3.6.x ~ v4.0.x 都支持 manifest_version=1
- v4.0 计划升级到 manifest_version=2（届时插件需重新签名）
- 同时支持 v=1 和 v=2 一段时间（v4.0 ~ v4.5）

### 8.2 升级 manifest_version 的流程

1. 在 `design/01_manifest_schema_v2.md` 写新版定义
2. 主程序代码同时支持两版（schema 双版本）
3. 通知插件作者重打包
4. 半年后下线 v=1 支持

---

## 九、命名空间二次确认（强制）

| 资源 | 命名空间 | 校验位置 |
|---|---|---|
| plugins/ 目录名 | == manifest.customer_code | 阶段 4.1 |
| `plugin.{cc}.*` SystemConfig 键 | manifest.default_config 内必须 | 阶段 4.6 |
| `p_{cc}_*` ORM 表名 | backend.tables[].name 必须 | 阶段 4.6 |
| `Plugin{Cc}*` ORM 类名 | backend.tables[].class_name 必须 | jsonschema |
| `plugin-{cc}-*` adapter name | backend.adapters[].name 必须 | jsonschema |
| `plugin-{cc}-*` Pinia store id | frontend.stores[].id 必须 | jsonschema |
| `Plugin-{cc}-*` 路由 name | frontend.routes[].name 加载时拼出 | 加载器 |
| `/plugin/{cc}/*` 路由 path | frontend.routes[].path 加载时拼出 | 加载器 |
| `/api/v1/plugins/{cc}/*` 后端路由 prefix | backend register_plugin 内拼 | 加载器 |
| `plugin.{cc}.*` i18n keys | 客户在 i18n JSON 内自觉遵守，主程序不强制 | （warning） |
| `plugin.{cc}.*` export field name | export.field_resolvers[].field 必须 | 阶段 4.6 |

---

## 十、错误代码总表

| 错误代码 | 含义 | 处理 |
|---|---|---|
| `MANIFEST_NOT_FOUND` | plugin.json 不存在 | 跳过 |
| `MANIFEST_TOO_LARGE` | manifest 超过 256 KB | 跳过 |
| `MANIFEST_NOT_UTF8` | 非 UTF-8 编码 | 跳过 |
| `MANIFEST_INVALID_JSON` | JSON 解析失败 | 跳过 |
| `MANIFEST_SCHEMA_FAIL` | JSON Schema 校验失败 | 跳过 |
| `MANIFEST_CUSTOMER_CODE_FORMAT` | customer_code 格式不合规 | 跳过 |
| `MANIFEST_CUSTOMER_CODE_DIR_MISMATCH` | customer_code ≠ 目录名 | 跳过 |
| `MANIFEST_VERSION_TOO_NEW` | manifest_version > 主程序支持 | 跳过 |
| `MANIFEST_NAME_INVALID` | name 含换行 / 太长 | 跳过 |
| `MANIFEST_PLUGIN_VERSION_FORMAT` | 不是 SemVer | 跳过 |
| `MANIFEST_DESCRIPTION_TOO_LONG` | description > 200 字符 | 跳过 |
| `MANIFEST_AUTHOR_INVALID` | author 字段格式错 | 跳过 |
| `MANIFEST_HOMEPAGE_NOT_HTTPS` | homepage 不是 HTTPS | 跳过 |
| `MANIFEST_TIER_INVALID` | tier ∉ {1,2,3} | 跳过 |
| `MANIFEST_TIER_BACKEND_FORBIDDEN` | tier 1/2 写了 backend | 跳过 |
| `MANIFEST_TIER_BACKEND_REQUIRED` | tier 3 没写 backend | 跳过 |
| `MANIFEST_CAPABILITY_UNKNOWN` | capabilities 项不在枚举 | 跳过 |
| `MANIFEST_CAPABILITY_INCONSISTENT` | capabilities 与实际声明不一致 | 跳过 |
| `MANIFEST_DATE_FORMAT` | created_at / signed_at 不是 ISO | 跳过 |
| `MANIFEST_SIGNED_AT_FUTURE` | signed_at 是未来时间 | 跳过 |
| `MANIFEST_SIGNED_BY_INVALID` | signed_by 不合规 | 跳过 |
| `MANIFEST_DEFAULT_CONFIG_KEY_INVALID` | default_config 键不以 `plugin.{cc}.` 开头 | 跳过 |
| `MANIFEST_TABLE_PREFIX_INVALID` | 表名不以 `p_{cc}_` 开头 | 跳过 |
| `MANIFEST_HIDDEN_MENU_FORBIDDEN` | 试图隐藏 `/monitor` 等核心菜单 | 跳过 |
| `MANIFEST_I18N_LOCALE_UNKNOWN` | i18n 键不在已知 locale | 跳过 |
| `MANIFEST_I18N_FILE_NOT_FOUND` | i18n 文件不存在 | 跳过 |
| `PLUGIN_LICENSE_MISMATCH` | customer_code ≠ license.customerName | 跳过 |
| `PLUGIN_MAIN_VERSION_TOO_LOW` | 主程序版本 < main_version_min | 跳过 |
| `PLUGIN_MAIN_VERSION_TOO_HIGH` | 主程序版本 > main_version_max | 跳过 |
| `PLUGIN_FILES_DIGEST_MISMATCH` | 文件被改 / digest 算错 | 跳过（可能恶意） |
| `PLUGIN_GPU_REQUIRED_BUT_NOT_AVAILABLE` | requires.gpu=true 但客户机无 GPU | 跳过 |
| `PLUGIN_SIGNATURE_FAIL` | RSA / HMAC 验签失败 | 跳过 |
| `PLUGIN_DEPENDENCY_NOT_FOUND` | python_packages 中某个包不可用 | 跳过 |

---

## 十一、待 design 02 / 03 / 06 细化

- **签名格式**（signature.bin 内具体字节布局） → design 02
- **数据库表**（plugins / plugin_state） → design 03
- **加载器实现**（`PluginManager` 类完整代码） → design 06
- **register_plugin 接口契约**（registry 参数传什么） → design 06
- **Hook 注册接口**（每个 hook type 的具体函数签名） → design 06

---

## 十二、本节决策摘要（供下游文档引用）

| 决策 | 值 |
|---|---|
| manifest 文件格式 | UTF-8 JSON, sort_keys, ≤ 256KB |
| 当前 manifest_version | 1 |
| customer_code 正则 | `^[a-z][a-z0-9-]{2,19}$` |
| files_digest 算法 | SHA256（路径排序 + 文件内容） |
| capabilities 枚举数 | 26 |
| 错误代码数 | 33 |
| 三档必填字段差异 | tier=1 必 frontend.theme / tier=2 必 frontend.routes / tier=3 必 backend |

---

**本文最后更新**：2026-05-08
**事实校验**：基于 design 00 + inventory 03 / 04 + brief Q1~Q8 推荐答案 + 现有 License manager 加载流程
