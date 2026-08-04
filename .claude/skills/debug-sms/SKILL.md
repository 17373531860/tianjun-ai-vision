---
name: debug-sms
description: "诊断主程序短信/微信推送通知与每日短信日报：12 小时汇总不发、日报不发/变量不对、AT 串口失败、云 HTTP/阿里云/腾讯云失败、WxPusher 失败、离线队列堆积、配置不生效、与灯塔串口冲突。当客户反馈收不到汇总短信/微信/日报或短信配置异常时使用。"
argument-hint: "[问题描述]"
---

# debug-sms: 短信 / 微信推送通知诊断

诊断天军主程序 **NG 短信/微信推送 / 12 小时滚动汇总 / 每日短信日报（v3.46）**。与灯塔蜂鸣器（`debug-alarm`）**完全隔离**——独立配置文件、独立 COM、独立服务线程。

> v3.46 统一短信通道：NG 汇总通知与每日短信日报**共用同一份 `sms_config.json` 通道配置**（报警页「短信通知」卡编辑）。日报差异见本文第六节。

用户问题：$ARGUMENTS

---

## 一、一句话架构

| 角色 | 文件 | 职责 |
|---|---|---|
| API | `backend/api/sms.py` | `/api/v1/sms/config|ports|test`；持有进程级 `SmsService` 单例 |
| Schema | `backend/schemas/sms.py` | 多 Provider payload + 一期兼容平铺字段 |
| 门面 | `backend/services/sms_service.py` | 选 Provider、入队发送、12h 汇总调度、关机 |
| 配置 | `backend/services/sms_config.py` | 读写 `sms_config.json`；坏配置回退默认关闭 |
| AT | `sms_at_client.py` + `sms_modem.py` + `sms_providers/at_modem_provider.py` | USB 虚拟串口 AT |
| HTTP | `sms_providers/generic_http_provider.py` | 厂商无关 JSON HTTP/HTTPS |
| WxPusher | `sms_providers/wxpusher_provider.py` | 微信推送（appToken + UID/Topic） |
| 阿里云 | `sms_providers/aliyun_provider.py` | 官方云短信（审核签名+模板+命名变量，HMAC-SHA1） |
| 腾讯云 | `sms_providers/tencent_provider.py` | 官方云短信（审核签名+模板+位置变量，TC3-HMAC-SHA256） |
| 工厂 | `sms_providers/__init__.py` `create_provider` | 统一通道工厂，NG 通知与日报共用 |
| 日报 | `backend/services/sms_report.py` + `api/sms_report.py` + `models/notify_models.py` | v3.46 每日短信日报（规则/调度/发送/日志三表） |
| 日报前端 | `frontend/src/api/smsReport.js` + `views/Data/components/SmsReportDialog.vue` | 数据中心日报对话框（通道 tab 只读） |
| 汇总 | `sms_summary.py` | 窗口水位 + 读已结算周期 OK/NG |
| 离线 | `sms_offline_queue.py` | SQLite 离线重试队列（`generic_http` / `wxpusher`） |
| 前端 | `frontend/src/api/sms.js` + `views/Alarm/index.vue` | 报警页配置卡 |
| 调试工具 | `tools/sms_4g/` | 脱离主程序测 AT 模块 |

**不变量：**

1. **默认关闭**（`enabled=false`）——存量客户零差异。
2. **不在 `_trigger_event` 热路径发推送**——只读已落库结算周期做 12h 汇总。
3. **AT COM 必须独立**——禁止与灯塔/蜂鸣器共用同一串口。
4. **Provider 五选一**：`at_modem` | `generic_http` | `wxpusher` | `aliyun` | `tencent`（`mock` 仅试发，不可持久化）。**通道实现只能进 `sms_providers/`，禁止再造第二套适配器**（v3.46 曾有 `sms_adapters/` 平行实现，已删）。
5. **汇总调度二选一**：`summary_schedule_mode=rolling_12h`（默认：后端**冷启动**以启动时刻重新锚定，配置热替换不重锚）或 `daily_shift`（`shift_start_hour`/`shift_end_hour`，`send_night_window` 默认 false）。
6. 启停接线在 `backend/main.py`：`_start_sms_summary_scheduler(reset_rolling_anchor=True)` / `_shutdown_sms_notifications()`。

---

## 二、排查决策树

### 2.1 完全收不到短信 / 微信

1. 报警页「NG 短信/微信推送」总开关是否打开？`GET /api/v1/sms/config` → `enabled`
2. Provider 与目标：
   - `at_modem` / `generic_http`：收件人手机号非空、格式合法
   - `wxpusher`：`app_token` 非空，且 `uids` 或 `topic_ids` 至少一个非空（**不强制手机号**）
3. Provider 通路：
   - `at_modem`：COM 是否选对？用 `tools/sms_4g` 或 `POST /api/v1/sms/test` 单独发测
   - `generic_http`：工控机能否访问 `api_url`？看后端日志 `[SMS]`
   - `wxpusher`：工控机能否访问 `wxpusher.api_url`（默认 `https://wxpusher.zjiecode.com/api/send/message`）？微信是否已关注应用拿到正确 UID？
