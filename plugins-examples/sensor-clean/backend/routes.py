"""sensor-clean 查询 / 控制接口。

挂在 /api/v1/plugins/sensor-clean/swab/* 下（register_plugin 里 subpath="swab"）。
"""
from typing import Optional

from fastapi import APIRouter, Body, Query

from .hooks import (
    get_state,
    reset_swab,
    reset_counts,
    reload_config,
    apply_preset_config,
    import_demo_projects,
    get_config,
    save_config,
    get_swab_records,
)
from .preset import get_templates

router = APIRouter()


@router.get("/config")
def swab_get_config():
    """当前完整配置（默认+已保存覆盖），项目配置 Tab 回填表单用。"""
    return get_config()


@router.post("/config")
def swab_save_config(patch: dict = Body(...)):
    """保存双工位参数 + 三判定事件映射（部分字段即可）+ 热加载。"""
    return save_config(patch)


@router.get("/state")
def swab_state():
    """当前状态：总产量 / 不良 / 棉签已用 / 剩余 / 是否超限。监控看板轮询。"""
    return get_state()


@router.get("/records")
def swab_records(
    limit: int = Query(300, ge=1, le=2000),
    since: Optional[float] = Query(None, description="仅取结束时间 >= 该 epoch 秒的记录"),
    until: Optional[float] = Query(None, description="仅取结束时间 <= 该 epoch 秒的记录"),
    channel: Optional[int] = Query(None, description="按工位筛选"),
):
    """棉签历史记录 + 汇总：数据页列表 / 图表 / 导出共用。

    一条记录 = 一根棉签的完整生命周期（擦第 1 件 → 换棉签/手动/整批重置清零）。
    """
    return get_swab_records(limit=limit, since=since, until=until, channel=channel)


@router.post("/reset")
def swab_reset():
    """手动更换棉签（本根清零、解除超限；总产量/不良不清）。"""
    return reset_swab()


@router.post("/reset-counts")
def swab_reset_counts():
    """整批重置：总产量 / 不良 / 棉签全部清零。"""
    return reset_counts()


@router.post("/reload-config")
def swab_reload_config():
    """改了系统配置后重新加载插件配置。"""
    return reload_config()


@router.get("/templates")
def swab_templates():
    """获取客户真值标定的项目配置模板（视角1/视角2）+ 耗材默认配置。"""
    return get_templates()


@router.post("/apply-preset")
def swab_apply_preset():
    """一键应用：最优计数/耗材/三判定参数写库 + 热加载，并返回项目模板。"""
    return apply_preset_config()


@router.post("/import-project")
def swab_import_project():
    """一键导入：创建视角1/视角2 项目 + 登记模型 + 激活到双通道（幂等）。"""
    return import_demo_projects()
