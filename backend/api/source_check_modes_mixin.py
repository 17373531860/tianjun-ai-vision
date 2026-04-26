"""判定模式聚合 mixin (拆分自原 971L 上帝 mixin, v2.7.x)。

实际逻辑在以下 4 个独立 mixin 中:
  - ContainerGroupingMixin  (容器分组 + per-box 结算)
  - ChecklistMixin          (checklist 维护 + counting 模式)
  - EventsCheckMixin        (事件 FSM)
  - SequentialMixin         (顺序 / 自定义顺序 / 自定义检测)

CheckModesMixin 通过多继承聚合, 对外行为完全等价于原版。
"""
from backend.api.source_container_grouping_mixin import ContainerGroupingMixin
from backend.api.source_checklist_mixin import ChecklistMixin
from backend.api.source_events_check_mixin import EventsCheckMixin
from backend.api.source_sequential_mixin import SequentialMixin


class CheckModesMixin(
    ContainerGroupingMixin,
    ChecklistMixin,
    EventsCheckMixin,
    SequentialMixin,
):
    """聚合 mixin: 提供原 CheckModesMixin 的 9 个方法, 由 4 个子 mixin 实现。"""
    pass
