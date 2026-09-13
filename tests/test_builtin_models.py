# -*- coding: utf-8 -*-
"""内置能力模型入仓 (2026-09) 测试。

覆盖 (RFC 内置能力模型入仓与能力选用体系):
  - seed 幂等: 重复跑不重复建行; 出厂文件缺失不建假行;
  - 能力绑定 KV: set/get + resolve 顺序 (绑定 > 出厂内置);
  - API: /models/capabilities 目录 / bind 校验 / builtin 禁删 /
         upload capability 校验 / list capability 过滤;
  - 能力挂件配置解析 (parse_capability_attachments 纯函数)。
"""
from __future__ import annotations

import io
import os

import pytest

from backend.services import builtin_models as bm


@pytest.fixture()
def db():
    from backend.db.database import SessionLocal
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _reset_builtin(db):
    from backend.models.models import Model, SystemConfig
    db.query(Model).filter(Model.builtin.is_(True)).delete(
        synchronize_session=False)
    db.query(SystemConfig).filter(
        SystemConfig.key == "capability_binding").delete(synchronize_session=False)
    db.commit()


# ============================================================
# seed
# ============================================================

class TestSeed:
    def test_seed_idempotent(self, db):
        _reset_builtin(db)
        n1 = bm.seed_builtin_models(db)
        assert n1 >= 3  # 至少 no_file 三行 (ocr/anomaly/vlm) + 仓内有真文件的行
        from backend.models.models import Model
        count1 = db.query(Model).filter(Model.builtin.is_(True)).count()
        n2 = bm.seed_builtin_models(db)
        count2 = db.query(Model).filter(Model.builtin.is_(True)).count()
        assert n2 == 0 and count1 == count2  # 幂等: 二跑零变更零新行

    def test_file_rows_have_real_files(self, db):
        """文件型内置行的 file_path 必须指向真实存在的文件 (交付审计口径)。"""
        _reset_builtin(db)
        bm.seed_builtin_models(db)
        from backend.models.models import Model
        for row in db.query(Model).filter(Model.builtin.is_(True)).all():
            meta = row.meta or {}
            if not meta.get("no_file"):
                assert row.file_path and os.path.isfile(row.file_path), \
                    f"{row.name} 文件缺失: {row.file_path}"
            else:
                assert row.file_path == ""

    def test_missing_weights_no_fake_rows(self, db, monkeypatch, tmp_path):
        """出厂权重目录空 → 文件型能力不建行, 无文件能力照常建。"""
        _reset_builtin(db)
        monkeypatch.setattr(bm, "WEIGHTS_DIR", str(tmp_path))
        bm.seed_builtin_models(db)
        from backend.models.models import Model
        rows = db.query(Model).filter(Model.builtin.is_(True)).all()
        caps = {r.capability for r in rows}
        assert caps == {"ocr", "anomaly", "vlm"}

    def test_no_file_rows_meta(self, db):
        _reset_builtin(db)
        bm.seed_builtin_models(db)
        from backend.models.models import Model
        vlm = (db.query(Model)
               .filter(Model.builtin.is_(True), Model.capability == "vlm").first())
        assert vlm is not None
        assert (vlm.meta or {}).get("no_file") is True
        assert (vlm.meta or {}).get("builtin_key") == "builtin_vlm"


# ============================================================
# 能力绑定 + 权重解析
# ============================================================

class TestBinding:
    def test_bind_resolve_order(self, db, tmp_path):
        """resolve: 用户绑定 > 出厂内置; 解绑回落内置。"""
        _reset_builtin(db)
        bm.seed_builtin_models(db)
        from backend.models.models import Model
        fake = tmp_path / "user_pose.pt"
        fake.write_bytes(b"fake")
        user_row = Model(name="用户姿态模型", capability="pose", builtin=False,
                         file_path=str(fake), file_name="user_pose.pt",
                         file_size=4, framework="PyTorch")
        db.add(user_row)
        db.commit()

        builtin_path = bm.resolve_capability_weight("pose")
        assert builtin_path and builtin_path.endswith("yolo11n-pose.pt")

        bm.set_capability_binding("pose", user_row.id, db)
        assert bm.resolve_capability_weight("pose") == str(fake)
        assert bm.get_capability_binding(db) == {"pose": user_row.id}

        bm.set_capability_binding("pose", None, db)  # 恢复出厂
        assert bm.resolve_capability_weight("pose") == builtin_path

    def test_bound_file_gone_falls_back(self, db, tmp_path):
        """绑定的文件被删 → 回落出厂内置, 不抛错。"""
        _reset_builtin(db)
        bm.seed_builtin_models(db)
        from backend.models.models import Model
        gone = tmp_path / "gone.pt"
        gone.write_bytes(b"x")
        row = Model(name="悬空", capability="pose", builtin=False,
                    file_path=str(gone), file_name="gone.pt",
                    file_size=1, framework="PyTorch")
        db.add(row)
        db.commit()
        bm.set_capability_binding("pose", row.id, db)
        gone.unlink()
        p = bm.resolve_capability_weight("pose")
        assert p and p.endswith("yolo11n-pose.pt")


