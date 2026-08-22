<template>
  <!-- 单根 div: 让父级 (WorkstationColumn) 的 data-layout-slot / data-testid / class 经
       attrs fallthrough 落在这里, 槽位契约 (dual=sop-row / triple=sop) 由父级供给。 -->
  <div class="flex flex-col min-h-0 min-w-0 overflow-y-auto" :data-mode-panel="mode">
    <!-- tracking: 物品/容器清点 (数据源 chData.tracking, 多工位轮询 v3.50+ 已带) -->
    <div v-if="mode === 'tracking'" class="flex-1 min-h-[6rem] bg-slate-900 border border-slate-700 rounded-lg overflow-hidden flex flex-col">
      <div class="bg-slate-800 px-2 py-1 border-b border-slate-700 flex-shrink-0 flex justify-between items-center">
        <span class="text-cyan-400 text-sm font-bold">{{ tracking.container_mode ? '容器清点' : '物品清点' }}</span>
        <div class="flex items-center gap-2">
          <span v-if="tracking.container_mode && (tracking.settled_boxes || 0) > 0" class="text-[0.625rem] px-1.5 py-0.5 rounded"
            :class="(tracking.settled_ng || 0) > 0 ? 'bg-red-500/20 text-red-400' : 'bg-green-500/20 text-green-400'">
            已结算 {{ tracking.settled_boxes }} (OK:{{ tracking.settled_ok || 0 }} NG:{{ tracking.settled_ng || 0 }})
          </span>
          <span v-if="tracking.cycle_active" class="text-xs text-green-400 animate-pulse">跟踪中...</span>
          <span v-else class="text-xs text-gray-500">等待</span>
        </div>
      </div>
      <div class="flex-1 p-1.5 overflow-x-auto min-h-0">
        <!-- 容器模式: 逐箱卡片 -->
        <div v-if="tracking.container_mode" class="flex items-stretch h-full gap-2">
          <div v-for="(box, boxDid) in trackingBoxes" :key="boxDid"
            class="flex-shrink-0 w-40 bg-slate-800 rounded-lg border p-1.5 flex flex-col transition-all"
            :class="box.is_complete ? 'border-green-500/70' : 'border-amber-500/70'">
            <div class="flex items-center justify-between mb-1">
              <span class="text-xs font-bold text-white">{{ boxDid }}</span>
              <span class="text-[0.625rem] px-1.5 py-0.5 rounded"
                :class="box.is_complete ? 'bg-green-500/20 text-green-400' : 'bg-amber-500/20 text-amber-400'">
                {{ box.is_complete ? 'OK' : '...' }}
              </span>
            </div>
            <div class="flex-1 space-y-0.5 overflow-y-auto">
              <template v-if="checklist._boxes && checklist._boxes[boxDid]">
                <div v-for="(info, cls) in checklist._boxes[boxDid].items" :key="cls"
                  class="flex items-center justify-between text-[0.625rem] px-1 py-0.5 rounded"
                  :class="info.counted >= info.expected && info.expected > 0 ? 'bg-green-500/10 text-green-400' : 'bg-slate-700/50 text-gray-400'">
                  <span class="truncate">{{ info.display_name || cls }}</span>
                  <span class="font-mono font-bold">{{ info.counted }}<span v-if="info.expected > 0" class="text-gray-500">/{{ info.expected }}</span></span>
                </div>
              </template>
            </div>
          </div>
          <div v-if="Object.keys(trackingBoxes).length === 0"
            class="flex items-center justify-center text-gray-500 text-xs w-full">
            等待容器出现...
          </div>
        </div>
        <!-- 普通模式: 平铺清单 -->
        <div v-else class="flex items-stretch h-full gap-2">
          <div v-for="(info, cls) in flatChecklist" :key="cls"
            class="flex-shrink-0 w-32 bg-slate-800 rounded-lg border p-1.5 flex flex-col justify-between transition-all"
            :class="info.counted >= info.expected && info.expected > 0 ? 'border-green-500/70' : info.counted > info.expected && info.expected > 0 ? 'border-red-500/70' : 'border-slate-700'">
            <div class="text-[0.625rem] text-gray-400 truncate">{{ info.display_name || cls }}</div>
            <div class="text-center my-0.5">
              <span class="text-2xl font-bold font-mono"
                :class="info.counted >= info.expected && info.expected > 0 ? 'text-green-400' : 'text-white'">{{ info.counted }}</span>
              <span v-if="info.expected > 0" class="text-xs text-gray-500"> / {{ info.expected }}</span>
            </div>
            <div class="text-[0.5625rem] text-gray-500 text-center">{{ info.prefix }}1 ~ {{ info.prefix }}{{ info.counted || '?' }}</div>
          </div>
          <div v-if="Object.keys(flatChecklist).length === 0"
            class="flex items-center justify-center text-gray-500 text-xs w-full">
            等待物品出现...
          </div>
        </div>
      </div>
    </div>

    <!-- per_item: 逐件覆盖面板 (数据源 chData.perItemState, v3.28 多工位轮询已带) -->
    <PerItemPanel v-else-if="mode === 'per_item'" class="flex-1 min-h-0" :state="chData?.perItemState || null" :channel="ch" />

    <!-- weighing: 称重投料看板 (自轮询 /weighing/state?channel=ch, 与单工位同一组件) -->
    <WeighingPanel v-else-if="mode === 'weighing'" class="flex-1 min-h-0" :channel="ch" />
  </div>
</template>

<script setup>
/**
 * WorkstationModePanel — 多工位列的按模式工艺面板（阶段3，v3.55 新功能）
 *
 * 治「双/三工位不管什么模式一律画通用 SOP 卡」：tracking / per_item /
 * weighing 三种模式在多工位列的 sop 槽位内换成与单工位同语义的专属面板。
 * sequential / detection / region_events 等步骤类模式仍走父级原 SOP 卡。
 *
 * 数据管道（均为存量, 零后端改动）：
 * - tracking:  results.tracking (item_checklist/_boxes/boxes/settled_*)
 * - per_item:  results.per_item_state (v3.28 起多工位轮询已带)
 * - weighing:  WeighingPanel 自轮询 GET /weighing/state?channel=ch
 */
import { computed } from 'vue';
import PerItemPanel from './PerItemPanel.vue';
import WeighingPanel from './WeighingPanel.vue';

const props = defineProps({
  mode: { type: String, required: true }, // 'tracking' | 'per_item' | 'weighing'
  ch: { type: Number, required: true },   // 零基工位号
  chData: { type: Object, default: null }, // multiChannelData[ch]
});

const tracking = computed(() => props.chData?.tracking || {});
const checklist = computed(() => tracking.value.item_checklist || {});
const trackingBoxes = computed(() => tracking.value.boxes || {});
// 平铺清单要剔除内部 _boxes 键（单工位视图 item_checklist 直接 v-for, 因容器模式
// 走另一分支不会撞上; 这里防御性过滤下划线内部键, 语义一致）
const flatChecklist = computed(() => {
  const out = {};
  for (const [k, v] of Object.entries(checklist.value)) {
    if (!k.startsWith('_')) out[k] = v;
  }
  return out;
});
</script>
