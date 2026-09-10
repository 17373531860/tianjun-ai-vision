# ==================== 分域收集: DB / DATA_DIR JSON → 内层 zip payload ====================
# payload 结构 (加密前的内层 zip):
#   manifest.json                 概要 (schema/版本/分域/项目与模型清单) — 导入预览用
#   config/<domain>.json          各分域配置 (已过 sanitize 剥离机器绑定)
#   assets/models/<uuid>_<原名>   模型权重文件 (.pt/.pth/.onnx; TRT 引擎绑 GPU 不带)

import json
import os
import uuid
import zipfile
from datetime import datetime

from sqlalchemy.orm import Session

from backend._version import get_main_version
from backend.core.config import DATA_DIR
from backend.models.export_models import ExportTemplate
from backend.models.mes_models import (
    DefectCode, MESConnection, PackagingFlowConfig,
    ScannerDevice, WorkpieceFlowConfig,
)
from backend.models.models import ChannelGroup, Model, Project, SystemConfig
from backend.models.plc_models import PLCConnection
from backend.models.scan_collect_models import ScanCollectConfig
from backend.models.trigger_models import TriggerChannel
from backend.services.site_pack import sanitize

PACK_SCHEMA = 1
MODEL_FILE_EXTS = {".pt", ".pth", ".onnx"}

# 分域中文名 (导出勾选 UI / 导入预览共用)
DOMAIN_LABELS = {
    "projects": "项目配置",
    "models": "模型权重",
    "workstation": "工位数与项目绑定",
    "monitor_layout": "检测主页自定义布局",
    "display": "显示设置",
    "system_kv": "轮询间隔/日志条数",
    "alarm": "报警设置",
    "sms": "短信/微信通知",
    "defect_codes": "缺陷代码",
    "scanners": "扫码器",
    "scan_collect": "周期多码采集",
    "mes_connections": "MES 网关连接",
    "mes_inbound": "MES 入站接收",
    "channel_groups": "工位组",
    "workpiece_flows": "串行流水线",
    "packaging_flows": "包装箱结算",
    "plc": "PLC 连接",
    "triggers": "触发中心",
    "export_templates": "自定义导出模板",
}
ALL_DOMAINS = list(DOMAIN_LABELS.keys())

_PROJECT_FIELDS = [
    "name", "task_type", "logic_mode", "model_format",
    "pipeline_config", "steps_config", "events_config", "counters_config",
    "alarm_config", "detection_config", "data_config",
]


def _kv_by_prefix(db: Session, *prefixes: str) -> dict:
    out = {}
    for prefix in prefixes:
        rows = db.query(SystemConfig).filter(
            SystemConfig.key.like(prefix + "%")).all()
        for r in rows:
            out[r.key] = r.value
    return out


def _project_name_by_id(db: Session) -> dict:
    return {p.id: p.name for p in db.query(Project).all()}


def _collect_projects(db: Session) -> list:
    model_names = {m.id: {"name": m.name, "version": m.version}
                   for m in db.query(Model).all()}
    items = []
    for p in db.query(Project).all():
        item = {f: getattr(p, f, None) for f in _PROJECT_FIELDS}
        item["default_model_ref"] = model_names.get(p.default_model_id)
        items.append(item)
    return items


def _collect_models(db: Session) -> tuple:
    """返回 (items, assets)。assets = [(zip 内路径, 磁盘源路径)]。"""
    items, assets = [], []
    for m in db.query(Model).all():
        path = m.file_path or ""
        ext = os.path.splitext(path)[1].lower()
        if ext not in MODEL_FILE_EXTS or not os.path.exists(path):
            continue
        arcname = f"assets/models/{uuid.uuid4().hex}_{os.path.basename(m.file_name or path)}"
        items.append({
            "name": m.name, "version": m.version, "framework": m.framework,
            "description": m.description, "labels": m.labels,
            "file_name": m.file_name or os.path.basename(path),
            "file_size": os.path.getsize(path), "arcname": arcname,
        })
        assets.append((arcname, path))
    return items, assets


def _collect_rows(db: Session, orm_cls, sanitizer=None) -> list:
    items = []
    for row in db.query(orm_cls).all():
        data = sanitize.row_to_dict(row)
        if sanitizer:
            data = sanitizer(data)
        items.append(data)
    return items


