import api from './index'

export const getClusterConfig = () => api.get('/cluster/config')
export const updateClusterConfig = (data) => api.put('/cluster/config', data)

export const getClusterBoxes = () => api.get('/cluster/boxes')
export const getBoxDetail = (boxSerial) => api.get(`/cluster/boxes/${encodeURIComponent(boxSerial)}`)

export const clusterHealth = () => api.get('/cluster/health')
