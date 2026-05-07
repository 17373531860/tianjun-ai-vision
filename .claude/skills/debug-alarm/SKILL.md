---
name: debug-alarm
description: "诊断报警系统问题：串口连接失败、Modbus指令不响应、灯塔不亮、蜂鸣器不响、事件触发不生效、报警线程问题。当报警设备无反应或行为异常时使用。"
argument-hint: "[问题描述]"
model: opus
effort: high
allowed-tools: "Read, Grep, Glob, Bash, Agent"
---

# debug-alarm: 报警系统诊断（v3.5.x 真相版）

诊断天军 AI 视觉检测系统的 **USB 串口报警 + Modbus 灯塔 / 蜂鸣器**。事实源是 `backend/api/alarm.py`（1009 行，**所有报警逻辑都在这一个文件里，没有独立的 alarm_service.py**）。

用户问题：$ARGUMENTS

---

## 一、整体架构（一句话）

`backend/api/alarm.py` 内只有**两个类 + 一个全局单例**：

| 角色 | 职责 |
|---|---|
| `AlarmManager` | 单台物理报警器（= 一个串口设备），管串口连接、命令编码、事件触发、空闲常亮、共享模式状态机 |
| `AlarmRouter` | **按 channel_id 路由**到 `AlarmManager`；解析 `shared_with` 让多个 ch 指向**同一**实例；统一 `_save_all` / `reload_config_from_disk` / `disconnect_all` / `on_channel_removed` |
| `alarm_router` | 模块级全局单例（`alarm.py:798`），是整个项目唯一入口 |
| `alarm_manager` | 兼容老代码的 `alarm_router.get(0)`，**不要新增引用** |

> 关键不变量：所有外部调用（事件触发、idle 灯、降工位）**必须经过 `alarm_router`**，并且**必须带 `channel_id`**。直接 import `alarm_manager` 在多工位 / 共享模式下会错。

---

## 二、AlarmRouter 启动流程（`_load_all`）

```
backend/data/alarm_config.json  →  AlarmRouter._load_all()
  1. 读 {"channels": {"0": {...}, "1": {...}}}（旧格式 = 单顶层 dict 也兼容）
  2. 每个 ch_id 创建 AlarmManager(config=merged)
  3. 扫描 owner 配置里的 shared_with: [1, 2, ...]
       → 调 owner_mgr.set_shared_mode(channels=[owner]+shared_with, priority_order, event_priority_map)
       → 把被共享的 ch_id 在 self.managers 里指向同一个 owner_mgr
       → 同时维护 self._owner_for[ch] = owner_ch（用于 _save_all 去重）
  4. 自动连接：seen=set()；每个物理 manager（按 id() 去重）只 connect 一次
       → 测试环境（BACKEND_SKIP_INIT=1）跳过连接
```

> 多工位共享靠的就是「**多个 channel_id → 同一个 AlarmManager 实例**」这个 dict 别名。`alarm_router.trigger_alarm(et, channel_id=2)` 在共享模式下，最终调的是 owner manager 的 `trigger_alarm(et, channel_id=2)`，由 `_state_lock` 保证状态写入安全。

---

## 三、Modbus / 灯塔 / 蜂鸣器协议

`alarm.py:21-86` 顶部的 `PROTOCOLS` 字典硬编码了所有命令字节：

| 协议 id | 适用场景 | 默认 |
|---|---|---|
| `modbus_4color` | **MODBUS RTU 四色塔灯（推荐 / 现场 99% 用这个）** | 是 |
| `simple_ascii` | 老式继电器 / 自制板，发字符 `1/0/2/3/A/B` | — |
| `hex_simple` | 单字节 0x01/0x00/0x02/... | — |
| `hex_relay` | 4 字节继电器协议（`A0 01 01 A2` 等） | — |
| `custom` | 用户在 `custom_commands` 里写十六进制空格分隔字符串 | — |

`modbus_4color` 命令族（**已校验过的 RTU 帧 + CRC，不要改字节**）：