# ============================================================
# API 面
# ============================================================

class TestApi:
    @pytest.fixture(autouse=True)
    def _seed(self, db):
        _reset_builtin(db)
        bm.seed_builtin_models(db)

    def test_capability_catalog(self, client):
        r = client.get("/api/v1/models/capabilities")
        assert r.status_code == 200
        items = {it["capability"]: it for it in r.json()["items"]}
        assert set(items) == set(bm.CAPABILITIES)
        assert items["pose"]["bindable"] is True
        assert items["ocr"]["bindable"] is False
        assert items["pose"]["builtin_model_id"] is not None
        assert items["vlm"]["no_file"] is True

    def test_builtin_delete_forbidden(self, client, db):
        from backend.models.models import Model
        row = db.query(Model).filter(Model.builtin.is_(True)).first()
        r = client.delete(f"/api/v1/models/{row.id}")
        assert r.status_code == 403
        db.expire_all()
        assert db.query(Model).filter(Model.id == row.id).count() == 1

    def test_bind_validation(self, client, db, tmp_path):
        # 不可绑能力
        r = client.post("/api/v1/models/capabilities/ocr/bind", json={"model_id": 1})
        assert r.status_code == 400
        # 模型不存在
        r = client.post("/api/v1/models/capabilities/pose/bind",
                        json={"model_id": 999999})
        assert r.status_code == 404
        # 能力类型不匹配
        from backend.models.models import Model
        f = tmp_path / "d.pt"
        f.write_bytes(b"x")
        det = Model(name="检测模型x", capability="detect", builtin=False,
                    file_path=str(f), file_name="d.pt", file_size=1,
                    framework="PyTorch")
        db.add(det)
        db.commit()
        r = client.post("/api/v1/models/capabilities/pose/bind",
                        json={"model_id": det.id})
        assert r.status_code == 400
        # 正常绑定 + 解绑
        pose = Model(name="姿态模型x", capability="pose", builtin=False,
                     file_path=str(f), file_name="d.pt", file_size=1,
                     framework="PyTorch")
        db.add(pose)
        db.commit()
        r = client.post("/api/v1/models/capabilities/pose/bind",
                        json={"model_id": pose.id})
        assert r.status_code == 200
        assert r.json()["binding"] == {"pose": pose.id}
        r = client.post("/api/v1/models/capabilities/pose/bind",
                        json={"model_id": None})
        assert r.status_code == 200
        assert r.json()["binding"] == {}

    def test_upload_capability_validation(self, client):
        # 无文件能力拒收上传
        r = client.post("/api/v1/models/upload",
                        data={"name": "坏能力", "framework": "PyTorch",
                              "capability": "vlm"},
                        files={"file": ("x.pt", io.BytesIO(b"fake"), "application/octet-stream")})
        assert r.status_code == 400
        # pose 上传入库带 capability
        r = client.post("/api/v1/models/upload",
                        data={"name": "测试姿态上传", "framework": "PyTorch",
                              "capability": "pose"},
                        files={"file": ("p.pt", io.BytesIO(b"fake"), "application/octet-stream")})
        assert r.status_code == 201
        body = r.json()
        assert body["capability"] == "pose" and body["builtin"] is False
        # list capability 过滤
        r = client.get("/api/v1/models", params={"capability": "pose"})
        names = [m["name"] for m in r.json()["items"]]
        assert "测试姿态上传" in names
        assert all(m["capability"] == "pose" for m in r.json()["items"])
        # 清理 (非 builtin 可删)
        client.delete(f"/api/v1/models/{body['id']}")


# ============================================================
# 能力挂件配置解析
# ============================================================

class TestAttachmentsParse:
    def test_parse_normalizes(self):
        from backend.api.source_capability_attachments import (
            parse_capability_attachments)
        atts = parse_capability_attachments({"capability_attachments": [
            {"capability": "pose"},
            {"capability": "ocr", "params": {"roi": [0.1, 0.2, 0.3, 0.4],
                                             "interval_s": 2}},
            {"capability": "anomaly", "params": {"bank_id": "b1", "event_id": 2,
                                                 "cooldown_s": 5}},
            {"capability": "nope"},          # 未知能力丢弃
            "garbage",                        # 坏项丢弃
        ]})
        assert [a["capability"] for a in atts] == ["pose", "ocr", "anomaly"]
        assert atts[0]["interval_s"] == 1.0          # pose 默认 1s
        assert atts[1]["roi"] == [0.1, 0.2, 0.3, 0.4]
        assert atts[2]["bank_id"] == "b1" and atts[2]["cooldown_s"] == 5.0

    def test_parse_empty_zero_config(self):
        from backend.api.source_capability_attachments import (
            parse_capability_attachments)
        assert parse_capability_attachments({}) == []
        assert parse_capability_attachments({"capability_attachments": None}) == []
