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

      <!-- 当前箱进度 (滑块口径显示滑块数/目标 + 尾箱标记; 否则托盘数/每箱) -->
      <div class="current-box bg-slate-800/60 rounded px-3 py-2 mb-3">
        <div class="flex items-center justify-between">
          <span class="text-gray-300 text-sm">
            正在装第 <span class="text-cyan-400 font-bold">{{ state.current_box_index || '-' }}</span> 箱
            <span v-if="isSliders && isTailBox"
                  class="ml-1 text-xs px-1 rounded bg-amber-600/40 text-amber-300">尾箱</span>
          </span>
          <span v-if="isSliders" class="text-sm">
            滑块
            <span class="text-green-400 font-bold text-lg">{{ state.current_box_sliders }}</span>
            <span class="text-gray-500"> / {{ currentBoxTarget }}</span>
          </span>
          <span v-else class="text-sm">
            托盘
            <span class="text-green-400 font-bold text-lg">{{ state.current_box_trays }}</span>
            <span class="text-gray-500"> / {{ traysPerBox }}</span>
          </span>
        </div>
        <el-progress
          :percentage="boxPercent"
          :stroke-width="8"
          :show-text="false"
          :color="boxPercent >= 100 ? '#22c55e' : '#06b6d4'"
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
          :title="isSliders
            ? `第 ${b.box} 箱${b.is_tail ? '(尾箱)' : ''}: ${b.sliders}/${b.target} 滑块 → ${b.result}`
            : `第 ${b.box} 箱: ${b.trays}/${b.need} 托盘 → ${b.result}`"
        >
          <template v-if="isSliders">#{{ b.box }}{{ b.is_tail ? '尾' : '' }} {{ b.sliders }}/{{ b.target }} {{ b.result }}</template>
          <template v-else>#{{ b.box }} {{ b.trays }}/{{ b.need }} {{ b.result }}</template>
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

// v3.22 滑块口径: 当前箱目标 = 普通箱每箱数 / 尾箱余数
const isSliders = computed(() => props.state?.count_unit === 'sliders');

const isTailBox = computed(() =>
  !!props.state && props.state.box_total > 0
  && props.state.current_box_index >= props.state.box_total);

const currentBoxTarget = computed(() => {
  if (!props.state) return 0;
  return isTailBox.value && props.state.tail_target > 0
    ? props.state.tail_target
    : (props.state.items_per_box || 0);
});

const boxPercent = computed(() => {
  if (!props.state) return 0;
  if (isSliders.value) {
    const tgt = currentBoxTarget.value;
    if (!tgt) return 0;
    return Math.min(100, Math.max(0, Math.round((props.state.current_box_sliders / tgt) * 100)));
  }
  if (!traysPerBox.value) return 0;
  return Math.min(100, Math.max(0, Math.round((props.state.current_box_trays / traysPerBox.value) * 100)));
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
