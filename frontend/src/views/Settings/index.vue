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
                <div class="flex items-center justify-between mb-2">
                  <span class="text-gray-300">品牌/系统名称</span>
                  <el-switch v-model="store.display.navbar.brandName" @change="saveDisplaySettings" />
                </div>
                <el-input v-model="store.display.brandName" placeholder="天军科技AI" @change="saveDisplaySettings" />
              </div>
              <div class="p-3 bg-slate-900 rounded border border-slate-800">
                <div class="flex items-center justify-between mb-2">
                  <span class="text-gray-300">软件名称</span>
                  <el-switch v-model="store.display.navbar.appName" @change="saveDisplaySettings" />
                </div>
                <el-input v-model="store.display.appName" placeholder="视觉AI行为引导系统" @change="saveDisplaySettings" />
              </div>
              <div class="p-3 bg-slate-900 rounded border border-slate-800">
                <div class="flex items-center justify-between mb-2">
                  <span class="text-gray-300">当前作业员</span>
                  <el-switch v-model="store.display.navbar.inspector" @change="saveDisplaySettings" />
                </div>
                <el-input v-model="store.display.inspectorName" placeholder="张三" @change="saveDisplaySettings" />
              </div>
              <div class="p-3 bg-slate-900 rounded border border-slate-800">
                <div class="flex items-center justify-between mb-2">
                  <span class="text-gray-300">设备编号</span>
                  <el-switch v-model="store.display.navbar.deviceId" @change="saveDisplaySettings" />
                </div>
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
              <div v-if="store.display.monitor.stepTable" class="ml-3 pl-3 border-l-2 border-slate-700 space-y-2">
                <div class="text-xs text-gray-500 px-1">表格列显示（默认全开）</div>
                <div class="flex items-center justify-between p-2 bg-slate-900/80 rounded border border-slate-800">
                  <span class="text-gray-300 text-sm">序号</span>
                  <el-switch v-model="store.display.monitor.stepTableColumns.showNo" @change="saveDisplaySettings" />
                </div>
                <div class="flex items-center justify-between p-2 bg-slate-900/80 rounded border border-slate-800">
                  <span class="text-gray-300 text-sm">步骤</span>
                  <el-switch v-model="store.display.monitor.stepTableColumns.showStep" @change="saveDisplaySettings" />
                </div>
                <div class="flex items-center justify-between p-2 bg-slate-900/80 rounded border border-slate-800">
                  <span class="text-gray-300 text-sm">状态</span>
                  <el-switch v-model="store.display.monitor.stepTableColumns.showStatus" @change="saveDisplaySettings" />
                </div>
                <div class="flex items-center justify-between p-2 bg-slate-900/80 rounded border border-slate-800">
                  <span class="text-gray-300 text-sm">PT/s</span>
                  <el-switch v-model="store.display.monitor.stepTableColumns.showPt" @change="saveDisplaySettings" />
                </div>
                <div class="flex items-center justify-between p-2 bg-slate-900/80 rounded border border-slate-800">
                  <span class="text-gray-300 text-sm">结果</span>
                  <el-switch v-model="store.display.monitor.stepTableColumns.showResult" @change="saveDisplaySettings" />
                </div>
              </div>
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <span class="text-gray-300">FPS</span>
                <el-switch v-model="store.display.monitor.showFps" @change="saveDisplaySettings" />
              </div>
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <span class="text-gray-300">延迟</span>
                <el-switch v-model="store.display.monitor.showLatency" @change="saveDisplaySettings" />
              </div>
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <span class="text-gray-300">检测数</span>
                <el-switch v-model="store.display.monitor.showDetectionCount" @change="saveDisplaySettings" />
              </div>
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <span class="text-gray-300">CT包含NG周期</span>
                <el-switch v-model="store.display.monitor.ctIncludeNg" @change="saveDisplaySettings" />
              </div>
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <div class="flex flex-col">
                  <span class="text-gray-300">PT 显示口径</span>
                  <span class="text-[10px] text-gray-500">步骤耗时取哪一轮</span>
                </div>
                <el-select v-model="store.display.monitor.ptMode" size="small" style="width: 8rem" @change="saveDisplaySettings">
                  <el-option label="平均" value="avg" />
                  <el-option label="最近一轮" value="last" />
                  <el-option label="当前周期内" value="current" />
                </el-select>
              </div>
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <div class="flex flex-col">
                  <span class="text-gray-300">PT 计算方式</span>
                  <span class="text-[10px] text-gray-500">同步骤一周期内多次出现：合并(SUM) / 最后一次</span>
                </div>
                <el-select v-model="store.display.monitor.ptAggregate" size="small" style="width: 8rem" @change="saveDisplaySettings">
                  <el-option label="合并" value="sum" />
                  <el-option label="最后一次" value="last" />
                </el-select>
              </div>
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <div class="flex flex-col">
                  <span class="text-gray-300">CT 显示口径</span>
                  <span class="text-[10px] text-gray-500">周期时间取哪一轮</span>
                </div>
                <el-select v-model="store.display.monitor.ctMode" size="small" style="width: 8rem" @change="saveDisplaySettings">
                  <el-option label="平均" value="avg" />
                  <el-option label="最近一轮" value="last" />
                  <el-option label="当前周期内" value="current" />
                </el-select>
              </div>
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <span class="text-gray-300">NG步骤TOP3</span>
                <el-switch v-model="store.display.monitor.ngTop3" @change="saveDisplaySettings" />
              </div>
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <span class="text-gray-300">NG TOP3 显示模式</span>
                <el-select v-model="store.display.monitor.ngTopDisplayMode" size="small" style="width: 6.25rem" @change="saveDisplaySettings">
                  <el-option label="百分比" value="percentage" />
                  <el-option label="次数" value="count" />
                </el-select>
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

          <!-- 画面变换（旋转 + 镜像；检测框随画面一起转，每通道独立） -->
          <el-card shadow="never" class="bg-slate-800 border-slate-700">
            <template #header>
              <div class="flex items-center gap-2">
                <el-icon class="text-tech-blue"><Refresh /></el-icon>
                <span class="font-bold text-white">画面变换</span>
                <el-tag size="small" type="info">每通道独立</el-tag>
              </div>
            </template>
            <div class="space-y-4">
              <div class="flex items-center gap-3">
                <span class="text-gray-300 whitespace-nowrap">工位通道</span>
                <el-select v-model="transformChannel" size="default" style="width: 10rem" @change="loadTransformConfig">
                  <el-option v-for="n in Math.max(transformTotalChannels, 1)" :key="n - 1" :label="`工位 ${n}`" :value="n - 1" />
                </el-select>
                <el-button size="small" @click="loadTransformConfig" :loading="transformLoading">
                  <el-icon class="mr-1"><Refresh /></el-icon>刷新
                </el-button>
              </div>
              <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
                <div class="p-3 bg-slate-900 rounded border border-slate-800">
                  <div class="flex items-center justify-between mb-2">
                    <span class="text-gray-300">旋转角度</span>
                  </div>
                  <el-select v-model="transformForm.rotation" size="default" class="w-full">
                    <el-option label="0°（不旋转）" :value="0" />
                    <el-option label="90°（顺时针）" :value="90" />
                    <el-option label="180°" :value="180" />
                    <el-option label="270°（逆时针 90°）" :value="270" />
                  </el-select>
                </div>
                <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                  <span class="text-gray-300">左右镜像</span>
                  <el-switch v-model="transformForm.flip_h" />
                </div>
                <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                  <span class="text-gray-300">上下镜像</span>
                  <el-switch v-model="transformForm.flip_v" />
                </div>
              </div>
              <div class="flex items-center justify-between">
                <span class="text-xs text-gray-400">变换发生在帧采集之后、推理之前；检测框、录像、MJPEG 流都会跟随一起转/镜像。</span>
                <el-button type="primary" size="default" @click="saveTransformConfig" :loading="transformSaving">
                  保存此工位设置
                </el-button>
              </div>
            </div>
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
                <el-slider v-model="store.detection.boxLineWidth" :min="0" :step="1" @change="saveDetectionSettings" />
              </div>
              <div class="p-3 bg-slate-900 rounded border border-slate-800">
                <div class="text-gray-300 mb-2">标签字体大小</div>
                <el-slider v-model="store.detection.labelFontSize" :min="0" :step="1" @change="saveDetectionSettings" />
              </div>
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <span class="text-gray-300">显示置信度</span>
                <el-switch v-model="store.detection.showConfidence" @change="saveDetectionSettings" />
              </div>
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <span class="text-gray-300">NG弹窗显示原因</span>
                <el-switch v-model="store.detection.showNgReason" @change="saveDetectionSettings" />
              </div>
            </div>
          </el-card>

          <!-- 系统预设提示框设置 -->
          <el-card v-if="store.detection.toasts?.ok" shadow="never" class="bg-slate-800 border-slate-700">
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
                  <el-slider v-model="store.detection.toasts.ok.duration" :min="0" :step="0.5" @change="saveDetectionSettings" />
                </div>
                <div class="p-3 bg-slate-900 rounded border border-slate-800">
                  <div class="text-gray-300 mb-2">字体大小</div>
                  <el-slider v-model="store.detection.toasts.ok.fontSize" :min="0" :step="1" @change="saveDetectionSettings" />
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
                  <el-slider v-model="store.detection.toasts.ng.duration" :min="0" :step="0.5" @change="saveDetectionSettings" />
                </div>
                <div class="p-3 bg-slate-900 rounded border border-slate-800">
                  <div class="text-gray-300 mb-2">字体大小</div>
                  <el-slider v-model="store.detection.toasts.ng.fontSize" :min="0" :step="1" @change="saveDetectionSettings" />
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
            
            <!-- 扫码提示框 -->
            <div>
              <div class="flex items-center gap-3 mb-3">
                <span class="text-cyan-400 font-bold">扫码提示框</span>
                <el-switch v-model="store.detection.toasts.scan.enabled" @change="saveDetectionSettings" active-text="启用" />
              </div>
              <div v-if="store.detection.toasts.scan?.enabled" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                <div class="p-3 bg-slate-900 rounded border border-slate-800">
                  <div class="text-gray-300 mb-2">颜色</div>
                  <el-color-picker v-model="store.detection.toasts.scan.color" @change="saveDetectionSettings" />
                </div>
                <div class="p-3 bg-slate-900 rounded border border-slate-800">
                  <div class="text-gray-300 mb-2">显示时长 (秒)</div>
                  <el-slider v-model="store.detection.toasts.scan.duration" :min="0" :step="0.5" @change="saveDetectionSettings" />
                </div>
                <div class="p-3 bg-slate-900 rounded border border-slate-800">
                  <div class="text-gray-300 mb-2">字体大小</div>
                  <el-slider v-model="store.detection.toasts.scan.fontSize" :min="0" :step="1" @change="saveDetectionSettings" />
                </div>
                <div class="p-3 bg-slate-900 rounded border border-slate-800">
                  <div class="text-gray-300 mb-2">位置</div>
                  <el-select v-model="store.detection.toasts.scan.position" class="w-full" @change="saveDetectionSettings">
                    <el-option label="右上角" value="top-right" />
                    <el-option label="左上角" value="top-left" />
                    <el-option label="右下角" value="bottom-right" />
                    <el-option label="左下角" value="bottom-left" />
                    <el-option label="视频顶部居中" value="center" />
                  </el-select>
                </div>
                <div class="p-3 bg-slate-900 rounded border border-slate-800">
                  <div class="text-gray-300 mb-2">主文字</div>
                  <el-input v-model="store.detection.toasts.scan.text" placeholder="扫码成功" @change="saveDetectionSettings" />
                </div>
                <div class="p-3 bg-slate-900 rounded border border-slate-800">
                  <div class="text-gray-300 mb-2">副文字（可选，留空则显示条码）</div>
                  <el-input v-model="store.detection.toasts.scan.subText" placeholder="" @change="saveDetectionSettings" />
                </div>
              </div>
            </div>

            <!-- 未绑码提示框 -->
            <div>
              <div class="flex items-center gap-3 mb-3">
                <span class="text-cyan-400 font-bold">未绑码提示框</span>
                <el-switch v-model="store.detection.toasts.warn_no_barcode.enabled" @change="saveDetectionSettings" active-text="启用" />
              </div>
              <div v-if="store.detection.toasts.warn_no_barcode?.enabled" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                <div class="p-3 bg-slate-900 rounded border border-slate-800">
                  <div class="text-gray-300 mb-2">颜色</div>
                  <el-color-picker v-model="store.detection.toasts.warn_no_barcode.color" @change="saveDetectionSettings" />
                </div>
                <div class="p-3 bg-slate-900 rounded border border-slate-800">
                  <div class="text-gray-300 mb-2">显示时长 (秒)</div>
                  <el-slider v-model="store.detection.toasts.warn_no_barcode.duration" :min="0" :step="0.5" @change="saveDetectionSettings" />
                </div>
                <div class="p-3 bg-slate-900 rounded border border-slate-800">
                  <div class="text-gray-300 mb-2">字体大小</div>
                  <el-slider v-model="store.detection.toasts.warn_no_barcode.fontSize" :min="0" :step="1" @change="saveDetectionSettings" />
                </div>
                <div class="p-3 bg-slate-900 rounded border border-slate-800">
                  <div class="text-gray-300 mb-2">位置</div>
                  <el-select v-model="store.detection.toasts.warn_no_barcode.position" class="w-full" @change="saveDetectionSettings">
                    <el-option label="右上角" value="top-right" />
                    <el-option label="左上角" value="top-left" />
                    <el-option label="右下角" value="bottom-right" />
                    <el-option label="左下角" value="bottom-left" />
                    <el-option label="居中" value="center" />
                  </el-select>
                </div>
                <div class="p-3 bg-slate-900 rounded border border-slate-800">
                  <div class="text-gray-300 mb-2">主文字</div>
                  <el-input v-model="store.detection.toasts.warn_no_barcode.text" placeholder="⚠ 未绑码" @change="saveDetectionSettings" />
                </div>
                <div class="p-3 bg-slate-900 rounded border border-slate-800">
                  <div class="text-gray-300 mb-2">副文字</div>
                  <el-input v-model="store.detection.toasts.warn_no_barcode.subText" placeholder="本次结算未绑定工件条码" @change="saveDetectionSettings" />
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
                    <el-slider v-model="toast.duration" :min="0" :step="0.5" @change="saveDetectionSettings" />
                  </div>
                  <div class="p-3 bg-slate-800 rounded">
                    <div class="text-gray-300 mb-2">字体大小</div>
                    <el-slider v-model="toast.fontSize" :min="0" :step="1" @change="saveDetectionSettings" />
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
                      fontSize: (store.detection.labelFontSize / 16) + 'rem'
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
                    fontSize: (store.detection.toasts.ok.fontSize / 16) + 'rem'
                  }"
                >
                  <div>{{ store.detection.toasts.ok.text || '合格' }}</div>
                  <div v-if="store.detection.toasts.ok.subText" class="text-sm opacity-80 mt-1">{{ store.detection.toasts.ok.subText }}</div>
                </div>
                <div 
                  class="px-6 py-4 rounded-xl shadow-lg text-white font-bold text-center"
                  :style="{ 
                    backgroundColor: store.detection.toasts.ng.color,
                    fontSize: (store.detection.toasts.ng.fontSize / 16) + 'rem'
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
                    fontSize: (toast.fontSize / 16) + 'rem'
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
                  :min="0" 
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

          <!-- 推理加速设置 -->
          <el-card shadow="never" class="bg-slate-800 border-slate-700">
            <template #header>
              <div class="flex items-center gap-2">
                <el-icon class="text-yellow-400"><Lightning /></el-icon>
                <span class="font-bold text-white">推理加速</span>
              </div>
            </template>
            <div class="space-y-4">
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <div>
                  <span class="text-gray-300">FP16 半精度推理</span>
                  <div class="text-xs text-gray-500 mt-1">开启后推理速度约提升 50%-100%，适用于 NVIDIA RTX 20/30/40/50 系列 GPU</div>
                </div>
                <el-switch v-model="store.performance.halfPrecision" @change="savePerformanceSettings" />
              </div>
              <el-alert
                title="说明"
                type="warning"
                :closable="false"
                show-icon
              >
                <template #default>
                  <div class="text-xs text-gray-300 mt-1">
                    <p>• 开启后使用 16 位浮点精度推理，大幅降低单帧推理耗时</p>
                    <p>• 检测精度几乎无损（差异 &lt; 0.1%），Ultralytics 官方推荐</p>
                    <p>• 仅在 GPU (CUDA) 设备上生效，CPU 推理不受影响</p>
                    <p>• 修改后<strong>下次加载模型时生效</strong>（重新开始检测即可）</p>
                  </div>
                </template>
              </el-alert>
            </div>
          </el-card>

          <!-- MediaPipe 骨架叠加 -->
          <el-card shadow="never" class="bg-slate-800 border-slate-700">
            <template #header>
              <div class="flex items-center gap-2">
                <el-icon class="text-purple-400"><Aim /></el-icon>
                <span class="font-bold text-white">MediaPipe 骨架叠加</span>
                <el-tag size="small" type="info">视觉增强</el-tag>
              </div>
            </template>
            <div class="space-y-4">
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <div>
                  <span class="text-gray-300">启用 MediaPipe 叠加</span>
                  <div class="text-xs text-gray-500 mt-1">在画面上实时显示人体骨架和手部关键点，纯视觉效果，不影响检测逻辑</div>
                </div>
                <el-switch v-model="store.performance.mediapipeEnabled" @change="savePerformanceSettings" />
              </div>
              <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                  <span class="text-gray-300">姿态骨架</span>
                  <el-switch v-model="store.performance.mediapipePose" :disabled="!store.performance.mediapipeEnabled" @change="savePerformanceSettings" />
                </div>
                <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                  <span class="text-gray-300">手部关键点</span>
                  <el-switch v-model="store.performance.mediapipeHands" :disabled="!store.performance.mediapipeEnabled" @change="savePerformanceSettings" />
                </div>
              </div>
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <div>
                  <span class="text-gray-300">检测置信度</span>
                  <div class="text-xs text-gray-500 mt-1">值越高误检越少但可能漏检（俯视角度建议 0.7-0.9）</div>
                </div>
                <div class="flex items-center gap-2">
                  <el-slider
                    v-model="store.performance.mediapipeConfidence"
                    :min="0"
                    :step="0.05"
                    :disabled="!store.performance.mediapipeEnabled"
                    @change="savePerformanceSettings"
                    style="width: 140px"
                    :show-tooltip="true"
                    :format-tooltip="v => v.toFixed(2)"
                  />
                  <span class="text-gray-400 text-xs w-8 text-right">{{ store.performance.mediapipeConfidence.toFixed(2) }}</span>
                </div>
              </div>
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <div>
                  <span class="text-gray-300">处理间隔</span>
                  <div class="text-xs text-gray-500 mt-1">每隔 N 帧处理一次（值越大性能越好但骨架更新越慢）</div>
                </div>
                <el-input-number
                  v-model="store.performance.mediapipeInterval"
                  size="small"
                  :min="0"
                  :precision="0"
                  :step="1"
                  :controls="true"
                  :disabled="!store.performance.mediapipeEnabled"
                  @change="savePerformanceSettings"
                  style="width: 120px"
                />
              </div>

              <!-- v3.8.0: 一键预设 -->
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <div>
                  <span class="text-gray-300">手部识别预设</span>
                  <div class="text-xs text-gray-500 mt-1">
                    省 CPU 模式 = 默认（complexity=0, 置信度 0.7）；高精度模式 = 友商同款（complexity=1, 置信度 0.5），手部识别率提升 3-4 倍但 CPU 占用增加约 80%
                  </div>
                </div>
                <div class="flex flex-col gap-2">
                  <el-button
                    size="small"
                    type="info"
                    plain
                    :disabled="!store.performance.mediapipeEnabled"
                    @click="applyMediaPipePreset('eco')"
                  >省 CPU 模式</el-button>
                  <el-button
                    size="small"
                    type="success"
                    plain
                    :disabled="!store.performance.mediapipeEnabled"
                    @click="applyMediaPipePreset('quality')"
                  >高精度模式</el-button>
                </div>
              </div>

              <!-- v3.8.0: 高级选项 -->
              <el-collapse class="border-slate-700">
                <el-collapse-item title="高级选项" name="mp-advanced">
                  <div class="space-y-4">
                    <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                      <div>
                        <span class="text-gray-300">手部模型档位</span>
                        <div class="text-xs text-gray-500 mt-1">
                          0 = 轻量版（推理快、识别黑手套/俯视等困难场景几乎无效）；1 = 完整版（推理慢约 1.8 倍、对手套/握工具等非标场景识别率显著提升）
                        </div>
                      </div>
                      <el-radio-group
                        v-model="store.performance.mediapipeModelComplexity"
                        size="small"
                        :disabled="!store.performance.mediapipeEnabled"
                        @change="savePerformanceSettings"
                      >
                        <el-radio-button :value="0">轻量(0)</el-radio-button>
                        <el-radio-button :value="1">完整(1)</el-radio-button>
                      </el-radio-group>
                    </div>
                    <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                      <div>
                        <span class="text-gray-300">跟踪置信度</span>
                        <div class="text-xs text-gray-500 mt-1">值越低骨架越"粘"住手不易丢失但更抖；首检后跟随期使用</div>
                      </div>
                      <div class="flex items-center gap-2">
                        <el-slider
                          v-model="store.performance.mediapipeTrackConfidence"
                          :min="0.05"
                          :max="0.95"
                          :step="0.05"
                          :disabled="!store.performance.mediapipeEnabled"
                          @change="savePerformanceSettings"
                          style="width: 140px"
                          :show-tooltip="true"
                          :format-tooltip="v => v.toFixed(2)"
                        />
                        <span class="text-gray-400 text-xs w-8 text-right">{{ store.performance.mediapipeTrackConfidence.toFixed(2) }}</span>
                      </div>
                    </div>
                  </div>
                </el-collapse-item>
              </el-collapse>

              <!-- v3.8.0: 工业专用手部模型 (二段管线) -->
              <el-collapse class="border-slate-700">
                <el-collapse-item name="mp-industrial">
                  <template #title>
                    <div class="flex items-center gap-2">
                      <span class="text-gray-200 font-bold">工业专用手部模型</span>
                      <el-tag
                        v-if="mediapipeStatusState === 'baseline'"
                        size="small"
                        type="info"
                      >未启用</el-tag>
                      <el-tag
                        v-else-if="mediapipeStatusState === 'active'"
                        size="small"
                        type="success"
                      >已启用</el-tag>
                      <el-tag
                        v-else-if="mediapipeStatusState === 'pending'"
                        size="small"
                        type="warning"
                      >待加载</el-tag>
                      <el-tag
                        v-else
                        size="small"
                        type="danger"
                      >异常</el-tag>
                    </div>
                  </template>
                  <div class="space-y-4">
                    <el-alert
                      :title="mediapipeStatusMessage"
                      :type="mediapipeStatusAlertType"
                      :closable="false"
                      show-icon
                    >
                      <template #default>
                        <div class="text-xs text-gray-300 mt-1">
                          基础 MediaPipe 在黑手套俯视、握工具遮挡等"工业死区"场景识别率为零。配一个本地训练的 hand-detector
                          (YOLO .pt) 即可启用二段管线 (YOLO 框出手部位置 → ROI 切割 → MediaPipe 画骨架)。
                          训好的模型放本地任意路径，填到下方即可，<strong>不需要重启</strong>。
                        </div>
                      </template>
                    </el-alert>

                    <div class="p-3 bg-slate-900 rounded border border-slate-800">
                      <div class="text-gray-300 mb-2">模型文件路径</div>
                      <div class="text-xs text-gray-500 mb-2">
                        本地 .pt 文件绝对路径（例如 <code>D:/tianjun/models/industrial_hand.pt</code>）。留空 = 关闭二段管线，走基础 MediaPipe
                      </div>
                      <div class="flex gap-2">
                        <el-input
                          v-model="store.performance.mediapipeHandDetectorPath"
                          placeholder="留空走基础模式 / 填本地 .pt 路径启用专用模型"
                          :disabled="!store.performance.mediapipeEnabled"
                          clearable
                          @change="savePerformanceSettings"
                        />
                        <el-button
                          size="default"
                          :disabled="!store.performance.mediapipeEnabled"
                          @click="savePerformanceSettings"
                        >应用</el-button>
                      </div>
                    </div>

                    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                      <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                        <div>
                          <span class="text-gray-300">模型格式</span>
                          <div class="text-xs text-gray-500 mt-1">YOLOv5 老格式选 v5，其余选 v8</div>
                        </div>
                        <el-radio-group
                          v-model="store.performance.mediapipeHandDetectorKind"
                          size="small"
                          :disabled="!store.performance.mediapipeEnabled || !store.performance.mediapipeHandDetectorPath"
                          @change="savePerformanceSettings"
                        >
                          <el-radio-button value="v8">v8</el-radio-button>
                          <el-radio-button value="v5">v5</el-radio-button>
                        </el-radio-group>
                      </div>
                      <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                        <div>
                          <span class="text-gray-300">检测置信度</span>
                          <div class="text-xs text-gray-500 mt-1">框越多→精度越低</div>
                        </div>
                        <div class="flex items-center gap-2">
                          <el-slider
                            v-model="store.performance.mediapipeHandDetectorConf"
                            :min="0.05"
                            :max="0.9"
                            :step="0.05"
                            :disabled="!store.performance.mediapipeEnabled || !store.performance.mediapipeHandDetectorPath"
                            @change="savePerformanceSettings"
                            style="width: 120px"
                            :show-tooltip="true"
                            :format-tooltip="v => v.toFixed(2)"
                          />
                          <span class="text-gray-400 text-xs w-8 text-right">{{ store.performance.mediapipeHandDetectorConf.toFixed(2) }}</span>
                        </div>
                      </div>
                      <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                        <div>
                          <span class="text-gray-300">NMS 阈值</span>
                          <div class="text-xs text-gray-500 mt-1">两个框重叠超此比例时去重</div>
                        </div>
                        <div class="flex items-center gap-2">
                          <el-slider
                            v-model="store.performance.mediapipeHandDetectorIou"
                            :min="0.1"
                            :max="0.9"
                            :step="0.05"
                            :disabled="!store.performance.mediapipeEnabled || !store.performance.mediapipeHandDetectorPath"
                            @change="savePerformanceSettings"
                            style="width: 120px"
                            :show-tooltip="true"
                            :format-tooltip="v => v.toFixed(2)"
                          />
                          <span class="text-gray-400 text-xs w-8 text-right">{{ store.performance.mediapipeHandDetectorIou.toFixed(2) }}</span>
                        </div>
                      </div>
                      <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                        <div>
                          <span class="text-gray-300">输入尺寸</span>
                          <div class="text-xs text-gray-500 mt-1">YOLO resize 后边长（640 兼顾速度/精度）</div>
                        </div>
                        <el-input-number
                          v-model="store.performance.mediapipeHandDetectorImgsz"
                          size="small"
                          :min="320"
                          :max="1280"
                          :step="32"
                          :precision="0"
                          :disabled="!store.performance.mediapipeEnabled || !store.performance.mediapipeHandDetectorPath"
                          @change="savePerformanceSettings"
                          style="width: 110px"
                        />
                      </div>
                      <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800 md:col-span-2">
                        <div>
                          <span class="text-gray-300">ROI 外扩比例</span>
                          <div class="text-xs text-gray-500 mt-1">手指容易被 YOLO 框切到 → 把框向外扩这个比例后再喂给 MediaPipe</div>
                        </div>
                        <div class="flex items-center gap-2">
                          <el-slider
                            v-model="store.performance.mediapipeHandRoiPad"
                            :min="0"
                            :max="1"
                            :step="0.05"
                            :disabled="!store.performance.mediapipeEnabled || !store.performance.mediapipeHandDetectorPath"
                            @change="savePerformanceSettings"
                            style="width: 180px"
                            :show-tooltip="true"
                            :format-tooltip="v => v.toFixed(2)"
                          />
                          <span class="text-gray-400 text-xs w-12 text-right">{{ (store.performance.mediapipeHandRoiPad * 100).toFixed(0) }}%</span>
                        </div>
                      </div>
                    </div>
                  </div>
                </el-collapse-item>
              </el-collapse>

              <el-alert
                title="说明"
                type="info"
                :closable="false"
                show-icon
              >
                <template #default>
                  <div class="text-xs text-gray-300 mt-1">
                    <p>• MediaPipe 由 Google 开发，提供轻量级人体姿态和手部关键点检测</p>
                    <p>• 仅在画面显示中叠加骨架效果，<strong>不参与检测判定、不影响录制</strong></p>
                    <p>• 性能参考（CPU 处理）：省 CPU 模式约 18-22ms/帧；高精度模式约 30-38ms/帧；专用模型模式约 13-16ms/帧（更快）</p>
                    <p>• 需要安装 mediapipe 包：<code>pip install mediapipe</code></p>
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
                  :min="0" 
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
                  :min="0" 
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
                  :min="0" 
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

      <!-- Plugin Management Tab -->
      <el-tab-pane label="插件管理">
        <div class="space-y-6 p-4">
          <el-alert
            title="更换或激活插件后，需要重启应用才能让插件代码生效。"
            type="warning"
            :closable="false"
            show-icon
          />

          <el-card shadow="never" class="bg-slate-800 border-slate-700">
            <template #header>
              <div class="flex items-center justify-between">
                <div class="flex items-center gap-2">
                  <el-icon class="text-purple-400"><Lightning /></el-icon>
                  <span class="font-bold text-white">插件管理</span>
                  <el-tag v-if="pluginStore.activeCustomerCode" type="success" size="small">当前 active: {{ pluginStore.activeCustomerCode }}</el-tag>
                  <el-tag v-if="pluginStore.licenseMismatch" type="danger" size="small">license 与激活插件 customer_code 不一致</el-tag>
                </div>
                <el-button size="small" :loading="pluginStore.loading" @click="pluginStore.fetchAll()">
                  <el-icon class="mr-1"><Refresh /></el-icon>刷新
                </el-button>
              </div>
            </template>

            <div class="grid grid-cols-1 lg:grid-cols-3 gap-4 mb-4">
              <div class="p-3 bg-slate-900 rounded border border-slate-800">
                <div class="text-xs text-gray-500 mb-1">当前 License 客户码</div>
                <div class="text-cyan-300 font-mono">{{ pluginStore.licenseCustomer || '未缓存' }}</div>
              </div>
              <div class="p-3 bg-slate-900 rounded border border-slate-800">
                <div class="text-xs text-gray-500 mb-1">已安装插件</div>
                <div class="text-white text-lg font-bold">{{ pluginStore.items.length }}</div>
              </div>
              <div class="p-3 bg-slate-900 rounded border border-slate-800">
                <div class="text-xs text-gray-500 mb-1">安装包</div>
                <el-upload
                  :auto-upload="false"
                  :show-file-list="false"
                  accept=".tjvplugin"
                  :on-change="onPluginFileChange"
                >
                  <el-button type="primary" :loading="pluginStore.uploading">上传 .tjvplugin</el-button>
                </el-upload>
              </div>
            </div>

            <el-table :data="pluginStore.items" stripe size="small" class="bg-transparent" v-loading="pluginStore.loading">
              <el-table-column prop="name" label="插件" min-width="160">
                <template #default="{ row }">
                  <div class="font-bold text-white">{{ row.name }}</div>
                  <div class="text-xs text-gray-500 font-mono">{{ row.customer_code }}</div>
                </template>
              </el-table-column>
              <el-table-column prop="plugin_version" label="版本" width="110" />
              <el-table-column label="状态" width="150">
                <template #default="{ row }">
                  <el-tag :type="row.is_active ? 'success' : 'info'" size="small">
                    {{ row.is_active ? 'active' : row.status }}
                  </el-tag>
                  <div class="text-xs text-gray-500 mt-1">{{ row.runtime_status || 'stopped' }}</div>
                </template>
              </el-table-column>
              <el-table-column label="最近错误" min-width="180">
                <template #default="{ row }">
                  <span v-if="row.last_error_code" class="text-red-400">
                    {{ row.last_error_code }}：{{ row.last_error_message }}
                  </span>
                  <span v-else class="text-gray-500">无</span>
                </template>
              </el-table-column>
              <el-table-column label="操作" width="230" fixed="right">
                <template #default="{ row }">
                  <el-button v-if="!row.is_active" size="small" type="success" @click="activateInstalledPlugin(row)">
                    激活
                  </el-button>
                  <el-button v-else size="small" type="warning" @click="deactivateInstalledPlugin(row)">
                    停用
                  </el-button>
                  <el-button size="small" type="danger" @click="removeInstalledPlugin(row)">
                    卸载
                  </el-button>
                </template>
              </el-table-column>
            </el-table>
            <div v-if="!pluginStore.hasAny && !pluginStore.loading" class="text-center text-gray-500 text-sm py-6">
              暂无插件，请上传已签名的 .tjvplugin 安装包。
            </div>
            <div v-if="pluginStore.lastError" class="mt-3 text-xs text-red-400">
              {{ pluginStore.lastError }}
            </div>
          </el-card>
        </div>
      </el-tab-pane>

      <!-- 账号鉴权 Tab (v3.10.0 用户系统) -->
      <el-tab-pane label="账号鉴权">
        <AuthPanel />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue';
