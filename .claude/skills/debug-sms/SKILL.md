---
name: debug-sms
description: "诊断主程序短信通知：12 小时汇总不发、AT 串口失败、云 HTTP 失败、离线队列堆积、配置不生效、与灯塔串口冲突。当客户反馈收不到汇总短信或短信配置异常时使用。"
argument-hint: "[问题描述]"
---

# debug-sms: 短信通知诊断

诊断天军主程序 **NG 短信通知 / 12 小时滚动汇总**。与灯塔蜂鸣器（`debug-alarm`）**完全隔离**——独立配置文件、独立 COM、独立服务线程。

用户问题：$ARGUMENTS

---

## 一、一句话架构

| 角色 | 文件 | 职责 |
|---|---|---|
| API | `backend/api/sms.py` | `/api/v1/sms/config|ports|test`；持有进程级 `SmsService` 单例 |
| Schema | `backend/schemas/sms.py` | 双 Provider payload + 一期兼容平铺字段 |
| 门面 | `backend/services/sms_service.py` | 选 Provider、入队发送、12h 汇总调度、关机 |
| 配置 | `backend/services/sms_config.py` | 读写 `sms_config.json`；坏配置回退默认关闭 |
| AT | `sms_at_client.py` + `sms_modem.py` + `sms_providers/at_modem_provider.py` | USB 虚拟串口 AT |
| HTTP | `sms_providers/generic_http_provider.py` | 厂商无关 JSON HTTP/HTTPS |
| 汇总 | `sms_summary.py` | 窗口水位 + 读已结算周期 OK/NG |
| 离线 | `sms_offline_queue.py` | SQLite 离线重试队列 |
| 前端 | `frontend/src/api/sms.js` + `views/Alarm/index.vue` | 报警页配置卡 |
| 调试工具 | `tools/sms_4g/` | 脱离主程序测 AT 模块 |

**不变量：**

1. **默认关闭**（`enabled=false`）——存量客户零差异。
2. **不在 `_trigger_event` 热路径发短信**——只读已落库结算周期做 12h 汇总。
3. **AT COM 必须独立**——禁止与灯塔/蜂鸣器共用同一串口。
4. **Provider 二选一**：`at_modem` | `generic_http`。
5. 启停接线在 `backend/main.py`：`_start_sms_summary_scheduler()` / `_shutdown_sms_notifications()`。

---

## 二、排查决策树

### 2.1 完全收不到短信

1. 报警页「NG 短信推送」总开关是否打开？`GET /api/v1/sms/config` → `enabled`
2. 收件人列表是否非空、号码格式是否合法？
3. Provider：
   - `at_modem`：COM 是否选对？用 `tools/sms_4g` 或 `POST /api/v1/sms/test` 单独发测
   - `generic_http`：工控机能否访问 `api_url`？看后端日志 `[SMS]`
4. 汇总是否已满一个 12h 窗口？水位在 `sms_summary_state.json`；服务刚启动未满窗不会发
5. 看离线队列是否堆积：`sms_offline_queue.db`（与 config 同目录）

### 2.2 AT 串口失败 / 与灯塔冲突

- 症状：开短信后灯塔不亮，或短信模块无响应
- 根因：COM 选成了灯塔口，或波特率不对
- 修法：刷新短信 COM 列表，选模块独立口；灯塔配置保持原 COM；两边不要交叉

### 2.3 云通道 4xx/超时

- 查 `generic_http`：`api_url` / `token` / `access_key` / `template_id` / `field_mapping`
- `verify_ssl=false` 仅内网自签证书场景临时用
- 用测试发送端点验证，勿等 12h 窗

### 2.4 配置保存后不生效

- PUT 会 `_replace_sms_service`：旧服务 `shutdown`，新服务按需重启汇总线程
- 权限：保存需 `alarm.edit`
- 配置损坏会回退默认关闭——查日志 `SmsConfigError`

### 2.5 担心热路径卡顿

- 确认：`source_event_trigger_mixin._trigger_event` **不调用**短信发送
- 汇总线程名 `sms-12h-summary`，只轮询 DB 结算数据

---

## 三、关键文件与落盘

| 落盘 | 说明 |
|---|---|
| `sms_config.json` | 运行时配置（gitignore） |
| `sms_summary_state.json` | 汇总窗口水位（gitignore） |
| `sms_offline_queue.db` | 离线重试队列 |

路径相对 `TIANJUN_DATA_DIR` / 后端数据目录（与 `SmsConfigStore.path` 同级）。

---

## 四、回归入口

```bash
# conda env: tianjun
python -m pytest tests/test_sms_*.py tests/test_sms_shutdown_wiring.py -q
python -m pytest tests/e2e_browser/test_alarm_page.py -q
# 硬件：tools/sms_4g 或报警页「测试发送」
```

---

## 五、与 debug-alarm 的边界

| | 灯塔/蜂鸣器 | 短信 |
|---|---|---|
| skill | `debug-alarm` | `debug-sms`（本文件） |
| 配置 | `alarm_config.json` | `sms_config.json` |
| API | `/api/v1/alarm/*` | `/api/v1/sms/*` |
| 触发 | `_trigger_event` → `alarm_router` | 12h 汇总调度线程 |
| 串口 | 灯塔 COM | **必须另一路** AT COM |
