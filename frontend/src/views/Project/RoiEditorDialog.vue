<template>
  <!-- ROI 编辑器对话框（2026-07 拆分批次 P-2 自 index.vue 外置；2026-09 增加 point/rect 模式） -->
  <el-dialog :model-value="visible"
    :title="title"
    width="80%" :close-on-click-modal="false" destroy-on-close
    class="roi-editor-dialog"
    @update:model-value="$emit('update:visible', $event)"
    @close="$emit('close')">
    <div class="space-y-3">
      <div class="flex items-center gap-3 text-sm">
        <span v-if="mode === 'polygon'" class="text-gray-400">单击添加顶点，点击<b class="text-amber-400">第一个点</b>闭合多边形（靠近时会变绿）。闭合后再次单击可重新绘制</span>
        <span v-else-if="mode === 'point'" class="text-gray-400">在画面上<b class="text-amber-400">单击一次</b>标定目标位置（如仪表所在点），再次单击可重新标定</span>
        <span v-else class="text-gray-400">单击两次框定矩形区域（<b class="text-amber-400">第一次点左上角、第二次点右下角</b>），框定后再次单击可重新绘制</span>
        <div class="flex-1"></div>
        <el-button v-if="mode === 'polygon'" size="small" @click="roiUndoPoint" :disabled="roiPoints.length === 0">撤销上一点</el-button>
        <el-button size="small" type="warning" @click="roiClearPoints" :disabled="roiPoints.length === 0">清除全部</el-button>
        <el-button v-if="mode === 'polygon'" size="small" type="success" @click="roiFinishPolygon" :disabled="roiPoints.length < 3">完成绘制</el-button>
      </div>
      <div class="relative bg-black rounded overflow-hidden flex justify-center" style="max-height: 70vh;">
        <canvas ref="roiEditorCanvas" class="cursor-crosshair" style="max-width: 100%; max-height: 70vh; object-fit: contain;"
          @click="roiCanvasClick" @dblclick="roiCanvasDblClick" @mousemove="roiCanvasMouseMove"></canvas>
      </div>
      <div class="flex items-center gap-2 text-xs text-gray-500">
        <template v-if="mode === 'polygon'">
          <span>顶点数: {{ roiPoints.length }}</span>
          <span v-if="roiPolygonClosed" class="text-green-400 font-bold">多边形已闭合</span>
        </template>
        <template v-else-if="mode === 'point'">
          <span v-if="roiPoints.length" class="text-green-400 font-bold">已标定 ({{ (roiPoints[0].x / (canvasW || 1)).toFixed(3) }}, {{ (roiPoints[0].y / (canvasH || 1)).toFixed(3) }})</span>
          <span v-else>未标定</span>
        </template>
        <template v-else>
          <span v-if="roiPoints.length >= 2" class="text-green-400 font-bold">矩形已框定</span>
          <span v-else-if="roiPoints.length === 1">已定第一角，点第二角完成</span>
          <span v-else>未框定</span>
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
//   polygon (默认) : 多边形, save 事件回传 [[nx,ny],...] —— 历史行为零差异
//   point          : 单点标定 (facing_dwell 仪表点), save-point 事件回传 [nx, ny]
//   rect           : 两点矩形 (OCR 读字区 / 异常监测区), save-rect 事件回传 [x, y, w, h]
// 打开流程: 父级设 visible=true → nextTick 等 canvas 挂载 → 调 load(channel, 初始标注)。
import { computed, ref } from 'vue';
import { getBackendHost } from '@/api/index';

const props = defineProps({
  visible: { type: Boolean, required: true },
  title: { type: String, default: '绘制 ROI 检测区域' },
  mode: { type: String, default: 'polygon' },  // 'polygon' | 'point' | 'rect'
});
const emit = defineEmits(['update:visible', 'save', 'save-point', 'save-rect', 'close']);

const roiEditorCanvas = ref(null);
const roiPoints = ref([]);
const roiPolygonClosed = ref(false);
const canvasW = ref(0);
const canvasH = ref(0);
let roiImage = null;
let roiMousePos = null;

const ROI_CLOSE_RADIUS = 15;

