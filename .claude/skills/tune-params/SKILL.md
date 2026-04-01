---
name: tune-params
description: "检测参数调优全流程：分析视频+模型、逐轮调参、记录过程、生成调优文档（MD+JSON）。当用户说'调参'、给出视频和模型让优化参数时使用。"
argument-hint: "[项目名 或 视频路径 或 问题描述]"
model: opus
effort: high
allowed-tools: "Read, Grep, Glob, Shell, Write, StrReplace, Agent"
---

# tune-params: 检测参数调优

用户要求对检测参数进行调优。你需要完成分析、调参、验证、记录的完整流程。

需求: $ARGUMENTS

## 第1步: 收集信息

### 必要信息（向用户确认）
- **项目名**: 哪个项目需要调参
- **视频路径**: 用于测试的视频文件
- **模型路径**: 使用的模型文件（或从项目配置中读取）
- **预期行为**: 用户期望的正确检测流程是什么

### 从系统读取
- 项目配置: `sqlite3 backend/sql_app.db "SELECT * FROM projects WHERE name='项目名'"`
- 当前步骤配置: 从 `steps_config` JSON 字段提取
- 当前 pipeline 配置: `sequence_order`, `settlement_mode`, `custom_conditions` 等

## 第2步: 分析原始检测结果

### 跑模型推理
```python
from ultralytics import YOLO
model = YOLO("模型路径")
results = model.predict("视频路径", conf=0.25, stream=True)
# 统计每个类别的检测帧数、置信度分布、出现时间线
```

### 分析指标
- 每个类别的总检测帧数
- 置信度分布（min/max/mean/median）
- 是否有误检（不应该出现的类别在某些帧出现）
- 是否有漏检（应该出现的类别未检测到）
- 检测框稳定性（同一目标的检测框是否抖动）

### 记录初始状态
记录分析发现，包括：
- 原始检测的问题列表
- 每个步骤的检测质量评估

## 第3步: 逐轮调参

每一轮调参都要记录：

```
### 第N轮: [调参目的]
- 观察: [上一轮或初始状态的问题]
- 调整: [修改了什么参数，从什么值改到什么值]
- 依据: [为什么这样调]
- 结果: [改后的效果，是否解决问题]
- 状态: [通过/未通过/需继续调整]
```

### 可调参数清单

| 参数 | 位置 | 典型范围 | 作用 |
|------|------|----------|------|
| `step_conf_threshold` | steps_config[].conf_threshold | 0.3-0.9 | 步骤确认的置信度阈值 |
| `min_duration` | steps_config[].min_duration | 0-5s | 步骤最短持续时间 |
| `max_duration` | steps_config[].max_duration | 1-30s | 步骤最长持续时间 |
| `min_frames` | steps_config[].min_frames | 1-30 | 步骤确认的最少帧数 |
| `max_interval` | steps_config[].max_interval | 1-10s | 步骤消失后的等待时间 |
| `gap_tolerance` | steps_config[].gap_tolerance | 0-60帧 | 允许的检测间断帧数 |
| `conf_threshold` | predict() 参数 | 0.15-0.5 | 模型推理置信度阈值 |
| `iou_threshold` | predict() 参数 | 0.3-0.7 | NMS IoU 阈值 |

### 调参策略

1. **误检（false positive）**: 优先提高 `min_frames` 或 `min_duration`，其次提高 `conf_threshold`
2. **漏检（false negative）**: 降低 `conf_threshold`，检查 `gap_tolerance`
3. **步骤跳过**: 检查 `max_interval` 是否太短
4. **重复计数**: 检查 `accept_once` 配置，检查 `max_interval`
5. **结算异常**: 检查 `settlement_mode`、`sequence_order` 配置

### 如何修改参数
通过数据库直接更新：
```sql
-- 查看当前步骤配置
SELECT name, steps_config FROM projects WHERE name='项目名';

-- 更新单个步骤的参数（需要更新整个 steps_config JSON）
-- 或者通过前端 Project 页面修改
```

