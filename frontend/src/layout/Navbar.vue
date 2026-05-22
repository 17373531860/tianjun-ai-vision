<template>
  <header class="h-16 bg-[#0f172a] border-b border-cyan-900 flex items-center px-4 text-white shadow-lg shadow-cyan-900/20">
    <!-- Left: Logo & Menu & Project -->
    <div class="flex items-center gap-4 flex-shrink-0">
      <slot name="left"></slot>
      <div v-if="store.display.navbar.brandName !== false" class="flex items-center gap-2">
        <img v-if="pluginTheme.logoUrl" :src="pluginTheme.logoUrl" alt="logo" class="h-8 w-auto object-contain" />
        <div class="text-xl font-bold bg-clip-text text-transparent bg-gradient-to-r from-cyan-400 to-blue-500">
          {{ pluginTheme.appTitle || store.display.brandName || $t('navbar.title') }}
        </div>
      </div>
      <div class="h-8 w-px bg-gray-700 mx-1"></div>
      
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
      </div>
    </div>

    <!-- Center: App Name (slightly left) -->
    <div class="flex-1 flex items-center justify-center -ml-24">
      <div v-if="store.display.navbar.appName !== false" class="app-name-wrapper">
        <div class="app-name-tech text-2xl font-semibold tracking-[0.25em]">
          {{ store.display.appName || '视觉AI行为引导系统' }}
          <span class="underline-bar"></span>
        </div>
      </div>
    </div>

    <!-- Right: Status & System -->
    <div class="flex items-center gap-6 flex-shrink-0">
      <!-- 作业员 & 设备编号 -->
      <div class="flex items-center gap-4 text-sm">
        <div v-if="store.display.navbar.inspector !== false && store.display.inspectorName" class="flex items-center gap-1.5 bg-slate-800/60 px-2.5 py-1 rounded border border-slate-700">
          <span class="text-gray-400 text-xs">作业员</span>
          <span class="text-cyan-300 font-medium">{{ store.display.inspectorName }}</span>
        </div>
        <div v-if="store.display.navbar.deviceId !== false && store.display.deviceNumber" class="flex items-center gap-1.5 bg-slate-800/60 px-2.5 py-1 rounded border border-slate-700">
          <span class="text-gray-400 text-xs">设备</span>
          <span class="text-white font-mono">{{ store.display.deviceNumber }}</span>
        </div>
      </div>

      <!-- 实时时间保留在顶部 -->
      <div class="flex items-center gap-4 text-sm font-mono">
        <div v-if="store.display.navbar.realtime !== false">
          <span class="text-white text-lg font-bold">{{ realTimeStr }}</span>
        </div>
      </div>

      <div class="h-8 w-px bg-gray-700"></div>

      <!-- Icon Actions -->
      <div class="flex items-center gap-3">
        <!-- Settings Dropdown -->
        <el-dropdown trigger="click" @command="handleCommand" :disabled="store.isDetecting">
          <el-icon 
            class="transition-colors text-[1.75rem]" 
            :class="store.isDetecting ? 'cursor-not-allowed text-gray-600' : 'cursor-pointer text-gray-300 hover:text-cyan-400'" 
          ><Setting /></el-icon>
          <template #dropdown>
            <el-dropdown-menu class="bg-slate-800 border-slate-700">
              <el-dropdown-item command="auto_save">
                <div class="flex items-center justify-between w-full min-w-[140px]">
                  <span>自动保存</span>
                  <el-icon v-if="autoSaveEnabled" class="text-green-400 ml-2 text-[1rem]"><Check /></el-icon>
                </div>
              </el-dropdown-item>
              <el-dropdown-item divided command="lang_zh">简体中文</el-dropdown-item>
              <el-dropdown-item command="lang_zh_tw">繁體中文</el-dropdown-item>
              <el-dropdown-item command="lang_en">English</el-dropdown-item>
              <el-dropdown-item command="lang_jp">日本語</el-dropdown-item>
              <el-dropdown-item command="lang_kr">한국어</el-dropdown-item>
              <el-dropdown-item divided command="developer_mode">
                <div class="flex items-center justify-between w-full min-w-[140px]">
                  <span>开发者模式</span>
                  <el-icon v-if="store.developerMode" class="text-green-400 ml-2 text-[1rem]"><Check /></el-icon>
                </div>
              </el-dropdown-item>
              <el-dropdown-item divided command="logout" class="text-red-400 hover:text-red-300">{{ $t('navbar.logout') }}</el-dropdown-item>
              <el-dropdown-item command="cancel">{{ $t('navbar.cancel') }}</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>

        <img src="/app-icon.png" alt="logo" class="w-10 h-10 rounded-full object-cover" />
      </div>
    </div>
  </header>
</template>