import { useSystemStore } from '@/store/useSystemStore';
import { useProjectStore } from '@/store/useProjectStore';
import { usePluginStore } from '@/store/usePluginStore';
import { Top, Monitor, Box, Bell, Edit, VideoCamera, Cpu, Refresh, DataLine, Lightning, Aim, User, Plus, Close } from '@element-plus/icons-vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { getProjectDetail } from '@/api/project';
import api from '@/api/index';
import AuthPanel from './AuthPanel.vue';

const store = useSystemStore();
const projectStore = useProjectStore();
const pluginStore = usePluginStore();

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
  enabled: false,
  processNoise: 0.03,
  measurementNoise: 0.1,
  maxMissingFrames: 5
});

async function loadPlugins() {
  try {
    await pluginStore.fetchAll();
  } catch {
    ElMessage.error('加载插件列表失败：' + (pluginStore.lastError || '未知错误'));
  }
}

async function onPluginFileChange(uploadFile) {
  const raw = uploadFile?.raw;
  if (!raw) return;
  try {
    await pluginStore.install(raw);
    ElMessage.success('插件安装成功，激活后重启生效');
  } catch {
    ElMessage.error(`插件安装失败：${pluginStore.lastError || '未知错误'}`);
  }
}

async function activateInstalledPlugin(row) {
  try {
    await pluginStore.activate(row.customer_code);
    ElMessage.success('插件已激活，重启后生效');
  } catch (e) {
    ElMessage.error('激活失败：' + (e?.response?.data?.detail?.message || e?.message || ''));
  }
}

