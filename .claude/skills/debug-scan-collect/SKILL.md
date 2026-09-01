---
name: debug-scan-collect
description: "诊断 v3.56 周期多码采集（一工件多码分类/槽位状态机/收尾结算）：码不入槽、归错类、重复码不拦/误拦、少扫不报NG、NG挂起不放行、多扫策略不对、视觉双重验证误判、监控面板不显示、txt不落盘、追溯查不到组件码。当客户说'扫码没进面板''码归错类了''少扫没报警''补扫没转OK''工装码被拒''txt没生成''追溯里看不到芯子码'时使用。"
---

# debug-scan-collect: 周期多码采集诊断

> v3.56 落地（六和焊接一号工位场景：1 母排码 + 6/9 芯子码 + 1 工装码收尾）。
> 默认关：项目未配置/未启用 = 存量行为零差异。

## 1. 一句话架构

```
扫码枪 → scanner.py → mes_hooks._handle_scan
    ├─ WorkpieceFlow 互斥（在多码采集之前）
    ├─ ★ ScanCollectEngine.on_scan（项目启用多码采集 → 互斥 return）
    │     分类入槽（正则优先/顺序兜底）→ 去重（组内/跨组，槽级可豁免）
    │     → 数量门（溢出按槽级策略）→ 收尾码结算 ok/ng_missing
    │     → [ng_pending 开] 少扫挂起等补扫/人工放行
    │     → [vision_gate 开] 视觉周期判定融合（两边都 OK 才 OK）
    │     → fire_external_event_response（灯/Toast/语音/计数器，不动检测周期）
    │     → 注册 Workpiece（收尾码为身份）→ scan_group_end 实时导出 txt
    └─ 单码 scan_pair / pending_workpiece（未启用多码采集才走）
```

## 2. 关键文件

| 文件 | 职责 |
|---|---|
| `backend/services/scan_collect.py` | 引擎单例：槽位状态机/去重/结算/挂起/视觉门/纠错/超时 |
| `backend/models/scan_collect_models.py` | `scan_collect_configs`（按项目）+ `scan_collect_records`（逐码） |
| `backend/api/scan_collect.py` | `/api/v1/scan-collect/*`：config / state / remove-code / clear / resolve-ng / records |
| `backend/services/mes_hooks.py` | `_handle_scan` 优先路径接入 + `_handle_cycle_end` 喂 `on_vision_cycle` |
| `backend/services/export_realtime.py` | `dispatch_scan_group_export`（trigger_event=`scan_group_end`） |
| `backend/services/export_seed.py` | 内置模板 `builtin_scan_group_txt`（一工件一 txt，UTF-8 BOM + CRLF） |
| `frontend/src/views/MES/ScanCollectConfigCard.vue` | MES→扫码器→「多码采集」配置卡（填入示例=现场三类真实码预设） |
| `frontend/src/views/Monitor/ScanSlotsPanel.vue` + `ScanSlotsDetail.vue` | 监控页面板（紧凑徽标条 / 完整面板双形态 + 明细浮层纠错 + NG 挂起横幅） |
| `frontend/src/views/MES/WorkpiecePanel.vue` | 追溯详情「组件码」区块（按 group_id 分节，循环工装码同名多轮不混排） |
| `tests/test_scan_collect_unit.py` / `tests/e2e_browser/test_scan_collect.py` / `tests/uat/uat_scan_collect_ui.py` | 单测 26 / CI e2e 3 / 可见 UAT 22 断言 |

## 3. 配置模型（存 `scan_collect_configs.config` JSON）

- `slots[]`：`key/label/count/regex/role(closing=收尾)` + 槽级覆盖 `dedup_cross_group` / `on_overflow`（`inherit`=跟随全局）
- 全局策略：`dedup_in_group`（ng_alarm/reject）、`dedup_cross_group`（off/reject/ng_alarm）、`on_overflow`、`on_unmatched`、`settle_on`（closing/all_filled）、`timeout_sec`
- `ng_pending`：少扫收尾不结算 → 挂起（报一次 NG 警）→ 补扫齐转 OK / `POST /resolve-ng` 按 NG 放行（放行不二次响铃）
- `vision_gate` + `vision_window_sec` + `vision_missing`（ignore/ng）：扫码 OK 后还要看本通道窗口内最近视觉周期判定；扫码已 NG 不再看视觉
- `event_ok_id` / `event_ng_id`：结算借事件响应面（挂事件系统的灯/语音/人工确认）
- 现场范式（填入示例预置）：母排 `^M.{29,32}$` ×1；芯子 `^\d{13}$` ×6 且 `on_overflow=ng_alarm`（多扫直接 NG）；工装 `^H-C` ×1 `role=closing` 且 `dedup_cross_group=off`（循环治具豁免）；`ng_pending=true`；`vision_gate=true + vision_missing=ignore`

