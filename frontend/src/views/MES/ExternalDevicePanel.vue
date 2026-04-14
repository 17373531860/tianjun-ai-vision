<template>
  <div class="flex gap-4">
    <!-- 左：设备管理 -->
    <div class="flex-1">
      <div class="flex items-center justify-between mb-4">
        <h3 class="text-cyan-300 font-semibold">外部设备</h3>
        <el-button size="small" type="success" @click="openAdd">添加设备</el-button>
      </div>

      <div v-if="devices.length === 0" class="text-gray-500 text-sm text-center py-8">
        暂无外部设备，点击「添加设备」接入称重器、传感器等
      </div>
      <div class="grid grid-cols-2 gap-3">
        <div v-for="dev in devices" :key="dev.id"
             class="bg-slate-800/60 rounded-lg border p-4"
             :class="getStatus(dev.id) === 'connected' ? 'border-green-700' : 'border-slate-700'">
          <div class="flex items-center justify-between mb-2">
            <span class="font-medium">{{ dev.name }}</span>
            <div class="flex items-center gap-1">
              <el-tag :type="roleTagType(dev.device_role)" size="small" effect="dark">
                {{ roleLabel(dev.device_role) }}
              </el-tag>
              <el-tag size="small" effect="plain">{{ dev.protocol.toUpperCase() }}</el-tag>
              <el-tag :type="statusTagType(getStatus(dev.id))" size="small">
                {{ statusLabel(getStatus(dev.id)) }}
              </el-tag>
            </div>
          </div>
          <div class="text-xs text-gray-400 space-y-1">
            <div v-if="dev.ip">地址: {{ dev.ip }}:{{ dev.port }}</div>
            <div v-if="dev.serial_port">串口: {{ dev.serial_port }} @ {{ dev.serial_baud }}</div>
            <div v-if="dev.station_id">工位: {{ dev.station_id }}</div>
            <div>解析: {{ dev.parse_mode }} | 目标: {{ targetLabel(dev.data_target) }}</div>
            <div v-if="getLastData(dev.id)" class="text-cyan-300 truncate">
              最近: {{ getLastData(dev.id) }}
            </div>
          </div>
          <div class="flex gap-1 mt-3">
            <el-button size="small" @click="testDev(dev)">测试</el-button>
            <el-button size="small" type="primary" @click="editDev(dev)">编辑</el-button>
            <el-button size="small" type="danger" @click="handleDelete(dev)">删除</el-button>
          </div>
        </div>
      </div>
    </div>

    <!-- 右：数据日志 -->
    <div class="w-[400px] bg-slate-800/50 rounded-lg border border-slate-700 p-4 overflow-auto">
      <div class="flex items-center justify-between mb-3">
        <h3 class="text-cyan-300 font-semibold">数据日志</h3>
        <div class="flex gap-1">
          <el-button size="small" @click="loadLogs">刷新</el-button>
          <el-button size="small" type="danger" @click="handleClearLogs">清空</el-button>
        </div>
      </div>
      <div v-if="logs.length === 0" class="text-gray-500 text-sm text-center py-8">暂无记录</div>
      <div v-else class="space-y-2">
        <div v-for="log in logs" :key="log.id"
             class="bg-slate-700/40 rounded p-2 text-xs"
             :class="log.is_valid ? '' : 'border border-red-800/30'">
          <div class="flex justify-between">
            <span class="font-mono text-cyan-200 truncate" :title="log.raw_data">{{ log.raw_data }}</span>
            <span class="text-gray-500 whitespace-nowrap ml-2">{{ formatTime(log.created_at) }}</span>
          </div>
          <div v-if="log.parsed_data" class="text-gray-400 mt-1 truncate">
            {{ JSON.stringify(log.parsed_data) }}
          </div>
          <div v-if="log.box_serial" class="text-green-400 mt-0.5">箱码: {{ log.box_serial }}</div>
          <div v-if="!log.is_valid" class="text-red-400 mt-0.5">{{ log.error_msg }}</div>
        </div>
      </div>
    </div>

    <!-- 添加/编辑对话框 -->
    <el-dialog v-model="showDialog" :title="editingId ? '编辑设备' : '添加设备'"
               width="580px" class="mes-dialog" destroy-on-close>
      <el-form :model="form" label-width="90px" size="small">
        <el-form-item label="名称" required>
          <el-input v-model="form.name" placeholder="如: 工位3称重器" />
        </el-form-item>

        <div class="grid grid-cols-2 gap-4">
          <el-form-item label="设备类型">
            <el-select v-model="form.device_role" class="w-full">
              <el-option label="称重器" value="weight" />
              <el-option label="传感器" value="sensor" />
              <el-option label="PLC" value="plc" />
              <el-option label="自定义" value="custom" />
            </el-select>
          </el-form-item>
          <el-form-item label="通信协议">
            <el-select v-model="form.protocol" class="w-full">
              <el-option label="TCP 直连" value="tcp" />
              <el-option label="Modbus TCP" value="modbus_tcp" />
              <el-option label="串口 (RS232/485)" value="serial" />
              <el-option label="HTTP 轮询" value="http_poll" />
            </el-select>
          </el-form-item>
        </div>

        <!-- TCP / Modbus 地址 -->
        <div v-if="form.protocol === 'tcp' || form.protocol === 'modbus_tcp'" class="grid grid-cols-2 gap-4">
          <el-form-item label="IP 地址">
            <el-input v-model="form.ip" placeholder="192.168.0.200" />
          </el-form-item>
          <el-form-item label="端口">
            <el-input-number v-model="form.port" :min="1" :max="65535" class="!w-full" controls-position="right" />
          </el-form-item>
        </div>

        <!-- 串口 -->
        <div v-if="form.protocol === 'serial'" class="grid grid-cols-2 gap-4">
          <el-form-item label="串口号">
            <el-input v-model="form.serial_port" placeholder="COM3 或 /dev/ttyUSB0" />
          </el-form-item>
          <el-form-item label="波特率">
            <el-select v-model="form.serial_baud" class="w-full">
              <el-option v-for="b in [9600,19200,38400,57600,115200]" :key="b" :label="b" :value="b" />
            </el-select>
          </el-form-item>
        </div>

        <!-- HTTP -->
        <el-form-item v-if="form.protocol === 'http_poll'" label="URL">
          <el-input v-model="httpUrl" placeholder="http://192.168.0.200/api/weight" />
        </el-form-item>

        <el-divider class="!my-2" />

        <div class="grid grid-cols-2 gap-4">
          <el-form-item label="解析模式">
            <el-select v-model="form.parse_mode" class="w-full">
              <el-option label="直接取值" value="direct" />
              <el-option label="分隔符拆分" value="split" />
              <el-option label="正则提取" value="regex" />
              <el-option label="JSON 字段" value="json_path" />
            </el-select>
          </el-form-item>
          <el-form-item label="数据流向">
            <el-select v-model="form.data_target" class="w-full">
              <el-option label="集群汇总" value="cluster" />
              <el-option label="MES 扩展字段" value="extra_fields" />
              <el-option label="两者都发" value="both" />
            </el-select>
          </el-form-item>
        </div>

        <div class="grid grid-cols-2 gap-4">
          <el-form-item label="工位标识">
            <el-input v-model="form.station_id" placeholder="如 C（用于集群汇总）" />
          </el-form-item>
          <el-form-item label="绑定工位">
            <el-input-number v-model="form.channel_id" :min="0" class="!w-full" controls-position="right" />
          </el-form-item>
        </div>

        <!-- 分隔符解析配置 -->
        <div v-if="form.parse_mode === 'split'" class="bg-slate-900/50 rounded p-3 mb-3">
          <div class="text-xs text-gray-400 mb-2">分隔符配置（如数据格式: BOX-001,25.30）</div>
          <div class="grid grid-cols-3 gap-2">
            <div>
              <div class="text-xs text-gray-500 mb-1">分隔符</div>
              <el-input v-model="splitDelimiter" size="small" placeholder="," />
            </div>
            <div>
              <div class="text-xs text-gray-500 mb-1">条码位置</div>
              <el-input-number v-model="splitBarcodeIdx" :min="0" size="small" class="!w-full" controls-position="right" />
            </div>
            <div>
              <div class="text-xs text-gray-500 mb-1">数值位置</div>
              <el-input-number v-model="splitValueIdx" :min="0" size="small" class="!w-full" controls-position="right" />
            </div>
          </div>
        </div>

        <!-- 范围校验 -->
        <div v-if="form.device_role === 'weight'" class="bg-slate-900/50 rounded p-3 mb-3">
          <div class="text-xs text-gray-400 mb-2">重量范围校验（可选，超出范围标记 NG）</div>
          <div class="grid grid-cols-2 gap-3">
            <div>
              <div class="text-xs text-gray-500 mb-1">最小 (kg)</div>
              <el-input-number v-model="weightMin" :min="0" :precision="2" size="small" class="!w-full" controls-position="right" />
            </div>
            <div>
              <div class="text-xs text-gray-500 mb-1">最大 (kg)</div>
              <el-input-number v-model="weightMax" :min="0" :precision="2" size="small" class="!w-full" controls-position="right" />
            </div>
          </div>
        </div>

        <el-form-item>
          <el-switch v-model="form.enabled" size="small" />
          <span class="text-xs text-gray-300 ml-2">启用</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button size="small" @click="showDialog = false">取消</el-button>
        <el-button size="small" type="primary" @click="handleSave" :loading="saving">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  getExternalDevices, createExternalDevice, updateExternalDevice,
  deleteExternalDevice, getExternalDeviceStatus, testExternalDevice,
  getExternalDeviceLogs, clearExternalDeviceLogs
} from '@/api/external_device'

