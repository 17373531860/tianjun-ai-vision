# WMax IDManager 通讯协议文档

> 通过 ILSpy 反编译 ZTools.dll / IDManager.exe 提取，供 Python 协议层实现参考。

## 1. 端口定义

| 用途 | 端口 | 协议 |
|------|------|------|
| UDP 发现 | 55266 | UDP |
| 文本发现 | 10000 | UDP |
| TCP 命令 | 55266 | TCP |
| TCP 图像 | 55276 | TCP |
| TCP 报告 | 55286 | TCP |
| TCP 通用 | 55256 | TCP |

## 2. 二进制帧格式

所有数据均为**大端序（网络序）**。

### 2.1 帧结构

```
+--------+--------+--------+--------+--------+--------+
| Header (2B)     | Flag (4B, big-endian)              |
| 0x5A   | 0x5A   | F[31:24] F[23:16] F[15:8] F[7:0]  |
+--------+--------+--------+--------+--------+--------+
| [可选: SerialNumber, 10B UTF-8, 当 Flag & 0x80000000] |
+--------+--------+
| CmdType| CmdIdx |  (各 1 字节)
+--------+--------+--------+--------+--------+--------+
| [可选: DataLen, 4B big-endian, 当 Flag & 0x40000000]  |
+--------+--------+--------+--------+--------+--------+
| HeadChk| (1 字节, 从 offset 2 到此前所有字节的累加和低8位) |
+--------+--------+--------+--------+--------+--------+
| [可选: Payload, DataLen 字节]                          |
| [可选: DataChk, 2B big-endian, Payload 字节累加和低16位]|
+--------+
| Tail   | 0xA5
+--------+
```

### 2.2 Flag 位定义

| 位 | 名称 | 值(int32) | 说明 |
|----|------|-----------|------|
| bit31 | HasSN | 0x80000000 (-2147483648) | 帧中包含 10 字节序列号 |
| bit30 | HasData | 0x40000000 (1073741824) | 帧中包含数据载荷 |
| bit27 | IsProtobuf | 0x08000000 (134217728) | 数据为 Protobuf 编码 |
| bit26 | IsResponse | 0x04000000 (67108864) | 这是一个响应帧 |
| bit[4:0] | DevClass | 0x1F | 设备类型（1=FixedMount） |

默认构造: `Flag = IsProtobuf | DevClass.FixedMount = 0x08000001`

### 2.3 校验和算法

简单字节累加：
```python
def checksum(data: bytes) -> int:
    return sum(data) & 0xFFFFFFFF
```
- **头校验**: `(byte)(sum(frame[2:header_end]))`
- **数据校验**: `(uint16)(sum(payload_bytes))`, 高字节在前

## 3. 命令码表 (CommandType_E)

### 3.1 请求命令 (0-57)

| 值 | 名称 | Protobuf 载荷 | 说明 |
|----|------|---------------|------|
| 0 | Unknown | - | - |
| 1 | QuickFind | 无 | 快速发现 |
| 2 | FindDevice | 无 | 设备发现(UDP) |
| 3 | HandShake | HandShake | TCP 握手 |
| 4 | SetBankOpt | SetBankOpt | 设置 Bank 参数 |
| 5 | SetEthernetOpt | SetEthernetOpt | 网络设置 |
| 6 | SetHardwareOpt | SetHardwareOpt | 硬件设置 |
| 7 | TurnOnOffVideo | TurnOnOffVideo | 开关实时视频 |
| 8 | TakePicture | - | 拍照 |
| 9 | SetInputOpt | SetInputOpt | 输入设置 |
| 10 | SetOutputOpt | SetOutputOpt | 输出设置 |
| 11 | SetCodesOpt | SetCodesOpt | 码制设置 |
| 12 | SetTriggerOpt | SetTriggerOpt | 触发设置 |
| 13 | SendFile | SendFile | 文件传输 |
| 14 | SendImagePtcol | 原始图像 | 图像协议(旧) |
| 15 | SetConfigOpt | SetConfigOpt | 下发配置(不保存) |
| 16 | GetConfigOpt | GetConfigOpt | 读取配置 |
| 17 | AutoFocus | AutoFocus | 自动对焦 |
| 18 | StartTune | StartTune | 开始自动调参 |
| 19 | CancelTune | CancelTune | 取消自动调参 |
| 20 | SendTermCmd | SendTermCmd | 终端命令(LON/LOFF等) |
| 21 | CtrlReboot | 无 | 重启设备 |
| 22 | CtrlReset | DevReset | 恢复出厂设置 |
| 23 | SetReadingOpt | SetReadingOpt | 读取选项 |
| 24 | GetFileList | - | 获取文件列表 |
| 25 | ReadFile | - | 读取文件 |
| 26 | DeleteFile | - | 删除文件 |
| 27 | CtrlRRTest | - | 读码率测试 |
| 28 | CtrlTactTest | - | 节拍测试 |
| 29 | CtrlDoFTest | - | 景深测试 |
| 30 | Trigger | TriggerReq | 触发控制 |
| 31 | TurnOnOffTrggerImage | - | 触发时返回图像 |
| 32 | DeviceRunMode | RunMode | 运行模式 |
| 33 | DeviceFeature | 无 | 设备特性查询 |
| 49 | SendImageNew | 新图像格式 | 图像协议(新) |
| 50 | ForceIp | ForceIp | 强制修改设备IP |
| 51 | FocusIllum | DevSetFocusIllum | 对焦照明 |
| 52 | FindGroup | FindGrouping | 查找分组 |
| 53 | SaveConfigAsCustomer | - | 保存为客户配置 |
| 54 | RestoreToCustomerConfig | - | 恢复客户配置 |
| 55 | SaveConfig | SetConfigOpt | 保存配置到设备 |
| 56 | IndicateDev | 无 | 指示设备(闪灯) |
| 57 | Verify | CtrlCodeQualityVerification | 码质量校验 |

