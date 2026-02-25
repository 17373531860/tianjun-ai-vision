<template>
  <div class="p-6 h-full overflow-y-auto">
    <!-- 标题和项目同步显示 -->
    <div class="flex justify-between items-center mb-6">
      <h2 class="text-2xl font-bold border-l-4 border-tech-blue pl-3 text-white">数据管理</h2>
      <div class="flex items-center gap-4">
        <span class="text-gray-400 text-sm">当前项目:</span>
        <span class="text-cyan-400 font-bold">{{ currentProjectName || '未选择' }}</span>
        <el-button v-if="!projectStore.currentProjectId" type="primary" size="small" @click="goToProjectSelect">
          去选择项目
        </el-button>
      </div>
    </div>

    <!-- 未选择项目提示 -->
    <div v-if="!projectStore.currentProjectId" class="text-center py-20">
      <el-icon :size="64" class="text-gray-600 mb-4"><Folder /></el-icon>
      <p class="text-gray-400 text-lg mb-4">请先从顶部导航栏选择一个项目</p>
      <el-button type="primary" @click="goToProjectSelect">前往项目管理</el-button>
    </div>

    <div v-else class="grid grid-cols-1 lg:grid-cols-3 gap-6">
      <!-- 左侧：实时数据 + 日期选择 + 启动记录 -->
      <div class="lg:col-span-1 space-y-6">
        <!-- 实时数据概览（与Monitor同步） -->
        <section class="bg-ind-panel p-4 rounded-xl border border-gray-800">
          <h3 class="text-lg font-bold mb-4 flex items-center text-tech-blue">
            <el-icon class="mr-2"><DataLine /></el-icon> 实时数据概览
          </h3>
          <div class="space-y-3">
            <div 
              v-for="counter in currentCounters" 
              :key="counter.name"
              class="flex justify-between items-center p-3 bg-slate-900 rounded-lg border border-slate-700"
            >
              <span class="text-gray-300 text-sm">{{ counter.name }}</span>
              <span 
                class="text-xl font-mono font-bold"
                :class="getCounterColor(counter.name)"
              >
                {{ counter.value.toLocaleString() }}
              </span>
            </div>
            <div v-if="currentCounters.length === 0" class="text-center text-gray-500 py-4">
              暂无计数器数据
            </div>
          </div>
        </section>

        <!-- 历史数据查询 -->
        <section class="bg-ind-panel p-4 rounded-xl border border-gray-800">
          <h3 class="text-lg font-bold mb-4 flex items-center text-cyan-400">
            <el-icon class="mr-2"><Calendar /></el-icon> 历史数据查询
          </h3>
          <el-date-picker
            v-model="selectedDate"
            type="date"
            placeholder="选择日期查看历史"
            format="YYYY-MM-DD"
            value-format="YYYY-MM-DD"
            class="w-full"
            :disabled-date="disabledDate"
            @change="handleDateChange"
          />
          <div v-if="availableDates.length > 0" class="mt-3 text-xs text-gray-400">
            <span>有数据的日期: </span>
            <span class="text-cyan-400">{{ availableDates.length }} 天</span>
          </div>
        </section>

        <!-- 启动记录 -->
        <section class="bg-ind-panel p-4 rounded-xl border border-gray-800">
          <h3 class="text-lg font-bold mb-4 flex items-center text-green-400">
            <el-icon class="mr-2"><Clock /></el-icon> 启动记录
          </h3>
          <div v-loading="loadingSessions" class="space-y-2 max-h-[300px] overflow-y-auto">
            <div v-if="sessions.length === 0" class="text-center text-gray-500 py-6">
              {{ selectedDate ? '当日无检测记录' : '请选择日期' }}
            </div>
            <div
              v-for="session in sessions"
              :key="session.id"
              class="p-3 bg-slate-800 rounded-lg border cursor-pointer transition-all"
              :class="selectedSession?.id === session.id 
                ? 'border-cyan-500 bg-slate-700' 
                : 'border-slate-700 hover:border-slate-500'"
              @click="selectSession(session)"
            >
              <div class="flex justify-between items-center">
                <div class="flex-1">
                  <div class="text-white font-mono text-sm">
                    {{ formatTime(session.start_time) }}
                    <span v-if="session.end_time" class="text-gray-400"> - {{ formatTime(session.end_time) }}</span>
                  </div>
                  <div class="text-xs mt-1">
                    <el-tag 
                      :type="getStatusType(session.status)" 
                      size="small"
                    >
                      {{ getStatusText(session.status) }}
                    </el-tag>
                  </div>
                </div>
                <div class="text-right mr-2">
                  <div class="text-cyan-400 font-mono text-sm">{{ session.total_cycles || 0 }} 轮</div>
                  <div class="text-xs">
                    <span class="text-green-400">{{ session.good_cycles || 0 }}</span> /
                    <span class="text-red-400">{{ session.ng_cycles || 0 }}</span>
                  </div>
                </div>
                <!-- 会话视频播放按钮 -->
                <el-button
                  v-if="session.video_id"
                  type="primary"
                  size="small"
                  circle
                  @click.stop="playSessionVideo(session)"
                >
                  <el-icon><VideoPlay /></el-icon>
                </el-button>
                <el-tooltip v-else content="无会话视频" placement="top">
                  <el-button type="info" size="small" circle disabled>
                    <el-icon><VideoPlay /></el-icon>
                  </el-button>
                </el-tooltip>
              </div>
            </div>
          </div>
        </section>
      </div>

      <!-- 右侧：历史数据详情 -->
      <div class="lg:col-span-2 space-y-6">
        <!-- 历史统计概览 -->
        <section class="bg-ind-panel p-6 rounded-xl border border-gray-800">
          <div class="flex justify-between items-center mb-4">
            <h3 class="text-lg font-bold flex items-center text-purple-400">
              <el-icon class="mr-2"><TrendCharts /></el-icon> 
              {{ selectedSession ? '会话统计' : (selectedDate ? '当日统计' : '历史统计') }}
            </h3>
            <div v-if="selectedSession" class="text-sm text-gray-400">
              会话: {{ selectedSession.session_uuid }}
            </div>
          </div>

          <div v-loading="loadingOverview">
            <!-- 历史计数器数据 -->
            <div v-if="Object.keys(historyCounters).length > 0" class="mb-6">
              <h4 class="text-sm font-bold text-gray-300 mb-3">计数器快照</h4>
              <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
                <div 
                  v-for="(value, name) in historyCounters" 
                  :key="name"
                  class="bg-slate-900 p-3 rounded-lg text-center border border-slate-700"
                >
                  <div class="text-gray-400 text-xs mb-1 truncate" :title="name">{{ name }}</div>
                  <div class="text-xl font-mono text-white">{{ value.toLocaleString() }}</div>
                </div>
              </div>
            </div>

            <!-- 时间统计 -->
            <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
              <div class="bg-slate-900 p-4 rounded-lg text-center border border-slate-700">
                <div class="text-gray-400 text-xs mb-1">总周期数</div>
                <div class="text-2xl font-mono text-cyan-400">{{ overviewData.total_cycles }}</div>
              </div>
              <div class="bg-slate-900 p-4 rounded-lg text-center border border-slate-700">
                <div class="text-gray-400 text-xs mb-1">平均周期时间</div>
                <div class="text-2xl font-mono text-blue-400">{{ formatDuration(overviewData.avg_cycle_time) }}</div>
              </div>
              <div class="bg-slate-900 p-4 rounded-lg text-center border border-slate-700">
                <div class="text-gray-400 text-xs mb-1">良率</div>
                <div class="text-2xl font-mono" :class="overviewData.yield_rate >= 95 ? 'text-green-400' : 'text-yellow-400'">
                  {{ overviewData.yield_rate.toFixed(1) }}%
                </div>
              </div>
              <div class="bg-slate-900 p-4 rounded-lg text-center border border-slate-700">
                <div class="text-gray-400 text-xs mb-1">合格 / 不良</div>
                <div class="text-2xl font-mono">
                  <span class="text-green-400">{{ overviewData.good_cycles }}</span>
                  <span class="text-gray-500"> / </span>
                  <span class="text-red-400">{{ overviewData.ng_cycles }}</span>
                </div>
              </div>
            </div>

            <!-- 步骤耗时统计 -->
            <div v-if="stepStats.length > 0" class="mb-6">
              <h4 class="text-sm font-bold text-gray-300 mb-3">步骤耗时统计（仅正常轮次）</h4>
              <el-table :data="stepStats" size="small" class="dark-table">
                <el-table-column prop="name" label="步骤名称" />
                <el-table-column prop="count" label="次数" width="80" align="center" />
                <el-table-column label="耗时 (秒)" width="150" align="center">
                  <template #default="{ row }">
                    <span class="text-cyan-400">{{ row.avg_duration.toFixed(2) }}</span>
                    <span class="text-gray-500 text-xs ml-1">({{ row.min_duration.toFixed(1) }}-{{ row.max_duration.toFixed(1) }})</span>
                  </template>
                </el-table-column>
                <el-table-column label="到下步间隔 (秒)" width="160" align="center">
                  <template #default="{ row, $index }">
                    <template v-if="$index < stepStats.length - 1">
                      <span class="text-blue-400">{{ row.avg_interval.toFixed(2) }}</span>
                      <span class="text-gray-500 text-xs ml-1">({{ row.min_interval.toFixed(1) }}-{{ row.max_interval.toFixed(1) }})</span>
                    </template>
                    <span v-else class="text-gray-500">-</span>
                  </template>
                </el-table-column>
              </el-table>
            </div>

            <!-- 周期详情（可展开显示步骤） -->
            <div v-if="selectedSession && cycles.length > 0">
              <div class="flex justify-between items-center mb-3">
                <h4 class="text-sm font-bold text-gray-300">周期详情</h4>
                <el-button size="small" type="primary" plain @click="exportSessionData">
                  <el-icon class="mr-1"><Download /></el-icon> 导出此会话
                </el-button>
              </div>
              <el-table 
                :data="cycles" 
                size="small" 
                class="dark-table" 
                max-height="400"
                row-key="id"
                :expand-row-keys="expandedRows"
                @expand-change="handleExpandChange"
              >
                <el-table-column type="expand">
                  <template #default="{ row }">
                    <div class="p-4 bg-slate-800">
                      <div v-if="cycleStepsMap[row.id]" class="space-y-2">
                        <div 
                          v-for="step in cycleStepsMap[row.id]" 
                          :key="step.id"
                          class="flex items-center justify-between p-2 bg-slate-700 rounded cursor-pointer hover:bg-slate-600"
                          @click="playStepVideo(step)"
                        >
                          <div class="flex items-center gap-3">
                            <span class="text-gray-400 text-xs w-8">#{{ step.step_order }}</span>
                            <span class="text-white">{{ step.step_name || step.step_label }}</span>
                          </div>
                          <div class="flex items-center gap-4 text-sm">
                            <span class="text-gray-400">{{ formatTime(step.start_time) }}</span>
                            <span class="text-cyan-400">{{ step.duration ? step.duration.toFixed(2) + 's' : '-' }}</span>
                            <span v-if="step.interval_to_next" class="text-blue-400">
                              → {{ step.interval_to_next.toFixed(2) }}s
                            </span>
                            <el-icon v-if="step.video_id" class="text-purple-400"><VideoPlay /></el-icon>
                          </div>
                        </div>
                      </div>
                      <div v-else class="text-center text-gray-500 py-2">
                        加载中...
                      </div>
                    </div>
                  </template>
                </el-table-column>
                <el-table-column prop="cycle_number" label="#" width="50" align="center" />
                <el-table-column label="开始时间" width="90">
                  <template #default="{ row }">{{ formatTime(row.start_time) }}</template>
                </el-table-column>
                <el-table-column label="耗时" width="70" align="center">
                  <template #default="{ row }">
                    <span class="text-cyan-400">{{ row.duration ? row.duration.toFixed(2) + 's' : '-' }}</span>
                  </template>
                </el-table-column>
                <el-table-column label="结果" width="70" align="center">
                  <template #default="{ row }">
                    <el-tag :type="row.is_good ? 'success' : 'danger'" size="small">
                      {{ row.is_good ? 'OK' : 'NG' }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column prop="event_name" label="事件" min-width="80" />
                <el-table-column label="操作" width="80" align="center">
                  <template #default="{ row }">
                    <el-button 
                      v-if="row.video_id" 
                      type="primary" 
                      link 
                      size="small"
                      @click="playCycleVideo(row)"
                    >
                      <el-icon><VideoPlay /></el-icon>
                    </el-button>
                  </template>
                </el-table-column>
              </el-table>
            </div>

            <!-- 无数据提示 -->
            <div v-if="!selectedDate && !selectedSession" class="text-center text-gray-500 py-8">
              选择左侧日期查看历史数据
            </div>
          </div>
        </section>

        <!-- 记录设置和数据导出 -->
        <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
          <!-- 记录设置 -->
          <section class="bg-ind-panel p-6 rounded-xl border border-gray-800">
            <h3 class="text-lg font-bold mb-4 flex items-center text-purple-400">
              <el-icon class="mr-2"><Setting /></el-icon> 记录设置
            </h3>
            <div class="space-y-3">
              <div class="text-sm text-gray-400 mb-2">数据记录</div>
              <div class="flex justify-between items-center py-1">
                <span class="text-sm text-gray-300">记录步骤耗时</span>
                <el-switch v-model="exportSettings.record_step_duration" @change="saveExportSettings" />
              </div>
              <div class="flex justify-between items-center py-1">
                <span class="text-sm text-gray-300">记录步骤间隔</span>
                <el-switch v-model="exportSettings.record_step_interval" @change="saveExportSettings" />
              </div>
              <div class="flex justify-between items-center py-1">
                <span class="text-sm text-gray-300">记录周期耗时</span>
                <el-switch v-model="exportSettings.record_cycle_duration" @change="saveExportSettings" />
              </div>
              <div class="flex justify-between items-center py-1">
                <span class="text-sm text-gray-300">记录周期间隔</span>
                <el-switch v-model="exportSettings.record_cycle_interval" @change="saveExportSettings" />
              </div>
              <div class="flex justify-between items-center py-1">
                <span class="text-sm text-gray-300">记录计数器</span>
                <el-switch v-model="exportSettings.record_counters" @change="saveExportSettings" />
              </div>
              
              <el-divider class="my-3" />
              
              <div class="text-sm text-gray-400 mb-2">视频录制</div>
              <div class="flex justify-between items-center py-1">
                <span class="text-sm text-gray-300">录制步骤视频</span>
                <el-switch v-model="exportSettings.record_step_video" @change="saveExportSettings" />
              </div>
              <div class="flex justify-between items-center py-1">
                <span class="text-sm text-gray-300">录制周期视频</span>
                <el-switch v-model="exportSettings.record_cycle_video" @change="saveExportSettings" />
              </div>
              <div class="flex justify-between items-center py-1">
                <span class="text-sm text-gray-300">录制会话视频</span>
                <el-switch v-model="exportSettings.record_session_video" @change="saveExportSettings" />
              </div>
              
              <div v-if="exportSettings.record_step_video || exportSettings.record_cycle_video || exportSettings.record_session_video" class="mt-3">
                <div class="flex justify-between items-center py-1">
                  <span class="text-sm text-gray-300">视频质量</span>
                  <el-select v-model="exportSettings.video_quality" size="small" style="width: 100px" @change="saveExportSettings">
                    <el-option label="低" value="low" />
                    <el-option label="中" value="medium" />
                    <el-option label="高" value="high" />
                  </el-select>
                </div>
                <div class="flex justify-between items-center py-1">
                  <span class="text-sm text-gray-300">帧率</span>
                  <el-input-number v-model="exportSettings.video_fps" :min="10" :max="60" size="small" style="width: 100px" @change="saveExportSettings" />
                </div>
              </div>
            </div>
          </section>

          <!-- 数据导出 -->
          <section class="bg-ind-panel p-6 rounded-xl border border-gray-800">
            <h3 class="text-lg font-bold mb-4 flex items-center text-white">
              <el-icon class="mr-2"><Download /></el-icon> 数据导出
            </h3>
            <div class="space-y-3">
              <!-- 导出设置 -->
              <el-collapse>
                <el-collapse-item title="导出设置（选择导出项）" name="exportConfig">
                  <div class="space-y-2 px-2">
                    <div class="flex justify-between items-center py-1">
                      <span class="text-sm text-gray-300">导出会话信息</span>
                      <el-switch v-model="exportSettings.export_session_info" @change="saveExportSettings" size="small" />
                    </div>
                    <div class="flex justify-between items-center py-1">
                      <span class="text-sm text-gray-300">导出计数器</span>
                      <el-switch v-model="exportSettings.export_counters" @change="saveExportSettings" size="small" />
                    </div>
                    <div class="flex justify-between items-center py-1">
                      <span class="text-sm text-gray-300">导出周期结果</span>
                      <el-switch v-model="exportSettings.export_cycle_result" @change="saveExportSettings" size="small" />
                    </div>
                    <div class="flex justify-between items-center py-1">
                      <span class="text-sm text-gray-300">导出周期耗时</span>
                      <el-switch v-model="exportSettings.export_cycle_duration" @change="saveExportSettings" size="small" />
                    </div>
                    <div class="flex justify-between items-center py-1">
                      <span class="text-sm text-gray-300">导出周期间隔</span>
                      <el-switch v-model="exportSettings.export_cycle_interval" @change="saveExportSettings" size="small" />
                    </div>
                    <div class="flex justify-between items-center py-1">
                      <span class="text-sm text-gray-300">导出步骤耗时</span>
                      <el-switch v-model="exportSettings.export_step_duration" @change="saveExportSettings" size="small" />
                    </div>
                    <div class="flex justify-between items-center py-1">
                      <span class="text-sm text-gray-300">导出步骤间隔</span>
                      <el-switch v-model="exportSettings.export_step_interval" @change="saveExportSettings" size="small" />
                    </div>
                    <div class="flex justify-between items-center py-1">
                      <span class="text-sm text-gray-300">导出步骤事件</span>
                      <el-switch v-model="exportSettings.export_step_event" @change="saveExportSettings" size="small" />
                    </div>
                  </div>
                </el-collapse-item>
              </el-collapse>
              
              <el-button 
                type="primary" 
                class="w-full" 
                @click="exportByDate" 
                :loading="exporting" 
                :disabled="!selectedDate"
              >
                导出当日数据
              </el-button>
              <el-button type="primary" plain class="w-full" @click="showExportDialog('week')">
                导出某周数据
              </el-button>
              <el-button type="primary" plain class="w-full" @click="showExportDialog('month')">
                导出某月数据
              </el-button>
              <el-button type="primary" plain class="w-full" @click="showExportDialog('range')">
                导出日期范围
              </el-button>
              
              <el-divider class="my-3" />
              
              <el-button type="warning" plain class="w-full" @click="handleBackup">
                备份数据库
              </el-button>
              <el-button type="danger" plain class="w-full" @click="handleClearData" :loading="clearing">
                清空所有历史数据
              </el-button>
            </div>
          </section>

          <!-- 数据清理设置 -->
          <section class="bg-ind-panel p-4 rounded-xl border border-gray-800">
            <h3 class="text-lg font-bold mb-4 flex items-center text-orange-400">
              <el-icon class="mr-2"><Delete /></el-icon> 数据清理
            </h3>
            <div class="space-y-4">
              <!-- 存储信息 -->
              <div class="bg-slate-900 rounded-lg p-3 border border-slate-700">
                <div class="text-xs text-gray-400 mb-2">存储空间</div>
                <div class="flex justify-between text-sm mb-1">
                  <span class="text-gray-300">应用数据</span>
                  <span class="text-cyan-400 font-mono">{{ formatSize(storageInfo.data_size_mb) }}</span>
                </div>
                <div class="flex justify-between text-xs text-gray-500 mb-1 ml-3">
                  <span>数据库</span>
                  <span>{{ formatSize(storageInfo.breakdown?.database || 0) }}</span>
                </div>
                <div class="flex justify-between text-xs text-gray-500 mb-1 ml-3">
                  <span>录制视频</span>
                  <span>{{ formatSize(storageInfo.breakdown?.recordings || 0) }}</span>
                </div>
                <div class="flex justify-between text-xs text-gray-500 mb-1 ml-3">
                  <span>上传视频</span>
                  <span>{{ formatSize(storageInfo.breakdown?.upload_videos || 0) }}</span>
                </div>
                <div class="flex justify-between text-xs text-gray-500 mb-2 ml-3">
                  <span>模型文件</span>
                  <span>{{ formatSize(storageInfo.breakdown?.upload_models || 0) }}</span>
                </div>
                <el-divider class="my-2" />
                <div class="flex justify-between text-sm mb-1">
                  <span class="text-gray-300">磁盘使用</span>
                  <span 
                    class="font-mono"
                    :class="storageInfo.disk_usage_percent >= 90 ? 'text-red-400' : storageInfo.disk_usage_percent >= 80 ? 'text-yellow-400' : 'text-green-400'"
                  >{{ storageInfo.disk_usage_percent }}%</span>
                </div>
                <el-progress 
                  :percentage="storageInfo.disk_usage_percent" 
                  :stroke-width="6"
                  :color="storageInfo.disk_usage_percent >= 90 ? '#f87171' : storageInfo.disk_usage_percent >= 80 ? '#facc15' : '#4ade80'"
                  :show-text="false"
                />
                <div class="flex justify-between text-xs text-gray-500 mt-1">
                  <span>已用 {{ storageInfo.disk_used_gb }} GB</span>
                  <span>剩余 {{ storageInfo.disk_free_gb }} GB</span>
                </div>
              </div>

              <!-- 自动清理设置 -->
              <div class="bg-slate-900 rounded-lg p-3 border border-slate-700">
                <div class="flex justify-between items-center mb-3">
                  <span class="text-sm text-gray-300">自动清理</span>
                  <el-switch v-model="cleanupSettings.auto_cleanup" @change="saveCleanupSettings" size="small" />
                </div>
                <div class="flex items-center gap-2 mb-3">
                  <span class="text-sm text-gray-300 whitespace-nowrap">保留天数</span>
                  <el-input-number 
                    v-model="cleanupSettings.retention_days" 
                    :min="1" 
                    :max="365" 
                    size="small"
                    controls-position="right"
                    class="flex-1"
                    @change="saveCleanupSettings"
                  />
                </div>
                <div class="text-xs text-gray-500 mb-3">
                  超过 {{ cleanupSettings.retention_days }} 天的数据将在每天自动清理
                </div>
                <el-button 
                  type="warning" 
                  plain 
                  size="small"
                  class="w-full" 
                  @click="handleRunCleanup" 
                  :loading="runningCleanup"
                >
                  立即执行清理
                </el-button>
              </div>

              <!-- 按日期范围删除 -->
              <div class="bg-slate-900 rounded-lg p-3 border border-slate-700">
                <div class="text-sm text-gray-300 mb-2">按日期范围删除</div>
                <el-date-picker
                  v-model="clearDateRange"
                  type="daterange"
                  range-separator="至"
                  start-placeholder="开始日期"
                  end-placeholder="结束日期"
                  format="YYYY-MM-DD"
                  value-format="YYYY-MM-DD"
                  class="w-full mb-2"
                  size="small"
                  :disabled-date="disabledDate"
                />
                <el-button 
                  type="danger" 
                  plain 
                  size="small"
                  class="w-full" 
                  @click="handleClearRange" 
                  :loading="clearingRange"
                  :disabled="!clearDateRange || clearDateRange.length !== 2"
                >
                  删除选定范围数据
                </el-button>
              </div>
            </div>
          </section>
        </div>
      </div>
    </div>

    <!-- 导出选择弹窗 -->
    <el-dialog v-model="exportDialogVisible" :title="exportDialogTitle" width="400px">
      <el-form label-position="top">
        <el-form-item v-if="exportDialogType === 'week'" label="选择周">
          <el-date-picker
            v-model="exportWeekDate"
            type="week"
            format="YYYY 年 第 ww 周"
            placeholder="选择周"
            class="w-full"
            @change="handleWeekChange"
          />
        </el-form-item>
        <el-form-item v-if="exportDialogType === 'month'" label="选择月份">
          <el-date-picker
            v-model="exportMonth"
            type="month"
            format="YYYY年MM月"
            value-format="YYYY-MM"
            placeholder="选择月份"
            class="w-full"
          />
        </el-form-item>
        <el-form-item v-if="exportDialogType === 'range'" label="选择日期范围">
          <el-date-picker
            v-model="exportDateRange"
            type="daterange"
            start-placeholder="开始日期"
            end-placeholder="结束日期"
            value-format="YYYY-MM-DD"
            class="w-full"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="exportDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="handleExport" :loading="exporting">导出</el-button>
      </template>
    </el-dialog>

    <!-- 视频播放弹窗 -->
    <el-dialog 
      v-model="videoDialogVisible" 
      title="视频播放" 
      width="70%" 
      destroy-on-close
      :close-on-click-modal="false"
    >
      <div class="video-container">
        <video 
          v-if="currentVideoUrl" 
          :src="currentVideoUrl" 
          controls 
          autoplay 
          class="w-full"
          style="max-height: 70vh; background: #000;"
          @error="handleVideoError"
        >
          您的浏览器不支持视频播放
        </video>
        <div v-else class="text-center text-gray-500 py-10">
          视频加载中...
        </div>
      </div>
      <template #footer>
        <span class="text-gray-400 text-sm">提示：视频首次加载可能需要几秒钟进行格式转换</span>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted, watch } from 'vue';
