---
name: debug-video-archive
description: 诊断录像归档与媒体证据体系 (v3.53)：归档不触发/文件没搬走、文件名模板渲染错、spool 堆积重试不停、NG 关键帧不出图、证据包 zip 缺件、事件切片失败、FTP/SFTP/S3/HTTP 远端投递失败、凭据解密报错、时间窗不搬、限速不生效、回补重复副本、删源没删或误删担忧、archived_* 字段为空、video_archived 网关事件没推。当客户说"NG 录像没归档到网盘""归档一直失败""关键帧是黑的/没有""证据包里少文件""FTP 推不上去""密码改了还是连不上""夜间才搬怎么没动静"时使用。
---

# debug-video-archive: 录像归档与媒体证据体系诊断

> v3.53 落地。全链路：周期录像收尾 → 入队 → worker 匹配规则 → (切片/关键帧/sidecar/打包) → adapter 投递 → 台账 → 生态联动。
> **默认关**：没有启用的归档规则时零帧不抽、零文件不搬、零开销。

## 1. 文件地图

| 文件 | 职责 |
|---|---|
| `backend/models/archive_models.py` | `VideoArchiveRule` / `VideoArchiveLog` 两表（字段清单见 modify-model skill 1.10） |
| `backend/services/video_archive.py` | 引擎：规则缓存、任务队列+worker、spool 重放、时间窗、匹配、模板渲染、交付编排、删源、回补、手动证据包 |
| `backend/services/archive_media.py` | 媒体件：NG 关键帧 (save_ng_keyframe / save_keyframe_async / find_keyframe)、clip_tail 切片、build_evidence_zip |
| `backend/services/archive_adapters.py` | 目的地 adapter：local_dir/ftp/sftp/s3/http + 插件注册表 + ThrottledReader 限速 |
| `backend/services/archive_secrets.py` | dest_config 凭据 Fernet 加密/解密/打码/回传合并 |
| `backend/services/archive_ecosystem.py` | 归档成功联动：插件 hook + 网关事件 + sidecar 渲染 + archived_* 反查 |
| `backend/api/video_archive.py` | `/api/v1/export/video-archive/*` 路由（权限 `data.export`） |
| `backend/api/source_recording_api_mixin.py` | 触发点①：`stop_cycle_recording` → `_delayed_release` → `enqueue_cycle` |
| `backend/api/source_session_lifecycle_mixin.py` | 触发点②：`end_cycle` NG 定案后置 `_archive_ng_frame_pending`（keyframe_wanted 守门） |
| `backend/api/source_inference_loop_mixin.py` | 触发点③：推理循环同帧消费 pending → save_keyframe_async（画框/编码在独立线程） |
| `frontend/src/api/videoArchive.js` + `views/Data/components/VideoArchiveDialog.vue` | 前端配置弹窗（规则/状态/台账/证据包下载） |

关键磁盘落点（都在 DATA_DIR 下）：
- `video_archive_spool.jsonl` — 瞬态失败任务的断点续传池
- `recordings/keyframes/<YYYY-MM-DD>/cycle_<uuid>.jpg` — NG 关键帧（按约定路径反查，只查今天/昨天两目录）
- `archive_secret.key` — 本机 Fernet 密钥（0600，**换机/删掉后旧密文不可解，需重填凭据**；已 gitignore）

## 2. 症状 → 检查顺序

### 2.1 「归档不触发 / 目标目录没文件」

1. `GET /export/video-archive/status` 看 `rules_enabled` / `queue_depth` / `spool_depth` / `last_error`。
2. 规则匹配三重过滤逐项对：`result_filter`（ng_only 时 OK 周期直接 skip 记台账）、`channel_filter`、`project_filter`。
3. **录像开关**：没开周期录像 (`record_cycle_video`) 就没有源文件，归档无从谈起。
4. 时间窗：`active_window` 配了夜间窗，白天任务全部 defer（status `deferred` 计数涨、任务回 spool 不耗重试预算）——这是设计不是故障。
5. worker 活着吗：`worker_alive`。它是 lazy 启动（首个任务 enqueue 时拉起），进程重启后 spool 由 `refresh_rules_cache`/首个任务重放。
6. 台账查失败原因：`GET /export/video-archive/logs?status=failed`。

