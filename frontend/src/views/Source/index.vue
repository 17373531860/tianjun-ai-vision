<template>
  <div class="p-6 h-full overflow-y-auto">
    <h2 class="text-2xl font-bold mb-6 border-l-4 border-tech-blue pl-3 text-white">输入源设置</h2>

    <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
      <!-- 左侧：输入源选择 -->
      <div class="space-y-6">
        <!-- 输入源类型选择 -->
        <el-card shadow="never" class="bg-slate-800 border-slate-700">
          <template #header>
            <div class="flex items-center gap-2">
              <el-icon class="text-tech-blue"><VideoCamera /></el-icon>
              <span class="font-bold text-white">选择输入源类型</span>
            </div>
          </template>
          
          <el-radio-group v-model="sourceType" class="flex flex-col gap-4" @change="handleSourceTypeChange">
            <el-radio value="camera" class="!mr-0">
              <div class="flex items-center gap-2">
                <el-icon><Camera /></el-icon>
                <span>摄像头</span>
                <el-tag type="info" size="small">USB / 海康工业相机</el-tag>
              </div>
            </el-radio>
            <el-radio value="video" class="!mr-0">
              <div class="flex items-center gap-2">
                <el-icon><VideoPlay /></el-icon>
                <span>本地视频文件</span>
              </div>
            </el-radio>
            <el-radio value="image" class="!mr-0">
              <div class="flex items-center gap-2">
                <el-icon><Picture /></el-icon>
                <span>本地图片文件</span>
              </div>
            </el-radio>
          </el-radio-group>
        </el-card>

        <!-- 摄像头设置（统一 USB 和海康工业相机） -->
        <el-card v-if="sourceType === 'camera'" shadow="never" class="bg-slate-800 border-slate-700">
          <template #header>
            <span class="font-bold text-white">摄像头设置</span>
          </template>
          
          <el-form label-position="top">
            <el-form-item label="选择摄像头">
              <el-select v-model="selectedCameraId" class="w-full" placeholder="选择摄像头设备" value-key="id">
                <el-option-group v-if="usbCameras.length > 0" label="USB 摄像头">
                  <el-option 
                    v-for="cam in usbCameras" 
                    :key="cam.id" 
                    :label="cam.name" 
                    :value="cam.id"
                  />
                </el-option-group>
                <el-option-group v-if="hikvisionCameras.length > 0" label="海康工业相机">
                  <el-option 
                    v-for="cam in hikvisionCameras" 
                    :key="cam.id" 
                    :label="cam.name" 
                    :value="cam.id"
                  />
                </el-option-group>
                <el-option v-if="allCameras.length === 0" disabled value="" label="未检测到摄像头设备" />
              </el-select>
              <div class="flex items-center gap-2 mt-2">
                <el-button type="primary" link @click="refreshAllCameras" :loading="loadingCameras">
                  <el-icon class="mr-1"><Refresh /></el-icon> 刷新设备列表
                </el-button>
                <span class="text-xs text-gray-500">
                  已检测: USB {{ usbCameras.length }} 个, 海康 {{ hikvisionCameras.length }} 个
                </span>
              </div>
            </el-form-item>
            
            <el-form-item label="分辨率">
              <el-select v-model="cameraSettings.resolution" class="w-full">
                <el-option label="640 x 480" value="640x480" />
                <el-option label="1280 x 720 (720p)" value="1280x720" />
                <el-option label="1920 x 1080 (1080p)" value="1920x1080" />
              </el-select>
            </el-form-item>
            
            <el-form-item label="帧率 (FPS)">
              <el-select v-model="cameraSettings.fps" class="w-full">
                <el-option label="5 FPS (省性能)" :value="5" />
                <el-option label="10 FPS (推荐：匹配检测速度)" :value="10" />
                <el-option label="15 FPS" :value="15" />
                <el-option label="30 FPS" :value="30" />
                <el-option label="60 FPS" :value="60" />
              </el-select>
              <div class="text-xs text-gray-400 mt-1">
                提示：如果检测速度跟不上，降低帧率可以避免丢帧
              </div>
            </el-form-item>
          </el-form>
        </el-card>

        <!-- 视频文件设置 -->
        <el-card v-if="sourceType === 'video'" shadow="never" class="bg-slate-800 border-slate-700">
          <template #header>
            <span class="font-bold text-white">视频文件设置</span>
          </template>
          
          <el-upload
            ref="videoUploadRef"
            drag
            action="#"
            :auto-upload="false"
            :on-change="handleVideoChange"
            :limit="1"
            accept=".mp4,.avi,.mov,.mkv"
          >
            <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
            <div class="el-upload__text">
              拖拽视频文件到此处或 <em>点击上传</em>
            </div>
            <template #tip>
              <div class="el-upload__tip">支持 MP4, AVI, MOV, MKV 格式</div>
            </template>
          </el-upload>
          
          <div v-if="videoFile" class="mt-4 p-3 bg-slate-900 rounded">
            <p class="text-sm text-gray-300">已选择: {{ videoFile.name }}</p>
            <p class="text-xs text-gray-500">大小: {{ (videoFile.size / 1024 / 1024).toFixed(2) }} MB</p>
          </div>
          
          <!-- 显示上次使用的视频文件 -->
          <div v-else-if="lastVideoFileName && sourceStore.videoPath" class="mt-4 p-3 bg-slate-900 rounded border border-cyan-800">
            <div class="flex items-center justify-between">
              <div>
                <p class="text-sm text-cyan-400">上次使用: {{ lastVideoFileName }}</p>
                <p class="text-xs text-gray-500">可直接使用上次的视频文件</p>
              </div>
              <el-button type="primary" size="small" @click="useLastVideo">
                使用此视频
              </el-button>
            </div>
          </div>
          
          <el-form label-position="top" class="mt-4">
            <el-form-item label="播放倍速">
              <el-select v-model="videoSettings.speed" class="w-full">
                <el-option label="0.5x (慢速)" :value="0.5" />
                <el-option label="1x (正常)" :value="1" />
                <el-option label="2x" :value="2" />
                <el-option label="4x" :value="4" />
                <el-option label="8x" :value="8" />
              </el-select>
              <div class="text-xs text-gray-500 mt-1">倍速越高，检测速度越快，但可能影响检测精度</div>
            </el-form-item>
          </el-form>
        </el-card>

        <!-- 图片文件设置 -->
        <el-card v-if="sourceType === 'image'" shadow="never" class="bg-slate-800 border-slate-700">
          <template #header>
            <span class="font-bold text-white">图片文件设置</span>
          </template>
          
          <el-upload
            ref="imageUploadRef"
            drag
            action="#"
            :auto-upload="false"
            :on-change="handleImageChange"
            :limit="1"
            accept=".jpg,.jpeg,.png,.bmp"
          >
            <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
            <div class="el-upload__text">
              拖拽图片文件到此处或 <em>点击上传</em>
            </div>
            <template #tip>
              <div class="el-upload__tip">支持 JPG, PNG, BMP 格式</div>
            </template>
          </el-upload>
          
          <div v-if="imageFile" class="mt-4 p-3 bg-slate-900 rounded">
            <p class="text-sm text-gray-300">已选择: {{ imageFile.name }}</p>
            <p class="text-xs text-gray-500">大小: {{ (imageFile.size / 1024).toFixed(2) }} KB</p>
          </div>
          
          <!-- 显示上次使用的图片文件 -->
          <div v-else-if="lastImageFileName && sourceStore.imagePath" class="mt-4 p-3 bg-slate-900 rounded border border-cyan-800">
            <div class="flex items-center justify-between">
              <div>
                <p class="text-sm text-cyan-400">上次使用: {{ lastImageFileName }}</p>
                <p class="text-xs text-gray-500">可直接使用上次的图片文件</p>
              </div>
              <el-button type="primary" size="small" @click="useLastImage">
                使用此图片
              </el-button>
            </div>
          </div>
        </el-card>
        
        <!-- 操作按钮 -->
        <div class="flex gap-4">
          <el-button type="primary" size="large" class="flex-1" @click="saveAndStart" :loading="saving">
            <el-icon class="mr-2"><Check /></el-icon>
            保存并启动检测
          </el-button>
        </div>
      </div>

      <!-- 右侧：当前配置 -->
      <div class="space-y-6">
        <el-card shadow="never" class="bg-slate-800 border-slate-700">
          <template #header>
            <span class="font-bold text-white">当前配置摘要</span>
          </template>
          
          <el-descriptions :column="1" border size="small">
            <el-descriptions-item label="输入源类型">{{ sourceTypeLabel }}</el-descriptions-item>
            <el-descriptions-item v-if="sourceType === 'camera'" label="摄像头设备">
              {{ selectedCamera?.name || '未选择' }}
            </el-descriptions-item>
            <el-descriptions-item v-if="sourceType === 'camera'" label="设备类型">
              {{ selectedCamera?.type === 'hikvision' ? '海康工业相机' : 'USB 摄像头' }}
            </el-descriptions-item>
            <el-descriptions-item v-if="sourceType === 'camera'" label="分辨率">{{ cameraSettings.resolution }}</el-descriptions-item>
            <el-descriptions-item v-if="sourceType === 'camera'" label="帧率">{{ cameraSettings.fps }} FPS</el-descriptions-item>
            <el-descriptions-item v-if="sourceType === 'video'" label="视频文件">{{ videoFile?.name || '未选择' }}</el-descriptions-item>
            <el-descriptions-item v-if="sourceType === 'video'" label="播放倍速">{{ videoSettings.speed }}x</el-descriptions-item>
            <el-descriptions-item v-if="sourceType === 'image'" label="图片文件">{{ imageFile?.name || '未选择' }}</el-descriptions-item>
          </el-descriptions>
        </el-card>
        
        <el-alert
          title="使用说明"
          type="info"
          :closable="false"
          show-icon
        >
          <template #default>
            <ul class="text-xs mt-2 space-y-1 text-gray-300">
              <li>1. 选择输入源类型（摄像头/视频/图片）</li>
              <li>2. 配置相关参数或上传文件</li>
              <li>3. 点击"保存并启动检测"开始使用</li>
              <li>4. 系统将自动跳转到检测中心页面</li>
            </ul>
          </template>
        </el-alert>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue';
