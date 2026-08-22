/**
 * monitorModes.js — 监控页模式/步骤视图纯函数（巨石重构阶段1）
 *
 * 只放无副作用的纯计算：从 results 载荷 + 兜底项目配置推导
 * 步骤表行 / SOP 卡片 / 隐藏标签集 / 逻辑模式。
 * 不 import Vue、不碰 DOM、不读全局 store —— 一切靠参数传入，vitest 可直测。
 *
 * 语义契约（勿改，行为与 v3.54.1 的 processChannelResult 内联版逐行等价）：
 * - tableData / sopSteps 仅在 (d.detections || region_events) 时产出，否则返回
 *   null 表示"本轮不更新，保留旧值"。
 * - region_events 步骤来自 pipeline_config.region_events.rules 规则名
 *   （v3.32 起后端 results 载荷已带；载荷缺失时回退兜底项目的 pipeline_config）。
 * - tracking 模式按 item_checklist counted>0 判完成，容器模式扫 _boxes；
 *   SOP/步骤表只展示"每箱期望物品"，未填清单时仅排除容器 label。
 * - SOP 卡片"图永不空"：后端无新图时按 label 从上一轮继承缩略图。
 */

/** 区域事件模式：按动作规则名建 SOP 步骤卡（v3.54.1） */
export const regionEventRuleSteps = (pipelineConfig) =>
  (pipelineConfig?.region_events?.rules || [])
    .filter(r => r && r.name)
    .map((r, i) => ({
      id: `re_${r.id || i}`,
      label: r.name,
      displayLabel: r.name,
      enabled: true,
    }));

/** 工位逻辑模式判定：轮询配置优先，回退兜底项目（阶段3模式面板同源） */
export const resolveLogicMode = (pollProjectConfig, fallbackProject) =>
  pollProjectConfig?.logic_mode || fallbackProject?.logic_mode;

/**
 * 从单通道 results 载荷构建步骤视图。
 *
 * @param {object} d 后端 /source/detection/results 单通道载荷
 * @param {object|null} fallbackProject 兜底项目（全局 currentProject；载荷缺配置时用）
 * @param {object} chState 通道已累计状态：
 *   { currentCycleSteps, backupCoveredLabels, stepInflightDurations, prevSteps }
 * @returns {{ tableData: Array|null, sopSteps: Array|null, hiddenLabels: Set }}
 *   tableData/sopSteps 为 null = 本轮不更新；hiddenLabels 每轮都产出。
 */
