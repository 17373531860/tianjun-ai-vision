<template>
  <component :is="activeView" :key="handsCropMode ? 'hands-crop' : 'full-monitor'" />
</template>

<script setup>
import { computed, defineAsyncComponent } from 'vue';
import { useDisplayWindow } from '@/composables/useDisplayWindow';

const FullMonitor = defineAsyncComponent(() => import('./index.vue'));
const HandsCropMonitor = defineAsyncComponent(() => import('./HandsCropMonitor.vue'));

const { handsOnly: handsCropMode } = useDisplayWindow();
const activeView = computed(() => handsCropMode.value ? HandsCropMonitor : FullMonitor);
</script>
