<template>
  <div class="nodes-page">
    <header class="hub-topbar">
      <button class="hub-link" data-test="back-to-wall"
              @click="router.push({ name: 'wall' })">‹ 检测集群</button>
      <h1>节点与审计</h1>
      <nav class="actions">
        <button v-if="isAdmin" class="hub-btn sm" data-test="enroll-open"
                @click="showEnroll = true">纳管节点</button>
      </nav>
    </header>

    <main class="content">
      <div class="tabs" data-test="admin-tabs">
        <button class="tab" :class="{ active: tab === 'nodes' }"
                data-test="tab-nodes" @click="tab = 'nodes'">节点管理</button>
        <button class="tab" :class="{ active: tab === 'conn' }"
                data-test="tab-conn" @click="switchConn">连接历史</button>
        <button v-if="canAudit" class="tab" :class="{ active: tab === 'audit' }"
                data-test="tab-audit" @click="switchAudit">审计台账</button>
        <button v-if="isAdmin" class="tab" :class="{ active: tab === 'notify' }"
                data-test="tab-notify" @click="switchNotify">通知设置</button>
      </div>

      <!-- ============ 节点管理 ============ -->
      <section v-if="tab === 'nodes'" class="panel">
        <table class="hub-table" data-test="nodes-table">
          <thead>
            <tr>
              <th>状态</th><th>名称</th><th>地址</th><th>主机</th>
              <th>版本</th><th>工位</th><th>资源</th><th>License</th>
              <th>最近异常</th>
              <th v-if="isAdmin" class="ops-col">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="n in nodes" :key="n.id" :data-test="`node-row-${n.id}`">
              <td><span class="dot" :class="n.status" />
                <span class="st-text">{{ statusText(n.status) }}</span></td>
              <td class="strong">{{ n.name }}</td>
              <td class="mono">{{ n.base_url }}</td>
              <td>{{ n.hostname || '—' }}</td>
              <td>
                <span class="mono">{{ n.app_version || '—' }}</span>
                <span v-if="versionOutlier(n)" class="hub-badge warn"
                      data-test="version-outlier" title="与集群主流版本不一致">
                  版本不一</span>
              </td>
              <td class="tabular">{{ n.station_count }}</td>
              <td class="res tabular" :data-test="`node-res-${n.id}`">
                <template v-if="n.resources">
                  <span :class="{ hot: pct(n.resources.cpu) >= 90 }">
                    CPU {{ fmtPct(n.resources.cpu) }}</span> ·
                  <span :class="{ hot: pct(n.resources.memory) >= 90 }">
                    内存 {{ fmtPct(n.resources.memory) }}</span> ·
                  <span :class="{ hot: pct(n.resources.disk) >= 90 }">
                    盘 {{ fmtPct(n.resources.disk) }}</span>
                </template>
                <span v-else class="muted">—</span>
              </td>
              <td>
                <span v-if="n.license_state && n.license_state !== 'valid'"
                      class="hub-badge ng">{{ n.license_state }}</span>
                <span v-else class="muted">正常</span>
              </td>
              <td class="err" :title="n.last_error || ''">
                {{ n.last_error || '—' }}</td>
              <td v-if="isAdmin" class="ops-col">
                <button class="hub-link" :data-test="`node-poll-${n.id}`"
                        @click="pollNow(n)">刷新</button>
                <button class="hub-link" :data-test="`node-edit-${n.id}`"
                        @click="openEdit(n)">编辑</button>
                <button class="hub-link danger" :data-test="`node-del-${n.id}`"
                        @click="askRemove(n)">移除</button>
              </td>
            </tr>
            <tr v-if="!nodes.length">
              <td :colspan="isAdmin ? 10 : 9" class="empty">尚未纳管任何节点</td>
            </tr>
          </tbody>
        </table>
        <p v-if="opNotice" class="notice" data-test="node-notice">{{ opNotice }}</p>
      </section>

      <!-- ============ 连接历史 (上下线记录 + 7 日断连统计) ============ -->
      <section v-else-if="tab === 'conn'" class="panel">
        <div v-if="Object.keys(connStats).length" class="conn-stats"
             data-test="conn-stats">
          <div v-for="(s, nid) in connStats" :key="nid" class="conn-chip"
               :class="{ bad: s.offline_count_7d > 0 }">
            <b>{{ nodeName(Number(nid)) }}</b>
            近 7 日断连 {{ s.offline_count_7d }} 次 ·
            累计离线 {{ fmtDur(s.offline_seconds_7d) }}
          </div>
        </div>
        <div class="filters">
          <select v-model="connNode" class="hub-input sm" data-test="conn-node"
                  @change="loadConn">
            <option :value="null">全部节点</option>
            <option v-for="n in nodes" :key="n.id" :value="n.id">{{ n.name }}</option>
          </select>
          <span class="muted tabular">共 {{ connTotal }} 条切换记录</span>
        </div>
        <table class="hub-table" data-test="conn-table">
          <thead>
            <tr><th>时间</th><th>节点</th><th>事件</th><th>离线时长</th><th>原因</th></tr>
          </thead>
          <tbody>
            <tr v-for="r in connRows" :key="r.id" :data-test="`conn-row-${r.id}`">
              <td class="tabular nowrap">{{ fmtTime(r.ts) }}</td>
              <td>{{ nodeName(r.node_id) }}</td>
              <td>
                <span class="hub-badge" :class="r.status === 'offline' ? 'ng' : ''">
                  {{ r.status === 'offline' ? '离线' : '恢复在线' }}</span>
              </td>
              <td class="tabular">
                {{ r.status === 'online' && r.duration_s != null
                  ? fmtDur(r.duration_s) : '—' }}</td>
              <td class="err" :title="r.error || ''">{{ r.error || '—' }}</td>
            </tr>
            <tr v-if="!connRows.length">
              <td colspan="5" class="empty">暂无上下线记录（节点一直在线是好事）</td>
            </tr>
          </tbody>
        </table>
      </section>

      <!-- ============ 通知设置 (离线外推: webhook/钉钉/企微) ============ -->
      <section v-else-if="tab === 'notify'" class="panel notify-panel">
        <div class="notify-form">
          <label class="row-line">
            <input v-model="notifyCfg.enabled" type="checkbox"
                   data-test="notify-enabled" />
            启用离线告警外推
          </label>
          <label class="row-line">节点失联超过
            <input v-model.number="notifyCfg.offline_threshold_min"
                   type="number" min="0" max="1440" class="hub-input num"
                   data-test="notify-threshold" /> 分钟才通知（0=立即）
          </label>
          <label class="row-line">
            <input v-model="notifyCfg.notify_recover" type="checkbox"
                   data-test="notify-recover" />
            恢复在线时补发恢复通知（仅当离线侧已通知过）
          </label>
          <label class="row-line">NG 报警无人确认超过
            <input v-model.number="notifyCfg.alarm_escalate_min"
                   type="number" min="0" max="1440" class="hub-input num"
                   data-test="escalate-threshold" /> 分钟时升级通知（0=关），
            冷却
            <input v-model.number="notifyCfg.alarm_escalate_cooldown_min"
                   type="number" min="1" max="1440" class="hub-input num"
                   data-test="escalate-cooldown" /> 分钟内只发一条汇总
          </label>

          <h3>通知通道</h3>
          <div v-for="(ch, i) in notifyCfg.channels" :key="i" class="chan-row"
               :data-test="`chan-row-${i}`">
            <select v-model="ch.type" class="hub-input sm">
              <option value="webhook">通用 Webhook</option>
              <option value="dingtalk">钉钉机器人</option>
              <option value="wecom">企业微信机器人</option>
            </select>
            <input v-model="ch.url" class="hub-input url"
                   placeholder="https://…（机器人 Webhook 地址）" />
            <input v-if="ch.type === 'dingtalk'" v-model="ch.secret"
                   class="hub-input secret" placeholder="加签密钥（可选）" />
            <button class="hub-link danger" @click="notifyCfg.channels.splice(i, 1)">
              删除</button>
          </div>
          <button class="hub-link" data-test="chan-add"
                  @click="notifyCfg.channels.push({ type: 'webhook', url: '', secret: '' })">
            + 添加通道</button>

          <div class="notify-actions">
            <button class="hub-btn" data-test="notify-save"
                    :disabled="notifySaving" @click="saveNotify">
              {{ notifySaving ? '保存中…' : '保存配置' }}</button>
            <button class="hub-btn ghost" data-test="notify-test"
                    :disabled="notifyTesting || !notifyCfg.channels.length"
                    @click="testNotify">
              {{ notifyTesting ? '发送中…' : '发测试消息' }}</button>
          </div>
          <p v-if="notifyMsg" class="notice" data-test="notify-msg">{{ notifyMsg }}</p>
          <ul v-if="testResults.length" class="test-results" data-test="test-results">
            <li v-for="(r, i) in testResults" :key="i"
                :class="r.ok ? 'ok' : 'bad'">
              {{ r.type }} · {{ r.ok ? '发送成功' : `失败: ${r.error}` }}</li>
          </ul>
          <p class="hint">局域网无法直连钉钉/企微时，用「通用 Webhook」指向内网中转服务；
            告警权威仍以墙上置顶条为准，外推只是提醒补充。</p>
        </div>
      </section>

      <!-- ============ 审计台账 ============ -->
      <section v-else class="panel">
        <div class="filters">
          <select v-model="auditNode" class="hub-input sm" data-test="audit-node"
                  @change="loadAudit(0)">
            <option :value="null">全部节点</option>
            <option v-for="n in nodes" :key="n.id" :value="n.id">{{ n.name }}</option>
          </select>
          <select v-model="auditAction" class="hub-input sm" data-test="audit-action"
                  @change="loadAudit(0)">
            <option value="">全部动作</option>
            <option v-for="a in AUDIT_ACTIONS" :key="a.v" :value="a.v">{{ a.t }}</option>
          </select>
          <span class="muted tabular">共 {{ auditTotal }} 条</span>
        </div>
        <table class="hub-table" data-test="audit-table">
          <thead>
            <tr>
              <th>时间</th><th>用户</th><th>动作</th><th>对象</th>
              <th>变更</th><th>来源 IP</th><th>结果</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in auditRows" :key="r.id" :data-test="`audit-row-${r.id}`">
              <td class="tabular nowrap">{{ fmtTime(r.created_at) }}</td>
              <td>{{ r.username || '—' }}</td>
              <td><span class="mono">{{ actionText(r.action) }}</span></td>
              <td class="nowrap">{{ auditTarget(r) }}</td>
              <td class="chg" :title="chgTitle(r)">{{ chgBrief(r) }}</td>
              <td class="mono">{{ r.source_ip || '—' }}</td>
              <td>
                <span class="hub-badge" :class="r.result === 'ok' ? '' : 'ng'">
                  {{ r.result }}</span>
              </td>
            </tr>
            <tr v-if="!auditRows.length">
              <td colspan="7" class="empty">暂无审计记录</td>
            </tr>
          </tbody>
        </table>
        <div v-if="auditTotal > AUDIT_PAGE" class="pager">
          <button class="hub-link" :disabled="auditOffset === 0"
                  data-test="audit-prev"
                  @click="loadAudit(auditOffset - AUDIT_PAGE)">‹ 上一页</button>
          <span class="tabular">{{ auditOffset / AUDIT_PAGE + 1 }} /
            {{ Math.ceil(auditTotal / AUDIT_PAGE) }}</span>
          <button class="hub-link" :disabled="auditOffset + AUDIT_PAGE >= auditTotal"
                  data-test="audit-next"
                  @click="loadAudit(auditOffset + AUDIT_PAGE)">下一页 ›</button>
        </div>
      </section>
    </main>

    <!-- 编辑节点 -->
    <div v-if="editNode" class="hub-modal-mask" data-test="edit-dialog"
         @click.self="editNode = null">
      <form class="hub-modal" @submit.prevent="saveEdit">
        <h2>编辑节点 · {{ editNode.name }}</h2>
        <label>显示名<input v-model="editForm.name" class="hub-input"
                           data-test="edit-name" /></label>
        <label>边缘地址<input v-model="editForm.base_url" class="hub-input"
                             data-test="edit-url" /></label>
        <label>API Key (留空不换)
          <input v-model="editForm.api_key" class="hub-input"
                 data-test="edit-key" placeholder="tk_… 仅换新 Key 时填" /></label>
        <p class="hint">改地址 / 换 Key 会先连边缘验证身份，验证不通过不保存。</p>
        <p v-if="editError" class="hub-error" data-test="edit-error">{{ editError }}</p>
        <div class="hub-modal-actions">
          <button type="button" class="hub-btn ghost" @click="editNode = null">取消</button>
          <button type="submit" class="hub-btn" data-test="edit-save"
                  :disabled="editLoading">{{ editLoading ? '验证中…' : '保存' }}</button>
        </div>
      </form>
    </div>

    <!-- 移除节点确认 -->
    <div v-if="removeTarget" class="hub-modal-mask" data-test="remove-dialog"
         @click.self="removeTarget = null">
      <div class="hub-modal">
        <h2>移除节点</h2>
        <p>确认把「{{ removeTarget.name }}」移出枢纽纳管？<br />
          <span class="muted">边缘机自身不受影响，历史数据与审计保留。</span></p>
        <div class="hub-modal-actions">
          <button class="hub-btn ghost" data-test="remove-cancel"
                  @click="removeTarget = null">取消</button>
          <button class="hub-btn danger" data-test="remove-go"
                  @click="doRemove">移除</button>
        </div>
      </div>
    </div>

    <!-- 纳管弹窗 (与墙页同款) -->
    <div v-if="showEnroll" class="hub-modal-mask" @click.self="showEnroll = false">
      <form class="hub-modal" @submit.prevent="onEnroll">
        <h2>纳管边缘节点</h2>
        <label>显示名<input v-model="enroll.name" class="hub-input" required /></label>
        <label>边缘地址<input v-model="enroll.base_url" class="hub-input"
                             placeholder="http://192.168.1.10:8001" required /></label>
        <label>API Key<input v-model="enroll.api_key" class="hub-input"
                             placeholder="tk_…" required /></label>
        <p v-if="enrollError" class="hub-error">{{ enrollError }}</p>
        <div class="hub-modal-actions">
          <button type="button" class="hub-btn ghost"
                  @click="showEnroll = false">取消</button>
          <button type="submit" class="hub-btn" :disabled="enrollLoading">
            {{ enrollLoading ? '连接中…' : '纳管' }}</button>
        </div>
      </form>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import api from '../api'
