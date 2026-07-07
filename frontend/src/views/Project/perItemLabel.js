// ==================== 逐件配对 item_label 换算助手（2026-07 拆分批次 P-4 自 index.vue 外置） ====================
// v3.9+ per_item: item_label 兼容 string / array 两种存储
//   - el-select multiple 需要 array
//   - 后端兼容 string 单个 + array 多个 (字符串=单标签, 数组=OR)
//   - 序列化时只有 1 个时落回字符串 (老前端继续可读)
// 步骤设置 Tab（index.vue 表C）与逻辑设置 Tab（LogicConfigTab.vue）共用。
export function _pi_itemLabelToArray(val) {
  if (Array.isArray(val)) return val.filter(s => typeof s === 'string' && s);
  if (typeof val === 'string' && val) return [val];
  return [];
}

export function _pi_itemLabelFromArray(arr) {
  const clean = (arr || []).filter(s => typeof s === 'string' && s.trim()).map(s => s.trim());
  if (clean.length === 0) return '';
  if (clean.length === 1) return clean[0];
  return clean;
}
