<template>
  <div class="space-y-6 p-4">
    <!-- ============================================================ -->
    <!-- 卡片 1: 当前状态 + 总开关                                       -->
    <!-- ============================================================ -->
    <el-card shadow="never" class="bg-slate-800 border-slate-700">
      <template #header>
        <div class="flex items-center gap-2">
          <el-icon class="text-tech-blue"><Lock /></el-icon>
          <span class="font-bold text-white">账号鉴权状态</span>
        </div>
      </template>

      <div class="space-y-4">
        <!-- 状态说明 -->
        <div
          class="p-4 rounded-lg border"
          :class="authStore.authEnabled
            ? 'bg-green-900/30 border-green-700 text-green-200'
            : 'bg-amber-900/30 border-amber-700 text-amber-200'"
        >
          <div class="flex items-start gap-3">
            <el-icon class="mt-1" :size="20">
              <SuccessFilled v-if="authStore.authEnabled" />
              <WarningFilled v-else />
            </el-icon>
            <div class="flex-1">
              <div class="font-medium mb-1">
                {{ authStore.authEnabled ? '账号鉴权已启用' : '账号鉴权未启用' }}
              </div>
              <p v-if="authStore.authEnabled" class="text-sm opacity-90">
                已有账号 <b>{{ authStore.userCount }}</b> 个 · 当前身份: <b>{{ authStore.displayLabel }}</b>
              </p>
              <p v-else class="text-sm opacity-90">
                未启用时系统对所有用户开放, 无登录限制. 启用后将强制创建第一个管理员账号, 未登录用户进入系统为「操作员」身份, 仅可使用「开始检测/停止检测/待机」三个按钮.
              </p>
            </div>
          </div>
        </div>

        <!-- 当前账号区 (启用后才显示) -->
        <div v-if="authStore.authEnabled" class="grid grid-cols-1 md:grid-cols-3 gap-3">
          <div class="p-3 rounded bg-slate-900 border border-slate-700">
            <div class="text-xs text-slate-400 mb-1">登录状态</div>
            <div class="text-white font-medium">
              <el-tag v-if="authStore.isLoggedIn" type="success" size="small">已登录</el-tag>
              <el-tag v-else type="warning" size="small">匿名 (操作员)</el-tag>
            </div>
          </div>
          <div class="p-3 rounded bg-slate-900 border border-slate-700">
            <div class="text-xs text-slate-400 mb-1">当前身份</div>
            <div class="text-white font-medium">{{ authStore.displayLabel }}</div>
          </div>
          <div class="p-3 rounded bg-slate-900 border border-slate-700">
            <div class="text-xs text-slate-400 mb-1">当前角色</div>
            <div class="flex flex-wrap gap-1">
              <el-tag
                v-for="r in authStore.currentUser.roles || []"
                :key="r"
                size="small"
                :type="r === 'admin' ? 'danger' : (r === 'engineer' ? 'warning' : 'info')"
              >{{ r }}</el-tag>
              <span v-if="!(authStore.currentUser.roles || []).length" class="text-slate-500 text-xs">无</span>
            </div>
          </div>
        </div>

        <!-- 操作按钮 -->
        <div class="flex flex-wrap gap-3">
          <el-button
            v-if="!authStore.authEnabled"
            type="primary"
            @click="showEnableDialog = true"
          >
            <el-icon class="mr-1"><Lock /></el-icon>启用账号鉴权
          </el-button>

          <template v-else>
            <el-button v-if="!authStore.isLoggedIn" type="primary" @click="goLogin">
              <el-icon class="mr-1"><UserFilled /></el-icon>去登录
            </el-button>
            <el-button v-else @click="onLogout">
              <el-icon class="mr-1"><SwitchButton /></el-icon>登出
            </el-button>

            <el-button v-if="authStore.isLoggedIn" @click="showChangePwdDialog = true">
              <el-icon class="mr-1"><Key /></el-icon>修改密码
            </el-button>

            <el-popconfirm
              title="确定要关闭账号鉴权吗? 关闭后所有人都可以无登录使用系统."
              @confirm="onDisableAuth"
            >
              <template #reference>
                <el-button type="danger" plain :disabled="!authStore.hasPermission('system.auth_toggle')">
                  <el-icon class="mr-1"><Unlock /></el-icon>关闭账号鉴权
                </el-button>
              </template>
            </el-popconfirm>
          </template>

          <el-button :icon="Refresh" link @click="refreshAll">刷新</el-button>
        </div>

        <!-- v3.10+ 阶段 7+: 匿名 operator 兜底开关 -->
        <div
          v-if="authStore.authEnabled"
          class="mt-4 p-3 rounded bg-slate-900/60 border border-slate-700 flex items-center gap-4"
        >
          <div class="flex-1">
            <div class="flex items-center gap-2">
              <span class="text-white font-medium text-sm">匿名兜底</span>
              <el-tag :type="allowAnonymous ? 'success' : 'danger'" size="small">
                {{ allowAnonymous ? '已开启' : '已关闭' }}
              </el-tag>
              <el-tooltip
                effect="dark"
                placement="right"
                content="开启: 未登录客户端被当作受限 operator (能看 Monitor + 按 3 个按钮). 关闭: 未登录必须先登录, 适合严格安全场景."
              >
                <el-icon class="text-slate-500 cursor-help"><InfoFilled /></el-icon>
              </el-tooltip>
            </div>
            <div class="text-xs text-slate-400 mt-1">
              {{ allowAnonymous
                ? '未登录客户端进入系统视为「操作员」, 可看画面 + 按开始/停止/待机.'
                : '未登录客户端必须先登录, 否则跳转登录页 (严格模式).' }}
            </div>
          </div>
          <el-switch
            v-model="allowAnonymous"
            :loading="allowAnonymousLoading"
            :disabled="!authStore.hasPermission('system.auth_toggle')"
            active-text="开启"
            inactive-text="关闭"
            inline-prompt
            @change="onToggleAllowAnonymous"
            data-testid="auth-anon-switch"
          />
        </div>

        <!-- v3.10+ 阶段 7+: 重启后保持登录开关 -->
        <div
          v-if="authStore.authEnabled"
          class="p-3 rounded bg-slate-900/60 border border-slate-700 flex items-center gap-4"
        >
          <div class="flex-1">
            <div class="flex items-center gap-2">
              <span class="text-white font-medium text-sm">重启后保持登录</span>
              <el-tag :type="sessionPersist ? 'success' : 'warning'" size="small">
                {{ sessionPersist ? '已开启' : '已关闭' }}
              </el-tag>
              <el-tooltip
                effect="dark"
                placement="right"
                content="开启 (默认): 登录后 token 落盘, 重启软件自动恢复登录, 工人换班不用每次输密码. 关闭: 关 tab / 重启软件即失效, 适合"
              >
                <el-icon class="text-slate-500 cursor-help"><InfoFilled /></el-icon>
              </el-tooltip>
            </div>
            <div class="text-xs text-slate-400 mt-1">
              {{ sessionPersist
                ? '关闭软件再打开仍保持上次登录身份 (工厂场景推荐).'
                : '关 tab 或重启软件后需要重新登录 (高安全场景).' }}
            </div>
          </div>
          <el-switch
            v-model="sessionPersist"
            :loading="sessionPersistLoading"
            :disabled="!authStore.hasPermission('system.auth_toggle')"
            active-text="开启"
            inactive-text="关闭"
            inline-prompt
            @change="onToggleSessionPersist"
            data-testid="auth-persist-switch"
          />
        </div>
      </div>
    </el-card>

    <!-- ============================================================ -->
    <!-- 卡片 2: 账号列表 + CRUD (v3.10+ 阶段 7+ 补完)                   -->
    <!-- ============================================================ -->
    <el-card v-if="authStore.authEnabled" shadow="never" class="bg-slate-800 border-slate-700">
      <template #header>
        <div class="flex items-center justify-between">
          <div class="flex items-center gap-2">
            <el-icon class="text-tech-blue"><User /></el-icon>
            <span class="font-bold text-white">账号列表</span>
            <el-tooltip
              effect="dark" placement="right"
              content="每个工人一个账号 (建议 用户名=工号). 工人用账号密码登录后, 检测记录的 operator_id 才会绑定到这个账号."
            >
              <el-icon class="text-slate-500 cursor-help"><InfoFilled /></el-icon>
            </el-tooltip>
          </div>
          <el-button
            type="primary"
            size="small"
            :disabled="!authStore.hasPermission('system.users.manage')"
            @click="openCreateUserDialog"
            data-testid="auth-create-user-btn"
          >
            <el-icon class="mr-1"><Plus /></el-icon>创建账号
          </el-button>
        </div>
      </template>

      <el-table :data="users" stripe size="small" v-loading="usersLoading">
        <el-table-column prop="id" label="ID" width="60" />
        <el-table-column prop="username" label="用户名 (工号)" min-width="140" />
        <el-table-column prop="display_name" label="显示名" min-width="120">
          <template #default="{ row }">
            <span>{{ row.display_name || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="身份" min-width="160">
          <template #default="{ row }">
            <!-- 一人一身份: 取首个角色 (后端仍支持多角色, 但 UI 简化) -->
            <el-tag
              v-if="(row.roles || [])[0]"
              size="small"
              :type="(row.roles[0]) === 'admin' ? 'danger' : ((row.roles[0]) === 'engineer' ? 'warning' : 'info')"
            >{{ (row.roles[0]) }}</el-tag>
            <span v-else class="text-slate-500 text-xs">未分配</span>
          </template>
        </el-table-column>
        <el-table-column label="启用" width="70">
          <template #default="{ row }">
            <el-tag size="small" :type="row.active ? 'success' : 'info'">
              {{ row.active ? '是' : '否' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="last_login_at" label="上次登录" min-width="160">
          <template #default="{ row }">
            <span class="text-slate-400 text-xs">{{ row.last_login_at || '从未' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="230" align="center" fixed="right">
          <template #default="{ row }">
            <el-button
              size="small" link type="primary"
              :disabled="!authStore.hasPermission('system.users.manage')"
              @click="openResetPwdDialog(row)"
            >重置密码</el-button>
            <el-button
              size="small" link
              :type="row.active ? 'warning' : 'success'"
              :disabled="!authStore.hasPermission('system.users.manage') || row.id === authStore.currentUser.id"
              @click="onToggleUserActive(row)"
            >{{ row.active ? '禁用' : '启用' }}</el-button>
            <el-button
              size="small" link type="danger"
              :disabled="!authStore.hasPermission('system.users.manage') || row.id === authStore.currentUser.id"
              @click="onDeleteUser(row)"
            >删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div v-if="usersError" class="mt-2 text-red-400 text-xs">{{ usersError }}</div>
    </el-card>

    <!-- ============================================================ -->
    <!-- 卡片 3: 角色列表 + CRUD (v3.10+ 阶段 7+ 补完)                   -->
    <!-- ============================================================ -->
    <el-card v-if="authStore.authEnabled" shadow="never" class="bg-slate-800 border-slate-700">
      <template #header>
        <div class="flex items-center justify-between">
          <div class="flex items-center gap-2">
            <el-icon class="text-tech-blue"><Avatar /></el-icon>
            <span class="font-bold text-white">角色列表</span>
            <el-tooltip
              effect="dark" placement="right"
              content="角色是权限套餐. 内置 3 个角色 (admin/engineer/operator) 不可删, 但可改名改权限. 你也可以创建自己的角色, 比如 质检主管 / 夜班操作员 等."
            >
              <el-icon class="text-slate-500 cursor-help"><InfoFilled /></el-icon>
            </el-tooltip>
          </div>
          <el-button
            type="primary"
            size="small"
            :disabled="!authStore.hasPermission('system.roles.manage')"
            @click="openCreateRoleDialog"
            data-testid="auth-create-role-btn"
          >
            <el-icon class="mr-1"><Plus /></el-icon>创建角色
          </el-button>
        </div>
      </template>

      <el-table :data="roles" stripe size="small" v-loading="rolesLoading">
        <el-table-column prop="code" label="角色代码" width="140">
          <template #default="{ row }">
            <el-tag
              size="small"
              :type="row.code === 'admin' ? 'danger' : (row.code === 'engineer' ? 'warning' : 'info')"
            >{{ row.code }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="name" label="名称" min-width="120" />
        <el-table-column prop="description" label="描述" min-width="180" />
        <el-table-column label="权限" min-width="220">
          <template #default="{ row }">
            <div class="flex flex-wrap gap-1">
              <el-tag
                v-for="p in (row.permissions || []).slice(0, 6)"
                :key="p"
                size="small"
                effect="plain"
              >{{ p }}</el-tag>
              <el-tag
                v-if="(row.permissions || []).length > 6"
                size="small"
                effect="plain"
              >+{{ (row.permissions || []).length - 6 }}</el-tag>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="内置" width="70">
          <template #default="{ row }">
            <el-tag v-if="row.is_builtin" size="small" type="info">内置</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="180" align="center" fixed="right">
          <template #default="{ row }">
            <el-button
              size="small" link type="primary"
              :disabled="!authStore.hasPermission('system.roles.manage')"
              @click="openRoleEditor(row)"
            >编辑权限</el-button>
            <el-button
              size="small" link type="danger"
              :disabled="!authStore.hasPermission('system.roles.manage') || row.is_builtin"
              @click="onDeleteRole(row)"
            >删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div v-if="rolesError" class="mt-2 text-red-400 text-xs">{{ rolesError }}</div>
    </el-card>

    <!-- ============================================================ -->
    <!-- 卡片 4: M2M API Key 管理 (需 system.apikey.manage 权限)        -->
    <!-- ============================================================ -->
    <el-card
      v-if="authStore.authEnabled && authStore.hasPermission('system.apikey.manage')"
      shadow="never"
      class="bg-slate-800 border-slate-700"
    >
      <template #header>
        <div class="flex items-center justify-between">
          <div class="flex items-center gap-2">
            <el-icon class="text-tech-blue"><Key /></el-icon>
            <span class="font-bold text-white">M2M API Key 管理</span>
            <el-tooltip
              effect="dark"
              content="给副机心跳 / 外部 MES 推工单 / Electron License IPC 这种机器对机器通信用的凭证. 与用户登录 token 互不相干."
              placement="right"
            >
              <el-icon class="text-slate-500 cursor-help"><InfoFilled /></el-icon>
            </el-tooltip>
          </div>
          <el-button type="primary" size="small" @click="openCreateApiKeyDialog">
            <el-icon class="mr-1"><Plus /></el-icon>创建 API Key
          </el-button>
        </div>
      </template>

      <el-table :data="apiKeys" stripe size="small" v-loading="apiKeysLoading">
        <el-table-column prop="id" label="ID" width="60" />
        <el-table-column prop="name" label="名称" min-width="140" />
        <el-table-column label="Key 前缀" width="160">
          <template #default="{ row }">
            <code class="text-cyan-300 text-xs">{{ row.key_prefix }}…</code>
          </template>
        </el-table-column>
        <el-table-column label="Scope" width="140">
          <template #default="{ row }">
            <el-tag size="small" :type="row.scope === '*' ? 'danger' : 'info'">{{ row.scope }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="80">
          <template #default="{ row }">
            <el-tag size="small" :type="row.enabled ? 'success' : 'info'">
              {{ row.enabled ? '启用' : '禁用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="使用次数" width="90" align="right">
          <template #default="{ row }">
            <span class="text-slate-400 text-xs">{{ row.use_count }}</span>
          </template>
        </el-table-column>
        <el-table-column label="上次使用" min-width="160">
          <template #default="{ row }">
            <span class="text-slate-400 text-xs">{{ row.last_used_at || '从未' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="160" align="center">
          <template #default="{ row }">
            <el-button size="small" link type="warning" @click="onToggleApiKey(row)">
              {{ row.enabled ? '禁用' : '启用' }}
            </el-button>
            <el-popconfirm
              :title="`确定删除 API Key 「${row.name}」? 删除后引用该 key 的客户端将立即失效, 不可恢复.`"
              @confirm="onDeleteApiKey(row)"
            >
              <template #reference>
                <el-button size="small" link type="danger">删除</el-button>
              </template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
      <div v-if="apiKeysError" class="mt-2 text-red-400 text-xs">{{ apiKeysError }}</div>
      <div v-if="!apiKeysLoading && apiKeys.length === 0" class="text-slate-500 text-xs mt-2">
        还没有任何 API Key. 点击右上角「创建」生成第一个.
      </div>
    </el-card>

    <!-- ============================================================ -->
    <!-- 对话框: 启用引导 (创建首个管理员)                              -->
    <!-- ============================================================ -->
    <el-dialog v-model="showEnableDialog" title="启用账号鉴权" width="480px" destroy-on-close>
      <p class="text-slate-500 text-sm mb-4">
        启用后, 系统将强制创建第一个超级管理员账号. 请妥善保管该账号信息, <b>遗忘后只能重置数据库</b>.
      </p>
      <el-form ref="enableFormRef" :model="enableForm" :rules="enableRules" label-width="90px">
        <el-form-item label="用户名" prop="username">
          <el-input v-model="enableForm.username" placeholder="例如 admin" autocomplete="off" />
        </el-form-item>
        <el-form-item label="显示名" prop="display_name">
          <el-input v-model="enableForm.display_name" placeholder="可选, 例如 系统管理员" />
        </el-form-item>
        <el-form-item label="密码" prop="password">
          <el-input v-model="enableForm.password" type="password" show-password placeholder="至少 6 位" />
        </el-form-item>
        <el-form-item label="确认密码" prop="password_confirm">
          <el-input v-model="enableForm.password_confirm" type="password" show-password />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showEnableDialog = false">取消</el-button>
        <el-button type="primary" :loading="enabling" @click="onEnableAuth">启用并创建管理员</el-button>
      </template>
    </el-dialog>

    <!-- ============================================================ -->
    <!-- 对话框: 创建 API Key                                            -->
    <!-- ============================================================ -->
    <el-dialog
      v-model="showCreateApiKeyDialog"
      title="创建 M2M API Key"
      width="480px"
      destroy-on-close
    >
      <p class="text-amber-400 text-sm mb-4 flex items-start gap-2">
        <el-icon class="mt-0.5"><WarningFilled /></el-icon>
        <span>创建后系统会一次性显示完整 Key, 请立即复制并妥善保管. 关闭对话框后将再也无法查看完整 Key, 遗忘只能删除并重建.</span>
      </p>
      <el-form ref="apiKeyFormRef" :model="apiKeyForm" :rules="apiKeyRules" label-width="80px">
        <el-form-item label="名称" prop="name">
          <el-input v-model="apiKeyForm.name" placeholder="例如 副机-工位1 / 外部MES" />
        </el-form-item>
        <el-form-item label="Scope" prop="scope">
          <el-select v-model="apiKeyForm.scope" placeholder="选择允许的 M2M 类型" class="w-full">
            <el-option label="cluster — 副机推送 / 心跳" value="cluster" />
            <el-option label="mes.receive — 外部 MES 推工单" value="mes.receive" />
            <el-option label="license.cache — Electron License IPC" value="license.cache" />
            <el-option label="* (全部, 慎用)" value="*" />
          </el-select>
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="apiKeyForm.description" type="textarea" :rows="2" placeholder="可选, 例如 '工位 2 副机 - 2026 部署'" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreateApiKeyDialog = false">取消</el-button>
        <el-button type="primary" :loading="creatingApiKey" @click="onCreateApiKey">
          创建
        </el-button>
      </template>
    </el-dialog>

    <!-- ============================================================ -->
    <!-- 对话框: 新建 API Key 后一次性展示明文                            -->
    <!-- ============================================================ -->
    <el-dialog
      v-model="showApiKeyPlaintextDialog"
      title="API Key 已创建 — 立即复制!"
      width="560px"
      :close-on-click-modal="false"
      :close-on-press-escape="false"
      :show-close="false"
    >
      <p class="text-red-400 text-sm mb-3 flex items-start gap-2">
        <el-icon class="mt-0.5"><WarningFilled /></el-icon>
        <span>这是 <b>唯一一次</b> 可以查看完整 Key 的机会. 关闭对话框后系统只保留前 10 位用于识别, 永远拿不到完整 Key.</span>
      </p>
      <div class="bg-slate-900 border border-slate-700 rounded p-3 mb-3 break-all font-mono text-sm text-emerald-300">
        {{ createdApiKeyPlaintext }}
      </div>
      <div class="flex items-center gap-2">
        <el-button type="primary" size="small" @click="copyApiKeyPlaintext">
          <el-icon class="mr-1"><CopyDocument /></el-icon>复制 Key
        </el-button>
        <span v-if="copiedTip" class="text-emerald-400 text-xs">{{ copiedTip }}</span>
      </div>
      <template #footer>
        <el-button @click="confirmClosePlaintext">我已保存, 关闭</el-button>
      </template>
    </el-dialog>

    <!-- ============================================================ -->
    <!-- 对话框: 编辑角色权限 (admin 调整 operator/engineer 默认权限)    -->
    <!-- ============================================================ -->
    <el-dialog
      v-model="showRoleEditorDialog"
      :title="`编辑角色权限 — ${editingRole?.name || ''}`"
      width="720px"
      destroy-on-close
    >
      <p class="text-slate-400 text-sm mb-3">
        <template v-if="editingRole?.is_builtin">
          内置角色, 仅允许调整 <b>权限列表</b>, 名称 / 代码不可改.
          <span v-if="editingRole?.code === 'admin'" class="text-amber-400">
            提示: admin 默认含 <code>*</code> 通配, 改成精确列表后可能误锁定自己, 操作前请充分确认.
          </span>
        </template>
        <template v-else>
          自定义角色, 可同时改名称 / 描述 / 权限.
        </template>
      </p>

      <el-form v-if="!editingRole?.is_builtin" :model="roleEditForm" label-width="80px" class="mb-2">
        <el-form-item label="名称">
          <el-input v-model="roleEditForm.name" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="roleEditForm.description" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>

      <div class="mb-2 flex items-center justify-between">
        <span class="text-slate-300 text-sm">权限勾选 (已选 <b>{{ roleEditForm.permissions.length }}</b> 项)</span>
        <div class="flex gap-2">
          <el-button size="small" link @click="setAllPerms(true)">全选</el-button>
          <el-button size="small" link @click="setAllPerms(false)">全不选</el-button>
          <el-button size="small" link type="primary" @click="usePermWildcard">使用通配 *</el-button>
        </div>
      </div>

      <div
        v-if="roleEditForm.permissions.includes('*')"
        class="p-2 mb-3 rounded bg-rose-900/30 border border-rose-700 text-rose-200 text-xs"
      >
        当前角色含通配 <code>*</code>, 等同所有权限 (含未来新增的). 若想精确控制请先去掉 <code>*</code> 再勾选.
      </div>

      <div class="max-h-[420px] overflow-y-auto border border-slate-700 rounded">
        <div
          v-for="group in permCatalogGrouped"
          :key="group.name"
          class="border-b border-slate-700 last:border-b-0"
        >
          <div class="bg-slate-900 px-3 py-1.5 text-slate-300 text-sm font-medium flex items-center justify-between">
            <span>{{ group.name }} ({{ group.items.length }})</span>
            <el-button size="small" link @click="toggleGroup(group)">
              {{ groupAllChecked(group) ? '取消本组' : '选中本组' }}
            </el-button>
          </div>
          <div class="p-2 grid grid-cols-1 md:grid-cols-2 gap-1">
            <el-checkbox
              v-for="item in group.items"
              :key="item.key"
              :model-value="roleEditForm.permissions.includes(item.key)"
              :disabled="roleEditForm.permissions.includes('*')"
              @change="togglePerm(item.key, $event)"
            >
              <span class="text-slate-200 text-xs">{{ item.label }}</span>
              <code class="ml-1 text-slate-500 text-[10px]">({{ item.key }})</code>
            </el-checkbox>
          </div>
        </div>
      </div>

      <template #footer>
        <el-button @click="showRoleEditorDialog = false">取消</el-button>
        <el-button type="primary" :loading="savingRole" @click="onSaveRolePerms">保存</el-button>
      </template>
    </el-dialog>

    <!-- ============================================================ -->
    <!-- 对话框: 修改密码                                                -->
    <!-- ============================================================ -->
    <el-dialog v-model="showChangePwdDialog" title="修改密码" width="420px" destroy-on-close>
      <el-form ref="pwdFormRef" :model="pwdForm" :rules="pwdRules" label-width="90px">
        <el-form-item label="原密码" prop="old_password">
          <el-input v-model="pwdForm.old_password" type="password" show-password />
        </el-form-item>
        <el-form-item label="新密码" prop="new_password">
          <el-input v-model="pwdForm.new_password" type="password" show-password placeholder="至少 6 位" />
        </el-form-item>
        <el-form-item label="确认新密码" prop="new_password_confirm">
          <el-input v-model="pwdForm.new_password_confirm" type="password" show-password />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showChangePwdDialog = false">取消</el-button>
        <el-button type="primary" :loading="changingPwd" @click="onChangePassword">提交</el-button>
      </template>
    </el-dialog>

    <!-- ============================================================ -->
    <!-- 对话框: 创建账号 (v3.10+ 阶段 7+)                                -->
    <!-- ============================================================ -->
    <el-dialog
      v-model="showCreateUserDialog"
      title="创建账号"
      width="520px"
      destroy-on-close
      data-testid="auth-create-user-dialog"
    >
      <el-form
        ref="createUserFormRef"
        :model="createUserForm"
        :rules="createUserRules"
        label-width="100px"
      >
        <el-form-item label="用户名" prop="username">
          <el-input
            v-model="createUserForm.username"
            placeholder="工号或英文 ID, 例如 W2001 / zhangsan"
            data-testid="auth-create-user-username"
          />
        </el-form-item>
        <el-form-item label="显示名" prop="display_name">
          <el-input
            v-model="createUserForm.display_name"
            placeholder="工人姓名, 例如 张师傅 (选填, 留空用用户名)"
          />
        </el-form-item>
        <el-form-item label="密码" prop="password">
          <el-input
            v-model="createUserForm.password"
            type="password"
            show-password
            placeholder="至少 6 位"
            data-testid="auth-create-user-password"
          />
        </el-form-item>
        <el-form-item label="确认密码" prop="password_confirm">
          <el-input
            v-model="createUserForm.password_confirm"
            type="password"
            show-password
          />
        </el-form-item>
        <el-form-item label="身份" prop="role_code">
          <el-select
            v-model="createUserForm.role_code"
            placeholder="为这个账号选一个身份"
            class="w-full"
            data-testid="auth-create-user-role"
          >
            <el-option
              v-for="r in roles"
              :key="r.code"
              :label="`${r.name} (${r.code})`"
              :value="r.code"
            />
          </el-select>
          <div class="text-xs text-slate-500 mt-1">
            一人一身份. 想要更细的权限, 先去「角色列表」自定义角色, 再回来这里选.
          </div>
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="createUserForm.active" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreateUserDialog = false">取消</el-button>
        <el-button
          type="primary"
          :loading="creatingUser"
          @click="onCreateUserSubmit"
          data-testid="auth-create-user-submit"
        >创建</el-button>
      </template>
    </el-dialog>

    <!-- ============================================================ -->
    <!-- 对话框: 创建角色 (v3.10+ 阶段 7+)                                -->
    <!-- ============================================================ -->
    <el-dialog
      v-model="showCreateRoleDialog"
      title="创建角色"
      width="640px"
      destroy-on-close
      data-testid="auth-create-role-dialog"
    >
      <el-form
        ref="createRoleFormRef"
        :model="createRoleForm"
        :rules="createRoleRules"
        label-width="100px"
      >
        <el-form-item label="角色代码" prop="code">
          <el-input
            v-model="createRoleForm.code"
            placeholder="小写字母开头, 例如 supervisor / night_op"
            data-testid="auth-create-role-code"
          />
          <div class="text-xs text-slate-500 mt-1">
            代码用于系统内部识别, 一旦创建不能改
          </div>
        </el-form-item>
        <el-form-item label="角色名称" prop="name">
          <el-input
            v-model="createRoleForm.name"
            placeholder="中文名也行, 例如 质检主管"
            data-testid="auth-create-role-name"
          />
        </el-form-item>
        <el-form-item label="描述">
          <el-input
            v-model="createRoleForm.description"
            type="textarea"
            :rows="2"
            placeholder="可选 - 这个角色能做什么"
          />
        </el-form-item>
        <el-form-item label="权限">
          <div class="w-full">
            <div class="flex items-center justify-between mb-2">
              <span class="text-xs text-slate-400">
                已勾选 {{ createRoleForm.permissions.length }} 项
              </span>
              <div class="flex gap-2">
                <el-button size="small" link @click="createRoleForm.permissions = permCatalog.map(p => p.key)">全选</el-button>
                <el-button size="small" link @click="createRoleForm.permissions = []">全不选</el-button>
                <el-button size="small" link type="primary" @click="createRoleForm.permissions = ['*']">使用通配 *</el-button>
              </div>
            </div>
            <el-checkbox-group v-model="createRoleForm.permissions" class="w-full">
              <div class="max-h-[320px] overflow-y-auto border border-slate-700 rounded">
                <div
                  v-for="group in permCatalogGrouped"
                  :key="group.name"
                  class="border-b border-slate-700 last:border-b-0"
                >
                  <div class="bg-slate-900 px-3 py-1.5 text-slate-300 text-sm font-medium">
                    {{ group.name }} ({{ group.items.length }})
                  </div>
                  <div class="p-2 grid grid-cols-1 md:grid-cols-2 gap-1">
                    <el-checkbox
                      v-for="item in group.items"
                      :key="item.key"
                      :value="item.key"
                    >
                      <span class="text-sm">{{ item.label || item.key }}</span>
                      <span class="text-slate-500 text-xs ml-1">{{ item.key }}</span>
                    </el-checkbox>
                  </div>
                </div>
              </div>
            </el-checkbox-group>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreateRoleDialog = false">取消</el-button>
        <el-button
          type="primary"
          :loading="creatingRole"
          @click="onCreateRoleSubmit"
          data-testid="auth-create-role-submit"
        >创建</el-button>
      </template>
    </el-dialog>

    <!-- ============================================================ -->
    <!-- 对话框: 重置密码 (管理员给工人重置) (v3.10+ 阶段 7+)            -->
    <!-- ============================================================ -->
    <el-dialog
      v-model="showResetPwdDialog"
      title="重置账号密码"
      width="420px"
      destroy-on-close
    >
      <div class="mb-3 text-sm text-slate-300">
        正在重置 <b class="text-white">{{ resetPwdTarget?.display_name || resetPwdTarget?.username }}</b> 的密码
      </div>
      <el-form
        ref="resetPwdFormRef"
        :model="resetPwdForm"
        :rules="resetPwdRules"
        label-width="100px"
      >
        <el-form-item label="新密码" prop="new_password">
          <el-input
            v-model="resetPwdForm.new_password"
            type="password"
            show-password
            placeholder="至少 6 位"
          />
        </el-form-item>
        <el-form-item label="确认密码" prop="new_password_confirm">
          <el-input
            v-model="resetPwdForm.new_password_confirm"
            type="password"
            show-password
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showResetPwdDialog = false">取消</el-button>
        <el-button
          type="primary"
          :loading="resettingPwd"
          @click="onResetPwdSubmit"
        >重置</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue';
import { useRouter } from 'vue-router';
import { ElMessage, ElMessageBox } from 'element-plus';
import {
  Lock, Unlock, User, UserFilled, Avatar, Key, Refresh, SwitchButton,
  SuccessFilled, WarningFilled, Plus, EditPen, CopyDocument, InfoFilled,
} from '@element-plus/icons-vue';
import { useAuthStore } from '@/store/useAuthStore';
import {
  enableAuth, disableAuth, changePassword,
  listUsers, createUser, updateUser, deleteUser,
  listRoles, createRole, updateRole, deleteRole,
  getAuthConfig, updateAuthConfig,
  getPermissionCatalog,
  listApiKeys, createApiKey, toggleApiKey, deleteApiKey,
} from '@/api/auth';

const router = useRouter();
const authStore = useAuthStore();

// ---------- 列表数据 ----------
const users = ref([]);
const usersLoading = ref(false);
const usersError = ref('');

const roles = ref([]);
const rolesLoading = ref(false);
const rolesError = ref('');

async function refreshUsers() {
  if (!authStore.authEnabled) return;
  usersLoading.value = true;
  usersError.value = '';
  try {
    const res = await listUsers();
    // 后端 /users 直接返回数组, 兼容老式 {users:[...]} wrapper
    users.value = Array.isArray(res.data) ? res.data : (res.data?.users || []);
  } catch (e) {
    usersError.value = e?.response?.data?.detail || e?.message || '读取账号列表失败';
  } finally {
    usersLoading.value = false;
  }
}

async function refreshRoles() {
  if (!authStore.authEnabled) return;
  rolesLoading.value = true;
  rolesError.value = '';
  try {
    const res = await listRoles();
    roles.value = Array.isArray(res.data) ? res.data : (res.data?.roles || []);
  } catch (e) {
    rolesError.value = e?.response?.data?.detail || e?.message || '读取角色列表失败';
  } finally {
    rolesLoading.value = false;
  }
}

// ---------- API Key 列表 ----------
const apiKeys = ref([]);
const apiKeysLoading = ref(false);
const apiKeysError = ref('');

async function refreshApiKeys() {
  if (!authStore.authEnabled) return;
  if (!authStore.hasPermission('system.apikey.manage')) return;
  apiKeysLoading.value = true;
  apiKeysError.value = '';
  try {
    const res = await listApiKeys();
    apiKeys.value = Array.isArray(res.data) ? res.data : [];
  } catch (e) {
    apiKeysError.value = e?.response?.data?.detail || e?.message || '读取 API Key 列表失败';
  } finally {
    apiKeysLoading.value = false;
  }
}

// ---------- 权限目录 (给角色编辑器用) ----------
const permCatalog = ref([]);
const permCatalogGrouped = computed(() => {
  const groups = {};
  for (const item of permCatalog.value) {
    const g = item.group || '其它';
    if (!groups[g]) groups[g] = { name: g, items: [] };
    groups[g].items.push(item);
  }
  return Object.values(groups);
});

async function refreshPermCatalog() {
  if (!authStore.authEnabled) return;
  try {
    const res = await getPermissionCatalog();
    permCatalog.value = Array.isArray(res.data) ? res.data : (res.data?.permissions || []);
  } catch {
    // 静默 - 权限目录加载失败不阻塞主界面
  }
}

async function refreshAll() {
  await authStore.init(true);
  await Promise.all([refreshUsers(), refreshRoles(), refreshApiKeys(), refreshPermCatalog()]);
}

onMounted(async () => {
  await authStore.init();
  await Promise.all([refreshUsers(), refreshRoles(), refreshApiKeys(), refreshPermCatalog()]);
});

// ---------- 启用引导 ----------
const showEnableDialog = ref(false);
const enabling = ref(false);
const enableFormRef = ref(null);
const enableForm = reactive({
  username: '',
  display_name: '',
  password: '',
  password_confirm: '',
});
const enableRules = {
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' },
    { min: 2, max: 64, message: '长度 2-64 个字符', trigger: 'blur' },
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 6, message: '密码至少 6 位', trigger: 'blur' },
  ],
  password_confirm: [
    {
      validator: (_r, v, cb) => {
        if (v !== enableForm.password) cb(new Error('两次密码不一致'));
        else cb();
      },
      trigger: 'blur',
    },
  ],
};

async function onEnableAuth() {
  if (!enableFormRef.value) return;
  try {
    await enableFormRef.value.validate();
  } catch {
    return;
  }
  enabling.value = true;
  try {
    await enableAuth(enableForm.username, enableForm.password, enableForm.display_name || null);
    ElMessage.success('账号鉴权已启用, 首个管理员已创建');
    showEnableDialog.value = false;
    // 清表单
    enableForm.username = '';
    enableForm.display_name = '';
    enableForm.password = '';
    enableForm.password_confirm = '';
    // 刷新 store + 列表
    await refreshAll();
    // 提示用户去登录
    await ElMessageBox.confirm(
      '现在已强制要求登录. 是否立即跳转到登录页?',
      '账号鉴权已启用',
      { confirmButtonText: '去登录', cancelButtonText: '稍后', type: 'info' }
    ).then(() => goLogin()).catch(() => {});
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '启用失败');
  } finally {
    enabling.value = false;
  }
}

// ---------- 关闭鉴权 ----------
async function onDisableAuth() {
  try {
    await disableAuth();
    ElMessage.success('账号鉴权已关闭');
    await refreshAll();
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '关闭失败');
  }
}

// ---------- 登出 ----------
async function onLogout() {
  await authStore.logout();
  ElMessage.success('已登出');
  await refreshAll();
}

function goLogin() {
  router.push({ name: 'Login' });
}

// ---------- 改密 ----------
const showChangePwdDialog = ref(false);
const changingPwd = ref(false);
const pwdFormRef = ref(null);
const pwdForm = reactive({
  old_password: '',
  new_password: '',
  new_password_confirm: '',
});
const pwdRules = {
  old_password: [{ required: true, message: '请输入原密码', trigger: 'blur' }],
  new_password: [
    { required: true, message: '请输入新密码', trigger: 'blur' },
    { min: 6, message: '密码至少 6 位', trigger: 'blur' },
  ],
  new_password_confirm: [
    {
      validator: (_r, v, cb) => {
        if (v !== pwdForm.new_password) cb(new Error('两次新密码不一致'));
        else cb();
      },
      trigger: 'blur',
    },
  ],
};

async function onChangePassword() {
  if (!pwdFormRef.value) return;
  try {
    await pwdFormRef.value.validate();
  } catch {
    return;
  }
  changingPwd.value = true;
  try {
    await changePassword(pwdForm.old_password, pwdForm.new_password);
    ElMessage.success('密码已更新, 请用新密码重新登录');
    showChangePwdDialog.value = false;
    pwdForm.old_password = '';
    pwdForm.new_password = '';
    pwdForm.new_password_confirm = '';
    // 改密后后端可能让所有 token 失效, 这里强制刷新一下
    await authStore.init(true);
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '修改失败');
  } finally {
    changingPwd.value = false;
  }
}

// ============================================================
// ⑤a: M2M API Key 管理
// ============================================================

const showCreateApiKeyDialog = ref(false);
const creatingApiKey = ref(false);
const apiKeyFormRef = ref(null);
const apiKeyForm = reactive({
  name: '',
  scope: 'cluster',
  description: '',
});
const apiKeyRules = {
  name: [
    { required: true, message: '请输入名称', trigger: 'blur' },
    { min: 1, max: 128, message: '长度 1-128 个字符', trigger: 'blur' },
  ],
  scope: [{ required: true, message: '请选择 scope', trigger: 'change' }],
};

const showApiKeyPlaintextDialog = ref(false);
const createdApiKeyPlaintext = ref('');
const copiedTip = ref('');

function openCreateApiKeyDialog() {
  apiKeyForm.name = '';
  apiKeyForm.scope = 'cluster';
  apiKeyForm.description = '';
  showCreateApiKeyDialog.value = true;
}

async function onCreateApiKey() {
  if (!apiKeyFormRef.value) return;
  try { await apiKeyFormRef.value.validate(); } catch { return; }
  creatingApiKey.value = true;
  try {
    const res = await createApiKey({
      name: apiKeyForm.name.trim(),
      scope: apiKeyForm.scope,
      description: apiKeyForm.description?.trim() || null,
    });
    const plain = res.data?.plaintext;
    if (!plain) {
      ElMessage.warning('后端未返回明文 key, 请联系开发');
    } else {
      createdApiKeyPlaintext.value = plain;
      showApiKeyPlaintextDialog.value = true;
    }
    showCreateApiKeyDialog.value = false;
    await refreshApiKeys();
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '创建失败');
  } finally {
    creatingApiKey.value = false;
  }
}

async function copyApiKeyPlaintext() {
  try {
    await navigator.clipboard.writeText(createdApiKeyPlaintext.value);
    copiedTip.value = '已复制到剪贴板';
    setTimeout(() => { copiedTip.value = ''; }, 2000);
  } catch {
    copiedTip.value = '复制失败, 请手动选中复制';
    setTimeout(() => { copiedTip.value = ''; }, 3000);
  }
}

async function confirmClosePlaintext() {
  await ElMessageBox.confirm(
    '确认已保存 Key? 关闭后将无法再看到完整内容.',
    '确认关闭',
    { confirmButtonText: '已保存, 关闭', cancelButtonText: '再看一下', type: 'warning' }
  ).then(() => {
    showApiKeyPlaintextDialog.value = false;
    createdApiKeyPlaintext.value = '';
  }).catch(() => {});
}

async function onToggleApiKey(row) {
  try {
    await toggleApiKey(row.id);
    ElMessage.success(row.enabled ? `已禁用 ${row.name}` : `已启用 ${row.name}`);
    await refreshApiKeys();
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '切换失败');
  }
}

async function onDeleteApiKey(row) {
  try {
    await deleteApiKey(row.id);
    ElMessage.success(`已删除 ${row.name}`);
    await refreshApiKeys();
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '删除失败');
  }
}

// ============================================================
// ⑤b: 编辑角色权限 (admin 可改 operator/engineer 默认权限)
// ============================================================

const showRoleEditorDialog = ref(false);
const savingRole = ref(false);
const editingRole = ref(null);
const roleEditForm = reactive({
  name: '',
  description: '',
  permissions: [],
});

function openRoleEditor(role) {
  editingRole.value = role;
  roleEditForm.name = role.name || '';
  roleEditForm.description = role.description || '';
  roleEditForm.permissions = [...(role.permissions || [])];
  showRoleEditorDialog.value = true;
}

function togglePerm(key, checked) {
  const idx = roleEditForm.permissions.indexOf(key);
  if (checked && idx < 0) roleEditForm.permissions.push(key);
  if (!checked && idx >= 0) roleEditForm.permissions.splice(idx, 1);
}

function setAllPerms(checkAll) {
  if (checkAll) {
    const all = permCatalog.value.map(p => p.key);
    roleEditForm.permissions = [...new Set([...roleEditForm.permissions.filter(p => p === '*'), ...all])];
  } else {
    roleEditForm.permissions = [];
  }
}

function usePermWildcard() {
  roleEditForm.permissions = ['*'];
}

function groupAllChecked(group) {
  return group.items.every(it => roleEditForm.permissions.includes(it.key));
}

function toggleGroup(group) {
  const allOn = groupAllChecked(group);
  if (allOn) {
    roleEditForm.permissions = roleEditForm.permissions.filter(
      p => !group.items.some(it => it.key === p)
    );
  } else {
    const newOnes = group.items.map(it => it.key);
    roleEditForm.permissions = [...new Set([...roleEditForm.permissions, ...newOnes])];
  }
}

async function onSaveRolePerms() {
  if (!editingRole.value) return;

  // 移除 admin 通配的最后防线: 若当前用户是登录 admin 且要改的是 admin 角色, 二次确认
  const isEditingAdminRole = editingRole.value.code === 'admin';
  const wouldLoseStar = isEditingAdminRole && !roleEditForm.permissions.includes('*');
  if (wouldLoseStar) {
    try {
      await ElMessageBox.confirm(
        '你正在去掉 admin 角色的 * 通配权限. 一旦保存, 未来新增的权限将默认不属于 admin, 你可能需要手动维护. 确定继续?',
        '危险操作', { type: 'warning', confirmButtonText: '我懂, 继续', cancelButtonText: '取消' }
      );
    } catch { return; }
  }

  savingRole.value = true;
  try {
    const payload = { permissions: roleEditForm.permissions };
    if (!editingRole.value.is_builtin) {
      payload.name = roleEditForm.name;
      payload.description = roleEditForm.description;
    }
    await updateRole(editingRole.value.id, payload);
    ElMessage.success(`已保存 ${editingRole.value.code} 的权限`);
    showRoleEditorDialog.value = false;
    editingRole.value = null;
    // 刷新角色列表 + 我自己的 me (我自己可能就在改自己的角色, 权限即时生效)
    await refreshRoles();
    await authStore.init(true);
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '保存失败');
  } finally {
    savingRole.value = false;
  }
}

// ============================================================
// v3.10+ 阶段 7+: 创建角色对话框
// ============================================================

const showCreateRoleDialog = ref(false);
const creatingRole = ref(false);
const createRoleFormRef = ref(null);
const createRoleForm = reactive({
  code: '',
  name: '',
  description: '',
  permissions: [],
});
const createRoleRules = {
  code: [
    { required: true, message: '请输入角色代码', trigger: 'blur' },
    { pattern: /^[a-z][a-z0-9_]*$/, message: '小写字母开头, 仅允许小写字母/数字/下划线', trigger: 'blur' },
    { min: 2, max: 32, message: '长度 2-32 个字符', trigger: 'blur' },
  ],
  name: [{ required: true, message: '请输入角色名称', trigger: 'blur' }],
};

function openCreateRoleDialog() {
  createRoleForm.code = '';
  createRoleForm.name = '';
  createRoleForm.description = '';
  createRoleForm.permissions = [];
  showCreateRoleDialog.value = true;
}

async function onCreateRoleSubmit() {
  if (!createRoleFormRef.value) return;
  try { await createRoleFormRef.value.validate(); } catch { return; }
  creatingRole.value = true;
  try {
    await createRole({
      code: createRoleForm.code,
      name: createRoleForm.name,
      description: createRoleForm.description || null,
      permissions: createRoleForm.permissions,
    });
    ElMessage.success(`角色 ${createRoleForm.code} 已创建`);
    showCreateRoleDialog.value = false;
    await refreshRoles();
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '创建失败');
  } finally {
    creatingRole.value = false;
  }
}

// ============================================================
// v3.10+ 阶段 7+: 删除角色 (仅非内置)
// ============================================================

async function onDeleteRole(role) {
  if (role.is_builtin) {
    ElMessage.warning('内置角色不允许删除');
    return;
  }
  try {
    await ElMessageBox.confirm(
      `确定删除角色「${role.name}」(${role.code}) 吗? 此操作不可撤销.`,
      '删除角色', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }
    );
  } catch { return; }
  try {
    await deleteRole(role.id);
    ElMessage.success(`角色 ${role.code} 已删除`);
    await refreshRoles();
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '删除失败');
  }
}

