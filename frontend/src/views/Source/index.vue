<template>
  <div class="p-6 h-full overflow-y-auto">
    <h2 class="text-2xl font-bold mb-6 border-l-4 border-tech-blue pl-3 text-white">输入源设置</h2>

    <!-- ===== Workstation Mode Selector ===== -->
    <el-card shadow="never" class="bg-slate-800 border-slate-700 mb-6">
      <template #header>
        <div class="flex items-center gap-2">
          <el-icon class="text-tech-blue"><Monitor /></el-icon>
          <span class="font-bold text-white">工位模式</span>
        </div>
      </template>
      <div class="flex items-center gap-4">
        <el-radio-group v-model="workstationMode" @change="handleWorkstationModeChange" size="large">
          <el-radio-button :value="1">单工位</el-radio-button>
          <el-radio-button :value="2">双工位</el-radio-button>
          <el-radio-button :value="4">四工位</el-radio-button>
        </el-radio-group>
        <span class="text-xs text-gray-400">{{ workstationMode > 1 ? `同时运行 ${workstationMode} 个独立检测通道` : '单摄像头标准模式' }}</span>
      </div>
    </el-card>

    <!-- ===== Multi-Workstation Configuration (2 or 4) ===== -->
    <div v-if="workstationMode > 1" class="mb-6">
      <div :class="workstationMode <= 2 ? 'grid grid-cols-2 gap-4' : 'grid grid-cols-2 gap-4'">
        <el-card v-for="ch in workstationMode" :key="ch" shadow="never" class="bg-slate-800 border-slate-700">
          <template #header>
            <div class="flex items-center justify-between">
              <span class="font-bold text-white">工位 {{ ch }}</span>
              <el-tag :type="wsConfigured(ch - 1) ? 'success' : 'info'" size="small">
                {{ wsConfigured(ch - 1) ? '已配置' : '未配置' }}
              </el-tag>
            </div>
          </template>
          <el-form label-position="top" size="small">
            <!-- Source type selector per workstation -->
            <el-form-item label="输入源">
              <el-radio-group v-model="wsConfigs[ch - 1].sourceType" size="small">
                <el-radio-button value="camera">摄像头</el-radio-button>
                <el-radio-button value="rtsp">RTSP</el-radio-button>
                <el-radio-button value="video">视频</el-radio-button>
                <el-radio-button value="image">图片</el-radio-button>
              </el-radio-group>
            </el-form-item>

            <!-- Camera selector -->
            <el-form-item v-if="wsConfigs[ch - 1].sourceType === 'camera'" label="摄像头">
              <el-select v-model="wsConfigs[ch - 1].cameraId" class="w-full" placeholder="选择设备" clearable>
                <el-option-group v-if="usbCameras.length > 0" label="USB">
                  <el-option v-for="cam in usbCameras" :key="cam.id" :label="cam.name" :value="cam.id" />
                </el-option-group>
                <el-option-group v-if="hikvisionCameras.length > 0" label="海康">
                  <el-option v-for="cam in hikvisionCameras" :key="cam.id" :label="cam.name" :value="cam.id" />
                </el-option-group>
              </el-select>
            </el-form-item>

            <!-- RTSP URL input -->
            <el-form-item v-if="wsConfigs[ch - 1].sourceType === 'rtsp'" label="RTSP 地址">
              <el-input v-model="wsConfigs[ch - 1].rtspUrl" placeholder="rtsp://admin:密码@IP:554/Streaming/Channels/通道号02 (子码流)" size="small" />
            </el-form-item>
            <el-form-item v-if="wsConfigs[ch - 1].sourceType === 'rtsp'" label="FPS">
              <el-select v-model="wsConfigs[ch - 1].rtspFps" class="w-full" size="small">
                <el-option label="10" :value="10" />
                <el-option label="15" :value="15" />
                <el-option label="25" :value="25" />
                <el-option label="30" :value="30" />
              </el-select>
            </el-form-item>

            <!-- Video file selector -->
            <el-form-item v-if="wsConfigs[ch - 1].sourceType === 'video'" label="视频文件">
              <el-upload drag action="#" :auto-upload="false" :limit="1" accept=".mp4,.avi,.mov,.mkv"
                :on-change="(f) => wsConfigs[ch - 1].videoFile = f.raw" class="w-full">
                <div class="text-xs text-gray-400 py-2">拖拽或点击选择视频</div>
              </el-upload>
              <p v-if="wsConfigs[ch - 1].videoFile" class="text-xs text-green-400 mt-1">{{ wsConfigs[ch - 1].videoFile.name }}</p>
            </el-form-item>

            <!-- Image file selector -->
            <el-form-item v-if="wsConfigs[ch - 1].sourceType === 'image'" label="图片文件">
              <el-upload drag action="#" :auto-upload="false" :limit="1" accept=".jpg,.jpeg,.png,.bmp"
                :on-change="(f) => wsConfigs[ch - 1].imageFile = f.raw" class="w-full">
                <div class="text-xs text-gray-400 py-2">拖拽或点击选择图片</div>
              </el-upload>
              <p v-if="wsConfigs[ch - 1].imageFile" class="text-xs text-green-400 mt-1">{{ wsConfigs[ch - 1].imageFile.name }}</p>
            </el-form-item>

            <el-form-item label="绑定项目">
              <el-select v-model="wsConfigs[ch - 1].projectId" class="w-full" placeholder="选择项目" clearable>
                <el-option v-for="p in projectList" :key="p.id" :label="p.name" :value="p.id" />
              </el-select>
            </el-form-item>
            <div class="grid grid-cols-3 gap-2">
              <el-form-item v-if="wsConfigs[ch - 1].sourceType === 'camera'" label="分辨率">
                <el-select v-model="wsConfigs[ch - 1].resolution" class="w-full">
                  <el-option label="640x480" value="640x480" />
                  <el-option label="1280x720" value="1280x720" />
                  <el-option label="1920x1080" value="1920x1080" />
                </el-select>
              </el-form-item>
              <el-form-item v-if="wsConfigs[ch - 1].sourceType === 'camera'" label="FPS">
                <el-select v-model="wsConfigs[ch - 1].fps" class="w-full">
                  <el-option label="10" :value="10" />
                  <el-option label="15" :value="15" />
                  <el-option label="30" :value="30" />
                </el-select>
              </el-form-item>
              <el-form-item label="GPU">
                <el-select v-model="wsConfigs[ch - 1].gpuDevice" class="w-full">
                  <el-option label="自动" value="auto" />
                  <el-option label="GPU 0" value="cuda:0" />
                  <el-option label="GPU 1" value="cuda:1" />
                  <el-option label="CPU" value="cpu" />
                </el-select>
              </el-form-item>
            </div>
          </el-form>
        </el-card>
      </div>
      <div class="mt-4 flex gap-4">
        <el-button type="primary" size="large" class="flex-1" @click="saveAndStartMulti" :loading="saving">
          <el-icon class="mr-2"><Check /></el-icon>
          保存并启动所有工位
        </el-button>
      </div>
    </div>

    <!-- ===== Single Workstation Configuration (original) ===== -->
    <div v-if="workstationMode <= 1" class="grid grid-cols-1 lg:grid-cols-2 gap-6">
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
            <el-radio value="rtsp" class="!mr-0">
              <div class="flex items-center gap-2">
                <el-icon><Monitor /></el-icon>
                <span>网络摄像头 (RTSP)</span>
                <el-tag type="warning" size="small">NVR / IP Camera</el-tag>
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

        <!-- RTSP 网络视频流设置 -->
        <el-card v-if="sourceType === 'rtsp'" shadow="never" class="bg-slate-800 border-slate-700">
          <template #header>
            <span class="font-bold text-white">RTSP 网络视频流设置</span>
          </template>
          
          <el-form label-position="top">
            <el-form-item label="RTSP 地址">
              <el-input 
                v-model="rtspSettings.url" 
                placeholder="rtsp://用户名:密码@IP地址:554/Streaming/Channels/101"
                clearable
              >
                <template #prepend>URL</template>
              </el-input>
              <div class="text-xs text-gray-400 mt-1 space-y-1">
                <div>海康 NVR 主码流: rtsp://admin:密码@IP:554/Streaming/Channels/通道号<b>01</b></div>
                <div>海康 NVR 子码流: rtsp://admin:密码@IP:554/Streaming/Channels/通道号<b>02</b> <span class="text-green-400">(推荐，720p H.264，性能更好)</span></div>
              </div>
            </el-form-item>
            
            <el-form-item label="目标帧率 (FPS)">
              <el-select v-model="rtspSettings.fps" class="w-full">
                <el-option label="10 FPS (省性能)" :value="10" />
                <el-option label="15 FPS" :value="15" />
                <el-option label="25 FPS (推荐)" :value="25" />
                <el-option label="30 FPS" :value="30" />
              </el-select>
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
            <el-descriptions-item v-if="sourceType === 'rtsp'" label="RTSP 地址">{{ rtspSettings.url || '未输入' }}</el-descriptions-item>
            <el-descriptions-item v-if="sourceType === 'rtsp'" label="帧率">{{ rtspSettings.fps }} FPS</el-descriptions-item>
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
import { ref, computed, onMounted, reactive } from 'vue';
import { useRouter } from 'vue-router';
import { 
  VideoCamera, Camera, VideoPlay, Picture, 
  UploadFilled, Refresh, Check, Monitor
} from '@element-plus/icons-vue';
import { ElMessage } from 'element-plus';
import api from '@/api/index';
import { useSourceStore } from '@/store/useSourceStore';
import { useSystemStore } from '@/store/useSystemStore';
import { setWorkstationMode, getWorkstations, setProjectConfig as apiSetProjectConfig, startDetection as apiStartDetection, setChannelGpu } from '@/api/detection';
import { getModelDetail } from '@/api/model';
import { getProjects } from '@/api/project';

