<template>
  <div>
    <div class="flex gap-4">
      <!-- 左: 缺陷列表 -->
      <div class="flex-1">
        <div class="flex items-center gap-3 mb-4 flex-wrap">
          <el-select v-model="filterCategory" placeholder="分类" size="small" class="w-28" clearable @change="loadDefects">
            <el-option label="外观" value="appearance" />
            <el-option label="尺寸" value="dimension" />
            <el-option label="功能" value="function" />
            <el-option label="其他" value="other" />
          </el-select>
          <el-select v-model="filterSeverity" placeholder="严重度" size="small" class="w-28" clearable @change="loadDefects">
            <el-option label="轻微" value="minor" />
            <el-option label="一般" value="major" />
            <el-option label="严重" value="critical" />
          </el-select>
          <el-button size="small" type="primary" @click="loadDefects">查询</el-button>
          <el-button size="small" @click="showCodeMgr = true">缺陷代码管理</el-button>
          <el-button size="small" type="danger" plain :disabled="selected.length === 0"
                     @click="handleBatchDeleteDefect">
            批量删除{{ selected.length > 0 ? `(${selected.length})` : '' }}
          </el-button>
        </div>

        <el-table :data="defects" stripe size="small" class="mes-table" max-height="calc(100vh - 320px)"
                  @selection-change="handleSelectionChange">
          <el-table-column type="selection" width="42" />
          <el-table-column prop="defect_code" label="缺陷代码" width="120" />
          <el-table-column prop="defect_name" label="缺陷名称" width="140" />
          <el-table-column prop="defect_category" label="分类" width="80" />
          <el-table-column prop="severity" label="严重度" width="80">
            <template #default="{ row }">
              <el-tag :type="row.severity === 'critical' ? 'danger' : row.severity === 'major' ? 'warning' : 'info'" size="small">
                {{ { minor: '轻微', major: '一般', critical: '严重' }[row.severity] || row.severity }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="detection_label" label="检测标签" width="120" />
          <el-table-column prop="source" label="来源" width="80" />
          <el-table-column prop="created_at" label="时间" width="160">
            <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="80" fixed="right">
            <template #default="{ row }">
              <el-button size="small" type="danger" plain @click="handleDeleteDefect(row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>

        <div class="flex justify-end mt-3">
          <el-pagination
            v-model:current-page="currentPage" v-model:page-size="pageSize"
            :total="total" :page-sizes="[20,50,100]"
            layout="total, sizes, prev, pager, next"
            small @size-change="loadDefects" @current-change="loadDefects"
          />
        </div>
      </div>

      <!-- 右: Pareto 图 -->
      <div class="w-[380px] bg-slate-800/50 rounded-lg border border-slate-700 p-4">
        <h3 class="text-cyan-300 text-base font-semibold mb-3">缺陷 Pareto</h3>
        <div v-if="paretoData.length === 0" class="text-gray-500 text-sm text-center py-8">暂无数据</div>
        <div v-else class="space-y-2">
          <div v-for="(item, idx) in paretoData" :key="item.code" class="flex items-center gap-2">
            <span class="w-5 text-xs text-gray-400 text-right">{{ idx + 1 }}</span>
            <div class="flex-1">
              <div class="flex justify-between text-xs mb-1">
                <span>{{ item.name }}</span>
                <span class="text-gray-400">{{ item.count }}</span>
              </div>
              <div class="h-2 bg-slate-700 rounded-full overflow-hidden">
                <div class="h-full rounded-full transition-all"
                     :style="{ width: maxPareto > 0 ? (item.count / maxPareto * 100) + '%' : '0%' }"
                     :class="idx === 0 ? 'bg-red-500' : idx === 1 ? 'bg-orange-500' : 'bg-yellow-500'" />
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 缺陷代码管理对话框 -->
    <el-dialog v-model="showCodeMgr" title="缺陷代码管理" width="600px" class="mes-dialog" destroy-on-close>
      <div class="flex justify-end mb-3">
        <el-button size="small" type="success" @click="showAddCode = true">新增代码</el-button>
      </div>
      <el-table :data="codes" size="small" class="mes-table" max-height="400">
        <el-table-column prop="code" label="代码" width="120" />
        <el-table-column prop="name" label="名称" width="140" />
        <el-table-column prop="category" label="分类" width="80" />
        <el-table-column prop="severity" label="严重度" width="80" />
        <el-table-column prop="detection_labels" label="映射标签">
          <template #default="{ row }">
            <span class="text-xs text-gray-400">{{ (row.detection_labels || []).join(', ') || '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="100">
          <template #default="{ row }">
            <el-button size="small" type="danger" @click="handleDeleteCode(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <!-- 新增代码小表单 -->
      <div v-if="showAddCode" class="mt-4 p-3 bg-slate-700/50 rounded">
        <div class="grid grid-cols-2 gap-2">
          <el-input v-model="codeForm.code" placeholder="代码" size="small" />
          <el-input v-model="codeForm.name" placeholder="名称" size="small" />
          <el-select v-model="codeForm.category" size="small" class="w-full">
            <el-option label="外观" value="appearance" />
            <el-option label="尺寸" value="dimension" />
            <el-option label="功能" value="function" />
            <el-option label="其他" value="other" />
          </el-select>
          <el-select v-model="codeForm.severity" size="small" class="w-full">
            <el-option label="轻微" value="minor" />
            <el-option label="一般" value="major" />
            <el-option label="严重" value="critical" />
          </el-select>
        </div>
        <el-input v-model="codeForm.labelsStr" placeholder="映射标签 (逗号分隔)" size="small" class="mt-2" />
        <div class="flex justify-end gap-2 mt-2">
          <el-button size="small" @click="showAddCode = false">取消</el-button>
          <el-button size="small" type="primary" @click="handleAddCode">保存</el-button>
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getDefects, getPareto, getDefectCodes, createDefectCode, deleteDefectCode, deleteDefect } from '@/api/mes'

const defects = ref([])
const total = ref(0)
const currentPage = ref(1)
const pageSize = ref(50)
const filterCategory = ref('')
const filterSeverity = ref('')
const paretoData = ref([])
const maxPareto = computed(() => paretoData.value.length > 0 ? paretoData.value[0].count : 0)

const showCodeMgr = ref(false)
const codes = ref([])
const showAddCode = ref(false)
const codeForm = ref({ code: '', name: '', category: 'other', severity: 'minor', labelsStr: '' })

const selected = ref([])
const handleSelectionChange = (rows) => { selected.value = rows || [] }

const formatTime = (t) => t ? t.replace('T', ' ').substring(0, 19) : '-'

const loadDefects = async () => {
  try {
    const res = await getDefects({
      category: filterCategory.value || undefined,
      severity: filterSeverity.value || undefined,
      skip: (currentPage.value - 1) * pageSize.value,
      limit: pageSize.value,
    })
    defects.value = res.data.items || []
    total.value = res.data.total || 0
  } catch { ElMessage.error('加载缺陷列表失败') }
}

const loadPareto = async () => {
  try {
    const res = await getPareto({})
    paretoData.value = res.data || []
  } catch (e) {
    ElMessage.error('加载 Pareto 数据失败')
  }
}

const loadCodes = async () => {
  try {
    const res = await getDefectCodes({})
    codes.value = res.data || []
  } catch (e) {
    ElMessage.error('加载缺陷代码失败')
  }
}

const handleAddCode = async () => {
  if (!codeForm.value.code || !codeForm.value.name) {
    ElMessage.warning('请填写代码和名称')
    return
  }
  try {
    await createDefectCode({
      ...codeForm.value,
      detection_labels: codeForm.value.labelsStr ? codeForm.value.labelsStr.split(',').map(s => s.trim()).filter(Boolean) : [],
    })
    ElMessage.success('缺陷代码已创建')
    showAddCode.value = false
    codeForm.value = { code: '', name: '', category: 'other', severity: 'minor', labelsStr: '' }
    loadCodes()
  } catch (e) { ElMessage.error(e.response?.data?.detail || '创建失败') }
}

const handleDeleteCode = async (row) => {
  try {
    await ElMessageBox.confirm(`确定删除缺陷代码 ${row.code}？`, '确认')
    await deleteDefectCode(row.id)
    ElMessage.success('已删除')
    loadCodes()
  } catch (e) {
    if (e !== 'cancel' && e !== 'close')
      ElMessage.error('删除失败: ' + (e.response?.data?.detail || e.message || '未知错误'))
  }
}

const handleDeleteDefect = async (row) => {
  try {
    await ElMessageBox.confirm(
      `确定删除缺陷记录 ${row.defect_code || row.defect_name || ''} ？此操作不可撤销。`,
      '删除确认', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }
    )
    await deleteDefect(row.id)
    ElMessage.success('已删除')
    loadDefects()
    loadPareto()
  } catch (e) {
    if (e !== 'cancel' && e !== 'close')
      ElMessage.error('删除失败: ' + (e.response?.data?.detail || e.message || '未知错误'))
  }
}

const handleBatchDeleteDefect = async () => {
  if (selected.value.length === 0) return
  const n = selected.value.length
  try {
    await ElMessageBox.confirm(
      `确定批量删除 ${n} 条缺陷记录？此操作不可撤销。`,
      '批量删除确认', { type: 'warning', confirmButtonText: `删除 ${n} 条`, cancelButtonText: '取消' }
    )
    const ids = selected.value.map(r => r.id)
    const results = await Promise.allSettled(ids.map(id => deleteDefect(id)))
    const failed = results.filter(r => r.status === 'rejected').length
    if (failed === 0) {
      ElMessage.success(`已删除 ${n} 条`)
    } else {
      ElMessage.warning(`成功 ${n - failed} 条, 失败 ${failed} 条`)
    }
    selected.value = []
    loadDefects()
    loadPareto()
  } catch (e) {
    if (e !== 'cancel' && e !== 'close')
      ElMessage.error('批量删除失败: ' + (e.response?.data?.detail || e.message || '未知错误'))
  }
}

watch(showCodeMgr, (v) => { if (v) loadCodes() })

onMounted(() => { loadDefects(); loadPareto() })
</script>

<style scoped>
.mes-table :deep(.el-table__header th) { background: #1e293b !important; color: #94a3b8; }
.mes-table :deep(.el-table__row) { background: #0f172a; }
.mes-table :deep(.el-table__row:hover > td) { background: #1e293b !important; }
.mes-dialog :deep(.el-dialog) { background: #1e293b; border: 1px solid #334155; }
.mes-dialog :deep(.el-dialog__title) { color: #e2e8f0; }
</style>
