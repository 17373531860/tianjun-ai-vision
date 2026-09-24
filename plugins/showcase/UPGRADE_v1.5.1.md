# UPGRADE v1.5.1 — 对齐主程序 v3.56 ~ v3.61（插件即主程序）

> 上一版 v1.5.0 对齐到 v3.55。本版把 v3.56~v3.61 覆盖面补进展会 8 页，并修掉
> **现场事故**：鉴权启用后进访客态，插件内登录只写 iframe 内存 token，宿主
> `host.api` 仍读 `localStorage tianjun:auth_token` → 权限全挂、连禁用插件都做不到。
>
> 产品取舍：**插件即完整应用**，不再要求「先在主程序配好再套皮肤」。
> `main_version_min` 提升到 **3.61.0**。

## 零、P0 鉴权闭环（本版最高优先级）

| 项 | 改动 |
|---|---|
| 桥 `auth-set-token` / `auth-clear-token` / `auth-token-state` | 登录/登出/关鉴权/改密把 token 写穿宿主存储，与 `useAuthStore` 同源 |
| persist | `session_persist=true` → localStorage；`false` → sessionStorage（对齐主程序） |
| 启动 | `auth-token-state` 继承宿主已有 token，插件打开即续用身份 |
| 顶栏身份菜单 | 任意页可登录 / 切换账号 / 登出，不再深埋设置页 |
| 401 / 403 | toast 引导登录；访客态明示 |

## 一、P1 显示设置接真

设置页「顶部导航栏 / 检测中心 / 默认计数器」三卡 `data-disp` 改动即存。
桥 `host-display-get/set` 读写宿主 `localStorage display_settings`，`monitor` /
`navbar` 段深合并（含 `defaultCounters`、`stepTableColumns`），与 `useSystemStore` 互认。

## 二、项目页（v3.56~v3.61）

| 能力 | 键 / 行为 |
|---|---|
| 检测结算方式露出 | `pipeline_config.settlement_mode` first_step / last_step（及 last_first 守门） |
| 空闲超时中断事件 | `idle_timeout_event_id`（空=回退事件 2） |
| 容器定界周期 v3.59 | `custom_cycle_owner=container` + `container_gate_*` |
| 缺步提前发现 v3.56 | `ng_handling.missing_step_early`（hold 档露出） |
| 逐件五件套 + 开始判定 v3.56/v3.60.2 | `per_item.start_by_stability` / `start_labels` / `start_sustain_frames` / `start_conf` |
| 整板重配准 / 动作框扩边 v3.60 | `board_rereg_enabled` / 步骤级 `coverage_margin` |
| 混合逐件虚拟步骤 v3.60 | `custom_mix_per_item_virtual_step` + `_label`；custom+per_item 时露出逐件步骤角色 |
| 装箱清点锚点 v3.56 | `custom_mix_container_stable_anchor_s` |
| OCR / 异常模式 v3.57 | `data-lg="ocr"` / `anomaly` 参数卡 |
| ROI 多块化 v3.57 | 完成本块 / 删除上一块；单块存旧格式、多块存嵌套 |

## 三、MES / 监控 / 模型 / 数据

- MES「多码采集」卡：`GET/PUT /scan-collect/config`（槽位、催扫、待机静默、结算计数、随视觉周期结算、vision_gate、NG 挂起）。
- 监控：scan_collect 徽标条 + 本件扫完 / 按 NG 放行；清零 `scope=cycle`；`start_gate` / `container_gate` 徽标。
- 模型：`capability` / `builtin` 徽标 + `/models/capabilities` 目录摘要。
- 数据：`data_config.record_boxes_data` sidecar；周期表「原片 / 带框」下载 `/data/videos/{id}` 与 `/annotated`。

## 四、已知边界

- Fleet Hub 本身是独立 `hub/` 应用，**不**塞进展会 8 页。
- 自定义布局拖拽编辑器仍走宿主 `monitor_layout.*` KV；本版只接显示开关，不重做拖拽编辑器。
- 插件包需主程序 ≥ v3.61（scan-collect / hub_access 等端点）。
