<template>
  <div class="panel-card">
    <div class="panel-header flex items-center justify-between">
      <div class="flex items-center gap-2">
        <el-icon class="text-cyan-400"><Histogram /></el-icon>
        <span>称重投料逐件记录</span>
      </div>
      <div class="flex items-center gap-2">
        <span class="text-xs text-gray-500">工位</span>
        <el-select v-model="weighingChannel" size="small" style="width: 100px" @change="loadWeighingRecords">
          <el-option v-for="n in (channelCountForData)" :key="n - 1" :label="'工位' + n" :value="n - 1" />
        </el-select>
        <el-button size="small" @click="loadWeighingRecords">刷新</el-button>
        <el-button size="small" type="primary" :disabled="!weighingRecords.length" @click="exportWeighingCsv">导出CSV</el-button>
      </div>
    </div>
    <div class="grid grid-cols-4 gap-3 mb-3">
      <div class="stat-tile"><div class="text-xs text-gray-400">记录数</div><div class="text-xl font-bold text-white">{{ weighingRecords.length }}</div></div>
      <div class="stat-tile"><div class="text-xs text-gray-400">合格</div><div class="text-xl font-bold text-green-400">{{ wStat.ok }}</div></div>
      <div class="stat-tile"><div class="text-xs text-gray-400">缺料</div><div class="text-xl font-bold text-red-400">{{ wStat.shortage }}</div></div>
      <div class="stat-tile"><div class="text-xs text-gray-400">超量</div><div class="text-xl font-bold text-orange-400">{{ wStat.over }}</div></div>
    </div>
    <el-table :data="weighingRecords" size="small" height="520" empty-text="暂无称重记录">
      <el-table-column label="时间" width="160">
        <template #default="{ row }">{{ fmtTs(row.ts) }}</template>
      </el-table-column>
      <el-table-column prop="sn" label="序列号" width="140" />
      <el-table-column prop="model" label="型号" width="120" />
      <el-table-column prop="operator" label="人员" width="100" />
      <el-table-column prop="material" label="料别" width="120" />
      <el-table-column label="标准量(kg)" width="110">
        <template #default="{ row }">{{ row.standard != null ? Number(row.standard).toFixed(3) : '—' }}</template>
      </el-table-column>
      <el-table-column label="实投(kg)" width="110">
        <template #default="{ row }">{{ Number(row.net).toFixed(3) }}</template>
      </el-table-column>
      <el-table-column label="判定">
        <template #default="{ row }">
          <el-tag size="small" :type="wVerdictTag(row.verdict)">{{ wVerdictText(row.verdict) }}</el-tag>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<script setup>
// 称重投料模式逐件记录台账（v3.31）。完全自含：自己拉工位数与记录，
// 仅在父视图判定当前项目为 weighing 模式时挂载。
import { ref, computed, onMounted } from 'vue';
import { Histogram } from '@element-plus/icons-vue';
import { getWorkstations } from '@/api/detection';
import { getWeighingRecords } from '@/api/weighing';

const weighingChannel = ref(0);
const weighingRecords = ref([]);
const weighingChannelCount = ref(1);  // 系统工位数 (来自 /workstations, 非项目字段)
const channelCountForData = computed(() => Math.max(1, Math.min(16, weighingChannelCount.value || 1)));
const wStat = computed(() => {
  const s = { ok: 0, shortage: 0, over: 0, no_spec: 0 };
  weighingRecords.value.forEach(r => { if (s[r.verdict] !== undefined) s[r.verdict]++; });
  return s;
});
const loadWeighingRecords = async () => {
  try {
    const { data } = await getWeighingRecords(weighingChannel.value, 500);
    weighingRecords.value = (data?.records || []).slice().reverse();
  } catch (e) {
    weighingRecords.value = [];
  }
};
const loadWeighingChannelCount = async () => {
  try {
    const res = await getWorkstations();
    weighingChannelCount.value = res?.data?.channel_count || 1;
  } catch (e) {
    weighingChannelCount.value = 1;
  }
};
const fmtTs = (ts) => {
  if (!ts) return '—';
  const d = new Date(ts * 1000);
  const p = (x) => String(x).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
};
const wVerdictText = (v) => ({ ok: '合格', shortage: '缺料', over: '超量', no_spec: '未配标准' }[v] || v);
const wVerdictTag = (v) => ({ ok: 'success', shortage: 'danger', over: 'warning', no_spec: 'info' }[v] || 'info');
const exportWeighingCsv = () => {
  const head = ['时间', '序列号', '型号', '人员', '料别', '标准量kg', '实投kg', '判定'];
  const lines = [head.join(',')];
  weighingRecords.value.forEach(r => {
    lines.push([fmtTs(r.ts), r.sn || '', r.model || '', r.operator || '', r.material || '',
      r.standard != null ? Number(r.standard).toFixed(3) : '', Number(r.net).toFixed(3),
      wVerdictText(r.verdict)].map(x => `"${String(x).replace(/"/g, '""')}"`).join(','));
  });
  const blob = new Blob(['\ufeff' + lines.join('\n')], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `称重记录_工位${weighingChannel.value + 1}_${Date.now()}.csv`;
  a.click();
  URL.revokeObjectURL(url);
};

onMounted(() => {
  loadWeighingChannelCount();
  loadWeighingRecords();
});
</script>

<style scoped>
.panel-card {
  background: linear-gradient(145deg, rgba(15, 23, 42, 0.9), rgba(30, 41, 59, 0.6));
  border: 1px solid rgba(51, 65, 85, 0.5);
  border-radius: 12px;
  padding: 16px;
  backdrop-filter: blur(8px);
}

.stat-tile {
  background: rgba(15, 23, 42, 0.6);
  border: 1px solid rgba(51, 65, 85, 0.5);
  border-radius: 8px;
  padding: 10px 12px;
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

/* 暗色表格（与 Data 主视图一致） */
:deep(.el-table) {
  --el-table-bg-color: transparent;
  --el-table-tr-bg-color: transparent;
  --el-table-header-bg-color: rgba(30, 41, 59, 0.6);
  --el-table-row-hover-bg-color: rgba(51, 65, 85, 0.4);
  --el-table-border-color: rgba(51, 65, 85, 0.4);
  --el-table-text-color: #cbd5e1;
  --el-table-header-text-color: #64748b;
  background-color: transparent;
  font-size: 0.75rem;
}

:deep(.el-table th.el-table__cell) {
  background-color: rgba(30, 41, 59, 0.6);
  font-weight: 500;
}

:deep(.el-table td.el-table__cell) {
  border-bottom: 1px solid rgba(51, 65, 85, 0.3);
}

:deep(.el-table__body tr:hover > td.el-table__cell) {
  background-color: rgba(51, 65, 85, 0.3) !important;
}

:deep(.el-table__empty-text) {
  color: #475569;
}

:deep(.el-button--default) {
  --el-button-bg-color: rgba(30, 41, 59, 0.8);
  --el-button-border-color: rgba(51, 65, 85, 0.5);
  --el-button-text-color: #e2e8f0;
  --el-button-hover-bg-color: rgba(51, 65, 85, 0.6);
  --el-button-hover-border-color: rgba(71, 85, 105, 0.7);
  --el-button-hover-text-color: #f1f5f9;
}
</style>
