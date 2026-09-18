<template>
  <!-- ROI 编辑器对话框（2026-07 拆分批次 P-2 自 index.vue 外置；2026-09 增加 point/rect 模式；
       2026-09 多块 ROI 改造: polygon/rect 模式支持连续绘制任意多块） -->
  <el-dialog :model-value="visible"
    :title="title"
    width="80%" :close-on-click-modal="false" destroy-on-close
    class="roi-editor-dialog"
    @update:model-value="$emit('update:visible', $event)"
    @close="$emit('close')">
    <div class="space-y-3">
      <div class="flex items-center gap-3 text-sm">
        <span v-if="mode === 'polygon'" class="text-gray-400">单击添加顶点，点击<b class="text-amber-400">第一个点</b>闭合当前区块（靠近时会变绿）。闭合后可<b class="text-cyan-400">继续单击绘制下一块</b>，支持任意多块互不相连的区域</span>
        <span v-else-if="mode === 'point'" class="text-gray-400">在画面上<b class="text-amber-400">单击一次</b>标定目标位置（如仪表所在点），再次单击可重新标定</span>
        <span v-else class="text-gray-400">单击两次框定矩形区域（<b class="text-amber-400">第一次点左上角、第二次点右下角</b>），框定后可<b class="text-cyan-400">继续框选下一块</b>，支持任意多块</span>
        <div class="flex-1"></div>
        <el-button v-if="mode === 'polygon'" size="small" @click="roiUndoPoint" :disabled="roiPoints.length === 0">撤销上一点</el-button>
        <el-button v-if="mode !== 'point'" size="small" type="warning" plain @click="roiRemoveLastShape" :disabled="finishedShapes.length === 0">删除最后一块</el-button>
        <el-button size="small" type="warning" @click="roiClearPoints" :disabled="finishedShapes.length === 0 && roiPoints.length === 0">清除全部</el-button>
        <el-button v-if="mode === 'polygon'" size="small" type="success" @click="roiFinishPolygon" :disabled="roiPoints.length < 3">完成本块</el-button>
      </div>
      <div class="relative bg-black rounded overflow-hidden flex justify-center" style="max-height: 70vh;">
        <canvas ref="roiEditorCanvas" class="cursor-crosshair" style="max-width: 100%; max-height: 70vh; object-fit: contain;"
          @click="roiCanvasClick" @dblclick="roiCanvasDblClick" @mousemove="roiCanvasMouseMove"></canvas>
      </div>
      <div class="flex items-center gap-2 text-xs text-gray-500">
        <template v-if="mode === 'polygon'">
          <span v-if="finishedShapes.length" class="text-green-400 font-bold">已绘制 {{ finishedShapes.length }} 块区域</span>
          <span v-if="roiPoints.length">当前区块顶点数: {{ roiPoints.length }}</span>
          <span v-if="!finishedShapes.length && !roiPoints.length">未绘制</span>
        </template>
        <template v-else-if="mode === 'point'">
          <span v-if="roiPoints.length" class="text-green-400 font-bold">已标定 ({{ (roiPoints[0].x / (canvasW || 1)).toFixed(3) }}, {{ (roiPoints[0].y / (canvasH || 1)).toFixed(3) }})</span>
          <span v-else>未标定</span>
        </template>
        <template v-else>
          <span v-if="finishedShapes.length" class="text-green-400 font-bold">已框定 {{ finishedShapes.length }} 块区域</span>
          <span v-if="roiPoints.length === 1">已定第一角，点第二角完成本块</span>
          <span v-if="!finishedShapes.length && !roiPoints.length">未框定</span>
        </template>
      </div>
    </div>
    <template #footer>
      <el-button @click="$emit('update:visible', false)">取消</el-button>
      <el-button type="primary" @click="roiSave" :disabled="!saveEnabled">{{ mode === 'point' ? '保存标定点' : mode === 'rect' ? '保存区域' : '保存 ROI' }}</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
