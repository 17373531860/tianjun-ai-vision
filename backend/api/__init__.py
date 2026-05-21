from fastapi import APIRouter
from backend.api import projects, models, cameras, tasks, reports, alarm
from backend.api import system_display, export_custom, export_realtime, export_scheduled

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