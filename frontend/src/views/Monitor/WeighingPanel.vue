<template>
  <div class="bg-slate-900 border border-slate-700 rounded-lg overflow-hidden flex flex-col">
    <!-- 顶栏: 工位 / 相位 / 实时重量 -->
    <div class="bg-slate-800 px-3 py-2 border-b border-slate-700 flex items-center justify-between flex-shrink-0">
      <span class="text-cyan-400 text-lg font-bold">称重投料 · {{ state?.name || ('工位' + (channel + 1)) }}</span>
      <div class="flex items-center gap-4">
        <span class="px-2 py-0.5 rounded text-sm font-bold" :class="phaseClass">{{ phaseText }}</span>
        <!-- v3.35.1 皮重: 去皮那一刻的工件/容器自重 -->
        <span class="text-gray-400 text-sm">皮重(工件)</span>
        <span class="text-xl font-mono font-bold" :class="tareWeight != null ? 'text-amber-300' : 'text-gray-600'">
          {{ tareWeight != null ? tareWeight.toFixed(3) : '--.---' }} <span class="text-sm text-gray-400">kg</span>
        </span>
        <span class="text-gray-400 text-sm">{{ phase === 'filling' ? '净重(已投料)' : '实时重量' }}</span>
        <span class="text-2xl font-mono font-bold" :class="liveWeight != null ? 'text-white' : 'text-gray-600'">
          {{ liveWeight != null ? liveWeight.toFixed(3) : '--.---' }} <span class="text-sm text-gray-400">kg</span>
        </span>
      </div>
    </div>

    <div v-if="!state" class="p-6 text-center text-gray-500 text-sm">
      本通道未启用称重模式，或后端未就绪。
    </div>

    <template v-else>
      <!-- 前置选择: 人员 / 型号 / 序列号 + 开始 -->
      <div class="px-3 py-2 border-b border-slate-700 flex flex-wrap items-center gap-2 text-sm">
        <span class="text-gray-400">人员</span>
        <el-input v-model="opInput" size="small" style="width: 120px" placeholder="操作人员" />
        <span class="text-gray-400 ml-2">型号</span>
        <el-select v-model="modelInput" size="small" style="width: 150px" placeholder="选择水泥型号"
                   :disabled="state.material_check === 'visual'">
          <el-option v-for="m in (state.available_models || [])" :key="m" :label="m" :value="m" />
        </el-select>
        <el-button size="small" @click="applyContext" :disabled="phase === 'filling' || phase === 'await_tare'">确定人员/型号</el-button>
        <span class="text-gray-500 text-xs ml-1" v-if="state.material_check === 'visual'">(视觉识别料别，型号由模型自动判定)</span>

        <div class="flex-1"></div>

        <el-input v-model="snInput" size="small" style="width: 140px" placeholder="产品序列号" @keyup.enter="doScan" />
        <el-button size="small" type="primary" @click="doScan">扫码开始</el-button>
        <el-button size="small" @click="doReset">复位</el-button>
      </div>

      <!-- 当前作业上下文 -->
      <div class="px-3 py-1.5 border-b border-slate-700 flex items-center gap-4 text-xs text-gray-300">
        <span>人员: <b class="text-white">{{ state.operator || '未选' }}</b></span>
        <span>型号: <b class="text-white">{{ state.model_name || '未选' }}</b></span>
        <span>当前件: <b class="text-white">{{ state.product_sn || '—' }}</b></span>
        <span v-if="state.visual_label">视觉料别: <b class="text-amber-400">{{ state.visual_label }}</b></span>
      </div>

      <!-- 料别投料进度看板 -->
      <div class="p-3 space-y-2 overflow-y-auto" style="max-height: 240px">
        <div v-if="!(state.materials || []).length" class="text-gray-500 text-sm text-center py-3">
          该型号未配料别，请到项目「称重配置」页设置。
        </div>
        <div v-for="(mat, idx) in (state.materials || [])" :key="mat"
             class="rounded border px-3 py-2 flex items-center justify-between"
             :class="rowClass(idx)">
          <div class="flex items-center gap-3">
            <span class="w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold"
                  :class="badgeClass(idx)">{{ idx + 1 }}</span>
            <span class="font-bold text-white">{{ mat }}</span>
            <span class="text-xs text-gray-400">标准 {{ specStd(mat) }}</span>
          </div>
          <div class="flex items-center gap-3">
            <template v-if="resultOf(idx)">
              <span class="font-mono text-white">实投 {{ resultOf(idx).net.toFixed(3) }}kg</span>
              <span class="px-2 py-0.5 rounded text-xs font-bold" :class="verdictClass(resultOf(idx).verdict)">
                {{ verdictText(resultOf(idx).verdict) }}
              </span>
            </template>
            <span v-else-if="idx === state.material_idx" class="text-xs" :class="phaseClass">
              {{ phase === 'await_tare' ? '待去皮' : (phase === 'filling' ? '投料中…' : '等待') }}
            </span>
            <span v-else class="text-xs text-gray-500">未开始</span>
          </div>
        </div>
      </div>

      <!-- 本件结论 + 操作 -->
      <div class="px-3 py-2 border-t border-slate-700 flex items-center justify-between flex-shrink-0">
        <span v-if="phase === 'done'" class="text-sm font-bold"
              :class="itemAllOk ? 'text-green-400' : 'text-red-400'">
          本件{{ itemAllOk ? '合格' : '不合格' }}（{{ (state.results || []).length }} 道料完成）
        </span>
        <span v-else class="text-sm text-gray-400">投料进行中…</span>
        <div class="flex items-center gap-2">
          <el-button size="small" @click="doTare">去皮</el-button>
          <el-button size="small" @click="doZero">置零</el-button>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount, watch } from 'vue';