// ============================================================
// v3.10+ 阶段 7+: 创建账号对话框
// ============================================================

const showCreateUserDialog = ref(false);
const creatingUser = ref(false);
const createUserFormRef = ref(null);
const createUserForm = reactive({
  username: '',
  password: '',
  password_confirm: '',
  display_name: '',
  // v3.10+ 阶段 7+ 修正: 单角色, 一人一身份, 更贴近工厂场景认知
  role_code: 'operator',
  active: true,
});
const createUserRules = {
  username: [
    { required: true, message: '请输入用户名 (工号)', trigger: 'blur' },
    { min: 3, max: 64, message: '长度 3-64 个字符', trigger: 'blur' },
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 6, message: '密码至少 6 位', trigger: 'blur' },
  ],
  password_confirm: [{
    validator: (_r, v, cb) => {
      if (v !== createUserForm.password) cb(new Error('两次密码不一致'));
      else cb();
    },
    trigger: 'blur',
  }],
  role_code: [{ required: true, message: '请选择身份', trigger: 'change' }],
};

function openCreateUserDialog() {
  createUserForm.username = '';
  createUserForm.password = '';
  createUserForm.password_confirm = '';
  createUserForm.display_name = '';
  createUserForm.role_code = 'operator';
  createUserForm.active = true;
  showCreateUserDialog.value = true;
}

