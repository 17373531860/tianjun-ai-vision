<template>
  <div>
    <div class="text-xs text-gray-500 mb-3">
      外部生产管控系统主动把「开工/完工」任务 POST 推过来，本机接收后做字段映射、校验、切项目/建工单，并回标准响应。
      与「外部对接」(我们推出去) 、「工单拉取」(我们主动查) 方向互补。配置全局共用，不随项目切换。
    </div>

    <!-- 工具栏 -->
    <div class="flex items-center justify-between mb-4">
      <div class="flex items-center gap-3">
        <span class="text-sm text-gray-300">入站接收</span>
        <el-switch v-model="form.enabled" size="small" />
        <el-tag size="small" :type="form.enabled ? 'success' : 'info'">
          {{ form.enabled ? '已启用' : '已停用' }}
        </el-tag>
      </div>
      <div class="flex gap-2">
        <el-button size="small" @click="load" :loading="loading">重新加载</el-button>
        <el-button size="small" type="success" @click="save" :loading="saving">保存配置</el-button>
      </div>
    </div>

    <!-- 接收地址 -->
    <div class="mb-4 p-3 rounded bg-slate-800/50 text-xs">
      <div class="text-gray-400 mb-1">外部系统把任务 POST 到本机此地址（无需鉴权，工控内网 M2M）：</div>
      <code class="text-cyan-300 break-all">POST  {{ endpointUrl }}</code>
    </div>

    <el-form :model="form" label-width="130px" size="small">
      <!-- ========== 字段映射 ========== -->
      <el-divider content-position="left"><span class="text-cyan-300 text-xs">字段映射（我们字段 ← 外部字段）</span></el-divider>
      <div class="text-xs text-gray-500 mb-2 ml-2">
        外部字段支持点路径取嵌套值，如 <code>data.order.no</code>。常用我们字段：
        <code>task_no</code>(任务/工单号) <code>product_code</code>(产品代号) <code>step_code</code>(工序)
        <code>operator</code>(操作员) <code>begin_time</code>(开工时间) <code>is_complete</code>(完工信号)
      </div>
      <div class="flex flex-col gap-2 mb-2">
        <div v-for="(row, i) in form.fieldRows" :key="i" class="flex items-center gap-2">
          <el-input v-model="row.k" placeholder="我们字段(如 task_no)" class="w-52" />
          <span class="text-gray-500">←</span>
          <el-input v-model="row.v" placeholder="外部字段路径(如 TaskNo)" class="w-72" />
          <el-button size="small" type="danger" plain @click="form.fieldRows.splice(i, 1)">删</el-button>
        </div>
      </div>
      <el-button size="small" plain @click="form.fieldRows.push({ k: '', v: '' })">+ 加一行映射</el-button>

      <el-form-item label="必填字段" class="mt-3">
        <el-select v-model="form.required" multiple filterable allow-create default-first-option
          placeholder="缺这些字段直接回失败" class="w-full">
          <el-option v-for="k in ourKeys" :key="k" :label="k" :value="k" />
        </el-select>
      </el-form-item>

      <!-- ========== 处理动作 (全可选) ========== -->
      <el-divider content-position="left"><span class="text-cyan-300 text-xs">收到任务后做什么（按需勾选）</span></el-divider>

      <el-form-item label="按产品码切项目">
        <el-switch v-model="form.switch_project_on_task" />
        <span class="text-xs text-gray-500 ml-2">开工时按产品代号自动激活对应检测项目（会重载模型，敏感动作，默认关）</span>
      </el-form-item>
      <el-form-item v-if="form.switch_project_on_task" label="产品代号 → 项目">
        <div class="flex flex-col gap-2 w-full">
          <div v-for="(row, i) in form.mapRows" :key="i" class="flex items-center gap-2">
            <el-input v-model="row.code" placeholder="产品代号(外部传来的)" class="w-52" />
            <span class="text-gray-500">→</span>
            <el-select v-model="row.project_id" filterable placeholder="选检测项目" class="w-60">
              <el-option v-for="p in projects" :key="p.id" :label="`#${p.id} ${p.name}`" :value="p.id" />
            </el-select>
            <el-button size="small" type="danger" plain @click="form.mapRows.splice(i, 1)">删</el-button>
          </div>
          <el-button size="small" plain @click="form.mapRows.push({ code: '', project_id: null })">+ 加一行对照</el-button>
        </div>
      </el-form-item>

      <el-form-item label="开工即建工单">
        <el-switch v-model="form.create_work_order_on_task" />
        <span class="text-xs text-gray-500 ml-2">用任务号建/激活工单(in_progress)，让检测周期绑该任务、出站报文自带工单号</span>
      </el-form-item>
      <el-form-item v-if="form.create_work_order_on_task" label="工单绑定方式">
        <el-radio-group v-model="form.order_binding">
          <el-radio value="project">绑当前激活项目(默认)</el-radio>
          <el-radio value="channel">按工位路由</el-radio>
        </el-radio-group>
        <template v-if="form.order_binding === 'channel'">
          <span class="text-xs text-gray-400 mx-2">工位号取字段</span>
          <el-select v-model="form.channel_field" filterable class="w-40">
            <el-option v-for="k in ourKeys" :key="k" :label="k" :value="k" />
          </el-select>
        </template>
      </el-form-item>
      <el-form-item label="拒绝重复任务">
        <el-switch v-model="form.reject_duplicate_task" />
        <span class="text-xs text-gray-500 ml-2">同任务号已在产时回"重复任务"码拒收（与"最新开工为准"相反，二选一）</span>
      </el-form-item>

      <el-form-item label="最新开工为准">
        <el-switch v-model="form.supersede_previous_task" />
        <span class="text-xs text-gray-500 ml-2">收到新开工直接顶替(收尾)当前在产的旧任务，新任务独占</span>
      </el-form-item>
      <el-form-item v-if="form.supersede_previous_task" label="顶替范围">
        <el-select v-model="form.supersede_scope" class="w-72">
          <el-option label="仅项目绑定的外部在产单 (默认)" value="project" />
          <el-option label="仅与本任务同一项目" value="same_project" />
          <el-option label="仅与本任务同一工位" value="same_channel" />
          <el-option label="所有外部在产单 (含工位/集群绑定)" value="external" />
        </el-select>
      </el-form-item>
      <el-form-item v-if="form.supersede_previous_task" label="顶替即回传完工">
        <el-switch v-model="form.report_complete_on_supersede" />
        <span class="text-xs text-gray-400 ml-2">完工出站事件名</span>
        <el-input v-model="form.complete_event_type" placeholder="task_complete" class="w-44 ml-2" />
        <span class="text-xs text-gray-500 ml-2">出站连接 push_events 订阅此名即推完工报文</span>
      </el-form-item>

      <el-form-item label="完工信号字段">
        <el-select v-model="form.complete_field" clearable filterable placeholder="留空=不识别完工信号" class="w-60">
          <el-option v-for="k in ourKeys" :key="k" :label="k" :value="k" />
        </el-select>
        <span class="text-xs text-gray-500 ml-2">该字段为真时，本次按"完工"收工单（不当开工处理）</span>
      </el-form-item>
      <el-form-item v-if="form.complete_field" label="完工匹配方式">
        <el-radio-group v-model="form.complete_match_column">
          <el-radio value="order_no">按工单号(任务号)</el-radio>
          <el-radio value="product_code">按产品码(取该产品最新在产单)</el-radio>
        </el-radio-group>
        <span class="text-xs text-gray-400 mx-2">取字段</span>
        <el-select v-model="form.complete_match_field" filterable class="w-44">
          <el-option v-for="k in ourKeys" :key="k" :label="k" :value="k" />
        </el-select>
      </el-form-item>

      <!-- ========== 响应码 ========== -->
      <el-divider content-position="left"><span class="text-cyan-300 text-xs">回给外部系统的响应（按客户协议定）</span></el-divider>
      <el-form-item label="响应体格式">
        <el-radio-group v-model="form.response.format">
          <el-radio value="json">JSON(默认)</el-radio>
          <el-radio value="xml">XML / SOAP</el-radio>
        </el-radio-group>
        <template v-if="form.response.format === 'xml'">
          <span class="text-xs text-gray-400 mx-2">根标签</span>
          <el-input v-model="form.response.xml_root" placeholder="response" class="w-40" />
          <el-checkbox v-model="form.response.xml_declaration" class="ml-2">带 &lt;?xml?&gt; 头</el-checkbox>
        </template>
      </el-form-item>
      <el-form-item label="业务码字段">
        <el-input v-model="form.response.code_field" placeholder="code" class="w-40" />
        <span class="text-xs text-gray-400 ml-3">消息字段</span>
        <el-input v-model="form.response.message_field" placeholder="message" class="w-40 ml-2" />
      </el-form-item>
      <el-form-item label="成功码 / 成功消息">
        <el-input v-model="form.response.success_code" placeholder="0" class="w-32" />
        <el-input v-model="form.response.success_message" placeholder="OK" class="w-60 ml-2" />
      </el-form-item>
      <el-form-item label="各类失败码">
        <div class="grid grid-cols-2 gap-2 w-full">
          <div v-for="c in codeRows" :key="c.key" class="flex items-center gap-2">
            <span class="text-xs text-gray-300 w-32 text-right">{{ c.label }}</span>
            <el-input v-model="form.response.codes[c.key]" :placeholder="c.ph" class="w-32" />
          </div>
        </div>
      </el-form-item>
      <el-form-item label="原样回显字段">
        <el-select v-model="form.echo_fields" multiple filterable allow-create default-first-option
          placeholder="把哪些已映射字段原样塞回响应(如 task_no)" class="w-full">
          <el-option v-for="k in ourKeys" :key="k" :label="k" :value="k" />
        </el-select>
      </el-form-item>
      <el-form-item label="业务码字段(进阶)">
        <span class="text-xs text-gray-500">上面"业务码字段"支持点路径，如 <code>head.code</code> → 嵌套 <code>{ head:{ code:0 } }</code></span>
      </el-form-item>
      <el-form-item label="自定义响应模板">
        <el-input v-model="form.response_template_text" type="textarea" :rows="4"
          placeholder='留空=用上面的码字段平铺。填 JSON 可全自定义信封(兼容 SOAP/复杂协议)，占位符 {code} {message} {task_no} 等' />
        <div class="text-xs text-gray-500 mt-1">
          例：<code>{"resultCode":"{code}","resultMsg":"{message}","data":{"taskNo":"{task_no}"}}</code>
          。填了模板则忽略上面的码字段平铺。
        </div>
      </el-form-item>

      <!-- ========== 高级 ========== -->
      <el-collapse v-model="advancedOpen" class="adv-collapse mt-2">
        <el-collapse-item name="adv">
          <template #title><span class="text-xs text-gray-300">展开高级设置</span></template>
          <el-form-item label="入站编码格式">
            <el-select v-model="form.input_format" class="w-52">
              <el-option label="自动识别 (按 Content-Type)" value="auto" />
              <el-option label="JSON" value="json" />
              <el-option label="表单 (urlencoded/multipart)" value="form" />
              <el-option label="URL 参数 (query)" value="query" />
              <el-option label="XML / SOAP" value="xml" />
            </el-select>
            <span class="text-xs text-gray-500 ml-2">对方用什么格式推就选什么；GET 一律按 URL 参数</span>
          </el-form-item>
          <el-form-item label="并入 URL 参数">
            <el-switch v-model="form.merge_query_params" />
            <span class="text-xs text-gray-500 ml-2">把网址 ?后面的参数也并进报文（同名以 body 为准）</span>
          </el-form-item>
          <el-form-item label="失败→HTTP状态码">
            <div class="grid grid-cols-2 gap-2 w-full">
              <div v-for="c in statusRows" :key="c.key" class="flex items-center gap-2">
                <span class="text-xs text-gray-300 w-28 text-right">{{ c.label }}</span>
                <el-input v-model="form.http_status[c.key]" placeholder="200" class="w-24" />
              </div>
            </div>
            <div class="text-xs text-gray-500 mt-1">留空=200(业务码在响应体里)。要求失败回非2xx时填，如缺字段→400、内部错误→500</div>
          </el-form-item>
          <el-form-item label="请求体上限(字节)">
            <el-input-number v-model="form.max_body_bytes" :min="0" :step="1024" class="w-44" />
            <span class="text-xs text-gray-500 ml-2">超限回 413，0 = 不限</span>
          </el-form-item>

          <el-divider content-position="left"><span class="text-gray-400 text-xs">来源校验（默认关=内网无校验）</span></el-divider>
          <el-form-item label="开启来源校验">
            <el-switch v-model="form.auth.enabled" />
            <span class="text-xs text-gray-500 ml-2">要求对方带共享密钥头 / 限定来源 IP</span>
          </el-form-item>
          <el-form-item v-if="form.auth.enabled" label="共享密钥头">
            <el-input v-model="form.auth.header_name" placeholder="X-API-Key" class="w-44" />
            <span class="text-xs text-gray-400 mx-2">值</span>
            <el-input v-model="form.auth.header_value" placeholder="期望的密钥(留空=不校验头)" class="w-60" />
          </el-form-item>
          <el-form-item v-if="form.auth.enabled" label="IP 白名单">
            <el-select v-model="form.auth.ip_whitelist" multiple filterable allow-create default-first-option
              placeholder="留空=不限 IP；支持 CIDR，如 192.168.1.0/24" class="w-full" />
          </el-form-item>
        </el-collapse-item>
      </el-collapse>
    </el-form>

    <!-- ========== 通讯日志 ========== -->
    <el-divider content-position="left"><span class="text-cyan-300 text-xs">最近入站记录</span></el-divider>
    <div class="flex justify-end mb-2">
      <el-button size="small" @click="loadLogs" :loading="logLoading">刷新日志</el-button>
    </div>
    <el-table :data="logs" stripe size="small" class="mes-table" max-height="320">
      <el-table-column prop="id" label="ID" width="60" />
      <el-table-column label="时间" width="170">
        <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="结果" width="80">
        <template #default="{ row }">
          <el-tag size="small" :type="row.success ? 'success' : 'danger'">{{ row.success ? '成功' : '失败' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="收到内容" min-width="220">
        <template #default="{ row }"><span class="text-xs text-gray-300 break-all">{{ row.request_body }}</span></template>
      </el-table-column>
      <el-table-column label="回复内容" min-width="220">
        <template #default="{ row }"><span class="text-xs text-gray-400 break-all">{{ row.response_body }}</span></template>
      </el-table-column>
    </el-table>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { getInboundConfig, saveInboundConfig, getInboundLogs } from '@/api/gateway'
import { getProjects } from '@/api/project'
import { dbg } from '@/utils/debug'

const loading = ref(false)
const saving = ref(false)
const logLoading = ref(false)
const advancedOpen = ref([])
const projects = ref([])
const logs = ref([])

const codeRows = [
  { key: 'bad_request', label: '请求格式错', ph: '40005' },
  { key: 'missing_field', label: '缺必填字段', ph: '40004' },
  { key: 'duplicate', label: '重复任务', ph: '40001' },
  { key: 'unknown_product', label: '产品码未知', ph: '40002' },
  { key: 'activate_failed', label: '切项目失败', ph: '40003' },
  { key: 'internal_error', label: '内部错误', ph: '40006' },
  { key: 'disabled', label: '功能未启用', ph: '40006' },
]

const statusRows = [{ key: 'success', label: '成功' }, ...codeRows]

function emptyForm() {
  return {
    enabled: false,
    fieldRows: [],
    required: [],
    response: {
      code_field: 'code', message_field: 'message',
      success_code: '0', success_message: 'OK',
      codes: {},
      format: 'json', xml_root: 'response', xml_declaration: true,
    },
    response_template_text: '',
    http_status: {},
    auth: { enabled: false, header_name: 'X-API-Key', header_value: '', ip_whitelist: [] },
    max_body_bytes: 1048576,
    input_format: 'auto',
    merge_query_params: true,
    echo_fields: [],
    switch_project_on_task: false,
    mapRows: [],
    create_work_order_on_task: false,
    order_binding: 'project',
    channel_field: 'channel',
    reject_duplicate_task: false,
    complete_field: '',
    complete_match_field: 'task_no',
    complete_match_column: 'order_no',
    supersede_previous_task: false,
    supersede_scope: 'project',
    report_complete_on_supersede: true,
    complete_event_type: 'task_complete',
  }
}

const form = reactive(emptyForm())

// 接收地址 (展示用)
const endpointUrl = computed(() => {
  const base = (import.meta.env.VITE_API_BASE_URL || 'http://<本机IP>:8001/api/v1').replace(/\/+$/, '')
  return `${base}/mes/inbound/task`
})

// 当前已配的"我们字段"名 (供必填/完工/回显下拉)
const ourKeys = computed(() => form.fieldRows.map(r => r.k).filter(Boolean))

function formatTime(t) {
  if (!t) return '-'
  try { return new Date(t).toLocaleString('zh-CN') } catch { return t }
}

function applyConfig(cfg) {
  const f = emptyForm()
  f.enabled = !!cfg.enabled
  f.fieldRows = Object.entries(cfg.field_map || {}).map(([k, v]) => ({ k, v }))
  f.required = Array.isArray(cfg.required_fields) ? [...cfg.required_fields] : []
  const r = cfg.response || {}
  f.response = {
    code_field: r.code_field || 'code',
    message_field: r.message_field || 'message',
    success_code: String(r.success_code ?? '0'),
    success_message: r.success_message || 'OK',
    codes: { ...(r.codes || {}) },
    format: r.format || 'json',
    xml_root: r.xml_root || 'response',
    xml_declaration: r.xml_declaration !== false,
  }
  f.response_template_text = r.template ? JSON.stringify(r.template, null, 2) : ''
  const hsm = cfg.http_status_map || {}
  f.http_status = {}
  Object.entries(hsm).forEach(([k, v]) => { f.http_status[k] = String(v) })
  const a = cfg.auth || {}
  f.auth = {
    enabled: !!a.enabled,
    header_name: a.header_name || 'X-API-Key',
    header_value: a.header_value || '',
    ip_whitelist: Array.isArray(a.ip_whitelist) ? [...a.ip_whitelist] : [],
  }
  f.max_body_bytes = cfg.max_body_bytes ?? 1048576
  f.input_format = cfg.input_format || 'auto'
  f.merge_query_params = cfg.merge_query_params !== false
  f.echo_fields = Array.isArray(cfg.echo_fields) ? [...cfg.echo_fields] : []
  f.switch_project_on_task = !!cfg.switch_project_on_task
  f.mapRows = Object.entries(cfg.product_project_map || {}).map(([code, pid]) => ({
    code, project_id: typeof pid === 'number' ? pid : (parseInt(pid, 10) || null),
  }))
  f.create_work_order_on_task = !!cfg.create_work_order_on_task
  f.order_binding = cfg.order_binding || 'project'
  f.channel_field = cfg.channel_field || 'channel'
  f.reject_duplicate_task = !!cfg.reject_duplicate_task
  f.complete_field = cfg.complete_field || ''
  f.complete_match_field = cfg.complete_match_field || 'task_no'
  f.complete_match_column = cfg.complete_match_column || 'order_no'
  f.supersede_previous_task = !!cfg.supersede_previous_task
  f.supersede_scope = cfg.supersede_scope || 'project'
  f.report_complete_on_supersede = cfg.report_complete_on_supersede !== false
  f.complete_event_type = cfg.complete_event_type || 'task_complete'
  Object.assign(form, f)
}

function buildConfig() {
  const field_map = {}
  form.fieldRows.forEach(r => { if (r.k && r.k.trim()) field_map[r.k.trim()] = (r.v || '').trim() })
  const product_project_map = {}
  form.mapRows.forEach(r => {
    if (r.code && r.code.trim() && r.project_id != null) product_project_map[r.code.trim()] = r.project_id
  })
  // 成功码 / 失败码: 纯数字串转回数字, 否则保留原值(允许字符串码)
  const numOrRaw = (v) => {
    const s = String(v ?? '').trim()
    if (s === '') return s
    return /^-?\d+$/.test(s) ? parseInt(s, 10) : s
  }
  const codes = {}
  Object.entries(form.response.codes || {}).forEach(([k, v]) => { codes[k] = numOrRaw(v) })
  const response = {
    code_field: form.response.code_field || 'code',
    message_field: form.response.message_field || 'message',
    success_code: numOrRaw(form.response.success_code),
    success_message: form.response.success_message || 'OK',
    codes,
    format: form.response.format || 'json',
    xml_root: form.response.xml_root || 'response',
    xml_declaration: form.response.xml_declaration !== false,
  }
  const tpl = (form.response_template_text || '').trim()
  if (tpl) response.template = JSON.parse(tpl)  // 解析失败由 save() 捕获并提示
  const http_status_map = {}
  Object.entries(form.http_status || {}).forEach(([k, v]) => {
    const s = String(v ?? '').trim()
    if (s !== '' && /^\d+$/.test(s)) http_status_map[k] = parseInt(s, 10)
  })
  return {
    http_status_map,
    enabled: form.enabled,
    field_map,
    required_fields: form.required,
    response,
    max_body_bytes: form.max_body_bytes || 0,
    input_format: form.input_format || 'auto',
    merge_query_params: form.merge_query_params,
    auth: {
      enabled: form.auth.enabled,
      header_name: form.auth.header_name || 'X-API-Key',
      header_value: form.auth.header_value || '',
      ip_whitelist: form.auth.ip_whitelist || [],
    },
    echo_fields: form.echo_fields,
    switch_project_on_task: form.switch_project_on_task,
    product_project_map,
    create_work_order_on_task: form.create_work_order_on_task,
    order_binding: form.order_binding || 'project',
    channel_field: form.channel_field || 'channel',
    reject_duplicate_task: form.reject_duplicate_task,
    complete_field: form.complete_field || '',
    complete_match_field: form.complete_match_field || 'task_no',
    complete_match_column: form.complete_match_column || 'order_no',
    supersede_previous_task: form.supersede_previous_task,
    supersede_scope: form.supersede_scope || 'project',
    report_complete_on_supersede: form.report_complete_on_supersede,
    complete_event_type: form.complete_event_type || 'task_complete',
  }
}

async function load() {
  loading.value = true
  try {
    const res = await getInboundConfig()
    applyConfig(res.data || res || {})
  } catch (e) {
    ElMessage.error('加载入站配置失败：' + (e?.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}

async function loadProjects() {
  try {
    const res = await getProjects()
    projects.value = res.data || res || []
  } catch { projects.value = [] }
}

async function loadLogs() {
  logLoading.value = true
  try {
    const res = await getInboundLogs({ limit: 50 })
    logs.value = res.data || res || []
  } catch (e) {
    ElMessage.error('加载日志失败：' + (e?.response?.data?.detail || e.message))
  } finally {
    logLoading.value = false
  }
}

async function save() {
  const tpl = (form.response_template_text || '').trim()
  if (tpl) {
    try { JSON.parse(tpl) } catch (e) {
      ElMessage.error('自定义响应模板不是合法 JSON：' + e.message); return
    }
  }
  saving.value = true
  try {
    const cfg = buildConfig()
    await saveInboundConfig(cfg)
    dbg('mes.gateway', '保存入站配置',
        `enabled=${cfg.enabled} 映射=${Object.keys(cfg.field_map).length} 切项目=${cfg.switch_project_on_task} 建工单=${cfg.create_work_order_on_task} 顶替=${cfg.supersede_previous_task}`)
    ElMessage.success('已保存')
    await load()
  } catch (e) {
    ElMessage.error('保存失败：' + (e?.response?.data?.detail || e.message))
  } finally {
    saving.value = false
  }
}

onMounted(() => {
  load()
  loadProjects()
  loadLogs()
})
</script>

<style scoped>
.adv-collapse :deep(.el-collapse-item__header),
.adv-collapse :deep(.el-collapse-item__wrap) {
  background: transparent;
  border-color: rgba(8, 145, 178, 0.3);
}
.adv-collapse :deep(.el-collapse-item__content) {
  padding-top: 12px;
}
</style>
