<template>
  <div class="flex gap-4">
    <!-- 左：连接管理 -->
    <div class="flex-1 min-w-0">
      <div class="text-xs text-gray-500 mb-2">
        PLC 对接为设备级配置：协议 / 点位 / 触发规则 / 写回规则全部可配，新产线通常从「方案模板」起步改地址即用，无需改软件。
      </div>
      <div class="flex items-center justify-between mb-4">
        <h3 class="text-cyan-300 font-semibold">PLC 连接</h3>
        <div class="flex gap-2">
          <el-dropdown @command="applyTemplate" size="small">
            <el-button size="small">方案模板 ▼</el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item v-for="t in templates" :key="t.template_id" :command="t.template_id">
                  {{ t.template_name }}
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
          <el-button size="small" @click="openImport">导入配置</el-button>
          <el-button size="small" type="success" @click="openAdd">添加连接</el-button>
        </div>
      </div>

      <div v-if="connections.length === 0" class="text-gray-500 text-sm text-center py-8">
        暂无 PLC 连接。点「方案模板」载入西门子 S7 / Modbus 范式，或「添加连接」从零配置
      </div>
      <div class="grid grid-cols-2 gap-3">
        <div v-for="conn in connections" :key="conn.id"
             class="bg-slate-800/60 rounded-lg border p-4 cursor-pointer"
             :class="[connStatus(conn) === 'connected' ? 'border-green-700' : 'border-slate-700',
                      selectedId === conn.id ? 'ring-1 ring-cyan-500' : '']"
             @click="selectConn(conn)">
          <div class="flex items-center justify-between mb-2">
            <span class="font-medium truncate">{{ conn.name }}</span>
            <div class="flex items-center gap-1 shrink-0">
              <el-tag size="small" effect="plain">{{ driverLabel(conn.driver) }}</el-tag>
              <el-tag :type="statusTagType(connStatus(conn))" size="small">
                {{ statusLabel(connStatus(conn)) }}
              </el-tag>
            </div>
          </div>
          <div class="text-xs text-gray-400 space-y-1">
            <div>{{ connAddr(conn) }}</div>
            <div>点位 {{ (conn.points || []).length }} · 触发规则 {{ (conn.read_rules || []).length }} · 写回规则 {{ (conn.write_rules || []).length }}</div>
            <div v-if="conn.runtime" class="text-cyan-300/80">
              轮询 {{ conn.runtime.counters?.polls ?? 0 }} · 触发 {{ conn.runtime.counters?.rule_fires ?? 0 }} · 写 {{ conn.runtime.counters?.writes ?? 0 }}
            </div>
            <div v-if="conn.runtime?.last_error" class="text-red-400 truncate" :title="conn.runtime.last_error">
              {{ conn.runtime.last_error }}
            </div>
          </div>
          <div class="flex items-center gap-1 mt-3 flex-wrap" @click.stop>
            <el-switch v-model="conn.enabled" size="small" inline-prompt
                       active-text="启用" inactive-text="停用"
                       @change="v => toggleEnabled(conn, v)" />
            <el-button size="small" @click="testConn(conn)" :loading="testingId === conn.id">测试</el-button>
            <el-button size="small" type="primary" @click="editConn(conn)">编辑</el-button>
            <el-button size="small" @click="exportConn(conn)">导出</el-button>
            <el-button size="small" type="danger" @click="removeConn(conn)">删除</el-button>
          </div>
        </div>
      </div>
    </div>

    <!-- 右：实时监视 -->
    <div class="w-[420px] shrink-0 bg-slate-800/50 rounded-lg border border-slate-700 p-4 overflow-auto">
      <div class="flex items-center justify-between mb-3">
        <h3 class="text-cyan-300 font-semibold">实时监视</h3>
        <span v-if="selectedConn" class="text-xs text-gray-400 truncate max-w-[12rem]">{{ selectedConn.name }}</span>
      </div>

      <div v-if="!selectedConn" class="text-gray-500 text-sm text-center py-8">点击左侧连接卡片查看点位实时值</div>
      <template v-else>
        <div v-if="!live" class="text-gray-500 text-sm text-center py-4">
          连接未运行（启用后开始轮询）
        </div>
        <template v-else>
          <!-- 点位实时值 -->
          <div class="space-y-1 mb-4">
            <div v-for="p in (selectedConn.points || [])" :key="p.key"
                 class="flex items-center justify-between bg-slate-700/40 rounded px-2 py-1 text-xs">
              <div class="min-w-0">
                <span class="font-mono text-cyan-200">{{ p.key }}</span>
                <span class="text-gray-500 ml-1">{{ p.addr }}</span>
              </div>
              <div class="flex items-center gap-2 shrink-0">
                <span class="font-mono" :class="valueClass(live.values?.[p.key])">
                  {{ formatValue(live.values?.[p.key]) }}
                </span>
                <span class="text-gray-600 w-14 text-right">{{ changedAgo(p.key) }}</span>
              </div>
            </div>
          </div>

          <!-- 手动写值 -->
          <div class="mb-4">
            <div class="text-xs text-gray-400 mb-1">手动写值（现场联调）</div>
            <div class="flex gap-1">
              <el-select v-model="writeForm.point" size="small" class="flex-1" placeholder="选择点位">
                <el-option v-for="p in writablePoints" :key="p.key" :label="`${p.key} (${p.addr})`" :value="p.key" />
              </el-select>
              <el-input v-model="writeForm.value" size="small" class="w-24" placeholder="值" />
              <el-button size="small" type="warning" plain :loading="writing" @click="doWrite">写入</el-button>
            </div>
            <div v-if="selectedConn.driver === 'mock'" class="flex gap-1 mt-1">
              <el-select v-model="mockForm.point" size="small" class="flex-1" placeholder="模拟 PLC 侧点位">
                <el-option v-for="p in (selectedConn.points || [])" :key="p.key" :label="`${p.key} (${p.addr})`" :value="p.key" />
              </el-select>
              <el-input v-model="mockForm.value" size="small" class="w-24" placeholder="值" />
              <el-button size="small" type="info" plain @click="doMockSet">PLC 侧写</el-button>
            </div>
          </div>

          <!-- IO 日志 -->
          <div class="flex items-center justify-between mb-2">
            <div class="text-xs text-gray-400">IO 日志（值变化 / 写入 / 触发 / 错误）</div>
            <el-button size="small" text @click="loadLogsNow">刷新</el-button>
          </div>
          <div class="space-y-1">
            <div v-for="(log, i) in logs" :key="i"
                 class="bg-slate-700/40 rounded px-2 py-1 text-xs flex gap-2"
                 :class="log.dir === 'error' ? 'border border-red-800/40' : ''">
              <span class="shrink-0" :class="logDirClass(log.dir)">{{ logDirLabel(log.dir) }}</span>
              <span class="text-gray-300 min-w-0 break-all">{{ log.detail }}</span>
              <span class="text-gray-600 ml-auto shrink-0">{{ formatTs(log.ts) }}</span>
            </div>
            <div v-if="logs.length === 0" class="text-gray-500 text-xs text-center py-3">暂无日志</div>
          </div>
        </template>
      </template>
    </div>

    <!-- 添加/编辑对话框 -->
    <el-dialog v-model="showDialog" :title="editingId ? '编辑 PLC 连接' : '添加 PLC 连接'"
               width="860px" class="mes-dialog" destroy-on-close top="4vh">
      <el-form :model="form" label-width="100px" size="small">
        <div class="grid grid-cols-2 gap-x-4">
          <el-form-item label="名称" required>
            <el-input v-model="form.name" placeholder="如: 机加线 1 号 PLC" />
          </el-form-item>
          <el-form-item label="协议驱动">
            <el-select v-model="form.driver" class="w-full" @change="onDriverChange">
              <el-option v-for="d in drivers" :key="d.name" :value="d.name"
                         :label="driverLabel(d.name) + (d.available ? '' : ' (依赖库未安装)')"
                         :disabled="!d.available" />
            </el-select>
          </el-form-item>
        </div>

        <!-- 连接参数 (按驱动动态字段) -->
        <el-divider content-position="left"><span class="text-xs text-gray-400">连接参数</span></el-divider>
        <div class="grid grid-cols-3 gap-x-4">
          <el-form-item v-for="f in driverFields(form.driver)" :key="f.key" :label="f.label">
            <el-input-number v-if="f.type === 'number'" v-model="form.conn_params[f.key]"
                             :precision="0" :step="1" class="w-full" controls-position="right" />
            <el-input v-else v-model="form.conn_params[f.key]" :placeholder="f.placeholder || ' '" />
          </el-form-item>
        </div>

        <!-- 点位表 -->
        <el-divider content-position="left"><span class="text-xs text-gray-400">点位表（地址方言见驱动说明）</span></el-divider>
        <el-table :data="form.points" size="small" class="mb-2" empty-text="暂无点位，点下方按钮添加">
          <el-table-column label="key" width="120">
            <template #default="{ row }"><el-input v-model="row.key" size="small" /></template>
          </el-table-column>
          <el-table-column label="地址" width="170">
            <template #default="{ row }"><el-input v-model="row.addr" size="small" :placeholder="addrPlaceholder" /></template>
          </el-table-column>
          <el-table-column label="类型" width="120">
            <template #default="{ row }">
              <el-select v-model="row.type" size="small">
                <el-option v-for="t in POINT_TYPES" :key="t" :label="t" :value="t" />
              </el-select>
            </template>
          </el-table-column>
          <el-table-column label="方向" width="100">
            <template #default="{ row }">
              <el-select v-model="row.dir" size="small">
                <el-option label="读" value="read" />
                <el-option label="写" value="write" />
                <el-option label="读写" value="read_write" />
              </el-select>
            </template>
          </el-table-column>
          <el-table-column label="长度" width="80">
            <template #default="{ row }">
              <el-input-number v-if="String(row.type||'').startsWith('string') || row.type==='bcd'"
                               v-model="row.length" size="small" :precision="0" :step="1"
                               controls-position="right" class="!w-full" />
              <span v-else class="text-gray-600 text-xs">—</span>
            </template>
          </el-table-column>
          <el-table-column label="bit" width="70">
            <template #default="{ row }">
              <el-input-number v-if="row.type === 'bool'" v-model="row.bit" size="small"
                               :precision="0" :step="1" :min="0" :max="15"
                               controls-position="right" class="!w-full" />
              <span v-else class="text-gray-600 text-xs">—</span>
            </template>
          </el-table-column>
          <el-table-column label="字节序/编码" width="130">
            <template #default="{ row }">
              <el-select v-if="isNumericMulti(row.type)" v-model="row.byte_order" size="small" placeholder="big">
                <el-option label="big (ABCD)" value="big" />
                <el-option label="little (DCBA)" value="little" />
                <el-option label="word_swap (CDAB)" value="word_swap" />
                <el-option label="byte_swap (BADC)" value="byte_swap" />
              </el-select>
              <el-select v-else-if="String(row.type||'').startsWith('string')" v-model="row.encoding" size="small" placeholder="ascii">
                <el-option label="ascii" value="ascii" />
                <el-option label="gbk" value="gbk" />
                <el-option label="utf8" value="utf8" />
              </el-select>
              <span v-else class="text-gray-600 text-xs">—</span>
            </template>
          </el-table-column>
          <el-table-column width="50">
            <template #default="{ $index }">
              <el-button size="small" text type="danger" @click="form.points.splice($index, 1)">✕</el-button>
            </template>
          </el-table-column>
        </el-table>
        <el-button size="small" plain @click="addPoint">+ 添加点位</el-button>

        <!-- 规则 (JSON) -->
        <el-divider content-position="left">
          <span class="text-xs text-gray-400">触发规则 / 写回规则（JSON，模板已含常用范式）</span>
        </el-divider>
        <div class="grid grid-cols-2 gap-3">
          <div>
            <div class="text-xs text-gray-400 mb-1">read_rules — 点位变化 → 动作（绑码/切项目/回执/报警）</div>
            <el-input v-model="form.read_rules_text" type="textarea" :rows="9"
                      class="font-mono" :class="{ 'json-error': jsonErrors.read_rules }" />
            <div v-if="jsonErrors.read_rules" class="text-red-400 text-xs mt-0.5">{{ jsonErrors.read_rules }}</div>
          </div>
          <div>
            <div class="text-xs text-gray-400 mb-1">write_rules — 主程序事件 → 写点位（结果码/心跳/脉冲）</div>
            <el-input v-model="form.write_rules_text" type="textarea" :rows="9"
                      class="font-mono" :class="{ 'json-error': jsonErrors.write_rules }" />
            <div v-if="jsonErrors.write_rules" class="text-red-400 text-xs mt-0.5">{{ jsonErrors.write_rules }}</div>
          </div>
        </div>
        <div class="mt-2">
          <div class="text-xs text-gray-400 mb-1">options — 默认工位 / 断链报警 / PLC 心跳监视 / 重连参数</div>
          <el-input v-model="form.options_text" type="textarea" :rows="4"
                    class="font-mono" :class="{ 'json-error': jsonErrors.options }" />
          <div v-if="jsonErrors.options" class="text-red-400 text-xs mt-0.5">{{ jsonErrors.options }}</div>
        </div>
      </el-form>
      <template #footer>
        <el-checkbox v-model="form.enabled" class="float-left">保存后启用</el-checkbox>
        <el-button @click="showDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveConn">保存</el-button>
      </template>
    </el-dialog>

    <!-- 导入对话框 -->
    <el-dialog v-model="showImport" title="导入 PLC 配置" width="620px" class="mes-dialog" destroy-on-close>
      <div class="text-xs text-gray-400 mb-2">粘贴其他产线「导出」的配置 JSON（导入后默认停用，核对地址后再启用）</div>
      <el-input v-model="importText" type="textarea" :rows="14" class="font-mono" />
      <template #footer>
        <el-button @click="showImport = false">取消</el-button>
        <el-button type="primary" @click="doImport">导入</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { usePollingStore } from '@/store/usePollingStore'
