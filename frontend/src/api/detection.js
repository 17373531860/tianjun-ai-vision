import api from './index';

/**
 * Step 8 (feat/multi-model-roi-link): startDetection 双签名.
 *
 * 老调用 (向后 100% 兼容):
 *   startDetection('/path/main.pt', 0.25, 0.45, 0)
 *
 * 新调用 (多模型, modelPath 传 null 或对象作为第一参数):
 *   startDetection({ models: [
 *     { name: 'main', model_path: '/p/main.pt', conf: 0.3, iou: 0.5,
 *       display_color: '#10b981' },
 *     { name: 'tray', model_path: '/p/tray.pt', conf: 0.5, iou: 0.5,
 *       roi: [[0.7,0.7],[1,0.7],[1,1],[0.7,1]],
 *       schedule: { type: 'every_n_frames', n: 5 },
 *       class_filter: ['tray_normal','tray_side'],
 *       priority: 50, display_color: '#f59e0b' },
 *   ] }, undefined, undefined, 0)
 */
export const startDetection = (modelPathOrPayload, conf = 0.25, iou = 0.45, channel = 0) => {
  let body;
  if (modelPathOrPayload && typeof modelPathOrPayload === 'object'
      && Array.isArray(modelPathOrPayload.models)) {
    body = { models: modelPathOrPayload.models };
    // 把可选的兜底字段也带上 (后端路径多模型时不读, 但保持类型一致)
    if (modelPathOrPayload.model_path) body.model_path = modelPathOrPayload.model_path;
    if (modelPathOrPayload.conf != null) body.conf = modelPathOrPayload.conf;
    if (modelPathOrPayload.iou != null) body.iou = modelPathOrPayload.iou;
  } else {
    body = {
      model_path: modelPathOrPayload || null,
      conf,
      iou,
    };
  }
  return api.post(`/source/detection/start?channel=${channel}`, body);
};

export const stopDetection = (channel = 0) => api.post(`/source/detection/stop?channel=${channel}`);

export const pauseDetection = (channel = 0) => api.post(`/source/detection/pause?channel=${channel}`);

export const resumeDetection = (channel = 0) => api.post(`/source/detection/resume?channel=${channel}`);

export const standbyDetection = (channel = 0) => api.post(`/source/detection/standby?channel=${channel}`);

export const resumeInference = (channel = 0) => api.post(`/source/detection/resume-inference?channel=${channel}`);

export const getDetectionResults = (channel = 0) => api.get(`/source/detection/results?channel=${channel}`);

export const getSourceStatus = (channel = 0) => api.get(`/source/status?channel=${channel}`);

export const setProjectConfig = (projectConfig, channel = 0) => {
  return api.post(`/source/detection/set-project?channel=${channel}`, projectConfig);
};

export const resetDetection = (channel = 0) => api.post(`/detection/reset?channel=${channel}`);

export const resetDetectionStats = (channel = 0) => api.post(`/source/detection/reset-stats?channel=${channel}`);

export const getDetectionStatus = (channel = 0) => api.get(`/source/status?channel=${channel}`);

// Multi-channel / workstation APIs
export const getWorkstations = () => api.get('/workstations/');

export const setWorkstationMode = (channelCount) => api.post('/workstations/mode', { channel_count: channelCount });

export const setChannelGpu = (channelId, device) => api.post(`/workstations/${channelId}/gpu`, { device });

export const getGpuAllocation = () => api.get('/workstations/gpu-allocation');

// v3.3.0 码-码闭环结算: 查询当前窗口 / 停止时收尾最后一码
export const getScanPairActive = (channel = 0) => api.get(`/scanner/scan-pair/active?channel_id=${channel}`);

export const settleScanPairForStop = (channel = 0, discard = false) =>
  api.post('/scanner/scan-pair/stop', { channel_id: channel, discard });
