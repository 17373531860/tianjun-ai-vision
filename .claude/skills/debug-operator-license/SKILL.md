---
name: debug-operator-license
description: "诊断操作员管理与 License 授权：machineId 不一致、授权过期 / 不匹配、激活失败、当前操作员落盘、License 缓存推送后端。当客户机激活码不通过、'授权与本机不匹配'、操作员选择丢失时使用。"
argument-hint: "[问题现象]"
model: opus
effort: high
allowed-tools: "Read, Grep, Glob, Bash, mcp__sequential-thinking"
---

# debug-operator-license: 操作员 + License 诊断

> 这是两条独立但相邻的子系统：
> 1. **操作员**（Operators）—— 工人选谁在使用，纯业务概念，无 token，文件落盘
> 2. **License**（授权）—— 客户授权码 / RSA 签名 / machineId 绑定，**Electron 主进程**负责，**不走后端**
>
> 主进程（Electron）验证 license 后，再通过 IPC + HTTP 把 license 信息缓存到后端，模板里 `{{ license.* }}` 才能用。

问题现象: $ARGUMENTS

---

## 一、子系统全貌

### 1.1 操作员

| 角色 | 文件 | 行数 | 职责 |
|---|---|---:|---|
| API | `backend/api/operators.py` | 213 | CRUD + 当前操作员（按 channel）|
| ORM | `backend/models/models.py` 中 `Operator` | — | id / name / employee_no / role / active / created_at |
| 当前操作员落盘 | `backend/data/current_operator.json` | — | `{channel_id: operator_id}` JSON |
| 前端 | `frontend/src/views/Operators/*` 或 MES 子页 | — | 工人列表 + 切换当前 |

**关键设计**：
- **无登录 token**：项目从未要求工人登录；操作员只是"现在谁在用"的元数据
- **当前操作员按 channel 隔离**：多工位时每个 channel 独立选人
- **进程重启不丢**：v3.x 改为 `current_operator.json` 落盘（早期是模块级 dict 重启即丢）

**API 端点（6 个，前缀 `/api/v1/operators/`）**：

| Method | Path | 作用 |
|---|---|---|
| GET | `/` | 列表（含分页/active 过滤） |
| POST | `/` | 创建 |
| PUT | `/{id}` | 更新 |
| DELETE | `/{id}` | 删除（软删除？需查代码） |
| GET | `/current` | 读当前操作员（带 channel_id 查询） |
| POST | `/current` | 设置当前操作员（带 channel_id） |

### 1.2 License

| 角色 | 文件 | 职责 |
|---|---|---|
| Electron 主进程入口 | `electron/main.js` | 启动时调 `licenseManager.verify()`，开发模式跳过 |
| LicenseManager 类 | `electron/license-manager.js` | 完整加密验证 + machineId 生成 + 自动安装 |
| 后端 license 缓存 | `backend/api/system_display.py` | `POST /license-cache` 接收前端推送，存 `system_configs` 表 |
| 公钥（PUBLIC_KEY）| `electron/license-manager.js` 顶部硬编码 | RSA 2048 公钥 |
| License 文件位置 | `{userData}/license.lic` | JSON 格式，含 `data` + `signature` |
| machineId 缓存 | `{userData}/machine_id.txt` + `hw_verify.txt` | 加速 + 防止换机器复用 |
| 自动安装路径 | `{resourcesPath}/licenses/<machineId>.lic` | 安装包带客户机 license，安装时自动复制 |

---

## 二、关键概念

### 2.1 machineId 生成（**必看**，最容易出问题）

> **v3.10.2 重做** —— v3.10.1 及之前版本存在严重 bug，导致同型号工控机批量出货时撞 ID，详见下文 2.1.x。

**v3.10.2+ 来源**：稳定硬件指纹，**5 强 + 2 弱**多源采集，黑名单过滤占位值，强度守门。

| 来源 | 类别 | Windows API | Linux 路径 |
|---|---|---|---|
| 主板序列号 | **强** | `Win32_BaseBoard.SerialNumber` | `/sys/class/dmi/id/board_serial` |
| BIOS UUID | **强** | `Win32_ComputerSystemProduct.UUID` | `/sys/class/dmi/id/product_uuid`（root） |
| BIOS 序列号 | **强** | `Win32_BIOS.SerialNumber` | `/sys/class/dmi/id/product_serial` |
| 系统盘序列号 | **强** | `Win32_DiskDrive.SerialNumber` | `lsblk -ndo SERIAL,MODEL` |
| 第一块物理网卡 MAC | **强** | `Get-NetAdapter -Physical` | `/sys/class/net/<iface>/address`（排除 lo/docker/br-/veth/virbr） |
| 主板型号 | 弱（不计入强标识源数） | `Win32_BaseBoard.Product` | `/sys/class/dmi/id/board_name` |
| CPU 型号 | 弱（不计入强标识源数） | `os.cpus()[0].model` | 同左 |

