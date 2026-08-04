// 每日短信日报 (v3.46) — /api/v1/sms-report/*
import api from './index';

// ============ 规则 CRUD ============

export const listSmsRules = (params = {}) => {
  return api.get('/sms-report/rules', { params });
};

export const getSmsRule = (id) => {
  return api.get(`/sms-report/rules/${id}`);
};

export const createSmsRule = (payload) => {
  return api.post('/sms-report/rules', payload);
};

export const updateSmsRule = (id, payload) => {
  return api.put(`/sms-report/rules/${id}`, payload);
};

export const deleteSmsRule = (id) => {
  return api.delete(`/sms-report/rules/${id}`);
};

export const toggleSmsRule = (id) => {
  return api.post(`/sms-report/rules/${id}/toggle`);
};

// ============ 试发 / 预览 / 日志 ============

// useMock=true 走 mock 适配器, 不发真短信 (开发/验收用)
export const testSendSmsRule = (id, useMock = false) => {
  return api.post(`/sms-report/rules/${id}/test-send`, null, {
    params: { use_mock: useMock },
  });
};

export const previewSmsRule = (id) => {
  return api.post(`/sms-report/rules/${id}/preview`);
};

export const listSmsRuleLogs = (id, params = {}) => {
  return api.get(`/sms-report/rules/${id}/logs`, { params });
};

// ============ 服务商配置 ============

export const getSmsProviderConfig = () => {
  return api.get('/sms-report/provider-config');
};

export const setSmsProviderConfig = (provider, config) => {
  return api.put('/sms-report/provider-config', { provider, config });
};

export const listSmsProviders = () => {
  return api.get('/sms-report/providers');
};
