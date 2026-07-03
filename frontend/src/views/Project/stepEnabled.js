// ==================== 步骤启用状态变化清理/恢复（2026-07 拆分批次 P-5 自 index.vue 外置） ====================
// 禁用步骤时从序列/检测清单/自定义条件里移除引用, 启用时加回。
// StepsConfigTab.vue（表A启用开关）与 index.vue（副模型步骤清理 _purgeStepsByFromModel）共用。
export function applyStepEnabledChange(project, step, enabled) {
  const stepId = step.id;

  if (!enabled) {
    // === 禁用：从所有配置中移除 ===
    if (project.sequence_order) {
      project.sequence_order = project.sequence_order.filter(
        item => item.step_id !== stepId
      );
    }
    if (project.detection_steps) {
      project.detection_steps = project.detection_steps.filter(
        id => id !== stepId
      );
    }
    if (project.custom_sequence_order) {
      project.custom_sequence_order = project.custom_sequence_order.filter(
        item => item.step_id !== stepId
      );
    }
    if (project.custom_detection_steps) {
      project.custom_detection_steps = project.custom_detection_steps.filter(
        id => id !== stepId
      );
    }
    if (project.custom_conditions) {
      project.custom_conditions.forEach(cond => {
        if (cond.sequence) {
          cond.sequence = cond.sequence.filter(id => id !== stepId);
        }
      });
    }
    console.log(`步骤 [${step.label}] 已禁用，已从所有配置中移除`);
  } else {
    // === 启用：加回到相关配置中 ===
    if (!project.sequence_order) project.sequence_order = [];
    const alreadyInSeq = project.sequence_order.some(item => item.step_id === stepId);
    if (!alreadyInSeq) {
      project.sequence_order.push({ step_id: stepId });
    }

    if (!project.detection_steps) project.detection_steps = [];
    if (!project.detection_steps.includes(stepId)) {
      project.detection_steps.push(stepId);
    }

    if (!project.custom_sequence_order) project.custom_sequence_order = [];
    const alreadyInCustomSeq = project.custom_sequence_order.some(item => item.step_id === stepId);
    if (!alreadyInCustomSeq) {
      project.custom_sequence_order.push({ step_id: stepId });
    }

    if (!project.custom_detection_steps) project.custom_detection_steps = [];
    if (!project.custom_detection_steps.includes(stepId)) {
      project.custom_detection_steps.push(stepId);
    }

    console.log(`步骤 [${step.label}] 已启用，已加回到配置中`);
  }
}
