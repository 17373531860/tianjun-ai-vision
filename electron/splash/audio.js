// =====================================================================
// 赛博启动屏 · 程序化音效 v0.16（带派版）
// ---------------------------------------------------------------------
// · 主音量拉高 + DynamicsCompressor 限幅
// · 始终在线的「粒子碰撞」调度器（金属 tick / 玻璃 ping / 电火花 crackle）
// v0.17：去掉 ambient 白噪/LFO（沙沙风声来源）；握拳改高频能量嗡鸣；碰撞全振荡器无噪声

const MASTER         = 0.78;   // 主音量
const COLLISION_BASE = 0.55;   // 碰撞响度基准

let ctx = null;
let masterGain = null;
let compressor = null;
let pinchPulseTimer = null;
let pinchPulseRunning = false;
let lastPinchValue = 0;

let enabled = true;
let unlocked = false;

// ---- 粒子碰撞调度器 ----
let collisionRunning = false;
let collisionTimer = null;
let collisionIntensity = 0.55;   // 0.2 ~ 2.5，越高越密越响
let lastStage = -1;

function getCtx() {
  if (!ctx) {
    ctx = new (window.AudioContext || window.webkitAudioContext)();
    compressor = ctx.createDynamicsCompressor();
    compressor.threshold.value = -18;
    compressor.knee.value = 12;
    compressor.ratio.value = 3;
    compressor.attack.value = 0.003;
    compressor.release.value = 0.15;

    masterGain = ctx.createGain();
    masterGain.gain.value = enabled ? MASTER : 0;
    masterGain.connect(compressor);
    compressor.connect(ctx.destination);
  }
  return ctx;
}

export async function ensureAudio() {
  if (!enabled) return;
  const c = getCtx();
  if (c.state === 'suspended') {
    try { await c.resume(); unlocked = true; } catch (_) { /* ignore */ }
  } else {
    unlocked = true;
  }
  if (unlocked) {
    if (!collisionRunning) startParticleCollisions();
    // v0.18：不再播放持续底噪 hum，只靠粒子碰撞声构成环境音
  }
}

export function setAudioEnabled(on) {
  enabled = on;
  if (masterGain) masterGain.gain.value = on ? MASTER : 0;
  if (!on) {
    stopPinchPulses();
    stopParticleCollisions();
  } else if (unlocked) {
    startParticleCollisions();
  }
}

// ---- 工具 ----
function env(gain, t0, peak, attack, decay, sustain, release) {
  const g = gain.gain;
  g.cancelScheduledValues(t0);
  g.setValueAtTime(0.0001, t0);
  g.exponentialRampToValueAtTime(Math.max(peak, 0.0001), t0 + attack);
  g.exponentialRampToValueAtTime(Math.max(sustain, 0.0001), t0 + attack + decay);
  if (release > 0) {
    g.exponentialRampToValueAtTime(0.0001, t0 + attack + decay + release);
  }
}

function connectWithPan(node, panValue) {
  const c = getCtx();
  const pan = c.createStereoPanner();
  pan.pan.value = Math.max(-1, Math.min(1, panValue));
  node.connect(pan);
  pan.connect(masterGain);
  return pan;
}

function playOsc({ type, f0, f1, t, peak, attack, decay, release, detune = 0, pan = 0 }) {
  if (!enabled || !unlocked) return;
  const c = getCtx();
  const t0 = c.currentTime;
  const osc = c.createOscillator();
  const g = c.createGain();
  osc.type = type;
  osc.frequency.setValueAtTime(f0, t0);
  if (f1 != null) osc.frequency.exponentialRampToValueAtTime(Math.max(f1, 20), t0 + t);
  osc.detune.value = detune;
  osc.connect(g);
  connectWithPan(g, pan);
  env(g, t0, peak, attack, decay, peak * 0.25, release);
  osc.start(t0);
  osc.stop(t0 + t + release + 0.08);
}

