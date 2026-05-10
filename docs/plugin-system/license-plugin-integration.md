# License × Plugin 对接清单

> 目标：插件系统不替代 License 系统，但必须复用 License 里的客户身份，确保插件只在对应客户机器上运行。

## 1. 两套系统的边界

| 系统 | 职责 | 不做什么 |
|---|---|---|
| License | 机器授权、有效期、客户身份、功能开关 | 不验证 `.tjvplugin` 文件完整性 |
| Plugin | 验签、客户码绑定、加载、错误隔离 | 不决定机器是否已激活 |

唯一交点：

```text
license_payload.customerName == plugin.json.customer_code
```

## 2. 后端需要的最小 License Payload

PluginManager 启动时需要拿到：

```json
{
  "valid": true,
  "customerName": "internal-demo",
  "machineId": "xxx",
  "expiresAt": "2030-01-01T00:00:00+00:00",
  "features": ["plugin"]
}
```

字段约束：

| 字段 | 必须 | 用途 |
|---|---|---|
| `valid` | 是 | false 时不加载任何插件 |
| `customerName` | 是 | 与 manifest `customer_code` 严格相等 |
| `machineId` | 建议 | 审计日志 |
| `expiresAt` | 建议 | Settings 显示 |
| `features` | 可选 | 未来可加 `plugin.tier3` 等功能开关 |

## 3. 校验顺序

```text
1. Electron / backend 已完成 License 激活校验
2. PluginManager 读取 active plugin manifest
3. 校验 customer_code 正则
4. 校验 customer_code 已在 customer-codes.md 对应的生产白名单中
5. 校验 license_payload.valid == true
6. 校验 license_payload.customerName == manifest.customer_code
7. 校验 files_digest
8. 校验 RSA signature
9. 校验 HMAC customer binding
10. 加载插件
```

## 4. 错误码建议

| 场景 | 错误码 | 客户可见文案 |
|---|---|---|
| License 无效 | `PLUGIN_LICENSE_INVALID` | 当前授权无效，插件未加载 |
| 客户码不一致 | `PLUGIN_CUSTOMER_MISMATCH` | 插件不属于当前客户 |
| License 未包含插件功能 | `PLUGIN_FEATURE_DISABLED` | 当前授权未开通插件功能 |
| HMAC 不匹配 | `PLUGIN_CUSTOMER_HMAC_FAIL` | 插件客户绑定校验失败 |

## 5. 前端 Settings 展示

Settings 插件页至少展示：

- 当前 License 客户码
- 当前 active 插件 customer_code
- 是否匹配
- 最近一次加载错误码
- 最近一次加载时间

## 6. 不在 v3.7 做的事

- 不做在线 license server 校验
- 不做插件级单独 license
- 不做每机独立 PLUGIN_SECRET
- 不做多个客户码共用一个插件包

## 7. 实施落点

| 模块 | 改动 |
|---|---|
| `electron/license-manager.js` | 确保 customerName 可被后端读取 |
| `backend/plugin_system/manager.py` | 接收 / 缓存 license_payload |
| `backend/api/plugins.py` | Settings 页展示 license/plugin 匹配状态 |
| `docs/plugin-system/customer-codes.md` | 维护客户码权威表 |
