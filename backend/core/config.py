from pydantic_settings import BaseSettings
from typing import Optional
import os
import sys

# 代码目录（只读）
CODE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def get_data_dir():
    """获取数据存储目录（可写）
    
    打包后的 Electron 应用运行在 Program Files 等受保护目录，
    需要将数据存储在用户可写的位置。
    """
    # 检查是否在打包环境中运行
    # 方法1: 检查是否在 Program Files 或 resources 目录中
    # 方法2: 检查环境变量
    is_packaged = (
        'Program Files' in CODE_DIR or 
        'resources' in CODE_DIR or
        os.environ.get('ELECTRON_RUN_AS_NODE') is not None
    )
    
    if is_packaged:
        # 打包环境：使用用户数据目录
        if sys.platform == 'win32':
            # Windows: C:\Users\xxx\AppData\Local\tianjun-ai-vision
            base = os.environ.get('LOCALAPPDATA', os.path.expanduser('~'))
            return os.path.join(base, 'tianjun-ai-vision')
        elif sys.platform == 'darwin':
            # macOS: ~/Library/Application Support/tianjun-ai-vision
            return os.path.expanduser('~/Library/Application Support/tianjun-ai-vision')
        else:
            # Linux: ~/.local/share/tianjun-ai-vision
            return os.path.expanduser('~/.local/share/tianjun-ai-vision')
    else:
        # 开发环境：使用代码目录
        return CODE_DIR

# 数据目录（用于数据库、上传、录制等）
DATA_DIR = get_data_dir()

# 兼容性：保留 BASE_DIR 指向数据目录
BASE_DIR = DATA_DIR

class Settings(BaseSettings):
    PROJECT_NAME: str = "Tianjun Machine Vision"
    API_V1_STR: str = "/api/v1"
    
    # Database - 存储在数据目录
    SQLALCHEMY_DATABASE_URI: str = f"sqlite:///{os.path.join(DATA_DIR, 'sql_app.db')}"
    
    # File Upload Paths - 存储在数据目录
    UPLOAD_DIR: str = os.path.join(DATA_DIR, "uploads")
    MODEL_UPLOAD_DIR: str = os.path.join(DATA_DIR, "uploads", "models")
    IMAGE_UPLOAD_DIR: str = os.path.join(DATA_DIR, "uploads", "images")
    VIDEO_UPLOAD_DIR: str = os.path.join(DATA_DIR, "uploads", "videos")
    
    # 录制视频存储路径 - 存储在数据目录
    RECORDING_DIR: str = os.path.join(DATA_DIR, "recordings")
    SESSION_VIDEO_DIR: str = os.path.join(DATA_DIR, "recordings", "sessions")  # 检测周期视频
    STEP_VIDEO_DIR: str = os.path.join(DATA_DIR, "recordings", "steps")  # 步骤视频
    CYCLE_VIDEO_DIR: str = os.path.join(DATA_DIR, "recordings", "cycles")  # 周期视频
    
    # Camera Settings
    DEFAULT_CAMERA_INDEX: int = 0
    DEFAULT_FRAME_WIDTH: int = 1280
    DEFAULT_FRAME_HEIGHT: int = 720
    DEFAULT_FPS: int = 30

    class Config:
        case_sensitive = True

settings = Settings()

# 打印数据目录位置（方便调试）
print(f"[Config] 数据目录: {DATA_DIR}")
print(f"[Config] 代码目录: {CODE_DIR}")

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
    
print(f"[Config] 数据库: {settings.SQLALCHEMY_DATABASE_URI}")
