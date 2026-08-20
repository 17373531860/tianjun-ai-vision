<template>
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
                  <el-input-number v-model="exportSettings.video_fps" :min="0" :precision="0" size="small" style="width: 90px" @change="saveExportSettings" />
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

            <!-- v2.7.2: 导出范围提示条 + "全部项目"开关 -->
            <div class="rounded border border-slate-700 bg-slate-900/50 p-2.5 space-y-2">
              <div class="text-xs text-gray-300 leading-relaxed">
                <div class="text-gray-400 mb-1">导出范围</div>
                <div class="text-yellow-300 font-mono break-all">{{ exportScopeText }}</div>
              </div>
              <div v-if="channelFilter === null && totalChannelCount > 1" class="text-xs text-orange-300">
                ⚠ 多工位模式，建议先在左上角"工位"筛选器选一个工位再导出
              </div>
              <div class="flex items-center justify-between pt-1 border-t border-slate-700/60">
                <span class="text-xs text-gray-400">导出全部项目</span>
                <el-switch v-model="exportAllProjects" size="small" />
              </div>
              <!-- v3.8.x: 导出格式 (CSV / TXT / XLSX / DOCX / PDF) -->
              <div class="flex items-center justify-between pt-1 border-t border-slate-700/60">
                <span class="text-xs text-gray-400">输出格式</span>
                <el-select v-model="exportOutputFormat" size="small" class="w-28">
                  <el-option label="CSV" value="csv" />
                  <el-option label="TXT" value="txt" />
                  <el-option label="XLSX (Excel)" value="xlsx" />
                  <el-option label="DOCX (Word)" value="docx" />
                  <el-option label="PDF" value="pdf" />
                </el-select>
              </div>
            </div>

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

            <!-- v3.5.0: 自定义导出 (txt/csv 模板，可选 cycle/session/range/system) -->
            <div class="border-t border-slate-800 pt-3 mt-1 space-y-2">
              <el-button type="success" class="w-full" @click="$emit('open-custom-export')">
                <el-icon class="mr-1"><MagicStick /></el-icon> 自定义导出 / 客户模板
              </el-button>
              <el-button type="info" plain class="w-full" @click="realtimeRulesVisible = true">
                <el-icon class="mr-1"><Connection /></el-icon> 实时规则（扫码自动写入）
              </el-button>
              <!-- v3.8.x: 定时导出 — 按 cron 周期性把"标准日报"自动写到客户指定目录 -->
              <el-button type="warning" plain class="w-full" @click="scheduledRulesVisible = true">
                <el-icon class="mr-1"><Clock /></el-icon> 定时导出（每天某时自动）
              </el-button>
              <!-- v3.46: 短信日报 — 每天定点把当日 KPI 摘要发到客户手机 -->
              <el-button type="success" plain class="w-full" @click="smsReportVisible = true">
                <el-icon class="mr-1"><Message /></el-icon> 短信日报（每天发到手机）
              </el-button>
              <div class="text-[11px] text-gray-500 leading-relaxed px-1">
                支持 Jinja2 模板渲染、308 项数据字段、txt/csv 输出。<br>
                <span class="text-cyan-400">实时规则</span>：cycle 结束后按规则自动渲染落盘到客户文件夹。<br>
                <span class="text-orange-400">定时导出</span>：按 cron 定时把日报 CSV/XLSX/PDF 等写到指定目录。
              </div>
            </div>

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
            <!-- v3.53: 录像归档卡 -->
            <div class="border-t border-slate-800 pt-4 mt-4" data-test="video-archive-card">
              <div class="text-xs text-gray-500 font-medium mb-3 uppercase tracking-wider">录像归档</div>
              <div class="text-xs text-gray-600 pl-1 mb-2">
                周期录像收尾后自动拷贝到指定目录（本地或网络盘），支持仅 NG 筛选、按条码/工单命名。
              </div>
              <div class="space-y-2">
                <div class="setting-row">
                  <span>归档规则</span>
                  <span class="font-mono text-xs" :class="archiveStatus.rules_enabled > 0 ? 'text-cyan-400' : 'text-gray-500'">
                    {{ archiveStatus.rules_enabled || 0 }} / {{ archiveStatus.rules_total || 0 }} 启用
                  </span>
                </div>
                <div class="setting-row">
                  <span>累计成功 / 失败</span>
                  <span class="font-mono text-xs">
                    <span class="text-emerald-400">{{ archiveStatus.success || 0 }}</span>
                    <span class="text-gray-600"> / </span>
                    <span :class="archiveStatus.failed > 0 ? 'text-red-400' : 'text-gray-500'">{{ archiveStatus.failed || 0 }}</span>
                  </span>
                </div>
                <div v-if="archiveStatus.spool_depth > 0" class="text-xs text-yellow-400 pl-1">
                  {{ archiveStatus.spool_depth }} 个任务等待重试（目标目录可能暂不可写）
                </div>
              </div>
              <el-button type="info" plain size="small" class="w-full mt-3" @click="videoArchiveVisible = true">
                配置录像归档
              </el-button>
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
                  <el-input-number v-model="cleanupSettings.retention_days" :min="0" :precision="0" size="small" controls-position="right" style="width: 100px" @change="saveCleanupSettings" />
                </div>
                <div class="text-xs text-gray-600 pl-1">
                  超过 {{ cleanupSettings.retention_days }} 天的数据将被自动清理
                </div>
                <div class="setting-row">
                  <span>OK/NG 录像分开存</span>
                  <el-switch v-model="cleanupSettings.video_split_ok_ng" @change="saveCleanupSettings" size="small" />
                </div>
                <template v-if="cleanupSettings.video_split_ok_ng">
                  <div class="setting-row">
                    <span>OK 录像保留天数</span>
                    <el-input-number v-model="cleanupSettings.video_ok_retention_days" :min="0" :precision="0" size="small" controls-position="right" style="width: 100px" @change="saveCleanupSettings" />
                  </div>
                  <div class="setting-row">
                    <span>NG 录像保留天数</span>
                    <el-input-number v-model="cleanupSettings.video_ng_retention_days" :min="0" :precision="0" size="small" controls-position="right" style="width: 100px" @change="saveCleanupSettings" />
                  </div>
                  <div class="text-xs text-gray-600 pl-1">
                    仅作用于周期录像文件。数据记录仍至少保留上方天数；NG 录像更久时其周期记录会同步保留以便回放。
                  </div>
                </template>
              </div>
              <el-button type="warning" plain size="small" class="w-full mt-3" @click="handleRunCleanup" :loading="runningCleanup">
                立即执行清理
              </el-button>
            </div>
            <div class="border-t border-slate-800 pt-4">
              <div class="text-xs text-gray-500 font-medium mb-3 uppercase tracking-wider">导出与日志清理</div>
              <div class="space-y-2">
                <div class="setting-row">
                  <span>每日固定清理时点</span>
                  <el-time-picker
                    v-model="cleanupSettings.cleanup_daily_time"
                    format="HH:mm"
                    value-format="HH:mm"
                    placeholder="不设=开机后清理"
                    size="small"
                    style="width: 140px"
                    @change="saveCleanupSettings"
                  />
                </div>
                <div class="text-xs text-gray-600 pl-1">
                  留空 = 仅开机及每 24 小时清理；设定后每天到点再清一次。
                </div>
                <div class="setting-row">
                  <span>导出文件保留天数</span>
                  <el-input-number v-model="cleanupSettings.export_retention_days" :min="0" :precision="0" size="small" controls-position="right" style="width: 100px" @change="saveCleanupSettings" />
                </div>
                <div class="setting-row">
                  <span>过程日志保留天数</span>
                  <el-input-number v-model="cleanupSettings.log_retention_days" :min="0" :precision="0" size="small" controls-position="right" style="width: 100px" @change="saveCleanupSettings" />
                </div>
                <div class="text-xs text-gray-600 pl-1">
                  0 = 跟随上方保留天数。过程日志指扫码 / MES 通讯 / 外设流水，不含工单等业务数据。
                </div>
              </div>
            </div>
            <div class="border-t border-slate-800 pt-4">
              <div class="text-xs text-gray-500 font-medium mb-3 uppercase tracking-wider">自定义导出目录</div>
              <div class="space-y-2">
                <div class="text-xs text-gray-600 pl-1 mb-1">
                  登记导出文件的落地目录后，可对其做托底扫描清理（仅删 txt/csv/docx/xlsx/pdf 文件、不删子目录，系统盘 / 盘根目录禁止登记）。
                </div>
                <el-input
                  v-model="cleanupSettings.export_cleanup_dir"
                  placeholder="如 D:\导出 或 /data/export"
                  size="small"
                  clearable
                  @change="saveCleanupSettings"
                />
                <div class="setting-row">
                  <span>对该目录做托底扫描清理</span>
                  <el-switch v-model="cleanupSettings.export_cleanup_scan_dir" @change="saveCleanupSettings" size="small" :disabled="!cleanupSettings.export_cleanup_dir" />
                </div>
                <div class="text-xs text-gray-600 pl-1">
                  不开启时只按导出台账精准清理我方产出文件；开启后才会扫描该目录兜底（仍受上述护栏限制）。
                </div>
              </div>
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

  <!-- v3.5.0 Step 4: 实时规则管理对话框 -->
  <RealtimeRulesDialog v-model="realtimeRulesVisible" />

  <!-- v3.8.x: 定时导出规则管理对话框 -->
  <ScheduledRulesDialog v-model="scheduledRulesVisible" />

  <!-- v3.46: 每日短信日报管理对话框 -->
  <SmsReportDialog v-model="smsReportVisible" />

  <!-- v3.53: 录像归档规则管理对话框 -->
  <VideoArchiveDialog v-model="videoArchiveVisible" />
