<script setup>
import { onMounted, onErrorCaptured } from 'vue';
import { useRouter } from 'vue-router';

const router = useRouter();

onMounted(() => {
  console.log(`[⬛ App] App.vue onMounted — 路由: ${router.currentRoute.value.fullPath}`);
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
