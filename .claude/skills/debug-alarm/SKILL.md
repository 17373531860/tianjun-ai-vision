---
name: debug-alarm
description: "诊断报警系统问题：串口连接失败、Modbus指令不响应、灯塔不亮、蜂鸣器不响、事件触发不生效、报警线程问题。当报警设备无反应或行为异常时使用。"
argument-hint: "[问题描述]"
model: opus
effort: high
allowed-tools: "Read, Grep, Glob, Bash, Agent"
---

# debug-alarm: 报警系统诊断

你正在诊断天军AI视觉检测系统的 **USB串口报警灯塔系统**。

用户问题: $ARGUMENTS

## 硬件架构

```
PC USB → CH340/CH341 转串口 → RS485 → Modbus 4色灯塔
                                        ├── 红灯
                                        ├── 绿灯
                                        ├── 蓝灯
                                        ├── 黄灯
                                        └── 蜂鸣器
```

- **驱动:** CH340/CH341 USB-to-Serial（安装包在 `drivers/CH341SER/`）
- **协议:** Modbus RTU 为主，也支持 ASCII、HEX、Relay

## 代码结构 (backend/api/alarm.py ~456行)

### AlarmManager 类
```python
class AlarmManager:
    # 核心状态
    self._serial: serial.Serial     # pyserial 连接
    self._connected: bool
    self._port: str                 # 如 /dev/ttyUSB0
    self._baudrate: int             # 默认 9600
    self._protocol: str             # 如 'modbus_4color'
    
    # 方法
    connect(port, baudrate, protocol)    # 打开串口
    disconnect()                          # 关闭串口
    trigger_alarm(event_config)          # 触发报警（新线程）
    stop_alarm()                         # 停止报警
    _send_command(cmd_bytes)             # 发送串口命令
```

### 协议定义 (PROTOCOLS 全局字典)
```python
PROTOCOLS = {
    'modbus_4color': {
        'red_on': bytes, 'red_off': bytes,
        'green_on': bytes, 'green_off': bytes,
        'blue_on': bytes, 'blue_off': bytes,
        'yellow_on': bytes, 'yellow_off': bytes,
        'buzzer_on': bytes, 'buzzer_off': bytes,
    },
    'ascii': {...},
    'hex': {...},
    'relay': {...},
}
```

### API端点
| 端点 | 功能 |
|------|------|
| `GET /alarm/ports` | 列出可用串口 |
| `POST /alarm/connect` | 连接设备 |
| `POST /alarm/disconnect` | 断开连接 |
| `GET /alarm/status` | 获取状态 |
| `POST /alarm/config` | 保存配置 |
| `POST /alarm/test` | 测试（需test_mode） |
| `POST /alarm/trigger/{event_type}` | 触发报警 |
| `POST /alarm/stop` | 停止报警 |
| `GET /alarm/protocols` | 列出协议 |

## 事件触发链路

```
检测到步骤完成/NG
  → source.py: VideoSourceManager 触发事件
    → 通过 API 调用 POST /alarm/trigger/{event_type}
      → AlarmManager.trigger_alarm(event_config)
        → 新线程:
          1. 发送灯光命令 (颜色+效果)
          2. 如果有蜂鸣器 → 发送蜂鸣器命令
          3. 延时 duration 秒
          4. 发送关闭命令

前端 Alarm 页面 → 手动测试:
  → POST /alarm/test {color, action}
    → 发送对应 on/off 命令
```

## 报警配置存储

**两处存储（容易不同步）：**
1. `backend/data/alarm_config.json` — AlarmManager 自身配置
2. 项目数据库 `Project.alarm_config` — 项目级报警配置

**前端 Alarm 页面** 同时写两处：
- `POST /alarm/config` → alarm_config.json
- `updateProject()` → DB Project 记录

## 诊断步骤

### 串口连接失败
1. 检查 CH340 驱动是否安装
2. Linux: `ls /dev/ttyUSB*` 确认设备存在
3. 权限: `sudo chmod 666 /dev/ttyUSB0` 或将用户加入 dialout 组
4. 检查波特率是否匹配（默认9600）
5. 确认没有其他程序占用串口

### 灯塔不响应
1. 确认协议选择正确（`modbus_4color` vs 其他）
2. 检查 Modbus 地址是否正确
3. 用 `POST /alarm/test` 直接测试（需确保 `test_mode: true`）
4. 检查 `_send_command()` 是否真正发送了字节

