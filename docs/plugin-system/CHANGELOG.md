# 插件系统设计变更记录

## 2026-05-28 (后续)

### M3.3 + M1.3b 部分交付 — `write_plugin_step_field` 真实现

- **`step_records.plugin_data: JSON` 字段落地**（`backend/models/models.py`）+ `migrate_database()` 加 ALTER TABLE 探针（老客户库升级自动补列）
- **`PluginHost.write_plugin_step_field(step_record_id, key, value)` 从 stub 升级为真实现**（`backend/plugin_system/registry.py`）：
  - 需声明新 capability `runtime.step_field_write`
  - key 必须 `plugin_<customer_code>_` 前缀（命名空间隔离，与 `write_system_config` 一致）
  - value 必须可 JSON 序列化（lambda / 自定义类被拒，audit rejected，不抛）
  - step_record_id 不存在 → 返 False + audit rejected
  - **JSON 合并语义**：浅拷 + 整字段重赋（不直接 `row.plugin_data[key] = value`），防 SQLAlchemy mutation 检测失效导致不 UPDATE；不删其它 key（含其他客户插件的 key）
  - audit log + 错误隔离与现有 4 个主动 API 一致
- **manifest schema capabilities 词汇表加 `runtime.step_field_write` 枚举**（`docs/plugin-system/design/01_manifest_schema.md` §3.4 + §3.4.1）
- **18 个新单测**（`tests/plugin_system/test_write_plugin_step_field_M3_3.py`）：capability 拒绝 / 命名空间拒绝 3 个 / JSON 不可序列化拒绝 3 个 / step_record 不存在拒绝 / 写入合并语义 4 个 / audit log 3 个 / 错误隔离 / 签名 + capability 字符串锁定 2 个
- **回归状态**：plugin_system 全套 **304/304 通过**（287 + 18 新 − 1 旧 stub 测试），零回归
- 主程序 CSV / 默认导出**不**暴露 `plugin_data`（M3.3 设计目标）；自定义导出模板可显式取 `{step.plugin_data.<key>}`
- M1.3b 剩余项：`broadcast_to_channel_group` / `query_channel_group` / `list_channel_groups` 仍 stub，等 RFC 10 工位组主程序原生落地

## 2026-05-28

### Added

- **新增 `design/09_v3_13_platform_upgrade_rfc.md`（749 行）** — 插件平台 v3.13 升级 RFC，覆盖 M1（业务流程双向打通：6 新 hook 接入点 / Returnable hook 契约 / PluginHost 主动 API）+ M2（UI 扩展平台化：UMD 加载 / 7 个落地 slot / 路由 prefix / tab 注入 / 主题增强）+ M3（配置扩展系统：Project plugin_data 透传 / SystemConfig 命名空间 / StepRecord plugin_data 字段 / 配置面板 helper）+ 工位组 hook 留口；7 个附录（PluginHost.frontend 完整定义 / 里程碑依赖图 / 客户需求 → AC 反向追溯表 / M2 Slot 文件级定位 / 风险表 +4 / 测试金字塔文件级估算）
- **新增 `design/10_channel_group_rfc.md`（410 行）** — 工位组主程序原生 RFC（承接客户需求 4 双工位 NG 联动），引入 `channel_groups` 表 + `ChannelGroupCoordinator` 单线程协调器 + 4 种 settle_strategy（`synchronized_any_ng` / `synchronized_all_ok` / `independent` / `master_slave`）+ 与 cluster 跨机聋齐正交说明 + 与 RFC 09 的耦合关系
- 主仓库 `AGENTS.md` 第三节加"产品决策原则（功能分层归位）"硬约束 + 实操工作流；第四节"必读 skill 触发表"加 `feature-placement` 指针
- 主仓库 `.claude/skills/feature-placement/SKILL.md` — AGENTS.md 产品决策原则的可检索详细版（8 节 + 5 个典型案例 + 决策树 + 反模式自检表）

### Notes

