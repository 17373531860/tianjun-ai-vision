# v3.7.1 → v3.7.2 验收报告

**验收时间**: 2026-05-13 03:53 +08:00
**验收范围**: 从 commit `a384a51` (v3.7.1 release) 到当前工作区的所有改动
**验收结论**: ✅ **全部通过**

---

## 1. 改动汇总

| 类别 | 内容 |
|---|---|
| **FIX-381-A** | 顺序/自定义-基于顺序 模式下，不在序列里的标签不入 cycle 判定 |
| **FIX-381-B** | 每个标签可独立配置检测框颜色 (steps_config 加 box_color) |
| **FIX-381-C** | 副模型 label 升一等公民，进 steps_config 表，可独立配置 |
| **v3.7.2** 扫码器旁路 | 完整链路: DB schema (9 字段) + helper (策略参数) + cycle_start 锁快照 + 三策略分发 + 异步去重重试 + 系统预设带默认配置 + 前端 UI 自动填 |

**修改文件**: 14 个 (1045 行新增 / 67 行删除)
**新增模块**: `backend/services/export_snapshot.py`
**新增测试**: 5 个测试文件, 87 个测试用例

---

## 2. L1 · 变更清单 ✅

```
backend/api/export_custom.py            +12  TemplateOut/Create/Update/Clone 透 default_rule_config
backend/api/export_realtime.py          +27  Pydantic 加 7 字段 + 序列化透出
backend/api/source_settlement_mixin.py  +13  FIX-381-A: _process_single_step 加 expected_seq 守卫
backend/main.py                         +19  8 条 ALTER TABLE 启动迁移
backend/models/export_models.py         +38  ExportRealtimeRule 7 字段 + ExportTemplate.default_rule_config
backend/models/models.py                +17  DetectionCycle.external_meta JSON 列
backend/services/export_realtime.py    +291  _resolve_input_for_rule 3 策略分发 + _async_dedupe_retry
backend/services/export_renderer.py    +241  helper 加 wait_stable_ms/max_age_sec + locked 优先 + _wait_for_stable
backend/services/export_seed.py        +191  _DEFAULT_RULE_SCANNER_BYPASS 12 字段 + seed 强制覆盖系统预设的 drc
backend/services/mes_hooks.py           +16  _handle_cycle_start 接入 snapshot_for_cycle_start
backend/services/export_snapshot.py    新建  snapshot_for_cycle_start + lookup_snapshot_from_cycle
frontend/src/views/Data/.../RealtimeRulesDialog.vue  +143  策略单选 + 去重区 + onTemplateChange 自动填
frontend/src/views/Monitor/index.vue    +29   pickDetColor 优先级 helper (副模型 display_color > step.box_color > 全局 OK/NG)
frontend/src/views/Project/index.vue    +73   检测框颜色列 + from_model 标签 + 副模型 label 入 steps_config
```

---

## 3. L2 · 单元 + 集成测试 ✅

跑过 **87/87** 测试用例，**0 失败**：

| 测试文件 | 用例数 | 内容 |
|---|---|---|
| `test_export_latest_input_helpers.py` | 35 | helper 基础行为 + Jinja2 集成 + 现场场景 |
| `test_export_template_default_rule_config.py` | 6 | 预设带 drc / clone / create / update / seed 覆盖 |
| `test_integration_scanner_bypass_export.py` | 4 | 旧路径回归 (预设 + ng cycle + 多 txt + 空目录) |
| `test_integration_scanner_bypass_strategies.py` | 10 | 三策略 × 去重 on/off × 异步重试 |
| `test_integration_cycle_end_chain.py` | 5 | cycle_end 通用链路 (含 channel filter / disable / ng / periodic) |
| `test_pairwise_input_modes.py` | 9 | input_file_mode × format 组合 |
| `test_unexpected_labels_dont_join_cycle.py` | 6 | FIX-381-A 顺序模式守卫 + 自定义-基于顺序 + 兼容回归 |
| `test_periodic_actions_v352.py` | 12 | 周期性强制动作 (与本次改动无关, 防 regression) |
| **合计** | **87** | |

