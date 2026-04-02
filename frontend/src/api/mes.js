import api from './index'

// ---- 工单 ----
export const getOrders = (params) => api.get('/mes/orders', { params })
export const createOrder = (data) => api.post('/mes/orders', data)
export const getOrder = (id) => api.get(`/mes/orders/${id}`)
export const updateOrder = (id, data) => api.put(`/mes/orders/${id}`, data)
export const changeOrderStatus = (id, data) => api.post(`/mes/orders/${id}/status`, data)
export const deleteOrder = (id) => api.delete(`/mes/orders/${id}`)
export const getOrderSummary = (id) => api.get(`/mes/orders/${id}/summary`)

// ---- 批次 ----
export const createBatch = (orderId, data) => api.post(`/mes/orders/${orderId}/batches`, data)
export const getBatches = (orderId) => api.get(`/mes/orders/${orderId}/batches`)

// ---- 工件 ----
export const getWorkpieces = (params) => api.get('/mes/workpieces', { params })
export const registerWorkpiece = (data) => api.post('/mes/workpieces', data)
export const getWorkpiece = (id) => api.get(`/mes/workpieces/${id}`)
export const getWorkpieceTrace = (id) => api.get(`/mes/workpieces/${id}/trace`)
export const workpieceAction = (id, data) => api.post(`/mes/workpieces/${id}/action`, data)
export const searchWorkpieces = (keyword, params) => api.get(`/mes/workpieces/search/${keyword}`, { params })

// ---- 缺陷 ----
export const getDefects = (params) => api.get('/mes/defects', { params })
export const createDefect = (data) => api.post('/mes/defects', data)
export const getPareto = (params) => api.get('/mes/defects/pareto', { params })

// ---- 缺陷代码 ----
export const getDefectCodes = (params) => api.get('/mes/defect-codes', { params })
export const createDefectCode = (data) => api.post('/mes/defect-codes', data)
export const updateDefectCode = (id, data) => api.put(`/mes/defect-codes/${id}`, data)
export const deleteDefectCode = (id) => api.delete(`/mes/defect-codes/${id}`)