const devices = ref([])
const statusMap = ref({})
const logs = ref([])
const showDialog = ref(false)
const editingId = ref(null)
const saving = ref(false)

const defaultForm = () => ({
  name: '', device_role: 'weight', protocol: 'tcp',
  ip: '', port: 502, serial_port: '', serial_baud: 9600,
  protocol_config: {}, parse_mode: 'direct', parse_config: {},
  station_id: '', channel_id: 0, data_target: 'cluster',
  validation_rules: {}, enabled: true,
})
const form = ref(defaultForm())

const splitDelimiter = ref(',')
const splitBarcodeIdx = ref(0)
const splitValueIdx = ref(1)
const weightMin = ref(0)
const weightMax = ref(100)
const httpUrl = ref('')

const formatTime = (t) => t ? t.replace('T', ' ').substring(0, 19) : '-'

const roleLabel = (r) => ({ weight: '称重器', sensor: '传感器', plc: 'PLC', custom: '自定义' }[r] || r)
const roleTagType = (r) => ({ weight: 'warning', sensor: 'info', plc: 'success', custom: '' }[r] || '')
const statusLabel = (s) => ({ connected: '已连接', connecting: '连接中', disconnected: '断开', error: '错误' }[s] || '未知')
const statusTagType = (s) => ({ connected: 'success', connecting: 'warning', disconnected: 'danger', error: 'danger' }[s] || 'info')
const targetLabel = (t) => ({ cluster: '集群汇总', extra_fields: 'MES扩展', both: '两者' }[t] || t)

