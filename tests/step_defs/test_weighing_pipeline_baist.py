"""萍乡百斯特两阶段流水线称重 (drive_mode=pipeline) 全功能 BDD。

服务层直驱 + 受控虚拟时钟 (确定性, 不 sleep):
- 状态机相位/去皮/离秤冻结/待收尾队列/超时兜底 → 直接驱动引擎创建的 PipelineStation,
  产出的事件同步转交引擎执行层 (_execute) → 记录真落库 / 网关真推送。
- 达梦对接 → 用 SQLite 文件当"虚拟达梦中间表" (DatabaseAdapter 的 dm/sqlite 路径只差
  驱动分支, INSERT 组装/事务/模板渲染完全共享), 走 网关连接 CRUD API → dispatch →
  数据库直写适配器 → 表里真出行 的完整链路。
- API 面场景走 TestClient (/weighing/feed /weighing/state /weighing/records)。
"""
from __future__ import annotations

import sqlite3
import time
import uuid

import pytest
from pytest_bdd import scenarios, given, when, then, parsers

scenarios("../features/weighing_pipeline_baist.feature")


# ============================================================
# 配置 / 工具
# ============================================================
_CH_SEQ = iter(range(900, 999))   # 每个 scenario 独立通道号, 避免串扰

_BASE_CFG = {
    "drive_mode": "pipeline",
    "materials": ["钢帽水泥"],
    "models": {
        "型号A": {"钢帽水泥": {"standard": 3.000, "low_tol": 0.100, "high_tol": 0.100}},
    },
    "require_operator": False,
    "require_model": True,
    "pipeline": {
        "material": "钢帽水泥",
        "label_onscale": "工件上秤",
        "label_finalize": "加钢脚水泥",
        "tare_min_kg": 0.5,
        "tare_max_kg": 5.0,
        "queue_depth": 2,
    },
    "timing": {
        "tare_trigger_source": "weight_first",
        "tare_delay_ms": 0,
        "tare_stable_ms": 400,
        "tare_stable_tol_kg": 0.005,
        "net_stable_ms": 400,
        "net_stable_tol_kg": 0.005,
        "shortage_alarm_sec": 0.5,
        "depart_confirm_ms": 300,
        "zero_delay_ms": 0,
        "zero_verify_ms": 500,
        "zero_retry": 1,
        "label1_min_frames": 2,
        "label1_fresh_sec": 3.0,
        "label3_min_frames": 2,
        "label3_cooldown_sec": 1.0,
        "fill_timeout_sec": 300,
        "finalize_timeout_sec": 5.0,
    },
}


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _register(ctx, **overrides):
    """登记流水线称重通道 (走引擎真实注册路径, 含视觉守卫装配)。"""
    from backend.services.weighing_engine import get_weighing_engine
    eng = get_weighing_engine()
    ch = ctx.get("ch")
    if ch is None:
        ch = next(_CH_SEQ)
    cfg_in = _deep_merge(_BASE_CFG, overrides)
    eng.set_channel_config(ch, cfg_in)
    ctx.update({
        "eng": eng,
        "ch": ch,
        "st": eng._stations[ch],
        "cfg": eng._configs[ch],
        "now": 1_000_000.0,
        "events": ctx.get("events", []),
    })


def _feed(ctx, weight, dur=0.6, hz=10):
    """按虚拟时钟喂 dur 秒同一读数, 事件转交引擎执行层 (落库/推送/报警真跑)。"""
    st, cfg, eng, ch = ctx["st"], ctx["cfg"], ctx["eng"], ctx["ch"]
    n = max(2, int(dur * hz))
    for _ in range(n):
        ctx["now"] += 1.0 / hz
        evs = st.on_weight(float(weight), ctx["now"], cfg)
        if evs:
            ctx["events"].extend(evs)
            eng._execute(ch, evs)


