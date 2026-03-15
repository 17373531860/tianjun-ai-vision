<template>
  <!-- ===== DUAL WORKSTATION MODE (2 channels) ===== -->
  <div v-if="channelCount === 2" class="grid grid-cols-2 gap-2 h-[calc(100vh-7.25rem)] p-2 relative">
    <div v-for="ch in 2" :key="ch - 1" class="flex flex-col gap-1.5 min-h-0 overflow-hidden">
      <!-- Video panel -->
      <div class="relative bg-black border-2 rounded-lg overflow-hidden flex-1 min-h-0"
        :class="selectedChannel === (ch - 1) ? 'border-cyan-500' : 'border-slate-700'"
        @click="selectedChannel = ch - 1">
        <canvas :ref="el => { if (el) multiVideoCanvasRefs[ch - 1] = el }" class="absolute inset-0 w-full h-full"></canvas>
        <canvas :ref="el => { if (el) multiCanvasRefs[ch - 1] = el }" class="absolute inset-0 w-full h-full pointer-events-none"></canvas>
        <div class="absolute top-1.5 left-1.5 bg-slate-900/80 text-white px-2 py-0.5 rounded text-xs font-bold">
          工位 {{ ch }}
          <span v-if="multiChannelData[ch - 1]?.projectName" class="text-cyan-400 ml-1">{{ multiChannelData[ch - 1].projectName }}</span>
        </div>
        <div class="absolute top-1.5 right-1.5 px-2 py-0.5 rounded text-[10px] font-bold"
          :class="multiChannelData[ch - 1]?.isDetecting ? 'bg-green-600/90 text-white animate-pulse' : multiChannelData[ch - 1]?.isRunning ? 'bg-yellow-600/90 text-white' : 'bg-gray-600/90 text-white'">
          {{ multiChannelData[ch - 1]?.isDetecting ? '检测中' : multiChannelData[ch - 1]?.isRunning ? '待机' : '停止' }}
        </div>
        <div class="absolute bottom-0 left-0 right-0 bg-black/70 backdrop-blur-sm px-2 py-1 flex gap-3 text-xs">
          <span class="text-white font-mono">总: <span class="text-cyan-400 font-bold">{{ multiChannelData[ch - 1]?.total ?? 0 }}</span></span>
          <span class="text-white font-mono">OK: <span class="text-green-400 font-bold">{{ multiChannelData[ch - 1]?.ok ?? 0 }}</span></span>
          <span class="text-white font-mono">NG: <span class="text-red-400 font-bold">{{ multiChannelData[ch - 1]?.ng ?? 0 }}</span></span>
          <span class="ml-auto text-gray-400">FPS: {{ multiChannelData[ch - 1]?.fps ?? 0 }}</span>
        </div>
      </div>
      <!-- SOP mini strip -->
      <div class="h-20 bg-slate-900 border border-slate-700 rounded overflow-hidden flex flex-col flex-shrink-0">
        <div class="bg-slate-800 px-2 py-0.5 text-cyan-400 text-xs font-bold border-b border-slate-700 flex items-center justify-between">
          <span>SOP</span>
          <span class="text-[10px] text-gray-400">CT: {{ getDisplayCT(multiChannelData[ch - 1]) }}</span>
        </div>
        <div class="flex-1 flex items-center gap-1 px-1.5 overflow-x-auto">
          <div v-for="(step, idx) in (multiChannelData[ch - 1]?.steps || [])" :key="idx"
            class="flex-shrink-0 px-1.5 py-0.5 rounded text-[10px] font-bold border"
            :class="step.status === 'completed' ? 'border-green-500 bg-green-900/30 text-green-300' : step.status === 'active' ? 'border-cyan-500 bg-cyan-900/30 text-cyan-300 animate-pulse' : 'border-slate-600 bg-slate-800 text-gray-500'">
            {{ step.name }}
          </div>
          <div v-if="!multiChannelData[ch - 1]?.steps?.length" class="text-gray-600 text-[10px] w-full text-center">等待检测</div>
        </div>
      </div>
      <!-- Data panel: counters + charts + table -->
      <div class="h-48 grid grid-cols-2 gap-1.5 flex-shrink-0">
        <!-- Left: counters + step table -->
        <div class="flex flex-col gap-1.5 min-h-0">
          <div class="grid grid-cols-3 gap-1 flex-shrink-0">
            <div class="bg-slate-900 border border-slate-700 rounded p-1 text-center">
              <div class="text-[10px] text-gray-400">总产量</div>
              <div class="text-lg font-bold font-mono text-white">{{ multiChannelData[ch - 1]?.total ?? 0 }}</div>
            </div>
            <div class="bg-slate-900 border border-slate-700 rounded p-1 text-center">
              <div class="text-[10px] text-gray-400">合格</div>
              <div class="text-lg font-bold font-mono text-green-400">{{ multiChannelData[ch - 1]?.ok ?? 0 }}</div>
            </div>
            <div class="bg-slate-900 border border-slate-700 rounded p-1 text-center">
              <div class="text-[10px] text-gray-400">不良</div>
              <div class="text-lg font-bold font-mono text-red-400">{{ multiChannelData[ch - 1]?.ng ?? 0 }}</div>
            </div>
          </div>
          <div class="flex-1 bg-slate-900 border border-slate-700 rounded overflow-auto min-h-0">
            <table class="w-full text-[10px]">
              <thead class="bg-slate-800 text-gray-400 sticky top-0"><tr><th class="px-1 py-0.5">步骤</th><th class="px-1 py-0.5">状态</th></tr></thead>
              <tbody class="text-gray-300 divide-y divide-slate-800">
                <tr v-for="(row, i) in (multiChannelData[ch - 1]?.tableData || []).slice(0, 6)" :key="i" :class="row.status === 'completed' ? 'bg-green-900/20' : ''">
                  <td class="px-1 py-0.5 truncate max-w-[80px]">{{ row.step }}</td>
                  <td class="px-1 py-0.5"><span :class="row.status === 'completed' ? 'text-green-400' : 'text-gray-500'">{{ row.status === 'completed' ? 'OK' : '--' }}</span></td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
        <!-- Right: charts area -->
        <div class="flex flex-col gap-1.5 min-h-0">
          <!-- Yield rate + pie -->
          <div class="flex-1 bg-slate-900 border border-slate-700 rounded p-1 flex items-center justify-center min-h-0">
            <div class="text-center">
              <div class="text-[10px] text-gray-400 mb-0.5">合格率</div>
              <div class="text-2xl font-bold font-mono" :class="(multiChannelData[ch - 1]?.yieldRate ?? 0) >= 90 ? 'text-green-400' : (multiChannelData[ch - 1]?.yieldRate ?? 0) >= 70 ? 'text-yellow-400' : 'text-red-400'">
                {{ multiChannelData[ch - 1]?.yieldRate ?? 0 }}%
              </div>
              <div class="w-full bg-slate-700 rounded-full h-1.5 mt-1">
                <div class="h-1.5 rounded-full transition-all" :class="(multiChannelData[ch - 1]?.yieldRate ?? 0) >= 90 ? 'bg-green-500' : (multiChannelData[ch - 1]?.yieldRate ?? 0) >= 70 ? 'bg-yellow-500' : 'bg-red-500'"
                  :style="{ width: (multiChannelData[ch - 1]?.yieldRate ?? 0) + '%' }"></div>
              </div>
            </div>
          </div>
          <!-- NG top 3 -->
          <div class="flex-1 bg-slate-900 border border-slate-700 rounded p-1 flex flex-col min-h-0">
            <div class="text-[10px] text-cyan-400 font-bold mb-0.5">NG TOP3</div>
            <div class="flex-1 overflow-auto space-y-0.5">
              <div v-for="(item, idx) in (multiChannelData[ch - 1]?.ngStepRanking || [])" :key="item.step"
                class="flex items-center gap-1 text-[10px] bg-slate-800/50 px-1 py-0.5 rounded">
                <span class="text-white font-bold w-3">{{ idx + 1 }}</span>
                <span class="flex-1 text-gray-300 truncate">{{ item.step }}</span>
                <span class="text-white font-bold">{{ item.rate.toFixed(0) }}%</span>
              </div>
              <div v-if="!multiChannelData[ch - 1]?.ngStepRanking?.length" class="text-gray-600 text-[10px] text-center">暂无</div>
            </div>
          </div>
        </div>
      </div>
      <!-- Controls -->
      <div class="flex gap-1.5 flex-shrink-0">
        <button @click="startDetectionForChannel(ch - 1)" :disabled="!currentProject || multiChannelData[ch - 1]?.isDetecting"
          class="flex-1 bg-emerald-600 hover:bg-emerald-500 disabled:bg-gray-700 disabled:cursor-not-allowed text-white py-1 rounded text-xs font-bold">开始</button>
        <button @click="stopDetectionForChannel(ch - 1)" :disabled="!multiChannelData[ch - 1]?.isRunning"
          class="flex-1 bg-red-600 hover:bg-red-500 disabled:bg-gray-700 disabled:cursor-not-allowed text-white py-1 rounded text-xs font-bold">停止</button>
        <button @click="standbyForChannel(ch - 1)" :disabled="!multiChannelData[ch - 1]?.isDetecting"
          class="flex-1 bg-yellow-600 hover:bg-yellow-500 disabled:bg-gray-700 disabled:cursor-not-allowed text-white py-1 rounded text-xs font-bold">待机</button>
      </div>
    </div>
    <!-- Event toasts -->
    <template v-for="position in ['top-right', 'top-left', 'bottom-right', 'bottom-left', 'center']" :key="position">
      <div class="fixed z-50 pointer-events-none flex flex-col gap-2" :class="getPositionClass(position)">
        <transition-group name="toast">
          <div v-for="toast in activeToasts.filter(t => t.position === position)" :key="toast.id"
            class="px-6 py-4 rounded-xl shadow-2xl text-white font-bold pointer-events-auto transform transition-all duration-300 text-center"
            :style="{ backgroundColor: toast.color, fontSize: toast.fontSize + 'px' }">
            <div class="flex items-center gap-3 justify-center">
              <el-icon :size="24"><component :is="toast.icon" /></el-icon>
              <div><div class="font-bold">{{ toast.title }}</div><div v-if="toast.subtitle" class="text-sm opacity-80">{{ toast.subtitle }}</div></div>
            </div>
          </div>
        </transition-group>
      </div>
    </template>
  </div>

  <!-- ===== QUAD WORKSTATION MODE (4 channels) ===== -->
  <div v-else-if="channelCount > 2" class="flex flex-col h-[calc(100vh-7.25rem)] p-2 gap-2 relative">
    <!-- 2x2 video grid -->
    <div class="flex-1 grid grid-cols-2 grid-rows-2 gap-2 min-h-0">
      <div v-for="ch in channelCount" :key="ch - 1"
        class="relative bg-black border-2 rounded-lg overflow-hidden cursor-pointer transition-all min-h-0"
        :class="selectedChannel === (ch - 1) ? 'border-cyan-500 shadow-[0_0_8px_rgba(6,182,212,0.4)]' : 'border-slate-700 hover:border-slate-500'"
        @click="selectedChannel = ch - 1">
        <canvas :ref="el => { if (el) multiVideoCanvasRefs[ch - 1] = el }" class="absolute inset-0 w-full h-full"></canvas>
        <canvas :ref="el => { if (el) multiCanvasRefs[ch - 1] = el }" class="absolute inset-0 w-full h-full pointer-events-none"></canvas>
        <div class="absolute top-1 left-1 bg-slate-900/80 text-white px-2 py-0.5 rounded text-[10px] font-bold">
          工位{{ ch }}
          <span v-if="multiChannelData[ch - 1]?.projectName" class="text-cyan-400 ml-0.5">{{ multiChannelData[ch - 1].projectName }}</span>
        </div>
        <div class="absolute top-1 right-1 px-1.5 py-0.5 rounded text-[10px] font-bold"
          :class="multiChannelData[ch - 1]?.isDetecting ? 'bg-green-600/90 text-white animate-pulse' : multiChannelData[ch - 1]?.isRunning ? 'bg-yellow-600/90 text-white' : 'bg-gray-600/90 text-white'">
          {{ multiChannelData[ch - 1]?.isDetecting ? '检测中' : multiChannelData[ch - 1]?.isRunning ? '待机' : '停止' }}
        </div>
        <div class="absolute bottom-0 left-0 right-0 bg-black/70 px-2 py-1 flex gap-3 text-[10px]">
          <span class="text-white font-mono">总:<span class="text-cyan-400 font-bold">{{ multiChannelData[ch - 1]?.total ?? 0 }}</span></span>
          <span class="text-white font-mono">OK:<span class="text-green-400 font-bold">{{ multiChannelData[ch - 1]?.ok ?? 0 }}</span></span>
          <span class="text-white font-mono">NG:<span class="text-red-400 font-bold">{{ multiChannelData[ch - 1]?.ng ?? 0 }}</span></span>
          <span class="ml-auto text-gray-400">FPS:{{ multiChannelData[ch - 1]?.fps ?? 0 }}</span>
        </div>
      </div>
    </div>
    <!-- Selected channel detail panel -->
    <div class="h-64 bg-slate-900 border border-slate-700 rounded-lg overflow-hidden flex flex-col flex-shrink-0">
      <div class="bg-slate-800 px-3 py-1 border-b border-slate-700 flex items-center gap-3">
        <span class="text-cyan-400 font-bold text-sm">工位 {{ selectedChannel + 1 }} 详情</span>
        <span class="text-[10px] bg-slate-700 px-2 py-0.5 rounded text-gray-300">CT: {{ getDisplayCT(multiChannelData[selectedChannel]) }}</span>
        <div class="ml-auto flex gap-1.5">
          <button @click="startDetectionForChannel(selectedChannel)" :disabled="!currentProject || multiChannelData[selectedChannel]?.isDetecting"
            class="bg-emerald-600 hover:bg-emerald-500 disabled:bg-gray-700 disabled:cursor-not-allowed text-white px-2.5 py-0.5 rounded text-[10px] font-bold">开始</button>
          <button @click="stopDetectionForChannel(selectedChannel)" :disabled="!multiChannelData[selectedChannel]?.isRunning"
            class="bg-red-600 hover:bg-red-500 disabled:bg-gray-700 disabled:cursor-not-allowed text-white px-2.5 py-0.5 rounded text-[10px] font-bold">停止</button>
          <button @click="standbyForChannel(selectedChannel)" :disabled="!multiChannelData[selectedChannel]?.isDetecting"
            class="bg-yellow-600 hover:bg-yellow-500 disabled:bg-gray-700 disabled:cursor-not-allowed text-white px-2.5 py-0.5 rounded text-[10px] font-bold">待机</button>
        </div>
      </div>
      <div class="flex-1 grid grid-cols-4 gap-2 p-2 min-h-0 overflow-hidden">
        <!-- Col 1: SOP steps -->
        <div class="flex flex-col gap-1 overflow-auto">
          <div class="text-[10px] text-cyan-400 font-bold mb-0.5">SOP 流程</div>
          <div v-for="(step, idx) in (multiChannelData[selectedChannel]?.steps || [])" :key="idx"
            class="px-1.5 py-0.5 rounded text-[10px] font-bold border"
            :class="step.status === 'completed' ? 'border-green-500 bg-green-900/30 text-green-300' : step.status === 'active' ? 'border-cyan-500 bg-cyan-900/30 text-cyan-300 animate-pulse' : 'border-slate-600 bg-slate-800 text-gray-500'">
            {{ step.name }}
          </div>
          <div v-if="!multiChannelData[selectedChannel]?.steps?.length" class="text-gray-600 text-[10px] text-center mt-2">等待检测</div>
        </div>
        <!-- Col 2: Counters + step table -->
        <div class="flex flex-col gap-1 min-h-0">
          <div class="grid grid-cols-3 gap-1 flex-shrink-0">
            <div class="bg-slate-800 rounded p-0.5 text-center"><div class="text-[9px] text-gray-400">总</div><div class="text-base font-bold font-mono text-white">{{ multiChannelData[selectedChannel]?.total ?? 0 }}</div></div>
            <div class="bg-slate-800 rounded p-0.5 text-center"><div class="text-[9px] text-gray-400">OK</div><div class="text-base font-bold font-mono text-green-400">{{ multiChannelData[selectedChannel]?.ok ?? 0 }}</div></div>
            <div class="bg-slate-800 rounded p-0.5 text-center"><div class="text-[9px] text-gray-400">NG</div><div class="text-base font-bold font-mono text-red-400">{{ multiChannelData[selectedChannel]?.ng ?? 0 }}</div></div>
          </div>
          <div class="flex-1 overflow-auto min-h-0">
            <table class="w-full text-[10px]">
              <thead class="bg-slate-800 text-gray-400 sticky top-0"><tr><th class="px-1 py-0.5">步骤</th><th class="px-1 py-0.5">状态</th></tr></thead>
              <tbody class="text-gray-300 divide-y divide-slate-800">
                <tr v-for="(row, i) in (multiChannelData[selectedChannel]?.tableData || [])" :key="i" :class="row.status === 'completed' ? 'bg-green-900/20' : ''">
                  <td class="px-1 py-0.5 truncate max-w-[100px]">{{ row.step }}</td>
                  <td class="px-1 py-0.5"><span :class="row.status === 'completed' ? 'text-green-400' : 'text-gray-500'">{{ row.status === 'completed' ? 'OK' : '--' }}</span></td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
        <!-- Col 3: Yield rate -->
        <div class="flex flex-col items-center justify-center bg-slate-800 rounded p-2">
          <div class="text-[10px] text-gray-400 mb-1">合格率</div>
          <div class="text-3xl font-bold font-mono" :class="(multiChannelData[selectedChannel]?.yieldRate ?? 0) >= 90 ? 'text-green-400' : (multiChannelData[selectedChannel]?.yieldRate ?? 0) >= 70 ? 'text-yellow-400' : 'text-red-400'">
            {{ multiChannelData[selectedChannel]?.yieldRate ?? 0 }}%
          </div>
          <div class="w-full bg-slate-700 rounded-full h-2 mt-2">
            <div class="h-2 rounded-full transition-all" :class="(multiChannelData[selectedChannel]?.yieldRate ?? 0) >= 90 ? 'bg-green-500' : (multiChannelData[selectedChannel]?.yieldRate ?? 0) >= 70 ? 'bg-yellow-500' : 'bg-red-500'"
              :style="{ width: (multiChannelData[selectedChannel]?.yieldRate ?? 0) + '%' }"></div>
          </div>
          <div class="mt-2 text-[10px] text-gray-400">
            <span class="text-green-400">{{ multiChannelData[selectedChannel]?.ok ?? 0 }}</span> / <span class="text-white">{{ multiChannelData[selectedChannel]?.total ?? 0 }}</span>
          </div>
        </div>
        <!-- Col 4: NG ranking -->
        <div class="flex flex-col min-h-0">
          <div class="text-[10px] text-cyan-400 font-bold mb-1">NG 步骤 TOP3</div>
          <div class="flex-1 overflow-auto space-y-1">
            <div v-for="(item, idx) in (multiChannelData[selectedChannel]?.ngStepRanking || [])" :key="item.step"
              class="flex items-center gap-1.5 bg-slate-800/50 px-1.5 py-1 rounded text-xs">
              <span class="text-sm font-bold w-4 text-white text-center">{{ idx + 1 }}</span>
              <span class="flex-1 text-gray-300 truncate">{{ item.step }}</span>
              <span class="text-sm font-bold text-white">{{ item.rate.toFixed(0) }}%</span>
            </div>
            <div v-if="!multiChannelData[selectedChannel]?.ngStepRanking?.length" class="flex items-center justify-center h-full text-gray-600 text-xs">暂无数据</div>
          </div>
        </div>
      </div>
    </div>
    <!-- Event toasts -->
    <template v-for="position in ['top-right', 'top-left', 'bottom-right', 'bottom-left', 'center']" :key="position">
      <div class="fixed z-50 pointer-events-none flex flex-col gap-2" :class="getPositionClass(position)">
        <transition-group name="toast">
          <div v-for="toast in activeToasts.filter(t => t.position === position)" :key="toast.id"
            class="px-6 py-4 rounded-xl shadow-2xl text-white font-bold pointer-events-auto transform transition-all duration-300 text-center"
            :style="{ backgroundColor: toast.color, fontSize: toast.fontSize + 'px' }">
            <div class="flex items-center gap-3 justify-center">
              <el-icon :size="24"><component :is="toast.icon" /></el-icon>
              <div><div class="font-bold">{{ toast.title }}</div><div v-if="toast.subtitle" class="text-sm opacity-80">{{ toast.subtitle }}</div></div>
            </div>
          </div>
        </transition-group>
      </div>
    </template>
  </div>

  <!-- ===== SINGLE-VIEW MODE (original layout) ===== -->
  <div v-else class="grid grid-cols-12 gap-3 h-[calc(100vh-7.25rem)] p-2 relative">
    <!-- LEFT COLUMN: VIDEO & STEPS -->
    <div class="col-span-7 flex flex-col gap-3 min-h-0">
      
      <!-- Video Region -->
      <div class="min-h-0 bg-black border-2 border-slate-700 rounded-lg relative overflow-hidden group" style="aspect-ratio: 16/9; max-height: 100%;">
        <!-- 视频流：双缓冲 img，交替使用以释放 Chromium 原生解码内存 -->
        <img 
          v-show="activeStream === 0"
          ref="streamImg0"
          :src="streamSrc0"
          class="w-full h-full object-contain"
          @load="onStreamReady(0)"
          @error="onStreamError(0)"
        />
        <img 
          v-show="activeStream === 1"
          ref="streamImg1"
          :src="streamSrc1"
          class="w-full h-full object-contain"
          @load="onStreamReady(1)"
          @error="onStreamError(1)"
        />
        
        <!-- 检测框覆盖层 -->
        <canvas 
          ref="detectionCanvas"
          class="absolute top-0 left-0 w-full h-full pointer-events-none"
        ></canvas>
        
        <!-- 运行状态指示 -->
        <div v-if="isDetecting" class="absolute top-4 right-4 bg-green-600/90 text-white px-6 py-2 rounded shadow-lg text-lg font-bold animate-pulse">
          检测中
        </div>
        <div v-else-if="isRunning && !isDetecting" class="absolute top-4 right-4 bg-yellow-600/90 text-white px-6 py-2 rounded shadow-lg text-lg font-bold">
          待机中
        </div>
        <div v-else class="absolute top-4 right-4 bg-gray-600/90 text-white px-6 py-2 rounded shadow-lg text-lg font-bold">
          已停止
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
                :disabled="isDetecting"
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
                :disabled="isDetecting || videoInfo.syncMode"
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
                  :disabled="isRunning"
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
            <span v-if="systemStore.display.monitor.showFps !== false">FPS: <span class="text-cyan-400 font-mono">{{ fps }}</span></span>
            <span v-if="systemStore.display.monitor.showLatency !== false">延迟: <span class="text-cyan-400 font-mono">{{ latency }} ms</span></span>
            <span v-if="systemStore.display.monitor.showDetectionCount !== false">检测数: <span class="text-cyan-400 font-mono">{{ detectionCount }}</span></span>
          </div>
        </div>
      </div>

      <!-- SOP流程 (Step Indicators) — non-tracking modes -->
      <div v-if="systemStore.display.monitor.stepStrip && steps.length > 0 && !isTrackingMode" class="h-44 bg-slate-900 border border-slate-700 rounded-lg overflow-hidden flex flex-col">
        <div class="bg-slate-800 px-3 py-1 border-b border-slate-700 flex-shrink-0">
          <span class="text-cyan-400 text-lg font-bold">SOP流程卡片</span>
        </div>
        <div ref="sopScrollContainer" class="flex-1 p-2 overflow-x-auto scroll-smooth">
          <div class="flex items-center h-full">
            <template v-for="(step, idx) in steps" :key="idx">
              <!-- 间隔时间显示 -->
              <div v-if="idx > 0" class="flex flex-col items-center justify-center px-1 flex-shrink-0">
                <div class="w-6 h-[2px] bg-slate-600"></div>
                <div class="text-[9px] text-yellow-400 font-mono mt-0.5 whitespace-nowrap">
                  {{ formatInterval(step.label) }}
                </div>
                <div class="w-6 h-[2px] bg-slate-600"></div>
              </div>
              
              <!-- 步骤卡片 -->
              <div
                :ref="el => { if (el) sopCardRefs[idx] = el }"
                class="w-32 flex-shrink-0 flex flex-col rounded border transition-all duration-300"
                :class="getSopCardClass(step)"
              >
                <div class="h-7 px-2 flex items-center justify-between text-xs"
                  :class="getSopHeaderClass(step)"
                >
                  <span class="font-bold truncate">{{ step.name }}</span>
                </div>
                <div class="h-16 p-1 flex items-center justify-center relative overflow-hidden"
                  :class="getSopBodyClass(step)"
                >
                   <img 
                     v-if="step.screenshot" 
                     :src="step.screenshot" 
                     class="w-full h-full object-cover rounded"
                   />
                   <el-icon v-else :size="24" class="text-slate-600"><Picture /></el-icon>
                   
                   <div v-if="step.status === 'active'" class="absolute inset-0 border-2 border-cyan-500 animate-pulse"></div>
                </div>
              </div>
            </template>
          </div>
        </div>
      </div>

      <!-- Tracking Mode Checklist Panel -->
      <div v-else-if="isTrackingMode" class="h-44 bg-slate-900 border border-slate-700 rounded-lg overflow-hidden flex flex-col">
        <div class="bg-slate-800 px-3 py-1 border-b border-slate-700 flex-shrink-0 flex justify-between items-center">
          <span class="text-cyan-400 text-lg font-bold">物品清点</span>
          <span v-if="trackingCycleActive" class="text-xs text-green-400 animate-pulse">跟踪中...</span>
          <span v-else class="text-xs text-gray-500">等待</span>
        </div>
        <div class="flex-1 p-2 overflow-x-auto">
          <div class="flex items-stretch h-full gap-3">
            <div v-for="(info, cls) in trackingChecklist" :key="cls"
              class="flex-shrink-0 w-36 bg-slate-800 rounded-lg border p-2 flex flex-col justify-between transition-all"
              :class="info.counted >= info.expected && info.expected > 0 ? 'border-green-500/70' : info.counted > info.expected && info.expected > 0 ? 'border-red-500/70' : 'border-slate-700'"
            >
              <div class="text-xs text-gray-400 truncate">{{ info.display_name || cls }}</div>
              <div class="text-center my-1">
                <span class="text-3xl font-bold font-mono"
                  :class="info.counted >= info.expected && info.expected > 0 ? 'text-green-400' : 'text-white'"
                >{{ info.counted }}</span>
                <span v-if="info.expected > 0" class="text-sm text-gray-500"> / {{ info.expected }}</span>
              </div>
              <div class="text-[10px] text-gray-500 text-center">{{ info.prefix }}1 ~ {{ info.prefix }}{{ info.counted || '?' }}</div>
            </div>
            <div v-if="Object.keys(trackingChecklist).length === 0"
              class="flex items-center justify-center text-gray-500 text-sm w-full"
            >
              等待物品出现...
            </div>
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
    <div class="col-span-5 flex flex-col gap-3 min-h-0">
      
      <!-- Top Row: Stats Counters (Dynamic) -->
      <div v-if="systemStore.display.monitor.statsPanel" class="bg-slate-900 border border-slate-700 rounded-lg p-3 flex flex-col">
        <template v-if="currentProject && counters.length > 0">
          <!-- 三个系统内置计数器并排 -->
          <div class="grid grid-cols-3 gap-2 mb-2">
            <div v-for="counter in builtinCounters" :key="counter.name"
              class="flex flex-col items-center justify-center bg-slate-800/50 p-2 rounded-lg"
            >
               <div class="text-sm text-gray-300 mb-0.5">{{ getCounterDisplayName(counter.name) }}</div>
               <div class="font-mono"
                 :class="counter.name === '合格总数' ? 'text-2xl text-green-400' : counter.name === '不良总数' ? 'text-2xl text-red-500' : 'text-2xl text-white'"
               >
                 {{ counter.value }}
               </div>
            </div>
          </div>
          
          <!-- 其他计数器（NG步骤+自定义），可滚动 -->
          <div v-if="extraCounters.length > 0" class="grid grid-cols-3 gap-2 border-t border-slate-800 pt-2 overflow-y-auto max-h-24">
             <div v-for="counter in extraCounters" :key="counter.name"
               class="flex flex-col items-center justify-center bg-slate-800/30 p-1.5 rounded"
             >
                <span class="text-sm text-gray-500 truncate w-full text-center">{{ counter.name }}</span>
                <span class="font-bold text-xl" :class="getCounterColor(counter.name)">{{ counter.value }}</span>
             </div>
          </div>
        </template>
        <div v-else class="flex flex-col items-center justify-center h-full text-gray-500 py-4">
          <el-icon :size="32" class="mb-2"><Folder /></el-icon>
          <span class="text-xs">请先选择项目以查看统计数据</span>
        </div>
      </div>

      <!-- Middle: Charts + NG Ranking -->
      <div class="h-52 grid grid-cols-3 gap-2">
         <!-- Pie Chart -->
         <div v-if="systemStore.display.monitor.defectChart" class="bg-slate-900 border border-slate-700 rounded-lg p-2 relative">
            <h3 class="text-cyan-400 text-base font-bold absolute top-1.5 left-2">良品/不良统计</h3>
            <div ref="defectChartRef" class="w-full h-full"></div>
         </div>
         <!-- Yield Rate Gauge -->
         <div v-if="systemStore.display.monitor.capacityChart" class="bg-slate-900 border border-slate-700 rounded-lg p-2 relative">
            <h3 class="text-cyan-400 text-base font-bold absolute top-1.5 left-2">合格率</h3>
            <div ref="capacityGaugeRef" class="w-full h-full"></div>
         </div>
         <!-- NG Step Ranking -->
         <div class="bg-slate-900 border border-slate-700 rounded-lg p-2 flex flex-col">
            <h3 class="text-cyan-400 text-base font-bold mb-1.5">NG步骤TOP3</h3>
            <div class="flex-1 overflow-y-auto space-y-1">
              <div v-if="ngStepRanking.length === 0" class="flex items-center justify-center h-full text-gray-600 text-base">
                暂无数据
              </div>
              <div v-for="(item, idx) in ngStepRanking.slice(0, 3)" :key="item.step"
                class="flex items-center gap-2 bg-slate-800/50 px-2 py-1.5 rounded"
              >
                <span class="text-lg font-bold w-6 text-center text-white">{{ idx + 1 }}</span>
                <span class="flex-1 text-base text-gray-300 truncate">{{ item.step }}</span>
                <span class="text-lg font-bold text-white">{{ item.rate.toFixed(1) }}%</span>
              </div>
            </div>
         </div>
      </div>

      <!-- Bottom: Detail Table & Controls -->
      <div v-if="systemStore.display.monitor.stepTable" class="flex-1 bg-slate-900 border border-slate-700 rounded-lg overflow-hidden flex flex-col">
         <div class="bg-slate-800 px-3 py-2 flex justify-between items-center border-b border-slate-700">
            <span class="text-cyan-400 text-lg font-bold">步骤统计</span>
            <span class="text-sm bg-slate-700 px-2 py-0.5 rounded text-gray-300">CT: {{ displayCT }}s</span>
         </div>
         <div class="flex-1 overflow-auto">
            <table class="w-full text-left text-sm">
               <thead class="bg-slate-800 text-gray-400 top-0 sticky">
                  <tr>
                     <th class="px-2 py-1.5">No</th>
                     <th class="px-2 py-1.5">步骤</th>
                     <th class="px-2 py-1.5">结果</th>
                     <th class="px-2 py-1.5">PT/s</th>
                     <th class="px-2 py-1.5">状态</th>
                  </tr>
               </thead>
               <tbody class="divide-y divide-slate-800 text-gray-300">
                  <tr v-for="(row, i) in tableData" :key="i" 
                    class="hover:bg-slate-800/50"
                    :class="row.status === 'completed' ? 'bg-green-800/30' : ''"
                  >
                     <td class="px-2 py-1.5">{{ i + 1 }}</td>
                     <td class="px-2 py-1.5">{{ row.step }}</td>
                     <td class="px-2 py-1.5">
                       <span v-if="row.cycleResult === 'ok'" class="text-green-400">OK</span>
                       <span v-else-if="row.cycleResult === 'ng'" class="text-red-500">NG</span>
                       <span v-else class="text-gray-500">--</span>
                     </td>
                     <td class="px-2 py-1.5 text-white font-mono">{{ formatStepPT(row.label) }}</td>
                     <td class="px-2 py-1.5">
                       <span :class="row.status === 'completed' ? 'text-white' : 'text-gray-500'">
                         {{ row.status === 'completed' ? '已检测' : '待检测' }}
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
              :disabled="!currentProject || isDetecting || isOperating"
              class="flex-1 bg-emerald-500 hover:bg-emerald-400 disabled:bg-gray-600 disabled:cursor-not-allowed text-white py-2.5 rounded text-lg font-bold shadow transition-colors"
            >
              {{ isOperating && !isDetecting ? '启动中...' : '开始' }}
            </button>
            <button 
              @click="stopDetectionHandler"
              :disabled="!isRunning || isOperating"
              class="flex-1 bg-red-500 hover:bg-red-400 disabled:bg-gray-600 disabled:cursor-not-allowed text-white py-2.5 rounded text-lg font-bold shadow transition-colors"
            >
              {{ isOperating && isRunning ? '停止中...' : '停止' }}
            </button>
            <button 
              @click="standbyHandler"
              :disabled="!isDetecting || isOperating"
              class="flex-1 bg-yellow-600 hover:bg-yellow-500 disabled:bg-gray-600 disabled:cursor-not-allowed text-white py-2.5 rounded text-lg font-bold shadow transition-colors"
            >
              待机
            </button>
            <button 
              @click="resetCounters"
              :disabled="isDetecting || isOperating"
              class="flex-1 bg-cyan-500 hover:bg-cyan-400 disabled:bg-gray-600 disabled:cursor-not-allowed text-white py-2.5 rounded text-lg font-bold shadow transition-colors"
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
import { startDetection as apiStartDetection, stopDetection as apiStopDetection, pauseDetection, resumeDetection, standbyDetection, resumeInference, resetDetection, resetDetectionStats, getDetectionResults, getSourceStatus, setProjectConfig, getWorkstations } from '@/api/detection';
import { getModelDetail } from '@/api/model';
import api, { getBackendHost } from '@/api/index';

