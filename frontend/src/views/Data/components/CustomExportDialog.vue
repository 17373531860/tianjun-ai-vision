<!--
  v3.5.0 自定义导出对话框 (MVP 版)
  
  本版能力：
  - 数据范围 4 选 1：单 cycle / 单 session / 日期范围 / 系统级
  - 模板下拉（含系统预设 + 用户自建），可编辑、复制、删除、新建
  - 模板内容文本编辑（Step 3 会扩展为字段树拖拽）
  - 实时预览 + 错误捕获
  - 立即下载（流式 Blob）

  Step 3 计划升级：
  - 左侧加 "ExportFieldTree" — 308 字段分组树，可拖拽到内容编辑器
  - 内容编辑器换成支持拖入 token 的富文本（保留 Jinja2 兼容）
  - 加"基于输入文件改写"开关 (input_file_mode)
  - 加文件名模板的字段树辅助
-->
<template>
  <el-dialog
    v-model="visible"
    title="自定义导出"
    width="80%"
    :close-on-click-modal="false"
    destroy-on-close
    class="dark-dialog custom-export-dialog"
  >
    <el-form label-position="top" :model="form" class="text-sm">
      <el-row :gutter="16">
        <!-- 左列：数据范围 -->
        <el-col :span="8">
          <div class="panel-card !p-3 h-full">
            <div class="panel-header !mb-3">
              <el-icon class="text-cyan-400"><Aim /></el-icon>
              <span>数据范围</span>
            </div>

            <el-form-item label="范围模式">
              <el-radio-group v-model="form.scope" class="!flex !flex-col !gap-2">
                <el-radio value="cycle">单 cycle (按 ID)</el-radio>
                <el-radio value="session">单 session (按 ID)</el-radio>
                <el-radio value="range">日期范围</el-radio>
                <el-radio value="system">系统级 (无周期)</el-radio>
              </el-radio-group>
            </el-form-item>

            <el-form-item v-if="form.scope === 'cycle'" label="Cycle ID">
              <el-input-number v-model="form.cycle_id" :min="1" :precision="0" class="w-full" />
              <div class="text-xs text-gray-500 mt-1">默认填当前选中行的 cycle</div>
            </el-form-item>

            <el-form-item v-if="form.scope === 'session'" label="Session ID">
              <el-input-number v-model="form.session_id" :min="1" :precision="0" class="w-full" />
            </el-form-item>

            <template v-if="form.scope === 'range'">
              <el-form-item label="日期范围">
                <el-date-picker
                  v-model="rangePicker"
                  type="daterange"
                  start-placeholder="起"
                  end-placeholder="止"
                  value-format="YYYY-MM-DD"
                  class="w-full"
                />
              </el-form-item>
              <el-form-item label="项目 (可选)">
                <el-input-number v-model="form.project_id" :min="1" :precision="0" class="w-full" />
              </el-form-item>
              <el-form-item label="通道 (可选)">
                <el-input-number v-model="form.channel_id" :min="0" :precision="0" class="w-full" />
              </el-form-item>
              <el-form-item>
                <el-checkbox v-model="form.include_cycles">包含 cycle 明细 (stats.cycles)</el-checkbox>
              </el-form-item>
            </template>
          </div>
        </el-col>

        <!-- 中列：模板选择 + 元数据 -->
        <el-col :span="8">
          <div class="panel-card !p-3 h-full">
            <div class="panel-header !mb-3 !flex !justify-between !items-center">
              <div class="flex items-center gap-2">
                <el-icon class="text-cyan-400"><Document /></el-icon>
                <span>模板</span>
              </div>
              <div class="flex gap-1">
                <el-tooltip content="新建空白模板" placement="top">
                  <el-button size="small" plain @click="onNewTemplate">
                    <el-icon><Plus /></el-icon>
                  </el-button>
                </el-tooltip>
                <el-tooltip content="复制当前模板" placement="top">
                  <el-button
                    size="small"
                    plain
                    :disabled="!form.template_id"
                    @click="onCloneTemplate"
                  >
                    <el-icon><CopyDocument /></el-icon>
                  </el-button>
                </el-tooltip>
                <el-tooltip content="保存当前模板" placement="top">
                  <el-button
                    size="small"
                    type="primary"
                    plain
                    :disabled="!canSaveTemplate"
                    @click="onSaveTemplate"
                  >
                    <el-icon><Check /></el-icon>
                  </el-button>
                </el-tooltip>
                <el-tooltip content="删除自建模板" placement="top">
                  <el-button
                    size="small"
                    type="danger"
                    plain
                    :disabled="!canDeleteTemplate"
                    @click="onDeleteTemplate"
                  >
                    <el-icon><Delete /></el-icon>
                  </el-button>
                </el-tooltip>
              </div>
            </div>

            <el-form-item label="选择模板">
              <el-select
                v-model="form.template_id"
                placeholder="选择已有模板，或点 + 新建"
                class="w-full"
                clearable
                @change="onTemplateSelect"
              >
                <el-option-group label="系统预设">
                  <el-option
                    v-for="t in systemTemplates"
                    :key="t.id"
                    :label="`[${t.format}] ${t.name}`"
                    :value="t.id"
                  >
                    <span>{{ t.name }}</span>
                    <el-tag size="small" type="info" class="ml-2">{{ t.format }}</el-tag>
                  </el-option>
                </el-option-group>
                <el-option-group label="自建模板">
                  <el-option
                    v-for="t in userTemplates"
                    :key="t.id"
                    :label="`[${t.format}] ${t.name}`"
                    :value="t.id"
                  >
                    <span>{{ t.name }}</span>
                    <el-tag size="small" type="success" class="ml-2">{{ t.format }}</el-tag>
                  </el-option>
                </el-option-group>
              </el-select>
            </el-form-item>

            <el-form-item label="模板名称">
              <el-input
                v-model="form.template_name"
                placeholder="自建/复制后请改名"
                :disabled="isSystemTemplate"
              />
              <div v-if="isSystemTemplate" class="text-xs text-orange-300 mt-1">
                系统预设不可改 — 请先点 <el-icon class="align-middle"><CopyDocument /></el-icon> 复制
              </div>
            </el-form-item>

            <el-form-item label="输出格式">
              <el-select v-model="form.fmt" :disabled="isSystemTemplate" class="w-full">
                <el-option label="txt - 纯文本" value="txt" />
                <el-option label="csv - 逗号分隔表格" value="csv" />
                <el-option label="docx - Word (自动样式 / 上传模板)" value="docx" />
                <el-option label="xlsx - Excel (自动样式 / 上传模板)" value="xlsx" />
                <el-option label="pdf - 中文 PDF (自动样式)" value="pdf" />
              </el-select>
              <div class="text-xs text-gray-500 mt-1" v-if="['docx','xlsx','pdf'].includes(form.fmt)">
                <span v-if="form.fmt === 'pdf'">
                  PDF 仅自动样式：模板纯文本/markdown，自动转表格 + 中文字体 STSong-Light
                </span>
                <span v-else>
                  自动样式：模板纯文本/markdown，自动识别 # 标题 + 逗号分隔行转表格<br>
                  上传模板：编辑模板时上传 .{{ form.fmt }} 占位符文件，保留原样式渲染填值
                </span>
              </div>
            </el-form-item>

            <el-form-item label="文件名模板">
              <el-input
                v-model="form.filename_template"
                :placeholder="filenamePlaceholder"
              />
              <div class="text-xs text-gray-500 mt-1">
                Jinja2 模板。常用：<code>&#123;&#123; cycle.id &#125;&#125;</code>、<code>&#123;&#123; now_ymdhms &#125;&#125;</code>
              </div>
            </el-form-item>

            <!-- v3.5.0 Step 5.6: docx/xlsx 占位符模板上传 (路线 B) -->
            <el-form-item v-if="['docx', 'xlsx'].includes(form.fmt)" label="占位符模板文件 (路线 B)">
              <div v-if="!form.template_id || isSystemTemplate" class="text-xs text-orange-300">
                请先选择/新建用户模板（系统预设无法上传）
              </div>
              <template v-else>
                <div v-if="currentTemplate?.template_file_path" class="space-y-2">
                  <div class="flex items-center justify-between gap-2 bg-slate-800 rounded p-2 border border-slate-700">
                    <div class="flex items-center gap-2 min-w-0">
                      <el-icon class="text-emerald-400 flex-shrink-0"><Document /></el-icon>
                      <code class="text-cyan-400 text-[11px] truncate">
                        {{ currentTemplate.template_file_path }}
                      </code>
                      <el-tag size="small" type="success">路线 B</el-tag>
                    </div>
                    <div class="flex gap-1">
                      <el-button size="small" link type="primary" @click="onDownloadTemplateFile">下载</el-button>
                      <el-button size="small" link type="danger" @click="onDeleteTemplateFile">删除</el-button>
                    </div>
                  </div>
                  <div class="text-xs text-gray-500">
                    渲染时会保留此 .{{ form.fmt }} 文件的原始字体/表格/图片样式，只替换 <code>&#123;&#123; ... &#125;&#125;</code> 占位符。
                  </div>
                </div>
                <el-upload
                  v-else
                  :show-file-list="false"
                  :before-upload="beforeUploadTemplate"
                  :http-request="onUploadTemplateFile"
                  :accept="form.fmt === 'docx' ? '.docx' : '.xlsx'"
                  drag
                  class="upload-template-zone"
                >
                  <el-icon class="text-cyan-400" :size="32"><UploadFilled /></el-icon>
                  <div class="text-sm text-gray-400 mt-2">
                    点击或拖拽 <code>.{{ form.fmt }}</code> 占位符模板文件
                  </div>
                  <div class="text-xs text-gray-500 mt-1">
                    保留原文档样式渲染（路线 B）。不上传则走自动样式（路线 A）
                  </div>
                </el-upload>
              </template>
            </el-form-item>
          </div>
        </el-col>

        <!-- 右列：模板字段速查 (简易版，Step 3 会换成完整字段树) -->
        <el-col :span="8">
          <div class="panel-card !p-3 h-full">
            <div class="panel-header !mb-3 !flex !justify-between !items-center">
              <div class="flex items-center gap-2">
                <el-icon class="text-cyan-400"><Search /></el-icon>
                <span>字段速查 (308)</span>
              </div>
              <el-button size="small" link @click="loadFields">刷新</el-button>
            </div>

            <el-input
              v-model="fieldFilter"
              placeholder="搜索字段路径或标签"
              size="small"
              clearable
              class="mb-2"
            >
              <template #prefix><el-icon><Search /></el-icon></template>
            </el-input>

            <div class="field-tree">
              <div v-for="g in filteredGroups" :key="g.group" class="mb-2">
                <div
                  class="text-xs text-cyan-300 font-semibold mb-1 cursor-pointer hover:text-cyan-100 select-none"
                  @click="toggleGroup(g.group)"
                >
                  <el-icon class="align-middle mr-1" :size="10">
                    <component :is="collapsedGroups[g.group] ? 'ArrowRight' : 'ArrowDown'" />
                  </el-icon>
                  {{ g.label }} ({{ g.fields.length }})
                </div>
                <div v-if="!collapsedGroups[g.group]" class="space-y-1">
                  <div
                    v-for="f in g.fields"
                    :key="f.path"
                    class="field-item"
                    :title="(f.notes || f.example || '') + ' · 可拖拽到模板内容区'"
                    draggable="true"
                    @click="insertField(f)"
                    @dragstart="onFieldDragStart($event, f)"
                  >
                    <code class="text-cyan-400 text-[11px]">{{ f.path }}</code>
                    <span class="text-gray-500 text-[10px] ml-1">{{ f.label }}</span>
                  </div>
                </div>
              </div>
              <div v-if="filteredGroups.length === 0" class="text-gray-500 text-xs p-2">
                无匹配字段
              </div>
            </div>
            <div class="text-xs text-gray-500 mt-2">
              点击字段插入到光标位置 · Step 3 会做拖拽编辑器
            </div>
          </div>
        </el-col>
      </el-row>

      <!-- v3.5.0 Step 3: 模板内容 + 工具栏 + 实时预览 -->
      <el-row :gutter="16" class="mt-4">
        <!-- 左：模板内容编辑器 -->
        <el-col :span="12">
          <div class="text-xs text-gray-400 mb-2 flex justify-between items-center">
            <span>模板内容 (Jinja2)</span>
            <div class="flex items-center gap-2">
              <span v-if="isSystemTemplate" class="text-orange-300">
                ⚠ 系统预设只读
              </span>
              <span v-if="hasLicense" class="text-emerald-400" title="license 已注入">
                <el-icon class="align-middle" :size="11"><Lock /></el-icon>
                license
              </span>
              <span v-if="hasDisplay" class="text-cyan-400" title="display 字段从前端 store 注入">
                <el-icon class="align-middle" :size="11"><User /></el-icon>
                display
              </span>
            </div>
          </div>

          <!-- 快速插入工具栏 -->
          <div class="quick-toolbar mb-2" v-if="!isSystemTemplate">
            <span class="text-[11px] text-gray-500 mr-2">快插：</span>
            <el-button
              v-for="snip in quickSnippets"
              :key="snip.label"
              size="small"
              link
              :title="snip.tip"
              @click="insertText(snip.text)"
            >{{ snip.label }}</el-button>
          </div>

          <!-- textarea 容器 - 接收 drop -->
          <div
            class="textarea-drop-zone"
            :class="{ 'is-drag-over': isDragOver }"
            @dragover.prevent="onDragOver"
            @dragleave.prevent="isDragOver = false"
            @drop.prevent="onDropToTextarea"
          >
            <el-input
              v-model="form.template_content"
              type="textarea"
              :rows="14"
              :readonly="isSystemTemplate"
              :placeholder="contentPlaceholder"
              class="mono-textarea"
              ref="contentRef"
            />
            <div v-if="isDragOver" class="drop-indicator">
              <el-icon class="mr-1"><Plus /></el-icon> 释放以插入字段
            </div>
          </div>
        </el-col>

        <!-- 右：实时预览 -->
        <el-col :span="12">
          <div class="text-xs text-gray-400 mb-2 flex justify-between items-center">
            <span>实时预览</span>
            <div class="flex gap-2 items-center">
              <span v-if="previewMeta" class="text-gray-500">
                <span v-if="previewMeta.filename">
                  文件名: <code class="text-cyan-400">{{ previewMeta.filename }}</code> ·
                </span>
                上下文 {{ (previewMeta.context_size / 1024).toFixed(1) }}KB
              </span>
              <el-button size="small" plain @click="doPreview" :loading="previewing">
                <el-icon class="mr-1"><Refresh /></el-icon>刷新
              </el-button>
              <el-button size="small" link @click="showCtxJson = !showCtxJson">
                <el-icon class="mr-1"><DataAnalysis /></el-icon>
                {{ showCtxJson ? '隐藏 JSON' : '查看上下文 JSON' }}
              </el-button>
            </div>
          </div>
          <el-input
            v-if="!showCtxJson"
            :model-value="previewError ? previewError : previewText"
            type="textarea"
            :rows="14"
            readonly
            :class="['mono-textarea', previewError && 'preview-error']"
            placeholder="点【刷新】生成预览"
          />
          <el-input
            v-else
            :model-value="previewMeta?.context || '(刷新预览后才会带回上下文 JSON)'"
            type="textarea"
            :rows="14"
            readonly
            class="mono-textarea ctx-json"
            placeholder="点【刷新】拉取上下文"
          />
          <div v-if="previewMeta?.context_truncated" class="text-xs text-orange-300 mt-1">
            ⚠ 上下文 JSON 超过 200KB 已截断显示，渲染本身不受影响
          </div>
        </el-col>
      </el-row>

      <!-- v3.5.0 Step 3.2: 输入文件改写模式（实时规则才生效，立即下载会忽略） -->
      <el-row :gutter="16" class="mt-4">
        <el-col :span="24">
          <div class="panel-card !p-3">
            <div class="panel-header !mb-3 !flex !justify-between !items-center">
              <div class="flex items-center gap-2">
                <el-icon class="text-cyan-400"><DocumentCopy /></el-icon>
                <span>输入文件改写模式（仅在保存为实时规则时生效）</span>
              </div>
              <el-tag v-if="form.input_file_mode !== 'none'" size="small" type="warning">
                立即下载会忽略此设置
              </el-tag>
            </div>

            <el-row :gutter="12">
              <el-col :span="8">
                <el-radio-group v-model="form.input_file_mode" class="!flex !flex-col !gap-2">
                  <el-radio value="none">直接生成 (无输入文件)</el-radio>
                  <el-radio value="read_template">读取作为模板初值</el-radio>
                  <el-radio value="append">追加到现有文件末尾</el-radio>
                </el-radio-group>
              </el-col>

              <el-col :span="16">
                <el-form-item v-if="form.input_file_mode !== 'none'" label="输入文件路径模板">
                  <el-input
                    v-model="form.input_file_template"
                    :placeholder="inputFilePlaceholder"
                  >
                    <template #append>
                      <el-tooltip content="可使用 Jinja2 变量，例：D:\\sn\\&#123;&#123;workpiece.serial_no&#125;&#125;.txt" placement="top">
                        <el-icon><InfoFilled /></el-icon>
                      </el-tooltip>
                    </template>
                  </el-input>
                  <div class="text-xs text-gray-500 mt-1">
                    路径支持 Jinja2 模板，扫码后会按 cycle 上下文展开。常用变量：
                    <code class="text-cyan-400 ml-1">workpiece.serial_no</code>、
                    <code class="text-cyan-400 ml-1">cycle.id</code>、
                    <code class="text-cyan-400 ml-1">project.name</code>
                  </div>
                </el-form-item>
                <el-form-item v-if="form.input_file_mode !== 'none'" label="输出文件路径模板">
                  <el-input
                    v-model="form.output_file_template"
                    :placeholder="outputFilePlaceholder"
                  />
                  <div class="text-xs text-gray-500 mt-1">
                    实时规则触发时把渲染结果按此路径写出。空 = 与输入文件同路径覆盖。
                  </div>
                </el-form-item>
              </el-col>
            </el-row>
          </div>
        </el-col>
      </el-row>
    </el-form>

    <template #footer>
      <div class="flex justify-between items-center">
        <div class="text-xs text-gray-500">
          v3.5.0 · 字段总数 {{ totalFields }} · 系统预设 {{ systemTemplates.length }} · 自建 {{ userTemplates.length }}
        </div>
        <div class="flex gap-2">
          <el-button @click="visible = false">关闭</el-button>
          <el-button type="primary" :loading="downloading" :disabled="!canDownload" @click="doDownload">
            <el-icon class="mr-1"><Download /></el-icon>立即下载
          </el-button>
        </div>
      </div>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, reactive, computed, watch, onMounted, nextTick } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import {
  Aim, Document, Search, Plus, CopyDocument, Check, Delete,
  Refresh, Download, ArrowRight, ArrowDown, Lock, User,
  DataAnalysis, DocumentCopy, InfoFilled, UploadFilled,
} from '@element-plus/icons-vue';
import { useSystemStore } from '@/store/useSystemStore';
import {
  getExportFields,
  listExportTemplates,
  createExportTemplate,
  updateExportTemplate,
  deleteExportTemplate,
  cloneExportTemplate,
  previewExport,
  renderExport,
  uploadTemplateFile,
  deleteTemplateFile,
  getTemplateFileDownloadUrl,
  downloadBlob,
  parseFilenameFromResponse,
} from '@/api/export';

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  // 调用方提示初始范围（点击"自定义导出"时若有 cycle/session 选中可传入）
  initialScope: { type: String, default: 'system' },
  initialCycleId: { type: Number, default: null },
  initialSessionId: { type: Number, default: null },
  initialDateRange: { type: Array, default: null }, // [start, end]
});
const emit = defineEmits(['update:modelValue']);

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
});

