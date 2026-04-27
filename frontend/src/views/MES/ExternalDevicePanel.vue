<template>
  <div class="flex gap-4">
    <!-- 左：设备管理 -->
    <div class="flex-1">
      <div class="flex items-center justify-between mb-4">
        <h3 class="text-cyan-300 font-semibold">外部设备</h3>
        <div class="flex gap-2">
          <el-dropdown @command="applyPreset" size="small">
            <el-button size="small">预设模板 ▼</el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="weighing_modbus">称重器 - Modbus ASCII</el-dropdown-item>
                <el-dropdown-item command="weighing_continuous">称重器 - 连续接收</el-dropdown-item>
                <el-dropdown-item command="tcp_sensor">TCP 传感器</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
          <el-button v-if="systemStore.developerMode" size="small" type="warning" plain @click="showSimulate = true">
            模拟数据
          </el-button>
          <el-button size="small" type="success" @click="openAdd">添加设备</el-button>
        </div>
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
            <div>{{ channelLabel(dev.channel_id) }}<span v-if="dev.station_id" class="text-yellow-400 ml-2">(集群标识: {{ dev.station_id }})</span><span v-if="dev.pairing_group" class="text-purple-300 ml-2">(分组: {{ dev.pairing_group }})</span></div>
            <div>解析: {{ dev.parse_mode }} | 目标: {{ targetLabel(dev.data_target) }}</div>
            <div v-if="getLastData(dev.id)" class="text-cyan-300 truncate">
              最近: {{ getLastData(dev.id) }}
            </div>
          </div>
          <div class="flex gap-1 mt-3">
            <el-button size="small" @click="testDev(dev)" :loading="testingDeviceId === dev.id">测试</el-button>
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
          <el-button size="small" @click="loadLogs(true)" :loading="logsLoading">刷新</el-button>
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
          <div v-if="log.box_serial" class="text-green-400 mt-0.5">目标编号: {{ log.box_serial }}</div>
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
            <el-select v-model="form.protocol" class="w-full" @change="onProtocolChange">
              <el-option label="TCP 直连" value="tcp" />
              <el-option label="Modbus TCP" value="modbus_tcp" />
              <el-option label="串口 (RS232/485)" value="serial" />
              <el-option label="串口 Modbus ASCII" value="serial_modbus_ascii" />
              <el-option label="串口连续接收" value="serial_continuous" />
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
            <el-input-number v-model="form.port" :min="0" :precision="0" class="!w-full" controls-position="right" />
          </el-form-item>
        </div>

        <!-- 串口 -->
        <div v-if="isSerialProtocol">
          <div class="grid grid-cols-2 gap-4">
            <el-form-item label="串口号">
              <el-input v-model="form.serial_port" placeholder="COM3 或 /dev/ttyUSB0" />
            </el-form-item>
            <el-form-item label="波特率">
              <el-select v-model="form.serial_baud" class="w-full">
                <el-option v-for="b in [1200,2400,4800,9600,19200,38400,57600,115200]" :key="b" :label="b" :value="b" />
              </el-select>
            </el-form-item>
          </div>
          <div class="grid grid-cols-3 gap-4">
            <el-form-item label="数据位">
              <el-select v-model="serialBytesize" class="w-full">
                <el-option :value="5" label="5" />
                <el-option :value="6" label="6" />
                <el-option :value="7" label="7" />
                <el-option :value="8" label="8" />
              </el-select>
            </el-form-item>
            <el-form-item label="校验">
              <el-select v-model="serialParity" class="w-full">
                <el-option value="N" label="无 (N)" />
                <el-option value="E" label="偶 (E)" />
                <el-option value="O" label="奇 (O)" />
                <el-option value="M" label="标记 (M)" />
                <el-option value="S" label="空格 (S)" />
              </el-select>
            </el-form-item>
            <el-form-item label="停止位">
              <el-select v-model="serialStopbits" class="w-full">
                <el-option :value="1" label="1" />
                <el-option :value="1.5" label="1.5" />
                <el-option :value="2" label="2" />
              </el-select>
            </el-form-item>
          </div>
        </div>

        <!-- Modbus ASCII 参数 -->
        <div v-if="form.protocol === 'serial_modbus_ascii'" class="bg-slate-900/50 rounded p-3 mb-3">
          <div class="text-xs text-gray-400 mb-2">Modbus ASCII 主从参数</div>
          <div class="grid grid-cols-3 gap-2">
            <div>
              <div class="text-xs text-gray-500 mb-1">从站地址</div>
              <el-input-number v-model="modbusSlaveId" :min="0" :precision="2" size="small" class="!w-full" controls-position="right" />
            </div>
            <div>
              <div class="text-xs text-gray-500 mb-1">寄存器地址</div>
              <el-input-number v-model="modbusRegister" :min="0" :precision="0" size="small" class="!w-full" controls-position="right" />
            </div>
            <div>
              <div class="text-xs text-gray-500 mb-1">寄存器数量</div>
              <el-input-number v-model="modbusCount" :min="0" :precision="0" size="small" class="!w-full" controls-position="right" />
            </div>
          </div>
          <div class="grid grid-cols-3 gap-2 mt-2">
            <div>
              <div class="text-xs text-gray-500 mb-1">字节序</div>
              <el-select v-model="modbusByteOrder" size="small" class="w-full">
                <el-option label="大端 (H4H3L2L1)" value="H4H3L2L1" />
                <el-option label="小端 (L2L1H4H3)" value="L2L1H4H3" />
              </el-select>
            </div>
            <div>
              <div class="text-xs text-gray-500 mb-1">比例系数</div>
              <el-input-number v-model="modbusScale" :min="0" :precision="2" size="small" class="!w-full" controls-position="right" />
            </div>
            <div>
              <div class="text-xs text-gray-500 mb-1">轮询间隔 (秒)</div>
              <el-input-number v-model="modbusPollInterval" :min="0" :precision="2" size="small" class="!w-full" controls-position="right" />
            </div>
          </div>
        </div>

        <!-- 连续接收模式参数 -->
        <div v-if="form.protocol === 'serial_continuous'" class="bg-slate-900/50 rounded p-3 mb-3">
          <div class="text-xs text-gray-400 mb-2">连续接收模式（设备主动推送数据）</div>
          <div class="grid grid-cols-2 gap-2">
            <div>
              <div class="text-xs text-gray-500 mb-1">数据格式</div>
              <el-select v-model="continuousDataFormat" size="small" class="w-full">
                <el-option label="纯文本 ASCII" value="ascii" />
                <el-option label="Modbus ASCII 帧" value="modbus_ascii" />
              </el-select>
            </div>
            <div>
              <div class="text-xs text-gray-500 mb-1">行分隔符</div>
              <el-input v-model="continuousDelimiter" size="small" placeholder="\r\n" />
            </div>
          </div>
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
            <el-select v-model="form.channel_id" placeholder="选择工位" class="!w-full">
              <el-option v-for="opt in channelOptions" :key="opt.value" :label="opt.label" :value="opt.value" />
            </el-select>
          </el-form-item>
        </div>

        <el-form-item label="设备分组号">
          <el-input v-model="form.pairing_group" placeholder="如 scale-c；留空则按绑定工位自动配对" clearable>
            <template #append>
              <el-tooltip placement="top" :show-after="300">
                <template #content>
                  <div class="max-w-xs text-xs">
                    与扫码枪之间的<b>配对标识</b>，和"绑定工位"无关。<br>
                    本设备和对应扫码枪填相同分组号即配成一对，<br>
                    扫码数据会自动注入本设备（如称重器带上条码）。<br>
                    <b>留空则沿用老逻辑</b>——按"绑定工位"匹配。
                  </div>
                </template>
                <el-icon class="text-gray-400 cursor-help"><QuestionFilled /></el-icon>
              </el-tooltip>
            </template>
          </el-input>
        </el-form-item>

        <!-- 分隔符解析配置 -->
        <div v-if="form.parse_mode === 'split'" class="bg-slate-900/50 rounded p-3 mb-3">
          <div class="text-xs text-gray-400 mb-2">分隔符配置（如数据格式: SN-001,25.30）</div>
          <div class="grid grid-cols-3 gap-2">
            <div>
              <div class="text-xs text-gray-500 mb-1">分隔符</div>
              <el-input v-model="splitDelimiter" size="small" placeholder="," />
            </div>
            <div>
              <div class="text-xs text-gray-500 mb-1">条码位置</div>
              <el-input-number v-model="splitBarcodeIdx" :min="0" :precision="0" size="small" class="!w-full" controls-position="right" />
            </div>
            <div>
              <div class="text-xs text-gray-500 mb-1">数值位置</div>
              <el-input-number v-model="splitValueIdx" :min="0" :precision="0" size="small" class="!w-full" controls-position="right" />
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

        <!-- 稳定值判定（仅称重） -->
        <div v-if="form.device_role === 'weight'" class="bg-slate-900/50 rounded p-3 mb-3">
          <div class="flex items-center justify-between mb-2">
            <span class="text-xs text-gray-400">稳定值判定（抖动、空载数据将被过滤，不上报）</span>
            <el-switch v-model="form.stable_enabled" size="small" />
          </div>
          <div v-if="form.stable_enabled" class="grid grid-cols-3 gap-3">
            <div>
              <div class="text-xs text-gray-500 mb-1">稳定波动 ±Δ (kg)</div>
              <el-input-number v-model="form.stable_delta" :min="0.001" :step="0.01" :precision="3" size="small" class="!w-full" controls-position="right" />
            </div>
            <div>
              <div class="text-xs text-gray-500 mb-1">连续稳定次数</div>
              <el-input-number v-model="form.stable_count" :min="2" :max="30" :precision="0" size="small" class="!w-full" controls-position="right" />
            </div>
            <div>
              <div class="text-xs text-gray-500 mb-1">空载阈值 (kg)</div>
              <el-input-number v-model="form.zero_threshold" :min="0" :step="0.01" :precision="3" size="small" class="!w-full" controls-position="right" />
            </div>
          </div>
          <div class="text-xs text-gray-500 mt-2">
            连续 <b>{{ form.stable_count }}</b> 次读数的最大差 ≤ <b>{{ form.stable_delta }}</b> kg 才视为稳定，上报该窗口的中位数；|值| < <b>{{ form.zero_threshold }}</b> kg 视为空载并清空条码。
          </div>
        </div>

        <!-- 有重无码告警（仅称重） -->
        <div v-if="form.device_role === 'weight'" class="bg-slate-900/50 rounded p-3 mb-3">
          <div class="flex items-center justify-between mb-2">
            <span class="text-xs text-gray-400">有重无码告警（稳定值非零但未扫码超时触发）</span>
            <el-switch v-model="form.weight_no_barcode_alarm_enabled" size="small" />
          </div>
          <div v-if="form.weight_no_barcode_alarm_enabled" class="grid grid-cols-1 gap-3">
            <div>
              <div class="text-xs text-gray-500 mb-1">超时时间 (秒)</div>
              <el-input-number v-model="form.weight_no_barcode_alarm_delay_sec" :min="1" :max="600" :precision="0" size="small" class="!w-full" controls-position="right" />
              <div class="text-xs text-gray-500 mt-1">
                稳定重量 ≥ 空载阈值持续 <b>{{ form.weight_no_barcode_alarm_delay_sec }}</b> 秒仍未收到条码，则派发 weight_no_barcode 事件到 MES 并触发报警灯。默认关闭。
              </div>
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

    <el-dialog v-model="showSimulate" title="模拟外部设备数据（调试）" width="460px" class="mes-dialog" destroy-on-close>
      <el-form label-width="100px" size="small">
        <el-form-item label="设备">
          <el-select v-model="simulateForm.device_id" class="w-full" clearable placeholder="可不选，使用虚拟称重器">
            <el-option v-for="dev in devices" :key="dev.id" :label="dev.name" :value="dev.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="原始数据" required>
          <el-input v-model="simulateForm.raw_data" placeholder="12.34kg" />
        </el-form-item>
        <el-form-item label="条码">
          <el-input v-model="simulateForm.barcode" placeholder="AUTO_QA_BOX_001，可留空" clearable />
        </el-form-item>
        <el-form-item label="工位">
          <el-select v-model="simulateForm.channel_id" class="w-full">
            <el-option v-for="opt in channelOptions" :key="opt.value" :label="opt.label" :value="opt.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="真实链路">
          <el-switch v-model="simulateForm.full_chain" />
          <div class="text-xs text-gray-500 mt-1">关闭时只写日志；开启时会按设备配置进入 extra fields / 集群链路。</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button size="small" @click="showSimulate = false">取消</el-button>
        <el-button size="small" type="primary" @click="submitSimulateData" :loading="simulating">注入数据</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { QuestionFilled } from '@element-plus/icons-vue'