import { authState } from '../auth'

const router = useRouter()
const isAdmin = computed(() => authState.user?.role === 'admin')
const canAudit = computed(() =>
  ['admin', 'director'].includes(authState.user?.role))

const tab = ref('nodes')
const nodes = ref([])
const opNotice = ref('')
let timer = null

// ---- 节点列表 (含 poller 运行态; last_error/资源 从 status 端点补) ----
async function loadNodes() {
  try {
    const r = await api.get('/nodes')
    // 逐节点补 last_error + 资源 (量小: 4~10 节点, 串行足够)
    const rows = r.data
    for (const n of rows) {
      try {
        const s = await api.get(`/nodes/${n.id}/status`)
        n.last_error = s.data.runtime?.last_error || ''
        n.resources = s.data.runtime?.summary?.resources || null
      } catch { n.last_error = ''; n.resources = null }
    }
    nodes.value = rows
  } catch { /* 列表失败保留上一轮 */ }
}

function nodeName(id) {
  return nodes.value.find((x) => x.id === id)?.name || `节点${id}`
}
function pct(res) { return res?.percent ?? -1 }
function fmtPct(res) {
  return res?.percent != null ? `${Math.round(res.percent)}%` : '—'
}
function fmtDur(s) {
  if (!s) return '0 分钟'
  if (s < 60) return `${s} 秒`
  if (s < 3600) return `${Math.round(s / 60)} 分钟`
  return `${(s / 3600).toFixed(1)} 小时`
}

