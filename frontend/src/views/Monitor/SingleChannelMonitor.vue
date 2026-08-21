<template>
  <!-- v3.54 自定义布局: 放大态与 kiosk 共用形态键 zoom, 根容器 = 画布 -->
  <div
    data-layout-canvas="zoom"
    class="flex min-h-0 flex-col gap-2 bg-[#0f172a] text-white"
    :class="kiosk ? 'h-screen p-2' : 'h-[calc(100vh-7.25rem)] p-2'"
    data-testid="single-channel-monitor"
    :data-channel="channelId"
    :data-readonly="readonly ? 'true' : 'false'"
  >
    <header data-layout-slot="topbar" class="flex flex-shrink-0 flex-wrap items-center gap-2 rounded-lg border border-slate-700 bg-slate-900 px-3 py-1.5">
      <button
        v-if="!kiosk"
        type="button"
        class="rounded border border-slate-600 bg-slate-800 px-2.5 py-1 text-xs font-bold text-gray-200 hover:border-cyan-500 hover:text-cyan-300"
        data-testid="single-channel-back"
        @click="$emit('back')"
      >
        ‹ 返回总览
      </button>
      <span class="text-base font-bold text-cyan-400" data-testid="single-channel-title">工位 {{ channelId + 1 }}</span>
      <span v-if="channelData?.projectName" class="max-w-48 truncate text-xs text-gray-400">{{ channelData.projectName }}</span>
      <span
        class="rounded px-2 py-0.5 text-[0.625rem] font-bold"
        :class="channelData?.isDetecting ? 'bg-green-600/90' : channelData?.isRunning ? 'bg-yellow-600/90' : 'bg-gray-600/90'"
      >
        {{ channelData?.isDetecting ? '检测中' : channelData?.isRunning ? '待机' : '停止' }}
      </span>
      <span v-if="readonly" class="rounded border border-slate-600 px-2 py-0.5 text-[0.625rem] text-gray-300" data-testid="single-channel-readonly">只读监看</span>

      <div v-if="!kiosk" class="ml-auto flex items-center gap-1.5">
        <button type="button" class="nav-button" data-testid="single-channel-previous" @click="$emit('previous')">‹ 上一路</button>
        <button type="button" class="nav-button" data-testid="single-channel-next" @click="$emit('next')">下一路 ›</button>
      </div>
    </header>

    <!-- context-bar 有内容(主屏放大态 MES 条)或编辑态才渲染包装, 避免 kiosk 空 div 占 gap -->
    <div v-if="$slots['context-bar'] || layoutEditActive" data-layout-slot="context-bar">
      <slot name="context-bar" />
    </div>

    <div class="grid min-h-0 flex-1 grid-cols-12 gap-3">
      <!-- 与原单工位同构：左 7 列视频，上下紧接同一个 SopStepPanel。 -->
      <div class="col-span-7 flex min-h-0 min-w-0 flex-col gap-3">
        <div data-layout-slot="video" class="relative min-h-0 flex-1" data-testid="single-channel-video">
          <ChannelVideoCard
            :key="`single-${channelId}`"
            class="h-full w-full"
            :ch="channelId"
            :ch-data="channelData"
            :model-stats="modelStats"
            :selected="false"
            :register-video-canvas="registerVideoCanvas"
            :register-overlay-canvas="registerOverlayCanvas"
            @select="() => {}"
          />
          <slot name="video-overlay" />
        </div>

        <SopStepPanel
          v-if="(showStepStrip && steps.length > 0) || layoutEditActive"
          data-layout-slot="sop"
          :steps="steps"
          :step-intervals="channelData?.stepIntervals || {}"
        />
        <div v-else-if="showStepStrip && !hasProject" class="flex h-40 flex-shrink-0 items-center justify-center rounded-lg border border-slate-700 bg-slate-900 text-sm text-gray-500">
          请先选择项目
        </div>
      </div>

      <!-- 与原单工位同构：右 5 列统计、NG TOP3、步骤统计和底部控制。 -->
      <aside class="col-span-5 flex min-h-0 min-w-0 flex-col gap-3 overflow-hidden">
        <div v-if="showStatsPanel || layoutEditActive" data-layout-slot="stats" class="flex flex-shrink-0 flex-col rounded-lg border border-slate-700 bg-slate-900 p-3" data-testid="single-channel-stats-panel">
          <template v-if="hasProject">
            <div class="grid grid-cols-3 gap-2">
              <div v-for="stat in builtinStats" :key="stat.label" class="flex flex-col items-center justify-center rounded-lg bg-slate-800/50 p-2">
                <div class="mb-0.5 text-sm text-gray-300">{{ stat.label }}</div>
                <div class="font-mono text-2xl font-bold" :class="stat.color">{{ stat.value }}</div>
              </div>
            </div>
            <div class="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-slate-800 pt-2 text-xs text-gray-400">
              <span>合格率: <span class="font-mono text-green-400">{{ yieldRate }}%</span></span>
              <span v-if="showFps">FPS: <span class="font-mono text-cyan-400">{{ channelData?.fps ?? 0 }}</span></span>
              <span v-if="showLatency">延迟: <span class="font-mono text-cyan-400">{{ channelData?.latency ?? 0 }} ms</span></span>
              <span>CT: <span class="font-mono text-cyan-400">{{ displayCt }}</span></span>
            </div>
            <div class="mt-2 grid grid-cols-3 gap-2" data-testid="single-channel-ct-metrics">
              <div v-for="metric in ctMetrics" :key="metric.label" class="rounded bg-slate-800/30 px-2 py-1 text-center">
                <div class="text-[0.625rem] text-gray-500">{{ metric.label }}</div>
                <div class="font-mono text-sm font-bold" :class="metric.color">{{ metric.value }}</div>
              </div>
            </div>
          </template>
          <div v-else class="py-4 text-center text-xs text-gray-500">请先选择项目以查看统计数据</div>
        </div>

        <div
          v-if="showDefectChart || showCapacityChart || showNgTop3 || layoutEditActive"
          data-layout-slot="summary-row"
          class="grid h-40 flex-shrink-0 gap-3"
          :style="{ gridTemplateColumns: `repeat(${summaryPanelCount}, minmax(0, 1fr))` }"
          data-testid="single-channel-summary-row"
        >
          <div
            v-if="showDefectChart"
            class="flex min-w-0 flex-col rounded-lg border border-slate-700 bg-slate-900 p-2"
            data-testid="single-channel-defect-card"
          >
            <h3 class="flex-shrink-0 text-base font-bold text-cyan-400">良品/不良统计</h3>
            <div class="min-h-0 flex-1">
              <GoodBadPieChart
                :good-count="channelData?.ok ?? 0"
                :bad-count="channelData?.ng ?? 0"
                test-id="single-channel-defect-chart"
              />
            </div>
          </div>

          <div
            v-if="showCapacityChart"
            class="flex min-w-0 flex-col rounded-lg border border-slate-700 bg-slate-900 p-2"
            data-testid="single-channel-yield-card"
          >
            <h3 class="flex-shrink-0 text-base font-bold text-cyan-400">合格率</h3>
            <div class="min-h-0 flex-1">
              <YieldRateGauge
                :good-count="channelData?.ok ?? 0"
                :total-count="channelData?.total ?? 0"
                test-id="single-channel-yield-gauge"
              />
            </div>
          </div>

          <div v-if="showNgTop3" class="flex min-w-0 flex-1 flex-col rounded-lg border border-slate-700 bg-slate-900 p-2" data-testid="single-channel-ng-top3">
            <div class="mb-1.5 flex items-center justify-between">
              <h3 class="text-base font-bold text-cyan-400">NG步骤TOP3</h3>
              <button v-if="!readonly" type="button" class="select-none text-xs text-gray-500 hover:text-cyan-400" @click="$emit('toggle-ng-top-mode')">
                {{ ngTopDisplayMode === 'percentage' ? '百分比' : '次数' }}
              </button>
              <span v-else class="text-xs text-gray-500">{{ ngTopDisplayMode === 'percentage' ? '百分比' : '次数' }}</span>
            </div>
            <div class="flex-1 space-y-1 overflow-y-auto">
              <div v-if="!channelData?.ngStepRanking?.length" class="flex h-full items-center justify-center text-base text-gray-600">暂无数据</div>
              <div v-for="(item, idx) in (channelData?.ngStepRanking || []).slice(0, 3)" :key="item.step" class="flex items-center gap-2 rounded bg-slate-800/50 px-2 py-1.5">
                <span class="w-6 text-center text-lg font-bold text-white">{{ idx + 1 }}</span>
                <span class="min-w-0 flex-1 truncate text-base text-gray-300">{{ item.step }}</span>
                <span class="text-lg font-bold text-white">{{ ngTopDisplayMode === 'count' ? item.count : `${Number(item.rate || 0).toFixed(1)}%` }}</span>
              </div>
            </div>
          </div>
        </div>

        <div v-if="showStepTable || layoutEditActive" data-layout-slot="step-table" class="flex min-h-0 flex-1 flex-col overflow-hidden rounded-lg border border-slate-700 bg-slate-900" data-testid="single-channel-step-table">
          <div class="flex flex-shrink-0 items-center justify-between border-b border-slate-700 bg-slate-800 px-3 py-2">
            <span class="text-lg font-bold text-cyan-400">步骤统计</span>
            <span class="rounded bg-slate-700 px-2 py-0.5 text-sm text-gray-300">CT: {{ displayCt }}</span>
          </div>
          <div class="min-h-0 flex-1 overflow-auto">
            <table class="w-full text-left text-sm">
              <thead class="sticky top-0 bg-slate-800 text-gray-400">
                <tr>
                  <th v-if="stepTableColumns.showNo !== false" class="px-2 py-1.5">No</th>
                  <th v-if="stepTableColumns.showStep !== false" class="px-2 py-1.5">步骤</th>
                  <th v-if="stepTableColumns.showStatus !== false" class="px-2 py-1.5">状态</th>
                  <th v-if="stepTableColumns.showPt !== false" class="px-2 py-1.5">PT/s</th>
                  <th v-if="stepTableColumns.showResult !== false" class="px-2 py-1.5">结果</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-slate-800 text-gray-300">
                <tr v-for="(row, idx) in tableRows" :key="row.label || idx" class="hover:bg-slate-800/50" :class="row.status === 'completed' ? 'bg-green-800/30' : ''">
                  <td v-if="stepTableColumns.showNo !== false" class="px-2 py-1.5">{{ idx + 1 }}</td>
                  <td v-if="stepTableColumns.showStep !== false" class="px-2 py-1.5">{{ row.step }}</td>
                  <td v-if="stepTableColumns.showStatus !== false" class="px-2 py-1.5">
                    <span :class="row.status === 'completed' ? 'text-white' : 'text-gray-500'">{{ row.status === 'completed' ? '已检测' : '待检测' }}</span>
                  </td>
                  <td v-if="stepTableColumns.showPt !== false" class="px-2 py-1.5 font-mono text-white">{{ row.status === 'completed' ? getStepPt(row.label) : '--' }}</td>
                  <td v-if="stepTableColumns.showResult !== false" class="px-2 py-1.5">
                    <span v-if="row.status === 'completed' && row.cycleResult === 'ok'" class="text-green-400">OK</span>
                    <span v-else-if="row.status === 'completed' && row.cycleResult === 'ng'" class="text-red-500">NG</span>
                    <span v-else class="text-gray-500">--</span>
                  </td>
                </tr>
                <tr v-if="!tableRows.length">
                  <td :colspan="visibleTableColumnCount" class="px-2 py-6 text-center text-gray-600">暂无步骤</td>
                </tr>
              </tbody>
            </table>
          </div>

        </div>

        <div
          data-layout-slot="controls"
          class="flex flex-shrink-0 gap-2 rounded-lg border border-slate-800 bg-slate-950 p-2"
          data-testid="single-channel-controls"
          :data-readonly="readonly ? 'true' : 'false'"
          :title="readonly ? '一期只读监看，控制操作将在二期开放' : ''"
        >
          <button type="button" class="control-button bg-emerald-500 hover:bg-emerald-400" :disabled="readonly || !hasProject || channelData?.isDetecting" data-testid="single-channel-start" @click="$emit('start')">开始</button>
          <button type="button" class="control-button bg-red-500 hover:bg-red-400" :disabled="readonly || !channelData?.isRunning" data-testid="single-channel-stop" @click="$emit('stop')">停止</button>
          <button type="button" class="control-button bg-yellow-600 hover:bg-yellow-500" :disabled="readonly || !channelData?.isDetecting" data-testid="single-channel-standby" @click="$emit('standby')">待机</button>
          <button type="button" class="control-button bg-cyan-500 hover:bg-cyan-400" :disabled="readonly || channelData?.isDetecting" data-testid="single-channel-reset" @click="$emit('reset')">清零</button>
        </div>
      </aside>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue';
