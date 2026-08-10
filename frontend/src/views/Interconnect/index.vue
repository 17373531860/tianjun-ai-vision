<template>
  <div class="p-6 space-y-6">
    <div class="flex justify-between items-center">
      <h2 class="text-2xl font-bold border-l-4 border-tech-blue pl-3">训练平台互连 (YoloVision)</h2>
      <el-button type="primary" :loading="saving" @click="saveConfig">保存配置</el-button>
    </div>
    <div class="text-xs text-gray-500 -mt-3">
      与公司 YoloVision 训练平台双向互连：训练平台的模型可直接推送到本机模型仓库（含训练分析）；
      本机现场推理可按规则采样回传帧到训练平台做数据集增量与增强训练。项目名在两个产品中需保持一致。
    </div>

    <div v-loading="loading" class="grid grid-cols-1 lg:grid-cols-2 gap-6">
      <!-- 连接配置 -->
      <div class="bg-ind-panel border border-gray-800 rounded-xl p-5 space-y-4">
        <div class="flex justify-between items-center">
          <h3 class="text-base font-bold text-gray-200">连接配置</h3>
          <el-switch v-model="form.enabled" active-text="启用互连" />
        </div>
        <el-form label-position="top">
          <el-form-item label="训练平台地址 (Base URL)">
            <el-input v-model="form.platform_url" placeholder="例如: http://192.168.1.10:8080" />
          </el-form-item>
          <el-form-item label="共享令牌 (双方需配置同一随机串, ≥32 字符)">
            <el-input v-model="form.token" type="password" show-password placeholder="X-Interconnect-Token" />
          </el-form-item>
          <el-form-item label="本机设备名 (多台工控机连同一平台时的可读标识, 空 = 主机名)">
            <el-input v-model="form.device_name" placeholder="例如: 装配线-3号工控机" />
          </el-form-item>
        </el-form>
        <div class="text-xs text-gray-500">
          设备 ID: <span class="font-mono text-gray-400">{{ status?.device_id || '—' }}</span>
          <span class="ml-2">(自动生成不可改, 训练平台按它区分每台设备)</span>
        </div>
        <div class="flex items-center gap-3">
          <el-button size="small" :loading="testing" @click="testConnection">测试连接</el-button>
          <span v-if="testResult" class="text-xs" :class="testResult.reachable ? 'text-green-400' : 'text-red-400'">
            {{ testResult.reachable
              ? `连通: ${testResult.product || '对端'} ${testResult.version || ''} (契约 ${testResult.contract || '?'})`
              : `不可达: ${testResult.error}` }}
          </span>
        </div>
        <div class="text-xs text-gray-500">
          未启用时：训练平台推送的模型会被拒收，采样回传不发送。默认关闭，对现有部署零影响。
        </div>
      </div>

      <!-- 采样回传规则 -->
      <div class="bg-ind-panel border border-gray-800 rounded-xl p-5 space-y-4">
        <div class="flex justify-between items-center">
          <h3 class="text-base font-bold text-gray-200">现场帧采样回传</h3>
          <el-switch v-model="form.sampling.enabled" active-text="启用采样" />
        </div>
        <div class="space-y-3 text-sm">
          <div class="flex items-center gap-3 flex-wrap">
            <el-switch v-model="form.sampling.low_conf_enabled" size="small" />
            <span class="text-gray-300">置信度带采样 (疑难样本)</span>
            <el-input-number v-model="form.sampling.conf_min" :min="0" :max="1" :step="0.05" :precision="2" size="small" controls-position="right" class="!w-24" />
            <span class="text-gray-500">≤ 置信度 &lt;</span>
            <el-input-number v-model="form.sampling.conf_max" :min="0" :max="1" :step="0.05" :precision="2" size="small" controls-position="right" class="!w-24" />
          </div>
          <div class="flex items-center gap-3 flex-wrap">
            <el-switch v-model="form.sampling.no_detection_enabled" size="small" />
            <span class="text-gray-300">未检出帧采样 (负样本候选 / 漏检候选)</span>
            <el-checkbox v-model="form.sampling.no_detection_in_cycle_only" size="small" :disabled="!form.sampling.no_detection_enabled">
              <span class="text-xs text-gray-400">仅周期内 (工件在检时的空帧才可疑, 建议保持勾选)</span>
            </el-checkbox>
          </div>
          <div class="flex items-center gap-3 flex-wrap">
            <el-switch v-model="form.sampling.dropout_enabled" size="small" />
            <span class="text-gray-300">检出闪断采样 (漏检强证据)</span>
            <span class="text-gray-500 text-xs">连续出现 ≥</span>
            <el-input-number v-model="form.sampling.dropout_min_frames" :min="2" :max="120" :step="1" :precision="0" size="small" controls-position="right" class="!w-20" />
            <span class="text-gray-500 text-xs">帧后突然消失 → 采该帧</span>
          </div>
          <div class="flex items-center gap-3">
            <el-switch v-model="form.sampling.ng_event_enabled" size="small" />
            <span class="text-gray-300">NG 事件现场帧采样</span>
          </div>
          <div class="flex items-center gap-3">
            <el-switch v-model="form.sampling.include_annotations" size="small" />
            <span class="text-gray-300">携带模型推理结果作预标注 (训练平台侧待人工/自动审核)</span>
          </div>
          <div class="flex items-center gap-4 flex-wrap pt-1">
            <div class="flex items-center gap-2">
              <span class="text-gray-400 text-xs">最小间隔(秒)</span>
              <el-input-number v-model="form.sampling.min_interval_s" :min="0" :max="3600" :step="1" :precision="0" size="small" controls-position="right" class="!w-24" />
            </div>
            <div class="flex items-center gap-2">
              <span class="text-gray-400 text-xs">每小时上限</span>
              <el-input-number v-model="form.sampling.max_per_hour" :min="1" :max="3600" :step="10" :precision="0" size="small" controls-position="right" class="!w-24" />
            </div>
            <div class="flex items-center gap-2">
              <span class="text-gray-400 text-xs">JPEG 质量</span>
              <el-input-number v-model="form.sampling.jpeg_quality" :min="30" :max="100" :step="5" :precision="0" size="small" controls-position="right" class="!w-24" />
            </div>
          </div>
        </div>
        <div class="text-xs text-gray-500">
          采样在推理线程外异步入本地磁盘队列，训练平台离线不丢帧、不影响检测；恢复后自动补传。
        </div>
      </div>

      <!-- 模型分发 (拉取模式) -->
      <div class="bg-ind-panel border border-gray-800 rounded-xl p-5 space-y-4 lg:col-span-2">
        <div class="flex justify-between items-center">
          <h3 class="text-base font-bold text-gray-200">模型分发 — 定时拉取</h3>
          <el-switch v-model="form.model_pull.enabled" active-text="启用定时拉取" />
        </div>
        <div class="flex items-center gap-4 flex-wrap text-sm">
          <div class="flex items-center gap-2">
            <span class="text-gray-400 text-xs">轮询间隔(秒)</span>
            <el-input-number v-model="form.model_pull.interval_s" :min="30" :max="86400" :step="60" :precision="0" size="small" controls-position="right" class="!w-28" />
          </div>
          <el-button size="small" :loading="pulling" @click="doPullNow">立即拉取</el-button>
          <span v-if="pullResult" class="text-xs" :class="pullResult.error ? 'text-red-400' : 'text-green-400'">
            {{ pullResult.error
              ? `拉取失败: ${pullResult.error}`
              : `本轮入库 ${pullResult.pulled ?? 0} 个模型包 (跳过重复 ${pullResult.skipped ?? 0})` }}
          </span>
        </div>
        <div class="text-xs text-gray-500">
          两种分发方式并存：<b>推送</b>（训练平台直连本机 models/push，适合平台能访问到设备的局域网单机）；
          <b>拉取</b>（本机定期问训练平台"我的项目有没有新模型"，多台工控机 / 有 NAT·防火墙时用这个——平台不需要能反向连进来）。
        </div>
      </div>
    </div>

    <!-- 运行状态 -->
    <div class="bg-ind-panel border border-gray-800 rounded-xl p-5 space-y-4">
      <div class="flex justify-between items-center">
        <h3 class="text-base font-bold text-gray-200">运行状态</h3>
        <el-button size="small" @click="refreshStatus">刷新</el-button>
      </div>
      <div v-if="status" class="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-4 text-center">
        <div class="stat-cell">
          <div class="text-lg font-bold" :class="status.enabled ? 'text-green-400' : 'text-gray-500'">
            {{ status.enabled ? '已启用' : '未启用' }}
          </div>
          <div class="stat-label">互连开关</div>
        </div>
        <div class="stat-cell">
          <div class="text-lg font-bold" :class="status.sampling_active ? 'text-green-400' : 'text-gray-500'">
            {{ status.sampling_active ? '采样中' : '关闭' }}
          </div>
          <div class="stat-label">帧采样</div>
        </div>
        <div class="stat-cell">
          <div class="text-lg font-bold text-tech-blue">{{ status.uploader?.queue_pending ?? '-' }}</div>
          <div class="stat-label">队列待发</div>
        </div>
        <div class="stat-cell">
          <div class="text-lg font-bold text-green-400">{{ status.uploader?.sent_total ?? 0 }}</div>
          <div class="stat-label">已回传</div>
        </div>
        <div class="stat-cell">
          <div class="text-lg font-bold text-red-400">{{ status.uploader?.failed_total ?? 0 }}</div>
          <div class="stat-label">失败次数</div>
        </div>
        <div class="stat-cell">
          <div class="text-sm font-mono text-gray-300 pt-1">{{ formatTs(status.uploader?.last_success_at) }}</div>
          <div class="stat-label">最近成功回传</div>
        </div>
        <div class="stat-cell">
          <div class="text-lg font-bold text-tech-blue">{{ status.puller?.pulled_total ?? 0 }}</div>
          <div class="stat-label">已拉取模型包</div>
        </div>
        <div class="stat-cell">
          <div class="text-sm font-mono text-gray-300 pt-1">{{ formatTs(status.puller?.last_check_at) }}</div>
          <div class="stat-label">最近拉取检查</div>
        </div>
      </div>
      <div v-if="status?.uploader?.last_error" class="text-xs text-red-400">
        回传错误: {{ status.uploader.last_error }}
      </div>
      <div v-if="status?.puller?.last_error" class="text-xs text-red-400">
        拉取错误: {{ status.puller.last_error }}
      </div>

      <el-table :data="recentSamples" size="small" empty-text="暂无采样记录" max-height="320">
        <el-table-column label="时间" width="160">
          <template #default="{ row }">{{ formatTs(row.created_at) }}</template>
        </el-table-column>
        <el-table-column prop="project_name" label="项目" min-width="140" show-overflow-tooltip />
        <el-table-column label="工位" width="70">
          <template #default="{ row }">{{ (row.channel_id ?? 0) + 1 }}</template>
        </el-table-column>
        <el-table-column label="采样原因" width="110">
          <template #default="{ row }">{{ reasonText(row.reason) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="statusTagType(row.status)" size="small">{{ statusText(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="detail" label="备注" min-width="200" show-overflow-tooltip />
      </el-table>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue';