import { ElMessage } from 'element-plus';
import {
  getWeighingState, setWeighingContext, weighingScan,
  weighingTare, weighingZero, weighingReset
} from '@/api/weighing';

const props = defineProps({
  channel: { type: Number, default: 0 },
});

const state = ref(null);
const opInput = ref('');
const modelInput = ref('');
const snInput = ref('');
let timer = null;

const phase = computed(() => state.value?.phase || 'idle');
const liveWeight = computed(() => {
  const w = state.value?.live_weight;
  return (typeof w === 'number') ? w : null;
});
const tareWeight = computed(() => {
  const w = state.value?.tare_weight;
  return (typeof w === 'number') ? w : null;
});

const phaseText = computed(() => ({
  idle: '待开始', await_tare: '待去皮(放料盆)', filling: '投料中', done: '完成',
}[phase.value] || phase.value));
const phaseClass = computed(() => ({
  idle: 'bg-slate-700 text-gray-300',
  await_tare: 'bg-amber-600/30 text-amber-300',
  filling: 'bg-cyan-600/30 text-cyan-300',
  done: 'bg-green-600/30 text-green-300',
}[phase.value] || 'bg-slate-700 text-gray-300'));

const itemAllOk = computed(() =>
  (state.value?.results || []).every(r => r.verdict === 'ok'));

const resultOf = (idx) => (state.value?.results || [])[idx] || null;
const specStd = (mat) => {
  const s = (state.value?.model_specs || {})[mat];
  return s && s.standard != null ? `${Number(s.standard).toFixed(3)}kg` : '—';
};

const rowClass = (idx) => {
  if (resultOf(idx)) {
    return resultOf(idx).verdict === 'ok'
      ? 'border-green-700/50 bg-green-900/10'
      : 'border-red-700/50 bg-red-900/10';
  }
  if (idx === state.value?.material_idx && phase.value !== 'done') {
    return 'border-cyan-600/60 bg-cyan-900/10';
  }
  return 'border-slate-700 bg-slate-800/40';
};
const badgeClass = (idx) => {
  if (resultOf(idx)) {
    return resultOf(idx).verdict === 'ok' ? 'bg-green-600 text-white' : 'bg-red-600 text-white';
  }
  if (idx === state.value?.material_idx && phase.value !== 'done') return 'bg-cyan-600 text-white';
  return 'bg-slate-600 text-gray-300';
};
const verdictText = (v) => ({ ok: '合格', shortage: '缺料', over: '超量', no_spec: '未配标准' }[v] || v);
const verdictClass = (v) => ({
  ok: 'bg-green-600/30 text-green-300',
  shortage: 'bg-red-600/30 text-red-300',
  over: 'bg-orange-600/30 text-orange-300',
  no_spec: 'bg-slate-600/40 text-gray-300',
}[v] || 'bg-slate-600/40 text-gray-300');

const refresh = async () => {
  try {
    const { data } = await getWeighingState(props.channel);
    state.value = data && data.phase !== undefined ? data : (data || null);
    // 回填未编辑的上下文输入框
    if (state.value) {
      if (!opInput.value && state.value.operator) opInput.value = state.value.operator;
      if (!modelInput.value && state.value.model_name) modelInput.value = state.value.model_name;
    }
  } catch (e) {
    // 通道未启用称重模式时静默 (面板自身只在 weighing 项目下渲染)
  }
};

const applyContext = async () => {
  try {
    await setWeighingContext({ channel_id: props.channel, operator: opInput.value || null, model_name: modelInput.value || null });
    ElMessage.success('已设置人员/型号');
    refresh();
  } catch (e) { ElMessage.error('设置失败'); }
};
const doScan = async () => {
  if (!snInput.value) { ElMessage.warning('请输入产品序列号'); return; }
  try {
    const { data } = await weighingScan({ channel_id: props.channel, serial_no: snInput.value });
    if (data && data.ok === false) { ElMessage.warning(data.message || '开始失败'); }
    else { snInput.value = ''; refresh(); }
  } catch (e) { ElMessage.error('扫码开始失败'); }
};
const doTare = async () => { try { await weighingTare(props.channel); refresh(); } catch (e) { ElMessage.error('去皮失败'); } };
const doZero = async () => { try { await weighingZero(props.channel); refresh(); } catch (e) { ElMessage.error('置零失败'); } };
const doReset = async () => { try { await weighingReset(props.channel); refresh(); } catch (e) { ElMessage.error('复位失败'); } };

watch(() => props.channel, () => { state.value = null; opInput.value = ''; modelInput.value = ''; refresh(); });

onMounted(() => { refresh(); timer = setInterval(refresh, 800); });
onBeforeUnmount(() => { if (timer) clearInterval(timer); });
</script>
