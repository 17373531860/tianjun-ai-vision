<template>
  <div class="projection-root">
    <!-- 引导画布 (常驻, 标定态被图案盖住) -->
    <canvas ref="canvasRef" class="guide-canvas"></canvas>

    <!-- 标定态: 全屏 1:1 显示 ArUco 图案 -->
    <img
      v-if="mode === 'calibrate'"
      :src="patternUrl"
      class="pattern-img"
      alt=""
      @load="onPatternLoaded"
    />

    <!-- 标定结果浮层 -->
    <div v-if="calibResult" class="calib-result" :class="calibResult.ok ? 'is-ok' : 'is-err'">
      <div class="calib-result-title">{{ calibResult.ok ? '标定完成' : '标定失败' }}</div>
      <div class="calib-result-body">{{ calibResult.text }}</div>
    </div>

    <!-- 未标定引导菜单 -->
    <div v-if="mode === 'menu'" class="menu-overlay">
      <div class="menu-title">投影光引导 · 工位 {{ channel }}</div>
      <div class="menu-sub">
        {{ calibrated ? `已标定 (误差 ${calibInfo?.reproj_error_px ?? '?'}px)` : '尚未标定：请确认投影仪已对准工作台、相机可看到整个投影区域' }}
      </div>
      <div class="menu-actions">
        <button class="menu-btn primary" @click="startCalibration">
          {{ calibrated ? '重新自动标定 (C)' : '开始自动标定 (C)' }}
        </button>
        <button v-if="calibrated" class="menu-btn" @click="enterGuide">进入引导 (Enter)</button>
        <button class="menu-btn" @click="toggleGrid">对位网格 (G)</button>
      </div>
      <div class="menu-hint">快捷键: C 标定 · G 网格 · Esc 返回菜单 · 双击全屏</div>
    </div>

    <!-- 运行态右下角状态 chip (低亮度, 不抢投影) -->
    <div v-if="mode === 'guide'" class="status-chip">
      工位 {{ channel }} · 误差 {{ calibInfo?.reproj_error_px ?? '?' }}px
    </div>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue';
import { useRoute } from 'vue-router';
import { ackPendingEvent, getDetectionResults } from '@/api/detection';
import {
  calibrationPatternUrl, getAnchorLayout, getLightguideConfig,
  getLightguideParams, interactionSample, LIGHTGUIDE_PARAM_DEFAULTS,
  markerUrl, solveCalibration, verifyCalibration,
} from '@/api/lightguide';
import { resolveStepsToShow } from '@/views/Monitor/monitorModes';
import { GuideEngine } from './guideEngine';

const route = useRoute();
const channel = Number(route.query.channel || 0);

const canvasRef = ref(null);
const mode = ref('menu');           // menu | calibrate | guide
const calibrated = ref(false);
const calibInfo = ref(null);        // 后端标定对象 (homography/cam尺寸/误差)
const patternUrl = ref('');
const calibResult = ref(null);      // {ok, text} 结果浮层

let engine = null;
let pollTimer = null;
let lastEventSeq = 0;
let solving = false;

// P2 自愈标定 + 悬停确认
let driftTimer = null;
let hoverTimer = null;
let confirmCtx = null;   // {rect, camPolygon, baseline, progress, lastTs, acking, shownAt}

// P3: 引导参数 — 出厂默认兜底, onMounted 拉设置页保存值覆盖
const gp = { ...LIGHTGUIDE_PARAM_DEFAULTS };

async function loadGuideParams() {
  try {
    const { data } = await getLightguideParams();
    Object.assign(gp, data.params || {});
  } catch (e) {
    // 拉取失败用出厂默认, 引导照常
  }
  engine?.setState({ brightness: gp.brightness, flowPath: gp.flow_path });
}

// ---------- 坐标变换 ----------

function applyH(H, x, y) {
  const w = H[2][0] * x + H[2][1] * y + H[2][2];
  return [
    (H[0][0] * x + H[0][1] * y + H[0][2]) / w,
    (H[1][0] * x + H[1][1] * y + H[1][2]) / w,
  ];
}

