<template>
  <div class="grid grid-cols-12 gap-3 h-[calc(100vh-8rem)] p-2 relative">
    <!-- LEFT COLUMN: VIDEO & STEPS -->
    <div class="col-span-7 flex flex-col gap-3">
      
      <!-- Video Region -->
      <div class="flex-1 bg-black border-2 border-slate-700 rounded-lg relative overflow-hidden group">
        <!-- 视频流 -->
        <img 
          ref="videoElement"
          :src="streamUrl"
          class="w-full h-full object-contain"
          @load="handleVideoLoad"
          @error="handleStreamError"
        />
        
        <!-- 检测框覆盖层 -->
        <canvas 
          ref="detectionCanvas"
          class="absolute top-0 left-0 w-full h-full pointer-events-none"
        ></canvas>
        
        <!-- 运行状态指示 -->
        <div v-if="isRunning" class="absolute top-4 right-4 bg-green-600/90 text-white px-6 py-2 rounded shadow-lg text-lg font-bold animate-pulse">
          检测中
        </div>
        <div v-else class="absolute top-4 right-4 bg-gray-600/90 text-white px-6 py-2 rounded shadow-lg text-lg font-bold">
          待机中
        </div>
        
        <!-- Current Project Info -->
        <div v-if="currentProject" class="absolute top-4 left-4 bg-slate-900/80 text-white px-4 py-2 rounded shadow-lg">
          <div class="text-xs text-gray-400">当前项目</div>
          <div class="font-bold">{{ currentProject.name }}</div>
          <div class="text-xs text-cyan-400">{{ logicModeText }}</div>
        </div>
        
        <!-- Video Footer Stats -->
        <div class="absolute bottom-0 left-0 right-0 bg-black/60 backdrop-blur-sm border-t border-white/10">
          <!-- 视频进度条（仅视频输入源时显示，鼠标悬停时出现） -->
          <div v-if="isVideoSource" class="px-3 pt-2 pb-1 opacity-0 group-hover:opacity-100 transition-opacity duration-300">
            <div class="flex items-center gap-3">
              <span class="text-xs text-gray-400 font-mono w-16">{{ formatVideoTime(videoInfo.currentTime) }}</span>
              <el-slider
                v-model="videoInfo.progress"
                :min="0"
                :max="1"
                :step="0.001"
                :show-tooltip="false"
                class="flex-1 video-progress-slider"
                @mousedown="isDraggingProgress = true"
                @mouseup="handleProgressChange"
                @change="handleProgressChange"
              />
              <span class="text-xs text-gray-400 font-mono w-16 text-right">{{ formatVideoTime(videoInfo.duration) }}</span>
              <el-select 
                v-model="videoInfo.speed" 
                size="small" 
                class="w-20 video-speed-select"
                :disabled="videoInfo.syncMode"
                @change="handleSpeedChange"
              >
                <el-option label="0.5x" :value="0.5" />
                <el-option label="1x" :value="1" />
                <el-option label="2x" :value="2" />
                <el-option label="4x" :value="4" />
                <el-option label="8x" :value="8" />
              </el-select>
              <el-tooltip content="同步模式：逐帧检测，确保每一帧都被处理（适合分析快速动作）" placement="top">
                <el-switch
                  v-model="videoInfo.syncMode"
                  size="small"
                  active-text="逐帧"
                  inactive-text=""
                  class="video-sync-switch"
                  @change="handleSyncModeChange"
                />
              </el-tooltip>
            </div>
            <div v-if="videoInfo.ended" class="text-center text-yellow-400 text-xs mt-1">
              视频播放完毕
            </div>
            <div v-if="videoInfo.syncMode && !videoInfo.ended" class="text-center text-cyan-400 text-xs mt-1">
              逐帧检测模式：按检测速度播放，确保每帧都被检测
            </div>
          </div>
          <!-- 状态信息 -->
          <div class="p-2 flex gap-6 text-xs text-gray-300">
            <span class="flex items-center gap-2">
              <span class="w-2 h-2 rounded-full" :class="isStreaming ? 'bg-green-500' : 'bg-gray-500'"></span> 
              {{ sourceStatusText }}
            </span>
            <span>FPS: <span class="text-cyan-400 font-mono">{{ fps }}</span></span>
            <span>延迟: <span class="text-cyan-400 font-mono">{{ latency }} ms</span></span>
            <span>检测数: <span class="text-cyan-400 font-mono">{{ detectionCount }}</span></span>
          </div>
        </div>
      </div>

      <!-- 工艺卡片 (Step Indicators) -->
      <div v-if="systemStore.display.monitor.stepStrip && steps.length > 0" class="h-44 bg-slate-900 border border-slate-700 rounded-lg overflow-hidden flex flex-col">
        <div class="bg-slate-800 px-3 py-1 border-b border-slate-700 flex-shrink-0">
          <span class="text-cyan-400 text-xs font-bold">工艺卡片</span>
        </div>
        <div class="flex-1 p-2 overflow-x-auto">
          <div class="flex items-center h-full">
            <template v-for="(step, idx) in steps" :key="idx">
              <!-- 间隔时间显示（第一个步骤前不显示） -->
              <div v-if="idx > 0" class="flex flex-col items-center justify-center px-1 flex-shrink-0">
                <div class="w-6 h-[2px] bg-slate-600"></div>
                <div class="text-[9px] text-yellow-400 font-mono mt-0.5 whitespace-nowrap">
                  {{ formatInterval(step.label) }}
                </div>
                <div class="w-6 h-[2px] bg-slate-600"></div>
              </div>
              
              <!-- 步骤卡片 -->
              <div
                class="w-32 flex-shrink-0 flex flex-col bg-slate-800 rounded border transition-all duration-300"
                :class="getStepClass(step)"
              >
                <div class="h-6 bg-slate-950 px-2 flex items-center justify-between text-[10px] text-gray-400">
                  <span>Step {{ idx + 1 }}</span>
                  <span v-if="tableData[idx]?.count" class="text-cyan-400">x{{ tableData[idx].count }}</span>
                </div>
                <div class="h-16 p-1 flex items-center justify-center bg-black/20 relative overflow-hidden">
                   <!-- 截图显示 -->
                   <img 
                     v-if="step.screenshot" 
                     :src="step.screenshot" 
                     class="w-full h-full object-cover rounded"
                   />
                   <el-icon v-else :size="24" class="text-slate-600"><Picture /></el-icon>
                   
                   <!-- 状态覆盖层 -->
                   <div v-if="step.status === 'active'" class="absolute inset-0 border-2 border-cyan-500 animate-pulse"></div>
                   <div v-if="step.status === 'completed'" class="absolute bottom-1 right-1">
                     <el-icon :size="16" class="text-green-500 bg-green-900/80 rounded-full p-0.5"><Check /></el-icon>
                   </div>
                </div>
                <div class="h-10 px-1 flex flex-col items-center justify-center bg-slate-800">
                  <div class="text-xs font-bold text-center truncate w-full">{{ step.name }}</div>
                  <div class="text-[10px] text-cyan-400 font-mono">耗时: {{ formatDuration(step.label) }}</div>
                </div>
              </div>
            </template>
          </div>
        </div>
      </div>
      
      <!-- No Project Selected -->
      <div v-else-if="!currentProject" class="h-40 bg-slate-900 border border-slate-700 rounded-lg flex items-center justify-center text-gray-500">
        <div class="text-center">
          <el-icon :size="32" class="mb-2"><Folder /></el-icon>
          <p class="text-sm">请先在顶部选择项目</p>
        </div>
      </div>
    </div>

    <!-- RIGHT COLUMN: DASHBOARD Stats -->
    <div class="col-span-5 flex flex-col gap-3">
      
      <!-- Top Row: Stats Counters (Dynamic) -->
      <div v-if="systemStore.display.monitor.statsPanel" class="h-56 bg-slate-900 border border-slate-700 rounded-lg p-4 flex flex-col">
        <template v-if="currentProject && counters.length > 0">
          <!-- Main Stats: 总产量 和 合格总数（上面两个大的） -->
          <div class="grid grid-cols-2 gap-4 mb-3">
            <div v-for="(counter, idx) in mainCounters" :key="counter.name"
              class="flex flex-col items-center justify-center bg-slate-800/50 p-3 rounded-lg"
            >
               <div class="text-sm text-gray-400 mb-1">{{ counter.name }}</div>
               <div class="text-3xl font-mono font-bold" 
                    :class="getCounterColor(counter.name)">
                 {{ counter.value }}
               </div>
            </div>
          </div>
          
          <!-- Secondary Stats: 不良总数、NG步骤 和自定义计数器 -->
          <div class="flex-1 grid grid-cols-2 gap-3 mt-1 border-t border-slate-800 pt-3 overflow-y-auto">
             <div v-for="(counter, idx) in secondaryCounters" :key="counter.name"
               class="flex flex-col items-center justify-center bg-slate-800/30 p-2 rounded"
             >
                <span class="text-xs text-gray-500 truncate w-full text-center">{{ counter.name }}</span>
                <span class="font-bold text-lg" :class="getCounterColor(counter.name)">{{ counter.value }}</span>
             </div>
          </div>
        </template>
        <div v-else class="flex flex-col items-center justify-center h-full text-gray-500">
          <el-icon :size="32" class="mb-2"><Folder /></el-icon>
          <span class="text-xs">请先选择项目以查看统计数据</span>
        </div>
      </div>

      <!-- Middle: Charts -->
      <div class="h-52 grid grid-cols-2 gap-3">
         <!-- Pie Chart -->
         <div v-if="systemStore.display.monitor.defectChart" class="bg-slate-900 border border-slate-700 rounded-lg p-3 relative">
            <h3 class="text-cyan-400 text-sm font-bold absolute top-2 left-3">良品/不良统计</h3>
            <div ref="defectChartRef" class="w-full h-full"></div>
         </div>
         <!-- Yield Rate Gauge -->
         <div v-if="systemStore.display.monitor.capacityChart" class="bg-slate-900 border border-slate-700 rounded-lg p-3 relative">
            <h3 class="text-cyan-400 text-sm font-bold absolute top-2 left-3">良率</h3>
            <div ref="capacityGaugeRef" class="w-full h-full"></div>
         </div>
      </div>

      <!-- Bottom: Detail Table & Controls -->
      <div v-if="systemStore.display.monitor.stepTable" class="flex-1 bg-slate-900 border border-slate-700 rounded-lg overflow-hidden flex flex-col">
         <div class="bg-slate-800 px-3 py-2 flex justify-between items-center border-b border-slate-700">
            <span class="text-cyan-400 text-sm font-bold">步骤统计</span>
            <span class="text-[10px] bg-slate-700 px-2 py-0.5 rounded text-gray-300">CT: {{ cycleTime }}s</span>
         </div>
         <div class="flex-1 overflow-auto">
            <table class="w-full text-left text-[11px]">
               <thead class="bg-slate-800 text-gray-400 top-0 sticky">
                  <tr>
                     <th class="p-2">No</th>
                     <th class="p-2">步骤</th>
                     <th class="p-2">检测次数</th>
                     <th class="p-2">状态</th>
                  </tr>
               </thead>
               <tbody class="divide-y divide-slate-800 text-gray-300">
                  <tr v-for="(row, i) in tableData" :key="i" class="hover:bg-slate-800/50">
                     <td class="p-2">{{ i + 1 }}</td>
                     <td class="p-2">{{ row.step }}</td>
                     <td class="p-2 text-cyan-400">{{ row.count }}</td>
                     <td class="p-2">
                       <span :class="row.status === 'completed' ? 'text-green-500' : 'text-gray-500'">
                         {{ row.status === 'completed' ? '已完成' : '待检测' }}
                       </span>
                     </td>
                  </tr>
               </tbody>
            </table>
         </div>
         
         <!-- Control Buttons -->
         <div class="p-2 bg-slate-950 border-t border-slate-800 flex gap-2">
            <button 
              @click="startDetection" 
              :disabled="!currentProject || isRunning"
              class="flex-1 bg-green-600 hover:bg-green-500 disabled:bg-gray-600 disabled:cursor-not-allowed text-white py-2 rounded text-sm font-bold shadow transition-colors"
            >
              开始
            </button>
            <button 
              @click="stopDetectionHandler"
              :disabled="!isRunning"
              class="flex-1 bg-red-600 hover:bg-red-500 disabled:bg-gray-600 disabled:cursor-not-allowed text-white py-2 rounded text-sm font-bold shadow transition-colors"
            >
              停止
            </button>
            <button 
              @click="standby"
              class="flex-1 bg-yellow-600 hover:bg-yellow-500 text-white py-2 rounded text-sm font-bold shadow transition-colors"
            >
              待机
            </button>
            <button 
              @click="resetCounters"
              class="flex-1 bg-cyan-700 hover:bg-cyan-600 text-white py-2 rounded text-sm font-bold shadow transition-colors"
            >
              清零
            </button>
         </div>
      </div>

    </div>
    
    <!-- 事件提示框容器 - 不同位置 -->
    <template v-for="position in ['top-right', 'top-left', 'bottom-right', 'bottom-left', 'center']" :key="position">
      <div 
        class="fixed z-50 pointer-events-none flex flex-col gap-2"
        :class="getPositionClass(position)"
      >
        <transition-group name="toast">
          <div 
            v-for="toast in activeToasts.filter(t => t.position === position)" 
            :key="toast.id"
            class="px-6 py-4 rounded-xl shadow-2xl text-white font-bold pointer-events-auto transform transition-all duration-300 text-center"
            :style="{ 
              backgroundColor: toast.color,
              fontSize: toast.fontSize + 'px'
            }"
          >
            <div class="flex items-center gap-3 justify-center">
              <el-icon :size="24">
                <component :is="toast.icon" />
              </el-icon>
              <div>
                <div class="font-bold">{{ toast.title }}</div>
                <div v-if="toast.subtitle" class="text-sm opacity-80">{{ toast.subtitle }}</div>
              </div>
            </div>
          </div>
        </transition-group>
      </div>
    </template>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, ref, watch, nextTick, computed } from 'vue';
