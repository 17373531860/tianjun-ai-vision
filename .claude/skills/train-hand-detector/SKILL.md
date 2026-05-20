---
name: train-hand-detector
description: "训练客户/工厂专用工业手部检测模型，喂给 MediaPipe 二段管线 (v3.8.0+)。当用户说 '训手部模型'、'客户工厂手套识别不出'、'手部骨架在黑手套场景不出'、需要做工业 hand-detector 时使用。"
argument-hint: "[客户名 或 工厂录像路径]"
model: opus
effort: high
allowed-tools: "Read, Grep, Glob, Shell, Write, StrReplace"
---

# train-hand-detector: 工业专用手部检测器训练

> **背景** (v3.8.0 实证): MediaPipe `mp.solutions.hands` 在工业死区场景命中率为零 ——
> 黑手套俯视、握工具遮挡、半遮挡、非标手姿等。即使切到"高精度模式 (complexity=1, conf=0.5)"
> 也救不了。唯一可行方案是训一个**工厂专用的 hand-detector**，喂给项目的二段管线 (YOLO 框 →
> ROI → MediaPipe HandLandmarker)，绕过 MediaPipe 在 hand 定位环节的训练分布问题。
>
> **接口已埋好** (backend/api/source_mediapipe.py 二段管线、Settings UI 工业模型面板)，
> 训好的 .pt 文件填到「工业专用手部模型」面板路径栏即生效，不需要改任何代码。

## 总览：一个工程师 3-4 天

| 阶段 | 工时 | 产出 |
|------|------|------|
| 1. 现场录像 | 0.5 天 | 30-60 分钟覆盖各种手套/角度/工具的视频 |
| 2. 抽帧 | 10 分钟 | 3000-5000 张图 |
| 3. 标注 | 4-6 小时 | YOLO 格式 hand bbox 标签 |
| 4. 训练 | 2-3 小时 | best.pt (yolov11s, ~18MB) |
| 5. 验证 | 0.5 天 | 用 tools/diag_hand_two_stage.py 跑命中率 |
| 6. 部署 | 10 分钟 | 客户机配置路径, 二段管线自动启用 |

---

## 第 1 步: 现场录像

### 不可省略的覆盖维度

**任何一个维度漏了, 模型在该子场景下命中率会断崖。**

- **手套类型**: 黑色橡胶 / 白色线手套 / 蓝色丁腈 / 红色防割 / 无手套（露出皮肤）
- **拍摄角度**: 正视（手平举）/ 俯视 45° / 俯视 90° / 侧视
- **持物状态**: 空手 / 握电批 / 握扳手 / 握毛刷 / 双手协作传物
- **光照**: 早班自然光 / 中班顶灯 / 夜班单点光源
- **遮挡程度**: 全手露出 / 1/3 遮挡 / 1/2 遮挡 / 几乎只露指尖

### 录像建议

- 工业相机/RTSP 直接录 MP4，分辨率与生产线一致（**不要**改分辨率，分布要匹配）
- 每个维度组合录 1-2 分钟，**强制工人做出不同手势** (举手、放下、敲、拿、传)
- 总长 **30-60 分钟够了**，再多边际收益递减

### 抽帧

```bash
mkdir -p ~/datasets/hand_<客户名>/raw
ffmpeg -i recording.mp4 -vf fps=2 ~/datasets/hand_<客户名>/raw/%06d.jpg
```

`fps=2` = 每秒 2 帧。60 分钟视频 → 7200 张，删掉重复后 **3000-5000 张可用**。

---

## 第 2 步: 标注

### 工具选择

| 工具 | 评分 | 备注 |
|------|------|------|
| **X-AnyLabeling** | ⭐⭐⭐⭐⭐ | 自带 SAM 半自动框, 鼠标点一下手就自动出框, 比手画快 5 倍。**强烈推荐** |
| Label Studio | ⭐⭐⭐⭐ | 重器, 团队协作好但单人用过重 |
| LabelImg | ⭐⭐⭐ | 老牌, 纯手画, 慢但稳定 |

### X-AnyLabeling 安装

```bash
pip install x-anylabeling
x-anylabeling
```