// ==================== ROI 编辑器（画布交互自 index.vue 平移） ====================
// 数据流约定: 本组件只管「取快照底图 + 画标注」；点保存时把归一化结果通过事件交回父级
// —— 写到哪个目标、是否立即触发保存项目, 路由逻辑全在父级。
// 三种模式 (2026-09):
//   polygon (默认) : 多边形, save 事件回传
//   point          : 单点标定 (facing_dwell 仪表点), save-point 事件回传 [nx, ny]
//   rect           : 两点矩形 (OCR 读字区 / 异常监测区), save-rect 事件回传
// 多块 ROI (2026-09 改造): polygon / rect 模式支持连续绘制任意多块。
//   保存格式约定 (与后端 normalize_polygons/normalize_rects 对齐):
//     只画 1 块 → 旧格式 ([[nx,ny],...] / [x,y,w,h]) —— 存量客户降级可回退
//     ≥2 块    → 新格式 ([[[nx,ny],...],...] / [[x,y,w,h],...])
// 打开流程: 父级设 visible=true → nextTick 等 canvas 挂载 → 调 load(channel, 初始标注)。
import { computed, ref } from 'vue';
import { getBackendHost } from '@/api/index';
import { normalizePolygons, normalizeRects } from '@/utils/polygons';

const props = defineProps({
  visible: { type: Boolean, required: true },
  title: { type: String, default: '绘制 ROI 检测区域' },
  mode: { type: String, default: 'polygon' },  // 'polygon' | 'point' | 'rect'
});
const emit = defineEmits(['update:visible', 'save', 'save-point', 'save-rect', 'close']);

// 多块颜色轮换 (完成块)
const SHAPE_COLORS = ['#00c8ff', '#22c55e', '#f97316', '#a855f7', '#eab308', '#ec4899'];

const roiEditorCanvas = ref(null);
const roiPoints = ref([]);          // 进行中的顶点 (polygon: 未闭合块 / rect: 第一角 / point: 标定点)
const finishedShapes = ref([]);     // 已完成的区块: polygon=[{x,y},...] / rect=[{x,y},{x,y}] (两角)
const canvasW = ref(0);
const canvasH = ref(0);
let roiImage = null;
let roiMousePos = null;

const ROI_CLOSE_RADIUS = 15;

const saveEnabled = computed(() => {
  if (props.mode === 'point') return roiPoints.value.length >= 1;
  if (props.mode === 'rect') return finishedShapes.value.length >= 1;
  // polygon: 已有完成块, 或进行中的块已够 3 点 (保存时自动闭合)
  return finishedShapes.value.length >= 1 || roiPoints.value.length >= 3;
});

// d1: Promise-based, 调用方 await 图片加载完再画 polygon (替代裸 setTimeout 时序坑).
// 2026-07 缺陷 A 修复: 通道由父级按「项目绑定的通道」解析后传入, 不再写死 0 号。
const loadRoiSnapshot = (channel = 0) => {
  return new Promise((resolve) => {
    const canvas = roiEditorCanvas.value;
    if (!canvas) { resolve(false); return; }
    const img = new Image();
    img.crossOrigin = 'anonymous';
    const host = getBackendHost();
    img.src = `${host}/snapshot?channel=${channel}&t=${Date.now()}`;
    img.onload = () => {
      roiImage = img;
      canvas.width = img.naturalWidth;
      canvas.height = img.naturalHeight;
      canvasW.value = canvas.width;
      canvasH.value = canvas.height;
      roiRedraw();
      resolve(true);
    };
    img.onerror = () => {
      const ctx = canvas.getContext('2d');
      canvas.width = 640;
      canvas.height = 480;
      canvasW.value = 640;
      canvasH.value = 480;
      ctx.fillStyle = '#1e293b';
      ctx.fillRect(0, 0, 640, 480);
      ctx.fillStyle = '#94a3b8';
      ctx.font = '16px Arial';
      ctx.textAlign = 'center';
      ctx.fillText('无法获取摄像头画面，请确保摄像头已连接', 320, 240);
      roiImage = null;
      resolve(false);
    };
  });
};