import { useRouter } from 'vue-router';
import { useSystemStore } from '@/store/useSystemStore';
import { useProjectStore } from '@/store/useProjectStore';
import { Calendar, Clock, DataLine, Setting, Download, Folder, TrendCharts, VideoPlay, Delete, Warning } from '@element-plus/icons-vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { getDetectionResults } from '@/api/detection';
import { 
  getSessionsByDate, 
  getSessionDates,
  getSessionCycles,
  getCycleSteps,
  getStepAverages,
  getExportSettings,
  updateExportSettings,
  exportSessionCsv,
  exportWeekCsv,
  exportMonthCsv,
  exportDateRangeCsv,
  downloadBlob,
  getVideoUrl,
  backupDatabase,
  clearAllData,
  clearDataByRange,
  getCleanupSettings,
  updateCleanupSettings,
  runCleanupNow,
  getStorageInfo
} from '@/api/data';
import { getProjectDetail, updateProject } from '@/api/project';

const router = useRouter();
const store = useSystemStore();
const projectStore = useProjectStore();

// 当前项目名称（从导航栏同步）
const currentProjectName = computed(() => {
  return projectStore.currentProject?.name || '';
});

// 实时计数器（与Monitor同步）
const currentCounters = ref([]);

// 日期和会话
const selectedDate = ref('');
const availableDates = ref([]);
const sessions = ref([]);
const selectedSession = ref(null);
const cycles = ref([]);
const stepStats = ref([]);
const loadingSessions = ref(false);
const loadingOverview = ref(false);
const exporting = ref(false);
const clearing = ref(false);