// ---- 连接历史 ----
const connRows = ref([])
const connTotal = ref(0)
const connStats = ref({})
const connNode = ref(null)
async function loadConn() {
  const params = { limit: 100 }
  if (connNode.value != null) params.node_id = connNode.value
  try {
    const r = await api.get('/nodes/status-events', { params })
    connRows.value = r.data.items
    connTotal.value = r.data.total
    connStats.value = r.data.stats || {}
  } catch { /* 留空 */ }
}
function switchConn() {
  tab.value = 'conn'
  loadConn()
}

// ---- 通知设置 ----
const notifyCfg = reactive({
  enabled: false, offline_threshold_min: 5, notify_recover: true, channels: [],
  alarm_escalate_min: 0, alarm_escalate_cooldown_min: 30,
})
const notifySaving = ref(false)
const notifyTesting = ref(false)
const notifyMsg = ref('')
const testResults = ref([])
async function switchNotify() {
  tab.value = 'notify'
  notifyMsg.value = ''
  testResults.value = []
  try {
    const r = await api.get('/notify/config')
    Object.assign(notifyCfg, r.data)
  } catch { /* 默认值 */ }
}
async function saveNotify() {
  notifySaving.value = true
  notifyMsg.value = ''
  try {
    await api.put('/notify/config', { ...notifyCfg })
    notifyMsg.value = '已保存'
  } catch (e) {
    notifyMsg.value = e.response?.data?.detail || '保存失败'
  } finally {
    notifySaving.value = false
  }
}
async function testNotify() {
  notifyTesting.value = true
  testResults.value = []
  try {
    await api.put('/notify/config', { ...notifyCfg }) // 先存再测: 测的就是保存态
    const r = await api.post('/notify/test')
    testResults.value = r.data.results
  } catch (e) {
    notifyMsg.value = e.response?.data?.detail || '测试失败'
  } finally {
    notifyTesting.value = false
  }
}

