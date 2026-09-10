// AI 能力试用 (2026-09 全量批次): OCR 读字 + 异常检测 + VLM 坐诊
// 对应后端 /api/v1/ocr/* 与 /api/v1/anomaly/* 与 /api/v1/vlm/*
import api from './index';

// ---------- OCR ----------
export function getOcrStatus() {
  return api.get('/ocr/status').then(r => r.data);
}

export function ocrReadImage(file, { roi = null, minScore = 0.5 } = {}) {
  const fd = new FormData();
  fd.append('file', file);
  if (roi) fd.append('roi', JSON.stringify(roi));
  fd.append('min_score', String(minScore));
  return api.post('/ocr/read', fd, { headers: { 'Content-Type': 'multipart/form-data' } }).then(r => r.data);
}

export function ocrReadFrame(channelId = 0, { roi = null, minScore = 0.5 } = {}) {
  return api.post('/ocr/read-frame', { channel_id: channelId, roi, min_score: minScore }).then(r => r.data);
}

// ---------- 异常检测 ----------
export function listAnomalyBanks() {
  return api.get('/anomaly/banks').then(r => r.data);
}

export function createAnomalyBank(name, files) {
  const fd = new FormData();
  files.forEach(f => fd.append('files', f));
  fd.append('name', name || '');
  return api.post('/anomaly/banks', fd, { headers: { 'Content-Type': 'multipart/form-data' }, timeout: 300000 }).then(r => r.data);
}

export function deleteAnomalyBank(bankId) {
  return api.delete(`/anomaly/banks/${bankId}`).then(r => r.data);
}

export function updateAnomalyThreshold(bankId, threshold) {
  return api.put(`/anomaly/banks/${bankId}/threshold`, { threshold }).then(r => r.data);
}

export function scoreAnomalyImage(bankId, file, { threshold = null, withHeatmap = true } = {}) {
  const fd = new FormData();
  fd.append('file', file);
  if (threshold != null) fd.append('threshold', String(threshold));
  fd.append('with_heatmap', withHeatmap ? 'true' : 'false');
  return api.post(`/anomaly/banks/${bankId}/score`, fd, { headers: { 'Content-Type': 'multipart/form-data' }, timeout: 120000 }).then(r => r.data);
}

export function scoreAnomalyFrame(bankId, channelId = 0, { threshold = null, withHeatmap = true } = {}) {
  return api.post(`/anomaly/banks/${bankId}/score-frame`, {
    channel_id: channelId, threshold, with_heatmap: withHeatmap,
  }).then(r => r.data);
}

// ---------- VLM 坐诊 (默认关, 本地 OpenAI 兼容端点) ----------
export function getVlmConfig() {
  return api.get('/vlm/config').then(r => r.data);
}

export function saveVlmConfig(patch) {
  return api.put('/vlm/config', patch).then(r => r.data);
}

export function getVlmStatus() {
  return api.get('/vlm/status').then(r => r.data);
}

export function vlmAskImage(file, question) {
  const fd = new FormData();
  fd.append('file', file);
  fd.append('question', question);
  return api.post('/vlm/ask', fd, { headers: { 'Content-Type': 'multipart/form-data' }, timeout: 180000 }).then(r => r.data);
}

export function vlmAskFrame(channelId = 0, question) {
  return api.post('/vlm/ask-frame', { channel_id: channelId, question }, { timeout: 180000 }).then(r => r.data);
}
