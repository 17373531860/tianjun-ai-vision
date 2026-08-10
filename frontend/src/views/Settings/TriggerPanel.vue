<template>
  <div class="flex gap-4">
    <!-- 左：触发源管理 -->
    <div class="flex-1 min-w-0">
      <div class="text-xs text-gray-500 mb-2">
        触发中心：把「什么信号让系统做事」做成纯配置——虚拟按钮（遮画面标定区）/ 脚踏板 / 外部 URL / 串口报文 / 定时，
        任意触发源 × 任意动作（结算 / 清零 / 起停检测 / 事件 / 消警 / 写 PLC…），新触发方式不再改软件。
      </div>
      <div class="flex items-center justify-between mb-4">
        <h3 class="text-cyan-300 font-semibold">触发源</h3>
        <div class="flex gap-2">
          <el-dropdown @command="applyTemplate" size="small">
            <el-button size="small">方案模板 ▼</el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item v-for="t in templates" :key="t.key" :command="t.key">
                  {{ t.label }}
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
          <el-button size="small" @click="openImport">导入配置</el-button>
          <el-button size="small" type="success" @click="openAdd">添加触发源</el-button>
        </div>
      </div>

      <div v-if="triggers.length === 0" class="text-gray-500 text-sm text-center py-8">
        暂无触发源。点「方案模板」载入虚拟按钮 / 脚踏板 / HTTP 范式，或「添加触发源」从零配置
      </div>
      <div class="grid grid-cols-2 gap-3">
        <div v-for="trg in triggers" :key="trg.id"
             class="bg-slate-800/60 rounded-lg border p-4 cursor-pointer"
             :class="[trgStatus(trg) === 'running' ? 'border-green-700' : 'border-slate-700',
                      selectedId === trg.id ? 'ring-1 ring-cyan-500' : '']"
             @click="selectTrg(trg)">
          <div class="flex items-center justify-between mb-2">
            <span class="font-medium truncate">{{ trg.name }}</span>
            <div class="flex items-center gap-1 shrink-0">
              <el-tag size="small" effect="plain">{{ typeLabel(trg.type) }}</el-tag>
              <el-tag :type="statusTagType(trgStatus(trg))" size="small">
                {{ statusLabel(trgStatus(trg)) }}
              </el-tag>
            </div>
          </div>
          <div class="text-xs text-gray-400 space-y-1">
            <div>{{ trgSummary(trg) }}</div>
            <div>规则 {{ (trg.rules || []).length }} 条</div>
            <div v-if="trg.runtime" class="text-cyan-300/80">
              触发 {{ trg.runtime.counters?.fires ?? 0 }} · 拦截 {{ trg.runtime.counters?.suppressed ?? 0 }}
            </div>
            <div v-if="trg.runtime?.last_error" class="text-red-400 truncate" :title="trg.runtime.last_error">
              {{ trg.runtime.last_error }}
            </div>
          </div>
          <div class="flex items-center gap-1 mt-3 flex-wrap" @click.stop>
            <el-switch v-model="trg.enabled" size="small" inline-prompt
                       active-text="启用" inactive-text="停用"
                       @change="v => toggleEnabled(trg, v)" />
            <el-button size="small" type="primary" @click="editTrg(trg)">编辑</el-button>
            <el-button v-if="trg.type === 'pixel_region'" size="small"
                       @click="openCalibrator(trg)">标定</el-button>
            <el-button size="small" @click="exportTrg(trg)">导出</el-button>
            <el-button size="small" type="danger" @click="removeTrg(trg)">删除</el-button>
          </div>
        </div>
      </div>
    </div>

    <!-- 右：实时状态 -->
    <div class="w-[420px] shrink-0 bg-slate-800/50 rounded-lg border border-slate-700 p-4 overflow-auto">
      <div class="flex items-center justify-between mb-3">
        <h3 class="text-cyan-300 font-semibold">实时状态</h3>
        <span v-if="selectedTrg" class="text-xs text-gray-400 truncate max-w-[12rem]">{{ selectedTrg.name }}</span>
      </div>

      <div v-if="!selectedTrg" class="text-gray-500 text-sm text-center py-8">点击左侧卡片查看触发源状态与历史</div>
      <template v-else>
        <div v-if="!live" class="text-gray-500 text-sm text-center py-4">
          触发源未运行（启用后开始监听）
        </div>
        <template v-else>
          <!-- 源指标 -->
          <div class="space-y-1 mb-4 text-xs">
            <div v-for="(v, k) in sourceMetrics" :key="k"
                 class="flex items-center justify-between bg-slate-700/40 rounded px-2 py-1">
              <span class="font-mono text-cyan-200">{{ k }}</span>
              <span class="font-mono text-gray-200 truncate max-w-[14rem]">{{ formatValue(v) }}</span>
            </div>
            <div class="flex items-center justify-between bg-slate-700/40 rounded px-2 py-1">
              <span class="font-mono text-cyan-200">counters</span>
              <span class="font-mono text-gray-200">
                信号 {{ live.counters?.signals ?? 0 }} / 脉冲 {{ live.counters?.pulses ?? 0 }} /
                触发 {{ live.counters?.fires ?? 0 }} / 拦截 {{ live.counters?.suppressed ?? 0 }}
              </span>
            </div>
          </div>

          <!-- 联调操作 -->
          <div class="mb-4 space-y-1">
            <div class="text-xs text-gray-400 mb-1">现场联调</div>
            <div class="flex gap-1 flex-wrap">
              <el-button size="small" type="warning" plain
                         v-for="(rule, i) in (selectedTrg.rules || [])" :key="i"
                         @click="doTestFire(i)">
                试触发: {{ rule.name || `规则${i + 1}` }}
              </el-button>
              <template v-if="selectedTrg.type === 'mock'">
                <el-button size="small" type="info" plain @click="doMockFire">注入脉冲</el-button>
                <el-button size="small" type="info" plain @click="doMockLevel(true)">电平↑</el-button>
                <el-button size="small" type="info" plain @click="doMockLevel(false)">电平↓</el-button>
              </template>
              <el-button v-if="selectedTrg.type === 'pixel_region'" size="small" type="success" plain
                         @click="doCalibrate">一键标定参考帧</el-button>
            </div>
          </div>

          <!-- 触发历史 -->
          <div class="text-xs text-gray-400 mb-1">最近触发</div>
          <div class="space-y-1 mb-4">
            <div v-for="(h, i) in history" :key="i"
                 class="bg-slate-700/40 rounded px-2 py-1 text-xs flex gap-2">
              <span class="text-green-400 shrink-0">{{ h.rule }}</span>
              <span class="text-gray-400">{{ h.edge }}</span>
              <span class="text-gray-600 ml-auto shrink-0">{{ formatTs(h.ts) }}</span>
            </div>
            <div v-if="history.length === 0" class="text-gray-500 text-xs text-center py-2">暂无触发记录</div>
          </div>

          <!-- 日志 -->
          <div class="flex items-center justify-between mb-2">
            <div class="text-xs text-gray-400">日志（触发 / 动作 / 错误）</div>
            <el-button size="small" text @click="loadLogsNow">刷新</el-button>
          </div>
          <div class="space-y-1">
            <div v-for="(log, i) in logs" :key="i"
                 class="bg-slate-700/40 rounded px-2 py-1 text-xs flex gap-2"
                 :class="log.dir === 'error' ? 'border border-red-800/40' : ''">
              <span class="shrink-0" :class="log.dir === 'error' ? 'text-red-400' : 'text-green-400'">
                {{ log.dir === 'error' ? '错误' : '事件' }}
              </span>
              <span class="text-gray-300 min-w-0 break-all">{{ log.detail }}</span>
              <span class="text-gray-600 ml-auto shrink-0">{{ formatTs(log.ts) }}</span>
            </div>
            <div v-if="logs.length === 0" class="text-gray-500 text-xs text-center py-3">暂无日志</div>
          </div>
        </template>
      </template>
    </div>

    <!-- 添加/编辑对话框 -->
    <el-dialog v-model="showDialog" :title="editingId ? '编辑触发源' : '添加触发源'"
               width="760px" class="mes-dialog" destroy-on-close top="4vh">
      <el-form :model="form" label-width="100px" size="small">
        <div class="grid grid-cols-2 gap-x-4">
          <el-form-item label="名称" required>
            <el-input v-model="form.name" placeholder="如: 1号工位虚拟结算按钮" />
          </el-form-item>
          <el-form-item label="触发源类型">
            <el-select v-model="form.type" class="w-full" @change="onTypeChange">
              <el-option v-for="t in types" :key="t.name" :value="t.name"
                         :label="typeLabel(t.name) + (t.available ? '' : ' (依赖库未安装)')"
                         :disabled="!t.available" />
            </el-select>
          </el-form-item>
        </div>

        <!-- 类型参数 (按类型动态字段) -->
        <el-divider content-position="left"><span class="text-xs text-gray-400">触发源参数</span></el-divider>
        <div class="grid grid-cols-3 gap-x-4">
          <el-form-item v-for="f in typeFields(form.type)" :key="f.key" :label="f.label">
            <el-input-number v-if="f.type === 'number'" v-model="form.params[f.key]"
                             :precision="0" :step="1" class="w-full" controls-position="right" />
            <el-input-number v-else-if="f.type === 'float'" v-model="form.params[f.key]"
                             :step="f.step || 0.01" class="w-full" controls-position="right" />
            <el-switch v-else-if="f.type === 'bool'" v-model="form.params[f.key]"
                       :active-value="true" :inactive-value="false" />
            <el-select v-else-if="f.type === 'select'" v-model="form.params[f.key]" class="w-full">
              <el-option v-for="o in f.options" :key="o.value ?? o"
                         :label="o.label ?? String(o)" :value="o.value ?? o" />
            </el-select>
            <el-input v-else-if="f.type === 'json'" v-model="form.params[f.key]" type="textarea"
                      :rows="2" class="font-mono" :placeholder="f.placeholder || ' '" />
            <el-input v-else v-model="form.params[f.key]" :placeholder="f.placeholder || ' '" />
            <p v-if="f.hint" class="text-xs text-gray-500 leading-snug mt-0.5">{{ f.hint }}</p>
          </el-form-item>
        </div>
        <div v-if="form.type === 'pixel_region'" class="text-xs text-gray-500 -mt-1 mb-2 ml-2">
          区域坐标可先随意保存，然后用卡片上的「标定」在画面上框选并存参考帧
        </div>
        <div v-if="form.type === 'http'" class="text-xs text-gray-500 -mt-1 mb-2 ml-2">
          外部系统调用: POST /api/v1/triggers/fire/{{ form.params.key || '{key}' }}（配了密钥则带 X-Trigger-Secret 头）
        </div>

        <!-- 规则 (JSON) -->
        <el-divider content-position="left">
          <span class="text-xs text-gray-400">触发规则（JSON，模板已含常用范式；动作与 PLC 规则同一套）</span>
        </el-divider>
        <el-input v-model="form.rules_text" type="textarea" :rows="10"
                  class="font-mono" :class="{ 'json-error': jsonErrors.rules }" />
        <div v-if="jsonErrors.rules" class="text-red-400 text-xs mt-0.5">{{ jsonErrors.rules }}</div>
        <div class="text-xs text-gray-500 mt-1">
          可用动作: {{ actionNames.join(' / ') }}
        </div>
        <div class="mt-2">
          <div class="text-xs text-gray-400 mb-1">options — 默认工位 / 日志与历史条数</div>
          <el-input v-model="form.options_text" type="textarea" :rows="3"
                    class="font-mono" :class="{ 'json-error': jsonErrors.options }" />
          <div v-if="jsonErrors.options" class="text-red-400 text-xs mt-0.5">{{ jsonErrors.options }}</div>
        </div>
      </el-form>
      <template #footer>
        <el-checkbox v-model="form.enabled" class="float-left">保存后启用</el-checkbox>
        <el-button @click="showDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveTrg">保存</el-button>
      </template>
    </el-dialog>

    <!-- 画面标定器 -->
    <el-dialog v-model="showCalibrator" title="画面标定器 — 拖拽框选触发区域" width="860px"
               class="mes-dialog" destroy-on-close top="4vh">
      <div class="text-xs text-gray-400 mb-2">
        在画面上按住拖拽框选区域（坐标与原始帧 1:1）。保存区域后建议再点「存参考帧」把当前画面定为"未触发"基准。
      </div>
      <div class="relative inline-block select-none" @mousedown="calDown" @mousemove="calMove" @mouseup="calUp">
        <img ref="calImg" :src="calSnapshotUrl" class="max-w-full block" draggable="false"
             @load="calImgLoaded = true" />
        <div v-if="calRectStyle" class="absolute border-2 border-cyan-400 bg-cyan-400/15 pointer-events-none"
             :style="calRectStyle" />
      </div>
      <div class="text-xs text-gray-400 mt-2">
        区域: {{ calRegion ? calRegion.join(', ') : '未框选' }}
      </div>
      <template #footer>
        <el-button @click="refreshCalSnapshot">刷新画面</el-button>
        <el-button type="success" :disabled="!calTrg?.enabled" @click="doCalibrateFromDialog">
          存参考帧{{ calTrg?.enabled ? '' : '（需先启用）' }}
        </el-button>
        <el-button type="primary" :disabled="!calRegion" :loading="saving" @click="saveCalRegion">保存区域</el-button>
      </template>
    </el-dialog>

    <!-- 导入对话框 -->
    <el-dialog v-model="showImport" title="导入触发源配置" width="620px" class="mes-dialog" destroy-on-close>
      <div class="text-xs text-gray-400 mb-2">粘贴其他产线「导出」的配置 JSON（导入后默认停用，核对后再启用）</div>
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
  calibrateTrigger, createTrigger, deleteTrigger, enableTrigger,
  exportTrigger, getTriggerActions, getTriggerHistory, getTriggerLive,
  getTriggerLogs, getTriggers, getTriggerTemplates, getTriggerTypes,
  importTrigger, mockFireTrigger, mockLevelTrigger, testFireTrigger,
  updateTrigger,
} from '@/api/triggers'