function statusText(s) {
  return { online: '在线', offline: '离线', unknown: '未知' }[s] || s
}

// 版本不一致高亮: 与"多数版本"不同的标黄 (fleet 惯例)
const majorityVersion = computed(() => {
  const counts = {}
  for (const n of nodes.value) {
    if (n.app_version) counts[n.app_version] = (counts[n.app_version] || 0) + 1
  }
  let best = null
  for (const [v, c] of Object.entries(counts)) {
    if (!best || c > counts[best]) best = v
  }
  return best
})
function versionOutlier(n) {
  return n.app_version && majorityVersion.value
    && nodes.value.length > 1 && n.app_version !== majorityVersion.value
}

async function pollNow(n) {
  opNotice.value = ''
  try {
    await api.post(`/nodes/${n.id}/poll`)
    await loadNodes()
    opNotice.value = `已立即轮询「${n.name}」`
  } catch (e) {
    opNotice.value = e.response?.data?.detail || '轮询失败'
  }
}

// ---- 编辑 ----
const editNode = ref(null)
const editForm = reactive({ name: '', base_url: '', api_key: '' })
const editError = ref('')
const editLoading = ref(false)
function openEdit(n) {
  editNode.value = n
  editForm.name = n.name
  editForm.base_url = n.base_url
  editForm.api_key = ''
  editError.value = ''
}
async function saveEdit() {
  editError.value = ''
  editLoading.value = true
  try {
    const body = { name: editForm.name, base_url: editForm.base_url }
    if (editForm.api_key) body.api_key = editForm.api_key
    await api.put(`/nodes/${editNode.value.id}`, body)
    editNode.value = null
    await loadNodes()
  } catch (e) {
    editError.value = e.response?.data?.detail || '保存失败'
  } finally {
    editLoading.value = false
  }
}

