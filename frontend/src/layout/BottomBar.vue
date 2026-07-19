<template>
  <footer class="h-9 bg-[#0a0f1a] border-t border-cyan-900/50 flex items-center justify-between px-6 text-sm select-none shrink-0">
    <!-- Left: 所有标签 -->
    <div class="flex items-center gap-8 text-gray-400">
      <!-- v3.10+ 阶段 6: 作业员标签内容随鉴权状态切换 (与 Navbar 同语义) -->
      <div v-if="store.display.navbar.inspector" class="flex items-center gap-1.5"
           data-testid="bottombar-identity">
        <span class="text-cyan-500/70">作业员</span>
        <span :class="effectiveInspectorClass" data-testid="bottombar-identity-name">
          {{ effectiveInspectorName }}
        </span>
        <span v-if="effectiveRoleBadge"
              class="text-[0.625rem] px-1 rounded ml-1"
              :class="effectiveRoleClass"
              data-testid="bottombar-role-badge">
          {{ effectiveRoleBadge }}
        </span>
      </div>
      <div v-if="store.display.navbar.deviceId" class="flex items-center gap-1.5">
        <span class="text-cyan-500/70">设备编号</span>
        <span class="text-gray-200">{{ store.display.deviceNumber || '未设置' }}</span>
      </div>
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
import { useAuthStore } from '@/store/useAuthStore';
import { computed, ref, onMounted, onUnmounted } from 'vue';
import { useI18n } from 'vue-i18n';

const store = useSystemStore();
const projectStore = useProjectStore();
const authStore = useAuthStore();
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

// ============================================================
// v3.10+ 阶段 6: 底栏"作业员"标签内容随鉴权状态切换 (与 Navbar 同语义)
// ============================================================
const effectiveInspectorName = computed(() => {
  if (!authStore.authEnabled) {
    return store.display.inspectorName || '未设置';
  }
  if (authStore.isLoggedIn) {
    return authStore.currentUser.display_name || authStore.currentUser.username;
  }
  return '操作员（未登录）';
});

const effectiveInspectorClass = computed(() => {
  if (!authStore.authEnabled) return 'text-gray-200';
  if (authStore.isLoggedIn) return 'text-emerald-300';
  return 'text-amber-300';
});

const effectiveRoleBadge = computed(() => {
  if (!authStore.authEnabled || !authStore.isLoggedIn) return '';
  const roles = authStore.currentUser?.roles || [];
  if (roles.includes('admin')) return '管理员';
  if (roles.includes('engineer')) return '工程师';
  if (roles.includes('operator')) return '操作员';
  return roles[0] || '';
});

const effectiveRoleClass = computed(() => {
  const roles = authStore.currentUser?.roles || [];
  if (roles.includes('admin')) return 'bg-rose-900/60 text-rose-200';
  if (roles.includes('engineer')) return 'bg-amber-900/60 text-amber-200';
  return 'bg-slate-700/60 text-gray-300';
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
