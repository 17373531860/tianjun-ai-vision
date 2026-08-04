import api from './index'

const BASE = '/sms'

export const getSmsConfig = () => api.get(`${BASE}/config`)
export const updateSmsConfig = (data) => api.put(`${BASE}/config`, data)
export const getSmsPorts = () => api.get(`${BASE}/ports`)
export const testSms = (data = {}) => api.post(`${BASE}/test`, data)
