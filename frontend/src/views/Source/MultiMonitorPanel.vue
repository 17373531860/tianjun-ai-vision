<template>
  <!-- 多屏工位显示：配置持久化在后端，窗口由 Electron 主进程编排。 -->
  <el-card shadow="never" class="bg-slate-800 border-slate-700" data-testid="multi-monitor-card">
    <template #header>
      <div class="flex items-center gap-2">
        <el-icon class="text-cyan-400"><Monitor /></el-icon>
        <span class="font-bold text-white">多屏工位显示</span>
        <el-tag size="small" type="info">桌面版</el-tag>
      </div>
    </template>
    <div class="mb-3 text-xs leading-5 text-gray-500">
      每个工位可绑定一块可操作的检测主屏，并按需增加一块手部裁切副屏。默认关闭，关闭时保持现有单窗口行为。<br>
      显示器 ID 可能因异显坞重插而变化；已保存的 ID 失联时仍保留，并由桌面版优先按记忆坐标降级定位。
    </div>
    <div class="space-y-2" v-loading="multiMonitorLoading">
      <div class="flex items-center justify-between rounded border border-slate-800 bg-slate-900 p-3">
        <div class="flex flex-col">
          <span class="text-gray-300">启用多屏工位显示</span>
          <span class="text-[10px] text-gray-500">开启后按下方映射创建工位主屏与可选副屏窗口</span>
        </div>
        <el-switch v-model="multiMonitorForm.enabled" data-testid="multi-monitor-enabled-switch" />
      </div>
      <div class="flex items-center justify-between rounded border border-slate-800 bg-slate-900 p-3">
        <div class="flex flex-col">
          <span class="text-gray-300">副屏只读</span>
          <span class="text-[10px] text-gray-500">仅约束手部裁切副屏；工位主屏始终保留开始、停止等操作</span>
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
          class="mb-3 rounded border border-slate-800 bg-slate-950/40 p-3 last:mb-0"
          :data-testid="`multi-monitor-channel-row-${channelId}`"
        >
          <div class="mb-2 text-xs font-bold text-gray-300">工位 {{ channelId + 1 }}</div>
          <div class="space-y-2">
            <div class="flex items-center gap-3">
              <span class="w-20 flex-shrink-0 text-xs text-gray-400">主屏显示器</span>
              <el-select
                class="flex-1"
                clearable
                placeholder="不创建工位主屏窗口"
                :model-value="mappingDisplayId(channelId, 'main')"
                :data-testid="`multi-monitor-display-${channelId}`"
                @change="value => onMultiMonitorDisplayChange(channelId, 'main', value)"
              >
                <el-option
                  v-for="display in multiMonitorDisplays"
                  :key="display.id"
                  :label="formatDisplayLabel(display)"
                  :value="String(display.id)"
                />
                <el-option
                  v-if="missingDisplayId(channelId, 'main')"
                  :label="`失联显示器 ${missingDisplayId(channelId, 'main')}（按记忆坐标降级）`"
                  :value="missingDisplayId(channelId, 'main')"
                />
              </el-select>
            </div>
            <div class="flex items-center justify-between gap-3 rounded border border-slate-800 px-3 py-2">
              <div class="flex flex-col">
                <span class="text-xs text-gray-300">启用手部副屏</span>
                <span class="text-[0.625rem] text-gray-500">关闭时该工位只显示完整检测主屏</span>
              </div>
              <el-switch
                :model-value="auxHandsEnabled(channelId)"
                :disabled="!hasMainDisplay(channelId) && !auxHandsEnabled(channelId)"
                :data-testid="`multi-monitor-aux-hands-enabled-${channelId}`"
                @change="enabled => onAuxHandsEnabledChange(channelId, enabled)"
              />
            </div>
            <div v-if="auxHandsEnabled(channelId)" class="flex items-center gap-3">
              <span class="w-20 flex-shrink-0 text-xs text-gray-400">副屏显示器</span>
              <el-select
                class="flex-1"
                clearable
                :disabled="!hasMainDisplay(channelId) && !hasAuxDisplay(channelId)"
                :placeholder="hasMainDisplay(channelId) ? '可空：不创建手部裁切副屏' : '请先选择主屏显示器'"
                :model-value="mappingDisplayId(channelId, 'aux')"
                :data-testid="`multi-monitor-aux-display-${channelId}`"
                @change="value => onMultiMonitorDisplayChange(channelId, 'aux', value)"
              >
                <el-option
                  v-for="display in multiMonitorDisplays"
                  :key="display.id"
                  :label="formatDisplayLabel(display)"
                  :value="String(display.id)"
                />
                <el-option
                  v-if="missingDisplayId(channelId, 'aux')"
                  :label="`失联显示器 ${missingDisplayId(channelId, 'aux')}（按记忆坐标降级）`"
                  :value="missingDisplayId(channelId, 'aux')"
                />
              </el-select>
            </div>
            <div v-if="auxHandsEnabled(channelId)" class="flex items-center gap-3">
              <span class="w-20 flex-shrink-0 text-xs text-gray-400">副屏视角</span>
              <el-select
                class="flex-1"
                :disabled="!hasAuxDisplay(channelId)"
                :model-value="auxViewMode(channelId)"
                :data-testid="`multi-monitor-aux-view-mode-${channelId}`"
                @change="value => onAuxViewModeChange(channelId, value)"
              >
                <el-option label="手部跟随（固定大小）" value="follow" />
                <el-option label="固定中心" value="fixed" />
              </el-select>
            </div>
            <div
              v-if="auxHandsEnabled(channelId)"
              class="pl-[5.75rem] text-[0.625rem] text-gray-500"
            >
              固定中心锁定画面中央；手部跟随仅移动裁切中心，两种模式的视窗大小都固定。
            </div>
            <div
              v-if="auxHandsEnabled(channelId) && !hasMainDisplay(channelId)"
              class="pl-[5.75rem] text-[10px] text-amber-400"
              :data-testid="`multi-monitor-aux-requires-main-${channelId}`"
            >
              副屏必须依附同工位主屏；请恢复主屏，或清空副屏后再保存。
            </div>
            <div
              v-else-if="auxHandsEnabled(channelId) && !hasAuxDisplay(channelId)"
              class="pl-[5.75rem] text-[0.625rem] text-amber-400"
              :data-testid="`multi-monitor-aux-display-required-${channelId}`"
            >
              已启用手部副屏，请选择副屏显示器。
            </div>
          </div>
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

