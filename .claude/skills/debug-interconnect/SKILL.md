---
name: debug-interconnect
description: "诊断 YoloVision 训练平台互连（v3.47）：模型包推送/拉取不入库、来源徽标不显示、现场帧采样不回传、磁盘队列堆积、鉴权失败、设备身份异常。当客户说'平台推的模型没出现在模型仓库'、'采样流水一直空'、'拉取游标不动'时使用。"
argument-hint: "[症状描述]"
model: opus
effort: high
allowed-tools: "Read, Grep, Glob, Shell, StrReplace"
---

# debug-interconnect: 训练平台互连诊断（v3.47）

与公司 **YoloVision 训练平台**的双向互连，契约 interconnect-contract 1.1（共同事实源在训练平台仓库 `TIANJUN_INTERCONNECT_SPEC.md`）。**默认关闭，零配置零影响。**

需求: $ARGUMENTS

---

## 一、模块地图

```
backend/api/interconnect.py            # /api/v1/interconnect/* 8 个端点（health 免鉴权，其余 token）
backend/services/interconnect/
  ├── config.py        # 配置存取（SystemConfig KV 'interconnect.config'，不是独立 json 文件）
  ├── identity.py      # device_id 稳定身份（KV 'interconnect.device_id'，License machineId 同源，tj- 前缀）
  ├── package_ingest.py# .yvmodel 包安全入库（SHA-256 逐产物 / 防路径穿越 / 防 zip 炸弹 / x-project-name 匹配项目）
  ├── puller.py        # 拉取分发：定期轮询对端 packages 游标增量列包 → 下载 → 复用 push 同一入库路径
  ├── sampler.py       # 推理循环采样决策（置信度带 / 未检出守门 / 检出闪断 / NG 事件帧）
  ├── sample_queue.py  # 磁盘队列（默认 max 500 条 / TTL 72h，进程重启可恢复）
  └── uploader.py      # 后台 worker 异步回传（限流 + 指数退避，对端离线不丢帧）
frontend/src/views/Interconnect/index.vue  # 互连设置页（连接/采样/拉取/状态与流水）
frontend/src/api/interconnect.js
```

- **启动挂载**：`backend/main.py::_start_interconnect_uploader()`（模块导入时执行）——配置开着才拉起 uploader worker + puller 线程
- **采样挂点**：`source_inference_loop_mixin.py` 推理循环里调 `sampler.maybe_sample_frame(self, original_frame, detections)`，**try/except 错误隔离**（采样炸了不影响检测）；NG 事件帧由 `source_event_trigger_mixin.py` 置 `_interconnect_ng_sample_pending` 标记、推理循环消费
- **数据落点**：`models` 表 `source` 列（'yolovision'/'local'，NULL 视同 local）+ `meta` JSON（训练分析 x-analysis），迁移 `m0008_model_interconnect_meta`

## 二、排查决策树

```
症状
 ├─ 平台推的模型没出现在模型仓库
 │   ├─ 看后端日志 grep interconnect：入库失败会记原因（SHA 不符/违禁载荷/项目不匹配）
 │   ├─ x-project-name 是精确匹配本机项目名 → 项目名对不上 = 拒收（404 项目不存在）
 │   └─ 鉴权失败 401 → 双方共享令牌必须同一随机串（≥32 字符）
 ├─ 拉取模式游标不动 / 不拉新包
 │   ├─ GET /api/v1/interconnect/status 看 puller 状态与 last_error
 │   ├─ 坏包会跳过不卡游标；**传输失败会停轮保序重试**（对端恢复前游标停在原地是设计行为）
 │   └─ POST /pull-now 手动触发一轮，观察日志
 ├─ 采样流水一直空
 │   ├─ 采样默认关，先确认互连设置页「现场帧采样回传」开了
 │   ├─ 未检出帧有周期内守门：**工件不在检时的空帧不算可疑**（空帧≠漏检），没起周期就不会采
 │   ├─ 限流：最小间隔 + 每小时上限，验证时把限流调宽
 │   └─ 检测必须真的在跑（采样挂在推理循环里）
 ├─ 队列堆积 / 帧没回传
 │   ├─ GET /status 看 queue 深度与 uploader last_error；对端离线 = 指数退避堆队列（TTL 72h 会丢过期帧）
 │   └─ 404 项目不存在的帧直接丢弃不堵队列（设计行为）
 └─ 设备台账错乱（平台端看到重复设备）
     └─ device_id 持久化在 SystemConfig KV 'interconnect.device_id'；换机/克隆盘会带走旧 ID，删该 KV 重启重新生成
```

## 三、配置与状态速查

```bash
# 配置真相（不是 json 文件，在 DB KV 里）
sqlite3 sql_app.db "SELECT value FROM system_configs WHERE key='interconnect.config'"
# 运行状态一把抓（队列/上传/采样/拉取/设备身份）
curl -s http://localhost:8001/api/v1/interconnect/status
# 最近采样流水
curl -s "http://localhost:8001/api/v1/interconnect/samples/recent?limit=20"
# 探活对端
curl -s -X POST http://localhost:8001/api/v1/interconnect/test-connection
```

## 四、回归入口

```bash
# 单元/端点 + 模拟对端真 HTTP 闭环集成（34 个）
~/miniconda3/envs/tianjun/bin/python -m pytest tests/test_interconnect.py tests/test_interconnect_loop.py -q
# CI E2E（需起前后端，见 start-dev-servers skill）
E2E_API_URL=... E2E_BASE_URL=... python -m pytest tests/e2e_browser/test_interconnect_page.py -q
# UAT 截图脚本
tests/uat/uat_interconnect_screenshots.py
```

## 五、不变量 / 边界

1. **采样绝不允许拖垮推理热路径**：`maybe_sample_frame` 全程 try/except 隔离 + 关闭时 O(1) 早退——改 sampler 别把重活挪进推理线程
2. **package_ingest 的安全校验一条不能省**（路径穿越/违禁载荷/zip 炸弹/逐产物 SHA-256）——push 和 puller 两条入口**复用同一入库路径**，改校验只改一处
3. 拉取到的模型**永不自动启用**——只入库打徽标，切换模型仍是人的决定
4. `models.source` 为 NULL 的存量行语义 = 'local'，读取端不要写成 `== 'local'` 精确比较
5. 与插件平台无关：互连是主程序原生基础设施（多客户通用），客户级变种（如只采某类 NG）再走插件 hook
