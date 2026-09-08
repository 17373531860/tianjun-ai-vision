<template>
  <!-- OCR 读字模式专属看板 (2026-09): 消费 results.ai_mode 快照, 纯展示零写通路 (kiosk 天然只读) -->
  <div class="flex flex-col gap-2 p-2 min-h-0 overflow-y-auto">
    <div v-if="!snap" class="text-gray-500 text-xs text-center py-4">等待 OCR 采样数据…（开始检测后按采样间隔更新）</div>

    <template v-else>
      <div v-if="snap.available === false" class="bg-red-900/30 border border-red-700 rounded p-2 text-xs text-red-300">
        {{ snap.error || 'OCR 引擎不可用' }}
      </div>

      <div v-for="rule in (snap.rules || [])" :key="rule.id"
        class="bg-slate-800 rounded border p-2 space-y-1"
        :class="rule.matched === false ? 'border-red-600' : (rule.triggered ? 'border-emerald-500' : 'border-slate-700')">
        <div class="flex items-center justify-between text-xs">
          <span class="font-bold text-white truncate" :title="rule.name">{{ rule.name }}</span>
          <span v-if="rule.matched === true" class="text-emerald-400 font-bold whitespace-nowrap">匹配</span>
          <span v-else-if="rule.matched === false" class="text-red-400 font-bold whitespace-nowrap">不匹配</span>
          <span v-else class="text-gray-500 whitespace-nowrap">{{ rule.stable > 0 ? `稳定中 ${rule.stable}/${rule.stable_reads}` : '等待文本' }}</span>
        </div>
        <div class="bg-slate-900 rounded px-2 py-1.5 font-mono text-sm break-all"
          :class="rule.last_text ? 'text-cyan-300' : 'text-gray-600'">
          {{ rule.last_text || '（未读到文字）' }}
        </div>
        <div class="flex items-center justify-between text-[11px] text-gray-500">
          <span v-if="rule.pattern" class="truncate" :title="rule.pattern">匹配式: {{ rule.pattern }}</span>
          <span v-else>任意文字即匹配</span>
          <span v-if="rule.last_score">置信 {{ (rule.last_score * 100).toFixed(0) }}%</span>
        </div>
      </div>

      <div v-if="snap.available !== false && !(snap.rules || []).length"
        class="text-amber-400 text-xs text-center py-3">未配置读字规则——到项目「逻辑设置」页添加</div>

      <div v-if="snap.updated_at" class="text-[10px] text-gray-600 text-right">
        采样间隔 {{ snap.interval_s }}s · 更新于 {{ fmtTime(snap.updated_at) }}
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed } from 'vue';

const props = defineProps({
  state: { type: Object, default: null },   // results.ai_mode 快照 (mode='ocr')
  readonly: { type: Boolean, default: false },  // 纯展示面板, 保留签名与其他模式面板一致
});

const snap = computed(() => (props.state && props.state.mode === 'ocr') ? props.state : null);

const fmtTime = (ts) => {
  try {
    return new Date(ts * 1000).toLocaleTimeString('zh-CN', { hour12: false });
  } catch { return ''; }
};
</script>
