-- 容器初始化时执行（仅在数据卷为空时运行一次）。
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS btree_gin;
