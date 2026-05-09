# 插件系统工具集 — `scripts/plugin/`

> 配套 design 文档：[`docs/plugin-system/design/07_distribution.md`](../../docs/plugin-system/design/07_distribution.md)

本目录存放插件系统的命令行工具，与 `scripts/` 下原有的应用打包脚本（`build-app.bat` 等）**严格分开**。

## 工具清单（按角色）

### 主作者专用（高度敏感, 绝不在 CI 跑）

| 脚本 | 作用 | 频率 | 状态 |
|---|---|---|---|
| `gen-master-keypair.py`     | 一次性生成 RSA-4096 私钥 + PLUGIN_SECRET | 一次/紧急轮换 | ✅ 已实现 |
| `inject-public-key.py`      | 把公钥嵌入到 `backend/plugin_system/plugin_public_keys.py` | 每次轮换 | ✅ 骨架完成 |
| `sign-plugin.py`            | 给 `.tjvplugin-uns` 签 RSA + HMAC | 每次发版 | ✅ 骨架完成（待端到端测试）|

### 插件作者用（半敏感）

| 脚本 | 作用 | 状态 |
|---|---|---|
| `pack-plugin.py`            | manifest 校验 / vite build / files_digest / 打 ZIP（不签）| ✅ 骨架完成（vite build 部分待 v3.7）|
| `dev-plugin.py`             | 本地热重载开发（DEBUG_MODE 下跳过验签）| ✅ 骨架完成（watch mode 待）|

### 客户/集成测试用

| 脚本 | 作用 | 状态 |
|---|---|---|
| `verify-plugin.py`          | 离线验签（无需 PLUGIN_SECRET，只验 RSA + files_digest）| ✅ 骨架完成 |
| `install-plugin.py`         | CLI 安装（与 UI 安装等效, 用于 IT 部署）| ✅ 骨架完成（HTTP 模式待 backend API）|

### 共享代码

| 文件 | 作用 |
|---|---|
| `_plugin_common.py`         | 集中实现 files_digest / pubkey fingerprint / RSA-PSS / HMAC / signature.bin 序列化（其他工具与后端 PluginManager 都引用）|

## 安全约束

- 主作者脚本**必须**在物理隔离机器上跑：
  - 不联网
  - 没有剪贴板同步 / 远程桌面 / IM
  - 加密分区作输出目录
  - 跑完 `shred -uvz` + `history -c` + 关机
- `PLUGIN_SECRET` 与 `plugin_master_pri.pem` 二者只允许保存：
  - 加密 USB ×2（主备 + 异地）
  - 1Password（主备账号）
  - 物理保险柜（两个独立位置）
- 仓库里只能存放：
  - `plugin_master_pub.pem`
  - `fingerprint.txt`
  - `METADATA.json`（不含敏感字段）

## 首次使用（主作者）

```bash
# 1. 切到加密分区
cd /mnt/encrypted

# 2. 安装依赖（仅本地隔离机）
pip install cryptography>=41.0.0

# 3. 一次性生成
python /path/to/repo/scripts/plugin/gen-master-keypair.py --out ./keygen-20260508

# 4. 按脚本输出的 8 步指引备份与销毁
```

详见 `gen-master-keypair.py --help` 与 design/02_signature.md §八/§九。

## 与 CI 的关系

| 工具 | CI 运行 | 原因 |
|---|---|---|
| `gen-master-keypair.py` | ❌ **永远不** | 私钥不能离开主作者控制 |
| `inject-public-key.py`  | ❌ **永远不** | 公钥变更触发主程序版本号修订 |
| `sign-plugin.py`        | ⚠ 可选（默认不） | 见 design/07 §6.2，若启用需 GitHub Environment Secret |
| `pack-plugin.py`        | ✅ 可 | 输出未签 ZIP，CI 完成后由主作者本地签名 |
| `verify-plugin.py`      | ✅ 可 | 仅校验，不暴露密钥 |

## 历史

- 2026-05-08  目录创建 + `gen-master-keypair.py` 首版实现
