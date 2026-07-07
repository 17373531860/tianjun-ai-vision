import api from './index'

// 原生称重投料模式 (logic_mode='weighing') 控制/查询接口
const BASE = '/weighing'

export const getWeighingState = (channel) => api.get(`${BASE}/state`, { params: channel != null ? { channel } : {} })
export const setWeighingContext = (data) => api.post(`${BASE}/context`, data)
export const weighingScan = (data) => api.post(`${BASE}/scan`, data)
export const weighingTare = (channel_id) => api.post(`${BASE}/tare`, { channel_id })
export const weighingZero = (channel_id) => api.post(`${BASE}/zero`, { channel_id })
export const weighingReset = (channel_id) => api.post(`${BASE}/reset`, { channel_id })
export const weighingMaterialLabel = (data) => api.post(`${BASE}/material-label`, data)
export const getWeighingRecords = (channel, limit = 200) => api.get(`${BASE}/records`, { params: { channel, limit } })
export const weighingFeed = (channel_id, weight) => api.post(`${BASE}/feed`, { channel_id, weight })