import * as echarts from 'echarts';
import { useProjectStore } from '@/store/useProjectStore';
import { useSystemStore } from '@/store/useSystemStore';
import { useSourceStore } from '@/store/useSourceStore';
import { Check, Folder, Picture, CircleCheck, CircleClose, Warning } from '@element-plus/icons-vue';
import { ElMessage } from 'element-plus';
import { startDetection as apiStartDetection, stopDetection as apiStopDetection, pauseDetection, resumeDetection, standbyDetection, resetDetection, resetDetectionStats, getDetectionResults, getSourceStatus, setProjectConfig } from '@/api/detection';
import { getModelDetail } from '@/api/model';
import api, { getBackendHost } from '@/api/index';

const projectStore = useProjectStore();
const systemStore = useSystemStore();
const sourceStore = useSourceStore();

const videoElement = ref(null);
const detectionCanvas = ref(null);

// Chart Refs
const defectChartRef = ref(null);
const capacityGaugeRef = ref(null);
let pieChartInstance = null;
let gaugeChartInstance = null;

// State
const steps = ref([]);
const tableData = ref([]);
const isRunning = ref(false);
const isPaused = ref(false);  // 是否处于暂停状态
const isStreaming = ref(false);
const fps = ref(0);
const latency = ref(0);
const cycleTime = ref(0);
const detectionCount = ref(0);
const currentDetections = ref([]);

