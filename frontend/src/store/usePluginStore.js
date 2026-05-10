import { defineStore } from 'pinia';
import {
  getPlugins,
  installPlugin,
  activatePlugin,
  deactivatePlugin,
  deletePlugin,
} from '@/api/plugins';
import api from '@/api/index';

/**
 * 插件管理 Pinia store。
 *
 * 抽离自 Settings/index.vue 内联逻辑，目的：
 *   1. 让多个组件（Settings、SystemStatus、Diag）能共享同一份插件状态
 *   2. axios 调用集中在 actions，UI 只关心 reactive state
 *   3. 提供 `licenseCustomer` 一并加载, 便于 UI 高亮"客户码 vs license 不匹配"
 *
 * State:
 *   - items: PluginRecord[]
 *   - activeCustomerCode: string|null
 *   - licenseCustomer: string  (来自 /system/license-cache)
 *   - loading / uploading: boolean  (UI loading 标记)
 *
 * 用法（Vue Composition API）：
 *   const pluginStore = usePluginStore();
 *   await pluginStore.fetchAll();
 *   pluginStore.items;            // 列表
 *   pluginStore.licenseMismatch;  // computed: 当前激活插件 vs license
 */
export const usePluginStore = defineStore('plugin', {
  state: () => ({
    items: [],
    activeCustomerCode: null,
    licenseCustomer: '',
    loading: false,
    uploading: false,
    lastError: null,
  }),

  getters: {
    /** 是否有任何已安装插件 */
    hasAny: (s) => s.items.length > 0,
    /** 当前激活的插件记录（无则 null） */
    activePlugin: (s) =>
      s.activeCustomerCode
        ? s.items.find((p) => p.customer_code === s.activeCustomerCode) || null
        : null,
    /** license 与已激活插件 customer_code 是否不一致（UI 高亮警告用） */
    licenseMismatch: (s) =>
      Boolean(
        s.activeCustomerCode &&
          s.licenseCustomer &&
          s.licenseCustomer !== s.activeCustomerCode,
      ),
  },

  actions: {
    async _loadLicenseCustomer() {
      try {
        const { data } = await api.get('/system/license-cache');
        this.licenseCustomer =
          data?.customer || data?.customerName || data?.customer_name || '';
      } catch {
        this.licenseCustomer = '';
      }
    },

    /** 拉取插件列表 + license 缓存。失败会写入 lastError 但不抛。 */
    async fetchAll() {
      this.loading = true;
      this.lastError = null;
      try {
        const { data } = await getPlugins();
        this.items = data?.items || [];
        this.activeCustomerCode = data?.active_customer_code || null;
        await this._loadLicenseCustomer();
      } catch (e) {
        this.lastError =
          e?.response?.data?.detail?.message ||
          e?.response?.data?.detail ||
          e?.message ||
          '加载失败';
        throw e;
      } finally {
        this.loading = false;
      }
    },

    /** 上传安装。成功后自动重新拉取列表。 */
    async install(file) {
      if (!file) return;
      this.uploading = true;
      this.lastError = null;
      try {
        await installPlugin(file);
        await this.fetchAll();
      } catch (e) {
        const detail = e?.response?.data?.detail;
        this.lastError = detail?.message || detail || e?.message || '安装失败';
        throw e;
      } finally {
        this.uploading = false;
      }
    },

    async activate(customerCode) {
      await activatePlugin(customerCode);
      await this.fetchAll();
    },

    async deactivate(customerCode) {
      await deactivatePlugin(customerCode);
      await this.fetchAll();
    },

    async remove(customerCode) {
      await deletePlugin(customerCode);
      await this.fetchAll();
    },

    /** 测试 / 故障恢复用：清空 store 重新拉取。 */
    async reset() {
      this.items = [];
      this.activeCustomerCode = null;
      this.licenseCustomer = '';
      this.lastError = null;
      await this.fetchAll();
    },
  },
});
