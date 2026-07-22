<template>
  <div class="packaging-card bg-slate-900 border border-slate-700 rounded-lg overflow-hidden">
    <div class="bg-slate-800 px-3 py-1 border-b border-slate-700 flex justify-between items-center">
      <span class="text-cyan-400 text-lg font-bold">包装箱结算 · {{ config.name }}</span>
      <div class="flex items-center gap-2">
        <button
          v-if="canForceSettle"
          @click="onForceSettle"
          :disabled="forcing"
          class="text-xs px-2 py-0.5 rounded bg-rose-700/70 hover:bg-rose-600 disabled:opacity-50
                 text-rose-100 font-bold transition-colors"
          title="管理员/主管手动收尾当前进行中工单 (需填理由)"
        >
          强制结案
        </button>
        <span class="text-xs" :class="statusColor">{{ statusLabel }}</span>
      </div>
    </div>

    <!-- 没有进行中工单 -->
    <div v-if="!state" class="py-6 text-center text-gray-500 text-sm">
      等待扫工单标签开工…
    </div>

    <div v-else class="p-3">
      <!-- 工单 + 箱进度概览 -->
      <div class="flex items-center justify-between mb-3">
        <div>
          <span class="text-gray-400 text-xs">当前工单</span>
          <div class="font-mono text-white text-base">{{ state.order_no || '-' }}</div>
          <div v-if="state.cust_name" class="text-xs text-amber-300 mt-0.5 truncate max-w-[12rem]" :title="state.cust_name">
            客户：{{ state.cust_name }}
          </div>
          <div v-if="state.spec" class="text-xs text-gray-400">
            规格：{{ state.spec }}<span v-if="state.slider_total > 0"> · 滑块总数 {{ state.slider_total }}</span>
          </div>
        </div>
        <div class="text-right">
          <span class="text-gray-400 text-xs">箱进度</span>
          <div class="text-base font-bold">
            <span class="text-cyan-300">{{ state.box_done }}</span>
            <span class="text-gray-500"> / {{ state.box_total > 0 ? state.box_total : '?' }}</span>
            <span v-if="state.box_ng > 0" class="text-red-400 text-xs ml-2">NG {{ state.box_ng }}</span>
          </div>
        </div>
      </div>

      <!-- 当前箱进度 (滑块口径显示滑块数/目标 + 尾箱标记; 否则托盘数/每箱) -->
      <div class="current-box bg-slate-800/60 rounded px-3 py-2 mb-3">
        <div class="flex items-center justify-between">
          <span class="text-gray-300 text-sm">
            正在装第 <span class="text-cyan-400 font-bold">{{ state.current_box_index || '-' }}</span> 箱
            <span v-if="isSliders && isTailBox"
                  class="ml-1 text-xs px-1 rounded bg-amber-600/40 text-amber-300">尾箱</span>
          </span>
          <span v-if="isSliders" class="text-sm">
            滑块
            <span class="text-green-400 font-bold text-lg">{{ state.current_box_sliders }}</span>
            <span class="text-gray-500"> / {{ currentBoxTarget }}</span>
          </span>
          <span v-else class="text-sm">
            托盘
            <span class="text-green-400 font-bold text-lg">{{ state.current_box_trays }}</span>
            <span class="text-gray-500"> / {{ traysPerBox }}</span>
          </span>
        </div>
        <el-progress
          :percentage="boxPercent"
          :stroke-width="8"
          :show-text="false"
          :color="boxPercent >= 100 ? '#22c55e' : '#06b6d4'"
          class="mt-1"
        />
      </div>

      <!-- v3.43 等放工单收尾横幅: 各箱已落账, 只差放工单动作完成工单 -->
      <div v-if="state.status === 'awaiting_paper'"
           class="mb-3 rounded border border-cyan-600/60 bg-cyan-900/30 px-3 py-2">
        <div class="text-cyan-300 text-sm font-bold mb-1">
          ⏳ 各箱已落账，等「放工单」动作完成工单收尾…
        </div>
        <div class="text-xs text-cyan-200/80">
          检测到放工单动作即合格收尾；一直没放，按配置的判定方式（扫新单时判定 / 到时限判定）报警并判 NG 收尾。
        </div>
      </div>

      <!-- v3.44 工单收尾快照横幅: 完成/作废后信息保留展示, 扫新单自然顶掉 -->
      <div v-if="isDoneSnapshot" class="mb-3 rounded border px-3 py-2"
           :class="state.final_result === 'OK'
             ? 'border-green-600/60 bg-green-900/30' : 'border-red-600/60 bg-red-900/30'">
        <div class="text-sm font-bold mb-1"
             :class="state.final_result === 'OK' ? 'text-green-300' : 'text-red-300'">
          {{ state.status === 'aborted' ? '✕ 工单已作废' : '✓ 工单已完成' }}
          — 最终 {{ state.final_result || '-' }}
        </div>
        <div class="text-xs text-gray-300">
          共 {{ state.box_done }} 箱落账<span v-if="state.box_ng > 0">，其中 NG {{ state.box_ng }} 箱</span>。信息保留展示，扫新工单开工后自动切换。
        </div>
      </div>

      <!-- v3.44 NG 箱账挂起横幅: 处置入口在人工确认弹窗, 这里只提示不重复给按钮 -->
      <div v-if="ngAckBox" class="mb-3 rounded border border-rose-600/60 bg-rose-900/30 px-3 py-2">
        <div class="text-rose-300 text-sm font-bold mb-1">
          ⚠ 第 {{ ngAckBox.box }} 箱 NG 箱账挂起（进箱 {{ ngAckBox.sliders }}<template v-if="ngAckBox.target > 0"> / {{ ngAckBox.target }}</template>）
        </div>
        <div class="text-xs text-rose-200/80">
          未落 NG、未翻页 — 请在屏幕中央的人工确认弹窗选择「重做本箱」或「认 NG 落账」。
        </div>
      </div>

      <!-- v3.23 少装挂起等补做横幅 -->
      <div v-if="pendingBox" class="mb-3 rounded border border-amber-600/60 bg-amber-900/30 px-3 py-2">
        <div class="text-amber-300 text-sm font-bold mb-1">
          ⚠ 第 {{ pendingBox.box }} 箱少装：滑块 {{ pendingBox.sliders }} / {{ pendingBox.target }}
        </div>
        <div class="text-xs text-amber-200/80 mb-2">
          人工确认后可「补齐」直接判合格（延迟落账，不重置周期），或「重做」本箱。
        </div>
        <div v-if="canRemediate" class="flex items-center gap-2 flex-wrap">
          <button @click="onSupplement(true)" :disabled="remediating"
            class="text-xs px-2 py-1 rounded bg-green-700/70 hover:bg-green-600 disabled:opacity-50 text-green-100 font-bold transition-colors">
            补齐到 {{ pendingBox.target }}
          </button>
          <button @click="onSupplement(false)" :disabled="remediating"
            class="text-xs px-2 py-1 rounded bg-cyan-700/70 hover:bg-cyan-600 disabled:opacity-50 text-cyan-100 font-bold transition-colors">
            手动输入实际数
          </button>
          <button @click="onRedo" :disabled="remediating"
            class="text-xs px-2 py-1 rounded bg-slate-600/70 hover:bg-slate-500 disabled:opacity-50 text-slate-100 font-bold transition-colors">
            重做本箱
          </button>
        </div>
        <div v-else class="text-xs text-rose-300">无补做权限（需 monitor.detection.ack；操作员请联系管理员授权或提权）</div>
      </div>

      <!-- 各箱明细 -->
      <div v-if="(state.box_details || []).length" class="boxes flex flex-wrap gap-1">
        <span
          v-for="b in state.box_details"
          :key="b.box"
          class="box-chip text-xs px-2 py-0.5 rounded font-mono"
          :class="b.result === 'OK' ? 'bg-green-700/40 text-green-300' : 'bg-red-700/40 text-red-300'"
          :title="isSliders
            ? `第 ${b.box} 箱${b.is_tail ? '(尾箱)' : ''}: ${b.sliders}/${b.target} 滑块 → ${b.result}`
            : `第 ${b.box} 箱: ${b.trays}/${b.need} 托盘 → ${b.result}`"
        >
          <template v-if="isSliders">#{{ b.box }}{{ b.is_tail ? '尾' : '' }} {{ b.sliders }}/{{ b.target }} {{ b.result }}</template>
          <template v-else>#{{ b.box }} {{ b.trays }}/{{ b.need }} {{ b.result }}</template>
        </span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { useAuthStore } from '@/store/useAuthStore';