const projectStore = useProjectStore();
const systemStore = useSystemStore();
const sourceStore = useSourceStore();

const streamImg0 = ref(null);
const streamImg1 = ref(null);
const detectionCanvas = ref(null);

// SOP 滚动相关
const sopScrollContainer = ref(null);
const sopCardRefs = {};
let lastScrolledIdx = -1;

// Chart Refs
const defectChartRef = ref(null);
const capacityGaugeRef = ref(null);
let pieChartInstance = null;
let gaugeChartInstance = null;

// State
const steps = ref([]);
const tableData = ref([]);
const isRunning = ref(false);   // video capture thread active
const isDetecting = ref(false); // inference active (subset of isRunning)
const isPaused = ref(false);    // fully paused (camera released, model kept)
const isOperating = ref(false); // async guard for start/stop/standby buttons

watch(isDetecting, (newVal) => {
  systemStore.setDetecting(newVal);
});
const isStreaming = ref(false);
const fps = ref(0);
const latency = ref(0);
const cycleTime = ref(0);
const cycleTimeWithNg = ref(0);
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

// Double-buffered MJPEG stream: two <img> elements alternate to release
// Chromium's native decoder memory without any visible flicker.
const activeStream = ref(0);           // which img is currently visible (0 or 1)
const streamSrc0 = ref('');
const streamSrc1 = ref('');
let streamErrorCount = 0;