import ChannelVideoCard from './ChannelVideoCard.vue';
import GoodBadPieChart from './GoodBadPieChart.vue';
import SopStepPanel from './SopStepPanel.vue';
import YieldRateGauge from './YieldRateGauge.vue';
import { layoutRuntimeState } from './layout/monitorLayout';

const props = defineProps({
  channelId: { type: Number, required: true },
  channelData: { type: Object, default: null },
  modelStats: { type: Array, default: () => [] },
  readonly: { type: Boolean, default: true },
  kiosk: { type: Boolean, default: false },
  hasProject: { type: Boolean, default: false },
  displayCt: { type: String, default: '--' },
  showFps: { type: Boolean, default: true },
  showLatency: { type: Boolean, default: true },
  showStepStrip: { type: Boolean, default: true },
  showStatsPanel: { type: Boolean, default: true },
  showDefectChart: { type: Boolean, default: true },
  showCapacityChart: { type: Boolean, default: true },
  showStepTable: { type: Boolean, default: true },
  stepTableColumns: { type: Object, default: () => ({}) },
  defaultCounters: { type: Object, default: () => ({}) },
  showNgTop3: { type: Boolean, default: true },
  ngTopDisplayMode: { type: String, default: 'percentage' },
  getStepPt: { type: Function, default: () => '--' },
  registerVideoCanvas: { type: Function, required: true },
  registerOverlayCanvas: { type: Function, required: true },
});