// ---- 移除 ----
const removeTarget = ref(null)
function askRemove(n) { removeTarget.value = n }
async function doRemove() {
  try {
    await api.delete(`/nodes/${removeTarget.value.id}`)
    removeTarget.value = null
    await loadNodes()
  } catch (e) {
    opNotice.value = e.response?.data?.detail || '移除失败'
    removeTarget.value = null
  }
}

// ---- 纳管 (同墙页) ----
const showEnroll = ref(false)
const enrollLoading = ref(false)
const enrollError = ref('')
const enroll = reactive({ name: '', base_url: '', api_key: '' })
async function onEnroll() {
  enrollError.value = ''
  enrollLoading.value = true
  try {
    await api.post('/nodes', { ...enroll })
    showEnroll.value = false
    enroll.name = enroll.base_url = enroll.api_key = ''
    await loadNodes()
  } catch (e) {
    enrollError.value = e.response?.data?.detail || '纳管失败'
  } finally {
    enrollLoading.value = false
  }
}

// ---- 审计台账 ----
const AUDIT_PAGE = 50
// 与后端 write_audit 实际 action 名一一对应 (ops 是 ops.<动作> 前缀族)
const AUDIT_ACTIONS = [
  { v: 'ops.start_detection', t: '远程开始检测' },
  { v: 'ops.stop_detection', t: '远程停止检测' },
  { v: 'ops.activate_project', t: '远程切项目' },
  { v: 'ops.ack_alarm', t: '远程消警' },
  { v: 'node.enroll', t: '纳管节点' }, { v: 'node.update', t: '修改节点' },
  { v: 'node.remove', t: '移除节点' }, { v: 'station.rename', t: '工位改名' },
  { v: 'event.ack', t: '报警确认' }, { v: 'lock.steal', t: '抢锁' },
  { v: 'auth.login', t: '登录' },
  { v: 'user.create', t: '建用户' }, { v: 'user.update', t: '改用户' },
  { v: 'user.change_password', t: '改密码' },
]
const auditRows = ref([])
const auditTotal = ref(0)
const auditOffset = ref(0)
const auditNode = ref(null)
const auditAction = ref('')
async function loadAudit(offset) {
  auditOffset.value = Math.max(0, offset)
  const params = { limit: AUDIT_PAGE, offset: auditOffset.value }
  if (auditNode.value != null) params.node_id = auditNode.value
  if (auditAction.value) params.action = auditAction.value
  try {
    const r = await api.get('/audit', { params })
    auditRows.value = r.data.items
    auditTotal.value = r.data.total
  } catch { /* 无权限/失败: 表留空 */ }
}
function switchAudit() {
  tab.value = 'audit'
  loadAudit(0)
}
function actionText(a) {
  return AUDIT_ACTIONS.find((x) => x.v === a)?.t || a
}
function auditTarget(r) {
  const n = nodes.value.find((x) => x.id === r.node_id)
  const name = n ? n.name : (r.node_id != null ? `节点${r.node_id}` : '')
  if (r.channel_id != null) return `${name} · 工位${r.channel_id}`
  return name || '—'
}
function fmtTime(ts) {
  if (!ts) return '—'
  const d = new Date(ts)
  const p = (x) => String(x).padStart(2, '0')
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} `
    + `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}
