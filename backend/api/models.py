from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
from sqlalchemy.orm import Session
from typing import List, Optional
import os
import shutil
import uuid
import json
import threading
import queue
import traceback
from backend.db.database import get_db, SessionLocal
from backend.models.models import Model, ModelConversion, Project
from backend.schemas.model import (
    ModelUpdate, ModelResponse, ModelListResponse,
    ConversionRequest, ConversionResponse, ConversionStatusResponse,
    FormatInfo, FormatsAvailableResponse,
)
from backend.core.config import settings

router = APIRouter()

ALLOWED_EXTENSIONS = {'.pt', '.pth', '.onnx', '.engine', '.pkl', '.h5', '.pb'}

# ---------------------------------------------------------------------------
# Model format definitions
# ---------------------------------------------------------------------------
FORMAT_DEFINITIONS = [
    {
        "key": "pytorch_fp32",
        "name": "PyTorch FP32",
        "extension": ".pt",
        "description": "原始模型，兼容性最强，无需转换。速度较慢。",
        "tag": "默认",
        "requires_gpu": False,
    },
    {
        "key": "pytorch_fp16",
        "name": "PyTorch FP16",
        "extension": ".pt",
        "description": "半精度推理，速度约提升50%。极少数情况精度微降。",
        "tag": None,
        "requires_gpu": True,
    },
    {
        "key": "onnx",
        "name": "ONNX Runtime + CUDA",
        "extension": ".onnx",
        "description": "跨平台通用格式，速度约提升1.5-2倍。",
        "tag": None,
        "requires_gpu": False,
    },
    {
        "key": "torchscript",
        "name": "TorchScript + CUDA",
        "extension": ".torchscript",
        "description": "PyTorch 编译格式，速度约提升1.5-2倍。",
        "tag": None,
        "requires_gpu": False,
    },
    {
        "key": "tensorrt_fp32",
        "name": "TensorRT FP32",
        "extension": ".engine",
        "description": "NVIDIA 深度优化，速度约3倍。仅限当前显卡。",
        "tag": None,
        "requires_gpu": True,
    },
    {
        "key": "tensorrt_fp16",
        "name": "TensorRT FP16",
        "extension": ".engine",
        "description": "最佳性价比，速度约4-5倍。仅限当前显卡。",
        "tag": "最快",
        "requires_gpu": True,
    },
    {
        "key": "tensorrt_int8",
        "name": "TensorRT INT8",
        "extension": ".engine",
        "description": "极速模式，速度最快但精度可能明显下降。仅限当前显卡。",
        "tag": "实验性",
        "requires_gpu": True,
    },
]

FORMAT_EXPORT_ARGS = {
    "pytorch_fp32": None,
    "pytorch_fp16": {"format": "torchscript", "half": True},
    "onnx": {"format": "onnx", "simplify": True},
    "torchscript": {"format": "torchscript"},
    "tensorrt_fp32": {"format": "engine"},
    "tensorrt_fp16": {"format": "engine", "half": True},
    "tensorrt_int8": {"format": "engine", "int8": True},
}

# ---------------------------------------------------------------------------
# GPU helper
# ---------------------------------------------------------------------------
def _get_gpu_info():
    """Return (available, name, arch) for the first CUDA device."""
    try:
        import torch
        if not torch.cuda.is_available():
            return False, None, None
        name = torch.cuda.get_device_name(0)
        cap = torch.cuda.get_device_capability(0)
        arch = f"sm_{cap[0]}{cap[1]}"
        return True, name, arch
    except Exception:
        return False, None, None


def _has_tensorrt():
    try:
        import tensorrt  # noqa: F401
        return True
    except ImportError:
        return False


def _has_onnx():
    try:
        import onnx  # noqa: F401
        return True
    except ImportError:
        return False