// 视频播放信息（仅视频输入源时有效）
const videoInfo = ref({
  progress: 0,
  currentTime: 0,
  duration: 0,
  speed: 1,
  syncMode: false,  // 同步模式：逐帧检测
  ended: false
});
const isVideoSource = computed(() => sourceStore.sourceType === 'video');
const isHikvisionSource = computed(() => sourceStore.sourceType === 'hikvision');
const sourceStatusText = computed(() => {
  if (isVideoSource.value) {
    return videoInfo.value.ended ? '视频已结束' : '视频播放中';
  } else if (isHikvisionSource.value) {
    return isStreaming.value ? '海康相机在线' : '海康相机离线';
  } else {
    return isStreaming.value ? '摄像头在线' : '摄像头离线';
  }
});
const isDraggingProgress = ref(false);  // 是否正在拖动进度条
const isChangingSpeed = ref(false);  // 是否正在改变倍速（防止轮询覆盖）

// 视频流 URL（使用响应式变量强制刷新）
const streamTimestamp = ref(Date.now());
const streamUrl = computed(() => `${getBackendHost()}/video_feed?t=${streamTimestamp.value}`);

// 刷新视频流
const refreshStream = () => {
  streamTimestamp.value = Date.now();
};

// 当前项目
const currentProject = computed(() => projectStore.currentProject);

// 默认计数器定义（系统内置，不可删除）
const DEFAULT_COUNTERS = ['总产量', '合格总数', '不良总数', 'NG步骤'];

// 计数器数据 - 按固定顺序排列：总产量、合格总数在上，不良总数、NG步骤在下，然后是自定义计数器
const counters = computed(() => {
  if (!currentProject.value?.counters_config) return [];
  
  const allCounters = currentProject.value.counters_config;
  const displaySettings = systemStore.display.monitor.defaultCounters || {};
  
  // 分离默认计数器和自定义计数器
  const defaultCounters = [];
  const customCounters = [];
  
  // 按固定顺序添加默认计数器（如果启用显示）
  DEFAULT_COUNTERS.forEach(name => {
    const counter = allCounters.find(c => c.name === name);
    if (counter) {
      // 检查是否启用显示（默认显示）
      const showKey = name === '总产量' ? 'showTotal' : 
                      name === '合格总数' ? 'showGood' : 
                      name === '不良总数' ? 'showBad' : 
                      name === 'NG步骤' ? 'showNgSteps' : 'show';
      if (displaySettings[showKey] !== false) {
        defaultCounters.push(counter);
      }
    }
  });
  
  // 添加自定义计数器（排除默认计数器）
  allCounters.forEach(counter => {
    if (!DEFAULT_COUNTERS.includes(counter.name)) {
      customCounters.push(counter);
    }
  });
  
  return [...defaultCounters, ...customCounters];
});

// 主计数器（上面两个大的）：总产量、合格总数
const mainCounters = computed(() => {
  return counters.value.filter(c => c.name === '总产量' || c.name === '合格总数');
});

// 次级计数器（下面的）：不良总数、NG步骤、自定义计数器
const secondaryCounters = computed(() => {
  return counters.value.filter(c => c.name !== '总产量' && c.name !== '合格总数');
});

// 根据计数器名称返回颜色类
const getCounterColor = (name) => {
  switch (name) {
    case '总产量': return 'text-cyan-400';      // 青色 - 中性
    case '合格总数': return 'text-green-500';   // 绿色 - 好
    case '不良总数': return 'text-red-500';     // 红色 - 坏
    case 'NG步骤': return 'text-orange-500';    // 橙色 - 警告
    default: return 'text-white';               // 白色 - 自定义
  }
};

// 逻辑模式文本
const logicModeText = computed(() => {
  const mode = currentProject.value?.logic_mode;
  switch(mode) {
    case 'sequential': return '顺序模式';
    case 'detection': return '检测模式';
    case 'custom': return '自定义模式';
    default: return '未设置';
  }
});

// 事件提示框
const activeToasts = ref([]);
let toastIdCounter = 0;

// 获取提示框配置
const getToastConfig = (toastId) => {
  // 系统预设提示框
  if (toastId === 'ok') return systemStore.detection.toasts.ok;
  if (toastId === 'ng') return systemStore.detection.toasts.ng;
  
  // 自定义提示框
  const customToast = systemStore.detection.customToasts.find(t => t.id === toastId);
  if (customToast) return customToast;
  
  // 默认返回 ok 配置
  return systemStore.detection.toasts.ok;
};

