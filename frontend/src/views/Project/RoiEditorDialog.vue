<template>
  <!-- ROI 多边形编辑器对话框（2026-07 拆分批次 P-2 自 index.vue 外置） -->
  <el-dialog :model-value="visible"
    :title="title"
    width="80%" :close-on-click-modal="false" destroy-on-close
    class="roi-editor-dialog"
    @update:model-value="$emit('update:visible', $event)"
    @close="$emit('close')">
    <div class="space-y-3">
      <div class="flex items-center gap-3 text-sm">
        <span class="text-gray-400">单击添加顶点，点击<b class="text-amber-400">第一个点</b>闭合多边形（靠近时会变绿）。闭合后再次单击可重新绘制</span>
        <div class="flex-1"></div>
        <el-button size="small" @click="roiUndoPoint" :disabled="roiPoints.length === 0">撤销上一点</el-button>
        <el-button size="small" type="warning" @click="roiClearPoints" :disabled="roiPoints.length === 0">清除全部</el-button>
        <el-button size="small" type="success" @click="roiFinishPolygon" :disabled="roiPoints.length < 3">完成绘制</el-button>
      </div>
      <div class="relative bg-black rounded overflow-hidden flex justify-center" style="max-height: 70vh;">
        <canvas ref="roiEditorCanvas" class="cursor-crosshair" style="max-width: 100%; max-height: 70vh; object-fit: contain;"
          @click="roiCanvasClick" @dblclick="roiCanvasDblClick" @mousemove="roiCanvasMouseMove"></canvas>
      </div>
      <div class="flex items-center gap-2 text-xs text-gray-500">
        <span>顶点数: {{ roiPoints.length }}</span>
        <span v-if="roiPolygonClosed" class="text-green-400 font-bold">多边形已闭合</span>
      </div>
    </div>
    <template #footer>
      <el-button @click="$emit('update:visible', false)">取消</el-button>
      <el-button type="primary" @click="roiSave" :disabled="roiPoints.length < 3">保存 ROI</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
// ==================== ROI 多边形编辑器（画布交互自 index.vue 平移） ====================
// 数据流约定: 本组件只管「取快照底图 + 画多边形」；点保存时把归一化多边形
// (顶点 [[nx,ny],...], 0~1) 通过 save 事件交回父级——写到副模型/步骤/全局
// tracking 哪个目标、以及是否立即触发保存项目, 路由逻辑全在父级。
// 打开流程: 父级设 visible=true → nextTick 等 canvas 挂载 → 调 load(channel, 初始多边形)。
import { ref } from 'vue';
import { getBackendHost } from '@/api/index';

defineProps({
  visible: { type: Boolean, required: true },
  title: { type: String, default: '绘制 ROI 检测区域' },
});
const emit = defineEmits(['update:visible', 'save', 'close']);

const roiEditorCanvas = ref(null);
const roiPoints = ref([]);
const roiPolygonClosed = ref(false);
let roiImage = null;
let roiMousePos = null;

const ROI_CLOSE_RADIUS = 15;

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
      roiRedraw();
      resolve(true);
    };
    img.onerror = () => {
      const ctx = canvas.getContext('2d');
      canvas.width = 640;
      canvas.height = 480;
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

// 父级打开对话框后调用: 重置状态 → 取快照 → (可选)预加载已有多边形
const load = async (channel = 0, existingPolygon = null) => {
  roiPoints.value = [];
  roiPolygonClosed.value = false;
  roiMousePos = null;
  const ok = await loadRoiSnapshot(channel);
  if (!ok) return false;  // 快照失败时画兜底文字, polygon 没意义不画
  const canvas = roiEditorCanvas.value;
  if (Array.isArray(existingPolygon) && existingPolygon.length >= 3 && canvas) {
    const w = canvas.width || 1;
    const h = canvas.height || 1;
    roiPoints.value = existingPolygon.map(([nx, ny]) => ({ x: nx * w, y: ny * h }));
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
  if (roiPolygonClosed.value) {
    roiPoints.value = [];
    roiPolygonClosed.value = false;
    roiRedraw();
    return;
  }
  const pt = roiGetCanvasXY(e);
  if (!pt) return;

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
  if (roiPoints.value.length >= 3 && !roiPolygonClosed.value) {
    roiPolygonClosed.value = true;
    roiRedraw();
  }
};

const roiCanvasMouseMove = (e) => {
  if (roiPolygonClosed.value) return;
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
  if (roiPoints.value.length < 3) return;
  const canvas = roiEditorCanvas.value;
  const w = canvas?.width || 1;
  const h = canvas?.height || 1;
  const polygon = roiPoints.value.map(pt => [
    Math.round((pt.x / w) * 10000) / 10000,
    Math.round((pt.y / h) * 10000) / 10000
  ]);
  emit('save', polygon);
};
</script>
