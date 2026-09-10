# -*- coding: utf-8 -*-
"""安装包预置模型 seeding — 可选包模型随安装包出厂, 启动时自动入模型仓库.

预置模型以标准 .yvmodel 包形态放在预置目录 (安装器 extraResources 落位):
    <BASE_DIR>/resources/preset_models/*.yvmodel
开发/测试可用环境变量 TIANJUN_PRESET_MODELS_DIR 覆盖目录。

设计约束:
- 完全复用 interconnect.package_ingest 的安全校验与入库路径 (同一套包契约),
  source 标记为 "preset", 试用模型由包内 extensions["x-trial"]=true 声明。
- 幂等: name+version 已存在 (PackageConflict) 视为已 seed 过, 静默跳过——
  升级安装新增的预置包会被增量补入, 已有的不重复。
- 错误隔离: 单个坏包只记日志跳过, 绝不让启动失败 (不变量: 预置资源挂了
  不能影响主程序起动)。
"""
from __future__ import annotations

import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PRESET_DIR = os.path.join(BASE_DIR, "resources", "preset_models")


def get_preset_dir() -> str:
    return os.environ.get("TIANJUN_PRESET_MODELS_DIR", DEFAULT_PRESET_DIR)


def seed_preset_models() -> dict:
    """扫描预置目录, 将未入库的 .yvmodel 逐个入模型仓库.

    Returns: {"scanned": n, "seeded": n, "skipped": n, "failed": n}
    """
    from backend.db.database import SessionLocal
    from backend.services.interconnect.package_ingest import (
        PackageConflict, PackageError, ingest_package,
    )

    stats = {"scanned": 0, "seeded": 0, "skipped": 0, "failed": 0}
    preset_dir = get_preset_dir()
    if not os.path.isdir(preset_dir):
        return stats

    try:
        entries = sorted(
            f for f in os.listdir(preset_dir)
            if f.lower().endswith(".yvmodel"))
    except OSError as e:
        print(f"[预置模型] 目录不可读 (跳过): {preset_dir}: {e}")
        return stats

    for fname in entries:
        stats["scanned"] += 1
        path = os.path.join(preset_dir, fname)
        db = SessionLocal()
        try:
            result = ingest_package(path, db, source="preset")
            stats["seeded"] += 1
            print(f"[预置模型] 已入库: {fname} -> "
                  f"{result['model_name']} v{result['version'] or '-'}"
                  + (f" (警告: {'; '.join(result['warnings'])})"
                     if result.get("warnings") else ""))
        except PackageConflict:
            stats["skipped"] += 1  # 已 seed 过, 幂等跳过
        except PackageError as e:
            stats["failed"] += 1
            print(f"[预置模型] 包非法 (跳过): {fname}: {e}")
        except Exception as e:  # noqa: BLE001 — 错误隔离底线
            stats["failed"] += 1
            print(f"[预置模型] 入库异常 (跳过): {fname}: {e}")
        finally:
            db.close()

    if stats["scanned"]:
        print(f"[预置模型] seeding 完成: 扫描 {stats['scanned']} / "
              f"入库 {stats['seeded']} / 已存在 {stats['skipped']} / "
              f"失败 {stats['failed']}")
    return stats
