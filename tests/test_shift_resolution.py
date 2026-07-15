# ==================== v3.35.1 自定义班次判定 (resolve_shift_label 纯函数) ====================
# 语义钉死: 某时刻属于"最近一个已开始的班次"; 早于当天所有班开始 → 昨天末班跨天延续。
# 与 v2.x 两班制边界口径一致 (白08:00/晚20:00 时 20:01 归晚班、07:59 归昨晚)。
from backend.api.source_session_lifecycle_mixin import resolve_shift_label

THREE = [
    {"name": "夜班", "start": "00:00"},
    {"name": "白班", "start": "08:00"},
    {"name": "午班", "start": "16:00"},
]


def test_three_shifts_basic():
    assert resolve_shift_label(THREE, "08:00") == "白班"   # 开始时刻含在本班
    assert resolve_shift_label(THREE, "12:30") == "白班"
    assert resolve_shift_label(THREE, "16:00") == "午班"
    assert resolve_shift_label(THREE, "20:01") == "午班"   # 用户强调的边界: 归最近已开始的班
    assert resolve_shift_label(THREE, "23:59") == "午班"
    assert resolve_shift_label(THREE, "00:00") == "夜班"
    assert resolve_shift_label(THREE, "07:59") == "夜班"


def test_wrap_before_first_shift():
    """首班不是 00:00 时, 更早的时刻归昨天最晚开始的班 (跨天延续)。"""
    shifts = [{"name": "白班", "start": "08:00"}, {"name": "晚班", "start": "20:00"}]
    assert resolve_shift_label(shifts, "07:59") == "晚班"   # 与两班制语义一致
    assert resolve_shift_label(shifts, "20:01") == "晚班"
    assert resolve_shift_label(shifts, "08:00") == "白班"


def test_unordered_input_is_sorted():
    shuffled = [THREE[2], THREE[0], THREE[1]]
    assert resolve_shift_label(shuffled, "12:00") == "白班"


def test_invalid_entries_skipped_and_fallback():
    # 有效班次不足 2 个 → None (调用方回退两班制)
    assert resolve_shift_label([], "12:00") is None
    assert resolve_shift_label([{"name": "白班", "start": "08:00"}], "12:00") is None
    assert resolve_shift_label([{"name": "", "start": "08:00"},
                                {"name": "晚班", "start": "20:00"}], "12:00") is None
    # 坏条目被跳过, 剩余仍够 2 个 → 正常判定
    mixed = [{"name": "白班", "start": "08:00"},
             {"bad": True}, {"name": "坏时刻", "start": "8点"},
             {"name": "晚班", "start": "20:00"}]
    assert resolve_shift_label(mixed, "21:00") == "晚班"
