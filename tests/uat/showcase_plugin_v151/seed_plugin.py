# -*- coding: utf-8 -*-
"""在隔离 DB 里种 showcase 插件记录 (install_path 指向源码树, 免签名热载).

用法 (TIANJUN_DATA_DIR 必须与随后启动后端的一致):
  TIANJUN_DATA_DIR=/tmp/tj_uat151_data \
  ~/miniconda3/envs/tianjun/bin/python tests/uat/showcase_plugin_v151/seed_plugin.py
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "threads;1")

SRC = ROOT / "plugins-examples/tianjun-showcase"


def main():
    data_dir = os.environ.get("TIANJUN_DATA_DIR")
    assert data_dir and "tj_uat151" in data_dir, "必须显式设 TIANJUN_DATA_DIR=/tmp/tj_uat151_data (防误写生产库)"

    from backend.db.database import SessionLocal, engine, Base
    import backend.models  # noqa: F401 注册全部模型
    from backend.models.plugin_models import PluginRecord

    Base.metadata.create_all(bind=engine)
    manifest = json.loads((SRC / "plugin.json").read_text(encoding="utf-8"))

    db = SessionLocal()
    try:
        rec = db.query(PluginRecord).filter(PluginRecord.customer_code == "showcase").first()
        if rec is None:
            rec = PluginRecord(customer_code="showcase", name=manifest["name"],
                               plugin_version=manifest["plugin_version"], tier=manifest.get("tier", 2),
                               status="installed", is_active=True,
                               install_path=str(SRC), manifest_json=json.dumps(manifest, ensure_ascii=False),
                               files_digest=manifest.get("files_digest", ""),
                               signed_by=manifest.get("signed_by"), signed_at=manifest.get("signed_at"))
            db.add(rec)
        else:
            rec.plugin_version = manifest["plugin_version"]
            rec.install_path = str(SRC)
            rec.manifest_json = json.dumps(manifest, ensure_ascii=False)
            rec.is_active = True
            rec.status = "installed"
        db.commit()
        print("seeded showcase", manifest["plugin_version"], "->", SRC)
    finally:
        db.close()


if __name__ == "__main__":
    main()
