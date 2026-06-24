from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import Optional
from backend.core.auth_deps import require_perm
from backend.db.database import get_db
from backend.models.models import Project, Model
from backend.schemas.project import ProjectCreate, ProjectUpdate, ProjectResponse, ProjectListResponse, ProjectPluginDataPatch
from backend.services.project_config_normalize import ensure_sequence_orders

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
        model_labels = None
        if p.default_model_id:
            model = db.query(Model).filter(Model.id == p.default_model_id).first()
            if model:
                model_name = model.name
                model_version = model.version
                model_labels = model.labels or []
        
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
            model_version=model_version,
            model_labels=model_labels
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
    model_labels = None
    if project.default_model_id:
        model = db.query(Model).filter(Model.id == project.default_model_id).first()
        if model:
            model_name = model.name
            model_version = model.version
            model_labels = model.labels or []
    
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
        model_version=model_version,
        model_labels=model_labels
    )

@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED,
              dependencies=[Depends(require_perm("project.create"))])
def create_project(project: ProjectCreate, db: Session = Depends(get_db)):
    """创建新项目"""
    existing = db.query(Project).filter(Project.name == project.name).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"项目名称 '{project.name}' 已存在")

    pipeline_config = ensure_sequence_orders(
        project.pipeline_config,
        project.logic_mode,
        project.steps_config,
    )
    db_project = Project(
        name=project.name,
        task_type=project.task_type,
        pipeline_config=pipeline_config,
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

@router.put("/{project_id}", response_model=ProjectResponse,
             dependencies=[Depends(require_perm("project.edit"))])
def update_project(project_id: int, project: ProjectUpdate, db: Session = Depends(get_db)):
    """更新项目"""
    db_project = db.query(Project).filter(Project.id == project_id).first()
    if not db_project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    update_data = project.model_dump(exclude_unset=True)
    merged_pipeline = dict(db_project.pipeline_config or {})
    if update_data.get('pipeline_config'):
        merged_pipeline.update(update_data['pipeline_config'])
    logic_mode = update_data.get('logic_mode', db_project.logic_mode)
    steps_config = update_data.get('steps_config', db_project.steps_config)
    normalized_pipeline = ensure_sequence_orders(merged_pipeline, logic_mode, steps_config)
    if normalized_pipeline != (db_project.pipeline_config or {}):
        update_data['pipeline_config'] = normalized_pipeline
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

    # v3.9.x: 项目当前是激活态时, 自动把最新配置 push 到运行时 VSM, 客户在 Project
    # 页保存事件配置 / 阈值 / sequence_order 等不再需要"重新激活项目"才能生效.
    # 之前 v3.7.x 只在 activate 路径加了这个同步, 这里把 update 路径也补上.
    # 现象 (此前): 客户勾上"需人工确认", 保存项目, 项目仍在跑 → require_ack 仍是
    # 旧值, 事件触发时不阻塞.
    if db_project.is_active:
        try:
            _sync_project_config_to_channels(db_project)
        except Exception as _e:
            print(f"[update_project] 配置同步到运行时失败 (忽略): {_e}")

    model_name = None
    model_version = None
    model_labels = None
    if db_project.default_model_id:
        model = db.query(Model).filter(Model.id == db_project.default_model_id).first()
        if model:
            model_name = model.name
            model_version = model.version
            model_labels = model.labels or []
    
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
        model_version=model_version,
        model_labels=model_labels
    )

@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT,
                dependencies=[Depends(require_perm("project.delete"))])
def delete_project(project_id: int, db: Session = Depends(get_db)):
    """删除项目"""
    db_project = db.query(Project).filter(Project.id == project_id).first()
    if not db_project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    db.delete(db_project)
    db.commit()
    return None

def _build_project_config_dict(project: Project) -> dict:
    """从 Project ORM 对象构建用于 mgr.set_project_config 的 dict.
    与 backend/main.py::_build_project_config 字段保持一致.
    """
    return {
        'id': project.id,
        'name': project.name,
        'task_type': getattr(project, 'task_type', 'detection'),
        'logic_mode': project.logic_mode,
        'steps_config': project.steps_config or [],
        'pipeline_config': project.pipeline_config or {},
        'events_config': project.events_config or [],
        'counters_config': project.counters_config or [],
        'data_config': project.data_config or {},
    }