const videoElement = computed(() => activeStream.value === 0 ? streamImg0.value : streamImg1.value);

// ==================== Multi-Channel State ====================
const channelCount = ref(1);
const selectedChannel = ref(0);
const multiCanvasRefs = {};
const multiChannelData = ref({});
let multiPollingTimer = null;

const initMultiChannelData = (count) => {
  for (let i = 0; i < count; i++) {
    if (!multiChannelData.value[i]) {
      multiChannelData.value[i] = {
        isRunning: false, isDetecting: false, fps: 0, latency: 0,
        total: 0, ok: 0, ng: 0, avgCycleTime: 0, avgCycleTimeWithNg: 0, projectName: '',
        steps: [], detections: [], tracking: null,
        counters: {}, allCounters: [],
        stepCounts: {}, recentEvents: [],
        currentCycleSteps: [], backupCoveredLabels: [],
        ngStepRanking: [], yieldRate: 0,
        tableData: [],
        _ngStepCountMap: {},
        _processedEventIds: new Set(),
      };
    }
  }
};

const multiVideoCanvasRefs = {};
let multiStreamRunning = false;
const multiStreamAborts = {};

const STREAM_HOST = 'http://localhost:8001';
const BOUNDARY = '--frame';
const HEADER_END = '\r\n\r\n';

