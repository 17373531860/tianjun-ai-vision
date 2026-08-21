# -*- coding: utf-8 -*-
"""自定义录像存储位置 (v3.54) 单元测试。

覆盖:
  1. 目录护栏: 空/相对路径/系统盘根拒绝, 与启用中的本地归档目的地互斥
  2. 动态解析: 无配置走默认; 配置后走自定义; 自定义不可用回退默认不丢录像
  3. 扫描根: all_scan_roots 默认+自定义去重
  4. API: GET/PUT 往返 + 校验 400 + 空串恢复默认
  5. 归档护栏联动: 归档目的地指入自定义录像根被拒 (v3.54 扩展)
"""
import os

import pytest

from backend.core.config import settings
from backend.db.database import SessionLocal
from backend.models.models import SystemConfig
from backend.services import recording_storage as rs


def _set_kv(value):
    db = SessionLocal()
    try:
        row = db.query(SystemConfig).filter(
            SystemConfig.key == rs.KV_KEY).first()
        if row:
            row.value = value
        else:
            db.add(SystemConfig(key=rs.KV_KEY, value=value))
        db.commit()
    finally:
        db.close()
    rs.refresh_cache()


@pytest.fixture(autouse=True)
def _clean_kv():
    """每个用例前后清掉配置并刷缓存, 防串扰。"""
    _set_kv("")
    yield
    _set_kv("")


# ============================================================
# 1. 目录护栏
# ============================================================

class TestValidateRecordingDir:
    def test_reject_empty(self):
        assert rs.validate_recording_dir("") is not None
        assert rs.validate_recording_dir("   ") is not None

    def test_reject_relative(self):
        assert "绝对路径" in rs.validate_recording_dir("recordings/custom")

    def test_reject_blacklist_roots(self):
        for bad in ("/", "/etc", "C:\\Windows", "D:"):
            assert rs.validate_recording_dir(bad) is not None, bad

    def test_accept_normal_abs_dir(self, tmp_path):
        target = str(tmp_path / "rec_root")
        assert rs.validate_recording_dir(target) is None
        assert os.path.isdir(target)  # 校验顺手建目录

    def test_reject_nested_with_enabled_archive_dest(self, tmp_path):
        from backend.models.archive_models import VideoArchiveRule
        dest = str(tmp_path / "archive_dest")
        os.makedirs(dest, exist_ok=True)
        db = SessionLocal()
        try:
            rule = VideoArchiveRule(
                name="互斥测试规则", enabled=True, result_filter="all",
                dest_dir=dest, filename_template="{{ cycle.id }}.mp4",
            )
            db.add(rule)
            db.commit()
            # 录像根 = 归档目的地内部 → 拒
            assert rs.validate_recording_dir(
                os.path.join(dest, "sub")) is not None
            # 录像根 = 归档目的地的父目录 → 也拒 (反向嵌套)
            assert rs.validate_recording_dir(str(tmp_path)) is not None
            # 无嵌套关系 → 放行
            other = str(tmp_path.parent / f"{tmp_path.name}_other")
            assert rs.validate_recording_dir(other) is None
            db.delete(rule)
            db.commit()
        finally:
            db.close()


# ============================================================
# 2. 动态解析与回退
# ============================================================

class TestResolution:
    def test_default_without_config(self):
        assert rs.get_recording_root() == settings.RECORDING_DIR
        dirs = rs.get_video_dirs()
        assert dirs["sessions"] == settings.SESSION_VIDEO_DIR
        assert dirs["cycles"] == settings.CYCLE_VIDEO_DIR
        assert dirs["steps"] == settings.STEP_VIDEO_DIR

    def test_custom_root_takes_effect(self, tmp_path):
        custom = str(tmp_path / "big_disk")
        _set_kv(custom)
        assert rs.get_recording_root() == custom
        dirs = rs.get_video_dirs()
        for sub in ("sessions", "cycles", "steps", "cache"):
            assert dirs[sub] == os.path.join(custom, sub)
            assert os.path.isdir(dirs[sub])  # 已自动创建

    def test_unusable_custom_falls_back(self, tmp_path):
        # 把"目录"指到一个普通文件下面 → makedirs 必失败 → 回退默认
        blocker = tmp_path / "im_a_file"
        blocker.write_text("x")
        _set_kv(str(blocker / "sub"))
        assert rs.get_recording_root() == settings.RECORDING_DIR

    def test_all_scan_roots(self, tmp_path):
        assert rs.all_scan_roots() == [settings.RECORDING_DIR]
        custom = str(tmp_path / "scan_root")
        os.makedirs(custom, exist_ok=True)
        _set_kv(custom)
        roots = rs.all_scan_roots()
        assert settings.RECORDING_DIR in roots
        assert os.path.abspath(custom) in [os.path.abspath(r) for r in roots]
        assert len(roots) == 2


# ============================================================
# 3. API 往返
# ============================================================

class TestApi:
    URL = "/api/v1/data/storage/recording-dir"

    def test_get_default_state(self, client):
        r = client.get(self.URL)
        assert r.status_code == 200
        body = r.json()
        assert body["custom_dir"] == ""
        assert body["using_custom"] is False
        assert body["effective_root"] == settings.RECORDING_DIR

    def test_put_roundtrip_and_reset(self, client, tmp_path):
        custom = str(tmp_path / "api_root")
        r = client.put(self.URL, json={"dir": custom})
        assert r.status_code == 200
        body = r.json()
        assert body["using_custom"] is True
        assert body["effective_root"] == custom
        assert "disk_free_gb" in body

        # 新开录像目录立即生效
        assert rs.get_recording_root() == custom

        # 空串恢复默认
        r = client.put(self.URL, json={"dir": ""})
        assert r.status_code == 200
        assert r.json()["using_custom"] is False
        assert rs.get_recording_root() == settings.RECORDING_DIR

    def test_put_rejects_bad_dir(self, client):
        r = client.put(self.URL, json={"dir": "relative/path"})
        assert r.status_code == 400
        r = client.put(self.URL, json={"dir": "/etc"})
        assert r.status_code == 400


# ============================================================
# 4. 归档护栏联动
# ============================================================

class TestArchiveGuardIntegration:
    def test_archive_dest_rejects_custom_recording_root(self, tmp_path):
        from backend.services.video_archive import validate_dest_dir
        custom = str(tmp_path / "rec_custom")
        os.makedirs(custom, exist_ok=True)
        _set_kv(custom)
        assert validate_dest_dir(os.path.join(custom, "inner")) is not None
        # 无关目录照常放行
        ok_dir = str(tmp_path / "elsewhere")
        assert validate_dest_dir(ok_dir) is None
