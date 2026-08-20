<template>
  <TjSlot name="mes.layout.body">
  <div class="mes-container h-full flex flex-col bg-[#0a0e1a] text-white overflow-hidden">
    <!-- 顶部标签页（按域分组的分段控件：生产 / 采集对接 / 外部系统） -->
    <div class="flex items-end flex-wrap gap-x-6 gap-y-2 px-4 pt-3 pb-3 border-b border-slate-700/60">
      <div v-for="group in tabGroups" :key="group.name" class="flex flex-col gap-1">
        <span class="text-[10px] text-gray-500 tracking-[0.2em] select-none pl-1">{{ group.name }}</span>
        <div class="flex items-center gap-1 p-1 rounded-lg bg-slate-800/70 border border-slate-700/60">
          <button
            v-for="tab in group.tabs" :key="tab.key"
            @click="activeTab = tab.key"
            class="px-3.5 py-1.5 rounded-md text-sm whitespace-nowrap transition-colors"
            :class="activeTab === tab.key
              ? 'bg-cyan-600/90 text-white font-medium shadow-sm'
              : 'text-gray-400 hover:text-gray-100 hover:bg-slate-700/70'"
          >{{ tab.label }}</button>
        </div>
      </div>
    </div>

    <!-- 内容区 -->
    <div class="flex-1 overflow-auto p-4">
      <!-- 工单管理 -->
      <template v-if="activeTab === 'orders'">
        <OrderPanel />
      </template>

      <!-- 包装箱结算（原系统设置 Tab，v3.21+ 上银包装线） -->
      <template v-if="activeTab === 'packaging'">
        <PackagingFlowPanel />
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

      <!-- 工单拉取 -->
      <template v-if="activeTab === 'order-pull'">
        <OrderPullPanel />
      </template>

      <!-- 工单接收 (入站对接) -->
      <template v-if="activeTab === 'order-inbound'">
        <OrderInboundPanel />
      </template>

      <!-- 外部设备 -->
      <template v-if="activeTab === 'external'">
        <ExternalDevicePanel />
      </template>

      <!-- PLC 对接 (RFC 13) -->
      <template v-if="activeTab === 'plc'">
        <PlcPanel />
      </template>

      <!-- 触发中心 (RFC 14，原系统设置 Tab；与 PLC 共享动作注册表) -->
      <template v-if="activeTab === 'triggers'">
        <TriggerPanel />
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
import OrderPullPanel from './OrderPullPanel.vue'
import OrderInboundPanel from './OrderInboundPanel.vue'
import ExternalDevicePanel from './ExternalDevicePanel.vue'
import PlcPanel from './PlcPanel.vue'
import ClusterPanel from './ClusterPanel.vue'
import PackagingFlowPanel from './PackagingFlowPanel.vue'
import TriggerPanel from './TriggerPanel.vue'

const activeTab = ref('orders')
const tabGroups = [
  {
    name: '生产',
    tabs: [
      { key: 'orders', label: '工单管理' },
      { key: 'packaging', label: '包装结算' },
      { key: 'workpieces', label: '工件追溯' },
      { key: 'defects', label: '缺陷分析' },
    ],
  },
  {
    name: '采集对接',
    tabs: [
      { key: 'scanner', label: '扫码器' },
      { key: 'external', label: '外部设备' },
      { key: 'plc', label: 'PLC 对接' },
      { key: 'triggers', label: '触发中心' },
    ],
  },
  {
    name: '外部系统',
    tabs: [
      { key: 'gateway', label: '外部对接' },
      { key: 'order-pull', label: '工单拉取' },
      { key: 'order-inbound', label: '工单接收' },
      { key: 'cluster', label: '集群汇总' },
    ],
  },
]
</script>
