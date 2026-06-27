<template>
  <div class="space-y-4">
    <div class="text-xs text-gray-500">集群主从配置为整机级全局设置，不随当前项目切换。</div>
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
          <div class="text-xs text-gray-400 mb-1">汇总需要的工位 <span class="text-gray-600">（需要到齐的站点，例如 A、B、C）</span></div>
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

        <!-- B2 计时参数 (心跳/离线/扫描间隔, 默认即现状) -->
        <div v-if="config.role !== 'standalone'" class="mt-3 pt-3 border-t border-slate-700/50">
          <div class="text-xs text-gray-400 mb-2">高级计时（默认即现状，一般无需改）</div>
          <div class="grid grid-cols-3 gap-2">
            <div>
              <div class="text-xs text-gray-500 mb-1">副机心跳间隔(秒)</div>
              <el-input-number v-model="config.heartbeat_interval_sec" :min="1" :max="3600"
                               size="small" class="!w-full" controls-position="right"
                               @change="saveConfig" />
            </div>
            <div>
              <div class="text-xs text-gray-500 mb-1">副机离线超时(秒)</div>
              <el-input-number v-model="config.slave_timeout_sec" :min="2" :max="86400"
                               size="small" class="!w-full" controls-position="right"
                               @change="saveConfig" />
            </div>
            <div>
              <div class="text-xs text-gray-500 mb-1">box扫描间隔(秒)</div>
              <el-input-number v-model="config.box_scan_interval_sec" :min="1" :max="3600"
                               size="small" class="!w-full" controls-position="right"
                               @change="saveConfig" />
            </div>
          </div>
        </div>

        <!-- v3.1.2 站点结果合并策略 -->
        <div v-if="config.role === 'master'" class="mt-3">
          <div class="text-xs text-gray-400 mb-1">站点结果合并策略</div>
          <el-radio-group v-model="config.station_result_strategy" size="small" @change="saveConfig">
            <el-radio value="latest">最新覆盖 (默认)</el-radio>
            <el-radio value="ok_lock">OK 锁定</el-radio>
          </el-radio-group>
          <div class="text-xs text-gray-500 mt-1 leading-relaxed">
            <div v-if="config.station_result_strategy === 'ok_lock'">
              <b class="text-yellow-300">OK 锁定模式</b>: 站点一旦合格 (OK), 后续 NG 数据<b>不能</b>把它改回 NG;
              NG 站点收到 OK 数据时<b>允许翻盘</b>. 被拒的 NG 仍会写入子上报记录, 便于审计追溯.
              适合"返工后第二次检测合格"或"工人补救后重新检测"的场景.
            </div>
            <div v-else>
              <b>最新覆盖</b>: 同一路重复上报取最新; 跨路 / 多次上报中<b>任一 NG 即整站 NG</b>.
              宁可错杀, 适合"必须每次都合格"的高质量管控场景.
            </div>
          </div>
        </div>
      </div>

      <!-- 本机通道 → 站点映射 -->
      <div v-if="config.role !== 'standalone'" class="mt-4 pt-4 border-t border-slate-700">
        <div class="flex items-center justify-between mb-2">
          <div>
            <div class="text-sm text-cyan-300 font-medium">本机通道归属的站点</div>
            <div class="text-xs text-gray-500 mt-0.5">
              本机开了多个视觉工位（通道）时，每个通道各自上报成哪个站点。
              留空则默认：单通道用"本机工位标识"；多通道自动拼成 "{标识}-{通道号}"。
            </div>
          </div>
          <el-button size="small" @click="addChannelMapRow">+ 新增一条</el-button>
        </div>

        <div v-if="channelMapRows.length === 0"
             class="text-xs text-gray-500 bg-slate-900/40 rounded p-3 text-center">
          未配置映射，按默认规则上报。如果本机有多通道且希望都归到同一个站点（例如两路视觉都叫站点 B），请新增映射。
        </div>

        <div v-for="(row, idx) in channelMapRows" :key="idx"
             class="grid grid-cols-[1fr_1fr_auto] gap-2 mb-2 items-center">
          <div>
            <div v-if="idx === 0" class="text-xs text-gray-400 mb-1">通道号</div>
            <el-input-number v-model="row.channel" :min="0" :precision="0"
                             size="small" class="!w-full" controls-position="right"
                             @change="saveChannelMap" />
          </div>
          <div>
            <div v-if="idx === 0" class="text-xs text-gray-400 mb-1">归属站点</div>
            <el-input v-model="row.station" size="small" placeholder="如 A / B / C"
                      @blur="saveChannelMap" />
          </div>
          <div>
            <div v-if="idx === 0" class="text-xs text-gray-400 mb-1">&nbsp;</div>
            <el-button size="small" type="danger" plain @click="removeChannelMapRow(idx)">
              删除
            </el-button>
          </div>
        </div>
      </div>

      <div v-if="testResult" class="mt-3 p-2 rounded text-xs"
           :class="testResult.ok ? 'bg-green-900/30 text-green-300' : 'bg-red-900/30 text-red-300'">
        {{ testResult.message }}
      </div>
    </div>

    <!-- 已连接副机（主机时显示） -->
    <div v-if="config.role === 'master' && config.enabled"
         class="bg-slate-800/60 rounded-lg border border-slate-700 p-4">
      <div class="flex items-center justify-between mb-3">
        <h3 class="text-cyan-300 font-semibold">已连接副机</h3>
        <span class="text-xs" :class="connectedSlaves.length > 0 ? 'text-green-400' : 'text-gray-500'">
          {{ connectedSlaves.length }} 台在线
        </span>
      </div>
      <div v-if="connectedSlaves.length === 0" class="text-gray-500 text-sm text-center py-4">
        暂无副机连接
      </div>
      <div v-else class="grid grid-cols-2 gap-3">
        <div v-for="s in connectedSlaves" :key="s.station_id"
             class="bg-slate-700/40 rounded-lg p-3 border border-green-800/40">
          <div class="flex items-center justify-between mb-1">
            <span class="font-semibold text-green-300">工位 {{ s.station_id }}</span>
            <div class="flex items-center gap-1.5">
              <span class="w-2 h-2 rounded-full bg-green-400 animate-pulse"></span>
              <el-tag v-if="s.detecting" type="success" size="small" effect="dark">检测中</el-tag>
              <el-tag v-else type="info" size="small" effect="dark">待机</el-tag>
            </div>
          </div>
          <div class="text-xs text-gray-400 space-y-0.5">
            <div>IP: <span class="font-mono text-cyan-300">{{ s.ip }}:{{ s.port }}</span></div>
            <div v-if="s.project">项目: {{ s.project }}</div>
            <div v-if="s.hostname">主机名: {{ s.hostname }}</div>
            <div>通道: {{ s.channel_count }} | 在线: {{ formatDuration(s.online_seconds) }}</div>
          </div>
        </div>
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
            <el-button size="small" type="danger" plain
                       :disabled="pendingBoxes.length === 0"
                       @click="confirmClearScope('pending')">清空</el-button>
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
              <div class="flex items-center gap-1.5">
                <el-tag :type="box.is_complete ? 'success' : 'warning'" size="small">
                  {{ box.is_complete ? '已齐' : `缺 ${box.missing_stations.join(', ')}` }}
                </el-tag>
                <el-button size="small" text type="danger" class="!px-1.5 !py-0"
                           @click.stop="confirmDeleteBox(box.box_serial)">删除</el-button>
              </div>
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
          <div class="flex items-center gap-3">
            <span class="text-xs text-gray-500">共 {{ recentTotal }} 条</span>
            <el-button size="small" type="danger" plain
                       :disabled="recentSummaries.length === 0"
                       @click="confirmClearScope('recent')">清空</el-button>
            <el-dropdown trigger="click" @command="handleBatchClearCommand">
              <el-button size="small" type="danger" plain>批量清理</el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item command="older7">清 7 天前</el-dropdown-item>
                  <el-dropdown-item command="older30">清 30 天前</el-dropdown-item>
                  <el-dropdown-item command="older90">清 90 天前</el-dropdown-item>
                  <el-dropdown-item command="older_custom">清 N 天前…</el-dropdown-item>
                  <el-dropdown-item divided command="all">清空全部（含待汇总）</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </div>
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
              <div class="flex items-center gap-1.5">
                <el-tag :type="s.overall_result === 'OK' ? 'success' : s.overall_result === 'TIMEOUT' ? 'warning' : 'danger'" size="small">
                  {{ s.overall_result }}
                </el-tag>
                <el-tag :type="s.status === 'pushed' || s.status === 'pushed_timeout' ? 'success' : s.status === 'timeout' ? 'warning' : 'info'" size="small">
                  {{ {pushed: '已推送', pushed_timeout: '超时已推送', timeout: '超时未推送', complete: '已完成'}[s.status] || s.status }}
                </el-tag>
                <el-button size="small" text type="danger" class="!px-1.5 !py-0"
                           @click.stop="confirmDeleteBox(s.box_serial)">删除</el-button>
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
          <div class="flex items-center gap-2">
            <template v-if="detailData.summary">
              <el-tag :type="detailData.summary.overall_result === 'OK' ? 'success' : detailData.summary.overall_result === 'TIMEOUT' ? 'warning' : 'danger'" size="default">
                {{ detailData.summary.overall_result }}
              </el-tag>
              <el-tag :type="detailData.summary.status === 'pushed' || detailData.summary.status === 'pushed_timeout' ? 'success' : detailData.summary.status === 'timeout' ? 'warning' : 'info'" size="default">
                {{ {pushed: '已推送', pushed_timeout: '超时已推送', timeout: '超时未推送', complete: '已完成'}[detailData.summary.status] || detailData.summary.status }}
              </el-tag>
            </template>
            <el-tag v-else type="warning" size="default">待汇总</el-tag>
            <el-button size="small" type="danger" plain
                       @click="confirmDeleteBox(detailData.box_serial)">删除记录</el-button>
          </div>
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
                <span v-if="getSubReports(st).length > 1" class="text-xs text-gray-500">
                  · {{ getSubReports(st).length }} 路汇总
                </span>
                <span v-else-if="st.channel_id != null" class="text-xs text-gray-500">通道 {{ st.channel_id }}</span>
                <span v-if="describeStationKind(st)" class="text-xs text-gray-500">· {{ describeStationKind(st) }}</span>
              </div>
              <el-tag :type="st.is_good ? 'success' : 'danger'" size="small">
                {{ st.is_good ? 'OK' : 'NG' }}
              </el-tag>
            </div>

            <template v-if="getSubReports(st).length > 1">
              <div class="text-xs text-gray-500 mb-1">合并规则: 任一路 NG 即整站 NG</div>
              <div class="space-y-1.5 mt-1.5 pl-2 border-l-2 border-slate-600/60">
                <div v-for="(sub, i) in getSubReports(st)" :key="i" class="text-sm">
                  <div class="flex items-center gap-2 mb-0.5">
                    <span class="text-xs text-gray-400">
                      通道 {{ sub.channel_id ?? '-' }}
                    </span>
                    <el-tag :type="sub.is_good ? 'success' : 'danger'" size="small">
                      {{ sub.is_good ? 'OK' : 'NG' }}
                    </el-tag>
                  </div>
                  <div :class="sub.is_good ? 'text-gray-300' : 'text-red-300'">
                    {{ describeSubReport(sub) || (sub.is_good ? '通过' : '未通过') }}
                  </div>
                </div>
              </div>
            </template>

            <div v-else-if="describeStationDetail(st)" class="text-sm mb-1"
                 :class="st.is_good ? 'text-gray-200' : 'text-red-300'">
              {{ describeStationDetail(st) }}
            </div>

            <div class="text-xs text-gray-400 space-y-0.5 mt-1">
              <div v-if="st.source">来源: {{ st.source }}</div>
              <div>最后送达 · {{ formatTime(st.received_at) }}</div>
            </div>
          </div>
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  getClusterConfig, updateClusterConfig,
  getClusterBoxes, getBoxDetail, clusterHealth,
  sendHeartbeat, getConnectedSlaves,
  deleteBox, clearBoxes,
} from '@/api/cluster'
import { dbg, dbgErr } from '@/utils/debug'
import { usePollingStore } from '@/store/usePollingStore'