import { forcePackagingSettle, supplementPackagingSliders, redoPackagingBox } from '@/api/packaging_flow';

const props = defineProps({
  config: { type: Object, required: true },
  state: { type: Object, default: null },
});

const authStore = useAuthStore();
const forcing = ref(false);
const remediating = ref(false);

// v3.23 少装挂起箱 + 补做权限 (鉴权关时人人=超管, 始终可补)
// v3.44 起同一状态位还承载 NG 箱账挂起 (reason=ng_ack, 处置走确认弹窗) — 按 reason 分流
const pendingBox = computed(() =>
  props.state && props.state.status === 'pending_remediation'
    && props.state.pending_box?.reason !== 'ng_ack'
    ? props.state.pending_box : null);
const ngAckBox = computed(() =>
  props.state && props.state.status === 'pending_remediation'
    && props.state.pending_box?.reason === 'ng_ack'
    ? props.state.pending_box : null);
// v3.44 收尾快照 (completed/aborted 保留展示, 扫新单顶掉)
const isDoneSnapshot = computed(() =>
  !!props.state && ['completed', 'aborted'].includes(props.state.status));
const canRemediate = computed(() => authStore.hasPermission('monitor.detection.ack'));

async function onSupplement(auto) {
  if (!props.config?.id || !pendingBox.value) return;
  let targetCount = null;
  if (!auto) {
    try {
      const r = await ElMessageBox.prompt(
        `第 ${pendingBox.value.box} 箱实际滑块数（目标 ${pendingBox.value.target}）`,
        '补滑块 · 手动输入实际数',
        {
          confirmButtonText: '确认补做',
          cancelButtonText: '取消',
          inputType: 'number',
          inputValue: String(pendingBox.value.target),
          inputValidator: (v) => {
            const n = Number(v);
            if (!Number.isFinite(n) || n < 0) return '请输入有效数量';
            if (n > pendingBox.value.target) return `不能超过目标 ${pendingBox.value.target}（超出属多装，需现场处置）`;
            return true;
          },
        });
      targetCount = Math.floor(Number(r.value));
    } catch { return; }
  }
  remediating.value = true;
  try {
    await supplementPackagingSliders(props.config.id, targetCount);
    ElMessage.success(`已补做第 ${pendingBox.value?.box} 箱`);
  } catch (e) {
    const status = e?.response?.status;
    const detail = e?.response?.data?.detail || e?.message || '未知错误';
    if (status === 403) ElMessage.error('无补做权限（需 monitor.detection.ack）');
    else if (status === 409) ElMessage.warning('当前没有等待补做的少装箱');
    else ElMessage.error('补做失败：' + detail);
  } finally {
    remediating.value = false;
  }
}

