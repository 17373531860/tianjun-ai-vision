<template>
  <!-- 批量操作 (M9, RFC §4.3 P1): 预览清单 → 逐台执行进度 → 失败原因。
       架构纪律: 不新增后端写路径 —— 前端编排逐台调用现有唯一写网关
       POST /nodes/{n}/stations/{ch}/ops (锁/白名单/审计全复用);
       批量切项目按「项目名」跨机匹配 (各边缘 project_id 空间独立)。 -->
  <div class="hub-modal-mask" data-test="batch-dialog" @click.self="tryClose">
    <div class="hub-modal dialog">
      <h2>批量操作</h2>

      <!-- ① 动作 -->
      <div class="row">
        <label>动作</label>
        <select v-model="action" class="hub-select" data-test="batch-action"
                :disabled="running">
          <option value="start_detection">开始检测（选中节点全部工位）</option>
          <option value="stop_detection">停止检测（选中节点全部工位）</option>
          <option value="activate_project">切换项目（按项目名逐台匹配）</option>
        </select>
      </div>

      <!-- ② 目标节点 -->
      <div class="row">
        <label>目标节点
          <button class="mini-link" data-test="batch-all" :disabled="running"
                  @click="toggleAll">{{ allChecked ? '全不选' : '全选在线' }}</button>
        </label>
        <div class="node-picks">
          <label v-for="n in nodes" :key="n.id" class="pick"
                 :class="{ off: n.status !== 'online' }">
            <input v-model="picked" type="checkbox" :value="n.id"
                   :disabled="n.status !== 'online' || running"
                   :data-test="`batch-node-${n.id}`" />
            {{ n.name }}
            <i v-if="n.status !== 'online'">（离线不可选）</i>
            <i v-else>（{{ n.stations?.length || 0 }} 工位）</i>
          </label>
        </div>
      </div>

      <!-- ③ 切项目: 项目名 (候选 = 各选中节点项目名并集) -->
      <div v-if="action === 'activate_project'" class="row">
        <label>项目名</label>
        <input v-model="projectName" class="hub-input" list="batch-proj-names"
               placeholder="输入或从候选选择（按名逐台匹配）"
               data-test="batch-project" :disabled="running" />
        <datalist id="batch-proj-names">
          <option v-for="nm in projectNames" :key="nm" :value="nm" />
        </datalist>
        <p class="hint">危险动作：切项目影响整机全部工位；检测中的机器会被边缘拒绝。</p>
      </div>

      <!-- ④ 预览清单 / 执行结果 (同一张表, 状态列切换) -->
      <div v-if="plan.length" class="plan" data-test="batch-plan">
        <table class="hub-table">
          <thead><tr><th>目标</th><th>动作</th><th class="st-col">状态</th></tr></thead>
          <tbody>
            <tr v-for="(t, i) in plan" :key="i" :data-test="`batch-row-${i}`">
              <td>{{ t.label }}</td>
              <td class="dim">{{ actionLabel }}</td>
              <td class="st-col">
                <span v-if="t.state === 'pending'" class="muted">待执行</span>
                <span v-else-if="t.state === 'doing'" class="doing">执行中…</span>
                <span v-else-if="t.state === 'ok'" class="ok">✓ 成功</span>
                <span v-else class="bad" :title="t.message">✗ {{ t.message }}</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else class="muted empty-hint">选择目标节点后此处出现预览清单</p>

      <p v-if="doneSummary" class="summary" data-test="batch-summary">{{ doneSummary }}</p>

      <div class="hub-modal-actions">
        <button class="hub-btn ghost" data-test="batch-close"
                :disabled="running" @click="tryClose">
          {{ finished ? '关闭' : '取消' }}</button>
        <button class="hub-btn" :class="{ danger: action === 'activate_project' }"
                data-test="batch-go"
                :disabled="running || finished || !plan.length
                  || (action === 'activate_project' && !projectName.trim())"
                @click="run">
          {{ running ? `执行中 ${doneCount}/${plan.length}…` : '执行' }}</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import api from '../api'

const props = defineProps({
  nodes: { type: Array, required: true },   // 墙载荷 nodes (含 stations)
})
const emit = defineEmits(['close', 'done'])

const action = ref('start_detection')
const picked = ref([])
const projectName = ref('')
const projectNames = ref([])
const running = ref(false)
const finished = ref(false)
const doneSummary = ref('')
const plan = ref([])   // [{node_id, channel_id, label, state, message}]

const onlineIds = computed(() =>
  props.nodes.filter((n) => n.status === 'online').map((n) => n.id))