async function onCreateUserSubmit() {
  if (!createUserFormRef.value) return;
  try { await createUserFormRef.value.validate(); } catch { return; }
  creatingUser.value = true;
  try {
    await createUser({
      username: createUserForm.username,
      password: createUserForm.password,
      display_name: createUserForm.display_name || null,
      // 后端接受 role_codes 数组, 我们这里只传单个
      role_codes: [createUserForm.role_code],
      active: createUserForm.active,
    });
    ElMessage.success(`账号 ${createUserForm.username} 已创建`);
    showCreateUserDialog.value = false;
    await refreshUsers();
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '创建失败');
  } finally {
    creatingUser.value = false;
  }
}

// ============================================================
// v3.10+ 阶段 7+: 重置密码对话框
// ============================================================

const showResetPwdDialog = ref(false);
const resettingPwd = ref(false);
const resetPwdFormRef = ref(null);
const resetPwdTarget = ref(null);
const resetPwdForm = reactive({
  new_password: '',
  new_password_confirm: '',
});
const resetPwdRules = {
  new_password: [
    { required: true, message: '请输入新密码', trigger: 'blur' },
    { min: 6, message: '密码至少 6 位', trigger: 'blur' },
  ],
  new_password_confirm: [{
    validator: (_r, v, cb) => {
      if (v !== resetPwdForm.new_password) cb(new Error('两次密码不一致'));
      else cb();
    },
    trigger: 'blur',
  }],
};