// 提示框位置样式
const getPositionClass = (position) => {
  switch (position) {
    case 'top-right': return 'top-24 right-8';
    case 'top-left': return 'top-24 left-72';
    case 'bottom-right': return 'bottom-8 right-8';
    case 'bottom-left': return 'bottom-8 left-72';
    case 'center': return 'top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2';
    default: return 'top-24 right-8';
  }
};

// 默认位置（兼容旧代码）
const toastPositionClass = computed(() => {
  return getPositionClass(systemStore.detection.toasts.ok.position);
});

// 显示提示框（新版：根据 toast_id 获取配置）
const showToastById = (toastId, eventName, reason = '') => {
  const config = getToastConfig(toastId);
  
  const icons = {
    ok: CircleCheck,
    ng: CircleClose,
    custom: Warning
  };
  
  // NG提示框优先显示reason（NG原因），其他提示框使用配置的subText或reason
  const subtitle = (toastId === 'ng' && reason) ? reason : (config.subText || reason);
  
  const toast = {
    id: ++toastIdCounter,
    toastId,
    title: config.text || eventName,
    subtitle: subtitle,
    color: config.color,
    fontSize: config.fontSize,
    position: config.position,
    icon: toastId === 'ok' ? icons.ok : (toastId === 'ng' ? icons.ng : icons.custom)
  };
  
  activeToasts.value.push(toast);
  
  // 自动移除
  setTimeout(() => {
    const idx = activeToasts.value.findIndex(t => t.id === toast.id);
    if (idx > -1) {
      activeToasts.value.splice(idx, 1);
    }
  }, config.duration * 1000);
};

// 兼容旧版显示提示框
const showToast = (type, title, subtitle = '') => {
  const toastId = type === 'ok' ? 'ok' : (type === 'ng' ? 'ng' : 'ok');
  const config = getToastConfig(toastId);
  
  const icons = {
    ok: CircleCheck,
    ng: CircleClose,
    custom: Warning
  };
  
  const toast = {
    id: ++toastIdCounter,
    toastId,
    title: title || config.text,
    subtitle: subtitle || config.subText,
    color: config.color,
    fontSize: config.fontSize,
    position: config.position,
    icon: icons[type] || icons.custom
  };
  
  activeToasts.value.push(toast);
  
  // 自动移除
  setTimeout(() => {
    const idx = activeToasts.value.findIndex(t => t.id === toast.id);
    if (idx > -1) {
      activeToasts.value.splice(idx, 1);
    }
  }, config.duration * 1000);
};

// 格式化步骤检测时间
const formatStepTime = (stepLabel) => {
  const timestamp = stepDetectionTimes.value[stepLabel];
  if (!timestamp) return '--:--:--';
  
  const date = new Date(timestamp * 1000);
  const hours = date.getHours().toString().padStart(2, '0');
  const minutes = date.getMinutes().toString().padStart(2, '0');
  const seconds = date.getSeconds().toString().padStart(2, '0');
  return `${hours}:${minutes}:${seconds}`;
};

// 格式化步骤耗时
const formatDuration = (stepLabel) => {
  const duration = stepDurations.value[stepLabel];
  if (duration === undefined || duration === null) return '--';
  return `${duration.toFixed(1)}s`;
};

// 格式化步骤间隔时间
const formatInterval = (stepLabel) => {
  const interval = stepIntervals.value[stepLabel];
  if (interval === undefined || interval === null || interval === 0) return '--';
  return `${interval.toFixed(1)}s`;
};

// 获取步骤样式
const getStepClass = (step) => {
  if (step.status === 'active') {
    return 'border-cyan-500 shadow-[0_0_10px_rgba(6,182,212,0.3)]';
  } else if (step.status === 'completed') {
    return 'border-green-500 bg-green-900/20';
  } else {
    return 'border-slate-700 opacity-60';
  }
};

// 格式化视频时间（秒转 mm:ss）
const formatVideoTime = (seconds) => {
  if (!seconds || isNaN(seconds)) return '00:00';
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
};

// 处理视频进度条变化
const handleProgressChange = async () => {
  try {
    await api.post('/source/video/progress', { progress: videoInfo.value.progress });
    
    // 立即获取最新的视频信息（更新时间显示）
    const videoRes = await api.get('/source/video/info');
    if (videoRes.data.status === 'success') {
      videoInfo.value.currentTime = videoRes.data.current_time || 0;
      videoInfo.value.duration = videoRes.data.duration || 0;
    }
    
    // 刷新视频流，重新连接
    setTimeout(() => {
      refreshStream();
      // 确保 sourceType 保持为 video
      sourceStore.setSourceType('video');
    }, 100);
  } catch (err) {
    console.error('设置视频进度失败:', err);
    ElMessage.error('设置进度失败');
  } finally {
    // 确保拖动状态被重置
    isDraggingProgress.value = false;
  }
};

// 处理视频倍速变化
const handleSpeedChange = async (speed) => {
  try {
    isChangingSpeed.value = true;  // 防止轮询覆盖
    await api.post('/source/video/speed', { speed });
    ElMessage.success(`播放倍速已设为 ${speed}x`);
    // 延迟解除保护，确保后端已更新
    setTimeout(() => {
      isChangingSpeed.value = false;
    }, 500);
  } catch (err) {
    console.error('设置视频倍速失败:', err);
    ElMessage.error('设置倍速失败');
    isChangingSpeed.value = false;
  }
};

// 处理同步模式变化
const handleSyncModeChange = async (enabled) => {
  try {
    await api.post('/source/video/sync-mode', { enabled });
    const modeName = enabled ? '逐帧检测模式' : '正常播放模式';
    ElMessage.success(`已切换到${modeName}`);
    sourceStore.setVideoSyncMode(enabled);
  } catch (err) {
    console.error('设置同步模式失败:', err);
    ElMessage.error('设置同步模式失败');
    // 恢复原状态
    videoInfo.value.syncMode = !enabled;
  }
};

// 处理视频加载
let streamErrorCount = 0;
const handleVideoLoad = () => {
  isStreaming.value = true;
  streamErrorCount = 0;  // 重置错误计数
  resizeCanvas();
};

// 处理视频流错误（自动重连）
const handleStreamError = () => {
  streamErrorCount++;
  console.warn(`视频流错误 (第${streamErrorCount}次)，尝试重连...`);
  
  // 防止无限重连
  if (streamErrorCount > 10) {
    console.error('视频流重连失败次数过多，停止重连');
    return;
  }
  
  // 延迟重连
  setTimeout(() => {
    refreshStream();
  }, 500 * Math.min(streamErrorCount, 5));
};