或下 release: https://github.com/CVHub520/X-AnyLabeling/releases

### 标注规则（**关键**）

1. **类别只设 1 个: `hand`**
   - 不区分左右手
   - 不区分握工具/空手

2. **框框法则**:
   - **整只手都框进去** (包括手套、被工具部分遮挡的也算)
   - **露出 1/3 以上的手**也标 (这是死区救场关键 —— 不标就训不出来)
   - 只露指尖（< 1/3）→ **不标**, 标了会引入大量噪声
   - 手腕到指尖, **包括手套延伸部分**

3. **不要漏标**:
   - 一张图有几只手就标几只手
   - 漏标会让模型学到"这个区域不是手"的错误 prior

4. **质量优先于数量**:
   - 800 张高质量标注 > 3000 张随便糊的
   - 复杂场景（遮挡、握工具）多标几张

### 输出格式

X-AnyLabeling 选 "YOLO 格式" 导出, 得到目录：

```
hand_<客户名>/
├── images/
│   ├── 000001.jpg
│   ├── 000002.jpg
│   └── ...
└── labels/
    ├── 000001.txt   # 每行: class_id cx_norm cy_norm w_norm h_norm
    ├── 000002.txt
    └── ...
```

---

## 第 3 步: 训练

### 数据集组织 (train/val 8:2 切分)

```bash
cd ~/datasets/hand_<客户名>
python3 -c "
import os, random, shutil
random.seed(42)
src = 'images'
imgs = [f for f in os.listdir(src) if f.endswith('.jpg')]
random.shuffle(imgs)
n_val = len(imgs) // 5
for split in ['train', 'val']:
    os.makedirs(f'images/{split}', exist_ok=True)
    os.makedirs(f'labels/{split}', exist_ok=True)
for i, img in enumerate(imgs):
    split = 'val' if i < n_val else 'train'
    base = img[:-4]
    shutil.move(f'images/{img}',          f'images/{split}/{img}')
    if os.path.exists(f'labels/{base}.txt'):
        shutil.move(f'labels/{base}.txt', f'labels/{split}/{base}.txt')
print(f'train={len(imgs)-n_val}, val={n_val}')
"
```

### 数据集配置 `hand.yaml`

```yaml
path: /home/<user>/datasets/hand_<客户名>
train: images/train
val: images/val
names:
  0: hand
```

### 训练命令 (yolov11s 起点, 项目模型仓库同源)

```bash
source ~/anaconda3/etc/profile.d/conda.sh && conda activate tianjun
# 项目里已经装了 ultralytics
yolo train \
    data=/home/<user>/datasets/hand_<客户名>/hand.yaml \
    model=yolov11s.pt \
    epochs=80 \
    imgsz=640 \
    batch=16 \
    device=0 \
    project=runs/hand_<客户名> \
    name=v1
```

**说明**:
- `model=yolov11s.pt`: COCO 预训练起点。YOLOv11 系列里 s = small, 兼顾速度精度
- `epochs=80`: 单类任务 fine-tune, **30-50 epoch 就收敛**, 80 给余量
- `imgsz=640`: 输入边长。生产线相机 1080p → 自动 letterbox 到 640x640
- `batch=16`: RTX 3060 12GB 单卡上限。显存不够减到 8
- `device=0`: 单卡训。多卡 `device=0,1`

### 训完看曲线

```bash
ls runs/hand_<客户名>/v1/
# weights/best.pt  weights/last.pt  results.png  confusion_matrix.png ...
```

打开 `results.png`，看 **mAP50** 曲线后期是否平稳。健康值：
- `mAP50 > 0.85`: 优秀，部署
- `0.7 < mAP50 < 0.85`: 可接受，部署后看实测
- `mAP50 < 0.7`: 数据质量有问题，回到第 1-2 步补数据

---

## 第 4 步: 验证（**不可跳过**）

项目里现成的 PoC 脚本就是为这个准备的：

