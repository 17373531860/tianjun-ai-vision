from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import Optional
from backend.db.database import get_db
from backend.models.models import Project, Model
from backend.schemas.project import ProjectCreate, ProjectUpdate, ProjectResponse, ProjectListResponse

router = APIRouter()

@router.get("", response_model=ProjectListResponse)
def get_projects(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db)
):
    """获取项目列表"""
    total = db.query(Project).count()
    projects = db.query(Project).offset(skip).limit(limit).all()
    
    items = []
    for p in projects:
        model_name = None
        model_version = None
        if p.default_model_id:
            model = db.query(Model).filter(Model.id == p.default_model_id).first()
            if model:
                model_name = model.name
                model_version = model.version
        
        items.append(ProjectResponse(
            id=p.id,
            name=p.name,
            task_type=p.task_type,
            pipeline_config=p.pipeline_config,
            logic_mode=p.logic_mode,
            steps_config=p.steps_config,
            events_config=p.events_config,
            counters_config=p.counters_config,
            alarm_config=p.alarm_config,
            detection_config=p.detection_config,
            data_config=p.data_config,
            default_model_id=p.default_model_id,
            model_format=p.model_format or "pytorch_fp32",
            is_active=p.is_active,
            created_at=p.created_at,
            updated_at=p.updated_at,
            model_name=model_name,
            model_version=model_version
        ))
    
    return ProjectListResponse(total=total, items=items)

