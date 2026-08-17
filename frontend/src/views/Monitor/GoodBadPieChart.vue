<template>
  <div
    ref="chartRef"
    class="h-full w-full"
    :data-testid="testId"
    :data-good="goodCount"
    :data-bad="badCount"
  />
</template>

<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import * as echarts from 'echarts';

const props = defineProps({
  goodCount: { type: Number, default: 0 },
  badCount: { type: Number, default: 0 },
  testId: { type: String, default: 'good-bad-pie-chart' },
});

const chartRef = ref(null);
let chart = null;
let resizeObserver = null;

const normalizedCount = (value) => Math.max(0, Number(value) || 0);

const updateChart = () => {
  if (!chart || chart.isDisposed()) return;
  const good = normalizedCount(props.goodCount);
  const bad = normalizedCount(props.badCount);
  chart.setOption({
    color: ['#10b981', '#ef4444'],
    series: [{
      type: 'pie',
      radius: ['40%', '70%'],
      center: ['50%', '55%'],
      label: { show: false },
      data: [
        { value: good || 1, name: '良品' },
        { value: bad, name: '不良' },
      ],
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

watch(() => [props.goodCount, props.badCount], updateChart);

onBeforeUnmount(() => {
  window.removeEventListener('resize', resizeChart);
  if (resizeObserver) resizeObserver.disconnect();
  resizeObserver = null;
  if (chart && !chart.isDisposed()) chart.dispose();
  chart = null;
});
</script>
