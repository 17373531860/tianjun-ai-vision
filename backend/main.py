from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse, Response
from backend.core.config import settings
from backend.db.database import engine, Base
from backend.api import api_router
from backend.api.websocket import router as ws_router
from backend.api.source import router as source_router, get_video_feed, get_video_manager
from backend.api.detection import router as detection_router
from backend.api.sessions import router as sessions_router
from backend.services.detector import get_detection_service
# Import models to ensure they are registered
from backend.models import models
import os
import cv2
import atexit
import signal
import threading
from sqlalchemy import text

# ===== Startup diagnostics =====
from backend.core.config import BASE_DIR, DATA_DIR
print(f"[DIAG] main.py: DB URI = {settings.SQLALCHEMY_DATABASE_URI}")
print(f"[DIAG] main.py: MODEL_UPLOAD_DIR = {settings.MODEL_UPLOAD_DIR}")
_db_path = os.path.join(DATA_DIR, 'sql_app.db')
print(f"[DIAG] main.py: DB file exists before create_all: {os.path.exists(_db_path)}")
if os.path.exists(_db_path):
    print(f"[DIAG] main.py: DB file size: {os.path.getsize(_db_path)} bytes")

# Create database tables
Base.metadata.create_all(bind=engine)
print(f"[DIAG] main.py: create_all done, DB file size: {os.path.getsize(_db_path) if os.path.exists(_db_path) else 'N/A'}")

# 简单的数据库迁移：添加缺失的列
def migrate_database():
    """添加新增的数据库列（如果不存在）"""
    migrations = [
        # (表名, 列名, 列类型)
        ("step_records", "interval_to_next", "FLOAT"),
        ("detection_cycles", "interval_to_next", "FLOAT"),
        ("data_export_settings", "record_cycle_interval", "BOOLEAN DEFAULT 1"),
        ("data_export_settings", "export_step_duration", "BOOLEAN DEFAULT 1"),
        ("data_export_settings", "export_step_interval", "BOOLEAN DEFAULT 1"),
        ("data_export_settings", "export_step_event", "BOOLEAN DEFAULT 1"),
        ("data_export_settings", "export_cycle_duration", "BOOLEAN DEFAULT 1"),
        ("data_export_settings", "export_cycle_interval", "BOOLEAN DEFAULT 1"),
        ("data_export_settings", "export_cycle_result", "BOOLEAN DEFAULT 1"),
        ("data_export_settings", "export_counters", "BOOLEAN DEFAULT 1"),
        ("data_export_settings", "export_session_info", "BOOLEAN DEFAULT 1"),
        ("projects", "alarm_config", "JSON"),
        ("projects", "detection_config", "JSON"),
        ("projects", "data_config", "JSON"),
    ]
    
    try:
        with engine.connect() as conn:
            for table, column, col_type in migrations:
                try:
                    conn.execute(text(f"SELECT {column} FROM {table} LIMIT 1"))
                except Exception:
                    print(f"添加 {table}.{column} 列...")
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
                    conn.commit()
                    print(f"成功添加 {column} 列")
    except Exception as e:
        print(f"数据库迁移检查: {e}")

def fix_orphan_sessions():
    """修复孤立的会话（服务器重启后，之前运行中的会话应该标记为已中断）"""
    from backend.models.models import DetectionSession, DetectionCycle
    from sqlalchemy.orm import Session
    from sqlalchemy import func
    
    try:
        with Session(engine) as db:
            # 查找所有运行中的会话
            running_sessions = db.query(DetectionSession).filter(
                DetectionSession.status == "running"
            ).all()
            
            for session in running_sessions:
                # 计算统计数据
                cycles = db.query(DetectionCycle).filter(
                    DetectionCycle.session_id == session.id
                ).all()
                
                session.total_cycles = len(cycles)
                session.good_cycles = len([c for c in cycles if c.is_good])
                session.ng_cycles = session.total_cycles - session.good_cycles
                
                durations = [c.duration for c in cycles if c.duration]
                if durations:
                    session.avg_cycle_time = sum(durations) / len(durations)
                    session.min_cycle_time = min(durations)
                    session.max_cycle_time = max(durations)
                
                # 设置结束时间为最后一个周期的结束时间，或者会话开始时间
                if cycles:
                    last_cycle = max(cycles, key=lambda c: c.id)
                    if last_cycle.end_time:
                        session.end_time = last_cycle.end_time
                    elif last_cycle.start_time:
                        session.end_time = last_cycle.start_time
                    else:
                        session.end_time = session.start_time
                else:
                    session.end_time = session.start_time
                
                session.status = "interrupted"  # 标记为中断（服务器重启导致）
                
                print(f"修复会话 {session.session_uuid}: {session.total_cycles} 轮, {session.good_cycles}/{session.ng_cycles}")
            
            db.commit()
            
            if running_sessions:
                print(f"已修复 {len(running_sessions)} 个孤立会话")
    except Exception as e:
        print(f"修复孤立会话失败: {e}")
        import traceback
        traceback.print_exc()