- 颜色控制：`{red|green|blue|yellow}_{on|off|slow|fast}`（蓝、黄无 slow/fast）
- 蜂鸣器：`buzzer_{on|off|slow|fast}`
- 红绿+蜂鸣组合：`{red|green}_buzzer_{on|off|fast}`
- 全开 / 全关：`all_on` / `all_off` / `light_off` / `light_on`

> 写底层串口的唯一通道是 `AlarmManager._send_command(bytes)`（`alarm.py:240`）→ `pyserial.Serial.write + flush`。**不要自己 open serial、不要绕过 _state_lock**。

串口参数固定：`8N1`，`timeout=1`，波特率默认 9600（配置可改）。

---

## 四、报警线程模型（**v2.7.x 起改用 threading.Timer**）

| 场景 | 线程方式 | 来源 |
|---|---|---|
| 单工位事件触发（`_trigger_alarm_solo`） | `threading.Timer(duration, delayed_off)` daemon | `alarm.py:349` |
| 共享模式事件触发（`_trigger_alarm_shared`） | `threading.Timer(duration, expire)` daemon，回调里再调 `_recompose_and_apply` | `alarm.py:381` |
| `stop_alarm` | `_alarm_stop_event.set()` + 共享模式下清所有 channel state 后重算 | `alarm.py:385` |

> **关键改进**：旧版用 `Thread + sleep` 没法 cancel，会留悬挂线程；现版用 `Timer`，关停时一次重算就清干净。**改动 trigger_alarm/expire 时不要回退到 Thread+sleep**。

---

## 五、事件 → 报警联动

外部触发链路（**只有 4 个调用点**，调试时直接搜 `alarm_router.`）：

| 调用方 | 文件 / 行 | 时机 |
|---|---|---|
| 检测事件触发 | `source_event_trigger_mixin.py:223-225` | `_trigger_event` 中心 hook 触发后 → `alarm_router.trigger_alarm(f'event{id}', channel_id=self.channel_id)` |
| 周期性强制动作（v3.5.0） | `source_periodic_actions_mixin.py:438-442` | 每 N 轮强制触发自定义事件 |
| 称重「有重无码」 | `services/external_device_pipeline.py:208-209` | 触发 `weight_no_barcode` 事件类型 |
| Source 启停 / 暂停 / 待机 | `source.py:1129/1179`，`source_lifecycle_mixin.py:32/164/193/233` | `start_idle_light` / `stop_idle_light` |

> **没有 alarm 走 HTTP 这一层**：项目内调用就是直接 `from backend.api.alarm import alarm_router`，**不要造 POST /alarm/trigger 这种自调自的链路**。HTTP 端点 `/api/v1/alarm/trigger/{event_type}` 只给前端「Alarm 配置页」手动测试用。

事件 id ↔ 报警事件 key 映射：`event_id` 整数 → `f'event{event_id}'` 字符串 → 查 `alarm_config.json` 的 `triggers.event{N}`。所以 **新增事件类型时**：要在 Alarm 配置页给 `event{N}` 配 trigger，否则 `triggers.get(et).get('enabled')` 是 False，`trigger_alarm` 就会**静默 return**。

---

## 六、共享模式合成（`_recompose_and_apply`）

`alarm.py:477-521` 是核心。**必须在 `_state_lock` 下调用**。

```python
1. 遍历 _shared_channels 中每个 ch 的 _channel_states：
     - 收集仍未 expire 的事件 → active_events
     - 任一 ch 的 is_idle=True → any_idle = True
2. 按 _priority_order 排序 active_events
     - 默认顺序: ['ng', 'warn', 'ok', 'idle']  → ng 抢占 ok / warn
3. 决定 target_key:
     - 有 active 事件 → ('event', owner_ch, event_type)
     - 没事件但有 idle → ('idle', None, None)
     - 都没 → ('off', None, None)
4. 与 _current_visual 对比：相同则 return（避免重复发命令打滑灯）
5. 不同则真发命令：_apply_event_visual / _apply_idle_visual / all_off
```