import {
  getExternalDevices, createExternalDevice, updateExternalDevice,
  deleteExternalDevice, getExternalDeviceStatus, testExternalDevice,
  getExternalDeviceLogs, clearExternalDeviceLogs, simulateExternalDeviceData
} from '@/api/external_device'
import { getWorkstations } from '@/api/detection'
import { useSystemStore } from '@/store/useSystemStore'

const systemStore = useSystemStore()
const devices = ref([])
const statusMap = ref({})
const logs = ref([])
const showDialog = ref(false)
const editingId = ref(null)
const saving = ref(false)
const logsLoading = ref(false)
const testingDeviceId = ref(null)
const showSimulate = ref(false)
const simulating = ref(false)
const channelCount = ref(1)
const channelOptions = computed(() =>
  Array.from({ length: channelCount.value }, (_, i) => ({
    value: i,
    label: `工位 ${i + 1}`,
  }))
)
const channelLabel = (ch) => (ch == null ? '未绑定' : `工位 ${ch + 1}`)

const defaultForm = () => ({
  name: '', device_role: 'weight', protocol: 'tcp',
  ip: '', port: 502, serial_port: '', serial_baud: 9600,
  protocol_config: {}, parse_mode: 'direct', parse_config: {},
  station_id: '', channel_id: 0, pairing_group: '', data_target: 'cluster',
  validation_rules: {}, enabled: true,
  stable_enabled: true,
  stable_delta: 0.05,
  stable_count: 5,
  zero_threshold: 0.05,
  weight_no_barcode_alarm_enabled: false,
  weight_no_barcode_alarm_delay_sec: 10,
})
const form = ref(defaultForm())
const simulateForm = ref({
  device_id: null,
  raw_data: '12.34kg',
  barcode: 'AUTO_QA_BOX_001',
  channel_id: 0,
  full_chain: false,
})