const router = useRouter();
const sourceStore = useSourceStore();
const systemStore = useSystemStore();

// ===== Workstation Mode =====
const workstationMode = ref(1);
const makeDefaultWsConfig = () => ({ sourceType: 'camera', cameraId: '', projectId: null, resolution: '1280x720', fps: 60, gpuDevice: 'auto', videoFile: null, imageFile: null, rtspUrl: '', rtspFps: 25 });
const wsConfigured = (idx) => {
  const c = wsConfigs[idx];
  if (!c) return false;
  if (c.sourceType === 'camera') return !!c.cameraId;
  if (c.sourceType === 'rtsp') return !!c.rtspUrl;
  if (c.sourceType === 'video') return !!c.videoFile;
  if (c.sourceType === 'image') return !!c.imageFile;
  return false;
};
const wsConfigs = reactive([makeDefaultWsConfig(), makeDefaultWsConfig(), makeDefaultWsConfig(), makeDefaultWsConfig()]);

const projectList = ref([]);
const fetchProjectList = async () => {
  try {
    const res = await getProjects();
    projectList.value = res.data?.items || res.data || [];
  } catch (e) { /* silent */ }
};

const handleWorkstationModeChange = async (count) => {
  try {
    await setWorkstationMode(count);
    ElMessage.success(`已切换到 ${count === 1 ? '单工位' : count === 2 ? '双工位' : '四工位'} 模式`);
  } catch (e) {
    ElMessage.error('切换模式失败: ' + (e.message || ''));
    workstationMode.value = 1;
  }
};

