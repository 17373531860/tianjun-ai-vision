<template>
  <section class="plugin-dashboard">
    <h2>客户看板</h2>
    <p>这是开发态源码示例；打包后由 Vite 输出到 frontend/dist/index.esm.js。</p>

    <div class="plugin-dashboard__cards">
      <article>
        <strong>当班目标</strong>
        <span>{{ shiftTarget }}</span>
      </article>
      <article>
        <strong>缺陷阈值</strong>
        <span>{{ defectThreshold }}%</span>
      </article>
    </div>
  </section>
</template>

<script setup>
import { storeToRefs } from 'pinia'
import { usePluginDashboardStore } from './store'

const store = usePluginDashboardStore()
const { shiftTarget, defectThreshold } = storeToRefs(store)
</script>

<style scoped>
.plugin-dashboard {
  padding: 24px;
}

.plugin-dashboard__cards {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}
</style>
