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

- **v1.4.0**（当前）：全量对齐主程序 **v3.33 ~ v3.35** —— 区域事件全字段（秒基确认 /
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
