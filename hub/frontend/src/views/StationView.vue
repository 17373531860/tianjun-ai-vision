<template>
  <div class="station-page">
    <header class="hub-topbar">
      <router-link class="hub-back" :to="{ name: 'wall' }" data-test="back-to-wall">
        ← 返回检测集群
      </router-link>
      <h1 v-if="detail">{{ detail.node_name }} · {{ stationName }}</h1>
      <span v-if="detail" class="node-state">
        <span class="dot"
              :class="detail.status === 'online' && detail.stale ? 'stale' : detail.status" />
        <span :class="detail.status === 'offline' ? 'state-bad' : 'state-dim'">
          {{ stateLabel }}</span>
      </span>
      <span v-if="lock.locked" class="lock-tag tabular" :class="{ mine: lockMine }"
            data-test="lock-tag">
        🔒 {{ lockMine ? '我在操作' : `${lock.holder} 操作中` }}
        ({{ lock.expires_in_s }}s)
      </span>
      <span v-else-if="!canOps" class="hub-badge neutral readonly-tag">只读</span>
    </header>

    <main class="body" v-if="detail">
      <div class="left-col">
        <div class="viewer"
             :class="{ dim: detail.status === 'offline' || broken || detail.stale }">
          <img v-if="src" :src="src" alt="" data-test="station-live" />
          <!-- 离线/滞后遮罩在场时不再画"加载中"占位, 避免文字重叠 -->
          <div v-else-if="detail.status !== 'offline' && !detail.stale"
               class="placeholder">画面加载中…</div>
          <div v-if="detail.status === 'offline'" class="stale-mask">节点离线 — 保留最后画面</div>
          <div v-else-if="detail.stale" class="stale-mask lag">数据滞后</div>
        </div>

        <!-- 生产实况 (M7.5 值班读面: 边缘 /hub/live 投影, 2s 轮询) -->
        <section v-if="live" class="live-panel" data-test="live-panel">
          <div class="live-kpis">
            <div class="kpi"><i>合格 OK</i>
              <b class="tabular" data-test="live-ok">{{ live.counters?.ok ?? '—' }}</b></div>
            <div class="kpi"><i>不合格 NG</i>
              <b class="tabular ng" data-test="live-ng">{{ live.counters?.ng ?? '—' }}</b></div>
            <div class="kpi"><i>总数</i>
              <b class="tabular">{{ live.counters?.total ?? '—' }}</b></div>
            <div class="kpi"><i>当前周期</i>
              <b class="tabular">{{ live.cycle?.active
                ? fmtSec(live.cycle?.current_time) : '空闲' }}</b></div>
            <div class="kpi"><i>平均周期</i>
              <b class="tabular">{{ fmtSec(live.cycle?.average_time) }}</b></div>
            <div class="kpi"><i>上周期</i>
              <b class="tabular">{{ fmtSec(live.cycle?.last_time) }}</b></div>
          </div>

          <div class="live-cols">
            <div v-if="live.steps?.length" class="live-steps" data-test="live-steps">
              <h3>步骤进度</h3>
              <ul>
                <li v-for="s in live.steps.filter((x) => x.enabled)" :key="s.label"
                    :class="{ done: s.in_cycle, doing: s.inflight_s != null }">
                  <span class="st-dot" />
                  <span class="st-label">{{ s.label }}</span>
                  <span v-if="s.inflight_s != null" class="st-state tabular">
                    进行中 {{ s.inflight_s }}s</span>
                  <span v-else-if="s.in_cycle" class="st-state">本周期已完成</span>
                  <span class="st-count tabular">×{{ s.count }}</span>
                </li>
              </ul>
            </div>
            <div v-if="live.tracking" class="live-steps" data-test="live-tracking">
              <h3>清点实况</h3>
              <ul>
                <li><span class="st-label">画面内物品</span>
                  <span class="st-count tabular">{{ live.tracking.active_count ?? 0 }}</span></li>
                <li v-for="(n, cls) in live.tracking.class_counters || {}" :key="cls">
                  <span class="st-label">{{ cls }}</span>
                  <span class="st-count tabular">×{{ n }}</span></li>
              </ul>
            </div>
            <div v-if="live.recent_events?.length" class="live-events"
                 data-test="live-events">
              <h3>最近事件</h3>
              <ul>
                <li v-for="(e, i) in [...live.recent_events].reverse()" :key="i"
                    :class="e.kind === 'ng' ? 'ev-ng' : ''">
                  <span class="ev-time tabular">{{ fmtEvTime(e.ts) }}</span>
                  <span class="ev-name">{{ e.name }}</span>
                  <span v-if="e.reason" class="ev-reason" :title="e.reason">{{ e.reason }}</span>
                </li>
              </ul>
            </div>
          </div>
        </section>
      </div>

      <aside class="panel" data-test="station-panel">
        <h2>工位状态</h2>
        <dl>
          <template v-for="p in readableProps" :key="p.id">
            <dt>{{ propLabel(p.id) }}</dt>
            <dd :class="tone(p.id, reported[p.id])"
                :data-test="`field-${p.id}`">{{ formatProp(p, reported[p.id]) }}</dd>
          </template>
          <template v-if="!readableProps.length">
            <dt>检测状态</dt>
            <dd :class="reported.detecting ? '' : 'dim'" data-test="field-detecting">
              {{ reported.detecting ? '检测中' : '待机' }}</dd>
          </template>
        </dl>

        <template v-if="canOps && detail.actions?.length">
          <h2>远程操作</h2>
          <div class="ops" data-test="ops-panel">
            <button v-for="a in [...detail.actions, ...(detail.node_actions || [])]"
                    :key="a.id"
                    class="op-btn" :class="a.confirm"
                    :disabled="opBusy || detail.status !== 'online' || lockedByOther"
                    :data-test="`op-${a.id}`"
                    @click="askConfirm(a)">
              {{ a.label || a.id }}
            </button>
          </div>
          <p v-if="lockedByOther" class="lock-hint" data-test="lock-hint">
            {{ lock.holder }} 正在操作本工位
            <button v-if="canSteal" class="steal-btn" data-test="steal-lock"
                    @click="stealLock">夺锁</button>
          </p>
          <p v-if="opError" class="op-error" data-test="op-error">{{ opError }}</p>
          <p v-if="opNotice" class="op-notice" data-test="op-notice">{{ opNotice }}</p>
        </template>

        <h2>所属节点</h2>
        <dl>
          <dt>主机</dt><dd class="dim">{{ detail.hostname || '—' }}</dd>
          <dt>版本</dt><dd class="dim">v{{ detail.app_version }}</dd>
          <dt>License</dt>
          <dd :class="detail.license_state === 'valid' ? 'dim' : 'bad'">
            {{ detail.license_state === 'valid' ? '有效' : detail.license_state }}</dd>
          <dt>状态</dt>
          <dd :class="detail.status === 'online' ? '' : 'bad'"
              data-test="field-node-status">{{ stateLabel }}</dd>
        </dl>
      </aside>
    </main>
    <p v-else-if="loadError" class="hub-load-error">{{ loadError }}</p>
    <p v-else class="hub-empty">加载中…</p>

    <!-- 操作确认对话框 (danger 级动作强制走这里, RFC 15 确认梯度) -->
    <div v-if="confirming" class="hub-modal-mask" data-test="confirm-dialog">
      <div class="hub-modal dialog">
        <h2>{{ confirming.label || confirming.id }}</h2>
        <template v-if="confirming.id === 'activate_project'">
          <p>切换项目会影响整机全部工位（检测中会被边缘拒绝）。</p>
          <select v-model="selectedProject" class="hub-select"
                  data-test="project-select">
            <option disabled value="">选择目标项目…</option>
            <option v-for="p in projects" :key="p.id" :value="p.id">
              {{ p.name }}{{ p.is_active ? '（当前激活）' : '' }}
            </option>
          </select>
        </template>
        <p v-else>
          确认对 {{ detail.node_name }} · {{ stationName }} 执行
          「{{ confirming.label || confirming.id }}」？
        </p>
        <div class="hub-modal-actions">
          <button class="hub-btn ghost" data-test="confirm-cancel"
                  @click="confirming = null">取消</button>
          <button class="hub-btn" :class="{ danger: confirming.confirm === 'danger' }"
                  data-test="confirm-go"
                  :disabled="confirming.id === 'activate_project' && !selectedProject"
                  @click="runOp">确认执行</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import api from '../api'
