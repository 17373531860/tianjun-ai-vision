<template>
  <!--
    逐件模式可视化面板 (v3.8+) - 整页布局, 与 tracking 模式的「物品清点」对齐.
    数据源: detection/results.per_item_state (来自 source_per_item_mixin.get_per_item_state).
    位置: 视频下方横条 (h-44), 与 SOP 流程卡片 / 跟踪清单 互斥.
  -->
  <div class="bg-slate-900 border border-slate-700 rounded-lg overflow-hidden flex flex-col"
       :style="{ height: panelHeight }">

      <!-- 头部 bar (对齐 tracking 风格) -->
    <div class="bg-slate-800 px-3 py-1 border-b border-slate-700 flex-shrink-0 flex justify-between items-center">
      <div class="flex items-center gap-2">
        <span class="text-cyan-400 text-lg font-bold">{{ mix ? '物品校验 · 逐件覆盖' : '逐件覆盖' }}</span>
        <!-- 手动结算模式标记 -->
        <span v-if="state?.config?.disable_auto_settle"
              class="bg-amber-500/20 text-amber-300 text-[0.625rem] px-1.5 py-0.5 rounded font-mono border border-amber-500/40"
              title="自动 OK / 周期超时 NG / 空闲超时 NG / 收尾标签触发, 全部禁用. 周期结算只能靠手动按钮.">
          🖐 手动结算模式
        </span>
        <span v-if="overallDisplayTotal > 0 && state?.cycle_active"
              class="bg-green-500/20 text-green-400 text-[0.625rem] px-1.5 py-0.5 rounded font-mono">
          周期中 · {{ overallCovered }}/{{ overallDisplayTotal }}
        </span>
        <span v-else-if="overallDisplayTotal > 0"
              class="bg-slate-700 text-cyan-300 text-[0.625rem] px-1.5 py-0.5 rounded font-mono">
          目标 · {{ overallDisplayTotal }}
        </span>
        <span v-else-if="state?.cycle_active"
              class="bg-cyan-500/20 text-cyan-400 text-[0.625rem] px-1.5 py-0.5 rounded animate-pulse">
          周期中 · 识别物件…
        </span>
        <span v-else
              class="bg-slate-700 text-gray-400 text-[0.625rem] px-1.5 py-0.5 rounded">
          {{ mix ? '等待周期开始（由步骤驱动）' : `等待场景稳定 (${stabilityWindow} 帧${state?.config?.require_exact_count ? ' · 严格等量' : ''})` }}
        </span>
      </div>
      <div class="flex items-center gap-3">
        <!-- v3.10.2+ 手动周期时机控制
             仅在「手动结算模式」开启时可见, 避免和自动判定路径互相打架.
             需要时去「项目配置 → 逻辑设置 → 结算时机」开「纯手动」开关. -->
        <div v-if="!readonly && state?.config?.disable_auto_settle" class="flex items-center gap-1">
          <button
            class="px-2 py-0.5 text-[0.625rem] font-bold rounded transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
            :class="state?.cycle_active
              ? 'bg-slate-700 text-gray-500'
              : 'bg-cyan-600 hover:bg-cyan-500 text-white'"
            :disabled="busy || state?.cycle_active"
            title="手动开始一个新周期 (替代等画面稳定那一刻; 后续覆盖/超时/NG 全部真实跑)"
            @click="handleControl('force_start')">手动开始</button>
          <button
            class="px-2 py-0.5 text-[0.625rem] font-bold rounded transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
            :class="state?.cycle_active
              ? 'bg-amber-500 hover:bg-amber-400 text-white ring-2 ring-amber-300/50 animate-pulse'
              : 'bg-slate-700 text-gray-500'"
            :disabled="busy || !state?.cycle_active"
            title="手动触发结算 (替代 finish_label 那一刻; OK/NG 由真实覆盖状态判定)"
            @click="handleControl('settle')">手动结算</button>
        </div>
        <!-- v3.28+ 手动判定 (判定时机=manual 时显示): 点一下拍快照亮绿/红, 不落账 -->
        <button
          v-if="!readonly && state?.config?.judge_timing === 'manual' && state?.cycle_active"
          class="px-2 py-0.5 text-[0.625rem] font-bold rounded transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
          :class="state?.judged && state?.judged_ok
            ? 'bg-green-600 text-white'
            : 'bg-emerald-600 hover:bg-emerald-500 text-white ring-2 ring-emerald-300/50 animate-pulse'"
          :disabled="busy || (state?.judged && state?.judged_ok)"
          title="手动判定: 立刻按当前真实覆盖拍快照, 全覆盖亮绿 / 有漏亮红+漏点. 不落账, 结算仍由结算时机触发."
          @click="handleControl('judge')">{{ state?.judged && state?.judged_ok ? '已判合格' : '手动判定' }}</button>
        <!-- 全局进度条 (cycle 激活后才显示) -->
        <div v-if="state?.cycle_active && overallDisplayTotal > 0" class="w-32 h-1.5 bg-slate-900 rounded-full overflow-hidden">
          <div class="h-full transition-all duration-300 rounded-full"
               :class="overallProgress >= 1 ? 'bg-green-500' : 'bg-cyan-500'"
               :style="{ width: (overallProgress * 100).toFixed(0) + '%' }"></div>
        </div>
        <div class="flex items-center gap-2 text-xs text-gray-500 font-mono">
          <span v-if="state?.cycle_active && cycleSeconds !== null">{{ cycleSeconds.toFixed(1) }}s</span>
          <span v-if="state?.cycle_active" class="text-green-400 animate-pulse">●</span>
          <span v-else class="text-gray-600">○</span>
        </div>
      </div>
    </div>

    <!-- 待补态横幅 (离场判 NG + ng_hold_for_remediation 开启时): 红条提示漏点 + 确认NG -->
    <div v-if="state?.awaiting_remediation"
         class="bg-red-900/70 border-y-2 border-red-500 px-3 py-1.5 flex-shrink-0 flex items-center justify-between gap-3 animate-pulse">
      <div class="flex items-center gap-2 min-w-0">
        <span class="text-red-300 text-base font-bold whitespace-nowrap">⚠ 待补打</span>
        <span class="text-red-200 text-xs truncate">
          仍有 <span class="font-mono font-bold text-white">{{ remediationMissingTotal }}</span> 颗未拧紧{{ remediationMissingHint }} · 请补打 (自动撤红转 OK) 或点「确认NG」
        </span>
      </div>
      <button
        v-if="!readonly"
        class="px-3 py-0.5 text-xs font-bold rounded bg-red-600 hover:bg-red-500 text-white whitespace-nowrap transition-colors disabled:opacity-40"
        :disabled="busy"
        title="放弃补打, 按当前真实覆盖状态把本件判为 NG 落账 (= 触发事件人工确认)"
        @click="handleControl('confirm_ng')">确认 NG</button>
    </div>

    <!-- 重复打警告横幅 (duplicate_screw_alarm 开启且检出回头重打时): 黄条提示 + 报警灯已响 -->
    <div v-if="duplicateWarning"
         class="bg-amber-900/70 border-y-2 border-amber-500 px-3 py-1.5 flex-shrink-0 flex items-center gap-2 animate-pulse">
      <span class="text-amber-300 text-base font-bold whitespace-nowrap">⚠ 重复打</span>
      <span class="text-amber-100 text-xs truncate">{{ duplicateWarnText }} · 请勿重复打同一颗螺丝</span>
    </div>

    <!-- 主体: 横向滚动 / 每步一卡片 (对齐 tracking 容器模式风格) -->
    <div class="flex-1 p-2 overflow-x-auto">
      <div class="flex h-full gap-3 items-stretch">

        <!-- 步骤卡片 (一个 per_item 步骤一张) -->
        <div v-for="step in (state?.steps || [])" :key="step.step_id || step.label"
             class="flex-shrink-0 w-72 bg-slate-800 rounded-lg border p-2 flex flex-col transition-all"
             :class="step.completed
                ? 'border-green-500/70'
                : (step.total > 0 ? 'border-amber-500/70' : 'border-slate-700')">

          <!-- 标题 (步骤名 + 动作显示, 第一行) -->
          <div class="flex items-center justify-between gap-2">
            <span class="text-sm font-bold text-white truncate flex-1" :title="step.display_label || step.label">
              {{ step.display_label || step.label }}
            </span>
            <span v-if="step.completed" class="text-green-400 text-sm font-bold">✓ 完成</span>
            <span v-else-if="step.total > 0" class="text-amber-400 text-[0.625rem] font-bold animate-pulse">进行中</span>
            <span v-else class="text-gray-500 text-[0.625rem]">待开始</span>
          </div>

          <!-- 巨大数字进度 (第一反馈, 视觉核心) -->
          <div class="flex items-baseline gap-1 mb-1">
            <span class="font-mono font-bold leading-none"
                  :class="step.completed
                    ? 'text-green-400 text-4xl'
                    : ((stepDisplayTotal(step) > 0) ? 'text-white text-4xl' : 'text-gray-600 text-3xl')">
              {{ step.covered_count }}
            </span>
            <span class="text-gray-500 text-2xl font-mono leading-none">/</span>
            <span class="text-gray-400 text-2xl font-mono leading-none">{{ stepDisplayTotal(step) || '?' }}</span>
            <span class="text-[0.625rem] text-gray-500 ml-1">已 {{ step.action_label || '动作' }}</span>
            <!-- 锁定不足提示 (期望 14 但只锁到 12 之类) -->
            <span v-if="step.expected_count > 0 && step.total > 0 && step.total < step.expected_count"
                  class="text-[0.5625rem] text-amber-400 ml-auto font-mono"
                  :title="`目标 ${step.expected_count} 颗 · 实际仅锁定 ${step.total} 颗 (模型漏检或 lookahead 不足)`">
              ⚠ 锁 {{ step.total }}/{{ step.expected_count }}
            </span>
          </div>

          <!-- 中部:左网格 + 右 minimap -->
          <div class="flex-1 flex gap-1.5 min-h-0">

            <!-- 个体网格 (左侧, 占主要空间) -->
            <div v-if="(step.items?.length ?? 0) > 0" class="flex-1 min-w-0 flex flex-col">
              <div class="text-[0.5625rem] text-gray-500 mb-0.5">个体状态</div>
              <div class="flex-1 grid gap-0.5 content-start overflow-y-auto custom-scrollbar"
                   :style="{ gridTemplateColumns: 'repeat(' + colsForItems(step.items.length) + ', minmax(0, 1fr))' }">
                <div v-for="item in step.items" :key="item.id"
                     class="aspect-square rounded text-[9px] flex items-center justify-center font-bold border transition-colors"
                     :class="itemClass(item)"
                     :style="itemStyle(item)"
                     :title="itemTitle(item)">
                  {{ item.id }}
                </div>
              </div>
            </div>

            <!-- minimap (右侧, 16:9 缩略图) -->
            <div v-if="(step.items?.length ?? 0) > 0" class="w-20 shrink-0 flex flex-col">
              <div class="text-[0.5625rem] text-gray-500 mb-0.5">空间分布</div>
              <div class="flex-1 bg-slate-950 border border-slate-700 rounded overflow-hidden flex items-center">
                <svg viewBox="0 0 160 90" class="w-full block" preserveAspectRatio="xMidYMid meet">
                  <!-- 网格辅助线 -->
                  <line v-for="g in 3" :key="'vg'+g" :x1="g * 40" y1="0" :x2="g * 40" y2="90" stroke="#1e293b" stroke-width="0.5" />
                  <line v-for="g in 2" :key="'hg'+g" x1="0" :y1="g * 30" x2="160" :y2="g * 30" stroke="#1e293b" stroke-width="0.5" />
                  <!-- 个体框 -->
                  <g v-for="item in step.items" :key="'mm'+item.id">
                    <rect
                      :x="item.bbox[0] * 160"
                      :y="item.bbox[1] * 90"
                      :width="Math.max(2, item.bbox[2] * 160)"
                      :height="Math.max(2, item.bbox[3] * 90)"
                      :fill="itemFillColor(item)"
                      :fill-opacity="item.covered ? 0.55 : 0.3"
                      :stroke="itemStrokeColor(item)"
                      stroke-width="1" />
                    <text
                      :x="(item.bbox[0] + item.bbox[2] / 2) * 160"
                      :y="(item.bbox[1] + item.bbox[3] / 2) * 90 + 2"
                      font-size="6"
                      fill="#fff"
                      text-anchor="middle">{{ item.id }}</text>
                  </g>
                </svg>
              </div>
            </div>

            <!-- 空态 -->
            <div v-else class="flex-1 flex items-center justify-center text-center px-2">
              <div class="text-[0.625rem] text-gray-500">
                <div v-if="!step.item_label || !step.action_label" class="text-amber-400">
                  ⚠ 未配置物件/动作标签
                </div>
                <div v-else-if="state?.cycle_active">
                  识别 {{ step.item_label }} 中…
                </div>
                <div v-else>
                  {{ mix ? '等待周期开始（由步骤驱动）' : '等待场景稳定后开始' }}
                </div>
              </div>
            </div>
          </div>

          <!-- 底部 item⟶action 提示 -->
          <div class="mt-1.5 pt-1 border-t border-slate-700/50 text-[0.5625rem] text-gray-500 truncate"
               :title="(step.item_label || '?') + ' ⟶ ' + (step.action_label || '?')">
            <span class="text-cyan-300">{{ step.item_label || '?' }}</span>
            <span class="mx-1">⟶</span>
            <span class="text-amber-300">{{ step.action_label || '?' }}</span>
          </div>
        </div>

        <!-- 收尾标签卡片 (config.finish_label 配了才显示) -->
        <div v-if="state?.config?.finish_label"
             class="flex-shrink-0 w-28 bg-slate-800 rounded-lg border border-slate-700 p-2 flex flex-col">
          <span class="text-[0.625rem] text-gray-400">收尾标签</span>
          <div class="flex-1 flex items-center justify-center">
            <span class="text-base font-bold text-cyan-300 text-center break-all leading-tight">{{ state.config.finish_label }}</span>
          </div>
          <span class="text-[0.5625rem] text-gray-500 text-center">检出后才算 OK</span>
        </div>

        <!-- 全空态 -->
        <div v-if="(state?.steps?.length ?? 0) === 0"
             class="flex items-center justify-center text-gray-500 text-sm w-full">
          {{ mix ? '请在「项目配置 → 步骤设置」把标签角色切为「物品」并配好目标⟶动作配对' : '请在「项目配置 → 逻辑设置」启用至少一个 per_item 步骤' }}
        </div>
      </div>
    </div>

    <!-- 底部 NG 提示 (last_ng_detail 有时才显示, 占用极小高度) -->
    <div v-if="state?.last_ng_detail"
         class="bg-red-950/40 border-t border-red-800/50 px-3 py-0.5 text-[0.625rem] text-red-300 flex-shrink-0 truncate">
      <span class="font-bold">上次 NG:</span>
      <span class="ml-1">{{ formatNgDetail(state.last_ng_detail) }}</span>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch, onBeforeUnmount } from 'vue'
