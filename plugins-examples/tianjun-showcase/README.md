# 天军 展会 全应用定制界面（Tier 2 UI 插件 · RFC12 全页面整页覆盖）

> 展会展示用。把主程序 **8 个页面** 全部替换为高科技重设计界面，纯插件落地，不污染主程序。
> 依赖平台能力：RFC 12 全页面整页覆盖 slot（`*.layout.body`），见 `docs/plugin-system/design/12_full_page_override_rfc.md`。

## 覆盖范围（注册的 slot）

| slot | 页面 | 数据来源 |
|---|---|---|
| `monitor.layout.body` | 检测主页（hero） | 透传 props + 插件自轮询 `/source/detection/results`、`/system/device-status`、`/data/stats/behavior-score`、`/data/stats/yield-trend`；控制按钮走透传 `actions` |
| `project.layout.body` | 项目管理 | `host.api` `/projects` CRUD + 激活 |
| `model.layout.body` | 模型仓库 | `host.api` `/models` 上传/删除 |
| `source.layout.body` | 输入源设置 | `host.api` `/workstations/` |
| `data.layout.body` | 数据中心 | `host.api` `/data/sessions/*`、`/data/export/csv` |
| `mes.layout.body` | MES 管理（7 子面板 Tab） | `host.api` `/mes/*`、`/scanner/*`、`/cluster/*`、`/external-devices/*` |
| `alarm.layout.body` | 报警设置 | `host.api` `/alarm/*` |
| `settings.layout.body` | 系统设置（多 Tab） | `host.api` `/system/*`、`/plugins`、`/auth/status`、`/source/gpu/list` |

## 版本与主程序对齐

- **v1.5.1**（当前）：对齐主程序 **v3.56 ~ v3.61**，且 **插件即完整应用**（配置 + 鉴权不再依赖「先在主程序配好再套皮」）。P0：登录/登出 token 写穿宿主 `tianjun:auth_token`（含 session_persist）；P1：显示设置三卡写穿 `display_settings`；P2：容器定界 / 逐件开始判定 / ROI 多块 / 多码采集 / OCR·异常 / sidecar 带框下载。详见 `UPGRADE_v1.5.1.md`。`main_version_min` = **3.61.0**。
- **v1.5.0**：全量对齐主程序 **v3.36 ~ v3.55** —— 项目页：NG 判定与处置
  统一模型（`ng_handling` 四行语义 + legacy 键收编，v3.44）、跟踪齐件即结算
  （`tracking_settle_on_complete` + 步骤表「确认放入帧」列，v3.50）、混合跟踪装箱清点
  参数群（`custom_mixed_with` + `custom_mix_container_*` 记账精度/去重/槽位门，
  v3.19~v3.46）、计数组合判定表（`combo_table` 标签×计数查表判型，v3.48）、自定义
  班次列表（`data_config.shifts`，v3.35.1）、称重两阶段流水线（`drive_mode=pipeline`
  + pipeline/timing 参数卡，v3.39）与人员名单下拉（`operator_from_users`，v3.45）、
  逐件离场快照判定（`judge_on_workpiece_leave`，v3.28 补漏）。监控页：人工确认弹窗
  升级（reason 固化 / keeps_cycle 明示 / 包装箱账挂起双选，v3.43~v3.44）、恢复扫码
  按钮（`scanner_resume_blocked` → `POST /scanner/resume`，v3.50）、扫码拒绝统一警告
  （`scan_warning`+`warn_reason`）、称重皮重显示与流水线待收尾队列看板、融合模式实时
  称重数值条（`show_monitor_weights`，v3.35.1）、包装卡箱标签扫码授权/等放工单收尾
  横幅（v3.43/v3.45）、判型 tag 实时 chip（`combo_verdict`）。保存安全：`per_item`
  改展开式保留未知键、`pipeline_config` 整体展开式不丢主程序新键。逐项契约见
  `UPGRADE_v1.5.0.md`。`main_version_min` = **3.55.0**。
- v1.4.1：区域事件 overlap 规则新增「目标框扩边」（`object_margin`，
  对齐主程序 2026-07-13 引擎新参数——动作发生在目标框边缘外侧时的几何桥接，与帧率无关）。
- v1.4.0：全量对齐主程序 **v3.33 ~ v3.35** —— 区域事件全字段（秒基确认 /
  位移门槛 / 锚点跟随 / 每类置信度 / 结算判定）、同标签区域拆分编辑器（多轮次两防护 +
  虚拟步骤同步）、逐件重复打防护与换板兜底、步骤表「等待不被打断 / 外设门控」两新列
  （称重融合 step_gate）、称重前置选择有效期 + 视觉料源防错、事件断点补做
  （ack_keep_cycle）、MES 网关数据库直写适配器、包装复合条码取段 / 工单识别 /
  放工单收尾。逐项契约见 `UPGRADE_v1.4.0.md`。`main_version_min` = **3.35.0**。
- v1.3.x：对齐 v3.31/v3.32（7 逻辑模式 / 称重 / 区域事件基础面 / MES 入站与包装），
  见 `UPGRADE_v1.3.0.md`。

## 实时数据口径（展会语境）

- 检测主页的 **AI 行为分析评分 / 节拍 / 合格率趋势** 由 `detection_cycles` 真实周期数据派生（良率 / 节拍稳定度 / 连续无 NG），**非物理传感**。
- **设备状态** 为真实 GPU（温度/利用率/显存）+ CPU/内存/磁盘（`/api/v1/system/device-status`，pynvml+psutil，缺失降级）。

## 工程要点

- 前端单文件 ESM（`frontend/dist/index.esm.js`），手写 `h()` 渲染，**无构建工具链**（与福建金龙插件一致，`pack-plugin.py` 检测到无 `frontend/package.json` 即跳过 vite build）。
- 样式 `frontend/dist/theme.css`，全部 `.tjsc-` 前缀，不污染主程序 Element/Tailwind。
- 错误隔离：每页 render 包 `safeRender`，组件抛错只显示该页兜底框，不连累其它页（叠加主程序全局 errorHandler）。

## 打包 / 签名

```bash
# 1. 打未签名包（校验 manifest + 回填 files_digest）
python scripts/plugin/pack-plugin.py --src plugins-examples/tianjun-showcase --out ./dist

# 2. 主作者本地签名（需 PLUGIN_SECRET + plugin_master_pri.pem，隔离机）
python scripts/plugin/sign-plugin.py --in ./dist/<...>-uns.tjvplugin --out ./dist
```

## 开发热载（跳验签）

```bash
python scripts/plugin/dev-plugin.py --src plugins-examples/tianjun-showcase
```

> 注意：`monitor.layout.body` 已在 RFC12 解除"仅双工位"限制，单/多工位都会挂载本插件；插件按 `channel-count` 自适应单工位富仪表盘 / 多工位栅格。
