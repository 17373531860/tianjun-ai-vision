# 15 — Web 集中管控枢纽（Fleet Hub）主程序原生 RFC

> **类型**：ADR / RFC（explanation 象限）
> **本文不讲**：插件平台能力面（见 [00_overview.md](00_overview.md)）、集群 box 聚齐推 MES（见 `debug-cluster` skill）、多显示器 kiosk 体系（见 `debug-electron` skill）、单机触发中心（见 [14_trigger_hub_rfc.md](14_trigger_hub_rfc.md)）。
> **与代码冲突时**：以代码为准，发现请顺手修本文或告知主作者。

> 适用版本：基于 v3.59.0 主线起草，尚未落地。
> 本文目的：把"**一个厂区多台工控机的监控与运行操作集中到局域网 Web 端**"做成主程序原生基础设施，并按设备生态（而非管控台）的方式设计——设备自动发现、能力档案驱动、期望态收敛、跨机场景联动、舰队升级。
>
> **立项背景（决策留痕）**：某客户现场部署 4 台工控机共 12 工位（1×2 + 2×3 + 1×4）。
> 每个工位有独立显示屏（现有多屏 kiosk 体系，只做检测画面显示），但机器多工位共用，操作员没有可用的操作入口。
> 客户诉求：以后所有运行操作都在 Web 端做——"不仅是大脑（监控汇总），还是枢纽（操作中心）"。
> 主作者已定四项边界决策（2026-09-18）：
> ① **无独立 License**——枢纽只是连接器，边缘机授权状态是纳管门槛；
> ② **独立服务器部署**——枢纽跑在专用服务器上，不占用 4 台工控机；
> ③ **范围收窄**——切项目、启停控制、监控、报警处理；**不做参数编辑**（画 ROI / 编步骤 / 调阈值仍在工控机本机做）；
> ④ **对标消费生态设计**——参考华为万物互联（超级终端/分布式软总线）与小米智能生态（米家物模型/本地中枢/场景自动化）的互联哲学，取其模式、去其炫技。
>
> **归位判定**（`feature-placement` A 类基础设施 + D 类底层架构）：多工控机集中管控是多客户通用维度（任何多机部署的客户都会提），且触碰节点注册/鉴权/审计等新架构 → **主程序原生**（同仓库新子系统），同时给插件平台留看板区块 slot 与节点事件 hook，客户级看板定制走插件。
>
> **调研留痕（两轮，2026-09-18）**：
> 第一轮对标工业侧：Cognex VisionView/Edge Intelligence、Keyence VisionTerminal、海康 iVMS/VM、百度 IQI/BIE、LandingAI LandingEdge、Ignition Perspective、WinCC Unified、FactoryTalk、Milestone、安灯类。
> 结论：业界无单一现成对标品——视觉厂商只做小集群远程 Job 切换，云边平台只管模型下发，SCADA 才有写操作安全体系，VMS 才有成熟监控墙；写操作安全纪律直接抄 SCADA 惯例。
> 第二轮对标消费/物联生态：HarmonyOS 分布式软总线（发现→认证→组网→传输分层、LAN 发现走 CoAP 广播而非 mDNS、HiChain 先绑定再组网 PIN/PAKE）、超级终端与 DeviceProfile、米家（MIoT-Spec 物模型三元组、BLE MiBeacon 发现 + miIO UDP token 控制边界即"发现明文、控制加密、绑定需在场证明"、本地中枢断网可用、When-If-Then 场景引擎、家庭权限）、Matter（Node→Endpoint→Cluster 能力自省、Descriptor 按 Device Type 画 UI、PASE/CASE commissioning、multi-admin 显式开窗）、HomeKit（扫码不含 IP、发现走 mDNS）、Home Assistant（Integration/Device/Entity/Area + WebSocket 事件总线）、KubeEdge/Azure/IoTDA（device twin：desired/reported/version 期望态收敛、重连先拉全量再丢弃旧 version）、OPC UA LDS-ME（mDNS 发现 + 永远保留手填 URL）、UNS/ISA-95（厂/线/机/工位主题树）。
> 结论：**枢纽 = 局域网内的设备生态目录与编排器，不是第二台分布式操作系统**——最贴切的一句话定位：**厂区局域网里的工业版 Home Assistant**。华为贡献"设备=能力、先信任再组网、卡片化组合"，小米贡献"物模型、本地中枢、发现≠能控、驾驶舱信息架构"，Matter/KubeEdge 贡献"能力自省 + desired/reported"，HA 贡献"轮询+事件推送混合与实体/区域组织"。
>
> 阅读前置：AGENTS.md 第三节产品决策原则、`debug-operator-license` skill（v3.10 用户系统 / API Key）、`debug-cluster` skill（现有主从心跳）、RFC 14（单机触发中心，4.6 节与之划界）。

---

## 一、动机与现状

### 1.1 现状盘点：离"Web 端"有多远

| 事实 | 代码位置 | 对本 RFC 的含义 |
|---|---|---|
| 生产前端由 Electron 加载本地静态文件，后端不托管 SPA | `electron/main.js → loadMainWindowApplicationUrl`（loadFile app/dist）；`backend/main.py` StaticFiles 仅挂 `/uploads` `/recordings` | 浏览器访问工控机 8001 端口看不到界面，只有 API 与 `/docs` |
| 后端监听 `0.0.0.0:8001`，API/视频端点局域网可达 | `electron/backend-manager.js`（--host 0.0.0.0）；`backend/main.py` uvicorn 入口 | 枢纽→边缘的通路天然存在，缺的是鉴权与守门 |
| CORS 默认白名单仅本机 + Electron 协议 | `backend/main.py` CORS 中间件段（`CORS_ALLOW_ORIGINS` 可覆盖） | 枢纽走服务端转发则不受影响 |
| 集群主从只做 box_serial 聚齐推 MES | `backend/services/cluster_collector.py` 模块头职责注释 | 有"多机、心跳、副机上报"的概念底子，但不是监控/操作通道 |
| 用户鉴权默认关，`/video_feed` `/snapshot` 无权限标注 | `backend/core/auth_deps.py`（auth.enabled 默认 False） | Web 化必须强制开鉴权，否则全厂局域网裸奔 |
| License 只管 Electron 壳；后端仅存缓存 | `electron/license-manager.js`；`backend/api/system_display.py → GET/PUT /system/license-cache` | "未授权连不上"今天并不成立，需要新做纳管守门（见 4.2） |
| 前端全局单 axios 实例，只会连一个后端 | `frontend/src/api/index.js`（VITE_API_BASE_URL） | 枢纽前端必须走聚合层，不能靠现有前端改 baseURL |
| 激活项目端点无"检测中拒绝"后端守门 | `backend/api/projects.py → activate_project`（仅 require_perm，靠前端锁页拦） | 远程化后必须补后端守门，按钮隐藏≠安全 |

### 1.2 可复用资产

| 资产 | 位置 | 复用方式 |
|---|---|---|
| v3.10 用户系统（用户/角色/端点级权限/M2M API Key） | `backend/api/auth.py` `users.py` `roles.py` `api_keys.py` | 枢纽用户体系复用同一套代码；枢纽→边缘用 API Key 认证 |
| v3.48.1 快照轮询（多工位 MJPEG 超限降级） | `frontend/src/views/Monitor/composables/useMultiStreams.js`；`GET /snapshot` | 监控墙 12 路低帧率画面的现成取帧模式 |
| kiosk 只读防护 | `frontend readonlyActions.protectMonitorActions`（v3.58） | 枢纽"只读角色"的前端防护同源复用 |
| 单工位监控组件 | `SingleChannelMonitor`（v3.52） | 单工位下钻页的组件形态参考 |
| 集群心跳/副机上报线程模型 | `backend/services/cluster_collector.py` + `backend/api/cluster.py` | 节点在线判定的语义参考（本 RFC 采用枢纽 pull 模型） |
| 单机触发中心（触发源 × 动作注册表） | RFC 14；`backend/services/triggers/` | 枢纽场景引擎的规则语义与前端面板范式参考（见 4.6） |
| 多平台打包（Ubuntu deb / macOS dmg / 嵌入式 PG） | v3.57 CI | 枢纽服务器安装包直接受益 |
| 归档凭据 Fernet 加密 | v3.53（`debug-video-archive` skill） | 枢纽存储边缘 API Key 的加密模式 |
| 手动结算 / 人工确认 / 清零等运行操作 API | `backend/api/source_routes.py` 各端点 | 枢纽写操作全部代理现有端点，不新造业务语义 |

---

## 二、设计哲学：六条不变量（生态调研的沉淀）

> 借消费生态的模式，必须先翻成工业语言：华为"超级终端"的可移植内核是"设备是能力，不是 IP 列表"；小米"智能生态"的可移植内核是"物模型驱动 UI + 本地中枢保证断网可用"。
> 消费电子里"把多设备合成一台"在工业里是反模式——枢纽挂了，产线必须还能检。

以下六条写死为本 RFC 不变量，后续任何设计/PR 违反即打回：

1. **边缘自治**：枢纽宕机/断网，12 个工位继续检测、继续结算、继续报警、kiosk 屏照常。禁止出现"只有枢纽在线才能开始检测"的依赖。
2. **枢纽是目录与编排，不是分布式 OS**：不借用边缘 GPU/摄像头做跨机推理，不做软总线式 mesh 传输平面。A 机的摄像头永远不是 B 机的视频源。
3. **能力驱动，禁止硬编码版本判断**：枢纽 UI 与操作面只认边缘上报的能力档案（4.3），不写 `if version >= x.y`。未知能力忽略，缺失能力置灰。
4. **发现与信任分离**：mDNS 扫到只是"候选设备"，完成信任绑定（License 校验 + 凭据下发）才入舰队。局域网不是安全边界（米家早期 miIO hello 套 token 是反面教材）。
5. **单控制平面**：一个厂区一个主枢纽；浏览器/大屏/手机都是同一枢纽的视图。不做多枢纽对等纳管（双脑对账分裂），冷备可选但同一时刻只有一个 active。
6. **本地优先的执行位置**：每条能力标注执行点 `edge` / `hub`。检测、结算、单机触发（RFC 14）、单机报警 = edge；跨机通知、舰队 OTA、总览 = hub。（对标米家"能本地执行的自动化下发到中枢落盘，断外网继续跑"。）

