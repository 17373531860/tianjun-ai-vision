<template>
  <div class="flex h-screen w-screen bg-ind-bg relative">
    <!-- 侧边栏遮罩 -->
    <Transition v-if="!kioskMode" name="fade">
      <div v-if="sidebarOpen" class="fixed inset-0 bg-black/40 z-30" @click="sidebarOpen = false"></div>
    </Transition>

    <!-- 侧边栏（默认隐藏，点击按钮滑出） -->
    <Transition v-if="!kioskMode" name="slide">
      <aside v-if="sidebarOpen" class="fixed left-0 top-0 h-full w-64 bg-ind-panel border-r border-gray-800 flex flex-col z-40 shadow-2xl">
        <div class="p-6 text-xl font-bold text-tech-blue border-b border-gray-800 flex justify-between items-center">
          <span>VISION SYSTEM</span>
          <button @click="sidebarOpen = false" class="text-gray-500 hover:text-white transition p-1">
            <el-icon class="text-[1.125rem]"><Close /></el-icon>
          </button>
        </div>
        
        <nav class="flex-1 mt-4">
          <router-link v-if="canShow('/monitor')" to="/monitor" class="nav-item" @click="sidebarOpen = false">
            <el-icon class="mr-2"><Monitor /></el-icon> {{ $t('menu.monitor') }}
          </router-link>
          <router-link v-if="canShow('/project')" to="/project" class="nav-item" :class="{ 'nav-disabled': systemStore.isDetecting }" @click.capture="handleNav">
            <el-icon class="mr-2"><Folder /></el-icon> {{ $t('menu.project') }}
          </router-link>
          <router-link v-if="canShow('/model')" to="/model" class="nav-item" :class="{ 'nav-disabled': systemStore.isDetecting }" @click.capture="handleNav">
            <el-icon class="mr-2"><Cpu /></el-icon> {{ $t('menu.model') }}
          </router-link>
          <router-link v-if="canShow('/source')" to="/source" class="nav-item" :class="{ 'nav-disabled': systemStore.isDetecting }" @click.capture="handleNav">
            <el-icon class="mr-2"><VideoCamera /></el-icon> 工位与输入源
          </router-link>
          <router-link v-if="canShow('/data')" to="/data" class="nav-item" :class="{ 'nav-disabled': systemStore.isDetecting }" @click.capture="handleNav">
            <el-icon class="mr-2"><DataLine /></el-icon> {{ $t('menu.data') }}
          </router-link>
          <router-link v-if="canShow('/mes')" to="/mes" class="nav-item" :class="{ 'nav-disabled': systemStore.isDetecting }" @click.capture="handleNav">
            <el-icon class="mr-2"><Tickets /></el-icon> MES 管理
          </router-link>
          <router-link v-if="canShow('/alarm')" to="/alarm" class="nav-item" :class="{ 'nav-disabled': systemStore.isDetecting }" @click.capture="handleNav">
            <el-icon class="mr-2"><Bell /></el-icon> 报警设置
          </router-link>
          <router-link v-if="canShow('/interconnect')" to="/interconnect" class="nav-item" :class="{ 'nav-disabled': systemStore.isDetecting }" @click.capture="handleNav">
            <el-icon class="mr-2"><Connection /></el-icon> 训练平台互连
          </router-link>
          <router-link v-if="canShow('/ai-tools')" to="/ai-tools" class="nav-item" :class="{ 'nav-disabled': systemStore.isDetecting }" @click.capture="handleNav">
            <el-icon class="mr-2"><MagicStick /></el-icon> AI 能力试用
          </router-link>
          <router-link v-if="canShow('/settings')" to="/settings" class="nav-item" :class="{ 'nav-disabled': systemStore.isDetecting }" @click.capture="handleNav">
            <el-icon class="mr-2"><Setting /></el-icon> {{ $t('menu.settings') }}
          </router-link>

          <!-- G2: Tier 2/3 插件动态注入菜单 -->
          <router-link
            v-for="m in pluginTheme.sortedPluginMenus"
            :key="m.path"
            :to="m.path"
            class="nav-item plugin-nav-item"
            :class="{ 'nav-disabled': systemStore.isDetecting }"
            @click.capture="handleNav"
          >
            <el-icon class="mr-2"><DataAnalysis /></el-icon> {{ m.label }}
          </router-link>
        </nav>
        
        <div v-if="systemStore.isDetecting" class="px-4 py-3 bg-red-900/30 border-t border-red-800 text-red-400 text-xs text-center">
          检测运行中，请先停止检测
        </div>
      </aside>
    </Transition>

    <main class="flex-1 flex flex-col overflow-hidden">
      <Navbar v-if="!kioskMode">
        <template #left>
          <button @click="sidebarOpen = true" class="p-2 rounded hover:bg-slate-700 transition text-gray-400 hover:text-white mr-2" title="导航菜单">
            <el-icon class="text-[1.25rem]"><Menu /></el-icon>
          </button>
        </template>
      </Navbar>

      <section
        class="flex-1 bg-[#0f172a]"
        :class="kioskMode ? 'overflow-hidden p-0' : 'overflow-auto px-4 pt-4 pb-0'"
      >
        <router-view />
      </section>

      <BottomBar v-if="!kioskMode" />
    </main>
  </div>
