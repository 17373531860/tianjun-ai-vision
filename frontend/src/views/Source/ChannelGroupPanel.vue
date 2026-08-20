<template>
  <div class="channel-group-panel p-4">
    <div class="header flex justify-between items-center mb-4">
      <h2 class="text-lg text-white font-bold">工位组互通配置 (RFC 10 CG)</h2>
      <el-button type="primary" size="small" @click="openCreate">新建工位组</el-button>
    </div>

    <div class="text-gray-400 text-xs mb-3">
      工位组（并行联动）：把多个工位绑成一组，同一周期内按策略联动判定，两个工位作为一个完整检测工序一起结算
      （如 A 站 NG → B 站联动 NG）。与流水线串行互斥，同一通道不能同时属于两者。未创建任何组时结算行为与单工位完全一致。
    </div>

    <el-table :data="groups" stripe size="small" empty-text="尚未创建任何工位组" class="w-full">
      <el-table-column prop="id" label="ID" width="60" />
      <el-table-column prop="name" label="名称" min-width="120" />
      <el-table-column label="成员工位" min-width="120">
        <template #default="{ row }">
          <span class="font-mono text-cyan-400">[{{ row.member_channel_ids.join(', ') }}]</span>
        </template>
      </el-table-column>
      <el-table-column label="联动策略" min-width="150">
        <template #default="{ row }">
          <el-tag size="small" :type="strategyColor(row.settle_strategy)">{{ strategyLabel(row.settle_strategy) }}</el-tag>
          <el-tag v-if="row.unified_ok_report" size="small" class="ml-1" type="warning">统一播报</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="超时(ms)" width="100" prop="timeout_ms" />
      <el-table-column label="超时动作" width="110">
        <template #default="{ row }">
          <span class="text-xs">{{ timeoutActionLabel(row.timeout_action) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="启用" width="80">
        <template #default="{ row }">
          <el-switch :model-value="row.enabled" @change="toggleEnabled(row, $event)" />
        </template>
      </el-table-column>
      <el-table-column label="操作" width="150" align="center">
        <template #default="{ row }">
          <el-button size="small" @click="openEdit(row)">编辑</el-button>
          <el-button size="small" type="danger" :disabled="row.enabled" @click="del(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 创建/编辑对话框 -->
    <el-dialog
      v-model="dialogVisible"
      :title="form.id ? '编辑工位组' : '新建工位组'"
      width="560px"
      :close-on-click-modal="false"
    >
      <el-form :model="form" label-width="120px" size="small">
        <el-form-item label="名称">
          <el-input v-model="form.name" placeholder="组-A" :disabled="!!form.id" />
        </el-form-item>

        <el-form-item label="成员工位">
          <el-input v-model="memberCsv" placeholder="0,1" />
          <div class="text-xs text-gray-400 mt-1">
            填工位（通道）编号，逗号分隔，至少 2 个。双工位填 <span class="font-mono">0,1</span>。
          </div>
        </el-form-item>

        <el-form-item label="联动策略">
          <el-select v-model="form.settle_strategy" class="w-full">
            <el-option label="任一工位 NG 则全组 NG（推荐 · 双工位互通）" value="synchronized_any_ng" />
            <el-option label="全部工位 OK 才算合格" value="synchronized_all_ok" />
            <el-option label="独立（不联动，各判各的）" value="independent" />
          </el-select>
        </el-form-item>

        <el-form-item label="等待超时 (ms)">
          <el-input-number v-model="form.timeout_ms" :min="500" :step="500" />
          <span class="text-xs text-gray-400 ml-2">等其他工位结算的最长时间</span>
        </el-form-item>

        <el-form-item label="超时动作">
          <el-select v-model="form.timeout_action" class="w-full">
            <el-option label="回退独立结算（各工位单独判）" value="fallback_independent" />
            <el-option label="强制判 NG" value="force_ng" />
          </el-select>
        </el-form-item>

        <el-form-item v-if="form.settle_strategy === 'synchronized_all_ok'" label="统一播报">
          <el-switch v-model="form.unified_ok_report" />
          <div class="text-xs text-gray-400 mt-1">
            开启后各工位合格时<b>不单独</b>亮灯/语音/弹合格提示，等组内工位<b>全部合格</b>后统一报一次合格。
            任一工位 NG 仍然立即播报（安全优先）。关闭时各工位照旧各报各的。
          </div>
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
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import {
  listChannelGroups,
  createChannelGroup,
  updateChannelGroup,
  deleteChannelGroup,
} from '@/api/channel_group';

const groups = ref([]);
const dialogVisible = ref(false);

const _newForm = () => ({
  id: null,
  name: '',
  member_channel_ids: [],
  settle_strategy: 'synchronized_any_ng',
  timeout_ms: 5000,
  timeout_action: 'fallback_independent',
  enabled: false,
  unified_ok_report: false,
});

const form = reactive(_newForm());
const memberCsv = ref('');

const strategyLabel = (s) => ({
  synchronized_any_ng: '任一NG则全组NG',
  synchronized_all_ok: '全部OK才合格',
  independent: '独立',
  master_slave: '主从(未实现)',
}[s] || s);

const strategyColor = (s) => ({
  synchronized_any_ng: 'danger',
  synchronized_all_ok: 'success',
  independent: 'info',
}[s] || '');

const timeoutActionLabel = (a) => ({
  fallback_independent: '回退独立',
  force_ng: '强制NG',
}[a] || a);

const loadList = async () => {
  try {
    const { data } = await listChannelGroups();
    groups.value = data.items || [];
  } catch (e) {
    ElMessage.error('加载工位组列表失败: ' + (e?.response?.data?.detail || e.message));
  }
};

const openCreate = () => {
  Object.assign(form, _newForm());
  memberCsv.value = '';
  dialogVisible.value = true;
};

const openEdit = (row) => {
  Object.assign(form, row);
  memberCsv.value = (row.member_channel_ids || []).join(',');
  dialogVisible.value = true;
};

const submit = async () => {
  const members = memberCsv.value
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean)
    .map((s) => parseInt(s, 10))
    .filter((n) => !Number.isNaN(n));
  if (members.length < 2) {
    ElMessage.error('工位组至少需要 2 个成员工位');
    return;
  }
  form.member_channel_ids = members;

  try {
    if (form.id) {
      await updateChannelGroup(form.id, form);
      ElMessage.success('工位组已更新');
    } else {
      const { id, ...payload } = form;
      await createChannelGroup(payload);
      ElMessage.success('工位组已创建');
    }
    dialogVisible.value = false;
    loadList();
  } catch (e) {
    ElMessage.error('保存失败: ' + (e?.response?.data?.detail || e.message));
  }
};

const toggleEnabled = async (row, val) => {
  try {
    await updateChannelGroup(row.id, { enabled: val });
    ElMessage.success(val ? '已启用' : '已禁用');
    loadList();
  } catch (e) {
    ElMessage.error('切换失败: ' + (e?.response?.data?.detail || e.message));
    loadList();
  }
};

const del = async (row) => {
  try {
    await ElMessageBox.confirm(`确认删除工位组 "${row.name}"?`, '提示', { type: 'warning' });
    await deleteChannelGroup(row.id);
    ElMessage.success('已删除');
    loadList();
  } catch (e) {
    if (e !== 'cancel') {
      ElMessage.error('删除失败: ' + (e?.response?.data?.detail || e.message));
    }
  }
};

onMounted(loadList);
</script>

<style scoped>
.channel-group-panel {
  color: white;
}
</style>
