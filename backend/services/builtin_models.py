# -*- coding: utf-8 -*-
"""内置能力模型入仓 (2026-09, RFC 内置能力模型入仓与能力选用体系).

职责 (本文件是能力目录的唯一事实源):
  1. CAPABILITIES: 能力类型目录 (标签/价值说明/是否有独立权重文件/试用端点);
  2. seed_builtin_models(): 启动幂等登记出厂内置模型行到 models 表
     (文件缺失不建假行, v3.51.4 交付审计教训: 写了代码 ≠ 交付了文件);
  3. 能力绑定: 用户上传新权重后可绑定为某能力的当前权重
     (SystemConfig KV `capability_binding`, 引擎解析顺序 env > 绑定 > 出厂默认)。

设计约束:
  - builtin 行禁删 (API 层守门), 升级安装包覆盖出厂文件不丢用户绑定;
  - 无文件能力 (ocr=pip 内嵌 / anomaly=torchvision 预训练 / vlm=外部 API):
    file_path 存空串, 前端按 no_file 隐藏"更换权重";
  - 老库/无该行为零差异: seed 只 upsert builtin 行, 不碰用户模型。
"""
from __future__ import annotations

import json
import os

WEIGHTS_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "models")

# 能力类型目录: 选用点 (模型页徽标/试用抽屉/项目挂件选择器) 共用
CAPABILITIES = {
    "detect": {
        "label": "目标检测",
        "value": "画框识物, 驱动步骤/计数/区域事件等全部检测状态机 (主模型)",
        "no_file": False,
    },
    "segment": {
        "label": "图像分割",
        "value": "像素级轮廓, 用于形状/覆盖类判定 (主模型)",
        "no_file": False,
    },
    "pose": {
        "label": "人体姿态",
        "value": "17 关键点骨架 → 人体朝向注入 (facing_dwell 巡检) 与骨架叠加 (能力挂件)",
        "no_file": False,
    },
    "headpose": {
        "label": "头部朝向",
        "value": "头姿三轴角精化人体朝向 (叠加在姿态之上, 近景/主码流场景, 能力挂件)",
        "no_file": False,
    },
    "ocr": {
        "label": "文字识别",
        "value": "读铭牌/序列号/屏幕数值, 区域定时读数 (能力挂件)",
        "no_file": True,   # rapidocr pip 包内嵌模型
    },
    "anomaly": {
        "label": "异常检测",
        "value": "合格品记忆库比对, 无 NG 样本冷启动 (anomaly 逻辑模式/能力挂件)",
        "no_file": True,   # torchvision 预训练骨干 + 数据目录记忆库
    },
    "vlm": {
        "label": "看图问答",
        "value": "大模型看图诊断 (外部 API, 系统设置配置密钥)",
        "no_file": True,
    },
}

# 出厂内置模型行 (meta.builtin_key 幂等锚点)
BUILTIN_MODELS = [
    {
        "key": "builtin_person_detect",
        "name": "通用检人 (YOLO11n)",
        "capability": "detect",
        "file": "yolo11n.pt",
        "framework": "PyTorch",
        "description": "COCO 预训练通用检测 (person 等 80 类), 巡检/朝向驻留类项目的主模型即拿即用。",
    },
    {
        "key": "builtin_pose",
        "name": "人体朝向 (YOLO11n-pose)",
        "capability": "pose",
        "file": "yolo11n-pose.pt",
        "framework": "PyTorch",
        "description": "COCO 17 关键点姿态, 朝向估计引擎首选后端 (facing_dwell 巡检规则的朝向来源)。",
    },
    {
        "key": "builtin_headpose",
        "name": "头姿精化 (6DRepNet360)",
        "capability": "headpose",
        "file": "headpose.onnx",
        "framework": "ONNX",
        "description": "头部三轴角估计, 叠加在姿态之上精化朝向。授权已谈定；现场用「更换权重」绑定授权版, 引擎热重载。",
        "meta_extra": {"license_note": "licensed"},
    },
    {
        "key": "builtin_ocr",
        "name": "OCR 读字 (RapidOCR)",
        "capability": "ocr",
        "file": None,
        "framework": "ONNX",
        "description": "PP-OCR 系文字识别 (pip 包内嵌模型, 无独立权重文件), CPU 运行不抢检测 GPU。",
    },
    {
        "key": "builtin_anomaly",
        "name": "异常检测 (记忆库比对)",
        "capability": "anomaly",
        "file": None,
        "framework": "PyTorch",
        "description": "合格品特征记忆库 + 距离比对, 只喂 OK 样本即可上线; 记忆库按项目存数据目录。",
    },
    {
        "key": "builtin_vlm",
        "name": "VLM 看图问答",
        "capability": "vlm",
        "file": None,
        "framework": "External",
        "description": "外部多模态大模型看图诊断; API 地址与密钥在本行「配置」或系统设置维护。",
    },
]

_BINDING_KEY = "capability_binding"
# 有独立权重且引擎支持热换的能力 (绑定 API 白名单)
BINDABLE_CAPABILITIES = ("pose", "headpose")


