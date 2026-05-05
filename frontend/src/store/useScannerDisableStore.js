import { defineStore } from 'pinia';
import {
  getScannerDisableStatus,
  toggleScannerDisable,
} from '@/api/scanner';

// v3.4.2 "按工位禁用扫码"全局状态.
//
// 后端把"扫码联动闭包"算好后返回 disabled_channels 列表 (含联动)，前端只
// 信任这个列表。各工位的码栏组件通过 isChannelDisabled(ch) 判断显隐 / 按钮
// 文字 / 颜色，所有组件共享同一份状态，切换某工位的禁用会自动联动同步.
export const useScannerDisableStore = defineStore('scannerDisable', {
  state: () => ({
    disabledChannels: [],
    loaded: false,
    loading: false,
    toggling: false,
  }),
  getters: {
    isChannelDisabled: (state) => (channelId) =>
      state.disabledChannels.includes(Number(channelId)),
    disabledSet: (state) => new Set(state.disabledChannels.map(Number)),
  },
  actions: {
    async loadStatus(force = false) {
      if (this.loaded && !force) return;
      this.loading = true;
      try {
        const { data } = await getScannerDisableStatus();
        this.disabledChannels = (data?.disabled_channels || []).map(Number);
        this.loaded = true;
      } catch (e) {
        console.warn('[scannerDisable] loadStatus failed:', e);
      } finally {
        this.loading = false;
      }
    },
    async toggle(channelId, disabled) {
      this.toggling = true;
      try {
        const { data } = await toggleScannerDisable(channelId, disabled);
        this.disabledChannels = (data?.disabled_channels || []).map(Number);
        this.loaded = true;
        return data;
      } catch (e) {
        console.error('[scannerDisable] toggle failed:', e);
        throw e;
      } finally {
        this.toggling = false;
      }
    },
    // v3.4.2 hotfix: source/status 推送的 mes.scan_disabled 同步进 store.
    // 让 backend reload (从 disk 恢复 _disabled_channels) / 别的终端切换时,
    // 本地 store 保持最新 (而不是只在 onMounted 拉一次, 之后再也不更新).
    applyServerHint(channelId, disabled) {
      const ch = Number(channelId);
      const cur = this.disabledChannels.includes(ch);
      if (disabled && !cur) {
        this.disabledChannels = [...this.disabledChannels, ch].sort((a, b) => a - b);
        this.loaded = true;
      } else if (!disabled && cur) {
        this.disabledChannels = this.disabledChannels.filter((c) => c !== ch);
        this.loaded = true;
      }
    },
  },
});
