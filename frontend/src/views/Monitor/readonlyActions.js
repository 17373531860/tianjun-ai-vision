// 只读展示保留查询、格式化和本窗绘制；未来新增 action 默认视作写操作。
const READONLY_ACTIONS = new Set([
  'formatVideoTime', 'renderDetectionOverlay', 'setFrameNaturalSize',
  'shouldShowMesBarFor', 'getDisplayWorkpieceFor', 'getMesDataFor',
  'getTaskInfoItemsFor', 'hasScannerFor', 'isScanDisabledFor',
  'getScannerDisableToggling', 'getDisplayCT', 'getStepTableColumns',
  'isStepTableEnabled', 'getMonitorDisplay', 'formatStepPTForChannel',
  'renderStepCellDuration', 'renderStepCellStatus', 'isMonitorSlotHidden',
]);

export function protectMonitorActions(actions, isReadonly) {
  return Object.fromEntries(Object.entries(actions).map(([name, action]) => [
    name,
    READONLY_ACTIONS.has(name) ? action : (...args) => {
      if (isReadonly()) return Promise.resolve();
      return action(...args);
    },
  ]));
}