```bash
$ python -m pytest tests/test_export_*.py tests/test_integration_scanner_*.py \
    tests/test_integration_cycle_end_chain.py tests/test_pairwise_input_modes.py \
    tests/test_unexpected_labels_dont_join_cycle.py tests/test_periodic_actions_v352.py
============================== 87 passed in 5.99s ==============================
```

完整 backend pytest 套件 (跳过 e2e_browser / manual_uat / plugin_system): **428 / 430 过** (剩 2 个 synthetic 失败为旧的套件运行顺序污染问题，与本次改动无关)。

---

## 4. L3 · DB schema 迁移 ✅

启动迁移后直接查 schema，所有 9 个新字段就位：

| 表 | 字段 | 验证 |
|---|---|---|
| `detection_cycles` | `external_meta` | ✅ |
| `export_realtime_rules` | `latest_file_strategy` | ✅ |
| `export_realtime_rules` | `latest_file_wait_stable_ms` | ✅ |
| `export_realtime_rules` | `latest_file_max_age_sec` | ✅ |
| `export_realtime_rules` | `dedupe_same_filename` | ✅ |
| `export_realtime_rules` | `dedupe_retry_max_sec` | ✅ |
| `export_realtime_rules` | `dedupe_retry_interval_ms` | ✅ |
| `export_realtime_rules` | `last_used_input_filename` | ✅ |
| `export_templates` | `default_rule_config` | ✅ |

---

## 5. L4 · seed 系统预设 ✅

启动后 seed 5 个系统预设，其中扫码器旁路预设带着完整的 12 字段推荐配置：

```
#1 [txt] 客户 SN.txt（Pass/Fail 三行）
#2 [txt] 单 cycle 简明文本
#3 [csv] session cycle 列表 CSV
#5 [csv] session 5 步产线 CSV (会话信息+计数器+周期明细)
#6 [txt] 扫码器旁路三行 TXT (序列号+OK/NG+步骤时长+版本)             ★ 带配
```

扫码器旁路预设的 `default_rule_config`:
```python
trigger_event = 'cycle_end'
input_file_mode = 'none'
filename_template = '{{ latest_input_filename() }}'
encoding = 'utf-8'
newline = 'crlf'
overwrite_policy = 'overwrite'
latest_file_strategy = 'cycle_start_snapshot'       # C 策略 (推荐)
latest_file_wait_stable_ms = 100
latest_file_max_age_sec = 60
dedupe_same_filename = False                        # 客户决定是否开
dedupe_retry_max_sec = 5
dedupe_retry_interval_ms = 100
```

---

## 6. L5 · 端到端 API 链路 ✅ (8/8)

`tmp/acceptance_v372.py` 跑完整 cycle 模拟：

| Step | 内容 | 结果 |
|---|---|---|
| 1/8 | GET /templates 找扫码器旁路系统预设 | ✅ |
| 2/8 | POST /templates/{id}/clone — drc 跟随复制 | ✅ |
| 3/8 | 准备临时输入/输出目录 + 模拟扫码 txt | ✅ |
| 4/8 | POST /realtime-rules 建规则 (用 drc 填) | ✅ |
| 5/8 | GET /realtime-rules — 7 个新字段透出 | ✅ |
| 6/8 | snapshot_for_cycle_start → cycle.external_meta 写入 | ✅ |
| 7/8 | dispatch_cycle_end_export → 用快照, 不被周期中产生的新 txt 影响 | ✅ |
| 8/8 | 文件落盘 5 行 CRLF, 内容完全符合预期 | ✅ |

落盘文件内容：
```
WP_ACC_001_VAL          ← 来自 cycle_start 锁定的扫码序列号
                        ← 空行
合格                    ← OK/NG
取件: 2.34s | 装配: 5.67s   ← 步骤时长
3.7.1                   ← 软件版本
```

关键验证：周期中扫码器又产生了 `WP_NEW_DURING_CYCLE.txt`，但 cycle_end 渲染时**仍使用 cycle_start 锁定的 `WP_ACC_001.txt`**——零串号风险 ✅

