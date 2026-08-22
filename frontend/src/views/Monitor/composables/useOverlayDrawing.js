/**
 * useOverlayDrawing — 检测框/叠加层画布域（巨石重构阶段1⑤，v3.54.1 逐行等价移植）
 *
 * 职责：单工位 drawDetections + 多工位 drawMultiDetections 及共用叠加层——
 * v3.32 拆分区域/就位引导、v3.48 判型 positional 锁框、v3.49 虚拟按钮触发区、
 * per_item 覆盖态上色/编号、ROI 多边形（tracking/副模型/逐步骤）、mask 分割。
 *
 * ctx 依赖（index.vue 注入，函数体与内联版逐字节一致）：
 *   multiFrameNaturalSize / multiChannelData / currentProject / systemStore
 *   videoElement / detectionCanvas / perItemState / isPerItemMode
 */
import { ref } from 'vue';
import { getTriggers } from '@/api/triggers';
import { dbg } from '@/utils/debug';

export function useOverlayDrawing(ctx) {
  const {
    multiFrameNaturalSize, multiChannelData, currentProject, systemStore,
    videoElement, detectionCanvas, perItemState, isPerItemMode,
  } = ctx;

  // v3.5.2: 把归一化 bbox clip 到 [0, 1] 防越界, 兜底后端推理/Kalman 滤波偶发飘出
  // 即使后端漏 clip, 前端也保证框永远画在画面内
  const clipNormalizedBox = (det) => {
    const x = Math.max(0, Math.min(1, Number(det.x) || 0));
    const y = Math.max(0, Math.min(1, Number(det.y) || 0));
    const w = Math.max(0, Math.min(1 - x, Number(det.w) || 0));
    const h = Math.max(0, Math.min(1 - y, Number(det.h) || 0));
    return { x, y, w, h };
  };

  /** 归一化坐标下的射线法点在多边形内（含边界） */
  const pointInPolygonNorm = (px, py, poly) => {
    if (!poly || poly.length < 3) return true;
    let inside = false;
    const n = poly.length;
    for (let i = 0, j = n - 1; i < n; j = i++) {
      const xi = Number(poly[i][0]);
      const yi = Number(poly[i][1]);
      const xj = Number(poly[j][0]);
      const yj = Number(poly[j][1]);
      const denom = (yj - yi) || 1e-18;
      if (((yi > py) !== (yj > py)) && (px < ((xj - xi) * (py - yi)) / denom + xi)) {
        inside = !inside;
      }
    }
    return inside;
  };

  /**
   * pipeline_config.hide_boxes_outside_step_roi：对已配置步骤 ROI 的标签，
   * 框中心不在多边形内则不绘制（仅监视 UI）。
   */
  const shouldDrawDetWithStepRoi = (det, stepsConfig, pipelineConfig) => {
    const pc = pipelineConfig || {};
    if (!pc.hide_boxes_outside_step_roi) return true;
    const label = det.label;
    if (!label) return true;
    const step = (stepsConfig || []).find(s => s && s.label === label && s.enabled !== false);
    if (!step || !Array.isArray(step.roi) || step.roi.length < 3) return true;
    const cb = clipNormalizedBox(det);
    const cx = cb.x + cb.w / 2;
    const cy = cb.y + cb.h / 2;
    return pointInPolygonNorm(cx, cy, step.roi);
  };

  // ==================== v3.48 判型表 positional 锁定框叠加 ====================
  // 位置去重计数引擎的可视化: 已计数位置画常驻锁框+编号 (青色实线, 与当前帧检测框
  // 区分), 候选确认中画虚线+进度。数据来自 /detection/results 的
  // combo_verdict.positional_rois (归一化 xyxy), 空结果帧也要画 —— 锁定位置
  // 在检测框消失后仍然常驻到周期结算, 这正是"锁定"语义的可视化本体。
  // 单工位 drawDetections 与多工位 drawMultiDetections 共用, 坐标映射同拆分叠加层。
  const COMBO_LOCK_COLOR = '#22d3ee';     // 锁定 = 青色 (区别 OK 绿 / NG 红)
  const COMBO_PENDING_COLOR = '#94a3b8';  // 候选 = 灰

  const drawComboPositionalOverlay = (ctx, comboVerdict, dx, dy, dw, dh) => {
    // 显示开关 (combo_table.show_lock_overlay, 默认开): 关掉只是不画, 锁定/计数照常
    if (comboVerdict?.show_lock_overlay === false) return;
    const rois = comboVerdict?.positional_rois;
    if (!Array.isArray(rois) || rois.length === 0) return;
    const fs = 11 * (window.__uiScale || 1);
    ctx.save();
    ctx.font = `bold ${fs}px sans-serif`;
    for (const r of rois) {
      const b = r?.box;
      if (!Array.isArray(b) || b.length !== 4) continue;
      const x = dx + b[0] * dw, y = dy + b[1] * dh;
      const w = (b[2] - b[0]) * dw, h = (b[3] - b[1]) * dh;
      const locked = r.state === 'locked';
      ctx.strokeStyle = locked ? COMBO_LOCK_COLOR : COMBO_PENDING_COLOR;
      ctx.lineWidth = locked ? 2 : 1;
      ctx.setLineDash(locked ? [] : [5, 4]);
      ctx.strokeRect(x, y, w, h);
      const tag = locked ? `${r.label} #${r.seq}` : `${r.label} ${r.seen}/${r.need}`;
      const tw = ctx.measureText(tag).width;
      ctx.setLineDash([]);
      ctx.fillStyle = locked ? 'rgba(8,51,68,0.85)' : 'rgba(51,65,85,0.75)';
      ctx.fillRect(x, y - fs - 5, tw + 8, fs + 5);
      ctx.fillStyle = locked ? COMBO_LOCK_COLOR : '#cbd5e1';
      ctx.fillText(tag, x + 4, y - 4);
    }
    ctx.restore();
  };

  // ==================== v3.49 虚拟按钮触发区域叠加 ====================
  // 触发中心 pixel_region (虚拟按钮) 的标定区域在监控画面常驻显示 —— 操作员得
  // 知道往哪伸手 (2026-08-13 现场反馈"标定完看不见按钮在哪")。列表低频拉取
  // (区域/启用改动不频繁), 叠加画在检测框之下, 琥珀虚线框 + 名称。
  const TRIGGER_ZONE_COLOR = '#fbbf24';   // 琥珀 (区别锁框青 / OK绿 / NG红)
  const pixelTriggerZones = ref([]);      // [{name, region:[x1,y1,x2,y2], channel}]

  const loadTriggerZones = async () => {
    try {
      const res = await getTriggers();
      const rows = res.data?.triggers || [];
      pixelTriggerZones.value = rows
        .filter(t => t.enabled && t.type === 'pixel_region'
          && Array.isArray(t.params?.region) && t.params.region.length === 4)
        .map(t => ({
          name: t.name || '虚拟按钮',
          region: t.params.region.map(Number),
          channel: Number(t.params.channel || 0),
        }));
    } catch { /* 触发中心不可用不影响监控 */ }
  };

  // region 是原始帧像素坐标 (与 /snapshot 1:1), 按帧自然尺寸归一后映射到画布
  const drawTriggerZoneOverlay = (ctx, ch, dx, dy, dw, dh, natW, natH) => {
    if (!natW || !natH) return;
    const zones = pixelTriggerZones.value.filter(z => z.channel === ch);
    if (!zones.length) return;
    const fs = 12 * (window.__uiScale || 1);
    ctx.save();
    ctx.font = `bold ${fs}px sans-serif`;
    for (const z of zones) {
      const [x1, y1, x2, y2] = z.region;
      const x = dx + (x1 / natW) * dw, y = dy + (y1 / natH) * dh;
      const w = ((x2 - x1) / natW) * dw, h = ((y2 - y1) / natH) * dh;
      ctx.strokeStyle = TRIGGER_ZONE_COLOR;
      ctx.lineWidth = 2;
      ctx.setLineDash([7, 5]);
      ctx.strokeRect(x, y, w, h);
      ctx.setLineDash([]);
      ctx.globalAlpha = 0.12;
      ctx.fillStyle = TRIGGER_ZONE_COLOR;
      ctx.fillRect(x, y, w, h);
      ctx.globalAlpha = 1;
      const tag = z.name;
      const tw = ctx.measureText(tag).width;
      const ty = y > fs + 8 ? y - 4 : y + h + fs + 2;   // 顶部放不下就画在框下沿
      ctx.fillStyle = 'rgba(0,0,0,0.55)';
      ctx.fillRect(x, ty - fs - 1, tw + 8, fs + 5);
      ctx.fillStyle = TRIGGER_ZONE_COLOR;
      ctx.fillText(tag, x + 4, ty);
    }
    ctx.restore();
  };

  // ==================== v3.32 同标签区域拆分 / 工件就位提示 画布叠加 ====================
  // 单工位 drawDetections 与多工位 drawMultiDetections 共用。坐标映射由调用方传入
  // (dx,dy = letterbox 偏移, dw,dh = 实际渲染尺寸)。
  // - 拆分区域: fixed 直接画; anchor 用「当前帧锚点框 vs 标定框」平移缩放后画,
  //   本帧没检出锚点就不画 (后端引擎有 hold 缓存, 前端叠加层只做可视化, 缺帧可接受)
  // - 就位引导框: 已就位=绿实线, 未就位/锚点不可见=黄虚线 + 顶部提示文字
  const SPLIT_REGION_FALLBACK_COLORS = ['#f97316', '#22d3ee', '#a78bfa', '#84cc16', '#ec4899', '#facc15'];

  const drawLabelSplitOverlay = (ctx, pipeCfg, detections, guideState, dx, dy, dw, dh, roundsState = null) => {
    const pc = pipeCfg || {};
    const rules = Array.isArray(pc.label_splits) ? pc.label_splits : [];
    const mapX = (nx) => dx + nx * dw;
    const mapY = (ny) => dy + ny * dh;

    const drawPolygon = (poly, color, name, dashed = false, alpha = 0.10) => {
      if (!Array.isArray(poly) || poly.length < 3) return;
      ctx.save();
      ctx.beginPath();
      poly.forEach((p, i) => {
        const x = mapX(Number(p[0]) || 0), y = mapY(Number(p[1]) || 0);
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      });
      ctx.closePath();
      if (alpha > 0) {
        ctx.fillStyle = color;
        ctx.globalAlpha = alpha;
        ctx.fill();
        ctx.globalAlpha = 1;
      }
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      if (dashed) ctx.setLineDash([8, 5]);
      ctx.stroke();
      ctx.setLineDash([]);
      if (name) {
        const cx = poly.reduce((s, p) => s + (Number(p[0]) || 0), 0) / poly.length;
        const cy = poly.reduce((s, p) => s + (Number(p[1]) || 0), 0) / poly.length;
        const fs = 13 * (window.__uiScale || 1);
        ctx.font = `bold ${fs}px Arial`;
        const tw = ctx.measureText(name).width;
        const tx = mapX(cx) - tw / 2, ty = mapY(cy);
        ctx.fillStyle = 'rgba(0,0,0,0.5)';
        ctx.fillRect(tx - 3, ty - fs, tw + 6, fs + 6);
        ctx.fillStyle = color;
        ctx.fillText(name, tx, ty);
      }
      ctx.restore();
    };

    for (const rule of rules) {
      if (!rule || rule.enabled === false || !Array.isArray(rule.regions)) continue;
      let transform = null;
      if (rule.mode === 'anchor') {
        const ref = rule.anchor_ref;
        if (!ref || !(ref.w > 0) || !(ref.h > 0)) continue;
        let cur = null;
        for (const det of (detections || [])) {
          if (det.label === rule.anchor_label && !det.hidden) {
            if (!cur || (det.confidence || 0) > (cur.confidence || 0)) cur = det;
          }
        }
        if (!cur) continue;  // 锚点本帧不可见 → 区域位置未知, 不画
        const sx = (Number(cur.w) || 0) / ref.w;
        const sy = (Number(cur.h) || 0) / ref.h;
        transform = ([px, py]) => [
          (Number(cur.x) || 0) + (px - ref.x) * sx,
          (Number(cur.y) || 0) + (py - ref.y) * sy,
        ];
      }
      // v3.32 多轮次: 区域名前挂当前轮前缀 (运行态来自 /detection/results.label_split_rounds;
      // 检测未跑/轮次未开始时按第 1 轮前缀兜底, 与后端引擎同语义)
      let roundPrefix = '';
      let roundBadge = '';
      let regionsToDraw = rule.regions;
      if (rule.rounds && rule.rounds.enabled && Array.isArray(rule.rounds.prefixes) && rule.rounds.prefixes.length) {
        const rt = roundsState && roundsState[rule.source_label];
        roundPrefix = (rt && rt.prefix) || rule.rounds.prefixes[0] || '';
        const cur = rt && rt.round > 0 ? rt.round : 0;
        roundBadge = cur > 0
          ? `第${cur}/${rule.rounds.count}轮 · ${roundPrefix}`
          : `等待${rule.rounds.trigger_label || '切换标签'}开第1轮`;
        // 每轮独立区域: 当前轮配了 override 就画 override 的那批 (未开始按第1轮, 与后端引擎同语义)
        const ov = rule.rounds.region_overrides?.[String(cur > 0 ? cur : 1)];
        if (Array.isArray(ov) && ov.length) regionsToDraw = ov;
      }
      let badgeAnchor = null;
      regionsToDraw.forEach((region, i) => {
        if (!region || !Array.isArray(region.polygon) || region.polygon.length < 3) return;
        const poly = transform ? region.polygon.map(transform) : region.polygon;
        drawPolygon(poly, region.color || SPLIT_REGION_FALLBACK_COLORS[i % SPLIT_REGION_FALLBACK_COLORS.length],
          `${roundPrefix}${region.name || ''}`);
        if (!badgeAnchor) {
          for (const p of poly) {
            const px = Number(p[0]) || 0, py = Number(p[1]) || 0;
            if (!badgeAnchor || py < badgeAnchor[1]) badgeAnchor = [px, py];
          }
        }
      });
      if (roundBadge && badgeAnchor) {
        const fs = 13 * (window.__uiScale || 1);
        ctx.save();
        ctx.font = `bold ${fs}px Arial`;
        const tw = ctx.measureText(roundBadge).width;
        const tx = mapX(badgeAnchor[0]);
        const ty = Math.max(fs + 4, mapY(badgeAnchor[1]) - 8);
        ctx.fillStyle = 'rgba(0,0,0,0.6)';
        ctx.fillRect(tx - 4, ty - fs - 3, tw + 8, fs + 8);
        ctx.fillStyle = '#fbbf24';
        ctx.fillText(roundBadge, tx, ty);
        ctx.restore();
      }
    }

    // 就位引导框 (独立功能): 后端 /detection/results 的 placement_guide 运行态驱动颜色
    const pg = pc.placement_guide;
    if (pg && pg.enabled && Array.isArray(pg.polygon) && pg.polygon.length >= 3) {
      const inPos = !!(guideState && guideState.in_position);
      // 就位后显示策略 (未就位时永远完整显示): always=常驻 | fade_on_ready=淡化细框 | hide_on_ready=隐藏
      const display = pg.display || 'always';
      if (inPos && display === 'hide_on_ready') return;
      const faded = inPos && display === 'fade_on_ready';
      const color = inPos ? (faded ? 'rgba(34,197,94,0.35)' : '#22c55e') : '#facc15';
      drawPolygon(pg.polygon, color, '', !inPos, faded ? 0 : (inPos ? 0.06 : 0.10));
      if (faded) return;  // 淡化档: 只留半透明细框, 不挂文字
      // 提示文字挂在引导框最高点上方
      let topX = 0.5, topY = 1;
      for (const p of pg.polygon) {
        if ((Number(p[1]) || 0) < topY) { topY = Number(p[1]) || 0; topX = Number(p[0]) || 0; }
      }
      const msg = inPos ? '工件已就位'
        : (guideState && guideState.anchor_visible ? '请将工件放入引导框' : `等待工件（${pg.anchor_label || '锚点'}）就位`);
      const fs = 14 * (window.__uiScale || 1);
      ctx.save();
      ctx.font = `bold ${fs}px Arial`;
      const tw = ctx.measureText(msg).width;
      const tx = mapX(topX) - tw / 2;
      const ty = Math.max(fs + 6, mapY(topY) - 10);
      ctx.fillStyle = 'rgba(0,0,0,0.55)';
      ctx.fillRect(tx - 5, ty - fs - 3, tw + 10, fs + 8);
      ctx.fillStyle = color;
      ctx.fillText(msg, tx, ty);
      ctx.restore();
    }
  };

  const drawMultiDetections = (ch, canvas, detections, hiddenLabels = null, pollProjectConfig = null) => {
    if (!canvas) return;
    const parent = canvas.parentElement;
    if (parent) { canvas.width = parent.offsetWidth; canvas.height = parent.offsetHeight; }
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    const cw = canvas.width, ch2 = canvas.height;

    const nat = multiFrameNaturalSize[ch];
    let dx = 0, dy = 0, dw = cw, dh = ch2;
    if (nat && nat.w > 0 && nat.h > 0) {
      const scale = Math.min(cw / nat.w, ch2 / nat.h);
      dw = nat.w * scale;
      dh = nat.h * scale;
      dx = (cw - dw) / 2;
      dy = (ch2 - dh) / 2;
    }

    const stepsConfMulti = pollProjectConfig?.steps_config || currentProject.value?.steps_config || [];
    const pipeMulti = pollProjectConfig?.pipeline_config || currentProject.value?.pipeline_config || {};

    // v3.32: 拆分区域 / 就位引导框 叠加层 (画在检测框底下)
    drawLabelSplitOverlay(ctx, pipeMulti, detections, multiChannelData.value[ch]?.placementGuide, dx, dy, dw, dh,
      multiChannelData.value[ch]?.labelSplitRounds);
    // v3.48: 判型表 positional 锁定框 (常驻, 空结果帧也画)
    drawComboPositionalOverlay(ctx, multiChannelData.value[ch]?.comboVerdict, dx, dy, dw, dh);
    // v3.49: 虚拟按钮触发区域 (常驻)
    drawTriggerZoneOverlay(ctx, ch, dx, dy, dw, dh, nat?.w, nat?.h);

    // v3.8.x: 多工位画框也读用户配置 (老逻辑硬编码 #10b981/#ef4444、线宽2、字号11,
    // 客户在设置页改的检测框颜色/线宽/字号在多工位下全部失效).
    // 优先取该通道独立配置, 没配就回退全局检测配置.
    const chDet = systemStore.getChannelDetection(ch) || systemStore.detection;
    const okColorMulti = chDet.boxColor || '#10b981';
    const ngColorMulti = chDet.boxColorNG || '#ef4444';
    const lineWidthMulti = Number(chDet.boxLineWidth) || 2;
    const fontSizeMulti = (Number(chDet.labelFontSize) || 11) * (window.__uiScale || 1);
    const showConfMulti = chDet.showConfidence !== false;

    // v3.28+ per_item: 按覆盖态上色 + 显示螺丝编号 (复用单工位同一套个体配对)
    const piStateMulti = multiChannelData.value[ch]?.perItemState || null;
    const piCfgMulti = piStateMulti?.config;
    const colorByCovMulti = !!piCfgMulti?.color_by_coverage;
    const showNumMulti = !!piCfgMulti?.show_item_numbers;
    let coverByLabelMulti = null;
    let covOnMulti = okColorMulti, covOffMulti = ngColorMulti;
    if ((colorByCovMulti || showNumMulti) && Array.isArray(piStateMulti?.steps)) {
      coverByLabelMulti = new Map();
      for (const st of piStateMulti.steps) {
        if (!st || !st.item_label || !Array.isArray(st.items)) continue;
        let arr = coverByLabelMulti.get(st.item_label);
        if (!arr) { arr = []; coverByLabelMulti.set(st.item_label, arr); }
        for (const it of st.items) {
          // associated=false 表示只有整板平移后的预测位置，本帧没有检测目标
          // 与该逻辑 ID 完成一对一关联；不得借预测框显示编号或绿色。
          if (it.associated !== false && Array.isArray(it.bbox) && it.bbox.length === 4) arr.push(it);
        }
      }
      if (coverByLabelMulti.size === 0) coverByLabelMulti = null;
      covOnMulti = piCfgMulti.box_color_covered || okColorMulti;
      covOffMulti = piCfgMulti.box_color_uncovered || ngColorMulti;
    }
    const piHitMulti = (det, cb) => {
      if (!coverByLabelMulti) return null;
      const items = coverByLabelMulti.get(det.label);
      if (!items || !items.length) return null;
      let best = 0.3, hit = null;
      for (const it of items) {
        const b = it.bbox;
        const ix1 = Math.max(cb.x, b[0]), iy1 = Math.max(cb.y, b[1]);
        const ix2 = Math.min(cb.x + cb.w, b[0] + b[2]), iy2 = Math.min(cb.y + cb.h, b[1] + b[3]);
        const iw = Math.max(0, ix2 - ix1), ih = Math.max(0, iy2 - iy1);
        const inter = iw * ih, uni = cb.w * cb.h + b[2] * b[3] - inter;
        const iou = uni > 0 ? inter / uni : 0;
        if (iou > best) { best = iou; hit = it; }
      }
      return hit;
    };

    detections.forEach(det => {
      if (det.hidden) return;
      if (hiddenLabels && det.label && hiddenLabels.has(det.label)) return;
      if (!shouldDrawDetWithStepRoi(det, stepsConfMulti, pipeMulti)) return;
      const cb = clipNormalizedBox(det);
      const x = cb.x * dw + dx, y = cb.y * dh + dy;
      const w = cb.w * dw, h = cb.h * dh;
      const hitMulti = (colorByCovMulti || showNumMulti) ? piHitMulti(det, cb) : null;
      const isPerItemTargetMulti = colorByCovMulti && !!coverByLabelMulti?.has(det.label);
      const color = isPerItemTargetMulti
        ? (hitMulti?.covered ? covOnMulti : covOffMulti)
        : pickDetColor(det, stepsConfMulti, okColorMulti, ngColorMulti);
      if (det.mask && Array.isArray(det.mask) && det.mask.length > 2) {
        ctx.beginPath();
        det.mask.forEach((pt, i) => {
          const mxN = Math.max(0, Math.min(1, Number(pt[0]) || 0));
          const myN = Math.max(0, Math.min(1, Number(pt[1]) || 0));
          const mx = mxN * dw + dx, my = myN * dh + dy;
          if (i === 0) ctx.moveTo(mx, my); else ctx.lineTo(mx, my);
        });
        ctx.closePath();
        ctx.save();
        ctx.fillStyle = color;
        ctx.globalAlpha = 0.25;
        ctx.fill();
        ctx.restore();
        ctx.strokeStyle = color;
        ctx.lineWidth = lineWidthMulti;
        ctx.stroke();
      } else {
        ctx.strokeStyle = color;
        ctx.lineWidth = lineWidthMulti;
        ctx.strokeRect(x, y, w, h);
      }
      ctx.font = `bold ${fontSizeMulti}px Arial`;
      let label = det.display_id || det.display_name || det.label || '';
      if (showNumMulti && hitMulti) label = `${det.label || ''}#${hitMulti.id}`;
      if (showConfMulti && det.confidence) label += ` ${(det.confidence * 100).toFixed(0)}%`;
      const lh = Math.max(12, fontSizeMulti + 2);
      ctx.fillStyle = color;
      ctx.fillRect(x, y - lh, ctx.measureText(label).width + 6, lh);
      ctx.fillStyle = 'white';
      ctx.fillText(label, x + 3, y - 4);
    });
  };

  // v3.7.5: 单条 detection 的检测框颜色优先级 (改自 v3.7.2 FIX-381-B).
  //   1) steps_config 里该 label 配置的 box_color (用户明确意图最高优先级)
  //   2) 副模型 display_color (model_name != 'main' 且后端注入了 display_color)
  //   3) 全局 OK / NG 兜底 (来自 systemStore.detection 或 multi 版本的硬编码)
  // 注: 老版优先级 (副模型色 > 步骤色) 让客户在步骤列表给副模型 label 配的颜色
  // 形同摆设, tooltip 又写"任何模式都生效", 自相矛盾. 翻过来后用户配啥就是啥.
  const pickDetColor = (det, stepsConfig, fallbackOK, fallbackNG) => {
    if (stepsConfig && det && det.label) {
      const cfg = stepsConfig.find(s => s && s.label === det.label);
      if (cfg && cfg.box_color) return cfg.box_color;
    }
    if (det && det.model_name && det.model_name !== 'main' && det.display_color) {
      return det.display_color;
    }
    return det && det.is_ng ? fallbackNG : fallbackOK;
  };

  // 叠加层闪烁诊断: 记"画布尺寸重置"和"有框/空结果"边沿 (调试设置「视频流」开关)
  let _ovlResizeWxH = '';
  let _ovlHadBoxes = false;
  const resizeCanvas = () => {
    if (!videoElement.value || !detectionCanvas.value) return;
  
    const video = videoElement.value;
    const canvas = detectionCanvas.value;
  
    canvas.width = video.offsetWidth;
    canvas.height = video.offsetHeight;

    const _wh = `${canvas.width}x${canvas.height}`;
    if (_wh !== _ovlResizeWxH) {
      dbg('monitor.video', '画布尺寸重置', `${_ovlResizeWxH || '初始'}→${_wh} (重置会清空叠加框→闪一下没框)`);
      _ovlResizeWxH = _wh;
    }
  };

  // 绘制检测框
  const drawDetections = (detections) => {
    if (!detectionCanvas.value) return;
  
    const canvas = detectionCanvas.value;
    const ctx = canvas.getContext('2d');
  
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    // 闪烁边沿: 有框↔空结果切换才记一条 (6.7Hz 轮询, 不能逐帧打)
    const _has = !!(detections && detections.length);
    if (_has !== _ovlHadBoxes) {
      dbg('monitor.video', _has ? '叠加层恢复画框' : '叠加层清空(空结果)',
          `检出=${detections ? detections.length : 0} (空结果清框=画面闪烁直接元凶, 看推理是否间歇空)`);
      _ovlHadBoxes = _has;
    }

    // v3.32: 拆分区域/就位引导框常驻叠加层 — 在检测框之前画(垫底), 且空结果帧也要画
    {
      const img0 = videoElement.value;
      let ox = 0, oy = 0, rw = canvas.width, rh = canvas.height;
      if (img0 && img0.naturalWidth && img0.naturalHeight) {
        const ia = img0.naturalWidth / img0.naturalHeight;
        const ca = canvas.width / canvas.height;
        if (ia > ca) { rw = canvas.width; rh = canvas.width / ia; oy = (canvas.height - rh) / 2; }
        else { rh = canvas.height; rw = canvas.height * ia; ox = (canvas.width - rw) / 2; }
      }
      drawLabelSplitOverlay(ctx, currentProject.value?.pipeline_config,
        detections, multiChannelData.value[0]?.placementGuide, ox, oy, rw, rh,
        multiChannelData.value[0]?.labelSplitRounds);
      // v3.48: 判型表 positional 锁定框 (常驻叠加层, 在提前 return 之前 —— 空结果帧
      // 锁框也要画, "检测框消失后位置仍锁定"正是要给现场看的语义)
      drawComboPositionalOverlay(ctx, multiChannelData.value[0]?.comboVerdict, ox, oy, rw, rh);
      // v3.49: 虚拟按钮触发区域 (常驻, 操作员要看得见往哪伸手)
      drawTriggerZoneOverlay(ctx, 0, ox, oy, rw, rh,
        img0?.naturalWidth, img0?.naturalHeight);
    }

    if (!detections || detections.length === 0) return;
  
    // 获取启用的步骤标签列表
    const stepsConfig = currentProject.value?.steps_config || [];
    const enabledLabels = new Set(
      stepsConfig
        .filter(s => s.enabled !== false)  // 默认启用
        .map(s => s.label)
    );
    // v2.7.4: 项目配置中标记 hide_in_view=true 的标签，画面上不画框（仅视觉隐藏）
    const hiddenLabels = new Set(
      stepsConfig.filter(s => s && s.hide_in_view && s.label).map(s => s.label)
    );
  
    const boxColor = systemStore.detection.boxColor;
    const ngColor = systemStore.detection.boxColorNG;
    const lineWidth = systemStore.detection.boxLineWidth;
    const fontSize = systemStore.detection.labelFontSize * (window.__uiScale || 1);
    const showConf = systemStore.detection.showConfidence;
  
    // object-contain offset: compute actual rendered image area within canvas
    const img = videoElement.value;
    let offsetX = 0, offsetY = 0, renderW = canvas.width, renderH = canvas.height;
    if (img && img.naturalWidth && img.naturalHeight) {
      const imgAspect = img.naturalWidth / img.naturalHeight;
      const canvasAspect = canvas.width / canvas.height;
      if (imgAspect > canvasAspect) {
        renderW = canvas.width;
        renderH = canvas.width / imgAspect;
        offsetY = (canvas.height - renderH) / 2;
      } else {
        renderH = canvas.height;
        renderW = canvas.height * imgAspect;
        offsetX = (canvas.width - renderW) / 2;
      }
    }

    const pipeCfg = currentProject.value?.pipeline_config || {};

    // v3.x per_item: 按"扭完/未扭"覆盖态给 item_label 检测框上色.
    // 默认关 (color_by_coverage=false) → 零行为变化; 颜色留空回退全局 OK/NG 色.
    const piState = perItemState.value;
    const piCfg = piState?.config;
    const colorByCoverage = isPerItemMode.value && !!piCfg?.color_by_coverage;
    const showItemNumbers = isPerItemMode.value && !!piCfg?.show_item_numbers;
    let coverByLabel = null;
    let covColorOn = boxColor;
    let covColorOff = ngColor;
    if ((colorByCoverage || showItemNumbers) && Array.isArray(piState?.steps)) {
      coverByLabel = new Map();
      for (const st of piState.steps) {
        if (!st || !st.item_label || !Array.isArray(st.items)) continue;
        let arr = coverByLabel.get(st.item_label);
        if (!arr) { arr = []; coverByLabel.set(st.item_label, arr); }
        for (const it of st.items) {
          if (it.associated !== false && Array.isArray(it.bbox) && it.bbox.length === 4) arr.push(it);
        }
      }
      if (coverByLabel.size === 0) coverByLabel = null;
      covColorOn = piCfg.box_color_covered || boxColor;
      covColorOff = piCfg.box_color_uncovered || ngColor;
    }
    const _iouNorm = (ax, ay, aw, ah, bx, by, bw, bh) => {
      const ix1 = Math.max(ax, bx), iy1 = Math.max(ay, by);
      const ix2 = Math.min(ax + aw, bx + bw), iy2 = Math.min(ay + ah, by + bh);
      const iw = Math.max(0, ix2 - ix1), ih = Math.max(0, iy2 - iy1);
      const inter = iw * ih;
      const uni = aw * ah + bw * bh - inter;
      return uni > 0 ? inter / uni : 0;
    };
    // 把检测框配到对应个体 (返回该个体: 含 id + covered), 编号/上色共用
    const perItemHitFor = (det, cb) => {
      if (!coverByLabel) return null;
      const items = coverByLabel.get(det.label);
      if (!items || !items.length) return null;
      let best = 0.3, hit = null;
      for (const it of items) {
        const b = it.bbox;
        const iou = _iouNorm(cb.x, cb.y, cb.w, cb.h, b[0], b[1], b[2], b[3]);
        if (iou > best) { best = iou; hit = it; }
      }
      return hit;
    };

    detections.forEach(det => {
      if (!enabledLabels.has(det.label)) return;
      if (det.hidden) return;
      if (hiddenLabels.has(det.label)) return;
      if (!shouldDrawDetWithStepRoi(det, stepsConfig, pipeCfg)) return;
      const cb = clipNormalizedBox(det);
      const x = offsetX + cb.x * renderW;
      const y = offsetY + cb.y * renderH;
      const w = cb.w * renderW;
      const h = cb.h * renderH;
    
      // v3.7.5: 颜色优先级 步骤 box_color > 副模型 display_color > OK/NG 兜底.
      // (v3.7.2 FIX-381-B 原先把副模型色放最高, 但客户在步骤列表配的颜色被覆盖,
      // 与 tooltip 文案"任何模式都生效"矛盾, 现翻转优先级让用户配置说了算.)
      // v3.x per_item: color_by_coverage 开启时, item_label 框按覆盖态优先上色.
      const piHit = (colorByCoverage || showItemNumbers) ? perItemHitFor(det, cb) : null;
      const isPerItemTarget = colorByCoverage && !!coverByLabel?.has(det.label);
      // per_item 目标只存在两种合法颜色：已关联且已覆盖=绿；其余=红。
      // 特别是找不到逻辑 ID 的检测框，禁止回退模型/步骤默认绿色。
      const color = isPerItemTarget
        ? (piHit?.covered ? covColorOn : covColorOff)
        : pickDetColor(det, stepsConfig, boxColor, ngColor);

      // Render polygon mask if available (segmentation model)
      if (det.mask && Array.isArray(det.mask) && det.mask.length > 2) {
        ctx.beginPath();
        det.mask.forEach((pt, i) => {
          const mxN = Math.max(0, Math.min(1, Number(pt[0]) || 0));
          const myN = Math.max(0, Math.min(1, Number(pt[1]) || 0));
          const mx = offsetX + mxN * renderW;
          const my = offsetY + myN * renderH;
          if (i === 0) ctx.moveTo(mx, my);
          else ctx.lineTo(mx, my);
        });
        ctx.closePath();
        // Step 8: color 可能是 hex (#xxxxxx) 或 rgb(...). hex 不能直接 replace 成 rgba,
        // 用 globalAlpha 兼容两种格式.
        ctx.save();
        ctx.fillStyle = color;
        ctx.globalAlpha = 0.25;
        ctx.fill();
        ctx.restore();
        ctx.strokeStyle = color;
        ctx.lineWidth = lineWidth;
        ctx.stroke();
      } else {
        ctx.strokeStyle = color;
        ctx.lineWidth = lineWidth;
        ctx.strokeRect(x, y, w, h);
      }
    
      // In tracking mode, show display_id (A1, B2, etc.) as the label
      let label = det.display_id || det.display_name || det.label || 'Unknown';
      // v3.28+ per_item: 显示螺丝编号 → 同标签各自从 #1 起 (如 5N螺丝#3 / 7N螺丝#1)
      if (showItemNumbers && piHit) {
        label = `${det.label || ''}#${piHit.id}`;
      }
      if (showConf && det.confidence) {
        label += ` ${(det.confidence * 100).toFixed(0)}%`;
      }
    
      // 绘制标签背景
      ctx.font = `bold ${fontSize}px Arial`;
      const textMetrics = ctx.measureText(label);
      const textHeight = fontSize;
    
      ctx.fillStyle = color;
      ctx.fillRect(x, y - textHeight - 4, textMetrics.width + 8, textHeight + 4);
    
      // 绘制标签文字
      ctx.fillStyle = 'white';
      ctx.fillText(label, x + 4, y - 4);
    });

    // Draw ROI polygon overlay if configured (tracking mode)
    const roiPoly = currentProject.value?.pipeline_config?.tracking_roi?.polygon;
    if (roiPoly && roiPoly.length >= 3) {
      ctx.save();
      ctx.strokeStyle = 'rgba(0, 200, 255, 0.6)';
      ctx.lineWidth = 2;
      ctx.setLineDash([8, 4]);
      ctx.beginPath();
      ctx.moveTo(offsetX + roiPoly[0][0] * renderW, offsetY + roiPoly[0][1] * renderH);
      for (let i = 1; i < roiPoly.length; i++) {
        ctx.lineTo(offsetX + roiPoly[i][0] * renderW, offsetY + roiPoly[i][1] * renderH);
      }
      ctx.closePath();
      ctx.stroke();
      ctx.fillStyle = 'rgba(0, 200, 255, 0.05)';
      ctx.fill();
      ctx.setLineDash([]);
      ctx.restore();
    }

    // Step 8 (feat/multi-model-roi-link): 副模型 ROI 多边形叠加 (各自 display_color 虚线描边).
    // 主模型 tracking_roi 已上面画完, 副模型 ROI 单独画一圈让用户清楚每个副 slot 的工作区.
    const extraRois = (currentProject.value?.pipeline_config?.models || [])
      .filter(m => m && m.name && m.name !== 'main' && Array.isArray(m.roi) && m.roi.length >= 3);
    for (const slot of extraRois) {
      ctx.save();
      ctx.strokeStyle = slot.display_color || '#f59e0b';
      ctx.lineWidth = 2;
      ctx.setLineDash([4, 6]);
      ctx.beginPath();
      ctx.moveTo(offsetX + slot.roi[0][0] * renderW, offsetY + slot.roi[0][1] * renderH);
      for (let i = 1; i < slot.roi.length; i++) {
        ctx.lineTo(offsetX + slot.roi[i][0] * renderW, offsetY + slot.roi[i][1] * renderH);
      }
      ctx.closePath();
      ctx.stroke();
      ctx.fillStyle = slot.display_color || '#f59e0b';
      ctx.globalAlpha = 0.05;
      ctx.fill();
      ctx.setLineDash([]);
      ctx.restore();
    }

    // 逐步骤 ROI (steps_config[].roi): 浅色虚线 + 微弱填充，与 tracking_roi / 副模型 ROI 区分
    const stepRois = (currentProject.value?.steps_config || []).filter(
      s => s && s.enabled !== false && Array.isArray(s.roi) && s.roi.length >= 3
    );
    const STEP_ROI_PALETTE = ['#c4b5fd', '#6ee7b7', '#fcd34d', '#f9a8d4', '#7dd3fc'];
    stepRois.forEach((s, idx) => {
      const col = STEP_ROI_PALETTE[idx % STEP_ROI_PALETTE.length];
      ctx.save();
      ctx.strokeStyle = col;
      ctx.lineWidth = 1.5;
      ctx.setLineDash([3, 5]);
      ctx.beginPath();
      ctx.moveTo(offsetX + s.roi[0][0] * renderW, offsetY + s.roi[0][1] * renderH);
      for (let i = 1; i < s.roi.length; i++) {
        ctx.lineTo(offsetX + s.roi[i][0] * renderW, offsetY + s.roi[i][1] * renderH);
      }
      ctx.closePath();
      ctx.stroke();
      ctx.fillStyle = col;
      ctx.globalAlpha = 0.04;
      ctx.fill();
      ctx.setLineDash([]);
      ctx.restore();
    });
  };
  return { loadTriggerZones, drawMultiDetections, resizeCanvas, drawDetections, pickDetColor };
}
