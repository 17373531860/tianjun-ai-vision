# Changelog

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
