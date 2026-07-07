# 多模型 + ROI 联动使用指南 (feat/multi-model-roi-link)

> ⚠️ **本文已归档**：该功能分支已并入主线（多模型 slot 路径见
> `backend/api/source_project_config_apply.py`，端到端回归见 `tests/test_e2e_multi_model.py`），
> `feat/multi-model-roi-link` 分支已不存在。现状以代码为准，本文仅供决策考古。

> 适用分支：`feat/multi-model-roi-link`（已合并删除）
> 主线版本基线：v3.6.0
> 最后更新：2026-05-08

---

## 一、能干什么

本特性引入 **N 模型 + ROI 联动** 架构，单工位可以同时跑最多 4 个模型，每个模型有独立配置：

- 主模型按整画面跑（保持老链路完全等价）
- 副模型可在指定 ROI 多边形内跑、按"每帧 / 每 N 帧 / 按事件"调度
- 每个模型独立 `conf` / `iou` / `class_filter` / `display_color`
- 检测框按各自颜色渲染、ROI 多边形可视化叠加

典型场景：

| 场景 | 主模型 | 副模型 |
|---|---|---|
| 员工 SOP + 产品状态检测 | SOP 步骤识别（全画面） | 产品正反放（右下角 ROI） |
| 三模型 + 多通道 | 工位 1: SOP + 副模型 | 工位 2: 第三模型独立运行 |
| 主 + 分割辅助 | 整画面物体检测 | ROI 内分割副模型出 mask |

GPU 内存约束：3050（4-5 GB）建议最多 2 个 slot，跨通道 warmup 已串行化防 OOM。

---

## 二、给谁用

- **现场操作员**：去 `Project` 页"附加模型 (多模型 ROI)"卡片配置；启动检测时前端会自动按 `pipeline_config.models[]` 构造多模型 payload
- **二次开发 / 集成方**：直接调 `/api/v1/source/detection/start` 带 `models` 数组（schema 见第六节）
- **现有客户（单模型项目）**：**完全无感**，老 UI / 老 API / 老配置文件 100% 行为等价

---

## 三、架构 8 步全图

| 步 | 模块 | 责任 | commit |
|---|---|---|---|
| 1 | `source_inference_router.py` | `InferenceRouter` 调度器 + `ModelInstance` 数据类 + `gpu_lock` / `warmup_lock` | fc3a4e4 |
| 2 | `source_state_init.py` | VSM `_init_components` 接 router，初始化 default `main` slot 空壳 | 068283c |
| 3 | `source_model_load_mixin.py` | `load_model_into_slot()` 多 slot 加载 + `main↔host` 双向同步桥 + `release_all_models()` | 2609365 |
| 4 | `source_detect_runners_mixin.py` | `_detect_only` / `_detect_and_track` / `_detect_segment` 接 `mi=` 参数 + ROI 黑底 + class_filter + display_color 注入 | 6cf2e39 |
| 5 | `source_inference_loop_mixin.py` | 主推理 loop 走 `router.schedule_models_for_frame` + 多模型 detections 合并 + per-model `tick_fps` | 5c575af |
| 6 | `source_routes.py` + `source_project_config_apply.py` | `DetectionStartRequest.models[]` Pydantic + `apply_models_config` 解析 + `/detection/results.models[]` 透出 | 3f18082 |
| 7 | `channel_manager.py` | 全局 `_global_warmup_lock` 跨通道共享 + `load_model_for_channel` 双签名 + `release_all_models_for_channel` | 0c0c21e |
| 8 | `Project/index.vue` + `Monitor/index.vue` + `api/detection.js` | 多模型配置 UI + ROI 编辑器复用 + 启动时构造 payload + 检测框 display_color | 11d652f, b46030e |

---

## 四、前端使用流程

### 4.1 配置阶段（`/project`）

1. 进 `Project/<项目>` 页 → "基础设置" Tab
2. **主模型**：在"模型配置"卡片选模型（不变）
3. **副模型**：在"附加模型 (多模型 ROI)"卡片：
   - 点 `添加副模型`（最多 4 个）
   - 填 `slot 名`（默认 `aux/aux2/...`，建议改成业务名如 `tray`）
   - 点 `选择` 选副模型
   - 设 `置信度` / `IoU` / `优先级`（数字大 = 先跑，主模型固定 100）
   - 选 `检测频率`：
     - `每帧`：和主模型同步跑，最贵
     - `间隔 N 帧`：每 N 帧跑一次，节省 GPU（默认 5）
     - `按事件`：等待业务触发（高级用法，预留）
   - 设 `ROI 区域`：点 `设置区域` 在快照上画多边形（点第一个点闭合）
   - `类别白名单`：留空 = 模型全部类别；填了只显示这些
   - 选 `颜色`：检测框 / ROI 边框颜色
4. 保存项目（右上角）

### 4.2 运行阶段（`/monitor`）

1. 选这个项目，开始检测
2. 主流程不变；同时副模型按各自频率跑
3. 顶部状态栏（≥2 slot 时）显示 per-model 性能：
   ```
   [● main 25fps] [● tray 5fps]
   ```
