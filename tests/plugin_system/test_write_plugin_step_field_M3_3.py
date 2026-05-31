"""M3.3: write_plugin_step_field 真实现 (从 M1.3a stub 升级)

客户视角叙事:
  M1.2b 让 step_change.warn_threshold_violated 缓存到 _plugin_step_warn_cache,
  但当时 cycle / step 表都没 plugin_data JSON 字段, 仅在内存里; 重启就丢, 也不能
  出口走自定义导出.

  M3.3 解锁 step_records.plugin_data:
    - StepRecord 加 plugin_data JSON 字段 (nullable, 默认 {})
    - main.py: migrate_database() 加 ALTER TABLE 探针 (老客户库自动补列)
    - PluginHost.write_plugin_step_field 从 stub 升真实现:
      * 需声明 runtime.step_field_write capability
      * key 必须 plugin_<customer_code>_ 前缀 (命名空间隔离)
      * value 必须可 JSON 序列化 (拒绝 lambda / 自定义类等)
      * JSON 合并写入, 不删其它 key
      * 写完不通知主程序; 主程序导出/CSV 默认不暴露此字段
    - manifest schema capabilities 词汇表加 runtime.step_field_write 枚举

  本测试覆盖:
    - capability 拒绝 (1)
    - 命名空间拒绝 (3)
    - JSON 不可序列化拒绝 (3)
    - step_record 不存在拒绝 (1)
    - 写入成功 + JSON 合并语义 (4)
    - audit log (3)
    - 错误隔离 (2)
    - 签名 + capability 词汇表锁定 (2)

19 个测试. 测试隔离: 用 in-memory SQLite + Base.metadata.create_all 起独立 DB.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


# ============================================================
# 基础设施: 隔离 DB + host fixtures (与 test_active_apis.py 同款)
# ============================================================


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """每个测试独立 SQLite (含 plugin_audit_log + step_records + StepRecord 表)."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # 用 file-based SQLite 让 multi-session 看到同一份数据
    db_path = tmp_path / "test_m33.db"
    engine = create_engine(f"sqlite:///{db_path}")

    # 建所有表 (含 StepRecord / DetectionCycle / DetectionSession / PluginAuditLog)
    from backend.db.database import Base
    from backend.models import models, plugin_models, mes_models, export_models  # noqa: F401
    Base.metadata.create_all(bind=engine)

    TestSessionLocal = sessionmaker(bind=engine)

    # 把 SessionLocal patch 到隔离 DB
    from backend.db import database as db_mod
    monkeypatch.setattr(db_mod, "SessionLocal", TestSessionLocal)
    return TestSessionLocal


@pytest.fixture
def host_with_cap(isolated_db):
    """声明 runtime.step_field_write capability 的 host."""
    from backend.plugin_system.registry import PluginHost
    return PluginHost(
        customer_code="acme",
        plugin_dir="/tmp",
        main_version="3.13.0",
        capabilities=["runtime.step_field_write"],
    )


@pytest.fixture
def host_no_cap(isolated_db):
    """未声明 capability 的 host."""
    from backend.plugin_system.registry import PluginHost
    return PluginHost(
        customer_code="acme",
        plugin_dir="/tmp",
        main_version="3.13.0",
        capabilities=[],
    )


def _create_step_record(SessionLocal, step_id: int = 1, plugin_data=None):
    """造一个 StepRecord 行供测试用.

    SQLite 默认不强制 FK (PRAGMA foreign_keys = OFF), 所以 cycle_id 留 None
    即可, 不需要先建上游 DetectionSession / DetectionCycle.
    """
    import uuid
    from datetime import datetime, timezone
    from backend.models.models import StepRecord

    db = SessionLocal()
    try:
        rec = StepRecord(
            id=step_id,
            record_uuid=str(uuid.uuid4()),
            cycle_id=None,  # FK 不强制
            step_label="step_1",
            start_time=datetime.now(timezone.utc),
            plugin_data=plugin_data,
        )
        db.add(rec)
        db.commit()
        return rec.id
    finally:
        db.close()


def _read_plugin_data(SessionLocal, step_id: int):
    from backend.models.models import StepRecord
    db = SessionLocal()
    try:
        row = db.query(StepRecord).filter(StepRecord.id == step_id).first()
        return row.plugin_data if row else None
    finally:
        db.close()


def _audit_rows(SessionLocal):
    from backend.models.plugin_models import PluginAuditLog
    db = SessionLocal()
    try:
        return db.query(PluginAuditLog).order_by(PluginAuditLog.id).all()
    finally:
        db.close()


# ============================================================
# A. capability 拒绝
# ============================================================


def test_rejects_when_capability_not_declared(host_no_cap, isolated_db):
    """未声明 runtime.step_field_write → PluginRuntimeError + audit rejected."""
    from backend.plugin_system.registry import PluginRuntimeError
    sid = _create_step_record(isolated_db, step_id=1)

    with pytest.raises(PluginRuntimeError, match="runtime.step_field_write"):
        host_no_cap.write_plugin_step_field(sid, "plugin_acme_x", 1)

    # 拒绝路径不写入 plugin_data
    assert _read_plugin_data(isolated_db, sid) is None


