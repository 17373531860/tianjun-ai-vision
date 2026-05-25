<template>
  <div class="min-h-screen bg-gradient-to-br from-slate-950 via-slate-900 to-slate-950 flex items-center justify-center p-4">
    <div class="w-full max-w-md">
      <div class="bg-slate-800/80 backdrop-blur-sm border border-slate-700 rounded-2xl shadow-2xl p-8">
        <!-- 头部 logo -->
        <div class="text-center mb-8">
          <div class="w-16 h-16 bg-gradient-to-br from-cyan-500 to-blue-600 rounded-2xl flex items-center justify-center mx-auto mb-4 shadow-lg shadow-cyan-500/20">
            <el-icon :size="32" class="text-white"><UserFilled /></el-icon>
          </div>
          <h1 class="text-2xl font-bold text-white mb-1">账号登录</h1>
          <p class="text-slate-400 text-sm">天军科技AI视觉检测系统</p>
        </div>

        <!-- 鉴权未启用提示 -->
        <div v-if="!authEnabled" class="mb-6 p-4 rounded-lg bg-amber-900/30 border border-amber-700/50 text-amber-200 text-sm">
          <p class="font-medium mb-1">账号鉴权当前未启用</p>
          <p class="text-amber-300/80 text-xs">无需登录即可进入系统。如需启用账号鉴权请前往「设置 → 账号鉴权」。</p>
          <el-button type="primary" size="small" class="mt-3" @click="goHome">直接进入系统</el-button>
        </div>

        <!-- 登录表单 -->
        <el-form
          v-else
          ref="formRef"
          :model="form"
          :rules="rules"
          label-position="top"
          @submit.prevent="onSubmit"
        >
          <el-form-item label="用户名" prop="username">
            <el-input
              v-model="form.username"
              size="large"
              placeholder="请输入用户名"
              :prefix-icon="User"
              autocomplete="username"
              @keyup.enter="onSubmit"
            />
          </el-form-item>

          <el-form-item label="密码" prop="password">
            <el-input
              v-model="form.password"
              type="password"
              size="large"
              placeholder="请输入密码"
              :prefix-icon="Lock"
              autocomplete="current-password"
              show-password
              @keyup.enter="onSubmit"
            />
          </el-form-item>

          <!-- 错误提示 -->
          <div v-if="errorMessage" class="mb-4 p-3 rounded-lg text-sm bg-red-900/40 border border-red-700/50 text-red-300">
            {{ errorMessage }}
          </div>

          <el-button
            type="primary"
            size="large"
            class="w-full"
            :loading="submitting"
            @click="onSubmit"
          >
            登录
          </el-button>

          <!-- 匿名进入入口 -->
          <div class="mt-4 text-center">
            <el-button text @click="goHome" class="!text-slate-400">以操作员身份继续 (无需登录)</el-button>
          </div>
        </el-form>

        <!-- 版本号 -->
        <div class="mt-8 text-center text-slate-600 text-xs">
          v{{ appVersion }}
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue';
import { useRouter, useRoute } from 'vue-router';
import { ElMessage } from 'element-plus';
import { User, Lock, UserFilled } from '@element-plus/icons-vue';
import { useAuthStore } from '@/store/useAuthStore';

const router = useRouter();
const route = useRoute();
const authStore = useAuthStore();

const formRef = ref(null);
const form = reactive({ username: '', password: '' });
const rules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
};
const submitting = ref(false);
const errorMessage = ref('');
const appVersion = ref('');

const authEnabled = computed(() => authStore.authEnabled);

onMounted(async () => {
  // 进入登录页前确保 store 已 init (拿到最新 auth_enabled)
  await authStore.init();

  // 已经登录就直接回去 (避免重复登录)
  if (authStore.isLoggedIn) {
    goHome();
    return;
  }

  // 尝试获取版本号
  try {
    if (window.electronAPI?.getAppInfo) {
      const info = await window.electronAPI.getAppInfo();
      appVersion.value = info.version || '';
    }
  } catch {}
});

function goHome() {
  // 优先回 redirect query, 否则回首页
  const redirect = (route.query.redirect && String(route.query.redirect)) || '/';
  router.replace(redirect);
}

async function onSubmit() {
  errorMessage.value = '';
  if (!formRef.value) return;
  try {
    await formRef.value.validate();
  } catch {
    return;
  }
  submitting.value = true;
  try {
    await authStore.login(form.username, form.password);
    ElMessage.success(`欢迎, ${authStore.currentUser.display_name || authStore.currentUser.username}`);
    goHome();
  } catch (err) {
    const detail = err?.response?.data?.detail || err?.message || '登录失败';
    errorMessage.value = detail;
  } finally {
    submitting.value = false;
  }
}
</script>
