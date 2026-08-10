---
name: debug-triggers
description: "诊断 RFC 14 统一触发中心：触发源不运行/配置错误、虚拟按钮(pixel_region)不触发或误触发、脚踏板(hid_key)按了没反应、HTTP 触发 403/404、串口报文不匹配、定时不发、规则防抖/min_interval/生效窗口拦截、动作执行失败(manual_settle/trigger_event/ack_alarm)、与 PLC 共享动作注册表问题。当客户说'手遮画面没结算''脚踏板没反应''调了接口没触发''定时清零没跑'时使用。"
---

# debug-triggers — 统一触发中心诊断

> 设计文档：`docs/plugin-system/design/14_trigger_hub_rfc.md`。
> 操作手册用户视角说明：`docs/软件操作手册.md` §4.7「触发中心」。
> 动作注册表与 PLC 共享（RFC 13 上移），动作类问题两边通用，另见 `debug-plc`。

## 〇、一分钟定位表

| 症状 | 第一嫌疑 | 跳读 |
|---|---|---|
| 卡片「配置错误」 | 参数校验不过（region 坐标 / 正则 / 键名） | §2 |
| 卡片「异常」 | 依赖库缺失（pynput/pyserial）/ 设备打不开 | §2 |
| 虚拟按钮不触发 | 没标参考帧 / 阈值太高 / 工位没画面 / 光照漂移 | §3 |
| 虚拟按钮误触发 | 阈值太低 / 检测框恰好画进区域（MediaPipe 骨架）/ 参考帧过期 | §3 |
| 脚踏板没反应 | 键名不对（看 last_seen_key）/ macOS 无辅助功能权限 | §4 |
| HTTP 触发 403/404 | 密钥没带 / IP 白名单 / key 不匹配或未启用 | §5 |
| 规则不触发但信号有 | 防抖没稳住 / min_interval 拦 / 生效窗口外（看 suppressed 计数） | §6 |
| 动作没执行 | manual_settle 非 per_item 模式 / trigger_event 无激活项目 | §7 |
| 主程序起不来疑似触发中心拖累 | 不可能——设计上全兜底，先查别处 | §1 |

## 1. 架构速记（谁在哪个线程干什么）

```
TriggerHubManager (单例, backend/services/triggers/manager.py)
 ├─ 每条启用实例 → TriggerChannelEngine (engine.py)
 │    源线程 emit_level/emit_pulse → 防抖/边沿/min_interval/窗口 → submit_actions
 ├─ 动作执行线程 (全实例共享): triggers/actions.py 全局注册表
 │    (bind_sn/switch_project/manual_settle/clear_reset/trigger_event/ack_alarm/...)
 └─ 源注册表 sources/ (pixel_region/hid_key/http/serial_pattern/timer/mock, 插件可注册)
```

不变量：既有 8 条成熟通道（扫码/称重/PLC/入站…）不走 Hub；触发判定线程只 enqueue、
动作在共享线程执行（不变量 15）；源依赖惰性 import 缺库只影响对应类型
（`GET /api/v1/triggers/types` 的 `available` 字段可查）；无启用实例零线程零开销。
**主程序启动失败绝不可能是触发中心引起**（全兜底）。表 `trigger_channels` 由 create_all 自建。

## 2. 实例不运行（config_error / error）

1. `GET /api/v1/triggers/channels` 看 `runtime.status` + `runtime.last_error`，报错直读
2. `GET /api/v1/triggers/types` 看该类型 `available`——False 是依赖库没装
   （出厂包应齐装 pynput；serial 用既有 pyserial；pixel/timer/http/mock 纯内置）
3. 参数校验规则在各 `sources/*.py` 的 `validate_params`（保存时 400 提示同源）
4. CRUD 后自动热重载（`manager.restart_trigger`），不用重启后端

## 3. pixel_region（虚拟按钮）

- **信号语义是电平**：遮挡期间持续 True，引擎做 rising/falling 边沿
- 取帧走 `mgr.get_frame()`（原始尺寸显示帧，与 `/snapshot` 坐标 1:1；检测框画在前端不污染像素，
  但 **MediaPipe 骨架叠加在帧里**——区域别选骨架常出没的地方）
- `ref_diff` 模式启动时无参考帧会**拿首帧自动标定**；光照渐变有 EMA 缓漂（`ref_drift`，仅未触发时更新）
- 联调三板斧：实时状态看 `metric`（当前差分值）vs `threshold`；「一键标定参考帧」（需实例在运行且工位有画面，
  会回写 params.ref_bgr 持久化）；画面标定器拖拽框选区域（保存区域会清掉旧 ref_bgr）
