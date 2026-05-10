"""根据 git diff 推荐应跑的 synthetic 剧本。

用法（在仓库根）：
    python scripts/recommend_scenarios.py                  # 与 origin/main 比
    python scripts/recommend_scenarios.py HEAD~1           # 与某个 commit 比
    python scripts/recommend_scenarios.py main feature/x   # 比较两个 ref

输出格式：
    [推荐] tests/scenarios/ok_sequential_cycle.json   ← 因为 source_step_stats_mixin.py 改了
    [推荐] tests/scenarios/ng_missing_step.json       ← 同上
    [建议] e2e_browser/test_settings_page.py           ← 因为 Settings 视图改了
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Iterable, List, Tuple


# 改动文件正则关键词 → (推荐剧本 list, 推荐 e2e/bdd 测试 list, 理由)
RULES: List[Tuple[str, List[str], List[str], str]] = [
    (
        "source_step_stats_mixin",
        ["ok_sequential_cycle.json", "ng_missing_step.json"],
        ["tests/step_defs/test_core_detection_flow.py"],
        "步骤判定核心",
    ),
    (
        "source_settlement_mixin",
        ["ok_sequential_cycle.json"],
        ["tests/test_synthetic_full_flow.py"],
        "周期结算与写库",
    ),
    (
        "source_session_lifecycle_mixin",
        ["ok_sequential_cycle.json"],
        ["tests/test_synthetic_full_flow.py"],
        "session 生命周期",
    ),
    (
        "source_event_trigger_mixin",
        ["alarm_event.json"],
        ["tests/step_defs/test_alarm_event_chain.py"],
        "事件触发",
    ),
    (
        "source_events_check_mixin",
        ["alarm_event.json"],
        ["tests/step_defs/test_alarm_event_chain.py"],
        "事件检查",
    ),
    (
        "mes_hooks",
        ["ok_sequential_cycle.json"],
        ["tests/step_defs/test_mes_scan_workflow.py"],
        "MES Hook 链路",
    ),
    (
        "export_",
        ["ok_sequential_cycle.json"],
        ["tests/features/custom_export.feature", "tests/features/realtime_rules.feature"],
        "导出系统",
    ),
    (
        "Monitor/index.vue",
        ["smoke_static_label.json", "ok_sequential_cycle.json"],
        ["tests/e2e_browser/test_monitor_page.py", "tests/e2e_browser/test_sat_full_workflow.py"],
        "Monitor 视觉与计数器",
    ),
    (
        "Settings/index.vue",
        [],
        ["tests/e2e_browser/test_settings_page.py"],
        "Settings 页 UI",
    ),
    (
        "Source/index.vue",
        [],
        ["tests/e2e_browser/test_source_page.py"],
        "Source 页 UI",
    ),
    (
        "Data/index.vue",
        ["ok_sequential_cycle.json"],
        ["tests/e2e_browser/test_data_export_dialog.py"],
        "Data 页落库展示",
    ),
    (
        "synthetic_mixin",
        ["smoke_static_label.json", "ok_sequential_cycle.json", "ng_missing_step.json", "alarm_event.json"],
        ["tests/test_synthetic_full_flow.py", "tests/step_defs/test_source_connection.py"],
        "synthetic 源本身",
    ),
    (
        "test_runtime_routes",
        ["smoke_static_label.json"],
        ["tests/test_synthetic_full_flow.py"],
        "测试路由本身",
    ),
]


def _git_diff_files(refs: Iterable[str]) -> List[str]:
    cmd = ["git", "diff", "--name-only", *refs]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def main(argv: List[str]) -> int:
    if not argv:
        refs = ["origin/main"]
    elif len(argv) == 1:
        refs = [argv[0]]
    else:
        refs = [argv[0], argv[1]]

    print(f"[recommend_scenarios] 比较 ref(s)：{refs}")
    files = _git_diff_files(refs)
    if not files:
        print("[recommend_scenarios] 没有改动文件 (或 git diff 失败)")
        return 0

    print(f"[recommend_scenarios] 改动 {len(files)} 个文件\n")

    seen_scenarios: set = set()
    seen_tests: set = set()
    matched_any = False
    for keyword, scenarios, tests, reason in RULES:
        hit = [f for f in files if keyword in f]
        if not hit:
            continue
        matched_any = True
        print(f"--- 命中规则: {keyword!r}  原因: {reason}")
        for f in hit:
            print(f"    diff 涉及: {f}")
        for s in scenarios:
            if s in seen_scenarios:
                continue
            seen_scenarios.add(s)
            print(f"  [推荐剧本] tests/scenarios/{s}")
        for t in tests:
            if t in seen_tests:
                continue
            seen_tests.add(t)
            print(f"  [建议测试] {t}")
        print()

    if not matched_any:
        print("[recommend_scenarios] 改动未命中任何已知规则。可手动跑全套：")
        print("  pytest tests/test_synthetic_full_flow.py tests/step_defs -v")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
