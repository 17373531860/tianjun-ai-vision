"""USB 4G 短信独立测试工具入口。"""

from __future__ import annotations

import argparse
import os
import sys
import unittest
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent


def run_self_tests() -> int:
    """运行工具目录内的离线单元测试，不连接真实 COM。"""

    suite = unittest.defaultTestLoader.discover(
        str(BASE_DIR / "tests"), pattern="test_*.py"
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="天军 AI 视觉 USB 4G 短信测试工具")
    parser.add_argument("--self-test", action="store_true", help="运行离线单元测试")
    parser.add_argument("--gui-smoke", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.self_test:
        return run_self_tests()

    if args.gui_smoke:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    try:
        from sms_4g.gui import run_gui
    except ImportError as exc:
        print(
            "缺少独立工具依赖。请在 tools/sms_4g 目录执行：\n"
            "  python -m pip install -r requirements.txt\n"
            f"原始错误：{exc}",
            file=sys.stderr,
        )
        return 2
    return run_gui(smoke_quit_ms=250 if args.gui_smoke else None)


if __name__ == "__main__":
    raise SystemExit(main())
