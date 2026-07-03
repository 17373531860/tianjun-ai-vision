// ==================== 自定义混合模式物品行默认值（2026-07 拆分批次 P-5 自 index.vue 外置） ====================
// 角色切换：切到"物品"时按混合类型初始化原生字段；切回"步骤"保留参数（再切回来不丢配置）。
// index.vue（逻辑 Tab 混合类型切换回调）与 StepsConfigTab.vue（表A角色列切换）共用。
export function ensureMixItemDefaults(project, step) {
  const mixType = project?.custom_mixed_with;
  if (mixType === 'tracking') {
    // 原生跟踪行字段 (与独立跟踪模式同名同义)
    if (step.count_mode === undefined) step.count_mode = 'track';
    if (step.expected_count === undefined) {
      step.expected_count = step.mix_item?.expected_count ?? 1;  // 旧存储兜底
    }
  } else if (mixType === 'per_item') {
    // 原生逐件配对字段 (与独立逐件模式 steps_config[i].per_item 同名同义)
    if (!step.per_item) {
      step.per_item = {
        item_label: step.label || '',
        action_label: '',
        item_tracking_iou: 0.3,
        coverage_iou: 0.3,
        coverage_use_center: false,
        sustain_frames: 5,
        expected_count: 0,
        completion: 'all_covered',
      };
    }
  }
}
