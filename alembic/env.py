"""Alembic 运行时入口（与项目共享 SQLAlchemy 元数据/DSN）。"""
from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# 让 backend.* 可导入
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# 加载所有 ORM 模块以填充 Base.metadata
from backend.db.database import Base  # noqa: E402
from backend.models import models  # noqa: F401, E402
from backend.models import export_models  # noqa: F401, E402
from backend.models import mes_models  # noqa: F401, E402
from backend.models import plugin_models  # noqa: F401, E402
from backend.models import auth_models  # noqa: F401, E402  users 表 (detection_sessions.operator_id FK)
from backend.models import notify_models  # noqa: F401, E402
from backend.models import weighing_models  # noqa: F401, E402
from backend.models import plc_models  # noqa: F401, E402
from backend.models import trigger_models  # noqa: F401, E402
from backend.models import archive_models  # noqa: F401, E402

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 优先使用环境变量 DATABASE_URL；回退到 alembic.ini 里的 sqlalchemy.url
_DSN = os.environ.get("DATABASE_URL", "").strip() or config.get_main_option("sqlalchemy.url")
if not _DSN:
    raise RuntimeError("Alembic 需要 DATABASE_URL 环境变量或 alembic.ini sqlalchemy.url")
# configparser 用 % 作插值，需要把 DSN 里的 % 转义为 %%
config.set_main_option("sqlalchemy.url", _DSN.replace("%", "%%"))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=_DSN,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_schemas=False,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
