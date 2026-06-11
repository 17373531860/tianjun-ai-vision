<template>
  <div class="debug-panel p-4">
    <div class="flex justify-between items-center mb-2">
      <h2 class="text-lg text-white font-bold">调试设置</h2>
      <div class="flex gap-2">
        <el-button size="small" @click="openLogsDir" v-if="isElectron">打开日志目录</el-button>
        <el-button size="small" @click="exportLogs">导出日志</el-button>
        <el-button size="small" type="warning" @click="store.paused = !store.paused">
          {{ store.paused ? '继续滚动' : '暂停滚动' }}
        </el-button>
        <el-button size="small" type="danger" @click="store.clearAll()">清空日志</el-button>
      </div>
    </div>

    <div class="text-gray-400 text-xs mb-4">
      按 页面 / 板块 / 功能 逐项开启实时调试日志（默认全关，零开销）。开启后对应按钮、板块的每次动作、
      状态变迁、异常都会出现在下方日志流中，前端日志同时回传后端落盘
      <span class="font-mono text-cyan-400">logs/backend-debug.log</span>，方便客户机出事后取文件排查。
      后端开关为内存态、重启自动归零；前端开关本机记忆。
    </div>

    <!-- ==================== 开关矩阵 ==================== -->
    <el-collapse v-model="openedSections" class="mb-4">
      <el-collapse-item name="fe">
        <template #title>
          <span class="text-white font-bold">前端调试开关（页面 / 按钮 / 板块）</span>
          <el-tag size="small" class="ml-2" :type="feOnCount ? 'success' : 'info'">{{ feOnCount }} 开启</el-tag>
        </template>
        <div class="flex gap-2 mb-3">
          <el-button size="small" @click="store.setAllFrontendFlags(true)">全开</el-button>
          <el-button size="small" @click="store.setAllFrontendFlags(false)">全关</el-button>
        </div>
        <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-x-6 gap-y-1">
          <template v-for="(items, group) in feGroups" :key="group">
            <div class="col-span-full text-cyan-400 text-xs font-bold mt-2 border-b border-gray-700 pb-1">{{ group }}</div>
            <div v-for="it in items" :key="it.key" class="flex items-center justify-between py-1">
              <div class="min-w-0 mr-2">
                <div class="text-gray-200 text-sm truncate">{{ it.label }}</div>
                <div class="text-gray-500 text-xs font-mono">{{ it.key }}</div>
              </div>
              <el-switch :model-value="store.frontendFlags[it.key]" size="small"
                         @change="v => store.setFrontendFlag(it.key, v)" />
            </div>
          </template>
        </div>
      </el-collapse-item>

      <el-collapse-item name="be">
        <template #title>
          <span class="text-white font-bold">后端调试开关（检测 / MES / 系统模块）</span>
          <el-tag size="small" class="ml-2" :type="beOnCount ? 'success' : 'info'">{{ beOnCount }} 开启</el-tag>
        </template>
        <div class="flex gap-2 mb-3">
          <el-button size="small" @click="store.setAllBackendFlags(true)">全开</el-button>
          <el-button size="small" @click="store.setAllBackendFlags(false)">全关</el-button>
          <el-button size="small" @click="store.fetchBackendFlags()">刷新</el-button>
        </div>
        <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-x-6 gap-y-1">
          <template v-for="(items, group) in beGroups" :key="group">
            <div class="col-span-full text-amber-400 text-xs font-bold mt-2 border-b border-gray-700 pb-1">{{ group }}</div>
            <div v-for="it in items" :key="it.key" class="flex items-center justify-between py-1">
              <div class="min-w-0 mr-2">
                <div class="text-gray-200 text-sm truncate">{{ it.label }}</div>
                <div class="text-gray-500 text-xs font-mono">{{ it.key }}</div>
              </div>
              <el-switch :model-value="store.backendFlags[it.key]" size="small"
                         @change="v => store.setBackendFlag(it.key, v)" />
            </div>
          </template>
        </div>
      </el-collapse-item>
    </el-collapse>

    <!-- ==================== 实时日志查看器 ==================== -->
    <div class="flex items-center gap-3 mb-2">
      <span class="text-white font-bold">实时日志</span>
      <el-input v-model="filterText" size="small" placeholder="关键字过滤 (类别/动作/详情)" clearable class="!w-64" />
      <span class="text-gray-500 text-xs">{{ filteredLogs.length }} 条 (缓冲上限 3000)</span>
      <el-tag v-if="store.paused" type="warning" size="small">已暂停</el-tag>
    </div>
    <div ref="logBox" class="log-box font-mono text-xs rounded border border-gray-700 bg-black/60 overflow-auto p-2"
         style="height: 380px;">
      <div v-if="!filteredLogs.length" class="text-gray-600 p-4 text-center">
        暂无日志 — 打开上方任意开关后，去操作对应页面/按钮，日志会实时出现在这里
      </div>
      <div v-for="e in filteredLogs" :key="e.source + '-' + e.seq" class="whitespace-pre-wrap leading-5">
        <span class="text-gray-500">{{ e.ts }}</span>
        <span :class="e.source === 'frontend' ? 'text-cyan-400' : 'text-amber-400'"> [{{ e.category }}]</span>
        <span class="text-gray-200"> {{ e.action }}</span>
        <span v-if="e.detail" class="text-gray-400"> :: {{ e.detail }}</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, nextTick, onMounted, onBeforeUnmount } from 'vue';
