import api from './index';

/**
 * v3.7.0 合并 feat/multi-model-roi-link + session-id:
 *
 * 老单模型 (向后 100% 兼容):
 *   startDetection('/path/main.pt', 0.25, 0.45, 0)
 *
 * 新多模型 (第一参数对象):
 *   startDetection({ models: [
 *     { name: 'main', model_path: '/p/main.pt', conf: 0.3, iou: 0.5,
 *       display_color: '#10b981' },
 *     { name: 'tray', model_path: '/p/tray.pt', conf: 0.5, iou: 0.5,
 *       roi: [[0.7,0.7],[1,0.7],[1,1],[0.7,1]],
 *       schedule: { type: 'every_n_frames', n: 5 },
 *       class_filter: ['tray_normal','tray_side'],
 *       priority: 50, display_color: '#f59e0b' },
 *   ] }, undefined, undefined, 0)
 *
 * sessionName (第 5 参) 可选, 给后端写入 detection_sessions.name (≤ 64).
 */
export const startDetection = (modelPathOrPayload, conf = 0.25, iou = 0.45,
                                channel = 0, sessionName = null) => {
  let body;
  if (modelPathOrPayload && typeof modelPathOrPayload === 'object'
      && Array.isArray(modelPathOrPayload.models)) {
    body = { models: modelPathOrPayload.models };
    if (modelPathOrPayload.model_path) body.model_path = modelPathOrPayload.model_path;
    if (modelPathOrPayload.conf != null) body.conf = modelPathOrPayload.conf;
    if (modelPathOrPayload.iou != null) body.iou = modelPathOrPayload.iou;
  } else {
    body = {
      model_path: modelPathOrPayload || null,
      conf,
      iou,
    };
  }
  if (sessionName) body.session_name = sessionName;
  return api.post(`/source/detection/start?channel=${channel}`, body);
};

export const stopDetection = (channel = 0) => api.post(`/source/detection/stop?channel=${channel}`);

export const pauseDetection = (channel = 0) => api.post(`/source/detection/pause?channel=${channel}`);

export const resumeDetection = (channel = 0) => api.post(`/source/detection/resume?channel=${channel}`);

export const standbyDetection = (channel = 0) => api.post(`/source/detection/standby?channel=${channel}`);

export const resumeInference = (channel = 0) => api.post(`/source/detection/resume-inference?channel=${channel}`);

export const getDetectionResults = (channel = 0, knownShots = null) => {
  let url = `/source/detection/results?channel=${channel}`;
  // B6 截图去重（默认关）：仅当传入已持有指纹时附带 known_shots，后端据此省略未变截图。
  if (knownShots) url += `&known_shots=${encodeURIComponent(knownShots)}`;
  return api.get(url);
};

// v3.42.0 标定用单帧推理: 检测未运行(停止/待机)时对当前帧现推一帧, 给「抓取锚点框」兜底。
// 检测运行中调用则等价返回实时结果。无副作用。
export const inferOnce = (channel = 0) => api.post(`/source/detection/infer-once?channel=${channel}`);

export const getSourceStatus = (channel = 0) => api.get(`/source/status?channel=${channel}`);

export const setProjectConfig = (projectConfig, channel = 0) => {
  return api.post(`/source/detection/set-project?channel=${channel}`, projectConfig);
};

export const resetDetection = (channel = 0) => api.post(`/detection/reset?channel=${channel}`);

export const resetDetectionStats = (channel = 0, scope = 'all') =>
  api.post(`/source/detection/reset-stats?channel=${channel}&scope=${scope}`);

// v3.9.x 工人确认重做 — 解除 require_ack 触发的阻塞态
// v3.23 action: 缺步骤延迟落账挂起时 supplement_step(补步骤判OK) / confirm_ng(认NG) / redo(缺省)
export const ackPendingEvent = (channel = 0, action = null) =>
  api.post(`/source/detection/ack-event?channel=${channel}${action ? `&action=${action}` : ''}`);

// v3.23 借密码提权确认 — 操作员无 ack 权限时, 借管理员账密授权一次, 不改当前登录身份
export const ackPendingEventElevated = (channel = 0, username = '', password = '', action = null) =>
  api.post(`/source/detection/ack-event-elevated?channel=${channel}`, { username, password, action });

// v3.10.2+ per_item 手动周期时机控制
//   只代替"时机判定", 不代替"结果判定". 不可伪造 OK/NG.
//   action:
//     'force_start' — 手动开始周期 (= 画面稳定锁定那一刻)
//     'settle'      — 手动触发结算 (= finish_label 那一刻); OK/NG 由真实覆盖状态判
export const perItemControl = (action, channel = 0) =>
  api.post(`/source/detection/per-item-control?channel=${channel}&action=${encodeURIComponent(action)}`);

export const resetPeriodicAction = (channel = 0, ruleId = null) => {
  const params = new URLSearchParams({ channel: String(channel) });
  if (ruleId) params.append('rule_id', ruleId);
  return api.post(`/source/detection/reset-periodic?${params.toString()}`);
};

export const getDetectionStatus = (channel = 0) => api.get(`/source/status?channel=${channel}`);

// Multi-channel / workstation APIs
export const getWorkstations = () => api.get('/workstations/');

export const setWorkstationMode = (channelCount) => api.post('/workstations/mode', { channel_count: channelCount });

export const setChannelGpu = (channelId, device) => api.post(`/workstations/${channelId}/gpu`, { device });

export const getGpuAllocation = () => api.get('/workstations/gpu-allocation');

// 一期多屏工位显示：配置由后端按 workstation_config.json 顶层分段持久化。
export const getMultiMonitorConfig = () => api.get('/workstations/multi-monitor');

export const setMultiMonitorConfig = (config) => api.put('/workstations/multi-monitor', config);

// v3.3.0 码-码闭环结算: 查询当前窗口 / 停止时收尾最后一码
export const getScanPairActive = (channel = 0) => api.get(`/scanner/scan-pair/active?channel_id=${channel}`);

export const settleScanPairForStop = (channel = 0, discard = false) =>
  api.post('/scanner/scan-pair/stop', { channel_id: channel, discard });