async function deactivateInstalledPlugin(row) {
  try {
    await pluginStore.deactivate(row.customer_code);
    ElMessage.success('插件已停用，重启后生效');
  } catch (e) {
    ElMessage.error('停用失败：' + (e?.response?.data?.detail?.message || e?.message || ''));
  }
}

async function removeInstalledPlugin(row) {
  await ElMessageBox.confirm(
    `确认卸载插件 ${row.name}？插件业务数据表会保留。`,
    '卸载插件',
    { type: 'warning' }
  );
  try {
    await pluginStore.remove(row.customer_code);
    ElMessage.success('插件已卸载');
  } catch (e) {
    ElMessage.error('卸载失败：' + (e?.response?.data?.detail?.message || e?.message || ''));
  }
}

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
    await api.post('/source/stream/config', {
      frame_limit_enabled: store.performance.frameLimitEnabled,
      target_stream_fps: store.performance.targetStreamFps,
      use_half: store.performance.halfPrecision,
      mediapipe_enabled: store.performance.mediapipeEnabled,
      mediapipe_pose: store.performance.mediapipePose,
      mediapipe_hands: store.performance.mediapipeHands,
      mediapipe_confidence: store.performance.mediapipeConfidence,
      mediapipe_interval: store.performance.mediapipeInterval,
      mediapipe_model_complexity: store.performance.mediapipeModelComplexity,
      mediapipe_track_confidence: store.performance.mediapipeTrackConfidence,
      // v3.8.0 工业 hand-detector
      mediapipe_hand_detector_path: store.performance.mediapipeHandDetectorPath || '',
      mediapipe_hand_detector_kind: store.performance.mediapipeHandDetectorKind || 'v8',
      mediapipe_hand_detector_conf: store.performance.mediapipeHandDetectorConf,
      mediapipe_hand_detector_iou: store.performance.mediapipeHandDetectorIou,
      mediapipe_hand_detector_imgsz: store.performance.mediapipeHandDetectorImgsz,
      mediapipe_hand_roi_pad: store.performance.mediapipeHandRoiPad
    });
    ElMessage.success('性能设置已保存');
    // 保存后立即刷新二段状态（让徽章动）
    await refreshMediaPipeStatus();
  } catch (e) {
    console.error('同步性能设置到后端失败:', e);
  }
};

