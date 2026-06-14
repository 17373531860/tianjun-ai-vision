"""外部 MES 工单主动拉取 (v3.20) 单元/集成测试.

mock 掉 requests, 用上银 HIWIN 的真实返回结构验证:
  成功判定 → 取数组 → 字段映射 → upsert 工单 → 幂等 → validate/dry_run/结构识别。

所有用例用 uuid 工单号隔离, 跑完清理, 避免与其他测试串污染。
"""
from __future__ import annotations

import json
import uuid

import pytest

from backend.db.database import Base, SessionLocal, engine
from backend.models.mes_models import WorkOrder
from backend.services.mes_puller import MESPuller, DEFAULT_FIELD_MAPPING

# conftest 建表时未注册 MES 模型 (走 app import 链才会注册); 本测试直连 SessionLocal,
# 显式确保 MES 表存在 (幂等, 已存在则跳过)。
Base.metadata.create_all(bind=engine)


def _fake_resp(status: int, body):
    class _R:
        status_code = status

        def json(self):
            if body is None:
                raise ValueError("no json")
            return body

        @property
        def text(self):
            return json.dumps(body, ensure_ascii=False) if body is not None else ""

    return _R()


@pytest.fixture
def patch_http(monkeypatch):
    """劫持 puller 内部的 requests.request, 用 holder['resp'] 控制返回。"""
    holder = {}

    def fake_request(method, url, **kwargs):
        holder["last_call"] = {"method": method, "url": url, "kwargs": kwargs}
        return holder["resp"]

    monkeypatch.setattr("backend.services.mes_puller.requests.request", fake_request)
    return holder


def _cfg(mode="upsert", **over):
    cfg = {
        "url": "http://fake-hiwin/api",
        "method": "POST",
        "request_body_template": '{"api":"hiwin/x/query","parameters":{"job_no":"{job_no}"}}',
        "success_path": "statusCode",
        "success_value": 200,
        "array_path": "response.resultData",
        "field_mapping": DEFAULT_FIELD_MAPPING,
        "import_mode": mode,
        "retry_count": 0,
    }
    cfg.update(over)
    return cfg


def _hiwin_body(rows):
    # 上银真实结构: 工单数组嵌在 response.resultData (两层), 顶层是 statusCode + success
    return {
        "error": None, "statusCode": 200, "success": True,
        "response": {"pageNo": 1, "numberOfPerPage": 200,
                     "resultData": rows, "i18nInfo": None},
    }


def _cleanup(db, nos):
    db.query(WorkOrder).filter(WorkOrder.order_no.in_(nos)).delete(synchronize_session=False)
    db.commit()


# ============================================================
# 核心: 创建 + 幂等
# ============================================================
def test_pull_creates_orders_and_is_idempotent(patch_http):
    no1 = f"WO-{uuid.uuid4().hex[:8]}"
    no2 = f"WO-{uuid.uuid4().hex[:8]}"
    patch_http["resp"] = _fake_resp(200, _hiwin_body([
        {"job_no": no1, "cust_name": "雷鸟", "dispatch_qty": "120", "spec": "HG20-A"},
        {"job_no": no2, "cust_name": "雷鸟", "dispatch_qty": 500, "spec": "HG20-B"},
    ]))
    p = MESPuller()
    db = SessionLocal()
    try:
        r = p._pull_with_config(db, _cfg())
        db.commit()
        assert r["success"] is True
        assert r["fetched"] == 2 and r["created"] == 2

        o1 = db.query(WorkOrder).filter(WorkOrder.order_no == no1).first()
        assert o1 is not None
        assert o1.customer_name == "雷鸟"
        assert o1.product_spec == "HG20-A"
        assert o1.planned_qty == 120          # 字符串 "120" → int
        assert o1.source == "external"
        assert o1.product_name == "HG20-A"    # 无产品名 → 规格兜底

        # 幂等: 同样的数据再拉一次, 不重复建, 工单总数不变
        r2 = p._pull_with_config(db, _cfg())
        db.commit()
        assert r2["created"] == 0 and r2["updated"] == 2
        cnt = db.query(WorkOrder).filter(WorkOrder.order_no.in_([no1, no2])).count()
        assert cnt == 2
    finally:
        _cleanup(db, [no1, no2])
        db.close()


