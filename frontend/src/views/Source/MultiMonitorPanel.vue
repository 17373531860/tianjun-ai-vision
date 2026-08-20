<template>
  <!-- 一期多屏工位显示：配置持久化在后端，窗口由 Electron 主进程编排。 -->
  <el-card shadow="never" class="bg-slate-800 border-slate-700" data-testid="multi-monitor-card">
    <template #header>
      <div class="flex items-center gap-2">
        <el-icon class="text-cyan-400"><Monitor /></el-icon>
        <span class="font-bold text-white">多屏工位显示（一期）</span>
        <el-tag size="small" type="info">桌面版</el-tag>
      </div>
    </template>
    <div class="mb-3 text-xs leading-5 text-gray-500">
      主屏保留总览，各工位可绑定到独立显示器并全屏监看。默认关闭、工位副屏默认只读。<br>
      显示器 ID 可能因异显坞重插而变化；已保存的 ID 失联时仍保留，并由桌面版优先按记忆坐标降级定位。
    </div>
    <div class="space-y-2" v-loading="multiMonitorLoading">
      <div class="flex items-center justify-between rounded border border-slate-800 bg-slate-900 p-3">
        <div class="flex flex-col">
          <span class="text-gray-300">启用多屏工位显示</span>
          <span class="text-[10px] text-gray-500">关闭时与现有单窗口完全一致；开启后按下方映射创建副屏窗口</span>
        </div>
        <el-switch v-model="multiMonitorForm.enabled" data-testid="multi-monitor-enabled-switch" />
      </div>
      <div class="flex items-center justify-between rounded border border-slate-800 bg-slate-900 p-3">
        <div class="flex flex-col">
          <span class="text-gray-300">副屏只读</span>
          <span class="text-[10px] text-gray-500">一期建议保持开启；关闭后复用 Monitor 已有控制动作，为二期预留</span>
        </div>
        <el-switch v-model="multiMonitorForm.readonly" data-testid="multi-monitor-readonly-switch" />
      </div>

      <div class="rounded border border-slate-800 bg-slate-900 p-3">
        <div class="mb-2 flex items-center justify-between gap-2">
          <span class="text-sm font-bold text-gray-300">工位 → 显示器映射</span>
          <el-button size="small" :loading="multiMonitorDisplayLoading" data-testid="multi-monitor-refresh" @click="refreshMultiMonitorDisplays">
            <el-icon class="mr-1"><Refresh /></el-icon>刷新显示器
          </el-button>
        </div>
        <div
          v-for="channelId in multiMonitorChannelIds"
          :key="channelId"
          class="mb-2 flex items-center gap-3 last:mb-0"
          :data-testid="`multi-monitor-channel-row-${channelId}`"
        >
          <span class="w-20 flex-shrink-0 text-xs text-gray-400">工位 {{ channelId + 1 }}</span>
          <el-select
            class="flex-1"
            clearable
            placeholder="不创建副屏窗口"
            :model-value="multiMonitorForm.mapping[String(channelId)]?.display_id || ''"
            :data-testid="`multi-monitor-display-${channelId}`"
            @change="value => onMultiMonitorDisplayChange(channelId, value)"
          >
            <el-option
              v-for="display in multiMonitorDisplays"
              :key="display.id"
              :label="formatDisplayLabel(display)"
              :value="String(display.id)"
              :disabled="display.isMainWindowDisplay === true"
            />
            <el-option
              v-if="missingDisplayId(channelId)"
              :label="`失联显示器 ${missingDisplayId(channelId)}（按记忆坐标降级）`"
              :value="missingDisplayId(channelId)"
            />
          </el-select>
        </div>
        <div v-if="!isElectronEnv" class="mt-2 text-[10px] text-amber-400" data-testid="multi-monitor-browser-hint">
          当前为浏览器环境：可保存映射，但无法枚举或立即应用物理显示器；桌面版启动后生效。
        </div>
        <div v-else-if="multiMonitorDisplayError" class="mt-2 text-[10px] text-amber-400">
          显示器枚举失败：{{ multiMonitorDisplayError }}。已有映射会保留，桌面版可按记忆坐标或手工 bounds 降级。
        </div>
      </div>

      <div class="flex items-center justify-between gap-3">
        <div class="text-[10px] text-gray-500" data-testid="multi-monitor-apply-result">{{ multiMonitorApplySummary }}</div>
        <el-button type="primary" :loading="multiMonitorSaving" data-testid="multi-monitor-save" @click="saveAndApplyMultiMonitor">
          保存并应用
        </el-button>
      </div>
    </div>
  </el-card>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue';
