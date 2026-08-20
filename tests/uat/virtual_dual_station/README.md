# 虚拟双工位 UAT 剧本（捷昌 B 站整改 · v3.51.1 战役归档）

无摄像头、无扫码枪、无模型，全虚拟复现捷昌 B 站「一枪广播双工位」现场：
synthetic 剧本源跑真实 tracking 管线 + 假 TCP 扫码器跑真实 E 模式协议。

> 这是人眼可查证据的 UAT 剧本（AGENTS.md T7 层），不进 CI。
> 对应 CI 回归在 `tests/test_settle_on_complete.py`（豁免标签/强制收账）、
> `tests/test_fire_external_event.py`（toast_id）、
> `tests/channel_group/test_unified_ok_report_v3_51.py`（统一播报 + TTL）。

## 启动方法

```bash
# 1. 后端 (test 模式, 独立数据; 端口 8001)
RUNTIME_MODE=test ENABLE_DEV_MOCKS=1 python -u -m uvicorn backend.main:app --port 8001

# 2. 假扫码器 (TCP 设备口 24001 / 控制口 24002)
python tests/uat/virtual_dual_station/fake_scanner.py

# 3. 一键配环境: 双工位 + 两个 tracking 项目(齐件即结算+扫码门) +
#    E模式广播扫码器 + synchronized_all_ok 工位组(统一播报开)
python tests/uat/virtual_dual_station/vsetup.py

# 4. 逐个跑剧本 (每个自带断言与 PASS/FAIL 汇总)
python tests/uat/virtual_dual_station/vcase1.py   # 主链路 18 断言
python tests/uat/virtual_dual_station/vcase2.py   # 删码复活 10 断言
python tests/uat/virtual_dual_station/vcase3.py   # 统一播报 16 断言
python tests/uat/virtual_dual_station/vcase4.py   # 残留大扫除 16 断言
```

`scanctl.py`：手动注码/查灯态（`SCAN <码>` / `STATE` / `CLEAR`）。

## 剧本覆盖矩阵

| 剧本 | 覆盖 |
|---|---|
| vcase1 | 扫码门(扫前不入账) / 双工位同码状态一致 / 先后合格只报统一那次 / OK 后在场物品不复账 / strict_ok_dedup 拒已 OK 码 / **拒码后灯必须回亮**(v3.51.1 修复) / LON-LOFF 全链路 |
| vcase2 | 数据页 clear/all 联动解锁工件 / MES 删工件后码当新码 / 检测中重复扫码不产生多余工件、双工位状态一致 |
| vcase3 | 同时合格统一播报恰好一次 / 个体 OK 落日志但不可见 / 超时 fallback 只补播已 OK 工位 / **超时 force_ng 全组统一报 NG 不播"已合格"**(v3.51.1) / **新码强制收挂账旧账为 NG**(v3.51.1 修复) / 组内 NG 不播统一合格 / 开关关回到个体播报 |
| vcase4 | 用户"ABCD、D 最后放、合格后残留 OK"场景 / 合格后物品未撤走扫新码不二次入账不残留 / **撤走后同位置放不同品种不被豁免吞掉**(v3.51.1 修复) / clear/range 解锁重扫 / 全程灯态断言 |

## 本战役发现并修复的 4 个产品 bug（均已收编 v3.51.1）

1. `source_session_lifecycle_mixin.force_settle_pending_cycle`：挂账周期
   (懒开 DB cycle, uuid=None) 被 uuid 守门静默跳过 → "新码强制收旧账"失效。
2. `channel_group_coordinator`：超时 force_ng 档仍给已 OK 工位播"已合格"；
   `_pending_override` 无 TTL 跨轮污染。
3. `mes_hooks` + `scanner`：strict_ok_dedup/冷却/在检拒码后不通知扫码器
   → 灯永不回亮，产线卡死。
4. `source_tracking_mixin` 豁免漂移兜底不看 label → 旧件拿走几秒内同位置
   放"另一种"新品被吞、永不入账；`source_event_trigger_mixin`
   fire_external_event_response 默认 toast_id='ng' → 统一播报"全部合格"
   弹红色不合格 Toast。
