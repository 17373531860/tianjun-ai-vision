from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from typing import List
import cv2
from backend.db.database import get_db
from backend.models.models import Camera
from backend.schemas.camera import CameraCreate, CameraUpdate, CameraResponse
from backend.core.config import settings

router = APIRouter()

# 全局相机实例管理
active_cameras = {}

def get_camera_capture(source):
    """获取相机捕获对象"""
    try:
        # 尝试解析为整数（USB相机索引）
        source_int = int(source)
        cap = cv2.VideoCapture(source_int)
    except ValueError:
        # 否则作为URL/路径处理
        cap = cv2.VideoCapture(source)
    
    return cap

def generate_frames(camera_source, width=1280, height=720, fps=30):
    """生成视频帧的生成器"""
    cap = get_camera_capture(camera_source)
    
    if not cap.isOpened():
        yield b''
        return
    
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    cap.set(cv2.CAP_PROP_FPS, fps)
    
    try:
        while True:
            success, frame = cap.read()
            if not success:
                break
            
            # 编码为JPEG
            ret, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
            if not ret:
                continue
            
            frame_bytes = buffer.tobytes()
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
    finally:
        cap.release()

@router.get("", response_model=List[CameraResponse])
def get_cameras(db: Session = Depends(get_db)):
    """获取相机列表"""
    cameras = db.query(Camera).all()
    return cameras

@router.get("/{camera_id}", response_model=CameraResponse)
def get_camera(camera_id: int, db: Session = Depends(get_db)):
    """获取相机详情"""
    camera = db.query(Camera).filter(Camera.id == camera_id).first()
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")
    return camera

@router.post("", response_model=CameraResponse, status_code=status.HTTP_201_CREATED)
def create_camera(camera: CameraCreate, db: Session = Depends(get_db)):
    """创建相机配置"""
    db_camera = Camera(**camera.model_dump())
    db.add(db_camera)
    db.commit()
    db.refresh(db_camera)
    return db_camera

@router.put("/{camera_id}", response_model=CameraResponse)
def update_camera(camera_id: int, camera: CameraUpdate, db: Session = Depends(get_db)):
    """更新相机配置"""
    db_camera = db.query(Camera).filter(Camera.id == camera_id).first()
    if not db_camera:
        raise HTTPException(status_code=404, detail="Camera not found")
    
    update_data = camera.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_camera, key, value)
    
    db.commit()
    db.refresh(db_camera)
    return db_camera

@router.delete("/{camera_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_camera(camera_id: int, db: Session = Depends(get_db)):
    """删除相机配置"""
    db_camera = db.query(Camera).filter(Camera.id == camera_id).first()
    if not db_camera:
        raise HTTPException(status_code=404, detail="Camera not found")
    
    db.delete(db_camera)
    db.commit()
    return None

@router.get("/{camera_id}/stream")
def stream_camera(camera_id: int, db: Session = Depends(get_db)):
    """获取相机视频流（MJPEG）"""
    camera = db.query(Camera).filter(Camera.id == camera_id).first()
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")
    
    return StreamingResponse(
        generate_frames(
            camera.source, 
            camera.resolution_width, 
            camera.resolution_height,
            camera.fps
        ),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )

@router.get("/{camera_id}/test")
def test_camera(camera_id: int, db: Session = Depends(get_db)):
    """测试相机连接"""
    camera = db.query(Camera).filter(Camera.id == camera_id).first()
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")
    
    cap = get_camera_capture(camera.source)
    if cap.isOpened():
        ret, _ = cap.read()
        cap.release()
        if ret:
            # 更新相机状态
            camera.status = "online"
            db.commit()
            return {"status": "online", "message": "Camera connected successfully"}
    
    camera.status = "offline"
    db.commit()
    return {"status": "offline", "message": "Failed to connect to camera"}

# 默认视频流端点（使用默认相机）
@router.get("/default/stream")
def stream_default_camera():
    """获取默认相机视频流"""
    return StreamingResponse(
        generate_frames(
            settings.DEFAULT_CAMERA_INDEX,
            settings.DEFAULT_FRAME_WIDTH,
            settings.DEFAULT_FRAME_HEIGHT,
            settings.DEFAULT_FPS
        ),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )
