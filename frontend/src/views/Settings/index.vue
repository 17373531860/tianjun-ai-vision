<template>
  <div class="p-6 h-full overflow-y-auto">
    <h2 class="text-2xl font-bold mb-6 border-l-4 border-tech-blue pl-3 text-white">系统设置</h2>

    <el-tabs type="border-card" class="bg-gray-800 border-gray-700">
      
      <!-- Display Settings Tab -->
      <el-tab-pane label="显示设置">
        <div class="space-y-6 p-4">
          <!-- 基本信息设置 -->
          <el-card shadow="never" class="bg-slate-800 border-slate-700">
            <template #header>
              <div class="flex items-center gap-2">
                <el-icon class="text-tech-blue"><Edit /></el-icon>
                <span class="font-bold text-white">基本信息设置</span>
              </div>
            </template>
            <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
              <div class="p-3 bg-slate-900 rounded border border-slate-800">
                <div class="text-gray-300 mb-2">品牌/系统名称</div>
                <el-input v-model="store.display.brandName" placeholder="天军科技AI" @change="saveDisplaySettings" />
              </div>
              <div class="p-3 bg-slate-900 rounded border border-slate-800">
                <div class="text-gray-300 mb-2">作业员姓名</div>
                <el-input v-model="store.display.inspectorName" placeholder="张三" @change="saveDisplaySettings" />
              </div>
              <div class="p-3 bg-slate-900 rounded border border-slate-800">
                <div class="text-gray-300 mb-2">设备编号</div>
                <el-input v-model="store.display.deviceNumber" placeholder="251011" @change="saveDisplaySettings" />
              </div>
            </div>
          </el-card>
          
          <!-- Navbar Settings -->
          <el-card shadow="never" class="bg-slate-800 border-slate-700">
            <template #header>
              <div class="flex items-center gap-2">
                <el-icon class="text-tech-blue"><Top /></el-icon>
                <span class="font-bold text-white">顶部导航栏显示</span>
              </div>
            </template>
            <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <span class="text-gray-300">项目选择板块</span>
                <el-switch v-model="store.display.navbar.projectSelector" @change="saveDisplaySettings" />
              </div>
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <span class="text-gray-300">作业员姓名</span>
                <el-switch v-model="store.display.navbar.inspector" @change="saveDisplaySettings" />
              </div>
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <span class="text-gray-300">设备编号</span>
                <el-switch v-model="store.display.navbar.deviceId" @change="saveDisplaySettings" />
              </div>
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <span class="text-gray-300">当前模式</span>
                <el-switch v-model="store.display.navbar.mode" @change="saveDisplaySettings" />
              </div>
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <span class="text-gray-300">运行状态</span>
                <el-switch v-model="store.display.navbar.status" @change="saveDisplaySettings" />
              </div>
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <span class="text-gray-300">运行时间</span>
                <el-switch v-model="store.display.navbar.runtime" @change="saveDisplaySettings" />
              </div>
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <span class="text-gray-300">实时时间</span>
                <el-switch v-model="store.display.navbar.realtime" @change="saveDisplaySettings" />
              </div>
            </div>
          </el-card>

          <!-- Monitor Settings -->
          <el-card shadow="never" class="bg-slate-800 border-slate-700">
            <template #header>
              <div class="flex items-center gap-2">
                <el-icon class="text-tech-blue"><Monitor /></el-icon>
                <span class="font-bold text-white">检测中心显示</span>
              </div>
            </template>
            <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <span class="text-gray-300">SOP流程条</span>
                <el-switch v-model="store.display.monitor.stepStrip" @change="saveDisplaySettings" />
              </div>
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <span class="text-gray-300">右侧统计数据面板</span>
                <el-switch v-model="store.display.monitor.statsPanel" @change="saveDisplaySettings" />
              </div>
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <span class="text-gray-300">不良统计图表</span>
                <el-switch v-model="store.display.monitor.defectChart" @change="saveDisplaySettings" />
              </div>
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <span class="text-gray-300">产能完成图表</span>
                <el-switch v-model="store.display.monitor.capacityChart" @change="saveDisplaySettings" />
              </div>
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <span class="text-gray-300">步骤统计表格</span>
                <el-switch v-model="store.display.monitor.stepTable" @change="saveDisplaySettings" />
              </div>
            </div>
          </el-card>

          <!-- 默认计数器显示设置 -->
          <el-card shadow="never" class="bg-slate-800 border-slate-700">
            <template #header>
              <div class="flex items-center gap-2">
                <el-icon class="text-cyan-400"><DataLine /></el-icon>
                <span class="font-bold text-white">默认计数器显示</span>
                <el-tag size="small" type="info">系统内置</el-tag>
              </div>
            </template>
            <div class="mb-3 text-xs text-gray-500">
              以下是系统内置的默认计数器，可以选择在检测中心是否显示。自定义计数器需要在项目管理中配置，且与事件绑定后才会显示。
            </div>
            <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <div class="flex items-center gap-2">
                  <span class="w-2 h-2 rounded-full bg-cyan-400"></span>
                  <span class="text-gray-300">检测次数（总产量）</span>
                </div>
                <el-switch v-model="store.display.monitor.defaultCounters.showTotal" @change="saveDisplaySettings" />
              </div>
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <div class="flex items-center gap-2">
                  <span class="w-2 h-2 rounded-full bg-green-500"></span>
                  <span class="text-gray-300">OK次数（合格总数）</span>
                </div>
                <el-switch v-model="store.display.monitor.defaultCounters.showGood" @change="saveDisplaySettings" />
              </div>
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <div class="flex items-center gap-2">
                  <span class="w-2 h-2 rounded-full bg-red-500"></span>
                  <span class="text-gray-300">NG次数（不良总数）</span>
                </div>
                <el-switch v-model="store.display.monitor.defaultCounters.showBad" @change="saveDisplaySettings" />
              </div>
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <div class="flex items-center gap-2">
                  <span class="w-2 h-2 rounded-full bg-orange-500"></span>
                  <span class="text-gray-300">NG步骤</span>
                </div>
                <el-switch v-model="store.display.monitor.defaultCounters.showNgSteps" @change="saveDisplaySettings" />
              </div>
            </div>
            <el-alert
              title="NG步骤说明"
              type="warning"
              :closable="false"
              show-icon
              class="mt-4"
            >
              <template #default>
                <div class="text-xs text-gray-300">
                  <p>• <strong>NG步骤</strong>：在顺序检测模式或基于顺序的自定义模式中自动计数</p>
                  <p>• 只统计<strong>漏做的步骤</strong>（缺少步骤），按缺少数量计入</p>
                  <p>• 此计数器<strong>不能设置默认数量</strong>，但可以在事件中 +1、-1 等操作</p>
                </div>
              </template>
            </el-alert>
          </el-card>
        </div>
      </el-tab-pane>

      <!-- Detection Box Settings Tab -->
      <el-tab-pane label="检测框设置">
        <div class="space-y-6 p-4">
          <!-- 检测框外观 -->
          <el-card shadow="never" class="bg-slate-800 border-slate-700">
            <template #header>
              <div class="flex items-center gap-2">
                <el-icon class="text-tech-blue"><Box /></el-icon>
                <span class="font-bold text-white">检测框外观</span>
              </div>
            </template>
            <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
              <div class="p-3 bg-slate-900 rounded border border-slate-800">
                <div class="text-gray-300 mb-2">正常检测框颜色</div>
                <el-color-picker v-model="store.detection.boxColor" @change="saveDetectionSettings" />
              </div>
              <div class="p-3 bg-slate-900 rounded border border-slate-800">
                <div class="text-gray-300 mb-2">NG 检测框颜色</div>
                <el-color-picker v-model="store.detection.boxColorNG" @change="saveDetectionSettings" />
              </div>
              <div class="p-3 bg-slate-900 rounded border border-slate-800">
                <div class="text-gray-300 mb-2">检测框线宽</div>
                <el-slider v-model="store.detection.boxLineWidth" :min="1" :max="10" :step="1" @change="saveDetectionSettings" />
              </div>
              <div class="p-3 bg-slate-900 rounded border border-slate-800">
                <div class="text-gray-300 mb-2">标签字体大小</div>
                <el-slider v-model="store.detection.labelFontSize" :min="10" :max="30" :step="1" @change="saveDetectionSettings" />
              </div>
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <span class="text-gray-300">显示置信度</span>
                <el-switch v-model="store.detection.showConfidence" @change="saveDetectionSettings" />
              </div>
            </div>
          </el-card>

          <!-- 系统预设提示框设置 -->
          <el-card shadow="never" class="bg-slate-800 border-slate-700">
            <template #header>
              <div class="flex items-center gap-2">
                <el-icon class="text-status-ok"><Bell /></el-icon>
                <span class="font-bold text-white">系统预设提示框</span>
              </div>
            </template>
            
            <!-- 合格提示框 -->
            <div class="mb-6">
              <div class="text-cyan-400 font-bold mb-3">合格提示框</div>
              <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                <div class="p-3 bg-slate-900 rounded border border-slate-800">
                  <div class="text-gray-300 mb-2">颜色</div>
                  <el-color-picker v-model="store.detection.toasts.ok.color" @change="saveDetectionSettings" />
                </div>
                <div class="p-3 bg-slate-900 rounded border border-slate-800">
                  <div class="text-gray-300 mb-2">显示时长 (秒)</div>
                  <el-slider v-model="store.detection.toasts.ok.duration" :min="1" :max="10" :step="0.5" @change="saveDetectionSettings" />
                </div>
                <div class="p-3 bg-slate-900 rounded border border-slate-800">
                  <div class="text-gray-300 mb-2">字体大小</div>
                  <el-slider v-model="store.detection.toasts.ok.fontSize" :min="12" :max="36" :step="1" @change="saveDetectionSettings" />
                </div>
                <div class="p-3 bg-slate-900 rounded border border-slate-800">
                  <div class="text-gray-300 mb-2">位置</div>
                  <el-select v-model="store.detection.toasts.ok.position" class="w-full" @change="saveDetectionSettings">
                    <el-option label="右上角" value="top-right" />
                    <el-option label="左上角" value="top-left" />
                    <el-option label="右下角" value="bottom-right" />
                    <el-option label="左下角" value="bottom-left" />
                    <el-option label="视频顶部居中" value="center" />
                  </el-select>
                </div>
                <div class="p-3 bg-slate-900 rounded border border-slate-800">
                  <div class="text-gray-300 mb-2">主文字</div>
                  <el-input v-model="store.detection.toasts.ok.text" placeholder="合格" @change="saveDetectionSettings" />
                </div>
                <div class="p-3 bg-slate-900 rounded border border-slate-800">
                  <div class="text-gray-300 mb-2">副文字（可选）</div>
                  <el-input v-model="store.detection.toasts.ok.subText" placeholder="" @change="saveDetectionSettings" />
                </div>
              </div>
            </div>
            
            <!-- NG提示框 -->
            <div>
              <div class="text-red-400 font-bold mb-3">NG 提示框</div>
              <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                <div class="p-3 bg-slate-900 rounded border border-slate-800">
                  <div class="text-gray-300 mb-2">颜色</div>
                  <el-color-picker v-model="store.detection.toasts.ng.color" @change="saveDetectionSettings" />
                </div>
                <div class="p-3 bg-slate-900 rounded border border-slate-800">
                  <div class="text-gray-300 mb-2">显示时长 (秒)</div>
                  <el-slider v-model="store.detection.toasts.ng.duration" :min="1" :max="10" :step="0.5" @change="saveDetectionSettings" />
                </div>
                <div class="p-3 bg-slate-900 rounded border border-slate-800">
                  <div class="text-gray-300 mb-2">字体大小</div>
                  <el-slider v-model="store.detection.toasts.ng.fontSize" :min="12" :max="36" :step="1" @change="saveDetectionSettings" />
                </div>
                <div class="p-3 bg-slate-900 rounded border border-slate-800">
                  <div class="text-gray-300 mb-2">位置</div>
                  <el-select v-model="store.detection.toasts.ng.position" class="w-full" @change="saveDetectionSettings">
                    <el-option label="右上角" value="top-right" />
                    <el-option label="左上角" value="top-left" />
                    <el-option label="右下角" value="bottom-right" />
                    <el-option label="左下角" value="bottom-left" />
                    <el-option label="视频顶部居中" value="center" />
                  </el-select>
                </div>
                <div class="p-3 bg-slate-900 rounded border border-slate-800">
                  <div class="text-gray-300 mb-2">主文字</div>
                  <el-input v-model="store.detection.toasts.ng.text" placeholder="不合格" @change="saveDetectionSettings" />
                </div>
                <div class="p-3 bg-slate-900 rounded border border-slate-800">
                  <div class="text-gray-300 mb-2">副文字（可选）</div>
                  <el-input v-model="store.detection.toasts.ng.subText" placeholder="" @change="saveDetectionSettings" />
                </div>
              </div>
            </div>
          </el-card>

          <!-- 自定义提示框 -->
          <el-card shadow="never" class="bg-slate-800 border-slate-700">
            <template #header>
              <div class="flex items-center justify-between">
                <div class="flex items-center gap-2">
                  <el-icon class="text-yellow-400"><Bell /></el-icon>
                  <span class="font-bold text-white">自定义提示框</span>
                </div>
                <el-button type="primary" size="small" @click="addCustomToast">+ 新建提示框</el-button>
              </div>
            </template>
            
            <div v-if="store.detection.customToasts.length === 0" class="text-gray-500 text-center py-8 border border-dashed border-slate-700 rounded">
              暂无自定义提示框，点击上方按钮添加
            </div>
            
            <div v-else class="space-y-6">
              <div v-for="(toast, idx) in store.detection.customToasts" :key="toast.id" class="p-4 bg-slate-900 rounded border border-slate-700">
                <div class="flex items-center justify-between mb-3">
                  <el-input v-model="toast.name" class="w-48" size="small" placeholder="提示框名称" @change="saveDetectionSettings" />
                  <el-button type="danger" size="small" link @click="removeCustomToast(idx)">删除</el-button>
                </div>
                <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                  <div class="p-3 bg-slate-800 rounded">
                    <div class="text-gray-300 mb-2">颜色</div>
                    <el-color-picker v-model="toast.color" @change="saveDetectionSettings" />
                  </div>
                  <div class="p-3 bg-slate-800 rounded">
                    <div class="text-gray-300 mb-2">显示时长 (秒)</div>
                    <el-slider v-model="toast.duration" :min="1" :max="10" :step="0.5" @change="saveDetectionSettings" />
                  </div>
                  <div class="p-3 bg-slate-800 rounded">
                    <div class="text-gray-300 mb-2">字体大小</div>
                    <el-slider v-model="toast.fontSize" :min="12" :max="36" :step="1" @change="saveDetectionSettings" />
                  </div>
                  <div class="p-3 bg-slate-800 rounded">
                    <div class="text-gray-300 mb-2">位置</div>
                    <el-select v-model="toast.position" class="w-full" @change="saveDetectionSettings">
                      <el-option label="右上角" value="top-right" />
                      <el-option label="左上角" value="top-left" />
                      <el-option label="右下角" value="bottom-right" />
                      <el-option label="左下角" value="bottom-left" />
                      <el-option label="视频顶部居中" value="center" />
                    </el-select>
                  </div>
                  <div class="p-3 bg-slate-800 rounded">
                    <div class="text-gray-300 mb-2">主文字（事件名）</div>
                    <el-input v-model="toast.text" placeholder="显示事件名称" @change="saveDetectionSettings" />
                  </div>
                  <div class="p-3 bg-slate-800 rounded">
                    <div class="text-gray-300 mb-2">副文字</div>
                    <el-input v-model="toast.subText" placeholder="可选的副标题" @change="saveDetectionSettings" />
                  </div>
                </div>
              </div>
            </div>
          </el-card>
          
          <!-- 预览 -->
          <el-card shadow="never" class="bg-slate-800 border-slate-700">
            <template #header>
              <span class="font-bold text-white">效果预览</span>
            </template>
            <div class="flex flex-wrap gap-8 items-center justify-center p-6">
              <!-- 检测框预览 -->
              <div class="relative w-48 h-32 bg-gray-900 rounded border border-gray-700 flex items-center justify-center">
                <div 
                  class="w-24 h-16 border-2 relative"
                  :style="{ 
                    borderColor: store.detection.boxColor,
                    borderWidth: store.detection.boxLineWidth + 'px'
                  }"
                >
                  <div 
                    class="absolute -top-6 left-0 px-1 text-white"
                    :style="{ 
                      backgroundColor: store.detection.boxColor,
                      fontSize: store.detection.labelFontSize + 'px'
                    }"
                  >
                    检测框 {{ store.detection.showConfidence ? '95%' : '' }}
                  </div>
                </div>
              </div>
              
              <!-- 提示框预览 -->
              <div class="flex flex-wrap gap-4">
                <div 
                  class="px-6 py-4 rounded-xl shadow-lg text-white font-bold text-center"
                  :style="{ 
                    backgroundColor: store.detection.toasts.ok.color,
                    fontSize: store.detection.toasts.ok.fontSize + 'px'
                  }"
                >
                  <div>{{ store.detection.toasts.ok.text || '合格' }}</div>
                  <div v-if="store.detection.toasts.ok.subText" class="text-sm opacity-80 mt-1">{{ store.detection.toasts.ok.subText }}</div>
                </div>
                <div 
                  class="px-6 py-4 rounded-xl shadow-lg text-white font-bold text-center"
                  :style="{ 
                    backgroundColor: store.detection.toasts.ng.color,
                    fontSize: store.detection.toasts.ng.fontSize + 'px'
                  }"
                >
                  <div>{{ store.detection.toasts.ng.text || '不合格' }}</div>
                  <div v-if="store.detection.toasts.ng.subText" class="text-sm opacity-80 mt-1">{{ store.detection.toasts.ng.subText }}</div>
                </div>
                <div 
                  v-for="toast in store.detection.customToasts" 
                  :key="toast.id"
                  class="px-6 py-4 rounded-xl shadow-lg text-white font-bold text-center"
                  :style="{ 
                    backgroundColor: toast.color,
                    fontSize: toast.fontSize + 'px'
                  }"
                >
                  <div>{{ toast.text || toast.name }}</div>
                  <div v-if="toast.subText" class="text-sm opacity-80 mt-1">{{ toast.subText }}</div>
                </div>
              </div>
            </div>
          </el-card>
        </div>
      </el-tab-pane>

      <!-- Performance Settings Tab -->
      <el-tab-pane label="性能设置">
        <div class="space-y-6 p-4">
          <!-- 视频流设置 -->
          <el-card shadow="never" class="bg-slate-800 border-slate-700">
            <template #header>
              <div class="flex items-center gap-2">
                <el-icon class="text-tech-blue"><VideoCamera /></el-icon>
                <span class="font-bold text-white">视频流设置</span>
              </div>
            </template>
            <div class="space-y-4">
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <div>
                  <span class="text-gray-300">启用帧率限制</span>
                  <div class="text-xs text-gray-500 mt-1">本地应用建议关闭以获得最低延迟</div>
                </div>
                <el-switch v-model="store.performance.frameLimitEnabled" @change="savePerformanceSettings" />
              </div>
              <div v-if="store.performance.frameLimitEnabled" class="p-3 bg-slate-900 rounded border border-slate-800">
                <div class="text-gray-300 mb-2">目标帧率 (FPS)</div>
                <el-slider 
                  v-model="store.performance.targetStreamFps" 
                  :min="10" 
                  :max="60" 
                  :step="5" 
                  show-stops
                  :marks="{10: '10', 30: '30', 60: '60'}"
                  @change="savePerformanceSettings" 
                />
                <div class="text-xs text-gray-500 mt-2">当前: {{ store.performance.targetStreamFps }} FPS</div>
              </div>
              <el-alert
                title="延迟优化说明"
                type="info"
                :closable="false"
                show-icon
              >
                <template #default>
                  <div class="text-xs text-gray-300 mt-1">
                    <p>• <strong>关闭帧率限制</strong>：有新帧立即发送，延迟最低（推荐本地使用）</p>
                    <p>• <strong>开启帧率限制</strong>：限制传输频率，适合低配电脑或远程使用</p>
                  </div>
                </template>
              </el-alert>
            </div>
          </el-card>

          <!-- GPU/推理设备设置 -->
          <el-card shadow="never" class="bg-slate-800 border-slate-700">
            <template #header>
              <div class="flex items-center justify-between">
                <div class="flex items-center gap-2">
                  <el-icon class="text-green-400"><Cpu /></el-icon>
                  <span class="font-bold text-white">推理设备 (GPU/CPU)</span>
                </div>
                <el-button type="primary" link @click="refreshGpuList" :loading="loadingGpu">
                  <el-icon class="mr-1"><Refresh /></el-icon> 刷新
                </el-button>
              </div>
            </template>
            <div class="space-y-4">
              <!-- CUDA状态显示 -->
              <div class="p-3 bg-slate-900 rounded border border-slate-800">
                <div class="flex items-center justify-between">
                  <span class="text-gray-300">CUDA 状态</span>
                  <el-tag :type="gpuInfo.cudaAvailable ? 'success' : 'danger'" size="small">
                    {{ gpuInfo.cudaAvailable ? `可用 (CUDA ${gpuInfo.cudaVersion})` : '不可用' }}
                  </el-tag>
                </div>
                <div v-if="gpuInfo.gpuCount > 0" class="text-xs text-gray-500 mt-1">
                  检测到 {{ gpuInfo.gpuCount }} 个GPU设备
                </div>
              </div>

              <!-- 设备选择 -->
              <div class="p-3 bg-slate-900 rounded border border-slate-800">
                <div class="flex items-center justify-between mb-2">
                  <span class="text-gray-300">默认推理设备</span>
                  <el-tag v-if="isDefaultDevice" type="success" size="small">已保存为默认</el-tag>
                </div>
                <el-select v-model="selectedDevice" class="w-full" @change="changeDevice" :loading="changingDevice">
                  <el-option
                    v-for="device in gpuInfo.devices"
                    :key="device.id"
                    :label="device.name"
                    :value="device.id"
                  >
                    <div class="flex items-center justify-between w-full">
                      <span>{{ device.name }}</span>
                      <el-tag v-if="device.type === 'GPU'" type="success" size="small">GPU</el-tag>
                      <el-tag v-else-if="device.type === 'CPU'" type="info" size="small">CPU</el-tag>
                      <el-tag v-else type="warning" size="small">自动</el-tag>
                    </div>
                  </el-option>
                </el-select>
                <div class="text-xs text-gray-500 mt-2">
                  选择后自动保存，下次启动将使用此设备
                </div>
              </div>

              <!-- 当前使用的设备 -->
              <div v-if="currentDeviceInfo" class="p-3 bg-slate-900 rounded border" 
                   :class="currentDeviceInfo.type === 'GPU' ? 'border-green-600' : 'border-slate-700'">
                <div class="flex items-center justify-between">
                  <span class="text-gray-300">当前推理设备</span>
                  <el-tag :type="currentDeviceInfo.type === 'GPU' ? 'success' : 'info'" size="small">
                    {{ currentDeviceInfo.type }}
                  </el-tag>
                </div>
                <div class="text-lg font-bold mt-1" :class="currentDeviceInfo.type === 'GPU' ? 'text-green-400' : 'text-gray-400'">
                  {{ currentDeviceInfo.name }}
                </div>
                <div class="text-xs text-gray-500 mt-1">设备: {{ currentDeviceInfo.device }}</div>
              </div>
              <div v-else class="p-3 bg-slate-900 rounded border border-slate-700 text-center text-gray-500">
                尚未加载模型，设备信息将在加载模型后显示
              </div>

              <el-alert
                title="GPU 推理说明"
                type="info"
                :closable="false"
                show-icon
              >
                <template #default>
                  <div class="text-xs text-gray-300 mt-1">
                    <p>• <strong>GPU推理</strong>：速度快（约10-50ms/帧），需要NVIDIA显卡和驱动</p>
                    <p>• <strong>CPU推理</strong>：速度慢（约100-300ms/帧），任何电脑都支持</p>
                    <p>• 切换设备后需要重新加载模型才能生效</p>
                  </div>
                </template>
              </el-alert>
            </div>
          </el-card>

          <!-- 检测框滤波设置 -->
          <el-card shadow="never" class="bg-slate-800 border-slate-700">
            <template #header>
              <div class="flex items-center gap-2">
                <el-icon class="text-purple-400"><Box /></el-icon>
                <span class="font-bold text-white">检测框滤波 (卡尔曼滤波)</span>
              </div>
            </template>
            <div class="space-y-4">
              <!-- 启用开关 -->
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <div>
                  <span class="text-gray-300">启用卡尔曼滤波</span>
                  <div class="text-xs text-gray-500 mt-1">平滑检测框运动轨迹，减少抖动</div>
                </div>
                <el-switch v-model="kalmanConfig.enabled" @change="saveKalmanConfig" />
              </div>

              <!-- 过程噪声 Q -->
              <div v-if="kalmanConfig.enabled" class="p-3 bg-slate-900 rounded border border-slate-800">
                <div class="flex items-center justify-between mb-2">
                  <span class="text-gray-300">响应速度 (过程噪声 Q)</span>
                  <span class="text-tech-blue font-mono">{{ kalmanConfig.processNoise.toFixed(3) }}</span>
                </div>
                <el-slider 
                  v-model="kalmanConfig.processNoise" 
                  :min="0.001" 
                  :max="0.5" 
                  :step="0.005" 
                  @change="saveKalmanConfig" 
                />
                <div class="text-xs text-gray-500 mt-2">
                  <span class="text-yellow-400">← 更平滑</span>
                  <span class="float-right text-green-400">响应更快 →</span>
                </div>
              </div>

              <!-- 观测噪声 R -->
              <div v-if="kalmanConfig.enabled" class="p-3 bg-slate-900 rounded border border-slate-800">
                <div class="flex items-center justify-between mb-2">
                  <span class="text-gray-300">平滑程度 (观测噪声 R)</span>
                  <span class="text-tech-blue font-mono">{{ kalmanConfig.measurementNoise.toFixed(2) }}</span>
                </div>
                <el-slider 
                  v-model="kalmanConfig.measurementNoise" 
                  :min="0.01" 
                  :max="1.0" 
                  :step="0.01" 
                  @change="saveKalmanConfig" 
                />
                <div class="text-xs text-gray-500 mt-2">
                  <span class="text-red-400">← 更抖动</span>
                  <span class="float-right text-blue-400">更平滑 →</span>
                </div>
              </div>

              <!-- 消失帧数阈值 -->
              <div v-if="kalmanConfig.enabled" class="p-3 bg-slate-900 rounded border border-slate-800">
                <div class="flex items-center justify-between mb-2">
                  <span class="text-gray-300">目标消失容忍帧数</span>
                  <span class="text-tech-blue font-mono">{{ kalmanConfig.maxMissingFrames }} 帧</span>
                </div>
                <el-slider 
                  v-model="kalmanConfig.maxMissingFrames" 
                  :min="1" 
                  :max="30" 
                  :step="1" 
                  @change="saveKalmanConfig" 
                />
                <div class="text-xs text-gray-500 mt-2">
                  目标消失多少帧后移除滤波器（值越大，短暂遮挡时框越稳定）
                </div>
              </div>

              <!-- 推荐预设 -->
              <div v-if="kalmanConfig.enabled" class="p-3 bg-slate-900 rounded border border-slate-800">
                <div class="text-gray-300 mb-3">快速预设</div>
                <div class="flex gap-2 flex-wrap">
                  <el-button size="small" @click="applyKalmanPreset('smooth')">
                    🎯 更平滑
                  </el-button>
                  <el-button size="small" @click="applyKalmanPreset('balanced')">
                    ⚖️ 平衡
                  </el-button>
                  <el-button size="small" @click="applyKalmanPreset('responsive')">
                    ⚡ 快响应
                  </el-button>
                </div>
              </div>

              <el-alert
                title="滤波参数调节指南"
                type="info"
                :closable="false"
                show-icon
              >
                <template #default>
                  <div class="text-xs text-gray-300 mt-1">
                    <p>• <strong>物体移动较慢</strong>：增大R值（更平滑），减小Q值</p>
                    <p>• <strong>物体移动较快</strong>：增大Q值（响应更快），减小R值</p>
                    <p>• <strong>检测框抖动严重</strong>：增大R值</p>
                    <p>• <strong>检测框跟不上物体</strong>：增大Q值</p>
                  </div>
                </template>
              </el-alert>
            </div>
          </el-card>
        </div>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue';
