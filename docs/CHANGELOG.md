# Changelog

## v3.6.1 (2026-05-09)
- [FEAT-361-001] 步骤耗时（PT）新增「计算方式」维度：合并 / 最后一次（默认合并） — 客户场景中同一步骤一周期内多次连续出现时，原有 PT 仅取最后那段不直观；新增"合并(SUM)"档作为默认，旧行为切回"最后一次"即可。后端在周期/步骤耗时统计层加 `step_cycle_durations`（当前周期 SUM）+ `step_cycle_durations_history`（历史周期 SUM 列表，封顶 100），段消失结算 + `_supplement_step_durations` 两路同步累加，`end_cycle` 快照入历史并重置，`_discard_empty_cycle` 仅重置不入历史，`reset_stats` 全清；`/api/v1/source/detection/results` 新增 3 个字段（`cycle_sum_step_durations` / `last_cycle_sum_step_durations` / `avg_cycle_sum_step_durations`）；前端 `useSystemStore.display.monitor` 加 `ptAggregate`（默认 'sum'）；Settings 页加「PT 计算方式」下拉；Monitor `formatStepPT` 改造为（aggregate × mode）二维选数据源；单/多工位轮询同步填充新字段；备用步骤 `default_pt` 兜底覆盖新字段
- [TEST-361-001] PT 合并档暴露测试 + 既有 mock fixture 兼容修复 — 新增 `test_PT合并档_暴露三个字段_v3_5_x` 验证当前/最近/平均三档；现有 mock fixture 显式补 2 个新属性为空 dict 防 MagicMock 序列化爆炸；空 history 用例追加新字段必须 {} 的断言；相关测试 13/13 全过
- [DOC-361-001] 操作手册新增 3.2 节「PT 计算方式」+ FAQ Q16.1 — 合并/最后一次语义对照、与 PT 显示口径正交的 6 档组合、每周期单次出现两档等价、CSV 导出兼容性说明、SQL 端 `SUM(duration) GROUP BY cycle_id, step_label` 自助提示
- [CONFIG-361-001] 版本号升级到 3.6.1（package.json + splash.html）；StepRecord 表结构不变；CSV 导出"耗时(平均/秒)"列暂不联动新开关保持段级语义；老 localStorage 自动深合并默认值

## v3.6.0 (2026-05-08)
- [FEAT-360-001] 周期性强制动作能力增强 + 语义修正 — `pipeline_config.run_on_start` 新开关；`resume()/resume_inference()` 把 run_on_start=true 的规则计数推到 interval 并加入 `_run_on_start_pending`（不立即发事件）；新增 `_check_periodic_actions_on_first_step(step_label)` 钩入步骤完成流程（首步=触发步骤→静默清零；首步≠触发步骤→立即发漏检告警）；`reset_stats()` 显式保留周期计数器与 `_run_on_start_pending`；`_emit_periodic_notification` 标 `should_warn_no_barcode=False`；前端 Project 页加 run_on_start switch + 事件选择器旁加"+ 新建事件"按钮直跳事件管理对话框
- [FIX-360-001] 检测框越界三层防御性 clipping — 后端推理出口 `source_geometry.py::clip_bbox_normalized`（_detect_only / _detect_and_track / _detect_segment 全接入）+ 后端 Kalman 输出 `apply_kalman_filter` 对 smoothed_pos clip + 前端 Monitor `clipNormalizedBox` 助手（drawMultiDetections / drawDetections / mask polygon 全覆盖），任一层独立兜底
- [FIX-360-002] MES 无扫码器时不再误报 ⚠ 未绑码 (85c351c) — `services/mes_hooks.py::has_any_scanner_present()` + `is_warn_no_barcode()` early-exit；`source_event_trigger_mixin.py` 在每个 event 写 `should_warn_no_barcode` 字段，前端只消费这个布尔值
- [FIX-360-003] 海康摄像头 NameError 永久修复 (df7ce2c) — `source_camera_start_mixin.py` 顶部补全 SDK / ctypes import，不再依赖临时热补丁
- [DOC-360-001] 文档体系大重构 — 新建 `AGENTS.md`（827 行，14 章项目地图）；27 个 skill 全面整治：8 大修 / 6 中修 / 2 小修 / 3 新建（debug-export / debug-cluster / debug-operator-license）/ 8 保留；事实修正 14→**15** mixin、12→**10** 视图、24→**27** skill 总数
- [DOC-360-002] 操作手册 PDF 化 + UTF-8 BOM 修复 — 解决 Windows / Android 乱码问题；新增 `tools/build_manual_pdf.py` (reportlab + 中文字体)；交付物 `docs/软件操作手册.pdf` (~2.3MB) 入仓
- [TEST-360-001] 周期性动作测试套件按新语义重写 — `tests/features/periodic_actions.feature` + `tests/step_defs/test_periodic_actions.py` 重写；新增 `tests/test_periodic_actions_v352.py` 集成测试覆盖 run_on_start / 首步触发分支 / reset 不清零 / `should_warn_no_barcode` 标记
- [CONFIG-360-001] `.gitignore` 收紧 — 排除 `.tmp_audit/` / `test (2)/` / `tools/diag_*.py` / `tools/test_scan_pair_*.py` / `docs/*.html`
- [CONFIG-360-002] 版本号升级到 3.6.0；feat/plugin-system 分支清理（无独立提交）