function openResetPwdDialog(user) {
  resetPwdTarget.value = user;
  resetPwdForm.new_password = '';
  resetPwdForm.new_password_confirm = '';
  showResetPwdDialog.value = true;
}

async function onResetPwdSubmit() {
  if (!resetPwdFormRef.value) return;
  try { await resetPwdFormRef.value.validate(); } catch { return; }
  resettingPwd.value = true;
  try {
    await updateUser(resetPwdTarget.value.id, {
      new_password: resetPwdForm.new_password,
    });
    ElMessage.success(`已重置 ${resetPwdTarget.value.username} 的密码`);
    showResetPwdDialog.value = false;
    resetPwdTarget.value = null;
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '重置失败');
  } finally {
    resettingPwd.value = false;
  }
}

// ============================================================
// v3.10+ 阶段 7+: 启用/禁用账号
// ============================================================

const adminUserCount = computed(() => {
  return users.value.filter(u => (u.roles || []).includes('admin') && u.active).length;
});

async function onToggleUserActive(user) {
  // 禁止禁用自己
  if (user.id === authStore.currentUser.id) {
    ElMessage.warning('不能禁用当前登录账号');
    return;
  }
  // 禁止禁用最后一个 admin
  if (user.active && (user.roles || []).includes('admin') && adminUserCount.value <= 1) {
    ElMessage.warning('不能禁用最后一个 admin 账号');
    return;
  }
  const next = !user.active;
  const verb = next ? '启用' : '禁用';
  try {
    await ElMessageBox.confirm(
      `确定${verb}账号「${user.display_name || user.username}」吗?`,
      `${verb}账号`, { type: 'warning', confirmButtonText: verb, cancelButtonText: '取消' }
    );
  } catch { return; }
  try {
    await updateUser(user.id, { active: next });
    ElMessage.success(`已${verb}账号 ${user.username}`);
    await refreshUsers();
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || e?.message || `${verb}失败`);
  }
}