import {
  createPlcConnection, deletePlcConnection, enablePlcConnection,
  exportPlcConnection, getPlcConnections, getPlcDrivers, getPlcLive,
  getPlcLogs, getPlcTemplates, importPlcConnection, mockSetPlcPoint,
  testPlcConnection, updatePlcConnection, writePlcPoint,
} from '@/api/plc'

const pollingStore = usePollingStore()

const connections = ref([])
const drivers = ref([])
const templates = ref([])
const selectedId = ref(null)
const live = ref(null)
const logs = ref([])
const testingId = ref(null)
const writing = ref(false)
const saving = ref(false)

const showDialog = ref(false)
const showImport = ref(false)
const importText = ref('')
const editingId = ref(null)

const writeForm = reactive({ point: '', value: '' })
const mockForm = reactive({ point: '', value: '' })
const jsonErrors = reactive({ read_rules: '', write_rules: '', options: '' })

const POINT_TYPES = ['bool', 'byte', 'int16', 'uint16', 'int32', 'uint32',
  'float32', 'float64', 'word', 'dword', 'bcd',
  'string_s7', 'string_fixed', 'string_cstr']

const DRIVER_LABELS = {
  s7: '西门子 S7', modbus_tcp: 'Modbus TCP', modbus_rtu: 'Modbus RTU',
  mc: '三菱 MC', fins: '欧姆龙 FINS', ethernet_ip: 'EtherNet/IP',
  opcua: 'OPC UA', mock: '虚拟 PLC',
}

