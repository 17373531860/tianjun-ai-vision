# 展会插件 v1.4.0 升级说明 — 全量对齐主程序 v3.33 ~ v3.35

> 上一版 v1.3.1 对齐到主程序 v3.31/v3.32。本次把 v3.33（逐件重复打防护/换板兜底）、
> v3.34（断点补做/多轮次两防护/区域事件秒基门槛）、v3.35（称重融合/料源防错/数据库
> 直写/包装复合条码/等待不被打断/外设门控）在插件界面里全部补齐。
> 契约来源：主程序 `Project/index.vue` 保存清洗白名单、`LogicConfigTab.vue` /
> `StepsConfigTab.vue` / `EventsConfigTab.vue` / `WeighingConfigTab.vue` /
> `LabelSplitDialog.vue` + `labelSplit.js`、`GatewayPanel.vue` +
> `database_adapter.py`、`PackagingFlowPanel.vue`、`source_region_events.py`、
> `source_per_item_mixin.py`、ack-event 端点。

## 兼容基线

- `main_version_min` 从 3.31.0 提到 **3.35.0**（步骤外设门控、数据库直写、料源防错
  等字段在旧后端无解析方）。老现场先升主程序再换插件。

## 一、项目页 — 逻辑设置

### 区域事件（v3.32/v3.34 全字段）
- 规则卡按类型条件渲染，补齐主程序全部调优字段：
  - 非出区：确认时长秒基 `min_seconds`（帧率解耦）、位移门槛 `min_move`、
    消失确认秒 `gone_seconds`、辅助约束类别 `require_label`；
  - overlap 专属：区域组合方式 `region_mode`、重叠 IoU 下限 `min_iou`、
    重叠深度下限 `min_overlap_ratio`；
  - region_exit 专属：消失确认帧 `gone_frames`、帧间关联 IoU `match_iou`；
  - 通用：附加触发事件 `event_id`、区域跟随锚点 `anchor`（标签/沿用秒数/标定框）。
- 新增「每类置信度阈值」`class_conf` 与「结算判定」`settlement_rules` 编辑器
  （序列完全匹配 / 缺某动作 / 重复≥N / 兜底，先匹配先赢）。
- 保存清洗 `_tjSanitizeRegionEvents` 对齐主程序：0 值调优字段不落库、半截锚点
  丢弃、无效结算判定行剔除——否则激活时后端解析器抛 ValueError。

### 同标签区域拆分（新块，v3.32 + v3.34 两防护）
- 规则编辑器：原始标签 / 固定画面·锚点跟随 / 区域外落点三策略 / 区域列表 JSON /
  多轮次（切换标签、轮数、前缀、间隔秒 + v3.34 `trigger_min_seconds`、
  `trigger_conf` 两防护 + 每轮独立区域）。
- 保存时 `_tjSyncSplitSteps` 幂等同步虚拟步骤到步骤表（`split_origin` 标记行，
  孤儿清理 + 序列引用摘除），为主程序 `syncSplitVirtualSteps` 的插件版。

### 逐件覆盖（v3.33 两块）
- 「防止重复打同一颗螺丝」开关 + 移开确认帧 / 重压确认帧 / 报警节流秒 /
  提示存在秒四参数（键与默认值对齐主程序保存块）。
- 「换板兜底: 工件整体消失几帧」（手动结算模式下不生效的提示同主程序）。

### 事件配置（v3.34）
- `require_ack` 打开后新增「确认后保留周期（断点补做）」`ack_keep_cycle` 勾选。

### 称重投料（v3.35）
- 融合模式提示：`drive_mode='step_gate'` 时非称重模式也显示称重块。
- 「前置选择有效期」`context_expiry`：永不 / 每日定时 / 班次表 / 滚动小时 +
  过期清空项（型号/人员）。
- 「视觉料源防错」`visual_guard`：识别来源 / 抓错拦截 / 冷却秒 + 料别规则列表
  （料别 ↔ 识别标签 ↔ 确认帧数 ↔ 判定区域）。

## 二、项目页 — 步骤设置（v3.35 两新列）

- 「等待不被打断」`disappear_uninterruptible`：仅消失等待时间 >0 可编辑，
  否则锁定并给原因（对齐主程序表 B disabled 语义）。
- 「外设门控」`device_gate`：顺序类模式（sequential / custom 基于 sequential）
  专属；未启用 / 去皮门控 / 称重判定三态，称重判定露出料别与拦截子项。
  启用任一门控保存时自动注入 `pipeline_config.weighing.drive_mode='step_gate'`
  （子树缺失时给全套默认），与主程序 onGateEnabledChange 同语义。

## 三、监控页

- 逐件重复打黄条：读 `per_item_state.last_warning`，按
  `duplicate_warning_display_sec` 客户端倒计时收起（0=等后端清）；个体格
  `dup>0` 加琥珀环 + 悬停「重复打 N 次」。
- 人工确认成功文案区分 v3.34 断点补做：响应带 `kept_cycle=true` 时提示
  「已确认 · 保留周期，从断点继续补做」。

## 四、MES 页

- 网关新建连接对话框：适配器新增 **database（数据库直写）**；选中后隐藏
  接口地址/请求方法/鉴权类型，露出数据库类型（MySQL/PostgreSQL/SQL Server/
  Oracle/SQLite）+ 主机/端口/账号/密码/库名/表名，`config` 键名对齐
  `database_adapter`（`db_type/host/db_port/user/password/database/table`）。
- 包装结算：配置表新增「复合条码 / 尾箱收尾」列 + 每流「条码/收尾」编辑面板：
  复合条码取段（分隔符 / 按前缀 / 按第 N 段）、工单号识别正则
  `order_code_pattern`、尾箱需放工单 + 放工单步骤标签 + 放工单=收尾动作
  `tail_paper_as_close_action`。走 PUT 部分更新（`exclude_unset`），只发这批字段。

## 五、不影响老行为的说明

- 所有新增均为可配置项且默认关/默认空，不动既有配置的渲染与保存路径。
- 保存清洗只对新增键做 0 值剥离；`min_iou`/`min_overlap_ratio`/
  `gap_tolerance_frames` 等主程序允许落 0 的键保持落 0。