const pollingStore = usePollingStore()

const triggers = ref([])
const types = ref([])
const templates = ref([])
const actionNames = ref([])
const selectedId = ref(null)
const live = ref(null)
const history = ref([])
const logs = ref([])
const saving = ref(false)

const showDialog = ref(false)
const showImport = ref(false)
const importText = ref('')
const editingId = ref(null)
const jsonErrors = reactive({ rules: '', options: '' })

const TYPE_LABELS = {
  pixel_region: '虚拟按钮(画面)', hid_key: 'HID 按键(踏板)', http: 'HTTP 触发',
  serial_pattern: '串口报文', timer: '定时', mock: '虚拟(联调)',
}

// 各类型参数字段 (声明式: 加类型 = 加一行)。type: number 整数 / float 小数 /
// bool 开关 / select 下拉 (支持 {label,value}) / csv 逗号分隔列表 / json 对象文本。
// csv/json 在 resetForm/saveTrg 双向转换, 存库仍是原生 list/dict。
const TYPE_FIELDS = {
  pixel_region: [
    { key: 'channel', label: '工位', type: 'number' },
    { key: 'mode', label: '比较模式', type: 'select', options: ['ref_diff', 'brightness', 'color_match'],
      hint: 'ref_diff=与参考帧差分(手遮按钮) / brightness=亮度过阈(指示灯) / color_match=颜色命中' },
    { key: 'threshold', label: '阈值', type: 'float', step: 1,
      hint: 'ref_diff/color_match: 0~441 色距; brightness: 0~255' },
    { key: 'sample_ms', label: '采样(ms)', type: 'number', hint: '默认 150, 最小 50' },
    { key: 'ref_drift', label: '参考帧缓漂', type: 'bool', default: true,
      hint: '开=参考帧缓慢跟随光照渐变(默认开, 仅未触发时更新)' },
    { key: 'ref_drift_alpha', label: '缓漂步长', type: 'float', step: 0.01,
      hint: '默认 0.02; 越大跟光照越快, 太大慢速手遮会被漂掉' },
    { key: 'invert', label: '反相触发', type: 'bool', hint: '开=低于阈值算触发' },
  ],
  hid_key: [
    { key: 'key', label: '按键名', placeholder: 'f9 / space / a',
      hint: '建议 F 区/小键盘等不常用键, 避免与正常打字冲突' },
    { key: 'long_press_ms', label: '长按(ms)', type: 'number',
      hint: '配了则区分长/短按(松开发脉冲); 不配按下即发' },
  ],
  http: [
    { key: 'key', label: '触发键', placeholder: 'start-line' },
    { key: 'secret', label: '共享密钥', placeholder: '可空',
      hint: '配了则请求须带 X-Trigger-Secret 头或 ?secret=' },
    { key: 'ip_allow', label: 'IP 白名单', type: 'csv', placeholder: '192.168., 10.0.',
      hint: '逗号分隔的 IP 前缀, 空=不限' },
    { key: 'extract', label: '变量提取', type: 'json', placeholder: '{"sn": "data.sn"}',
      hint: '请求 body 点路径 → 动作模板变量, 空=不提取' },
  ],
  serial_pattern: [
    { key: 'port', label: '串口', placeholder: 'COM3' },
    { key: 'baudrate', label: '波特率', type: 'number', hint: '默认 9600' },
    { key: 'pattern', label: '正则', placeholder: '^TRIG',
      hint: '命中即触发; 命名捕获组 (?P<var>...) 提为变量' },
    { key: 'bytesize', label: '数据位', type: 'select', options: [8, 7] },
    { key: 'parity', label: '校验位', type: 'select',
      options: [{ label: 'N (无)', value: 'N' }, { label: 'E (偶)', value: 'E' }, { label: 'O (奇)', value: 'O' }] },
    { key: 'stopbits', label: '停止位', type: 'select', options: [1, 2] },
    { key: 'line_ending', label: '行分隔符', type: 'select',
      options: [{ label: '\\n (默认)', value: '\n' }, { label: '\\r\\n', value: '\r\n' }, { label: 'raw (按块读)', value: 'raw' }] },
    { key: 'encoding', label: '编码', placeholder: 'ascii', hint: '默认 ascii, 解码错误忽略' },
  ],
  timer: [
    { key: 'interval_s', label: '间隔(秒)', type: 'number', hint: '每 N 秒一发, 启动即计时' },
    { key: 'daily', label: '每日定点', type: 'csv', placeholder: '08:00, 20:00',
      hint: 'HH:MM 逗号分隔可多个, 与间隔可并用' },
  ],
  mock: [],
}