# ============================================================
# 仅校验模式: 不落库
# ============================================================
def test_validate_mode_does_not_write(patch_http):
    no = f"WO-{uuid.uuid4().hex[:8]}"
    patch_http["resp"] = _fake_resp(200, _hiwin_body([
        {"job_no": no, "cust_name": "x", "dispatch_qty": 1, "spec": "s"},
    ]))
    p = MESPuller()
    db = SessionLocal()
    try:
        r = p._pull_with_config(db, _cfg("validate"))
        db.commit()
        assert r["validated"] == 1 and r["created"] == 0
        assert db.query(WorkOrder).filter(WorkOrder.order_no == no).first() is None
    finally:
        _cleanup(db, [no])
        db.close()


# ============================================================
# 成功判定失败: 外部返回非 200 statusCode
# ============================================================
def test_success_check_fail_carries_external_error(patch_http):
    # 上银真实错误结构: 详情嵌在 error 对象里 (error.errorInfo / error.detail_message)
    patch_http["resp"] = _fake_resp(200, {
        "statusCode": 500, "success": False, "response": None,
        "error": {"errorCode": 1, "errorInfo": "SYSTEM ERROR~~~~",
                  "detail_message": "column reference ambiguous"},
    })
    p = MESPuller()
    db = SessionLocal()
    try:
        r = p._pull_with_config(db, _cfg())
        assert r["success"] is False
        assert "SYSTEM ERROR" in (r["error"] or "")  # 嵌套 error 详情被带出来
        assert r["created"] == 0
    finally:
        db.close()


# ============================================================
# dry_run: 报告会建, 但不真写库
# ============================================================
def test_dry_run_reports_but_no_write(patch_http):
    no = f"WO-{uuid.uuid4().hex[:8]}"
    patch_http["resp"] = _fake_resp(200, _hiwin_body([
        {"job_no": no, "cust_name": "x", "dispatch_qty": 1, "spec": "s"},
    ]))
    p = MESPuller()
    db = SessionLocal()
    try:
        r = p._pull_with_config(db, _cfg(), dry_run=True)
        db.commit()
        assert r["created"] == 1            # 预览报告说会建 1 条
        assert db.query(WorkOrder).filter(WorkOrder.order_no == no).first() is None
    finally:
        _cleanup(db, [no])
        db.close()


# ============================================================
# 测试连接: 自动识别返回结构 (给前端点选映射)
# ============================================================
def test_test_connection_guesses_structure(patch_http):
    patch_http["resp"] = _fake_resp(200, _hiwin_body([
        {"job_no": "W1", "cust_name": "c", "dispatch_qty": 1, "spec": "s"},
    ]))
    p = MESPuller()
    out = p.test_connection({"url": "http://fake/api", "method": "POST"})
    assert out["success"] is True
    g = out["structure_guess"]
    # 上银两层嵌套, 自动识别要钻进 response 找到 resultData
    assert g["array_path"] == "response.resultData"
    assert set(g["fields"]) == {"job_no", "cust_name", "dispatch_qty", "spec"}


