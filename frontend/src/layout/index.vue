<template>
  <div class="flex h-screen w-screen bg-ind-bg relative">
    <!-- 侧边栏遮罩 -->
    <Transition name="fade">
      <div v-if="sidebarOpen" class="fixed inset-0 bg-black/40 z-30" @click="sidebarOpen = false"></div>
    </Transition>

    <!-- 侧边栏（默认隐藏，点击按钮滑出） -->
    <Transition name="slide">
      <aside v-if="sidebarOpen" class="fixed left-0 top-0 h-full w-64 bg-ind-panel border-r border-gray-800 flex flex-col z-40 shadow-2xl">
        <div class="p-6 text-xl font-bold text-tech-blue border-b border-gray-800 flex justify-between items-center">
          <span>VISION SYSTEM</span>
          <button @click="sidebarOpen = false" class="text-gray-500 hover:text-white transition p-1">
            <el-icon class="text-[1.125rem]"><Close /></el-icon>
          </button>
        </div>
        
        <nav class="flex-1 mt-4">
          <router-link v-if="!pluginTheme.isMenuHidden('/monitor')" to="/monitor" class="nav-item" @click="sidebarOpen = false">
            <el-icon class="mr-2"><Monitor /></el-icon> {{ $t('menu.monitor') }}
          </router-link>
          <router-link v-if="!pluginTheme.isMenuHidden('/project')" to="/project" class="nav-item" :class="{ 'nav-disabled': systemStore.isDetecting }" @click.capture="handleNav">
            <el-icon class="mr-2"><Folder /></el-icon> {{ $t('menu.project') }}
          </router-link>
          <router-link v-if="!pluginTheme.isMenuHidden('/model')" to="/model" class="nav-item" :class="{ 'nav-disabled': systemStore.isDetecting }" @click.capture="handleNav">
            <el-icon class="mr-2"><Cpu /></el-icon> {{ $t('menu.model') }}
          </router-link>
          <router-link v-if="!pluginTheme.isMenuHidden('/source')" to="/source" class="nav-item" :class="{ 'nav-disabled': systemStore.isDetecting }" @click.capture="handleNav">
            <el-icon class="mr-2"><VideoCamera /></el-icon> 输入源设置
          </router-link>
          <router-link v-if="!pluginTheme.isMenuHidden('/data')" to="/data" class="nav-item" :class="{ 'nav-disabled': systemStore.isDetecting }" @click.capture="handleNav">
            <el-icon class="mr-2"><DataLine /></el-icon> {{ $t('menu.data') }}
          </router-link>
          <router-link v-if="!pluginTheme.isMenuHidden('/mes')" to="/mes" class="nav-item" :class="{ 'nav-disabled': systemStore.isDetecting }" @click.capture="handleNav">
            <el-icon class="mr-2"><Tickets /></el-icon> MES 管理
          </router-link>
          <router-link v-if="!pluginTheme.isMenuHidden('/alarm')" to="/alarm" class="nav-item" :class="{ 'nav-disabled': systemStore.isDetecting }" @click.capture="handleNav">
            <el-icon class="mr-2"><Bell /></el-icon> 报警设置
          </router-link>
          <router-link v-if="!pluginTheme.isMenuHidden('/settings')" to="/settings" class="nav-item" :class="{ 'nav-disabled': systemStore.isDetecting }" @click.capture="handleNav">
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
      <Navbar>
        <template #left>
          <button @click="sidebarOpen = true" class="p-2 rounded hover:bg-slate-700 transition text-gray-400 hover:text-white mr-2" title="导航菜单">
            <el-icon class="text-[1.25rem]"><Menu /></el-icon>
          </button>
        </template>
      </Navbar>

      <section class="flex-1 overflow-auto px-4 pt-4 pb-0 bg-[#0f172a]">
        <router-view />
      </section>

      <BottomBar />
    </main>
  </div>
</template>

<script setup>
import Navbar from './Navbar.vue';
import BottomBar from './BottomBar.vue';
import { ref } from 'vue';
import { Monitor, Folder, Cpu, DataLine, Setting, VideoCamera, Bell, Close, Menu, Tickets, DataAnalysis } from '@element-plus/icons-vue';
import { useSystemStore } from '@/store/useSystemStore';
import { usePluginThemeStore } from '@/store/usePluginThemeStore';
import { ElMessage } from 'element-plus';

const systemStore = useSystemStore();
const pluginTheme = usePluginThemeStore();
const sidebarOpen = ref(false);

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