---

## 三、总体架构

### 3.1 部署形态

```
     值班室/办公室浏览器 · 车间大屏(kiosk只读) · 手机(响应式)
                      │ HTTPS（枢纽自有用户登录，同权同源不同视口）
                      ▼
        ┌─────────────────────────────────┐
        │  枢纽服务器（独立主机，局域网）          │
        │  tianjun-hub：FastAPI 聚合后端        │
        │  + Vue3 枢纽前端 + 自有 DB            │
        │  节点目录/能力档案/设备孪生/审计/操作锁    │
        │  场景引擎(通知类)/OTA 包仓库            │
        └──────┬──────┬──────┬──────┬─────┘
               │ mDNS 发现（候选）+ 纳管绑定（信任）
               │ 日常：API Key 认证 pull 轮询 + 写转发（HTTPS）
        ┌──────▼─┐ ┌──▼─────┐ ┌─▼──────┐ ┌─▼──────┐
        │工控机-1 │ │工控机-2 │ │工控机-3 │ │工控机-4 │
        │2 工位   │ │3 工位   │ │3 工位   │ │4 工位  │
        │(边缘权威 │ │ 8001   │ │ 8001   │ │ 8001   │
        │+kiosk屏)│ │        │ │        │ │        │
        └────────┘ └────────┘ └────────┘ └────────┘
```

- 枢纽是**新的独立服务**（决策 ②），与主程序同仓库开发、同 CI 发版，技术栈同构（FastAPI + Vue3 + SQLite/PG），复用打包链路。
- 边缘工控机不新增常驻 agent 进程；发现宣告与纳管端点长在现有后端里，挂 `hub_access.enabled` KV 默认关。

### 3.2 连接模型：发现 → 信任 → 会话（借鸿蒙软总线三段式，砍掉传输层自研）

鸿蒙软总线的分层（发现 → 认证 → 组网 → 传输）里，前两段值得抄，后两段不需要——我们已有 REST + HTTPS，不再造 mesh。

| 段 | 机制 | 说明 |
|---|---|---|
| **发现（候选）** | 边缘机开启 `hub_access.enabled` 后 mDNS 宣告 `_tianjun-edge._tcp`（TXT：node_id、版本、api_port、能力摘要 hash、纳管状态，**只放非机密**） | 枢纽"添加设备"页自动列出同网段候选；Windows 原生 DNS-SD 不全，用 zeroconf 库宣告；厂区交换机常禁 mDNS（UDP 5353），实现预留 **UDP 组播 Hello 回退**（miIO hello 同构，仅宣告）；跨 VLAN 需 mDNS 反射器（手册写明）；**手填 IP 永远保留为逃生舱**（OPC UA 规范同款态度） |
| **信任（绑定）** | 一键纳管：校验 License（4.2）→ 版本/能力握手 → enroll 完成 M2M API Key 登记 | **发现报文可伪造**：TXT 只当候选展示、不做路由与信任依据；首次纳管人工核对身份指纹后 pinning（"发现明文、控制加密、绑定需在场证明"——米家 BLE 绑定认证 / HiChain PIN-PAKE 的同款安全模型）。可选**扫码绑定**（P1）：边缘机设置页生成二维码（node_id + 指纹 + 一次性 enroll token 短 TTL），解决"12 台里到底是哪台、防配错机"（HomeKit 式：码不含 IP，发现另走 mDNS） |
| **会话（日常）** | **轮询为真相源 + WS 推送为加速器**：枢纽 pull 轮询边缘 REST（状态/结果/健康/快照），心跳=轮询成败；同时枢纽作为 WebSocket **客户端**订阅边缘 `WS /api/v1/hub/ws`，NG/报警/周期结算/锁变化秒级上墙；WS 断开自动回退纯轮询，**零功能损失** | 对标 HA"WebSocket 事件总线 + REST 兜底"混合；WS 由枢纽发起，边缘仍无主动外连依赖、无上报线程；写操作经枢纽转发 |

- 枢纽前端**只连枢纽后端**，统一鉴权与审计口，CORS 不用动。
- 视频：监控墙走枢纽转发的低帧率快照（12 路 × 1~2 fps JPEG，局域网带宽无压力）；单工位下钻拉高帧率，同时高清路数设上限。
- **不采纳**：BLE/靠近配对（工控机无统一 BLE、车间干扰大）、CoAP 广播自研（mDNS 更贴浏览器生态）。

### 3.3 鉴权链

| 段 | 机制 | 复用 |
|---|---|---|
| 浏览器 → 枢纽 | 枢纽自有用户/角色/token，强制登录（不提供匿名档） | 复用 v3.10 auth 代码栈 |
| 枢纽 → 边缘 | 纳管时签发的 M2M API Key（scope 限定枢纽所需端点面），Fernet 加密落枢纽 DB | 复用 `backend/api/api_keys.py` + v3.53 凭据加密模式 |
| 边缘本机 UI | 不变（客户不开鉴权则本机体验照旧；API Key 通路与匿名档互不影响） | 零配置差异 |

### 3.4 License 守门（决策 ① 的落地）

原则：**枢纽无独立授权；边缘机授权是纳管门槛**。

现状与缺口：License 验签在 Electron 壳（`electron/license-manager.js`），后端只存缓存（`/system/license-cache`），API 通路今天不验授权。
"未授权就连不上"需要新做最小守门：

- 纳管握手端点返回 machineId、版本、license 缓存状态及验签摘要；license 无效/过期 → 握手拒绝，节点不可纳管。
- 已纳管节点 license 到期 → 枢纽降级为"仅监控只读"并在节点卡红标，拒绝写转发。
- 枢纽本身不做验签/激活流程——它连不上未授权的机器，仅此而已。

### 3.5 网络介质与拓扑（有线/无线都支持，但分工明确）

枢纽↔边缘只有四种流量：发现（mDNS/UDP 组播）、REST 轮询与写转发（HTTP）、事件推送（WS）、快照（HTTP JPEG）——全部跑在标准 TCP/IP 上，**不感知物理层**；以太网、Wi-Fi、工业无线网桥、4G/5G CPE 均可承载。

| 段 | 推荐介质 | 说明 |
|---|---|---|
| 边缘机 → 厂区网 | **有线以太网（底线）** | 工控机固定安装有布线条件；检测机连接稳定性直接决定墙的可信度 |
| 枢纽服务器 | 有线，与边缘同交换机/VLAN 优先 | 跨 VLAN 支持，但 mDNS 发现失效 → 反射器或手填 IP 逃生舱（3.2） |
| 人的终端（浏览器/手机/平板） | 厂区 Wi-Fi | 访问枢纽 HTTPS；"在手机打开"深链（4.4）即此场景 |
| 拉不了网线的孤机 | 工业无线网桥 / Wi-Fi 客户端模式 | 三个前置条件：**静态 IP**（DHCP 漂移使纳管 base_url 失效）、**关 AP 客户端隔离**（否则挡 mDNS 与互访）、现场信号评估（车间金属多径干扰） |

无线链路抖动的兜底已内建：WS 断开回退轮询、STALE 灰屏标注、离线指数退避、重连孪生对账（10.2/10.3）——最坏结果是墙变灰几秒，边缘生产不受影响（不变量 1）。
不做：BLE/NFC 近场（4.8）、自研无线 mesh 传输层（不变量 2）。

**全无线部署（客户现场一机拖多屏、希望少拉长线时的支持形态）**：

- 概念澄清写进手册：工位主屏/副屏占的是显卡输出口（HDMI/DP），kiosk 画面本机渲染**不走网络**——屏的数量与网络介质选择无关。
- 枢纽流量预算（单机）：墙快照 本机工位数×1fps×约 100KB ≈ 2~4 Mbps + 下钻偶发约 4 Mbps + 轮询/WS kbps 级，常态 < 10 Mbps/台——Wi-Fi 5/6 工业 AP 充分承载，**"工控机 → 厂区网"这一跳允许全无线**。
- **相机 → 工控机必须保持有线**（本地短线直连/USB/本地小交换机）：RTSP/NVR 每路数 Mbps、GigE Vision 需稳定千兆，比枢纽流量大一个数量级，且丢帧直接打检测精度，不像枢纽丢包只是墙延迟。省长线不省相机短线。
- 全无线四前置：工业级 AP（5GHz 专用 SSID + 信道规划）、边缘机静态 IP、关 AP 客户端隔离、OTA 大包限速或夜间窗（约 1.5GB 是无线链路上唯一大文件场景）。

---

## 四、核心机制设计

### 4.1 能力档案（Capability Profile）——生态化的第一块骨头

> 原型：鸿蒙 DeviceProfile（设备硬件能力+系统特征档案，发起分布式业务的前提）、米家 MIoT-Spec（设备 = service×property/event/action，UI 与自动化都吃同一份 spec）、Matter Descriptor Cluster（客户端自省能力树再决定画什么 UI）。

纳管握手与后续定期刷新时，边缘上报一份结构化能力档案：

| 段 | 内容示例 |
|---|---|
| 身份 | node_id、主机名、machineId、软件版本、**API 契约版本**、License 状态 |
| 工位 | 通道数、每通道 logic_mode（sequential/tracking/per_item/weighing/region_events/ocr/anomaly/custom…）、绑定项目 |
| 外设 | 扫码器 / PLC 连接 / 称重外设 / 报警串口 / 多显示器 / 录像归档 / 多码采集 是否启用 |
| 操作面 | 本节点可用的写端点清单（启停、激活项目、ack、手动结算…），随权限与运行态动态变化 |
| 资源 | 是否在检、操作锁持有者、GPU/磁盘粗指标 |

**消费规则（对应不变量 3）**：

- 工位卡与下钻页**按档案组装**：没启用称重的节点不渲染称重卡（米家"没有窗帘就不显示窗帘"）；有扫码器才显示扫码徽章。
- API 契约版本低于枢纽最低门槛 → 该节点降级"只监控 + 提示升级"，功能置灰而非报错。
- 未知能力字段忽略（向前兼容），缺失字段按无此能力处理。
- 映射关系：**节点 = Matter Node，工位 = Endpoint，能力 = Cluster**。

