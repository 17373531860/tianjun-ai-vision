from __future__ import annotations

import json
import mimetypes
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from backend.db.database import get_db
from backend.models.plugin_models import PluginAuditLog, PluginRecord, PluginState
from backend.plugin_system.verifier import (
    PluginVerifyError,
    copy_verified_to_install_dir,
    plugins_root,
    verify_package_to_temp,
)


router = APIRouter(prefix="/plugins", tags=["Plugins"])


def _audit(db: Session, customer_code: Optional[str], action: str, status: str, message: str) -> None:
    db.add(PluginAuditLog(
        customer_code=customer_code,
        action=action,
        status=status,
        message=message,
    ))


def _state(
    db: Session,
    customer_code: str,
    runtime_status: str,
    health: str,
    code: Optional[str] = None,
    message: Optional[str] = None,
) -> PluginState:
    row = db.query(PluginState).filter(PluginState.customer_code == customer_code).first()
    if not row:
        row = PluginState(customer_code=customer_code)
        db.add(row)
    row.runtime_status = runtime_status
    row.health = health
    row.last_error_code = code
    row.last_error_message = message
    return row


def _serialize(record: PluginRecord, state: Optional[PluginState] = None) -> Dict[str, Any]:
    return {
        "customer_code": record.customer_code,
        "name": record.name,
        "plugin_version": record.plugin_version,
        "tier": record.tier,
        "status": record.status,
        "is_active": bool(record.is_active),
        "files_digest": record.files_digest,
        "signed_by": record.signed_by,
        "signed_at": record.signed_at,
        "public_key_fingerprint": record.public_key_fingerprint,
        "installed_at": record.installed_at.isoformat() if record.installed_at else None,
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
        "runtime_status": state.runtime_status if state else "stopped",
        "health": state.health if state else "unknown",
        "last_error_code": state.last_error_code if state else None,
        "last_error_message": state.last_error_message if state else None,
    }


def _get_record(db: Session, customer_code: str) -> PluginRecord:
    record = db.query(PluginRecord).filter(PluginRecord.customer_code == customer_code).first()
    if not record:
        raise HTTPException(status_code=404, detail="插件不存在")
    return record


@router.get("")
def list_plugins(db: Session = Depends(get_db)) -> Dict[str, Any]:
    rows = db.query(PluginRecord).order_by(PluginRecord.installed_at.desc()).all()
    states = {
        s.customer_code: s
        for s in db.query(PluginState).all()
    }
    return {
        "items": [_serialize(row, states.get(row.customer_code)) for row in rows],
        "active_customer_code": next((row.customer_code for row in rows if row.is_active), None),
    }


