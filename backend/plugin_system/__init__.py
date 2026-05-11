"""后端插件系统入口。

在 import 时为 `tianjun.plugin` logger 挂一个 stdout handler（如果还没挂）。
这样 PluginManager / registry / 插件 hook 触发等日志能被 uvicorn 抓到, 客户
现场排错 (`app.log`) 和单元测试都能看到。
"""
from __future__ import annotations

import logging
import sys


_plugin_log = logging.getLogger("tianjun.plugin")
if not _plugin_log.handlers:
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(logging.Formatter("%(asctime)s [Plugin] %(levelname)s %(message)s"))
    _plugin_log.addHandler(_h)
    _plugin_log.setLevel(logging.INFO)
    _plugin_log.propagate = False