**兼容契约（借鸿蒙智联认证的工业版，轻量落地）**：新边缘版本进舰队要过一份"枢纽兼容清单"CI 契约测试——profile 字段 schema、健康端点、启停幂等、断连行为、操作锁语义。防止"新版本字段悄悄改名，总览假绿"。

### 4.2 设备孪生（desired / reported）——远程操作可对账

> 原型：KubeEdge Device Twin、AWS IoT Device Shadow（App 只写 desired、设备只写 reported、平台算 delta、version 防乱序、重连先 get 再对账）。

枢纽侧每节点/工位维护一份孪生文档：

```json
{
  "desired":  { "detecting": true, "active_project_id": 12, "upgrade_to": null },
  "reported": { "detecting": true, "active_project_id": 12, "lock": null, "profile_hash": "…" },
  "version": 184,
  "reported_at": "2026-09-18T11:00:00+08:00",
  "online": true
}
```

- 值班室的写操作改的是 **desired**；pull 循环把 desired 收敛到边缘（转发现有端点）。
- 边缘拒绝（如检测中拒绝激活）→ reported 带拒绝原因，界面显示"未对齐 + 原因"，而不是静默失败。
- 断连时界面读 last-reported 并标 **STALE**（灰屏 + 最后上报时间，与"无手不冻帧"同纪律）；重连先全量 GET 对账再算 delta，version 丢弃乱序。
- **冲突策略**：现场锁持有/本机正在操作时，枢纽 desired 排队不强推，锁释放后再收敛——边缘现场永远优先（不变量 1 的写路径体现）。
- **边界**：孪生只放"配置与运行意图"（启停、激活项目、升级意图）；检测结果/视频是遥测，不进孪生。
- 可写入 desired 的键采用**白名单**制，一期仅 `detecting`、`active_project_id`。

### 4.3 写操作安全（沿用第一轮调研的 SCADA 纪律）

**三角色**：

| 角色 | 能力 |
|---|---|
| 操作员 | 看授权范围 + 本工位 ack 报警 |
| 工程师 | 启停、切项目、人工确认、清零（全部经确认对话框 + 审计 + 操作锁） |
| 主任 | 全厂视图 + 强制夺锁 + （P1）报警升级接收人 |

**审计**（枢纽 DB，只增不改）：谁 / 何时 / 哪机哪工位 / 动作 / 旧值→新值 / 来源 IP / 结果。
所有边缘转发收敛到枢纽单一 client 出口并在该层写审计——内部调用绕过审计层等于没有审计（Ignition 旧 write 不进审计的教训）。

**工位级操作锁**：先到先得，TTL 约 2 分钟心跳续期；他端看到"工程师张三 @IP 正在操作工位 3"，主任可强制夺取。
锁只约束**经枢纽的写**；本机操作不经锁，现场紧急停止永远可用（机械指令多控制位惯例）。

**确认梯度（P0 两档）**：

| 操作 | 确认要求 |
|---|---|
| 启停检测、ack 报警 | 确认对话框 |
| 切项目、人工改判、清零 | 确认对话框 + 明示影响（"将工位 3 从《A》切到《B》，当前检测将停止"） |

原因必填、二次输密码留 P1/P2。
**切项目守门**：只列目标机上已有项目；边缘后端拒绝检测中激活（须补守门，见七.1）；失败保持旧项目，禁止半切换（Cognex Offline-换Job-Online 与 Keyence PW+NACK 同款语义）。

### 4.4 监控墙信息架构：从设备列表到产线驾驶舱

> 原型：米家 7.0 全屋页三区（状态区/场景区/控制区）、按房间+功能组织、无设备则不展示、标准 UI 同类设备学一次；鸿蒙智联"万能卡片"（关键状态/操作前置）；HyperOS 常用设备置顶。

```
顶：产线健康条 —— 在线 4/4 · 在检 9/12 · 未确认 NG 数 · 孪生未对齐/STALE 数
中：场景快捷 —— 最多 4 条已启用的枢纽联动规则（默认空，见 4.6）
主：按「产线/车间分组」的工位卡（不是按 IP 排）
    卡片万能字段：快照缩略图 · OK/NG · 当前项目 · 当前步骤 · 锁徽章 · 外设徽章(按档案)
    点卡进入单工位操作台（能力完整面：有称重才有称重区，有扫码才有扫码区）
侧/底：异常优先队列 —— 离线、NG 爆发、磁盘水位、License 到期（自动置顶，不用人翻）
```

- 分组即米家"房间"：产线 A / 包装线 / 备用机；组级"全部停检"必须二次确认 + 逐工位锁检查，**不提供组级一键开始**。
- 宫格上不放写操作按钮，写操作一律进下钻页（Cognex VisionView 同款纪律）。
- 工位卡可拖到"大屏槽位"编排值班墙布局（借超级终端拖拽气泡的交互，不借其硬件互借语义）；复用 v3.54 自定义布局的交互经验。
- 手机端 = 同 API 同 token 的响应式视图（借"应用接力"的体验目标，不做 OS 级流转）；大屏 = 枢纽签发的 kiosk 只读会话。
- 工位卡/报警项提供"在手机打开"**带上下文深链**（P1）：URL 携带工位/周期/报警 ID，扫码或点击直达处理页——鸿蒙任务接续的 80 分体验、20 分成本裁剪，不做会话热迁移。

### 4.5 舰队 OTA（对客户价值最直接的生态能力）

> 原型：米家"检查更新→进度可见→失败可重试"的体验；波次/熔断编排抄 AWS IoT Jobs / Azure Device Update。
> 现状痛点：安装包约 1.5GB，4 台机升级要逐台下载/U 盘安装。

**先泼一盆冷水（机制级调研的修正）**：消费级 OTA 的对象是几 MB 的 MCU 固件，双分区失败可回切；我们的对象是约 1.5GB 安装包 + Nuitka pyd + conda 环境 + 客户 DB 迁移，"失败自动回滚"在这个形态上不存在（IoTDA 也没有真回滚，回滚 = 再发旧包）。所以枢纽做**编排与可见性，不发明新安装器**。

**一期（P1）：版本可见性 + 引导式升级编排**

- 节点卡常显各机版本与"可升级"徽标（版本可见性本身就已消灭"哪台还没升"的现场混乱）。
- 升级动作 = 枢纽下发"拉取此包/此 hotfix id"指令 + 展示逐台进度与失败原因；**执行仍走现有 Inno 安装器与 `create-hotfix` 通道**，包哈希/签名校验后才允许下发。
- 守门：目标机全部工位空闲 + 人工确认 + 失败即暂停（IoTDA `need_confirm` 同款）；**禁止自动跨大版本、不做静默推包**；升级动作走确认 + 审计。

**二期（P2）：波次与熔断**

- 金丝雀 1 台 → 同产线其余 → 全舰队；每波健康检查（进程起、握手过、抽帧成功），失败率超阈自动停波。
- 回滚依赖安装器本地保留上一版（不能假设新包坏网后还能从枢纽拉回滚包）。
- `upgrade_when: idle` 进孪生 desired，由边缘在停检窗口执行。
- 中期演进：整包与"模型/配置热更"分层发布，降低 1.5GB 全量频率（可接现有 `/interconnect` 模型分发）。

### 4.6 枢纽场景引擎（跨机联动）与 RFC 14 的划界

> 原型：米家"如果（触发）×且（条件）×就（动作）"+ 本地中枢落盘断网可用 + 场景模板推荐。

**两层规则，禁止拍平**（对应不变量 6）：

| 层 | 归属 | 例子 | 枢纽宕机时 |
|---|---|---|---|
| 工位内触发 | 边缘 RFC 14 触发中心（不动） | 脚踏板结算、像素虚拟按钮、单机 NG 亮灯 | 照常运行 |
| 跨机/跨人联动 | 枢纽场景引擎（新增） | "3 号机 10 分钟 NG 率 > 5% → 通知主任 + 值班室横幅"；"节点离线 > 30s → 通知"；"磁盘 > 90% → 异常队列置顶" | 联动暂停，边缘生产不受影响 |

- 触发源 = 枢纽 pull 上来的遥测与事件，**不改边缘状态机**。
- 动作白名单一期只开放**通知类**：值班室横幅/声音、短信/微信（复用 v3.46 通道）、写审计、生成"待办"（请求有权限的人处理）。
- **场景不直接执行停检/切项目**——高危写操作必须由人经确认 + 操作锁执行；场景只能把它变成一条待办推到有权限者面前。
- 规则限流与冷却必配（防刷屏），全部默认关；内置模板（离线通知/NG 率/磁盘满）代替"智能推荐"。
- 规则 JSON 语义对齐 RFC 14（when 条件/防抖/冷却/动作序列），现场工程师学一遍两处通用。规则结构示例（When × If × Then，米家场景 2.0 同构）：

```json
{
  "name": "产线A NG 风暴通知",
  "when": { "event": "ng", "scope": "line:A", "window_s": 60, "count": 3 },
  "if": [{ "property": "node.online", "eq": true }],
  "then": [
    { "action": "banner", "params": { "level": "ng", "pin_station": true } },
    { "action": "notify", "target": "director", "template": "ng_storm" }
  ],
  "mode": "single",
  "cooldown_s": 300
}
```

- `mode: single` = 同条规则执行中不重入（风暴抑制）；触发类型含"节点离线持续 N 秒"（IoTDA 端侧规则的离线触发同款）。
- 二期可将仅涉单机的规则**下发到该机 RFC 14 托管**（米家中枢"能本地执行的下发落盘"同构）——4 台机规模一期全在枢纽执行即可，延迟可忽略。

### 4.7 资源树与权限空间（ISA-95 结构，不抄米家家庭扁平分享）