// =====================================================================
// 粒子碰撞 · 单发声（3 种变体随机）
// =====================================================================
function playParticleCollision(intensity = 1) {
  if (!enabled || !unlocked) return;
  const c = getCtx();
  const t0 = c.currentTime;
  const vol = COLLISION_BASE * intensity * (0.65 + Math.random() * 0.55);
  const pan = (Math.random() - 0.5) * 1.6;
  const roll = Math.random();

  if (roll < 0.50) {
    // A · 金属粒子 tick
    const f = 1800 + Math.random() * 5200;
    playOsc({
      type: 'square', f0: f, f1: f * 0.35,
      t: 0.012 + Math.random() * 0.018,
      peak: vol * 0.55,
      attack: 0.001, decay: 0.008, release: 0.025,
      detune: (Math.random() - 0.5) * 40,
      pan,
    });
    // 谐波尾
    playOsc({
      type: 'sine', f0: f * 2.2, f1: f * 0.8,
      t: 0.02, peak: vol * 0.18,
      attack: 0.001, decay: 0.015, release: 0.03,
      pan: pan * 0.5,
    });

  } else if (roll < 0.85) {
    // B · 玻璃/能量 ping
    const f = 900 + Math.random() * 3800;
    const osc = c.createOscillator();
    const g = c.createGain();
    const bp = c.createBiquadFilter();
    bp.type = 'bandpass';
    bp.frequency.value = f;
    bp.Q.value = 8 + Math.random() * 18;
    osc.type = 'sine';
    osc.frequency.value = f;
    osc.connect(bp);
    bp.connect(g);
    connectWithPan(g, pan);
    env(g, t0, vol * 0.7, 0.001, 0.02, vol * 0.15, 0.06);
    osc.start(t0);
    osc.stop(t0 + 0.12);

  } else {
    // C · 芯片 zap（纯振荡器，不用噪声 → 杜绝沙沙声）
    const f = 2400 + Math.random() * 3600;
    playOsc({
      type: 'square', f0: f, f1: f * 1.4,
      t: 0.008, peak: vol * 0.45,
      attack: 0.001, decay: 0.006, release: 0.018,
      detune: (Math.random() - 0.5) * 25,
      pan,
    });
    playOsc({
      type: 'sine', f0: f * 1.5, f1: f * 0.6,
      t: 0.015, peak: vol * 0.35,
      attack: 0.001, decay: 0.01, release: 0.022,
      pan: pan * 0.6,
    });
  }
}

function scheduleNextCollision() {
  if (!collisionRunning || !enabled || !unlocked) return;
  // 密度：intensity 越高间隔越短；2.0 时约 25~60ms 一发，0.5 时约 80~220ms
  const minMs = Math.max(18, 110 / collisionIntensity);
  const maxMs = Math.max(minMs + 15, 280 / collisionIntensity);
  const delay = minMs + Math.random() * (maxMs - minMs);

  collisionTimer = setTimeout(() => {
    // 高密度时偶尔连发 2~3 个（粒子簇碰撞）
    const burst = collisionIntensity > 1.1 && Math.random() < 0.22
      ? (collisionIntensity > 1.8 ? 3 : 2)
      : 1;
    for (let i = 0; i < burst; i++) {
      setTimeout(() => playParticleCollision(collisionIntensity * (1 - i * 0.12)), i * (8 + Math.random() * 18));
    }
    scheduleNextCollision();
  }, delay);
}

function startParticleCollisions() {
  if (collisionRunning) return;
  collisionRunning = true;
  scheduleNextCollision();
}

function stopParticleCollisions() {
  collisionRunning = false;
  if (collisionTimer) {
    clearTimeout(collisionTimer);
    collisionTimer = null;
  }
}

