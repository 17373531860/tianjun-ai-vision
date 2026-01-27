import { defineStore } from 'pinia';

export const useSystemStore = defineStore('system', {
  state: () => ({
    language: 'zh-CN',
    theme: 'light',
    plcStatus: 'online', 
    unreadAlarms: 2,     
    // Display Settings
    display: {
      navbar: {
        projectSelector: true,
        inspector: true,
        deviceId: true,
        mode: true,
        status: true,
        runtime: true
      },
      monitor: {
        stepStrip: true,
        statsPanel: true,
        defectChart: true,
        capacityChart: true,
        stepTable: true
      }
    },
    // Data Settings
    data: {
      retentionDays: 30,
      autoCleanup: true,
      autoBackup: false,
      backupPath: '/backup/data'
    }
  }),
  actions: {
    setLanguage(lang) {
      this.language = lang;
    },
    setTheme(theme) {
      this.theme = theme;
    },
    addLog(msg, type = 'info') {
      console.log(`[${type}] ${msg}`);
    },
    clearAlarms() {
      this.unreadAlarms = 0;
    }
  },
});

export default useSystemStore;
