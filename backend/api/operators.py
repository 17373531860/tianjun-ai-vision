"""
操作员管理 API — v3.10.0 阶段 5 已彻底清空

历史: v3.9 之前的"操作员"系统已被新版"用户/角色"系统接管 (新表 users / roles).

本模块当前仅保留 6 个 410 Gone 端点, 为老客户脚本提供清晰的迁移提示.
ORM 类 Operator / current_operator.json 落盘 / get_current_operator_id() 工具函数
全部在阶段 5 删除. operators 表也由 main.py:migrate_database 升级时 DROP.

老客户机器上的 backend/current_operator.json 文件 (如有) 仍然存在但已无人读, 属
僵尸文件; 我们不主动删除以避免误碰客户数据 — 阶段 5 之后任何路径都不再触碰它.

迁移路径 (供副机 / 客户脚本作者参考):
| 旧调用 (410)                       | 新调用                                       |
|-----------------------------------|---------------------------------------------|
| GET    /api/v1/operators          | GET    /api/v1/users                        |
| POST   /api/v1/operators          | POST   /api/v1/users                        |
| PUT    /api/v1/operators/{id}     | PUT    /api/v1/users/{id}                   |
| DELETE /api/v1/operators/{id}     | DELETE /api/v1/users/{id}                   |
| POST   /api/v1/operators/set-current | 由 Token 机制接管 (POST /api/v1/auth/login)|
| GET    /api/v1/operators/current  | GET    /api/v1/auth/me                      |
"""
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/operators", tags=["Operators (Deprecated)"])

# === 410 Gone 响应 ===
_GONE_HEADERS = {"X-Deprecated-Replacement": "/api/v1/users"}
_GONE_DETAIL = (
    "此端点已弃用 (v3.10+ 用户系统接管). "
    "请改用 /api/v1/users / /api/v1/auth/* — 详见响应头 X-Deprecated-Replacement."
)


def _gone():
    raise HTTPException(
        status_code=410,
        detail=_GONE_DETAIL,
        headers=_GONE_HEADERS,
    )


# ============================================================
# 6 个 HTTP 端点 — 全部 410 Gone (v3.10+)
# 调用方拿到 410 应迁到 /api/v1/users / /api/v1/auth/*.
# ============================================================
@router.get("")
def list_operators():
    _gone()


@router.post("")
def create_operator():
    _gone()


@router.put("/{op_id}")
def update_operator(op_id: int):
    _gone()


@router.delete("/{op_id}")
def delete_operator(op_id: int):
    _gone()


@router.post("/set-current")
def set_current_operator():
    _gone()


@router.get("/current")
def get_current_operator(channel_id: int = 0):
    _gone()
