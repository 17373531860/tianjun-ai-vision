# UPGRADE v1.5.0 — 对齐主程序 v3.36 ~ v3.55

> 上一版 v1.4.1 对齐到 v3.35；本版把 20 个主程序版本 (v3.36~v3.55) 中**属于插件
> 8 页覆盖面**的功能补齐。契约全部按主程序当前代码核对 (2026-09-01)，逐条列出
> 键名 / 端点 / 默认值来源，防止口口相传漂移。
>
> `main_version_min` 提升到 **3.55.0**（依赖 v3.50 `/scanner/resume`、v3.48
> `combo_verdict` 载荷、v3.44 `pending_ack.pkg_hold` 等新契约；装老主程序请继续用 v1.4.1）。

## 一、保存安全（本版最重要的改动）

| 项 | 改动 | 契约依据 |
|---|---|---|
| `pipeline_config.per_item` | 保存从**白名单重建**改为 `Object.assign({}, s, {…清洗覆盖})` 展开式——主程序未来给 per_item 加键，插件保存不再丢 | 主程序 `Project/index.vue` 序列化同思路 |
| 非 per_item 模式 | 原来保存写 `per_item:{}` 会抹掉库里已有子树，改为原样透传 `pc.per_item` | 数据无损原则 |
| legacy NG 键 | 保存时 `delete` `instant_ng_on_violation` / `strict_order_violation_event_id` / `ng_remediation` / `closing_guard`，只落 `ng_handling`（对齐主程序「读兼容、写只落新块」） | `backend/api/source_project_config_apply.py::resolve_ng_handling` |

## 二、项目页（配置面）

### 1. NG 判定与处置统一模型（v3.44，新卡 `data-lg="ngh"`）
- 键：`pipeline_config.ng_handling = {violation, violation_event_id, missing_step,
  hold_timeout_s, hold_event_id, short_count, gate_enabled, gate_steps,
  gate_event_id, gate_escalate_steps}`。
- 枚举：`violation: none|hint|instant_ng`；`missing_step/short_count: ng|ack|hold`。
- 读兼容：老项目无 `ng_handling` 时在 `_peNormalize` 从 legacy 键合成（逻辑照抄主程序
  `index.vue` L1361~1379）。
- 保存净化：`last_first` 结算模式强制 `violation='none'`；`gate_escalate_steps`
  与 `gate_steps` 取交集；`gate_steps` 为**标签字符串**数组。

### 2. 跟踪齐件即结算（v3.50）
- `pipeline_config.tracking_settle_on_complete`（bool，默认关），仅
  `tracking_cycle_strategy ∈ {roi_exit, container}` 时露出 + 落 true（后端同规则守门）。
- 步骤表（tracking 表）新增「确认放入帧 (v3.50)」列 → `steps_config[].settle_confirm_frames`
  （空=后端默认 1；配了 ≥1 取整）。
- UI 注明与 scan_pair 互斥（后端拒绝同开）。

### 3. 混合跟踪 · 装箱清点（v3.19~v3.46，新卡 `data-lg="mixbox"`，custom 模式）
- `pipeline_config.custom_mixed_with: ''|tracking|per_item`。
- 物品行标记：`steps_config[].detect_role='item'`（+ tracking 混合时 `expected_count`）
  ——对齐 `backend/api/source_custom_mix.py::build_custom_mix`。
- 容器记账平铺键（全部 `custom_mix_container_*`）：`label / count_mode(trays|items) /
  gone_frames(30) / iou_match(0.3) / box_count(0) / item_target(0)`；
- 进箱确认（v3.30）：`confirm_by_frames(true) / confirm_by_action(false) / action_label /
  confirm_combine(or|and) / action_min_frames(空=随步骤) / action_gone_frames(空=随步骤) /
  action_cooldown_s(空=2.0, 0=关)`；
- 记账精度与去重（v3.44~v3.46 上银整改）：`peak_cap(0) / stable_min_frames(0) /
  dedup_items(true) / dedup_trays(false) / purge_empty_primary(false) /
  yield_primary(false) / unified_book_source(false) / per_tray_guard(false) /
  item_dedup_iou(0.45) / tray_dedup_iou(0)`；
- 槽位完整性门（v3.46）：`slot_check_label('') / slot_total(0)`。

### 4. 计数组合判定表（v3.48，新卡 `data-lg="combo"`，detection 模式）
- `pipeline_config.combo_table = {enabled, labels[], rows[{counts[], verdict:OK|NG, tag}],
  count_mode: steps|positional}`；行 counts 与 labels 等长非负整数（保存净化对齐
  `_parse_combo_table`）；未命中一律 NG。
- v3.49 二期键（step_guard / live_display / plc_display / tracking_per_label /
  行级 plc_code）**不做 UI**（展会无 PLC），保存用展开式原样保留。

### 5. 自定义班次列表（v3.35.1，基本设置「班次拆分」卡内）
- `data_config.shifts = [{name, start:"HH:MM"}]`，每班只填开始时刻（持续到下一班，
  跨天衔接）；≥2 条生效，否则后端回退两班制。保存过滤无名/无时刻行。
- 未配过时从 day/night 两班字段生成初始两条（对齐主程序初始化）。

### 6. 称重扩展（v3.39 / v3.45）
- 驱动模式三选一：`weighing.drive_mode: scale(默认)|step_gate|pipeline`（此前只有
  step_gate 提示，无法选 pipeline）。
