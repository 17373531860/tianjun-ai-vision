<template>
  <div class="data-page">
    <header class="hub-topbar">
      <button class="hub-back" data-test="back-wall" @click="router.push({ name: 'wall' })">
        ← 监控墙
      </button>
      <h1>数据中心</h1>
      <span v-if="summary?.catching_up" class="hub-badge warn" data-test="catching-up">
        数据追赶中
      </span>
      <nav class="tabs">
        <button v-for="t in TABS" :key="t.id" class="tab-btn"
                :class="{ active: tab === t.id }" :data-test="`tab-${t.id}`"
                @click="tab = t.id">{{ t.label }}</button>
      </nav>
    </header>

    <!-- 全局筛选条: 时间 × 节点 × 工位, 全页所有面板一起变 (Grafana 惯例) -->
    <div class="filter-bar">
      <div class="preset-group" data-test="presets">
        <button v-for="p in PRESETS" :key="p.id" class="preset-btn"
                :class="{ active: preset === p.id }" :data-test="`preset-${p.id}`"
                @click="preset = p.id">{{ p.label }}</button>
      </div>
      <template v-if="preset === 'custom'">
        <input type="date" v-model="customStart" class="hub-input date" data-test="custom-start" />
        <span class="date-sep">—</span>
        <input type="date" v-model="customEnd" class="hub-input date" data-test="custom-end" />
      </template>
      <select v-model="nodeFilter" class="hub-input sel" data-test="node-filter">
        <option :value="null">全部节点</option>
        <option v-for="n in nodeOptions" :key="n.id" :value="n.id">{{ n.name }}</option>
      </select>
      <select v-model="channelFilter" class="hub-input sel" data-test="channel-filter"
              :disabled="nodeFilter === null">
        <option :value="null">全部工位</option>
        <option v-for="c in channelOptions" :key="c.channel_id" :value="c.channel_id">
          {{ c.station_name }}</option>
      </select>
      <span class="spacer" />
      <button class="hub-btn ghost sm" data-test="export-csv" @click="exportCsv">
        导出 CSV
      </button>
    </div>

    <main class="content">
      <p v-if="loadError" class="hub-load-error" data-test="data-error">{{ loadError }}</p>

      <!-- ============ Tab 1: 总览 ============ -->
      <template v-if="tab === 'overview'">
        <div v-if="summary" class="kpi-cards" data-test="kpi-cards">
          <div class="kcard">
            <span class="knum tabular" data-test="kpi-total">{{ summary.current.total }}</span>
            <span class="klabel">总产量</span>
            <span class="kdelta tabular" :class="deltaCls(deltaTotal)" data-test="kpi-total-delta">
              {{ fmtDelta(deltaTotal, '%') }}</span>
          </div>
          <div class="kcard">
            <span class="knum tabular" :class="yieldCls(summary.current.yield_rate)"
                  data-test="kpi-yield">{{ fmtPct(summary.current.yield_rate) }}</span>
            <span class="klabel">合格率</span>
            <span class="kdelta tabular" :class="deltaCls(deltaYield)">
              {{ fmtDelta(deltaYield, 'pp') }}</span>
          </div>
          <div class="kcard">
            <span class="knum tabular" :class="summary.current.ng ? 'ng-num' : ''"
                  data-test="kpi-ng">{{ summary.current.ng }}</span>
            <span class="klabel">NG 件数</span>
            <span class="kdelta tabular" :class="deltaCls(-deltaNg)">
              {{ fmtDelta(deltaNg, '%') }}</span>
          </div>
          <div class="kcard">
            <span class="knum tabular" data-test="kpi-duration">
              {{ fmtDur(summary.current.avg_duration_ms) }}</span>
            <span class="klabel">平均周期耗时</span>
          </div>
          <div class="kcard">
            <span class="knum tabular" data-test="kpi-stations">
              {{ summary.stations_active }}<i class="dim-sep">/</i>{{ summary.stations_total }}</span>
            <span class="klabel">有产出工位</span>
          </div>
        </div>

        <div class="panel chart-panel">
          <h2>产量与合格率趋势</h2>
          <div v-if="emptyWindow" class="chart-empty" data-test="trend-empty">
            这段时间没有结算记录
          </div>
          <EchartsPane v-else :option="trendOption" class="trend-chart" data-test="trend-chart" />
        </div>

        <div class="two-col">
          <div class="panel">
            <h2>NG 原因 Top{{ pareto?.items?.length || 0 }}</h2>
            <div v-if="!pareto || !pareto.total_ng" class="chart-empty">窗口内无 NG</div>
            <EchartsPane v-else :option="paretoOption" class="pareto-chart"
                         @chart-click="onParetoClick" />
          </div>
          <div class="panel table-panel">
            <h2>工位排行（最差在上）</h2>
            <table class="hub-table" data-test="stations-table">
              <thead>
                <tr>
                  <th>节点 / 工位</th><th>项目</th>
                  <th class="num">产量</th><th class="num">NG</th>
                  <th class="num">合格率</th><th class="num">均耗时</th>
                  <th>趋势</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="s in stations?.items || []"
                    :key="`${s.node_id}-${s.channel_id}`"
                    :data-test="`st-row-${s.node_id}-${s.channel_id}`">
                  <td>
                    <router-link class="st-link"
                      :to="{ name: 'station', params: { nodeId: s.node_id, channelId: s.channel_id } }">
                      {{ s.node_name }} · {{ s.station_name }}
                    </router-link>
                  </td>
                  <td class="dim">{{ s.project_name || '—' }}</td>
                  <td class="num tabular">{{ s.total }}</td>
                  <td class="num tabular" :class="s.ng ? 'ng-num' : ''">{{ s.ng }}</td>
                  <td class="num tabular" :class="yieldCls(s.yield_rate)">
                    {{ fmtPct(s.yield_rate) }}</td>
                  <td class="num tabular">{{ fmtDur(s.avg_duration_ms) }}</td>
                  <td class="spark-cell">
                    <svg v-if="s.total" class="spark" viewBox="0 0 64 20" preserveAspectRatio="none">
                      <polyline :points="sparkPoints(s.spark)" />
                    </svg>
                    <span v-else class="dim">—</span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </template>

      <!-- ============ Tab 2: NG 分析 ============ -->
      <template v-else-if="tab === 'ng'">
        <div class="two-col">
          <div class="panel">
            <h2>NG Pareto <span class="hint">点击条形筛选原因</span></h2>
            <div v-if="!pareto || !pareto.total_ng" class="chart-empty" data-test="ng-empty">
              窗口内无 NG
            </div>
            <EchartsPane v-else :option="paretoOption" class="pareto-chart"
                         data-test="ng-pareto" @chart-click="onParetoClick" />
          </div>
          <div class="panel table-panel">
            <h2>原因 × 工位交叉表</h2>
            <div v-if="!ngMatrix || !ngMatrix.reasons.length" class="chart-empty">窗口内无 NG</div>
            <table v-else class="hub-table matrix" data-test="ng-matrix">
              <thead>
                <tr>
                  <th>原因</th>
                  <th v-for="st in ngMatrix.stations" :key="`${st.node_id}-${st.channel_id}`"
                      class="num col-st">{{ st.name }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(reason, i) in ngMatrix.reasons" :key="reason"
                    :class="{ selected: reasonFilter === reason }"
                    @click="toggleReason(reason)">
                  <td class="reason-cell">{{ reason }}</td>
                  <td v-for="(st, j) in ngMatrix.stations" :key="j"
                      class="num tabular heat" :style="heatStyle(ngMatrix.cells[i][j])">
                    {{ ngMatrix.cells[i][j] || '' }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <div class="panel table-panel">
          <h2>
            NG 样本
            <span v-if="reasonFilter" class="hub-badge neutral reason-tag" data-test="reason-tag">
              {{ reasonFilter }}
              <button class="tag-x" data-test="reason-clear" @click="reasonFilter = null">✕</button>
            </span>
            <span class="hint">最近 {{ ngSamples?.items?.length || 0 }} 条 / 共 {{ ngSamples?.total || 0 }}</span>
          </h2>
          <table class="hub-table" data-test="ng-samples">
            <thead>
              <tr><th>时间</th><th>节点 / 工位</th><th>项目</th><th>事件</th><th>原因</th><th class="num">耗时</th></tr>
            </thead>
            <tbody>
              <tr v-for="c in ngSamples?.items || []" :key="c.id">
                <td class="tabular dim">{{ fmtTs(c.ts_epoch) }}</td>
                <td>
                  <router-link class="st-link"
                    :to="{ name: 'station', params: { nodeId: c.node_id, channelId: c.channel_id } }">
                    {{ c.node_name }} · {{ c.station_name }}
                  </router-link>
                </td>
                <td class="dim">{{ c.project_name || '—' }}</td>
                <td>{{ c.event_name || '周期结算' }}</td>
                <td class="reason-cell">{{ c.reason || '—' }}</td>
                <td class="num tabular">{{ fmtDur(c.duration_ms) }}</td>
              </tr>
            </tbody>
          </table>
          <p v-if="ngSamples && !ngSamples.items.length" class="chart-empty">无匹配样本</p>
        </div>
      </template>

      <!-- ============ Tab 3: 周期明细 ============ -->
      <template v-else>
        <div class="panel table-panel">
          <div class="detail-head">
            <h2>周期明细 <span class="hint">共 {{ cycles?.total || 0 }} 条</span></h2>
            <select v-model="resultFilter" class="hub-input sel sm" data-test="result-filter">
              <option :value="null">全部结果</option>
              <option value="OK">仅 OK</option>
              <option value="NG">仅 NG</option>
            </select>
          </div>
          <table class="hub-table" data-test="cycles-table">
            <thead>
              <tr><th>时间</th><th>节点 / 工位</th><th>项目</th><th>结果</th><th>原因</th><th class="num">耗时</th></tr>
            </thead>
            <tbody>
              <tr v-for="c in cycles?.items || []" :key="c.id" :data-test="`cy-row-${c.id}`">
                <td class="tabular dim">{{ fmtTs(c.ts_epoch) }}</td>
                <td>{{ c.node_name }} · {{ c.station_name }}</td>
                <td class="dim">{{ c.project_name || '—' }}</td>
                <td>
                  <span class="hub-badge" :class="c.result === 'NG' ? 'ng' : 'neutral'">
                    {{ c.result }}</span>
                </td>
                <td class="reason-cell">{{ c.reason || c.event_name || '—' }}</td>
                <td class="num tabular">{{ fmtDur(c.duration_ms) }}</td>
              </tr>
            </tbody>
          </table>
          <p v-if="cycles && !cycles.items.length" class="chart-empty" data-test="cycles-empty">
            这段时间没有结算记录
          </p>
          <div v-if="cycles && cycles.total > PAGE" class="pager">
            <button class="hub-btn ghost sm" :disabled="page === 0" data-test="page-prev"
                    @click="page--">上一页</button>
            <span class="tabular dim">{{ page + 1 }} / {{ Math.ceil(cycles.total / PAGE) }}</span>
            <button class="hub-btn ghost sm" :disabled="(page + 1) * PAGE >= cycles.total"
                    data-test="page-next" @click="page++">下一页</button>
          </div>
        </div>
      </template>
    </main>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import api from '../api'
import EchartsPane from '../components/EchartsPane.vue'

const router = useRouter()

// ---- 图表配色: 与 App.vue :root tokens 同值 (canvas 拿不到 CSS 变量) ----
const C = {
  text2: '#cbd5e1', text3: '#94a3b8', text4: '#64748b',
  border: '#334155', soft: 'rgba(148, 163, 184, 0.12)',
  primary: '#00a8ff', ng: '#f87171', line: '#e2e8f0',
}

const TABS = [
  { id: 'overview', label: '总览' },
  { id: 'ng', label: 'NG 分析' },
  { id: 'cycles', label: '周期明细' },
]
const PRESETS = [
  { id: 'today', label: '今天' },
  { id: 'yesterday', label: '昨天' },
  { id: '7d', label: '近 7 天' },
  { id: '30d', label: '近 30 天' },
  { id: 'custom', label: '自定义' },
]
const PAGE = 50

const tab = ref('overview')
const preset = ref('today')
const customStart = ref('')
const customEnd = ref('')
const nodeFilter = ref(null)
const channelFilter = ref(null)
const resultFilter = ref(null)
const reasonFilter = ref(null)
const page = ref(0)

const summary = ref(null)
const timeseries = ref(null)
const pareto = ref(null)
const stations = ref(null)
const ngMatrix = ref(null)
const ngSamples = ref(null)
const cycles = ref(null)
const allStations = ref([])       // 不带过滤的工位全集 (节点/工位下拉数据源)
const loadError = ref('')
let timer = null

// ---- 时间窗 ----
function dayStart(d) {
  const x = new Date(d)
  x.setHours(0, 0, 0, 0)
  return Math.floor(x.getTime() / 1000)
}
const window_ = computed(() => {
  const now = Math.floor(Date.now() / 1000)
  const today0 = dayStart(new Date())
  if (preset.value === 'today') return { start: today0, end: now }
  if (preset.value === 'yesterday') return { start: today0 - 86400, end: today0 }
  if (preset.value === '7d') return { start: today0 - 6 * 86400, end: now }
  if (preset.value === '30d') return { start: today0 - 29 * 86400, end: now }
  // custom: 含尾日整天
  const s = customStart.value ? dayStart(customStart.value) : today0
  const e = customEnd.value ? dayStart(customEnd.value) + 86400 : now
  return { start: s, end: Math.min(e, now) }
})

const baseParams = computed(() => ({
  start: window_.value.start,
  end: window_.value.end,
  node_id: nodeFilter.value ?? undefined,
  channel_id: channelFilter.value ?? undefined,
}))

// ---- 下拉数据源 ----
const nodeOptions = computed(() => {
  const seen = new Map()
  for (const s of allStations.value) if (!seen.has(s.node_id)) seen.set(s.node_id, s.node_name)
  return [...seen].map(([id, name]) => ({ id, name }))
})
const channelOptions = computed(() =>
  allStations.value.filter((s) => s.node_id === nodeFilter.value))

// ---- 加载 ----
async function loadOverview() {
  const p = baseParams.value
  const [s, t, pa, st] = await Promise.all([
    api.get('/stats/summary', { params: p }),
    api.get('/stats/timeseries', { params: p }),
    api.get('/stats/pareto', { params: { ...p, top: 8 } }),
    api.get('/stats/stations', {
      params: { start: p.start, end: p.end, node_id: p.node_id } }),
  ])
  summary.value = s.data
  timeseries.value = t.data
  pareto.value = pa.data
  stations.value = st.data
}

async function loadNg() {
  const p = baseParams.value
  const [pa, m, sm] = await Promise.all([
    api.get('/stats/pareto', { params: { ...p, top: 10 } }),
    api.get('/stats/ng-matrix', {
      params: { start: p.start, end: p.end, node_id: p.node_id } }),
    api.get('/stats/cycles', {
      params: { ...p, result: 'NG', reason: reasonFilter.value || undefined, limit: 50 } }),
  ])
  pareto.value = pa.data
  ngMatrix.value = m.data
  ngSamples.value = sm.data
}

async function loadCycles() {
  const p = baseParams.value
  const r = await api.get('/stats/cycles', {
    params: { ...p, result: resultFilter.value || undefined,
              limit: PAGE, offset: page.value * PAGE } })
  cycles.value = r.data
}

async function refresh() {
  try {
    if (tab.value === 'overview') await loadOverview()
    else if (tab.value === 'ng') await loadNg()
    else await loadCycles()
    loadError.value = ''
  } catch (e) {
    if (e.response?.status === 401) return
    loadError.value = '数据加载失败，正在重试…'
  }
}

async function loadStationDict() {
  try {
    const r = await api.get('/stats/stations', { params: baseParams.value })
    allStations.value = r.data.items
  } catch { /* 下拉退化为空, 不阻塞主查询 */ }
}

// 筛选变更 → 全页重拉 (Grafana 惯例); 切节点清工位选择
watch(nodeFilter, () => { channelFilter.value = null })
watch([preset, customStart, customEnd, nodeFilter, channelFilter], () => {
  page.value = 0
  refresh()
})
watch(tab, refresh)
watch(resultFilter, () => { page.value = 0; refresh() })
watch(page, refresh)
watch(reasonFilter, () => { if (tab.value === 'ng') refresh() })

onMounted(() => {
  refresh()
  loadStationDict()
  timer = setInterval(refresh, 60000)   // 分析页 60s 慢刷 (不与监控墙抢实时)
})
onBeforeUnmount(() => clearInterval(timer))

// ---- KPI 派生 ----
const emptyWindow = computed(() =>
  summary.value && summary.value.current.total === 0)
function pctDelta(cur, prev) {
  if (!prev) return null
  return ((cur - prev) / prev) * 100
}
const deltaTotal = computed(() =>
  summary.value?.prev ? pctDelta(summary.value.current.total, summary.value.prev.total) : null)
const deltaNg = computed(() =>
  summary.value?.prev ? pctDelta(summary.value.current.ng, summary.value.prev.ng) : null)
const deltaYield = computed(() => {
  const c = summary.value?.current?.yield_rate
  const p = summary.value?.prev?.yield_rate
  if (c == null || p == null) return null
  return (c - p) * 100   // 百分点
})

function fmtPct(v) { return v == null ? '—' : `${(v * 100).toFixed(1)}%` }
function fmtDur(ms) {
  if (ms == null) return '—'
  if (ms < 1000) return `${ms}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  return `${Math.floor(ms / 60000)}m${Math.round((ms % 60000) / 1000)}s`
}
function fmtDelta(v, unit) {
  if (v == null) return ''
  const sign = v > 0 ? '+' : ''
  return unit === 'pp' ? `${sign}${v.toFixed(1)}pp` : `${sign}${v.toFixed(0)}%`
}
function deltaCls(v) {
  // 正向绿 / 负向红只用于"越大越好"的指标; NG 调用方先取负
  if (v == null || Math.abs(v) < 0.05) return 'dim'
  return v > 0 ? 'up' : 'down'
}
function yieldCls(v) {
  if (v == null) return 'dim'
  if (v < 0.90) return 'ng-num'
  if (v < 0.95) return 'warn-num'
  return ''
}
function fmtTs(epoch) {
  const d = new Date(epoch * 1000)
  const p = (n) => String(n).padStart(2, '0')
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

// ---- 趋势图 ----
function fmtBucket(epoch, interval) {
  const d = new Date(epoch * 1000)
  const p = (n) => String(n).padStart(2, '0')
  if (interval === 'day') return `${d.getMonth() + 1}/${d.getDate()}`
  return `${p(d.getHours())}:00`
}
const trendOption = computed(() => {
  const ts = timeseries.value
  if (!ts) return {}
  const cats = ts.buckets.map((b) => fmtBucket(b.bucket_epoch, ts.interval))
  return {
    backgroundColor: 'transparent',
    grid: { left: 48, right: 52, top: 28, bottom: 28 },
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#2d3b50', borderColor: C.border,
      textStyle: { color: C.line, fontSize: 12 },
      valueFormatter: (v) => (v == null ? '—' : v),
    },
    legend: {
      top: 0, left: 'center', itemWidth: 12, itemHeight: 8,
      textStyle: { color: C.text3, fontSize: 12 },
    },
    xAxis: {
      type: 'category', data: cats,
      axisLine: { lineStyle: { color: C.border } },
      axisLabel: { color: C.text3, fontSize: 11 },
      axisTick: { show: false },
    },
    yAxis: [
      { type: 'value', name: '产量',
        nameTextStyle: { color: C.text4, fontSize: 11 },
        axisLabel: { color: C.text3, fontSize: 11 },
        splitLine: { lineStyle: { color: C.soft } } },
      { type: 'value', name: '合格率%', min: 0, max: 100,
        nameTextStyle: { color: C.text4, fontSize: 11 },
        axisLabel: { color: C.text3, fontSize: 11 },
        splitLine: { show: false } },
    ],
    series: [
      { name: '产量', type: 'bar', data: ts.buckets.map((b) => b.total),
        itemStyle: { color: 'rgba(0, 168, 255, 0.55)', borderRadius: [2, 2, 0, 0] },
        barMaxWidth: 26 },
      { name: '合格率', type: 'line', yAxisIndex: 1,
        // 缺桶 null → 断线 (调研铁律: 生产计数禁止插值)
        data: ts.buckets.map((b) => (b.yield_rate == null ? null : +(b.yield_rate * 100).toFixed(1))),
        connectNulls: false, symbol: 'circle', symbolSize: 5,
        itemStyle: { color: C.line }, lineStyle: { color: C.line, width: 2 } },
    ],
  }
})

// ---- Pareto ----
const paretoOption = computed(() => {
  const p = pareto.value
  if (!p || !p.total_ng) return {}
  const items = [...p.items]
  if (p.other_count > 0) {
    items.push({ reason: '其他', count: p.other_count, pct: 0, cum_pct: 1 })
  }
  return {
    backgroundColor: 'transparent',
    grid: { left: 44, right: 46, top: 24, bottom: 56 },
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#2d3b50', borderColor: C.border,
      textStyle: { color: C.line, fontSize: 12 },
    },
    xAxis: {
      type: 'category', data: items.map((i) => i.reason),
      axisLine: { lineStyle: { color: C.border } },
      axisLabel: {
        color: C.text3, fontSize: 11, rotate: 28, width: 84,
        overflow: 'truncate',
      },
      axisTick: { show: false },
    },
    yAxis: [
      { type: 'value',
        axisLabel: { color: C.text3, fontSize: 11 },
        splitLine: { lineStyle: { color: C.soft } } },
      { type: 'value', min: 0, max: 100,
        axisLabel: { color: C.text3, fontSize: 11, formatter: '{value}%' },
        splitLine: { show: false } },
    ],
    series: [
      { name: 'NG 件数', type: 'bar', data: items.map((i) => i.count),
        itemStyle: {
          color: (pp) => (reasonFilter.value && items[pp.dataIndex].reason === reasonFilter.value
            ? '#ef4444' : 'rgba(248, 113, 113, 0.65)'),
          borderRadius: [2, 2, 0, 0],
        },
        barMaxWidth: 30 },
      { name: '累计占比', type: 'line', yAxisIndex: 1,
        data: items.map((i) => +(i.cum_pct * 100).toFixed(1)),
        symbol: 'circle', symbolSize: 4,
        itemStyle: { color: C.text3 }, lineStyle: { color: C.text3, width: 1.5 },
        markLine: {
          silent: true, symbol: 'none',
          label: { show: false },
          lineStyle: { color: C.text4, type: 'dashed', width: 1 },
          data: [{ yAxis: 80 }],
        } },
    ],
  }
})

function onParetoClick(p) {
  if (p?.componentType !== 'series' || p.seriesType !== 'bar') return
  const reason = p.name === '其他' ? null : p.name
  reasonFilter.value = reasonFilter.value === reason ? null : reason
  if (tab.value !== 'ng') tab.value = 'ng'
}
function toggleReason(reason) {
  reasonFilter.value = reasonFilter.value === reason ? null : reason
}

// ---- 交叉表热力 ----
const matrixMax = computed(() => {
  const cells = ngMatrix.value?.cells || []
  return Math.max(1, ...cells.flat())
})
function heatStyle(v) {
  if (!v) return {}
  const a = 0.08 + 0.4 * (v / matrixMax.value)
  return { background: `rgba(239, 68, 68, ${a.toFixed(3)})` }
}

// ---- sparkline (inline SVG, 不为每行起 ECharts 实例) ----
function sparkPoints(spark) {
  if (!spark || !spark.length) return ''
  const max = Math.max(1, ...spark)
  const n = spark.length
  return spark.map((v, i) => {
    const x = n === 1 ? 32 : (i / (n - 1)) * 64
    const y = 18 - (v / max) * 16
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')
}

// ---- CSV 导出 (当前 Tab 当前筛选; BOM 让 Excel 认 UTF-8 中文) ----
function csvEscape(v) {
  const s = v == null ? '' : String(v)
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
}
function downloadCsv(rows, filename) {
  const text = '\ufeff' + rows.map((r) => r.map(csvEscape).join(',')).join('\r\n')
  const blob = new Blob([text], { type: 'text/csv;charset=utf-8' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = filename
  a.click()
  URL.revokeObjectURL(a.href)
}
async function exportCsv() {
  const stamp = new Date().toISOString().slice(0, 10)
  if (tab.value === 'overview') {
    const rows = [['节点', '工位', '项目', '产量', 'OK', 'NG', '合格率', '平均耗时ms']]
    for (const s of stations.value?.items || []) {
      rows.push([s.node_name, s.station_name, s.project_name || '',
                 s.total, s.ok, s.ng,
                 s.yield_rate == null ? '' : (s.yield_rate * 100).toFixed(2) + '%',
                 s.avg_duration_ms ?? ''])
    }
    downloadCsv(rows, `数据中心-工位统计-${stamp}.csv`)
    return
  }
  // NG 分析 / 明细: 拉当前筛选的原始行 (上限 500, 更多请缩小时间窗)
  const p = baseParams.value
  const params = tab.value === 'ng'
    ? { ...p, result: 'NG', reason: reasonFilter.value || undefined, limit: 500 }
    : { ...p, result: resultFilter.value || undefined, limit: 500 }
  const r = await api.get('/stats/cycles', { params })
  const rows = [['时间', '节点', '工位', '项目', '结果', '事件', '原因', '耗时ms']]
  for (const c of r.data.items) {
    rows.push([fmtTs(c.ts_epoch), c.node_name, c.station_name,
               c.project_name || '', c.result, c.event_name || '',
               c.reason || '', c.duration_ms ?? ''])
  }
  downloadCsv(rows, `数据中心-周期明细-${stamp}.csv`)
}
</script>

<style scoped>
.data-page { min-height: 100%; }
.tabs { margin-left: 24px; display: flex; gap: 4px; }
.tab-btn {
  background: transparent; border: none; color: var(--hub-text-3);
  font-size: 14px; padding: 6px 14px; cursor: pointer;
  border-radius: var(--hub-radius);
  transition: color 150ms var(--hub-ease), background 150ms var(--hub-ease);
}
.tab-btn:hover { color: var(--hub-text); background: var(--hub-hover); }
.tab-btn.active { color: var(--hub-primary); background: rgba(0, 168, 255, 0.10); font-weight: 600; }

/* ---- 筛选条 ---- */
.filter-bar {
  display: flex; align-items: center; gap: 10px;
  padding: 12px 20px; border-bottom: 1px solid var(--hub-border-soft);
  flex-wrap: wrap;
}
.preset-group {
  display: flex; border: 1px solid var(--hub-border);
  border-radius: var(--hub-radius); overflow: hidden;
}
.preset-btn {
  background: transparent; border: none; color: var(--hub-text-3);
  font-size: 13px; padding: 6px 12px; cursor: pointer;
  border-right: 1px solid var(--hub-border);
  transition: background 150ms var(--hub-ease);
}
.preset-btn:last-child { border-right: none; }
.preset-btn:hover { background: var(--hub-hover); color: var(--hub-text); }
.preset-btn.active { background: rgba(0, 168, 255, 0.14); color: var(--hub-primary); font-weight: 600; }
.hub-input.date { width: 140px; padding: 5px 8px; font-size: 13px; }
.date-sep { color: var(--hub-text-4); }
.hub-input.sel { width: auto; min-width: 120px; padding: 5px 28px 5px 8px; font-size: 13px; }
.hub-input.sel.sm { min-width: 96px; }
.spacer { flex: 1; }

.content { padding: 16px 20px 32px; display: flex; flex-direction: column; gap: 16px; }

/* ---- KPI 卡 ---- */
.kpi-cards { display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; }
.kcard {
  background: var(--hub-panel); border: 1px solid var(--hub-border);
  border-radius: var(--hub-radius-lg); padding: 14px 16px 12px;
  display: flex; flex-direction: column; gap: 2px; position: relative;
}
.knum { font-size: 26px; font-weight: 600; color: var(--hub-text); line-height: 1.2; }
.knum.ng-num { color: var(--hub-ng-solid); }
.knum.warn-num { color: var(--hub-warn); }
.knum.dim { color: var(--hub-text-4); }
.dim-sep { font-style: normal; color: var(--hub-text-4); margin: 0 2px; }
.klabel { font-size: 12px; color: var(--hub-text-3); }
.kdelta { position: absolute; top: 14px; right: 14px; font-size: 12px; }
.kdelta.up { color: var(--hub-ok); }
.kdelta.down { color: var(--hub-ng); }
.kdelta.dim { color: var(--hub-text-4); }

/* ---- 面板 ---- */
.panel {
  background: var(--hub-panel); border: 1px solid var(--hub-border);
  border-radius: var(--hub-radius-lg); padding: 14px 16px;
}
.panel h2 {
  margin: 0 0 10px; font-size: 14px; font-weight: 600; color: var(--hub-text-3);
  display: flex; align-items: center; gap: 10px;
}
.hint { font-size: 12px; font-weight: 400; color: var(--hub-text-4); }
.chart-panel .trend-chart { height: 260px; }
.pareto-chart { height: 300px; }
.chart-empty {
  color: var(--hub-text-4); font-size: 13px; text-align: center;
  padding: 48px 0;
}
.two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; align-items: start; }
@media (max-width: 1100px) { .two-col { grid-template-columns: 1fr; } }

/* ---- 表格补充 ---- */
.table-panel { overflow-x: auto; }
.hub-table .num { text-align: right; }
.hub-table td.dim { color: var(--hub-text-3); }
.ng-num { color: var(--hub-ng); }
.warn-num { color: var(--hub-warn); }
.st-link { color: var(--hub-primary); }
.st-link:hover { color: var(--hub-primary-hover); }
.reason-cell { max-width: 320px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.spark-cell { width: 76px; }
.spark { width: 64px; height: 20px; display: block; }
.spark polyline {
  fill: none; stroke: var(--hub-primary); stroke-width: 1.5;
  vector-effect: non-scaling-stroke;
}

/* ---- 交叉表热力 ---- */
.matrix tbody tr { cursor: pointer; }
.matrix tbody tr.selected td { outline: 1px solid var(--hub-primary); outline-offset: -1px; }
.matrix .col-st { max-width: 96px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.matrix .heat { transition: background 150ms var(--hub-ease); }

.reason-tag { display: inline-flex; align-items: center; gap: 6px; }
.tag-x {
  background: transparent; border: none; color: var(--hub-text-3);
  cursor: pointer; font-size: 11px; padding: 0;
}
.tag-x:hover { color: var(--hub-text); }

.detail-head { display: flex; align-items: center; justify-content: space-between; }
.pager {
  display: flex; align-items: center; justify-content: center; gap: 14px;
  padding: 12px 0 4px;
}
</style>
