import { defineStore } from 'pinia';
import { updateProject } from '@/api/project';

// 默认检测框设置
const defaultDetection = {
  // 检测框
  boxColor: '#00FF00',          // 默认绿色
  boxColorNG: '#FF0000',        // NG 红色
  boxLineWidth: 2,              // 线宽
  labelFontSize: 14,            // 标签字体大小
  showConfidence: true,         // 显示置信度
  
  // NG reason display
  showNgReason: false,
  
  // Voice announcement
  voiceEnabled: false,
  voiceVolume: 1.0,
  
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
};

export const useSystemStore = defineStore('system', {
  state: () => ({
    language: 'zh-CN',
    theme: 'light',
    // 检测运行状态（用于全局禁用控件）
    isDetecting: false,
    // 当前项目 ID（用于保存设置）
    currentProjectId: null,
    // 显示设置 (Display Settings) - 全局设置，不绑定项目
    display: {
      // 基本信息
      brandName: '天军科技AI',      // 品牌/系统名称
      appName: '视觉AI行为引导系统',  // 软件名称
      inspectorName: '张三',        // 检测员姓名
      deviceNumber: '251011',       // 设备编号
      // 导航栏显示开关
      navbar: {
        projectSelector: true,
        inspector: true,
        deviceId: true,
        mode: true,
        status: true,
        runtime: true,
        realtime: true
      },
      monitor: {
        stepStrip: true,
        statsPanel: true,
        defectChart: true,
        capacityChart: true,
        stepTable: true,
        showFps: true,
        showLatency: true,
        showDetectionCount: true,
        ctIncludeNg: false,
        ngTopDisplayMode: 'percentage',  // 'percentage' | 'count'
        defaultCounters: {
          showTotal: true,      // 总产量
          showGood: true,       // 合格总数
          showBad: true,        // 不良总数
          showNgSteps: true     // NG步骤
        }
      }
    },
    // 检测框设置 (Detection Box Settings) - 绑定项目
    detection: { ...defaultDetection },
    // 数据管理设置 (Data Settings)
    data: {
      retentionDays: 30,
      autoCleanup: true,
      autoBackup: false,
      backupPath: '/backup/data'
    },
    // 上次输入源设置（用于自动保存功能）
    lastSourceType: null,
    lastSourceValue: null,
    // 性能设置
    performance: {
      frameLimitEnabled: false,  // 帧率限制开关（默认禁用，本地应用无需节流）
      targetStreamFps: 30,       // 目标流帧率
      halfPrecision: false,      // FP16 半精度推理（默认关闭）
      mediapipeEnabled: false,   // MediaPipe 骨架叠加（默认关闭）
      mediapipePose: true,       // 显示姿态骨架
      mediapipeHands: true,      // 显示手部关键点
      mediapipeConfidence: 0.7,  // MediaPipe 检测置信度
      mediapipeInterval: 2       // MediaPipe 处理间隔（帧）
    }
  }),
  actions: {
    setLanguage(lang) {
      this.language = lang;
    },
    setTheme(theme) {
      this.theme = theme;
    },
    // 设置检测运行状态
    setDetecting(value) {
      this.isDetecting = value;
    },
    // 设置当前项目 ID
    setCurrentProjectId(projectId) {
      this.currentProjectId = projectId;
    },
    // 加载保存的设置（全局显示设置）
    loadSettings() {
      const displaySaved = localStorage.getItem('display_settings');
      if (displaySaved) {
        try {
          const saved = JSON.parse(displaySaved);
          // 深度合并，确保新增的设置项有默认值
          this.display = {
            ...this.display,
            ...saved,
            navbar: { ...this.display.navbar, ...(saved.navbar || {}) },
            monitor: { 
              ...this.display.monitor, 
              ...(saved.monitor || {}),
              // 确保 defaultCounters 有默认值
              defaultCounters: {
                showTotal: true,
                showGood: true,
                showBad: true,
                showNgSteps: true,
                ...(saved.monitor?.defaultCounters || {})
              }
            }
          };
        } catch (e) {}
      }
    },
    // 从项目加载检测框设置
    loadDetectionFromProject(detectionConfig) {
      if (detectionConfig) {
        // 深度合并，确保缺失的字段使用默认值
        this.detection = {
          ...defaultDetection,
          ...detectionConfig,
          toasts: {
            ok: { ...defaultDetection.toasts.ok, ...(detectionConfig.toasts?.ok || {}) },
            ng: { ...defaultDetection.toasts.ng, ...(detectionConfig.toasts?.ng || {}) }
          },
          customToasts: detectionConfig.customToasts || []
        };
      } else {
        // 没有项目配置，使用默认值
        this.detection = { ...defaultDetection };
      }
    },
    // 保存检测框设置到项目
    async saveDetectionSettings() {
      if (this.currentProjectId) {
        try {
          await updateProject(this.currentProjectId, {
            detection_config: this.detection
          });
        } catch (e) {
          console.error('保存检测框设置到项目失败:', e);
        }
      }
      // 同时保存到 localStorage 作为备份/默认值
      localStorage.setItem('detection_settings', JSON.stringify(this.detection));
    },
    // 设置上次输入源
    setLastSource(type, value) {
      this.lastSourceType = type;
      this.lastSourceValue = value;
    },
    // 加载性能设置
    loadPerformanceSettings() {
      const saved = localStorage.getItem('performance_settings');
      if (saved) {
        try {
          this.performance = { ...this.performance, ...JSON.parse(saved) };
        } catch (e) {}
      }
    },
    // 保存性能设置
    savePerformanceSettings() {
      localStorage.setItem('performance_settings', JSON.stringify(this.performance));
    }
  },
});