import { ElMessage } from 'element-plus';
import { useDebugStore } from '@/store/useDebugStore';

const store = useDebugStore();
const openedSections = ref(['fe']);
const filterText = ref('');
const logBox = ref(null);
const isElectron = !!(window.electronAPI && window.electronAPI.isElectron);

const groupBy = (catalog) => {
  const out = {};
  Object.entries(catalog || {}).forEach(([key, meta]) => {
    const g = meta.group || '其他';
    (out[g] = out[g] || []).push({ key, label: meta.label });
  });
  return out;
};
const feGroups = computed(() => groupBy(store.frontendCatalog));
const beGroups = computed(() => groupBy(store.backendCatalog));
const feOnCount = computed(() => Object.values(store.frontendFlags).filter(Boolean).length);
const beOnCount = computed(() => Object.values(store.backendFlags).filter(Boolean).length);

const filteredLogs = computed(() => {
  const logs = store.mergedLogs;
  const q = filterText.value.trim().toLowerCase();
  if (!q) return logs;
  return logs.filter(e =>
    e.category.toLowerCase().includes(q) ||
    (e.action || '').toLowerCase().includes(q) ||
    (e.detail || '').toLowerCase().includes(q));
});

// 自动滚到底 (暂停时不动)
watch(() => filteredLogs.value.length, async () => {
  if (store.paused) return;
  await nextTick();
  if (logBox.value) logBox.value.scrollTop = logBox.value.scrollHeight;
});

async function openLogsDir() {
  try {
    const r = await window.electronAPI.openLogsDir();
    if (!r || !r.ok) ElMessage.warning('打开失败: ' + ((r && r.error) || '未知'));
  } catch (e) {
    ElMessage.error('打开日志目录失败: ' + (e.message || e));
  }
}

function exportLogs() {
  const lines = filteredLogs.value.map(e =>
    `[${e.ts}] [${e.source}] [${e.category}] ${e.action}${e.detail ? ' :: ' + e.detail : ''}`);
  const blob = new Blob([lines.join('\n')], { type: 'text/plain;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `tianjun-debug-${new Date().toISOString().replace(/[:.]/g, '-')}.txt`;
  a.click();
  URL.revokeObjectURL(a.href);
  ElMessage.success(`已导出 ${lines.length} 条日志`);
}

onMounted(() => {
  store.fetchBackendFlags().catch(() => {});
  store.startPolling();
});
onBeforeUnmount(() => {
  store.stopPolling();
});
</script>

<style scoped>
.log-box::-webkit-scrollbar { width: 8px; }
.log-box::-webkit-scrollbar-thumb { background: #374151; border-radius: 4px; }
</style>