4. 画面中：
   - 主模型框按 NG/OK 配色（不变）
   - 副模型框按其 `display_color`（业务一眼区分）
   - ROI 多边形虚线叠加（各自颜色，便于调试）

### 4.3 跨工位场景

每个工位（channel）的 VSM 完全独立，但**共享一个全局 warmup_lock**：

- 工位 A `/detection/start` 加载主+副模型
- 工位 B 同时 `/detection/start` 加载主+副模型
- 后端：A 的所有 slot warmup 串行 → 释放锁 → B 的所有 slot warmup 串行
- 防止 4 个模型同时初始化 TensorRT context 引爆 4 GB VRAM

---

## 五、后端 API 规范

### 5.1 `POST /api/v1/source/detection/start?channel=N`

**老 payload（向后 100% 兼容）**：

```json
{ "model_path": "/data/models/main.pt", "conf": 0.25, "iou": 0.45 }
```

**新 payload（多模型）**：

```json
{
  "models": [
    {
      "name": "main",
      "model_path": "/data/models/main.pt",
      "conf": 0.3,
      "iou": 0.5,
      "display_color": "#10b981",
      "priority": 100
    },
    {
      "name": "tray",
      "model_path": "/data/models/tray.pt",
      "conf": 0.5,
      "iou": 0.5,
      "roi": [[0.7, 0.7], [1.0, 0.7], [1.0, 1.0], [0.7, 1.0]],
      "schedule": { "type": "every_n_frames", "n": 5, "events": [] },
      "class_filter": ["tray_normal", "tray_side"],
      "priority": 50,
      "display_color": "#f59e0b",
      "use_half": false,
      "original_pt_path": null
    }
  ]
}
```

字段语义（详见 `backend/api/source_routes.py:ModelSpec`）：

| 字段 | 必填 | 说明 |
|---|---|---|
| `name` | ✅ | slot 标识；`main` 为主模型，其他任意字符串为副模型 |
| `model_path` | ✅ | 磁盘路径（`.pt` / `.engine` / `.onnx` / 等） |
| `conf` | ❌ | 置信度阈值，默认 0.25 |
| `iou` | ❌ | NMS IoU 阈值，默认 0.45 |
| `roi` | ❌ | 归一化多边形顶点 `[[x,y],...]`，至少 3 个；为空 = 全画面 |
| `schedule` | ❌ | `{ type: 'every_frame'\|'every_n_frames'\|'on_event', n: int, events: [name] }` |
| `class_filter` | ❌ | 类别白名单字符串列表；为空 = 全部 |
| `priority` | ❌ | 整数，调度优先级（高先跑），主默认 100，副默认 50 |
| `display_color` | ❌ | hex 颜色（前端检测框 / ROI 描边） |
| `use_half` | ❌ | FP16 推理（仅 PyTorch 原生模型生效） |

### 5.2 `GET /api/v1/source/detection/results?channel=N`

新增字段 `models[]`（单模型时仍含 main 一项）：

```json
{
  "...原有字段...": "...",
  "models": [
    {
      "name": "main",
      "model_path": "/data/models/main.pt",
      "model_task": "detect",
      "model_loaded": true,
      "conf": 0.3,
      "iou": 0.5,
      "imgsz": 640,
      "use_half": false,
      "device": "cuda:0",
      "roi": null,
      "schedule": { "type": "every_frame", "n": 1, "events": [] },
      "class_filter": null,
      "priority": 100,
      "display_color": "#10b981",
      "fps_inference": 25,
      "latency": 38
    },
    { "name": "tray", "...": "..." }
  ]
}
```

每个 detection 在原有字段基础上注入 `model_name` + `display_color`，前端按此着色。

### 5.3 `POST /api/v1/source/gpu/set`

切设备时自动遍历重载所有已加载的 slot：

```json
// Request
{ "device": "cuda:1" }

// Response (多模型场景)
{
  "status": "success",
  "message": "已切换 2 个模型到 NVIDIA RTX 3050",
  "device": "cuda:1",
  "current_device_info": { "name": "NVIDIA RTX 3050", "device": "cuda:1" },
  "reloaded_models": ["main", "tray"]
}
```

### 5.4 `POST /api/v1/projects` / `POST /api/v1/source/detection/set-project`

`pipeline_config.models[]` 字段可持久化到 DB；`apply_project_config` 解析时**只写 mi 配置**，不触发 `load_model`（耗时副作用集中在 `/detection/start`）。

切项目时配置外的旧副 slot 自动释放，`main` 永远保留。

---

## 六、向后兼容承诺

| 用例 | 行为 |
|---|---|
| 单模型项目（无 `pipeline_config.models`） | 与 Step 8 之前 100% 等价（路由、UI、性能、字段） |
| 老前端版本调新后端 | 老 payload 走老路径，`/detection/results` 多 1 个 `models` 字段（老前端忽略即可） |
| 新前端调老后端 | `/detection/start` 老格式照旧，`models[]` 字段不被识别会被 Pydantic 拒绝 → 前端会自动只用单模型分支 |
| 数据库老项目升级 | 无 `pipeline_config.models` 时 `extra_models = []`，UI 显示"暂无副模型" |

