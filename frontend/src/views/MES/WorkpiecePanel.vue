<template>
  <div>
    <!-- 搜索栏 -->
    <div class="flex items-center gap-3 mb-4 flex-wrap">
      <el-input v-model="keyword" placeholder="搜索序列号/条码..." size="small" class="w-52" clearable @clear="loadList" @keyup.enter="loadList" />
      <el-select v-model="filterStatus" placeholder="状态" size="small" class="w-28" clearable @change="loadList">
        <el-option label="已登记" value="registered" />
        <el-option label="排队中" value="queued" />
        <el-option label="检测中" value="inspecting" />
        <el-option label="合格" value="ok" />
        <el-option label="不良" value="ng" />
        <el-option label="返工中" value="rework" />
        <el-option label="已报废" value="scrapped" />
      </el-select>
      <el-date-picker
        v-model="dateRange"
        type="daterange"
        range-separator="至"
        start-placeholder="开始日期"
        end-placeholder="结束日期"
        size="small"
        class="!w-64"
        value-format="YYYY-MM-DD"
        @change="loadList"
        clearable
      />
      <el-button size="small" type="primary" @click="loadList">查询</el-button>
      <el-button size="small" type="danger" plain :disabled="selected.length === 0"
                 @click="handleBatchDelete">
        批量删除{{ selected.length > 0 ? `(${selected.length})` : '' }}
      </el-button>
      <div class="flex items-center gap-1 ml-auto">
        <el-switch v-model="onlyCurrentProject" size="small" @change="handleProjectScopeChange" />
        <span class="text-xs text-gray-400">仅当前项目<template v-if="onlyCurrentProject && projectStore.currentProjectName">（{{ projectStore.currentProjectName }}）</template></span>
      </div>
    </div>

    <div class="flex gap-4 h-[calc(100vh-240px)]">
      <!-- 左: 工件列表 -->
      <div class="flex-1 overflow-auto">
        <el-table :data="items" stripe size="small" class="mes-table" highlight-current-row
                  @current-change="handleSelect" @selection-change="handleSelectionChange">
          <el-table-column type="selection" width="42" />
          <el-table-column prop="serial_no" label="序列号" width="160" />
          <el-table-column prop="status" label="状态" width="90">
            <template #default="{ row }">
              <el-tag :type="wpStatusType(row.status)" size="small">{{ wpStatusLabel(row.status) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="inspection_count" label="检测次数" width="90" />
          <el-table-column prop="scan_source" label="来源" width="80" />
          <el-table-column prop="registered_at" label="登记时间" width="160">
            <template #default="{ row }">{{ formatTime(row.registered_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="220">
            <template #default="{ row }">
              <el-button v-if="row.status === 'ng'" size="small" type="warning" @click="doAction(row, 'rework')">返工</el-button>
              <el-button v-if="['ng','rework'].includes(row.status)" size="small" type="danger" @click="doAction(row, 'scrap')">报废</el-button>
              <el-button size="small" type="danger" plain @click="handleDelete(row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>

        <div class="flex justify-end mt-3">
          <el-pagination
            v-model:current-page="currentPage" v-model:page-size="pageSize"
            :total="total" :page-sizes="[20,50,100]"
            layout="total, sizes, prev, pager, next"
            small @size-change="loadList" @current-change="loadList"
          />
        </div>
      </div>

      <!-- 右: 追溯详情 -->
      <div class="w-[420px] bg-slate-800/50 rounded-lg border border-slate-700 p-4 overflow-auto" v-if="traceData">
        <h3 class="text-cyan-300 text-base font-semibold mb-3">追溯详情</h3>
        <div class="space-y-2 text-sm mb-4">
          <div><span class="text-gray-400">序列号：</span>{{ traceData.workpiece.serial_no }}</div>
          <div><span class="text-gray-400">状态：</span>
            <el-tag :type="wpStatusType(traceData.workpiece.status)" size="small">{{ wpStatusLabel(traceData.workpiece.status) }}</el-tag>
          </div>
          <div><span class="text-gray-400">检测次数：</span>{{ traceData.workpiece.inspection_count }}</div>
          <div v-if="traceData.workpiece.registered_at"><span class="text-gray-400">登记时间：</span>{{ formatTime(traceData.workpiece.registered_at) }}</div>
        </div>

        <!-- v3.56 多码采集组件码 (确认单 7.5: 输入工件码反查当时绑的所有组件码)。
             工装码是循环治具码会跨工件重复 → 同一序列号名下可能有多轮码组,
             按 group_id 分节显示 (新在前), 不同轮次的码不混排 -->
        <template v-if="scanGroups.length">
          <h4 class="text-cyan-200 text-sm font-semibold mb-2">组件码（多码采集）</h4>
          <div class="space-y-3 mb-4" data-testid="wp-scan-codes">
            <div v-for="g in scanGroups" :key="g.group_id">
              <div v-if="scanGroups.length > 1" class="text-[0.7rem] text-gray-500 mb-1">
                {{ formatTime(g.settled_at) }}
                <el-tag :type="g.result === 'ok' ? 'success' : 'danger'" size="small" class="ml-1">
                  {{ g.result === 'ok' ? 'OK' : 'NG' }}</el-tag>
              </div>
              <div class="space-y-1">
                <div v-for="r in g.records" :key="r.id"
                     class="bg-slate-700/50 rounded px-2 py-1 text-xs flex items-center gap-2">
                  <span class="text-gray-400 flex-shrink-0 w-14">{{ r.slot_label }}</span>
                  <span class="font-mono text-gray-200 truncate min-w-0" :title="r.code">{{ r.code }}</span>
                  <span class="ml-auto text-gray-500 flex-shrink-0">{{ formatTime(r.scanned_at) }}</span>
                </div>
              </div>
            </div>
          </div>
        </template>

        <!-- 检测历史 -->
        <h4 class="text-cyan-200 text-sm font-semibold mb-2">检测历史</h4>
        <div v-if="traceData.inspections.length === 0" class="text-gray-500 text-xs mb-4">暂无检测记录</div>
        <div v-else class="space-y-2 mb-4">
          <div v-for="insp in traceData.inspections" :key="insp.id"
               class="bg-slate-700/50 rounded p-2 text-xs">
            <div class="flex justify-between">
              <span>第{{ insp.inspection_seq }}次检测</span>
              <el-tag :type="insp.result === 'ok' ? 'success' : insp.result === 'ng' ? 'danger' : 'info'" size="small">{{ insp.result }}</el-tag>
            </div>
            <div v-if="insp.event_name" class="text-gray-400 mt-1">事件: {{ insp.event_name }}</div>
            <div v-if="insp.duration" class="text-gray-400">耗时: {{ insp.duration.toFixed(2) }}s</div>
            <div class="text-gray-500">{{ formatTime(insp.created_at) }}</div>
          </div>
        </div>

        <!-- 缺陷记录 -->
        <h4 class="text-cyan-200 text-sm font-semibold mb-2">缺陷记录</h4>
        <div v-if="traceData.defects.length === 0" class="text-gray-500 text-xs">无缺陷</div>
        <div v-else class="space-y-2">
          <div v-for="d in traceData.defects" :key="d.id"
               class="bg-red-900/20 border border-red-800/30 rounded p-2 text-xs">
            <div class="flex justify-between">
              <span class="text-red-300 font-medium">{{ d.defect_name }}</span>
              <el-tag type="danger" size="small">{{ d.severity }}</el-tag>
            </div>
            <div class="text-gray-400 mt-1">代码: {{ d.defect_code }} / 分类: {{ d.defect_category }}</div>
            <div v-if="d.description" class="text-gray-500 mt-1">{{ d.description }}</div>
          </div>
        </div>
      </div>
      <div v-else class="w-[420px] bg-slate-800/30 rounded-lg border border-slate-700 flex items-center justify-center text-gray-500 text-sm">
        选择工件查看追溯详情
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getWorkpieces, getWorkpieceTrace, workpieceAction, deleteWorkpiece } from '@/api/mes'
import { getScanCollectRecords } from '@/api/scanCollect'
import { getWorkstations } from '@/api/detection'
import { useProjectStore } from '@/store/useProjectStore'
import { dbg, dbgErr } from '@/utils/debug'

const projectStore = useProjectStore()
// v3.51.5: 单工位默认只看当前项目; 多工位下"当前项目"只是最后激活的那个,
// 各工位项目不同, 默认过滤会藏起其它工位项目的工件 → 操作员"全选批量删除"
// 以为删干净, 其它项目的 ok 工件残留 → strict_ok_dedup 拒码但人找不到原因
// (2026-08-15 捷昌 B 站现场实录: "记录全删了为什么还去重")。
const onlyCurrentProject = ref(true)
const items = ref([])
const total = ref(0)
const currentPage = ref(1)
const pageSize = ref(50)
const keyword = ref('')
const filterStatus = ref('')
const dateRange = ref(null)
const traceData = ref(null)
const scanCodes = ref([])  // v3.56 多码采集组件码 (无记录=空数组不渲染区块)
const selected = ref([])

// 按码组分节 (records 已按 seq 升序; 组间按结算时间倒序 = 最近一轮在前)
const scanGroups = computed(() => {
  const map = new Map()
  for (const r of scanCodes.value) {
    if (!map.has(r.group_id)) {
      map.set(r.group_id, { group_id: r.group_id, settled_at: r.settled_at,
                            result: r.group_result, records: [] })
    }
    map.get(r.group_id).records.push(r)
  }
  return [...map.values()].sort((a, b) => (b.settled_at || '').localeCompare(a.settled_at || ''))
})

const handleSelectionChange = (rows) => { selected.value = rows || [] }

const wpStatusLabel = (s) => ({ registered: '已登记', queued: '排队', inspecting: '检测中', ok: '合格', ng: '不良', rework: '返工中', scrapped: '已报废' }[s] || s)
const wpStatusType = (s) => ({
  ok: 'success',
  ng: 'danger',
  inspecting: 'warning',
  rework: 'warning',
  scrapped: 'info',
  registered: 'info',
  queued: 'info',
}[s] || 'info')
const formatTime = (t) => t ? t.replace('T', ' ').substring(0, 19) : '-'

const loadList = async () => {
  try {
    const params = {
      keyword: keyword.value || undefined,
      status: filterStatus.value || undefined,
      skip: (currentPage.value - 1) * pageSize.value,
      limit: pageSize.value,
    }
    if (dateRange.value && dateRange.value.length === 2) {
      params.date_from = dateRange.value[0]
      params.date_to = dateRange.value[1] + ' 23:59:59'
    }
    if (onlyCurrentProject.value && projectStore.currentProjectId) {
      params.project_id = projectStore.currentProjectId
    }
    const res = await getWorkpieces(params)
    items.value = res.data.items || []
    total.value = res.data.total || 0
  } catch (e) {
    dbgErr('mes.workpiece', '查询工件列表', e)
    ElMessage.error('加载工件列表失败')
  }
}

const handleSelect = async (row) => {
  if (!row) { traceData.value = null; scanCodes.value = []; return }
  dbg('mes.workpiece', '点击「工件详情/追溯」', `id=${row?.id} serial_no=${row?.serial_no || ''}`)
  try {
    const res = await getWorkpieceTrace(row.id)
    traceData.value = res.data
  } catch (e) {
    dbgErr('mes.workpiece', '加载追溯详情', e)
    ElMessage.error('加载追溯详情失败')
  }
  // 组件码独立拉取, 失败不连坐追溯详情 (老工件/未启用多码采集 = 空)
  try {
    const res = await getScanCollectRecords({ workpiece_id: row.id })
    scanCodes.value = res.data || []
  } catch {
    scanCodes.value = []
  }
}

const doAction = async (row, action) => {
  const labels = { rework: '标记返工', scrap: '标记报废' }
  dbg('mes.workpiece', `点击「${labels[action] || action}」`, `id=${row?.id} serial_no=${row?.serial_no || ''}`)
  try {
    await ElMessageBox.confirm(`确定对 ${row.serial_no} 执行「${labels[action]}」？`, '确认')
    await workpieceAction(row.id, { action })
    ElMessage.success('操作成功')
    loadList()
    if (traceData.value?.workpiece?.id === row.id) handleSelect(row)
  } catch (e) {
    if (e !== 'cancel' && e !== 'close') dbgErr('mes.workpiece', '手动判定操作', e)
    if (e !== 'cancel' && e !== 'close')
      ElMessage.error('操作失败: ' + (e.response?.data?.detail || e.message || '未知错误'))
  }
}

const handleDelete = async (row) => {
  dbg('mes.workpiece', '点击「删除工件」', `id=${row?.id} serial_no=${row?.serial_no || ''}`)
  try {
    await ElMessageBox.confirm(
      `确定删除工件 ${row.serial_no} ？\n\n会同时清掉它的全部检测关联和缺陷记录, 不可撤销。`,
      '删除确认', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }
    )
    await deleteWorkpiece(row.id)
    ElMessage.success('已删除')
    if (traceData.value?.workpiece?.id === row.id) traceData.value = null
    loadList()
  } catch (e) {
    if (e !== 'cancel' && e !== 'close') dbgErr('mes.workpiece', '删除工件', e)
    if (e !== 'cancel' && e !== 'close')
      ElMessage.error('删除失败: ' + (e.response?.data?.detail || e.message || '未知错误'))
  }
}