function chgBrief(r) {
  const s = r.new_value || r.old_value || ''
  return s.length > 60 ? `${s.slice(0, 60)}…` : s
}
function chgTitle(r) {
  return [r.old_value && `旧: ${r.old_value}`, r.new_value && `新: ${r.new_value}`]
    .filter(Boolean).join('\n')
}

onMounted(() => {
  loadNodes()
  timer = setInterval(loadNodes, 5000)
})
onBeforeUnmount(() => clearInterval(timer))
</script>

<style scoped>
.nodes-page { min-height: 100%; display: flex; flex-direction: column; }
.hub-topbar h1 { margin-left: 4px; }
.actions { margin-left: auto; display: flex; gap: 12px; }
.content { padding: 16px 20px; display: flex; flex-direction: column; gap: 14px; }

.tabs { display: flex; gap: 2px; border-bottom: 1px solid var(--hub-border); }
.tab {
  background: none; border: none; color: var(--hub-text-3); font-size: 14px;
  padding: 8px 16px; cursor: pointer; border-bottom: 2px solid transparent;
}
.tab:hover { color: var(--hub-text); }
.tab.active {
  color: var(--hub-primary); border-bottom-color: var(--hub-primary);
  font-weight: 600;
}

.panel { display: flex; flex-direction: column; gap: 12px; }
.filters { display: flex; gap: 10px; align-items: center; }
.hub-input.sm { padding: 6px 10px; font-size: 13px; width: auto; }

