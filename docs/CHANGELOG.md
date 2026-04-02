# Changelog

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