export function buildChannelStepViews(d, fallbackProject, chState) {
  const fallback = fallbackProject || null;
  const currentCycleSteps = chState.currentCycleSteps || [];
  const backupCoveredLabels = chState.backupCoveredLabels || [];
  const stepInflightDurations = chState.stepInflightDurations || {};
  const prevSteps = chState.prevSteps || [];

  // 跟踪模式：后端不推 _currentCycleSteps，改用 tracking.item_checklist
  // 把 counted > 0 的步骤翻成 completed/OK（v3.1.3）
  const _isTracking = resolveLogicMode(d.project_config, fallback) === 'tracking';
  const _trackChecklist = _isTracking ? (d.tracking?.item_checklist || {}) : null;
  const _trackBoxes = _isTracking ? (d.tracking?.boxes || {}) : null;
  const _isContainer = _isTracking && !!d.tracking?.container_mode;
  const _trackHit = (label) => {
    if (!_isTracking) return false;
    if (_isContainer && _trackBoxes) {
      // 容器模式: 任意箱子里 counted > 0 即视为已检测
      for (const bid of Object.keys(_trackBoxes)) {
        const items = _trackChecklist?._boxes?.[bid]?.items;
        if (items && items[label] && items[label].counted > 0) return true;
      }
      return false;
    }
    return !!(_trackChecklist?.[label] && _trackChecklist[label].counted > 0);
  };

  // v3.2.1: 跟踪模式下，步骤统计/SOP 只展示"每箱期望物品"中的项目，
  // 排除作为"容器"的箱子类别
  const _trkExpectedLabels = (() => {
    if (!_isTracking) return null;
    const projCfg = d.project_config || fallback || {};
    const pipeCfg = projCfg.pipeline_config || {};
    const expList = projCfg.counting_expected_list
      || pipeCfg.counting_expected_list
      || [];
    const expDict = pipeCfg.counting_expected_items || {};
    const set = new Set();
    expList.forEach(it => { if (it && it.label) set.add(it.label); });
    Object.keys(expDict).forEach(l => set.add(l));
    if (set.size > 0) return set;
    // 兜底:未填清单时仅排除容器 label
    const containerLabel = projCfg.tracking_container_label
      || pipeCfg.tracking_container_label
      || '';
    return containerLabel ? { _excludeContainer: containerLabel } : null;
  })();
  const _trkAllow = (label) => {
    if (!_trkExpectedLabels) return true;
    if (_trkExpectedLabels instanceof Set) return _trkExpectedLabels.has(label);
    if (_trkExpectedLabels._excludeContainer) {
      return label !== _trkExpectedLabels._excludeContainer;
    }
    return true;
  };

  const _logicMode = resolveLogicMode(d.project_config, fallback);
  const _isRegionEvents = _logicMode === 'region_events';
  const _resultRegionRules = d.project_config?.pipeline_config?.region_events?.rules;
  const _stepsConf = _isRegionEvents
    ? regionEventRuleSteps(
      Array.isArray(_resultRegionRules)
        ? d.project_config.pipeline_config
        : fallback?.pipeline_config
    )
    : (d.project_config?.steps_config || fallback?.steps_config || []);
  const _stepIsActive = (label) =>
    _isRegionEvents && (stepInflightDurations[label] || 0) > 0;

  let tableData = null;
  if (d.detections || _isRegionEvents) {
    // v3.31.x 语义收窄: hide_in_view 只隐藏画面检测框, SOP/步骤详情照常显示
    tableData = _stepsConf
      .filter(s => s.enabled !== false && !s.is_backup && _trkAllow(s.label))
      .map((s) => {
        const inCycle = currentCycleSteps.includes(s.label);
        const coveredByBackup = backupCoveredLabels.includes(s.label);
        const trackHit = _trackHit(s.label);
        // v3.8.x (二次修订): 多工位"已完成"判定与单工位对齐 —
        // 步骤进过 cycle_steps 就算完成, 不再硬等权威 PT 写入。
        return {
          step: s.displayLabel || s.label,
          label: s.label,
          status: (inCycle || coveredByBackup || trackHit)
            ? 'completed'
            : (_stepIsActive(s.label) ? 'active' : 'pending'),
          cycleResult: trackHit ? 'ok' : null,
        };
      });
  }

  let sopSteps = null;
  if (d.detections || _isRegionEvents) {
    const screenshots = d.step_screenshots || {};
    // v3.10.x: SOP 卡片"图永不空"策略 — 后端有新图就替换, 没有就从上一轮按 label 继承
    const prevSopByLabel = Object.fromEntries(
      prevSteps.map(s => [s.label, s.screenshot])
    );
    sopSteps = _stepsConf
      .filter(s => s.enabled !== false && !s.is_backup && _trkAllow(s.label))
      .map(s => {
        const inCycle = currentCycleSteps.includes(s.label);
        const coveredByBackup = backupCoveredLabels.includes(s.label);
        const trackHit = _trackHit(s.label);
        const rawB64 = screenshots[s.label];
        return {
          name: s.displayLabel || s.label,
          label: s.label,
          status: (inCycle || coveredByBackup || trackHit)
            ? 'completed'
            : (_stepIsActive(s.label) ? 'active' : 'pending'),
          screenshot: rawB64
            ? `data:image/jpeg;base64,${rawB64}`
            : (prevSopByLabel[s.label] || null),
        };
      });
  }

  // v2.7.4: "隐藏标注框"label 集合，drawMultiDetections 据此跳过画框
  // v3.31.x 语义收窄: 仅影响实时画面检测框
  const hiddenStepsConf = d.project_config?.steps_config || fallback?.steps_config || [];
  const hiddenLabels = new Set(
    hiddenStepsConf.filter(s => s && s.hide_in_view && s.label).map(s => s.label)
  );

  return { tableData, sopSteps, hiddenLabels };
}
