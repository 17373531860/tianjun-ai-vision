"""Page Object 模式：用于浏览器 E2E 测试时把页面交互封装成类。

约定：
- 每个 Page Object 接收 playwright `page` 与 `base_url`。
- 方法名以业务动词为主（goto / wait_loaded / click_xxx / get_xxx），不直接暴露 selector。
- selector 集中在类顶部 `class Selectors:` 内部，方便统一维护。
- 文件命名小写下划线；类名 PascalCase + Page 后缀。
"""
from .base_page import BasePage
from .monitor_page import MonitorPage
from .project_page import ProjectPage
from .source_page import SourcePage
from .data_page import DataPage
from .settings_page import SettingsPage

__all__ = [
    "BasePage",
    "MonitorPage",
    "ProjectPage",
    "SourcePage",
    "DataPage",
    "SettingsPage",
]