/** 每帧/阶段更新碰撞密度（从 main.js 传入 stage 0~4） */
export function updateAudioStage(stage, pinch = 0, progress = 0, explosionPower = 0) {
  if (!enabled || !unlocked) return;

  let intensity = 0.55;
  switch (stage) {
    case 0: intensity = 0.55; break;                          // IDLE
    case 1: intensity = 0.70 + pinch * 0.95; break;             // HOVER 握紧越密
    case 2: intensity = 0.95 + progress * 0.85; break;          // COLLAPSE 吸入加速
    case 3: intensity = 1.35 + explosionPower * 1.8; break;     // EXPLOSION 爆发
    case 4: intensity = 0.75; break;                            // READY 矩阵
    default: intensity = 0.55;
  }
  collisionIntensity = Math.min(2.6, Math.max(0.25, intensity));

  // 阶段切换时额外打一小簇碰撞（听觉反馈）
  if (stage !== lastStage) {
    lastStage = stage;
    const burstCount = stage === 3 ? 8 : stage === 2 ? 4 : 2;
    for (let i = 0; i < burstCount; i++) {
      setTimeout(() => playParticleCollision(collisionIntensity * 1.1), i * 25);
    }
  }
}

// =====================================================================
// 舱体底噪：已关闭（v0.18 用户不要 hum；环境音仅保留粒子碰撞调度器）
// =====================================================================
function startAmbient() { /* intentionally silent */ }
function stopAmbient()  { /* intentionally silent */ }

// =====================================================================
// 阶段音效（整体响度 x1.8~2.5）
// =====================================================================
const STAGE_SFX = {
  hover() {
    playOsc({ type: 'sine',     f0: 440,  f1: 1760, t: 0.14, peak: 0.55, attack: 0.005, decay: 0.07, release: 0.18, pan: -0.3 });
    playOsc({ type: 'triangle', f0: 880,  f1: 2640, t: 0.10, peak: 0.28, attack: 0.008, decay: 0.05, release: 0.12, detune: 12, pan: 0.35 });
    playOsc({ type: 'square',   f0: 2200, f1: 4400, t: 0.06, peak: 0.12, attack: 0.002, decay: 0.03, release: 0.08, pan: 0 });
    for (let i = 0; i < 4; i++) setTimeout(() => playParticleCollision(0.9), i * 30);
  },

  idle() {
    playOsc({ type: 'sine', f0: 880, f1: 220, t: 0.28, peak: 0.35, attack: 0.008, decay: 0.1, release: 0.25 });
  },

  collapse() {
    if (!enabled || !unlocked) return;
    // 吸入：纯振荡器下坠（不用噪声，避免风感）
    playOsc({ type: 'sine',     f0: 880, f1: 220, t: 0.45, peak: 0.5,  attack: 0.01,  decay: 0.15, release: 0.3, pan: 0 });
    playOsc({ type: 'triangle', f0: 1760, f1: 440, t: 0.4, peak: 0.35, attack: 0.008, decay: 0.12, release: 0.25, pan: -0.3 });
    playOsc({ type: 'square',   f0: 1320, f1: 330, t: 0.38, peak: 0.22, attack: 0.005, decay: 0.1,  release: 0.2,  pan: 0.3, detune: 8 });
  },

  explosion() {
    if (!enabled || !unlocked) return;

    playOsc({ type: 'sine',     f0: 880,  f1: 110,  t: 0.35, peak: 0.7,  attack: 0.003, decay: 0.12, release: 0.35 });
    playOsc({ type: 'triangle', f0: 1760, f1: 220,  t: 0.28, peak: 0.45, attack: 0.002, decay: 0.08, release: 0.25 });
    playOsc({ type: 'square',   f0: 3520, f1: 440,  t: 0.18, peak: 0.28, attack: 0.001, decay: 0.05, release: 0.15, detune: -12 });

    for (let i = 0; i < 12; i++) {
      setTimeout(() => playParticleCollision(2.0 - i * 0.08), i * 18);
    }
  },

  ready() {
    if (!enabled || !unlocked) return;
    const c = getCtx();
    const t0 = c.currentTime;
    const notes = [523.25, 659.25, 783.99, 1046.5, 1318.5];
    notes.forEach((freq, i) => {
      const osc = c.createOscillator();
      const g = c.createGain();
      osc.type = i % 2 === 0 ? 'sine' : 'square';
      osc.frequency.value = freq;
      const st = t0 + i * 0.048;
      g.gain.setValueAtTime(0.0001, st);
      g.gain.exponentialRampToValueAtTime(0.45 - i * 0.05, st + 0.012);
      g.gain.exponentialRampToValueAtTime(0.0001, st + 0.4);
      osc.connect(g);
      connectWithPan(g, (i - 2) * 0.35);
      osc.start(st);
      osc.stop(st + 0.45);
    });
    playOsc({ type: 'triangle', f0: 130.81, f1: 196, t: 0.8, peak: 0.42, attack: 0.04, decay: 0.25, release: 0.5 });
    for (let i = 0; i < 6; i++) setTimeout(() => playParticleCollision(1.1), i * 40);
  },
};