// ============================================================
// v3.10+ 阶段 7+: 删除账号
// ============================================================

async function onDeleteUser(user) {
  if (user.id === authStore.currentUser.id) {
    ElMessage.warning('不能删除当前登录账号');
    return;
  }
  if ((user.roles || []).includes('admin') && adminUserCount.value <= 1) {
    ElMessage.warning('不能删除最后一个 admin 账号');
    return;
  }
  try {
    await ElMessageBox.confirm(
      `确定删除账号「${user.display_name || user.username}」吗? 此操作不可撤销, 历史检测记录会保留(operator_id 置空).`,
      '删除账号', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }
    );
  } catch { return; }
  try {
    await deleteUser(user.id);
    ElMessage.success(`账号 ${user.username} 已删除`);
    await refreshUsers();
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '删除失败');
  }
}

// ============================================================
// v3.10+ 阶段 7+: 匿名 operator 兜底开关
// ============================================================

const allowAnonymous = ref(true);
const allowAnonymousLoading = ref(false);
const sessionPersist = ref(true);
const sessionPersistLoading = ref(false);

async function refreshAuthConfig() {
  if (!authStore.authEnabled) return;
  try {
    const res = await getAuthConfig();
    allowAnonymous.value = res.data?.allow_anonymous_operator !== false;
    sessionPersist.value = res.data?.session_persist !== false;
  } catch {
    // 匿名也能读, 失败不阻塞主界面
  }
}