`event_priority_map` 把事件名映射到优先级类别，默认：

```json
{"event1": "ok", "event2": "ng", "event3": "warn", "event4": "warn"}
```

> 共享模式启用条件：`is_shared() == len(_shared_channels) > 1`。单 ch 走 `_trigger_alarm_solo` 老路径，**不进合成**。

---

## 七、配置文件 `backend/data/alarm_config.json`

实际样例（核心字段）：

```json
{
  "channels": {
    "0": {
      "enabled": true,
      "port": "/dev/ttyUSB0",
      "baudrate": 9600,
      "protocol": "modbus_4color",
      "shared_with": [1, 2],
      "priority_order": ["ng", "warn", "ok", "idle"],
      "event_priority_map": {"event1": "ok", "event2": "ng"},
      "triggers": {
        "event1": {"name": "合格", "enabled": true, "color": "green", "effect": "on",   "buzzer": false, "duration": 2},
        "event2": {"name": "NG",   "enabled": true, "color": "red",   "effect": "fast", "buzzer": true,  "duration": 5}
      },
      "idle_light": {"enabled": true, "color": "blue"},
      "test_mode": false
    }
  }
}
```

完整示例：`backend/data/alarm_config.shared.example.json`。

> **两处存储仍需注意**：本文件 = `AlarmManager` 自身配置；`Project.alarm_config` 字段 = 项目级报警绑定（步骤事件→哪个 trigger key）。前端 `Alarm/index.vue` 只写本文件，不写 DB。前端 `Project/index.vue` 写 DB 不写本文件。**两处都要看**。

---

## 八、API 端点（仅前端 / 手测用）

| 端点 | Query | 用途 |
|---|---|---|
| `GET /api/v1/alarm/ports?channel=N` | channel | 列 USB 串口（含 CH340） |
| `POST /api/v1/alarm/connect` | channel | 打开串口 + 写回 config |
| `POST /api/v1/alarm/disconnect` | channel | 关串口 |
| `GET /api/v1/alarm/status?channel=-1` | channel=-1 全部 | 含 `is_shared`/`shared_channels`/`is_owner` |
| `POST /api/v1/alarm/config` | channel | 保存配置；含 `shared_with` 时**自动 reload** |
| `POST /api/v1/alarm/reload` | — | 手动重载配置（v2.7.3） |
| `POST /api/v1/alarm/test` | channel | 手测命令（**必须 `test_mode=true`**） |
| `POST /api/v1/alarm/trigger/{event_type}` | channel | 手动触发 |
| `POST /api/v1/alarm/stop` | channel | 停止 |
| `POST /api/v1/alarm/idle-light/{start,stop}` | channel | 手动 idle |
| `GET /api/v1/alarm/protocols` | — | 列 4 种协议 |

---

## 九、常见问题与处置

### 1) 串口连接失败

| 现象 | 检查项 |
|---|---|
| Linux: `串口不存在` | `ls /dev/ttyUSB*` / `dmesg | tail` 看是否 enumerate；CH340 内核模块（一般已自带） |
| Linux: `串口权限不足` | `connect()` 已尝试 `chmod 666`；不行就 `sudo usermod -aG dialout $USER` 后重启；或 udev 规则 |
| Windows: `串口不存在` | 设备管理器看 COM 口；安装 `drivers/CH341SER/` |
| 任意系统：`port already in use` | 串口被占（旧进程没退 / Modbus MES 适配器同时打开）。先 `disconnect_all`、停 MES Gateway 再连 |
| 波特率不匹配 | 9600 是默认；塔灯背面 DIP 拨码确认 |

### 2) Modbus 不响应（设备物理在但发了没反应）

