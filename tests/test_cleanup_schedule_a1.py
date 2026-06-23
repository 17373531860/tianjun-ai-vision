"""
A1 回归测试: 每天固定时点清理 + 清理互斥锁。

客户实锤: "自动删除只有重启软件之后才会自动删除"。
根因: 清理是 24h Timer, 工控机日常关机/重启几乎等不到第二次触发, 实际只有开机那一下清。

修复:
  - 加"每天固定时点清理"(cleanup_daily_time="HH:MM"), 到点必清, 不靠连续运行 24h;
  - 加清理互斥锁, 防止"开机清理/固定时点/手动立即"三者并发删库。

本测试锁定:
  1. 固定时点秒数计算正确, 非法时点返回 None;
  2. 持锁时 _perform_auto_cleanup_safe 跳过(返回 False), 释放后能执行(返回 True);
  3. 配置端点对时点格式做校验(非法 400, 合法落库, 空=关闭)。
"""


def test_a1_seconds_until_next_daily(client):
    from backend.main import _seconds_until_next_daily
    from datetime import datetime, timedelta

    future = (datetime.now() + timedelta(minutes=5)).strftime("%H:%M")
    s = _seconds_until_next_daily(future)
    assert s is not None and 0 < s <= 86400, f"固定时点秒数异常: {s}"

    assert _seconds_until_next_daily("25:00") is None
    assert _seconds_until_next_daily("9:99") is None
    assert _seconds_until_next_daily("abc") is None
    assert _seconds_until_next_daily("") is None


def test_a1_cleanup_lock_skips_when_busy():
    from backend.db.database import SessionLocal
    from backend.api.sessions_maintenance import (
        _cleanup_lock, _perform_auto_cleanup_safe, _set_system_config,
    )

    db = SessionLocal()
    try:
        _set_system_config(db, "auto_cleanup", "true", "")
        _set_system_config(db, "retention_days", "30", "")
        db.commit()
    finally:
        db.close()

    # 模拟"已有清理在执行": 手动持锁
    assert _cleanup_lock.acquire(blocking=False) is True
    try:
        assert _perform_auto_cleanup_safe() is False, "持锁时应跳过本次清理"
    finally:
        _cleanup_lock.release()

    # 释放后能正常执行
    assert _perform_auto_cleanup_safe() is True, "释放锁后应能正常执行清理"


def test_a1_daily_time_config_validation(client):
    base = "/api/v1/data/cleanup-settings"

    r = client.put(base, json={"cleanup_daily_time": "25:99"})
    assert r.status_code == 400, "非法时点应被拒绝"

    r = client.put(base, json={"cleanup_daily_time": "03:00"})
    assert r.status_code == 200, r.text
    assert r.json()["cleanup_daily_time"] == "03:00"

    r = client.put(base, json={"cleanup_daily_time": ""})
    assert r.status_code == 200
    assert r.json()["cleanup_daily_time"] == "", "空字符串应表示关闭固定时点"