/** 3x3 矩阵求逆 (伴随矩阵法), 投影空间 → 相机空间要用 H⁻¹ */
function invert3(m) {
  const [[a, b, c], [d, e, f], [g, h, i]] = m;
  const A = e * i - f * h, B = c * h - b * i, C = b * f - c * e;
  const det = a * A + d * B + g * C;
  if (!det) return null;
  const k = 1 / det;
  return [
    [A * k, B * k, C * k],
    [(f * g - d * i) * k, (a * i - c * g) * k, (c * d - a * f) * k],
    [(d * h - e * g) * k, (b * g - a * h) * k, (a * e - b * d) * k],
  ];
}

/** 投影空间矩形 → 相机归一化多边形 (悬停采样区) */
function projRectToCamPolygon(rect) {
  const c = calibInfo.value;
  const Hinv = invert3(c.homography);
  if (!Hinv) return null;
  const [x, y, w, h] = rect;
  const corners = [[x, y], [x + w, y], [x + w, y + h], [x, y + h]];
  return corners.map(([px, py]) => {
    const [cx, cy] = applyH(Hinv, px, py);
    return [
      Math.max(0, Math.min(1, cx / c.cam_width)),
      Math.max(0, Math.min(1, cy / c.cam_height)),
    ];
  });
}

/** 归一化多边形 (相机空间) → 投影空间多边形 */
function normPolyToProj(poly) {
  const c = calibInfo.value;
  if (!c) return null;
  return poly.map(([nx, ny]) => applyH(c.homography, nx * c.cam_width, ny * c.cam_height));
}

/** 归一化 bbox {x,y,w,h} → 投影空间四角 */
function normBoxToProj(det) {
  return normPolyToProj([
    [det.x, det.y], [det.x + det.w, det.y],
    [det.x + det.w, det.y + det.h], [det.x, det.y + det.h],
  ]);
}

// ---------- 步骤状态推导 (与 Monitor 同源: resolveStepsToShow) ----------

function deriveGuideState(d) {
  const pc = d.project_config || {};
  const stepsToShow = resolveStepsToShow({
    steps_config: pc.steps_config || [],
    logic_mode: pc.logic_mode || 'sequential',
    pipeline_config: pc.pipeline_config || {},
  });

  // current_cycle_steps 是本周期已出现的 label 列表; 同 label 多次出现按次数消耗
  const seen = {};
  for (const l of d.current_cycle_steps || []) seen[l] = (seen[l] || 0) + 1;
  const used = {};
  let currentIdx = -1;
  const steps = stepsToShow.map((st, i) => {
    const label = st.label || '';
    const display = st.displayLabel || st.display_name || label;
    used[label] = used[label] || 0;
    let status;
    if (used[label] < (seen[label] || 0)) {
      used[label] += 1;
      status = 'done';
    } else if (currentIdx === -1) {
      currentIdx = i;
      status = 'current';
    } else {
      status = 'pending';
    }
    return { label, display, status };
  });

  // 当前步目标几何: 步骤 ROI 优先, 没有 ROI 用实时检测框, 都没有则无目标
  let target = null;
  const liveBoxes = [];
  if (currentIdx >= 0) {
    const cur = stepsToShow[currentIdx];
    const curLabel = cur.label || '';
    const display = steps[currentIdx].display;
    const roi = Array.isArray(cur.roi) && cur.roi.length >= 3 ? cur.roi : null;
    const liveDets = (d.detections || []).filter((det) => det.label === curLabel);
    for (const det of liveDets) {
      const quad = normBoxToProj(det);
      if (quad) liveBoxes.push(quad);
    }
    if (roi) {
      const poly = normPolyToProj(roi.map((p) => [Number(p[0]), Number(p[1])]));
      if (poly) target = { polygon: poly, display, live: false };
    } else if (liveBoxes.length) {
      target = { polygon: liveBoxes[0], display, live: true };
    }
  }

  return { steps, target, liveBoxes };
}

// ---------- 轮询 ----------