def _sync_project_config_to_channels(project: Project) -> None:
    """把项目配置同步到所有未绑定其它项目的 channel 的 mgr.

    v3.7.x 修复: activate 此前只重载模型, 不刷新 mgr.project_config,
    导致客户在 Project 页改完阈值/min_duration/sequence_order 后点保存,
    Monitor 页跑检测仍用旧配置, 必须手动重启检测才能生效 (用户感知为"参数没用").

    与 backend/main.py::auto_load_active_project 的 fallback 分支同源:
      - 已绑定其它 project_id 的通道不动 (多工位各自的项目优先)
      - 其余通道一律调 mgr.set_project_config
      - 任何失败只打日志, 不影响 activate 本身
    """
    from backend.api.channel_manager import channel_manager

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
        print("[激活项目] 所有通道都已绑定其它项目，跳过配置同步")
        return

    config = _build_project_config_dict(project)
    for ch_id in remaining:
        mgr = channel_manager.channels.get(ch_id)
        if not mgr:
            continue
        try:
            mgr.set_project_config(config)
            print(f"[激活项目] ch{ch_id} 配置已同步: '{project.name}' "
                  f"(steps={len(config['steps_config'])}, "
                  f"sequence_order={len(config['pipeline_config'].get('sequence_order', []))} 步)")
        except Exception as e:
            print(f"[激活项目] ch{ch_id} 配置同步失败 (忽略): {e}")
            import traceback; traceback.print_exc()


def _reload_model_for_active_project(db: Session, project: Project) -> None:
    """激活项目后把 default_model 装到未绑定专属项目的通道上。

    逻辑与 backend/main.py::auto_load_active_project 的 fallback 分支保持一致：
    - 已绑定其它 project_id 的通道不动（多工位各自的项目优先）
    - 其余通道全部走 load_model_for_channel（每通道独立实例）
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

    all_ok = True
    for ch_id in remaining:
        ok = channel_manager.load_model_for_channel(ch_id, model.file_path, "auto")
        all_ok = all_ok and ok
        print(f"[激活项目] ch{ch_id} 加载模型 '{model.name}': "
              f"{'成功' if ok else '失败'}")
    if len(remaining) > 1:
        print(f"[激活项目] 多通道独立实例加载结果: channels={remaining}, all_ok={all_ok}")


def activate_project_core(db: Session, project_id: int):
    """激活项目的可复用核心: 设激活态 → 重载模型 → 同步配置 → 清 MES pending → 触发插件 hook。

    供 HTTP 端点 (activate_project) 与外部入站对接 (mes_inbound 按产品码切项目) 共用,
    保证两条路径行为完全一致, 避免逻辑漂移。

    返回 (db_project, model_name, model_version, model_labels)。项目不存在抛 HTTPException 404。
    """
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

    # v3.7.x 修复：activate 之前只重载模型, 不刷新 mgr.project_config, 导致
    # Project 页改完阈值/min_duration/sequence_order 等配置后, Monitor 页仍用旧配置
    # (用户感知为"参数没用"). 此处补上配置同步, 与启动期 auto_load_active_project 行为对齐.
    try:
        _sync_project_config_to_channels(db_project)
    except Exception as e:
        import traceback
        print(f"[激活项目] 配置同步异常 (不影响激活状态): {e}")
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

    model_name = None
    model_version = None
    model_labels = None
    if db_project.default_model_id:
        model = db.query(Model).filter(Model.id == db_project.default_model_id).first()
        if model:
            model_name = model.name
            model_version = model.version
            model_labels = model.labels or []

    # v3.13 M1.1: project_activated 插件 hook — 项目已落库激活 + 模型已重载 + 配置已同步 +
    # MES pending 已清理. 客户级"激活时下发外部系统 / 二次校验配置"在这里挂.
    try:
        from backend.plugin_system.hook_dispatch import fire_plugin_hook
        from backend.api.channel_manager import channel_manager as _cm
        _active_channels = list(_cm.channels.keys()) if hasattr(_cm, "channels") else []
        fire_plugin_hook("project_activated", "post_activate", "post", {
            "project_id": db_project.id,
            "project_name": db_project.name,
            "project_version": getattr(db_project, "version", None),
            "active_channel_ids": _active_channels,
            "model_name": model_name,
            "model_version": model_version,
        })
    except Exception as e:
        print(f"[Plugin] project_activated hook 触发异常 (已隔离, 主流程继续): {e}")

    return db_project, model_name, model_version, model_labels


@router.post("/{project_id}/activate", response_model=ProjectResponse,
              dependencies=[Depends(require_perm("project.activate"))])
def activate_project(project_id: int, db: Session = Depends(get_db)):
    """激活项目（设为当前运行项目）"""
    db_project, model_name, model_version, model_labels = activate_project_core(db, project_id)

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
        model_version=model_version,
        model_labels=model_labels
    )

@router.post("/{project_id}/duplicate", response_model=ProjectResponse,
              status_code=status.HTTP_201_CREATED,
              dependencies=[Depends(require_perm("project.create"))])
def duplicate_project(project_id: int, db: Session = Depends(get_db)):
    """复制项目：把一个项目的全部配置克隆成一个新副本.

    克隆范围: 任务类型 / 逻辑模式 / 默认模型 + 推理格式 + 7 个 JSON 配置字段
    (pipeline / steps / events / counters / alarm / detection / data).
    副本默认 **不激活**, 名称自动加「副本」后缀防重名, 客户复制后可改名继续编辑.

    JSON 字段全部 deepcopy, 避免新旧项目共享同一引用导致改一个动另一个.
    """
    import copy

    src = db.query(Project).filter(Project.id == project_id).first()
    if not src:
        raise HTTPException(status_code=404, detail="Project not found")

    # 生成唯一副本名: "原名 副本" / "原名 副本2" / "原名 副本3" ...
    base_name = f"{src.name} 副本"
    new_name = base_name
    n = 2
    while db.query(Project).filter(Project.name == new_name).first():
        new_name = f"{base_name}{n}"
        n += 1

    db_project = Project(
        name=new_name,
        task_type=src.task_type,
        pipeline_config=copy.deepcopy(src.pipeline_config),
        default_model_id=src.default_model_id,
        model_format=src.model_format or "pytorch_fp32",
        logic_mode=src.logic_mode,
        steps_config=copy.deepcopy(src.steps_config),
        events_config=copy.deepcopy(src.events_config),
        counters_config=copy.deepcopy(src.counters_config),
        alarm_config=copy.deepcopy(src.alarm_config),
        detection_config=copy.deepcopy(src.detection_config),
        data_config=copy.deepcopy(src.data_config),
    )
    db.add(db_project)
    db.commit()
    db.refresh(db_project)

    model_name = None
    model_version = None
    model_labels = None
    if db_project.default_model_id:
        model = db.query(Model).filter(Model.id == db_project.default_model_id).first()
        if model:
            model_name = model.name
            model_version = model.version
            model_labels = model.labels or []

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
        model_version=model_version,
        model_labels=model_labels
    )


_SCOPE_DICT_FIELDS = {
    "pipeline_config",
    "alarm_config",
    "detection_config",
    "data_config",
}
_SCOPE_LIST_FIELDS = {
    "steps_config",
    "events_config",
    "counters_config",
}
_ALLOWED_SCOPES = _SCOPE_DICT_FIELDS | _SCOPE_LIST_FIELDS


def _validate_customer_code(code: str) -> None:
    """校验 customer_code 安全字符. 只允许 ascii alnum + _ + -.

    防止 customer_code 注入特殊字符 (例: 句点 / 斜杠) 破坏 JSON 路径解析,
    或与主程序已有 key 冲突.
    """
    import re
    if not isinstance(code, str) or not code:
        raise HTTPException(status_code=400, detail="customer_code 必填且非空")
    if not re.match(r"^[A-Za-z0-9_-]+$", code):
        raise HTTPException(
            status_code=400,
            detail=f"customer_code='{code}' 含非法字符 (只允许 字母/数字/_/-)",
        )


def _patch_plugin_data_into_dict(field_dict: dict, customer_code: str, patch_data: dict) -> dict:
    """在 dict 类型字段 (pipeline_config/alarm_config/...) 写 plugin_data.<cc>.

    返回新的 dict (浅拷, SQLAlchemy mutation 检测要求).
    """
    new_field = dict(field_dict or {})
    plugin_data = dict(new_field.get("plugin_data") or {})
    existing = dict(plugin_data.get(customer_code) or {})
    existing.update(patch_data)
    plugin_data[customer_code] = existing
    new_field["plugin_data"] = plugin_data
    return new_field


def _patch_plugin_data_into_list(field_list: list, index: int, customer_code: str, patch_data: dict) -> list:
    """在 list 类型字段 (steps_config/events_config/counters_config) 的 [index] 项里写 plugin_data.<cc>.

    返回新的 list (浅拷).
    """
    new_field = list(field_list or [])
    if index < 0 or index >= len(new_field):
        raise HTTPException(
            status_code=400,
            detail=f"index={index} 越界 (字段长度={len(new_field)})",
        )
    item = dict(new_field[index] or {})
    plugin_data = dict(item.get("plugin_data") or {})
    existing = dict(plugin_data.get(customer_code) or {})
    existing.update(patch_data)
    plugin_data[customer_code] = existing
    item["plugin_data"] = plugin_data
    new_field[index] = item
    return new_field


@router.put("/{project_id}/plugin-data", response_model=ProjectResponse,
             dependencies=[Depends(require_perm("project.edit"))])
def patch_project_plugin_data(
    project_id: int,
    patch: ProjectPluginDataPatch,
    db: Session = Depends(get_db),
):
    """精准 PATCH Project 的某个 JSON 字段下的 plugin_data.<customer_code> 子树.

    v3.13 M3.1 新增 — 给客户专属插件存项目级配置 (例: 步骤警告耗时阈值).

    路径定位:
      - scope ∈ dict 字段: 目标 = ``<scope>.plugin_data.<customer_code>``
      - scope ∈ list 字段: 目标 = ``<scope>[index].plugin_data.<customer_code>``

    安全约束:
      - 需要 ``project.edit`` 权限 (与 update_project 一致)
      - scope 必须在白名单内 (7 个 JSON 字段名)
      - list 字段必须给 index, dict 字段必须不给 (或 None)
      - customer_code 只能 字母/数字/_/-
      - data 必须 dict

    合并语义: 浅合并到 ``plugin_data[customer_code]``, 未提及的旧 key 保留;
    其它客户的 plugin_data 子键不动.

    主程序业务代码用 ``.get(key, default)`` 拿自己的键, ``plugin_data`` 子树
    天然透传不污染主 schema (RFC 09 §6.2).
    """
    if patch.scope not in _ALLOWED_SCOPES:
        raise HTTPException(
            status_code=400,
            detail=f"scope='{patch.scope}' 不在白名单 {sorted(_ALLOWED_SCOPES)}",
        )
    _validate_customer_code(patch.customer_code)
    if not isinstance(patch.data, dict):
        raise HTTPException(status_code=400, detail="data 必须是 dict")

    db_project = db.query(Project).filter(Project.id == project_id).first()
    if not db_project:
        raise HTTPException(status_code=404, detail="Project not found")

    is_list_scope = patch.scope in _SCOPE_LIST_FIELDS
    if is_list_scope:
        if patch.index is None:
            raise HTTPException(
                status_code=400,
                detail=f"scope='{patch.scope}' 是 list 字段, index 必填",
            )
        current = getattr(db_project, patch.scope) or []
        new_field = _patch_plugin_data_into_list(
            current, patch.index, patch.customer_code, patch.data,
        )
    else:
        if patch.index is not None:
            raise HTTPException(
                status_code=400,
                detail=f"scope='{patch.scope}' 是 dict 字段, 不应给 index",
            )
        current = getattr(db_project, patch.scope) or {}
        new_field = _patch_plugin_data_into_dict(
            current, patch.customer_code, patch.data,
        )

    # 整字段重赋以触发 SQLAlchemy mutation 检测
    setattr(db_project, patch.scope, new_field)
    db.commit()
    db.refresh(db_project)

    # v3.7.x 风格: 激活态项目同步配置到运行时
    if db_project.is_active:
        try:
            _sync_project_config_to_channels(db_project)
        except Exception as _e:
            print(f"[patch_project_plugin_data] 配置同步到运行时失败 (忽略): {_e}")

    model_name = None
    model_version = None
    model_labels = None
    if db_project.default_model_id:
        model = db.query(Model).filter(Model.id == db_project.default_model_id).first()
        if model:
            model_name = model.name
            model_version = model.version
            model_labels = model.labels or []

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
        model_version=model_version,
        model_labels=model_labels,
    )


@router.get("/active/current", response_model=Optional[ProjectResponse])
def get_active_project(db: Session = Depends(get_db)):
    """获取当前激活的项目"""
    project = db.query(Project).filter(Project.is_active == True).first()
    if not project:
        return None
    
    model_name = None
    model_version = None
    model_labels = None
    if project.default_model_id:
        model = db.query(Model).filter(Model.id == project.default_model_id).first()
        if model:
            model_name = model.name
            model_version = model.version
            model_labels = model.labels or []
    
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
        model_version=model_version,
        model_labels=model_labels
    )
