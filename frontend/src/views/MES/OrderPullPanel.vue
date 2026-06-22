<template>
  <div>
    <div class="text-xs text-gray-500 mb-3">
      主动去外部 MES 查询工单并回填到本地（与"外部对接"的推送方向相反）。配置全局共用，不随项目切换。
    </div>

    <!-- 工具栏 -->
    <div class="flex items-center justify-between mb-4">
      <div class="flex items-center gap-3">
        <span class="text-sm text-gray-300">外部 MES 工单拉取</span>
        <el-tag size="small" :type="enabledCount > 0 ? 'success' : 'info'">
          {{ enabledCount }} 个已启用
        </el-tag>
      </div>
      <el-button size="small" type="success" @click="openCreate">新建拉取配置</el-button>
    </div>

    <div class="text-xs text-gray-500 mb-3">
      想用 USB 扫码枪扫工单标签自动拉取？去「扫码器 → USB 扫码枪」Tab 配置（拉工单/绑工件统一在那里）。
    </div>

    <!-- 配置列表 -->
    <el-table :data="pullConns" stripe size="small" class="mes-table" max-height="calc(100vh - 300px)">
      <el-table-column prop="id" label="连接ID" width="70" />
      <el-table-column prop="name" label="配置名称" width="160" />
      <el-table-column label="接口地址" min-width="220">
        <template #default="{ row }">
          <span class="text-xs text-gray-300">{{ row.config?.pull?.url || '-' }}</span>
        </template>
      </el-table-column>
      <el-table-column label="拉回怎么用" width="150">
        <template #default="{ row }">
          <el-tag size="small" type="warning">{{ importModeLabel(row.config?.pull?.import_mode) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="定时同步" width="110">
        <template #default="{ row }">
          <span v-if="row.config?.pull?.triggers?.scheduled" class="text-xs text-cyan-300">
            每 {{ row.config?.pull?.triggers?.interval_sec || 300 }}s
          </span>
          <span v-else class="text-xs text-gray-500">关</span>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="80">
        <template #default="{ row }">
          <el-switch v-model="row.pull_enabled" size="small" @change="togglePull(row)" />
        </template>
      </el-table-column>
      <el-table-column label="最后同步" width="160">
        <template #default="{ row }">
          {{ row.last_sync_at ? formatTime(row.last_sync_at) : '-' }}
        </template>
      </el-table-column>
      <el-table-column label="操作" width="300" fixed="right">
        <template #default="{ row }">
          <div class="flex gap-1">
            <el-button size="small" @click="openEdit(row)">编辑</el-button>
            <el-button size="small" type="warning" @click="doPull(row, true)" :loading="row._dry">试同步</el-button>
            <el-button size="small" type="primary" @click="doPull(row, false)" :loading="row._run">立即同步</el-button>
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
      :title="editing ? '编辑拉取配置' : '新建拉取配置'"
      width="800px"
      destroy-on-close
      top="5vh"
    >
      <!-- 预设模板 -->
      <div class="flex items-center gap-2 mb-4 p-2 rounded bg-slate-800/50">
        <span class="text-xs text-gray-400">预设模板：</span>
        <el-button size="small" type="primary" plain @click="applyTemplate('hiwin')">上银 HIWIN</el-button>
        <el-button size="small" plain @click="applyTemplate('blank')">通用 REST（空白）</el-button>
        <span class="text-xs text-gray-500 ml-2">选模板自动填好大部分，只需改地址和认证</span>
      </div>

      <el-form :model="form" label-width="120px" size="small">
        <!-- ========== 基础区 ========== -->
        <el-divider content-position="left"><span class="text-cyan-300 text-xs">基础配置</span></el-divider>

        <el-form-item label="配置名称" required>
          <el-input v-model="form.name" placeholder="如：上银工单同步" />
        </el-form-item>
        <el-form-item label="接口地址" required>
          <el-input v-model="form.url" placeholder="上银给你的查询接口网址" />
        </el-form-item>
        <el-form-item label="认证方式">
          <el-select v-model="form.auth_type" class="w-44">
            <el-option label="无" value="none" />
            <el-option label="Basic（账号密码）" value="basic" />
            <el-option label="Bearer Token" value="bearer" />
            <el-option label="API-Key（请求头）" value="api_key" />
          </el-select>
          <div class="ml-2 inline-flex gap-2">
            <template v-if="form.auth_type === 'basic'">
              <el-input v-model="form.auth_user" placeholder="账号" class="w-32" />
              <el-input v-model="form.auth_pass" placeholder="密码" type="password" class="w-32" />
            </template>
            <el-input v-if="form.auth_type === 'bearer'" v-model="form.auth_token" placeholder="Token" class="w-64" />
            <template v-if="form.auth_type === 'api_key'">
              <el-input v-model="form.auth_apikey_header" placeholder="头名(默认X-API-Key)" class="w-44" />
              <el-input v-model="form.auth_apikey_value" placeholder="Key 值" class="w-44" />
            </template>
          </div>
        </el-form-item>
        <el-form-item label="拉回来怎么用">
          <el-radio-group v-model="form.import_mode">
            <el-radio value="upsert">自动建工单</el-radio>
            <el-radio value="validate">只校验工单号存不存在</el-radio>
            <el-radio value="upsert_with_planned">自动建 + 把排产量当计划数量</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="什么时候同步">
          <div class="flex flex-col gap-1">
            <el-checkbox v-model="form.trig_manual">手动按钮（始终可用）</el-checkbox>
            <div class="flex items-center gap-2">
              <el-checkbox v-model="form.trig_scheduled">定时自动同步</el-checkbox>
              <span v-if="form.trig_scheduled" class="text-xs text-gray-400">每</span>
              <el-input-number v-if="form.trig_scheduled" v-model="form.trig_interval_sec"
                :min="10" :step="30" size="small" class="w-28" />
              <span v-if="form.trig_scheduled" class="text-xs text-gray-400">秒</span>
            </div>
            <el-checkbox v-model="form.trig_on_scan">扫码枪扫到工单号即查</el-checkbox>
          </div>
        </el-form-item>

        <!-- 测试连接 -->
        <el-form-item label="测试">
          <div class="flex items-center gap-2 flex-wrap">
            <el-input v-model="testJobNo" placeholder="工单号(留空=查全部)" class="w-44" />
            <el-button size="small" type="primary" @click="doTest" :loading="testing">测试连接</el-button>
            <span v-if="testResult" :class="testResult.success ? 'text-green-400' : 'text-red-400'" class="text-xs">
              {{ testResult.success ? `成功 (HTTP ${testResult.http_status})` : `失败: ${testResult.error}` }}
            </span>
          </div>
          <div v-if="guessFields.length" class="text-xs text-cyan-300 mt-1">
            已自动识别返回字段：{{ guessFields.join(' / ') }} — 下方"字段对应"已尽量帮你填好，核对一下即可
          </div>
        </el-form-item>

        <!-- ========== 高级区 ========== -->
        <el-collapse v-model="advancedOpen" class="adv-collapse mt-2">
          <el-collapse-item name="adv">
            <template #title><span class="text-xs text-gray-300">展开高级设置（请求/解析/字段映射/稳健性）</span></template>

            <el-form-item label="请求方法">
              <el-select v-model="form.method" class="w-28">
                <el-option label="POST" value="POST" />
                <el-option label="GET" value="GET" />
              </el-select>
              <el-input v-model="form.content_type" placeholder="Content-Type" class="w-72 ml-2" />
            </el-form-item>
            <el-form-item label="请求体模板">
              <el-input v-model="form.request_body_template" type="textarea" :rows="4"
                placeholder='JSON，用 {job_no} 占位工单号' />
              <div class="text-xs text-gray-500 mt-1">查询时 <code>{job_no}</code> 会被替换成实际工单号（留空=查全部）</div>
            </el-form-item>

            <el-divider content-position="left"><span class="text-gray-400 text-xs">返回怎么解析</span></el-divider>
            <el-form-item label="成功判定">
              当
              <el-input v-model="form.success_path" placeholder="statusCode" class="w-40 mx-1" />
              等于
              <el-input v-model="form.success_value" placeholder="200" class="w-24 ml-1" />
            </el-form-item>
            <el-form-item label="工单数组在哪">
              <el-input v-model="form.array_path" placeholder="response.resultData" class="w-60" />
              <span class="text-xs text-gray-500 ml-2">测试连接后会自动填</span>
            </el-form-item>
            <el-form-item label="字段对应关系">
              <div class="flex flex-col gap-2 w-full">
                <div v-for="fm in fieldMappingRows" :key="fm.key" class="flex items-center gap-2">
                  <span class="text-xs text-gray-300 w-28 text-right">{{ fm.label }}</span>
                  <span class="text-gray-500">←</span>
                  <el-select v-model="form[fm.model]" :placeholder="fm.placeholder"
                    filterable allow-create default-first-option class="w-52" clearable>
                    <el-option v-for="f in guessFields" :key="f" :label="f" :value="f" />
                  </el-select>
                  <span v-if="fm.required" class="text-xs text-red-400">必填(去重依据)</span>
                </div>
              </div>
            </el-form-item>

            <el-divider content-position="left"><span class="text-gray-400 text-xs">稳健性</span></el-divider>
            <el-form-item label="超时(秒)">
              <el-input-number v-model="form.timeout_sec" :min="1" :max="120" size="small" class="w-28" />
              <span class="text-xs text-gray-400 ml-3">失败重试</span>
              <el-input-number v-model="form.retry_count" :min="0" :max="5" size="small" class="w-24 ml-1" />
              <span class="text-xs text-gray-400 ml-1">次, 间隔</span>
              <el-input-number v-model="form.retry_interval_sec" :min="0" :max="60" size="small" class="w-24 ml-1" />
              <span class="text-xs text-gray-400 ml-1">秒</span>
            </el-form-item>
            <el-form-item label="证书校验">
              <el-switch v-model="form.verify_ssl" />
              <span class="text-xs text-gray-500 ml-2">https 自签名证书接不上时可关闭</span>
            </el-form-item>
          </el-collapse-item>
        </el-collapse>
      </el-form>

      <template #footer>
        <el-button @click="showEditor = false">取消</el-button>
        <el-button type="success" @click="save" :loading="saving">保存</el-button>
      </template>
    </el-dialog>

    <!-- 同步结果对话框 -->
    <el-dialog v-model="showResult" title="同步结果" width="640px">
      <div v-if="pullResult">
        <div class="flex gap-4 mb-3 text-sm">
          <span :class="pullResult.success ? 'text-green-400' : 'text-red-400'">
            {{ pullResult.success ? '✓ 成功' : '✗ 失败' }}
          </span>
          <span class="text-gray-300">拉回 {{ pullResult.fetched }} 条</span>
          <span class="text-green-400">新建 {{ pullResult.created }}</span>
          <span class="text-cyan-300">更新 {{ pullResult.updated }}</span>
          <span class="text-gray-400">校验 {{ pullResult.validated }}</span>
          <span class="text-gray-500">跳过 {{ pullResult.skipped }}</span>
        </div>
        <div v-if="pullResult.error" class="text-red-400 text-xs mb-2">错误：{{ pullResult.error }}</div>
        <el-table v-if="pullResult.items?.length" :data="pullResult.items" size="small" max-height="360">
          <el-table-column label="工单号" width="160">
            <template #default="{ row }">{{ row.order_no }}</template>
          </el-table-column>
          <el-table-column label="动作" width="100">
            <template #default="{ row }">
              <el-tag size="small" :type="actionTag(row.action)">{{ actionLabel(row.action) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="映射结果">
            <template #default="{ row }">
              <span class="text-xs text-gray-300">{{ JSON.stringify(row.mapped) }}</span>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import {
  getConnections, createConnection, updateConnection, deleteConnection,
  pullTest, pullOrders,
} from '@/api/gateway'
import { dbg } from '@/utils/debug'

const connections = ref([])
const showEditor = ref(false)
const editing = ref(false)
const saving = ref(false)
const advancedOpen = ref([])

const testJobNo = ref('')
const testing = ref(false)
const testResult = ref(null)
const guessFields = ref([])

const showResult = ref(false)
const pullResult = ref(null)

const fieldMappingRows = [
  { key: 'order_no', label: '工单号', model: 'fm_order_no', placeholder: 'job_no', required: true },
  { key: 'customer_name', label: '客户名', model: 'fm_customer_name', placeholder: 'cust_name' },
  { key: 'product_spec', label: '物料规格', model: 'fm_product_spec', placeholder: 'spec' },
  { key: 'planned_qty', label: '计划数量', model: 'fm_planned_qty', placeholder: 'dispatch_qty' },
  { key: 'product_name', label: '产品名', model: 'fm_product_name', placeholder: 'spec' },
]

function emptyForm() {
  return {
    id: null,
    name: '',
    enabled: false,
    url: '',
    method: 'POST',
    content_type: 'application/json; charset=UTF-8',
    request_body_template: '',
    success_path: 'statusCode',
    success_value: '200',
    array_path: '',
    fm_order_no: '', fm_customer_name: '', fm_product_spec: '',
    fm_planned_qty: '', fm_product_name: '',
    import_mode: 'upsert',
    auth_type: 'none', auth_user: '', auth_pass: '', auth_token: '',
    auth_apikey_header: '', auth_apikey_value: '',
    timeout_sec: 10, retry_count: 1, retry_interval_sec: 2, verify_ssl: true,
    trig_manual: true, trig_scheduled: false, trig_interval_sec: 300, trig_on_scan: false,
  }
}

const form = ref(emptyForm())

// 只显示配了拉取的连接
const pullConns = computed(() => connections.value.filter(c => c.config?.pull?.url))
const enabledCount = computed(() => pullConns.value.filter(c => c.pull_enabled).length)

function importModeLabel(m) {
  return { upsert: '自动建工单', validate: '仅校验', upsert_with_planned: '建+用排产量' }[m] || '自动建工单'
}
function actionLabel(a) {
  return { created: '新建', updated: '更新', matched: '已存在', remote_only: '远端有', skipped: '跳过' }[a] || a
}
function actionTag(a) {
  return { created: 'success', updated: 'primary', matched: 'info', remote_only: 'warning', skipped: 'info' }[a] || 'info'
}
function formatTime(t) {
  try { return new Date(t).toLocaleString('zh-CN') } catch { return t }
}

function applyTemplate(kind) {
  const base = emptyForm()
  if (kind === 'hiwin') {
    Object.assign(base, {
      name: '上银 HIWIN MES',
      url: 'https://itweb.hiwin.cn/java_demo_test/api',
      request_body_template: JSON.stringify(
        { api: 'hiwin/webcn/ai_error prevention_job_info/query', parameters: { job_no: '{job_no}' } },
        null, 2,
      ),
      success_path: 'statusCode', success_value: '200', array_path: 'response.resultData',
      fm_order_no: 'job_no', fm_customer_name: 'cust_name', fm_product_spec: 'spec',
      fm_planned_qty: 'dispatch_qty', fm_product_name: 'spec',
    })
  }
  // 保留正在编辑的 id/name(若已填)
  base.id = form.value.id
  form.value = base
  guessFields.value = []
  testResult.value = null
}

function buildPullConfig(f) {
  const auth = { type: f.auth_type }
  if (f.auth_type === 'basic') { auth.username = f.auth_user; auth.password = f.auth_pass }
  if (f.auth_type === 'bearer') { auth.token = f.auth_token }
  if (f.auth_type === 'api_key') { auth.header = f.auth_apikey_header || 'X-API-Key'; auth.value = f.auth_apikey_value }
  return {
    enabled: f.enabled,
    url: f.url, method: f.method, content_type: f.content_type,
    request_body_template: f.request_body_template,
    success_path: f.success_path, success_value: f.success_value,
    array_path: f.array_path,
    field_mapping: {
      order_no: f.fm_order_no, customer_name: f.fm_customer_name,
      product_spec: f.fm_product_spec, planned_qty: f.fm_planned_qty,
      product_name: f.fm_product_name,
    },
    import_mode: f.import_mode,
    auth,
    timeout_sec: f.timeout_sec, retry_count: f.retry_count, retry_interval_sec: f.retry_interval_sec,
    verify_ssl: f.verify_ssl,
    triggers: {
      manual: f.trig_manual, scheduled: f.trig_scheduled,
      interval_sec: f.trig_interval_sec, on_scan: f.trig_on_scan,
    },
  }
}

function openCreate() {
  form.value = emptyForm()
  editing.value = false
  guessFields.value = []
  testResult.value = null
  advancedOpen.value = []
  showEditor.value = true
}

function openEdit(row) {
  const p = row.config?.pull || {}
  const fm = p.field_mapping || {}
  const auth = p.auth || {}
  const trig = p.triggers || {}
  form.value = {
    id: row.id,
    name: row.name,
    enabled: !!row.pull_enabled,
    url: p.url || '', method: p.method || 'POST',
    content_type: p.content_type || 'application/json; charset=UTF-8',
    request_body_template: p.request_body_template || '',
    success_path: p.success_path || 'statusCode', success_value: String(p.success_value ?? '200'),
    array_path: p.array_path || '',
    fm_order_no: fm.order_no || '', fm_customer_name: fm.customer_name || '',
    fm_product_spec: fm.product_spec || '', fm_planned_qty: fm.planned_qty || '',
    fm_product_name: fm.product_name || '',
    import_mode: p.import_mode || 'upsert',
    auth_type: auth.type || 'none', auth_user: auth.username || '', auth_pass: auth.password || '',
    auth_token: auth.token || '', auth_apikey_header: auth.header || '', auth_apikey_value: auth.value || '',
    timeout_sec: p.timeout_sec ?? 10, retry_count: p.retry_count ?? 1,
    retry_interval_sec: p.retry_interval_sec ?? 2, verify_ssl: p.verify_ssl !== false,
    trig_manual: trig.manual !== false, trig_scheduled: !!trig.scheduled,
    trig_interval_sec: trig.interval_sec || 300, trig_on_scan: !!trig.on_scan,
  }
  form.value._conn = row  // 保留原连接(合并 config 时不丢推送配置)
  editing.value = true
  guessFields.value = []
  testResult.value = null
  advancedOpen.value = []
  showEditor.value = true
}

async function doTest() {
  if (!form.value.url) { ElMessage.warning('请先填接口地址'); return }
  testing.value = true
  try {
    const resp = await pullTest({ pull_config: buildPullConfig(form.value), job_no: testJobNo.value })
    const res = resp?.data ?? resp
    testResult.value = res
    const g = res.structure_guess
    if (g) {
      if (g.array_path && !form.value.array_path) form.value.array_path = g.array_path
      guessFields.value = g.fields || []
      autoGuessMapping(g.fields || [])
      advancedOpen.value = ['adv']  // 自动展开高级区让用户核对映射
    }
    dbg('mes.pull', '测试连接', `url=${form.value.url} → ${res.success ? '成功' : '失败:' + (res.error || '')} 识别字段=${(res.structure_guess?.fields || []).length}`)
    if (res.success) ElMessage.success('测试成功，已识别返回结构')
    else ElMessage.error('测试失败：' + (res.error || '未知'))
  } catch (e) {
    dbg('mes.pull', '测试连接异常', e?.response?.data?.detail || e.message)
    ElMessage.error('测试失败：' + (e?.response?.data?.detail || e.message))
  } finally {
    testing.value = false
  }
}

// 自动猜映射: 目标字段未填时, 按常见命名在返回字段里找最像的
function autoGuessMapping(fields) {
  const f = form.value
  const lower = fields.map(x => ({ raw: x, low: String(x).toLowerCase() }))
  const pick = (cands) => {
    for (const c of cands) {
      const hit = lower.find(x => x.low === c || x.low.includes(c))
      if (hit) return hit.raw
    }
    return ''
  }
  if (!f.fm_order_no) f.fm_order_no = pick(['job_no', 'order_no', 'orderno', 'workorder', 'job'])
  if (!f.fm_customer_name) f.fm_customer_name = pick(['cust_name', 'customer', 'cust'])
  if (!f.fm_product_spec) f.fm_product_spec = pick(['spec', 'material', 'product_spec'])
  if (!f.fm_planned_qty) f.fm_planned_qty = pick(['dispatch_qty', 'planned_qty', 'qty', 'plan_qty'])
  if (!f.fm_product_name) f.fm_product_name = pick(['product_name', 'prod_name', 'name', 'spec'])
}

async function save() {
  if (!form.value.name) { ElMessage.warning('请填配置名称'); return }
  if (!form.value.url) { ElMessage.warning('请填接口地址'); return }
  if (!form.value.fm_order_no) { ElMessage.warning('工单号字段映射必填（去重依据）'); return }
  saving.value = true
  try {
    const pullCfg = buildPullConfig(form.value)
    const existConn = form.value._conn
    const payload = {
      name: form.value.name,
      adapter_type: 'rest',
      enabled: existConn?.enabled ?? false,
      pull_enabled: form.value.enabled,
      pull_interval_sec: form.value.trig_interval_sec,
      config: { ...(existConn?.config || {}), pull: pullCfg },
      push_events: existConn?.push_events || [],
    }
    if (editing.value) await updateConnection(form.value.id, payload)
    else await createConnection(payload)
    ElMessage.success('已保存')
    showEditor.value = false
    await load()
  } catch (e) {
    ElMessage.error('保存失败：' + (e?.response?.data?.detail || e.message))
  } finally {
    saving.value = false
  }
}

async function togglePull(row) {
  try {
    await updateConnection(row.id, { pull_enabled: row.pull_enabled })
    ElMessage.success(row.pull_enabled ? '已启用' : '已停用')
  } catch (e) {
    row.pull_enabled = !row.pull_enabled
    ElMessage.error('操作失败：' + (e?.response?.data?.detail || e.message))
  }
}

async function doPull(row, dryRun) {
  const loadingKey = dryRun ? '_dry' : '_run'
  row[loadingKey] = true
  try {
    dbg('mes.pull', dryRun ? '试同步' : '立即同步', `配置=${row.name}`)
    const resp = await pullOrders(row.id, {
      job_no: '', dry_run: dryRun, max_items: dryRun ? 1 : null,
    })
    const r = resp?.data ?? resp
    pullResult.value = r
    showResult.value = true
    dbg('mes.pull', dryRun ? '试同步结果' : '立即同步结果',
        `${r?.success ? '成功' : '失败:' + (r?.error || '')} 取回${r?.fetched || 0} 新建${r?.created || 0} 更新${r?.updated || 0}`)
    await load()
  } catch (e) {
    dbg('mes.pull', '同步异常', e?.response?.data?.detail || e.message)
    ElMessage.error('同步失败：' + (e?.response?.data?.detail || e.message))
  } finally {
    row[loadingKey] = false
  }
}

async function doDelete(row) {
  try {
    await deleteConnection(row.id)
    ElMessage.success('已删除')
    await load()
  } catch (e) {
    ElMessage.error('删除失败：' + (e?.response?.data?.detail || e.message))
  }
}

async function load() {
  try {
    const res = await getConnections()
    connections.value = res.data || res || []
  } catch (e) {
    ElMessage.error('加载失败：' + (e?.response?.data?.detail || e.message))
  }
}

onMounted(load)
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