import { ElMessage } from 'element-plus'
import { perItemControl } from '@/api/detection'

const props = defineProps({
  state: {
    type: Object,
    default: null,
  },
  channel: {
    type: Number,
    default: 0,
  },
  // v3.19.x 自定义混合逐件复用本面板: 周期主权在步骤侧, 文案随之切换
  // (state.config 为 null → 手动结算按钮 / 收尾标签卡片自然隐藏)
  mix: {
    type: Boolean,
    default: false,
  },
  readonly: {
    type: Boolean,
    default: false,
  },
})

// v3.10.2+ 手动周期时机控制 (仅 settle | force_start)
const busy = ref(false)
const handleControl = async (action) => {
  if (props.readonly) return
  if (busy.value) return
  busy.value = true
  try {
    const res = await perItemControl(action, props.channel)
    const data = res?.data || {}
    const msg = data?.result?.msg || data?.action || '操作完成'
    if (action === 'force_start') ElMessage.success(`▶ ${msg}`)
    else if (action === 'settle') ElMessage.info(`⏹ ${msg}`)
    else if (action === 'judge') {
      if (data?.result?.judged_ok === false) ElMessage.warning(`🔴 ${msg}`)
      else ElMessage.success(`🟢 ${msg}`)
    }
    else ElMessage.success(msg)
  } catch (err) {
    const detail = err?.response?.data?.detail || err?.message || '操作失败'
    ElMessage.error(`手动控制失败: ${detail}`)
  } finally {
    busy.value = false
  }
}

