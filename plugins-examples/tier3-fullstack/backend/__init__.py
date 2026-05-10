"""Tier 3 full-stack 示例插件入口。"""

from .hooks import on_cycle_end_post
from .models import PluginInspectionNote
from .routes import router


def register_plugin(app, registry, license_payload, host):
    """主程序 PluginManager 调用的注册入口。

    真实 registry API 见 docs/plugin-system/design/06_tier3_fullstack.md。
    这里故意只调用设计里约定的最小接口，便于后续主程序实现时对齐。
    """
    registry.tables.register(PluginInspectionNote)
    registry.routes.include_router(router, subpath="demo")
    registry.hooks.register(
        hook_type="cycle_end",
        phase="post_cycle",
        when="post",
        priority=100,
        handler=on_cycle_end_post,
    )

    return {
        "name": "fullstack-mes-extension",
        "customer_code": license_payload.get("customerName"),
    }