async function onRedo() {
  if (!props.config?.id) return;
  remediating.value = true;
  try {
    await redoPackagingBox(props.config.id);
    ElMessage.success('已转回重做，等下一周期重新结算本箱');
  } catch (e) {
    const status = e?.response?.status;
    const detail = e?.response?.data?.detail || e?.message || '未知错误';
    if (status === 403) ElMessage.error('无补做权限（需 monitor.detection.ack）');
    else if (status === 409) ElMessage.warning('当前没有等待补做的少装箱');
    else ElMessage.error('重做失败：' + detail);
  } finally {
    remediating.value = false;
  }
}

// 强制结案按钮: 有进行中工单 + 当前账号有权限才出现 (鉴权关时人人=超管, 始终可见)
const canForceSettle = computed(() =>
  !!props.state
  && ['order_loaded', 'running', 'awaiting_paper'].includes(props.state.status)
  && authStore.hasPermission('system.packaging_flow.force_settle'));

async function onForceSettle() {
  if (!props.config?.id) return;
  let reason;
  try {
    const r = await ElMessageBox.prompt(
      `强制结案当前工单「${props.state?.order_no || '-'}」？将立即收尾未完成的箱并完成工单，理由会留痕审计。`,
      '强制结案 (管理员/主管)',
      {
        confirmButtonText: '确认强制结案',
        cancelButtonText: '取消',
        inputType: 'textarea',
        inputPlaceholder: '请输入强制结案理由（必填）',
        inputValidator: (v) => (v && v.trim() ? true : '理由不能为空'),
        confirmButtonClass: 'el-button--danger',
      });
    reason = (r.value || '').trim();
  } catch {
    return; // 用户取消
  }
  forcing.value = true;
  try {
    const { data } = await forcePackagingSettle(props.config.id, reason);
    const lr = data?.last_run || {};
    ElMessage.success(`已强制结案 ${lr.order_no || ''}：${lr.final_result || '完成'}`
      + (data?.forced_by ? `（授权人 ${data.forced_by}）` : ''));
  } catch (e) {
    const status = e?.response?.status;
    const detail = e?.response?.data?.detail || e?.message || '未知错误';
    if (status === 403) ElMessage.error('无强制结案权限（需管理员 / 主管）');
    else if (status === 409) ElMessage.warning('当前没有进行中的工单可结案');
    else ElMessage.error('强制结案失败：' + detail);
  } finally {
    forcing.value = false;
  }
}