const normalizeDisplayId = (displayId) => (
  displayId === null || displayId === undefined ? '' : String(displayId)
);

const normalizeAuxViewMode = (mode) => (
  String(mode || '').trim().toLowerCase() === 'fixed' ? 'fixed' : 'follow'
);

const roleFields = (role) => role === 'aux'
  ? { display: 'aux_display_id', bounds: 'aux_bounds' }
  : { display: 'display_id', bounds: 'bounds' };

const mappingDisplayId = (channelId, role) => {
  const { display } = roleFields(role);
  return normalizeDisplayId(multiMonitorForm.mapping[String(channelId)]?.[display]);
};

const hasDisplayTarget = (item, role) => {
  const { display, bounds } = roleFields(role);
  return !!(normalizeDisplayId(item?.[display]) || normalizeDisplayBounds(item?.[bounds]));
};

const hasMainDisplay = (channelId) => (
  hasDisplayTarget(multiMonitorForm.mapping[String(channelId)], 'main')
);

const hasAuxDisplay = (channelId) => (
  hasDisplayTarget(multiMonitorForm.mapping[String(channelId)], 'aux')
);

const auxHandsEnabled = (channelId) => (
  multiMonitorForm.mapping[String(channelId)]?.aux_hands_enabled === true
);

const auxViewMode = (channelId) => (
  normalizeAuxViewMode(multiMonitorForm.mapping[String(channelId)]?.aux_view_mode)
);

const applyNormalizedMultiMonitor = (config) => {
  multiMonitorForm.enabled = config?.enabled === true;
  multiMonitorForm.readonly = config?.readonly !== false;
  Object.keys(multiMonitorForm.mapping).forEach((key) => delete multiMonitorForm.mapping[key]);
  Object.entries(config?.mapping || {}).forEach(([channelId, item]) => {
    const displayId = normalizeDisplayId(item?.display_id);
    const auxDisplayId = normalizeDisplayId(item?.aux_display_id);
    const bounds = normalizeDisplayBounds(item?.bounds);
    const auxBounds = normalizeDisplayBounds(item?.aux_bounds);
    const hasAuxTarget = !!(auxDisplayId || auxBounds);
    if (!displayId && !bounds && !auxDisplayId && !auxBounds) return;
    multiMonitorForm.mapping[String(channelId)] = {
      ...(displayId ? { display_id: displayId } : {}),
      ...(bounds ? { bounds } : {}),
      ...(auxDisplayId ? { aux_display_id: auxDisplayId } : {}),
      ...(auxBounds ? { aux_bounds: auxBounds } : {}),
      ...(hasAuxTarget ? {
        aux_hands_enabled: item?.aux_hands_enabled === true,
        aux_view_mode: normalizeAuxViewMode(item?.aux_view_mode),
      } : {}),
    };
  });
};

