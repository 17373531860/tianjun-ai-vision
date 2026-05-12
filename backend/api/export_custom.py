"""
v3.5.0 自定义导出系统 — 后端 API

挂载路径: /api/v1/export

端点清单：
  字段树
    GET    /export/fields                  字段树（按 group 分组）
    GET    /export/fields?flat=true        平铺列表

  模板 CRUD
    GET    /export/templates                列表（可按 format/scope 过滤）
    GET    /export/templates/{id}           详情
    POST   /export/templates                新建
    PUT    /export/templates/{id}           更新（系统预设受限制）
    DELETE /export/templates/{id}           删除（is_system 禁止）
    POST   /export/templates/{id}/clone     从系统预设复制为自建版

  渲染
    POST   /export/preview                  预览（返回字符串 + 错误）
    POST   /export/render                   立即下载（StreamingResponse）

实时规则相关 CRUD 在 Step 4 单独实现 (export_realtime.py)。
"""
from __future__ import annotations

import io
import os
import re
import shutil
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.core.config import DATA_DIR
from backend.db.database import get_db
from backend.models.export_models import ExportTemplate
from backend.services import export_field_registry as efr
from backend.services.export_context import (
    build_cycle_context, build_range_context, build_system_context,
)
from backend.services.export_renderer import (
    render_string, render_to_bytes, render_filename, render_to_file,
)


# ============================================================
# 路线 B 占位符模板文件存放
# ============================================================

EXPORT_TEMPLATE_UPLOAD_DIR = os.path.join(DATA_DIR, "export_templates")
ALLOWED_TEMPLATE_EXTS = {".docx", ".xlsx"}  # PDF 路线 B 不支持
MAX_TEMPLATE_FILE_SIZE = 20 * 1024 * 1024  # 20MB


router = APIRouter()


# ============================================================
# 字段树
# ============================================================

@router.get("/fields")
def list_export_fields(flat: bool = Query(False, description="true 返回平铺列表，false 按 group 分组")) -> Dict[str, Any]:
    """字段元数据 — 前端"自定义导出"对话框左侧字段树拉取此端点"""
    if flat:
        return {
            "total": len(efr.ALL_FIELDS),
            "fields": efr.list_fields(),
        }
    return {
        "total": len(efr.ALL_FIELDS),
        "groups": efr.list_groups(),
        "stats": efr.stats(),
    }


# ============================================================
# 模板 CRUD
# ============================================================

class TemplateOut(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    format: str
    content: str
    template_file_path: Optional[str] = None
    scope: str
    is_system: bool
    builtin_id: Optional[str] = None
    # v3.7.2 模板自带"推荐规则配置" — 选模板新建规则时, 前端用它自动填空字段
    default_rule_config: Optional[Dict[str, Any]] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    @classmethod
    def from_orm_row(cls, row: ExportTemplate) -> "TemplateOut":
        return cls(
            id=row.id,
            name=row.name,
            description=row.description,
            format=row.format,
            content=row.content or "",
            template_file_path=row.template_file_path,
            scope=row.scope,
            is_system=row.is_system,
            builtin_id=row.builtin_id,
            default_rule_config=row.default_rule_config,
            created_at=row.created_at.isoformat() if row.created_at else None,
            updated_at=row.updated_at.isoformat() if row.updated_at else None,
        )


class TemplateCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    description: Optional[str] = None
    format: str = Field("txt", pattern="^(txt|csv|docx|xlsx|pdf)$")
    content: str = ""
    scope: str = Field("both", pattern="^(batch|realtime|both)$")
    template_file_path: Optional[str] = None
    default_rule_config: Optional[Dict[str, Any]] = None


class TemplateUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=128)
    description: Optional[str] = None
    format: Optional[str] = Field(None, pattern="^(txt|csv|docx|xlsx|pdf)$")
    content: Optional[str] = None
    scope: Optional[str] = Field(None, pattern="^(batch|realtime|both)$")
    template_file_path: Optional[str] = None
    default_rule_config: Optional[Dict[str, Any]] = None


@router.get("/templates")
def list_templates(format: Optional[str] = None,
                   scope: Optional[str] = None,
                   include_system: bool = True,
                   db: Session = Depends(get_db)) -> Dict[str, Any]:
    """模板列表 — 自定义导出对话框右侧"模板选择器"用"""
    q = db.query(ExportTemplate)
    if format:
        q = q.filter(ExportTemplate.format == format)
    if scope:
        # both 模板对所有 scope 都可用
        q = q.filter(ExportTemplate.scope.in_([scope, "both"]))
    if not include_system:
        q = q.filter(ExportTemplate.is_system == False)  # noqa: E712

    rows = q.order_by(ExportTemplate.is_system.desc(), ExportTemplate.id.desc()).all()
    return {"items": [TemplateOut.from_orm_row(r).model_dump() for r in rows]}