// ============ 状态 ============
const systemStore = useSystemStore();

const form = reactive({
  scope: 'system',
  cycle_id: null,
  session_id: null,
  project_id: null,
  channel_id: null,
  include_cycles: true,
  template_id: null,
  template_name: '',
  template_content: '',
  filename_template: '{{ cycle.id or now_ymdhms }}',
  fmt: 'txt',
  // Step 3.2: 输入文件改写模式
  input_file_mode: 'none',          // 'none' | 'read_template' | 'append'
  input_file_template: '',          // Jinja2: 例 D:\sn\{{ workpiece.serial_no }}.txt
  output_file_template: '',         // Jinja2，空=覆盖输入文件
});

const rangePicker = ref([]);
watch(rangePicker, (v) => {
  form.start_date = v?.[0] || null;
  form.end_date = v?.[1] || null;
});

// 占位符里包含字面 {{ }}，需要在 JS 里组装绕开 Vue 模板的插值解析
const _LB = '{' + '{';
const _RB = '}' + '}';
const filenamePlaceholder = `例如 ${_LB} workpiece.serial_no or cycle.id ${_RB}.txt`;
const contentPlaceholder = [
  '例如：',
  `${_LB} cycle.is_good ? 'Pass' : 'Fail' ${_RB}`,
  `{% for v in cycle.test_values %}${_LB} v ${_RB},{% endfor %}`,
  `REVISION=${_LB} app.version ${_RB}`,
].join('\n');
const inputFilePlaceholder = `例如 D:\\\\sn\\\\${_LB} workpiece.serial_no ${_RB}.txt`;
const outputFilePlaceholder = `留空覆盖输入；或 D:\\\\out\\\\${_LB} workpiece.serial_no ${_RB}.txt`;

