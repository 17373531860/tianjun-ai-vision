<template>
  <el-dialog v-model="visible" title="视频播放" width="70%" destroy-on-close :close-on-click-modal="false" class="dark-dialog" @closed="onClosed">
    <!-- v3.54: 长会话录像分段选择 (单段/周期/步骤视频不显示) -->
    <div v-if="segments.length > 1" class="flex items-center gap-2 mb-2 flex-wrap" data-testid="video-segment-bar">
      <span class="text-xs text-gray-500 flex-shrink-0">录像分段：</span>
      <el-button
        v-for="(seg, idx) in segments"
        :key="seg.video_uuid"
        size="small"
        :type="idx === activeSegment ? 'primary' : 'default'"
        :disabled="!seg.file_exists"
        :data-testid="`video-segment-${idx + 1}`"
        @click="switchSegment(idx)"
      >
        第{{ idx + 1 }}段 {{ segTimeLabel(seg) }}
      </el-button>
    </div>
    <div class="video-container" style="position: relative;">
      <video v-if="currentVideoUrl" ref="videoPlayerRef" :src="currentVideoUrl" controls autoplay class="w-full" style="max-height: 70vh; background: #000; border-radius: 8px;" @error="handleVideoError" @loadedmetadata="applyPlaybackRate">
        您的浏览器不支持视频播放
      </video>
      <div v-else class="text-center text-gray-500 py-10">视频加载中...</div>
      <!-- 2026-09 检测框叠加层 (sidecar 数据驱动, 纯前端绘制, 视频原片不变) -->
      <canvas
        v-show="showBoxes && boxesData"
        ref="overlayRef"
        data-testid="video-boxes-overlay"
        style="position: absolute; pointer-events: none; left: 0; top: 0;"
      ></canvas>
    </div>
    <template #footer>
      <div class="flex items-center justify-between">
        <span class="text-gray-500 text-xs">提示：视频首次加载可能需要几秒钟进行格式转换</span>
        <div class="flex items-center gap-3">
          <!-- 2026-09 检测框开关 (仅当该录像带 sidecar 数据时出现) -->
          <div v-if="boxesData" class="flex items-center gap-1" data-testid="video-boxes-toggle">
            <span class="text-xs text-gray-400">检测框</span>
            <el-switch v-model="showBoxes" size="small" />
          </div>
          <!-- v3.48.1: 播放倍速 -->
          <el-radio-group v-model="videoPlaybackRate" size="small" @change="applyPlaybackRate" data-testid="video-speed-group">
            <el-radio-button v-for="r in [0.5, 1, 1.5, 2, 4]" :key="r" :label="r">{{ r }}x</el-radio-button>
          </el-radio-group>
          <!-- 2026-09 下载带框版 (后端现场渲染烧框 MP4) -->
          <el-button v-if="boxesData" size="small" type="warning" plain :loading="annotatedDownloading" @click="downloadAnnotatedVideo" data-testid="video-download-annotated-btn">
            <el-icon class="mr-1"><Download /></el-icon>下载带框版
          </el-button>
          <!-- v3.48.1: 下载录像 -->
          <el-button size="small" type="primary" plain @click="downloadCurrentVideo" data-testid="video-download-btn">
            <el-icon class="mr-1"><Download /></el-icon>下载录像
          </el-button>
        </div>
      </div>
    </template>
  </el-dialog>
</template>

<script setup>
// 录像回放弹窗（会话/周期/步骤视频共用）。父视图拿 ref 调 open(url) 打开;
// 长会话分段录像调 openSegments(list, urlOf) 打开并显示分段切换条。
// 2026-09: open(url, videoUuid) 传了 uuid 时尝试拉检测框 sidecar 数据,
// 有数据则出现「检测框」叠加开关与「下载带框版」按钮 (无数据静默降级)。
import { ref, watch, onBeforeUnmount } from 'vue';
import { Download } from '@element-plus/icons-vue';
import { ElMessage } from 'element-plus';
import { dbg } from '@/utils/debug';
import { getVideoBoxes, getAnnotatedVideoUrl } from '@/api/data';

