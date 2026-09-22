<template>
  <div ref="el" class="echarts-pane" />
</template>

<script setup>
// 通用 ECharts 容器: option 变更 setOption, 容器尺寸变化自动 resize。
// canvas 内拿不到 CSS 变量, 图内配色由调用方传 (与 App.vue tokens 同值)。
// 按需注册 (全量 import 让 DataView chunk 到 1.1MB, 按需 ~1/3): 数据中心
// 只用柱/线 + 网格/提示/图例/标线, 新图形先来这里登记。
import { BarChart, LineChart } from 'echarts/charts'
import { GridComponent, LegendComponent, MarkLineComponent,
         TooltipComponent } from 'echarts/components'
import * as echarts from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

echarts.use([BarChart, LineChart, GridComponent, LegendComponent,
             MarkLineComponent, TooltipComponent, CanvasRenderer])

const props = defineProps({
  option: { type: Object, required: true },
})
const emit = defineEmits(['chart-click'])

const el = ref(null)
let chart = null
let ro = null

onMounted(() => {
  chart = echarts.init(el.value)
  chart.setOption(props.option)
  chart.on('click', (p) => emit('chart-click', p))
  ro = new ResizeObserver(() => chart && chart.resize())
  ro.observe(el.value)
})

watch(() => props.option, (opt) => {
  // notMerge: 数据集变小时旧系列不残留
  if (chart) chart.setOption(opt, { notMerge: true })
}, { deep: true })

onBeforeUnmount(() => {
  if (ro) ro.disconnect()
  if (chart) { chart.dispose(); chart = null }
})
</script>

<style scoped>
.echarts-pane { width: 100%; height: 100%; min-height: 200px; }
</style>