// ──── 头部展示 ────
const stabilityWindow = computed(() => props.state?.config?.stability_window_frames ?? '?')

// ──── 全局进度 ────
// v3.10.2+ display_total 优先: expected_count > 0 时, 即便锁定不足也按目标显示
const stepDisplayTotal = (st) => {
  if (!st) return 0
  return st.display_total ?? (st.expected_count > 0 ? st.expected_count : (st.total || 0))
}
const overallDisplayTotal = computed(() => {
  if (!props.state?.steps) return 0
  return props.state.steps.reduce((s, st) => s + stepDisplayTotal(st), 0)
})
const overallCovered = computed(() => {
  if (!props.state?.steps) return 0
  return props.state.steps.reduce((s, st) => s + (st.covered_count || 0), 0)
})
const overallProgress = computed(() => {
  if (overallDisplayTotal.value === 0) return 0
  return overallCovered.value / overallDisplayTotal.value
})

// ──── 周期已运行秒数 ────
const cycleSeconds = computed(() => {
  const startTs = props.state?.cycle_start_time
  if (!startTs) return null
  // 后端用 time.time(), 单位秒
  return Math.max(0, (Date.now() / 1000) - startTs)
})

// ──── 个体网格列数 (按数量自适应) ────
const colsForItems = (n) => {
  if (n <= 4) return 4
  if (n <= 9) return 3
  if (n <= 16) return 4
  if (n <= 25) return 5
  if (n <= 36) return 6
  return 8
}