// 各驱动连接参数字段 (声明式: 加驱动 = 加一行, 不改逻辑)
const DRIVER_FIELDS = {
  s7: [
    { key: 'ip', label: 'PLC IP', placeholder: '172.20.11.111' },
    { key: 'rack', label: 'Rack', type: 'number' },
    { key: 'slot', label: 'Slot', type: 'number' },
    { key: 'port', label: '端口', type: 'number' },
    { key: 'poll_interval_ms', label: '轮询(ms)', type: 'number' },
  ],
  modbus_tcp: [
    { key: 'ip', label: 'PLC IP', placeholder: '192.168.1.10' },
    { key: 'port', label: '端口', type: 'number' },
    { key: 'unit_id', label: '从站号', type: 'number' },
    { key: 'poll_interval_ms', label: '轮询(ms)', type: 'number' },
  ],
  modbus_rtu: [
    { key: 'serial_port', label: '串口', placeholder: 'COM3' },
    { key: 'baudrate', label: '波特率', type: 'number' },
    { key: 'unit_id', label: '从站号', type: 'number' },
    { key: 'poll_interval_ms', label: '轮询(ms)', type: 'number' },
  ],
  mc: [
    { key: 'ip', label: 'PLC IP', placeholder: '192.168.1.20' },
    { key: 'port', label: '端口', type: 'number' },
    { key: 'plc_type', label: 'PLC 型号', placeholder: 'Q / L / iQ-R / FX5' },
    { key: 'poll_interval_ms', label: '轮询(ms)', type: 'number' },
  ],
  fins: [
    { key: 'ip', label: 'PLC IP', placeholder: '192.168.1.30' },
    { key: 'port', label: '端口', type: 'number' },
    { key: 'src_node', label: '本机节点', type: 'number' },
    { key: 'poll_interval_ms', label: '轮询(ms)', type: 'number' },
  ],
  ethernet_ip: [
    { key: 'ip', label: 'PLC IP', placeholder: '192.168.1.40 或 192.168.1.40/1' },
    { key: 'poll_interval_ms', label: '轮询(ms)', type: 'number' },
  ],
  opcua: [
    { key: 'url', label: 'UA 地址', placeholder: 'opc.tcp://192.168.1.50:4840' },
    { key: 'username', label: '用户名', placeholder: '可空' },
    { key: 'password', label: '密码', placeholder: '可空' },
    { key: 'poll_interval_ms', label: '轮询(ms)', type: 'number' },
  ],
  mock: [
    { key: 'store_id', label: '隔离键', placeholder: 'default' },
    { key: 'poll_interval_ms', label: '轮询(ms)', type: 'number' },
  ],
}

