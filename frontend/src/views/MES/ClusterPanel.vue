<template>
  <div class="space-y-4">
    <!-- 集群配置 -->
    <div class="bg-slate-800/60 rounded-lg border border-slate-700 p-5">
      <div class="flex items-center justify-between mb-4">
        <h3 class="text-cyan-300 font-semibold text-base">集群配置</h3>
        <div class="flex items-center gap-3">
          <el-switch v-model="config.enabled" size="small" @change="saveConfig" />
          <span class="text-xs" :class="config.enabled ? 'text-green-400' : 'text-gray-500'">
            {{ config.enabled ? '已启用' : '未启用' }}
          </span>
        </div>
      </div>

      <div class="grid grid-cols-2 gap-x-6 gap-y-4">
        <!-- 角色 -->
        <div>
          <div class="text-xs text-gray-400 mb-1">本机角色</div>
          <el-select v-model="config.role" size="small" class="w-full" @change="saveConfig">
            <el-option label="独立运行（不参与集群）" value="standalone" />
            <el-option label="主机（汇总数据，推送 MES）" value="master" />
            <el-option label="从机（上报数据给主机）" value="slave" />
          </el-select>
        </div>

        <!-- 工位标识 -->
        <div>
          <div class="text-xs text-gray-400 mb-1">本机工位标识</div>
          <el-input v-model="config.station_id" size="small" placeholder="如 A, B, C"
                    @blur="saveConfig" />
        </div>

        <!-- 主机地址（从机时显示） -->
        <div v-if="config.role === 'slave'">
          <div class="text-xs text-gray-400 mb-1">主机地址</div>
          <el-input v-model="config.master_url" size="small"
                    placeholder="http://192.168.0.10:8001" @blur="saveConfig">
            <template #append>
              <el-button size="small" @click="testMaster" :loading="testing">测试</el-button>
            </template>
          </el-input>
        </div>

        <!-- 期望工位列表（主机时显示） -->
        <div v-if="config.role === 'master'">
          <div class="text-xs text-gray-400 mb-1">箱子需要的工位 <span class="text-gray-600">（双工位机器填 B-0, B-1）</span></div>
          <el-select v-model="config.expected_stations" multiple allow-create filterable
                     size="small" class="w-full" placeholder="输入工位标识后回车（多通道用 B-0, B-1 格式）"
                     @change="saveConfig">
            <el-option v-for="s in presetStations" :key="s" :label="s" :value="s" />
          </el-select>
          <div class="text-xs text-gray-500 mt-1">
            所有列出的工位数据到齐后才会汇总推送
          </div>
        </div>

        <!-- 同步模式（主机时显示） -->
        <div v-if="config.role === 'master'">
          <div class="text-xs text-gray-400 mb-1">同步模式</div>
          <el-select v-model="config.sync_mode" size="small" class="w-full" @change="saveConfig">
            <el-option label="等齐再推（所有工位完成后一次推送）" value="wait_all" />
            <el-option label="实时 + 汇总（每个工位到即推，全齐后再推汇总）" value="realtime" />
          </el-select>
        </div>

        <!-- 超时时间（主机时显示） -->
        <div v-if="config.role === 'master'">
          <div class="text-xs text-gray-400 mb-1">超时时间（秒）</div>
          <el-input-number v-model="config.timeout_sec" :min="30" :max="3600"
                           size="small" class="!w-full" controls-position="right"
                           @change="saveConfig" />
          <div class="text-xs text-gray-500 mt-1">
            超过此时间未齐的箱子标记为超时
          </div>
        </div>
      </div>

      <!-- 连接测试结果 -->
      <div v-if="testResult" class="mt-3 p-2 rounded text-xs"
           :class="testResult.ok ? 'bg-green-900/30 text-green-300' : 'bg-red-900/30 text-red-300'">
        {{ testResult.message }}
      </div>
    </div>

    <!-- 状态概览（主机时显示） -->
    <div v-if="config.role === 'master' && config.enabled" class="flex gap-4">
      <!-- 待汇总箱子 -->
      <div class="flex-1 bg-slate-800/60 rounded-lg border border-slate-700 p-4">
        <div class="flex items-center justify-between mb-3">
          <h3 class="text-cyan-300 font-semibold">待汇总</h3>
          <el-button size="small" @click="loadBoxes">刷新</el-button>
        </div>
        <div v-if="pendingBoxes.length === 0" class="text-gray-500 text-sm text-center py-6">
          暂无待汇总箱子
        </div>
        <div v-else class="space-y-2">
          <div v-for="box in pendingBoxes" :key="box.box_serial"
               class="bg-slate-700/40 rounded p-3">
            <div class="flex items-center justify-between">
              <span class="font-mono text-cyan-200">{{ box.box_serial }}</span>
              <el-tag :type="box.is_complete ? 'success' : 'warning'" size="small">
                {{ box.is_complete ? '已齐' : `缺 ${box.missing_stations.join(', ')}` }}
              </el-tag>
            </div>
            <div class="text-xs text-gray-400 mt-1">
              已到: {{ box.received_stations.join(', ') || '无' }}
              <span v-if="box.first_received_at" class="ml-2">
                | 首次: {{ formatTime(box.first_received_at) }}
              </span>
            </div>
          </div>
        </div>
      </div>

      <!-- 最近完成 -->
      <div class="flex-1 bg-slate-800/60 rounded-lg border border-slate-700 p-4">
        <div class="flex items-center justify-between mb-3">
          <h3 class="text-cyan-300 font-semibold">最近完成</h3>
        </div>
        <div v-if="recentSummaries.length === 0" class="text-gray-500 text-sm text-center py-6">
          暂无完成记录
        </div>
        <div v-else class="space-y-2">
          <div v-for="s in recentSummaries" :key="s.id"
               class="bg-slate-700/40 rounded p-3">
            <div class="flex items-center justify-between">
              <span class="font-mono text-cyan-200">{{ s.box_serial }}</span>
              <div class="flex gap-1">
                <el-tag :type="s.overall_result === 'OK' ? 'success' : 'danger'" size="small">
                  {{ s.overall_result }}
                </el-tag>
                <el-tag :type="s.status === 'pushed' ? 'success' : 'info'" size="small">
                  {{ s.status === 'pushed' ? '已推送' : s.status }}
                </el-tag>
              </div>
            </div>
            <div class="text-xs text-gray-400 mt-1">
              {{ s.completed_stations }}/{{ s.total_stations }} 工位
              <span v-if="s.pushed_at" class="ml-2">| 推送: {{ formatTime(s.pushed_at) }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 从机状态提示 -->
    <div v-if="config.role === 'slave' && config.enabled"
         class="bg-slate-800/60 rounded-lg border border-blue-800/50 p-4">
      <div class="text-blue-300 font-semibold mb-2">从机模式</div>
      <div class="text-sm text-gray-300">
        本机（工位 {{ config.station_id }}）的检测结果将自动上报到主机
        <span class="text-cyan-300 font-mono">{{ config.master_url }}</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import {
  getClusterConfig, updateClusterConfig,
  getClusterBoxes, clusterHealth
} from '@/api/cluster'

const config = ref({
  role: 'standalone',
  master_url: '',
  station_id: 'A',
  expected_stations: [],
  sync_mode: 'wait_all',
  timeout_sec: 300,
  enabled: false,
})

const pendingBoxes = ref([])
const recentSummaries = ref([])
const testing = ref(false)
const testResult = ref(null)
const presetStations = ['A', 'B', 'C', 'D']

const formatTime = (t) => t ? t.replace('T', ' ').substring(0, 19) : '-'

const loadConfig = async () => {
  try {
    const res = await getClusterConfig()
    config.value = { ...config.value, ...res.data }
  } catch {}
}

const saveConfig = async () => {
  try {
    const res = await updateClusterConfig(config.value)
    config.value = { ...config.value, ...res.data }
    ElMessage.success('集群配置已保存')
  } catch (e) {
    ElMessage.error('保存失败: ' + (e.response?.data?.detail || e.message))
  }
}

const loadBoxes = async () => {
  try {
    const res = await getClusterBoxes()
    pendingBoxes.value = res.data.pending || []
    recentSummaries.value = res.data.recent || []
  } catch {}
}

const testMaster = async () => {
  testing.value = true
  testResult.value = null
  try {
    const url = config.value.master_url?.replace(/\/$/, '') + '/api/v1/cluster/health'
    const res = await fetch(url, { method: 'GET', signal: AbortSignal.timeout(5000) })
    if (res.ok) {
      const data = await res.json()
      testResult.value = {
        ok: true,
        message: `连接成功 — 主机角色: ${data.role}, 工位: ${data.station_id}`
      }
    } else {
      testResult.value = { ok: false, message: `HTTP ${res.status}` }
    }
  } catch (e) {
    testResult.value = { ok: false, message: `连接失败: ${e.message}` }
  } finally {
    testing.value = false
  }
}

let refreshTimer = null
onMounted(() => {
  loadConfig()
  loadBoxes()
  refreshTimer = setInterval(loadBoxes, 5000)
})
onUnmounted(() => {
  if (refreshTimer) clearInterval(refreshTimer)
})
</script>