// 加载性能设置
const loadPerformanceSettings = async () => {
  store.loadPerformanceSettings();
  try {
    const res = await api.get('/source/stream/config');
    if (res.data) {
      store.performance.frameLimitEnabled = res.data.frame_limit_enabled;
      store.performance.targetStreamFps = res.data.target_stream_fps;
      if (res.data.use_half !== undefined) {
        store.performance.halfPrecision = res.data.use_half;
      }
      if (res.data.mediapipe_enabled !== undefined) {
        store.performance.mediapipeEnabled = res.data.mediapipe_enabled;
      }
      if (res.data.mediapipe_pose !== undefined) {
        store.performance.mediapipePose = res.data.mediapipe_pose;
      }
      if (res.data.mediapipe_hands !== undefined) {
        store.performance.mediapipeHands = res.data.mediapipe_hands;
      }
      if (res.data.mediapipe_confidence !== undefined) {
        store.performance.mediapipeConfidence = res.data.mediapipe_confidence;
      }
      if (res.data.mediapipe_interval !== undefined) {
        store.performance.mediapipeInterval = res.data.mediapipe_interval;
      }
      if (res.data.mediapipe_model_complexity !== undefined) {
        store.performance.mediapipeModelComplexity = res.data.mediapipe_model_complexity;
      }
      if (res.data.mediapipe_track_confidence !== undefined) {
        store.performance.mediapipeTrackConfidence = res.data.mediapipe_track_confidence;
      }
      if (res.data.mediapipe_hand_detector_path !== undefined) {
        store.performance.mediapipeHandDetectorPath = res.data.mediapipe_hand_detector_path || '';
      }
      if (res.data.mediapipe_hand_detector_kind !== undefined) {
        store.performance.mediapipeHandDetectorKind = res.data.mediapipe_hand_detector_kind || 'v8';
      }
      if (res.data.mediapipe_hand_detector_conf !== undefined) {
        store.performance.mediapipeHandDetectorConf = res.data.mediapipe_hand_detector_conf;
      }
      if (res.data.mediapipe_hand_detector_iou !== undefined) {
        store.performance.mediapipeHandDetectorIou = res.data.mediapipe_hand_detector_iou;
      }
      if (res.data.mediapipe_hand_detector_imgsz !== undefined) {
        store.performance.mediapipeHandDetectorImgsz = res.data.mediapipe_hand_detector_imgsz;
      }
      if (res.data.mediapipe_hand_roi_pad !== undefined) {
        store.performance.mediapipeHandRoiPad = res.data.mediapipe_hand_roi_pad;
      }
      // v3.8.0 二段管线状态
      if (res.data.mediapipe_two_stage_status) {
        mediapipeStatusState.value = res.data.mediapipe_two_stage_status.state || 'baseline';
        mediapipeStatusMessage.value = res.data.mediapipe_two_stage_status.message || '';
      }
    }
  } catch (e) {
    console.error('加载后端性能设置失败:', e);
  }
};

