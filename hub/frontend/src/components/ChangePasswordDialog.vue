<template>
  <div class="hub-modal-mask" data-test="pwd-dialog" @click.self="$emit('close')">
    <form class="hub-modal" @submit.prevent="onSubmit">
      <h2>修改密码</h2>
      <label>当前密码
        <input v-model="oldPassword" type="password" class="hub-input"
               data-test="pwd-old" required autocomplete="current-password" />
      </label>
      <label>新密码（至少 6 位）
        <input v-model="newPassword" type="password" class="hub-input"
               data-test="pwd-new" required autocomplete="new-password" />
      </label>
      <p v-if="error" class="hub-error" data-test="pwd-error">{{ error }}</p>
      <div class="hub-modal-actions">
        <button type="button" class="hub-btn ghost" @click="$emit('close')">取消</button>
        <button type="submit" class="hub-btn" data-test="pwd-submit" :disabled="saving">
          {{ saving ? '提交中…' : '确认修改' }}
        </button>
      </div>
    </form>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import api from '../api'
import { clearAuth } from '../auth'

const emit = defineEmits(['close'])
const router = useRouter()
const oldPassword = ref('')
const newPassword = ref('')
const error = ref('')
const saving = ref(false)

async function onSubmit() {
  error.value = ''
  saving.value = true
  try {
    await api.post('/auth/change-password', {
      old_password: oldPassword.value,
      new_password: newPassword.value,
    })
    // 改密后端吊销全部会话 → 引导重新登录
    clearAuth()
    router.push({ name: 'login' })
  } catch (e) {
    error.value = e.response?.data?.detail || '修改失败'
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
/* 样式全部走 App.vue 全局 hub-* 类 */
</style>