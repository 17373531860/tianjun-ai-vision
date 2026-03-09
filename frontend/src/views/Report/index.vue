<template>
  <div class="space-y-4 flex flex-col h-full">
    <!-- Query bar -->
    <div class="bg-ind-panel p-4 rounded-lg border border-gray-800 flex flex-wrap gap-4 items-center">
      <el-date-picker
        v-model="query.dateRange"
        type="daterange"
        range-separator="至"
        start-placeholder="开始日期"
        end-placeholder="结束日期"
        size="default"
        format="YYYY-MM-DD"
        value-format="YYYY-MM-DD"
        class="!bg-ind-bg"
      />
      <el-select v-model="query.projectId" placeholder="选择项目" clearable size="default">
        <el-option v-for="p in projectList" :key="p.id" :label="p.name" :value="p.id" />
      </el-select>
      <el-select v-model="query.shift" placeholder="班次" clearable size="default" style="width: 130px">
        <el-option label="全天" value="" />
        <el-option label="白班 (08:00-20:00)" value="day" />
        <el-option label="晚班 (20:00-08:00)" value="night" />
        <el-option label="自定义时段" value="custom" />
      </el-select>
      <template v-if="query.shift === 'custom'">
        <el-time-picker v-model="query.startHour" placeholder="开始时间" format="HH:mm" value-format="HH:mm" size="default" style="width: 120px" />
        <span class="text-gray-400">-</span>
        <el-time-picker v-model="query.endHour" placeholder="结束时间" format="HH:mm" value-format="HH:mm" size="default" style="width: 120px" />
      </template>
      <el-button type="primary" @click="handleSearch" :loading="loading">查询</el-button>
      <el-button type="success" plain @click="handleExportCsv" :loading="exporting">导出 CSV</el-button>
    </div>

    <!-- Summary cards -->
    <div class="grid grid-cols-5 gap-4">
      <div class="bg-ind-panel p-4 rounded border border-gray-800">
        <p class="text-gray-400 text-xs">检测总数</p>
        <p class="text-2xl font-mono mt-1 text-white">{{ summary.total_count.toLocaleString() }}</p>
      </div>
      <div class="bg-ind-panel p-4 rounded border border-gray-800">
        <p class="text-gray-400 text-xs">良品数 (OK)</p>
        <p class="text-2xl font-mono mt-1 text-status-ok">{{ summary.good_count.toLocaleString() }}</p>
      </div>
      <div class="bg-ind-panel p-4 rounded border border-gray-800">
        <p class="text-gray-400 text-xs">不良数 (NG)</p>
        <p class="text-2xl font-mono mt-1 text-status-ng">{{ summary.bad_count.toLocaleString() }}</p>
      </div>
      <div class="bg-ind-panel p-4 rounded border border-gray-800">
        <p class="text-gray-400 text-xs">综合良率</p>
        <p class="text-2xl font-mono mt-1 text-tech-blue">{{ summary.yield_rate.toFixed(2) }}%</p>
      </div>
      <div class="bg-ind-panel p-4 rounded border border-gray-800">
        <p class="text-gray-400 text-xs">平均耗时</p>
        <p class="text-2xl font-mono mt-1 text-yellow-400">{{ summary.avg_duration.toFixed(1) }}s</p>
      </div>
    </div>

    <!-- Trend chart -->
    <div class="bg-ind-panel p-4 rounded-lg border border-gray-800 h-72">
      <div ref="trendChartRef" class="w-full h-full"></div>
    </div>

    <!-- Daily breakdown table -->
    <div class="flex-1 bg-ind-panel rounded-lg border border-gray-800 overflow-hidden flex flex-col">
      <el-table :data="dailyStats" height="100%" size="small" style="width: 100%" v-loading="loadingDaily"
        :header-cell-style="{ background: '#1e293b', color: '#94a3b8' }"
        :row-style="{ background: 'transparent' }">
        <el-table-column prop="date" label="日期" width="140" />
        <el-table-column prop="total_count" label="总数" width="100" />
        <el-table-column prop="good_count" label="良品" width="100">
          <template #default="{ row }">
            <span class="text-status-ok">{{ row.good_count }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="bad_count" label="不良" width="100">
          <template #default="{ row }">
            <span class="text-status-ng">{{ row.bad_count }}</span>
          </template>
        </el-table-column>
        <el-table-column label="良率" width="120">
          <template #default="{ row }">
            <span :class="row.yield_rate >= 95 ? 'text-status-ok' : row.yield_rate >= 80 ? 'text-yellow-400' : 'text-status-ng'">
              {{ row.yield_rate.toFixed(2) }}%
            </span>
          </template>
        </el-table-column>
        <el-table-column label="良率条">
          <template #default="{ row }">
            <div class="w-full bg-gray-700 rounded h-3 overflow-hidden">
              <div class="h-full rounded transition-all"
                :class="row.yield_rate >= 95 ? 'bg-green-500' : row.yield_rate >= 80 ? 'bg-yellow-500' : 'bg-red-500'"
                :style="{ width: row.yield_rate + '%' }"
              ></div>
            </div>
          </template>
        </el-table-column>
      </el-table>
      
      <div v-if="dailyStats.length === 0 && !loadingDaily" class="flex-1 flex items-center justify-center text-gray-500">
        暂无数据，请选择日期范围后点击查询
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, reactive, nextTick, onUnmounted } from 'vue';
import * as echarts from 'echarts';
import { ElMessage } from 'element-plus';
import { getSummary, getDailyStats, exportCsvReport } from '@/api/report';
import { getProjects } from '@/api/project';

