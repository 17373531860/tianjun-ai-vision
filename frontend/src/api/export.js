/**
 * v3.5.0 自定义导出系统 API
 *
 * 后端路径前缀: /api/v1/export
 * 字段树 + 模板 CRUD + 预览/下载
 */
import api, { getBackendHost } from './index';

// ============ 字段元数据 ============

// 字段树（按 group 分组）— 前端字段树左侧拉取此端点
export const getExportFields = (flat = false) => {
  return api.get('/export/fields', { params: { flat } });
};

// ============ 模板 CRUD ============

// 模板列表（可按 format/scope 过滤）
export const listExportTemplates = (params = {}) => {
  return api.get('/export/templates', { params });
};

// 路线 B: 上传 docx/xlsx 占位符模板文件
export const uploadTemplateFile = (templateId, file) => {
  const fd = new FormData();
  fd.append('file', file);
  return api.post(`/export/templates/${templateId}/upload-template-file`, fd, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
};

// 路线 B: 删除已上传的占位符文件（回退到路线 A 自动样式）
export const deleteTemplateFile = (templateId) => {
  return api.delete(`/export/templates/${templateId}/template-file`);
};

// 路线 B: 下载占位符文件原文（让用户改完再上传）
export const getTemplateFileDownloadUrl = (templateId) => {
  return `${getBackendHost()}/api/v1/export/templates/${templateId}/template-file`;
};

export const getExportTemplate = (id) => {
  return api.get(`/export/templates/${id}`);
};

export const createExportTemplate = (payload) => {
  return api.post('/export/templates', payload);
};

export const updateExportTemplate = (id, payload) => {
  return api.put(`/export/templates/${id}`, payload);
};

export const deleteExportTemplate = (id) => {
  return api.delete(`/export/templates/${id}`);
};

// 复制系统预设到独立自建副本
export const cloneExportTemplate = (id, newName = null) => {
  const params = newName ? { new_name: newName } : {};
  return api.post(`/export/templates/${id}/clone`, null, { params });
};

// ============ 渲染（预览 / 下载） ============

/**
 * 预览模板渲染结果
 * @param {Object} payload {
 *   template_content?: string,    // 模板源（与 template_id 二选一）
 *   template_id?: number,
 *   filename_template?: string,   // 文件名模板
 *   fmt?: 'txt'|'csv'|'docx'|'xlsx'|'pdf',
 *   cycle_id?: number,            // 单 cycle 上下文
 *   session_id?: number,          // session 范围上下文
 *   start_date?: string,          // 日期范围
 *   end_date?: string,
 *   project_id?: number,
 *   channel_id?: number,
 *   include_cycles?: boolean,
 *   license_payload?: Object,     // 前端 IPC 拿到的 license
 * }
 */
export const previewExport = (payload) => {
  return api.post('/export/preview', payload);
};

/**
 * 立即渲染并下载（流式响应）
 * 返回的是 Blob，配合 downloadBlob() 使用
 */
export const renderExport = (payload) => {
  return api.post('/export/render', payload, { responseType: 'blob' });
};

// ============ 实时规则 (v3.5.0 Step 4) ============

export const listRealtimeRules = (params = {}) => {
  return api.get('/export/realtime-rules', { params });
};

export const getRealtimeRule = (id) => {
  return api.get(`/export/realtime-rules/${id}`);
};

export const createRealtimeRule = (payload) => {
  return api.post('/export/realtime-rules', payload);
};

export const updateRealtimeRule = (id, payload) => {
  return api.put(`/export/realtime-rules/${id}`, payload);
};

export const deleteRealtimeRule = (id) => {
  return api.delete(`/export/realtime-rules/${id}`);
};

export const toggleRealtimeRule = (id) => {
  return api.post(`/export/realtime-rules/${id}/toggle`);
};

// 手动测试触发：传 cycle_id 用真实数据，不传走 system 上下文
export const testRunRealtimeRule = (id, payload = {}) => {
  return api.post(`/export/realtime-rules/${id}/test-run`, payload);
};

export const listRuleLogs = (ruleId, params = {}) => {
  return api.get(`/export/realtime-rules/${ruleId}/logs`, { params });
};

export const listAllRunLogs = (params = {}) => {
  return api.get('/export/run-logs', { params });
};

// ============ 定时导出规则 (v3.8.x) ============

export const listScheduledRules = (params = {}) => {
  return api.get('/export/scheduled-rules', { params });
};

export const getScheduledRule = (id) => {
  return api.get(`/export/scheduled-rules/${id}`);
};

export const createScheduledRule = (payload) => {
  return api.post('/export/scheduled-rules', payload);
};

export const updateScheduledRule = (id, payload) => {
  return api.put(`/export/scheduled-rules/${id}`, payload);
};

export const deleteScheduledRule = (id) => {
  return api.delete(`/export/scheduled-rules/${id}`);
};

export const toggleScheduledRule = (id) => {
  return api.post(`/export/scheduled-rules/${id}/toggle`);
};

export const testRunScheduledRule = (id) => {
  return api.post(`/export/scheduled-rules/${id}/test-run`);
};

export const listScheduledRuleLogs = (id, params = {}) => {
  return api.get(`/export/scheduled-rules/${id}/logs`, { params });
};

export const getDefaultOutputDir = () => {
  return api.get('/export/scheduled-rules/_default-output-dir');
};

export const setDefaultOutputDir = (value) => {
  return api.put('/export/scheduled-rules/_default-output-dir', { value });
};

export const previewCron = (cronExpression, count = 5) => {
  return api.post('/export/scheduled-rules/_cron-preview', {
    cron_expression: cronExpression,
    count,
  });
};

// ============ 辅助 ============

// 把后端返回的 Blob + filename 触发浏览器下载
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

// 从 axios 响应解析 Content-Disposition 里的 filename
// 兼容 RFC 5987 (filename*=UTF-8''xxx) 和普通 filename="xxx"
export const parseFilenameFromResponse = (response, fallback = 'export.txt') => {
  const cd = response.headers?.['content-disposition'] || '';
  // RFC 5987 优先
  const m1 = /filename\*=UTF-8''([^;]+)/i.exec(cd);
  if (m1) {
    try { return decodeURIComponent(m1[1].trim()); } catch { /* noop */ }
  }
  const m2 = /filename="?([^";]+)"?/i.exec(cd);
  if (m2) return m2[1].trim();
  return fallback;
};