const startMultiStreams = (count) => {
  stopMultiStreams();
  multiStreamRunning = true;
  for (let ch = 0; ch < count; ch++) {
    connectMjpegStream(ch);
  }
};

const connectMjpegStream = async (ch) => {
  if (!multiStreamRunning) return;
  const abort = new AbortController();
  multiStreamAborts[ch] = abort;
  try {
    const res = await fetch(`${STREAM_HOST}/video_feed?channel=${ch}`, { signal: abort.signal });
    const reader = res.body.getReader();
    let buf = new Uint8Array(0);

    while (multiStreamRunning) {
      const { done, value } = await reader.read();
      if (done) break;

      const merged = new Uint8Array(buf.length + value.length);
      merged.set(buf);
      merged.set(value, buf.length);
      buf = merged;

      let startIdx = 0;
      while (true) {
        const boundaryIdx = findBytes(buf, BOUNDARY, startIdx);
        if (boundaryIdx === -1) break;
        const headerEndIdx = findBytes(buf, HEADER_END, boundaryIdx);
        if (headerEndIdx === -1) break;
        const jpegStart = headerEndIdx + HEADER_END.length;
        const nextBoundary = findBytes(buf, BOUNDARY, jpegStart);
        if (nextBoundary === -1) break;

        const jpegEnd = nextBoundary - 2;
        if (jpegEnd > jpegStart) {
          const jpegData = buf.slice(jpegStart, jpegEnd);
          drawFrameToCanvas(ch, jpegData);
        }
        startIdx = nextBoundary;
      }
      if (startIdx > 0) {
        buf = buf.slice(startIdx);
      }
      if (buf.length > 2 * 1024 * 1024) {
        buf = buf.slice(buf.length - 512 * 1024);
      }
    }
  } catch (e) {
    if (e.name !== 'AbortError' && multiStreamRunning) {
      console.warn(`[MJPEGStream] ch${ch} disconnected, reconnecting...`);
      setTimeout(() => connectMjpegStream(ch), 2000);
    }
  }
};

const findBytes = (buf, str, offset = 0) => {
  const target = typeof str === 'string' ? new TextEncoder().encode(str) : str;
  outer: for (let i = offset; i <= buf.length - target.length; i++) {
    for (let j = 0; j < target.length; j++) {
      if (buf[i + j] !== target[j]) continue outer;
    }
    return i;
  }
  return -1;
};

const drawFrameToCanvas = (ch, jpegData) => {
  const canvas = multiVideoCanvasRefs[ch];
  if (!canvas) return;
  const blob = new Blob([jpegData], { type: 'image/jpeg' });
  const url = URL.createObjectURL(blob);
  const img = new Image();
  img.onload = () => {
    const parent = canvas.parentElement;
    if (parent) {
      canvas.width = parent.clientWidth;
      canvas.height = parent.clientHeight;
    }
    const ctx = canvas.getContext('2d');
    const cw = canvas.width, ch2 = canvas.height;
    const scale = Math.min(cw / img.naturalWidth, ch2 / img.naturalHeight);
    const dw = img.naturalWidth * scale;
    const dh = img.naturalHeight * scale;
    const dx = (cw - dw) / 2;
    const dy = (ch2 - dh) / 2;
    ctx.fillStyle = '#000';
    ctx.fillRect(0, 0, cw, ch2);
    ctx.drawImage(img, dx, dy, dw, dh);
    URL.revokeObjectURL(url);
  };
  img.src = url;
};

const stopMultiStreams = () => {
  multiStreamRunning = false;
  Object.values(multiStreamAborts).forEach(a => { try { a.abort(); } catch {} });
  Object.keys(multiStreamAborts).forEach(k => delete multiStreamAborts[k]);
};

