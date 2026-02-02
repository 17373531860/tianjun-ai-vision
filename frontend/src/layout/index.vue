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
        <router-link to="/project" class="nav-item">
          <el-icon class="mr-2"><Folder /></el-icon> {{ $t('menu.project') }}
        </router-link>
        <router-link to="/model" class="nav-item">
          <el-icon class="mr-2"><Cpu /></el-icon> {{ $t('menu.model') }}
        </router-link>
        <router-link to="/source" class="nav-item">
          <el-icon class="mr-2"><VideoCamera /></el-icon> 输入源设置
        </router-link>
        <router-link to="/data" class="nav-item">
          <el-icon class="mr-2"><DataLine /></el-icon> {{ $t('menu.data') }}
        </router-link>
        <router-link to="/alarm" class="nav-item">
          <el-icon class="mr-2"><Bell /></el-icon> 报警设置
        </router-link>
        <router-link to="/settings" class="nav-item">
          <el-icon class="mr-2"><Setting /></el-icon> {{ $t('menu.settings') }}
        </router-link>
      </nav>
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
</script>

<style scoped>
.nav-item {
  @apply flex items-center px-6 py-4 text-gray-400 hover:bg-gray-800 hover:text-white transition-all;
}
.router-link-active {
  @apply bg-tech-blue/10 text-tech-blue border-r-4 border-tech-blue;
}
</style>