```js
machineId = "TJ-" + SHA256(parts.join("|")).substring(0, 12).toUpperCase()
// 例如: TJ-BD600BF2727B (共 15 字符)
```

**Windows 实施细节（v3.10.2）**：
- **优先 PowerShell `Get-CimInstance`**：单次调用一把取回所有字段，比串行 5 次 wmic 快得多，**不依赖 Win11 24H2 已废弃的 wmic 工具**
- **wmic 兜底**：Win10 / Win11 早期版本继续能跑
- **黑名单过滤**：拒收 `"To be filled by O.E.M."` / 全 0 UUID / 全 F UUID / `"Default string"` / `"Not Specified"` 等工控机厂家常见占位值（详见 `BAD_VALUES` 集合）

**强度守门**：
- 强标识源 ≥ 1 → 正常生成 machineId
- 强标识源 = 0 → 打 ERROR log `!!!! NO STRONG HARDWARE IDENTIFIER AVAILABLE !!!!` + **不写缓存** + machineId 标记为 `weak`（防止该 ID 被持久化到撞 ID 状态）

**老机器零影响兼容**：
- `getMachineId()` 第一步读 `machine_id.txt`，缓存命中且 `hw_verify.txt` 校验通过 → 直接返回老 ID，不重算
- 即升级 v3.10.2 后，**老客户机器沿用老 ID，老 license 继续有效**

### 2.1.1 v3.10.1 及之前撞 ID bug（**历史踩坑**）

**事故事实**：现场 7-8 台不同的工控机生成同一个 machineId（`TJ-F64AF21C6DBB`），license 失去机器绑定意义。

**老算法（v3.10.1 及之前）**：
```js
Windows:
  parts = [
    wmic baseboard get product /value,    // 主板型号 → 同型号必相同
    wmic csproduct get uuid /value,       // 工控机厂家常忘刷 → 全 0 / 全 F
    os.cpus()[0].model                    // CPU 型号 → 同款必相同
  ]
```

**三重失败**：
1. `wmic baseboard get product` 取**主板型号**（不是序列号），同型号工控机批量出货时值完全一样（例如全是 `B660M-DS3H`）
2. `wmic csproduct get uuid` 工控机厂家批量烧 BIOS 时常常忘刷，全 0 / 全 F / 模板 UUID 是已知问题
3. `os.cpus()[0].model` 是 CPU 型号字符串，同款 CPU 必定一样

**雪上加霜**：Win11 24H2 起 Microsoft 默认移除 `wmic`，`execSafe('wmic ...')` 直接返回空字符串 → 前两个来源都空 → 只剩 CPU 型号 → 同款 CPU 机器全是同一 machineId。`execSafe` 失败兜底返回 `''`，`parts.filter(Boolean).join('|')` 把空值过滤掉，**没有任何报错日志**，所以一直没人发现。

### 2.1.2 现场撞 ID 处置（**给客户支持工程师**）

| 场景 | 处置 |
|---|---|
| 老机器（已激活的客户机） | **完全不需要操作**：升级 v3.10.2 后沿用老 ID，老 license 继续有效 |
| 现场已撞 ID 的机器 | 远程让客户删 3 个文件 → 重启 → 拿到新 ID → 重发 license（详见下方步骤） |
| 新装机 | 自动用新算法，强标识源 ≥ 1 即可生成稳定唯一 ID |
| 强标识源全部为 0（极端工控机） | 联系厂家在 BIOS 写真序列号 / 升级 TPM 2.0 / 业务层用客户名 + 工位号双重绑定 |

**撞 ID 机器重发 license 步骤**：
1. 远程让客户**关闭天军软件**
2. 删除：
   - `%APPDATA%\tianjun-ai-vision\machine_id.txt`
   - `%APPDATA%\tianjun-ai-vision\hw_verify.txt`
   - `%APPDATA%\tianjun-ai-vision\license.lic`
3. 重新打开天军软件 → 显示"未授权"+ 新 machineId
4. 客户把新 machineId 报回来 → `python backend/scripts/generate_license.py --machine-id <新ID> --customer "<客户名>" --expires <YYYY-MM-DD>`

### 2.1.3 现场诊断工具

