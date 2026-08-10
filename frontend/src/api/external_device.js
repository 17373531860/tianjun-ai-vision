import api from './index'

const BASE = '/external-devices'

export const getExternalDevices = () => api.get(`${BASE}/`)
export const createExternalDevice = (data) => api.post(`${BASE}/`, data)
export const updateExternalDevice = (id, data) => api.put(`${BASE}/${id}`, data)
export const deleteExternalDevice = (id) => api.delete(`${BASE}/${id}`)
export const getExternalDeviceStatus = () => api.get(`${BASE}/status`)
export const testExternalDevice = (data) => api.post(`${BASE}/test`, data)
export const injectBarcode = (deviceId, barcode) => api.post(`${BASE}/barcode`, { device_id: deviceId, barcode })
export const sendExternalDeviceCommand = (deviceId, command) => api.post(`${BASE}/command`, { device_id: deviceId, command })
// Modbus 完成脉冲「试发脉冲」：wait=true 等 PLC 真写完再返回，界面能直接给出通不通
export const pulseExternalDevice = (deviceId, wait = true) => api.post(`${BASE}/pulse`, { device_id: deviceId, wait })
export const simulateExternalDeviceData = (data) => api.post(`${BASE}/simulate`, data)
export const getExternalDeviceLogs = (params) => api.get(`${BASE}/logs`, { params })
export const clearExternalDeviceLogs = () => api.delete(`${BASE}/logs`)
