from fastapi import APIRouter
from backend.api import projects, models, cameras, tasks, reports, alarm
from backend.api import system_display, export_custom, export_realtime, export_scheduled
from backend.api import channel_groups  # v3.13 RFC 10 工位组
from backend.api import workpiece_flows  # v3.14 RFC 11 串行流水线
from backend.api import showcase_stats  # RFC12 展会: 行为评分/趋势只读统计

api_router = APIRouter()

api_router.include_router(projects.router, prefix="/projects", tags=["projects"])
api_router.include_router(models.router, prefix="/models", tags=["models"])
api_router.include_router(cameras.router, prefix="/cameras", tags=["cameras"])
api_router.include_router(tasks.router, prefix="/tasks", tags=["tasks"])
api_router.include_router(reports.router, prefix="/reports", tags=["reports"])
api_router.include_router(alarm.router, prefix="/alarm", tags=["alarm"])
api_router.include_router(system_display.router, prefix="/system", tags=["system"])
api_router.include_router(export_custom.router, prefix="/export", tags=["export"])
api_router.include_router(export_realtime.router, prefix="/export", tags=["export-realtime"])
api_router.include_router(export_scheduled.router, prefix="/export", tags=["export-scheduled"])
# v3.13 RFC 10: 工位组 CRUD (单机内多通道结算联动)
api_router.include_router(channel_groups.router, prefix="/channel-groups", tags=["channel-groups"])
# v3.14 RFC 11: 串行流水线 CRUD + 历史 run
api_router.include_router(workpiece_flows.router, prefix="/workpiece-flows", tags=["workpiece-flows"])
# RFC12 展会: 监控页高科技面板真实数据 (行为评分/合格率趋势), 挂 /data 前缀
api_router.include_router(showcase_stats.router, prefix="/data", tags=["showcase-stats"])