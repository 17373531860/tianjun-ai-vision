<template>
  <!-- ==================== 融合模式实时称重数值条 (v3.35.1) ====================
       视觉 SOP 驱动 + 秤做步骤门控时, SOP 面板占核心位, 称重数值以横条补充展示:
       实时读数 / 皮重(去皮时的工件·容器自重) / 净重(去皮后读数=已投料) / 门控状态 / 最近判定。
       由项目称重配置「检测中心显示实时称重数值条」开关控制显隐 (父级判断)。 -->
  <div v-if="state" class="bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 flex flex-wrap items-center gap-x-5 gap-y-1">
    <span class="text-cyan-400 text-sm font-bold flex-shrink-0">称重实时</span>

    <span class="flex items-baseline gap-1.5">
      <span class="text-gray-400 text-xs">{{ tared ? '净重(已投料)' : '当前读数' }}</span>
      <span class="text-xl font-mono font-bold" :class="liveWeight != null ? 'text-white' : 'text-gray-600'">
        {{ liveWeight != null ? liveWeight.toFixed(3) : '--.---' }}</span>
      <span class="text-xs text-gray-500">kg</span>
    </span>

    <span class="flex items-baseline gap-1.5">
      <span class="text-gray-400 text-xs">皮重(工件/容器)</span>
      <span class="text-xl font-mono font-bold" :class="tareWeight != null ? 'text-amber-300' : 'text-gray-600'">
        {{ tareWeight != null ? tareWeight.toFixed(3) : '--.---' }}</span>
      <span class="text-xs text-gray-500">kg</span>
    </span>

    <span v-if="state.model_name" class="text-xs text-gray-400">
      型号 <b class="text-white">{{ state.model_name }}</b>
    </span>

    <!-- 步骤门控状态 chips -->
    <span v-for="(st, lbl) in (state.gates || {})" :key="lbl"
          class="text-xs px-2 py-0.5 rounded font-bold"
          :class="st === 'passed' ? 'bg-green-600/25 text-green-300' : 'bg-amber-600/25 text-amber-300 animate-pulse'">
      {{ lbl }}{{ st === 'passed' ? ' ✓' : ' 等秤…' }}
    </span>

    <span class="flex-1"></span>

    <!-- 最近一次判定 -->
    <span v-if="lastResult" class="text-xs px-2 py-0.5 rounded font-bold" :class="verdictClass(lastResult.verdict)">
      {{ lastResult.material }} 实投 {{ Number(lastResult.net).toFixed(3) }}kg
      <template v-if="lastResult.standard != null"> / 标准 {{ Number(lastResult.standard).toFixed(3) }}kg</template>
      · {{ verdictText(lastResult.verdict) }}
    </span>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount, watch } from 'vue';
import { getWeighingState } from '@/api/weighing';

const props = defineProps({
  channel: { type: Number, default: 0 },
});

const state = ref(null);
let timer = null;

const liveWeight = computed(() => {
  const w = state.value?.live_weight;
  return (typeof w === 'number') ? w : null;
});
const tareWeight = computed(() => {
  const w = state.value?.tare_weight;
  return (typeof w === 'number') ? w : null;
});
// 已记录皮重 = 已去皮 → 当前读数就是净重
const tared = computed(() => tareWeight.value != null);
const lastResult = computed(() => {
  const rs = state.value?.results || [];
  return rs.length ? rs[rs.length - 1] : null;
});

const verdictText = (v) => ({ ok: '合格', shortage: '缺料', over: '超量', no_spec: '未配标准' }[v] || v);
const verdictClass = (v) => ({
  ok: 'bg-green-600/25 text-green-300',
  shortage: 'bg-red-600/25 text-red-300',
  over: 'bg-orange-600/25 text-orange-300',
  no_spec: 'bg-slate-600/40 text-gray-300',
}[v] || 'bg-slate-600/40 text-gray-300');

const refresh = async () => {
  try {
    const { data } = await getWeighingState(props.channel);
    state.value = data || null;
  } catch (e) {
    // 通道未登记称重引擎时静默 (父级已按项目配置守门显隐)
  }
};

watch(() => props.channel, () => { state.value = null; refresh(); });
onMounted(() => { refresh(); timer = setInterval(refresh, 800); });
onBeforeUnmount(() => { if (timer) clearInterval(timer); });
</script>