## v3.5.1 (2026-05-06)
- [FEAT-351-001] 新增: Monitor 页 PT/CT 三档显示口径 — 后端 `/api/v1/source/detection/results` 暴露 `last_cycle_time / last_cycle_time_with_ng / current_cycle_time / last_step_durations` 4 个新字段; 前端 systemStore 加 `ptMode/ctMode` (默认 'avg', 三档 avg/last/current); Settings 页两个独立下拉支持 9 种组合; `formatStepPT / getDisplayCT / displayCT` 全面改造跟随 mode + ctIncludeNg 二维组合; multiChannelData 缓存新字段支持多工位
- [FEAT-351-002] 新增: 数据中心快捷 CSV 导出与显示口径联动 — `/data/export/csv` 接 `pt_mode/ct_mode` 参数; 新增 `_calc_aggregates(cycles, steps)` 工具 (按 step_label 分组求平均); `_write_session_export / _write_cycle_export / _write_range_export` 三个 builder 全部接受 mode; `ct_mode=avg` 时周期详情区在"耗时(秒)"旁多一列"耗时(平均/秒)" (每行填全局平均); `pt_mode=avg` 时步骤详情区同样多一列; last/current/None 时 CSV 保持原列结构 (老脚本兼容); 前端 4 个快捷按钮 (当日/某周/某月/日期范围) 全覆盖
- [DOC-351-001] 操作手册大幅扩充 — 690 行 → 1579 行 (+880 行); 4.2 加周期性强制动作 / 4.5 数据导出 4 类彻底重写 / 4.7 PT/CT 三档说明 / 4.8 MES 完整章节 (~330 行覆盖 7 个 Tab) / 5.4-5.9 共 6 个常见配方 (SN.txt / 清洁治具 / Word 占位符 / 日报 / 跟踪校准 / MES 闭环) / FAQ Q9-Q19 共 11 项 v3.5.x 相关
- [TEST-351-001] 测试套件扩至 95 项全过 — 新增 `tests/test_pt_ct_modes_exposure.py` (5) + `tests/test_csv_export_pt_ct_modes.py` (5); BDD/集成/Pairwise 75 项 (14.69s) + Playwright 浏览器 E2E 20 项 (83.83s)
- [CONFIG-351-001] 配置: 版本号升级到 3.5.1; 完全兼容 v3.5.0 数据结构与 API, 无需 migration

## v3.5.0 (2026-05-06)
- [FEAT-350-001] 新增: 自定义导出 / 客户模板系统 — Data 页"数据导出"tab 加"自定义导出"+ "实时规则"按钮; 5 种格式 (txt/csv/docx/xlsx/pdf) + 3 种 input_file_mode (none/read_template/append) + 路线 A (Jinja2 自动样式) / 路线 B (占位符模板上传); 308 字段中央仓库 (`export_field_registry.py`) 含拖拽编辑器; 新增 ORM `ExportTemplate / ExportRealtimeRule / ExportRunLog / SystemConfig` + manual migration; 解决客户场景"从固定文件夹提取 SN.txt → 检测后改写测试结果/各项检测值/程序版本号"
- [FEAT-350-002] 新增: 实时规则 — cycle 结束自动渲染落盘. `services/export_realtime.py::dispatch_cycle_end_export` 入口扫描启用规则、按 channel/project filter 匹配后渲染; `mes_hooks._handle_cycle_end` 末尾调用, 独立 try/except 不影响 MES Hook / Scanner / Container 清理; ExportRunLog 记录每次执行 + 错误堆栈 + 前端日志面板; 规则 CRUD 含 test-run 干跑能力
- [FEAT-350-003] 新增: 周期性强制动作 (Periodic Required Actions) — 解决场景"动作 A-B-C-D 每做完 20 轮必须做动作 E, 超期触发事件". 新增 `PeriodicActionsMixin` (count_basis ∈ {all/good_only/ng_only} × reset_policy ∈ {always/only_when_due} × overdue_repeat ∈ {every_cycle/once/cooldown:N} × interval 全可配); VSM MRO 加 mixin; `apply_project_config` 末尾调用 `_apply_periodic_actions`; `end_cycle()` 在 commit + MES Hook 后调用 `_check_periodic_actions`; `get_detection_results` 暴露 periodic_actions 状态; Project 页"逻辑设置"加独立卡片 + Monitor 页加进度区块 (counter 进度条 + 状态色 ok/due/overdue); 计数器 JSON 持久化
- [FEAT-350-004] 新增: SystemConfig KV + License 缓存 — `/api/v1/system/display` 读写 brand_name / app_name / inspector_name / device_number / factory_name / line_name; 前端从 Electron IPC 推 license 给后端 (`/api/v1/system/license-cache`), 模板可用 `{{ license.customer }} {{ display.factory_name }}` 等
- [FEAT-350-005] 新增: Step 置信度聚合 (`sessions_stats.py` 加 avg/min/max_confidence) + 内部跟踪状态暴露 (`_stack_state / max_recognized` 通过 `get_detection_results.tracking` 子树暴露)
- [TEST-350-001] 测试框架建立 — 4 层 86 个测试 1 分 9 秒跑完: L1 BDD/集成 (pytest-bdd 29 个) + L2 Pairwise 矩阵 (allpairspy 减枝, 周期性 4 维 11 个 / 导出 3 维 9 个 / input_modes 9+3 个) + L3 集成链路 (5 个真 DB+真 Jinja2+真磁盘 IO) + 真实视频 sanity (1 个 @slow, 启真 cv2 + VSM) + L4 Playwright 浏览器 E2E (sanity 5 + Project 4 + Monitor 3 + Data 5 + 实时规则 3 = 20 个); 全 conftest 隔离; e2e_browser 用 `__e2e_` 前缀清理保证不留垃圾
- [CONFIG-350-001] 配置: 版本号升级到 3.5.0; 新依赖 `allpairspy / pytest-bdd / playwright / pytest-playwright / docxtpl / reportlab` 写入 `backend/requirements.txt`

