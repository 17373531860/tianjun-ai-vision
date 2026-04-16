<template>
  <div>
    <!-- 工具栏 -->
    <div class="flex items-center justify-between mb-4">
      <div class="flex items-center gap-3">
        <el-input v-model="keyword" placeholder="搜索工单号/产品名..." size="small" class="w-52" clearable @clear="loadOrders" @keyup.enter="loadOrders" />
        <el-select v-model="filterStatus" placeholder="状态" size="small" class="w-28" clearable @change="loadOrders">
          <el-option label="草稿" value="draft" />
          <el-option label="待生产" value="pending" />
          <el-option label="生产中" value="in_progress" />
          <el-option label="已暂停" value="paused" />
          <el-option label="已完成" value="completed" />
          <el-option label="已取消" value="cancelled" />
        </el-select>
        <el-button size="small" type="primary" @click="loadOrders">查询</el-button>
      </div>
      <div class="flex items-center gap-2">
        <el-switch v-model="autoRefresh" size="small" active-text="自动刷新" @change="toggleAutoRefresh" />
        <el-button size="small" type="success" @click="openCreate">新建工单</el-button>
      </div>
    </div>

    <!-- 工单列表 -->
    <el-table :data="orders" stripe size="small" class="mes-table" max-height="calc(100vh - 240px)">
      <el-table-column prop="order_no" label="工单号" width="140" />
      <el-table-column prop="product_name" label="产品" width="120" />
      <el-table-column prop="product_code" label="产品编码" width="100" />
      <el-table-column label="来源" width="80">
        <template #default="{ row }">
          <el-tag :type="row.source === 'external' ? 'warning' : 'info'" size="small">
            {{ row.source === 'external' ? '外部' : '手动' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="status" label="状态" width="100">
        <template #default="{ row }">
          <el-tag :type="statusType(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="进度" width="180">
        <template #default="{ row }">
          <div class="flex items-center gap-2">
            <el-progress
              :percentage="row.planned_qty > 0 ? Math.min(100, Math.round(row.completed_qty / row.planned_qty * 100)) : 0"
              :stroke-width="12" :text-inside="true" style="flex:1"
            />
            <span class="text-xs text-gray-400 whitespace-nowrap">{{ row.completed_qty }}/{{ row.planned_qty }}</span>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="良品/不良" width="120">
        <template #default="{ row }">
          <span class="text-green-400">{{ row.good_qty }}</span> /
          <span class="text-red-400">{{ row.ng_qty }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="yield_rate" label="良率" width="80">
        <template #default="{ row }">
          <span :class="row.yield_rate >= 95 ? 'text-green-400' : row.yield_rate >= 80 ? 'text-yellow-400' : 'text-red-400'">
            {{ row.yield_rate != null ? row.yield_rate + '%' : '-' }}
          </span>
        </template>
      </el-table-column>
      <el-table-column prop="created_at" label="创建时间" width="160">
        <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" min-width="260" fixed="right">
        <template #default="{ row }">
          <div class="flex gap-1 flex-wrap">
            <el-button v-if="row.status === 'draft'" size="small" type="warning" @click="changeStatus(row, 'pending')">提交</el-button>
            <el-button v-if="row.status === 'pending'" size="small" type="success" @click="changeStatus(row, 'in_progress')">开始</el-button>
            <el-button v-if="row.status === 'in_progress'" size="small" type="warning" @click="changeStatus(row, 'paused')">暂停</el-button>
            <el-button v-if="row.status === 'paused'" size="small" type="success" @click="changeStatus(row, 'in_progress')">恢复</el-button>
            <el-button v-if="row.status === 'in_progress'" size="small" type="primary" @click="changeStatus(row, 'completed')">完成</el-button>
            <el-button size="small" @click="editOrder(row)">编辑</el-button>
            <el-button v-if="hasExtraData(row)" size="small" type="info" @click="showExtraData(row)">附加</el-button>
            <el-button v-if="['draft','cancelled'].includes(row.status)" size="small" type="danger" @click="handleDelete(row)">删除</el-button>
          </div>
        </template>
      </el-table-column>
    </el-table>

    <!-- 分页 -->
    <div class="flex justify-end mt-3">
      <el-pagination
        v-model:current-page="currentPage" v-model:page-size="pageSize"
        :total="total" :page-sizes="[20,50,100]"
        layout="total, sizes, prev, pager, next"
        small @size-change="loadOrders" @current-change="loadOrders"
      />
    </div>

    <!-- 新建/编辑 对话框 -->
    <el-dialog v-model="showCreate" :title="editingId ? '编辑工单' : '新建工单'" width="560px" class="mes-dialog" destroy-on-close>
      <el-form :model="form" label-width="80px" size="small">
        <el-form-item label="工单号" required>
          <el-input v-model="form.order_no" :disabled="!!editingId" />
        </el-form-item>
        <el-form-item label="产品名称" required>
          <el-input v-model="form.product_name" />
        </el-form-item>
        <el-form-item label="产品编码">
          <el-input v-model="form.product_code" />
        </el-form-item>
        <el-form-item label="规格">
          <el-input v-model="form.product_spec" />
        </el-form-item>
        <el-form-item label="计划数量">
          <el-input-number v-model="form.planned_qty" :min="0" :precision="2" />
        </el-form-item>
        <el-form-item label="优先级">
          <el-select v-model="form.priority" class="w-full">
            <el-option :value="1" label="紧急" />
            <el-option :value="2" label="高" />
            <el-option :value="3" label="正常" />
            <el-option :value="4" label="低" />
          </el-select>
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="form.remark" type="textarea" :rows="2" />
        </el-form-item>

        <!-- extra_data 手动添加 -->
        <el-divider content-position="left">附加字段 (extra_data)</el-divider>
        <div v-for="(item, idx) in extraItems" :key="idx" class="flex items-center gap-2 mb-2">
          <el-input v-model="item.key" placeholder="字段名" size="small" class="w-36" />
          <el-input v-model="item.value" placeholder="值" size="small" style="flex:1" />
          <el-button size="small" type="danger" circle @click="extraItems.splice(idx, 1)">
            <el-icon><Close /></el-icon>
          </el-button>
        </div>
        <el-button size="small" type="primary" plain @click="extraItems.push({ key: '', value: '' })">
          + 添加字段
        </el-button>
      </el-form>
      <template #footer>
        <el-button size="small" @click="showCreate = false">取消</el-button>
        <el-button size="small" type="primary" @click="handleSave" :loading="saving">保存</el-button>
      </template>
    </el-dialog>

    <!-- extra_data 查看/编辑 对话框 -->
    <el-dialog v-model="showExtra" title="附加字段 (extra_data)" width="520px" class="mes-dialog" destroy-on-close>
      <div v-if="!editingExtra">
        <el-descriptions :column="1" border size="small" v-if="Object.keys(currentExtraData).length > 0">
          <el-descriptions-item v-for="(val, key) in currentExtraData" :key="key" :label="key">
            {{ typeof val === 'object' ? JSON.stringify(val) : val }}
          </el-descriptions-item>
        </el-descriptions>
        <el-empty v-else description="暂无附加字段" :image-size="60" />
      </div>
      <div v-else>
        <div v-for="(item, idx) in editExtraItems" :key="idx" class="flex items-center gap-2 mb-2">
          <el-input v-model="item.key" placeholder="字段名" size="small" class="w-36" />
          <el-input v-model="item.value" placeholder="值" size="small" style="flex:1" />
          <el-button size="small" type="danger" circle @click="editExtraItems.splice(idx, 1)">
            <el-icon><Close /></el-icon>
          </el-button>
        </div>
        <el-button size="small" type="primary" plain @click="editExtraItems.push({ key: '', value: '' })">
          + 添加字段
        </el-button>
      </div>
      <template #footer>
        <template v-if="!editingExtra">
          <el-button size="small" @click="showExtra = false">关闭</el-button>
          <el-button size="small" type="primary" @click="startEditExtra">编辑</el-button>
        </template>
        <template v-else>
          <el-button size="small" @click="editingExtra = false">取消</el-button>
          <el-button size="small" type="primary" @click="saveExtraData" :loading="savingExtra">保存</el-button>
        </template>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Close } from '@element-plus/icons-vue'
import { getOrders, createOrder, updateOrder, changeOrderStatus, deleteOrder, updateOrderExtraData } from '@/api/mes'

const orders = ref([])
const total = ref(0)
const currentPage = ref(1)
const pageSize = ref(50)
const keyword = ref('')
const filterStatus = ref('')
const showCreate = ref(false)
const editingId = ref(null)
const saving = ref(false)
const autoRefresh = ref(false)
let refreshTimer = null

const extraItems = ref([])

const showExtra = ref(false)
const editingExtra = ref(false)
const currentExtraData = ref({})
const currentExtraOrderId = ref(null)
const editExtraItems = ref([])
const savingExtra = ref(false)

const defaultForm = () => ({
  order_no: '', product_name: '', product_code: '', product_spec: '',
  planned_qty: 0, priority: 3, remark: '',
})
const form = ref(defaultForm())

const statusLabel = (s) => ({ draft: '草稿', pending: '待生产', in_progress: '生产中', paused: '已暂停', completed: '已完成', cancelled: '已取消' }[s] || s)
const statusType = (s) => ({ draft: 'info', pending: 'warning', in_progress: 'success', paused: '', completed: 'primary', cancelled: 'danger' }[s] || '')

const formatTime = (t) => {
  if (!t) return '-'
  return t.replace('T', ' ').substring(0, 19)
}

const hasExtraData = (row) => row.extra_data && Object.keys(row.extra_data).length > 0

const loadOrders = async () => {
  try {
    const res = await getOrders({
      keyword: keyword.value || undefined,
      status: filterStatus.value || undefined,
      skip: (currentPage.value - 1) * pageSize.value,
      limit: pageSize.value,
    })
    orders.value = res.data.items || []
    total.value = res.data.total || 0
  } catch (e) {
    ElMessage.error('加载工单失败')
  }
}

const toggleAutoRefresh = (val) => {
  if (val) {
    refreshTimer = setInterval(loadOrders, 10000)
  } else {
    if (refreshTimer) clearInterval(refreshTimer)
    refreshTimer = null
  }
}

const buildExtraData = (items) => {
  const data = {}
  for (const item of items) {
    if (item.key && item.key.trim()) data[item.key.trim()] = item.value
  }
  return Object.keys(data).length > 0 ? data : null
}

const openCreate = () => {
  editingId.value = null
  form.value = defaultForm()
  extraItems.value = []
  showCreate.value = true
}

const handleSave = async () => {
  if (!form.value.order_no || !form.value.product_name) {
    ElMessage.warning('请填写工单号和产品名称')
    return
  }
  saving.value = true
  try {
    const payload = { ...form.value, extra_data: buildExtraData(extraItems.value) }
    if (editingId.value) {
      await updateOrder(editingId.value, payload)
      ElMessage.success('工单已更新')
    } else {
      await createOrder(payload)
      ElMessage.success('工单已创建')
    }
    showCreate.value = false
    loadOrders()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}

const editOrder = (row) => {
  editingId.value = row.id
  form.value = {
    order_no: row.order_no, product_name: row.product_name,
    product_code: row.product_code, product_spec: row.product_spec,
    planned_qty: row.planned_qty, priority: row.priority,
    remark: row.remark,
  }
  const extra = row.extra_data || {}
  extraItems.value = Object.entries(extra).map(([key, value]) => ({
    key,
    value: typeof value === 'object' ? JSON.stringify(value) : String(value ?? ''),
  }))
  showCreate.value = true
}

const changeStatus = async (row, status) => {
  try {
    await changeOrderStatus(row.id, { status })
    ElMessage.success(`工单 ${row.order_no} 状态已变更为 ${statusLabel(status)}`)
    loadOrders()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '状态变更失败')
  }
}

const handleDelete = async (row) => {
  try {
    await ElMessageBox.confirm(`确定删除工单 ${row.order_no}？`, '确认')
    await deleteOrder(row.id)
    ElMessage.success('工单已删除')
    loadOrders()
  } catch (e) {
    if (e !== 'cancel' && e !== 'close')
      ElMessage.error('删除失败: ' + (e.response?.data?.detail || e.message || '未知错误'))
  }
}

const showExtraData = (row) => {
  currentExtraData.value = row.extra_data || {}
  currentExtraOrderId.value = row.id
  editingExtra.value = false
  showExtra.value = true
}

const startEditExtra = () => {
  editExtraItems.value = Object.entries(currentExtraData.value).map(([key, value]) => ({
    key,
    value: typeof value === 'object' ? JSON.stringify(value) : String(value ?? ''),
  }))
  editingExtra.value = true
}

const saveExtraData = async () => {
  savingExtra.value = true
  try {
    const data = {}
    for (const item of editExtraItems.value) {
      if (item.key && item.key.trim()) data[item.key.trim()] = item.value
    }
    const res = await updateOrderExtraData(currentExtraOrderId.value, data)
    currentExtraData.value = res.data.extra_data || {}
    editingExtra.value = false
    ElMessage.success('附加字段已更新')
    loadOrders()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '保存失败')
  } finally {
    savingExtra.value = false
  }
}

onMounted(() => {
  form.value = defaultForm()
  loadOrders()
})

onBeforeUnmount(() => {
  if (refreshTimer) clearInterval(refreshTimer)
})
</script>

<style scoped>
.mes-table :deep(.el-table__header th) {
  background: #1e293b !important;
  color: #94a3b8;
}
.mes-table :deep(.el-table__row) {
  background: #0f172a;
}
.mes-table :deep(.el-table__row:hover > td) {
  background: #1e293b !important;
}
.mes-dialog :deep(.el-dialog) {
  background: #1e293b;
  border: 1px solid #334155;
}
.mes-dialog :deep(.el-dialog__title) {
  color: #e2e8f0;
}
.mes-dialog :deep(.el-form-item__label) {
  color: #94a3b8;
}
.mes-dialog :deep(.el-divider__text) {
  color: #94a3b8;
  background: #1e293b;
}
</style>
