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
    // 视频设置
    videoPath: null,
    videoSpeed: 1,  // 视频倍速
    videoSyncMode: false,  // 同步模式：按检测速度播放，确保每帧都被检测
    videoFileName: null,  // 视频文件名（用于显示）
    // 图片文件路径
    imagePath: null,
    imageFileName: null,  // 图片文件名
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
    setVideoSpeed(speed) {
      this.videoSpeed = speed;
    },
    setVideoSyncMode(enabled) {
      this.videoSyncMode = enabled;
    },
    setVideoFileName(name) {
      this.videoFileName = name;
    },
    setImagePath(path) {
      this.imagePath = path;
    },
    setImageFileName(name) {
      this.imageFileName = name;
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
        cameraSettings: this.cameraSettings,
        videoPath: this.videoPath,
        videoSpeed: this.videoSpeed,
        videoSyncMode: this.videoSyncMode,
        videoFileName: this.videoFileName,
        imagePath: this.imagePath,
        imageFileName: this.imageFileName
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
          this.videoPath = config.videoPath || null;
          this.videoSpeed = config.videoSpeed || 1;
          this.videoSyncMode = config.videoSyncMode || false;
          this.videoFileName = config.videoFileName || null;
          this.imagePath = config.imagePath || null;
          this.imageFileName = config.imageFileName || null;
        } catch (e) {
          console.error('加载输入源配置失败:', e);
        }
      }
    }
  }
});
