/**
 * monitorModes.js — 监控页模式/步骤视图纯函数（巨石重构阶段1）
 *
 * 只放无副作用的纯计算：从 results 载荷 + 兜底项目配置推导
 * 步骤表行 / SOP 卡片 / 隐藏标签集 / 逻辑模式。
 * 不 import Vue、不碰 DOM、不读全局 store —— 一切靠参数传入，vitest 可直测。
 *
 * 语义契约：
 * - tableData / sopSteps 仅在 (d.detections || region_events) 时产出，否则返回
 *   null 表示"本轮不更新，保留旧值"。
 * - 步骤类 SOP 按 sequence/detection/custom 身份建卡（与单工位 watch 同源）；
 *   poll 已有 project_config 时禁止用顶部 currentProject 的 sequence 串项目。
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

/**
 * 从项目配置算出 SOP/步骤表应展示的步骤（单工位 watch 与多工位建卡同源）。
 *
 * 顶层字段优先于 pipeline_config（与 Project 页加载期注入的视图字段对齐）。
 * sequence 空 → 摊 enabled 步骤（与旧 mock / 旧后端缺键同行为）。
 */
export function resolveStepsToShow(projectLike) {
  const proj = projectLike || {};
  const stepsConfig = proj.steps_config || [];
  const logicMode = proj.logic_mode || 'sequential';
  const pipelineConfig = proj.pipeline_config || {};

  const sequenceOrder = proj.sequence_order || pipelineConfig.sequence_order || [];
  const detectionSteps = proj.detection_steps || pipelineConfig.detection_steps || [];
  const customBasedOn = proj.custom_based_on || pipelineConfig.custom_based_on || null;
  const customSequenceOrder = proj.custom_sequence_order || pipelineConfig.custom_sequence_order || [];
  const customDetectionSteps = proj.custom_detection_steps || pipelineConfig.custom_detection_steps || [];

  let stepsToShow = [];

  if (logicMode === 'sequential') {
    if (sequenceOrder.length > 0) {
      stepsToShow = sequenceOrder.map(seqItem => {
        return stepsConfig.find(s => s.id === seqItem.step_id);
      }).filter(Boolean);
    } else {
      stepsToShow = stepsConfig.filter(s => s.enabled);
    }
  } else if (logicMode === 'detection') {
    if (detectionSteps.length > 0) {
      stepsToShow = detectionSteps.map(id => {
        return stepsConfig.find(s => s.id === id);
      }).filter(Boolean);
    } else {
      stepsToShow = stepsConfig.filter(s => s.enabled);
    }
  } else if (logicMode === 'custom') {
    if (customBasedOn === 'sequential') {
      if (customSequenceOrder.length > 0) {
        stepsToShow = customSequenceOrder.map(seqItem => {
          return stepsConfig.find(s => s.id === seqItem.step_id);
        }).filter(Boolean);
      } else {
        stepsToShow = stepsConfig.filter(s => s.enabled);
      }
    } else if (customBasedOn === 'detection') {
      if (customDetectionSteps.length > 0) {
        stepsToShow = customDetectionSteps.map(id => {
          return stepsConfig.find(s => s.id === id);
        }).filter(Boolean);
      } else {
        stepsToShow = stepsConfig.filter(s => s.enabled);
      }
    } else {
      const customConditions = proj.custom_conditions || pipelineConfig.custom_conditions || [];
      const involvedStepIds = new Set();
      customConditions.forEach(cond => {
        (cond.sequence || []).forEach(stepId => involvedStepIds.add(stepId));
      });
      if (involvedStepIds.size > 0) {
        stepsToShow = [...involvedStepIds].map(id => stepsConfig.find(s => s.id === id)).filter(Boolean);
      } else {
        stepsToShow = stepsConfig.filter(s => s.enabled);
      }
    }
  } else if (logicMode === 'region_events') {
    stepsToShow = regionEventRuleSteps(pipelineConfig);
  } else {
    stepsToShow = stepsConfig.filter(s => s.enabled);
  }

  if (logicMode === 'tracking') {
    const expectedList = proj.counting_expected_list
      || pipelineConfig.counting_expected_list
      || [];
    const expectedDict = pipelineConfig.counting_expected_items || {};
    const expectedLabels = new Set();
    expectedList.forEach(item => {
      if (item && item.label) expectedLabels.add(item.label);
    });
    Object.keys(expectedDict).forEach(label => expectedLabels.add(label));

    if (expectedLabels.size > 0) {
      stepsToShow = stepsToShow.filter(s => expectedLabels.has(s.label));
    } else {
      const containerLabel = proj.tracking_container_label
        || pipelineConfig.tracking_container_label
        || '';
      if (containerLabel) {
        stepsToShow = stepsToShow.filter(s => s.label !== containerLabel);
      }
    }
  }

  return stepsToShow.filter(s => !s.backup_for);
}