def _collect_defect_codes(db: Session) -> list:
    names = _project_name_by_id(db)
    items = []
    for row in db.query(DefectCode).all():
        data = sanitize.row_to_dict(row)
        data["project_name_ref"] = names.get(data.pop("project_id", None))
        items.append(data)
    return items


def _collect_scan_collect(db: Session) -> list:
    names = _project_name_by_id(db)
    items = []
    for row in db.query(ScanCollectConfig).all():
        data = sanitize.row_to_dict(row)
        pname = names.get(data.pop("project_id", None))
        if not pname:
            continue  # 绑的项目已不存在, 该配置无意义
        data["project_name_ref"] = pname
        items.append(data)
    return items


def _scanner_name_by_id(db: Session) -> dict:
    return {s.id: s.name for s in db.query(ScannerDevice).all()}


def _collect_workpiece_flows(db: Session) -> list:
    scanner_names = _scanner_name_by_id(db)
    items = []
    for row in db.query(WorkpieceFlowConfig).all():
        data = sanitize.row_to_dict(row)
        data["scanner_name_ref"] = scanner_names.get(data.pop("scan_device_id", None))
        items.append(data)
    return items


def _collect_packaging_flows(db: Session) -> list:
    scanner_names = _scanner_name_by_id(db)
    conn_names = {c.id: c.name for c in db.query(MESConnection).all()}
    items = []
    for row in db.query(PackagingFlowConfig).all():
        data = sanitize.row_to_dict(row)
        data["scanner_name_ref"] = scanner_names.get(data.pop("scan_device_id", None))
        data["mes_conn_name_ref"] = conn_names.get(data.pop("pull_conn_id", None))
        items.append(data)
    return items


def _collect_export_templates(db: Session) -> list:
    # 系统预设由主程序 seed, 不带; 只带用户自建模板
    return [sanitize.row_to_dict(t) for t in
            db.query(ExportTemplate).filter(ExportTemplate.is_system == False).all()]  # noqa: E712


def _read_data_json(filename: str):
    path = os.path.join(DATA_DIR, filename)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _collect_workstation(db: Session) -> dict:
    """只带工位数 + 每工位绑定的项目名。相机参数 (device_index/分辨率/曝光) 不带。"""
    names = _project_name_by_id(db)
    cfg = _read_data_json("workstation_config.json") or {}
    bindings = {}
    for ch_key, ch_cfg in (cfg.get("channels") or {}).items():
        pname = names.get((ch_cfg or {}).get("project_id"))
        if pname:
            bindings[ch_key] = pname
    return {"channel_count": cfg.get("channel_count", 1),
            "project_bindings": bindings}


def collect_domain_overview(db: Session) -> list:
    """导出 UI 用: 每个分域当前有多少条内容, 便于现场人员勾选。"""
    models_items, _ = _collect_models(db)
    counts = {
        "projects": db.query(Project).count(),
        "models": len(models_items),
        "workstation": 1,
        "monitor_layout": len(_kv_by_prefix(db, "monitor_layout.")),
        "display": len(_kv_by_prefix(db, "display.")),
        "system_kv": len(_kv_by_prefix(db, "polling.", "loglimit.")),
        "alarm": 1 if _read_data_json("alarm_config.json") else 0,
        "sms": 1 if _read_data_json("sms_config.json") else 0,
        "defect_codes": db.query(DefectCode).count(),
        "scanners": db.query(ScannerDevice).count(),
        "scan_collect": db.query(ScanCollectConfig).count(),
        "mes_connections": db.query(MESConnection).count(),
        "mes_inbound": len(_kv_by_prefix(db, "mes_inbound_config")),
        "channel_groups": db.query(ChannelGroup).count(),
        "workpiece_flows": db.query(WorkpieceFlowConfig).count(),
        "packaging_flows": db.query(PackagingFlowConfig).count(),
        "plc": db.query(PLCConnection).count(),
        "triggers": db.query(TriggerChannel).count(),
        "export_templates": db.query(ExportTemplate).filter(
            ExportTemplate.is_system == False).count(),  # noqa: E712
    }
    model_bytes = sum(i["file_size"] for i in models_items)
    return [{"key": k, "label": DOMAIN_LABELS[k], "count": counts.get(k, 0),
             "size_bytes": model_bytes if k == "models" else None}
            for k in ALL_DOMAINS]


