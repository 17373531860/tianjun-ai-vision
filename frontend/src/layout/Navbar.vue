<template>
  <header class="h-16 bg-[#0f172a] border-b border-cyan-900 flex items-center justify-between px-4 text-white shadow-lg shadow-cyan-900/20">
    <!-- Left: Logo & Menu -->
    <div class="flex items-center gap-4">
      <slot name="left"></slot>
      <div class="text-xl font-bold bg-clip-text text-transparent bg-gradient-to-r from-cyan-400 to-blue-500">
        {{ store.display.brandName || $t('navbar.title') }}
      </div>
      <div class="h-8 w-px bg-gray-700 mx-2"></div>
      
      <!-- Project Info Block -->
      <div v-if="store.display.navbar.projectSelector" class="flex items-center gap-6 text-sm">
        <div class="flex items-center gap-2 bg-slate-800/50 px-3 py-1.5 rounded border border-slate-700">
          <span class="text-cyan-400 font-bold">{{ $t('navbar.project') }}:</span>
          <el-select 
            v-model="selectedProjectId" 
            placeholder="选择项目" 
            size="small" 
            class="w-40"
            :disabled="store.isDetecting"
            @change="handleProjectChange"
          >
            <el-option 
              v-for="project in projectList" 
              :key="project.id" 
              :label="project.name" 
              :value="project.id"
            />
          </el-select>
          <button 
            @click="handleNavToProject"
            :disabled="store.isDetecting"
            class="text-xs bg-cyan-700 hover:bg-cyan-600 disabled:bg-gray-600 disabled:cursor-not-allowed px-2 py-0.5 rounded ml-2 cursor-pointer"
          >
            {{ $t('navbar.select') }}
          </button>
        </div>
        
        <div class="hidden md:flex items-center gap-4 text-gray-300">
          <span v-if="store.display.navbar.inspector">{{ $t('navbar.inspector') }}: <span class="text-white">{{ store.display.inspectorName || '未设置' }}</span></span>
          <span v-if="store.display.navbar.deviceId">{{ $t('navbar.deviceId') }}: <span class="text-white">{{ store.display.deviceNumber || '未设置' }}</span></span>
        </div>
      </div>
    </div>

    <!-- Right: Status & System -->
    <div class="flex items-center gap-6">
      <!-- Status Indicators -->
      <div class="flex items-center gap-4 text-sm font-mono">
        <div v-if="store.display.navbar.mode" class="flex gap-2">
          <span class="text-cyan-400">{{ $t('navbar.mode') }}:</span> 
          <span>{{ modeLabel }}</span>
        </div>
        <div v-if="store.display.navbar.status" class="flex gap-2">
          <span class="text-cyan-400">{{ $t('navbar.status') }}:</span> 
          <span :class="projectStore.isRunning ? 'text-status-ok' : 'text-gray-400'" class="font-bold">
            {{ projectStore.isRunning ? $t('navbar.running') : '待机' }}
          </span>
        </div>
        <div v-if="store.display.navbar.runtime" class="flex gap-2">
          <span class="text-cyan-400">{{ $t('navbar.runTime') }}:</span> 
          <span>{{ runTimeStr }}</span>
        </div>
      </div>

      <div class="h-8 w-px bg-gray-700"></div>

      <!-- Icon Actions -->
      <div class="flex items-center gap-3">
        <!-- Settings Dropdown -->
        <el-dropdown trigger="click" @command="handleCommand" :disabled="store.isDetecting">
          <el-icon 
            class="transition-colors" 
            :class="store.isDetecting ? 'cursor-not-allowed text-gray-600' : 'cursor-pointer text-gray-300 hover:text-cyan-400'" 
            :size="20"
          ><Setting /></el-icon>
          <template #dropdown>
            <el-dropdown-menu class="bg-slate-800 border-slate-700">
              <el-dropdown-item command="auto_save">
                <div class="flex items-center justify-between w-full min-w-[140px]">
                  <span>自动保存</span>
                  <el-icon v-if="autoSaveEnabled" class="text-green-400 ml-2"><Check /></el-icon>
                </div>
              </el-dropdown-item>
              <el-dropdown-item divided command="lang_zh">简体中文</el-dropdown-item>
              <el-dropdown-item command="lang_zh_tw">繁體中文</el-dropdown-item>
              <el-dropdown-item command="lang_en">English</el-dropdown-item>
              <el-dropdown-item command="lang_jp">日本語</el-dropdown-item>
              <el-dropdown-item command="lang_kr">한국어</el-dropdown-item>
              <el-dropdown-item divided command="logout" class="text-red-400 hover:text-red-300">{{ $t('navbar.logout') }}</el-dropdown-item>
              <el-dropdown-item command="cancel">{{ $t('navbar.cancel') }}</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>

        <el-avatar :size="32" class="bg-cyan-900 text-cyan-200">User</el-avatar>
      </div>
    </div>
  </header>
