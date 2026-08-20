<template>
  <TjSlot name="data.layout.body">
  <div class="data-page p-5 h-full overflow-y-auto">
    <!-- 页面头部 -->
    <div class="flex justify-between items-center mb-5">
      <div class="flex items-center gap-3">
        <div class="w-1 h-7 rounded-full bg-gradient-to-b from-cyan-400 to-blue-500"></div>
        <h2 class="text-xl font-bold text-white tracking-wide">数据中心</h2>
      </div>
      <div class="flex items-center gap-3 bg-slate-800/60 px-4 py-2 rounded-lg border border-slate-700/50">
        <span class="text-gray-500 text-xs">当前项目</span>
        <span class="text-cyan-400 font-semibold text-sm">{{ currentProjectName || '未选择' }}</span>
        <el-button v-if="!projectStore.currentProjectId" type="primary" size="small" round @click="goToProjectSelect">
          选择项目
        </el-button>
      </div>
    </div>

    <!-- 未选择项目提示 -->
    <div v-if="!projectStore.currentProjectId" class="flex flex-col items-center justify-center py-24">
      <div class="w-20 h-20 rounded-2xl bg-slate-800 flex items-center justify-center mb-5 border border-slate-700">
        <el-icon :size="36" class="text-gray-600"><Folder /></el-icon>
      </div>
      <p class="text-gray-400 text-base mb-5">请先选择一个项目以查看数据</p>
      <el-button type="primary" round @click="goToProjectSelect">前往项目管理</el-button>
    </div>

    <!-- ========== 称重投料模式：逐件逐料记录台账 ========== -->
    <div v-else-if="isWeighingProject" class="space-y-4">
      <WeighingLedgerCard />
    </div>

    <div v-else class="grid grid-cols-1 lg:grid-cols-12 gap-5">
      <!-- ========== 左侧边栏：查询 + 会话列表 ========== -->
      <div class="lg:col-span-3 space-y-4">
        <!-- 日期与时间段查询 -->
        <div class="panel-card">
          <div class="panel-header">
            <el-icon class="text-cyan-400"><Calendar /></el-icon>
            <span>数据查询</span>
          </div>
          <div class="space-y-3">
            <!-- v3.4.3: 工件码全局搜索 (跨日期/会话, 支持模糊) -->
            <div>
              <div class="text-xs text-gray-500 mb-1.5 flex items-center justify-between">
                <span>工件码搜索</span>
                <el-button
                  v-if="searchActive"
                  type="info"
                  size="small"
                  link
                  @click="clearSerialSearch"
                >清除</el-button>
              </div>
              <el-input
                v-model="serialSearchInput"
                size="small"
                placeholder="输入工件码 (支持模糊)"
                clearable
                @keyup.enter="runSerialSearch"
                @clear="clearSerialSearch"
              >
                <template #append>
                  <el-button :loading="loadingSerialSearch" @click="runSerialSearch">
                    <el-icon><Search /></el-icon>
                  </el-button>
                </template>
              </el-input>
              <div v-if="searchActive" class="text-[11px] text-cyan-400 mt-1 font-mono">
                搜索中: {{ serialSearchActive }} · {{ cycleTotal }} 条记录
              </div>
            </div>
            <el-date-picker
              v-model="selectedDate"
              type="date"
              placeholder="选择日期"
              format="YYYY-MM-DD"
              value-format="YYYY-MM-DD"
              class="w-full"
              :disabled-date="disabledDate"
              :disabled="searchActive"
              @change="handleDateChange"
            />
            <div>
              <div class="text-xs text-gray-500 mb-1.5">时间段</div>
              <el-select v-model="shiftType" size="small" class="w-full" @change="handleShiftChange">
                <el-option label="全天" value="all" />
                <!-- v3.35.1: 项目配了自定义班次列表 → 按列表出选项; 否则保持旧白/晚两班 -->
                <template v-if="customShifts">
                  <el-option v-for="(s, i) in customShifts" :key="s.name + s.start"
                    :label="`${s.name} (${s.start}-${customShiftEnd(i)})`" :value="s.name" />
                </template>
                <template v-else>
                  <el-option label="白班 (08:00-20:00)" value="day" />
                  <el-option label="晚班 (20:00-08:00)" value="night" />
                </template>
                <el-option label="自定义" value="custom" />
              </el-select>
              <div v-if="shiftType === 'custom'" class="flex items-center gap-2 mt-2">
                <el-time-picker v-model="customStartHour" size="small" placeholder="开始" format="HH:mm" value-format="HH:mm" class="flex-1" @change="handleShiftChange" />
                <span class="text-gray-600 text-xs">至</span>
                <el-time-picker v-model="customEndHour" size="small" placeholder="结束" format="HH:mm" value-format="HH:mm" class="flex-1" @change="handleShiftChange" />
              </div>
            </div>
            <!-- Workstation / Channel filter -->
            <div v-if="totalChannelCount > 1">
              <div class="text-xs text-gray-500 mb-1.5">工位筛选</div>
              <el-select v-model="channelFilter" size="small" class="w-full" clearable placeholder="全部工位" @change="handleChannelFilterChange">
                <el-option label="全部工位" :value="null" />
                <el-option v-for="ch in totalChannelCount" :key="ch - 1" :label="`工位 ${ch}`" :value="ch - 1" />
              </el-select>
            </div>
            <!-- MES: 工单筛选 -->
            <div>
              <div class="text-xs text-gray-500 mb-1.5">MES 工单</div>
              <el-select v-model="mesOrderFilter" size="small" class="w-full" clearable placeholder="全部工单" @change="loadSessions">
                <el-option v-for="o in mesOrderOptions" :key="o.id" :label="`${o.order_no} - ${o.product_name}`" :value="o.id" />
              </el-select>
            </div>
            <div v-if="availableDates.length > 0" class="flex items-center justify-between text-xs pt-1 border-t border-slate-800">
              <span class="text-gray-500">有数据的日期</span>
              <span class="text-cyan-400 font-mono">{{ availableDates.length }} 天</span>
            </div>
          </div>
        </div>

        <!-- 会话列表 -->
        <SessionListCard
          :sessions="sessions"
          :selected-session="selectedSession"
          :selected-date="selectedDate"
          :total-channel-count="totalChannelCount"
          :loading="loadingSessions"
          @select="selectSession"
          @rename="openRenameSessionDialog"
          @play="playSessionVideo"
        />
      </div>

      <!-- ========== 右侧主区域 ========== -->
      <div class="lg:col-span-9 space-y-5">
        <!-- 统计概览卡片 -->
        <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
          <div class="stat-card stat-card--cyan">
            <div class="stat-card__label">总周期数</div>
            <div class="stat-card__value text-cyan-400">{{ overviewData.total_cycles }}</div>
          </div>
          <div class="stat-card stat-card--blue">
            <div class="stat-card__label">平均周期时间</div>
            <div class="stat-card__value text-blue-400">{{ formatDuration(overviewData.avg_cycle_time) }}</div>
          </div>
          <div class="stat-card" :class="overviewData.yield_rate >= 95 ? 'stat-card--green' : 'stat-card--yellow'">
            <div class="stat-card__label">良率</div>
            <div class="stat-card__value" :class="overviewData.yield_rate >= 95 ? 'text-emerald-400' : 'text-amber-400'">
              {{ overviewData.yield_rate.toFixed(1) }}%
            </div>
          </div>
          <div class="stat-card stat-card--mixed">
            <div class="stat-card__label">合格 / 不良</div>
            <div class="stat-card__value">
              <span class="text-emerald-400">{{ overviewData.good_cycles }}</span>
              <span class="text-gray-600 mx-1">/</span>
              <span class="text-red-400">{{ overviewData.ng_cycles }}</span>
            </div>
          </div>
        </div>

        <!-- 计数器快照 -->
        <div v-if="Object.keys(historyCounters).length > 0" class="panel-card">
          <div class="panel-header">
            <el-icon class="text-violet-400"><DataLine /></el-icon>
            <span>计数器快照</span>
          </div>
          <div class="grid grid-cols-2 md:grid-cols-4 gap-2">
            <div 
              v-for="(value, name) in historyCounters" 
              :key="name"
              class="bg-slate-800/60 px-3 py-2 rounded-lg text-center"
            >
              <div class="text-gray-500 text-xs truncate" :title="name">{{ name }}</div>
              <div class="text-lg font-mono text-white mt-0.5">{{ value.toLocaleString() }}</div>
            </div>
          </div>
        </div>

        <!-- 步骤耗时统计 + 周期详情 -->
        <div class="panel-card" v-loading="loadingOverview">
          <div class="flex justify-between items-center mb-3">
            <div class="panel-header mb-0">
              <el-icon class="text-violet-400"><TrendCharts /></el-icon>
              <span v-if="searchActive">搜索结果: <span class="text-cyan-400 font-mono">{{ serialSearchActive }}</span></span>
              <span v-else>{{ selectedSession ? '会话详情' : (selectedDate ? '当日统计' : '数据详情') }}</span>
            </div>
            <div class="flex items-center gap-3">
              <span v-if="!searchActive && selectedSession" class="text-xs text-gray-500 font-mono">{{ selectedSession.session_uuid }}</span>
              <el-button v-if="!searchActive && selectedSession && cycles.length > 0" size="small" type="primary" plain round @click="exportSessionData">
                <el-icon class="mr-1" :size="12"><Download /></el-icon>导出会话
              </el-button>
            </div>
          </div>

          <!-- 步骤耗时统计 -->
          <div v-if="stepStats.length > 0" class="mb-4">
            <div class="text-xs text-gray-500 mb-2 font-medium">步骤耗时统计（仅正常轮次）</div>
            <el-table :data="stepStats" size="small" class="dark-table">
              <el-table-column prop="name" label="步骤名称" />
              <el-table-column prop="count" label="次数" width="70" align="center" />
              <el-table-column label="耗时 (秒)" width="150" align="center">
                <template #default="{ row }">
                  <span class="text-cyan-400 font-mono">{{ row.avg_duration.toFixed(2) }}</span>
                  <span class="text-gray-600 text-xs ml-1">({{ row.min_duration.toFixed(1) }}~{{ row.max_duration.toFixed(1) }})</span>
                </template>
              </el-table-column>
              <el-table-column label="到下步间隔 (秒)" width="160" align="center">
                <template #default="{ row, $index }">
                  <template v-if="$index < stepStats.length - 1">
                    <span class="text-blue-400 font-mono">{{ row.avg_interval.toFixed(2) }}</span>
                    <span class="text-gray-600 text-xs ml-1">({{ row.min_interval.toFixed(1) }}~{{ row.max_interval.toFixed(1) }})</span>
                  </template>
                  <span v-else class="text-gray-700">-</span>
                </template>
              </el-table-column>
            </el-table>
          </div>

          <!-- 周期详情 / 搜索结果 -->
          <div v-if="(selectedSession || searchActive) && (cycles.length > 0 || cycleResultFilter)">
            <div class="text-xs text-gray-500 mb-2 font-medium flex items-center justify-between">
              <span>{{ searchActive ? '匹配的周期' : '周期详情' }}</span>
              <!-- v3.48.1: OK/NG 结果筛选（只看 NG 录像） -->
              <el-radio-group v-if="!searchActive" v-model="cycleResultFilter" size="small" @change="handleCycleResultFilterChange" data-testid="cycle-result-filter">
                <el-radio-button label="">全部</el-radio-button>
                <el-radio-button label="ok">仅 OK</el-radio-button>
                <el-radio-button label="ng">仅 NG</el-radio-button>
              </el-radio-group>
            </div>
            <el-table 
              :data="cycles" 
              size="small" 
              class="dark-table" 
              max-height="360"
              row-key="id"
              :expand-row-keys="expandedRows"
              @expand-change="handleExpandChange"
            >
              <el-table-column type="expand">
                <template #default="{ row }">
                  <div class="px-4 py-3">
                    <div v-if="cycleStepsMap[row.id]" class="space-y-1.5">
                      <div 
                        v-for="step in cycleStepsMap[row.id]" 
                        :key="step.id"
                        class="flex items-center justify-between px-3 py-2 bg-slate-800/80 rounded-lg cursor-pointer hover:bg-slate-700/80 transition-colors"
                        @click="playStepVideo(step)"
                      >
                        <div class="flex items-center gap-3">
                          <span class="text-gray-600 text-xs font-mono w-6">#{{ step.step_order }}</span>
                          <span class="text-gray-200 text-sm">{{ step.step_name || step.step_label }}</span>
                        </div>
                        <div class="flex items-center gap-4 text-xs">
                          <span class="text-gray-500 font-mono">{{ formatTime(step.start_time) }}</span>
                          <span class="text-cyan-400 font-mono">{{ step.duration ? step.duration.toFixed(2) + 's' : '-' }}</span>
                          <span v-if="step.interval_to_next" class="text-blue-400 font-mono">
                            → {{ step.interval_to_next.toFixed(2) }}s
                          </span>
                          <el-icon v-if="step.video_id" class="text-violet-400" :size="14"><VideoPlay /></el-icon>
                        </div>
                      </div>
                    </div>
                    <div v-else class="text-center text-gray-600 py-3 text-sm">加载中...</div>
                  </div>
                </template>
              </el-table-column>
              <el-table-column prop="cycle_number" label="#" width="50" align="center" />
              <el-table-column label="工件码" min-width="130">
                <template #default="{ row }">
                  <span v-if="row.serial_no" class="font-mono text-cyan-300 text-xs">{{ row.serial_no }}</span>
                  <span v-else class="text-gray-600 text-xs">-</span>
                </template>
              </el-table-column>
              <!-- 搜索模式下额外的 来源 列 (项目 / 启动时间) -->
              <el-table-column v-if="searchActive" label="来源" min-width="160">
                <template #default="{ row }">
                  <div class="flex flex-col leading-tight">
                    <span class="text-violet-300 text-xs">{{ row.project_name || '—' }}</span>
                    <span class="text-gray-500 text-[10px] font-mono">
                      {{ row.session_start_time || row.session_uuid || '' }}
                    </span>
                  </div>
                </template>
              </el-table-column>
              <el-table-column :label="searchActive ? '周期开始' : '开始时间'" width="100">
                <template #default="{ row }">
                  <span class="font-mono text-xs">{{ searchActive ? (row.start_time || '').slice(5, 16) : formatTime(row.start_time) }}</span>
                </template>
              </el-table-column>
              <el-table-column label="耗时" width="80" align="center">
                <template #default="{ row }">
                  <span class="text-cyan-400 font-mono">{{ row.duration ? row.duration.toFixed(2) + 's' : '-' }}</span>
                </template>
              </el-table-column>
              <el-table-column label="结果" width="70" align="center">
                <template #default="{ row }">
                  <el-tag :type="row.is_good ? 'success' : 'danger'" size="small" effect="dark" round>
                    {{ row.is_good ? 'OK' : 'NG' }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="event_name" label="事件" min-width="80" />
              <el-table-column label="操作" width="70" align="center">
                <template #default="{ row }">
                  <el-button v-if="row.video_id" type="primary" link size="small" @click="playCycleVideo(row)">
                    <el-icon><VideoPlay /></el-icon>
                  </el-button>
                </template>
              </el-table-column>
            </el-table>
            <div v-if="cycleTotal > cyclePageSize" class="flex justify-end mt-2">
              <el-pagination
                small
                background
                layout="prev, pager, next"
                :total="cycleTotal"
                :page-size="cyclePageSize"
                :current-page="cyclePage"
                @current-change="handleCyclePageChange"
              />
            </div>
          </div>

          <!-- 无数据 -->
          <div v-if="!selectedDate && !selectedSession && stepStats.length === 0" class="text-center py-12">
            <el-icon :size="40" class="text-gray-700 mb-3"><Calendar /></el-icon>
            <div class="text-gray-600 text-sm">选择左侧日期查看历史数据</div>
          </div>
        </div>

        <!-- ========== 底部功能区：Tabs（记录设置/数据导出/存储与清理 + 随区弹窗） ========== -->
        <DataSettingsTabs
          :selected-date="selectedDate"
          :channel-filter="channelFilter"
          :total-channel-count="totalChannelCount"
          @open-custom-export="openCustomExportDialog"
          @cleared="handleDataCleared"
          @cleaned="loadAvailableDates"
        />
      </div>
    </div>

    <!-- 视频播放弹窗 -->
    <VideoPlayerDialog ref="videoPlayerRef" />

    <!-- v3.5.0: 自定义导出对话框 -->
    <CustomExportDialog
      v-model="customExportVisible"
      :initial-scope="customExportInitialScope"
      :initial-cycle-id="customExportInitialCycleId"
      :initial-session-id="customExportInitialSessionId"
      :initial-date-range="customExportInitialDateRange"
    />
  </div>
  </TjSlot>
</template>

<script setup>
import TjSlot from '@/components/TjSlot.vue';
import { ref, reactive, computed, onMounted, onUnmounted, watch } from 'vue';
import { useRouter } from 'vue-router';
import { useProjectStore } from '@/store/useProjectStore';
import { Calendar, DataLine, Download, Folder, TrendCharts, VideoPlay, Search } from '@element-plus/icons-vue';
import CustomExportDialog from './components/CustomExportDialog.vue';
import WeighingLedgerCard from './components/WeighingLedgerCard.vue';
import SessionListCard from './components/SessionListCard.vue';
import VideoPlayerDialog from './components/VideoPlayerDialog.vue';
import DataSettingsTabs from './components/DataSettingsTabs.vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { getDetectionResults, getWorkstations } from '@/api/detection';
import { 
  getSessionsByDate, 
  getSessionDates,
  renameSession,
  getSessionCycles,
  searchCyclesBySerial,
  getCycleSteps,
  getStepAverages,
  exportSessionCsv,
  downloadBlob,
  getVideoUrl
} from '@/api/data';
import { dbg, dbgErr } from '@/utils/debug';

const router = useRouter();
const projectStore = useProjectStore();

// 当前项目名称（从导航栏同步）
const currentProjectName = computed(() => {
  return projectStore.currentProject?.name || '';
});

// 称重投料模式项目 → 显示逐件记录台账（WeighingLedgerCard）
const isWeighingProject = computed(() => projectStore.currentProject?.logic_mode === 'weighing');

// 实时计数器（与Monitor同步）
const currentCounters = ref([]);

// 日期和会话
const selectedDate = ref('');
const availableDates = ref([]);
const sessions = ref([]);
const selectedSession = ref(null);

// 时间段筛选
const shiftType = ref('all');
const customStartHour = ref('08:00');
const customEndHour = ref('20:00');

// v3.35.1 自定义班次列表 (项目数据设置里配的, 有效条目 ≥2 才生效; 按开始时刻排序)
const customShifts = computed(() => {
  const dc = projectStore.currentProject?.data_config || {};
  const raw = Array.isArray(dc.shifts)
    ? dc.shifts.filter(s => s && (s.name || '').trim() && s.start)
    : [];
  return raw.length >= 2
    ? [...raw].sort((a, b) => (a.start < b.start ? -1 : 1))
    : null;
});
// 本班结束时刻 = 排序后下一班的开始时刻 (末班跨天回到首班)
const customShiftEnd = (idx) =>
  customShifts.value[(idx + 1) % customShifts.value.length].start;

// 切项目后班次列表变了 → 已选班次名失效时回落"全天", 防止拿旧名查空
watch(customShifts, (list) => {
  const t = shiftType.value;
  if (t === 'all' || t === 'custom') return;
  const okLegacy = !list && (t === 'day' || t === 'night');
  const okCustom = !!list && list.some(s => s.name === t);
  if (!okLegacy && !okCustom) shiftType.value = 'all';
});

const getShiftHours = () => {
  if (shiftType.value === 'all') return { start: null, end: null };
  if (shiftType.value === 'custom') return { start: customStartHour.value, end: customEndHour.value };
  // 自定义班次: shiftType 存的是班次名
  if (customShifts.value) {
    const idx = customShifts.value.findIndex(s => s.name === shiftType.value);
    if (idx !== -1) return { start: customShifts.value[idx].start, end: customShiftEnd(idx) };
  }
  const dc = projectStore.currentProject?.data_config || {};
  const dayStart = dc.day_shift_start || '08:00';
  const nightStart = dc.night_shift_start || '20:00';
  if (shiftType.value === 'day') return { start: dayStart, end: nightStart };
  if (shiftType.value === 'night') return { start: nightStart, end: dayStart };
  return { start: null, end: null };
};

// Multi-channel filter
const totalChannelCount = ref(1);
const channelFilter = ref(null); // null = all channels

// MES 工单筛选
const mesOrderFilter = ref(null);
const mesOrderOptions = ref([]);
const loadMesOrders = async () => {
  try {
    const { getOrders } = await import('@/api/mes');
    const res = await getOrders({ status: 'in_progress', limit: 100 });
    const doneRes = await getOrders({ status: 'completed', limit: 50 });
    mesOrderOptions.value = [...(res.data.items || []), ...(doneRes.data.items || [])];
  } catch {}
};

const handleChannelFilterChange = () => {
  if (selectedDate.value) {
    handleDateChange(selectedDate.value);
  }
};

const loadSessions = () => {
  if (selectedDate.value) {
    handleDateChange(selectedDate.value);
  } else {
    sessions.value = [];
    selectedSession.value = null;
    resetOverviewData();
  }
};

const loadChannelCount = async () => {
  try {
    const res = await getWorkstations();
    totalChannelCount.value = res.data.channel_count || 1;
  } catch (e) { /* keep default */ }
};

const cycles = ref([]);
const cycleTotal = ref(0);
const cyclePage = ref(1);
const cyclePageSize = ref(50);
const stepStats = ref([]);
const loadingSessions = ref(false);
const loadingOverview = ref(false);
const exporting = ref(false);

// v3.4.3 工件码搜索
const serialSearchInput = ref('');     // 输入框双向绑定
const serialSearchActive = ref('');    // 当前生效的搜索关键字
const searchActive = computed(() => !!serialSearchActive.value);
const loadingSerialSearch = ref(false);

// 历史数据概览
const overviewData = reactive({
  total_cycles: 0,
  good_cycles: 0,
  ng_cycles: 0,
  avg_cycle_time: 0,
  yield_rate: 0
});

// 历史计数器数据
const historyCounters = ref({});

// 周期步骤数据
const expandedRows = ref([]);
const cycleStepsMap = ref({});

// v3.5.0: 自定义导出对话框
const customExportVisible = ref(false);
const customExportInitialScope = ref('system');
const customExportInitialCycleId = ref(null);
const customExportInitialSessionId = ref(null);
const customExportInitialDateRange = ref(null);

function openCustomExportDialog() {
  // 默认按当前页面状态预填范围：
  // 1) 已选某 session → 单 session
  // 2) 否则 → 日期范围（左侧筛选器的日期）
  // 3) 兜底 system 级
  if (selectedSession.value?.id) {
    customExportInitialScope.value = 'session';
    customExportInitialSessionId.value = selectedSession.value.id;
    customExportInitialDateRange.value = null;
  } else if (selectedDate.value) {
    customExportInitialScope.value = 'range';
    customExportInitialSessionId.value = null;
    customExportInitialDateRange.value = [selectedDate.value, selectedDate.value];
  } else {
    customExportInitialScope.value = 'system';
    customExportInitialSessionId.value = null;
    customExportInitialDateRange.value = null;
  }
  customExportInitialCycleId.value = null;
  dbg('data.export', '发起自定义导出', `scope=${customExportInitialScope.value ?? ''}`);
  customExportVisible.value = true;
}

// 清空/按范围删除数据后（DataSettingsTabs 上抛）：刷新查询区
const handleDataCleared = () => {
  loadAvailableDates();
  resetOverviewData();
  sessions.value = [];
  cycles.value = [];
};

// 视频播放（VideoPlayerDialog 子组件）
const videoPlayerRef = ref(null);

// 轮询定时器
let pollingTimer = null;

// 禁用未来日期
const disabledDate = (time) => {
  return time.getTime() > Date.now();
};

// 格式化时间
const formatTime = (timeStr) => {
  if (!timeStr) return '';
  const parts = timeStr.split(' ');
  return parts.length > 1 ? parts[1].substring(0, 8) : timeStr;
};

// 格式化持续时间
const formatDuration = (seconds) => {
  if (!seconds && seconds !== 0) return '-';
  if (seconds < 60) return `${seconds.toFixed(2)}s`;
  const mins = Math.floor(seconds / 60);
  const secs = (seconds % 60).toFixed(1);
  return `${mins}m ${secs}s`;
};

// 跳转到项目选择
const goToProjectSelect = () => {
  router.push('/project');
};

// 从项目配置初始化计数器
const initCountersFromProject = () => {
  const countersConfig = projectStore.currentProject?.counters_config || [];
  currentCounters.value = countersConfig.map(c => ({
    name: c.name,
    value: c.value || 0
  }));
};

// 开始轮询实时数据（与Monitor同步）
const startPolling = () => {
  stopPolling();
  
  if (!projectStore.currentProjectId) return;
  
  pollingTimer = setInterval(async () => {
    try {
      // 多工位场景：
      // - 选了工位 => 只拉该工位
      // - 未选工位 => 拉全部工位并按同名计数器求和
      const channelsToPoll = channelFilter.value !== null
        ? [channelFilter.value]
        : Array.from({ length: Math.max(totalChannelCount.value, 1) }, (_, i) => i);

      const responses = await Promise.all(
        channelsToPoll.map((ch) => getDetectionResults(ch).catch(() => null))
      );

      const mergedCounters = {};
      for (const res of responses) {
        const counters = res?.data?.counters;
        if (!counters) continue;
        for (const [name, rawVal] of Object.entries(counters)) {
          const val = Number(rawVal);
          if (!Number.isFinite(val)) continue;
          mergedCounters[name] = (mergedCounters[name] || 0) + val;
        }
      }

      if (Object.keys(mergedCounters).length) {
        currentCounters.value = currentCounters.value.map(counter => ({
          ...counter,
          value: mergedCounters[counter.name] ?? counter.value
        }));
      }
    } catch (e) {
      // 静默失败
    }
  }, 1000);
};

// 停止轮询
const stopPolling = () => {
  if (pollingTimer) {
    clearInterval(pollingTimer);
    pollingTimer = null;
  }
};

// 加载有数据的日期
const loadAvailableDates = async () => {
  if (!projectStore.currentProjectId) return;
  
  try {
    const params = { project_id: projectStore.currentProjectId };
    if (channelFilter.value !== null) params.channel_id = channelFilter.value;
    const res = await getSessionDates(params);
    availableDates.value = res.data?.dates || [];
  } catch (e) {
    console.error('加载日期失败:', e);
  }
};

// 日期变化
const handleDateChange = async (date) => {
  if (!date) {
    sessions.value = [];
    selectedSession.value = null;
    resetOverviewData();
    return;
  }
  
  loadingSessions.value = true;
  dbg('data.query', '选择日期查询', `date=${date ?? ''} ch=${channelFilter.value ?? 'all'} shift=${shiftType.value ?? ''}`);
  try {
    const { start, end } = getShiftHours();
    const dc = projectStore.currentProject?.data_config || {};
    const shiftParam = dc.shift_split_enabled && shiftType.value !== 'all' && shiftType.value !== 'custom'
      ? shiftType.value : null;
    const res = await getSessionsByDate(date, projectStore.currentProjectId, start, end, channelFilter.value, shiftParam);
    sessions.value = res.data?.sessions || [];
    
    overviewData.total_cycles = res.data?.total_cycles || 0;
    overviewData.good_cycles = res.data?.total_good || 0;
    overviewData.ng_cycles = res.data?.total_ng || 0;
    overviewData.avg_cycle_time = res.data?.avg_cycle_time || 0;
    overviewData.yield_rate = overviewData.total_cycles > 0 
      ? (overviewData.good_cycles / overviewData.total_cycles * 100) 
      : 0;
    
    historyCounters.value = res.data?.counters_summary || {};
    
    await loadStepStats({ date, project_id: projectStore.currentProjectId });
    
    selectedSession.value = null;
    cycles.value = [];
    expandedRows.value = [];
    cycleStepsMap.value = {};
  } catch (e) {
    console.error('加载会话失败:', e);
  } finally {
    loadingSessions.value = false;
  }
};

// 时间段切换 - 重新加载当前日期的数据
const handleShiftChange = () => {
  if (selectedDate.value) {
    handleDateChange(selectedDate.value);
  }
};

// 重置概览数据
const resetOverviewData = () => {
  overviewData.total_cycles = 0;
  overviewData.good_cycles = 0;
  overviewData.ng_cycles = 0;
  overviewData.avg_cycle_time = 0;
  overviewData.yield_rate = 0;
  historyCounters.value = {};
  stepStats.value = [];
};

// 选择会话
const selectSession = async (session) => {
  // v3.4.3: 切换到 session 视图时退出搜索模式
  if (searchActive.value) {
    serialSearchInput.value = '';
    serialSearchActive.value = '';
  }
  dbg('data.query', '查看会话详情', `session_id=${session?.id ?? ''} uuid=${session?.session_uuid ?? ''}`);
  selectedSession.value = session;
  loadingOverview.value = true;
  expandedRows.value = [];
  cycleStepsMap.value = {};
  
  try {
    // 更新概览为会话数据
    overviewData.total_cycles = session.total_cycles;
    overviewData.good_cycles = session.good_cycles;
    overviewData.ng_cycles = session.ng_cycles;
    overviewData.avg_cycle_time = session.avg_cycle_time;
    overviewData.yield_rate = session.total_cycles > 0 
      ? (session.good_cycles / session.total_cycles * 100) 
      : 0;
    
    historyCounters.value = session.counters_snapshot || {};
    
    // 加载周期列表（分页）
    cyclePage.value = 1;
    await loadCycles(session.id);
    
    // 加载步骤统计
    await loadStepStats({ session_id: session.id });
  } catch (e) {
    console.error('加载会话详情失败:', e);
  } finally {
    loadingOverview.value = false;
  }
};

const loadCycles = async (sessionId) => {
  try {
    const skip = (cyclePage.value - 1) * cyclePageSize.value;
    const res = await getSessionCycles(sessionId, skip, cyclePageSize.value, cycleResultFilter.value || undefined);
    cycles.value = res.data?.items || [];
    cycleTotal.value = res.data?.total || 0;
  } catch (e) {
    console.error('加载周期列表失败:', e);
    cycles.value = [];
    cycleTotal.value = 0;
  }
};

// v3.48.1: OK/NG 结果筛选
const cycleResultFilter = ref('');
const handleCycleResultFilterChange = () => {
  dbg('data.query', '周期结果筛选', `result=${cycleResultFilter.value || '全部'}`);
  cyclePage.value = 1;
  expandedRows.value = [];
  cycleStepsMap.value = {};
  if (selectedSession.value) loadCycles(selectedSession.value.id);
};

const handleCyclePageChange = (page) => {
  dbg('data.query', '周期列表翻页', `page=${page ?? ''} search=${searchActive.value ? '1' : '0'}`);
  cyclePage.value = page;
  expandedRows.value = [];
  cycleStepsMap.value = {};
  if (searchActive.value) {
    loadSerialSearchPage();
  } else if (selectedSession.value) {
    loadCycles(selectedSession.value.id);
  }
};

// v3.4.3 工件码搜索 — 触发新搜索
const runSerialSearch = async () => {
  const q = (serialSearchInput.value || '').trim();
  if (!q) {
    ElMessage.info('请输入工件码');
    return;
  }
  dbg('data.query', '工件码搜索', `q=${q}`);
  serialSearchActive.value = q;
  selectedSession.value = null;
  cyclePage.value = 1;
  expandedRows.value = [];
  cycleStepsMap.value = {};
  await loadSerialSearchPage();
};

// 翻页 / 内部加载
const loadSerialSearchPage = async () => {
  if (!serialSearchActive.value) return;
  loadingSerialSearch.value = true;
  try {
    const skip = (cyclePage.value - 1) * cyclePageSize.value;
    const res = await searchCyclesBySerial(serialSearchActive.value, {
      skip, limit: cyclePageSize.value, fuzzy: true,
    });
    cycles.value = res.data?.items || [];
    cycleTotal.value = res.data?.total || 0;
    if (cycles.value.length === 0) {
      ElMessage.warning(`未找到与 "${serialSearchActive.value}" 关联的检测周期`);
    }
  } catch (e) {
    dbgErr('data.query', '工件码搜索', e);
    console.error('工件码搜索失败:', e);
    ElMessage.error('工件码搜索失败');
    cycles.value = [];
    cycleTotal.value = 0;
  } finally {
    loadingSerialSearch.value = false;
  }
};

// 清除搜索 → 回到正常 (按当前日期/会话) 模式
const clearSerialSearch = () => {
  serialSearchInput.value = '';
  serialSearchActive.value = '';
  cycles.value = [];
  cycleTotal.value = 0;
  cyclePage.value = 1;
  expandedRows.value = [];
  cycleStepsMap.value = {};
  if (selectedSession.value) {
    loadCycles(selectedSession.value.id);
  }
};

// 加载步骤统计
const loadStepStats = async (params) => {
  try {
    const res = await getStepAverages(params);
    stepStats.value = res.data?.steps || [];
  } catch (e) {
    console.error('加载步骤统计失败:', e);
  }
};

// 处理周期行展开
const handleExpandChange = async (row, expandedRowsList) => {
  expandedRows.value = expandedRowsList.map(r => r.id);
  
  // 如果展开且没有加载过步骤数据
  if (expandedRowsList.includes(row) && !cycleStepsMap.value[row.id]) {
    try {
      const res = await getCycleSteps(row.id);
      cycleStepsMap.value[row.id] = res.data || [];
    } catch (e) {
      console.error('加载周期步骤失败:', e);
    }
  }
};

// 播放步骤视频
const playStepVideo = (step) => {
  if (step.video_id) {
    videoPlayerRef.value?.open(getVideoUrl(step.video_id));
  } else {
    ElMessage.info('该步骤无录制视频（请在记录设置中开启"录制步骤视频"后重新检测）');
  }
};

// 播放周期视频
const playCycleVideo = (cycle) => {
  dbg('data.query', '回放周期视频', `cycle_id=${cycle?.id ?? ''} video_id=${cycle?.video_id ?? '无'}`);
  if (cycle.video_id) {
    videoPlayerRef.value?.open(getVideoUrl(cycle.video_id));
  } else {
    ElMessage.info('该周期无录制视频（请在记录设置中开启"录制周期视频"后重新检测）');
  }
};

// 播放会话视频
const playSessionVideo = (session) => {
  if (session.video_id) {
    videoPlayerRef.value?.open(getVideoUrl(session.video_id));
  } else {
    ElMessage.info('该会话无录制视频（请在记录设置中开启"录制会话视频"后重新检测）');
  }
};

// v3.6.2: 重命名会话标识 (客户自定义"会话 ID")
const openRenameSessionDialog = async (session) => {
  try {
    const { value } = await ElMessageBox.prompt(
      `给该会话设置/修改业务标识。可留空清除（系统 UUID 不变）。

不允许字符: / \\ : * ? " < > |
最长 64 字符`,
      `会话标识 — ${session.session_uuid}`,
      {
        inputValue: session.name || '',
        inputPlaceholder: '如 LINE3-NIGHT-20260510 / BATCH-A1234 (可留空)',
        confirmButtonText: '保存',
        cancelButtonText: '取消',
        inputValidator: (val) => {
          if (!val) return true;
          if (/[\\/:*?"<>|\r\n\t]/.test(val)) return '不能含 / \\ : * ? " < > | 等字符';
          if (val.length > 64) return '最长 64 个字符';
          return true;
        },
      }
    );
    const cleaned = (value || '').trim();
    await renameSession(session.id, cleaned || null);
    session.name = cleaned || null;
    ElMessage.success(cleaned ? `会话标识已更新为：${cleaned}` : '会话标识已清除');
  } catch (e) {
    if (e === 'cancel' || e === 'close') return;
    const detail = e?.response?.data?.detail || e?.message || '未知错误';
    ElMessage.error('重命名失败: ' + detail);
  }
};

// 导出当前会话
const exportSessionData = async () => {
  if (!selectedSession.value) return;
  
  exporting.value = true;
  dbg('data.export', '点击「导出当前会话」', `session_id=${selectedSession.value?.id ?? ''}`);
  try {
    const res = await exportSessionCsv(selectedSession.value.id);
    const filename = `session_${selectedSession.value.session_uuid}_${selectedDate.value}.csv`;
    downloadBlob(res.data, filename);
    ElMessage.success(`已导出到下载文件夹: ${filename}`);
  } catch (e) {
    dbgErr('data.export', '导出当前会话', e);
    console.error('导出失败:', e);
    ElMessage.error('导出失败: ' + (e.message || '未知错误'));
  } finally {
    exporting.value = false;
  }
};

// 监听项目变化
watch(() => projectStore.currentProjectId, (newId) => {
  if (newId) {
    initCountersFromProject();
    loadAvailableDates();
    startPolling();
    // 重置选择状态
    selectedDate.value = '';
    selectedSession.value = null;
    sessions.value = [];
    cycles.value = [];
    resetOverviewData();
  } else {
    stopPolling();
  }
}, { immediate: true });

// 监听项目计数器配置变化
watch(() => projectStore.currentProject?.counters_config, (newCounters) => {
  if (newCounters) {
    currentCounters.value = newCounters.map(c => ({
      name: c.name,
      value: c.value || 0
    }));
  }
}, { deep: true });

onMounted(() => {
  loadChannelCount();
  loadMesOrders();
  
  if (projectStore.currentProjectId) {
    loadAvailableDates();
  }
});

onUnmounted(() => {
  stopPolling();
});
</script>

<style scoped>
/* =================== Panel & Card Components =================== */
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

/* =================== Stat Cards =================== */
.stat-card {
  position: relative;
  background: rgba(15, 23, 42, 0.8);
  border: 1px solid rgba(51, 65, 85, 0.4);
  border-radius: 10px;
  padding: 14px 16px;
  text-align: center;
  overflow: hidden;
}

.stat-card::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 2px;
  border-radius: 10px 10px 0 0;
}

.stat-card--cyan::before { background: linear-gradient(90deg, #06b6d4, #22d3ee); }
.stat-card--blue::before { background: linear-gradient(90deg, #3b82f6, #60a5fa); }
.stat-card--green::before { background: linear-gradient(90deg, #10b981, #34d399); }
.stat-card--yellow::before { background: linear-gradient(90deg, #f59e0b, #fbbf24); }
.stat-card--mixed::before { background: linear-gradient(90deg, #10b981, #ef4444); }

.stat-card__label {
  font-size: 0.6875rem;
  color: #64748b;
  margin-bottom: 0.25rem;
}

.stat-card__value {
  font-size: 1.375rem;
  font-weight: 700;
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  line-height: 1.2;
}

/* =================== Table Dark Theme =================== */
.dark-table :deep(.el-table),
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

:deep(.el-table--border .el-table__cell) {
  border-right: 1px solid rgba(51, 65, 85, 0.3);
}

:deep(.el-table__body tr:hover > td.el-table__cell) {
  background-color: rgba(51, 65, 85, 0.3) !important;
}

:deep(.el-table__expand-icon) {
  color: #64748b;
}

:deep(.el-table__expand-icon--expanded) {
  color: #22d3ee;
}

:deep(.el-table__empty-text) {
  color: #475569;
}

/* =================== Input / Date Picker Dark Theme =================== */
:deep(.el-date-editor) {
  --el-input-bg-color: rgba(30, 41, 59, 0.8);
  --el-input-border-color: rgba(51, 65, 85, 0.5);
  --el-input-text-color: #e2e8f0;
  --el-input-placeholder-color: #475569;
  --el-input-hover-border-color: #475569;
  --el-input-focus-border-color: #0ea5e9;
}

:deep(.el-date-editor .el-input__wrapper) {
  background-color: rgba(30, 41, 59, 0.8);
  box-shadow: 0 0 0 1px rgba(51, 65, 85, 0.5) inset;
}

:deep(.el-date-editor .el-input__wrapper:hover) {
  box-shadow: 0 0 0 1px rgba(71, 85, 105, 0.7) inset;
}

:deep(.el-date-editor .el-input__wrapper.is-focus) {
  box-shadow: 0 0 0 1px rgba(14, 165, 233, 0.6) inset;
}

:deep(.el-date-editor .el-input__inner) {
  color: #e2e8f0;
}

:deep(.el-date-editor .el-input__prefix-inner .el-icon) {
  color: #475569;
}

/* =================== Divider =================== */
:deep(.el-divider) {
  border-color: rgba(51, 65, 85, 0.4);
}

/* =================== Button Tweaks =================== */
:deep(.el-button--default) {
  --el-button-bg-color: rgba(30, 41, 59, 0.8);
  --el-button-border-color: rgba(51, 65, 85, 0.5);
  --el-button-text-color: #e2e8f0;
  --el-button-hover-bg-color: rgba(51, 65, 85, 0.6);
  --el-button-hover-text-color: #f1f5f9;
}
</style>