### 3.2 上报命令 (200-206)

| 值 | 名称 | Protobuf 载荷 | 说明 |
|----|------|---------------|------|
| 200 | RptCode | RptCode | 扫码结果上报 |
| 201 | RptUpdateResult | RptUpdateResultResp | 固件升级结果 |
| 202 | RptTuneResult | RptTuneResultResp | 自动调参结果 |
| 203 | RptReadRateTest | RptReadRateResp | 读码率测试结果 |
| 204 | RptTactTest | RptTactTestResp | 节拍测试结果 |
| 205 | RptDoFTest | RptDoFTestResp | 景深测试结果 |
| 206 | RptFocusResult | RptFocusResultResp | 对焦结果 |

### 3.3 特殊命令

| 值 | 名称 | 说明 |
|----|------|------|
| 254 | PCMD_RAW | 原始命令 |

## 4. UDP 设备发现协议

### 4.1 文本发现

向 `255.255.255.255:10000` 发送 UTF-8 字符串 `"4f3d-FND"`，
设备回复文本格式数据到端口 10000。

### 4.2 二进制发现

向 `255.255.255.255:55266` 发送 `ProtocolBase.Pack(FindDevice)` 帧（CmdType=2, 无载荷）。
设备回复 `FindDeviceResp` Protobuf 消息。

### FindDeviceResp 结构

```protobuf
message FindDeviceResp {
    DeviceInfo dev_info = 1;       // 设备信息(SN/型号/固件版本)
    EthernetOpt network_opt = 2;   // 网络配置(IP/掩码/网关/MAC)
    GroupingOpt grouping_opt = 3;  // 分组信息
}
```

## 5. 新图像格式 (CmdType=49, ParseNewImage)

| 偏移 | 长度 | 内容 |
|------|------|------|
| 0 | 1 | BankId |
| 1 | 1 | ImageType (ImgType_E) |
| 2 | 8 | TimeStamp (uint64 BE) |
| 10 | 1 | ImageFormat (ImgFormat_E) |
| 11 | 2 | Width (uint16 BE) |
| 13 | 2 | Height (uint16 BE) |
| 15 | 4 | DataLen (uint32 BE) |
| 19 | DataLen | 图像数据 (JPEG/BMP) |

## 6. 关键 Protobuf 消息 (SensorOpt 参数)

| 字段号 | 名称 | 类型 | 说明 |
|--------|------|------|------|
| 1 | exposure_mode | Int32Value | 0=固定, 1=自动 |
| 2 | exposure_sample | ExposureSample | 曝光采样 |
| 3 | exposure | Int32Value | 曝光时间 |
| 4 | min_exposure | Int32Value | 最小曝光 |
| 5 | max_exposure | Int32Value | 最大曝光 |
| 6 | gain | DoubleValue | 增益 |
| 7 | min_gain | DoubleValue | 最小增益 |
| 8 | max_gain | DoubleValue | 最大增益 |
| 9 | gamma | Int32Value | 伽马 |
| 10 | contrast | Int32Value | 对比度 |
| 11 | contrast_adj | ContrastAdjMode_E | 对比度模式 |
| 12 | aimer_ctrl | Int32Value | 瞄准灯控制 |
| 13 | focus_mode | Int32Value | 对焦模式 |
| 14 | focus_sample | FocusSample | 对焦采样 |
| 15 | focus_value | Int32Value | 手动对焦值 |
| 16 | frame_rate | Int32Value | 帧率 |

## 7. TCP 连接流程

1. 连接到设备 `IP:55256`
2. 发送 `HandShake`（CmdType=3）
3. 发送 `GetConfigOpt`（CmdType=16）读取全部配置
4. 发送 `DeviceFeature`（CmdType=33）查询设备能力
5. 开始 `TurnOnOffVideo`（CmdType=7）获取实时图像
6. 通过 `SetConfigOpt`（CmdType=15）修改参数
7. 通过 `SaveConfig`（CmdType=55）保存到设备
