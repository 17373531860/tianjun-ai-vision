// =====================================================================
// 粒子·幻境 · 天军 AI 视觉检测启动概念稿 v0.3
// ---------------------------------------------------------------------
// v0.2 → v0.3 重大改造（对标抖音"澜安"的「粒子·幻境 v2.1」）：
//   1. 粒子从"漫天星云"改为"球面壳实体"（带厚度）
//   2. 加中心发光内核（billboard sprite + 高斯光晕 shader）
//   3. 接 UnrealBloomPass 后处理（核心辉光感）
//   4. IDLE 改自旋呼吸；HOVER 朝手方向局部鼓起；EXPLOSION 变星空辐射
//   5. HUD 改产品感（左上品牌字 + 按 D 调出 dev panel）
// =====================================================================

import * as THREE from 'three';
import { EffectComposer }  from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass }      from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { AfterimagePass }  from 'three/addons/postprocessing/AfterimagePass.js';
import { OutputPass }      from 'three/addons/postprocessing/OutputPass.js';
import {
  ensureAudio,
  playStageSfx,
  updatePinchHum,
  playGlitchSfx,
  playLoadTickSfx,
  playLoadCompleteSfx,
  setAudioEnabled,
  updateAudioStage,
} from './audio.js';

// =====================================================================
// SPLASH IPC 桥（v3.8.2 集成进 Electron 主进程后才有 window.splashAPI）
// ---------------------------------------------------------------------
// 浏览器里独立打开 prototype 时 splashAPI 不存在，所有调用走静默兜底，
// 保持 prototype 沙盒可直接 file://+server 调试。
// =====================================================================
const splashAPI = (typeof window !== 'undefined' && window.splashAPI) || null;
let backendReadyFromIPC = false;       // 主进程喂的"后端 ready"事件
let backendLogLineCount = 0;            // 累计收到的后端日志行数（用来推进度）
let backendLogQueue    = [];            // 待消费的后端日志（喂给代码瀑布 + bootSequence）
const BACKEND_LOG_BUFFER_MAX = 800;     // 防止 splash 长开内存爆

// =====================================================================
// v0.14 · 特效总开关（一改全改，回滚 C·E·F·I·J 之前的简洁形态）
// ---------------------------------------------------------------------
// 用户反馈：加了 5 件套之后，粒子球本体被覆盖（Fresnel 经纬度网格）、
// COLLAPSE 时变成纯几何黄球（不是粒子）、左右侧栏过于杂乱。
// 全部置 false → 回到 v0.12 之前的"粒子球 + 四角 HUD + 代码瀑布"形态。
// 想恢复某项功能时，单独改对应开关为 true 即可。
// =====================================================================
const FEAT = {
  fresnel:  false,   // F · 边缘辉光球壳
  arcs:     false,   // C · 球面飞线
  shocks:   false,   // C · 圆环冲击波
  glitch3d: false,   // E · 粒子 shader 横向撕裂（DOM 抖动仍保留）
  sidebars: false,   // I + J · 左右两侧装饰侧栏 + 雷达/bar/警示三角
  audio:    true,    // v0.15 · Web Audio 程序化音效
};

// =====================================================================
// PALETTE · 全局调色板（改一处即生效）
// ---------------------------------------------------------------------
// 主基调：深紫 + 冰青，对标 AI 科技产品（OpenAI / Anthropic / Midjourney）
// 想换主题？只改这里的几个 hex，整个画面跟着走。
// =====================================================================
// v0.12：粒子配色赛博化——和 DOM 装饰层的霓虹青/洋红/电黄/酸绿统一
// 之前是柔和紫青冷调，现在拉饱和：洋红 + 电青 双色调粒子球
const PALETTE = {
  bgDeep:        0x02060c,   // 场景背景（赛博近黑深蓝，和 CSS 一致）

  particleCool:  new THREE.Vector3(0.95, 0.18, 0.55),  // 粒子冷端 → 洋红（霓虹）
  particleWarm:  new THREE.Vector3(0.00, 0.88, 1.00),  // 粒子暖端 → 电青（霓虹）
  particleHi:    new THREE.Vector3(0.70, 0.98, 1.00),  // 高光（冰青）

  bgStarLow:     new THREE.Vector3(0.30, 0.05, 0.40),  // 远场流入粒子暗端（深紫红）
  bgStarHi:      new THREE.Vector3(0.00, 0.88, 1.00),  // 远场流入粒子亮端（电青）

  coreIdle:      0x00E5FF,   // 内核 IDLE 色 → 电青
  coreHover:     0xFF2D7E,   // 内核 HOVER 色 → 洋红（手悬停时变色，反馈感更强）
  coreFlash:     0xFFEE00,   // 内核 COLLAPSE/EXPLOSION 极亮态 → 电黄（爆点）

  // FLOW（READY）阶段调色：墨蓝主调 + 5% 电黄少数派
  flowInk:       new THREE.Vector3(0.05, 0.22, 0.65),  // 主体深青蓝
  flowDeep:      new THREE.Vector3(0.02, 0.08, 0.28),  // 根部更深
  flowGold:      new THREE.Vector3(1.00, 0.92, 0.05),  // 电黄少数派
  flowEdge:      new THREE.Vector3(0.00, 0.88, 1.00),  // 流体边缘高光（电青）
};

// =====================================================================
// 0. GPU 等级识别
// =====================================================================
function detectGPUTier() {
  try {
    const c = document.createElement('canvas');
    const gl = c.getContext('webgl2') || c.getContext('webgl');
    if (!gl) return { tier: 'low', name: 'no-webgl' };
    const ext = gl.getExtension('WEBGL_debug_renderer_info');
    if (!ext) return { tier: 'unknown', name: 'masked' };
    const renderer = (gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) || '').toLowerCase();

    if (/rtx\s*50[789]0|rtx\s*5090|rtx\s*4090|rtx\s*4080/.test(renderer))
      return { tier: 'beast', name: renderer };
    if (/rtx\s*50[5-7]0|rtx\s*40[6-7]0|rtx\s*3090|rtx\s*3080/.test(renderer))
      return { tier: 'high', name: renderer };
    if (/rtx\s*30[6-7]0|rtx\s*40[5-6]0|rtx\s*5050/.test(renderer))
      return { tier: 'mid', name: renderer };
    if (/rtx\s*3050|rtx\s*20\d0|gtx\s*16\d0/.test(renderer))
      return { tier: 'low', name: renderer };
    if (/gtx|intel|amd|radeon/.test(renderer))
      return { tier: 'low', name: renderer };
    return { tier: 'unknown', name: renderer };
  } catch (e) {
    return { tier: 'unknown', name: 'error' };
  }
}

// v0.8：再砍半。集显也能稳跑，独显粒子分散开后颗粒感反而更好
const TIER_PARTICLES = {
  beast:   150000,
  high:    100000,
  mid:     65000,
  low:     35000,
  unknown: 50000,
};
const MAX_PARTICLES = 200000;

const gpuInfo = detectGPUTier();
let activeCount = Math.min(TIER_PARTICLES[gpuInfo.tier] || 180000, MAX_PARTICLES);
console.log(`[GPU] ${gpuInfo.tier} · ${gpuInfo.name} → 初始 ${activeCount.toLocaleString()} 粒子`);

// =====================================================================
// 1. 状态机
// =====================================================================
const Stage = {
  IDLE:       0,
  HOVER:      1,
  COLLAPSE:   2,
  EXPLOSION:  3,
  READY:      4,
};
const stageLabel = {
  [Stage.IDLE]:      'IDLE · 自旋',
  [Stage.HOVER]:     'HOVER · 鼓起',
  [Stage.COLLAPSE]:  'COLLAPSE · 塌缩',
  [Stage.EXPLOSION]: 'EXPLOSION · 辐射',
  [Stage.READY]:     'READY · 就绪',
};

let currentStage   = Stage.IDLE;
let stageStartTime = 0;
let stageProgress  = 0;
let explosionPower = 0;

const COLLAPSE_DURATION  = 1.2;
const EXPLOSION_DURATION = 2.4;

// =====================================================================
// 2. 后端 ready 模拟器
// =====================================================================
const FAKE_BACKEND_DELAY = 3800;
let backendReady   = false;
let backendStarted = false;

const events = {
  HAND_DETECTED: () => {
    console.log('[Event → backend] HAND_DETECTED');
    if (backendStarted) return;
    backendStarted = true;
    setBackend('初始化中 · DB / camera / model warmup', 'warn');
    setSub('SYSTEM WARMUP IN PROGRESS');
    setTimeout(() => {
      backendReady = true;
      console.log('[Event ← backend] BACKEND_READY');
      setBackend('就绪 · 等待动画完成', 'ok');
      setSub('READY · AWAITING UNFURL');
    }, FAKE_BACKEND_DELAY);
  },
  STAGE_COLLAPSE: () => {
    console.log('[Event → backend] STAGE_COLLAPSE');
  },
  INITIALIZATION_COMPLETE: () => {
    console.log('[Event → backend] INITIALIZATION_COMPLETE');
    setBackend('完成 · 推送 dashboard', 'ok');
    setSub('UNFURL COMPLETE');
  },
};

// =====================================================================
// 3. Three.js 场景 + 渲染管线
// =====================================================================
const canvasEl = document.getElementById('three');
const scene    = new THREE.Scene();
scene.background = new THREE.Color(PALETTE.bgDeep);

const camera = new THREE.PerspectiveCamera(55, window.innerWidth / window.innerHeight, 0.1, 1000);
camera.position.set(0, 0, 11);

const renderer = new THREE.WebGLRenderer({ canvas: canvasEl, antialias: false, alpha: false });
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5));
renderer.setClearColor(PALETTE.bgDeep, 1);
renderer.outputColorSpace = THREE.SRGBColorSpace;

// ---------- EffectComposer + Afterimage (拖尾) + UnrealBloom ----------
// AfterimagePass：每帧把上一帧 framebuffer 按 damp 系数残留，做出流体拖尾感
// 默认 damp = 0（即不残留），仅在 READY 阶段升高到 0.92 形成"流体蔓延"视觉
const composer = new EffectComposer(renderer);
composer.addPass(new RenderPass(scene, camera));

const afterimagePass = new AfterimagePass(0.0);
composer.addPass(afterimagePass);

const bloomPass = new UnrealBloomPass(
  new THREE.Vector2(window.innerWidth, window.innerHeight),
  0.22,   // strength：v0.7 再砍到 0.22，几乎只是给内核一圈淡光晕
  0.38,   // radius
  0.80,   // threshold：阈值飙到 0.80，普通粒子基本不参与 bloom
);
composer.addPass(bloomPass);
composer.addPass(new OutputPass());

// =====================================================================
// 4. 球面壳粒子
// =====================================================================
const SPHERE_RADIUS = 3.0;
const SHELL_THICK   = 0.55;  // v0.7：壳更厚，粒子在径向上分散开，不再聚成实层

const geometry = new THREE.BufferGeometry();
const origins = new Float32Array(MAX_PARTICLES * 3);
const seeds   = new Float32Array(MAX_PARTICLES * 3);
const indices = new Float32Array(MAX_PARTICLES);