const saveAndStartMulti = async () => {
  saving.value = true;
  try {
    await setWorkstationMode(workstationMode.value);

    for (let ch = 0; ch < workstationMode.value; ch++) {
      const cfg = wsConfigs[ch];
      if (!wsConfigured(ch)) continue;

      if (cfg.sourceType === 'camera') {
        const cam = allCameras.value.find(c => c.id === cfg.cameraId);
        if (!cam) continue;
        const [w, h] = cfg.resolution.split('x').map(Number);
        if (cam.type === 'usb') {
          await api.post(`/source/camera/start?channel=${ch}`, { device_index: cam.index, width: w, height: h, fps: cfg.fps });
        } else if (cam.type === 'hikvision') {
          await api.post(`/source/hikvision/start?channel=${ch}`, { device_index: cam.index, width: w, height: h, fps: cfg.fps });
        }
      } else if (cfg.sourceType === 'rtsp' && cfg.rtspUrl) {
        await api.post(`/source/rtsp/start?channel=${ch}`, { url: cfg.rtspUrl, fps: cfg.rtspFps || 25 });
      } else if (cfg.sourceType === 'video' && cfg.videoFile) {
        const formData = new FormData();
        formData.append('file', cfg.videoFile);
        const uploadRes = await api.post('/source/video/upload', formData, { headers: { 'Content-Type': 'multipart/form-data' } });
        await api.post(`/source/video/start?channel=${ch}`, { file_path: uploadRes.data.file_path, speed: 1 });
      } else if (cfg.sourceType === 'image' && cfg.imageFile) {
        const formData = new FormData();
        formData.append('file', cfg.imageFile);
        const uploadRes = await api.post('/source/image/upload', formData, { headers: { 'Content-Type': 'multipart/form-data' } });
        await api.post(`/source/image/set?channel=${ch}`, { file_path: uploadRes.data.file_path });
      }

      if (cfg.gpuDevice && cfg.gpuDevice !== 'auto') {
        try {
          await setChannelGpu(ch, cfg.gpuDevice);
        } catch (ge) {
          console.warn(`Ch${ch} GPU assignment failed:`, ge);
        }
      }

      if (cfg.projectId) {
        const proj = projectList.value.find(p => p.id === cfg.projectId);
        if (proj) {
          await apiSetProjectConfig({
            project_id: proj.id,
            name: proj.name,
            task_type: proj.task_type || 'detection',
            logic_mode: proj.logic_mode || 'detection',
            steps_config: proj.steps_config || [],
            pipeline_config: proj.pipeline_config || {},
            events_config: proj.events_config || [],
            counters_config: proj.counters_config || []
          }, ch);

          if (proj.default_model_id) {
            try {
              const modelRes = await getModelDetail(proj.default_model_id);
              await apiStartDetection(modelRes.data.file_path, 0.25, 0.45, ch);
            } catch (me) {
              console.warn(`Ch${ch} model start failed:`, me);
            }
          }
        }
      }
    }
    ElMessage.success('所有工位已启动，正在跳转...');
    setTimeout(() => router.push('/monitor'), 500);
  } catch (e) {
    const detail = e.response?.data?.detail || e.message || '未知错误';
    ElMessage.error('启动失败: ' + detail);
    console.error('[saveAndStartMulti] 错误详情:', detail, e);
  } finally {
    saving.value = false;
  }
};

