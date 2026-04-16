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
        <div>
          <div class="text-xs text-gray-400 mb-1">本机角色</div>
          <el-select v-model="config.role" size="small" class="w-full" @change="saveConfig">
            <el-option label="独立运行（不参与集群）" value="standalone" />
            <el-option label="主机（汇总数据，推送 MES）" value="master" />
            <el-option label="从机（上报数据给主机）" value="slave" />
          </el-select>
        </div>

        <div>
          <div class="text-xs text-gray-400 mb-1">本机工位标识</div>
          <el-input v-model="config.station_id" size="small" placeholder="如 A, B, C"
                    @blur="saveConfig" />
        </div>

        <div v-if="config.role === 'slave'">
          <div class="text-xs text-gray-400 mb-1">主机地址</div>
          <el-input v-model="config.master_url" size="small"
                    placeholder="http://192.168.0.10:8001" @blur="saveConfig">
            <template #append>
              <el-button size="small" @click="testMaster" :loading="testing">测试</el-button>
            </template>
          </el-input>
        </div>

        <div v-if="config.role === 'master'">
          <div class="text-xs text-gray-400 mb-1">汇总需要的工位 <span class="text-gray-600">（多通道填 B-0, B-1）</span></div>
          <el-select v-model="config.expected_stations" multiple allow-create filterable
                     size="small" class="w-full" placeholder="输入工位标识后回车"
                     @change="saveConfig">
            <el-option v-for="s in presetStations" :key="s" :label="s" :value="s" />
          </el-select>
          <div class="text-xs text-gray-500 mt-1">
            所有列出的工位数据到齐后才会汇总推送
          </div>
        </div>

        <div v-if="config.role === 'master'">
          <div class="text-xs text-gray-400 mb-1">同步模式</div>
          <el-select v-model="config.sync_mode" size="small" class="w-full" @change="saveConfig">
            <el-option label="等齐再推（所有工位完成后一次推送）" value="wait_all" />
            <el-option label="实时 + 汇总（每个工位到即推，全齐后再推汇总）" value="realtime" />
          </el-select>
        </div>

        <div v-if="config.role === 'master'">
          <div class="text-xs text-gray-400 mb-1">超时时间（秒）</div>
          <el-input-number v-model="config.timeout_sec" :min="0" :precision="2"
                           size="small" class="!w-full" controls-position="right"
                           @change="saveConfig" />
          <div class="text-xs text-gray-500 mt-1">
            超过此时间未齐的目标标记为超时
          </div>
        </div>

        <div v-if="config.role === 'master'">
          <div class="flex items-center justify-between mt-2">
            <div>
              <div class="text-xs text-gray-400">超时仍推送 MES</div>
              <div class="text-xs text-gray-500">
                开启后超时的目标也会推送给客户 MES，结果标记为 TIMEOUT
              </div>
            </div>
            <el-switch v-model="config.timeout_push" size="small" @change="saveConfig" />
          </div>
        </div>
      </div>

      <div v-if="testResult" class="mt-3 p-2 rounded text-xs"
           :class="testResult.ok ? 'bg-green-900/30 text-green-300' : 'bg-red-900/30 text-red-300'">
        {{ testResult.message }}
      </div>
    </div>

    <!-- 状态概览（主机时显示） -->
    <div v-if="config.role === 'master' && config.enabled" class="flex gap-4">
      <!-- 待汇总 -->
      <div class="flex-1 bg-slate-800/60 rounded-lg border border-slate-700 p-4">
        <div class="flex items-center justify-between mb-3">
          <h3 class="text-cyan-300 font-semibold">待汇总</h3>
          <div class="flex gap-2">
            <el-button size="small" type="warning" @click="loadMockData">测试数据</el-button>
            <el-button size="small" @click="loadBoxes">刷新</el-button>
          </div>
        </div>
        <div v-if="pendingBoxes.length === 0" class="text-gray-500 text-sm text-center py-6">
          暂无待汇总目标
        </div>
        <div v-else class="space-y-2">
          <div v-for="box in pendingBoxes" :key="box.box_serial"
               class="bg-slate-700/40 rounded p-3 cursor-pointer hover:bg-slate-700/60 transition-colors"
               @click="showBoxDetail(box.box_serial)">
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
          <span class="text-xs text-gray-500">共 {{ recentTotal }} 条</span>
        </div>
        <div v-if="recentSummaries.length === 0" class="text-gray-500 text-sm text-center py-6">
          暂无完成记录
        </div>
        <div v-else class="space-y-2">
          <div v-for="s in recentSummaries" :key="s.id || s.box_serial"
               class="bg-slate-700/40 rounded p-3 cursor-pointer hover:bg-slate-700/60 transition-colors"
               @click="showBoxDetail(s.box_serial)">
            <div class="flex items-center justify-between">
              <span class="font-mono text-cyan-200">{{ s.box_serial }}</span>
              <div class="flex gap-1">
                <el-tag :type="s.overall_result === 'OK' ? 'success' : s.overall_result === 'TIMEOUT' ? 'warning' : 'danger'" size="small">
                  {{ s.overall_result }}
                </el-tag>
                <el-tag :type="s.status === 'pushed' || s.status === 'pushed_timeout' ? 'success' : s.status === 'timeout' ? 'warning' : 'info'" size="small">
                  {{ {pushed: '已推送', pushed_timeout: '超时已推送', timeout: '超时未推送', complete: '已完成'}[s.status] || s.status }}
                </el-tag>
              </div>
            </div>
            <div class="text-xs text-gray-400 mt-1">
              {{ s.completed_stations }}/{{ s.total_stations }} 工位
              <span v-if="s.pushed_at" class="ml-2">| 推送: {{ formatTime(s.pushed_at) }}</span>
            </div>
          </div>
        </div>
        <div v-if="recentTotal > recentPageSize" class="flex justify-center mt-3">
          <el-pagination
            v-model:current-page="recentPage"
            :page-size="recentPageSize"
            :total="recentTotal"
            layout="prev, pager, next"
            small
            @current-change="loadBoxes"
          />
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

    <!-- 目标明细弹窗 -->
    <el-dialog v-model="showDetail" title="目标明细" width="680px" class="mes-dialog" destroy-on-close>
      <div v-if="detailLoading" class="text-center py-8 text-gray-400">加载中...</div>
      <div v-else-if="detailData">
        <div class="flex items-center justify-between mb-4">
          <div class="text-lg font-mono text-cyan-200">{{ detailData.box_serial }}</div>
          <div class="flex gap-2" v-if="detailData.summary">
            <el-tag :type="detailData.summary.overall_result === 'OK' ? 'success' : detailData.summary.overall_result === 'TIMEOUT' ? 'warning' : 'danger'" size="default">
              {{ detailData.summary.overall_result }}
            </el-tag>
            <el-tag :type="detailData.summary.status === 'pushed' || detailData.summary.status === 'pushed_timeout' ? 'success' : detailData.summary.status === 'timeout' ? 'warning' : 'info'" size="default">
              {{ {pushed: '已推送', pushed_timeout: '超时已推送', timeout: '超时未推送', complete: '已完成'}[detailData.summary.status] || detailData.summary.status }}
            </el-tag>
          </div>
          <el-tag v-else type="warning" size="default">待汇总</el-tag>
        </div>

        <div v-if="detailData.summary?.pushed_at" class="text-xs text-gray-400 mb-4">
          推送时间: {{ formatTime(detailData.summary.pushed_at) }}
        </div>

        <h4 class="text-cyan-300 text-sm font-semibold mb-2">各工位数据</h4>
        <div v-if="detailData.stations.length === 0" class="text-gray-500 text-sm text-center py-4">暂无工位数据</div>
        <div v-else class="space-y-2">
          <div v-for="st in detailData.stations" :key="st.station_id + '-' + (st.channel_id || '')"
               class="bg-slate-700/40 rounded p-3 border"
               :class="st.is_good ? 'border-green-800/30' : 'border-red-800/30'">
            <div class="flex items-center justify-between mb-1">
              <div class="flex items-center gap-2">
                <span class="font-semibold">工位 {{ st.station_id }}</span>
                <span v-if="st.channel_id != null" class="text-xs text-gray-500">通道 {{ st.channel_id }}</span>
              </div>
              <div class="flex items-center gap-2">
                <el-tag :type="st.is_good ? 'success' : 'danger'" size="small">
                  {{ st.is_good ? 'OK' : 'NG' }}
                </el-tag>
                <el-tag v-if="st.event_name" type="info" size="small">{{ st.event_name }}</el-tag>
              </div>
            </div>
            <div class="text-xs text-gray-400 space-y-0.5">
              <div v-if="st.source">来源: {{ st.source }}</div>
              <div>状态: {{ st.status }} | 时间: {{ formatTime(st.received_at) }}</div>
            </div>
          </div>
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import {
  getClusterConfig, updateClusterConfig,
  getClusterBoxes, getBoxDetail, clusterHealth
} from '@/api/cluster'