// Step 3.4: 快插片段
const quickSnippets = [
  { label: 'if/else', text: `${_LB} cycle.is_good ? 'Pass' : 'Fail' ${_RB}`,
    tip: '三元判断，OK→Pass NG→Fail' },
  { label: 'for cycles', text: `{% for c in stats.cycles %}${_LB} c.id ${_RB},${_LB} c.result ${_RB}\n{% endfor %}`,
    tip: '遍历 cycle 列表（range 模式可用）' },
  { label: 'for steps', text: `{% for s in cycle.steps %}${_LB} s.label ${_RB}=${_LB} s.is_valid ? 'OK' : 'NG' ${_RB},{% endfor %}`,
    tip: '遍历 cycle 内步骤' },
  { label: 'for test_values', text: `{% for v in cycle.test_values %}${_LB} v ${_RB},{% endfor %}`,
    tip: '客户 SN.txt 第 2 行专用' },
  { label: '时间戳', text: `${_LB} now_ymdhms ${_RB}`, tip: '格式 20260506-120130' },
  { label: '版本', text: `${_LB} app.version ${_RB}`, tip: '软件版本号' },
  { label: 'NG-code', text: `{% if not cycle.is_good %},${_LB} cycle.ng_code ${_RB}{% endif %}`,
    tip: 'NG 时附加错误码' },
];

