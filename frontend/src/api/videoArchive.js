/**
 * 录像归档规则 API (v3.53 一~四期)
 * 后端前缀: /api/v1/export/video-archive/*  (backend/api/video_archive.py)
 */
import api from './index';

// ============ 规则 CRUD ============

export const listArchiveRules = () => {
  return api.get('/export/video-archive/rules');
};

export const getArchiveRule = (id) => {
  return api.get(`/export/video-archive/rules/${id}`);
};

export const createArchiveRule = (payload) => {
  return api.post('/export/video-archive/rules', payload);
};

export const updateArchiveRule = (id, payload) => {
  return api.put(`/export/video-archive/rules/${id}`, payload);
};

export const deleteArchiveRule = (id) => {
  return api.delete(`/export/video-archive/rules/${id}`);
};

export const toggleArchiveRule = (id) => {
  return api.post(`/export/video-archive/rules/${id}/toggle`);
};

// ============ 试归档 / 状态 / 台账 ============

// 试归档: cycle_id 不传取最近一个带录像的周期; rule_id 不传跑全部启用规则
export const testRunArchive = (payload = {}) => {
  return api.post('/export/video-archive/test-run', payload);
};

export const getArchiveStatus = () => {
  return api.get('/export/video-archive/status');
};

export const listArchiveLogs = (params = {}) => {
  return api.get('/export/video-archive/logs', { params });
};

// ============ 四期: 目的地类型 / 历史回补 ============

// 可用目的地类型 (内置 local_dir/ftp/sftp/s3/http + 插件注册的 plugin:*)
export const getAdapterTypes = () => {
  return api.get('/export/video-archive/adapter-types');
};

// 历史回补: 把存量带录像的周期按规则补归档 { rule_id, date_from?, date_to?, limit? }
export const backfillArchive = (payload) => {
  return api.post('/export/video-archive/backfill', payload);
};

// ============ 二期: 证据包 ============

// 手动证据包 (zip 下载): { cycle_ids? | session_id? | date?, ng_only?, limit? }
export const downloadEvidencePack = (payload) => {
  return api.post('/export/video-archive/evidence-pack', payload, {
    responseType: 'blob',
    timeout: 300000, // 大批量打包可能较久
  });
};