import { Monitor, Refresh } from '@element-plus/icons-vue';
import { ElMessage } from 'element-plus';
import api from '@/api/index';
import { getMultiMonitorConfig, setMultiMonitorConfig } from '@/api/detection';

const isElectronEnv = computed(() => !!(typeof window !== 'undefined' && window.electronAPI?.isElectron));

const totalChannels = ref(1);
const multiMonitorLoading = ref(false);
const multiMonitorSaving = ref(false);
const multiMonitorDisplayLoading = ref(false);
const multiMonitorDisplayError = ref('');
const multiMonitorApplySummary = ref('');
const multiMonitorDisplays = ref([]);
const multiMonitorForm = reactive({ enabled: false, readonly: true, mapping: {} });
const multiMonitorChannelIds = computed(() =>
  Array.from({ length: Math.max(1, totalChannels.value || 1) }, (_, channelId) => channelId)
);

async function loadTotalChannels() {
  try {
    const res = await api.get('/workstations');
    totalChannels.value = res?.data?.channel_count || 1;
  } catch {
    totalChannels.value = 1;
  }
}

const normalizeDisplayBounds = (bounds) => {
  if (!bounds) return null;
  const normalized = {
    x: Number(bounds.x),
    y: Number(bounds.y),
    width: Number(bounds.width),
    height: Number(bounds.height),
  };
  return Number.isFinite(normalized.x) && Number.isFinite(normalized.y)
    && Number.isFinite(normalized.width) && normalized.width > 0
    && Number.isFinite(normalized.height) && normalized.height > 0
    ? normalized
    : null;
};

const applyNormalizedMultiMonitor = (config) => {
  multiMonitorForm.enabled = config?.enabled === true;
  multiMonitorForm.readonly = config?.readonly !== false;
  Object.keys(multiMonitorForm.mapping).forEach((key) => delete multiMonitorForm.mapping[key]);
  Object.entries(config?.mapping || {}).forEach(([channelId, item]) => {
    const displayId = String(item?.display_id || '');
    const bounds = normalizeDisplayBounds(item?.bounds);
    if (!displayId && !bounds) return;
    multiMonitorForm.mapping[String(channelId)] = {
      display_id: displayId,
      ...(bounds ? { bounds } : {}),
    };
  });
};

const formatDisplayLabel = (display) => {
  const bounds = display?.bounds || {};
  const title = display?.label || `显示器 ${display?.id}`;
  const mainWindow = display?.isMainWindowDisplay ? ' · 主窗口（保留总览，不可绑定）' : '';
  const primary = display?.isPrimary ? ' · OS 主屏' : '';
  return `${title}${mainWindow}${primary} · ${bounds.width || '?'}×${bounds.height || '?'} @ ${bounds.x ?? '?'},${bounds.y ?? '?'}`;
};

const isMainWindowDisplayId = (displayId) => multiMonitorDisplays.value.some((display) => (
  display?.isMainWindowDisplay === true && String(display.id) === String(displayId)
));

const missingDisplayId = (channelId) => {
  const saved = multiMonitorForm.mapping[String(channelId)]?.display_id;
  if (!saved) return '';
  return multiMonitorDisplays.value.some((display) => String(display.id) === String(saved)) ? '' : String(saved);
};