// 调整 canvas 大小
const resizeCanvas = () => {
  if (!videoElement.value || !detectionCanvas.value) return;
  
  const video = videoElement.value;
  const canvas = detectionCanvas.value;
  
  canvas.width = video.offsetWidth;
  canvas.height = video.offsetHeight;
};

// 绘制检测框
const drawDetections = (detections) => {
  if (!detectionCanvas.value) return;
  
  const canvas = detectionCanvas.value;
  const ctx = canvas.getContext('2d');
  
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  
  if (!detections || detections.length === 0) return;
  
  const boxColor = systemStore.detection.boxColor;
  const ngColor = systemStore.detection.boxColorNG;
  const lineWidth = systemStore.detection.boxLineWidth;
  const fontSize = systemStore.detection.labelFontSize;
  const showConf = systemStore.detection.showConfidence;
  
  detections.forEach(det => {
    // 归一化坐标转换为实际坐标
    const x = det.x * canvas.width;
    const y = det.y * canvas.height;
    const w = det.w * canvas.width;
    const h = det.h * canvas.height;
    
    // 判断颜色
    const color = det.is_ng ? ngColor : boxColor;
    
    // 绘制边界框
    ctx.strokeStyle = color;
    ctx.lineWidth = lineWidth;
    ctx.strokeRect(x, y, w, h);
    
    // 准备标签
    let label = det.label || 'Unknown';
    if (showConf && det.confidence) {
      label += ` ${(det.confidence * 100).toFixed(0)}%`;
    }
    
    // 绘制标签背景
    ctx.font = `bold ${fontSize}px Arial`;
    const textMetrics = ctx.measureText(label);
    const textHeight = fontSize;
    
    ctx.fillStyle = color;
    ctx.fillRect(x, y - textHeight - 4, textMetrics.width + 8, textHeight + 4);
    
    // 绘制标签文字
    ctx.fillStyle = 'white';
    ctx.fillText(label, x + 4, y - 4);
  });
};

// Watch for project changes to sync UI
watch(() => currentProject.value, (newProject) => {
  if (!newProject) {
    steps.value = [];
    tableData.value = [];
    return;
  }
  
  // 获取步骤配置
  const stepsConfig = newProject.steps_config || [];
  const logicMode = newProject.logic_mode || 'sequential';
  const pipelineConfig = newProject.pipeline_config || {};
  
  // 从顶层或 pipeline_config 获取配置（优先顶层，因为可能有更新的值）
  const sequenceOrder = newProject.sequence_order || pipelineConfig.sequence_order || [];
  const detectionSteps = newProject.detection_steps || pipelineConfig.detection_steps || [];
  const customBasedOn = newProject.custom_based_on || pipelineConfig.custom_based_on || null;
  const customSequenceOrder = newProject.custom_sequence_order || pipelineConfig.custom_sequence_order || [];
  const customDetectionSteps = newProject.custom_detection_steps || pipelineConfig.custom_detection_steps || [];
  
  let stepsToShow = [];
  
  if (logicMode === 'sequential') {
    if (sequenceOrder.length > 0) {
      stepsToShow = sequenceOrder.map(seqItem => {
        return stepsConfig.find(s => s.id === seqItem.step_id);
      }).filter(Boolean);
    } else {
      stepsToShow = stepsConfig.filter(s => s.enabled);
    }
  } else if (logicMode === 'detection') {
    if (detectionSteps.length > 0) {
      stepsToShow = detectionSteps.map(id => {
        return stepsConfig.find(s => s.id === id);
      }).filter(Boolean);
    } else {
      stepsToShow = stepsConfig.filter(s => s.enabled);
    }
  } else if (logicMode === 'custom') {
    // 自定义模式：根据 custom_based_on 决定显示哪些步骤
    if (customBasedOn === 'sequential') {
      // 基于顺序模式：使用自定义模式独立的顺序配置
      if (customSequenceOrder.length > 0) {
        stepsToShow = customSequenceOrder.map(seqItem => {
          return stepsConfig.find(s => s.id === seqItem.step_id);
        }).filter(Boolean);
      } else {
        stepsToShow = stepsConfig.filter(s => s.enabled);
      }
    } else if (customBasedOn === 'detection') {
      // 基于检测模式：使用自定义模式独立的检测配置
      if (customDetectionSteps.length > 0) {
        stepsToShow = customDetectionSteps.map(id => {
          return stepsConfig.find(s => s.id === id);
        }).filter(Boolean);
      } else {
        stepsToShow = stepsConfig.filter(s => s.enabled);
      }
    } else {
      // 不基于任何模式：显示自定义条件中涉及的步骤
      const customConditions = newProject.custom_conditions || pipelineConfig.custom_conditions || [];
      const involvedStepIds = new Set();
      customConditions.forEach(cond => {
        (cond.sequence || []).forEach(stepId => involvedStepIds.add(stepId));
      });
      if (involvedStepIds.size > 0) {
        stepsToShow = [...involvedStepIds].map(id => stepsConfig.find(s => s.id === id)).filter(Boolean);
      } else {
        stepsToShow = stepsConfig.filter(s => s.enabled);
      }
    }
  } else {
    stepsToShow = stepsConfig.filter(s => s.enabled);
  }
  

  // 更新步骤条 - 同时保存 label 用于后端匹配
  steps.value = stepsToShow.map((s, idx) => ({
    id: s.id,
    name: s.displayLabel || s.label,
    label: s.label,  // 添加 label 字段用于后端匹配
    status: 'pending',
    result: null,
    screenshot: null
  }));

  // 更新表格数据
  tableData.value = stepsToShow.map(s => ({
    step: s.displayLabel || s.label,
    label: s.label,  // 添加 label 字段
    count: 0,
    status: 'pending'
  }));
  
  nextTick(() => {
    updateCharts();
  });

}, { immediate: true, deep: true });

// Initialize Charts
const initCharts = () => {
  if (pieChartInstance && !pieChartInstance.isDisposed()) {
    pieChartInstance.dispose();
  }
  pieChartInstance = null;
  
  if (gaugeChartInstance && !gaugeChartInstance.isDisposed()) {
    gaugeChartInstance.dispose();
  }
  gaugeChartInstance = null;

  if (defectChartRef.value) {
    pieChartInstance = echarts.init(defectChartRef.value);
    updatePieChart();
  }

  if (capacityGaugeRef.value) {
    gaugeChartInstance = echarts.init(capacityGaugeRef.value);
    updateGaugeChart();
  }
};