- 工位停流 → `frame_missing: true`，源挂起不发电平，恢复自动继续；工位裁撤 → 实例被 on_channel_removed 停用
- `_pending_ack` 推流冻结期间帧不更新 → 差分不变，不会误触发（这是特性不是 bug）

## 4. hid_key（脚踏板/按钮盒）

- pynput 全局钩子，**不区分是哪把键盘**——踏板输出键要选不常用的（F 区/小键盘），
  避免与正常打字、USB 扫码枪（成串字符+回车）冲突
- 踏板按出来的键不知道是什么 → 实时状态 `last_seen_key` 就是联调探针（踩一脚看它变什么）
- `long_press_ms` 配了则**松开时**发脉冲（meta.press = long/short），不配则按下即发
- macOS 开发机要给终端/Python「辅助功能」权限，Windows 工控机无此问题
- 系统按住自动重发（key repeat）只记首按，不会连发

## 5. http / serial_pattern / timer

- **http**：`POST /api/v1/triggers/fire/{key}`。403 = 密钥不匹配（头 `X-Trigger-Secret` 或 `?secret=`）
  或 IP 不在白名单；404 = 无**启用中**的该 key 实例（保存了没启用也是 404）。
  payload 变量提取 `extract: {var: "json.path"}` → 动作模板 `{{var}}` 可用
- **serial_pattern**：正则 `search` 按行匹配（`line_ending` 默认 \n，"raw" 按块）；
  命名捕获组 `(?P<sn>\w+)` 提为变量。断链指数退避重连（1/2/5/10/30s），
  实时状态看 `connected`/`reconnects`/`last_line`（没匹配上时先看 last_line 长什么样）
- **timer**：`interval_s` 启动即计时；`daily` 每日 HH:MM（同分钟去重）。系统级定时，
  与检测运行状态解耦（区别于 v3.5.x 周期内强制动作）

## 6. 信号有了规则不触发

- `runtime.counters`：`signals`(电平采样)/`pulses`(脉冲)/`fires`(触发)/`suppressed`(拦截) 四个数对着看
- **边沿要有"边"**：启动时信号已 active 只锁存不触发（对齐 PLC）；mock 联调先 `mock-level false` 再 true
- 规则 `when.trigger` 与源形态要匹配：电平源(pixel_region)用 rising/falling/both，
  脉冲源(hid/http/timer/mock)用 pulse；缺省自动按源形态
- `debounce_ms`（电平稳定才认）→ 手抖闪遮不触发是特性；`min_interval_ms` 防连击；
  `active_window`（only_detecting / time_from~time_to 可跨零点）拦了会记 suppressed + 日志写原因
- 手动「试触发」（`POST /channels/{id}/test`）绕过全部信号判定直接执行动作——
  分离"信号没到"还是"动作不对"

## 7. 动作执行失败

- `manual_settle`：**仅 per_item 模式**（与界面手动结算同语义：只代替时机不代替结果）。
  非 per_item 工位日志报"未启用 per_item 模式"
- `trigger_event`：需工位有**激活项目**（events_config 里的事件 id）；`_pending_ack` 阻塞态被丢弃是既有守门
- `clear_reset`：= 界面清零（end_session + reset_stats）
- `ack_alarm`：清在途报警（默认本工位，`all_channels: true` 清全部），clear_source=trigger_hub
- `write_points`：非 PLC 引擎要 `connection` 指定目标 PLC 连接（id 或名称；只有一条在跑时可省）
- 动作失败不阻断后续动作（错误隔离），失败原因在实例日志（`GET /channels/{id}/logs`）

## 8. mock 联调剧本（无硬件全链路）

```
1. 方案模板 → 「虚拟触发源 (联调/演示)」→ 保存并启用
2. 界面「注入脉冲」(或 POST /channels/{id}/mock-fire) → fires+1、历史出记录、动作执行
3. 测边沿: 「电平↑」「电平↓」(mock-level) 配 rising 规则验证防抖/边沿/首锁存
```

自动化资产：`tests/test_trigger_hub.py`（13 单测）/ `tests/e2e_browser/test_trigger_panel.py`（CI e2e）/
`tests/manual_uat/trigger_hub_uat.py`（可见浏览器 UAT）。

## 9. 扩展点（插件）

- 新触发源类型：`sources/__init__.py` 的 `register_trigger_source(type, cls)`（覆盖内建同名即定制）
- 新动作：`triggers/actions.py` 的 `register_trigger_action`（**PLC 规则与触发中心同时可用**；
  PLC 侧老名 `register_plc_action` 是兼容别名）
- 新范式沉淀：`presets.py` 的 `BUILTIN_TEMPLATES` 加一项（纯数据）
