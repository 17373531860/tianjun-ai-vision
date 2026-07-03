<template>
  <!-- 推理格式选择对话框（主/副模型共用, 2026-07 拆分批次 P-2 自 index.vue 外置） -->
  <el-dialog :model-value="visible"
    :title="extraIdx !== null
      ? `选择推理格式 [副模型: ${project?.extra_models?.[extraIdx]?.name || ''}]`
      : '选择推理格式 [主模型]'"
    width="640px" :close-on-click-modal="!converting"
    @update:model-value="$emit('update:visible', $event)"
    @close="$emit('cancel')">
    <div v-if="converting" class="text-center py-12">
      <el-icon class="is-loading text-4xl text-blue-400 mb-4"><Loading /></el-icon>
      <p class="text-white text-lg mb-2">正在转换为 {{ getFormatDisplayName(converting) }}...</p>
      <p class="text-gray-400 text-sm">预计需要 2-10 分钟，请勿关闭此窗口</p>
      <el-button class="mt-6" @click="$emit('cancel')">取消并使用原始格式</el-button>
    </div>
    <div v-else class="space-y-2 max-h-[28rem] overflow-y-auto pr-1">
      <div class="flex items-center justify-between mb-3">
        <p v-if="gpuName" class="text-xs text-gray-400">当前显卡: {{ gpuName }}</p>
        <el-button size="small" text type="info" @click="$emit('diagnose')">
          <el-icon class="mr-1"><Warning /></el-icon>环境诊断
        </el-button>
      </div>
      <div v-for="fmt in formats" :key="fmt.key"
        @click="$emit('select', fmt)"
        :class="[
          'p-3 rounded border transition-all',
          fmt.available
            ? 'cursor-pointer hover:border-blue-500 bg-slate-800 border-slate-700'
            : 'cursor-not-allowed opacity-50 bg-slate-900 border-slate-800',
          currentFormat === fmt.key
            ? 'border-blue-500 bg-slate-700'
            : ''
        ]">
        <div class="flex justify-between items-start">
          <div>
            <span class="text-white font-bold">{{ fmt.name }}</span>
            <span class="text-gray-500 text-xs ml-2">({{ fmt.extension }})</span>
          </div>
          <div class="flex gap-1.5">
            <el-tag v-if="fmt.tag" size="small" :type="fmt.tag === '最快' ? 'success' : fmt.tag === '实验性' ? 'warning' : 'info'">{{ fmt.tag }}</el-tag>
            <el-tag v-if="recommended === fmt.key" size="small" type="success">推荐</el-tag>
            <el-tag v-if="currentFormat === fmt.key" size="small">当前</el-tag>
          </div>
        </div>
        <p class="text-xs text-gray-400 mt-1">{{ fmt.description }}</p>
        <p v-if="!fmt.available" class="text-xs text-red-400 mt-1">{{ fmt.unavailable_reason }}</p>
        <p v-if="fmt.key.startsWith('tensorrt') && fmt.available" class="text-xs text-amber-400 mt-1">此格式仅在当前显卡上有效，更换显卡后需重新转换</p>
      </div>
    </div>
  </el-dialog>
</template>

<script setup>
// 数据流约定: 格式清单/推荐/显卡名由父级拉取传入; 点格式只发事件,
// 转换请求 + 轮询 + 回写 model_format 的业务逻辑仍在父级 selectFormat。
// converting 非空 = 转换进行中(父级状态), 此时对话框锁定为进度视图。
import { computed } from 'vue';
import { Loading, Warning } from '@element-plus/icons-vue';
import { getFormatDisplayName } from './modelFormats';

const props = defineProps({
  visible: { type: Boolean, required: true },
  project: { type: Object, default: null },
  extraIdx: { type: Number, default: null },
  converting: { type: String, default: null },
  formats: { type: Array, default: () => [] },
  gpuName: { type: String, default: '' },
  recommended: { type: String, default: 'pytorch_fp32' },
});
defineEmits(['update:visible', 'select', 'cancel', 'diagnose']);

// 「当前」格式: 副模型上下文取 slot.model_format, 主模型取 project.model_format
const currentFormat = computed(() => (
  props.extraIdx !== null
    ? (props.project?.extra_models?.[props.extraIdx]?.model_format || 'pytorch_fp32')
    : (props.project?.model_format || 'pytorch_fp32')
));
</script>