</template>

<script setup>
import { useSystemStore } from '@/store/useSystemStore';
import { useProjectStore } from '@/store/useProjectStore';
import { useRouter } from 'vue-router';
import { computed, ref, onMounted, onUnmounted, watch } from 'vue';
import { Setting, UserFilled, Check } from '@element-plus/icons-vue';
import { useI18n } from 'vue-i18n';
import { ElMessage, ElMessageBox } from 'element-plus';
import { getProjects, getProjectDetail, activateProject } from '@/api/project';

const store = useSystemStore();
const projectStore = useProjectStore();
const router = useRouter();
const { locale, t } = useI18n();

// 项目列表和选择
const projectList = ref([]);
const selectedProjectId = ref(null);

// 自动保存功能
const autoSaveEnabled = ref(false);
const AUTO_SAVE_KEY = 'auto_save_settings';

// 加载自动保存设置
const loadAutoSaveSettings = () => {
  const saved = localStorage.getItem(AUTO_SAVE_KEY);
  if (saved) {
    const settings = JSON.parse(saved);
    autoSaveEnabled.value = settings.enabled || false;
    // 恢复 store 中的输入源信息（防止被覆盖）
    if (settings.sourceType && settings.sourceValue) {
      store.setLastSource(settings.sourceType, settings.sourceValue);
    }
    return settings;
  }
  return null;
};

// 保存当前选择
const saveCurrentSelection = () => {
  if (autoSaveEnabled.value) {
    // 优先使用已保存的值，避免被空值覆盖
    const existingSaved = localStorage.getItem(AUTO_SAVE_KEY);
    const existing = existingSaved ? JSON.parse(existingSaved) : {};
    
    const settings = {
      enabled: true,
      projectId: projectStore.currentProjectId || existing.projectId,
      sourceType: store.lastSourceType || existing.sourceType || null,
      sourceValue: store.lastSourceValue || existing.sourceValue || null
    };
    localStorage.setItem(AUTO_SAVE_KEY, JSON.stringify(settings));
  }
};

// 切换自动保存
const toggleAutoSave = () => {
  autoSaveEnabled.value = !autoSaveEnabled.value;
  if (autoSaveEnabled.value) {
    saveCurrentSelection();
    ElMessage.success('已开启自动保存，下次将自动恢复当前选择');
  } else {
    localStorage.removeItem(AUTO_SAVE_KEY);
    ElMessage.info('已关闭自动保存');
  }
};