async function onToggleAllowAnonymous(next) {
  if (!authStore.hasPermission('system.auth_toggle')) {
    ElMessage.warning('需要 system.auth_toggle 权限');
    allowAnonymous.value = !next;
    return;
  }
  if (!next) {
    try {
      await ElMessageBox.confirm(
        '关闭匿名兜底后, 未登录的客户端将无法使用任何功能, 必须先登录. 适合严格安全场景. 确定关闭?',
        '关闭匿名兜底', { type: 'warning', confirmButtonText: '我懂, 关闭', cancelButtonText: '取消' }
      );
    } catch {
      allowAnonymous.value = !next;
      return;
    }
  }
  allowAnonymousLoading.value = true;
  try {
    await updateAuthConfig({ allow_anonymous_operator: next });
    allowAnonymous.value = next;
    // 同步给 store 让路由守卫立即生效
    authStore.allowAnonymous = next;
    ElMessage.success(next ? '已开启匿名兜底' : '已关闭匿名兜底; 未登录将被强制跳转登录页');
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '保存失败');
    allowAnonymous.value = !next;
  } finally {
    allowAnonymousLoading.value = false;
  }
}

async function onToggleSessionPersist(next) {
  if (!authStore.hasPermission('system.auth_toggle')) {
    ElMessage.warning('需要 system.auth_toggle 权限');
    sessionPersist.value = !next;
    return;
  }
  if (!next) {
    try {
      await ElMessageBox.confirm(
        '关闭"重启后保持登录"后, 关 tab / 重启软件就要重新输密码. 适合高安全场景. 确定关闭?',
        '关闭会话保持', { type: 'warning', confirmButtonText: '关闭', cancelButtonText: '取消' }
      );
    } catch {
      sessionPersist.value = !next;
      return;
    }
  }
  sessionPersistLoading.value = true;
  try {
    await updateAuthConfig({ session_persist: next });
    sessionPersist.value = next;
    authStore.sessionPersist = next;
    ElMessage.success(next
      ? '已开启会话保持, 后续登录会跨重启自动恢复'
      : '已关闭会话保持, 关 tab / 重启后需要重新登录');
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '保存失败');
    sessionPersist.value = !next;
  } finally {
    sessionPersistLoading.value = false;
  }
}

// 启动时拉一次匿名 + 会话保持开关
onMounted(() => { refreshAuthConfig(); });
</script>