// 父级打开对话框后调用: 重置状态 → 取快照 → (可选)预加载已有标注
// existing 形态随 mode (单块/多块双格式均可):
//   polygon = [[nx,ny],...] 或 [[[nx,ny],...],...]
//   point   = [nx,ny]
//   rect    = [x,y,w,h] 或 [[x,y,w,h],...]
const load = async (channel = 0, existing = null) => {
  roiPoints.value = [];
  finishedShapes.value = [];
  roiMousePos = null;
  const ok = await loadRoiSnapshot(channel);
  if (!ok) return false;  // 快照失败时画兜底文字, 标注没意义不画
  const canvas = roiEditorCanvas.value;
  if (!canvas) return true;
  const w = canvas.width || 1;
  const h = canvas.height || 1;
  if (props.mode === 'point') {
    if (Array.isArray(existing) && existing.length >= 2 && Number.isFinite(Number(existing[0]))) {
      roiPoints.value = [{ x: Number(existing[0]) * w, y: Number(existing[1]) * h }];
      roiRedraw();
    }
  } else if (props.mode === 'rect') {
    finishedShapes.value = normalizeRects(existing).map(([rx, ry, rw, rh]) => ([
      { x: rx * w, y: ry * h }, { x: (rx + rw) * w, y: (ry + rh) * h },
    ]));
    roiRedraw();
  } else {
    finishedShapes.value = normalizePolygons(existing).map(
      (poly) => poly.map(([nx, ny]) => ({ x: nx * w, y: ny * h })));
    roiRedraw();
  }
  return true;
};

defineExpose({ load });

const roiGetCanvasXY = (e) => {
  const canvas = roiEditorCanvas.value;
  if (!canvas) return null;
  const rect = canvas.getBoundingClientRect();
  const scaleX = canvas.width / rect.width;
  const scaleY = canvas.height / rect.height;
  return {
    x: (e.clientX - rect.left) * scaleX,
    y: (e.clientY - rect.top) * scaleY
  };
};

const roiCanvasClick = (e) => {
  const pt = roiGetCanvasXY(e);
  if (!pt) return;

  if (props.mode === 'point') {
    roiPoints.value = [pt];  // 单点: 每次点击直接替换
    roiRedraw();
    return;
  }
  if (props.mode === 'rect') {
    roiPoints.value.push(pt);
    if (roiPoints.value.length >= 2) {
      // 两角凑齐 → 本块完成, 立即可继续框下一块
      const [a, b] = roiPoints.value;
      if (Math.abs(b.x - a.x) > 2 && Math.abs(b.y - a.y) > 2) {
        finishedShapes.value.push([a, b]);
      }
      roiPoints.value = [];
    }
    roiRedraw();
    return;
  }

  // polygon 模式: 靠近首点 → 闭合当前块, 之后继续点击开始画下一块
  if (roiPoints.value.length >= 3) {
    const first = roiPoints.value[0];
    const canvas = roiEditorCanvas.value;
    const rect = canvas.getBoundingClientRect();
    const scale = canvas.width / rect.width;
    const dist = Math.sqrt((pt.x - first.x) ** 2 + (pt.y - first.y) ** 2);
    if (dist < ROI_CLOSE_RADIUS * scale) {
      roiFinishPolygon();
      return;
    }
  }

  roiPoints.value.push(pt);
  roiRedraw();
};

const roiCanvasDblClick = (e) => {
  e.preventDefault();
  if (props.mode !== 'polygon') return;
  if (roiPoints.value.length >= 3) roiFinishPolygon();
};

const roiCanvasMouseMove = (e) => {
  if (props.mode === 'point') return;
  if (props.mode === 'rect' && roiPoints.value.length !== 1) return;
  if (props.mode === 'polygon' && roiPoints.value.length === 0) return;
  roiMousePos = roiGetCanvasXY(e);
  roiRedraw();
};