def _get_trt_diagnosis() -> dict:
    """收集 TensorRT / CUDA / cuDNN 环境诊断信息"""
    info: dict = {
        "cuda_available": False,
        "cuda_version": None,
        "cudnn_version": None,
        "gpu_name": None,
        "gpu_arch": None,
        "gpu_memory_total_mb": None,
        "gpu_memory_free_mb": None,
        "tensorrt_version": None,
        "tensorrt_compatible": None,
        "issues": [],
    }
    try:
        import torch
        info["cuda_available"] = torch.cuda.is_available()
        if not info["cuda_available"]:
            info["issues"].append("CUDA 不可用")
            return info
        info["cuda_version"] = torch.version.cuda
        info["gpu_name"] = torch.cuda.get_device_name(0)
        cap = torch.cuda.get_device_capability(0)
        info["gpu_arch"] = f"sm_{cap[0]}{cap[1]}"
        props = torch.cuda.get_device_properties(0)
        mem_total = getattr(props, 'total_memory', None) or getattr(props, 'total_mem', 0)
        info["gpu_memory_total_mb"] = round(mem_total / 1024 / 1024) if mem_total else None
        mem_free = mem_total - torch.cuda.memory_allocated(0) if mem_total else 0
        try:
            free, total = torch.cuda.mem_get_info(0)
            info["gpu_memory_free_mb"] = round(free / 1024 / 1024)
        except Exception:
            info["gpu_memory_free_mb"] = round(mem_free / 1024 / 1024)
    except Exception as e:
        info["issues"].append(f"PyTorch/CUDA 检测失败: {e}")

    try:
        if hasattr(torch.backends, "cudnn"):
            info["cudnn_version"] = str(torch.backends.cudnn.version())
    except Exception:
        pass

    try:
        import tensorrt as trt
        info["tensorrt_version"] = trt.__version__
        trt_major = int(trt.__version__.split(".")[0])
        cuda_major = int(info["cuda_version"].split(".")[0]) if info["cuda_version"] else 0

        if trt_major >= 10 and cuda_major < 12:
            info["tensorrt_compatible"] = False
            info["issues"].append(
                f"TensorRT {trt.__version__} 需要 CUDA 12.x，"
                f"当前 CUDA {info['cuda_version']}"
            )
        elif trt_major == 8 and cuda_major >= 12:
            info["tensorrt_compatible"] = False
            info["issues"].append(
                "TensorRT 8.x 不兼容 CUDA 12.x，"
                "请升级 TensorRT 到 10.x"
            )
        else:
            info["tensorrt_compatible"] = True

        if info["gpu_memory_total_mb"] and info["gpu_memory_total_mb"] < 6000:
            info["issues"].append(
                f"显存仅 {info['gpu_memory_total_mb']}MB，"
                f"TensorRT 编译可能因显存不足而失败或极慢"
            )
    except ImportError:
        info["tensorrt_version"] = None
        info["issues"].append("TensorRT 未安装")
    except Exception as e:
        info["issues"].append(f"TensorRT 检测失败: {e}")

    return info


# ---------------------------------------------------------------------------
# Conversion queue (serial execution)
# ---------------------------------------------------------------------------
_convert_queue: queue.Queue = queue.Queue()
_convert_lock = threading.Lock()
_convert_thread: Optional[threading.Thread] = None


def _conversion_worker():
    """Background thread that processes conversions one at a time."""
    while True:
        item = _convert_queue.get()
        if item is None:
            break
        conv_id, model_file_path, fmt_key = item
        db = SessionLocal()
        try:
            conv = db.query(ModelConversion).filter(ModelConversion.id == conv_id).first()
            if not conv or conv.status != "queued":
                continue
            conv.status = "converting"
            db.commit()

            from ultralytics import YOLO

            model = YOLO(model_file_path)
            export_args = FORMAT_EXPORT_ARGS.get(fmt_key)
            if export_args is None:
                conv.status = "ready"
                conv.file_path = model_file_path
                conv.file_size = os.path.getsize(model_file_path)
                db.commit()
                continue

            is_trt = fmt_key.startswith("tensorrt")
            diag = _get_trt_diagnosis() if is_trt else {}
            diag_str = ""
            if diag:
                diag_str = (
                    f"  GPU: {diag.get('gpu_name')} ({diag.get('gpu_arch')})\n"
                    f"  显存: {diag.get('gpu_memory_total_mb')}MB 总计, "
                    f"{diag.get('gpu_memory_free_mb')}MB 空闲\n"
                    f"  CUDA: {diag.get('cuda_version')}\n"
                    f"  cuDNN: {diag.get('cudnn_version')}\n"
                    f"  TensorRT: {diag.get('tensorrt_version')}\n"
                    f"  兼容性: {'OK' if diag.get('tensorrt_compatible') else 'FAIL'}\n"
                    f"  问题: {diag.get('issues') or '无'}"
                )

            if is_trt and diag.get("issues"):
                issue_text = "; ".join(diag["issues"])
                conv.status = "failed"
                conv.error_msg = f"环境不兼容: {issue_text}"
                db.commit()
                print(f"[ModelConvert] 环境检查失败:\n{diag_str}", flush=True)
                continue

            if is_trt:
                export_args = dict(export_args)
                export_args["workspace"] = 4

            timeout_sec = 600
            print(
                f"[ModelConvert] 开始 {fmt_key} 转换 (超时 {timeout_sec}s)\n"
                f"{diag_str}" if diag_str else
                f"[ModelConvert] 开始 {fmt_key} 转换 (超时 {timeout_sec}s)",
                flush=True,
            )

            export_result = [None]
            export_error = [None]
            _final_args = dict(export_args) if export_args else {}

            def _do_export():
                try:
                    export_result[0] = model.export(**_final_args)
                except Exception as ex:
                    export_error[0] = ex

            t = threading.Thread(target=_do_export, daemon=True)
            t.start()
            t.join(timeout=timeout_sec)

            if t.is_alive():
                detail = (
                    f"TensorRT 转换超时 ({timeout_sec}s)。\n"
                    f"GPU: {diag.get('gpu_name', '?')}, "
                    f"显存空闲: {diag.get('gpu_memory_free_mb', '?')}MB, "
                    f"TRT: {diag.get('tensorrt_version', '?')}, "
                    f"CUDA: {diag.get('cuda_version', '?')}\n"
                    f"建议: 请尝试 PyTorch FP16 格式，或关闭其他占用 GPU 的程序后重试。"
                ) if diag else (
                    f"转换超时 ({timeout_sec}s)，请尝试其他格式。"
                )
                raise TimeoutError(detail)
            if export_error[0]:
                raise export_error[0]

            exported_path = export_result[0]

            dest_dir = settings.MODEL_CONVERTED_DIR
            os.makedirs(dest_dir, exist_ok=True)
            ext = os.path.splitext(exported_path)[1]
            gpu_suffix = f"_{conv.gpu_arch}" if conv.gpu_arch else ""
            dest_name = f"{conv.model_id}_{fmt_key}{gpu_suffix}{ext}"
            dest_path = os.path.join(dest_dir, dest_name)
            if os.path.abspath(exported_path) != os.path.abspath(dest_path):
                shutil.move(exported_path, dest_path)

            conv.file_path = dest_path
            conv.file_size = os.path.getsize(dest_path)
            conv.status = "ready"
            db.commit()
            print(f"[ModelConvert] {fmt_key} 转换完成: {dest_path}")

        except Exception as e:
            print(f"[ModelConvert] 转换失败: {e}")
            traceback.print_exc()
            try:
                conv = db.query(ModelConversion).filter(ModelConversion.id == conv_id).first()
                if conv:
                    conv.status = "failed"
                    conv.error_msg = str(e)[:500]
                    db.commit()
            except Exception:
                pass
        finally:
            db.close()
            _convert_queue.task_done()