const visible = ref(false);
const currentVideoUrl = ref('');
// v3.54: 分段列表 [{video_uuid, start_time, end_time, file_exists}] + url 构造器
const segments = ref([]);
const activeSegment = ref(0);
let segmentUrlOf = null;

// 2026-09 检测框叠加状态
const boxesData = ref(null);      // {fps, frames: [{f, d: [...]}, ...]} | null
const showBoxes = ref(false);
const currentVideoUuid = ref('');
const annotatedDownloading = ref(false);
const overlayRef = ref(null);
let rafId = null;

const resetBoxes = () => {
  boxesData.value = null;
  showBoxes.value = false;
  currentVideoUuid.value = '';
  stopOverlayLoop();
};

const loadBoxes = async (videoUuid) => {
  if (!videoUuid) return;
  try {
    const res = await getVideoBoxes(videoUuid);
    if (res.data && Array.isArray(res.data.frames) && res.data.frames.length) {
      boxesData.value = res.data;
      currentVideoUuid.value = videoUuid;
      dbg('data.query', '检测框数据加载', `video=${videoUuid} 条目=${res.data.frames.length}`);
    }
  } catch (e) {
    // 404 = 该录像没记检测框数据, 静默降级为普通回放
  }
};

const open = (url, videoUuid = '') => {
  segments.value = [];
  activeSegment.value = 0;
  resetBoxes();
  currentVideoUrl.value = url;
  visible.value = true;
  if (videoUuid) loadBoxes(videoUuid);
};

const openSegments = (segList, urlOf) => {
  segments.value = segList || [];
  segmentUrlOf = urlOf;
  resetBoxes();
  // 默认播首个存在的段
  const first = segments.value.findIndex((s) => s.file_exists);
  activeSegment.value = first >= 0 ? first : 0;
  const seg = segments.value[activeSegment.value];
  currentVideoUrl.value = seg ? urlOf(seg.video_uuid) : '';
  visible.value = true;
};

const switchSegment = (idx) => {
  if (idx === activeSegment.value || !segmentUrlOf) return;
  const seg = segments.value[idx];
  if (!seg) return;
  activeSegment.value = idx;
  currentVideoUrl.value = segmentUrlOf(seg.video_uuid);
  dbg('data.query', '切换录像分段', `第${idx + 1}段 ${seg.video_uuid}`);
};

const segTimeLabel = (seg) => {
  const hm = (t) => (t ? t.slice(11, 16) : '');
  const a = hm(seg.start_time);
  const b = hm(seg.end_time);
  if (a && b) return `${a}-${b}`;
  if (a) return `${a} 起`;
  return '';
};

// 视频播放错误处理
const handleVideoError = (e) => {
  console.error('视频播放错误:', e);
  ElMessage.error('视频加载失败：文件可能损坏或格式转换未完成，可点「下载录像」用本地播放器查看');
};

// v3.48.1: 播放倍速（记住选择, 换视频不重置）
const videoPlayerRef = ref(null);
const videoPlaybackRate = ref(1);
const applyPlaybackRate = () => {
  if (videoPlayerRef.value) {
    videoPlayerRef.value.playbackRate = Number(videoPlaybackRate.value) || 1;
  }
};

// v3.48.1: 下载当前录像（走同一 /data/videos/{id} 端点, FileResponse 带文件名）
const downloadCurrentVideo = () => {
  if (!currentVideoUrl.value) return;
  dbg('data.query', '下载录像', currentVideoUrl.value);
  const a = document.createElement('a');
  a.href = currentVideoUrl.value;
  a.download = '';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
};

