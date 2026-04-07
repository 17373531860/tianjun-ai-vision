"""
操作员管理 API

CRUD + 当前操作员设置/获取
"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
from backend.db.database import SessionLocal
from backend.models.models import Operator

router = APIRouter(prefix="/operators", tags=["Operators"])

_current_operator: dict[int, int] = {}


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
            return {"success": True, "channel_id": body.channel_id, "operator": _serialize(op)}
        finally:
            db.close()
    else:
        _current_operator.pop(body.channel_id, None)
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
    return _current_operator.get(channel_id)
