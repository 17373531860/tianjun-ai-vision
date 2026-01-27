// src/api/report.js
import api from './index';

// 获取统计摘要（良率、总数等）
export const getSummary = (params) => api.get('/reports/summary', { params });

// 获取历史检测记录（含图片路径）
export const getRecords = (params) => api.get('/reports/records', { params });

// 获取趋势数据
export const getTrend = (params) => api.get('/reports/trend', { params });

// 获取每日统计
export const getDailyStats = (params) => api.get('/reports/daily-stats', { params });

// 导出 PDF 报表
export const exportPdfReport = (params) => api.get('/reports/export', { 
  params: { ...params, format: 'pdf' }, 
  responseType: 'blob' 
});

// 导出 CSV 报表
export const exportCsvReport = (params) => api.get('/reports/export', { 
  params: { ...params, format: 'csv' }, 
  responseType: 'blob' 
});