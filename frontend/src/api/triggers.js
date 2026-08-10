import api from './index'

const BASE = '/triggers'

export const getTriggerTypes = () => api.get(`${BASE}/types`)
export const getTriggerActions = () => api.get(`${BASE}/actions`)
export const getTriggerTemplates = () => api.get(`${BASE}/templates`)

export const getTriggers = () => api.get(`${BASE}/channels`)
export const createTrigger = (data) => api.post(`${BASE}/channels`, data)
export const updateTrigger = (id, data) => api.put(`${BASE}/channels/${id}`, data)
export const deleteTrigger = (id) => api.delete(`${BASE}/channels/${id}`)
export const enableTrigger = (id, enabled) => api.post(`${BASE}/channels/${id}/enable`, { enabled })

export const getTriggerLive = (id) => api.get(`${BASE}/channels/${id}/live`)
export const getTriggerHistory = (id, limit = 50) => api.get(`${BASE}/channels/${id}/history`, { params: { limit } })
export const getTriggerLogs = (id, limit = 100) => api.get(`${BASE}/channels/${id}/logs`, { params: { limit } })

export const testFireTrigger = (id, ruleIndex = 0) => api.post(`${BASE}/channels/${id}/test`, { rule_index: ruleIndex })
export const mockFireTrigger = (id, meta = {}) => api.post(`${BASE}/channels/${id}/mock-fire`, { meta })
export const mockLevelTrigger = (id, value, meta = {}) => api.post(`${BASE}/channels/${id}/mock-level`, { value, meta })
export const calibrateTrigger = (id) => api.post(`${BASE}/channels/${id}/calibrate`)

export const exportTrigger = (id) => api.get(`${BASE}/channels/${id}/export`)
export const importTrigger = (data) => api.post(`${BASE}/import`, data)
