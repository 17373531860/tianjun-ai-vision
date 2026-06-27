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

// ---- 出站主动健康探测 (A2) ----
export const getHealthStatus = () => api.get('/mes/gateway/health-status')
export const probeNow = (id) => api.post(`/mes/gateway/connections/${id}/probe-now`)

// ---- 额外字段 ----
export const setExtraFields = (data) => api.post('/mes/gateway/extra-fields', data)
export const getExtraFields = (channelId = 0) => api.get('/mes/gateway/extra-fields', { params: { channel_id: channelId } })
export const getExtraFieldsSchema = () => api.get('/mes/gateway/extra-fields-schema')

// ---- 工单拉取 (v3.20: 反向对接外部 MES, 主动查询工单回填) ----
// pullTest: 拿编辑中的 config.pull 试一次, 返回结构识别结果 (不依赖已保存连接)
export const pullTest = (data) => api.post('/mes/gateway/pull-test', data)
// pullOrders: 按连接执行同步; dry_run=true 为试同步(不落库), false 为立即同步
export const pullOrders = (id, data) => api.post(`/mes/gateway/connections/${id}/pull`, data || {})

// ---- 通讯日志 ----
export const getGatewayLogs = (params) => api.get('/mes/gateway/logs', { params })

// ---- 入站对接 (外部系统主动 POST 开工/完工任务过来, 我们接收) ----
export const getInboundConfig = () => api.get('/mes/inbound/config')
export const saveInboundConfig = (data) => api.put('/mes/inbound/config', data)
export const getInboundLogs = (params) => api.get('/mes/inbound/logs', { params })

// ---- 在途报警台账 (外部系统已收到的报警, 待其回推消除; 监控页持续横幅轮询用) ----
export const getActiveAlarms = (params) => api.get('/mes/inbound/active-alarms', { params })