const ADDR_HINTS = {
  s7: 'DB1200.DBX0.2 / DBW2 / STRING@6',
  modbus_tcp: 'hr:100 / ir:0 / co:5 / di:2',
  modbus_rtu: 'hr:100 / co:5',
  mc: 'D100 / M10 / X0',
  fins: 'DM100 / CIO50 / W10',
  ethernet_ip: 'Tag 名, 如 ProductSN',
  opcua: 'ns=2;s=Device.Tag',
  mock: '任意串, 如 m0',
}

const form = reactive({
  name: '', driver: 's7', enabled: false,
  conn_params: {}, points: [],
  read_rules_text: '[]', write_rules_text: '[]', options_text: '{}',
})

// ---------------- 展示辅助 ----------------

const selectedConn = computed(() =>
  connections.value.find(c => c.id === selectedId.value) || null)
const writablePoints = computed(() =>
  (selectedConn.value?.points || []).filter(p => (p.dir || 'read') !== 'read'))
const addrPlaceholder = computed(() => ADDR_HINTS[form.driver] || ' ')

const driverLabel = (d) => DRIVER_LABELS[d] || d
const driverFields = (d) => DRIVER_FIELDS[d] || []
const isNumericMulti = (t) => ['int16', 'uint16', 'int32', 'uint32', 'float32',
  'float64', 'word', 'dword', 'bcd'].includes(t)

