<script setup>
import { onMounted, onErrorCaptured } from 'vue';
import { useRouter } from 'vue-router';
import { ElMessage } from 'element-plus';

const router = useRouter();

onMounted(() => {
  console.log(`[⬛ App] App.vue onMounted — 路由: ${router.currentRoute.value.fullPath}`);

  // v3.23.x: 加深启动就绪门槛降级提示 — 后端进程起了但数据库探测持续超时,
  // 主进程已降级放主窗进来 (而非死等到 5 分钟超时退出). 给用户一次明确告警,
  // 便于排查数据库异常; 业务照常显示 + 走各自的错误提示/重试。
  if (typeof window !== 'undefined' && window.electronAPI?.onDeepGateDowngraded) {
    window.electronAPI.onDeepGateDowngraded(() => {
      ElMessage({
        type: 'warning',
        message: '后端已启动但数据库就绪检测超时, 已降级进入界面。若项目/数据显示异常, 请检查数据库状态。',
        duration: 8000,
        showClose: true,
      });
    });
  }
});

onErrorCaptured((err, instance, info) => {
  const name = instance?.$options?.name || instance?.$options?.__name || '未知组件';
  console.error(`[⬛ App] 子组件错误被捕获 — 组件: ${name}, info: ${info}`, err);
  return false;
});
</script>

<template>
  <router-view />
</template>

<style>
</style>