import { authState } from '../auth'
import { useSnapshot } from '../composables/useSnapshot'

const LABELS = {
  detecting: '检测状态',
  active_project_id: '激活项目',
  logic_mode: '逻辑模式',
  is_running: '源运行',
  source_type: '视频源',
  fps_inference: '推理帧率',
}

const route = useRoute()
const nodeId = Number(route.params.nodeId)
const channelId = Number(route.params.channelId)

const detail = ref(null)
const loadError = ref('')
const confirming = ref(null)       // 待确认的 action 对象
const projects = ref([])
const selectedProject = ref('')
const opBusy = ref(false)
const opError = ref('')
const opNotice = ref('')
let timer = null

const stationName = computed(() =>
  detail.value?.display_name || `工位${channelId}`)
const stateLabel = computed(() => {
  const s = detail.value?.status
  if (s === 'offline') return '离线'
  if (s === 'online') return detail.value?.stale ? '数据滞后' : '在线'
  return '未知'
})
const reported = computed(() => detail.value?.reported || {})
const readableProps = computed(() =>
  (detail.value?.properties || []).filter((p) =>
    (p.access || ['read']).includes('read')))

const role = computed(() => authState.user?.role || 'operator')
const canOps = computed(() =>
  ['admin', 'director', 'engineer'].includes(role.value))
