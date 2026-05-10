# 插件系统设计变更记录

## 2026-05-10

### Added

- 新增 `plugins-examples/` 三档示例插件骨架：
  - Tier 1 Theme
  - Tier 2 UI
  - Tier 3 Full-stack
- 新增 `.github/workflows/plugin-tooling.yml`，CI 校验 schema、工具语法、单元测试和示例打包。
- 新增 `implementation/ISSUES.md`，把 F1~F15 拆成可执行 issue。
- 新增两份 ADR：
  - `adr/0001-signature-and-digest.md`
  - `adr/0002-frontend-plugin-loading.md`
- 新增 `license-plugin-integration.md`，定义 License 与 Plugin 的最小对接契约。
- 新增 `error-codes.i18n.json`，先落地 14 个关键错误码的中英双语文案。
- 新增 `scripts/plugin/lint-plugin-docs.py`，用于文档链接、JSON、示例 manifest 校验。

### Changed

- `files_digest` 算法明确排除 `plugin.json` 与 `signature.bin`：
  - `plugin.json` 由 RSA 签名保护
  - `signature.bin` 是签名产物自身
  - 避免 `files_digest` 自引用循环
- `pack-plugin.py` 的 ZIP 文件收集逻辑与 digest 文件遍历逻辑解耦，确保 ZIP 仍包含 `plugin.json`。
- 未签名 manifest 使用 `signed_at=1970-01-01T00:00:00+00:00` 与 `signed_by=unsigned-dev-build` 作为占位，签名阶段覆盖。

### Fixed

- 插件系统测试从 `55 passed / 7 failed` 修复为 `63 passed`。

## 2026-05-09

### Added

- 完成插件系统 inventory 01~05。
- 完成插件系统 design 00~08。
- 新增 `REVIEW_CHECKLIST.md`。
- 新增 `customer-codes.md`。
- 新增主作者密钥生成脚本 `scripts/plugin/gen-master-keypair.py`。
- 新增 `plugin.schema.json` 与插件工具骨架。

### Fixed

- 修复 BUG-1：`backend/core/config.py` 中 `_fix_db_paths` 使用错误表名 `ml_models`，已改为 `models`。
- 验证并撤销 INCONSIST-2：`mes_gateway` 实际路径是 `/api/v1/mes/gateway/*`，与 `AGENTS.md` 一致。