const connStatus = (conn) => conn.runtime?.status || (conn.enabled ? 'starting' : 'disabled')
const statusLabel = (s) => ({
  connected: '已连接', error: '通讯异常', config_error: '配置错误',
  starting: '启动中', stopped: '已停止', disabled: '停用',
}[s] || s)
const statusTagType = (s) => ({
  connected: 'success', error: 'danger', config_error: 'danger',
  starting: 'warning', stopped: 'info', disabled: 'info',
}[s] || 'info')

const connAddr = (conn) => {
  const p = conn.conn_params || {}
  if (conn.driver === 'opcua') return p.url || ''
  if (conn.driver === 'modbus_rtu') return `${p.serial_port || '?'} @ ${p.baudrate || 9600}`
  if (conn.driver === 'mock') return `虚拟 (store: ${p.store_id || 'default'})`
  return `${p.ip || '?'}${p.port ? ':' + p.port : ''}` +
    (conn.driver === 's7' ? ` (rack ${p.rack ?? 0} / slot ${p.slot ?? 2})` : '')
}

const formatValue = (v) => {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'boolean') return v ? 'TRUE' : 'FALSE'
  if (typeof v === 'number' && !Number.isInteger(v)) return v.toFixed(3)
  return String(v) === '' ? '(空)' : String(v)
}
const valueClass = (v) => {
  if (v === true) return 'text-green-400'
  if (v === false) return 'text-gray-500'
  return 'text-cyan-200'
}
const changedAgo = (key) => {
  const ts = live.value?.value_changed_ts?.[key]
  if (!ts) return ''
  const sec = Math.max(0, Date.now() / 1000 - ts)
  if (sec < 60) return `${sec.toFixed(0)}s 前`
  if (sec < 3600) return `${(sec / 60).toFixed(0)}m 前`
  return `${(sec / 3600).toFixed(1)}h 前`
}
const formatTs = (ts) => new Date(ts * 1000).toLocaleTimeString('zh-CN', { hour12: false })
const logDirLabel = (d) => ({ read: '变化', write: '写入', event: '触发', error: '错误' }[d] || d)
const logDirClass = (d) => ({
  read: 'text-cyan-400', write: 'text-yellow-400',
  event: 'text-green-400', error: 'text-red-400',
}[d] || 'text-gray-400')

