"""
操作员管理 API

CRUD + 当前操作员设置/获取
"""
import json
import os
import threading
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from backend.db.database import SessionLocal
from backend.core.config import DATA_DIR
from backend.models.models import Operator

router = APIRouter(prefix="/operators", tags=["Operators"])

# === 当前操作员持久化 ===
# 之前 _current_operator 是模块级 dict, 重启即丢, 客户每次开机都要重新选人.
# 改为 backend/data/current_operator.json 落盘, 每次 set/clear 立即写入.
_OP_STATE_PATH = os.path.join(DATA_DIR, "current_operator.json")
_op_state_lock = threading.Lock()


def _load_current_operator() -> dict:
    """启动时从磁盘恢复 {channel_id: operator_id}"""
    try:
        if not os.path.exists(_OP_STATE_PATH):
            return {}
        with open(_OP_STATE_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f) or {}
        # JSON key 强制为 str, 转回 int
        return {int(k): int(v) for k, v in raw.items() if v is not None}
    except Exception as _e:
        print(f"[Operator] 当前操作员状态加载失败（忽略，按空处理）: {_e}", flush=True)
        return {}


def _save_current_operator():
    """把 _current_operator 写盘, 失败不阻断业务"""
    try:
        os.makedirs(os.path.dirname(_OP_STATE_PATH), exist_ok=True)
        with _op_state_lock:
            tmp = _OP_STATE_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({str(k): v for k, v in _current_operator.items()}, f)
            os.replace(tmp, _OP_STATE_PATH)
    except Exception as _e:
        print(f"[Operator] 当前操作员状态保存失败（忽略）: {_e}", flush=True)


_current_operator: dict[int, int] = _load_current_operator()


class OperatorCreate(BaseModel):
    name: str
    employee_no: str
    role: str = "operator"

class OperatorUpdate(BaseModel):
    name: Optional[str] = None
    employee_no: Optional[str] = None
    role: Optional[str] = None
    active: Optional[bool] = None

class SetCurrent(BaseModel):
    channel_id: int = 0
    operator_id: Optional[int] = None


def _serialize(op):
    return {
        "id": op.id,
        "name": op.name,
        "employee_no": op.employee_no,
        "role": op.role,
        "active": op.active,
        "created_at": op.created_at.isoformat() if op.created_at else None,
    }


@router.get("")
def list_operators(active: Optional[bool] = None):
    db = SessionLocal()
    try:
        q = db.query(Operator)
        if active is not None:
            q = q.filter(Operator.active == active)
        items = q.order_by(Operator.id).all()
        return [_serialize(op) for op in items]
    finally:
        db.close()


@router.post("")
def create_operator(body: OperatorCreate):
    db = SessionLocal()
    try:
        existing = db.query(Operator).filter(Operator.employee_no == body.employee_no).first()
        if existing:
            raise HTTPException(400, f"工号 {body.employee_no} 已存在")
        op = Operator(name=body.name, employee_no=body.employee_no, role=body.role)
        db.add(op)
        db.commit()
        db.refresh(op)
        return _serialize(op)
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(400, str(e))
    finally:
        db.close()


@router.put("/{op_id}")
def update_operator(op_id: int, body: OperatorUpdate):
    db = SessionLocal()
    try:
        op = db.query(Operator).filter(Operator.id == op_id).first()
        if not op:
            raise HTTPException(404, "操作员不存在")
        data = body.model_dump(exclude_unset=True)
        if "employee_no" in data:
            dup = db.query(Operator).filter(
                Operator.employee_no == data["employee_no"],
                Operator.id != op_id
            ).first()
            if dup:
                raise HTTPException(400, f"工号 {data['employee_no']} 已被使用")
        for field, val in data.items():
            setattr(op, field, val)
        db.commit()
        db.refresh(op)
        return _serialize(op)
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(400, str(e))
    finally:
        db.close()


@router.delete("/{op_id}")
def delete_operator(op_id: int):
    """软删除: 设 active=False"""
    db = SessionLocal()
    try:
        op = db.query(Operator).filter(Operator.id == op_id).first()
        if not op:
            raise HTTPException(404, "操作员不存在")
        op.active = False
        db.commit()
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(400, str(e))
    finally:
        db.close()


@router.post("/set-current")
def set_current_operator(body: SetCurrent):
    """设置当前工位的操作员"""
    if body.operator_id is not None:
        db = SessionLocal()
        try:
            op = db.query(Operator).filter(Operator.id == body.operator_id).first()
            if not op:
                raise HTTPException(404, "操作员不存在")
            _current_operator[body.channel_id] = body.operator_id
            _save_current_operator()
            return {"success": True, "channel_id": body.channel_id, "operator": _serialize(op)}
        finally:
            db.close()
    else:
        _current_operator.pop(body.channel_id, None)
        _save_current_operator()
        return {"success": True, "channel_id": body.channel_id, "operator": None}


@router.get("/current")
def get_current_operator(channel_id: int = 0):
    op_id = _current_operator.get(channel_id)
    if not op_id:
        return {"channel_id": channel_id, "operator": None}
    db = SessionLocal()
    try:
        op = db.query(Operator).filter(Operator.id == op_id).first()
        return {"channel_id": channel_id, "operator": _serialize(op) if op else None}
    finally:
        db.close()


def get_current_operator_id(channel_id: int = 0) -> Optional[int]:
    """供检测引擎内部调用"""
    op_id = _current_operator.get(channel_id)
    if op_id is None:
        return None
    # 防御: 如果磁盘里的 op_id 在 DB 中已被删除/置非 active, 应当返回 None 并清掉脏值
    db = SessionLocal()
    try:
        op = db.query(Operator).filter(Operator.id == op_id, Operator.active == True).first()  # noqa: E712
        if op is None:
            _current_operator.pop(channel_id, None)
            _save_current_operator()
            return None
        return op_id
    finally:
        db.close()