@router.post("/install")
async def install_plugin(file: UploadFile = File(...), db: Session = Depends(get_db)) -> Dict[str, Any]:
    if not file.filename or not file.filename.endswith(".tjvplugin"):
        raise HTTPException(status_code=400, detail="只支持 .tjvplugin 文件")

    tmp_path = Path(tempfile.mkstemp(prefix="tjv-upload-", suffix=".tjvplugin")[1])
    verified = None
    try:
        size = 0
        with tmp_path.open("wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > 100 * 1024 * 1024:
                    raise PluginVerifyError("PLUGIN_PACKAGE_TOO_LARGE", "插件包超过 100MB")
                out.write(chunk)

        verified = verify_package_to_temp(tmp_path, db=db)
        manifest = verified.manifest
        customer_code = manifest["customer_code"]
        install_dir = copy_verified_to_install_dir(verified)

        record = db.query(PluginRecord).filter(PluginRecord.customer_code == customer_code).first()
        if not record:
            record = PluginRecord(customer_code=customer_code)
            db.add(record)
        record.name = manifest["name"]
        record.plugin_version = manifest["plugin_version"]
        record.tier = int(manifest["tier"])
        record.status = "installed"
        record.install_path = str(install_dir)
        record.manifest_json = json.dumps(manifest, ensure_ascii=False, sort_keys=True)
        record.files_digest = manifest["files_digest"]
        record.signed_by = manifest.get("signed_by")
        record.signed_at = manifest.get("signed_at")
        record.public_key_fingerprint = verified.signature_fingerprint
        _state(db, customer_code, "installed", "unknown")
        _audit(db, customer_code, "install", "success", "插件安装成功")
        db.commit()
        db.refresh(record)
        return {"status": "ok", "plugin": _serialize(record, db.query(PluginState).filter(PluginState.customer_code == customer_code).first())}
    except PluginVerifyError as exc:
        db.rollback()
        _audit(db, None, "install", "failed", f"{exc.code}: {exc.message}")
        db.commit()
        raise HTTPException(status_code=400, detail={"code": exc.code, "message": exc.message}) from exc
    except Exception as exc:
        db.rollback()
        _audit(db, None, "install", "failed", str(exc))
        db.commit()
        raise HTTPException(status_code=500, detail={"code": "PLUGIN_INSTALL_FAILED", "message": str(exc)}) from exc
    finally:
        if verified is not None:
            shutil.rmtree(verified.extracted_dir, ignore_errors=True)
        tmp_path.unlink(missing_ok=True)


@router.get("/active/manifest")
def get_active_manifest(db: Session = Depends(get_db)) -> Dict[str, Any]:
    record = db.query(PluginRecord).filter(PluginRecord.is_active == True).first()
    if not record:
        return {}
    return json.loads(record.manifest_json)


@router.get("/active/assets/{asset_path:path}")
def get_active_asset(asset_path: str, db: Session = Depends(get_db)) -> FileResponse:
    record = db.query(PluginRecord).filter(PluginRecord.is_active == True).first()
    if not record:
        raise HTTPException(status_code=404, detail="没有 active 插件")
    root = Path(record.install_path).resolve()
    target = (root / asset_path).resolve()
    if not str(target).startswith(str(root) + os.sep) or not target.is_file():
        raise HTTPException(status_code=404, detail="资源不存在")
    media_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
    return FileResponse(target, media_type=media_type)


@router.post("/{customer_code}/activate")
def activate_plugin(customer_code: str, db: Session = Depends(get_db)) -> Dict[str, Any]:
    record = _get_record(db, customer_code)
    for row in db.query(PluginRecord).all():
        row.is_active = row.customer_code == customer_code
        if row.customer_code != customer_code and row.status == "active":
            row.status = "installed"
    record.status = "active"
    _state(db, customer_code, "pending_restart", "unknown")
    _audit(db, customer_code, "activate", "success", "插件已激活，重启后生效")
    db.commit()
    db.refresh(record)
    return {"status": "ok", "plugin": _serialize(record, db.query(PluginState).filter(PluginState.customer_code == customer_code).first())}


@router.post("/{customer_code}/deactivate")
def deactivate_plugin(customer_code: str, db: Session = Depends(get_db)) -> Dict[str, Any]:
    record = _get_record(db, customer_code)
    record.is_active = False
    record.status = "installed"
    _state(db, customer_code, "stopped", "unknown")
    _audit(db, customer_code, "deactivate", "success", "插件已停用")
    db.commit()
    db.refresh(record)
    return {"status": "ok", "plugin": _serialize(record, db.query(PluginState).filter(PluginState.customer_code == customer_code).first())}


@router.delete("/{customer_code}")
def delete_plugin(customer_code: str, db: Session = Depends(get_db)) -> Dict[str, Any]:
    record = _get_record(db, customer_code)
    install_path = Path(record.install_path)
    if install_path.exists():
        shutil.rmtree(install_path)
    db.delete(record)
    state = db.query(PluginState).filter(PluginState.customer_code == customer_code).first()
    if state:
        db.delete(state)
    _audit(db, customer_code, "delete", "success", "插件已卸载，业务数据保留")
    db.commit()
    return {"status": "ok"}


@router.get("/{customer_code}/status")
def get_plugin_status(customer_code: str, db: Session = Depends(get_db)) -> Dict[str, Any]:
    record = _get_record(db, customer_code)
    state = db.query(PluginState).filter(PluginState.customer_code == customer_code).first()
    return {"plugin": _serialize(record, state)}
