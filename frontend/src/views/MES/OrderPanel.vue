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
        <div class="flex items-center gap-1">
          <el-switch v-model="onlyCurrentProject" size="small" @change="handleProjectScopeChange" />
          <span class="text-xs text-gray-400">仅当前项目<template v-if="onlyCurrentProject && projectStore.currentProjectName">（{{ projectStore.currentProjectName }}）</template></span>
        </div>
      </div>
      <div class="flex items-center gap-2">
        <el-switch v-model="autoRefresh" size="small" active-text="自动刷新" @change="toggleAutoRefresh" />
        <el-select
          v-model="activeTemplateId"
          size="small"
          class="w-40"
          placeholder="选择模板"
          @change="handleTemplateSwitch"
        >
          <el-option
            v-for="tpl in templateLibrary"
            :key="tpl.id"
            :label="tpl.name"
            :value="tpl.id"
          />
        </el-select>
        <el-button size="small" @click="openTemplate">字段配置</el-button>
        <el-button size="small" type="success" @click="openCreate">新建工单</el-button>
      </div>
    </div>

    <!-- 工单列表 -->
    <!-- v2.7.15: 表头按模板顺序 + visible 过滤渲染. 每列内容按 key 走专属模板 -->
    <el-table :data="orders" stripe size="small" class="mes-table" max-height="calc(100vh - 240px)">
      <el-table-column
        v-for="key in visibleColOrder" :key="key"
        :prop="colMetaFor(key).prop"
        :label="colLabel(key)"
        :width="colMetaFor(key).width"
        :min-width="colMetaFor(key).minWidth"
        :fixed="colMetaFor(key).fixed"
      >
        <template #default="{ row }">
          <!-- order_no/product_name/product_code 用默认 prop 渲染 -->
          <template v-if="key === 'order_no'">{{ row.order_no }}</template>
          <template v-else-if="key === 'product_name'">{{ row.product_name }}</template>
          <template v-else-if="key === 'product_code'">{{ row.product_code }}</template>
          <template v-else-if="key === 'source'">
            <el-tag :type="row.source === 'external' ? 'warning' : 'info'" size="small">
              {{ row.source === 'external' ? '外部' : '手动' }}
            </el-tag>
          </template>
          <template v-else-if="key === 'binding'">
            <!-- v3.1.0: 显示绑定方式 + 详情 + 旧工单警告 -->
            <div class="flex flex-col gap-0.5">
              <el-tag :type="bindingTagType(row)" size="small">{{ bindingLabel(row) }}</el-tag>
              <span class="text-xs text-gray-400 truncate" :title="bindingDetail(row)">
                {{ bindingDetail(row) }}
              </span>
            </div>
          </template>
          <template v-else-if="key === 'status'">
            <el-tag :type="statusType(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag>
          </template>
          <template v-else-if="key === 'progress'">
            <div class="flex items-center gap-2">
              <el-progress
                :percentage="row.planned_qty > 0 ? Math.min(100, Math.round(row.completed_qty / row.planned_qty * 100)) : 0"
                :stroke-width="12" :text-inside="true" style="flex:1"
              />
              <span class="text-xs text-gray-400 whitespace-nowrap">{{ row.completed_qty }}/{{ row.planned_qty }}</span>
            </div>
          </template>
          <template v-else-if="key === 'good_ng'">
            <span class="text-green-400">{{ row.good_qty }}</span> /
            <span class="text-red-400">{{ row.ng_qty }}</span>
          </template>
          <template v-else-if="key === 'yield_rate'">
            <span :class="row.yield_rate >= 95 ? 'text-green-400' : row.yield_rate >= 80 ? 'text-yellow-400' : 'text-red-400'">
              {{ row.yield_rate != null ? row.yield_rate + '%' : '-' }}
            </span>
          </template>
          <template v-else-if="key === 'created_at'">{{ formatTime(row.created_at) }}</template>
          <template v-else-if="key === 'actions'">
            <div class="flex gap-1 flex-wrap">
              <el-button v-if="row.status === 'draft'" size="small" type="warning" @click="changeStatus(row, 'pending')">提交</el-button>
              <el-button v-if="row.status === 'pending'" size="small" type="success" @click="changeStatus(row, 'in_progress')">开始</el-button>
              <el-button v-if="row.status === 'in_progress'" size="small" type="warning" @click="changeStatus(row, 'paused')">暂停</el-button>
              <el-button v-if="row.status === 'paused'" size="small" type="success" @click="changeStatus(row, 'in_progress')">恢复</el-button>
              <el-button v-if="row.status === 'in_progress'" size="small" type="primary" @click="changeStatus(row, 'completed')">完成</el-button>
              <el-button size="small" @click="editOrder(row)">编辑</el-button>
              <el-button v-if="hasExtraData(row)" size="small" type="info" @click="showExtraData(row)">附加</el-button>
              <el-button size="small" type="danger" @click="handleDelete(row)">删除</el-button>
            </div>
          </template>
          <!-- 自定义字段 / 未来新增 preset: 从 extra_data 或 row 自身取值 -->
          <template v-else>{{ (row.extra_data && row.extra_data[key]) ?? row[key] ?? '' }}</template>
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

    <!-- 新建/编辑 对话框（模板驱动） -->
    <el-dialog v-model="showCreate" :title="editingId ? '编辑工单' : '新建工单'" width="640px" class="mes-dialog" destroy-on-close>
      <el-form label-width="110px" size="small">
        <!-- v3.1.0: 绑定方式 (不进模板, 三选一必填) -->
        <el-form-item label="绑定方式" required>
          <el-radio-group v-model="bindingForm.scope" size="small">
            <el-radio-button value="project">按项目</el-radio-button>
            <el-radio-button value="channels">按工位</el-radio-button>
            <el-radio-button value="cluster">按集群站点</el-radio-button>
          </el-radio-group>
          <div class="text-xs text-gray-400 mt-1 leading-tight">
            <span v-if="bindingForm.scope === 'project'">本机任意工位上, 该项目下的 cycle 完成都计入此工单 (按 cycle +1)</span>
            <span v-else-if="bindingForm.scope === 'channels'">只有指定工位上的 cycle 完成才计入此工单 (按 cycle +1, 不限项目)</span>
            <span v-else-if="bindingForm.scope === 'cluster'">集群里指定站点参与时, 一个 box_serial 完成算一件 (按 box +1)</span>
          </div>
        </el-form-item>
        <el-form-item v-if="bindingForm.scope === 'project'" label="所属项目" required>
          <el-select v-model="bindingForm.project_id" placeholder="选择项目" clearable filterable size="small" style="width: 100%">
            <el-option v-for="p in projectOptions" :key="p.id" :value="p.id" :label="`${p.name} (#${p.id})`" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="bindingForm.scope === 'channels'" label="目标工位" required>
          <el-select v-model="bindingForm.target_channels" multiple placeholder="选择工位号 (例如 0, 1)" size="small" style="width: 100%">
            <el-option v-for="n in channelOptions" :key="n" :value="n" :label="`工位 ${n}`" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="bindingForm.scope === 'cluster'" label="集群计件">
          <div class="text-xs text-gray-300 leading-relaxed bg-slate-700/30 rounded px-3 py-2 w-full">
            <div>工单建在主机即可, 集群里所有完成的 box (主机推送 box_complete 时) 自动 +1.</div>
            <div class="text-gray-400 mt-1">一个 box_serial = 1 件. 同一时间只会有一条活跃集群工单 (按优先级取).</div>
          </div>
        </el-form-item>

        <el-divider class="!my-3" />

        <el-empty v-if="formItems.length === 0" description="还未配置任何字段，点『字段配置』添加" :image-size="60" />
        <el-form-item
          v-for="item in formItems" :key="item.key"
          :label="item.label" :required="item.required"
        >
          <DynamicFieldInput
            v-if="item.preset"
            :item="item"
            :model-value="presetValues[item.key]"
            @update:model-value="v => presetValues[item.key] = v"
          />
          <DynamicFieldInput
            v-else
            :item="item"
            :model-value="customValues[item.key]"
            @update:model-value="v => customValues[item.key] = v"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button size="small" @click="showCreate = false">取消</el-button>
        <el-button size="small" type="primary" @click="handleSave" :loading="saving">保存</el-button>
      </template>
    </el-dialog>

    <!-- 字段配置（模板编辑器） -->
    <el-dialog v-model="showTemplate" title="工单表单字段配置" width="720px" class="mes-dialog" destroy-on-close>
      <div class="text-xs text-gray-400 mb-3">
        拖动顺序请用上下按钮；系统预设字段可改名或删除（删除后新建工单时：工单号自动生成 <code>ORD-时间戳</code>，产品名称默认『未命名』），其余字段保存到工单的附加字段中。
      </div>
      <div class="flex items-center gap-2 mb-3">
        <el-select
          v-model="activeTemplateId"
          size="small"
          class="w-40"
          placeholder="选择模板"
          @change="handleTemplateSwitch"
        >
          <el-option
            v-for="tpl in templateLibrary"
            :key="tpl.id"
            :label="tpl.name"
            :value="tpl.id"
          />
        </el-select>
        <el-button size="small" @click="createTemplate">新建模板</el-button>
        <el-button size="small" @click="duplicateTemplate">另存为</el-button>
        <el-button size="small" @click="renameTemplate">重命名</el-button>
        <el-button size="small" type="danger" plain @click="deleteTemplate">删除模板</el-button>
      </div>

      <el-table :data="tplDraft" size="small" border class="tpl-table">
        <el-table-column label="#" width="50">
          <template #default="{ $index }">{{ $index + 1 }}</template>
        </el-table-column>
        <el-table-column label="字段Key" width="160">
          <template #default="{ row }">
            <span v-if="row.system" class="text-gray-400 text-xs">{{ row.key }} <el-tag size="small" type="warning">系统列</el-tag></span>
            <span v-else-if="row.preset" class="text-gray-400 text-xs">{{ row.key }} <el-tag size="small" type="info">预设</el-tag></span>
            <el-input v-else v-model="row.key" size="small" placeholder="customField1" />
          </template>
        </el-table-column>
        <el-table-column label="显示名">
          <template #default="{ row }">
            <el-input v-model="row.label" size="small" :placeholder="row.system ? '工单列表表头显示名' : '请填写中文名(如 工单号)'" />
          </template>
        </el-table-column>
        <el-table-column label="类型" width="120">
          <template #default="{ row }">
            <span v-if="row.system" class="text-gray-400 text-xs" :title="`${SYSTEM_COL_META[row.key]?.typeLabel || '列表列'}（不可改）`">
              {{ SYSTEM_COL_META[row.key]?.typeLabel || '列表列' }}
              <span class="text-gray-600">（不可改）</span>
            </span>
            <span v-else-if="row.preset" class="text-gray-400 text-xs">{{ fieldTypeLabel(row.type) }}</span>
            <el-select v-else v-model="row.type" size="small">
              <el-option v-for="o in fieldTypeOptions" :key="o.value" :value="o.value" :label="o.label" />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column label="必填" width="70">
          <template #default="{ row }">
            <el-switch v-if="!row.system" v-model="row.required" size="small" />
            <span v-else class="text-gray-500 text-xs">-</span>
          </template>
        </el-table-column>
        <el-table-column label="显示" width="60">
          <template #default="{ row }">
            <el-switch v-if="canHide(row)" v-model="row.visible" size="small" />
            <span v-else class="text-gray-500 text-xs">-</span>
          </template>
        </el-table-column>
        <el-table-column label="选项" min-width="160">
          <template #default="{ row }">
            <el-input
              v-if="!row.system && row.type === 'select'"
              v-model="row.optionsText" size="small" placeholder="逗号分隔：选项A,选项B,选项C"
            />
            <span v-else class="text-gray-500 text-xs">-</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="130">
          <template #default="{ row, $index }">
            <el-button size="small" circle @click="moveUp($index)" :disabled="$index === 0">↑</el-button>
            <el-button size="small" circle @click="moveDown($index)" :disabled="$index === tplDraft.length - 1">↓</el-button>
            <el-button size="small" type="danger" circle @click="removeItem($index)" :disabled="row.system">×</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="flex items-center gap-2 mt-3">
        <el-button size="small" type="primary" plain @click="addCustomField">+ 添加自定义字段</el-button>
        <el-dropdown @command="addMissingPreset" v-if="missingPresets.length > 0">
          <el-button size="small" plain>+ 恢复系统预设字段</el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item v-for="k in missingPresets" :key="k" :command="k">
                {{ PRESET_META[k].label }}（{{ k }}）
              </el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
        <el-button size="small" @click="resetTemplate">恢复默认</el-button>
      </div>

      <template #footer>
        <el-button size="small" @click="showTemplate = false">取消</el-button>
        <el-button size="small" type="primary" @click="saveTemplate">保存模板</el-button>
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
import { ref, computed, onMounted, onBeforeUnmount, watch, h } from 'vue'
import {
  ElMessage, ElMessageBox,
  ElInput, ElInputNumber, ElDatePicker, ElTimePicker,
  ElSwitch, ElSelect, ElOption,
} from 'element-plus'
import { Close } from '@element-plus/icons-vue'
import { getOrders, createOrder, updateOrder, changeOrderStatus, deleteOrder, updateOrderExtraData } from '@/api/mes'
import { getProjects } from '@/api/project'
import { getClusterConfig } from '@/api/cluster'
import { useProjectStore } from '@/store/useProjectStore'
import { dbg, dbgErr } from '@/utils/debug'

