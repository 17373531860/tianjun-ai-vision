from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse
from backend.core.config import settings
from backend.db.database import engine, Base
from backend.api import api_router
from backend.api.websocket import router as ws_router
from backend.api.source import router as source_router, get_video_feed, get_video_manager
from backend.api.detection import router as detection_router
from backend.api.sessions import router as sessions_router, start_auto_cleanup
from backend.services.detector import get_detection_service
# Import models to ensure they are registered
from backend.models import models
import os
import cv2
import atexit
import signal
import threading
from sqlalchemy import text

# Create database tables
Base.metadata.create_all(bind=engine)

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

migrate_database()
fix_orphan_sessions()
start_auto_cleanup()

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
    
    # 如果有活动的输入源，使用它
    if video_manager.is_running:
        return StreamingResponse(
            video_manager.generate_mjpeg(),
            media_type="multipart/x-mixed-replace; boundary=frame"
        )
    
    # 没有活动的输入源时，返回黑色占位帧（不尝试打开摄像头）
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001, reload=True)
