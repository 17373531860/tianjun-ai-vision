// ==================== 调试设置 Store (v3.17.x) ====================
// 管「调试设置」页的后端半边: 后端开关同步 + 后端日志 1s 增量轮询。
// 前端开关与前端日志缓冲在 utils/debug.js (模块级, 避免初始化顺序问题),
// 本 store 只做后端通信与日志合并视图。
import { defineStore } from 'pinia';
import api from '@/api';
import {
  FRONTEND_CATEGORIES, bufferVersion,
  getFlags, setFlag, setAllFlags, getFrontendLogs, clearFrontendLogs,
} from '@/utils/debug';

export const useDebugStore = defineStore('debug', {
  state: () => ({
    backendFlags: {},          // { 'backend.mes': false, ... }
    backendCatalog: {},        // { key: {label, group} } 来自 GET /debug/flags
    backendLogs: [],           // 轮询累积 (上限 3000, 与后端缓冲对齐)
    lastSeq: 0,
    polling: false,
    paused: false,             // 暂停滚动: 停止轮询合并但不清数据
    _timer: null,
    frontendFlagsTick: 0,      // setFlag 后自增, 驱动开关矩阵重渲染
  }),

  getters: {
    frontendCatalog: () => FRONTEND_CATEGORIES,
    frontendFlags(state) {
      void state.frontendFlagsTick;
      return getFlags();
    },
    // 合并日志流: 前端缓冲 + 后端轮询, 按时间字符串排序 (HH:MM:SS.mmm 可字典序)
    mergedLogs(state) {
      void bufferVersion.value;
      const all = [...getFrontendLogs(), ...state.backendLogs];
      all.sort((a, b) => (a.ts < b.ts ? -1 : a.ts > b.ts ? 1 : 0));
      return all.slice(-3000);
    },
  },

  actions: {
    setFrontendFlag(key, on) {
      setFlag(key, on);
      this.frontendFlagsTick++;
    },
    setAllFrontendFlags(on) {
      setAllFlags(on);
      this.frontendFlagsTick++;
    },

    async fetchBackendFlags() {
      const r = await api.get('/debug/flags');
      this.backendFlags = r.data.flags || {};
      this.backendCatalog = r.data.catalog || {};
    },
    async setBackendFlag(key, on) {
      this.backendFlags = { ...this.backendFlags, [key]: !!on };
      const r = await api.put('/debug/flags', { flags: { [key]: !!on } });
      this.backendFlags = r.data.flags || this.backendFlags;
    },
    async setAllBackendFlags(on) {
      const flags = {};
      Object.keys(this.backendCatalog).forEach(k => { flags[k] = !!on; });
      const r = await api.put('/debug/flags', { flags });
      this.backendFlags = r.data.flags || {};
    },

    async pollOnce() {
      if (this.paused) return;
      try {
        const r = await api.get('/debug/logs', { params: { since_seq: this.lastSeq, limit: 500 } });
        const items = r.data.logs || [];
        if (items.length) {
          // 后端缓冲里前端回传(source=frontend)的条目在本地已有原件, 去重跳过
          const fresh = items.filter(e => e.source !== 'frontend');
          this.backendLogs.push(...fresh);
          if (this.backendLogs.length > 3000) this.backendLogs.splice(0, this.backendLogs.length - 3000);
        }
        if (r.data.max_seq != null) this.lastSeq = r.data.max_seq;
      } catch { /* 后端没起来时静默, 下一轮再试 */ }
    },
    startPolling() {
      if (this._timer) return;
      this.polling = true;
      this._timer = setInterval(() => this.pollOnce(), 1000);
      this.pollOnce();
    },
    stopPolling() {
      if (this._timer) { clearInterval(this._timer); this._timer = null; }
      this.polling = false;
    },

    async clearAll() {
      clearFrontendLogs();
      this.backendLogs = [];
      this.lastSeq = 0;
      try { await api.post('/debug/logs/clear'); } catch {}
    },
  },
});