// 历史数据概览
const overviewData = reactive({
  total_cycles: 0,
  good_cycles: 0,
  ng_cycles: 0,
  avg_cycle_time: 0,
  yield_rate: 0
});

// 历史计数器数据
const historyCounters = ref({});

// 周期步骤数据
const expandedRows = ref([]);
const cycleStepsMap = ref({});

// 导出设置
const exportSettings = reactive({
  // 记录设置
  record_step_duration: true,
  record_step_interval: true,
  record_cycle_duration: true,
  record_cycle_interval: true,
  record_counters: true,
  record_step_video: false,
  record_cycle_video: false,
  record_session_video: false,
  video_quality: 'medium',
  video_fps: 30,
  // 导出设置
  export_session_info: true,
  export_counters: true,
  export_cycle_result: true,
  export_cycle_duration: true,
  export_cycle_interval: true,
  export_step_duration: true,
  export_step_interval: true,
  export_step_event: true
});

// 导出弹窗
const exportDialogVisible = ref(false);
const exportDialogType = ref('');
const exportWeek = ref('');
const exportWeekDate = ref(null);  // 周选择器绑定的日期对象
const exportMonth = ref('');
const exportDateRange = ref([]);

// 处理周选择变化 - 计算ISO周
const handleWeekChange = (date) => {
  if (date) {
    const d = new Date(date);
    // 复制日期对象避免修改原始值
    const target = new Date(d.valueOf());
    // ISO周从周一开始，调整到最近的周四（ISO周算法）
    const dayNr = (d.getDay() + 6) % 7;  // 周一=0, 周日=6
    target.setDate(target.getDate() - dayNr + 3);  // 调整到周四
    // 获取该周四所在年份的第一个周四
    const firstThursday = new Date(target.getFullYear(), 0, 4);
    const dayOfWeek = (firstThursday.getDay() + 6) % 7;
    firstThursday.setDate(firstThursday.getDate() - dayOfWeek + 3);
    // 计算周数
    const weekNum = 1 + Math.round((target - firstThursday) / 604800000);
    const year = target.getFullYear();
    exportWeek.value = `${year}-W${weekNum.toString().padStart(2, '0')}`;
    console.log('选择的周:', exportWeek.value, '日期:', d.toISOString().split('T')[0]);
  } else {
    exportWeek.value = '';
  }
};