<script setup>
import { useSystemStore } from '@/store/useSystemStore';
import { useProjectStore } from '@/store/useProjectStore';
import { usePluginThemeStore } from '@/store/usePluginThemeStore';
import { useRouter } from 'vue-router';
import { computed, ref, onMounted, onUnmounted, watch } from 'vue';
import { Setting, UserFilled, Check } from '@element-plus/icons-vue';
import { useI18n } from 'vue-i18n';
import { ElMessage, ElMessageBox } from 'element-plus';
import { getProjects, getProjectDetail, activateProject } from '@/api/project';

const store = useSystemStore();
const projectStore = useProjectStore();
const pluginTheme = usePluginThemeStore();
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
              fps: sourceStore.cameraSettings?.fps || 60
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

const toggleDeveloperMode = async () => {
  if (store.developerMode) {
    store.setDeveloperMode(false);
    ElMessage.info('开发者模式已关闭');
    return;
  }
  try {
    const { value } = await ElMessageBox.prompt('请输入开发者密码', '开发者模式', {
      inputType: 'password',
      confirmButtonText: '确认',
      cancelButtonText: '取消',
      inputPlaceholder: '输入密码...',
    });
    if (value === 'tianjunKEJI') {
      store.setDeveloperMode(true);
      ElMessage.success('开发者模式已开启');
    } else {
      ElMessage.error('密码错误');
    }
  } catch {}
};

const handleCommand = (command) => {
  switch (command) {
    case 'auto_save':
      toggleAutoSave();
      break;
    case 'developer_mode':
      toggleDeveloperMode();
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
      }).then(async () => {
        // v3.8.2: 全屏 + 无边框模式下没有 Windows 标题栏的 × 按钮,
        // 这里统一接管"退出"为主进程优雅关机 (8 步), 而不是路由跳登录页.
        // 浏览器调试场景 (没有 electronAPI) 退回老行为, 跳登录页.
        if (window.electronAPI && typeof window.electronAPI.gracefulQuit === 'function') {
          ElMessage.success(t('navbar.safeExit'));
          try {
            await window.electronAPI.gracefulQuit();
          } catch (e) {
            console.warn('[Navbar] gracefulQuit 失败, 退回路由模式:', e.message);
            router.push('/login');
          }
        } else {
          ElMessage.success(t('navbar.safeExit'));
          router.push('/login');
        }
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

// 实时时间
const realTimeStr = ref('');
const updateRealTime = () => {
  const now = new Date();
  const y = now.getFullYear();
  const mo = (now.getMonth() + 1).toString().padStart(2, '0');
  const d = now.getDate().toString().padStart(2, '0');
  const h = now.getHours().toString().padStart(2, '0');
  const mi = now.getMinutes().toString().padStart(2, '0');
  const s = now.getSeconds().toString().padStart(2, '0');
  realTimeStr.value = `${y}-${mo}-${d} ${h}:${mi}:${s}`;
};

let timerInterval;
onMounted(async () => {
  console.log(`[⬛ Navbar] onMounted 开始 ${new Date().toLocaleTimeString()}`);

  try {
    console.log('[⬛ Navbar] 加载 display_settings...');
    const saved = localStorage.getItem('display_settings');
    if (saved) {
      store.display = JSON.parse(saved);
      console.log('[⬛ Navbar] ✓ display_settings 已恢复');
    }
  } catch (e) {
    console.error('[⬛ Navbar] ✗ display_settings 解析失败:', e);
  }

  store.loadDeveloperMode();

  try {
    console.log('[⬛ Navbar] 加载项目列表...');
    const t0 = Date.now();
    await loadProjects();
    console.log(`[⬛ Navbar] ✓ 项目列表加载完成 (${Date.now() - t0}ms), 数量: ${projectList.value.length}`);
  } catch (e) {
    console.error('[⬛ Navbar] ✗ 项目列表加载失败:', e);
  }

  updateRealTime();
  timerInterval = setInterval(() => {
    runTimeSeconds.value++;
    updateRealTime();
  }, 1000);

  console.log(`[⬛ Navbar] onMounted 完成 ${new Date().toLocaleTimeString()}`);
});

onUnmounted(() => {
  if (timerInterval) clearInterval(timerInterval);
});
</script>

<style scoped>
.app-name-wrapper {
  display: flex;
  align-items: center;
}

.app-name-tech {
  position: relative;
  background: linear-gradient(90deg, #c084fc, #818cf8, #38bdf8, #818cf8, #c084fc);
  background-size: 200% 100%;
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
  animation: shimmer 6s ease-in-out infinite;
  padding-bottom: 0.375rem;
}

.underline-bar {
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  height: 0.125rem;
  border-radius: 1px;
  background: linear-gradient(90deg, transparent 0%, #7c3aed 20%, #818cf8 50%, #7c3aed 80%, transparent 100%);
  animation: bar-breathe 3s ease-in-out infinite;
}

@keyframes shimmer {
  0%, 100% { background-position: 0% 50%; }
  50% { background-position: 200% 50%; }
}

@keyframes bar-breathe {
  0%, 100% { opacity: 0.3; }
  50% { opacity: 0.8; }
}

</style>