4. 汇总是否已满窗？滚动 12h：水位在 `sms_summary_state.json`，**每次后端冷启动会重锚到启动时刻**，未满 12h 不发；班次模式看是否到 `shift_end_hour`
5. 看离线队列是否堆积：`sms_offline_queue.db`（与 config 同目录；仅 HTTP 类通道）

### 2.2 AT 串口失败 / 与灯塔冲突

- 症状：开短信后灯塔不亮，或短信模块无响应
- 根因：COM 选成了灯塔口，或波特率不对
- 修法：刷新短信 COM 列表，选模块独立口；灯塔配置保持原 COM；两边不要交叉

### 2.3 云通道 4xx/超时

- 查 `generic_http`：`api_url` / `token` / `access_key` / `template_id` / `field_mapping`
- `verify_ssl=false` 仅内网自签证书场景临时用
- 用测试发送端点验证，勿等 12h 窗

### 2.4 WxPusher 收不到

1. `provider` 是否为 `wxpusher`，总开关是否开启
2. `app_token` / `uids`（或 `topic_ids`）是否正确；UID 是否来自当前应用关注
3. 工控机外网：能否访问 WxPusher API（内网断外网测不了）
4. 后端日志 `[SMS]`：`WXPUSHER_1001` 等业务码通常是 token/参数问题（不重试）；`HTTP_OFFLINE` / `HTTP_TIMEOUT` 会走离线队列
5. 报警页「测试发送」看即时入队回执，勿只等 12h 窗

### 2.5 配置保存后不生效

- PUT 会 `_replace_sms_service`：旧服务 `shutdown`，新服务按需重启汇总线程
- 权限：保存需 `alarm.edit`
- 配置损坏会回退默认关闭——查日志 `SmsConfigError`

### 2.6 担心热路径卡顿

- 确认：`source_event_trigger_mixin._trigger_event` **不调用**短信/微信发送
- 汇总线程名 `sms-12h-summary`，只轮询 DB 结算数据

---

## 三、关键文件与落盘

| 落盘 | 说明 |
|---|---|
| `sms_config.json` | 运行时配置（gitignore；含 `wxpusher{}` 命名空间） |
| `sms_summary_state.json` | 汇总窗口水位（gitignore） |
| `sms_offline_queue.db` | 离线重试队列 |

路径相对 `TIANJUN_DATA_DIR` / 后端数据目录（与 `SmsConfigStore.path` 同级）。

---

## 四、回归入口

```bash
# conda env: tianjun
python -m pytest tests/test_sms_*.py tests/test_sms_shutdown_wiring.py -q   # 含 test_sms_report.py 日报
python -m pytest tests/e2e_browser/test_alarm_page.py tests/e2e_browser/test_sms_report_dialog.py -q
# 硬件/真机：tools/sms_4g；云短信或 WxPusher 用报警页「测试发送」
# 日报可见浏览器 UAT：python tests/uat/uat_sms_report_ui.py（前端 6003 / 后端 8003）
```

---

## 五、与 debug-alarm 的边界

| | 灯塔/蜂鸣器 | 短信 / 微信推送 |
|---|---|---|
| skill | `debug-alarm` | `debug-sms`（本文件） |
| 配置 | `alarm_config.json` | `sms_config.json` |
| API | `/api/v1/alarm/*` | `/api/v1/sms/*` |
| 触发 | `_trigger_event` → `alarm_router` | 12h 汇总调度线程 |
| 串口 | 灯塔 COM | AT 通道时**必须另一路** COM；WxPusher 无串口 |

---

## 六、每日短信日报（v3.46）差异速查

与 NG 汇总同通道不同链路：日报走 `services/sms_report.py` 自己的 APScheduler cron（`_start_sms_report`），规则/日志/计数器台账三表在 `models/notify_models.py`（`sms_report_rules` / `sms_send_logs` / `counter_daily_stats`）。

排查顺序：

1. **通道**：`GET /api/v1/sms-report/providers` → `active_provider` 是否预期（来自共享 `sms_config.json`；日报不看 `enabled` 总开关——那是 NG 通知的开关，日报看规则自己的 `enabled`）
2. **规则**：`GET /api/v1/sms-report/rules` → `enabled` / `cron_expression` / `next_run_time` / `phone_numbers`
3. **变量**：`POST /rules/{id}/preview` 渲染当前窗口变量不发送；计数器当日增量来自 `counter_daily`（内存日桶节流落库，`flush_now()` 后再查）
4. **发送**：`POST /rules/{id}/test-send?use_mock=true` 全链路不发真短信；日志表 `sms_send_logs` 的 `error_msg` 有逐号失败明细
5. **正文口径**：云模板通道（aliyun/tencent）用平台审核模板渲染，规则 `template_code` 可覆盖默认；内容式通道（at_modem/generic_http/wxpusher）用规则 `content_template`（`${变量名}` 占位）本端渲染
6. **插件干预**：`daily_report_before_send` returnable hook 可改写变量/收件人/跳过（`skip_send is True`）

**日报不变量**：发送在调度线程，不在检测热路径；`counter_daily.record_for_host` 只累计正向增量；异常必须被 `_run_rule` 吞掉不外抛。
