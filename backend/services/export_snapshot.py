"""v3.7.2 自定义导出 — cycle_start 锁定快照

C 策略 (cycle_start_snapshot) 的实现核心:

cycle_end 触发 ExportRealtimeRule 渲染时, 默认行为是去 input_dir 翻最新 txt.
但这有 5 个坑 (串号/读半截/时钟漂移/相同 mtime/文件没生成).

C 策略改成: 在 cycle_start 那一刻 (扫码已发生, 文件已在), 立即从 input_dir
取一份快照 (filename + text + mtime) 写到 DetectionCycle.external_meta.
后续 cycle_end 渲染时, helper 优先从 ctx.export.locked 拿快照, 而不是
现去翻文件夹.

这样:
  • 一周期 1 份快照, 0 串号概率
  • 即使周期中扫码器又写了一份新 txt, 不影响本周期判定
  • DB 里留下"这一轮绑了哪份扫码 txt"的痕迹, 可追溯

入口: snapshot_for_cycle_start(db, channel_id, cycle_id, project_id)
被 mes_hooks._handle_cycle_start 末尾调用.
"""
from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from backend.models.export_models import ExportRealtimeRule
from backend.models.models import DetectionCycle
from backend.services.export_renderer import (
    latest_input_filename, latest_input_text, _wait_for_stable
)


# 用于"规范化"路径作为快照字典的 key. 同一个目录走不同的写法 (D:\xxx vs D:/xxx
# vs 末尾带不带 /) 应该命中同一个快照, 否则一条规则会重复算多次.
def _normalize_dir(d: str) -> str:
    if not d:
        return ""
    try:
        return os.path.normpath(os.path.abspath(d))
    except Exception:
        return d.replace("\\", "/").rstrip("/")


def _collect_target_input_dirs(db: Session,
                                channel_id: int,
                                project_id: Optional[int]) -> List[Dict[str, Any]]:
    """扫所有适用本 cycle 的 cycle_start_snapshot 规则, 按 input_dir 去重.

    返回: [{"input_dir": "...", "wait_stable_ms": 100, "max_age_sec": 0,
            "rule_ids": [1, 5, 8]}, ...]
    一个 input_dir 可能被多条规则共用, 我们只对每个 input_dir 拍一次照.
    """
    try:
        rules = (
            db.query(ExportRealtimeRule)
            .filter(
                ExportRealtimeRule.enabled == True,  # noqa: E712
                ExportRealtimeRule.trigger_event == "cycle_end",
                ExportRealtimeRule.latest_file_strategy == "cycle_start_snapshot",
                ExportRealtimeRule.input_dir.isnot(None),
                ExportRealtimeRule.input_dir != "",
            )
            .all()
        )
    except Exception as e:
        print(f"[ExportSnapshot] 查询规则异常 ch{channel_id}: {e}", flush=True)
        return []

    # 按 input_dir 分组, 取每条规则里最严格的 wait_stable_ms / max_age_sec
    # (取 max 是为了 "宁严勿松", 单条规则也能因为同目录其他规则的设定享受更稳)
    grouped: Dict[str, Dict[str, Any]] = {}
    for r in rules:
        # filter: 通道 / 项目
        if r.channel_filter and channel_id not in r.channel_filter:
            continue
        if r.project_filter and project_id is not None \
                and project_id not in r.project_filter:
            continue

        key = _normalize_dir(r.input_dir or "")
        if not key:
            continue
        existing = grouped.get(key)
        if existing:
            existing["wait_stable_ms"] = max(
                existing["wait_stable_ms"], int(r.latest_file_wait_stable_ms or 0)
            )
            existing["max_age_sec"] = max(
                existing["max_age_sec"], int(r.latest_file_max_age_sec or 0)
            )
            existing["rule_ids"].append(r.id)
        else:
            grouped[key] = {
                "input_dir": r.input_dir,
                "wait_stable_ms": int(r.latest_file_wait_stable_ms or 0),
                "max_age_sec": int(r.latest_file_max_age_sec or 0),
                "rule_ids": [r.id],
            }
    return list(grouped.values())


