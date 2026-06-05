#!/usr/bin/env python3
"""dev-activate-showcase.py — 开发态直接把 showcase 插件写进 DB 并激活。

为什么不用 /plugins/install:
  install 端点强制验签, 展会插件是未签名 dev build。而 PluginManager.load_active
  启动加载时只校验版本兼容、不验签 (验签只在安装那一步)。所以开发演示直接 upsert
  一条 is_active=True 的 PluginRecord 即可, 后端重启就会加载并通过
  /plugins/active/* 把前端 ESM/theme/i18n 提供给浏览器。

仅供本机开发/展会演示, 不走生产安装链路。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from backend.db.database import Base, SessionLocal, engine  # noqa: E402
from backend.models.plugin_models import PluginRecord, PluginState  # noqa: E402

INSTALL_DIR = REPO / "plugins" / "showcase"


def main() -> int:
    manifest_path = INSTALL_DIR / "plugin.json"
    if not manifest_path.exists():
        print(f"[ERR] 找不到插件: {manifest_path}")
        return 1
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cc = manifest["customer_code"]

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        for row in db.query(PluginRecord).all():
            row.is_active = row.customer_code == cc
            if row.customer_code != cc and row.status == "active":
                row.status = "installed"

        rec = db.query(PluginRecord).filter(PluginRecord.customer_code == cc).first()
        if not rec:
            rec = PluginRecord(customer_code=cc)
            db.add(rec)
        rec.name = manifest["name"]
        rec.plugin_version = manifest["plugin_version"]
        rec.tier = int(manifest["tier"])
        rec.status = "active"
        rec.is_active = True
        rec.install_path = str(INSTALL_DIR)
        rec.manifest_json = json.dumps(manifest, ensure_ascii=False, sort_keys=True)
        rec.files_digest = manifest["files_digest"]
        rec.signed_by = manifest.get("signed_by")
        rec.signed_at = manifest.get("signed_at")

        st = db.query(PluginState).filter(PluginState.customer_code == cc).first()
        if not st:
            st = PluginState(customer_code=cc)
            db.add(st)
        st.runtime_status = "pending_restart"
        st.health = "unknown"

        db.commit()
        print(f"[OK] 已激活插件 customer_code={cc} version={rec.plugin_version}")
        print(f"     install_path={rec.install_path}")
        actives = [r.customer_code for r in db.query(PluginRecord).filter(PluginRecord.is_active == True).all()]
        print(f"     当前 active 列表={actives}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
