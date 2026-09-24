<template>
  <div class="wall-page">
    <header class="hub-topbar">
      <div class="brand">
        <span class="logo">◈</span>
        <h1>集中管控枢纽 · 检测集群</h1>
      </div>
      <nav class="actions">
        <template v-if="!tvMode">
          <button class="hub-link" data-test="data-link"
                  @click="router.push({ name: 'data' })">数据中心</button>
          <button class="hub-link alarm-link" data-test="alarms-link"
                  @click="router.push({ name: 'alarms' })">
            报警中心
            <span v-if="unackedCount" class="badge" data-test="alarm-badge">
              {{ unackedCount > 99 ? '99+' : unackedCount }}</span>
          </button>
          <button v-if="canOps" class="hub-link" data-test="batch-open"
                  @click="showBatch = true">批量操作</button>
          <button v-if="canManage" class="hub-link" data-test="users-link"
                  @click="router.push({ name: 'users' })">用户管理</button>
          <button v-if="canManage || isDirector" class="hub-link" data-test="nodes-link"
                  @click="router.push({ name: 'nodes' })">节点与审计</button>
          <button v-if="canManage" class="hub-link" data-test="enroll-open"
                  @click="showEnroll = true">纳管节点</button>
          <span class="divider" />
        </template>
        <!-- 电视墙模式仍保留报警计数 (甩眼核心信息) -->
        <span v-else-if="unackedCount" class="badge tv-badge" data-test="alarm-badge">
          {{ unackedCount > 99 ? '99+' : unackedCount }} 未处理</span>
        <button class="hub-link muted" data-test="sound-toggle"
                :title="soundOn ? '关闭新报警提示音' : '开启新报警提示音'"
                @click="soundOn = !soundOn">{{ soundOn ? '🔔' : '🔕' }}</button>
        <button class="hub-link muted" data-test="tv-toggle" @click="toggleTv">
          {{ tvMode ? '退出电视墙' : '电视墙模式' }}</button>
        <span class="clock tabular" :class="{ big: tvMode }" data-test="wall-clock">
          <b>{{ clockTime }}</b><i>{{ clockDate }}</i></span>
        <template v-if="!tvMode">
          <span class="divider" />
          <span class="whoami" :title="user?.username">
            {{ user?.display_name || user?.username }}</span>
          <button class="hub-link muted" data-test="pwd-open"
                  @click="showPwd = true">改密</button>
          <button class="hub-link muted" data-test="logout-btn"
                  @click="onLogout">退出</button>
        </template>
      </nav>
    </header>

    <main class="content">
      <!-- KPI Stat 条: 大数字, 异常才上色 (ISA-101 正常态安静 / Grafana Stat) -->
      <div v-if="wall" class="kpi-bar tabular" data-test="wall-totals">
        <div class="kpi" :class="{ alert: unackedCount }">
          <span class="kpi-num">{{ unackedCount }}</span>
          <span class="kpi-label">未处理报警</span>
        </div>
        <div class="kpi" :class="{ alert: wall.totals.offline_nodes }">
          <span class="kpi-num">{{ wall.totals.offline_nodes }}</span>
          <span class="kpi-label">离线节点</span>
        </div>
        <div class="kpi" :class="{ warn: wall.totals.stale_nodes }">
          <span class="kpi-num">{{ wall.totals.stale_nodes }}</span>
          <span class="kpi-label">数据滞后</span>
        </div>
        <div class="kpi">
          <span class="kpi-num">{{ wall.totals.detecting_stations }}</span>
          <span class="kpi-label">在检工位</span>
        </div>
        <div class="kpi">
          <span class="kpi-num">{{ wall.totals.nodes }} <i>·</i> {{ wall.totals.stations }}</span>
          <span class="kpi-label">节点 · 工位</span>
        </div>
      </div>

      <!-- NG 置顶条: 最新未确认事件甩眼可见 (报警上墙惯例), 点击直达工位 -->
      <ul v-if="recentUnacked.length" class="ng-ticker" data-test="ng-ticker">
        <li v-for="ev in recentUnacked" :key="ev.id" data-test="ticker-item"
            role="button" tabindex="0"
            @click="router.push({ name: 'station',
                                  params: { nodeId: ev.node_id, channelId: ev.channel_id } })">
          <span class="tk-time tabular">{{ tickerTime(ev.ts) }}</span>
          <span class="tk-where">{{ ev.node_name }} · 工位{{ ev.channel_id }}</span>
          <span class="tk-reason">{{ ev.reason || ev.event_name || 'NG' }}</span>
          <span class="tk-go">处理 ›</span>
        </li>
      </ul>

      <ul v-if="wall?.alerts?.length" class="alerts" data-test="wall-alerts">
        <li v-for="(a, i) in wall.alerts" :key="i"
            :class="a.kind" :data-test="`alert-${a.kind}`">{{ a.message }}</li>
      </ul>
      <p v-if="loadError" class="hub-load-error" data-test="wall-error">
        {{ loadError }}
      </p>
      <div v-else-if="wall && !wall.nodes.length" class="hub-empty" data-test="wall-empty">
        <p>尚未纳管任何边缘节点</p>
        <button v-if="canManage" class="hub-btn empty-cta" data-test="empty-enroll"
                @click="showEnroll = true">纳管节点</button>
      </div>

      <!-- 视图模式: 按节点分组卡 / 全景网格 (全部工位摊平, N×N 可选) -->
      <div v-if="wall && wall.nodes.length" class="view-bar">
        <div class="seg" data-test="view-modes">
          <button class="seg-btn" :class="{ active: viewMode === 'nodes' }"
                  data-test="view-nodes" @click="viewMode = 'nodes'">按节点</button>
          <button class="seg-btn" :class="{ active: viewMode === 'grid' }"
                  data-test="view-grid" @click="viewMode = 'grid'">全景网格</button>
        </div>
        <div v-if="viewMode === 'grid'" class="seg" data-test="grid-sizes">
          <button v-for="n in GRID_SIZES" :key="n" class="seg-btn"
                  :class="{ active: gridCols === n }"
                  :data-test="`grid-${n}`" @click="gridCols = n">{{ n }}×{{ n }}</button>
        </div>
        <div v-if="viewMode === 'grid' && pageCount > 1" class="seg"
             data-test="grid-pager">
          <button class="seg-btn" data-test="grid-prev"
                  :disabled="gridPage === 0" @click="manualPage(-1)">‹</button>
          <span class="seg-info tabular" data-test="grid-page-info">
            {{ gridPage + 1 }} / {{ pageCount }}</span>
          <button class="seg-btn" data-test="grid-next"
                  :disabled="gridPage >= pageCount - 1" @click="manualPage(1)">›</button>
        </div>
        <!-- 轮巡: 无人值守自动翻页 (海康轮巡/BI cycle 惯例, 默认 15s) -->
        <div v-if="viewMode === 'grid' && pageCount > 1" class="seg"
             data-test="cycle-ctrl">
          <button class="seg-btn" :class="{ active: cycleOn }" data-test="cycle-toggle"
                  @click="cycleOn = !cycleOn">
            {{ cycleOn ? '轮巡中' : '轮巡' }}</button>
          <select v-if="cycleOn" v-model.number="cycleSec" class="seg-select tabular"
                  data-test="cycle-sec" title="轮巡间隔">
            <option v-for="s in CYCLE_SECS" :key="s" :value="s">{{ s }}s</option>
          </select>
        </div>
        <span v-if="viewMode === 'grid'" class="view-hint">
          {{ flatStations.length }} 个工位 · 点击画面放大</span>
      </div>

      <!-- 形态 A: 按节点分组卡 -->
      <template v-if="viewMode === 'nodes'">
        <section v-for="g in sortedGroups" :key="g.name"
                 class="node-card"
                 :class="{ 'sev-offline': g.offline, 'sev-stale': !g.offline && g.stale }"
                 :data-test="`group-card-${g.name}`">
          <div class="node-head">
            <span class="dot" :class="g.offline ? 'offline' : (g.stale ? 'stale' : g.status)" />
            <span class="node-name">{{ g.name }}</span>
            <span class="node-count">{{ g.stations.length }} 工位</span>
            <span class="node-sub">{{ g.subtitle }}</span>
            <span v-if="!g.offline && g.stale" class="hub-badge warn">数据滞后</span>
            <span v-if="g.offline" class="hub-badge ng">离线</span>
          </div>
          <div class="station-grid">
            <StationTile v-for="st in g.stations"
                         :key="`${st.nodeId}-${st.channel_id}`"
                         :node-id="st.nodeId" :station="st"
                         :offline="st.offline"
                         @open="zoomSt = { ...st, nodeName: g.name }" />
          </div>
        </section>
      </template>

      <!-- 形态 B: 全景网格 (跨节点摊平, 类主程序超多工位总览):
           N×N = 每页 N*N 格 (与主程序 9×9 语义对齐), 超出翻页 -->
      <div v-else class="flat-grid" :class="{ dense: gridCols >= 6 }"
           :style="{ gridTemplateColumns: `repeat(${gridCols}, 1fr)` }"
           data-test="flat-grid">
        <StationTile v-for="st in pagedStations"
                     :key="`${st.nodeId}-${st.channel_id}`"
                     :node-id="st.nodeId" :station="st"
                     :offline="st.offline" :node-name="st.nodeName"
                     :dense="gridCols >= 6"
                     @open="zoomSt = st" />
      </div>
    </main>

    <!-- 单格放大覆盖层 (2fps 提帧, Esc/点罩退出) -->
    <StationZoom v-if="zoomSt" :node-id="zoomSt.nodeId" :station="zoomSt"
                 :node-name="zoomSt.nodeName || ''" :offline="zoomSt.offline"
                 @close="zoomSt = null" />

    <div v-if="showEnroll" class="hub-modal-mask" data-test="enroll-dialog"
         @click.self="showEnroll = false">
      <form class="hub-modal" @submit.prevent="onEnroll">
        <h2>纳管边缘节点</h2>
        <label>显示名<input v-model="enroll.name" class="hub-input" data-test="enroll-name"
                           placeholder="包装线-1#" required /></label>
        <label>边缘地址<input v-model="enroll.base_url" class="hub-input" data-test="enroll-url"
                             placeholder="http://192.168.1.10:8001" required /></label>
        <label>API Key<input v-model="enroll.api_key" class="hub-input" data-test="enroll-key"
                             placeholder="tk_…" required /></label>
        <p v-if="enrollError" class="hub-error" data-test="enroll-error">{{ enrollError }}</p>
        <div class="hub-modal-actions">
          <button type="button" class="hub-btn ghost" data-test="enroll-cancel"
                  @click="showEnroll = false">取消</button>
          <button type="submit" class="hub-btn" data-test="enroll-submit"
                  :disabled="enrollLoading">
            {{ enrollLoading ? '连接中…' : '纳管' }}
          </button>
        </div>
      </form>
    </div>

    <ChangePasswordDialog v-if="showPwd" @close="showPwd = false" />
    <BatchOpsDialog v-if="showBatch" :nodes="wall?.nodes || []"
                    @close="showBatch = false" @done="refresh" />
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api from '../api'
import { authState, logout } from '../auth'
import BatchOpsDialog from '../components/BatchOpsDialog.vue'
import ChangePasswordDialog from '../components/ChangePasswordDialog.vue'
import StationTile from '../components/StationTile.vue'
import StationZoom from '../components/StationZoom.vue'
import { useWsHint } from '../composables/useWsHint'

