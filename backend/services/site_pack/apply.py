# ==================== 导入侧: payload zip → 目标机 DB / DATA_DIR ====================
# 原则:
#   - 按稳定身份 upsert: 项目=name, 模型=name+version, 缺陷码=code, KV=key,
#     其余表=name。目标机已有同名 → 覆盖配置列; 没有 → 新建。
#   - 绝不动历史数据 (session/cycle/工件/扫码记录), 也不删目标机多出来的配置。
#   - 跨表引用一律在包里存"名字引用" (xxx_name_ref / project_name_ref),
#     导入时查目标机新 id 回填; 找不到就置空并记 warning。
#   - 每个分域独立 try: 单域失败记 error 继续下一域, 最后整体报告。

import json
import os
import shutil
import uuid
import zipfile

from sqlalchemy.orm import Session

from backend.core.config import DATA_DIR, settings
from backend.models.export_models import ExportTemplate
from backend.models.mes_models import (
    DefectCode, MESConnection, PackagingFlowConfig,
    ScannerDevice, WorkpieceFlowConfig,
)
from backend.models.models import ChannelGroup, Model, Project, SystemConfig
from backend.models.plc_models import PLCConnection
from backend.models.scan_collect_models import ScanCollectConfig
from backend.models.trigger_models import TriggerChannel
from backend.services.site_pack.collect import _PROJECT_FIELDS, DOMAIN_LABELS
from backend.services.site_pack.sanitize import DOMAIN_TODOS, apply_row_dict


class SitePackApplyError(Exception):
    """导入前置校验失败 (消息直接给用户)。"""


def _load_config(zf: zipfile.ZipFile, domain: str):
    name = f"config/{domain}.json"
    if name not in zf.namelist():
        return None
    return json.loads(zf.read(name).decode("utf-8"))


def _apply_kv(db: Session, kv: dict) -> int:
    for key, value in kv.items():
        row = db.query(SystemConfig).filter(SystemConfig.key == key).first()
        if row:
            row.value = value
        else:
            db.add(SystemConfig(key=key, value=value,
                                description="现场配方包导入"))
    return len(kv)


def _apply_models(db: Session, zf: zipfile.ZipFile, items: list,
                  warnings: list) -> int:
    os.makedirs(settings.MODEL_UPLOAD_DIR, exist_ok=True)
    count = 0
    for item in items:
        arcname = item.get("arcname") or ""
        if arcname not in zf.namelist():
            warnings.append(f"模型「{item.get('name')}」包内文件缺失, 已跳过")
            continue
        dest = os.path.join(settings.MODEL_UPLOAD_DIR,
                            f"{uuid.uuid4().hex}_{item['file_name']}")
        with zf.open(arcname) as src, open(dest, "wb") as dst:
            shutil.copyfileobj(src, dst, length=4 * 1024 * 1024)

        row = db.query(Model).filter(
            Model.name == item["name"],
            Model.version == item.get("version")).first()
        old_path = row.file_path if row else None
        if not row:
            row = Model(name=item["name"], version=item.get("version"),
                        file_path=dest, file_name=item["file_name"])
            db.add(row)
        row.file_path = dest
        row.file_name = item["file_name"]
        row.file_size = os.path.getsize(dest)
        row.framework = item.get("framework") or row.framework
        row.labels = item.get("labels")
        row.description = item.get("description")
        # 同名模型旧文件不再被引用, 删掉避免磁盘越导越大 (转换产物走各自记录不动)
        if old_path and old_path != dest and os.path.exists(old_path):
            try:
                os.remove(old_path)
            except OSError:
                pass
        count += 1
    db.flush()
    return count