import { useRouter } from 'vue-router';
import { 
  VideoCamera, Camera, VideoPlay, Picture, 
  UploadFilled, Refresh, Check
} from '@element-plus/icons-vue';
import { ElMessage } from 'element-plus';
import api from '@/api/index';
import { useSourceStore } from '@/store/useSourceStore';
import { useSystemStore } from '@/store/useSystemStore';

const router = useRouter();
const sourceStore = useSourceStore();
const systemStore = useSystemStore();

// 输入源类型
const sourceType = ref('camera');

// 摄像头设置
const cameraSettings = ref({
  deviceIndex: 0,
  resolution: '1280x720',
  fps: 30
});

// 统一摄像头列表（USB + 海康）
const usbCameras = ref([]);       // USB 摄像头列表
const hikvisionCameras = ref([]); // 海康工业相机列表
const selectedCameraId = ref(''); // 选中的摄像头 ID（格式：usb_0 或 hik_0）

// 所有摄像头的计算属性
const allCameras = computed(() => [...usbCameras.value, ...hikvisionCameras.value]);

// 获取选中的摄像头信息
const selectedCamera = computed(() => {
  if (!selectedCameraId.value) return null;
  return allCameras.value.find(c => c.id === selectedCameraId.value);
});

// 文件
const videoFile = ref(null);
const imageFile = ref(null);
const videoUploadRef = ref(null);
const imageUploadRef = ref(null);

