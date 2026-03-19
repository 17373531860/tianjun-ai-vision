from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
from sqlalchemy.orm import Session
from typing import List, Optional
import os
import shutil
import uuid
import json
from datetime import datetime
from backend.db.database import get_db
from backend.models.models import Model, Project
from backend.schemas.model import ModelCreate, ModelUpdate, ModelResponse, ModelListResponse
from backend.core.config import settings

router = APIRouter()

ALLOWED_EXTENSIONS = {'.pt', '.pth', '.onnx', '.engine', '.pkl', '.h5', '.pb'}

def get_file_extension(filename: str) -> str:
    return os.path.splitext(filename)[1].lower()

def parse_model_labels(file_path: str, framework: str) -> Optional[List[str]]:
    """解析模型文件获取标签列表"""
    ext = get_file_extension(file_path)
    labels = None
    
    try:
        # PyTorch YOLO 模型 (.pt)
        if ext in ['.pt', '.pth'] and framework.lower() in ['pytorch', 'yolo', 'ultralytics']:
            try:
                from ultralytics import YOLO
                model = YOLO(file_path)
                if hasattr(model, 'names') and model.names:
                    # model.names 是一个字典 {0: 'class1', 1: 'class2', ...}
                    labels = list(model.names.values())
                    print(f"[Model Parser] 从 YOLO 模型解析到 {len(labels)} 个标签: {labels}")
            except ImportError:
                print("[Model Parser] ultralytics 未安装，跳过 YOLO 模型解析")
            except Exception as e:
                print(f"[Model Parser] YOLO 解析失败: {e}")
                # 尝试直接用 torch 加载
                try:
                    import torch
                    checkpoint = torch.load(file_path, map_location='cpu')
                    # 尝试从 checkpoint 中提取 names
                    if isinstance(checkpoint, dict):
                        if 'names' in checkpoint:
                            names = checkpoint['names']
                            labels = list(names.values()) if isinstance(names, dict) else list(names)
                        elif 'model' in checkpoint and hasattr(checkpoint['model'], 'names'):
                            labels = list(checkpoint['model'].names.values())
                    print(f"[Model Parser] 从 torch checkpoint 解析到标签: {labels}")
                except Exception as e2:
                    print(f"[Model Parser] torch 解析也失败: {e2}")
        
        # ONNX 模型
        elif ext == '.onnx':
            try:
                import onnx
                model = onnx.load(file_path)
                # 尝试从 metadata 中获取类别信息
                for prop in model.metadata_props:
                    if prop.key == 'names' or prop.key == 'classes':
                        try:
                            labels = json.loads(prop.value)
                            if isinstance(labels, dict):
                                labels = list(labels.values())
                        except:
                            labels = prop.value.split(',')
                print(f"[Model Parser] 从 ONNX 模型解析到标签: {labels}")
            except ImportError:
                print("[Model Parser] onnx 未安装，跳过 ONNX 模型解析")
            except Exception as e:
                print(f"[Model Parser] ONNX 解析失败: {e}")
                
    except Exception as e:
        print(f"[Model Parser] 解析模型标签时出错: {e}")
    
    return labels

@router.get("", response_model=ModelListResponse)
def get_models(
    skip: int = 0,
    limit: int = 100,
    project_id: Optional[int] = None,
    db: Session = Depends(get_db)
):
    """获取模型列表"""
    query = db.query(Model)
    if project_id:
        query = query.filter(Model.project_id == project_id)
    
    total = query.count()
    models = query.offset(skip).limit(limit).all()
    
    return ModelListResponse(total=total, items=models)

@router.get("/{model_id}", response_model=ModelResponse)
def get_model(model_id: int, db: Session = Depends(get_db)):
    """获取模型详情"""
    model = db.query(Model).filter(Model.id == model_id).first()
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")
    return model