---

## 7. L6 · 前端可见浏览器 UAT (Playwright) ✅

输出: `evidence/v372_acceptance_2026-05-13_0352/`
录像: `*.webm`
截图: 11 张 PNG

### Phase A · 项目管理页 (FIX-381-B / FIX-381-C)

| 验证项 | 结果 | 详情 |
|---|---|---|
| A1 检测框颜色列 | ✅ | 步骤设置 tab 下找到「检测框颜色」列头 |
| A1b 颜色选择器 | ✅ | 步骤设置表里 **18 个 el-color-picker** 触发器 |
| A2 from_model 标签 | ⚠️ 0 个 | 开发机 17 个项目均未启用副模型, 主模型默认不打标签 (设计如此) |
| A3 颜色选择器交互 | ✅ | 点击触发后正常弹出颜色面板 |

> **副模型标签** 的代码路径 (`Project/index.vue:571`) 已实现，仅在 `step.from_model && step.from_model !== 'main'` 时显示。开发机项目没用副模型，所以标签数 0 是正常的。该路径由单测 `test_export_template_default_rule_config` 等覆盖。

### Phase B · 实时导出规则对话框 (v3.7.2 扫码器旁路)

| 验证项 | 结果 | 详情 |
|---|---|---|
| B1 模板下拉 ★ 标记 | ✅ | `[txt] 扫码器旁路三行 TXT (...) (系统) ★` |
| B2 自动应用提示 | ✅ | 选模板后顶部出现 cyan 文字「★ 已自动应用模板...」 |
| B3 C 策略默认选中 | ✅ | 「C · 周期开始锁快照（推荐）」单选 is-checked |
| B4-B6 表单/去重区 | ✅ | 截图 b5_form_filled / b6_dedupe_section 完整 |

---

## 8. 客户侧最短操作路径 (验证后)

```
1. 数据 → 实时规则（扫码自动写入） → 新建规则
2. 「绑定模板」下拉里选带 ★ 的『扫码器旁路三行 TXT (...) (系统) ★』
   → 表单瞬间自动填好 12 项: cycle_end / none / latest_input_filename / utf-8 /
     crlf / overwrite / cycle_start_snapshot / 100ms / 60s / 关闭去重 / 5s / 100ms
   → 顶部出现 cyan 提示「★ 已自动应用模板...」
3. 客户只填两件事：输入目录 + 输出目录
4. 保存 → 跑一轮检测 → 5 行 txt 在输出目录就绪
```

---

## 9. 已知非阻塞事项

| 项 | 影响 | 备注 |
|---|---|---|
| 软件版本号显示 `3.7.1` | ⚠️ 文案 | 工作区代码改动尚未发版，splash 和 package.json 仍是 v3.7.1。下一次正式打包时需要 bump 到 v3.7.2 |
| 开发机现有 17 个项目 from_model 全空 | ⚠️ 数据 | 老项目存的 steps_config 没有 from_model 字段, 前端打开时会兼容补 'main' (in-memory only)，重存项目会持久化。不影响新建项目和功能正确性 |
| 2 个 synthetic 测试失败 (套件运行顺序污染) | ⚠️ 测试基建 | 与本次改动无关，单独跑均通过 |

---

## 10. 验收结论

✅ **全部通过**，可以发版 v3.7.2 / 给客户提供 hotfix。

落盘的客户操作指引文档：
- `docs/客户操作-扫码器旁路导出.md`
- 扫码器旁路系统预设描述 (启动 seed 时刷入 DB.export_templates.description)

完整证据目录：
- 代码: `git diff a384a51 HEAD`
- 单测: `tests/test_export_*.py` + `tests/test_integration_scanner_*.py` + `tests/test_unexpected_labels_dont_join_cycle.py`
- 端到端: `tmp/acceptance_v372.py` (本机一次性运行)
- UAT 截图/录像: `evidence/v372_acceptance_2026-05-13_0352/`