// 视频设置
const videoSettings = ref({
  speed: 1  // 默认 1 倍速
});

// 状态
const loadingCameras = ref(false);
const saving = ref(false);

// 计算属性
const sourceTypeLabel = computed(() => {
  const labels = {
    camera: '摄像头',
    video: '本地视频',
    image: '本地图片'
  };
  return labels[sourceType.value] || '未知';
});

// 刷新所有摄像头列表（USB + 海康）
const refreshAllCameras = async () => {
  loadingCameras.value = true;
  
  // 并行获取 USB 和海康摄像头
  const [usbResult, hikResult] = await Promise.allSettled([
    api.get('/source/cameras', { params: { refresh: true } }),
    api.get('/source/hikvision/cameras')
  ]);
  
  // 处理 USB 摄像头结果
  if (usbResult.status === 'fulfilled') {
    const cameras = usbResult.value.data.cameras || [];
    usbCameras.value = cameras.map(cam => ({
      id: `usb_${cam.index}`,
      type: 'usb',
      index: cam.index,
      name: cam.name
    }));
  } else {
    console.error('获取 USB 摄像头列表失败:', usbResult.reason);
    usbCameras.value = [];
  }
  
  // 处理海康摄像头结果
  if (hikResult.status === 'fulfilled' && hikResult.value.data.available) {
    const cameras = hikResult.value.data.cameras || [];
    hikvisionCameras.value = cameras.map(cam => ({
      id: `hik_${cam.index}`,
      type: 'hikvision',
      index: cam.index,
      name: cam.name
    }));
  } else {
    hikvisionCameras.value = [];
  }
  
  loadingCameras.value = false;
  
  const totalCount = usbCameras.value.length + hikvisionCameras.value.length;
  if (totalCount > 0) {
    ElMessage.success(`检测到 ${totalCount} 个摄像头设备`);
    // 如果没有选中的摄像头，自动选择第一个
    if (!selectedCameraId.value && allCameras.value.length > 0) {
      selectedCameraId.value = allCameras.value[0].id;
    }
  } else {
    ElMessage.warning('未检测到任何摄像头设备');
  }
};