const pollingStore = usePollingStore()

const config = ref({
  role: 'standalone',
  master_url: '',
  station_id: 'A',
  expected_stations: [],
  sync_mode: 'wait_all',
  timeout_sec: 300,
  timeout_push: false,
  enabled: false,
  channel_station_map: {},
  station_result_strategy: 'latest',
  heartbeat_interval_sec: 5,
  slave_timeout_sec: 20,
  box_scan_interval_sec: 30,
})

// 本机多通道场景下，每个视觉通道各自归属哪个站点。
// 例如机器B的两路视觉都想合并到站点B时：{"0":"B","1":"B"}；
// 两路视觉分别代表B、C两个站点时：{"0":"B","1":"C"}。
// 数据本身是字典，为方便编辑，在页面里用数组行形式展开。
const channelMapRows = ref([])

const addChannelMapRow = () => {
  channelMapRows.value.push({ channel: '', station: '' })
}
const removeChannelMapRow = (idx) => {
  channelMapRows.value.splice(idx, 1)
  saveChannelMap()
}
const saveChannelMap = () => {
  const map = {}
  for (const row of channelMapRows.value) {
    const ch = String(row.channel ?? '').trim()
    const st = String(row.station ?? '').trim()
    if (ch === '' || st === '') continue
    map[ch] = st
  }
  config.value.channel_station_map = map
  saveConfig()
}