for (let i = 0; i < MAX_PARTICLES; i++) {
  // 球面壳采样：Marsaglia 法生成均匀球面，半径加抖动
  const theta = Math.random() * Math.PI * 2;
  const phi   = Math.acos(2 * Math.random() - 1);
  const r     = SPHERE_RADIUS + (Math.random() - 0.5) * SHELL_THICK;

  origins[i * 3 + 0] = r * Math.sin(phi) * Math.cos(theta);
  origins[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
  origins[i * 3 + 2] = r * Math.cos(phi);

  seeds[i * 3 + 0] = Math.random();
  seeds[i * 3 + 1] = Math.random();
  seeds[i * 3 + 2] = Math.random();
  indices[i] = i;
}

geometry.setAttribute('a_origin', new THREE.BufferAttribute(origins, 3));
geometry.setAttribute('a_seed',   new THREE.BufferAttribute(seeds, 3));
geometry.setAttribute('a_index',  new THREE.BufferAttribute(indices, 1));
geometry.setAttribute('position', new THREE.BufferAttribute(origins, 3));
geometry.setDrawRange(0, activeCount);

// =====================================================================
// 5. 粒子 Shader
// =====================================================================
const vertexShader = /* glsl */`
  uniform float u_time;
  uniform vec2  u_target;
  uniform float u_targetActive;
  uniform float u_stage;
  uniform float u_stageProgress;
  uniform float u_explosionPower;
  uniform float u_aspect;
  uniform float u_activeCount;
  uniform float u_pinchTightness;
  uniform float u_glitchAmt;     // E · 横向撕裂 glitch 强度 0~1

  // ---- 配色（由 JS 端 PALETTE 注入）----
  uniform vec3  u_colCool;   // 球粒子冷端（紫）
  uniform vec3  u_colWarm;   // 球粒子暖端（青）
  uniform vec3  u_colHi;     // 高光（冰青）
  uniform vec3  u_colFlowInk;   // FLOW 主体墨蓝
  uniform vec3  u_colFlowDeep;  // FLOW 流体根部更深
  uniform vec3  u_colFlowGold;  // FLOW 金色少数派
  uniform vec3  u_colFlowEdge;  // FLOW 流体边缘高光

  attribute vec3  a_origin;
  attribute vec3  a_seed;
  attribute float a_index;

  varying float v_intensity;
  varying vec3  v_color;

  // 绕 Y 轴 + 微微绕 X 轴的旋转矩阵
  mat3 rotY(float a) {
    float c = cos(a); float s = sin(a);
    return mat3(c, 0.0, s, 0.0, 1.0, 0.0, -s, 0.0, c);
  }
  mat3 rotX(float a) {
    float c = cos(a); float s = sin(a);
    return mat3(1.0, 0.0, 0.0, 0.0, c, -s, 0.0, s, c);
  }

  // 伪 curl-noise 流场：用 sin/cos 组合一个 divergence 较低的 3D 矢量场
  // 不是数学严格的 curl(noise)，但视觉等同流体场，且非常便宜
  vec3 fakeCurl(vec3 p) {
    return vec3(
      sin(p.y * 0.85 + u_time * 0.32) - cos(p.z * 0.62 + u_time * 0.21),
      sin(p.z * 0.74 + u_time * 0.41) - cos(p.x * 0.58 + u_time * 0.27),
      sin(p.x * 0.66 + u_time * 0.36) - cos(p.y * 0.79 + u_time * 0.19)
    );
  }

  void main() {
    // 超出激活数的粒子，抛到视野外
    if (a_index >= u_activeCount) {
      gl_Position = vec4(10.0, 10.0, 10.0, 1.0);
      v_intensity = 0.0;
      v_color = vec3(0.0);
      gl_PointSize = 0.0;
      return;
    }

    vec3 pos = a_origin;
    vec3 normalDir = normalize(a_origin);

    // ---------- IDLE：球自旋 + 呼吸 + 表面抖动 ----------
    if (u_stage < 0.5) {
      mat3 R = rotY(u_time * 0.12) * rotX(sin(u_time * 0.18) * 0.15);
      pos = R * a_origin;

      // 呼吸（轻微涨缩）
      float breathe = 1.0 + sin(u_time * 0.7) * 0.025;
      pos *= breathe;

      // 每粒子的微小抖动（让球面有"沸腾"感）
      float jitterPhase = u_time * 1.6 + a_seed.x * 6.28;
      pos += normalDir * sin(jitterPhase) * 0.05;
      pos += (a_seed - 0.5) * 0.04;

      // v0.8：再降一档，配合粒子数砍半，总光通量降到舒适区间
      float pulse = 0.18 * sin(u_time * 1.2 + a_seed.x * 12.0 + a_seed.y * 6.28);
      v_intensity = 0.45 + pulse;
      v_color = mix(u_colCool, u_colWarm, a_seed.x);

    // ---------- HOVER：球整体跟手旋转（不再局部变形）----------
    // 设计：手在哪个方向 → 球整体转向那个方向，形态保持
    // 握紧度 (u_pinchTightness) 让球"绷紧"——微微收缩 + 整体提亮，作为塌缩前的视觉预兆
    } else if (u_stage < 1.5) {
      // 基础自旋（与 IDLE 一致，保证手从画面消失时无缝回到 IDLE）
      float autoRotY = u_time * 0.12;
      float autoRotX = sin(u_time * 0.18) * 0.15;

      // 手控旋转：u_targetActive 让旋转幅度从 IDLE→HOVER 平滑生效
      // u_target.x ∈ [-1, 1] → ±0.9 弧度（约 ±51°）
      // u_target.y ∈ [-1, 1] → ±0.6 弧度（约 ±34°）
      float handRotY =  u_target.x * 0.9 * u_targetActive;
      float handRotX = -u_target.y * 0.6 * u_targetActive;

      mat3 R = rotY(autoRotY + handRotY) * rotX(autoRotX + handRotX);
      pos = R * a_origin;

      // 呼吸保留
      float breathe = 1.0 + sin(u_time * 0.7) * 0.025;
      pos *= breathe;

      // 握紧反馈：closure 越高，球整体微微收缩（最大 6%），酝酿塌缩
      float tighten = u_pinchTightness * 0.06;
      pos *= (1.0 - tighten);

      // 表面抖动保留
      vec3 normalDir = normalize(a_origin);
      float jitterPhase = u_time * 1.6 + a_seed.x * 6.28;
      pos += normalDir * sin(jitterPhase) * 0.05;
      pos += (a_seed - 0.5) * 0.04;

      // 亮度：基础与 IDLE 接近，握紧时整体提亮，作为"将要收缩"的反馈
      float pulse = 0.18 * sin(u_time * 1.2 + a_seed.x * 12.0 + a_seed.y * 6.28);
      v_intensity = 0.55 + u_pinchTightness * 0.5 + pulse;

      // 颜色：基础紫青渐变；握紧时整体向高光冰青过渡
      vec3 baseCol = mix(u_colCool, u_colWarm, a_seed.x);
      v_color = mix(baseCol, u_colHi, u_pinchTightness * 0.5);

    // ---------- COLLAPSE：所有粒子向中心塌缩 ----------
    } else if (u_stage < 2.5) {
      float p = clamp(u_stageProgress, 0.0, 1.0);
      float eased = p * p * (3.0 - 2.0 * p);

      mat3 R = rotY(u_time * 0.5);  // 塌缩时加速自旋
      vec3 base = R * a_origin;

      vec3 jitter = (a_seed - 0.5) * (1.0 - eased) * 0.5;
      pos = mix(base, vec3(0.0), eased) + jitter;

      v_intensity = 0.7 + eased * 3.0;
      // 塌缩时由冷暖渐变过渡到高光冰青，不再去到纯白
      v_color = mix(u_colWarm, u_colHi, eased);

    // ---------- EXPLOSION：从奇点向四面辐射成星空 ----------
    } else if (u_stage < 3.5) {
      float power = u_explosionPower;
      vec3 dir = normalize(a_origin + (a_seed - 0.5) * 0.5);
      float speed = 22.0 * (0.7 + a_seed.x * 0.6);
      pos = dir * power * speed;

      // 早期高光冰青，后期衰减回紫青调（星空辐射）
      v_intensity = max(0.05, 1.6 - power * 1.4);
      v_color = mix(
        u_colHi,                                    // 起点：高光冰青
        mix(u_colCool, u_colWarm, a_seed.x),        // 终点：每粒子自己的紫青色
        clamp(power * 1.5, 0.0, 1.0)
      );

    // ---------- READY · 神经矩阵：粒子飞进 16×16×16 立方阵列 ----------
    // v0.14：用户选 B 形态。每个粒子按 a_seed 量化到立方阵列的一个格子，
    // 整体绕 Y 轴自转 + 微摆 X 轴，格子内做小幅抖动（避免方块感僵硬）。
    // 边角格子的粒子亮度衰减 → 立方体看起来有立体景深，而不是平的方框。
    // 爆炸末态 lerp 进矩阵位置，配合 6 秒加载进度条共构"AI 模型初始化"的语义。
    } else {
      // 把 [0,1) 的 seed 量化到 0..15 的网格坐标
      float gx = floor(a_seed.x * 16.0);
      float gy = floor(a_seed.y * 16.0);
      // gz 用 seed 的非线性组合生成第三个独立"种子"（避免 x/y 重复）
      float gz = floor(fract(a_seed.z + a_seed.x * 7.13 + a_seed.y * 13.71) * 16.0);

      // 居中：减 7.5 让阵列以原点为中心；每格间距 0.42（总跨度 ~6.3）
      vec3 gridLocal = vec3(gx - 7.5, gy - 7.5, gz - 7.5) * 0.42;

      // 整体自转 + 微摆：让矩阵在镜头前做缓慢的 3D 旋转，每个面都能看到
      mat3 R = rotY(u_time * 0.18) * rotX(sin(u_time * 0.32) * 0.22);
      vec3 gridPos = R * gridLocal;

      // 单粒在格子内微抖（避免完全静止的"塑料感"）：
      // 用 sin 时变 + seed 错位让相邻格子的粒子不同步
      vec3 jitter = vec3(
        sin(u_time * 1.5 + a_seed.x * 23.7),
        sin(u_time * 1.7 + a_seed.y * 31.3),
        sin(u_time * 1.3 + a_seed.z * 19.1)
      ) * 0.04;
      gridPos += jitter;

      // 起步位置：从爆炸末态 lerp 到矩阵格子
      vec3 explosionEnd = normalize(a_origin + (a_seed - 0.5) * 0.5) * 18.0 * (0.7 + a_seed.x * 0.6);
      float regroup = smoothstep(0.0, 0.6, u_stageProgress);
      pos = mix(explosionEnd, gridPos, regroup);

      // 立体景深：离矩阵中心远的格子整体偏暗，让立方体边角自然"虚化"
      float distToCenter = length(gridLocal);
      float depthFade = 1.0 - smoothstep(2.0, 3.4, distToCenter);

      // 矩阵呼吸：每隔 ~5s 整体涨缩一次的脉冲（像神经网络在思考）
      float pulse = 0.20 * sin(u_time * 0.8 + a_seed.x * 8.0);

      // 起步亮度从余光（0.85）淡入稳态（0.55 × depthFade）
      v_intensity = mix(0.85, (0.50 + pulse) * depthFade, regroup);

      // 颜色：保留紫青混合 + 偏冰青（标记"开机完成"）+ 边角粒子偏冷
      vec3 baseCol = mix(u_colCool, u_colWarm, a_seed.x);
      v_color = mix(baseCol, u_colHi, 0.30 + (1.0 - depthFade) * 0.25);
    }

    // ============ E · 横向撕裂 glitch（按 y 分带横移） ============
    // 触发时 u_glitchAmt 在 ~140ms 内从 1 衰减到 0；按 y 坐标分 12 个
    // 横带，每带用 hash 决定本帧是否撕裂、撕裂方向与幅度。
    // 配合 fragment 端的 RGB 错位（在 v_color 上轻微偏移）形成赛博显示器
    // 信号干扰感。
    if (u_glitchAmt > 0.001) {
      float band = floor(pos.y * 6.0 + 100.0);
      // 每帧用 time 微扰让 hash 在 band 内时变（同一带横移不会"凝固"）
      float hash = fract(sin(band * 12.9898 + floor(u_time * 80.0)) * 43758.5453);
      // 70% 的带不动（hash<0.7），30% 横移
      float shiftMask = step(0.70, hash);
      float dir       = (hash - 0.85) * 6.0;     // 方向 + 幅度（基于 hash）
      pos.x += dir * u_glitchAmt * shiftMask;
      // 同时给颜色加点品红/青错位，模拟 RGB 偏色
      v_color += vec3(0.4, 0.0, 0.6) * shiftMask * u_glitchAmt * 0.5;
    }

    vec4 mvPos = modelViewMatrix * vec4(pos, 1.0);
    gl_Position = projectionMatrix * mvPos;

    // 粒子大小（v0.7 收回到 2.4，配合稀疏分布让单粒清晰）
    float baseSize = 2.4;
    if (u_stage > 2.5 && u_stage < 3.5) baseSize = 3.0;
    float size = baseSize * (320.0 / -mvPos.z) * (0.5 + v_intensity * 0.7);
    gl_PointSize = clamp(size, 0.6, 9.0);
  }
`;

const fragmentShader = /* glsl */`
  varying float v_intensity;
  varying vec3  v_color;

  void main() {
    if (v_intensity < 0.01) discard;
    vec2 uv = gl_PointCoord - 0.5;
    float d = length(uv);
    if (d > 0.5) discard;
    // v0.8：单粒 alpha 与中心高光加成都进一步压低，
    // 避免大量粒子在视觉上叠加成白雾
    float alpha = exp(-d * 7.0) * v_intensity * 0.65;
    vec3 col = v_color + pow(1.0 - d * 2.0, 4.0) * 0.25;
    gl_FragColor = vec4(col, alpha);
  }
`;

const uniforms = {
  u_time:            { value: 0 },
  u_target:          { value: new THREE.Vector2(0, 0) },
  u_targetActive:    { value: 0 },
  u_stage:           { value: Stage.IDLE },
  u_stageProgress:   { value: 0 },
  u_explosionPower:  { value: 0 },
  u_aspect:          { value: window.innerWidth / window.innerHeight },
  u_activeCount:     { value: activeCount },
  u_pinchTightness:  { value: 0 },
  u_glitchAmt:       { value: 0 },   // E · 横向撕裂强度
  u_colCool:         { value: PALETTE.particleCool },
  u_colWarm:         { value: PALETTE.particleWarm },
  u_colHi:           { value: PALETTE.particleHi },

  // v0.9：FLOW（READY）阶段配色与流场强度
  u_colFlowInk:      { value: PALETTE.flowInk },
  u_colFlowDeep:     { value: PALETTE.flowDeep },
  u_colFlowGold:     { value: PALETTE.flowGold },
  u_colFlowEdge:     { value: PALETTE.flowEdge },
};

const material = new THREE.ShaderMaterial({
  uniforms,
  vertexShader,
  fragmentShader,
  transparent: true,
  depthTest: false,
  depthWrite: false,
  blending: THREE.AdditiveBlending,
});

const points = new THREE.Points(geometry, material);
scene.add(points);

// =====================================================================
// 5.5 流入粒子层（"河流"——从远场螺旋流入球壳中心）
// ---------------------------------------------------------------------
// 每颗粒子有自己的：起始方向（a_dir，归一化）、初始相位（a_seed.x）、寿命
// 寿命周期内：远场出生 → 螺旋向球壳中心方向流动 → 接近球壳时淡出 → 回到起点
// COLLAPSE 阶段：流入加速，终点拉到中心（被吸入感）
// EXPLOSION 阶段：所有流入粒子停止流入，从当前位置向外辐射
// =====================================================================
const INFLOW_COUNT = 30000;  // v0.7：80k → 30k，避免中心区域堆叠成白雾
const bgGeometry = new THREE.BufferGeometry();
{
  const bgDir   = new Float32Array(INFLOW_COUNT * 3);   // 流入方向（归一化）
  const bgSeed  = new Float32Array(INFLOW_COUNT * 3);   // 随机相位 / 寿命 / 螺旋强度
  for (let i = 0; i < INFLOW_COUNT; i++) {
    const theta = Math.random() * Math.PI * 2;
    const phi   = Math.acos(2 * Math.random() - 1);
    // 单位方向（每粒子从这个方向"远端"流入中心）
    bgDir[i * 3 + 0] = Math.sin(phi) * Math.cos(theta);
    bgDir[i * 3 + 1] = Math.sin(phi) * Math.sin(theta);
    bgDir[i * 3 + 2] = Math.cos(phi);
    bgSeed[i * 3 + 0] = Math.random();   // 初始相位 0-1
    bgSeed[i * 3 + 1] = Math.random();   // 寿命扰动
    bgSeed[i * 3 + 2] = Math.random();   // 螺旋扰动 / 大小扰动
  }
  // position 用 dir 占位（shader 不直接用 position，但 BufferGeometry 必须有）
  bgGeometry.setAttribute('position', new THREE.BufferAttribute(bgDir.slice(), 3));
  bgGeometry.setAttribute('a_dir',    new THREE.BufferAttribute(bgDir, 3));
  bgGeometry.setAttribute('a_seed',   new THREE.BufferAttribute(bgSeed, 3));
}

// 流入粒子的关键参数（每粒子根据 seed 派生）
// 起点远场半径范围：14 ~ 24
// 终点目标半径（球壳外缘）：约 3.6（略大于 SPHERE_RADIUS=3.0，让粒子在贴近球壳前淡出）
// 寿命范围：6 ~ 11 秒，每粒子不同 → 整体看起来是源源不断的河流
const bgMaterial = new THREE.ShaderMaterial({
  uniforms: {
    u_time:            uniforms.u_time,
    u_explosionPower:  uniforms.u_explosionPower,
    u_stage:           uniforms.u_stage,
    u_stageProgress:   uniforms.u_stageProgress,
    u_starLow:         { value: PALETTE.bgStarLow },
    u_starHi:          { value: PALETTE.bgStarHi },
    u_sphereRadius:    { value: SPHERE_RADIUS },
  },
  vertexShader: /* glsl */`
    uniform float u_time;
    uniform float u_stage;
    uniform float u_stageProgress;
    uniform float u_explosionPower;
    uniform float u_sphereRadius;

    attribute vec3 a_dir;     // 每粒子的固定流入方向（归一化）
    attribute vec3 a_seed;

    varying float v_intensity;

    // ---- 工具：基于流入方向 dir 构造一对正交侧向轴，用于螺旋扰动 ----
    void buildBasis(vec3 dir, out vec3 ax1, out vec3 ax2) {
      vec3 up = abs(dir.y) < 0.95 ? vec3(0.0, 1.0, 0.0) : vec3(1.0, 0.0, 0.0);
      ax1 = normalize(cross(dir, up));
      ax2 = normalize(cross(dir, ax1));
    }

    void main() {
      // ---- 寿命参数 ----
      float life       = 6.5 + a_seed.y * 4.5;        // 6.5 - 11s
      float startR     = 14.0 + a_seed.z * 10.0;       // 远场起点半径 14 - 24
      float endR       = u_sphereRadius + 0.6;         // 终点：球壳外侧（~3.6）

      // ---- 时间 → 当前生命相位 phase ∈ [0, 1] ----
      float phaseRaw = mod(u_time + a_seed.x * life, life) / life;

      // COLLAPSE：流入加速，终点拉到中心（被吸进去的感觉）
      float phase = phaseRaw;
      vec3 targetDir = a_dir;
      float currentEndR = endR;

      if (u_stage > 1.5 && u_stage < 2.5) {
        // COLLAPSE：相位加速 1.8x，终点缩到 0
        phase = clamp(phaseRaw + u_stageProgress * 0.5, 0.0, 1.0);
        currentEndR = mix(endR, 0.1, u_stageProgress);
      }

      // ---- 沿流入方向的距离插值（指数曲线：越靠近中心越快）----
      float t = phase;
      // smoothstep 让两端慢、中间快
      float ease = smoothstep(0.0, 1.0, t);
      // 加点指数感（越接近末端流速越快，像被吸进去）
      ease = mix(ease, 1.0 - pow(1.0 - t, 2.5), 0.55);
      float r = mix(startR, currentEndR, ease);

      // ---- 螺旋扰动：在路径上加一个绕方向轴旋转的横向偏移 ----
      vec3 ax1, ax2;
      buildBasis(targetDir, ax1, ax2);
      float spiralPhase = phase * 6.28 * (1.4 + a_seed.z * 1.2) + a_seed.x * 6.28;
      // v0.7：螺旋半径减小，粒子轨迹更收敛、不在大面积横向铺开
      float spiralRad   = (1.0 - phase) * (0.8 + a_seed.y * 1.0);
      vec3 spiralOffset = (ax1 * cos(spiralPhase) + ax2 * sin(spiralPhase)) * spiralRad;

      vec3 pos = targetDir * r + spiralOffset;

      // ---- 亮度：v0.7 大幅压低，且提前在 0.65 phase 就开始淡出，避免堆中心 ----
      float fadeIn  = smoothstep(0.0, 0.15, phase);
      float fadeOut = 1.0 - smoothstep(0.65, 0.92, phase);
      float twinkle = 0.45 + 0.35 * sin(u_time * 1.6 + a_seed.x * 14.0);
      v_intensity = fadeIn * fadeOut * twinkle * (0.22 + a_seed.y * 0.22);

      // EXPLOSION：流入停止，粒子从当前位置向外辐射（与球壳粒子一起爆）
      if (u_stage > 2.5 && u_stage < 3.5) {
        vec3 outwardDir = normalize(pos + (a_seed - 0.5) * 0.4);
        // 当前位置 → 远处，随 u_explosionPower 增长
        float outR = mix(length(pos), 45.0, u_explosionPower);
        pos = outwardDir * outR;
        // 爆炸时统一变亮一波再衰减
        v_intensity = (0.9 + a_seed.y * 0.6) * (1.4 - u_explosionPower * 1.2);
        v_intensity = max(0.0, v_intensity);
      }
      // v0.13.1：READY 分支已删除——背景粒子继续走上方默认的"汇入流"逻辑，
      // 与 IDLE 一致，呼应用户反馈"其他粒子往中心汇聚最炫"。

      vec4 mvPos = modelViewMatrix * vec4(pos, 1.0);
      gl_Position = projectionMatrix * mvPos;
      // v0.7：单粒子尺寸减小
      gl_PointSize = (1.0 + a_seed.z * 0.8) * (300.0 / -mvPos.z);
    }
  `,
  fragmentShader: /* glsl */`
    uniform vec3 u_starLow;
    uniform vec3 u_starHi;
    varying float v_intensity;
    void main() {
      if (v_intensity < 0.02) discard;
      vec2 uv = gl_PointCoord - 0.5;
      float d = length(uv);
      if (d > 0.5) discard;
      // v0.7：fragment alpha 更柔，避免单粒子在中心累加曝光
      float a = exp(-d * 7.5) * v_intensity * 0.75;
      vec3 col = mix(u_starLow, u_starHi, v_intensity);
      gl_FragColor = vec4(col, a);
    }
  `,
  transparent: true,
  depthTest: false,
  depthWrite: false,
  blending: THREE.AdditiveBlending,
});
const bgPoints = new THREE.Points(bgGeometry, bgMaterial);
scene.add(bgPoints);

// =====================================================================
// 6. 中心发光内核（Billboard Mesh + 自定义高斯光晕 shader）
// ---------------------------------------------------------------------
// 设计：内核应该是"球面壳里隐约可见的小亮点"，而不是占据 C 位的大白球。
// 主视觉留给粒子壳本身，内核只在 COLLAPSE/EXPLOSION 才真正变大。
// =====================================================================
const glowGeometry = new THREE.PlaneGeometry(1.0, 1.0);
const glowMaterial = new THREE.ShaderMaterial({
  uniforms: {
    u_size:      { value: 1.0 },                          // 整体缩放
    u_intensity: { value: 1.0 },                          // 整体强度
    u_color:     { value: new THREE.Color(PALETTE.coreIdle) },
  },
  vertexShader: /* glsl */`
    uniform float u_size;
    varying vec2 vUv;
    void main() {
      vUv = uv;
      vec3 scaled = position * u_size;
      gl_Position = projectionMatrix * modelViewMatrix * vec4(scaled, 1.0);
    }
  `,
  fragmentShader: /* glsl */`
    uniform float u_intensity;
    uniform vec3  u_color;
    varying vec2 vUv;
    void main() {
      float d = length(vUv - 0.5);
      // 极亮中心核
      float core = exp(-d * 22.0);
      // 中段晕
      float mid  = exp(-d * 7.0) * 0.45;
      // 外圈大光晕
      float halo = exp(-d * 2.5) * 0.15;
      float a = (core + mid + halo) * u_intensity;
      vec3 col = mix(u_color, vec3(1.0), core * 0.8);
      gl_FragColor = vec4(col, a);
    }
  `,
  transparent: true,
  depthTest: false,
  depthWrite: false,
  blending: THREE.AdditiveBlending,
});
const glowMesh = new THREE.Mesh(glowGeometry, glowMaterial);
glowMesh.position.set(0, 0, 0);
scene.add(glowMesh);

// 内核颜色随阶段变化的工具
const glowState = {
  size: 1.0,
  intensity: 1.0,
  color: new THREE.Color(0xaaf0ff),
};

// =====================================================================
// 6.5 Fresnel 边缘辉光壳（F · 全息感半透明球壳）
// ---------------------------------------------------------------------
// 在粒子球外面套一层略大半径的透明球（3.15 vs 粒子球 3.0），shader 用
// 经典 Fresnel：fres = pow(1 - dot(normal, viewDir), power)。
// 视线越接近"擦边"的位置，fres 越接近 1，边缘自然形成一圈辉光。
// 配合 additive blending + 双面渲染 → 球前后都透出微微的边缘光环，
// 像数字地球外壳的全息保护层。
// =====================================================================
const fresnelGeometry = new THREE.SphereGeometry(SPHERE_RADIUS * 1.05, 96, 48);
const fresnelMaterial = new THREE.ShaderMaterial({
  uniforms: {
    u_color:     { value: new THREE.Color(PALETTE.coreIdle) },
    u_intensity: { value: 0.6 },
    u_power:     { value: 2.8 },
    u_time:      { value: 0.0 },
  },
  vertexShader: /* glsl */`
    varying vec3 vNormal;
    varying vec3 vViewDir;
    varying vec3 vWorldPos;
    void main() {
      // 把法线和视线方向都转到世界空间，避免 modelMatrix 旋转干扰
      vec4 worldPos4 = modelMatrix * vec4(position, 1.0);
      vWorldPos = worldPos4.xyz;
      vNormal   = normalize(mat3(modelMatrix) * normal);
      vViewDir  = normalize(cameraPosition - worldPos4.xyz);
      gl_Position = projectionMatrix * viewMatrix * worldPos4;
    }
  `,
  fragmentShader: /* glsl */`
    uniform vec3  u_color;
    uniform float u_intensity;
    uniform float u_power;
    uniform float u_time;
    varying vec3 vNormal;
    varying vec3 vViewDir;
    varying vec3 vWorldPos;
    void main() {
      // 经典 fresnel：法线和视线夹角越大（边缘），fres 越亮
      float ndv  = max(0.0, dot(normalize(vNormal), normalize(vViewDir)));
      float fres = pow(1.0 - ndv, u_power);

      // 经线扫描带：用 atan(z,x) 在球面上形成纬度条纹，时变扫过
      float lon = atan(vWorldPos.z, vWorldPos.x);
      float lat = vWorldPos.y;
      float scanBand = smoothstep(0.5, 1.0,
        sin(lat * 12.0 - u_time * 1.6) * 0.5 + 0.5);
      float meridian = smoothstep(0.65, 1.0,
        sin(lon * 24.0) * 0.5 + 0.5) * 0.35;

      vec3 col = u_color * (fres + scanBand * 0.4 + meridian * 0.6);
      float a  = (fres + scanBand * 0.2) * u_intensity;
      gl_FragColor = vec4(col, a);
    }
  `,
  transparent: true,
  side: THREE.DoubleSide,
  depthWrite: false,
  blending: THREE.AdditiveBlending,
});
const fresnelMesh = new THREE.Mesh(fresnelGeometry, fresnelMaterial);
fresnelMesh.position.set(0, 0, 0);
if (FEAT.fresnel) scene.add(fresnelMesh);

// Fresnel 状态：跟阶段绑定的强度/色彩
// v0.13.1：用户反馈"球本体被罩住了"——大幅降低 intensity，把 power 提高，
// 让壳光只在球的擦边处显微微一圈，不再遮挡粒子主体
const fresnelState = {
  intensity: 0.18,
  power: 4.5,
  color: new THREE.Color(PALETTE.coreIdle),
};

// =====================================================================
// 6.6 球面飞线（C · Globe arcs · 沿大圆滑过）
// ---------------------------------------------------------------------
// 8 条弧线沿粒子球壳的大圆轨迹（球面 slerp）滑过，shader 把"光头"位置
// 沿弧线方向跑：head 前面发亮 + 拖尾衰减，head 之后 discard。
// 每条弧线生命周期 1.5-2.5s，跑完后重置端点重新出发 → 永不停歇。
// =====================================================================
const ARC_COUNT = 8;
const ARC_SEGMENTS = 64;
const ARC_COLORS = [
  PALETTE.coreIdle,    // 电青
  PALETTE.coreIdle,
  PALETTE.coreHover,   // 洋红
  PALETTE.coreFlash,   // 电黄
  0x00ff94,            // 酸绿
  PALETTE.coreIdle,
  PALETTE.coreHover,
  PALETTE.coreFlash,
];
const arcs = [];
const _arcVecA = new THREE.Vector3();
const _arcVecB = new THREE.Vector3();

function pickSpherePoint(out) {
  // Marsaglia 球面均匀采样：在 (-1,1)² 内取一点直到 s<1，再投到球面
  let u, v, s;
  do {
    u = Math.random() * 2 - 1;
    v = Math.random() * 2 - 1;
    s = u * u + v * v;
  } while (s >= 1);
  const fac = 2 * Math.sqrt(1 - s);
  out.set(u * fac, v * fac, 1 - 2 * s);
  return out;
}

function rebuildArc(arc, tNow) {
  // 选两个新的球面端点（保证不太近，避免退化成点）
  let omega;
  do {
    pickSpherePoint(_arcVecA);
    pickSpherePoint(_arcVecB);
    omega = Math.acos(Math.max(-1, Math.min(1, _arcVecA.dot(_arcVecB))));
  } while (omega < 0.6);   // 至少 ~34° 弧长才好看

  const sinO = Math.sin(omega);
  const r = SPHERE_RADIUS * 1.08;
  const pos = arc.geom.attributes.position.array;
  for (let j = 0; j < ARC_SEGMENTS; j++) {
    const tt = j / (ARC_SEGMENTS - 1);
    const ka = Math.sin((1 - tt) * omega) / sinO;
    const kb = Math.sin(tt * omega) / sinO;
    pos[j * 3 + 0] = (_arcVecA.x * ka + _arcVecB.x * kb) * r;
    pos[j * 3 + 1] = (_arcVecA.y * ka + _arcVecB.y * kb) * r;
    pos[j * 3 + 2] = (_arcVecA.z * ka + _arcVecB.z * kb) * r;
  }
  arc.geom.attributes.position.needsUpdate = true;
  arc.lifeStart    = tNow;
  arc.lifeDuration = 1.5 + Math.random() * 1.2;
}

for (let i = 0; i < ARC_COUNT; i++) {
  const positions = new Float32Array(ARC_SEGMENTS * 3);
  const ts        = new Float32Array(ARC_SEGMENTS);
  for (let j = 0; j < ARC_SEGMENTS; j++) ts[j] = j / (ARC_SEGMENTS - 1);

  const geom = new THREE.BufferGeometry();
  geom.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  geom.setAttribute('a_t',      new THREE.BufferAttribute(ts, 1));

  const mat = new THREE.ShaderMaterial({
    uniforms: {
      u_head:      { value: -0.2 },     // 当前光头位置 0~1（>1 时为拖尾收尾期）
      u_color:     { value: new THREE.Color(ARC_COLORS[i]) },
      u_intensity: { value: 1.2 },
    },
    vertexShader: /* glsl */`
      attribute float a_t;
      varying float vT;
      void main() {
        vT = a_t;
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
      }
    `,
    fragmentShader: /* glsl */`
      uniform float u_head;
      uniform vec3  u_color;
      uniform float u_intensity;
      varying float vT;
      void main() {
        // 头部之前（vT > head）的弧段还没"画"出来：discard
        float d = u_head - vT;
        if (d < 0.0) discard;
        // 拖尾：距离头部越远越淡，exp 衰减
        float trail = exp(-d * 7.0);
        // 头部本身极亮：在 d~0 处发白
        float head  = smoothstep(0.05, 0.0, d) * 1.3;
        vec3 col = mix(u_color, vec3(1.0), head);
        float a = (trail + head) * u_intensity;
        gl_FragColor = vec4(col * a, a);
      }
    `,
    transparent: true,
    depthWrite: false,
    depthTest: true,
    blending: THREE.AdditiveBlending,
  });

  const line = new THREE.Line(geom, mat);
  const arc  = { line, geom, mat, lifeStart: 0, lifeDuration: 2.0 };
  rebuildArc(arc, -i * 0.4);   // 错峰：每条相差 0.4s 起步
  if (FEAT.arcs) scene.add(line);
  arcs.push(arc);
}

function tickArcs(tSec) {
  const vis = (currentStage !== Stage.EXPLOSION);
  for (const arc of arcs) {
    const dt = tSec - arc.lifeStart;
    const headPos = dt / arc.lifeDuration;
    if (headPos > 1.35) {
      // 跑完拖尾，重置端点继续下一条
      rebuildArc(arc, tSec);
      arc.mat.uniforms.u_head.value = -0.05;
    } else {
      arc.mat.uniforms.u_head.value = headPos;
    }
    // 强度跟阶段：HOVER/COLLAPSE 时亮一点点，IDLE 较柔，EXPLOSION 整体藏起来
    let intensity = 1.0;
    if (currentStage === Stage.IDLE)     intensity = 0.85;
    else if (currentStage === Stage.HOVER)    intensity = 1.25;
    else if (currentStage === Stage.COLLAPSE) intensity = 1.55;
    else if (currentStage === Stage.READY)    intensity = 0.95;
    arc.mat.uniforms.u_intensity.value = intensity;
    arc.line.visible = vis;
  }
}

// =====================================================================
// 6.7 圆环冲击波（C · Shockwave rings · 由内向外扩散）
// ---------------------------------------------------------------------
// 3 个圆环错峰扩散：从粒子球中心向外膨胀，半径 0.2 → 5.0，透明度跟随
// 进度线性衰减。环用 RingGeometry，vertex shader 整体放缩。
// billboard 朝向相机：永远是正圆，不会侧视成椭圆。
// =====================================================================
const SHOCK_COUNT = 3;
const shocks = [];
for (let i = 0; i < SHOCK_COUNT; i++) {
  const geom = new THREE.RingGeometry(0.985, 1.0, 96);
  const mat = new THREE.ShaderMaterial({
    uniforms: {
      u_progress:  { value: -1.0 },
      u_color:     { value: new THREE.Color(i === 1 ? PALETTE.coreHover : PALETTE.coreIdle) },
      u_intensity: { value: 1.0 },
    },
    vertexShader: /* glsl */`
      uniform float u_progress;
      void main() {
        // 半径从 0.2 涨到 5.0
        float scale = 0.2 + max(0.0, u_progress) * 4.8;
        vec3 scaled = position * scale;
        gl_Position = projectionMatrix * modelViewMatrix * vec4(scaled, 1.0);
      }
    `,
    fragmentShader: /* glsl */`
      uniform float u_progress;
      uniform vec3  u_color;
      uniform float u_intensity;
      void main() {
        if (u_progress < 0.0) discard;
        // 透明度：起步亮、扩散过程线性衰减
        float a = (1.0 - u_progress) * u_intensity;
        a = max(0.0, a);
        // 起步阶段瞬间爆点：progress<0.1 时额外加强
        float burst = smoothstep(0.10, 0.0, u_progress) * 0.6;
        a += burst;
        gl_FragColor = vec4(u_color * a * 2.0, a);
      }
    `,
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
    side: THREE.DoubleSide,
  });
  const mesh = new THREE.Mesh(geom, mat);
  mesh.position.set(0, 0, 0);
  if (FEAT.shocks) scene.add(mesh);
  shocks.push({
    mesh, mat,
    phaseStart: -i * 0.9,   // 错峰 0.9s
    duration: 2.7,
  });
}

function tickShocks(tSec) {
  for (const s of shocks) {
    const dt = tSec - s.phaseStart;
    if (dt < 0) {
      s.mat.uniforms.u_progress.value = -1.0;
    } else {
      const prog = (dt % s.duration) / s.duration;
      s.mat.uniforms.u_progress.value = prog;
    }
    s.mesh.lookAt(camera.position);
    s.mesh.visible = (currentStage !== Stage.EXPLOSION);
  }
}

// =====================================================================
// 7. 输入：鼠标 + MediaPipe Hands（同 v0.2，时间戳改进保留）
// =====================================================================
const input = {
  target: new THREE.Vector2(0, 0),
  active: false,
  pinch:  0,
  source: 'none',
};

const handState = {
  pinchStartTs: null,
  history: [],
};

// v0.4：从"拇指食指距离"改为"手掌开合度（closure）"
// closure ≈ 1 → 握拳；closure ≈ 0 → 五指张开
// 实现：5 个指尖到手腕的平均距离，除以手掌宽度（食指根 5 → 小指根 17）归一化
const FIST_TIGHT_THRESHOLD = 0.65;   // closure > 此值算"握紧"
const FIST_LOOSE_THRESHOLD = 0.30;   // closure < 此值算"完全张开"
const COLLAPSE_HOLD_TIME   = 300;

const mouseState = { x: 0, y: 0, inside: false, down: false };

window.addEventListener('mousemove', (e) => {
  mouseState.x = (e.clientX / window.innerWidth) * 2 - 1;
  mouseState.y = -(e.clientY / window.innerHeight) * 2 + 1;
  mouseState.inside = true;
});
window.addEventListener('mouseleave', () => { mouseState.inside = false; });
window.addEventListener('mousedown',  () => { mouseState.down = true;  });
window.addEventListener('mouseup',    () => { mouseState.down = false; });

let handsReady = false;
let cameraReady = false;
let lastHandSeenTime = 0;
const HAND_LOST_TIMEOUT = 500;

async function initHands() {
  if (typeof window.Hands === 'undefined' || typeof window.Camera === 'undefined') {
    console.warn('[Hands] MediaPipe 未加载，鼠标 fallback');
    setInput('鼠标（手势库未加载）', 'warn');
    input.source = 'mouse';
    return false;
  }

  const videoEl = document.getElementById('webcam');

  // ─────────────────────────────────────────────────────────────────
  // 摄像头选择策略 (v3.9.x): 三档可配置, 落盘 workstation_config.json 顶层 splash 字段.
  // ─────────────────────────────────────────────────────────────────
  // 客户痛点: 工厂工控机经常装了 Todesk / 向日葵等远程控制软件, 它们会注册"虚拟摄像头",
  // 老逻辑只看 ch1 USB 绑定 → 没绑就 facingMode:user → 浏览器随机挑一个 → 经常拿到虚拟相机.
  //
  // 配置来源 — 主前端「设置 → 启动动画手势相机」
  //   cfg.splash = {
  //     camera_mode: 'auto'     - 老逻辑 (优先 ch1.usb_device_id, 否则 facingMode:user)
  //                  'specific' - 锁死指定 device_id, 拿不到走"无摄像头"兜底
  //                  'disabled' - 不开摄像头, splash 直接进自动播放
  //     device_id:    '...'     - specific 模式锁的 deviceId
  //     device_label: '...'     - 仅回显, splash 不读
  //   }
  // ─────────────────────────────────────────────────────────────────
  let cameraMode = 'auto';
  let preferredDeviceId = '';
  if (splashAPI && typeof splashAPI.getWorkstationConfig === 'function') {
    try {
      const cfg = await splashAPI.getWorkstationConfig();
      // 顶层 splash 段 (v3.9.x+)
      const splashCfg = cfg && cfg.splash;
      if (splashCfg && typeof splashCfg.camera_mode === 'string') {
        cameraMode = splashCfg.camera_mode;
        if (cameraMode === 'specific' && splashCfg.device_id) {
          preferredDeviceId = String(splashCfg.device_id);
          console.log(`[Camera] 模式=specific, 锁定 deviceId: ${preferredDeviceId.slice(0, 16)}...`);
        } else {
          console.log(`[Camera] 模式=${cameraMode}`);
        }
      }
      // 老路径 (v3.8.2): 当 mode=auto 且 splash 段没明确 device_id 时, 沿用工位 1 USB 绑定
      if (cameraMode === 'auto' && !preferredDeviceId) {
        const ch1 = cfg && cfg.channels && cfg.channels['1'];
        if (ch1 && ch1.usb_device_id) {
          preferredDeviceId = String(ch1.usb_device_id);
          console.log(`[Camera] auto 模式, 沿用工位 1 deviceId: ${preferredDeviceId.slice(0, 16)}...`);
        }
      }
    } catch (e) {
      console.warn('[Camera] 读取工位配置失败，降级到系统默认:', e.message);
    }
  }

  // disabled: 用户主动关闭手势, 直接走自动播放兜底, 不打扰任何摄像头授权弹窗
  if (cameraMode === 'disabled') {
    console.log('[Camera] 用户关闭手势, 跳过 getUserMedia, 进入自动播放');
    setInput('已关闭手势（自动播放）', 'dim');
    input.source = 'mouse';
    scheduleAutoplayFallback();
    return false;
  }

  try {
    // specific 模式: exact 强制锁定; auto 模式: ideal 软锁定 (拿不到会回退到其他设备).
    // 老逻辑统一用 ideal — 在 auto 模式下行为不变; specific 模式必须用 exact, 否则
    // 浏览器找不到指定相机时会偷偷换一台 (经常就换到了 todesk 虚拟相机), 违背用户意图.
    let videoConstraint;
    if (cameraMode === 'specific' && preferredDeviceId) {
      videoConstraint = { deviceId: { exact: preferredDeviceId }, width: 640, height: 480 };
    } else if (preferredDeviceId) {
      videoConstraint = { deviceId: { ideal: preferredDeviceId }, width: 640, height: 480 };
    } else {
      videoConstraint = { width: 640, height: 480, facingMode: 'user' };
    }
    const stream = await navigator.mediaDevices.getUserMedia({ video: videoConstraint });
    videoEl.srcObject = stream;
    await videoEl.play();
    cameraReady = true;
    document.getElementById('webcamWrap').classList.add('show');
  } catch (e) {
    console.warn('[Camera] 获取摄像头失败，自动播放兜底:', e.message);
    setInput(cameraMode === 'specific' ? '指定相机不可用（自动播放）' : '无摄像头（自动播放）', 'warn');
    input.source = 'mouse';
    scheduleAutoplayFallback();   // 没摄像头：等后端 ready 后自动塌缩+爆炸
    return false;
  }

  const hands = new window.Hands({
    // 本地化的 MediaPipe 资源（v3.8.2 离线机器也能跑）
    locateFile: (file) => `./vendor/mediapipe/${file}`,
  });
  hands.setOptions({
    maxNumHands: 1,
    modelComplexity: 0,
    minDetectionConfidence: 0.6,
    minTrackingConfidence: 0.5,
    selfieMode: true,
  });
  hands.onResults(onHandResults);

  const mpCamera = new window.Camera(videoEl, {
    onFrame: async () => {
      if (handsReady) await hands.send({ image: videoEl });
    },
    width: 640,
    height: 480,
  });
  mpCamera.start();
  handsReady = true;
  input.source = 'hand';
  setInput('摄像头 · MediaPipe', 'ok');
  console.log('[Hands] 启动成功');
  return true;
}

// ---- 计算"手掌开合度" closure ∈ [0, 1]
// 5 个指尖（4/8/12/16/20）到手腕（0）的平均距离 / 手掌宽度（5→17）
// 这样握拳/张开都能稳定区分，且对手到摄像头的距离免疫
function computeFistClosure(lm) {
  const wrist = lm[0];
  const dxP = lm[5].x - lm[17].x;
  const dyP = lm[5].y - lm[17].y;
  const palmW = Math.sqrt(dxP * dxP + dyP * dyP);
  if (palmW < 0.005) return 0;

  const tipIds = [4, 8, 12, 16, 20];
  let sumRatio = 0;
  for (const id of tipIds) {
    const dx = lm[id].x - wrist.x;
    const dy = lm[id].y - wrist.y;
    sumRatio += Math.sqrt(dx * dx + dy * dy) / palmW;
  }
  const avgRatio = sumRatio / 5;  // 经验值：握拳 ~1.0-1.3，张开 ~2.2-2.6
  // 把 ratio 反向映射到 0..1（closure 越大越握紧）
  const closure = Math.max(0, Math.min(1, (2.1 - avgRatio) / (2.1 - 1.0)));
  return closure;
}

function onHandResults(results) {
  const hudCanvas = document.getElementById('handCanvas');
  if (hudCanvas) drawHandOverlay(hudCanvas, results);

  if (!results.multiHandLandmarks || results.multiHandLandmarks.length === 0) return;
  lastHandSeenTime = performance.now();

  const lm = results.multiHandLandmarks[0];

  // 中心点改用手腕到中指根（landmark 9）的中点，作为"掌心"，
  // 比拇指食指中点稳定得多（握拳张开时都能拿到稳定的"手心位置"）
  const palmCenter = lm[9];
  input.target.x = -(palmCenter.x * 2 - 1);
  input.target.y = -(palmCenter.y * 2 - 1);

  // 握紧度
  const closure = computeFistClosure(lm);
  input.pinch = closure;     // 复用字段名 pinch，但语义已变成 closure
  input.active = true;

  const now = performance.now();
  handState.history.push({ ts: now, closure });
  while (handState.history.length > 12) handState.history.shift();

  // 持续握紧检测：closure 高于 FIST_TIGHT_THRESHOLD 才计时
  if (closure > FIST_TIGHT_THRESHOLD) {
    if (handState.pinchStartTs === null) handState.pinchStartTs = now;
  } else {
    handState.pinchStartTs = null;
  }

  const tag = closure > FIST_TIGHT_THRESHOLD ? '握拳'
            : closure < FIST_LOOSE_THRESHOLD ? '张开'
            : '半张';
  setHud('hudHand', `识别 · ${tag} closure=${closure.toFixed(2)}`, 'ok');
  setHud('hudPinch', `closure ${closure.toFixed(2)}`);
}

function drawHandOverlay(canvas, results) {
  const w = canvas.clientWidth;
  const h = canvas.clientHeight;
  if (canvas.width !== w) canvas.width = w;
  if (canvas.height !== h) canvas.height = h;
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, w, h);
  if (!results.multiHandLandmarks || results.multiHandLandmarks.length === 0) return;
  const lm = results.multiHandLandmarks[0];
  const thumb = lm[4], index = lm[8];
  ctx.strokeStyle = '#aedcff';
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(thumb.x * w, thumb.y * h);
  ctx.lineTo(index.x * w, index.y * h);
  ctx.stroke();
  for (const pt of [thumb, index]) {
    ctx.fillStyle = '#80ffcc';
    ctx.beginPath();
    ctx.arc(pt.x * w, pt.y * h, 4, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.fillStyle = 'rgba(120,180,220,0.5)';
  for (let i = 0; i < lm.length; i++) {
    if (i === 4 || i === 8) continue;
    const p = lm[i];
    ctx.beginPath();
    ctx.arc(p.x * w, p.y * h, 1.8, 0, Math.PI * 2);
    ctx.fill();
  }
}

function updateInput() {
  const handStillActive =
    cameraReady && (performance.now() - lastHandSeenTime) < HAND_LOST_TIMEOUT;

  if (handStillActive) {
    input.source = 'hand';
    input.active = true;
  } else if (mouseState.inside) {
    input.source = 'mouse';
    input.target.x = mouseState.x;
    input.target.y = mouseState.y;
    input.pinch = mouseState.down ? 1.0 : 0.0;
    input.active = true;
    setHud('hudHand', '未识别（鼠标接管）', 'dim');
  } else {
    input.source = 'none';
    input.active = false;
    if (cameraReady) setHud('hudHand', '未识别', 'dim');
  }
}

// =====================================================================
// 8. 输入 → 状态机
// =====================================================================
function driveStateMachineByInput() {
  const now = performance.now();

  if (currentStage === Stage.IDLE && input.active) {
    transitionTo(Stage.HOVER);
    events.HAND_DETECTED();
  }

  if (currentStage === Stage.HOVER && !input.active) {
    transitionTo(Stage.IDLE);
  }

  if (currentStage === Stage.HOVER) {
    let shouldCollapse = false;
    if (input.source === 'hand' && handState.pinchStartTs !== null) {
      if (now - handState.pinchStartTs > COLLAPSE_HOLD_TIME) shouldCollapse = true;
    } else if (input.source === 'mouse' && mouseState.down) {
      shouldCollapse = true;
    }
    if (shouldCollapse) {
      transitionTo(Stage.COLLAPSE);
      events.STAGE_COLLAPSE();
    }
  }

  if (currentStage === Stage.COLLAPSE) {
    let explodeReason = null;
    if (input.source === 'hand') {
      // 突变：250ms 内 closure 从 > 0.7 跌到 < 0.25（握拳猛地张开）
      const recent = handState.history.slice(-8);
      if (recent.length >= 2) {
        const oldest = recent[0];
        const latest = recent[recent.length - 1];
        const dt = latest.ts - oldest.ts;
        if (dt < 300 && oldest.closure > 0.70 && latest.closure < 0.25) {
          explodeReason = `突变·dt=${dt.toFixed(0)}ms·${oldest.closure.toFixed(2)}→${latest.closure.toFixed(2)}`;
        }
      }
      if (!explodeReason && performance.now() - lastHandSeenTime > 800) {
        explodeReason = '手离开 >800ms';
      }
    } else if (input.source === 'mouse') {
      if (!mouseState.down) explodeReason = '鼠标松开';
    }
    if (!explodeReason && now / 1000 - stageStartTime > COLLAPSE_DURATION + 2.8) {
      explodeReason = `保底超时·${(now/1000 - stageStartTime).toFixed(1)}s`;
    }
    if (explodeReason) {
      console.log(`[Stage] COLLAPSE → EXPLOSION · 原因: ${explodeReason}`);
      transitionTo(Stage.EXPLOSION);
    }
  }
}

// =====================================================================
// 9. 状态切换
// =====================================================================
function transitionTo(next) {
  currentStage   = next;
  stageStartTime = performance.now() / 1000;
  stageProgress  = 0;
  if (next === Stage.EXPLOSION) explosionPower = 0;
  setHud('hudStage', stageLabel[next]);

  // Fresnel 壳跟阶段切换：保留"颜色随阶段切换"的机制（用户喜欢这个），
  // 但整体强度大幅降低 → 壳只在球边缘留一圈微光，不再罩住粒子本体
  if (next === Stage.IDLE) {
    fresnelState.intensity = 0.18;
    fresnelState.power     = 4.5;
    fresnelState.color.set(PALETTE.coreIdle);   // 电青
  } else if (next === Stage.HOVER) {
    fresnelState.intensity = 0.30;              // 略亮（强化"手悬停反馈"），但仍是边缘光
    fresnelState.power     = 4.0;
    fresnelState.color.set(PALETTE.coreHover);  // 洋红
  } else if (next === Stage.COLLAPSE) {
    fresnelState.intensity = 0.55;              // 塌缩期允许稍亮，配合"绷紧"感
    fresnelState.power     = 3.2;
    fresnelState.color.set(PALETTE.coreFlash);  // 电黄
  } else if (next === Stage.EXPLOSION) {
    fresnelState.intensity = 0.0;               // 爆炸期关闭壳
  } else if (next === Stage.READY) {
    fresnelState.intensity = 0.15;              // READY 比 IDLE 还淡一点，干净
    fresnelState.power     = 5.0;
    fresnelState.color.set(PALETTE.coreIdle);
  }

  if (next === Stage.READY) {
    // 切 body class：CSS 驱动 #brandReveal、代码瀑布、拓扑图入场
    document.body.classList.add('stage-ready');
    afterimagePass.uniforms['damp'].value = 0.0;
    // v0.14：矩阵形态——粒子数压到 6400（16³=4096 格的 1.5 倍，每格 ~1-2 粒子）
    // 之前在 IDLE 是 ~12 万粒子全开，放到立方阵列里会过曝把矩阵糊成实心方块
    activeCount = 6400;
    uniforms.u_activeCount.value = activeCount;
    geometry.setDrawRange(0, activeCount);
    events.INITIALIZATION_COMPLETE();
    // 启动 ~6s 加载进度条 + 满后自动跳转主界面
    startReadyLoading();
  }
  // Backspace 回 IDLE 时把 trail 关掉
  if (next === Stage.IDLE) {
    afterimagePass.uniforms['damp'].value = 0.0;
  }

  // v0.15 · 阶段切换音效
  if (FEAT.audio) {
    const sfxMap = {
      [Stage.IDLE]:      'idle',
      [Stage.HOVER]:     'hover',
      [Stage.COLLAPSE]:  'collapse',
      [Stage.EXPLOSION]: 'explosion',
      [Stage.READY]:     'ready',
    };
    const key = sfxMap[next];
    if (key) playStageSfx(key);
  }
}

// =====================================================================
// 10. FPS 反馈自适应
// =====================================================================
let frames = 0, lastFpsTime = performance.now(), fps = 60;
const fpsHistory = [];
let lastTierAdjustTime = performance.now();

function adjustParticlesByFps() {
  const now = performance.now();
  if (now - lastTierAdjustTime < 1000) return;
  lastTierAdjustTime = now;

  if (fpsHistory.length < 3) return;
  const avg = fpsHistory.reduce((a, b) => a + b, 0) / fpsHistory.length;

  if (currentStage >= Stage.EXPLOSION) return;

  if (avg < 45 && activeCount > 60000) {
    activeCount = Math.max(60000, Math.floor(activeCount * 0.75));
    uniforms.u_activeCount.value = activeCount;
    geometry.setDrawRange(0, activeCount);
    setHud('hudCount', activeCount.toLocaleString() + ' ⬇', 'warn');
    console.log(`[Adaptive] FPS ${avg.toFixed(0)} 低 → ${activeCount.toLocaleString()}`);
  } else if (avg > 58 && activeCount < MAX_PARTICLES) {
    const next = Math.min(MAX_PARTICLES, Math.floor(activeCount * 1.15));
    if (next > activeCount + 10000) {
      activeCount = next;
      uniforms.u_activeCount.value = activeCount;
      geometry.setDrawRange(0, activeCount);
      setHud('hudCount', activeCount.toLocaleString() + ' ⬆', 'ok');
      console.log(`[Adaptive] FPS ${avg.toFixed(0)} 充裕 → ${activeCount.toLocaleString()}`);
    }
  } else {
    setHud('hudCount', activeCount.toLocaleString());
  }
}

// =====================================================================
// 11. Resize
// =====================================================================
window.addEventListener('resize', () => {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
  composer.setSize(window.innerWidth, window.innerHeight);
  bloomPass.setSize(window.innerWidth, window.innerHeight);
  uniforms.u_aspect.value = window.innerWidth / window.innerHeight;
});

// v0.12.1：按钮已删除，由 startReadyLoading() 6s 后自动触发跳转

// ─────────────────────────────────────────────────────────────────────
// 跳过手势 → 直接进 READY 的统一入口 (v3.9.x)
// ─────────────────────────────────────────────────────────────────────
// 触发源:
//   - 键盘 ESC      (v3.8.2 起)
//   - 鼠标点击屏幕   (v3.9.x 起 — 客户工厂工控机经常没键盘 / 没人知道按 ESC)
// 守门: 仅 IDLE / HOVER 阶段响应; COLLAPSE / EXPLOSION / READY 期间忽略,
// 避免动画播到一半被二次触发打断 (状态机不接受倒退).
function skipToReady(reason) {
  if (currentStage !== Stage.IDLE && currentStage !== Stage.HOVER) return;
  console.log(`[Splash] ${reason} 触发, 跳过手势直接进 READY`);
  // v3.9.1a hotfix: 跳过手势路径必须自己把"虚拟后端 ready 信号"打开,
  // 否则 EXPLOSION 末态 (stageProgress=1.0) 会卡死等不到 backendReady=true,
  // 永远进不去 Stage.READY → splash 永远关不掉。
  // 历史: v3.8.2 ESC 跳过 + v3.9.1 鼠标/触摸跳过 都遗漏了这一步,
  //       客户工厂触摸屏机器随手一摸就复现 → 修。
  backendReady = true;
  transitionTo(Stage.COLLAPSE);
  setTimeout(() => {
    if (currentStage === Stage.COLLAPSE) transitionTo(Stage.EXPLOSION);
  }, 1200);
}

// 调试 + 跳过快捷键
window.addEventListener('keydown', (e) => {
  // D: 切换 dev panel（隐藏面板，按 D 调出来看 FPS / 粒子数 / GPU 信息）
  if (e.key === 'd' || e.key === 'D') {
    document.getElementById('devPanel').classList.toggle('show');
  }
  // R: 强制触发 READY（开发期快速预览 logo reveal）
  if (e.key === 'r' || e.key === 'R') {
    backendReady = true;
    transitionTo(Stage.READY);
  }
  // Backspace: 复位回 IDLE
  if (e.key === 'Backspace') {
    document.body.classList.remove('stage-ready');
    transitionTo(Stage.IDLE);
  }
  // ESC (v3.8.2): 跳过手势 — 给"没有耐心做手势"或"摄像头死了"的场景兜底
  if (e.key === 'Escape') {
    skipToReady('ESC');
  }
});

// v3.9.x: 鼠标点击空白区域跳过手势 → 触发塌缩 + 爆炸 → 加载动画
// 守门:
//   1) 已交互元素 (audioToggle 等 <button>) 的 click 通过 closest('button,a,input') 排除,
//      避免按音效按钮也被当成"跳过"。
//   2) skipToReady 内部再守 IDLE/HOVER 阶段, 不会重复触发或打断动画。
window.addEventListener('click', (e) => {
  const t = e.target;
  if (t && typeof t.closest === 'function' && t.closest('button, a, input, select, textarea')) {
    return;  // 点中已有 UI 按钮 — 不当作跳过手势
  }
  skipToReady('CLICK');
});

// v0.15 · 音效开关按钮
let audioOn = true;
const audioBtn = document.getElementById('audioToggle');
if (audioBtn && FEAT.audio) {
  audioBtn.addEventListener('click', async () => {
    if (!audioOn) {
      audioOn = true;
      setAudioEnabled(true);
      await ensureAudio();
      playStageSfx('hover');
      audioBtn.textContent = '♪ AUDIO ON';
      audioBtn.classList.remove('muted');
    } else {
      audioOn = false;
      setAudioEnabled(false);
      audioBtn.textContent = '♪ AUDIO OFF';
      audioBtn.classList.add('muted');
    }
  });
} else if (audioBtn) {
  audioBtn.style.display = 'none';
}

// =====================================================================
// 12. 主循环
// =====================================================================
const clock = new THREE.Clock();

function loop() {
  requestAnimationFrame(loop);
  const elapsed = clock.getElapsedTime();
  uniforms.u_time.value = elapsed;

  updateInput();
  driveStateMachineByInput();

  // 输入平滑
  const tgt = uniforms.u_target.value;
  tgt.x += (input.target.x - tgt.x) * 0.18;
  tgt.y += (input.target.y - tgt.y) * 0.18;
  uniforms.u_targetActive.value +=
    ((input.active ? 1 : 0) - uniforms.u_targetActive.value) * 0.08;
  uniforms.u_pinchTightness.value +=
    (input.pinch - uniforms.u_pinchTightness.value) * 0.2;

  // v0.15 · HOVER 握紧 tension 嗡鸣
  if (FEAT.audio) {
    if (input.active) ensureAudio();
    updatePinchHum(uniforms.u_pinchTightness.value, currentStage === Stage.HOVER);
    updateAudioStage(currentStage, uniforms.u_pinchTightness.value, stageProgress, explosionPower);
  }

  // 阶段进度
  const now = performance.now() / 1000;
  if (currentStage === Stage.COLLAPSE) {
    stageProgress = Math.min(1, (now - stageStartTime) / COLLAPSE_DURATION);
  } else if (currentStage === Stage.EXPLOSION) {
    stageProgress = Math.min(1, (now - stageStartTime) / EXPLOSION_DURATION);
    explosionPower = Math.pow(stageProgress, 0.75);
    if (stageProgress >= 1.0 && backendReady) {
      transitionTo(Stage.READY);
    }
  } else if (currentStage === Stage.READY) {
    // v0.13.1：READY 期推进 stageProgress 用于"粒子从爆炸末态归位"的 lerp
    // 1.4s 完成归位（前 0.84s 跑完 regroup 曲线，配合 stagger 入场的 logo 动画）
    const READY_REGROUP_DURATION = 1.4;
    stageProgress = Math.min(1, (now - stageStartTime) / READY_REGROUP_DURATION);
  }

  uniforms.u_stage.value          = currentStage;
  uniforms.u_stageProgress.value  = stageProgress;
  uniforms.u_explosionPower.value = explosionPower;

  // 中心内核同步状态
  updateGlow(elapsed);

  // C · 球面飞线 + 圆环冲击波每帧推进
  tickArcs(elapsed);
  tickShocks(elapsed);

  // 渲染（经过 EffectComposer 加 bloom）
  composer.render();

  // FPS
  frames++;
  if (performance.now() - lastFpsTime > 500) {
    fps = Math.round(frames / ((performance.now() - lastFpsTime) / 1000));
    setHud('hudFps', fps + '', fps < 30 ? 'warn' : fps < 45 ? 'dim' : 'ok');
    fpsHistory.push(fps);
    if (fpsHistory.length > 6) fpsHistory.shift();
    frames = 0;
    lastFpsTime = performance.now();
    adjustParticlesByFps();
  }
}

// 内核更新（让它跟着相机方向 + 状态联动）
// v0.6：内核退到"球壳里若隐若现的小光斑"，让位给汇入粒子的河流感
const TARGET_GLOW = {
  [Stage.IDLE]:      { size: 0.10, intensity: 0.22, color: new THREE.Color(PALETTE.coreIdle)  },
  [Stage.HOVER]:     { size: 0.18, intensity: 0.45, color: new THREE.Color(PALETTE.coreHover) },
  [Stage.COLLAPSE]:  { size: 0.75, intensity: 1.40, color: new THREE.Color(PALETTE.coreFlash) },
  [Stage.EXPLOSION]: { size: 1.40, intensity: 2.20, color: new THREE.Color(PALETTE.coreFlash) },
  [Stage.READY]:     { size: 0.00, intensity: 0.00, color: new THREE.Color(0x000000)          },
};

function updateGlow(t) {
  const target = TARGET_GLOW[currentStage];
  if (!target) return;

  // 缓动追随
  glowState.size      += (target.size      - glowState.size)      * 0.10;
  glowState.intensity += (target.intensity - glowState.intensity) * 0.10;
  glowState.color.lerp(target.color, 0.06);

  // COLLAPSE 阶段：内核呼吸感更强
  let pulseSize = 0;
  if (currentStage === Stage.COLLAPSE) {
    pulseSize = Math.sin(t * 6.0) * 0.08 * stageProgress;
  } else if (currentStage === Stage.HOVER) {
    pulseSize = Math.sin(t * 2.5) * 0.04;
  } else if (currentStage === Stage.IDLE) {
    pulseSize = Math.sin(t * 1.2) * 0.03;
  } else if (currentStage === Stage.EXPLOSION) {
    // 爆炸时内核迅速膨胀再衰减（v0.6 峰值再下调）
    const p = stageProgress;
    glowState.intensity = 2.2 * (1.0 - p) + 0.10;
  }

  glowMaterial.uniforms.u_size.value      = Math.max(0, glowState.size + pulseSize);
  glowMaterial.uniforms.u_intensity.value = glowState.intensity;
  glowMaterial.uniforms.u_color.value.copy(glowState.color);

  // billboard：永远朝向相机
  glowMesh.lookAt(camera.position);

  // ============ Fresnel 壳同步：跟随阶段 + 时变扫描带 ============
  fresnelMaterial.uniforms.u_intensity.value = fresnelState.intensity;
  fresnelMaterial.uniforms.u_power.value     = fresnelState.power;
  fresnelMaterial.uniforms.u_color.value.copy(fresnelState.color);
  fresnelMaterial.uniforms.u_time.value      = t;
  // 缓慢自转：让经线扫描带视觉移动，全息感更强
  fresnelMesh.rotation.y = t * 0.08;
}

// =====================================================================
// 13. HUD 工具
// =====================================================================
function setHud(id, text, cls) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = text;
  el.className = 'value' + (cls ? ' ' + cls : '');
}
function setBackend(text, cls) {
  const el = document.getElementById('subBackend');
  if (!el) return;
  el.textContent = text;
  el.className = 'v' + (cls ? ' ' + cls : '');
}
function setInput(text, cls) {
  const el = document.getElementById('subInput');
  if (!el) return;
  el.textContent = text;
  el.className = 'v' + (cls ? ' ' + cls : '');
}
function setSub(text) {
  const el = document.getElementById('subText');
  if (el) el.textContent = text;
}

// =====================================================================
// 14. 启动
// =====================================================================
setHud('hudCount', activeCount.toLocaleString());
setHud('hudGpu', gpuInfo.name.length > 36 ? gpuInfo.name.slice(0, 36) + '...' : gpuInfo.name);
setHud('hudTier', gpuInfo.tier.toUpperCase());
setHud('hudStage', stageLabel[Stage.IDLE]);

setBackend('待机 · 等待手势', 'dim');

initHands();
loop();

// =====================================================================
// 15. 流体装饰层：代码瀑布 + 拓扑图
// ---------------------------------------------------------------------
// 启动时一次性把 DOM 织好，body.stage-ready 触发 CSS 入场动画。
// 代码池贴合"天军 AI 视觉检测"业务：YOLO 推理、MES 推送、相机日志，
// 而不是 Matrix 那种无意义的 0/1。15% 行染金色高亮，模拟"执行代码"闪动。
// =====================================================================
// =====================================================================
// CODE_POOL · 真实启动日志池
// ---------------------------------------------------------------------
// 内容直接对应天军 AI 视觉检测真实启动链路（取自源码 console.log / print）：
//   Electron 主进程   →  License 验证      →  BackendManager spawn python
//   →  uvicorn 启动   →  DB 诊断 + 迁移    →  自动加载项目/模型/视频源
//   →  通道/扫码/MES 初始化  →  前端就绪   →  实时检测推理
// 行尾 ✓ / OK 表示完成；箭头 → 表示状态流转；十六进制是真实 ID 风格。
// =====================================================================
const CODE_POOL = [
  // ---- 1. Electron 主进程 ----
  '[App] Starting TianJun AI Vision...',
  '[App] Is packaged: true',
  '[App] Resources path: C:\\Program Files\\tianjun-ai-vision',
  '[App] Machine ID: TJ-A7F2C9D3E4B1',
  '[App] Loading splash window...',
  '[App] electron version: 32.2.5',
  '[App] node integration: false · sandbox: true',

  // ---- 2. License 验证 ----
  '[License] Found pre-installed license: license.lic',
  '[License] Stable fingerprint: 9b3f8a2c1e7d4f5b6a8c2d3e...',
  '[License] Verify RSA-SHA256 signature ... OK ✓',
  '[License] Machine ID (cached, verified): TJ-A7F2C9D3E4B1',
  '[License] expires: 2027-12-31 23:59:59',
  '[License] License check: 有效 ✓',
  '[License] Machine ID + verify hash cached',

  // ---- 3. BackendManager: spawn python uvicorn ----
  '[BackendManager] Checking for stale backend processes...',
  '[BackendManager] No stale processes found on port 8001',
  '[BackendManager] Configuration:',
  '  Python:     C:\\Program Files\\tianjun-ai-vision\\python\\python.exe',
  '  Backend:    C:\\Program Files\\tianjun-ai-vision\\backend',
  '  Port:       8001',
  '  Dev mode:   false',
  '[BackendManager] Starting: python -m uvicorn backend.main:app --port 8001',
  '[BackendManager] Spawned pid: 14728',

  // ---- 4. 后端启动：诊断 ----
  '[Backend] OPENCV_FFMPEG_CAPTURE_OPTIONS=threads;1  ← v3.1.3 saving fix',
  '[DIAG] main.py: dialect = sqlite',
  '[DIAG] main.py: DB URI = sqlite:///./data/sql_app.db',
  '[DIAG] main.py: MODEL_UPLOAD_DIR = ./data/models',
  '[DIAG] main.py: DB file exists before create_all: true',
  '[DIAG] main.py: DB file size: 4.27 MB',

  // ---- 5. ORM 表注册（31 张表）----
  'import backend.models.models',
  'import backend.models.mes_models',
  'import backend.models.export_models',
  'import backend.models.plugin_models',
  'Base.metadata.create_all(bind=engine)',
  '[DIAG] create_all done · 31 tables registered',

  // ---- 6. DB 迁移：60+ ALTER TABLE ----
  '[migrate] step_records · ADD COLUMN interval_to_next FLOAT',
  '[migrate] detection_cycles · ADD COLUMN interval_to_next FLOAT',
  '[migrate] detection_cycles · ADD COLUMN order_id INTEGER',
  '[migrate] detection_cycles · ADD COLUMN workpiece_id INTEGER',
  '[migrate] detection_sessions · ADD COLUMN channel_id INTEGER DEFAULT 0',
  '[migrate] detection_sessions · ADD COLUMN shift_label VARCHAR(20)',
  '[migrate] projects · ADD COLUMN alarm_config JSON',
  '[migrate] projects · ADD COLUMN detection_config JSON',
  '[migrate] projects · ADD COLUMN data_config JSON',
  '[migrate] projects · ADD COLUMN model_format VARCHAR(50)',
  '[migrate] data_export_settings · 9 cols added',
  '[migrate] export_templates · ADD COLUMN updated_at DATETIME',
  '[migrate] export_realtime_rules · ADD COLUMN trigger_event VARCHAR(40)',
  '[migrate] workpiece_inspections · 4 cols added',
  '[migrate] cluster_config · ADD COLUMN heartbeat_timeout INTEGER',
  '[migrate] box_aggregations · ADD COLUMN box_serial VARCHAR(64)',
  '[migrate] mes_connections · ADD COLUMN retry_count INTEGER',
  '[migrate] 60 migrations done in 84 ms ✓',

  // ---- 7. 孤儿清理 + 内置模板 seed ----
  '[fix_orphan_sessions] scanning detection_sessions...',
  '[fix_orphan_sessions] no orphans found ✓',
  '[cleanup_orphan_inspections] cleaned 0 rows ✓',
  '[seed] export_templates · check builtin templates...',
  '[seed] export_templates · all 6 builtin templates present',

  // ---- 8. 自动加载激活项目（含通道绑定） ----
  '[启动] 读取 workstation_config.json',
  '[启动] channel_manager.set_channel_count(4)',
  '[启动] ch0 加载绑定项目: 螺丝装配检测 (id=12)',
  '[启动] ch0 加载模型: yolov8m-bolt-v3.pt',
  '[启动] ch1 加载绑定项目: 螺母上料检测 (id=15)',
  '[启动] ch1 加载模型: yolov8s-nut-v2.pt',
  '[启动] ch2 加载绑定项目: 弹簧装配 (id=8)',
  '[启动] ch2 加载模型: yolov8m-spring.pt',
  '[启动] ch3 加载绑定项目: 包装线复检 (id=22)',
  '[启动] 兜底: 激活项目 "通用检测" 加载到通道 []',

  // ---- 9. GPU 分配 + 视频源恢复 ----
  '[启动] ch0 GPU 恢复: cuda:0',
  '[启动] ch1 GPU 恢复: cuda:0',
  '[启动] ch2 GPU 恢复: cuda:1',
  '[启动] ch3 GPU 恢复: cuda:1',
  '[启动] ch0 自动恢复摄像头: device=0, resolution=1920x1080',
  '[启动] ch1 自动恢复RTSP: rtsp://192.168.1.64/Streaming/Channels/101',
  '[启动] ch2 自动恢复海康SDK: 192.168.1.65 (HCNetSDK)',
  '[启动] ch3 自动恢复视频: ./recordings/loop_demo.mp4',
  '[启动] ch0 自动恢复检测状态 ✓',
  '[启动] ch1 自动恢复检测状态 ✓',

  // ---- 10. 报警 / 扫码 / MES / 集群 ----
  '[alarm] AlarmManager init · 4 channels',
  '[alarm] AlarmRouter init · shared_with map loaded',
  '[alarm] modbus serial: COM3 · 19200,N,8,1',
  '[alarm] light_tower: green=ON · channel=ALL',
  '[scanner] ScannerService init',
  '[scanner] device LON-A : 192.168.1.80:55256 · mode=B',
  '[scanner] device WMax-1 : COM5 · 3-port virtual',
  '[scanner] _listen_loop start · timeout=2000ms',
  '[scanner] broadcast_channels: [0, 1, 2]',
  '[mes] MESHookManager singleton ready',
  '[mes] mes-hook-worker thread started (daemon)',
  '[mes] _disabled_channels: []',
  '[mes_gateway] adapters registered: rest, form-data, form-urlencoded, query-string, modbus_rtu',
  '[mes_gateway] connection: 客户MES · POST /api/inspect · bearer auth',
  '[cluster] mode=standalone · skip heartbeat',
  '[external_device] WeighingScale-A · COM4 · 9600,N,8,1',
  '[external_device] state_machine: idle → stable_window=800ms',

  // ---- 11. FastAPI router 挂载 ----
  '[fastapi] api_router · 19 prefixes mounted',
  '  ├─ /api/v1/source/*       (1279 lines)',
  '  ├─ /api/v1/data/*         (sessions / cycles / steps)',
  '  ├─ /api/v1/projects/*     · CRUD + activate',
  '  ├─ /api/v1/models/*       · upload / convert / labels',
  '  ├─ /api/v1/alarm/*        · light tower + buzzer',
  '  ├─ /api/v1/scanner/*      · LON + WMax',
  '  ├─ /api/v1/mes/*          · workorder / workpiece / defect',
  '  ├─ /api/v1/mes/gateway/*  · external push',
  '  ├─ /api/v1/export/*       · custom templates + realtime',
  '  └─ /api/v1/cluster/*      · standalone / host / slave',

  // ---- 12. 启动完成 ----
  'INFO:     Started server process [14728]',
  'INFO:     Waiting for application startup.',
  'INFO:     Application startup complete.',
  'INFO:     Uvicorn running on http://127.0.0.1:8001 ✓',

  // ---- 13. Electron 等待后端就绪 ----
  '[App] Waiting for backend health check...',
  '[BackendManager] GET http://127.0.0.1:8001/api/v1/health → 200',
  '[BackendManager] Backend is ready ✓',
  '[App] Backend is ready ✓',

  // ---- 14. 前端启动 ----
  '[vite] dev server starting on port 5180...',
  '[vite] page reload in 287 ms',
  '[frontend] axios baseURL = http://localhost:8001/api/v1',
  '[frontend] pinia init: useSystemStore',
  '[frontend] pinia init: useProjectStore',
  '[frontend] pinia init: useSourceStore',
  '[frontend] pinia init: useScannerDisableStore',
  '[router] route /monitor registered',
  '[router] route /project registered',
  '[router] route /data registered',
  '[router] route /mes registered',
  '[router] route /alarm registered',
  '[router] route /settings registered',
  '[router] route /activation registered',
  '[router] route /source registered',
  '[router] route /model registered',
  '[App] ✓ 页面加载完成: http://localhost:5180/#/monitor',

  // ---- 15. 实时推理 ----
  'conv2d  [1,3,640,640] → [1,32,320,320]',
  'silu  activation',
  'anchor_grid  [3,80,80,2]',
  'yolo_head    P3 P4 P5',
  'nms_iou_threshold = 0.45',
  'tensor.shape = (1, 25, 8400)',
  'detector.load("./models/yolov8m-bolt-v3.pt")',
  '[detect] ch0 target=bolt   conf=0.873 box=(120, 80,40,40)',
  '[detect] ch0 target=nut    conf=0.912 box=(220,150,30,30)',
  '[detect] ch1 target=spring conf=0.685 box=(440,360,28,28)',
  '[detect] ch2 target=gasket conf=0.794 box=(610,260,52,52)',
  'inference_time : 18.4 ms · gpu=cuda:0',
  'fps            : 54.2 / 60.0',
  'gpu_mem        : 1.2 GB / 4.0 GB',

  // ---- 16. MES 推送 ----
  '[mes] workorder = WO-2026-0521-001',
  '[mes] workpiece = #042 · PASS ✓',
  '[mes] workpiece = #043 · PASS ✓',
  '[mes] defect    = NONE',
  '[scanner] barcode = 8F2K-7A923',
  '[hook] cycle_end → mes.push',
  '[mes_gateway] POST /api/inspect · body 412 B',
  '[mes_gateway] response 200 OK · 12 ms ✓',

  // ---- 17. 周期 / 状态机 / 报警 ----
  'cycle_complete : 3.42 s · ch0',
  'total_ok       : 1247',
  'total_ng       : 3',
  'alarm_router   : ALL_CLEAR',
  'state_machine  : IDLE → HOVER',
  'state_machine  : HOVER → COLLAPSE',
  'state_machine  : COLLAPSE → EXPLOSION',
  'state_machine  : EXPLOSION → READY ✓',

  // ---- 18. 系统级（点睛之笔）----
  'channel_manager.set_channel_count(4)',
  'alarm_router.idle_light(channel=2)',
  'mes_hook.on_cycle_end(channel_id=0, cycle=...)',
  'export_renderer.render_to_file(template_id=3, format="docx")',
  'plugin_manager.load_active_plugin_on_startup(app=app)',
  'INITIALIZATION_COMPLETE ✓',
];

function populateCodeStream(container, lineCount = 80) {
  const frag = document.createDocumentFragment();
  for (let i = 0; i < lineCount; i++) {
    const text = CODE_POOL[Math.floor(Math.random() * CODE_POOL.length)];
    const div = document.createElement('div');
    div.className = 'code-line';
    // 含 ✓ 的行 60% 概率染绿（"成功标记")；否则按 15% 金 / 12% 亮蓝染色
    if (text.includes('✓') && Math.random() < 0.6) {
      div.classList.add('ok');
    } else {
      const r = Math.random();
      if (r < 0.15) div.classList.add('gold');
      else if (r < 0.27) div.classList.add('bright');
    }
    div.textContent = text;
    frag.appendChild(div);
  }
  container.appendChild(frag);
}

function buildTopology() {
  const g = document.querySelector('#topology .topo-group');
  if (!g) return;
  const NS = 'http://www.w3.org/2000/svg';

  // 节点：分布在屏幕四角和边缘，刻意避开正中央（让 logo C 位）
  // viewBox 1920×1080
  const nodes = [
    [80, 120],  [80, 320],  [80, 720],   [80, 940],         // 左边缘
    [240, 200], [240, 540], [240, 880],                     // 左次外圈
    [1680, 180],[1840, 360],[1840, 720], [1680, 960],       // 右边缘
    [960, 60],  [1680, 60], [240, 60],                      // 顶部
    [960, 1020],[480, 1020],[1440, 1020],                   // 底部
  ];
  // 连线
  const links = [
    [0,1],[1,2],[2,3],         // 左边缘竖向干道
    [0,4],[1,4],[2,5],[3,6],   // 边缘 → 内侧
    [4,5],[5,6],                // 内侧竖向
    [13,0],[13,4],[11,12],      // 顶部
    [11,4],[12,7],
    [7,8],[8,9],[9,10],         // 右边缘竖向
    [10,16],[16,14],[14,15],[15,3],
    [11,14],
  ];

  links.forEach(([i, j], k) => {
    const [x1, y1] = nodes[i], [x2, y2] = nodes[j];
    const len = Math.hypot(x2 - x1, y2 - y1);
    const ln = document.createElementNS(NS, 'line');
    ln.setAttribute('class', 'link');
    ln.setAttribute('x1', x1); ln.setAttribute('y1', y1);
    ln.setAttribute('x2', x2); ln.setAttribute('y2', y2);
    ln.style.setProperty('--len', len.toFixed(2));
    ln.style.strokeDasharray  = len.toFixed(2);
    ln.style.strokeDashoffset = len.toFixed(2);
    // 阶梯延迟：和 logo reveal 同节奏（READY 进入 1.0s 后开始通电）
    ln.style.animationDelay = `${1.0 + k * 0.08}s`;
    g.appendChild(ln);
  });
  nodes.forEach(([cx, cy], i) => {
    const c = document.createElementNS(NS, 'circle');
    const isBig = i % 3 === 0;
    c.setAttribute('class', 'node' + (isBig ? ' big' : ''));
    c.setAttribute('cx', cx);
    c.setAttribute('cy', cy);
    c.setAttribute('r', isBig ? 3.6 : 2.4);
    // node 上电延迟 + flicker 错相
    const d = 1.4 + i * 0.09;
    c.style.animationDelay = `${d}s, ${d + 0.4}s`;
    g.appendChild(c);
  });
}

function initFluidDecor() {
  document.querySelectorAll('.code-stream').forEach(el => populateCodeStream(el));
  buildTopology();
}

initFluidDecor();

// =====================================================================
// 16. 赛博 HUD：跳变数据 + glitch 触发器
// ---------------------------------------------------------------------
// 目的：让画面"活着"——UID/CRC/SEED 每秒跳变，时钟实时走，FPS/LAT/MEM 微抖。
// glitch 抖动：每隔几秒随机触发一次 logo + HUD 的色差错位，造系统抖动感。
// =====================================================================
const HEX_CHARS = '0123456789ABCDEF';
function randHex(len) {
  let s = '';
  for (let i = 0; i < len; i++) s += HEX_CHARS[Math.floor(Math.random() * 16)];
  return s;
}
function setText(id, txt) {
  const el = document.getElementById(id);
  if (el) el.textContent = txt;
}
function pad2(n) { return n < 10 ? '0' + n : '' + n; }
function pad3(n) { return n < 10 ? '00' + n : n < 100 ? '0' + n : '' + n; }

// HUD 数据更新：每个字段有自己的节奏
let hudFrame = 0;
function updateCyberHud() {
  hudFrame++;
  // 时钟：每帧更新（最直白的"活着"信号）
  const d = new Date();
  setText('hudTime',
    `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}.${pad3(d.getMilliseconds())}`);

  // UID：每 500ms 跳变（造"机器在算"的感觉）
  if (hudFrame % 30 === 0) setText('hudUid', randHex(8).toLowerCase());
  // CRC：每 700ms 跳变
  if (hudFrame % 42 === 0) setText('hudCrc', '0x' + randHex(4));
  // SEED：每 1100ms 跳变
  if (hudFrame % 66 === 0) setText('hudSeed', '0x' + randHex(6));

  // FPS / LAT / MEM：偶尔微抖
  if (hudFrame % 24 === 0) {
    const fpsVal = (52 + Math.random() * 5).toFixed(1);
    setText('hudFps2', fpsVal);
    const latVal = (16 + Math.random() * 4).toFixed(1) + ' ms';
    setText('hudLat', latVal);
    const memVal = (1.2 + Math.random() * 0.08).toFixed(2) + ' / 4.00 GB';
    setText('hudMem', memVal);
  }

  requestAnimationFrame(updateCyberHud);
}
updateCyberHud();

// =====================================================================
// 17. 启动序列叙事 #bootSequence
// ---------------------------------------------------------------------
// 12 个真实启动阶段循环跳动，每 ~800ms 一档。
// "BOOT 07/12  preload yolov8m-bolt-v3.pt · 23 MB    ✓"
// 让等待变得"机器在跑"，叙事感。READY 后由 CSS 淡出。
// =====================================================================
const BOOT_STAGES = [
  { tag: '[BOOT 01/12]', msg: 'mount filesystem · TIANJUN_DATA_DIR' },
  { tag: '[BOOT 02/12]', msg: 'open sqlite WAL · sql_app.db (4.27 MB)' },
  { tag: '[BOOT 03/12]', msg: 'migrate database · 60 ALTER TABLE in 84 ms' },
  { tag: '[BOOT 04/12]', msg: 'init alarm_router · 4 channels · COM3 19200 8N1' },
  { tag: '[BOOT 05/12]', msg: 'init channel_manager · GPU cuda:0 cuda:1' },
  { tag: '[BOOT 06/12]', msg: 'init mes_hook · queue worker thread spawned' },
  { tag: '[BOOT 07/12]', msg: 'preload yolov8m-bolt-v3.pt · 23 MB · cuda:0' },
  { tag: '[BOOT 08/12]', msg: 'init scanner registry · LON + WMax · 3 devices' },
  { tag: '[BOOT 09/12]', msg: 'init external_device · WeighingScale-A COM4' },
  { tag: '[BOOT 10/12]', msg: 'init cluster_collector · mode = standalone' },
  { tag: '[BOOT 11/12]', msg: 'mount api_router · 19 prefixes · 25 endpoints' },
  { tag: '[BOOT 12/12]', msg: 'uvicorn listening · http://127.0.0.1:8001' },
];
let bootIdx = 0;
function tickBootSequence() {
  if (document.body.classList.contains('stage-ready')) return;  // READY 后停转
  const s = BOOT_STAGES[bootIdx];
  const tag = document.getElementById('bootTag');
  const msg = document.getElementById('bootMsg');
  if (tag && msg) {
    tag.textContent = s.tag;
    // 50% 概率结尾带 OK ✓（绿色）
    const okSpan = Math.random() < 0.55
      ? '<span class="boot-ok">✓</span>'
      : '';
    msg.innerHTML = s.msg + okSpan;
  }
  bootIdx = (bootIdx + 1) % BOOT_STAGES.length;
}
tickBootSequence();
setInterval(tickBootSequence, 800);

// Glitch 触发器：每 2-5s 随机触发一次（IDLE 和 READY 都触发）
// E · v0.13：除了 DOM 抖动，还驱动 u_glitchAmt 在 ~180ms 内从 1 衰减到 0，
// 让粒子球本身按 y 分带横向撕裂、RGB 偏色 → 真"赛博显示器信号干扰"。
function triggerGlitch() {
  document.body.classList.add('glitch');
  setTimeout(() => document.body.classList.remove('glitch'), 140);
  if (FEAT.audio) playGlitchSfx();

  // shader 侧撕裂：仅在 FEAT.glitch3d 打开时驱动 u_glitchAmt 衰减
  // （关闭时只走 DOM 抖动，不影响粒子球本体形态）
  if (!FEAT.glitch3d) return;
  const startTs = performance.now();
  const dur = 180;
  function decay() {
    const t = (performance.now() - startTs) / dur;
    if (t >= 1) {
      uniforms.u_glitchAmt.value = 0;
      return;
    }
    const stepped = (t < 0.35) ? 1.0 : (1 - (t - 0.35) / 0.65);
    uniforms.u_glitchAmt.value = Math.max(0, stepped);
    requestAnimationFrame(decay);
  }
  decay();
}
function scheduleNextGlitch() {
  const wait = 2000 + Math.random() * 3500;  // 2-5.5s 一次
  setTimeout(() => {
    // v0.13：IDLE 也触发（之前限制只在 READY，现在让 IDLE 也偶尔抖一下）
    triggerGlitch();
    scheduleNextGlitch();
  }, wait);
}
scheduleNextGlitch();

// =====================================================================
// 18. 左右侧栏装饰：telemetry / bar chart / event log 实时驱动
// ---------------------------------------------------------------------
// 三类数据：
//   1) telemetry 5 根 bar：每 800ms 在 ±8% 范围内布朗运动，模拟系统负载抖动
//   2) bar-chart 24 根条：每 220ms 整体重洗，模拟"神经网络频谱"
//   3) event log：每 1.2-2.4s 推一行新日志，超过 6 行裁掉
// =====================================================================
function rngWalk(cur, step, lo, hi) {
  const next = cur + (Math.random() - 0.5) * step * 2;
  return Math.max(lo, Math.min(hi, next));
}
const telemetryState = { cpu: 47, gpu0: 72, gpu1: 89, ram: 31, disk: 58 };
function tickTelemetry() {
  telemetryState.cpu  = rngWalk(telemetryState.cpu,  6, 30, 78);
  telemetryState.gpu0 = rngWalk(telemetryState.gpu0, 5, 55, 92);
  telemetryState.gpu1 = rngWalk(telemetryState.gpu1, 5, 60, 96);
  telemetryState.ram  = rngWalk(telemetryState.ram,  3, 22, 48);
  telemetryState.disk = rngWalk(telemetryState.disk, 4, 35, 75);
  const apply = (id, val) => {
    const fill = document.getElementById(id);
    const txt  = document.getElementById(id + 'V');
    if (fill) fill.style.width = val.toFixed(0) + '%';
    if (txt)  txt.textContent  = String(Math.round(val)).padStart(3, '0');
  };
  apply('telCpu',  telemetryState.cpu);
  apply('telGpu0', telemetryState.gpu0);
  apply('telGpu1', telemetryState.gpu1);
  apply('telRam',  telemetryState.ram);
  apply('telDisk', telemetryState.disk);
}
if (FEAT.sidebars) {
  tickTelemetry();
  setInterval(tickTelemetry, 800);
}

// bar-chart 24 根条：每 220ms 重洗高度，符合"神经网络频谱"波动感
function tickBarChart() {
  const chart = document.getElementById('barChart');
  if (!chart) return;
  const bars = chart.querySelectorAll('.bc-bar');
  bars.forEach((b, i) => {
    // 用正弦+随机叠加：低频呼吸 + 高频脉冲
    const base = 35 + 30 * Math.sin(performance.now() / 600 + i * 0.5);
    const jitter = (Math.random() - 0.5) * 40;
    const h = Math.max(8, Math.min(98, base + jitter));
    b.style.height = h + '%';
  });
}
if (FEAT.sidebars) {
  tickBarChart();
  setInterval(tickBarChart, 220);
}

// event log：模拟项目里 mes_hooks / source 的真实事件类型
const EVENT_TEMPLATES = [
  { tag: 'CYC_START',   cls: '',     msg: (ch) => `channel#${ch} cycle begin` },
  { tag: 'CYC_OK',      cls: 'ok',   msg: (ch) => `channel#${ch} OK · 3 steps · 124ms` },
  { tag: 'CYC_NG',      cls: 'ng',   msg: (ch) => `channel#${ch} NG · missing_bolt` },
  { tag: 'SCN_PAIR',    cls: '',     msg: (ch) => `scanner→ch${ch} settle` },
  { tag: 'MES_PUSH',    cls: 'ok',   msg: ( ) => `gateway · 200 · 47ms` },
  { tag: 'BOX_AGG',     cls: '',     msg: ( ) => `box#A47-2031 · 4/4 settled` },
  { tag: 'ALARM_IDLE',  cls: 'warn', msg: ( ) => `tower · green · all clear` },
  { tag: 'INF_RUN',     cls: '',     msg: (ch) => `inference ch${ch} · 18.4ms` },
  { tag: 'OP_LOGIN',    cls: 'ok',   msg: ( ) => `operator · WANG_QIANG` },
  { tag: 'EXP_TRIG',    cls: 'ok',   msg: ( ) => `export · daily_summary.docx` },
];
function tickEventLog() {
  const log = document.getElementById('eventLog');
  if (!log) return;
  const tpl = EVENT_TEMPLATES[Math.floor(Math.random() * EVENT_TEMPLATES.length)];
  const ch  = (Math.floor(Math.random() * 4) + 1).toString().padStart(2, '0');
  const now = new Date();
  const hh  = String(now.getHours()).padStart(2, '0');
  const mm  = String(now.getMinutes()).padStart(2, '0');
  const ss  = String(now.getSeconds()).padStart(2, '0');
  const row = document.createElement('div');
  row.className = 'ev-row';
  row.innerHTML =
    `<span class="ev-ts">${hh}:${mm}:${ss}</span>` +
    `<span class="ev-tag ${tpl.cls}">${tpl.tag}</span>` +
    `<span class="ev-msg">${tpl.msg(ch)}</span>`;
  log.prepend(row);
  // 控制行数 ≤ 6
  while (log.children.length > 6) log.removeChild(log.lastChild);
}
if (FEAT.sidebars) {
  for (let i = 0; i < 4; i++) tickEventLog();
  setInterval(() => { if (Math.random() < 0.85) tickEventLog(); }, 1200);
}


// =====================================================================
// 19. READY 阶段加载进度条（v3.8.2：真实后端日志驱动）
// ---------------------------------------------------------------------
// 设计原则：
//   1. 进度完全由真实后端 stdout/stderr 行数推动 — 每收到 1 行 +0.5%
//   2. 进度封顶 95%，等 /api/v1/health 通了（backendReadyFromIPC）才一次性补 100%
//   3. 阶段标签按行数粗略映射（INITIALIZING / BOOTSTRAP / LOADING UI / WARMUP / READY）
//   4. 100% 后播放完成音效 + 600ms 停留 → 调 splashAPI.notifySplashFinished()
//      让 Electron 主进程关闭 splash 窗口 + 显示主界面
//   5. 沙盒模式（splashAPI 不存在）：保留 5s 模拟节奏 + 不调 splashFinished，
//      方便直接在浏览器调试
// =====================================================================
const LOADING_STAGES = [
  { atLine:    0, stage: 'INITIALIZING' },
  { atLine:   12, stage: 'BOOTSTRAP'    },
  { atLine:   40, stage: 'LOADING DB'   },
  { atLine:   80, stage: 'LOADING UI'   },
  { atLine:  130, stage: 'WARMUP'       },
];
const PROGRESS_PER_LINE = 0.5;           // 每收到 1 行后端日志 +0.5%
const PROGRESS_CAP_BEFORE_READY = 95;    // 后端没就绪前进度封顶
const SANDBOX_FALLBACK_MS = 5000;        // 浏览器沙盒模式假进度时长

let readyLoadingStarted = false;
function startReadyLoading() {
  if (readyLoadingStarted) return;
  readyLoadingStarted = true;
  const startTs = performance.now();
  const loader  = document.getElementById('revealLoader');
  const stageEl = document.getElementById('loaderStage');
  const pctEl   = document.getElementById('loaderPct');
  const fillEl  = document.getElementById('loaderFill');
  const stepEl  = document.getElementById('loaderStep');
  let displayPct = 0;           // 渲染用进度（平滑插值避免跳变）
  let lastStageIdx = -1;
  let completed = false;

  function pickStage(lineCount) {
    let cur = LOADING_STAGES[0];
    for (const s of LOADING_STAGES) {
      if (lineCount >= s.atLine) cur = s;
    }
    return cur;
  }

  function tick() {
    if (completed) return;

    // ---- 计算目标进度 ----
    let targetPct;
    if (splashAPI) {
      // 真·后端日志驱动: 行数 × 0.5% (封顶 95%); 后端 ready 强制补 100%
      // 修复 bug: 原写法用 min(cap, lineCount*0.5) 会让 ready 后停在 lineCount*0.5,
      // 因为这是上限不是下限. 拆成两路, ready=true 时无视行数直接 100%.
      if (backendReadyFromIPC) {
        targetPct = 100;
      } else {
        targetPct = Math.min(PROGRESS_CAP_BEFORE_READY, backendLogLineCount * PROGRESS_PER_LINE);
      }
    } else {
      // 沙盒兜底：5 秒 easeOutQuad 走完，方便浏览器调试
      const t = Math.min(1, (performance.now() - startTs) / SANDBOX_FALLBACK_MS);
      targetPct = (1 - (1 - t) * (1 - t)) * 100;
    }

    // ---- 平滑插值，每帧最多移动 1.2%，避免突然跳进 ----
    const diff = targetPct - displayPct;
    if (Math.abs(diff) > 0.05) {
      displayPct += diff * 0.12;
      if (Math.abs(targetPct - displayPct) < 0.5) displayPct = targetPct;
    }
    // v3.9.1a hotfix: 浮点收敛 edge case 兜底——targetPct=100 且 displayPct≥99 时强制 snap。
    // 修原 `Math.abs(diff) > 0.05` 退出条件下 displayPct 永远卡 99.95 (floor=99) 的边界:
    //   - rAF 节流 (浏览器后台 tab / F12 打开) 或浮点累积都可能让 displayPct 落在
    //     `(targetPct - 0.05, targetPct]` 区间永远出不来 → pctVal=99 → 完成判定 `>=100` 永远不触发。
    // 仅当 target=100 (真后端 ready 或沙盒 5s 后) 且 display≥99 时生效, 不影响 0-99 区间正常爬升。
    if (targetPct >= 100 && displayPct >= 99 && displayPct < 100) {
      displayPct = 100;
    }
    const pctVal = Math.floor(displayPct);

    if (pctEl)  pctEl.textContent  = String(pctVal).padStart(3, '0') + '%';
    if (fillEl) fillEl.style.width = pctVal + '%';

    // ---- 阶段标签：行数驱动 ----
    const cur = pickStage(splashAPI ? backendLogLineCount : Math.floor(displayPct * 1.5));
    if (stageEl) stageEl.textContent = cur.stage;

    // ---- 当前 step 文案：优先用最近一条真实日志 ----
    if (stepEl) {
      const recent = backendLogQueue[backendLogQueue.length - 1];
      stepEl.textContent = recent
        ? '▸ ' + recent.slice(0, 80)
        : '▸ awaiting backend handshake ...';
    }

    // ---- 阶段切换音效 ----
    const curIdx = LOADING_STAGES.indexOf(cur);
    if (FEAT.audio && curIdx !== lastStageIdx) {
      lastStageIdx = curIdx;
      if (curIdx > 0) playLoadTickSfx();
    }

    // ---- 完成判定 ----
    if (pctVal >= 100) {
      completed = true;
      if (loader) loader.classList.add('complete');
      if (stepEl) stepEl.textContent = '✓ jack in monitor view';
      if (FEAT.audio) playLoadCompleteSfx();
      // 600ms 闪烁停留后走统一的 forceSplashFinish 路径
      // (v3.9.1a hotfix: 收敛到同一个 finish 入口, 和 hard deadline 兜底共享 splashFinished flag,
      //  避免两条路径都触发 finish IPC 让主进程重复处理)
      setTimeout(() => forceSplashFinish('progress-100'), 600);
      return;
    }
    requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
}

// =====================================================================
// 20. SPLASH IPC 桥安装（v3.8.2）
// ---------------------------------------------------------------------
// Electron 主进程在 createSplashWindow() 后, 通过 webContents.send 把
// BackendManager 的 'stdout' 'stderr' 'ready' 事件实时推送到这里。
// =====================================================================
function consumeBackendLog(line) {
  if (!line) return;
  backendLogLineCount++;
  backendLogQueue.push(line);
  if (backendLogQueue.length > BACKEND_LOG_BUFFER_MAX) {
    backendLogQueue.splice(0, backendLogQueue.length - BACKEND_LOG_BUFFER_MAX);
  }
  // IDLE 阶段把后端日志投到底部 bootSequence 行，给用户"系统正在动"的反馈
  if (!document.body.classList.contains('stage-ready')) {
    const bootMsg = document.getElementById('bootMsg');
    const bootTag = document.getElementById('bootTag');
    if (bootMsg && bootTag) {
      // 截 80 字防止溢出
      bootMsg.textContent = line.slice(0, 80);
      // 阶段标签按行数粗映射
      const stageNum = String(Math.min(12, Math.floor(backendLogLineCount / 12) + 1)).padStart(2, '0');
      bootTag.textContent = `[BOOT ${stageNum}/12]`;
    }
  }
}

if (splashAPI && typeof splashAPI.onBackendLog === 'function') {
  splashAPI.onBackendLog((payload) => {
    if (!payload) return;
    // payload 形如 { stream: 'stdout'|'stderr', msg: string }
    // msg 可能多行（uvicorn 一次性 flush 一段），按 \n 拆
    const lines = String(payload.msg || '').split(/\r?\n/);
    for (const ln of lines) consumeBackendLog(ln);
  });
}
// v3.9.1a hotfix: splash 完成状态共享 flag, 给 hard deadline 兜底用,
// 避免 progress tick 自然完成 和 hard deadline 强制完成 重复触发 finish IPC。
let splashFinished = false;

function forceSplashFinish(reason) {
  if (splashFinished) return;
  splashFinished = true;
  console.log(`[Splash] forceFinish 触发: ${reason}`);
  if (splashAPI && typeof splashAPI.notifySplashFinished === 'function') {
    splashAPI.notifySplashFinished();
  } else {
    console.log('[Splash] 沙盒模式: 流程完成 (无主进程可通知)');
  }
}

if (splashAPI && typeof splashAPI.onBackendReady === 'function') {
  splashAPI.onBackendReady(() => {
    console.log('[Splash] 主进程通知后端 ready');
    backendReadyFromIPC = true;

    // v3.9.1a hotfix: hard deadline 兜底——后端真 ready 后最多再等 8s,
    // 进度条还没自然冲到 100% 就强制走 finish 路径,
    // 防御 progress tick 收敛 bug / rAF 节流 / 任何未知阻塞让 splash 永远关不掉。
    // 注意: 必须 backendReadyFromIPC=true 之后才开始计时, 不是 splash 启动就计时——
    //   后端模型加载可能要 30-60s (工控机性能弱时), 不能在后端没 ready 时就强制关 splash。
    setTimeout(() => forceSplashFinish('backend-ready-deadline-8s'), 8000);
  });
}

// =====================================================================
// 21. 无摄像头自动播放兜底（v3.8.2）
// ---------------------------------------------------------------------
// 客户机没摄像头时, 不能让 splash 永远卡在 IDLE。
// initHands 失败时调用本函数: 等后端 ready 后自动塌缩 + 爆炸 + 进 READY。
// =====================================================================
let autoplayScheduled = false;
function scheduleAutoplayFallback() {
  if (autoplayScheduled) return;
  autoplayScheduled = true;
  console.log('[Splash] 进入无摄像头自动播放模式，等待后端 ready');
  const poll = setInterval(() => {
    if (!backendReadyFromIPC && splashAPI) return;   // 接 IPC 模式: 等后端 ready
    if (!splashAPI) {                                 // 沙盒模式: 3 秒后自动播放
      // 由 sandbox 模式启动后 3s 自动 collapse
    }
    if (currentStage !== Stage.IDLE) return;          // 已被用户/其他路径触发就不抢
    clearInterval(poll);
    console.log('[Splash] 自动塌缩 → 爆炸 → 进 READY');
    transitionTo(Stage.COLLAPSE);
    setTimeout(() => {
      if (currentStage === Stage.COLLAPSE) transitionTo(Stage.EXPLOSION);
    }, 1400);   // 给 COLLAPSE 一段视觉时间，主循环会自然推到 EXPLOSION → READY
  }, 600);
  // 沙盒兜底：splashAPI 不存在时，5 秒后强制启动
  if (!splashAPI) setTimeout(() => { backendReadyFromIPC = true; }, 5000);
}
