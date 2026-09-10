<template>
  <!-- ==================== v3.19.x 自定义混合模式物品校验面板（2026-07 拆分批次 M-3 自 index.vue 外置） ====================
       混合类型=tracking 时的物品校验看板, 与上方 SOP 面板并存（步骤看 SOP, 物品看这里）。
       排他条件(v-else-if 链: 逐件适配态优先走 PerItemPanel)留在父级调用点。
       state = 轮询下发的混合模式状态, trackingChecklist = 独立跟踪同源的物品清单,
       均由父级轮询维护, 纯展示无 emit。 -->
  <div class="h-44 bg-slate-900 border border-slate-700 rounded-lg overflow-hidden flex flex-col">
    <div class="bg-slate-800 px-3 py-1 border-b border-slate-700 flex-shrink-0 flex justify-between items-center">
      <span class="text-cyan-400 text-lg font-bold">
        {{ customMixContainer ? `物品校验 · ${customMixContainer.container_display || '容器'}装箱` : '物品校验 · 跟踪清点' }}
      </span>
      <div class="flex items-center gap-3">
        <!-- 总数模式: 已进箱滑块总数 N/目标 -->
        <span v-if="customMixItemTotal" class="text-sm font-bold"
          :class="customMixItemTotal.target > 0 && customMixItemTotal.done === customMixItemTotal.target ? 'text-green-400' : 'text-amber-400'">
          已进箱{{ customMixItemTotal.display || '滑块' }}
          {{ customMixItemTotal.done }}<template v-if="customMixItemTotal.target > 0"> / {{ customMixItemTotal.target }}</template>
        </span>
        <!-- 盘计数模式: 已装托盘累计 N/每箱 -->
        <span v-else-if="customMixContainer" class="text-sm font-bold"
          :class="customMixContainer.box_count > 0 && customMixContainer.trays_done >= customMixContainer.box_count ? 'text-green-400' : 'text-amber-400'">
          已装{{ customMixContainer.container_display || '托盘' }}
          {{ customMixContainer.trays_done }}<template v-if="customMixContainer.box_count > 0"> / {{ customMixContainer.box_count }}</template>
        </span>
        <span v-if="state.cycle_active" class="text-xs text-green-400 animate-pulse">周期中...</span>
        <span v-else class="text-xs text-gray-500">等待周期开始（由步骤驱动）</span>
      </div>
    </div>
    <!-- 容器模式: 显示"当前正在装的托盘"实时滑块数 + 已装托盘进度 -->
    <div v-if="customMixContainer" class="flex-1 p-2 overflow-x-auto">
      <div class="flex items-stretch h-full gap-3">
        <template v-for="it in customMixContainer.current_tray_items" :key="it.label">
          <div class="flex-shrink-0 w-32 bg-slate-800 rounded-lg border border-cyan-700/60 p-2 flex flex-col justify-between">
            <div class="text-xs text-gray-400 truncate">实时 · {{ it.display_name || it.label }}</div>
            <div class="text-center my-1">
              <span class="text-4xl font-bold font-mono text-cyan-400">{{ it.current_count ?? 0 }}</span>
            </div>
            <div class="text-[0.625rem] text-gray-500 text-center">当前帧在位数</div>
          </div>
          <div class="flex-shrink-0 w-36 bg-slate-800 rounded-lg border p-2 flex flex-col justify-between"
            :class="!customMixItemTotal && it.expected_per_tray > 0 && (it.peak_count ?? 0) >= it.expected_per_tray ? 'border-green-500/70' : 'border-amber-500/50'">
            <div class="text-xs text-gray-400 truncate">{{ customMixContainer.container_display || '托盘' }}峰值 · {{ it.display_name || it.label }}</div>
            <div class="text-center my-1">
              <span class="text-4xl font-bold font-mono"
                :class="!customMixItemTotal && it.expected_per_tray > 0 && (it.peak_count ?? 0) >= it.expected_per_tray ? 'text-green-400' : 'text-white'"
              >{{ it.peak_count ?? 0 }}</span>
              <span v-if="!customMixItemTotal && it.expected_per_tray > 0" class="text-base text-gray-500"> / {{ it.expected_per_tray }}</span>
            </div>
            <div class="text-[0.625rem] text-gray-500 text-center">本盘在位最高</div>
          </div>
          <div class="flex-shrink-0 w-36 bg-slate-800 rounded-lg border border-emerald-700/60 p-2 flex flex-col justify-between">
            <div class="text-xs text-gray-400 truncate">预计进箱 · {{ it.display_name || it.label }}</div>
            <div class="text-center my-1">
              <span class="text-4xl font-bold font-mono text-emerald-400">{{ it.book_preview ?? it.peak_count ?? 0 }}</span>
            </div>
            <div v-if="slotView" class="text-[0.6rem] text-center"
              :class="slotView.ok ? 'text-emerald-400' : 'text-amber-400'">
              {{ slotView.ok ? '本帧看全' : '本帧未看全' }}
              · 货{{ slotView.items }}+空{{ slotView.empty }}={{ slotView.items + slotView.empty }}/{{ slotView.total }}
            </div>
            <div v-else class="text-[0.625rem] text-gray-500 text-center">结账将记入的数</div>
          </div>
        </template>
        <!-- 已装托盘明细 (每盘装了多少) -->
        <div v-if="(customMixContainer.done_detail || []).length"
          class="flex-shrink-0 min-w-32 bg-slate-800/60 rounded-lg border border-slate-700 p-2 flex flex-col">
          <div class="text-xs text-gray-400 mb-1">已装明细</div>
          <div class="flex-1 overflow-y-auto space-y-0.5">
            <div v-for="(tray, idx) in customMixContainer.done_detail" :key="idx"
              class="text-[0.7rem] text-gray-300 font-mono">
              第{{ idx + 1 }}盘: {{ Object.values(tray).join('/') }}
            </div>
          </div>
        </div>
        <div v-if="!customMixContainer.current_tray_items.length"
          class="flex items-center justify-center text-gray-500 text-sm w-full">
          等待{{ customMixContainer.container_display || '托盘' }}出现...
        </div>
      </div>
    </div>
    <div v-else class="flex-1 p-2 overflow-x-auto">
      <!-- 复用独立跟踪模式的物品清单数据 (后端 _rebuild_checklist 同一来源) -->
      <div class="flex items-stretch h-full gap-3">
        <div v-for="(info, cls) in trackingChecklist" :key="cls"
          class="flex-shrink-0 w-36 bg-slate-800 rounded-lg border p-2 flex flex-col justify-between transition-all"
          :class="info.counted >= info.expected && info.expected > 0 ? 'border-green-500/70' : info.counted > info.expected && info.expected > 0 ? 'border-red-500/70' : 'border-slate-700'"
        >
          <div class="text-xs text-gray-400 truncate">{{ info.display_name || cls }}</div>
          <div class="text-center my-1">
            <span class="text-3xl font-bold font-mono"
              :class="info.counted >= info.expected && info.expected > 0 ? 'text-green-400' : 'text-white'"
            >{{ info.counted }}</span>
            <span v-if="info.expected > 0" class="text-sm text-gray-500"> / {{ info.expected }}</span>
          </div>
          <div v-if="(customMixItemByLabel[cls]?.partials || []).length"
            class="text-[0.625rem] text-red-400 text-center truncate"
            :title="customMixItemByLabel[cls].partials.map(p => `${p.peak}/${p.required}`).join(', ')">
            缺件批次: {{ customMixItemByLabel[cls].partials.map(p => `${p.peak}/${p.required}`).join(', ') }}
          </div>
          <div v-else class="text-[0.625rem] text-gray-500 text-center">{{ info.prefix }}1 ~ {{ info.prefix }}{{ info.counted || '?' }}</div>
        </div>
        <div v-if="Object.keys(trackingChecklist).length === 0"
          class="flex items-center justify-center text-gray-500 text-sm w-full">
          等待物品出现...
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue';

