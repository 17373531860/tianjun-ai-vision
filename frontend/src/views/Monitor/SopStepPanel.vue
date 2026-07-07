<template>
  <!-- ==================== SOP 流程卡片面板（2026-07 拆分批次 M-2 自 index.vue 外置） ====================
       单工位视频下方核心展示位, 与 PerItemPanel / WeighingPanel / Tracking 清点面板排他,
       排他条件(v-if)留在父级调用点。steps/stepIntervals 由父级轮询维护, 走 props 只读;
       自动滚动由父级轮询在"最新变化卡片"判定后经 defineExpose 方法驱动
       (scrollToCard / resetScroll), 子组件不自起轮询（Monitor 隔离不变量）。 -->
  <div class="h-44 bg-slate-900 border border-slate-700 rounded-lg overflow-hidden flex flex-col">
    <div class="bg-slate-800 px-3 py-1 border-b border-slate-700 flex-shrink-0">
      <span class="text-cyan-400 text-lg font-bold">SOP流程卡片</span>
    </div>
    <div ref="sopScrollContainer" class="flex-1 p-2 overflow-x-auto scroll-smooth">
      <div class="flex items-center h-full">
        <template v-for="(step, idx) in steps" :key="idx">
          <!-- 间隔时间显示 -->
          <div v-if="idx > 0" class="flex flex-col items-center justify-center px-1 flex-shrink-0">
            <div class="w-6 h-[2px] bg-slate-600"></div>
            <div class="text-[0.5625rem] text-yellow-400 font-mono mt-0.5 whitespace-nowrap">
              {{ formatInterval(step.label) }}
            </div>
            <div class="w-6 h-[2px] bg-slate-600"></div>
          </div>

          <!-- 步骤卡片 -->
          <div
            :ref="el => { if (el) sopCardRefs[idx] = el }"
            class="w-32 flex-shrink-0 flex flex-col rounded border transition-all duration-300"
            :class="getSopCardClass(step)"
          >
            <div class="h-7 px-2 flex items-center justify-between text-xs"
              :class="getSopHeaderClass(step)"
            >
              <span class="font-bold truncate">{{ step.name }}</span>
            </div>
            <div class="h-16 p-1 flex items-center justify-center relative overflow-hidden"
              :class="getSopBodyClass(step)"
            >
               <img
                 v-if="step.screenshot"
                 :src="step.screenshot"
                 class="w-full h-full object-cover rounded"
               />
               <el-icon v-else :size="24" class="text-slate-600"><Picture /></el-icon>

               <div v-if="step.status === 'active'" class="absolute inset-0 border-2 border-cyan-500 animate-pulse"></div>
            </div>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue';
import { Picture } from '@element-plus/icons-vue';

const props = defineProps({
  // 父级轮询维护的单工位步骤数组（name/label/status/cycleResult/screenshot）
  steps: { type: Array, default: () => [] },
  // 步骤间隔秒数 { label: seconds }, 父级轮询写入
  stepIntervals: { type: Object, default: () => ({}) },
});

// 格式化步骤间隔时间
const formatInterval = (stepLabel) => {
  const interval = props.stepIntervals[stepLabel];
  if (interval === undefined || interval === null || interval === 0) return '--';
  return `${interval.toFixed(1)}s`;
};

// SOP卡片整体样式：完成=绿色，漏检/重复=红色
const getSopCardClass = (step) => {
  if (step.cycleResult === 'ng') {
    return 'border-red-500 bg-red-900/30';
  } else if (step.status === 'completed' || step.cycleResult === 'ok') {
    return 'border-green-500 bg-green-900/20';
  } else if (step.status === 'active') {
    return 'border-cyan-500 shadow-[0_0_10px_rgba(6,182,212,0.3)] bg-slate-800';
  } else {
    return 'border-slate-700 bg-slate-800 opacity-60';
  }
};

const getSopHeaderClass = (step) => {
  if (step.cycleResult === 'ng') {
    return 'bg-red-900/60 text-red-200';
  } else if (step.status === 'completed' || step.cycleResult === 'ok') {
    return 'bg-green-900/60 text-green-200';
  } else {
    return 'bg-slate-950 text-gray-300';
  }
};

const getSopBodyClass = (step) => {
  if (step.cycleResult === 'ng') {
    return 'bg-red-950/30';
  } else if (step.status === 'completed' || step.cycleResult === 'ok') {
    return 'bg-green-950/20';
  } else {
    return 'bg-black/20';
  }
};

// ==================== 自动滚动（父级轮询驱动） ====================
const sopScrollContainer = ref(null);
const sopCardRefs = {};
let lastScrolledIdx = -1;

// SOP 卡片自动滚动：将正在变化的卡片滚动到可视区域中间
const scrollToCard = (idx) => {
  if (idx === lastScrolledIdx || idx < 0) return;
  const container = sopScrollContainer.value;
  const card = sopCardRefs[idx];
  if (!container || !card) return;

  const containerWidth = container.clientWidth;
  const cardLeft = card.offsetLeft;
  const cardWidth = card.offsetWidth;
  const targetScroll = cardLeft - (containerWidth / 2) + (cardWidth / 2);

  container.scrollTo({ left: Math.max(0, targetScroll), behavior: 'smooth' });
  lastScrolledIdx = idx;
};

// 滚动游标归零（周期切换 / 清零时父级调用, 让新一轮从第一张卡片开始展示）
const resetScroll = () => {
  lastScrolledIdx = -1;
};

defineExpose({ scrollToCard, resetScroll });
</script>
