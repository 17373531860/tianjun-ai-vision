import { defineStore } from 'pinia';

export const useSourceStore = defineStore('source', {
  state: () => ({
    // 输入源类型: 'camera' | 'video' | 'image' | 'hikvision' | 'rtsp' | 'hcnetsdk'
    sourceType: 'camera',
    // 摄像头设置
    cameraSettings: {
      deviceIndex: 0,
      resolution: '1280x720',
      fps: 60,
      autoExposure: true,
      exposureValue: -6
    },
    // 海康工业相机设置
    hikvisionSettings: {
      deviceIndex: 0,
      resolution: '1280x720',
      fps: 60
    },
    // RTSP 网络视频流设置
    rtspSettings: {
      url: '',
      fps: 25
    },
    // 海康设备网络SDK (HCNetSDK) 设置
    hcnetsdkSettings: {
      ip: '',
      port: 8000,
      username: 'admin',
      password: '',
      channel: 1,
      streamType: 1,
      fps: 25
    },
    // 视频设置
    videoPath: null,
    videoSpeed: 1,
    videoSyncMode: false,
    videoFileName: null,
    // 图片文件路径
    imagePath: null,
    imageFileName: null,
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
    setHikvisionSettings(settings) {
      this.hikvisionSettings = { ...this.hikvisionSettings, ...settings };
    },
    setRtspSettings(settings) {
      this.rtspSettings = { ...this.rtspSettings, ...settings };
    },
    setHcnetsdkSettings(settings) {
      this.hcnetsdkSettings = { ...this.hcnetsdkSettings, ...settings };
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
        hikvisionSettings: this.hikvisionSettings,
        rtspSettings: this.rtspSettings,
        hcnetsdkSettings: this.hcnetsdkSettings,
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
          if (config.hikvisionSettings) {
            this.hikvisionSettings = { ...this.hikvisionSettings, ...config.hikvisionSettings };
          }
          if (config.rtspSettings) {
            this.rtspSettings = { ...this.rtspSettings, ...config.rtspSettings };
          }
          if (config.hcnetsdkSettings) {
            this.hcnetsdkSettings = { ...this.hcnetsdkSettings, ...config.hcnetsdkSettings };
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
