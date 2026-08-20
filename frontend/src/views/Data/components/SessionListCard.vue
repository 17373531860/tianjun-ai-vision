<template>
  <div class="panel-card">
    <div class="panel-header">
      <el-icon class="text-emerald-400"><Clock /></el-icon>
      <span>启动记录</span>
    </div>
    <div v-loading="loading" class="space-y-2 max-h-[340px] overflow-y-auto pr-1 custom-scrollbar">
      <div v-if="sessions.length === 0" class="text-center text-gray-600 py-8 text-sm">
        {{ selectedDate ? '当日无检测记录' : '请选择日期' }}
      </div>
      <div
        v-for="session in sessions"
        :key="session.id"
        class="session-card"
        :class="{ 'session-card--active': selectedSession?.id === session.id }"
        @click="$emit('select', session)"
      >
        <div class="flex items-center justify-between">
          <div class="flex-1 min-w-0">
            <div class="text-white font-mono text-xs leading-5">
              {{ formatTime(session.start_time) }}
              <span v-if="session.end_time" class="text-gray-500"> → {{ formatTime(session.end_time) }}</span>
            </div>
            <div v-if="session.name" class="text-amber-300 font-mono text-xs mt-0.5 truncate" :title="session.name">
              标识: {{ session.name }}
            </div>
            <div class="flex items-center gap-2 mt-1 flex-wrap">
              <el-tag v-if="totalChannelCount > 1" type="" size="small" effect="plain" round class="!text-cyan-400 !border-cyan-800">
                工位{{ (session.channel_id || 0) + 1 }}
              </el-tag>
              <el-tag :type="getStatusType(session.status)" size="small" effect="dark" round>
                {{ getStatusText(session.status) }}
              </el-tag>
              <span class="text-cyan-400 font-mono text-xs">{{ session.total_cycles || 0 }}轮</span>
              <span class="text-xs">
                <span class="text-green-400">{{ session.good_cycles || 0 }}</span>
                <span class="text-gray-600">/</span>
                <span class="text-red-400">{{ session.ng_cycles || 0 }}</span>
              </span>
            </div>
          </div>
          <el-tooltip content="重命名会话标识" placement="top">
            <el-button
              type="warning"
              size="small"
              circle
              class="ml-2 flex-shrink-0"
              @click.stop="$emit('rename', session)"
            >
              <el-icon :size="12"><Edit /></el-icon>
            </el-button>
          </el-tooltip>
          <el-button
            v-if="session.video_id"
            type="primary"
            size="small"
            circle
            class="ml-1 flex-shrink-0"
            @click.stop="$emit('play', session)"
          >
            <el-icon :size="12"><VideoPlay /></el-icon>
          </el-button>
          <el-tooltip v-else content="无会话视频" placement="top">
            <el-button type="info" size="small" circle disabled class="ml-1 flex-shrink-0">
              <el-icon :size="12"><VideoPlay /></el-icon>
            </el-button>
          </el-tooltip>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
// 启动记录（session）列表卡：纯展示，选择/重命名/播放全部上抛给父视图。
import { Clock, Edit, VideoPlay } from '@element-plus/icons-vue';

defineProps({
  sessions: { type: Array, default: () => [] },
  selectedSession: { type: Object, default: null },
  selectedDate: { type: String, default: '' },
  totalChannelCount: { type: Number, default: 1 },
  loading: { type: Boolean, default: false },
});

defineEmits(['select', 'rename', 'play']);

const formatTime = (timeStr) => {
  if (!timeStr) return '';
  const parts = timeStr.split(' ');
  return parts.length > 1 ? parts[1].substring(0, 8) : timeStr;
};

const getStatusType = (status) => {
  switch (status) {
    case 'running': return 'warning';
    case 'completed': return 'success';
    case 'interrupted': return 'info';
    default: return 'info';
  }
};

const getStatusText = (status) => {
  switch (status) {
    case 'running': return '运行中';
    case 'completed': return '已完成';
    case 'interrupted': return '已中断';
    default: return status || '未知';
  }
};
</script>

<style scoped>
.panel-card {
  background: linear-gradient(145deg, rgba(15, 23, 42, 0.9), rgba(30, 41, 59, 0.6));
  border: 1px solid rgba(51, 65, 85, 0.5);
  border-radius: 12px;
  padding: 16px;
  backdrop-filter: blur(8px);
}

.panel-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.875rem;
  font-weight: 600;
  color: #e2e8f0;
  margin-bottom: 0.75rem;
}

.session-card {
  padding: 10px 12px;
  background: rgba(30, 41, 59, 0.5);
  border-radius: 8px;
  border: 1px solid rgba(51, 65, 85, 0.4);
  cursor: pointer;
  transition: all 0.2s ease;
}

.session-card:hover {
  border-color: rgba(71, 85, 105, 0.8);
  background: rgba(30, 41, 59, 0.8);
}

.session-card--active {
  border-color: rgba(6, 182, 212, 0.5) !important;
  background: rgba(6, 182, 212, 0.08) !important;
  box-shadow: 0 0 0 1px rgba(6, 182, 212, 0.15);
}

.custom-scrollbar::-webkit-scrollbar {
  width: 0.25rem;
}

.custom-scrollbar::-webkit-scrollbar-track {
  background: transparent;
}

.custom-scrollbar::-webkit-scrollbar-thumb {
  background: #334155;
  border-radius: 0.25rem;
}

.custom-scrollbar::-webkit-scrollbar-thumb:hover {
  background: #475569;
}
</style>