**硬性回归保证**：每步原子 commit 都有单测覆盖，全分支累计 **176 passed, 0 regression**。

---

## 七、性能 / 资源占用参考

环境：单 RTX 3050 (4 GB)、PyTorch 2.x、`use_half=true`、imgsz=640

| 配置 | GPU 显存 | 主 fps | 副 fps | 备注 |
|---|---|---|---|---|
| 单模型 (主) | ~1.2 GB | 25 | — | 基线 |
| 主 + 副 (`every_frame`) | ~2.5 GB | 25 | 25 | 副完全跟随 |
| 主 + 副 (`every_n_frames`, n=5) | ~2.5 GB | 25 | 5 | 推荐配置 |
| 主 + 副 + 副 (`every_n_frames`, n=10) | ~3.7 GB | 25 | 2.5 | 极限边缘，TRT FP16 可降 30% |

**OOM 风险阈值**：
- 单卡 4 GB：**≤2 个 slot**
- 单卡 6 GB：**≤3 个 slot**
- 8 GB+：4 个 slot 上限

**warmup_lock 保护场景**：
- 多通道（4 工位）× 多模型（每通道 2 slot）= 8 个模型加载
- 没有 warmup_lock：8 个 TRT context 同时申请 workspace → 必 OOM
- 有 warmup_lock：串行申请，每次申请完释放，峰值 = 单个最大模型

---

## 八、调试 / 排错

### 8.1 副模型不出框

依次检查：

1. `Project` 页副 slot 是否选了模型？`model_id` 不为 null？
2. 启动检测时 ElMessage 有无"副模型路径解析失败"？
3. `/detection/results.models[]` 中该 slot 的 `model_loaded` 是否 true？
4. ROI 设置范围是否覆盖目标？画面中虚线框看得见吗？
5. `class_filter` 是否过严（留空更稳）
6. `schedule_n` 是否过大（实测 fps 看起来偏低）

### 8.2 GPU OOM

检查后端日志：

- `[资源释放] [tray] 开始释放模型资源...` 正常释放
- `RuntimeError: CUDA out of memory` 多半是 slot 太多 / 模型太大
- 应对：减少 slot / 切 TensorRT FP16 / 降 imgsz / `use_half=true`

### 8.3 多通道 warmup 卡死

`backend/api/channel_manager.py` 的 `_global_warmup_lock` 死锁的可能场景：

- 某个通道 `load_model_into_slot` 抛异常但未释放锁 → 不可能（用 `with` 上下文管理器）
- 真要查就看 `warmup_lock._owner` 调试栈

### 8.4 老项目突然无法启动检测

- 看 ElMessage 是不是抛"加载模型失败"
- 后端日志看 `_warmup_model_cuda` AssertionError → imgsz 不匹配，多半 .engine 模型导出尺寸 ≠ 推理尺寸

---

## 九、后续路线（不在本特性范围）

- [ ] **副模型也支持模型格式选择**（当前默认 `pytorch_fp32`）
- [ ] **跨通道事件总线**：A 工位检测结果触发 B 工位副模型 `on_event` 调度（START_HERE.md 子方向 C，未做）
- [ ] **副模型 results 进 Data 页导出**：当前 detection 入了主流程统计，副模型 detection 仅 UI 渲染
- [ ] **MES 联动**：副模型检测结果入 `mes.workpiece.defects` 字段
- [ ] **Settings 页 GPU 切换 UI 透出 `reloaded_models`** 反馈

---

## 十、相关文档

- `START_HERE.md` — 任务接手人入口（本特性的需求来源）
- `AGENTS.md` § 八 — 架构不变量清单（请保持本特性的 8 步原子 commit 风格）
- `.claude/skills/modify-source/SKILL.md` — VSM 修改前必读
- `.claude/skills/debug-channel/SKILL.md` — 多通道 ChannelManager
- `.claude/skills/debug-detection/SKILL.md` — 推理路径
- `.claude/skills/modify-project-config/SKILL.md` — 配置字段全链路对齐

---

## 十一、commit history（feat/multi-model-roi-link 分支）

```
b46030e Step 8 续: 端到端集成测试 + /gpu/set NoneType 兜底修复
11d652f Step 8: 前端多模型 UI + 启动调用 + 检测框配色
0c0c21e Step 7: ChannelManager 全局 warmup_lock + 双签名
3f18082 Step 6: source_routes + project_config_apply 多模型路由
5c575af Step 5: InferenceLoopMixin 走 router.dispatch
6cf2e39 Step 4: DetectRunnersMixin 接 mi + ROI + display_color
2609365 Step 3: ModelLoadMixin 多模型加载 + 双向同步桥
068283c Step 2: _init_components 接 router (default main slot)
fc3a4e4 Step 1: InferenceRouter 骨架 + Schedule + ModelInstance
```

测试覆盖（`tests/test_*_multi*.py` + `tests/test_e2e_multi_model.py`）：**176 passed, 0 regression**。
