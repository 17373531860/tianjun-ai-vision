<template>
  <div class="p-6 h-full overflow-y-auto">
    <h2 class="text-2xl font-bold mb-6 border-l-4 border-tech-blue pl-3 text-white">系统设置</h2>

    <el-tabs type="border-card" class="bg-gray-800 border-gray-700">
      
      <!-- Display Settings Tab -->
      <el-tab-pane label="显示设置">
        <div class="space-y-6 p-4">
          <!-- Navbar Settings -->
          <el-card shadow="never" class="bg-slate-800 border-slate-700">
            <template #header>
              <div class="flex items-center gap-2">
                <el-icon class="text-tech-blue"><Top /></el-icon>
                <span class="font-bold text-white">顶部导航栏 (Navbar)</span>
              </div>
            </template>
            <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <span class="text-gray-300">项目选择板块</span>
                <el-switch v-model="store.display.navbar.projectSelector" @change="saveDisplaySettings" />
              </div>
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <span class="text-gray-300">检测员姓名</span>
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
            </div>
          </el-card>

          <!-- Monitor Settings -->
          <el-card shadow="never" class="bg-slate-800 border-slate-700">
            <template #header>
              <div class="flex items-center gap-2">
                <el-icon class="text-tech-blue"><Monitor /></el-icon>
                <span class="font-bold text-white">实时监控 (Monitor)</span>
              </div>
            </template>
            <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <span class="text-gray-300">底部步骤抓拍/条</span>
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
                    <el-option label="画面正中间" value="center" />
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
                    <el-option label="画面正中间" value="center" />
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
                      <el-option label="画面正中间" value="center" />
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
    </el-tabs>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue';
import { useSystemStore } from '@/store/useSystemStore';
import { Top, Monitor, Box, Bell } from '@element-plus/icons-vue';
import { ElMessage } from 'element-plus';

const store = useSystemStore();

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

onMounted(() => {
  store.loadSettings();
});
</script>
