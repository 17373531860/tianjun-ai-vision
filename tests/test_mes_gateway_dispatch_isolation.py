# -*- coding: utf-8 -*-
"""v3.38 网关逐连接提交 · 真库集成回归 (川南"框冻结"修复的风险矩阵钉死)

与 test_mes_gateway_lock_release.py (假会话钉时序不变量) 互补, 本文件用
真实 SQLite 文件库跑完整 dispatch, 覆盖修复引入的三类新风险:

  R1 逐连接提交使 ORM 对象过期 → 循环中途连接行被并发删除时,
     一条连接的异常不得拖垮其余连接 (错误隔离)
  R2 "前面死、后面活"混合连接: 提交后过期对象重查必须走通,
     活连接照常推送 + 通信日志/last_sync_at 落库
  R3 部分提交语义: 中途有连接炸掉, 前面已推连接的通信日志不得丢
  R0 (核心不变量活体版) 网络发送瞬间, 独立连接以 300ms busy_timeout
     写库必须成功 = 网关没握写锁
"""
import sqlite3

import pytest

from backend.services.mes_gateway import MESGateway
from backend.models.mes_models import MESConnection, MESCommLog


# ==================== 真库 fixture ====================
@pytest.fixture
def db_env(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from backend.db.database import Base
    from backend.models import models as _m  # noqa: F401
    from backend.models import auth_models as _a  # noqa: F401
    from backend.models import mes_models as _mes  # noqa: F401
    from backend.models import export_models as _ex  # noqa: F401
    from backend.models import plugin_models as _p  # noqa: F401

    db_file = str(tmp_path / "gw_test.db")
    engine = create_engine(
        f"sqlite:///{db_file}",
        connect_args={"check_same_thread": False, "timeout": 15},
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr("backend.services.mes_gateway.SessionLocal", factory)
    # 报警台账路径默认关 (入站配置为空), 但为隔离外部依赖直接短路
    monkeypatch.setattr(MESGateway, "_alarm_dedup_should_skip",
                        lambda self, db, et, ctx: False)
    monkeypatch.setattr(MESGateway, "_record_active_alarm_if_alarm",
                        lambda self, db, et, ctx, channel_id=None: None)
    yield {"file": db_file, "factory": factory}
    engine.dispose()


def _mk_conn(session, name, url="http://127.0.0.1:9/dead"):
    c = MESConnection(
        name=name, adapter_type="rest", enabled=True,
        config={"url": url, "timeout": 1},
        push_events=["cycle_end"], retry_count=0, retry_interval_sec=0,
    )
    session.add(c)
    session.commit()
    return c.id


class _ScriptedAdapter:
    """按 url 决定行为: dead=失败字典 / ok=成功 / boom=抛异常; 可挂 send 侧钩子。"""

    def __init__(self, on_send=None):
        self.on_send = on_send
        self.sent_urls = []

    def build_payload(self, full_context, config):
        return full_context

    def send(self, payload, config):
        url = config.get("url", "")
        self.sent_urls.append(url)
        if self.on_send:
            self.on_send(url)
        if "boom" in url:
            raise RuntimeError("适配器爆炸(模拟未捕获异常)")
        if "dead" in url:
            return {"status_code": 0, "body": None, "duration_ms": 1000,
                    "error": "连接失败: 模拟死端点"}
        return {"status_code": 200, "body": {"ok": True}, "duration_ms": 5}

    def check_response(self, result, config):
        return (result or {}).get("status_code") == 200


def _logs(factory):
    s = factory()
    try:
        return s.query(MESCommLog).order_by(MESCommLog.id).all()
    finally:
        s.close()


# ==================== R0: 网络发送瞬间不持写锁 (真库活体版) ====================
def test_no_write_lock_during_network_send_real_db(db_env, monkeypatch):
    probe_results = []

    def probe_write(url):
        # 模拟推理线程: 独立连接短超时写库, 网关若握写锁这里必然 database is locked
        con = sqlite3.connect(db_env["file"], timeout=0.3)
        try:
            con.execute("PRAGMA busy_timeout=300")
            con.execute(
                "CREATE TABLE IF NOT EXISTS _probe (id INTEGER PRIMARY KEY, v TEXT)")
            con.execute("INSERT INTO _probe (v) VALUES (?)", (url,))
            con.commit()
            probe_results.append("ok")
        except sqlite3.OperationalError as e:
            probe_results.append(f"locked: {e}")
        finally:
            con.close()

    s = db_env["factory"]()
    _mk_conn(s, "死端点1", "http://x/dead")
    _mk_conn(s, "死端点2", "http://x/dead")
    s.close()

    adapter = _ScriptedAdapter(on_send=probe_write)
    monkeypatch.setattr("backend.services.mes_gateway.get_adapter", lambda t: adapter)

    MESGateway().dispatch("cycle_end", {"cycle": {"result": "OK"}}, channel_id=0)

    assert len(adapter.sent_urls) == 2
    # 第 2 次发送发生在第 1 条失败日志落库之后 — 修复前这里必然 locked
    assert probe_results == ["ok", "ok"], f"网络发送期间写锁被持有: {probe_results}"
    assert len(_logs(db_env["factory"])) == 2


# ==================== R2: 死活混合, 过期对象重查走通 ====================
def test_dead_then_alive_mixed_connections(db_env, monkeypatch):
    s = db_env["factory"]()
    _mk_conn(s, "先死", "http://x/dead")
    alive_id = _mk_conn(s, "后活", "http://x/ok")
    s.close()

    adapter = _ScriptedAdapter()
    monkeypatch.setattr("backend.services.mes_gateway.get_adapter", lambda t: adapter)

    MESGateway().dispatch("cycle_end", {"cycle": {"result": "OK"}}, channel_id=0)

    logs = _logs(db_env["factory"])
    assert [l.success for l in logs] == [False, True], \
        f"预期 先死后活: {[(l.connection_id, l.success) for l in logs]}"
    # 活连接的同步时间戳照常更新 (提交后过期对象的写回路径)
    s = db_env["factory"]()
    alive = s.query(MESConnection).get(alive_id)
    assert alive.last_sync_at is not None
    s.close()


# ==================== R1: 循环中途连接被并发删除 → 隔离, 其余照推 ====================
def test_concurrent_delete_mid_dispatch_isolated(db_env, monkeypatch):
    s = db_env["factory"]()
    _mk_conn(s, "第一条", "http://x/dead")
    victim_id = _mk_conn(s, "会被删掉", "http://x/ok")
    _mk_conn(s, "第三条", "http://x/ok")
    s.close()

    def delete_victim(url):
        if "dead" in url:  # 在第一条连接推送期间, 别处把第二条删了
            con = sqlite3.connect(db_env["file"], timeout=5)
            con.execute("DELETE FROM mes_connections WHERE id=?", (victim_id,))
            con.commit()
            con.close()

    adapter = _ScriptedAdapter(on_send=delete_victim)
    monkeypatch.setattr("backend.services.mes_gateway.get_adapter", lambda t: adapter)

    # 关键断言 1: dispatch 全程不向外抛异常
    MESGateway().dispatch("cycle_end", {"cycle": {"result": "OK"}}, channel_id=0)

    # 关键断言 2: 第三条连接不被第二条的尸体拖垮, 照常推送成功
    logs = _logs(db_env["factory"])
    ok_urls = [l for l in logs if l.success]
    assert len(ok_urls) >= 1, f"第三条连接未推送: {[(l.connection_id, l.success) for l in logs]}"


# ==================== R3: 中途爆炸, 已推连接的日志不丢 (部分提交语义) ====================
def test_earlier_logs_survive_later_explosion(db_env, monkeypatch):
    s = db_env["factory"]()
    _mk_conn(s, "正常推", "http://x/ok")
    _mk_conn(s, "会爆炸", "http://x/boom")
    s.close()

    adapter = _ScriptedAdapter()
    monkeypatch.setattr("backend.services.mes_gateway.get_adapter", lambda t: adapter)

    MESGateway().dispatch("cycle_end", {"cycle": {"result": "OK"}}, channel_id=0)

    logs = _logs(db_env["factory"])
    assert any(l.success for l in logs), "爆炸前已成功连接的通信日志被回滚丢失"


# ==================== v3.49: 单事件重试总耗时预算 ====================
def test_retry_budget_clamps_total_time(db_env, monkeypatch):
    """retry_budget_sec 生效: 预算耗尽提前收场, 不再把重试拖成 次数×间隔。"""
    import time as _time

    s = db_env["factory"]()
    c = MESConnection(
        name="慢死端点", adapter_type="rest", enabled=True,
        config={"url": "http://x/dead", "timeout": 1, "retry_budget_sec": 1},
        push_events=["cycle_end"], retry_count=10, retry_interval_sec=1,
    )
    s.add(c)
    s.commit()
    s.close()

    adapter = _ScriptedAdapter()
    monkeypatch.setattr("backend.services.mes_gateway.get_adapter", lambda t: adapter)

    t0 = _time.monotonic()
    MESGateway().dispatch("cycle_end", {"cycle": {"result": "OK"}}, channel_id=0)
    elapsed = _time.monotonic() - t0

    # 无预算时是 10 次重试 × 1s 间隔 ≥ 10s; 预算 1s → 首发后最多再等 <1s
    assert elapsed < 5, f"重试预算未生效, 耗时 {elapsed:.1f}s"
    assert len(adapter.sent_urls) <= 2, \
        f"预算 1s 内不应发出 {len(adapter.sent_urls)} 次请求"

    logs = _logs(db_env["factory"])
    assert len(logs) == 1 and logs[0].success is False
    assert "重试预算耗尽" in (logs[0].error_msg or ""), \
        f"失败日志应标注预算耗尽: {logs[0].error_msg}"


def test_retry_budget_zero_keeps_legacy_behavior(db_env, monkeypatch):
    """retry_budget_sec 缺省/0 → 历史行为不变 (按 retry_count 重试到耗尽)。"""
    s = db_env["factory"]()
    c = MESConnection(
        name="无预算死端点", adapter_type="rest", enabled=True,
        config={"url": "http://x/dead", "timeout": 1},
        push_events=["cycle_end"], retry_count=2, retry_interval_sec=0,
    )
    s.add(c)
    s.commit()
    s.close()

    adapter = _ScriptedAdapter()
    monkeypatch.setattr("backend.services.mes_gateway.get_adapter", lambda t: adapter)

    MESGateway().dispatch("cycle_end", {"cycle": {"result": "OK"}}, channel_id=0)

    assert len(adapter.sent_urls) == 3, \
        f"预算=0 应保持 1+retry_count 次请求, 实际 {len(adapter.sent_urls)}"
    logs = _logs(db_env["factory"])
    assert len(logs) == 1 and logs[0].success is False
    assert "重试预算耗尽" not in (logs[0].error_msg or "")


# ==================== 过滤路径: 不该推的事件零写入 ====================
def test_event_filter_no_writes(db_env, monkeypatch):
    s = db_env["factory"]()
    _mk_conn(s, "只订阅cycle_end", "http://x/ok")
    s.close()

    adapter = _ScriptedAdapter()
    monkeypatch.setattr("backend.services.mes_gateway.get_adapter", lambda t: adapter)

    MESGateway().dispatch("session_end", {"session": {}}, channel_id=0)

    assert adapter.sent_urls == []
    assert _logs(db_env["factory"]) == []
