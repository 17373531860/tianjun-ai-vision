# 展会全应用定制界面 v1.3.0 升级记录 — 对齐主程序 v3.31.0 / v3.32

> 基线：插件 v1.2.2（对齐主程序 v3.19.0 时代的功能面）。
> 目标：对齐当前 main 工作区（v3.31.0 发版 + v3.32 未发版的区域事件/同标签拆分）。
> 本文档是**实施后的落地记录**（改了什么、接了哪些后端契约、怎么验证的），
> 不是计划书。逐条与代码对得上，行号漂移以代码为准。

---

## 一、版本与兼容

| 项 | 值 |
|---|---|
| 插件版本 | 1.2.2 → **1.3.0** |
| main_version_min | 3.13.0 → **3.31.0**（称重/入站/包装 API 需要） |
| 兼容策略 | 所有新接口调用均 try/catch 降级：后端缺该路由时显示「接口不可用/版本过旧」空状态，不炸页面 |
| 打包产物 | `dist-plugins/天军_展会_全应用定制界面-1.3.0-showcase-uns.tjvplugin`（未签名，需主作者 sign） |

## 二、监控页（showcase-app.html 主界面）

### 2.1 逻辑模式从 5 种扩到 7 种

布局由检测帧真值直驱（`__tjscApplyLayout`，不借道下拉框）：

| 模式 | 布局表现 |
|---|---|
| sequential / detection / custom | SOP 流程时间轴（原有） |
| tracking（含 container 变体） | 清点布局（原有） |
| per_item | 逐件覆盖布局（原有）+ **新增漏打挂起待补横幅** |
| **weighing（新）** | SOP 区替换为**称重投料看板**：相位徽标（待机/去皮/投料中/完成/报警）、实时毛重/净重大数字、物料轨道逐料进度、去皮/置零/复位按钮、型号下拉+条码输入；右上反馈块换「投料实时反馈」 |
| **region_events（新）** | SOP 卡片按区域动作规则渲染 + **轮次胶囊**（同标签拆分 `label_split_rounds`）+ **周期动作胶囊**（`periodic_actions` 计数/时间双口径） |

数据源：`/weighing/state`（500ms，仅 weighing 模式轮询）与 `GET /source/detection/results` 数据泵。

### 2.2 iframe 内自绘覆盖层（宿主层 Z-index 够不到 iframe，全部内置）

| 覆盖层 | 元素 | 后端契约 |
|---|---|---|
| 人工确认三态层 | `#tjAckLayer` | `pending_ack`（普通/升级/超时三态）→ `POST /source/detection/ack-event` / `ack-event-elevated` |
| 在途报警横幅 | `#tjExtAlarm` | `GET /mes/inbound/config`（横幅开关）+ `GET /mes/inbound/active-alarms` 轮询 |
| 四要素信息条 | `#tjTaskInfo` | 检测帧 `mes.order.extra_data.inbound`（工单/规格/批次/操作人上屏） |
| 包装结算卡 | `#tjPackCard` | `GET /packaging-flows` + `{id}/state`（箱进度/待补做）+ `force-settle` / `supplement-sliders` / `remediation-redo` |
| 虚拟扫码枪 | `#tjScanPill` + `#tjScanBox` | 按当前模式分流：weighing→`/weighing/scan`；包装流启用→`/packaging-flows/scan`；否则 `/scanner/simulate` |
| 录像异常入口 | `#tjRecFailBadge` + `#tjRecFailPanel` | 检测帧 `mes.recording_failures[]`（recorder_type/reason/error） |
| 逐件待补横幅 | `#piRemedBar` | `per_item_state.pending_remediation` → `POST /source/detection/per-item-control?action=confirm_ng`（人工确认落账） |
| 误检重绑提示 | toast | 检测帧 `mes.rebind_prompt`（workpiece_id/cycle_id） |
| 就位引导 | 画布叠加 | `pipeline_config.placement_guide.polygon` 画多边形 + 快照 `in_position/anchor_visible` 着色 |

### 2.3 UI 风格