### 事件触发不生效
1. 确认事件配置已保存到 `alarm_config.json`
2. 检查 `trigger_alarm()` 是否被调用
3. 检查 source.py 中触发报警的代码路径
4. 确认事件类型字符串匹配

### 报警无法停止（已知bug）
- `stop_alarm()` 调用 `self._alarm_stop_event.set()` 并 join `self._alarm_thread`
- 但 `trigger_alarm()` 创建的是 **未跟踪的 daemon 线程**
- `self._alarm_thread` 可能指向上一次的线程，不是当前正在执行的
- **修复方向:** `trigger_alarm` 需要将新线程赋值给 `self._alarm_thread`

## 关键文件
- `backend/api/alarm.py` — AlarmManager + API端点
- `backend/data/alarm_config.json` — 报警配置文件
- `backend/api/source.py` — 事件触发调用点（搜索 `alarm` 或 `trigger`）
- `frontend/src/views/Alarm/index.vue` — 报警配置页面
- `frontend/src/layout/Navbar.vue` — 自动连接报警设备逻辑

## 空闲常亮模式 (v2.5.0+)

检测运行期间报警灯保持常亮（颜色可选），OK/NG 事件触发闪烁后自动恢复常亮。

### 配置项
- `idle_light.enabled`: 是否启用空闲常亮
- `idle_light.color`: 常亮颜色（如 green/yellow/blue）
- 前端 Alarm 页面底部「空闲灯」区域配置

### 生命周期
```
start_detection() → 开启 idle_light（常亮）
  → OK/NG 事件 → trigger_alarm() → 闪烁对应颜色
    → 闪烁结束 → 恢复 idle_light 常亮
stop_detection() → 关闭 idle_light
```

### 诊断要点
- 常亮不亮：检查 `idle_light.enabled` 配置 + `start_detection` 是否调用了开灯
- 闪烁后不恢复：检查 `trigger_alarm()` 结束后是否调用了恢复常亮逻辑
- 与报警灯共用串口：确认 Modbus 地址不冲突

## 已知陷阱
- `trigger_alarm` 中的 `delayed_off()` 使用 `import time` 内联导入
- `stop_alarm` 无法真正停止 `trigger_alarm` 启动的线程（见上方bug描述）
- 前端 Alarm 页面直接用 `api.get/post` 而非封装的API函数
- 报警配置在项目DB和alarm_config.json两处存储，可能不同步
- 空闲常亮与 Modbus MES 适配器共用 RS485 串口时需加锁（`_serial_lock`）

## 共享报警灯模式 (v2.7.3+)

一个物理报警灯（一个 USB 串口）被多个工位共用时使用。**典型坑：旧版本两个 ch 填同一个 port，导致串口抢占失败或行为异常 — v2.7.3 用此模式解决。**

### 启用方法

在 `alarm_config.json` 的 owner 通道（如 ch0）配置里加：

```json
"shared_with": [1],
"priority_order": ["ng", "warn", "ok", "idle"],
"event_priority_map": {
  "event1": "ok",
  "event2": "ng"
}
```

被共享的通道（如 ch1）的配置可以**整段删掉**，或保留但 `enabled: false` + `port: ""`。

### 合成规则

每个共享通道维护自己的"瞬时事件"和"是否 idle 中"。每次状态变化按优先级合成：
- 任一通道有未过期的 NG 事件 → 红灯
- 没 NG 但有警告 → 黄灯
- 都没有但有任一通道处于检测中 → idle 灯
- 都停了 → 灯灭

事件 expire 后自动重算，不会一直停留在最后一次状态。

### 调试要点
- 启动日志看 `[报警] 共享组: owner=ch0, 服务=[0,1]` 是否打印
- 运行时看 `[报警·共享] ch0 触发 event1(ok)` / `[报警·共享] 显示 ch1 的 event2`
- `mgr.is_shared()` 返回 True 即处于共享模式
- `mgr._shared_channels` 是当前服务的工位集合

### 完整示例
见 `backend/data/alarm_config.shared.example.json`

### 代码位置
- `AlarmManager.set_shared_mode()` — 启用共享
- `AlarmManager._recompose_and_apply()` — 合成核心
- `AlarmManager._apply_event_visual()` / `_apply_idle_visual()` — 实际发命令
- `AlarmRouter._load_all()` — 解析 `shared_with`
- `AlarmRouter._save_all()` — 共享 manager 去重序列化
- `AlarmRouter.on_channel_removed()` — 共享时只摘 ch 不断串口