.strong { color: var(--hub-text); font-weight: 500; }
.mono { font-family: var(--hub-mono, ui-monospace, monospace); font-size: 12px; }
.muted { color: var(--hub-text-3); font-size: 12px; }
.nowrap { white-space: nowrap; }
.st-text { margin-left: 6px; font-size: 12px; color: var(--hub-text-2); }
.err {
  color: var(--hub-text-3); font-size: 12px; max-width: 220px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.chg {
  color: var(--hub-text-2); font-size: 12px; max-width: 320px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.empty { text-align: center; color: var(--hub-text-4); padding: 24px 0; }
.ops-col { white-space: nowrap; }
.ops-col .hub-link { margin-right: 10px; font-size: 13px; }
.hub-link.danger { color: var(--hub-ng); }
.notice { color: var(--hub-text-3); font-size: 13px; }
.pager {
  display: flex; gap: 12px; align-items: center; justify-content: center;
  color: var(--hub-text-3); font-size: 13px;
}
.hint { color: var(--hub-text-4); font-size: 12px; }
.hub-btn.sm { padding: 6px 14px; font-size: 13px; }
.hub-btn.danger { background: var(--hub-ng-solid); border-color: var(--hub-ng-solid); }

/* 资源列: 超 90% 标红 */
.res { font-size: 12px; color: var(--hub-text-2); white-space: nowrap; }
.res .hot { color: var(--hub-ng); font-weight: 600; }

/* 连接历史: 7 日统计 chip */
.conn-stats { display: flex; gap: 10px; flex-wrap: wrap; }
.conn-chip {
  padding: 6px 12px; border: 1px solid var(--hub-border);
  border-radius: var(--hub-radius); font-size: 12px; color: var(--hub-text-3);
}
.conn-chip b { color: var(--hub-text); font-weight: 500; margin-right: 6px; }
.conn-chip.bad { border-color: var(--hub-warn-bd); background: var(--hub-warn-bg); }

/* 通知设置 */
.notify-panel { max-width: 720px; }
.notify-form { display: flex; flex-direction: column; gap: 12px; }
.notify-form h3 {
  color: var(--hub-text); font-size: 14px; font-weight: 600; margin-top: 8px;
}
.row-line {
  display: flex; align-items: center; gap: 8px; color: var(--hub-text-2);
  font-size: 14px;
}
.hub-input.num { width: 72px; padding: 5px 8px; text-align: center; }
.chan-row { display: flex; gap: 8px; align-items: center; }
.chan-row .hub-input.url { flex: 1; }
.chan-row .hub-input.secret { width: 160px; }
.notify-actions { display: flex; gap: 12px; margin-top: 6px; }
.test-results { list-style: none; font-size: 13px; display: flex;
  flex-direction: column; gap: 4px; }
.test-results .ok { color: var(--hub-text-2); }
.test-results .bad { color: var(--hub-ng); }
</style>