def _apply_projects(db: Session, items: list, warnings: list) -> int:
    model_ids = {(m.name, m.version): m.id for m in db.query(Model).all()}
    for item in items:
        row = db.query(Project).filter(Project.name == item["name"]).first()
        if not row:
            row = Project(name=item["name"])
            db.add(row)
        for f in _PROJECT_FIELDS:
            if f in item:
                setattr(row, f, item[f])
        ref = item.get("default_model_ref")
        if ref:
            mid = model_ids.get((ref.get("name"), ref.get("version")))
            if mid:
                row.default_model_id = mid
            else:
                warnings.append(
                    f"项目「{item['name']}」的默认模型「{ref.get('name')}」"
                    "不在包内也不在本机, 请手动绑定模型")
    db.flush()
    return len(items)


def _project_id_by_name(db: Session) -> dict:
    return {p.name: p.id for p in db.query(Project).all()}


def _upsert_named_rows(db: Session, orm_cls, items: list,
                       identity_field: str = "name") -> int:
    for item in items:
        ident = item.get(identity_field)
        row = db.query(orm_cls).filter(
            getattr(orm_cls, identity_field) == ident).first()
        if not row:
            row = orm_cls(**{identity_field: ident})
            db.add(row)
        apply_row_dict(row, item)
    db.flush()
    return len(items)


def _apply_defect_codes(db: Session, items: list, warnings: list) -> int:
    pids = _project_id_by_name(db)
    for item in items:
        pname = item.get("project_name_ref")
        item["project_id"] = pids.get(pname) if pname else None
        if pname and item["project_id"] is None:
            warnings.append(f"缺陷码「{item.get('code')}」绑定的项目「{pname}」不存在, 已置为不限项目")
    return _upsert_named_rows(db, DefectCode, items, identity_field="code")


def _apply_scan_collect(db: Session, items: list, warnings: list) -> int:
    pids = _project_id_by_name(db)
    count = 0
    for item in items:
        pid = pids.get(item.get("project_name_ref"))
        if not pid:
            warnings.append(
                f"多码采集配置绑定的项目「{item.get('project_name_ref')}」不存在, 已跳过")
            continue
        row = db.query(ScanCollectConfig).filter(
            ScanCollectConfig.project_id == pid).first()
        if not row:
            row = ScanCollectConfig(project_id=pid)
            db.add(row)
        item["project_id"] = pid
        apply_row_dict(row, item)
        count += 1
    db.flush()
    return count


def _apply_workpiece_flows(db: Session, items: list, warnings: list) -> int:
    scanner_ids = {s.name: s.id for s in db.query(ScannerDevice).all()}
    for item in items:
        sname = item.get("scanner_name_ref")
        item["scan_device_id"] = scanner_ids.get(sname) if sname else None
        if sname and item["scan_device_id"] is None:
            warnings.append(f"串行流水线「{item.get('name')}」绑定的扫码器「{sname}」不存在, 已置空")
    return _upsert_named_rows(db, WorkpieceFlowConfig, items)


def _apply_packaging_flows(db: Session, items: list, warnings: list) -> int:
    scanner_ids = {s.name: s.id for s in db.query(ScannerDevice).all()}
    conn_ids = {c.name: c.id for c in db.query(MESConnection).all()}
    for item in items:
        sname = item.get("scanner_name_ref")
        cname = item.get("mes_conn_name_ref")
        item["scan_device_id"] = scanner_ids.get(sname) if sname else None
        item["pull_conn_id"] = conn_ids.get(cname) if cname else None
        if sname and item["scan_device_id"] is None:
            warnings.append(f"包装流「{item.get('name')}」绑定的扫码器「{sname}」不存在, 已置空")
        if cname and item["pull_conn_id"] is None:
            warnings.append(f"包装流「{item.get('name')}」拉工单的 MES 连接「{cname}」不存在, 已置空")
    return _upsert_named_rows(db, PackagingFlowConfig, items)


def _write_data_json(filename: str, data: dict) -> int:
    path = os.path.join(DATA_DIR, filename)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return 1


