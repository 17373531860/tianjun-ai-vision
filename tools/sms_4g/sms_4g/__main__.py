"""允许从工具目录执行 ``python -m sms_4g``。"""

from .gui import run_gui

raise SystemExit(run_gui())
