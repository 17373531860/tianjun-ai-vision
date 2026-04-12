import api from './index'

export const getScannerDevices = () => api.get('/scanner/devices')
export const createScannerDevice = (data) => api.post('/scanner/devices', data)
export const updateScannerDevice = (id, data) => api.put(`/scanner/devices/${id}`, data)
export const deleteScannerDevice = (id) => api.delete(`/scanner/devices/${id}`)
export const testScannerConnection = (ip, port = 55256) => api.post(`/scanner/devices/test?ip=${ip}&port=${port}`)
export const getScannerStatus = () => api.get('/scanner/status')
export const getLatestScan = (channelId) => api.get(`/scanner/latest/${channelId}`)
export const getScanLogs = (params) => api.get('/scanner/logs', { params })
export const clearScanLogs = () => api.delete('/scanner/logs')