const config = ref({
  role: 'standalone',
  master_url: '',
  station_id: 'A',
  expected_stations: [],
  sync_mode: 'wait_all',
  timeout_sec: 300,
  timeout_push: false,
  enabled: false,
})

const pendingBoxes = ref([])
const recentSummaries = ref([])
const recentTotal = ref(0)
const recentPage = ref(1)
const recentPageSize = 20
const testing = ref(false)
const testResult = ref(null)
const presetStations = ['A', 'B', 'C', 'D']

const showDetail = ref(false)
const detailData = ref(null)
const detailLoading = ref(false)

const formatTime = (t) => t ? t.replace('T', ' ').substring(0, 19) : '-'

const now = () => new Date().toISOString()

const loadMockData = () => {
  pendingBoxes.value = [
    {
      box_serial: 'SN-20260413-001',
      is_complete: false,
      received_stations: ['A', 'B-0'],
      missing_stations: ['B-1', 'C'],
      first_received_at: '2026-04-13T14:30:12',
    },
    {
      box_serial: 'SN-20260413-002',
      is_complete: true,
      received_stations: ['A', 'B-0', 'B-1', 'C'],
      missing_stations: [],
      first_received_at: '2026-04-13T14:28:05',
    },
  ]
  recentSummaries.value = [
    {
      id: 1001, box_serial: 'SN-20260413-000', total_stations: 4, completed_stations: 4,
      overall_result: 'OK', status: 'pushed', pushed_at: '2026-04-13T14:25:33',
    },
    {
      id: 1000, box_serial: 'SN-20260412-088', total_stations: 4, completed_stations: 4,
      overall_result: 'NG', status: 'pushed', pushed_at: '2026-04-12T17:55:10',
    },
    {
      id: 999, box_serial: 'SN-20260412-087', total_stations: 3, completed_stations: 3,
      overall_result: 'OK', status: 'pushed', pushed_at: '2026-04-12T17:50:02',
    },
    {
      id: 998, box_serial: 'SN-20260412-086', total_stations: 3, completed_stations: 3,
      overall_result: 'OK', status: 'completed', pushed_at: null,
    },
    {
      id: 997, box_serial: 'SN-20260412-085', total_stations: 3, completed_stations: 2,
      overall_result: 'TIMEOUT', status: 'pushed_timeout', pushed_at: '2026-04-12T16:40:00',
    },
    {
      id: 996, box_serial: 'SN-20260412-084', total_stations: 3, completed_stations: 1,
      overall_result: 'TIMEOUT', status: 'timeout', pushed_at: null,
    },
  ]
  recentTotal.value = 6
  ElMessage.success('已加载测试数据（仅前端展示，不影响后端）')
}