- RFC 09 / 10 状态：草案，待主作者评审 + 客户场景演练后定稿
- RFC 10 实施依赖 RFC 09 的 M1.1（hook 接入点）+ M1.3（PluginHost 主动 API）+ M3.3（StepRecord plugin_data 字段）先落地

### M1.1 末项（source_status_change）+ M1.1 首批 + M1.2a + M1.2b + M1.2c + M1.3a 代码交付（2026-05-28）

- **M1.1 5 个新 hook 接入点落地**：`cycle_start` / `step_change` / `event_fire` / `scan_received` / `project_activated` 已嵌入主程序对应主流程结束点；ctx 字段契约用静态分析锁定（`tests/plugin_system/test_new_hook_points_M1.py`）
- **M1.1 末项 `source_status_change` hook 接入点落地**（原延后项现已交付）：
  - 5 个 lifecycle 公共方法接 hook：`pause` / `resume` / `standby` / `resume_inference` / `stop`（`source_lifecycle_mixin.py`）；`resume` 3 个 + `resume_inference` 2 个 early return False 路径同步配 `resume_failed` / `resume_inference_failed` reason
  - capture_loop 3 处异常中断点接 hook：`capture_loop_video_ended` / `capture_loop_reopen_failed` / `capture_loop_recover_failed`（`source_capture_loop_mixin.py`）
  - 设计选择**最小侵入**：散落 30+ 处 `is_running` / `is_detecting` 写点（init / 各 source_type 启动如海康 / 工业相机 / synthetic）全部不动，仅在客户真正关心的 lifecycle API + capture_loop 断流点接 hook
  - helper `_fire_source_status_change(before_running, before_detecting, reason)` 自带 **dedup**：状态前后 `bool()` 相等时静默 → 防同状态再赋值虚报 / early return False 路径自动 skip
  - 备用 contextmanager `_track_status_change(reason)` 留作未来扩展使用（finally 保证异常路径也 fire）
  - ctx 字段稳定：`{channel_id, before:{is_running,is_detecting}, after:{is_running,is_detecting}, reason, source_type}`
  - 26 个新单测（`tests/plugin_system/test_source_status_change_hook_M1_1.py`）：helper dedup 7 个 + contextmanager 3 个 + 5 个 lifecycle 方法静态扫描 9 个 + capture_loop 2 个 + e2e fake VSM 真消费 3 个 + 签名/ctx 字段锁定 3 个
- **M1.2a Returnable hook 契约（基础设施层）落地**：`fire_plugin_hook` 升级为返回 `Dict[str, Any]`；新增 `_merge_handler_results` + `RETURNABLE_HOOK_FIELDS` 白名单（首批锁 `pre_cycle_end` / `step_change` 两组字段）；17 个新单测 (`tests/plugin_system/test_returnable_hook.py`)
- **M1.2b 业务侧消费 returnable hook 返回值落地**：
  - `end_cycle` 消费 `pre_cycle_end.override_result` → 改写 `cycle.is_good`、`result_reason` 追加 `[plugin override: A → B]` 痕迹；下游 MES `on_cycle_end` / 周期性强制动作 / `cycle_end` hook ctx 全部走 final 值
  - `record_step` 消费 `step_change.warn_threshold_violated` + `warn_label` → 缓存到 VSM 实例 `_plugin_step_warn_cache[step_record_id]` 字典；`start_cycle` 时清空避免跨周期串污染
  - 消费逻辑抽到 `_resolve_pre_cycle_end_overrides` + `_resolve_step_change_warn` 两个纯函数（同 `source_session_lifecycle_mixin.py`），让单测可独立验证消费契约（不需要起完整 `VideoSourceManager`）
  - 31 个新单测（`tests/plugin_system/test_returnable_hook_consumption_M1_2b.py`）：纯函数语义 / end_cycle 静态扫描 / record_step 静态扫描 / start_cycle 清缓存 / e2e fire_plugin_hook → resolve 链路
  - **暂未消费**：`pre_cycle_end.extra_counters`（cycle 表无 plugin_data 字段，等 M3.3）
