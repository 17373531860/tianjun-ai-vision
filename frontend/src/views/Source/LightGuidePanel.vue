<template>
  <!-- 投影光引导：标定状态 + 引导参数。参数全局一份存 SystemConfig KV，
       投影窗每次进入引导态拉取；标定本身 per 工位（在投影窗内完成）。 -->
  <el-card shadow="never" class="bg-slate-800 border-slate-700 mt-3" data-testid="lightguide-card">
    <template #header>
      <div class="flex items-center gap-2">
        <el-icon class="text-cyan-400"><VideoCamera /></el-icon>
        <span class="font-bold text-white">投影光引导</span>
        <el-tag size="small" type="info">需投影仪</el-tag>
      </div>
    </template>
    <div class="mb-3 text-xs leading-5 text-gray-500">
      投影仪向工作台投射装配引导（步骤高亮/OK·NG 反馈/悬停确认）。上方多屏映射中把显示口设为「投影光引导」，
      或直接打开投影窗完成标定（快捷键 C 自动标定）。
    </div>

    <div v-loading="loading" class="space-y-3">
      <!-- 工位标定状态 -->
      <div class="rounded border border-slate-800 bg-slate-900 p-3">
        <div class="mb-2 flex items-center justify-between">
          <span class="text-sm font-bold text-gray-300">工位标定状态</span>
          <el-button size="small" @click="loadStatuses">
            <el-icon class="mr-1"><Refresh /></el-icon>刷新
          </el-button>
        </div>
        <div
          v-for="st in statuses"
          :key="st.channel"
          class="mb-2 flex items-center gap-3 last:mb-0"
          :data-testid="`lightguide-status-${st.channel}`"
        >
          <span class="w-20 flex-shrink-0 text-xs text-gray-400">工位 {{ st.channel + 1 }}</span>
          <el-tag v-if="st.calibrated" size="small" type="success">
            已标定 · 误差 {{ st.calibration?.reproj_error_px ?? '?' }}px · {{ st.calibration?.calibrated_at || '' }}
          </el-tag>
          <el-tag v-else size="small" type="info">未标定</el-tag>
          <div class="flex-1"></div>
          <el-button size="small" :data-testid="`lightguide-open-${st.channel}`" @click="openProjection(st.channel)">
            打开投影窗
          </el-button>
          <el-button
            v-if="st.calibrated"
            size="small"
            type="danger"
            plain
            :data-testid="`lightguide-clear-${st.channel}`"
            @click="clearCalibration(st.channel)"
          >
            清除标定
          </el-button>
        </div>
      </div>

      <!-- 引导参数 -->
      <div class="rounded border border-slate-800 bg-slate-900 p-3">
        <div class="mb-2 text-sm font-bold text-gray-300">引导参数（全局）</div>
        <div class="grid grid-cols-1 gap-x-8 gap-y-2 md:grid-cols-2">
          <div class="param-row">
            <span class="param-label">漂移自检周期 (秒)</span>
            <el-input-number v-model="form.drift_interval_s" :min="5" :max="600" :step="5" size="small" data-testid="lightguide-param-drift-interval" />
          </div>
          <div class="param-row">
            <span class="param-label">漂移阈值 (px)</span>
            <el-input-number v-model="form.drift_threshold_px" :min="2" :max="50" :step="1" size="small" data-testid="lightguide-param-drift-threshold" />
          </div>
          <div class="param-row">
            <span class="param-label">漂移后自动重标</span>
            <el-switch v-model="form.auto_recalibrate" data-testid="lightguide-param-auto-recal" />
          </div>
          <div class="param-row">
            <span class="param-label">悬停确认时长 (ms)</span>
            <el-input-number v-model="form.hover_dwell_ms" :min="400" :max="5000" :step="100" size="small" data-testid="lightguide-param-hover-dwell" />
          </div>
          <div class="param-row">
            <span class="param-label">悬停亮度阈值</span>
            <el-input-number v-model="form.hover_delta" :min="5" :max="60" :step="1" size="small" data-testid="lightguide-param-hover-delta" />
          </div>
          <div class="param-row">
            <span class="param-label">悬停采样周期 (ms)</span>
            <el-input-number v-model="form.hover_poll_ms" :min="100" :max="1000" :step="50" size="small" data-testid="lightguide-param-hover-poll" />
          </div>
          <div class="param-row">
            <span class="param-label">标定曝光等待 (ms)</span>
            <el-input-number v-model="form.calib_settle_ms" :min="300" :max="5000" :step="100" size="small" data-testid="lightguide-param-settle" />
          </div>
          <div class="param-row">
            <span class="param-label">标定阵 列×行</span>
            <div class="flex items-center gap-1">
              <el-input-number v-model="form.pattern_cols" :min="2" :max="8" size="small" class="!w-20" data-testid="lightguide-param-cols" />
              <span class="text-gray-500">×</span>
              <el-input-number v-model="form.pattern_rows" :min="2" :max="6" size="small" class="!w-20" data-testid="lightguide-param-rows" />
            </div>
          </div>
          <div class="param-row">
            <span class="param-label">引导画面亮度</span>
            <el-slider v-model="form.brightness" :min="0.3" :max="1" :step="0.05" class="!w-40" data-testid="lightguide-param-brightness" />
          </div>
          <div class="param-row">
            <span class="param-label">流向引导路径动效</span>
            <el-switch v-model="form.flow_path" data-testid="lightguide-param-flow" />
          </div>
        </div>
        <div class="mt-1 text-[10px] text-gray-500">
          锚点与悬停确认按钮不受亮度系数影响（相机需要原始对比度）。参数改动在投影窗下次进入引导时生效。
        </div>
      </div>

      <div class="flex items-center justify-end gap-3">
        <el-button data-testid="lightguide-reset" @click="resetDefaults">恢复默认</el-button>
        <el-button type="primary" :loading="saving" data-testid="lightguide-save" @click="saveParams">保存参数</el-button>
      </div>
    </div>
  </el-card>
