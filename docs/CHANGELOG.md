# Changelog

## v2.4.0 (2026-04-07)

## Bug 修复

- [BUG-001] TensorRT 模型切换后无检测框
  - 症状: 切换到 TensorRT FP16 模型后推理无任何检测框
  - 根因: `load_model` 优先读 `model.overrides['imgsz']`（默认 640），而 TensorRT 引擎实际输入为 1024，导致 `AssertionError: input size not equal to max model size`
  - 修复: 优先从 TensorRT 引擎 bindings / input_shape 读取实际 imgsz，再回退 overrides
  - 影响文件: backend/api/source.py

- [BUG-002] 视频重新开始检测后 FPS 飙升至 60+
  - 症状: 停止视频后再开始检测，FPS 从正常 30 飙到 60+，视频像快进
  - 根因: 高分辨率帧（2448×2048）推理耗时大，降低了帧处理速度形成隐式限速；优化后处理速度提升，视频文件以原始帧率回放
  - 修复: 在捕获线程中预缩小帧到模型 imgsz 供推理和录制使用，保留全分辨率给显示
  - 影响文件: backend/api/source.py

- [BUG-003] 第一步结算模式有两个触发点
  - 症状: first_step 结算模式下，"最后一步消失"也会触发结算，等同于 last_step 模式
  - 根因: `_check_events` 中"最后一步消失"结算逻辑未区分 settlement_mode
  - 修复: 在"最后一步消失"结算路径加 `if self.settlement_mode != 'first_step'` 条件
  - 影响文件: backend/api/source.py

- [BUG-004] 第一步结算后新周期丢失第一步
  - 症状: 第一步触发结算后，下一周期中第一步未被加入序列，被消失处理器以 duration=0 移出
  - 根因: 结算后 old_last_seen 未重置为 None，且 `_step_raw_start` 未回填 min_duration
  - 修复: 结算后 `old_last_seen = None` + `_step_raw_start[label] = current_time - first_min_dur`
  - 影响文件: backend/api/source.py

- [BUG-005] 自定义提示框计数器重启后清零
  - 症状: 每次重启后端，自定义 Toast 计数器归零，客户机上也会
  - 根因: `self.counters` 仅存在内存中，未持久化到数据库
  - 修复: 新增 `_persist_counters()` 方法，在事件触发、会话结束、检测停止时写回 `project.counters_config`
  - 影响文件: backend/api/source.py

## 功能变更

- [FEAT-001] 高分辨率推理性能优化
  - 需求: RTX4060 + TensorRT FP16 推理 2448×2048 帧时 CPU resize 成为瓶颈
  - 实现: 捕获线程预缩小帧到 imgsz 分辨率，推理和录制用小帧，显示保留原分辨率
  - 影响文件: backend/api/source.py

- [FEAT-002] 外部 MES 工单双向推送
  - 需求: 支持客户 MES 系统推送工单到本系统，以及本系统回传额外字段
  - 实现: `POST /mes/orders/receive` upsert 端点 + `PUT /mes/orders/{id}/extra-data` 字段更新
  - 影响文件: backend/api/mes.py, backend/services/work_order.py, frontend/src/api/mes.js, frontend/src/views/MES/OrderPanel.vue

- [FEAT-003] MES 适配器框架
  - 需求: 不同客户使用不同 MES 系统，需要可配置的通用接入层
  - 实现: `mes_gateway.py` 网关 + `mes_adapters/` 可插拔适配器 + 前端 GatewayPanel 配置页
  - 影响文件: backend/api/mes_gateway.py, backend/services/mes_gateway.py, backend/services/mes_adapters/, frontend/src/views/MES/GatewayPanel.vue, frontend/src/api/gateway.js

- [FEAT-004] 作业员管理系统
  - 需求: 记录每个检测会话和周期的操作员，支持登录/登出
  - 实现: 后端 operators API + 前端 Settings 页作业员管理 + Session/Cycle 关联 operator_id
  - 影响文件: backend/api/operators.py, frontend/src/api/operators.js, frontend/src/views/Settings/index.vue, backend/api/source.py, backend/models/models.py

- [FEAT-005] 工单管理页面增强
  - 需求: 工单页面需要支持来源显示、自动刷新、额外字段编辑
  - 实现: OrderPanel 增加来源列、产品编码列、自动刷新开关、extra_data 编辑对话框
  - 影响文件: frontend/src/views/MES/OrderPanel.vue, frontend/src/api/mes.js

- [FEAT-006] 授权更新工具
  - 需求: 离线客户机需要远程更换授权（永久→限时）
  - 实现: read_machine_id.bat（读取机器码）+ update_license.bat（GBK 编码，自动查找+备份+替换）
  - 影响文件: read_machine_id.bat, update_license.bat

## Skill 更新
- [SKILL-001] 更新: debug-source — 新增计数器持久化、第一步结算逻辑说明
- [SKILL-002] 更新: modify-source — 新增 _model_imgsz、预缩小帧机制说明
- [SKILL-003] 更新: debug-video — 新增高分辨率帧预缩小流程
- [SKILL-004] 更新: modify-api — 新增 MES 外部推送端点
- [SKILL-005] 更新: modify-frontend — 新增 OrderPanel extra_data 编辑
- [SKILL-006] 更新: api-sync — 新增 operators、mes_gateway 端点
- [SKILL-007] 更新: modify-model — 新增 operator_id 字段
- [SKILL-008] 新增: debug-mes — MES 子系统调试
- [SKILL-009] 新增: create-hotfix — 热补丁创建流程

## 已知问题
- 消失确认延迟（disappear_delay）默认 0 导致检测抖动被切碎，需用户手动设为 1.0s（待后续版本改为合理默认值）
- 去重间隔（max_interval）在消失处理器中未生效，仅在 is_new_appearance 判定中使用

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