- **M1.2c `_trigger_event` 重构 + `event_fire.suppress_alarm` 全链路落地**：
  - 尾部重排：`events_log → _pending_ack → event_fire hook → resolve suppress → [alarm?] → router → _last_event_time`（hook 上移到 alarm 之前，suppress_alarm 才来得及作用）
  - 抽 `_resolve_event_fire_suppress_alarm` 模块级纯函数（严格 `is True` 才抑制，防 `1` / `"true"` / `[True]` 等非 bool truthy 被误识为抑制）
  - 抽 `_dispatch_event_alarm` 私有方法封装 alarm 触发（让 M1.2c 顺序逻辑清晰 + 测试可静态扫描）
  - `RETURNABLE_HOOK_FIELDS["event_fire"] = {"suppress_alarm"}` 白名单升级（**改白名单 = 升 plugin SDK 主版本**）
  - **安全侧三大兜底**：alarm 串口抛错 → swallow + anchor 仍更新；handler 抛错 → fire 返 `{}` → 不抑制；无 active 插件 → registry is None → 不抑制
  - **19 个基线测试**（`tests/plugin_system/test_trigger_event_baseline_M1_2c.py`）：在重构前先录"无插件场景行为"，重构后跑 0 diff 守护字节级等价；覆盖 alarm 调用次数 + 入参 + 与 router/anchor 相对顺序 + 抑制路径（settle_dedup / ng_protect / `_pending_ack` / event 未找到 / no project_config）+ alarm 异常隔离 + 返回值契约
  - **27 个业务消费测试**（`tests/plugin_system/test_returnable_hook_consumption_M1_2c.py`）：纯函数 8 个（含 truthy non-bool 严格契约）+ 白名单 2 个 + 静态扫描 5 个 + e2e fire→resolve 5 个 + e2e fake VSM 真消费 5 个 + 签名锁定 2 个
  - 旧 M1.2a 测试同步更新：`test_returnable_hook_fields_whitelist_event_fire`（空 → `{"suppress_alarm"}`）+ `test_merge_event_fire_whitelist_keeps_suppress_alarm_drops_others`
- **M1.3a `PluginHost` 主动 API（4 个 API + 2 个 stub）落地**：
  - 4 个主动 API：`trigger_alarm` / `mes_push` / `read_system_config` / `write_system_config` 已实现，安全模型四层防护（capabilities 声明 / 命名空间隔离 / audit 落库 / 错误隔离）
  - 2 个 stub：`write_plugin_step_field`（依赖 M3.3）+ `broadcast_to_channel_group`（依赖 RFC 10）— 调用即抛 `PluginNotImplementedError` 带清晰里程碑提示
  - 新增 `PluginRuntimeError`（RuntimeError 子类，与 `PluginNotImplementedError` 区分）
  - manifest `capabilities` 词汇表扩展 3 个枚举：`runtime.alarm_trigger` / `runtime.mes_push` / `runtime.system_config_write`（见 `design/01_manifest_schema.md` §3.4.1）
  - 33 个新单测（`tests/plugin_system/test_active_apis.py`），覆盖异常类 / capabilities / 命名空间 / 实际效果 / audit / 错误隔离 / stub / 向后兼容
- **回归状态**：plugin_system 全套 **287/287 通过**（M1.1 首批 134 + M1.2a 17 + M1.3a 33 + M1.2b 31 + M1.2c baseline 19 + M1.2c consumption 27 + M1.1 末项 source_status_change 26），零回归
- **plugin SDK 主版本号建议 +1**：因 `event_fire` 白名单从 `set()` 变为 `{"suppress_alarm"}`，按 RFC 09 §4.3 契约"改白名单 = 升 SDK 主版本"。客户插件如果原本就没用 `event_fire` returnable（包括所有 M1.1/M1.2a 时期开发的插件），实际行为完全等价 — 仅作语义化版本号信号。
- **M1.1 hook 接入点全部 6/6 落地**：原 RFC 09 §4.2 标注 6 个 hook，首批 5 个 + 末项 1 个全部交付，AGENTS.md 的 hook 表无需再标"暂缓"。

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
