"""
v3.53 录像归档 — 规则 CRUD + 状态观测 + 台账查询 + 试归档 API

端点全部前缀 /api/v1/export/video-archive：

- GET    /export/video-archive/rules              列出规则
- GET    /export/video-archive/rules/{id}         获取详情
- POST   /export/video-archive/rules              创建
- PUT    /export/video-archive/rules/{id}         更新
- DELETE /export/video-archive/rules/{id}         删除
- POST   /export/video-archive/rules/{id}/toggle  启停
- POST   /export/video-archive/test-run           试归档 (指定/最近一个带录像的周期)
- GET    /export/video-archive/status             引擎状态 (队列/spool/计数/最近错误)
- GET    /export/video-archive/logs               台账 (分页)
- GET    /export/video-archive/adapter-types      可用目的地类型 (含插件注册的)
- POST   /export/video-archive/backfill           历史回补 (四期, 按规则补归档存量录像)
- POST   /export/video-archive/evidence-pack      手动证据包 (二期, 勾选周期打 zip 下载)

写操作挂 settings.edit 权限 (归档目录是系统级配置, 与数据导出的 data.export 分开);
证据包导出挂 data.export (与其他数据导出一致)。
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.core.auth_deps import require_perm
from backend.db.database import get_db
from backend.models.archive_models import VideoArchiveLog, VideoArchiveRule
from backend.models.models import DetectionCycle
from backend.services.archive_adapters import list_adapter_types
from backend.services.archive_secrets import (
    encrypt_config, mask_config, merge_masked_config,
)
from backend.services.video_archive import (
    archive_cycle_now, backfill_rule, build_evidence_pack, get_status,
    refresh_rules_cache, validate_dest_dir, validate_window,
)


router = APIRouter()


# ============================================================
# Pydantic Schema
# ============================================================

_DEST_TYPE_RE = "^(local_dir|ftp|sftp|s3|http|plugin:[A-Za-z0-9_\\-]+)$"


class _RuleBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    enabled: bool = True
    description: Optional[str] = None
    result_filter: str = Field("ng_only", pattern="^(all|ng_only|ok_only)$")
    channel_filter: Optional[List[int]] = None
    project_filter: Optional[List[int]] = None
    dest_dir: str = Field(..., min_length=1, max_length=500)
    subdir_by_date: bool = True
    filename_template: str = Field(
        "{{ workpiece.serial_no | default(cycle.id, true) }}"
        "_{{ 'OK' if cycle.is_good else 'NG' }}.mp4",
        min_length=1, max_length=256)
    overwrite_policy: str = Field("rename", pattern="^(rename|overwrite|skip)$")
    # 二期: 证据能力
    attach_keyframe: bool = False
    keyframe_watermark: bool = True
    sidecar_template_id: Optional[int] = None
    bundle_zip: bool = False
    transform: str = Field("none", pattern="^(none|clip_tail)$")
    clip_seconds: int = Field(10, ge=1, le=600)
    # 四期: 远端与治理
    dest_type: str = Field("local_dir", pattern=_DEST_TYPE_RE)
    dest_config: Optional[Dict[str, Any]] = None
    active_window: Optional[str] = None
    bandwidth_limit_kbps: Optional[int] = Field(None, ge=0, le=1024 * 1024)
    delete_source_after: bool = False


class RuleCreate(_RuleBase):
    pass


class RuleUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=128)
    enabled: Optional[bool] = None
    description: Optional[str] = None
    result_filter: Optional[str] = Field(None, pattern="^(all|ng_only|ok_only)$")
    channel_filter: Optional[List[int]] = None
    project_filter: Optional[List[int]] = None
    dest_dir: Optional[str] = Field(None, min_length=1, max_length=500)
    subdir_by_date: Optional[bool] = None
    filename_template: Optional[str] = Field(None, min_length=1, max_length=256)
    overwrite_policy: Optional[str] = Field(None, pattern="^(rename|overwrite|skip)$")
    attach_keyframe: Optional[bool] = None
    keyframe_watermark: Optional[bool] = None
    sidecar_template_id: Optional[int] = None
    bundle_zip: Optional[bool] = None
    transform: Optional[str] = Field(None, pattern="^(none|clip_tail)$")
    clip_seconds: Optional[int] = Field(None, ge=1, le=600)
    dest_type: Optional[str] = Field(None, pattern=_DEST_TYPE_RE)
    dest_config: Optional[Dict[str, Any]] = None
    active_window: Optional[str] = None
    bandwidth_limit_kbps: Optional[int] = Field(None, ge=0, le=1024 * 1024)
    delete_source_after: Optional[bool] = None


class TestRunRequest(BaseModel):
    cycle_id: Optional[int] = None   # 不给则取最近一个带录像的周期
    rule_id: Optional[int] = None    # 不给则跑全部启用规则


class BackfillRequest(BaseModel):
    rule_id: int
    date_from: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    date_to: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    limit: int = Field(500, ge=1, le=5000)


class EvidencePackRequest(BaseModel):
    """三选一: cycle_ids 直接给 / session_id 整会话 / date 整天。默认只打 NG。"""
    cycle_ids: Optional[List[int]] = None
    session_id: Optional[int] = None
    date: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    ng_only: bool = True
    limit: int = Field(200, ge=1, le=500)


def _rule_to_dict(r: VideoArchiveRule) -> Dict[str, Any]:
    return {
        "id": r.id,
        "name": r.name,
        "enabled": r.enabled,
        "description": r.description,
        "result_filter": r.result_filter,
        "channel_filter": r.channel_filter,
        "project_filter": r.project_filter,
        "dest_dir": r.dest_dir,
        "subdir_by_date": r.subdir_by_date,
        "filename_template": r.filename_template,
        "overwrite_policy": r.overwrite_policy,
        "attach_keyframe": r.attach_keyframe,
        "keyframe_watermark": r.keyframe_watermark,
        "sidecar_template_id": r.sidecar_template_id,
        "bundle_zip": r.bundle_zip,
        "transform": r.transform,
        "clip_seconds": r.clip_seconds,
        "dest_type": r.dest_type,
        "dest_config": mask_config(r.dest_config),
        "active_window": r.active_window,
        "bandwidth_limit_kbps": r.bandwidth_limit_kbps,
        "delete_source_after": r.delete_source_after,
        "last_run_time": r.last_run_time.isoformat() if r.last_run_time else None,
        "last_run_status": r.last_run_status,
        "last_run_error": r.last_run_error,
        "last_dest_file": r.last_dest_file,
        "success_count": r.success_count,
        "failed_count": r.failed_count,
        "skipped_count": r.skipped_count,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


def _validate_payload_dirs(dest_dir: Optional[str], dest_type: str = "local_dir"):
    # 目录护栏只对本地目的地有意义; 远端类型 dest_dir 仅作展示标签
    if dest_dir is None or (dest_type or "local_dir") != "local_dir":
        return
    reason = validate_dest_dir(dest_dir)
    if reason:
        raise HTTPException(status_code=400, detail=reason)


def _validate_window_payload(window: Optional[str]):
    reason = validate_window(window)
    if reason:
        raise HTTPException(status_code=400, detail=reason)


# ============================================================
# 规则 CRUD
# ============================================================

@router.get("/video-archive/rules")
def list_rules(db: Session = Depends(get_db)) -> Dict[str, Any]:
    rules = db.query(VideoArchiveRule).order_by(VideoArchiveRule.id).all()
    return {"items": [_rule_to_dict(r) for r in rules], "total": len(rules)}


@router.get("/video-archive/rules/{rule_id}")
def get_rule(rule_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    r = db.query(VideoArchiveRule).filter(VideoArchiveRule.id == rule_id).first()
    if not r:
        raise HTTPException(status_code=404, detail="规则不存在")
    return _rule_to_dict(r)


@router.post("/video-archive/rules",
             dependencies=[Depends(require_perm("settings.edit"))])
def create_rule(payload: RuleCreate, db: Session = Depends(get_db)) -> Dict[str, Any]:
    _validate_payload_dirs(payload.dest_dir, payload.dest_type)
    _validate_window_payload(payload.active_window)
    data = payload.model_dump()
    data["dest_config"] = encrypt_config(data.get("dest_config"))
    r = VideoArchiveRule(**data)
    db.add(r)
    db.commit()
    db.refresh(r)
    refresh_rules_cache()
    return _rule_to_dict(r)


@router.put("/video-archive/rules/{rule_id}",
            dependencies=[Depends(require_perm("settings.edit"))])
def update_rule(rule_id: int, payload: RuleUpdate,
                db: Session = Depends(get_db)) -> Dict[str, Any]:
    r = db.query(VideoArchiveRule).filter(VideoArchiveRule.id == rule_id).first()
    if not r:
        raise HTTPException(status_code=404, detail="规则不存在")
    data = payload.model_dump(exclude_unset=True)
    _validate_payload_dirs(data.get("dest_dir"),
                           data.get("dest_type", r.dest_type))
    if "active_window" in data:
        _validate_window_payload(data.get("active_window"))
    if "dest_config" in data:
        # 前端敏感字段回传 '******' = 未改, 保留库里旧值后再加密
        data["dest_config"] = encrypt_config(
            merge_masked_config(data["dest_config"], r.dest_config))
    for k, v in data.items():
        setattr(r, k, v)
    db.commit()
    db.refresh(r)
    refresh_rules_cache()
    return _rule_to_dict(r)


@router.delete("/video-archive/rules/{rule_id}",
               dependencies=[Depends(require_perm("settings.edit"))])
def delete_rule(rule_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    r = db.query(VideoArchiveRule).filter(VideoArchiveRule.id == rule_id).first()
    if not r:
        raise HTTPException(status_code=404, detail="规则不存在")
    db.delete(r)
    db.commit()
    refresh_rules_cache()
    return {"ok": True}


@router.post("/video-archive/rules/{rule_id}/toggle",
             dependencies=[Depends(require_perm("settings.edit"))])
def toggle_rule(rule_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    r = db.query(VideoArchiveRule).filter(VideoArchiveRule.id == rule_id).first()
    if not r:
        raise HTTPException(status_code=404, detail="规则不存在")
    r.enabled = not r.enabled
    db.commit()
    db.refresh(r)
    refresh_rules_cache()
    return _rule_to_dict(r)


# ============================================================
# 试归档 / 状态 / 台账
# ============================================================

@router.post("/video-archive/test-run",
             dependencies=[Depends(require_perm("settings.edit"))])
def test_run(payload: TestRunRequest,
             db: Session = Depends(get_db)) -> Dict[str, Any]:
    cycle_id = payload.cycle_id
    if cycle_id is None:
        row = db.query(DetectionCycle).filter(
            DetectionCycle.video_path.isnot(None),
        ).order_by(DetectionCycle.id.desc()).first()
        if not row:
            raise HTTPException(status_code=404, detail="没有找到带录像的周期，无法试归档")
        cycle_id = row.id
    results = archive_cycle_now(cycle_id, rule_id=payload.rule_id)
    return {"cycle_id": cycle_id, "results": results}


@router.get("/video-archive/status")
def archive_status(db: Session = Depends(get_db)) -> Dict[str, Any]:
    st = get_status()
    st["rules_total"] = db.query(VideoArchiveRule).count()
    st["rules_enabled"] = db.query(VideoArchiveRule).filter(
        VideoArchiveRule.enabled.is_(True)).count()
    return st


@router.get("/video-archive/adapter-types")
def adapter_types() -> Dict[str, Any]:
    """可用目的地类型 (内置 + 插件注册的), 前端下拉数据源。"""
    return {"types": list_adapter_types()}


@router.post("/video-archive/backfill",
             dependencies=[Depends(require_perm("settings.edit"))])
def backfill(payload: BackfillRequest) -> Dict[str, Any]:
    """历史回补 (四期): 把存量带录像周期按指定规则补归档, 异步执行。
    目标端建议规则用 skip/overwrite 重名策略保证幂等。"""
    result = backfill_rule(payload.rule_id, payload.date_from,
                           payload.date_to, payload.limit)
    if result.get("error"):
        raise HTTPException(status_code=404, detail=result["error"])
    return result


def _collect_pack_cycle_ids(payload: EvidencePackRequest,
                            db: Session) -> List[int]:
    q = db.query(DetectionCycle.id)
    if payload.cycle_ids:
        q = q.filter(DetectionCycle.id.in_(payload.cycle_ids))
    elif payload.session_id:
        q = q.filter(DetectionCycle.session_id == payload.session_id)
    elif payload.date:
        day = datetime.strptime(payload.date, "%Y-%m-%d")
        q = q.filter(DetectionCycle.start_time >= day,
                     DetectionCycle.start_time
                     < day.replace(hour=23, minute=59, second=59))
    else:
        raise HTTPException(status_code=400,
                            detail="cycle_ids / session_id / date 至少给一个")
    if payload.ng_only:
        q = q.filter(DetectionCycle.is_good.is_(False))
    return [row[0] for row in
            q.order_by(DetectionCycle.id.desc()).limit(payload.limit).all()]


@router.post("/video-archive/evidence-pack",
             dependencies=[Depends(require_perm("data.export"))])
def evidence_pack(payload: EvidencePackRequest,
                  background_tasks: BackgroundTasks,
                  db: Session = Depends(get_db)):
    """手动证据包 (二期): 勾选周期/会话/日期打 zip, 直接流式下载。
    zip 内每周期一个文件夹: video.mp4 + keyframe.jpg + meta.json。"""
    ids = _collect_pack_cycle_ids(payload, db)
    if not ids:
        raise HTTPException(status_code=404, detail="没有符合条件的周期")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(tempfile.gettempdir(), f"evidence_pack_{ts}.zip")
    result = build_evidence_pack(db, ids, out_path)
    if not result.get("ok"):
        raise HTTPException(status_code=404,
                            detail=result.get("error") or "打包失败")
    background_tasks.add_task(lambda p: os.path.exists(p) and os.remove(p),
                              out_path)
    return FileResponse(out_path, media_type="application/zip",
                        filename=f"evidence_pack_{ts}.zip")


@router.get("/video-archive/logs")
def archive_logs(rule_id: Optional[int] = Query(None),
                 status: Optional[str] = Query(None),
                 limit: int = Query(50, ge=1, le=500),
                 offset: int = Query(0, ge=0),
                 db: Session = Depends(get_db)) -> Dict[str, Any]:
    q = db.query(VideoArchiveLog)
    if rule_id is not None:
        q = q.filter(VideoArchiveLog.rule_id == rule_id)
    if status:
        q = q.filter(VideoArchiveLog.status == status)
    total = q.count()
    rows = q.order_by(VideoArchiveLog.id.desc()).offset(offset).limit(limit).all()
    return {
        "total": total,
        "items": [{
            "id": x.id, "rule_id": x.rule_id, "cycle_id": x.cycle_id,
            "channel_id": x.channel_id, "src_path": x.src_path,
            "dest_path": x.dest_path, "status": x.status, "error": x.error,
            "file_size": x.file_size, "duration_ms": x.duration_ms,
            "created_at": x.created_at.isoformat() if x.created_at else None,
        } for x in rows],
    }