- 协议选错：检查 `protocol == 'modbus_4color'`，不是 `simple_ascii`
- 设备 Modbus **从机站号**：默认 `0x01`（CRC 校验绑死了，**字节里第一字节就是 0x01**）。如果客户的塔灯是站号 2/3，**`PROTOCOLS['modbus_4color']` 的字节都对不上**，需要新协议（不要硬改 PROTOCOLS）
- RTU vs TCP：本项目**只做 RTU over RS485（USB-RS485 转换器）**。如果客户给的是 Modbus TCP（以太网灯柱），需新建 adapter
- 自检流程：`POST /alarm/test`（`action=red_on`）→ 看 `_send_command` 返回 True / False；再用 `screen /dev/ttyUSB0 9600` 看是否能写

### 3) 灯塔不亮（连接 OK 但灯不动）

| 子症状 | 排查 |
|---|---|
| **完全不亮** | 看启动日志 `[报警] ch0 自动连接成功`；`is_connected()` 真假；`_idle_light_active` 是否 True |
| **idle 不亮** | `idle_light.enabled`、`config.enabled`、`is_connected()` — `start_idle_light` 在每个失败点都打了中文日志，直接看 stdout |
| **颜色映射错** | `cmd = self._get_command(f'{color}_on')`，`color='blue'` 在 `simple_ascii` 等协议里**没有 `blue_on` 键** → 返回 b''，静默失败 |
| **共享模式下不亮** | `_recompose_and_apply` 早 return（`target_key == _current_visual`）。先 `POST /alarm/reload` 强制重算 |
| **优先级被高级别压制** | 共享模式下另一 ch 的 NG 事件还没 expire，红灯抢占；查 `_channel_states[ch].expire_at` |
| **idle 被关闭** | `restore_idle_light` 只在 `_idle_light_active=True` 时回亮；`stop_alarm` 会 all_off |

### 4) 蜂鸣器不响

- `triggers.event{N}.buzzer == true` 没设
- `duration == 0` 直接 expire 没声音
- 颜色 + 蜂鸣组合：红/绿用 `{color}_buzzer_{effect}` 单帧；其他颜色是**两帧**（先 `buzzer_*`，再 `{color}_*`），**串口若被中途打断只发了一半**会出现「灯亮但不响 / 响了但不亮」
- `effect` 取值仅 `on/slow/fast`，写错回退到 `on`

### 5) 事件触发但报警没动

按调用链反查：

```
1. source._trigger_event 是否真到达？  → 看 events_log 末尾是否有这条
2. event_id → f'event{N}'，alarm_config.triggers 是否有这个 key？
3. trigger_config.enabled 是否 True？
4. config.enabled（整个报警器）是否 True？
5. 串口是否 connected？
6. （共享模式）_recompose_and_apply 是否被高优先级压制？
7. （共享模式）_current_visual 是否已是该状态？  → 此时**真的不会再发命令**，是预期
```

最快的烟测：`curl -X POST http://localhost:8001/api/v1/alarm/trigger/event2?channel=0`，看后端日志 `[报警] / [报警·共享]` 输出。

---

## 十、系统级钩子（**改动这些一定要顺便看 alarm**）

| 钩子 | 必须做 | 文件 |
|---|---|---|
| 后端关机 8 步 | `alarm_router.disconnect_all()`；先 `stop_idle_light` 再断口 | `backend/main.py:700/895` |
| 多工位降级 / 升级 | `channel_manager.set_channel_count` → `alarm_router.on_channel_removed(cid)`（共享模式只摘 ch 不断口） | `backend/api/channel_manager.py:100`，`alarm.py:726-773` |
| 项目切换后 | 不走 alarm_router；只是 `Project.alarm_config` 变化，事件→trigger 的映射变；**串口配置不变** | — |
| 配置热重载 | `POST /alarm/reload` → `reload_config_from_disk`：先 stop+all_off+disconnect 所有去重 manager，再 `_load_all` 重建 | `alarm.py:699-724` |

---

## 十一、关键文件速查

