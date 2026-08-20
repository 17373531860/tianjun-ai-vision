<template>
  <!-- 人工确认阻塞层 — compact: 多工位 layout 顶部同构块 / full: 单工位全屏覆盖层 (两套模板原样保留, 行为零差异) -->
  <div
    v-if="variant === 'compact'"
    class="absolute inset-0 z-[60] flex items-center justify-center bg-black/75 backdrop-blur-sm p-4"
  >
    <div class="w-full max-w-lg bg-slate-900 border-2 border-amber-500 rounded-xl shadow-2xl p-6 flex flex-col gap-4">
      <div class="text-center">
        <div class="text-amber-400 text-sm font-bold mb-1">⚠ 需要人工确认</div>
        <div class="text-2xl font-bold text-white">
          工位 {{ display.channel + 1 }}
        </div>
      </div>
      <div class="bg-slate-800 rounded-lg p-4 space-y-2 text-sm">
        <div class="flex gap-2">
          <span class="text-gray-400 flex-shrink-0">事件:</span>
          <span class="text-white font-bold">{{ display.eventName || '(未命名事件)' }}</span>
        </div>
        <div class="flex gap-2">
          <span class="text-gray-400 flex-shrink-0">原因:</span>
          <span class="text-gray-200 break-all">{{ display.reason || '—' }}</span>
        </div>
        <div class="flex gap-2 items-center">
          <span class="text-gray-400 flex-shrink-0">已等待:</span>
          <span class="text-cyan-300 font-mono">{{ waitedSec }} 秒</span>
          <template v-if="display.timeoutSec > 0">
            <span class="text-gray-600">|</span>
            <span class="text-amber-300 font-mono">{{ remainSec }} 秒后自动确认</span>
          </template>
        </div>
        <!-- v3.23 缺步骤延迟落账挂起: 展示缺项明细 -->
        <div v-if="display.remediation" class="flex gap-2 items-start pt-2 border-t border-slate-700">
          <span class="text-gray-400 flex-shrink-0">缺步骤:</span>
          <span class="text-rose-300 font-bold break-all">
            {{ (display.remediation.missing || []).join('、') || '（未解析出具体步骤）' }}
          </span>
        </div>
        <!-- v3.44 包装层箱账挂起: 展示在制箱明细 -->
        <div v-if="display.pkgHold" class="flex gap-2 items-start pt-2 border-t border-slate-700">
          <span class="text-gray-400 flex-shrink-0">在制箱:</span>
          <span class="text-rose-300 font-bold">
            第 {{ display.pkgHold.box }} 箱 — 已进箱 {{ display.pkgHold.sliders }}
            <template v-if="display.pkgHold.target > 0"> / {{ display.pkgHold.target }}</template>
            （箱账挂起，未落 NG）
          </span>
        </div>
      </div>
      <!-- v3.23 挂起态: 补步骤/认NG/重做 三选一; v3.44 包装挂账: 重做本箱/认NG落账 二选一; 普通确认: 单"重做"按钮 -->
      <template v-if="display.remediation">
        <div class="text-xs text-gray-400 text-center">
          工人补做缺的步骤后点「补步骤」直接判合格（不重置周期）；确认确实漏做点「认 NG」；想整件重做点「重做」。
        </div>
        <div class="flex items-center justify-center gap-2 flex-wrap">
          <el-button type="success" size="large" :loading="display.acking"
            @click="$emit('ack', display.channel, 'supplement_step')">补步骤 — 判合格</el-button>
          <el-button type="danger" size="large" plain :loading="display.acking"
            @click="$emit('ack', display.channel, 'confirm_ng')">认 NG</el-button>
          <el-button size="large" :loading="display.acking"
            @click="$emit('ack', display.channel, 'redo')">重做本件</el-button>
        </div>
      </template>
      <template v-else-if="display.pkgHold">
        <div class="text-xs text-gray-400 text-center">
          本箱账挂起未落 NG：点「重做本箱」丢弃这次结果同箱重测（工单进度不动）；点「认 NG 落账」按实际进箱数记 NG 箱并进入下一箱。
        </div>
        <div class="flex items-center justify-center gap-2 flex-wrap">
          <el-button type="warning" size="large" :loading="display.acking"
            @click="$emit('ack', display.channel, 'redo')">重做本箱 — 不记 NG</el-button>
          <el-button type="danger" size="large" plain :loading="display.acking"
            @click="$emit('ack', display.channel, 'confirm_ng')">认 NG 落账 — 进下一箱</el-button>
        </div>
      </template>
      <div v-else class="flex items-center justify-center gap-3">
        <span v-if="display.acking" class="text-xs text-gray-400">提交中...</span>
        <el-button
          type="warning"
          size="large"
          :loading="display.acking"
          @click="$emit('ack', display.channel)"
        >
          我已确认 — 重做工位 {{ display.channel + 1 }}
        </el-button>
      </div>
    </div>
  </div>

  <!-- v3.9.x 事件人工确认 全屏覆盖层 (full 变体) -->
  <div
    v-else
    class="absolute inset-0 z-[60] bg-black/70 backdrop-blur-sm flex items-center justify-center p-4"
    @click.stop
  >
    <div class="w-full max-w-xl bg-slate-900 border-2 border-amber-500 rounded-xl shadow-2xl flex flex-col overflow-hidden">
      <div class="px-5 py-3 bg-gradient-to-r from-amber-700 to-amber-900 flex items-center gap-3">
        <el-icon :size="28" class="text-amber-200"><Warning /></el-icon>
        <div class="flex-1">
          <div class="text-white font-bold text-lg">需要人工确认</div>
          <div class="text-amber-200 text-xs">画面与状态机已暂停 — 请工人重做本周期后点击下方按钮</div>
        </div>
        <span class="bg-slate-900/60 px-2 py-0.5 rounded text-amber-200 text-xs font-bold">
          工位 {{ display.channel + 1 }}
        </span>
      </div>

      <div class="px-5 py-4 space-y-3 text-sm">
        <div class="flex items-center gap-3">
          <span class="text-gray-400 w-20 shrink-0">触发事件</span>
          <span class="text-white font-bold">{{ display.eventName || '(未命名事件)' }}</span>
        </div>
        <div class="flex items-start gap-3">
          <span class="text-gray-400 w-20 shrink-0">触发原因</span>
          <span class="text-gray-200 break-all">{{ display.reason || '—' }}</span>
        </div>
        <div class="flex items-center gap-3">
          <span class="text-gray-400 w-20 shrink-0">已等待</span>
          <span class="text-cyan-300 font-mono">{{ waitedSec }} 秒</span>
          <template v-if="display.timeoutSec > 0">
            <span class="text-gray-500">/</span>
            <span class="text-amber-300 font-mono">{{ remainSec }} 秒后自动确认</span>
          </template>
        </div>
        <!-- v3.23 缺步骤延迟落账挂起: 缺项明细 -->
        <div v-if="display.remediation" class="flex items-start gap-3">
          <span class="text-gray-400 w-20 shrink-0">缺步骤</span>
          <span class="text-rose-300 font-bold break-all">
            {{ (display.remediation.missing || []).join('、') || '（未解析出具体步骤）' }}
          </span>
        </div>
        <!-- v3.44 包装层箱账挂起明细: 挂起的是第几箱、当前进箱数/目标 -->
        <div v-if="display.pkgHold" class="flex items-center gap-3">
          <span class="text-gray-400 w-20 shrink-0">在制箱</span>
          <span class="text-rose-300 font-bold">
            第 {{ display.pkgHold.box }} 箱 — 已进箱 {{ display.pkgHold.sliders }}
            <template v-if="display.pkgHold.target > 0"> / 目标 {{ display.pkgHold.target }}</template>
            <span class="text-amber-300 ml-1">（箱账挂起，未落 NG）</span>
          </span>
        </div>
        <!-- v3.43.1 确认后的处置方式明示: 工人点按钮前就知道是"重做"还是"断点续做" -->
        <div v-if="!display.remediation && !display.pkgHold" class="flex items-center gap-3">
          <span class="text-gray-400 w-20 shrink-0">确认后</span>
          <span v-if="display.keepsCycle" class="text-emerald-300 font-bold">
            保留已完成步骤 — 从断点继续补做
          </span>
          <span v-else class="text-amber-300 font-bold">
            清空本周期已识别步骤 — 整件从头重做
          </span>
        </div>
        <div class="text-xs text-gray-500 bg-slate-950/60 rounded p-2 border-l-2 border-amber-700/50 leading-relaxed">
          <template v-if="display.remediation">
            本件缺步骤被<span class="text-amber-300">延迟落账</span>（还没记 OK/NG）。工人补做后点「补步骤」直接判合格；确认确实漏做点「认 NG」落账；想整件重做点「重做」。
          </template>
          <template v-else-if="display.pkgHold">
            本箱账已<span class="text-amber-300">挂起等处置</span>（还没记 NG 箱、没翻页）。点「重做本箱」丢弃这次结果、同一箱号重测（工单进度不动）；点「认 NG 落账」按实际进箱数记 NG 箱并进入下一箱。
          </template>
          <template v-else-if="display.keepsCycle">
            本次事件已按配置落账（<span class="text-amber-300">计数已记，确认不回滚记录</span>）。该事件配置为「确认后保留周期」：点确认只解除定格，已做对的步骤保留，请工人从断点接着补做后面的步骤。
          </template>
          <template v-else>
            本次事件已按配置落账（<span class="text-amber-300">计数已记，确认不回滚记录</span>）。点确认后清当前周期运行时（步骤序列、识别状态），请工人整件从头重做。如果有多个工位都在等确认，这里会按顺序依次显示。
          </template>
        </div>
      </div>

      <!-- v3.23 挂起态: 补步骤/认NG/重做 三选一; v3.44 包装挂账: 重做本箱/认NG落账 二选一; 普通确认: 单"重做"按钮 -->
      <div class="px-5 py-4 bg-slate-950 border-t border-slate-800 flex items-center justify-end gap-3 flex-wrap">
        <span v-if="display.acking" class="text-xs text-gray-400">提交中...</span>
        <template v-if="display.remediation">
          <el-button size="large" :loading="display.acking"
            @click="$emit('ack', display.channel, 'redo')">重做本件</el-button>
          <el-button type="danger" size="large" plain :loading="display.acking"
            @click="$emit('ack', display.channel, 'confirm_ng')">认 NG</el-button>
          <el-button type="success" size="large" :loading="display.acking"
            @click="$emit('ack', display.channel, 'supplement_step')">补步骤 — 判合格</el-button>
        </template>
        <template v-else-if="display.pkgHold">
          <el-button type="danger" size="large" plain :loading="display.acking"
            @click="$emit('ack', display.channel, 'confirm_ng')">认 NG 落账 — 进下一箱</el-button>
          <el-button type="warning" size="large" :loading="display.acking"
            @click="$emit('ack', display.channel, 'redo')">重做本箱 — 不记 NG</el-button>
        </template>
        <el-button
          v-else
          :type="display.keepsCycle ? 'success' : 'warning'"
          size="large"
          :loading="display.acking"
          @click="$emit('ack', display.channel)"
        >
          {{ display.keepsCycle
            ? `我已确认 — 断点继续 工位 ${display.channel + 1}`
            : `我已确认 — 整件重做 工位 ${display.channel + 1}` }}
        </el-button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { Warning } from '@element-plus/icons-vue';

defineProps({
  // pendingAckDisplay 计算结果 (父级 v-if 保证非空才渲染本组件)
  display: { type: Object, required: true },
  waitedSec: { type: Number, default: 0 },
  remainSec: { type: Number, default: 0 },
  // compact = 多工位 layout 顶部同构块; full = 单工位全屏覆盖层
  variant: { type: String, default: 'full' },
});

defineEmits(['ack']);
</script>
