import { defineStore } from 'pinia';

export const useSystemStore = defineStore('system', {
  state: () => ({
    language: 'zh-CN',
    theme: 'light',
    plcStatus: 'online', // 模拟初始状态
    unreadAlarms: 2,     // 模拟初始状态
    // 显示设置 (Display Settings)
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
    // 检测框设置 (Detection Box Settings)
    detection: {
      // 检测框
      boxColor: '#00FF00',          // 默认绿色
      boxColorNG: '#FF0000',        // NG 红色
      boxLineWidth: 2,              // 线宽
      labelFontSize: 14,            // 标签字体大小
      showConfidence: true,         // 显示置信度
      
      // 系统预设提示框（合格/NG）
      toasts: {
        ok: {
          id: 'ok',
          name: '合格提示框',
          color: '#10b981',
          duration: 3,
          fontSize: 18,
          position: 'top-right',
          text: '合格',
          subText: '',
          isSystem: true
        },
        ng: {
          id: 'ng',
          name: 'NG提示框',
          color: '#ef4444',
          duration: 3,
          fontSize: 18,
          position: 'top-right',
          text: '不合格',
          subText: '',
          isSystem: true
        }
      },
      
      // 自定义提示框列表
      customToasts: []
    },
    // 数据管理设置 (Data Settings)
    data: {
      retentionDays: 30,
      autoCleanup: true,
      autoBackup: false,
      backupPath: '/backup/data'
    },
    // 上次输入源设置（用于自动保存功能）
    lastSourceType: null,
    lastSourceValue: null
  }),
  actions: {
    setLanguage(lang) {
      this.language = lang;
    },
    setTheme(theme) {
      this.theme = theme;
    },
    // 加载保存的设置
    loadSettings() {
      const displaySaved = localStorage.getItem('display_settings');
      if (displaySaved) {
        try {
          this.display = JSON.parse(displaySaved);
        } catch (e) {}
      }
      const detectionSaved = localStorage.getItem('detection_settings');
      if (detectionSaved) {
        try {
          this.detection = { ...this.detection, ...JSON.parse(detectionSaved) };
        } catch (e) {}
      }
    },
    // 保存检测框设置
    saveDetectionSettings() {
      localStorage.setItem('detection_settings', JSON.stringify(this.detection));
    },
    // 设置上次输入源
    setLastSource(type, value) {
      this.lastSourceType = type;
      this.lastSourceValue = value;
    }
  },
});