def _feed_labels(ctx, hit_onscale=False, hit_finalize=False, frames=3, gap=0.1):
    st, cfg, eng, ch = ctx["st"], ctx["cfg"], ctx["eng"], ctx["ch"]
    for _ in range(frames):
        ctx["now"] += gap
        evs = st.feed_labels(hit_onscale, hit_finalize, ctx["now"], cfg)
        if evs:
            ctx["events"].extend(evs)
            eng._execute(ch, evs)


def _actions(ctx):
    return [e.get("action") for e in ctx["events"]]


def _alarm_kinds(ctx):
    return [e.get("kind") for e in ctx["events"] if e.get("action") == "alarm"]


def _do_full_piece(ctx, tare, net):
    """一件完整流程: 上秤去皮 → 装料稳定 → 离秤确认结算 (含结算后清零指令帧)。"""
    _feed(ctx, tare, dur=0.6)                 # 上秤, 皮重稳定 → T
    assert ctx["st"].phase == "filling", f"未进入装料相位: {ctx['st'].phase}"
    _feed(ctx, net, dur=0.7)                  # 装料稳定 (净重就绪)
    _feed(ctx, -tare, dur=0.8)                # 拿离 → departing → 结算 + 调度 Z
    _feed(ctx, 0.0, dur=0.3)                  # 秤已清零 → 归零确认
    assert ctx["st"].phase == "empty"


@pytest.fixture(autouse=True)
def _weighing_pipeline_cleanup(ctx, client):
    """scenario 结束: 注销通道 + 删掉虚拟达梦网关连接, 不给其他测试留副作用。"""
    yield
    try:
        from backend.services.weighing_engine import get_weighing_engine
        if ctx.get("ch") is not None:
            get_weighing_engine().set_channel_config(ctx["ch"], None)
    except Exception:
        pass
    if ctx.get("dm_conn_id"):
        client.delete(f"/api/v1/mes/gateway/connections/{ctx['dm_conn_id']}")


# ============================================================
# 背景
# ============================================================
@given("通道已登记流水线称重模式")
def given_channel_registered(ctx):
    _register(ctx)


@given(parsers.parse('已选择产品型号 "{model}"'))
def given_model_selected(ctx, model):
    ctx["st"].set_context(model_name=model)


# ============================================================
# Given: 组合前置
# ============================================================
@given("工件已上秤去皮完成")
def given_tared(ctx):
    _feed(ctx, 1.2, dur=0.6)
    assert ctx["st"].phase == "filling"
    assert "send_tare" in _actions(ctx)


@given(parsers.parse("一件净重 {net:g} 公斤的工件已离秤结算进入待收尾队列"))
def given_settled_piece(ctx, net):
    _do_full_piece(ctx, tare=1.2, net=net)
    assert len(ctx["st"].pending) >= 1


@given(parsers.parse("一件净重 {net:g} 公斤的工件已离秤结算进入待收尾队列且清零延迟很长"))
def given_settled_piece_slow_zero(ctx, net):
    _register(ctx, timing={"zero_delay_ms": 60_000})
    ctx["st"].set_context(model_name="型号A")
    _feed(ctx, 1.2, dur=0.6)
    _feed(ctx, net, dur=0.7)
    _feed(ctx, -1.2, dur=0.8)                 # 结算; Z 被排到 60s 后, 尚未发出
    assert ctx["st"].phase == "empty"
    assert len(ctx["st"].pending) == 1
    assert "send_zero" not in _actions(ctx)


@given("视觉守卫配置了加水泥动作仅允许在钢帽料盆区域")
def given_guard_restrict(ctx):
    _register(ctx, visual_guard={
        "enabled": True,
        "rules": [{
            "name": "钢帽料盆",
            "labels": ["加水泥"],
            "polygon": [[0.0, 0.0], [0.4, 0.0], [0.4, 0.4], [0.0, 0.4]],
            "mode": "restrict",
            "min_frames": 2,
        }],
    })
    ctx["st"].set_context(model_name="型号A")


