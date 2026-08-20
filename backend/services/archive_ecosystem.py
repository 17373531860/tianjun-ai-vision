# -*- coding: utf-8 -*-
"""归档生态联动 (v3.53 二/三/四期): sidecar 渲染 / 插件 hook / MES 网关事件。

全部函数错误隔离: 联动是增值品, 任何失败都不允许影响归档主流程。
"""
import os
from typing import Any, Dict, Optional


def render_sidecar(db, template_id: int, ctx: Dict[str, Any],
                   out_dir: str, basename: str) -> Optional[str]:
    """按导出模板在 out_dir 渲染 sidecar 报告, 文件名与录像同 basename。
    返回落盘路径; 模板不存在/渲染失败返回 None (打日志, 不推翻录像归档)。"""
    try:
        from backend.models.export_models import ExportTemplate
        from backend.services.export_renderer import render_to_file
        tpl = db.query(ExportTemplate).filter(
            ExportTemplate.id == template_id).first()
        if not tpl:
            print(f"[VideoArchive] sidecar 模板 {template_id} 不存在, 跳过")
            return None
        fmt = tpl.format or "txt"
        result = render_to_file(
            tpl.content or "",
            filename_template=f"{basename}.{fmt}",
            output_dir=out_dir, context=ctx, fmt=fmt,
            overwrite_policy="overwrite",
            template_file_path=tpl.template_file_path,
        )
        if result.status == "success" and result.output_path:
            return result.output_path
        print(f"[VideoArchive] sidecar 渲染失败: {result.error_msg}")
        return None
    except Exception as e:
        print(f"[VideoArchive] sidecar 渲染异常: {e}")
        return None


def on_video_archived(db, cycle, rule, dest_path: Optional[str],
                      channel_id: Optional[int]):
    """归档成功后的联动出口 (worker 线程内调用, 逐项错误隔离):
    1. 插件 hook video_archived
    2. MES 网关 video_archived 事件 (订阅了该事件的连接推最终地址)
    """
    payload = {
        "cycle_id": cycle.id,
        "cycle_uuid": cycle.cycle_uuid,
        "channel_id": channel_id,
        "is_good": bool(cycle.is_good),
        "src_path": cycle.video_path,
        "dest_path": dest_path,
        "rule_id": rule.id,
        "rule_name": rule.name,
        "dest_type": rule.dest_type or "local_dir",
    }
    _fire_plugin_hook(payload)
    _fire_gateway_event(db, cycle, payload)


def _fire_plugin_hook(payload: Dict[str, Any]):
    """插件 hook (observe 型, 不进 RETURNABLE 白名单, 返回值丢弃)。"""
    try:
        from backend.plugin_system.hook_dispatch import fire_plugin_hook
        fire_plugin_hook("video_archived", "post_archive", "post", payload)
    except Exception as e:
        print(f"[VideoArchive] video_archived 插件 hook 失败: {e}")


def _fire_gateway_event(db, cycle, payload: Dict[str, Any]):
    """MES 网关事件: 订阅了 video_archived 的连接推最终归档地址。
    走 dispatch_gateway 异步执行器 + gateway_spool (不变量 15, 不阻塞 worker);
    dispatch 内部自开 SessionLocal, 不传归档 worker 的 db session。"""
    try:
        from backend.services.mes_hooks import get_mes_hook
        hook = get_mes_hook()
        if hook is None:
            return
        context = {
            "cycle": {
                "id": cycle.id,
                "uuid": cycle.cycle_uuid,
                "number": cycle.cycle_number,
                "result": "OK" if cycle.is_good else "NG",
                "is_good": bool(cycle.is_good),
            },
            "archive": {
                "video_path": payload.get("dest_path"),
                "dest_type": payload.get("dest_type"),
                "rule_id": payload.get("rule_id"),
                "rule_name": payload.get("rule_name"),
            },
        }
        hook.dispatch_gateway("video_archived", context,
                              payload.get("channel_id"))
    except Exception as e:
        print(f"[VideoArchive] video_archived 网关事件失败: {e}")


# ---------------------------------------------------------------------------
# archived_* 字段查询 (三期字段库用)
# ---------------------------------------------------------------------------

def latest_archive_info(db, cycle_id: int) -> Dict[str, Any]:
    """查该周期最近一次归档成功记录, 供字段库 cycle.archived_* 填充。"""
    empty = {"archived_video_path": None, "archived_at": None,
             "archive_status": None}
    try:
        from backend.models.archive_models import VideoArchiveLog
        row = db.query(VideoArchiveLog).filter(
            VideoArchiveLog.cycle_id == cycle_id,
            VideoArchiveLog.status == "success",
        ).order_by(VideoArchiveLog.id.desc()).first()
        if row:
            return {
                "archived_video_path": row.dest_path,
                "archived_at": row.created_at.isoformat()
                if row.created_at else None,
                "archive_status": "success",
            }
        row = db.query(VideoArchiveLog).filter(
            VideoArchiveLog.cycle_id == cycle_id,
        ).order_by(VideoArchiveLog.id.desc()).first()
        if row:
            return {"archived_video_path": None, "archived_at": None,
                    "archive_status": row.status}
        return empty
    except Exception:
        return empty
