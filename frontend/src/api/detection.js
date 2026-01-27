import api from './index';

// 启动检测 (新版 - 直接传模型路径)
export const startDetection = (modelPath, conf = 0.25, iou = 0.45) => {
  return api.post('/source/detection/start', {
    model_path: modelPath,
    conf,
    iou
  });
};

// 停止检测（只停止推理）
export const stopDetection = () => api.post('/source/detection/stop');

// 暂停：停止画面更新和检测，画面停在当前帧
export const pauseDetection = () => api.post('/source/detection/pause');

// 恢复：从暂停状态恢复
export const resumeDetection = () => api.post('/source/detection/resume');

// 待机：只停止检测推理，画面继续播放
export const standbyDetection = () => api.post('/source/detection/standby');

// 获取检测结果
export const getDetectionResults = () => api.get('/source/detection/results');

// 获取输入源状态
export const getSourceStatus = () => api.get('/source/status');

// 设置项目配置
export const setProjectConfig = (projectConfig) => {
  return api.post('/source/detection/set-project', projectConfig);
};

// 重置检测（保留兼容）
export const resetDetection = () => api.post('/detection/reset');

// 重置统计数据（计数器、步骤计数等）
export const resetDetectionStats = () => api.post('/source/detection/reset-stats');

// 获取检测状态（保留兼容）
export const getDetectionStatus = () => api.get('/source/status');
