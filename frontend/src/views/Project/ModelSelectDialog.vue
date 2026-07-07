<template>
  <!-- 模型选择对话框（主/副模型共用, 2026-07 拆分批次 P-2 自 index.vue 外置） -->
  <el-dialog :model-value="visible"
    :title="extraIdx >= 0
      ? `选择副模型 [${project?.extra_models?.[extraIdx]?.name || ''}]`
      : '选择模型'"
    width="600px"
    @update:model-value="$emit('update:visible', $event)"
    @close="$emit('close')">
    <div v-loading="loading" class="space-y-2 max-h-96 overflow-y-auto">
      <div v-for="model in models" :key="model.id"
        @click="$emit('select', model)"
        class="p-3 bg-slate-800 rounded cursor-pointer hover:bg-slate-700 flex justify-between items-center">
        <div>
          <p class="font-bold">{{ model.name }}<span v-if="model.version" class="text-gray-400 font-normal ml-2">v{{ model.version }}</span></p>
          <p class="text-xs text-gray-400">{{ model.framework }} - {{ (model.file_size / 1024 / 1024).toFixed(2) }} MB - {{ getLabelsCount(model.labels) }} 个类别</p>
        </div>
        <el-tag v-if="extraIdx >= 0
          ? project?.extra_models?.[extraIdx]?.model_id === model.id
          : project?.default_model_id === model.id" type="success">当前</el-tag>
      </div>
      <div v-if="models.length === 0" class="text-center text-gray-500 py-8">
        暂无可用模型，请先上传模型
      </div>
    </div>
  </el-dialog>
</template>

<script setup>
// 数据流约定: 模型列表由父级拉取传入; 点条目只发事件, 选型后写回项目对象
// (default_model_id / extra_models[idx]) 的业务逻辑仍在父级 selectModel。
defineProps({
  visible: { type: Boolean, required: true },
  project: { type: Object, default: null },
  extraIdx: { type: Number, default: -1 },
  models: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
});
defineEmits(['update:visible', 'select', 'close']);

const getLabelsCount = (labels) => {
  if (!labels) return 0;
  try {
    const parsed = typeof labels === 'string' ? JSON.parse(labels) : labels;
    return Array.isArray(parsed) ? parsed.length : 0;
  } catch {
    return 0;
  }
};
</script>