def migrate_data_to_external_dir():
    """Migrate data from old install directory to external data directory.

    Handles the case where create_all already made an empty DB in DATA_DIR
    by checking whether the existing DB is empty before skipping.
    After copying files, rewrites absolute paths stored in the database.
    """
    from backend.core.config import BASE_DIR, DATA_DIR, _is_empty_db, _fix_db_paths
    if os.path.abspath(DATA_DIR) == os.path.abspath(BASE_DIR):
        return
    
    old_db = os.path.join(BASE_DIR, 'sql_app.db')
    new_db = os.path.join(DATA_DIR, 'sql_app.db')
    need_path_fix = False
    
    if os.path.exists(old_db):
        should_copy = not os.path.exists(new_db) or _is_empty_db(new_db)
        if should_copy:
            print(f"[数据迁移] 检测到旧数据，开始迁移: {BASE_DIR} -> {DATA_DIR}")
            import shutil
            try:
                os.makedirs(DATA_DIR, exist_ok=True)
                shutil.copy2(old_db, new_db)
                need_path_fix = True
                print(f"[数据迁移] 数据库已迁移")
            except Exception as e:
                print(f"[数据迁移] 数据库迁移失败: {e}")
                import traceback
                traceback.print_exc()

    import shutil
    for subdir in ['uploads', 'recordings']:
        old_path = os.path.join(BASE_DIR, subdir)
        new_path = os.path.join(DATA_DIR, subdir)
        if os.path.isdir(old_path):
            os.makedirs(new_path, exist_ok=True)
            try:
                for item in os.listdir(old_path):
                    src = os.path.join(old_path, item)
                    dst = os.path.join(new_path, item)
                    if os.path.exists(dst):
                        continue
                    if os.path.isdir(src):
                        shutil.copytree(src, dst)
                    else:
                        shutil.copy2(src, dst)
                print(f"[数据迁移] {subdir}/ 已迁移")
            except Exception as e:
                print(f"[数据迁移] {subdir}/ 迁移失败: {e}")

    if need_path_fix and os.path.exists(new_db):
        _fix_db_paths(new_db, BASE_DIR, DATA_DIR)
        print("[数据迁移] 数据库路径已修复")
    
    print("[数据迁移] 迁移完成")

print(f"[DIAG] main.py: running migrate_data_to_external_dir()")
migrate_data_to_external_dir()

def _fixup_stale_paths():
    from backend.core.config import BASE_DIR, DATA_DIR, _fix_db_paths
    if os.path.abspath(DATA_DIR) == os.path.abspath(BASE_DIR):
        return
    db_path = os.path.join(DATA_DIR, 'sql_app.db')
    if os.path.exists(db_path):
        print(f"[DIAG] main.py: running _fixup_stale_paths on {db_path}")
        _fix_db_paths(db_path, BASE_DIR, DATA_DIR)

_fixup_stale_paths()

# Post-migration DB health check
def _diag_db_health():
    """Log counts from key tables so we can verify data survived migration."""
    import sqlite3
    db_path = os.path.join(DATA_DIR, 'sql_app.db')
    if not os.path.exists(db_path):
        print(f"[DIAG] DB health: file not found at {db_path}")
        return
    try:
        conn = sqlite3.connect(db_path)
        for table in ('projects', 'ml_models', 'detection_sessions', 'detection_cycles', 'step_records', 'video_clips'):
            try:
                count = conn.execute(f"SELECT COUNT(*) FROM [{table}]").fetchone()[0]
                print(f"[DIAG] DB health: {table} = {count} rows")
            except Exception:
                print(f"[DIAG] DB health: {table} = <table not found>")
        # Check model file_path validity
        try:
            rows = conn.execute("SELECT id, name, file_path FROM ml_models").fetchall()
            for mid, mname, mpath in rows:
                exists = os.path.isfile(mpath) if mpath else False
                print(f"[DIAG] Model #{mid} '{mname}': path={mpath}, file_exists={exists}")
        except Exception:
            pass
        conn.close()
    except Exception as e:
        print(f"[DIAG] DB health check failed: {e}")

