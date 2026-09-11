/**
 * useChannelResults — 多通道结果处理 + 数据轮询域（巨石重构阶段1⑥，v3.54.1 逐行等价移植）
 *
 * 职责：150ms 全工位 results 轮询（kiosk 只轮绑定工位）、processChannelResult
 * 全量载荷落 multiChannelData（计数/PT/步骤视图/工件 OK-NG hold/扫码事件/
 * 事件 Toast/NG TOP3/叠加层绘制调度/源恢复重连）。
 *
 * 视图构建纯逻辑在 monitorModes.buildChannelStepViews；取流在 useMultiStreams；
 * Toast 在 useToastVoice；画框在 useOverlayDrawing —— 本域是它们的消费编排层。
 */
import { nextTick } from 'vue';
import api from '@/api/index';
import { getDetectionResults } from '@/api/detection';
import { dbgErr } from '@/utils/debug';
import { buildChannelStepViews } from '../monitorModes';

export function useChannelResults(ctx) {
  const {
    multiChannelData, channelCount, kioskMode, kioskChannel, currentProject,
    multiDraggingProgress, channelModelStats, workpieceOverridesByCh,
    effectiveLayoutBodyOverride,
    multiLastSeenSeq, multiCanvasRefs, workpieceOverrideTimers, workpieceHideTimers,
    systemStore, scannerDisableStore,
    showMultiToast, handleScanToast, handleRebindPrompt, drawMultiDetections,
    isMultiStreamRunning, reconnectChannelStream, updateGlobalDetectingState,
  } = ctx;

  const WORKPIECE_RESULT_HOLD_MS = 3500;  // 结果 OK/NG 标签保留时长
  let multiPollingTimer = null;
  let multiPollingInProgress = false;

  const processChannelResult = (ch, d) => {
    const chData = multiChannelData.value[ch] || {};
    const nowMs = Date.now();
    // 与单工位周期边界口径一致；优先用创建即有的 current_cycle_uuid，兼容
    // current_cycle_id。结算后的 token -> null 保留上轮反馈；null/旧 token ->
    // 新 token 或切换项目时，首步尚未入 cycle 也要
    // 清掉旧 OK/NG，避免扩展屏把上一件结果挂到下一件。
    const hasCycleId = Object.prototype.hasOwnProperty.call(d, 'current_cycle_id');
    const hasCycleUuid = Object.prototype.hasOwnProperty.call(d, 'current_cycle_uuid');
    const previousCycleId = chData.currentCycleId;
    const incomingCycleId = hasCycleId
      ? (d.current_cycle_id ?? null)
      : (previousCycleId ?? null);
    const previousCycleToken = chData.currentCycleToken;
    const incomingCycleToken = (hasCycleId || hasCycleUuid)
      ? (d.current_cycle_uuid ?? d.current_cycle_id ?? null)
      : (previousCycleToken ?? null);
    const newCycleStarted = (hasCycleId || hasCycleUuid)
      && incomingCycleToken !== null
      && previousCycleToken !== undefined
      && incomingCycleToken !== previousCycleToken;
    const firstObservedCycleIsActive = previousCycleToken === undefined
      && incomingCycleToken !== null;
    const cycleEnded = previousCycleToken !== undefined
      && previousCycleToken !== null
      && incomingCycleToken === null;
    const projectConfig = d.project_config || {};
    const hasProjectId = Object.prototype.hasOwnProperty.call(projectConfig, 'project_id');
    const previousProjectId = chData.projectId;
    const incomingProjectId = hasProjectId
      ? (projectConfig.project_id ?? null)
      : (previousProjectId ?? null);
    const projectChanged = hasProjectId
      && previousProjectId !== undefined
      && incomingProjectId !== previousProjectId;
    if (newCycleStarted || projectChanged || firstObservedCycleIsActive) {
      // recent_events 最多保留 30 秒且没有 cycle UUID。记录浏览器观察到的新周期/
      // 项目边界，后续即使空轮询仍带上一轮 1/2 事件，也不得重新盖回旧 OK/NG。
      chData.resultEventIgnoreBeforeMs = nowMs;
      // 上一周期由计数器确认的最终 verdict 只在周期间隙保留；新周期/新项目
      // 必须同步失效，不能继续压住新一轮的逐步骤状态。
      chData.counterSettledVerdict = null;
    }
    chData.currentCycleId = incomingCycleId;
    chData.currentCycleToken = incomingCycleToken;
    chData.projectId = incomingProjectId;
    // v3.51.5: 工位源"从停到跑"的瞬间强制重连该路视频流 — 开机恢复要 30s+ 的现场
    // (捷昌 B 站), 页面挂载时源还没起来, MJPEG 零帧断流两次就永久降级快照、或旧连接
    // 对着停掉的源干等; 源恢复后没人负责把流拉回来, 表现为"画面加载完还得切页
    // 再切回来才有画面"。数据轮询本来就 150ms 一拍知道 is_running, 在这里补上闭环。
    const wasRunning = chData.isRunning === true;
    if (!wasRunning && d.is_running && isMultiStreamRunning()) {
      reconnectChannelStream(ch);
      console.log(`[MultiStream] ch${ch} 源已恢复运行, 重连视频流`);
    }
    chData.isRunning = d.is_running;
    chData.isDetecting = d.is_detecting;
    chData.sourceType = d.source_type || '';
    chData.fps = d.fps || 0;
    chData.latency = d.latency || 0;
    // Step 8: per-channel 多模型快照
    if (Array.isArray(d.models)) {
      channelModelStats.value[ch] = d.models;
    }
    if (d.project_config?.project_name) {
      chData.projectName = d.project_config.project_name;
    }
    const ctrs = d.counters || {};
    // v3.1.3: 多工位下也按 channel 维护 OK/NG hold (跟单工位一致),
    // 总产量从 N → N+1 时, 把当前 mes.workpiece 用 OK/NG 标签覆盖 3.5s,
    // 然后清空让 UI 显示 "等待扫码..."
    const prevTotal = multiChannelData.value[ch]?.total ?? -1;
    const prevNg = multiChannelData.value[ch]?.ng ?? -1;
    const newTotal = ctrs['总产量'] ?? 0;
    const newNg = ctrs['不良总数'] ?? 0;
    // events_log 写在 end_cycle 外层，插件/工位组可能在 end_cycle 内翻转最终结果。
    // active token -> null 且总产量增长时，以最终计数器增量为本拍结算 verdict。
    const settledVerdict = cycleEnded && prevTotal >= 0 && newTotal > prevTotal
      ? (prevNg >= 0 && newNg > prevNg ? 'ng' : 'ok')
      : null;
    if (settledVerdict) chData.counterSettledVerdict = settledVerdict;
    // events_log 可能比 counters 早/晚一拍，也可能在插件最终翻转前写入。
    // 一旦 active token -> null 的计数器增量确认最终结果，就在整个周期间隙
    // 持续以它为准；否则迟到的旧 event_id 会在下一拍把最终 OK/NG 反转。
    const effectiveSettledVerdict = incomingCycleToken === null
      && ['ok', 'ng'].includes(chData.counterSettledVerdict)
      ? chData.counterSettledVerdict
      : null;
    if (channelCount.value > 1 && prevTotal >= 0 && newTotal > prevTotal) {
      const cycleIsNg = prevNg >= 0 && newNg > prevNg;
      const realWp = d.mes?.workpiece;
      if (realWp) {
        if (workpieceOverrideTimers[ch]) { clearTimeout(workpieceOverrideTimers[ch]); workpieceOverrideTimers[ch] = null; }
        if (workpieceHideTimers[ch]) { clearTimeout(workpieceHideTimers[ch]); workpieceHideTimers[ch] = null; }
        workpieceOverridesByCh.value = {
          ...workpieceOverridesByCh.value,
          [ch]: { ...realWp, status: cycleIsNg ? 'ng' : 'ok' },
        };
        workpieceHideTimers[ch] = setTimeout(() => {
          workpieceOverridesByCh.value = { ...workpieceOverridesByCh.value, [ch]: null };
          workpieceHideTimers[ch] = null;
        }, WORKPIECE_RESULT_HOLD_MS);
      }
    }
    chData.total = newTotal;
    chData.ok = ctrs['合格总数'] ?? 0;
    chData.ng = newNg;
    chData.counters = ctrs;
    chData.avgCycleTime = d.average_cycle_time || 0;
    chData.avgCycleTimeWithNg = d.average_cycle_time_with_ng || 0;
    chData.lastCycleTime = d.last_cycle_time || 0;
    chData.lastCycleTimeWithNg = d.last_cycle_time_with_ng || 0;
    chData.currentCycleTime = d.current_cycle_time || 0;
    chData.stepIntervals = d.step_intervals || {};
    chData.lastStepDurations = d.last_step_durations || {};
    chData.stepDurations = d.step_durations || {};
    chData.avgStepDurations = d.avg_step_durations || {};
    // v3.5.x: PT 合并档（多工位场景预存数据，便于未来在多工位 UI 也使用）
    chData.cycleSumStepDurations = d.cycle_sum_step_durations || {};
    chData.lastCycleSumStepDurations = d.last_cycle_sum_step_durations || {};
    chData.avgCycleSumStepDurations = d.avg_cycle_sum_step_durations || {};
    chData.stepCycleSegments = d.step_cycle_segments || {};
    // v3.9.x D 方案: 累计可见时长 (visible PT 数据源)
    chData.stepVisibleSeconds = d.step_visible_seconds || {};
    chData.detections = d.detections || [];
    chData._pollProjectConfig = d.project_config || null;
    chData.perItemState = d.per_item_state || null;   // v3.28: 多工位画框贴螺丝编号用
    chData.customMixState = d.custom_mix_state || null;  // v3.19: 混合模式物品校验（阶段3 落通道态）
    chData.placementGuide = d.placement_guide || null;  // v3.32: 就位引导框运行态(已就位/未就位)
    chData.labelSplitRounds = d.label_split_rounds || null;  // v3.32: 多轮次拆分当前轮次
    chData.comboVerdict = d.combo_verdict || null;  // v3.48: 判型表运行态(positional 锁定ROI+实时计数)
    chData.currentCycleSteps = d.current_cycle_steps || [];
    chData.stepInflightDurations = d.step_inflight_durations || {};
    chData.backupCoveredLabels = d.backup_covered_labels || [];
    chData.stepCounts = d.step_counts || {};
    chData.recentEvents = d.recent_events || [];
    // v3.9.x 事件人工确认阻塞态 (后端透出, 前端 modal 直接读)
    chData.pendingAck = d.pending_ack || { active: false };
    // v3.23 缺步骤延迟落账挂起明细 (None=无挂起; 有值时 ack 窗展示缺项 + "补步骤"按钮)
    chData.pendingRemediation = d.pending_remediation || null;
    if (d.tracking) chData.tracking = d.tracking;

    // MES 实时数据 — 每个工位各自显示，不限 selectedChannel
    // v3.1.3: 新扫码来了 (serial_no 从 X → Y), 清掉残留的 override 恢复默认显示
    const prevSn = multiChannelData.value[ch]?.mes?.workpiece?.serial_no;
    const newSn = d.mes?.workpiece?.serial_no;
    if (newSn && newSn !== prevSn && ch in workpieceOverridesByCh.value) {
      if (workpieceOverrideTimers[ch]) { clearTimeout(workpieceOverrideTimers[ch]); workpieceOverrideTimers[ch] = null; }
      if (workpieceHideTimers[ch]) { clearTimeout(workpieceHideTimers[ch]); workpieceHideTimers[ch] = null; }
      const next = { ...workpieceOverridesByCh.value };
      delete next[ch];
      workpieceOverridesByCh.value = next;
    }

    chData.mes = d.mes || null;
    // v3.56: 周期多码采集实况 (项目未启用时后端不带该段 = null 零差异)
    chData.scanCollect = d.scan_collect || null;
    if (d.mes) {
      if (d.mes.scan_event) {
        if (!kioskMode.value) handleScanToast(d.mes.scan_event, ch);
      }
      if (d.mes.rebind_prompt) {
        if (!kioskMode.value) handleRebindPrompt(d.mes.rebind_prompt, ch);
      }
      // v3.4.2 hotfix: 后端 reload / 别终端切换"扫码禁用"时, 把状态同步进 store,
      // 让 isScanDisabledFor / 守门 / 按钮文字 / 信息条都跟上.
      if (typeof d.mes.scan_disabled === 'boolean') {
        scannerDisableStore.applyServerHint(ch - 1, d.mes.scan_disabled);
      }
    }

    const total = chData.total || 0;
    const ok = chData.ok || 0;
    chData.yieldRate = total > 0 ? Math.round((ok / total) * 100) : 0;

    const chTotalCycles = ctrs['总产量'] || ctrs['total'] || 0;
    const backendNgMap = d.ng_step_cycle_counts || {};
    // 后端空 map 时保留现有 TOP3 (待机/重开 sync 配置不再清累计); 清零后后端 reset_stats 会归零
    if (Object.keys(backendNgMap).length > 0) {
      const ranking = Object.entries(backendNgMap)
        .filter(([, c]) => c > 0)
        .map(([step, ngCount]) => ({ step, count: ngCount, rate: chTotalCycles > 0 ? (ngCount / chTotalCycles * 100) : 0 }))
        .sort((a, b) => b.rate - a.rate);
      chData.ngStepRanking = ranking.slice(0, 3);
    } else if (chTotalCycles === 0 && (ctrs['不良总数'] || 0) === 0) {
      chData.ngStepRanking = [];
    }

    const allC = [];
    for (const [name, value] of Object.entries(ctrs)) {
      if (!name.startsWith('_')) allC.push({ name, value });
    }
    chData.allCounters = allC;

    // 步骤表 / SOP 卡 / 隐藏标签集：纯函数构建（逻辑在 monitorModes.js，vitest 直测）
    // tableData/sopSteps 为 null = 本轮无检测载荷且非 region_events，保留旧值不动
    const stepViews = buildChannelStepViews(d, currentProject.value, {
      currentCycleSteps: chData.currentCycleSteps,
      backupCoveredLabels: chData.backupCoveredLabels,
      stepInflightDurations: chData.stepInflightDurations,
      prevSteps: chData.steps,
      prevTableData: chData.tableData,
      resetPreviousResults: newCycleStarted || projectChanged,
      cycleInProgress: incomingCycleToken !== null,
      resultEventIgnoreBeforeMs: chData.resultEventIgnoreBeforeMs || 0,
      lastHandledResultEventKey: chData.lastHandledResultEventKey || null,
      settledVerdict: effectiveSettledVerdict,
      nowMs,
    });
    if (stepViews.tableData !== null) chData.tableData = stepViews.tableData;
    if (stepViews.sopSteps !== null) chData.steps = stepViews.sopSteps;
    if (stepViews.handledResultEventKey) {
      chData.lastHandledResultEventKey = stepViews.handledResultEventKey;
    }
    chData._hiddenLabels = stepViews.hiddenLabels;

    const events = d.recent_events || [];
    if (events.length > 0) {
      if (!multiLastSeenSeq[ch]) multiLastSeenSeq[ch] = 0;
      const newEvents = events
        .filter(e => e.seq > multiLastSeenSeq[ch] && e.show_notification)
        .slice(-3);
      newEvents.forEach(event => {
        multiLastSeenSeq[ch] = Math.max(multiLastSeenSeq[ch], event.seq);
        const toastId = event.toast_id || (event.event_id === 1 ? 'ok' : event.event_id === 2 ? 'ng' : 'ok');
        showMultiToast(ch, toastId, event.event_name, event.reason, event.event_id);

        const warnCfg = systemStore.detection.toasts?.warn_no_barcode;
        // v3.5.2: 直接读后端权威判定, 不再做客户端守门 (避免索引错位/缓存竞态).
        // 老后端缺该字段时按"未绑码 + 没有显式静默"逻辑保持原行为兼容.
        const _shouldWarn = event.should_warn_no_barcode !== undefined
          ? !!event.should_warn_no_barcode
          : !event.had_workpiece;
        if (warnCfg?.enabled && _shouldWarn) {
          setTimeout(() => {
            showMultiToast(ch, 'warn_no_barcode', warnCfg.text || '⚠ 未绑码', warnCfg.subText || '本次结算未绑定工件条码');
          }, 300);
        }
      });
    }

    // D4①: 原地更新降 GC。chData 已是 multiChannelData.value[ch] 的响应式引用,
    // 上面所有 chData.xxx= 都已被 Vue3 深响应追踪; 不再每 150ms×N 工位整对象 spread
    // 重建(那样每 tick 都丢一个旧对象 + 建一个新对象, 长跑 GC 抖动)。首次创建才赋值。
    if (!multiChannelData.value[ch]) {
      multiChannelData.value[ch] = chData;
    }

    const pollCfg = d.project_config || null;
    const dets = d.detections || [];
    const hidden = chData._hiddenLabels;
    // v3.32: 配了拆分区域/就位引导框时, 空检测帧也要走 draw 保住叠加层 (否则区域一闪一闪)
    const _pcOverlay = pollCfg?.pipeline_config || {};
    const hasSplitOverlay = (Array.isArray(_pcOverlay.label_splits) && _pcOverlay.label_splits.length > 0)
      || !!(_pcOverlay.placement_guide && _pcOverlay.placement_guide.enabled);

    // v3.47: 翻页/放大后隐藏工位的旧 canvas 已从 DOM 摘除但字典里还挂着
    // (register 回调只写不清), isConnected 守门避免往脱离的画布上白画。
    const canvas = multiCanvasRefs[ch];
    if (canvas && canvas.isConnected) {
      if (dets.length || hasSplitOverlay) {
        drawMultiDetections(ch, canvas, dets, hidden, pollCfg);
      } else {
        const ctx = canvas.getContext('2d');
        ctx.clearRect(0, 0, canvas.width, canvas.height);
      }
    }

    // layout.body 插件: native overlay canvas 不在 DOM，每轮 polling 主动画到插件 canvas
    // (不能只靠插件 watch detections — 换页/重进 Monitor 时 prop 时序会丢帧)
    if (effectiveLayoutBodyOverride.value) {
      const paintPluginOverlay = () => {
        const overlays = document.querySelectorAll('canvas.fjjl-det-overlay');
        const pluginCanvas = overlays[ch];
        if (!pluginCanvas?.parentElement) return;
        if (pluginCanvas.parentElement.offsetWidth < 2) return;
        if (dets.length || hasSplitOverlay) {
          drawMultiDetections(ch, pluginCanvas, dets, hidden, pollCfg);
        } else {
          const ctx = pluginCanvas.getContext('2d');
          ctx.clearRect(0, 0, pluginCanvas.width, pluginCanvas.height);
        }
      };
      paintPluginOverlay();
      nextTick(() => {
        paintPluginOverlay();
        requestAnimationFrame(paintPluginOverlay);
      });
    }
  };

  const startMultiPolling = () => {
    stopMultiPolling();
    multiPollingInProgress = false;
    multiPollingTimer = setInterval(async () => {
      if (multiPollingInProgress) return;
      multiPollingInProgress = true;
      try {
        const promises = [];
        const pollChannels = kioskMode.value
          ? [kioskChannel.value]
          : Array.from({ length: channelCount.value }, (_, ch) => ch);
        for (const ch of pollChannels) {
          promises.push(
            getDetectionResults(ch)
              .then(res => processChannelResult(ch, res.data))
              .catch((e) => { dbgErr('monitor.poll', `工位${ch + 1} 结果轮询`, e); })
          );
          if (multiChannelData.value[ch]?.sourceType === 'video' && !multiDraggingProgress.value[ch]) {
            promises.push(
              api.get('/source/video/info', { params: { channel: ch } })
                .then((videoRes) => {
                  if (videoRes.data.status !== 'success') return;
                  const chData = multiChannelData.value[ch];
                  if (!chData) return;
                  chData.videoInfo = {
                    progress: videoRes.data.progress || 0,
                    currentTime: videoRes.data.current_time || 0,
                    duration: videoRes.data.duration || 0,
                    speed: videoRes.data.speed || 1,
                    ended: videoRes.data.ended || false,
                  };
                })
                .catch(() => {})
            );
          }
        }
        await Promise.all(promises);
        updateGlobalDetectingState();
        updateMultiCharts();
      } finally {
        multiPollingInProgress = false;
      }
    }, 150);
  };

  const stopMultiPolling = () => {
    if (multiPollingTimer) { clearInterval(multiPollingTimer); multiPollingTimer = null; }
  };

  const updateMultiCharts = () => {
    // Data-driven rendering via template — no separate ECharts needed for multi-view
  };
  // resetMultiRuntimeState 复位轮询门闩用 (原内联 multiPollingInProgress = false)
  const resetMultiPollingGate = () => { multiPollingInProgress = false; };

  return { processChannelResult, startMultiPolling, stopMultiPolling, resetMultiPollingGate };
}