const templates = ref([]);
const fields = ref({ groups: [], total: 0 });
const fieldFilter = ref('');
const collapsedGroups = reactive({});

const previewText = ref('');
const previewMeta = ref(null);
const previewError = ref(null);
const previewing = ref(false);
const downloading = ref(false);
const contentRef = ref(null);
const showCtxJson = ref(false);

// Step 3.1: 拖拽
const isDragOver = ref(false);
const draggingField = ref(null);

// Step 3.3: license + display 注入
const licensePayload = ref(null);  // 从 Electron IPC 拿到的 license 信息
const hasLicense = computed(() => !!licensePayload.value);
const hasDisplay = computed(() =>
  !!(systemStore.display?.brandName || systemStore.display?.appName)
);

// ============ 计算属性 ============
const systemTemplates = computed(() => templates.value.filter(t => t.is_system));
const userTemplates = computed(() => templates.value.filter(t => !t.is_system));
const totalFields = computed(() => fields.value.total);

const currentTemplate = computed(() =>
  templates.value.find(t => t.id === form.template_id)
);
const isSystemTemplate = computed(() => !!currentTemplate.value?.is_system);

const canSaveTemplate = computed(() =>
  !!form.template_id && !isSystemTemplate.value
);
const canDeleteTemplate = computed(() =>
  !!form.template_id && !isSystemTemplate.value
);
const canDownload = computed(() =>
  form.template_content?.trim()
  && ['txt', 'csv', 'docx', 'xlsx', 'pdf'].includes(form.fmt)
);

