/**
 * guideEngine — 投影光引导渲染引擎 (Canvas2D, 零依赖)
 *
 * 投影仪介质特性决定的设计:
 * - 背景纯黑 = 投影仪不打光, 台面保持原貌; 所有引导元素用高亮度发光色
 * - 加法混合 (globalCompositeOperation='lighter') 叠出辉光, 物理上就是"光"
 * - 动画克制: 呼吸 ~1Hz、完成扫光 <=700ms 后立即静场, 产线不能一直闪
 *
 * 坐标契约: setState 传入的所有几何都在"标定投影空间" (projW×projH, CSS px),
 * 引擎内部按 canvas 实际尺寸缩放, 调用方不用关心 DPR。
 */

const COLORS = {
  accent: '#00d2ff',      // 引导主色 (青)
  accentDim: 'rgba(0, 210, 255, 0.35)',
  ok: '#2dbe7c',
  ng: '#eb3e4b',
  amber: '#ffc63a',
  text: '#ecf5fa',
  textDim: 'rgba(236, 245, 250, 0.55)',
  chipDone: 'rgba(45, 190, 124, 0.85)',
  chipPending: 'rgba(110, 131, 144, 0.35)',
};

// ---------- 几何工具 ----------

function centroid(poly) {
  let x = 0, y = 0;
  for (const p of poly) { x += p[0]; y += p[1]; }
  return [x / poly.length, y / poly.length];
}

function polyBounds(poly) {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const p of poly) {
    x0 = Math.min(x0, p[0]); y0 = Math.min(y0, p[1]);
    x1 = Math.max(x1, p[0]); y1 = Math.max(y1, p[1]);
  }
  return { x0, y0, x1, y1, w: x1 - x0, h: y1 - y0 };
}

/** 多边形周长参数化: 返回 (t in [0,1)) → [x, y] 的取点函数 */
function perimeterWalker(poly) {
  const segs = [];
  let total = 0;
  for (let i = 0; i < poly.length; i++) {
    const a = poly[i], b = poly[(i + 1) % poly.length];
    const len = Math.hypot(b[0] - a[0], b[1] - a[1]);
    segs.push({ a, b, len, acc: total });
    total += len;
  }
  if (total <= 0) return () => poly[0] || [0, 0];
  return (t) => {
    const d = ((t % 1) + 1) % 1 * total;
    for (const s of segs) {
      if (d <= s.acc + s.len) {
        const k = s.len > 0 ? (d - s.acc) / s.len : 0;
        return [s.a[0] + (s.b[0] - s.a[0]) * k, s.a[1] + (s.b[1] - s.a[1]) * k];
      }
    }
    return segs[0].a;
  };
}

function tracePoly(ctx, poly) {
  ctx.beginPath();
  ctx.moveTo(poly[0][0], poly[0][1]);
  for (let i = 1; i < poly.length; i++) ctx.lineTo(poly[i][0], poly[i][1]);
  ctx.closePath();
}

// ---------- 引擎 ----------