const traysPerBox = computed(() => props.config?.trays_per_box_fixed || 4);

// v3.22 滑块口径: 当前箱目标 = 普通箱每箱数 / 尾箱余数
const isSliders = computed(() => props.state?.count_unit === 'sliders');

const isTailBox = computed(() =>
  !!props.state && props.state.box_total > 0
  && props.state.current_box_index >= props.state.box_total);

const currentBoxTarget = computed(() => {
  if (!props.state) return 0;
  return isTailBox.value && props.state.tail_target > 0
    ? props.state.tail_target
    : (props.state.items_per_box || 0);
});

const boxPercent = computed(() => {
  if (!props.state) return 0;
  if (isSliders.value) {
    const tgt = currentBoxTarget.value;
    if (!tgt) return 0;
    return Math.min(100, Math.max(0, Math.round((props.state.current_box_sliders / tgt) * 100)));
  }
  if (!traysPerBox.value) return 0;
  return Math.min(100, Math.max(0, Math.round((props.state.current_box_trays / traysPerBox.value) * 100)));
});

const statusLabel = computed(() => ({
  order_loaded: '工单已开',
  running: '装箱中',
  pending_remediation: '少装·等补做',
  awaiting_paper: '等放工单收尾',
  completed: '已完成',
  aborted: '已作废',
}[props.state?.status] || (props.state ? props.state.status : '空闲')));

const statusColor = computed(() => ({
  running: 'text-green-400',
  pending_remediation: 'text-amber-400',
  awaiting_paper: 'text-cyan-400',
  completed: 'text-cyan-400',
  aborted: 'text-red-400',
}[props.state?.status] || 'text-gray-400'));
</script>

<style scoped>
.packaging-card {
  color: white;
}
</style>
