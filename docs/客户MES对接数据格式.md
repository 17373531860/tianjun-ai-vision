# 天竣视觉检测系统 · 客户 MES 对接数据格式

> 本文档面向客户 MES 系统开发方, 说明天竣检测系统如何将检测结果推送到你们的 MES.
> 文档版本: v1.0 (对应天竣 v2.7.10)

---

## 1. 概述

天竣在每一**箱**工件检测完成时, 会把该箱的汇总结果通过一次 HTTP 请求推送给你们的 MES.
事件名: `box_complete` (正常完成) / `box_timeout` (超时但仍推).

- **协议**: HTTP POST
- **默认数据格式**: `application/x-www-form-urlencoded`, 业务数据放在一个字段内 (默认 `param`), 值为 JSON 字符串
- **可选数据格式**: `application/json` (纯 JSON body, 若贵方需要此方式请告知)
- **字符集**: UTF-8
- **鉴权**: 支持无鉴权 / Basic / Bearer Token / API Key / 自定义 Header (贵方任选)
- **重试**: 默认失败重试 3 次, 间隔 5 秒

---

## 2. 请求格式

### 2.1 请求头 (示例)

```
POST /your/mes/endpoint HTTP/1.1
Host: mes.customer.com
Content-Type: application/x-www-form-urlencoded
Authorization: Bearer eyJhbGciOi...     ← 按鉴权方式决定, 可选
User-Agent: python-requests/2.x
Accept: */*
```

### 2.2 请求体 (form-data 方式)

```
param=<URL编码后的 JSON 字符串>
```

解码后的 JSON 结构 (核心 4 字段):

| 字段名 | 类型 | 说明 | 示例 |
|---|---|---|---|
| `order_no` | string | 工单号 (天竣工单界面配置) | `"WO-20260423-001"` |
| `workpiece_id` | string | 工件/箱 ID (扫码器扫入) | `"SN-123457"` |
| `result` | string | 整体检测结果, `"OK"` 或 `"NG"` | `"NG"` |
| `missing_items` | array<string> | 缺失/不合格物品列表 (已按物料映射转换), OK 时为 `[]` | `["PART-001","MAT-SC-B02"]` |

---

## 3. 请求示例

### 3.1 OK 场景 (合格)

**实际发送的 form-data**:
```
param=%7B%22order_no%22%3A%22WO-20260423-001%22%2C%22workpiece_id%22%3A%22SN-123456%22%2C%22result%22%3A%22OK%22%2C%22missing_items%22%3A%5B%5D%7D
```

**param 解码后**:
```json
{
  "order_no": "WO-20260423-001",
  "workpiece_id": "SN-123456",
  "result": "OK",
  "missing_items": []
}
```

### 3.2 NG 场景 (2 个物料缺失)

**param 解码后**:
```json
{
  "order_no": "WO-20260423-001",
  "workpiece_id": "SN-123457",
  "result": "NG",
  "missing_items": ["PART-001", "MAT-SC-B02"]
}
```

### 3.3 NG 场景 (1 个物料缺失)

**param 解码后**:
```json
{
  "order_no": "WO-20260423-001",
  "workpiece_id": "SN-123458",
  "result": "NG",
  "missing_items": ["PART-001"]
}
```

### 3.4 超时场景 (箱未齐即超时, 仍推送)

**param 解码后**:
```json
{
  "order_no": "WO-20260423-001",
  "workpiece_id": "SN-TIMEOUT-001",
  "result": "NG",
  "missing_items": ["MISSING-StationB"]
}
```
> 说明: 超时场景下, `missing_items` 中未到位的**工位**会以 `MISSING-<工位名>` 形式给出.

---

## 4. 响应要求

### 4.1 成功响应 (HTTP 200)

贵方 MES 只要返回 HTTP 200 即视为接收成功, 响应体内容由贵方决定.
推荐格式 (天竣默认按此校验):
```json
{ "code": 0, "msg": "ok" }
```

天竣的"响应校验"可以配置检查哪个字段 (如 `code=0` 或 `success=true`), 请告知贵方实际字段.

### 4.2 失败响应

- HTTP 4xx/5xx → 天竣自动重试 3 次 (间隔 5 秒)
- 3 次后仍失败 → 记录到通讯日志, 不再重试 (下一箱完成时再推下一批)

---

## 5. curl 调试示例

```bash
curl -X POST "http://your-mes-host/your/endpoint" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d 'param={"order_no":"WO-20260423-001","workpiece_id":"SN-123457","result":"NG","missing_items":["PART-001","MAT-SC-B02"]}'
```

---

## 6. 物料名称映射说明

天竣内部步骤名默认是中文 (如"螺丝A"、"垫片B"), 但贵方 MES 通常用物料代码 (如 `PART-001`).
天竣会在**发送前**按你们提供的映射表做转换, **贵方收到的直接就是物料代码**.

**需要贵方提供**: 一份映射表 (Excel/CSV 即可), 格式:

| 天竣内部步骤名 | 贵方物料代码 |
|---|---|
| 螺丝A | PART-001 |
| 垫片B | MAT-SC-B02 |
| ... | ... |

---

## 7. 请贵方确认的事项 (重要 · 请逐项回复)

| # | 问题 | 贵方回答 |
|---|---|---|
| 1 | 接收接口的完整 URL (含 path) | `________________` |
| 2 | 请求方式: POST / PUT / 其他 | `________________` |
| 3 | Content-Type: form-data / application/json / 其他 | `________________` |
| 4 | 若是 form-data, 业务字段名: `param` / `data` / `json` / 其他 | `________________` |
| 5 | 鉴权方式: 无 / Basic / Bearer / API Key / 自定义 Header | `________________` |
| 6 | 若需鉴权, 具体 token/用户名密码/key | `________________` |
| 7 | 贵方响应成功判定字段 (如 `code=0` 或 `success=true`) | `________________` |
| 8 | 物料映射表 (见 §6, 可另附 Excel) | `________________` |
| 9 | 是否只在 NG 时推送, 还是 OK/NG 都要? | `________________` |
| 10 | 测试环境 URL + 生产环境 URL | `________________` |

---

## 8. 字段扩展 (可选)

若 §2.2 的 4 字段不够, 天竣还可以推送以下字段, 贵方按需勾选:

| 字段 | 类型 | 说明 |
|---|---|---|
| `box_serial` | string | 箱序列号 (天竣内部) |
| `total_stations` | int | 箱应包含的工位总数 |
| `completed_stations` | int | 实际完成的工位数 |
| `timestamp` | string (ISO 8601) | 推送时间戳 |
| `stations` | array<object> | 各工位明细 (含每站 OK/NG 和步骤) |

---

## 9. 联系方式

对接过程中如有疑问, 请联系天竣技术支持.

- 项目: 天竣视觉检测系统 v2.7.10+
- 文档版本: v1.0