import { useSystemStore } from '@/store/useSystemStore';
import { useProjectStore } from '@/store/useProjectStore';
import { Top, Monitor, Box, Bell, Edit, VideoCamera, Cpu, Refresh, DataLine } from '@element-plus/icons-vue';
import { ElMessage } from 'element-plus';
import { getProjectDetail } from '@/api/project';
import api from '@/api/index';

const store = useSystemStore();
const projectStore = useProjectStore();

// GPU相关状态
const loadingGpu = ref(false);
const changingDevice = ref(false);
const selectedDevice = ref('auto');
const currentDeviceInfo = ref(null);
const isDefaultDevice = ref(false);
const gpuInfo = reactive({
  devices: [],
  cudaAvailable: false,
  cudaVersion: null,
  gpuCount: 0
});

// 卡尔曼滤波配置
const kalmanConfig = reactive({
  enabled: true,
  processNoise: 0.03,
  measurementNoise: 0.1,
  maxMissingFrames: 5
});

const saveDisplaySettings = () => {
  localStorage.setItem('display_settings', JSON.stringify(store.display));
};

const saveDetectionSettings = () => {
  store.saveDetectionSettings();
};

// 添加自定义提示框
const addCustomToast = () => {
  const nextId = 'custom_' + Date.now();
  store.detection.customToasts.push({
    id: nextId,
    name: '新提示框',
    color: '#eab308',
    duration: 3,
    fontSize: 18,
    position: 'top-right',
    text: '',
    subText: ''
  });
  saveDetectionSettings();
  ElMessage.success('已添加自定义提示框');
};