const loadWorkstationMode = async () => {
  try {
    const res = await getWorkstations();
    workstationMode.value = res.data.channel_count || 1;
    if (workstationMode.value > 1 && res.data.channels) {
      res.data.channels.forEach(ch => {
        if (wsConfigs[ch.channel_id]) {
          wsConfigs[ch.channel_id].gpuDevice = ch.gpu_device || 'auto';
        }
      });
    }
  } catch (e) { /* keep default */ }
};

// 输入源类型
const sourceType = ref('camera');

// 摄像头设置
const cameraSettings = ref({
  deviceIndex: 0,
  resolution: '1280x720',
  fps: 60
});

// RTSP 设置
const rtspSettings = ref({
  url: '',
  fps: 25
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
    rtsp: '网络摄像头 (RTSP)',
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
      
    } else if (sourceType.value === 'rtsp') {
      if (!rtspSettings.value.url) {
        ElMessage.warning('请输入 RTSP 地址');
        saving.value = false;
        return;
      }
      
      await api.post('/source/rtsp/start', {
        url: rtspSettings.value.url,
        fps: rtspSettings.value.fps
      });
      
      sourceStore.setSourceType('rtsp');
      sourceStore.setRtspSettings(rtspSettings.value);
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
    } else if (sourceType.value === 'rtsp') {
      sourceValue = rtspSettings.value.url;
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
  
  // 恢复输入源类型（hikvision 映射到 camera）
  const savedType = sourceStore.sourceType;
  sourceType.value = (savedType === 'hikvision') ? 'camera' : savedType;
  
  // 恢复 RTSP 设置
  if (sourceStore.rtspSettings) {
    rtspSettings.value = { ...rtspSettings.value, ...sourceStore.rtspSettings };
  }
  
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
  loadWorkstationMode();
  fetchProjectList();
  await refreshAllCameras();
});
</script>