@router.post("/upload", response_model=ModelResponse, status_code=status.HTTP_201_CREATED)
async def upload_model(
    file: UploadFile = File(...),
    name: str = Form(...),
    description: Optional[str] = Form(None),
    version: Optional[str] = Form(None),
    project_id: Optional[int] = Form(None),
    framework: str = Form("PyTorch"),
    db: Session = Depends(get_db)
):
    """上传模型文件"""
    existing = db.query(Model).filter(
        Model.name == name,
        Model.version == (version or None)
    ).first()
    if existing:
        v = version or '未填写'
        raise HTTPException(status_code=400, detail=f"模型 '{name}' 版本 '{v}' 已存在，请使用不同的版本号")

    ext = get_file_extension(file.filename)
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400, 
            detail=f"File type not allowed. Allowed types: {', '.join(ALLOWED_EXTENSIONS)}"
        )
    
    # 验证项目存在
    if project_id:
        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
    
    # 生成唯一文件名
    unique_filename = f"{uuid.uuid4().hex}_{file.filename}"
    file_path = os.path.join(settings.MODEL_UPLOAD_DIR, unique_filename)
    
    # 保存文件
    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        file_size = os.path.getsize(file_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save file: {str(e)}")
    finally:
        file.file.close()
    
    # 解析模型标签
    labels = parse_model_labels(file_path, framework)
    # 注意：SQLAlchemy JSON 类型会自动处理序列化，不需要 json.dumps
    
    # 创建数据库记录
    db_model = Model(
        name=name,
        file_path=file_path,
        file_name=file.filename,
        file_size=file_size,
        framework=framework,
        description=description,
        version=version,
        project_id=project_id,
        labels=labels  # 直接存储 list，SQLAlchemy JSON 会自动处理
    )
    
    db.add(db_model)
    db.commit()
    db.refresh(db_model)
    
    return db_model

@router.post("/{model_id}/parse-labels", response_model=ModelResponse)
def parse_model_labels_api(model_id: int, db: Session = Depends(get_db)):
    """重新解析模型标签"""
    db_model = db.query(Model).filter(Model.id == model_id).first()
    if not db_model:
        raise HTTPException(status_code=404, detail="Model not found")
    
    if not os.path.exists(db_model.file_path):
        raise HTTPException(status_code=404, detail="Model file not found")
    
    labels = parse_model_labels(db_model.file_path, db_model.framework)
    if labels:
        db_model.labels = labels  # 直接存储 list，SQLAlchemy JSON 会自动处理
        db.commit()
        db.refresh(db_model)
    else:
        raise HTTPException(status_code=400, detail="Failed to parse labels from model")
    
    return db_model

@router.put("/{model_id}", response_model=ModelResponse)
def update_model(model_id: int, model: ModelUpdate, db: Session = Depends(get_db)):
    """更新模型信息"""
    db_model = db.query(Model).filter(Model.id == model_id).first()
    if not db_model:
        raise HTTPException(status_code=404, detail="Model not found")
    
    update_data = model.model_dump(exclude_unset=True)
    new_name = update_data.get('name', db_model.name)
    new_version = update_data.get('version', db_model.version)
    if 'name' in update_data or 'version' in update_data:
        existing = db.query(Model).filter(
            Model.name == new_name,
            Model.version == new_version,
            Model.id != model_id
        ).first()
        if existing:
            v = new_version or '未填写'
            raise HTTPException(status_code=400, detail=f"模型 '{new_name}' 版本 '{v}' 已存在")

    for key, value in update_data.items():
        setattr(db_model, key, value)
    
    db.commit()
    db.refresh(db_model)
    
    return db_model

@router.delete("/{model_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_model(model_id: int, db: Session = Depends(get_db)):
    """删除模型"""
    db_model = db.query(Model).filter(Model.id == model_id).first()
    if not db_model:
        raise HTTPException(status_code=404, detail="Model not found")
    
    # 删除文件
    if os.path.exists(db_model.file_path):
        try:
            os.remove(db_model.file_path)
        except Exception as e:
            print(f"Failed to delete file: {e}")
    
    # 清除关联项目的默认模型
    db.query(Project).filter(Project.default_model_id == model_id).update(
        {Project.default_model_id: None}
    )
    
    db.delete(db_model)
    db.commit()
    return None

@router.post("/{model_id}/set-active", response_model=ModelResponse)
def set_model_active(model_id: int, db: Session = Depends(get_db)):
    """将模型设为当前使用"""
    db_model = db.query(Model).filter(Model.id == model_id).first()
    if not db_model:
        raise HTTPException(status_code=404, detail="Model not found")
    
    # 取消同项目下其他模型的激活状态
    if db_model.project_id:
        db.query(Model).filter(
            Model.project_id == db_model.project_id,
            Model.id != model_id
        ).update({Model.status: "idle"})
    
    db_model.status = "active"
    db.commit()
    db.refresh(db_model)
    
    return db_model
