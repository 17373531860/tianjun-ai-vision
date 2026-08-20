<template>
  <div
    ref="chartRef"
    class="h-full w-full"
    :data-testid="testId"
    :data-rate="rate.toFixed(1)"
  />
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import * as echarts from 'echarts';

const props = defineProps({
  goodCount: { type: Number, default: 0 },
  totalCount: { type: Number, default: 0 },
  testId: { type: String, default: 'yield-rate-gauge' },
});

const chartRef = ref(null);
const rate = computed(() => {
  const total = Math.max(0, Number(props.totalCount) || 0);
  const good = Math.max(0, Number(props.goodCount) || 0);
  return total > 0 ? Math.min(100, (good / total) * 100) : 0;
});

let chart = null;
let resizeObserver = null;

const updateChart = () => {
  if (!chart || chart.isDisposed()) return;
  const value = Number(rate.value.toFixed(1));
  chart.setOption({
    series: [{
      type: 'gauge',
      center: ['50%', '60%'],
      radius: '80%',
      startAngle: 180,
      endAngle: 0,
      min: 0,
      max: 100,
      splitNumber: 5,
      itemStyle: { color: value >= 90 ? '#10b981' : value >= 70 ? '#f59e0b' : '#ef4444' },
      progress: { show: true, width: 10 },
      pointer: { show: false },
      axisLine: { lineStyle: { width: 10, color: [[1, '#334155']] } },
      axisTick: { show: false },
      splitLine: { show: false },
      axisLabel: { show: false },
      detail: {
        valueAnimation: true,
        offsetCenter: [0, '20%'],
        fontSize: 24,
        fontWeight: 'bold',
        color: '#fff',
        formatter: '{value}%',
      },
      data: [{ value }],
    }],
  });
};

const resizeChart = () => {
  if (chart && !chart.isDisposed()) chart.resize();
};

onMounted(async () => {
  await nextTick();
  if (!chartRef.value) return;
  chart = echarts.init(chartRef.value);
  updateChart();
  if (typeof ResizeObserver !== 'undefined') {
    resizeObserver = new ResizeObserver(resizeChart);
    resizeObserver.observe(chartRef.value);
  }
  window.addEventListener('resize', resizeChart);
});

watch(() => [props.goodCount, props.totalCount], updateChart);

onBeforeUnmount(() => {
  window.removeEventListener('resize', resizeChart);
  if (resizeObserver) resizeObserver.disconnect();
  resizeObserver = null;
  if (chart && !chart.isDisposed()) chart.dispose();
  chart = null;
});
</script>
