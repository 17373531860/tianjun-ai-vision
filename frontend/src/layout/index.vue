<template>
  <div class="flex h-screen w-screen bg-ind-bg">
    <aside class="w-64 bg-ind-panel border-r border-gray-800 flex flex-col">
      <div class="p-6 text-xl font-bold text-tech-blue border-b border-gray-800">
        VISION SYSTEM
      </div>
      
      <nav class="flex-1 mt-4">
        <router-link to="/monitor" class="nav-item">
          <el-icon class="mr-2"><Monitor /></el-icon> {{ $t('menu.monitor') }}
        </router-link>
        <router-link to="/project" class="nav-item" :class="{ 'nav-disabled': systemStore.isDetecting }" @click.capture="preventNavIfDetecting">
          <el-icon class="mr-2"><Folder /></el-icon> {{ $t('menu.project') }}
        </router-link>
        <router-link to="/model" class="nav-item" :class="{ 'nav-disabled': systemStore.isDetecting }" @click.capture="preventNavIfDetecting">
          <el-icon class="mr-2"><Cpu /></el-icon> {{ $t('menu.model') }}
        </router-link>
        <router-link to="/source" class="nav-item" :class="{ 'nav-disabled': systemStore.isDetecting }" @click.capture="preventNavIfDetecting">
          <el-icon class="mr-2"><VideoCamera /></el-icon> 输入源设置
        </router-link>
        <router-link to="/data" class="nav-item" :class="{ 'nav-disabled': systemStore.isDetecting }" @click.capture="preventNavIfDetecting">
          <el-icon class="mr-2"><DataLine /></el-icon> {{ $t('menu.data') }}
        </router-link>
        <router-link to="/alarm" class="nav-item" :class="{ 'nav-disabled': systemStore.isDetecting }" @click.capture="preventNavIfDetecting">
          <el-icon class="mr-2"><Bell /></el-icon> 报警设置
        </router-link>
        <router-link to="/settings" class="nav-item" :class="{ 'nav-disabled': systemStore.isDetecting }" @click.capture="preventNavIfDetecting">
          <el-icon class="mr-2"><Setting /></el-icon> {{ $t('menu.settings') }}
        </router-link>
      </nav>
      
      <!-- 检测运行提示 -->
      <div v-if="systemStore.isDetecting" class="px-4 py-3 bg-red-900/30 border-t border-red-800 text-red-400 text-xs text-center">
        检测运行中，请先停止检测
      </div>
    </aside>

    <main class="flex-1 flex flex-col overflow-hidden">
      <!-- Replaced internal header with Navbar component -->
      <Navbar />

      <section class="flex-1 overflow-auto p-4 bg-[#0f172a]">
        <router-view />
      </section>
    </main>
  </div>
</template>

<script setup>
import Navbar from './Navbar.vue';
import { ref } from 'vue';
import { Monitor, Folder, Cpu, DataLine, Setting, VideoCamera, Bell } from '@element-plus/icons-vue';
import { useSystemStore } from '@/store/useSystemStore';
import { ElMessage } from 'element-plus';

const systemStore = useSystemStore();

// 检测运行时阻止导航
const preventNavIfDetecting = (e) => {
  if (systemStore.isDetecting) {
    e.preventDefault();
    e.stopPropagation();
    ElMessage.warning('检测运行中，请先停止检测再切换页面');
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
</style>