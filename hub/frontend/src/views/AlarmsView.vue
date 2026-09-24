<template>
  <div class="alarms-page">
    <header class="hub-topbar">
      <button class="hub-back" data-test="back-wall" @click="router.push({ name: 'wall' })">
        ← 检测集群
      </button>
      <h1>报警中心</h1>
      <span v-if="data" class="counts tabular" data-test="alarm-counts">
        未处理 <b :class="data.unacked ? 'warn' : ''">{{ data.unacked }}</b>
        / 共 {{ data.total }}
      </span>
      <label class="filter">
        <input type="checkbox" v-model="unackedOnly" data-test="filter-unacked" />
        只看未处理
      </label>
    </header>

    <main class="content">
      <p v-if="loadError" class="hub-load-error">{{ loadError }}</p>
      <p v-else-if="data && !data.items.length" class="hub-empty" data-test="alarms-empty">
        暂无 NG 报警事件
      </p>

      <div v-else-if="data" class="table-card">
      <table class="hub-table" data-test="alarms-table">
        <thead>
          <tr>
            <th>时间</th><th>节点</th><th>工位</th><th>事件</th>
            <th>原因</th><th>处理</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="ev in sortedItems" :key="ev.id"
              :class="{ acked: ev.acked_by, open: !ev.acked_by }"
              :data-test="`ev-row-${ev.id}`">
            <td class="ts tabular">{{ fmtTs(ev.ts) }}</td>
            <td>
              <router-link class="node-link"
                :to="{ name: 'station', params: { nodeId: ev.node_id, channelId: ev.channel_id } }">
                {{ ev.node_name }}
              </router-link>
            </td>
            <td>工位 {{ ev.channel_id + 1 }}</td>
            <td><span class="hub-badge ng">NG</span> {{ ev.event_name || '周期结算' }}</td>
            <td class="reason">{{ ev.reason || '—' }}</td>
            <td>
              <span v-if="ev.acked_by" class="acked-by" data-test="acked-by">
                {{ ev.acked_by }} 已处理
              </span>
              <button v-else-if="canAck" class="hub-btn sm ghost-primary"
                      :data-test="`ack-${ev.id}`" @click="onAck(ev)">
                确认处理
              </button>
              <span v-else class="acked-by">待处理</span>
            </td>
          </tr>
        </tbody>
      </table>
      </div>
      <p v-if="ackError" class="hub-load-error" data-test="ack-error">{{ ackError }}</p>
    </main>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import api from '../api'
import { authState } from '../auth'
import { useWsHint } from '../composables/useWsHint'

const router = useRouter()
const canAck = computed(() => ['engineer', 'director', 'admin'].includes(authState.user?.role))
const data = ref(null)
// 未处理置顶 (PagerDuty/舰队产品惯例), 组内按时间倒序 (后端已倒序)
const sortedItems = computed(() => {
  const items = data.value?.items || []
  return [...items].sort((a, b) =>
    (a.acked_by ? 1 : 0) - (b.acked_by ? 1 : 0))
})
const unackedOnly = ref(false)
const loadError = ref('')
const ackError = ref('')
let timer = null

function fmtTs(ts) {
  if (!ts) return '—'
  return ts.replace('T', ' ').slice(0, 19)
}

async function refresh() {
  try {
    const r = await api.get('/events', {
      params: { limit: 100, unacked_only: unackedOnly.value || undefined },
    })
    data.value = r.data
    loadError.value = ''
  } catch (e) {
    if (e.response?.status === 401) return
    loadError.value = '枢纽连接失败，正在重试…'
  }
}

async function onAck(ev) {
  ackError.value = ''
  try {
    await api.post(`/events/${ev.id}/ack`)
    await refresh()
  } catch (e) {
    ackError.value = e.response?.data?.detail || '确认失败'
  }
}

watch(unackedOnly, refresh)
// M8 WS 加速: 新 NG 事件到达立即刷新列表 (3s 轮询兜底不变)
useWsHint((topic) => { if (topic === 'events') refresh() })
onMounted(() => {
  refresh()
  timer = setInterval(refresh, 3000)
})
onBeforeUnmount(() => clearInterval(timer))
</script>

<style scoped>
.alarms-page { min-height: 100%; }
.counts { color: var(--hub-text-3); font-size: 13px; }
.counts .warn { color: var(--hub-ng); }
.filter {
  margin-left: auto; color: var(--hub-text-3); font-size: 13px;
  display: flex; align-items: center; gap: 6px; cursor: pointer;
}

.content { padding: 18px 20px; }
/* 卡内自滚 + 表头吸顶: 一屏外的长报警流水 (99+ 场景) 表头不滚走 */
.table-card {
  background: var(--hub-panel); border: 1px solid var(--hub-border);
  border-radius: var(--hub-radius-lg);
  max-height: calc(100vh - 108px); overflow: auto;
}
.hub-table th {
  position: sticky; top: 0; background: var(--hub-panel); z-index: 1;
  /* border-collapse 下 sticky th 的 border 不跟随, 用内阴影画底边 */
  border-bottom: none; box-shadow: inset 0 -1px 0 var(--hub-border);
}
/* 未处理行: 左 3px 红条 + 极淡红底 (需行动信号); 已处理整体弱化 */
.hub-table tr.open td:first-child { box-shadow: inset 3px 0 0 var(--hub-ng-solid); }
.hub-table tr.open td { background: rgba(239, 68, 68, 0.05); }
.hub-table tr.open:hover td { background: rgba(239, 68, 68, 0.10); }
.hub-table tr.acked td { color: var(--hub-text-4); }
.hub-table tr.acked .hub-badge.ng {
  background: transparent; color: var(--hub-text-4); border-color: var(--hub-border);
}
.ts { white-space: nowrap; color: var(--hub-text-3); }
.node-link { color: var(--hub-primary); }
.node-link:hover { color: var(--hub-primary-hover); }
.hub-badge.ng { margin-right: 6px; }
.reason { max-width: 380px; }
.acked-by { color: var(--hub-text-3); font-size: 12px; }
tr.acked .acked-by { color: var(--hub-text-4); }
</style>