const router = useRouter()
const route = useRoute()
const user = authState.user
const canManage = computed(() => authState.user?.role === 'admin')
const isDirector = computed(() => authState.user?.role === 'director')
const canOps = computed(() =>
  ['admin', 'director', 'engineer'].includes(authState.user?.role))
const showBatch = ref(false)
const wall = ref(null)

// ---- 视图模式 (localStorage 持久化, 电视墙场景重启保持) ----
const GRID_SIZES = [2, 3, 4, 6, 9]
const viewMode = ref(localStorage.getItem('hub_wall_view') || 'nodes')
const gridCols = ref(Number(localStorage.getItem('hub_wall_grid')) || 3)
if (!GRID_SIZES.includes(gridCols.value)) gridCols.value = 3
watch(viewMode, (v) => localStorage.setItem('hub_wall_view', v))
watch(gridCols, (v) => localStorage.setItem('hub_wall_grid', String(v)))
const unackedCount = ref(0)
const recentUnacked = ref([])   // NG 置顶条: 最新未确认事件 (最多 3 条)
const loadError = ref('')
const showEnroll = ref(false)
const showPwd = ref(false)
const enrollLoading = ref(false)
const enrollError = ref('')
const enroll = reactive({ name: '', base_url: '', api_key: '' })
const zoomSt = ref(null)        // 单格放大目标 (null=关闭)
let timer = null