// 更新饼图
const updatePieChart = () => {
  if (!pieChartInstance || pieChartInstance.isDisposed()) return;
  
  const goodCount = counters.value.find(c => c.name === '合格总数')?.value || 0;
  const badCount = counters.value.find(c => c.name === '不良总数')?.value || 0;
  
  pieChartInstance.setOption({
    color: ['#10b981', '#ef4444'],
    series: [{
      type: 'pie',
      radius: ['40%', '70%'],
      center: ['50%', '55%'],
      label: { show: false },
      data: [
        { value: goodCount || 1, name: '良品' },
        { value: badCount, name: '不良' }
      ]
    }]
  });
};

// 更新仪表盘
const updateGaugeChart = () => {
  if (!gaugeChartInstance || gaugeChartInstance.isDisposed()) return;
  
  const goodCount = counters.value.find(c => c.name === '合格总数')?.value || 0;
  const totalCount = counters.value.find(c => c.name === '总产量')?.value || 0;
  const yieldRate = totalCount > 0 ? (goodCount / totalCount * 100) : 0;
  
  gaugeChartInstance.setOption({
    series: [{
      type: 'gauge',
      center: ['50%', '60%'],
      radius: '80%',
      startAngle: 180,
      endAngle: 0,
      min: 0,
      max: 100,
      splitNumber: 5,
      itemStyle: { color: yieldRate >= 90 ? '#10b981' : yieldRate >= 70 ? '#f59e0b' : '#ef4444' },
      progress: { show: true, width: 10 },
      pointer: { show: false },
      axisLine: { lineStyle: { width: 10, color: [[1, '#334155']] } },
      axisTick: { show: false },
      splitLine: { show: false },
      axisLabel: { show: false },
      detail: { 
        valueAnimation: true, 
        offsetCenter: [0, '20%'],
        fontSize: 16,
        color: '#fff',
        formatter: '{value}%'
      },
      data: [{ value: yieldRate.toFixed(1) }]
    }]
  });
};

// 更新所有图表
const updateCharts = () => {
  updatePieChart();
  updateGaugeChart();
};

// 检测结果轮询定时器
let pollingTimer = null;

// 控制按钮
const startDetection = async () => {
  if (!currentProject.value) {
    ElMessage.warning('请先选择项目');
    return;
  }
  
  // 如果是从暂停状态恢复
  if (isPaused.value) {
    try {
      await resumeDetection();
      isPaused.value = false;
      isRunning.value = true;
      projectStore.setRunningStatus(true);
      refreshStream();  // 刷新视频流，重新连接
      ElMessage.success('检测已恢复');
      startPolling();
      return;
    } catch (err) {
      console.error('恢复失败，尝试重新启动:', err);
      isPaused.value = false;
      // 继续执行下面的完整启动流程
    }
  }
  
  // 获取模型路径
  const modelId = currentProject.value.default_model_id;
  if (!modelId) {
    ElMessage.warning('请先在项目管理中配置模型');
    return;
  }
  
  try {
    // 获取模型详情
    const modelRes = await getModelDetail(modelId);
    const modelPath = modelRes.data.file_path;
    
    if (!modelPath) {
      ElMessage.error('模型文件路径无效');
      return;
    }
    
    // 构建完整的 pipeline_config（合并顶层配置和 pipeline_config）
    const pipelineConfig = {
      ...(currentProject.value.pipeline_config || {}),
      sequence_order: currentProject.value.sequence_order || currentProject.value.pipeline_config?.sequence_order || [],
      detection_steps: currentProject.value.detection_steps || currentProject.value.pipeline_config?.detection_steps || [],
      custom_conditions: currentProject.value.custom_conditions || currentProject.value.pipeline_config?.custom_conditions || [],
      custom_based_on: currentProject.value.custom_based_on || currentProject.value.pipeline_config?.custom_based_on || 'sequential'
    };
    
    // 发送项目配置到后端
    await setProjectConfig({
      project_id: currentProject.value.id,
      name: currentProject.value.name,
      logic_mode: currentProject.value.logic_mode || 'detection',
      steps_config: currentProject.value.steps_config || [],
      pipeline_config: pipelineConfig,
      events_config: currentProject.value.events_config || [],
      counters_config: currentProject.value.counters_config || []
    });
    
    // 启动检测
    await apiStartDetection(modelPath, 0.25, 0.45);
    isRunning.value = true;
    isPaused.value = false;
    projectStore.setRunningStatus(true);
    refreshStream();  // 刷新视频流
    ElMessage.success('检测已开始');
    
    // 开始轮询检测结果
    startPolling();
    
  } catch (err) {
    console.error('启动检测失败:', err);
    ElMessage.error('启动检测失败: ' + (err.response?.data?.detail || err.message));
  }
};

const stopDetectionHandler = async () => {
  try {
    // 暂停：停止画面和检测，画面停在当前帧
    await pauseDetection();
    isRunning.value = false;
    isPaused.value = true;  // 标记为暂停状态，方便后续恢复
    projectStore.setRunningStatus(false);
    stopPolling();
    ElMessage.info('已停止：画面和检测都已暂停');
  } catch (err) {
    console.error('停止检测失败:', err);
  }
};

// 步骤截图
const stepScreenshots = ref({});

// 步骤检测时间
const stepDetectionTimes = ref({});

// 步骤耗时
const stepDurations = ref({});

// 步骤间隔时间
const stepIntervals = ref({});

// 定期刷新视频流（防止浏览器缓存/卡死）
let streamRefreshCounter = 0;
const STREAM_REFRESH_INTERVAL = 150; // 每150次轮询（约30秒）刷新一次流

