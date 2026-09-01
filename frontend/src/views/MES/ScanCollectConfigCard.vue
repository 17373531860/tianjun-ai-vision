<template>
  <!-- v3.56 周期多码采集配置 (MES管理→扫码器→多码采集)
       配置按项目存 (scan_collect_configs 表): 切作业=切项目, 芯子 6/9 数量随项目自动切换;
       此处集中编辑各项目的规则, 编辑对象用项目选择器指定, 默认当前激活项目。 -->
  <div class="max-w-4xl">
    <div class="text-xs text-gray-500 mb-3 leading-5">
      一个周期内采集多个分类码（如 母排+盖板+芯子×N+工件码收尾），少扫/重扫按策略报 NG，
      检测主页实时显示已扫进度。<b class="text-gray-400">规则按项目保存</b>——不同作业（芯子 6 个 / 9 个）各建一个项目，切项目即切数量。
      txt 分类落盘格式在 <b class="text-gray-400">数据中心 → 自定义导出</b> 里配模板（字段组「多码采集」）。
    </div>

    <!-- 编辑对象: 项目 -->
    <div class="flex items-center gap-3 mb-4">
      <span class="text-sm text-gray-400">配置项目</span>
      <el-select v-model="projectId" filterable class="w-64" data-testid="sc-project-select"
                 @change="loadConfig">
        <el-option v-for="p in projects" :key="p.id" :value="p.id"
                   :label="p.is_active ? `${p.name}（当前激活）` : p.name" />
      </el-select>
      <el-switch v-model="form.enabled" data-testid="sc-enabled-switch"
                 active-text="启用多码采集" inactive-text="关闭" />
      <el-button type="primary" :loading="saving" :disabled="loading"
                 data-testid="sc-save-btn" @click="save">保存</el-button>
    </div>

    <el-skeleton v-if="loading" :rows="4" animated />
    <template v-else>
      <!-- 码类别（槽位）表 -->
      <div class="flex items-center justify-between mb-2">
        <h3 class="text-cyan-300 font-semibold text-sm">码类别</h3>
        <div class="flex gap-2">
          <el-button size="small" plain @click="fillExample">填入示例（母排+盖板+芯子+工件码）</el-button>
          <el-button size="small" type="success" plain data-testid="sc-add-slot" @click="addSlot">加一类</el-button>
        </div>
      </div>
      <el-table :data="form.slots" size="small" class="mb-1" empty-text="还没有码类别，点「加一类」或「填入示例」">
        <el-table-column label="名称" width="130">
          <template #default="{ row }">
            <el-input v-model="row.label" size="small" placeholder="如 芯子码" />
          </template>
        </el-table-column>
        <el-table-column label="标识 key" width="130">
          <template #default="{ row }">
            <el-input v-model="row.key" size="small" placeholder="英文, 如 chip" />
          </template>
        </el-table-column>
        <el-table-column label="应扫数量" width="110">
          <template #default="{ row }">
            <el-input-number v-model="row.count" size="small" :min="1" :max="99" controls-position="right" class="!w-full" />
          </template>
        </el-table-column>
        <el-table-column label="码值正则（留空=不按码值匹配）" min-width="150">
          <template #default="{ row }">
            <el-input v-model="row.regex" size="small" placeholder="如 ^[A-Z]+\d+$" class="font-mono" />
          </template>
        </el-table-column>
        <el-table-column width="120" align="center">
          <template #header>
            <el-tooltip content="该类码在历史工件里出现过再次扫到怎么办。循环使用的治具码（如工装码）选「豁免」，否则会被跨工件查重误拦" placement="top">
              <span class="border-b border-dotted border-gray-600">历史重复</span>
            </el-tooltip>
          </template>
          <template #default="{ row }">
            <el-select v-model="row.dedup_cross_group" size="small">
              <el-option value="inherit" label="跟随全局" />
              <el-option value="off" label="豁免（治具）" />
              <el-option value="reject" label="拒收提示" />
              <el-option value="ng_alarm" label="判 NG 报警" />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column width="120" align="center">
          <template #header>
            <el-tooltip content="该类码超出应扫数量时怎么办。如：芯子多扫直接判 NG；多扫母排（=忘扫收尾码就开始下一件）只拦截提示" placement="top">
              <span class="border-b border-dotted border-gray-600">多扫策略</span>
            </el-tooltip>
          </template>
          <template #default="{ row }">
            <el-select v-model="row.on_overflow" size="small">
              <el-option value="inherit" label="跟随全局" />
              <el-option value="reject" label="拒收提示" />
              <el-option value="ng_alarm" label="判 NG 报警" />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column label="收尾码" width="80" align="center">
          <template #default="{ row }">
            <el-radio :model-value="closingKey" :value="row.key" @change="setClosing(row.key)"><span /></el-radio>
          </template>
        </el-table-column>
        <el-table-column width="60" align="center">
          <template #default="{ $index }">
            <el-button size="small" type="danger" text @click="form.slots.splice($index, 1)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div class="text-xs text-gray-600 mb-4">
        分类顺序：先按正则匹配码值；没配正则或没匹配中时，按「扫码顺序兜底」把码依表内顺序归入未扫满的类别。
        收尾码类别（如工件码）扫到即触发结算。
      </div>

      <!-- 判定与策略 -->
      <h3 class="text-cyan-300 font-semibold text-sm mb-2">判定策略</h3>
      <div class="grid grid-cols-2 gap-x-8 gap-y-3 mb-4">
        <div class="flex items-center justify-between">
          <span class="text-sm text-gray-400">正则不中时按扫码顺序归类</span>
          <el-switch v-model="form.sequence_fallback" />
        </div>
        <div class="flex items-center justify-between">
          <span class="text-sm text-gray-400">结算时机</span>
          <el-select v-model="form.settle_on" class="w-44" size="small">
            <el-option value="closing" label="扫收尾码时结算" />
            <el-option value="all_filled" label="数量凑齐即结算" />
          </el-select>
        </div>
        <div class="flex items-center justify-between">
          <span class="text-sm text-gray-400">本组内重复扫同一码</span>
          <el-select v-model="form.dedup_in_group" class="w-44" size="small">
            <el-option value="ng_alarm" label="判 NG 报警" />
            <el-option value="reject" label="拒收并提示" />
          </el-select>
        </div>
        <div class="flex items-center justify-between">
          <span class="text-sm text-gray-400">历史组已用过的码再次出现</span>
          <el-select v-model="form.dedup_cross_group" class="w-44" size="small">
            <el-option value="off" label="不拦截" />
            <el-option value="reject" label="拒收并提示" />
            <el-option value="ng_alarm" label="判 NG 报警" />
          </el-select>
        </div>
        <div class="flex items-center justify-between">
          <span class="text-sm text-gray-400">超出应扫数量（多扫）</span>
          <el-select v-model="form.on_overflow" class="w-44" size="small">
            <el-option value="reject" label="拒收并提示" />
            <el-option value="ng_alarm" label="判 NG 报警" />
          </el-select>
        </div>
        <div class="flex items-center justify-between">
          <span class="text-sm text-gray-400">不属于任何类别的码</span>
          <el-select v-model="form.on_unmatched" class="w-44" size="small">
            <el-option value="reject" label="拒收并提示" />
            <el-option value="ng_alarm" label="判 NG 报警" />
          </el-select>
        </div>
        <div class="flex items-center justify-between">
          <el-tooltip content="从扫第一个码开始计时，超时未扫齐判 NG。0 = 不限时" placement="top">
            <span class="text-sm text-gray-400 border-b border-dotted border-gray-600">采集超时（秒）</span>
          </el-tooltip>
          <el-input-number v-model="form.timeout_sec" :min="0" :max="86400" size="small" controls-position="right" class="!w-44" />
        </div>
        <div class="flex items-center justify-between">
          <el-tooltip content="结算 OK / NG 时触发的事件 ID（对应项目事件设置，默认 1=OK / 2=NG，联动灯塔/语音/计数）" placement="top">
            <span class="text-sm text-gray-400 border-b border-dotted border-gray-600">OK / NG 事件 ID</span>
          </el-tooltip>
          <div class="flex items-center gap-2">
            <el-input-number v-model="form.event_ok_id" :min="0" :max="99" size="small" controls-position="right" class="!w-20" />
            <el-input-number v-model="form.event_ng_id" :min="0" :max="99" size="small" controls-position="right" class="!w-20" />
          </div>
        </div>
        <div class="flex items-center justify-between">
          <el-tooltip content="开启后：少扫就收尾不立即出 NG 结果，先报警并挂起本工件——操作员补扫缺码自动转 OK，或在监控页点「按NG放行」出 NG 结果；挂起期间扫新工件的码会被拦住。关闭=收尾立即判 NG（默认）" placement="top">
            <span class="text-sm text-gray-400 border-b border-dotted border-gray-600">少扫 NG 挂起补扫</span>
          </el-tooltip>
          <el-switch v-model="form.ng_pending" data-testid="sc-ng-pending" />
        </div>
        <div class="flex items-center justify-between">
          <el-tooltip content="开启后：扫码齐了还要看本工位视觉检测最近一次周期判定，两边都 OK 才 OK（视觉装了 6 个但只扫 5 码→NG）。工件开始/结束以扫码为准。关闭=只按扫码判（默认）" placement="top">
            <span class="text-sm text-gray-400 border-b border-dotted border-gray-600">视觉+扫码双重验证</span>
          </el-tooltip>
          <el-switch v-model="form.vision_gate" data-testid="sc-vision-gate" />
        </div>
        <template v-if="form.vision_gate">
          <div class="flex items-center justify-between">
            <el-tooltip content="只认收尾前 N 秒内产生的视觉周期结果，超窗视为无结果。0 = 不限时" placement="top">
              <span class="text-sm text-gray-400 border-b border-dotted border-gray-600">视觉结果有效窗口（秒）</span>
            </el-tooltip>
            <el-input-number v-model="form.vision_window_sec" :min="0" :max="86400" size="small" controls-position="right" class="!w-44" />
          </div>
          <div class="flex items-center justify-between">
            <span class="text-sm text-gray-400">窗口内无视觉结果时</span>
            <el-select v-model="form.vision_missing" class="w-44" size="small">
              <el-option value="ignore" label="按扫码结果判" />
              <el-option value="ng" label="判 NG" />
            </el-select>
          </div>
        </template>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue';
