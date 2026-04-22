import api from './index'

export const getClusterConfig = () => api.get('/cluster/config')
export const updateClusterConfig = (data) => api.put('/cluster/config', data)

export const getClusterBoxes = (params) => api.get('/cluster/boxes', { params })
export const getBoxDetail = (boxSerial) => api.get(`/cluster/boxes/${encodeURIComponent(boxSerial)}`)

export const clusterHealth = () => api.get('/cluster/health')

export const sendHeartbeat = (data) => api.post('/cluster/heartbeat', data)
export const getConnectedSlaves = () => api.get('/cluster/slaves')

// 删除单箱记录（BoxAggregation + BoxSummary）
export const deleteBox = (boxSerial) =>
  api.delete(`/cluster/boxes/${encodeURIComponent(boxSerial)}`)

// 批量清理集群记录。scope: 'all' | 'pending' | 'recent'
export const clearBoxes = (scope = 'all') =>
  api.delete('/cluster/boxes', { params: { scope } })
