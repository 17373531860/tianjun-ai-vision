# -*- coding: utf-8 -*-
"""接收 YoloVision .yvmodel 模型运行包 → 安全校验 → 入模型仓库.

.yvmodel = ZIP, 内含 manifest.json (契约 1.0.0, camelCase) + artifacts/* 模型产物
(+ 可选 validation/ licenses/ docs/ signature.bin)。安全边界:
- 拒绝路径穿越 / 绝对路径 / 可执行载荷
- 逐产物 SHA-256 对账 (manifest.artifacts[].sha256)
- 解压总量与单文件压缩比防 zip 炸弹
入库映射:
- primary 产物 → Model 文件 (framework 按产物 format 映射)
- manifest.classes (按 id 排序) → Model.labels
- extensions['x-project-name'] 精确匹配 Project.name → Model.project_id
- manifest + extensions['x-analysis'] → Model.meta (前端模型仓库展示训练分析)
"""
from __future__ import annotations

import hashlib
import json
import os
import posixpath
import uuid
import zipfile
from datetime import datetime


class PackageError(Exception):
    """包校验失败 → HTTP 400。"""


class PackageConflict(Exception):
    """同名同版本已存在 → HTTP 409。"""

    def __init__(self, model_id: int, name: str, version: str | None):
        self.model_id = model_id
        self.name = name
        self.version = version
        super().__init__(f"模型 '{name}' 版本 '{version or '未填写'}' 已存在 (id={model_id})")


_BLOCKED_EXT = {
    ".exe", ".dll", ".so", ".dylib", ".bat", ".cmd", ".ps1", ".sh",
    ".py", ".pyc", ".js", ".vbs", ".msi", ".com", ".scr", ".jar",
}
_FORMAT_TO_FRAMEWORK = {
    "onnx": "ONNX",
    "pytorch": "PyTorch",
    "torchscript": "TorchScript",
    "tensorrt": "TensorRT",
}
_MAX_TOTAL_UNCOMPRESSED = 8 * 1024 ** 3   # 8 GB
_MAX_COMPRESSION_RATIO = 300              # 单文件压缩比上限 (>10MB 时校验)
_CHUNK = 4 * 1024 * 1024