// 恢复上次选择
const restoreLastSelection = async (settings) => {
  console.log('[AutoRestore] 开始恢复设置:', settings);
  
  if (settings.projectId && projectList.value.length > 0) {
    // 检查项目是否存在
    const projectExists = projectList.value.some(p => p.id === settings.projectId);
    console.log('[AutoRestore] 项目存在:', projectExists, '项目ID:', settings.projectId);
    
    if (projectExists) {
      await handleProjectChange(settings.projectId);
      
      // 等待一下让后端准备好
      await new Promise(resolve => setTimeout(resolve, 1000));
      
      // 保存输入源信息到 store，让输入源设置页面恢复
      console.log('[AutoRestore] 输入源信息:', settings.sourceType, settings.sourceValue);
      
      if (settings.sourceType && settings.sourceValue !== null && settings.sourceValue !== undefined) {
        store.setLastSource(settings.sourceType, settings.sourceValue);
        
        // 自动启动输入源
        try {
          const api = (await import('@/api/index')).default;
          
          if (settings.sourceType === 'camera') {
            // 摄像头类型：sourceValue 是 deviceIndex
            const sourceStore = (await import('@/store/useSourceStore')).useSourceStore();
            sourceStore.loadConfig();
            
            console.log('[AutoRestore] 正在启动摄像头:', settings.sourceValue);
            await api.post('/source/start', {
              type: 'camera',
              device_index: settings.sourceValue,
              resolution: sourceStore.cameraSettings?.resolution || '1280x720',
              fps: sourceStore.cameraSettings?.fps || 30
            });
            sourceStore.setStreaming(true);
            console.log('[AutoRestore] 摄像头自动启动成功');
          } else if (settings.sourceType === 'video') {
            // 视频类型：sourceValue 是视频路径
            const sourceStore = (await import('@/store/useSourceStore')).useSourceStore();
            sourceStore.loadConfig();
            
            console.log('[AutoRestore] 正在启动视频:', settings.sourceValue);
            await api.post('/source/start', {
              type: 'video',
              path: settings.sourceValue,
              speed: sourceStore.videoSpeed || 1
            });
            sourceStore.setStreaming(true);
            console.log('[AutoRestore] 视频自动启动成功');
          }
          // 图片类型通常不需要自动启动
        } catch (e) {
          console.error('[AutoRestore] 自动启动输入源失败:', e);
        }
      } else {
        console.log('[AutoRestore] 没有保存的输入源信息');
      }
    }
  } else {
    console.log('[AutoRestore] 条件不满足 - projectId:', settings.projectId, '项目数:', projectList.value.length);
  }
};

// 加载项目列表
const loadProjects = async () => {
  try {
    const res = await getProjects();
    projectList.value = res.data.items || [];
    
    // 优先使用后端 is_active 状态来同步当前项目
    const activeInBackend = projectList.value.find(p => p.is_active);
    
    if (activeInBackend) {
      if (projectStore.currentProjectId !== activeInBackend.id) {
        await handleProjectChange(activeInBackend.id);
      } else {
        selectedProjectId.value = activeInBackend.id;
      }
    } else if (projectStore.currentProjectId) {
      selectedProjectId.value = projectStore.currentProjectId;
    } else {
      const savedSettings = loadAutoSaveSettings();
      if (savedSettings && savedSettings.enabled) {
        await restoreLastSelection(savedSettings);
      }
    }
  } catch (err) {
    console.error('加载项目列表失败:', err);
  }
};

// 导航到项目管理页面
const handleNavToProject = () => {
  if (store.isDetecting) {
    ElMessage.warning('检测运行中，请先停止检测再切换页面');
    return;
  }
  router.push('/project');
};

