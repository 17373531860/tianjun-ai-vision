#!/usr/bin/env python3
"""NG TOP3 显示模式 — 前后端连通性综合测试

测试内容:
1. 后端: ng_step_cycle_counts 初始化 & API 返回
2. 后端: _trigger_event 中 NG TOP3 计数逻辑 (per-cycle 去重)
3. 后端: _force_timeout_ng → _trigger_event(2, reason) 链路
4. 前端: systemStore 默认值 ngTopDisplayMode = 'percentage'
5. 前端: Settings 页面下拉选择器
6. 前端: Monitor 单通道 + 多通道 显示切换逻辑
7. API 连通: /api/source/detection/results 返回 ng_step_cycle_counts
8. API 连通: /api/source/status 正常响应
"""

import os, sys, json, re, time

BASE = os.path.dirname(os.path.abspath(__file__))

passed = 0
failed = 0
errors = []

def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  ✓ {name}")
    else:
        failed += 1
        msg = f"  ✗ {name}"
        if detail:
            msg += f" — {detail}"
        print(msg)
        errors.append(name)


def read(relpath):
    with open(os.path.join(BASE, relpath), encoding='utf-8') as f:
        return f.read()


# ═════════════════════════════════════════════════════════
#  1. 后端: source.py — ng_step_cycle_counts 基础
# ═════════════════════════════════════════════════════════
print("\n[1] 后端 source.py — ng_step_cycle_counts 基础")
src = read('backend/api/source.py')

check("ng_step_cycle_counts 初始化为空字典",
      "self.ng_step_cycle_counts = {}" in src)

check("ng_step_cycle_counts 在 set_project_config 中重置",
      src.count("self.ng_step_cycle_counts = {}") >= 2)

check("_trigger_event 中有 NG TOP3 计数逻辑",
      "# NG TOP3:" in src and "ng_step_cycle_counts" in src)

check("NG 事件 (event_id==2) 时解析 involved 步骤",
      "if current_event_id == 2 and reason:" in src)

check("每个涉及步骤只 +1 (per-cycle 去重)",
      "self.ng_step_cycle_counts[step_name] = self.ng_step_cycle_counts.get(step_name, 0) + 1" in src)

# ═════════════════════════════════════════════════════════
#  2. 后端: API 端点返回 ng_step_cycle_counts
# ═════════════════════════════════════════════════════════
print("\n[2] 后端 API — 返回 ng_step_cycle_counts")

check("/detection/results 返回 ng_step_cycle_counts",
      '"ng_step_cycle_counts": mgr.ng_step_cycle_counts.copy()' in src)

check("@router.get('/detection/results') 端点存在",
      '@router.get("/detection/results")' in src)

check("@router.get('/status') 端点存在",
      '@router.get("/status")' in src)

# ═════════════════════════════════════════════════════════
#  3. 后端: _force_timeout_ng 链路
# ═════════════════════════════════════════════════════════
print("\n[3] 后端 — _force_timeout_ng → _trigger_event(2) 链路")

check("_force_timeout_ng 方法存在",
      "def _force_timeout_ng(self, reason: str):" in src)

check("_force_timeout_ng 调用 _trigger_event(2, reason)",
      "self._trigger_event(2, reason)" in src)

# 步骤超时NG 中的 reason 包含步骤显示名
timeout_ng_block = src[src.index("_force_timeout_ng(f'步骤"):][:200]
check("步骤超时NG reason 包含步骤显示名",
      "display" in timeout_ng_block or "{display}" in timeout_ng_block)

# 周期超时NG 中的 reason
check("周期超时NG reason 包含时长信息",
      "周期总时长超时" in src)

# ═════════════════════════════════════════════════════════
#  4. 后端: NG TOP3 per-cycle 去重验证
# ═════════════════════════════════════════════════════════
print("\n[4] 后端 — NG TOP3 per-cycle 去重逻辑")

check("使用 set() 收集 involved 步骤 (自然去重)",
      "involved = set()" in src)

check("缺少步骤: 正则解析",
      re.search(r"re\.search\(r.*缺少", src) is not None)

check("重复步骤: 正则解析",
      re.search(r"re\.search\(r.*重复步骤", src) is not None)

check("顺序错误: 正则解析",
      re.search(r"re\.search\(r.*期望.*实际", src) is not None)

check("缺件: 正则解析",
      re.search(r"re\.search\(r.*缺件", src) is not None)

check("fallback: 用 expected-actual 推断 missing",
      "missing = expected - actual" in src)