const splitDelimiter = ref(',')
const splitBarcodeIdx = ref(0)
const splitValueIdx = ref(1)
const weightMin = ref(0)
const weightMax = ref(100)
const httpUrl = ref('')

const modbusSlaveId = ref(1)
const modbusRegister = ref(41201)
const modbusCount = ref(2)
const modbusByteOrder = ref('H4H3L2L1')
const modbusScale = ref(1.0)
const modbusPollInterval = ref(0.5)
const continuousDataFormat = ref('ascii')
const continuousDelimiter = ref('\\r\\n')

// 串口通用参数（所有 serial_* 协议共用）
const serialBytesize = ref(8)
const serialParity = ref('N')
const serialStopbits = ref(1)

const isSerialProtocol = computed(() =>
  ['serial', 'serial_modbus_ascii', 'serial_continuous'].includes(form.value.protocol)
)

const onProtocolChange = (val) => {
  if (val === 'serial_modbus_ascii') {
    form.value.parse_mode = 'direct'
    form.value.device_role = 'weight'
  } else if (val === 'serial_continuous') {
    form.value.parse_mode = 'direct'
    form.value.device_role = 'weight'
  }
}

// 后端返回 naive UTC ISO（server_default=func.now()）。
// 按 UTC 解析 → 浏览器本地时区显示（国内 = 北京时间）。
const formatTime = (t) => {
  if (!t) return '-'
  const hasTz = /Z$|[+-]\d{2}:?\d{2}$/.test(t)
  const s = hasTz ? t : (String(t).replace(' ', 'T') + 'Z')
  const d = new Date(s)
  if (isNaN(d.getTime())) return t
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} `
    + `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