const allChecked = computed(() =>
  onlineIds.value.length > 0 && picked.value.length === onlineIds.value.length)
const actionLabel = computed(() => ({
  start_detection: '开始检测', stop_detection: '停止检测',
  activate_project: `切到「${projectName.value || '…'}」`,
}[action.value]))
const doneCount = computed(() =>
  plan.value.filter((t) => t.state === 'ok' || t.state === 'fail').length)

function toggleAll() {
  picked.value = allChecked.value ? [] : [...onlineIds.value]
}

// 预览清单: 工位级动作=选中节点全部工位; 切项目=每节点一行 (节点级动作)
function buildPlan() {
  if (finished.value) return
  const rows = []
  for (const n of props.nodes) {
    if (!picked.value.includes(n.id)) continue
    if (action.value === 'activate_project') {
      rows.push({ node_id: n.id, channel_id: (n.stations?.[0]?.channel_id ?? 0),
                  label: `${n.name}（整机）`, state: 'pending', message: '' })
    } else {
      for (const st of n.stations || []) {
        rows.push({ node_id: n.id, channel_id: st.channel_id,
                    label: `${n.name} · ${st.display_name || `工位${st.channel_id}`}`,
                    state: 'pending', message: '' })
      }
    }
  }
  plan.value = rows
}
watch([picked, action], buildPlan, { deep: true })

// 切项目候选: 选中节点项目名并集 (懒拉, 失败静默 —— 手输仍可用)
watch([picked, action], async () => {
  if (action.value !== 'activate_project') return
  const names = new Set()
  for (const nid of picked.value) {
    try {
      const r = await api.get(`/nodes/${nid}/projects`)
      for (const p of r.data.items || []) names.add(p.name)
    } catch { /* 离线/无权限: 跳过 */ }
  }
  projectNames.value = [...names].sort()
})

async function run() {
  running.value = true
  doneSummary.value = ''
  for (const t of plan.value) {
    t.state = 'doing'
    try {
      let projectId = null
      if (action.value === 'activate_project') {
        // 按名匹配本机项目 id (各边缘 id 空间独立, 名字才是跨机通货)
        const r = await api.get(`/nodes/${t.node_id}/projects`)
        const hit = (r.data.items || []).find(
          (p) => p.name === projectName.value.trim())
        if (!hit) { t.state = 'fail'; t.message = '本机无同名项目'; continue }
        if (hit.is_active) { t.state = 'ok'; t.message = ''; continue }
        projectId = hit.id
      }
      await api.post(`/nodes/${t.node_id}/stations/${t.channel_id}/ops`, {
        action: action.value,
        ...(projectId != null ? { project_id: projectId } : {}),
      })
      t.state = 'ok'
    } catch (e) {
      t.state = 'fail'
      t.message = e.response?.data?.detail || e.message || '失败'
    }
  }
  running.value = false
  finished.value = true
  const ok = plan.value.filter((t) => t.state === 'ok').length
  const fail = plan.value.length - ok
  doneSummary.value = `完成：${ok} 成功` + (fail ? `，${fail} 失败（原因见状态列）` : '')
  emit('done')
}

function tryClose() {
  if (!running.value) emit('close')
}
</script>

<style scoped>
.dialog { width: 560px; max-width: 92vw; }
.row { margin-bottom: 14px; }
.row > label {
  display: flex; align-items: center; gap: 10px;
  font-size: 13px; color: var(--hub-text-3); margin-bottom: 6px;
}
.mini-link {
  background: none; border: none; color: var(--hub-primary);
  font-size: 12px; cursor: pointer; padding: 0;
}
.node-picks { display: flex; flex-wrap: wrap; gap: 6px 16px; }
.pick {
  display: flex; align-items: center; gap: 6px; font-size: 13px;
  color: var(--hub-text-2); cursor: pointer;
}
.pick.off { color: var(--hub-text-4); cursor: not-allowed; }
.pick i { font-style: normal; font-size: 12px; color: var(--hub-text-4); }
.hint { font-size: 12px; color: var(--hub-warn); margin-top: 6px; }
.plan { max-height: 260px; overflow: auto; margin-bottom: 10px; }
.st-col { width: 180px; }
.doing { color: var(--hub-primary); }
.ok { color: var(--hub-ok); }
.bad { color: var(--hub-ng); }
.muted { color: var(--hub-text-4); }
.dim { color: var(--hub-text-3); }
.empty-hint { font-size: 13px; margin: 10px 0; }
.summary { font-size: 13px; color: var(--hub-text-2); margin-bottom: 8px; }
</style>