const handleBatchDelete = async () => {
  if (selected.value.length === 0) return
  const n = selected.value.length
  dbg('mes.workpiece', '点击「批量删除工件」', `count=${n}`)
  try {
    await ElMessageBox.confirm(
      `确定批量删除 ${n} 个工件？\n\n会同时清掉这些工件的全部检测关联和缺陷记录, 不可撤销。`,
      '批量删除确认', { type: 'warning', confirmButtonText: `删除 ${n} 个`, cancelButtonText: '取消' }
    )
    const ids = selected.value.map(r => r.id)
    const results = await Promise.allSettled(ids.map(id => deleteWorkpiece(id)))
    const failed = results.filter(r => r.status === 'rejected').length
    if (failed === 0) {
      ElMessage.success(`已删除 ${n} 个工件`)
    } else {
      ElMessage.warning(`成功 ${n - failed} 个, 失败 ${failed} 个`)
    }
    if (traceData.value && selected.value.some(r => r.id === traceData.value.workpiece?.id)) {
      traceData.value = null
    }
    selected.value = []
    loadList()
  } catch (e) {
    if (e !== 'cancel' && e !== 'close') dbgErr('mes.workpiece', '批量删除工件', e)
    if (e !== 'cancel' && e !== 'close')
      ElMessage.error('批量删除失败: ' + (e.response?.data?.detail || e.message || '未知错误'))
  }
}

const handleProjectScopeChange = () => { currentPage.value = 1; loadList() }
watch(() => projectStore.currentProjectId, () => {
  if (onlyCurrentProject.value) { currentPage.value = 1; loadList() }
})

onMounted(async () => {
  try {
    const ws = await getWorkstations()
    const n = ws?.data?.channel_count || 1
    if (n > 1) {
      onlyCurrentProject.value = false
      dbg('mes.workpiece', '多工位默认关闭「仅当前项目」过滤',
          `channel_count=${n} — 显示全部项目工件, 避免批量删除漏删其它工位项目的 ok 工件`)
    } else {
      dbg('mes.workpiece', '单工位保持「仅当前项目」默认开', `channel_count=${n}`)
    }
  } catch (e) {
    dbgErr('mes.workpiece', '查询工位数失败, 保持单工位默认过滤', e)
  }
  loadList()
})
</script>

<style scoped>
.mes-table :deep(.el-table__header th) { background: #1e293b !important; color: #94a3b8; }
.mes-table :deep(.el-table__row) { background: #0f172a; }
.mes-table :deep(.el-table__row:hover > td) { background: #1e293b !important; }
</style>