全部新元素沿用赛博 HUD 语言：深海军蓝底、青色辉光边、霓虹进度、角标括弧、`tabular-nums` 数字。称重相位色：待机灰 / 去皮黄 / 投料青 / 完成绿 / 报警红。

## 三、项目管理页

- 模式胶囊 5 → 7：新增「称重投料」「区域事件」（`data-logic-mode` 驱动，`applyLogicVisibility` 通用机制直接生效）。
- 新增配置块 `data-lg="weighing"`（对齐 `weighing_engine` 默认配置：去皮方式/触发重量/稳定阈值/投料下限/物料防错/料别顺序/型号标准量表 JSON）与 `data-lg="regionEvents"`（对齐 `source_region_events` 解析器：规则数组 overlap/region_enter/region_exit + 归一化多边形 JSON + 顺序校验 + 去重/容忍帧）。
- 表单绑定新增 `data-t="strlist"`（逗号分隔→数组）与 `data-t="json"`（textarea→对象，解析失败 toast 且不写脏值）；动作 `reAdd`/`reDel` 管理区域规则。
- 保存载荷 `tjBuildProjPayload` 以 `Object.assign({}, pc, {...})` 展开——`pc.weighing` / `pc.region_events` 未列入白名单也随展开原样透传，不丢配置。

## 四、MES 管理页（7 Tab → 9 Tab）

- **生产管控入站**（`data-mes-panel="inbound"`）：接收配置只读镜像（路径前缀/鉴权/规格切换/横幅/四要素/完工回推）+ 当前生产任务四要素 + 在途报警台账，接 `GET /mes/inbound/config` + `active-alarms`。
- **包装结算**（`data-mes-panel="packaging"`）：结算流配置表 + 运行状态（箱号/进度/待补做/强制结算按钮，带确认与审计语义提示），接 `GET /packaging-flows` + `{id}/state` + `POST force-settle`。自动预选只挑启用中的流（停用流不在协调器内存，查状态必 404——UAT 抓到后修复）。

## 五、数据中心页

- 第三栏新增「称重台账」Tab：`GET /weighing/records`（字段 material/standard/initial/net/verdict/ts/sn/model/operator），判定徽标合格/缺料/超量，倒序展示 + 手动刷新。

## 六、系统设置页

- 性能设置 Tab 新增「面板轮询与日志条数」卡片（v3.29 去硬编码）：8 个轮询间隔 + 5 个日志条数输入，`GET/PUT /system/polling` 与 `/system/log-limits`，保存后回读刷新。

## 七、全局（index.esm.js 宿主桥）

- **扫码键盘流转发**：全屏 iframe 抢焦点后 USB 扫码枪键流打不到宿主 `useScanGun`。iframe postMessage `scan-key`（物理枪逐键）/`scan-keys`（虚拟枪整串+回车）→ 父层在宿主 window 重放合成 KeyboardEvent，宿主按原速度特征识别。
- 数据泵维持 400ms 检测帧 + 1.5s 慢泵不变，新增消费字段全部走检测帧（无新增轮询通道，除 weighing 模式的 `/weighing/state`）。

## 八、与主程序联调验证（2026-07-07，全绿）

环境：main 栈，后端 8001（tianjun conda env）+ 前端 6001，主程序版本 3.31.0。

1. **OpenAPI 连通性对比**：插件全部 24 个调用端点逐一比对 `/api/v1/openapi.json`，24/24 存在（source/detection 4、weighing 8、mes/inbound 2、packaging-flows 4、scanner/simulate、system/polling+log-limits 4、plugins/active/manifest）。
2. **GET 实探**：9 个读端点全部 200。
3. **可见浏览器 UAT**：`tests/uat/uat_20260707_showcase_v130_upgrade.py`，**12/12 通过**，证据（截图×7 + 视频 + run.json）在 `tests/uat/evidence_20260707_showcase_v130_upgrade/`。覆盖：iframe 加载、监控页 10 项新骨架、项目页 7 胶囊+2 配置块、MES 9 Tab 接真、称重台账真数据（PERSIST-001 历史记录）、轮询设置改→存→后端回读 12345 成功、控制台零前端逻辑报错。
   - 脚本要点：该 iframe 跨源（6001→8001）是 OOPIF，`page.frames` 在 headed 模式下会漏，必须走 iframe 元素句柄 `content_frame()` 拿帧。