_diag_db_health()

migrate_database()
fix_orphan_sessions()

# ========== 后台自动清理定时任务 ==========
_cleanup_timer = None

def _schedule_auto_cleanup():
    """后台定时执行数据清理（每24小时一次）"""
    global _cleanup_timer
    try:
        from backend.api.sessions import _perform_auto_cleanup
        _perform_auto_cleanup()
    except Exception as e:
        print(f"[定时清理] 执行失败: {e}")
    _cleanup_timer = threading.Timer(86400, _schedule_auto_cleanup)
    _cleanup_timer.daemon = True
    _cleanup_timer.start()

def _start_auto_cleanup():
    """启动时执行一次清理，然后每24小时重复"""
    print("[定时清理] 启动时执行首次自动清理...")
    _schedule_auto_cleanup()

threading.Thread(target=_start_auto_cleanup, daemon=True).start()

# 清理状态标志（防止重复清理）
_cleanup_done = False
_cleanup_lock = threading.Lock()

# 程序退出时保存数据
def cleanup_on_exit():
    """程序退出时的清理和数据保存"""
    global _cleanup_done
    
    with _cleanup_lock:
        if _cleanup_done:
            print("[退出钩子] 清理已完成，跳过")
            return
        _cleanup_done = True
    
    print("[退出钩子] 正在保存数据...")
    try:
        video_manager = get_video_manager()
        
        # 保存计数器到当前会话
        if video_manager.current_session_id and video_manager.counters:
            video_manager._save_counters_snapshot()
            print(f"[退出钩子] 计数器已保存: {video_manager.counters}")
        
        # 结束当前会话（如果有）
        if video_manager.current_session_id:
            video_manager.end_session()
            print("[退出钩子] 会话已结束")
        
        # 停止检测
        if video_manager.is_detecting:
            video_manager.stop_detection()
            print("[退出钩子] 检测已停止")
        
        # 停止视频流
        if video_manager.is_running:
            video_manager.stop()
            print("[退出钩子] 视频流已停止")
            
    except Exception as e:
        print(f"[退出钩子] 清理时出错: {e}")
        import traceback
        traceback.print_exc()
    
    print("[退出钩子] 清理完成")

# 注册退出钩子
atexit.register(cleanup_on_exit)

# 处理 SIGTERM 信号（Docker/系统关闭）
def signal_handler(signum, frame):
    print(f"[信号处理] 收到信号 {signum}，正在退出...")
    cleanup_on_exit()
    os._exit(0)

signal.signal(signal.SIGTERM, signal_handler)
# 注意：SIGINT (Ctrl+C) 由 uvicorn 处理

app = FastAPI(
    title=settings.PROJECT_NAME, 
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    docs_url="/docs",
    redoc_url="/redoc"
)

# Set all CORS enabled origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routers
app.include_router(api_router, prefix=settings.API_V1_STR)

# Include WebSocket router
app.include_router(ws_router)

# Include Source router
app.include_router(source_router, prefix=f"{settings.API_V1_STR}/source", tags=["source"])

# Include Detection router
app.include_router(detection_router, prefix=f"{settings.API_V1_STR}/detection", tags=["detection"])

# Include Sessions router (数据管理)
app.include_router(sessions_router, prefix=f"{settings.API_V1_STR}/data", tags=["data"])

# Mount static files for uploads (images, etc.)
if os.path.exists(settings.UPLOAD_DIR):
    app.mount("/uploads", StaticFiles(directory=settings.UPLOAD_DIR), name="uploads")

# Mount recordings directory for video playback
if os.path.exists(settings.RECORDING_DIR):
    app.mount("/recordings", StaticFiles(directory=settings.RECORDING_DIR), name="recordings")

@app.get("/")
def root():
    return {
        "message": "Welcome to Tianjun Machine Vision API",
        "version": "1.0.0",
        "docs": "/docs",
        "api_prefix": settings.API_V1_STR
    }