// 视频播放
const videoDialogVisible = ref(false);
const currentVideoUrl = ref('');

// 清理设置
const cleanupSettings = reactive({
  retention_days: 30,
  auto_cleanup: true
});
const savingCleanupSettings = ref(false);
const runningCleanup = ref(false);
const clearingRange = ref(false);
const clearDateRange = ref([]);

// 存储信息
const storageInfo = reactive({
  data_size_mb: 0,
  breakdown: { database: 0, recordings: 0, uploads: 0 },
  disk_total_gb: 0,
  disk_used_gb: 0,
  disk_free_gb: 0,
  disk_usage_percent: 0
});

// 轮询定时器
let pollingTimer = null;

const exportDialogTitle = computed(() => {
  switch (exportDialogType.value) {
    case 'week': return '导出某周数据';
    case 'month': return '导出某月数据';
    case 'range': return '导出日期范围';
    default: return '导出';
  }
});

// 获取计数器颜色
const getCounterColor = (name) => {
  if (name.includes('合格') || name.includes('良品')) return 'text-green-400';
  if (name.includes('不良') || name.includes('NG')) return 'text-red-400';
  if (name.includes('总')) return 'text-cyan-400';
  return 'text-white';
};

// 获取状态显示类型
const getStatusType = (status) => {
  switch (status) {
    case 'running': return 'warning';
    case 'completed': return 'success';
    case 'interrupted': return 'info';
    default: return 'info';
  }
};

