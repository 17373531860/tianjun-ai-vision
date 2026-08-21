"""v3.54 检测主页自定义布局存储 API: /system/monitor-layouts 读写删 + 校验护栏。

覆盖:
- 空态 GET / PUT 后 GET 回读 / 按形态删除 / 全部删除
- form_key 与 slot id 合法性校验
- 坐标钳制 (w/h 最小 1%, 越界收敛) 与 NaN/类型拒收
- 坏 JSON 存量数据不影响其他形态读取 (渲染兜底)
"""
import json

import pytest

from backend.db.database import SessionLocal
from backend.models.models import SystemConfig

PREFIX = "monitor_layout."


@pytest.fixture
def clean_layouts():
    def _purge():
        s = SessionLocal()
        s.query(SystemConfig).filter(
            SystemConfig.key.like(PREFIX + "%")).delete(synchronize_session=False)
        s.commit()
        s.close()
    _purge()
    yield
    _purge()


def _layout(slots=None, **kw):
    return {
        "version": 1,
        "snap": True,
        "slots": slots or {"video": {"x": 0, "y": 0, "w": 0.6, "h": 0.5}},
        **kw,
    }


def test_get_empty(client, clean_layouts):
    r = client.get("/api/v1/system/monitor-layouts")
    assert r.status_code == 200
    assert r.json() == {"layouts": {}}


def test_put_get_roundtrip(client, clean_layouts):
    layout = _layout(slots={
        "video": {"x": 0, "y": 0, "w": 0.6, "h": 0.5, "z": 1},
        "mes-bar@0": {"x": 0.6, "y": 0, "w": 0.4, "h": 0.1},
    })
    r = client.put("/api/v1/system/monitor-layouts/single:sequential", json=layout)
    assert r.status_code == 200
    assert r.json()["slot_count"] == 2

    g = client.get("/api/v1/system/monitor-layouts").json()["layouts"]
    assert "single:sequential" in g
    saved = g["single:sequential"]
    assert saved["version"] == 1
    assert saved["snap"] is True
    assert saved["slots"]["mes-bar@0"]["x"] == 0.6
    assert saved["slots"]["video"]["z"] == 1


def test_put_overwrites(client, clean_layouts):
    client.put("/api/v1/system/monitor-layouts/dual", json=_layout())
    client.put("/api/v1/system/monitor-layouts/dual", json=_layout(
        slots={"sop": {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4}}))
    g = client.get("/api/v1/system/monitor-layouts").json()["layouts"]["dual"]
    assert list(g["slots"]) == ["sop"]


def test_delete_single_form(client, clean_layouts):
    client.put("/api/v1/system/monitor-layouts/dual", json=_layout())
    client.put("/api/v1/system/monitor-layouts/triple", json=_layout())
    r = client.delete("/api/v1/system/monitor-layouts/dual")
    assert r.status_code == 200 and r.json()["deleted"] == 1
    g = client.get("/api/v1/system/monitor-layouts").json()["layouts"]
    assert "dual" not in g and "triple" in g
    # 幂等: 再删返回 ok
    assert client.delete("/api/v1/system/monitor-layouts/dual").json()["deleted"] == 0


def test_delete_all_forms(client, clean_layouts):
    client.put("/api/v1/system/monitor-layouts/dual", json=_layout())
    client.put("/api/v1/system/monitor-layouts/zoom", json=_layout())
    r = client.delete("/api/v1/system/monitor-layouts")
    assert r.status_code == 200 and r.json()["deleted"] == 2
    assert client.get("/api/v1/system/monitor-layouts").json()["layouts"] == {}


@pytest.mark.parametrize("bad_key", [
    "Single", "single sequential", "a" * 65, "single:a:b", "../etc", "single:序列"])
def test_rejects_bad_form_key(client, clean_layouts, bad_key):
    r = client.put(f"/api/v1/system/monitor-layouts/{bad_key}", json=_layout())
    # ../etc 会被路由层直接 404 (路径归一化), 其余走 400 校验; 都算拒收
    assert r.status_code in (400, 404)


@pytest.mark.parametrize("bad_slots", [
    {"BAD ID": {"x": 0, "y": 0, "w": 0.5, "h": 0.5}},          # 大写/空格
    {"video@9999": {"x": 0, "y": 0, "w": 0.5, "h": 0.5}},      # 通道号超 3 位
    {"video": {"x": 0, "y": 0, "w": 0.5}},                     # 缺 h
    {"video": {"x": "0", "y": 0, "w": 0.5, "h": 0.5}},         # 类型错
    {"video": {"x": float("nan"), "y": 0, "w": 0.5, "h": 0.5}},  # NaN
    {"video": {"x": 0, "y": 0, "w": 0.5, "h": 0.5, "z": -1}},  # z 越界
])
def test_rejects_bad_slots(client, clean_layouts, bad_slots):
    # NaN 过不了 json 序列化, 用 data= 原文直发
    body = json.dumps(_layout(slots=bad_slots), allow_nan=True)
    r = client.put("/api/v1/system/monitor-layouts/dual", data=body,
                   headers={"Content-Type": "application/json"})
    assert r.status_code in (400, 422)


def test_missing_version_rejected(client, clean_layouts):
    r = client.put("/api/v1/system/monitor-layouts/dual",
                   json={"slots": {"video": {"x": 0, "y": 0, "w": 0.5, "h": 0.5}}})
    assert r.status_code == 400


def test_coords_clamped(client, clean_layouts):
    client.put("/api/v1/system/monitor-layouts/dual", json=_layout(slots={
        "video": {"x": -0.5, "y": 2.0, "w": 0.001, "h": 5.0},
    }))
    g = client.get("/api/v1/system/monitor-layouts").json()["layouts"]["dual"]
    rect = g["slots"]["video"]
    assert rect["x"] == 0.0          # 负值钳到 0
    assert rect["y"] == 1.0          # 越界钳到 1
    assert rect["w"] == 0.01         # 最小 1%, 拖不没
    assert rect["h"] == 1.0


def test_corrupt_row_skipped_not_fatal(client, clean_layouts):
    """坏 JSON 存量行只跳过自己, 不能拖垮整个 GET (主页渲染兜底)。"""
    client.put("/api/v1/system/monitor-layouts/triple", json=_layout())
    s = SessionLocal()
    s.add(SystemConfig(key=PREFIX + "dual", value="{not json"))
    s.commit()
    s.close()
    g = client.get("/api/v1/system/monitor-layouts")
    assert g.status_code == 200
    layouts = g.json()["layouts"]
    assert "triple" in layouts and "dual" not in layouts


def test_permission_registered():
    from backend.core.permissions import get_permission_catalog
    keys = {p["key"] for p in get_permission_catalog()}
    assert "monitor.layout.edit" in keys
