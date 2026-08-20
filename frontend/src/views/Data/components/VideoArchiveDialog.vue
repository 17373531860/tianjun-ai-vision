<!--
  v3.53 一~四期: 录像归档规则管理对话框

  功能：
  - 引擎状态条（worker/队列/spool 积压/成功失败计数/最近错误）
  - 规则列表（启停、筛选、目标目录、命名模板、累计统计、历史回补）
  - 新建/编辑规则（结果筛选 x 通道/项目过滤 x 目的地(本地/FTP/SFTP/S3/HTTP/插件)
    x Jinja2 文件名模板 x 重名策略 x 证据能力(关键帧/sidecar/证据包/事件切片)
    x 治理(时间窗/限速/归档后删源)）
  - 试归档（取最近一个带录像的周期立即跑一遍）
  - 归档台账（每个文件去向/成败/耗时）+ 手动证据包导出
-->
<template>
  <el-dialog
    v-model="visible"
    title="录像归档"
    width="88%"
    top="4vh"
    :close-on-click-modal="false"
    destroy-on-close
    class="dark-dialog va-dialog"
    @open="onOpen"
  >
    <!-- 状态条 -->
    <div class="va-status-bar flex flex-wrap items-center gap-x-5 gap-y-1 mb-3 px-3 py-2 rounded bg-slate-800/60 text-xs">
      <span>
        引擎：
        <el-tag size="small" :type="status.worker_alive ? 'success' : 'info'">
          {{ status.worker_alive ? '运行中' : '待机' }}
        </el-tag>
      </span>
      <span class="font-mono">
        <span class="text-emerald-400" title="成功">✓ {{ status.success || 0 }}</span> ·
        <span class="text-red-400" title="失败">✗ {{ status.failed || 0 }}</span> ·
        <span class="text-gray-400" title="跳过">- {{ status.skipped || 0 }}</span>
      </span>
      <span class="text-gray-400">队列 <span class="font-mono">{{ status.queue_depth || 0 }}</span></span>
      <span :class="status.spool_depth > 0 ? 'text-yellow-400' : 'text-gray-400'">
        待重试(spool) <span class="font-mono">{{ status.spool_depth || 0 }}</span>
      </span>
      <span v-if="status.last_error" class="text-red-400 truncate max-w-[360px]" :title="status.last_error">
        最近错误: {{ status.last_error }}
      </span>
      <el-button size="small" link type="primary" class="ml-auto" @click="loadStatus">
        <el-icon class="mr-0.5"><Refresh /></el-icon>刷新
      </el-button>
    </div>

    <el-tabs v-model="activeTab" class="va-tabs">
      <!-- ========== Tab 1: 规则列表 ========== -->
      <el-tab-pane label="归档规则" name="rules">
        <div class="flex justify-between items-center mb-3">
          <div class="text-xs text-gray-400">
            周期录像收尾后自动按规则拷贝到指定目录（本地或已挂载的网络盘）。目录断开时任务落盘等待重试，不丢任务、不阻塞检测。
          </div>
          <div class="flex gap-2">
            <el-button size="small" type="warning" plain :loading="testRunning" @click="onTestRun()">
              试归档（取最近一条录像）
            </el-button>
            <el-button type="primary" size="small" @click="onCreate">
              <el-icon class="mr-1"><Plus /></el-icon>新建规则
            </el-button>
          </div>
        </div>

        <el-table :data="rules" size="small" class="dark-table" empty-text="暂无规则，点上方按钮新建一条">
          <el-table-column type="index" width="40" />
          <el-table-column prop="name" label="名称" min-width="130">
            <template #default="{ row }">
              <div class="flex items-center gap-2">
                <el-switch :model-value="row.enabled" size="small" @change="onToggle(row)" />
                <span :class="{ 'text-gray-500': !row.enabled }">{{ row.name }}</span>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="筛选" width="150">
            <template #default="{ row }">
              <div class="text-[11px]">
                <el-tag size="small" :type="resultTagType(row.result_filter)">
                  {{ resultLabel(row.result_filter) }}
                </el-tag>
                <div v-if="row.channel_filter" class="text-gray-500 mt-0.5">通道: {{ row.channel_filter.join(',') }}</div>
                <div v-if="row.project_filter" class="text-gray-500">项目: {{ row.project_filter.join(',') }}</div>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="目标" min-width="220">
            <template #default="{ row }">
              <div class="font-mono text-[11px]">
                <div class="text-cyan-400 truncate" :title="row.dest_dir">
                  <el-tag v-if="row.dest_type && row.dest_type !== 'local_dir'" size="small" type="warning" class="mr-1">
                    {{ row.dest_type }}
                  </el-tag>
                  {{ row.dest_dir }}<span v-if="row.subdir_by_date" class="text-gray-500">/日期</span>
                </div>
                <div class="text-gray-500 truncate" :title="row.filename_template">{{ row.filename_template }}</div>
                <div class="text-[10px] text-gray-500 mt-0.5">
                  <span v-if="row.attach_keyframe" class="mr-1">📷关键帧</span>
                  <span v-if="row.sidecar_template_id" class="mr-1">📄报告</span>
                  <span v-if="row.bundle_zip" class="mr-1">📦证据包</span>
                  <span v-if="row.transform === 'clip_tail'" class="mr-1">✂️尾段{{ row.clip_seconds }}s</span>
                  <span v-if="row.active_window" class="mr-1">🕒{{ row.active_window }}</span>
                  <span v-if="row.delete_source_after" class="text-orange-400">归档后删源</span>
                </div>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="重名" width="80">
            <template #default="{ row }">
              <span class="text-xs text-gray-400">{{ policyLabel(row.overwrite_policy) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="最近" width="150">
            <template #default="{ row }">
              <div class="text-[11px]">
                <el-tag v-if="row.last_run_status === 'success'" size="small" type="success">success</el-tag>
                <el-tag v-else-if="row.last_run_status === 'failed'" size="small" type="danger">failed</el-tag>
                <el-tag v-else-if="row.last_run_status === 'skipped'" size="small" type="info">skipped</el-tag>
                <span v-else class="text-gray-500">未运行</span>
                <div class="text-gray-500 mt-0.5" v-if="row.last_run_time">{{ formatTime(row.last_run_time) }}</div>
                <div class="text-red-400 truncate" v-if="row.last_run_error" :title="row.last_run_error">
                  {{ row.last_run_error }}
                </div>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="累计" width="120">
            <template #default="{ row }">
              <div class="text-[11px] font-mono">
                <span class="text-emerald-400" title="成功">✓ {{ row.success_count }}</span> ·
                <span class="text-red-400" title="失败">✗ {{ row.failed_count }}</span> ·
                <span class="text-gray-400" title="跳过">- {{ row.skipped_count }}</span>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="220" fixed="right">
            <template #default="{ row }">
              <el-button size="small" link type="primary" @click="onEdit(row)">编辑</el-button>
              <el-button size="small" link type="warning" @click="onTestRun(row)">试归档</el-button>
              <el-button size="small" link @click="onBackfill(row)">回补</el-button>
              <el-button size="small" link type="danger" @click="onDelete(row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- ========== Tab 2: 归档台账 ========== -->
      <el-tab-pane label="归档台账" name="logs">
        <div class="flex justify-between items-center mb-3">
          <el-select v-model="logFilter.status" size="small" placeholder="状态" clearable style="width: 110px" @change="loadLogs">
            <el-option label="success" value="success" />
            <el-option label="failed" value="failed" />
            <el-option label="skipped" value="skipped" />
          </el-select>
          <div class="flex gap-2">
            <el-button size="small" type="success" plain @click="packVisible = true">
              导出证据包 (zip)
            </el-button>
            <el-button size="small" @click="loadLogs">
              <el-icon class="mr-1"><Refresh /></el-icon>刷新
            </el-button>
          </div>
        </div>
        <el-table :data="logs" size="small" class="dark-table" empty-text="暂无归档记录">
          <el-table-column prop="id" label="#" width="60" />
          <el-table-column label="状态" width="90">
            <template #default="{ row }">
              <el-tag size="small" :type="row.status === 'success' ? 'success' : row.status === 'failed' ? 'danger' : 'info'">
                {{ row.status }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="cycle_id" label="周期" width="80" />
          <el-table-column label="去向" min-width="260">
            <template #default="{ row }">
              <div class="font-mono text-[11px] text-cyan-400 truncate" :title="row.dest_path || row.src_path">
                {{ row.dest_path || row.src_path }}
              </div>
              <div v-if="row.error" class="text-red-400 text-[11px] truncate" :title="row.error">{{ row.error }}</div>
            </template>
          </el-table-column>
          <el-table-column label="大小" width="90">
            <template #default="{ row }">
              <span class="font-mono text-[11px]">{{ formatBytes(row.file_size) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="耗时" width="80">
            <template #default="{ row }">
              <span class="font-mono text-[11px]">{{ row.duration_ms != null ? row.duration_ms + 'ms' : '-' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="时间" width="150">
            <template #default="{ row }">
              <span class="text-[11px] text-gray-400">{{ formatTime(row.created_at) }}</span>
            </template>
          </el-table-column>
        </el-table>
        <div class="flex justify-end mt-2">
          <el-pagination
            small layout="prev, pager, next, total"
            :total="logTotal" :page-size="logFilter.limit"
            :current-page="logPage" @current-change="onLogPage"
          />
        </div>
      </el-tab-pane>
    </el-tabs>

    <!-- ========== 编辑器 ========== -->
    <el-dialog
      v-model="editorVisible"
      :title="editing.id ? '编辑归档规则' : '新建归档规则'"
      width="640px"
      append-to-body
      :close-on-click-modal="false"
      class="dark-dialog"
    >
      <el-form label-position="top" size="small">
        <el-row :gutter="12">
          <el-col :span="16">
            <el-form-item label="规则名称" required>
              <el-input v-model="editing.name" placeholder="如：NG 录像归档到质量部网盘" maxlength="128" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="启用">
              <el-switch v-model="editing.enabled" />
            </el-form-item>
          </el-col>
        </el-row>

        <el-row :gutter="12">
          <el-col :span="8">
            <el-form-item label="结果筛选">
              <el-select v-model="editing.result_filter" class="w-full">
                <el-option label="仅 NG 周期" value="ng_only" />
                <el-option label="仅 OK 周期" value="ok_only" />
                <el-option label="全部周期" value="all" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="通道过滤（留空=全部）">
              <el-input
                :model-value="(editing.channel_filter || []).join(',')"
                placeholder="如 0,1"
                @input="onChannelFilterInput"
              />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="项目过滤（留空=全部）">
              <el-input
                :model-value="(editing.project_filter || []).join(',')"
                placeholder="项目 ID，如 3"
                @input="onProjectFilterInput"
              />
            </el-form-item>
          </el-col>
        </el-row>

        <el-divider content-position="left" class="!my-3">目的地</el-divider>
        <el-row :gutter="12">
          <el-col :span="8">
            <el-form-item label="目的地类型">
              <el-select v-model="editing.dest_type" class="w-full">
                <el-option v-for="t in adapterTypes" :key="t" :label="destTypeLabel(t)" :value="t" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="16">
            <el-form-item v-if="editing.dest_type === 'local_dir'"
                          label="目标目录（绝对路径；网络盘先在系统里挂载好）" required>
              <el-input v-model="editing.dest_dir" placeholder="如 D:\NG归档 或 \\server\quality\videos" />
            </el-form-item>
            <el-form-item v-else label="备注标签（台账里显示，可留空自动生成）">
              <el-input v-model="editing.dest_dir" placeholder="如 质量部 FTP" />
            </el-form-item>
          </el-col>
        </el-row>

        <!-- 远端 adapter 配置 (敏感字段回显为 ******，不改就保持原值) -->
        <el-row v-if="destConfigFields.length" :gutter="12">
          <el-col v-for="f in destConfigFields" :key="f.key" :span="f.span || 8">
            <el-form-item :label="f.label">
              <el-input
                v-model="editing.dest_config[f.key]"
                :type="f.sensitive ? 'password' : 'text'"
                :show-password="f.sensitive"
                :placeholder="f.placeholder || ''"
              />
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item v-if="editing.dest_type.startsWith('plugin:')" label="插件 adapter 配置（JSON）">
          <el-input v-model="pluginCfgText" type="textarea" :rows="3" placeholder='{"key": "value"}' />
        </el-form-item>
        <div v-if="editing.dest_type !== 'local_dir'" class="text-xs text-gray-500 -mt-1 mb-2">
          远端目的地为覆盖写语义（重名策略仅本地目录生效）；密码/密钥加密存储，回显为 ****** 表示未修改。
        </div>

        <el-row :gutter="12">
          <el-col :span="12">
            <el-form-item label="按日期分子目录">
              <el-switch v-model="editing.subdir_by_date" />
              <span class="text-xs text-gray-500 ml-2">开=目标目录下按 YYYY-MM-DD 存放</span>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="重名策略（仅本地目录生效）">
              <el-select v-model="editing.overwrite_policy" class="w-full" :disabled="editing.dest_type !== 'local_dir'">
                <el-option label="追加序号（推荐）" value="rename" />
                <el-option label="覆盖" value="overwrite" />
                <el-option label="跳过" value="skip" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>

        <el-form-item label="文件名模板（Jinja2，字段与自定义导出同源）" required>
          <el-input v-model="editing.filename_template" />
          <div class="text-xs text-gray-500 mt-1 leading-5">
            常用变量：<code v-pre>{{ workpiece.serial_no }}</code> 条码 ·
            <code v-pre>{{ cycle.id }}</code> 周期号 ·
            <code v-pre>{{ 'OK' if cycle.is_good else 'NG' }}</code> 结果 ·
            <code v-pre>{{ order.order_no }}</code> 工单号 ·
            <code v-pre>{{ channel.id }}</code> 工位
          </div>
        </el-form-item>

        <el-divider content-position="left" class="!my-3">证据能力</el-divider>
        <el-row :gutter="12">
          <el-col :span="8">
            <el-form-item label="附带 NG 关键帧快照">
              <el-switch v-model="editing.attach_keyframe" />
              <span class="text-xs text-gray-500 ml-2">NG 结算瞬间带框画面 JPEG</span>
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="关键帧烧水印">
              <el-switch v-model="editing.keyframe_watermark" :disabled="!editing.attach_keyframe" />
              <span class="text-xs text-gray-500 ml-2">时间戳/工位烧入画面</span>
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="打包成证据包 (zip)">
              <el-switch v-model="editing.bundle_zip" />
              <span class="text-xs text-gray-500 ml-2">录像+关键帧+报告一个 zip</span>
            </el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="12">
          <el-col :span="8">
            <el-form-item label="录像变换">
              <el-select v-model="editing.transform" class="w-full">
                <el-option label="整段归档" value="none" />
                <el-option label="事件切片（只留结尾 N 秒）" value="clip_tail" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="切片秒数">
              <el-input-number v-model="editing.clip_seconds" :min="1" :max="600"
                               :disabled="editing.transform !== 'clip_tail'" class="w-full" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="伴随数据报告（导出模板）">
              <el-select v-model="editing.sidecar_template_id" class="w-full" clearable placeholder="不生成">
                <el-option v-for="t in exportTemplates" :key="t.id"
                           :label="`${t.name} (.${t.format})`" :value="t.id" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>

        <el-divider content-position="left" class="!my-3">治理（可选）</el-divider>
        <el-row :gutter="12">
          <el-col :span="8">
            <el-form-item label="归档时间窗（留空=全天）">
              <el-input v-model="editing.active_window" placeholder="如 22:00-06:00" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="带宽限速 KB/s（留空=不限）">
              <el-input-number v-model="editing.bandwidth_limit_kbps" :min="0" :max="1048576"
                               class="w-full" placeholder="不限" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="归档后删本地源">
              <el-switch v-model="editing.delete_source_after" />
            </el-form-item>
          </el-col>
        </el-row>
        <div v-if="editing.delete_source_after" class="text-xs text-orange-400 -mt-1 mb-2">
          ⚠️ 归档成功并校验字节数一致后删除本地录像 — 数据中心将无法回放该周期录像（归档即分层，本地只做热存）。
        </div>

        <el-form-item label="备注">
          <el-input v-model="editing.description" type="textarea" :rows="2" maxlength="500" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button size="small" @click="editorVisible = false">取消</el-button>
        <el-button size="small" type="primary" :loading="saving" @click="onSave">保存</el-button>
      </template>
    </el-dialog>

    <!-- ========== 手动证据包 ========== -->
    <el-dialog
      v-model="packVisible"
      title="导出证据包 (zip)"
      width="420px"
      append-to-body
      :close-on-click-modal="false"
      class="dark-dialog"
    >
      <el-form label-position="top" size="small">
        <el-form-item label="日期" required>
          <el-date-picker v-model="packForm.date" type="date" value-format="YYYY-MM-DD" class="w-full" />
        </el-form-item>
        <el-form-item label="只打包 NG 周期">
          <el-switch v-model="packForm.ng_only" />
        </el-form-item>
        <div class="text-xs text-gray-500">
          zip 内每周期一个文件夹：录像 + NG 关键帧（如有）+ 周期元数据 JSON。单次最多 200 个周期。
        </div>
      </el-form>
      <template #footer>
        <el-button size="small" @click="packVisible = false">取消</el-button>
        <el-button size="small" type="primary" :loading="packing" @click="onDownloadPack">打包下载</el-button>
      </template>
    </el-dialog>
  </el-dialog>
</template>

<script setup>
import { computed, reactive, ref } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { Plus, Refresh } from '@element-plus/icons-vue';
import {
  listArchiveRules, createArchiveRule, updateArchiveRule, deleteArchiveRule,
  toggleArchiveRule, testRunArchive, getArchiveStatus, listArchiveLogs,
  getAdapterTypes, backfillArchive, downloadEvidencePack,
} from '@/api/videoArchive';
import { listExportTemplates } from '@/api/export';

const props = defineProps({ modelValue: Boolean });
const emit = defineEmits(['update:modelValue']);
const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
});

const activeTab = ref('rules');
const rules = ref([]);
const status = ref({});
const logs = ref([]);
const logTotal = ref(0);
const logPage = ref(1);
const logFilter = reactive({ status: null, limit: 20 });
const saving = ref(false);
const testRunning = ref(false);

const editorVisible = ref(false);
const editing = reactive(defaultRule());
const adapterTypes = ref(['local_dir']);
const exportTemplates = ref([]);
const pluginCfgText = ref('');

const packVisible = ref(false);
const packing = ref(false);
const packForm = reactive({ date: null, ng_only: true });

function defaultRule() {
  return {
    id: null,
    name: '',
    enabled: true,
    description: '',
    result_filter: 'ng_only',
    channel_filter: null,
    project_filter: null,
    dest_dir: '',
    subdir_by_date: true,
    filename_template: "{{ workpiece.serial_no | default(cycle.id, true) }}_{{ 'OK' if cycle.is_good else 'NG' }}.mp4",
    overwrite_policy: 'rename',
    // 二期: 证据能力
    attach_keyframe: false,
    keyframe_watermark: true,
    sidecar_template_id: null,
    bundle_zip: false,
    transform: 'none',
    clip_seconds: 10,
    // 四期: 远端与治理
    dest_type: 'local_dir',
    dest_config: {},
    active_window: '',
    bandwidth_limit_kbps: null,
    delete_source_after: false,
  };
}

// 远端 adapter 的动态配置字段清单 (sensitive 字段回显 ******，不改=保持原值)
const DEST_CONFIG_FIELDS = {
  ftp: [
    { key: 'host', label: 'FTP 服务器', placeholder: '192.168.1.100' },
    { key: 'port', label: '端口', placeholder: '21' },
    { key: 'user', label: '用户名' },
    { key: 'password', label: '密码', sensitive: true },
    { key: 'remote_dir', label: '远端目录', placeholder: '/quality/videos', span: 16 },
  ],
  sftp: [
    { key: 'host', label: 'SFTP 服务器', placeholder: '192.168.1.100' },
    { key: 'port', label: '端口', placeholder: '22' },
    { key: 'user', label: '用户名' },
    { key: 'password', label: '密码', sensitive: true },
    { key: 'remote_dir', label: '远端目录', placeholder: '/quality/videos', span: 16 },
  ],
  s3: [
    { key: 'endpoint_url', label: 'Endpoint（MinIO 填，AWS 留空）', placeholder: 'http://192.168.1.100:9000', span: 16 },
    { key: 'region', label: 'Region（可空）' },
    { key: 'bucket', label: 'Bucket' },
    { key: 'prefix', label: 'Key 前缀（可空）', placeholder: 'quality/videos' },
    { key: 'access_key', label: 'Access Key' },
    { key: 'secret_key', label: 'Secret Key', sensitive: true },
  ],
  http: [
    { key: 'url', label: '上传 URL（multipart POST）', placeholder: 'http://mes.local/api/upload', span: 16 },
    { key: 'field_name', label: '文件字段名（默认 file）' },
    { key: 'token', label: 'Bearer Token（可空）', sensitive: true },
  ],
};

const destConfigFields = computed(() => DEST_CONFIG_FIELDS[editing.dest_type] || []);

function destTypeLabel(t) {
  const m = {
    local_dir: '本地/网络盘目录',
    ftp: 'FTP',
    sftp: 'SFTP',
    s3: 'S3 / MinIO',
    http: 'HTTP 上传',
  };
  return m[t] || (t.startsWith('plugin:') ? `插件: ${t.slice(7)}` : t);
}

function onOpen() {
  loadRules();
  loadStatus();
  loadLogs();
  loadAdapterTypes();
  loadTemplates();
}

async function loadAdapterTypes() {
  try {
    const res = await getAdapterTypes();
    adapterTypes.value = res.data?.types || ['local_dir'];
  } catch (e) { adapterTypes.value = ['local_dir', 'ftp', 'sftp', 's3', 'http']; }
}

async function loadTemplates() {
  try {
    const res = await listExportTemplates();
    exportTemplates.value = res.data?.items || res.data || [];
  } catch (e) { /* sidecar 下拉失败静默, 不挡主流程 */ }
}

async function loadRules() {
  try {
    const res = await listArchiveRules();
    rules.value = res.data?.items || [];
  } catch (e) {
    ElMessage.error('加载归档规则失败');
  }
}

async function loadStatus() {
  try {
    const res = await getArchiveStatus();
    status.value = res.data || {};
  } catch (e) { /* 状态条失败静默 */ }
}

async function loadLogs() {
  try {
    const res = await listArchiveLogs({
      status: logFilter.status || undefined,
      limit: logFilter.limit,
      offset: (logPage.value - 1) * logFilter.limit,
    });
    logs.value = res.data?.items || [];
    logTotal.value = res.data?.total || 0;
  } catch (e) {
    ElMessage.error('加载归档台账失败');
  }
}

function onLogPage(p) {
  logPage.value = p;
  loadLogs();
}

function onCreate() {
  Object.assign(editing, defaultRule());
  pluginCfgText.value = '';
  editorVisible.value = true;
}

function onEdit(row) {
  Object.assign(editing, defaultRule(), row);
  // dest_config 可能是 null (老规则) — 表单要求 object
  editing.dest_config = { ...(row.dest_config || {}) };
  editing.active_window = row.active_window || '';
  pluginCfgText.value = editing.dest_type?.startsWith('plugin:')
    ? JSON.stringify(editing.dest_config, null, 2) : '';
  editorVisible.value = true;
}

function onChannelFilterInput(val) {
  const arr = (val || '').split(',').map(s => s.trim()).filter(Boolean).map(Number).filter(n => !isNaN(n));
  editing.channel_filter = arr.length ? arr : null;
}
function onProjectFilterInput(val) {
  const arr = (val || '').split(',').map(s => s.trim()).filter(Boolean).map(Number).filter(n => !isNaN(n));
  editing.project_filter = arr.length ? arr : null;
}

async function onSave() {
  if (!editing.name?.trim()) return ElMessage.warning('请填写规则名称');
  const isLocal = editing.dest_type === 'local_dir';
  if (isLocal && !editing.dest_dir?.trim()) return ElMessage.warning('请填写目标目录');
  if (!editing.filename_template?.trim()) return ElMessage.warning('请填写文件名模板');
  // 组 dest_config: 插件类型解析 JSON 文本, 其余按动态字段收集
  let destConfig = null;
  if (editing.dest_type.startsWith('plugin:')) {
    try {
      destConfig = pluginCfgText.value?.trim() ? JSON.parse(pluginCfgText.value) : {};
    } catch (e) { return ElMessage.warning('插件 adapter 配置不是合法 JSON'); }
  } else if (!isLocal) {
    destConfig = {};
    for (const f of destConfigFields.value) {
      const v = editing.dest_config?.[f.key];
      if (v !== undefined && v !== null && String(v) !== '') destConfig[f.key] = v;
    }
    if (!Object.keys(destConfig).length) return ElMessage.warning('请填写目的地连接配置');
  }
  // 远端类型 dest_dir 只是展示标签, 留空自动生成
  const destDir = isLocal
    ? editing.dest_dir.trim()
    : (editing.dest_dir?.trim() || `${editing.dest_type}://${destConfig?.host || destConfig?.url || destConfig?.bucket || '远端'}`);
  saving.value = true;
  try {
    const payload = {
      name: editing.name.trim(),
      enabled: editing.enabled,
      description: editing.description || null,
      result_filter: editing.result_filter,
      channel_filter: editing.channel_filter,
      project_filter: editing.project_filter,
      dest_dir: destDir,
      subdir_by_date: editing.subdir_by_date,
      filename_template: editing.filename_template.trim(),
      overwrite_policy: editing.overwrite_policy,
      // 二期: 证据能力
      attach_keyframe: editing.attach_keyframe,
      keyframe_watermark: editing.keyframe_watermark,
      sidecar_template_id: editing.sidecar_template_id || null,
      bundle_zip: editing.bundle_zip,
      transform: editing.transform,
      clip_seconds: editing.clip_seconds || 10,
      // 四期: 远端与治理
      dest_type: editing.dest_type,
      dest_config: destConfig,
      active_window: editing.active_window?.trim() || null,
      bandwidth_limit_kbps: editing.bandwidth_limit_kbps || null,
      delete_source_after: editing.delete_source_after,
    };
    if (editing.id) {
      await updateArchiveRule(editing.id, payload);
      ElMessage.success('规则已更新');
    } else {
      await createArchiveRule(payload);
      ElMessage.success('规则已创建');
    }
    editorVisible.value = false;
    loadRules();
    loadStatus();
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '保存失败');
  } finally {
    saving.value = false;
  }
}

async function onToggle(row) {
  try {
    await toggleArchiveRule(row.id);
    loadRules();
    loadStatus();
  } catch (e) {
    ElMessage.error('切换失败');
  }
}

async function onDelete(row) {
  try {
    await ElMessageBox.confirm(`确定删除规则「${row.name}」？归档台账保留。`, '删除确认', { type: 'warning' });
  } catch { return; }
  try {
    await deleteArchiveRule(row.id);
    ElMessage.success('已删除');
    loadRules();
    loadStatus();
  } catch (e) {
    ElMessage.error('删除失败');
  }
}

async function onTestRun(row = null) {
  testRunning.value = true;
  try {
    const res = await testRunArchive(row ? { rule_id: row.id } : {});
    const results = res.data?.results || [];
    if (!results.length) {
      ElMessage.info('没有命中任何规则');
    } else {
      const ok = results.filter(r => r.status === 'success').length;
      const bad = results.filter(r => r.status === 'failed');
      if (bad.length) {
        ElMessage.error(`试归档：${ok} 成功，${bad.length} 失败 — ${bad[0].error || ''}`);
      } else {
        ElMessage.success(`试归档成功（周期 #${res.data.cycle_id}，${ok} 条规则），去目标目录看文件`);
      }
    }
    loadRules();
    loadStatus();
    loadLogs();
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '试归档失败');
  } finally {
    testRunning.value = false;
  }
}

async function onBackfill(row) {
  try {
    await ElMessageBox.confirm(
      `按规则「${row.name}」回补归档存量录像（最近 500 条带录像的周期，按规则筛选后异步执行）？\n` +
      '提示：重名策略为"追加序号"时重复回补会产生 _1 副本，建议目标端用"跳过"或"覆盖"。',
      '历史回补', { type: 'warning' },
    );
  } catch { return; }
  try {
    const res = await backfillArchive({ rule_id: row.id, limit: 500 });
    ElMessage.success(`已入队 ${res.data?.enqueued ?? 0} 条回补任务，后台执行中（进度看状态条/台账）`);
    loadStatus();
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '回补失败');
  }
}

async function onDownloadPack() {
  if (!packForm.date) return ElMessage.warning('请选择日期');
  packing.value = true;
  try {
    const res = await downloadEvidencePack({ date: packForm.date, ng_only: packForm.ng_only });
    const url = URL.createObjectURL(new Blob([res.data], { type: 'application/zip' }));
    const a = document.createElement('a');
    a.href = url;
    a.download = `evidence_pack_${packForm.date}${packForm.ng_only ? '_NG' : ''}.zip`;
    a.click();
    URL.revokeObjectURL(url);
    packVisible.value = false;
    ElMessage.success('证据包已下载');
  } catch (e) {
    // blob 响应的错误体也是 blob, 读出 detail
    let msg = '打包失败';
    try {
      const text = await e?.response?.data?.text?.();
      msg = JSON.parse(text)?.detail || msg;
    } catch { /* 保持默认文案 */ }
    ElMessage.error(msg);
  } finally {
    packing.value = false;
  }
}

function resultLabel(v) {
  return { ng_only: '仅 NG', ok_only: '仅 OK', all: '全部' }[v] || v;
}
function resultTagType(v) {
  return { ng_only: 'danger', ok_only: 'success', all: 'info' }[v] || 'info';
}
function policyLabel(v) {
  return { rename: '加序号', overwrite: '覆盖', skip: '跳过' }[v] || v;
}
function formatTime(t) {
  if (!t) return '';
  try {
    return new Date(t).toLocaleString('zh-CN', { hour12: false });
  } catch { return t; }
}
function formatBytes(n) {
  if (n == null) return '-';
  if (n >= 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + ' MB';
  if (n >= 1024) return (n / 1024).toFixed(0) + ' KB';
  return n + ' B';
}
</script>

<style scoped>
.va-dialog :deep(.el-dialog__body) {
  padding-top: 8px;
}
.va-status-bar code,
.va-dialog code {
  background: rgba(148, 163, 184, 0.15);
  padding: 0 4px;
  border-radius: 3px;
  font-size: 11px;
}
</style>