const errMsg = (e, fallback) => {
  const d = e?.response?.data?.detail
  return typeof d === 'string' ? d
    : Array.isArray(d) ? d.map(x => x.msg || JSON.stringify(x)).join('; ')
      : d ? JSON.stringify(d) : (fallback || '操作失败')
}

// ---------------- 数据加载与轮询 ----------------

let listTimer = null
let liveTimer = null

const loadConnections = async () => {
  try {
    const { data } = await getPlcConnections()
    connections.value = data.connections || []
  } catch (e) { /* 轮询失败静默, 下轮重试 */ }
}

const loadLive = async () => {
  if (!selectedId.value) { live.value = null; return }
  try {
    const { data } = await getPlcLive(selectedId.value)
    live.value = data
  } catch (e) {
    live.value = null      // 未启用/已停止 → 404
  }
}

const loadLogsNow = async () => {
  if (!selectedId.value) return
  try {
    const { data } = await getPlcLogs(selectedId.value, pollingStore.logLimit('plc', 60))
    logs.value = data.logs || []
  } catch (e) { logs.value = [] }
}

const selectConn = (conn) => {
  selectedId.value = conn.id
  live.value = null
  logs.value = []
  loadLive()
  loadLogsNow()
}

onMounted(async () => {
  await pollingStore.load()
  loadConnections()
  try {
    const [dRes, tRes] = await Promise.all([getPlcDrivers(), getPlcTemplates()])
    drivers.value = dRes.data.drivers || []
    templates.value = tRes.data.templates || []
  } catch (e) { /* 首屏加载失败不阻塞面板 */ }
  listTimer = setInterval(loadConnections, pollingStore.get('plc_status', 3000))
  liveTimer = setInterval(() => { loadLive(); loadLogsNow() },
    pollingStore.get('plc_live', 1500))
})

onUnmounted(() => {
  if (listTimer) clearInterval(listTimer)
  if (liveTimer) clearInterval(liveTimer)
})

// ---------------- CRUD ----------------

const resetForm = (cfg = null) => {
  editingId.value = null
  form.name = cfg?.name || ''
  form.driver = cfg?.driver || 's7'
  form.enabled = false
  form.conn_params = { ...(cfg?.conn_params || { poll_interval_ms: 100 }) }
  form.points = JSON.parse(JSON.stringify(cfg?.points || []))
  form.read_rules_text = JSON.stringify(cfg?.read_rules || [], null, 2)
  form.write_rules_text = JSON.stringify(cfg?.write_rules || [], null, 2)
  form.options_text = JSON.stringify(cfg?.options || { default_channel: 0 }, null, 2)
  jsonErrors.read_rules = jsonErrors.write_rules = jsonErrors.options = ''
}

const openAdd = () => { resetForm(); showDialog.value = true }

const applyTemplate = (templateId) => {
  const t = templates.value.find(x => x.template_id === templateId)
  if (!t) return
  resetForm(t.config)
  showDialog.value = true
  ElMessage.info('已载入模板，请核对 IP / 点位地址 / 机型映射后保存')
}

const editConn = (conn) => {
  resetForm(conn)
  editingId.value = conn.id
  form.enabled = conn.enabled
  showDialog.value = true
}

const onDriverChange = () => {
  form.conn_params = { poll_interval_ms: form.conn_params.poll_interval_ms || 100 }
}

const addPoint = () => {
  form.points.push({ key: '', addr: '', type: 'bool', dir: 'read' })
}