const projectStore = useProjectStore()
const onlyCurrentProject = ref(true)

// ========== 模板常量 ==========
const TEMPLATE_KEY = 'mes_order_form_template_v1'
const TEMPLATE_LIBRARY_KEY = 'mes_order_template_library_v1'
const TEMPLATE_ACTIVE_ID_KEY = 'mes_order_template_active_id_v1'
const ORDER_ARCHIVE_KEY = 'mes_order_archived_ids_v1'
// v2.7.15: 一次性迁移标记 — 旧版可能把 visible 写成 false 污染了 localStorage
// 第一次加载时强制把所有列的 visible 重置成 true, 再写标记防止再触发
const TEMPLATE_VISIBLE_MIGRATED_KEY = 'mes_order_form_template_visible_migrated_v1'

// 预设字段元信息：前端保存时会按 key 映射到后端字段名
const PRESET_META = {
  order_no:     { label: '工单号',   type: 'text',     required: true,  placeholder: '留空自动生成 ORD-时间戳' },
  product_name: { label: '产品名称', type: 'text',     required: true,  placeholder: '留空默认『未命名』' },
  product_code: { label: '产品编码', type: 'text',     required: false },
  product_spec: { label: '规格',     type: 'text',     required: false },
  planned_qty:  { label: '计划数量', type: 'number',   required: false, min: 0 },
  priority:     { label: '优先级',   type: 'select',   required: false, optionsText: '紧急=1,高=2,正常=3,低=4' },
  remark:       { label: '备注',     type: 'textarea', required: false },
}
const PRESET_ORDER = ['order_no', 'product_name', 'product_code', 'product_spec', 'planned_qty', 'priority', 'remark']
const PRESET_KEYS = new Set(PRESET_ORDER)