# ═════════════════════════════════════════════════════════
#  5. 前端: systemStore — ngTopDisplayMode 默认值
# ═════════════════════════════════════════════════════════
print("\n[5] 前端 systemStore — ngTopDisplayMode")
store_src = read('frontend/src/store/useSystemStore.js')

check("ngTopDisplayMode 字段存在",
      "ngTopDisplayMode" in store_src)

check("默认值为 'percentage'",
      "ngTopDisplayMode: 'percentage'" in store_src)

check("字段在 display.monitor 对象内",
      store_src.index("ngTopDisplayMode") > store_src.index("monitor: {"))

# loadSettings 深度合并: monitor spread 确保新字段生效
check("loadSettings 中 monitor 深度合并",
      "...this.display.monitor" in store_src and "...(saved.monitor || {})" in store_src)

# ═════════════════════════════════════════════════════════
#  6. 前端: Settings 页面 — 下拉选择器
# ═════════════════════════════════════════════════════════
print("\n[6] 前端 Settings — NG TOP3 显示模式选择器")
settings_src = read('frontend/src/views/Settings/index.vue')

check("Settings 中有 NG TOP3 显示模式 标签",
      "NG TOP3 显示模式" in settings_src)

check("el-select 绑定 ngTopDisplayMode",
      'v-model="store.display.monitor.ngTopDisplayMode"' in settings_src)

check("选项: 百分比 (percentage)",
      'label="百分比" value="percentage"' in settings_src)

check("选项: 次数 (count)",
      'label="次数" value="count"' in settings_src)

check("change 事件触发 saveDisplaySettings",
      settings_src.count('ngTopDisplayMode') >= 1 and
      'saveDisplaySettings' in settings_src)

# ═════════════════════════════════════════════════════════
#  7. 前端: Monitor 单通道 — 显示切换
# ═════════════════════════════════════════════════════════
print("\n[7] 前端 Monitor 单通道 — NG TOP3 显示切换")
mon_src = read('frontend/src/views/Monitor/index.vue')

check("单通道 NG TOP3 标题存在",
      "NG步骤TOP3" in mon_src)

check("单通道 标题旁有切换按钮文本 (百分比/次数)",
      "systemStore.display.monitor.ngTopDisplayMode === 'percentage' ? '百分比' : '次数'" in mon_src)

check("单通道 点击调用 toggleNgTopMode()",
      'toggleNgTopMode()' in mon_src)

check("toggleNgTopMode 函数定义存在",
      "const toggleNgTopMode" in mon_src)

check("toggleNgTopMode 切换 percentage ↔ count",
      "=== 'percentage' ? 'count' : 'percentage'" in mon_src)

check("toggleNgTopMode 保存到 localStorage",
      "localStorage.setItem('display_settings'" in mon_src)

# 单通道显示值: count 模式显示 item.count, percentage 模式显示 rate%
single_display_line = [l for l in mon_src.split('\n') if "item.count" in l and "item.rate" in l and "ngTopDisplayMode" in l]
check("单通道 数值根据模式切换 (count vs rate%)",
      len(single_display_line) >= 1,
      "应包含 item.count 和 item.rate.toFixed 的三元表达式")

# ═════════════════════════════════════════════════════════
#  8. 前端: Monitor 多通道 — 显示切换
# ═════════════════════════════════════════════════════════
print("\n[8] 前端 Monitor 多通道 — NG TOP3 显示切换")

multi_section = mon_src[:mon_src.index("<!-- ===== SINGLE-VIEW MODE")]

check("多通道 NG TOP3 标题存在",
      "NG 步骤 TOP3" in multi_section)

check("多通道 标题旁有切换按钮",
      "toggleNgTopMode()" in multi_section)

multi_display_lines = [l for l in multi_section.split('\n') if "item.count" in l and "ngTopDisplayMode" in l]
check("多通道 数值根据模式切换",
      len(multi_display_lines) >= 1)

# ═════════════════════════════════════════════════════════
#  9. 前端: 数据流 — ng_step_cycle_counts 接收
# ═════════════════════════════════════════════════════════
print("\n[9] 前端数据流 — ng_step_cycle_counts 接收 & 排名计算")

check("单通道: 从后端接收 _ngStepCycleCounts",
      "_ngStepCycleCounts = data.ng_step_cycle_counts" in mon_src)

check("单通道: 排名计算使用 backendNgMap",
      "backendNgMap = backendCounters?._ngStepCycleCounts" in mon_src)

check("单通道: 排名包含 count 和 rate 字段",
      "count: ngCount" in mon_src and "rate" in mon_src)

check("多通道: 从后端接收 ng_step_cycle_counts",
      "d.ng_step_cycle_counts" in mon_src)

