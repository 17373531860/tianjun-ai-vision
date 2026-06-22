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
// 强制结案: 管理员/主管手动收尾进行中工单 (必填理由, 审计留痕)
export const forcePackagingSettle = (configId, reason) =>
  api.post(`/packaging-flows/${configId}/force-settle`, { reason });
// v3.23 补滑块: 对挂起中的少装箱补齐数量直接落账 (targetCount 为空=自动补齐到目标)
export const supplementPackagingSliders = (configId, targetCount = null, reason = null) =>
  api.post(`/packaging-flows/${configId}/supplement-sliders`, { target_count: targetCount, reason });
// v3.23 重做: 对挂起中的少装箱丢弃本箱, 等下一检测周期重新结算
export const redoPackagingBox = (configId) =>
  api.post(`/packaging-flows/${configId}/remediation-redo`, {});