def build_payload_zip(db: Session, domains: list, zip_path: str,
                      local_settings: dict = None, note: str = "") -> dict:
    """收集勾选分域写成内层 zip (明文), 返回 manifest。"""
    domains = [d for d in ALL_DOMAINS if d in set(domains)]
    config_files = {}
    assets = []

    if "projects" in domains:
        config_files["projects"] = {"items": _collect_projects(db)}
    if "models" in domains:
        items, assets = _collect_models(db)
        config_files["models"] = {"items": items}
    if "workstation" in domains:
        config_files["workstation"] = _collect_workstation(db)
    if "monitor_layout" in domains:
        config_files["monitor_layout"] = {"kv": _kv_by_prefix(db, "monitor_layout.")}
    if "display" in domains:
        config_files["display"] = {"kv": _kv_by_prefix(db, "display."),
                                   "local_settings": local_settings or {}}
    if "system_kv" in domains:
        config_files["system_kv"] = {"kv": _kv_by_prefix(db, "polling.", "loglimit.")}
    if "alarm" in domains:
        data = _read_data_json("alarm_config.json")
        if data is not None:
            config_files["alarm"] = sanitize.sanitize_alarm_config(data)
    if "sms" in domains:
        data = _read_data_json("sms_config.json")
        if data is not None:
            config_files["sms"] = sanitize.sanitize_sms_config(data)
    if "defect_codes" in domains:
        config_files["defect_codes"] = {"items": _collect_defect_codes(db)}
    if "scanners" in domains:
        config_files["scanners"] = {"items": _collect_rows(
            db, ScannerDevice, sanitize.sanitize_scanner)}
    if "scan_collect" in domains:
        config_files["scan_collect"] = {"items": _collect_scan_collect(db)}
    if "mes_connections" in domains:
        config_files["mes_connections"] = {"items": _collect_rows(
            db, MESConnection, sanitize.sanitize_mes_connection)}
    if "mes_inbound" in domains:
        config_files["mes_inbound"] = {"kv": _kv_by_prefix(db, "mes_inbound_config")}
    if "channel_groups" in domains:
        config_files["channel_groups"] = {"items": _collect_rows(db, ChannelGroup)}
    if "workpiece_flows" in domains:
        config_files["workpiece_flows"] = {"items": _collect_workpiece_flows(db)}
    if "packaging_flows" in domains:
        config_files["packaging_flows"] = {"items": _collect_packaging_flows(db)}
    if "plc" in domains:
        config_files["plc"] = {"items": _collect_rows(
            db, PLCConnection, sanitize.sanitize_plc)}
    if "triggers" in domains:
        config_files["triggers"] = {"items": _collect_rows(
            db, TriggerChannel, sanitize.sanitize_trigger)}
    if "export_templates" in domains:
        config_files["export_templates"] = {"items": _collect_export_templates(db)}

    included = [d for d in domains if d in config_files or d == "models"]
    manifest = {
        "schema": PACK_SCHEMA,
        "app_version": get_main_version(),
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "note": note or "",
        "domains": included,
        "domain_labels": {d: DOMAIN_LABELS[d] for d in included},
        "counts": {d: (len(v["items"]) if isinstance(v, dict) and "items" in v
                       else len(v.get("kv", {})) if isinstance(v, dict) and "kv" in v
                       else 1)
                   for d, v in config_files.items()},
        "projects": [i["name"] for i in
                     config_files.get("projects", {}).get("items", [])],
        "models": [{"name": i["name"], "version": i["version"],
                    "file_size": i["file_size"]}
                   for i in config_files.get("models", {}).get("items", [])],
    }

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json",
                    json.dumps(manifest, ensure_ascii=False, indent=2))
        for domain, data in config_files.items():
            zf.writestr(f"config/{domain}.json",
                        json.dumps(data, ensure_ascii=False, indent=2))
        for arcname, src_path in assets:
            # 模型文件已压缩过, 存储级写入省 CPU
            zf.write(src_path, arcname, compress_type=zipfile.ZIP_STORED)
    return manifest
