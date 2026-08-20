<template>
        <div class="space-y-6 p-4">
          <el-card shadow="never" class="bg-slate-800 border-slate-700">
            <template #header>
              <div class="flex items-center gap-2">
                <el-icon class="text-tech-blue"><Refresh /></el-icon>
                <span class="font-bold text-white">管理面板刷新间隔</span>
              </div>
            </template>
            <el-alert type="info" :closable="false" show-icon class="mb-4">
              <template #default>
                <div class="text-xs text-gray-300">
                  仅控制各管理面板的数据刷新频率（毫秒），不影响检测主循环。最小 500ms，留空/非法值回落默认。
                </div>
              </template>
            </el-alert>
            <div class="grid grid-cols-2 gap-4">
              <div v-for="item in pollingItems" :key="item.key"
                   class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <div>
                  <span class="text-gray-300">{{ item.label }}</span>
                  <div class="text-xs text-gray-500 mt-1">默认 {{ pollingDefaults[item.key] }} ms</div>
                </div>
                <el-input-number
                  v-model="pollingForm[item.key]"
                  :min="500" :max="600000" :step="500" controls-position="right"
                  size="small" style="width: 140px"
                  @change="savePollingItem(item.key)"
                />
              </div>
            </div>
          </el-card>

          <el-card shadow="never" class="bg-slate-800 border-slate-700">
            <template #header>
              <div class="flex items-center gap-2">
                <el-icon class="text-tech-blue"><DataLine /></el-icon>
                <span class="font-bold text-white">日志显示条数</span>
              </div>
            </template>
            <el-alert type="info" :closable="false" show-icon class="mb-4">
              <template #default>
                <div class="text-xs text-gray-300">
                  控制各管理面板「最近日志」一次显示/分页的条数（1–500），越界回落默认。
                </div>
              </template>
            </el-alert>
            <div class="grid grid-cols-2 gap-4">
              <div v-for="item in logLimitItems" :key="item.key"
                   class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <div>
                  <span class="text-gray-300">{{ item.label }}</span>
                  <div class="text-xs text-gray-500 mt-1">默认 {{ logLimitDefaults[item.key] }} 条</div>
                </div>
                <el-input-number
                  v-model="logLimitForm[item.key]"
                  :min="1" :max="500" :step="10" controls-position="right"
                  size="small" style="width: 140px"
                  @change="saveLogLimitItem(item.key)"
                />
              </div>
            </div>
          </el-card>
        </div>
</template>

<script setup>
import { reactive, onMounted } from 'vue';
import { Refresh, DataLine } from '@element-plus/icons-vue';
import { ElMessage } from 'element-plus';
import { usePollingStore } from '@/store/usePollingStore';

const pollingStore = usePollingStore();

// ==================== 轮询间隔配置 ====================
const pollingItems = [
  { key: 'cluster_boxes', label: '集群-包装箱列表' },
  { key: 'cluster_slaves', label: '集群-在线副机' },
  { key: 'cluster_heartbeat', label: '集群-心跳' },
  { key: 'gateway_health', label: '网关-健康探测' },
  { key: 'order_list', label: '工单-列表刷新' },
  { key: 'scanner_status', label: '扫码器-状态' },
  { key: 'external_device', label: '外设-状态' },
  { key: 'wmax_status', label: 'WMax-状态' },
  { key: 'plc_status', label: 'PLC-连接状态' },
  { key: 'plc_live', label: 'PLC-点位实时值' },
  { key: 'trigger_status', label: '触发中心-列表' },
  { key: 'trigger_live', label: '触发中心-实时状态' },
];
const pollingDefaults = pollingStore.defaults();
const pollingForm = reactive({ ...pollingDefaults });
const loadPolling = async () => {
  const intervals = await pollingStore.load(true);
  Object.assign(pollingForm, intervals);
  Object.assign(logLimitForm, pollingStore.logLimits);
};
const savePollingItem = async (key) => {
  const val = pollingForm[key];
  if (typeof val !== 'number' || val < 500) {
    pollingForm[key] = pollingDefaults[key];
    return;
  }
  try {
    await pollingStore.save({ [key]: val });
    ElMessage.success('已保存，重新进入对应面板生效');
  } catch (e) {
    ElMessage.error('保存失败');
    await loadPolling();
  }
};

// ==================== 日志显示条数配置 ====================
const logLimitItems = [
  { key: 'scanner', label: '扫码器-日志' },
  { key: 'external_device', label: '外设-日志' },
  { key: 'inbound', label: '入站工单-日志' },
  { key: 'gateway', label: '网关-日志(分页)' },
  { key: 'cluster', label: '集群-汇总(分页)' },
  { key: 'plc', label: 'PLC-IO 日志' },
  { key: 'trigger', label: '触发中心-日志' },
];
const logLimitDefaults = pollingStore.logDefaults();
const logLimitForm = reactive({ ...logLimitDefaults });
const saveLogLimitItem = async (key) => {
  const val = logLimitForm[key];
  if (typeof val !== 'number' || val < 1 || val > 500) {
    logLimitForm[key] = logLimitDefaults[key];
    return;
  }
  try {
    await pollingStore.saveLogLimits({ [key]: val });
    ElMessage.success('已保存，重新进入对应面板生效');
  } catch (e) {
    ElMessage.error('保存失败');
  }
};

onMounted(() => {
  loadPolling();  // B3: 各管理面板轮询间隔
});
</script>
