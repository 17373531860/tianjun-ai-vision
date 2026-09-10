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
        v-if="!kiosk && showNavigation"
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

      <div v-if="!kiosk && showNavigation" class="ml-auto flex items-center gap-1.5">
        <button type="button" class="nav-button" data-testid="single-channel-previous" @click="$emit('previous')">‹ 上一路</button>
        <button type="button" class="nav-button" data-testid="single-channel-next" @click="$emit('next')">下一路 ›</button>
      </div>
    </header>

    <div v-if="$slots['context-bar'] || layoutEditActive" data-layout-slot="context-bar">
      <slot name="context-bar" />
    </div>

    <div class="grid min-h-0 flex-1 grid-cols-12 gap-3">
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

        <WorkstationModePanel
          v-if="((showStepStrip || layoutEditActive) && modePanelKind)"
          data-layout-slot="sop"
          :mode="modePanelKind"
          :ch="channelId"
          :ch-data="channelData"
          :readonly="readonly"
          :compact="false"
        />
        <SopStepPanel
          v-else-if="(showStepStrip && steps.length > 0) || layoutEditActive"
          data-layout-slot="sop"
          :steps="steps"
          :step-intervals="channelData?.stepIntervals || {}"
        />
        <div v-else-if="showStepStrip && !hasProject" class="flex h-40 flex-shrink-0 items-center justify-center rounded-lg border border-slate-700 bg-slate-900 text-sm text-gray-500">
          请先选择项目
        </div>

        <!-- v3.55.x 混合模式物品校验 (装箱清点三分框/混合逐件): 放大态与 kiosk 同源;
             未配 custom_mix 零差异, 显示设置「物品校验面板」可整体关。kiosk 只读由 readonly 透传守住写通路。 -->
        <PerItemPanel
          v-if="showMixPanel && mixPerItemState"
          data-layout-slot="mix-panel"
          class="flex-shrink-0"
          :state="mixPerItemState"
          :channel="channelId"
          :readonly="readonly"
          mix
        />
        <CustomMixItemPanel
          v-else-if="showMixPanel && mixTrackingState"
          data-layout-slot="mix-panel"
          class="flex-shrink-0"
          :state="mixTrackingState"
          :tracking-checklist="channelData?.tracking?.item_checklist || {}"
        />

        <!-- v3.56: 周期多码采集已扫列表 — 放大态与 kiosk 同源; kiosk 只读由 readonly 屏蔽纠错按钮 -->
        <ScanSlotsPanel
          v-if="channelData?.scanCollect || layoutEditActive"
          data-layout-slot="scan-slots"
          class="flex-shrink-0"
          :state="channelData?.scanCollect"
          :channel-id="channelId"
          :readonly="readonly"
        />
      </div>

      <ChannelDashboard
        :channel-data="channelData"
        :readonly="readonly"
        :has-project="hasProject"
        :display-ct="displayCt"
        :show-fps="showFps"
        :show-latency="showLatency"
        :show-stats-panel="showStatsPanel"
        :show-defect-chart="showDefectChart"
        :show-capacity-chart="showCapacityChart"
        :show-step-table="showStepTable"
        :step-table-columns="stepTableColumns"
        :default-counters="defaultCounters"
        :showNgTop3="showNgTop3"
        :ng-top-display-mode="ngTopDisplayMode"
        :get-step-pt="getStepPt"
        :layout-edit-active="layoutEditActive"
        :mode-panel-kind="modePanelKind"
        @toggle-ng-top-mode="$emit('toggle-ng-top-mode')"
        @start="$emit('start')"
        @stop="$emit('stop')"
        @standby="$emit('standby')"
        @reset="$emit('reset')"
      />
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue';
import ChannelVideoCard from './ChannelVideoCard.vue';
import SopStepPanel from './SopStepPanel.vue';
import WorkstationModePanel from './WorkstationModePanel.vue';
import CustomMixItemPanel from './CustomMixItemPanel.vue';
import PerItemPanel from './PerItemPanel.vue';
import ScanSlotsPanel from './ScanSlotsPanel.vue';
import ChannelDashboard from './ChannelDashboard.vue';
import { layoutRuntimeState } from './layout/monitorLayout';
import { resolveModePanelKind } from './monitorModes';

const props = defineProps({
  channelId: { type: Number, required: true },
  channelData: { type: Object, default: null },
  modelStats: { type: Array, default: () => [] },
  readonly: { type: Boolean, default: true },
  kiosk: { type: Boolean, default: false },
  showNavigation: { type: Boolean, default: true },
  hasProject: { type: Boolean, default: false },
  displayCt: { type: String, default: '--' },
  showFps: { type: Boolean, default: true },
  showLatency: { type: Boolean, default: true },
  showStepStrip: { type: Boolean, default: true },
  showStatsPanel: { type: Boolean, default: true },
  showDefectChart: { type: Boolean, default: true },
  showCapacityChart: { type: Boolean, default: true },
  showStepTable: { type: Boolean, default: true },
  // v3.55.x 显示设置「物品校验面板」(display.monitor.mixPanel), 父级传入与其他开关同构
  showMixPanel: { type: Boolean, default: true },
  stepTableColumns: { type: Object, default: () => ({}) },
  defaultCounters: { type: Object, default: () => ({}) },
  showNgTop3: { type: Boolean, default: true },
  ngTopDisplayMode: { type: String, default: 'percentage' },
  getStepPt: { type: Function, default: () => '--' },
  registerVideoCanvas: { type: Function, required: true },
  registerOverlayCanvas: { type: Function, required: true },
});

defineEmits(['back', 'previous', 'next', 'start', 'stop', 'standby', 'reset', 'toggle-ng-top-mode']);

const { editMode: layoutEditMode } = layoutRuntimeState();
const layoutEditActive = computed(() => layoutEditMode.value && !props.kiosk);
const steps = computed(() => props.channelData?.steps || []);
const modePanelKind = computed(() =>
  resolveModePanelKind(props.channelData?._pollProjectConfig, props.channelData?.project) || null,
);

// v3.55.x 混合模式物品校验: 通道轮询带 custom_mix_state 才渲染 (未配置 = null 零差异)
const mixTrackingState = computed(() => {
  const s = props.channelData?.customMixState;
  return (s && s.mix_type === 'tracking') ? s : null;
});
// 混合逐件: 与单工位 customMixPerItemState 同构适配 (config=null 隐藏手动按钮/收尾卡片)
const mixPerItemState = computed(() => {
  const s = props.channelData?.customMixState;
  if (!s || s.mix_type !== 'per_item') return null;
  return {
    enabled: true,
    cycle_active: !!s.cycle_active,
    cycle_start_time: null,
    config: null,
    steps: s.steps || [],
    last_ng_detail: s.last_ng_detail || null,
  };
});
</script>

<style scoped>
.nav-button {
  @apply rounded border border-slate-600 bg-slate-800 px-2.5 py-1 text-xs font-bold text-gray-200 hover:border-cyan-500 hover:text-cyan-300;
}
</style>
