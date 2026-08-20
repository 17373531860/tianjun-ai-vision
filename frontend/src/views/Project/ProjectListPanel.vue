<template>
  <!-- ==================== 项目列表卡（2026-08 拆分批次 自 index.vue 外置） ==================== -->
  <div class="bg-slate-900 rounded-lg border border-slate-700 flex flex-col overflow-hidden transition-all duration-300"
       :class="activeProject && (activeTab === 'logic' || activeTab === 'events') ? 'h-1/2' : 'h-full'">
    <div class="p-3 border-b border-slate-800 bg-slate-950/50">
      <el-input v-model="searchQuery" placeholder="搜索项目..." prefix-icon="Search" size="small" />
    </div>
    <div v-loading="loading" class="flex-1 overflow-y-auto p-2 space-y-2 custom-scrollbar">
      <div v-if="filteredProjects.length === 0" class="text-center text-gray-500 py-8">
        暂无项目
      </div>
      <div 
        v-for="item in filteredProjects" 
        :key="item.id"
        @click="selectProject(item)"
        class="p-4 rounded-lg border cursor-pointer transition-all group hover:border-cyan-500/50"
        :class="activeProject?.id === item.id ? 'border-cyan-500 bg-cyan-900/20' : 'border-slate-800 bg-slate-900 hover:bg-slate-800'"
      >
        <div class="flex justify-between items-start mb-2">
          <span class="font-bold text-gray-200 group-hover:text-white">{{ item.name }}</span>
          <el-tag size="small" :type="item.is_active ? 'success' : 'info'" effect="dark">
            {{ item.is_active ? '运行中' : (item.task_type || 'detection').toUpperCase() }}
          </el-tag>
        </div>
        <div class="text-xs text-gray-500 flex justify-between">
          <span>模型: {{ item.model_name || '未配置' }}</span>
          <span>{{ formatDate(item.updated_at) }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue';

const props = defineProps({
  projects: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  activeProject: { type: Object, default: null },
  activeTab: { type: String, default: 'basic' },
});

const emit = defineEmits(['select']);

const searchQuery = ref('');

const filteredProjects = computed(() => {
  return props.projects.filter(p => p.name.toLowerCase().includes(searchQuery.value.toLowerCase()));
});

const formatDate = (dateStr) => {
  if (!dateStr) return '';
  const date = new Date(dateStr);
  return date.toLocaleDateString();
};

// 项目详情加载/初始化在父级（selectProject 同时被复制项目流程复用），这里只上抛选择意图
const selectProject = (item) => emit('select', item);
</script>

<style scoped>
.custom-scrollbar::-webkit-scrollbar {
  width: 6px;
  height: 6px;
}
.custom-scrollbar::-webkit-scrollbar-track {
  background: #1e293b;
}
.custom-scrollbar::-webkit-scrollbar-thumb {
  background: #475569;
  border-radius: 3px;
}
.custom-scrollbar::-webkit-scrollbar-thumb:hover {
  background: #64748b;
}
</style>