- pipeline 参数卡：`weighing.pipeline.{material, label_onscale, label_fill,
  label_finalize, tare_min_kg(0.2), tare_max_kg(10.0), queue_depth(2)}`；
  时序卡：`weighing.timing.{tare_trigger_source(weight_first), tare_stable_ms(1000),
  net_stable_ms(1500), shortage_alarm_sec(3.0), depart_confirm_ms(500),
  fill_timeout_sec(300), finalize_timeout_sec(120), label3_cooldown_sec(2.0)}`
  （其余细参走后端默认，展开式保存不丢）。
- `weighing.show_monitor_weights`（v3.35.1，step_gate 时露出，默认开=`!==false`）。
- `weighing.operator_from_users`（v3.45，默认关）。

### 7. 逐件补漏
- `per_item.judge_on_workpiece_leave`（v3.28 离场快照判定）此前无 UI 入口，补开关。

## 三、监控页（运行面）

| 功能 | 契约 | 实现点 |
|---|---|---|
| 人工确认弹窗升级 | `pending_ack.{reason, keeps_cycle, pkg_hold}`（v3.43.1/v3.44，`source_routes.py` L1900~1916） | `renderAck`：原因用固化 reason；无挂起时明示「保留周期续做/整件重做」；`pkg_hold` 非空 → 「认NG落账(进下一箱) / 确认重做(本箱不记NG)」双键 |
| 恢复扫码 | `det.mes.scanner_resume_blocked` → `POST /scanner/resume?channel_id=0`，响应 `{resumed:[]}`（v3.50） | 新浮层 `#tjScanResume`，红色呼吸提示 + 一键恢复 |
| 扫码拒绝统一警告 | `det.mes.scan_event.{scan_warning, warn_reason}`（v3.50；老键 `scan_pair_dup_warning` 兼容） | `tjscScanToast` 升级 |
| 称重皮重/净重口径 | station snapshot `tare_weight`（v3.35.1：去皮后秤归零，`live_weight` 即净重） | 看板副行改「实时读数 · 皮重」 |
| 流水线看板 | pipeline snapshot `{pipeline_phase, effective_weight, offscale, pending[{sn,net,verdict}], settled_count}`（v3.39） | `#wgPipeBoard`：相位 + 已拿下装料中 + 待收尾 FIFO 队列 chips |
| 融合称重数值条 | `weighing.drive_mode=step_gate && show_monitor_weights!==false` 时轮询 `/weighing/state`（v3.35.1） | 新浮条 `#tjWgLiveBar`（底部居中，实时读数+皮重） |
| 包装卡新状态 | run.status 增 `waiting_label`（v3.45 箱标签扫码授权）/ `awaiting_paper`（v3.43 放工单=工单收尾） | `#pkState` 横幅两文案 |
| 判型 chip | `det.combo_verdict.{enabled, last_tag, positional_counts}`（v3.48） | 新浮层 `#tjComboTag` |
| 称重相位补全 | `empty / departing`（pipeline 新相位） | `renderWeighing` phaseMap |

## 四、明确不做（范围裁决）

- **PLC / 触发中心 / 训练平台互连 / 录像归档 / 多显示器 / 自定义布局**：
  不在插件 8 页覆盖面或属主程序 Electron/新页面能力，展会场景无硬件依赖。
- **combo_table v3.49 二期 UI**（切步数量门/PLC 缸型码/大字卡）：需 PLC 在场，
  只保证保存不丢键。
- **逐件 Modbus 完成脉冲**（v3.48）：配置在 MES→外部设备页（插件 MES 页未做外设
  编辑器），不属项目配置。
- **tracking「已结算等离开」状态角标**：后端未透出到 results 载荷（纯内部状态机），
  无法渲染。

## 五、操作失败回传后端日志（本版新增排障通道）

此前只有**插件加载**日志回传（v3.15.4 通道），插件内**操作报错**（上传失败/保存失败
的 toast）在现场是黑盒。本版在 iframe 桥的失败路径统一挂 `_tjReportErr`：任何
API 调用失败（含 8s 桥超时）自动 POST `/plugins/client-log`，后端打出

```
[ClientLog][showcase-iframe] API失败 POST /xxx HTTP 400 :: <后端 detail 业务原因>
```

现场"点了没反应/保存失败"直接翻后端日志即得根因。上限 40 条防刷屏、不记自身防递归。

## 六、验证

- 两个 `<script>` 块 node vm 语法校验通过。
- 假桥接结构化 UAT（`uat_v150.py`）：35/35 断言通过（项目页 5 形态 + 监控页浮层）。
- **真实环境鼠标模拟 E2E**（`uat_real_e2e.py`，真后端 8004 + vite 6001 + 插件激活，
  Playwright 可见浏览器真点）：11/11 通过 —— 上传 76MB .pt 模型入库(201)、新建项目(201)、
  改配置保存落库、选择模型对话框绑定(default_model_id+按 labels 重建 9 步骤)、步骤阈值
  落库、显示设置落库、报警配置保存(200)、事件页保存(200)。
- **v1.4.1 对照实验**（用户在用的旧包装进同一环境）：基础写通路同样能通，确认用户
  "很多功能无法实现"的主因是旧包 **UI 停留在 v3.35 功能面**（v3.36~v3.55 新配置项
  无入口）+ per_item 保存丢键，而非桥接坏死；升级本版即解。

## 七、打包与签名

本仓无签名私钥（隔离机专用），本次产出：

```bash
# 1. 作者打未签名包
python scripts/plugin/pack-plugin.py --src plugins-examples/tianjun-showcase --out dist-plugins
# 2. 主作者隔离机签名
python scripts/plugin/sign-plugin.py --in dist-plugins/天军_展会_全应用定制界面-1.5.0-showcase-uns.tjvplugin \
  --out dist-plugins/signed/ --key <私钥> --secret <PLUGIN_SECRET> --signed-by tianjun-ai-master
```