async function pollOnce() {
  try {
    const { data: d } = await getDetectionResults(channel);
    if (!engine) return;

    if (!d.is_detecting) {
      stopHoverConfirm();
      engine.setState({ idle: true, steps: [], target: null, liveBoxes: [], message: '' });
      return;
    }
    const gs = deriveGuideState(d);
    engine.setState({
      idle: false,
      steps: gs.steps,
      target: gs.target,
      liveBoxes: gs.liveBoxes,
      message: gs.target ? '' : '当前步骤未配置 ROI 且暂未检出目标',
    });

    // OK/NG 一次性动效: events_log seq 增量 + toast_id 区分
    for (const ev of d.recent_events || []) {
      if ((ev.seq || 0) > lastEventSeq) {
        lastEventSeq = ev.seq;
        if (ev.toast_id === 'ok') engine.flash('ok');
        else if (ev.toast_id === 'ng') engine.flash('ng');
      }
    }

    // 人工确认阻塞态 → 投影悬停按钮
    const pa = d.pending_ack || {};
    if (pa.active && !confirmCtx) startHoverConfirm(pa);
    else if (!pa.active && confirmCtx) stopHoverConfirm();
  } catch (e) {
    // 轮询失败静默, 下个 tick 自愈 (投影窗不弹 Toast)
  }
}

// ---------- P2: 悬停确认 (投影按钮 = 亮度传感器) ----------

function startHoverConfirm(pa) {
  const c = calibInfo.value;
  const label = pa.event_name ? `确认: ${pa.event_name}` : '人工确认';
  const h = 120;
  // 宽度按文案长度算 (中文约占 h*0.3 字号), 防长事件名溢出按钮
  const w = Math.min(640, Math.max(360, 150 + label.length * h * 0.31));
  const rect = [(c.proj_width - w) / 2, c.proj_height * 0.7, w, h];
  const camPolygon = projRectToCamPolygon(rect);
  if (!camPolygon) return;
  confirmCtx = {
    rect, camPolygon, baseline: null, progress: 0,
    lastTs: 0, acking: false, shownAt: Date.now(), label,
  };
  engine?.setState({ confirm: { rect, label: confirmCtx.label, progress: 0 } });
  hoverTimer = setInterval(hoverTick, gp.hover_poll_ms);
}

function stopHoverConfirm() {
  if (hoverTimer) { clearInterval(hoverTimer); hoverTimer = null; }
  confirmCtx = null;
  engine?.setState({ confirm: null });
}

async function hoverTick() {
  const ctx = confirmCtx;
  if (!ctx || ctx.acking) return;
  const now = Date.now();
  if (now - ctx.shownAt < 500) return;   // 等按钮真实投出去再采基线
  try {
    const { data } = await interactionSample(channel, ctx.camPolygon);
    if (ctx !== confirmCtx) return;      // 期间已被关闭
    if (ctx.baseline === null) {
      ctx.baseline = data.mean_gray;
      ctx.lastTs = now;
      return;
    }
    const occluded = Math.abs(data.mean_gray - ctx.baseline) > gp.hover_delta;
    const dt = ctx.lastTs ? now - ctx.lastTs : gp.hover_poll_ms;
    ctx.lastTs = now;
    ctx.progress = occluded
      ? Math.min(1, ctx.progress + dt / gp.hover_dwell_ms)
      : Math.max(0, ctx.progress - (dt * 2) / gp.hover_dwell_ms);
    engine?.setState({
      confirm: { rect: ctx.rect, label: ctx.label, progress: ctx.progress },
    });
    if (ctx.progress >= 1 && !ctx.acking) {
      ctx.acking = true;
      await ackPendingEvent(channel);
      // pending_ack 清除由下个结果轮询确认, 届时 stopHoverConfirm
    }
  } catch (e) {
    // 采样失败静默, 下个 tick 重试
  }
}

// ---------- P2: 漂移自检 (自愈标定) ----------

async function driftCheckOnce() {
  if (mode.value !== 'guide' || solving || !calibrated.value) return;
  try {
    const { data } = await verifyCalibration(channel);
    // 锚点被手/工件遮挡时 found 少 → 不信任本次结果, 防误触发重标
    if (data.anchors_found >= 3 && data.drift_px != null
        && data.drift_px > gp.drift_threshold_px) {
      if (!gp.auto_recalibrate) {
        engine?.setState({
          message: `检测到投影漂移 ${data.drift_px}px, 请重新标定 (自动重标已关闭)`,
        });
        return;
      }
      engine?.setState({
        message: `检测到投影漂移 ${data.drift_px}px, 正在自动重新标定…`,
      });
      await startCalibration();
    }
  } catch (e) {
    // 无画面/未标定等情况静默, 下轮再查
  }
}