const roiUndoPoint = () => {
  if (roiPoints.value.length) {
    roiPoints.value.pop();
  } else if (finishedShapes.value.length && props.mode === 'polygon') {
    // 没有进行中的点时, 撤销 = 把最后一个完成块打回编辑态
    roiPoints.value = finishedShapes.value.pop();
  }
  roiRedraw();
};

const roiRemoveLastShape = () => {
  finishedShapes.value.pop();
  roiRedraw();
};

const roiClearPoints = () => {
  roiPoints.value = [];
  finishedShapes.value = [];
  roiMousePos = null;
  roiRedraw();
};

const roiFinishPolygon = () => {
  if (props.mode !== 'polygon' || roiPoints.value.length < 3) return;
  finishedShapes.value.push(roiPoints.value);
  roiPoints.value = [];
  roiMousePos = null;
  roiRedraw();
};

const _drawClosedPolygon = (ctx, pts, color, index) => {
  ctx.fillStyle = color + '26'; // ~15% alpha
  ctx.beginPath();
  ctx.moveTo(pts[0].x, pts[0].y);
  for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i].x, pts[i].y);
  ctx.closePath();
  ctx.fill();
  ctx.strokeStyle = color;
  ctx.lineWidth = 2;
  ctx.stroke();
  // 区块编号
  const cx = pts.reduce((s, p) => s + p.x, 0) / pts.length;
  const cy = pts.reduce((s, p) => s + p.y, 0) / pts.length;
  ctx.fillStyle = color;
  ctx.font = 'bold 14px Arial';
  ctx.textAlign = 'center';
  ctx.fillText(`区${index + 1}`, cx, cy);
  ctx.textAlign = 'start';
};

const _drawRectShape = (ctx, [a, b], color, index, dashed = false) => {
  const x = Math.min(a.x, b.x), y = Math.min(a.y, b.y);
  const rw = Math.abs(b.x - a.x), rh = Math.abs(b.y - a.y);
  ctx.fillStyle = color + '26';
  ctx.fillRect(x, y, rw, rh);
  ctx.strokeStyle = color;
  ctx.lineWidth = 2;
  ctx.setLineDash(dashed ? [6, 4] : []);
  ctx.strokeRect(x, y, rw, rh);
  ctx.setLineDash([]);
  if (index >= 0) {
    ctx.fillStyle = color;
    ctx.font = 'bold 14px Arial';
    ctx.textAlign = 'center';
    ctx.fillText(`区${index + 1}`, x + rw / 2, y + rh / 2);
    ctx.textAlign = 'start';
  }
};

