<template>
  <div class="space-y-4 flex flex-col h-full">
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
      <el-select v-model="query.projectId" placeholder="选择项目" clearable>
        <el-option v-for="p in projectList" :key="p.id" :label="p.name" :value="p.id" />
      </el-select>
      <el-button type="primary" @click="handleSearch" :loading="loading">查询</el-button>
      <el-button type="success" plain @click="handleExportPdf" :loading="exporting">导出 PDF</el-button>
      <el-button type="info" plain @click="handleExportCsv" :loading="exporting">导出 CSV</el-button>
    </div>

    <div class="grid grid-cols-4 gap-4">
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
    </div>

    <div class="bg-ind-panel p-4 rounded-lg border border-gray-800 h-72">
      <div ref="trendChartRef" class="w-full h-full"></div>
    </div>

    <div class="flex-1 bg-ind-panel rounded-lg border border-gray-800 overflow-hidden flex flex-col">
      <el-table :data="recordList" height="100%" size="small" style="width: 100%" v-loading="loadingRecords">
        <el-table-column prop="timestamp" label="检测时间" width="180" />
        <el-table-column prop="projectName" label="项目" />
        <el-table-column label="结果" width="100">
          <template #default="{ row }">
            <span :class="row.result === 'OK' ? 'text-status-ok' : 'text-status-ng'">● {{ row.result }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="confidence" label="置信度" />
        <el-table-column prop="stepName" label="步骤" />
        <el-table-column prop="duration" label="耗时(ms)" width="100" />
      </el-table>
      
      <div v-if="recordList.length === 0 && !loadingRecords" class="flex-1 flex items-center justify-center text-gray-500">
        暂无数据
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, reactive, nextTick } from 'vue';
import * as echarts from 'echarts';
import { ElMessage } from 'element-plus';
import { getSummary, getRecords, getTrend, exportPdfReport, exportCsvReport } from '@/api/report';
import { getProjects } from '@/api/project';

const query = reactive({ dateRange: [], projectId: '' });
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
const recordList = ref([]);
const trendData = ref({ dates: [], yield_rates: [] });

const loading = ref(false);
const loadingRecords = ref(false);
const exporting = ref(false);

// 加载项目列表
const loadProjects = async () => {
  try {
    const res = await getProjects();
    projectList.value = res.data.items || [];
  } catch (err) {
    console.error('加载项目失败:', err);
  }
};

// 加载统计摘要
const loadSummary = async () => {
  try {
    const params = buildQueryParams();
    const res = await getSummary(params);
    Object.assign(summary, res.data);
  } catch (err) {
    console.error('加载统计失败:', err);
  }
};

// 加载检测记录
const loadRecords = async () => {
  loadingRecords.value = true;
  try {
    const params = buildQueryParams();
    const res = await getRecords(params);
    recordList.value = res.data || [];
  } catch (err) {
    console.error('加载记录失败:', err);
  } finally {
    loadingRecords.value = false;
  }
};

// 加载趋势数据
const loadTrend = async () => {
  try {
    const params = { days: 7 };
    if (query.projectId) params.project_id = query.projectId;
    const res = await getTrend(params);
    trendData.value = res.data;
    updateChart();
  } catch (err) {
    console.error('加载趋势失败:', err);
  }
};

// 构建查询参数
const buildQueryParams = () => {
  const params = {};
  if (query.dateRange && query.dateRange.length === 2) {
    params.start_date = query.dateRange[0];
    params.end_date = query.dateRange[1];
  }
  if (query.projectId) {
    params.project_id = query.projectId;
  }
  return params;
};

// 初始化图表
const initChart = () => {
  if (!trendChartRef.value) return;
  chartInstance = echarts.init(trendChartRef.value);
  updateChart();
  window.addEventListener('resize', () => chartInstance?.resize());
};

// 更新图表
const updateChart = () => {
  if (!chartInstance) return;
  chartInstance.setOption({
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis' },
    legend: { data: ['良率'], textStyle: { color: '#94a3b8' } },
    grid: { left: '3%', right: '4%', bottom: '3%', containLabel: true },
    xAxis: { 
      type: 'category', 
      data: trendData.value.dates || [], 
      axisLabel: { color: '#94a3b8' } 
    },
    yAxis: { 
      type: 'value', 
      min: 0,
      max: 100,
      axisLabel: { color: '#94a3b8', formatter: '{value}%' }, 
      splitLine: { lineStyle: { color: '#1e293b' } } 
    },
    series: [
      { 
        name: '良率', 
        type: 'line', 
        smooth: true, 
        data: trendData.value.yield_rates || [], 
        itemStyle: { color: '#3b82f6' },
        areaStyle: { color: 'rgba(59, 130, 246, 0.1)' }
      }
    ]
  });
};

// 搜索
const handleSearch = async () => {
  loading.value = true;
  try {
    await Promise.all([loadSummary(), loadRecords(), loadTrend()]);
  } finally {
    loading.value = false;
  }
};

// 导出 PDF
const handleExportPdf = async () => {
  exporting.value = true;
  try {
    const params = buildQueryParams();
    const res = await exportPdfReport(params);
    
    // 下载文件
    const blob = new Blob([res.data], { type: 'application/pdf' });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `report_${new Date().toISOString().split('T')[0]}.pdf`;
    a.click();
    window.URL.revokeObjectURL(url);
    
    ElMessage.success('PDF 报表已导出');
  } catch (err) {
    ElMessage.error('导出失败: ' + err.message);
  } finally {
    exporting.value = false;
  }
};

// 导出 CSV
const handleExportCsv = async () => {
  exporting.value = true;
  try {
    const params = buildQueryParams();
    const res = await exportCsvReport(params);
    
    // 下载文件
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

onMounted(async () => {
  await loadProjects();
  nextTick(() => {
    initChart();
  });
  // 初始加载数据
  handleSearch();
});
</script>
