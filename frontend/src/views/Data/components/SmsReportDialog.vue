<!--
  v3.46 每日短信日报管理对话框

  特点:
  - 规则列表: 每天定点把当日/昨日 KPI 摘要发到客户手机 (云短信模板+变量)
  - 指标从字段中央仓库勾选 (stats/aggregations 标量字段) + 自定义计数器当日增量
  - 汇总模式一条短信 / 分工位模式每工位一条
  - 服务商设置: 阿里云 / 腾讯云, AK/SK 走后端 SystemConfig (SK 脱敏回显)
  - 预览 (渲染当前窗口变量不发送) / 模拟试发 (mock 不发真短信) / 真实试发
-->
<template>
  <el-dialog
    v-model="visible"
    title="每日短信日报"
    width="92%"
    top="3vh"
    :close-on-click-modal="false"
    destroy-on-close
    class="dark-dialog sms-report-dialog"
  >
    <el-tabs v-model="activeTab">
      <!-- ========== Tab 1: 规则列表 ========== -->
      <el-tab-pane label="日报规则" name="rules">
        <div class="flex justify-between items-center mb-3">
          <div class="text-xs text-gray-400">
            按时定点把当日数据摘要以短信发到客户手机。国内云短信须先在服务商平台审核"签名+模板"，短信内容 = 模板 + 这里勾选的变量。
          </div>
          <el-button type="primary" size="small" @click="onCreate">
            <el-icon class="mr-1"><Plus /></el-icon>新建规则
          </el-button>
        </div>

        <el-table :data="rules" size="small" class="dark-table" empty-text="暂无规则, 点上方按钮新建一条">
          <el-table-column type="index" width="40" />
          <el-table-column prop="name" label="名称" min-width="130">
            <template #default="{ row }">
              <div class="flex items-center gap-2">
                <el-switch :model-value="row.enabled" size="small" @change="onToggle(row)" />
                <span :class="{ 'text-gray-500': !row.enabled }">{{ row.name }}</span>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="发送时间" min-width="140">
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
          <el-table-column label="窗口/范围" width="130">
            <template #default="{ row }">
              <div class="text-[11px]">
                {{ row.data_window_type === 'yesterday' ? '昨天全天' : '今天截至发送' }}
                <div class="mt-0.5">
                  <el-tag size="small" :type="row.group_by_channel ? 'warning' : 'info'">
                    {{ row.group_by_channel ? '分工位' : '汇总' }}
                  </el-tag>
                </div>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="指标" min-width="160">
            <template #default="{ row }">
              <div class="text-[11px] text-gray-400 truncate" :title="(row.metrics || []).join(', ')">
                {{ (row.metrics || []).length }} 项:
                {{ (row.metrics || []).map(m => metricLabel(m)).slice(0, 3).join('、') }}
                {{ (row.metrics || []).length > 3 ? '…' : '' }}
              </div>
            </template>
          </el-table-column>
          <el-table-column label="手机号" min-width="130">
            <template #default="{ row }">
              <div class="text-[11px] font-mono text-cyan-400 truncate"
                   :title="(row.phone_numbers || []).join(', ')">
                {{ (row.phone_numbers || []).join(', ') || '-' }}
              </div>
            </template>
          </el-table-column>
          <el-table-column label="最近" width="130">
            <template #default="{ row }">
              <div class="text-[11px]">
                <el-tag v-if="row.last_run_status" size="small" :type="statusTagType(row.last_run_status)">
                  {{ row.last_run_status }}
                </el-tag>
                <span v-else class="text-gray-500">未运行</span>
                <div class="text-gray-500 mt-0.5" v-if="row.last_run_time">{{ formatTime(row.last_run_time) }}</div>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="290" fixed="right">
            <template #default="{ row }">
              <el-button size="small" type="primary" link @click="onEdit(row)">编辑</el-button>
              <el-button size="small" type="success" link @click="onPreview(row)">预览</el-button>
              <el-button size="small" type="warning" link @click="onTestSend(row, true)">模拟试发</el-button>
              <el-button size="small" type="danger" link @click="onTestSend(row, false)">真实试发</el-button>
              <el-button size="small" type="info" link @click="onViewLogs(row)">记录</el-button>
              <el-button size="small" type="danger" link @click="onDelete(row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- ========== Tab 2: 服务商设置 ========== -->
      <el-tab-pane label="服务商设置" name="provider">
        <div class="text-xs text-gray-400 mb-3">
          凭据保存在服务器配置库（不落浏览器）。短信签名与模板需先在云平台申请并通过审核，模板里的变量名要与规则的"变量映射"一致。
        </div>
        <el-form label-width="140px" size="small" class="max-w-2xl">
          <el-form-item label="服务商">
            <el-radio-group v-model="providerForm.provider">
              <el-radio-button label="aliyun">阿里云短信</el-radio-button>
              <el-radio-button label="tencent">腾讯云短信</el-radio-button>
              <el-radio-button label="http_relay">自建 HTTP 中转</el-radio-button>
            </el-radio-group>
          </el-form-item>

          <!-- 自建中转: 云审核未过的过渡通道 / 客户自有短信网关 -->
          <template v-if="providerForm.provider === 'http_relay'">
            <el-form-item label="中转服务地址">
              <el-input v-model="providerForm.config.relay_url"
                        placeholder="https://your.domain/send" />
            </el-form-item>
            <el-form-item label="鉴权 Token">
              <el-input v-model="providerForm.config.relay_token" type="password" show-password
                        placeholder="与中转服务约定的 Bearer token, 回显为 ******" />
            </el-form-item>
            <el-form-item label="短信正文模板">
              <el-input v-model="providerForm.config.content_template" type="textarea" :rows="3"
                        placeholder="【天军视觉】${date}日报: 总数${total_cycles} 良率${yield_rate}%" />
              <div class="text-[11px] text-gray-500 mt-1">
                ${变量名} 引用规则"变量映射"里的云模板变量名; 留空则按 变量:值 拼接。
                无平台审核, 正文本端直接渲染 — 适合云短信签名/模板审核期间先跑起来。
              </div>
            </el-form-item>
          </template>

          <template v-else>
            <el-form-item :label="providerForm.provider === 'tencent' ? 'SecretId' : 'AccessKey ID'">
              <el-input v-model="providerForm.config.access_key_id" placeholder="云账号访问密钥 ID" />
            </el-form-item>
            <el-form-item :label="providerForm.provider === 'tencent' ? 'SecretKey' : 'AccessKey Secret'">
              <el-input v-model="providerForm.config.access_key_secret" type="password" show-password
                        placeholder="密钥原文只写入, 回显为 ******" />
            </el-form-item>
            <el-form-item label="短信签名">
              <el-input v-model="providerForm.config.sign_name" placeholder="如: 天军视觉 (须平台审核通过)" />
            </el-form-item>
            <el-form-item label="默认模板 Code">
              <el-input v-model="providerForm.config.template_code"
                        :placeholder="providerForm.provider === 'tencent' ? '模板 ID (纯数字)' : '如: SMS_123456789'" />
            </el-form-item>
            <el-form-item v-if="providerForm.provider === 'tencent'" label="SdkAppId">
              <el-input v-model="providerForm.config.sms_sdk_app_id" placeholder="腾讯云短信应用 SdkAppId (必填)" />
            </el-form-item>
            <el-form-item label="Region">
              <el-input v-model="providerForm.config.region"
                        :placeholder="providerForm.provider === 'tencent' ? 'ap-guangzhou' : 'cn-hangzhou'" />
            </el-form-item>
          </template>

          <el-form-item>
            <el-button type="primary" :loading="providerSaving" @click="onSaveProvider">保存服务商配置</el-button>
          </el-form-item>
        </el-form>
      </el-tab-pane>
    </el-tabs>

    <!-- ========== 编辑规则 ========== -->
    <el-dialog
      v-model="editVisible"
      :title="editForm.id ? `编辑日报规则 #${editForm.id}` : '新建短信日报规则'"
      width="860px"
      top="4vh"
      :close-on-click-modal="false"
      append-to-body
      destroy-on-close
      class="dark-dialog"
    >
      <el-form :model="editForm" label-width="130px" size="small">
        <el-form-item label="规则名称" required>
          <el-input v-model="editForm.name" placeholder="如: 每日 20 点产量日报" />
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="editForm.enabled" />
        </el-form-item>

        <el-divider><span class="text-xs text-cyan-400">发送时间与数据窗口</span></el-divider>

        <el-form-item label="每天发送时刻">
          <div class="flex items-center gap-3">
            <el-time-picker v-model="dailyTime" format="HH:mm" value-format="HH:mm" @change="onDailyTimeChange" />
            <el-checkbox v-model="cronAdvanced">高级 cron</el-checkbox>
          </div>
        </el-form-item>
        <el-form-item v-if="cronAdvanced" label="cron 表达式">
          <el-input v-model="editForm.cron_expression" placeholder="0 20 * * *" />
          <div class="text-[11px] text-gray-500 mt-1">5 字段: 分 时 日 月 周。例 <code>0 20 * * *</code> = 每天 20:00</div>
        </el-form-item>
        <el-form-item label="数据窗口">
          <el-radio-group v-model="editForm.data_window_type">
            <el-radio-button label="today">今天 (0点→发送时刻)</el-radio-button>
            <el-radio-button label="yesterday">昨天全天</el-radio-button>
          </el-radio-group>
          <div class="text-[11px] text-gray-500 mt-1">"发今天数据"建议晚上发; 凌晨发则选"昨天全天"。</div>
        </el-form-item>

        <el-divider><span class="text-xs text-cyan-400">范围</span></el-divider>

        <el-form-item label="工位范围">
          <el-radio-group v-model="editForm.group_by_channel">
            <el-radio-button :label="false">汇总一条短信</el-radio-button>
            <el-radio-button :label="true">分工位每工位一条</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="工位过滤">
          <el-input v-model="channelIdsText" placeholder="留空 = 全部工位; 逗号分隔工位号, 如 0,1,2" />
        </el-form-item>
        <el-form-item label="项目过滤">
          <el-input-number v-model="editForm.project_id" :min="1" controls-position="right" />
          <span class="text-[11px] text-gray-500 ml-2">留空 = 全部项目</span>
        </el-form-item>

        <el-divider><span class="text-xs text-cyan-400">指标勾选 (来自数据中心字段仓库)</span></el-divider>

        <el-form-item label="统计指标">
          <div class="grid grid-cols-2 gap-x-6 max-h-48 overflow-y-auto w-full pr-2">
            <el-checkbox
              v-for="f in pickableFields" :key="f.path"
              :model-value="editForm.metrics.includes(f.path)"
              @change="v => onMetricToggle(f.path, v)"
            >
              <span class="text-xs">{{ f.label }}</span>
              <span class="text-[10px] text-gray-500 ml-1 font-mono">{{ f.path }}</span>
            </el-checkbox>
          </div>
        </el-form-item>

        <el-form-item label="计数器当日增量">
          <div class="w-full">
            <div class="flex gap-2 mb-1">
              <el-input v-model="counterNameInput" placeholder="输入项目里的计数器名, 如: 合格总数"
                        class="w-64" @keyup.enter="addCounterMetric" />
              <el-button size="small" @click="addCounterMetric">添加</el-button>
            </div>
            <el-tag
              v-for="m in counterMetrics" :key="m" closable class="mr-1 mb-1"
              @close="onMetricToggle(m, false)"
            >{{ m.replace('counters_daily.', '') }}(当日)</el-tag>
            <div class="text-[11px] text-gray-500">只累计当日正向增量, 清零/重置不扣减。</div>
          </div>
        </el-form-item>

        <el-divider><span class="text-xs text-cyan-400">模板变量映射</span></el-divider>

        <el-form-item label="变量映射">
          <div class="w-full">
            <el-table :data="mappingRows" size="small" class="dark-table" empty-text="先在上方勾选指标">
              <el-table-column label="指标" min-width="200">
                <template #default="{ row }">
                  <span class="text-xs">{{ metricLabel(row.path) }}</span>
                  <span class="text-[10px] text-gray-500 ml-1 font-mono">{{ row.path }}</span>
                </template>
              </el-table-column>
              <el-table-column label="云模板变量名" width="220">
                <template #default="{ row }">
                  <el-input :model-value="editForm.template_param_mapping[row.path] || row.defaultVar"
                            size="small"
                            @update:model-value="v => editForm.template_param_mapping[row.path] = v" />
                </template>
              </el-table-column>
            </el-table>
            <div class="text-[11px] text-gray-500 mt-1">
              建议模板文案 (去云平台提审时参考):
              <code class="text-cyan-400">{{ suggestedTemplate }}</code>
            </div>
          </div>
        </el-form-item>

        <el-divider><span class="text-xs text-cyan-400">收件与模板</span></el-divider>

        <el-form-item label="收件手机号" required>
          <div class="w-full">
            <div class="flex gap-2 mb-1">
              <el-input v-model="phoneInput" placeholder="11 位手机号" class="w-64" @keyup.enter="addPhone" />
              <el-button size="small" @click="addPhone">添加</el-button>
            </div>
            <el-tag v-for="p in editForm.phone_numbers" :key="p" closable type="success" class="mr-1 mb-1"
                    @close="removePhone(p)">{{ p }}</el-tag>
          </div>
        </el-form-item>
        <el-form-item label="模板 Code 覆盖">
          <el-input v-model="editForm.template_code" placeholder="留空 = 用服务商设置里的默认模板" />
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" @click="onSave" :loading="saving">保存</el-button>
      </template>
    </el-dialog>

    <!-- ========== 预览 ========== -->
    <el-dialog v-model="previewVisible" title="日报内容预览 (不发送)" width="640px" append-to-body class="dark-dialog">
      <div v-for="(scope, i) in previewScopes" :key="i" class="mb-3">
        <div class="text-xs text-orange-400 mb-1">
          {{ scope.channel_id != null ? `工位 ${scope.channel_id}` : '汇总' }}
          <span v-if="scope.note" class="text-gray-500 ml-2">{{ scope.note }}</span>
        </div>
        <el-table :data="paramsToRows(scope.params)" size="small" class="dark-table" empty-text="无变量">
          <el-table-column prop="name" label="变量名" width="180" />
          <el-table-column prop="value" label="值" />
        </el-table>
      </div>
    </el-dialog>

    <!-- ========== 发送记录 ========== -->
    <el-dialog v-model="logsVisible" :title="`规则 #${logsRuleId} 发送记录`" width="900px" append-to-body class="dark-dialog">
      <el-table :data="logs" size="small" class="dark-table" max-height="500" empty-text="暂无发送记录">
        <el-table-column label="时间" width="150">
          <template #default="{ row }">{{ formatTime(row.triggered_at) }}</template>
        </el-table-column>
        <el-table-column label="来源" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="row.source_type === 'cron' ? '' : 'warning'">
              {{ row.source_type === 'cron' ? '定时' : '试发' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="provider" label="通道" width="80" />
        <el-table-column label="工位" width="70">
          <template #default="{ row }">{{ row.channel_id != null ? row.channel_id : '汇总' }}</template>
        </el-table-column>
        <el-table-column label="手机号" min-width="130">
          <template #default="{ row }">
            <span class="text-[11px] font-mono">{{ (row.phone_numbers || []).join(', ') }}</span>
          </template>
        </el-table-column>
        <el-table-column label="变量" min-width="200">
          <template #default="{ row }">
            <span class="text-[11px] text-gray-400">{{ formatParams(row.template_params) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="结果" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="row.success ? 'success' : 'danger'">
              {{ row.success ? '成功' : '失败' }}
            </el-tag>
            <span v-if="row.retry_count" class="text-[10px] text-orange-400 ml-1">重试{{ row.retry_count }}</span>
          </template>
        </el-table-column>
        <el-table-column label="错误" min-width="160">
          <template #default="{ row }">
            <span class="text-[11px] text-red-400" :title="row.error_msg">
              {{ row.error_msg ? row.error_msg.slice(0, 50) : '-' }}
            </span>
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
import { getExportFields } from '@/api/export';
import {
  listSmsRules, createSmsRule, updateSmsRule, deleteSmsRule, toggleSmsRule,
  testSendSmsRule, previewSmsRule, listSmsRuleLogs,
  getSmsProviderConfig, setSmsProviderConfig,
} from '@/api/smsReport';

const props = defineProps({ modelValue: Boolean });
const emit = defineEmits(['update:modelValue']);

const visible = computed({
  get: () => props.modelValue,
  set: v => emit('update:modelValue', v),
});

const activeTab = ref('rules');
const rules = ref([]);
const allFields = ref([]);

const providerForm = ref({ provider: 'aliyun', config: {} });
const providerSaving = ref(false);

const editVisible = ref(false);
const editForm = ref(blankRule());
const saving = ref(false);
const cronAdvanced = ref(false);
const dailyTime = ref('20:00');
const channelIdsText = ref('');
const counterNameInput = ref('');
const phoneInput = ref('');

const previewVisible = ref(false);
const previewScopes = ref([]);

const logsVisible = ref(false);
const logsRuleId = ref(null);
const logs = ref([]);

function blankRule() {
  return {
    name: '',
    enabled: true,
    cron_expression: '0 20 * * *',
    data_window_type: 'today',
    group_by_channel: false,
    channel_ids: [],
    project_id: null,
    metrics: ['stats.total_cycles', 'stats.good_cycles', 'stats.ng_cycles', 'stats.yield_rate'],
    template_param_mapping: {},
    phone_numbers: [],
    template_code: '',
  };
}

// 可勾选字段: stats / aggregations 分组里的标量字段 (数组展开路径不适合短信)
const pickableFields = computed(() =>
  allFields.value.filter(f =>
    (f.group === 'stats' || f.group === 'aggregations') &&
    !f.path.includes('[*]') &&
    ['int', 'float', 'str'].includes(f.type)
  )
);

const counterMetrics = computed(() =>
  (editForm.value.metrics || []).filter(m => m.startsWith('counters_daily.')));

const mappingRows = computed(() =>
  (editForm.value.metrics || []).map(path => ({
    path,
    defaultVar: path.split('.').pop(),
  })));

const suggestedTemplate = computed(() => {
  const vars = (editForm.value.metrics || []).map(path =>
    editForm.value.template_param_mapping[path] || path.split('.').pop());
  if (!vars.length) return '(先勾选指标)';
  return '【签名】${date}日报: ' + vars.map(v => `${v}=\${${v}}`).join(' ');
});

watch(visible, async (v) => {
  if (v) {
    await Promise.all([refreshRules(), refreshFields(), refreshProvider()]);
  }
});

const refreshRules = async () => {
  try {
    const res = await listSmsRules();
    rules.value = res.data?.rules || [];
  } catch (e) {
    rules.value = [];
  }
};

const refreshFields = async () => {
  if (allFields.value.length) return;
  try {
    const res = await getExportFields(true);
    allFields.value = res.data?.fields || [];
  } catch (e) {
    allFields.value = [];
  }
};

const refreshProvider = async () => {
  try {
    const res = await getSmsProviderConfig();
    providerForm.value = {
      provider: res.data?.provider || 'aliyun',
      config: res.data?.config || {},
    };
  } catch (e) { /* 保持默认 */ }
};

const onSaveProvider = async () => {
  providerSaving.value = true;
  try {
    await setSmsProviderConfig(providerForm.value.provider, providerForm.value.config);
    ElMessage.success('服务商配置已保存');
    await refreshProvider();
  } catch (e) {
    ElMessage.error('保存失败: ' + (e.response?.data?.detail || e.message));
  } finally {
    providerSaving.value = false;
  }
};

// ---- 规则编辑 ----

const onCreate = () => {
  editForm.value = blankRule();
  dailyTime.value = '20:00';
  cronAdvanced.value = false;
  channelIdsText.value = '';
  editVisible.value = true;
};

const onEdit = (row) => {
  editForm.value = JSON.parse(JSON.stringify(row));
  editForm.value.metrics = editForm.value.metrics || [];
  editForm.value.template_param_mapping = editForm.value.template_param_mapping || {};
  editForm.value.phone_numbers = editForm.value.phone_numbers || [];
  channelIdsText.value = (row.channel_ids || []).join(',');
  // 反推每日时刻
  const parts = (row.cron_expression || '').split(' ');
  if (parts.length === 5 && parts[2] === '*' && parts[3] === '*' && parts[4] === '*') {
    dailyTime.value = `${String(parts[1]).padStart(2, '0')}:${String(parts[0]).padStart(2, '0')}`;
    cronAdvanced.value = false;
  } else {
    cronAdvanced.value = true;
  }
  editVisible.value = true;
};

const onDailyTimeChange = () => {
  if (cronAdvanced.value) return;
  const [h, m] = (dailyTime.value || '20:00').split(':');
  editForm.value.cron_expression = `${parseInt(m)} ${parseInt(h)} * * *`;
};

const onMetricToggle = (path, checked) => {
  const list = editForm.value.metrics;
  if (checked && !list.includes(path)) list.push(path);
  if (!checked) {
    const i = list.indexOf(path);
    if (i >= 0) list.splice(i, 1);
    delete editForm.value.template_param_mapping[path];
  }
};

const addCounterMetric = () => {
  const name = (counterNameInput.value || '').trim();
  if (!name) return;
  onMetricToggle(`counters_daily.${name}`, true);
  counterNameInput.value = '';
};

const addPhone = () => {
  const p = (phoneInput.value || '').trim();
  if (!/^1\d{10}$/.test(p)) {
    ElMessage.warning('请输入 11 位手机号');
    return;
  }
  if (!editForm.value.phone_numbers.includes(p)) editForm.value.phone_numbers.push(p);
  phoneInput.value = '';
};

const removePhone = (p) => {
  const i = editForm.value.phone_numbers.indexOf(p);
  if (i >= 0) editForm.value.phone_numbers.splice(i, 1);
};

const onSave = async () => {
  if (!editForm.value.name || !editForm.value.name.trim()) {
    ElMessage.warning('请填写规则名称');
    return;
  }
  if (!editForm.value.phone_numbers.length) {
    ElMessage.warning('请至少添加一个收件手机号');
    return;
  }
  onDailyTimeChange();
  editForm.value.channel_ids = channelIdsText.value
    .split(/[,，\s]+/).filter(s => s !== '').map(Number).filter(n => !Number.isNaN(n));
  if (!editForm.value.project_id) editForm.value.project_id = null;

  saving.value = true;
  try {
    if (editForm.value.id) {
      await updateSmsRule(editForm.value.id, editForm.value);
    } else {
      await createSmsRule(editForm.value);
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
    await toggleSmsRule(row.id);
    await refreshRules();
  } catch (e) {
    ElMessage.error('切换失败');
  }
};

const onDelete = async (row) => {
  try {
    await ElMessageBox.confirm(`确认删除日报规则"${row.name}" ?`, '提示', { type: 'warning' });
    await deleteSmsRule(row.id);
    ElMessage.success('已删除');
    await refreshRules();
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('删除失败');
  }
};

const onPreview = async (row) => {
  try {
    const res = await previewSmsRule(row.id);
    previewScopes.value = res.data?.scopes || [];
    previewVisible.value = true;
  } catch (e) {
    ElMessage.error('预览失败: ' + (e.response?.data?.detail || e.message));
  }
};

const onTestSend = async (row, useMock) => {
  try {
    if (!useMock) {
      await ElMessageBox.confirm(
        `将真实发送短信到 ${(row.phone_numbers || []).length} 个手机号 (计费), 确认?`,
        '真实试发', { type: 'warning' });
    }
    const res = await testSendSmsRule(row.id, useMock);
    const r = res.data || {};
    if (r.status === 'success') {
      ElMessage.success(`${useMock ? '模拟' : ''}发送成功 (${r.sent} 条)`);
    } else if (r.status === 'skipped') {
      ElMessage.info('已跳过: ' + (r.error || '无内容可发'));
    } else {
      ElMessage.error(`发送失败: ${r.error || (r.details || []).map(d => d.error).filter(Boolean).join('; ') || '未知错误'}`);
    }
    await refreshRules();
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('试发失败: ' + (e.response?.data?.detail || e.message));
  }
};

const onViewLogs = async (row) => {
  logsRuleId.value = row.id;
  try {
    const res = await listSmsRuleLogs(row.id, { limit: 50 });
    logs.value = res.data?.logs || [];
  } catch (e) {
    logs.value = [];
  }
  logsVisible.value = true;
};

// ---- helpers ----

const metricLabel = (path) => {
  if (path.startsWith('counters_daily.')) return `${path.replace('counters_daily.', '')}(当日)`;
  const f = allFields.value.find(x => x.path === path);
  return f ? f.label : path;
};

const describeCron = (expr) => {
  const parts = (expr || '').split(' ');
  if (parts.length === 5 && parts[2] === '*' && parts[3] === '*' && parts[4] === '*') {
    return `每天 ${parts[1]}:${String(parts[0]).padStart(2, '0')}`;
  }
  return '高级 cron';
};

const statusTagType = (s) => ({
  success: 'success', partial: 'warning', failed: 'danger', skipped: 'info',
}[s] || '');

const formatTime = (s) => {
  if (!s) return '-';
  try { return new Date(s).toLocaleString('zh-CN', { hour12: false }); } catch { return s; }
};

const paramsToRows = (params) =>
  Object.entries(params || {}).map(([name, value]) => ({ name, value }));

const formatParams = (params) =>
  Object.entries(params || {}).map(([k, v]) => `${k}=${v}`).join(' ');
</script>

<style scoped>
.sms-report-dialog :deep(.el-dialog__body) {
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