import { ElMessage } from 'element-plus';
import {
  getInterconnectConfig, saveInterconnectConfig, getInterconnectStatus,
  testInterconnectConnection, getRecentSamples, pullModelsNow,
} from '@/api/interconnect';
import { dbg, dbgErr } from '@/utils/debug';

const loading = ref(false);
const saving = ref(false);
const testing = ref(false);
const testResult = ref(null);
const status = ref(null);
const recentSamples = ref([]);
let refreshTimer = null;

const form = ref({
  enabled: false,
  platform_url: '',
  token: '',
  device_name: '',
  sampling: {
    enabled: false,
    low_conf_enabled: true,
    conf_min: 0.2,
    conf_max: 0.6,
    no_detection_enabled: false,
    no_detection_in_cycle_only: true,
    dropout_enabled: true,
    dropout_min_frames: 5,
    ng_event_enabled: false,
    include_annotations: true,
    min_interval_s: 10,
    max_per_hour: 60,
    jpeg_quality: 85,
  },
  model_pull: {
    enabled: false,
    interval_s: 300,
  },
});

const errText = (e) => {
  const d = e?.response?.data?.detail;
  return typeof d === 'string' ? d
    : Array.isArray(d) ? d.map(x => x.msg || JSON.stringify(x)).join('; ')
    : d ? JSON.stringify(d) : (e?.message || '未知错误');
};

