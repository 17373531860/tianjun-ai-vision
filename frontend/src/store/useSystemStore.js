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
    },
    scan: {
      id: 'scan',
      name: '扫码提示框',
      enabled: true,
      color: '#0891b2',
      duration: 2,
      fontSize: 18,
      position: 'top-right',
      text: '扫码成功',
      subText: '',
      isSystem: true
    },
    warn_no_barcode: {
      id: 'warn_no_barcode',
      name: '未绑码提示框',
      enabled: true,
      color: '#f59e0b',
      duration: 5,
      fontSize: 16,
      position: 'top-right',
      text: '⚠ 未绑码',
      subText: '本次结算未绑定工件条码',
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
        brandName: true,
        appName: true,
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
        stepTableColumns: {
          showNo: true,
          showStep: true,
          showStatus: true,
          showPt: true,
          showResult: true
        },
        showFps: true,
        showLatency: true,
        showDetectionCount: true,
        ctIncludeNg: false,
        ptMode: 'current',  // 'avg' | 'last' | 'current' — 步骤耗时显示口径（v3.7.x: 默认改为 current, 周期结束 PT 列归零, 符合客户直觉）
        ptAggregate: 'sum',  // 'sum' | 'last' — PT 计算方式：同步骤同周期多次出现时合并求和 / 仅取最后一次（默认合并）
        // v3.10.x: 同步骤同周期多次出现的分段如何合并 (仅 ptAggregate=sum 时生效)
        //   'sum'        — 全部累加 (默认, 客户看周期内总耗时; 现场 YOLO 准时贴近真实)
        //   'max'        — 取最长一段 (YOLO 抖动严重的现场可切, 抗多段累加假象)
        //   'first_only' — 仅首段 (适合 accept_once 步骤, 但主操作不在第一段时会漏)
        ptAccumulateStrategy: 'sum',
        ptCalcMode: 'span',  // v3.9.x D 方案: 'span' | 'visible' — 跨度 / 累计可见时长. 'span' 用 step_durations / cycle_sum, 'visible' 改读 step_visible_seconds 解决"标签持续被识别 PT 拖太大"
        ctMode: 'avg',  // 'avg' | 'last' | 'current' — 周期时间显示口径
        // v3.9.x A 方案: 结算后强制保留显示 (默认关), 给产线工人多看 N 秒 OK + PT 数字
        resultHoldEnabled: false,
        resultHoldSeconds: 1.5,
        ngTop3: true,
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
    detection: JSON.parse(JSON.stringify(defaultDetection)),
    // 多工位: 每通道独立检测设置 { channelId: detectionConfig }
    channelDetections: {},
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
    // 开发者模式（默认关闭，需要密码开启）
    developerMode: false,
    // 性能设置
    performance: {
      frameLimitEnabled: false,  // 帧率限制开关（默认禁用，本地应用无需节流）
      targetStreamFps: 30,       // 目标流帧率
      // B6 截图去重（默认关）：开启后检测结果轮询携带本地已持有的步骤截图指纹，
      // 后端对内容未变的截图省略不传（前端合并保留旧图），减小高频轮询包体。
      // 关 = 行为与旧版字节级一致。
      screenshotDedup: false,
      // D1 多通道视频解码背压（默认关）：开启后多工位画面改用 createImageBitmap 解码 +
      // "每工位同时只解一帧、跟不上就丢旧留最新"的背压策略，长时间多工位运行不再因
      // 解码积压导致内存/GC 压力升高。关 = 走原 new Image() 逐帧解码路径，字节级一致。
      multiChannelBitmapDecode: false,
      halfPrecision: false,      // FP16 半精度推理（默认关闭）
      mediapipeEnabled: false,   // MediaPipe 骨架叠加（默认关闭）
      mediapipePose: true,       // 显示姿态骨架
      mediapipeHands: true,      // 显示手部关键点
      mediapipeConfidence: 0.7,  // MediaPipe 检测置信度（首检阈值）
      mediapipeInterval: 2,      // MediaPipe 处理间隔（帧）
      // v3.8.0 hands 调优：朋友同款"完美骨架" = complexity=1 + confidence=0.5 + trackConfidence=0.5
      mediapipeModelComplexity: 0,   // 0=lite (默认, 快、CPU 友好) / 1=full (慢, 精度高)
      mediapipeTrackConfidence: 0.5, // 跟踪置信度（值越低骨架越粘但抖动更大）
      // v3.8.0 工业 hand-detector 二段管线（公司日常自训模型, 路径填了即启用）
      mediapipeHandDetectorPath: '',    // 自训 hand-detector .pt 路径 (空=走基础)
      mediapipeHandDetectorKind: 'v8',  // v8 (ultralytics 默认) / v5 (legacy yolov5)
      mediapipeHandDetectorConf: 0.25,  // YOLO 框检测置信度阈值
      mediapipeHandDetectorIou: 0.45,   // NMS 阈值
      mediapipeHandDetectorImgsz: 640,  // 输入尺寸
      mediapipeHandRoiPad: 0.3,         // ROI 外扩比例（手指容易被框边切到 → 外扩）
      // v3.32.0 自定义纯色骨架样式（默认关 = MediaPipe 官方花色，老行为不变）
      mediapipeCustomStyle: false,
      mediapipePoseColor: '#00FF00',    // 姿态连线颜色
      mediapipePosePointColor: '',      // 姿态关键点颜色（空 = 跟随连线颜色）
      mediapipePoseThickness: 2,        // 姿态骨架线宽 (1-10)
      mediapipeHandsColor: '#00FF00',   // 手部连线颜色
      mediapipeHandsPointColor: '',     // 手部关键点颜色（空 = 跟随连线颜色）
      mediapipeHandsThickness: 2        // 手部骨架线宽 (1-10)
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
              },
              stepTableColumns: {
                showNo: true,
                showStep: true,
                showStatus: true,
                showPt: true,
                showResult: true,
                ...(saved.monitor?.stepTableColumns || {})
              }
            }
          };
        } catch (e) {}
      }
    },
    // 从项目加载检测框设置.
    // v3.8.x: 加 localStorage 兜底 + 自动回写 DB.
    //   - detectionConfig 存在: 直接合并加载, 同时刷新 localStorage 当快照
    //   - detectionConfig 为空 + 给了 projectId: 读 localStorage 当首选, 加载完后
    //     自动 updateProject 写回 DB, 让下次重启直接命中 DB 不再走兜底
    //   - detectionConfig 为空 + 没 projectId (例如用户切到"无项目"): 回默认值
    // 这是修"重启检测框配置被刷掉"bug 的核心 — 老逻辑 DB null 时直接默认, 用户改的
    // 颜色/线宽/Toast 永久丢失.
    loadDetectionFromProject(detectionConfig, projectId = null) {
      if (detectionConfig) {
        // 深度合并，确保缺失的字段使用默认值
        this.detection = {
          ...defaultDetection,
          ...detectionConfig,
          toasts: {
            ok: { ...defaultDetection.toasts.ok, ...(detectionConfig.toasts?.ok || {}) },
            ng: { ...defaultDetection.toasts.ng, ...(detectionConfig.toasts?.ng || {}) },
            scan: { ...defaultDetection.toasts.scan, ...(detectionConfig.toasts?.scan || {}) },
            warn_no_barcode: { ...defaultDetection.toasts.warn_no_barcode, ...(detectionConfig.toasts?.warn_no_barcode || {}) }
          },
          customToasts: detectionConfig.customToasts || []
        };
        // 同步 localStorage 快照 (下次冷启动 / 切项目兜底要用)
        try { localStorage.setItem('detection_settings', JSON.stringify(this.detection)); } catch {}
        return;
      }

      // DB 没有 → 试 localStorage 兜底
      let restored = null;
      try {
        const saved = localStorage.getItem('detection_settings');
        if (saved) restored = JSON.parse(saved);
      } catch {}

      if (restored) {
        this.detection = {
          ...defaultDetection,
          ...restored,
          toasts: {
            ok: { ...defaultDetection.toasts.ok, ...(restored.toasts?.ok || {}) },
            ng: { ...defaultDetection.toasts.ng, ...(restored.toasts?.ng || {}) },
            scan: { ...defaultDetection.toasts.scan, ...(restored.toasts?.scan || {}) },
            warn_no_barcode: { ...defaultDetection.toasts.warn_no_barcode, ...(restored.toasts?.warn_no_barcode || {}) }
          },
          customToasts: restored.customToasts || []
        };
        // 有 projectId 就主动回写 DB, 修复"localStorage 有 / DB 空"的历史项目
        if (projectId) {
          this.currentProjectId = projectId;
          updateProject(projectId, { detection_config: this.detection }).catch((e) => {
            console.warn('[useSystemStore] 回写 DB 失败 (localStorage 兜底已生效):', e?.message);
          });
        }
        return;
      }

      // localStorage 也空 → 用默认值
      this.detection = JSON.parse(JSON.stringify(defaultDetection));
    },
    // 为指定通道加载检测设置
    loadDetectionForChannel(channelId, detectionConfig) {
      const merged = {
        ...defaultDetection,
        ...(detectionConfig || {}),
        toasts: {
          ok: { ...defaultDetection.toasts.ok, ...(detectionConfig?.toasts?.ok || {}) },
          ng: { ...defaultDetection.toasts.ng, ...(detectionConfig?.toasts?.ng || {}) },
          scan: { ...defaultDetection.toasts.scan, ...(detectionConfig?.toasts?.scan || {}) },
          warn_no_barcode: { ...defaultDetection.toasts.warn_no_barcode, ...(detectionConfig?.toasts?.warn_no_barcode || {}) }
        },
        customToasts: detectionConfig?.customToasts || []
      };
      this.channelDetections[channelId] = merged;
    },
    // 获取指定通道的检测设置，无则回退到全局
    getChannelDetection(channelId) {
      return this.channelDetections[channelId] || this.detection;
    },
    // 保存检测框设置到项目.
    // v3.14.x 修复: 多工位 (含插件 layout.body 模式) 下, 设置页只有一份全局检测框配置,
    // 但每个工位绑各自项目, 提示框位置/颜色等是从"工位绑定项目的 detection_config"读的.
    // 老逻辑只写 currentProjectId 一个项目 → 客户改右上角, 其它工位读自己项目的旧值,
    // 表现为"怎么改都没用". 现在多工位时把同一份配置写进所有在用工位绑定的项目,
    // 并同步刷新内存里每个通道的检测配置, 改一次两个工位即时生效, 不用重启/切项目.
    async saveDetectionSettings() {
      // 同时保存到 localStorage 作为备份/默认值
      localStorage.setItem('detection_settings', JSON.stringify(this.detection));

      const targetProjectIds = new Set();
      if (this.currentProjectId) targetProjectIds.add(this.currentProjectId);

      try {
        const { getWorkstations } = await import('@/api/detection');
        const res = await getWorkstations();
        const count = res.data?.channel_count || 1;
        if (count > 1) {
          const sources = res.data?.source_configs || {};
          for (const [chStr, cfg] of Object.entries(sources)) {
            const pid = cfg?.project_id;
            if (pid) targetProjectIds.add(pid);
            // 内存里该通道检测配置一并刷新, 检测页无需重载即可生效
            this.loadDetectionForChannel(parseInt(chStr), this.detection);
          }
        }
      } catch (e) {
        console.warn('[useSystemStore] 读取工位绑定失败, 退回仅保存当前项目:', e?.message);
      }

      for (const pid of targetProjectIds) {
        try {
          await updateProject(pid, { detection_config: this.detection });
        } catch (e) {
          console.error(`保存检测框设置到项目 ${pid} 失败:`, e);
        }
      }
    },
    // 设置上次输入源
    setLastSource(type, value) {
      this.lastSourceType = type;
      this.lastSourceValue = value;
    },
    // 开发者模式
    setDeveloperMode(enabled) {
      this.developerMode = enabled;
      localStorage.setItem('developer_mode', JSON.stringify(enabled));
    },
    loadDeveloperMode() {
      try {
        const saved = localStorage.getItem('developer_mode');
        if (saved !== null) this.developerMode = JSON.parse(saved);
      } catch {}
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
