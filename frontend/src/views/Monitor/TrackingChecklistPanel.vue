<template>
  <div class="bg-slate-900 border border-slate-700 rounded-lg overflow-hidden flex flex-col"
       :class="compact ? 'flex-1 min-h-[6rem]' : 'h-44'">
    <div class="bg-slate-800 border-b border-slate-700 flex-shrink-0 flex justify-between items-center"
         :class="compact ? 'px-2 py-1' : 'px-3 py-1'">
      <span class="text-cyan-400 font-bold" :class="compact ? 'text-sm' : 'text-lg'">
        {{ tracking.container_mode ? '容器清点' : '物品清点' }}
      </span>
      <div class="flex items-center gap-2">
        <span v-if="tracking.container_mode && (tracking.settled_boxes || 0) > 0"
          class="text-[0.625rem] px-1.5 py-0.5 rounded"
          :class="(tracking.settled_ng || 0) > 0 ? 'bg-red-500/20 text-red-400' : 'bg-green-500/20 text-green-400'">
          已结算 {{ tracking.settled_boxes }} (OK:{{ tracking.settled_ok || 0 }} NG:{{ tracking.settled_ng || 0 }})
        </span>
        <span v-if="tracking.cycle_active" class="text-xs text-green-400 animate-pulse">跟踪中...</span>
        <span v-else class="text-xs text-gray-500">等待</span>
      </div>
    </div>
    <div class="flex-1 overflow-x-auto min-h-0" :class="compact ? 'p-1.5' : 'p-2'">
      <div v-if="tracking.container_mode" class="flex items-stretch h-full" :class="compact ? 'gap-2' : 'gap-3'">
        <div v-for="(box, boxDid) in boxes" :key="boxDid"
          class="flex-shrink-0 bg-slate-800 rounded-lg border flex flex-col transition-all"
          :class="[compact ? 'w-40 p-1.5' : 'w-44 p-2', box.is_complete ? 'border-green-500/70' : 'border-amber-500/70']">
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
        <div v-if="Object.keys(boxes).length === 0"
          class="flex items-center justify-center text-gray-500 w-full"
          :class="compact ? 'text-xs' : 'text-sm'">
          等待容器出现...
        </div>
      </div>
      <div v-else class="flex items-stretch h-full" :class="compact ? 'gap-2' : 'gap-3'">
        <div v-for="(info, cls) in flatChecklist" :key="cls"
          class="flex-shrink-0 bg-slate-800 rounded-lg border flex flex-col justify-between transition-all"
          :class="[
            compact ? 'w-32 p-1.5' : 'w-36 p-2',
            info.counted >= info.expected && info.expected > 0 ? 'border-green-500/70'
              : info.counted > info.expected && info.expected > 0 ? 'border-red-500/70' : 'border-slate-700',
          ]">
          <div class="text-gray-400 truncate" :class="compact ? 'text-[0.625rem]' : 'text-xs'">{{ info.display_name || cls }}</div>
          <div class="text-center" :class="compact ? 'my-0.5' : 'my-1'">
            <span class="font-bold font-mono"
              :class="[
                compact ? 'text-2xl' : 'text-3xl',
                info.counted >= info.expected && info.expected > 0 ? 'text-green-400' : 'text-white',
              ]">{{ info.counted }}</span>
            <span v-if="info.expected > 0" class="text-gray-500" :class="compact ? 'text-xs' : 'text-sm'"> / {{ info.expected }}</span>
          </div>
          <div class="text-gray-500 text-center" :class="compact ? 'text-[0.5625rem]' : 'text-[0.625rem]'">
            {{ info.prefix }}1 ~ {{ info.prefix }}{{ info.counted || '?' }}
          </div>
        </div>
        <div v-if="Object.keys(flatChecklist).length === 0"
          class="flex items-center justify-center text-gray-500 w-full"
          :class="compact ? 'text-xs' : 'text-sm'">
          等待物品出现...
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
/**
 * 跟踪模式物品/容器清点看板。compact=多工位列窄卡；默认=单工位/放大态原尺寸。
 * 数据契约 = results.tracking（item_checklist / boxes / container_mode / settled_*）。
 */
import { computed } from 'vue';

const props = defineProps({
  tracking: { type: Object, default: () => ({}) },
  compact: { type: Boolean, default: false },
});

const checklist = computed(() => props.tracking?.item_checklist || {});
const boxes = computed(() => props.tracking?.boxes || {});
const flatChecklist = computed(() => {
  const out = {};
  for (const [k, v] of Object.entries(checklist.value)) {
    if (!k.startsWith('_')) out[k] = v;
  }
  return out;
});
</script>
