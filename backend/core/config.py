from pydantic_settings import BaseSettings
from typing import Optional
import os
import shutil
import logging

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 数据目录：生产模式通过 TIANJUN_DATA_DIR 环境变量指向用户数据目录（%APPDATA%），
# 开发模式不设置该变量，回退到 BASE_DIR（与代码同目录，兼容旧行为）
DATA_DIR = os.environ.get('TIANJUN_DATA_DIR', BASE_DIR)


def _migrate_old_data():
    """Migrate data from old install directory to user data directory.
    
    Runs every startup (no one-time marker) so that any data left behind
    in the installation directory is always rescued to the safe DATA_DIR.
    The NSIS installer.nsh customInit macro also backs up data BEFORE
    the old uninstaller runs, but this serves as a secondary safety net.
    """
    if DATA_DIR == BASE_DIR:
        return

    os.makedirs(DATA_DIR, exist_ok=True)

    old_db = os.path.join(BASE_DIR, 'sql_app.db')
    new_db = os.path.join(DATA_DIR, 'sql_app.db')
    if os.path.exists(old_db) and not os.path.exists(new_db):
        try:
            shutil.copy2(old_db, new_db)
            logger.info(f"Migrated database: {old_db} -> {new_db}")
        except Exception as e:
            logger.error(f"Failed to migrate database: {e}")

    for folder_name in ('uploads', 'recordings'):
        old_dir = os.path.join(BASE_DIR, folder_name)
        new_dir = os.path.join(DATA_DIR, folder_name)
        if os.path.isdir(old_dir):
            os.makedirs(new_dir, exist_ok=True)
            try:
                for item in os.listdir(old_dir):
                    src = os.path.join(old_dir, item)
                    dst = os.path.join(new_dir, item)
                    if os.path.exists(dst):
                        continue
                    if os.path.isdir(src):
                        shutil.copytree(src, dst)
                    else:
                        shutil.copy2(src, dst)
                logger.info(f"Migrated {folder_name}: {old_dir} -> {new_dir}")
            except Exception as e:
                logger.error(f"Failed to migrate {folder_name}: {e}")


_migrate_old_data()


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
