# LG 工时看板 — 领导视角 LEAN 价值分析 (Tier 3)

> 把 LG 客户的独立「视觉AI工时测量系统」(PySide6 桌面软件) 收编为主程序插件。
> 检测/计时/落库全部复用主程序 sequential 状态机；插件承担 **LEAN 价值分析** 与
> **领导看板 UI**（整页覆盖 Monitor，数据大于检测画面）。

## 产品定位

LG 原系统是给**领导当看板**用的：大数字 KPI、LEAN 占比、CT 节拍、步骤耗时——
检测画面只是配角。与主程序功能对照后，检测侧全部有现成等价物：

| LG 原功能 | 主程序等价物 |
|---|---|
| 严格/跳步 SOP 序列 | sequential 逻辑模式 |
| 轮次 (round) | detection_cycles |
| 步骤工时 | step_records.duration |
| 步骤间等待 | step_records.interval_from_prev |
| 轮超时 NG | cycle_max_duration |
| OK/NG 分类录像 | 主程序录像体系 |

**插件真正新增的只有三件事**：

1. **LEAN 价值分析** — 步骤级 VA(增值)/BVA(必要非增值)/NVA(非增值) 动作价值配置
   (存 `Project.steps_config[i].plugin_data["lg-worktime"].value_type`)；
   `cycle_end` hook 按当时配置把步骤耗时归类、步骤间等待归 NVA，
   **冻结**写入自有表 `p_lg_worktime_cycle_lean`（配置变更不污染历史）
2. **领导看板** — 整页覆盖 `monitor.layout.body`：KPI 卡(产量/良率/CT均值/CT累计/等待累计)
   + LEAN 堆叠图(当前轮实时 vs 今日合格均值) + 占比环 + 当前周期实况 + 步骤统计表
   + 近 7 天趋势；检测画面缩为小窗保留开始/停止控制
3. **导出字段 (F8)** — 18 个 Lean 字段注册进自定义导出中央仓库
   (`{{ plugin.lg_worktime.* }}`)，配套两份 xlsx 模板激活时自动植入：
   「LG 日明细」+「LG LEAN统计汇总」（复刻 LG 原 Excel 口径：仅累计合格轮、等待归 NVA）

## 目录结构

```
plugins-examples/lg-worktime/
├── plugin.json                    # tier=3, main_version_min=3.45.0
├── backend/
│   ├── __init__.py                # register_plugin: 表/4个hook/路由/F8字段/模板植入
│   ├── lean.py                    # 纯逻辑: 价值归类/等待归NVA/占比 (无框架依赖)
│   ├── models.py                  # p_lg_worktime_cycle_lean ORM
│   ├── routes.py                  # dashboard 路由 (live/summary/trend/step-averages/step-values)
│   └── export_fields.py           # F8: 18 个导出字段 + provider
├── frontend/
│   ├── dist/index.esm.js          # 看板 + project.step-cell 价值单元格 (单文件 ESM)
│   ├── dist/theme.css             # lgwt- 前缀命名空间样式
│   └── i18n/{zh-CN,en-US}.json
└── templates/                     # 两份 xlsx 导出模板 (Jinja2 CSV → 路线 A 转表格)
    ├── lg_daily_detail.csv.j2
    └── lg_lean_summary.csv.j2
```

## API 面

挂载在 `/api/v1/plugins/lg-worktime/dashboard/*`：

| 端点 | 用途 |
|---|---|
| `GET /live` | 内存实时快照：各通道进行中周期 + 实时 LEAN 分解（看板 1s 轮询，不打 DB）|
| `GET /summary?date&channel_id&project_id` | 某日汇总：产量/良率/CT(主表) + LEAN 累计与占比(插件表) |
| `GET /trend?days` | 近 N 天逐日产量/良率/CT/LEAN |
| `GET /step-averages?date` | 按步骤聚合：次数/平均·最短·最长耗时/平均等待 + 动作价值 |
| `GET /step-values?project_id` | 项目的 {步骤label: VA/BVA/NVA} 映射 |

步骤价值**写入**不走插件路由，走主程序
`PUT /api/v1/projects/{id}/plugin-data`（scope=steps_config, 浅合并不误伤别家）。

## 开发 / 打包 / 测试

```bash
# 开发态激活 (直接 upsert PluginRecord, 跳过验签; 仿 dev-activate-showcase.py)
# 后端重启后生效, 前端硬刷新

# 打包 (未签名)
python scripts/plugin/pack-plugin.py --src plugins-examples/lg-worktime --out ./dist
# 签名由主作者在隔离机用 sign-plugin.py 完成

# 单测 (18 个: lean纯逻辑/hook状态机/落库/路由/F8/模板渲染/植入幂等)
python -m pytest tests/plugin_system/test_lg_worktime_plugin.py -q
# CI E2E (需 backend+frontend+插件激活, 未激活自动 skip)
E2E_API_URL=http://localhost:8004 E2E_BASE_URL=http://localhost:6001 \
  python -m pytest tests/e2e_browser/test_lg_worktime_plugin.py -q
# 可见浏览器 UAT (三件套证据)
cd tests/uat && python uat_lg_worktime_dashboard.py
```

## 口径备忘 (LG 原系统口径 = 出厂默认; v1.5.0 起全参数可配)

出厂默认与 LG 原系统对齐，v1.5.0 起可在 **系统设置(展会页) → 显示设置 → LG 工时参数** 卡调整，
存主程序 `SystemConfig` KV（key=`plugin_lg_worktime_settings`），API 为
`GET/POST /api/v1/plugins/lg-worktime/dashboard/settings`：

| 参数 | 出厂 (LG 原系统) | 可选 | 生效期 |
|---|---|---|---|
| `default_value_type` 未配置步骤默认价值 | VA | VA/BVA/NVA | **冻结期**：只影响之后结算的周期，不回写历史 |
| `wait_value_type` 步骤间等待归类 | NVA | NVA/BVA/VA/EXCLUDE(不计入) | **统计口径**：查询/展示时折算，历史数据跟随新口径 |
| `lean_scope` LEAN 统计范围 | good_only 仅合格轮 | good_only/all | 统计口径 |
| `trend_days` 看板趋势天数 | 7 | 1~60 | 展示 |

- 每步骤价值在 **项目管理(展会页) → 步骤设置 → 动作价值** 逐步配置
  （存 `steps_config[i].plugin_data["lg-worktime"].value_type`，结算时冻结进 lean 表）
- lean 表恒存**原始四桶** `va/bva/nva/wait`（nva 不含等待）；等待归类在查询时按
  `apply_wait` 折算 → `nva_total`/占比/`avg_per_good` 全部跟随
- NG 轮的 lean 行照常落库，`lean_scope=good_only` 时汇总过滤 `is_good=True`
- 占比 = 各类 / (va+bva+nva_total)，四舍五入 1 位小数