const getStatus = (id) => statusMap.value[id]?.status || 'disconnected'
const getLastData = (id) => statusMap.value[id]?.last_data || ''

const buildFormData = () => {
  const data = { ...form.value }
  if (data.protocol === 'http_poll') {
    data.protocol_config = { ...data.protocol_config, url: httpUrl.value }
  }
  if (data.parse_mode === 'split') {
    data.parse_config = {
      delimiter: splitDelimiter.value,
      fields: { barcode: splitBarcodeIdx.value, weight: splitValueIdx.value }
    }
  }
  if (data.device_role === 'weight' && (weightMin.value > 0 || weightMax.value > 0)) {
    data.validation_rules = { weight: {} }
    if (weightMin.value > 0) data.validation_rules.weight.min = weightMin.value
    if (weightMax.value > 0) data.validation_rules.weight.max = weightMax.value
  }
  return data
}

const openAdd = () => {
  editingId.value = null
  form.value = defaultForm()
  splitDelimiter.value = ','
  splitBarcodeIdx.value = 0
  splitValueIdx.value = 1
  weightMin.value = 0
  weightMax.value = 100
  httpUrl.value = ''
  showDialog.value = true
}

const editDev = (dev) => {
  editingId.value = dev.id
  form.value = { ...dev }
  const pc = dev.parse_config || {}
  if (dev.parse_mode === 'split') {
    splitDelimiter.value = pc.delimiter || ','
    splitBarcodeIdx.value = pc.fields?.barcode ?? 0
    splitValueIdx.value = pc.fields?.weight ?? 1
  }
  const vr = dev.validation_rules?.weight || {}
  weightMin.value = vr.min || 0
  weightMax.value = vr.max || 100
  httpUrl.value = dev.protocol_config?.url || ''
  showDialog.value = true
}

