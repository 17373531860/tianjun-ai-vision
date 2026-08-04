# NG 短信推送：独立 AT 工具与主程序双通道

> **类型**：how-to
> **本文不讲**：视觉检测状态机改造、现有灯塔 Modbus 配置、MES Gateway 对接、阿里云官方 SDK/签名实现
> **与代码冲突时**：主程序以 `backend/services/sms_*`、`backend/services/sms_providers/*` 为准；独立 AT 工具以本目录代码为准

## 产品定位

这是一项面向多个客户的通用可选能力。主程序软件侧现已支持 `at_modem` 与 `generic_http` 两种 Provider，操作员同一时间只能选择其中一种；总开关默认关闭。

产品形态是“NG 短信推送”总开关，默认关闭。
主程序只发送滚动 12 小时生产汇总：从短信服务启动时刻起按工位分别统计窗口内已结算周期的 OK/NG 数，不含进行中周期，全零窗口不发送。
单次 NG 与累计 N 次 NG 均不即时发送；短信 I/O 不进入检测热路径。

> 当前交付边界：软件双通道、配置 UI、测试快照入队、失败重试、离线队列与 shutdown 回归已完成；AT 真机与云端真实接口均未做 T7 验收，不能据此宣称短信已经送达。

它不是某条产线、sensor-clean、棉签或其他客户插件的专属逻辑。

本目录仍是独立硬件诊断工具：验证 Windows COM、模块 AT 指令、SIM、运营商注册、信号和真实短信提交。主程序已经在 `backend/services/` 内实现自己的生产门面，两者不共享运行时实例或配置文件。
本工具不导入 `backend`、不导入 `cv2`，也不会启动天军主程序。它只用于独立验证 COM、SIM、AT 指令、编码与运营商条件；主程序不会 import 本目录，主程序 Alarm 页的配置也不会驱动本工具。

## 归位决策与边界

| 子项 | 归位 | 原因 |
|---|---|---|
| 独立 AT 诊断工具 | `tools/sms_4g/` | 先验证硬件、AT 指令和运营商条件；不被主程序 import |
| 主程序短信门面与 AT 实现 | `backend/services/sms_service.py`、`sms_at_client.py`、`sms_modem.py` | 与检测核心解耦，后台入队发送 |
| 双 Provider | `backend/services/sms_providers/` | `at_modem` 与 `generic_http` 二选一 |
| 配置与 UI | `backend/services/sms_config.py`、`backend/api/sms.py`、Alarm 页 | 独立 `sms_config.json`，不复用灯塔配置 |

以下边界必须保持：

- 不做成客户插件私货。
- 不修改或复用 `backend/api/alarm.py` 的灯塔串口。Modbus 灯塔/蜂鸣器与 4G 短信是两个不同 USB 设备、两个 COM。
- 不在检测推理线程里同步发短信。12 小时汇总到点后只快速入队，串口或 HTTP I/O 和重试在后台 worker 执行。
- 不把短信并入 MES Gateway。短信是独立通知通道。
- 不用独立工具替代主程序配置；工具代码的修改不会改变主程序行为。

## 现场前提

- Windows 能在“设备管理器 → 端口（COM 和 LPT）”看到 USB 4G 模块的 AT 串口。
- 同一模块可能枚举多个 COM；应选择厂商说明中的 AT/Modem 端口，不要选 GPS、诊断或 NMEA 端口。
- SIM 可为中国移动、中国联通或中国电信，但必须开通短信业务且未欠费。
- 纯流量卡、未开短信的物联网卡、被运营商限制短信的卡无法靠软件修复。
- 模块天线已接好，且现场已注册运营商网络。
- 默认波特率为 `115200`；若模块手册另有说明，以模块手册为准。

## 快速开始

推荐使用与主项目一致的 Python 3.10，但依赖安装在本工具自己的环境中，不需要启动后端。

在 PowerShell 中进入本目录后执行：