- 资源树 **厂区 → 产线/区域 → 工控机 → 工位** 一期即建（4.4 的"分组"就是这棵树的产线层；对标 HA Area / UNS 主题树 / 米家"房间"，但房间在米家只是 UX 分组，我们的树还要承载授权）。
- 授权绑树（P1）：角色 × 树节点范围，如"一线班长：产线 A 只读监控 + 处理本线报警，不能切项目"；单按钮级粒度（只能停不能开）不做，三角色权限点已覆盖。
- 临时只读票（P1）：给参观/巡检发短 TTL 只读链接，不能写、不能看历史录像（可选），随时撤销立即失效——对应米家"仅查看"共享档。
- 高危操作（组级停检、OTA、强制夺锁）永远不进临时票与操作员角色。
- 外部系统（MES/大屏第三方）要订阅数据时，发独立只读凭据，不做 Matter multi-admin 式多 fabric（单供应商枢纽用不上，代码模型预留 scope 字段即可）。

### 4.8 明确不采纳的生态机制（决策留痕，防止未来跑偏）

| 机制 | 原型 | 不采纳理由 |
|---|---|---|
| 分布式软总线式 mesh 传输平面 | 鸿蒙 DSoftBus | 已有 REST+HTTPS；固定以太网工控机不需要多链路融合，自研传输层纯风险 |
| 跨机能力互借（A 机摄像头给 B 机用、跨机调度 GPU） | 超级终端硬件虚拟化 | 把 12 工位合成"一台超级检测机"= 故障域放大，违反不变量 2；只做只读画面投影 |
| OS 级任务流转 / 跨设备剪贴板 / 键鼠共享 | 鸿蒙 Continuation、HyperOS | Web 场景用"同 API 同 token 不同视口"即达体验目标 |
| 多枢纽对等纳管 | Matter multi-admin | 4 机规模撑不起 fabric；双脑对账分裂；冷备（非 active）即可 |
| BLE / 靠近配对 | 米家畅快连、隔空投送 | 工控机无统一 BLE、车间射频环境差；扫码 + mDNS 已覆盖"选对机器"诉求 |
| 云端场景/远程外网入口 | 米家云场景 | 局域网内闭环（不变量 6）；外网诉求出现另立 RFC |

---

## 五、目标与反目标（汇总）

### 5.1 目标

| 目标 | 说明 |
|---|---|
| **一屏看全厂** | 值班室浏览器同时看到 12 工位画面缩略 + 红绿状态 + 当前项目 + 最近结果，异常自动置顶 |
| **远程运行操作** | 启停、切项目、人工确认、清零、ack 报警——操作员离开机台后的全部日常动作 |
| **生态化接入** | 新机器上线：通电入网 → 枢纽发现 → 扫码/一键纳管 → 能力档案自动渲染功能面，全程不填配置文件 |
| **写操作全程受控** | 三角色 + 审计（旧值→新值+来源）+ 工位级操作锁 + 现场急停永远优先 + 孪生对账可见 |
| **舰队运维** | 集中升级替代逐台 U 盘；健康/License/磁盘水位一屏可见 |
| **边缘零风险** | 4 台工控机现有行为零变化；新增端点挂 `hub_access.enabled` 默认关，未纳管零配置差异 |

### 5.2 反目标

- ❌ 浏览器端项目配置编辑器（画 ROI/编步骤/绑模型/调阈值仍在本机，决策 ③）
- ❌ 独立 License 体系（决策 ①）
- ❌ 云端 SaaS / 外网访问
- ❌ 收编改造现有本机 Electron UI 与多屏 kiosk 体系
- ❌ 4.8 节全部不采纳项
- ❌ 双人复核/电子签名（受控行业客户出现再做）

---

## 六、功能分期

### 6.1 P0（一期：能用的枢纽）

| # | 功能 | 对应机制 |
|---|---|---|
| 1 | 节点纳管（手填 IP + License 守门 + API Key 签发）与资产树 | 3.2 信任段、3.4 |
| 2 | **能力档案握手 + 按档案渲染** | 4.1（生态化的根，一期就上，避免二期返工 UI） |
| 3 | **设备孪生 desired/reported + STALE 标注** | 4.2（远程操作对账的地基） |
| 4 | 12 路监控墙（分组宫格 + 异常置顶 + 产线健康条） | 4.4 |
| 5 | 单工位下钻操作台（高帧画面 + 按档案组装的能力面） | 4.4 |
| 6 | 远程启停 / 切项目（确认 + 空闲守门 + 失败保旧） | 4.3 |
| 7 | 三角色权限 + 写操作审计 + 工位级操作锁 | 4.3 |
| 8 | 全厂报警列表 + ack | 4.3 |
| 9 | 节点健康卡（在线/GPU/磁盘/License/版本） | 4.1 资源段 |
| 10 | 事件通道：轮询真相源 + WS 推送加速器（断开自动回退变频） | 3.2 会话段、10.2 |
| 11 | 资源树结构（厂区→产线→机→工位，即分组） | 4.7 |

### 6.2 P1（二期：生态体验）

| 功能 | 对应机制 |
|---|---|
| mDNS 自动发现候选列表（含 UDP 组播回退）+ 扫码绑定 | 3.2 发现段 |
| 舰队升级一期（版本可见性 + 引导式编排，复用现有安装器/热补丁通道） | 4.5 |
| 资源树授权（产线级只读/工位级操作，角色绑树） | 4.7 |
| "在手机打开"带上下文深链 | 4.4 |
| 枢纽场景引擎（通知类动作 + 内置模板 + 限流冷却） | 4.6 |
| 批量切项目（预览清单 → 逐台进度与失败原因） | 4.3 |
| 锁状态回显工位 kiosk 顶栏（"远程操作中：张三"） | 4.3 |
| 报警超时升级主任（复用 v3.46 短信/微信） | 4.6 |
| 班次报表（产量/NG 率/缺陷 Top/换型记录聚合） | — |
| 临时只读票 | 4.7 |
| 会话空闲回落只读、写操作原因必填 | 4.3 |
| 大屏 kiosk 只读会话 + 工位卡拖拽编排值班墙 | 4.4 |

### 6.3 P2（远期，触发条件出现再做）

OTA 波次/熔断/生产窗（4.5 二期）、场景规则下发单机 RFC 14 托管（4.6）、MQTT UNS 主题树给第三方订阅（有 MES/大屏外部消费者再上）、参数下发+版本+回滚（客户已明确基本不调参，触发了再做）、双人复核/电子签名、模型/配置分层热更（接 `/interconnect`）、产线平面图、完整 OEE、AD/IdP 登录、冷备枢纽。

---

## 七、边缘侧改动清单（最小化，全部默认关）

### 7.1 必须新增

