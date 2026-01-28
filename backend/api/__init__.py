from fastapi import APIRouter
from backend.api import projects, models, cameras, tasks, reports, websocket, alarm

api_router = APIRouter()

api_router.include_router(projects.router, prefix="/projects", tags=["projects"])
api_router.include_router(models.router, prefix="/models", tags=["models"])
api_router.include_router(cameras.router, prefix="/cameras", tags=["cameras"])
api_router.include_router(tasks.router, prefix="/tasks", tags=["tasks"])
api_router.include_router(reports.router, prefix="/reports", tags=["reports"])
api_router.include_router(alarm.router, prefix="/alarm", tags=["alarm"])
