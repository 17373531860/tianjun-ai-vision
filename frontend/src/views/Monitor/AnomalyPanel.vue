<template>
  <!-- 异常检测模式专属看板 (2026-09): 消费 results.ai_mode 快照, 纯展示零写通路 (kiosk 天然只读) -->
  <div class="flex flex-col gap-2 p-2 min-h-0 overflow-y-auto">
    <div v-if="!snap" class="text-gray-500 text-xs text-center py-4">等待异常检测采样数据…（开始检测后按采样间隔更新）</div>

    <template v-else>
      <div v-if="snap.available === false" class="bg-red-900/30 border border-red-700 rounded p-2 text-xs text-red-300">
        {{ snap.error || '异常检测引擎不可用' }}
      </div>

      <template v-else>
        <!-- 状态大灯 -->
        <div class="rounded border p-3 text-center"
          :class="snap.in_alarm ? 'bg-red-900/40 border-red-600' : (snap.is_anomaly ? 'bg-amber-900/30 border-amber-600' : 'bg-emerald-900/20 border-emerald-700')">
          <div class="text-2xl font-bold" :class="snap.in_alarm ? 'text-red-400' : (snap.is_anomaly ? 'text-amber-400' : 'text-emerald-400')">
            {{ snap.in_alarm ? '异常报警中' : (snap.is_anomaly ? '疑似异常' : '正常') }}
          </div>
          <div v-if="snap.is_anomaly && !snap.in_alarm" class="text-[11px] text-amber-300 mt-1">
            连续 {{ snap.consec_ng }}/{{ snap.consecutive }} 次超阈值（达到即报警）
          </div>
          <div v-if="snap.cooldown_remaining > 0" class="text-[11px] text-gray-400 mt-1">
            报警冷却 {{ snap.cooldown_remaining }}s
          </div>
        </div>

        <!-- 分数条 -->
        <div class="bg-slate-800 rounded border border-slate-700 p-2 space-y-1.5">
          <div class="flex items-center justify-between text-xs">
            <span class="text-gray-400">异常分</span>
            <span class="font-mono font-bold" :class="snap.is_anomaly ? 'text-red-400' : 'text-emerald-400'">
              {{ snap.score }} / 阈值 {{ snap.threshold }}
            </span>
          </div>
          <div class="h-2 bg-slate-900 rounded overflow-hidden relative">
            <div class="h-full transition-all duration-300"
              :class="snap.is_anomaly ? 'bg-red-500' : 'bg-emerald-500'"
              :style="{ width: `${scorePct}%` }"></div>
            <!-- 阈值刻度线 (固定在 2/3 处, 分数条按 1.5x 阈值满格) -->
            <div class="absolute top-0 bottom-0 w-px bg-amber-400" style="left: 66.7%"></div>
          </div>
          <div class="flex items-center justify-between text-[10px] text-gray-600">
            <span>记忆库 {{ snap.bank_id }}</span>
            <span v-if="snap.backbone">骨干 {{ snap.backbone }}</span>
          </div>
        </div>

        <div v-if="snap.updated_at" class="text-[10px] text-gray-600 text-right">
          采样间隔 {{ snap.interval_s }}s · 更新于 {{ fmtTime(snap.updated_at) }}
        </div>
      </template>
    </template>
  </div>
</template>

<script setup>
import { computed } from 'vue';

const props = defineProps({
  state: { type: Object, default: null },   // results.ai_mode 快照 (mode='anomaly')
  readonly: { type: Boolean, default: false },  // 纯展示面板, 保留签名与其他模式面板一致
});

const snap = computed(() => (props.state && props.state.mode === 'anomaly') ? props.state : null);

// 分数条: 分数=阈值时正好到 66.7% 刻度线, 满格 = 1.5x 阈值 (超出夹 100%)
const scorePct = computed(() => {
  if (!snap.value || !snap.value.threshold) return 0;
  return Math.max(2, Math.min(100, (snap.value.score / snap.value.threshold) * 66.7));
});

const fmtTime = (ts) => {
  try {
    return new Date(ts * 1000).toLocaleTimeString('zh-CN', { hour12: false });
  } catch { return ''; }
};
</script>