def _apply_workstation(db: Session, data: dict, warnings: list) -> int:
    """工位数走 set_channel_count (自带 5 处联动清理); 项目绑定走 merge 段写。
    相机参数不在包里, 目标机各工位原有的 source 配置原样保留。"""
    from backend.api.channel_manager import get_channel_manager

    manager = get_channel_manager()
    count = int(data.get("channel_count") or 1)
    if count != manager.channel_count:
        manager.set_channel_count(count)

    pids = _project_id_by_name(db)
    for ch_key, pname in (data.get("project_bindings") or {}).items():
        pid = pids.get(pname)
        if pid is None:
            warnings.append(f"工位 {ch_key} 绑定的项目「{pname}」不存在, 未绑定")
            continue
        manager.save_channel_source(int(ch_key), {"project_id": pid}, merge=True)
    return 1


def apply_payload_zip(db: Session, zip_path: str) -> dict:
    """把解密后的 payload zip 应用到本机。调用方负责: 检测已停止 + 已做回滚包。"""
    report = {"applied": {}, "warnings": [], "errors": [], "todos": []}
    warnings = report["warnings"]

    with zipfile.ZipFile(zip_path, "r") as zf:
        if "manifest.json" not in zf.namelist():
            raise SitePackApplyError("配方包缺少 manifest, 无法导入")
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        report["manifest"] = manifest

        # (domain, 执行函数) — 顺序即依赖顺序: 模型 → 项目 → 引用它们的各域
        def _run(domain, fn):
            data = _load_config(zf, domain)
            if data is None:
                return
            try:
                report["applied"][domain] = fn(data)
                db.commit()
            except Exception as e:  # 单域失败不拖垮整包
                db.rollback()
                report["errors"].append(
                    f"{DOMAIN_LABELS.get(domain, domain)}: {e}")

        _run("models", lambda d: _apply_models(db, zf, d["items"], warnings))
        _run("projects", lambda d: _apply_projects(db, d["items"], warnings))
        _run("monitor_layout", lambda d: _apply_kv(db, d["kv"]))
        _run("display", lambda d: _apply_kv(db, d["kv"]))
        _run("system_kv", lambda d: _apply_kv(db, d["kv"]))
        _run("mes_inbound", lambda d: _apply_kv(db, d["kv"]))
        _run("defect_codes", lambda d: _apply_defect_codes(db, d["items"], warnings))
        _run("scanners", lambda d: _upsert_named_rows(db, ScannerDevice, d["items"]))
        _run("scan_collect", lambda d: _apply_scan_collect(db, d["items"], warnings))
        _run("mes_connections", lambda d: _upsert_named_rows(db, MESConnection, d["items"]))
        _run("channel_groups", lambda d: _upsert_named_rows(db, ChannelGroup, d["items"]))
        _run("workpiece_flows", lambda d: _apply_workpiece_flows(db, d["items"], warnings))
        _run("packaging_flows", lambda d: _apply_packaging_flows(db, d["items"], warnings))
        _run("plc", lambda d: _upsert_named_rows(db, PLCConnection, d["items"]))
        _run("triggers", lambda d: _upsert_named_rows(db, TriggerChannel, d["items"]))
        _run("export_templates", lambda d: _upsert_named_rows(db, ExportTemplate, d["items"]))
        _run("alarm", lambda d: _write_data_json("alarm_config.json", d))
        _run("sms", lambda d: _write_data_json("sms_config.json", d))
        _run("workstation", lambda d: _apply_workstation(db, d, warnings))

        # 前端 localStorage 侧配置 (显示/检测框样式) 原样回带, 由前端落 localStorage
        display_cfg = _load_config(zf, "display")
        if display_cfg and display_cfg.get("local_settings"):
            report["local_settings"] = display_cfg["local_settings"]

    for domain in report["applied"]:
        todo = DOMAIN_TODOS.get(domain)
        if todo and todo not in report["todos"]:
            report["todos"].append(todo)
    return report
