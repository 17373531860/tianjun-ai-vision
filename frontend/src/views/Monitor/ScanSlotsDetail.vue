<template>
  <!-- v3.56 多码采集逐槽位明细 (ScanSlotsPanel 完整态直嵌 / 紧凑态浮层复用) -->
  <div class="space-y-1.5">
    <div v-for="s in state.slots" :key="s.key" class="text-xs">
      <div class="flex items-center gap-1.5 flex-wrap">
        <span class="text-gray-400 flex-shrink-0 min-w-[3.5rem]">
          {{ s.label }}<span v-if="s.role === 'closing'" class="text-cyan-500">（收尾）</span>
        </span>
        <span class="flex-shrink-0 px-1 rounded border font-mono"
              :class="s.got >= s.expected
                ? 'border-green-800 bg-green-900/40 text-green-300'
                : s.got > 0
                  ? 'border-yellow-800 bg-yellow-900/40 text-yellow-300'
                  : 'border-slate-700 bg-slate-800/60 text-gray-500'">
          {{ s.got }}/{{ s.expected }}
        </span>
        <template v-for="c in s.codes" :key="c.record_id ?? c.seq">
          <span data-testid="scan-code-chip"
                class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-slate-800 border border-slate-600 font-mono text-gray-200 max-w-[11rem]"
                :title="`#${c.seq} ${c.ts} ${c.code}`">
            <span class="truncate min-w-0">{{ c.code }}</span>
            <button v-if="!readonly && c.record_id" data-testid="scan-code-remove"
                    class="flex-shrink-0 text-gray-500 hover:text-red-400 leading-none disabled:opacity-40"
                    :disabled="busy" title="删除该码（扫错纠正）"
                    @click.stop="$emit('remove', c.record_id)">✕</button>
          </span>
        </template>
        <span v-if="s.got < s.expected" class="text-gray-600">缺{{ s.expected - s.got }}</span>
      </div>
    </div>
    <div v-if="!hideClear && !readonly && state.collecting" class="pt-1">
      <el-popconfirm title="清空本组全部已扫码、整组重扫？"
                     confirm-button-text="清空重扫" cancel-button-text="取消"
                     @confirm="$emit('clear')">
        <template #reference>
          <el-button size="small" type="warning" plain :loading="busy"
                     data-testid="scan-clear-btn">清空重扫</el-button>
        </template>
      </el-popconfirm>
    </div>
  </div>
</template>

<script setup>
defineProps({
  state: { type: Object, required: true },
  readonly: { type: Boolean, default: false },
  busy: { type: Boolean, default: false },
  hideClear: { type: Boolean, default: false },
});
defineEmits(['remove', 'clear']);
</script>