// csv/json 字段 ↔ 文本 双向转换 (存库原生 list/dict, 表单里是字符串)
const paramsToForm = (type, params) => {
  const out = JSON.parse(JSON.stringify(params || {}))
  for (const f of (TYPE_FIELDS[type] || [])) {
    if (f.type === 'csv' && Array.isArray(out[f.key])) out[f.key] = out[f.key].join(', ')
    if (f.type === 'json' && out[f.key] && typeof out[f.key] === 'object') {
      out[f.key] = JSON.stringify(out[f.key])
    }
    // bool 缺省水合: 后端语义缺省开的开关 (如 ref_drift), 参数缺失时按缺省显示,
    // 避免"开关显示关、实际行为开"的误导
    if (f.type === 'bool' && typeof out[f.key] !== 'boolean') out[f.key] = !!f.default
  }
  return out
}
const paramsFromForm = (type, params) => {
  const out = JSON.parse(JSON.stringify(params || {}))
  for (const f of (TYPE_FIELDS[type] || [])) {
    if (f.type === 'csv') {
      const s = String(out[f.key] ?? '').trim()
      if (s) out[f.key] = s.split(/[,，]/).map(x => x.trim()).filter(Boolean)
      else delete out[f.key]
    }
    if (f.type === 'json') {
      const s = String(out[f.key] ?? '').trim()
      if (!s || s === '{}') { delete out[f.key]; continue }
      try { out[f.key] = JSON.parse(s) } catch {
        throw new Error(`「${f.label}」不是合法 JSON`)
      }
    }
  }
  return out
}