// ──── 待补态展示 (横幅) ────
const remediationMissingTotal = computed(() => {
  const nd = props.state?.last_ng_detail
  return (nd && typeof nd.missing_total === 'number') ? nd.missing_total : 0
})
const remediationMissingHint = computed(() => {
  const nd = props.state?.last_ng_detail
  const steps = (nd && Array.isArray(nd.steps_failed)) ? nd.steps_failed : []
  const parts = steps.map((s) => {
    const label = s.display_label || s.step_label || '?'
    const ids = (s.missing_item_ids || []).slice(0, 4)
    return ids.length ? `${label} #${ids.join('/')}` : label
  })
  return parts.length ? ` (${parts.join(' · ')})` : ''
})

// ──── 重复打警告 (last_warning 有时才显示黄条; 报警灯已由后端复用 event2 触发) ────
// 前端自己按配置时间倒计时撤下横幅, 不依赖后端持续送帧清空 (视频停/暂停时后端不再清,
// 若只靠后端会一直挂着). 只在"出现新警告"(ts 变化) 时重置计时器; 后端反复送同一条
// (ts 不变) 不会刷新计时, 到点就撤. display_sec=0 → 不自动撤, 等后端清 (ts 变 null).
const shownWarning = ref(null)
let _warnHideTimer = null
const _clearWarnTimer = () => {
  if (_warnHideTimer) { clearTimeout(_warnHideTimer); _warnHideTimer = null }
}
watch(
  () => props.state?.last_warning?.ts,
  (ts) => {
    _clearWarnTimer()
    if (!ts) { shownWarning.value = null; return }   // 后端已清 → 撤下
    shownWarning.value = props.state.last_warning
    const disp = Number(props.state?.config?.duplicate_warning_display_sec ?? 0)
    if (disp > 0) {
      _warnHideTimer = setTimeout(() => { shownWarning.value = null }, disp * 1000)
    }
  },
  { immediate: true },
)
onBeforeUnmount(_clearWarnTimer)

