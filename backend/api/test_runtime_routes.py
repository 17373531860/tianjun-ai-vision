"""仅在 RUNTIME_MODE=test 时挂载：虚拟 synthetic 源控制 API。"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()


def _require_test_mode() -> None:
    if os.environ.get("RUNTIME_MODE") != "test":
        raise HTTPException(status_code=404, detail="Not found")


class SyntheticStartBody(BaseModel):
    scenario: Optional[str] = None
    scenario_json: Optional[Dict[str, Any]] = None
    channel: int = 0
    fps: Optional[float] = None
    with_project: bool = False
    project_steps: Optional[list] = None
    logic_mode: str = "sequential"
    project_id: Optional[int] = None  # 显式注入项目 id (测试场景: 用真项目 id, 让 session 关联到该项目)


def _collect_labels_from_timeline(timeline) -> list:
    labels: list = []
    seen = set()
    for seg in timeline or []:
        for d in seg.get("detections") or []:
            label = d.get("label")
            if label and label not in seen:
                seen.add(label)
                labels.append(label)
    return labels


def _build_min_project_config(labels: list, logic_mode: str = "sequential") -> dict:
    # 必须带 id: 结算步序解析 (source_sequence_labels._sequence_order) 的
    # steps_config 兜底只认有 id 的步骤, 缺 id → 首步标签解析不出 → 周期永不结算
    steps = [
        {
            "id": f"synth-{idx + 1}",
            "label": label,
            "threshold": 0.3,
            "min_frames": 1,
            "color": "#1976d2",
        }
        for idx, label in enumerate(labels)
    ]
    # sequence_order 必须显式给: _settle_sequential_cycle 直接读它 (无 steps_config
    # 兜底), 缺了会在结算时静默丢弃周期, 永远不出 OK/NG (2026-06-11 排查实锤)
    pipeline_config = {
        "sequence_order": [{"step_id": s["id"]} for s in steps],
    }
    # 让 OK/NG 真正落计数器与周期记录: 挂最小 events_config (id 1=OK, 2=NG 是
    # _trigger_event 的内置约定; 缺事件定义会"事件未找到"早退, 周期变孤儿)
    events_config = [
        {"id": 1, "name": "合格(OK)", "actions": [
            {"counter_name": "合格总数", "delta": 1},
            {"counter_name": "总产量", "delta": 1},
        ]},
        {"id": 2, "name": "不合格(NG)", "actions": [
            {"counter_name": "不良总数", "delta": 1},
            {"counter_name": "总产量", "delta": 1},
        ]},
    ]
    return {
        "id": -1,  # synthetic 占位 id；让 start_detection 能创建 DetectionSession
        "name": "__synthetic__",
        "task_type": "detect",
        "logic_mode": logic_mode,
        "pipeline_config": pipeline_config,
        "steps_config": steps,
        "events_config": events_config,
        "periodic_actions": [],
        "alarm_config": {},
    }


@router.post("/start")
def synthetic_start(body: SyntheticStartBody):
    _require_test_mode()
    from backend.api.source import _get_mgr

    mgr = _get_mgr(body.channel)

    try:
        if body.scenario_json is not None:
            spec = body.scenario_json
            mgr.start_synthetic(scenario_dict=spec, fps=body.fps)
        elif body.scenario:
            mgr.start_synthetic(scenario=body.scenario, fps=body.fps)
            spec = getattr(mgr, "_synthetic_spec", None) or {}
        else:
            spec = {"name": "empty", "timeline": []}
            mgr.start_synthetic(scenario_dict=spec, fps=body.fps)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except (ValueError, KeyError) as e:
        raise HTTPException(status_code=400, detail=str(e))

    if body.with_project:
        labels = body.project_steps or _collect_labels_from_timeline(spec.get("timeline", []))
        if labels:
            cfg = _build_min_project_config(labels, body.logic_mode)
            # 优先级: body.project_id > mgr 已有 project_config.id > -1
            # 让 synthetic 创建的 session.project_id 落到指定项目，Data 页按项目过滤时也能看到。
            if isinstance(body.project_id, int) and body.project_id > 0:
                cfg["id"] = body.project_id
            else:
                existing = getattr(mgr, "project_config", None) or {}
                existing_id = existing.get("id")
                if isinstance(existing_id, int) and existing_id > 0:
                    cfg["id"] = existing_id
                    cfg["name"] = existing.get("name") or cfg.get("name")
            try:
                mgr.apply_temporary_project_config(cfg)
            except Exception as e:
                return {
                    "status": "ok",
                    "debug": mgr.get_synthetic_debug_state(),
                    "project_apply_warning": str(e),
                    "project_labels": labels,
                }
            return {
                "status": "ok",
                "debug": mgr.get_synthetic_debug_state(),
                "project_applied": True,
                "project_labels": labels,
            }

    return {"status": "ok", "debug": mgr.get_synthetic_debug_state()}


@router.post("/stop")
def synthetic_stop(channel: int = 0):
    _require_test_mode()
    from backend.api.source import _get_mgr

    mgr = _get_mgr(channel)
    if getattr(mgr, "source_type", None) == "synthetic":
        mgr.stop_synthetic()
    return {"status": "ok"}


@router.get("/state")
def synthetic_state(channel: int = 0):
    _require_test_mode()
    from backend.api.source import _get_mgr

    mgr = _get_mgr(channel)
    return mgr.get_synthetic_debug_state()


class PackagingSettleBody(BaseModel):
    """仅测试态: 模拟检测层结算一个箱 (等价检测周期 cycle_end 调包装协调器).

    生产里这一跳由检测层 source_session_lifecycle_mixin 自动调,
    slider_count 来自容器累加器峰值. 开发机无相机, 用本端点直接喂数, 走完全相同的
    协调器逐箱状态机, 让 Monitor 包装卡能用虚拟数据可见地逐箱推进.
    """
    channel_id: int = 0
    cycle_id: int = 1
    is_good: bool = True
    slider_count: Optional[int] = None
    paper_done: Optional[bool] = None  # 控制尾箱"塞工单"探测结果 (None=用真实探测)
    # 按步骤标签分别控制视觉探测结果, 用于独立测"放油嘴包"/"放工单"两个 gate.
    # 例: {"放油嘴包": False, "放工单": True} → 缺油嘴 gate 不过、塞工单 gate 过. 未列标签默认 True.
    probe_labels: Optional[Dict[str, bool]] = None


@router.post("/packaging-settle")
def packaging_settle(body: PackagingSettleBody):
    _require_test_mode()
    from backend.services.packaging_flow_coordinator import (
        get_coordinator, _real_paper_order_probe)
    from backend.db.database import SessionLocal

    coord = get_coordinator()
    _probe_overridden = False
    if body.probe_labels is not None:
        _m = {str(k): bool(v) for k, v in body.probe_labels.items()}
        coord.set_paper_order_probe(lambda c, l, _mm=_m: bool(_mm.get(str(l), True)))
        _probe_overridden = True
    elif body.paper_done is not None:
        coord.set_paper_order_probe(lambda c, l, _v=bool(body.paper_done): _v)
        _probe_overridden = True
    db = SessionLocal()
    try:
        cid = coord.resolve_config_id(body.channel_id, None)
        coord.on_cycle_settled(int(body.channel_id), int(body.cycle_id),
                               bool(body.is_good), db, slider_count=body.slider_count)
        cid = cid if cid is not None else coord.resolve_config_id(body.channel_id, None)
        state = coord.get_state(cid) if cid is not None else None
        # 完成/中止后 run 已从内存弹出 → state=None; 回查 DB 最近一条运行记录, 让完成态可观测
        last_run = None
        if cid is not None:
            from backend.models.mes_models import PackagingFlowRun
            row = (db.query(PackagingFlowRun)
                   .filter(PackagingFlowRun.flow_config_id == cid)
                   .order_by(PackagingFlowRun.id.desc()).first())
            if row is not None:
                last_run = {
                    "order_no": row.order_no, "status": row.status,
                    "final_result": row.final_result, "box_total": row.box_total,
                    "box_done": row.box_done, "box_ng": row.box_ng,
                    "count_unit": row.count_unit, "tail_target": row.tail_target,
                }
        return {"status": "ok", "config_id": cid, "state": state, "last_run": last_run}
    finally:
        db.close()
        if _probe_overridden:
            coord.set_paper_order_probe(_real_paper_order_probe)  # 复位, 不污染后续


@router.post("/packaging-reset")
def packaging_reset():
    """仅测试态: 清空包装协调器内存在途运行 + 重接真实钩子 + 重载配置.

    后端长驻进程跨 UAT 脚本运行时, 协调器 _runs 会残留上轮在途工单 → 污染下一轮.
    UAT setup 清完 DB 配置后调一次本端点, 拿到干净的协调器内存.
    """
    _require_test_mode()
    from backend.services.packaging_flow_coordinator import (
        get_coordinator, wire_real_hooks)
    from backend.db.database import SessionLocal

    coord = get_coordinator()
    coord.cleanup_for_testing()        # 清 _runs / _configs + 复位钩子
    wire_real_hooks(coord)             # 重接真实拉单/报警/箱目标/切项目/塞工单探测
    db = SessionLocal()
    try:
        coord.reload_configs(db)       # 按当前 DB 里 enabled 的配置重建 _configs
    finally:
        db.close()
    return {"status": "ok", "loaded": coord.list_loaded_config_ids()}


@router.post("/fire-plugin-cycle-end")
def fire_plugin_cycle_end_hook(channel: int = 0, cycle_id: int = 999, is_good: bool = False):
    """G1.5 调试端点 — 直接触发一次 cycle_end/post_cycle/post hook (仅 RUNTIME_MODE=test).

    避免 synthetic 推理链路 + 真 end_cycle 全跑通才能验证插件 hook 是否被调.
    返回 hook handlers 的执行结果列表 (含错误信息).
    """
    _require_test_mode()
    from backend.plugin_system.manager import plugin_manager

    if plugin_manager.registry is None:
        raise HTTPException(status_code=409, detail="没有 active 插件 / registry 未加载")

    ctx = {
        "channel_id": channel,
        "cycle_id": cycle_id,
        "cycle_uuid": f"debug-fire-{cycle_id}",
        "session_id": -1,
        "is_good": bool(is_good),
        "result": "OK" if is_good else "NG",
        "judgement": "OK" if is_good else "NG",
        "event_id": None,
        "event_name": "debug-trigger",
        "reason": "manual fire from test endpoint",
        "duration": 3.45,
        "step_sequence": ["step_a", "step_b", "step_c"],
        "project_id": None,
    }
    results = plugin_manager.registry.hooks.fire("cycle_end", "post_cycle", "post", ctx)
    return {
        "status": "fired",
        "handlers_count": len(results),
        "ctx": ctx,
        "results": results,
    }