import { ElMessage } from 'element-plus';
import { getProjects } from '@/api/project';
import { getScanCollectConfig, saveScanCollectConfig } from '@/api/scanCollect';

const projects = ref([]);
const projectId = ref(null);
const loading = ref(false);
const saving = ref(false);

const emptyForm = () => ({
  enabled: false,
  slots: [],
  sequence_fallback: true,
  dedup_in_group: 'ng_alarm',
  dedup_cross_group: 'off',
  settle_on: 'closing',
  on_overflow: 'reject',
  on_unmatched: 'reject',
  timeout_sec: 0,
  event_ok_id: 1,
  event_ng_id: 2,
  ng_pending: false,
  vision_gate: false,
  vision_window_sec: 300,
  vision_missing: 'ignore',
});
const form = ref(emptyForm());

const closingKey = computed(() => form.value.slots.find((s) => s.role === 'closing')?.key ?? '');

function setClosing(key) {
  form.value.slots.forEach((s) => { s.role = s.key === key ? 'closing' : ''; });
}

const newSlot = (extra = {}) => ({
  key: `slot${form.value.slots.length + 1}`, label: '', count: 1, regex: '',
  role: '', dedup_cross_group: 'inherit', on_overflow: 'inherit', ...extra,
});

function addSlot() {
  form.value.slots.push(newSlot());
}