# ============================================================
# B. 命名空间拒绝
# ============================================================


def test_rejects_key_without_plugin_prefix(host_with_cap, isolated_db):
    """key 不带 plugin_ 前缀 → PluginRuntimeError."""
    from backend.plugin_system.registry import PluginRuntimeError
    sid = _create_step_record(isolated_db, step_id=2)

    with pytest.raises(PluginRuntimeError, match="命名空间"):
        host_with_cap.write_plugin_step_field(sid, "step_warn", 1)


def test_rejects_other_customer_namespace(host_with_cap, isolated_db):
    """key 是别家客户的命名空间 → PluginRuntimeError."""
    from backend.plugin_system.registry import PluginRuntimeError
    sid = _create_step_record(isolated_db, step_id=3)

    with pytest.raises(PluginRuntimeError, match="命名空间"):
        host_with_cap.write_plugin_step_field(sid, "plugin_other_x", 1)


def test_rejects_main_program_reserved_key(host_with_cap, isolated_db):
    """key 是主程序保留 key (无 plugin_ 前缀) → PluginRuntimeError."""
    from backend.plugin_system.registry import PluginRuntimeError
    sid = _create_step_record(isolated_db, step_id=4)

    with pytest.raises(PluginRuntimeError, match="命名空间"):
        host_with_cap.write_plugin_step_field(sid, "screenshot_path", "/etc/passwd")


# ============================================================
# C. JSON 不可序列化拒绝 — 返 False + audit rejected
# ============================================================


def test_rejects_non_json_value_lambda(host_with_cap, isolated_db):
    """value 是 lambda → 不可 JSON 序列化 → 返 False + audit rejected, 不抛."""
    sid = _create_step_record(isolated_db, step_id=10)

    ret = host_with_cap.write_plugin_step_field(sid, "plugin_acme_x", lambda: 1)
    assert ret is False

    # 数据未写入
    assert _read_plugin_data(isolated_db, sid) is None

    rows = _audit_rows(isolated_db)
    assert len(rows) == 1
    assert rows[0].action == "write_plugin_step_field"
    assert rows[0].status == "rejected"
    assert "不可 JSON" in rows[0].message


def test_rejects_non_json_value_custom_class(host_with_cap, isolated_db):
    """value 是自定义类实例 → 返 False."""
    class _Custom:
        pass
    sid = _create_step_record(isolated_db, step_id=11)

    ret = host_with_cap.write_plugin_step_field(sid, "plugin_acme_x", _Custom())
    assert ret is False
    assert _read_plugin_data(isolated_db, sid) is None


def test_accepts_json_primitives_and_containers(host_with_cap, isolated_db):
    """主流 JSON 类型都接受: bool / int / float / str / None / list / dict."""
    sid = _create_step_record(isolated_db, step_id=12)

    for i, value in enumerate([
        True, 1, 1.5, "str", None,
        [1, "a", True], {"nested": [1, 2]},
    ]):
        ret = host_with_cap.write_plugin_step_field(sid, f"plugin_acme_v{i}", value)
        assert ret is True, f"应接受类型 {type(value).__name__}, 值={value!r}"

    data = _read_plugin_data(isolated_db, sid)
    assert data["plugin_acme_v0"] is True
    assert data["plugin_acme_v3"] == "str"
    assert data["plugin_acme_v4"] is None
    assert data["plugin_acme_v6"] == {"nested": [1, 2]}


# ============================================================
# D. step_record 不存在拒绝
# ============================================================


def test_rejects_nonexistent_step_record_id(host_with_cap, isolated_db):
    """step_record_id 不存在 → 返 False + audit rejected."""
    ret = host_with_cap.write_plugin_step_field(99999, "plugin_acme_x", 1)
    assert ret is False

    rows = _audit_rows(isolated_db)
    assert len(rows) == 1
    assert rows[0].status == "rejected"
    assert "不存在" in rows[0].message


# ============================================================
# E. 写入成功 + JSON 合并语义
# ============================================================


def test_writes_new_plugin_data_when_none(host_with_cap, isolated_db):
    """初始 plugin_data=None → 写入后 = {key: value}."""
    sid = _create_step_record(isolated_db, step_id=20, plugin_data=None)

    ret = host_with_cap.write_plugin_step_field(sid, "plugin_acme_warn", "yellow")
    assert ret is True

    data = _read_plugin_data(isolated_db, sid)
    assert data == {"plugin_acme_warn": "yellow"}


def test_merges_with_existing_plugin_data(host_with_cap, isolated_db):
    """已有 plugin_data, 新写入合并不删旧 key."""
    sid = _create_step_record(
        isolated_db, step_id=21,
        plugin_data={"plugin_acme_other": "kept", "plugin_other_pre": "kept2"},
    )

    ret = host_with_cap.write_plugin_step_field(sid, "plugin_acme_new", "added")
    assert ret is True

    data = _read_plugin_data(isolated_db, sid)
    assert data == {
        "plugin_acme_other": "kept",
        "plugin_other_pre": "kept2",  # 其它客户的 key 也不删
        "plugin_acme_new": "added",
    }