## 八点五、2026-07-07 第二轮修正（用户验收反馈）

**反馈 1：区域事件模式的时间轴变成了"图片墙"。**
根因：`renderRegionEvents` 把主程序 Monitor「SOP 流程卡片」的步骤截图（`step_screenshots`）塞进了插件时间轴卡片，卡片被撑宽成图片卡，丢了插件自己的时间轴风格。定位与主程序的差异是**有意的**——插件是时间轴（序号/名称/状态 + 连接段），主程序才是截图卡。
修复：时间轴卡片去掉截图 `<img>` 与对应 CSS，保留 ×N 触发计数小角标与进行中走表；`tjscSop` 与 `renderRegionEvents` 补标题互切守门（区域模式=「区域动作时间轴」，其余=「SOP步骤时间轴」，切项目不再串标题）。

**反馈 2：OK/NG/自定义提示框是否可配置、样式是否与插件一致。** 排查发现原实现只有一个硬编码的中央 OK/NG 大牌，不吃任何配置。本轮全面对齐主程序 `detection_config` 契约：

- **监控页提示框引擎重写**（`tjPopToast`）：事件帧 `recent_events[]` 按 `show_notification` 守门 + `toast_id` 路由（含 `custom_*` 自定义提示框），样式字段（颜色/主副文字/字号/时长/位置五档）全部读激活项目 `detection_config.toasts` + `customToasts`，外观保持插件 HUD 风格（深底 + 事件色描边辉光 + 图标 ✓/✕/⚠）。NG 副标题吃 `showNgReason` + 事件 reason；`voiceEnabled/voiceVolume` 时语音播报（合格/不合格+原因/自定义主文字），同文案 1.5s 去重。扫码成功（`mes.scan_event`，含重复扫码软警告）与未绑码警告也走同一引擎。
- **检测框画框吃用户配置**（`tjscBoxes`）：正常/NG 框色、线宽、标签字号、显示置信度全部来自 `detection_config`；取色优先级对齐主程序 `pickDetColor`（步骤级 `box_color` > 副模型 `display_color` > `is_ng`）；步骤 `hide_in_view` 不画框。
- **设置页·检测框设置 Tab 接真**：检测框外观（6 字段 + 语音 2 字段）+ 系统预设提示框 4 块 + 自定义提示框列表（新建/删除/编辑）全部可读可改；「保存」合并写回激活项目 + 所有在用工位绑定项目的 `detection_config`（对齐主程序 `saveDetectionSettings` 多工位同步语义），其余键（boxColor 之外未动字段）原样保留。
- **项目页事件配置**：提示框下拉从写死的 ok/ng 扩为「系统预设 + 本项目自定义提示框」，与主程序 EventsConfigTab 一致。

验证：`tests/uat/uat_20260707_showcase_toast_timeline.py`，**15/15 通过**（证据 `tests/uat/evidence_20260707_showcase_toast_timeline/`）。真实链路：TP 项目真视频真模型跑周期 → 时间轴 3 卡无 `<img>`、周期结算弹出「✓ 合格」toast（位置/文字与项目配置一致）、设置页改 NG 色 #123456 + 线宽 5 保存后后端回读成功且自定义提示框不丢、事件下拉含 3 个 `custom_*` 选项、控制台零报错。

## 八点六、2026-07-07 第三轮修正（用户验收反馈）

**反馈 3：左侧导航栏应该像主程序一样默认收起。** 主程序的导航是抽屉式：默认完全隐藏，顶栏 ☰ 按钮呼出，点菜单项或遮罩自动收回。插件原来是常驻 150px 侧栏。本轮改为同款抽屉：默认收起（主区独占整宽），顶栏新增 ☰ 按钮，导航滑出带半透明遮罩，点菜单项跳页/点遮罩都会自动收回。

**反馈 4：虚拟扫码枪不该无条件冒出来。** 主程序 Monitor 的虚拟扫码枪只在「存在启用中的包装结算配置」时渲染（无包装客户零差异）。插件原来的入口胶囊是常驻的。本轮加同款门控：默认隐藏，探测包装结算流列表（30s 周期跟进启停变化），仅"有启用中的流 + 当前在监控页"才显示。