// 删除自定义提示框
const removeCustomToast = (idx) => {
  store.detection.customToasts.splice(idx, 1);
  saveDetectionSettings();
  ElMessage.info('已删除提示框');
};

// 保存性能设置
const savePerformanceSettings = async () => {
  store.savePerformanceSettings();
  try {
    // 同步到后端
    await api.post('/source/stream/config', {
      frame_limit_enabled: store.performance.frameLimitEnabled,
      target_stream_fps: store.performance.targetStreamFps
    });
    ElMessage.success('性能设置已保存');
  } catch (e) {
    console.error('同步性能设置到后端失败:', e);
  }
};

// 加载性能设置
const loadPerformanceSettings = async () => {
  store.loadPerformanceSettings();
  try {
    // 从后端同步配置
    const res = await api.get('/source/stream/config');
    if (res.data) {
      store.performance.frameLimitEnabled = res.data.frame_limit_enabled;
      store.performance.targetStreamFps = res.data.target_stream_fps;
    }
  } catch (e) {
    console.error('加载后端性能设置失败:', e);
  }
};

// 刷新GPU列表
const refreshGpuList = async () => {
  loadingGpu.value = true;
  try {
    const res = await api.get('/source/gpu/list');
    if (res.data) {
      gpuInfo.devices = res.data.devices || [];
      gpuInfo.cudaAvailable = res.data.cuda_available;
      gpuInfo.cudaVersion = res.data.cuda_version;
      gpuInfo.gpuCount = res.data.gpu_count;
    }
  } catch (e) {
    console.error('获取GPU列表失败:', e);
    ElMessage.error('获取GPU列表失败');
  } finally {
    loadingGpu.value = false;
  }
};

