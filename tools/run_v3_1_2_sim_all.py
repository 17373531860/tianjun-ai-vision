"""v3.1.2 全量仿真测试一键跑.

把 v3.1.2 期间所有改动对应的仿真脚本串起来一次性跑完, 输出表格汇总.

覆盖矩阵:
  脚本                            修复项 / 特性
  test_exposure_control.py        摄像头自动曝光控制 (FPS 锁 10 修复)
  test_broadcast_settle.py        多工位广播结算 (主工位带动全体)
  test_cluster_strategy.py        集群 station 合并策略 (latest / ok_lock)
  test_weight_pairing.py          称重器即时配对模式 (instant)
  test_workorder_binding.py       工单绑定范围 (project / channels / cluster)
  test_v3_1_2_misc.py             KeyError 防御 + match_thresh + 录制异常可见
"""
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = [
    ("曝光控制 (FPS 锁 10 修复)",          "tools/test_exposure_control.py"),
    ("广播结算 (主工位带动)",                "tools/test_broadcast_settle.py"),
    ("集群合并策略 (latest / ok_lock)",       "tools/test_cluster_strategy.py"),
    ("称重器即时配对",                       "tools/test_weight_pairing.py"),
    ("工单绑定范围",                         "tools/test_workorder_binding.py"),
    ("杂项 (KeyError / match_thresh / 录制异常)", "tools/test_v3_1_2_misc.py"),
]


def parse_unittest_summary(stdout: str) -> tuple[int, int]:
    """从 unittest 输出抽 (passed, failed). 兼容多种格式."""
    # 标准 unittest: "Ran 5 tests in 1.246s\n\nOK"  或  "FAILED (failures=1)"
    rans = re.findall(r'Ran (\d+) tests in', stdout)
    fails = re.findall(r'failures=(\d+)|errors=(\d+)', stdout)
    total = sum(int(x) for x in rans)
    failed = 0
    for f, e in fails:
        failed += int(f or 0) + int(e or 0)
    if total > 0:
        return total - failed, failed
    # 备用解析: "=== Total: 7, Passed: 7, Failed: 0 ==="
    m = re.search(r'Total:\s*(\d+),\s*Passed:\s*(\d+),\s*Failed:\s*(\d+)', stdout)
    if m:
        return int(m.group(2)), int(m.group(3))
    # 备用解析: "=== 11/11 通过 ===" / "=== 总结: 32 通过 / 0 失败 ==="
    m = re.search(r'(\d+)/(\d+)\s*通过', stdout)
    if m:
        passed, total = int(m.group(1)), int(m.group(2))
        return passed, total - passed
    m = re.search(r'总结:\s*(\d+)\s*通过\s*/\s*(\d+)\s*失败', stdout)
    if m:
        return int(m.group(1)), int(m.group(2))
    return 0, 0


def run_one(label: str, script: str) -> dict:
    t0 = time.time()
    proc = subprocess.run(
        [sys.executable, script],
        cwd=ROOT,
        capture_output=True, text=True, timeout=180,
    )
    elapsed = time.time() - t0
    out = proc.stdout + proc.stderr
    passed, failed = parse_unittest_summary(out)
    return {
        "label": label,
        "script": script,
        "exit": proc.returncode,
        "passed": passed,
        "failed": failed,
        "elapsed": elapsed,
        "ok": proc.returncode == 0 and failed == 0 and passed > 0,
        "tail": out.splitlines()[-3:] if out else [],
    }


def main():
    print("=" * 72)
    print(" v3.1.2 全量仿真测试")
    print("=" * 72)
    results = []
    for label, script in SCRIPTS:
        sys.stdout.write(f"  [跑] {label:<40s} ... ")
        sys.stdout.flush()
        try:
            r = run_one(label, script)
            mark = "PASS" if r["ok"] else "FAIL"
            sys.stdout.write(f"{mark} ({r['passed']}/{r['passed']+r['failed']}, {r['elapsed']:.1f}s)\n")
        except subprocess.TimeoutExpired:
            r = {"label": label, "script": script, "exit": -1,
                 "passed": 0, "failed": 0, "elapsed": 180.0,
                 "ok": False, "tail": ["timeout"]}
            sys.stdout.write("TIMEOUT\n")
        results.append(r)

    # 汇总表
    print()
    print("=" * 72)
    print(" 汇总")
    print("=" * 72)
    header = f"{'用例':<42s}{'通过/总':>10s}{'耗时':>9s}  {'结果':<5s}"
    print(header)
    print("-" * len(header))
    total_p = total_t = 0
    for r in results:
        total = r["passed"] + r["failed"]
        total_p += r["passed"]
        total_t += total
        line = f"{r['label']:<42s}{r['passed']:>4d}/{total:<5d}{r['elapsed']:>7.1f}s  {'PASS' if r['ok'] else 'FAIL':<5s}"
        print(line)
    print("-" * len(header))
    overall = "PASS" if all(r["ok"] for r in results) else "FAIL"
    print(f"{'合计':<42s}{total_p:>4d}/{total_t:<5d}  {'':>7s}  {overall}")
    print()
    if overall == "PASS":
        print("[v3.1.2] 所有仿真路径全部通过, 可发布.")
        sys.exit(0)
    else:
        print("[v3.1.2] 有失败的用例, 请查看上方日志.")
        for r in results:
            if not r["ok"]:
                print(f"\n  -- {r['label']} ({r['script']}) 失败尾部 --")
                for line in r["tail"]:
                    print(f"     {line}")
        sys.exit(1)


if __name__ == '__main__':
    main()