const form = reactive({
  name: '', type: 'pixel_region', enabled: false,
  params: {}, rules_text: '[]', options_text: '{}',
})

// ---------------- 展示辅助 ----------------

const selectedTrg = computed(() =>
  triggers.value.find(t => t.id === selectedId.value) || null)
const sourceMetrics = computed(() => live.value?.source || {})

const typeLabel = (t) => TYPE_LABELS[t] || t
const typeFields = (t) => TYPE_FIELDS[t] || []

const trgStatus = (trg) => trg.runtime?.status || (trg.enabled ? 'starting' : 'disabled')
const statusLabel = (s) => ({
  running: '运行中', error: '异常', config_error: '配置错误',
  starting: '启动中', stopped: '已停止', disabled: '停用',
}[s] || s)
const statusTagType = (s) => ({
  running: 'success', error: 'danger', config_error: 'danger',
  starting: 'warning', stopped: 'info', disabled: 'info',
}[s] || 'info')

const trgSummary = (trg) => {
  const p = trg.params || {}
  if (trg.type === 'pixel_region') return `工位${p.channel ?? 0} 区域[${(p.region || []).join(',')}] ${p.mode || 'ref_diff'}`
  if (trg.type === 'hid_key') return `按键 ${p.key}${p.long_press_ms ? ` (长按 ${p.long_press_ms}ms)` : ''}`
  if (trg.type === 'http') return `POST /api/v1/triggers/fire/${p.key || '?'}`
  if (trg.type === 'serial_pattern') return `${p.port || '?'} @ ${p.baudrate || 9600} ~ /${p.pattern || ''}/`
  if (trg.type === 'timer') return p.interval_s ? `每 ${p.interval_s}s` : `每日 ${(p.daily || []).join(' ')}`
  if (trg.type === 'mock') return '虚拟触发源 (联调/演示)'
  return ''
}

