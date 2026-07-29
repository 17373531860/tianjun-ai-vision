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


# ---------- 达梦大整数绑定降级 (2026-07 萍乡百斯特现场: 毫秒时间戳溢出) ----------
def test_dm_bind_safe():
    from backend.services.mes_adapters.database_adapter import _dm_bind_safe
    assert _dm_bind_safe(1753247000123) == "1753247000123"   # 毫秒时间戳 → 字符串
    assert _dm_bind_safe(-1753247000123) == "-1753247000123"
    assert _dm_bind_safe(12345) == 12345                     # 32 位内整数原样
    assert _dm_bind_safe(0.5) == 0.5                         # 浮点不动
    assert _dm_bind_safe(True) is True                       # bool 不降级
    assert _dm_bind_safe("x") == "x"


# ---------- 端口合法性守门 (2026-07 萍乡现场: 端口框粘贴出超长数字) ----------
def test_invalid_port_clear_error():
    ad = DatabaseAdapter()
    # 0/未填走"回退默认端口"旧语义, 不在守门范围
    for bad_port in (152361523615236, -1, 70000):
        resp = ad.send({"SN": "x"}, {"db_type": "dm", "host": "127.0.0.1",
                                     "db_port": bad_port, "table": "T"})
        assert resp["success"] is False
        assert "端口配置无效" in resp["error"], \
            f"port={bad_port} 应报端口配置无效, 实际: {resp['error']}"


def test_dm_path_downgrades_big_int(db_path, monkeypatch):
    """db_type=dm 时超 32 位整数应降级为字符串绑定 (用 SQLite 顶替达梦连接验证)。"""
    ad = DatabaseAdapter()

    def fake_connect(config):
        return sqlite3.connect(db_path), "?"

    monkeypatch.setattr(ad, "_connect", fake_connect)
    cfg = {"db_type": "dm", "table": "T_WEIGH_RECORD"}
    # 大整数放 TEXT 亲和列 (SN) 断言绑定值确为字符串; REAL 列会被 SQLite 亲和性转回数字
    resp = ad.send({"SN": 1753247000123, "MODEL_NAME": "M", "OPERATOR": "op",
                    "NET_WEIGHT": 0.5, "VERDICT": "ok"}, cfg)
    assert resp["success"] is True
    row = _rows(db_path)[0]
    assert row[0] == "1753247000123"     # 大整数已降级为字符串绑定入库
    assert row[3] == 0.5                 # 正常数值不受影响