const loadConfig = async () => {
  loading.value = true;
  try {
    const res = await getInterconnectConfig();
    const cfg = res.data || {};
    form.value = {
      ...form.value,
      ...cfg,
      sampling: { ...form.value.sampling, ...(cfg.sampling || {}) },
      model_pull: { ...form.value.model_pull, ...(cfg.model_pull || {}) },
    };
  } catch (e) {
    dbgErr('interconnect', '加载互连配置', e);
    ElMessage.error('加载互连配置失败: ' + errText(e));
  } finally {
    loading.value = false;
  }
};

const saveConfig = async () => {
  saving.value = true;
  try {
    const payload = {
      ...form.value,
      sampling: { ...form.value.sampling },
      model_pull: { ...form.value.model_pull },
    };
    if (!payload.token?.trim()) delete payload.token;
    delete payload.token_configured;
    await saveInterconnectConfig(payload);
    dbg('interconnect', '保存互连配置', `enabled=${form.value.enabled} sampling=${form.value.sampling.enabled}`);
    ElMessage.success('互连配置已保存');
    refreshStatus();
  } catch (e) {
    dbgErr('interconnect', '保存互连配置', e);
    ElMessage.error('保存失败: ' + errText(e));
  } finally {
    saving.value = false;
  }
};

const testConnection = async () => {
  if (!form.value.platform_url) {
    ElMessage.warning('请先填写训练平台地址');
    return;
  }
  testing.value = true;
  testResult.value = null;
  try {
    const res = await testInterconnectConnection({ platform_url: form.value.platform_url });
    testResult.value = res.data;
  } catch (e) {
    testResult.value = { reachable: false, error: errText(e) };
  } finally {
    testing.value = false;
  }
};

