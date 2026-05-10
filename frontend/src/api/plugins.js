import api from './index';

export function getPlugins() {
  return api.get('/plugins');
}

export function installPlugin(file) {
  const form = new FormData();
  form.append('file', file);
  return api.post('/plugins/install', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 120000,
  });
}

export function activatePlugin(customerCode) {
  return api.post(`/plugins/${encodeURIComponent(customerCode)}/activate`);
}

export function deactivatePlugin(customerCode) {
  return api.post(`/plugins/${encodeURIComponent(customerCode)}/deactivate`);
}

export function deletePlugin(customerCode) {
  return api.delete(`/plugins/${encodeURIComponent(customerCode)}`);
}

export function getPluginStatus(customerCode) {
  return api.get(`/plugins/${encodeURIComponent(customerCode)}/status`);
}
