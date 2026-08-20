"""
==================== 主程序路由挂载清单（唯一登记处） ====================

OVERLAP-3 治理（2026-07）：此前主程序路由分两条路径挂载——
`backend/api/__init__.py` 的 api_router 聚合 14 个 + `backend/main.py` 直挂 19 个，
新增路由不知道进哪边。现在统一：**所有主程序路由都在本文件登记**，main.py 只调
`mount_all_routers(app)` 一次。

规则：
- 新增主程序路由 → 在本文件按序加一行（顺序即 FastAPI 匹配顺序，
  存在真实前缀重叠：showcase_stats 与 sessions 都在 /data 下，勿乱序）
- 插件路由 → 走 plugin_system RoutesRegistry（/api/v1/plugins/{customer_code}/*），
  不进本文件
- 所有 import 放在函数体内：source.py 等模块导入即拉起 cv2/PIL/numpy，
  放模块顶层会让任何 `import backend.api.router_manifest` 变重，且可能
  在环境变量未设时触发 cv2 导入（AGENTS.md 不变量 #10）
"""

from fastapi import FastAPI

from backend.core.config import settings


def mount_all_routers(app: FastAPI) -> None:
    """把全部主程序路由挂到 app。只应被 backend/main.py 调用一次。"""
    v1 = settings.API_V1_STR  # "/api/v1"

    # -------- 业务 CRUD（原 api_router 聚合段，v3.31 前挂在 backend/api/__init__.py） --------
    from backend.api import projects, models, cameras, tasks, reports, alarm, sms
    from backend.api import system_display, export_custom, export_realtime, export_scheduled
    from backend.api import sms_report      # v3.46 每日短信日报
    from backend.api import channel_groups    # v3.13 RFC 10 工位组
    from backend.api import workpiece_flows   # v3.14 RFC 11 串行流水线
    from backend.api import packaging_flows   # v3.21 包装箱结算 (上银包装线)
    from backend.api import showcase_stats    # RFC12 展会: 行为评分/趋势只读统计
    from backend.api import interconnect      # v3.47 训练平台互连 (契约 1.0)
    from backend.api import plc               # RFC 13 通用 PLC 连接器
    from backend.api import triggers          # RFC 14 统一触发中心
    from backend.api import video_archive     # v3.53 录像归档规则

    app.include_router(projects.router, prefix=f"{v1}/projects", tags=["projects"])
    app.include_router(models.router, prefix=f"{v1}/models", tags=["models"])
    app.include_router(cameras.router, prefix=f"{v1}/cameras", tags=["cameras"])
    app.include_router(tasks.router, prefix=f"{v1}/tasks", tags=["tasks"])
    app.include_router(reports.router, prefix=f"{v1}/reports", tags=["reports"])
    app.include_router(alarm.router, prefix=f"{v1}/alarm", tags=["alarm"])
    app.include_router(sms.router, prefix=f"{v1}/sms", tags=["sms"])
    app.include_router(system_display.router, prefix=f"{v1}/system", tags=["system"])
    app.include_router(export_custom.router, prefix=f"{v1}/export", tags=["export"])
    app.include_router(export_realtime.router, prefix=f"{v1}/export", tags=["export-realtime"])
    app.include_router(export_scheduled.router, prefix=f"{v1}/export", tags=["export-scheduled"])
    app.include_router(video_archive.router, prefix=f"{v1}/export", tags=["video-archive"])
    app.include_router(sms_report.router, prefix=f"{v1}/sms-report", tags=["sms-report"])
    app.include_router(channel_groups.router, prefix=f"{v1}/channel-groups", tags=["channel-groups"])
    app.include_router(workpiece_flows.router, prefix=f"{v1}/workpiece-flows", tags=["workpiece-flows"])
    app.include_router(packaging_flows.router, prefix=f"{v1}/packaging-flows", tags=["packaging-flows"])
    # 展会统计挂 /data 前缀，与下方 sessions 的 /data 共存——本段必须先注册
    app.include_router(showcase_stats.router, prefix=f"{v1}/data", tags=["showcase-stats"])
    app.include_router(interconnect.router, prefix=f"{v1}/interconnect", tags=["interconnect"])
    app.include_router(plc.router, prefix=f"{v1}/plc", tags=["plc"])
    app.include_router(triggers.router, prefix=f"{v1}/triggers", tags=["triggers"])

    # -------- 核心/重量级路由（原 main.py 直挂段，保持原注册顺序） --------
    from backend.api.source import router as source_router          # 视频源 + 检测核心
    from backend.api.sessions import router as sessions_router      # 数据管理
    from backend.api.channel_manager import router as workstation_router  # 多工位管理
    from backend.api.mes import router as mes_router
    from backend.api.scanner import router as scanner_router
    from backend.api.wmax import router as wmax_router
    from backend.api.mes_gateway import router as mes_gateway_router
    from backend.api.mes_inbound import router as mes_inbound_router
    from backend.api.operators import router as operators_router    # v3.10.0 已废弃, 全 410
    from backend.api.cluster import router as cluster_router
    from backend.api.external_device import router as extdev_router
    from backend.api.weighing import router as weighing_router      # v3.31 称重投料
    from backend.api.debug import router as debug_router
    from backend.api.plugins import router as plugins_router
    from backend.api.auth import router as auth_router              # v3.10.0 用户系统
    from backend.api.users import router as users_router
    from backend.api.roles import router as roles_router
    from backend.api.api_keys import router as api_keys_router

    app.include_router(source_router, prefix=f"{v1}/source", tags=["source"])
    # Detection router 已删除 (走 /source/detection/* 即 source_routes.py, 前端只用这套)
    app.include_router(sessions_router, prefix=f"{v1}/data", tags=["data"])
    app.include_router(workstation_router, prefix=v1, tags=["workstations"])
    app.include_router(mes_router, prefix=v1, tags=["MES"])
    app.include_router(scanner_router, prefix=v1, tags=["Scanner"])
    app.include_router(wmax_router, prefix=v1, tags=["WMax Scanner"])
    app.include_router(mes_gateway_router, prefix=v1, tags=["MES-Gateway"])
    app.include_router(mes_inbound_router, prefix=v1, tags=["MES-Inbound"])
    app.include_router(operators_router, prefix=v1, tags=["Operators"])
    app.include_router(cluster_router, prefix=v1, tags=["Cluster"])
    app.include_router(extdev_router, prefix=v1, tags=["External Devices"])
    app.include_router(weighing_router, prefix=v1, tags=["Weighing"])
    app.include_router(debug_router, prefix=v1, tags=["Debug"])
    app.include_router(plugins_router, prefix=v1, tags=["Plugins"])
    app.include_router(auth_router, prefix=v1, tags=["Auth"])
    app.include_router(users_router, prefix=v1, tags=["Users"])
    app.include_router(roles_router, prefix=v1, tags=["Roles"])
    app.include_router(api_keys_router, prefix=v1, tags=["API Keys"])

    # -------- 测试运行时专用路由（仅 RUNTIME_MODE=test 挂载） --------
    import os
    if os.environ.get("RUNTIME_MODE") == "test":
        from backend.api.test_runtime_routes import router as test_synthetic_router
        from backend.api.test_compat_routes import router as test_compat_router

        app.include_router(
            test_synthetic_router,
            prefix=f"{v1}/test/synthetic",
            tags=["test-synthetic"],
        )
        app.include_router(
            test_compat_router,
            prefix=v1,
            tags=["test-compat"],
        )
        print("[RUNTIME_MODE=test] mounted /api/v1/test/synthetic/* (virtual detection scenarios)")
        print("[RUNTIME_MODE=test] mounted test-compat shim routes (mes/alarm/sessions/source legacy paths)")
