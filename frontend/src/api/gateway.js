import api from './index'

// ---- 连接管理 ----
export const getConnections = () => api.get('/mes/gateway/connections')
export const createConnection = (data) => api.post('/mes/gateway/connections', data)
export const getConnection = (id) => api.get(`/mes/gateway/connections/${id}`)
export const updateConnection = (id, data) => api.put(`/mes/gateway/connections/${id}`, data)
export const deleteConnection = (id) => api.delete(`/mes/gateway/connections/${id}`)

// ---- 测试 / 推送 ----
export const testConnection = (id, data) => api.post(`/mes/gateway/connections/${id}/test`, data || {})
export const manualPush = (id, data) => api.post(`/mes/gateway/connections/${id}/push`, data)

// ---- 额外字段 ----
export const setExtraFields = (data) => api.post('/mes/gateway/extra-fields', data)
export const getExtraFields = (channelId = 0) => api.get('/mes/gateway/extra-fields', { params: { channel_id: channelId } })
export const getExtraFieldsSchema = () => api.get('/mes/gateway/extra-fields-schema')

// ---- 通讯日志 ----
export const getGatewayLogs = (params) => api.get('/mes/gateway/logs', { params })
