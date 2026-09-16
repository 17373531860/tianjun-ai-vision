<template>
  <footer class="h-9 bg-[#0a0f1a] border-t border-cyan-900/50 flex items-center justify-between px-6 text-sm select-none shrink-0">
    <!-- 顶栏已展示作业员与设备信息，底栏仅保留运行状态信息，避免重复 -->
    <div class="flex items-center gap-8 text-gray-400">
      <div v-if="store.display.navbar.mode" class="flex items-center gap-1.5">
        <span class="text-cyan-500/70">模式</span>
        <span class="text-gray-200">{{ modeLabel }}</span>
      </div>
      <div v-if="store.display.navbar.status" class="flex items-center gap-1.5">
        <span class="text-cyan-500/70">状态</span>
        <span :class="projectStore.isRunning ? 'text-green-400' : 'text-gray-400'">
          {{ projectStore.isRunning ? '运行中' : '待机' }}
        </span>
      </div>
      <div v-if="store.display.navbar.runtime" class="flex items-center gap-1.5 font-mono">
        <span class="text-cyan-500/70">运行时间</span>
        <span class="text-gray-200">{{ runTimeStr }}</span>
      </div>
    </div>
  </footer>
</template>

<script setup>
import { useSystemStore } from '@/store/useSystemStore';
import { useProjectStore } from '@/store/useProjectStore';
import { computed, ref, onMounted, onUnmounted } from 'vue';
import { useI18n } from 'vue-i18n';

const store = useSystemStore();
const projectStore = useProjectStore();
const { t } = useI18n();

const modeLabel = computed(() => {
  const mode = projectStore.currentProject?.logic_mode;
  const map = {
    'sequential': t('mode.sequential'),
    'detection': t('mode.detection'),
    'custom': t('mode.custom'),
    'tracking': t('mode.tracking'),
    'per_item': t('mode.per_item'),
    'weighing': t('mode.weighing'),
    'region_events': t('mode.region_events'),
  };
  return map[mode] || t('mode.undefined');
});

const runTimeSeconds = ref(0);
const runTimeStr = computed(() => {
  const h = Math.floor(runTimeSeconds.value / 3600);
  const m = Math.floor((runTimeSeconds.value % 3600) / 60);
  const s = runTimeSeconds.value % 60;
  return `${h.toString().padStart(2, '0')}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
});

let timerInterval;
onMounted(() => {
  timerInterval = setInterval(() => {
    runTimeSeconds.value++;
  }, 1000);
});

onUnmounted(() => {
  if (timerInterval) clearInterval(timerInterval);
});
</script>
