/**
 * 监控画面该画哪些检测框的标签集合。
 *
 * 历史: 只画 steps_config 里 enabled 的步骤行。混合逐件把「螺丝锁付-已完成」
 * 这类动作标签单开一行并停用 (避免进 SOP 序列刷噪音) 后, 后端配对引擎仍在
 * 用它记账, 但单工位 overlay 把框整段跳过 → 现场「打螺丝没有已完成框」
 * (六和二工位 2026-09-20, 与后端 _get_enabled_labels 豁免同构)。
 */
export function collectOverlayDrawLabels(stepsConfig) {
  const labels = new Set();
  for (const step of stepsConfig || []) {
    if (!step) continue;
    if (step.enabled !== false && step.label) labels.add(step.label);
    const per = step.per_item;
    if (!per || typeof per !== 'object') continue;
    for (const key of ['item_label', 'action_label']) {
      const val = per[key];
      if (typeof val === 'string' && val.trim()) labels.add(val.trim());
      else if (Array.isArray(val)) {
        for (const v of val) {
          if (typeof v === 'string' && v.trim()) labels.add(v.trim());
        }
      }
    }
  }
  return labels;
}
