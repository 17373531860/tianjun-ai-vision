# ADR 0001: 插件签名采用 RSA-PSS + HMAC，files_digest 排除 manifest

## 状态

Accepted — 2026-05-10

## 背景

插件包需要同时解决三类问题：

- 防伪造：客户或第三方不能自己造包冒充主作者签发
- 防篡改：包内前后端代码、模板、资源不能被改
- 客户绑定：A 客户的插件不能直接拷贝到 B 客户机器上使用

同时，`plugin.json` 本身包含 `files_digest`、`signed_at`、`signed_by` 等签名相关字段。如果把 `plugin.json` 直接纳入 `files_digest`，会出现自引用循环：

```text
files_digest = sha256(plugin.json(files_digest=...?))
```

签名阶段还会更新 `signed_at/signed_by`，如果 digest 覆盖 `plugin.json`，签名后校验也会失败。

## 决策

采用三层保护：

1. `files_digest`
   - SHA256 覆盖插件包内业务文件
   - 排除：`plugin.json`、`signature.bin`、`__pycache__`、`.git`、`node_modules` 等噪声文件
2. RSA-PSS-SHA256
   - 对 canonical `plugin.json` 签名
   - 保护 manifest 的 `customer_code`、`files_digest`、`signed_at`、`signed_by` 等字段
3. HMAC-SHA256
   - `HMAC(PLUGIN_SECRET, customer_code|files_digest)`
   - 绑定客户码与包内容

## 结果

- 改业务文件：`files_digest` 失败
- 改 `plugin.json`：RSA 失败
- 改 `signature.bin`：RSA/HMAC/fingerprint 失败
- 拷贝到其他客户：License customerName 与 manifest customer_code 不一致，或 HMAC 校验失败

## 被拒绝方案

### A. 把 `plugin.json` 纳入 digest 并把 `files_digest` 字段置零再算

拒绝原因：

- 工具链必须维护“规范化但忽略某字段”的特殊算法
- 实现复杂，容易出现 pack/sign/verify 三端不一致

### B. 不做 files_digest，只签 manifest

拒绝原因：

- manifest 只能声明文件路径，不能证明文件内容未变
- 攻击者可保留 manifest/signature，替换 `backend/*.py` 或 `frontend/*.js`

### C. Ed25519 替代 RSA-PSS

拒绝原因：

- RSA-PSS 在当前 Python `cryptography`、企业审计和现有许可证体系里更常见
- 4096-bit RSA 签名体积可接受（插件包远大于 512 byte）

## 实施引用

- `scripts/plugin/_plugin_common.py`
- `tests/plugin_system/test_files_digest.py`
- `tests/plugin_system/test_sign_verify_roundtrip.py`
