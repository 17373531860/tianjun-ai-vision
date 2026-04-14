<template>
  <div>
    <!-- Tab 切换: 基本管理 / WMax 高级（仅检测到 WMax 设备时显示） -->
    <el-tabs v-model="activeTab" class="scanner-tabs mb-4">
      <el-tab-pane label="设备管理" name="basic" />
      <el-tab-pane v-if="hasWmaxDevice" label="WMax 高级控制" name="wmax" />
    </el-tabs>

    <!-- WMax 高级面板 -->
    <WMaxPanel v-if="activeTab === 'wmax' && hasWmaxDevice" :wmax-ip="firstWmaxIp" :wmax-port="firstWmaxPort" />

    <!-- 基本设备管理面板 -->
    <div v-show="activeTab === 'basic'" class="flex gap-4">
      <!-- 左: 设备管理 -->
      <div class="flex-1">
        <div class="flex items-center justify-between mb-4">
          <h3 class="text-cyan-300 font-semibold">扫码器设备</h3>
          <div class="flex gap-2">
            <el-button size="small" @click="refreshStatus">刷新状态</el-button>
            <el-button size="small" type="primary" @click="handleAutoDiscover" :loading="discovering">
              搜索设备
            </el-button>
            <el-button size="small" type="success" @click="showAdd = true">手动添加</el-button>
            <el-button v-if="!hasWmaxDevice" size="small" type="warning" @click="createVirtualWmax">
              创建虚拟 WMax
            </el-button>
            <el-button v-if="hasVirtualWmax" size="small" type="danger" @click="deleteVirtualWmax">
              删除虚拟设备
            </el-button>
          </div>
        </div>

        <!-- 自动发现的设备 -->
        <div v-if="discoveredDevices.length > 0" class="mb-4">
          <div class="text-sm text-gray-400 mb-2">
            <span class="text-green-400 mr-1">●</span>
            自动发现的设备（启动时已自动连接）
          </div>
          <div class="grid grid-cols-2 gap-3">
            <div v-for="d in discoveredDevices" :key="d.ip"
                 class="bg-slate-800/60 rounded-lg border border-green-700/50 p-4">
              <div class="flex items-center justify-between mb-2">
                <span class="font-medium text-green-300">{{ d.name || 'WMax 设备' }}</span>
                <div class="flex items-center gap-1">
                  <el-tag type="primary" size="small" effect="dark">WMax</el-tag>
                  <el-tag :type="d.action === 'connected' || d.action === 'already_connected' ? 'success' : 'danger'" size="small">
                    {{ d.action === 'connected' ? '已连接' : d.action === 'already_connected' ? '已连接' : '失败' }}
                  </el-tag>
                </div>
              </div>
              <div class="text-xs text-gray-400 space-y-1">
                <div>IP: <span class="text-cyan-300 font-mono">{{ d.ip }}:{{ d.port }}</span></div>
                <div v-if="d.sn">SN: <span class="font-mono">{{ d.sn }}</span></div>
                <div v-if="d.error" class="text-red-400">{{ d.error }}</div>
              </div>
              <div class="flex gap-1 mt-3">
                <el-button v-if="d.action === 'connected' || d.action === 'already_connected'"
                           size="small" type="warning" @click="openWmaxForDiscovered(d)">
                  高级控制
                </el-button>
                <el-button v-if="d.action === 'failed'"
                           size="small" type="primary" @click="retryConnect(d)">
                  重试连接
                </el-button>
                <el-button size="small" @click="addDiscoveredToDb(d)">保存到设备列表</el-button>
              </div>
            </div>
          </div>
        </div>

        <!-- 数据库中配置的设备 -->
        <div v-if="devices.length > 0" class="text-sm text-gray-400 mb-2">已配置的设备</div>
        <div class="grid grid-cols-2 gap-3">
          <div v-for="dev in devices" :key="dev.id"
               class="bg-slate-800/60 rounded-lg border p-4 relative"
               :class="getDevStatus(dev.id) === 'connected' ? 'border-green-700' : 'border-slate-700'">
            <div class="flex items-center justify-between mb-2">
              <span class="font-medium">{{ dev.name }}</span>
              <div class="flex items-center gap-1">
                <el-tag v-if="getDevType(dev.id) === 'wmax'" type="primary" size="small" effect="dark">WMax</el-tag>
                <el-tag :type="getDevStatus(dev.id) === 'connected' ? 'success' : getDevStatus(dev.id) === 'connecting' ? 'warning' : 'danger'" size="small">
                  {{ { connected: '已连接', connecting: '连接中', disconnected: '断开', error: '错误' }[getDevStatus(dev.id)] || '未知' }}
                </el-tag>
              </div>
            </div>
            <div class="text-xs text-gray-400 space-y-1">
              <div>IP: {{ dev.ip }}:{{ dev.port }}</div>
              <div>工位: {{ dev.channel_id ?? '未绑定' }}
                <span v-if="dev.broadcast_channels && dev.broadcast_channels.length > 1" class="text-yellow-400 ml-1">
                  (广播: {{ dev.broadcast_channels.join(', ') }})
                </span>
              </div>
              <div>解析: {{ dev.parse_mode }}</div>
              <div v-if="getDevLastScan(dev.id)" class="text-cyan-300">
                最近: {{ getDevLastScan(dev.id) }}
              </div>
            </div>
            <div class="flex gap-1 mt-3">
              <el-button size="small" @click="testDevice(dev)">测试</el-button>
              <el-button size="small" type="primary" @click="editDevice(dev)">编辑</el-button>
              <el-button v-if="getDevType(dev.id) === 'wmax'" size="small" type="warning"
                         @click="openWmaxPanel(dev)">高级控制</el-button>
              <el-button size="small" type="danger" @click="handleDelete(dev)">删除</el-button>
            </div>
          </div>
        </div>
      </div>

      <!-- 右: 扫码记录 -->
      <div class="w-[400px] bg-slate-800/50 rounded-lg border border-slate-700 p-4 overflow-auto">
        <div class="flex items-center justify-between mb-3">
          <h3 class="text-cyan-300 font-semibold">扫码记录</h3>
          <div class="flex gap-1">
            <el-button size="small" @click="loadLogs">刷新</el-button>
            <el-button size="small" type="danger" @click="handleClearLogs">清空</el-button>
          </div>
        </div>
        <div class="flex items-center gap-2 text-xs text-gray-400 mb-2 bg-slate-900/60 rounded px-2 py-1.5">
          <span>去重间隔:</span>
          <el-input-number
            v-model="quickDedup"
            :min="0" :max="60" size="small"
            class="!w-20"
            controls-position="right"
            @change="applyQuickDedup"
          />
          <span>秒 — 相同条码在此时间内重复扫入将忽略</span>
        </div>
        <div v-if="logs.length === 0" class="text-gray-500 text-sm text-center py-8">暂无记录</div>
        <div v-else class="space-y-2">
          <div v-for="log in logs" :key="log.id"
               class="bg-slate-700/40 rounded p-2 text-xs"
               :class="log.success ? '' : 'border border-red-800/30'">
            <div class="flex justify-between">
              <span class="font-mono text-cyan-200">{{ log.parsed_serial || log.raw_data }}</span>
              <span class="text-gray-500">{{ formatTime(log.created_at) }}</span>
            </div>
            <div class="text-gray-400 mt-1">
              原始: {{ log.raw_data }}
              <span v-if="!log.success" class="text-red-400 ml-2">{{ log.error_msg }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 添加/编辑设备对话框 -->
    <el-dialog v-model="showAdd" :title="editingId ? '编辑设备' : '添加设备'" width="520px" class="mes-dialog" destroy-on-close>
      <el-form :model="form" label-width="80px" size="small">
        <!-- 上部：大输入框 -->
        <el-form-item label="名称" required>
          <el-input v-model="form.name" placeholder="如: 工位1扫码器" />
        </el-form-item>
        <el-form-item label="IP 地址" required>
          <el-input v-model="form.ip" placeholder="192.168.1.100" />
        </el-form-item>
        <el-form-item label="解析模式">
          <el-select v-model="form.parse_mode" class="w-full">
            <el-option label="直接使用" value="direct" />
            <el-option label="分隔符" value="separator" />
            <el-option label="正则" value="regex" />
          </el-select>
        </el-form-item>
        <el-form-item label="重复扫码">
          <el-select v-model="form.duplicate_scan_action" class="w-full">
            <el-option label="覆盖（默认）" value="overwrite" />
            <el-option label="拒绝（已有待检码时忽略新码）" value="reject" />
            <el-option label="排队（多件连扫依次检测）" value="queue" />
          </el-select>
        </el-form-item>
        <el-form-item label="误检重绑">
          <el-select v-model="form.rebind_mode" class="w-full">
            <el-option label="重新扫码（默认）" value="rescan" />
            <el-option label="自动重绑（NG 后自动放回待检）" value="auto_rebind" />
            <el-option label="手动选择（弹窗询问）" value="manual" />
          </el-select>
        </el-form-item>
        <el-form-item label="绑定时机">
          <el-select v-model="form.bind_timing" class="w-full">
            <el-option label="中途绑定（默认，扫码立即绑当前周期）" value="mid_cycle" />
            <el-option label="下周期绑定（扫码后等下个周期开始才绑）" value="cycle_start" />
          </el-select>
        </el-form-item>
        <el-form-item label="广播工位">
          <el-select v-model="form.broadcast_channels" multiple placeholder="留空则只发给绑定工位" class="w-full">
            <el-option v-for="ch in [0,1,2,3]" :key="ch" :label="'工位 ' + ch" :value="ch" />
          </el-select>
          <div class="text-xs text-gray-500 mt-1">选多个工位时，扫码结果同时发送到所有选中工位（同箱双工位场景）</div>
        </el-form-item>

        <!-- 中部：数字输入框并排 -->
        <div class="grid grid-cols-3 gap-3 my-3">
          <div class="text-center">
            <div class="text-xs text-gray-400 mb-1">端口</div>
            <el-input-number v-model="form.port" :min="1" :max="65535" size="small" class="!w-full" controls-position="right" />
          </div>
          <div class="text-center">
            <div class="text-xs text-gray-400 mb-1">绑定工位</div>
            <el-input-number v-model="form.channel_id" :min="0" size="small" class="!w-full" controls-position="right" />
          </div>
          <div class="text-center">
            <div class="text-xs text-gray-400 mb-1">去重间隔(秒)</div>
            <el-input-number v-model="form.dedup_interval_sec" :min="0" :max="60" size="small" class="!w-full" controls-position="right" />
          </div>
        </div>

        <!-- 下部：开关并排 -->
        <el-divider class="!my-2" />
        <div class="grid grid-cols-3 gap-x-4 gap-y-2 px-1">
          <div class="flex items-center gap-1.5">
            <el-switch v-model="form.enabled" size="small" />
            <span class="text-xs text-gray-300">启用</span>
          </div>
          <div class="flex items-center gap-1.5">
            <el-switch v-model="form.scan_required" size="small" />
            <span class="text-xs text-gray-300">先扫后检</span>
          </div>
          <div class="flex items-center gap-1.5">
            <el-switch v-model="form.warn_no_barcode" size="small" />
            <span class="text-xs text-gray-300">无码告警</span>
          </div>
          <div class="flex items-center gap-1.5">
            <el-switch v-model="form.auto_create_workpiece" size="small" />
            <span class="text-xs text-gray-300">自动建工件</span>
          </div>
          <div class="flex items-center gap-1.5">
            <el-switch v-model="form.auto_link_order" size="small" />
            <span class="text-xs text-gray-300">关联工单</span>
          </div>
          <div class="flex items-center gap-1.5">
            <el-switch v-model="systemStore.detection.toasts.scan.enabled" size="small" />
            <span class="text-xs text-gray-300">扫码提示框</span>
          </div>
        </div>
      </el-form>
      <template #footer>
        <el-button size="small" @click="showAdd = false">取消</el-button>
        <el-button size="small" type="primary" @click="handleSave" :loading="saving">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  getScannerDevices, createScannerDevice, updateScannerDevice,
  deleteScannerDevice, testScannerConnection, getScannerStatus, getScanLogs,
  clearScanLogs
} from '@/api/scanner'
import {
  wmaxCreateVirtual, wmaxDeleteVirtual, wmaxAutoDiscover,
  connectWMaxDevice
} from '@/api/wmax'
import WMaxPanel from './WMaxPanel.vue'
import { useSystemStore } from '@/store/useSystemStore'

