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

const RESULT_EVENT_HOLD_MS = 8000;

const resultEventId = event => String(event?.event_id ?? event?.eventId ?? '');

const resultEventKey = event => [
  resultEventId(event),
  event?.seq ?? '',
  event?.timestamp ?? '',
].join('|');

// recent_events 保留 30 秒且不带 cycle UUID；新周期/切项目只能用浏览器观察到的
// 时间边界隔离上一轮。这里只接受检测周期本身写入的 1/2 事件，排除借事件响应面的
// external/periodic source 和“待补做、尚未结算”的 remediation 事件。
const latestFreshResultEvent = (events, nowMs, ignoreBeforeMs = 0) => {
  let latest = null;
  let latestEventMs = -Infinity;
  let latestSeq = -Infinity;
  for (const event of (Array.isArray(events) ? events : [])) {
    if (!['1', '2'].includes(resultEventId(event))) continue;
    if (event?.source || event?.remediation === true) continue;
    const eventMs = Number(event?.timestamp) * 1000;
    const ageMs = nowMs - eventMs;
    if (!Number.isFinite(eventMs) || eventMs <= 0) continue;
    if (ageMs < -3000 || ageMs > RESULT_EVENT_HOLD_MS) continue;
    if (Number.isFinite(ignoreBeforeMs) && ignoreBeforeMs > 0 && eventMs <= ignoreBeforeMs) continue;
    const seq = Number(event?.seq);
    const comparableSeq = Number.isFinite(seq) ? seq : -Infinity;
    if (eventMs > latestEventMs || (eventMs === latestEventMs && comparableSeq > latestSeq)) {
      latest = event;
      latestEventMs = eventMs;
      latestSeq = comparableSeq;
    }
  }
  return latest;
};

const parseListedLabels = (value) => String(value || '')
  .split(/[,，]/)
  .map(item => item.trim().replace(/^['"]|['"]$/g, '').trim())
  .filter(Boolean);

// 只解析后端明确声明“哪个步骤出错”的文案；不再抓 reason 中所有引号，避免把
// “自定义条件匹配: ['A']”里的正常命中条件误当成 NG 步骤。
const resultEventIssueHints = (event) => {
  const missingLabels = [];
  let hasMissingDeclaration = false;
  const addMissing = (item) => {
    const value = typeof item === 'string'
      ? item
      : (item?.label || item?.name || item?.step || '');
    if (value) missingLabels.push(String(value).trim());
  };
  [
    event?.missing_steps,
    event?.missingSteps,
    event?.data?.missing_steps,
    event?.details?.missing_steps,
  ].forEach((value) => {
    if (Array.isArray(value)) {
      hasMissingDeclaration = true;
      value.forEach(addMissing);
    }
  });

  const reason = String(event?.reason || '');
  const missing = reason.match(/缺少(?:步骤)?[：:]\s*\[([^\]]+)\]/);
  if (missing) {
    hasMissingDeclaration = true;
    missingLabels.push(...parseListedLabels(missing[1]));
  }
  const duplicate = reason.match(/重复步骤[：:]\s*\[([^\]]+)\]/);
  const duplicateLabels = duplicate ? parseListedLabels(duplicate[1]) : [];
  const orderIndexMatch = reason.match(/第\s*(\d+)\s*步[^，,。]*顺序错误/);
  const orderPair = reason.match(/期望\[([^\]]+)\].*实际\[([^\]]+)\]/);
  const explicitLabel = reason.match(/(?:步骤回退|违反严格顺序)[：:]\s*\[([^\]]+)\]/)
    || reason.match(/步骤\s*\[([^\]]+)\]\s*超时/);
  return {
    missingLabels,
    hasMissingDeclaration,
    duplicateLabels,
    orderIndex: orderIndexMatch ? Number(orderIndexMatch[1]) - 1 : null,
    orderActual: orderPair ? String(orderPair[2]).trim() : '',
    explicitLabels: explicitLabel ? parseListedLabels(explicitLabel[1]) : [],
  };
};

/**
 * 从单通道 results 载荷构建步骤视图。
 *
 * @param {object} d 后端 /source/detection/results 单通道载荷
 * @param {object|null} fallbackProject 兜底项目（全局 currentProject；载荷缺配置时用）
 * @param {object} chState 通道已累计状态：
 *   { currentCycleSteps, backupCoveredLabels, stepInflightDurations,
 *     prevSteps, prevTableData, resetPreviousResults, cycleInProgress,
 *     resultEventIgnoreBeforeMs, lastHandledResultEventKey, settledVerdict, nowMs }
 * @returns {{ tableData: Array|null, sopSteps: Array|null, hiddenLabels: Set,
 *   handledResultEventKey: string|null }}
 *   tableData/sopSteps 为 null = 本轮不更新；hiddenLabels 每轮都产出。
 */
