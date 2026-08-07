<template>
  <div class="min-h-screen bg-gradient-to-br from-slate-950 via-slate-900 to-slate-950 flex items-center justify-center p-4">
    <div class="w-full max-w-lg">
      <div class="bg-slate-800/80 backdrop-blur-sm border border-slate-700 rounded-2xl shadow-2xl p-8">
        <!-- Logo & Title -->
        <div class="text-center mb-8">
          <div class="w-16 h-16 bg-gradient-to-br from-cyan-500 to-blue-600 rounded-2xl flex items-center justify-center mx-auto mb-4 shadow-lg shadow-cyan-500/20">
            <el-icon :size="32" class="text-white"><Lock /></el-icon>
          </div>
          <h1 class="text-2xl font-bold text-white mb-1">天军科技AI视觉检测系统</h1>
          <p class="text-slate-400 text-sm">Software Activation</p>
        </div>

        <!-- Machine ID -->
        <div class="mb-6">
          <label class="block text-slate-400 text-xs font-medium mb-2 uppercase tracking-wide">Machine ID</label>
          <div class="flex items-center gap-2">
            <div class="flex-1 bg-slate-900 border border-slate-600 rounded-lg px-4 py-3 font-mono text-cyan-400 text-lg tracking-widest select-all">
              {{ machineId || 'Loading...' }}
            </div>
            <el-button :icon="CopyDocument" circle @click="copyMachineId" />
          </div>
          <p class="text-slate-500 text-xs mt-2">Please send this Machine ID to your vendor for activation</p>
        </div>

        <!-- Status Message -->
        <div v-if="statusMessage" class="mb-6 p-3 rounded-lg text-sm" :class="statusClass">
          <el-icon v-if="starting" class="animate-spin mr-1 align-middle"><Loading /></el-icon>
          {{ statusMessage }}
        </div>

        <!-- Import Button -->
        <el-button
          type="primary"
          size="large"
          class="w-full"
          :loading="importing || starting"
          :disabled="starting"
          @click="importLicense"
        >
          <el-icon class="mr-2"><Upload /></el-icon>
          Import License File
        </el-button>

        <!-- License Info (if exists but invalid) -->
        <div v-if="licenseError" class="mt-4 text-center">
          <p class="text-red-400 text-xs">{{ licenseError }}</p>
        </div>

        <!-- Version -->
        <div class="mt-8 text-center text-slate-600 text-xs">
          v{{ appVersion }}
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue';
import { useRouter } from 'vue-router';
import { Lock, CopyDocument, Upload, Loading } from '@element-plus/icons-vue';
import { ElMessage } from 'element-plus';

const router = useRouter();

const machineId = ref('');
const appVersion = ref('');
const statusMessage = ref('');
const statusClass = ref('');
const licenseError = ref('');
const importing = ref(false);
// 激活成功后, 后端在主进程后台冷启动 (首次可能要几分钟), 期间保持"启动中"状态;
// license-activated 到达 → 跳主页; license-backend-start-failed 到达 → 显示错误
const starting = ref(false);

const isElectron = !!(window.electronAPI?.isElectron);

onMounted(async () => {
  if (!isElectron) {
    router.replace('/');
    return;
  }

  try {
    const info = await window.electronAPI.getAppInfo();
    appVersion.value = info.version || '';

    const status = await window.electronAPI.getLicenseStatus();
    machineId.value = status.machineId || '';

    if (status.valid) {
      router.replace('/');
      return;
    }

    window.electronAPI.onLicenseActivated(() => {
      router.replace('/');
    });

    // 老 preload 没有这个桥时跳过 (可选链), 不影响激活主流程
    window.electronAPI.onLicenseBackendStartFailed?.((payload) => {
      starting.value = false;
      statusMessage.value = 'Activation succeeded, but backend failed to start. Please restart the application.';
      statusClass.value = 'bg-red-900/50 border border-red-700 text-red-300';
      licenseError.value = payload?.message || 'Backend startup failed';
    });
  } catch (err) {
    console.error('Failed to get license status:', err);
  }
});

const copyMachineId = async () => {
  if (!machineId.value) return;
  try {
    await navigator.clipboard.writeText(machineId.value);
    ElMessage.success('Machine ID copied');
  } catch {
    ElMessage.error('Copy failed');
  }
};

const importLicense = async () => {
  if (!isElectron) return;
  importing.value = true;
  statusMessage.value = '';
  licenseError.value = '';

  try {
    const result = await window.electronAPI.importLicense();

    if (result.reason === 'cancelled') {
      importing.value = false;
      return;
    }

    if (result.valid) {
      // 主进程已改为立即返回验签结果、后端后台启动 — 这里进入"启动中"等待态
      starting.value = true;
      statusMessage.value = 'Activation successful! System is starting, the first launch may take a few minutes...';
      statusClass.value = 'bg-green-900/50 border border-green-700 text-green-300';
    } else {
      licenseError.value = result.message || 'Activation failed';
      statusMessage.value = 'Activation failed';
      statusClass.value = 'bg-red-900/50 border border-red-700 text-red-300';
    }
  } catch (err) {
    licenseError.value = err.message;
    statusMessage.value = 'Error during activation';
    statusClass.value = 'bg-red-900/50 border border-red-700 text-red-300';
  }

  importing.value = false;
};
</script>
