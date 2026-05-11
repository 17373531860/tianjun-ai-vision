# G1.5 source.py cycle_end → 插件 hook 触发点 — 完工证据

> 解决 `docs/plugin-system/implementation/ISSUES.md` §运行时缺口 G1.5：
> Tier 3 插件 hook 在 G1 完工时已能 register，但 `source.py` 还没接 `plugin_manager.registry.hooks.fire(...)`，
> 真实 cycle_end 时 demo hook `on_cycle_end_post(ctx)` **不会被调**。

## 改动

- **改 `backend/api/source_session_lifecycle_mixin.py` `end_cycle()`**：在「v3.5.0 周期性强制动作判定」之后、Scanner resume / Container 清理之前插入：

```python
# v3.7 / G1.5: 触发 active 插件的 cycle_end/post_cycle/post hook.
# 独立 try/except — 插件抛错绝不影响主程序后续步骤
try:
    from backend.plugin_system.manager import plugin_manager
    if plugin_manager.registry is not None:
        plugin_ctx = {
            "channel_id": self.channel_id,
            "cycle_id": cycle.id,
            "cycle_uuid": cycle.cycle_uuid,
            "session_id": cycle.session_id,
            "is_good": bool(is_good),
            "result": "OK" if is_good else "NG",
            "judgement": "OK" if is_good else "NG",
            "event_id": event_id,
            "event_name": event_name,
            "reason": reason,
            "duration": cycle.duration,
            "step_sequence": cycle.step_sequence or [],
            "project_id": self.project_config.get("id") if self.project_config else None,
        }
        plugin_manager.registry.hooks.fire(
            "cycle_end", "post_cycle", "post", plugin_ctx
        )
except Exception as e:
    print(f"[Plugin] cycle_end hook 触发异常 (已隔离, 主流程继续): {e}")
```

- **改 `backend/plugin_system/registry.py` `HooksRegistry.fire`**：触发后追加 `[Plugin][cc] hook fired: type/phase/when handlers=N cycle_id=X` INFO 日志，便于客户现场排错
- **新增 `backend/api/test_runtime_routes.py` `POST /api/v1/test/synthetic/fire-plugin-cycle-end`**：仅 `RUNTIME_MODE=test` 挂载的调试端点；直接触发一次 hook fire 用于 UAT，避免依赖 synthetic 推理链路
- **新增 `tests/plugin_system/test_g1_5_cycle_end_hook.py`**：4 个回归护栏（ctx 字段集合锁定 / demo hook 消费契约 / 异常 swallow / 空 registry 安全）

## 影响分析（按 modify-source skill 第 15 节填表）

```text
修改内容: source_session_lifecycle_mixin.end_cycle 末尾追加 try/except + plugin_manager.registry.hooks.fire 调用
所属层:   SessionLifecycleMixin (mixin 文件单点)
影响线程: 推理线程 (end_cycle 由结算路径调用); 触发的 hook 在同线程执行
影响调用链: 仅本文件; 现有 MES Hook / 周期性动作 / Scanner resume / Container 清理位置不变
兼容层影响: 无 (不动 _XXX_FIELDS / _XXX_METHOD_ALIASES)
state_init 影响: 无 (不增加 VSM 状态字段)
DB 影响:   无 (插件用 host.get_db_session() 自行决定)
MES Hook 影响: 无 (在 MES on_cycle_end 之后, 独立 try/except)
前端影响: 无
风险等级: 低 (try/except 隔离 + hook 注册为空时 fire 直接返回 [])
建议测试: 单元 (本文件 4 测) + VSM 冒烟 + RUNTIME_MODE=test 调 fire 端点验日志
```

## 客户视角验收

| 断言 | 实际 | 结果 |
|---|---|---|
| **单元测试** ctx 字段集合锁定不可漂移 | PASSED | ✅ |
| **单元测试** Tier 3 demo `on_cycle_end_post` 能消费 ctx 返回合理 dict | PASSED | ✅ |
| **单元测试** 插件抛错 → fire 不抛回 | PASSED | ✅ |
| **单元测试** 无 registry → fire 返回 [] 安全 | PASSED | ✅ |
| **冒烟** `VideoSourceManager(0)` 构造不炸（验证 source 改动无破坏） | OK + has-a 组件全就位 | ✅ |
| **端到端** `POST /api/v1/test/synthetic/fire-plugin-cycle-end` → 后端日志 `[Plugin][internal-demo] hook fired: cycle_end/post_cycle/post handlers=1 cycle_id=4242` | logged | ✅ |
| **端到端** Demo hook 真消费 ctx 返回 `{cycle_id, result, message}` | NG case 返回正确 / OK case 返回正确 | ✅ |

证据：
- `unit_tests_result.txt` — 4 个单元测试结果
- `fire_result_ng.json` / `fire_result_ok.json` — 端到端 API 响应
- `backend_plugin_log.txt` — 后端 `[Plugin] hook fired ...` 日志

## **不**在 G1.5 范围（明确）

- ❌ **synthetic 推理链路修复**：观察到 synthetic 模式下 detections 不流到 InferenceLoop（推理线程跑但 fps_inference=0、detections=[]）。**这是 source.py 独立 bug**，独立 PR
- ❌ **F1 cycle_end 8-phase 拆分**：本 PR 只加 1 个 fire 触发点；完整 phase 框架（pre_cycle/main/post_cycle/finalize 各阶段独立 hook）属 F1 工程

## 复现

```bash
# 前置（同 G1）
export PLUGIN_SECRET_FILE=/tmp/tianjun-dev-keys/keygen/PLUGIN_SECRET.txt
export RUNTIME_MODE=test
./start_backend.sh

# 装 Tier 3 + 激活 + 重启
curl -X POST -F "file=@/tmp/tjv-signed/Fullstack_MES_Extension-1.0.0-internal-demo.tjvplugin" \
  http://localhost:8001/api/v1/plugins/install
curl -X POST http://localhost:8001/api/v1/plugins/internal-demo/activate
# 重启后端

# 触发 hook
curl -X POST "http://localhost:8001/api/v1/test/synthetic/fire-plugin-cycle-end?cycle_id=42&is_good=false"
# 期待: {"status":"fired","handlers_count":1,"results":[{...,"message":"internal-demo hook observed cycle_end"}]}

# 看日志
grep "hook fired" backend.log
# 期待: [Plugin][internal-demo] hook fired: cycle_end/post_cycle/post handlers=1 cycle_id=42
```