// 获取当前设备信息
const loadCurrentDevice = async () => {
  try {
    const res = await api.get('/source/gpu/current');
    if (res.data) {
      selectedDevice.value = res.data.device || 'auto';
      currentDeviceInfo.value = res.data.current_device_info;
      isDefaultDevice.value = true;  // 从后端加载的就是已保存的默认设备
    }
  } catch (e) {
    console.error('获取当前设备信息失败:', e);
  }
};

// 切换推理设备
const changeDevice = async (device) => {
  changingDevice.value = true;
  try {
    const res = await api.post('/source/gpu/set', { device });
    if (res.data) {
      if (res.data.status === 'success') {
        ElMessage.success(res.data.message);
        currentDeviceInfo.value = res.data.current_device_info;
        isDefaultDevice.value = true;  // 标记已保存
      } else {
        ElMessage.error(res.data.message);
      }
    }
  } catch (e) {
    console.error('切换设备失败:', e);
    ElMessage.error('切换设备失败');
  } finally {
    changingDevice.value = false;
  }
};

// ========== 卡尔曼滤波配置 ==========
const loadKalmanConfig = async () => {
  try {
    const res = await api.get('/source/kalman/config');
    if (res.data) {
      kalmanConfig.enabled = res.data.enabled;
      kalmanConfig.processNoise = res.data.process_noise;
      kalmanConfig.measurementNoise = res.data.measurement_noise;
      kalmanConfig.maxMissingFrames = res.data.max_missing_frames;
    }
  } catch (e) {
    console.error('获取卡尔曼配置失败:', e);
  }
};