const formatDisplayLabel = (display) => {
  const bounds = display?.bounds || {};
  const title = display?.label || `显示器 ${display?.id}`;
  const mainWindow = display?.isMainWindowDisplay ? ' · 主窗口当前所在' : '';
  const primary = display?.isPrimary ? ' · OS 主屏' : '';
  const primaryReuse = display?.isPrimary ? '（映射为主屏时保留侧栏）' : '';
  return `${title}${mainWindow}${primary}${primaryReuse} · ${bounds.width || '?'}×${bounds.height || '?'} @ ${bounds.x ?? '?'},${bounds.y ?? '?'}`;
};

const missingDisplayId = (channelId, role) => {
  const saved = mappingDisplayId(channelId, role);
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

function onMultiMonitorDisplayChange(channelId, role, displayId) {
  const key = String(channelId);
  const { display: displayField, bounds: boundsField } = roleFields(role);
  const current = { ...(multiMonitorForm.mapping[key] || {}) };
  if (!displayId) {
    delete current[displayField];
    delete current[boundsField];
    if (role === 'aux') delete current.aux_view_mode;
    if (Object.keys(current).length) multiMonitorForm.mapping[key] = current;
    else delete multiMonitorForm.mapping[key];
    return;
  }
  const selected = multiMonitorDisplays.value.find((display) => String(display.id) === String(displayId));
  const remembered = current[boundsField];
  const bounds = normalizeDisplayBounds(selected?.bounds) || normalizeDisplayBounds(remembered);
  multiMonitorForm.mapping[key] = {
    ...current,
    [displayField]: String(displayId),
    ...(bounds ? { [boundsField]: bounds } : {}),
    ...(role === 'aux' ? { aux_view_mode: normalizeAuxViewMode(current.aux_view_mode) } : {}),
  };
}

function onAuxViewModeChange(channelId, mode) {
  if (!hasAuxDisplay(channelId)) return;
  const key = String(channelId);
  multiMonitorForm.mapping[key] = {
    ...(multiMonitorForm.mapping[key] || {}),
    aux_view_mode: normalizeAuxViewMode(mode),
  };
}

function onAuxHandsEnabledChange(channelId, enabled) {
  const key = String(channelId);
  const current = { ...(multiMonitorForm.mapping[key] || {}) };
  if (enabled === true && !hasDisplayTarget(current, 'main')) return;
  multiMonitorForm.mapping[key] = {
    ...current,
    aux_hands_enabled: enabled === true,
    ...(enabled === true ? {
      aux_view_mode: normalizeAuxViewMode(current.aux_view_mode),
    } : {}),
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
    const orphanAuxEntry = Object.entries(multiMonitorForm.mapping).find(([, item]) => (
      item?.aux_hands_enabled === true && !hasDisplayTarget(item, 'main')
    ));
    if (orphanAuxEntry) {
      const channelLabel = Number(orphanAuxEntry[0]) + 1;
      const message = `工位 ${channelLabel} 的副屏必须先配置主屏显示器`;
      multiMonitorApplySummary.value = message;
      ElMessage.warning(message);
      return;
    }
    const missingAuxEntry = Object.entries(multiMonitorForm.mapping).find(([, item]) => (
      item?.aux_hands_enabled === true && !hasDisplayTarget(item, 'aux')
    ));
    if (missingAuxEntry) {
      const channelLabel = Number(missingAuxEntry[0]) + 1;
      const message = `工位 ${channelLabel} 已启用手部副屏，请选择副屏显示器`;
      multiMonitorApplySummary.value = message;
      ElMessage.warning(message);
      return;
    }
    const payload = {
      enabled: !!multiMonitorForm.enabled,
      readonly: multiMonitorForm.readonly !== false,
      mapping: Object.fromEntries(Object.entries(multiMonitorForm.mapping)
        .filter(([, item]) => normalizeDisplayId(item?.display_id) || normalizeDisplayBounds(item?.bounds))
        .map(([channelId, item]) => {
          const bounds = normalizeDisplayBounds(item?.bounds);
          const auxDisplayId = normalizeDisplayId(item?.aux_display_id);
          const auxBounds = normalizeDisplayBounds(item?.aux_bounds);
          const hasAuxTarget = !!(auxDisplayId || auxBounds);
          return [channelId, {
            display_id: normalizeDisplayId(item?.display_id),
            ...(bounds ? { bounds } : {}),
            ...(auxDisplayId ? { aux_display_id: auxDisplayId } : {}),
            ...(auxBounds ? { aux_bounds: auxBounds } : {}),
            ...(hasAuxTarget ? {
              aux_hands_enabled: item?.aux_hands_enabled === true,
              aux_view_mode: normalizeAuxViewMode(item?.aux_view_mode),
            } : {}),
          }];
        })),
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
