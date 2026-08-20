<template>
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
</template>

<script setup>
import { onMounted, watch } from 'vue';
import { useSystemStore } from '@/store/useSystemStore';
import { useProjectStore } from '@/store/useProjectStore';
import { Box, Bell } from '@element-plus/icons-vue';
import { ElMessage } from 'element-plus';
import { getProjectDetail } from '@/api/project';
import { dbg } from '@/utils/debug';

const store = useSystemStore();
const projectStore = useProjectStore();

const saveDetectionSettings = () => {
  dbg('settings.ops', '保存检测显示设置');
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

// 从当前项目加载检测配置（包括自定义提示框）
// v3.8.x: 不再守 `if (res.data?.detection_config)` — DB null 也要进 store,
// store 内部会用 localStorage 兜底 + 自动回写 DB. 设置页是改这些配置的主入口,
// 必须确保 currentProjectId 被绑定, 否则用户改的颜色/线宽全进不了 DB.
const loadProjectDetection = async () => {
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
};

onMounted(async () => {
  await loadProjectDetection();
});

// 切项目热刷新: 停在设置页时 Navbar 切项目, 检测框/提示框配置实时跟随当前项目
watch(() => projectStore.currentProjectId, () => { loadProjectDetection(); });
</script>