// 处理项目切换
const handleProjectChange = async (projectId) => {
  if (!projectId) {
    projectStore.setCurrentProject(null);
    store.setCurrentProjectId(null);
    store.loadDetectionFromProject(null);
    saveCurrentSelection();
    return;
  }
  
  try {
    // 同步后端激活状态
    await activateProject(projectId).catch(() => {});
    
    const res = await getProjectDetail(projectId);
    const project = res.data;
    
    // 初始化默认值
    if (!project.counters_config) {
      project.counters_config = [
        { name: '合格总数', value: 0 },
        { name: '不良总数', value: 0 },
        { name: '总产量', value: 0 }
      ];
    }
    if (!project.steps_config) project.steps_config = [];
    if (!project.events_config) project.events_config = [];
    if (!project.pipeline_config) project.pipeline_config = {};
    
    // 从 pipeline_config 中提取配置到顶层（保持前端数据结构一致）
    const pipelineConfig = project.pipeline_config || {};
    if (project.sequence_order === undefined) {
      project.sequence_order = pipelineConfig.sequence_order || [];
    }
    if (project.detection_steps === undefined) {
      project.detection_steps = pipelineConfig.detection_steps || [];
    }
    if (project.custom_conditions === undefined) {
      project.custom_conditions = pipelineConfig.custom_conditions || [];
    }
    if (project.custom_based_on === undefined) {
      project.custom_based_on = pipelineConfig.custom_based_on || 'sequential';
    }
    
    projectStore.setCurrentProject(project);
    
    // 加载项目的检测框设置
    store.setCurrentProjectId(projectId);
    store.loadDetectionFromProject(project.detection_config);
    
    // 自动连接报警设备（如果有保存的配置）
    if (project.alarm_config?.port) {
      try {
        const api = (await import('@/api/index')).default;
        // 先同步报警配置
        await api.post('/alarm/config', {
          enabled: project.alarm_config.enabled ?? false,
          protocol: project.alarm_config.protocol ?? 'modbus_4color',
          test_mode: project.alarm_config.test_mode ?? false,
          custom_commands: project.alarm_config.custom_commands ?? {},
          triggers: project.alarm_config.triggers ?? {},
        });
        // 尝试连接设备
        await api.post('/alarm/connect', {
          port: project.alarm_config.port,
          baudrate: project.alarm_config.baudrate || 9600
        });
        console.log('报警设备自动连接成功');
      } catch (e) {
        console.warn('报警设备自动连接失败:', e.message);
      }
    }
    
    // 自动保存当前选择
    saveCurrentSelection();
    
    ElMessage.success(`已切换到项目: ${project.name}`);
  } catch (err) {
    console.error('加载项目详情失败:', err);
    ElMessage.error('切换项目失败');
  }
};

// 监听 store 中项目变化
watch(() => projectStore.currentProjectId, (newId) => {
  if (newId !== selectedProjectId.value) {
    selectedProjectId.value = newId;
  }
});

const handleCommand = (command) => {
  switch (command) {
    case 'auto_save':
      toggleAutoSave();
      break;
    case 'lang_zh':
      setLang('zh-CN', '语言已切换为简体中文');
      break;
    case 'lang_zh_tw':
      setLang('zh-TW', '語言已切換為繁體中文');
      break;
    case 'lang_en':
      setLang('en-US', 'Language switched to English');
      break;
    case 'lang_jp':
      setLang('ja-JP', '言語が日本語に切り替わりました');
      break;
    case 'lang_kr':
      setLang('ko-KR', '언어가 한국어로 변경되었습니다');
      break;
    case 'logout':
      ElMessageBox.confirm(t('navbar.exitConfirm'), t('navbar.exitTitle'), {
        confirmButtonText: t('navbar.logout'),
        cancelButtonText: t('navbar.cancel'),
        type: 'warning'
      }).then(() => {
        ElMessage.success(t('navbar.safeExit'));
        router.push('/login');
      }).catch(() => {});
      break;
    case 'cancel':
      break;
  }
};

const setLang = (lang, msg) => {
  locale.value = lang;
  store.setLanguage(lang);
  ElMessage.success(msg);
};

const modeLabel = computed(() => {
  const mode = projectStore.currentProject?.logic_mode;
  const map = {
    'sequential': t('mode.sequential'),
    'detection': t('mode.detection'),
    'custom': t('mode.custom')
  };
  return map[mode] || t('mode.undefined');
});

// Timer Logic
const runTimeSeconds = ref(0);
const runTimeStr = computed(() => {
  const h = Math.floor(runTimeSeconds.value / 3600);
  const m = Math.floor((runTimeSeconds.value % 3600) / 60);
  const s = runTimeSeconds.value % 60;
  return `${h.toString().padStart(2, '0')}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
});

let timerInterval;
onMounted(() => {
  // 加载项目列表
  loadProjects();
  
  // 启动计时器
  timerInterval = setInterval(() => {
    runTimeSeconds.value++;
  }, 1000);
  
  // 加载显示设置
  const saved = localStorage.getItem('display_settings');
  if (saved) {
    store.display = JSON.parse(saved);
  }
});

onUnmounted(() => {
  if (timerInterval) clearInterval(timerInterval);
});
</script>
