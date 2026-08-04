# 短信中转服务 — 云审核过渡期个人 SIM 卡通道

> 场景：云短信「签名 + 模板」审核要好几天，客户体验等不了。
> 用自己的域名 + 云服务器 + 个人手机 SIM 卡先把日报短信真实发到客户手机，
> 审核通过后在软件「服务商设置」里切回阿里云/腾讯云，**日报规则零改动**。

## 链路

```
工厂主程序 (http_relay 适配器)
    │ POST https://your.domain/send  {phones, content}   Bearer token
    ▼
relay_server.py (你的云服务器, 零依赖)
    ▲ GET /pull 每 30~60s 轮询取任务                      token
    │
你的安卓手机 (MacroDroid / SmsForwarder)
    │ 用你的 SIM 卡发真实短信
    ▼
客户手机
```

手机是**主动轮询**云服务器，不需要公网 IP、不需要内网穿透，插电常联网即可。

## 一、云服务器部署（5 分钟）

```bash
# 上传本目录的 relay_server.py 到服务器, 然后:
nohup python3 relay_server.py --port 8080 --token 换成你的长随机串 > relay.log 2>&1 &

# 探活
curl http://127.0.0.1:8080/health
```

- 有域名建议套一层 nginx + https（`location / { proxy_pass http://127.0.0.1:8080; }`）；
  没配 https 也能用 `http://你的域名:8080`（记得云安全组放行端口）。
- 任务持久化在同目录 `sms_relay_queue.json`，重启不丢。
- `GET /tasks?token=xxx` 可随时看最近任务和发送状态。

## 二、手机端（推荐 MacroDroid，免费版够用）

新建宏，三步：

1. **触发器**：定期触发，间隔 1 分钟（或 30 秒）。
2. **动作 1**：HTTP 请求 GET `https://your.domain/pull?limit=3&token=你的token`，
   响应存变量 `resp`。
3. **动作 2**：遍历 `resp.tasks`（JSON 数组迭代），对每条任务：
   - 「发送短信」动作：号码 = `phones` 逐个，内容 = `content`；
   - （可选）HTTP POST `/report?token=...`，body `{"task_id":"...","success":true}` 回报结果。

> 备选：开源 [SmsForwarder](https://github.com/pppscn/SmsForwarder) 的「主动控制·服务端」
> 也能收指令发短信，但需要手机可被公网访问（要配 frp 穿透），部署比轮询麻烦，不推荐赶工期用。

## 三、软件侧配置

数据中心 → 短信日报 → 服务商设置：

| 字段 | 填什么 |
|---|---|
| 服务商 | **自建 HTTP 中转** |
| 中转服务地址 | `https://your.domain/send` |
| 鉴权 Token | 与 `--token` 一致 |
| 短信正文模板 | 例：`【天军视觉】${date}日报: 总数${total_cycles} 良率${yield_rate}%` |

`${变量名}` 引用日报规则里「变量映射」的变量名。留空则按 `变量:值` 自动拼接。
配完在规则上点「真实试发」，先发到你自己手机验一遍，再加客户号码。

## 四、注意事项

- **频控**：个人 SIM 卡发短信有运营商风控（普遍日发 100~200 条内、避免营销措辞、
  同内容高频会被拦）。日报一天几条完全安全，但别拿这个通道群发。
- **这是过渡通道**：云平台签名/模板审核通过后，服务商设置切回阿里云/腾讯云即可，
  规则、手机号、变量映射全部沿用。
- **token 保密**：token 即发送权限，用 32 位以上随机串，别用弱口令。
