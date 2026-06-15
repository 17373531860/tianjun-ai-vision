// v3.21: 包装箱结算 (Packaging Flow) REST API client
// 与 backend/api/packaging_flows.py 对齐
import api from './index';

export const listPackagingFlows = () => api.get('/packaging-flows');
export const createPackagingFlow = (payload) => api.post('/packaging-flows', payload);
export const getPackagingFlow = (id) => api.get(`/packaging-flows/${id}`);
export const updatePackagingFlow = (id, payload) => api.put(`/packaging-flows/${id}`, payload);
export const deletePackagingFlow = (id) => api.delete(`/packaging-flows/${id}`);
export const getPackagingFlowState = (id) => api.get(`/packaging-flows/${id}/state`);
// 扫码入口: USB 扫码枪在前端捕获原始码后喂给后端状态机
export const packagingScan = (payload) => api.post('/packaging-flows/scan', payload);