def snapshot_for_cycle_start(db: Session,
                              channel_id: int,
                              cycle_id: int,
                              project_id: Optional[int] = None) -> Dict[str, Any]:
    """周期开始时拍快照: 把每个目标 input_dir 当前最新 txt 的内容和文件名
    写入 DetectionCycle.external_meta['scan_snapshots'][<规范化目录>].

    幂等: 重入会覆盖上一次的快照 (理论上 cycle_start 一周期只调一次).

    返回简要统计 dict (供日志):
        {"taken": 2, "skipped": 0, "errors": 0, "details": [...]}

    失败不抛, 仅日志记录 — cycle_start 主流程不能因此受影响.
    """
    summary = {"taken": 0, "skipped": 0, "errors": 0, "details": []}
    try:
        groups = _collect_target_input_dirs(db, channel_id, project_id)
        if not groups:
            return summary

        snapshots: Dict[str, Dict[str, Any]] = {}
        for g in groups:
            d = g["input_dir"]
            key = _normalize_dir(d)
            try:
                filename = None
                text = ""
                mtime = None
                read_via = None

                # 内存优先: 先读旁路监控线程的缓存 (cycle_start 不扫目录、不等文件稳定).
                # 监控无此目录 / 未预热 / 状态非 ok 时, 回退到直接 glob 兜底.
                try:
                    from backend.services.scanner_bypass_monitor import (
                        get_current_for_dir,
                    )
                    mem = get_current_for_dir(key)
                except Exception:
                    mem = None
                if mem and mem.get("status") == "ok" and mem.get("filename"):
                    filename = mem.get("filename")
                    text = mem.get("text") or ""
                    mtime = mem.get("mtime")
                    read_via = "monitor"
                else:
                    # 回退: 直接调底层入口, 显式传 wait_stable / max_age
                    filename = latest_input_filename(
                        d,
                        wait_stable_ms=g["wait_stable_ms"],
                        max_age_sec=g["max_age_sec"],
                    )
                    text = latest_input_text(
                        d,
                        wait_stable_ms=g["wait_stable_ms"],
                        max_age_sec=g["max_age_sec"],
                    ) if filename else ""
                    read_via = "glob"

                if not filename:
                    summary["skipped"] += 1
                    summary["details"].append({
                        "input_dir": d, "rule_ids": g["rule_ids"],
                        "skipped": "no_matching_file",
                    })
                    print(f"[ExportSnapshot] cycle#{cycle_id} ch{channel_id} "
                          f"目录 {d} 无可用文件 (规则 {g['rule_ids']})", flush=True)
                    continue

                # mtime (best-effort) — 内存路径已带 mtime, glob 路径这里补
                if mtime is None:
                    try:
                        mtime = os.path.getmtime(os.path.join(d, filename))
                    except Exception:
                        pass

                now_iso = datetime.now().isoformat()
                snapshots[key] = {
                    # v3.7.2 起字段 (向后兼容, 勿删)
                    "filename": filename,
                    "text": text,
                    "mtime": mtime,
                    "snapshot_at": now_iso,
                    "rule_ids": g["rule_ids"],
                    # 旁路 SN 增补字段
                    "serial_no": os.path.splitext(filename)[0],
                    "input_dir": d,
                    "locked_at": now_iso,
                    "source": "scanner_bypass",
                    "read_via": read_via,
                }
                summary["taken"] += 1
                summary["details"].append({
                    "input_dir": d, "rule_ids": g["rule_ids"],
                    "filename": filename, "text_preview": (text or "")[:50],
                    "read_via": read_via,
                })
            except Exception as e:
                summary["errors"] += 1
                summary["details"].append({
                    "input_dir": d, "rule_ids": g["rule_ids"],
                    "error": f"{type(e).__name__}: {e}",
                })
                print(f"[ExportSnapshot] cycle#{cycle_id} ch{channel_id} "
                      f"目录 {d} 拍照异常: {e}", flush=True)

        if not snapshots:
            return summary

        # 写入 DetectionCycle.external_meta
        cyc = db.query(DetectionCycle).filter(
            DetectionCycle.id == cycle_id
        ).first()
        if cyc is None:
            print(f"[ExportSnapshot] cycle#{cycle_id} 未找到, 快照不落库", flush=True)
            return summary

        meta = dict(cyc.external_meta or {})
        meta["scan_snapshots"] = snapshots
        cyc.external_meta = meta
        # JSON 字段不会自动检测 dict 内部变动, 显式标 modified
        try:
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(cyc, "external_meta")
        except Exception:
            pass
        db.flush()
        # commit 留给上层 (mes_hooks worker 末尾会 commit)
        print(f"[ExportSnapshot] cycle#{cycle_id} ch{channel_id} "
              f"已锁 {len(snapshots)} 个 input_dir 快照", flush=True)
    except Exception as e:
        summary["errors"] += 1
        print(f"[ExportSnapshot] snapshot_for_cycle_start "
              f"cycle#{cycle_id} ch{channel_id} 总异常: {e}", flush=True)

    return summary


def lookup_snapshot_from_cycle(cycle_external_meta: Optional[Dict[str, Any]],
                                input_dir: str) -> Optional[Dict[str, Any]]:
    """cycle_end 渲染时反查: 给定 rule.input_dir, 返回 cycle_start 锁定的快照.

    返回 None 表示该 cycle 没拍这个目录的快照 (rule 后建? cycle_start 时报错?).
    调用方应 fallback 到 mtime 路径.
    """
    if not cycle_external_meta or not isinstance(cycle_external_meta, dict):
        return None
    snapshots = cycle_external_meta.get("scan_snapshots") or {}
    if not isinstance(snapshots, dict):
        return None
    return snapshots.get(_normalize_dir(input_dir))