## v3.4.1 (2026-04-30)
- [BUG-341-001] 修复: D 模式扫码器灯一直闪 (像 continuous 持续扫码) — `services/scanner.py` 的 ERROR 自动续 LON 路径只把日志加了个 `mode=D` 标签, 没真正排除 D 模式; listen 主循环也照样按 `_lon_sent=False` 逻辑续 LON. 改动 3 处: `start_scanning` D 模式不发 LON (灯一开始保持灭, 等 box 跨线触发); ERROR 收到 D 模式只 log 不动 `_lon_sent / _next_lon_after`; listen 主循环 D 模式跳过自动续 LON. 现在 D 模式 LON 唯一发送源是 `source._scan_d_update.send_lon_for_channel`, 真正实现"按 box 物理位置脉冲式发"
- [BUG-341-002] 修复: 前端 D 模式校验工位拿错 — 切到 D 模式时校验的是 `broadcast_channels[0]` (广播工位的第一个) 而不是 `channel_id` (绑定工位); 几何编辑器加载 snapshot 也是同样错误. 用户场景"绑定工位=工位2 / 广播=[工位1, 工位2]"时报"工位 1 不是容器模式"被回退. D 模式状态机跑在扫码器**绑定工位**的检测帧循环里 (用绑定工位的摄像头看 box), 广播工位与 D 触发判定无关. 改成只看 `channel_id`
- [FEAT-341-001] D 模式诊断日志增强: `_scan_d_update` 加首次出现 box / 跨线方向反 / 跨线触发 LON 三类调试 log, 让用户拿到日志一眼看出"为什么没触发" (是 box 没跨过线 / 还是配的方向反了 / 还是 box 一直在线同侧). 客户场景 v3.4.0 测试时画线后 LON 没触发, 加这日志后能直接定位
- [TOOL-341-001] tools/test_scan_d_trigger.py 8 用例全过 (含新增诊断 log 不破坏判定逻辑)
- [CONFIG-341-001] 配置: 版本号升级到 3.4.1 (hotfix)

## v3.4.0 (2026-04-30)
- [FEAT-340-001] 新增: 扫码模式 D — 容器跨线/区域触发 LON/LOFF 闭环 (仅容器模式项目可启用). 扫码器编辑加 D 选项 + 几何弹窗 (画线 + A/B 两侧染色 + 选触发方向, 或画 polygon 区域); 容器中心点跨线方向匹配 / 进区域 → 后端发 LON, 扫到码自动 LOFF + 等 box 离开 reset 允下一个; 同时刻只允许一个 armed box (产线节奏串行假设, 异常并发 log 不重复发); armed 但 box 离开未扫到码 → 主动 LOFF 防 LON 残留
- [API-340-001] 新增: `GET /scanner/check-container-mode?channel_id=N` — 切换 D 模式时前端校验绑定工位是否容器项目, 否则弹警告自动回退. `services/scanner.py` 加 `send_lon_for_channel / send_loff_for_channel / is_scan_d_for_channel / get_scan_d_config_for_channel` helper
- [TOOL-340-001] 新增: `tools/test_scan_d_trigger.py` 8 用例 (line A→B 触发 / 反向不触发 / 扫码 LOFF / scanned 后 gone 重置 / 同 box 不重 LON / zone 进入触发 / armed 离开未扫码强制 LOFF / 并发 box 不重发); v3.3.0 / v3.2.x / v3.1.x 回归 87/87 全过
- [CONFIG-340-001] 配置: scanner_devices 加 `scan_d_geometry / scan_d_line / scan_d_zone / scan_d_gone_confirm_frames` 4 个字段 (自动 migration); 版本号升级到 3.4.0

## v3.3.0 (2026-04-30)
- [FEAT-330-001] 新增: 扫码闭环结算 (`bind_timing='scan_pair'`) — 扫码 A 起新窗口, 扫码 B (≠A) 结算 A 周期 + 起 B 窗口, 同码二次扫软忽略 + 推 dup_warning toast, 多工位广播共享窗口同步开/同步结算; 容器模式按 sticky `was_complete` 判 OK/NG (允许工人扫前从箱里拿件后扫下一码), 非容器模式按 `_tracking_was_complete` 判定; 新增 `scan_pair_max_wait_sec` 超时兜底 (0=不超时, >0=强制 NG); 切到此模式自动锁定 `scan_required=on / scan_mode≠C / late_bind=0`; 停止/待机时弹窗 [结算 (默认)] / [丢弃] 收尾最后一码窗口; container_grouping 在 scan_pair 激活时关掉 gone-confirm 自动 _settle_box (扫码节拍接管)
- [TOOL-330-001] 新增: `tools/test_scan_pair_settle.py` 8 用例覆盖 (首扫 / 同码软忽略 / 不同码切窗口 / sticky OK / 从未齐过 NG / 超时强制 NG / 多工位广播共享 / 停止双分支); v3.2.0 / v3.2.1 / v3.1.2 回归 6+8+11 全过
- [CONFIG-330-001] 配置: scanner_devices 加 `scan_pair_max_wait_sec INTEGER DEFAULT 0` (自动 migration); bind_timing 字符串字段加新枚举值 `'scan_pair'`; 版本号升级到 3.3.0

## v3.2.0 (2026-04-30)
- [BUG-320-001] 修复: 容器 ID 漂移幽灵箱深层兜底 — `_update_container_grouping` 加 active 接管 (gone-confirm 期间同位置 IoU≥阈值复用老 did, 不开新条目); JC1 测试视频离线仿真 17→12 settle, 合并 5 次 ByteTrack 切 ID
- [FEAT-320-001] 新增: 项目设置 → 跟踪选项 → 容器策略行加 "ID 漂移合并 IoU" `el-input-number` (默认 0=关闭, 0.5=推荐, 0.7+=保守; 老项目升级行为不变)
- [TOOL-320-001] 新增: `tools/test_id_drift_merge.py` 6 用例覆盖 (核心 / 边界 / 配置兼容); v3.1.4 回归 11/11 全过
- [CONFIG-320-001] 配置: 版本号升级到 3.2.0

## v3.1.4 (2026-04-29)
- [BUG-314-001] 修复: 容器多箱模式幽灵箱误报 NG (客户机合格率 30% → 真实合格率), `_settle_box` 入口加 `container_settle_min_items` 阈值过滤
- [FEAT-314-001] 新增: 项目设置 → 跟踪选项 → 容器策略下"结算最少件数" UI (默认 1, 设 0 关闭过滤)
- [TOOL-314-001] 新增: `tools/test_ghost_box_fix.py` 11 用例覆盖 (含 Negative 复刻 v3.1.3 bug 现象的反证)
- [CONFIG-314-001] 配置: 版本号升级到 3.1.4