const pulling = ref(false);
const pullResult = ref(null);
const doPullNow = async () => {
  pulling.value = true;
  pullResult.value = null;
  try {
    const res = await pullModelsNow();
    pullResult.value = res.data;
    dbg('interconnect', '立即拉取', JSON.stringify(res.data));
    refreshStatus();
  } catch (e) {
    dbgErr('interconnect', '立即拉取', e);
    pullResult.value = { error: errText(e) };
  } finally {
    pulling.value = false;
  }
};

const refreshStatus = async () => {
  try {
    const [st, samples] = await Promise.all([
      getInterconnectStatus(),
      getRecentSamples(50),
    ]);
    status.value = st.data;
    recentSamples.value = samples.data?.items || [];
  } catch (e) {
    dbgErr('interconnect', '刷新互连状态', e);
  }
};

const formatTs = (ts) => {
  if (!ts) return '—';
  return new Date(ts * 1000).toLocaleString();
};

const REASON_MAP = {
  low_confidence: '置信度带',
  no_detection: '未检出',
  detection_dropout: '检出闪断',
  ng_event: 'NG 事件',
  manual: '手动',
};
const reasonText = (r) => REASON_MAP[r] || r || '—';

const STATUS_MAP = { pending: '待发送', sent: '已发送', failed: '失败', dropped: '已丢弃' };
const statusText = (s) => STATUS_MAP[s] || s || '—';
const statusTagType = (s) => (
  s === 'sent' ? 'success' : s === 'failed' ? 'danger' : s === 'dropped' ? 'warning' : 'info'
);

onMounted(() => {
  loadConfig();
  refreshStatus();
  refreshTimer = setInterval(refreshStatus, 5000);
});
onUnmounted(() => {
  if (refreshTimer) clearInterval(refreshTimer);
});
</script>

<style scoped>
.stat-cell {
  @apply bg-gray-900/50 rounded-lg py-3 px-2;
}
.stat-label {
  @apply text-xs text-gray-500 mt-1;
}
</style>