const query = reactive({
  dateRange: [],
  projectId: '',
  shift: '',
  startHour: '08:00',
  endHour: '20:00'
});
const trendChartRef = ref(null);
let chartInstance = null;

const projectList = ref([]);
const summary = reactive({
  total_count: 0,
  good_count: 0,
  bad_count: 0,
  yield_rate: 0,
  avg_duration: 0
});
const dailyStats = ref([]);

const loading = ref(false);
const loadingDaily = ref(false);
const exporting = ref(false);

const getShiftHours = () => {
  if (query.shift === 'day') return { start: '08:00', end: '20:00' };
  if (query.shift === 'night') return { start: '20:00', end: '08:00' };
  if (query.shift === 'custom') return { start: query.startHour, end: query.endHour };
  return null;
};

const loadProjects = async () => {
  try {
    const res = await getProjects();
    projectList.value = res.data.items || [];
  } catch (err) {
    console.error('Failed to load projects:', err);
  }
};

const buildQueryParams = () => {
  const params = {};
  if (query.dateRange && query.dateRange.length === 2) {
    params.start_date = query.dateRange[0];
    params.end_date = query.dateRange[1];
  }
  if (query.projectId) {
    params.project_id = query.projectId;
  }
  const shift = getShiftHours();
  if (shift) {
    params.start_hour = shift.start;
    params.end_hour = shift.end;
  }
  return params;
};

const loadSummary = async () => {
  try {
    const params = buildQueryParams();
    const res = await getSummary(params);
    Object.assign(summary, res.data);
  } catch (err) {
    console.error('Failed to load summary:', err);
  }
};

const loadDaily = async () => {
  loadingDaily.value = true;
  try {
    const params = buildQueryParams();
    const res = await getDailyStats(params);
    dailyStats.value = res.data || [];
    updateChart();
  } catch (err) {
    console.error('Failed to load daily stats:', err);
  } finally {
    loadingDaily.value = false;
  }
};

const initChart = () => {
  if (!trendChartRef.value) return;
  chartInstance = echarts.init(trendChartRef.value);
  updateChart();
};

const updateChart = () => {
  if (!chartInstance) return;
  const dates = dailyStats.value.map(d => d.date);
  const goodCounts = dailyStats.value.map(d => d.good_count);
  const badCounts = dailyStats.value.map(d => d.bad_count);
  const yieldRates = dailyStats.value.map(d => d.yield_rate);

  chartInstance.setOption({
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis' },
    legend: { data: ['良品', '不良', '良率'], textStyle: { color: '#94a3b8' } },
    grid: { left: '3%', right: '4%', bottom: '3%', containLabel: true },
    xAxis: { 
      type: 'category', 
      data: dates, 
      axisLabel: { color: '#94a3b8' } 
    },
    yAxis: [
      { 
        type: 'value',
        name: '数量',
        axisLabel: { color: '#94a3b8' }, 
        splitLine: { lineStyle: { color: '#1e293b' } } 
      },
      {
        type: 'value',
        name: '良率',
        min: 0,
        max: 100,
        axisLabel: { color: '#94a3b8', formatter: '{value}%' },
        splitLine: { show: false }
      }
    ],
    series: [
      { 
        name: '良品', type: 'bar', stack: 'total', data: goodCounts,
        itemStyle: { color: '#22c55e' }
      },
      { 
        name: '不良', type: 'bar', stack: 'total', data: badCounts,
        itemStyle: { color: '#ef4444' }
      },
      { 
        name: '良率', type: 'line', yAxisIndex: 1, smooth: true, data: yieldRates,
        itemStyle: { color: '#3b82f6' },
        areaStyle: { color: 'rgba(59, 130, 246, 0.1)' }
      }
    ]
  });
};

const handleSearch = async () => {
  if (!query.dateRange || query.dateRange.length < 2) {
    ElMessage.warning('请选择日期范围');
    return;
  }
  loading.value = true;
  try {
    await Promise.all([loadSummary(), loadDaily()]);
  } finally {
    loading.value = false;
  }
};

const handleExportCsv = async () => {
  exporting.value = true;
  try {
    const params = buildQueryParams();
    const res = await exportCsvReport(params);
    const blob = new Blob([res.data], { type: 'text/csv' });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `report_${new Date().toISOString().split('T')[0]}.csv`;
    a.click();
    window.URL.revokeObjectURL(url);
    ElMessage.success('CSV 报表已导出');
  } catch (err) {
    ElMessage.error('导出失败: ' + err.message);
  } finally {
    exporting.value = false;
  }
};

const handleResize = () => chartInstance?.resize();

onMounted(async () => {
  await loadProjects();
  nextTick(() => initChart());
  window.addEventListener('resize', handleResize);
});

onUnmounted(() => {
  window.removeEventListener('resize', handleResize);
  if (chartInstance) {
    chartInstance.dispose();
    chartInstance = null;
  }
});
</script>