const duplicateWarning = computed(() => shownWarning.value)
const duplicateWarnText = computed(() => {
  const w = duplicateWarning.value
  return w ? (w.reason_summary || '检测到重复打同一颗螺丝') : ''
})

// ──── 面板高度: 基础 11rem, 每出现一条横幅 (待补红条 / 重复打黄条) 就整体加高一截 ────
// 这样横幅弹出时是"把框拉长", 而不是压缩中间步骤卡片区域 (flex-1 高度保持不变).
const bannerCount = computed(() => {
  let n = 0
  if (props.state?.awaiting_remediation) n++
  if (duplicateWarning.value) n++
  return n
})
const panelHeight = computed(() => `${11 + bannerCount.value * 2.75}rem`)

// ──── 个体色块样式 (颜色可配: state.config.box_color_covered / box_color_uncovered) ────
const coveredColor = computed(() => props.state?.config?.box_color_covered || '#22c55e')
const uncoveredColor = computed(() => props.state?.config?.box_color_uncovered || '#ef4444')
// 自定义色时走内联 style; 留空时回退原 tailwind class (零视觉差异)
const itemClass = (item) => {
  // 本周期被重复打过 (item.dup > 0) → 叠一圈琥珀色环, 一眼看出哪颗被重打
  const dupRing = item.dup > 0 ? ' ring-2 ring-amber-400' : ''
  if (props.state?.config?.box_color_covered || props.state?.config?.box_color_uncovered) {
    return 'border' + dupRing
  }
  if (item.covered) {
    return 'bg-green-600/30 border-green-500 text-green-200' + dupRing
  }
  return 'bg-red-950/40 border-red-700/60 text-red-300' + dupRing
}
const itemStyle = (item) => {
  if (!props.state?.config?.box_color_covered && !props.state?.config?.box_color_uncovered) return {}
  const c = item.covered ? coveredColor.value : uncoveredColor.value
  return { backgroundColor: c + '4d', borderColor: c, color: '#fff' }
}
const itemFillColor = (item) => (item.covered ? coveredColor.value : uncoveredColor.value)
const itemStrokeColor = (item) => (item.covered ? coveredColor.value : uncoveredColor.value)