// 获取状态显示文本
const getStatusText = (status) => {
  switch (status) {
    case 'running': return '运行中';
    case 'completed': return '已完成';
    case 'interrupted': return '已中断';
    default: return status || '未知';
  }
};

// 禁用未来日期
const disabledDate = (time) => {
  return time.getTime() > Date.now();
};

// 格式化时间
const formatTime = (timeStr) => {
  if (!timeStr) return '';
  const parts = timeStr.split(' ');
  return parts.length > 1 ? parts[1].substring(0, 8) : timeStr;
};

// 格式化持续时间
const formatDuration = (seconds) => {
  if (!seconds && seconds !== 0) return '-';
  if (seconds < 60) return `${seconds.toFixed(2)}s`;
  const mins = Math.floor(seconds / 60);
  const secs = (seconds % 60).toFixed(1);
  return `${mins}m ${secs}s`;
};

// 格式化存储大小（MB → 自动转换为合适单位）
const formatSize = (mb) => {
  if (!mb && mb !== 0) return '0 MB';
  if (mb >= 1024) return `${(mb / 1024).toFixed(2)} GB`;
  return `${mb.toFixed(2)} MB`;
};

// 跳转到项目选择
const goToProjectSelect = () => {
  router.push('/project');
};

// 从项目配置初始化计数器
const initCountersFromProject = () => {
  const countersConfig = projectStore.currentProject?.counters_config || [];
  currentCounters.value = countersConfig.map(c => ({
    name: c.name,
    value: c.value || 0
  }));
};

