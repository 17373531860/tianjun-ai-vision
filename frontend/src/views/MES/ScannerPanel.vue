<template>
  <div>
    <div class="flex gap-4">
      <!-- 左: 设备管理 -->
      <div class="flex-1">
        <div class="flex items-center justify-between mb-4">
          <h3 class="text-cyan-300 font-semibold">扫码器设备</h3>
          <div class="flex gap-2">
            <el-button size="small" @click="refreshStatus">刷新状态</el-button>
            <el-button size="small" type="success" @click="showAdd = true">添加设备</el-button>
          </div>
        </div>

        <div class="grid grid-cols-2 gap-3">
          <div v-for="dev in devices" :key="dev.id"
               class="bg-slate-800/60 rounded-lg border p-4 relative"
               :class="getDevStatus(dev.id) === 'connected' ? 'border-green-700' : 'border-slate-700'">
            <div class="flex items-center justify-between mb-2">
              <span class="font-medium">{{ dev.name }}</span>
              <el-tag :type="getDevStatus(dev.id) === 'connected' ? 'success' : getDevStatus(dev.id) === 'connecting' ? 'warning' : 'danger'" size="small">
                {{ { connected: '已连接', connecting: '连接中', disconnected: '断开', error: '错误' }[getDevStatus(dev.id)] || '未知' }}
              </el-tag>
            </div>
            <div class="text-xs text-gray-400 space-y-1">
              <div>IP: {{ dev.ip }}:{{ dev.port }}</div>
              <div>工位: {{ dev.channel_id ?? '未绑定' }}</div>
              <div>解析: {{ dev.parse_mode }}</div>
              <div v-if="getDevLastScan(dev.id)" class="text-cyan-300">
                最近: {{ getDevLastScan(dev.id) }}
              </div>
            </div>
            <div class="flex gap-1 mt-3">
              <el-button size="small" @click="testDevice(dev)">测试</el-button>
              <el-button size="small" type="primary" @click="editDevice(dev)">编辑</el-button>
              <el-button size="small" type="danger" @click="handleDelete(dev)">删除</el-button>
            </div>
          </div>
        </div>
      </div>

      <!-- 右: 扫码记录 -->
      <div class="w-[400px] bg-slate-800/50 rounded-lg border border-slate-700 p-4 overflow-auto">
        <div class="flex items-center justify-between mb-3">
          <h3 class="text-cyan-300 font-semibold">扫码记录</h3>
          <el-button size="small" @click="loadLogs">刷新</el-button>
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
    <el-dialog v-model="showAdd" :title="editingId ? '编辑设备' : '添加设备'" width="480px" class="mes-dialog" destroy-on-close>
      <el-form :model="form" label-width="90px" size="small">
        <el-form-item label="名称" required>
          <el-input v-model="form.name" placeholder="如: 工位1扫码器" />
        </el-form-item>
        <el-form-item label="IP 地址" required>
          <el-input v-model="form.ip" placeholder="192.168.1.100" />
        </el-form-item>
        <el-form-item label="端口">
          <el-input-number v-model="form.port" :min="1" :max="65535" />
        </el-form-item>
        <el-form-item label="绑定工位">
          <el-input-number v-model="form.channel_id" :min="0" />
        </el-form-item>
        <el-form-item label="解析模式">
          <el-select v-model="form.parse_mode" class="w-full">
            <el-option label="直接使用" value="direct" />
            <el-option label="分隔符" value="separator" />
            <el-option label="正则" value="regex" />
          </el-select>
        </el-form-item>
        <el-form-item label="去重间隔">
          <el-input-number v-model="form.dedup_interval_sec" :min="0" :max="30" />
          <span class="text-xs text-gray-400 ml-2">秒</span>
        </el-form-item>
        <el-form-item label="自动建工件">
          <el-switch v-model="form.auto_create_workpiece" />
        </el-form-item>
        <el-form-item label="自动关联工单">
          <el-switch v-model="form.auto_link_order" />
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="form.enabled" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button size="small" @click="showAdd = false">取消</el-button>
        <el-button size="small" type="primary" @click="handleSave" :loading="saving">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  getScannerDevices, createScannerDevice, updateScannerDevice,
  deleteScannerDevice, testScannerConnection, getScannerStatus, getScanLogs
} from '@/api/scanner'

const devices = ref([])
const statusMap = ref({})
const logs = ref([])
const showAdd = ref(false)
const editingId = ref(null)
const saving = ref(false)

const defaultForm = () => ({
  name: '', ip: '', port: 55256, channel_id: 0, enabled: true,
  parse_mode: 'direct', dedup_interval_sec: 2,
  auto_create_workpiece: true, auto_link_order: true,
})
const form = ref(defaultForm())

const formatTime = (t) => t ? t.replace('T', ' ').substring(0, 19) : '-'

const getDevStatus = (id) => statusMap.value[id]?.status || 'disconnected'
const getDevLastScan = (id) => statusMap.value[id]?.last_scan || ''

const loadDevices = async () => {
  try {
    const res = await getScannerDevices()
    devices.value = res.data || []
  } catch {}
}

const refreshStatus = async () => {
  try {
    const res = await getScannerStatus()
    const map = {}
    for (const s of (res.data || [])) {
      map[s.device_id] = s
    }
    statusMap.value = map
  } catch {}
}

const loadLogs = async () => {
  try {
    const res = await getScanLogs({ limit: 30 })
    logs.value = res.data.items || []
  } catch {}
}

const testDevice = async (dev) => {
  try {
    const res = await testScannerConnection(dev.ip, dev.port)
    if (res.data.success) ElMessage.success(res.data.message)
    else ElMessage.error(res.data.message)
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
  } catch {}
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
</style>
