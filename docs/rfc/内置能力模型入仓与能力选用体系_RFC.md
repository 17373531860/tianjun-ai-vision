# RFC: 内置能力模型入仓与能力型模型选用体系

- 状态: **已实施**（2026-09-13，主作者拍板一至三期合并一次做完；VLM 也入仓）
- 日期: 2026-09-13
- 起因: 主作者对 2026-09 AI 能力批次（OCR/异常/VLM/朝向 +「AI 能力试用」tab）的方向性反馈：
  1. 「AI 能力试用」tab 是孤岛橱窗——能力不在模型仓库、不可管理、不可替换、与项目/工位无关联；
  2. 内置模型藏在 `backend/data/models/` + 环境变量里，用户看不见摸不着（换授权权重要靠替换文件）；
  3. 模型选用时只有 detect/segment 两种人格——YOLO 常规检测/分割之外的模型类型（姿态/头姿/OCR/异常……）
     被选为主模型或副模型时，应当带来各自的特性与价值，而不是没入口或走旁门。

## 一、目标形态（北极星）

- **模型仓库 = 唯一模型事实源**：用户上传模型、出厂内置能力模型、训练平台下发模型
  同仓分区管理，统一版本 / 替换 / 试用 / 授权信息。
- **每个模型有 capability（能力类型）**：`detect` / `segment` / `pose` / `headpose` /
  `ocr` / `anomaly` / …。选用点按 capability 呈现专属配置卡与价值说明。
- **项目选模型 = 主模型 + 能力挂件**：主模型驱动检测状态机（现状不变）；副模型槽位
  升级为"能力挂件"——按 capability 注入专属运行时行为：
  - `pose` 挂件 → 骨架叠加 + 人体朝向注入（facing_dwell 的 'facing' 字段供给源）
  - `headpose` 挂件 → 朝向精化（叠加在 pose 之上，远小目标可关）
  - `ocr` 挂件 → ROI 区域读数进事件/导出字段（读控制屏数值类需求）
  - `anomaly` 挂件 → 合格品记忆库比对
- **试用不再是独立 tab**：模型仓库每行一个「试一试」动作（通用面板：传图 / 取工位当前帧
  → 按 capability 渲染结果：检测框 / 骨架 / 朝向罗盘 / 文本 / 异常热图）。现有
  `/ocr/*`、`/orientation/*`、`/anomaly/*` 的 estimate / read 端点原样复用，只换前端入口。

## 二、分层设计

### 1. 数据层（migration m00xx）

`models` 表新增：

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `capability` | String(50) | `'detect'` | 能力类型枚举（老数据全部回填 detect，零差异） |
| `builtin` | Boolean | `False` | 出厂内置行（禁删，可停用；升级 seed 幂等） |

- 内置模型开机 seed：按 `(capability, builtin=True)` 幂等 upsert，`file_path` 指向
  `backend/data/models/` 出厂文件；文件缺失 → 不建假行 + 状态标不可用（v3.51.4
  交付审计教训：写了代码 ≠ 交付了文件，seed 必须探测真身）。
- 首批内置行：通用检人（yolo11n，随包）、人体朝向 YOLO11-pose（已随包）、头姿精化
  6DRepNet360（90MB 已定进包；`meta` 记授权占位状态，licensed 权重到位同名热替换）、
  OCR / 异常检测引擎权重（按现有 ocr_engine / anomaly_engine 落点收编）。
- **替换语义**：上传新权重 → 「绑定为该能力当前权重」→ 引擎热重载（见引擎层）。
  内置行本体不动，绑定关系存 SystemConfig KV（`capability_binding.<capability>`），
  升级安装包覆盖出厂文件不丢用户绑定。
- VLM **不入仓**：它是外部 API/大模型配置不是权重文件，留在系统设置。

### 2. 引擎层

内置引擎（person_orientation / ocr_engine / anomaly_engine）权重解析顺序统一为：

```
env 显式指定（开发调试） > 模型仓库能力绑定（用户可换） > 出厂默认文件
```

各引擎补 `reload()` 钩子（绑定变更时调用；朝向引擎已有 release() 可复用）。

### 3. 运行时层（多模型框架扩容）

- `ModelInstance.model_task` 扩枚举：`detect | segment | pose | headpose | ocr | anomaly`。
- `source_inference_router` 按 capability 选 runner：
  - `pose` runner：产出关键点 → 喂 person_orientation 注入链（`_region_inject_facing`
    现在自己起 singleton 跑姿态；改为优先消费 pose 挂件的现成关键点，无挂件时回落
    singleton——蒸镀点检一期只需此项 + headpose）；
  - `ocr` / `anomaly` runner：二期，按 ROI/调度节流跑，结果进事件与导出字段。
- 挂件默认无 → 全部现状行为零差异（不变量思维：不加挂件的项目一行代码路径都不变）。

### 4. 前端

- **模型仓库页改版**：三分区（我的模型 / 内置能力 / 平台下发——复用 v3.47 source 徽标
  样式）+ capability 徽标 + 每行「试一试」抽屉 + 内置行「更换权重」入口。
- **项目配置页**：副模型选择器按 capability 分组；选中 pose/ocr 等挂件即出专属参数卡
  （骨架样式 / 朝向注入开关 / OCR 区域与频率……）。