</template>

<script setup>
import { reactive, ref, onMounted } from 'vue';
import { Refresh, VideoCamera } from '@element-plus/icons-vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import api from '@/api/index';
import {
  deleteLightguideConfig, getLightguideConfig, getLightguideParams,
  LIGHTGUIDE_PARAM_DEFAULTS, setLightguideParams,
} from '@/api/lightguide';

const loading = ref(false);
const saving = ref(false);
const statuses = ref([]);
const form = reactive({ ...LIGHTGUIDE_PARAM_DEFAULTS });

async function loadStatuses() {
  let count = 1;
  try {
    const res = await api.get('/workstations');
    count = res?.data?.channel_count || 1;
  } catch { /* 默认单工位 */ }
  const list = [];
  for (let ch = 0; ch < count; ch++) {
    try {
      const { data } = await getLightguideConfig(ch);
      list.push({ channel: ch, calibrated: data.calibrated === true, calibration: data.calibration });
    } catch {
      list.push({ channel: ch, calibrated: false, calibration: null });
    }
  }
  statuses.value = list;
}

async function loadParams() {
  try {
    const { data } = await getLightguideParams();
    Object.assign(form, data.params || {});
  } catch {
    ElMessage.warning('引导参数加载失败，显示出厂默认');
  }
}

async function saveParams() {
  saving.value = true;
  try {
    const { data } = await setLightguideParams({ ...form });
    Object.assign(form, data.params || {});
    ElMessage.success('引导参数已保存（投影窗下次进入引导时生效）');
  } catch (error) {
    ElMessage.error('保存失败: ' + (error?.response?.data?.detail || error?.message || ''));
  } finally {
    saving.value = false;
  }
}

function resetDefaults() {
  Object.assign(form, LIGHTGUIDE_PARAM_DEFAULTS);
  ElMessage.info('已填入出厂默认，点「保存参数」生效');
}

function openProjection(channel) {
  window.open(`${window.location.origin}${window.location.pathname}#/projection?channel=${channel}`, '_blank');
}

async function clearCalibration(channel) {
  try {
    await ElMessageBox.confirm(
      `清除工位 ${channel + 1} 的投影标定？清除后该工位需重新自动标定。`,
      '清除标定', { type: 'warning', confirmButtonText: '清除', cancelButtonText: '取消' },
    );
  } catch { return; }
  try {
    await deleteLightguideConfig(channel);
    ElMessage.success('已清除，重新打开投影窗按 C 即可重标');
    loadStatuses();
  } catch (error) {
    ElMessage.error('清除失败: ' + (error?.response?.data?.detail || error?.message || ''));
  }
}

onMounted(async () => {
  loading.value = true;
  await Promise.all([loadStatuses(), loadParams()]);
  loading.value = false;
});
</script>

<style scoped>
.param-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
}
.param-label {
  font-size: 12px;
  color: #94a3b8;
  flex-shrink: 0;
}
</style>
