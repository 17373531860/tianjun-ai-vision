# ==================== 现场配方包 (.tjvsite) API ====================
# 把一台已配好机器的逻辑配置打成加密文件, 拖到另一台机器导入。
#
# 端点 (挂 /api/v1/site-pack):
#   GET  /overview       各分域内容量 (导出勾选 UI)
#   POST /export         勾选分域 + 可选密码 → 下载 .tjvsite
#   POST /preview        上传包 (multipart) → 只解密看 manifest, 不落任何配置
#   POST /import         上传包 → 校验 + 自动回滚包 + 应用, 返回报告与待办
#   POST /preview-path   同 preview, 但按本机路径读 (Electron 双击 .tjvsite 打开时,
#   POST /import-path    文件已在本机磁盘, 不必再过一遍 HTTP 上传)
#
# 导入硬门槛: 任一工位检测中 → 409 拒绝 (导入会覆盖项目配置/重载工位数)。
# 导入前自动把本机同分域现状打成回滚包存 DATA_DIR/site_pack_backups/。

import os
import shutil
import tempfile
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from backend.core.auth_deps import require_perm
from backend.core.config import DATA_DIR
from backend.db.database import get_db
from backend.services.site_pack import apply as pack_apply
from backend.services.site_pack import collect as pack_collect
from backend.services.site_pack import crypto as pack_crypto

router = APIRouter()

BACKUP_DIR = os.path.join(DATA_DIR, "site_pack_backups")
BACKUP_KEEP = 5


class ExportRequest(BaseModel):
    domains: List[str]
    password: Optional[str] = None
    note: Optional[str] = None
    # 前端 localStorage 侧配置 (显示设置/检测框样式), 由前端随请求带上打进包
    local_settings: Optional[dict] = None


class PathRequest(BaseModel):
    path: str
    password: Optional[str] = None


def _ensure_not_detecting():
    from backend.api.channel_manager import get_channel_manager
    busy = [cid for cid, mgr in get_channel_manager().channels.items()
            if getattr(mgr, "is_detecting", False)]
    if busy:
        raise HTTPException(
            status_code=409,
            detail=f"工位 {busy} 正在检测中, 请先停止所有工位的检测再导入配方包")


def _make_rollback_pack(db: Session, domains: List[str]) -> str:
    """导入前把本机同分域现状打成回滚包 (产品密钥加密, 无密码)。"""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(BACKUP_DIR, f"rollback-{stamp}.tjvsite")
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        tmp_zip = tmp.name
    try:
        pack_collect.build_payload_zip(
            db, domains, tmp_zip, note=f"导入前自动备份 {stamp}")
        pack_crypto.encrypt_file(tmp_zip, dest)
    finally:
        if os.path.exists(tmp_zip):
            os.remove(tmp_zip)
    # 只留最近 N 份, 防止反复导入吃满磁盘
    backups = sorted(f for f in os.listdir(BACKUP_DIR)
                     if f.startswith("rollback-") and f.endswith(".tjvsite"))
    for old in backups[:-BACKUP_KEEP]:
        try:
            os.remove(os.path.join(BACKUP_DIR, old))
        except OSError:
            pass
    return dest


def _decrypt_to_zip(pack_path: str, password: Optional[str]) -> str:
    """解密 .tjvsite → 明文 zip 临时文件路径。加密错误转 HTTP 4xx。"""
    fd, zip_path = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    try:
        pack_crypto.decrypt_file(pack_path, zip_path, password)
        return zip_path
    except pack_crypto.SitePackPasswordRequired as e:
        os.remove(zip_path)
        raise HTTPException(status_code=401, detail=str(e))
    except pack_crypto.SitePackError as e:
        os.remove(zip_path)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        os.remove(zip_path)
        raise


def _read_manifest(zip_path: str) -> dict:
    import json
    import zipfile
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            if "manifest.json" not in zf.namelist():
                raise HTTPException(status_code=400, detail="配方包缺少 manifest, 无法识别")
            return json.loads(zf.read("manifest.json").decode("utf-8"))
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="配方包内容损坏, 无法解包")