// 处理视频文件选择
const handleVideoChange = (file) => {
  videoFile.value = file.raw;
  lastVideoFileName.value = null;  // 选择新文件后清除上次记录
};

// 使用上次的视频文件
const useLastVideo = async () => {
  if (!sourceStore.videoPath) {
    ElMessage.warning('没有上次使用的视频文件');
    return;
  }
  
  saving.value = true;
  try {
    // 先停止现有流
    try {
      await api.post('/source/camera/stop');
    } catch (e) {}
    
    // 启动视频（使用上次的路径和倍速）
    await api.post('/source/video/start', { 
      file_path: sourceStore.videoPath,
      speed: videoSettings.value.speed
    });
    
    sourceStore.setSourceType('video');
    sourceStore.setVideoSpeed(videoSettings.value.speed);
    sourceStore.setStreaming(true);
    sourceStore.saveConfig();
    
    ElMessage.success('已使用上次的视频文件，正在跳转到检测中心...');
    
    setTimeout(() => {
      router.push('/monitor');
    }, 500);
  } catch (err) {
    console.error('启动视频失败:', err);
    ElMessage.error('启动失败: ' + (err.response?.data?.detail || err.message));
  } finally {
    saving.value = false;
  }
};

// 处理图片文件选择
const handleImageChange = (file) => {
  imageFile.value = file.raw;
  lastImageFileName.value = null;  // 选择新文件后清除上次记录
};

// 使用上次的图片文件
const useLastImage = async () => {
  if (!sourceStore.imagePath) {
    ElMessage.warning('没有上次使用的图片文件');
    return;
  }
  
  saving.value = true;
  try {
    // 先停止现有流
    try {
      await api.post('/source/camera/stop');
    } catch (e) {}
    
    // 设置图片
    await api.post('/source/image/set', { file_path: sourceStore.imagePath });
    
    sourceStore.setSourceType('image');
    sourceStore.setStreaming(true);
    sourceStore.saveConfig();
    
    ElMessage.success('已使用上次的图片文件，正在跳转到检测中心...');
    
    setTimeout(() => {
      router.push('/monitor');
    }, 500);
  } catch (err) {
    console.error('设置图片失败:', err);
    ElMessage.error('设置失败: ' + (err.response?.data?.detail || err.message));
  } finally {
    saving.value = false;
  }
};

// 处理源类型变化
const handleSourceTypeChange = () => {
  // 清空文件选择
  videoFile.value = null;
  imageFile.value = null;
};