function fillExample() {
  // 焊接组装工位实测规律: 母排 M 前缀长码 / 芯子 13 位纯数字 / 工装 H-C 前缀收尾
  // 工装是循环治具跨工件必然重复 → 历史重复豁免; 芯子多扫直接判 NG
  form.value.slots = [
    { key: 'busbar', label: '母排码', count: 1, regex: '^M.{29,32}$',
      role: '', dedup_cross_group: 'inherit', on_overflow: 'inherit' },
    { key: 'chip', label: '芯子码', count: 6, regex: '^\\d{13}$',
      role: '', dedup_cross_group: 'inherit', on_overflow: 'ng_alarm' },
    { key: 'fixture', label: '工装码', count: 1, regex: '^H-C',
      role: 'closing', dedup_cross_group: 'off', on_overflow: 'inherit' },
  ];
  // 现场确认单预设: 少扫收尾报警后挂起等补扫 (5.3 "判NG后补扫缺码");
  // 视觉+扫码双重验证 (9.3), vision_missing=ignore → 视觉侧没结果时按扫码判,
  // 相机项目没配好前开着也不会误 NG
  form.value.ng_pending = true;
  form.value.vision_gate = true;
  form.value.vision_missing = 'ignore';
  form.value.vision_window_sec = 300;
}

async function loadConfig() {
  if (!projectId.value) return;
  loading.value = true;
  try {
    const { data } = await getScanCollectConfig(projectId.value);
    const base = emptyForm();
    for (const k of Object.keys(base)) {
      if (data[k] !== undefined && data[k] !== null) base[k] = data[k];
    }
    // 老配置槽位可能缺 v3.56.1 新键, 补默认值保证下拉正常绑定
    base.slots = (base.slots || []).map((s) => ({
      dedup_cross_group: 'inherit', on_overflow: 'inherit', ...s,
    }));
    form.value = base;
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '加载多码采集配置失败');
  } finally {
    loading.value = false;
  }
}

async function save() {
  if (!projectId.value) return;
  saving.value = true;
  try {
    await saveScanCollectConfig(projectId.value, form.value);
    ElMessage.success('多码采集配置已保存，切到该项目即生效');
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '保存失败');
  } finally {
    saving.value = false;
  }
}

onMounted(async () => {
  try {
    const { data } = await getProjects();
    projects.value = data?.items || (Array.isArray(data) ? data : []);
    const active = projects.value.find((p) => p.is_active);
    projectId.value = active?.id ?? projects.value[0]?.id ?? null;
    if (projectId.value) await loadConfig();
  } catch {
    ElMessage.error('加载项目列表失败');
  }
});
</script>