const formatValue = (v) => {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'boolean') return v ? 'TRUE' : 'FALSE'
  if (Array.isArray(v)) return v.join(', ')
  return String(v)
}
const formatTs = (ts) => new Date(ts * 1000).toLocaleTimeString('zh-CN', { hour12: false })

const errMsg = (e, fallback) => {
  const d = e?.response?.data?.detail
  return typeof d === 'string' ? d
    : Array.isArray(d) ? d.map(x => x.msg || JSON.stringify(x)).join('; ')
      : d ? JSON.stringify(d) : (fallback || '操作失败')
}

// ---------------- 数据加载与轮询 ----------------

let listTimer = null
let liveTimer = null

const loadTriggers = async () => {
  try {
    const { data } = await getTriggers()
    triggers.value = data.triggers || []
  } catch (e) { /* 轮询失败静默, 下轮重试 */ }
}

const loadLive = async () => {
  if (!selectedId.value) { live.value = null; return }
  try {
    const [lRes, hRes] = await Promise.all([
      getTriggerLive(selectedId.value),
      getTriggerHistory(selectedId.value, 20),
    ])
    live.value = lRes.data
    history.value = hRes.data.history || []
  } catch (e) {
    live.value = null
    history.value = []
  }
}

const loadLogsNow = async () => {
  if (!selectedId.value) return
  try {
    const { data } = await getTriggerLogs(selectedId.value, pollingStore.logLimit('trigger', 60))
    logs.value = data.logs || []
  } catch (e) { logs.value = [] }
}

