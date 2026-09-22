<template>
  <div class="station-page">
    <header class="hub-topbar">
      <router-link class="hub-back" :to="{ name: 'wall' }" data-test="back-to-wall">
        ← 返回监控墙
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
      <div class="viewer" :class="{ dim: detail.status === 'offline' || broken || detail.stale }">
        <img v-if="src" :src="src" alt="" data-test="station-live" />
        <!-- 离线/滞后遮罩在场时不再画"加载中"占位, 避免文字重叠 -->
        <div v-else-if="detail.status !== 'offline' && !detail.stale"
             class="placeholder">画面加载中…</div>
        <div v-if="detail.status === 'offline'" class="stale-mask">节点离线 — 保留最后画面</div>
        <div v-else-if="detail.stale" class="stale-mask lag">数据滞后</div>
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

function propLabel(id) { return LABELS[id] || id }

function formatProp(p, v) {
  if (v === undefined || v === null || v === '') return '—'
  if (p.id === 'detecting') return v ? '检测中' : '待机'
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
})
onBeforeUnmount(() => clearInterval(timer))
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
.viewer {
  flex: 1; position: relative; background: #000;
  border-radius: var(--hub-radius-lg); overflow: hidden;
  border: 1px solid var(--hub-border); min-height: 420px;
  display: flex; align-items: center; justify-content: center;
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