const filteredGroups = computed(() => {
  const kw = fieldFilter.value.trim().toLowerCase();
  if (!kw) return fields.value.groups || [];
  return (fields.value.groups || [])
    .map(g => ({
      ...g,
      fields: g.fields.filter(f =>
        f.path.toLowerCase().includes(kw) ||
        (f.label || '').toLowerCase().includes(kw)
      ),
    }))
    .filter(g => g.fields.length > 0);
});

// ============ 数据加载 ============
async function loadTemplates() {
  try {
    const r = await listExportTemplates();
    templates.value = r.data?.items || r.items || [];
  } catch (e) {
    ElMessage.error('加载模板列表失败: ' + (e?.message || e));
  }
}

async function loadFields() {
  try {
    const r = await getExportFields(false);
    fields.value = r.data || r;
    // 默认折叠除 cycle/app/now 外的分组
    const defaultExpanded = new Set(['time', 'app', 'cycle', 'workpiece']);
    fields.value.groups?.forEach(g => {
      if (collapsedGroups[g.group] === undefined) {
        collapsedGroups[g.group] = !defaultExpanded.has(g.group);
      }
    });
  } catch (e) {
    ElMessage.error('加载字段树失败: ' + (e?.message || e));
  }
}

function toggleGroup(g) {
  collapsedGroups[g] = !collapsedGroups[g];
}

