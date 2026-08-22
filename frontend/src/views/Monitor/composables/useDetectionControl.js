/**
 * useDetectionControl — 多通道检测控制域（巨石重构阶段1⑦，v3.54.1 逐行等价移植）
 *
 * 职责：按工位 开始(项目同步/主副模型路径解析/多模型payload/会话ID校验)、
 * 停止/待机(v3.3 码-码闭环收尾确认弹窗)、全局检测态聚合回写 store。
 *
 * 单工位 startDetection/stopDetectionHandler 仍在 index.vue（与会话命名/
 * 视频源进度等 UI 深耦合，后续阶段处理）；本域覆盖多工位按钮通路。
 */
import { ElMessage, ElMessageBox } from 'element-plus';
import {
  startDetection as apiStartDetection, pauseDetection, standbyDetection,
  getScanPairActive, settleScanPairForStop,
} from '@/api/detection';
import { getModelDetail, resolveModelPath as apiResolveModelPath } from '@/api/model';
import { dbg, dbgErr } from '@/utils/debug';

export function useDetectionControl(ctx) {
  const {
    multiChannelData, currentProject, systemStore, projectStore,
    sessionName, sessionNameError, validateSessionName, syncProjectConfig,
  } = ctx;

  // Step 8 (feat/multi-model-roi-link): 解析单个 model id 到磁盘路径 (含格式回退)
  const _resolveModelPath = async (modelId, modelFormat = 'pytorch_fp32') => {
    try {
      const resolveRes = await apiResolveModelPath(modelId, modelFormat);
      return {
        path: resolveRes.data.path,
        fallback: resolveRes.data.fallback,
        reason: resolveRes.data.reason,
      };
    } catch {
      const modelRes = await getModelDetail(modelId);
      return { path: modelRes.data.file_path, fallback: false };
    }
  };

  const startDetectionForChannel = async (ch) => {
    const chProj = multiChannelData.value[ch]?.project || currentProject.value;
    dbg('monitor.control', `点击「开始」(工位${ch + 1})`, `project=${chProj?.name || '无'}`);
    if (!chProj) {
      dbg('monitor.control', `工位${ch + 1} 开始被拒: 未绑定项目`);
      ElMessage.warning(`工位 ${ch + 1} 未绑定项目`);
      return;
    }
    try {
      await syncProjectConfig(ch, chProj);
      const modelId = chProj.default_model_id;
      if (!modelId) { ElMessage.warning(`工位 ${ch + 1} 未配置模型`); return; }
      const modelFormat = chProj.model_format || 'pytorch_fp32';

      const mainResolved = await _resolveModelPath(modelId, modelFormat);
      if (mainResolved.fallback && modelFormat !== 'pytorch_fp32') {
        ElMessage.warning(mainResolved.reason || '转换模型不可用，已回退到原始模型');
      }
      const mainPath = mainResolved.path;

      // Step 8: 解析项目的副模型配置 (来自 pipeline_config.models[], name !== 'main')
      const pipelineModels = chProj?.pipeline_config?.models || [];
      const extraSlots = pipelineModels.filter(m => m && m.name && m.name !== 'main' && m.model_id);

      // v3.7.0: 会话 ID 校验提前到多/单模型 if 分支之前, 失败直接退出
      if (!validateSessionName(sessionName.value)) {
        ElMessage.warning('会话 ID 不合法：' + sessionNameError.value);
        return;
      }
      const _sessionId = sessionName.value || null;
      if (extraSlots.length > 0) {
        // 多模型 payload
        const mainPipelineSpec = pipelineModels.find(m => m && m.name === 'main') || {};
        const specs = [{
          name: 'main',
          model_path: mainPath,
          conf: 0.25,
          iou: 0.45,
          display_color: mainPipelineSpec.display_color || '#10b981',
          priority: 100,
        }];
        const failedSlots = [];
        for (const e of extraSlots) {
          try {
            // v3.7.x: 副模型也按 model_format resolve (与单通道分支对齐).
            // 无 model_format 字段的老项目兜底 pytorch_fp32, 行为完全等价旧版本.
            const r = await _resolveModelPath(e.model_id, e.model_format || 'pytorch_fp32');
            specs.push({
              name: e.name,
              model_path: r.path,
              conf: typeof e.conf === 'number' ? e.conf : 0.25,
              iou: typeof e.iou === 'number' ? e.iou : 0.45,
              roi: Array.isArray(e.roi) && e.roi.length >= 3 ? e.roi : null,
              schedule: e.schedule || { type: 'every_frame', n: 1, events: [] },
              class_filter: Array.isArray(e.class_filter) && e.class_filter.length
                ? e.class_filter : null,
              priority: typeof e.priority === 'number' ? e.priority : 50,
              display_color: e.display_color || '#f59e0b',
              use_half: !!e.use_half,
            });
          } catch (err) {
            failedSlots.push(e.name);
            console.warn(`[Monitor] 副模型 ${e.name} 路径解析失败:`, err);
          }
        }
        if (failedSlots.length) {
          ElMessage.warning(`副模型路径解析失败已跳过: ${failedSlots.join(', ')}`);
        }
        await apiStartDetection({ models: specs }, undefined, undefined, ch, _sessionId);
        dbg('monitor.control', `工位${ch + 1} 启动成功 (多模型 ${specs.length})`);
        ElMessage.success(
          `工位 ${ch + 1} 检测已启动 (主 + ${specs.length - 1} 个副模型)`
        );
      } else {
        await apiStartDetection(mainPath, 0.25, 0.45, ch, _sessionId);
        dbg('monitor.control', `工位${ch + 1} 启动成功`);
        ElMessage.success(`工位 ${ch + 1} 检测已启动`);
      }
    } catch (e) {
      dbgErr('monitor.control', `工位${ch + 1} 启动`, e);
      ElMessage.error(`工位 ${ch + 1} 启动失败: ${e.message}`);
    }
  };

  const updateGlobalDetectingState = () => {
    const anyDetecting = Object.values(multiChannelData.value).some(d => d?.isDetecting);
    systemStore.setDetecting(anyDetecting);
    projectStore.setRunningStatus(anyDetecting);
  };

  // v3.3.0 码-码闭环结算: 停止/待机前如果工位有"未关闭的扫码窗口", 弹窗让用户选
  //   - 结算 (默认): 把这枚工件按"曾齐过"判定 OK/NG 入账
  //   - 丢弃: 直接丢, 产能不计
  //   - 取消: 中止本次停止操作
  // 返回 true=继续 stop, false=取消 stop.
  const confirmScanPairBeforeStop = async (ch) => {
    try {
      const res = await getScanPairActive(ch);
      const data = res.data || {};
      if (!data.is_scan_pair_mode || !data.active_serial) return true;
      let action = 'settle';
      try {
        await ElMessageBox({
          title: '码-码闭环: 最后一码未关闭',
          message: `工位 ${ch + 1} 当前周期开始码 "${data.active_serial}" 尚未扫到下一码。\n` +
                   `选择如何收尾本枚工件:`,
          showCancelButton: true,
          confirmButtonText: '结算 (默认)',
          cancelButtonText: '丢弃',
          distinguishCancelAndClose: true,
          type: 'warning',
        });
        action = 'settle';
      } catch (e) {
        if (e === 'close') return false;
        action = 'discard';
      }
      try {
        await settleScanPairForStop(ch, action === 'discard');
        ElMessage.success(action === 'discard'
          ? `工位 ${ch + 1} 最后一码已丢弃`
          : `工位 ${ch + 1} 最后一码已结算`);
      } catch (e) {
        ElMessage.error(`工位 ${ch + 1} 收尾失败: ${e?.response?.data?.detail || e?.message || e}`);
      }
      return true;
    } catch (e) {
      return true;
    }
  };

  const stopDetectionForChannel = async (ch) => {
    dbg('monitor.control', `点击「停止」(工位${ch + 1})`);
    if (!(await confirmScanPairBeforeStop(ch))) { dbg('monitor.control', `工位${ch + 1} 停止被取消: scan_pair 确认未通过`); return; }
    try {
      await pauseDetection(ch);
      if (multiChannelData.value[ch]) multiChannelData.value[ch].isDetecting = false;
      updateGlobalDetectingState();
      dbg('monitor.control', `工位${ch + 1} 停止成功`);
      ElMessage.info(`工位 ${ch + 1} 已停止`);
    } catch (e) {
      dbgErr('monitor.control', `工位${ch + 1} 停止`, e);
      ElMessage.error(`工位 ${ch + 1} 停止失败`);
    }
  };

  const standbyForChannel = async (ch) => {
    dbg('monitor.control', `点击「待机」(工位${ch + 1})`);
    if (!(await confirmScanPairBeforeStop(ch))) { dbg('monitor.control', `工位${ch + 1} 待机被取消: scan_pair 确认未通过`); return; }
    try {
      await standbyDetection(ch);
      if (multiChannelData.value[ch]) multiChannelData.value[ch].isDetecting = false;
      updateGlobalDetectingState();
      dbg('monitor.control', `工位${ch + 1} 待机成功`);
      ElMessage.info(`工位 ${ch + 1} 已待机`);
    } catch (e) {
      dbgErr('monitor.control', `工位${ch + 1} 待机`, e);
      ElMessage.error(`工位 ${ch + 1} 待机失败`);
    }
  };
  return {
    _resolveModelPath, startDetectionForChannel, stopDetectionForChannel,
    standbyForChannel, updateGlobalDetectingState, confirmScanPairBeforeStop,
  };
}
