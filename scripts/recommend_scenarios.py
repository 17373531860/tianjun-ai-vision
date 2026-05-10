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


def _scan_diff(refs: List[str]) -> tuple[set, set, list]:
    """返回 (推荐剧本 set, 建议测试 set, hit 详情行列表)"""
    files = _git_diff_files(refs)
    seen_scenarios: set = set()
    seen_tests: set = set()
    hit_lines: list = []
    for keyword, scenarios, tests, reason in RULES:
        hit = [f for f in files if keyword in f]
        if not hit:
            continue
        hit_lines.append(f"--- 命中规则: {keyword!r}  原因: {reason}")
        for f in hit:
            hit_lines.append(f"    diff 涉及: {f}")
        for s in scenarios:
            seen_scenarios.add(s)
        for t in tests:
            seen_tests.add(t)
    return seen_scenarios, seen_tests, hit_lines


def main(argv: List[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="按 git diff 推荐 synthetic 剧本与测试")
    ap.add_argument("refs", nargs="*", default=["origin/main"],
                    help="git diff 比较 ref(s)：默认 origin/main")
    ap.add_argument("--run", action="store_true",
                    help="看完推荐直接 pytest 跑命中的 BDD/端到端测试")
    ap.add_argument("--full", action="store_true",
                    help="未命中任何规则时也跑全套")
    ap.add_argument("--dry-run", action="store_true",
                    help="--run 模式下只打印 pytest 命令不执行")
    args = ap.parse_args(argv)

    refs = args.refs[:2]
    print(f"[recommend_scenarios] 比较 ref(s)：{refs}")

    files = _git_diff_files(refs)
    if not files:
        print("[recommend_scenarios] 没有改动文件 (或 git diff 失败)")
        if args.full and args.run:
            return _run_pytest(["tests/test_synthetic_full_flow.py", "tests/step_defs/"], dry_run=args.dry_run)
        return 0

    print(f"[recommend_scenarios] 改动 {len(files)} 个文件\n")
    scenarios, tests, hit_lines = _scan_diff(refs)
    for line in hit_lines:
        print(line)
    print()
    for s in sorted(scenarios):
        print(f"  [推荐剧本] tests/scenarios/{s}")
    for t in sorted(tests):
        print(f"  [建议测试] {t}")

    matched = bool(scenarios or tests)
    if not matched:
        print("\n[recommend_scenarios] 改动未命中任何已知规则。")
        if not args.full:
            print("  → 加 --full 跑全套：python scripts/recommend_scenarios.py --run --full")
            return 0

    if args.run:
        targets = sorted(tests) if matched else ["tests/test_synthetic_full_flow.py", "tests/step_defs/"]
        if args.full:
            targets = list(set(targets) | {"tests/test_synthetic_full_flow.py", "tests/step_defs/"})
        return _run_pytest(targets, dry_run=args.dry_run)

    return 0


def _run_pytest(targets: List[str], *, dry_run: bool = False) -> int:
    cmd = ["python", "-m", "pytest", *targets, "-v", "--tb=short"]
    print(f"\n[recommend_scenarios] pytest 命令: {' '.join(cmd)}")
    if dry_run:
        print("[recommend_scenarios] --dry-run 不执行")
        return 0
    proc = subprocess.run(cmd)
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
