from pydantic_settings import BaseSettings
from typing import Optional
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 数据目录：生产模式通过 TIANJUN_DATA_DIR 环境变量指向用户数据目录（%APPDATA%），
# 开发模式不设置该变量，回退到 BASE_DIR（与代码同目录，兼容旧行为）
DATA_DIR = os.environ.get('TIANJUN_DATA_DIR', BASE_DIR)

class Settings(BaseSettings):
    PROJECT_NAME: str = "Tianjun Machine Vision"
    API_V1_STR: str = "/api/v1"
    
    # Database
    SQLALCHEMY_DATABASE_URI: str = f"sqlite:///{os.path.join(DATA_DIR, 'sql_app.db')}"
    
    # File Upload Paths
    UPLOAD_DIR: str = os.path.join(DATA_DIR, "uploads")
    MODEL_UPLOAD_DIR: str = os.path.join(DATA_DIR, "uploads", "models")
    IMAGE_UPLOAD_DIR: str = os.path.join(DATA_DIR, "uploads", "images")
    VIDEO_UPLOAD_DIR: str = os.path.join(DATA_DIR, "uploads", "videos")
    
    # 录制视频存储路径
    RECORDING_DIR: str = os.path.join(DATA_DIR, "recordings")
    SESSION_VIDEO_DIR: str = os.path.join(DATA_DIR, "recordings", "sessions")
    STEP_VIDEO_DIR: str = os.path.join(DATA_DIR, "recordings", "steps")
    CYCLE_VIDEO_DIR: str = os.path.join(DATA_DIR, "recordings", "cycles")
    
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
