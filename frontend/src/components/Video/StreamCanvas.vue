<template>
  <div class="relative w-full h-full bg-black flex justify-center items-center overflow-hidden">
    <img 
      ref="videoImg"
      :src="streamUrl" 
      class="max-w-full max-h-full object-contain"
      @load="resizeCanvas"
    />
    
    <canvas 
      ref="overlayCanvas" 
      class="absolute pointer-events-none"
      :style="canvasStyle"
    ></canvas>
  </div>
</template>

<script setup>
import { ref, onMounted, reactive, onUnmounted } from 'vue';

const props = defineProps({
  streamUrl: String, // FastAPI 的视频流地址
  detections: Array  // 当前帧的检测目标 [{x, y, w, h, label, color}]
});

const videoImg = ref(null);
const overlayCanvas = ref(null);
const canvasStyle = reactive({ width: '0px', height: '0px', top: '0px', left: '0px' });

// 动态调整 Canvas 尺寸与视频图片完全重合
const resizeCanvas = () => {
  if (!videoImg.value || !overlayCanvas.value) return;
  const { offsetWidth, offsetHeight, offsetLeft, offsetTop } = videoImg.value;
  
  canvasStyle.width = `${offsetWidth}px`;
  canvasStyle.height = `${offsetHeight}px`;
  canvasStyle.top = `${offsetTop}px`;
  canvasStyle.left = `${offsetLeft}px`;
  
  // 设置 Canvas 内部分辨率
  overlayCanvas.value.width = offsetWidth;
  overlayCanvas.value.height = offsetHeight;
};

// 绘图逻辑
const drawDetections = (items) => {
  const ctx = overlayCanvas.value.getContext('2d');
  ctx.clearRect(0, 0, overlayCanvas.value.width, overlayCanvas.value.height);
  
  items.forEach(item => {
    ctx.strokeStyle = item.label === 'NG' ? '#ef4444' : '#10b981';
    ctx.lineWidth = 3;
    // 假设后端传的是 0-1 的归一化坐标，需要乘以当前宽高
    const x = item.x * overlayCanvas.value.width;
    const y = item.y * overlayCanvas.value.height;
    const w = item.w * overlayCanvas.value.width;
    const h = item.h * overlayCanvas.value.height;
    
    ctx.strokeRect(x, y, w, h);
    
    // 绘制标签背景
    ctx.fillStyle = ctx.strokeStyle;
    ctx.fillRect(x, y - 25, 60, 25);
    ctx.fillStyle = 'white';
    ctx.font = '14px Monaco';
    ctx.fillText(`${item.label}`, x + 5, y - 8);
  });
};

// 暴露绘图方法给父组件
defineExpose({ drawDetections, resizeCanvas });

onMounted(() => {
  window.addEventListener('resize', resizeCanvas);
});
onUnmounted(() => {
  window.removeEventListener('resize', resizeCanvas);
});
</script>