@app.get("/health")
def health_check():
    """健康检查端点"""
    return {"status": "healthy"}

# ========== 优雅关闭 API ==========

@app.post("/api/v1/source/shutdown/step/{step}")
def shutdown_step(step: str):
    """执行单个关闭步骤"""
    video_manager = get_video_manager()
    
    try:
        if step == "stop_detection":
            if video_manager.is_detecting:
                video_manager.is_detecting = False
                video_manager._stop_inference_thread()
            return {"status": "success", "step": step}
        
        elif step == "save_counters":
            if video_manager.current_session_id and video_manager.counters:
                video_manager._save_counters_snapshot()
            return {"status": "success", "step": step}
        
        elif step == "end_cycle":
            # 如果有进行中的周期，结束它
            if video_manager.current_cycle_id:
                try:
                    video_manager.end_cycle(is_good=False, reason="程序关闭")
                except:
                    pass
            return {"status": "success", "step": step}
        
        elif step == "end_session":
            if video_manager.current_session_id:
                video_manager.end_session()
            return {"status": "success", "step": step}
        
        elif step == "stop_recording":
            video_manager.stop_session_recording()
            video_manager.stop_cycle_recording()
            video_manager._stop_recording_thread()
            return {"status": "success", "step": step}
        
        elif step == "release_camera":
            if video_manager.source_type == 'hikvision':
                video_manager._release_hik_camera()
            if video_manager.capture:
                try:
                    video_manager.capture.release()
                except:
                    pass
                video_manager.capture = None
            return {"status": "success", "step": step}
        
        elif step == "release_model":
            video_manager._release_model()
            return {"status": "success", "step": step}
        
        elif step == "cleanup":
            video_manager.is_running = False
            video_manager._clear_all_caches()
            return {"status": "success", "step": step}
        
        else:
            return {"status": "skipped", "step": step, "reason": "Unknown step"}
    
    except Exception as e:
        print(f"[关闭步骤] {step} 出错: {e}")
        return {"status": "error", "step": step, "error": str(e)}

@app.post("/api/v1/source/shutdown/complete")
def shutdown_complete():
    """完整关闭（被 backend-manager 调用）"""
    def delayed_exit():
        import time
        time.sleep(0.5)  # 给响应时间返回
        cleanup_on_exit()
        os._exit(0)
    
    threading.Thread(target=delayed_exit, daemon=True).start()
    return {"status": "shutting_down"}

# ========== 视频流端点 ==========

@app.get("/video_feed")
def video_feed():
    """视频流端点 - 使用输入源管理器"""
    video_manager = get_video_manager()
    
    # 运行中或已暂停但仍有输入源时，使用 generate_mjpeg（暂停时低帧率输出当前帧）
    if video_manager.is_running or video_manager.source_type:
        return StreamingResponse(
            video_manager.generate_mjpeg(),
            media_type="multipart/x-mixed-replace; boundary=frame"
        )
    
    # 没有任何输入源时，返回黑色占位帧
    def generate_frames():
        import numpy as np
        black_frame = np.zeros((480, 640, 3), dtype=np.uint8)
        cv2.putText(black_frame, "No Source", (220, 240), 
                   cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
        ret, buffer = cv2.imencode('.jpg', black_frame)
        if ret:
            frame_data = buffer.tobytes()
            while True:
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + frame_data + b'\r\n')
                import time
                time.sleep(0.1)
    
    return StreamingResponse(
        generate_frames(),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )

@app.get("/snapshot")
def snapshot():
    """单帧快照端点 - 返回当前帧的 JPEG（用于前端 canvas 渲染，避免 MJPEG 内存泄漏）"""
    video_manager = get_video_manager()
    data = video_manager.get_snapshot()
    if data:
        return Response(content=data, media_type="image/jpeg",
                        headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
    
    import numpy as np
    black_frame = np.zeros((480, 640, 3), dtype=np.uint8)
    cv2.putText(black_frame, "No Source", (220, 240),
               cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
    _, buffer = cv2.imencode('.jpg', black_frame)
    return Response(content=buffer.tobytes(), media_type="image/jpeg",
                    headers={"Cache-Control": "no-cache, no-store, must-revalidate"})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001, reload=True)
