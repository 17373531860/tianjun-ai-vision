from pydantic_settings import BaseSettings
from typing import Optional
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

class Settings(BaseSettings):
    PROJECT_NAME: str = "Tianjun Machine Vision"
    API_V1_STR: str = "/api/v1"
    
    # Database
    SQLALCHEMY_DATABASE_URI: str = f"sqlite:///{os.path.join(BASE_DIR, 'sql_app.db')}"
    
    # File Upload Paths
    UPLOAD_DIR: str = os.path.join(BASE_DIR, "uploads")
    MODEL_UPLOAD_DIR: str = os.path.join(BASE_DIR, "uploads", "models")
    IMAGE_UPLOAD_DIR: str = os.path.join(BASE_DIR, "uploads", "images")
    VIDEO_UPLOAD_DIR: str = os.path.join(BASE_DIR, "uploads", "videos")
    
    # 录制视频存储路径
    RECORDING_DIR: str = os.path.join(BASE_DIR, "recordings")
    SESSION_VIDEO_DIR: str = os.path.join(BASE_DIR, "recordings", "sessions")  # 检测周期视频
    STEP_VIDEO_DIR: str = os.path.join(BASE_DIR, "recordings", "steps")  # 步骤视频
    CYCLE_VIDEO_DIR: str = os.path.join(BASE_DIR, "recordings", "cycles")  # 周期视频
    
    # Camera Settings
    DEFAULT_CAMERA_INDEX: int = 0
    DEFAULT_FRAME_WIDTH: int = 1280
    DEFAULT_FRAME_HEIGHT: int = 720
    DEFAULT_FPS: int = 30

    class Config:
        case_sensitive = True

settings = Settings()

# Ensure upload directories exist
all_dirs = [
    settings.MODEL_UPLOAD_DIR, 
    settings.IMAGE_UPLOAD_DIR, 
    settings.VIDEO_UPLOAD_DIR,
    settings.RECORDING_DIR,
    settings.SESSION_VIDEO_DIR,
    settings.STEP_VIDEO_DIR,
    settings.CYCLE_VIDEO_DIR
]
for dir_path in all_dirs:
    os.makedirs(dir_path, exist_ok=True)
