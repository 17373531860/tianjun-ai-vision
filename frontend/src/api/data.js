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
export const getSessionsByDate = (date, projectId = null, startHour = null, endHour = null, channelId = null, shift = null) => {
  const params = {};
  if (projectId) params.project_id = projectId;
  if (startHour) params.start_hour = startHour;
  if (endHour) params.end_hour = endHour;
  if (channelId !== null && channelId !== undefined) params.channel_id = channelId;
  if (shift) params.shift = shift;
  return api.get(`/data/sessions/by-date/${date}`, { params });
};

// 获取有会话记录的日期列表
export const getSessionDates = (params = {}) => {
  return api.get('/data/sessions/dates', { params });
};

// 重命名会话（v3.6.2 新增 — 客户自定义会话标识，含合法性校验，参数 name 为 null/空清空标识）
export const renameSession = (sessionId, name) => {
  return api.patch(`/data/sessions/${sessionId}/name`, { name: name || null });
};

// ============ 周期管理 ============

// 获取会话的周期（分页）
export const getSessionCycles = (sessionId, skip = 0, limit = 50) => {
  return api.get(`/data/sessions/${sessionId}/cycles`, { params: { skip, limit } });
};

// 获取单个周期详情
export const getCycle = (cycleId) => {
  return api.get(`/data/cycles/${cycleId}`);
};

// v3.4.3 按工件条码全局检索关联检测周期 (跨 session/日期)
export const searchCyclesBySerial = (serialNo, { skip = 0, limit = 50, fuzzy = true } = {}) => {
  const sn = encodeURIComponent((serialNo || '').trim());
  return api.get(`/data/cycles/by-serial/${sn}`, { params: { skip, limit, fuzzy } });
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

// v2.7.2: 三个批量导出 API 支持 projectId/channelId 过滤，避免不同项目/工位数据混入同一份 CSV
// projectId=null 表示"全部项目"，channelId=null 表示"全部工位"
// v3.5.x: ptMode/ctMode 跟随显示设置 (avg/last/current)，avg 时 CSV 多输出"耗时(平均/秒)"列
const _appendModeParams = (params, ptMode, ctMode) => {
  if (ptMode) params.pt_mode = ptMode;
  if (ctMode) params.ct_mode = ctMode;
};

// v3.8.x: outputFormat 支持 csv/txt/xlsx/docx/pdf, 默认 csv
const _appendFormat = (params, outputFormat) => {
  if (outputFormat && outputFormat !== 'csv') params.output_format = outputFormat;
};

export const exportDateRangeCsv = (startDate, endDate, startHour = null, endHour = null, projectId = null, channelId = null, ptMode = null, ctMode = null, outputFormat = 'csv') => {
  const params = { export_type: 'all', start_date: startDate, end_date: endDate };
  if (startHour) params.start_hour = startHour;
  if (endHour) params.end_hour = endHour;
  if (projectId !== null && projectId !== undefined) params.project_id = projectId;
  if (channelId !== null && channelId !== undefined) params.channel_id = channelId;
  _appendModeParams(params, ptMode, ctMode);
  _appendFormat(params, outputFormat);
  return api.get('/data/export/csv', {
    params,
    responseType: 'blob'
  });
};

export const exportWeekCsv = (week, startHour = null, endHour = null, projectId = null, channelId = null, ptMode = null, ctMode = null, outputFormat = 'csv') => {
  const params = { export_type: 'all', week };
  if (startHour) params.start_hour = startHour;
  if (endHour) params.end_hour = endHour;
  if (projectId !== null && projectId !== undefined) params.project_id = projectId;
  if (channelId !== null && channelId !== undefined) params.channel_id = channelId;
  _appendModeParams(params, ptMode, ctMode);
  _appendFormat(params, outputFormat);
  return api.get('/data/export/csv', {
    params,
    responseType: 'blob'
  });
};

export const exportMonthCsv = (month, startHour = null, endHour = null, projectId = null, channelId = null, ptMode = null, ctMode = null, outputFormat = 'csv') => {
  const params = { export_type: 'all', month };
  if (startHour) params.start_hour = startHour;
  if (endHour) params.end_hour = endHour;
  if (projectId !== null && projectId !== undefined) params.project_id = projectId;
  if (channelId !== null && channelId !== undefined) params.channel_id = channelId;
  _appendModeParams(params, ptMode, ctMode);
  _appendFormat(params, outputFormat);
  return api.get('/data/export/csv', {
    params,
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

// 按日期范围删除数据
export const clearDataByRange = (startDate, endDate) => {
  return api.delete('/data/clear/range', { data: { start_date: startDate, end_date: endDate } });
};

// ============ 清理设置 ============

// 获取清理设置
export const getCleanupSettings = () => {
  return api.get('/data/cleanup-settings');
};

// 更新清理设置
export const updateCleanupSettings = (settings) => {
  return api.put('/data/cleanup-settings', settings);
};

// 手动触发清理
export const runCleanupNow = () => {
  return api.post('/data/cleanup/run');
};

// ============ 存储信息 ============

// 获取存储空间信息
export const getStorageInfo = () => {
  return api.get('/data/storage-info');
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