- **`electron/scripts/diagnose-machine-id.bat`**：客户机双击就能跑，输出每个硬件源的返回值 + 哪些被黑名单拒收 + 最终 machineId + 强标识源数量，自动写 `machineid-report-<hostname>-<时间戳>.txt`
- **IPC `get-machine-id-report`**：前端可读 `electronAPI.getMachineIdReport()` 拿到详细指纹采集报告（用于后续在"设置"页加诊断面板）

### 2.2 machineId 缓存与防换机

`machine_id.txt` + `hw_verify.txt` 两个文件：

- 第一次运行：计算指纹 → 生成 machineId → 同时写两个文件
- 后续运行：读 cached → 重新算 verify hash → 对比；不一致说明硬件变了（换机器或主板换了），重新计算

### 2.3 License 文件结构

```json
{
  "data": "<JSON 字符串>",
  "signature": "<base64 RSA-SHA256 签名>"
}
```

`data` 反序列化后：

```json
{
  "machineId": "TJ-XXXXX",
  "customerName": "...",
  "expiresAt": "2026-12-31"  // ISO 字符串，无则永久
}
```

### 2.4 验证流程（`LicenseManager.verify()`）

1. 读 `license.lic` → JSON parse → 拿 data + signature
2. RSA SHA256 验签（公钥验 data 的 signature）
3. 反序列化 data → 比对 `data.machineId === thisMachine.machineId`
4. 比对过期时间（如有）
5. 全部通过 → return `{ valid: true, info: {...} }`

### 2.5 IPC 通道

| 通道名 | 方向 | 用途 |
|---|---|---|
| `get-app-info` | 渲染 → 主 | 获取版本号 + 平台 |
| `get-backend-url` | 渲染 → 主 | 拿后端地址（端口） |
| `get-license-status` | 渲染 → 主 | 查授权状态 + machineId + 客户名等 |
| `import-license` | 渲染 → 主 | 从用户选的文件导入并验证 |
| `license-activated` | 主 → 渲染 | 验证通过广播给前端 |
| `shutdown-*` | 主 ↔ 渲染 | 关机相关，与 license 无关 |

### 2.6 License 缓存到后端

为啥要缓存？模板里要用 `{{ license.customer_name }}` 这种字段。

流程：

```
Electron 验证通过
  → mainWindow.webContents.send('license-activated', info)
渲染进程收到
  → 调 ipcMain.handle('get-license-status') 拿详情
  → 用 axios POST <后端>/api/v1/system/license-cache
后端 system_display.py
  → 写 system_configs 表 KV：license_customer / license_expires_at / license_machine_id
导出渲染：
  → build_system_context 读这些 KV → 出现在 {{ license.* }}
```

---

## 三、常见问题排查

### 3.1 "授权与本机不匹配"（machine_mismatch）

**最常见！**

**原因**：客户拿了别人的 lic 装到自己机器；或者你给客户发 lic 时用错了 machineId。

**排查**：

1. 让客户截图启动时控制台 / 激活页显示的 machineId
2. 对比 lic 文件里的 `data.machineId`：
   ```bash
   cat <userData>/license.lic | python3 -c "import json,sys; d=json.load(sys.stdin); print(json.loads(d['data'])['machineId'])"
   ```
3. 不一致 → 重新签发 lic 给客户当前的 machineId

