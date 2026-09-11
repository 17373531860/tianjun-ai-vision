<template>
  <div
    class="flex h-screen w-screen items-center justify-center overflow-hidden bg-black"
    data-testid="hands-crop-view"
    :data-channel="channelId"
    :data-crop-mode="cropMode"
  >
    <img
      :src="snapshotUrl"
      class="h-full w-full select-none object-contain"
      alt="手部检测裁切画面"
      draggable="false"
      data-testid="hands-crop-image"
      @load="scheduleNextFrame"
      @error="scheduleNextFrame"
    >
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue';
import { useRoute } from 'vue-router';
import { getBackendHost } from '@/api/index';

const FRAME_PERIOD_MS = 80; // 相邻请求起点约 80ms（12.5fps），且始终串行。
const FRAME_WATCHDOG_MS = 1000;

const route = useRoute();
const channelId = computed(() => {
  const parsed = Number.parseInt(String(route.query.channel ?? '0'), 10);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : 0;
});
const cropMode = computed(() => (
  String(route.query.aux_view_mode || '').trim().toLowerCase() === 'fixed'
    ? 'fixed'
    : 'follow'
));
const snapshotUrl = ref('');

let frameTimer = null;
let watchdogTimer = null;
let stopped = false;
let lastRequestAt = 0;

const clearFrameTimers = () => {
  if (frameTimer) {
    clearTimeout(frameTimer);
    frameTimer = null;
  }
  if (watchdogTimer) {
    clearTimeout(watchdogTimer);
    watchdogTimer = null;
  }
};

const requestFrame = () => {
  if (stopped) return;
  clearFrameTimers();
  lastRequestAt = Date.now();
  snapshotUrl.value = `${getBackendHost()}/snapshot?channel=${channelId.value}`
    + `&view=hands&crop_mode=${cropMode.value}&_t=${lastRequestAt}`;
  // 后端未响应时主动换 URL，避免副屏永久停在一次挂起请求上。
  watchdogTimer = setTimeout(requestFrame, FRAME_WATCHDOG_MS);
};

const scheduleNextFrame = () => {
  if (stopped) return;
  clearFrameTimers();
  const elapsed = Date.now() - lastRequestAt;
  frameTimer = setTimeout(requestFrame, Math.max(0, FRAME_PERIOD_MS - elapsed));
};

watch([channelId, cropMode], requestFrame);

onMounted(() => {
  stopped = false;
  requestFrame();
});

onUnmounted(() => {
  stopped = true;
  clearFrameTimers();
});
</script>