## v3.1.3 (2026-04-29)
- [BUG-313-001] 修复: ffmpeg 多线程解码偶发断言 SIGABRT 导致 uvicorn worker 崩溃 / 8001 永久卡死 (强制单线程解码兜底)
- [BUG-313-002] 修复: 跟踪模式下"步骤统计"表永远停在 `--/待检测`, 改用 `tracking.item_checklist` 翻 OK
- [FEAT-313-001] 新增: 多工位 Monitor per-channel MES 信息条 (工件号/未绑码/等待扫码/清除按钮 + OK/NG 3.5s hold), 与单工位行为对齐
- [CONFIG-313-001] 配置: 版本号升级到 3.1.3

## v3.1.2 (2026-04-29)
- [BUG-312-001] 修复: 容器分组迭代过程中并发删除导致 `KeyError: '泡沫槽3'`
- [BUG-312-002] 修复: 客户机录制视频全是 0 帧 / 播放失败 (NameError 被吞 + FFmpeg stderr 屏蔽 三层叠加)
- [BUG-312-003] 修复: USB 摄像头光线变暗后 FPS 从 30 跌到 10 之后再也不回 (auto exposure 被驱动锁定)
- [FEAT-312-001] 新增: 多工位广播扫码"主工位带动结算"模式 + 件数门槛
- [FEAT-312-002] 新增: 集群汇总 "OK 不可被 NG 覆盖" 合并策略 (`ok_lock`)
- [FEAT-312-003] 新增: ByteTrack `match_thresh` 暴露到项目设置
- [TOOL-312-001] 新增: v3.1.2 全量仿真测试套件 + 一键汇总 (75/75 通过, 13.2s)
- [CONFIG-312-001] 配置: 版本号升级到 3.1.2

## v3.1.1 (2026-04-29)
- [FEAT-311-001] 新增: 称重器"即时快照"配对模式 (pairing_mode=instant) — 解决"流水线工件不回零、1 工件 1 工位不允许丢数据"
- [CONFIG-311-001] 配置: 版本号升级到 3.1.1
- [TOOL-311-001] 新增: tools/test_weight_pairing.py 配对模式端到端仿真 (7 PASS / 0 FAIL)

## v3.1.0 (2026-04-28)
- [FEAT-310-001] 新增: 工单按项目/工位/集群三选一计件 (binding_scope) — 解决"集群已完成但工单数量一直 0"
- [FEAT-310-002] 简化: 集群模式工单不再选目标站点 (主机即代表集群,按 priority 取首条)
- [BUG-310-001] 修复: cluster-only 部署下 _handle_session_start "没找到工单"日志噪声
- [CONFIG-310-001] 配置: 版本号升级到 3.1.0
- [TOOL-310-001] 新增: tools/test_workorder_binding.py 工单绑定端到端仿真 (32 PASS / 0 FAIL)

## v3.0.0 (2026-04-27)
- [BUG-300-001] 修复: 检测启动卡住、开始按钮不可用、TensorRT 引擎不兼容
- [BUG-300-002] 修复: 检测停止/待机接口被离线扫码器拖慢约 20 秒
- [BUG-300-003] 修复: 会话启动时导出设置加载失败
- [BUG-300-004] 修复: 离线扫码器/外设反复打印大段 traceback
- [BUG-300-005] 修复: 数据中心 MES 工单筛选缺少刷新函数
- [BUG-300-006] 修复: MES 工件状态标签类型警告
- [BUG-300-007] 修复: 报表跨天班次统计边界和平均耗时单位误读
- [BUG-300-008] 修复: 录像写入失败对用户不可见
- [BUG-300-009] 修复: Electron 开发模式前端端口与计时器清理问题
- [BUG-300-010] 修复: 多通道 Monitor 局部状态串扰
- [FEAT-300-001] 新增: 开发者模式下的模拟扫码入口
- [FEAT-300-002] 新增: 开发者模式下的模拟外设/称重数据入口
- [FEAT-300-003] 新增: MES 刷新/测试按钮 loading 与可见反馈
- [CONFIG-300-001] 配置: 开发机后端启动环境切换为 `tianjun-runtime`
- [CONFIG-300-002] 配置: 版本号升级到 3.0.0
- [TOOL-300-001] 新增: 全量路由烟测与导入检查工具
- [TOOL-300-002] 新增: MES / Source 端到端回归测试脚本
- [TEST-300-001] 验证: 全前后端功能 QA 复测
- [TEST-300-002] 验证: 检测运行环境与发版元数据一致性
- [SKILL-300-001] 更新: 10 个 v3.0 相关 skill（API、source、检测、MES、前端、Session、打包发版）

## v2.7.15 (2026-04-25)
- [BUG-015-001] 修复: 工单管理表头修改了模板没生效, 一直是硬编码中文 (改成 v-for 渲染 visibleColOrder + colLabel 动态解析)
- [BUG-015-002] 修复: 新建工单弹窗字段标签变成数字 (formItems 过滤掉 type=system, 表单只渲染表单字段)
- [BUG-015-003] 修复: 字段配置"显示开关"默认 OFF 关闭后表格变全空白 (一次性迁移 visible=true + openTemplate 强制 normalize)
- [FEAT-015-001] 新增: 工单管理表头全动态化 (SYSTEM_COL_META + colLabel, 系统列也能改显示名)
- [FEAT-015-002] 新增: 字段"显示"开关 - 任意列可隐藏 (system / preset / custom 全可显隐)
- [FEAT-015-003] 新增: 自定义字段也能进表格 (从 row.extra_data 取值, 默认列宽 120)
- [FEAT-015-004] 新增: system 列"类型"显示具体类型 (标签/进度条/按钮组等), 加(不可改)后缀

