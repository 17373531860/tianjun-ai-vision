import api from './index'

const BASE = '/external-devices'

export const getExternalDevices = () => api.get(`${BASE}/`)
export const createExternalDevice = (data) => api.post(`${BASE}/`, data)
export const updateExternalDevice = (id, data) => api.put(`${BASE}/${id}`, data)
export const deleteExternalDevice = (id) => api.delete(`${BASE}/${id}`)
export const getExternalDeviceStatus = () => api.get(`${BASE}/status`)
export const testExternalDevice = (data) => api.post(`${BASE}/test`, data)
export const injectBarcode = (deviceId, barcode) => api.post(`${BASE}/barcode`, { device_id: deviceId, barcode })
export const simulateExternalDeviceData = (data) => api.post(`${BASE}/simulate`, data)
export const getExternalDeviceLogs = (params) => api.get(`${BASE}/logs`, { params })
export const clearExternalDeviceLogs = () => api.delete(`${BASE}/logs`)
