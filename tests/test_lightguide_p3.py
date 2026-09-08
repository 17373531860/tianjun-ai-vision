"""投影光引导 P3: 引导参数 (/lightguide/params) 全局 KV。

覆盖: 出厂默认 / 部分更新 / 越界钳制 / 类型不合 422 / 持久化回读。
"""

PREFIX = "/api/v1/lightguide"

DEFAULTS = {
    "drift_interval_s": 20,
    "drift_threshold_px": 6.0,
    "auto_recalibrate": True,
    "hover_dwell_ms": 1200,
    "hover_delta": 14.0,
    "hover_poll_ms": 250,
    "calib_settle_ms": 1500,
    "pattern_cols": 4,
    "pattern_rows": 3,
    "brightness": 1.0,
    "flow_path": True,
}


def test_params_default(client):
    r = client.get(f"{PREFIX}/params")
    assert r.status_code == 200
    assert r.json()["params"] == DEFAULTS


def test_params_partial_update_and_roundtrip(client):
    r = client.put(f"{PREFIX}/params",
                   json={"hover_dwell_ms": 2000, "auto_recalibrate": False})
    assert r.status_code == 200
    params = r.json()["params"]
    assert params["hover_dwell_ms"] == 2000
    assert params["auto_recalibrate"] is False
    # 未提及的键保持默认
    assert params["drift_interval_s"] == DEFAULTS["drift_interval_s"]
    assert params["brightness"] == DEFAULTS["brightness"]

    # GET 回读与 PUT 返回一致 (持久化)
    r = client.get(f"{PREFIX}/params")
    assert r.json()["params"] == params

    # 还原, 不留现场
    client.put(f"{PREFIX}/params", json=DEFAULTS)


def test_params_out_of_range_clamped(client):
    r = client.put(f"{PREFIX}/params", json={
        "drift_interval_s": 99999,   # 上限 600
        "drift_threshold_px": 0.1,   # 下限 2
        "brightness": 3.0,           # 上限 1.0
        "pattern_cols": 100,         # 上限 8
    })
    assert r.status_code == 200
    params = r.json()["params"]
    assert params["drift_interval_s"] == 600
    assert params["drift_threshold_px"] == 2.0
    assert params["brightness"] == 1.0
    assert params["pattern_cols"] == 8
    client.put(f"{PREFIX}/params", json=DEFAULTS)


def test_params_type_guard(client):
    # 数值字段给非数字字符串 → pydantic 422
    r = client.put(f"{PREFIX}/params", json={"hover_dwell_ms": "abc"})
    assert r.status_code == 422
    # bool 字段 pydantic 宽松转换 ("yes"→True), 落库仍是规范 bool
    r = client.put(f"{PREFIX}/params", json={"flow_path": "yes"})
    assert r.status_code == 200
    assert r.json()["params"]["flow_path"] is True
    client.put(f"{PREFIX}/params", json=DEFAULTS)


def test_params_int_fields_rounded(client):
    """int 语义字段传小数按四舍五入落库 (前端滑块可能给浮点)。"""
    r = client.put(f"{PREFIX}/params", json={"hover_poll_ms": 250.6})
    assert r.status_code == 200
    assert r.json()["params"]["hover_poll_ms"] == 251
    client.put(f"{PREFIX}/params", json=DEFAULTS)