const systemStore = useSystemStore()

const activeTab = ref('basic')
const devices = ref([])
const statusMap = ref({})
const discoveredDevices = ref([])
const discovering = ref(false)
const logs = ref([])
const showAdd = ref(false)
const editingId = ref(null)
const saving = ref(false)

const defaultForm = () => ({
  name: '', ip: '', port: 55256, channel_id: 0, enabled: true,
  parse_mode: 'direct', dedup_interval_sec: 2,
  auto_create_workpiece: true, auto_link_order: true,
  scan_required: false, duplicate_scan_action: 'overwrite', warn_no_barcode: false,
  rebind_mode: 'rescan',
  bind_timing: 'mid_cycle',
  broadcast_channels: [],
})
const form = ref(defaultForm())

const quickDedup = ref(2)

const formatTime = (t) => t ? t.replace('T', ' ').substring(0, 19) : '-'

const getDevStatus = (id) => statusMap.value[id]?.status || 'disconnected'
const getDevType = (id) => statusMap.value[id]?.device_type || 'text'
const getDevLastScan = (id) => statusMap.value[id]?.last_scan || ''

const hasWmaxDevice = computed(() =>
  Object.values(statusMap.value).some(s => s.device_type === 'wmax')
)
const hasVirtualWmax = computed(() =>
  Object.values(statusMap.value).some(s => s.device_type === 'wmax' && s.device_id === -999)
)
const firstWmaxIp = computed(() => {
  const s = Object.values(statusMap.value).find(s => s.device_type === 'wmax')
  return s?.ip || ''
})
const firstWmaxPort = computed(() => {
  return 55266
})

