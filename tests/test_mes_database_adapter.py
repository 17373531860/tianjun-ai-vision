"""v3.35 网关数据库直写适配器单测 (用内置 SQLite 驱动跑真 INSERT, 不依赖外部库)。

达梦/MySQL/PG/SQLServer 路径与 SQLite 只差 _connect 分支 (驱动 + 占位符),
INSERT 组装 / 事务 / 标识符防注入 / 行展开逻辑完全共享, 故 SQLite 即可覆盖核心。
"""
import sqlite3

import pytest

from backend.services.mes_adapters import get_adapter
from backend.services.mes_adapters.database_adapter import DatabaseAdapter, _safe_ident


@pytest.fixture()
def db_path(tmp_path):
    p = str(tmp_path / "mes_mid.db")
    conn = sqlite3.connect(p)
    conn.execute("CREATE TABLE T_WEIGH_RECORD ("
                 "SN TEXT, MODEL_NAME TEXT, OPERATOR TEXT, "
                 "NET_WEIGHT REAL, VERDICT TEXT)")
    conn.commit()
    conn.close()
    return p


def _cfg(db_path, **over):
    cfg = {"db_type": "sqlite", "database": db_path, "table": "T_WEIGH_RECORD"}
    cfg.update(over)
    return cfg


def _rows(db_path):
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT SN, MODEL_NAME, OPERATOR, NET_WEIGHT, VERDICT "
                        "FROM T_WEIGH_RECORD").fetchall()
    conn.close()
    return rows


# ---------- 注册表 ----------
def test_registry_has_database_adapter():
    assert isinstance(get_adapter("database"), DatabaseAdapter)


# ---------- 模板 → 单行 INSERT ----------
def test_send_single_row(db_path):
    ad = DatabaseAdapter()
    cfg = _cfg(db_path, template={
        "SN": "{sn}", "MODEL_NAME": "{model}", "OPERATOR": "{operator}",
        "NET_WEIGHT": "{net}", "VERDICT": "{verdict}",
    })
    ctx = {"sn": "SN001", "model": "型号A", "operator": "张三",
           "net": 0.5, "verdict": "ok"}
    payload = ad.build_payload(ctx, cfg)
    resp = ad.send(payload, cfg)
    assert resp["success"] is True
    assert resp["body"]["inserted"] == 1
    assert ad.check_response(resp, cfg) is True
    assert _rows(db_path) == [("SN001", "型号A", "张三", 0.5, "ok")]


# ---------- _array_source → 逐行 INSERT (同一事务) ----------
def test_send_array_rows(db_path):
    ad = DatabaseAdapter()
    cfg = _cfg(db_path, template={
        "_array_source": "results",
        "_item_template": {
            "SN": "{sn}", "MODEL_NAME": "{model}", "OPERATOR": "{operator}",
            "NET_WEIGHT": "{item.net}", "VERDICT": "{item.verdict}",
        },
    })
    ctx = {"sn": "SN002", "model": "型号A", "operator": "李四",
           "results": [{"net": 0.5, "verdict": "ok"},
                       {"net": 0.25, "verdict": "shortage"}]}
    payload = ad.build_payload(ctx, cfg)
    resp = ad.send(payload, cfg)
    assert resp["success"] is True and resp["body"]["inserted"] == 2
    rows = _rows(db_path)
    assert len(rows) == 2
    assert rows[1] == ("SN002", "型号A", "李四", 0.25, "shortage")


# ---------- 失败路径 ----------
def test_send_empty_template_fails_gracefully(db_path):
    ad = DatabaseAdapter()
    resp = ad.send({}, _cfg(db_path, template={}))
    assert resp["success"] is False
    assert "模板渲染结果为空" in resp["error"]


def test_send_bad_table_rejected(db_path):
    ad = DatabaseAdapter()
    resp = ad.send({"SN": "x"}, _cfg(db_path, table="T_WEIGH; DROP TABLE x"))
    assert resp["success"] is False
    assert "非法表/列名" in resp["error"]
    assert ad.check_response(resp, _cfg(db_path)) is False


def test_send_bad_column_rejected(db_path):
    ad = DatabaseAdapter()
    resp = ad.send({"SN\" ,": "x"}, _cfg(db_path))
    assert resp["success"] is False
    assert "非法表/列名" in resp["error"]


def test_send_missing_table_error(db_path):
    ad = DatabaseAdapter()
    resp = ad.send({"SN": "x"}, _cfg(db_path, table="NOT_EXIST_TABLE"))
    assert resp["success"] is False
    assert "数据库直写失败" in resp["error"]


def test_unknown_db_type_error(db_path):
    ad = DatabaseAdapter()
    resp = ad.send({"SN": "x"}, _cfg(db_path, db_type="oracle"))
    assert resp["success"] is False
    assert "未知数据库类型" in resp["error"]


# ---------- 标识符校验纯函数 ----------
def test_safe_ident():
    assert _safe_ident("T_WEIGH_RECORD") == "T_WEIGH_RECORD"
    assert _safe_ident("PROD.T_WEIGH") == "PROD.T_WEIGH"     # schema 前缀
    for bad in ("", "a b", "a;b", "a'b", 'a"b', "a-b"):
        with pytest.raises(ValueError):
            _safe_ident(bad)
