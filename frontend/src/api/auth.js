/**
 * 用户系统 API 客户端 (v3.10.0+)
 *
 * 对应后端:
 *   GET  /api/v1/auth/status                  → 看账号鉴权是否启用 + 已有几个用户
 *   GET  /api/v1/auth/me                      → 当前身份 + 权限 (用于前端渲染菜单)
 *   POST /api/v1/auth/login                   → 用户名密码登录, 返回 token
 *   POST /api/v1/auth/logout                  → 删 token (后端清内存 + 数据库)
 *   POST /api/v1/auth/enable-auth             → 启用账号鉴权 (同时强制创建首个管理员)
 *   POST /api/v1/auth/disable-auth            → 关闭账号鉴权 (需 system.auth_toggle 权限)
 *   POST /api/v1/auth/change-password         → 改自己的密码
 *   GET  /api/v1/auth/permissions/catalog     → 权限目录 (给账号管理页渲染权限树)
 *
 *   /api/v1/users/*                           → 账号 CRUD (受 system.users.manage 保护)
 *   /api/v1/roles/*                           → 角色 CRUD (受 system.roles.manage 保护)
 */
import api from './index';

// ============================================================
// 鉴权状态 / 当前身份
// ============================================================

export function getAuthStatus() {
  return api.get('/auth/status');
}

export function getMe() {
  return api.get('/auth/me');
}

// ============================================================
// 登录 / 登出 / 改密
// ============================================================

export function login(username, password) {
  return api.post('/auth/login', { username, password });
}

export function logout() {
  return api.post('/auth/logout');
}

export function changePassword(oldPassword, newPassword) {
  return api.post('/auth/change-password', {
    old_password: oldPassword,
    new_password: newPassword,
  });
}

// ============================================================
// 总开关: 启用 / 关闭账号鉴权
// ============================================================

export function enableAuth(adminUsername, adminPassword, adminDisplayName = null) {
  return api.post('/auth/enable-auth', {
    admin_username: adminUsername,
    admin_password: adminPassword,
    admin_display_name: adminDisplayName,
  });
}

export function disableAuth() {
  return api.post('/auth/disable-auth');
}

// ============================================================
// 鉴权高级配置 (匿名兜底开关 — v3.10+ 阶段 7+)
// ============================================================

export function getAuthConfig() {
  return api.get('/auth/config');
}

export function updateAuthConfig(payload) {
  // payload: { allow_anonymous_operator?: boolean }
  return api.put('/auth/config', payload);
}

// ============================================================
// 权限目录
// ============================================================

export function getPermissionCatalog() {
  return api.get('/auth/permissions/catalog');
}

// ============================================================
// 账号 CRUD (需 system.users.manage 权限)
// ============================================================

export function listUsers(active = null) {
  const params = {};
  if (active !== null && active !== undefined) params.active = active;
  return api.get('/users', { params });
}

export function getUser(id) {
  return api.get(`/users/${id}`);
}

export function createUser(payload) {
  return api.post('/users', payload);
}

export function updateUser(id, payload) {
  return api.put(`/users/${id}`, payload);
}

export function deleteUser(id) {
  return api.delete(`/users/${id}`);
}

// ============================================================
// 角色 CRUD (需 system.roles.manage 权限)
// ============================================================

export function listRoles() {
  return api.get('/roles');
}

export function getRole(id) {
  return api.get(`/roles/${id}`);
}

export function createRole(payload) {
  return api.post('/roles', payload);
}

export function updateRole(id, payload) {
  return api.put(`/roles/${id}`, payload);
}

export function deleteRole(id) {
  return api.delete(`/roles/${id}`);
}

// ============================================================
// M2M API Key 管理 (需 system.apikey.manage 权限)
// ============================================================

export function listApiKeys() {
  return api.get('/api-keys');
}

export function createApiKey(payload) {
  // payload: { name, scope, description?, expires_at? }
  // 响应里仅这一次包含 plaintext, 之后再也拿不到
  return api.post('/api-keys', payload);
}

export function toggleApiKey(id) {
  return api.put(`/api-keys/${id}/toggle`);
}

export function deleteApiKey(id) {
  return api.delete(`/api-keys/${id}`);
}
