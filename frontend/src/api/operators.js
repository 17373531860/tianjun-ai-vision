import api from './index'

export const getOperators = (params) => api.get('/operators', { params })
export const createOperator = (data) => api.post('/operators', data)
export const updateOperator = (id, data) => api.put(`/operators/${id}`, data)
export const deleteOperator = (id) => api.delete(`/operators/${id}`)
export const setCurrentOperator = (data) => api.post('/operators/set-current', data)
export const getCurrentOperator = (channelId = 0) => api.get('/operators/current', { params: { channel_id: channelId } })
