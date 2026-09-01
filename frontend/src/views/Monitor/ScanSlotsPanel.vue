<template>
  <!-- v3.56 周期多码采集已扫列表面板
       完整态: 单工位右侧栏 / 放大态左列 — 分组进度 + 逐码 chip + 纠错
       紧凑态(compact): 双/三工位列 — 一行进度 chip, 点「明细」浮层看逐码/纠错
       readonly: kiosk 副屏只读, 隐藏全部纠错按钮
       state 为 null 时(布局编辑态强制渲染)画占位骨架, 保证编辑器能抓到区块 -->
  <div data-testid="scan-slots-panel"
       class="bg-slate-900 border border-cyan-800/50 rounded-lg"
       :class="compact ? 'px-2 py-1' : 'px-3 py-2'">

    <!-- 占位骨架 (布局编辑态且项目未启用) -->
    <div v-if="!state" class="flex items-center gap-2 text-xs text-gray-500 py-0.5">
      <span class="text-cyan-400 font-bold">多码采集</span>
      <span>（当前项目未启用）</span>
    </div>

    <!-- 紧凑态: 双/三工位 -->
    <div v-else-if="compact" class="flex items-center gap-1.5 text-xs overflow-x-auto">
      <span class="text-cyan-400 font-bold flex-shrink-0">码</span>
      <span data-testid="scan-total" class="flex-shrink-0 px-1 rounded font-mono font-bold border" :class="totalClass">
        {{ state.total_got }}/{{ state.total_expected }}
      </span>
      <span v-if="state.pending_ng" data-testid="scan-pending-chip"
            class="flex-shrink-0 px-1 rounded border font-bold border-red-700 bg-red-900/60 text-red-300 animate-pulse">
        NG 挂起
      </span>
      <span v-for="s in state.slots" :key="s.key"
            class="flex-shrink-0 px-1 rounded border font-mono"
            :class="slotClass(s)">
        {{ s.label }} {{ s.got }}/{{ s.expected }}
      </span>
      <!-- 7.4 结算后保留: 空闲时露上组结果 -->
      <span v-if="!state.collecting && state.last_settled"
            class="flex-shrink-0 px-1 rounded font-bold"
            :class="state.last_settled.is_good ? 'bg-green-800/70 text-green-200' : 'bg-red-800/70 text-red-200'">
        上组{{ state.last_settled.is_good ? 'OK' : 'NG' }}
      </span>
      <el-popover placement="bottom" :width="330" trigger="click"
                  popper-class="scan-slots-popover">
        <template #reference>
          <el-button size="small" text type="primary" class="!ml-auto flex-shrink-0" data-testid="scan-detail-btn">明细</el-button>
        </template>
        <div class="max-h-72 overflow-y-auto">
          <div v-if="state.pending_ng" class="mb-2 p-1.5 rounded border border-red-800 bg-red-950/60 text-xs">
            <div class="text-red-300">{{ state.pending_ng.reason }}</div>
            <el-button v-if="!readonly" size="small" type="danger" plain class="mt-1"
                       :loading="busy" @click="onResolveNg">按 NG 放行</el-button>
          </div>
          <ScanSlotsDetail :state="state" :readonly="readonly" :busy="busy"
                           @remove="onRemove" @clear="onClear" />
        </div>
      </el-popover>
    </div>

    <!-- 完整态: 单工位 / 放大 / kiosk -->
    <div v-else>
      <div class="flex items-center gap-2 mb-1.5">
        <span class="text-cyan-400 font-bold text-sm">多码采集</span>
        <span data-testid="scan-total" class="px-1.5 rounded font-mono font-bold text-xs border" :class="totalClass">
          {{ state.total_got }}/{{ state.total_expected }}
        </span>
        <span v-if="state.collecting" class="text-[0.625rem] text-yellow-400 animate-pulse">采集中</span>
        <el-popconfirm v-if="!readonly && state.collecting" title="清空本组全部已扫码、整组重扫？"
                       confirm-button-text="清空重扫" cancel-button-text="取消"
                       @confirm="onClear">
          <template #reference>
            <el-button size="small" type="warning" plain class="!ml-auto" :loading="busy"
                       data-testid="scan-clear-btn">清空重扫</el-button>
          </template>
        </el-popconfirm>
      </div>
      <!-- v3.56.1 NG 挂起横幅: 补扫缺码转 OK / 按 NG 放行 -->
      <div v-if="state.pending_ng" data-testid="scan-pending-banner"
           class="mb-1.5 p-1.5 rounded border border-red-800 bg-red-950/60 flex items-center gap-2 text-xs">
        <span class="text-red-300 font-bold animate-pulse flex-shrink-0">NG 挂起</span>
        <span class="text-red-300/90 truncate min-w-0" :title="state.pending_ng.reason">{{ state.pending_ng.reason }}</span>
        <el-popconfirm v-if="!readonly" title="放弃补扫，本工件按 NG 结算导出？"
                       confirm-button-text="按 NG 放行" cancel-button-text="取消"
                       @confirm="onResolveNg">
          <template #reference>
            <el-button size="small" type="danger" plain class="!ml-auto flex-shrink-0" :loading="busy"
                       data-testid="scan-resolve-ng-btn">按 NG 放行</el-button>
          </template>
        </el-popconfirm>
      </div>
      <ScanSlotsDetail :state="state" :readonly="readonly" :busy="busy" hide-clear
                       @remove="onRemove" @clear="onClear" />
      <!-- 上组结算结果 -->
      <div v-if="state.last_settled" data-testid="scan-last-settled"
           class="mt-1.5 pt-1.5 border-t border-slate-700/70 text-xs">
        <div class="flex items-center gap-2">
          <span class="text-gray-500">上组</span>
          <span class="px-1 rounded font-bold"
                :class="state.last_settled.is_good ? 'bg-green-700 text-green-100' : 'bg-red-700 text-red-100'">
            {{ state.last_settled.is_good ? 'OK' : 'NG' }}
          </span>
          <span v-if="state.last_settled.workpiece_sn" class="font-mono text-gray-300 truncate min-w-0"
                :title="state.last_settled.workpiece_sn">{{ state.last_settled.workpiece_sn }}</span>
          <span v-if="!state.last_settled.is_good" class="text-red-400 truncate min-w-0"
                :title="state.last_settled.reason">{{ state.last_settled.reason }}</span>
          <span class="ml-auto text-gray-500 flex-shrink-0">{{ state.last_settled.settled_at }}</span>
        </div>
        <!-- 7.4 现场确认: 上工件码列表保留显示到下一个工件开扫 -->
        <div v-if="!state.collecting && (state.last_settled.codes || []).length"
             data-testid="scan-last-codes" class="mt-1 flex flex-wrap gap-1">
          <span v-for="c in state.last_settled.codes" :key="c.seq"
                class="px-1 rounded border border-slate-700 bg-slate-800/70 font-mono text-[0.625rem] text-gray-400"
                :title="`${c.slot_label} · ${c.ts}`">
            <span class="text-gray-500">{{ c.slot_label }}</span> {{ c.code }}
          </span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue';
