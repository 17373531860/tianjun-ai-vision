import { defineStore } from 'pinia';
import api from '@/api/index';

// 各管理面板轮询间隔(ms)默认值 — 与后端 system_display.POLLING_DEFAULTS 对齐。
// 仅覆盖"管理面板的数据刷新"，核心检测循环 / 时钟 / 进度条不在此列。
const DEFAULTS = {
  cluster_boxes: 5000,
  cluster_slaves: 5000,
  cluster_heartbeat: 10000,
  gateway_health: 15000,
  order_list: 10000,
  scanner_status: 5000,
  external_device: 5000,
  wmax_status: 5000,
  plc_status: 3000,
  plc_live: 1500,
  trigger_status: 3000,
  trigger_live: 1500,
};

const MIN_MS = 500;

// 各管理面板「最近日志」显示条数默认值 — 与后端 system_display.LOG_LIMIT_DEFAULTS 对齐。
const LOG_DEFAULTS = {
  scanner: 30,
  external_device: 30,
  inbound: 50,
  gateway: 50,
  cluster: 20,
  plc: 60,
  trigger: 60,
};
const LOG_MIN = 1;
const LOG_MAX = 500;

export const usePollingStore = defineStore('polling', {
  state: () => ({
    intervals: { ...DEFAULTS },
    logLimits: { ...LOG_DEFAULTS },
    loaded: false,
  }),
  actions: {
    async load(force = false) {
      if (this.loaded && !force) return this.intervals;
      try {
        const { data } = await api.get('/system/polling');
        this.intervals = { ...DEFAULTS, ...(data || {}) };
      } catch (e) {
        this.intervals = { ...DEFAULTS };
      }
      try {
        const { data } = await api.get('/system/log-limits');
        this.logLimits = { ...LOG_DEFAULTS, ...(data || {}) };
      } catch (e) {
        this.logLimits = { ...LOG_DEFAULTS };
      }
      this.loaded = true;
      return this.intervals;
    },
    // 取某面板间隔(ms)，非法/过小回落默认；调用方可传自定义兜底
    get(key, fallback) {
      const v = this.intervals[key];
      if (typeof v === 'number' && v >= MIN_MS) return v;
      return fallback ?? DEFAULTS[key] ?? 5000;
    },
    // 取某面板日志显示条数，非法回落默认
    logLimit(key, fallback) {
      const v = this.logLimits[key];
      if (typeof v === 'number' && v >= LOG_MIN && v <= LOG_MAX) return v;
      return fallback ?? LOG_DEFAULTS[key] ?? 50;
    },
    async save(partial) {
      const { data } = await api.put('/system/polling', partial || {});
      if (data && data.config) this.intervals = { ...DEFAULTS, ...data.config };
      return data;
    },
    async saveLogLimits(partial) {
      const { data } = await api.put('/system/log-limits', partial || {});
      if (data && data.config) this.logLimits = { ...LOG_DEFAULTS, ...data.config };
      return data;
    },
    defaults() {
      return { ...DEFAULTS };
    },
    logDefaults() {
      return { ...LOG_DEFAULTS };
    },
  },
});
