/**
 * 数据管理 API
 * 包含会话、周期、步骤记录和导出功能
 */
import api, { getBackendHost } from './index';

// ============ 会话管理 ============

// 获取会话列表
export const getSessions = (params = {}) => {
  return api.get('/data/sessions', { params });
};

// 获取单个会话详情
export const getSession = (sessionId) => {
  return api.get(`/data/sessions/${sessionId}`);
};

// 获取指定日期的会话概览
export const getSessionsByDate = (date, projectId = null) => {
  const params = projectId ? { project_id: projectId } : {};
  return api.get(`/data/sessions/by-date/${date}`, { params });
};

// 获取有会话记录的日期列表
export const getSessionDates = (params = {}) => {
  return api.get('/data/sessions/dates', { params });
};

// ============ 周期管理 ============

// 获取会话的所有周期
export const getSessionCycles = (sessionId) => {
  return api.get(`/data/sessions/${sessionId}/cycles`);
};

// 获取单个周期详情
export const getCycle = (cycleId) => {
  return api.get(`/data/cycles/${cycleId}`);
};

// 获取周期的所有步骤记录
export const getCycleSteps = (cycleId) => {
  return api.get(`/data/cycles/${cycleId}/steps`);
};

// ============ 视频管理 ============

// 获取视频列表
export const getVideos = (params = {}) => {
  return api.get('/data/videos', { params });
};

// 获取视频播放URL
export const getVideoUrl = (videoId) => {
  return `${getBackendHost()}/api/v1/data/videos/${videoId}`;
};

// ============ 统计 API ============

// 获取步骤平均耗时和间隔统计
export const getStepAverages = (params = {}) => {
  return api.get('/data/stats/step-averages', { params });
};

// 获取周期平均耗时统计
export const getCycleAverages = (params = {}) => {
  return api.get('/data/stats/cycle-averages', { params });
};

// ============ 导出设置 ============

// 获取导出设置
export const getExportSettings = () => {
  return api.get('/data/export-settings');
};

// 更新导出设置
export const updateExportSettings = (settings) => {
  return api.put('/data/export-settings', settings);
};

// ============ CSV 导出 ============

// 导出CSV
export const exportCsv = (params = {}) => {
  return api.get('/data/export/csv', { 
    params,
    responseType: 'blob'
  });
};

// 导出单个会话的CSV
export const exportSessionCsv = (sessionId) => {
  return api.get('/data/export/csv', {
    params: { export_type: 'session', session_id: sessionId },
    responseType: 'blob'
  });
};

// 导出单个周期的CSV
export const exportCycleCsv = (cycleId) => {
  return api.get('/data/export/csv', {
    params: { export_type: 'cycle', cycle_id: cycleId },
    responseType: 'blob'
  });
};

// 导出日期范围的CSV
export const exportDateRangeCsv = (startDate, endDate) => {
  return api.get('/data/export/csv', {
    params: { export_type: 'all', start_date: startDate, end_date: endDate },
    responseType: 'blob'
  });
};

// 导出某周的CSV
export const exportWeekCsv = (week) => {
  return api.get('/data/export/csv', {
    params: { export_type: 'all', week },
    responseType: 'blob'
  });
};

// 导出某月的CSV
export const exportMonthCsv = (month) => {
  return api.get('/data/export/csv', {
    params: { export_type: 'all', month },
    responseType: 'blob'
  });
};

// ============ 数据库备份 ============

// 备份数据库（触发下载）
export const backupDatabase = () => {
  window.open(`${getBackendHost()}/api/v1/data/backup/database`, '_blank');
};

// ============ 数据清理 ============

// 清空所有历史数据（会话、周期、步骤、视频）
export const clearAllData = () => {
  return api.delete('/data/clear/all');
};

// ============ 辅助函数 ============

// 下载Blob文件
export const downloadBlob = (blob, filename) => {
  const url = window.URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  window.URL.revokeObjectURL(url);
};