async function refreshMultiMonitorDisplays() {
  multiMonitorDisplayError.value = '';
  if (!isElectronEnv.value || !window.electronAPI?.getDisplays) {
    multiMonitorDisplays.value = [];
    return;
  }
  multiMonitorDisplayLoading.value = true;
  try {
    const result = await window.electronAPI.getDisplays();
    if (!result?.ok) throw new Error(result?.error || '未知错误');
    multiMonitorDisplays.value = Array.isArray(result.displays) ? result.displays : [];
  } catch (error) {
    multiMonitorDisplays.value = [];
    multiMonitorDisplayError.value = error?.message || String(error);
  } finally {
    multiMonitorDisplayLoading.value = false;
  }
}

function onMultiMonitorDisplayChange(channelId, displayId) {
  const key = String(channelId);
  if (!displayId) {
    delete multiMonitorForm.mapping[key];
    return;
  }
  const selected = multiMonitorDisplays.value.find((display) => String(display.id) === String(displayId));
  if (selected?.isMainWindowDisplay) {
    ElMessage.warning('主窗口所在显示器必须保留总览，请选择其他显示器');
    return;
  }
  const remembered = multiMonitorForm.mapping[key]?.bounds;
  const bounds = normalizeDisplayBounds(selected?.bounds) || normalizeDisplayBounds(remembered);
  multiMonitorForm.mapping[key] = {
    display_id: String(displayId),
    ...(bounds ? { bounds } : {}),
  };
}

async function loadMultiMonitorConfig() {
  multiMonitorLoading.value = true;
  try {
    const response = await getMultiMonitorConfig();
    applyNormalizedMultiMonitor(response?.data || {});
  } catch (error) {
    ElMessage.warning('多屏工位配置加载失败，已按默认关闭显示');
    applyNormalizedMultiMonitor({ enabled: false, readonly: true, mapping: {} });
  } finally {
    multiMonitorLoading.value = false;
  }
}

async function saveAndApplyMultiMonitor() {
  multiMonitorSaving.value = true;
  multiMonitorApplySummary.value = '';
  try {
    const payload = {
      enabled: !!multiMonitorForm.enabled,
      readonly: multiMonitorForm.readonly !== false,
      mapping: Object.fromEntries(Object.entries(multiMonitorForm.mapping)
        .filter(([, item]) => !isMainWindowDisplayId(item?.display_id))
        .map(([channelId, item]) => [channelId, {
          display_id: String(item?.display_id || ''),
          ...(normalizeDisplayBounds(item?.bounds) ? { bounds: normalizeDisplayBounds(item.bounds) } : {}),
        }])),
    };
    const response = await setMultiMonitorConfig(payload);
    const normalized = response?.data || payload;
    applyNormalizedMultiMonitor(normalized);
    if (isElectronEnv.value && window.electronAPI?.applyMultiMonitor) {
      const result = await window.electronAPI.applyMultiMonitor(normalized);
      const warnings = Array.isArray(result?.warnings) ? result.warnings : [];
      const windows = Array.isArray(result?.windows) ? result.windows : [];
      multiMonitorApplySummary.value = result?.ok
        ? `已应用：${windows.length} 个工位窗口${warnings.length ? `；${warnings.join('；')}` : ''}`
        : `配置已保存，但应用失败：${result?.error || warnings.join('；') || '未知错误'}`;
      if (result?.ok && !warnings.length) ElMessage.success('多屏配置已保存并应用');
      else if (result?.ok) ElMessage.warning(multiMonitorApplySummary.value);
      else ElMessage.warning(multiMonitorApplySummary.value);
    } else {
      multiMonitorApplySummary.value = '已保存；请在桌面版中应用或重启后生效';
      ElMessage.success('多屏配置已保存，桌面版生效');
    }
  } catch (error) {
    ElMessage.error('保存多屏配置失败: ' + (error?.response?.data?.detail || error?.message || ''));
  } finally {
    multiMonitorSaving.value = false;
  }
}

onMounted(() => {
  loadTotalChannels();
  loadMultiMonitorConfig();
  refreshMultiMonitorDisplays();
});
</script>