</template>

<script setup>
// Data 页底部功能区（记录设置 / 数据导出 / 存储与清理）+ 随区弹窗。
// 自定义导出弹窗需要父视图的日期/会话上下文预填，留在父视图（emit 上抛）。
// 清空/清理成功后 emit 'cleared'/'cleaned' 让父视图刷新查询区状态。
import { ref, reactive, computed, watch, onMounted } from 'vue';
import { useSystemStore } from '@/store/useSystemStore';
import { useProjectStore } from '@/store/useProjectStore';
import { Clock, Download, MagicStick, Connection, Message } from '@element-plus/icons-vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import RealtimeRulesDialog from './RealtimeRulesDialog.vue';
import ScheduledRulesDialog from './ScheduledRulesDialog.vue';
import SmsReportDialog from './SmsReportDialog.vue';
import VideoArchiveDialog from './VideoArchiveDialog.vue';
import { getArchiveStatus } from '@/api/videoArchive';
import {
  updateExportSettings,
  exportWeekCsv,
  exportMonthCsv,
  exportDateRangeCsv,
  downloadBlob,
  backupDatabase,
  clearAllData,
  clearDataByRange,
  getCleanupSettings,
  updateCleanupSettings,
  runCleanupNow,
  getStorageInfo
} from '@/api/data';
import { getProjectDetail, updateProject } from '@/api/project';
import { dbg, dbgErr } from '@/utils/debug';

