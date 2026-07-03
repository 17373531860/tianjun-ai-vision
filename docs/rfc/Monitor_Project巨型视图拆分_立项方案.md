# 立项方案：Monitor / Project 巨型视图拆分（逐 panel）

- 状态：**立项待批（批准后按批次单独排期执行，本期不动代码）**
- 日期：2026-07-03
- 关联：第五期治理计划第 5 项；技术债 `05_tech_debt.md` 第九节过大文件表
- 铁律：**碰 `.vue` 强制走 T4（真浏览器）/ T5（落库双向验证）/ T6（CI E2E），每批只拆一个 panel，拆完即验收即提交**

---

## 1. 现状

| 文件 | 行数 | 结构摸底 |
|---|---|---|
| `views/Monitor/index.vue` | 6293 | 多通道视频卡片（双缓冲 MJPEG）× 状态面板 × 控制条，同构块重复多份（如录像失败遮罩出现 4 处）；**已有拆分先例**：`WeighingPanel` / `PerItemPanel` / `PackagingFlowCard` / `ExternalAlarmBanner` / `VirtualScanGun` / `framePump.js` 六件已外置 |
| `views/Project/index.vue` | 5971 | 五个配置 Tab（基础/步骤/称重/逻辑/事件）+ 四个对话框（新建项目/模型选择/推理格式/ROI 编辑器），Tab 之间靠 `activeProject` 单一数据源联动 |

**为什么要拆**：单文件超 6000 行后，任何客户需求都要在同一文件改，diff 审查困难、AI/人定位慢、合并冲突高发（多 agent 并行时最痛）；且 v3.31 称重、v3.8 逐件等新模式都已经证明"panel 外置"模式可行。

## 2. 拆分原则（对齐既有先例，不发明新模式）

1. **子组件放同目录**（`views/Monitor/XxxPanel.vue`、`views/Project/XxxTab.vue`），不建深层目录。
2. **数据流单向**：父传 `props`（通常是 `activeProject` / 通道状态对象），子发 `emit`；**不许**子组件自己另起轮询或直连 store 写状态（Monitor 多通道 state 隔离是 v2.6.0/v3.0.0/v3.1.3 三修的地雷，见 AGENTS 不变量 7）。
3. **只搬不改**：每批次纯移动 template + 相关 script/样式，禁止顺手重构逻辑；行为差异 = 验收不通过。
4. **插件槽位不动**：`<TjSlot>`（如 `monitor.layout.footer`）保持在父级原位，拆分不得改变槽位挂载层级（插件视图覆盖依赖它）。

## 3. 批次清单与顺序（风险从低到高，每批一个独立发版窗口）

### Project/index.vue（先做——纯配置表单，无视频流地雷，5971 → 目标 <1500 行）

| 批次 | 拆出组件 | 大致范围 | 风险 |
|---|---|---|---|
| P-1 ✅（2026-07-03 完成） | `WeighingConfigTab.vue`（称重投料 Tab） | ~1385-1554 行，5 张卡片 | 低：v3.31 新增、自包含、边界清晰，当模板批次。实测 index.vue 5971→5756 行；UAT 10/10 + CI E2E `test_weighing_config_tab.py` 3 用例绿 |
| P-2 ✅（2026-07-03 完成） | 四个对话框各自成组件（`CreateProjectDialog` / `ModelSelectDialog` / `FormatSelectDialog` / `RoiEditorDialog`）+ 格式显示名共用模块 `modelFormats.js` | ~3098 行起 | 低-中：ROI 编辑器带 canvas 交互。实测 index.vue 5756→5468 行；UAT 11/11 + CI E2E `test_project_dialogs.py` 3 用例绿 + Project 页既有 13 用例回归绿。RoiEditorDialog 通过 load(通道, 已有多边形) 由父级驱动, 保存路由（副模型/步骤/全局）留在父级 |
| P-3 ✅（2026-07-03 完成） | `EventsConfigTab.vue`（事件设置 Tab） | 事件 FSM 配置 | 中。实测 index.vue 5468→5355 行；UAT 9/9 + CI E2E `test_events_config_tab.py` 2 用例绿 + Project 页既有 16 用例回归绿。计数器候选 computed 留父级传 props（与基础设置 Tab 共用）；addEventAndBindToRule 属逻辑 Tab 链路留父级 |
| P-4 ✅（2026-07-03 完成） | `LogicConfigTab.vue`（逻辑设置 Tab）+ per_item 标签换算共用模块 `perItemLabel.js` | ~1400 行模板 + 20 个编辑函数 + 4 个步骤候选 computed + ROI 预览画布整体平移 | 中-高：五种模式分支全回归。实测 index.vue 5355→3707 行；UAT 10/10（五模式卡片渲染 + sequential 序列落库 + custom 周期规则/快捷建事件落库 + tracking ROI emit 链路）+ CI E2E `test_logic_config_tab.py` 3 用例绿 + Project 页既有 18 用例回归绿。父级保留三个上下文回调（sequence-step-pick / mix-type-change / open-roi-editor 走 emit）；ROI 预览小画布随卡片进子组件, 内部 deep watch 自动重绘 |
| P-5 ✅（2026-07-03 完成） | `StepsConfigTab.vue`（步骤/物品设置 Tab，含表 A/B/C）+ 共用模块 `mixItemDefaults.js` / `stepEnabled.js` | ~890 行模板 + 三表 computed/编辑函数平移 | 高：表 B 随模式切换、custom_mix 表 C。实测 index.vue 3707→2704 行；UAT 11/11（sequential 三表渲染 + 结算判定/连续重复两条 prop 链 + 步骤ROI emit 拉起外置编辑器 + 禁用步骤序列剔除落库 + 混合模式角色切物品表C出现且逐件参数落库 + tracking 物品列）+ CI E2E `test_steps_config_tab.py` 4 用例绿 + Project 页既有 21 用例回归绿。与父级共享的两条判定链走 props（consecutiveDupStepIds / isSettlementStep 与父级 watch 同源实现留父级）；onStepEnabledChange 抽 `stepEnabled.js`（父级副模型步骤清理 `_purgeStepsByFromModel` 共用）；物品行默认值注入抽 `mixItemDefaults.js`（父级 mix-type-change 回调共用） |

