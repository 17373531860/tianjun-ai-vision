<template>
  <!-- ==================== 录像异常入口按钮 + 详情面板（2026-07 拆分批次 M-1 自 index.vue 消重） ====================
       同构块原在 index.vue 重复 4 处（插件覆盖布局 / 双工位 / 四工位 / 单工位），合一后仅层级微差:
       插件覆盖布局层按钮压在列级 Toast(z-[45]) 之上, 走 elevated 抬高 z-index。
       数据流: rows/visible/loading 全走 props, 清空走 clear emit（API 调用与轮询数据写回留父级,
       遵守拆分原则 2——子组件不自起轮询、不直写共享状态）。 -->
  <button
    v-if="rows.length > 0"
    class="absolute bg-amber-600/90 hover:bg-amber-500 text-white text-xs px-2 py-1 rounded flex items-center gap-1"
    :class="elevated ? 'right-3 bottom-3 z-[50]' : 'right-2 bottom-2 z-40'"
    @click="$emit('update:visible', true)"
  >
    <el-icon><Warning /></el-icon>
    录像异常 {{ rows.length }}
  </button>
  <div
    v-if="visible"
    class="absolute inset-0 bg-black/50 flex items-center justify-center p-4"
    :class="elevated ? 'z-[55]' : 'z-50'"
  >
    <div class="w-full max-w-5xl max-h-[85vh] bg-slate-900 border border-slate-700 rounded-lg flex flex-col">
      <div class="px-4 py-3 border-b border-slate-700 flex items-center">
        <span class="text-amber-300 font-bold">录像异常详情</span>
        <span class="text-xs text-gray-400 ml-3">仅记录最近异常，用于排查</span>
        <div class="ml-auto flex gap-2">
          <el-button size="small" type="warning" plain :loading="loading" @click="$emit('clear')">
            清空列表
          </el-button>
          <el-button size="small" @click="$emit('update:visible', false)">关闭</el-button>
        </div>
      </div>
      <div class="p-3 overflow-auto">
        <table class="w-full text-xs text-left">
          <thead class="text-gray-400 border-b border-slate-700">
            <tr>
              <th class="py-1 pr-2">时间</th>
              <th class="py-1 pr-2">工位</th>
              <th class="py-1 pr-2">类型</th>
              <th class="py-1 pr-2">原因</th>
              <th class="py-1 pr-2">文件</th>
              <th class="py-1 pr-2">已写帧</th>
            </tr>
          </thead>
          <tbody class="text-gray-200">
            <tr v-for="(item, idx) in rows" :key="idx" class="border-b border-slate-800">
              <td class="py-1 pr-2 whitespace-nowrap">{{ formatTime(item.timestamp) }}</td>
              <td class="py-1 pr-2">工位{{ item.channel_id + 1 }}</td>
              <td class="py-1 pr-2">{{ item.recorder_type }}</td>
              <td class="py-1 pr-2">
                <div>{{ reasonText(item.reason) }}</div>
                <div v-if="item.error" class="text-gray-400 break-all">{{ item.error }}</div>
              </td>
              <td class="py-1 pr-2 break-all text-gray-300">{{ item.file_path || '-' }}</td>
              <td class="py-1 pr-2">{{ item.frame_count ?? '-' }}</td>
            </tr>
            <tr v-if="rows.length === 0">
              <td colspan="6" class="py-4 text-center text-gray-500">暂无录像异常</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<script setup>
import { Warning } from '@element-plus/icons-vue';

defineProps({
  // 聚合后的异常行（父级按通道汇总排序, 见 index.vue recordingFailureRows）
  rows: { type: Array, default: () => [] },
  visible: { type: Boolean, default: false },
  loading: { type: Boolean, default: false },
  // 插件覆盖布局层需压过列级 Toast(z-[45]) → 按钮 z-[50] / 面板 z-[55]
  elevated: { type: Boolean, default: false },
});
defineEmits(['update:visible', 'clear']);

const formatTime = (ts) => {
  if (!ts) return '-';
  const d = new Date(ts * 1000);
  if (Number.isNaN(d.getTime())) return '-';
  return `${d.toLocaleDateString()} ${d.toLocaleTimeString()}`;
};

const reasonText = (reason) => {
  const map = {
    open_failed: '录制器启动失败',
    open_exception: '录制器启动异常',
    write_failed: '写入失败/通道失效',
    write_exception: '写入异常',
  };
  return map[reason] || reason || '未知异常';
};
</script>
