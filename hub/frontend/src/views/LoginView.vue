<template>
  <div class="login-page">
    <form class="login-card" @submit.prevent="onSubmit">
      <div class="logo">◈</div>
      <h1>集中管控枢纽</h1>
      <p class="sub">天军 AI 视觉检测 · Fleet Hub</p>
      <input v-model="username" class="hub-input" data-test="login-username"
             placeholder="用户名" autocomplete="username" />
      <input v-model="password" class="hub-input" data-test="login-password" type="password"
             placeholder="密码" autocomplete="current-password" />
      <button type="submit" class="hub-btn submit" data-test="login-submit" :disabled="loading">
        {{ loading ? '登录中…' : '登录' }}
      </button>
      <p v-if="error" class="login-error" data-test="login-error">{{ error }}</p>
    </form>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { login } from '../auth'

const router = useRouter()
const username = ref('')
const password = ref('')
const loading = ref(false)
const error = ref('')

async function onSubmit() {
  error.value = ''
  loading.value = true
  try {
    await login(username.value, password.value)
    router.push({ name: 'wall' })
  } catch (e) {
    error.value = e.response?.data?.detail || '登录失败，请检查网络'
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-page {
  height: 100%; display: flex; align-items: center; justify-content: center;
  background: var(--hub-bg);
}
/* 居中单列卡 (IBM Carbon 登录模式): 登录卡确实浮着, 允许一层软阴影 */
.login-card {
  width: 380px; padding: 40px 36px; border-radius: var(--hub-radius-lg);
  background: var(--hub-panel); border: 1px solid var(--hub-border);
  box-shadow: 0 1px 2px rgba(0, 0, 0, .24), 0 16px 32px -8px rgba(0, 0, 0, .40);
  display: flex; flex-direction: column; gap: 16px;
}
.logo { text-align: center; color: var(--hub-primary); font-size: 28px; }
h1 { font-size: 20px; font-weight: 600; color: var(--hub-text); text-align: center; }
.sub {
  text-align: center; color: var(--hub-text-3);
  font-size: 13px; margin-bottom: 8px;
}
.submit { margin-top: 4px; height: 40px; font-size: 14px; }
.login-error {
  text-align: center; font-size: 13px; padding: 8px 12px;
  border-radius: var(--hub-radius);
  background: var(--hub-ng-bg); color: var(--hub-ng);
  border: 1px solid var(--hub-ng-bd);
}
</style>
