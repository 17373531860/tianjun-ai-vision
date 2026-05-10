"""baseline_v3_7_0

Revision ID: 24693b55b51d
Revises:
Create Date: 2026-05-11 00:40:14

v3.7.0 PostgreSQL baseline。
直接用 Base.metadata.create_all() 让 SQLAlchemy 自己按外键依赖顺序建表，
比 autogenerate 写死的顺序更可靠（避免循环引用导致先建子表的问题）。

后续业务字段变更走常规 alembic revision --autogenerate。
"""
from typing import Sequence, Union

from alembic import op


revision: str = "24693b55b51d"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from backend.db.database import Base
    from backend.models import models  # noqa: F401
    from backend.models import export_models  # noqa: F401
    from backend.models import mes_models  # noqa: F401
    from backend.models import plugin_models  # noqa: F401

    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    from backend.db.database import Base
    from backend.models import models  # noqa: F401
    from backend.models import export_models  # noqa: F401
    from backend.models import mes_models  # noqa: F401
    from backend.models import plugin_models  # noqa: F401

    Base.metadata.drop_all(bind=op.get_bind())
