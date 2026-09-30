// 检测主页逻辑分区共用同一套字号，默认值保持已有视觉效果。
export const MODULE_FONT_OPTIONS = [
  { key: 'videoHud', label: '画面角标' },
  { key: 'sop', label: 'SOP流程卡片' },
  { key: 'stats', label: '计数卡片' },
  { key: 'charts', label: '统计图表' },
  { key: 'stepTable', label: '步骤统计' },
  { key: 'sessionBar', label: '会话 ID 输入条' },
  { key: 'controls', label: '操作按钮' },
  { key: 'mesBar', label: '工件/工单信息条' },
];

export const DEFAULT_MODULE_FONT_SCALE = Object.fromEntries(
  MODULE_FONT_OPTIONS.map(({ key }) => [key, 100]),
);

export function getModuleFontScale(scales, key) {
  const percent = Number(scales?.[key] ?? 100);
  // 旧设置缺项或损坏时回退默认；外部导入仍遵守滑块的整数档位。
  if (!Number.isFinite(percent)) return 1;
  return Math.max(80, Math.min(200, Math.round(percent / 10) * 10)) / 100;
}