```powershell
cd D:\Tianjun\tianjun-ai-vision\tools\sms_4g
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

也可以完成依赖安装后双击 `启动短信测试工具.bat`。
启动脚本优先使用本目录 `.venv\Scripts\python.exe`，没有本地虚拟环境时才使用 PATH 中的 `python`。

独立依赖只有：

- `PyQt5`：Windows 图形界面。
- `pyserial`：COM 枚举与 AT 串口通信。

## 建议操作顺序

1. 插入 USB 4G 模块并等待 Windows 枚举 COM。
2. 点击“刷新串口”，选择模块的 AT/Modem COM。
3. 保持 `115200`，除非模块手册明确要求其他波特率。
4. 点击“检测模块与网络”。应依次看到 AT、SIM READY、网络已注册和 Text Mode 支持。
5. 填写一个或多个手机号；多个号码可用逗号、分号或换行分隔。
6. 保持“自动”编码。中文会使用 UCS2，纯 ASCII 使用 GSM。
7. 勾选资费确认，点击“发送测试短信”。
8. 工具显示“已提交运营商”后，到接收手机确认最终送达。

“`+CMGS` 成功”表示模块已接受并提交短信，不等于接收手机一定送达。
最终送达仍受 SIM 资费、运营商风控、短信中心、信号和手机状态影响。

## 工具做了什么

诊断流程使用标准 AT 指令：

| 检查 | 主要指令 | 通过条件 |
|---|---|---|
| 基础通信 | `AT`、`ATE0` | 返回 `OK` |
| 模块信息 | `ATI` | 能返回厂商或型号信息；空信息只提示 |
| SIM | `AT+CPIN?` | 返回 `READY` |
| 网络注册 | `AT+CEREG?`、`AT+CREG?`、`AT+CGREG?` | 注册状态为 `1` 或漫游状态 `5` |
| 信号 | `AT+CSQ` | 显示 CSQ 和近似 dBm；低信号只提示风险 |
| 运营商 | `AT+COPS?` | 返回当前运营商；不返回时只提示 |
| 短信能力 | `AT+CMGF=?` | 声明支持 Text Mode `1` |

发送中文短信时使用：

1. `AT+CMGF=1` 切到 Text Mode。
2. `AT+CSCS="UCS2"`。
3. `AT+CSMP=17,167,0,8` 设置 UCS2 数据编码。
4. 手机号和正文转换为 UTF-16BE 大写十六进制。
5. `AT+CMGS` 收到 `>` 后写正文和 Ctrl+Z。
6. 必须同时收到 `+CMGS` 和最终 `OK` 才判定为已提交。

独立工具限定单条短信：GSM 最多 160 个 ASCII 字符，UCS2 最多 70 个 UTF-16 字符单元。
这样可以避免不同模块对长短信自动分片行为不一致。

## 常见问题

### 找不到 COM

- 拔插模块后点击“刷新串口”。
- 在 Windows 设备管理器确认驱动和端口名称。
- 某些 4G 模块需要安装厂商 USB 驱动。
- 一个模块出现多个 COM 时，逐个对照厂商手册确定 AT 端口。

### COM 无法打开

- 关闭串口助手、拨号软件和其他占用该 COM 的程序。
- 确认没有误选现有灯塔/蜂鸣器的 COM。
- 拔插模块，确认 COM 号是否变化。

### `AT` 无响应

- 核对波特率；常见值为 `115200` 或 `9600`。
- 确认选择的是 AT/Modem 端口，不是 GPS/NMEA/诊断端口。
- 检查模块供电；部分模块发送时瞬时电流较大，普通 USB 延长线可能不稳定。

### SIM READY，但网络未注册

- 检查天线、现场信号、SIM 是否停机或欠费。
- 注册状态 `2` 常表示正在搜索，等待后再测。
- 注册状态 `3` 常表示被网络拒绝，需要联系运营商或检查模块频段。
- 纯流量或受限物联网卡即使能上网，也可能不允许短信。

### 返回 `CMS ERROR`

工具会把常见错误转成中文提示。
例如 `302` 常见于操作不允许，`310` 表示未检测到 SIM，`311` 表示要求 PIN，`330` 表示短信中心号码未知，`515` 表示模块忙。
不同厂商可能扩展错误码，仍需对照该模块 AT 手册。

### 模块不支持标准 Text Mode

当前独立工具面向支持 `CMGF=1` 的标准 AT 模块。
若模块只支持 PDU 或厂商专用短信指令，先保存完整 AT 日志和模块型号，再为该模块增加独立 adapter；不要改写灯塔串口逻辑。

## 主程序双通道用法

在 Alarm 页的“NG 短信推送”中先选择一种通道，再开启总开关：

- `provider=at_modem`：选择短信模块的独立 COM，配置波特率、编码、模板和接收手机号。模块需插 SIM 并支持相应 AT 短信指令；不要选择灯塔/蜂鸣器 COM。本目录工具可先独立诊断硬件。
- `provider=generic_http`：配置 `api_url`、`token` 或成对的 `access_key/access_secret`、签名、模板 ID、超时、TLS 校验和字段映射。工控机必须能访问该云 API。
- 同一时间只启用一个 Provider，不会同时双发。共用配置包括手机号、重试和离线队列策略；旧 `ng_threshold` 与即时冷却字段只做配置兼容，汇总发送不使用。
- “测试发送”取当前未闭合窗口快照，不等于完整 12 小时窗口；返回的 queued 只表示后台入队，不等于运营商接受或手机送达。

配置固定保存在 `DATA_DIR/sms_config.json`。文件缺失或损坏时回退为 `enabled=false`，不会创建串口连接或 HTTP 请求，也不会读写灯塔 `alarm_config.json`。

主程序实现位置：

- 门面、冷却与 Provider 分发：`backend/services/sms_service.py`
- 独立配置：`backend/services/sms_config.py`
- AT 与 HTTP Provider：`backend/services/sms_providers/`
- API/Schema：`backend/api/sms.py`、`backend/schemas/sms.py`
- Alarm UI/API 客户端：`frontend/src/views/Alarm/index.vue`、`frontend/src/api/sms.js`
- 单次事件短信旁路：`backend/api/source_event_trigger_mixin.py`
- 退出收尾：`backend/main.py`

滚动窗口使用持久化水位去重，每个 `channel_id` 独立汇总和入队；重启续接未闭合窗口，同一已入队窗口不会重复发送。

云通道建议使用 `device_name`、`time_range`、`ok_count`、`ng_count` 四个模板变量。
阿里云需要重新申请用于 12 小时汇总的新模板 CODE，旧即时 NG 模板不适用；当前 `generic_http` 仍需客户中转服务完成阿里云签名，或后续新增官方 Provider。

## 客户云 API 对接清单

`generic_http` 是厂商无关的 JSON HTTP 适配器。客户联调前必须提供：

1. API URL、HTTP 方法、`Content-Type` 和全部固定/动态请求头。
2. 鉴权与签名规则，包括 token/密钥用途、时间戳、随机数、签名串、编码和时钟要求。
3. 手机号、签名、模板 ID、模板参数等请求字段名、嵌套路径、类型和必填条件。
4. 成功与失败响应样例、业务错误码、HTTP 状态码及可重试规则。
5. 频控规则、单次号码上限、批量限制与幂等要求。
6. 最终送达回执的查询接口或回调协议。

稳定逻辑字段和默认 JSON 路径定义在 `backend/services/sms_providers/generic_http_provider.py::DEFAULT_FIELD_MAPPING`；客户字段名通过 `generic_http.field_mapping` 配置，合法来源由 `backend/services/sms_config.py::ALLOWED_MAPPING_SOURCES` 约束。当前鉴权只支持 Bearer token 或 `X-Access-Key`/`X-Access-Secret` 请求头。

阿里云官方短信 API 不是上述静态请求头协议：若直接对接官方 API，需要后续新增独立 Provider/官方 SDK及其签名流程；本期也可对接一个完成阿里云签名的客户中转服务。现有 `generic_http` 不能被表述为“已直连阿里云”。

## T7 真环境待办

- AT：准备 Windows 可识别的虚拟串口短信模块、已开通短信的 SIM、天线和接收手机；确认选中 AT COM 而非灯塔 COM，分别验收 ASCII 与中文短信实际送达。
- 云：使用公司阿里云账号或客户真实账号，先备好签名与模板，再核对出口网络/白名单，完成真实提交、错误场景和最终回执验收；直连阿里云前先实现其专用 Provider/SDK，或准备签名中转服务。

## 目录结构

```text
tools/sms_4g/
├── main.py                     # GUI、离线单测入口
├── requirements.txt            # 独立依赖
├── 启动短信测试工具.bat         # Windows 启动器（CRLF）
├── README.md
├── sms_4g/
│   ├── at_client.py            # 8N1 串口、AT 超时/ERROR 处理
│   ├── modem.py                # SIM/网络诊断、GSM/UCS2 发送
│   ├── sms_service.py          # 独立工具的同步测试 + 异步 send_alarm 门面
│   ├── utils.py                # 手机号校验、脱敏、编码
│   └── gui.py                  # PyQt5 独立界面
└── tests/                      # fake serial 离线单测，不连接硬件
```

## 离线验证

安装 `pyserial` 后可运行不接硬件的自动化测试：

```powershell
python main.py --self-test
```

若本机已安装 PyQt5，可执行无窗口冒烟：

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
python main.py --gui-smoke
```

离线测试只能证明 AT 状态机、编码、重试、冷却和 GUI 能启动。
真机最终验收仍必须使用现场模块、天线、SIM 和真实接收手机完成，至少确认：

- 选中的确是模块 AT COM，不是灯塔 COM。
- 诊断全部通过或提示项已理解。
- GSM/ASCII 测试短信能送达。
- 中文 UCS2 测试短信能送达且无乱码。
- 多手机号逐个发送符合预期。
- 拔掉模块、SIM 未开短信、网络未注册时，界面能给出可定位错误且不崩溃。