const selectTrg = (trg) => {
  selectedId.value = trg.id
  live.value = null
  logs.value = []
  history.value = []
  loadLive()
  loadLogsNow()
}

onMounted(async () => {
  await pollingStore.load()
  loadTriggers()
  try {
    const [tRes, tplRes, aRes] = await Promise.all([
      getTriggerTypes(), getTriggerTemplates(), getTriggerActions()])
    types.value = tRes.data.types || []
    templates.value = tplRes.data.templates || []
    actionNames.value = aRes.data.actions || []
  } catch (e) { /* 首屏加载失败不阻塞面板 */ }
  listTimer = setInterval(loadTriggers, pollingStore.get('trigger_status', 3000))
  liveTimer = setInterval(() => { loadLive(); loadLogsNow() },
    pollingStore.get('trigger_live', 1500))
})

onUnmounted(() => {
  if (listTimer) clearInterval(listTimer)
  if (liveTimer) clearInterval(liveTimer)
})

// ---------------- CRUD ----------------

const resetForm = (cfg = null) => {
  editingId.value = null
  form.name = cfg?.name || ''
  form.type = cfg?.type || 'pixel_region'
  form.enabled = false
  form.params = paramsToForm(cfg?.type || 'pixel_region', cfg?.params)
  form.rules_text = JSON.stringify(cfg?.rules || [], null, 2)
  form.options_text = JSON.stringify(cfg?.options || { default_channel: 0 }, null, 2)
  jsonErrors.rules = jsonErrors.options = ''
}

