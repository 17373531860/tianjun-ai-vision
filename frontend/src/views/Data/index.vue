<template>
  <div class="data-page p-5 h-full overflow-y-auto">
    <!-- 页面头部 -->
    <div class="flex justify-between items-center mb-5">
      <div class="flex items-center gap-3">
        <div class="w-1 h-7 rounded-full bg-gradient-to-b from-cyan-400 to-blue-500"></div>
        <h2 class="text-xl font-bold text-white tracking-wide">数据中心</h2>
      </div>
      <div class="flex items-center gap-3 bg-slate-800/60 px-4 py-2 rounded-lg border border-slate-700/50">
        <span class="text-gray-500 text-xs">当前项目</span>
        <span class="text-cyan-400 font-semibold text-sm">{{ currentProjectName || '未选择' }}</span>
        <el-button v-if="!projectStore.currentProjectId" type="primary" size="small" round @click="goToProjectSelect">
          选择项目
        </el-button>
      </div>
    </div>

    <!-- 未选择项目提示 -->
    <div v-if="!projectStore.currentProjectId" class="flex flex-col items-center justify-center py-24">
      <div class="w-20 h-20 rounded-2xl bg-slate-800 flex items-center justify-center mb-5 border border-slate-700">
        <el-icon :size="36" class="text-gray-600"><Folder /></el-icon>
      </div>
      <p class="text-gray-400 text-base mb-5">请先选择一个项目以查看数据</p>
      <el-button type="primary" round @click="goToProjectSelect">前往项目管理</el-button>
    </div>

    <div v-else class="grid grid-cols-1 lg:grid-cols-12 gap-5">
      <!-- ========== 左侧边栏：查询 + 会话列表 ========== -->
      <div class="lg:col-span-3 space-y-4">
        <!-- 日期与时间段查询 -->
        <div class="panel-card">
          <div class="panel-header">
            <el-icon class="text-cyan-400"><Calendar /></el-icon>
            <span>数据查询</span>
          </div>
          <div class="space-y-3">
            <el-date-picker
              v-model="selectedDate"
              type="date"
              placeholder="选择日期"
              format="YYYY-MM-DD"
              value-format="YYYY-MM-DD"
              class="w-full"
              :disabled-date="disabledDate"
              @change="handleDateChange"
            />
            <div>
              <div class="text-xs text-gray-500 mb-1.5">时间段</div>
              <el-select v-model="shiftType" size="small" class="w-full" @change="handleShiftChange">
                <el-option label="全天" value="all" />
                <el-option label="白班 (08:00-20:00)" value="day" />
                <el-option label="晚班 (20:00-08:00)" value="night" />
                <el-option label="自定义" value="custom" />
              </el-select>
              <div v-if="shiftType === 'custom'" class="flex items-center gap-2 mt-2">
                <el-time-picker v-model="customStartHour" size="small" placeholder="开始" format="HH:mm" value-format="HH:mm" class="flex-1" @change="handleShiftChange" />
                <span class="text-gray-600 text-xs">至</span>
                <el-time-picker v-model="customEndHour" size="small" placeholder="结束" format="HH:mm" value-format="HH:mm" class="flex-1" @change="handleShiftChange" />
              </div>
            </div>
            <!-- Workstation / Channel filter -->
            <div v-if="totalChannelCount > 1">
              <div class="text-xs text-gray-500 mb-1.5">工位筛选</div>
              <el-select v-model="channelFilter" size="small" class="w-full" clearable placeholder="全部工位" @change="handleChannelFilterChange">
                <el-option label="全部工位" :value="null" />
                <el-option v-for="ch in totalChannelCount" :key="ch - 1" :label="`工位 ${ch}`" :value="ch - 1" />
              </el-select>
            </div>
            <!-- MES: 工单筛选 -->
            <div>
              <div class="text-xs text-gray-500 mb-1.5">MES 工单</div>
              <el-select v-model="mesOrderFilter" size="small" class="w-full" clearable placeholder="全部工单" @change="loadSessions">
                <el-option v-for="o in mesOrderOptions" :key="o.id" :label="`${o.order_no} - ${o.product_name}`" :value="o.id" />
              </el-select>
            </div>
            <div v-if="availableDates.length > 0" class="flex items-center justify-between text-xs pt-1 border-t border-slate-800">
              <span class="text-gray-500">有数据的日期</span>
              <span class="text-cyan-400 font-mono">{{ availableDates.length }} 天</span>
            </div>
          </div>
        </div>

        <!-- 会话列表 -->
        <div class="panel-card">
          <div class="panel-header">
            <el-icon class="text-emerald-400"><Clock /></el-icon>
            <span>启动记录</span>
          </div>
          <div v-loading="loadingSessions" class="space-y-2 max-h-[340px] overflow-y-auto pr-1 custom-scrollbar">
            <div v-if="sessions.length === 0" class="text-center text-gray-600 py-8 text-sm">
              {{ selectedDate ? '当日无检测记录' : '请选择日期' }}
            </div>
            <div
              v-for="session in sessions"
              :key="session.id"
              class="session-card"
              :class="{ 'session-card--active': selectedSession?.id === session.id }"
              @click="selectSession(session)"
            >
              <div class="flex items-center justify-between">
                <div class="flex-1 min-w-0">
                  <div class="text-white font-mono text-xs leading-5">
                    {{ formatTime(session.start_time) }}
                    <span v-if="session.end_time" class="text-gray-500"> → {{ formatTime(session.end_time) }}</span>
                  </div>
                  <div class="flex items-center gap-2 mt-1">
                    <el-tag v-if="totalChannelCount > 1" type="" size="small" effect="plain" round class="!text-cyan-400 !border-cyan-800">
                      工位{{ (session.channel_id || 0) + 1 }}
                    </el-tag>
                    <el-tag :type="getStatusType(session.status)" size="small" effect="dark" round>
                      {{ getStatusText(session.status) }}
                    </el-tag>
                    <span class="text-cyan-400 font-mono text-xs">{{ session.total_cycles || 0 }}轮</span>
                    <span class="text-xs">
                      <span class="text-green-400">{{ session.good_cycles || 0 }}</span>
                      <span class="text-gray-600">/</span>
                      <span class="text-red-400">{{ session.ng_cycles || 0 }}</span>
                    </span>
                  </div>
                </div>
                <el-button
                  v-if="session.video_id"
                  type="primary"
                  size="small"
                  circle
                  class="ml-2 flex-shrink-0"
                  @click.stop="playSessionVideo(session)"
                >
                  <el-icon :size="12"><VideoPlay /></el-icon>
                </el-button>
                <el-tooltip v-else content="无会话视频" placement="top">
                  <el-button type="info" size="small" circle disabled class="ml-2 flex-shrink-0">
                    <el-icon :size="12"><VideoPlay /></el-icon>
                  </el-button>
                </el-tooltip>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- ========== 右侧主区域 ========== -->
      <div class="lg:col-span-9 space-y-5">
        <!-- 统计概览卡片 -->
        <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
          <div class="stat-card stat-card--cyan">
            <div class="stat-card__label">总周期数</div>
            <div class="stat-card__value text-cyan-400">{{ overviewData.total_cycles }}</div>
          </div>
          <div class="stat-card stat-card--blue">
            <div class="stat-card__label">平均周期时间</div>
            <div class="stat-card__value text-blue-400">{{ formatDuration(overviewData.avg_cycle_time) }}</div>
          </div>
          <div class="stat-card" :class="overviewData.yield_rate >= 95 ? 'stat-card--green' : 'stat-card--yellow'">
            <div class="stat-card__label">良率</div>
            <div class="stat-card__value" :class="overviewData.yield_rate >= 95 ? 'text-emerald-400' : 'text-amber-400'">
              {{ overviewData.yield_rate.toFixed(1) }}%
            </div>
          </div>
          <div class="stat-card stat-card--mixed">
            <div class="stat-card__label">合格 / 不良</div>
            <div class="stat-card__value">
              <span class="text-emerald-400">{{ overviewData.good_cycles }}</span>
              <span class="text-gray-600 mx-1">/</span>
              <span class="text-red-400">{{ overviewData.ng_cycles }}</span>
            </div>
          </div>
        </div>

        <!-- 计数器快照 -->
        <div v-if="Object.keys(historyCounters).length > 0" class="panel-card">
          <div class="panel-header">
            <el-icon class="text-violet-400"><DataLine /></el-icon>
            <span>计数器快照</span>
          </div>
          <div class="grid grid-cols-2 md:grid-cols-4 gap-2">
            <div 
              v-for="(value, name) in historyCounters" 
              :key="name"
              class="bg-slate-800/60 px-3 py-2 rounded-lg text-center"
            >
              <div class="text-gray-500 text-xs truncate" :title="name">{{ name }}</div>
              <div class="text-lg font-mono text-white mt-0.5">{{ value.toLocaleString() }}</div>
            </div>
          </div>
        </div>

        <!-- 步骤耗时统计 + 周期详情 -->
        <div class="panel-card" v-loading="loadingOverview">
          <div class="flex justify-between items-center mb-3">
            <div class="panel-header mb-0">
              <el-icon class="text-violet-400"><TrendCharts /></el-icon>
              <span>{{ selectedSession ? '会话详情' : (selectedDate ? '当日统计' : '数据详情') }}</span>
            </div>
            <div class="flex items-center gap-3">
              <span v-if="selectedSession" class="text-xs text-gray-500 font-mono">{{ selectedSession.session_uuid }}</span>
              <el-button v-if="selectedSession && cycles.length > 0" size="small" type="primary" plain round @click="exportSessionData">
                <el-icon class="mr-1" :size="12"><Download /></el-icon>导出会话
              </el-button>
            </div>
          </div>

          <!-- 步骤耗时统计 -->
          <div v-if="stepStats.length > 0" class="mb-4">
            <div class="text-xs text-gray-500 mb-2 font-medium">步骤耗时统计（仅正常轮次）</div>
            <el-table :data="stepStats" size="small" class="dark-table">
              <el-table-column prop="name" label="步骤名称" />
              <el-table-column prop="count" label="次数" width="70" align="center" />
              <el-table-column label="耗时 (秒)" width="150" align="center">
                <template #default="{ row }">
                  <span class="text-cyan-400 font-mono">{{ row.avg_duration.toFixed(2) }}</span>
                  <span class="text-gray-600 text-xs ml-1">({{ row.min_duration.toFixed(1) }}~{{ row.max_duration.toFixed(1) }})</span>
                </template>
              </el-table-column>
              <el-table-column label="到下步间隔 (秒)" width="160" align="center">
                <template #default="{ row, $index }">
                  <template v-if="$index < stepStats.length - 1">
                    <span class="text-blue-400 font-mono">{{ row.avg_interval.toFixed(2) }}</span>
                    <span class="text-gray-600 text-xs ml-1">({{ row.min_interval.toFixed(1) }}~{{ row.max_interval.toFixed(1) }})</span>
                  </template>
                  <span v-else class="text-gray-700">-</span>
                </template>
              </el-table-column>
            </el-table>
          </div>

          <!-- 周期详情 -->
          <div v-if="selectedSession && cycles.length > 0">
            <div class="text-xs text-gray-500 mb-2 font-medium">周期详情</div>
            <el-table 
              :data="cycles" 
              size="small" 
              class="dark-table" 
              max-height="360"
              row-key="id"
              :expand-row-keys="expandedRows"
              @expand-change="handleExpandChange"
            >
              <el-table-column type="expand">
                <template #default="{ row }">
                  <div class="px-4 py-3">
                    <div v-if="cycleStepsMap[row.id]" class="space-y-1.5">
                      <div 
                        v-for="step in cycleStepsMap[row.id]" 
                        :key="step.id"
                        class="flex items-center justify-between px-3 py-2 bg-slate-800/80 rounded-lg cursor-pointer hover:bg-slate-700/80 transition-colors"
                        @click="playStepVideo(step)"
                      >
                        <div class="flex items-center gap-3">
                          <span class="text-gray-600 text-xs font-mono w-6">#{{ step.step_order }}</span>
                          <span class="text-gray-200 text-sm">{{ step.step_name || step.step_label }}</span>
                        </div>
                        <div class="flex items-center gap-4 text-xs">
                          <span class="text-gray-500 font-mono">{{ formatTime(step.start_time) }}</span>
                          <span class="text-cyan-400 font-mono">{{ step.duration ? step.duration.toFixed(2) + 's' : '-' }}</span>
                          <span v-if="step.interval_to_next" class="text-blue-400 font-mono">
                            → {{ step.interval_to_next.toFixed(2) }}s
                          </span>
                          <el-icon v-if="step.video_id" class="text-violet-400" :size="14"><VideoPlay /></el-icon>
                        </div>
                      </div>
                    </div>
                    <div v-else class="text-center text-gray-600 py-3 text-sm">加载中...</div>
                  </div>
                </template>
              </el-table-column>
              <el-table-column prop="cycle_number" label="#" width="50" align="center" />
              <el-table-column label="开始时间" width="90">
                <template #default="{ row }">
                  <span class="font-mono">{{ formatTime(row.start_time) }}</span>
                </template>
              </el-table-column>
              <el-table-column label="耗时" width="80" align="center">
                <template #default="{ row }">
                  <span class="text-cyan-400 font-mono">{{ row.duration ? row.duration.toFixed(2) + 's' : '-' }}</span>
                </template>
              </el-table-column>
              <el-table-column label="结果" width="70" align="center">
                <template #default="{ row }">
                  <el-tag :type="row.is_good ? 'success' : 'danger'" size="small" effect="dark" round>
                    {{ row.is_good ? 'OK' : 'NG' }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="event_name" label="事件" min-width="80" />
              <el-table-column label="操作" width="70" align="center">
                <template #default="{ row }">
                  <el-button v-if="row.video_id" type="primary" link size="small" @click="playCycleVideo(row)">
                    <el-icon><VideoPlay /></el-icon>
                  </el-button>
                </template>
              </el-table-column>
            </el-table>
            <div v-if="cycleTotal > cyclePageSize" class="flex justify-end mt-2">
              <el-pagination
                small
                background
                layout="prev, pager, next"
                :total="cycleTotal"
                :page-size="cyclePageSize"
                :current-page="cyclePage"
                @current-change="handleCyclePageChange"
              />
            </div>
          </div>

          <!-- 无数据 -->
          <div v-if="!selectedDate && !selectedSession && stepStats.length === 0" class="text-center py-12">
            <el-icon :size="40" class="text-gray-700 mb-3"><Calendar /></el-icon>
            <div class="text-gray-600 text-sm">选择左侧日期查看历史数据</div>
          </div>
        </div>

        <!-- ========== 底部功能区：Tabs ========== -->
        <div class="panel-card">
          <el-tabs v-model="activeSettingsTab" class="settings-tabs">
            <!-- Tab 1: 记录设置 -->
            <el-tab-pane label="记录设置" name="record">
              <div class="grid grid-cols-1 md:grid-cols-2 gap-6 pt-2">
                <div>
                  <div class="text-xs text-gray-500 font-medium mb-3 uppercase tracking-wider">数据记录</div>
                  <div class="space-y-2">
                    <div class="setting-row">
                      <span>记录步骤耗时</span>
                      <el-switch v-model="exportSettings.record_step_duration" @change="saveExportSettings" size="small" />
                    </div>
                    <div class="setting-row">
                      <span>记录步骤间隔</span>
                      <el-switch v-model="exportSettings.record_step_interval" @change="saveExportSettings" size="small" />
                    </div>
                    <div class="setting-row">
                      <span>记录周期耗时</span>
                      <el-switch v-model="exportSettings.record_cycle_duration" @change="saveExportSettings" size="small" />
                    </div>
                    <div class="setting-row">
                      <span>记录周期间隔</span>
                      <el-switch v-model="exportSettings.record_cycle_interval" @change="saveExportSettings" size="small" />
                    </div>
                    <div class="setting-row">
                      <span>记录计数器</span>
                      <el-switch v-model="exportSettings.record_counters" @change="saveExportSettings" size="small" />
                    </div>
                  </div>
                </div>
                <div>
                  <div class="text-xs text-gray-500 font-medium mb-3 uppercase tracking-wider">视频录制</div>
                  <div class="space-y-2">
                    <div class="setting-row">
                      <span>录制步骤视频</span>
                      <el-switch v-model="exportSettings.record_step_video" @change="saveExportSettings" size="small" />
                    </div>
                    <div class="setting-row">
                      <span>录制周期视频</span>
                      <el-switch v-model="exportSettings.record_cycle_video" @change="saveExportSettings" size="small" />
                    </div>
                    <div class="setting-row">
                      <span>录制会话视频</span>
                      <el-switch v-model="exportSettings.record_session_video" @change="saveExportSettings" size="small" />
                    </div>
                    <div v-if="exportSettings.record_step_video || exportSettings.record_cycle_video || exportSettings.record_session_video" class="mt-3 space-y-2">
                      <div class="setting-row">
                        <span>视频质量</span>
                        <el-select v-model="exportSettings.video_quality" size="small" style="width: 90px" @change="saveExportSettings">
                          <el-option label="低" value="low" />
                          <el-option label="中" value="medium" />
                          <el-option label="高" value="high" />
                        </el-select>
                      </div>
                      <div class="setting-row">
                        <span>帧率</span>
                        <el-input-number v-model="exportSettings.video_fps" :min="10" :max="60" size="small" style="width: 90px" @change="saveExportSettings" />
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </el-tab-pane>

            <!-- Tab 2: 数据导出 -->
            <el-tab-pane label="数据导出" name="export">
              <div class="grid grid-cols-1 md:grid-cols-2 gap-6 pt-2">
                <!-- 左列：导出选项 + 时间段 -->
                <div class="space-y-4">
                  <div>
                    <div class="text-xs text-gray-500 font-medium mb-3 uppercase tracking-wider">导出字段</div>
                    <div class="space-y-2">
                      <div class="setting-row">
                        <span>会话信息</span>
                        <el-switch v-model="exportSettings.export_session_info" @change="saveExportSettings" size="small" />
                      </div>
                      <div class="setting-row">
                        <span>计数器</span>
                        <el-switch v-model="exportSettings.export_counters" @change="saveExportSettings" size="small" />
                      </div>
                      <div class="setting-row">
                        <span>周期结果</span>
                        <el-switch v-model="exportSettings.export_cycle_result" @change="saveExportSettings" size="small" />
                      </div>
                      <div class="setting-row">
                        <span>周期耗时</span>
                        <el-switch v-model="exportSettings.export_cycle_duration" @change="saveExportSettings" size="small" />
                      </div>
                      <div class="setting-row">
                        <span>周期间隔</span>
                        <el-switch v-model="exportSettings.export_cycle_interval" @change="saveExportSettings" size="small" />
                      </div>
                      <div class="setting-row">
                        <span>步骤耗时</span>
                        <el-switch v-model="exportSettings.export_step_duration" @change="saveExportSettings" size="small" />
                      </div>
                      <div class="setting-row">
                        <span>步骤间隔</span>
                        <el-switch v-model="exportSettings.export_step_interval" @change="saveExportSettings" size="small" />
                      </div>
                      <div class="setting-row">
                        <span>步骤事件</span>
                        <el-switch v-model="exportSettings.export_step_event" @change="saveExportSettings" size="small" />
                      </div>
                    </div>
                  </div>
                  <div>
                    <div class="text-xs text-gray-500 font-medium mb-2 uppercase tracking-wider">导出时间段</div>
                    <el-select v-model="exportShiftType" size="small" class="w-full">
                      <el-option label="全天" value="all" />
                      <el-option label="白班 (08:00-20:00)" value="day" />
                      <el-option label="夜班 (20:00-08:00)" value="night" />
                      <el-option label="自定义" value="custom" />
                    </el-select>
                    <div v-if="exportShiftType === 'custom'" class="flex items-center gap-2 mt-2">
                      <el-time-picker v-model="exportCustomStart" size="small" placeholder="开始" format="HH:mm" value-format="HH:mm" class="flex-1" @change="() => {}" />
                      <span class="text-gray-600 text-xs">至</span>
                      <el-time-picker v-model="exportCustomEnd" size="small" placeholder="结束" format="HH:mm" value-format="HH:mm" class="flex-1" @change="() => {}" />
                    </div>
                  </div>
                </div>
                <!-- 右列：导出按钮 -->
                <div class="space-y-3">
                  <div class="text-xs text-gray-500 font-medium mb-3 uppercase tracking-wider">导出操作</div>
                  <el-button type="primary" class="w-full" @click="exportByDate" :loading="exporting" :disabled="!selectedDate">
                    <el-icon class="mr-1"><Download /></el-icon> 导出当日数据
                  </el-button>
                  <el-button type="primary" plain class="w-full" @click="showExportDialog('week')">
                    <el-icon class="mr-1"><Download /></el-icon> 导出某周数据
                  </el-button>
                  <el-button type="primary" plain class="w-full" @click="showExportDialog('month')">
                    <el-icon class="mr-1"><Download /></el-icon> 导出某月数据
                  </el-button>
                  <el-button type="primary" plain class="w-full" @click="showExportDialog('range')">
                    <el-icon class="mr-1"><Download /></el-icon> 导出日期范围
                  </el-button>
                  <div class="border-t border-slate-800 pt-3 mt-1 space-y-2">
                    <el-button type="warning" plain class="w-full" @click="handleBackup">
                      备份数据库
                    </el-button>
                    <el-button type="danger" plain class="w-full" @click="handleClearData" :loading="clearing">
                      清空所有历史数据
                    </el-button>
                  </div>
                </div>
              </div>
            </el-tab-pane>

            <!-- Tab 3: 数据清理 -->
            <el-tab-pane label="存储与清理" name="cleanup">
              <div class="grid grid-cols-1 md:grid-cols-2 gap-6 pt-2">
                <!-- 存储信息 -->
                <div>
                  <div class="text-xs text-gray-500 font-medium mb-3 uppercase tracking-wider">存储空间</div>
                  <div class="space-y-2">
                    <div class="setting-row">
                      <span>应用数据</span>
                      <span class="text-cyan-400 font-mono text-xs">{{ formatSize(storageInfo.data_size_mb) }}</span>
                    </div>
                    <div class="setting-row pl-4">
                      <span class="text-gray-600">数据库</span>
                      <span class="text-gray-400 font-mono text-xs">{{ formatSize(storageInfo.breakdown?.database || 0) }}</span>
                    </div>
                    <div class="setting-row pl-4">
                      <span class="text-gray-600">录制视频</span>
                      <span class="text-gray-400 font-mono text-xs">{{ formatSize(storageInfo.breakdown?.recordings || 0) }}</span>
                    </div>
                    <div class="setting-row pl-4">
                      <span class="text-gray-600">上传视频</span>
                      <span class="text-gray-400 font-mono text-xs">{{ formatSize(storageInfo.breakdown?.upload_videos || 0) }}</span>
                    </div>
                    <div class="setting-row pl-4">
                      <span class="text-gray-600">模型文件</span>
                      <span class="text-gray-400 font-mono text-xs">{{ formatSize(storageInfo.breakdown?.upload_models || 0) }}</span>
                    </div>
                    <div class="mt-3 pt-3 border-t border-slate-800">
                      <div class="flex justify-between text-sm mb-1.5">
                        <span class="text-gray-400">磁盘使用</span>
                        <span 
                          class="font-mono text-xs"
                          :class="storageInfo.disk_usage_percent >= 90 ? 'text-red-400' : storageInfo.disk_usage_percent >= 80 ? 'text-yellow-400' : 'text-green-400'"
                        >{{ storageInfo.disk_usage_percent }}%</span>
                      </div>
                      <el-progress 
                        :percentage="storageInfo.disk_usage_percent" 
                        :stroke-width="4"
                        :color="storageInfo.disk_usage_percent >= 90 ? '#f87171' : storageInfo.disk_usage_percent >= 80 ? '#facc15' : '#4ade80'"
                        :show-text="false"
                      />
                      <div class="flex justify-between text-xs text-gray-600 mt-1">
                        <span>已用 {{ storageInfo.disk_used_gb }} GB</span>
                        <span>剩余 {{ storageInfo.disk_free_gb }} GB</span>
                      </div>
                    </div>
                  </div>
                </div>
                <!-- 清理操作 -->
                <div class="space-y-4">
                  <div>
                    <div class="text-xs text-gray-500 font-medium mb-3 uppercase tracking-wider">自动清理</div>
                    <div class="space-y-2">
                      <div class="setting-row">
                        <span>启用自动清理</span>
                        <el-switch v-model="cleanupSettings.auto_cleanup" @change="saveCleanupSettings" size="small" />
                      </div>
                      <div class="setting-row">
                        <span>保留天数</span>
                        <el-input-number v-model="cleanupSettings.retention_days" :min="1" :max="365" size="small" controls-position="right" style="width: 100px" @change="saveCleanupSettings" />
                      </div>
                      <div class="text-xs text-gray-600 pl-1">
                        超过 {{ cleanupSettings.retention_days }} 天的数据将被自动清理
                      </div>
                    </div>
                    <el-button type="warning" plain size="small" class="w-full mt-3" @click="handleRunCleanup" :loading="runningCleanup">
                      立即执行清理
                    </el-button>
                  </div>
                  <div class="border-t border-slate-800 pt-4">
                    <div class="text-xs text-gray-500 font-medium mb-3 uppercase tracking-wider">按日期删除</div>
                    <el-date-picker
                      v-model="clearDateRange"
                      type="daterange"
                      range-separator="至"
                      start-placeholder="开始"
                      end-placeholder="结束"
                      format="YYYY-MM-DD"
                      value-format="YYYY-MM-DD"
                      class="w-full mb-2"
                      size="small"
                      :disabled-date="disabledDate"
                    />
                    <el-button type="danger" plain size="small" class="w-full" @click="handleClearRange" :loading="clearingRange" :disabled="!clearDateRange || clearDateRange.length !== 2">
                      删除选定范围数据
                    </el-button>
                  </div>
                </div>
              </div>
            </el-tab-pane>
          </el-tabs>
        </div>
      </div>
    </div>

    <!-- 导出选择弹窗 -->
    <el-dialog v-model="exportDialogVisible" :title="exportDialogTitle" width="420px" class="dark-dialog">
      <el-form label-position="top">
        <el-form-item v-if="exportDialogType === 'week'" label="选择周">
          <el-date-picker v-model="exportWeekDate" type="week" format="YYYY 年 第 ww 周" placeholder="选择周" class="w-full" @change="handleWeekChange" />
        </el-form-item>
        <el-form-item v-if="exportDialogType === 'month'" label="选择月份">
          <el-date-picker v-model="exportMonth" type="month" format="YYYY年MM月" value-format="YYYY-MM" placeholder="选择月份" class="w-full" />
        </el-form-item>
        <el-form-item v-if="exportDialogType === 'range'" label="选择日期范围">
          <el-date-picker v-model="exportDateRange" type="daterange" start-placeholder="开始日期" end-placeholder="结束日期" value-format="YYYY-MM-DD" class="w-full" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="exportDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="handleExport" :loading="exporting">导出</el-button>
      </template>
    </el-dialog>

    <!-- 视频播放弹窗 -->
    <el-dialog v-model="videoDialogVisible" title="视频播放" width="70%" destroy-on-close :close-on-click-modal="false" class="dark-dialog">
      <div class="video-container">
        <video v-if="currentVideoUrl" :src="currentVideoUrl" controls autoplay class="w-full" style="max-height: 70vh; background: #000; border-radius: 8px;" @error="handleVideoError">
          您的浏览器不支持视频播放
        </video>
        <div v-else class="text-center text-gray-500 py-10">视频加载中...</div>
      </div>
      <template #footer>
        <span class="text-gray-500 text-xs">提示：视频首次加载可能需要几秒钟进行格式转换</span>
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
import { getDetectionResults, getWorkstations } from '@/api/detection';
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

// 时间段筛选
const shiftType = ref('all');
const customStartHour = ref('08:00');
const customEndHour = ref('20:00');

const getShiftHours = () => {
  if (shiftType.value === 'all') return { start: null, end: null };
  const dc = projectStore.currentProject?.data_config || {};
  const dayStart = dc.day_shift_start || '08:00';
  const nightStart = dc.night_shift_start || '20:00';
  if (shiftType.value === 'day') return { start: dayStart, end: nightStart };
  if (shiftType.value === 'night') return { start: nightStart, end: dayStart };
  return { start: customStartHour.value, end: customEndHour.value };
};

// Multi-channel filter
const totalChannelCount = ref(1);
const channelFilter = ref(null); // null = all channels

// MES 工单筛选
const mesOrderFilter = ref(null);
const mesOrderOptions = ref([]);
const loadMesOrders = async () => {
  try {
    const { getOrders } = await import('@/api/mes');
    const res = await getOrders({ status: 'in_progress', limit: 100 });
    const doneRes = await getOrders({ status: 'completed', limit: 50 });
    mesOrderOptions.value = [...(res.data.items || []), ...(doneRes.data.items || [])];
  } catch {}
};

const handleChannelFilterChange = () => {
  if (selectedDate.value) {
    handleDateChange(selectedDate.value);
  }
};

const loadChannelCount = async () => {
  try {
    const res = await getWorkstations();
    totalChannelCount.value = res.data.channel_count || 1;
  } catch (e) { /* keep default */ }
};

const cycles = ref([]);
const cycleTotal = ref(0);
const cyclePage = ref(1);
const cyclePageSize = ref(50);
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

// 底部功能区Tab
const activeSettingsTab = ref('export');

// 导出弹窗
const exportDialogVisible = ref(false);
const exportDialogType = ref('');
const exportWeek = ref('');
const exportWeekDate = ref(null);
const exportMonth = ref('');
const exportDateRange = ref([]);

// 导出专用时间段（独立于左侧查询时间段）
const exportShiftType = ref('all');
const exportCustomStart = ref('08:00');
const exportCustomEnd = ref('20:00');

const getExportShiftHours = () => {
  if (exportShiftType.value === 'all') return { start: null, end: null };
  if (exportShiftType.value === 'day') return { start: '08:00', end: '20:00' };
  if (exportShiftType.value === 'night') return { start: '20:00', end: '08:00' };
  return { start: exportCustomStart.value, end: exportCustomEnd.value };
};

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
    const params = { project_id: projectStore.currentProjectId };
    if (channelFilter.value !== null) params.channel_id = channelFilter.value;
    const res = await getSessionDates(params);
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
    const { start, end } = getShiftHours();
    const dc = projectStore.currentProject?.data_config || {};
    const shiftParam = dc.shift_split_enabled && shiftType.value !== 'all' && shiftType.value !== 'custom'
      ? shiftType.value : null;
    const res = await getSessionsByDate(date, projectStore.currentProjectId, start, end, channelFilter.value, shiftParam);
    sessions.value = res.data?.sessions || [];
    
    overviewData.total_cycles = res.data?.total_cycles || 0;
    overviewData.good_cycles = res.data?.total_good || 0;
    overviewData.ng_cycles = res.data?.total_ng || 0;
    overviewData.avg_cycle_time = res.data?.avg_cycle_time || 0;
    overviewData.yield_rate = overviewData.total_cycles > 0 
      ? (overviewData.good_cycles / overviewData.total_cycles * 100) 
      : 0;
    
    historyCounters.value = res.data?.counters_summary || {};
    
    await loadStepStats({ date, project_id: projectStore.currentProjectId });
    
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

// 时间段切换 - 重新加载当前日期的数据
const handleShiftChange = () => {
  if (selectedDate.value) {
    handleDateChange(selectedDate.value);
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
    
    // 加载周期列表（分页）
    cyclePage.value = 1;
    await loadCycles(session.id);
    
    // 加载步骤统计
    await loadStepStats({ session_id: session.id });
  } catch (e) {
    console.error('加载会话详情失败:', e);
  } finally {
    loadingOverview.value = false;
  }
};

const loadCycles = async (sessionId) => {
  try {
    const skip = (cyclePage.value - 1) * cyclePageSize.value;
    const res = await getSessionCycles(sessionId, skip, cyclePageSize.value);
    cycles.value = res.data?.items || [];
    cycleTotal.value = res.data?.total || 0;
  } catch (e) {
    console.error('加载周期列表失败:', e);
    cycles.value = [];
    cycleTotal.value = 0;
  }
};

const handleCyclePageChange = (page) => {
  cyclePage.value = page;
  if (selectedSession.value) {
    expandedRows.value = [];
    cycleStepsMap.value = {};
    loadCycles(selectedSession.value.id);
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
    const { start: sh, end: eh } = getExportShiftHours();
    const res = await exportDateRangeCsv(selectedDate.value, selectedDate.value, sh, eh);
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
    
    const { start: sh, end: eh } = getExportShiftHours();
    if (exportDialogType.value === 'week' && exportWeek.value) {
      res = await exportWeekCsv(exportWeek.value, sh, eh);
      filename = `data_${exportWeek.value}.csv`;
    } else if (exportDialogType.value === 'month' && exportMonth.value) {
      res = await exportMonthCsv(exportMonth.value, sh, eh);
      filename = `data_${exportMonth.value}.csv`;
    } else if (exportDialogType.value === 'range' && exportDateRange.value?.length === 2) {
      res = await exportDateRangeCsv(exportDateRange.value[0], exportDateRange.value[1], sh, eh);
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
  loadChannelCount();
  loadMesOrders();
  
  if (projectStore.currentProjectId) {
    loadAvailableDates();
  }
});

onUnmounted(() => {
  stopPolling();
});
</script>

<style scoped>
/* =================== Panel & Card Components =================== */
.panel-card {
  background: linear-gradient(145deg, rgba(15, 23, 42, 0.9), rgba(30, 41, 59, 0.6));
  border: 1px solid rgba(51, 65, 85, 0.5);
  border-radius: 12px;
  padding: 16px;
  backdrop-filter: blur(8px);
}

.panel-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.875rem;
  font-weight: 600;
  color: #e2e8f0;
  margin-bottom: 0.75rem;
}

/* =================== Stat Cards =================== */
.stat-card {
  position: relative;
  background: rgba(15, 23, 42, 0.8);
  border: 1px solid rgba(51, 65, 85, 0.4);
  border-radius: 10px;
  padding: 14px 16px;
  text-align: center;
  overflow: hidden;
}

.stat-card::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 2px;
  border-radius: 10px 10px 0 0;
}

