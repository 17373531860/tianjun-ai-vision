"""B2 集群计时参数可配 (心跳间隔/离线超时/box扫描间隔), 存 SystemConfig KV, 当前值作默认。"""
import pytest

from backend.db.database import SessionLocal
from backend.services.cluster_collector import get_cluster_collector
from backend.models.models import SystemConfig


_KEYS = ["cluster.heartbeat_interval_sec", "cluster.slave_timeout_sec",
         "cluster.box_scan_interval_sec",
         "cluster.report_timeout_sec", "cluster.report_async"]


@pytest.fixture
def db():
    s = SessionLocal()
    # 清掉历史 KV, 保证默认态
    for k in _KEYS:
        for row in s.query(SystemConfig).filter(SystemConfig.key == k).all():
            s.delete(row)
    s.commit()
    yield s
    for k in _KEYS:
        for row in s.query(SystemConfig).filter(SystemConfig.key == k).all():
            s.delete(row)
    s.commit()
    s.close()


def test_defaults_when_unset(db):
    c = get_cluster_collector()
    t = c._read_timing_config(db)
    assert t == {"heartbeat_interval_sec": 5, "slave_timeout_sec": 20,
                 "box_scan_interval_sec": 30,
                 # v3.49: 副机上报超时 + 异步开关 (默认开)
                 "report_timeout_sec": 10, "report_async": 1}


def test_save_and_read_roundtrip(db):
    c = get_cluster_collector()
    out = c.save_timing_config(db, {
        "heartbeat_interval_sec": 3,
        "slave_timeout_sec": 15,
        "box_scan_interval_sec": 10,
    })
    db.commit()
    assert out == {"heartbeat_interval_sec": 3, "slave_timeout_sec": 15,
                   "box_scan_interval_sec": 10,
                   "report_timeout_sec": 10, "report_async": 1}
    # 重新读确认落库
    assert c._read_timing_config(db) == out


def test_out_of_range_ignored(db):
    c = get_cluster_collector()
    c.save_timing_config(db, {
        "heartbeat_interval_sec": 0,       # < 1 → 忽略, 保持默认
        "slave_timeout_sec": 999999,       # > 86400 → 忽略
        "box_scan_interval_sec": 12,       # 合法
    })
    db.commit()
    t = c._read_timing_config(db)
    assert t["heartbeat_interval_sec"] == 5      # 默认
    assert t["slave_timeout_sec"] == 20          # 默认
    assert t["box_scan_interval_sec"] == 12      # 生效


def test_partial_update_keeps_others(db):
    c = get_cluster_collector()
    c.save_timing_config(db, {"slave_timeout_sec": 25})
    db.commit()
    t = c._read_timing_config(db)
    assert t["slave_timeout_sec"] == 25
    assert t["heartbeat_interval_sec"] == 5      # 未动 → 默认
    assert t["box_scan_interval_sec"] == 30