验证：`tests/uat/uat_20260707_showcase_nav_drawer.py`，**9/9 通过**（证据 `tests/uat/evidence_20260707_showcase_nav_drawer/`）：导航默认滑出屏外、主区左缘=0、☰ 呼出+遮罩、点菜单项跳数据中心后自动收回、点遮罩收回、当前环境唯一包装流为停用态时扫码枪不可见、控制台零报错。

## 八点七、2026-07-07 第四轮修正（用户验收反馈）

**反馈 5："画面跑哪去了" — 监控页黑屏只剩 "Ch0 - No Source" 占位。**
根因：视频源被停掉后（停止按钮 / 自动化脚本清理都会走到这个状态），插件「开始」按钮只做了"推项目配置 + 加载模型 + 启动检测线程"，**从不检查视频源在不在跑**——检测线程对着空帧空转，后端推流只能出 "No Source" 占位黑屏。主程序 Monitor 的开始按钮有完整的暂停/待机恢复路径（先把画面拉回来再谈检测），插件缺了这一段。

修复（`tjDetStart` 加视频源守门，对齐主程序 `startDetection` 的恢复语义）：

- **源没在跑** → 先调恢复接口（`/source/detection/resume`）让后端重启记忆中的视频源，画面和检测一起回来；后端也恢复不了（从没配过源）就明确提示去「输入源设置」，**绝不再"无画面空跑检测"**。
- **源在跑 + 模型已载 + 单模型项目** → 走待机快速恢复（`resume-inference`），不重复加载模型；带副模型的项目仍走完整启动路径（与主程序 v3.7.x 副模型修复的语义一致）。
- 项目配置推送（set-project）保持在所有启动/恢复路径之前，与主程序 `syncProjectConfig` 先行的顺序一致。

验证：`tests/uat/uat_20260707_showcase_video_restore.py`，**7/7 通过**（证据 `tests/uat/evidence_20260707_showcase_video_restore/`）：先把源停掉复现用户场景 → 插件页点「开始」→ 后端源+检测双恢复、连抓两张快照像素不同（证明是真视频帧不是静态占位）、再验待机→开始的快速恢复推理路径。

## 八点八、2026-07-07 第五轮修正（用户验收反馈）

**反馈 6：视频文件源没有进度条/倍速等播放控制。** 主程序 Monitor 的视频区对视频文件源有一条悬停浮现的控制条（进度拖拽 + 当前/总时长 + 0.5x~8x 倍速 + 逐帧检测开关），插件完全没有。本轮补齐同款（HUD 风格悬停控制条）：

- 数据契约与主程序一致：视频信息接口 1s 轮询（进度/当前时间/总时长/倍速/逐帧/播完），进度与倍速写回走同一组接口，拖进度后强制重连 MJPEG 流立即看到新位置；
- 禁用语义对齐主程序：检测中进度与倍速禁用、逐帧模式下倍速禁用、运行中逐帧开关禁用；"视频播放完毕/逐帧检测模式"提示文案同款；
- 仅视频文件源显示，摄像头/RTSP/海康源零差异。

**反馈 7：打开检测中心永远是那张静态示例照片。** 原实现只在"检测中"才接 MJPEG 流，其余时间裸露打包在插件里的示例照片 + 假检测框。本轮把画面区改成三态接管（对齐主程序挂载时自动恢复语义）：

- **源在跑** → 接真实流（原逻辑保留）；
- **源没跑（刚打开）** → 自动恢复一轮：先让后端接回记忆中的上次源（画面+检测一起回），不行再按插件本地记忆的输入源配置重启（输入源设置页每次启动成功都会落一份 localStorage），全程占位层显示"正在恢复输入源…"；
- **恢复不了 / 用户手动停止** → 动态设计占位层（雷达扫描环 + 摄像机图标 + 扫描光条 + 呼吸文案，全 HUD 风格）：无源时显示"未接入输入源"并带「接入输入源」按钮直跳输入源设置页；手动停止后显示"画面已停止，点「开始」恢复"，**不会**把用户刚点的停止自动顶掉。占位层默认常驻（不透明），示例照片从此不再露出。