// 把字段元数据转成 Jinja2 token
function fieldToToken(f) {
  const tokens = {
    list: `{% for item in ${f.path.replace('[*]', '')} %}${_LB} item ${_RB}{% endfor %}`,
    dict_kv: `${_LB} ${f.path.split('.').slice(0, -1).join('.')}['key_name'] ${_RB}`,
    dict: `${_LB} ${f.path} ${_RB}`,
  };
  return tokens[f.type] || `${_LB} ${f.path} ${_RB}`;
}

// 点击字段插入到 textarea 光标位置
function insertField(f) {
  if (isSystemTemplate.value) {
    ElMessage.warning('系统预设只读，请先复制再编辑');
    return;
  }
  insertText(fieldToToken(f));
}

// 通用：在光标位置插入文本（工具栏快插也用这个）
function insertText(text) {
  if (isSystemTemplate.value) {
    ElMessage.warning('系统预设只读，请先复制再编辑');
    return;
  }
  const textarea = contentRef.value?.textarea;
  if (textarea && typeof textarea.selectionStart === 'number') {
    const start = textarea.selectionStart;
    const end = textarea.selectionEnd;
    const before = form.template_content.slice(0, start);
    const after = form.template_content.slice(end);
    form.template_content = before + text + after;
    nextTick(() => {
      const pos = start + text.length;
      textarea.focus();
      textarea.setSelectionRange(pos, pos);
    });
  } else {
    form.template_content += text;
  }
}

// Step 3.1: 拖拽 — dragstart 把 token 写入 dataTransfer
function onFieldDragStart(ev, f) {
  if (isSystemTemplate.value) {
    ev.preventDefault();
    return;
  }
  draggingField.value = f;
  const token = fieldToToken(f);
  // 浏览器原生：拖到 textarea 默认插入 dataTransfer 的 text，正合我意
  try { ev.dataTransfer.setData('text/plain', token); } catch { /* noop */ }
  try { ev.dataTransfer.effectAllowed = 'copy'; } catch { /* noop */ }
}

function onDragOver(ev) {
  if (isSystemTemplate.value) return;
  isDragOver.value = true;
  try { ev.dataTransfer.dropEffect = 'copy'; } catch { /* noop */ }
}

function onDropToTextarea(ev) {
  isDragOver.value = false;
  if (isSystemTemplate.value) return;
  // 浏览器拖到 textarea 时会原生插入 text，这里只是补一道保险：
  // 如果 dataTransfer 已被 textarea 自己处理，dataTransfer.types 仍会包含 text/plain
  // 但 textarea 已自动写入并触发 input → v-model 同步。
  // 唯一需要我们手动处理的场景：拖到 drop-zone 但落点在 textarea 外
  const token = ev.dataTransfer?.getData('text/plain');
  const textarea = contentRef.value?.textarea;
  if (token && textarea && ev.target !== textarea) {
    insertText(token);
  }
  draggingField.value = null;
}

// ============ 模板操作 ============
function onTemplateSelect(id) {
  if (!id) {
    form.template_name = '';
    form.template_content = '';
    return;
  }
  const t = templates.value.find(x => x.id === id);
  if (!t) return;
  form.template_name = t.name;
  form.template_content = t.content || '';
  form.fmt = t.format || 'txt';
}

async function onNewTemplate() {
  try {
    const { value: name } = await ElMessageBox.prompt(
      '请输入新模板名称',
      '新建模板',
      { inputPattern: /\S/, inputErrorMessage: '名称不能为空' }
    );
    const r = await createExportTemplate({
      name: name.trim(),
      format: form.fmt || 'txt',
      content: '',
      scope: 'both',
    });
    const created = r.data || r;
    await loadTemplates();
    form.template_id = created.id;
    onTemplateSelect(created.id);
    ElMessage.success(`已新建：${created.name}`);
  } catch (e) {
    if (e !== 'cancel' && e?.message) ElMessage.error('新建失败: ' + e.message);
  }
}

async function onCloneTemplate() {
  if (!form.template_id) return;
  try {
    const r = await cloneExportTemplate(form.template_id);
    const cloned = r.data || r;
    await loadTemplates();
    form.template_id = cloned.id;
    onTemplateSelect(cloned.id);
    ElMessage.success(`已复制：${cloned.name}`);
  } catch (e) {
    ElMessage.error('复制失败: ' + (e?.message || e));
  }
}