@router.get("/templates/{tpl_id}")
def get_template(tpl_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    row = db.query(ExportTemplate).filter(ExportTemplate.id == tpl_id).first()
    if not row:
        raise HTTPException(404, f"模板 {tpl_id} 不存在")
    return TemplateOut.from_orm_row(row).model_dump()


@router.post("/templates")
def create_template(payload: TemplateCreate,
                    db: Session = Depends(get_db)) -> Dict[str, Any]:
    row = ExportTemplate(
        name=payload.name,
        description=payload.description,
        format=payload.format,
        content=payload.content,
        scope=payload.scope,
        template_file_path=payload.template_file_path,
        default_rule_config=payload.default_rule_config,
        is_system=False,
        builtin_id=None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return TemplateOut.from_orm_row(row).model_dump()


@router.put("/templates/{tpl_id}")
def update_template(tpl_id: int, payload: TemplateUpdate,
                    db: Session = Depends(get_db)) -> Dict[str, Any]:
    row = db.query(ExportTemplate).filter(ExportTemplate.id == tpl_id).first()
    if not row:
        raise HTTPException(404, f"模板 {tpl_id} 不存在")
    if row.is_system:
        raise HTTPException(
            400,
            "系统预设模板不可直接修改 — 请先 POST /templates/{id}/clone 复制后再改"
        )

    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(row, k, v)
    db.commit()
    db.refresh(row)
    return TemplateOut.from_orm_row(row).model_dump()


@router.delete("/templates/{tpl_id}")
def delete_template(tpl_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    row = db.query(ExportTemplate).filter(ExportTemplate.id == tpl_id).first()
    if not row:
        raise HTTPException(404, f"模板 {tpl_id} 不存在")
    if row.is_system:
        raise HTTPException(400, "系统预设模板不可删除")
    db.delete(row)
    db.commit()
    return {"status": "ok"}


@router.post("/templates/{tpl_id}/clone")
def clone_template(tpl_id: int,
                   new_name: Optional[str] = None,
                   db: Session = Depends(get_db)) -> Dict[str, Any]:
    """从系统预设或现有自建模板复制一份独立副本（is_system=False）"""
    src = db.query(ExportTemplate).filter(ExportTemplate.id == tpl_id).first()
    if not src:
        raise HTTPException(404, f"模板 {tpl_id} 不存在")

    name = new_name or f"{src.name} (副本)"
    # v3.7.2 default_rule_config 跟着模板复制 — 客户复制扫码器旁路预设后,
    # 自建模板继续保留"推荐规则配置", 新建规则时表单仍能自动填充.
    row = ExportTemplate(
        name=name,
        description=src.description,
        format=src.format,
        content=src.content,
        scope=src.scope,
        template_file_path=src.template_file_path,
        default_rule_config=(
            dict(src.default_rule_config) if isinstance(src.default_rule_config, dict)
            else None
        ),
        is_system=False,
        builtin_id=None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return TemplateOut.from_orm_row(row).model_dump()


# ============================================================
# 渲染（预览 / 下载）
# ============================================================

class _ContextSelector(BaseModel):
    """选哪种 context — 三选一

    优先级: cycle_id > session_id/start_date+end_date > 系统级
    """
    cycle_id: Optional[int] = None
    session_id: Optional[int] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    project_id: Optional[int] = None
    channel_id: Optional[int] = None
    include_cycles: bool = True
    license_payload: Optional[Dict[str, Any]] = None


class PreviewRequest(_ContextSelector):
    template_content: str = ""
    template_id: Optional[int] = None
    filename_template: Optional[str] = None
    fmt: str = Field("txt", pattern="^(txt|csv|docx|xlsx|pdf)$")
    include_context: bool = False  # 调试用：返回构造出的上下文 JSON
    display_payload: Optional[Dict[str, Any]] = None  # 前端 store 直接注入的 display 字段


def _build_context(payload: _ContextSelector, db: Session) -> Dict[str, Any]:
    if payload.cycle_id:
        return build_cycle_context(db, payload.cycle_id,
                                   license_payload=payload.license_payload)
    if (payload.session_id or payload.start_date or payload.end_date
            or payload.project_id or payload.channel_id):
        return build_range_context(
            db,
            start_date=payload.start_date, end_date=payload.end_date,
            session_id=payload.session_id,
            project_id=payload.project_id, channel_id=payload.channel_id,
            include_cycles=payload.include_cycles,
            license_payload=payload.license_payload,
        )
    return build_system_context(db, license_payload=payload.license_payload)


@router.post("/preview")
def preview_template(payload: PreviewRequest,
                     db: Session = Depends(get_db)) -> Dict[str, Any]:
    """预览模板渲染结果

    用于前端"自定义导出"对话框右侧"实时预览"区域。返回:
      {
        "rendered": "...",
        "filename": "abc.txt",
        "context_size": 1234,
        "error": null   # 渲染错误时返回字符串
      }
    """
    tpl_str = payload.template_content
    if payload.template_id and not tpl_str:
        row = db.query(ExportTemplate).filter(
            ExportTemplate.id == payload.template_id
        ).first()
        if not row:
            raise HTTPException(404, f"模板 {payload.template_id} 不存在")
        tpl_str = row.content or ""

    ctx = _build_context(payload, db)

    # 显式 display_payload 优先：前端 useSystemStore.display 即时注入
    # (覆盖 SystemConfig KV 里可能过期的副本)
    if payload.display_payload and isinstance(ctx.get("display"), dict):
        for k, v in payload.display_payload.items():
            if v is not None:
                ctx["display"][k] = v

    out = {
        "rendered": "",
        "filename": "",
        "context_size": 0,
        "error": None,
    }
    try:
        out["rendered"] = render_string(tpl_str, ctx)
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"

    if payload.filename_template:
        try:
            out["filename"] = render_filename(
                payload.filename_template, ctx, fallback_ext=payload.fmt
            )
        except Exception as e:
            out["filename"] = ""
            if out["error"] is None:
                out["error"] = f"filename: {e}"

    try:
        import json as _json
        ctx_json = _json.dumps(ctx, default=str, ensure_ascii=False)
        out["context_size"] = len(ctx_json)
        if payload.include_context:
            # 防爆保护 — 上下文太大就只回前几个 KB
            out["context"] = ctx_json[:200_000]
            if len(ctx_json) > 200_000:
                out["context_truncated"] = True
    except Exception:
        pass
    return out


class RenderRequest(_ContextSelector):
    template_content: str = ""
    template_id: Optional[int] = None
    filename_template: str = "export_{{ now_ymdhms }}.txt"
    fmt: str = Field("txt", pattern="^(txt|csv|docx|xlsx|pdf)$")
    encoding: str = "utf-8"
    newline: str = "lf"
    display_payload: Optional[Dict[str, Any]] = None


@router.post("/render")
def render_to_download(payload: RenderRequest,
                       db: Session = Depends(get_db)) -> StreamingResponse:
    """立即渲染并以文件流返回（前端"立即下载"按钮）"""
    tpl_str = payload.template_content
    template_file_path: Optional[str] = None
    if payload.template_id and not tpl_str:
        row = db.query(ExportTemplate).filter(
            ExportTemplate.id == payload.template_id
        ).first()
        if not row:
            raise HTTPException(404, f"模板 {payload.template_id} 不存在")
        tpl_str = row.content or ""
        template_file_path = row.template_file_path

    if not tpl_str and not template_file_path:
        raise HTTPException(400, "template_content 或 template_id (或上传的 template_file_path) 必填")

    ctx = _build_context(payload, db)
    if payload.display_payload and isinstance(ctx.get("display"), dict):
        for k, v in payload.display_payload.items():
            if v is not None:
                ctx["display"][k] = v
    try:
        filename = render_filename(payload.filename_template, ctx, fallback_ext=payload.fmt)
        raw, mime = render_to_bytes(
            tpl_str, ctx,
            fmt=payload.fmt,
            encoding=payload.encoding,
            newline=payload.newline,
            template_file_path=template_file_path,
        )
    except Exception as e:
        raise HTTPException(400, f"渲染失败: {type(e).__name__}: {e}")

    # 安全的 Content-Disposition (含中文 SN 时需要 RFC 5987 转码)
    safe_ascii = re.sub(r"[^A-Za-z0-9._-]", "_", filename)
    from urllib.parse import quote as _q
    cd = f"attachment; filename=\"{safe_ascii}\"; filename*=UTF-8''{_q(filename)}"

    return StreamingResponse(
        io.BytesIO(raw),
        media_type=mime,
        headers={"Content-Disposition": cd},
    )


# ============================================================
# 路线 B 模板文件上传/下载/删除
# ============================================================

@router.post("/templates/{template_id}/upload-template-file")
async def upload_template_file(
    template_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """上传 docx/xlsx 占位符模板文件 — 路线 B 渲染源

    限制：
    - 文件后缀必须 .docx 或 .xlsx
    - 大小 ≤ 20MB
    - 模板必须存在且 format 与上传扩展名匹配
    - 系统预设禁止上传（先克隆为自建副本）
    """
    row = db.query(ExportTemplate).filter(
        ExportTemplate.id == template_id
    ).first()
    if not row:
        raise HTTPException(404, f"模板 {template_id} 不存在")
    if row.is_system:
        raise HTTPException(403, "系统预设不可上传文件，请先克隆为自建副本")

    fname = file.filename or ""
    ext = os.path.splitext(fname)[1].lower()
    if ext not in ALLOWED_TEMPLATE_EXTS:
        raise HTTPException(400,
            f"仅支持 {sorted(ALLOWED_TEMPLATE_EXTS)} 后缀，收到 {ext}")
    if row.format != ext.lstrip("."):
        raise HTTPException(400,
            f"模板 format={row.format}，但上传文件后缀 {ext} 不匹配")

    os.makedirs(EXPORT_TEMPLATE_UPLOAD_DIR, exist_ok=True)
    save_name = f"tpl_{template_id}{ext}"
    abs_path = os.path.join(EXPORT_TEMPLATE_UPLOAD_DIR, save_name)
    rel_path = os.path.relpath(abs_path, DATA_DIR)

    try:
        with open(abs_path, "wb") as buf:
            shutil.copyfileobj(file.file, buf)
        size = os.path.getsize(abs_path)
        if size > MAX_TEMPLATE_FILE_SIZE:
            os.remove(abs_path)
            raise HTTPException(400,
                f"文件过大 {size} 字节 (限制 {MAX_TEMPLATE_FILE_SIZE})")
    finally:
        file.file.close()

    # 删除旧文件（如有）— 不同后缀情况
    if row.template_file_path and row.template_file_path != rel_path:
        old_abs = row.template_file_path
        if not os.path.isabs(old_abs):
            old_abs = os.path.join(DATA_DIR, old_abs)
        if os.path.exists(old_abs):
            try:
                os.remove(old_abs)
            except Exception:
                pass

    row.template_file_path = rel_path
    db.commit()
    db.refresh(row)
    return TemplateOut.from_orm_row(row).model_dump()


@router.delete("/templates/{template_id}/template-file")
def delete_template_file(template_id: int,
                         db: Session = Depends(get_db)) -> Dict[str, Any]:
    """删除已上传的占位符模板文件 — 删除后渲染会回退到路线 A 自动样式"""
    row = db.query(ExportTemplate).filter(
        ExportTemplate.id == template_id
    ).first()
    if not row:
        raise HTTPException(404, f"模板 {template_id} 不存在")
    if row.is_system:
        raise HTTPException(403, "系统预设不可修改")
    if not row.template_file_path:
        return {"deleted": False, "reason": "no_file"}

    abs_path = row.template_file_path
    if not os.path.isabs(abs_path):
        abs_path = os.path.join(DATA_DIR, abs_path)
    if os.path.exists(abs_path):
        try:
            os.remove(abs_path)
        except Exception as e:
            raise HTTPException(500, f"删除失败: {e}")

    row.template_file_path = None
    db.commit()
    db.refresh(row)
    return {"deleted": True, "template_id": template_id}


@router.get("/templates/{template_id}/template-file")
def download_template_file(template_id: int,
                           db: Session = Depends(get_db)):
    """下载占位符模板文件原文件 — 让用户改完再上传"""
    row = db.query(ExportTemplate).filter(
        ExportTemplate.id == template_id
    ).first()
    if not row:
        raise HTTPException(404, f"模板 {template_id} 不存在")
    if not row.template_file_path:
        raise HTTPException(404, "该模板没有上传占位符文件")

    abs_path = row.template_file_path
    if not os.path.isabs(abs_path):
        abs_path = os.path.join(DATA_DIR, abs_path)
    if not os.path.exists(abs_path):
        raise HTTPException(404, f"文件已不存在: {abs_path}")

    ext = os.path.splitext(abs_path)[1].lower()
    mime = {
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }.get(ext, "application/octet-stream")
    filename = f"{row.name}{ext}"
    return FileResponse(abs_path, media_type=mime, filename=filename)
