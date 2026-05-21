<!--
  v3.8.x 定时导出规则管理对话框

  特点:
  - cron 表达式编辑器 (简化模式: 每天/每周/每小时几点 ↔ 高级模式: 原始 cron)
  - 数据窗口预设 (昨天 / 今天 / 过去 N 小时 / 过去 N 天 / 白班 / 夜班 / 自定义)
  - 数据窗口的"早晚班 / 跨日截断"逻辑跟数据页 4 个手动按钮完全一致
  - 输出格式 csv/txt/xlsx/docx/pdf
  - 标准日报列 (默认) 或 走自定义模板
  - 输出目录 (空则走全局默认)
  - 测试触发 / 启停 / 删除
-->
<template>
  <el-dialog
    v-model="visible"
    title="定时导出规则"
    width="92%"
    top="3vh"
    :close-on-click-modal="false"
    destroy-on-close
    class="dark-dialog sched-rules-dialog"
  >
    <el-tabs v-model="activeTab" class="sched-tabs">
      <!-- ========== Tab 1: 规则列表 ========== -->
      <el-tab-pane label="规则列表" name="rules">
        <div class="flex justify-between items-center mb-3">
          <div class="text-xs text-gray-400">
            按 cron 周期性自动把"标准日报"或自定义模板写到指定目录。早晚班 / 跨日截断逻辑跟手动 4 个按钮一致。
          </div>
          <el-button type="primary" size="small" @click="onCreate">
            <el-icon class="mr-1"><Plus /></el-icon>新建规则
          </el-button>
        </div>

        <el-table :data="rules" size="small" class="dark-table" empty-text="暂无规则, 点上方按钮新建一条">
          <el-table-column type="index" width="40" />
          <el-table-column prop="name" label="名称" min-width="140">
            <template #default="{ row }">
              <div class="flex items-center gap-2">
                <el-switch :model-value="row.enabled" size="small" @change="onToggle(row)" />
                <span :class="{ 'text-gray-500': !row.enabled }">{{ row.name }}</span>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="触发时机" min-width="200">
            <template #default="{ row }">
              <div class="text-[11px]">
                <code class="text-cyan-400">{{ row.cron_expression }}</code>
                <div class="text-gray-500 mt-0.5">{{ describeCron(row.cron_expression) }}</div>
                <div v-if="row.next_run_time" class="text-orange-400 mt-0.5">
                  下次: {{ formatTime(row.next_run_time) }}
                </div>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="数据窗口" width="130">
            <template #default="{ row }">
              <span class="text-[11px]">{{ windowLabel(row.data_window_type) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="输出" min-width="200">
            <template #default="{ row }">
              <div class="font-mono text-[11px]">
                <el-tag size="small" :type="formatTagType(row.output_format)">
                  {{ row.output_format.toUpperCase() }}
                </el-tag>
                <span class="ml-2 text-cyan-400" :title="row.output_dir || '使用全局默认目录'">
                  {{ row.output_dir || '<全局默认>' }}
                </span>
                <div class="text-gray-500 truncate mt-0.5">{{ row.filename_template }}</div>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="模板" width="120">
            <template #default="{ row }">
              <el-tag v-if="row.use_standard_daily_report" size="small" type="info">标准日报</el-tag>
              <el-tag v-else size="small" type="success">自定义模板#{{ row.template_id }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="最近" width="140">
            <template #default="{ row }">
              <div class="text-[11px]">
                <el-tag v-if="row.last_run_status === 'success'" size="small" type="success">success</el-tag>
                <el-tag v-else-if="row.last_run_status === 'failed'" size="small" type="danger">failed</el-tag>
                <el-tag v-else-if="row.last_run_status === 'skipped'" size="small" type="info">skipped</el-tag>
                <el-tag v-else-if="row.last_run_status === 'no_data'" size="small" type="warning">no_data</el-tag>
                <span v-else class="text-gray-500">未运行</span>
                <div class="text-gray-500 mt-0.5" v-if="row.last_run_time">
                  {{ formatTime(row.last_run_time) }}
                </div>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="累计" width="120">
            <template #default="{ row }">
              <div class="text-[11px] font-mono">
                <span class="text-emerald-400" title="成功">✓ {{ row.success_count }}</span> ·
                <span class="text-red-400" title="失败">✗ {{ row.failed_count }}</span> ·
                <span class="text-gray-400" title="跳过">- {{ row.skipped_count }}</span>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="240" fixed="right">
            <template #default="{ row }">
              <el-button size="small" type="primary" link @click="onEdit(row)">编辑</el-button>
              <el-button size="small" type="warning" link @click="onTestRun(row)">立即测试</el-button>
              <el-button size="small" type="info" link @click="onViewLogs(row)">日志</el-button>
              <el-button size="small" type="danger" link @click="onDelete(row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- ========== Tab 2: 全局默认输出目录 ========== -->
      <el-tab-pane label="全局默认目录" name="defaults">
        <div class="text-xs text-gray-400 mb-3">
          规则没单独填输出目录时, 走这个目录。建议填客户工厂网络路径 (如 \\\\NAS\\日报\\)。
        </div>
        <el-form label-width="120px">
          <el-form-item label="默认输出目录">
            <div class="flex gap-2 w-full">
              <el-input v-model="defaultDir" placeholder="/data/exports/scheduled 或 D:\\日报\\" />
              <el-button type="primary" @click="onSaveDefaultDir">保存</el-button>
            </div>
            <div class="text-[11px] text-gray-500 mt-1">
              当前: <code class="text-cyan-400">{{ defaultDirCurrent || '(未设置, 默认走 DATA_DIR/exports/scheduled)' }}</code>
            </div>
          </el-form-item>
        </el-form>
      </el-tab-pane>
    </el-tabs>

    <!-- ========== 编辑规则对话框 ========== -->
    <el-dialog
      v-model="editVisible"
      :title="editForm.id ? `编辑规则 #${editForm.id}` : '新建定时导出规则'"
      width="800px"
      top="5vh"
      :close-on-click-modal="false"
      append-to-body
      destroy-on-close
      class="dark-dialog"
    >
      <el-form :model="editForm" label-width="130px" size="small">
        <el-form-item label="规则名称" required>
          <el-input v-model="editForm.name" placeholder="如: 每日凌晨日报" />
        </el-form-item>

        <el-form-item label="启用">
          <el-switch v-model="editForm.enabled" />
        </el-form-item>

        <el-divider><span class="text-xs text-cyan-400">触发时机</span></el-divider>

        <el-form-item label="模式">
          <el-radio-group v-model="cronMode" @change="onCronModeChange">
            <el-radio-button label="daily">每天某时</el-radio-button>
            <el-radio-button label="weekly">每周某天某时</el-radio-button>
            <el-radio-button label="hourly">每小时某分</el-radio-button>
            <el-radio-button label="custom">高级 cron</el-radio-button>
          </el-radio-group>
        </el-form-item>

        <el-form-item v-if="cronMode === 'daily'" label="每天">
          <el-time-picker v-model="dailyTime" format="HH:mm" value-format="HH:mm" @change="onSimpleChange" />
        </el-form-item>

        <el-form-item v-else-if="cronMode === 'weekly'" label="每周">
          <div class="flex gap-2">
            <el-select v-model="weeklyDay" class="w-32" @change="onSimpleChange">
              <el-option label="周一" :value="1" />
              <el-option label="周二" :value="2" />
              <el-option label="周三" :value="3" />
              <el-option label="周四" :value="4" />
              <el-option label="周五" :value="5" />
              <el-option label="周六" :value="6" />
              <el-option label="周日" :value="0" />
            </el-select>
            <el-time-picker v-model="weeklyTime" format="HH:mm" value-format="HH:mm" @change="onSimpleChange" />
          </div>
        </el-form-item>

        <el-form-item v-else-if="cronMode === 'hourly'" label="每小时">
          <div class="flex items-center gap-2">
            第
            <el-input-number v-model="hourlyMinute" :min="0" :max="59" controls-position="right" class="w-24" @change="onSimpleChange" />
            分
          </div>
        </el-form-item>

        <el-form-item label="cron 表达式" required>
          <el-input v-model="editForm.cron_expression" placeholder="0 0 * * *" />
          <div class="text-[11px] text-gray-500 mt-1">
            标准 5 字段格式: <code>分 时 日 月 周</code>。例如 <code>0 8 * * 1-5</code> = 每周一到周五 8 点。
          </div>
        </el-form-item>

        <el-form-item label="下次触发预览">
          <div class="text-[11px] text-cyan-400 font-mono">
            <div v-for="(t, i) in cronPreviewList" :key="i">{{ t }}</div>
            <div v-if="cronPreviewError" class="text-red-400">{{ cronPreviewError }}</div>
          </div>
        </el-form-item>

        <el-divider><span class="text-xs text-cyan-400">数据窗口</span></el-divider>

        <el-form-item label="数据窗口">
          <el-select v-model="editForm.data_window_type" class="w-full">
            <el-option label="昨天全天" value="yesterday" />
            <el-option label="今天截至当前" value="today" />
            <el-option label="过去 N 小时" value="last_n_hours" />
            <el-option label="过去 N 天" value="last_n_days" />
            <el-option label="昨天白班 (08:00-20:00)" value="shift_day_yesterday" />
            <el-option label="昨夜班 (20:00-次日08:00, 跨日)" value="shift_night_yesterday" />
            <el-option label="自定义偏移 + 时段" value="custom_offset" />
          </el-select>
          <div class="text-[11px] text-gray-500 mt-1">
            跨日截断 (start_hour > end_hour) 会自动走跨日 OR 查询, 跟数据页手动按钮的截断逻辑完全一致。
          </div>
        </el-form-item>

        <el-form-item v-if="editForm.data_window_type === 'last_n_hours'" label="N 小时">
          <el-input-number v-model="windowConfigN" :min="1" :max="720" />
        </el-form-item>
        <el-form-item v-if="editForm.data_window_type === 'last_n_days'" label="N 天">
          <el-input-number v-model="windowConfigN" :min="1" :max="365" />
        </el-form-item>

        <el-form-item v-if="['shift_day_yesterday','shift_night_yesterday','custom_offset'].includes(editForm.data_window_type)" label="时段">
          <div class="flex items-center gap-2">
            <el-input v-model="windowConfigStartHour" placeholder="08:00" class="w-24" />
            <span class="text-gray-400">至</span>
            <el-input v-model="windowConfigEndHour" placeholder="20:00" class="w-24" />
          </div>
        </el-form-item>

        <el-form-item v-if="editForm.data_window_type === 'custom_offset'" label="日期偏移(天)">
          <el-input-number v-model="windowConfigOffset" :min="-30" :max="0" />
          <span class="text-[11px] text-gray-500 ml-2">0=今天, -1=昨天, -2=前天, ...</span>
        </el-form-item>

        <el-form-item label="项目过滤">
          <el-input-number v-model="editForm.project_id" :min="1" controls-position="right" />
          <span class="text-[11px] text-gray-500 ml-2">留空 = 全部项目</span>
        </el-form-item>
        <el-form-item label="工位过滤">
          <el-input-number v-model="editForm.channel_id" :min="0" controls-position="right" />
          <span class="text-[11px] text-gray-500 ml-2">留空 = 全部工位, 0/1/2/3 = 1-4 号工位</span>
        </el-form-item>

        <el-divider><span class="text-xs text-cyan-400">输出</span></el-divider>

        <el-form-item label="输出格式">
          <el-radio-group v-model="editForm.output_format">
            <el-radio-button label="csv">CSV</el-radio-button>
            <el-radio-button label="txt">TXT</el-radio-button>
            <el-radio-button label="xlsx">XLSX</el-radio-button>
            <el-radio-button label="docx">DOCX</el-radio-button>
            <el-radio-button label="pdf">PDF</el-radio-button>
          </el-radio-group>
        </el-form-item>

        <el-form-item label="模板来源">
          <el-radio-group v-model="editForm.use_standard_daily_report">
            <el-radio :label="true">标准日报列 (跟手动按钮一致)</el-radio>
            <el-radio :label="false">自定义模板 (Jinja2)</el-radio>
          </el-radio-group>
        </el-form-item>

        <el-form-item v-if="!editForm.use_standard_daily_report" label="自定义模板">
          <el-select v-model="editForm.template_id" placeholder="选择已配置的导出模板" class="w-full">
            <el-option v-for="t in templates" :key="t.id" :label="`#${t.id} ${t.name}`" :value="t.id" />
          </el-select>
          <div class="text-[11px] text-orange-400 mt-1">
            ⚠ 注意: 自定义模板渲染上下文是"单 cycle 字段"。日报场景请优先用标准日报列。
          </div>
        </el-form-item>

        <el-form-item label="输出目录">
          <el-input v-model="editForm.output_dir" :placeholder="`留空 → 全局默认 (${defaultDirCurrent || '未设置'})`" />
        </el-form-item>

        <el-form-item label="文件名模板">
          <el-input v-model="editForm.filename_template" placeholder="{rule_name}_{date}.{format}" />
          <div class="text-[11px] text-gray-500 mt-1">
            占位符: <code>{rule_name}</code> <code>{date}</code> <code>{datetime}</code> <code>{window_start}</code> <code>{window_end}</code> <code>{format}</code>
          </div>
        </el-form-item>

        <el-form-item label="冲突策略">
          <el-radio-group v-model="editForm.overwrite_policy">
            <el-radio-button label="overwrite">覆盖</el-radio-button>
            <el-radio-button label="rename">加时间戳</el-radio-button>
            <el-radio-button label="skip">跳过</el-radio-button>
          </el-radio-group>
        </el-form-item>

        <el-form-item label="编码">
          <el-radio-group v-model="editForm.encoding">
            <el-radio-button label="utf-8-sig">UTF-8 (带 BOM, Excel 友好)</el-radio-button>
            <el-radio-button label="utf-8">UTF-8</el-radio-button>
            <el-radio-button label="gbk">GBK</el-radio-button>
          </el-radio-group>
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" @click="onSave" :loading="saving">保存</el-button>
      </template>
    </el-dialog>

    <!-- ========== 日志查看 ========== -->
    <el-dialog
      v-model="logsVisible"
      :title="`规则 #${logsRuleId} 运行日志`"
      width="800px"
      append-to-body
      class="dark-dialog"
    >
      <el-table :data="logs" size="small" class="dark-table" max-height="500">
        <el-table-column label="时间" width="160">
          <template #default="{ row }">{{ formatTime(row.triggered_at) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="statusTagType(row.status)">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="耗时" width="80">
          <template #default="{ row }">{{ row.duration_ms }}ms</template>
        </el-table-column>
        <el-table-column label="文件" min-width="200">
          <template #default="{ row }">
            <span class="text-[11px] text-cyan-400" :title="row.output_file">{{ row.output_file || '-' }}</span>
            <span v-if="row.file_size" class="text-[11px] text-gray-500 ml-2">({{ formatSize(row.file_size) }})</span>
          </template>
        </el-table-column>
        <el-table-column label="错误" min-width="200">
          <template #default="{ row }">
            <span class="text-[11px] text-red-400" :title="row.error_msg">{{ row.error_msg ? row.error_msg.slice(0, 60) : '-' }}</span>
          </template>
        </el-table-column>
      </el-table>
    </el-dialog>
  </el-dialog>
</template>

<script setup>
import { ref, computed, watch } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { Plus } from '@element-plus/icons-vue';
import {
  listScheduledRules, createScheduledRule, updateScheduledRule,
  deleteScheduledRule, toggleScheduledRule, testRunScheduledRule,
  listScheduledRuleLogs, getDefaultOutputDir, setDefaultOutputDir,
  previewCron, listExportTemplates,
} from '@/api/export';

const props = defineProps({ modelValue: Boolean });
const emit = defineEmits(['update:modelValue']);

const visible = computed({
  get: () => props.modelValue,
  set: v => emit('update:modelValue', v),
});

const activeTab = ref('rules');
const rules = ref([]);
const templates = ref([]);
const defaultDir = ref('');
const defaultDirCurrent = ref('');

const editVisible = ref(false);
const editForm = ref({});
const saving = ref(false);
const cronMode = ref('daily');
const dailyTime = ref('00:00');
const weeklyDay = ref(1);
const weeklyTime = ref('00:00');
const hourlyMinute = ref(0);
const windowConfigN = ref(24);
const windowConfigStartHour = ref('08:00');
const windowConfigEndHour = ref('20:00');
const windowConfigOffset = ref(-1);
const cronPreviewList = ref([]);
const cronPreviewError = ref('');

const logsVisible = ref(false);
const logsRuleId = ref(null);
const logs = ref([]);

const blankRule = () => ({
  name: '',
  enabled: true,
  description: '',
  cron_expression: '0 0 * * *',
  data_window_type: 'yesterday',
  data_window_config: {},
  project_id: null,
  channel_id: null,
  output_format: 'csv',
  output_dir: '',
  filename_template: '{rule_name}_{date}.{format}',
  use_standard_daily_report: true,
  template_id: null,
  encoding: 'utf-8-sig',
  newline: 'lf',
  overwrite_policy: 'overwrite',
});

watch(visible, async (v) => {
  if (v) {
    await refreshRules();
    await refreshDefaultDir();
    await refreshTemplates();
  }
});

watch(() => editForm.value.cron_expression, () => {
  refreshCronPreview();
});

const refreshRules = async () => {
  const res = await listScheduledRules();
  rules.value = res.data || [];
};

const refreshTemplates = async () => {
  try {
    const res = await listExportTemplates();
    templates.value = res.data || [];
  } catch (e) {
    templates.value = [];
  }
};

const refreshDefaultDir = async () => {
  try {
    const res = await getDefaultOutputDir();
    defaultDirCurrent.value = res.data?.value || '';
    defaultDir.value = defaultDirCurrent.value;
  } catch (e) {
    defaultDirCurrent.value = '';
  }
};

const refreshCronPreview = async () => {
  cronPreviewError.value = '';
  cronPreviewList.value = [];
  const expr = (editForm.value.cron_expression || '').trim();
  if (!expr) return;
  try {
    const res = await previewCron(expr, 3);
    cronPreviewList.value = (res.data?.next_runs || []).map(formatTime);
  } catch (e) {
    cronPreviewError.value = e.response?.data?.detail || 'cron 表达式不合法';
  }
};

const onSimpleChange = () => {
  if (cronMode.value === 'daily') {
    const [h, m] = (dailyTime.value || '00:00').split(':');
    editForm.value.cron_expression = `${parseInt(m)} ${parseInt(h)} * * *`;
  } else if (cronMode.value === 'weekly') {
    const [h, m] = (weeklyTime.value || '00:00').split(':');
    editForm.value.cron_expression = `${parseInt(m)} ${parseInt(h)} * * ${weeklyDay.value}`;
  } else if (cronMode.value === 'hourly') {
    editForm.value.cron_expression = `${hourlyMinute.value} * * * *`;
  }
};

const onCronModeChange = () => {
  onSimpleChange();
};

const onCreate = () => {
  editForm.value = blankRule();
  cronMode.value = 'daily';
  dailyTime.value = '00:00';
  windowConfigN.value = 24;
  windowConfigStartHour.value = '08:00';
  windowConfigEndHour.value = '20:00';
  windowConfigOffset.value = -1;
  editVisible.value = true;
};

const onEdit = (row) => {
  editForm.value = JSON.parse(JSON.stringify(row));
  const cfg = editForm.value.data_window_config || {};
  windowConfigN.value = cfg.n || 24;
  windowConfigStartHour.value = cfg.start_hour || '08:00';
  windowConfigEndHour.value = cfg.end_hour || '20:00';
  windowConfigOffset.value = cfg.date_offset_days ?? -1;
  // 反推 cron 简化模式
  const parts = (editForm.value.cron_expression || '').split(' ');
  if (parts.length === 5 && parts[2] === '*' && parts[3] === '*') {
    if (parts[4] === '*' && parts[1] !== '*') {
      cronMode.value = 'daily';
      dailyTime.value = `${String(parts[1]).padStart(2,'0')}:${String(parts[0]).padStart(2,'0')}`;
    } else if (parts[4] !== '*' && parts[1] !== '*') {
      cronMode.value = 'weekly';
      weeklyDay.value = parseInt(parts[4]);
      weeklyTime.value = `${String(parts[1]).padStart(2,'0')}:${String(parts[0]).padStart(2,'0')}`;
    } else if (parts[1] === '*' && parts[4] === '*') {
      cronMode.value = 'hourly';
      hourlyMinute.value = parseInt(parts[0]) || 0;
    } else {
      cronMode.value = 'custom';
    }
  } else {
    cronMode.value = 'custom';
  }
  editVisible.value = true;
};

const onSave = async () => {
  if (!editForm.value.name || !editForm.value.name.trim()) {
    ElMessage.warning('请填写规则名称');
    return;
  }
  // 合并 window_config
  const cfg = {};
  const wt = editForm.value.data_window_type;
  if (wt === 'last_n_hours' || wt === 'last_n_days') cfg.n = windowConfigN.value;
  if (['shift_day_yesterday','shift_night_yesterday','custom_offset'].includes(wt)) {
    cfg.start_hour = windowConfigStartHour.value;
    cfg.end_hour = windowConfigEndHour.value;
  }
  if (wt === 'custom_offset') cfg.date_offset_days = windowConfigOffset.value;
  editForm.value.data_window_config = cfg;
  // 清掉空 project/channel
  if (!editForm.value.project_id) editForm.value.project_id = null;
  if (editForm.value.channel_id === undefined || editForm.value.channel_id === '') editForm.value.channel_id = null;

  saving.value = true;
  try {
    if (editForm.value.id) {
      await updateScheduledRule(editForm.value.id, editForm.value);
    } else {
      await createScheduledRule(editForm.value);
    }
    ElMessage.success('已保存');
    editVisible.value = false;
    await refreshRules();
  } catch (e) {
    ElMessage.error('保存失败: ' + (e.response?.data?.detail || e.message));
  } finally {
    saving.value = false;
  }
};

const onToggle = async (row) => {
  try {
    await toggleScheduledRule(row.id);
    await refreshRules();
  } catch (e) {
    ElMessage.error('切换失败');
  }
};

const onDelete = async (row) => {
  try {
    await ElMessageBox.confirm(`确认删除规则"${row.name}" ?`, '提示', { type: 'warning' });
    await deleteScheduledRule(row.id);
    ElMessage.success('已删除');
    await refreshRules();
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('删除失败');
  }
};

const onTestRun = async (row) => {
  try {
    const res = await testRunScheduledRule(row.id);
    const r = res.data?.result || {};
    if (r.status === 'success') {
      ElMessage.success(`成功. 文件: ${r.file} (${formatSize(r.size)})`);
    } else if (r.status === 'no_data') {
      ElMessage.warning('窗口内没数据, 未生成文件');
    } else if (r.status === 'skipped') {
      ElMessage.info('已跳过 (overwrite_policy=skip 且文件已存在)');
    } else {
      ElMessage.error('失败: ' + (r.error || '未知错误'));
    }
    await refreshRules();
  } catch (e) {
    ElMessage.error('测试触发失败: ' + (e.response?.data?.detail || e.message));
  }
};

const onViewLogs = async (row) => {
  logsRuleId.value = row.id;
  try {
    const res = await listScheduledRuleLogs(row.id, { limit: 50 });
    logs.value = res.data?.items || [];
  } catch (e) {
    logs.value = [];
  }
  logsVisible.value = true;
};

const onSaveDefaultDir = async () => {
  try {
    await setDefaultOutputDir(defaultDir.value || '');
    ElMessage.success('已保存');
    await refreshDefaultDir();
  } catch (e) {
    ElMessage.error('保存失败');
  }
};

// ---- helpers ----
const describeCron = (expr) => {
  if (!expr) return '';
  const parts = expr.split(' ');
  if (parts.length === 5) {
    const [m, h, d, mo, dw] = parts;
    if (d === '*' && mo === '*' && dw === '*') return `每天 ${h}:${m.padStart(2,'0')}`;
    if (d === '*' && mo === '*' && dw !== '*') {
      const dwMap = { '0':'周日','1':'周一','2':'周二','3':'周三','4':'周四','5':'周五','6':'周六' };
      return `每${dwMap[dw] || dw} ${h}:${m.padStart(2,'0')}`;
    }
    if (h === '*' && d === '*' && mo === '*' && dw === '*') return `每小时第 ${m} 分`;
  }
  return '高级 cron';
};

const windowLabel = (t) => ({
  yesterday: '昨天全天', today: '今天截至当前',
  last_n_hours: '过去 N 小时', last_n_days: '过去 N 天',
  shift_day_yesterday: '昨天白班', shift_night_yesterday: '昨夜班(跨日)',
  custom_offset: '自定义偏移',
}[t] || t);

const formatTagType = (fmt) => ({
  csv: '', txt: 'info', xlsx: 'success', docx: 'warning', pdf: 'danger',
}[fmt] || '');

const statusTagType = (s) => ({
  success: 'success', failed: 'danger', skipped: 'info', no_data: 'warning',
}[s] || '');

const formatTime = (s) => {
  if (!s) return '-';
  try {
    return new Date(s).toLocaleString('zh-CN', { hour12: false });
  } catch { return s; }
};

const formatSize = (n) => {
  if (!n) return '';
  if (n < 1024) return `${n}B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)}KB`;
  return `${(n / 1024 / 1024).toFixed(2)}MB`;
};
</script>

<style scoped>
.sched-rules-dialog :deep(.el-dialog__body) {
  padding: 12px 20px;
}

.dark-table :deep(th) {
  background-color: rgb(15 23 42) !important;
  color: rgb(148 163 184) !important;
}

.dark-table :deep(tr) {
  background-color: rgb(30 41 59) !important;
}

.dark-table :deep(td) {
  background-color: rgb(30 41 59) !important;
  border-bottom-color: rgb(51 65 85) !important;
  color: rgb(226 232 240) !important;
}

.dark-table :deep(.el-table__empty-block) {
  background-color: rgb(30 41 59) !important;
}
</style>