// v3.8.0 二段管线状态轮询（只在面板展开时调一次）
const mediapipeStatusState = ref('baseline');     // baseline / active / pending / path_invalid / load_failed
const mediapipeStatusMessage = ref('未启用专用手部模型 (走基础 MediaPipe)');
const mediapipeStatusAlertType = computed(() => {
  const s = mediapipeStatusState.value;
  if (s === 'active') return 'success';
  if (s === 'baseline' || s === 'pending') return 'info';
  return 'error';
});
const refreshMediaPipeStatus = async () => {
  try {
    const res = await api.get('/source/stream/config');
    if (res.data?.mediapipe_two_stage_status) {
      mediapipeStatusState.value = res.data.mediapipe_two_stage_status.state || 'baseline';
      mediapipeStatusMessage.value = res.data.mediapipe_two_stage_status.message || '';
    }
  } catch (e) {
    // 静默
  }
};

// v3.8.0 一键预设：省 CPU 模式 / 高精度模式（友商同款）
const applyMediaPipePreset = (mode) => {
  if (mode === 'eco') {
    store.performance.mediapipeModelComplexity = 0;
    store.performance.mediapipeConfidence = 0.7;
    store.performance.mediapipeTrackConfidence = 0.5;
    ElMessage.success('已切换到「省 CPU 模式」');
  } else if (mode === 'quality') {
    store.performance.mediapipeModelComplexity = 1;
    store.performance.mediapipeConfidence = 0.5;
    store.performance.mediapipeTrackConfidence = 0.5;
    ElMessage.success('已切换到「高精度模式」');
  }
  savePerformanceSettings();
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
        // Step 8 (feat/multi-model-roi-link): 多模型场景透出已重载的 slot 列表
        const reloaded = res.data.reloaded_models;
        if (Array.isArray(reloaded) && reloaded.length >= 2) {
          ElMessage.success(
            `${res.data.message} (重载: ${reloaded.join(', ')})`
          );
        } else {
          ElMessage.success(res.data.message);
        }
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

// ========== 画面变换（按通道） ==========
const transformChannel = ref(0);
const transformTotalChannels = ref(1);
const transformLoading = ref(false);
const transformSaving = ref(false);
const transformForm = reactive({ rotation: 0, flip_h: false, flip_v: false });

async function loadTransformTotalChannels() {
  try {
    const res = await api.get('/workstations');
    transformTotalChannels.value = res?.data?.channel_count || 1;
  } catch {
    transformTotalChannels.value = 1;
  }
}

async function loadTransformConfig() {
  transformLoading.value = true;
  try {
    const res = await api.get(`/source/transform/config?channel=${transformChannel.value}`);
    transformForm.rotation = res?.data?.rotation || 0;
    transformForm.flip_h = !!res?.data?.flip_h;
    transformForm.flip_v = !!res?.data?.flip_v;
  } catch (e) {
    ElMessage.error('加载画面变换失败: ' + (e?.response?.data?.detail || e?.message || ''));
  } finally {
    transformLoading.value = false;
  }
}

async function saveTransformConfig() {
  transformSaving.value = true;
  try {
    await api.post(`/source/transform/config?channel=${transformChannel.value}`, {
      rotation: transformForm.rotation || 0,
      flip_h: !!transformForm.flip_h,
      flip_v: !!transformForm.flip_v,
    });
    ElMessage.success(`工位 ${transformChannel.value + 1} 画面变换已保存并生效`);
  } catch (e) {
    ElMessage.error('保存失败: ' + (e?.response?.data?.detail || e?.message || ''));
  } finally {
    transformSaving.value = false;
  }
}

onMounted(async () => {
  store.loadSettings();
  loadPerformanceSettings();
  refreshGpuList();
  loadCurrentDevice();
  loadKalmanConfig();
  loadPlugins();
  loadTransformTotalChannels();
  loadTransformConfig();
  
  // 从当前项目加载检测配置（包括自定义提示框）
  // v3.8.x: 不再守 `if (res.data?.detection_config)` — DB null 也要进 store,
  // store 内部会用 localStorage 兜底 + 自动回写 DB. 设置页是改这些配置的主入口,
  // 必须确保 currentProjectId 被绑定, 否则用户改的颜色/线宽全进不了 DB.
  if (projectStore.currentProjectId) {
    try {
      const res = await getProjectDetail(projectStore.currentProjectId);
      store.setCurrentProjectId(projectStore.currentProjectId);
      store.loadDetectionFromProject(res.data?.detection_config || null, projectStore.currentProjectId);
    } catch (e) {
      console.error('加载项目检测配置失败:', e);
    }
  } else {
    // 没有项目时也走 store, 它内部读 localStorage; 无 projectId 不会回写 DB
    store.loadDetectionFromProject(null);
  }
});
</script>