export function playStageSfx(stageName) {
  if (!enabled || !unlocked) return;
  const fn = STAGE_SFX[stageName];
  if (fn) fn();
}

// ---- 握拳反馈：离散「磁吸脉冲」（非连续嗡鸣） ----
// 中频 300~480Hz 短促 sine 包络，像力场一格一格锁紧；握紧越密越快，不尖锐不闷
function playPinchPulse(pinch) {
  const freq = 300 + pinch * 180;
  playOsc({
    type: 'sine', f0: freq, f1: freq * 0.92,
    t: 0.04, peak: 0.10 + pinch * 0.16,
    attack: 0.012, decay: 0.018, release: 0.03,
    pan: (pinch - 0.5) * 0.3,
  });
}

function schedulePinchPulse() {
  if (!pinchPulseRunning || !enabled || !unlocked || lastPinchValue < 0.06) {
    pinchPulseRunning = false;
    return;
  }
  playPinchPulse(lastPinchValue);
  const interval = 240 - lastPinchValue * 170;   // 240ms → 70ms
  pinchPulseTimer = setTimeout(schedulePinchPulse, interval);
}

function stopPinchPulses() {
  pinchPulseRunning = false;
  lastPinchValue = 0;
  if (pinchPulseTimer) {
    clearTimeout(pinchPulseTimer);
    pinchPulseTimer = null;
  }
}

export function updatePinchHum(pinch, stageIsHover) {
  if (!enabled || !unlocked) return;

  if (!stageIsHover || pinch < 0.06) {
    stopPinchPulses();
    return;
  }

  lastPinchValue = pinch;
  if (!pinchPulseRunning) {
    pinchPulseRunning = true;
    schedulePinchPulse();
  }
}

export function playGlitchSfx() {
  if (!enabled || !unlocked) return;
  playOsc({ type: 'square', f0: 2800, f1: 800, t: 0.07, peak: 0.32, attack: 0.001, decay: 0.02, release: 0.04, pan: (Math.random() - 0.5) });
  playOsc({ type: 'sine',   f0: 4200, f1: 1200, t: 0.05, peak: 0.18, attack: 0.001, decay: 0.015, release: 0.03, pan: (Math.random() - 0.5) * 0.8 });
  for (let i = 0; i < 3; i++) setTimeout(() => playParticleCollision(1.3), i * 20);
}

export function playLoadTickSfx() {
  playOsc({ type: 'sine',   f0: 1400, f1: 2200, t: 0.05, peak: 0.22, attack: 0.001, decay: 0.025, release: 0.04, pan: 0.2 });
  playParticleCollision(0.85);
}

export function playLoadCompleteSfx() {
  if (!enabled || !unlocked) return;
  playOsc({ type: 'sine',     f0: 523.25, f1: 659.25, t: 0.18, peak: 0.55, attack: 0.008, decay: 0.1,  release: 0.25 });
  playOsc({ type: 'triangle', f0: 659.25, f1: 783.99, t: 0.18, peak: 0.42, attack: 0.012, decay: 0.12, release: 0.3,  detune: 8 });
  playOsc({ type: 'square',   f0: 783.99, f1: 1318.5, t: 0.25, peak: 0.38, attack: 0.01,  decay: 0.15, release: 0.4,  detune: -5 });
  for (let i = 0; i < 5; i++) setTimeout(() => playParticleCollision(1.0), i * 35);
}

['pointerdown', 'keydown', 'touchstart'].forEach((ev) => {
  window.addEventListener(ev, () => ensureAudio(), { once: false, passive: true });
});