def _ensure_worker():
    global _convert_thread
    with _convert_lock:
        if _convert_thread is None or not _convert_thread.is_alive():
            _convert_thread = threading.Thread(target=_conversion_worker, daemon=True)
            _convert_thread.start()

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
                        except Exception:
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


# Static-path routes MUST be registered before /{model_id} to avoid
# FastAPI matching "formats" or "conversions" as a model_id parameter.

@router.get("/formats/available", response_model=FormatsAvailableResponse)
def get_available_formats():
    """返回所有模型格式及当前环境可用性，附带智能推荐"""
    gpu_avail, gpu_name, gpu_arch = _get_gpu_info()
    has_trt = _has_tensorrt()
    has_onnx = _has_onnx()

    formats = []
    for fd in FORMAT_DEFINITIONS:
        available = True
        reason = None
        if fd["requires_gpu"] and not gpu_avail:
            available = False
            reason = "需要 NVIDIA GPU"
        if fd["key"].startswith("tensorrt") and not has_trt:
            available = False
            reason = "TensorRT 未安装"
        if fd["key"] == "onnx" and not has_onnx:
            available = False
            reason = "onnx 模块未安装"
        formats.append(FormatInfo(
            key=fd["key"],
            name=fd["name"],
            extension=fd["extension"],
            description=fd["description"],
            tag=fd.get("tag"),
            available=available,
            unavailable_reason=reason,
        ))

    recommended = "pytorch_fp32"
    if gpu_avail:
        if has_trt:
            recommended = "tensorrt_fp16"
        else:
            recommended = "pytorch_fp16"
    elif has_onnx:
        recommended = "onnx"

    return FormatsAvailableResponse(
        formats=formats,
        recommended=recommended,
        gpu_name=gpu_name,
        gpu_available=gpu_avail,
    )


@router.get("/formats/diagnosis")
def get_format_diagnosis():
    """返回 GPU / TensorRT / CUDA 环境诊断信息，帮助排查转换失败"""
    return _get_trt_diagnosis()