// M8 WS 加速: 工位状态/新 NG 一变就立即 refresh (轮询兜底不变)
useWsHint(() => refresh())

// ---- 时钟 (值班墙对班次/工时, Andon 惯例) ----
const clockTime = ref('')
const clockDate = ref('')
let clockTimer = null
function tickClock() {
  const d = new Date()
  const p = (n) => String(n).padStart(2, '0')
  clockTime.value = `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
  const week = '日一二三四五六'[d.getDay()]
  clockDate.value = `${p(d.getMonth() + 1)}-${p(d.getDate())} 周${week}`
}

// ---- 电视墙模式: 隐藏管理入口 + 全屏 + Wake Lock 防休眠 ----
// 深链 #/?tv=1 直接进墙态 (kiosk 浏览器负责全屏, Fullscreen API 需手势无法自动)
const tvMode = ref(route.query.tv === '1')
let wakeLock = null
async function acquireWakeLock() {
  try { wakeLock = await navigator.wakeLock?.request('screen') } catch { /* 不支持则靠 OS 设置 */ }
}
function releaseWakeLock() {
  try { wakeLock?.release() } catch { /* noop */ }
  wakeLock = null
}
function onVisibility() {
  // 切回前台重新拿锁 (Wake Lock 页面隐藏即失效)
  if (tvMode.value && document.visibilityState === 'visible') acquireWakeLock()
}
async function toggleTv() {
  tvMode.value = !tvMode.value
  if (tvMode.value) {
    try { await document.documentElement.requestFullscreen() } catch { /* iframe/权限受限 */ }
  } else if (document.fullscreenElement) {
    try { await document.exitFullscreen() } catch { /* noop */ }
  }
}
function onFsChange() {
  // 浏览器 Esc 退全屏 = 退出电视墙 (Genetec F11 语义)
  if (!document.fullscreenElement && tvMode.value && !route.query.tv) tvMode.value = false
}
watch(tvMode, (v) => {
  if (v) acquireWakeLock()
  else releaseWakeLock()
}, { immediate: true })

// ---- 轮巡: 全景网格自动翻页 (无人值守标配, 默认 15s) ----
const CYCLE_SECS = [5, 10, 15, 30, 60]
const cycleOn = ref(localStorage.getItem('hub_wall_cycle') === '1')
const cycleSec = ref(Number(localStorage.getItem('hub_wall_cycle_s')) || 15)
if (!CYCLE_SECS.includes(cycleSec.value)) cycleSec.value = 15
watch(cycleOn, (v) => localStorage.setItem('hub_wall_cycle', v ? '1' : '0'))
watch(cycleSec, (v) => localStorage.setItem('hub_wall_cycle_s', String(v)))
let cycleTimer = null
function restartCycle() {
  clearInterval(cycleTimer)
  cycleTimer = null
  if (cycleOn.value && viewMode.value === 'grid' && pageCount.value > 1) {
    cycleTimer = setInterval(() => {
      gridPage.value = (gridPage.value + 1) % pageCount.value
    }, cycleSec.value * 1000)
  }
}
// (watch 注册在分页声明之后, 见下方 —— pageCount 属 const, 先引用会 TDZ)
function manualPage(delta) {
  gridPage.value += delta
  restartCycle() // 人工翻页重置轮巡计时 (避免刚翻就被自动翻走)
}

// ---- 提示音: 新未确认报警短蜂鸣 (默认关; 最短间隔 10s 防疲劳) ----
const soundOn = ref(localStorage.getItem('hub_wall_sound') === '1')
watch(soundOn, (v) => localStorage.setItem('hub_wall_sound', v ? '1' : '0'))
let lastBeepAt = 0
let audioCtx = null
function beep() {
  const now = Date.now()
  if (now - lastBeepAt < 10000) return
  lastBeepAt = now
  try {
    audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)()
    for (const t0 of [0, 0.22]) { // 两短音, 克制不刺耳
      const osc = audioCtx.createOscillator()
      const gain = audioCtx.createGain()
      osc.frequency.value = 880
      gain.gain.setValueAtTime(0.12, audioCtx.currentTime + t0)
      gain.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + t0 + 0.15)
      osc.connect(gain).connect(audioCtx.destination)
      osc.start(audioCtx.currentTime + t0)
      osc.stop(audioCtx.currentTime + t0 + 0.16)
    }
  } catch { /* 无音频设备 */ }
}

function tickerTime(ts) {
  if (!ts) return ''
  const d = new Date(ts)
  const p = (n) => String(n).padStart(2, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

const groups = computed(() => {
  const nodes = wall.value?.nodes || []
  const map = new Map()
  for (const node of nodes) {
    for (const st of node.stations || []) {
      const name = st.group_name || node.name
      if (!map.has(name)) {
        map.set(name, {
          name,
          subtitle: node.hostname ? `${node.hostname} · v${node.app_version || ''}` : '',
          status: node.status,
          stale: node.stale,
          offline: node.status === 'offline',
          stations: [],
        })
      }
      const g = map.get(name)
      if (node.status === 'offline') g.offline = true
      if (node.stale) g.stale = true
      g.stations.push({
        ...st,
        nodeId: node.id,
        offline: node.status === 'offline',
      })
    }
  }
  return [...map.values()]
})

// 异常置顶: 离线 > 滞后 > 正常 (ISA-101 扫描路径)
const sortedGroups = computed(() =>
  [...groups.value].sort((a, b) => {
    const sev = (g) => (g.offline ? 2 : g.stale ? 1 : 0)
    return sev(b) - sev(a)
  }))

// 全景网格: 跨节点摊平, 保持节点原始顺序 (电视墙上位置固定不跳动,
// 不做异常置顶排序 —— 值班员靠"格子位置"记工位, 乱序反而找不到)
const flatStations = computed(() => {
  const out = []
  for (const node of wall.value?.nodes || []) {
    for (const st of node.stations || []) {
      out.push({
        ...st,
        nodeId: node.id,
        nodeName: node.name,
        offline: node.status === 'offline',
      })
    }
  }
  return out
})

// 分页: N×N = 每页 N*N 格 (与主程序超多工位总览语义一致)
const gridPage = ref(0)
const pageCount = computed(() =>
  Math.max(1, Math.ceil(flatStations.value.length / (gridCols.value ** 2))))
const pagedStations = computed(() => {
  const per = gridCols.value ** 2
  return flatStations.value.slice(gridPage.value * per, (gridPage.value + 1) * per)
})
// 切规格/工位数变化时页码防越界
watch([gridCols, pageCount], () => {
  if (gridPage.value >= pageCount.value) gridPage.value = pageCount.value - 1
})
// 轮巡随开关/间隔/视图/页数变化重排 (声明在 pageCount 之后, 避免 TDZ)
watch([cycleOn, cycleSec, viewMode, pageCount], restartCycle)

async function refresh() {
  try {
    const r = await api.get('/wall')
    wall.value = r.data
    loadError.value = ''
  } catch (e) {
    if (e.response?.status === 401) return
    loadError.value = '枢纽连接失败，正在重试…'
    return
  }
  try {
    // 徽标计数 + NG 置顶条 (最新 3 条未确认) 一次拉齐
    const r = await api.get('/events', { params: { limit: 3, unacked_only: true } })
    const prev = unackedCount.value
    unackedCount.value = r.data.unacked
    recentUnacked.value = r.data.items || []
    // 新增未确认才响 (确认减少不响); 首轮 prev=0 且历史堆积不响
    if (soundOn.value && prev > 0 && r.data.unacked > prev) beep()
    else if (soundOn.value && prev === 0 && r.data.unacked > 0 && hasRefreshed) beep()
    hasRefreshed = true
  } catch { /* 徽标失败不影响墙 */ }
}
let hasRefreshed = false

async function onEnroll() {
  enrollError.value = ''
  enrollLoading.value = true
  try {
    await api.post('/nodes', {
      name: enroll.name, base_url: enroll.base_url, api_key: enroll.api_key,
    })
    showEnroll.value = false
    enroll.name = enroll.base_url = enroll.api_key = ''
    await refresh()
  } catch (e) {
    enrollError.value = e.response?.data?.detail || '纳管失败'
  } finally {
    enrollLoading.value = false
  }
}

async function onLogout() {
  await logout()
  router.push({ name: 'login' })
}

onMounted(() => {
  refresh()
  timer = setInterval(refresh, 2000)
  tickClock()
  clockTimer = setInterval(tickClock, 1000)
  restartCycle()
  document.addEventListener('fullscreenchange', onFsChange)
  document.addEventListener('visibilitychange', onVisibility)
})
onBeforeUnmount(() => {
  clearInterval(timer)
  clearInterval(clockTimer)
  clearInterval(cycleTimer)
  document.removeEventListener('fullscreenchange', onFsChange)
  document.removeEventListener('visibilitychange', onVisibility)
  releaseWakeLock()
})
</script>

<style scoped>
.wall-page { min-height: 100%; display: flex; flex-direction: column; }
.brand { display: flex; align-items: center; gap: 10px; flex-shrink: 0; }
.logo { color: var(--hub-primary); font-size: 20px; }

/* 右侧: 功能入口组 | 分隔线 | 账号组 (弱化) */
.actions {
  margin-left: auto; display: flex; gap: 14px; align-items: center;
  flex-shrink: 0; white-space: nowrap;
}
.divider { width: 1px; height: 16px; background: var(--hub-border); }
.whoami { color: var(--hub-text-3); font-size: 13px; }
.hub-link.muted { color: var(--hub-text-3); font-size: 13px; }
.hub-link.muted:hover { color: var(--hub-text); }
.alarm-link { position: relative; }
.badge {
  background: #b91c1c; color: #fff; border-radius: 9px; font-size: 11px;
  padding: 0 6px; margin-left: 4px; display: inline-block; min-width: 18px;
  text-align: center; line-height: 17px;
  font-variant-numeric: tabular-nums;
}
.tv-badge { font-size: 13px; line-height: 22px; padding: 0 10px; border-radius: 11px; }

/* 时钟: 值班墙对班次; 电视墙模式放大 (Andon 远距可读) */
.clock { display: inline-flex; align-items: baseline; gap: 8px; }
.clock b { color: var(--hub-text); font-size: 15px; font-weight: 600; }
.clock i { color: var(--hub-text-3); font-size: 12px; font-style: normal; }
.clock.big b { font-size: 22px; }
.clock.big i { font-size: 13px; }

/* NG 置顶条: 未确认事件甩眼可见, 整行可点直达工位 */
.ng-ticker { list-style: none; display: flex; flex-direction: column; gap: 6px; }
.ng-ticker li {
  display: flex; align-items: center; gap: 12px; padding: 8px 12px;
  background: var(--hub-ng-bg); border: 1px solid var(--hub-ng-bd);
  border-radius: var(--hub-radius); font-size: 13px; cursor: pointer;
  transition: border-color 150ms var(--hub-ease);
}
.ng-ticker li:hover { border-color: var(--hub-ng-solid); }
.tk-time { color: var(--hub-ng); font-weight: 600; }
.tk-where { color: var(--hub-text); flex-shrink: 0; }
.tk-reason {
  color: var(--hub-text-2); overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; flex: 1;
}
.tk-go { color: var(--hub-ng); flex-shrink: 0; font-size: 12px; }

.content { padding: 16px 20px; display: flex; flex-direction: column; gap: 16px; }

/* KPI Stat 条: 数字 22px/600, 异常才上色 (正常态安静) */
.kpi-bar { display: flex; gap: 16px; flex-wrap: wrap; }
.kpi {
  flex: 1; min-width: 140px; padding: 12px 16px;
  background: var(--hub-panel); border: 1px solid var(--hub-border);
  border-radius: var(--hub-radius-lg);
  display: flex; flex-direction: column; gap: 2px;
}
.kpi-num { font-size: 22px; font-weight: 600; color: var(--hub-text); line-height: 28px; }
.kpi-num i { font-style: normal; color: var(--hub-text-4); font-weight: 400; }
.kpi-label { font-size: 12px; color: var(--hub-text-3); }
.kpi.alert { border-color: var(--hub-ng-bd); background: var(--hub-ng-bg); }
.kpi.alert .kpi-num { color: var(--hub-ng); }
.kpi.warn { border-color: var(--hub-warn-bd); background: var(--hub-warn-bg); }
.kpi.warn .kpi-num { color: var(--hub-warn); }

/* 节点卡: 左 3px 最差状态色条 (正常时无色条, 异常才可见) */
.node-card {
  background: var(--hub-panel); border: 1px solid var(--hub-border);
  border-radius: var(--hub-radius-lg); padding: 14px 16px;
}
.node-card.sev-offline { box-shadow: inset 3px 0 0 var(--hub-ng-solid); }
.node-card.sev-stale { box-shadow: inset 3px 0 0 var(--hub-warn); }
.node-head { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
.node-name { color: var(--hub-text); font-weight: 600; }
.node-count { color: var(--hub-text-3); font-size: 12px; }
.node-sub { color: var(--hub-text-4); font-size: 12px; }

.alerts { list-style: none; display: flex; flex-direction: column; gap: 6px; }
.alerts li {
  padding: 8px 12px; border-radius: var(--hub-radius); font-size: 13px;
}
.alerts .offline, .alerts .license {
  background: var(--hub-ng-bg); color: var(--hub-ng);
  border: 1px solid var(--hub-ng-bd);
}
.alerts .stale {
  background: var(--hub-warn-bg); color: var(--hub-warn);
  border: 1px solid var(--hub-warn-bd);
}

.empty-cta { margin-top: 14px; }

.station-grid {
  display: grid; gap: 12px;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
}

/* ---- 视图模式工具行 ---- */
.view-bar { display: flex; align-items: center; gap: 12px; }
.seg {
  display: flex; border: 1px solid var(--hub-border);
  border-radius: var(--hub-radius); overflow: hidden;
}
.seg-btn {
  background: transparent; border: none; color: var(--hub-text-3);
  font-size: 13px; padding: 6px 14px; cursor: pointer;
  border-right: 1px solid var(--hub-border);
  transition: background 150ms var(--hub-ease), color 150ms var(--hub-ease);
  font-variant-numeric: tabular-nums;
}
.seg-btn:last-child { border-right: none; }
.seg-btn:hover { background: var(--hub-hover); color: var(--hub-text); }
.seg-btn.active {
  background: rgba(0, 168, 255, 0.14); color: var(--hub-primary); font-weight: 600;
}
.seg-btn:disabled { color: var(--hub-text-4); cursor: default; background: transparent; }
.seg-info {
  color: var(--hub-text-3); font-size: 13px; padding: 6px 10px;
  border-right: 1px solid var(--hub-border);
  display: inline-flex; align-items: center;
}
.seg > .seg-info:last-child { border-right: none; }
.seg-select {
  background: transparent; border: none; color: var(--hub-text-2);
  font-size: 13px; padding: 6px 8px; cursor: pointer; outline: none;
}
.seg-select option { background: var(--hub-panel); color: var(--hub-text); }
.view-hint { color: var(--hub-text-4); font-size: 12px; }

/* ---- 全景网格: N 列等宽, 行数自适应 ---- */
.flat-grid { display: grid; gap: 12px; }
.flat-grid.dense { gap: 6px; }
</style>