@given("视觉守卫配置了大盆区域舀料映射钢帽水泥料别")
def given_guard_map(ctx):
    _register(ctx, visual_guard={
        "enabled": True,
        "rules": [{
            "name": "大盆",
            "labels": ["舀料"],
            "polygon": [[0.0, 0.0], [0.4, 0.0], [0.4, 0.4], [0.0, 0.4]],
            "material": "钢帽水泥",
            "mode": "map",
            "min_frames": 2,
        }],
    })
    ctx["st"].set_context(model_name="型号A")


@given("已配置数据库直写连接指向虚拟达梦中间表")
def given_dm_connection(ctx, client, tmp_path):
    db_file = str(tmp_path / "dm_virtual.db")
    conn = sqlite3.connect(db_file)
    conn.execute(
        "CREATE TABLE T_BAIST_WEIGH ("
        "SN TEXT, MODEL_NAME TEXT, OPERATOR TEXT, MATERIAL TEXT, "
        "NET_WEIGHT REAL, VERDICT TEXT, FINALIZE_STATUS TEXT, SHIFT TEXT)")
    conn.commit()
    conn.close()
    r = client.post("/api/v1/mes/gateway/connections", json={
        "name": f"__bdd_dm_{uuid.uuid4().hex[:8]}",
        "adapter_type": "database",
        "enabled": True,
        "retry_count": 0,
        "push_events": ["weighing_product_done"],
        "config": {
            "db_type": "sqlite",
            "database": db_file,
            "table": "T_BAIST_WEIGH",
            "template": {
                "SN": "{sn}", "MODEL_NAME": "{model}", "OPERATOR": "{operator}",
                "MATERIAL": "{material}", "NET_WEIGHT": "{net}",
                "VERDICT": "{verdict}", "FINALIZE_STATUS": "{finalize_status}",
                "SHIFT": "{shift}",
            },
        },
    })
    assert r.status_code == 200, r.text
    ctx["dm_conn_id"] = r.json()["id"]
    ctx["dm_file"] = db_file


# ============================================================
# When
# ============================================================
@when(parsers.parse("秤上放上自重 {w:g} 公斤的工件并保持稳定"))
def when_place_piece(ctx, w):
    _feed(ctx, w, dur=0.6)


@when(parsers.parse("装料到净重 {net:g} 公斤并保持稳定"))
def when_fill_to(ctx, net):
    _feed(ctx, net, dur=0.9)


@when("工件被拿离秤台并确认离秤")
def when_depart(ctx):
    tare = ctx["st"].tare_weight or 1.2
    _feed(ctx, -tare, dur=0.8)


@when("视觉连续识别到加钢脚水泥动作")
def when_finalize_label(ctx):
    ctx["now"] += 1.2   # 越过收尾冷却期 (label3_cooldown_sec=1.0)
    _feed_labels(ctx, hit_finalize=True, frames=3)


@when("收尾超时时间已过且秤面持续空秤")
def when_finalize_timeout(ctx):
    ctx["now"] += 6.0   # finalize_timeout_sec=5.0
    _feed(ctx, 0.0, dur=0.3)


@when(parsers.parse("第二件自重 {tare:g} 公斤上秤去皮并装料到 {net:g} 公斤后离秤"))
def when_second_piece(ctx, tare, net):
    ctx["first_sn"] = ctx["st"].pending[0]["sn"] if ctx["st"].pending else None
    _feed(ctx, tare, dur=0.6)
    _feed(ctx, net, dur=0.7)
    _feed(ctx, -tare, dur=0.8)


@when(parsers.parse("清零指令发出前新件自重 {w:g} 公斤直接上秤并保持稳定"))
def when_fast_hand(ctx, w):
    # 秤面此刻显示 -上件皮重 (未清零), 新件上秤后读数 = -旧皮重 + 新自重
    baseline = ctx["st"]._baseline
    _feed(ctx, baseline + w, dur=0.6)