function startPolling() {
  stopPolling();
  pollTimer = setInterval(pollOnce, 150);
}

function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}

// ---------- 标定流程 ----------

async function startCalibration() {
  if (solving) return;
  calibResult.value = null;
  stopPolling();
  stopHoverConfirm();
  stopDriftTimer();
  mode.value = 'calibrate';
  patternUrl.value = calibrationPatternUrl({
    projW: window.innerWidth,
    projH: window.innerHeight,
    cols: gp.pattern_cols,
    rows: gp.pattern_rows,
  });
}

async function onPatternLoaded() {
  if (solving) return;
  solving = true;
  // 等投影仪真实打出图案 + 相机曝光稳定 (时长可在设置页调)
  await new Promise((r) => setTimeout(r, gp.calib_settle_ms));
  try {
    const { data } = await solveCalibration({
      channel,
      projW: window.innerWidth,
      projH: window.innerHeight,
      cols: gp.pattern_cols,
      rows: gp.pattern_rows,
    });
    const c = data.calibration;
    calibInfo.value = c;
    calibrated.value = true;
    calibResult.value = {
      ok: true,
      text: `识别 ${c.markers_matched}/${c.markers_total} 标记 · 重投影误差 ${c.reproj_error_px}px (max ${c.reproj_error_max_px}px)`,
    };
    setTimeout(() => {
      calibResult.value = null;
      enterGuide();
    }, 2600);
  } catch (e) {
    const detail = e?.response?.data?.detail;
    calibResult.value = {
      ok: false,
      text: typeof detail === 'string' ? detail : (e?.message || '求解失败'),
    };
    setTimeout(() => {
      calibResult.value = null;
      mode.value = 'menu';
    }, 4000);
  } finally {
    solving = false;
  }
}

async function enterGuide() {
  if (!calibrated.value) return;
  await loadGuideParams(); // 每次进引导重拉参数 (设置页改动 Esc→Enter 即生效)
  mode.value = 'guide';
  engine?.setState({
    projW: calibInfo.value.proj_width,
    projH: calibInfo.value.proj_height,
    showGrid: false,
    idle: true,
  });
  startPolling();
  loadAnchors();
  startDriftTimer();
}

async function loadAnchors() {
  try {
    const { data } = await getAnchorLayout({
      projW: calibInfo.value.proj_width,
      projH: calibInfo.value.proj_height,
    });
    const anchors = (data.markers || []).map((m) => {
      const img = new Image();
      img.crossOrigin = 'anonymous'; // 跨源标记图不污染引导画布
      img.src = markerUrl(m.id, m.tile[2]);
      return { img, tile: m.tile };
    });
    engine?.setState({ anchors });
  } catch (e) {
    // 锚点加载失败只影响自愈校验, 引导本体照常
  }
}

function startDriftTimer() {
  stopDriftTimer();
  driftTimer = setInterval(driftCheckOnce, gp.drift_interval_s * 1000);
}

function stopDriftTimer() {
  if (driftTimer) { clearInterval(driftTimer); driftTimer = null; }
}

function toGridMode() {
  // 对位网格画在引导画布上, 用标定分辨率 (未标定用窗口分辨率)
  engine?.setState({
    projW: calibInfo.value?.proj_width || window.innerWidth,
    projH: calibInfo.value?.proj_height || window.innerHeight,
    showGrid: true,
    idle: false,
    steps: [],
    target: null,
    liveBoxes: [],
    message: '对位网格 — 按 Esc 返回',
  });
}

function toggleGrid() {
  const showing = engine?.state?.showGrid;
  if (showing) backToMenu();
  else { mode.value = 'guide'; stopPolling(); toGridMode(); }
}

function backToMenu() {
  stopPolling();
  stopHoverConfirm();
  stopDriftTimer();
  mode.value = 'menu';
  engine?.setState({ idle: true, showGrid: false, steps: [], target: null, liveBoxes: [], message: '' });
}

// ---------- 键盘/全屏 ----------

function onKeydown(e) {
  if (e.key === 'c' || e.key === 'C') startCalibration();
  else if (e.key === 'g' || e.key === 'G') toggleGrid();
  else if (e.key === 'Escape') backToMenu();
  else if (e.key === 'Enter' && mode.value === 'menu') enterGuide();
}