| 文件 | 行数 | 关键内容 |
|---|---|---|
| `backend/api/alarm.py` | 1009 | `PROTOCOLS` / `AlarmManager` / `AlarmRouter` / 全局 `alarm_router` / 12 个 API |
| `backend/data/alarm_config.json` | — | 运行时配置（多工位 / 共享） |
| `backend/data/alarm_config.shared.example.json` | — | 共享模式参考样例 |
| `backend/api/source_event_trigger_mixin.py:223` | — | **唯一**主路径事件→报警联动 |
| `backend/api/source_periodic_actions_mixin.py:438` | — | 周期性强制动作触发 |
| `backend/services/external_device_pipeline.py:208` | — | 称重「有重无码」触发 |
| `backend/api/source.py:1129/1179` + `source_lifecycle_mixin.py:32/164/193/233` | — | idle 灯起停 |
| `backend/api/channel_manager.py:100` | — | 降工位 → `on_channel_removed` |
| `backend/main.py:700/895` | — | 关机 → `disconnect_all` |
| `frontend/src/views/Alarm/index.vue` | ~670 | 配置页（共享 / idle / triggers） |
| `frontend/src/layout/Navbar.vue` | — | 启动时自动连接报警设备的 UI 入口 |

---

## 十二、已知陷阱（**改动前必读**）

1. **多 ch 同 port = 抢占** — v2.7.3 前的旧配置里多个 ch 各自填同一 `port`，会出现互相 close 串口、命令乱序。**新现场必须用 `shared_with`，不要回退**。
2. **`alarm_manager` 兼容别名** — `alarm.py:801` 是 `alarm_router.get(0)`，多工位场景下别再用，直接 `alarm_router.xxx(channel_id=...)`。
3. **共享模式 idle**：`_idle_light_active` 是**全局标志**（不是 per-channel）；只有 `any_idle = any(is_idle for state in ...)` 决定是否亮，单 ch `stop_idle_light` 不会真灭灯。
4. **重复 `_save_all`**：共享 manager 按 `id()` 去重，只按 owner 序列化一次。如果你改了 `AlarmRouter`，**必须保留这个去重**，否则 JSON 里会出现重复 owner 配置。
5. **改协议字节** — `PROTOCOLS['modbus_4color']` 的 CRC 是预计算硬编码，**直接改 byte = 帧错**。新协议请加新 key，不要原地改。
6. **`_recompose_and_apply` 必须在 `_state_lock` 下调** — 否则 `_current_visual` 与实际命令会撕裂。
7. **`_apply_event_visual` 内部有 `time.sleep(0.05)`** — 在持锁下短睡是有意为之（先 all_off 再发新命令防止串口缓存），不要去掉，不要换更长时间。
8. **`stop_alarm` 老 bug 已修**：旧版 `self._alarm_thread` 跟踪不到 daemon 线程；现已改 `Timer` + `_alarm_stop_event` + 共享模式下清 channel state 后重算。如果再出现「停不下来」先看是不是引入了新的 `Thread+sleep` 路径。
9. **`reload_config_from_disk` 顺序**：`stop_alarm → all_off → disconnect → managers.clear → _load_all`，少一步都会泄漏（线程 / 灯 / 串口）。
10. **Modbus MES 适配器**（`mes_adapters/modbus_rtu`）**可能与报警共用 RS485 总线**，两者各自用 `pyserial.Serial`，没有共享 lock；现场用同一物理串口时必须分两个 USB 转换器。

---

## 十三、最小诊断脚本

后端在跑时直接：

```bash
curl -s http://localhost:8001/api/v1/alarm/status?channel=-1 | python -m json.tool
curl -s http://localhost:8001/api/v1/alarm/protocols
curl -s -X POST 'http://localhost:8001/api/v1/alarm/trigger/event2?channel=0'
```

或在 Python REPL 里：

```python
from backend.api.alarm import alarm_router
mgr = alarm_router.get(0)
print(mgr.is_connected(), mgr.is_shared(), mgr.get_shared_channels_snapshot())
print(mgr.config.get('triggers'))
```

不要写脚本之外的硬测代码进 `alarm.py` 主体。