```bash
cd /home/qianqian/桌面/word/tianjun副本
source ~/anaconda3/etc/profile.d/conda.sh && conda activate tianjun
python tools/diag_hand_two_stage.py \
    --video /path/to/<客户>真实工位视频.mp4 \
    --yolo runs/hand_<客户名>/v1/weights/best.pt \
    --yolo-kind v8 \
    --task backend/data/models/hand_landmarker.task \
    --frames 300 \
    --conf 0.25
```

输出会落在 `/home/qianqian/桌面/mediapipe_poc/two_stage/`，包含：
- `baseline_*.mp4` (纯 MediaPipe baseline 结果, 死区场景接近 0%)
- `two_stage_*.mp4` (自训 detector + ROI + MediaPipe, 应明显改善)
- `report.json` (命中率/平均推理时间数字)

### 验收门槛

- **黑手套俯视场景**: baseline 0% → 二段 **≥ 60%**
- **米色手套握工具场景**: baseline 47.7% → 二段 **≥ 50%** (持平或更好即可)
- **平均推理时间**: 二段 **≤ 25ms/帧** (再慢就要怀疑 imgsz 设太大)

未达标 → 看 baseline 报告里二段失败的帧是 YOLO 没框出 (回去补黑手套数据) 还是 YOLO 框对但 HandLandmarker 不出 21 点 (这是 Google 模型问题, 数据再多也没用 — 走第 7 步备选方案)。

---

## 第 5 步: 部署

### 5a. 把 best.pt 放到客户机本地

```bash
# 在客户工控机上 (Windows)
mkdir D:\tianjun\models
# 把 best.pt 上传/拷贝到这里
# 改个有意义的名字, 便于以后管理
mv D:\tianjun\models\best.pt D:\tianjun\models\hand_<客户名>_v1.pt
```

### 5b. 通过 Settings 面板配置（推荐）

1. 打开「系统设置」→「性能设置」→「MediaPipe 骨架叠加」
2. 启用 MediaPipe 叠加, 勾选「手部关键点」
3. 展开「工业专用手部模型」折叠
4. 模型文件路径填: `D:/tianjun/models/hand_<客户名>_v1.pt`
5. 模型格式选 v8 (ultralytics 训出的就是 v8)
6. 默认参数即可 (置信度 0.25 / NMS 0.45 / 输入 640 / ROI 外扩 30%)
7. 点「应用」

**徽章变化**:
- 应用瞬间: 「待加载」(灰)
- 开启视频源 + 检测后首帧: 「已启用」(绿)
- 路径错: 「异常」(红)

### 5c. 或者用 curl (Linux 调试 / 远程脚本部署)

```bash
curl -X POST http://127.0.0.1:8001/api/v1/source/stream/config \
  -H 'Content-Type: application/json' \
  -d '{
    "frame_limit_enabled": false,
    "target_stream_fps": 30,
    "use_half": false,
    "mediapipe_enabled": true,
    "mediapipe_pose": false,
    "mediapipe_hands": true,
    "mediapipe_confidence": 0.5,
    "mediapipe_interval": 1,
    "mediapipe_model_complexity": 1,
    "mediapipe_track_confidence": 0.5,
    "mediapipe_hand_detector_path": "D:/tianjun/models/hand_<客户名>_v1.pt",
    "mediapipe_hand_detector_kind": "v8",
    "mediapipe_hand_detector_conf": 0.25,
    "mediapipe_hand_detector_iou": 0.45,
    "mediapipe_hand_detector_imgsz": 640,
    "mediapipe_hand_roi_pad": 0.3
  }'
```

### 5d. 验证已生效

```bash
curl http://127.0.0.1:8001/api/v1/source/stream/config | python -m json.tool | grep two_stage_status
# 期望: "state": "active", "message": "专用手部模型已启用 (二段管线)"
```

---

## 第 6 步: 持续维护

### 客户反馈"骨架时不时丢"

- 拿客户当天录像, 跑 `diag_hand_two_stage.py` 出命中率
- 命中率 > 70% → 调 UI 里 ROI 外扩比例 (从 30% → 50%) 让 HandLandmarker 看到更多上下文
- 命中率 < 70% → 回到第 1 步, 拿这次录像补 200-500 张标注, 用 `model=runs/.../best.pt` 做增量 fine-tune (epochs=30 就够)

