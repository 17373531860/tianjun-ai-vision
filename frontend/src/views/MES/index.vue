<template>
  <TjSlot name="mes.layout.body">
  <div class="mes-container h-full flex flex-col bg-[#0a0e1a] text-white overflow-hidden">
    <!-- 顶部标签页（按域分组：生产 / 采集对接 / 外部系统） -->
    <div class="flex items-center flex-wrap px-4 pt-3 pb-1 gap-2 border-b border-cyan-900/50">
      <template v-for="(group, gi) in tabGroups" :key="group.name">
        <div v-if="gi > 0" class="h-6 w-px bg-cyan-900/60 mx-1"></div>
        <span class="text-[10px] text-gray-600 uppercase tracking-wider select-none">{{ group.name }}</span>
        <button
          v-for="tab in group.tabs" :key="tab.key"
          @click="activeTab = tab.key"
          class="px-4 py-2 rounded-t text-sm font-medium transition-all"
          :class="activeTab === tab.key
            ? 'bg-cyan-800/40 text-cyan-300 border border-cyan-700 border-b-transparent'
            : 'text-gray-400 hover:text-gray-200 hover:bg-slate-800/50'"
        >{{ tab.label }}</button>
      </template>
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
