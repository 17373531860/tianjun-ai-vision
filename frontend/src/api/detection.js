import api from './index';

export const startDetection = (modelPath, conf = 0.25, iou = 0.45, channel = 0) => {
  return api.post(`/source/detection/start?channel=${channel}`, {
    model_path: modelPath,
    conf,
    iou
  });
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
