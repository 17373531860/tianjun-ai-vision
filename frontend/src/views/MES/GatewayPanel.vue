<template>
  <div>
    <!-- 工具栏 -->
    <div class="flex items-center justify-between mb-4">
      <div class="flex items-center gap-3">
        <span class="text-sm text-gray-300">外部 MES 连接</span>
        <el-tag size="small" :type="enabledCount > 0 ? 'success' : 'info'">
          {{ enabledCount }} 个已启用
        </el-tag>
      </div>
      <el-button size="small" type="success" @click="openCreate">新建连接</el-button>
    </div>

    <!-- 连接列表 -->
    <el-table :data="connections" stripe size="small" class="mes-table" max-height="calc(100vh - 300px)">
      <el-table-column prop="name" label="连接名称" width="140" />
      <el-table-column prop="adapter_type" label="类型" width="100">
        <template #default="{ row }">
          <el-tag size="small" :type="row.adapter_type === 'rest' ? '' : 'warning'">
            {{ row.adapter_type === 'rest' ? 'REST/JSON' : 'Form-Data' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="80">
        <template #default="{ row }">
          <el-switch v-model="row.enabled" size="small" @change="toggleEnabled(row)" />
        </template>
      </el-table-column>
      <el-table-column label="推送事件" width="180">
        <template #default="{ row }">
          <div class="flex gap-1 flex-wrap">
            <el-tag v-for="e in (row.push_events || [])" :key="e" size="small" type="info">
              {{ eventLabels[e] || e }}
            </el-tag>
            <span v-if="!row.push_events?.length" class="text-xs text-gray-500">未配置</span>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="重试" width="90">
        <template #default="{ row }">
          {{ row.retry_count }}次/{{ row.retry_interval_sec }}s
        </template>
      </el-table-column>
      <el-table-column label="最后同步" width="160">
        <template #default="{ row }">
          {{ row.last_sync_at ? formatTime(row.last_sync_at) : '-' }}
        </template>
      </el-table-column>
      <el-table-column label="操作" width="260" fixed="right">
        <template #default="{ row }">
          <div class="flex gap-1">
            <el-button size="small" @click="openEdit(row)">编辑</el-button>
            <el-button size="small" type="primary" @click="doTest(row)" :loading="row._testing">测试</el-button>
            <el-button size="small" type="info" @click="openLogs(row)">日志</el-button>
            <el-popconfirm title="确认删除?" @confirm="doDelete(row)">
              <template #reference>
                <el-button size="small" type="danger">删除</el-button>
              </template>
            </el-popconfirm>
          </div>
        </template>
      </el-table-column>
    </el-table>

    <!-- 新建/编辑对话框 -->
    <el-dialog
      v-model="showEditor"
      :title="editing ? '编辑连接' : '新建连接'"
      width="720px"
      destroy-on-close
    >
      <el-form :model="form" label-width="120px" size="small">
        <el-form-item label="连接名称" required>
          <el-input v-model="form.name" placeholder="如: 客户A MES" />
        </el-form-item>
        <el-form-item label="适配器类型">
          <el-radio-group v-model="form.adapter_type">
            <el-radio value="rest">REST / JSON</el-radio>
            <el-radio value="form-data">Form-Data</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="接口地址">
          <el-input v-model="configUrl" placeholder="http://192.168.50.12:11211/api/..." />
        </el-form-item>
        <el-form-item label="请求方法">
          <el-select v-model="configMethod" class="w-28">
            <el-option label="POST" value="POST" />
            <el-option label="PUT" value="PUT" />
            <el-option label="GET" value="GET" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="form.adapter_type === 'form-data'" label="表单字段名">
          <el-input v-model="configFormKey" placeholder="param" class="w-40" />
        </el-form-item>
        <el-form-item label="推送时机">
          <el-checkbox-group v-model="form.push_events">
            <el-checkbox value="cycle_end" label="检测周期结束" />
            <el-checkbox value="session_end" label="检测会话结束" />
          </el-checkbox-group>
        </el-form-item>
        <el-form-item label="重试次数">
          <el-input-number v-model="form.retry_count" :min="0" :max="10" />
        </el-form-item>
        <el-form-item label="重试间隔(秒)">
          <el-input-number v-model="form.retry_interval_sec" :min="1" :max="60" />
        </el-form-item>

        <!-- 静态字段 -->
        <el-divider content-position="left">静态字段</el-divider>
        <div class="mb-3 text-xs text-gray-400">
          固定值字段 (如设备编号), 格式: namespace.key = value
        </div>
        <div v-for="(sf, idx) in staticFields" :key="idx" class="flex items-center gap-2 mb-2">
          <el-input v-model="sf.key" placeholder="device.device_id" class="w-48" size="small" />
          <span class="text-gray-400">=</span>
          <el-input v-model="sf.value" placeholder="JC-M-1234" class="flex-1" size="small" />
          <el-button size="small" type="danger" circle @click="staticFields.splice(idx, 1)">
            <el-icon><Close /></el-icon>
          </el-button>
        </div>
        <el-button size="small" @click="staticFields.push({ key: '', value: '' })">+ 添加</el-button>

        <!-- 额外字段输入框定义 -->
        <el-divider content-position="left">动态额外字段 (Monitor 页输入)</el-divider>
        <div class="mb-3 text-xs text-gray-400">
          定义 Monitor 检测页面显示的额外输入框 (如重量), 数据通过 extra.xxx 引用
        </div>
        <div v-for="(ef, idx) in extraFieldsDef" :key="idx" class="flex items-center gap-2 mb-2">
          <el-input v-model="ef.key" placeholder="weight" class="w-28" size="small" />
          <el-input v-model="ef.label" placeholder="重量(g)" class="w-28" size="small" />
          <el-select v-model="ef.type" class="w-24" size="small">
            <el-option label="文本" value="text" />
            <el-option label="数字" value="number" />
          </el-select>
          <el-input v-model="ef.default" placeholder="默认值" class="w-24" size="small" />
          <el-button size="small" type="danger" circle @click="extraFieldsDef.splice(idx, 1)">
            <el-icon><Close /></el-icon>
          </el-button>
        </div>
        <el-button size="small" @click="extraFieldsDef.push({ key: '', label: '', type: 'text', default: '' })">+ 添加</el-button>

        <!-- 字段映射模板 -->
        <el-divider content-position="left">字段映射模板 (JSON)</el-divider>
        <div class="mb-3 text-xs text-gray-400">
          用 {context.field} 引用数据。可用: workpiece.*, cycle.*, order.*, project.*, steps[], defects[], extra.*, session.*, device.*, timestamp
        </div>
        <el-input
          v-model="templateJson"
          type="textarea"
          :rows="12"
          :autosize="{ minRows: 6, maxRows: 20 }"
          placeholder='{"macno": "{device.device_id}", "data": {...}}'
          spellcheck="false"
          class="template-editor"
        />
        <div v-if="templateError" class="text-xs text-red-400 mt-1">{{ templateError }}</div>

        <!-- 响应校验 -->
        <el-divider content-position="left">响应校验</el-divider>
        <div class="flex items-center gap-3">
          <span class="text-xs text-gray-400">成功字段:</span>
          <el-input v-model="successField" placeholder="success" class="w-32" size="small" />
          <span class="text-xs text-gray-400">期望值:</span>
          <el-select v-model="successExpect" class="w-24" size="small">
            <el-option label="true" :value="true" />
            <el-option label="false" :value="false" />
            <el-option label="1" :value="1" />
            <el-option label="0" :value="0" />
          </el-select>
        </div>
      </el-form>

      <template #footer>
        <el-button @click="showEditor = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="doSave">保存</el-button>
      </template>
    </el-dialog>

    <!-- 测试结果弹窗 -->
    <el-dialog v-model="showTestResult" title="测试结果" width="600px" destroy-on-close>
      <div v-if="testResult">
        <el-result
          :icon="testResult.success ? 'success' : 'error'"
          :title="testResult.success ? '连接成功' : '连接失败'"
          :sub-title="testResult.error || `HTTP ${testResult.status_code} (${testResult.duration_ms}ms)`"
        />
        <el-divider content-position="left">发送数据预览</el-divider>
        <pre class="text-xs bg-gray-900 p-3 rounded overflow-auto max-h-48">{{ formatJSON(testResult.payload_preview) }}</pre>
        <el-divider content-position="left">响应体</el-divider>
        <pre class="text-xs bg-gray-900 p-3 rounded overflow-auto max-h-48">{{ formatJSON(testResult.response_body) }}</pre>
      </div>
    </el-dialog>

    <!-- 通讯日志弹窗 -->
    <el-dialog v-model="showLogs" title="通讯日志" width="900px" destroy-on-close>
      <div class="flex items-center gap-3 mb-3">
        <el-select v-model="logFilter.success" placeholder="状态" size="small" class="w-24" clearable>
          <el-option label="成功" :value="true" />
          <el-option label="失败" :value="false" />
        </el-select>
        <el-button size="small" @click="loadLogs">查询</el-button>
        <span class="text-xs text-gray-400 ml-auto">共 {{ logTotal }} 条</span>
      </div>
      <el-table :data="logs" stripe size="small" max-height="400px">
        <el-table-column prop="event_type" label="事件" width="100">
          <template #default="{ row }">
            <el-tag size="small" type="info">{{ eventLabels[row.event_type] || row.event_type }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="70">
          <template #default="{ row }">
            <el-tag :type="row.success ? 'success' : 'danger'" size="small">
              {{ row.success ? '成功' : '失败' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="status_code" label="HTTP" width="70" />
        <el-table-column prop="duration_ms" label="耗时" width="80">
          <template #default="{ row }">{{ row.duration_ms }}ms</template>
        </el-table-column>
        <el-table-column prop="error_msg" label="错误信息" min-width="200" show-overflow-tooltip />
        <el-table-column prop="created_at" label="时间" width="160">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="详情" width="80">
          <template #default="{ row }">
            <el-button size="small" link @click="showLogDetail(row)">查看</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div class="flex justify-center mt-3">
        <el-pagination
          v-model:current-page="logPage"
          :page-size="20"
          :total="logTotal"
          layout="prev, pager, next"
          small
          @current-change="loadLogs"
        />
      </div>
    </el-dialog>

    <!-- 日志详情弹窗 -->
    <el-dialog v-model="showDetail" title="请求/响应详情" width="700px" destroy-on-close>
      <div v-if="detailLog">
        <el-divider content-position="left">请求体</el-divider>
        <pre class="text-xs bg-gray-900 p-3 rounded overflow-auto max-h-48">{{ formatJSON(detailLog.request_body) }}</pre>
        <el-divider content-position="left">响应体</el-divider>
        <pre class="text-xs bg-gray-900 p-3 rounded overflow-auto max-h-48">{{ formatJSON(detailLog.response_body) }}</pre>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { Close } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import {
  getConnections, createConnection, updateConnection, deleteConnection,
  testConnection, getGatewayLogs
} from '@/api/gateway'

const eventLabels = {
  cycle_end: '周期结束',
  session_end: '会话结束',
}

const connections = ref([])
const showEditor = ref(false)
const editing = ref(null)
const saving = ref(false)
const showTestResult = ref(false)
const testResult = ref(null)
const showLogs = ref(false)
const showDetail = ref(false)
const detailLog = ref(null)
const logs = ref([])
const logTotal = ref(0)
const logPage = ref(1)
const logFilter = reactive({ success: null, connectionId: null })
const logConnId = ref(null)

const form = reactive({
  name: '',
  adapter_type: 'rest',
  push_events: [],
  retry_count: 3,
  retry_interval_sec: 5,
})

const configUrl = ref('')
const configMethod = ref('POST')
const configFormKey = ref('param')
const templateJson = ref('{}')
const templateError = ref('')
const successField = ref('success')
const successExpect = ref(true)
const staticFields = ref([])
const extraFieldsDef = ref([])

const enabledCount = computed(() => connections.value.filter(c => c.enabled).length)

function formatTime(t) {
  if (!t) return '-'
  return t.replace('T', ' ').substring(0, 19)
}

function formatJSON(v) {
  if (!v) return ''
  if (typeof v === 'string') {
    try { return JSON.stringify(JSON.parse(v), null, 2) } catch { return v }
  }
  try { return JSON.stringify(v, null, 2) } catch { return String(v) }
}

async function loadConnections() {
  try {
    const { data } = await getConnections()
    connections.value = (data || []).map(c => ({ ...c, _testing: false }))
  } catch (e) {
    ElMessage.error('加载连接列表失败')
  }
}

function openCreate() {
  editing.value = null
  form.name = ''
  form.adapter_type = 'rest'
  form.push_events = []
  form.retry_count = 3
  form.retry_interval_sec = 5
  configUrl.value = ''
  configMethod.value = 'POST'
  configFormKey.value = 'param'
  templateJson.value = '{}'
  templateError.value = ''
  successField.value = 'success'
  successExpect.value = true
  staticFields.value = []
  extraFieldsDef.value = []
  showEditor.value = true
}

function openEdit(row) {
  editing.value = row
  form.name = row.name
  form.adapter_type = row.adapter_type
  form.push_events = [...(row.push_events || [])]
  form.retry_count = row.retry_count
  form.retry_interval_sec = row.retry_interval_sec

  const cfg = row.config || {}
  configUrl.value = cfg.url || ''
  configMethod.value = cfg.method || 'POST'
  configFormKey.value = cfg.form_key || 'param'
  successField.value = cfg.success_check?.field || 'success'
  successExpect.value = cfg.success_check?.expect ?? true

  const sf = cfg.static_fields || {}
  staticFields.value = Object.entries(sf).map(([key, value]) => ({ key, value: String(value) }))

  const tpl = cfg.template || {}
  templateJson.value = JSON.stringify(tpl, null, 2)
  templateError.value = ''

  extraFieldsDef.value = (row.extra_fields_schema || []).map(f => ({ ...f }))
  showEditor.value = true
}

function buildConfig() {
  let template = {}
  try {
    template = JSON.parse(templateJson.value || '{}')
    templateError.value = ''
  } catch (e) {
    templateError.value = 'JSON 格式错误: ' + e.message
    return null
  }

  const sf = {}
  for (const item of staticFields.value) {
    if (item.key.trim()) sf[item.key.trim()] = item.value
  }

  return {
    url: configUrl.value,
    method: configMethod.value,
    content_type: form.adapter_type === 'form-data' ? 'form-data' : 'application/json',
    form_key: configFormKey.value,
    headers: {},
    auth: { type: 'none' },
    static_fields: sf,
    template,
    success_check: {
      field: successField.value,
      expect: successExpect.value,
    },
  }
}

async function doSave() {
  if (!form.name.trim()) {
    ElMessage.warning('请输入连接名称')
    return
  }
  const config = buildConfig()
  if (!config) return

  const efSchema = extraFieldsDef.value.filter(f => f.key?.trim())
  saving.value = true
  try {
    const payload = {
      name: form.name,
      adapter_type: form.adapter_type,
      config,
      push_events: form.push_events,
      retry_count: form.retry_count,
      retry_interval_sec: form.retry_interval_sec,
      extra_fields_schema: efSchema.length ? efSchema : null,
    }
    if (editing.value) {
      await updateConnection(editing.value.id, payload)
      ElMessage.success('已更新')
    } else {
      await createConnection(payload)
      ElMessage.success('已创建')
    }
    showEditor.value = false
    loadConnections()
  } catch (e) {
    ElMessage.error('保存失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    saving.value = false
  }
}

async function toggleEnabled(row) {
  try {
    await updateConnection(row.id, { enabled: row.enabled })
    ElMessage.success(row.enabled ? '已启用' : '已禁用')
  } catch {
    row.enabled = !row.enabled
    ElMessage.error('操作失败')
  }
}

async function doDelete(row) {
  try {
    await deleteConnection(row.id)
    ElMessage.success('已删除')
    loadConnections()
  } catch {
    ElMessage.error('删除失败')
  }
}

async function doTest(row) {
  row._testing = true
  try {
    const { data } = await testConnection(row.id)
    testResult.value = data
    showTestResult.value = true
  } catch (e) {
    ElMessage.error('测试请求失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    row._testing = false
  }
}

function openLogs(row) {
  logConnId.value = row.id
  logFilter.success = null
  logPage.value = 1
  showLogs.value = true
  loadLogs()
}

async function loadLogs() {
  try {
    const params = {
      connection_id: logConnId.value,
      skip: (logPage.value - 1) * 20,
      limit: 20,
    }
    if (logFilter.success !== null && logFilter.success !== '') {
      params.success = logFilter.success
    }
    const { data } = await getGatewayLogs(params)
    logs.value = data.items || []
    logTotal.value = data.total || 0
  } catch {
    ElMessage.error('加载日志失败')
  }
}

function showLogDetail(row) {
  detailLog.value = row
  showDetail.value = true
}

onMounted(loadConnections)
</script>

<style scoped>
.template-editor :deep(.el-textarea__inner) {
  font-family: 'Fira Code', 'Consolas', monospace;
  font-size: 12px;
  line-height: 1.5;
  background: #1a1a2e;
  color: #e0e0e0;
}
</style>