const roiRedraw = () => {
  const canvas = roiEditorCanvas.value;
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  if (roiImage) {
    ctx.drawImage(roiImage, 0, 0);
  }

  const pts = roiPoints.value;

  // ---- point 模式: 十字准星 + 圆点
  if (props.mode === 'point') {
    if (!pts.length) return;
    const p = pts[0];
    ctx.strokeStyle = '#f59e0b';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(p.x - 18, p.y); ctx.lineTo(p.x + 18, p.y);
    ctx.moveTo(p.x, p.y - 18); ctx.lineTo(p.x, p.y + 18);
    ctx.stroke();
    ctx.fillStyle = '#f59e0b';
    ctx.beginPath();
    ctx.arc(p.x, p.y, 6, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = 'white';
    ctx.font = 'bold 12px Arial';
    ctx.fillText('目标点', p.x + 12, p.y - 8);
    return;
  }

  // ---- rect 模式: 已完成块 + 进行中预览 (第二角跟随鼠标)
  if (props.mode === 'rect') {
    finishedShapes.value.forEach((shape, i) => {
      _drawRectShape(ctx, shape, SHAPE_COLORS[i % SHAPE_COLORS.length], i);
    });
    if (pts.length === 1) {
      const b = roiMousePos;
      if (b) {
        _drawRectShape(ctx, [pts[0], b],
          SHAPE_COLORS[finishedShapes.value.length % SHAPE_COLORS.length], -1, true);
      }
      ctx.fillStyle = '#f59e0b';
      ctx.beginPath();
      ctx.arc(pts[0].x, pts[0].y, 5, 0, Math.PI * 2);
      ctx.fill();
    }
    return;
  }

  // ---- polygon 模式: 已完成块 (实线填充+编号) + 进行中块 (虚线)
  finishedShapes.value.forEach((shape, i) => {
    _drawClosedPolygon(ctx, shape, SHAPE_COLORS[i % SHAPE_COLORS.length], i);
  });

  if (pts.length === 0) return;

  const curColor = SHAPE_COLORS[finishedShapes.value.length % SHAPE_COLORS.length];
  ctx.strokeStyle = curColor;
  ctx.lineWidth = 2;
  ctx.setLineDash([6, 4]);
  ctx.beginPath();
  ctx.moveTo(pts[0].x, pts[0].y);
  for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i].x, pts[i].y);
  if (roiMousePos) ctx.lineTo(roiMousePos.x, roiMousePos.y);
  ctx.stroke();
  ctx.setLineDash([]);

  const nearFirst = pts.length >= 3 && roiMousePos &&
    Math.sqrt((roiMousePos.x - pts[0].x) ** 2 + (roiMousePos.y - pts[0].y) ** 2) < ROI_CLOSE_RADIUS * (canvas.width / (canvas.getBoundingClientRect().width || 1));

  pts.forEach((pt, i) => {
    const isFirst = i === 0;
    const radius = isFirst && nearFirst ? 10 : 5;
    ctx.fillStyle = isFirst ? (nearFirst ? '#22c55e' : '#f59e0b') : curColor;
    ctx.beginPath();
    ctx.arc(pt.x, pt.y, radius, 0, Math.PI * 2);
    ctx.fill();
    if (isFirst && nearFirst) {
      ctx.strokeStyle = '#22c55e';
      ctx.lineWidth = 2;
      ctx.stroke();
    }
    ctx.fillStyle = 'white';
    ctx.font = 'bold 11px Arial';
    ctx.fillText(isFirst && nearFirst ? '点击闭合' : `${i + 1}`, pt.x + 8, pt.y - 4);
  });
};

const roiSave = () => {
  const canvas = roiEditorCanvas.value;
  const w = canvas?.width || 1;
  const h = canvas?.height || 1;
  const r4 = (v) => Math.round(v * 10000) / 10000;

  if (props.mode === 'point') {
    if (roiPoints.value.length < 1) return;
    const p = roiPoints.value[0];
    emit('save-point', [r4(p.x / w), r4(p.y / h)]);
    return;
  }
  if (props.mode === 'rect') {
    const rects = finishedShapes.value.map(([a, b]) => {
      const x = Math.min(a.x, b.x) / w;
      const y = Math.min(a.y, b.y) / h;
      const rw = Math.abs(b.x - a.x) / w;
      const rh = Math.abs(b.y - a.y) / h;
      return [r4(x), r4(y), r4(rw), r4(rh)];
    }).filter(([, , rw, rh]) => rw > 0 && rh > 0);
    if (!rects.length) return;
    // 1 块 → 旧格式 [x,y,w,h]; 多块 → [[x,y,w,h],...]
    emit('save-rect', rects.length === 1 ? rects[0] : rects);
    return;
  }

  // polygon: 进行中的块 (≥3 点) 保存时自动闭合入列
  const shapes = [...finishedShapes.value];
  if (roiPoints.value.length >= 3) shapes.push(roiPoints.value);
  if (!shapes.length) return;
  const polys = shapes.map((shape) => shape.map(pt => [r4(pt.x / w), r4(pt.y / h)]));
  // 1 块 → 旧格式 [[nx,ny],...]; 多块 → [[[nx,ny],...],...]
  emit('save', polys.length === 1 ? polys[0] : polys);
};
</script>