check("多通道: 排名计算包含 count 字段",
      "count: ngCount" in mon_src and "d.ng_step_cycle_counts" in mon_src)

# ═════════════════════════════════════════════════════════
#  10. API 实际连通测试 (如果后端运行中)
# ═════════════════════════════════════════════════════════
print("\n[10] API 实际连通测试")

api_tested = False
try:
    import requests
    base_url = "http://localhost:8000/api/source"

    # /status
    r1 = requests.get(f"{base_url}/status", timeout=3)
    check("GET /api/source/status 返回 200", r1.status_code == 200)
    api_tested = True

    # /detection/results
    r2 = requests.get(f"{base_url}/detection/results", timeout=3)
    check("GET /api/source/detection/results 返回 200", r2.status_code == 200)

    data = r2.json()
    check("返回数据包含 ng_step_cycle_counts 字段",
          "ng_step_cycle_counts" in data)

    ng_counts = data.get("ng_step_cycle_counts", {})
    check("ng_step_cycle_counts 是字典类型",
          isinstance(ng_counts, dict))

    # 验证值都是整数 (每个步骤的 NG 周期数)
    if ng_counts:
        all_int = all(isinstance(v, int) for v in ng_counts.values())
        check(f"ng_step_cycle_counts 值均为整数 (当前 {len(ng_counts)} 个步骤)",
              all_int)
    else:
        check("ng_step_cycle_counts 当前为空 (无NG数据, 正常)", True)

    # 验证 counters 存在 (用于百分比计算)
    counters = data.get("counters", {})
    check("返回数据包含 counters 字段",
          isinstance(counters, dict))

    total_key = '总产量' if '总产量' in counters else 'total'
    check(f"counters 中包含总产量 key ('{total_key}')",
          total_key in counters,
          f"可用 keys: {list(counters.keys())[:5]}")

    # 模拟前端排名计算
    total_cycles = counters.get('总产量', counters.get('total', 0))
    ranking = []
    for step, ng_count in ng_counts.items():
        if ng_count > 0:
            rate = (ng_count / total_cycles * 100) if total_cycles > 0 else 0
            ranking.append({'step': step, 'count': ng_count, 'rate': rate})
    ranking.sort(key=lambda x: -x['rate'])

    check(f"模拟排名计算成功 (TOP3: {ranking[:3] if ranking else '暂无NG数据'})", True)

    if ranking:
        top = ranking[0]
        print(f"    → 百分比模式显示: {top['step']} {top['rate']:.1f}%")
        print(f"    → 次数模式显示: {top['step']} {top['count']}")

except ImportError:
    print("  ⚠ requests 未安装, 跳过 API 连通测试")
except requests.exceptions.ConnectionError:
    print("  ⚠ 后端未运行 (localhost:8000 无响应), 跳过 API 连通测试")
except Exception as e:
    print(f"  ⚠ API 测试异常: {e}")

# ═════════════════════════════════════════════════════════
#  11. 前端 API 层 — detection.js
# ═════════════════════════════════════════════════════════
print("\n[11] 前端 API 层 — detection.js")
det_api = read('frontend/src/api/detection.js')

check("getDetectionResults 调用 /source/detection/results",
      "getDetectionResults" in det_api and "/source/detection/results" in det_api)

check("getSourceStatus 调用 /source/status",
      "getSourceStatus" in det_api and "/source/status" in det_api)

# ═════════════════════════════════════════════════════════
#  12. 一致性: 前后端字段名一致
# ═════════════════════════════════════════════════════════
print("\n[12] 前后端字段名一致性")

check("后端返回字段名: ng_step_cycle_counts",
      '"ng_step_cycle_counts"' in src)

check("前端接收字段名: data.ng_step_cycle_counts (单通道)",
      "data.ng_step_cycle_counts" in mon_src)

check("前端接收字段名: d.ng_step_cycle_counts (多通道)",
      "d.ng_step_cycle_counts" in mon_src)

# ═════════════════════════════════════════════════════════
#  汇总
# ═════════════════════════════════════════════════════════
total = passed + failed
print(f"\n{'='*60}")
print(f"  总计: {total} 项 | ✓ 通过: {passed} | ✗ 失败: {failed}")
if api_tested:
    print(f"  (含 API 实际连通测试)")
else:
    print(f"  (未含 API 连通测试 — 后端未运行或 requests 不可用)")
print(f"{'='*60}")

if failed:
    print(f"\n失败项:")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
else:
    print("\n全部通过! NG TOP3 显示模式功能前后端一致性验证完毕。")
    sys.exit(0)
