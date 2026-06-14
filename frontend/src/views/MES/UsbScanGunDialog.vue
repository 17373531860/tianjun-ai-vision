<template>
  <el-dialog
    :model-value="modelValue"
    :title="editDev ? '编辑 USB 扫码枪' : '添加 USB 扫码枪'"
    width="640px"
    destroy-on-close
    @update:model-value="$emit('update:modelValue', $event)"
    @closed="$emit('close')"
  >
    <div class="text-xs text-gray-500 mb-4">
      USB 键盘式扫码枪（如 NT-1202W）插上即用、无需 IP。本机全局捕获扫到的码，按下面的用途自动处理（任意页面、含全屏检测页都生效）。
    </div>

    <el-form :model="form" label-width="130px" size="default">
      <el-form-item label="名称" required>
        <el-input v-model="form.name" placeholder="如：包装线 USB 扫码枪" class="w-72" />
      </el-form-item>

      <el-form-item label="启用">
        <el-switch v-model="form.enabled" />
        <span class="text-xs text-gray-500 ml-3">插上 USB 扫码枪后打开（同时只建议启用一把 USB 枪）</span>
      </el-form-item>

      <el-form-item label="扫到的码用来">
        <el-radio-group v-model="form.usage">
          <el-radio-button label="pull">拉工单</el-radio-button>
          <el-radio-button label="bind">绑工件</el-radio-button>
          <el-radio-button label="both">两者(按规则区分)</el-radio-button>
        </el-radio-group>
        <div class="text-xs text-gray-500 mt-1">
          拉工单 = 扫工单标签条码去外部 MES 查回工单；绑工件 = 扫工件条码绑定到检测周期；两者 = 一把枪都干，按工单号规则自动分。
        </div>
      </el-form-item>

      <el-form-item v-if="form.usage !== 'bind'" label="拉工单用哪条连接">
        <el-select v-model="form.pullConnId" placeholder="选已配好的工单拉取连接" class="w-72">
          <el-option v-for="c in pullConns" :key="c.id" :label="c.name" :value="c.id" />
        </el-select>
        <span v-if="!pullConns.length" class="text-xs text-orange-400 ml-2">还没有拉取连接，先去「工单拉取」页建一条</span>
      </el-form-item>

      <el-form-item v-if="form.usage !== 'pull'" label="绑工件到工位">
        <el-select v-model="form.bindChannelId" class="w-40">
          <el-option v-for="n in 4" :key="n - 1" :label="`工位 ${n}`" :value="n - 1" />
        </el-select>
        <span class="text-xs text-gray-500 ml-2">单工位选工位 1 即可</span>
      </el-form-item>

      <el-form-item v-if="form.usage === 'both'" label="工单号识别规则">
        <el-input v-model="form.orderPattern" placeholder="^(JOB|ORD)" class="w-72" />
        <div class="text-xs text-gray-500 mt-1">
          正则表达式。扫到的码匹配上 = 当工单去拉取，匹配不上 = 当工件去绑定。默认匹配 JOB / ORD 开头（上银工单号格式）。
        </div>
      </el-form-item>

      <el-divider content-position="left"><span class="text-cyan-300 text-xs">扫一下试试（不用真扫码枪）</span></el-divider>
      <el-form-item label="模拟扫一个码">
        <el-input v-model="testCode" placeholder="如 JOB260500444-136 或工件码" class="w-72" />
        <el-tag v-if="testCode" size="small" :type="testRoute === 'pull' ? 'warning' : 'primary'" class="ml-2">
          会去：{{ testRoute === 'pull' ? '拉工单' : '绑工件' }}
        </el-tag>
        <el-button size="small" type="primary" class="ml-2" :disabled="!testCode" :loading="testing" @click="runTest">
          真跑一次
        </el-button>
      </el-form-item>
    </el-form>

    <template #footer>
      <el-button @click="$emit('update:modelValue', false)">取消</el-button>
      <el-button type="primary" :loading="saving" @click="save">保存</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { ElMessage, ElNotification } from 'element-plus'