- **「AI 能力试用」tab 下线**：能力卡迁入模型仓库试用抽屉后即删（2026-09-13 主作者
  「不留尾巴」指令提前执行——原计划保留一版跳转提示）。旧页三块管理功能全量迁入抽屉：
  记忆库管理（建库/阈值/删库）、VLM 连接配置、各能力「读通道当前画面」试用；
  内置卡补引擎可用性标；路由 `/ai-tools` 与左侧菜单项删除。

## 三、与蒸镀点检项目的关系

蒸镀点检在此体系上的配置 = 主模型选内置「通用检人」+ 能力挂件选「人体朝向」（主码流
条件下开 headpose 精化）+ region_events 六条规则——**零特例代码**，且客户在模型页能看到
自己用的是什么模型、能自己换授权权重。演示项目已验证的两条工程结论
（steps_config 需含模型类别行 / 朝向层人体 ≥180px）原样适用。

## 四、分期与工作量

| 期 | 内容 | 估时 |
|---|---|---|
| 一期 | 迁移 + seed + capability/builtin 字段 + 绑定 KV + 引擎解析顺序 + 模型页三分区与试用抽屉 | 2-3 天 |
| 二期 | pose/headpose 能力挂件（router 扩 task + 朝向注入消费挂件）+ 项目页挂件选择器 | 1-2 天 |
| 三期 | ocr/anomaly 挂件 + 试用 tab 下线 + 手册/skill 同步 | 后续 |

## 五、风险与不变量对照

- models 表加列 → 必须走版本化迁移（不变量 8），老库升级回填默认值零差异；
- 内置行与用户重名/误删：builtin 行禁删（API 层 403 + 前端隐藏删除钮）；
- CI 打包：出厂权重必须进 extraResources 并有缺失红灯（v3.51.4 复盘规矩，
  yolo11n-pose 已豁免 .gitignore，headpose 90MB 与 yolo11n 需同步核对打包清单）；
- 多 GPU/多工位：pose 挂件每通道独立实例遵循 ChannelManager 隔离（不变量 4 的清理链
  对挂件同样生效）；
- 平台下发（yolovision）模型默认 capability=detect，互连协议后续再扩能力字段。

## 六、实施纪要（2026-09-13）

- **数据层**：`models` 表 + `capability`/`builtin`（迁移 m0011，SQLite/PG 双方言）；
  `services/builtin_models.py` 为能力目录唯一事实源（6 个内置行 seed 幂等，
  文件缺失不建假行；绑定 KV `capability_binding`，`resolve_capability_weight`
  异常安全恒不抛）。启动挂 `_seed_builtin_models`。
- **引擎层**：`person_orientation` 权重解析改 env > 仓绑定 > 出厂默认；
  `release()` 即热重载钩子（bind 端点调用）。OCR/anomaly/VLM 无独立权重不参与绑定。
- **API**：`GET /models/capabilities`（目录+绑定+引擎探针）、
  `POST /models/capabilities/{cap}/bind`、upload `capability` 表单字段、
  list `capability` 过滤、builtin DELETE 403。
- **运行时**：`pipeline_config.capability_attachments` + `CapabilityAttachmentsMixin`
  （pose/ocr/anomaly 挂件；后台线程 + busy 防堆积 + 事件回投推理线程；
  无挂件一个属性判断早退零开销），输出进 results `capability_outputs`。
  **挂件不进 YOLO ModelInstance 加载器**（能力引擎与多模型槽位解耦）。
- **前端**：模型页三分区（我的/平台下发/内置能力）+ 能力徽标 + 占位红标 +
  试一试抽屉（`CapabilityTryDrawer.vue`，复用 /ocr /orientation /anomaly /vlm 端点）+
  更换权重对话框；项目基础设置「能力挂件」卡；AI 能力试用独立页已删除
  （抽屉承接其全部功能：传图/通道帧试用 + 记忆库管理 + VLM 配置 + 罗盘/热力图）。
- **打包**：yolo11n.pt / yolo11n-pose.pt 随 git（.gitignore 豁免）；headpose.onnx
  (90MB) 挂 GitHub Release `model-assets-headpose-v1`，build.yml Windows 道
  下载 + sha256 校验 + 资源自检三件套红灯。授权版到位：上传绑定（推荐）或
  出 model-assets v2 换 build.yml 引用。
- **验证**：单测 28（seed 幂等/绑定解析顺序/悬空回落/API 校验/挂件解析）+
  CI e2e 5（三分区/抽屉/上传能力/挂件 UI→落库/迁移横幅）+ 全量回归 3159 过
  （4 失败均与本次无关：doc-ci 生成物已再生复绿，hands 快照两例是本机
  mediapipe 缺 framework 模块的环境问题）+ 真浏览器截图 + 真引擎 OCR 试用 +
  蒸镀点检项目迁新体系后 pose 挂件 `capability_outputs` 实时出角验证。
- **试用抽屉字段对齐**（真浏览器验证抓出的 bug）：OCR 端点返回 `results`
  不是 `texts`；朝向端点整幅当人框返回单结果 `found/yaw_deg`，不是 persons
  列表；异常热力图是 2D 数组非 base64（抽屉不渲染热图只给结论）。
