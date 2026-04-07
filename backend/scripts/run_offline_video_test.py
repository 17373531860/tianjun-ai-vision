import os
import sys
import time


def main():
    """
    使用本地模型和视频在后端直接跑一遍完整流程，用于回归测试步骤/周期逻辑。
    
    - 模型: 仓库根目录的 best(5).pt
    - 视频: 仓库根目录的 01_2K17411_03.avi
    - 环境: 需在 conda 环境 tianjun 中运行
    """
    # 确保可以 import 到 backend 包
    root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if root_dir not in sys.path:
        sys.path.insert(0, root_dir)
    
    from backend.api.source import get_video_manager
    from backend.db.database import SessionLocal
    from backend.models.models import Project, DetectionSession, DetectionCycle
    
    video_manager = get_video_manager()
    
    model_path = os.path.join(root_dir, "best(5).pt")
    video_path = os.path.join(root_dir, "01_2K17411_03.avi")
    
    if not os.path.exists(model_path):
        raise FileNotFoundError(f"模型不存在: {model_path}")
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"视频不存在: {video_path}")
    
    db = SessionLocal()
    try:
        # 优先使用当前激活项目，其次使用最新项目
        project = (
            db.query(Project)
            .filter(Project.is_active == True)  # noqa: E712
            .order_by(Project.id.desc())
            .first()
        )
        if not project:
            project = db.query(Project).order_by(Project.id.desc()).first()
        if not project:
            raise RuntimeError("数据库中没有任何项目，无法进行离线测试")
        
        print(f"使用项目: id={project.id}, name={project.name}")
        
        project_config = {
            "id": project.id,
            "name": project.name,
            "logic_mode": project.logic_mode,
            "steps_config": project.steps_config or [],
            "pipeline_config": project.pipeline_config or {},
            "events_config": project.events_config or [],
            "counters_config": project.counters_config or [],
        }
        
        # 设置项目配置
        video_manager.set_project_config(project_config)
        
        # 启动视频源
        print(f"启动视频: {video_path}")
        video_manager.start_video(video_path, speed=1.0)
        
        # 启动检测
        print(f"加载模型并启动检测: {model_path}")
        video_manager.conf_threshold = 0.25
        video_manager.iou_threshold = 0.45
        video_manager.start_detection(model_path)
        
        # 创建检测会话（与 /source/detection/start 保持一致）
        session_info = video_manager.start_session(project.id)
        if not session_info:
            raise RuntimeError("创建检测会话失败")
        session_id = session_info["session_id"]
        print(f"检测会话已创建: id={session_id}, uuid={session_info['session_uuid']}")
        
        # 等待视频跑完
        print("正在运行离线检测，请稍候...")
        while video_manager.is_running and video_manager.source_type == "video":
            time.sleep(1)
        
        # 停止检测并结束会话
        print("视频播放结束，停止检测并结束会话")
        video_manager.stop_detection()
        video_manager.end_session()
        
        # 简单输出本次会话的统计信息，方便对比
        session = db.query(DetectionSession).filter(DetectionSession.id == session_id).first()
        if not session:
            print("警告: 未找到会话记录")
            return
        
        cycles = (
            db.query(DetectionCycle)
            .filter(DetectionCycle.session_id == session.id)
            .order_by(DetectionCycle.cycle_number.asc())
            .all()
        )
        print(f"本次会话周期数: {len(cycles)}, 合格: {session.good_cycles}, 不良: {session.ng_cycles}")
        for c in cycles:
            seq = " -> ".join(c.step_sequence or [])
            print(f"周期#{c.cycle_number}: 结果={'OK' if c.is_good else 'NG'}, 步骤序列={seq}")
    finally:
        db.close()


if __name__ == "__main__":
    main()

