<template>
  <!-- 新建项目对话框（2026-07 拆分批次 P-2 自 index.vue 外置） -->
  <el-dialog :model-value="visible" title="新建项目" width="500px" destroy-on-close
    @update:model-value="$emit('update:visible', $event)">
    <el-form label-position="top">
      <el-form-item label="项目名称" required>
        <el-input v-model="form.name" placeholder="例如: 手机壳外观检测" />
      </el-form-item>
      <el-form-item label="任务类型">
        <el-select v-model="form.task_type" class="w-full">
          <el-option label="目标检测 (Object Detection)" value="detection" />
          <el-option label="图像分割 (Instance Segmentation)" value="segmentation" />
        </el-select>
      </el-form-item>
      <el-form-item label="逻辑模式">
        <el-select v-model="form.logic_mode" class="w-full">
          <el-option label="顺序模式" value="sequential" />
          <el-option label="检测模式" value="detection" />
          <el-option label="自定义模式" value="custom" />
          <el-option label="跟踪模式" value="tracking" />
          <el-option label="逐件模式" value="per_item" />
          <el-option label="称重投料模式" value="weighing" />
          <el-option label="区域事件模式" value="region_events" />
          <el-option label="OCR 读字模式" value="ocr" />
          <el-option label="异常检测模式" value="anomaly" />
        </el-select>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="$emit('update:visible', false)">取消</el-button>
      <el-button type="primary" :loading="creating" :disabled="!form.name"
        @click="$emit('create')">创建</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
// 数据流约定: 表单对象由父级持有并传入 (打开前重置也在父级), 点「创建」只发事件,
// 创建请求 / 列表刷新 / 关窗仍由父级 handleCreateProject 统一负责。
defineProps({
  visible: { type: Boolean, required: true },
  form: { type: Object, required: true },
  creating: { type: Boolean, default: false },
});
defineEmits(['update:visible', 'create']);
</script>