function onDblClick() {
  if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
  else document.documentElement.requestFullscreen().catch(() => {});
}

// ---------- 生命周期 ----------

onMounted(async () => {
  engine = new GuideEngine(canvasRef.value);
  engine.setState({ projW: window.innerWidth, projH: window.innerHeight, idle: true });
  engine.start();
  window.addEventListener('keydown', onKeydown);
  window.addEventListener('dblclick', onDblClick);

  await loadGuideParams(); // 先拉参数再进引导 (漂移周期/悬停时长依赖它)

  try {
    const { data } = await getLightguideConfig(channel);
    if (data.calibrated && data.calibration?.homography) {
      calibrated.value = true;
      calibInfo.value = data.calibration;
      enterGuide(); // 已标定 → 直接进引导 (开机即用)
    }
  } catch (e) {
    // 后端未就绪时留在菜单, 用户可手动重试
  }
});

onBeforeUnmount(() => {
  stopPolling();
  stopHoverConfirm();
  stopDriftTimer();
  engine?.stop();
  engine = null;
  window.removeEventListener('keydown', onKeydown);
  window.removeEventListener('dblclick', onDblClick);
});
</script>

<style scoped>
.projection-root {
  position: fixed;
  inset: 0;
  background: #000;
  overflow: hidden;
  cursor: none; /* 投影介质不该有鼠标指针 */
}

.projection-root:has(.menu-overlay) {
  cursor: default;
}

.guide-canvas {
  position: absolute;
  inset: 0;
}

.pattern-img {
  position: absolute;
  inset: 0;
  width: 100vw;
  height: 100vh;
  /* 必须 1:1, 任何 object-fit 缩放都会毁掉标定几何 */
  object-fit: fill;
  image-rendering: pixelated;
}

.calib-result {
  position: absolute;
  left: 50%;
  top: 50%;
  transform: translate(-50%, -50%);
  padding: 1.5rem 2.5rem;
  border-radius: 0.75rem;
  background: rgba(4, 20, 28, 0.92);
  text-align: center;
  z-index: 10;
}

.calib-result.is-ok { border: 2px solid #2dbe7c; }
.calib-result.is-err { border: 2px solid #eb3e4b; }

.calib-result-title {
  font-size: 1.5rem;
  font-weight: 600;
  color: #ecf5fa;
  margin-bottom: 0.5rem;
}

.calib-result.is-ok .calib-result-title { color: #2dbe7c; }
.calib-result.is-err .calib-result-title { color: #eb3e4b; }

.calib-result-body {
  font-size: 1rem;
  color: #96b0be;
}

.menu-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 1rem;
  background: radial-gradient(ellipse at center, rgba(7, 22, 32, 0.95), #000 75%);
}

.menu-title {
  font-size: 1.75rem;
  font-weight: 600;
  color: #ecf5fa;
  letter-spacing: 0.15em;
}

.menu-sub {
  font-size: 0.9rem;
  color: #96b0be;
  max-width: 32rem;
  text-align: center;
}

.menu-actions {
  display: flex;
  gap: 0.75rem;
  margin-top: 0.75rem;
}

.menu-btn {
  padding: 0.7rem 1.5rem;
  border-radius: 0.5rem;
  border: 1px solid rgba(0, 210, 255, 0.4);
  background: rgba(6, 44, 58, 0.6);
  color: #ecf5fa;
  font-size: 0.95rem;
  cursor: pointer;
  transition: all 0.15s;
}

.menu-btn:hover {
  border-color: #00d2ff;
  box-shadow: 0 0 1rem rgba(0, 210, 255, 0.35);
}

.menu-btn.primary {
  background: rgba(0, 150, 190, 0.35);
  border-color: #00d2ff;
}

.menu-hint {
  margin-top: 1.5rem;
  font-size: 0.75rem;
  color: rgba(150, 176, 190, 0.6);
}

.status-chip {
  position: absolute;
  right: 0.75rem;
  bottom: 0.75rem;
  font-size: 0.7rem;
  color: rgba(150, 176, 190, 0.4);
  user-select: none;
}
</style>
