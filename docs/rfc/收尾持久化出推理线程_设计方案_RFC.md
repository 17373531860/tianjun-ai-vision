# RFC: 周期收尾持久化出推理线程（决策/落库分离）

- 状态: **已落地（2026-07-15, 目标版本 v3.38）** — 实现与本文 3.2 的差异见文末"七、落地实录"
- 起因: 2026-07 川南"检测框冻结"事故链（v3.38 两刀之后的第三阶段根治）
- 前置已落地: v3.38 第一刀（MES 网关推送不握写锁跨网络 I/O）+ 第二刀（自动清理分批提交、文件 I/O 出事务）+ 收尾链慢提交留证埋点

## 一、问题

推理线程在步骤完成 / 周期结算时**同步写数据库**（步骤记录插入、周期行更新提交）。
SQLite 单写者模型下，任何别处持有写锁的长事务都会把推理线程堵到 busy_timeout
上限（15s），表现为：检测框冻结、后续类别漏检、周期误判 NG。

v3.38 两刀已消灭进程内全部**已知**长事务（网关推送、自动清理），并加了 >1s 慢提交
留证。但架构层面"推理线程做磁盘持久化"这个原罪仍在——未来任何新代码、第三方
工具（备份、杀毒）造成的写锁/文件系统停顿，仍会直接打在检测节奏上。

## 二、目标与非目标

**目标**：推理线程从"帧到帧"路径中彻底移除数据库 I/O；任意时长的写锁竞争最多
延迟数据落库，绝不延迟检测判定与画面。

**非目标**：不改变判定语义（5 种逻辑模式 × 4 种结算模式的行为一字不变）；
不改变对外 API 契约；不做 PG 迁移（另有专项）。

## 三、设计：决策与持久化分离

### 3.1 原则

- **决策留在推理线程**（内存状态机原地不动）：步骤判定、周期开始/结束的全部
  内存状态变更（current_cycle_steps、屏蔽集合、计数器、PT 账本、容器清理……）
  仍在帧循环内同步完成——这些是下一帧判定的输入，不能异步。
- **持久化移到每通道一条 FIFO 落库线程**：步骤记录插入、周期行创建/更新、
  录像记录回写，全部封装为"落库作业"（纯 dict 载荷，不携带 ORM 对象）入队。

### 3.2 关键改造点

1. **周期句柄改用 uuid**：现在 `current_cycle_id`（DB 自增主键）是内存状态机与
   持久层的耦合点（start_cycle 必须先写库拿 id）。改为内存侧只持有
   `current_cycle_uuid`，DB 自增 id 由落库线程在处理"周期开始"作业时生成，
   并维护 uuid→id 映射供后续作业（步骤插入的外键、MES hook 的 cycle_id）解析。
2. **作业顺序即事务顺序**：同通道作业严格 FIFO（周期开始 → 步骤 N → 周期结束），
   落库线程串行消费，天然保证外键与时序一致。
3. **hook 触发点后移**：`on_cycle_end`（MES）、cycle_end 插件 hook、三个
   coordinator（工位组/串行流水线/包装）依赖"周期行已提交"，全部改由落库线程在
   周期结束作业 commit 之后触发（MES hook 本就异步队列，语义不变；coordinator
   需评估跨通道联动时序，见风险 R3）。
4. **背压与丢失保护**：队列有界（如 1000）；满则阻塞落库线程上游？不——推理线程
   绝不阻塞，改为溢出落盘（复用 mes_hooks 的 spill 机制思路）+ 告警。
5. **优雅关停**：stop_detection / 进程退出时 flush 队列（有超时上限），保证已判定
   周期不丢。

### 3.3 读路径

- `get_detection_results` 等读接口读的是内存状态，不受影响。
- Data 页查询最近周期可能晚看到几百毫秒（落库延迟），可接受。
- 需盘点**同步读回**的点：end_cycle 里 `db.query(DetectionCycle)` 读回 start_time
  算 duration → 改为内存记录周期开始时间（已有 cycle_start_time）。

## 四、风险清单（立项时逐条钉死）