// v2.7.15: 工单列表的"系统列"也通过模板配置"显示名", 但不会出现在新建工单表单里
// type='system' 标记: v-for 表单循环跳过, 字段配置只允许改 label, 不可改 key/type/required/options
const SYSTEM_COL_META = {
  source:     { label: '来源',      typeLabel: '标签'   },
  binding:    { label: '绑定',      typeLabel: '绑定标签' },   // v3.1.0 新增
  status:     { label: '状态',      typeLabel: '状态标签' },
  progress:   { label: '进度',      typeLabel: '进度条'  },
  good_ng:    { label: '良品/不良', typeLabel: '良品/不良数' },
  yield_rate: { label: '良率',      typeLabel: '百分比'  },
  created_at: { label: '创建时间',  typeLabel: '时间'   },
  actions:    { label: '操作',      typeLabel: '按钮组'  },
}
const SYSTEM_COL_ORDER = Object.keys(SYSTEM_COL_META)
const SYSTEM_COL_KEYS = new Set(SYSTEM_COL_ORDER)

// v2.7.15: 工单列表所有可见列的渲染元数据 — prop / width / fixed
// 表头改成 v-for 渲染, 顺序跟模板, 隐藏跟 visible 字段
const COL_META = {
  order_no:     { prop: 'order_no',     width: 140 },
  product_name: { prop: 'product_name', width: 120 },
  product_code: { prop: 'product_code', width: 100 },
  source:       { width: 80 },
  binding:      { width: 180 },   // v3.1.0
  status:       { prop: 'status',       width: 100 },
  progress:     { width: 180 },
  good_ng:      { width: 120 },
  yield_rate:   { prop: 'yield_rate',   width: 80 },
  created_at:   { prop: 'created_at',   width: 160 },
  actions:      { minWidth: 260, fixed: 'right' },
}