def seed_builtin_models(db=None) -> int:
    """幂等登记内置模型行。返回本次新建/修复的行数。

    幂等锚点: meta.builtin_key (不是 name, 用户可改显示名不破锚)。
    文件型能力出厂文件缺失时: 已有行标 status='missing', 无行不新建。
    """
    from backend.db.database import SessionLocal
    from backend.models.models import Model

    own = db is None
    if own:
        db = SessionLocal()
    changed = 0
    try:
        rows = db.query(Model).filter(Model.builtin.is_(True)).all()
        by_key = {}
        for r in rows:
            key = (r.meta or {}).get("builtin_key") if isinstance(r.meta, dict) else None
            if key:
                by_key[key] = r
        for spec in BUILTIN_MODELS:
            path = ""
            size = 0
            status = "idle"
            if spec["file"]:
                abs_path = os.path.abspath(os.path.join(WEIGHTS_DIR, spec["file"]))
                if os.path.isfile(abs_path):
                    path, size = abs_path, os.path.getsize(abs_path)
                else:
                    status = "missing"
            row = by_key.get(spec["key"])
            if row is None:
                if status == "missing":
                    # 出厂文件缺失 → 不建假行 (交付审计: 行的存在必须有真身背书)
                    print(f"[BuiltinModels] 出厂权重缺失, 跳过登记: {spec['file']}")
                    continue
                meta = {"builtin_key": spec["key"],
                        "no_file": spec["file"] is None}
                meta.update(spec.get("meta_extra") or {})
                row = Model(
                    name=spec["name"], capability=spec["capability"],
                    builtin=True, source="factory",
                    file_path=path, file_name=spec["file"] or "",
                    file_size=size, framework=spec["framework"],
                    description=spec["description"], meta=meta, status=status,
                )
                db.add(row)
                changed += 1
                print(f"[BuiltinModels] 登记内置模型: {spec['name']}")
            else:
                # 修复: 文件路径漂移 (安装目录变更) / 缺失状态翻转
                fix = False
                if spec["file"] and path and row.file_path != path:
                    row.file_path, row.file_size = path, size
                    fix = True
                if spec["file"] and not path and row.status != "missing":
                    row.status = "missing"
                    fix = True
                if spec["file"] and path and row.status == "missing":
                    row.status = "idle"
                    fix = True
                if row.capability != spec["capability"]:
                    row.capability = spec["capability"]
                    fix = True
                if fix:
                    changed += 1
        if changed:
            db.commit()
        return changed
    except Exception as e:
        db.rollback()
        print(f"[BuiltinModels] seed 失败 (不阻塞启动): {e}")
        return 0
    finally:
        if own:
            db.close()


# ---------------- 能力绑定 (SystemConfig KV) ----------------

def _read_binding_map(db) -> dict:
    from backend.models.models import SystemConfig
    row = db.query(SystemConfig).filter(SystemConfig.key == _BINDING_KEY).first()
    if row is None or not row.value:
        return {}
    try:
        v = json.loads(row.value)  # value 是 Text 列, 存 JSON 字符串
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def get_capability_binding(db=None) -> dict:
    """{capability: model_id} 当前绑定表 (只含用户显式绑定过的能力)。"""
    from backend.db.database import SessionLocal
    own = db is None
    if own:
        db = SessionLocal()
    try:
        return _read_binding_map(db)
    finally:
        if own:
            db.close()


def set_capability_binding(capability: str, model_id, db) -> dict:
    """绑定/解绑 (model_id=None 恢复出厂默认)。调用方负责校验与引擎 reload。"""
    from backend.models.models import SystemConfig
    m = _read_binding_map(db)
    if model_id is None:
        m.pop(capability, None)
    else:
        m[capability] = int(model_id)
    row = db.query(SystemConfig).filter(SystemConfig.key == _BINDING_KEY).first()
    payload = json.dumps(m, ensure_ascii=False)
    if row is None:
        row = SystemConfig(key=_BINDING_KEY, value=payload,
                           description="能力权重绑定 (capability -> model_id)")
        db.add(row)
    else:
        row.value = payload
    db.commit()
    return m


def resolve_capability_weight(capability: str) -> str | None:
    """按 '用户绑定 > 出厂内置行' 解析能力权重文件路径 (env 覆盖由引擎侧负责)。

    任何异常返回 None (引擎回落出厂默认路径), 绝不向调用方抛错 —— 该函数
    会在推理链路上被调用, 错误隔离是底线。
    """
    try:
        from backend.db.database import SessionLocal
        from backend.models.models import Model
        db = SessionLocal()
        try:
            binding = _read_binding_map(db)
            mid = binding.get(capability)
            if mid:
                row = db.query(Model).filter(Model.id == int(mid)).first()
                if row is not None and row.file_path and os.path.isfile(row.file_path):
                    return row.file_path
            row = (db.query(Model)
                   .filter(Model.builtin.is_(True), Model.capability == capability)
                   .first())
            if row is not None and row.file_path and os.path.isfile(row.file_path):
                return row.file_path
            return None
        finally:
            db.close()
    except Exception:
        return None