# ============================================================
# upsert_with_planned: 已存在工单时用排产量覆盖计划数量
# ============================================================
def test_upsert_with_planned_overrides_qty(patch_http):
    no = f"WO-{uuid.uuid4().hex[:8]}"
    p = MESPuller()
    db = SessionLocal()
    try:
        patch_http["resp"] = _fake_resp(200, _hiwin_body([
            {"job_no": no, "cust_name": "c", "dispatch_qty": 10, "spec": "s"},
        ]))
        p._pull_with_config(db, _cfg("upsert"))
        db.commit()

        patch_http["resp"] = _fake_resp(200, _hiwin_body([
            {"job_no": no, "cust_name": "c", "dispatch_qty": 99, "spec": "s"},
        ]))
        p._pull_with_config(db, _cfg("upsert_with_planned"))
        db.commit()

        o = db.query(WorkOrder).filter(WorkOrder.order_no == no).first()
        assert o.planned_qty == 99
    finally:
        _cleanup(db, [no])
        db.close()


# ============================================================
# 请求体模板渲染 + 占位符替换
# ============================================================
def test_request_body_template_renders_job_no(patch_http):
    patch_http["resp"] = _fake_resp(200, _hiwin_body([]))
    p = MESPuller()
    db = SessionLocal()
    try:
        p._pull_with_config(db, _cfg(), job_no="WO-XYZ")
        sent = patch_http["last_call"]["kwargs"].get("json")
        assert sent == {"api": "hiwin/x/query", "parameters": {"job_no": "WO-XYZ"}}
    finally:
        db.close()


# ============================================================
# 定时调度器: 到点拉取 + 未到点跳过
# ============================================================
def test_scheduler_tick_pulls_then_skips_within_interval(patch_http):
    from backend.models.mes_models import MESConnection
    from backend.services.mes_puller import PullScheduler

    no = f"WO-{uuid.uuid4().hex[:8]}"
    db = SessionLocal()
    conn = MESConnection(
        name="UT-Sched", adapter_type="rest", enabled=False, pull_enabled=True,
        config={"pull": {**_cfg(), "triggers": {"scheduled": True, "interval_sec": 60}}},
    )
    db.add(conn)
    db.commit()
    cid = conn.id
    try:
        patch_http["resp"] = _fake_resp(200, _hiwin_body([
            {"job_no": no, "cust_name": "c", "dispatch_qty": 5, "spec": "s"},
        ]))
        sched = PullScheduler()
        sched._tick()
        assert db.query(WorkOrder).filter(WorkOrder.order_no == no).first() is not None
        assert cid in sched._last_run

        # interval(60s) 内第二次 tick 应跳过: 删掉工单看是否重建
        db.query(WorkOrder).filter(WorkOrder.order_no == no).delete(synchronize_session=False)
        db.commit()
        sched._tick()
        assert db.query(WorkOrder).filter(WorkOrder.order_no == no).first() is None
    finally:
        db.query(WorkOrder).filter(WorkOrder.order_no == no).delete(synchronize_session=False)
        db.query(MESConnection).filter(MESConnection.id == cid).delete(synchronize_session=False)
        db.commit()
        db.close()


def test_scheduler_skips_disabled_or_unscheduled(patch_http):
    """pull_enabled=False 或 triggers.scheduled=False 的连接不被定时拉取。"""
    from backend.models.mes_models import MESConnection
    from backend.services.mes_puller import PullScheduler

    no = f"WO-{uuid.uuid4().hex[:8]}"
    db = SessionLocal()
    # pull_enabled=True 但 scheduled=False → 不该拉
    conn = MESConnection(
        name="UT-NoSched", adapter_type="rest", enabled=False, pull_enabled=True,
        config={"pull": {**_cfg(), "triggers": {"scheduled": False}}},
    )
    db.add(conn)
    db.commit()
    cid = conn.id
    try:
        patch_http["resp"] = _fake_resp(200, _hiwin_body([
            {"job_no": no, "cust_name": "c", "dispatch_qty": 5, "spec": "s"},
        ]))
        PullScheduler()._tick()
        assert db.query(WorkOrder).filter(WorkOrder.order_no == no).first() is None
    finally:
        db.query(WorkOrder).filter(WorkOrder.order_no == no).delete(synchronize_session=False)
        db.query(MESConnection).filter(MESConnection.id == cid).delete(synchronize_session=False)
        db.commit()
        db.close()
