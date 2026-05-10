# 客户码注册表（Customer Codes Registry）

> **本文是插件系统 customer_code 的唯一权威列表。**
>
> 任何新加客户、内部测试码都必须先在本文 PR 注册才能被签名工具接受。
>
> 配套 design 文档：design/00_overview.md Q5、design/01_manifest_schema.md §3.1、design/07_distribution.md §3.2。

---

## 1. 命名规则（强约束）

```
customer_code 正则: ^[a-z][a-z0-9-]{2,19}$

要求:
- 全小写英文字母 + 数字 + 连字符
- 必须以字母开头
- 长度 3 ~ 20 字符
- 全局唯一（本表内）

例:
  ✓ acme        ✗ ACME            (大写)
  ✓ acme-tj     ✗ acme_tj         (下划线)
  ✓ tj2026      ✗ 2026tj          (数字开头)
  ✓ default     ✗ d               (太短)
  ✓ corp-china  ✗ very-long-code  (>20 字符)
```

## 2. 推荐取名 SOP（主作者维护）

```
A. 单一公司名直接简称
   "ACME Corporation"   → acme
   "天骏 AI"            → tianjun
   "晶上集团"           → jingshang  (拼音首字母也可用 js, 不建议过短)

B. 重名加后缀
   "ACME 上海工厂"      → acme-sh
   "ACME 西安工厂"      → acme-xa

C. 阶段后缀（短期项目）
   "客户 A 试用 2026"    → custa-trial-2026

D. 内部用前缀 internal-*
   内部测试码           → internal-test
   主作者样例插件       → internal-demo
```

> 命名是主作者主观判断，但**每次取名都尽量遵守 SOP**避免后期混乱。

## 3. 当前注册表

| customer_code | 类型 | 客户/用途 | 注册日期 | 主作者 | 备注 |
|---|---|---|---|---|---|
| `default`  | 保留 | 内置示例 / 占位 | 2026-05-08 | tianjun-ai-master | 出厂自带 demo 插件用，不发给真实客户 |
| `internal-test` | 内部 | CI / 集成测试 | 2026-05-08 | tianjun-ai-master | 仅 dev mode 加载，不发布 |
| `internal-demo` | 内部 | 三档示例插件 | 2026-05-08 | tianjun-ai-master | plugins-examples/ 用，发版前替换为真实客户码 |

> ⚠️ `default` / `internal-*` 三个前缀**保留给主作者**，不分配给真实客户。

### 3.1 真实客户列表

| customer_code | 客户名 | 国家/地区 | 首单日期 | License 已生效 | 当前激活插件 | 备注 |
|---|---|---|---|---|---|---|
| _(待填)_ | _(待填)_ | _(待填)_ | _(待填)_ | _(是/否)_ | _(版本)_ | _(备注)_ |

> 加新客户：本表加一行 + git commit，commit 信息使用 `[customer-code] add <code> for <client name>`。

## 4. 历史变更

```
2026-05-08  初始化，加 default / internal-test / internal-demo 三个保留码
```

## 5. customer_code 查询小工具

主作者本地查重：

```bash
# 查 customer_code 是否已注册
$ grep -P '^\| `[a-z][a-z0-9-]{2,19}`' docs/plugin-system/customer-codes.md
```

签名工具调用：

```bash
# scripts/plugin/sign-plugin.py 内
def check_customer_registered(cc):
    reg_file = "docs/plugin-system/customer-codes.md"
    content = open(reg_file).read()
    if f"`{cc}`" not in content:
        print(f"[WARN] customer_code '{cc}' 未在注册表登记")
```

## 6. 撤销 / 终止合作

如果某客户合作终止：

1. 不删本表行（保留审计）
2. 在"备注"列加 `[REVOKED YYYY-MM-DD]`
3. 主作者**不再签**该 customer_code 的新插件
4. 客户工控机上的旧插件**仍能用**（除非主作者 hotfix 把对应公钥失效）
5. 该 customer_code 永久不再分配给其他客户

例：

| customer_code | 备注 |
|---|---|
| `acme-old` | [REVOKED 2027-08-15] 原 ACME 子公司，2027 合作终止 |

## 7. 新增客户的完整流程

```
1. 销售签合约确认客户 → 邮件给主作者
2. 主作者按 §2 SOP 拟一个 customer_code
3. 主作者本地 git pull → 编辑本表 §3.1 → 加一行
4. git checkout -b add-customer-{cc}
5. git commit -m "[customer-code] add {cc} for {client name}"
6. git push + 创建 PR (要求至少 1 个 reviewer)
7. PR 合并后, 即可签发该 customer_code 的插件
8. License 系统同时下发给客户的 license.dat 内 customerName={cc}
   (License 与 plugin customer_code 必须严格一致, 见 design 02 §五)
```

## 8. 与 License 系统的关系

| 两套独立系统 | 但有 1 个交点 |
|---|---|
| License 系统由公司另一团队（设计 doc 没覆盖）| 插件加载时 license.customerName 必须 == plugin.manifest.customer_code |
| 私钥独立（Lic 用一对、Plugin 用另一对） | 同一个 customer_code 字符串 |
| 表互不影响 | design 02 §阶段 8 校验 |

> 本注册表是**插件视角**的真理。License 系统的客户表理论上要保持一致——若发生不一致，以本表为准并要求 License 系统同步。

## 9. FAQ

**Q: 一个客户能有多个 customer_code 吗？**
A: 不行。一个客户 license 只对应一个 customerName，`customer_code` 也唯一对应。如果客户要做"基础包"+"扩展包"，做成同一 customer_code 下不同 capabilities 即可。

**Q: 客户改名怎么办？**
A: 不改 customer_code（避免插件批量重签 + license 重发）。仅更新本表"客户名"列。

**Q: customer_code 能改吗？**
A: 不行。一旦签发，客户机器上的 license + plugin 都绑死。改名等同新客户流程。

**Q: 内部测试用什么？**
A: 用 `internal-test`。

**Q: 三档示例插件 (acme demo) 用什么 customer_code？**
A: 当前 plugins-examples/ 暂用 `acme`，发版前替换为 `internal-demo`。客户拿到的真实 ACME 插件用 `acme`（前提 ACME 客户登记到 §3.1）。

---

**本文最后更新**：2026-05-08
**维护**：主作者每次签发新 customer_code 必须 PR 更新本表