def _preview(pack_path: str, password: Optional[str]) -> dict:
    info = pack_crypto.probe_header(pack_path)
    if info["needs_password"] and not password:
        return {"needs_password": True}
    zip_path = _decrypt_to_zip(pack_path, password)
    try:
        manifest = _read_manifest(zip_path)
    finally:
        os.remove(zip_path)
    return {"needs_password": info["needs_password"], "manifest": manifest,
            "todos": [pack_apply.DOMAIN_TODOS[d]
                      for d in manifest.get("domains", [])
                      if d in pack_apply.DOMAIN_TODOS]}


def _do_import(db: Session, pack_path: str, password: Optional[str]) -> dict:
    _ensure_not_detecting()
    zip_path = _decrypt_to_zip(pack_path, password)
    try:
        manifest = _read_manifest(zip_path)
        rollback_path = _make_rollback_pack(db, manifest.get("domains", []))
        report = pack_apply.apply_payload_zip(db, zip_path)
        report["rollback_pack"] = rollback_path
        return report
    except pack_apply.SitePackApplyError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        os.remove(zip_path)


# ---------------- 端点 ----------------

@router.get("/overview",
            dependencies=[Depends(require_perm("system.site_pack.export"))])
def overview(db: Session = Depends(get_db)):
    """各分域当前内容量, 供导出页勾选。"""
    return {"domains": pack_collect.collect_domain_overview(db)}


@router.post("/export",
             dependencies=[Depends(require_perm("system.site_pack.export"))])
def export_pack(req: ExportRequest, db: Session = Depends(get_db)):
    if not req.domains:
        raise HTTPException(status_code=400, detail="请至少勾选一个配置分域")
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        tmp_zip = tmp.name
    fd, pack_path = tempfile.mkstemp(suffix=".tjvsite")
    os.close(fd)
    try:
        pack_collect.build_payload_zip(
            db, req.domains, tmp_zip,
            local_settings=req.local_settings, note=req.note or "")
        pack_crypto.encrypt_file(tmp_zip, pack_path, req.password or None)
    except Exception:
        if os.path.exists(pack_path):
            os.remove(pack_path)
        raise
    finally:
        if os.path.exists(tmp_zip):
            os.remove(tmp_zip)

    filename = f"tianjun-site-{datetime.now().strftime('%Y%m%d-%H%M')}.tjvsite"
    return FileResponse(
        pack_path, filename=filename,
        media_type="application/octet-stream",
        background=BackgroundTask(lambda: os.path.exists(pack_path) and os.remove(pack_path)))


@router.post("/preview",
             dependencies=[Depends(require_perm("system.site_pack.import"))])
async def preview_pack(file: UploadFile = File(...),
                       password: Optional[str] = Form(None)):
    """上传 .tjvsite 看内容概要, 不改任何配置。"""
    fd, pack_path = tempfile.mkstemp(suffix=".tjvsite")
    try:
        with os.fdopen(fd, "wb") as dst:
            shutil.copyfileobj(file.file, dst, length=4 * 1024 * 1024)
        return _preview(pack_path, password)
    finally:
        os.remove(pack_path)


@router.post("/import",
             dependencies=[Depends(require_perm("system.site_pack.import"))])
async def import_pack(file: UploadFile = File(...),
                      password: Optional[str] = Form(None),
                      db: Session = Depends(get_db)):
    fd, pack_path = tempfile.mkstemp(suffix=".tjvsite")
    try:
        with os.fdopen(fd, "wb") as dst:
            shutil.copyfileobj(file.file, dst, length=4 * 1024 * 1024)
        return _do_import(db, pack_path, password)
    finally:
        os.remove(pack_path)


@router.post("/preview-path",
             dependencies=[Depends(require_perm("system.site_pack.import"))])
def preview_pack_path(req: PathRequest):
    """按本机路径预览 (Electron 双击 .tjvsite 打开的场景)。"""
    if not os.path.isfile(req.path):
        raise HTTPException(status_code=404, detail=f"文件不存在: {req.path}")
    return _preview(req.path, req.password)


@router.post("/import-path",
             dependencies=[Depends(require_perm("system.site_pack.import"))])
def import_pack_path(req: PathRequest, db: Session = Depends(get_db)):
    if not os.path.isfile(req.path):
        raise HTTPException(status_code=404, detail=f"文件不存在: {req.path}")
    return _do_import(db, req.path, req.password)