const props = defineProps({
  selectedDate: { type: String, default: '' },
  channelFilter: { type: Number, default: null },
  totalChannelCount: { type: Number, default: 1 },
});

const emit = defineEmits(['open-custom-export', 'cleared', 'cleaned']);

const store = useSystemStore();
const projectStore = useProjectStore();

const currentProjectName = computed(() => projectStore.currentProject?.name || '');

// 底部功能区Tab
const activeSettingsTab = ref('export');

// v2.7.2: 导出范围开关 —— 默认按"当前项目"导出，避免混入其他项目数据
const exportAllProjects = ref(false);

// v3.8.x: 4 个快捷导出按钮共用的输出格式 (csv / txt / xlsx / docx / pdf)
const exportOutputFormat = ref('csv');

// 导出范围摘要文案（用于按钮上方提示条）
const exportScopeText = computed(() => {
  const projText = exportAllProjects.value
    ? '【全部项目】'
    : (currentProjectName.value ? `【${currentProjectName.value}】` : '【当前项目】');
  const chText = props.channelFilter === null
    ? '【全部工位】'
    : `【工位${props.channelFilter + 1}】`;
  const dateText = props.selectedDate ? `【${props.selectedDate}】` : '【未选日期】';
  return `项目 ${projText} · 工位 ${chText} · 日期 ${dateText}`;
});

// 导出设置
const exportSettings = reactive({
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
});

// 默认导出设置
const defaultExportSettings = { ...exportSettings };