import { getConnections, pullOrders } from '@/api/gateway'
import { createScannerDevice, updateScannerDevice, simulateScannerScan } from '@/api/scanner'
import { routeCode, refreshScanGunConfig } from '@/composables/useScanGun'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  editDev: { type: Object, default: null },
})
const emit = defineEmits(['update:modelValue', 'close', 'saved'])

const connections = ref([])
const testCode = ref('')
const testing = ref(false)
const saving = ref(false)

const pullConns = computed(() => connections.value.filter(c => c.config?.pull?.url))
const testRoute = computed(() => routeCode(form.value, testCode.value))

function emptyForm() {
  return { name: '', enabled: true, usage: 'pull', pullConnId: null, bindChannelId: 0, orderPattern: '^(JOB|ORD)' }
}
const form = ref(emptyForm())

function loadFromDev(dev) {
  if (!dev) { form.value = emptyForm(); return }
  const u = (dev.parse_config || {}).usb || {}
  form.value = {
    name: dev.name || '',
    enabled: dev.enabled !== false,
    usage: u.usage || 'pull',
    pullConnId: u.pull_conn_id ?? null,
    bindChannelId: dev.channel_id || 0,
    orderPattern: u.order_pattern || '^(JOB|ORD)',
  }
}

// 对话框每次打开时回填
watch(() => props.modelValue, (v) => { if (v) loadFromDev(props.editDev) })

function buildPayload() {
  return {
    name: form.value.name,
    device_type: 'usb_hid',
    ip: '',
    port: 0,
    channel_id: form.value.bindChannelId || 0,
    enabled: form.value.enabled,
    parse_mode: 'direct',
    parse_config: {
      usb: {
        usage: form.value.usage,
        pull_conn_id: form.value.pullConnId,
        order_pattern: form.value.orderPattern,
      },
    },
  }
}

async function save() {
  if (!form.value.name) { ElMessage.warning('请填名称'); return }
  if (form.value.usage !== 'bind' && !form.value.pullConnId) {
    ElMessage.warning('拉工单用途需选一条拉取连接'); return
  }
  saving.value = true
  try {
    const payload = buildPayload()
    if (props.editDev?.id) await updateScannerDevice(props.editDev.id, payload)
    else await createScannerDevice(payload)
    ElMessage.success('已保存')
    await refreshScanGunConfig()  // 让全局键盘监听立刻用上新配置
    emit('saved')
    emit('update:modelValue', false)
  } catch (e) {
    ElMessage.error('保存失败：' + (e?.response?.data?.detail || e.message))
  } finally {
    saving.value = false
  }
}

async function runTest() {
  testing.value = true
  try {
    const route = routeCode(form.value, testCode.value)
    if (route === 'pull') {
      if (!form.value.pullConnId) { ElNotification.warning({ title: '先选拉取连接' }); return }
      const resp = await pullOrders(form.value.pullConnId, { job_no: testCode.value, dry_run: false })
      const r = resp?.data ?? resp
      r?.success
        ? ElNotification.success({ title: '拉工单成功', message: `新建 ${r.created || 0} / 更新 ${r.updated || 0}` })
        : ElNotification.error({ title: '拉工单失败', message: r?.error || '未知' })
    } else {
      await simulateScannerScan({ barcode: testCode.value, channel_id: form.value.bindChannelId || 0 })
      ElNotification.success({ title: '已注入绑定链路', message: `工位${(form.value.bindChannelId || 0) + 1}：${testCode.value}` })
    }
  } catch (e) {
    ElNotification.error({ title: '测试异常', message: e?.response?.data?.detail || e?.message || String(e) })
  } finally {
    testing.value = false
  }
}

async function loadConns() {
  try {
    const resp = await getConnections()
    connections.value = resp?.data ?? resp ?? []
  } catch {
    connections.value = []
  }
}

onMounted(loadConns)
</script>