import { ElMessage } from 'element-plus';
import { removeScanCollectCode, clearScanCollectGroup, resolveScanCollectNg } from '@/api/scanCollect';
import ScanSlotsDetail from './ScanSlotsDetail.vue';

const props = defineProps({
  state: { type: Object, default: null },
  channelId: { type: Number, required: true },
  readonly: { type: Boolean, default: false },
  compact: { type: Boolean, default: false },
});

const busy = ref(false);

// 颜色规则与总览网格徽标同源: 齐=绿 / 有进度=黄 / 空=灰
const totalClass = computed(() => {
  const s = props.state;
  if (!s) return 'border-slate-600 bg-slate-800/80 text-gray-400';
  if (s.total_expected > 0 && s.total_got >= s.total_expected)
    return 'border-green-700 bg-green-900/60 text-green-300';
  if (s.total_got > 0) return 'border-yellow-700 bg-yellow-900/60 text-yellow-300';
  return 'border-slate-600 bg-slate-800/80 text-gray-400';
});

function slotClass(s) {
  if (s.got >= s.expected) return 'border-green-800 bg-green-900/40 text-green-300';
  if (s.got > 0) return 'border-yellow-800 bg-yellow-900/40 text-yellow-300';
  return 'border-slate-700 bg-slate-800/60 text-gray-400';
}

async function onRemove(recordId) {
  if (busy.value) return;
  busy.value = true;
  try {
    await removeScanCollectCode(props.channelId, recordId);
    ElMessage.success('已删除该码，可重扫');
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '删除失败');
  } finally {
    busy.value = false;
  }
}

async function onClear() {
  if (busy.value) return;
  busy.value = true;
  try {
    await clearScanCollectGroup(props.channelId);
    ElMessage.success('已清空本组，整组重扫');
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '清空失败');
  } finally {
    busy.value = false;
  }
}

async function onResolveNg() {
  if (busy.value) return;
  busy.value = true;
  try {
    await resolveScanCollectNg(props.channelId);
    ElMessage.warning('已按 NG 放行结算，可开始下一工件');
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '放行失败');
  } finally {
    busy.value = false;
  }
}
</script>

<style>
/* 明细浮层 teleport 到 body, scoped 够不着 — 深色主题跟随监控页 */
.scan-slots-popover.el-popover.el-popper {
  background: #0f172a;
  border: 1px solid rgba(34, 211, 238, 0.45);
  color: #e2e8f0;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.65);
}
.scan-slots-popover .el-popper__arrow::before {
  background: #0f172a;
  border-color: rgba(8, 145, 178, 0.5);
}
</style>