**找客户机的 userData**：
- Windows: `%APPDATA%\tianjun-vision\` 或 `%APPDATA%\<app-name>\`（看 `electron/package.json` 中 productName）
- Linux: `~/.config/<app-name>/`

### 3.2 "未找到授权文件"（no_license）

- 客户没把 lic 放到 `{userData}/license.lic`
- 或者：自动安装期望路径错了。检查 `tryAutoInstall()`：
  - `<resourcesPath>/licenses/<machineId>.lic`
  - `<resourcesPath>/../licenses/<machineId>.lic`
- 安装包打包时是否把客户的 lic 放到 `licenses/` 目录

### 3.3 "授权签名验证失败"（invalid_signature）

- lic 文件被改坏（编辑过 data 或 signature）
- 公钥不匹配（私钥被换了重新生成 lic 但客户机还是老公钥）→ **不要换密钥**！
- base64 编码 / 解码出错（罕见，签发工具问题）

### 3.4 "授权已过期"（expired）

- `data.expiresAt` < now
- 客户改了系统时间想欺骗？看 `expiresAt` 字段（ISO 字符串），可手动验

### 3.5 machineId 每次开机变了

**症状**：客户重启后，machineId 变了，导致原 lic 不匹配。

**可能原因**：
- 客户机 dmi 信息为空（虚拟机 / 容器 / 某些 OEM 机）
- `wmic` 命令权限问题
- `verify hash` 文件被删 → 但理论上即使删了，machineId 还是同一个（基于硬件）
- 主板更换 / BIOS 重置 → 真换硬件了

**排查**：
```bash
# 客户机控制台日志看
[License] Stable fingerprint: <内容>
```
内容前后两次是否一致？空字符串 → 硬件指纹源头出问题。

### 3.6 当前操作员不持久 / 重启丢

**症状**：客户每次开机都要重新选人。

**v3.x 已修复**：现在落盘到 `backend/data/current_operator.json`（DATA_DIR 下）。

**仍有问题排查**：
1. 看 `DATA_DIR` 路径：`grep DATA_DIR backend/core/config.py`
2. 看落盘文件是否能写入（权限）
3. 看 `_save_current_operator` 调用点（每次 set 应触发）

### 3.7 license 在模板里 `{{ license.customer_name }}` 输出空

**排查**：
1. 启动后是否调过 `POST /api/v1/system/license-cache`？前端控制台 Network 看
2. `system_configs` 表里有 `license_customer` 这条 KV 吗？
3. `build_system_context` 是否读了这些 KV？grep `license` 在 `export_context.py`

---

## 四、调试入口（按问题类型）

| 现象 | 第一步看 | 第二步看 |
|---|---|---|
| machine_mismatch | 客户机 machineId | lic 文件里 machineId |
| no_license | userData 路径 | 自动安装路径 |
| invalid_signature | lic 文件完整性 | 公钥版本 |
| expired | expiresAt | 客户系统时间 |
| machineId 每次变 | stable_fingerprint 内容 | 硬件源头是否空 |
| 操作员不持久 | current_operator.json 文件 | DATA_DIR 路径 |
| license 缓存丢 | POST /license-cache 调用 | system_configs 表 |
| 模板 license 字段空 | system_context.license | export_context 代码 |

---

## 五、关键文件速查

```
electron/main.js                            主进程入口（调 licenseManager.verify()）
electron/license-manager.js                 License 类（公钥/验签/machineId）
backend/api/operators.py                    操作员 CRUD (213 / 6 端点)
backend/api/system_display.py               license 缓存接收 (135 / 4 端点)
backend/data/current_operator.json          当前操作员落盘
{userData}/license.lic                      授权文件
{userData}/machine_id.txt + hw_verify.txt   machineId 缓存
{resourcesPath}/licenses/<machineId>.lic    安装包预置 lic（自动安装来源）
backend/services/export_context.py          build_system_context 读 license_*
backend/models/models.py 中 SystemConfig    KV 表
backend/models/models.py 中 Operator        操作员 ORM
```

---

## 六、签发新 license 流程（开发者端）

> 不在这个 skill 范围内详写，但简要：

1. 拿到客户机的 machineId（让客户截图激活页或控制台日志）
2. 写 license 数据 JSON：`{"machineId": "TJ-XXXX", "customerName": "...", "expiresAt": "..."}`
3. 用私钥（**严密保管，不在仓库**）对 data 字符串做 SHA256 签名 → base64
4. 组装 `{"data": "<json string>", "signature": "<base64>"}` → 存为 `<machineId>.lic`
5. 通过 IM 发给客户

公钥在仓库里（`license-manager.js` 顶部）；私钥**绝不能进仓库**。

---

## 七、相关 skill

- `debug-electron` — Electron 主进程更广问题
- `add-api-endpoint` — 加 system 相关端点
- `modify-model` — 改 SystemConfig / Operator 字段
- `debug-export` — 模板里 `{{ license.* }}` 渲染问题先看那里

---

## 八、已知坑

1. **公钥只在 license-manager.js**：换私钥会让所有老客户失效，不要换
2. **lic 文件用户能看到**：默认放在 userData，路径透明，但内容签名保护，改不动
3. **操作员的 channel_id 是 int，但 JSON 里是 str**：API 已用 `int(k)` cast，新代码沿用
4. **当前操作员清空**：传 `operator_id: null` 等价于"现在没人选"
5. **dev 模式跳过 License**：`electron/main.js` 中 `if (isDev) skip`，开发期不要混淆"我没装 lic 但能跑"——发版后真的会拦
6. **machineId 缓存 + verify hash**：换主板会失效但 machineId.txt 可能仍存在导致误判，需要删掉两个文件强制重算
7. **license 后端缓存依赖前端推送**：如果 Electron 启动正常但前端没推 license-cache，模板里 `{{ license.* }}` 是空——别忘了这条链路