const fieldTypeOptions = [
  { value: 'text', label: '文本' },
  { value: 'textarea', label: '多行文本' },
  { value: 'number', label: '数字' },
  { value: 'date', label: '日期' },
  { value: 'time', label: '时间' },
  { value: 'datetime', label: '日期时间' },
  { value: 'select', label: '下拉选项' },
  { value: 'switch', label: '开关' },
]
const fieldTypeLabel = (t) => (fieldTypeOptions.find(o => o.value === t) || {}).label || t

// 默认模板: 表单字段(PRESET) + 列表系统列(SYSTEM)
// 表单字段进入新建工单弹窗; 系统列只用来配置工单列表表头别名
const buildDefaultTemplate = () => [
  ...PRESET_ORDER.map(k => ({
    key: k,
    label: PRESET_META[k].label,
    type: PRESET_META[k].type,
    required: PRESET_META[k].required,
    preset: true,
    visible: true,
    optionsText: PRESET_META[k].optionsText || '',
  })),
  ...SYSTEM_COL_ORDER.map(k => ({
    key: k,
    label: SYSTEM_COL_META[k].label,
    type: 'system',
    required: false,
    preset: false,
    system: true,
    visible: true,
    optionsText: '',
  })),
]

// 表头列名解析: 优先用模板里的 label, 找不到回落到 SYSTEM/PRESET 默认中文名
const colLabel = (key) => {
  const item = template.value.find(t => t.key === key)
  if (item && item.label) return item.label
  return SYSTEM_COL_META[key]?.label || PRESET_META[key]?.label || key
}

// v2.7.15: 工单列表表头按模板顺序 + visible 过滤
// 全部模板字段都可进表格 (包括自定义字段, 从 extra_data 取值)
// 用户全关 → 表格变空, 这是用户意图. 重新打开请走右上角"字段配置"
const visibleColOrder = computed(() =>
  template.value
    .filter(it => it.visible !== false)
    .map(it => it.key)
)

// ========== DynamicFieldInput：按类型渲染不同控件 ==========
const DynamicFieldInput = {
  name: 'DynamicFieldInput',
  props: {
    item: { type: Object, required: true },
    modelValue: { default: undefined },
  },
  emits: ['update:modelValue'],
  setup(props, { emit }) {
    const onInput = (v) => emit('update:modelValue', v)
    return () => {
      const { item, modelValue } = props
      const common = { size: 'small', style: 'width:100%' }
      const ph = PRESET_META[item.key]?.placeholder
      if (item.type === 'textarea') {
        return h(ElInput, { ...common, type: 'textarea', rows: 2, modelValue, 'onUpdate:modelValue': onInput, placeholder: ph })
      }
      if (item.type === 'number') {
        const isInt = item.key === 'planned_qty'
        return h(ElInputNumber, {
          ...common,
          modelValue,
          'onUpdate:modelValue': onInput,
          min: PRESET_META[item.key]?.min ?? undefined,
          precision: isInt ? 0 : 2,
          step: isInt ? 1 : 0.01,
          controlsPosition: 'right',
        })
      }
      if (item.type === 'date') {
        return h(ElDatePicker, { ...common, type: 'date', valueFormat: 'YYYY-MM-DD', modelValue, 'onUpdate:modelValue': onInput })
      }
      if (item.type === 'time') {
        return h(ElTimePicker, { ...common, valueFormat: 'HH:mm:ss', modelValue, 'onUpdate:modelValue': onInput })
      }
      if (item.type === 'datetime') {
        return h(ElDatePicker, { ...common, type: 'datetime', valueFormat: 'YYYY-MM-DD HH:mm:ss', modelValue, 'onUpdate:modelValue': onInput })
      }
      if (item.type === 'switch') {
        return h(ElSwitch, { size: 'small', modelValue: !!modelValue, 'onUpdate:modelValue': onInput })
      }
      if (item.type === 'select') {
        const opts = parseOptions(item.optionsText)
        return h(ElSelect, { ...common, modelValue, 'onUpdate:modelValue': onInput, clearable: true },
          () => opts.map(o => h(ElOption, { key: String(o.value), value: o.value, label: o.label })))
      }
      return h(ElInput, { ...common, modelValue, 'onUpdate:modelValue': onInput, placeholder: ph })
    }
  }
}

// "紧急=1,高=2,正常=3" → [{label:紧急,value:1}, ...]
// "选项A,选项B" → [{label:选项A,value:选项A}, ...]
function parseOptions(text) {
  if (!text) return []
  return text.split(',').map(s => s.trim()).filter(Boolean).map(seg => {
    const eq = seg.indexOf('=')
    if (eq > 0) {
      const label = seg.slice(0, eq).trim()
      const raw = seg.slice(eq + 1).trim()
      const num = Number(raw)
      return { label, value: Number.isNaN(num) || raw === '' ? raw : num }
    }
    return { label: seg, value: seg }
  })
}

// ========== 列表/分页/过滤 ==========
const orders = ref([])
const total = ref(0)
const archivedOrderIds = ref(new Set())
const currentPage = ref(1)
const pageSize = ref(50)
const keyword = ref('')
const filterStatus = ref('')
const showCreate = ref(false)
const editingId = ref(null)
const saving = ref(false)
const autoRefresh = ref(false)
let refreshTimer = null

// v3.1.0: 工单绑定方式 — project / channels / cluster, 不进模板, 单独维护
const defaultBindingForm = () => ({
  scope: 'project',
  project_id: null,
  target_channels: [],
  target_stations: [],
})
const bindingForm = ref(defaultBindingForm())
const projectOptions = ref([])     // [{id, name, channel_count}]
const stationOptions = ref([])     // ["A","B"...] 集群 expected_stations
const channelOptions = computed(() => {
  // 工位号候选: 项目选了的话用项目 channel_count, 否则给 0..3 让用户手填
  const proj = projectOptions.value.find(p => p.id === bindingForm.value.project_id)
  const cnt = (proj && proj.channel_count) || 4
  return Array.from({ length: Math.max(cnt, 4) }, (_, i) => i)
})