@when("视觉连续在钢脚料盆区域识别到加水泥动作")
def when_guard_violate(ctx):
    guard = ctx["eng"]._guards[ctx["ch"]]
    assert guard is not None, "视觉守卫未随通道注册装配"
    dets = [{"label": "加水泥", "x": 0.7, "y": 0.7, "w": 0.1, "h": 0.1}]  # 区域外
    for _ in range(3):
        ctx["now"] += 0.1
        ctx["events"].extend(guard.feed(dets, now=ctx["now"]))


@when("视觉连续在大盆区域识别到舀料动作")
def when_guard_map_hit(ctx):
    guard = ctx["eng"]._guards[ctx["ch"]]
    assert guard is not None
    dets = [{"label": "舀料", "x": 0.1, "y": 0.1, "w": 0.1, "h": 0.1}]  # 区域内
    for _ in range(3):
        ctx["now"] += 0.1
        for ev in guard.feed(dets, now=ctx["now"]):
            ctx["events"].append(ev)
            if ev.get("action") == "material_label":   # 复刻引擎 feed_detections 的接线
                ctx["st"].set_material_label(ev.get("material"))


@when(parsers.parse("通过接口喂入一帧 {w:g} 公斤读数"))
def when_api_feed(ctx, client, w):
    r = client.post("/api/v1/weighing/feed",
                    json={"channel_id": ctx["ch"], "weight": w})
    assert r.status_code == 200 and r.json().get("ok"), r.text
    ctx["api_snap"] = r.json()["snapshot"]


# ============================================================
# Then
# ============================================================
@then("系统发出去皮指令")
def then_tare_sent(ctx):
    assert "send_tare" in _actions(ctx)


@then("系统未发出去皮指令")
def then_tare_not_sent(ctx):
    assert "send_tare" not in _actions(ctx)


@then(parsers.parse('状态机进入 "{phase}" 相位'))
@then(parsers.parse('状态机保持 "{phase}" 相位'))
def then_phase(ctx, phase):
    assert ctx["st"].phase == phase, f"相位={ctx['st'].phase}, 期望={phase}"


@then(parsers.parse("当前皮重记为 {w:g} 公斤"))
def then_tare_weight(ctx, w):
    assert ctx["st"].tare_weight == pytest.approx(w, abs=0.01), \
        f"皮重={ctx['st'].tare_weight}"


@then(parsers.parse('离秤结算判定为 "{verdict}"'))
def then_settle_verdict(ctx, verdict):
    settled = [e for e in ctx["events"] if e.get("action") == "product_settled"]
    assert settled, "没有产生离秤结算事件"
    assert settled[-1]["result"]["verdict"] == verdict, settled[-1]["result"]


@then(parsers.parse("待收尾队列有 {n:d} 件"))
def then_pending_count(ctx, n):
    assert len(ctx["st"].pending) == n, \
        f"pending={[(e.get('sn'), e.get('net')) for e in ctx['st'].pending]}"


@then("系统调度清零指令")
def then_zero_scheduled(ctx):
    st = ctx["st"]
    assert ("send_zero" in _actions(ctx)
            or st._zero_due_at is not None or st._zero_sent_at is not None), \
        "既未发出清零指令也没有清零调度在途"


@then(parsers.parse('队头件按 "{status}" 状态结案'))
def then_finalized_with(ctx, status):
    fins = [e for e in ctx["events"] if e.get("action") == "finalize"]
    assert fins, "没有产生结案事件"
    assert fins[-1]["status"] == status, fins[-1]


@then(parsers.parse('事件流中出现 "{kind}" 报警'))
def then_alarm_kind(ctx, kind):
    assert kind in _alarm_kinds(ctx), f"报警种类={_alarm_kinds(ctx)}"


