# 天竣视觉检测系统 · 客户 MES 对接数据格式

> 本文档面向客户 MES 系统开发方, 说明天竣检测系统如何将检测结果推送到你们的 MES.
> 文档版本: v1.1 (对应天竣 v2.7.11+)

---

## 1. 概述

天竣在每一**箱**工件检测完成时, 会把该箱的汇总结果通过一次 HTTP 请求推送给你们的 MES.
事件名: `box_complete` (正常完成) / `box_timeout` (超时但仍推).

- **协议**: HTTP
- **请求方法**: POST (也支持 PUT / GET, 可配)
- **参数形式 4 选 1** (贵方任选其一, 在天竣界面配置, 无需改代码):
  1. **REST / JSON** — body 是纯 JSON: `{"order_no":"...","result":"NG"}`
  2. **Form-Data (打包)** — 一个字段塞 JSON 字符串: `param={"order_no":"..."}`
  3. **Form 平铺** — 字段在 body 里平铺: `order_no=xxx&result=NG&missing_items=A,B`
  4. **URL 参数** — 字段全塞 URL query: `POST /endpoint?order_no=xxx&result=NG&missing_items=A,B` (body 为空)
- **字符集**: UTF-8
- **鉴权**: 支持无鉴权 / Basic / Bearer Token / API Key / 自定义 Header (贵方任选)
- **重试**: 默认失败重试 3 次, 间隔 5 秒 (贵方系统幂等最好, 否则注意去重)

---

## 2. 请求格式 (4 种形式各自示例)

无论哪种形式, 业务字段都是同一组 "核心 4 字段":

| 字段名 | 类型 | 说明 | 示例 |
|---|---|---|---|
| `order_no` | string | 工单号 (天竣工单界面配置) | `"WO-20260423-001"` |
| `workpiece_id` | string | 工件/箱 ID (扫码器扫入) | `"SN-123457"` |
| `result` | string | 整体检测结果, `"OK"` 或 `"NG"` | `"NG"` |
| `missing_items` | array<string> / string | 缺失/不合格物品列表 (已按物料映射转换), OK 时为空 | NG: `["PART-001","MAT-SC-B02"]` 或 `"PART-001,MAT-SC-B02"` |

> `missing_items` 在"平铺/URL 参数"形式下会被拼成逗号分隔字符串; 在 JSON / Form-Data 打包形式下保持数组.

### 2.1 REST / JSON 形式

```
POST /your/mes/endpoint HTTP/1.1
Host: mes.customer.com
Content-Type: application/json
Authorization: Bearer eyJ...   ← 按鉴权方式决定

{"order_no":"WO-20260423-001","workpiece_id":"SN-123457","result":"NG","missing_items":["PART-001","MAT-SC-B02"]}
```

### 2.2 Form-Data (打包) 形式

```
POST /your/mes/endpoint HTTP/1.1
Content-Type: application/x-www-form-urlencoded

param=%7B%22order_no%22%3A%22WO-20260423-001%22%2C%22workpiece_id%22%3A%22SN-123457%22%2C%22result%22%3A%22NG%22%2C%22missing_items%22%3A%5B%22PART-001%22%2C%22MAT-SC-B02%22%5D%7D
```

字段名可改 (`param` / `data` / `json` / 其他), 值是 URL 编码后的 JSON 字符串.

### 2.3 Form 平铺形式

```
POST /your/mes/endpoint HTTP/1.1
Content-Type: application/x-www-form-urlencoded

order_no=WO-20260423-001&workpiece_id=SN-123457&result=NG&missing_items=PART-001%2CMAT-SC-B02
```

每个字段一个 key, 数组默认用逗号拼接 (可配成 JSON 字符串).

### 2.4 URL 参数形式

```
POST /your/mes/endpoint?order_no=WO-20260423-001&workpiece_id=SN-123457&result=NG&missing_items=PART-001%2CMAT-SC-B02 HTTP/1.1
Content-Length: 0
```

Body 为空, 所有字段都在 URL 里.

---

## 3. 业务数据示例 (解码后)

以下业务字段与"参数形式"无关, 4 种形式最终看到的数据都是这些.

### 3.1 OK 场景 (合格)

```json
{
  "order_no": "WO-20260423-001",
  "workpiece_id": "SN-123456",
  "result": "OK",
  "missing_items": []
}
```

### 3.2 NG 场景 (2 个物料缺失)

```json
{
  "order_no": "WO-20260423-001",
  "workpiece_id": "SN-123457",
  "result": "NG",
  "missing_items": ["PART-001", "MAT-SC-B02"]
}
```

### 3.3 NG 场景 (1 个物料缺失)

```json
{
  "order_no": "WO-20260423-001",
  "workpiece_id": "SN-123458",
  "result": "NG",
  "missing_items": ["PART-001"]
}
```

### 3.4 超时场景 (箱未齐即超时, 仍推送)

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

## 5. curl 调试示例 (4 种形式各一条)

### 5.1 REST / JSON

```bash
curl -X POST "http://your-mes-host/your/endpoint" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{"order_no":"WO-20260423-001","workpiece_id":"SN-123457","result":"NG","missing_items":["PART-001","MAT-SC-B02"]}'
```

### 5.2 Form-Data (打包 param)

```bash
curl -X POST "http://your-mes-host/your/endpoint" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d 'param={"order_no":"WO-20260423-001","workpiece_id":"SN-123457","result":"NG","missing_items":["PART-001","MAT-SC-B02"]}'
```

### 5.3 Form 平铺

```bash
curl -X POST "http://your-mes-host/your/endpoint" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d 'order_no=WO-20260423-001' \
  -d 'workpiece_id=SN-123457' \
  -d 'result=NG' \
  -d 'missing_items=PART-001,MAT-SC-B02'
```

### 5.4 URL 参数

```bash
curl -X POST "http://your-mes-host/your/endpoint?order_no=WO-20260423-001&workpiece_id=SN-123457&result=NG&missing_items=PART-001,MAT-SC-B02" \
  -H "Authorization: Bearer YOUR_TOKEN"
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
| 2 | 请求方式: POST / PUT / GET | `________________` |
| 3 | **参数形式 (4 选 1)**: REST/JSON · Form-Data 打包 · Form 平铺 · URL 参数 | `________________` |
| 4 | 若是 Form-Data 打包, 业务字段名: `param` / `data` / `json` / 其他 | `________________` |
| 5 | 鉴权方式: 无 / Basic / Bearer / API Key / 自定义 Header | `________________` |
| 6 | 若需鉴权, 具体 token/用户名密码/key | `________________` |
| 7 | 贵方响应成功判定字段 (如 `code=0` 或 `success=true`) | `________________` |
| 8 | 物料映射表 (见 §6, 可另附 Excel) | `________________` |
| 9 | 是否只在 NG 时推送, 还是 OK/NG 都要? | `________________` |
| 10 | 测试环境 URL + 生产环境 URL | `________________` |
| 11 | 最好给我们一个能打通的 curl 或 Postman 示例, 以便对齐 | `________________` |

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

- 项目: 天竣视觉检测系统 v2.7.11+
- 文档版本: v1.1 (新增 Form 平铺 / URL 参数两种形式)