### 客户反馈"骨架画到不是手的东西上"

- 多半是 hand-detector 把扳手/物料当成手了 (训练时这些样本不够)
- 调 UI 里 hand-detector 置信度 (0.25 → 0.35) 临时收口
- 长期: 补"非手对象 = 不框"的负样本到训练集

---

## 第 7 步: 备选方案 (MediaPipe HandLandmarker 仍然不出 21 点)

**症状**: hand-detector 框对了, ROI 也切得很准, 但 MediaPipe HandLandmarker 就是不画骨架。

这是 Google 训练数据决定的死结 (例如纯黑手套握工具)。两条退路：

### 退路 A: 用 YOLOv11-pose 直接出 21 关键点

- 训练目标: 在 hand bbox 上加 21 个关键点标注
- 标注成本 ×5 (每只手要标 21 个点而不是 1 个框)
- 训出来的 yolov11-pose 单模型完成"框 + 21 关键点"两件事
- 集成方式: 需要在 `source_mediapipe.py` 加一种 `kind=pose` 分支, 跳过 HandLandmarker 直接用 YOLO 输出画骨架

### 退路 B: 只出框, 不出骨架

- 客户其实只需要"画面里手在哪", 不一定要 21 关键点
- 在 hand-detector 框的位置画一个半透明手形覆盖图标
- 视觉效果"专业"足够忽悠业务方, 但骨架是假的
- 不实用, 仅作 demo 备份

---

## 8. 文件清单 (在哪改)

| 改动 | 文件 |
|------|------|
| 数据集 | `~/datasets/hand_<客户名>/` (不入 git, 太大) |
| 训练产出 | `runs/hand_<客户名>/v1/weights/best.pt` |
| 客户机部署位置 | `D:/tianjun/models/hand_<客户名>_v1.pt` |
| 后端二段管线代码 | `backend/api/source_mediapipe.py: _try_init_two_stage / _run_two_stage_inference` |
| 前端 UI | `frontend/src/views/Settings/index.vue: 工业专用手部模型 折叠面板` |
| 验证脚本 | `tools/diag_hand_two_stage.py` |
| MediaPipe Tasks 模型 | `backend/data/models/hand_landmarker.task` (项目内置, 7.5MB) |

---

## 9. 反 pattern (踩过的坑, 不要再踩)

| 错误做法 | 后果 | 正解 |
|---------|------|------|
| 用 COCO 标注里的 person 框近似 hand | hand-detector 把整个上半身当手 | 必须重新标 hand bbox, 不偷懒 |
| 不收集本工厂数据, 用网上 hand 公开数据集 | 在客户工厂 0% 命中 | 必须现场录像, 分布要匹配 |
| 标注时只框完全露出的手 | 半遮挡场景全漏 | **露出 1/3 以上都要框** |
| 用 yolov11x (最大版) 训, 想着精度高 | 推理慢, 客户机 CPU 撑不住 | yolov11s 已经够, 大模型不见得更准 |
| epochs=300 拉满 | 过拟合, 在 val 上反而下降 | epochs=80 + early stopping (Ultralytics 默认带) |
| 不做 train/val 切分, 全部 train | 不知道模型有没有过拟合 | 必须 8:2 切分, 看 val mAP |
| 部署后不开 MediaPipe 就期待状态显示「已启用」 | 卡在「待加载」 | 二段管线 lazy-init, 必须开启视频流 + 检测才会触发首次加载 |

---

## 10. 当用户问起时如何回答

| 客户/老板问 | 标准回答 |
|------------|---------|
| 多久能给我做出来? | 数据采集 + 标注 + 训练大概 3-4 个工作日 |
| 一定能识别我们的黑手套吗? | 在你们工厂现场录像训出来的模型可以; 通用模型不行 |
| 每个客户都要训一遍吗? | 第一个客户从头训, 后续客户做增量 fine-tune (epochs=30, 显著省时间) |
| 训完模型放哪? | 跟随安装包发或单独 .pt 补丁包 (~18MB, 不大) |
| 模型会不会被人偷走? | 部署到客户机就是物理隔离, 跟 yolov8 业务模型同等级别风险 |

EOF