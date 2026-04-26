from pydantic_settings import BaseSettings
import os
import shutil
import logging

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 数据目录：生产模式通过 TIANJUN_DATA_DIR 环境变量指向用户数据目录（%APPDATA%），
# 开发模式不设置该变量，回退到 BASE_DIR（与代码同目录，兼容旧行为）
DATA_DIR = os.environ.get('TIANJUN_DATA_DIR', BASE_DIR)

# ===== Startup diagnostics =====
print("[DIAG] config.py loaded")
print(f"[DIAG] BASE_DIR = {BASE_DIR}")
print(f"[DIAG] DATA_DIR = {DATA_DIR}")
print(f"[DIAG] TIANJUN_DATA_DIR env = {os.environ.get('TIANJUN_DATA_DIR', '<not set>')}")
print(f"[DIAG] BASE_DIR == DATA_DIR: {os.path.abspath(BASE_DIR) == os.path.abspath(DATA_DIR)}")


def _is_empty_db(db_path):
    """Check if a SQLite database has no user data (only empty auto-created tables)."""
    import sqlite3
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
        if not tables:
            conn.close()
            return True
        for table in tables:
            count = conn.execute(f"SELECT COUNT(*) FROM [{table}]").fetchone()[0]
            if count > 0:
                conn.close()
                return False
        conn.close()
        return True
    except Exception:
        return False


def _fix_db_paths(db_path, old_base, new_base):
    """Rewrite absolute file_path values in the database after data migration.

    Models and VideoClips store absolute paths.  When data moves from the
    install directory to AppData, those paths must be updated to match.
    """
    import sqlite3
    old_prefix = os.path.join(old_base, '').replace('\\', '/')
    new_prefix = os.path.join(new_base, '').replace('\\', '/')
    if old_prefix == new_prefix:
        return

    try:
        conn = sqlite3.connect(db_path)
        for table in ('ml_models', 'video_clips'):
            try:
                conn.execute(f"SELECT file_path FROM [{table}] LIMIT 1")
            except Exception:
                continue
            updated = conn.execute(
                f"UPDATE [{table}] SET file_path = REPLACE(file_path, ?, ?) "
                f"WHERE file_path LIKE ?",
                (old_prefix, new_prefix, old_prefix + '%')
            ).rowcount
            # Also handle backslash paths on Windows
            old_bs = old_prefix.replace('/', '\\')
            new_bs = new_prefix.replace('/', '\\')
            updated += conn.execute(
                f"UPDATE [{table}] SET file_path = REPLACE(file_path, ?, ?) "
                f"WHERE file_path LIKE ?",
                (old_bs, new_bs, old_bs + '%')
            ).rowcount
            if updated:
                logger.info(f"Fixed {updated} path(s) in {table}")
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Failed to fix DB paths: {e}")


def _migrate_old_data():
    """Migrate data from old install directory to user data directory.

    Runs every startup so that any data left behind in the installation
    directory is always rescued to the safe DATA_DIR.  The NSIS
    installer.nsh customInit macro also backs up data BEFORE the old
    uninstaller runs, but this serves as a secondary safety net.

    Handles the edge case where create_all already created an empty DB
    in DATA_DIR — we overwrite it with the real data from the old location.
    """
    print("[DIAG] _migrate_old_data() called")
    if DATA_DIR == BASE_DIR:
        print("[DIAG] DATA_DIR == BASE_DIR, skipping migration")
        return

    os.makedirs(DATA_DIR, exist_ok=True)

    old_db = os.path.join(BASE_DIR, 'sql_app.db')
    new_db = os.path.join(DATA_DIR, 'sql_app.db')
    need_path_fix = False

    print(f"[DIAG] old_db exists: {os.path.exists(old_db)}  path: {old_db}")
    print(f"[DIAG] new_db exists: {os.path.exists(new_db)}  path: {new_db}")
    if os.path.exists(new_db):
        empty = _is_empty_db(new_db)
        sz = os.path.getsize(new_db)
        print(f"[DIAG] new_db size: {sz} bytes, is_empty: {empty}")

    if os.path.exists(old_db):
        should_copy = not os.path.exists(new_db) or _is_empty_db(new_db)
        print(f"[DIAG] should_copy DB: {should_copy}")
        if should_copy:
            try:
                shutil.copy2(old_db, new_db)
                need_path_fix = True
                print(f"[DIAG] DB migrated: {old_db} -> {new_db}")
            except Exception as e:
                print(f"[DIAG] DB migration FAILED: {e}")

    for folder_name in ('uploads', 'recordings'):
        old_dir = os.path.join(BASE_DIR, folder_name)
        new_dir = os.path.join(DATA_DIR, folder_name)
        old_exists = os.path.isdir(old_dir)
        old_contents = os.listdir(old_dir) if old_exists else []
        print(f"[DIAG] {folder_name}: old_dir exists={old_exists}, items={len(old_contents)}")
        if old_exists:
            os.makedirs(new_dir, exist_ok=True)
            try:
                for item in old_contents:
                    src = os.path.join(old_dir, item)
                    dst = os.path.join(new_dir, item)
                    if os.path.exists(dst):
                        continue
                    if os.path.isdir(src):
                        shutil.copytree(src, dst)
                        print(f"[DIAG]   copied dir: {item}")
                    else:
                        shutil.copy2(src, dst)
                        print(f"[DIAG]   copied file: {item}")
            except Exception as e:
                print(f"[DIAG] {folder_name} migration FAILED: {e}")

    if need_path_fix and os.path.exists(new_db):
        print(f"[DIAG] fixing DB paths: {BASE_DIR} -> {DATA_DIR}")
        _fix_db_paths(new_db, BASE_DIR, DATA_DIR)


_migrate_old_data()


class Settings(BaseSettings):
    PROJECT_NAME: str = "Tianjun Machine Vision"
    API_V1_STR: str = "/api/v1"
    
    # Database
    SQLALCHEMY_DATABASE_URI: str = f"sqlite:///{os.path.join(DATA_DIR, 'sql_app.db')}"
    
    # File Upload Paths
    UPLOAD_DIR: str = os.path.join(DATA_DIR, "uploads")
    MODEL_UPLOAD_DIR: str = os.path.join(DATA_DIR, "uploads", "models")
    MODEL_CONVERTED_DIR: str = os.path.join(DATA_DIR, "uploads", "models", "converted")
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
    settings.MODEL_CONVERTED_DIR,
    settings.IMAGE_UPLOAD_DIR, 
    settings.VIDEO_UPLOAD_DIR,
    settings.RECORDING_DIR,
    settings.SESSION_VIDEO_DIR,
    settings.STEP_VIDEO_DIR,
    settings.CYCLE_VIDEO_DIR
]
for dir_path in all_dirs:
    os.makedirs(dir_path, exist_ok=True)
