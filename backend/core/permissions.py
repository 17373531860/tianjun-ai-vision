"""
权限系统 — permission catalog + 通配符匹配 + 内置角色定义

permission key 命名约定:
  <module>.<action>      monitor.view / project.edit / alarm.write
  "*"                    通配所有模块 (仅 admin 角色)
  <module>.*             通配某模块所有 action

匹配规则 (match_permission):
  required = "project.edit"
  user_perms = ["*"]               → match
  user_perms = ["project.*"]       → match
  user_perms = ["project.edit"]    → match
  user_perms = ["project.view"]    → not match

内置三角色:
  admin    = ["*"]               超级管理员, 全部权限
  engineer = [一组通配符...]      工程师, 可调项目/源/模型/报警/数据/MES
  operator = [极简列表]           操作员, 只能启停检测和待机
"""
from typing import List


# ============================================================
# 通配符匹配
# ============================================================

def match_permission(required: str, user_perms: List[str]) -> bool:
    """检查 user_perms 列表是否覆盖 required 这一权限.

    支持的通配:
      "*"            → 通配全部
      "project.*"    → 通配 project 模块所有 action
      "project.edit" → 精确匹配
    """
    if not required:
        return True
    if not user_perms:
        return False

    for p in user_perms:
        if not p:
            continue
        if p == "*":
            return True
        if p == required:
            return True
        if p.endswith(".*"):
            prefix = p[:-2]  # "project"
            if required == prefix or required.startswith(prefix + "."):
                return True
    return False


# ============================================================
# 内置角色定义
# ============================================================

BUILTIN_ROLES = {
    "admin": {
        "name": "超级管理员",
        "description": "最高权限, 可管理所有功能含账号体系与 License",
        "permissions": ["*"],
    },
    "engineer": {
        "name": "工程师",
        "description": "可调试项目/源/模型/报警/数据/MES; 不能动账号与 License",
        "permissions": [
            "monitor.*",
            "project.*",
            "source.*",
            "model.*",
            "alarm.*",
            "data.*",
            "mes.*",
            "settings.view",
            "settings.operators.*",
            "system.view",
            "system.packaging_flow.view",
            "system.packaging_flow.manage",
            "system.packaging_flow.force_settle",
        ],
    },
    "operator": {
        "name": "操作员",
        "description": "产线一线身份, 默认匿名进入; 仅可启停检测与切换待机",
        "permissions": [
            "monitor.view",
            "monitor.detection.control",
        ],
    },
}


# ============================================================
# 权限目录 (catalog) — 给前端账号管理页渲染勾选树
# ============================================================

# {key: {key, label, group}}
_PERMISSION_CATALOG: dict = {}


def register_permission(key: str, label: str, group: str = "其它") -> None:
    """注册一个 permission 到 catalog"""
    _PERMISSION_CATALOG[key] = {"key": key, "label": label, "group": group}


def get_permission_catalog() -> list:
    """返回 permission catalog, 给前端账号管理页渲染权限树"""
    return list(_PERMISSION_CATALOG.values())


# === 启动时一次性注册所有 permission key ===
# 监控
register_permission("monitor.view", "查看监控页", "监控")
register_permission("monitor.detection.control", "启停检测 / 待机", "监控")
register_permission(
    "monitor.detection.advanced",
    "清零计数 / 重置周期性动作 (高级操作, 工程师起步)",
    "监控",
)
register_permission(
    "monitor.detection.ack",
    "确认 / 解除人工确认 NG (默认仅工程师 / 管理员; 操作员需借密码提权)",
    "监控",
)

# 项目
register_permission("project.view", "查看项目", "项目")
register_permission("project.edit", "编辑项目配置", "项目")
register_permission("project.create", "新建项目", "项目")
register_permission("project.delete", "删除项目", "项目")
register_permission("project.activate", "切换激活项目", "项目")

# 视频源
register_permission("source.view", "查看视频源配置", "视频源")
register_permission("source.edit", "编辑视频源配置", "视频源")

# 模型
register_permission("model.view", "查看模型仓库", "模型")
register_permission("model.upload", "上传 / 转换模型", "模型")
register_permission("model.delete", "删除模型", "模型")

# 报警
register_permission("alarm.view", "查看报警配置", "报警")
register_permission("alarm.edit", "编辑报警配置", "报警")

# 数据
register_permission("data.view", "查看数据中心", "数据")
register_permission("data.export", "导出数据", "数据")
register_permission("data.cleanup", "数据清理与维护", "数据")
register_permission("data.backup_restore", "数据备份 / 恢复", "数据")

# MES
register_permission("mes.order.view", "查看工单", "MES")
register_permission("mes.order.edit", "编辑工单", "MES")
register_permission("mes.workpiece.view", "查看工件", "MES")
register_permission("mes.workpiece.edit", "编辑工件", "MES")
register_permission("mes.defect.view", "查看缺陷", "MES")
register_permission("mes.defect.edit", "编辑缺陷", "MES")
register_permission("mes.scanner.view", "查看扫码器配置", "MES")
register_permission("mes.scanner.edit", "编辑扫码器配置", "MES")
register_permission("mes.gateway.view", "查看 MES 网关", "MES")
register_permission("mes.gateway.edit", "编辑 MES 网关", "MES")
register_permission("mes.cluster.view", "查看集群配置", "MES")
register_permission("mes.cluster.edit", "编辑集群配置", "MES")
register_permission("mes.external.view", "查看外部设备", "MES")
register_permission("mes.external.edit", "编辑外部设备", "MES")

# 设置
register_permission("settings.view", "查看系统设置", "设置")
register_permission("settings.edit", "编辑系统设置", "设置")
register_permission("settings.operators.view", "查看操作员管理 (旧名册)", "设置")
register_permission("settings.operators.edit", "编辑操作员管理 (旧名册)", "设置")

# 系统
register_permission("system.view", "查看系统状态", "系统")
register_permission("system.users.manage", "管理账号", "系统")
register_permission("system.roles.manage", "管理角色", "系统")
register_permission("system.auth_toggle", "启用 / 关闭账号鉴权系统", "系统")
register_permission("system.license.manage", "管理 License 授权", "系统")
register_permission("system.plugin.manage", "管理客户定制插件 (安装 / 启停 / 删除)", "系统")
register_permission("system.apikey.manage", "管理 M2M API Key (副机 / 外部 MES / IPC)", "系统")
# v3.13 RFC 10: 工位组 (单机内多通道结算联动)
register_permission("system.channel_group.view", "查看工位组配置", "系统")
register_permission("system.channel_group.manage", "管理工位组 (创建 / 编辑 / 删除)", "系统")
register_permission("system.workpiece_flow.view", "查看流水线串行配置与历史", "系统")
register_permission("system.workpiece_flow.manage", "管理流水线串行 (创建 / 编辑 / 删除)", "系统")
register_permission("system.packaging_flow.view", "查看包装箱结算配置与进度", "系统")
register_permission("system.packaging_flow.manage", "管理包装箱结算 (创建 / 编辑 / 删除)", "系统")
register_permission(
    "system.packaging_flow.force_settle",
    "强制结案进行中工单 (管理员 / 主管; 需填理由)",
    "系统",
)