## v2.7.14 (2026-04-24)
- [BUG-014-001] 修复: 跟踪模式数据中心 OK/NG 偶尔空白 + 显示非清单物品 (_settle_counting_cycle / _settle_box 严格按 expected_items 过滤 + 虚拟补齐 + 容器模式补 record_step)
- [BUG-014-002] 修复: 摄像头检测帧率不稳定 29-31 vs 10 fps (A: _bench_fps 无条件实测一次; B: 全后端候选 CAP_PROP_BUFFERSIZE=1; C: MJPEG generator try/finally 捕获断开)
- [FEAT-014-001] 新增: 只翻显示不翻推理 (推理用 raw_frame 保持训练精度, 显示/录像/快照用 display_frame, bbox 坐标做映射)
- [TEST-014-001] 新增: tools/test_display_transform.py / test_tracking_settle_filter.py / test_mjpeg_disconnect.py 三套验证脚本

## v2.7.13 (2026-04-24)
- [BUG-001] 修复: 跟踪模式"秒→帧"阈值按 fps_actual 换算导致实际延迟约设定值的 5 倍 (改用 fps_inference 新统计量, 去掉 min_cycle_age 的 1 秒兜底)
- [BUG-002] 修复: 集群同条码二次校正后"待汇总/最近完成"两栏不一致 (方案 B: 判齐全用 all_records, 二次齐发按 overall_result+ng_items 决定是否重推 MES; 假未齐箱从 pending 列表剔除)
- [FEAT-001] 新增: get_detection_results / get_source_status / get_manager_config 暴露 fps_inference 字段
- [TEST-001] 新增: tools/test_tracking_fps.py 跟踪模式 FPS 换算验证, 实测 1 秒阈值旧 5.29s → 新 1.43s
- [TEST-002] 新增: tools/test_cluster_recovery.py 集群方案 B 端到端回归 (独立 tempdir 数据库)

## v2.7.12 (2026-04-23)
- [BUG-001] 修复: 工单新建对话框 7 个输入框全部隐形 (DynamicFieldInput h('el-input', ...) 字符串名 Vite+ElementPlus auto-import 不解析，改显式 import ElInput 等对象)
- [BUG-002] 修复: 计划数量显示 0.00 (DynamicFieldInput number 类型固定 precision:2，planned_qty 单独走整数精度)
- [BUG-003] 修复: 集群目标明细同通道多行、同条码 OK/NG 并存 (sub_reports 按 (channel_id, source_address) 去重保留最新)
- [BUG-004] 修复: 扫码器重复扫码留脏 queued 记录 (新增 ok_rescan_cooldown_sec 冷却, OK 后冷却窗口内同码静默, NG 不受影响)
- [BUG-005] 修复: Monitor 结果一闪而过 1.2s 就清 (延长到 3.5s, 新条码到达直接覆盖)
- [FEAT-001] 新增: MES 适配器支持 4 种参数形式 (新增 form-urlencoded 字段平铺 + query-string URL 参数; GatewayPanel 4 选 1, 每种一句话说明)
- [FEAT-002] 新增: 集群箱数据按条码删除 + 批量清理 (DELETE /cluster/box/{barcode}, POST /cluster/boxes/clear scope=all/pending/recent/older)
- [DOC-001] 升级: 客户 MES 对接文档 v1.1 (4 种参数形式并列 + 各自 curl 示例; docx/pdf 重生成)
- [TEST-001] 扩展: test_mes_gateway.py 到 9 场景+3 API (新增 8806 form-urlencoded / 8807 query-string mock 端口, 9/9+3/3 全绿)
- [TEST-002] 新增: test_fixes_simulation.py (扫码冷却 / 集群去重 / 批量清理 API 端到端仿真)

## v2.7.11 (2026-04-23)
- [FEAT-001] 新增: MES Gateway 统一鉴权 (bearer / api_key / custom_header 三种方式合并进 headers)
- [FEAT-002] 新增: MES Gateway 按结果过滤 (push_on_result 支持只推 OK / 只推 NG / 全推)
- [FEAT-003] 新增: MES Gateway 物料名称映射 (label_mapping 把中文步骤名转成客户物料代码)
- [FEAT-004] 新增: 集群 aggregated 顶层便利字段 (order_no / workpiece_id / ng_items / result)，模板不再要嵌套取值
- [FEAT-005] 新增: 前端 GatewayPanel UI 图形化 (鉴权下拉 + 额外 headers + 过滤 + 映射 + 4 个预设模板)
- [FEAT-006] 新增: /test API 按 event_type 分路生成真实 context (cycle_end / box_complete / box_timeout)
- [DOC-001] 新增: docs/客户MES对接数据格式.md + docx + pdf (含 OK/NG/超时 3 个完整示例 + 10 项贵方确认清单)
- [TOOL-001] 新增: tools/test_mes_gateway.py MES 端到端测试 (5 个 mock 客户 MES, 10/10 场景全绿)
- [CI-001] 变更: CI 双通道分卷 (Action Artifact 和 GitHub Release 都走 1.9GB 分卷, main push 也跑 Split)
- [SKILL-001] debug-mes 追加 4 章 (push_on_result / label_mapping / 统一鉴权 / test event_type)

## v2.7.10 (2026-04-21)
- [BUG-001] 修复: 扫码器 device_type=text_lon 对现场 WMax 固件完全无效 (连上但永远扫不到码；_start_device 自动升级为 auto + 新增 _trigger_wmax_discover_once 合并触发一次 UDP 发现+激活 RPT)
- [BUG-002] 修复: WMax 扫码器连接后必须 activate_rpt_reporting 才识别 (v2.7.7 改 ondemand 是误判；现场固件不响应单次 Trigger 只认常开 RPT；wmax/manager.py 两处 auto_discover + scanner.py 三处 _ensure_wmax_connected 统一激活；test_connection 改 activate_rpt+收码5s)
- [BUG-003] 修复: 容器模式同一 label 跨帧遮挡重现 track_id 变更导致累积超额 NG (_update_container_grouping 按 steps_config.max_recognized 做 box 内上限检查，到上限只刷 last_seen 不再 +1)
- [BUG-004] 修复: 称重器稳定后 set_barcode 不立即派发 (SN-0016 汇总丢失；set_barcode 注入时如外部设备已是稳定称重器直接 _dispatch)
- [CLEAN-001] 清理: hotfix.py::apply() 去掉 v2.7.7c 三块运行时补丁调用 (已并入源码；函数定义保留作回退)
- [FEAT-001] 新增: 集群聚合 cycle_context.sub_reports (每个通道原始快照) + ClusterPanel.vue 子报告卡片
- [FEAT-002] 新增: 扫码器/外部设备 pairing_group + external_only (只喂外部设备的扫码器按分组号匹配，UI 隐藏绑定工位)