/**
 * 多工位建卡用的 projectLike。
 *
 * R1：poll 只要带了 project_config，sequence/detection/custom 身份只读 poll
 * （缺键视为空，走 enabled 摊开，禁止用顶部 currentProject 的 sequence 串项目）。
 * fallback 仅用于：poll 缺 steps_config；region_events 载荷无 rules 数组时回退。
 */
export function projectLikeForChannelSteps(pollProjectConfig, fallbackProject) {
  const fallback = fallbackProject || null;
  if (!pollProjectConfig) return fallback || {};

  const pollPipe = pollProjectConfig.pipeline_config || {};
  const pollRules = pollPipe.region_events?.rules;
  const regionEvents = Array.isArray(pollRules)
    ? pollPipe.region_events
    : (fallback?.pipeline_config?.region_events || pollPipe.region_events);

  return {
    logic_mode: pollProjectConfig.logic_mode || fallback?.logic_mode,
    steps_config: pollProjectConfig.steps_config || fallback?.steps_config || [],
    sequence_order: pollProjectConfig.sequence_order || pollPipe.sequence_order || [],
    detection_steps: pollProjectConfig.detection_steps || pollPipe.detection_steps || [],
    custom_based_on: pollProjectConfig.custom_based_on || pollPipe.custom_based_on || null,
    custom_sequence_order: pollProjectConfig.custom_sequence_order || pollPipe.custom_sequence_order || [],
    custom_detection_steps: pollProjectConfig.custom_detection_steps || pollPipe.custom_detection_steps || [],
    custom_conditions: pollProjectConfig.custom_conditions || pollPipe.custom_conditions || [],
    counting_expected_list: pollProjectConfig.counting_expected_list || pollPipe.counting_expected_list || [],
    tracking_container_label: pollProjectConfig.tracking_container_label || pollPipe.tracking_container_label || '',
    pipeline_config: {
      ...pollPipe,
      region_events: regionEvents,
    },
  };
}

/** 工位逻辑模式判定：轮询配置优先，回退兜底项目（阶段3模式面板同源） */
export const resolveLogicMode = (pollProjectConfig, fallbackProject) =>
  pollProjectConfig?.logic_mode || fallbackProject?.logic_mode;

/** 多工位列应换成专属工艺面板的模式（步骤类模式仍走 SOP 卡） */
export const MODE_PANEL_KINDS = Object.freeze(['tracking', 'per_item', 'weighing']);

/** tracking/per_item/weighing → 模式名；步骤类 → null（调用方走原 SOP） */
export const resolveModePanelKind = (pollProjectConfig, fallbackProject) => {
  const mode = resolveLogicMode(pollProjectConfig, fallbackProject);
  return MODE_PANEL_KINDS.includes(mode) ? mode : null;
};

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
  const _stepsConf = resolveStepsToShow(
    projectLikeForChannelSteps(d.project_config, fallback)
  );
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