const roleLabel = (r) => ({ weight: '称重器', sensor: '传感器', plc: 'PLC', custom: '自定义' }[r] || r)
const roleTagType = (r) => ({ weight: 'warning', sensor: 'info', plc: 'success', custom: '' }[r] || '')
const statusLabel = (s) => ({ connected: '已连接', connecting: '连接中', disconnected: '断开', error: '错误' }[s] || '未知')
const statusTagType = (s) => ({ connected: 'success', connecting: 'warning', disconnected: 'danger', error: 'danger' }[s] || 'info')
const targetLabel = (t) => ({ cluster: '集群汇总', extra_fields: 'MES扩展', both: '两者' }[t] || t)

const getStatus = (id) => statusMap.value[id]?.status || 'disconnected'
const getLastData = (id) => statusMap.value[id]?.last_data || ''

const buildFormData = () => {
  const data = { ...form.value }
  // 去掉字符串字段的前后空格，避免肉眼看不见的空格导致连接失败（如 " /dev/ttyUSB0"）
  ;['name', 'serial_port', 'ip', 'station_id'].forEach(k => {
    if (typeof data[k] === 'string') data[k] = data[k].trim()
  })
  if (data.protocol === 'http_poll') {
    data.protocol_config = { ...data.protocol_config, url: httpUrl.value }
  }
  if (data.protocol === 'serial_modbus_ascii') {
    data.protocol_config = {
      ...data.protocol_config,
      slave_id: modbusSlaveId.value,
      register: modbusRegister.value,
      count: modbusCount.value,
      byte_order: modbusByteOrder.value,
      data_scale: modbusScale.value,
      poll_interval: modbusPollInterval.value,
    }
  }
  if (data.protocol === 'serial_continuous') {
    data.protocol_config = {
      ...data.protocol_config,
      data_format: continuousDataFormat.value,
      delimiter: continuousDelimiter.value,
      slave_id: modbusSlaveId.value,
      byte_order: modbusByteOrder.value,
      data_scale: modbusScale.value,
    }
  }
  // 所有串口协议统一注入 bytesize/parity/stopbits
  if (isSerialProtocol.value) {
    data.protocol_config = {
      ...data.protocol_config,
      bytesize: serialBytesize.value,
      parity: serialParity.value,
      stopbits: serialStopbits.value,
    }
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
  serialBytesize.value = 8
  serialParity.value = 'N'
  serialStopbits.value = 1
  showDialog.value = true
}

const editDev = (dev) => {
  editingId.value = dev.id
  form.value = { ...dev }
  const pc = dev.parse_config || {}
  const pcfg = dev.protocol_config || {}
  if (dev.parse_mode === 'split') {
    splitDelimiter.value = pc.delimiter || ','
    splitBarcodeIdx.value = pc.fields?.barcode ?? 0
    splitValueIdx.value = pc.fields?.weight ?? 1
  }
  if (dev.protocol === 'serial_modbus_ascii') {
    modbusSlaveId.value = pcfg.slave_id ?? 1
    modbusRegister.value = pcfg.register ?? 41201
    modbusCount.value = pcfg.count ?? 2
    modbusByteOrder.value = pcfg.byte_order ?? 'H4H3L2L1'
    modbusScale.value = pcfg.data_scale ?? 1.0
    modbusPollInterval.value = pcfg.poll_interval ?? 0.5
  }
  if (dev.protocol === 'serial_continuous') {
    continuousDataFormat.value = pcfg.data_format ?? 'ascii'
    continuousDelimiter.value = pcfg.delimiter ?? '\\r\\n'
    modbusSlaveId.value = pcfg.slave_id ?? 1
    modbusByteOrder.value = pcfg.byte_order ?? 'H4H3L2L1'
    modbusScale.value = pcfg.data_scale ?? 1.0
  }
  // 串口通用参数回填（所有 serial_* 协议）
  if (['serial', 'serial_modbus_ascii', 'serial_continuous'].includes(dev.protocol)) {
    serialBytesize.value = pcfg.bytesize ?? 8
    serialParity.value = pcfg.parity ?? 'N'
    serialStopbits.value = pcfg.stopbits ?? 1
  }
  const vr = dev.validation_rules?.weight || {}
  weightMin.value = vr.min || 0
  weightMax.value = vr.max || 100
  httpUrl.value = pcfg.url || ''
  showDialog.value = true
}

const applyPreset = (preset) => {
  openAdd()
  if (preset === 'weighing_modbus') {
    form.value.name = '称重器'
    form.value.device_role = 'weight'
    form.value.protocol = 'serial_modbus_ascii'
    form.value.serial_baud = 9600
    form.value.parse_mode = 'direct'
    modbusSlaveId.value = 1
    modbusRegister.value = 41203
    modbusCount.value = 2
    modbusByteOrder.value = 'H4H3L2L1'
    modbusScale.value = 0.01
    modbusPollInterval.value = 0.5
  } else if (preset === 'weighing_continuous') {
    form.value.name = '称重器 (连续)'
    form.value.device_role = 'weight'
    form.value.protocol = 'serial_continuous'
    form.value.serial_baud = 9600
    form.value.parse_mode = 'direct'
    continuousDataFormat.value = 'ascii'
    continuousDelimiter.value = '\\r\\n'
  } else if (preset === 'tcp_sensor') {
    form.value.name = '传感器'
    form.value.device_role = 'sensor'
    form.value.protocol = 'tcp'
    form.value.parse_mode = 'direct'
  }
}

const handleSave = async () => {
  if (!form.value.name) { ElMessage.warning('请填写名称'); return }
  saving.value = true
  try {
    const data = buildFormData()
    let resp
    if (editingId.value) {
      resp = await updateExternalDevice(editingId.value, data)
      ElMessage.success('设备已更新')
    } else {
      resp = await createExternalDevice(data)
      ElMessage.success('设备已添加')
    }
    const warning = resp?.data?.warning
    if (warning) {
      ElMessage({ type: 'warning', message: warning, duration: 6000 })
    }
    showDialog.value = false
    loadDevices()
    refreshStatus()
  } catch (e) {
    console.error('[ExtDev] save failed:', e, e?.response)
    let detail = ''
    if (e?.response) {
      const d = e.response.data?.detail
      if (typeof d === 'string') detail = d
      else if (d != null) { try { detail = JSON.stringify(d) } catch { detail = String(d) } }
      else detail = `HTTP ${e.response.status} ${e.response.statusText || ''}`.trim()
    } else if (e?.message) {
      detail = e.message
    } else {
      try { detail = JSON.stringify(e) } catch { detail = String(e) }
    }
    ElMessage.error(`保存失败: ${detail || '未知错误'}`)
  }
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
  testingDeviceId.value = dev.id
  try {
    const res = await testExternalDevice({
      protocol: dev.protocol, ip: dev.ip, port: dev.port,
      serial_port: dev.serial_port, serial_baud: dev.serial_baud || 9600,
      protocol_config: dev.protocol_config,
    })
    if (res.data.success) ElMessage.success(res.data.message)
    else ElMessage.error(res.data.message)
  } catch (e) {
    ElMessage.error('测试失败: ' + (e.response?.data?.detail || e.message || '网络错误'))
  } finally {
    testingDeviceId.value = null
  }
}

const submitSimulateData = async () => {
  const raw = (simulateForm.value.raw_data || '').trim()
  if (!raw) {
    ElMessage.warning('请填写模拟原始数据')
    return
  }
  simulating.value = true
  try {
    const { data } = await simulateExternalDeviceData({
      device_id: simulateForm.value.device_id || null,
      raw_data: raw,
      barcode: (simulateForm.value.barcode || '').trim() || null,
      channel_id: simulateForm.value.channel_id,
      full_chain: simulateForm.value.full_chain,
      repeat_count: simulateForm.value.full_chain ? 5 : 1,
    })
    ElMessage.success(data.message || '模拟数据已注入')
    showSimulate.value = false
    await Promise.all([refreshStatus(), loadLogs()])
  } catch (e) {
    ElMessage.error('模拟数据失败: ' + (e.response?.data?.detail || e.message || '未知错误'))
  } finally {
    simulating.value = false
  }
}

const loadDevices = async () => {
  try { devices.value = (await getExternalDevices()).data || [] }
  catch (e) { ElMessage.error('加载设备列表失败: ' + (e.response?.data?.detail || e.message || '网络错误')) }
}
const refreshStatus = async () => {
  try {
    const res = await getExternalDeviceStatus()
    const map = {}
    for (const s of (res.data || [])) map[s.device_id] = s
    statusMap.value = map
  } catch (e) { console.warn('[ExtDev] 刷新状态失败:', e.message) }
}
const loadLogs = async (showFeedback = false) => {
  if (showFeedback) logsLoading.value = true
  try {
    logs.value = (await getExternalDeviceLogs({ limit: 30 })).data.items || []
    if (showFeedback) ElMessage.success('外部设备日志已刷新')
  } catch (e) {
    const msg = e.response?.data?.detail || e.message || '网络错误'
    if (showFeedback) ElMessage.error('刷新外部设备日志失败: ' + msg)
    else console.warn('[ExtDev] 加载日志失败:', msg)
  } finally {
    if (showFeedback) logsLoading.value = false
  }
}
const handleClearLogs = async () => {
  try {
    await ElMessageBox.confirm('确定清空所有日志？', '确认')
  } catch (e) {
    return
  }
  try {
    await clearExternalDeviceLogs()
    logs.value = []
    ElMessage.success('已清空')
  } catch (e) {
    console.error('[ExtDev] clearExternalDeviceLogs failed:', e, e?.response)
    let detail = ''
    if (e?.response) {
      const d = e.response.data?.detail
      if (typeof d === 'string') detail = d
      else if (d != null) { try { detail = JSON.stringify(d) } catch { detail = String(d) } }
      else detail = `HTTP ${e.response.status} ${e.response.statusText || ''}`.trim()
    } else if (e?.message) {
      detail = e.message
    } else {
      try { detail = JSON.stringify(e) } catch { detail = String(e) }
    }
    ElMessage.error(`清空失败: ${detail || '未知错误'}`)
  }
}

let timer = null
const loadChannelCount = async () => {
  try {
    const res = await getWorkstations()
    channelCount.value = res.data.channel_count || 1
  } catch {
    channelCount.value = 1
  }
}
onMounted(() => {
  loadChannelCount()
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