### Monitor/index.vue（后做——视频流+多通道地雷区，6293 → 目标 <2000 行）

| 批次 | 拆出组件 | 说明 | 风险 |
|---|---|---|---|
| M-1 ✅（2026-07-03 完成） | `RecordingFailureOverlay.vue` | 同构遮罩在文件里重复 4 处，合一消重复（唯一允许"消重"的批次，因为是复制粘贴块）。实测 index.vue 6293→6091 行；4 处调用点=插件覆盖布局(elevated 抬 z-index 压列级 Toast)/双工位(每列一份, 保持原语义)/四工位/单工位；时间与原因文案格式化随组件下沉, 清空动作走 clear emit 留父级(API+轮询数据写回)。UAT 9/9（网络拦截往真实轮询响应注入 mes.recording_failures 走前端真实通路: 无异常不出按钮/双通道聚合计数/面板行按通道标注不串台/原因映射/清空 API 每通道各发一次/单工位负验证/console 零错误）+ CI E2E `test_recording_failure_overlay.py` 1 用例 + Monitor 页存量 3 用例回归绿 | 低 |
| M-2 | `SopStepPanel.vue`（步骤/SOP 展示面板） | 与已外置的 PerItemPanel 对等地位 | 中 |
| M-3 | `CustomMixItemPanel.vue`（v3.19 物品校验面板） | ~917 行起 | 中 |
| M-4 | `ChannelVideoCard.vue`（单通道视频卡片=双缓冲 MJPEG+状态角标） | **最后做**：直接踩不变量 7（双缓冲、多通道 state 隔离、framePump），需主作者亲自复核 | 高 |

## 4. 每批次统一验收协议（不可裁剪）

1. T2：lint 0 错误 + `npm run build` 绿。
2. T4：`headless=False` 真浏览器过该 panel 全部交互路径 + 截图（用 `tests/uat/_common.py`）。
3. T5：改一项配置 → GET 接口/查 DB 确认落库；Monitor 批次加"双通道各自独立不串台"检查。
4. T6：补/改 `tests/e2e_browser/` 对应用例跑绿（Project 批次至少覆盖"改配置→保存→重进页面值还在"）。
5. 提交粒度：一批一 commit；`05_tech_debt.md` 第九节行数表同批刷新。

## 5. 工作量与开放问题

- 估算：P-1/P-2/M-1 各 0.5 天；其余各 1 天；合计约 7 天，**分散在多个发版窗口执行，不集中突击**。
- 开放问题（批准时定）：
  - Project 五 Tab 全拆后 `activeProject` 是否下沉为 provide/inject（倾向不做，保持 props 显式传递）；
  - M-4 是否与"Monitor 支持 >4 通道布局"需求合并做（若近期有该需求则合并，避免拆两次）。