const canSteal = computed(() => ['admin', 'director'].includes(role.value))
const lock = computed(() => detail.value?.lock ||
  { locked: false, holder: null, expires_in_s: null })
const lockMine = computed(() =>
  lock.value.holder === authState.user?.username)
const lockedByOther = computed(() =>
  lock.value.locked && !lockMine.value)

const { src, broken } = useSnapshot(
  () => `/nodes/${nodeId}/stations/${channelId}/snapshot`, 500)

// ---- 生产实况 (M7.5): 下钻页开着才轮询, 关页即停 —— 边缘零常驻开销 ----
const live = ref(null)
let liveTimer = null
async function pollLive() {
  try {
    const r = await api.get(`/nodes/${nodeId}/stations/${channelId}/live`)
    live.value = r.data
  } catch { /* 离线/异常: 保留上一份读面, 遮罩已表达状态 */ }
}
function fmtSec(v) {
  if (v == null || v === 0) return '—'
  return `${Math.round(Number(v) * 10) / 10}s`
}
function fmtEvTime(ts) {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes())
    .padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`
}

function propLabel(id) { return LABELS[id] || id }

function formatProp(p, v) {
  if (v === undefined || v === null || v === '') return '—'
  if (p.id === 'detecting') return v ? '检测中' : '待机'
  if (p.id === 'fps_inference') return `${Math.round(Number(v) * 10) / 10} fps`
  if (p.format === 'bool') return v ? '是' : '否'
  return String(v)
}

function tone(id, v) {
  // ISA-101: 检测中是期望常态不上绿, 待机弱化
  if (id === 'detecting') return v ? '' : 'dim'
  return ''
}

async function askConfirm(action) {
  opError.value = ''
  opNotice.value = ''
  if (action.id === 'activate_project') {
    try {
      const r = await api.get(`/nodes/${nodeId}/projects`)
      projects.value = r.data.items || []
      selectedProject.value = ''
    } catch (e) {
      opError.value = e.response?.data?.detail || '拉取项目列表失败'
      return
    }
  }
  confirming.value = action
}

async function runOp() {
  const action = confirming.value
  confirming.value = null
  opBusy.value = true
  opError.value = ''
  try {
    const body = { action: action.id }
    if (action.id === 'activate_project') body.project_id = selectedProject.value
    const r = await api.post(
      `/nodes/${nodeId}/stations/${channelId}/ops`, body)
    opNotice.value = r.data.message || '已执行'
    await refresh()
  } catch (e) {
    opError.value = e.response?.data?.detail || '操作失败（枢纽或边缘无响应）'
  } finally {
    opBusy.value = false
  }
}

async function stealLock() {
  opError.value = ''
  try {
    await api.post(`/nodes/${nodeId}/stations/${channelId}/lock`,
      { steal: true })
    await refresh()
  } catch (e) {
    opError.value = e.response?.data?.detail || '夺锁失败'
  }
}

async function refresh() {
  try {
    const r = await api.get(`/nodes/${nodeId}/stations/${channelId}`)
    detail.value = r.data
    loadError.value = ''
  } catch (e) {
    if (e.response?.status === 401) return
    if (e.response?.status === 404) loadError.value = '工位不存在或已被移除'
    else loadError.value = '枢纽连接失败，正在重试…'
  }
}

onMounted(() => {
  refresh()
  timer = setInterval(refresh, 2000)
  pollLive()
  liveTimer = setInterval(pollLive, 2000)
})
onBeforeUnmount(() => {
  clearInterval(timer)
  clearInterval(liveTimer)
})
</script>

<style scoped>
.station-page { min-height: 100%; display: flex; flex-direction: column; }
.node-state { display: inline-flex; align-items: center; gap: 6px; font-size: 13px; }
.state-dim { color: var(--hub-text-3); }
.state-bad { color: var(--hub-ng); }
.readonly-tag { margin-left: auto; }
/* 他人持锁=冲突信号(红 muted); 我持锁=状态说明(主色 muted, 绿只作回执) */
.lock-tag {
  margin-left: auto; padding: 2px 10px; border-radius: var(--hub-radius-sm);
  background: var(--hub-ng-bg); color: var(--hub-ng); font-size: 12px;
  border: 1px solid var(--hub-ng-bd);
}
.lock-tag.mine {
  background: rgba(0, 168, 255, 0.12); color: #7dd3fc;
  border-color: rgba(0, 168, 255, 0.35);
}

.body { display: flex; gap: 16px; padding: 16px 20px; flex: 1; }
.left-col { flex: 1; display: flex; flex-direction: column; gap: 16px; min-width: 0; }
.viewer {
  position: relative; background: #000;
  border-radius: var(--hub-radius-lg); overflow: hidden;
  border: 1px solid var(--hub-border); min-height: 420px;
  display: flex; align-items: center; justify-content: center;
}

/* ---- 生产实况 (M7.5) ---- */
.live-panel {
  background: var(--hub-panel); border: 1px solid var(--hub-border);
  border-radius: var(--hub-radius-lg); padding: 14px 16px;
  display: flex; flex-direction: column; gap: 14px;
}
.live-kpis { display: flex; gap: 24px; flex-wrap: wrap; }
.kpi { display: flex; flex-direction: column; gap: 2px; min-width: 72px; }
.kpi i { font-style: normal; font-size: 12px; color: var(--hub-text-3); }
.kpi b { font-size: 20px; font-weight: 600; color: var(--hub-text); }
.kpi b.ng { color: var(--hub-ng); }
.live-cols { display: flex; gap: 24px; flex-wrap: wrap; }
.live-steps, .live-events { flex: 1; min-width: 220px; }
.live-panel h3 {
  font-size: 12px; font-weight: 600; color: var(--hub-text-3);
  text-transform: none; margin-bottom: 6px;
}
.live-steps ul, .live-events ul { list-style: none; display: flex;
  flex-direction: column; gap: 4px; }
.live-steps li {
  display: flex; align-items: center; gap: 8px; font-size: 13px;
  color: var(--hub-text-2); padding: 3px 0;
}
.st-dot {
  width: 8px; height: 8px; border-radius: 50%; flex: none;
  background: var(--hub-border);
}
.live-steps li.done .st-dot { background: var(--hub-ok); }
.live-steps li.doing .st-dot {
  background: var(--hub-primary); animation: livePulse 1.2s infinite;
}
@keyframes livePulse { 50% { opacity: 0.4; } }
.st-label { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.st-state { font-size: 12px; color: var(--hub-text-3); }
.live-steps li.doing .st-state { color: var(--hub-primary); }
.st-count { font-size: 12px; color: var(--hub-text-3); }
.live-events li {
  display: flex; gap: 8px; align-items: baseline; font-size: 13px;
  color: var(--hub-text-2); padding: 3px 0;
}
.live-events li.ev-ng .ev-name { color: var(--hub-ng); }
.ev-time { font-size: 12px; color: var(--hub-text-3); flex: none; }
.ev-reason {
  font-size: 12px; color: var(--hub-text-3); overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap; max-width: 45%;
}
.viewer img { width: 100%; height: 100%; object-fit: contain; }
.viewer.dim img { filter: grayscale(1) brightness(0.55); }
.placeholder { color: var(--hub-text-4); }
.stale-mask {
  position: absolute; inset: 0; display: flex; align-items: center;
  justify-content: center; color: var(--hub-ng); background: rgba(0, 0, 0, .45);
}
.stale-mask.lag { color: var(--hub-warn); }

.panel {
  width: 300px; background: var(--hub-panel); border: 1px solid var(--hub-border);
  border-radius: var(--hub-radius-lg); padding: 16px;
  align-self: flex-start;
}
.panel h2 {
  font-size: 12px; color: var(--hub-text-3); font-weight: 500; margin: 18px 0 10px;
  padding-bottom: 6px; border-bottom: 1px solid var(--hub-border-soft);
}
.panel h2:first-child { margin-top: 0; }
dl { display: grid; grid-template-columns: auto 1fr; gap: 8px 14px; font-size: 13px; }
dt { color: var(--hub-text-3); }
dd { color: var(--hub-text); text-align: right; }
dd.ok { color: var(--hub-ok); }
dd.dim { color: var(--hub-text-3); }
dd.bad { color: var(--hub-ng); }

/* 操作按钮: 等宽两列网格, normal 蓝边 / danger 红边, 大小节奏统一 */
.ops { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.op-btn {
  padding: 8px 10px; border-radius: var(--hub-radius);
  border: 1px solid var(--hub-border);
  background: transparent; color: var(--hub-text-2);
  cursor: pointer; font-size: 13px; white-space: nowrap;
  transition: border-color .15s, color .15s, background .15s;
}
.op-btn:hover:not(:disabled) {
  border-color: var(--hub-primary); color: var(--hub-primary);
}
.op-btn.danger { border-color: var(--hub-ng-bd); color: var(--hub-ng); }
.op-btn.danger:hover:not(:disabled) {
  border-color: var(--hub-ng); color: var(--hub-ng); background: var(--hub-ng-bg);
}
.op-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.lock-hint { margin-top: 10px; color: var(--hub-ng); font-size: 12px; }
.steal-btn {
  margin-left: 8px; padding: 2px 10px; border-radius: var(--hub-radius);
  border: 1px solid var(--hub-ng-bd); background: transparent; color: var(--hub-ng);
  cursor: pointer; font-size: 12px;
}
.steal-btn:hover { border-color: var(--hub-ng); color: var(--hub-ng); }
.op-error { margin-top: 10px; color: var(--hub-ng); font-size: 12px; }
.op-notice { margin-top: 10px; color: var(--hub-ok); font-size: 12px; }

.dialog p { color: var(--hub-text-3); font-size: 13px; }
</style>