@then(parsers.parse('称重记录已落库且判定为 "{verdict}"'))
def then_record_persisted(ctx, verdict):
    from backend.db.database import SessionLocal
    from backend.models.weighing_models import WeighingRecord
    db = SessionLocal()
    try:
        rows = (db.query(WeighingRecord)
                .filter(WeighingRecord.channel_id == ctx["ch"])
                .order_by(WeighingRecord.id.desc()).all())
    finally:
        db.close()
    assert rows, "称重记录表里没有本通道的行"
    assert rows[0].verdict == verdict


@then("队列顺序为先进先出")
def then_fifo_order(ctx):
    pend = ctx["st"].pending
    assert len(pend) == 2
    assert pend[0]["enqueued_at"] < pend[1]["enqueued_at"]
    assert pend[0]["sn"] == ctx["first_sn"]


@then("结案的是第一件")
def then_finalized_first(ctx):
    fins = [e for e in ctx["events"] if e.get("action") == "finalize"]
    assert fins and fins[-1]["entry"]["sn"] == ctx["first_sn"], fins


@then(parsers.parse('状态机当前视觉料别为 "{label}"'))
def then_visual_label(ctx, label):
    assert ctx["st"].visual_label == label, f"visual_label={ctx['st'].visual_label}"


@then(parsers.parse("虚拟达梦中间表新增 {n:d} 行"))
def then_dm_rows(ctx, n):
    # v3.45 起网关推送走后台线程 (热路径绝不等网络), 结算返回时行可能尚未落库
    # → 轮询等待最多 5s (契约: 异步但必达, 只要网关可用)
    deadline = time.time() + 5.0
    rows = []
    while time.time() < deadline:
        conn = sqlite3.connect(ctx["dm_file"])
        rows = conn.execute(
            "SELECT SN, MODEL_NAME, MATERIAL, NET_WEIGHT, VERDICT, FINALIZE_STATUS "
            "FROM T_BAIST_WEIGH").fetchall()
        conn.close()
        if len(rows) >= n:
            break
        time.sleep(0.1)
    ctx["dm_rows"] = rows
    assert len(rows) == n, f"虚拟达梦行数={len(rows)}: {rows}"


@then(parsers.parse('该行净重为 {net:g} 且判定为 "{verdict}" 且收尾状态为 "{fstatus}"'))
def then_dm_row_fields(ctx, net, verdict, fstatus):
    row = ctx["dm_rows"][-1]
    sn, model, material, net_w, vd, fs = row
    assert sn and sn.startswith("W"), row
    assert model == "型号A" and material == "钢帽水泥", row
    assert float(net_w) == pytest.approx(net, abs=0.01), row
    assert vd == verdict and fs == fstatus, row


@then(parsers.parse("看板状态接口返回该通道实时重量 {w:g} 公斤"))
def then_state_live_weight(ctx, client, w):
    r = client.get(f"/api/v1/weighing/state?channel={ctx['ch']}")
    assert r.status_code == 200
    snap = r.json()
    assert snap["live_weight"] == pytest.approx(w, abs=0.01), snap
    assert snap["effective_weight"] == pytest.approx(w, abs=0.01), snap


@then(parsers.parse('看板状态接口返回 "{mode}" 驱动模式'))
def then_state_drive_mode(ctx, client, mode):
    r = client.get(f"/api/v1/weighing/state?channel={ctx['ch']}")
    assert r.json()["drive_mode"] == mode


@then(parsers.parse("称重记录接口返回至少 {n:d} 条记录"))
def then_records_api(ctx, client, n):
    r = client.get(f"/api/v1/weighing/records?channel={ctx['ch']}")
    assert r.status_code == 200
    ctx["api_records"] = r.json()["records"]
    assert len(ctx["api_records"]) >= n, ctx["api_records"]


@then(parsers.parse('最新记录净重为 {net:g} 判定为 "{verdict}"'))
def then_latest_record(ctx, net, verdict):
    rec = ctx["api_records"][-1]
    assert rec["net"] == pytest.approx(net, abs=0.01), rec
    assert rec["verdict"] == verdict, rec