// 开始轮询实时数据（与Monitor同步）
const startPolling = () => {
  stopPolling();
  
  if (!projectStore.currentProjectId) return;
  
  pollingTimer = setInterval(async () => {
    try {
      const res = await getDetectionResults();
      if (res.data?.counters) {
        // 更新计数器数据
        currentCounters.value = currentCounters.value.map(counter => ({
          ...counter,
          value: res.data.counters[counter.name] ?? counter.value
        }));
      }
    } catch (e) {
      // 静默失败
    }
  }, 1000);
};

// 停止轮询
const stopPolling = () => {
  if (pollingTimer) {
    clearInterval(pollingTimer);
    pollingTimer = null;
  }
};

// 加载有数据的日期
const loadAvailableDates = async () => {
  if (!projectStore.currentProjectId) return;
  
  try {
    const res = await getSessionDates({ project_id: projectStore.currentProjectId });
    availableDates.value = res.data?.dates || [];
  } catch (e) {
    console.error('加载日期失败:', e);
  }
};

// 日期变化
const handleDateChange = async (date) => {
  if (!date) {
    sessions.value = [];
    selectedSession.value = null;
    resetOverviewData();
    return;
  }
  
  loadingSessions.value = true;
  try {
    const res = await getSessionsByDate(date, projectStore.currentProjectId);
    sessions.value = res.data?.sessions || [];
    
    // 更新历史概览数据
    overviewData.total_cycles = res.data?.total_cycles || 0;
    overviewData.good_cycles = res.data?.total_good || 0;
    overviewData.ng_cycles = res.data?.total_ng || 0;
    overviewData.avg_cycle_time = res.data?.avg_cycle_time || 0;
    overviewData.yield_rate = overviewData.total_cycles > 0 
      ? (overviewData.good_cycles / overviewData.total_cycles * 100) 
      : 0;
    
    historyCounters.value = res.data?.counters_summary || {};
    
    // 加载步骤统计
    await loadStepStats({ date, project_id: projectStore.currentProjectId });
    
    // 清除之前选择的会话
    selectedSession.value = null;
    cycles.value = [];
    expandedRows.value = [];
    cycleStepsMap.value = {};
  } catch (e) {
    console.error('加载会话失败:', e);
  } finally {
    loadingSessions.value = false;
  }
};

// 重置概览数据
const resetOverviewData = () => {
  overviewData.total_cycles = 0;
  overviewData.good_cycles = 0;
  overviewData.ng_cycles = 0;
  overviewData.avg_cycle_time = 0;
  overviewData.yield_rate = 0;
  historyCounters.value = {};
  stepStats.value = [];
};

// 选择会话
const selectSession = async (session) => {
  selectedSession.value = session;
  loadingOverview.value = true;
  expandedRows.value = [];
  cycleStepsMap.value = {};
  
  try {
    // 更新概览为会话数据
    overviewData.total_cycles = session.total_cycles;
    overviewData.good_cycles = session.good_cycles;
    overviewData.ng_cycles = session.ng_cycles;
    overviewData.avg_cycle_time = session.avg_cycle_time;
    overviewData.yield_rate = session.total_cycles > 0 
      ? (session.good_cycles / session.total_cycles * 100) 
      : 0;
    
    historyCounters.value = session.counters_snapshot || {};
    
    // 加载周期列表
    const res = await getSessionCycles(session.id);
    cycles.value = res.data || [];
    
    // 加载步骤统计
    await loadStepStats({ session_id: session.id });
  } catch (e) {
    console.error('加载会话详情失败:', e);
  } finally {
    loadingOverview.value = false;
  }
};

// 加载步骤统计
const loadStepStats = async (params) => {
  try {
    const res = await getStepAverages(params);
    stepStats.value = res.data?.steps || [];
  } catch (e) {
    console.error('加载步骤统计失败:', e);
  }
};

// 处理周期行展开
const handleExpandChange = async (row, expandedRowsList) => {
  expandedRows.value = expandedRowsList.map(r => r.id);
  
  // 如果展开且没有加载过步骤数据
  if (expandedRowsList.includes(row) && !cycleStepsMap.value[row.id]) {
    try {
      const res = await getCycleSteps(row.id);
      cycleStepsMap.value[row.id] = res.data || [];
    } catch (e) {
      console.error('加载周期步骤失败:', e);
    }
  }
};

// 播放步骤视频
const playStepVideo = (step) => {
  if (step.video_id) {
    currentVideoUrl.value = getVideoUrl(step.video_id);
    videoDialogVisible.value = true;
  } else {
    ElMessage.info('该步骤无录制视频（请在记录设置中开启"录制步骤视频"后重新检测）');
  }
};

// 播放周期视频
const playCycleVideo = (cycle) => {
  if (cycle.video_id) {
    currentVideoUrl.value = getVideoUrl(cycle.video_id);
    videoDialogVisible.value = true;
  } else {
    ElMessage.info('该周期无录制视频（请在记录设置中开启"录制周期视频"后重新检测）');
  }
};

// 播放会话视频
const playSessionVideo = (session) => {
  if (session.video_id) {
    currentVideoUrl.value = getVideoUrl(session.video_id);
    videoDialogVisible.value = true;
  } else {
    ElMessage.info('该会话无录制视频（请在记录设置中开启"录制会话视频"后重新检测）');
  }
};

// 视频播放错误处理
const handleVideoError = (e) => {
  console.error('视频播放错误:', e);
  ElMessage.error('视频加载失败，请稍后重试');
};

// 导出当前会话
const exportSessionData = async () => {
  if (!selectedSession.value) return;
  
  exporting.value = true;
  try {
    const res = await exportSessionCsv(selectedSession.value.id);
    const filename = `session_${selectedSession.value.session_uuid}_${selectedDate.value}.csv`;
    downloadBlob(res.data, filename);
    ElMessage.success(`已导出到下载文件夹: ${filename}`);
  } catch (e) {
    console.error('导出失败:', e);
    ElMessage.error('导出失败: ' + (e.message || '未知错误'));
  } finally {
    exporting.value = false;
  }
};

// 导出当日数据
const exportByDate = async () => {
  if (!selectedDate.value) {
    ElMessage.warning('请先选择日期');
    return;
  }
  
  exporting.value = true;
  try {
    const res = await exportDateRangeCsv(selectedDate.value, selectedDate.value);
    const filename = `data_${selectedDate.value}.csv`;
    downloadBlob(res.data, filename);
    ElMessage.success(`已导出到下载文件夹: ${filename}`);
  } catch (e) {
    console.error('导出失败:', e);
    ElMessage.error('导出失败: ' + (e.message || '未知错误'));
  } finally {
    exporting.value = false;
  }
};

