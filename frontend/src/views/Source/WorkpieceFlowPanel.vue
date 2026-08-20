<template>
  <div class="workpiece-flow-panel p-4">
    <div class="header flex justify-between items-center mb-4">
      <h2 class="text-lg text-white font-bold">流水线串行</h2>
      <el-button type="primary" size="small" @click="openCreate">新建流水线</el-button>
    </div>

    <div class="text-gray-400 text-xs mb-3">
      串行流水线: 同一工件依次走过 N 个工位摄像头, 全部 OK 才算合格.
      与工位组 (并行联动) 互斥, 同一通道不能同时属于两者.
    </div>

    <el-table :data="flows" stripe size="small" empty-text="尚未创建任何流水线" class="w-full">
      <el-table-column prop="id" label="ID" width="60" />
      <el-table-column prop="name" label="名称" min-width="120" />
      <el-table-column label="工位顺序" min-width="140">
        <template #default="{ row }">
          <span class="font-mono text-cyan-400">[{{ row.station_channel_ids.join(' → ') }}]</span>
        </template>
      </el-table-column>
      <el-table-column label="触发模式" width="120">
        <template #default="{ row }">
          <el-tag size="small" :type="triggerModeColor(row.trigger_mode)">
            {{ triggerModeLabel(row.trigger_mode) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="短路" width="80">
        <template #default="{ row }">
          <el-tag size="small" :type="row.short_circuit_on_ng ? 'success' : 'info'">
            {{ row.short_circuit_on_ng ? '开' : '关' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="启用" width="80">
        <template #default="{ row }">
          <el-switch
            :model-value="row.enabled"
            @change="toggleEnabled(row, $event)"
          />
        </template>
      </el-table-column>
      <el-table-column label="操作" width="180" align="center">
        <template #default="{ row }">
          <el-button size="small" @click="openEdit(row)">编辑</el-button>
          <el-button size="small" type="info" @click="viewState(row)">查看</el-button>
          <el-button size="small" type="danger" :disabled="row.enabled" @click="del(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 创建/编辑对话框 -->
    <el-dialog
      v-model="dialogVisible"
      :title="form.id ? '编辑流水线' : '新建流水线'"
      width="640px"
      :close-on-click-modal="false"
    >
      <el-form :model="form" label-width="140px" size="small">
        <el-form-item label="名称">
          <el-input v-model="form.name" placeholder="line-A" :disabled="!!form.id" />
        </el-form-item>

        <el-form-item label="工位顺序">
          <el-input v-model="stationCsv" placeholder="0,1,2" />
          <div class="text-xs text-gray-400 mt-1">
            按工件流动方向顺序填 channel id (逗号分隔, 至少 2 个).
          </div>
        </el-form-item>

        <el-form-item label="触发模式">
          <el-select v-model="form.trigger_mode" class="w-full">
            <el-option label="时间窗 FIFO (无扫码)" value="time_window" />
            <el-option label="扫码绑定" value="scan" />
            <el-option label="物理 GPIO (M7 实现)" value="physical" />
          </el-select>
          <div class="text-xs text-gray-400 mt-1" v-if="form.trigger_mode === 'time_window'">
            入口工位 cycle_start 即视为新工件进入. 节拍稳定才可用, 强烈推荐升级硬件加扫码器.
          </div>
        </el-form-item>

        <!-- scan 模式专属 -->
        <template v-if="form.trigger_mode === 'scan'">
          <el-form-item label="扫码器 ID">
            <el-input-number v-model="form.scan_device_id" :min="0" />
          </el-form-item>
          <el-form-item label="扫码策略">
            <el-radio-group v-model="form.scan_bind_strategy">
              <el-radio value="entry">仅入口扫一次 (broadcast)</el-radio>
              <el-radio value="each_station">每工位扫一次</el-radio>
            </el-radio-group>
          </el-form-item>
        </template>

        <!-- time_window 模式专属 -->
        <template v-if="form.trigger_mode === 'time_window'">
          <el-form-item label="FIFO 上限">
            <el-input-number v-model="form.fifo_max_in_flight" :min="1" :max="20" />
            <span class="text-xs text-gray-400 ml-2">同时追的最大 in-flight 工件数</span>
          </el-form-item>
          <el-form-item label="工位间窗口 (ms)">
            <el-input-number v-model="form.cycle_to_cycle_window_ms" :min="100" :step="1000" />
            <span class="text-xs text-gray-400 ml-2">预期 cycle 间间隔, 仅记录用</span>
          </el-form-item>
        </template>

        <!-- physical 模式专属 -->
        <template v-if="form.trigger_mode === 'physical'">
          <el-form-item label="GPIO 配置 (JSON)">
            <el-input
              type="textarea"
              v-model="physicalConfigJson"
              placeholder='{"device_id": 1, "entry_signal": "io_in_1"}'
              :rows="3"
            />
          </el-form-item>
        </template>

        <el-form-item label="短路结算">
          <el-switch v-model="form.short_circuit_on_ng" />
          <span class="text-xs text-gray-400 ml-2">任一工位 NG 立即停后续 (节省资源)</span>
        </el-form-item>

        <el-form-item label="工件超时 (ms)">
          <el-input-number v-model="form.workpiece_timeout_ms" :min="100" :step="5000" />
        </el-form-item>

        <el-form-item label="超时动作">
          <el-select v-model="form.timeout_action">
            <el-option label="强制 NG" value="force_ng" />
            <el-option label="丢弃 (不推 MES)" value="drop" />
            <el-option label="仅报警" value="alarm_only" />
          </el-select>
        </el-form-item>

        <el-form-item label="启用">
          <el-switch v-model="form.enabled" />
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="submit">保存</el-button>
      </template>
    </el-dialog>

    <!-- 运行时状态对话框 -->
    <el-dialog v-model="stateDialogVisible" :title="`运行时状态: ${stateData?.flow?.name || ''}`" width="720px">
      <div v-if="stateData">
        <div class="mb-2 text-gray-300">
          in-flight 工件数: <span class="text-cyan-400 font-bold">{{ stateData.in_flight_count }}</span>
        </div>
        <el-table :data="stateData.in_flight" size="small" empty-text="当前无 in-flight 工件">
          <el-table-column prop="serial_no" label="序列号" />
          <el-table-column prop="current_station_index" label="当前工位 idx" width="120" />
          <el-table-column label="工位 cycle">
            <template #default="{ row }">
              <span class="font-mono text-xs">{{ JSON.stringify(row.station_cycle_ids) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="工位结果">
            <template #default="{ row }">
              <span class="font-mono text-xs">{{ JSON.stringify(row.station_results) }}</span>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import {
  listFlows,
  createFlow,
  updateFlow,
  deleteFlow,
  getFlowState,
} from '@/api/workpiece_flow';

const flows = ref([]);
const dialogVisible = ref(false);
const stateDialogVisible = ref(false);
const stateData = ref(null);

const _newForm = () => ({
  id: null,
  name: '',
  station_channel_ids: [],
  trigger_mode: 'time_window',
  scan_device_id: null,
  scan_bind_strategy: 'entry',
  fifo_max_in_flight: 3,
  cycle_to_cycle_window_ms: 15000,
  physical_trigger_config: null,
  settle_strategy: 'all_ok_required',
  short_circuit_on_ng: true,
  workpiece_timeout_ms: 60000,
  timeout_action: 'force_ng',
  enabled: false,
});

const form = reactive(_newForm());
const stationCsv = ref('');
const physicalConfigJson = ref('');

const triggerModeLabel = (m) => ({
  time_window: '时间窗',
  scan: '扫码',
  physical: '物理',
}[m] || m);

const triggerModeColor = (m) => ({
  time_window: '',
  scan: 'success',
  physical: 'warning',
}[m] || '');

const loadList = async () => {
  try {
    const { data } = await listFlows();
    flows.value = data.items || [];
  } catch (e) {
    ElMessage.error('加载流水线列表失败: ' + (e?.response?.data?.detail || e.message));
  }
};

const openCreate = () => {
  Object.assign(form, _newForm());
  stationCsv.value = '';
  physicalConfigJson.value = '';
  dialogVisible.value = true;
};

const openEdit = (row) => {
  Object.assign(form, row);
  stationCsv.value = (row.station_channel_ids || []).join(',');
  physicalConfigJson.value = row.physical_trigger_config
    ? JSON.stringify(row.physical_trigger_config, null, 2)
    : '';
  dialogVisible.value = true;
};

const submit = async () => {
  // 解析 stationCsv
  const stations = stationCsv.value
    .split(',')
    .map(s => s.trim())
    .filter(Boolean)
    .map(s => parseInt(s, 10))
    .filter(n => !Number.isNaN(n));
  if (stations.length < 2) {
    ElMessage.error('至少需要 2 个工位');
    return;
  }
  form.station_channel_ids = stations;

  // 解析 physical config JSON
  if (form.trigger_mode === 'physical' && physicalConfigJson.value.trim()) {
    try {
      form.physical_trigger_config = JSON.parse(physicalConfigJson.value);
    } catch (e) {
      ElMessage.error('物理配置 JSON 解析失败: ' + e.message);
      return;
    }
  }

  try {
    if (form.id) {
      await updateFlow(form.id, form);
      ElMessage.success('流水线已更新');
    } else {
      const { id, ...payload } = form;
      await createFlow(payload);
      ElMessage.success('流水线已创建');
    }
    dialogVisible.value = false;
    loadList();
  } catch (e) {
    ElMessage.error('保存失败: ' + (e?.response?.data?.detail || e.message));
  }
};

const toggleEnabled = async (row, val) => {
  try {
    await updateFlow(row.id, { enabled: val });
    ElMessage.success(val ? '已启用' : '已禁用');
    loadList();
  } catch (e) {
    ElMessage.error('切换失败: ' + (e?.response?.data?.detail || e.message));
  }
};

const del = async (row) => {
  try {
    await ElMessageBox.confirm(`确认删除流水线 "${row.name}"?`, '提示', { type: 'warning' });
    await deleteFlow(row.id);
    ElMessage.success('已删除');
    loadList();
  } catch (e) {
    if (e !== 'cancel') {
      ElMessage.error('删除失败: ' + (e?.response?.data?.detail || e.message));
    }
  }
};

const viewState = async (row) => {
  try {
    const { data } = await getFlowState(row.id);
    stateData.value = data;
    stateDialogVisible.value = true;
  } catch (e) {
    ElMessage.error('查询状态失败: ' + (e?.response?.data?.detail || e.message));
  }
};

onMounted(loadList);
</script>

<style scoped>
.workpiece-flow-panel {
  color: white;
}
</style>