const openAdd = () => { resetForm(); showDialog.value = true }

const applyTemplate = (key) => {
  const t = templates.value.find(x => x.key === key)
  if (!t) return
  resetForm(t.config)
  showDialog.value = true
  ElMessage.info(t.description || '已载入模板，请按现场情况改参数后保存')
}

const editTrg = (trg) => {
  resetForm(trg)
  editingId.value = trg.id
  form.enabled = trg.enabled
  showDialog.value = true
}

const onTypeChange = () => { form.params = paramsToForm(form.type, {}) }

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

const saveTrg = async () => {
  if (!form.name.trim()) { ElMessage.warning('请填写名称'); return }
  const rules = parseJsonField(form.rules_text, 'rules')
  const options = parseJsonField(form.options_text, 'options')
  if (rules === null || options === null) return
  let params
  try { params = paramsFromForm(form.type, form.params) } catch (e) {
    ElMessage.warning(e.message); return
  }
  const payload = {
    name: form.name.trim(), type: form.type, enabled: form.enabled,
    params, rules, options,
  }
  saving.value = true
  try {
    if (editingId.value) {
      await updateTrigger(editingId.value, payload)
    } else {
      await createTrigger(payload)
    }
    ElMessage.success('已保存' + (form.enabled ? '并启动触发源' : ''))
    showDialog.value = false
    loadTriggers()
  } catch (e) {
    ElMessage.error(errMsg(e, '保存失败'))
  } finally {
    saving.value = false
  }
}

const toggleEnabled = async (trg, enabled) => {
  try {
    await enableTrigger(trg.id, enabled)
    ElMessage.success(enabled ? '已启用, 触发源启动中' : '已停用')
    loadTriggers()
  } catch (e) {
    trg.enabled = !enabled
    ElMessage.error(errMsg(e))
  }
}

