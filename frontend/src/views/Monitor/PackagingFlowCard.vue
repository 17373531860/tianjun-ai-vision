<template>
  <div class="packaging-card bg-slate-900 border border-slate-700 rounded-lg overflow-hidden">
    <div class="bg-slate-800 px-3 py-1 border-b border-slate-700 flex justify-between items-center">
      <span class="text-cyan-400 text-lg font-bold">包装箱结算 · {{ config.name }}</span>
      <span class="text-xs" :class="statusColor">{{ statusLabel }}</span>
    </div>

    <!-- 没有进行中工单 -->
    <div v-if="!state" class="py-6 text-center text-gray-500 text-sm">
      等待扫工单标签开工…
    </div>

    <div v-else class="p-3">
      <!-- 工单 + 箱进度概览 -->
      <div class="flex items-center justify-between mb-3">
        <div>
          <span class="text-gray-400 text-xs">当前工单</span>
          <div class="font-mono text-white text-base">{{ state.order_no || '-' }}</div>
        </div>
        <div class="text-right">
          <span class="text-gray-400 text-xs">箱进度</span>
          <div class="text-base font-bold">
            <span class="text-cyan-300">{{ state.box_done }}</span>
            <span class="text-gray-500"> / {{ state.box_total > 0 ? state.box_total : '?' }}</span>
            <span v-if="state.box_ng > 0" class="text-red-400 text-xs ml-2">NG {{ state.box_ng }}</span>
          </div>
        </div>
      </div>

      <!-- 当前箱托盘进度 -->
      <div class="current-box bg-slate-800/60 rounded px-3 py-2 mb-3">
        <div class="flex items-center justify-between">
          <span class="text-gray-300 text-sm">
            正在装第 <span class="text-cyan-400 font-bold">{{ state.current_box_index || '-' }}</span> 箱
          </span>
          <span class="text-sm">
            托盘
            <span class="text-green-400 font-bold text-lg">{{ state.current_box_trays }}</span>
            <span class="text-gray-500"> / {{ traysPerBox }}</span>
          </span>
        </div>
        <el-progress
          :percentage="trayPercent"
          :stroke-width="8"
          :show-text="false"
          :color="trayPercent >= 100 ? '#22c55e' : '#06b6d4'"
          class="mt-1"
        />
      </div>

      <!-- 各箱明细 -->
      <div v-if="(state.box_details || []).length" class="boxes flex flex-wrap gap-1">
        <span
          v-for="b in state.box_details"
          :key="b.box"
          class="box-chip text-xs px-2 py-0.5 rounded font-mono"
          :class="b.result === 'OK' ? 'bg-green-700/40 text-green-300' : 'bg-red-700/40 text-red-300'"
          :title="`第 ${b.box} 箱: ${b.trays}/${b.need} 托盘 → ${b.result}`"
        >
          #{{ b.box }} {{ b.trays }}/{{ b.need }} {{ b.result }}
        </span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue';

const props = defineProps({
  config: { type: Object, required: true },
  state: { type: Object, default: null },
});

const traysPerBox = computed(() => props.config?.trays_per_box_fixed || 4);

const trayPercent = computed(() => {
  if (!props.state || !traysPerBox.value) return 0;
  const p = Math.round((props.state.current_box_trays / traysPerBox.value) * 100);
  return Math.min(100, Math.max(0, p));
});

const statusLabel = computed(() => ({
  order_loaded: '工单已开',
  running: '装箱中',
  completed: '已完成',
  aborted: '已作废',
}[props.state?.status] || (props.state ? props.state.status : '空闲')));

const statusColor = computed(() => ({
  running: 'text-green-400',
  completed: 'text-cyan-400',
  aborted: 'text-red-400',
}[props.state?.status] || 'text-gray-400'));
</script>

<style scoped>
.packaging-card {
  color: white;
}
</style>