def test_overwrites_same_key(host_with_cap, isolated_db):
    """同 key 多次写, 后写覆盖前写."""
    sid = _create_step_record(isolated_db, step_id=22, plugin_data={})

    host_with_cap.write_plugin_step_field(sid, "plugin_acme_v", 1)
    host_with_cap.write_plugin_step_field(sid, "plugin_acme_v", 2)
    host_with_cap.write_plugin_step_field(sid, "plugin_acme_v", 3)

    data = _read_plugin_data(isolated_db, sid)
    assert data == {"plugin_acme_v": 3}


def test_multiple_keys_accumulate(host_with_cap, isolated_db):
    """多次写不同 key, 累加."""
    sid = _create_step_record(isolated_db, step_id=23, plugin_data={})

    host_with_cap.write_plugin_step_field(sid, "plugin_acme_a", 1)
    host_with_cap.write_plugin_step_field(sid, "plugin_acme_b", 2)
    host_with_cap.write_plugin_step_field(sid, "plugin_acme_c", 3)

    data = _read_plugin_data(isolated_db, sid)
    assert data == {"plugin_acme_a": 1, "plugin_acme_b": 2, "plugin_acme_c": 3}


# ============================================================
# F. audit log
# ============================================================


def test_audit_log_on_success(host_with_cap, isolated_db):
    """成功写入 → audit 一条 success."""
    sid = _create_step_record(isolated_db, step_id=30, plugin_data={})
    host_with_cap.write_plugin_step_field(sid, "plugin_acme_v", "x")

    rows = _audit_rows(isolated_db)
    assert len(rows) == 1
    assert rows[0].action == "write_plugin_step_field"
    assert rows[0].status == "success"
    assert rows[0].customer_code == "acme"


def test_audit_log_on_capability_rejection(host_no_cap, isolated_db):
    """capability 拒绝 → audit 一条 rejected (action=capability_check.*)."""
    from backend.plugin_system.registry import PluginRuntimeError
    sid = _create_step_record(isolated_db, step_id=31, plugin_data={})

    with pytest.raises(PluginRuntimeError):
        host_no_cap.write_plugin_step_field(sid, "plugin_acme_v", 1)

    rows = _audit_rows(isolated_db)
    assert len(rows) == 1
    assert "capability_check.runtime.step_field_write" == rows[0].action
    assert rows[0].status == "rejected"


def test_audit_log_on_namespace_rejection(host_with_cap, isolated_db):
    """命名空间拒绝 → audit 一条 namespace_check rejected."""
    from backend.plugin_system.registry import PluginRuntimeError
    sid = _create_step_record(isolated_db, step_id=32, plugin_data={})

    with pytest.raises(PluginRuntimeError):
        host_with_cap.write_plugin_step_field(sid, "bad_key", 1)

    rows = _audit_rows(isolated_db)
    assert len(rows) == 1
    assert rows[0].action == "namespace_check"
    assert rows[0].status == "rejected"


# ============================================================
# G. 错误隔离 — DB 异常不上抛
# ============================================================


def test_db_session_open_failure_swallowed(host_with_cap, isolated_db, monkeypatch):
    """SessionLocal 实例化抛错 → swallow, 返 False."""
    sid = _create_step_record(isolated_db, step_id=41, plugin_data={})

    def boom_session():
        raise RuntimeError("DB 挂了")

    from backend.db import database as db_mod
    monkeypatch.setattr(db_mod, "SessionLocal", boom_session)

    ret = host_with_cap.write_plugin_step_field(sid, "plugin_acme_v", 1)
    assert ret is False
    # 注: audit 也会失败 (因为 audit 也走 SessionLocal), 但被 _audit_log 内 swallow.
    # 这里只验证返 False 不上抛.


# ============================================================
# H. 签名 + capability 词汇表锁定
# ============================================================


def test_signature_locked():
    """``write_plugin_step_field`` 签名锁定 (改签名 = 升 plugin SDK)."""
    import inspect
    from backend.plugin_system.registry import PluginHost
    sig = inspect.signature(PluginHost.write_plugin_step_field)
    params = list(sig.parameters.keys())
    assert params == ["self", "step_record_id", "key", "value"]


def test_capability_keyword_locked():
    """需要的 capability 字符串 = ``runtime.step_field_write``.

    改这个字符串 = 升 plugin SDK + 同步 RFC 09 + manifest schema §3.4 表.
    """
    # 静态扫描确认 capability check 字符串
    from backend.plugin_system import registry as reg_mod
    import inspect
    src = inspect.getsource(reg_mod.PluginHost.write_plugin_step_field)
    assert '_require_capability("runtime.step_field_write")' in src, (
        "write_plugin_step_field 没用 'runtime.step_field_write' capability — "
        "改 capability 字符串 = 升 plugin SDK"
    )