// 开始轮询
const startPolling = () => {
  stopPolling();
  shownEventIds.value.clear(); // 清除已显示事件记录
  streamRefreshCounter = 0;
  
  pollingTimer = setInterval(async () => {
    // 定期刷新视频流
    streamRefreshCounter++;
    if (streamRefreshCounter >= STREAM_REFRESH_INTERVAL) {
      streamRefreshCounter = 0;
      refreshStream();
    }
    try {
      const res = await getDetectionResults();
      const data = res.data;
      
      // 同步输入源类型到 store
      if (data.source_type) {
        sourceStore.setSourceType(data.source_type);
      }
      
      fps.value = data.fps || 0;
      latency.value = data.latency || 0;
      detectionCount.value = (data.detections || []).length;
      
      // 更新平均周期时间 (CT)
      cycleTime.value = data.average_cycle_time || 0;
      
      // 更新步骤截图
      if (data.step_screenshots) {
        stepScreenshots.value = data.step_screenshots;
      }
      
      // 更新步骤检测时间
      if (data.step_detection_times) {
        stepDetectionTimes.value = data.step_detection_times;
      }
      
      // 更新步骤耗时
      if (data.step_durations) {
        stepDurations.value = data.step_durations;
      }
      
      // 更新步骤间隔时间
      if (data.step_intervals) {
        stepIntervals.value = data.step_intervals;
      }
      
      // 更新步骤计数、计数器和事件
      if (isRunning.value) {
        updateStepsFromBackend(
          data.step_counts || {}, 
          data.detections || [],
          data.counters || {},
          data.recent_events || []
        );
      }
      
      // 绘制检测框（前端绘制，支持中文和自定义样式）
      if (data.detections) {
        drawDetections(data.detections);
      }
      
      // 处理检测结果逻辑
      if (data.detections && data.detections.length > 0 && isRunning.value) {
        processDetections(data.detections);
      }
      
      // 如果是视频输入源，获取视频信息
      if (isVideoSource.value && !isDraggingProgress.value) {
        try {
          const videoRes = await api.get('/source/video/info');
          if (videoRes.data.status === 'success') {
            videoInfo.value.progress = videoRes.data.progress || 0;
            videoInfo.value.currentTime = videoRes.data.current_time || 0;
            videoInfo.value.duration = videoRes.data.duration || 0;
            // 只有在不改变倍速时才更新（防止用户选择的倍速被覆盖）
            if (!isChangingSpeed.value) {
              videoInfo.value.speed = videoRes.data.speed || 1;
            }
            videoInfo.value.syncMode = videoRes.data.sync_mode || false;
            videoInfo.value.ended = videoRes.data.ended || false;
            
            // 如果视频结束，停止轮询
            if (videoRes.data.ended) {
              isRunning.value = false;
              isStreaming.value = false;
              projectStore.setRunningStatus(false);
            }
          }
        } catch (e) {
          // 静默处理
        }
      }
    } catch (err) {
      // 静默处理轮询错误
    }
  }, 200); // 每 200ms 轮询一次
};

// 已显示的事件ID（避免重复显示提示框）
const shownEventIds = ref(new Set());

// 从后端数据更新步骤状态
const updateStepsFromBackend = (stepCounts, currentDetections, backendCounters, recentEvents) => {
  // 获取当前检测到的标签
  const detectingLabels = new Set(currentDetections.map(d => d.label));
  
  // 更新步骤状态
  steps.value.forEach((step, idx) => {
    // 使用 label 字段匹配后端数据（后端使用模型标签名）
    const stepLabel = step.label || step.name;
    const count = stepCounts[stepLabel] || 0;
    
    // 更新表格数据
    if (tableData.value[idx]) {
      tableData.value[idx].count = count;
      tableData.value[idx].status = count > 0 ? 'completed' : 'pending';
    }
    
    // 更新步骤状态
    if (detectingLabels.has(stepLabel)) {
      step.status = 'active';
    } else if (count > 0) {
      step.status = 'completed';
    }
    
    // 更新截图
    if (stepScreenshots.value[stepLabel]) {
      step.screenshot = `data:image/jpeg;base64,${stepScreenshots.value[stepLabel]}`;
    }
  });
  
  // 使用后端返回的计数器数据
  if (backendCounters && currentProject.value?.counters_config) {
    currentProject.value.counters_config.forEach(counter => {
      if (backendCounters[counter.name] !== undefined) {
        counter.value = backendCounters[counter.name];
      }
    });
  }
  
  // 处理事件提示框
  if (recentEvents && recentEvents.length > 0) {
    recentEvents.forEach(event => {
      const eventKey = `${event.event_id}_${Math.floor(event.timestamp)}`;
      if (!shownEventIds.value.has(eventKey) && event.show_notification) {
        shownEventIds.value.add(eventKey);
        
        // 使用事件配置的 toast_id 来显示提示框
        const toastId = event.toast_id || (event.event_id === 1 ? 'ok' : event.event_id === 2 ? 'ng' : 'ok');
        showToastById(toastId, event.event_name, event.reason);
      }
    });
  }
  
  // 更新图表
  updateCharts();
};

// 停止轮询
const stopPolling = () => {
  if (pollingTimer) {
    clearInterval(pollingTimer);
    pollingTimer = null;
  }
};

const standby = async () => {
  try {
    // 待机：只停止模型推理，画面继续播放
    await standbyDetection();
    isRunning.value = false;
    projectStore.setRunningStatus(false);
    // 重置步骤状态
    steps.value.forEach(s => {
      s.status = 'pending';
      s.result = null;
    });
    // 清除检测框
    if (detectionCanvas.value) {
      const ctx = detectionCanvas.value.getContext('2d');
      ctx.clearRect(0, 0, detectionCanvas.value.width, detectionCanvas.value.height);
    }
    // 确保轮询继续运行（更新视频进度等）
    if (!pollingTimer) {
      startPolling();
    }
    ElMessage.info('已待机：检测停止，画面继续');
  } catch (err) {
    console.error('待机失败:', err);
  }
};

const resetCounters = async () => {
  if (!currentProject.value?.counters_config) return;
  
  // 先重置后端统计数据（关键！必须先重置后端，否则轮询会覆盖前端数据）
  try {
    await resetDetectionStats();
  } catch (e) {
    console.error('重置后端统计失败:', e);
  }
  
  // 重置所有计数器
  currentProject.value.counters_config.forEach(c => {
    c.value = 0;
  });
  
  // 重置步骤状态
  steps.value.forEach(s => {
    s.status = 'pending';
    s.result = null;
    s.screenshot = null;
  });
  
  tableData.value.forEach(t => {
    t.count = 0;
    t.status = 'pending';
  });
  
  // 清空步骤截图缓存
  stepScreenshots.value = {};
  
  updateCharts();
  ElMessage.success('计数器已清零');
};


// 处理检测结果逻辑
const processDetections = (detections) => {
  if (!currentProject.value) return;
  
  const stepsConfig = currentProject.value.steps_config || [];
  const eventsConfig = currentProject.value.events_config || [];
  
  // 更新步骤状态
  detections.forEach(det => {
    const stepIdx = steps.value.findIndex(s => s.name === det.label || s.id === det.label);
    if (stepIdx > -1) {
      steps.value[stepIdx].status = 'active';
      
      // 更新表格计数
      if (tableData.value[stepIdx]) {
        tableData.value[stepIdx].count++;
      }
    }
  });
  
  // 检查事件触发（简化版）
  // 实际逻辑应该根据 logic_mode 和 pipeline_config 来判断
};