const props = defineProps({
  // 轮询下发的自定义混合模式状态 (custom_mix_state), 调用点已保证非空且 mix_type === 'tracking'
  state: { type: Object, required: true },
  // 独立跟踪模式同源的物品清单 { label: {counted, expected, display_name, prefix} }, 父级轮询维护
  trackingChecklist: { type: Object, default: () => ({}) },
});

// 混合跟踪卡片用: 按标签索引物品行状态 (堆叠批层的"缺件批次"明细显示)
const customMixItemByLabel = computed(() => {
  const map = {};
  for (const it of (props.state?.items || [])) map[it.label] = it;
  return map;
});
// 托盘容器累加器状态 (混合跟踪 + 配了容器标签时后端才下发; 否则 null = 走原扁平清单)
const customMixContainer = computed(() => {
  const c = props.state?.container;
  return (c && c.enabled) ? c : null;
});
// 总数模式 (items_total): 累加进箱滑块总数 / 整箱目标, 取首个被计数物品标签
const customMixItemTotal = computed(() => {
  const c = customMixContainer.value;
  if (!c || c.count_mode !== 'items_total') return null;
  const totals = c.item_total_done || {};
  const label = Object.keys(totals)[0] || (c.current_tray_items?.[0]?.label) || '';
  const done = Object.values(totals).reduce((a, b) => a + (b || 0), 0);
  const disp = c.current_tray_items?.find(i => i.label === label)?.display_name || label;
  return { done, target: c.item_target || 0, display: disp };
});
// v3.46 槽位完整性门本帧结果 (门没开则后端不下发)
const slotView = computed(() => customMixContainer.value?.slot_view || null);
</script>