| # | 风险 | 处置思路 |
|---|---|---|
| R1 | uuid→id 映射断链（落库线程崩溃/重启） | 落库线程带自愈重建（按 uuid 查库）；作业幂等设计 |
| R2 | 5×4 模式矩阵行为漂移 | 判定逻辑零改动；全矩阵 BDD + synthetic 剧本回归为发版门禁 |
| R3 | coordinator 跨通道联动时序（A 站结算驱动 B 站 override）延迟放大 | 联动决策所需字段全部走内存快照传递，不等落库 |
| R4 | 队列溢出/关停丢数据 | 溢出落盘 + 启动回放；关停 flush 带超时与留证 |
| R5 | 手动结算/强制结案等 API 路径与落库竞态 | 这些路径经 channel manager 调 VSM，与帧循环同锁（_settle_lock），入队点一致 |

## 五、工作量与排期建议

- 后端改造 + 单测: 2~3 天（session_lifecycle mixin 拆分、落库线程、映射）
- 全矩阵回归 + 活体实验（锁注入 + 死端点 + 清理并发三合一）: 1~2 天
- 建议作为独立小版本（如 v3.39）单独发，不与业务功能混版

## 六、当下防线（在本 RFC 落地前）

- 网关/清理两刀已消灭已知长事务；
- 收尾链 >1s 慢提交自动留证（调试中心 backend.session 类别 + 后端控制台），
  现场若再冻结，日志直接指认持锁方；
- 客户侧规避：外部推送保持指向在线端点，或开启"MES 异步推送"。

## 七、落地实录（2026-07-15）

**新组件**：`backend/api/source_persist_worker.py` — 每通道一条 FIFO 落库线程
（懒创建守护线程，队列上限 2000；单作业异常隔离；`flush()` 用哨兵作业等排空；
队列打满退化为调用方同步执行保数据；`TIANJUN_SYNC_PERSIST=1` 环境变量一键回
退全同步模式）。

**进落库线程的写库点**（全部在 `source_session_lifecycle_mixin.py` +
`source_recording_api_mixin.py`，闭包只捕获值快照）：

| 作业 | 内容 |
|---|---|
| cycle_start | 建周期行 + 回填上一周期间隔 + 提交后回填 `current_cycle_id` + MES on_cycle_start + cycle_start 插件 hook + 串行流水线 on_cycle_started |
| step#uuid | StepRecord 插入 + 前一步 interval 回写 + step_change 插件 hook（含 warn 缓存回写） |
| cycle_video / step_video | 录像元数据 VideoClip 插入 + 周期行 video_id 回写 |
| reconcile | 步骤对账（FIFO 排在全部 step 作业后，读到的必是完整集） |
| cycle_end | 周期行结果更新 + 录像 OK/NG 回写 + MES on_cycle_end + 周期性动作 + cycle_end 插件 hook + 三协调器 + 扫码器恢复/联动（保持原相对顺序） |
| discard | 空周期作废（删 StepRecord + DetectionCycle 行） |

**与 3.2 设计的差异**：
- 不维护 uuid→id 内存映射表——作业内直接按 `cycle_uuid` 查行（uuid 有唯一索引，
  查一次 <1ms），更简单且天然自愈（R1 消解）。
- "周期进行中"守门全面从 `current_cycle_id` 改为 `current_cycle_uuid`
  （end_cycle / record_step / 容器 settle_dedup / checklist / NG 补做 gate /
  scan_pair 建行守门 / main.py 关机步骤 / custom_mix cycle_active）。id 仅做
  展示与关联，由 cycle_start 作业提交后回填。
- 背压策略改为"满则调用方同步执行"（不做溢出落盘）：退化行为 = 老版本行为，
  数据零丢失，代价是退化窗口内推理线程可能再次等锁（打日志 + 调试中心留证）。
- `end_session` 统计前 `_persist.flush(timeout=15)` 等本通道队列排空，session
  级统计不缺账。

**验证**（详见 `tests/uat/uat_20260715_persist_offload_lock_ab.py`）：
- 锁注入活体 A/B：第三方连接 `BEGIN IMMEDIATE` 握写锁 8s——v3.37 基线推理心跳
  冻结 8.06s（复现川南症状）；本 RFC 后最大间隔 0.86s（即剧本节拍，零冻结），
  放锁后落库线程数秒内补齐全部积压数据。
- 新单测 `tests/test_persist_worker.py`（FIFO/异常隔离/flush/同步回退/满队退化）。
- 全量回归：与 v3.37 基线批跑失败集完全一致（32 个已知"批跑串污染"预存项，
  单独批次全绿），零新增失败。
