// v3.14 RFC 11: Workpiece Flow REST API client
// 与 backend/api/workpiece_flows.py 对齐
import api from './index';

export const listFlows = () => api.get('/workpiece-flows');
export const createFlow = (payload) => api.post('/workpiece-flows', payload);
export const getFlow = (id) => api.get(`/workpiece-flows/${id}`);
export const updateFlow = (id, payload) => api.put(`/workpiece-flows/${id}`, payload);
export const deleteFlow = (id) => api.delete(`/workpiece-flows/${id}`);
export const getFlowState = (id) => api.get(`/workpiece-flows/${id}/state`);
export const listFlowRuns = (id, params = {}) =>
  api.get(`/workpiece-flows/${id}/runs`, { params });
export const getFlowRun = (runId) => api.get(`/workpiece-flows/runs/${runId}`);
