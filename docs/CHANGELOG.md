# Changelog

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
