<template>
  <!-- 单根 div: 父级 data-layout-slot / data-testid / class 经 attrs fallthrough。 -->
  <div class="flex flex-col min-h-0 min-w-0 overflow-y-auto" :data-mode-panel="mode">
    <TrackingChecklistPanel v-if="mode === 'tracking'" :tracking="tracking" :compact="compact" />
    <PerItemPanel v-else-if="mode === 'per_item'" class="flex-1 min-h-0"
      :state="chData?.perItemState || null" :channel="ch" :readonly="readonly" />
    <WeighingPanel v-else-if="mode === 'weighing'" class="flex-1 min-h-0"
      :channel="ch" :readonly="readonly" />
  </div>
</template>

<script setup>
/**
 * WorkstationModePanel — 按工位 logic_mode 切工艺主面板（v3.55）
 *
 * tracking / per_item / weighing 换专属看板；步骤类模式由父级继续画 SOP。
 * readonly=true 时屏蔽称重/逐件写按钮（kiosk 只读副屏）。
 */
import { computed } from 'vue';
import PerItemPanel from './PerItemPanel.vue';
import WeighingPanel from './WeighingPanel.vue';
import TrackingChecklistPanel from './TrackingChecklistPanel.vue';

const props = defineProps({
  mode: { type: String, required: true },
  ch: { type: Number, required: true },
  chData: { type: Object, default: null },
  readonly: { type: Boolean, default: false },
  compact: { type: Boolean, default: true },
});

const tracking = computed(() => props.chData?.tracking || {});
</script>