const saveEnabled = computed(() => {
  if (props.mode === 'point') return roiPoints.value.length >= 1;
  if (props.mode === 'rect') return roiPoints.value.length >= 2;
  return roiPoints.value.length >= 3;
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
// existing 形态随 mode: polygon=[[nx,ny],...] / point=[nx,ny] / rect=[x,y,w,h]
const load = async (channel = 0, existing = null) => {
  roiPoints.value = [];
  roiPolygonClosed.value = false;
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
    if (Array.isArray(existing) && existing.length >= 4 && Number(existing[2]) > 0) {
      const [rx, ry, rw, rh] = existing.map(Number);
      roiPoints.value = [{ x: rx * w, y: ry * h }, { x: (rx + rw) * w, y: (ry + rh) * h }];
      roiRedraw();
    }
  } else if (Array.isArray(existing) && existing.length >= 3) {
    roiPoints.value = existing.map(([nx, ny]) => ({ x: nx * w, y: ny * h }));
    roiPolygonClosed.value = true;
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
    if (roiPoints.value.length >= 2) roiPoints.value = [];  // 已框定 → 重画
    roiPoints.value.push(pt);
    roiRedraw();
    return;
  }

  if (roiPolygonClosed.value) {
    roiPoints.value = [];
    roiPolygonClosed.value = false;
    roiRedraw();
    return;
  }

  if (roiPoints.value.length >= 3) {
    const first = roiPoints.value[0];
    const canvas = roiEditorCanvas.value;
    const rect = canvas.getBoundingClientRect();
    const scale = canvas.width / rect.width;
    const dist = Math.sqrt((pt.x - first.x) ** 2 + (pt.y - first.y) ** 2);
    if (dist < ROI_CLOSE_RADIUS * scale) {
      roiPolygonClosed.value = true;
      roiRedraw();
      return;
    }
  }

  roiPoints.value.push(pt);
  roiRedraw();
};

const roiCanvasDblClick = (e) => {
  e.preventDefault();
  if (props.mode !== 'polygon') return;
  if (roiPoints.value.length >= 3 && !roiPolygonClosed.value) {
    roiPolygonClosed.value = true;
    roiRedraw();
  }
};

const roiCanvasMouseMove = (e) => {
  if (props.mode === 'point') return;
  if (props.mode === 'rect' && roiPoints.value.length !== 1) return;
  if (props.mode === 'polygon' && roiPolygonClosed.value) return;
  roiMousePos = roiGetCanvasXY(e);
  roiRedraw();
};

const roiUndoPoint = () => {
  if (roiPolygonClosed.value) {
    roiPolygonClosed.value = false;
  } else {
    roiPoints.value.pop();
  }
  roiRedraw();
};

const roiClearPoints = () => {
  roiPoints.value = [];
  roiPolygonClosed.value = false;
  roiMousePos = null;
  roiRedraw();
};

const roiFinishPolygon = () => {
  if (roiPoints.value.length >= 3) {
    roiPolygonClosed.value = true;
    roiRedraw();
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

  // ---- rect 模式: 两角矩形 (第二角未定时跟随鼠标预览)
  if (props.mode === 'rect') {
    if (!pts.length) return;
    const a = pts[0];
    const b = pts.length >= 2 ? pts[1] : roiMousePos;
    if (b) {
      const x = Math.min(a.x, b.x), y = Math.min(a.y, b.y);
      const rw = Math.abs(b.x - a.x), rh = Math.abs(b.y - a.y);
      ctx.fillStyle = 'rgba(0, 200, 255, 0.15)';
      ctx.fillRect(x, y, rw, rh);
      ctx.strokeStyle = '#00c8ff';
      ctx.lineWidth = 2;
      ctx.setLineDash(pts.length >= 2 ? [] : [6, 4]);
      ctx.strokeRect(x, y, rw, rh);
      ctx.setLineDash([]);
    }
    ctx.fillStyle = '#f59e0b';
    pts.forEach(p => {
      ctx.beginPath();
      ctx.arc(p.x, p.y, 5, 0, Math.PI * 2);
      ctx.fill();
    });
    return;
  }

  // ---- polygon 模式 (历史行为)
  if (pts.length === 0) return;

  if (roiPolygonClosed.value && pts.length >= 3) {
    ctx.fillStyle = 'rgba(0, 200, 255, 0.15)';
    ctx.beginPath();
    ctx.moveTo(pts[0].x, pts[0].y);
    for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i].x, pts[i].y);
    ctx.closePath();
    ctx.fill();
  }

  ctx.strokeStyle = '#00c8ff';
  ctx.lineWidth = 2;
  ctx.setLineDash(roiPolygonClosed.value ? [] : [6, 4]);
  ctx.beginPath();
  ctx.moveTo(pts[0].x, pts[0].y);
  for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i].x, pts[i].y);
  if (roiPolygonClosed.value) ctx.closePath();
  else if (roiMousePos) ctx.lineTo(roiMousePos.x, roiMousePos.y);
  ctx.stroke();
  ctx.setLineDash([]);

  const nearFirst = !roiPolygonClosed.value && pts.length >= 3 && roiMousePos &&
    Math.sqrt((roiMousePos.x - pts[0].x) ** 2 + (roiMousePos.y - pts[0].y) ** 2) < ROI_CLOSE_RADIUS * (canvas.width / (canvas.getBoundingClientRect().width || 1));

  pts.forEach((pt, i) => {
    const isFirst = i === 0;
    const radius = isFirst && nearFirst ? 10 : 5;
    ctx.fillStyle = isFirst ? (nearFirst ? '#22c55e' : '#f59e0b') : '#00c8ff';
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
    if (roiPoints.value.length < 2) return;
    const [a, b] = roiPoints.value;
    const x = Math.min(a.x, b.x) / w;
    const y = Math.min(a.y, b.y) / h;
    const rw = Math.abs(b.x - a.x) / w;
    const rh = Math.abs(b.y - a.y) / h;
    if (rw <= 0 || rh <= 0) return;
    emit('save-rect', [r4(x), r4(y), r4(rw), r4(rh)]);
    return;
  }

  if (roiPoints.value.length < 3) return;
  const polygon = roiPoints.value.map(pt => [r4(pt.x / w), r4(pt.y / h)]);
  emit('save', polygon);
};
</script>