@router.get("/{project_id}", response_model=ProjectResponse)
def get_project(project_id: int, db: Session = Depends(get_db)):
    """获取项目详情"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    model_name = None
    model_version = None
    if project.default_model_id:
        model = db.query(Model).filter(Model.id == project.default_model_id).first()
        if model:
            model_name = model.name
            model_version = model.version
    
    return ProjectResponse(
        id=project.id,
        name=project.name,
        task_type=project.task_type,
        pipeline_config=project.pipeline_config,
        logic_mode=project.logic_mode,
        steps_config=project.steps_config,
        events_config=project.events_config,
        counters_config=project.counters_config,
        alarm_config=project.alarm_config,
        detection_config=project.detection_config,
        data_config=project.data_config,
        default_model_id=project.default_model_id,
        model_format=project.model_format or "pytorch_fp32",
        is_active=project.is_active,
        created_at=project.created_at,
        updated_at=project.updated_at,
        model_name=model_name,
        model_version=model_version
    )

@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
def create_project(project: ProjectCreate, db: Session = Depends(get_db)):
    """创建新项目"""
    existing = db.query(Project).filter(Project.name == project.name).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"项目名称 '{project.name}' 已存在")

    db_project = Project(
        name=project.name,
        task_type=project.task_type,
        pipeline_config=project.pipeline_config,
        default_model_id=project.default_model_id,
        model_format=getattr(project, 'model_format', 'pytorch_fp32') or 'pytorch_fp32',
        logic_mode=project.logic_mode,
        steps_config=project.steps_config,
        events_config=project.events_config,
        counters_config=project.counters_config,
        alarm_config=project.alarm_config,
        detection_config=project.detection_config,
        data_config=project.data_config
    )
    db.add(db_project)
    db.commit()
    db.refresh(db_project)
    
    return ProjectResponse(
        id=db_project.id,
        name=db_project.name,
        task_type=db_project.task_type,
        pipeline_config=db_project.pipeline_config,
        logic_mode=db_project.logic_mode,
        steps_config=db_project.steps_config,
        events_config=db_project.events_config,
        counters_config=db_project.counters_config,
        alarm_config=db_project.alarm_config,
        detection_config=db_project.detection_config,
        data_config=db_project.data_config,
        default_model_id=db_project.default_model_id,
        model_format=db_project.model_format or "pytorch_fp32",
        is_active=db_project.is_active,
        created_at=db_project.created_at,
        updated_at=db_project.updated_at
    )

@router.put("/{project_id}", response_model=ProjectResponse)
def update_project(project_id: int, project: ProjectUpdate, db: Session = Depends(get_db)):
    """更新项目"""
    db_project = db.query(Project).filter(Project.id == project_id).first()
    if not db_project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    update_data = project.model_dump(exclude_unset=True)
    if 'name' in update_data:
        existing = db.query(Project).filter(
            Project.name == update_data['name'],
            Project.id != project_id
        ).first()
        if existing:
            raise HTTPException(status_code=400, detail=f"项目名称 '{update_data['name']}' 已存在")
    for key, value in update_data.items():
        setattr(db_project, key, value)
    
    db.commit()
    db.refresh(db_project)
    
    model_name = None
    model_version = None
    if db_project.default_model_id:
        model = db.query(Model).filter(Model.id == db_project.default_model_id).first()
        if model:
            model_name = model.name
            model_version = model.version
    
    return ProjectResponse(
        id=db_project.id,
        name=db_project.name,
        task_type=db_project.task_type,
        pipeline_config=db_project.pipeline_config,
        logic_mode=db_project.logic_mode,
        steps_config=db_project.steps_config,
        events_config=db_project.events_config,
        counters_config=db_project.counters_config,
        alarm_config=db_project.alarm_config,
        detection_config=db_project.detection_config,
        data_config=db_project.data_config,
        default_model_id=db_project.default_model_id,
        model_format=db_project.model_format or "pytorch_fp32",
        is_active=db_project.is_active,
        created_at=db_project.created_at,
        updated_at=db_project.updated_at,
        model_name=model_name,
        model_version=model_version
    )

@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: int, db: Session = Depends(get_db)):
    """删除项目"""
    db_project = db.query(Project).filter(Project.id == project_id).first()
    if not db_project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    db.delete(db_project)
    db.commit()
    return None

def _reload_model_for_active_project(db: Session, project: Project) -> None:
    """激活项目后把 default_model 装到未绑定专属项目的通道上。

    逻辑与 backend/main.py::auto_load_active_project 的 fallback 分支保持一致：
    - 已绑定其它 project_id 的通道不动（多工位各自的项目优先）
    - 其余通道单通道走 load_model_for_channel，多通道走 load_shared_model
    - 任何失败只打日志，不影响 activate 本身的成功响应
    """
    import os
    from backend.api.channel_manager import channel_manager

    if not project.default_model_id:
        print(f"[激活项目] '{project.name}' 未配置默认模型，跳过模型加载")
        return

    model = db.query(Model).filter(Model.id == project.default_model_id).first()
    if not model or not model.file_path or not os.path.exists(model.file_path):
        file_path = getattr(model, 'file_path', None)
        print(f"[激活项目] '{project.name}' 默认模型文件不存在: {file_path}")
        return

    sources = channel_manager.get_channel_sources()
    bound_to_other = set()
    for ch_str, ch_cfg in sources.items():
        pid = ch_cfg.get("project_id")
        if pid and pid != project.id:
            try:
                bound_to_other.add(int(ch_str))
            except (TypeError, ValueError):
                continue

    remaining = [cid for cid in channel_manager.channels if cid not in bound_to_other]
    if not remaining:
        print("[激活项目] 所有通道都已绑定其它项目，跳过模型重载")
        return

    if len(remaining) == 1:
        ok = channel_manager.load_model_for_channel(remaining[0], model.file_path, "auto")
        print(f"[激活项目] ch{remaining[0]} 加载模型 '{model.name}': "
              f"{'成功' if ok else '失败'}")
    else:
        ok = channel_manager.load_shared_model(model.file_path, "auto")
        print(f"[激活项目] 共享模型 '{model.name}' 加载到通道 {remaining}: "
              f"{'成功' if ok else '失败'}")


@router.post("/{project_id}/activate", response_model=ProjectResponse)
def activate_project(project_id: int, db: Session = Depends(get_db)):
    """激活项目（设为当前运行项目）"""
    # 先取消所有项目的激活状态
    db.query(Project).update({Project.is_active: False})
    
    # 激活指定项目
    db_project = db.query(Project).filter(Project.id == project_id).first()
    if not db_project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    db_project.is_active = True
    db.commit()
    db.refresh(db_project)

    # v2.7.10 修复：activate 此前仅写 DB，ChannelManager 上的模型从不重新加载，
    # 导致切项目后 ch0 仍挂着旧项目的模型，标签和 project_config 对不上，
    # 用户感知为"检测什么都识别不出来"。此处补上与 main.py 启动逻辑一致的重载。
    try:
        _reload_model_for_active_project(db, db_project)
    except Exception as e:
        import traceback
        print(f"[激活项目] 模型重载异常 (不影响激活状态): {e}")
        traceback.print_exc()

    # 项目切换：清空所有通道的 MES pending/inspecting 状态，避免旧项目的扫码被新项目错误绑定
    try:
        from backend.services.mes_hooks import get_mes_hook
        from backend.api.channel_manager import channel_manager
        hook = get_mes_hook()
        ch_ids = list(channel_manager.channels.keys()) if hasattr(channel_manager, "channels") else [0]
        for ch_id in ch_ids:
            try:
                res = hook.clear_pending_scan(ch_id, force=False, db=db)
                if res and res.get("cleared_workpiece_id"):
                    print(f"[激活项目] 清除 ch{ch_id} 残留扫码: {res}", flush=True)
            except Exception as _e:
                print(f"[激活项目] ch{ch_id} 清除扫码失败（忽略）: {_e}", flush=True)
    except Exception as _e:
        print(f"[激活项目] MES 状态清理跳过: {_e}", flush=True)

    return ProjectResponse(
        id=db_project.id,
        name=db_project.name,
        task_type=db_project.task_type,
        pipeline_config=db_project.pipeline_config,
        logic_mode=db_project.logic_mode,
        steps_config=db_project.steps_config,
        events_config=db_project.events_config,
        counters_config=db_project.counters_config,
        alarm_config=db_project.alarm_config,
        detection_config=db_project.detection_config,
        data_config=db_project.data_config,
        default_model_id=db_project.default_model_id,
        model_format=db_project.model_format or "pytorch_fp32",
        is_active=db_project.is_active,
        created_at=db_project.created_at,
        updated_at=db_project.updated_at
    )

@router.get("/active/current", response_model=Optional[ProjectResponse])
def get_active_project(db: Session = Depends(get_db)):
    """获取当前激活的项目"""
    project = db.query(Project).filter(Project.is_active == True).first()
    if not project:
        return None
    
    model_name = None
    model_version = None
    if project.default_model_id:
        model = db.query(Model).filter(Model.id == project.default_model_id).first()
        if model:
            model_name = model.name
            model_version = model.version
    
    return ProjectResponse(
        id=project.id,
        name=project.name,
        task_type=project.task_type,
        pipeline_config=project.pipeline_config,
        logic_mode=project.logic_mode,
        steps_config=project.steps_config,
        events_config=project.events_config,
        counters_config=project.counters_config,
        alarm_config=project.alarm_config,
        detection_config=project.detection_config,
        data_config=project.data_config,
        default_model_id=project.default_model_id,
        model_format=project.model_format or "pytorch_fp32",
        is_active=project.is_active,
        created_at=project.created_at,
        updated_at=project.updated_at,
        model_name=model_name,
        model_version=model_version
    )