</template>

<script setup>
import Navbar from './Navbar.vue';
import BottomBar from './BottomBar.vue';
import { computed, ref, onMounted } from 'vue';
import { useRoute } from 'vue-router';
import { Monitor, Folder, Cpu, DataLine, Setting, VideoCamera, Bell, Close, Menu, Tickets, DataAnalysis, Connection, MagicStick } from '@element-plus/icons-vue';
import { useSystemStore } from '@/store/useSystemStore';
import { usePluginThemeStore } from '@/store/usePluginThemeStore';
import { useAuthStore } from '@/store/useAuthStore';
import { ElMessage } from 'element-plus';
import { startScanGun } from '@/composables/useScanGun';

const systemStore = useSystemStore();
const pluginTheme = usePluginThemeStore();
const authStore = useAuthStore();
const sidebarOpen = ref(false);
const route = useRoute();
const kioskMode = computed(() => route.query.kiosk === '1');

// USB 扫码枪: 全局挂键盘监听, 这样在任何页面 (含全屏检测页) 扫码都能按用途处理
// (拉工单/绑工件)。关/开与用途由 扫码器→USB 扫码枪 Tab 控制 (本监听内部实时读配置)。
onMounted(() => {
  if (!kioskMode.value) startScanGun();
});

// 菜单可见 = 插件主题未隐藏 AND 当前账号有路由权限.
// 两层门各自独立: 插件主题是客户定制层 (按 brand 隐藏), 权限层是账号层 (按角色隐藏).
const canShow = (path) => {
  return !pluginTheme.isMenuHidden(path) && authStore.canAccessRoute(path);
};

const handleNav = (e) => {
  if (systemStore.isDetecting) {
    e.preventDefault();
    e.stopPropagation();
    ElMessage.warning('检测运行中，请先停止检测再切换页面');
  } else {
    sidebarOpen.value = false;
  }
};
</script>

<style scoped>
.nav-item {
  @apply flex items-center px-6 py-4 text-gray-400 hover:bg-gray-800 hover:text-white transition-all;
}
.router-link-active {
  @apply bg-tech-blue/10 text-tech-blue border-r-4 border-tech-blue;
}
.nav-disabled {
  @apply opacity-50 cursor-not-allowed pointer-events-auto;
}
.nav-disabled:hover {
  @apply bg-transparent text-gray-400;
}

.slide-enter-active, .slide-leave-active {
  transition: transform 0.25s ease;
}
.slide-enter-from, .slide-leave-to {
  transform: translateX(-100%);
}

.fade-enter-active, .fade-leave-active {
  transition: opacity 0.25s ease;
}
.fade-enter-from, .fade-leave-to {
  opacity: 0;
}
</style>