const itemTitle = (item) => {
  const lines = [`物件 #${item.id}`]
  lines.push(item.covered ? '已覆盖 ✓' : '未覆盖')
  if (item.covered_at) {
    const dt = new Date(item.covered_at * 1000)
    lines.push(`完成时间: ${dt.toLocaleTimeString()}`)
  }
  if (item.dup > 0) lines.push(`⚠ 重复打 ${item.dup} 次`)
  return lines.join('\n')
}

// ──── 格式化 NG 详情 (兼容 string / 旧 list / 新 dict 三种格式) ────
const formatNgDetail = (ng) => {
  if (!ng) return ''
  if (typeof ng === 'string') return ng

  // 旧格式: list[step_detail] (v3.5.x 早期, 已废弃但保留兼容)
  if (Array.isArray(ng)) {
    if (!ng.length) return '未完成'
    return ng.map((d) => {
      const label = d.display_label || d.step_label || '?'
      const miss = d.missing_item_ids?.length || 0
      return `${label} 漏${miss}件`
    }).join(' · ')
  }

  // 新结构化 dict 格式 (v3.5.x 后期)
  const parts = []
  if (typeof ng.missing_total === 'number' && ng.missing_total > 0) {
    parts.push(`漏 ${ng.missing_total} 件`)
  }
  if (typeof ng.cycle_duration_sec === 'number') {
    parts.push(`耗时 ${ng.cycle_duration_sec.toFixed(1)}s`)
  }
  // 单件超时 (后续若支持周期内立即 NG 也走这里)
  if (ng.timeout_item_id !== undefined && ng.timeout_item_id !== null) {
    parts.push(`#${ng.timeout_item_id} 超时`)
  }
  // 步骤摘要 (最多列两个步骤, 避免单行过长)
  if (Array.isArray(ng.steps_failed) && ng.steps_failed.length) {
    const stepSummary = ng.steps_failed.slice(0, 2).map((s) => {
      const label = s.display_label || s.step_label || '?'
      const ids = (s.missing_item_ids || []).slice(0, 3)
      const idHint = ids.length ? ` #${ids.join('/')}` : ''
      return `${label}${idHint}`
    }).join(' / ')
    if (stepSummary) parts.push(stepSummary)
    if (ng.steps_failed.length > 2) parts.push(`+${ng.steps_failed.length - 2} 步骤`)
  }
  // 兜底
  if (!parts.length && ng.reason_summary) parts.push(ng.reason_summary)
  if (!parts.length && ng.reason) parts.push(ng.reason)
  return parts.length ? parts.join(' · ') : '未完成'
}
</script>