defineEmits(['back', 'previous', 'next', 'start', 'stop', 'standby', 'reset', 'toggle-ng-top-mode']);

// v3.54 自定义布局: 编辑态强制渲染受显示开关控制的区块 (kiosk 只读不进编辑态)
const { editMode: layoutEditMode } = layoutRuntimeState();
const layoutEditActive = computed(() => layoutEditMode.value && !props.kiosk);

const steps = computed(() => props.channelData?.steps || []);
const tableRows = computed(() => props.channelData?.tableData || []);
const yieldRate = computed(() => props.channelData?.yieldRate ?? 0);
const summaryPanelCount = computed(() => [props.showDefectChart, props.showCapacityChart, props.showNgTop3].filter(Boolean).length || 1);
const builtinStats = computed(() => [
  { label: '总产量', value: props.channelData?.total ?? 0, color: 'text-white', visible: props.defaultCounters.showTotal !== false },
  { label: '合格总数', value: props.channelData?.ok ?? 0, color: 'text-green-400', visible: props.defaultCounters.showGood !== false },
  { label: '不良总数', value: props.channelData?.ng ?? 0, color: 'text-red-500', visible: props.defaultCounters.showBad !== false },
].filter((item) => item.visible));
const visibleTableColumnCount = computed(() => [
  props.stepTableColumns.showNo,
  props.stepTableColumns.showStep,
  props.stepTableColumns.showStatus,
  props.stepTableColumns.showPt,
  props.stepTableColumns.showResult,
].filter((value) => value !== false).length || 1);
const formatSeconds = (value) => Number(value || 0) > 0 ? `${Number(value).toFixed(1)}s` : '--';
const ctMetrics = computed(() => [
  { label: '平均 CT', value: formatSeconds(props.channelData?.avgCycleTime), color: 'text-cyan-400' },
  { label: '上次 CT', value: formatSeconds(props.channelData?.lastCycleTime), color: 'text-white' },
  { label: '当前周期', value: formatSeconds(props.channelData?.currentCycleTime), color: 'text-yellow-400' },
]);
</script>

<style scoped>
.control-button {
  @apply flex-1 rounded py-2.5 text-lg font-bold text-white shadow transition-colors disabled:cursor-not-allowed disabled:bg-gray-600;
}
.nav-button {
  @apply rounded border border-slate-600 bg-slate-800 px-2.5 py-1 text-xs font-bold text-gray-200 hover:border-cyan-500 hover:text-cyan-300;
}
</style>
