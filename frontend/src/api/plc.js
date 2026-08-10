// RFC 13 通用 PLC 连接器 — /api/v1/plc/*
import api from './index'

const BASE = '/plc'

export const getPlcDrivers = () => api.get(`${BASE}/drivers`)
export const getPlcTemplates = () => api.get(`${BASE}/templates`)

export const getPlcConnections = () => api.get(`${BASE}/connections`)
export const createPlcConnection = (data) => api.post(`${BASE}/connections`, data)
export const updatePlcConnection = (id, data) => api.put(`${BASE}/connections/${id}`, data)
export const deletePlcConnection = (id) => api.delete(`${BASE}/connections/${id}`)
export const enablePlcConnection = (id, enabled) => api.post(`${BASE}/connections/${id}/enable`, { enabled })

export const testPlcConnection = (id) => api.post(`${BASE}/connections/${id}/test`)
export const getPlcLive = (id) => api.get(`${BASE}/connections/${id}/live`)
export const writePlcPoint = (id, point, value) => api.post(`${BASE}/connections/${id}/write`, { point, value })
export const getPlcLogs = (id, limit = 100) => api.get(`${BASE}/connections/${id}/logs`, { params: { limit } })
export const mockSetPlcPoint = (id, point, value) => api.post(`${BASE}/connections/${id}/mock-set`, { point, value })

export const exportPlcConnection = (id) => api.get(`${BASE}/connections/${id}/export`)
export const importPlcConnection = (data) => api.post(`${BASE}/import`, data)