验证：`tests/uat/uat_20260707_showcase_video_ctrl.py`，**12/12 通过**（证据 `tests/uat/evidence_20260707_showcase_video_ctrl/`）：源停掉后打开检测中心零点击自动接回、占位层收起真流接入、控制条三件齐全、检测中进度禁用、待机后拖进度到 50%/倍速 2x 后端跟随、手动停止显示"画面已停止"且 4s 内不被自动恢复顶掉、再点开始画面回来、控制台零报错。

## 八点九、2026-07-07 第六轮修正（用户验收反馈）

**反馈 8：MediaPipe 到底启没启用？能不能像主程序一样改骨架样式？**

排查结论：
- **启用状态是真的**：插件性能设置里的 MediaPipe 开关一直是读写后端真实配置（当时后端确为启用 + 仅手部关键点开、姿态骨架关），不是摆设；
- **骨架样式确实缺失**：主程序 v3.32.0 起支持"自定义纯色骨架样式"（自定义开关 + 姿态/手部各自的连线颜色、关键点颜色、线条粗细，关键点颜色清空 = 跟随连线色），走视频流配置同一组接口、改完即时生效不用重载模型——插件性能设置没接这 7 个字段，等于在插件里"看得见启用、改不了样式"。

修复：MediaPipe 卡片内新增「骨架样式」小节（对齐主程序同款字段与语义）：自定义开关联动展开配色区；姿态/手部各三项（连线颜色、关键点颜色带"跟随连线"勾选、线条粗细 1-10）；随卡片「保存」一并写回，与主程序读写同一份后端配置（插件改了主程序立即可见，反之亦然）；未开自定义时保持 MediaPipe 默认多彩配色。

验证：`tests/uat/uat_20260707_showcase_mp_style.py`，**9/9 通过**（证据 `tests/uat/evidence_20260707_showcase_mp_style/`）：启用/姿态/手部三开关回读后端真值、样式区随自定义开关联动、手部连线色与关键点色（含跟随语义）回读一致、插件改手部连线 #12ABCD + 粗细 4 + 关键点跟随保存后后端逐字段跟随、试验值恢复原配置、控制台零报错。

## 八点十、2026-07-07 第七轮修正（用户验收反馈）

**反馈 9：手部骨架配色太丑（灰线大红点），配不上插件风格。**
说明：骨架是后端画进视频帧的全局样式（插件与主程序共用同一份配置），当时生效的灰线红点是早前在主程序里设置的旧值，不是插件画的。但插件确实应该给一套配得上自己风格的默认配色。

处理：
- **设计并即刻应用 HUD 配色**（与插件界面同源的青色辉光系）：手部连线 `#2EE0C8`（界面主青）+ 关键点 `#EAF9F6`（近白薄荷）；姿态连线 `#38B6FF`（HUD 蓝）+ 关键点 `#EAF3FF`；线条粗细 2（后端关键点半径 = 粗细+1，保持小巧不糊脸）；
- **骨架样式区新增「✦ HUD 风格预设」按钮**：一键把 7 个字段切到上述配色并保存（后续想换回主程序默认多彩配色，关掉自定义开关即可）。

验证：`tests/uat/uat_20260707_showcase_mp_hud_preset.py`，**6/6 通过**（证据 `tests/uat/evidence_20260707_showcase_mp_hud_preset/`）：先故意写回旧丑配色 → 插件里点预设按钮 → 后端 7 字段全部变为 HUD 配色、界面取色器同步、控制台零报错；另抓真实检测帧确认手部骨架已是青线白点效果。

## 九、遗留与边界

- 包为未签名版（`-uns`），装到出厂机前需主作者 `sign-plugin.py` 签名。
- 区域事件模式依赖 v3.32 检测帧字段（`region_events`/`label_split_rounds`）；在纯 v3.31.0 上该 SOP 增强自动缺省为普通渲染，不报错。
- 入站 Tab 为只读监视，接收配置修改仍走主程序/API（避免插件重复实现鉴权敏感写路径）。
