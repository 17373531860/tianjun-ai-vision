<template>
  <component :is="activeView" :key="handsCropMode ? 'hands-crop' : 'full-monitor'" />
</template>

<script setup>
import { computed, defineAsyncComponent } from 'vue';
import { useRoute } from 'vue-router';

const FullMonitor = defineAsyncComponent(() => import('./index.vue'));
const HandsCropMonitor = defineAsyncComponent(() => import('./HandsCropMonitor.vue'));

const route = useRoute();
const handsCropMode = computed(() => (
  route.query.video_only === '1' && route.query.hands_crop === '1'
));
const activeView = computed(() => handsCropMode.value ? HandsCropMonitor : FullMonitor);
</script>
