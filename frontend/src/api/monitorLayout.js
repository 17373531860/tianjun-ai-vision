// v3.54 检测主页自定义布局 — 后端 /api/v1/system/monitor-layouts
// 布局按"形态键"独立存 SystemConfig KV(跟数据目录走, 升级/备份不丢);
// 读不鉴权, 写/删挂 monitor.layout.edit 权限。
import api from './index';

export const getMonitorLayouts = () => {
  return api.get('/system/monitor-layouts');
};

export const putMonitorLayout = (formKey, layout) => {
  return api.put(`/system/monitor-layouts/${encodeURIComponent(formKey)}`, layout);
};

export const deleteMonitorLayout = (formKey) => {
  return api.delete(`/system/monitor-layouts/${encodeURIComponent(formKey)}`);
};

export const deleteAllMonitorLayouts = () => {
  return api.delete('/system/monitor-layouts');
};