## v2.7.9 (2026-04-21)
- [BUG-001] 修复: 集群汇总页「已连接副机」切页后消失 (副机心跳原仅由前端 ClusterPanel setInterval 发送，页面 onUnmounted 即停；ClusterCollector.start() 新增后台线程 _heartbeat_sender_loop 每 5s 自主 POST /cluster/heartbeat，脱离前端页面状态)
- [BUG-002] 修复: Windows 串口 PermissionError 13 拒绝访问 (三个连接 loop 统一用新增的 _open_serial_with_retry 辅助，失败 1s 间隔重试 3 次；解决上次 close 未完全释放和 test_connection 竞争两种残留)
- [SKILL-001] debug-mes 追加「集群汇总 / 副机心跳」「外部设备串口 PermissionError 13」两章

## v2.7.8 (2026-04-21)
- [BUG-001] 修复: v2.7.6 传动杆误判过滤两个开关从未生效 (read_rod_filter_config 只读顶层但 _build_project_config 从不把字段放顶层，改为优先读 pipeline_config 兼容回退)
- [FEAT-001] 新增: Project 配置页「误判过滤（高级）」卡片，两层通用开关 UI (按 label 字符串匹配、可选或手输入 label、默认全关、老项目零影响)
- [SKILL-001] modify-source 更新 rod_filter 章节，记录 v2.7.6 静默失效坑
- [SKILL-002] modify-project-config 补充 pipeline_config 两个新子字段和数据流
- 已知遗留（v2.7.9 解）: 工位 3 称重器 COM20 权限拒绝；集群汇总无数据；副机 A 数据切页丢失

## v2.7.7 (2026-04-21)
- [BUG-001] 修复: WMax 扫码器三端口协议对齐 (DataLen 默认 4B→3B；encode_get_config_opt config_id<0 省略 field1；删除 HandShake 命令；activate_rpt_reporting 改为 GetConfigOpt+TurnOnOffVideo 序列)
- [BUG-002] 修复: scanner.py device_type='auto' 被误降级为 text_lon (保留原值，auto/wmax 统一走 WMaxDeviceManager 三端口)
- [BUG-003] 修复: _listen_loop 为 auto/wmax 建本地 TCP 抢占 WMax CMD 端口 (改为 pending_wmax 等待循环，完全交给 WMaxDeviceManager)
- [BUG-004] 修复: UDP 自动发现成功后 scanner conn.status 未更新 connected (统一处理 connected_mgmt_ips)
- [BUG-005] 修复: 测试按钮对 WMax 扫码器无效 (改走 WMaxDevice.flash_and_scan + 同步采集条码)
- [FEAT-001] 变更: 扫码器改为触发式 (ondemand) 工作模式——启动只建 TCP 不激活 RPT；start_scanning 发 LON，stop_scanning 发 LOFF；trigger_on/off 同时发 TurnOnOffVideo+Trigger+SendTermCmd 三命令兜底
- [FEAT-002] 新增: 测试按钮闪光 5 秒 + 同步返回扫到的条码 (ElMessage 直接显示"扫到 X 条: XXXXX")
- [DOC-001] 新增: WMax 协议文档 (docs/wmax_protocol.md) + IDManager 反编译资料 + wmax_known_devices.json 持久化
- [KNOWN] 已知: 扫码器 LOFF 后灯不熄灭 (三种关灯命令都发了但扫码器固件保持扫描模式，待抓包 IDManager 关灯行为后对齐，不影响扫码功能)

## v2.7.6 (2026-04-21)
- [BUG-001] 修复: 扫码器连上但"开始检测"不发 LON 命令 (resume/standby/resume_inference 补上 start/stop_scanning + _resolve_bound_channels 超界降级 + skip 原因显式日志)
- [FEAT-001] 新增: 称重器稳定值判定 + 抖动/空载过滤 (stable_delta/stable_count/zero_threshold 状态机 + 中位数上报)
- [FEAT-002] 新增: "有重无码"告警 (默认关闭 + 可配置延迟；MESGateway.dispatch + alarm_router 双通道)
- [FEAT-003] 新增: 传动杆误判过滤 (filter_rod_by_companion 空间共现 + RodSessionGate 软时序；默认关；实测砍 97% 误判保留 97.5% 真阳)
- [CONFIG-001] 配置: UI 工位编号统一 1-indexed (ScannerPanel/ExternalDevicePanel 动态生成选项 + 设备卡片 +1 显示)
- [SKILL-001] 更新: modify-source（传动杆误判过滤整章）

## v2.7.5 (2026-04-20)
- [BUG-001] 修复: 外部设备清空日志 int_parsing / [object Object] (FastAPI 路由顺序调整 + synchronize_session=False + 重试 + 原生 SQL 兜底 + 前端 detail 规范化)
- [BUG-002] 修复: 外部设备串口路径含前后空格导致保存失败 (前端 trim + 后端 _sanitize_device_payload + 串口打不开时改返回 200+warning)
- [BUG-003] 修复: 称重器 Modbus ASCII 粘包无法解析 (_parse_modbus_ascii_response 按 : 分段 + 逐段 LRC 校验)
- [FEAT-001] 新增: 画面旋转 / 镜像按通道独立配置 (VideoSourceManager 新增 video_rotation/flip_h/flip_v + _apply_frame_transform 在 _capture_loop 帧拷贝后应用 + per_channel 持久化 + GET/POST /source/transform/config API)
- [FEAT-002] 新增: MES 工单表单模板编辑器 (OrderPanel 重写：字段配置弹窗可改名/删除/重排预设字段 + 添加 8 种类型自定义字段 + DynamicFieldInput 动态渲染 + localStorage 持久化 + 必填字段兜底)
- [FEAT-003] 新增: MES 推送 context 便利字段 ng_steps[] + cycle.missing_step_count (build_context_from_cycle 追加，不破坏旧字段)
- [CONFIG-001] 配置: 外部设备 UI 暴露串口子参数 (数据位 5/6/7/8 + 校验位 N/E/O/M/S + 停止位 1/1.5/2)
- [SKILL-001] 更新: modify-source / api-sync / debug-mes / modify-frontend 四个 skill