// 显示导出弹窗
const showExportDialog = (type) => {
  exportDialogType.value = type;
  // 清空之前的选择
  exportWeek.value = '';
  exportWeekDate.value = null;
  exportMonth.value = '';
  exportDateRange.value = [];
  exportDialogVisible.value = true;
};

// 执行导出
const handleExport = async () => {
  exporting.value = true;
  try {
    let res, filename;
    
    if (exportDialogType.value === 'week' && exportWeek.value) {
      res = await exportWeekCsv(exportWeek.value);
      filename = `data_${exportWeek.value}.csv`;
    } else if (exportDialogType.value === 'month' && exportMonth.value) {
      res = await exportMonthCsv(exportMonth.value);
      filename = `data_${exportMonth.value}.csv`;
    } else if (exportDialogType.value === 'range' && exportDateRange.value?.length === 2) {
      res = await exportDateRangeCsv(exportDateRange.value[0], exportDateRange.value[1]);
      filename = `data_${exportDateRange.value[0]}_to_${exportDateRange.value[1]}.csv`;
    } else {
      ElMessage.warning('请选择导出范围');
      exporting.value = false;
      return;
    }
    
    downloadBlob(res.data, filename);
    ElMessage.success(`已导出到下载文件夹: ${filename}`);
    exportDialogVisible.value = false;
  } catch (e) {
    console.error('导出失败:', e);
    ElMessage.error('导出失败: ' + (e.message || '未知错误'));
  } finally {
    exporting.value = false;
  }
};

// 默认导出设置
const defaultExportSettings = {
  record_step_duration: true,
  record_step_interval: true,
  record_cycle_duration: true,
  record_cycle_interval: true,
  record_counters: true,
  record_step_video: false,
  record_cycle_video: false,
  record_session_video: false,
  video_quality: 'medium',
  video_fps: 30,
  export_session_info: true,
  export_counters: true,
  export_cycle_result: true,
  export_cycle_duration: true,
  export_cycle_interval: true,
  export_step_duration: true,
  export_step_interval: true,
  export_step_event: true
};

// 加载导出设置（从项目）
const loadExportSettings = async () => {
  if (!projectStore.currentProjectId) {
    // 没有项目时使用默认设置
    Object.assign(exportSettings, defaultExportSettings);
    return;
  }
  try {
    const res = await getProjectDetail(projectStore.currentProjectId);
    const dataConfig = res.data.data_config;
    if (dataConfig) {
      Object.assign(exportSettings, { ...defaultExportSettings, ...dataConfig });
    } else {
      Object.assign(exportSettings, defaultExportSettings);
    }
    // 同步到后端全局设置（用于检测时的录制）
    await updateExportSettings(exportSettings);
  } catch (e) {
    console.error('加载导出设置失败:', e);
    Object.assign(exportSettings, defaultExportSettings);
  }
};

// 保存导出设置（到项目）
const saveExportSettings = async () => {
  try {
    // 保存到后端全局设置（用于检测时的录制）
    await updateExportSettings(exportSettings);
    // 保存到项目数据库
    if (projectStore.currentProjectId) {
      await updateProject(projectStore.currentProjectId, {
        data_config: { ...exportSettings }
      });
    }
    ElMessage.success('设置已保存');
  } catch (e) {
    ElMessage.error('保存失败');
  }
};

// 备份数据库
const handleBackup = () => {
  try {
    backupDatabase();
    ElMessage.success('数据库备份文件已开始下载');
  } catch (e) {
    console.error('备份失败:', e);
    ElMessage.error('备份失败: ' + (e.message || '未知错误'));
  }
};

// 清空数据
const handleClearData = async () => {
  try {
    await ElMessageBox.confirm(
      '此操作将永久删除所有历史检测数据（包括会话、周期、步骤记录和视频文件），是否继续?',
      '警告',
      {
        confirmButtonText: '确定删除',
        cancelButtonText: '取消',
        type: 'warning',
      }
    );
    
    clearing.value = true;
    const res = await clearAllData();
    const deleted = res.data.deleted;
    ElMessage.success(`清理完成：${deleted.sessions}个会话, ${deleted.cycles}个周期, ${deleted.steps}条步骤, ${deleted.files}个视频文件`);
    loadAvailableDates();
    loadStorageInfo();
    resetOverviewData();
    sessions.value = [];
    cycles.value = [];
  } catch (err) {
    if (err !== 'cancel') {
      ElMessage.error('清理失败: ' + (err.response?.data?.detail || err.message));
    }
  } finally {
    clearing.value = false;
  }
};

// ========== 清理设置相关 ==========

const loadCleanupSettings = async () => {
  try {
    const res = await getCleanupSettings();
    cleanupSettings.retention_days = res.data.retention_days;
    cleanupSettings.auto_cleanup = res.data.auto_cleanup;
  } catch (e) {
    console.error('加载清理设置失败:', e);
  }
};

const saveCleanupSettings = async () => {
  savingCleanupSettings.value = true;
  try {
    await updateCleanupSettings({
      retention_days: cleanupSettings.retention_days,
      auto_cleanup: cleanupSettings.auto_cleanup
    });
    ElMessage.success('清理设置已保存');
  } catch (e) {
    ElMessage.error('保存清理设置失败');
  } finally {
    savingCleanupSettings.value = false;
  }
};

const handleRunCleanup = async () => {
  try {
    await ElMessageBox.confirm(
      `将删除 ${cleanupSettings.retention_days} 天前的所有历史数据（会话、周期、步骤、视频文件），是否继续？`,
      '手动触发清理',
      { confirmButtonText: '确定清理', cancelButtonText: '取消', type: 'warning' }
    );
    runningCleanup.value = true;
    await runCleanupNow();
    ElMessage.success('清理已完成');
    loadAvailableDates();
    loadStorageInfo();
  } catch (err) {
    if (err !== 'cancel') ElMessage.error('清理失败: ' + (err.response?.data?.detail || err.message));
  } finally {
    runningCleanup.value = false;
  }
};

const handleClearRange = async () => {
  if (!clearDateRange.value || clearDateRange.value.length !== 2) {
    ElMessage.warning('请先选择日期范围');
    return;
  }
  const [start, end] = clearDateRange.value;
  try {
    await ElMessageBox.confirm(
      `将删除 ${start} 至 ${end} 之间的所有历史数据，是否继续？`,
      '按范围删除',
      { confirmButtonText: '确定删除', cancelButtonText: '取消', type: 'warning' }
    );
    clearingRange.value = true;
    const res = await clearDataByRange(start, end);
    const d = res.data.deleted;
    ElMessage.success(`清理完成：${d.sessions}个会话, ${d.cycles}个周期, ${d.steps}条步骤, ${d.files}个文件`);
    clearDateRange.value = [];
    loadAvailableDates();
    loadStorageInfo();
    resetOverviewData();
    sessions.value = [];
    cycles.value = [];
  } catch (err) {
    if (err !== 'cancel') ElMessage.error('清理失败: ' + (err.response?.data?.detail || err.message));
  } finally {
    clearingRange.value = false;
  }
};