export class GuideEngine {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.state = {
      projW: 1920, projH: 1080,
      steps: [],            // [{label, display, status: 'done'|'current'|'pending'}]
      target: null,         // {polygon: [[x,y]...], display, live: bool}
      liveBoxes: [],        // [[[x,y]×4], ...] 当前步实时检测框 (投影空间四角)
      idle: true,
      message: '',
      showGrid: false,
      anchors: [],          // [{img: HTMLImageElement, tile: [x,y,size]}] 自愈锚点
      confirm: null,        // {label, sub, progress: 0..1, rect: [x,y,w,h]} 悬停确认按钮
      brightness: 1,        // 引导画面亮度系数 (设置页可调; 锚点/确认按钮不受影响)
      flowPath: true,       // 流向引导路径动效开关
    };
    this._flash = null;     // {type:'ok'|'ng', t0}
    this._particles = [];
    this._raf = null;
    this._running = false;
    this._resize = this._resize.bind(this);
  }

  start() {
    if (this._running) return;
    this._running = true;
    window.addEventListener('resize', this._resize);
    this._resize();
    const loop = () => {
      if (!this._running) return;
      this._render(performance.now() / 1000);
      this._raf = requestAnimationFrame(loop);
    };
    this._raf = requestAnimationFrame(loop);
  }

  stop() {
    this._running = false;
    if (this._raf) cancelAnimationFrame(this._raf);
    window.removeEventListener('resize', this._resize);
  }

  setState(partial) {
    Object.assign(this.state, partial);
  }

  /** 一次性动效: OK 扫光 / NG 红闪 (由视图层在事件 seq 变化时调用) */
  flash(type) {
    this._flash = { type, t0: performance.now() / 1000 };
    if (type === 'ok' && this.state.target) {
      // 完成粒子爆发 (<=400ms, 之后静场)
      const [cx, cy] = centroid(this.state.target.polygon);
      for (let i = 0; i < 26; i++) {
        const ang = Math.random() * Math.PI * 2;
        const speed = 120 + Math.random() * 260;
        this._particles.push({
          x: cx, y: cy,
          vx: Math.cos(ang) * speed, vy: Math.sin(ang) * speed,
          life: 0.45 + Math.random() * 0.25, born: this._flash.t0,
        });
      }
    }
  }

  _resize() {
    const dpr = window.devicePixelRatio || 1;
    const w = window.innerWidth, h = window.innerHeight;
    this.canvas.width = Math.round(w * dpr);
    this.canvas.height = Math.round(h * dpr);
    this.canvas.style.width = `${w}px`;
    this.canvas.style.height = `${h}px`;
    this._cssW = w;
    this._cssH = h;
    this._dpr = dpr;
  }

  // ---------- 渲染 ----------

  _render(t) {
    const { ctx } = this;
    const s = this.state;
    const dpr = this._dpr || 1;
    const W = this._cssW || window.innerWidth;
    const H = this._cssH || window.innerHeight;

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = '#000';
    ctx.fillRect(0, 0, W, H);

    // 标定投影空间 → 当前窗口 (标定后分辨率一般一致, 缩放只是兜底)
    const sx = W / (s.projW || W);
    const sy = H / (s.projH || H);
    const bright = Math.max(0.3, Math.min(1, s.brightness || 1));

    ctx.save();
    ctx.scale(sx, sy);
    ctx.globalAlpha = bright;

    if (s.showGrid) this._drawGrid(t);

    if (s.idle) {
      this._drawIdle(t);
    } else {
      if (s.flowPath && s.target) this._drawFlowPath(t, s.target);
      if (s.target) this._drawTarget(t, s.target);
      if (s.liveBoxes.length) this._drawLiveBoxes(t, s.liveBoxes);
      this._drawParticleBurst(t);
      this._drawFlashOverlay(t);
    }

    // 自愈锚点与确认按钮是"传感器" (相机要检测/采样它们), 不受亮度系数影响
    ctx.globalAlpha = 1;
    if (s.anchors.length) this._drawAnchors();
    if (s.confirm) this._drawConfirm(t, s.confirm);

    ctx.restore();

    // 步骤链和消息画在窗口空间 (不随投影空间缩放失真)
    ctx.save();
    ctx.globalAlpha = bright;
    if (!s.idle && s.steps.length) this._drawStepRail(t, W, H);
    if (s.message) this._drawMessage(s.message, W, H, t);
    ctx.restore();
  }

  _drawGrid(t) {
    const { ctx } = this;
    const s = this.state;
    ctx.save();
    ctx.strokeStyle = 'rgba(42, 94, 120, 0.55)';
    ctx.lineWidth = 1;
    const step = 120;
    for (let x = 0; x <= s.projW; x += step) {
      ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, s.projH); ctx.stroke();
    }
    for (let y = 0; y <= s.projH; y += step) {
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(s.projW, y); ctx.stroke();
    }
    // 四角对位标 (肉眼核标定精度)
    ctx.strokeStyle = COLORS.accent;
    ctx.lineWidth = 3;
    const m = 40, L = 60;
    for (const [cx, cy, dx, dy] of [
      [m, m, 1, 1], [s.projW - m, m, -1, 1],
      [s.projW - m, s.projH - m, -1, -1], [m, s.projH - m, 1, -1],
    ]) {
      ctx.beginPath();
      ctx.moveTo(cx + dx * L, cy); ctx.lineTo(cx, cy); ctx.lineTo(cx, cy + dy * L);
      ctx.stroke();
    }
    // 中心十字
    ctx.beginPath();
    ctx.moveTo(s.projW / 2 - 40, s.projH / 2); ctx.lineTo(s.projW / 2 + 40, s.projH / 2);
    ctx.moveTo(s.projW / 2, s.projH / 2 - 40); ctx.lineTo(s.projW / 2, s.projH / 2 + 40);
    ctx.stroke();
    ctx.restore();
  }

  _drawIdle(t) {
    const { ctx } = this;
    const s = this.state;
    const cx = s.projW / 2, cy = s.projH / 2;
    const breath = 0.5 + 0.5 * Math.sin(t * 1.6);
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    // 旋转弧环
    ctx.strokeStyle = COLORS.accentDim;
    ctx.lineWidth = 3;
    ctx.shadowColor = COLORS.accent;
    ctx.shadowBlur = 18;
    for (let i = 0; i < 3; i++) {
      const a0 = t * (0.6 + i * 0.25) + i * 2.1;
      ctx.beginPath();
      ctx.arc(cx, cy, 90 + i * 26, a0, a0 + Math.PI * 0.9);
      ctx.stroke();
    }
    ctx.restore();
    ctx.save();
    ctx.fillStyle = `rgba(236, 245, 250, ${0.35 + 0.3 * breath})`;
    ctx.font = '500 30px "PingFang SC", "Microsoft YaHei", sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('等待检测启动', cx, cy + 190);
    ctx.restore();
  }

  _drawTarget(t, target) {
    const { ctx } = this;
    const poly = target.polygon;
    if (!poly || poly.length < 3) return;
    const breath = 0.5 + 0.5 * Math.sin(t * Math.PI * 2 * 0.9); // ~0.9Hz 呼吸
    const b = polyBounds(poly);

    // 内部填充 (低亮度脉动)
    ctx.save();
    tracePoly(ctx, poly);
    ctx.fillStyle = `rgba(0, 210, 255, ${0.05 + 0.07 * breath})`;
    ctx.fill();
    ctx.restore();

    // 辉光描边: 宽模糊底 + 亮核线 双 pass
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    tracePoly(ctx, poly);
    ctx.strokeStyle = `rgba(0, 210, 255, ${0.35 + 0.35 * breath})`;
    ctx.lineWidth = 10;
    ctx.shadowColor = COLORS.accent;
    ctx.shadowBlur = 34 + 18 * breath;
    ctx.stroke();
    tracePoly(ctx, poly);
    ctx.strokeStyle = `rgba(220, 250, 255, ${0.75 + 0.25 * breath})`;
    ctx.lineWidth = 3;
    ctx.shadowBlur = 0;
    ctx.stroke();
    ctx.restore();

    // 流光粒子沿边缘运行
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    const walker = perimeterWalker(poly);
    const N = 5;
    for (let i = 0; i < N; i++) {
      const head = (t * 0.22 + i / N) % 1;
      for (let k = 0; k < 7; k++) {
        const [px, py] = walker(head - k * 0.006);
        const alpha = (1 - k / 7) * 0.9;
        ctx.beginPath();
        ctx.arc(px, py, 4.5 - k * 0.5, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(160, 240, 255, ${alpha})`;
        ctx.shadowColor = COLORS.accent;
        ctx.shadowBlur = 14;
        ctx.fill();
      }
    }
    ctx.restore();

    // 目标名称牌 (多边形上方)
    const label = target.display || '';
    if (label) {
      ctx.save();
      ctx.font = '600 26px "PingFang SC", "Microsoft YaHei", sans-serif';
      const tw = ctx.measureText(label).width;
      const chipW = tw + 44, chipH = 44;
      const chipX = b.x0 + b.w / 2 - chipW / 2;
      const chipY = Math.max(8, b.y0 - chipH - 16);
      ctx.fillStyle = 'rgba(4, 24, 32, 0.85)';
      ctx.strokeStyle = COLORS.accent;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.roundRect(chipX, chipY, chipW, chipH, 10);
      ctx.fill();
      ctx.stroke();
      ctx.fillStyle = COLORS.text;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      // ▶ 指示当前步
      ctx.fillText(`▶ ${label}`, chipX + chipW / 2, chipY + chipH / 2 + 1);
      ctx.restore();
    }
  }

  /**
   * 流向引导路径: 从投影底边中央 (操作员/取料方向) 到当前步目标的弧线,
   * chevron 箭头沿线流动 —— 手把手告诉操作员"从这里拿, 装到那里"。
   */
  _drawFlowPath(t, target) {
    const { ctx } = this;
    const s = this.state;
    const [tx, ty] = centroid(target.polygon);
    const x0 = s.projW / 2;
    const y0 = s.projH - 36;
    const dx = tx - x0, dy = ty - y0;
    const len = Math.hypot(dx, dy);
    if (len < 180) return; // 目标就在出发点旁, 画路径反而碍事

    // 二次贝塞尔: 控制点向路径法向偏移, 形成柔和弧线
    const cx = (x0 + tx) / 2 - dy / len * len * 0.16;
    const cy = (y0 + ty) / 2 + dx / len * len * 0.16;
    const q = (u) => [
      (1 - u) * (1 - u) * x0 + 2 * (1 - u) * u * cx + u * u * tx,
      (1 - u) * (1 - u) * y0 + 2 * (1 - u) * u * cy + u * u * ty,
    ];

    ctx.save();
    ctx.globalCompositeOperation = 'lighter';

    // 底线 (低亮度虚线, 起终点淡出)
    ctx.setLineDash([14, 18]);
    ctx.lineDashOffset = -t * 90; // 虚线本身也向目标流动
    ctx.strokeStyle = 'rgba(0, 210, 255, 0.28)';
    ctx.lineWidth = 3;
    ctx.shadowColor = COLORS.accent;
    ctx.shadowBlur = 10;
    ctx.beginPath();
    ctx.moveTo(x0, y0);
    ctx.quadraticCurveTo(cx, cy, tx, ty);
    ctx.stroke();
    ctx.setLineDash([]);

    // chevron 箭头组沿线流动 (目标端淡出, 避免糊住目标框)
    const N = 4;
    for (let i = 0; i < N; i++) {
      const u = (t * 0.35 + i / N) % 1;
      const fade = Math.min(1, u * 5) * Math.min(1, (0.92 - u) * 6);
      if (fade <= 0) continue;
      const [px, py] = q(u);
      const [qx, qy] = q(Math.min(1, u + 0.02));
      const ang = Math.atan2(qy - py, qx - px);
      const size = 15;
      ctx.save();
      ctx.translate(px, py);
      ctx.rotate(ang);
      ctx.strokeStyle = `rgba(160, 240, 255, ${0.95 * fade})`;
      ctx.lineWidth = 4.5;
      ctx.lineCap = 'round';
      ctx.shadowColor = COLORS.accent;
      ctx.shadowBlur = 16;
      ctx.beginPath();
      ctx.moveTo(-size * 0.6, -size * 0.7);
      ctx.lineTo(size * 0.5, 0);
      ctx.lineTo(-size * 0.6, size * 0.7);
      ctx.stroke();
      ctx.restore();
    }

    // 出发点标识 (取料方向的呼吸圆点)
    const breath = 0.5 + 0.5 * Math.sin(t * Math.PI * 2);
    ctx.beginPath();
    ctx.arc(x0, y0, 7 + 3 * breath, 0, Math.PI * 2);
    ctx.fillStyle = `rgba(0, 210, 255, ${0.5 + 0.3 * breath})`;
    ctx.shadowBlur = 18;
    ctx.fill();

    ctx.restore();
  }

  _drawLiveBoxes(t, boxes) {
    // 实时检测框: 琥珀色四角括号 (证明"我们在实时看着零件")
    const { ctx } = this;
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    ctx.strokeStyle = COLORS.amber;
    ctx.lineWidth = 3;
    ctx.shadowColor = COLORS.amber;
    ctx.shadowBlur = 12;
    for (const quad of boxes) {
      const b = polyBounds(quad);
      const L = Math.min(b.w, b.h) * 0.22;
      for (const [cx, cy, dx, dy] of [
        [b.x0, b.y0, 1, 1], [b.x1, b.y0, -1, 1],
        [b.x1, b.y1, -1, -1], [b.x0, b.y1, 1, -1],
      ]) {
        ctx.beginPath();
        ctx.moveTo(cx + dx * L, cy);
        ctx.lineTo(cx, cy);
        ctx.lineTo(cx, cy + dy * L);
        ctx.stroke();
      }
    }
    ctx.restore();
  }

  _drawParticleBurst(t) {
    if (!this._particles.length) return;
    const { ctx } = this;
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    this._particles = this._particles.filter((p) => t - p.born < p.life);
    for (const p of this._particles) {
      const age = t - p.born;
      const k = 1 - age / p.life;
      const x = p.x + p.vx * age;
      const y = p.y + p.vy * age;
      ctx.beginPath();
      ctx.arc(x, y, 3.5 * k, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(120, 255, 190, ${0.9 * k})`;
      ctx.shadowColor = COLORS.ok;
      ctx.shadowBlur = 16;
      ctx.fill();
    }
    ctx.restore();
  }

  _drawFlashOverlay(t) {
    if (!this._flash) return;
    const { ctx } = this;
    const s = this.state;
    const age = t - this._flash.t0;
    const type = this._flash.type;
    const dur = type === 'ok' ? 0.7 : 0.9;
    if (age > dur) { this._flash = null; return; }

    if (type === 'ok') {
      // 绿色扫光环从目标中心扩散
      const origin = s.target ? centroid(s.target.polygon) : [s.projW / 2, s.projH / 2];
      const k = age / dur;
      const r = 60 + k * Math.hypot(s.projW, s.projH) * 0.5;
      ctx.save();
      ctx.globalCompositeOperation = 'lighter';
      ctx.strokeStyle = `rgba(45, 190, 124, ${0.85 * (1 - k)})`;
      ctx.lineWidth = 14 * (1 - k) + 2;
      ctx.shadowColor = COLORS.ok;
      ctx.shadowBlur = 30;
      ctx.beginPath();
      ctx.arc(origin[0], origin[1], r, 0, Math.PI * 2);
      ctx.stroke();
      ctx.restore();
    } else {
      // 红色三连闪 vignette (收缩感, 不铺满全屏白闪)
      const pulse = Math.abs(Math.sin(age * Math.PI * 3.3));
      ctx.save();
      const g = ctx.createRadialGradient(
        s.projW / 2, s.projH / 2, Math.min(s.projW, s.projH) * 0.25,
        s.projW / 2, s.projH / 2, Math.max(s.projW, s.projH) * 0.72);
      g.addColorStop(0, 'rgba(235, 62, 75, 0)');
      g.addColorStop(1, `rgba(235, 62, 75, ${0.5 * pulse})`);
      ctx.fillStyle = g;
      ctx.fillRect(0, 0, s.projW, s.projH);
      ctx.restore();
    }
  }

  _drawAnchors() {
    // 白底 tile 直接 1:1 贴到投影空间坐标, 不加任何滤镜 (检测靠原始对比度)
    const { ctx } = this;
    for (const a of this.state.anchors) {
      if (!a.img || !a.img.complete) continue;
      const [x, y, size] = a.tile;
      ctx.drawImage(a.img, x, y, size, size);
    }
  }

  _drawConfirm(t, c) {
    const { ctx } = this;
    const [x, y, w, h] = c.rect;
    const progress = Math.max(0, Math.min(1, c.progress || 0));
    const pulse = 0.5 + 0.5 * Math.sin(t * Math.PI * 2 * 1.2);
    const active = progress > 0.02;

    // 按钮底板: 高亮度实底 (这是"传感器"—— 手要遮它, 必须够亮)
    ctx.save();
    ctx.beginPath();
    ctx.roundRect(x, y, w, h, 16);
    ctx.fillStyle = active ? 'rgba(255, 198, 58, 0.92)' : 'rgba(255, 198, 58, 0.78)';
    ctx.shadowColor = COLORS.amber;
    ctx.shadowBlur = 26 + 14 * pulse;
    ctx.fill();
    ctx.restore();

    // 进度环 (左侧)
    const ringR = h * 0.3;
    const ringCx = x + h * 0.5;
    const ringCy = y + h / 2;
    ctx.save();
    ctx.lineWidth = 6;
    ctx.strokeStyle = 'rgba(30, 22, 4, 0.35)';
    ctx.beginPath();
    ctx.arc(ringCx, ringCy, ringR, 0, Math.PI * 2);
    ctx.stroke();
    if (active) {
      ctx.strokeStyle = '#0b3d27';
      ctx.beginPath();
      ctx.arc(ringCx, ringCy, ringR, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * progress);
      ctx.stroke();
    } else {
      // 手形提示
      ctx.fillStyle = 'rgba(30, 22, 4, 0.8)';
      ctx.font = `500 ${Math.round(h * 0.34)}px sans-serif`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText('✋', ringCx, ringCy + 1);
    }
    ctx.restore();

    // 文案
    ctx.save();
    ctx.fillStyle = 'rgba(24, 18, 3, 0.95)';
    ctx.textAlign = 'left';
    ctx.textBaseline = 'middle';
    ctx.font = `600 ${Math.round(h * 0.3)}px "PingFang SC", "Microsoft YaHei", sans-serif`;
    ctx.fillText(c.label || '悬停确认', x + h * 0.95, y + h * 0.38);
    ctx.font = `400 ${Math.round(h * 0.19)}px "PingFang SC", "Microsoft YaHei", sans-serif`;
    ctx.fillStyle = 'rgba(24, 18, 3, 0.72)';
    ctx.fillText(c.sub || '手悬停在此区域确认', x + h * 0.95, y + h * 0.71);
    ctx.restore();
  }

  _drawStepRail(t, W, H) {
    const { ctx } = this;
    const steps = this.state.steps;
    const chipH = 40, gap = 12, padX = 20;
    ctx.save();
    ctx.font = '500 19px "PingFang SC", "Microsoft YaHei", sans-serif';
    const widths = steps.map((st) => ctx.measureText(st.display).width + padX * 2 + 26);
    const totalW = widths.reduce((a, b) => a + b, 0) + gap * (steps.length - 1);
    let x = Math.max(12, (W - totalW) / 2);
    const y = 18;
    for (let i = 0; i < steps.length; i++) {
      const st = steps[i];
      const w = widths[i];
      const isCurrent = st.status === 'current';
      const pulse = 0.5 + 0.5 * Math.sin(t * Math.PI * 2 * 1.1);
      ctx.beginPath();
      ctx.roundRect(x, y, w, chipH, 9);
      if (st.status === 'done') {
        ctx.fillStyle = 'rgba(18, 68, 46, 0.9)';
        ctx.strokeStyle = COLORS.chipDone;
      } else if (isCurrent) {
        ctx.fillStyle = 'rgba(6, 44, 58, 0.92)';
        ctx.strokeStyle = `rgba(0, 210, 255, ${0.55 + 0.45 * pulse})`;
      } else {
        ctx.fillStyle = 'rgba(16, 24, 30, 0.75)';
        ctx.strokeStyle = COLORS.chipPending;
      }
      ctx.lineWidth = isCurrent ? 3 : 1.5;
      if (isCurrent) {
        ctx.shadowColor = COLORS.accent;
        ctx.shadowBlur = 14 * pulse;
      }
      ctx.fill();
      ctx.stroke();
      ctx.shadowBlur = 0;
      // 序号点/对勾 + 文本
      const icon = st.status === 'done' ? '✓' : String(i + 1);
      ctx.fillStyle = st.status === 'done' ? COLORS.ok
        : isCurrent ? COLORS.accent : COLORS.textDim;
      ctx.textAlign = 'left';
      ctx.textBaseline = 'middle';
      ctx.fillText(icon, x + padX - 4, y + chipH / 2 + 1);
      ctx.fillStyle = st.status === 'pending' ? COLORS.textDim : COLORS.text;
      ctx.fillText(st.display, x + padX + 18, y + chipH / 2 + 1);
      x += w + gap;
    }
    ctx.restore();
  }

  _drawMessage(msg, W, H, t) {
    const { ctx } = this;
    const breath = 0.6 + 0.4 * Math.sin(t * 2);
    ctx.save();
    ctx.font = '400 17px "PingFang SC", "Microsoft YaHei", sans-serif';
    ctx.fillStyle = `rgba(150, 176, 190, ${breath})`;
    ctx.textAlign = 'center';
    ctx.fillText(msg, W / 2, H - 28);
    ctx.restore();
  }
}