或告知用户在前端 Project 页面修改后重启检测。

## 第4步: 验证最终效果

1. 用最终参数对完整视频跑一遍检测
2. 确认所有步骤能被正确检测
3. 确认周期结算结果正确（OK/NG 符合预期）
4. 确认没有新的误检/漏检

## 第5步: 等待用户确认

将最终配置和效果展示给用户，等用户明确说"可以了"、"没问题"、"满意"后再进入第6步。

**不要在用户确认前生成文档！**

## 第6步: 生成调参文档

在 `docs/tuning/` 下生成两个文件：

### Markdown: `docs/tuning/{项目名}_{YYYY-MM-DD}.md`

```markdown
# 调参记录: {项目名} (YYYY-MM-DD)

## 环境
- 项目: {项目名}
- 模型: {模型文件名} ({task_type}, {class_count} classes)
- 视频: {视频文件名} ({fps}fps, {resolution})
- GPU: {GPU型号}

## 初始配置
| 步骤 | 置信度 | 最短持续 | 最少帧数 | 最大间隔 | 丢帧容忍 |
|------|--------|----------|----------|----------|----------|
| ... | ... | ... | ... | ... | ... |

## 调优过程

### 第1轮: [目的]
- 观察: ...
- 调整: ...
- 依据: ...
- 结果: ...

### 第N轮: ...

## 最终配置
| 步骤 | 置信度 | 最短持续 | 最少帧数 | 最大间隔 | 丢帧容忍 |
|------|--------|----------|----------|----------|----------|
| ... | ... | ... | ... | ... | ... |

## 调优总结
- 关键发现: ...
- 推荐参数范围: ...
- 注意事项: ...
```

### JSON: `docs/tuning/{项目名}_{YYYY-MM-DD}.json`

```json
{
  "project": "项目名",
  "date": "YYYY-MM-DD",
  "model": {
    "name": "模型文件名",
    "task": "detect|segment",
    "classes": ["class1", "class2"],
    "class_count": 5
  },
  "video": {
    "path": "视频路径",
    "fps": 30,
    "resolution": "1280x720",
    "duration_s": 120
  },
  "initial_config": {
    "steps": [
      {
        "label": "步骤名",
        "conf_threshold": 0.5,
        "min_duration": null,
        "max_duration": null,
        "min_frames": 1,
        "max_interval": 3,
        "gap_tolerance": 0
      }
    ],
    "settlement_mode": "first_step|last_step",
    "logic_mode": "sequential|detection|custom"
  },
  "tuning_rounds": [
    {
      "round": 1,
      "purpose": "调参目的",
      "observation": "观察到的问题",
      "changes": [
        {
          "step": "步骤名",
          "param": "参数名",
          "from": "原值",
          "to": "新值"
        }
      ],
      "reasoning": "为什么这样调",
      "result": "效果描述",
      "passed": true
    }
  ],
  "final_config": {
    "steps": [...]
  },
  "summary": {
    "key_findings": ["发现1", "发现2"],
    "recommended_ranges": {
      "参数名": {"min": 0.3, "max": 0.7, "reason": "原因"}
    },
    "notes": ["注意事项"]
  },
  "keywords": ["误检", "min_duration", "项目名", "步骤名"]
}
```

## 重要提醒

- 每一轮调参都要有清晰的 观察→调整→依据→结果 链路
- 不要一次改太多参数，每轮只改 1-2 个参数，方便追踪效果
- 如果发现是代码 bug 而非参数问题，切换到 `debug-source` / `debug-detection` skill
- 调参涉及代码修改时，遵循 `modify-source` skill 的安全分析流程
- JSON 的 keywords 要包含项目名、步骤名、问题类型，方便后续 AI 训练检索
- 如果同一项目多次调参，文件名用日期区分，不要覆盖旧记录