const pendingBoxes = ref([])
const recentSummaries = ref([])
const recentTotal = ref(0)
const recentPage = ref(1)
const recentPageSize = computed(() => pollingStore.logLimit('cluster', 20))
const testing = ref(false)
const testResult = ref(null)
const presetStations = ['A', 'B', 'C', 'D']

const showDetail = ref(false)
const detailData = ref(null)
const detailLoading = ref(false)
const connectedSlaves = ref([])

// 后端返回的是 naive UTC ISO（server_default=func.now()）。
// 直接字符串切片会显示 UTC，比北京时间晚 8 小时。
// 统一按 UTC 解析 → 浏览器本地时区显示（国内=北京时间）。
const formatTime = (t) => {
  if (!t) return '-'
  const hasTz = /Z$|[+-]\d{2}:?\d{2}$/.test(t)
  const s = hasTz ? t : (String(t).replace(' ', 'T') + 'Z')
  const d = new Date(s)
  if (isNaN(d.getTime())) return t
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} `
    + `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

const formatDuration = (seconds) => {
  if (!seconds || seconds < 60) return `${seconds || 0}秒`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}分${seconds % 60}秒`
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  return `${h}时${m}分`
}

// 同站点多路上报时, 后端会把每路快照塞到 cycle_context.sub_reports.
// 单路上报时此数组不存在, 返回空数组, 由调用方决定回退到旧渲染逻辑.
const getSubReports = (st) => {
  const arr = st?.cycle_context?.sub_reports
  return Array.isArray(arr) ? arr : []
}

// 把单路 sub_report 渲染成业务可读文字
const describeSubReport = (sub) => {
  if (!sub) return ''
  if (sub.device_role === 'weight' || sub.device_role === 'sensor') {
    const data = sub.device_data || {}
    const v = data.weight ?? data._raw_value ?? data.raw
    if (v != null) {
      return sub.device_role === 'weight' ? `重量 ${v} kg` : `读数 ${v}`
    }
  }
  if (!sub.is_good) {
    if (sub.ng_reason) return `缺少: ${sub.ng_reason}`
    if (sub.event_name && sub.event_name !== 'ng') return `原因: ${sub.event_name}`
    return '未通过'
  }
  if (sub.duration != null) return `耗时 ${Number(sub.duration).toFixed(2)} 秒`
  return ''
}

// 根据 cycle_context 判断工位类别，给出一句话标签（"视觉检测" / "称重" 等）
const describeStationKind = (st) => {
  const ctx = st?.cycle_context || {}
  const role = ctx.device_role
  if (role === 'weight') return '称重'
  if (role === 'sensor') return '传感器'
  if (role) return role
  if (Array.isArray(ctx.steps) || ctx.cycle) return '视觉检测'
  return ''
}

// 根据 cycle_context 拼一段业务可读的详情：
//   - 视觉 NG：列出缺的步骤标签（ng_reason）
//   - 称重  ：显示重量数值（带单位）、越限时附原因
//   - 视觉 OK：不啰嗦，空
const describeStationDetail = (st) => {
  const ctx = st?.cycle_context || {}
  const role = ctx.device_role

  if (role === 'weight' || role === 'sensor') {
    const data = ctx.device_data || {}
    const weight = data.weight ?? data._raw_value
    const parts = []
    if (weight != null) {
      parts.push(role === 'weight' ? `重量 ${weight} kg` : `读数 ${weight}`)
    } else if (data.raw) {
      parts.push(`读数 ${data.raw}`)
    }
    if (!st.is_good && st.event_name && st.event_name !== 'ok') {
      parts.push(`判定: ${st.event_name}`)
    }
    return parts.join(' · ')
  }

  if (!st.is_good) {
    const reason = ctx.cycle?.ng_reason
    if (reason) return `缺少: ${reason}`
    if (st.event_name && st.event_name !== 'ng') return `原因: ${st.event_name}`
    return '未通过'
  }

  const dur = ctx.cycle?.duration
  if (dur != null) return `耗时 ${Number(dur).toFixed(2)} 秒`
  return ''
}

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
      { station_id: 'A', channel_id: null, is_good: true, event_name: 'ok', status: 'dispatched', source: '192.168.0.10:8001', received_at: '2026-04-13T14:30:12',
        cycle_context: { cycle: { result: 'OK', duration: 18.2 } } },
      { station_id: 'B', channel_id: 0, is_good: true, event_name: 'ok', status: 'dispatched', source: '192.168.0.20:8001', received_at: '2026-04-13T14:30:18',
        cycle_context: { cycle: { result: 'OK', duration: 22.5 } } },
      { station_id: 'B', channel_id: 1, is_good: false, event_name: 'missing_part', status: 'dispatched', source: '192.168.0.20:8001', received_at: '2026-04-13T14:30:20',
        cycle_context: { cycle: { result: 'NG', ng_reason: '线槽: 0/1, 侧板: 1/2' } } },
      { station_id: 'C', channel_id: null, is_good: true, event_name: 'weight_ok', status: 'dispatched', source: 'tcp:127.0.0.1:9001', received_at: '2026-04-13T14:30:25',
        cycle_context: { device_role: 'weight', device_name: '1111', device_data: { weight: 70.0 } } },
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
    const map = config.value.channel_station_map || {}
    channelMapRows.value = Object.keys(map)
      .sort((a, b) => Number(a) - Number(b))
      .map(k => ({ channel: k, station: map[k] }))
  } catch (e) {
    dbgErr('mes.cluster', '加载集群配置', e)
    ElMessage.error('加载集群配置失败: ' + (e.response?.data?.detail || e.message || '网络错误'))
  }
}

const saveConfig = async () => {
  dbg('mes.cluster', '保存集群配置', `role=${config.value?.role || ''} enabled=${config.value?.enabled} station=${config.value?.station_id || ''}`)
  try {
    const res = await updateClusterConfig(config.value)
    config.value = { ...config.value, ...res.data }
    ElMessage.success('集群配置已保存')
  } catch (e) {
    dbgErr('mes.cluster', '保存集群配置', e)
    ElMessage.error('保存失败: ' + (e.response?.data?.detail || e.message))
  }
}

const loadBoxes = async () => {
  try {
    const res = await getClusterBoxes({
      skip: (recentPage.value - 1) * recentPageSize.value,
      limit: recentPageSize.value,
    })
    pendingBoxes.value = res.data.pending || []
    recentSummaries.value = res.data.recent || []
    recentTotal.value = res.data.recent_total || 0
  } catch (e) {
    ElMessage.error('加载数据失败: ' + (e.response?.data?.detail || e.message || '网络错误'))
  }
}

const showBoxDetail = async (boxSerial) => {
  // "测试数据"按钮注入的 mock 条目固定用 SN-2026xxxx-xxx 这种日期式长串,
  // 真实扫码流程里的 box_serial 是用户自定义的业务编号(例如 SN-0017),
  // 之前用 startsWith('SN-') 判定会把真数据也当成 mock → 弹出假明细.
  const isMockSerial = (s) => typeof s === 'string' && /^SN-2026\d{4}-\d{3}$/.test(s)
  if (isMockSerial(boxSerial)) {
    showMockDetail(boxSerial)
    return
  }

  dbg('mes.cluster', '点击「box 明细」', `box_serial=${boxSerial ?? ''}`)
  showDetail.value = true
  detailLoading.value = true
  detailData.value = null
  try {
    const res = await getBoxDetail(boxSerial)
    detailData.value = res.data
  } catch (e) {
    dbgErr('mes.cluster', '加载 box 明细', e)
    ElMessage.error('加载明细失败: ' + (e.response?.data?.detail || e.message || '网络错误'))
    showDetail.value = false
  } finally {
    detailLoading.value = false
  }
}

const confirmDeleteBox = async (boxSerial) => {
  dbg('mes.cluster', '点击「删除 box」', `box_serial=${boxSerial ?? ''}`)
  // 前端 Mock 条目（box_serial 以 SN-2026 开头且未来自服务器）直接本地清掉，不发后端
  const inMockPending = pendingBoxes.value.some(b =>
    b.box_serial === boxSerial && boxSerial.startsWith('SN-2026'))
  const inMockRecent = recentSummaries.value.some(s =>
    s.box_serial === boxSerial && boxSerial.startsWith('SN-2026'))
  if (inMockPending || inMockRecent) {
    pendingBoxes.value = pendingBoxes.value.filter(b => b.box_serial !== boxSerial)
    recentSummaries.value = recentSummaries.value.filter(s => s.box_serial !== boxSerial)
    recentTotal.value = Math.max(0, recentTotal.value - 1)
    ElMessage.success(`已移除测试数据 ${boxSerial}`)
    return
  }
  try {
    await ElMessageBox.confirm(
      `确定要删除目标 ${boxSerial} 的所有记录吗？此操作不可恢复。`,
      '删除确认',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消',
        confirmButtonClass: 'el-button--danger' }
    )
  } catch { return }
  try {
    const res = await deleteBox(boxSerial)
    ElMessage.success(`已删除 ${boxSerial}（工位记录 ${res.data.aggregations}、汇总 ${res.data.summaries}）`)
    await loadBoxes()
    if (showDetail.value && detailData.value?.box_serial === boxSerial) {
      showDetail.value = false
    }
  } catch (e) {
    dbgErr('mes.cluster', '删除 box', e)
    ElMessage.error('删除失败: ' + (e.response?.data?.detail || e.message))
  }
}

const confirmClearScope = async (scope) => {
  dbg('mes.cluster', '点击「批量清空 box」', `scope=${scope ?? ''}`)
  const text = scope === 'pending' ? '所有待汇总目标'
             : scope === 'recent'  ? '所有已完成记录'
             : scope === 'all'     ? '全部集群汇总记录（待汇总 + 已完成）'
             : '所选记录'
  try {
    await ElMessageBox.confirm(
      `确定要清空${text}吗？此操作不可恢复。\n原始检测记录（数据页、工件追溯、已推送 MES 的数据）不会被删除。`,
      '批量清空',
      { type: 'warning', confirmButtonText: '清空', cancelButtonText: '取消',
        confirmButtonClass: 'el-button--danger' }
    )
  } catch { return }
  try {
    const res = await clearBoxes(scope)
    ElMessage.success(`已清空${text}（工位记录 ${res.data.aggregations}、汇总 ${res.data.summaries}）`)
    recentPage.value = 1
    await loadBoxes()
  } catch (e) {
    dbgErr('mes.cluster', '批量清空 box', e)
    ElMessage.error('清空失败: ' + (e.response?.data?.detail || e.message))
  }
}

const confirmClearOlder = async (presetDays = null) => {
  let days = presetDays
  if (!days) {
    try {
      const { value } = await ElMessageBox.prompt(
        '清除多少天以前的记录？',
        '清理旧记录',
        { inputPattern: /^[1-9]\d{0,3}$/, inputErrorMessage: '请输入 1-9999 的整数天数',
          inputValue: '30', confirmButtonText: '清理', cancelButtonText: '取消' }
      )
      days = parseInt(value, 10)
    } catch { return }
  }
  if (!days || days <= 0) return
  try {
    await ElMessageBox.confirm(
      `确定要清除 ${days} 天以前的所有集群汇总记录吗？此操作不可恢复。\n原始检测记录（数据页、工件追溯、已推送 MES 的数据）不会被删除。`,
      '清理旧记录',
      { type: 'warning', confirmButtonText: '清理', cancelButtonText: '取消',
        confirmButtonClass: 'el-button--danger' }
    )
  } catch { return }
  try {
    const res = await clearBoxes('older', days)
    ElMessage.success(`已清除 ${days} 天前的记录（工位记录 ${res.data.aggregations}、汇总 ${res.data.summaries}）`)
    recentPage.value = 1
    await loadBoxes()
  } catch (e) {
    ElMessage.error('清理失败: ' + (e.response?.data?.detail || e.message))
  }
}

const handleBatchClearCommand = (command) => {
  if (command === 'all') return confirmClearScope('all')
  if (command === 'older7') return confirmClearOlder(7)
  if (command === 'older30') return confirmClearOlder(30)
  if (command === 'older90') return confirmClearOlder(90)
  if (command === 'older_custom') return confirmClearOlder()
}

const testMaster = async () => {
  dbg('mes.cluster', '点击「测试主机连接」', `master_url=${config.value?.master_url || ''}`)
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
    dbgErr('mes.cluster', '测试主机连接', e)
    testResult.value = { ok: false, message: `连接失败: ${e.message}` }
  } finally {
    testing.value = false
  }
}

const loadSlaves = async () => {
  if (config.value.role !== 'master' || !config.value.enabled) return
  try {
    const res = await getConnectedSlaves()
    connectedSlaves.value = res.data.slaves || []
  } catch { /* ignore */ }
}

const doHeartbeat = async () => {
  if (config.value.role !== 'slave' || !config.value.enabled || !config.value.master_url) return
  try {
    const url = config.value.master_url.replace(/\/$/, '') + '/api/v1/cluster/heartbeat'
    await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        station_id: config.value.station_id || 'unknown',
        port: 8001,
        hostname: '',
        project: '',
        channel_count: 1,
        detecting: false,
      }),
      signal: AbortSignal.timeout(5000),
    })
  } catch { /* ignore */ }
}

let refreshTimer = null
let heartbeatTimer = null
let slaveTimer = null
onMounted(async () => {
  await pollingStore.load()
  loadConfig().then(() => {
    doHeartbeat()
    loadSlaves()
  })
  loadBoxes()
  refreshTimer = setInterval(loadBoxes, pollingStore.get('cluster_boxes', 5000))
  heartbeatTimer = setInterval(doHeartbeat, pollingStore.get('cluster_heartbeat', 10000))
  slaveTimer = setInterval(loadSlaves, pollingStore.get('cluster_slaves', 5000))
})
onUnmounted(() => {
  if (refreshTimer) clearInterval(refreshTimer)
  if (heartbeatTimer) clearInterval(heartbeatTimer)
  if (slaveTimer) clearInterval(slaveTimer)
})
</script>

<style scoped>
.mes-dialog :deep(.el-dialog) { background: #1e293b; border: 1px solid #334155; }
.mes-dialog :deep(.el-dialog__title) { color: #e2e8f0; }
</style>