const loadStorageInfo = async () => {
  try {
    const res = await getStorageInfo();
    Object.assign(storageInfo, res.data);
    if (storageInfo.disk_usage_percent >= 90) {
      ElMessage.warning({
        message: `磁盘空间不足！已使用 ${storageInfo.disk_usage_percent}%，剩余 ${storageInfo.disk_free_gb} GB，请及时清理数据。`,
        duration: 8000,
        showClose: true
      });
    } else if (storageInfo.disk_usage_percent >= 80) {
      ElMessage.warning({
        message: `磁盘空间较紧张，已使用 ${storageInfo.disk_usage_percent}%，剩余 ${storageInfo.disk_free_gb} GB。`,
        duration: 5000,
        showClose: true
      });
    }
  } catch (e) {
    console.error('获取存储信息失败:', e);
  }
};

// 监听项目变化
watch(() => projectStore.currentProjectId, (newId) => {
  if (newId) {
    initCountersFromProject();
    loadAvailableDates();
    startPolling();
    // 重置选择状态
    selectedDate.value = '';
    selectedSession.value = null;
    sessions.value = [];
    cycles.value = [];
    resetOverviewData();
  } else {
    stopPolling();
  }
}, { immediate: true });

// 监听项目计数器配置变化
watch(() => projectStore.currentProject?.counters_config, (newCounters) => {
  if (newCounters) {
    currentCounters.value = newCounters.map(c => ({
      name: c.name,
      value: c.value || 0
    }));
  }
}, { deep: true });

// 监听项目变化，重新加载设置
watch(() => projectStore.currentProjectId, () => {
  loadExportSettings();
});

onMounted(() => {
  loadExportSettings();
  loadCleanupSettings();
  loadStorageInfo();
});

onUnmounted(() => {
  stopPolling();
});
</script>

<style scoped>
/* 表格深色主题 */
.dark-table :deep(.el-table) {
  --el-table-bg-color: transparent;
  --el-table-tr-bg-color: transparent;
  --el-table-header-bg-color: #1e293b;
  --el-table-row-hover-bg-color: #334155;
  --el-table-border-color: #475569;
  --el-table-text-color: #e2e8f0;
  --el-table-header-text-color: #94a3b8;
}

.dark-table :deep(.el-table__expand-icon) {
  color: #94a3b8;
}

.dark-table :deep(.el-table__expand-icon--expanded) {
  color: #22d3ee;
}

/* 所有表格深色主题 */
:deep(.el-table) {
  --el-table-bg-color: #0f172a;
  --el-table-tr-bg-color: #0f172a;
  --el-table-header-bg-color: #1e293b;
  --el-table-row-hover-bg-color: #334155;
  --el-table-border-color: #334155;
  --el-table-text-color: #e2e8f0;
  --el-table-header-text-color: #94a3b8;
  background-color: #0f172a;
}

:deep(.el-table th.el-table__cell) {
  background-color: #1e293b;
}

:deep(.el-table td.el-table__cell) {
  border-bottom: 1px solid #334155;
}

:deep(.el-table--border .el-table__cell) {
  border-right: 1px solid #334155;
}

:deep(.el-table__body tr:hover > td.el-table__cell) {
  background-color: #334155 !important;
}

/* 日期选择器深色主题 */
:deep(.el-date-editor) {
  --el-input-bg-color: #1e293b;
  --el-input-border-color: #334155;
  --el-input-text-color: #e2e8f0;
  --el-input-placeholder-color: #64748b;
  --el-input-hover-border-color: #0ea5e9;
  --el-input-focus-border-color: #0ea5e9;
}

:deep(.el-date-editor .el-input__wrapper) {
  background-color: #1e293b;
  box-shadow: 0 0 0 1px #334155 inset;
}

:deep(.el-date-editor .el-input__wrapper:hover) {
  box-shadow: 0 0 0 1px #475569 inset;
}

:deep(.el-date-editor .el-input__wrapper.is-focus) {
  box-shadow: 0 0 0 1px #0ea5e9 inset;
}

:deep(.el-date-editor .el-input__inner) {
  color: #e2e8f0;
}

:deep(.el-date-editor .el-input__prefix-inner .el-icon) {
  color: #64748b;
}

/* 折叠面板深色主题 */
:deep(.el-collapse) {
  --el-collapse-border-color: #334155;
  --el-collapse-header-bg-color: #1e293b;
  --el-collapse-header-text-color: #e2e8f0;
  --el-collapse-header-font-size: 14px;
  --el-collapse-content-bg-color: #0f172a;
  --el-collapse-content-text-color: #cbd5e1;
  border: none;
}

:deep(.el-collapse-item__header) {
  background-color: #1e293b;
  color: #e2e8f0;
  border-bottom: 1px solid #334155;
  padding: 12px 16px;
  font-size: 14px;
}

:deep(.el-collapse-item__header:hover) {
  background-color: #334155;
}

:deep(.el-collapse-item__header.is-active) {
  border-bottom-color: #0ea5e9;
  color: #22d3ee;
}

:deep(.el-collapse-item__wrap) {
  background-color: #0f172a;
  border-bottom: 1px solid #334155;
}

:deep(.el-collapse-item__content) {
  background-color: #0f172a;
  color: #cbd5e1;
  padding: 12px 16px;
}

:deep(.el-collapse-item__arrow) {
  color: #94a3b8;
}

:deep(.el-collapse-item__header.is-active .el-collapse-item__arrow) {
  color: #22d3ee;
}

/* 分割线深色主题 */
:deep(.el-divider) {
  border-color: #334155;
}

:deep(.el-divider__text) {
  background-color: #0f172a;
  color: #94a3b8;
}

/* 按钮样式调整 */
:deep(.el-button--default) {
  --el-button-bg-color: #1e293b;
  --el-button-border-color: #334155;
  --el-button-text-color: #e2e8f0;
  --el-button-hover-bg-color: #334155;
  --el-button-hover-border-color: #475569;
  --el-button-hover-text-color: #f1f5f9;
}

/* 空状态文字颜色 */
:deep(.el-table__empty-text) {
  color: #64748b;
}
</style>
