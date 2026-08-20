<template>
  <!-- ==================== 计数器定义侧栏（2026-08 拆分批次 自 index.vue 外置；显隐 v-if 留父级） ==================== -->
  <div class="h-1/2 bg-slate-900 rounded-lg border border-slate-700 flex flex-col overflow-hidden">
    <div class="p-3 border-b border-slate-800 bg-slate-950/50 flex justify-between items-center">
      <span class="font-bold text-white">计数器定义</span>
      <el-button type="primary" size="small" link @click="addCounter">+ 新增计数器</el-button>
    </div>
    <div class="flex-1 overflow-y-auto p-4 custom-scrollbar">
      <div class="space-y-2">
        <!-- 系统默认计数器 -->
        <div class="text-xs text-gray-500 mb-1">系统默认：</div>
        <div v-for="(counter, idx) in defaultCounters" :key="'default-'+idx" class="flex items-center gap-2 bg-slate-800/50 p-2 rounded border border-slate-700">
          <span class="flex-1 text-gray-300 text-sm">{{ counter.name }}</span>
          <el-input-number v-model="counter.value" size="small" :min="0" :precision="2" class="w-24" controls-position="right" />
          <span class="text-[10px] text-gray-500 bg-slate-700 px-1 rounded">默认</span>
        </div>
        <!-- 用户自定义计数器 -->
        <div v-if="customCounters.length > 0" class="text-xs text-gray-500 mt-3 mb-1">
          自定义：
          <span class="text-[10px] text-gray-600 ml-1">（默认不在监控页显示，需勾选"显示"才会出现在右上角统计板块）</span>
        </div>
        <div v-for="(counter, idx) in customCounters" :key="'custom-'+idx" class="flex items-center gap-2 bg-slate-800 p-2 rounded">
          <el-input v-model="counter.name" size="small" placeholder="计数器名称" class="flex-1" />
          <el-input-number v-model="counter.value" size="small" :min="0" :precision="2" class="w-24" controls-position="right" />
          <!-- v3.8.x: 默认不显示, 用户主动勾选才在 Monitor 顶部统计板块出现。
               旧项目数据 counter.show_in_monitor 缺省 → undefined → 隐藏 (符合"默认不显示"语义)。 -->
          <el-tooltip content="是否在监控页右上角统计板块显示该计数器" placement="top">
            <el-checkbox v-model="counter.show_in_monitor" size="small" class="!mr-0">显示</el-checkbox>
          </el-tooltip>
          <el-button type="danger" size="small" link @click="removeCounter(idx + 3)">×</el-button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { dbg } from '@/utils/debug';

// defaultCounters / customCounters 是父级基于 project.counters_config 的切片 computed
// （EventsConfigTab 同样消费），保持父级单一来源，这里只按 props 渲染。
const props = defineProps({
  project: { type: Object, required: true },
  defaultCounters: { type: Array, default: () => [] },
  customCounters: { type: Array, default: () => [] },
});

// 计数器操作
const addCounter = () => {
  dbg('project.config', '点击「添加计数器」', `当前数量=${props.project?.counters_config?.length ?? 0}`);
  if (!props.project.counters_config) props.project.counters_config = [];
  // v3.8.x: 新增自定义计数器默认 show_in_monitor=false (Monitor 页统计板块不显示),
  // 客户在事件配置/逻辑里用计数器, 不必全部都堆在监控页上。需要时手动勾"显示"。
  props.project.counters_config.push({ name: '新计数器', value: 0, show_in_monitor: false });
};

const removeCounter = (idx) => {
  dbg('project.config', '点击「删除计数器」', `idx=${idx} name=${props.project?.counters_config?.[idx]?.name}`);
  props.project.counters_config.splice(idx, 1);
};
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