const removeTrg = async (trg) => {
  try {
    await ElMessageBox.confirm(`确定删除触发源「${trg.name}」? 规则配置将一并删除`, '删除确认', { type: 'warning' })
  } catch { return }
  try {
    await deleteTrigger(trg.id)
    if (selectedId.value === trg.id) { selectedId.value = null; live.value = null }
    ElMessage.success('已删除')
    loadTriggers()
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
}

// ---------------- 联调操作 ----------------

const doTestFire = async (ruleIndex) => {
  try {
    await testFireTrigger(selectedId.value, ruleIndex)
    ElMessage.success('已触发（动作在后台执行, 看日志确认）')
    loadLogsNow()
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
}

const doMockFire = async () => {
  try {
    await mockFireTrigger(selectedId.value)
    ElMessage.success('已注入脉冲')
  } catch (e) { ElMessage.error(errMsg(e)) }
}

const doMockLevel = async (v) => {
  try {
    await mockLevelTrigger(selectedId.value, v)
    ElMessage.success(`电平已置 ${v ? '高' : '低'}`)
  } catch (e) { ElMessage.error(errMsg(e)) }
}

const doCalibrate = async () => {
  try {
    const { data } = await calibrateTrigger(selectedId.value)
    ElMessage.success(`参考帧已标定: [${data.ref_bgr.join(', ')}]`)
  } catch (e) {
    ElMessage.error(errMsg(e, '标定失败（触发源需在运行且工位有画面）'))
  }
}

// ---------------- 画面标定器 ----------------

const showCalibrator = ref(false)
const calTrg = ref(null)
const calImg = ref(null)
const calImgLoaded = ref(false)
const calSnapshotUrl = ref('')
const calRegion = ref(null)      // 原始帧坐标 [x1,y1,x2,y2]
const calDrag = ref(null)        // {x, y} 显示坐标起点
const calRectDisp = ref(null)    // 显示坐标 [x1,y1,x2,y2]

const apiBase = () => {
  // /snapshot 挂在根路径 (不在 /api/v1 下)
  const base = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8001/api/v1'
  return base.replace(/\/api\/v1\/?$/, '')
}

const openCalibrator = (trg) => {
  calTrg.value = trg
  calRegion.value = (trg.params?.region || []).length === 4 ? [...trg.params.region] : null
  calRectDisp.value = null
  calImgLoaded.value = false
  refreshCalSnapshot()
  showCalibrator.value = true
}

const refreshCalSnapshot = () => {
  const ch = calTrg.value?.params?.channel ?? 0
  calSnapshotUrl.value = `${apiBase()}/snapshot?channel=${ch}&_t=${Date.now()}`
}

const _dispPos = (e) => {
  const rect = calImg.value.getBoundingClientRect()
  return {
    x: Math.max(0, Math.min(e.clientX - rect.left, rect.width)),
    y: Math.max(0, Math.min(e.clientY - rect.top, rect.height)),
  }
}

const calDown = (e) => {
  if (!calImgLoaded.value) return
  calDrag.value = _dispPos(e)
  calRectDisp.value = null
}

const calMove = (e) => {
  if (!calDrag.value) return
  const p = _dispPos(e)
  calRectDisp.value = [
    Math.min(calDrag.value.x, p.x), Math.min(calDrag.value.y, p.y),
    Math.max(calDrag.value.x, p.x), Math.max(calDrag.value.y, p.y),
  ]
}

const calUp = () => {
  if (!calDrag.value || !calRectDisp.value) { calDrag.value = null; return }
  calDrag.value = null
  // 显示坐标 → 原始帧坐标 (img 可能被 max-w-full 缩放)
  const img = calImg.value
  const sx = img.naturalWidth / img.clientWidth
  const sy = img.naturalHeight / img.clientHeight
  const [x1, y1, x2, y2] = calRectDisp.value
  calRegion.value = [
    Math.round(x1 * sx), Math.round(y1 * sy),
    Math.round(x2 * sx), Math.round(y2 * sy),
  ]
}

const calRectStyle = computed(() => {
  // 优先显示正在拖的框; 没有则把已存 region 反算回显示坐标
  let disp = calRectDisp.value
  if (!disp && calRegion.value && calImgLoaded.value && calImg.value?.naturalWidth) {
    const img = calImg.value
    const sx = img.clientWidth / img.naturalWidth
    const sy = img.clientHeight / img.naturalHeight
    const [x1, y1, x2, y2] = calRegion.value
    disp = [x1 * sx, y1 * sy, x2 * sx, y2 * sy]
  }
  if (!disp) return null
  return {
    left: `${disp[0]}px`, top: `${disp[1]}px`,
    width: `${disp[2] - disp[0]}px`, height: `${disp[3] - disp[1]}px`,
  }
})

const saveCalRegion = async () => {
  if (!calRegion.value || !calTrg.value) return
  saving.value = true
  try {
    const params = { ...(calTrg.value.params || {}), region: calRegion.value }
    delete params.ref_bgr   // 区域变了, 旧参考帧作废 (启用后重新标定)
    await updateTrigger(calTrg.value.id, { params })
    ElMessage.success('区域已保存（触发源已热重载; 建议再点「存参考帧」）')
    loadTriggers()
    calTrg.value = { ...calTrg.value, params }
  } catch (e) {
    ElMessage.error(errMsg(e, '保存失败'))
  } finally {
    saving.value = false
  }
}

const doCalibrateFromDialog = async () => {
  try {
    const { data } = await calibrateTrigger(calTrg.value.id)
    ElMessage.success(`参考帧已标定: [${data.ref_bgr.join(', ')}]`)
  } catch (e) {
    ElMessage.error(errMsg(e, '标定失败（触发源需已启用且工位有画面）'))
  }
}

// ---------------- 导入导出 ----------------

const exportTrg = async (trg) => {
  try {
    const { data } = await exportTrigger(trg.id)
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
    await importTrigger(cfg)
    ElMessage.success('导入成功（默认停用，核对后再启用）')
    showImport.value = false
    loadTriggers()
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
