"""
检测控制 API
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any
from backend.services.detector import get_detection_service
from backend.api.source import get_video_manager
from backend.db.database import SessionLocal
from backend.models.models import Model, Project

router = APIRouter()


class StartDetectionRequest(BaseModel):
    project_id: int
    model_id: Optional[int] = None


class ConfigRequest(BaseModel):
    project_config: Dict[str, Any]


@router.post("/start")
def start_detection(req: StartDetectionRequest):
    """启动检测"""
    db = SessionLocal()
    try:
        # 获取项目信息
        project = db.query(Project).filter(Project.id == req.project_id).first()
        if not project:
            raise HTTPException(status_code=404, detail="项目不存在")
        
        # 获取模型 - 使用 default_model_id
        model_id = req.model_id or project.default_model_id
        if not model_id:
            raise HTTPException(status_code=400, detail="请先在项目管理中配置模型")
        
        model = db.query(Model).filter(Model.id == model_id).first()
        if not model:
            raise HTTPException(status_code=404, detail="模型不存在")
        
        detection_service = get_detection_service()
        video_manager = get_video_manager()
        
        # 设置视频管理器
        detection_service.set_video_manager(video_manager)
        
        # 加载模型
        if not detection_service.detector.is_loaded or detection_service.detector.model_path != model.file_path:
            if not detection_service.load_model(model.file_path):
                raise HTTPException(status_code=500, detail="模型加载失败")
        
        # 设置项目配置
        project_config = {
            'id': project.id,
            'name': project.name,
            'logic_mode': project.logic_mode,
            'steps_config': project.steps_config or [],
            'pipeline_config': project.pipeline_config or {},
            'events_config': project.events_config or [],
            'counters_config': project.counters_config or []
        }
        detection_service.set_project_config(project_config)
        
        # 启动检测
        detection_service.start()
        
        return {
            "status": "success",
            "message": "检测已启动",
            "project": project.name,
            "model": model.name
        }
    finally:
        db.close()


@router.post("/stop")
def stop_detection():
    """停止检测"""
    detection_service = get_detection_service()
    detection_service.stop()
    return {"status": "success", "message": "检测已停止"}


@router.post("/reset")
def reset_detection():
    """重置检测状态"""
    detection_service = get_detection_service()
    detection_service.reset()
    return {"status": "success", "message": "检测已重置"}


@router.get("/status")
def get_detection_status():
    """获取检测状态"""
    detection_service = get_detection_service()
    return detection_service.get_status()


@router.get("/results")
def get_detection_results():
    """获取当前检测结果"""
    detection_service = get_detection_service()
    return {
        "detections": detection_service.get_detections(),
        "fps": detection_service.fps,
        "latency": detection_service.latency
    }