const startMultiPolling = () => {
  stopMultiPolling();
  multiPollingTimer = setInterval(async () => {
    for (let ch = 0; ch < channelCount.value; ch++) {
      try {
        const res = await getDetectionResults(ch);
        const d = res.data;
        const chData = multiChannelData.value[ch] || {};
        chData.isRunning = d.is_running;
        chData.isDetecting = d.is_detecting;
        chData.fps = d.fps || 0;
        chData.latency = d.latency || 0;
        const ctrs = d.counters || {};
        chData.total = ctrs['总产量'] ?? 0;
        chData.ok = ctrs['合格总数'] ?? 0;
        chData.ng = ctrs['不良总数'] ?? 0;
        chData.counters = ctrs;
        chData.avgCycleTime = d.average_cycle_time || 0;
        chData.avgCycleTimeWithNg = d.average_cycle_time_with_ng || 0;
        chData.detections = d.detections || [];
        chData.currentCycleSteps = d.current_cycle_steps || [];
        chData.backupCoveredLabels = d.backup_covered_labels || [];
        chData.stepCounts = d.step_counts || {};
        chData.recentEvents = d.recent_events || [];
        if (d.tracking) chData.tracking = d.tracking;

        const total = chData.total || 0;
        const ok = chData.ok || 0;
        chData.yieldRate = total > 0 ? Math.round((ok / total) * 100) : 0;

        const chTotalCycles = ctrs['总产量'] || ctrs['total'] || 0;
        const backendNgMap = d.ng_step_cycle_counts || {};
        if (Object.keys(backendNgMap).length > 0) {
          const ranking = Object.entries(backendNgMap)
            .filter(([, c]) => c > 0)
            .map(([step, ngCount]) => ({ step, count: ngCount, rate: chTotalCycles > 0 ? (ngCount / chTotalCycles * 100) : 0 }))
            .sort((a, b) => b.rate - a.rate);
          chData.ngStepRanking = ranking.slice(0, 3);
        } else {
          chData.ngStepRanking = [];
        }

        const allC = [];
        for (const [name, value] of Object.entries(ctrs)) {
          if (!name.startsWith('_')) allC.push({ name, value });
        }
        chData.allCounters = allC;

        if (d.detections) {
          const stepsConf = currentProject.value?.steps_config || [];
          const stMap = {};
          stepsConf.forEach(s => { stMap[s.label] = s; });
          const td = stepsConf.filter(s => s.enabled !== false && !s.is_backup).map((s, i) => {
            const inCycle = chData.currentCycleSteps.includes(s.label);
            const coveredByBackup = chData.backupCoveredLabels.includes(s.label);
            return {
              step: s.displayLabel || s.label,
              label: s.label,
              status: inCycle || coveredByBackup ? 'completed' : 'pending',
              cycleResult: null,
            };
          });
          chData.tableData = td;
        }

        if (d.detections) {
          const stepsConf = currentProject.value?.steps_config || [];
          const sopSteps = stepsConf.filter(s => s.enabled !== false && !s.is_backup).map(s => {
            const inCycle = chData.currentCycleSteps.includes(s.label);
            const coveredByBackup = chData.backupCoveredLabels.includes(s.label);
            return {
              name: s.displayLabel || s.label,
              label: s.label,
              status: inCycle || coveredByBackup ? 'completed' : 'pending',
            };
          });
          chData.steps = sopSteps;
        }

        multiChannelData.value[ch] = { ...chData };

        const canvas = multiCanvasRefs[ch];
        if (canvas && d.detections?.length) {
          drawMultiDetections(canvas, d.detections);
        } else if (canvas) {
          const ctx = canvas.getContext('2d');
          ctx.clearRect(0, 0, canvas.width, canvas.height);
        }
      } catch (e) { /* silent */ }
    }
    updateGlobalDetectingState();
    updateMultiCharts();
  }, 200);
};

const stopMultiPolling = () => {
  if (multiPollingTimer) { clearInterval(multiPollingTimer); multiPollingTimer = null; }
};

const updateMultiCharts = () => {
  // Data-driven rendering via template — no separate ECharts needed for multi-view
};

const drawMultiDetections = (canvas, detections) => {
  if (!canvas) return;
  const parent = canvas.parentElement;
  if (parent) { canvas.width = parent.offsetWidth; canvas.height = parent.offsetHeight; }
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const cw = canvas.width, ch2 = canvas.height;
  detections.forEach(det => {
    if (det.hidden) return;
    const x = det.x * cw, y = det.y * ch2;
    const w = det.w * cw, h = det.h * ch2;
    const color = det.is_ng ? '#ef4444' : '#10b981';
    if (det.mask && Array.isArray(det.mask) && det.mask.length > 2) {
      ctx.beginPath();
      det.mask.forEach((pt, i) => {
        const mx = pt[0] * cw, my = pt[1] * ch2;
        if (i === 0) ctx.moveTo(mx, my); else ctx.lineTo(mx, my);
      });
      ctx.closePath();
      ctx.fillStyle = color + '40';
      ctx.fill();
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      ctx.stroke();
    } else {
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      ctx.strokeRect(x, y, w, h);
    }
    ctx.font = 'bold 11px Arial';
    const label = det.display_id || det.display_name || det.label || '';
    ctx.fillStyle = color;
    ctx.fillRect(x, y - 14, ctx.measureText(label).width + 6, 14);
    ctx.fillStyle = 'white';
    ctx.fillText(label, x + 3, y - 2);
  });
};

const startDetectionForChannel = async (ch) => {
  if (!currentProject.value) return;
  try {
    await syncProjectConfig();
    await setProjectConfig({
      project_id: currentProject.value.id,
      name: currentProject.value.name,
      task_type: currentProject.value.task_type || 'detection',
      logic_mode: currentProject.value.logic_mode || 'detection',
      steps_config: currentProject.value.steps_config || [],
      pipeline_config: currentProject.value.pipeline_config || {},
      events_config: currentProject.value.events_config || [],
      counters_config: currentProject.value.counters_config || []
    }, ch);
    const modelId = currentProject.value.default_model_id;
    if (!modelId) { ElMessage.warning('请先配置模型'); return; }
    const modelRes = await getModelDetail(modelId);
    await apiStartDetection(modelRes.data.file_path, 0.25, 0.45, ch);
    ElMessage.success(`工位 ${ch + 1} 检测已启动`);
  } catch (e) {
    ElMessage.error(`工位 ${ch + 1} 启动失败: ${e.message}`);
  }
};

const updateGlobalDetectingState = () => {
  const anyDetecting = Object.values(multiChannelData.value).some(d => d?.isDetecting);
  systemStore.setDetecting(anyDetecting);
  projectStore.setRunningStatus(anyDetecting);
};

const stopDetectionForChannel = async (ch) => {
  try {
    await pauseDetection(ch);
    if (multiChannelData.value[ch]) multiChannelData.value[ch].isDetecting = false;
    updateGlobalDetectingState();
    ElMessage.info(`工位 ${ch + 1} 已停止`);
  } catch (e) {
    ElMessage.error(`工位 ${ch + 1} 停止失败`);
  }
};

const standbyForChannel = async (ch) => {
  try {
    await standbyDetection(ch);
    if (multiChannelData.value[ch]) multiChannelData.value[ch].isDetecting = false;
    updateGlobalDetectingState();
    ElMessage.info(`工位 ${ch + 1} 已待机`);
  } catch (e) {
    ElMessage.error(`工位 ${ch + 1} 待机失败`);
  }
};

const fetchChannelCount = async () => {
  try {
    const res = await getWorkstations();
    const count = res.data.channel_count || 1;
    channelCount.value = count;
    if (count > 1) {
      initMultiChannelData(count);
      startMultiStreams(count);
      startMultiPolling();
    }
  } catch (e) {
    channelCount.value = 1;
  }
};
// ==================== End Multi-Channel ====================

const buildStreamUrl = () => `${getBackendHost()}/video_feed?t=${Date.now()}`;

const connectStream = () => {
  streamSrc0.value = buildStreamUrl();
  activeStream.value = 0;
  streamSrc1.value = '';
  streamErrorCount = 0;
};

const disconnectStream = () => {
  streamSrc0.value = '';
  streamSrc1.value = '';
};

const swapStream = () => {
  const bg = activeStream.value === 0 ? 1 : 0;
  const bgSrcRef = bg === 0 ? streamSrc0 : streamSrc1;
  bgSrcRef.value = buildStreamUrl();
  // onStreamReady(bg) will do the actual swap when the first frame arrives
};

// Kept as alias so existing call-sites (start/stop/resume) still work
const forceReconnectStream = () => connectStream();

const onStreamReady = (idx) => {
  streamErrorCount = 0;
  isStreaming.value = true;
  resizeCanvas();
  if (idx !== activeStream.value) {
    const oldIdx = activeStream.value;
    activeStream.value = idx;
    // Release the old img's decoder memory
    if (oldIdx === 0) streamSrc0.value = '';
    else streamSrc1.value = '';
  }
};

const onStreamError = (idx) => {
  if (idx !== activeStream.value) return; // ignore errors on background img
  streamErrorCount++;
  if (streamErrorCount > 10) return;
  setTimeout(() => connectStream(), 500 * Math.min(streamErrorCount, 5));
};

// 当前项目
const currentProject = computed(() => projectStore.currentProject);

const isTrackingMode = computed(() => currentProject.value?.logic_mode === 'tracking');
const trackingChecklist = ref({});
const trackingCycleActive = ref(false);
const serverModelTask = ref('detect');

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

// 三个内置计数器（检测次数、OK次数、NG次数）并排
const BUILTIN_THREE = ['总产量', '合格总数', '不良总数'];
const builtinCounters = computed(() => {
  return counters.value.filter(c => BUILTIN_THREE.includes(c.name));
});

// 其余计数器（NG步骤 + 自定义）
const extraCounters = computed(() => {
  return counters.value.filter(c => !BUILTIN_THREE.includes(c.name));
});

// 显示名称映射
const getCounterDisplayName = (name) => {
  switch (name) {
    case '总产量': return '检测次数';
    case '合格总数': return 'OK次数';
    case '不良总数': return 'NG次数';
    default: return name;
  }
};

// 根据计数器名称返回颜色类
const getCounterColor = (name) => {
  switch (name) {
    case '总产量': return 'text-white';
    case '合格总数': return 'text-green-400';
    case '不良总数': return 'text-red-400';
    case 'NG步骤': return 'text-orange-500';
    default: return 'text-white';
  }
};