| 改动 | 位置 | 说明 |
|---|---|---|
| 纳管握手端点（如 `GET /api/v1/hub/handshake`） | 新路由文件，登记 `backend/api/router_manifest.py` | 返回 machineId/版本/License 状态/**能力档案**；`hub_access.enabled` KV 默认关，关闭时 404 |
| 能力档案与健康摘要端点（如 `GET /api/v1/hub/profile`） | 同上 | 一次拉齐 4.1 全部段，避免枢纽轮询打爆散装端点；档案 schema 进 CI 契约测试 |
| mDNS 宣告（P1） | 边缘后端启动时按 `hub_access.enabled` 决定是否宣告 | zeroconf 库；关闭时零网络行为 |
| 激活项目"检测中拒绝"后端守门 | `backend/api/projects.py → activate_project_core` | 现状仅前端锁页拦；补后端守门对本机 UI 零影响，对远程是安全底线 |
| OTA 执行器（P1） | 随安装器演进 | 拉包/校验/空闲窗安装/保留上一版回退；Windows 安装重启与检测会话互斥 |

### 7.2 复用不改

启停/结算/确认/清零（`source_routes.py`）、项目列表与激活（`projects.py`）、快照（`/snapshot`）、报警（`alarm.py`）、API Key（`api_keys.py`）、短信/微信通道（`sms.py`）——枢纽全部走现有端点，不新造业务语义。

### 7.3 明确不动

检测状态机、结算链路、MES 通道、集群 box 聚齐（与枢纽并存互不感知）、单机触发中心（RFC 14）、多屏 kiosk 体系。

---

## 八、枢纽侧数据模型（草案）

| 表 | 关键字段 | 说明 |
|---|---|---|
| `hub_nodes` | name, base_url, api_key(Fernet 加密), machine_id, version, license_state, profile(JSON), enabled | 纳管的边缘机 + 最近能力档案快照 |
| `hub_stations` | node_id, channel_id, display_name, group(产线分组) | 工位映射（从档案自动发现 + 可改名分组） |
| `hub_twins` | station_ref, desired(JSON), reported(JSON), version, reported_at | 设备孪生（运行态以内存为准，DB 落底） |
| `hub_audit_logs` | user_id, node_id, channel_id, action, old_value, new_value, source_ip, result, created_at | 审计，只增不改 |
| `hub_station_locks` | station_ref, holder_user, holder_ip, expires_at | 操作锁落底 |
| `hub_scene_rules` | name, when(JSON), actions(JSON), cooldown, enabled | 场景引擎（P1），JSON 语义对齐 RFC 14 |
| `hub_ota_packages` / `hub_ota_jobs` | 版本、哈希、签名、目标节点、状态、失败原因 | OTA（P1） |
| 用户/角色/权限表 | 同 v3.10 结构 | 代码复用 |

---

## 九、接口面草案

### 9.1 边缘侧新端点（全部挂 `hub_access.enabled`，默认关时 404）

| 端点 | 语义 |
|---|---|
| `GET /api/v1/hub/handshake` | 纳管前探测：身份（node_id/machineId/主机名）、软件版本、API 契约版本、License 状态与验签摘要、能力档案 hash。不需要 API Key（只读身份信息，不含敏感数据） |
| `POST /api/v1/hub/enroll` | 信任绑定：入参一次性 enroll token（边缘设置页生成，短 TTL）；边缘复用 `backend/api/api_keys.py` 签发链路生成 scope 限定的 M2M API Key 并一次性回传；枢纽 Fernet 加密留存。重复 enroll 使旧 Key 失效（防蹭网） |
| `GET /api/v1/hub/profile` | 完整能力档案（4.1 五段），响应带 `profile_hash`；枢纽仅在 hash 变化时拉全量 |
| `GET /api/v1/hub/health-summary` | 高频轮询摘要：各工位 检测态/当前项目/最近结果游标/锁观测/GPU/磁盘/在检数，一次拉齐 |
| `GET /api/v1/hub/events?cursor=` | 事件增量拉取（NG/报警/周期结算/项目切换），游标断点续传，断连重连不丢事件——**对账真相源** |
| `WS /api/v1/hub/ws` | 事件实时推送（与 events 同一事件流的推形态）；枢纽为 WS 客户端；断开由 events 游标补账，回退纯轮询零功能损失 |

以上端点走 API Key 认证（`handshake` 除外）；schema 全部进 CI 契约测试（4.1 兼容契约）。

### 9.2 边缘侧复用端点（枢纽转发消费，零改动）

启停（`source_routes.py` 检测控制组）、`GET /api/v1/projects` 与 `POST /api/v1/projects/{id}/activate`、`GET /snapshot`、报警 ack（`alarm.py` / `mes_inbound.py` 清报警）、人工确认/清零/手动结算（`source_routes.py`）。

### 9.3 枢纽侧 API（枢纽前端唯一消费面）

| 组 | 端点示意 | 说明 |
|---|---|---|
| 认证 | `/auth/*` `/users/*` `/roles/*` | 复用 v3.10 代码栈，强制登录 |
| 节点 | `GET/POST/DELETE /nodes`、`GET /nodes/discovered`（mDNS 候选，P1）、`POST /nodes/{id}/enroll` | 纳管向导后端 |
| 工位 | `GET/PUT /stations`（改名/分组）、`GET /wall`（墙聚合状态一次拉齐）、`GET /stations/{ref}` | 墙与下钻的读面 |
| 画面 | `GET /stations/{ref}/snapshot?quality=wall\|detail` | 转发边缘 `/snapshot`，枢纽侧限并发 |
| 操作 | `POST /stations/{ref}/ops/{action}`（start/stop/activate_project/ack/manual_confirm/reset_counters） | 统一写入口：权限 → 锁校验 → 写 desired → 快路径转发 → 审计，单出口 |
| 锁 | `POST/PUT/DELETE /stations/{ref}/lock`、`POST /stations/{ref}/lock/steal` | 见 10.1 |
| 报警 | `GET /alarms`、`POST /alarms/{id}/ack` | 全厂聚合队列 |
| 审计 | `GET /audit`（筛选：人/节点/工位/动作/时间） | 只读 |
| 场景/OTA | `/scenes/*`、`/ota/*`（P1） | 对齐 RFC 14 规则 JSON 语义 |

### 9.4 能力档案 schema 草案

结构对齐"节点 = Node、工位 = Endpoint、能力 = Cluster"的映射（4.1），示例：

采用 **property / action / event 三元组**（对标 miot-spec 的 service×{property,action,event} 与 Matter Cluster 的 attribute/command/event，工业印证 OPC UA 的 Variable/Method/EventType）：

```json
{
  "profile_schema": 1,
  "identity": {
    "node_id": "edge-a1b2", "hostname": "GXJ-01", "machine_id": "…",
    "app_version": "3.59.0", "api_contract": 1,
    "license": { "state": "valid", "expires_at": "2027-01-01", "digest": "…" }
  },
  "stations": [
    {
      "channel_id": 0, "logic_mode": "tracking",
      "properties": [
        { "id": "detecting", "access": ["read", "write", "notify"], "format": "bool" },
        { "id": "active_project_id", "access": ["read", "write", "notify"], "format": "int" },
        { "id": "last_result", "access": ["read", "notify"], "value_list": ["OK", "NG", "WARN"] }
      ],
      "actions": [
        { "id": "manual_confirm", "in": ["disposition"] },
        { "id": "reset_counters", "in": ["scope"] }
      ],
      "events": [
        { "id": "cycle_settled", "args": ["result", "cycle_id"] },
        { "id": "ng", "args": ["reason"] }
      ],
      "peripherals": ["scanner"]
    }
  ],
  "node_actions": [{ "id": "alarm_ack", "in": ["alarm_id"] }],
  "resources": { "gpu_percent": 41, "disk_percent": 62, "detecting_stations": 2 },
  "profile_hash": "sha256:…"
}
```

**消费规则（把 UI 从 if-else 里解放出来）**：

- property 的 `access` 决定渲染与通路：无 `write` → 只读；无 `notify` → 该字段走轮询不走 WS；`write` 属性即孪生 desired 白名单的来源（4.2）。
- `actions` 决定按钮渲染：档案里没有 `manual_confirm` 就不画人工确认按钮（米家"无该 property 则按钮不渲染"同款）。
- `events` 决定 WS 订阅面与场景引擎可用触发源（4.6）。
- **强制簇 vs 可选簇**（Matter Device Type 惯例）：枢纽内置"工位 Device Type 1.0"——强制能力为 `detecting`、`last_result`、启停；缺强制 → 标"不兼容，请升级"，拒绝纳入完整工位卡；缺可选 → 按钮置灰 + tooltip"该工位软件版本不支持"。
- `api_contract` 整数递增，独立于营销版本号——枢纽只比较契约号（不变量 3 的"禁止 if-version"落点）。

---

## 十、关键协议细节

### 10.1 操作锁协议

| 动作 | 语义 |
|---|---|
| acquire | `POST /stations/{ref}/lock` → 201；已被他人持有 → 409 + `{holder_user, holder_ip, expires_at}` |
| renew | 前端进入下钻页操作区后每 30s 心跳续期；TTL 120s，超时自动释放 |
| release | 离开操作区/登出时显式释放；浏览器崩溃靠 TTL 兜底 |
| steal | 主任权限 `POST …/lock/steal`：写审计 + 界面通知原持有者 |

- 所有写操作端点在枢纽侧校验"调用者 == 锁持有者"，无锁且空闲时**自动 acquire**（单次操作不强迫先点"取锁"，降低日常摩擦；锁的存在感只在冲突时出现）。
- 锁是枢纽概念，不下发边缘；本机操作天然不受锁约束（不变量 1）。

### 10.2 轮询节奏（默认值，全部可配）

| 数据 | 周期 | 说明 |
|---|---|---|
| health-summary | 2s/节点 | 心跳兼状态源；连续 3 次失败判离线并指数退避（2s→30s） |
| events cursor | 2s/节点 | 与 health-summary 合并请求亦可（实现细节） |
| profile | 60s 校验 hash，变更才拉全量 | 项目切换/工位数变化即时反映靠 events |
| 墙快照 | 1 fps/工位 | 12 路约数百 KB/s~数 MB/s，局域网无压力 |
| 下钻快照 | 5 fps，同时高清路数上限 2 | 超限降级为墙档 |

STALE 判定：数据超过 3 个轮询周期未更新即标注（灰屏 + 最后上报时间）。

WS 通道健康时上表中 health-summary/events 的轮询自动放缓（如 10s 对账档）；WS 断开立即恢复 2s 档——轮询永远在跑，只变频，保证"WS 只是加速器"不变形为单点依赖。

### 10.3 孪生收敛循环

1. 写操作 → 校验权限/锁 → desired 更新（version++）→ **快路径**：立即转发一次边缘端点。
2. 快路径成功 → 下一轮 health-summary 确认 reported 对齐，界面消除"未对齐"。
3. 快路径失败/边缘拒绝 → 拒绝原因写入孪生与审计；可重试类（网络抖动）进收敛队列按周期重试，重试预算耗尽转"待办"；业务拒绝类（检测中拒绝激活）不重试，等人处理。
4. 节点断连期间 desired 排队；重连后先全量 `GET profile + health-summary` 对账，再按白名单键收敛（Azure twin 重连纪律：先订阅变更、再拉全量、丢弃 version 落后的通知）。
5. **孪生 vs RPC 分工**（Azure"twin 放配置、消息放时序"纪律）：只有**配置/意图类**写走孪生（`detecting`、`active_project_id`，即档案中带 `write` 的 property）；**一次性动作**（ack 报警、人工确认、清零、手动结算、急停类）不进 desired，直接同步 RPC + 回执 + 审计——急停丢进期望态队列慢慢收敛是不可接受的。

### 10.4 传输安全（诚实现状）

- 浏览器 → 枢纽：HTTPS（枢纽安装包自带自签证书，手册写导入信任；客户有内部 CA 则可替换）。
- 枢纽 → 边缘：一期为明文 HTTP + API Key（现状边缘后端无 TLS 栈）；局域网内可接受，但手册明示。边缘侧 TLS 列为 P1 评估项，不阻塞一期。

---

## 十一、枢纽内部模块与前端信息架构

### 11.1 后端模块（`hub/backend/`，与主程序同仓库新顶层目录）

| 模块 | 职责 |
|---|---|
| `node_registry` | 节点 CRUD、enroll、License 门槛、契约版本门槛 |
| `discovery`（P1） | mDNS browser，候选列表 |
| `poller` | 每节点独立 asyncio 任务：health/events/profile 三档节奏 + 退避；任一节点异常不拖累其他节点 |
| `twin_store` | 孪生内存态 + DB 落底 + 收敛循环（10.3） |
| `op_gateway` | **唯一**写出口：权限 → 锁 → desired → 转发 → 审计，一个函数链路收口 |
| `lock_manager` | 10.1 协议 |
| `alarm_center` | 各节点报警聚合 + ack 转发 |
| `scene_engine` / `ota`（P1） | 4.6 / 4.5 |

### 11.2 前端页面（独立 Vite 包 `hub/frontend/`）

登录 / 监控墙（默认页） / 单工位操作台 / 报警中心 / 节点管理（纳管向导 + 健康卡） / 审计查询 / 用户与角色 / 枢纽设置。
组件从主前端复制起步（`SingleChannelMonitor` 形态、快照轮询 composable），抽公共包留二期评估。

---

## 十二、测试策略

| 层 | 做法 |
|---|---|
| 契约测试（CI） | 边缘 `hub/*` 端点 schema 快照测试进主程序 `tests/`；能力档案字段增删必须过契约（4.1 兼容清单的落点） |
| 枢纽单测 | twin 收敛状态机、锁协议、op_gateway 审计完整性（每条写必有审计行） |
| 多边缘 e2e | synthetic 剧本源起 2~3 个边缘实例（独立端口+独立 DB，沿用 `start-dev-servers` skill 副本隔离纪律）+ 1 枢纽：纳管 → 墙 → 启停 → 切项目 → 双人锁冲突 → kill 边缘看 STALE → 重连对账 |
| 故障注入 | kill 边缘进程、防火墙丢包模拟断网、改 License 缓存为无效验证降级只读 |
| UAT（路径 H） | 验收标准十五节 1~8 逐条可见浏览器走一遍，三件套证据 |

枢纽前端改动同样适用工作守则 T 流水线（T4/T5/T6 对 `.vue` 强制）。

---

## 十三、里程碑

| 里程碑 | 内容 | 备注 |
|---|---|---|
| M0 | 边缘三件套：handshake/profile/health-summary 端点、`hub_access.enabled` KV、激活守门 | ✅ **已落地**（2026-09-18，`backend/api/hub_access.py` + `tests/test_hub_access.py` 10 例全绿）。enroll 自动纳管流程挪 M1——M0 用现有 API Key 管理页手动签发 scope=hub 的 key，鉴权链路等价；档案顶层键定为 `profile_schema`（`schema` 是 pydantic 保留名） |
| M1 | 枢纽骨架：auth + 节点纳管 + poller + 孪生 + 审计（无 UI，API 可测） | ✅ **已落地**（2026-09-18，`hub/backend/` 9 模块 + `tests/hub/` 14 例全绿）。独立服务不 import `backend.*`；纳管门槛（License 非 valid 拒绝 / 契约超上限拒绝）、每节点 asyncio 循环（2s 心跳/3 失败判离线/指数退避/60s 档案 hash 校验）、孪生 reported 变更才落库 version++、登录成败与节点增删全审计；测试用 `httpx.ASGITransport` 进程内直连主程序 app 当边缘机（含真实循环冒烟）。启动：`uvicorn hub.backend.main:create_app --factory --port 9100` |
| M2 | 监控墙 + 单工位下钻（按档案渲染，只读） | ✅ **已落地**（2026-09-18）。`GET /wall` 聚合 + 异常队列（离线/滞后/License）+ 工位档案 `properties/actions`；`GET/PUT /nodes/{id}/stations/{ch}` 下钻读面与枢纽本地改名分组；快照转发限并发 8。前端独立 Vite 包 `hub/frontend/`：登录 / 墙（按产线分组）/ 单工位只读操作台 / admin 纳管对话框。启动：后端 `--port 9100`（有 dist 则同端口分发），开发 `npm run dev` → 9101。测试：`tests/hub/test_hub_m2_wall.py` + `test_hub_m2_e2e.py` |
| M3 | 写操作闭环：锁 + 启停 + 切项目 + ack + 确认梯度 | ✅ **已落地**（2026-09-18）。边缘新增 `POST /hub/ops`（启停/切项目，API Key 鉴权自成体系——不转发本机 `require_perm` 端点，避免鉴权体系错配与空 body 重置 conf/iou 的雷；start 对齐 `_auto_start_detection` 门槛语义 + 幂等，activate 复用检测中 409 守门）+ `GET /hub/projects` 精简列表；档案登记工位 actions（启停）与 node_actions（切项目）。枢纽侧：`lock_manager`（TTL 120s / 写时自动获取续期 / 他人 409 / 过期即无锁 / steal 需 `lock.steal` 且必审计）+ `ops.py` 操作网关（权限→锁→档案白名单→desired 落库→转发→审计，边缘拒绝原样透传不重试）+ station_detail 带锁态与动作；前端操作台（按档案渲染按钮 / danger 确认梯度 / 切项目下拉 / 锁徽章与夺锁）。ack 报警挪 M4（边缘缺 hub 侧 ack 端点，随多边缘 UAT 一起做）。测试：`tests/hub/test_hub_m3_ops.py` 12 例 + 边缘侧 9 例 + `test_hub_m3_e2e.py` 全链路浏览器闭环（契约级假边缘 `tests/hub/fake_edge.py`） |
| M4 | 多边缘 e2e + UAT + 枢纽安装包（打包链路复用） | ✅ **主体已落地**（2026-09-18）。① ack 报警（M3 顺延）：边缘 `/hub/ops` 加 `ack_alarm`（对齐 `POST /alarm/stop` 语义幂等，不碰 mes_inbound 在途报警横幅——那是外部 MES 对接契约）；枢纽网关按 §10.3-5 纪律**不写 desired**（一次性 RPC）；前端按钮由档案自动渲染零改动。② 多边缘：`tests/hub/test_hub_m4_multi.py`（3 假边缘并管：故障隔离/操作定向/锁按工位隔离/消警定向）+ `test_hub_m4_e2e.py`（双边缘浏览器闭环）；`fake_edge.py` 支持多工位。③ UAT：`tests/uat/hub_m4/run_uat.py` 还原客户现场 4 机 12 工位拓扑（1×2+2×3+1×4），headed+录像+8 截图+run.log 三件套全过。④ 交付形态：`hub/requirements.txt`（无 cv2/torch 依赖链）+ `hub/README.md` 部署手册（五分钟部署/纳管流程/环境变量/角色安全/systemd 模板）——**CI 安装包未做**（目标 OS Windows/Ubuntu 未定，pip+systemd 已可交付，待主作者拍板再接打包链路）。⑤ P0 收尾（2026-09-19）：**事件通道一期**——边缘 `GET /hub/events`（游标增量拉 `DetectionCycle`，NG 过滤在边缘做，next_cursor 覆盖被过滤行）+ 枢纽 `HubEvent` 表（`(node_id, edge_event_id)` 唯一约束防重放）+ poller 4s 变频拉取（`event_cursor=NULL` 表示从未拉过 → "从现在订阅"不翻纳管前历史帐，**不能用 0 兼任**——空边缘首拉后游标就是 0 会把第一批真实事件当历史跳过，测试期踩过的真 bug）+ 环形保留 `EVENT_KEEP_MAX`（默认 1 万，超限删最老）+ 枢纽 `GET /events`（聚合/筛选/未 ack 计数）与 `POST /events/{id}/ack`（幂等记名 + 审计 `event.ack`，需 ops.execute）；WS 加速器仍归 P1。**报警中心页** `AlarmsView`（NG 列表/只看未处理/确认处理/点节点直达工位）+ 墙顶栏未处理徽标。**用户管理**——`GET/POST /users` + `PUT /users/{id}`（admin CRUD/角色/启停/重置密码，最后活跃 admin 防禁用防降级，改状态/重置密码吊销全部 token）+ `POST /auth/change-password`（本人改密验旧密、吊销全会话引导重登）+ `UsersView` 页与顶栏改密对话框。测试：`tests/hub/test_hub_m4_events_users.py` 9 例（游标断点/去重/NG 过滤/保留上限/ack 流/CRUD 防呆/改密吊销）+ `test_hub_m4_events_e2e.py` 浏览器闭环（注入 NG→徽标→ack→建用户→新账号改密重登）。**P0 至此全部落地**；遗留仅 CI 安装包（待主作者拍板目标 OS） |
| UI 打磨批次 | 视觉语言统一（2026-09-19，用户美化反馈驱动） | ✅ **已落地**。三方并行调研（ISA-101/HP-HMI 工业规范、Datadog/Grafana/Uptime Kuma 舰队产品竞品、Material3/Linear/Primer 暗色设计系统）交叉裁决后一次性落地：① **对比度合规**——主按钮白字 on `#00a8ff` 仅 2.6:1 不过 AA，改深字 `#0f172a`+600 字重；`#64748b`（text-4）退出可读文字仅留 placeholder/disabled。② **绿退出常态**（ISA-101 正常态灰度安静）——"检测中"徽标去绿底改中性+左活动细条、License 有效/用户启用/在线态全部中性化，绿只留操作回执（op-notice）。③ **三态拆色+形状冗余**——离线红/滞后黄分离（原先都红）；状态点形状编码：在线空心灰环/滞后琥珀菱形/离线红方块（色盲不依赖颜色）。④ **墙顶 KPI Stat 条**——未处理报警/离线/滞后/在检/节点·工位五块大数字（22px/600/tabular-nums），异常才上色；节点卡按最差状态左 3px 色条+异常置顶排序。⑤ **状态徽标 muted 化**——15% 透明底+亮字+同色淡边，实底红只留危险按钮与徽标计数。⑥ **表格纪律**——行高 40px、hover 实色 `#263449`、行线发丝线 `rgba(148,163,184,.12)`；报警中心未处理行左红条+极淡红底+置顶（默认仍显示全部——E2E ack 后断言 acked-by 依赖行保留）。⑦ **排版**——正文 14/20、中文 letter-spacing 0、`font-synthesis: none` 防 faux bold、数字列 tabular-nums、标题 600 不用 700。⑧ **微交互**——`:focus-visible` 双层环、150ms 统一缓动、`prefers-reduced-motion`、模态升 surf-3 `#2d3b50`+浮层专属阴影。全部 token 收敛在 `App.vue :root`（surf-0~3 四档表面 + 文字四阶 + 状态 muted 三件套）。回归：53 例全绿（含 4 条 Playwright E2E），8 页截图审计归档 `test-results/beauty_audit/`。**全状态矩阵审计**（同日二轮）：20 态截图逐一验收归档 `test-results/state_audit/`（登录错误/报警空态/在检+长名截断/滞后窗口/离线+99+ 徽标/他人持锁+夺锁/danger 确认框/切项目下拉/操作回执/离线工位页+License 异常/404/报警混排+筛选/重名与最后 admin 防呆/operator 只读三页/改密错误），模拟手法：monkeypatch `OFFLINE_AFTER_FAILURES` 制造"在线但滞后"停留窗口、kill 假边缘判离线、第二账号 API 上锁。审计揪出并修复 3 个问题：① **路由参数复用 bug**（真 bug）——`StationView` setup 一次性读 `route.params`，URL 从工位 A 直接切工位 B 组件复用不刷新，`App.vue` router-view 加 `:key="$route.fullPath"` 强制重挂载；② 离线工位页"画面加载中"占位与离线遮罩文字重叠，遮罩在场时不画占位；③ 报警中心长流水（99+）表头滚走，table-card 改卡内自滚 + th sticky 吸顶（border-collapse 下 sticky th 边框不跟随，用内阴影画底边）。顺带 License 横幅文案中文化（"License 异常（expired），已降级只读"）。修后三轮审计+53 例回归全绿 |
| M5 | 数据中心（生产统计与趋势分析，2026-09-20） | ✅ **已落地**。三方并行调研（制造 KPI 口径与暗色图表规范 / 边缘-中心数据聚合架构 / 舰队看板产品形态：Ignition·ThingWorx·Tulip·SAP DM·Grafana·MachineMetrics 等 20 组检索）交叉裁决后一次落地。**边缘**：`GET /hub/events` 载荷扩展 `duration_ms`/`project_id`/`project_name`（session 侧 outerjoin 取项目，容忍项目已删）。**枢纽数据链路**：`HubNode.cycle_cursor` 独立统计游标（与报警通道 `event_cursor` 互不干扰，NULL/0 语义同教训）→ poller 5s 拉全量周期 OK+NG（页 500×单轮 4 页追赶预算）→ `hub_cycles` 明细表（`(node_id, edge_cycle_id)` 唯一防重放，90 天保留）+ 写入即标脏 `hub_rollup_dirty`（node×小时粒度）→ rollup worker 20s 消费脏桶**整桶 GROUP BY 覆盖写**（先删后插，迟到数据/游标重放/枢纽重启天然幂等，不做增量 +1）两张小时桶 `hub_cycle_hourly`（工位×项目×小时：分子分母+耗时和）与 `hub_ng_hourly`（工位×规整原因×小时）→ retention 线程小时级清理（明细 90 天/桶 2 年，全部 env 可配）。**口径铁律**（调研裁决）：合格率永远 sum(ok)/sum(total) 件数加权在查询层算、禁止平均工位比率；分母 0 回 None 画"—"不回 0；时序服务端填桶——零产画零柱、无数据 yield null 断线不插值；NG 原因规整（空→事件名→"未知原因"，截 120 字符）。**查询面** `stats.py` 六端点（全部 wall.view 只读）：summary（KPI+环比等长窗口+catching_up 脏桶标志）/ timeseries（hour|day 自动粒度，本地时区切日）/ pareto（TopN+Other+累计占比）/ ng-matrix（原因×工位交叉）/ stations（全工位出行含零产、合格率升序最差在上、spark 桶对齐）/ cycles（明细分页，规整原因前缀匹配+"未知原因"双空特判）。**前端** `DataView.vue` 三 Tab：总览（KPI 卡×5 环比 Δ 箭头+产量柱/合格率线双轴趋势+Pareto+工位排行表 SVG sparkline）/ NG 分析（Pareto 点条筛选+交叉表热力点行筛选+样本表）/ 周期明细（result 过滤+分页+空态）；筛选条时间五预设×节点×工位全局生效（Grafana 惯例）；ECharts 按需注册（全量 1.1MB→573KB 懒加载 chunk）+ `EchartsPane` 通用容器；CSV 导出当前 Tab 当前筛选（BOM 头 Excel 中文）；60s 慢刷不与监控墙抢实时。**一期明确不做**（调研裁决记档）：OEE/可用性、班次维度（小时桶天然支持后续任意切分）、同比、趋势环比虚线叠加、定时报表、成本加权 Pareto、边缘对账通道。测试：`test_hub_m5_stats.py` 13 例（游标独立/首拉订阅/去重/迟到幂等/保留清理/六端点口径含分母 0 与填桶）+ `test_hub_m5_e2e.py` 浏览器闭环（注入周期流→KPI/趋势/排行→Pareto 点选→交叉表筛选样本→明细过滤→空态+tab 高亮回归断言）+ 边缘契约 23 例（载荷新字段断言），hub 全套 90 例绿 |
| M6 | 检测集群全景网格（2026-09-22，用户诉求驱动） | ✅ **已落地**。① **改名**：「监控墙」全面更名「检测集群」（墙顶栏标题 + 报警中心/数据中心/用户管理/工位页返回按钮 + hub/README + 主程序 AuthPanel tooltip；路由名/data-test 不动零回归风险）。② **全景网格视图**：与「按节点分组卡」并列的第二形态——全部节点的全部工位跨节点摊平进一张网格（对齐主程序超多工位总览体验），规格 2×2/3×3/4×4/6×6/9×9 可选，**N×N=每页 N² 格**（与主程序语义一致）超出翻页（‹ 页码 › 控件，切规格页码防越界）；6×6 起密集档（信息条收薄/徽标缩小/间距 6px，电视墙远看画面为主）；瓦片带「节点名·工位名」（无分组容器时身份自明）+ 检测中/待机徽标 + 离线遮罩，点击直达工位下钻；摊平**保持节点原始顺序不做异常置顶**——值班员靠格子位置记工位，乱序反而找不到；视图模式与网格规格 localStorage 记忆（电视墙重启保持）。快照轮询复用 useSnapshot（每瓦片 1s），后端零改动（/wall 聚合与快照转发现成）。测试：`test_hub_m6_grid_e2e.py`（改名断言/双视图切换/2×2 翻页/9×9 单页/瓦片下钻/返回模式记忆），hub 全套 91 例绿 + live demo 4 节点 11 工位真视频实拍验收 |
| M7 | 电视墙无人值守 + 节点管理/审计台账页（2026-09-22，产品完备性自查驱动） | ✅ **已落地**。三方并行调研（电视墙值班惯例：海康 iVMS 轮巡/大华报警上墙/Milestone Carousel/Genetec kiosk/Blue Iris/Frigate/Andon 大屏 UX；舰队管理功能面；主程序↔枢纽差距内查）后按 P0 清单补齐。**电视墙批次**（全前端，后端零改动）：① 顶栏走秒时钟+日期周几（电视墙模式放大 22px，Andon 远距可读惯例）；② 全景网格**自动轮巡翻页**（5/10/15/30/60s 可选默认 15s，人工翻页重置计时防"刚翻就被翻走"，localStorage 记忆）；③ **NG 置顶条**——最新 3 条未确认事件红条甩眼可见（时间/节点·工位/原因），整行点击直达工位处理（大华报警上墙的 Web 化）；④ **电视墙模式**——一键隐藏全部管理入口只留时钟+未处理徽标+全屏（Fullscreen API）+ **Wake Lock 防休眠**（页面隐藏重拿锁），深链 `#/?tv=1` 直进墙态供开机自启 kiosk 浏览器；⑤ **单格点击先放大**（NVR 惯例取代直接跳路由）——放大层 2fps 提帧（总览 1fps/放大提帧分层惯例）+ Esc/点罩退出 + 「进入工位」下钻；⑥ 画面 object-fit 由 cover 改 **contain 保比留黑边**（Genetec boxed——检测画面变形误导判断）；⑦ 新报警**提示音**（默认关，Web Audio 两短音克制、最短间隔 10s 防疲劳、新增未确认才响）。**节点管理+审计页** `NodesView`（`/nodes`，admin+director 可见）：节点表（状态/名称/地址/主机/版本/工位数/License/最近异常 + **版本不一致高亮**——与多数版本不同标黄）、编辑弹窗（改名本地生效；**改地址/换 Key 先 handshake 验证再落库**，目标机器 node_uid 不符 409 拒绝防呆——后端新增 `PUT /nodes/{id}`）、立即轮询、移除确认；**审计台账 Tab**（此前只有后端无 UI）：节点×动作双筛选（动作名与 `write_audit` 实际值一一对应含 `ops.*` 前缀族）+ 变更 old→new 摘要悬停全文 + 50 条分页。测试：`test_hub_m7_tvwall_e2e.py`（时钟走秒/轮巡自动翻页/放大层 Esc/NG 置顶条点击直达/电视墙模式深链）+ `test_hub_m7_nodes_e2e.py`（节点表/改名/坏地址不保存/立即轮询/审计筛选/移除），点击链路改动同步 m3/m4/m6 三处回归（点格→放大层→进入工位），hub 全套 70+ 例全绿 |
| M7.5 | 运维告警链路 + 孪生指标补齐（2026-09-22，同批调研 P0 收尾） | ✅ **已落地**。**上下线历史**：新表 `hub_node_status_events`——poller 判离线/恢复时记账（`_transition`：unknown→online 初始转换不记防枢纽重启刷噪音；恢复行带 `duration_s` 离线秒数），`GET /nodes/status-events` 时间倒序分页 + 每节点近 7 日断连次数/累计离线秒聚合；NodesView 新 Tab「连接历史」（7 日统计 chip + 切换记录表 + 节点筛选）。**通知出口** `hub/backend/notify.py`：通用 Webhook / 钉钉机器人（加签 HMAC-SHA256）/ 企业微信机器人三通道（全是 HTTP POST JSON），规则一条——节点失联 ≥ N 分钟外推告警（默认关、阈值默认 5min）+ 恢复补发（仅当离线侧真的发过，防噪音）；`_notify_loop` 15s 巡检，**顺序纪律**：先销恢复行再判离线行——阈值内闪断两行一起静默销账一条不发；通知关闭期事件静默销账防开启时洪水外推；配置存新表 `hub_settings` KV（`GET/PUT /notify/config` + `POST /notify/test` 逐通道试发，admin 专属）；NodesView 新 Tab「通知设置」（阈值/恢复开关/通道增删/试发结果逐条可见）。**孪生指标补齐**（内查差距 B1）：poller 不再丢弃摘要里的 `source_type`/`fps_inference`（入孪生 reported），边缘 `_STATION_PROPERTIES` 补两条只读声明（capability-driven UI 自动渲染，profile_hash 变化枢纽 60s 内自动拉新档案）——工位下钻页新增「视频源」「推理帧率」两行；节点表新增**资源列**（CPU/内存/盘百分比，≥90% 标红，fake_edge resources 结构同步对齐真边缘 `{percent:…}` 字典形）。**e2e 竞态修复**：m4_events 徽标偶发超时定因——事件通道首拉"从现在订阅"语义会吞掉首拉前注入的事件，runtime snapshot 透出 `last_event_pull` 可观测点，测试注入前先等首拉落游标。**工位实时投影 `/hub/live`**（内查差距 C1~C6 治本——单工位下钻此前信息面薄）：边缘新端点复用 `get_detection_results` 聚合逻辑做**白名单裁剪**（计数器/步骤进度含 in-flight 秒数/周期节拍 当前·平均·上周期/最近 5 事件/tracking 清点摘要；截图、检测框、配置 JSON 等大载荷全剥），**按需调用不进 poller**——枢纽只在下钻页开着时经 `GET /nodes/{id}/stations/{ch}/live` 以 2s 轮询转发，无人看零开销；StationView 视频下方新增「生产实况」面板（六 KPI + 步骤进度点亮：本周期已完成 ✓/进行中脉冲 + 最近事件 NG 标红）。测试：`test_hub_m7_opsalarm.py` 9 例（切换记账/幂等外推/闪断静默/关闭不积压/权限/PUT nodes 身份防呆/钉钉加签/live 投影 fake+真主程序双链路含大载荷剥离断言）+ nodes e2e 扩三 Tab 断言 + m3 e2e 补实况面板断言，hub 全套 79 例全绿 |
| M8 | 部署整备 + WS 推送加速（2026-09-22，P1 批次收口） | ✅ **已落地**。**部署整备** `hub/deploy/`：`install.sh` Ubuntu/Debian 一键安装（python3.10+ 校验/缺 dist 就地构建/系统用户/venv/systemd 开机自启/健康探活，**幂等重跑=升级**、数据目录不动）+ `tianjun-hub.service`（安全收敛：NoNewPrivileges/ProtectSystem=strict/只写数据目录）+ `hub.env.example` → `/etc/tianjun-hub.env` + `backup.sh` **在线一致性备份**（SQLite backup API 快照不停服 + Fernet 密钥 + 14 份轮转，crontab 每日；已真跑验证备份→恢复→integrity ok 4 节点 3455 周期无损）+ `Dockerfile`（多阶段 node 构建→python3.10-slim 运行，HEALTHCHECK /health）+ `docker-compose.yml`（named volume）；README 部署节改三选一（脚本/Docker/手动）+ 部署资产清单表。**WS 推送加速**（RFC §10.2 P1 兑现）：架构纪律=**轮询是真相源，WS 只发提示帧** `{"topic":"wall"|"events"}` 不带业务载荷——`hub/backend/ws.py` WsHub 连接管理（token 查询参数鉴权复用登录体系、坏 token 4401、per-topic 0.3s 节流合并+pending 补发保证最后一次变化不被吞、无连接零开销），poller 三触发点（孪生 reported 变化/节点上下线切换/新 NG 事件落库）；前端 `useWsHint` composable（同源/跨端口自适应 ws(s):// 拼接、断线指数退避 2s→30s 静默重连、期间纯轮询兜底零功能损失），WallView 全量提示即刷、AlarmsView 只认 events。效果：工位状态翻转/新 NG 从最坏 2~4s 轮询延迟变准实时。测试 `test_hub_m8_ws.py` 3 例（4401 拒绝/孪生变化推 wall·NG 推 events·离线切换推 wall 全链路/无连接零影响），真浏览器验证连接+收帧，hub 全套 82 例全绿 |
| M9 | 批量操作 + 报警升级（2026-09-22，P1 批次收口） | ✅ **已落地**。**批量操作**（RFC §4.3「批量切项目」兑现 + 批量启停扩展）：架构纪律=**不新增任何后端写路径**——前端 `BatchOpsDialog.vue` 编排逐台调用现有唯一写网关 `POST /nodes/{n}/stations/{ch}/ops`（锁互斥/能力白名单/审计逐条落账全自动复用，批量本质是"代替人手连点"）；三动作：批量开始/停止检测（选中节点全部工位逐工位下发）+ **批量切项目按项目名跨机匹配**（各边缘 project_id 空间独立、项目名才是跨机通货——执行时逐台 GET /projects 按名找本机 id，无同名项目该台明确报"本机无同名项目"不误切、已激活跳过），候选项目名=选中节点并集 datalist；流程=选动作→选在线节点（离线不可选）→预览清单（工位级动作逐工位一行/切项目每机一行）→执行（逐行 pending→执行中→✓/✗+失败原因悬停可见）→汇总"N 成功 M 失败"；入口墙头部「批量操作」engineer+ 可见，切项目 danger 红钮。**报警升级**（RFC §4.6「超时升级」兑现）：notify 配置新增 `alarm_escalate_min`（NG 未确认超 N 分钟升级外推，0=默认关）+ `alarm_escalate_cooldown_min`（冷却窗默认 30 分钟内最多一条**汇总**——"未处理 NG x 条，最老挂 y 分钟"防每条一响的通知疲劳），poller `_escalate_unacked` 挂在 notify 循环内、冷却时间戳持久化 `HubSetting[alarm_escalate_last]` 重启不重置，复用同一批通知通道（webhook/钉钉/企微）；NodesView 通知设置 tab 新增两输入框。测试：升级规则 4 断言（关=不发/超阈值发汇总含 unacked+oldest_age/冷却拦重发/ack 清账后不再触发）+ 批量 e2e 3 幕（批量启动 3 工位全 ✓ 边缘状态翻转/按名切项目两机同切/无同名项目逐行报因不误切），hub 全套 84 例全绿 |
| P1 批次 | mDNS 发现/扫码、OTA 一期、场景引擎、kiosk 锁回显、班次报表… | 按 6.2 另排期 |

---

## 十四、风险与开放问题

| 风险/问题 | 应对 |
|---|---|
| 枢纽单点故障 | 不变量 1 兜底：边缘生产不依赖枢纽；恢复后孪生对账收敛；高可用留 P2 冷备 |
| mDNS 跨 VLAN 静默失败 | 发现只是加速器（不变量 4），手填 IP 永远可用；手册写明反射器配置 |
| 边缘开启 auth 的现场迁移 | 纳管向导引导开启 + 自动签发 API Key；本机匿名档不受影响需在 T5 验证 |
| 12 路快照轮询对边缘 CPU 增量 | 快照端点已有按工位共享 JPEG 编码（v3.57）；枢纽轮询频率可配，默认保守 |
| 审计被内部调用绕过 | 全部边缘转发收敛到单一 client 出口，在该层写审计 |
| 场景规则刷屏 | 限流 + 冷却必配字段；动作白名单只有通知类；默认关 |
| OTA 半途坏机 | 一期只做逐台 + 人守着升；回退靠安装器本地保留上一版，不依赖网络 |
| 能力档案 schema 漂移 | CI 契约测试（4.1 兼容契约）；未知字段忽略 |
| 前端组件复用方式 | 枢纽前端独立 Vite 包；与主前端共享组件从复制起步，抽公共包二期评估（开放） |
| 4 台机版本不一 | 档案带 API 契约版本；低于门槛只读纳管 + 提示升级（禁止 if-version 散落业务代码） |

---

## 十五、一期验收标准

1. 主任在值班室浏览器同时看到 12 工位红绿状态与画面缩略，离线/NG/STALE 自动置顶，一眼可辨。
2. 工程师不到机台前完成"停检 → 切项目 → 开检"闭环，全程确认对话框，孪生显示对齐过程。
3. 任一写操作在枢纽审计表可查：谁、何时、哪工位、旧值→新值、来源 IP、结果。
4. 两名用户同时操作同一工位，后者看到锁持有者信息且写被拒；主任可强制夺锁且留审计。
5. 未授权（License 无效）的边缘机无法纳管；已纳管机器 License 失效后降级只读。
6. 没启用称重/扫码的节点，枢纽界面不出现对应卡片；低版本节点功能置灰提示升级而非报错。
7. 拔掉枢纽服务器网线：4 台边缘机检测、kiosk 屏、MES 推送、单机触发全部照常；重插后孪生对账收敛，期间界面全程标 STALE。
8. 边缘机不开 `hub_access.enabled` 时，全部行为与升级前零差异（含无 mDNS 宣告）。

---

**本文件最后更新**：2026-09-22（v8：M6 检测集群全景网格——监控墙更名检测集群 + 跨节点全工位摊平网格（N×N 每页 N² 格翻页/密集档/localStorage 记忆/瓦片直达下钻），hub 91 例绿。上一版 v7：M5 数据中心——三方调研（KPI 口径/聚合架构/看板产品形态）裁决后落地：边缘事件载荷扩 duration/project、枢纽独立 cycle_cursor 全量拉取、hub_cycles 明细+脏桶幂等重算两张小时桶、/stats 六端点（加权合格率/填桶断线/Pareto/交叉表）、DataView 三 Tab（KPI 环比/双轴趋势/工位排行 sparkline/NG 分析点选联动/明细导出 CSV），hub 90 例全绿。上一版 v6：UI 打磨批次——三方调研（ISA-101 HMI/舰队竞品/暗色设计系统）驱动的视觉语言统一：主按钮深字过 AA、绿退出常态、三态拆色+形状冗余、KPI Stat 条、徽标 muted 化、表格 40px 行高+未处理左红条、tabular-nums、focus-visible 双环；token 收敛 App.vue，53 测试全绿。上一版 v5：M4 收尾——事件通道一期（游标增量 + NULL/0 语义分离 + 环形保留）、报警中心页与未处理徽标、用户管理（admin CRUD/最后 admin 防呆/本人改密吊销会话），P0 十一项全部落地，遗留仅 CI 安装包。上一版 v4：并入机制级补充调研（软总线 CoAP 发现/HiChain、miot-spec 三元组与 access、MiBeacon-miIO 安全边界、Matter Device Type 强制/可选簇、HA WS 事件总线、Azure/IoTDA 影子纪律与 OTA 现实）——能力档案升级 property/action/event 三元组、事件通道改"轮询真相源+WS 加速器"混合、孪生与 RPC 分工明确化、OTA 一期收敛为版本可见性+引导式编排、场景规则示例与风暴抑制、资源树 P0/绑树授权 P1、手机深链 P1。上一版 v3：补齐工程落地层——接口面草案（边缘 hub/* 五端点 + 枢纽 API 组 + 能力档案 schema 示例）、协议细节（操作锁/轮询节奏/孪生收敛/传输安全现状）、枢纽模块划分与前端 IA、测试策略、里程碑 M0~M4。上一版 v2：并入华为万物互联/小米智能生态第二轮调研——六条不变量、发现→信任→会话三段式、能力档案、设备孪生、驾驶舱 IA、舰队 OTA、枢纽场景引擎、不采纳清单）
**维护者**：项目主作者 + AI agents
