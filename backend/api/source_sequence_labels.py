"""序列/检测步骤标签计算 (P7 第九刀)

从 VideoSourceManager 中分离出来的纯粹"标签查询"逻辑：
  - 在 sequential / custom 模式下, 计算预期的步骤序列
  - 在 detection 模式下, 计算需要检测的步骤标签集合
  - 判断给定序列是否是某条 custom_condition 的前缀

设计原则：
  - 完全无状态 (除了一个 host 反向引用用来读 project_config)
  - 所有结果只取决于 self._host.project_config
  - 主类通过 _COMPONENT_ROUTES 把历史方法名 (`_get_first_sequence_step_label` 等)
    自动转发到本组件, 调用方零修改

历史背景：
  这 7 个方法散落在 VSM 类内部 ~190 行, 实质上是项目配置解析的"投影函数"
  (project_config → 标签列表), 拆出去后 VSM 类只需关心状态机本身.
"""
from __future__ import annotations

from typing import List, Optional


class SequenceLabels:
    """步骤标签查询器, 由 VideoSourceManager 持有"""

    def __init__(self, host):
        self._host = host

    @property
    def project_config(self) -> Optional[dict]:
        return getattr(self._host, 'project_config', None)

    # ============================================================
    # 内部 helper: 把 steps_config 解析为 (id→label, enabled_id_set)
    # ============================================================
    @staticmethod
    def _index_steps(steps_config: list):
        id_to_label = {}
        enabled_ids = set()
        for step in steps_config or []:
            sid = step.get('id')
            label = step.get('label', '')
            if sid and label:
                id_to_label[sid] = label
                if step.get('enabled', True):
                    enabled_ids.add(sid)
        return id_to_label, enabled_ids

    def _sequence_order(self) -> list:
        """根据 logic_mode 取 sequence_order 或 custom_sequence_order"""
        cfg = self.project_config or {}
        logic_mode = cfg.get('logic_mode', 'sequential')
        pipeline = cfg.get('pipeline_config', {})
        if logic_mode == 'custom':
            seq = pipeline.get('custom_sequence_order', []) or []
        else:
            seq = pipeline.get('sequence_order', []) or []
        if seq:
            return seq
        # API/脚本创建的项目常缺 sequence_order — 用 steps_config 顺序兜底
        steps = cfg.get('steps_config', []) or []
        fallback = []
        for step in steps:
            if step.get('enabled', True) and not step.get('is_backup') and not step.get('backup_for'):
                sid = step.get('id')
                if sid is not None:
                    fallback.append({'step_id': sid})
        return fallback

    # ============================================================
    # 公共查询接口
    # ============================================================
    def get_first_step_label(self) -> Optional[str]:
        """顺序模式下第一个启用步骤的 label (custom 模式用 custom_sequence_order)"""
        cfg = self.project_config
        if not cfg:
            return None
        seq = self._sequence_order()
        steps = cfg.get('steps_config', [])
        if not steps:
            return None
        id_to_label, enabled_ids = self._index_steps(steps)
        for item in seq:
            sid = item.get('step_id')
            if sid in enabled_ids:
                return id_to_label.get(sid)
        return None

    def get_last_step_label(self) -> Optional[str]:
        """顺序模式下最后一个启用步骤的 label"""
        cfg = self.project_config
        if not cfg:
            return None
        seq = self._sequence_order()
        steps = cfg.get('steps_config', [])
        if not steps:
            return None
        id_to_label, enabled_ids = self._index_steps(steps)
        for item in reversed(seq):
            sid = item.get('step_id')
            if sid in enabled_ids:
                return id_to_label.get(sid)
        return None

    def get_expected_labels(self) -> List[str]:
        """当前模式下预期的步骤标签有序列表"""
        cfg = self.project_config
        if not cfg:
            return []
        seq = self._sequence_order()
        steps = cfg.get('steps_config', [])
        if not seq or not steps:
            return []
        id_to_label, enabled_ids = self._index_steps(steps)
        out = []
        for item in seq:
            sid = item.get('step_id')
            if sid in id_to_label and sid in enabled_ids:
                out.append(id_to_label[sid])
        return out

    def get_detection_labels(self) -> List[str]:
        """检测模式下需要检测的步骤标签 (按 steps_config 配置顺序)"""
        cfg = self.project_config
        if not cfg:
            return []
        pipeline = cfg.get('pipeline_config', {})
        steps = cfg.get('steps_config', [])
        detection_step_ids = pipeline.get('detection_steps', []) or []

        ordered_enabled = []
        for step in steps:
            sid = step.get('id')
            label = step.get('label', '')
            if sid and label and step.get('enabled', True):
                ordered_enabled.append((sid, label))
        if detection_step_ids:
            det_set = set(detection_step_ids)
            return [label for sid, label in ordered_enabled if sid in det_set]
        return [label for _, label in ordered_enabled]

    def get_first_detection_label(self) -> Optional[str]:
        labels = self.get_detection_labels()
        return labels[0] if labels else None

    def get_last_detection_label(self) -> Optional[str]:
        labels = self.get_detection_labels()
        return labels[-1] if labels else None

    def is_legitimate_next_in_sequence(self, label: str) -> bool:
        """v3.7.0: 当前 label 加进 current_cycle_steps 后是否仍是 expected_labels 的合法前缀.

        客户场景: 期望序列 A-B-C-B-D 里 B 合法出现 2 次。第二个 B 触发时,
        旧代码只看 "label 已在 current_cycle_steps 里" 就标记回退 → 误判 NG.
        这里给出位置感知判断:
          - 仅在顺序型模式有意义 (sequential / custom-based-on-sequential)。
          - 若 expected_labels[len(current)] == label 就合法。
          - 检测模式不调用本方法 (那种模式按 count 判 NG, 走 settle 里的 expected_counter)。
        """
        cur = getattr(self._host, "current_cycle_steps", None)
        if cur is None:
            return False
        expected = self.get_expected_labels()
        if not expected:
            return False
        next_pos = len(cur)
        if next_pos >= len(expected):
            return False
        return expected[next_pos] == label

    def is_condition_prefix(self, sequence_to_check: list) -> bool:
        """判断给定序列是否是任意自定义条件序列的前缀 (custom 模式)

        Returns:
            True 如果是任意启用条件的前缀, False 否则
        """
        cfg = self.project_config
        if not cfg:
            return False
        pipeline = cfg.get('pipeline_config', {})
        custom_conditions = pipeline.get('custom_conditions', []) or []
        steps = cfg.get('steps_config', [])
        if not custom_conditions:
            return False

        id_to_label, enabled_ids = self._index_steps(steps)

        for cond in custom_conditions:
            cond_seq = cond.get('sequence', [])
            if not cond_seq:
                continue
            cond_labels = [
                id_to_label.get(sid)
                for sid in cond_seq
                if sid in id_to_label and sid in enabled_ids
            ]
            if not cond_labels:
                continue
            if len(sequence_to_check) <= len(cond_labels):
                if all(
                    sequence_to_check[i] == cond_labels[i]
                    for i in range(len(sequence_to_check))
                ):
                    print(
                        f"  前缀匹配成功: {sequence_to_check} 是条件 {cond_labels} 的前缀"
                    )
                    return True
        return False