// NG 步骤排名数据
const ngStepRanking = ref([]);
const ngStepCountMap = ref({});

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
    case 'top-left': return 'top-24 left-4';
    case 'bottom-right': return 'bottom-8 right-8';
    case 'bottom-left': return 'bottom-8 left-4';
    case 'center': return 'top-24 left-[29%] -translate-x-1/2';
    default: return 'top-24 right-8';
  }
};

// 默认位置（兼容旧代码）
const toastPositionClass = computed(() => {
  return getPositionClass(systemStore.detection.toasts.ok.position);
});

// TTS voice announcement
// Chromium bug: cancel() immediately followed by speak() silently drops the utterance.
// Workaround: delay speak() by ~100ms after cancel().
let speakTimer = null;
const speak = (text) => {
  if (!systemStore.detection.voiceEnabled || !window.speechSynthesis) return;
  if (speakTimer) clearTimeout(speakTimer);
  window.speechSynthesis.cancel();
  speakTimer = setTimeout(() => {
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = 'zh-CN';
    utterance.volume = systemStore.detection.voiceVolume ?? 1.0;
    utterance.rate = 1.1;
    window.speechSynthesis.speak(utterance);
  }, 100);
};

// 显示提示框（新版：根据 toast_id 获取配置）
const showToastById = (toastId, eventName, reason = '') => {
  const config = getToastConfig(toastId);
  
  const icons = {
    ok: CircleCheck,
    ng: CircleClose,
    custom: Warning
  };
  
  let subtitle = config.subText || '';
  if (toastId === 'ng' && reason && systemStore.detection.showNgReason) {
    subtitle = reason;
  }
  
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
  
  // Voice announcement
  if (toastId === 'ok') {
    speak('合格');
  } else if (toastId === 'ng') {
    const showReason = systemStore.detection.showNgReason && reason;
    speak(showReason ? `不合格，${reason}` : '不合格');
  } else {
    speak(config.text || eventName || '事件触发');
  }
  
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
    subtitle: config.subText || '',
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

// 格式化步骤耗时 (average PT)
const formatDuration = (stepLabel) => {
  const duration = avgStepDurations.value[stepLabel];
  if (duration === undefined || duration === null) return '--';
  return `${duration.toFixed(1)}s`;
};

// 格式化当前周期内步骤检测时间（PT） - uses average
const formatStepPT = (stepLabel) => {
  const duration = avgStepDurations.value[stepLabel];
  if (duration === undefined || duration === null) return '--';
  return `${duration.toFixed(1)}s`;
};

// 格式化步骤间隔时间
const formatInterval = (stepLabel) => {
  const interval = stepIntervals.value[stepLabel];
  if (interval === undefined || interval === null || interval === 0) return '--';
  return `${interval.toFixed(1)}s`;
};

const getDisplayCT = (chData) => {
  if (!chData) return '--';
  const includeNg = systemStore.display?.monitor?.ctIncludeNg;
  const val = includeNg ? chData.avgCycleTimeWithNg : chData.avgCycleTime;
  return val ? val.toFixed(1) + 's' : '--';
};

const displayCT = computed(() => {
  const includeNg = systemStore.display?.monitor?.ctIncludeNg;
  const val = includeNg ? cycleTimeWithNg.value : cycleTime.value;
  return val || 0;
});

// 获取步骤样式（步骤设置区域用）
const getStepClass = (step) => {
  if (step.status === 'active') {
    return 'border-cyan-500 shadow-[0_0_10px_rgba(6,182,212,0.3)]';
  } else if (step.status === 'completed') {
    return 'border-green-500 bg-green-900/20';
  } else {
    return 'border-slate-700 opacity-60';
  }
};

// SOP卡片整体样式：完成=绿色，漏检/重复=红色
const getSopCardClass = (step) => {
  if (step.cycleResult === 'ng') {
    return 'border-red-500 bg-red-900/30';
  } else if (step.status === 'completed' || step.cycleResult === 'ok') {
    return 'border-green-500 bg-green-900/20';
  } else if (step.status === 'active') {
    return 'border-cyan-500 shadow-[0_0_10px_rgba(6,182,212,0.3)] bg-slate-800';
  } else {
    return 'border-slate-700 bg-slate-800 opacity-60';
  }
};

const getSopHeaderClass = (step) => {
  if (step.cycleResult === 'ng') {
    return 'bg-red-900/60 text-red-200';
  } else if (step.status === 'completed' || step.cycleResult === 'ok') {
    return 'bg-green-900/60 text-green-200';
  } else {
    return 'bg-slate-950 text-gray-300';
  }
};

const getSopBodyClass = (step) => {
  if (step.cycleResult === 'ng') {
    return 'bg-red-950/30';
  } else if (step.status === 'completed' || step.cycleResult === 'ok') {
    return 'bg-green-950/20';
  } else {
    return 'bg-black/20';
  }
};

// SOP 卡片自动滚动：将正在变化的卡片滚动到可视区域中间
const scrollToSopCard = (idx) => {
  if (idx === lastScrolledIdx || idx < 0) return;
  const container = sopScrollContainer.value;
  const card = sopCardRefs[idx];
  if (!container || !card) return;
  
  const containerWidth = container.clientWidth;
  const cardLeft = card.offsetLeft;
  const cardWidth = card.offsetWidth;
  const targetScroll = cardLeft - (containerWidth / 2) + (cardWidth / 2);
  
  container.scrollTo({ left: Math.max(0, targetScroll), behavior: 'smooth' });
  lastScrolledIdx = idx;
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
    
    // 强制重连视频流
    setTimeout(() => {
      forceReconnectStream();
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
  
  // 获取启用的步骤标签列表
  const stepsConfig = currentProject.value?.steps_config || [];
  const enabledLabels = new Set(
    stepsConfig
      .filter(s => s.enabled !== false)  // 默认启用
      .map(s => s.label)
  );
  
  const boxColor = systemStore.detection.boxColor;
  const ngColor = systemStore.detection.boxColorNG;
  const lineWidth = systemStore.detection.boxLineWidth;
  const fontSize = systemStore.detection.labelFontSize;
  const showConf = systemStore.detection.showConfidence;
  
  // object-contain offset: compute actual rendered image area within canvas
  const img = videoElement.value;
  let offsetX = 0, offsetY = 0, renderW = canvas.width, renderH = canvas.height;
  if (img && img.naturalWidth && img.naturalHeight) {
    const imgAspect = img.naturalWidth / img.naturalHeight;
    const canvasAspect = canvas.width / canvas.height;
    if (imgAspect > canvasAspect) {
      renderW = canvas.width;
      renderH = canvas.width / imgAspect;
      offsetY = (canvas.height - renderH) / 2;
    } else {
      renderH = canvas.height;
      renderW = canvas.height * imgAspect;
      offsetX = (canvas.width - renderW) / 2;
    }
  }

  detections.forEach(det => {
    if (!enabledLabels.has(det.label)) return;
    if (det.hidden) return;
    const x = offsetX + det.x * renderW;
    const y = offsetY + det.y * renderH;
    const w = det.w * renderW;
    const h = det.h * renderH;
    
    const color = det.is_ng ? ngColor : boxColor;
    
    // Render polygon mask if available (segmentation model)
    if (det.mask && Array.isArray(det.mask) && det.mask.length > 2) {
      ctx.beginPath();
      det.mask.forEach((pt, i) => {
        const mx = offsetX + pt[0] * renderW;
        const my = offsetY + pt[1] * renderH;
        if (i === 0) ctx.moveTo(mx, my);
        else ctx.lineTo(mx, my);
      });
      ctx.closePath();
      ctx.fillStyle = color.replace(')', ', 0.25)').replace('rgb(', 'rgba(');
      ctx.fill();
      ctx.strokeStyle = color;
      ctx.lineWidth = lineWidth;
      ctx.stroke();
    } else {
      ctx.strokeStyle = color;
      ctx.lineWidth = lineWidth;
      ctx.strokeRect(x, y, w, h);
    }
    
    // In tracking mode, show display_id (A1, B2, etc.) as the label
    let label = det.display_id || det.display_name || det.label || 'Unknown';
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

  // Draw ROI polygon overlay if configured (tracking mode)
  const roiPoly = currentProject.value?.pipeline_config?.tracking_roi?.polygon;
  if (roiPoly && roiPoly.length >= 3) {
    ctx.save();
    ctx.strokeStyle = 'rgba(0, 200, 255, 0.6)';
    ctx.lineWidth = 2;
    ctx.setLineDash([8, 4]);
    ctx.beginPath();
    ctx.moveTo(offsetX + roiPoly[0][0] * renderW, offsetY + roiPoly[0][1] * renderH);
    for (let i = 1; i < roiPoly.length; i++) {
      ctx.lineTo(offsetX + roiPoly[i][0] * renderW, offsetY + roiPoly[i][1] * renderH);
    }
    ctx.closePath();
    ctx.stroke();
    ctx.fillStyle = 'rgba(0, 200, 255, 0.05)';
    ctx.fill();
    ctx.setLineDash([]);
    ctx.restore();
  }
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
  
  stepsToShow = stepsToShow.filter(s => !s.backup_for);

  // 更新步骤条 - 同时保存 label 用于后端匹配
  steps.value = stepsToShow.map((s, idx) => ({
    id: s.id,
    name: s.displayLabel || s.label,
    label: s.label,
    status: 'pending',
    result: null,
    cycleResult: null,
    screenshot: null
  }));

  // 更新表格数据
  tableData.value = stepsToShow.map(s => ({
    step: s.displayLabel || s.label,
    label: s.label,
    count: 0,
    status: 'pending',
    cycleResult: null
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
        fontSize: 24,
        fontWeight: 'bold',
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

// Helper: build and send latest project config to backend
const syncProjectConfig = async () => {
  const proj = currentProject.value;
  const pipelineCfg = {
    ...(proj.pipeline_config || {}),
    sequence_order: proj.sequence_order || proj.pipeline_config?.sequence_order || [],
    detection_steps: proj.detection_steps || proj.pipeline_config?.detection_steps || [],
    custom_conditions: proj.custom_conditions || proj.pipeline_config?.custom_conditions || [],
    custom_based_on: proj.custom_based_on || proj.pipeline_config?.custom_based_on || 'sequential'
  };
  await setProjectConfig({
    project_id: proj.id,
    name: proj.name,
    task_type: proj.task_type || 'detection',
    logic_mode: proj.logic_mode || 'detection',
    steps_config: proj.steps_config || [],
    pipeline_config: pipelineCfg,
    events_config: proj.events_config || [],
    counters_config: proj.counters_config || []
  });
};

const startDetection = async () => {
  if (!currentProject.value) {
    ElMessage.warning('请先选择项目');
    return;
  }
  if (isOperating.value) return;
  isOperating.value = true;
  
  try {
    await syncProjectConfig();

    // From standby: video stream still running + model loaded → just resume inference
    if (isRunning.value && !isDetecting.value) {
      try {
        await resumeInference();
        isDetecting.value = true;
        projectStore.setRunningStatus(true);
        ElMessage.success('已从待机恢复检测');
        startPolling();
        return;
      } catch (err) {
        console.warn('待机恢复失败，回退到完整启动:', err);
        // Model not loaded or other issue — fall through to full start
      }
    }

    // From paused: camera released, need full resume
    if (isPaused.value) {
      try {
        await resumeDetection();
        isPaused.value = false;
        isRunning.value = true;
        isDetecting.value = true;
        projectStore.setRunningStatus(true);
        forceReconnectStream();
        ElMessage.success('检测已恢复');
        startPolling();
        return;
      } catch (err) {
        console.warn('恢复失败，回退到完整启动:', err);
        isPaused.value = false;
      }
    }
    
    // Full start: load model, start capture + inference
    const modelId = currentProject.value.default_model_id;
    if (!modelId) {
      ElMessage.warning('请先在项目管理中配置模型');
      return;
    }
    
    const modelRes = await getModelDetail(modelId);
    const modelPath = modelRes.data.file_path;
    if (!modelPath) {
      ElMessage.error('模型文件路径无效');
      return;
    }
    
    await apiStartDetection(modelPath, 0.25, 0.45);
    isRunning.value = true;
    isDetecting.value = true;
    isPaused.value = false;
    projectStore.setRunningStatus(true);
    forceReconnectStream();
    ElMessage.success('检测已开始');
    startPolling();
    
  } catch (err) {
    console.error('启动检测失败:', err);
    ElMessage.error('启动检测失败: ' + (err.response?.data?.detail || err.message));
  } finally {
    isOperating.value = false;
  }
};

const stopDetectionHandler = async () => {
  if (isOperating.value) return;
  isOperating.value = true;
  try {
    await pauseDetection();
    isRunning.value = false;
    isDetecting.value = false;
    isPaused.value = true;
    projectStore.setRunningStatus(false);
    stopPolling();
    disconnectStream();
    ElMessage.info('已停止：画面和检测都已暂停');
  } catch (err) {
    console.error('停止检测失败:', err);
    ElMessage.error('停止失败: ' + (err.response?.data?.detail || err.message));
  } finally {
    isOperating.value = false;
  }
};

// 步骤截图
const stepScreenshots = ref({});

// 步骤检测时间
const stepDetectionTimes = ref({});

// 步骤耗时
const stepDurations = ref({});

// 步骤平均耗时 (average PT)
const avgStepDurations = ref({});

// 步骤间隔时间
const stepIntervals = ref({});

// Periodic double-buffer swap to release Chromium native decoder memory
let streamSwapCounter = 0;
const STREAM_SWAP_INTERVAL = 600; // every ~5 min (600 x 500ms polling)
let lastChartUpdate = 0;
const CHART_UPDATE_INTERVAL = 2000; // 图表最多每2秒更新一次
let lastScreenshotUpdate = 0;
const SCREENSHOT_UPDATE_INTERVAL = 1000;
let pollingInProgress = false; // 防止轮询重叠

// 开始轮询
const startPolling = () => {
  stopPolling();
  shownEventIds.value.clear();
  streamSwapCounter = 0;
  pollingInProgress = false;
  
  pollingTimer = setInterval(async () => {
    if (pollingInProgress) return;
    pollingInProgress = true;
    
    // Periodic double-buffer swap (memory release)
    streamSwapCounter++;
    if (streamSwapCounter >= STREAM_SWAP_INTERVAL) {
      streamSwapCounter = 0;
      swapStream();
    }
    try {
      const res = await getDetectionResults();
      const data = res.data;
      
      if (data.source_type) {
        sourceStore.setSourceType(data.source_type);
      }
      
      fps.value = data.fps || 0;
      latency.value = data.latency || 0;
      detectionCount.value = (data.detections || []).length;
      cycleTime.value = data.average_cycle_time || 0;
      cycleTimeWithNg.value = data.average_cycle_time_with_ng || 0;
      
      const now = Date.now();
      
      // 节流截图更新（合并而非替换，保留旧截图）
      if (data.step_screenshots && (now - lastScreenshotUpdate >= SCREENSHOT_UPDATE_INTERVAL)) {
        Object.assign(stepScreenshots.value, data.step_screenshots);
        lastScreenshotUpdate = now;
      }
      
      if (data.step_detection_times) {
        Object.assign(stepDetectionTimes.value, data.step_detection_times);
      }
      if (data.step_durations) {
        Object.assign(stepDurations.value, data.step_durations);
      }
      if (data.avg_step_durations) {
        Object.assign(avgStepDurations.value, data.avg_step_durations);
      }
      if (data.step_intervals) {
        Object.assign(stepIntervals.value, data.step_intervals);
      }
      
      if (isRunning.value) {
        const shouldUpdateCharts = (now - lastChartUpdate >= CHART_UPDATE_INTERVAL);
        const countersWithCycle = data.counters || {};
        countersWithCycle._currentCycleSteps = data.current_cycle_steps || [];
        countersWithCycle._backupCoveredLabels = data.backup_covered_labels || [];
        countersWithCycle._ngStepCycleCounts = data.ng_step_cycle_counts || {};
        updateStepsFromBackend(
          data.step_counts || {}, 
          data.detections || [],
          countersWithCycle,
          data.recent_events || [],
          shouldUpdateCharts
        );
        if (shouldUpdateCharts) {
          lastChartUpdate = now;
        }
      }
      
      if (data.detections) {
        drawDetections(data.detections);
      }
      
      if (data.model_task) {
        serverModelTask.value = data.model_task;
      }
      if (data.tracking) {
        trackingChecklist.value = data.tracking.item_checklist || {};
        trackingCycleActive.value = data.tracking.cycle_active || false;
      }
      
      if (isVideoSource.value && !isDraggingProgress.value) {
        try {
          const videoRes = await api.get('/source/video/info');
          if (videoRes.data.status === 'success') {
            videoInfo.value.progress = videoRes.data.progress || 0;
            videoInfo.value.currentTime = videoRes.data.current_time || 0;
            videoInfo.value.duration = videoRes.data.duration || 0;
            if (!isChangingSpeed.value) {
              videoInfo.value.speed = videoRes.data.speed || 1;
            }
            videoInfo.value.syncMode = videoRes.data.sync_mode || false;
            videoInfo.value.ended = videoRes.data.ended || false;
            
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
    } finally {
      pollingInProgress = false;
    }
  }, 150); // ~6.7Hz polling for responsive detection box overlay
};

// 已显示的事件ID（避免重复显示提示框）
const shownEventIds = ref(new Set());

// 缓存上一次截图 base64，避免重复创建 data URL
const cachedScreenshotUrls = {};

// 上一次的总产量，用于检测周期变化
let lastTotalCount = -1;
// 已处理的事件ID（防止NG排名重复计数）
const processedEventIds = new Set();

// 从后端数据更新步骤状态
const updateStepsFromBackend = (stepCounts, currentDetections, backendCounters, recentEvents, shouldUpdateCharts = true) => {
  const detectingLabels = new Set(currentDetections.map(d => d.label));
  
  // 获取后端的当前周期步骤列表
  const currentCycleSteps = backendCounters?._currentCycleSteps || [];
  const backupCoveredLabels = new Set(backendCounters?._backupCoveredLabels || []);
  
  // Inject default_pt for backup-covered steps that have no real duration
  if (backupCoveredLabels.size > 0) {
    const stepsConfig = currentProject.value?.steps_config || [];
    backupCoveredLabels.forEach(label => {
      if (stepDurations.value[label] === undefined || stepDurations.value[label] === null) {
        const cfg = stepsConfig.find(s => s.label === label);
        if (cfg?.default_pt) {
          stepDurations.value[label] = cfg.default_pt;
          if (avgStepDurations.value[label] === undefined) {
            avgStepDurations.value[label] = cfg.default_pt;
          }
        }
      }
    });
  }
  
  // 检测周期是否刚结束（总产量变化时表示新周期产生）
  const currentTotal = backendCounters?.['总产量'] ?? -1;
  if (lastTotalCount >= 0 && currentTotal > lastTotalCount) {
    // 新周期产生，延迟后重置视觉状态，让用户看到上一轮的颜色反馈
    setTimeout(() => {
      steps.value.forEach(s => {
        s.status = 'pending';
        s.cycleResult = null;
      });
      tableData.value.forEach(t => {
        t.status = 'pending';
        t.cycleResult = null;
      });
      lastScrolledIdx = -1;
    }, 1200);
  }
  lastTotalCount = currentTotal;
  
  // ── 实时周期反馈算法 ──
  // 在周期进行中，实时对比 currentCycleSteps 和预期顺序，动态标记每个步骤颜色
  const expectedLabels = steps.value.map(s => s.label || s.name);
  
  if (currentCycleSteps.length > 0 && expectedLabels.length > 0) {
    // 统计每个步骤在本周期中出现的次数
    const countInCycle = {};
    currentCycleSteps.forEach(l => { countInCycle[l] = (countInCycle[l] || 0) + 1; });
    
    // 每个步骤在 currentCycleSteps 中的首次出现位置
    const firstPos = {};
    currentCycleSteps.forEach((l, i) => { if (!(l in firstPos)) firstPos[l] = i; });
    
    // 已检测到的步骤中，最大的预期位置索引
    let maxDetectedExpectedIdx = -1;
    expectedLabels.forEach((label, idx) => {
      if (label in firstPos) {
        maxDetectedExpectedIdx = Math.max(maxDetectedExpectedIdx, idx);
      }
    });
    
    // 检测乱序：按预期顺序遍历，若某步骤的实际位置 < 前面某步骤的实际位置 → 乱序
    const outOfOrder = new Set();
    let maxActualPos = -1;
    for (const label of expectedLabels) {
      if (label in firstPos) {
        if (firstPos[label] < maxActualPos) {
          outOfOrder.add(label);
        }
        maxActualPos = Math.max(maxActualPos, firstPos[label]);
      }
    }
    
    // 设置每个步骤的 cycleResult
    steps.value.forEach((step, idx) => {
      const label = step.label || step.name;
      const cycleCount = countInCycle[label] || 0;
      
      const isCoveredByBackup = backupCoveredLabels.has(label);
      
      if (cycleCount > 1) {
        step.cycleResult = 'ng';  // 重复
      } else if (label in firstPos) {
        step.cycleResult = outOfOrder.has(label) ? 'ng' : 'ok';  // 检测到：看是否乱序
      } else if (isCoveredByBackup) {
        step.cycleResult = 'ok';  // 替补覆盖：视为已完成
      } else if (idx <= maxDetectedExpectedIdx) {
        step.cycleResult = 'ng';  // 漏做（后面的步骤已出现但此步骤未出现）
      } else {
        step.cycleResult = null;  // 还没轮到
      }
      
      // 设置 status
      if (detectingLabels.has(label)) {
        step.status = 'active';
      } else if (cycleCount > 0 || isCoveredByBackup) {
        step.status = 'completed';
      } else {
        step.status = 'pending';
      }
      
      // 同步表格状态
      if (tableData.value[idx]) {
        tableData.value[idx].count = stepCounts[label] || 0;
        tableData.value[idx].status = (cycleCount > 0 || isCoveredByBackup) ? 'completed' : 'pending';
        tableData.value[idx].cycleResult = step.cycleResult;
      }
    });
    
    // 自动滚动到最新变化的卡片
    let latestChangedIdx = -1;
    for (let i = steps.value.length - 1; i >= 0; i--) {
      if (steps.value[i].cycleResult === 'ok' || steps.value[i].cycleResult === 'ng') {
        latestChangedIdx = i;
        break;
      }
    }
    if (latestChangedIdx >= 0) {
      scrollToSopCard(latestChangedIdx);
    }
  } else {
    // 周期未开始或已结束：不改变 cycleResult（保留上一轮的视觉反馈直到 reset）
    steps.value.forEach((step, idx) => {
      const label = step.label || step.name;
      const count = stepCounts[label] || 0;
      
      if (detectingLabels.has(label)) {
        step.status = 'active';
      } else if (!step.cycleResult && count > 0 && currentCycleSteps.length === 0) {
        step.status = 'completed';
      }
      
      if (tableData.value[idx]) {
        tableData.value[idx].count = count;
      }
    });
  }
  
  // 更新截图
  steps.value.forEach((step) => {
    const stepLabel = step.label || step.name;
    const rawB64 = stepScreenshots.value[stepLabel];
    if (rawB64 && cachedScreenshotUrls[stepLabel] !== rawB64) {
      cachedScreenshotUrls[stepLabel] = rawB64;
      step.screenshot = `data:image/jpeg;base64,${rawB64}`;
    }
  });
  
  // NG TOP3: use authoritative counts from backend (survives page reload)
  {
    const totalCycles = backendCounters?.['总产量'] || backendCounters?.['total'] || 0;
    const backendNgMap = backendCounters?._ngStepCycleCounts || {};
    ngStepRanking.value = Object.entries(backendNgMap)
      .filter(([, ngCount]) => ngCount > 0)
      .map(([step, ngCount]) => {
        const rate = totalCycles > 0 ? (ngCount / totalCycles * 100) : 0;
        return { step, count: ngCount, total: totalCycles, rate };
      })
      .sort((a, b) => b.rate - a.rate);
  }
  
  if (backendCounters && currentProject.value?.counters_config) {
    currentProject.value.counters_config.forEach(counter => {
      if (backendCounters[counter.name] !== undefined) {
        counter.value = backendCounters[counter.name];
      }
    });
  }
  
  if (recentEvents && recentEvents.length > 0) {
    recentEvents.forEach(event => {
      const eventKey = `${event.event_id}_${Math.floor(event.timestamp)}`;
      if (!shownEventIds.value.has(eventKey) && event.show_notification) {
        shownEventIds.value.add(eventKey);
        const toastId = event.toast_id || (event.event_id === 1 ? 'ok' : event.event_id === 2 ? 'ng' : 'ok');
        showToastById(toastId, event.event_name, event.reason);
      }
    });
    
    // 防止 shownEventIds 无限增长
    if (shownEventIds.value.size > 500) {
      const arr = [...shownEventIds.value];
      shownEventIds.value = new Set(arr.slice(-200));
    }
  }
  
  if (shouldUpdateCharts) {
    updateCharts();
  }
};

// 停止轮询
const stopPolling = () => {
  if (pollingTimer) {
    clearInterval(pollingTimer);
    pollingTimer = null;
  }
};

const standbyHandler = async () => {
  if (isOperating.value) return;
  isOperating.value = true;
  try {
    await standbyDetection();
    isDetecting.value = false;
    // isRunning stays true — video stream keeps playing
    steps.value.forEach(s => {
      s.status = 'pending';
      s.result = null;
    });
    if (detectionCanvas.value) {
      const ctx = detectionCanvas.value.getContext('2d');
      ctx.clearRect(0, 0, detectionCanvas.value.width, detectionCanvas.value.height);
    }
    if (!pollingTimer) {
      startPolling();
    }
    ElMessage.info('已待机：检测停止，画面继续');
  } catch (err) {
    console.error('待机失败:', err);
    ElMessage.error('待机失败: ' + (err.response?.data?.detail || err.message));
  } finally {
    isOperating.value = false;
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
    s.cycleResult = null;
    s.screenshot = null;
  });
  
  tableData.value.forEach(t => {
    t.count = 0;
    t.status = 'pending';
    t.cycleResult = null;
  });
  lastScrolledIdx = -1;
  
  // 清空步骤截图缓存、PT/间隔缓存和NG排名
  stepScreenshots.value = {};
  stepDurations.value = {};
  avgStepDurations.value = {};
  stepIntervals.value = {};
  stepDetectionTimes.value = {};
  Object.keys(cachedScreenshotUrls).forEach(k => delete cachedScreenshotUrls[k]);
  ngStepRanking.value = [];
  ngStepCountMap.value = {};
  lastTotalCount = -1;
  processedEventIds.clear();
  
  trackingChecklist.value = {};
  trackingCycleActive.value = false;
  
  updateCharts();
  ElMessage.success('计数器已清零');
};


// processDetections 已移除：步骤计数完全由后端 step_counts 驱动，
// 前端不再重复累加，避免数据不一致和内存浪费

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

// 自动恢复输入源（返回是否成功恢复）
const autoRestoreSource = async () => {
  sourceStore.loadConfig();
  
  if (!localStorage.getItem('source_config')) return false;
  
  const savedType = sourceStore.sourceType;
  let restored = false;

  if (!savedType || savedType === 'camera') {
    try {
      const cameraSettings = sourceStore.cameraSettings;
      const [w, h] = (cameraSettings.resolution || '1280x720').split('x').map(Number);
      await api.post('/source/camera/start', {
        device_index: cameraSettings.deviceIndex || 0,
        width: w,
        height: h,
        fps: cameraSettings.fps || 60
      });
      sourceStore.setSourceType('camera');
      restored = true;
      console.log('[AutoRestore] 已自动恢复摄像头');
    } catch (err) {
      console.warn('[AutoRestore] 自动恢复摄像头失败:', err);
    }
  } else if (savedType === 'hikvision') {
    try {
      const hikSettings = sourceStore.hikvisionSettings;
      const [w, h] = (hikSettings.resolution || '1280x720').split('x').map(Number);
      await api.post('/source/hikvision/start', {
        device_index: hikSettings.deviceIndex || 0,
        width: w,
        height: h,
        fps: hikSettings.fps || 60
      });
      sourceStore.setSourceType('hikvision');
      restored = true;
      console.log('[AutoRestore] 已自动恢复海康相机');
    } catch (err) {
      console.warn('[AutoRestore] 自动恢复海康相机失败:', err);
    }
  } else if (savedType === 'video' && sourceStore.videoPath) {
    try {
      await api.post('/source/video/start', {
        file_path: sourceStore.videoPath,
        speed: sourceStore.videoSpeed || 1
      });
      sourceStore.setSourceType('video');
      restored = true;
      console.log('[AutoRestore] 已自动恢复视频');
    } catch (err) {
      console.warn('[AutoRestore] 自动恢复视频失败:', err);
    }
  }

  if (restored) {
    sourceStore.setStreaming(true);
    isStreaming.value = true;
    isRunning.value = true;
    projectStore.setRunningStatus(true);
    forceReconnectStream();
    startPolling();
  }

  return restored;
};

onMounted(() => {
  systemStore.loadSettings();
  fetchChannelCount();
  
  // Reset error count so reconnection works after page navigation
  streamErrorCount = 0;
  
  nextTick(() => {
    initCharts();
    resizeCanvas();
  });
  
  window.addEventListener('resize', handleResize);
  
  getSourceStatus().then(async res => {
    if (res.data.source_type) {
      sourceStore.setSourceType(res.data.source_type);
    }
    isRunning.value = !!res.data.is_running;
    isDetecting.value = !!res.data.is_detecting;
    
    if (channelCount.value <= 1) {
      projectStore.setRunningStatus(!!res.data.is_running);
      systemStore.setDetecting(!!res.data.is_detecting);
    }

    if (res.data.is_running) {
      startPolling();
      forceReconnectStream();
      return;
    }

    if (!res.data.is_detecting && res.data.source_type && res.data.model_loaded) {
      isPaused.value = true;
    }
    
    if (!res.data.source_type) {
      await autoRestoreSource();
    }
  }).catch(() => {
  });
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
  stopMultiPolling();
  stopMultiStreams();
  
  disconnectStream();
  
  if (counterWatchTimer) {
    clearTimeout(counterWatchTimer);
    counterWatchTimer = null;
  }
  
  systemStore.setDetecting(false);
  projectStore.setRunningStatus(false);
  
  if (pieChartInstance) {
    pieChartInstance.dispose();
    pieChartInstance = null;
  }
  if (gaugeChartInstance) {
    gaugeChartInstance.dispose();
    gaugeChartInstance = null;
  }
});

// 监听计数器变化更新图表（节流：最多每2秒更新一次）
let counterWatchTimer = null;
watch(counters, () => {
  if (!counterWatchTimer) {
    counterWatchTimer = setTimeout(() => {
      updateCharts();
      counterWatchTimer = null;
    }, 2000);
  }
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
