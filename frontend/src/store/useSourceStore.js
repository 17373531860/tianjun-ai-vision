import { defineStore } from 'pinia';

export const useSourceStore = defineStore('source', {
  state: () => ({
    // 输入源类型: 'camera' | 'video' | 'image'
    sourceType: 'camera',
    // 摄像头设置
    cameraSettings: {
      deviceIndex: 0,
      resolution: '1280x720',
      fps: 30
    },
    // 视频文件路径
    videoPath: null,
    // 图片文件路径
    imagePath: null,
    // 是否正在运行
    isStreaming: false,
    // 流 URL
    streamUrl: '/video_feed'
  }),
  actions: {
    setSourceType(type) {
      this.sourceType = type;
    },
    setCameraSettings(settings) {
      this.cameraSettings = { ...this.cameraSettings, ...settings };
    },
    setVideoPath(path) {
      this.videoPath = path;
    },
    setImagePath(path) {
      this.imagePath = path;
    },
    setStreaming(status) {
      this.isStreaming = status;
    },
    setStreamUrl(url) {
      this.streamUrl = url;
    },
    // 保存配置到 localStorage
    saveConfig() {
      const config = {
        sourceType: this.sourceType,
        cameraSettings: this.cameraSettings
      };
      localStorage.setItem('source_config', JSON.stringify(config));
    },
    // 加载配置
    loadConfig() {
      const saved = localStorage.getItem('source_config');
      if (saved) {
        try {
          const config = JSON.parse(saved);
          this.sourceType = config.sourceType || 'camera';
          if (config.cameraSettings) {
            this.cameraSettings = { ...this.cameraSettings, ...config.cameraSettings };
          }
        } catch (e) {
          console.error('加载输入源配置失败:', e);
        }
      }
    }
  }
});
