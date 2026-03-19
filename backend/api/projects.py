from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional
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
        default_model_id=db_project.default_model_id,
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
        is_active=project.is_active,
        created_at=project.created_at,
        updated_at=project.updated_at,
        model_name=model_name,
        model_version=model_version
    )