// 加载导出设置（从项目）
const loadExportSettings = async () => {
  if (!projectStore.currentProjectId) {
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
    await updateExportSettings(exportSettings);
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

// v2.7.2: 构造导出文件名的项目/工位标识段
const buildExportFilenameScope = () => {
  const projSeg = exportAllProjects.value
    ? 'projALL'
    : (projectStore.currentProjectId ? `proj${projectStore.currentProjectId}` : 'projNA');
  const chSeg = props.channelFilter === null ? 'chALL' : `ch${props.channelFilter + 1}`;
  return `${projSeg}_${chSeg}`;
};

// v2.7.2: 解析当前导出参数
const resolveExportScopeParams = () => {
  const projectId = exportAllProjects.value ? null : (projectStore.currentProjectId || null);
  const channelId = props.channelFilter; // null 表示全部工位
  // v3.5.x: PT/CT 显示口径透传给后端 — avg 时 CSV 多输出"耗时(平均/秒)"列
  const ptMode = store.display?.monitor?.ptMode || null;
  const ctMode = store.display?.monitor?.ctMode || null;
  return { projectId, channelId, ptMode, ctMode };
};

const exporting = ref(false);

// 导出当日数据 (v3.8.x: 加 outputFormat 支持)
const exportByDate = async () => {
  if (!props.selectedDate) {
    ElMessage.warning('请先选择日期');
    return;
  }

  exporting.value = true;
  dbg('data.export', '点击「导出当日数据」', `date=${props.selectedDate ?? ''} fmt=${exportOutputFormat.value ?? 'csv'}`);
  try {
    const { start: sh, end: eh } = getExportShiftHours();
    const { projectId, channelId, ptMode, ctMode } = resolveExportScopeParams();
    const fmt = exportOutputFormat.value || 'csv';
    const res = await exportDateRangeCsv(
      props.selectedDate, props.selectedDate, sh, eh, projectId, channelId, ptMode, ctMode, fmt
    );
    const filename = `data_${buildExportFilenameScope()}_${props.selectedDate}.${fmt}`;
    downloadBlob(res.data, filename);
    ElMessage.success(`已导出到下载文件夹: ${filename}`);
  } catch (e) {
    dbgErr('data.export', '导出当日数据', e);
    console.error('导出失败:', e);
    ElMessage.error('导出失败: ' + (e.message || '未知错误'));
  } finally {
    exporting.value = false;
  }
};

// 导出弹窗
const exportDialogVisible = ref(false);
const exportDialogType = ref('');
const exportWeek = ref('');
const exportWeekDate = ref(null);
const exportMonth = ref('');
const exportDateRange = ref([]);

const exportDialogTitle = computed(() => {
  switch (exportDialogType.value) {
    case 'week': return '导出某周数据';
    case 'month': return '导出某月数据';
    case 'range': return '导出日期范围';
    default: return '导出';
  }
});

// 显示导出弹窗
const showExportDialog = (type) => {
  dbg('data.export', '打开快捷导出弹窗', `type=${type ?? ''}`);
  exportDialogType.value = type;
  exportWeek.value = '';
  exportWeekDate.value = null;
  exportMonth.value = '';
  exportDateRange.value = [];
  exportDialogVisible.value = true;
};

// 处理周选择变化 - 计算ISO周
const handleWeekChange = (date) => {
  if (date) {
    const d = new Date(date);
    const target = new Date(d.valueOf());
    // ISO周从周一开始，调整到最近的周四（ISO周算法）
    const dayNr = (d.getDay() + 6) % 7;
    target.setDate(target.getDate() - dayNr + 3);
    const firstThursday = new Date(target.getFullYear(), 0, 4);
    const dayOfWeek = (firstThursday.getDay() + 6) % 7;
    firstThursday.setDate(firstThursday.getDate() - dayOfWeek + 3);
    const weekNum = 1 + Math.round((target - firstThursday) / 604800000);
    const year = target.getFullYear();
    exportWeek.value = `${year}-W${weekNum.toString().padStart(2, '0')}`;
    console.log('选择的周:', exportWeek.value, '日期:', d.toISOString().split('T')[0]);
  } else {
    exportWeek.value = '';
  }
};

// 执行导出 (v3.8.x: 加 outputFormat 支持)
const handleExport = async () => {
  exporting.value = true;
  dbg('data.export', '执行快捷导出', `type=${exportDialogType.value ?? ''} fmt=${exportOutputFormat.value ?? 'csv'}`);
  try {
    let res, filename;

    const { start: sh, end: eh } = getExportShiftHours();
    const { projectId, channelId, ptMode, ctMode } = resolveExportScopeParams();
    const scope = buildExportFilenameScope();
    const fmt = exportOutputFormat.value || 'csv';
    if (exportDialogType.value === 'week' && exportWeek.value) {
      res = await exportWeekCsv(exportWeek.value, sh, eh, projectId, channelId, ptMode, ctMode, fmt);
      filename = `data_${scope}_${exportWeek.value}.${fmt}`;
    } else if (exportDialogType.value === 'month' && exportMonth.value) {
      res = await exportMonthCsv(exportMonth.value, sh, eh, projectId, channelId, ptMode, ctMode, fmt);
      filename = `data_${scope}_${exportMonth.value}.${fmt}`;
    } else if (exportDialogType.value === 'range' && exportDateRange.value?.length === 2) {
      res = await exportDateRangeCsv(exportDateRange.value[0], exportDateRange.value[1], sh, eh, projectId, channelId, ptMode, ctMode, fmt);
      filename = `data_${scope}_${exportDateRange.value[0]}_to_${exportDateRange.value[1]}.${fmt}`;
    } else {
      ElMessage.warning('请选择导出范围');
      exporting.value = false;
      return;
    }

    downloadBlob(res.data, filename);
    ElMessage.success(`已导出到下载文件夹: ${filename}`);
    exportDialogVisible.value = false;
  } catch (e) {
    dbgErr('data.export', '执行快捷导出', e);
    console.error('导出失败:', e);
    ElMessage.error('导出失败: ' + (e.message || '未知错误'));
  } finally {
    exporting.value = false;
  }
};

// 备份数据库
const handleBackup = () => {
  dbg('data.maintain', '点击「备份数据库」');
  try {
    backupDatabase();
    ElMessage.success('数据库备份文件已开始下载');
  } catch (e) {
    console.error('备份失败:', e);
    ElMessage.error('备份失败: ' + (e.message || '未知错误'));
  }
};

// 清空数据
const clearing = ref(false);
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
    dbg('data.maintain', '确认清空所有数据');
    const res = await clearAllData();
    const deleted = res.data.deleted;
    ElMessage.success(`清理完成：${deleted.sessions}个会话, ${deleted.cycles}个周期, ${deleted.steps}条步骤, ${deleted.files}个视频文件`);
    loadStorageInfo();
    emit('cleared');
  } catch (err) {
    if (err !== 'cancel') {
      dbgErr('data.maintain', '清空所有数据', err);
      ElMessage.error('清理失败: ' + (err.response?.data?.detail || err.message));
    }
  } finally {
    clearing.value = false;
  }
};