// ========== 模板状态 ==========
const template = ref(buildDefaultTemplate())
const templateLibrary = ref([])
const activeTemplateId = ref('')
const presetValues = ref({})   // key: order_no / product_name / ...
const customValues = ref({})   // 自定义字段 → 最后进 extra_data
const _editingExtraOriginal = ref({})  // 编辑时原 extra_data 副本，用于保留模板之外的字段

const showTemplate = ref(false)
const tplDraft = ref([])

// v2.7.15: 新建/编辑工单表单只渲染表单字段, 跳过列表系统列(type=system)
const formItems = computed(() => template.value.filter(it => it.type !== 'system'))

// v2.7.15: 所有字段都可显/隐
// system/preset 走 COL_META 的专属渲染, 自定义字段从 extra_data[key] 取值
const canHide = () => true

// 自定义/preset 字段表格列默认宽度 (COL_META 没定义时用)
const colMetaFor = (key) => COL_META[key] || { prop: key, width: 120 }

const missingPresets = computed(() => {
  const inTpl = new Set(tplDraft.value.map(it => it.key))
  return PRESET_ORDER.filter(k => !inTpl.has(k))
})

// ========== 附加字段展示弹窗 ==========
const showExtra = ref(false)
const editingExtra = ref(false)
const currentExtraData = ref({})
const currentExtraOrderId = ref(null)
const editExtraItems = ref([])
const savingExtra = ref(false)

const statusLabel = (s) => ({ draft: '草稿', pending: '待生产', in_progress: '生产中', paused: '已暂停', completed: '已完成', cancelled: '已取消' }[s] || s)
const statusType = (s) => ({ draft: 'info', pending: 'warning', in_progress: 'success', paused: '', completed: 'primary', cancelled: 'danger' }[s] || '')

// v3.1.0: 工单绑定方式列渲染
const bindingLabel = (row) => {
  const scope = row.binding_scope || 'project'
  if (scope === 'channels') return '工位'
  if (scope === 'cluster')  return '集群'
  // project 模式但没绑项目 → "未绑定" 警告
  if (!row.project_id) return '未绑定'
  return '项目'
}
const bindingTagType = (row) => {
  const scope = row.binding_scope || 'project'
  if (scope === 'channels') return 'success'
  if (scope === 'cluster')  return 'primary'
  if (!row.project_id) return 'danger'
  return 'info'
}
const bindingDetail = (row) => {
  const scope = row.binding_scope || 'project'
  if (scope === 'channels') {
    return `工位 [${(row.target_channels || []).join(', ')}]`
  }
  if (scope === 'cluster') {
    // v3.1.0: 简化为整集群计件, target_stations 留作高级扩展位
    const ts = row.target_stations || []
    return ts.length > 0 ? `集群 [${ts.join(', ')}]` : '集群整体 (按 box_serial)'
  }
  if (!row.project_id) return '⚠ 未绑定项目, 不会计件'
  const proj = projectOptions.value.find(p => p.id === row.project_id)
  return proj ? `项目: ${proj.name}` : `项目 #${row.project_id}`
}

const formatTime = (t) => {
  if (!t) return '-'
  return t.replace('T', ' ').substring(0, 19)
}

const hasExtraData = (row) => row.extra_data && Object.keys(row.extra_data).length > 0

const loadArchivedOrderIds = () => {
  try {
    const raw = localStorage.getItem(ORDER_ARCHIVE_KEY)
    if (!raw) {
      archivedOrderIds.value = new Set()
      return
    }
    const arr = JSON.parse(raw)
    if (!Array.isArray(arr)) {
      archivedOrderIds.value = new Set()
      return
    }
    archivedOrderIds.value = new Set(arr.map(v => Number(v)).filter(v => Number.isInteger(v) && v > 0))
  } catch {
    archivedOrderIds.value = new Set()
  }
}

const persistArchivedOrderIds = () => {
  localStorage.setItem(ORDER_ARCHIVE_KEY, JSON.stringify(Array.from(archivedOrderIds.value)))
}

const archiveOrderLocally = (orderId) => {
  archivedOrderIds.value.add(orderId)
  persistArchivedOrderIds()
}

const unarchiveOrderLocally = (orderId) => {
  if (archivedOrderIds.value.delete(orderId)) {
    persistArchivedOrderIds()
  }
}

// ========== 模板持久化 ==========
const normalizeTemplateItems = (arr, forceVisible = false) => {
  if (!Array.isArray(arr)) return buildDefaultTemplate()
  const normalized = arr.map(it => {
    const k = String(it.key || '')
    const isSystem = SYSTEM_COL_KEYS.has(k)
    return {
      key: k,
      label: String(it.label || it.key || ''),
      type: isSystem ? 'system' : (it.type || 'text'),
      required: !isSystem && !!it.required,
      preset: PRESET_KEYS.has(k),
      system: isSystem,
      visible: forceVisible ? true : (it.visible !== false),
      optionsText: isSystem ? '' : (it.optionsText || ''),
    }
  }).filter(it => it.key)

  const existingKeys = new Set(normalized.map(it => it.key))
  for (const sk of SYSTEM_COL_ORDER) {
    if (!existingKeys.has(sk)) {
      normalized.push({
        key: sk,
        label: SYSTEM_COL_META[sk].label,
        type: 'system',
        required: false,
        preset: false,
        system: true,
        visible: true,
        optionsText: '',
      })
    }
  }
  return normalized
}

const buildTemplateName = (library) => {
  let idx = 1
  while (library.some(t => t.name === `模板${idx}`)) idx++
  return `模板${idx}`
}