export function buildChannelStepViews(d, fallbackProject, chState) {
  const fallback = fallbackProject || null;
  const currentCycleSteps = chState.currentCycleSteps || [];
  const backupCoveredLabels = chState.backupCoveredLabels || [];
  const stepInflightDurations = chState.stepInflightDurations || {};
  const prevSteps = chState.prevSteps || [];
  const prevTableData = chState.prevTableData || [];
  const resetPreviousResults = chState.resetPreviousResults === true;

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
  const _projectLike = projectLikeForChannelSteps(d.project_config, fallback);
  const _stepsConf = resolveStepsToShow(_projectLike);
  // detection 仅固定首/末步骤，中间步骤明确无序；只有 sequential 及其
  // custom-based-on-sequential 变体允许按配置位置推断乱序/跳步 NG。
  const _isSequenceLike = _logicMode === 'sequential'
    || (_logicMode === 'custom' && _projectLike.custom_based_on === 'sequential');
  const _visibleSteps = _stepsConf
    .filter(s => s.enabled !== false && !s.is_backup && _trkAllow(s.label));
  const _detectingLabels = new Set((d.detections || []).map(det => det?.label));
  const _stepIsActive = (label) => _isRegionEvents
    ? (stepInflightDurations[label] || 0) > 0
    : _detectingLabels.has(label);

  // 多屏完整工位窗走 multiChannelData，而主屏单工位走 index.vue 内的
  // updateStepsFromBackend。原多通道建卡只写 status，普通步骤 cycleResult 永远
  // 为 null，ChannelDashboard 的最右「结果」列因此一直显示 --。这里把主屏既有的
  // 位置分配、PT 守门、重复/乱序/漏步和备用步骤判定移植到纯函数，保持各工位同口径。
  const _expectedLabels = _visibleSteps.map(step => step.label);
  const _expectedCounter = {};
  _expectedLabels.forEach(label => {
    _expectedCounter[label] = (_expectedCounter[label] || 0) + 1;
  });
  const _actualPosByLabel = {};
  currentCycleSteps.forEach((label, index) => {
    (_actualPosByLabel[label] = _actualPosByLabel[label] || []).push(index);
  });
  const _consumedExpectedSlots = {};
  _expectedLabels.forEach(label => { _consumedExpectedSlots[label] = 0; });
  const _completedByPos = new Array(_expectedLabels.length).fill(false);
  const _assignedActualPos = new Array(_expectedLabels.length).fill(-1);
  let _maxCompletedIdx = -1;
  _expectedLabels.forEach((label, index) => {
    const seen = (_actualPosByLabel[label] || []).length;
    const expectedCount = _expectedCounter[label] || 0;
    const slotsToAllocate = Math.min(seen, expectedCount);
    if (_consumedExpectedSlots[label] < slotsToAllocate) {
      const occurrence = _consumedExpectedSlots[label];
      _completedByPos[index] = true;
      _assignedActualPos[index] = _actualPosByLabel[label][occurrence];
      _consumedExpectedSlots[label] += 1;
      _maxCompletedIdx = index;
    }
  });

  const _outOfOrderIdx = new Set();
  if (_isSequenceLike) {
    let _maxActualSoFar = -1;
    _expectedLabels.forEach((_label, index) => {
      if (!_completedByPos[index]) return;
      const actualPos = _assignedActualPos[index];
      if (actualPos < _maxActualSoFar) _outOfOrderIdx.add(index);
      _maxActualSoFar = Math.max(_maxActualSoFar, actualPos);
    });
  }

  const _cycleDurations = d.cycle_sum_step_durations || {};
  let _maxAuthoritativeCompletedIdx = -1;
  _expectedLabels.forEach((label, index) => {
    if (_completedByPos[index] && Number(_cycleDurations[label] || 0) > 0) {
      _maxAuthoritativeCompletedIdx = index;
    }
  });

  const _feedback = _visibleSteps.map((step, index) => {
    const label = step.label;
    const trackHit = _trackHit(label);
    const coveredByBackup = backupCoveredLabels.includes(label);
    const isActive = _stepIsActive(label);

    if (trackHit) return { status: 'completed', cycleResult: 'ok', resultFinalized: false };

    // end_cycle 会立即清 current_cycle_steps。沿用主屏行为：周期间隙保留上一轮
    // 已盖章结果，下一周期一旦有步骤进入就按新序列整表重算；活动中的新标签优先点亮。
    if (currentCycleSteps.length === 0) {
      if (isActive) return { status: 'active', cycleResult: null, resultFinalized: false };
      if (coveredByBackup) return { status: 'completed', cycleResult: 'ok', resultFinalized: false };
      const previous = resetPreviousResults ? null : prevTableData[index];
      const previousHasFinalResult = previous?.resultFinalized === true
        && (previous.cycleResult === 'ok' || previous.cycleResult === 'ng');
      if (
        previous?.label === label
        && (previous.cycleResult === 'ok' || previous.cycleResult === 'ng')
        && (previous.status === 'completed' || previousHasFinalResult)
      ) {
        return {
          status: previousHasFinalResult
            ? (previous.status || (previous.cycleResult === 'ok' ? 'completed' : 'pending'))
            : 'completed',
          cycleResult: previous.cycleResult,
          resultFinalized: previous.resultFinalized === true,
        };
      }
      return { status: 'pending', cycleResult: null, resultFinalized: false };
    }

    const thisPosCompleted = _completedByPos[index];
    const cycleCount = (_actualPosByLabel[label] || []).length;
    const expectedCount = _expectedCounter[label] || 0;
    const authoritative = Number(_cycleDurations[label] || 0) > 0;
    const hasTimedPt = authoritative || Number(stepInflightDurations[label] || 0) > 0;
    const passedThisStep = _maxCompletedIdx > index && thisPosCompleted;
    const leftFrame = !isActive;
    const isLastStep = index === _expectedLabels.length - 1;
    const positionDone = thisPosCompleted && (
      isLastStep
        ? (leftFrame || authoritative)
        : (hasTimedPt && (leftFrame || passedThisStep))
    );

    let cycleResult = null;
    if (_isRegionEvents) {
      cycleResult = positionDone ? 'ok' : null;
    } else if (cycleCount > expectedCount && positionDone) {
      cycleResult = 'ng';
    } else if (positionDone) {
      cycleResult = _outOfOrderIdx.has(index) ? 'ng' : 'ok';
    } else if (coveredByBackup) {
      cycleResult = 'ok';
    } else if (_isSequenceLike
      && !thisPosCompleted && index < _maxAuthoritativeCompletedIdx) {
      cycleResult = 'ng';
    }

    return {
      status: (thisPosCompleted || coveredByBackup)
        ? 'completed'
        : (isActive ? 'active' : 'pending'),
      cycleResult,
      resultFinalized: false,
    };
  });

  // 与主屏同一兜底：末步已经 OK 时，前面已完成且未判乱序的位置同步补 OK，
  // 避免后端 PT 写入相差一拍造成中间步骤结果短暂缺失。
  const _lastIdx = _feedback.length - 1;
  if (_lastIdx >= 0 && _feedback[_lastIdx].cycleResult === 'ok') {
    _feedback.forEach((item, index) => {
      if (
        index < _lastIdx
        && _completedByPos[index]
        && item.cycleResult == null
        && !_outOfOrderIdx.has(index)
      ) {
        item.cycleResult = 'ok';
      }
    });
  }

  // last_step 结算会在后端同一拍清 current_cycle_steps，浏览器可能看不到
  // “末步离场但尚未清周期”的中间态。8 秒内的结算提示只做展示兜底：
  // - 周期计数器在 active token -> null 时给出的 settledVerdict 优先，避免插件在
  //   end_cycle 内翻转最终 OK/NG 后 events_log 仍保留原 event_id；
  // - external / periodic / remediation 事件不参与；活动周期、项目/周期边界不参与；
  // - NG 只映射明确且位置可判定的步骤，重复 label 无位置证据时不猜。
  // 事件只消费一次，后续靠 resultFinalized 的前态保留，避免 150ms 重复跑文本解析。
  const _hasLiveProgress = currentCycleSteps.length > 0
    || _detectingLabels.size > 0
    || Object.values(stepInflightDurations).some(value => Number(value || 0) > 0);
  const _supportsResultEvent = ['sequential', 'detection'].includes(_logicMode);
  const _cycleInProgress = chState.cycleInProgress === true;
  const _settledVerdict = _supportsResultEvent && ['ok', 'ng'].includes(chState.settledVerdict)
    ? chState.settledVerdict
    : null;
  const _resultEvent = _supportsResultEvent && !resetPreviousResults
    && !_cycleInProgress && !d.pending_remediation
    ? latestFreshResultEvent(
      d.recent_events,
      Number(chState.nowMs),
      Number(chState.resultEventIgnoreBeforeMs || 0),
    )
    : null;
  const _resultEventKey = _resultEvent ? resultEventKey(_resultEvent) : null;
  const _newResultEvent = _resultEventKey
    && _resultEventKey !== chState.lastHandledResultEventKey
    ? _resultEvent
    : null;
  const _eventVerdict = _newResultEvent
    ? (resultEventId(_newResultEvent) === '1' ? 'ok' : 'ng')
    : null;
  const _resultVerdict = _settledVerdict
    || (!_hasLiveProgress ? _eventVerdict : null);
  let handledResultEventKey = null;

  if (_resultVerdict === 'ok') {
    _feedback.forEach((item) => {
      item.status = 'completed';
      item.cycleResult = 'ok';
      item.resultFinalized = true;
    });
  } else if (_resultVerdict === 'ng') {
    const targets = new Set();
    _feedback.forEach((item, index) => {
      if (item.cycleResult === 'ng') targets.add(index);
    });

    const hints = resultEventIssueHints(_newResultEvent || _resultEvent || {});
    const declaredMissingLabels = new Set(hints.missingLabels);
    if (Number.isInteger(hints.orderIndex)
      && hints.orderIndex >= 0 && hints.orderIndex < _visibleSteps.length) {
      targets.add(hints.orderIndex);
    }

    const matchingIndexes = (candidate) => {
      const wanted = String(candidate || '').trim();
      if (!wanted) return [];
      const matches = [];
      _visibleSteps.forEach((step, index) => {
        const aliases = [step.label, step.displayLabel, step.name, step.step]
          .filter(Boolean)
          .map(value => String(value).trim());
        if (aliases.includes(wanted)) matches.push(index);
      });
      return matches;
    };
    const addUnambiguousTarget = (candidate) => {
      const matches = matchingIndexes(candidate);
      if (matches.length === 1) {
        targets.add(matches[0]);
        return;
      }
      // 重复 label 本身没有 occurrence；只有前一拍恰好留下一个未完成/NG 位置时
      // 才能安全定位，不能把 A-B-A 中两张 A 一起染红。
      const evidenced = matches.filter(index => (
        _feedback[index].cycleResult === 'ng'
        || _feedback[index].cycleResult == null
      ));
      if (evidenced.length === 1) targets.add(evidenced[0]);
    };
    hints.missingLabels.forEach(addUnambiguousTarget);
    hints.duplicateLabels.forEach(addUnambiguousTarget);
    hints.explicitLabels.forEach(addUnambiguousTarget);
    if (targets.size === 0 && hints.orderActual) addUnambiguousTarget(hints.orderActual);

    // 只有已经找到 NG 落点，才把其它唯一 label 的正 PT 或既有 OK 盖章；如果是
    // 周期总超时/组合判型等无法定位的 NG，宁可保留前态，也不伪造“全步 OK”。
    _visibleSteps.forEach((step, index) => {
      if (targets.has(index)) {
        if (Number(_cycleDurations[step.label] || 0) > 0) {
          _feedback[index].status = 'completed';
        }
        _feedback[index].cycleResult = 'ng';
        _feedback[index].resultFinalized = true;
      } else if (_feedback[index].cycleResult === 'ok') {
        _feedback[index].resultFinalized = true;
      } else if (
        targets.size > 0
        && (
          (_expectedCounter[step.label] || 0) === 1
          || (hints.hasMissingDeclaration && !declaredMissingLabels.has(step.label))
        )
        && Number(_cycleDurations[step.label] || 0) > 0
      ) {
        _feedback[index].status = 'completed';
        _feedback[index].cycleResult = 'ok';
        _feedback[index].resultFinalized = true;
      }
    });
  }

  if (_newResultEvent && _resultVerdict) {
    handledResultEventKey = _resultEventKey;
  }

  let tableData = null;
  if (d.detections || _isRegionEvents) {
    // v3.31.x 语义收窄: hide_in_view 只隐藏画面检测框, SOP/步骤详情照常显示
    tableData = _visibleSteps.map((step, index) => ({
      step: step.displayLabel || step.label,
      label: step.label,
      status: _feedback[index].status,
      cycleResult: _feedback[index].cycleResult,
      resultFinalized: _feedback[index].resultFinalized,
    }));
  }

  let sopSteps = null;
  if (d.detections || _isRegionEvents) {
    const screenshots = d.step_screenshots || {};
    // v3.10.x: SOP 卡片"图永不空"策略 — 后端有新图就替换, 没有就从上一轮按 label 继承
    const prevSopByLabel = Object.fromEntries(
      prevSteps.map(s => [s.label, s.screenshot])
    );
    sopSteps = _visibleSteps
      .map((s, index) => {
        const rawB64 = screenshots[s.label];
        return {
          name: s.displayLabel || s.label,
          label: s.label,
          status: _feedback[index].status,
          cycleResult: _feedback[index].cycleResult,
          resultFinalized: _feedback[index].resultFinalized,
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

  return { tableData, sopSteps, hiddenLabels, handledResultEventKey };
}