const handleSave = async () => {
  if (!form.value.name) { ElMessage.warning('请填写名称'); return }
  saving.value = true
  try {
    const data = buildFormData()
    if (editingId.value) {
      await updateExternalDevice(editingId.value, data)
      ElMessage.success('设备已更新')
    } else {
      await createExternalDevice(data)
      ElMessage.success('设备已添加')
    }
    showDialog.value = false
    loadDevices()
    refreshStatus()
  } catch (e) { ElMessage.error(e.response?.data?.detail || '保存失败') }
  finally { saving.value = false }
}

const handleDelete = async (dev) => {
  try {
    await ElMessageBox.confirm(`确定删除 ${dev.name}？`, '确认')
    await deleteExternalDevice(dev.id)
    ElMessage.success('已删除')
    loadDevices()
    refreshStatus()
  } catch (e) {
    if (e !== 'cancel' && e !== 'close') ElMessage.error('删除失败')
  }
}

const testDev = async (dev) => {
  try {
    const res = await testExternalDevice({
      protocol: dev.protocol, ip: dev.ip, port: dev.port,
      serial_port: dev.serial_port, protocol_config: dev.protocol_config,
    })
    if (res.data.success) ElMessage.success(res.data.message)
    else ElMessage.error(res.data.message)
  } catch (e) { ElMessage.error('测试失败') }
}

const loadDevices = async () => {
  try { devices.value = (await getExternalDevices()).data || [] } catch {}
}
const refreshStatus = async () => {
  try {
    const res = await getExternalDeviceStatus()
    const map = {}
    for (const s of (res.data || [])) map[s.device_id] = s
    statusMap.value = map
  } catch {}
}
const loadLogs = async () => {
  try { logs.value = (await getExternalDeviceLogs({ limit: 30 })).data.items || [] } catch {}
}
const handleClearLogs = async () => {
  try {
    await ElMessageBox.confirm('确定清空所有日志？', '确认')
    await clearExternalDeviceLogs()
    logs.value = []
    ElMessage.success('已清空')
  } catch (e) {
    if (e !== 'cancel' && e !== 'close') ElMessage.error('清空失败')
  }
}

let timer = null
onMounted(() => {
  loadDevices(); refreshStatus(); loadLogs()
  timer = setInterval(() => { refreshStatus(); loadLogs() }, 5000)
})
onUnmounted(() => { if (timer) clearInterval(timer) })
</script>

<style scoped>
.mes-dialog :deep(.el-dialog) { background: #1e293b; border: 1px solid #334155; }
.mes-dialog :deep(.el-dialog__title) { color: #e2e8f0; }
.mes-dialog :deep(.el-form-item__label) { color: #94a3b8; }
</style>