const parseJsonField = (text, field) => {
  try {
    const v = JSON.parse(text || (field === 'options' ? '{}' : '[]'))
    jsonErrors[field] = ''
    return v
  } catch (e) {
    jsonErrors[field] = `JSON 语法错误: ${e.message}`
    return null
  }
}

const saveConn = async () => {
  if (!form.name.trim()) { ElMessage.warning('请填写连接名称'); return }
  const readRules = parseJsonField(form.read_rules_text, 'read_rules')
  const writeRules = parseJsonField(form.write_rules_text, 'write_rules')
  const options = parseJsonField(form.options_text, 'options')
  if (readRules === null || writeRules === null || options === null) return
  const payload = {
    name: form.name.trim(),
    driver: form.driver,
    enabled: form.enabled,
    conn_params: form.conn_params,
    points: form.points,
    read_rules: readRules,
    write_rules: writeRules,
    options,
  }
  saving.value = true
  try {
    if (editingId.value) {
      await updatePlcConnection(editingId.value, payload)
    } else {
      await createPlcConnection(payload)
    }
    ElMessage.success('已保存' + (form.enabled ? '并启动连接' : ''))
    showDialog.value = false
    loadConnections()
  } catch (e) {
    ElMessage.error(errMsg(e, '保存失败'))
  } finally {
    saving.value = false
  }
}

const toggleEnabled = async (conn, enabled) => {
  try {
    await enablePlcConnection(conn.id, enabled)
    ElMessage.success(enabled ? '已启用, 连接启动中' : '已停用')
    loadConnections()
  } catch (e) {
    conn.enabled = !enabled
    ElMessage.error(errMsg(e))
  }
}

const removeConn = async (conn) => {
  try {
    await ElMessageBox.confirm(`确定删除连接「${conn.name}」? 点位与规则配置将一并删除`, '删除确认', { type: 'warning' })
  } catch { return }
  try {
    await deletePlcConnection(conn.id)
    if (selectedId.value === conn.id) { selectedId.value = null; live.value = null }
    ElMessage.success('已删除')
    loadConnections()
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
}

const testConn = async (conn) => {
  testingId.value = conn.id
  try {
    const { data } = await testPlcConnection(conn.id)
    if (data.success) {
      ElMessage.success(data.message || '连接成功')
    } else {
      ElMessage.error(data.message || '连接失败')
    }
  } catch (e) {
    ElMessage.error(errMsg(e, '测试失败'))
  } finally {
    testingId.value = null
  }
}

// ---------------- 联调操作 ----------------

const doWrite = async () => {
  if (!writeForm.point) { ElMessage.warning('请选择点位'); return }
  writing.value = true
  try {
    await writePlcPoint(selectedId.value, writeForm.point, writeForm.value)
    ElMessage.success(`已写 ${writeForm.point} = ${writeForm.value}`)
    loadLive()
  } catch (e) {
    ElMessage.error(errMsg(e, '写入失败'))
  } finally {
    writing.value = false
  }
}

const doMockSet = async () => {
  if (!mockForm.point) { ElMessage.warning('请选择点位'); return }
  try {
    await mockSetPlcPoint(selectedId.value, mockForm.point, mockForm.value)
    ElMessage.success(`PLC 侧已写 ${mockForm.point} = ${mockForm.value}`)
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
}

// ---------------- 导入导出 ----------------

const exportConn = async (conn) => {
  try {
    const { data } = await exportPlcConnection(conn.id)
    await navigator.clipboard.writeText(JSON.stringify(data, null, 2))
    ElMessage.success('配置 JSON 已复制到剪贴板')
  } catch (e) {
    ElMessage.error(errMsg(e, '导出失败'))
  }
}

const openImport = () => { importText.value = ''; showImport.value = true }

const doImport = async () => {
  let cfg
  try {
    cfg = JSON.parse(importText.value)
  } catch (e) {
    ElMessage.error(`JSON 语法错误: ${e.message}`)
    return
  }
  try {
    await importPlcConnection(cfg)
    ElMessage.success('导入成功（默认停用，核对后再启用）')
    showImport.value = false
    loadConnections()
  } catch (e) {
    ElMessage.error(errMsg(e, '导入失败'))
  }
}
</script>

<style scoped>
.json-error :deep(textarea) {
  border-color: rgb(248 113 113 / 0.7);
}
</style>