@router.get("/conversions/{conv_id}/status", response_model=ConversionStatusResponse)
def get_conversion_status(conv_id: int, db: Session = Depends(get_db)):
    """查询单个转换任务的状态"""
    conv = db.query(ModelConversion).filter(ModelConversion.id == conv_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversion not found")
    return conv


@router.delete("/conversions/{conv_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversion(conv_id: int, db: Session = Depends(get_db)):
    """删除一个转换记录及其文件"""
    conv = db.query(ModelConversion).filter(ModelConversion.id == conv_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversion not found")
    if conv.file_path and os.path.exists(conv.file_path):
        try:
            os.remove(conv.file_path)
        except Exception as e:
            print(f"[ModelConvert] 删除文件失败: {e}")
    db.delete(conv)
    db.commit()
    return None


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
    
    # 删除所有转换文件
    for conv in db.query(ModelConversion).filter(ModelConversion.model_id == model_id).all():
        if conv.file_path and os.path.exists(conv.file_path):
            try:
                os.remove(conv.file_path)
            except Exception:
                pass

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


@router.post("/{model_id}/convert", response_model=ConversionResponse)
def convert_model(model_id: int, req: ConversionRequest, db: Session = Depends(get_db)):
    """发起模型格式转换。已有相同转换时直接复用。"""
    db_model = db.query(Model).filter(Model.id == model_id).first()
    if not db_model:
        raise HTTPException(status_code=404, detail="Model not found")
    if not os.path.exists(db_model.file_path):
        raise HTTPException(status_code=404, detail="Model file not found on disk")

    fmt_key = req.format
    if fmt_key not in FORMAT_EXPORT_ARGS:
        raise HTTPException(status_code=400, detail=f"Unknown format: {fmt_key}")

    if fmt_key == "pytorch_fp32":
        raise HTTPException(status_code=400, detail="pytorch_fp32 无需转换")

    gpu_avail, gpu_name, gpu_arch = _get_gpu_info()
    is_tensorrt = fmt_key.startswith("tensorrt")

    if is_tensorrt:
        if not gpu_avail:
            raise HTTPException(status_code=400, detail="TensorRT 需要 NVIDIA GPU")
        if not _has_tensorrt():
            raise HTTPException(status_code=400, detail="TensorRT 未安装")

    lookup_arch = gpu_arch if is_tensorrt else None

    existing = db.query(ModelConversion).filter(
        ModelConversion.model_id == model_id,
        ModelConversion.format == fmt_key,
        ModelConversion.gpu_arch == lookup_arch,
    ).first()
    if existing:
        if existing.status == "ready":
            if existing.file_path and os.path.exists(existing.file_path):
                return existing
            existing.status = "queued"
            db.commit()
        elif existing.status in ("queued", "converting"):
            return existing
        elif existing.status == "failed":
            existing.status = "queued"
            existing.error_msg = None
            db.commit()

        _ensure_worker()
        _convert_queue.put((existing.id, db_model.file_path, fmt_key))
        return existing

    conv = ModelConversion(
        model_id=model_id,
        format=fmt_key,
        file_path="",
        gpu_name=gpu_name if is_tensorrt else None,
        gpu_arch=lookup_arch,
        status="queued",
    )
    db.add(conv)
    db.commit()
    db.refresh(conv)

    _ensure_worker()
    _convert_queue.put((conv.id, db_model.file_path, fmt_key))

    return conv


@router.get("/{model_id}/conversions", response_model=List[ConversionResponse])
def get_model_conversions(model_id: int, db: Session = Depends(get_db)):
    """列出该模型的所有已有转换版本"""
    return db.query(ModelConversion).filter(
        ModelConversion.model_id == model_id
    ).order_by(ModelConversion.created_at.desc()).all()


@router.post("/{model_id}/resolve-path")
def resolve_model_path(
    model_id: int,
    format: str = "pytorch_fp32",
    db: Session = Depends(get_db),
):
    """根据格式返回实际应加载的模型文件路径。用于检测启动时。

    - pytorch_fp32: 直接返回原始 .pt 路径
    - 其他格式: 查找转换记录, TensorRT 额外校验 GPU
    """
    db_model = db.query(Model).filter(Model.id == model_id).first()
    if not db_model:
        raise HTTPException(status_code=404, detail="Model not found")

    original_path = db_model.file_path

    if format == "pytorch_fp32":
        return {"path": original_path, "original_path": original_path, "fallback": False}

    is_tensorrt = format.startswith("tensorrt")
    gpu_avail, gpu_name, gpu_arch = _get_gpu_info()
    lookup_arch = gpu_arch if is_tensorrt else None

    conv = db.query(ModelConversion).filter(
        ModelConversion.model_id == model_id,
        ModelConversion.format == format,
        ModelConversion.gpu_arch == lookup_arch,
    ).first()

    if conv and conv.status == "ready" and conv.file_path and os.path.exists(conv.file_path):
        return {"path": conv.file_path, "original_path": original_path, "fallback": False}

    return {"path": original_path, "original_path": original_path, "fallback": True,
            "reason": "转换模型不可用，已回退到原始模型"}
