import api from './index'

const BASE = '/scanner/wmax'

export const discoverWMaxDevices = (timeout = 2) =>
  api.get(`${BASE}/discover`, { params: { timeout } })

export const connectWMaxDevice = (ip, port = 55266) =>
  api.post(`${BASE}/connect`, { ip, port })

export const disconnectWMaxDevice = (ip, port = 55266) =>
  api.post(`${BASE}/disconnect`, { ip, port })

export const getWMaxStatus = () =>
  api.get(`${BASE}/status`)

export const wmaxHandshake = (ip, port = 55266) =>
  api.post(`${BASE}/handshake`, { ip, port })

export const wmaxLoadConfig = (ip, port = 55266, configId = -1) =>
  api.post(`${BASE}/load-config`, { ip, port }, { params: { config_id: configId } })

export const wmaxSetParams = (ip, port = 55266, params = {}) =>
  api.put(`${BASE}/params`, { ip, port, ...params })

export const wmaxSaveParams = (ip, port = 55266, params = {}) =>
  api.post(`${BASE}/save-params`, { ip, port, ...params })

export const wmaxLoadPreset = (ip, port = 55266, configId = 0) =>
  api.post(`${BASE}/preset/load`, { ip, port, config_id: configId })

export const wmaxSavePreset = (ip, port = 55266, configId = 0) =>
  api.post(`${BASE}/preset/save`, { ip, port, config_id: configId })

export const wmaxAutoFocus = (ip, port = 55266, start = true) =>
  api.post(`${BASE}/autofocus`, { ip, port }, { params: { start } })

export const wmaxStartTune = (ip, port = 55266) =>
  api.post(`${BASE}/autotune`, { ip, port })

export const wmaxCancelTune = (ip, port = 55266) =>
  api.post(`${BASE}/cancel-tune`, { ip, port })

export const wmaxTurnOnVideo = (ip, port = 55266, on = true) =>
  api.post(`${BASE}/video`, { ip, port, on })

export const wmaxTurnOnTriggerImage = (ip, port = 55266, on = true) =>
  api.post(`${BASE}/trigger-image`, { ip, port, on })

export const wmaxGetLastCode = (ip, port = 55266) =>
  api.get(`${BASE}/last-code`, { params: { ip, port } })

export const wmaxTrigger = (ip, port = 55266, on = true) =>
  api.post(`${BASE}/trigger`, { ip, port, on })

export const wmaxStartReadRate = (ip, port = 55266) =>
  api.post(`${BASE}/read-rate/start`, { ip, port })

export const wmaxStopReadRate = (ip, port = 55266) =>
  api.post(`${BASE}/read-rate/stop`, { ip, port })

export const wmaxGetReadRate = (ip, port = 55266) =>
  api.get(`${BASE}/read-rate/result`, { params: { ip, port } })

export const wmaxReboot = (ip, port = 55266) =>
  api.post(`${BASE}/reboot`, { ip, port })

export const wmaxReset = (ip, port = 55266) =>
  api.post(`${BASE}/reset`, { ip, port })

export const wmaxSetOutputConfig = (ip, port = 55266, signalMask = 0, durationMs = 150, save = false) =>
  api.put(`${BASE}/output-config`, { ip, port, signal_mask: signalMask, duration_ms: durationMs, save })

export const wmaxSetIndicatorConfig = (ip, port = 55266, mode = 3, save = false) =>
  api.put(`${BASE}/indicator-config`, { ip, port, mode, save })

export const wmaxAutoDiscover = (timeout = 3) =>
  api.post(`${BASE}/auto-discover`, null, { params: { timeout } })

export const wmaxCreateVirtual = () =>
  api.post(`${BASE}/virtual/create`)

export const wmaxDeleteVirtual = () =>
  api.post(`${BASE}/virtual/delete`)