## v2.7.4 (2026-04-20)
- [FEAT-001] 新增: 物品标注框可视化隐藏 (Project 步骤表加 hide_in_view 开关；Monitor 实时画面/SOP/步骤详情过滤；后端 0 改动)
- [FEAT-002] 新增: 堆叠模式 (跟踪计数模式下同 label 消失 N 秒后再现算下一层；新增 stack_enabled/stack_reappear_seconds/stack_required_count；独立状态机 + max 合并避免与 ByteTrack 计数双算)
- [FEAT-003] 新增: 最大识别数 (跟踪计数模式下同 label 同时只保留 Top-N 个 track_id，按置信度选 keeper 其余按距离归并；不动 ByteTrack 内部状态)

## v2.7.3 (2026-04-19)
- [BUG-001] 修复: 双工位 TensorRT 推理 imgsz 不传播报 AssertionError (engine 元数据直读 + channel_manager 三处属性传播)
- [BUG-002] 修复: 报警灯停检测/关软件不熄灭、副机检测时不亮 (pause/standby/resume 联动 stop/start_idle_light + shutdown 遍历所有通道 + start_idle_light 详细日志)
- [BUG-003] 修复: 扫码器允许同 IP+端口重复保存 (前后端 IP+port 唯一性校验，重复返回 409)
- [FEAT-001] 新增: 共享报警灯——一个物理报警灯多工位共用 + 优先级合成 (NG>警告>OK>待机，可配置；运行时 reload 无需重启)
- [FEAT-002] 新增: 共享报警灯示例配置文件 alarm_config.shared.example.json
- [SKILL-001] 更新: debug-alarm skill 增加共享报警灯模式整章

## v2.7.2 (2026-04-18)
- [BUG-001] 修复: 切换工位后 Toast 重复弹出 (events_log + _event_seq 清理 + 前端 initMultiChannelData 强制重置)
- [BUG-002] 修复: 降工位时 MESHook 残留工单/扫码状态 (新增 on_channel_removed 清理 6 个 dict)
- [BUG-003] 修复: 降工位时 AlarmRouter 串口/蜂鸣器残留 (新增 on_channel_removed: stop_alarm + all_off + disconnect + pop)
- [BUG-004] 修复: 数据中心 CSV 导出不区分项目和工位 (后端加 project_id/channel_id 参数 + CSV 加工位列 + 分组标题带 [项目名/工位N])
- [FEAT-001] 新增: 数据中心导出范围可见化提示条 + "导出全部项目"开关 + 文件名编码 proj{id}_ch{N}
- [FEAT-002] 新增: 降工位清理钩子公共接口 (on_channel_removed 统一入口)
- [TEST-001] 新增: 降工位清理测试 test_mes_hook_cleanup.py (5 用例)
- [TEST-002] 新增: 导出 CSV 全路径测试 test_export_csv.py (13 用例)