// ========== 清理设置相关 ==========

const cleanupSettings = reactive({
  retention_days: 30,
  auto_cleanup: true,
  video_split_ok_ng: false,
  video_ok_retention_days: 7,
  video_ng_retention_days: 180,
  log_retention_days: 0,
  export_retention_days: 0,
  export_cleanup_dir: '',
  export_cleanup_scan_dir: false,
  cleanup_daily_time: ''
});
const savingCleanupSettings = ref(false);
const runningCleanup = ref(false);
const clearingRange = ref(false);
const clearDateRange = ref([]);

// 禁用未来日期
const disabledDate = (time) => {
  return time.getTime() > Date.now();
};

const loadCleanupSettings = async () => {
  try {
    const res = await getCleanupSettings();
    cleanupSettings.retention_days = res.data.retention_days;
    cleanupSettings.auto_cleanup = res.data.auto_cleanup;
    cleanupSettings.video_split_ok_ng = res.data.video_split_ok_ng;
    cleanupSettings.video_ok_retention_days = res.data.video_ok_retention_days;
    cleanupSettings.video_ng_retention_days = res.data.video_ng_retention_days;
    cleanupSettings.log_retention_days = res.data.log_retention_days ?? 0;
    cleanupSettings.export_retention_days = res.data.export_retention_days ?? 0;
    cleanupSettings.export_cleanup_dir = res.data.export_cleanup_dir || '';
    cleanupSettings.export_cleanup_scan_dir = !!res.data.export_cleanup_scan_dir;
    cleanupSettings.cleanup_daily_time = res.data.cleanup_daily_time || '';
  } catch (e) {
    console.error('加载清理设置失败:', e);
  }
};