### 2.2 「文件名不对 / 渲染出空名」

- 模板是 Jinja2，上下文 = `build_cycle_context` 全字段（见 debug-export skill）。**未定义变量渲染为空串不报错**（Jinja2 默认 Undefined 行为），所以 `{{ workpiece.serial_no }}` 在无 MES 场景是空的——模板要用 `| default(cycle.id, true)` 兜底（默认模板已带）。
- 渲染彻底失败（语法错）= 永久失败记台账，不重试。
- 重名策略只有 local_dir 完整支持（rename 加 `_1` 序号 / overwrite / skip）；**远端 adapter 一律覆盖语义**（UI 已注明）。

### 2.3 「NG 关键帧不出图 / 是黑的 / 没画框」

链路：end_cycle NG 定案 → `keyframe_wanted()`（模块级 bool，规则缓存刷新时更新）→ 置 `_archive_ng_frame_pending` → 推理循环下一帧消费 → 独立线程画框+JPEG。逐点排查：
1. 至少一条**启用**规则开了 `attach_keyframe`？没有就零帧不抽（守门设计）。
2. 建/改规则后 `refresh_rules_cache()` 会自动跑（API 层已挂），手改 DB 不会。
3. 检测停止的瞬间结算的 NG：pending 置位后推理循环可能已停 → 该周期无关键帧（可接受，找不到图时归档照走只是不带 jpg）。
4. 画框依赖 `self.drawer`（has-a 组件）+ detections 归一化 x/y/w/h——synthetic 与真模型输出同构。
5. 水印开关是规则级 `keyframe_watermark`，全局取"任一启用规则要水印"。
6. **血泪**：cv2.imwrite 按扩展名选编码器，临时名必须 `.tmp.jpg` 结尾（BUG-002，勿改回 `.tmp`）。

### 2.4 「证据包 zip 缺件」

- 包内应有：主件 mp4（或切片）+ `<basename>.jpg`（找得到关键帧才有）+ `<basename>.<fmt>`（配了 sidecar 才有）+ `<basename>_meta.json`（恒有）。
- 缺 jpg：见 2.3；跨午夜注意 find_keyframe 只查今天/昨天两个日期目录。
- 缺 sidecar：模板被删/渲染失败**不推翻录像归档**（打日志继续），检查 export_templates 里模板还在不在。
- 手动批量包（evidence-pack 端点）结构不同：`cycle_<id>_<OK|NG>/video.mp4 + keyframe.jpg + meta.json` 逐周期一目录，缺录像的周期尽力打包不报错。

### 2.5 「事件切片失败 / 切出来跟原片一样长」

- `clip_tail` 用 `ffmpeg -sseof -N -i src -c copy`：**copy 模式起点对齐到最近关键帧**，录像 GOP 大（如整段只有一个关键帧）时切出来≈整段——不是 bug，是"宁多不少"。周期录像由 FFmpegRecorder 编码，GOP 正常。
- 切片抛异常 → 回退整段归档（证据宁全勿缺），台账仍 success。
- ffmpeg 路径走 `get_cached_ffmpeg_path()`（与录像转码同源）。

### 2.6 「远端投递失败」

先分清两类（台账 error 和 status API `last_error` 都有原文）：
- **PermanentDeliveryError**（配置/凭据/依赖类）：不重试直接记败。常见：缺 host/bucket/url、`sftp 需要 paramiko`、`s3 需要 boto3`、HTTP 4xx、插件 adapter 未注册（插件没激活或没调 register_archive_adapter）。
- **瞬态**（网络断/超时/HTTP 5xx）：回 spool 按预算重试（`_MAX_ATTEMPTS`=8），超限记败丢弃。

凭据相关：
- 「凭据解密失败 (密钥文件可能已更换)」= `archive_secret.key` 丢了/换机——重新在 UI 填一遍密码即可（会用新钥加密）。
- 前端回显 `******` 是打码不是真值；回传 `******` 保留库里旧密文（merge_masked_config 语义）。改密码必须真输入新值。
- 库里敏感字段是 `enc:` 前缀密文（encrypt_config 幂等，双重加密不会发生）。