## v2.7.1 (2026-04-17)
- [BUG-001] 修复: 扫码器手动添加设备覆盖已有设备 (editingId 未重置)
- [BUG-002] 修复: 顺序检测步骤回退误判OK (A-B-A-B-C 全局去重→连续去重+回退标记)
- [BUG-003] 修复: 后端日志噪音 (_get_mgr 失败和循环# 调试日志移除)
- [BUG-004] 修复: 报警灯串口连接失败无详细错误 (返回具体错误+Linux自动chmod)
- [FEAT-001] 新增: 集群副机心跳与主机在线副机显示 (10秒心跳+已连接副机卡片)
- [FEAT-002] 新增: 虚拟扫码器/称重器 tkinter 版 (bat一键启动，无需额外安装)

## v2.7.0 (2026-04-16)
- [FEAT-001] 新增: 扫码器 LON/LOFF 模式 (TCP 55256 替代 WMax 逆向协议，自动连接+检测联动)
- [FEAT-002] 新增: 防重复结算开关 (跟踪模式同批次 OK+NG 重复结算可选抑制)
- [FEAT-003] 新增: TensorRT 转换超时保护 (600s 超时 + GPU/CUDA 环境诊断)
- [FEAT-004] 新增: 集群超时推送选项 (等齐模式超时可选推送 TIMEOUT 到 MES)
- [FEAT-005] 新增: 前端数值限制放开 (移除所有 :max，支持长时间场景)
- [FEAT-006] 新增: 前端术语通用化 (箱子→目标 等通用词替换)
- [FEAT-007] 新增: 称重器 Modbus ASCII + 连续发送双模式
- [FEAT-008] 新增: 扫码器条码自动注入外部设备
- [BUG-001] 修复: 未扫码 Toast 在扫码后仍然显示
- [BUG-002] 修复: MES 前端操作无反馈 (全面添加 ElMessage + loading)
- [BUG-003] 修复: cluster_config.timeout_push 列缺失启动报错

## v2.6.0 (2026-04-14)
- [FEAT-001] 新增: 多工位/多通道架构 (1-4 工位，每通道独立项目/模型/GPU)
- [FEAT-002] 新增: 集群汇总系统 (Master/Slave 多机互联，箱子数据汇总推送)
- [FEAT-003] 新增: 外部设备集成框架 (TCP/Modbus TCP/串口/HTTP 轮询 + 5 种解析)
- [FEAT-004] 新增: MES Gateway 工位绑定 (每个 MES 连接可绑定指定工位)
- [FEAT-005] 新增: 扫码器广播 (单扫码器触发多通道检测)
- [FEAT-006] 新增: 多工位通知/报警隔离 (独立指示灯/语音/Toast)
- [FEAT-007] 新增: 多工位计数器独立持久化
- [FEAT-008] 新增: 自动保存/恢复全流程 (视频源+项目+模型+GPU+检测状态)
- [FEAT-009] 新增: 每通道独立检测/语音/Toast 设置
- [BUG-001] 修复: 多工位项目配置被全局覆盖导致 GPU 工位检测不到东西
- [BUG-002] 修复: channel_count 热重载被覆盖为 1 导致多工位黑屏
- [BUG-003] 修复: MJPEG 四通道视频卡顿 (CPU 争抢)
- [BUG-004] 修复: TensorRT imgsz 不匹配导致推理静默失败
- [BUG-005] 修复: 跟踪模式步骤截图不显示 (cv2 未导入)
- [BUG-006] 修复: 视频文件源不自动恢复
- [BUG-007] 修复: 推理连续报错导致 CPU 100%
- [BUG-008] 修复: 报警串口不自动重连

## v2.5.0 (2026-04-09)
- [FEAT-001] 新增: 报警灯空闲常亮模式（检测中常亮，OK/NG闪烁后恢复）
- [FEAT-002] 新增: 工件条码绑定系统（中途扫码绑定、去重、误检重绑）
- [FEAT-003] 新增: Data 页工件码列显示
- [FEAT-004] 新增: 扫码成功提示框（可配置 Toast）
- [FEAT-005] 新增: 未绑码告警（Toast + 闪烁横幅）
- [FEAT-006] 新增: 扫码记录管理增强（清空、快速去重、日期筛选）
- [FEAT-007] 新增: Modbus RTU/TCP MES 适配器（RS485 串口写寄存器）
- [FEAT-008] 新增: 扫码器模拟器（PyQt5 TCP 调试工具）
- [BUG-001] 修复: 改步骤设置后返回 Monitor 视频黑屏
- [BUG-002] 修复: Tracking 模式非预期物品导致误判 NG
- [BUG-003] 修复: 未绑码告警不触发（import 路径错误被静默吞掉）
- [BUG-004] 修复: 单通道模式 MES 数据不传递到 Monitor
- [BUG-005] 修复: 设置页 Toast 配置卡消失（detection_config null 导致）

## v2.4.0 (2026-04-07)
- [BUG-001] 修复: TensorRT 模型切换后无检测框（imgsz 检测优先级错误）
- [BUG-002] 修复: 视频重新检测后 FPS 飙升至 60+（高分辨率帧预缩小）
- [BUG-003] 修复: 第一步结算模式有两个触发点（最后一步消失不再触发）
- [BUG-004] 修复: 第一步结算后新周期丢失第一步（raw_start 回填）
- [BUG-005] 修复: 自定义提示框计数器重启后清零（持久化到 DB）
- [FEAT-001] 新增: 高分辨率推理性能优化（预缩小帧架构）
- [FEAT-002] 新增: 外部 MES 工单双向推送（receive + extra-data）
- [FEAT-003] 新增: MES 适配器框架（gateway + 可插拔 adapters）
- [FEAT-004] 新增: 作业员管理系统（登录/登出 + Session/Cycle 关联）
- [FEAT-005] 新增: 工单管理页面增强（来源/产品编码/自动刷新/extra_data）
- [FEAT-006] 新增: 离线授权更新工具（read_machine_id + update_license bat）

## v2.3.0 (2026-04-02)
- [FEAT-001] 新增: 内置 MES 系统 — 工单全生命周期管理（状态机/计数/良率/批次）
- [FEAT-002] 新增: 单件追溯系统（扫码登记→检测→缺陷→返工，全链路）
- [FEAT-003] 新增: 缺陷自动分类（检测标签→缺陷代码映射 + Pareto 统计）
- [FEAT-004] 新增: MES Hook 异步集成引擎（不影响检测帧率）
- [FEAT-005] 新增: VS600 扫码器 TCP 通讯服务（自动重连/去重/三种解析模式）
- [FEAT-006] 新增: MES REST API（30 条端点，完整 CRUD + 追溯 + 统计）
- [FEAT-007] 新增: MES 前端管理页面（工单/工件追溯/缺陷分析/扫码器 4 标签页）
- [FEAT-008] 新增: Monitor 检测页 MES 信息条（实时工件状态 + 工单进度）
- [FEAT-009] 新增: 数据中心 MES 工单筛选
- [FEAT-010] 新增: 开发者模式（密码保护，控制多工位选项可见性）

## v2.2.1 (2026-04-02)
- [BUG-001] 修复: _just_settled 机制导致 NG 后所有后续周期无法计数
- [BUG-002] 修复: 替补步骤在新周期开始时被清空导致 NG
- [BUG-003] 修复: 切换项目后点"开始"不加载新模型导致无检测结果
- [BUG-004] 修复: 页面导航离开 Monitor 再返回后视频黑屏
- [SKILL-001~004] 更新: debug-source, debug-video, debug-frontend, modify-source
- [SKILL-005] 新增: update-release skill（更新发版全流程）
- [SKILL-006] 新增: tune-params skill（检测参数调优全流程）
- [TOOL-001] 新增: USB 摄像头后端测试脚本 (test_camera_backend.py)

### 补丁 v2.2.1a (2026-04-02)
- [BUG-005] 修复: OpenCV/NumPy ABI 不兼容导致推理崩溃 (opencv-contrib-python 降级到 4.10.0)
- [BUG-006] 修复: MSMF 摄像头后端资源竞争导致 can't grab frame
- [HOTFIX-001] hotfix.py v2.0.5 — RTSP H.265 + 摄像头 MSMF 优化 + generate_frames 崩溃防护
- [TOOL-002] 新增: Windows 一键补丁脚本 (patch_v2.2.1a.bat)

## v2.2.0
- 初始版本（本 changelog 体系建立前的版本）
