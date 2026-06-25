"""sensor-clean 查询 / 控制接口。

挂在 /api/v1/plugins/sensor-clean/swab/* 下（register_plugin 里 subpath="swab"）。
"""
from fastapi import APIRouter, Body

from .hooks import (
    get_state,
    reset_swab,
    reload_config,
    apply_preset_config,
    get_config,
    save_config,
)
from .preset import get_templates

router = APIRouter()


@router.get("/state")
def swab_state():
    """当前棉签状态：已用 / 上限 / 剩余 / 是否锁定。前端面板轮询此接口。"""
    return get_state()


@router.post("/reset")
def swab_reset():
    """手动更换棉签（清零解锁），等同视角2 检出换棉签。"""
    return reset_swab()


@router.post("/reload-config")
def swab_reload_config():
    """改了系统配置后重新加载插件配置。"""
    return reload_config()


@router.get("/config")
def swab_get_config():
    """获取当前完整插件配置（前端配置面板预填：三判定事件映射 + 阈值 + 计数参数）。"""
    return get_config()


@router.post("/config")
def swab_save_config(patch: dict = Body(...)):
    """保存插件配置（合并写库 + 热加载）。前端配置 Tab 保存走此接口。"""
    return save_config(patch)


@router.get("/templates")
def swab_templates():
    """获取对齐 demo 的项目配置模板（视角1/视角2）+ 耗材默认配置。"""
    return get_templates()


@router.post("/apply-preset")
def swab_apply_preset():
    """一键应用：把对齐 demo 的最优计数/耗材参数写库持久化 + 热加载，
    并返回视角1/视角2 的项目+模型配置供前端应用到主程序通道（全部可再改）。"""
    return apply_preset_config()