### 2.7 「限速/时间窗/回补/删源」

- 限速：`bandwidth_limit_kbps` 走 256KB 块间 sleep（`_iter_file_throttled`），local/ftp/sftp/http 生效；s3 因 boto3 需 bytes 整读，限速仅在读侧节流。
- 时间窗跨午夜语义：`22:00-06:00` = 22 点到次日 6 点；起止相同 = 全天。判定函数 `_in_window`。**指定 rule_id 的 test-run 无视时间窗**（方便白天调试夜间规则）。
- 回补：`POST /backfill` 只入队"源文件还在盘上"的周期，`only_rule_id` 限定单规则；**rename 策略下重复回补会出 `_1` 副本**——回补目标端建议 skip/overwrite。
- 删源 `delete_source_after`：所有命中规则全 success + 本地类目的地字节数一致才删，删后回写 `cycle.video_path=None`（数据中心回放入口随之消失=分层语义）。任一规则失败绝不删。

### 2.8 「archived_* 字段是空的 / 网关没推」

- `cycle.archived_video_path/archived_at/archive_status` 从 `video_archive_logs` 按 cycle_id 反查**最近一次**记录（`archive_ecosystem.latest_archive_info`）——从未归档 = 三字段 None，正常。
- `video_archived` 网关事件：MES 网关连接要**订阅了该事件**才推（GatewayPanel 事件下拉有"录像归档完成"）；走 dispatch_gateway 异步执行器 + gateway_spool（不变量 15，不阻塞归档 worker）。
- 插件 hook `video_archived`（observe 型，返回值丢弃）；插件自定义目的地走 `PluginHost.register_archive_adapter`（capability `runtime.archive_adapter`，规则 dest_type 填 `plugin:<name>`，进程级注册插件启停要重启）。

## 3. 不变量（改这个子系统前先背）

1. **无启用规则 = 零开销**：`keyframe_wanted()` / `enqueue_cycle` 的守门不许绕过；新增能力先想"默认关时付什么代价"。
2. **归档失败绝不影响检测/录像主链路**：所有触发点 (recording/lifecycle/inference mixin) 的调用都包 try 并隔离。
3. **worker 线程里不许做会永久阻塞的事**：adapter 都带超时；插件 adapter 阻塞只影响归档不影响检测（文档已告知插件作者）。
4. **删源三重门**（全规则 success + 字节校验 + 回写 video_path）缺一不可。
5. **敏感字段落库必须密文**：任何新 adapter 的密码类 key 记得进 `archive_secrets.SENSITIVE_KEYS`。
6. **目录护栏**：`_DEST_DIR_BLACKLIST`（系统目录/DATA_DIR 内）在 API 层拒绝，engine 层不再查——别在 engine 加第二道挡了合法 UNC。
7. 新 ORM 文件三处注册（main.py / tests/conftest.py / alembic/env.py）——BUG-003 血泪。

## 4. 测试地图

| 层 | 文件 | 覆盖 |
|---|---|---|
| 单测 | `tests/test_video_archive.py`（25） | 一期：匹配/模板/原子落位/重名/spool/护栏/零开销守门 |
| 单测 | `tests/test_video_archive_evidence.py`（43） | 二~四期：凭据/时间窗/adapter/限速/关键帧/sidecar/证据包/真 ffmpeg 切片/archived_*/联动/回补/删源/API 打码 |
| e2e | `tests/e2e_browser/test_video_archive_rules.py`（4） | UI CRUD/护栏弹错/远端打码/证据开关落库双向 |
| UAT | `tests/uat/uat_video_archive_phase1.py` / `phase234.py` | 可见浏览器全链路（synthetic 真周期/NG 抽帧进 zip/手动导出下载） |

单测直调 `va._run_task` 前要 `va._stop_event.clear()`（stop_worker 置的停止位会让等待循环秒退返回 retry——测试踩过）。