## 4. 高频症状 → 排查

| 症状 | 先查 |
|---|---|
| 扫码没进面板 | ① 项目启用了吗（`GET /scan-collect/config?project_id=`）② 码被哪个通道收了——多工位下项目被哪个工位绑定就路由到哪个 ch，看日志 `on_scan_received (ch=X, project=Y)` ③ ScanLog.error_msg 有无 `多码采集:` 前缀 |
| state 接口一直空 | **参数名是 `channel` 不是 `channel_id`**（`GET /scan-collect/state?channel=2`）——传错参 FastAPI 静默用默认 0（2026-09-01 排障实录） |
| 码归错类 | 正则多槽命中取第一个未满槽；正则全不中才顺序兜底（有正则的槽只吃匹配码）。坏正则按不匹配处理不报错 |
| 工装码被拒"历史重复" | 该槽 `dedup_cross_group` 必须为 `off`（循环治具跨工件必然重复） |
| 少扫没报 NG | `settle_on=closing` 时只有扫到 `role=closing` 的码才结算；没扫收尾码永远不判（可配 timeout_sec 兜底） |
| 补扫没转 OK | 挂起态只在 `ng_pending=true` 且缺码补齐后**自动**结算转 OK；组内重复码补扫会被去重拦掉 |
| 视觉门误 NG | `vision_missing=ng` 且窗口内无视觉周期 → `ng_vision_missing`；相机侧没跑检测就把 vision_missing 设回 ignore |
| txt 没落盘 | ① Data 页实时规则 trigger_event 必须是 `scan_group_end` ② 文件名模板用 `{{ now_ymdhms }}` 等上下文字段——**`now()` helper 被上下文 `now` datetime 变量遮蔽，写 `now('...')` 会 TypeError**（v3.56 排障实录，看规则 last_run_error）③ 规则 channel_filter/project_filter |
| 追溯查不到组件码 | records 默认排除 `status=deleted`（纠错删的码只审计留痕）；工件详情按 `workpiece_id` 查，工装码复用时同名工件多轮码组按 group_id 分节显示 |
| 切项目后码丢了 | 设计如此：通道现组 project 与新扫码 project 不一致 → 旧组作废（status=void，防跨项目串账） |

## 5. 线程模型与不变量

- `on_scan` 在 mes-hook-worker 单线程；纠错/放行在 API 线程；超时在 Timer 线程——全部状态变更持 `self._lock`
- 配置按 project 缓存（`_cfg_cache`），PUT config 会 invalidate；Monitor 高频轮询走缓存零开销
- 通道裁撤清理已挂 `on_channel_removed`（AGENTS.md 不变量 #4：groups/last_settled/vision_last 三处）
- 结算/导出/工件注册各自 try 隔离，导出失败不连坐结算
- 新表走 `main.py create_all` 自建（模型三处注册：main.py + conftest.py + alembic/env.py），无 mXXXX 迁移

## 6. 快速联调（无扫码枪）

```bash
# 模拟扫码（走全链路: scanner → mes_hooks → engine）
curl -X POST http://localhost:8001/api/v1/scanner/simulate \
  -H 'Content-Type: application/json' -d '{"barcode":"9260000144908","channel_id":2}'
# 看实况
curl 'http://localhost:8001/api/v1/scan-collect/state?channel=2'
# NG 挂起放行
curl -X POST http://localhost:8001/api/v1/scan-collect/resolve-ng \
  -H 'Content-Type: application/json' -d '{"channel_id":2}'
```

可见 UAT（22 断言全剧本）：`python tests/uat/uat_scan_collect_ui.py`（证据落 `/tmp/uat_scan_collect` + `/tmp/uat_video`）。