// 2026-09: 下载带框版（后端按 sidecar 现场渲染, 首次可能等几秒, 有缓存）
const downloadAnnotatedVideo = () => {
  if (!currentVideoUuid.value) return;
  dbg('data.query', '下载带框版录像', currentVideoUuid.value);
  annotatedDownloading.value = true;
  ElMessage.info('正在生成带框版录像，首次下载可能需要几秒…');
  const a = document.createElement('a');
  a.href = getAnnotatedVideoUrl(currentVideoUuid.value);
  a.download = '';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  // 浏览器原生下载拿不到完成事件, 几秒后复位 loading (仅防连点)
  setTimeout(() => { annotatedDownloading.value = false; }, 5000);
};

// ============ 2026-09 检测框叠加绘制 ============
// run-length 语义: 当前生效检测框 = 最后一条 f <= 当前帧号的记录 (二分查找)
const detsAtFrame = (frameIdx) => {
  const frames = boxesData.value?.frames || [];
  let lo = 0, hi = frames.length - 1, ans = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (frames[mid].f <= frameIdx) { ans = mid; lo = mid + 1; } else { hi = mid - 1; }
  }
  return ans >= 0 ? (frames[ans].d || []) : [];
};

const drawOverlay = () => {
  const video = videoPlayerRef.value;
  const canvas = overlayRef.value;
  if (!video || !canvas || !boxesData.value) return;
  // 画布贴齐 video 元素 (video 在容器内的偏移与尺寸)
  const w = video.clientWidth, h = video.clientHeight;
  if (!w || !h) return;
  canvas.style.left = `${video.offsetLeft}px`;
  canvas.style.top = `${video.offsetTop}px`;
  if (canvas.width !== w) canvas.width = w;
  if (canvas.height !== h) canvas.height = h;
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, w, h);
  const vw = video.videoWidth, vh = video.videoHeight;
  if (!vw || !vh) return;
  // object-fit: contain 的实际画面区域 (letterbox 偏移)
  const scale = Math.min(w / vw, h / vh);
  const dispW = vw * scale, dispH = vh * scale;
  const offX = (w - dispW) / 2, offY = (h - dispH) / 2;
  const fps = boxesData.value.fps || 25;
  const frameIdx = Math.floor(video.currentTime * fps);
  const dets = detsAtFrame(frameIdx);
  ctx.lineWidth = 2;
  ctx.strokeStyle = '#00ff00';
  ctx.font = '12px sans-serif';
  for (const d of dets) {
    const x = offX + d.x * dispW;
    const y = offY + d.y * dispH;
    const bw = d.w * dispW;
    const bh = d.h * dispH;
    ctx.strokeRect(x, y, bw, bh);
    const text = `${d.label} ${Math.round((d.conf || 0) * 100)}%`;
    const tw = ctx.measureText(text).width;
    const ty = y - 16 > 0 ? y - 16 : y + 2;
    ctx.fillStyle = 'rgba(0, 128, 0, 0.85)';
    ctx.fillRect(x, ty, tw + 8, 16);
    ctx.fillStyle = '#fff';
    ctx.fillText(text, x + 4, ty + 12);
  }
};

const overlayLoop = () => {
  if (!visible.value || !showBoxes.value) { rafId = null; return; }
  drawOverlay();
  rafId = requestAnimationFrame(overlayLoop);
};

const startOverlayLoop = () => {
  if (rafId == null) rafId = requestAnimationFrame(overlayLoop);
};

const stopOverlayLoop = () => {
  if (rafId != null) {
    cancelAnimationFrame(rafId);
    rafId = null;
  }
  const canvas = overlayRef.value;
  if (canvas) {
    const ctx = canvas.getContext('2d');
    ctx && ctx.clearRect(0, 0, canvas.width, canvas.height);
  }
};

watch(showBoxes, (on) => {
  if (on) startOverlayLoop();
  else stopOverlayLoop();
});

const onClosed = () => stopOverlayLoop();
onBeforeUnmount(() => stopOverlayLoop());

defineExpose({ open, openSegments });
</script>