// 保存并启动
const saveAndStart = async () => {
  saving.value = true;
  
  try {
    // 先停止现有流
    try {
      await api.post('/source/camera/stop');
    } catch (e) {}
    
    if (sourceType.value === 'camera') {
      // 检查是否选择了摄像头
      if (!selectedCamera.value) {
        ElMessage.warning('请先选择摄像头设备');
        saving.value = false;
        return;
      }
      
      const [width, height] = cameraSettings.value.resolution.split('x').map(Number);
      const cam = selectedCamera.value;
      
      if (cam.type === 'usb') {
        // 启动 USB 摄像头
        await api.post('/source/camera/start', {
          device_index: cam.index,
          width,
          height,
          fps: cameraSettings.value.fps
        });
        
        // 更新 store
        sourceStore.setSourceType('camera');
        sourceStore.setCameraSettings({
          ...cameraSettings.value,
          deviceIndex: cam.index,
          cameraId: cam.id
        });
        
      } else if (cam.type === 'hikvision') {
        // 启动海康工业相机
        await api.post('/source/hikvision/start', {
          device_index: cam.index,
          width,
          height,
          fps: cameraSettings.value.fps
        });
        
        // 更新 store（内部使用 hikvision 类型）
        sourceStore.setSourceType('hikvision');
        sourceStore.setHikvisionSettings({
          ...cameraSettings.value,
          deviceIndex: cam.index,
          cameraId: cam.id
        });
      }
      
      sourceStore.setStreaming(true);
      
    } else if (sourceType.value === 'video') {
      if (!videoFile.value) {
        ElMessage.warning('请先选择视频文件');
        saving.value = false;
        return;
      }
      
      // 上传视频
      const formData = new FormData();
      formData.append('file', videoFile.value);
      const uploadRes = await api.post('/source/video/upload', formData, {
        headers: { 'Content-Type': 'multipart/form-data' }
      });
      
      // 启动视频（带倍速）
      await api.post('/source/video/start', { 
        file_path: uploadRes.data.file_path,
        speed: videoSettings.value.speed
      });
      
      sourceStore.setSourceType('video');
      sourceStore.setVideoPath(uploadRes.data.file_path);
      sourceStore.setVideoSpeed(videoSettings.value.speed);
      sourceStore.setVideoFileName(videoFile.value.name);
      sourceStore.setStreaming(true);
      
    } else if (sourceType.value === 'image') {
      if (!imageFile.value) {
        ElMessage.warning('请先选择图片文件');
        saving.value = false;
        return;
      }
      
      // 上传图片
      const formData = new FormData();
      formData.append('file', imageFile.value);
      const uploadRes = await api.post('/source/image/upload', formData, {
        headers: { 'Content-Type': 'multipart/form-data' }
      });
      
      // 设置图片
      await api.post('/source/image/set', { file_path: uploadRes.data.file_path });
      
      sourceStore.setSourceType('image');
      sourceStore.setImagePath(uploadRes.data.file_path);
      sourceStore.setImageFileName(imageFile.value.name);
      sourceStore.setStreaming(true);
    }
    
    // 保存配置
    sourceStore.saveConfig();
    
    // 保存输入源信息到 systemStore（用于自动保存功能）
    let sourceValue = null;
    if (sourceType.value === 'camera') {
      sourceValue = cameraSettings.value.deviceIndex;
    } else if (sourceType.value === 'video') {
      sourceValue = sourceStore.videoPath;
    } else if (sourceType.value === 'image') {
      sourceValue = sourceStore.imagePath;
    }
    systemStore.setLastSource(sourceType.value, sourceValue);
    
    // 更新自动保存设置
    const autoSaveSettings = localStorage.getItem('auto_save_settings');
    if (autoSaveSettings) {
      const settings = JSON.parse(autoSaveSettings);
      if (settings.enabled) {
        settings.sourceType = sourceType.value;
        settings.sourceValue = sourceValue;
        localStorage.setItem('auto_save_settings', JSON.stringify(settings));
      }
    }
    
    ElMessage.success('输入源已配置，正在跳转到检测中心...');
    
    // 跳转到检测中心页面
    setTimeout(() => {
      router.push('/monitor');
    }, 500);
    
  } catch (err) {
    console.error('启动输入源失败:', err);
    ElMessage.error('启动失败: ' + (err.response?.data?.detail || err.message));
  } finally {
    saving.value = false;
  }
};

// 加载配置（已合并到 restoreAutoSavedSource）
const loadConfig = () => {
  // 配置加载已由 restoreAutoSavedSource 处理
};

// 恢复自动保存的输入源设置
const restoreAutoSavedSource = () => {
  // 从 sourceStore 加载保存的配置
  sourceStore.loadConfig();
  
  // 恢复输入源类型（hikvision 也映射到 camera）
  const savedType = sourceStore.sourceType;
  sourceType.value = (savedType === 'hikvision') ? 'camera' : savedType;
  
  // 恢复摄像头设置
  if (sourceStore.cameraSettings) {
    cameraSettings.value = { ...cameraSettings.value, ...sourceStore.cameraSettings };
    // 恢复选中的摄像头 ID
    if (sourceStore.cameraSettings.cameraId) {
      selectedCameraId.value = sourceStore.cameraSettings.cameraId;
    }
  }
  // 如果是海康相机，也恢复
  if (savedType === 'hikvision' && sourceStore.hikvisionSettings?.cameraId) {
    selectedCameraId.value = sourceStore.hikvisionSettings.cameraId;
  }
  
  // 恢复视频设置
  videoSettings.value.speed = sourceStore.videoSpeed || 1;
  
  // 显示上次选择的视频/图片文件名（如果有）
  if (sourceStore.videoFileName) {
    lastVideoFileName.value = sourceStore.videoFileName;
  }
  if (sourceStore.imageFileName) {
    lastImageFileName.value = sourceStore.imageFileName;
  }
};

// 上次保存的文件名（用于显示）
const lastVideoFileName = ref(null);
const lastImageFileName = ref(null);

onMounted(async () => {
  loadConfig();
  restoreAutoSavedSource();
  // 页面加载时自动刷新所有摄像头列表
  await refreshAllCameras();
});
</script>
