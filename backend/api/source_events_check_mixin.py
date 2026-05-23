"""事件 FSM (从 CheckModesMixin 拆出)"""
import time
import traceback
import cv2
import numpy as np


class EventsCheckMixin:
    def _check_events(self, completed_step: str):
        """检查是否触发事件"""
        if not self.project_config:
            return
        
        events_config = self.project_config.get('events_config', [])
        logic_mode = self.project_config.get('logic_mode', 'detection')
        pipeline_config = self.project_config.get('pipeline_config', {})
        steps_config = self.project_config.get('steps_config', [])
        
        # 创建步骤ID到标签的映射
        id_to_label = {}
        label_to_id = {}
        for step in steps_config:
            step_id = step.get('id')
            label = step.get('label', '')
            if step_id and label:
                id_to_label[step_id] = label
                label_to_id[label] = step_id
        
        # 获取启用的步骤标签和ID集合
        enabled_step_labels = [s.get('label') for s in steps_config if s.get('enabled', True)]
        enabled_step_ids = {s.get('id') for s in steps_config if s.get('enabled', True)}
        
        # 自定义模式
        if logic_mode == 'custom':
            custom_based_on = pipeline_config.get('custom_based_on')  # 'sequential', 'detection', 或 None
            custom_conditions = pipeline_config.get('custom_conditions', [])
            
            self.current_cycle_steps = self._filter_cycle_by_duration(self.current_cycle_steps)
            
            # 先检查自定义条件（按优先级排序）
            condition_matched = False
            if custom_conditions:
                # 按优先级排序（数字越小优先级越高）
                sorted_conditions = sorted(custom_conditions, key=lambda c: c.get('priority', 999))
                
                for cond in sorted_conditions:
                    cond_sequence = cond.get('sequence', [])
                    cond_event_id = cond.get('event_id')
                    
                    if not cond_sequence or not cond_event_id:
                        continue
                    
                    # 将条件中的步骤ID转换为标签（只包含启用的步骤）
                    cond_labels = [id_to_label.get(sid) for sid in cond_sequence if sid in id_to_label and sid in enabled_step_ids]
                    
                    if not cond_labels:
                        continue
                    
                    print(f"自定义条件检查: 条件序列={cond_labels}, 当前周期={self.current_cycle_steps}")
                    
                    # 检查是否完全匹配（数量和顺序都要相同）
                    if self.current_cycle_steps == cond_labels:
                        print(f"  → 条件匹配！触发事件 {cond_event_id}")
                        self._trigger_event(cond_event_id, f'自定义条件匹配: {cond_labels}')
                        self.current_cycle_steps = []
                        self.backup_steps_seen_in_cycle = set()
                        self.last_added_step = None
                        return  # 匹配后不再检查其他条件和基础模式
            
            # 没有自定义条件匹配，回退到基础模式
            # first_step / last_first 模式跳过"最后一步消失"触发:
            #   - first_step: 由第一步重现触发结算
            #   - last_first (v3.8.x): 由末步"出现"立即触发 (在 _process_last_first_mode R1 处理)
            if self.settlement_mode in ('first_step', 'last_first'):
                pass
            elif custom_based_on == 'sequential':
                last_step_label = self._get_last_sequence_step_label()
                if last_step_label and completed_step == last_step_label:
                    if completed_step in self.current_cycle_steps:
                        self._check_custom_sequential_mode(pipeline_config, id_to_label)
                    else:
                        print(f"  → 步骤 [{completed_step}] 不在当前周期中，跳过判定（可能是上一周期的残留）")
            elif custom_based_on == 'detection':
                if completed_step in self.current_cycle_steps:
                    self._check_custom_detection_mode(pipeline_config, id_to_label, enabled_step_labels)
                else:
                    print(f"  → 步骤 [{completed_step}] 不在当前周期中，跳过判定（可能是上一周期的残留）")
        
        # 顺序模式
        elif logic_mode == 'sequential':
            # last_first 模式跳过末步消失结算 (R1 在 _process_last_first_mode 已处理)
            if self.settlement_mode not in ('first_step', 'last_first'):
                last_step_label = self._get_last_sequence_step_label()
                if last_step_label and completed_step == last_step_label:
                    if completed_step in self.current_cycle_steps:
                        self._check_sequential_mode(pipeline_config, id_to_label)
                    else:
                        print(f"  → 步骤 [{completed_step}] 不在当前周期中，跳过判定（可能是上一周期的残留）")
        
        # 检测模式 (last_first 不适用于 detection 模式, 互斥校验已阻止此组合)
        elif logic_mode == 'detection':
            if self.settlement_mode != 'first_step':
                last_det_label = self._get_last_detection_step_label()
                if last_det_label and completed_step == last_det_label:
                    if completed_step in self.current_cycle_steps:
                        self._settle_detection_cycle()
                    else:
                        print(f"  → 步骤 [{completed_step}] 不在当前周期中，跳过判定（可能是上一周期的残留）")
        
        # 检查步骤特定事件
        for step in steps_config:
            if step.get('label') == completed_step:
                trigger_event = step.get('trigger_event')
                if trigger_event:
                    self._trigger_event(trigger_event, f'步骤 {completed_step} 触发')
