<!--
  v3.5.0 Step 4: 实时规则管理对话框

  功能：
  - 规则列表（启停、最近运行状态、累计统计）
  - 新建/编辑规则（绑定模板、输出目录、文件名模板、input_file_mode、过滤器、编码换行）
  - 测试触发（指定 cycle_id 或走系统上下文）
  - 运行日志查看（按规则 + 全局两个 Tab）
-->
<template>
  <el-dialog
    v-model="visible"
    title="实时导出规则"
    width="92%"
    top="3vh"
    :close-on-click-modal="false"
    destroy-on-close
    class="dark-dialog rt-rules-dialog"
  >
    <el-tabs v-model="activeTab" class="rt-tabs">
      <!-- ========== Tab 1: 规则列表 ========== -->
      <el-tab-pane label="规则列表" name="rules">
        <div class="flex justify-between items-center mb-3">
          <div class="text-xs text-gray-400">
            cycle_end / session_end 后自动触发的规则。可绑定任意模板、输出到本地或网络路径，支持按通道/项目过滤。
          </div>
          <el-button type="primary" size="small" @click="onCreate">
            <el-icon class="mr-1"><Plus /></el-icon>新建规则
          </el-button>
        </div>

        <el-table :data="rules" size="small" class="dark-table" empty-text="暂无规则，点上方按钮新建一条">
          <el-table-column type="index" width="40" />
          <el-table-column prop="name" label="名称" min-width="140">
            <template #default="{ row }">
              <div class="flex items-center gap-2">
                <el-switch
                  :model-value="row.enabled"
                  size="small"
                  @change="onToggle(row)"
                />
                <span :class="{ 'text-gray-500': !row.enabled }">{{ row.name }}</span>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="模板" min-width="160">
            <template #default="{ row }">
              <el-tag size="small" :type="row.template?.is_system ? 'info' : 'success'">
                {{ row.template?.format || '?' }}
              </el-tag>
              <span class="ml-2">{{ row.template?.name || `#${row.template_id}` }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="trigger_event" label="触发" width="100">
            <template #default="{ row }">
              <el-tag size="small" type="warning">{{ row.trigger_event }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="input_file_mode" label="输入模式" width="110">
            <template #default="{ row }">
              <span class="text-xs">{{ inputModeLabel(row.input_file_mode) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="输出" min-width="180">
            <template #default="{ row }">
              <div class="font-mono text-[11px]" :title="row.output_dir">
                <div class="text-cyan-400 truncate">{{ row.output_dir }}</div>
                <div class="text-gray-500 truncate">{{ row.filename_template }}</div>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="过滤" width="120">
            <template #default="{ row }">
              <div class="text-[11px]">
                <div v-if="row.channel_filter">通道: {{ row.channel_filter.join(',') }}</div>
                <div v-if="row.project_filter">项目: {{ row.project_filter.join(',') }}</div>
                <div v-if="!row.channel_filter && !row.project_filter" class="text-gray-500">全部</div>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="最近" width="140">
            <template #default="{ row }">
              <div class="text-[11px]">
                <el-tag v-if="row.last_run_status === 'success'" size="small" type="success">success</el-tag>
                <el-tag v-else-if="row.last_run_status === 'failed'" size="small" type="danger">failed</el-tag>
                <el-tag v-else-if="row.last_run_status === 'skipped'" size="small" type="info">skipped</el-tag>
                <span v-else class="text-gray-500">未运行</span>
                <div class="text-gray-500 mt-0.5" v-if="row.last_run_time">
                  {{ formatTime(row.last_run_time) }}
                </div>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="累计" width="130">
            <template #default="{ row }">
              <div class="text-[11px] font-mono">
                <span class="text-emerald-400" title="成功">✓ {{ row.success_count }}</span> ·
                <span class="text-red-400" title="失败">✗ {{ row.failed_count }}</span> ·
                <span class="text-gray-400" title="跳过">- {{ row.skipped_count }}</span>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="200" fixed="right">
            <template #default="{ row }">
              <el-button size="small" link type="primary" @click="onEdit(row)">编辑</el-button>
              <el-button size="small" link type="warning" @click="onTestRun(row)">测试</el-button>
              <el-button size="small" link type="info" @click="onViewLogs(row)">日志</el-button>
              <el-button size="small" link type="danger" @click="onDelete(row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- ========== Tab 2: 全局运行日志 ========== -->
      <el-tab-pane label="运行日志" name="logs">
        <div class="flex justify-between items-center mb-3">
          <div class="flex gap-2 items-center">
            <el-select v-model="logFilter.status" size="small" placeholder="状态" clearable style="width: 110px" @change="loadLogs">
              <el-option label="success" value="success" />
              <el-option label="failed" value="failed" />
              <el-option label="skipped" value="skipped" />
            </el-select>
            <el-select v-model="logFilter.source_type" size="small" placeholder="来源" clearable style="width: 130px" @change="loadLogs">
              <el-option label="realtime" value="realtime" />
              <el-option label="manual_test" value="manual_test" />
              <el-option label="batch" value="batch" />
            </el-select>
            <el-input v-model.number="logFilter.cycle_id" size="small" placeholder="cycle_id" type="number" clearable style="width: 130px" @change="loadLogs" />
            <el-button size="small" @click="loadLogs">
              <el-icon class="mr-1"><Refresh /></el-icon>刷新
            </el-button>
          </div>
          <span class="text-xs text-gray-500">总计 {{ logTotal }} 条</span>
        </div>
        <el-table :data="logs" size="small" class="dark-table" max-height="60vh">
          <el-table-column prop="triggered_at" label="时间" width="170">
            <template #default="{ row }">{{ formatTime(row.triggered_at) }}</template>
          </el-table-column>
          <el-table-column prop="rule_id" label="规则" width="100">
            <template #default="{ row }">
              {{ ruleNameById(row.rule_id) || `#${row.rule_id}` }}
            </template>
          </el-table-column>
          <el-table-column prop="source_type" label="来源" width="100">
            <template #default="{ row }">
              <el-tag size="small" :type="sourceTypeColor(row.source_type)">{{ row.source_type }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="status" label="状态" width="90">
            <template #default="{ row }">
              <el-tag size="small" :type="statusColor(row.status)">{{ row.status }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="cycle_id" label="cycle" width="80" />
          <el-table-column prop="duration_ms" label="耗时" width="80">
            <template #default="{ row }">{{ row.duration_ms }}ms</template>
          </el-table-column>
          <el-table-column prop="output_file" label="输出文件" min-width="200">
            <template #default="{ row }">
              <span class="font-mono text-[11px] text-cyan-400" :title="row.output_file">
                {{ row.output_file || '-' }}
              </span>
              <span v-if="row.file_size" class="text-gray-500 text-[10px] ml-1">({{ formatSize(row.file_size) }})</span>
            </template>
          </el-table-column>
          <el-table-column label="错误/原因" min-width="240">
            <template #default="{ row }">
              <span v-if="row.error_msg" class="text-red-400 text-[11px]" :title="row.error_msg">
                {{ truncate(row.error_msg, 80) }}
              </span>
              <span v-else-if="row.skip_reason" class="text-gray-500 text-[11px]">
                {{ row.skip_reason }}
              </span>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>
    </el-tabs>

    <!-- ========== 编辑/新建子对话框 ========== -->
    <el-dialog
      v-model="editorVisible"
      :title="editing.id ? `编辑规则 #${editing.id}` : '新建实时规则'"
      width="780px"
      :close-on-click-modal="false"
      destroy-on-close
      class="dark-dialog"
      append-to-body
    >
      <el-form label-position="top" :model="editing" class="text-sm">
        <el-row :gutter="12">
          <el-col :span="12">
            <el-form-item label="规则名称" required>
              <el-input v-model="editing.name" placeholder="如：客户A_SN.txt自动写入" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="启用">
              <el-switch v-model="editing.enabled" />
              <span class="ml-2 text-xs text-gray-500">关闭后 cycle_end 会跳过此规则</span>
            </el-form-item>
          </el-col>
        </el-row>

        <el-form-item label="说明（可选）">
          <el-input v-model="editing.description" type="textarea" :rows="2" />
        </el-form-item>

        <el-row :gutter="12">
          <el-col :span="12">
            <el-form-item label="绑定模板" required>
              <el-select v-model="editing.template_id" placeholder="选模板" class="w-full"
                          filterable @change="onTemplateChange">
                <el-option
                  v-for="t in templates"
                  :key="t.id"
                  :label="`[${t.format}] ${t.name}${t.is_system ? ' (系统)' : ''}${t.default_rule_config ? ' ★' : ''}`"
                  :value="t.id"
                />
              </el-select>
              <div v-if="appliedTemplateHint" class="text-xs text-cyan-400 mt-1">
                {{ appliedTemplateHint }}
              </div>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="触发事件">
              <el-select v-model="editing.trigger_event" class="w-full">
                <el-option label="cycle_end (每周期结束，单工件文件)" value="cycle_end" />
                <el-option label="session_end (会话结束，整次开机汇总)" value="session_end" />
                <el-option label="box_complete (装箱完成 - 暂未实现)" value="box_complete" disabled />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>

        <el-form-item label="输出目录" required>
          <el-input v-model="editing.output_dir" :placeholder="osPathPlaceholder" />
          <div class="text-xs text-gray-500 mt-1">
            绝对路径或相对 DATA_DIR。Windows 路径用反斜杠转义：D:\\sn_out
          </div>
        </el-form-item>

        <el-form-item label="文件名模板">
          <el-input v-model="editing.filename_template" :placeholder="filenamePlaceholder" />
          <div class="text-xs text-gray-500 mt-1">
            Jinja2 模板。常用：<code>&#123;&#123; workpiece.serial_no &#125;&#125;.txt</code>、<code>&#123;&#123; cycle.id &#125;&#125;.csv</code>
          </div>
        </el-form-item>

        <!-- 输入文件改写模式 -->
        <el-form-item label="输入文件改写模式">
          <el-radio-group v-model="editing.input_file_mode">
            <el-radio value="none">none — 直接生成</el-radio>
            <el-radio value="read_template">read_template — 读输入做模板</el-radio>
            <el-radio value="append">append — 追加到输入文件</el-radio>
          </el-radio-group>
        </el-form-item>

        <el-form-item v-if="editing.input_file_mode !== 'none' || needsInputDirForStrategy" label="输入目录" :required="editing.input_file_mode !== 'none' || needsInputDirForStrategy">
          <el-input v-model="editing.input_dir" :placeholder="osInputPlaceholder" />
          <div class="text-xs text-gray-500 mt-1">
            实际输入文件路径 = input_dir + 文件名模板渲染结果。客户 SN 流程：扫码 SN12345 → 读 input_dir/SN12345.txt 作模板 → 写入 output_dir/SN12345.txt。
            <br/>
            <span class="text-cyan-400">扫码器旁路场景：</span>下方「取文件策略」启用后，input_dir 即为客户扫码器生成 txt 的目录，模板里 <code>&#123;&#123; latest_input_text() &#125;&#125;</code> 会自动读取该目录最新文件。
          </div>
        </el-form-item>

        <!-- v3.7.2 扫码器旁路 — 取「输入目录最新文件」的策略 -->
        <el-divider content-position="left">
          <span class="text-xs text-cyan-400">扫码器旁路：取最新文件策略</span>
        </el-divider>

        <el-form-item label="取文件策略">
          <el-radio-group v-model="editing.latest_file_strategy">
            <el-radio value="cycle_start_snapshot">
              C · 周期开始锁快照（推荐）
            </el-radio>
            <el-radio value="mtime_stable">
              B · 取最新 + 等稳定 + 限制年龄
            </el-radio>
            <el-radio value="mtime">
              A · 直接取 mtime 最新（最简）
            </el-radio>
          </el-radio-group>
          <div class="text-xs text-gray-500 mt-1">
            <b>C 推荐</b>：周期开始那一刻就锁住扫码 txt 内容写到 DB，结束渲染时直接用，最稳。<br/>
            <b>B</b>：结束时翻文件夹但加保险（避免读半截 / 限制最大年龄）。<br/>
            <b>A</b>：结束时翻文件夹按 mtime 最新取，调试用。
          </div>
        </el-form-item>

        <el-row v-if="editing.latest_file_strategy !== 'mtime'" :gutter="12">
          <el-col :span="12">
            <el-form-item label="稳定等待 (毫秒)">
              <el-input-number v-model="editing.latest_file_wait_stable_ms"
                :min="0" :max="5000" :step="50" class="w-full" />
              <div class="text-xs text-gray-500 mt-1">
                读两次中间等 N 毫秒，size+mtime 一致才采用。0 = 不等。建议 100ms。
              </div>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="最大年龄 (秒)">
              <el-input-number v-model="editing.latest_file_max_age_sec"
                :min="0" :max="3600" :step="5" class="w-full" />
              <div class="text-xs text-gray-500 mt-1">
                只接受 mtime 在 N 秒内的文件。0 = 不限。建议 60s。
              </div>
            </el-form-item>
          </el-col>
        </el-row>

        <el-form-item label="同名去重">
          <el-switch v-model="editing.dedupe_same_filename" />
          <span class="text-xs text-gray-500 ml-2">
            开启后：本轮取到的文件名 = 上一轮用过的 → 异步等待新文件，超时跳过本规则
          </span>
        </el-form-item>

        <el-row v-if="editing.dedupe_same_filename" :gutter="12">
          <el-col :span="12">
            <el-form-item label="重试最大等待 (秒)">
              <el-input-number v-model="editing.dedupe_retry_max_sec"
                :min="1" :max="60" :step="1" class="w-full" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="轮询间隔 (毫秒)">
              <el-input-number v-model="editing.dedupe_retry_interval_ms"
                :min="50" :max="2000" :step="50" class="w-full" />
            </el-form-item>
          </el-col>
        </el-row>

        <el-divider />


        <el-row :gutter="12">
          <el-col :span="12">
            <el-form-item label="通道过滤（留空=全部）">
              <el-input
                :model-value="(editing.channel_filter || []).join(',')"
                placeholder="如 0,1（多通道时只对 ch0/1 触发）"
                @input="onChannelFilterInput"
              />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="项目过滤（留空=全部）">
              <el-input
                :model-value="(editing.project_filter || []).join(',')"
                placeholder="项目 ID 列表，如 1,3"
                @input="onProjectFilterInput"
              />
            </el-form-item>
          </el-col>
        </el-row>

        <el-row :gutter="12">
          <el-col :span="8">
            <el-form-item label="覆盖策略">
              <el-select v-model="editing.overwrite_policy" class="w-full">
                <el-option label="overwrite — 覆盖" value="overwrite" />
                <el-option label="rename — 加时间戳后缀" value="rename" />
                <el-option label="skip — 已存在则跳过" value="skip" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="编码">
              <el-select v-model="editing.encoding" class="w-full">
                <el-option label="utf-8" value="utf-8" />
                <el-option label="utf-8-sig (Excel)" value="utf-8-sig" />
                <el-option label="gbk" value="gbk" />
                <el-option label="ascii" value="ascii" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="换行符">
              <el-select v-model="editing.newline" class="w-full">
                <el-option label="lf (Linux/Mac)" value="lf" />
                <el-option label="crlf (Windows)" value="crlf" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>
      </el-form>

      <template #footer>
        <el-button @click="editorVisible = false">取消</el-button>
        <el-button type="primary" @click="onSaveEdit" :loading="saving">
          {{ editing.id ? '保存' : '创建' }}
        </el-button>
      </template>
    </el-dialog>

    <!-- ========== 测试触发对话框 ========== -->
    <el-dialog
      v-model="testRunVisible"
      title="测试触发"
      width="520px"
      destroy-on-close
      class="dark-dialog"
      append-to-body
    >
      <el-form label-position="top" v-if="testTarget">
        <el-form-item label="目标规则">
          <span class="text-cyan-400">#{{ testTarget.id }} {{ testTarget.name }}</span>
        </el-form-item>
        <el-form-item label="测试上下文">
          <el-radio-group v-model="testRunForm.scope">
            <el-radio value="cycle">指定 cycle_id（用真实数据）</el-radio>
            <el-radio value="system">系统级（无 cycle 数据，所有 cycle.* 字段空）</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="testRunForm.scope === 'cycle'" label="Cycle ID">
          <el-input-number v-model="testRunForm.cycle_id" :min="1" :precision="0" class="w-full" />
        </el-form-item>
        <div class="text-xs text-gray-500">
          测试触发的日志会以 <code>source_type=manual_test</code> 写入，不影响 success/failed 累计统计。
        </div>
      </el-form>
      <template #footer>
        <el-button @click="testRunVisible = false">取消</el-button>
        <el-button type="warning" @click="onConfirmTestRun" :loading="testing">立即触发</el-button>
      </template>
    </el-dialog>

    <!-- ========== 单规则日志查看 ========== -->
    <el-dialog
      v-model="ruleLogVisible"
      :title="`规则 #${ruleLogTarget?.id} ${ruleLogTarget?.name} - 运行日志`"
      width="80%"
      destroy-on-close
      class="dark-dialog"
      append-to-body
    >
      <el-table :data="ruleLogList" size="small" max-height="60vh" class="dark-table">
        <el-table-column prop="triggered_at" label="时间" width="170">
          <template #default="{ row }">{{ formatTime(row.triggered_at) }}</template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="statusColor(row.status)">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="source_type" label="来源" width="110" />
        <el-table-column prop="cycle_id" label="cycle" width="80" />
        <el-table-column prop="duration_ms" label="耗时" width="80">
          <template #default="{ row }">{{ row.duration_ms }}ms</template>
        </el-table-column>
        <el-table-column prop="output_file" label="输出" min-width="180">
          <template #default="{ row }">
            <code class="text-cyan-400 text-[11px]">{{ row.output_file || '-' }}</code>
          </template>
        </el-table-column>
        <el-table-column label="错误/原因" min-width="240">
          <template #default="{ row }">
            <span v-if="row.error_msg" class="text-red-400 text-[11px]">{{ truncate(row.error_msg, 100) }}</span>
            <span v-else-if="row.skip_reason" class="text-gray-500 text-[11px]">{{ row.skip_reason }}</span>
          </template>
        </el-table-column>
      </el-table>
    </el-dialog>

    <template #footer>
      <div class="flex justify-between items-center">
        <span class="text-xs text-gray-500">
          v3.5.0 · 共 {{ rules.length }} 条规则 · 启用 {{ enabledCount }} 条
        </span>
        <el-button @click="visible = false">关闭</el-button>
      </div>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, reactive, computed, watch } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { Plus, Refresh } from '@element-plus/icons-vue';
import {
  listExportTemplates,
  listRealtimeRules,
  createRealtimeRule,
  updateRealtimeRule,
  deleteRealtimeRule,
  toggleRealtimeRule,
  testRunRealtimeRule,
  listRuleLogs,
  listAllRunLogs,
} from '@/api/export';

const props = defineProps({
  modelValue: { type: Boolean, default: false },
});
const emit = defineEmits(['update:modelValue']);
const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
});

// 占位符里有字面 {{ }} - 通过 JS 拼接绕开 Vue 插值解析
const _LB = '{' + '{', _RB = '}' + '}';
const filenamePlaceholder = `如 ${_LB} workpiece.serial_no or cycle.id ${_RB}.txt`;
const osPathPlaceholder = `D:\\sn_out 或 /var/lib/tianjun/sn_out`;
const osInputPlaceholder = `D:\\sn_input 或 /var/lib/tianjun/sn_input`;

const activeTab = ref('rules');
const rules = ref([]);
const templates = ref([]);
const enabledCount = computed(() => rules.value.filter(r => r.enabled).length);

// 编辑表单
const editorVisible = ref(false);
const editing = reactive(_emptyRule());
const saving = ref(false);

function _emptyRule() {
  return {
    id: null,
    name: '',
    enabled: true,
    description: '',
    template_id: null,
    output_dir: '',
    filename_template: '{{ workpiece.serial_no or cycle.id }}.txt',
    input_file_mode: 'none',
    input_dir: '',
    trigger_event: 'cycle_end',
    channel_filter: null,
    project_filter: null,
    overwrite_policy: 'overwrite',
    encoding: 'utf-8',
    newline: 'lf',
    // v3.7.2 扫码器旁路
    latest_file_strategy: 'cycle_start_snapshot',
    latest_file_wait_stable_ms: 100,
    latest_file_max_age_sec: 0,
    dedupe_same_filename: false,
    dedupe_retry_max_sec: 5,
    dedupe_retry_interval_ms: 100,
  };
}

// v3.7.2: 取文件策略需要 input_dir 来定位扫码器输入目录
const needsInputDirForStrategy = computed(() => {
  return ['cycle_start_snapshot', 'mtime_stable', 'mtime'].includes(
    editing.latest_file_strategy || ''
  );
});

// v3.7.2: 选模板后, 若模板带 default_rule_config 就自动合并到 editing
// (仅对客户没改过的字段, 避免覆盖客户已经填的内容)
const appliedTemplateHint = ref('');

// 哪些字段允许"自动从模板的 default_rule_config 填进来"
// input_dir / output_dir / channel_filter / project_filter / name 这些是场景相关的,
// 不在自动填范围内 - 客户必须自己填.
const _AUTO_FILL_KEYS = [
  'trigger_event', 'input_file_mode', 'filename_template',
  'encoding', 'newline', 'overwrite_policy',
  'latest_file_strategy', 'latest_file_wait_stable_ms', 'latest_file_max_age_sec',
  'dedupe_same_filename', 'dedupe_retry_max_sec', 'dedupe_retry_interval_ms',
];

// 一个字段算"客户已改过"的判断 — 跟 _emptyRule 的默认值不同 = 已改
function _isFieldUntouched(key) {
  const tpl = _emptyRule()[key];
  const cur = editing[key];
  return tpl === cur || (tpl == null && cur == null);
}

function onTemplateChange(tplId) {
  appliedTemplateHint.value = '';
  if (!tplId) return;
  const tpl = templates.value.find(t => t.id === tplId);
  if (!tpl) return;
  const cfg = tpl.default_rule_config;
  if (!cfg || typeof cfg !== 'object') return;

  // 只填客户没改过的字段
  const applied = [];
  const skipped = [];
  for (const k of _AUTO_FILL_KEYS) {
    if (!(k in cfg)) continue;
    if (_isFieldUntouched(k)) {
      editing[k] = cfg[k];
      applied.push(k);
    } else {
      skipped.push(k);
    }
  }
  if (applied.length === 0) return;
  appliedTemplateHint.value =
    `★ 已自动应用模板「${tpl.name}」的推荐配置 (${applied.length} 项) ` +
    (skipped.length ? `, ${skipped.length} 项已被你修改未覆盖` : '');
}

// ============ 加载 ============
async function loadRules() {
  try {
    const r = await listRealtimeRules();
    rules.value = r.data?.items || r.items || [];
  } catch (e) {
    ElMessage.error('加载规则失败: ' + (e?.message || e));
  }
}

async function loadTemplates() {
  try {
    const r = await listExportTemplates();
    templates.value = r.data?.items || r.items || [];
  } catch (e) {
    ElMessage.error('加载模板失败: ' + (e?.message || e));
  }
}

// ============ CRUD ============
function onCreate() {
  Object.assign(editing, _emptyRule());
  editorVisible.value = true;
}

function onEdit(row) {
  Object.assign(editing, {
    ...row,
    description: row.description || '',
    input_dir: row.input_dir || '',
  });
  editorVisible.value = true;
}

function onChannelFilterInput(val) {
  const arr = (val || '').split(',').map(s => s.trim()).filter(Boolean).map(Number).filter(n => !isNaN(n));
  editing.channel_filter = arr.length ? arr : null;
}
function onProjectFilterInput(val) {
  const arr = (val || '').split(',').map(s => s.trim()).filter(Boolean).map(Number).filter(n => !isNaN(n));
  editing.project_filter = arr.length ? arr : null;
}

async function onSaveEdit() {
  if (!editing.name?.trim()) {
    ElMessage.warning('请填写规则名称');
    return;
  }
  if (!editing.template_id) {
    ElMessage.warning('请选择绑定模板');
    return;
  }
  if (!editing.output_dir?.trim()) {
    ElMessage.warning('请填写输出目录');
    return;
  }
  if (editing.input_file_mode !== 'none' && !editing.input_dir?.trim()) {
    ElMessage.warning('input_file_mode 不为 none 时必须填写输入目录');
    return;
  }

  saving.value = true;
  try {
    const payload = { ...editing };
    delete payload.id;
    delete payload.template;
    delete payload.last_run_time;
    delete payload.last_run_status;
    delete payload.last_run_error;
    delete payload.last_output_file;
    delete payload.success_count;
    delete payload.failed_count;
    delete payload.skipped_count;
    delete payload.created_at;
    delete payload.updated_at;

    if (editing.id) {
      await updateRealtimeRule(editing.id, payload);
      ElMessage.success('已保存');
    } else {
      await createRealtimeRule(payload);
      ElMessage.success('已创建');
    }
    editorVisible.value = false;
    await loadRules();
  } catch (e) {
    ElMessage.error('保存失败: ' + (e?.response?.data?.detail || e?.message || e));
  } finally {
    saving.value = false;
  }
}

async function onToggle(row) {
  try {
    await toggleRealtimeRule(row.id);
    await loadRules();
  } catch (e) {
    ElMessage.error('切换失败: ' + (e?.message || e));
  }
}

async function onDelete(row) {
  try {
    await ElMessageBox.confirm(
      `确认删除规则「${row.name}」？关联的运行日志会保留但 rule_id 置空。`,
      '确认删除',
      { type: 'warning' }
    );
    await deleteRealtimeRule(row.id);
    await loadRules();
    ElMessage.success('已删除');
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('删除失败: ' + (e?.message || e));
  }
}

// ============ 测试触发 ============
const testRunVisible = ref(false);
const testTarget = ref(null);
const testing = ref(false);
const testRunForm = reactive({ scope: 'system', cycle_id: null });

function onTestRun(row) {
  testTarget.value = row;
  testRunForm.scope = 'system';
  testRunForm.cycle_id = null;
  testRunVisible.value = true;
}

async function onConfirmTestRun() {
  if (!testTarget.value) return;
  testing.value = true;
  try {
    const payload = {};
    if (testRunForm.scope === 'cycle' && testRunForm.cycle_id) {
      payload.cycle_id = testRunForm.cycle_id;
    }
    const r = await testRunRealtimeRule(testTarget.value.id, payload);
    const data = r.data || r;
    if (data.status === 'success') {
      ElMessage.success(`触发成功，文件已写入: ${data.output_path}`);
    } else if (data.status === 'skipped') {
      ElMessage.warning(`已跳过: ${data.skip_reason || data.error}`);
    } else {
      ElMessage.error(`失败: ${data.error_msg || data.error}`);
    }
    testRunVisible.value = false;
    await loadRules();
    if (activeTab.value === 'logs') await loadLogs();
  } catch (e) {
    ElMessage.error('触发失败: ' + (e?.response?.data?.detail || e?.message || e));
  } finally {
    testing.value = false;
  }
}

// ============ 日志 ============
const logs = ref([]);
const logTotal = ref(0);
const logFilter = reactive({ status: null, source_type: null, cycle_id: null });

async function loadLogs() {
  try {
    const params = {};
    if (logFilter.status) params.status = logFilter.status;
    if (logFilter.source_type) params.source_type = logFilter.source_type;
    if (logFilter.cycle_id) params.cycle_id = logFilter.cycle_id;
    const r = await listAllRunLogs(params);
    const data = r.data || r;
    logs.value = data.items || [];
    logTotal.value = data.total || 0;
  } catch (e) {
    ElMessage.error('加载日志失败: ' + (e?.message || e));
  }
}

watch(activeTab, (v) => {
  if (v === 'logs') loadLogs();
});

// 单规则日志
const ruleLogVisible = ref(false);
const ruleLogTarget = ref(null);
const ruleLogList = ref([]);

async function onViewLogs(row) {
  ruleLogTarget.value = row;
  ruleLogVisible.value = true;
  try {
    const r = await listRuleLogs(row.id, { limit: 100 });
    ruleLogList.value = (r.data || r).items || [];
  } catch (e) {
    ElMessage.error('加载日志失败: ' + (e?.message || e));
  }
}

// ============ 工具 ============
function formatTime(s) {
  if (!s) return '-';
  return new Date(s).toLocaleString('zh-CN', { hour12: false });
}
function formatSize(b) {
  if (!b) return '0';
  if (b < 1024) return `${b}B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)}KB`;
  return `${(b / 1024 / 1024).toFixed(1)}MB`;
}
function truncate(s, n) {
  return s && s.length > n ? s.slice(0, n) + '...' : s || '';
}
function inputModeLabel(m) {
  return { none: '直接生成', read_template: '读模板', append: '追加' }[m] || m;
}
function statusColor(s) {
  return { success: 'success', failed: 'danger', skipped: 'info' }[s] || '';
}
function sourceTypeColor(s) {
  return { realtime: 'success', manual_test: 'warning', batch: 'info' }[s] || '';
}
function ruleNameById(id) {
  return rules.value.find(r => r.id === id)?.name;
}

// ============ 初始化 ============
watch(() => props.modelValue, (v) => {
  if (v) {
    loadRules();
    loadTemplates();
    if (activeTab.value === 'logs') loadLogs();
  }
});
</script>

<style scoped>
.rt-rules-dialog :deep(.el-dialog__body) {
  padding: 12px 20px;
}

.dark-table {
  background: transparent !important;
}
.dark-table :deep(.el-table__inner-wrapper) {
  background: rgba(15, 23, 42, 0.4);
}
.dark-table :deep(th.el-table__cell) {
  background: rgba(15, 23, 42, 0.7) !important;
}
.dark-table :deep(td.el-table__cell) {
  background: rgba(30, 41, 59, 0.4) !important;
  border-bottom-color: rgba(51, 65, 85, 0.5) !important;
}

.rt-tabs :deep(.el-tabs__nav) {
  border-color: rgba(51, 65, 85, 0.6);
}
</style>