async function onSaveTemplate() {
  if (!canSaveTemplate.value) return;
  try {
    await updateExportTemplate(form.template_id, {
      name: form.template_name,
      content: form.template_content,
      format: form.fmt,
    });
    await loadTemplates();
    ElMessage.success('已保存');
  } catch (e) {
    ElMessage.error('保存失败: ' + (e?.response?.data?.detail || e?.message || e));
  }
}

// ============ 路线 B 占位符模板文件 (Step 5.6) ============
function beforeUploadTemplate(file) {
  const ext = file.name.split('.').pop().toLowerCase();
  if (!['docx', 'xlsx'].includes(ext)) {
    ElMessage.error('仅支持 .docx 或 .xlsx 文件');
    return false;
  }
  if (ext !== form.fmt) {
    ElMessage.error(`当前模板格式为 ${form.fmt}，但上传的是 .${ext}`);
    return false;
  }
  if (file.size > 20 * 1024 * 1024) {
    ElMessage.error('文件超过 20MB 限制');
    return false;
  }
  return true;
}

async function onUploadTemplateFile(opt) {
  if (!form.template_id || isSystemTemplate.value) {
    ElMessage.warning('请先选择/新建用户模板');
    return;
  }
  try {
    const r = await uploadTemplateFile(form.template_id, opt.file);
    await loadTemplates();
    onTemplateSelect(form.template_id);
    ElMessage.success('上传成功，渲染将走路线 B 保留原文档样式');
  } catch (e) {
    ElMessage.error('上传失败: ' + (e?.response?.data?.detail || e?.message || e));
  }
}

function onDownloadTemplateFile() {
  if (!form.template_id) return;
  window.open(getTemplateFileDownloadUrl(form.template_id), '_blank');
}

async function onDeleteTemplateFile() {
  if (!form.template_id) return;
  try {
    await ElMessageBox.confirm(
      '确认删除占位符模板文件？删除后渲染将回退到路线 A 自动样式。',
      '确认删除',
      { type: 'warning' }
    );
    await deleteTemplateFile(form.template_id);
    await loadTemplates();
    onTemplateSelect(form.template_id);
    ElMessage.success('已删除占位符文件');
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('删除失败: ' + (e?.message || e));
  }
}

async function onDeleteTemplate() {
  if (!canDeleteTemplate.value) return;
  try {
    await ElMessageBox.confirm(
      `确认删除自建模板「${form.template_name}」？`,
      '确认',
      { type: 'warning' }
    );
    await deleteExportTemplate(form.template_id);
    await loadTemplates();
    form.template_id = null;
    onTemplateSelect(null);
    ElMessage.success('已删除');
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('删除失败: ' + (e?.message || e));
  }
}

// ============ 预览 + 下载 ============
// 把 useSystemStore.display 的 camelCase 转成后端 snake_case 的 display 字段
function buildDisplayPayload() {
  const d = systemStore.display || {};
  return {
    brand_name: d.brandName || null,
    app_name: d.appName || null,
    inspector_name: d.inspectorName || null,
    device_number: d.deviceNumber || null,
    factory_name: d.factoryName || null,
    line_name: d.lineName || null,
  };
}

function buildPayload(extra = {}) {
  const p = {
    template_content: form.template_content,
    template_id: null, // 始终用本地编辑的内容预览/下载，避免和 textarea 不同步
    filename_template: form.filename_template,
    fmt: form.fmt,
    license_payload: licensePayload.value || null,
    display_payload: buildDisplayPayload(),
    ...extra,
  };
  if (form.scope === 'cycle' && form.cycle_id) {
    p.cycle_id = form.cycle_id;
  } else if (form.scope === 'session' && form.session_id) {
    p.session_id = form.session_id;
  } else if (form.scope === 'range') {
    if (form.start_date) p.start_date = form.start_date;
    if (form.end_date) p.end_date = form.end_date;
    if (form.project_id) p.project_id = form.project_id;
    if (form.channel_id !== null && form.channel_id !== undefined) {
      p.channel_id = form.channel_id;
    }
    p.include_cycles = !!form.include_cycles;
  }
  // system 场景什么都不传
  return p;
}

async function doPreview() {
  previewing.value = true;
  previewError.value = null;
  try {
    const r = await previewExport(buildPayload({ include_context: showCtxJson.value }));
    const data = r.data || r;
    previewMeta.value = data;
    previewText.value = data.rendered || '';
    if (data.error) {
      previewError.value = data.error;
    }
  } catch (e) {
    previewError.value = e?.response?.data?.detail || e?.message || String(e);
  } finally {
    previewing.value = false;
  }
}

// 切换"上下文 JSON"开关时若无数据自动刷一次
watch(showCtxJson, (v) => {
  if (v && previewMeta.value && !previewMeta.value.context) {
    doPreview();
  }
});

async function doDownload() {
  if (!canDownload.value) return;
  downloading.value = true;
  try {
    const r = await renderExport(buildPayload());
    const filename = parseFilenameFromResponse(r, `export_${Date.now()}.${form.fmt}`);
    downloadBlob(r.data, filename);
    ElMessage.success(`已下载：${filename}`);
  } catch (e) {
    // axios 把 detail 包在 blob 里需要解一下
    let msg = e?.message || String(e);
    if (e?.response?.data instanceof Blob) {
      try {
        const text = await e.response.data.text();
        const j = JSON.parse(text);
        msg = j.detail || msg;
      } catch { /* noop */ }
    }
    ElMessage.error('下载失败: ' + msg);
  } finally {
    downloading.value = false;
  }
}

