<template>
  <TjSlot name="mes.layout.body">
  <div class="mes-container h-full flex flex-col bg-[#0a0e1a] text-white overflow-hidden">
    <!-- 顶部标签页 -->
    <div class="flex items-center px-4 pt-3 pb-1 gap-2 border-b border-cyan-900/50">
      <button
        v-for="tab in tabs" :key="tab.key"
        @click="activeTab = tab.key"
        class="px-4 py-2 rounded-t text-sm font-medium transition-all"
        :class="activeTab === tab.key
          ? 'bg-cyan-800/40 text-cyan-300 border border-cyan-700 border-b-transparent'
          : 'text-gray-400 hover:text-gray-200 hover:bg-slate-800/50'"
      >{{ tab.label }}</button>
    </div>

    <!-- 内容区 -->
    <div class="flex-1 overflow-auto p-4">
      <!-- 工单管理 -->
      <template v-if="activeTab === 'orders'">
        <OrderPanel />
      </template>

      <!-- 工件追溯 -->
      <template v-if="activeTab === 'workpieces'">
        <WorkpiecePanel />
      </template>

      <!-- 缺陷分析 -->
      <template v-if="activeTab === 'defects'">
        <DefectPanel />
      </template>

      <!-- 扫码器 -->
      <template v-if="activeTab === 'scanner'">
        <ScannerPanel />
      </template>

      <!-- 外部对接 -->
      <template v-if="activeTab === 'gateway'">
        <GatewayPanel />
      </template>

      <!-- 外部设备 -->
      <template v-if="activeTab === 'external'">
        <ExternalDevicePanel />
      </template>

      <!-- 集群汇总 -->
      <template v-if="activeTab === 'cluster'">
        <ClusterPanel />
      </template>
    </div>
  </div>
  </TjSlot>
</template>

<script setup>
import TjSlot from '@/components/TjSlot.vue';
import { ref } from 'vue'
import OrderPanel from './OrderPanel.vue'
import WorkpiecePanel from './WorkpiecePanel.vue'
import DefectPanel from './DefectPanel.vue'
import ScannerPanel from './ScannerPanel.vue'
import GatewayPanel from './GatewayPanel.vue'
import ExternalDevicePanel from './ExternalDevicePanel.vue'
import ClusterPanel from './ClusterPanel.vue'

const activeTab = ref('orders')
const tabs = [
  { key: 'orders', label: '工单管理' },
  { key: 'workpieces', label: '工件追溯' },
  { key: 'defects', label: '缺陷分析' },
  { key: 'scanner', label: '扫码器' },
  { key: 'gateway', label: '外部对接' },
  { key: 'external', label: '外部设备' },
  { key: 'cluster', label: '集群汇总' },
]
</script>
