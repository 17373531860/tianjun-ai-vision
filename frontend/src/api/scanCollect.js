// v3.56 周期多码采集 API 封装 (/api/v1/scan-collect/*)
import api from './index'

// 配置（按项目存, 编辑入口在 MES管理→扫码器→多码采集）
export const getScanCollectConfig = (projectId) =>
  api.get(`/scan-collect/config?project_id=${projectId}`)
export const saveScanCollectConfig = (projectId, data) =>
  api.put(`/scan-collect/config?project_id=${projectId}`, data)

// 当前码组实况（Monitor 主用 detection/results 载荷内的 scan_collect 段, 这是兜底）
export const getScanCollectState = (channel = 0) =>
  api.get(`/scan-collect/state?channel=${channel}`)

// 纠错
export const removeScanCollectCode = (channelId, recordId) =>
  api.post('/scan-collect/remove-code', { channel_id: channelId, record_id: recordId })
export const clearScanCollectGroup = (channelId) =>
  api.post('/scan-collect/clear', { channel_id: channelId })

// v3.56.1 NG 挂起人工放行（挂起组按 NG 结算导出, 开放下一工件）
export const resolveScanCollectNg = (channelId) =>
  api.post('/scan-collect/resolve-ng', { channel_id: channelId })

// 追溯（工件详情反查组件码）
export const getScanCollectRecords = (params) =>
  api.get('/scan-collect/records', { params })