const saveCleanupSettings = async () => {
  savingCleanupSettings.value = true;
  try {
    await updateCleanupSettings({
      retention_days: cleanupSettings.retention_days,
      auto_cleanup: cleanupSettings.auto_cleanup,
      video_split_ok_ng: cleanupSettings.video_split_ok_ng,
      video_ok_retention_days: cleanupSettings.video_ok_retention_days,
      video_ng_retention_days: cleanupSettings.video_ng_retention_days,
      log_retention_days: cleanupSettings.log_retention_days,
      export_retention_days: cleanupSettings.export_retention_days,
      export_cleanup_dir: cleanupSettings.export_cleanup_dir || '',
      export_cleanup_scan_dir: cleanupSettings.export_cleanup_scan_dir,
      cleanup_daily_time: cleanupSettings.cleanup_daily_time || ''
    });
    ElMessage.success('清理设置已保存');
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '保存清理设置失败');
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
    dbg('data.maintain', '确认手动清理', `retention_days=${cleanupSettings?.retention_days ?? ''}`);
    await runCleanupNow();
    ElMessage.success('清理已完成');
    loadStorageInfo();
    emit('cleaned');
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
    dbg('data.maintain', '确认按范围删除', `range=${start ?? ''}~${end ?? ''}`);
    const res = await clearDataByRange(start, end);
    const d = res.data.deleted;
    ElMessage.success(`清理完成：${d.sessions}个会话, ${d.cycles}个周期, ${d.steps}条步骤, ${d.files}个文件`);
    clearDateRange.value = [];
    loadStorageInfo();
    emit('cleared');
  } catch (err) {
    if (err !== 'cancel') ElMessage.error('清理失败: ' + (err.response?.data?.detail || err.message));
  } finally {
    clearingRange.value = false;
  }
};

// 存储信息
const storageInfo = reactive({
  data_size_mb: 0,
  breakdown: { database: 0, recordings: 0, uploads: 0 },
  disk_total_gb: 0,
  disk_used_gb: 0,
  disk_free_gb: 0,
  disk_usage_percent: 0
});

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

// 格式化存储大小（MB → 自动转换为合适单位）
const formatSize = (mb) => {
  if (!mb && mb !== 0) return '0 MB';
  if (mb >= 1024) return `${(mb / 1024).toFixed(2)} GB`;
  return `${mb.toFixed(2)} MB`;
};

// ========== 随区弹窗（实时规则 / 定时导出 / 短信日报 / 录像归档） ==========
const realtimeRulesVisible = ref(false);
const scheduledRulesVisible = ref(false);
const smsReportVisible = ref(false);

// v3.53: 录像归档对话框 + 状态卡
const videoArchiveVisible = ref(false);
const archiveStatus = ref({});
async function loadArchiveStatus() {
  try {
    const res = await getArchiveStatus();
    archiveStatus.value = res.data || {};
  } catch (e) {
    dbgErr('data', '加载录像归档状态失败', e);
  }
}
// 弹窗里可能改了规则/跑了试归档, 关掉后刷新卡片计数
watch(videoArchiveVisible, (v) => { if (!v) loadArchiveStatus(); });

// 监听项目变化，重新加载设置
watch(() => projectStore.currentProjectId, () => {
  loadExportSettings();
});

onMounted(() => {
  loadExportSettings();
  loadCleanupSettings();
  loadStorageInfo();
  loadArchiveStatus();
});
</script>

<style scoped>
.panel-card {
  background: linear-gradient(145deg, rgba(15, 23, 42, 0.9), rgba(30, 41, 59, 0.6));
  border: 1px solid rgba(51, 65, 85, 0.5);
  border-radius: 12px;
  padding: 16px;
  backdrop-filter: blur(8px);
}

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

:deep(.el-button--default) {
  --el-button-bg-color: rgba(30, 41, 59, 0.8);
  --el-button-border-color: rgba(51, 65, 85, 0.5);
  --el-button-text-color: #e2e8f0;
  --el-button-hover-bg-color: rgba(51, 65, 85, 0.6);
  --el-button-hover-border-color: rgba(71, 85, 105, 0.7);
  --el-button-hover-text-color: #f1f5f9;
}
</style>