.stat-card--cyan::before { background: linear-gradient(90deg, #06b6d4, #22d3ee); }
.stat-card--blue::before { background: linear-gradient(90deg, #3b82f6, #60a5fa); }
.stat-card--green::before { background: linear-gradient(90deg, #10b981, #34d399); }
.stat-card--yellow::before { background: linear-gradient(90deg, #f59e0b, #fbbf24); }
.stat-card--mixed::before { background: linear-gradient(90deg, #10b981, #ef4444); }

.stat-card__label {
  font-size: 0.6875rem;
  color: #64748b;
  margin-bottom: 0.25rem;
}

.stat-card__value {
  font-size: 1.375rem;
  font-weight: 700;
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  line-height: 1.2;
}

/* =================== Session Cards =================== */
.session-card {
  padding: 10px 12px;
  background: rgba(30, 41, 59, 0.5);
  border-radius: 8px;
  border: 1px solid rgba(51, 65, 85, 0.4);
  cursor: pointer;
  transition: all 0.2s ease;
}

.session-card:hover {
  border-color: rgba(71, 85, 105, 0.8);
  background: rgba(30, 41, 59, 0.8);
}

.session-card--active {
  border-color: rgba(6, 182, 212, 0.5) !important;
  background: rgba(6, 182, 212, 0.08) !important;
  box-shadow: 0 0 0 1px rgba(6, 182, 212, 0.15);
}

/* =================== Setting Rows =================== */
.setting-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0.375rem 0.5rem;
  border-radius: 0.375rem;
  font-size: 0.8125rem;
  color: #cbd5e1;
  transition: background 0.15s;
}

.setting-row:hover {
  background: rgba(51, 65, 85, 0.3);
}

/* =================== Custom Scrollbar =================== */
.custom-scrollbar::-webkit-scrollbar {
  width: 0.25rem;
}

.custom-scrollbar::-webkit-scrollbar-track {
  background: transparent;
}

.custom-scrollbar::-webkit-scrollbar-thumb {
  background: #334155;
  border-radius: 0.25rem;
}

.custom-scrollbar::-webkit-scrollbar-thumb:hover {
  background: #475569;
}

/* =================== Settings Tabs =================== */
.settings-tabs :deep(.el-tabs__header) {
  margin-bottom: 0;
  border-bottom: 1px solid rgba(51, 65, 85, 0.5);
}

.settings-tabs :deep(.el-tabs__nav-wrap::after) {
  display: none;
}

.settings-tabs :deep(.el-tabs__item) {
  color: #64748b;
  font-size: 0.8125rem;
  font-weight: 500;
  padding: 0 1.25rem;
  height: 2.375rem;
  line-height: 2.375rem;
}

.settings-tabs :deep(.el-tabs__item:hover) {
  color: #94a3b8;
}

.settings-tabs :deep(.el-tabs__item.is-active) {
  color: #22d3ee;
}

.settings-tabs :deep(.el-tabs__active-bar) {
  background: linear-gradient(90deg, #06b6d4, #3b82f6);
  height: 2px;
  border-radius: 1px;
}

.settings-tabs :deep(.el-tabs__content) {
  padding: 0;
}

.settings-tabs :deep(.el-tab-pane) {
  padding: 12px 0 4px 0;
}

/* =================== Table Dark Theme =================== */
.dark-table :deep(.el-table),
:deep(.el-table) {
  --el-table-bg-color: transparent;
  --el-table-tr-bg-color: transparent;
  --el-table-header-bg-color: rgba(30, 41, 59, 0.6);
  --el-table-row-hover-bg-color: rgba(51, 65, 85, 0.4);
  --el-table-border-color: rgba(51, 65, 85, 0.4);
  --el-table-text-color: #cbd5e1;
  --el-table-header-text-color: #64748b;
  background-color: transparent;
  font-size: 0.75rem;
}

:deep(.el-table th.el-table__cell) {
  background-color: rgba(30, 41, 59, 0.6);
  font-weight: 500;
}

:deep(.el-table td.el-table__cell) {
  border-bottom: 1px solid rgba(51, 65, 85, 0.3);
}

:deep(.el-table--border .el-table__cell) {
  border-right: 1px solid rgba(51, 65, 85, 0.3);
}

:deep(.el-table__body tr:hover > td.el-table__cell) {
  background-color: rgba(51, 65, 85, 0.3) !important;
}

:deep(.el-table__expand-icon) {
  color: #64748b;
}

:deep(.el-table__expand-icon--expanded) {
  color: #22d3ee;
}

:deep(.el-table__empty-text) {
  color: #475569;
}

/* =================== Input / Date Picker Dark Theme =================== */
:deep(.el-date-editor) {
  --el-input-bg-color: rgba(30, 41, 59, 0.8);
  --el-input-border-color: rgba(51, 65, 85, 0.5);
  --el-input-text-color: #e2e8f0;
  --el-input-placeholder-color: #475569;
  --el-input-hover-border-color: #475569;
  --el-input-focus-border-color: #0ea5e9;
}

:deep(.el-date-editor .el-input__wrapper) {
  background-color: rgba(30, 41, 59, 0.8);
  box-shadow: 0 0 0 1px rgba(51, 65, 85, 0.5) inset;
}

:deep(.el-date-editor .el-input__wrapper:hover) {
  box-shadow: 0 0 0 1px rgba(71, 85, 105, 0.7) inset;
}

:deep(.el-date-editor .el-input__wrapper.is-focus) {
  box-shadow: 0 0 0 1px rgba(14, 165, 233, 0.6) inset;
}

:deep(.el-date-editor .el-input__inner) {
  color: #e2e8f0;
}

:deep(.el-date-editor .el-input__prefix-inner .el-icon) {
  color: #475569;
}

/* =================== Divider =================== */
:deep(.el-divider) {
  border-color: rgba(51, 65, 85, 0.4);
}

/* =================== Button Tweaks =================== */
:deep(.el-button--default) {
  --el-button-bg-color: rgba(30, 41, 59, 0.8);
  --el-button-border-color: rgba(51, 65, 85, 0.5);
  --el-button-text-color: #e2e8f0;
  --el-button-hover-bg-color: rgba(51, 65, 85, 0.6);
  --el-button-hover-border-color: rgba(71, 85, 105, 0.7);
  --el-button-hover-text-color: #f1f5f9;
}
</style>
