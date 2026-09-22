<template>
  <div class="wall-page">
    <header class="hub-topbar">
      <div class="brand">
        <span class="logo">◈</span>
        <h1>集中管控枢纽 · 监控墙</h1>
      </div>
      <nav class="actions">
        <button class="hub-link" data-test="data-link"
                @click="router.push({ name: 'data' })">数据中心</button>
        <button class="hub-link alarm-link" data-test="alarms-link"
                @click="router.push({ name: 'alarms' })">
          报警中心
          <span v-if="unackedCount" class="badge" data-test="alarm-badge">
            {{ unackedCount > 99 ? '99+' : unackedCount }}</span>
        </button>
        <button v-if="canManage" class="hub-link" data-test="users-link"
                @click="router.push({ name: 'users' })">用户管理</button>
        <button v-if="canManage" class="hub-link" data-test="enroll-open"
                @click="showEnroll = true">纳管节点</button>
        <span class="divider" />
        <span class="whoami" :title="user?.username">
          {{ user?.display_name || user?.username }}</span>
        <button class="hub-link muted" data-test="pwd-open"
                @click="showPwd = true">改密</button>
        <button class="hub-link muted" data-test="logout-btn"
                @click="onLogout">退出</button>
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
                       :offline="st.offline" />
        </div>
      </section>
    </main>

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
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import api from '../api'
import { authState, logout } from '../auth'
import ChangePasswordDialog from '../components/ChangePasswordDialog.vue'
import StationTile from '../components/StationTile.vue'

const router = useRouter()
const user = authState.user
const canManage = computed(() => authState.user?.role === 'admin')
const wall = ref(null)
const unackedCount = ref(0)
const loadError = ref('')
const showEnroll = ref(false)
const showPwd = ref(false)
const enrollLoading = ref(false)
const enrollError = ref('')
const enroll = reactive({ name: '', base_url: '', api_key: '' })
let timer = null

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
    // 顶栏未处理报警徽标 (轻查询: limit=1 只取计数)
    const r = await api.get('/events', { params: { limit: 1 } })
    unackedCount.value = r.data.unacked
  } catch { /* 徽标失败不影响墙 */ }
}

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
})
onBeforeUnmount(() => clearInterval(timer))
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
</style>
