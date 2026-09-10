// 投影光引导 API (feat/light-sensor) — 后端前缀 /api/v1/lightguide
import api from './index'

// 工位标定状态 + 当前帧可用性
export function getLightguideConfig(channel = 0) {
  return api.get('/lightguide/config', { params: { channel } })
}

// 清除工位标定
export function deleteLightguideConfig(channel = 0) {
  return api.delete('/lightguide/config', { params: { channel } })
}

// 标定图案 PNG 的 URL (投影窗 <img> 直接引用, 全屏 1:1 显示)
export function calibrationPatternUrl({ projW, projH, cols = 4, rows = 3 }) {
  const base = api.defaults.baseURL.replace(/\/$/, '')
  const q = `proj_w=${projW}&proj_h=${projH}&cols=${cols}&rows=${rows}&_t=${Date.now()}`
  return `${base}/lightguide/calibration/pattern?${q}`
}

// 自动求解 相机→投影 homography (投影窗显示图案稳定后调用)
export function solveCalibration({ channel = 0, projW, projH, cols = 4, rows = 3, grabFrames = 5 }) {
  return api.post('/lightguide/calibration/solve', {
    channel,
    proj_w: projW,
    proj_h: projH,
    cols,
    rows,
    grab_frames: grabFrames,
  })
}

// ---- P2 自愈标定 ----

// 四角自愈锚点布局 (引导画布按它画锚点 tile)
export function getAnchorLayout({ projW, projH }) {
  return api.get('/lightguide/calibration/anchors', {
    params: { proj_w: projW, proj_h: projH },
  })
}

// 单个 ArUco 标记 PNG 的 URL (白底 tile)
export function markerUrl(markerId, size) {
  const base = api.defaults.baseURL.replace(/\/$/, '')
  return `${base}/lightguide/calibration/marker?marker_id=${markerId}&size=${size}`
}

// 漂移校验: 抽帧找锚点, 与标定矩阵推算位置比对
export function verifyCalibration(channel = 0, grabFrames = 2) {
  return api.post('/lightguide/calibration/verify', {
    channel,
    grab_frames: grabFrames,
  })
}

// ---- P3 引导参数 (设置页可调, 全局一份) ----

// 出厂默认 (与后端 _PARAM_SPEC 一致; 设置卡「恢复默认」和投影窗兜底共用)
export const LIGHTGUIDE_PARAM_DEFAULTS = {
  drift_interval_s: 20,
  drift_threshold_px: 6,
  auto_recalibrate: true,
  hover_dwell_ms: 1200,
  hover_delta: 14,
  hover_poll_ms: 250,
  calib_settle_ms: 1500,
  pattern_cols: 4,
  pattern_rows: 3,
  brightness: 1,
  flow_path: true,
}

export function getLightguideParams() {
  return api.get('/lightguide/params')
}

export function setLightguideParams(params) {
  return api.put('/lightguide/params', params)
}

// ---- P2 投影按钮悬停 ----

// 采样相机帧上多边形区域平均亮度 (polygon 为相机归一化坐标)
export function interactionSample(channel, polygon) {
  return api.post('/lightguide/interaction/sample', { channel, polygon })
}