def ingest_package(package_path: str, db) -> dict:
    """解析并入库一个 .yvmodel 包, 返回契约响应体 (不含 HTTP 状态)。

    Raises: PackageError / PackageConflict
    """
    from backend.core.config import settings
    from backend.models.models import Model, Project

    try:
        zf = zipfile.ZipFile(package_path)
    except zipfile.BadZipFile as e:
        raise PackageError(f"不是合法的 .yvmodel (ZIP) 文件: {e}") from e

    with zf:
        _validate_entries(zf)
        manifest = _read_manifest(zf)

        contract = str(manifest.get("contractVersion") or "")
        if not contract.startswith("1."):
            raise PackageError(f"不支持的包契约版本: {contract or '缺失'} (需要 1.x)")

        name = (manifest.get("name") or "").strip()
        if not name:
            raise PackageError("manifest.name 缺失")
        version = (manifest.get("version") or "").strip() or None

        artifacts = manifest.get("artifacts") or []
        primary = next((a for a in artifacts
                        if (a.get("role") or "").lower() == "primary"), None)
        if primary is None:
            raise PackageError("manifest.artifacts 缺少 primary 产物")

        fmt = (primary.get("format") or "").lower()
        framework = _FORMAT_TO_FRAMEWORK.get(fmt)
        if framework is None:
            raise PackageError(
                f"primary 产物格式 '{fmt}' 不受支持 "
                f"(支持: {', '.join(sorted(_FORMAT_TO_FRAMEWORK))})")

        # 逐产物 SHA-256 对账 (不落盘, 流式)
        warnings: list[str] = []
        for art in artifacts:
            _verify_artifact_sha(zf, art)

        # 幂等: name + version 唯一
        existing = db.query(Model).filter(
            Model.name == name, Model.version == version).first()
        if existing is not None:
            raise PackageConflict(existing.id, name, version)

        # 类别表 → labels (按 id 排序)
        classes = manifest.get("classes") or []
        labels = [
            (c.get("displayName") or c.get("key") or f"class_{c.get('id')}")
            for c in sorted(classes, key=lambda c: int(c.get("id", 0) or 0))
        ] or None

        # 项目对齐: x-project-name 精确匹配
        extensions = manifest.get("extensions") or {}
        x_project = (extensions.get("x-project-name") or "").strip()
        project = None
        if x_project:
            project = db.query(Project).filter(Project.name == x_project).first()
            if project is None:
                warnings.append(f"未找到同名项目 '{x_project}', 模型未关联项目")
        else:
            warnings.append("包内缺少 x-project-name 扩展字段, 模型未关联项目")

        # 落盘 primary 产物
        member = primary.get("path")
        base_name = posixpath.basename(member)
        unique_filename = f"{uuid.uuid4().hex}_{base_name}"
        os.makedirs(settings.MODEL_UPLOAD_DIR, exist_ok=True)
        file_path = os.path.join(settings.MODEL_UPLOAD_DIR, unique_filename)
        try:
            with zf.open(member) as src, open(file_path, "wb") as dst:
                while True:
                    chunk = src.read(_CHUNK)
                    if not chunk:
                        break
                    dst.write(chunk)
            file_size = os.path.getsize(file_path)
        except Exception as e:
            _safe_remove(file_path)
            raise PackageError(f"提取模型产物失败: {e}") from e

        analysis = extensions.get("x-analysis")
        meta = {
            "source": "yolovision",
            "contract_version": contract,
            "package_id": manifest.get("packageId"),
            "release_channel": manifest.get("releaseChannel"),
            "created_at_utc": manifest.get("createdAtUtc"),
            "task_type": (manifest.get("task") or {}).get("type"),
            "x_project_name": x_project or None,
            "project_matched": project is not None,
            "provenance": manifest.get("provenance") or {},
            "analysis": analysis if isinstance(analysis, dict) else None,
            "artifacts": [
                {"id": a.get("id"), "format": a.get("format"),
                 "precision": a.get("precision"), "role": a.get("role"),
                 "size": a.get("size")}
                for a in artifacts
            ],
            "received_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }

        model = Model(
            name=name,
            file_path=file_path,
            file_name=base_name,
            file_size=file_size,
            framework=framework,
            labels=labels,
            description=f"YoloVision 训练平台推送 (包 {manifest.get('packageId') or '未知'})",
            version=version,
            project_id=project.id if project is not None else None,
            source="yolovision",
            meta=meta,
        )
        try:
            db.add(model)
            db.commit()
            db.refresh(model)
        except Exception:
            db.rollback()
            _safe_remove(file_path)
            raise

        return {
            "contract": "1.1",
            "success": True,
            "model_id": model.id,
            "model_name": model.name,
            "version": model.version,
            "project_matched": project is not None,
            "project_id": project.id if project is not None else None,
            "project_name": project.name if project is not None else None,
            "warnings": warnings,
        }


# ------------------------------ 校验 ------------------------------
def _validate_entries(zf: zipfile.ZipFile) -> None:
    total_uncompressed = 0
    for info in zf.infolist():
        n = info.filename
        if n.endswith("/"):
            continue
        norm = posixpath.normpath(n.replace("\\", "/"))
        if norm.startswith("/") or norm.startswith("..") or ".." in norm.split("/"):
            raise PackageError(f"包内存在非法路径: {n}")
        if os.path.splitext(norm)[1].lower() in _BLOCKED_EXT:
            raise PackageError(f"包内存在违禁可执行载荷: {n}")
        total_uncompressed += info.file_size
        if total_uncompressed > _MAX_TOTAL_UNCOMPRESSED:
            raise PackageError("包解压总量超限 (>8GB)")
        if (info.file_size > 10 * 1024 * 1024
                and info.file_size > _MAX_COMPRESSION_RATIO * max(info.compress_size, 1)):
            raise PackageError(f"包内文件压缩比异常 (疑似 zip 炸弹): {n}")


def _read_manifest(zf: zipfile.ZipFile) -> dict:
    try:
        raw = zf.read("manifest.json")
    except KeyError:
        raise PackageError("包内缺少 manifest.json") from None
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise PackageError(f"manifest.json 解析失败: {e}") from e
    if not isinstance(manifest, dict):
        raise PackageError("manifest.json 顶层必须是 JSON 对象")
    return manifest


def _verify_artifact_sha(zf: zipfile.ZipFile, art: dict) -> None:
    member = art.get("path") or ""
    norm = posixpath.normpath(member.replace("\\", "/"))
    if not norm.startswith("artifacts/"):
        raise PackageError(f"产物路径必须位于 artifacts/ 下: {member}")
    expected = (art.get("sha256") or "").lower().strip()
    try:
        src = zf.open(member)
    except KeyError:
        raise PackageError(f"manifest 声明的产物在包内不存在: {member}") from None
    hasher = hashlib.sha256()
    with src:
        while True:
            chunk = src.read(_CHUNK)
            if not chunk:
                break
            hasher.update(chunk)
    if expected and hasher.hexdigest() != expected:
        raise PackageError(f"产物 SHA-256 校验失败: {member}")


def _safe_remove(path: str) -> None:
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass
