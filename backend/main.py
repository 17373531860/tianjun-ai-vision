import os as _bootstrap_os
# v3.1.3: 在 cv2 / ffmpeg 被(间接)导入之前就锁定单线程解码,
# 防止视频文件回放偶发的 libavcodec pthread_frame.c:175 断言把 worker 整个 abort 掉
_bootstrap_os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "threads;1")
# OpenCV MSMF defaults to probing hardware color transforms.  On the Jicai
# UVC camera this makes VideoCapture open and every width/height/FPS set()
# renegotiate for several seconds.  Disable that probe before cv2 is imported;
# frame delivery and UVC exposure controls remain handled by the MSMF backend.
if _bootstrap_os.name == "nt":
    _bootstrap_os.environ.setdefault("OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS", "0")

# Force UTF-8 terminal output so any launch path (Electron / manual cmd / redirect)
# stays consistent and avoids Windows GBK-console garbling UTF-8 bytes.
# Electron already sets chcp 65001 + PYTHONIOENCODING; this is the bottom-line guard
# for the manual-cmd / log-redirect case the field hit (logs showed mojibake).
import sys as _bootstrap_sys
if _bootstrap_os.name == "nt":
    try:
        _bootstrap_os.system("chcp 65001 >nul 2>&1")
    except Exception:
        pass
for _bootstrap_stream in (_bootstrap_sys.stdout, _bootstrap_sys.stderr):
    try:
        _bootstrap_stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse, Response
from backend.core.config import settings
from backend.db.database import engine, Base
# v3.5.0 自定义导出系统：必须在 create_all 之前 import 让表注册到 Base.metadata
from backend.models import export_models  # noqa: F401
from backend.models import plugin_models  # noqa: F401
# v3.13 RFC 10: ChannelGroup 表 (单机内多通道结算联动); ChannelGroup ORM 在 models.py 中
from backend.models import models as _models  # noqa: F401  (兜底显式 import, 与 export_models 等保持一致风格)
# v3.10.0 用户系统: User/Role/UserRole/SessionToken 四张表
# 必须在 create_all 之前 import 让表注册到 Base.metadata
from backend.models import auth_models  # noqa: F401
# v3.14 RFC 11: WorkpieceFlow 表 (流水线串行结算). mes_models 已被其他路径间接 import,
# 这里显式声明仅为可读性与启动顺序一致.
from backend.models import mes_models as _mes_models  # noqa: F401
# 原生称重投料模式逐件记录表 (6.1 台账持久化, 重启不丢)
from backend.models import weighing_models as _weighing_models  # noqa: F401
# 每日短信日报: SmsReportRule/SmsSendLog/CounterDailyStat 三张表
from backend.models import notify_models as _notify_models  # noqa: F401
# 路由挂载统一走 router_manifest（OVERLAP-3 治理）; 这里只保留非路由用途的 import
from backend.api.router_manifest import mount_all_routers
from backend.api.source import get_video_manager
# Import models to ensure they are registered
import os
import cv2
import atexit
import signal
import threading
from sqlalchemy import text
from backend.core import debug_center

# ===== Startup diagnostics =====
from backend.core.config import BASE_DIR, DATA_DIR
from backend.db.database import get_dialect as _get_dialect
_DIALECT = _get_dialect()
print(f"[DIAG] main.py: dialect = {_DIALECT}")
print(f"[DIAG] main.py: DB URI = {os.environ.get('DATABASE_URL') or settings.SQLALCHEMY_DATABASE_URI}")
print(f"[DIAG] main.py: MODEL_UPLOAD_DIR = {settings.MODEL_UPLOAD_DIR}")
if _DIALECT == "sqlite":
    _db_path = os.path.join(DATA_DIR, 'sql_app.db')
    print(f"[DIAG] main.py: DB file exists before create_all: {os.path.exists(_db_path)}")
    if os.path.exists(_db_path):
        print(f"[DIAG] main.py: DB file size: {os.path.getsize(_db_path)} bytes")

# Create database tables
Base.metadata.create_all(bind=engine)
if _DIALECT == "sqlite":
    print(f"[DIAG] main.py: create_all done, DB file size: {os.path.getsize(_db_path) if os.path.exists(_db_path) else 'N/A'}")
else:
    print("[DIAG] main.py: create_all done")

# ==================== 数据库迁移（2026-07 版本化治理后） ====================
# 迁移逻辑已平移到 backend/db/migrations/（m0000_legacy = 原 109 条补列 +
# operators 清理, 逐字冻结; 新 schema 变更逐版本新建 mXXXX_*.py）。
# 详见 docs/rfc/DB迁移版本化治理_设计方案_RFC.md 与包内 __init__.py 说明。
def migrate_database():
    """断言桩：防止旧习惯回流——不要再往这里塞 ALTER TABLE。"""
    raise RuntimeError(
        "migrate_database() 已退役: 迁移逻辑在 backend/db/migrations/, "
        "新增 schema 变更请新建 mXXXX_*.py 并注册 (AGENTS.md 不变量 8)")

def fix_orphan_sessions():
    """修复孤立的会话（服务器重启后，之前运行中的会话应该标记为已中断）"""
    from backend.models.models import DetectionSession, DetectionCycle
    from sqlalchemy.orm import Session
    
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


def cleanup_orphan_inspections():
    """清理工件检测记录中指向不存在 Cycle/Session 的孤儿引用

    历史数据中 WorkpieceInspection 与 DetectionCycle 之间没有强外键，
    cycle 被删除时不会级联清理 inspection.cycle_id，导致越积越多的孤儿。
    本函数把这些孤儿引用置为 NULL（保留 inspection 痕迹但断开错误链接），
    并打印数量供运维参考。
    """
    from sqlalchemy import text
    try:
        with engine.connect() as conn:
            try:
                rs = conn.execute(text("""
                    SELECT COUNT(*) FROM workpiece_inspections wi
                    WHERE wi.cycle_id IS NOT NULL
                      AND NOT EXISTS (SELECT 1 FROM detection_cycles dc WHERE dc.id = wi.cycle_id)
                """)).fetchone()
                orphan_cycle = rs[0] if rs else 0
            except Exception:
                orphan_cycle = 0

            try:
                rs = conn.execute(text("""
                    SELECT COUNT(*) FROM workpiece_inspections wi
                    WHERE wi.session_id IS NOT NULL
                      AND NOT EXISTS (SELECT 1 FROM detection_sessions ds WHERE ds.id = wi.session_id)
                """)).fetchone()
                orphan_session = rs[0] if rs else 0
            except Exception:
                orphan_session = 0

            try:
                rs = conn.execute(text("""
                    SELECT COUNT(*) FROM defect_records dr
                    WHERE dr.cycle_id IS NOT NULL
                      AND NOT EXISTS (SELECT 1 FROM detection_cycles dc WHERE dc.id = dr.cycle_id)
                """)).fetchone()
                orphan_defect = rs[0] if rs else 0
            except Exception:
                orphan_defect = 0

            if orphan_cycle or orphan_session or orphan_defect:
                print(
                    f"[孤儿清理] 发现 inspection→cycle:{orphan_cycle} inspection→session:{orphan_session} "
                    f"defect→cycle:{orphan_defect} 条孤儿引用，置 NULL"
                )
                conn.execute(text("""
                    UPDATE workpiece_inspections SET cycle_id = NULL
                    WHERE cycle_id IS NOT NULL
                      AND NOT EXISTS (SELECT 1 FROM detection_cycles dc WHERE dc.id = workpiece_inspections.cycle_id)
                """))
                conn.execute(text("""
                    UPDATE workpiece_inspections SET session_id = NULL
                    WHERE session_id IS NOT NULL
                      AND NOT EXISTS (SELECT 1 FROM detection_sessions ds WHERE ds.id = workpiece_inspections.session_id)
                """))
                conn.execute(text("""
                    UPDATE defect_records SET cycle_id = NULL
                    WHERE cycle_id IS NOT NULL
                      AND NOT EXISTS (SELECT 1 FROM detection_cycles dc WHERE dc.id = defect_records.cycle_id)
                """))
                conn.commit()
    except Exception as _e:
        print(f"[孤儿清理] 跳过（表可能不存在）: {_e}")


def migrate_data_to_external_dir():
    """Migrate data from old install directory to external data directory.

    仅当当前 DSN 为 SQLite 时执行（PostgreSQL 部署不需要本地文件搬迁）。
    """
    from backend.db.database import get_dialect
    if get_dialect() != "sqlite":
        return
    from backend.core.config import DATA_DIR, _is_empty_db, _fix_db_paths
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
                print("[数据迁移] 数据库已迁移")
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

def _fixup_stale_paths():
    from backend.db.database import get_dialect
    if get_dialect() != "sqlite":
        return
    from backend.core.config import DATA_DIR, _fix_db_paths
    if os.path.abspath(DATA_DIR) == os.path.abspath(BASE_DIR):
        return
    db_path = os.path.join(DATA_DIR, 'sql_app.db')
    if os.path.exists(db_path):
        print(f"[DIAG] main.py: running _fixup_stale_paths on {db_path}")
        _fix_db_paths(db_path, BASE_DIR, DATA_DIR)

if not os.environ.get("BACKEND_SKIP_INIT"):
    print("[DIAG] main.py: running migrate_data_to_external_dir()")
    migrate_data_to_external_dir()
    _fixup_stale_paths()

# Post-migration DB health check
def _diag_db_health():
    """Log counts from key tables so we can verify data survived migration.

    使用 SQLAlchemy 通用写法，兼容 SQLite / PostgreSQL。
    """
    from sqlalchemy import inspect
    try:
        insp = inspect(engine)
        existing = set(insp.get_table_names())
    except Exception as e:
        print(f"[DIAG] DB health: inspect 失败: {e}")
        return
    try:
        with engine.connect() as conn:
            for table in ('projects', 'models', 'detection_sessions', 'detection_cycles', 'step_records', 'video_clips'):
                if table not in existing:
                    print(f"[DIAG] DB health: {table} = <table not found>")
                    continue
                try:
                    count = conn.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar()
                    print(f"[DIAG] DB health: {table} = {count} rows")
                except Exception as e:
                    print(f"[DIAG] DB health: {table} 读取失败: {e}")
            if 'models' in existing:
                try:
                    rows = conn.execute(text('SELECT id, name, file_path FROM models')).fetchall()
                    for mid, mname, mpath in rows:
                        exists = os.path.isfile(mpath) if mpath else False
                        print(f"[DIAG] Model #{mid} '{mname}': path={mpath}, file_exists={exists}")
                except Exception as _e:
                    print(f"[DIAG] models 表读取失败（已忽略）: {_e}", flush=True)
    except Exception as e:
        print(f"[DIAG] DB health check failed: {e}")

def _seed_export_builtin_templates():
    """v3.5.0: 插入/更新系统预设导出模板（is_system=True）

    调用 backend.services.export_seed.seed_builtin_templates() 完成。
    每次启动都会刷新一遍内置模板的 content / format（保证升级后用户拿到最新的预设），
    但不会覆盖用户复制后的自建模板。失败仅打日志，不阻塞启动。
    """
    try:
        from backend.services.export_seed import seed_builtin_templates
        seed_builtin_templates()
    except Exception as e:
        print(f"[Export] 系统预设模板初始化失败: {e}")
        import traceback
        traceback.print_exc()


def _seed_auth_builtin_roles():
    """v3.10.0 用户系统: 启动种子三个内置角色 (admin / engineer / operator).

    幂等: 已存在则刷新 name/description/permissions, 不存在则插入.
    内置角色的 is_builtin=True, 不可删 (api/roles.py 检查).
    种子失败不阻塞启动, 仅打日志.

    重要约束: 不种任何账号 (User) — 客户首次启用账号鉴权时
    通过 POST /api/v1/auth/enable-auth 自己创建第一个管理员.
    """
    try:
        from backend.core.permissions import BUILTIN_ROLES
        from backend.models.auth_models import Role
        from sqlalchemy.orm import Session as DBSession

        with DBSession(engine) as db:
            for code, info in BUILTIN_ROLES.items():
                role = db.query(Role).filter(Role.code == code).first()
                if role is None:
                    role = Role(
                        code=code,
                        name=info["name"],
                        description=info.get("description"),
                        permissions=list(info["permissions"]),
                        is_builtin=True,
                    )
                    db.add(role)
                    print(f"[Auth] 种子内置角色: {code}")
                else:
                    # 已存在: 刷新名称/描述/默认权限 (客户可能改过, 但首次升级时 description 字段为 NULL)
                    role.name = info["name"]
                    role.description = info.get("description")
                    role.is_builtin = True
                    # 仅 permissions 为空时刷新, 避免覆盖客户自定义
                    if not role.permissions:
                        role.permissions = list(info["permissions"])
            db.commit()
    except Exception as e:
        print(f"[Auth] 内置角色种子失败 (忽略): {e}")
        import traceback
        traceback.print_exc()


def _restore_session_tokens():
    """v3.10.0 启动恢复内存 token 缓存 (落盘的 session_tokens 表回填到 _token_cache)"""
    try:
        from backend.core.auth import load_tokens_from_disk
        load_tokens_from_disk()
    except Exception as e:
        print(f"[Auth] token 启动恢复失败 (忽略): {e}")


def _run_startup_init():
    """统一启动初始化：诊断、迁移、孤儿清理

    包成函数好处:
    1. 测试场景可设 BACKEND_SKIP_INIT=1 跳过, 避免 import 即触发副作用
    2. 失败时单一入口便于排查/补救
    3. 顺序集中可控
    """
    _diag_db_health()
    from backend.db.migrations import apply_pending
    apply_pending(engine)
    fix_orphan_sessions()
    cleanup_orphan_inspections()
    _seed_export_builtin_templates()
    # v3.10.0 用户系统: 种子三个内置角色 + 从落盘恢复内存 token 缓存
    _seed_auth_builtin_roles()
    _restore_session_tokens()
    # 注意: active 插件加载不能放在这里 — 这里 FastAPI app 尚未创建,
    # 插件 register_plugin 需要 app 引用挂 router。移到 main.py 末尾 app
    # 和所有内置 router/static mount 完毕之后 (见 _load_active_plugin_after_app)。

if not os.environ.get("BACKEND_SKIP_INIT"):
    _run_startup_init()

def _build_project_config(project) -> dict:
    """从 Project ORM 对象构建 config dict"""
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


def auto_load_active_project():
    """Backend startup: auto-load projects per channel.

    优先级:
    1. workstation_config.json 里每通道的 project_id（多工位各自绑定不同项目）
    2. 全局 is_active 项目（作为没有绑定的通道的兜底）
    """
    from backend.models.models import Project, Model
    from backend.api.channel_manager import channel_manager
    from sqlalchemy.orm import Session as DBSession
    import os
    try:
        sources = channel_manager.get_channel_sources()
        with DBSession(engine) as db:
            fallback_project = db.query(Project).filter(Project.is_active == True).first()

            loaded_channels = set()
            for ch_str, ch_cfg in sources.items():
                ch_id = int(ch_str)
                pid = ch_cfg.get("project_id")
                if not pid:
                    continue
                mgr = channel_manager.channels.get(ch_id)
                if not mgr:
                    continue
                proj = db.query(Project).filter(Project.id == pid).first()
                if not proj:
                    print(f"[启动] ch{ch_id} 绑定的项目 id={pid} 不存在，跳过")
                    continue
                config = _build_project_config(proj)
                mgr.set_project_config(config)
                loaded_channels.add(ch_id)
                print(f"[启动] ch{ch_id} 加载绑定项目: {proj.name} (id={proj.id})")

                if proj.default_model_id:
                    model = db.query(Model).filter(Model.id == proj.default_model_id).first()
                    if model and model.file_path and os.path.exists(model.file_path):
                        success = channel_manager.load_model_for_channel(ch_id, model.file_path,
                                                                         ch_cfg.get("gpu_device", "auto"))
                        if success:
                            print(f"[启动] ch{ch_id} 加载模型: {model.name}")
                        else:
                            print(f"[启动] ch{ch_id} 模型加载失败: {model.file_path}")

            remaining = [cid for cid in channel_manager.channels if cid not in loaded_channels]
            if remaining and fallback_project:
                config = _build_project_config(fallback_project)
                for ch_id in remaining:
                    channel_manager.channels[ch_id].set_project_config(config)
                print(f"[启动] 兜底: 激活项目 '{fallback_project.name}' 加载到通道 {remaining}")

                if fallback_project.default_model_id:
                    model = db.query(Model).filter(Model.id == fallback_project.default_model_id).first()
                    if model and model.file_path and os.path.exists(model.file_path):
                        all_ok = True
                        for ch_id in remaining:
                            ok = channel_manager.load_model_for_channel(ch_id, model.file_path)
                            all_ok = all_ok and ok
                            print(f"[启动] 兜底: ch{ch_id} 模型 '{model.name}' "
                                  f"{'加载成功' if ok else '加载失败'}")
                        print(f"[启动] 兜底: 模型 '{model.name}' 独立实例加载到通道 {remaining}, all_ok={all_ok}")
            elif not fallback_project and not loaded_channels:
                print("[启动] 没有激活的项目，也没有通道绑定项目，跳过自动加载")
    except Exception as e:
        print(f"[启动] 自动加载项目失败: {e}")
        import traceback; traceback.print_exc()

if not os.environ.get("BACKEND_SKIP_INIT"):
    auto_load_active_project()

# v3.15.1: CUDA 预热 — 把 torch CUDA 上下文首次初始化的开销移到启动期 (splash 期间).
# 现场问题: 项目未激活时启动全程不碰 GPU, 用户进设置页首次查 GPU 列表才触发 CUDA
# 冷初始化, 叠加 onMounted 并发请求导致偶发 "获取GPU列表失败". 后台线程 fire-and-forget,
# 幂等 (已加载模型则秒返回), 无 GPU 环境自动跳过, 异常隔离不影响主流程.
if not os.environ.get("BACKEND_SKIP_INIT"):
    def _warmup_cuda():
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.init()
                _ = torch.cuda.device_count()
                print("[启动] CUDA 预热完成")
        except Exception as _e:
            print(f"[启动] CUDA 预热跳过 (隔离, 不影响主流程): {_e}")
    import threading as _warmup_threading
    _warmup_threading.Thread(target=_warmup_cuda, daemon=True, name="cuda-warmup").start()

# v3.13 RFC 10: 启动时加载工位组配置到 Coordinator. 客户场景 (双工位联动)
# 才会有配置, 没有配置时返回 0 也无副作用 — 与 v3.12 零差异.
if not os.environ.get("BACKEND_SKIP_INIT"):
    try:
        from backend.services.channel_group_coordinator import get_coordinator as _get_cg_coord
        from backend.db.database import SessionLocal as _SessionLocal
        _db = _SessionLocal()
        try:
            _n = _get_cg_coord().reload_groups(_db)
            if _n > 0:
                print(f"[启动] ChannelGroupCoordinator 加载 {_n} 个工位组配置")
        finally:
            _db.close()
    except Exception as _e:
        print(f"[启动] ChannelGroupCoordinator 加载失败 (隔离, 不影响主流程): {_e}")

# v3.14 RFC 11: 启动时加载流水线串行配置 + 把上次进程留下的 in_progress 全部 abort.
# 客户场景 (流水线串行) 才会有配置, 没有时返回 0 也无副作用 — 与 v3.13 零差异.
if not os.environ.get("BACKEND_SKIP_INIT"):
    try:
        from backend.services.workpiece_flow_coordinator import get_coordinator as _get_wfc_coord
        from backend.db.database import SessionLocal as _SessionLocal2
        _db2 = _SessionLocal2()
        try:
            _aborted = _get_wfc_coord().abort_in_progress_on_startup(_db2)
            if _aborted > 0:
                print(f"[启动] WorkpieceFlow 把上次进程留下的 {_aborted} 个 in-flight run 标 aborted")
            _n2 = _get_wfc_coord().reload_flows(_db2)
            if _n2 > 0:
                print(f"[启动] WorkpieceFlowCoordinator 加载 {_n2} 个串行流水线配置")
        finally:
            _db2.close()
    except Exception as _e:
        print(f"[启动] WorkpieceFlowCoordinator 加载失败 (隔离, 不影响主流程): {_e}")

# v3.21: 启动时加载包装箱结算配置 + 把上次进程留下的进行中工单标 aborted.
# 客户场景 (上银包装线) 才会有配置, 没有时返回 0 也无副作用 — 与不配置时零差异.
if not os.environ.get("BACKEND_SKIP_INIT"):
    try:
        from backend.services.packaging_flow_coordinator import (
            get_coordinator as _get_pkg_coord,
            wire_real_hooks as _wire_pkg_hooks,
        )
        from backend.db.database import SessionLocal as _SessionLocal3
        _wire_pkg_hooks()  # 注入真实拉单 + 报警钩子
        _db3 = _SessionLocal3()
        try:
            _aborted_pkg = _get_pkg_coord().abort_in_progress_on_startup(_db3)
            if _aborted_pkg > 0:
                print(f"[启动] PackagingFlow 把上次进程留下的 {_aborted_pkg} 个进行中工单标 aborted")
            _n3 = _get_pkg_coord().reload_configs(_db3)
            if _n3 > 0:
                print(f"[启动] PackagingFlowCoordinator 加载 {_n3} 个包装结算配置")
        finally:
            _db3.close()
    except Exception as _e:
        print(f"[启动] PackagingFlowCoordinator 加载失败 (隔离, 不影响主流程): {_e}")


def auto_restore_video_sources():
    """后端启动时根据 workstation_config.json 自动恢复视频流 + GPU分配 + 检测状态"""
    from backend.api.channel_manager import channel_manager
    import os
    try:
        sources = channel_manager.get_channel_sources()
        if not sources:
            return
        for ch_str, ch_cfg in sources.items():
            ch_id = int(ch_str)
            src_type = ch_cfg.get("source_type")
            if not src_type:
                continue
            mgr = channel_manager.channels.get(ch_id)
            if not mgr:
                continue

            gpu = ch_cfg.get("gpu_device")
            if gpu and gpu != "auto":
                mgr.device = gpu
                print(f"[启动] ch{ch_id} GPU 恢复: {gpu}")

            if mgr.is_running:
                continue
            try:
                if src_type == "camera":
                    dev_idx = ch_cfg.get("device_index", 0)
                    if isinstance(dev_idx, str):
                        parts = dev_idx.split("_")
                        dev_idx = int(parts[-1]) if parts[-1].isdigit() else 0
                    res = ch_cfg.get("resolution", "1280x720")
                    w, h = (int(x) for x in res.split("x")) if "x" in str(res) else (1280, 720)
                    fps = ch_cfg.get("fps", 60)
                    auto_exp = ch_cfg.get("auto_exposure", True)
                    exp_val = ch_cfg.get("exposure_value", -6.0)
                    mgr.start_camera(dev_idx, w, h, fps,
                                     auto_exposure=auto_exp,
                                     exposure_value=exp_val)
                    print(f"[启动] ch{ch_id} 自动恢复摄像头: device={dev_idx}, "
                          f"auto_exposure={auto_exp}, exposure={exp_val}")
                elif src_type == "rtsp":
                    url = ch_cfg.get("url", "")
                    if url:
                        fps = ch_cfg.get("rtsp_fps", 25)
                        mgr.start_rtsp(url, fps)
                        print(f"[启动] ch{ch_id} 自动恢复RTSP: {url}")
                elif src_type == "hcnetsdk":
                    ip = ch_cfg.get("hcnet_ip")
                    if ip:
                        port = ch_cfg.get("hcnet_port", 8000)
                        user = ch_cfg.get("hcnet_username", "admin")
                        pwd = ch_cfg.get("hcnet_password", "")
                        ch_no = ch_cfg.get("hcnet_channel", 1)
                        stream = ch_cfg.get("hcnet_stream_type", 1)
                        hc_fps = ch_cfg.get("hcnet_fps", 25)
                        mgr.start_hcnetsdk(ip, port, user, pwd, ch_no, stream, hc_fps)
                        print(f"[启动] ch{ch_id} 自动恢复海康SDK: {ip}")
                elif src_type == "video":
                    vf = ch_cfg.get("video_file", "")
                    if vf and os.path.isfile(vf):
                        mgr.start_video(vf)
                        print(f"[启动] ch{ch_id} 自动恢复视频: {vf}")
            except Exception as e:
                print(f"[启动] ch{ch_id} 视频源恢复失败: {e}")

        # v3.22.x: 开机自动恢复检测开关 (默认 true). 关掉时只恢复项目+视频源,
        # 停在待机, 由工人手动点开始 — 项目/源恢复不受影响。
        if not channel_manager.get_auto_resume_config().get("enabled", True):
            print("[启动] 开机自动恢复检测开关=关闭, 跳过自动开始检测 (项目+视频源已恢复, 停在待机)")
            return

        import time
        time.sleep(0.5)

        def _restore_detection_pass(label: str):
            # v3.22.x: 开关开启时【无条件】自动开始检测 — 不再看上次是否在检测
            # (was_detecting)。只要该通道视频源已恢复运行 + 模型已就绪, 就自动开始,
            # 让工人开机即接着干、不用手动点。门槛仅剩"源在跑 + 模型就绪"两条物理前提。
            for ch_str, ch_cfg in sources.items():
                ch_id = int(ch_str)
                mgr = channel_manager.channels.get(ch_id)
                if not mgr or not mgr.is_running or mgr.model is None:
                    continue
                if mgr.is_detecting:
                    continue
                try:
                    mgr.start_detection()
                    print(f"[启动] ch{ch_id} 开机自动开始检测 ({label})")
                except Exception as e:
                    print(f"[启动] ch{ch_id} 自动开始检测失败 ({label}): {e}")

        _restore_detection_pass("首轮")
        # 工控机模型加载慢时, 0.5s 后模型可能仍未就绪 — 3s 后再试一轮
        time.sleep(3.0)
        _restore_detection_pass("重试")

    except Exception as e:
        print(f"[启动] 视频源自动恢复整体失败: {e}")

if not os.environ.get("BACKEND_SKIP_INIT"):
    auto_restore_video_sources()

# ========== MES Hook + Scanner 初始化 ==========
def _init_mes_services():
    """初始化 MES Hook 管理器和扫码器服务, 注入到 VideoSourceManager"""
    try:
        from backend.services.mes_hooks import get_mes_hook
        from backend.services.scanner import get_scanner_service

        mes_hook = get_mes_hook()
        mes_hook.start()

        vm = get_video_manager()
        vm._mes_hook = mes_hook

        from backend.api.channel_manager import channel_manager
        for ch_id, ch_mgr in channel_manager.channels.items():
            ch_mgr._mes_hook = mes_hook

        scanner_svc = get_scanner_service()
        scanner_svc.set_mes_hook(mes_hook)

        def _get_project_id_for_channel(ch):
            from backend.api.channel_manager import channel_manager
            try:
                mgr = channel_manager.get(ch)
                return mgr.project_config.get('id') if mgr.project_config else None
            except (ValueError, AttributeError):
                return vm.project_config.get('id') if vm.project_config else None

        scanner_svc.set_project_id_getter(_get_project_id_for_channel)
        scanner_svc.start_all()

        from backend.services.cluster_collector import get_cluster_collector
        cluster = get_cluster_collector()
        cluster.start()

        from backend.services.external_device import get_external_device_service
        extdev_svc = get_external_device_service()
        extdev_svc.start_all()

        # v3.20: 外部 MES 工单定时拉取调度器 (按 config.pull.triggers 轮询, 无配置则空转)
        from backend.services.mes_puller import get_pull_scheduler
        get_pull_scheduler().start()

        print("[MES] 服务初始化完成（含集群汇总、外部设备、工单拉取调度）")
    except Exception as e:
        print(f"[MES] 服务初始化失败（非致命）: {e}")

if not os.environ.get("BACKEND_SKIP_INIT"):
    _init_mes_services()

# ========== 后台自动清理定时任务 ==========
_cleanup_timer = None
_daily_cleanup_timer = None

def _schedule_auto_cleanup():
    """后台定时执行数据清理（每24小时一次）"""
    global _cleanup_timer
    try:
        from backend.api.sessions_maintenance import _perform_auto_cleanup_safe
        _perform_auto_cleanup_safe()
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


# A1: 每天固定时点清理。工控机日常关机/重启时, 24h 定时器几乎等不到第二次触发,
#     固定时点(如凌晨 3 点)保证每天到点清一次。配置 cleanup_daily_time="HH:MM",
#     空 = 不启用(只保留开机 + 24h 旧行为)。与上面的 24h 定时器并存, 由清理互斥锁
#     保证不会并发删库。
def _seconds_until_next_daily(hhmm):
    """从现在到下一个 HH:MM 的秒数; 解析失败返回 None。"""
    try:
        h, m = hhmm.strip().split(":")
        h, m = int(h), int(m)
        if not (0 <= h <= 23 and 0 <= m <= 59):
            return None
        from datetime import datetime as _dt, timedelta as _td
        now = _dt.now()
        target = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if target <= now:
            target += _td(days=1)
        return max((target - now).total_seconds(), 1)
    except Exception:
        return None

def _get_daily_cleanup_time():
    try:
        from backend.db.database import SessionLocal
        from backend.models.models import SystemConfig
        db = SessionLocal()
        try:
            row = db.query(SystemConfig).filter(
                SystemConfig.key == "cleanup_daily_time").first()
            return (row.value or "").strip() if row else ""
        finally:
            db.close()
    except Exception:
        return ""

def _schedule_daily_cleanup():
    global _daily_cleanup_timer
    hhmm = _get_daily_cleanup_time()
    secs = _seconds_until_next_daily(hhmm) if hhmm else None
    if secs is None:
        # 未配置或非法: 1 小时后重新看配置(允许客户运行中开启, 无需重启)
        _daily_cleanup_timer = threading.Timer(3600, _schedule_daily_cleanup)
        _daily_cleanup_timer.daemon = True
        _daily_cleanup_timer.start()
        return

    def _run():
        try:
            from backend.api.sessions_maintenance import _perform_auto_cleanup_safe
            print(f"[定时清理] 到达每日固定时点 {hhmm}, 执行清理...")
            _perform_auto_cleanup_safe()
        except Exception as e:
            print(f"[定时清理] 固定时点执行失败: {e}")
        _schedule_daily_cleanup()  # 排下一天

    _daily_cleanup_timer = threading.Timer(secs, _run)
    _daily_cleanup_timer.daemon = True
    _daily_cleanup_timer.start()

threading.Thread(target=_schedule_daily_cleanup, daemon=True).start()


# v2.7.9: 外设日志每小时清理一次（保留已绑定扫码的记录）
# 称重器/传感器稳定阶段会写 stabilizing 过程日志，长期跑会堆积。
# 扫码绑定过的日志（box_serial 非空）属于业务数据，不动；其他每小时清 1 次。
_extdev_cleanup_timer = None

def _schedule_extdev_log_cleanup():
    global _extdev_cleanup_timer
    try:
        from backend.services.external_device import get_external_device_service
        svc = get_external_device_service()
        svc.cleanup_old_logs(keep_bound=True, older_than_seconds=3600)
    except Exception as e:
        print(f"[ExtDev 定时清理] 执行失败: {e}")
    _extdev_cleanup_timer = threading.Timer(3600, _schedule_extdev_log_cleanup)
    _extdev_cleanup_timer.daemon = True
    _extdev_cleanup_timer.start()

def _start_extdev_log_cleanup():
    print("[ExtDev 定时清理] 每小时清理未绑定扫码的外设日志...")
    _schedule_extdev_log_cleanup()

threading.Thread(target=_start_extdev_log_cleanup, daemon=True).start()


# B8: 每小时清理一次过期登录 token（内存缓存 + 落盘表）。
# 当前持久登录默认不设过期, 此任务多为 no-op; 一旦启用带过期会话, 防止过期
# token 在内存/库里无限堆积。只删已过期, 不动有效/无过期 token。
_token_purge_timer = None

def _schedule_token_purge():
    global _token_purge_timer
    try:
        from backend.core.auth import purge_expired_tokens
        purge_expired_tokens()
    except Exception as e:
        print(f"[Auth 定时清理] 过期 token 清理失败: {e}")
    _token_purge_timer = threading.Timer(3600, _schedule_token_purge)
    _token_purge_timer.daemon = True
    _token_purge_timer.start()

threading.Thread(target=_schedule_token_purge, daemon=True).start()

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
        from backend.api.channel_manager import channel_manager
        for ch_id, mgr in channel_manager.channels.items():
            try:
                was_detecting = mgr.is_detecting
                channel_manager.save_channel_source(ch_id, {"was_detecting": was_detecting})
                if mgr.current_session_id and mgr.counters:
                    mgr._save_counters_snapshot()
                    print(f"[退出钩子] ch{ch_id} 计数器已保存")
                if mgr.current_session_id:
                    mgr.end_session()
                    print(f"[退出钩子] ch{ch_id} 会话已结束")
                if mgr.is_detecting:
                    mgr.stop_detection(persist_was_detecting=False)
                if mgr.is_running:
                    mgr.stop()
            except Exception as ch_e:
                print(f"[退出钩子] ch{ch_id} 清理出错: {ch_e}")
        print("[退出钩子] 所有通道已清理")
        
        # 停止 MES 服务
        try:
            from backend.services.mes_hooks import get_mes_hook
            from backend.services.scanner import get_scanner_service
            from backend.services.cluster_collector import get_cluster_collector
            from backend.services.external_device import get_external_device_service
            get_scanner_service().stop_all()
            get_mes_hook().stop()
            get_cluster_collector().stop()
            get_external_device_service().stop_all()
        except Exception as _e:
            print(f"[Shutdown] 停止 MES/集群/外设服务时异常（已忽略）: {_e}", flush=True)

        # v3.8.x 停止定时导出调度器
        try:
            from backend.services.export_scheduled import stop_scheduler
            stop_scheduler()
        except Exception as _e:
            print(f"[Shutdown] 停止定时导出调度器异常（已忽略）: {_e}", flush=True)

        # 短信日报: 停调度器 + 计数器日增量最后一次刷盘
        try:
            from backend.services.sms_report import stop_scheduler as _stop_sms_scheduler
            _stop_sms_scheduler()
        except Exception as _e:
            print(f"[Shutdown] 停止短信日报调度器异常（已忽略）: {_e}", flush=True)
        try:
            from backend.services.counter_daily import stop as _stop_counter_daily
            _stop_counter_daily()
        except Exception as _e:
            print(f"[Shutdown] 计数器日增量刷盘异常（已忽略）: {_e}", flush=True)

        # A2 停止出站健康探测调度器
        try:
            from backend.services.mes_health_probe import stop_health_probe
            stop_health_probe()
        except Exception as _e:
            print(f"[Shutdown] 停止健康探测调度器异常（已忽略）: {_e}", flush=True)

        # 停止扫码器旁路 SN 监控线程
        try:
            from backend.services.scanner_bypass_monitor import stop_monitor
            stop_monitor()
        except Exception as _e:
            print(f"[Shutdown] 停止旁路 SN 监控异常（已忽略）: {_e}", flush=True)

        # v2.7.3: 兜底熄灭所有通道报警灯并断开串口，避免主进程被 KILL 时灯塔残留
        try:
            from backend.api.alarm import alarm_router
            for ch_id, mgr in list(alarm_router.managers.items()):
                try:
                    mgr._idle_light_active = False
                    mgr.all_off()
                except Exception as _e:
                    print(f"[退出钩子] 报警器熄灯失败 ch{ch_id}（已忽略）: {_e}", flush=True)
            alarm_router.disconnect_all()
            print("[退出钩子] 所有通道报警器已熄灯并断开串口")
        except Exception as e:
            print(f"[退出钩子] 报警清理出错: {e}")
            
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
    # 安全：默认开放，设置 ENABLE_API_DOCS=0 可关闭（出厂版建议关闭）
    docs_url="/docs" if os.environ.get("ENABLE_API_DOCS", "1") != "0" else None,
    redoc_url="/redoc" if os.environ.get("ENABLE_API_DOCS", "1") != "0" else None,
)

# Set all CORS enabled origins
# 安全：CORS_ALLOW_ORIGINS 环境变量可配置（逗号分隔），未设置时仅允许本机
# 出厂工控机：前端通过 Electron 直连 127.0.0.1，无需放开公网域
_cors_origins_env = os.environ.get("CORS_ALLOW_ORIGINS", "").strip()
if _cors_origins_env:
    if _cors_origins_env == "*":
        _cors_origins = ["*"]
        _cors_creds = False  # CORS 规范：origin=* 时不能开 credentials
    else:
        _cors_origins = [o.strip() for o in _cors_origins_env.split(",") if o.strip()]
        _cors_creds = True
else:
    _cors_origins = [
        "http://localhost:5173", "http://127.0.0.1:5173",
        "http://localhost:8000", "http://127.0.0.1:8000",
        "http://localhost:5174", "http://127.0.0.1:5174",
        "http://localhost:6001", "http://127.0.0.1:6001",
        "app://./", "file://",
    ]
    _cors_creds = True

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    # 开发场景: 任意本机端口都放行 (Vite 改端口不用动后端)
    # 生产 Electron 走 file:// / app://, 默认列表已含, 不影响安全
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=_cors_creds,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==================== 调试中心: API 异常统一记录 ====================
# 纯观测插桩 — 异常原样 raise 不吞、响应不改; 开关关闭时开销 = 每请求一次 dict 查询
@app.middleware("http")
async def _debug_api_exception_middleware(request, call_next):
    import time as _t
    _t0 = _t.time()
    try:
        response = await call_next(request)
    except Exception:
        if debug_center.is_on("backend.api"):
            import traceback as _tb
            _q = ("?" + request.url.query) if request.url.query else ""
            debug_center.dbg("backend.api", f"未捕获异常 {request.method} {request.url.path}{_q}",
                             _tb.format_exc()[-1500:])
        raise
    if debug_center.is_on("backend.api"):
        _dur = (_t.time() - _t0) * 1000
        _q = ("?" + request.url.query) if request.url.query else ""
        # 4xx/5xx 统一记录 (含入参 query + 耗时); 401/403 鉴权噪声不记 (冷启动会刷屏)
        if response.status_code >= 400 and response.status_code not in (401, 403):
            _lvl = "5xx 响应" if response.status_code >= 500 else "4xx 响应"
            debug_center.dbg("backend.api", f"{_lvl} {request.method} {request.url.path}{_q}",
                             f"status={response.status_code} 耗时={_dur:.0f}ms")
        elif _dur > 2000:
            # 慢请求 (>2s): 排查"某接口拖慢前端/冷启动卡顿"
            debug_center.dbg("backend.api", f"慢请求 {request.method} {request.url.path}{_q}",
                             f"status={response.status_code} 耗时={_dur:.0f}ms")
    return response

# ==================== 路由挂载（唯一登记处: backend/api/router_manifest.py） ====================
# 新增主程序路由去 router_manifest 登记, 不要在这里 include_router (OVERLAP-3 治理)
mount_all_routers(app)

# Mount static files for uploads (images, etc.)
if os.path.exists(settings.UPLOAD_DIR):
    app.mount("/uploads", StaticFiles(directory=settings.UPLOAD_DIR), name="uploads")

# Mount recordings directory for video playback
if os.path.exists(settings.RECORDING_DIR):
    app.mount("/recordings", StaticFiles(directory=settings.RECORDING_DIR), name="recordings")


# v3.7 / G1: 在所有内置 router 与 static 挂载之后, 才加载 active 插件。
# 时序原因: 插件 register_plugin(app, registry, license, host) 需要 app 引用挂 router。
def _load_active_plugin_after_app():
    if os.environ.get("BACKEND_SKIP_INIT"):
        return
    try:
        from backend.plugin_system.manager import load_active_plugin_on_startup
        load_active_plugin_on_startup(app=app)
    except Exception as e:
        print(f"[Plugin] active 插件加载失败（已隔离, 主程序继续）: {e}")
        import traceback
        traceback.print_exc()


_load_active_plugin_after_app()


# 入站接收路径别名: 启动时按配置动态注册根路径接收 URL (客户自定义 /warning/clear 等)。
def _register_inbound_aliases_after_app():
    if os.environ.get("BACKEND_SKIP_INIT"):
        return
    try:
        from backend.api.mes_inbound import register_inbound_aliases
        register_inbound_aliases(app)
    except Exception as e:
        print(f"[MES-Inbound] 入站路径别名注册失败（已隔离, 主程序继续）: {e}")


_register_inbound_aliases_after_app()


# v3.8.x: 定时导出 — APScheduler 后台线程在主进程启动时启动,
# 在 lifespan shutdown 时停掉 (Electron 8 步关机会调 /shutdown/complete)。
def _start_scheduled_export():
    if os.environ.get("BACKEND_SKIP_INIT"):
        return
    try:
        from backend.services.export_scheduled import start_scheduler
        start_scheduler()
    except Exception as e:
        print(f"[Scheduled] 定时导出启动失败 (已隔离, 主程序继续): {e}")
        import traceback
        traceback.print_exc()


_start_scheduled_export()


# 每日短信日报 — 独立 APScheduler, 与定时导出同范式 (无 enabled 规则时零开销)。
def _start_sms_report():
    if os.environ.get("BACKEND_SKIP_INIT"):
        return
    try:
        from backend.services.sms_report import start_scheduler as _start_sms_scheduler
        _start_sms_scheduler()
    except Exception as e:
        print(f"[SmsReport] 短信日报调度启动失败 (已隔离, 主程序继续): {e}")


_start_sms_report()


# 出站 MES 连接主动健康探测调度器 (A2): 后台周期探活, 配置驱动 (默认全关零开销)。
def _start_mes_health_probe():
    if os.environ.get("BACKEND_SKIP_INIT"):
        return
    try:
        from backend.services.mes_health_probe import start_health_probe
        start_health_probe()
    except Exception as e:
        print(f"[MES-Health] 健康探测启动失败 (已隔离, 主程序继续): {e}")


_start_mes_health_probe()


# 扫码器旁路 SN 监控: 后台守护线程, 定期扫描 ExportRealtimeRule.input_dir 缓存当前 SN
# (前端 Monitor 显示 + cycle_start 内存优先锁快照)。无相关规则时线程空转极轻量。
def _start_scanner_bypass_monitor():
    if os.environ.get("BACKEND_SKIP_INIT"):
        return
    try:
        from backend.services.scanner_bypass_monitor import start_monitor
        start_monitor()
    except Exception as e:
        print(f"[ScannerBypassMonitor] 启动失败 (已隔离, 主程序继续): {e}")


_start_scanner_bypass_monitor()


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
            # v2.7.3: 必须走每个通道完整的 stop_detection() 流程（包含熄灭工作指示灯、
            # 停推理、停录像、停扫码、断 MES 等），否则关软件后灯塔常亮、串口残留。
            try:
                from backend.api.channel_manager import channel_manager
                for ch_id, mgr in list(channel_manager.channels.items()):
                    try:
                        if mgr.is_detecting:
                            # v3.22.x: 关机前先落盘 was_detecting=true, 否则下次启动无法自动恢复检测
                            channel_manager.persist_was_detecting(ch_id, True)
                            mgr.stop_detection(persist_was_detecting=False)
                        else:
                            # 即使没在检测，也兜底熄灯（防止 idle_light 残留）
                            try:
                                from backend.api.alarm import alarm_router
                                alarm_router.stop_idle_light(channel_id=ch_id)
                            except Exception as _e:
                                print(f"[关闭步骤] ch{ch_id} stop_idle_light 失败（已忽略）: {_e}", flush=True)
                    except Exception as e:
                        print(f"[关闭步骤] ch{ch_id} stop_detection 失败: {e}")
            except Exception as e:
                # 兜底：旧版兼容路径
                print(f"[关闭步骤] 多通道清理失败，回退到 ch0: {e}")
                try:
                    from backend.api.channel_manager import channel_manager
                    if video_manager.is_detecting:
                        channel_manager.persist_was_detecting(0, True)
                        video_manager.stop_detection(persist_was_detecting=False)
                except Exception as _e2:
                    print(f"[关闭步骤] ch0 兜底 stop_detection 失败: {_e2}")
            return {"status": "success", "step": step}
        
        elif step == "save_counters":
            if video_manager.current_session_id and video_manager.counters:
                video_manager._save_counters_snapshot()
            return {"status": "success", "step": step}
        
        elif step == "end_cycle":
            # 如果有进行中的周期，结束它 (v3.38: 看 uuid, id 由落库线程回填)
            if getattr(video_manager, 'current_cycle_uuid', None) or video_manager.current_cycle_id:
                try:
                    video_manager.end_cycle(is_good=False, reason="程序关闭")
                except Exception as _e:
                    print(f"[Shutdown] end_cycle 失败（已忽略）: {_e}", flush=True)
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
                except Exception as _e:
                    print(f"[Shutdown] capture.release 失败（已忽略）: {_e}", flush=True)
                video_manager.capture = None
            return {"status": "success", "step": step}
        
        elif step == "release_model":
            video_manager._release_model()
            return {"status": "success", "step": step}
        
        elif step == "cleanup":
            video_manager.is_running = False
            video_manager._clear_all_caches()
            # v2.7.3: 关软件最后一步统一断所有通道的报警串口，确保灯塔/蜂鸣器不残留
            try:
                from backend.api.alarm import alarm_router
                for ch_id, mgr in list(alarm_router.managers.items()):
                    try:
                        mgr._idle_light_active = False
                        mgr.all_off()
                    except Exception as _e:
                        print(f"[关闭步骤] 报警器熄灯失败 ch{ch_id}（已忽略）: {_e}", flush=True)
                alarm_router.disconnect_all()
                print("[关闭步骤] 所有通道报警器已熄灯并断开串口")
            except Exception as e:
                print(f"[关闭步骤] 报警清理失败: {e}")
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
def video_feed(channel: int = 0):
    """视频流端点 - 支持多通道。?channel=0 (default), ?channel=1, etc.

    通道未启动任何视频源时,返回一张黑底白字的 "Ch{N} - No Source" 占位 MJPEG 流,
    避免前端 <img> 因为 EOF 反复闪烁/重连. cv2.putText 在 OpenCV 4.11 + 某些
    Linux 环境下渲染特定字符会段错误, 用 try/except 兜底, 失败时退化为纯黑帧.
    """
    from backend.api.channel_manager import channel_manager
    vm = channel_manager.get(channel)

    if vm.is_running or vm.source_type:
        return StreamingResponse(
            vm.generate_mjpeg(),
            media_type="multipart/x-mixed-replace; boundary=frame"
        )

    import numpy as np
    import time as _time

    def _generate_no_source_frames():
        black_frame = np.zeros((480, 640, 3), dtype=np.uint8)
        try:
            cv2.putText(black_frame, f"Ch{channel} - No Source", (180, 240),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
        except cv2.error:
            pass
        ret, buffer = cv2.imencode('.jpg', black_frame)
        if not ret:
            return
        frame_bytes = buffer.tobytes()
        while True:
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
            _time.sleep(0.1)

    return StreamingResponse(
        _generate_no_source_frames(),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )

@app.get("/snapshot")
def snapshot(channel: int = 0):
    """单帧快照端点 - 支持多通道 ?channel=0"""
    vm = get_video_manager(channel)
    data = vm.get_snapshot()
    if data:
        return Response(content=data, media_type="image/jpeg",
                        headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
    
    import numpy as np
    black_frame = np.zeros((480, 640, 3), dtype=np.uint8)
    try:
        cv2.putText(black_frame, f"Ch{channel} - No Source", (180, 240),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
    except cv2.error:
        pass
    _, buffer = cv2.imencode('.jpg', black_frame)
    return Response(content=buffer.tobytes(), media_type="image/jpeg",
                    headers={"Cache-Control": "no-cache, no-store, must-revalidate"})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001, reload=True)
