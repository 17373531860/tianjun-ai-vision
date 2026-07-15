# -*- coding: utf-8 -*-
"""v3.38 川南"框冻结"根因回归: 网关推送在网络 I/O 期间不得持有数据库写锁。

事故机理 (2026-07 川南现场):
  dispatch 单会话循环推多条连接, 前一条失败落通信日志 (_log 内 flush) 即握住
  SQLite 写锁; 下一条连接对不在线端点的 HTTP 等待 (默认 30s) 期间锁不释放,
  推理线程写步骤记录被堵到 busy_timeout 边缘 (8~15s) → 前端检测框冻结。

本测试用记录型假会话钉死不变量: 每次进入 adapter.send (网络 I/O) 时,
会话不得存在"已 flush 未 commit"的悬挂写入。
"""
from backend.services.mes_gateway import MESGateway


# ==================== 测试替身 ====================
class _LedgerSession:
    """记录 flush/commit 时序, 模拟'flush 即取写锁, commit 即放锁'。"""

    def __init__(self):
        self.uncommitted_flushes = 0
        self.commits = 0
        self._conns = []

    def set_connections(self, conns):
        self._conns = conns

    # --- SQLAlchemy Session 接口子集 ---
    def add(self, *a, **k):
        pass

    def flush(self, *a, **k):
        self.uncommitted_flushes += 1

    def commit(self):
        self.uncommitted_flushes = 0
        self.commits += 1

    def rollback(self):
        self.uncommitted_flushes = 0

    def close(self):
        pass

    def query(self, model):
        return _FakeQuery(self._conns)


class _FakeQuery:
    def __init__(self, items):
        self._items = items

    def filter(self, *a, **k):
        return self

    def all(self):
        return self._items


class _FakeConn:
    def __init__(self, cid, name):
        self.id = cid
        self.name = name
        self.adapter_type = "rest"
        self.enabled = True
        self.config = {"url": "http://127.0.0.1:9/dead", "timeout": 1}
        self.push_events = ["cycle_end"]
        self.bound_channels = None
        self.retry_count = 0
        self.retry_interval_sec = 0
        self.last_sync_at = None


class _DeadEndpointAdapter:
    """send 即模拟网络 I/O 起点: 断言此刻会话无悬挂写入, 然后模拟端点不通。"""

    def __init__(self, session):
        self._session = session
        self.send_calls = 0
        self.lock_held_at_send = []

    def build_payload(self, full_context, config):
        return full_context

    def send(self, payload, config):
        # 真实 RESTAdapter 网络失败不抛异常, 返回 error 字典 (与线上行为一致)
        self.send_calls += 1
        self.lock_held_at_send.append(self._session.uncommitted_flushes > 0)
        return {"status_code": 0, "body": None, "duration_ms": 1000,
                "error": "连接失败: 模拟死端点"}

    def check_response(self, result, config):
        return False


# ==================== 用例 ====================
def test_dispatch_releases_lock_before_each_network_send(monkeypatch):
    session = _LedgerSession()
    conns = [_FakeConn(1, "报警上报"), _FakeConn(2, "任务完成")]
    session.set_connections(conns)
    adapter = _DeadEndpointAdapter(session)

    monkeypatch.setattr("backend.services.mes_gateway.SessionLocal", lambda: session)
    monkeypatch.setattr("backend.services.mes_gateway.get_adapter", lambda t: adapter)
    monkeypatch.setattr(MESGateway, "_alarm_dedup_should_skip",
                        lambda self, db, et, ctx: False)
    monkeypatch.setattr(MESGateway, "_record_active_alarm_if_alarm",
                        lambda self, db, et, ctx, channel_id=None: None)

    MESGateway().dispatch("cycle_end", {"cycle": {"result": "OK"}}, channel_id=0)

    assert adapter.send_calls == 2, "两条连接都应尝试推送"
    # 核心不变量: 任何一次进入网络发送时, 会话都不得有已 flush 未 commit 的写入
    # (修复前: 第 1 条失败日志 flush 后未提交, 第 2 条 send 时锁被握住 → [False, True])
    assert adapter.lock_held_at_send == [False, False], (
        f"进入网络 I/O 时持有写锁: {adapter.lock_held_at_send}")


def test_dispatch_commits_per_connection(monkeypatch):
    """每条连接推完(含失败落日志)立即提交, 不留到循环结束。"""
    session = _LedgerSession()
    session.set_connections([_FakeConn(1, "唯一连接")])
    adapter = _DeadEndpointAdapter(session)

    monkeypatch.setattr("backend.services.mes_gateway.SessionLocal", lambda: session)
    monkeypatch.setattr("backend.services.mes_gateway.get_adapter", lambda t: adapter)
    monkeypatch.setattr(MESGateway, "_alarm_dedup_should_skip",
                        lambda self, db, et, ctx: False)
    monkeypatch.setattr(MESGateway, "_record_active_alarm_if_alarm",
                        lambda self, db, et, ctx, channel_id=None: None)

    MESGateway().dispatch("cycle_end", {"cycle": {"result": "NG"}}, channel_id=0)

    assert session.commits >= 1, "失败日志必须被立即提交落库"
    assert session.uncommitted_flushes == 0, "dispatch 结束不得留悬挂写入"