const saveKalmanConfig = async () => {
  try {
    await api.post('/source/kalman/config', {
      enabled: kalmanConfig.enabled,
      process_noise: kalmanConfig.processNoise,
      measurement_noise: kalmanConfig.measurementNoise,
      max_missing_frames: kalmanConfig.maxMissingFrames
    });
    ElMessage.success('滤波参数已更新');
  } catch (e) {
    console.error('保存卡尔曼配置失败:', e);
    ElMessage.error('保存失败');
  }
};

const applyKalmanPreset = (preset) => {
  switch (preset) {
    case 'smooth':
      // 更平滑：低Q高R
      kalmanConfig.processNoise = 0.01;
      kalmanConfig.measurementNoise = 0.5;
      kalmanConfig.maxMissingFrames = 10;
      break;
    case 'balanced':
      // 平衡
      kalmanConfig.processNoise = 0.03;
      kalmanConfig.measurementNoise = 0.1;
      kalmanConfig.maxMissingFrames = 5;
      break;
    case 'responsive':
      // 快响应：高Q低R
      kalmanConfig.processNoise = 0.1;
      kalmanConfig.measurementNoise = 0.05;
      kalmanConfig.maxMissingFrames = 3;
      break;
  }
  saveKalmanConfig();
};

onMounted(async () => {
  store.loadSettings();
  loadPerformanceSettings();
  refreshGpuList();
  loadCurrentDevice();
  loadKalmanConfig();  // 加载卡尔曼滤波配置
  
  // 从当前项目加载检测配置（包括自定义提示框）
  if (projectStore.currentProjectId) {
    try {
      const res = await getProjectDetail(projectStore.currentProjectId);
      if (res.data?.detection_config) {
        store.loadDetectionFromProject(res.data.detection_config);
      }
    } catch (e) {
      console.error('加载项目检测配置失败:', e);
    }
  } else {
    // 没有选择项目时，尝试从 localStorage 恢复
    const saved = localStorage.getItem('detection_settings');
    if (saved) {
      try {
        store.loadDetectionFromProject(JSON.parse(saved));
      } catch (e) {}
    }
  }
});
</script>