const openWmaxPanel = (dev) => {
  activeTab.value = 'wmax'
}

const openWmaxForDiscovered = (d) => {
  activeTab.value = 'wmax'
}

const handleAutoDiscover = async () => {
  discovering.value = true
  try {
    const res = await wmaxAutoDiscover(3)
    const data = res.data
    if (data.results && data.results.length > 0) {
      discoveredDevices.value = data.results
      ElMessage.success(`发现 ${data.total} 台设备，${data.injected} 台新连接`)
    } else {
      ElMessage.info('未发现 WMax 设备')
    }
    await refreshStatus()
  } catch (e) {
    ElMessage.error('搜索失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    discovering.value = false
  }
}

const retryConnect = async (d) => {
  try {
    const res = await connectWMaxDevice(d.ip, d.port)
    if (res.data.success) {
      d.action = 'connected'
      ElMessage.success(`${d.ip} 连接成功`)
      await refreshStatus()
    } else {
      ElMessage.error(res.data.message)
    }
  } catch (e) {
    ElMessage.error('连接失败')
  }
}

const addDiscoveredToDb = async (d) => {
  form.value = {
    ...defaultForm(),
    name: d.name || `WMax-${d.ip}`,
    ip: d.ip,
    port: d.port,
  }
  editingId.value = null
  showAdd.value = true
}

const createVirtualWmax = async () => {
  try {
    const res = await wmaxCreateVirtual()
    if (res.data.success) {
      ElMessage.success('虚拟 WMax 设备已创建，点击 WMax 高级控制查看功能')
      await refreshStatus()
      activeTab.value = 'wmax'
    }
  } catch (e) { ElMessage.error('创建失败') }
}

const deleteVirtualWmax = async () => {
  try {
    await wmaxDeleteVirtual()
    ElMessage.success('虚拟设备已删除')
    activeTab.value = 'basic'
    await refreshStatus()
  } catch {}
}

const loadDevices = async () => {
  try {
    const res = await getScannerDevices()
    devices.value = res.data || []
    if (devices.value.length > 0) {
      quickDedup.value = devices.value[0].dedup_interval_sec ?? 2
    }
  } catch {}
}

const applyQuickDedup = async (val) => {
  for (const dev of devices.value) {
    try {
      await updateScannerDevice(dev.id, { dedup_interval_sec: val })
    } catch {}
  }
  ElMessage.success(`去重间隔已更新为 ${val} 秒`)
}

const refreshStatus = async () => {
  try {
    const res = await getScannerStatus()
    const map = {}
    for (const s of (res.data || [])) {
      map[s.device_id] = s
    }
    statusMap.value = map
    mergeAutoDevices()
  } catch {}
}

const mergeAutoDevices = () => {
  for (const s of Object.values(statusMap.value)) {
    const isAutoDevice = s.device_id < -900
    if (isAutoDevice && !devices.value.find(d => d.id === s.device_id)) {
      devices.value.push({
        id: s.device_id, name: s.name || `WMax-${s.ip}`,
        ip: s.ip, port: s.port, channel_id: 0,
        parse_mode: 'direct', enabled: true,
        _auto: true,
      })
    }
  }
  devices.value = devices.value.filter(d => {
    if (d._auto && !statusMap.value[d.id]) return false
    return true
  })
}

const loadLogs = async () => {
  try {
    const res = await getScanLogs({ limit: 30 })
    logs.value = res.data.items || []
  } catch {}
}

const handleClearLogs = async () => {
  try {
    await ElMessageBox.confirm('确定清空所有扫码记录？此操作不可恢复。', '确认清空')
    await clearScanLogs()
    logs.value = []
    ElMessage.success('扫码记录已清空')
  } catch (e) {
    if (e !== 'cancel' && e !== 'close') {
      ElMessage.error('清空失败')
    }
  }
}

const testDevice = async (dev) => {
  try {
    const res = await testScannerConnection(dev.ip, dev.port)
    if (res.data.success) {
      const typeLabel = res.data.device_type === 'wmax' ? ' (WMax 设备)' : ' (文本设备)'
      ElMessage.success(res.data.message + typeLabel)
    } else {
      ElMessage.error(res.data.message)
    }
    refreshStatus()
  } catch (e) { ElMessage.error('测试失败') }
}

const handleSave = async () => {
  if (!form.value.name || !form.value.ip) {
    ElMessage.warning('请填写名称和IP地址')
    return
  }
  saving.value = true
  try {
    if (editingId.value) {
      await updateScannerDevice(editingId.value, form.value)
      ElMessage.success('设备已更新')
    } else {
      await createScannerDevice(form.value)
      ElMessage.success('设备已添加')
    }
    showAdd.value = false
    loadDevices()
    refreshStatus()
  } catch (e) { ElMessage.error(e.response?.data?.detail || '保存失败') }
  finally { saving.value = false }
}

const editDevice = (dev) => {
  editingId.value = dev.id
  form.value = { ...dev }
  showAdd.value = true
}

const handleDelete = async (dev) => {
  try {
    await ElMessageBox.confirm(`确定删除设备 ${dev.name}？`, '确认')
    await deleteScannerDevice(dev.id)
    ElMessage.success('设备已删除')
    loadDevices()
    refreshStatus()
  } catch (e) {
    if (e !== 'cancel' && e !== 'close') {
      ElMessage.error(`删除失败: ${e?.response?.data?.detail || e?.message || e}`)
    }
  }
}

let statusTimer = null
onMounted(() => {
  form.value = defaultForm()
  loadDevices()
  refreshStatus()
  loadLogs()
  statusTimer = setInterval(() => { refreshStatus(); loadLogs() }, 5000)
})
onUnmounted(() => { if (statusTimer) clearInterval(statusTimer) })
</script>

<style scoped>
.mes-dialog :deep(.el-dialog) { background: #1e293b; border: 1px solid #334155; }
.mes-dialog :deep(.el-dialog__title) { color: #e2e8f0; }
.mes-dialog :deep(.el-form-item__label) { color: #94a3b8; }
.scanner-tabs :deep(.el-tabs__item) { color: #94a3b8; }
.scanner-tabs :deep(.el-tabs__item.is-active) { color: #67e8f9; }
.scanner-tabs :deep(.el-tabs__active-bar) { background-color: #06b6d4; }
</style>