const buildTemplateRecord = (name, items) => ({
  id: `tpl_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
  name,
  items: normalizeTemplateItems(items),
  createdAt: Date.now(),
  updatedAt: Date.now(),
})

const applyActiveTemplate = () => {
  const current = templateLibrary.value.find(t => t.id === activeTemplateId.value)
  if (current) {
    template.value = JSON.parse(JSON.stringify(current.items))
    return
  }
  if (templateLibrary.value.length > 0) {
    activeTemplateId.value = templateLibrary.value[0].id
    template.value = JSON.parse(JSON.stringify(templateLibrary.value[0].items))
    return
  }
  const fallback = buildTemplateRecord('模板1', buildDefaultTemplate())
  templateLibrary.value = [fallback]
  activeTemplateId.value = fallback.id
  template.value = JSON.parse(JSON.stringify(fallback.items))
}

const persistTemplateLibrary = () => {
  localStorage.setItem(TEMPLATE_LIBRARY_KEY, JSON.stringify(templateLibrary.value))
  localStorage.setItem(TEMPLATE_ACTIVE_ID_KEY, activeTemplateId.value || '')
  localStorage.setItem(TEMPLATE_KEY, JSON.stringify(template.value))
}

const loadTemplate = () => {
  try {
    const needMigrateVisible = !localStorage.getItem(TEMPLATE_VISIBLE_MIGRATED_KEY)
    const rawLibrary = localStorage.getItem(TEMPLATE_LIBRARY_KEY)

    if (rawLibrary) {
      const parsed = JSON.parse(rawLibrary)
      if (Array.isArray(parsed) && parsed.length > 0) {
        templateLibrary.value = parsed.map((item, i) => ({
          id: String(item.id || `tpl_${Date.now()}_${i}`),
          name: String(item.name || `模板${i + 1}`),
          items: normalizeTemplateItems(item.items, needMigrateVisible),
          createdAt: Number(item.createdAt) || Date.now(),
          updatedAt: Number(item.updatedAt) || Date.now(),
        }))
        const storedActiveId = localStorage.getItem(TEMPLATE_ACTIVE_ID_KEY) || ''
        activeTemplateId.value = templateLibrary.value.some(t => t.id === storedActiveId)
          ? storedActiveId
          : templateLibrary.value[0].id
        applyActiveTemplate()
        if (needMigrateVisible) {
          try { localStorage.setItem(TEMPLATE_VISIBLE_MIGRATED_KEY, '1') } catch {}
          persistTemplateLibrary()
        }
        return
      }
    }

    const rawLegacy = localStorage.getItem(TEMPLATE_KEY)
    const legacyItems = rawLegacy ? JSON.parse(rawLegacy) : buildDefaultTemplate()
    const migrated = buildTemplateRecord('模板1', normalizeTemplateItems(legacyItems, needMigrateVisible))
    templateLibrary.value = [migrated]
    activeTemplateId.value = migrated.id
    applyActiveTemplate()
    if (needMigrateVisible) {
      try { localStorage.setItem(TEMPLATE_VISIBLE_MIGRATED_KEY, '1') } catch {}
    }
    persistTemplateLibrary()
  } catch {
    const fallback = buildTemplateRecord('模板1', buildDefaultTemplate())
    templateLibrary.value = [fallback]
    activeTemplateId.value = fallback.id
    applyActiveTemplate()
    persistTemplateLibrary()
  }
}

const handleTemplateSwitch = (id) => {
  if (!id) return
  activeTemplateId.value = id
  applyActiveTemplate()
  persistTemplateLibrary()
}

// ========== 列表加载 ==========
const loadOrders = async () => {
  try {
    const res = await getOrders({
      keyword: keyword.value || undefined,
      status: filterStatus.value || undefined,
      project_id: (onlyCurrentProject.value && projectStore.currentProjectId) ? projectStore.currentProjectId : undefined,
      skip: (currentPage.value - 1) * pageSize.value,
      limit: pageSize.value,
    })
    const items = res.data.items || []
    orders.value = items.filter(item => !archivedOrderIds.value.has(item.id))
    const serverTotal = res.data.total || 0
    total.value = Math.max(0, serverTotal - archivedOrderIds.value.size)
  } catch (e) {
    dbgErr('mes.order', '加载工单列表', e)
    ElMessage.error('加载工单失败')
  }
}

const handleProjectScopeChange = () => { currentPage.value = 1; loadOrders() }
watch(() => projectStore.currentProjectId, () => {
  if (onlyCurrentProject.value) { currentPage.value = 1; loadOrders() }
})

const toggleAutoRefresh = (val) => {
  // D7: 开启前先清旧定时器, 防止重复开启叠加多个 interval(长跑泄漏)
  if (refreshTimer) { clearInterval(refreshTimer); refreshTimer = null }
  if (val) {
    refreshTimer = setInterval(loadOrders, 10000)
  }
}

// ========== 打开新建/编辑 ==========
const loadBindingOptions = async () => {
  // 项目列表
  try {
    const res = await getProjects()
    const data = res.data?.items || res.data || []
    projectOptions.value = (Array.isArray(data) ? data : []).map(p => ({
      id: p.id, name: p.name, channel_count: p.channel_count || 1,
    }))
  } catch {
    projectOptions.value = []
  }
  // 集群站点候选
  try {
    const res = await getClusterConfig()
    const cfg = res.data || {}
    stationOptions.value = Array.isArray(cfg.expected_stations) ? cfg.expected_stations : []
  } catch {
    stationOptions.value = []
  }
}

const openCreate = async () => {
  dbg('mes.order', '点击「新建工单」')
  editingId.value = null
  _editingExtraOriginal.value = {}
  presetValues.value = {}
  customValues.value = {}
  for (const it of formItems.value) {
    if (it.preset) presetValues.value[it.key] = defaultValueFor(it)
    else customValues.value[it.key] = defaultValueFor(it)
  }
  bindingForm.value = defaultBindingForm()
  await loadBindingOptions()
  showCreate.value = true
}

const editOrder = async (row) => {
  dbg('mes.order', '点击「编辑工单」', `id=${row?.id} order_no=${row?.order_no || ''}`)
  editingId.value = row.id
  const extra = row.extra_data || {}
  _editingExtraOriginal.value = JSON.parse(JSON.stringify(extra))

  presetValues.value = {}
  customValues.value = {}
  for (const it of formItems.value) {
    if (it.preset) {
      presetValues.value[it.key] = row[it.key] ?? defaultValueFor(it)
    } else {
      customValues.value[it.key] = extra[it.key] ?? defaultValueFor(it)
    }
  }

  // v3.1.0: 回填绑定方式. 老工单可能字段全空 → 默认 project, project_id 也可能空
  bindingForm.value = {
    scope: row.binding_scope || 'project',
    project_id: row.project_id ?? null,
    target_channels: Array.isArray(row.target_channels) ? [...row.target_channels] : [],
    target_stations: Array.isArray(row.target_stations) ? [...row.target_stations] : [],
  }
  await loadBindingOptions()
  showCreate.value = true
}

function defaultValueFor(it) {
  if (it.type === 'number') return 0
  if (it.type === 'switch') return false
  return ''
}

// ========== 保存工单 ==========
const handleSave = async () => {
  dbg('mes.order', editingId.value ? '点击「保存编辑工单」' : '点击「保存新建工单」', `id=${editingId.value ?? ''} order_no=${presetValues.value?.order_no || ''}`)
  // 1) 必填校验：按模板中 required=true 的字段检查
  for (const it of formItems.value) {
    const v = it.preset ? presetValues.value[it.key] : customValues.value[it.key]
    if (it.required && (v === null || v === undefined || v === '' || (typeof v === 'number' && Number.isNaN(v)))) {
      ElMessage.warning(`请填写『${it.label}』`)
      return
    }
  }

  // v3.1.0: 绑定方式校验
  const bf = bindingForm.value
  if (bf.scope === 'project' && !bf.project_id) {
    ElMessage.warning('请选择「所属项目」')
    return
  }
  if (bf.scope === 'channels' && (!Array.isArray(bf.target_channels) || bf.target_channels.length === 0)) {
    ElMessage.warning('请至少选择一个「目标工位」')
    return
  }
  // v3.1.0: cluster 模式不再要求选站点 (主机即代表整个集群)

  saving.value = true
  try {
    // 2) 构造 payload：preset → 后端标准字段；其余 → extra_data
    const payload = {}
    const extra = editingId.value ? { ..._editingExtraOriginal.value } : {}

    for (const it of formItems.value) {
      if (it.preset) {
        payload[it.key] = presetValues.value[it.key]
      } else {
        extra[it.key] = customValues.value[it.key]
      }
    }

    // 3) 必填兜底：模板里删掉了 order_no/product_name 时，后端仍强制要求
    if (!('order_no' in payload) || !payload.order_no) {
      if (!editingId.value) payload.order_no = `ORD-${Date.now()}`
    }
    if (!('product_name' in payload) || !payload.product_name) {
      if (!editingId.value) payload.product_name = '未命名'
    }
    // product_code/spec 等非必填，后端允许为空/null
    payload.extra_data = Object.keys(extra).length > 0 ? extra : null

    // v3.1.0: 绑定字段 (后端 _normalize_binding 会按 scope 三选一)
    // cluster 模式不再带 target_stations, 由后端 find_cluster_orders 取首条活跃工单
    payload.binding_scope = bf.scope
    payload.project_id = bf.scope === 'project' ? bf.project_id : null
    payload.target_channels = bf.scope === 'channels' ? bf.target_channels : null
    payload.target_stations = null

    if (editingId.value) {
      await updateOrder(editingId.value, payload)
      ElMessage.success('工单已更新')
    } else {
      await createOrder(payload)
      ElMessage.success('工单已创建')
    }
    dbg('mes.order', editingId.value ? '工单更新成功' : '工单创建成功', `scope=${bf?.scope || ''} order_no=${payload?.order_no || ''}`)
    showCreate.value = false
    loadOrders()
  } catch (e) {
    dbgErr('mes.order', '保存工单', e)
    ElMessage.error(e.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}

// ========== 状态/删除 ==========
const changeStatus = async (row, status) => {
  dbg('mes.order', '点击「工单状态流转」', `id=${row?.id} order_no=${row?.order_no || ''} ${row?.status || ''} -> ${status}`)
  try {
    await changeOrderStatus(row.id, { status })
    ElMessage.success(`工单 ${row.order_no} 状态已变更为 ${statusLabel(status)}`)
    loadOrders()
  } catch (e) {
    dbgErr('mes.order', '工单状态流转', e)
    ElMessage.error(e.response?.data?.detail || '状态变更失败')
  }
}

const handleDelete = async (row) => {
  dbg('mes.order', '点击「删除工单」', `id=${row?.id} order_no=${row?.order_no || ''} status=${row?.status || ''}`)
  try {
    if (['draft', 'cancelled'].includes(row.status)) {
      await ElMessageBox.confirm(`确定删除工单 ${row.order_no}？此操作不可恢复。`, '确认')
      await deleteOrder(row.id)
      unarchiveOrderLocally(row.id)
      ElMessage.success('工单已删除')
      loadOrders()
      return
    }

    await ElMessageBox.confirm(
      `工单 ${row.order_no} 当前处于${statusLabel(row.status)}，为避免影响追溯数据，将执行归档隐藏（可继续保留历史记录）。是否继续？`,
      '归档工单',
      { type: 'warning' }
    )
    archiveOrderLocally(row.id)
    ElMessage.success('工单已归档隐藏')
    loadOrders()
  } catch (e) {
    if (e !== 'cancel' && e !== 'close') dbgErr('mes.order', '删除工单', e)
    if (e !== 'cancel' && e !== 'close')
      ElMessage.error('删除失败: ' + (e.response?.data?.detail || e.message || '未知错误'))
  }
}

// ========== 附加字段查看 ==========
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

// ========== 模板编辑器交互 ==========
const openTemplate = () => {
  // v2.7.15: 拷贝时强制 normalize visible (兼容 HMR 残留 / 旧版 localStorage 缺失字段)
  tplDraft.value = JSON.parse(JSON.stringify(template.value)).map(it => ({
    ...it,
    visible: it.visible !== false,
  }))
  showTemplate.value = true
}

const moveUp = (i) => {
  if (i <= 0) return
  const arr = tplDraft.value
  ;[arr[i - 1], arr[i]] = [arr[i], arr[i - 1]]
}
const moveDown = (i) => {
  const arr = tplDraft.value
  if (i >= arr.length - 1) return
  ;[arr[i], arr[i + 1]] = [arr[i + 1], arr[i]]
}
const removeItem = (i) => {
  tplDraft.value.splice(i, 1)
}

const addCustomField = () => {
  let idx = 1
  while (tplDraft.value.some(it => it.key === `custom_${idx}`)) idx++
  tplDraft.value.push({
    key: `custom_${idx}`, label: `自定义字段${idx}`,
    type: 'text', required: false, preset: false, visible: true, optionsText: '',
  })
}

const addMissingPreset = (k) => {
  tplDraft.value.push({
    key: k, label: PRESET_META[k].label, type: PRESET_META[k].type,
    required: PRESET_META[k].required, preset: true, visible: true,
    optionsText: PRESET_META[k].optionsText || '',
  })
}

const resetTemplate = () => {
  tplDraft.value = buildDefaultTemplate()
}

const createTemplate = () => {
  const name = buildTemplateName(templateLibrary.value)
  const record = buildTemplateRecord(name, buildDefaultTemplate())
  templateLibrary.value.push(record)
  activeTemplateId.value = record.id
  applyActiveTemplate()
  tplDraft.value = JSON.parse(JSON.stringify(template.value))
  persistTemplateLibrary()
  ElMessage.success(`已创建${name}`)
}

const duplicateTemplate = () => {
  const source = templateLibrary.value.find(t => t.id === activeTemplateId.value)
  const name = buildTemplateName(templateLibrary.value)
  const record = buildTemplateRecord(name, source ? source.items : template.value)
  templateLibrary.value.push(record)
  activeTemplateId.value = record.id
  applyActiveTemplate()
  tplDraft.value = JSON.parse(JSON.stringify(template.value))
  persistTemplateLibrary()
  ElMessage.success(`已另存为${name}`)
}

const renameTemplate = async () => {
  const current = templateLibrary.value.find(t => t.id === activeTemplateId.value)
  if (!current) return
  try {
    const { value } = await ElMessageBox.prompt('请输入模板名称', '重命名模板', {
      inputValue: current.name,
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      inputPattern: /.+/,
      inputErrorMessage: '模板名称不能为空',
    })
    const name = String(value || '').trim()
    if (!name) return
    current.name = name
    current.updatedAt = Date.now()
    persistTemplateLibrary()
    ElMessage.success('模板已重命名')
  } catch {}
}

const deleteTemplate = async () => {
  if (templateLibrary.value.length <= 1) {
    ElMessage.warning('至少保留一个模板')
    return
  }
  const current = templateLibrary.value.find(t => t.id === activeTemplateId.value)
  if (!current) return
  try {
    await ElMessageBox.confirm(`确定删除模板『${current.name}』？`, '删除模板')
    templateLibrary.value = templateLibrary.value.filter(t => t.id !== current.id)
    activeTemplateId.value = templateLibrary.value[0]?.id || ''
    applyActiveTemplate()
    tplDraft.value = JSON.parse(JSON.stringify(template.value))
    persistTemplateLibrary()
    ElMessage.success('模板已删除')
  } catch {}
}

const saveTemplate = () => {
  // 校验自定义字段 key 唯一且为合法标识符
  const keys = new Set()
  for (const it of tplDraft.value) {
    const k = String(it.key || '').trim()
    if (!k) { ElMessage.warning('存在空的字段Key'); return }
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(k)) {
      ElMessage.warning(`字段Key『${k}』必须是英文/下划线开头的标识符`); return
    }
    if (keys.has(k)) { ElMessage.warning(`字段Key『${k}』重复`); return }
    keys.add(k)
    it.key = k
    it.label = String(it.label || k).trim() || k
    it.preset = PRESET_KEYS.has(k)
    it.visible = it.visible !== false  // undefined → true
    // v2.7.15: system 列保护 — 强制 type/required, 防止误改
    it.system = SYSTEM_COL_KEYS.has(k)
    if (it.system) {
      it.type = 'system'
      it.required = false
      it.optionsText = ''
    }
  }
  const normalizedItems = normalizeTemplateItems(tplDraft.value)
  const current = templateLibrary.value.find(t => t.id === activeTemplateId.value)
  if (current) {
    current.items = JSON.parse(JSON.stringify(normalizedItems))
    current.updatedAt = Date.now()
  } else {
    const record = buildTemplateRecord(buildTemplateName(templateLibrary.value), normalizedItems)
    templateLibrary.value.push(record)
    activeTemplateId.value = record.id
  }
  applyActiveTemplate()
  persistTemplateLibrary()
  ElMessage.success('字段模板已保存')
  showTemplate.value = false
}

onMounted(() => {
  loadArchivedOrderIds()
  loadTemplate()
  loadOrders()
  // v3.1.0: 列表里要显示项目名/集群站点, 提前拉一次
  loadBindingOptions()
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
.tpl-table :deep(.el-table__header th) {
  background: #0f172a !important;
  color: #94a3b8;
}
</style>
