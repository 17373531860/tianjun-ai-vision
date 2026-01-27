from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
from sqlalchemy.orm import Session
from sqlalchemy import desc
from typing import List, Optional
import os
import shutil
import uuid
from datetime import datetime
from backend.db.database import get_db
from backend.models.models import Task, Project, Model
from backend.schemas.task import TaskCreate, TaskResponse, TaskListResponse
from backend.core.config import settings

router = APIRouter()

@router.get("", response_model=TaskListResponse)
def get_tasks(
    skip: int = 0,
    limit: int = 100,
    project_id: Optional[int] = None,
    is_good: Optional[bool] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """获取检测任务列表"""
    query = db.query(Task)
    
    if project_id:
        query = query.filter(Task.project_id == project_id)
    if is_good is not None:
        query = query.filter(Task.is_good == is_good)
    if start_date:
        query = query.filter(Task.timestamp >= start_date)
    if end_date:
        query = query.filter(Task.timestamp <= end_date + " 23:59:59")
    
    total = query.count()
    tasks = query.order_by(desc(Task.timestamp)).offset(skip).limit(limit).all()
    
    return TaskListResponse(total=total, items=tasks)

@router.get("/{task_id}", response_model=TaskResponse)
def get_task(task_id: int, db: Session = Depends(get_db)):
    """获取任务详情"""
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task

@router.post("", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
def create_task(task: TaskCreate, db: Session = Depends(get_db)):
    """创建检测任务记录"""
    # 验证项目存在
    project = db.query(Project).filter(Project.id == task.project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    db_task = Task(
        project_id=task.project_id,
        model_id=task.model_id,
        input_file=task.input_file,
        step_name=task.step_name
    )
    
    db.add(db_task)
    db.commit()
    db.refresh(db_task)
    
    return db_task

@router.put("/{task_id}", response_model=TaskResponse)
def update_task(
    task_id: int, 
    is_good: Optional[bool] = None,
    confidence: Optional[float] = None,
    duration: Optional[int] = None,
    result_data: Optional[dict] = None,
    result_file: Optional[str] = None,
    error_msg: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """更新任务结果"""
    db_task = db.query(Task).filter(Task.id == task_id).first()
    if not db_task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    if is_good is not None:
        db_task.is_good = is_good
    if confidence is not None:
        db_task.confidence = confidence
    if duration is not None:
        db_task.duration = duration
    if result_data is not None:
        db_task.result_data = result_data
    if result_file is not None:
        db_task.result_file = result_file
    if error_msg is not None:
        db_task.error_msg = error_msg
    
    db.commit()
    db.refresh(db_task)
    
    return db_task

@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(task_id: int, db: Session = Depends(get_db)):
    """删除任务记录"""
    db_task = db.query(Task).filter(Task.id == task_id).first()
    if not db_task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    db.delete(db_task)
    db.commit()
    return None

@router.post("/record", response_model=TaskResponse)
async def record_detection(
    project_id: int = Form(...),
    model_id: Optional[int] = Form(None),
    is_good: bool = Form(True),
    confidence: Optional[float] = Form(None),
    duration: Optional[int] = Form(0),
    step_name: Optional[str] = Form(None),
    result_data: Optional[str] = Form(None),  # JSON string
    image: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db)
):
    """记录一次检测结果（带可选图片上传）"""
    import json
    
    # 验证项目存在
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # 处理图片上传
    input_file = None
    if image:
        unique_filename = f"{uuid.uuid4().hex}_{image.filename}"
        file_path = os.path.join(settings.IMAGE_UPLOAD_DIR, unique_filename)
        try:
            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(image.file, buffer)
            input_file = file_path
        except Exception as e:
            print(f"Failed to save image: {e}")
        finally:
            image.file.close()
    
    # 解析结果数据
    parsed_result_data = None
    if result_data:
        try:
            parsed_result_data = json.loads(result_data)
        except:
            pass
    
    # 创建任务记录
    db_task = Task(
        project_id=project_id,
        model_id=model_id,
        input_file=input_file,
        is_good=is_good,
        confidence=confidence,
        duration=duration,
        step_name=step_name,
        result_data=parsed_result_data
    )
    
    db.add(db_task)
    db.commit()
    db.refresh(db_task)
    
    return db_task

@router.delete("/batch/clear", status_code=status.HTTP_204_NO_CONTENT)
def clear_tasks(
    project_id: Optional[int] = None,
    before_date: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """批量清除任务记录"""
    query = db.query(Task)
    
    if project_id:
        query = query.filter(Task.project_id == project_id)
    if before_date:
        query = query.filter(Task.timestamp < before_date)
    
    query.delete(synchronize_session=False)
    db.commit()
    return None
