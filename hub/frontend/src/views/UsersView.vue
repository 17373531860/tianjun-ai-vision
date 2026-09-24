<template>
  <div class="users-page">
    <header class="hub-topbar">
      <button class="hub-back" data-test="back-wall" @click="router.push({ name: 'wall' })">
        ← 检测集群
      </button>
      <h1>用户管理</h1>
      <button class="hub-btn sm create" data-test="user-create-open" @click="openCreate">
        新建用户
      </button>
    </header>

    <main class="content">
      <p v-if="loadError" class="hub-load-error">{{ loadError }}</p>
      <div v-else-if="users" class="table-card">
      <table class="hub-table" data-test="users-table">
        <thead>
          <tr><th>用户名</th><th>显示名</th><th>角色</th><th>状态</th><th></th></tr>
        </thead>
        <tbody>
          <tr v-for="u in users" :key="u.id" :class="{ disabled: !u.active }"
              :data-test="`user-row-${u.username}`">
            <td class="uname">{{ u.username }}</td>
            <td>{{ u.display_name || '—' }}</td>
            <td>{{ roleLabel(u.role) }}</td>
            <td>
              <span class="hub-badge" :class="u.active ? 'neutral' : 'warn'">
                {{ u.active ? '启用' : '已禁用' }}
              </span>
            </td>
            <td class="ops">
              <button class="hub-link" :data-test="`user-edit-${u.username}`"
                      @click="openEdit(u)">编辑</button>
            </td>
          </tr>
        </tbody>
      </table>
      </div>
    </main>

    <div v-if="dialog" class="hub-modal-mask" data-test="user-dialog"
         @click.self="dialog = null">
      <form class="hub-modal" @submit.prevent="onSubmit">
        <h2>{{ dialog.id ? `编辑 ${dialog.username}` : '新建用户' }}</h2>
        <label v-if="!dialog.id">用户名
          <input v-model="dialog.username" class="hub-input"
                 data-test="user-username" required />
        </label>
        <label>显示名
          <input v-model="dialog.display_name" class="hub-input"
                 data-test="user-display-name" />
        </label>
        <label>角色
          <select v-model="dialog.role" class="hub-select" data-test="user-role">
            <option v-for="(label, r) in ROLES" :key="r" :value="r">{{ label }}</option>
          </select>
        </label>
        <label>{{ dialog.id ? '重置密码（留空不改）' : '密码（至少 6 位）' }}
          <input v-model="dialog.password" type="password" class="hub-input"
                 data-test="user-password"
                 :required="!dialog.id" autocomplete="new-password" />
        </label>
        <label v-if="dialog.id" class="row">
          <input type="checkbox" v-model="dialog.active" data-test="user-active" />
          启用账号
        </label>
        <p v-if="dialogError" class="hub-error" data-test="user-error">{{ dialogError }}</p>
        <div class="hub-modal-actions">
          <button type="button" class="hub-btn ghost" @click="dialog = null">取消</button>
          <button type="submit" class="hub-btn" data-test="user-submit" :disabled="saving">
            {{ saving ? '保存中…' : '保存' }}
          </button>
        </div>
      </form>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import api from '../api'

const ROLES = {
  operator: '操作员（只读）',
  engineer: '工程师（可操作）',
  director: '主管（可夺锁）',
  admin: '管理员',
}
const router = useRouter()
const users = ref(null)
const loadError = ref('')
const dialog = ref(null)
const dialogError = ref('')
const saving = ref(false)

function roleLabel(r) { return ROLES[r] || r }

async function refresh() {
  try {
    const r = await api.get('/users')
    users.value = r.data.items
    loadError.value = ''
  } catch (e) {
    loadError.value = e.response?.data?.detail || '加载失败'
  }
}

function openCreate() {
  dialogError.value = ''
  dialog.value = { id: null, username: '', display_name: '', role: 'operator', password: '' }
}

function openEdit(u) {
  dialogError.value = ''
  dialog.value = {
    id: u.id, username: u.username, display_name: u.display_name || '',
    role: u.role, active: u.active, password: '',
  }
}

async function onSubmit() {
  dialogError.value = ''
  saving.value = true
  try {
    if (dialog.value.id) {
      await api.put(`/users/${dialog.value.id}`, {
        display_name: dialog.value.display_name,
        role: dialog.value.role,
        active: dialog.value.active,
        password: dialog.value.password || null,
      })
    } else {
      await api.post('/users', {
        username: dialog.value.username,
        display_name: dialog.value.display_name,
        role: dialog.value.role,
        password: dialog.value.password,
      })
    }
    dialog.value = null
    await refresh()
  } catch (e) {
    dialogError.value = e.response?.data?.detail || '保存失败'
  } finally {
    saving.value = false
  }
}

onMounted(refresh)
</script>

<style scoped>
.users-page { min-height: 100%; }
.create { margin-left: auto; }

.content { padding: 18px 20px; }
.table-card {
  background: var(--hub-panel); border: 1px solid var(--hub-border);
  border-radius: var(--hub-radius-lg); overflow: hidden;
}
.uname { color: var(--hub-text); }
.hub-table tr.disabled td { color: var(--hub-text-4); }
.hub-table tr.disabled .uname { color: var(--hub-text-4); }
.ops { text-align: right; }
</style>