// 触发事件
const triggerEvent = (eventId) => {
  if (!currentProject.value) return;
  
  const eventsConfig = currentProject.value.events_config || [];
  const event = eventsConfig.find(e => e.id === eventId);
  
  if (!event) return;
  
  // 执行计数器动作
  if (event.actions) {
    event.actions.forEach(action => {
      const counter = currentProject.value.counters_config?.find(c => c.name === action.counter_name);
      if (counter) {
        counter.value += action.value || 1;
      }
    });
  }
  
  // 显示提示框
  if (event.show_notification) {
    const type = event.id === 'event_1' ? 'ok' : event.id === 'event_2' ? 'ng' : 'custom';
    showToast(type, event.name, event.custom_text);
  }
  
  updateCharts();
};

// 自动恢复输入源
const autoRestoreSource = async () => {
  // 检查是否开启了自动保存
  const autoSaveSettings = localStorage.getItem('auto_save_settings');
  if (!autoSaveSettings) return;
  
  const settings = JSON.parse(autoSaveSettings);
  if (!settings.enabled) return;
  
  // 从 sourceStore 加载保存的配置
  sourceStore.loadConfig();
  
  const savedType = sourceStore.sourceType;
  if (!savedType || savedType === 'camera') {
    // 摄像头：尝试启动上次使用的摄像头
    try {
      const cameraSettings = sourceStore.cameraSettings;
      const [w, h] = (cameraSettings.resolution || '1280x720').split('x').map(Number);
      await api.post('/source/camera/start', {
        device_index: cameraSettings.deviceIndex || 0,
        width: w,
        height: h,
        fps: cameraSettings.fps || 30
      });
      sourceStore.setSourceType('camera');
      sourceStore.setStreaming(true);
      isStreaming.value = true;
      console.log('[AutoRestore] 已自动恢复摄像头');
    } catch (err) {
      console.warn('[AutoRestore] 自动恢复摄像头失败:', err);
    }
  } else if (savedType === 'hikvision') {
    // 海康工业相机：尝试启动上次使用的相机
    try {
      const hikSettings = sourceStore.hikvisionSettings;
      const [w, h] = (hikSettings.resolution || '1280x720').split('x').map(Number);
      await api.post('/source/hikvision/start', {
        device_index: hikSettings.deviceIndex || 0,
        width: w,
        height: h,
        fps: hikSettings.fps || 30
      });
      sourceStore.setSourceType('hikvision');
      sourceStore.setStreaming(true);
      isStreaming.value = true;
      console.log('[AutoRestore] 已自动恢复海康相机');
    } catch (err) {
      console.warn('[AutoRestore] 自动恢复海康相机失败:', err);
    }
  } else if (savedType === 'video' && sourceStore.videoPath) {
    // 视频：尝试启动上次使用的视频
    try {
      await api.post('/source/video/start', {
        file_path: sourceStore.videoPath,
        speed: sourceStore.videoSpeed || 1
      });
      sourceStore.setSourceType('video');
      sourceStore.setStreaming(true);
      isStreaming.value = true;
      startPolling();
      console.log('[AutoRestore] 已自动恢复视频');
    } catch (err) {
      console.warn('[AutoRestore] 自动恢复视频失败:', err);
    }
  }
};

onMounted(() => {
  // 加载系统设置
  systemStore.loadSettings();
  
  nextTick(() => {
    initCharts();
    resizeCanvas();
  });
  
  // 监听窗口大小变化
  window.addEventListener('resize', handleResize);
  
  // 检查是否有正在进行的检测，并同步输入源类型
  getSourceStatus().then(async res => {
    // 同步输入源类型到 store
    if (res.data.source_type) {
      sourceStore.setSourceType(res.data.source_type);
    }
    if (res.data.is_detecting) {
      isRunning.value = true;
      startPolling();
    }
    // 即使没有检测，如果有输入源也开始轮询（以便更新视频进度）
    if (res.data.is_running && res.data.source_type === 'video') {
      startPolling();
    }
    
    // 如果当前没有输入源在运行，尝试自动恢复
    if (!res.data.is_running && !res.data.source_type) {
      await autoRestoreSource();
    }
  }).catch(() => {});
});

const handleResize = () => {
  resizeCanvas();
  if (pieChartInstance && !pieChartInstance.isDisposed()) {
    pieChartInstance.resize();
  }
  if (gaugeChartInstance && !gaugeChartInstance.isDisposed()) {
    gaugeChartInstance.resize();
  }
};

onUnmounted(() => {
  window.removeEventListener('resize', handleResize);
  stopPolling();
  
  if (pieChartInstance) {
    pieChartInstance.dispose();
    pieChartInstance = null;
  }
  if (gaugeChartInstance) {
    gaugeChartInstance.dispose();
    gaugeChartInstance = null;
  }
});

// 监听计数器变化更新图表
watch(counters, () => {
  updateCharts();
}, { deep: true });

// 暴露触发事件方法供测试
defineExpose({ triggerEvent, showToast });
</script>

<style scoped>
.toast-enter-active,
.toast-leave-active {
  transition: all 0.3s ease;
}

.toast-enter-from {
  opacity: 0;
  transform: translateX(50px);
}

.toast-leave-to {
  opacity: 0;
  transform: translateX(50px);
}

/* 视频进度条样式 */
.video-progress-slider :deep(.el-slider__runway) {
  height: 4px;
  background-color: rgba(100, 116, 139, 0.5);
}

.video-progress-slider :deep(.el-slider__bar) {
  height: 4px;
  background-color: #06b6d4;
}

.video-progress-slider :deep(.el-slider__button) {
  width: 12px;
  height: 12px;
  border: 2px solid #06b6d4;
  background-color: #0f172a;
}

.video-progress-slider :deep(.el-slider__button):hover {
  transform: scale(1.2);
}

/* 视频倍速选择器样式 */
.video-speed-select :deep(.el-input__wrapper) {
  background-color: rgba(15, 23, 42, 0.8);
  border-color: rgba(100, 116, 139, 0.3);
  box-shadow: none;
}

.video-speed-select :deep(.el-input__inner) {
  color: #06b6d4;
  font-size: 12px;
}

/* 同步模式开关样式 */
.video-sync-switch :deep(.el-switch__core) {
  background-color: rgba(100, 116, 139, 0.3);
  border-color: rgba(100, 116, 139, 0.3);
}

.video-sync-switch :deep(.is-checked .el-switch__core) {
  background-color: #06b6d4;
  border-color: #06b6d4;
}

.video-sync-switch :deep(.el-switch__label) {
  color: #9ca3af;
  font-size: 11px;
}

.video-sync-switch :deep(.el-switch__label.is-active) {
  color: #06b6d4;
}
</style>