const showMockDetail = (serial) => {
  showDetail.value = true
  detailLoading.value = false
  detailData.value = {
    box_serial: serial,
    stations: [
      { station_id: 'A', channel_id: null, is_good: true, event_name: 'ok', status: 'dispatched', source: '192.168.0.10:8001', received_at: '2026-04-13T14:30:12' },
      { station_id: 'B', channel_id: 0, is_good: true, event_name: 'ok', status: 'dispatched', source: '192.168.0.20:8001', received_at: '2026-04-13T14:30:18' },
      { station_id: 'B', channel_id: 1, is_good: false, event_name: 'missing_part', status: 'dispatched', source: '192.168.0.20:8001', received_at: '2026-04-13T14:30:20' },
      { station_id: 'C', channel_id: null, is_good: true, event_name: 'weight_ok', status: 'dispatched', source: 'serial:/dev/ttyUSB0:', received_at: '2026-04-13T14:30:25' },
    ],
    summary: {
      overall_result: serial.includes('088') ? 'NG' : 'OK',
      status: 'pushed',
      pushed_at: '2026-04-13T14:30:30',
    },
  }
}

const loadConfig = async () => {
  try {
    const res = await getClusterConfig()
    config.value = { ...config.value, ...res.data }
  } catch (e) {
    ElMessage.error('加载集群配置失败: ' + (e.response?.data?.detail || e.message || '网络错误'))
  }
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
    const res = await getClusterBoxes({
      skip: (recentPage.value - 1) * recentPageSize,
      limit: recentPageSize,
    })
    pendingBoxes.value = res.data.pending || []
    recentSummaries.value = res.data.recent || []
    recentTotal.value = res.data.recent_total || 0
  } catch (e) {
    ElMessage.error('加载数据失败: ' + (e.response?.data?.detail || e.message || '网络错误'))
  }
}

const showBoxDetail = async (boxSerial) => {
  const isMock = pendingBoxes.value.some(b => b.box_serial === boxSerial && b.box_serial.startsWith('SN-'))
    && !pendingBoxes.value._fromServer
  const isMockRecent = recentSummaries.value.some(s => s.box_serial === boxSerial && s.box_serial.startsWith('SN-'))
    && !recentSummaries.value._fromServer
  if (isMock || isMockRecent) {
    showMockDetail(boxSerial)
    return
  }

  showDetail.value = true
  detailLoading.value = true
  detailData.value = null
  try {
    const res = await getBoxDetail(boxSerial)
    detailData.value = res.data
  } catch (e) {
    ElMessage.error('加载明细失败: ' + (e.response?.data?.detail || e.message || '网络错误'))
    showDetail.value = false
  } finally {
    detailLoading.value = false
  }
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

<style scoped>
.mes-dialog :deep(.el-dialog) { background: #1e293b; border: 1px solid #334155; }
.mes-dialog :deep(.el-dialog__title) { color: #e2e8f0; }
</style>