// Step 3.3: 从 Electron IPC 拿 license 信息（renderer 进程通过 contextBridge）
async function loadLicensePayload() {
  try {
    const api = (typeof window !== 'undefined') ? window.electronAPI : null;
    if (!api?.getLicenseStatus) return; // 浏览器开发模式，跳过
    const status = await api.getLicenseStatus();
    if (status?.valid && status?.info) {
      // 把 Electron 端 license info 映射到后端 license_payload 期望字段
      // 后端 _read_license_info 关心的 keys: customer/expires/machine_id/product/scope...
      const info = status.info || {};
      licensePayload.value = {
        valid: !!status.valid,
        machine_id: status.machineId || info.machineId || null,
        customer: info.customer || info.licensee || null,
        product: info.product || null,
        expires: info.expires || info.expireDate || null,
        scope: info.scope || null,
        version: info.version || null,
        issued_at: info.issuedAt || info.issued_at || null,
        ...info,  // 把所有原始字段也带上，模板里随意取
      };
    } else {
      licensePayload.value = null;
    }
  } catch (e) {
    console.warn('[CustomExportDialog] 读取 license 失败', e);
    licensePayload.value = null;
  }
}

// ============ 初始化 ============
watch(() => props.modelValue, (v) => {
  if (v) {
    form.scope = props.initialScope || 'system';
    form.cycle_id = props.initialCycleId || null;
    form.session_id = props.initialSessionId || null;
    if (props.initialDateRange?.length === 2) {
      rangePicker.value = props.initialDateRange;
    }
    loadTemplates();
    loadFields();
    loadLicensePayload();
  }
});

onMounted(() => {
  if (props.modelValue) {
    loadTemplates();
    loadFields();
    loadLicensePayload();
  }
});
</script>

<style scoped>
.custom-export-dialog :deep(.el-dialog__body) {
  padding: 16px 20px;
}

.panel-card {
  background: rgba(15, 23, 42, 0.6);
  border: 1px solid rgba(51, 65, 85, 0.5);
  border-radius: 8px;
}

.panel-header {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  font-weight: 600;
  color: #cbd5e1;
  padding-bottom: 8px;
  margin-bottom: 12px;
  border-bottom: 1px solid rgba(51, 65, 85, 0.5);
}

.field-tree {
  max-height: 320px;
  overflow-y: auto;
  padding-right: 4px;
}
.field-tree::-webkit-scrollbar { width: 6px; }
.field-tree::-webkit-scrollbar-thumb { background: rgba(100, 116, 139, 0.4); border-radius: 3px; }

.field-item {
  cursor: pointer;
  padding: 2px 4px;
  border-radius: 3px;
  transition: background 0.15s;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.field-item:hover {
  background: rgba(34, 211, 238, 0.1);
}

.mono-textarea :deep(textarea) {
  font-family: 'JetBrains Mono', 'Consolas', 'Monaco', monospace;
  font-size: 12px;
  line-height: 1.6;
}

.preview-error :deep(textarea) {
  color: #fca5a5;
  background: rgba(127, 29, 29, 0.15);
}

.ctx-json :deep(textarea) {
  color: #93c5fd;
  background: rgba(15, 23, 42, 0.85);
  font-size: 11px;
}

.quick-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
  padding: 6px 8px;
  background: rgba(15, 23, 42, 0.4);
  border: 1px solid rgba(51, 65, 85, 0.4);
  border-radius: 6px;
}
.quick-toolbar :deep(.el-button) {
  padding: 0 6px;
  height: 22px;
  font-size: 11px;
}

.field-item[draggable="true"] {
  cursor: grab;
}
.field-item[draggable="true"]:active {
  cursor: grabbing;
}

.textarea-drop-zone {
  position: relative;
  border-radius: 4px;
  transition: box-shadow 0.15s, outline 0.15s;
}
.textarea-drop-zone.is-drag-over {
  outline: 2px dashed #06b6d4;
  outline-offset: 2px;
  box-shadow: 0 0 0 4px rgba(6, 182, 212, 0.15);
}
.upload-template-zone :deep(.el-upload-dragger) {
  background: rgba(15, 23, 42, 0.4);
  border: 1px dashed rgba(51, 65, 85, 0.7);
  padding: 16px;
  height: auto;
  width: 100%;
}
.upload-template-zone :deep(.el-upload-dragger:hover) {
  border-color: #06b6d4;
  background: rgba(6, 182, 212, 0.08);
}

.drop-indicator {
  position: absolute;
  top: 8px;
  right: 8px;
  background: rgba(6, 182, 212, 0.9);
  color: #fff;
  padding: 4px 10px;
  border-radius: 16px;
  font-size: 11px;
  pointer-events: none;
  display: flex;
  align-items: center;
  gap: 4px;
}
</style>
