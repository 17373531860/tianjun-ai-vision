<template>
  <!-- v3.13 M2.2b: monitor.layout.body slot — 整体 layout 完全覆盖.
       客户插件用于双工位左右半屏 + 共用底栏等深度重排. 默认走原 layout, 字节级零差异.
       v3.13.2 补丁: 加 :actions 透传开始/停止/待机/清零四个控制方法,
       让 layout.body 插件能完整重排控制按钮 (项目/模型解析等复杂前置都在 Monitor 内做了). -->
  <!-- RFC12: 整页覆盖不再限制工位数. 任意工位数都挂载插件, 插件据 channel-count 自适应单/多工位布局.
       全局 Toast / 人工确认 / 录像异常仍由宿主渲染, 避免插件漏功能. -->
  <div
    v-if="layoutBodyOverride"
    class="relative h-[calc(100vh-7.25rem)] min-h-0 overflow-hidden"
  >
    <component
      :is="layoutBodyOverride"
      :channel-count="channelCount"
      :multi-channel-data="multiChannelData"
      :selected-channel="selectedChannel"
      :channel-model-stats="channelModelStats"
      :current-project="currentProject"
      :actions="layoutBodyActions"
      :stream-url-builder="buildMultiStreamUrl"
      @update:selected-channel="selectedChannel = $event"
    />

    <!-- 双工位列级 OK/NG Toast (与原生双工位同结构, 保留 cycle-result.indicator slot).
         RFC12: 仅双工位时宿主渲染列级 Toast; 其它工位数由插件自行用 cycle-result.indicator slot 摆位. -->
    <div v-if="channelCount === 2" class="pointer-events-none absolute inset-0 z-[45] grid grid-cols-2 gap-2 p-2">
      <div v-for="ch in 2" :key="'plugin-toast-' + ch" class="relative min-h-0">
        <template v-for="position in ['top-right', 'top-left', 'bottom-right', 'bottom-left', 'center']" :key="position">
          <div class="absolute z-50 pointer-events-none flex flex-col gap-2" :class="getMultiPositionClass(position)">
            <transition-group name="toast">
              <TjSlot
                v-for="toast in (multiActiveToasts[ch - 1] || []).filter(t => t.position === position)"
                :key="toast.id"
                name="cycle-result.indicator"
                :toast="toast"
                :channel-id="ch - 1"
              >
                <div
                  class="px-4 py-3 rounded-xl shadow-2xl text-white font-bold pointer-events-auto transform transition-all duration-300 text-center"
                  :style="{ backgroundColor: toast.color, fontSize: (toast.fontSize / 16) + 'rem' }">
                  <div class="flex items-center gap-2 justify-center">
                    <el-icon :size="20"><component :is="toast.icon" /></el-icon>
                    <div><div class="font-bold">{{ toast.title }}</div><div v-if="toast.subtitle" class="text-sm opacity-80">{{ toast.subtitle }}</div></div>
                  </div>
                </div>
              </TjSlot>
            </transition-group>
          </div>
        </template>
      </div>
    </div>

    <!-- 人工确认阻塞层 (任一工位 pendingAck.active) -->
    <div
      v-if="pendingAckDisplay"
      class="absolute inset-0 z-[60] flex items-center justify-center bg-black/75 backdrop-blur-sm p-4"
    >
      <div class="w-full max-w-lg bg-slate-900 border-2 border-amber-500 rounded-xl shadow-2xl p-6 flex flex-col gap-4">
        <div class="text-center">
          <div class="text-amber-400 text-sm font-bold mb-1">⚠ 需要人工确认</div>
          <div class="text-2xl font-bold text-white">
            工位 {{ pendingAckDisplay.channel + 1 }}
          </div>
        </div>
        <div class="bg-slate-800 rounded-lg p-4 space-y-2 text-sm">
          <div class="flex gap-2">
            <span class="text-gray-400 flex-shrink-0">事件:</span>
            <span class="text-white font-bold">{{ pendingAckDisplay.eventName || '(未命名事件)' }}</span>
          </div>
          <div class="flex gap-2">
            <span class="text-gray-400 flex-shrink-0">原因:</span>
            <span class="text-gray-200 break-all">{{ pendingAckDisplay.reason || '—' }}</span>
          </div>
          <div class="flex gap-2 items-center">
            <span class="text-gray-400 flex-shrink-0">已等待:</span>
            <span class="text-cyan-300 font-mono">{{ pendingAckWaitedSec }} 秒</span>
            <template v-if="pendingAckDisplay.timeoutSec > 0">
              <span class="text-gray-600">|</span>
              <span class="text-amber-300 font-mono">{{ pendingAckRemainSec }} 秒后自动确认</span>
            </template>
          </div>
          <!-- v3.23 缺步骤延迟落账挂起: 展示缺项明细 -->
          <div v-if="pendingAckDisplay.remediation" class="flex gap-2 items-start pt-2 border-t border-slate-700">
            <span class="text-gray-400 flex-shrink-0">缺步骤:</span>
            <span class="text-rose-300 font-bold break-all">
              {{ (pendingAckDisplay.remediation.missing || []).join('、') || '（未解析出具体步骤）' }}
            </span>
          </div>
        </div>
        <!-- v3.23 挂起态: 补步骤/认NG/重做 三选一; 普通确认: 单"重做"按钮 -->
        <template v-if="pendingAckDisplay.remediation">
          <div class="text-xs text-gray-400 text-center">
            工人补做缺的步骤后点「补步骤」直接判合格（不重置周期）；确认确实漏做点「认 NG」；想整件重做点「重做」。
          </div>
          <div class="flex items-center justify-center gap-2 flex-wrap">
            <el-button type="success" size="large" :loading="pendingAckDisplay.acking"
              @click="ackPendingForChannel(pendingAckDisplay.channel, 'supplement_step')">补步骤 — 判合格</el-button>
            <el-button type="danger" size="large" plain :loading="pendingAckDisplay.acking"
              @click="ackPendingForChannel(pendingAckDisplay.channel, 'confirm_ng')">认 NG</el-button>
            <el-button size="large" :loading="pendingAckDisplay.acking"
              @click="ackPendingForChannel(pendingAckDisplay.channel, 'redo')">重做本件</el-button>
          </div>
        </template>
        <div v-else class="flex items-center justify-center gap-3">
          <span v-if="pendingAckDisplay.acking" class="text-xs text-gray-400">提交中...</span>
          <el-button
            type="warning"
            size="large"
            :loading="pendingAckDisplay.acking"
            @click="ackPendingForChannel(pendingAckDisplay.channel)"
          >
            我已确认 — 重做工位 {{ pendingAckDisplay.channel + 1 }}
          </el-button>
        </div>
      </div>
    </div>

    <!-- 录像异常入口+详情面板（M-1 外置, 原同构块消重） -->
    <RecordingFailureOverlay
      v-model:visible="showRecordingFailurePanel"
      :rows="recordingFailureRows"
      :loading="recordingFailureLoading" elevated
      @clear="clearRecordingFailures" />
  </div>

  <!-- ===== DUAL WORKSTATION MODE (2 channels) ===== -->
  <div v-else-if="channelCount === 2" class="grid grid-cols-2 gap-2 h-[calc(100vh-7.25rem)] p-2 relative">
    <div v-for="ch in 2" :key="ch - 1" class="flex flex-col gap-1.5 min-h-0 overflow-hidden relative">
      <!-- Video panel (70% height)（M-4 外置 ChannelVideoCard, 流/绘制机制留父级） -->
      <ChannelVideoCard
        style="flex: 7 1 0%;"
        :ch="ch - 1"
        :ch-data="multiChannelData[ch - 1]"
        :model-stats="channelModelStats[ch - 1]"
        :selected="selectedChannel === (ch - 1)"
        :register-video-canvas="el => { multiVideoCanvasRefs[ch - 1] = el }"
        :register-overlay-canvas="el => { multiCanvasRefs[ch - 1] = el }"
        @select="selectedChannel = ch - 1"
      />
      <!-- v3.1.3: per-channel MES 信息条 (工件号 / 未绑码警告 / 等待扫码 / 清除按钮) -->
      <div v-if="shouldShowMesBarFor(ch - 1)"
           class="bg-slate-900 border border-cyan-800/50 rounded-lg px-2 py-1 flex items-center gap-3 text-xs flex-shrink-0">
        <div v-if="!isScanDisabledFor(ch - 1) && getDisplayWorkpieceFor(ch - 1)" class="flex items-center gap-1.5 min-w-0">
          <span class="text-cyan-400 font-bold">工件:</span>
          <span class="font-mono text-white truncate" :title="getDisplayWorkpieceFor(ch - 1).serial_no">{{ getDisplayWorkpieceFor(ch - 1).serial_no }}</span>
          <el-tag :type="getDisplayWorkpieceFor(ch - 1).status === 'ok' ? 'success' : getDisplayWorkpieceFor(ch - 1).status === 'ng' ? 'danger' : getDisplayWorkpieceFor(ch - 1).status === 'inspecting' ? 'warning' : 'info'" size="small">
            {{ { registered: '已登记', queued: '排队', inspecting: '检测中', ok: '合格', ng: '不良' }[getDisplayWorkpieceFor(ch - 1).status] || getDisplayWorkpieceFor(ch - 1).status }}
          </el-tag>
        </div>
        <div v-if="!isScanDisabledFor(ch - 1) && hasScannerFor(ch - 1) && getMesDataFor(ch - 1)?.warn_no_barcode" class="warn-no-barcode-blink flex items-center gap-1 bg-yellow-600/30 border border-yellow-500 rounded px-2 py-0.5">
          <span class="text-yellow-300 font-bold">⚠ 未绑码</span>
          <span class="text-yellow-200">请扫描工件条码</span>
        </div>
        <div v-else-if="!isScanDisabledFor(ch - 1) && !getDisplayWorkpieceFor(ch - 1) && !getMesDataFor(ch - 1)?.order" class="text-gray-500">等待扫码...</div>
        <!-- v3.4.2 禁用扫码态: 显示小提示, 整栏其它工件/警告/等待全部隐藏 -->
        <div v-if="isScanDisabledFor(ch - 1)" class="flex items-center gap-1 text-gray-400 italic">
          <span>⛔ 扫码已禁用 · 走项目原生结算</span>
        </div>
        <div v-if="getMesDataFor(ch - 1)?.order" class="flex items-center gap-1 text-[0.625rem] ml-auto pl-2 border-l border-cyan-800/40">
          <span class="text-cyan-400">工单:</span>
          <span class="text-white truncate max-w-[80px]" :title="getMesDataFor(ch - 1).order.order_no">{{ getMesDataFor(ch - 1).order.order_no }}</span>
          <span class="text-gray-400">{{ getMesDataFor(ch - 1).order.completed_qty }}/{{ getMesDataFor(ch - 1).order.planned_qty }}</span>
        </div>
        <!-- 开工任务要素 (按入站配置逐项显示; 全关时无任何标签) -->
        <div v-if="getTaskInfoItemsFor(ch - 1).length" class="flex items-center gap-1 text-[0.625rem] pl-2 border-l border-cyan-800/40">
          <template v-for="it in getTaskInfoItemsFor(ch - 1)" :key="it.label">
            <span class="text-cyan-400">{{ it.label }}:</span>
            <span class="text-white truncate max-w-[72px]" :title="it.value">{{ it.value }}</span>
          </template>
        </div>
        <el-tooltip
          v-if="!isScanDisabledFor(ch - 1)"
          :content="getDisplayWorkpieceFor(ch - 1) && getDisplayWorkpieceFor(ch - 1).status === 'inspecting'
            ? '本次工件已开始检测，点击可作废本次检测、回到等待扫码状态'
            : '清除待检/扫码状态，让操作员重扫一次条码'"
          placement="top"
        >
          <el-button
            :class="getMesDataFor(ch - 1)?.order ? '' : 'ml-auto'"
            size="small"
            type="warning"
            plain
            @click.stop="clearPendingScan(ch - 1)"
          >
            清除
          </el-button>
        </el-tooltip>
        <!-- v3.4.2 按工位禁用扫码 -->
        <el-tooltip
          :content="isScanDisabledFor(ch - 1)
            ? '点击启用扫码：扫码器恢复工作，按扫码器配置的结算方式 (scan_pair / mid_cycle 等) 工作'
            : '点击禁用扫码：扫码器熄灯，所有联动工位回退到项目原生结算方式 (tracking → 全部消失，容器 → 箱子离开)'"
          placement="top"
        >
          <el-button
            :class="isScanDisabledFor(ch - 1) ? '' : (getMesDataFor(ch - 1)?.order ? '' : 'ml-auto')"
            size="small"
            :type="isScanDisabledFor(ch - 1) ? 'success' : 'danger'"
            plain
            :loading="scannerDisableStore.toggling"
            @click.stop="toggleScanDisableFor(ch - 1)"
          >
            {{ isScanDisabledFor(ch - 1) ? '启用扫码' : '禁用扫码' }}
          </el-button>
        </el-tooltip>
      </div>
      <!-- Row 1: Counters (scrollable) + Yield Rate -->
      <div class="flex gap-2 flex-shrink-0">
        <div class="flex-1 flex gap-2 overflow-x-auto min-w-0">
          <div class="flex-shrink-0 bg-slate-900 border border-slate-700 rounded px-4 py-2 text-center min-w-[5.625rem]">
            <div class="text-xs text-gray-400">总产量</div>
            <div class="text-2xl font-bold font-mono text-white">{{ multiChannelData[ch - 1]?.total ?? 0 }}</div>
          </div>
          <div class="flex-shrink-0 bg-slate-900 border border-slate-700 rounded px-4 py-2 text-center min-w-[5.625rem]">
            <div class="text-xs text-gray-400">合格</div>
            <div class="text-2xl font-bold font-mono text-green-400">{{ multiChannelData[ch - 1]?.ok ?? 0 }}</div>
          </div>
          <div class="flex-shrink-0 bg-slate-900 border border-slate-700 rounded px-4 py-2 text-center min-w-[5.625rem]">
            <div class="text-xs text-gray-400">不良</div>
            <div class="text-2xl font-bold font-mono text-red-400">{{ multiChannelData[ch - 1]?.ng ?? 0 }}</div>
          </div>
        </div>
        <div class="flex-shrink-0 w-32 bg-slate-900 border border-slate-700 rounded px-3 py-2 flex flex-col items-center justify-center">
          <div class="text-xs text-gray-400">合格率</div>
          <div class="text-2xl font-bold font-mono" :class="(multiChannelData[ch - 1]?.yieldRate ?? 0) >= 90 ? 'text-green-400' : (multiChannelData[ch - 1]?.yieldRate ?? 0) >= 70 ? 'text-yellow-400' : 'text-red-400'">
            {{ multiChannelData[ch - 1]?.yieldRate ?? 0 }}%
          </div>
        </div>
      </div>
      <!-- Row 2: SOP (left, with image cards, scrollable) + Step Stats (right) -->
      <div class="flex gap-2 min-h-0" style="flex: 3 1 0%;">
        <div class="w-[60%] bg-slate-900 border border-slate-700 rounded overflow-hidden flex flex-col min-w-0">
          <div class="bg-slate-800 px-3 py-1 text-cyan-400 text-sm font-bold border-b border-slate-700 flex items-center justify-between flex-shrink-0">
            <span>SOP</span>
            <span class="text-xs text-gray-400">CT: {{ getDisplayCT(multiChannelData[ch - 1]) }}</span>
          </div>
          <div class="flex-1 flex items-stretch gap-2 px-2 py-1 overflow-x-auto min-h-0">
            <div v-for="(step, idx) in (multiChannelData[ch - 1]?.steps || [])" :key="idx"
              class="flex-shrink-0 w-28 flex flex-col rounded border overflow-hidden"
              :class="step.status === 'completed' ? 'border-green-500 bg-green-900/30' : step.status === 'active' ? 'border-cyan-500 bg-cyan-900/30' : 'border-slate-600 bg-slate-800'">
              <div class="px-1.5 py-0.5 text-xs font-bold truncate text-center flex-shrink-0"
                :class="step.status === 'completed' ? 'text-green-300 bg-green-900/50' : step.status === 'active' ? 'text-cyan-300 bg-cyan-900/50 animate-pulse' : 'text-gray-500 bg-slate-700/50'">
                {{ step.name }}
              </div>
              <div class="flex-1 flex items-center justify-center relative overflow-hidden bg-slate-950/50">
                <img v-if="step.screenshot" :src="step.screenshot" class="w-full h-full object-cover" />
                <el-icon v-else :size="24" class="text-slate-600"><Picture /></el-icon>
                <div v-if="step.status === 'active'" class="absolute inset-0 border-2 border-cyan-500 animate-pulse"></div>
              </div>
            </div>
            <div v-if="!multiChannelData[ch - 1]?.steps?.length" class="text-gray-600 text-sm w-full text-center self-center">等待检测</div>
          </div>
        </div>
        <div class="w-[40%] bg-slate-900 border border-slate-700 rounded overflow-auto min-w-0">
          <table class="w-full text-xs">
            <thead class="bg-slate-800 text-gray-400 sticky top-0"><tr><th class="px-1.5 py-1 text-left">步骤</th><th class="px-1.5 py-1 text-left">状态</th></tr></thead>
            <tbody class="text-gray-300 divide-y divide-slate-800">
              <tr v-for="(row, i) in (multiChannelData[ch - 1]?.tableData || []).slice(0, 8)" :key="i" :class="row.status === 'completed' ? 'bg-green-900/20' : ''">
                <td class="px-1.5 py-0.5 truncate max-w-[100px]">{{ row.step }}</td>
                <td class="px-1.5 py-0.5"><span :class="row.status === 'completed' ? 'text-green-400' : 'text-gray-500'">{{ row.status === 'completed' ? 'OK' : '--' }}</span></td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
      <!-- Controls -->
      <div class="flex gap-1.5 flex-shrink-0">
        <button @click="startDetectionForChannel(ch - 1)" :disabled="(!multiChannelData[ch - 1]?.project && !currentProject) || multiChannelData[ch - 1]?.isDetecting"
          class="flex-1 bg-emerald-600 hover:bg-emerald-500 disabled:bg-gray-700 disabled:cursor-not-allowed text-white py-1 rounded text-xs font-bold">开始</button>
        <button @click="stopDetectionForChannel(ch - 1)" :disabled="!multiChannelData[ch - 1]?.isRunning"
          class="flex-1 bg-red-600 hover:bg-red-500 disabled:bg-gray-700 disabled:cursor-not-allowed text-white py-1 rounded text-xs font-bold">停止</button>
        <button @click="standbyForChannel(ch - 1)" :disabled="!multiChannelData[ch - 1]?.isDetecting"
          class="flex-1 bg-yellow-600 hover:bg-yellow-500 disabled:bg-gray-700 disabled:cursor-not-allowed text-white py-1 rounded text-xs font-bold">待机</button>
        <button @click="resetCountersForChannel(ch - 1)" :disabled="multiChannelData[ch - 1]?.isDetecting"
          class="flex-1 bg-cyan-500 hover:bg-cyan-400 disabled:bg-gray-700 disabled:cursor-not-allowed text-white py-1 rounded text-xs font-bold">清零</button>
      </div>
      <!-- Per-workstation event toasts -->
      <!-- v3.13 M2.2b: 客户插件可通过 cycle-result.indicator slot 替换或隐藏整个 toast 渲染体 -->
      <template v-for="position in ['top-right', 'top-left', 'bottom-right', 'bottom-left', 'center']" :key="position">
        <div class="absolute z-50 pointer-events-none flex flex-col gap-2" :class="getMultiPositionClass(position)">
          <transition-group name="toast">
            <TjSlot
              v-for="toast in (multiActiveToasts[ch - 1] || []).filter(t => t.position === position)"
              :key="toast.id"
              name="cycle-result.indicator"
              :toast="toast"
              :channel-id="ch - 1"
            >
              <div
                class="px-4 py-3 rounded-xl shadow-2xl text-white font-bold pointer-events-auto transform transition-all duration-300 text-center"
                :style="{ backgroundColor: toast.color, fontSize: (toast.fontSize / 16) + 'rem' }">
                <div class="flex items-center gap-2 justify-center">
                  <el-icon :size="20"><component :is="toast.icon" /></el-icon>
                  <div><div class="font-bold">{{ toast.title }}</div><div v-if="toast.subtitle" class="text-sm opacity-80">{{ toast.subtitle }}</div></div>
                </div>
              </div>
            </TjSlot>
          </transition-group>
        </div>
      </template>

      <!-- 录像异常入口+详情面板（M-1 外置, 原同构块消重） -->
      <RecordingFailureOverlay
        v-model:visible="showRecordingFailurePanel"
        :rows="recordingFailureRows"
        :loading="recordingFailureLoading"
        @clear="clearRecordingFailures" />
    </div>
  </div>

  <!-- ===== QUAD WORKSTATION MODE (4 channels) ===== -->
  <div v-else-if="channelCount > 2" class="flex flex-col h-[calc(100vh-7.25rem)] p-2 gap-2 relative">
    <!-- 2x2 video grid -->
    <div class="flex-1 grid grid-cols-2 grid-rows-2 gap-2 min-h-0">
      <ChannelVideoCard
        v-for="ch in channelCount" :key="ch - 1"
        class="cursor-pointer transition-all"
        compact
        :ch="ch - 1"
        :ch-data="multiChannelData[ch - 1]"
        :model-stats="channelModelStats[ch - 1]"
        :selected="selectedChannel === (ch - 1)"
        :register-video-canvas="el => { multiVideoCanvasRefs[ch - 1] = el }"
        :register-overlay-canvas="el => { multiCanvasRefs[ch - 1] = el }"
        @select="selectedChannel = ch - 1">
        <!-- v3.1.3: 4 工位每个小卡片在视频上沿额外显示一行 工件号 / 未绑码 / 等待扫码 -->
        <div v-if="shouldShowMesBarFor(ch - 1)"
             class="absolute top-7 left-1 right-1 bg-slate-900/85 border border-cyan-800/50 rounded px-1.5 py-0.5 flex items-center gap-1.5 text-[0.625rem] z-10">
          <template v-if="getDisplayWorkpieceFor(ch - 1)">
            <span class="text-cyan-400 font-bold">工件</span>
            <span class="font-mono text-white truncate min-w-0" :title="getDisplayWorkpieceFor(ch - 1).serial_no">{{ getDisplayWorkpieceFor(ch - 1).serial_no }}</span>
            <span class="ml-auto px-1 rounded font-bold"
                  :class="getDisplayWorkpieceFor(ch - 1).status === 'ok' ? 'bg-green-700 text-green-100' : getDisplayWorkpieceFor(ch - 1).status === 'ng' ? 'bg-red-700 text-red-100' : getDisplayWorkpieceFor(ch - 1).status === 'inspecting' ? 'bg-yellow-700 text-yellow-100' : 'bg-slate-700 text-gray-300'">
              {{ { registered: '已登记', queued: '排队', inspecting: '检测中', ok: '合格', ng: '不良' }[getDisplayWorkpieceFor(ch - 1).status] || getDisplayWorkpieceFor(ch - 1).status }}
            </span>
          </template>
          <template v-else-if="hasScannerFor(ch - 1) && getMesDataFor(ch - 1)?.warn_no_barcode">
            <span class="warn-no-barcode-blink text-yellow-300 font-bold w-full text-center">⚠ 未绑码 请扫描</span>
          </template>
          <template v-else>
            <span class="text-gray-400 w-full text-center">等待扫码...</span>
          </template>
        </div>
        <template v-for="position in ['top-right', 'top-left', 'bottom-right', 'bottom-left', 'center']" :key="position">
          <div class="absolute z-50 pointer-events-none flex flex-col gap-1" :class="getMultiPositionClass(position)">
            <transition-group name="toast">
              <div v-for="toast in (multiActiveToasts[ch - 1] || []).filter(t => t.position === position)" :key="toast.id"
                class="px-3 py-2 rounded-lg shadow-2xl text-white font-bold pointer-events-auto text-center text-xs"
                :style="{ backgroundColor: toast.color }">
                <div class="flex items-center gap-1 justify-center">
                  <el-icon :size="14"><component :is="toast.icon" /></el-icon>
                  <span>{{ toast.title }}</span>
                </div>
              </div>
            </transition-group>
          </div>
        </template>
      </ChannelVideoCard>
    </div>
    <!-- Selected channel detail panel -->
    <div class="h-64 bg-slate-900 border border-slate-700 rounded-lg overflow-hidden flex flex-col flex-shrink-0">
      <div class="bg-slate-800 px-3 py-1 border-b border-slate-700 flex items-center gap-3 flex-wrap">
        <span class="text-cyan-400 font-bold text-sm">工位 {{ selectedChannel + 1 }} 详情</span>
        <span class="text-[0.625rem] bg-slate-700 px-2 py-0.5 rounded text-gray-300">CT: {{ getDisplayCT(multiChannelData[selectedChannel]) }}</span>
        <!-- v3.1.3: 选中工位完整 MES 信息条 -->
        <template v-if="shouldShowMesBarFor(selectedChannel)">
          <span class="h-4 w-px bg-slate-600"></span>
          <div v-if="!isScanDisabledFor(selectedChannel) && getDisplayWorkpieceFor(selectedChannel)" class="flex items-center gap-1 text-xs">
            <span class="text-cyan-400 font-bold">工件:</span>
            <span class="font-mono text-white">{{ getDisplayWorkpieceFor(selectedChannel).serial_no }}</span>
            <el-tag :type="getDisplayWorkpieceFor(selectedChannel).status === 'ok' ? 'success' : getDisplayWorkpieceFor(selectedChannel).status === 'ng' ? 'danger' : getDisplayWorkpieceFor(selectedChannel).status === 'inspecting' ? 'warning' : 'info'" size="small">
              {{ { registered: '已登记', queued: '排队', inspecting: '检测中', ok: '合格', ng: '不良' }[getDisplayWorkpieceFor(selectedChannel).status] || getDisplayWorkpieceFor(selectedChannel).status }}
            </el-tag>
          </div>
          <div v-if="!isScanDisabledFor(selectedChannel) && hasScannerFor(selectedChannel) && getMesDataFor(selectedChannel)?.warn_no_barcode" class="warn-no-barcode-blink flex items-center gap-1 bg-yellow-600/30 border border-yellow-500 rounded px-2 py-0.5 text-xs">
            <span class="text-yellow-300 font-bold">⚠ 未绑码</span>
          </div>
          <div v-else-if="!isScanDisabledFor(selectedChannel) && hasScannerFor(selectedChannel) && !getDisplayWorkpieceFor(selectedChannel) && !getMesDataFor(selectedChannel)?.order" class="text-gray-500 text-xs">等待扫码...</div>
          <div v-if="isScanDisabledFor(selectedChannel)" class="flex items-center gap-1 text-gray-400 italic text-xs">
            <span>⛔ 扫码已禁用 · 走项目原生结算</span>
          </div>
          <el-button v-if="!isScanDisabledFor(selectedChannel)" size="small" type="warning" plain @click="clearPendingScan(selectedChannel)">清除本次扫码</el-button>
          <el-button
            size="small"
            :type="isScanDisabledFor(selectedChannel) ? 'success' : 'danger'"
            plain
            :loading="scannerDisableStore.toggling"
            @click="toggleScanDisableFor(selectedChannel)"
          >
            {{ isScanDisabledFor(selectedChannel) ? '启用扫码' : '禁用扫码' }}
          </el-button>
        </template>
        <div class="ml-auto flex gap-1.5">
          <button @click="startDetectionForChannel(selectedChannel)" :disabled="(!multiChannelData[selectedChannel]?.project && !currentProject) || multiChannelData[selectedChannel]?.isDetecting"
            class="bg-emerald-600 hover:bg-emerald-500 disabled:bg-gray-700 disabled:cursor-not-allowed text-white px-2.5 py-0.5 rounded text-[0.625rem] font-bold">开始</button>
          <button @click="stopDetectionForChannel(selectedChannel)" :disabled="!multiChannelData[selectedChannel]?.isRunning"
            class="bg-red-600 hover:bg-red-500 disabled:bg-gray-700 disabled:cursor-not-allowed text-white px-2.5 py-0.5 rounded text-[0.625rem] font-bold">停止</button>
          <button @click="standbyForChannel(selectedChannel)" :disabled="!multiChannelData[selectedChannel]?.isDetecting"
            class="bg-yellow-600 hover:bg-yellow-500 disabled:bg-gray-700 disabled:cursor-not-allowed text-white px-2.5 py-0.5 rounded text-[0.625rem] font-bold">待机</button>
          <button @click="resetCountersForChannel(selectedChannel)" :disabled="multiChannelData[selectedChannel]?.isDetecting"
            class="bg-cyan-500 hover:bg-cyan-400 disabled:bg-gray-700 disabled:cursor-not-allowed text-white px-2.5 py-0.5 rounded text-[0.625rem] font-bold">清零</button>
        </div>
      </div>
      <div class="flex-1 grid grid-cols-4 gap-2 p-2 min-h-0 overflow-hidden">
        <!-- Col 1: SOP steps -->
        <div class="flex flex-col gap-1 overflow-auto">
          <div class="text-[0.625rem] text-cyan-400 font-bold mb-0.5">SOP 流程</div>
          <div v-for="(step, idx) in (multiChannelData[selectedChannel]?.steps || [])" :key="idx"
            class="px-1.5 py-0.5 rounded text-[0.625rem] font-bold border"
            :class="step.status === 'completed' ? 'border-green-500 bg-green-900/30 text-green-300' : step.status === 'active' ? 'border-cyan-500 bg-cyan-900/30 text-cyan-300 animate-pulse' : 'border-slate-600 bg-slate-800 text-gray-500'">
            {{ step.name }}
          </div>
          <div v-if="!multiChannelData[selectedChannel]?.steps?.length" class="text-gray-600 text-[0.625rem] text-center mt-2">等待检测</div>
        </div>
        <!-- Col 2: Counters + step table -->
        <div class="flex flex-col gap-1 min-h-0">
          <div class="grid grid-cols-3 gap-1 flex-shrink-0">
            <div class="bg-slate-800 rounded p-0.5 text-center"><div class="text-[0.5625rem] text-gray-400">总</div><div class="text-base font-bold font-mono text-white">{{ multiChannelData[selectedChannel]?.total ?? 0 }}</div></div>
            <div class="bg-slate-800 rounded p-0.5 text-center"><div class="text-[0.5625rem] text-gray-400">OK</div><div class="text-base font-bold font-mono text-green-400">{{ multiChannelData[selectedChannel]?.ok ?? 0 }}</div></div>
            <div class="bg-slate-800 rounded p-0.5 text-center"><div class="text-[0.5625rem] text-gray-400">NG</div><div class="text-base font-bold font-mono text-red-400">{{ multiChannelData[selectedChannel]?.ng ?? 0 }}</div></div>
          </div>
          <div class="flex-1 overflow-auto min-h-0">
            <table class="w-full text-[0.625rem]">
              <thead class="bg-slate-800 text-gray-400 sticky top-0"><tr><th class="px-1 py-0.5">步骤</th><th class="px-1 py-0.5">状态</th></tr></thead>
              <tbody class="text-gray-300 divide-y divide-slate-800">
                <tr v-for="(row, i) in (multiChannelData[selectedChannel]?.tableData || [])" :key="i" :class="row.status === 'completed' ? 'bg-green-900/20' : ''">
                  <td class="px-1 py-0.5 truncate max-w-[100px]">{{ row.step }}</td>
                  <td class="px-1 py-0.5"><span :class="row.status === 'completed' ? 'text-green-400' : 'text-gray-500'">{{ row.status === 'completed' ? 'OK' : '--' }}</span></td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
        <!-- Col 3: Yield rate -->
        <div class="flex flex-col items-center justify-center bg-slate-800 rounded p-2">
          <div class="text-[0.625rem] text-gray-400 mb-1">合格率</div>
          <div class="text-3xl font-bold font-mono" :class="(multiChannelData[selectedChannel]?.yieldRate ?? 0) >= 90 ? 'text-green-400' : (multiChannelData[selectedChannel]?.yieldRate ?? 0) >= 70 ? 'text-yellow-400' : 'text-red-400'">
            {{ multiChannelData[selectedChannel]?.yieldRate ?? 0 }}%
          </div>
          <div class="w-full bg-slate-700 rounded-full h-2 mt-2">
            <div class="h-2 rounded-full transition-all" :class="(multiChannelData[selectedChannel]?.yieldRate ?? 0) >= 90 ? 'bg-green-500' : (multiChannelData[selectedChannel]?.yieldRate ?? 0) >= 70 ? 'bg-yellow-500' : 'bg-red-500'"
              :style="{ width: (multiChannelData[selectedChannel]?.yieldRate ?? 0) + '%' }"></div>
          </div>
          <div class="mt-2 text-[0.625rem] text-gray-400">
            <span class="text-green-400">{{ multiChannelData[selectedChannel]?.ok ?? 0 }}</span> / <span class="text-white">{{ multiChannelData[selectedChannel]?.total ?? 0 }}</span>
          </div>
        </div>
        <!-- Col 4: NG ranking -->
        <div v-if="systemStore.display.monitor.ngTop3 !== false" class="flex flex-col min-h-0">
          <div class="flex items-center justify-between mb-1">
            <span class="text-[0.625rem] text-cyan-400 font-bold">NG 步骤 TOP3</span>
            <span class="text-[0.5625rem] text-gray-500 cursor-pointer hover:text-cyan-400 select-none" @click="toggleNgTopMode()">
              {{ systemStore.display.monitor.ngTopDisplayMode === 'percentage' ? '百分比' : '次数' }}
            </span>
          </div>
          <div class="flex-1 overflow-auto space-y-1">
            <div v-for="(item, idx) in (multiChannelData[selectedChannel]?.ngStepRanking || [])" :key="item.step"
              class="flex items-center gap-1.5 bg-slate-800/50 px-1.5 py-1 rounded text-xs">
              <span class="text-sm font-bold w-4 text-white text-center">{{ idx + 1 }}</span>
              <span class="flex-1 text-gray-300 truncate">{{ item.step }}</span>
              <span class="text-sm font-bold text-white">{{ systemStore.display.monitor.ngTopDisplayMode === 'count' ? item.count : item.rate.toFixed(0) + '%' }}</span>
            </div>
            <div v-if="!multiChannelData[selectedChannel]?.ngStepRanking?.length" class="flex items-center justify-center h-full text-gray-600 text-xs">暂无数据</div>
          </div>
        </div>
      </div>

      <!-- 录像异常入口+详情面板（M-1 外置, 原同构块消重） -->
      <RecordingFailureOverlay
        v-model:visible="showRecordingFailurePanel"
        :rows="recordingFailureRows"
        :loading="recordingFailureLoading"
        @clear="clearRecordingFailures" />
    </div>
  </div>

  <!-- ===== SINGLE-VIEW MODE (original layout) ===== -->
  <div v-else class="grid grid-cols-12 gap-3 h-[calc(100vh-7.25rem)] p-2 relative">
    <!-- LEFT COLUMN: VIDEO & STEPS -->
    <div class="col-span-7 flex flex-col gap-3 min-h-0">
      
      <!-- Video Region -->
      <div class="min-h-0 bg-black border-2 border-slate-700 rounded-lg relative overflow-hidden group" style="aspect-ratio: 16/9; max-height: 100%;">
        <!-- 视频流：双缓冲 img + key 控制的 DOM 重建。watchdog 触发强制
             重连时 streamKey++, Vue 销毁旧 <img> 节点 + 创建新节点, Chrome
             看到 DOM 节点移除会关掉 keep-alive socket, 新 <img> 起新连接
             而不复用 pool, 是破"socket 卡死"的唯一可靠方式。 -->
        <img 
          v-show="activeStream === 0"
          :key="`img0-${streamKey}`"
          ref="streamImg0"
          :src="streamSrc0"
          class="w-full h-full object-contain"
          @load="onStreamReady(0)"
          @error="onStreamError(0)"
        />
        <img 
          v-show="activeStream === 1"
          :key="`img1-${streamKey}`"
          ref="streamImg1"
          :src="streamSrc1"
          class="w-full h-full object-contain"
          @load="onStreamReady(1)"
          @error="onStreamError(1)"
        />
        
        <!-- 检测框覆盖层 -->
        <canvas 
          ref="detectionCanvas"
          class="absolute top-0 left-0 w-full h-full pointer-events-none"
        ></canvas>
        
        <!-- 运行状态指示 -->
        <div v-if="isDetecting" class="absolute top-4 right-4 bg-green-600/90 text-white px-6 py-2 rounded shadow-lg text-lg font-bold animate-pulse">
          检测中
        </div>
        <div v-else-if="isRunning && !isDetecting" class="absolute top-4 right-4 bg-yellow-600/90 text-white px-6 py-2 rounded shadow-lg text-lg font-bold">
          待机中
        </div>
        <div v-else class="absolute top-4 right-4 bg-gray-600/90 text-white px-6 py-2 rounded shadow-lg text-lg font-bold">
          已停止
        </div>
        
        <!-- Current Project Info -->
        <div v-if="currentProject" class="absolute top-4 left-4 bg-slate-900/80 text-white px-4 py-2 rounded shadow-lg">
          <div class="text-xs text-gray-400">当前项目</div>
          <div class="font-bold">{{ currentProject.name }}</div>
          <div class="text-xs text-cyan-400">{{ logicModeText }}</div>
        </div>
        
        <!-- Video Footer Stats -->
        <div class="absolute bottom-0 left-0 right-0 bg-black/60 backdrop-blur-sm border-t border-white/10">
          <!-- 视频进度条（仅视频输入源时显示，鼠标悬停时出现） -->
          <div v-if="isVideoSource" class="px-3 pt-2 pb-1 opacity-0 group-hover:opacity-100 transition-opacity duration-300">
            <div class="flex items-center gap-3">
              <span class="text-xs text-gray-400 font-mono w-16">{{ formatVideoTime(videoInfo.currentTime) }}</span>
              <el-slider
                v-model="videoInfo.progress"
                :min="0"
                :max="1"
                :step="0.001"
                :show-tooltip="false"
                :disabled="isDetecting"
                class="flex-1 video-progress-slider"
                @mousedown="isDraggingProgress = true"
                @mouseup="handleProgressChange"
                @change="handleProgressChange"
              />
              <span class="text-xs text-gray-400 font-mono w-16 text-right">{{ formatVideoTime(videoInfo.duration) }}</span>
              <el-select 
                v-model="videoInfo.speed" 
                size="small" 
                class="w-20 video-speed-select"
                :disabled="isDetecting || videoInfo.syncMode"
                @change="handleSpeedChange"
              >
                <el-option label="0.5x" :value="0.5" />
                <el-option label="1x" :value="1" />
                <el-option label="2x" :value="2" />
                <el-option label="4x" :value="4" />
                <el-option label="8x" :value="8" />
              </el-select>
              <el-tooltip content="同步模式：逐帧检测，确保每一帧都被处理（适合分析快速动作）" placement="top">
                <el-switch
                  v-model="videoInfo.syncMode"
                  size="small"
                  :disabled="isRunning"
                  active-text="逐帧"
                  inactive-text=""
                  class="video-sync-switch"
                  @change="handleSyncModeChange"
                />
              </el-tooltip>
            </div>
            <div v-if="videoInfo.ended" class="text-center text-yellow-400 text-xs mt-1">
              视频播放完毕
            </div>
            <div v-if="videoInfo.syncMode && !videoInfo.ended" class="text-center text-cyan-400 text-xs mt-1">
              逐帧检测模式：按检测速度播放，确保每帧都被检测
            </div>
          </div>
          <!-- 状态信息 -->
          <div class="p-2 flex gap-6 text-xs text-gray-300 flex-wrap">
            <span class="flex items-center gap-2">
              <span class="w-2 h-2 rounded-full" :class="isStreaming ? 'bg-green-500' : 'bg-gray-500'"></span> 
              {{ sourceStatusText }}
            </span>
            <span v-if="systemStore.display.monitor.showFps !== false">FPS: <span class="text-cyan-400 font-mono">{{ fps }}</span></span>
            <span v-if="systemStore.display.monitor.showLatency !== false">延迟: <span class="text-cyan-400 font-mono">{{ latency }} ms</span></span>
            <span v-if="systemStore.display.monitor.showDetectionCount !== false">检测数: <span class="text-cyan-400 font-mono">{{ detectionCount }}</span></span>
            <!-- Step 8 (feat/multi-model-roi-link): 多模型 per-slot 性能快照 (仅 >=2 个 slot 时显示) -->
            <template v-if="modelStats.length >= 2">
              <span class="text-gray-500">|</span>
              <span v-for="m in modelStats" :key="m.name" class="flex items-center gap-1.5">
                <span class="w-2 h-2 rounded-sm" :style="{ backgroundColor: m.display_color || '#10b981' }"></span>
                <span class="text-gray-400">{{ m.name }}</span>
                <span class="text-cyan-400 font-mono">{{ m.fps_inference || 0 }}fps</span>
                <span v-if="!m.model_loaded" class="text-amber-400">(未加载)</span>
              </span>
            </template>
          </div>
        </div>
      </div>

      <!-- SOP流程 (Step Indicators) — non-tracking & non-per_item modes（M-2 外置 SopStepPanel） -->
      <SopStepPanel
        v-if="systemStore.display.monitor.stepStrip && steps.length > 0 && !isTrackingMode && !isPerItemMode"
        ref="sopPanelRef"
        :steps="steps"
        :step-intervals="stepIntervals"
      />

      <!-- Tracking Mode Checklist Panel -->
      <div v-else-if="isTrackingMode" class="h-44 bg-slate-900 border border-slate-700 rounded-lg overflow-hidden flex flex-col">
        <div class="bg-slate-800 px-3 py-1 border-b border-slate-700 flex-shrink-0 flex justify-between items-center">
          <span class="text-cyan-400 text-lg font-bold">{{ trackingContainerMode ? '容器清点' : '物品清点' }}</span>
          <div class="flex items-center gap-2">
            <span v-if="trackingContainerMode && trackingSettledCount > 0" class="text-[0.625rem] px-1.5 py-0.5 rounded"
              :class="trackingSettledNg > 0 ? 'bg-red-500/20 text-red-400' : 'bg-green-500/20 text-green-400'">
              已结算 {{ trackingSettledCount }} (OK:{{ trackingSettledOk }} NG:{{ trackingSettledNg }})
            </span>
            <span v-if="trackingCycleActive" class="text-xs text-green-400 animate-pulse">跟踪中...</span>
            <span v-else class="text-xs text-gray-500">等待</span>
          </div>
        </div>
        <div class="flex-1 p-2 overflow-x-auto">
          <!-- Container mode: per-box cards -->
          <div v-if="trackingContainerMode" class="flex items-stretch h-full gap-3">
            <div v-for="(box, boxDid) in trackingBoxes" :key="boxDid"
              class="flex-shrink-0 w-44 bg-slate-800 rounded-lg border p-2 flex flex-col transition-all"
              :class="box.is_complete ? 'border-green-500/70' : 'border-amber-500/70'">
              <div class="flex items-center justify-between mb-1">
                <span class="text-xs font-bold text-white">{{ boxDid }}</span>
                <span class="text-[0.625rem] px-1.5 py-0.5 rounded"
                  :class="box.is_complete ? 'bg-green-500/20 text-green-400' : 'bg-amber-500/20 text-amber-400'">
                  {{ box.is_complete ? 'OK' : '...' }}
                </span>
              </div>
              <div class="flex-1 space-y-0.5 overflow-y-auto">
                <template v-if="trackingChecklist._boxes && trackingChecklist._boxes[boxDid]">
                  <div v-for="(info, cls) in trackingChecklist._boxes[boxDid].items" :key="cls"
                    class="flex items-center justify-between text-[0.625rem] px-1 py-0.5 rounded"
                    :class="info.counted >= info.expected && info.expected > 0 ? 'bg-green-500/10 text-green-400' : 'bg-slate-700/50 text-gray-400'">
                    <span class="truncate">{{ info.display_name || cls }}</span>
                    <span class="font-mono font-bold">{{ info.counted }}<span v-if="info.expected > 0" class="text-gray-500">/{{ info.expected }}</span></span>
                  </div>
                </template>
              </div>
            </div>
            <div v-if="Object.keys(trackingBoxes).length === 0"
              class="flex items-center justify-center text-gray-500 text-sm w-full">
              等待容器出现...
            </div>
          </div>
          <!-- Normal mode: flat checklist -->
          <div v-else class="flex items-stretch h-full gap-3">
            <div v-for="(info, cls) in trackingChecklist" :key="cls"
              class="flex-shrink-0 w-36 bg-slate-800 rounded-lg border p-2 flex flex-col justify-between transition-all"
              :class="info.counted >= info.expected && info.expected > 0 ? 'border-green-500/70' : info.counted > info.expected && info.expected > 0 ? 'border-red-500/70' : 'border-slate-700'"
            >
              <div class="text-xs text-gray-400 truncate">{{ info.display_name || cls }}</div>
              <div class="text-center my-1">
                <span class="text-3xl font-bold font-mono"
                  :class="info.counted >= info.expected && info.expected > 0 ? 'text-green-400' : 'text-white'"
                >{{ info.counted }}</span>
                <span v-if="info.expected > 0" class="text-sm text-gray-500"> / {{ info.expected }}</span>
              </div>
              <div class="text-[0.625rem] text-gray-500 text-center">{{ info.prefix }}1 ~ {{ info.prefix }}{{ info.counted || '?' }}</div>
            </div>
            <div v-if="Object.keys(trackingChecklist).length === 0"
              class="flex items-center justify-center text-gray-500 text-sm w-full">
              等待物品出现...
            </div>
          </div>
        </div>
      </div>

      <!-- 原生称重投料模式专属看板 (与 SOP/Tracking/PerItem 排他, 占视频下方核心展示位) -->
      <WeighingPanel
        v-else-if="isWeighingMode"
        :channel="selectedChannel"
      />

      <!-- v3.8+ 逐件模式专属面板 (与 SOP/Tracking 排他, 占视频下方核心展示位) -->
      <PerItemPanel
        v-else-if="isPerItemMode"
        :state="perItemState"
        :channel="selectedChannel"
      />

      <!-- No Project Selected -->
      <div v-else-if="!currentProject" class="h-40 bg-slate-900 border border-slate-700 rounded-lg flex items-center justify-center text-gray-500">
        <div class="text-center">
          <el-icon :size="32" class="mb-2"><Folder /></el-icon>
          <p class="text-sm">请先在顶部选择项目</p>
        </div>
      </div>

      <!-- v3.19.x 自定义混合模式物品校验面板 (与上方 SOP 并存: 步骤看 SOP, 物品看这里) -->
      <PerItemPanel
        v-if="customMixPerItemState"
        :state="customMixPerItemState"
        :channel="selectedChannel"
        mix
      />
      <!-- 混合类型=tracking 物品校验看板（M-3 外置 CustomMixItemPanel） -->
      <CustomMixItemPanel
        v-else-if="customMixState && customMixState.mix_type === 'tracking'"
        :state="customMixState"
        :tracking-checklist="trackingChecklist"
      />

      <!-- v3.21: 包装箱结算进度 (仅当前工位有启用配置才显示, 否则不渲染/不轮询, 零差异) -->
      <PackagingFlowCard
        v-if="packagingCfgForChannel"
        :config="packagingCfgForChannel"
        :state="packagingState"
      />

      <!-- 虚拟扫码枪测试台 (仅当存在启用的包装结算配置时出现, 默认折叠; 无包装客户零差异) -->
      <VirtualScanGun
        v-if="packagingConfigs.length > 0"
        :channel-id="selectedChannel"
      />

      <!-- v3.5.0: 周期性强制动作进度（独立链，与上方 SOP/Tracking 不冲突） -->
      <div v-if="periodicActions.length > 0" class="bg-slate-900 border border-slate-700 rounded-lg overflow-hidden">
        <div class="bg-slate-800 px-3 py-1 border-b border-slate-700 flex items-center justify-between">
          <span class="text-cyan-400 text-base font-bold">周期性强制动作</span>
          <div class="flex items-center gap-2">
            <span class="text-[0.625rem] text-gray-500">每 N 轮必做（清洁/上油/校准...）</span>
            <button @click="resetAllPeriodicActions"
                    class="bg-slate-700 hover:bg-cyan-600 text-white px-2 py-0.5 rounded text-[0.625rem] font-bold transition-colors">
              全部重置
            </button>
          </div>
        </div>
        <div class="p-2 grid gap-2" :class="periodicActions.length === 1 ? 'grid-cols-1' : 'grid-cols-2'">
          <div v-for="rule in periodicActions" :key="rule.id"
               class="px-3 py-2 rounded border flex items-center gap-3"
               :class="rule.state === 'overdue' ? 'border-red-500 bg-red-500/10' :
                       rule.state === 'due' ? 'border-yellow-500 bg-yellow-500/10' :
                       'border-slate-700 bg-slate-800/50'">
            <span class="text-sm font-bold text-white truncate flex-1">{{ rule.name }}</span>
            <div class="flex items-center gap-2 text-xs whitespace-nowrap">
              <!-- 次数维度 (interval > 0 才显示) -->
              <template v-if="rule.interval > 0">
                <span class="text-gray-400">{{ rule.counter }} / {{ rule.interval }}</span>
                <span v-if="rule.count_state === 'ok'" class="text-cyan-400">还有 {{ rule.remaining }} 轮</span>
                <span v-else-if="rule.count_state === 'due'" class="text-yellow-400 animate-pulse">
                  请执行 {{ (rule.trigger_labels || []).join(' / ') }}
                </span>
                <span v-else-if="rule.count_state === 'overdue'" class="text-red-400 font-bold animate-pulse">
                  超期 {{ -rule.remaining }} 轮
                </span>
              </template>
              <!-- v3.7.4: 时间维度 (time_interval_seconds > 0 才显示) -->
              <template v-if="rule.time_interval_seconds > 0">
                <span v-if="rule.interval > 0" class="text-gray-600">|</span>
                <span class="text-gray-400">⏱ {{ rule.time_elapsed_seconds }}s / {{ rule.time_interval_seconds }}s</span>
                <span v-if="rule.time_state === 'ok'" class="text-cyan-400">
                  还有 {{ formatRemainingTime(rule.time_remaining_seconds) }}
                </span>
                <span v-else-if="rule.time_state === 'due'" class="text-yellow-400 animate-pulse">
                  时间到期
                </span>
                <span v-else class="text-red-400 font-bold animate-pulse">
                  超期 {{ formatRemainingTime(-rule.time_remaining_seconds) }}
                </span>
              </template>
              <!-- 没配 trigger_labels 引导文案 (整体 due/overdue 时, 上面分维度都没显示就用这个) -->
              <span v-if="rule.state !== 'ok' && (rule.interval || 0) === 0 && (rule.time_interval_seconds || 0) === 0"
                    class="text-yellow-400">请执行 {{ (rule.trigger_labels || []).join(' / ') }}</span>
            </div>
            <div class="w-32 h-2 bg-slate-900 rounded overflow-hidden flex-shrink-0">
              <div class="h-full transition-all"
                   :class="rule.state === 'overdue' ? 'bg-red-500' :
                           rule.state === 'due' ? 'bg-yellow-500' :
                           'bg-cyan-500'"
                   :style="{ width: Math.min(100, getPeriodicProgress(rule) * 100) + '%' }"></div>
            </div>
            <button @click="resetSinglePeriodicAction(rule)"
                    class="bg-slate-700 hover:bg-cyan-600 text-white px-2 py-0.5 rounded text-[0.625rem] font-bold transition-colors flex-shrink-0">
              重置
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- RIGHT COLUMN: DASHBOARD Stats -->
    <div class="col-span-5 flex flex-col gap-3 min-h-0">

      <!-- Top Row: Stats Counters (Dynamic) -->
      <div v-if="systemStore.display.monitor.statsPanel" class="bg-slate-900 border border-slate-700 rounded-lg p-3 flex flex-col">
        <template v-if="currentProject && counters.length > 0">
          <!-- 三个系统内置计数器并排 -->
          <div class="grid grid-cols-3 gap-2 mb-2">
            <div v-for="counter in builtinCounters" :key="counter.name"
              class="flex flex-col items-center justify-center bg-slate-800/50 p-2 rounded-lg"
            >
               <div class="text-sm text-gray-300 mb-0.5">{{ getCounterDisplayName(counter.name) }}</div>
               <div class="font-mono"
                 :class="counter.name === '合格总数' ? 'text-2xl text-green-400' : counter.name === '不良总数' ? 'text-2xl text-red-500' : 'text-2xl text-white'"
               >
                 {{ counter.value }}
               </div>
            </div>
          </div>
          
          <!-- 其他计数器（NG步骤+自定义），可滚动 -->
          <div v-if="extraCounters.length > 0" class="grid grid-cols-3 gap-2 border-t border-slate-800 pt-2 overflow-y-auto max-h-24">
             <div v-for="counter in extraCounters" :key="counter.name"
               class="flex flex-col items-center justify-center bg-slate-800/30 p-1.5 rounded"
             >
                <span class="text-sm text-gray-500 truncate w-full text-center">{{ counter.name }}</span>
                <span class="font-bold text-xl" :class="getCounterColor(counter.name)">{{ counter.value }}</span>
             </div>
          </div>
        </template>
        <div v-else class="flex flex-col items-center justify-center h-full text-gray-500 py-4">
          <el-icon :size="32" class="mb-2"><Folder /></el-icon>
          <span class="text-xs">请先选择项目以查看统计数据</span>
        </div>
      </div>

      <!-- MES 信息条 -->
      <div v-if="displayWorkpiece || mesData?.order || mesData?.warn_no_barcode || workpieceOverride === null || isScanDisabledFor(selectedChannel)" class="bg-slate-900 border border-cyan-800/50 rounded-lg px-3 py-2 flex items-center gap-6 text-sm">
        <div v-if="!isScanDisabledFor(selectedChannel) && displayWorkpiece" class="flex items-center gap-2">
          <span class="text-cyan-400 font-bold">工件:</span>
          <span class="font-mono text-white">{{ displayWorkpiece.serial_no }}</span>
          <el-tag :type="displayWorkpiece.status === 'ok' ? 'success' : displayWorkpiece.status === 'ng' ? 'danger' : displayWorkpiece.status === 'inspecting' ? 'warning' : 'info'" size="small">
            {{ { registered: '已登记', queued: '排队', inspecting: '检测中', ok: '合格', ng: '不良' }[displayWorkpiece.status] || displayWorkpiece.status }}
          </el-tag>
          <span class="text-gray-400 text-xs">第{{ displayWorkpiece.inspection_count }}次</span>
        </div>
        <div v-if="mesData.order" class="flex items-center gap-2">
          <span class="text-cyan-400 font-bold">工单:</span>
          <span class="text-white">{{ mesData.order.order_no }}</span>
          <span class="text-gray-400 text-xs">{{ mesData.order.completed_qty }}/{{ mesData.order.planned_qty }}</span>
          <span :class="(mesData.order.yield_rate ?? 0) >= 95 ? 'text-green-400' : 'text-yellow-400'" class="text-xs">
            良率 {{ mesData.order.yield_rate ?? '-' }}%
          </span>
        </div>
        <!-- 开工任务要素 (按入站配置 task_info_display 逐项显示; 全关时无任何标签) -->
        <div v-for="it in getTaskInfoItemsFor(selectedChannel)" :key="it.label" class="flex items-center gap-2">
          <span class="text-cyan-400 font-bold">{{ it.label }}:</span>
          <span class="text-white truncate max-w-[160px]" :title="it.value">{{ it.value }}</span>
        </div>
        <div v-if="!isScanDisabledFor(selectedChannel) && hasScannerFor(selectedChannel) && mesData.warn_no_barcode" class="warn-no-barcode-blink flex items-center gap-2 bg-yellow-600/30 border border-yellow-500 rounded px-3 py-1">
          <span class="text-yellow-300 font-bold text-base">⚠ 未绑码</span>
          <span class="text-yellow-200 text-sm">请扫描工件条码</span>
        </div>
        <div v-else-if="!isScanDisabledFor(selectedChannel) && !displayWorkpiece && !mesData?.order" class="text-gray-500 text-xs">等待扫码...</div>
        <div v-if="isScanDisabledFor(selectedChannel)" class="flex items-center gap-2 text-gray-400 italic">
          <span class="text-base">⛔ 扫码已禁用</span>
          <span class="text-xs">所有联动工位走项目原生结算 (跟踪→全部消失，容器→箱子离开)</span>
        </div>
        <!-- 清除本次扫码 + 禁用/启用扫码 -->
        <div class="ml-auto flex items-center gap-2">
          <el-tooltip
            v-if="!isScanDisabledFor(selectedChannel)"
            :content="displayWorkpiece && displayWorkpiece.status === 'inspecting'
              ? '本次工件已开始检测，点击可作废本次检测、回到等待扫码状态'
              : '清除待检/扫码状态，让操作员重扫一次条码'"
            placement="top"
          >
            <el-button
              size="small"
              type="warning"
              plain
              @click="clearPendingScan(selectedChannel)"
            >
              清除本次扫码
            </el-button>
          </el-tooltip>
          <el-tooltip
            :content="isScanDisabledFor(selectedChannel)
              ? '点击启用扫码：扫码器恢复工作，按扫码器配置的结算方式 (scan_pair / mid_cycle 等) 工作'
              : '点击禁用扫码：扫码器熄灯，所有联动工位回退到项目原生结算 (跟踪 → 全部消失，容器 → 箱子离开)'"
            placement="top"
          >
            <el-button
              size="small"
              :type="isScanDisabledFor(selectedChannel) ? 'success' : 'danger'"
              plain
              :loading="scannerDisableStore.toggling"
              @click="toggleScanDisableFor(selectedChannel)"
            >
              {{ isScanDisabledFor(selectedChannel) ? '启用扫码' : '禁用扫码' }}
            </el-button>
          </el-tooltip>
        </div>
        <!-- 额外字段输入 (外部 MES 动态字段) -->
        <div v-if="extraFieldsSchema.length" class="flex items-center gap-2 border-l border-cyan-800/50 pl-4">
          <div v-for="f in extraFieldsSchema" :key="f.key" class="flex items-center gap-1">
            <span class="text-gray-400 text-xs">{{ f.label || f.key }}:</span>
            <el-input
              v-model="extraFieldValues[f.key]"
              :type="f.type === 'number' ? 'number' : 'text'"
              :placeholder="f.default || ''"
              size="small"
              class="w-24"
              @change="submitExtraFields"
            />
          </div>
        </div>
      </div>

      <!-- Middle: Charts + NG Ranking -->
      <div class="h-52 grid grid-cols-3 gap-2">
         <!-- Pie Chart -->
         <div v-if="systemStore.display.monitor.defectChart" class="bg-slate-900 border border-slate-700 rounded-lg p-2 relative">
            <h3 class="text-cyan-400 text-base font-bold absolute top-1.5 left-2">良品/不良统计</h3>
            <div ref="defectChartRef" class="w-full h-full"></div>
         </div>
         <!-- Yield Rate Gauge -->
         <div v-if="systemStore.display.monitor.capacityChart" class="bg-slate-900 border border-slate-700 rounded-lg p-2 relative">
            <h3 class="text-cyan-400 text-base font-bold absolute top-1.5 left-2">合格率</h3>
            <div ref="capacityGaugeRef" class="w-full h-full"></div>
         </div>
         <!-- v3.8+: per_item 模式专属 - 逐件实时反馈 (排他 NG TOP3) -->
         <div v-if="isPerItemMode" class="bg-slate-900 border border-slate-700 rounded-lg p-2 flex flex-col">
            <div class="flex items-center justify-between mb-1">
              <h3 class="text-cyan-400 text-base font-bold">逐件实时反馈</h3>
              <span v-if="perItemRemaining > 0" class="text-[0.625rem] px-1.5 py-0.5 rounded bg-red-500/20 text-red-400 font-bold animate-pulse">
                漏 {{ perItemRemaining }} 颗
              </span>
              <span v-else-if="perItemTotal > 0" class="text-[0.625rem] px-1.5 py-0.5 rounded bg-green-500/20 text-green-400 font-bold">
                全部完成 ✓
              </span>
              <span v-else class="text-[0.625rem] text-gray-500">等待中</span>
            </div>

            <!-- 还差 X 颗 - 巨大数字 (第一反馈) -->
            <div v-if="perItemTotal > 0" class="flex items-baseline gap-1.5 mb-0.5">
              <span class="text-gray-400 text-xs">还差</span>
              <span class="font-mono font-bold leading-none"
                    :class="perItemRemaining > 0 ? 'text-red-400 text-4xl' : 'text-green-400 text-4xl'">
                {{ perItemRemaining }}
              </span>
              <span class="text-gray-500 text-base">颗</span>
              <span class="text-gray-500 text-xs ml-auto font-mono">
                {{ perItemCovered }}/{{ perItemTotal }}
              </span>
            </div>

            <!-- 已耗时 + 当前进度 -->
            <div v-if="perItemTotal > 0" class="text-[0.625rem] text-gray-400 mb-1.5 flex items-center gap-2">
              <span>已用 <span class="text-cyan-400 font-mono font-bold">{{ perItemElapsedSec.toFixed(1) }}s</span></span>
              <span v-if="perItemCovered > 0">·</span>
              <span v-if="perItemCovered > 0">第 <span class="text-white font-mono font-bold">{{ perItemCovered }}</span> 颗</span>
            </div>

            <!-- 未覆盖 id chip 列表 -->
            <div class="flex-1 min-h-0 flex flex-col">
              <div v-if="perItemMissingIds.length > 0" class="text-[0.625rem] text-gray-500 mb-0.5">
                未覆盖物件:
              </div>
              <div v-if="perItemMissingIds.length > 0" class="flex flex-wrap gap-1 overflow-y-auto custom-scrollbar">
                <span v-for="id in perItemMissingIds.slice(0, 16)" :key="'miss-'+id"
                      class="bg-red-950/50 border border-red-700/60 text-red-300 px-1.5 py-0.5 rounded text-[0.6875rem] font-mono font-bold">
                  #{{ id }}
                </span>
                <span v-if="perItemMissingIds.length > 16"
                      class="text-gray-500 text-[0.625rem] flex items-center px-1">
                  +{{ perItemMissingIds.length - 16 }}
                </span>
              </div>
              <div v-else-if="perItemTotal > 0 && perItemRemaining === 0"
                   class="flex items-center justify-center h-full text-green-400 text-sm font-bold">
                ✓ 已全部覆盖
              </div>
              <div v-else class="flex items-center justify-center h-full text-gray-600 text-xs">
                等待识别物件…
              </div>
            </div>
         </div>

         <!-- NG Step Ranking (非 per_item 模式) -->
         <div v-else-if="systemStore.display.monitor.ngTop3 !== false" class="bg-slate-900 border border-slate-700 rounded-lg p-2 flex flex-col">
            <div class="flex items-center justify-between mb-1.5">
              <h3 class="text-cyan-400 text-base font-bold">NG步骤TOP3</h3>
              <span class="text-xs text-gray-500 cursor-pointer hover:text-cyan-400 select-none"
                @click="toggleNgTopMode()"
              >{{ systemStore.display.monitor.ngTopDisplayMode === 'percentage' ? '百分比' : '次数' }}</span>
            </div>
            <div class="flex-1 overflow-y-auto space-y-1">
              <div v-if="ngStepRanking.length === 0" class="flex items-center justify-center h-full text-gray-600 text-base">
                暂无数据
              </div>
              <div v-for="(item, idx) in ngStepRanking.slice(0, 3)" :key="item.step"
                class="flex items-center gap-2 bg-slate-800/50 px-2 py-1.5 rounded"
              >
                <span class="text-lg font-bold w-6 text-center text-white">{{ idx + 1 }}</span>
                <span class="flex-1 text-base text-gray-300 truncate">{{ item.step }}</span>
                <span class="text-lg font-bold text-white">{{ systemStore.display.monitor.ngTopDisplayMode === 'count' ? item.count : item.rate.toFixed(1) + '%' }}</span>
              </div>
            </div>
         </div>
      </div>

      <!-- Bottom: Detail Table & Controls -->
      <div v-if="systemStore.display.monitor.stepTable" class="flex-1 bg-slate-900 border border-slate-700 rounded-lg overflow-hidden flex flex-col">
         <div class="bg-slate-800 px-3 py-2 flex justify-between items-center border-b border-slate-700">
            <span class="text-cyan-400 text-lg font-bold">步骤统计</span>
            <span class="text-sm bg-slate-700 px-2 py-0.5 rounded text-gray-300">CT: {{ displayCT }}s</span>
         </div>
         <div class="flex-1 overflow-auto">
            <table class="w-full text-left text-sm">
               <thead class="bg-slate-800 text-gray-400 top-0 sticky">
                  <tr>
                     <th v-if="systemStore.display.monitor.stepTableColumns?.showNo !== false" class="px-2 py-1.5">No</th>
                     <th v-if="systemStore.display.monitor.stepTableColumns?.showStep !== false" class="px-2 py-1.5">步骤</th>
                     <th v-if="systemStore.display.monitor.stepTableColumns?.showStatus !== false" class="px-2 py-1.5">状态</th>
                     <th v-if="systemStore.display.monitor.stepTableColumns?.showPt !== false" class="px-2 py-1.5">PT/s</th>
                     <th v-if="systemStore.display.monitor.stepTableColumns?.showResult !== false" class="px-2 py-1.5">结果</th>
                  </tr>
               </thead>
               <tbody class="divide-y divide-slate-800 text-gray-300">
                  <tr v-for="(row, i) in tableData" :key="i" 
                    class="hover:bg-slate-800/50"
                    :class="row.status === 'completed' ? 'bg-green-800/30' : ''"
                  >
                     <td v-if="systemStore.display.monitor.stepTableColumns?.showNo !== false" class="px-2 py-1.5">{{ i + 1 }}</td>
                     <td v-if="systemStore.display.monitor.stepTableColumns?.showStep !== false" class="px-2 py-1.5">{{ row.step }}</td>
                     <td v-if="systemStore.display.monitor.stepTableColumns?.showStatus !== false" class="px-2 py-1.5">
                       <span :class="row.status === 'completed' ? 'text-white' : 'text-gray-500'">
                         {{ row.status === 'completed' ? '已检测' : '待检测' }}
                       </span>
                     </td>
                     <!--
                       v3.7.x: PT 列硬性守门 — "待检测" (pending) 状态下强制显示 "--"。
                       数据源 cycleSumStepDurations 的 race / 历史档可能在 polling 间隔里残留旧值,
                       但视觉上只要这一步还没进入本周期, PT 就不应该显示任何数字。
                       跟踪模式跳过守门 (其 PT 字典本就为空, 不会有残留)。
                     -->
                     <!-- v3.13 M2.2b: 步骤表格"耗时"单元格 slot — 客户插件可换三档颜色 / 文案 -->
                    <td v-if="systemStore.display.monitor.stepTableColumns?.showPt !== false" class="px-2 py-1.5 text-white font-mono">
                       <TjSlot
                         name="monitor.step-cell.duration"
                         :step="row"
                         :label="row.label"
                         :status="row.status"
                         :is-tracking-mode="isTrackingMode"
                         :pt-text="(isTrackingMode || row.status === 'completed') ? formatStepPT(row.label) : '--'"
                       >
                         {{ (isTrackingMode || row.status === 'completed') ? formatStepPT(row.label) : '--' }}
                       </TjSlot>
                     </td>
                     <!-- v3.13 M2.2b: 步骤表格"结果"单元格 slot — 客户可隐藏步骤级红色, 用 ui_hidden 整列隐藏走 td 外层 -->
                     <td v-if="systemStore.display.monitor.stepTableColumns?.showResult !== false" class="px-2 py-1.5">
                       <TjSlot
                         name="monitor.step-cell.status"
                         :step="row"
                         :label="row.label"
                         :status="row.status"
                         :cycle-result="row.cycleResult"
                         :is-tracking-mode="isTrackingMode"
                       >
                         <!--
                           v3.7.x: 结果(OK/NG) 必须等 PT 时间出现后才显示。
                           v3.8.x (三次修订): 守门口径跟 PT 列对齐 — 必须 row.status === 'completed'。
                           旧守门 formatStepPT() !== '--' 会被 in-flight 实时累加值打穿:
                           步骤还在画面里 (status=active) 时, PT 列被守门挡显示 '--',
                           但 formatStepPT() 因为有 in-flight fallback 返回真实数字,
                           结果列守门直接放行 → 客户看到 "状态=待检测 + PT=-- + 结果=OK" 视觉割裂.
                           现在两道门同步: status='completed' 才允许显示 PT 和结果, 严格同帧出现.
                           例外：跟踪模式底层不写 step_durations, PT 永远是 '--',
                                该模式下跳过守门保持原"counted > 0 → OK"语义.
                         -->
                         <template v-if="isTrackingMode || row.status === 'completed'">
                           <span v-if="row.cycleResult === 'ok'" class="text-green-400">OK</span>
                           <span v-else-if="row.cycleResult === 'ng'" class="text-red-500">NG</span>
                           <span v-else class="text-gray-500">--</span>
                         </template>
                         <span v-else class="text-gray-500">--</span>
                       </TjSlot>
                     </td>
                  </tr>
               </tbody>
            </table>
         </div>
         
         <!-- 会话 ID 输入框（v3.6.2 新增, 客户用作业务标识写到 DB + 模板 {{ session.name }}） -->
         <div class="px-2 pt-2 pb-1 bg-slate-950 border-t border-slate-800">
            <el-input
              v-model="sessionName"
              size="small"
              placeholder="会话 ID（可选, 如 LINE3-NIGHT-20260510）"
              clearable
              maxlength="64"
              :disabled="isDetecting || isOperating"
              :class="sessionNameError ? 'session-name-error' : ''"
            />
            <div v-if="sessionNameError" class="text-xs text-red-400 mt-1">{{ sessionNameError }}</div>
         </div>

         <!-- Control Buttons -->
         <div class="p-2 bg-slate-950 flex gap-2">
            <button 
              @click="startDetection" 
              :disabled="!currentProject || isDetecting || isOperating || !!sessionNameError"
              class="flex-1 bg-emerald-500 hover:bg-emerald-400 disabled:bg-gray-600 disabled:cursor-not-allowed text-white py-2.5 rounded text-lg font-bold shadow transition-colors"
            >
              {{ isOperating && !isDetecting ? '启动中...' : '开始' }}
            </button>
            <button 
              @click="stopDetectionHandler"
              :disabled="!isRunning || isOperating"
              class="flex-1 bg-red-500 hover:bg-red-400 disabled:bg-gray-600 disabled:cursor-not-allowed text-white py-2.5 rounded text-lg font-bold shadow transition-colors"
            >
              {{ isOperating && isRunning ? '停止中...' : '停止' }}
            </button>
            <button 
              @click="standbyHandler"
              :disabled="!isDetecting || isOperating"
              class="flex-1 bg-yellow-600 hover:bg-yellow-500 disabled:bg-gray-600 disabled:cursor-not-allowed text-white py-2.5 rounded text-lg font-bold shadow transition-colors"
            >
              待机
            </button>
            <button 
              @click="resetCounters"
              :disabled="isDetecting || isOperating"
              class="flex-1 bg-cyan-500 hover:bg-cyan-400 disabled:bg-gray-600 disabled:cursor-not-allowed text-white py-2.5 rounded text-lg font-bold shadow transition-colors"
            >
              清零
            </button>
         </div>
      </div>

    </div>
    
    <!-- 事件提示框容器 - 不同位置 -->
    <template v-for="position in ['top-right', 'top-left', 'bottom-right', 'bottom-left', 'center']" :key="position">
      <div 
        class="fixed z-50 pointer-events-none flex flex-col gap-2"
        :class="getPositionClass(position)"
      >
        <transition-group name="toast">
          <div 
            v-for="toast in activeToasts.filter(t => t.position === position)" 
            :key="toast.id"
            class="px-6 py-4 rounded-xl shadow-2xl text-white font-bold pointer-events-auto transform transition-all duration-300 text-center"
            :style="{ 
              backgroundColor: toast.color,
              fontSize: (toast.fontSize / 16) + 'rem'
            }"
          >
            <div class="flex items-center gap-3 justify-center">
              <el-icon :size="24">
                <component :is="toast.icon" />
              </el-icon>
              <div>
                <div class="font-bold">{{ toast.title }}</div>
                <div v-if="toast.subtitle" class="text-sm opacity-80">{{ toast.subtitle }}</div>
              </div>
            </div>
          </div>
        </transition-group>
      </div>
    </template>

    <!-- v3.9.x 事件人工确认 全屏覆盖层 -->
    <!-- 触发条件: 任一通道 pendingAck.active=true → 弹覆盖层, 显示需确认的工位编号 -->
    <!-- 多通道场景: 只显示一个工位 (优先当前选中, 其次最早阻塞的), 工人逐个确认 -->
    <div
      v-if="pendingAckDisplay"
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
            工位 {{ pendingAckDisplay.channel + 1 }}
          </span>
        </div>

        <div class="px-5 py-4 space-y-3 text-sm">
          <div class="flex items-center gap-3">
            <span class="text-gray-400 w-20 shrink-0">触发事件</span>
            <span class="text-white font-bold">{{ pendingAckDisplay.eventName || '(未命名事件)' }}</span>
          </div>
          <div class="flex items-start gap-3">
            <span class="text-gray-400 w-20 shrink-0">触发原因</span>
            <span class="text-gray-200 break-all">{{ pendingAckDisplay.reason || '—' }}</span>
          </div>
          <div class="flex items-center gap-3">
            <span class="text-gray-400 w-20 shrink-0">已等待</span>
            <span class="text-cyan-300 font-mono">{{ pendingAckWaitedSec }} 秒</span>
            <template v-if="pendingAckDisplay.timeoutSec > 0">
              <span class="text-gray-500">/</span>
              <span class="text-amber-300 font-mono">{{ pendingAckRemainSec }} 秒后自动确认</span>
            </template>
          </div>
          <!-- v3.23 缺步骤延迟落账挂起: 缺项明细 -->
          <div v-if="pendingAckDisplay.remediation" class="flex items-start gap-3">
            <span class="text-gray-400 w-20 shrink-0">缺步骤</span>
            <span class="text-rose-300 font-bold break-all">
              {{ (pendingAckDisplay.remediation.missing || []).join('、') || '（未解析出具体步骤）' }}
            </span>
          </div>
          <div class="text-xs text-gray-500 bg-slate-950/60 rounded p-2 border-l-2 border-amber-700/50 leading-relaxed">
            <template v-if="pendingAckDisplay.remediation">
              本件缺步骤被<span class="text-amber-300">延迟落账</span>（还没记 OK/NG）。工人补做后点「补步骤」直接判合格；确认确实漏做点「认 NG」落账；想整件重做点「重做」。
            </template>
            <template v-else>
              确认后清当前周期运行时（步骤序列、识别状态），<span class="text-amber-300">合格 / 不合格 / 自定义计数器累计值不变动</span>。如果有多个工位都在等确认，这里会按顺序依次显示。
            </template>
          </div>
        </div>

        <!-- v3.23 挂起态: 补步骤/认NG/重做 三选一; 普通确认: 单"重做"按钮 -->
        <div class="px-5 py-4 bg-slate-950 border-t border-slate-800 flex items-center justify-end gap-3 flex-wrap">
          <span v-if="pendingAckDisplay.acking" class="text-xs text-gray-400">提交中...</span>
          <template v-if="pendingAckDisplay.remediation">
            <el-button size="large" :loading="pendingAckDisplay.acking"
              @click="ackPendingForChannel(pendingAckDisplay.channel, 'redo')">重做本件</el-button>
            <el-button type="danger" size="large" plain :loading="pendingAckDisplay.acking"
              @click="ackPendingForChannel(pendingAckDisplay.channel, 'confirm_ng')">认 NG</el-button>
            <el-button type="success" size="large" :loading="pendingAckDisplay.acking"
              @click="ackPendingForChannel(pendingAckDisplay.channel, 'supplement_step')">补步骤 — 判合格</el-button>
          </template>
          <el-button
            v-else
            type="warning"
            size="large"
            :loading="pendingAckDisplay.acking"
            @click="ackPendingForChannel(pendingAckDisplay.channel)"
          >
            我已确认 — 重做工位 {{ pendingAckDisplay.channel + 1 }}
          </el-button>
        </div>
      </div>
    </div>

    <!-- v3.23 借管理员密码授权确认: 无 ack 权限的操作员点确认被 403 → 弹此窗 -->
    <!-- 只校验一次管理员账密 + 权限解除阻塞, 不创建登录会话、不改当前登录身份 -->
    <!-- 取消 = 关本窗回到上面的人工确认覆盖层 (pendingAck 未清, 覆盖层仍在) -->
    <div
      v-if="elevateDialog.visible"
      class="absolute inset-0 z-[70] bg-black/80 backdrop-blur-sm flex items-center justify-center p-4"
      @click.stop
    >
      <div class="w-full max-w-md bg-slate-900 border-2 border-cyan-500 rounded-xl shadow-2xl flex flex-col overflow-hidden">
        <div class="px-5 py-3 bg-gradient-to-r from-cyan-700 to-cyan-900 flex items-center gap-3">
          <el-icon :size="26" class="text-cyan-100"><Lock /></el-icon>
          <div class="flex-1">
            <div class="text-white font-bold text-lg">借管理员密码授权确认</div>
            <div class="text-cyan-100 text-xs">当前账号无人工确认权限，请管理员授权本次确认（不改变当前登录身份）</div>
          </div>
          <span class="bg-slate-900/60 px-2 py-0.5 rounded text-cyan-100 text-xs font-bold">
            工位 {{ elevateDialog.channel + 1 }}
          </span>
        </div>

        <div class="px-5 py-4 space-y-3">
          <el-input
            v-model="elevateDialog.username"
            placeholder="管理员账号"
            size="large"
            clearable
            @keyup.enter="submitElevatedAck"
          >
            <template #prefix><el-icon><User /></el-icon></template>
          </el-input>
          <el-input
            v-model="elevateDialog.password"
            type="password"
            placeholder="管理员密码"
            size="large"
            show-password
            @keyup.enter="submitElevatedAck"
          >
            <template #prefix><el-icon><Lock /></el-icon></template>
          </el-input>
        </div>

        <div class="px-5 py-4 bg-slate-950 border-t border-slate-800 flex items-center justify-end gap-3">
          <el-button size="large" @click="elevateDialog.visible = false">取消</el-button>
          <el-button
            type="primary"
            size="large"
            :loading="elevateDialog.submitting"
            @click="submitElevatedAck"
          >
            授权并确认
          </el-button>
        </div>
      </div>
    </div>

  <!-- 录像异常入口+详情面板（M-1 外置, 原同构块消重） -->
  <RecordingFailureOverlay
    v-model:visible="showRecordingFailurePanel"
    :rows="recordingFailureRows"
    :loading="recordingFailureLoading"
    @clear="clearRecordingFailures" />
  </div>

  <!-- v3.13 M2.2b: monitor.layout.footer slot — 客户插件可在视频区底部叠加全局状态条 / 通知 / 控制按钮.
       默认不渲染任何内容 (主程序原行为, 字节级零差异). -->
  <component
    v-if="layoutFooterOverride"
    :is="layoutFooterOverride"
    :channel-count="channelCount"
    :multi-channel-data="multiChannelData"
  />

  <!-- v3.14 RFC 11: monitor.workpiece-flow.indicator slot — 展示当前 in-flight 工件状态.
       主程序默认实现: 横幅式列表 (max 3 个, FIFO 上限). 客户插件可覆盖整段展示.
       后端 /api/v1/workpiece-flows/{id}/state 提供轮询数据源. -->
  <TjSlot
    name="monitor.workpiece-flow.indicator"
    :channel-count="channelCount"
    :selected-channel="selectedChannel"
  />

  <!-- 外部生产管控系统「在途报警」持续横幅: 报警推给中控后一直挂屏顶, 中控回推消除命令即自动撤.
       惰性: 后端未配报警台账 → 无数据 → 永不出现 (字节级零打扰)。 -->
  <ExternalAlarmBanner />
</template>

<script setup>
import { onMounted, onUnmounted, ref, watch, nextTick, computed, h } from 'vue';
import * as echarts from 'echarts';
import { useProjectStore } from '@/store/useProjectStore';
import { useSystemStore } from '@/store/useSystemStore';
import { useSourceStore } from '@/store/useSourceStore';
import { useScannerDisableStore } from '@/store/useScannerDisableStore';
import { usePluginThemeStore } from '@/store/usePluginThemeStore';
import { Check, Folder, Picture, CircleCheck, CircleClose, Warning, Lock, User } from '@element-plus/icons-vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { startDetection as apiStartDetection, stopDetection as apiStopDetection, pauseDetection, resumeDetection, standbyDetection, resumeInference, resetDetection, resetDetectionStats, resetPeriodicAction, getDetectionResults, getSourceStatus, setProjectConfig, getWorkstations, getScanPairActive, settleScanPairForStop, ackPendingEvent, ackPendingEventElevated } from '@/api/detection';
import { getModelDetail, resolveModelPath as apiResolveModelPath } from '@/api/model';
import { getProjectDetail } from '@/api/project';
import api, { getBackendHost } from '@/api/index';
import { getExtraFieldsSchema, setExtraFields, getInboundConfig } from '@/api/gateway';
import PerItemPanel from './PerItemPanel.vue';
import PackagingFlowCard from './PackagingFlowCard.vue';
import WeighingPanel from './WeighingPanel.vue';
import VirtualScanGun from './VirtualScanGun.vue';
import ExternalAlarmBanner from './ExternalAlarmBanner.vue';
import RecordingFailureOverlay from './RecordingFailureOverlay.vue';
import SopStepPanel from './SopStepPanel.vue';
import CustomMixItemPanel from './CustomMixItemPanel.vue';
import ChannelVideoCard from './ChannelVideoCard.vue';
import { createFramePump } from './framePump';
import { listPackagingFlows, getPackagingFlowState } from '@/api/packaging_flow';
import TjSlot from '@/components/TjSlot.vue';
import { dbg, dbgErr } from '@/utils/debug';

const projectStore = useProjectStore();
const systemStore = useSystemStore();
const sourceStore = useSourceStore();
const scannerDisableStore = useScannerDisableStore();
const pluginThemeStore = usePluginThemeStore();

// v3.13 M2.2b: layout slot 覆盖 (整体 layout / 底栏). 没插件时永远是 null = 走原 layout.
const layoutBodyOverride = computed(() => pluginThemeStore.getSlotComponent('monitor.layout.body'));
const layoutFooterOverride = computed(() => pluginThemeStore.getSlotComponent('monitor.layout.footer'));

/** layout.body 插件内渲染主程序 TjSlot（步骤表 PT/结果列等） */
const renderMonitorSlot = (name, attrs, defaultRender) => {
  const slotDefault = typeof defaultRender === 'function' ? defaultRender : () => defaultRender;
  return h(TjSlot, { name, ...attrs }, { default: slotDefault });
};

// v3.13.2 补丁: layout.body 插件需要的控制方法集合,
// 没插件 (layoutBodyOverride === null) 时这个对象不会被消费, 主程序行为零差异.
// 这些方法都封装了项目/模型解析 + 扫码闭环 + 报警等完整业务逻辑, 插件直接调即可.
const layoutBodyActions = computed(() => ({
  startDetectionForChannel,
  stopDetectionForChannel,
  standbyForChannel,
  resetCountersForChannel,
  formatVideoTime,
  setVideoProgressForChannel: async (ch, progress) => {
    await api.post('/source/video/progress', { progress }, { params: { channel: ch } });
    const videoRes = await api.get('/source/video/info', { params: { channel: ch } });
    if (videoRes.data.status === 'success' && multiChannelData.value[ch]) {
      multiChannelData.value[ch].videoInfo = {
        progress: videoRes.data.progress || 0,
        currentTime: videoRes.data.current_time || 0,
        duration: videoRes.data.duration || 0,
        speed: videoRes.data.speed || 1,
        ended: videoRes.data.ended || false,
      };
    }
  },
  setVideoSpeedForChannel: async (ch, speed) => {
    await api.post('/source/video/speed', { speed }, { params: { channel: ch } });
    if (multiChannelData.value[ch]?.videoInfo) {
      multiChannelData.value[ch].videoInfo.speed = speed;
    }
  },
  /** layout.body 插件用: 在 canvas 上画检测框 (与原生双工位 overlay 同一套逻辑) */
  renderDetectionOverlay: (ch, canvas) => {
    const chData = multiChannelData.value[ch];
    if (!canvas || !chData) return;
    const pollCfg = chData._pollProjectConfig || (
      chData.project
        ? { steps_config: chData.project.steps_config, pipeline_config: chData.project.pipeline_config }
        : null
    );
    drawMultiDetections(ch, canvas, chData.detections || [], chData._hiddenLabels, pollCfg);
  },
  /** layout.body 插件 <img> 加载后上报帧尺寸, 画框 letterbox 对齐 */
  setFrameNaturalSize: (ch, w, h) => {
    if (w > 0 && h > 0) multiFrameNaturalSize[ch] = { w, h };
  },
  // MES / 扫码 / CT / 步骤表列 — layout.body 插件与原生双工位对齐
  shouldShowMesBarFor: (ch) => shouldShowMesBarFor(ch),
  getDisplayWorkpieceFor: (ch) => getDisplayWorkpieceFor(ch),
  getMesDataFor: (ch) => getMesDataFor(ch),
  getTaskInfoItemsFor: (ch) => getTaskInfoItemsFor(ch),
  hasScannerFor: (ch) => hasScannerFor(ch),
  isScanDisabledFor: (ch) => isScanDisabledFor(ch),
  clearPendingScan: (ch) => clearPendingScan(ch),
  toggleScanDisableFor: (ch) => toggleScanDisableFor(ch),
  getScannerDisableToggling: () => scannerDisableStore.toggling,
  getDisplayCT: (chData) => getDisplayCT(chData),
  getStepTableColumns: () => systemStore.display?.monitor?.stepTableColumns || {},
  isStepTableEnabled: () => systemStore.display?.monitor?.stepTable !== false,
  getMonitorDisplay: () => systemStore.display?.monitor || {},
  formatStepPTForChannel: (ch, stepLabel) => {
    const chData = multiChannelData.value[ch];
    if (!chData) return '--';
    const isTrk = !!(chData.tracking && chData.tracking.enabled);
    const row = (chData.tableData || []).find(r => (r.label || r.step) === stepLabel);
    const status = row?.status;
    if (status !== 'completed' && !isTrk) return '--';
    const map = chData.cycleSumStepDurations || {};
    const v = map[stepLabel];
    if (typeof v !== 'number' || !isFinite(v)) return '--';
    return `${v.toFixed(1)}s`;
  },
  /** 步骤表 PT 列 — 与原生 <TjSlot name="monitor.step-cell.duration"> 同一契约 */
  renderStepCellDuration: ({ step, label, status, isTrackingMode, ptText, channelIdx }) => {
    const text = ptText ?? (
      (isTrackingMode || status === 'completed')
        ? (() => {
            const chData = multiChannelData.value[channelIdx];
            if (!chData) return '--';
            const map = chData.cycleSumStepDurations || {};
            const v = map[label];
            if (typeof v !== 'number' || !isFinite(v)) return '--';
            return `${v.toFixed(1)}s`;
          })()
        : '--'
    );
    return renderMonitorSlot('monitor.step-cell.duration', {
      step,
      label,
      status,
      isTrackingMode,
      ptText: text,
    }, () => text);
  },
  /** 步骤表结果列 — 与原生 <TjSlot name="monitor.step-cell.status"> 同一契约 */
  renderStepCellStatus: ({ step, label, status, cycleResult, isTrackingMode }) => {
    return renderMonitorSlot('monitor.step-cell.status', {
      step,
      label,
      status,
      cycleResult,
      isTrackingMode,
    }, () => {
      if (isTrackingMode || status === 'completed') {
        if (cycleResult === 'ok') return h('span', { class: 'text-green-400' }, 'OK');
        if (cycleResult === 'ng') return h('span', { class: 'text-red-500' }, 'NG');
        return h('span', { class: 'text-gray-500' }, '--');
      }
      return h('span', { class: 'text-gray-500' }, '--');
    });
  },
  isMonitorSlotHidden: (name) => pluginThemeStore.isSlotHidden(name),
}));

// 给 layout.body 插件构造每通道 MJPEG 流 URL 的 helper.
// 主程序内部用 startMultiStreams 拉 multipart MJPEG 到 canvas (双缓冲), 插件用简化版 <img :src=url> 直接吃就够了.
// 端点是 main.py 的 `/video_feed?channel=N` (不在 /api/v1/source 前缀下, 是顶层端点).
const buildMultiStreamUrl = (ch) => `${STREAM_HOST}/video_feed?channel=${ch}&t=${Date.now()}`;

// v3.4.2 "禁用扫码"按工位开关 helper
const isScanDisabledFor = (ch) => scannerDisableStore.isChannelDisabled(ch);
// v3.5.2: "MES → 扫码器"中如果根本没创建任何扫码器(连虚拟扫码器都没有),
// 检测中心就不该再弹"⚠ 未绑码"信息条/toast.
// 后端在 mes.scanner_present 字段透出该状态; 老后端/未启用 MES 时
// 字段缺失, 此处默认按"有"处理保持向后兼容(即原有行为不变).
const hasScannerFor = (ch) => getMesDataFor(ch)?.scanner_present !== false;
async function toggleScanDisableFor(ch) {
  if (scannerDisableStore.toggling) return;
  const targetDisabled = !isScanDisabledFor(ch);
  try {
    const res = await scannerDisableStore.toggle(ch, targetDisabled);
    const linked = (res?.linked || []).map((c) => `工位${Number(c) + 1}`).join('、');
    if (targetDisabled) {
      ElMessage.warning(`已禁用扫码 (${linked})，回退到项目原生结算`);
    } else {
      ElMessage.success(`已启用扫码 (${linked})`);
    }
  } catch (e) {
    ElMessage.error('切换扫码禁用失败：' + (e?.response?.data?.detail || e.message));
  }
}

const streamImg0 = ref(null);
const streamImg1 = ref(null);
const detectionCanvas = ref(null);

// SOP 面板（M-2 外置 SopStepPanel.vue）: 滚动容器/卡片 ref/滚动游标随组件下沉,
// 父级轮询经 sopPanelRef 驱动 scrollToCard / resetScroll
const sopPanelRef = ref(null);

// Chart Refs
const defectChartRef = ref(null);
const capacityGaugeRef = ref(null);
let pieChartInstance = null;
let gaugeChartInstance = null;

// State
const steps = ref([]);
const tableData = ref([]);
const isRunning = ref(false);   // video capture thread active
const isDetecting = ref(false); // inference active (subset of isRunning)
const isPaused = ref(false);    // fully paused (camera released, model kept)
const isOperating = ref(false); // async guard for start/stop/standby buttons

// 客户自定义会话标识（开始检测时传给后端 → 写入 DetectionSession.name）
const sessionName = ref(localStorage.getItem('last_session_name') || '');
const sessionNameError = ref('');
const _SESSION_NAME_FORBIDDEN = /[\\/:*?"<>|\r\n\t]/;
const validateSessionName = (val) => {
  if (!val) { sessionNameError.value = ''; return true; }
  if (_SESSION_NAME_FORBIDDEN.test(val)) {
    sessionNameError.value = '不能含 / \\ : * ? " < > | 等字符';
    return false;
  }
  if (val.length > 64) {
    sessionNameError.value = '最长 64 个字符';
    return false;
  }
  sessionNameError.value = '';
  return true;
};
watch(sessionName, (val) => {
  validateSessionName(val);
  if (val) {
    localStorage.setItem('last_session_name', val);
  } else {
    localStorage.removeItem('last_session_name');
  }
});

watch(isDetecting, (newVal) => {
  systemStore.setDetecting(newVal);
});
const isStreaming = ref(false);
const fps = ref(0);
const latency = ref(0);
// Step 8 (feat/multi-model-roi-link): 多模型运行时性能快照, 来自 /detection/results
// 的 models[] 字段. 单模型时仅含 main 一项, 多模型时含全部 slot.
const modelStats = ref([]);
// 多通道版本: { [channelId]: [{name, fps_inference, latency, ...}] }
const channelModelStats = ref({});
// v3.8+: per_item 逐件模式运行时状态, 来自 /detection/results 的 per_item_state 字段.
// 非 per_item 模式或后端尚未启用时为 null. PerItemPanel 直接消费该结构.
const perItemState = ref(null);

// v3.21: 包装箱结算进度. 仅当前工位有启用配置才显示 + 轮询, 否则不渲染/不请求 (零差异).
const packagingConfigs = ref([]);   // 已启用的包装配置 (进 Monitor 时探测一次)
const packagingState = ref(null);   // 当前工位进行中工单的运行快照
let packagingTimer = null;
let packagingStateSnap = '';

const packagingCfgForChannel = computed(() =>
  packagingConfigs.value.find(c => c.enabled && c.channel_id === selectedChannel.value) || null
);

const loadPackagingConfigs = async () => {
  try {
    const { data } = await listPackagingFlows();
    packagingConfigs.value = (data.items || []).filter(c => c.enabled);
  } catch (_) {
    packagingConfigs.value = [];
  }
};

const pollPackagingState = async () => {
  const cfg = packagingCfgForChannel.value;
  if (!cfg) {
    packagingState.value = null;
    return;
  }
  try {
    const { data } = await getPackagingFlowState(cfg.id);
    packagingState.value = data.state || null;
    const st = packagingState.value;
    const snap = st
      ? `${st.order_no}|${st.box_done}/${st.box_total}|idx=${st.current_box_index}|sl=${st.current_box_sliders}/${st.items_per_box || st.tail_target || '?'}`
      : 'none';
    if (snap !== packagingStateSnap) {
      packagingStateSnap = snap;
      dbg('mes.packaging', 'Monitor 包装卡状态', `cfg=${cfg.name} ch=${cfg.channel_id} ${snap}`);
    }
  } catch (_) {
    // 静默: 进度查询失败不影响主页面
  }
};

onMounted(async () => {
  loadTaskInfoDisplay();
  await loadPackagingConfigs();
  if (packagingConfigs.value.length > 0) {
    pollPackagingState();
    packagingTimer = setInterval(pollPackagingState, 1500);
  }
});

onUnmounted(() => {
  if (packagingTimer) {
    clearInterval(packagingTimer);
    packagingTimer = null;
  }
});
// v3.19.x 自定义混合模式物品校验 (detection/results.custom_mix_state)
// 展示侧三个 computed(按标签索引/容器累加器/总数模式)已随面板外置到 CustomMixItemPanel.vue（M-3）,
// 本 ref 留父级: 轮询写入 + customMixPerItemState 适配 + 面板 props 数据源。
const customMixState = ref(null);
const cycleTime = ref(0);
const cycleTimeWithNg = ref(0);
const lastCycleTime = ref(0);
const lastCycleTimeWithNg = ref(0);
const currentCycleTime = ref(0);
const lastStepDurations = ref({});
const detectionCount = ref(0);
const currentDetections = ref([]);

// 视频播放信息（仅视频输入源时有效）
const videoInfo = ref({
  progress: 0,
  currentTime: 0,
  duration: 0,
  speed: 1,
  syncMode: false,  // 同步模式：逐帧检测
  ended: false
});
const isVideoSource = computed(() => sourceStore.sourceType === 'video');
const isHikvisionSource = computed(() => sourceStore.sourceType === 'hikvision');
const sourceStatusText = computed(() => {
  if (isVideoSource.value) {
    return videoInfo.value.ended ? '视频已结束' : '视频播放中';
  } else if (isHikvisionSource.value) {
    return isStreaming.value ? '海康相机在线' : '海康相机离线';
  } else {
    return isStreaming.value ? '摄像头在线' : '摄像头离线';
  }
});
const isDraggingProgress = ref(false);  // 是否正在拖动进度条
const isChangingSpeed = ref(false);  // 是否正在改变倍速（防止轮询覆盖）

// Double-buffered MJPEG stream: two <img> elements alternate to release
// Chromium's native decoder memory without any visible flicker.
const activeStream = ref(0);           // which img is currently visible (0 or 1)
const streamSrc0 = ref('');
const streamSrc1 = ref('');
// v3.7.x: <img> 元素的 key, watchdog 触发"强制重连"时 ++,
// Vue 会销毁旧 <img> DOM 节点 + 创建新节点, 这样 Chrome 必断
// keep-alive socket pool 里那条卡死的旧连接, 这是单纯改 src 做不到的。
const streamKey = ref(0);
let streamErrorCount = 0;
let monitorMounted = false;
let streamReconnectTimer = null;
let progressReconnectTimer = null;
let speedGuardTimer = null;

// v3.7.x MJPEG watchdog: "改完参数回 Monitor 偶尔黑屏"的根因是
// Chrome 对 multipart/x-mixed-replace 长连接的 socket 可能在 keep-alive
// pool 里复用到一条已经卡死的旧连接, <img @error> 不会触发, 导致
// 永远收不到新帧, 也没有任何兜底自愈机制。watchdog 在 connectStream 后
// 启动 N 秒首帧检测, 期间没收到首帧 -> 强制 disconnect+ ++streamKey 重建
// <img> DOM + 用新 url 重连, 这是破 Chrome socket pool 复用的唯一方式。
//
// 注: multipart/x-mixed-replace 流的 <img> 只在【首帧】触发 @load 事件,
// 后续帧通过 image decoder 直接推到 GPU, 不触发 onload。所以心跳式 watchdog
// (每帧 onload 重置 timer) 不可行 — 会误触发把好流当死流重连。中途流卡死
// 的检测改用"后端轮询 fps > 0 但前端 isStreaming=false" 来判定 (见
// pollDetectionResults 内 streamStuck 检测)。
let streamWatchdogTimer = null;
let streamConnectAttempts = 0;
const STREAM_FIRST_FRAME_TIMEOUT_MS = 5000;
const STREAM_MAX_WATCHDOG_RETRIES = 10;
// 后端报 fps > 0 但前端 isStreaming 假持续超过这个秒数 -> 判定真黑屏
const STREAM_BACKEND_FPS_MISMATCH_THRESHOLD_MS = 4000;
let streamBackendMismatchSince = 0;

// v3.8.x: swap watchdog — swap 后给 bg img 3 秒窗口接首帧, 超时强制 connectStream.
// 这是检测"中途卡死"的核心机制 (multipart 后续帧不触发 onload, 没法靠心跳).
// swap 是主动探活: 给 bg 拿新带时间戳的 url, 如果后端流仍活, bg 立刻触发 onload + 切 active;
// 如果后端推流卡死或 socket 被 Chrome pool 复用到死连接, bg 一直收不到首帧 → 此 watchdog 兜底.

const clearMonitorPendingTimers = () => {
  if (streamReconnectTimer) { clearTimeout(streamReconnectTimer); streamReconnectTimer = null; }
  if (progressReconnectTimer) { clearTimeout(progressReconnectTimer); progressReconnectTimer = null; }
  if (speedGuardTimer) { clearTimeout(speedGuardTimer); speedGuardTimer = null; }
  if (streamWatchdogTimer) { clearTimeout(streamWatchdogTimer); streamWatchdogTimer = null; }
  if (resultHoldTimer) { clearTimeout(resultHoldTimer); resultHoldTimer = null; }
  resultHoldActive = false;
  clearAllWorkpieceTimers();  // D6: 连带清掉所有工位的工件结果倒计时
};

// v3.9.x A 方案: 结果展示期 (单工位) — 周期刚结算到下一周期开始之间, 强制保留
// "最后一步 OK + 各步骤 PT 数字" 给客户工人多看 N 秒. 默认关闭, 项目级 pipeline_config
// 配置: result_hold_enabled / result_hold_seconds.
//
// 工作机制:
//   后端 current_cycle_id: number → null (周期刚结算) → armResultHold 起 timer
//   timer 期间: stepDurations / cycleSumStepDurations / stepInflightDurations 三大
//               数据源整体替换被守门跳过, _newCycleStarted 清屏分支被跳过,
//               让"上一周期的最后一步 OK + PT"稳稳停留
//   timer 到期: resultHoldActive 翻 false + 主动执行清屏 (避免下一轮还没开始时
//               PT 列继续残留上一轮数字)
//
// 与默认行为的关系: 默认 (resultHoldEnabled=false) 时不启用本 timer,
// 上一轮 OK + PT 由"cycle_id 边界 (_newCycleStarted)"驱动的清屏自然衔接.
// 只有用户主动开启展示期, 才需要这个独立的强制延迟.
//
// 注: 多工位场景 (multiChannelData) 暂不接入此机制, 单工位先验证效果再决定是否扩展.
let resultHoldTimer = null;
let resultHoldActive = false;
const armResultHold = (holdSec) => {
  if (resultHoldTimer) { clearTimeout(resultHoldTimer); resultHoldTimer = null; }
  resultHoldActive = true;
  resultHoldTimer = setTimeout(() => {
    resultHoldActive = false;
    resultHoldTimer = null;
    if (!monitorMounted) return;
    // 主动清屏: 与 _newCycleStarted 分支的目标字段对齐
    cycleSumStepDurations.value = {};
    stepCycleSegments.value = {};
    stepInflightDurations.value = {};
    stepDurations.value = {};
    stepVisibleSeconds.value = {};
    steps.value.forEach(s => {
      s.status = 'pending';
      s.cycleResult = null;
    });
    tableData.value.forEach(t => {
      t.status = 'pending';
      t.cycleResult = null;
    });
  }, Math.max(0, holdSec) * 1000);
};

const videoElement = computed(() => activeStream.value === 0 ? streamImg0.value : streamImg1.value);

// ==================== Multi-Channel State ====================
const channelCount = ref(1);
const selectedChannel = ref(0);
const multiCanvasRefs = {};
const multiChannelData = ref({});
let multiPollingTimer = null;
let multiPollingInProgress = false;
const multiActiveToasts = ref({});
const multiLastSeenSeq = {};
const multiFrameNaturalSize = {};
const multiDraggingProgress = ref({});
const showRecordingFailurePanel = ref(false);
const recordingFailureLoading = ref(false);

const resetMultiRuntimeState = (clearChannelData = false) => {
  multiPollingInProgress = false;
  selectedChannel.value = 0;
  showRecordingFailurePanel.value = false;
  multiActiveToasts.value = {};
  Object.keys(multiLastSeenSeq).forEach((k) => delete multiLastSeenSeq[k]);
  Object.keys(multiFrameNaturalSize).forEach((k) => delete multiFrameNaturalSize[k]);
  clearAllWorkpieceTimers();  // D6: 切工位/重置时清掉所有工位的工件结果倒计时
  if (clearChannelData) {
    multiChannelData.value = {};
  }
};

const recordingFailureRows = computed(() => {
  const rows = [];
  const maxChannel = Math.max(channelCount.value || 1, 1);
  for (let ch = 0; ch < maxChannel; ch++) {
    const failures = multiChannelData.value[ch]?.mes?.recording_failures || [];
    failures.forEach(item => {
      rows.push({
        ...item,
        channel_id: Number.isInteger(item?.channel_id) ? item.channel_id : ch,
      });
    });
  }
  return rows
    .sort((a, b) => (b.timestamp || 0) - (a.timestamp || 0))
    .slice(0, 120);
});

const totalRecordingFailureCount = computed(() => recordingFailureRows.value.length);

// 时间/原因文案格式化已随遮罩外置到 RecordingFailureOverlay.vue（M-1）;
// 清空动作因涉及 API 调用与轮询数据写回, 留父级, 子组件走 clear emit。
const clearRecordingFailures = async () => {
  recordingFailureLoading.value = true;
  try {
    const requests = [];
    for (let ch = 0; ch < Math.max(channelCount.value || 1, 1); ch++) {
      requests.push(
        api.post('/source/detection/recording-failures/clear', null, {
          params: { channel: ch },
        }).catch(() => null)
      );
    }
    await Promise.all(requests);
    Object.keys(multiChannelData.value).forEach((k) => {
      const idx = Number(k);
      const mes = multiChannelData.value[idx]?.mes;
      if (mes) {
        mes.recording_failures = [];
      }
    });
    ElMessage.success('录像异常列表已清空');
  } finally {
    recordingFailureLoading.value = false;
  }
};

const initMultiChannelData = (count) => {
  // v2.7.2: 每次初始化都强制重置所有状态，避免切换工位时残留旧数据
  // 1) 清理超出新 count 的通道条目（降工位场景）
  Object.keys(multiChannelData.value).forEach(k => {
    if (parseInt(k) >= count) delete multiChannelData.value[k];
  });
  Object.keys(multiActiveToasts.value).forEach(k => {
    if (parseInt(k) >= count) delete multiActiveToasts.value[k];
  });
  Object.keys(multiLastSeenSeq).forEach(k => {
    if (parseInt(k) >= count) delete multiLastSeenSeq[k];
  });
  Object.keys(multiFrameNaturalSize).forEach(k => {
    if (parseInt(k) >= count) delete multiFrameNaturalSize[k];
  });
  // 2) 为当前激活通道强制重置数据（修复 seq 基线残留导致新通道不显示 toast）
  for (let i = 0; i < count; i++) {
    multiChannelData.value[i] = {
      isRunning: false, isDetecting: false, fps: 0, latency: 0,
      total: 0, ok: 0, ng: 0, avgCycleTime: 0, avgCycleTimeWithNg: 0, projectName: '',
      steps: [], detections: [], tracking: null,
      counters: {}, allCounters: [],
      stepCounts: {}, recentEvents: [],
      currentCycleSteps: [], backupCoveredLabels: [],
      ngStepRanking: [], yieldRate: 0,
      tableData: [],
      sourceType: '',
      videoInfo: null,
      _ngStepCountMap: {},
      _processedEventIds: new Set(),
    };
    multiActiveToasts.value[i] = [];
    multiLastSeenSeq[i] = 0;
    multiFrameNaturalSize[i] = null;
  }
};

const multiVideoCanvasRefs = {};
let multiStreamRunning = false;
const multiStreamAborts = {};

const STREAM_HOST = 'http://localhost:8001';
const BOUNDARY = '--frame';
const HEADER_END = '\r\n\r\n';

const startMultiStreams = (count) => {
  stopMultiStreams();
  multiStreamRunning = true;
  for (let ch = 0; ch < count; ch++) {
    connectMjpegStream(ch);
  }
};

const connectMjpegStream = async (ch) => {
  if (!multiStreamRunning) return;
  const abort = new AbortController();
  multiStreamAborts[ch] = abort;
  try {
    const res = await fetch(`${STREAM_HOST}/video_feed?channel=${ch}`, { signal: abort.signal });
    const reader = res.body.getReader();
    const INIT_BUF_SIZE = 512 * 1024;
    let buf = new Uint8Array(INIT_BUF_SIZE);
    let bufLen = 0;

    while (multiStreamRunning) {
      const { done, value } = await reader.read();
      if (done) break;

      const needed = bufLen + value.length;
      if (needed > buf.length) {
        const newSize = Math.max(buf.length * 2, needed);
        const grown = new Uint8Array(newSize);
        grown.set(buf.subarray(0, bufLen));
        buf = grown;
      }
      buf.set(value, bufLen);
      bufLen += value.length;

      let startIdx = 0;
      const view = buf.subarray(0, bufLen);
      while (true) {
        const boundaryIdx = findBytes(view, BOUNDARY, startIdx);
        if (boundaryIdx === -1) break;
        const headerEndIdx = findBytes(view, HEADER_END, boundaryIdx);
        if (headerEndIdx === -1) break;
        const jpegStart = headerEndIdx + HEADER_END.length;
        const nextBoundary = findBytes(view, BOUNDARY, jpegStart);
        if (nextBoundary === -1) break;

        const jpegEnd = nextBoundary - 2;
        if (jpegEnd > jpegStart) {
          const jpegData = view.slice(jpegStart, jpegEnd);
          drawFrameToCanvas(ch, jpegData);
        }
        startIdx = nextBoundary;
      }
      if (startIdx > 0) {
        const remaining = bufLen - startIdx;
        buf.copyWithin(0, startIdx, bufLen);
        bufLen = remaining;
      }
      if (bufLen > 2 * 1024 * 1024) {
        const keep = 512 * 1024;
        buf.copyWithin(0, bufLen - keep, bufLen);
        bufLen = keep;
      }
    }
  } catch (e) {
    if (e.name !== 'AbortError' && multiStreamRunning) {
      console.warn(`[MJPEGStream] ch${ch} disconnected, reconnecting...`);
      setTimeout(() => connectMjpegStream(ch), 2000);
    }
  }
};

const findBytes = (buf, str, offset = 0) => {
  const target = typeof str === 'string' ? new TextEncoder().encode(str) : str;
  outer: for (let i = offset; i <= buf.length - target.length; i++) {
    for (let j = 0; j < target.length; j++) {
      if (buf[i + j] !== target[j]) continue outer;
    }
    return i;
  }
  return -1;
};

// 把一张解出的位图 (HTMLImageElement 或 ImageBitmap) 等比居中绘到工位画布
const paintToCanvas = (ch, src, natW, natH) => {
  const canvas = multiVideoCanvasRefs[ch];
  if (!canvas) return;
  const parent = canvas.parentElement;
  if (parent) {
    canvas.width = parent.clientWidth;
    canvas.height = parent.clientHeight;
  }
  multiFrameNaturalSize[ch] = { w: natW, h: natH };
  const ctx = canvas.getContext('2d');
  const cw = canvas.width, ch2 = canvas.height;
  const scale = Math.min(cw / natW, ch2 / natH);
  const dw = natW * scale;
  const dh = natH * scale;
  const dx = (cw - dw) / 2;
  const dy = (ch2 - dh) / 2;
  ctx.fillStyle = '#000';
  ctx.fillRect(0, 0, cw, ch2);
  ctx.drawImage(src, dx, dy, dw, dh);
};

// 旧路径 (默认): new Image() 逐帧解码, 行为与历史字节级一致
const drawFrameLegacy = (ch, jpegData) => {
  const canvas = multiVideoCanvasRefs[ch];
  if (!canvas) return;
  const blob = new Blob([jpegData], { type: 'image/jpeg' });
  const url = URL.createObjectURL(blob);
  const img = new Image();
  img.onload = () => {
    paintToCanvas(ch, img, img.naturalWidth, img.naturalHeight);
    URL.revokeObjectURL(url);
  };
  img.onerror = () => { try { URL.revokeObjectURL(url); } catch {} };
  img.src = url;
};

// D1 新路径 (开关开): createImageBitmap + 背压. 返回 Promise 供 framePump 判定在途。
const decodeFrameBitmap = (ch, jpegData) => {
  if (!multiVideoCanvasRefs[ch]) return Promise.resolve();
  const blob = new Blob([jpegData], { type: 'image/jpeg' });
  return createImageBitmap(blob)
    .then((bitmap) => {
      try {
        if (multiStreamRunning) paintToCanvas(ch, bitmap, bitmap.width, bitmap.height);
      } finally {
        bitmap.close();   // 立即释放解码像素, 不等 GC
      }
    });
};

const multiFramePump = createFramePump(decodeFrameBitmap);

const drawFrameToCanvas = (ch, jpegData) => {
  if (systemStore.performance?.multiChannelBitmapDecode) {
    multiFramePump.push(ch, jpegData);   // 背压: 每工位只解最新一帧
  } else {
    drawFrameLegacy(ch, jpegData);
  }
};

const stopMultiStreams = () => {
  multiStreamRunning = false;
  Object.values(multiStreamAborts).forEach(a => { try { a.abort(); } catch {} });
  Object.keys(multiStreamAborts).forEach(k => delete multiStreamAborts[k]);
  multiFramePump.reset();
};

const processChannelResult = (ch, d) => {
  const chData = multiChannelData.value[ch] || {};
  chData.isRunning = d.is_running;
  chData.isDetecting = d.is_detecting;
  chData.sourceType = d.source_type || '';
  chData.fps = d.fps || 0;
  chData.latency = d.latency || 0;
  // Step 8: per-channel 多模型快照
  if (Array.isArray(d.models)) {
    channelModelStats.value[ch] = d.models;
  }
  if (d.project_config?.project_name) {
    chData.projectName = d.project_config.project_name;
  }
  const ctrs = d.counters || {};
  // v3.1.3: 多工位下也按 channel 维护 OK/NG hold (跟单工位一致),
  // 总产量从 N → N+1 时, 把当前 mes.workpiece 用 OK/NG 标签覆盖 3.5s,
  // 然后清空让 UI 显示 "等待扫码..."
  const prevTotal = multiChannelData.value[ch]?.total ?? -1;
  const prevNg = multiChannelData.value[ch]?.ng ?? -1;
  const newTotal = ctrs['总产量'] ?? 0;
  const newNg = ctrs['不良总数'] ?? 0;
  if (channelCount.value > 1 && prevTotal >= 0 && newTotal > prevTotal) {
    const cycleIsNg = prevNg >= 0 && newNg > prevNg;
    const realWp = d.mes?.workpiece;
    if (realWp) {
      if (workpieceOverrideTimers[ch]) { clearTimeout(workpieceOverrideTimers[ch]); workpieceOverrideTimers[ch] = null; }
      if (workpieceHideTimers[ch]) { clearTimeout(workpieceHideTimers[ch]); workpieceHideTimers[ch] = null; }
      workpieceOverridesByCh.value = {
        ...workpieceOverridesByCh.value,
        [ch]: { ...realWp, status: cycleIsNg ? 'ng' : 'ok' },
      };
      workpieceHideTimers[ch] = setTimeout(() => {
        workpieceOverridesByCh.value = { ...workpieceOverridesByCh.value, [ch]: null };
        workpieceHideTimers[ch] = null;
      }, WORKPIECE_RESULT_HOLD_MS);
    }
  }
  chData.total = newTotal;
  chData.ok = ctrs['合格总数'] ?? 0;
  chData.ng = newNg;
  chData.counters = ctrs;
  chData.avgCycleTime = d.average_cycle_time || 0;
  chData.avgCycleTimeWithNg = d.average_cycle_time_with_ng || 0;
  chData.lastCycleTime = d.last_cycle_time || 0;
  chData.lastCycleTimeWithNg = d.last_cycle_time_with_ng || 0;
  chData.currentCycleTime = d.current_cycle_time || 0;
  chData.lastStepDurations = d.last_step_durations || {};
  chData.stepDurations = d.step_durations || {};
  chData.avgStepDurations = d.avg_step_durations || {};
  // v3.5.x: PT 合并档（多工位场景预存数据，便于未来在多工位 UI 也使用）
  chData.cycleSumStepDurations = d.cycle_sum_step_durations || {};
  chData.lastCycleSumStepDurations = d.last_cycle_sum_step_durations || {};
  chData.avgCycleSumStepDurations = d.avg_cycle_sum_step_durations || {};
  chData.stepCycleSegments = d.step_cycle_segments || {};
  // v3.9.x D 方案: 累计可见时长 (visible PT 数据源)
  chData.stepVisibleSeconds = d.step_visible_seconds || {};
  chData.detections = d.detections || [];
  chData._pollProjectConfig = d.project_config || null;
  chData.perItemState = d.per_item_state || null;   // v3.28: 多工位画框贴螺丝编号用
  chData.currentCycleSteps = d.current_cycle_steps || [];
  chData.backupCoveredLabels = d.backup_covered_labels || [];
  chData.stepCounts = d.step_counts || {};
  chData.recentEvents = d.recent_events || [];
  // v3.9.x 事件人工确认阻塞态 (后端透出, 前端 modal 直接读)
  chData.pendingAck = d.pending_ack || { active: false };
  // v3.23 缺步骤延迟落账挂起明细 (None=无挂起; 有值时 ack 窗展示缺项 + "补步骤"按钮)
  chData.pendingRemediation = d.pending_remediation || null;
  if (d.tracking) chData.tracking = d.tracking;

  // MES 实时数据 — 每个工位各自显示，不限 selectedChannel
  // v3.1.3: 新扫码来了 (serial_no 从 X → Y), 清掉残留的 override 恢复默认显示
  const prevSn = multiChannelData.value[ch]?.mes?.workpiece?.serial_no;
  const newSn = d.mes?.workpiece?.serial_no;
  if (newSn && newSn !== prevSn && ch in workpieceOverridesByCh.value) {
    if (workpieceOverrideTimers[ch]) { clearTimeout(workpieceOverrideTimers[ch]); workpieceOverrideTimers[ch] = null; }
    if (workpieceHideTimers[ch]) { clearTimeout(workpieceHideTimers[ch]); workpieceHideTimers[ch] = null; }
    const next = { ...workpieceOverridesByCh.value };
    delete next[ch];
    workpieceOverridesByCh.value = next;
  }

  chData.mes = d.mes || null;
  if (d.mes) {
    if (d.mes.scan_event) {
      handleScanToast(d.mes.scan_event, ch);
    }
    if (d.mes.rebind_prompt) {
      handleRebindPrompt(d.mes.rebind_prompt, ch);
    }
    // v3.4.2 hotfix: 后端 reload / 别终端切换"扫码禁用"时, 把状态同步进 store,
    // 让 isScanDisabledFor / 守门 / 按钮文字 / 信息条都跟上.
    if (typeof d.mes.scan_disabled === 'boolean') {
      scannerDisableStore.applyServerHint(ch - 1, d.mes.scan_disabled);
    }
  }

  const total = chData.total || 0;
  const ok = chData.ok || 0;
  chData.yieldRate = total > 0 ? Math.round((ok / total) * 100) : 0;

  const chTotalCycles = ctrs['总产量'] || ctrs['total'] || 0;
  const backendNgMap = d.ng_step_cycle_counts || {};
  if (Object.keys(backendNgMap).length > 0) {
    const ranking = Object.entries(backendNgMap)
      .filter(([, c]) => c > 0)
      .map(([step, ngCount]) => ({ step, count: ngCount, rate: chTotalCycles > 0 ? (ngCount / chTotalCycles * 100) : 0 }))
      .sort((a, b) => b.rate - a.rate);
    chData.ngStepRanking = ranking.slice(0, 3);
  } else {
    chData.ngStepRanking = [];
  }

  const allC = [];
  for (const [name, value] of Object.entries(ctrs)) {
    if (!name.startsWith('_')) allC.push({ name, value });
  }
  chData.allCounters = allC;

  // v3.1.3: 跟踪模式下,后端不推 _currentCycleSteps,改用 tracking.item_checklist
  // 把 counted > 0 的步骤翻成 completed/OK,避免步骤一直停在"--/待检测"
  const _isTracking = (d.project_config?.logic_mode || currentProject.value?.logic_mode) === 'tracking';
  const _trackChecklist = _isTracking ? (d.tracking?.item_checklist || {}) : null;
  const _trackBoxes = _isTracking ? (d.tracking?.boxes || {}) : null;
  const _isContainer = _isTracking && !!d.tracking?.container_mode;
  const _trackHit = (label) => {
    if (!_isTracking) return false;
    if (_isContainer && _trackBoxes) {
      // 容器模式: 任意箱子里 counted > 0 即视为已检测
      for (const bid of Object.keys(_trackBoxes)) {
        const items = _trackChecklist?._boxes?.[bid]?.items;
        if (items && items[label] && items[label].counted > 0) return true;
      }
      return false;
    }
    return !!(_trackChecklist?.[label] && _trackChecklist[label].counted > 0);
  };

  // v3.2.1: 跟踪模式下,步骤统计/SOP 只展示"每箱期望物品"中的项目,
  // 排除作为"容器"的箱子类别(避免容器分组模式表格里出现"箱子"行)
  const _trkExpectedLabels = (() => {
    if (!_isTracking) return null;
    const projCfg = d.project_config || currentProject.value || {};
    const pipeCfg = projCfg.pipeline_config || {};
    const expList = projCfg.counting_expected_list
      || pipeCfg.counting_expected_list
      || [];
    const expDict = pipeCfg.counting_expected_items || {};
    const set = new Set();
    expList.forEach(it => { if (it && it.label) set.add(it.label); });
    Object.keys(expDict).forEach(l => set.add(l));
    if (set.size > 0) return set;
    // 兜底:未填清单时仅排除容器 label
    const containerLabel = projCfg.tracking_container_label
      || pipeCfg.tracking_container_label
      || '';
    return containerLabel ? { _excludeContainer: containerLabel } : null;
  })();
  const _trkAllow = (label) => {
    if (!_trkExpectedLabels) return true;
    if (_trkExpectedLabels instanceof Set) return _trkExpectedLabels.has(label);
    if (_trkExpectedLabels._excludeContainer) {
      return label !== _trkExpectedLabels._excludeContainer;
    }
    return true;
  };

  if (d.detections) {
    const stepsConf = d.project_config?.steps_config || currentProject.value?.steps_config || [];
    const stMap = {};
    stepsConf.forEach(s => { stMap[s.label] = s; });
    const td = stepsConf
      .filter(s => s.enabled !== false && !s.is_backup && !s.hide_in_view && _trkAllow(s.label))
      .map((s) => {
        const inCycle = chData.currentCycleSteps.includes(s.label);
        const coveredByBackup = chData.backupCoveredLabels.includes(s.label);
        const trackHit = _trackHit(s.label);
        // v3.8.x (二次修订): 多工位 SOP/状态表"已完成"判定与单工位 status 对齐 —
        // 步骤进过 cycle_steps 就算完成, 不再硬等权威 PT 写入。
        // 修客户反馈"反应慢, 第三步显示时第一步 PT 还没出, 最后一步常常被周期清空根本来不及显示"。
        // cycleResult (OK/NG) 仍在 updateStepsFromBackend 那条路用权威 PT 守门, 这里 trackHit 不变。
        return {
          step: s.displayLabel || s.label,
          label: s.label,
          status: (inCycle || coveredByBackup || trackHit) ? 'completed' : 'pending',
          cycleResult: trackHit ? 'ok' : null,
        };
      });
    chData.tableData = td;
  }

  if (d.detections) {
    const stepsConf = d.project_config?.steps_config || currentProject.value?.steps_config || [];
    const screenshots = d.step_screenshots || {};
    // v3.10.x: SOP 卡片"图永不空"策略 — 后端有新图就替换, 没有就从上一轮按 label 继承,
    // 让客户视觉上始终有缩略图(包括短步骤截图节流漏窗 / 跨周期间隙等场景).
    // 状态色仍由 status/cycleResult 控制, 图片与状态完全解耦.
    const prevSopByLabel = Object.fromEntries(
      (chData.steps || []).map(s => [s.label, s.screenshot])
    );
    const sopSteps = stepsConf
      .filter(s => s.enabled !== false && !s.is_backup && !s.hide_in_view && _trkAllow(s.label))
      .map(s => {
        const inCycle = chData.currentCycleSteps.includes(s.label);
        const coveredByBackup = chData.backupCoveredLabels.includes(s.label);
        const trackHit = _trackHit(s.label);
        // v3.8.x (二次修订): 同步 sopSteps 与上方 tableData 的放宽规则, 进 cycle_steps 就算完成。
        const rawB64 = screenshots[s.label];
        return {
          name: s.displayLabel || s.label,
          label: s.label,
          status: (inCycle || coveredByBackup || trackHit) ? 'completed' : 'pending',
          screenshot: rawB64
            ? `data:image/jpeg;base64,${rawB64}`
            : (prevSopByLabel[s.label] || null),
        };
      });
    chData.steps = sopSteps;
  }
  // v2.7.4: 收集"项目配置中标记隐藏标注框"的 label 集合，drawMultiDetections 据此跳过画框
  // 仅影响 Monitor 画面 + SOP 卡片 + 步骤详情，不影响检测/数据/报警/MES
  {
    const stepsConf = d.project_config?.steps_config || currentProject.value?.steps_config || [];
    chData._hiddenLabels = new Set(
      stepsConf.filter(s => s && s.hide_in_view && s.label).map(s => s.label)
    );
  }

  const events = d.recent_events || [];
  if (events.length > 0) {
    if (!multiLastSeenSeq[ch]) multiLastSeenSeq[ch] = 0;
    const newEvents = events
      .filter(e => e.seq > multiLastSeenSeq[ch] && e.show_notification)
      .slice(-3);
    newEvents.forEach(event => {
      multiLastSeenSeq[ch] = Math.max(multiLastSeenSeq[ch], event.seq);
      const toastId = event.toast_id || (event.event_id === 1 ? 'ok' : event.event_id === 2 ? 'ng' : 'ok');
      showMultiToast(ch, toastId, event.event_name, event.reason, event.event_id);

      const warnCfg = systemStore.detection.toasts?.warn_no_barcode;
      // v3.5.2: 直接读后端权威判定, 不再做客户端守门 (避免索引错位/缓存竞态).
      // 老后端缺该字段时按"未绑码 + 没有显式静默"逻辑保持原行为兼容.
      const _shouldWarn = event.should_warn_no_barcode !== undefined
        ? !!event.should_warn_no_barcode
        : !event.had_workpiece;
      if (warnCfg?.enabled && _shouldWarn) {
        setTimeout(() => {
          showMultiToast(ch, 'warn_no_barcode', warnCfg.text || '⚠ 未绑码', warnCfg.subText || '本次结算未绑定工件条码');
        }, 300);
      }
    });
  }

  // D4①: 原地更新降 GC。chData 已是 multiChannelData.value[ch] 的响应式引用,
  // 上面所有 chData.xxx= 都已被 Vue3 深响应追踪; 不再每 150ms×N 工位整对象 spread
  // 重建(那样每 tick 都丢一个旧对象 + 建一个新对象, 长跑 GC 抖动)。首次创建才赋值。
  if (!multiChannelData.value[ch]) {
    multiChannelData.value[ch] = chData;
  }

  const pollCfg = d.project_config || null;
  const dets = d.detections || [];
  const hidden = chData._hiddenLabels;

  const canvas = multiCanvasRefs[ch];
  if (canvas) {
    if (dets.length) {
      drawMultiDetections(ch, canvas, dets, hidden, pollCfg);
    } else {
      const ctx = canvas.getContext('2d');
      ctx.clearRect(0, 0, canvas.width, canvas.height);
    }
  }

  // layout.body 插件: native overlay canvas 不在 DOM，每轮 polling 主动画到插件 canvas
  // (不能只靠插件 watch detections — 换页/重进 Monitor 时 prop 时序会丢帧)
  if (layoutBodyOverride.value) {
    const paintPluginOverlay = () => {
      const overlays = document.querySelectorAll('canvas.fjjl-det-overlay');
      const pluginCanvas = overlays[ch];
      if (!pluginCanvas?.parentElement) return;
      if (pluginCanvas.parentElement.offsetWidth < 2) return;
      if (dets.length) {
        drawMultiDetections(ch, pluginCanvas, dets, hidden, pollCfg);
      } else {
        const ctx = pluginCanvas.getContext('2d');
        ctx.clearRect(0, 0, pluginCanvas.width, pluginCanvas.height);
      }
    };
    paintPluginOverlay();
    nextTick(() => {
      paintPluginOverlay();
      requestAnimationFrame(paintPluginOverlay);
    });
  }
};

const startMultiPolling = () => {
  stopMultiPolling();
  multiPollingInProgress = false;
  multiPollingTimer = setInterval(async () => {
    if (multiPollingInProgress) return;
    multiPollingInProgress = true;
    try {
      const promises = [];
      for (let ch = 0; ch < channelCount.value; ch++) {
        promises.push(
          getDetectionResults(ch)
            .then(res => processChannelResult(ch, res.data))
            .catch((e) => { dbgErr('monitor.poll', `工位${ch + 1} 结果轮询`, e); })
        );
        if (multiChannelData.value[ch]?.sourceType === 'video' && !multiDraggingProgress.value[ch]) {
          promises.push(
            api.get('/source/video/info', { params: { channel: ch } })
              .then((videoRes) => {
                if (videoRes.data.status !== 'success') return;
                const chData = multiChannelData.value[ch];
                if (!chData) return;
                chData.videoInfo = {
                  progress: videoRes.data.progress || 0,
                  currentTime: videoRes.data.current_time || 0,
                  duration: videoRes.data.duration || 0,
                  speed: videoRes.data.speed || 1,
                  ended: videoRes.data.ended || false,
                };
              })
              .catch(() => {})
          );
        }
      }
      await Promise.all(promises);
      updateGlobalDetectingState();
      updateMultiCharts();
    } finally {
      multiPollingInProgress = false;
    }
  }, 150);
};

const stopMultiPolling = () => {
  if (multiPollingTimer) { clearInterval(multiPollingTimer); multiPollingTimer = null; }
};

const updateMultiCharts = () => {
  // Data-driven rendering via template — no separate ECharts needed for multi-view
};

// v3.5.2: 把归一化 bbox clip 到 [0, 1] 防越界, 兜底后端推理/Kalman 滤波偶发飘出
// 即使后端漏 clip, 前端也保证框永远画在画面内
const clipNormalizedBox = (det) => {
  const x = Math.max(0, Math.min(1, Number(det.x) || 0));
  const y = Math.max(0, Math.min(1, Number(det.y) || 0));
  const w = Math.max(0, Math.min(1 - x, Number(det.w) || 0));
  const h = Math.max(0, Math.min(1 - y, Number(det.h) || 0));
  return { x, y, w, h };
};

/** 归一化坐标下的射线法点在多边形内（含边界） */
const pointInPolygonNorm = (px, py, poly) => {
  if (!poly || poly.length < 3) return true;
  let inside = false;
  const n = poly.length;
  for (let i = 0, j = n - 1; i < n; j = i++) {
    const xi = Number(poly[i][0]);
    const yi = Number(poly[i][1]);
    const xj = Number(poly[j][0]);
    const yj = Number(poly[j][1]);
    const denom = (yj - yi) || 1e-18;
    if (((yi > py) !== (yj > py)) && (px < ((xj - xi) * (py - yi)) / denom + xi)) {
      inside = !inside;
    }
  }
  return inside;
};

/**
 * pipeline_config.hide_boxes_outside_step_roi：对已配置步骤 ROI 的标签，
 * 框中心不在多边形内则不绘制（仅监视 UI）。
 */
const shouldDrawDetWithStepRoi = (det, stepsConfig, pipelineConfig) => {
  const pc = pipelineConfig || {};
  if (!pc.hide_boxes_outside_step_roi) return true;
  const label = det.label;
  if (!label) return true;
  const step = (stepsConfig || []).find(s => s && s.label === label && s.enabled !== false);
  if (!step || !Array.isArray(step.roi) || step.roi.length < 3) return true;
  const cb = clipNormalizedBox(det);
  const cx = cb.x + cb.w / 2;
  const cy = cb.y + cb.h / 2;
  return pointInPolygonNorm(cx, cy, step.roi);
};

const drawMultiDetections = (ch, canvas, detections, hiddenLabels = null, pollProjectConfig = null) => {
  if (!canvas) return;
  const parent = canvas.parentElement;
  if (parent) { canvas.width = parent.offsetWidth; canvas.height = parent.offsetHeight; }
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const cw = canvas.width, ch2 = canvas.height;

  const nat = multiFrameNaturalSize[ch];
  let dx = 0, dy = 0, dw = cw, dh = ch2;
  if (nat && nat.w > 0 && nat.h > 0) {
    const scale = Math.min(cw / nat.w, ch2 / nat.h);
    dw = nat.w * scale;
    dh = nat.h * scale;
    dx = (cw - dw) / 2;
    dy = (ch2 - dh) / 2;
  }

  const stepsConfMulti = pollProjectConfig?.steps_config || currentProject.value?.steps_config || [];
  const pipeMulti = pollProjectConfig?.pipeline_config || currentProject.value?.pipeline_config || {};

  // v3.8.x: 多工位画框也读用户配置 (老逻辑硬编码 #10b981/#ef4444、线宽2、字号11,
  // 客户在设置页改的检测框颜色/线宽/字号在多工位下全部失效).
  // 优先取该通道独立配置, 没配就回退全局检测配置.
  const chDet = systemStore.getChannelDetection(ch) || systemStore.detection;
  const okColorMulti = chDet.boxColor || '#10b981';
  const ngColorMulti = chDet.boxColorNG || '#ef4444';
  const lineWidthMulti = Number(chDet.boxLineWidth) || 2;
  const fontSizeMulti = (Number(chDet.labelFontSize) || 11) * (window.__uiScale || 1);
  const showConfMulti = chDet.showConfidence !== false;

  // v3.28+ per_item: 按覆盖态上色 + 显示螺丝编号 (复用单工位同一套个体配对)
  const piStateMulti = multiChannelData.value[ch]?.perItemState || null;
  const piCfgMulti = piStateMulti?.config;
  const colorByCovMulti = !!piCfgMulti?.color_by_coverage;
  const showNumMulti = !!piCfgMulti?.show_item_numbers;
  let coverByLabelMulti = null;
  let covOnMulti = okColorMulti, covOffMulti = ngColorMulti;
  if ((colorByCovMulti || showNumMulti) && Array.isArray(piStateMulti?.steps)) {
    coverByLabelMulti = new Map();
    for (const st of piStateMulti.steps) {
      if (!st || !st.item_label || !Array.isArray(st.items)) continue;
      let arr = coverByLabelMulti.get(st.item_label);
      if (!arr) { arr = []; coverByLabelMulti.set(st.item_label, arr); }
      for (const it of st.items) {
        if (Array.isArray(it.bbox) && it.bbox.length === 4) arr.push(it);
      }
    }
    if (coverByLabelMulti.size === 0) coverByLabelMulti = null;
    covOnMulti = piCfgMulti.box_color_covered || okColorMulti;
    covOffMulti = piCfgMulti.box_color_uncovered || ngColorMulti;
  }
  const piHitMulti = (det, cb) => {
    if (!coverByLabelMulti) return null;
    const items = coverByLabelMulti.get(det.label);
    if (!items || !items.length) return null;
    let best = 0.3, hit = null;
    for (const it of items) {
      const b = it.bbox;
      const ix1 = Math.max(cb.x, b[0]), iy1 = Math.max(cb.y, b[1]);
      const ix2 = Math.min(cb.x + cb.w, b[0] + b[2]), iy2 = Math.min(cb.y + cb.h, b[1] + b[3]);
      const iw = Math.max(0, ix2 - ix1), ih = Math.max(0, iy2 - iy1);
      const inter = iw * ih, uni = cb.w * cb.h + b[2] * b[3] - inter;
      const iou = uni > 0 ? inter / uni : 0;
      if (iou > best) { best = iou; hit = it; }
    }
    return hit;
  };

  detections.forEach(det => {
    if (det.hidden) return;
    if (hiddenLabels && det.label && hiddenLabels.has(det.label)) return;
    if (!shouldDrawDetWithStepRoi(det, stepsConfMulti, pipeMulti)) return;
    const cb = clipNormalizedBox(det);
    const x = cb.x * dw + dx, y = cb.y * dh + dy;
    const w = cb.w * dw, h = cb.h * dh;
    const hitMulti = (colorByCovMulti || showNumMulti) ? piHitMulti(det, cb) : null;
    const color = (colorByCovMulti && hitMulti)
      ? (hitMulti.covered ? covOnMulti : covOffMulti)
      : pickDetColor(det, stepsConfMulti, okColorMulti, ngColorMulti);
    if (det.mask && Array.isArray(det.mask) && det.mask.length > 2) {
      ctx.beginPath();
      det.mask.forEach((pt, i) => {
        const mxN = Math.max(0, Math.min(1, Number(pt[0]) || 0));
        const myN = Math.max(0, Math.min(1, Number(pt[1]) || 0));
        const mx = mxN * dw + dx, my = myN * dh + dy;
        if (i === 0) ctx.moveTo(mx, my); else ctx.lineTo(mx, my);
      });
      ctx.closePath();
      ctx.save();
      ctx.fillStyle = color;
      ctx.globalAlpha = 0.25;
      ctx.fill();
      ctx.restore();
      ctx.strokeStyle = color;
      ctx.lineWidth = lineWidthMulti;
      ctx.stroke();
    } else {
      ctx.strokeStyle = color;
      ctx.lineWidth = lineWidthMulti;
      ctx.strokeRect(x, y, w, h);
    }
    ctx.font = `bold ${fontSizeMulti}px Arial`;
    let label = det.display_id || det.display_name || det.label || '';
    if (showNumMulti && hitMulti) label = `${det.label || ''}#${hitMulti.id}`;
    if (showConfMulti && det.confidence) label += ` ${(det.confidence * 100).toFixed(0)}%`;
    const lh = Math.max(12, fontSizeMulti + 2);
    ctx.fillStyle = color;
    ctx.fillRect(x, y - lh, ctx.measureText(label).width + 6, lh);
    ctx.fillStyle = 'white';
    ctx.fillText(label, x + 3, y - 4);
  });
};

// Step 8 (feat/multi-model-roi-link): 解析单个 model id 到磁盘路径 (含格式回退)
const _resolveModelPath = async (modelId, modelFormat = 'pytorch_fp32') => {
  try {
    const resolveRes = await apiResolveModelPath(modelId, modelFormat);
    return {
      path: resolveRes.data.path,
      fallback: resolveRes.data.fallback,
      reason: resolveRes.data.reason,
    };
  } catch {
    const modelRes = await getModelDetail(modelId);
    return { path: modelRes.data.file_path, fallback: false };
  }
};

const startDetectionForChannel = async (ch) => {
  const chProj = multiChannelData.value[ch]?.project || currentProject.value;
  dbg('monitor.control', `点击「开始」(工位${ch + 1})`, `project=${chProj?.name || '无'}`);
  if (!chProj) {
    dbg('monitor.control', `工位${ch + 1} 开始被拒: 未绑定项目`);
    ElMessage.warning(`工位 ${ch + 1} 未绑定项目`);
    return;
  }
  try {
    await syncProjectConfig(ch, chProj);
    const modelId = chProj.default_model_id;
    if (!modelId) { ElMessage.warning(`工位 ${ch + 1} 未配置模型`); return; }
    const modelFormat = chProj.model_format || 'pytorch_fp32';

    const mainResolved = await _resolveModelPath(modelId, modelFormat);
    if (mainResolved.fallback && modelFormat !== 'pytorch_fp32') {
      ElMessage.warning(mainResolved.reason || '转换模型不可用，已回退到原始模型');
    }
    const mainPath = mainResolved.path;

    // Step 8: 解析项目的副模型配置 (来自 pipeline_config.models[], name !== 'main')
    const pipelineModels = chProj?.pipeline_config?.models || [];
    const extraSlots = pipelineModels.filter(m => m && m.name && m.name !== 'main' && m.model_id);

    // v3.7.0: 会话 ID 校验提前到多/单模型 if 分支之前, 失败直接退出
    if (!validateSessionName(sessionName.value)) {
      ElMessage.warning('会话 ID 不合法：' + sessionNameError.value);
      return;
    }
    const _sessionId = sessionName.value || null;
    if (extraSlots.length > 0) {
      // 多模型 payload
      const mainPipelineSpec = pipelineModels.find(m => m && m.name === 'main') || {};
      const specs = [{
        name: 'main',
        model_path: mainPath,
        conf: 0.25,
        iou: 0.45,
        display_color: mainPipelineSpec.display_color || '#10b981',
        priority: 100,
      }];
      const failedSlots = [];
      for (const e of extraSlots) {
        try {
          // v3.7.x: 副模型也按 model_format resolve (与单通道分支对齐).
          // 无 model_format 字段的老项目兜底 pytorch_fp32, 行为完全等价旧版本.
          const r = await _resolveModelPath(e.model_id, e.model_format || 'pytorch_fp32');
          specs.push({
            name: e.name,
            model_path: r.path,
            conf: typeof e.conf === 'number' ? e.conf : 0.25,
            iou: typeof e.iou === 'number' ? e.iou : 0.45,
            roi: Array.isArray(e.roi) && e.roi.length >= 3 ? e.roi : null,
            schedule: e.schedule || { type: 'every_frame', n: 1, events: [] },
            class_filter: Array.isArray(e.class_filter) && e.class_filter.length
              ? e.class_filter : null,
            priority: typeof e.priority === 'number' ? e.priority : 50,
            display_color: e.display_color || '#f59e0b',
            use_half: !!e.use_half,
          });
        } catch (err) {
          failedSlots.push(e.name);
          console.warn(`[Monitor] 副模型 ${e.name} 路径解析失败:`, err);
        }
      }
      if (failedSlots.length) {
        ElMessage.warning(`副模型路径解析失败已跳过: ${failedSlots.join(', ')}`);
      }
      await apiStartDetection({ models: specs }, undefined, undefined, ch, _sessionId);
      dbg('monitor.control', `工位${ch + 1} 启动成功 (多模型 ${specs.length})`);
      ElMessage.success(
        `工位 ${ch + 1} 检测已启动 (主 + ${specs.length - 1} 个副模型)`
      );
    } else {
      await apiStartDetection(mainPath, 0.25, 0.45, ch, _sessionId);
      dbg('monitor.control', `工位${ch + 1} 启动成功`);
      ElMessage.success(`工位 ${ch + 1} 检测已启动`);
    }
  } catch (e) {
    dbgErr('monitor.control', `工位${ch + 1} 启动`, e);
    ElMessage.error(`工位 ${ch + 1} 启动失败: ${e.message}`);
  }
};

const updateGlobalDetectingState = () => {
  const anyDetecting = Object.values(multiChannelData.value).some(d => d?.isDetecting);
  systemStore.setDetecting(anyDetecting);
  projectStore.setRunningStatus(anyDetecting);
};

// v3.3.0 码-码闭环结算: 停止/待机前如果工位有"未关闭的扫码窗口", 弹窗让用户选
//   - 结算 (默认): 把这枚工件按"曾齐过"判定 OK/NG 入账
//   - 丢弃: 直接丢, 产能不计
//   - 取消: 中止本次停止操作
// 返回 true=继续 stop, false=取消 stop.
const confirmScanPairBeforeStop = async (ch) => {
  try {
    const res = await getScanPairActive(ch);
    const data = res.data || {};
    if (!data.is_scan_pair_mode || !data.active_serial) return true;
    let action = 'settle';
    try {
      await ElMessageBox({
        title: '码-码闭环: 最后一码未关闭',
        message: `工位 ${ch + 1} 当前周期开始码 "${data.active_serial}" 尚未扫到下一码。\n` +
                 `选择如何收尾本枚工件:`,
        showCancelButton: true,
        confirmButtonText: '结算 (默认)',
        cancelButtonText: '丢弃',
        distinguishCancelAndClose: true,
        type: 'warning',
      });
      action = 'settle';
    } catch (e) {
      if (e === 'close') return false;
      action = 'discard';
    }
    try {
      await settleScanPairForStop(ch, action === 'discard');
      ElMessage.success(action === 'discard'
        ? `工位 ${ch + 1} 最后一码已丢弃`
        : `工位 ${ch + 1} 最后一码已结算`);
    } catch (e) {
      ElMessage.error(`工位 ${ch + 1} 收尾失败: ${e?.response?.data?.detail || e?.message || e}`);
    }
    return true;
  } catch (e) {
    return true;
  }
};

const stopDetectionForChannel = async (ch) => {
  dbg('monitor.control', `点击「停止」(工位${ch + 1})`);
  if (!(await confirmScanPairBeforeStop(ch))) { dbg('monitor.control', `工位${ch + 1} 停止被取消: scan_pair 确认未通过`); return; }
  try {
    await pauseDetection(ch);
    if (multiChannelData.value[ch]) multiChannelData.value[ch].isDetecting = false;
    updateGlobalDetectingState();
    dbg('monitor.control', `工位${ch + 1} 停止成功`);
    ElMessage.info(`工位 ${ch + 1} 已停止`);
  } catch (e) {
    dbgErr('monitor.control', `工位${ch + 1} 停止`, e);
    ElMessage.error(`工位 ${ch + 1} 停止失败`);
  }
};

const standbyForChannel = async (ch) => {
  dbg('monitor.control', `点击「待机」(工位${ch + 1})`);
  if (!(await confirmScanPairBeforeStop(ch))) { dbg('monitor.control', `工位${ch + 1} 待机被取消: scan_pair 确认未通过`); return; }
  try {
    await standbyDetection(ch);
    if (multiChannelData.value[ch]) multiChannelData.value[ch].isDetecting = false;
    updateGlobalDetectingState();
    dbg('monitor.control', `工位${ch + 1} 待机成功`);
    ElMessage.info(`工位 ${ch + 1} 已待机`);
  } catch (e) {
    dbgErr('monitor.control', `工位${ch + 1} 待机`, e);
    ElMessage.error(`工位 ${ch + 1} 待机失败`);
  }
};

const fetchChannelCount = async () => {
  try {
    const res = await getWorkstations();
    const count = res.data.channel_count || 1;
    channelCount.value = count;
    if (selectedChannel.value >= count) {
      selectedChannel.value = 0;
    }
    if (count > 1) {
      // D2 启动竞态修复: 工位数是唯一真相源。进入多工位前必须显式停掉单工位那套
      // (单工位轮询 + 单工位 MJPEG 流), 否则 onMounted 里 getSourceStatus 若先于本函数
      // 解析、彼时 channelCount 仍是默认 1 → 误起单工位流, 随后本函数又起多工位流,
      // 两套并存抢同一通道 MJPEG → 画面冻结 / 双重轮询。"起新套前先停旧套"。
      stopPolling();
      disconnectStream();
      initMultiChannelData(count);
      // 整页覆盖插件 (monitor.layout.body) 自己用 <img> 吃 /video_feed?channel=N,
      // 原生双缓冲取流会和插件 <img> 抢同一通道的 MJPEG 连接 (后端每通道只保留最新
      // 一条连接, 旧连接主动让位), 先连的被踢断 → 画面冻在第一帧、而检测框叠加层走
      // 独立轮询照常更新。覆盖生效时跳过原生取流, 仅保留轮询 (叠加层画框/计数都靠它)。
      if (!layoutBodyOverride.value) startMultiStreams(count);
      startMultiPolling();
      loadPerChannelDetectionSettings(res.data.source_configs || {});
    } else {
      stopMultiPolling();
      stopMultiStreams();
      resetMultiRuntimeState(true);
    }
  } catch (e) {
    channelCount.value = 1;
    stopMultiPolling();
    stopMultiStreams();
    resetMultiRuntimeState(true);
  }
};

const loadPerChannelDetectionSettings = async (sourceConfigs) => {
  for (const [chStr, cfg] of Object.entries(sourceConfigs)) {
    const chId = parseInt(chStr);
    const pid = cfg?.project_id;
    if (!pid) continue;
    try {
      const { getProjectDetail } = await import('@/api/project');
      const res = await getProjectDetail(pid);
      const projData = res.data;
      const dc = projData?.detection_config;
      if (dc) {
        systemStore.loadDetectionForChannel(chId, dc);
      }
      if (projData && multiChannelData.value[chId]) {
        multiChannelData.value[chId].project = projData;
        multiChannelData.value[chId].projectName = projData.name || '';
      }
    } catch (e) {
      console.warn(`ch${chId} 加载检测设置失败:`, e);
    }
  }
};
// ==================== End Multi-Channel ====================

const buildStreamUrl = () => `${getBackendHost()}/video_feed?t=${Date.now()}`;

// v3.7.5: 单条 detection 的检测框颜色优先级 (改自 v3.7.2 FIX-381-B).
//   1) steps_config 里该 label 配置的 box_color (用户明确意图最高优先级)
//   2) 副模型 display_color (model_name != 'main' 且后端注入了 display_color)
//   3) 全局 OK / NG 兜底 (来自 systemStore.detection 或 multi 版本的硬编码)
// 注: 老版优先级 (副模型色 > 步骤色) 让客户在步骤列表给副模型 label 配的颜色
// 形同摆设, tooltip 又写"任何模式都生效", 自相矛盾. 翻过来后用户配啥就是啥.
const pickDetColor = (det, stepsConfig, fallbackOK, fallbackNG) => {
  if (stepsConfig && det && det.label) {
    const cfg = stepsConfig.find(s => s && s.label === det.label);
    if (cfg && cfg.box_color) return cfg.box_color;
  }
  if (det && det.model_name && det.model_name !== 'main' && det.display_color) {
    return det.display_color;
  }
  return det && det.is_ng ? fallbackNG : fallbackOK;
};

// 启动一个 watchdog timer。timeoutMs 内若没有被 onStreamReady 重新
// armStreamWatchdog 重置, 触发强制重连: ++ streamKey 重建 <img> DOM,
// nextTick 后设新 url。这是破 Chrome socket pool 复用的唯一可靠方式。
const armStreamWatchdog = (timeoutMs) => {
  if (streamWatchdogTimer) {
    clearTimeout(streamWatchdogTimer);
    streamWatchdogTimer = null;
  }
  streamWatchdogTimer = setTimeout(() => {
    streamWatchdogTimer = null;
    if (!monitorMounted) return;
    if (streamConnectAttempts >= STREAM_MAX_WATCHDOG_RETRIES) {
      console.warn('[MJPEG] watchdog 重试已达上限, 放弃自动重连');
      return;
    }
    streamConnectAttempts++;
    console.warn(`[MJPEG] watchdog 第 ${streamConnectAttempts} 次强制重连: ${timeoutMs}ms 内未收到帧, 重建 <img> DOM`);
    dbg('monitor.video', `watchdog 强制重连 (第${streamConnectAttempts}次)`, `${timeoutMs}ms 内未收到帧`);
    isStreaming.value = false;
    streamSrc0.value = '';
    streamSrc1.value = '';
    streamKey.value++;
    nextTick(() => {
      if (!monitorMounted) return;
      streamSrc0.value = buildStreamUrl();
      activeStream.value = 0;
      armStreamWatchdog(STREAM_FIRST_FRAME_TIMEOUT_MS);
    });
  }, timeoutMs);
};

const connectStream = () => {
  // 先清两个 img 的 src + 重建 DOM, 让浏览器关掉潜在旧 socket;
  // nextTick 后再设新 url, 配合 watchdog 形成完整的"破 socket 复用"信号。
  dbg('monitor.video', '连接视频流', `channel=${selectedChannel.value || 0}`);
  streamSrc0.value = '';
  streamSrc1.value = '';
  isStreaming.value = false;
  streamErrorCount = 0;
  streamConnectAttempts = 0;
  streamKey.value++;
  nextTick(() => {
    if (!monitorMounted) return;
    streamSrc0.value = buildStreamUrl();
    activeStream.value = 0;
    armStreamWatchdog(STREAM_FIRST_FRAME_TIMEOUT_MS);
  });
};

const disconnectStream = () => {
  dbg('monitor.video', '断开视频流');
  if (streamWatchdogTimer) {
    clearTimeout(streamWatchdogTimer);
    streamWatchdogTimer = null;
  }
  streamConnectAttempts = 0;
  isStreaming.value = false;
  streamSrc0.value = '';
  streamSrc1.value = '';
};

const swapStream = () => {
  const bg = activeStream.value === 0 ? 1 : 0;
  const bgSrcRef = bg === 0 ? streamSrc0 : streamSrc1;
  bgSrcRef.value = buildStreamUrl();
  // onStreamReady(bg) will do the actual swap when the first frame arrives.
  //
  // 历史踩坑: 这里曾经加过 swap watchdog (3s 内 bg 接不到首帧就强制 connectStream),
  // 但健康流的 swap 也会偶发 onload 延迟超过 3s, watchdog 误判 → 整体重连 → 闪烁。
  // (实测: uvicorn 日志每 9s 一组 3 次 /video_feed 长连接.)
  // 现在 swap 失败保持 active 不切, 等下次 swap (默认 1 分钟) 再尝试, 不再做激进探活。
};

// Kept as alias so existing call-sites (start/stop/resume) still work
const forceReconnectStream = () => connectStream();

const onStreamReady = (idx) => {
  streamErrorCount = 0;
  streamConnectAttempts = 0;
  isStreaming.value = true;
  streamBackendMismatchSince = 0;
  dbg('monitor.video', '视频流首帧到达', `img=${idx} channel=${selectedChannel.value || 0} (接管为active并重置画布)`);
  // 首帧成功 -> 取消首帧 watchdog。multipart/x-mixed-replace 后续帧不
  // 触发 onload, 不能用心跳 watchdog 重置, 中途卡死改由 polling 路径检测。
  if (streamWatchdogTimer) {
    clearTimeout(streamWatchdogTimer);
    streamWatchdogTimer = null;
  }
  resizeCanvas();
  if (idx !== activeStream.value) {
    const oldIdx = activeStream.value;
    activeStream.value = idx;
    // Release the old img's decoder memory
    if (oldIdx === 0) streamSrc0.value = '';
    else streamSrc1.value = '';
  }
};

const onStreamError = (idx) => {
  if (!monitorMounted) return;
  // 只 active img 的 error 触发重连; bg img 接不到首帧改由 swap watchdog 兜底。
  // 之前尝试取消这个守门让 bg error 也走重连, 结果 connectStream 清空 src='' 又
  // 触发新一轮 error → onStreamError → connectStream 的死循环, 画面 100ms 一次闪烁。
  // (uvicorn 日志同一秒内建 3 次 /video_feed 长连接 → 闭环已确认.)
  if (idx !== activeStream.value) return;
  streamErrorCount++;
  dbg('monitor.video', `视频流错误 (第${streamErrorCount}次)`, `img=${idx}`);
  if (streamErrorCount > 50) return;
  const delay = Math.min(streamErrorCount * 300, 3000);
  if (streamReconnectTimer) {
    clearTimeout(streamReconnectTimer);
    streamReconnectTimer = null;
  }
  streamReconnectTimer = setTimeout(() => {
    streamReconnectTimer = null;
    if (!monitorMounted) return;
    connectStream();
  }, delay);
};

// 当前项目
const currentProject = computed(() => projectStore.currentProject);

const isTrackingMode = computed(() => currentProject.value?.logic_mode === 'tracking');
// v3.8+: 逐件模式 — 视频下方专属面板, 排他 SOP/Tracking
const isPerItemMode = computed(() => currentProject.value?.logic_mode === 'per_item');
// 原生称重投料模式 — 视频下方专属看板 (人员/型号/各料投料进度/本件结论)
const isWeighingMode = computed(() => currentProject.value?.logic_mode === 'weighing');

// v3.19.x 自定义混合逐件: 把 custom_mix_state 适配成 PerItemPanel 的 state 形状
// (steps 与独立模式 per_item_state.steps 同形; config=null 自动隐藏手动按钮/收尾卡片)
const customMixPerItemState = computed(() => {
  const s = customMixState.value;
  if (!s || s.mix_type !== 'per_item') return null;
  return {
    enabled: true,
    cycle_active: !!s.cycle_active,
    cycle_start_time: null,
    config: null,
    steps: s.steps || [],
    last_ng_detail: s.last_ng_detail || null,
  };
});

// v3.8+: per_item 派生统计 (右栏「逐件实时反馈」卡片用)
// 聚合所有 per_item 步骤的 items 数据, 给"还差几颗"+"漏哪几颗"两个核心展示供数据.
const _perItemAllItems = computed(() => {
  const steps = perItemState.value?.steps || [];
  const out = [];
  for (const s of steps) {
    for (const it of (s.items || [])) {
      out.push({ ...it, _step_label: s.display_label || s.label });
    }
  }
  return out;
});
const perItemTotal = computed(() => _perItemAllItems.value.length);
const perItemCovered = computed(() => _perItemAllItems.value.filter(it => it.covered).length);
const perItemRemaining = computed(() => Math.max(0, perItemTotal.value - perItemCovered.value));
const perItemMissingIds = computed(() => _perItemAllItems.value.filter(it => !it.covered).map(it => it.id));
const perItemElapsedSec = computed(() => {
  const start = perItemState.value?.cycle_start_time;
  if (!start) return 0;
  return Math.max(0, (Date.now() / 1000) - start);
});

// MES 实时数据 (从轮询结果中获取)
const mesData = computed(() => multiChannelData.value[selectedChannel.value]?.mes || null);

// 工件卡片显示覆盖：周期结束时先把最终结果 OK/NG 覆盖上去保留几秒，然后清空让 UI 回到"等待扫码..."
// undefined = 跟随 mesData.workpiece；object = 强制显示这个快照；null = 强制隐藏（显示等待扫码提示）
//
// v3.1.3: 改为按 channel 索引存储 (workpieceOverridesByCh: Record<channelId, override>),
// 让双工位/4 工位每个工位都有自己的"等待扫码..."/OK/NG 显示状态.
// 现有 workpieceOverride 计算属性指向 selectedChannel 的 override (向后兼容旧代码).
const workpieceOverridesByCh = ref({});
const workpieceOverrideTimers = {};   // {ch: timeoutId} for OK/NG hold
const workpieceHideTimers = {};       // {ch: timeoutId} for hide-after-hold
const WORKPIECE_RESULT_HOLD_MS = 3500;  // 结果 OK/NG 标签保留时长

// D6: 卸载/切工位时清掉所有工位的工件结果倒计时定时器。原来只清了
// selectedChannel 的 legacy timer, 非当前工位的 per-channel timer 会泄漏,
// 长跑(主窗常年不关) + 频繁切工位会堆积。这里把两张按工位的 timer 表全清。
const clearAllWorkpieceTimers = () => {
  for (const k of Object.keys(workpieceOverrideTimers)) {
    if (workpieceOverrideTimers[k]) clearTimeout(workpieceOverrideTimers[k]);
    workpieceOverrideTimers[k] = null;
  }
  for (const k of Object.keys(workpieceHideTimers)) {
    if (workpieceHideTimers[k]) clearTimeout(workpieceHideTimers[k]);
    workpieceHideTimers[k] = null;
  }
};

const workpieceOverride = computed({
  get: () => {
    const ch = selectedChannel.value;
    return ch in workpieceOverridesByCh.value ? workpieceOverridesByCh.value[ch] : undefined;
  },
  set: (v) => {
    const ch = selectedChannel.value;
    if (v === undefined) {
      delete workpieceOverridesByCh.value[ch];
    } else {
      workpieceOverridesByCh.value[ch] = v;
    }
  },
});

// 旧的 workpieceOverrideTimer / workpieceHideTimer 通过 channel-aware getter/setter 提供,
// 仍然只代理 selectedChannel 的 timer (旧代码路径 = 单工位, 等价语义不变).
let workpieceOverrideTimer = null;
let workpieceHideTimer = null;

// 监控页"任务信息条"逐要素显示开关 (后端入站配置 task_info_display; 默认全关 = 原界面)
const taskInfoDisplay = ref({
  show_task_no: false,
  show_product_code: false,
  show_step_code: false,
  show_operator: false,
});
// 任一要素打开才需要渲染追加标签
const taskInfoAnyOn = computed(() =>
  taskInfoDisplay.value.show_task_no ||
  taskInfoDisplay.value.show_product_code ||
  taskInfoDisplay.value.show_step_code ||
  taskInfoDisplay.value.show_operator
);
async function loadTaskInfoDisplay() {
  try {
    const cfg = (await getInboundConfig())?.data || {};
    const t = cfg.task_info_display || {};
    taskInfoDisplay.value = {
      show_task_no: t.show_task_no === true,
      show_product_code: t.show_product_code === true,
      show_step_code: t.show_step_code === true,
      show_operator: t.show_operator === true,
    };
  } catch (e) {
    // 取不到配置时维持默认全关, 不影响原界面
  }
}
// 从某工位活跃工单提取要按开关显示的任务要素 [{label, value}]
function getTaskInfoItemsFor(ch) {
  if (!taskInfoAnyOn.value) return [];
  const order = getMesDataFor(ch)?.order;
  if (!order) return [];
  const inbound = order.extra_data?.inbound || {};
  const t = taskInfoDisplay.value;
  const items = [];
  if (t.show_task_no && (order.order_no || inbound.task_no))
    items.push({ label: '任务号', value: order.order_no || inbound.task_no });
  if (t.show_product_code && (order.product_code || inbound.product_code))
    items.push({ label: '产品代号', value: order.product_code || inbound.product_code });
  if (t.show_step_code && inbound.step_code)
    items.push({ label: '工序工步', value: inbound.step_code });
  if (t.show_operator && (inbound.operator || order.created_by))
    items.push({ label: '操作员', value: inbound.operator || order.created_by });
  return items;
}

// per-channel 工件展示 helper (双工位/4 工位每个 ch 用各自的数据)
function getMesDataFor(ch) {
  return multiChannelData.value[ch]?.mes || null;
}
function getDisplayWorkpieceFor(ch) {
  const ov = workpieceOverridesByCh.value[ch];
  if (ov === null) return null;            // 强制"等待扫码..."
  if (ov) return ov;                       // OK/NG 临时快照
  return multiChannelData.value[ch]?.mes?.workpiece || null;
}
function shouldShowMesBarFor(ch) {
  const mes = getMesDataFor(ch);
  const wp = getDisplayWorkpieceFor(ch);
  if (wp) return true;
  if (mes?.order) return true;
  if (mes?.warn_no_barcode && hasScannerFor(ch)) return true;
  if (workpieceOverridesByCh.value[ch] === null) return true;  // 显示"等待扫码"
  // v3.1.3: 多工位下只要工位在跑就显示信息条 (默认 "等待扫码..."),
  // 让客户能直观看到这一栏的存在 (单工位另有路径已显示)
  if (channelCount.value > 1) {
    const ch_data = multiChannelData.value[ch];
    if (ch_data?.isRunning || ch_data?.isDetecting) return true;
  }
  return false;
}

const displayWorkpiece = computed(() => {
  return getDisplayWorkpieceFor(selectedChannel.value);
});

// 真实工件变化（新扫码绑了新工件）立即恢复默认显示，清掉残留的 override
watch(
  () => mesData.value?.workpiece?.serial_no,
  (newSn, oldSn) => {
    if (newSn && newSn !== oldSn) {
      if (workpieceOverrideTimer) { clearTimeout(workpieceOverrideTimer); workpieceOverrideTimer = null; }
      if (workpieceHideTimer) { clearTimeout(workpieceHideTimer); workpieceHideTimer = null; }
      workpieceOverride.value = undefined;
    }
  }
);

// 外部 MES 额外字段
const extraFieldsSchema = ref([]);
const extraFieldValues = ref({});
async function loadExtraFieldsSchema() {
  try {
    const { data } = await getExtraFieldsSchema();
    extraFieldsSchema.value = data || [];
    for (const f of extraFieldsSchema.value) {
      if (!(f.key in extraFieldValues.value) && f.default) {
        extraFieldValues.value[f.key] = f.default;
      }
    }
    if (Object.keys(extraFieldValues.value).length) submitExtraFields();
  } catch { /* ignore */ }
}
async function submitExtraFields() {
  try {
    await setExtraFields({ channel_id: selectedChannel.value, fields: { ...extraFieldValues.value } });
  } catch { /* ignore */ }
}

const trackingChecklist = ref({});
const trackingCycleActive = ref(false);
const trackingContainerMode = ref(false);
const trackingBoxes = ref({});
const trackingSettledCount = ref(0);
const trackingSettledOk = ref(0);
const trackingSettledNg = ref(0);
const serverModelTask = ref('detect');

// v3.5.0: 周期性强制动作进度
const periodicActions = ref([]);

// 默认计数器定义（系统内置，不可删除）
const DEFAULT_COUNTERS = ['总产量', '合格总数', '不良总数', 'NG步骤'];

// 计数器数据 - 按固定顺序排列：总产量、合格总数在上，不良总数、NG步骤在下，然后是自定义计数器
const counters = computed(() => {
  if (!currentProject.value?.counters_config) return [];
  
  const allCounters = currentProject.value.counters_config;
  const displaySettings = systemStore.display.monitor.defaultCounters || {};
  
  // 分离默认计数器和自定义计数器
  const defaultCounters = [];
  const customCounters = [];
  
  // 按固定顺序添加默认计数器（如果启用显示）
  DEFAULT_COUNTERS.forEach(name => {
    const counter = allCounters.find(c => c.name === name);
    if (counter) {
      // 检查是否启用显示（默认显示）
      const showKey = name === '总产量' ? 'showTotal' : 
                      name === '合格总数' ? 'showGood' : 
                      name === '不良总数' ? 'showBad' : 
                      name === 'NG步骤' ? 'showNgSteps' : 'show';
      if (displaySettings[showKey] !== false) {
        defaultCounters.push(counter);
      }
    }
  });
  
  // 添加自定义计数器（排除默认计数器）
  // v3.8.x: 自定义计数器默认不在监控页统计板块显示,
  // 必须在 Project 页勾选 show_in_monitor=true 才会出现。
  // 旧项目数据缺该字段时按"未勾选"处理 (符合"默认不显示"的设计意图),
  // 客户感觉之前没设过的计数器突然消失, 这是预期行为, 改完去 Project 页勾上即可。
  allCounters.forEach(counter => {
    if (!DEFAULT_COUNTERS.includes(counter.name)) {
      if (counter.show_in_monitor === true) {
        customCounters.push(counter);
      }
    }
  });
  
  return [...defaultCounters, ...customCounters];
});

// 三个内置计数器（检测次数、OK次数、NG次数）并排
const BUILTIN_THREE = ['总产量', '合格总数', '不良总数'];
const builtinCounters = computed(() => {
  return counters.value.filter(c => BUILTIN_THREE.includes(c.name));
});

// 其余计数器（NG步骤 + 自定义）
const extraCounters = computed(() => {
  return counters.value.filter(c => !BUILTIN_THREE.includes(c.name));
});

// 显示名称映射
const getCounterDisplayName = (name) => {
  switch (name) {
    case '总产量': return '检测次数';
    case '合格总数': return 'OK次数';
    case '不良总数': return 'NG次数';
    default: return name;
  }
};

// 根据计数器名称返回颜色类
const getCounterColor = (name) => {
  switch (name) {
    case '总产量': return 'text-white';
    case '合格总数': return 'text-green-400';
    case '不良总数': return 'text-red-400';
    case 'NG步骤': return 'text-orange-500';
    default: return 'text-white';
  }
};

// NG 步骤排名数据
const ngStepRanking = ref([]);
const ngStepCountMap = ref({});

const toggleNgTopMode = () => {
  systemStore.display.monitor.ngTopDisplayMode =
    systemStore.display.monitor.ngTopDisplayMode === 'percentage' ? 'count' : 'percentage';
  localStorage.setItem('display_settings', JSON.stringify(systemStore.display));
};

// 逻辑模式文本
const logicModeText = computed(() => {
  const mode = currentProject.value?.logic_mode;
  switch(mode) {
    case 'sequential': return '顺序模式';
    case 'detection': return '检测模式';
    case 'custom': return '自定义模式';
    default: return '未设置';
  }
});

// 事件提示框
const activeToasts = ref([]);
let toastIdCounter = 0;

// 获取提示框配置（ch 不为 null 时使用通道级别设置）
const getToastConfig = (toastId, ch = null) => {
  const det = (ch != null && channelCount.value > 1) ? systemStore.getChannelDetection(ch) : systemStore.detection;
  if (toastId === 'ok') return det.toasts.ok;
  if (toastId === 'ng') return det.toasts.ng;
  if (toastId === 'scan') return det.toasts.scan;
  if (toastId === 'warn_no_barcode') return det.toasts.warn_no_barcode;
  const customToast = (det.customToasts || []).find(t => t.id === toastId);
  if (customToast) return customToast;
  return det.toasts.ok;
};

// 提示框位置样式
const getPositionClass = (position) => {
  switch (position) {
    case 'top-right': return 'top-24 right-8';
    case 'top-left': return 'top-24 left-4';
    case 'bottom-right': return 'bottom-8 right-8';
    case 'bottom-left': return 'bottom-8 left-4';
    case 'center': return 'top-24 left-[29%] -translate-x-1/2';
    default: return 'top-24 right-8';
  }
};

// 默认位置（兼容旧代码）
const toastPositionClass = computed(() => {
  return getPositionClass(systemStore.detection.toasts.ok.position);
});

// TTS voice announcement — queue mode: voices play sequentially, never cancel each other
const speechQueue = [];
let isSpeaking = false;
// D5: 语音队列上限。TTS 卡住/语速慢时队列会越堆越多, 高 NG 率刷屏更明显。
// 设 20 远高于正常节拍(正常队列 0~1); 到顶丢最旧, 且合并连续相同播报。
const MAX_SPEECH_QUEUE = 20;
const _playNext = () => {
  if (!speechQueue.length) { isSpeaking = false; return; }
  isSpeaking = true;
  const { text, volume } = speechQueue.shift();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = 'zh-CN';
  utterance.volume = volume;
  utterance.rate = 1.1;
  utterance.onend = () => _playNext();
  utterance.onerror = () => _playNext();
  window.speechSynthesis.speak(utterance);
};
const speak = (text, ch = null) => {
  const det = (ch != null && channelCount.value > 1) ? systemStore.getChannelDetection(ch) : systemStore.detection;
  if (!det.voiceEnabled || !window.speechSynthesis) return;
  // 合并连续相同播报(常见: 连续 NG 刷屏), 队尾相同就不重复入队
  const last = speechQueue[speechQueue.length - 1];
  if (last && last.text === text) return;
  // 上限保护: 队列积压(TTS 卡住)时丢最旧, 防无限涨
  if (speechQueue.length >= MAX_SPEECH_QUEUE) speechQueue.shift();
  speechQueue.push({ text, volume: det.voiceVolume ?? 1.0 });
  if (!isSpeaking) _playNext();
};

// 显示提示框（新版：根据 toast_id 获取配置）
const showToastById = (toastId, eventName, reason = '') => {
  dbg('monitor.events', `事件提示 [${toastId}] ${eventName || ''}`, reason || '');
  const config = getToastConfig(toastId);
  
  const icons = {
    ok: CircleCheck,
    ng: CircleClose,
    custom: Warning
  };
  
  let subtitle = config.subText || '';
  if (toastId === 'ng' && reason && systemStore.detection.showNgReason) {
    subtitle = reason;
  }
  
  const toast = {
    id: ++toastIdCounter,
    toastId,
    title: config.text || eventName,
    subtitle: subtitle,
    color: config.color,
    fontSize: config.fontSize,
    position: config.position,
    icon: toastId === 'ok' ? icons.ok : (toastId === 'ng' ? icons.ng : icons.custom)
  };
  
  activeToasts.value.push(toast);
  
  // Voice announcement
  if (toastId === 'ok') {
    speak('合格');
  } else if (toastId === 'ng') {
    const showReason = systemStore.detection.showNgReason && reason;
    speak(showReason ? `不合格，${reason}` : '不合格');
  } else {
    speak(config.text || eventName || '事件触发');
  }
  
  // 自动移除
  setTimeout(() => {
    const idx = activeToasts.value.findIndex(t => t.id === toast.id);
    if (idx > -1) {
      activeToasts.value.splice(idx, 1);
    }
  }, config.duration * 1000);
};

// 兼容旧版显示提示框
const showToast = (type, title, subtitle = '') => {
  const toastId = type === 'ok' ? 'ok' : (type === 'ng' ? 'ng' : 'ok');
  const config = getToastConfig(toastId);
  
  const icons = {
    ok: CircleCheck,
    ng: CircleClose,
    custom: Warning
  };
  
  const toast = {
    id: ++toastIdCounter,
    toastId,
    title: title || config.text,
    subtitle: config.subText || '',
    color: config.color,
    fontSize: config.fontSize,
    position: config.position,
    icon: icons[type] || icons.custom
  };
  
  activeToasts.value.push(toast);
  
  // 自动移除
  setTimeout(() => {
    const idx = activeToasts.value.findIndex(t => t.id === toast.id);
    if (idx > -1) {
      activeToasts.value.splice(idx, 1);
    }
  }, config.duration * 1000);
};

const getMultiPositionClass = (position) => {
  switch (position) {
    case 'top-right': return 'top-2 right-2';
    case 'top-left': return 'top-2 left-2';
    case 'bottom-right': return 'bottom-2 right-2';
    case 'bottom-left': return 'bottom-2 left-2';
    case 'center': return 'top-2 left-1/2 -translate-x-1/2';
    default: return 'top-2 right-2';
  }
};

const showMultiToast = (ch, toastId, eventName, reason = '', eventId = null) => {
  const det = systemStore.getChannelDetection(ch);
  const config = getToastConfig(toastId, ch);
  const icons = { ok: CircleCheck, ng: CircleClose, custom: Warning };

  let subtitle = config.subText || '';
  if (toastId === 'ng' && reason && det.showNgReason) {
    subtitle = reason;
  }

  const toast = {
    id: ++toastIdCounter,
    toastId,
    // 原始事件编号(1=合格OK / 2=不良NG / 其它=自定义事件)，透传给 cycle-result.indicator 插槽，
    // 让整页覆盖型插件能按事件身份决定"内置OK/NG交给自绘横幅、自定义事件仍弹标准提示框"。
    eventId,
    title: config.text || eventName,
    subtitle,
    color: config.color,
    fontSize: config.fontSize,
    position: config.position,
    icon: toastId === 'ok' ? icons.ok : (toastId === 'ng' ? icons.ng : icons.custom)
  };

  if (!multiActiveToasts.value[ch]) multiActiveToasts.value[ch] = [];
  multiActiveToasts.value[ch].push(toast);
  while (multiActiveToasts.value[ch].length > 2) {
    multiActiveToasts.value[ch].shift();
  }

  if (toastId === 'ok') {
    speak('合格', ch);
  } else if (toastId === 'ng') {
    const showReason = det.showNgReason && reason;
    speak(showReason ? `不合格，${reason}` : '不合格', ch);
  } else {
    speak(config.text || eventName || '事件触发', ch);
  }

  setTimeout(() => {
    const arr = multiActiveToasts.value[ch];
    if (arr) {
      const idx = arr.findIndex(t => t.id === toast.id);
      if (idx > -1) arr.splice(idx, 1);
    }
  }, config.duration * 1000);
};

// 格式化步骤检测时间
const formatStepTime = (stepLabel) => {
  const timestamp = stepDetectionTimes.value[stepLabel];
  if (!timestamp) return '--:--:--';
  
  const date = new Date(timestamp * 1000);
  const hours = date.getHours().toString().padStart(2, '0');
  const minutes = date.getMinutes().toString().padStart(2, '0');
  const seconds = date.getSeconds().toString().padStart(2, '0');
  return `${hours}:${minutes}:${seconds}`;
};

// 格式化步骤耗时（与 formatStepPT 行为一致，跟随 ptMode）
const formatDuration = (stepLabel) => formatStepPT(stepLabel);

// 格式化步骤检测时间（PT）— 由 ptCalcMode × ptAggregate × ptMode 共同决定数据源
//   ptCalcMode (v3.9.x D 方案):
//     span    → 用 step_durations / cycle_sum_step_durations (跨度: 首次出现到消失)
//     visible → 用 step_visible_seconds (累计: 标签每帧"在画面里"的时长之和)
//   ptAggregate:
//     sum  → 同步骤一周期内多次出现按 SUM 合并（默认 v3.5.x）
//     last → 仅取最后一段（旧行为）
//   ptMode:
//     avg     → 历史平均
//     last    → 最近一个已结束 cycle 的取值
//     current → 当前正在跑 cycle 的取值
//
// visible 口径下: avg / last 仍走 span 历史档 (后端没透出 visible 历史, 真要做历史
// 累计统计意义不大), 只有 current 改用累计可见时长.
const formatStepPT = (stepLabel) => {
  const mode = systemStore.display?.monitor?.ptMode || 'current';
  const agg = systemStore.display?.monitor?.ptAggregate || 'sum';
  const calcMode = systemStore.display?.monitor?.ptCalcMode || 'span';
  // v3.10.x: 多次出现的合并策略 (仅 ptAggregate='sum' + ptMode='current' 时生效)
  const accStrat = systemStore.display?.monitor?.ptAccumulateStrategy || 'max';

  // visible + current: 直接用累计可见时长字段, 不走 sum/last 矩阵 (visible 本身就是累计)
  if (calcMode === 'visible' && mode === 'current') {
    const v = stepVisibleSeconds.value ? stepVisibleSeconds.value[stepLabel] : undefined;
    if (v === undefined || v === null) return '--';
    if (v <= 0) return '--';
    return `${v.toFixed(1)}s`;
  }

  let src;
  if (agg === 'sum') {
    if (mode === 'last') src = lastCycleSumStepDurations.value;
    else if (mode === 'current') src = cycleSumStepDurations.value;
    else src = avgCycleSumStepDurations.value;
  } else {
    if (mode === 'last') src = lastStepDurations.value;
    else if (mode === 'current') src = stepDurations.value;
    else src = avgStepDurations.value;
  }
  let duration = src ? src[stepLabel] : undefined;
  // v3.10.x: ptAggregate='sum' + ptMode='current' 时, max/first 策略改读分段历史现算
  // (后端 cycle_sum_step_durations 始终是 SUM, 不动它的语义)
  if (agg === 'sum' && mode === 'current' && accStrat !== 'sum') {
    const segs = stepCycleSegments.value ? stepCycleSegments.value[stepLabel] : undefined;
    if (Array.isArray(segs) && segs.length > 0) {
      if (accStrat === 'max') {
        duration = Math.max(...segs);
      } else if (accStrat === 'first_only' || accStrat === 'first') {
        duration = segs[0];
      }
    }
  }
  // v3.8.x: 权威值没有 (步骤还在画面里, 没完成) → fallback 到 in-flight 实时 PT, 让用户看到渐增数字。
  // 仅 mode='current' 时 fallback (avg/last 是历史值, 不该被 in-flight 干扰)。
  if ((duration === undefined || duration === null) && mode === 'current') {
    const inflight = stepInflightDurations.value[stepLabel];
    if (inflight !== undefined && inflight !== null) duration = inflight;
  }
  if (duration === undefined || duration === null) return '--';
  if (duration <= 0) return '--';
  return `${duration.toFixed(1)}s`;
};

// formatInterval（步骤间隔格式化）已随 SOP 面板外置到 SopStepPanel.vue（M-2）。

// CT 取值: 根据 ctMode 三档 + ctIncludeNg 是否含 NG
//   - current 模式: ctIncludeNg 不影响 (cycle 还没结束分不出 OK/NG, 直接用当前已耗时)
//   - avg / last: 跟随 ctIncludeNg 切普通版/含 NG 版
const getDisplayCT = (chData) => {
  if (!chData) return '--';
  const mode = systemStore.display?.monitor?.ctMode || 'avg';
  const includeNg = systemStore.display?.monitor?.ctIncludeNg;
  let val = 0;
  if (mode === 'current') {
    val = chData.currentCycleTime || 0;
  } else if (mode === 'last') {
    val = includeNg ? chData.lastCycleTimeWithNg : chData.lastCycleTime;
  } else {
    val = includeNg ? chData.avgCycleTimeWithNg : chData.avgCycleTime;
  }
  return val ? val.toFixed(1) + 's' : '--';
};

const displayCT = computed(() => {
  const mode = systemStore.display?.monitor?.ctMode || 'avg';
  const includeNg = systemStore.display?.monitor?.ctIncludeNg;
  let val = 0;
  if (mode === 'current') {
    val = currentCycleTime.value;
  } else if (mode === 'last') {
    val = includeNg ? lastCycleTimeWithNg.value : lastCycleTime.value;
  } else {
    val = includeNg ? cycleTimeWithNg.value : cycleTime.value;
  }
  return val || 0;
});

// 获取步骤样式（步骤设置区域用）
const getStepClass = (step) => {
  if (step.status === 'active') {
    return 'border-cyan-500 shadow-[0_0_10px_rgba(6,182,212,0.3)]';
  } else if (step.status === 'completed') {
    return 'border-green-500 bg-green-900/20';
  } else {
    return 'border-slate-700 opacity-60';
  }
};

// SOP 卡片样式(getSopCardClass/Header/Body)与自动滚动实现已随 SOP 面板
// 外置到 SopStepPanel.vue（M-2）, 父级只保留轮询侧的驱动调用。

// 格式化视频时间（秒转 mm:ss）
const formatVideoTime = (seconds) => {
  if (!seconds || isNaN(seconds)) return '00:00';
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
};

// 处理视频进度条变化
const handleProgressChange = async () => {
  try {
    await api.post('/source/video/progress', { progress: videoInfo.value.progress });
    
    // 立即获取最新的视频信息（更新时间显示）
    const videoRes = await api.get('/source/video/info');
    if (videoRes.data.status === 'success') {
      videoInfo.value.currentTime = videoRes.data.current_time || 0;
      videoInfo.value.duration = videoRes.data.duration || 0;
    }
    
    // 强制重连视频流
    if (progressReconnectTimer) {
      clearTimeout(progressReconnectTimer);
      progressReconnectTimer = null;
    }
    progressReconnectTimer = setTimeout(() => {
      progressReconnectTimer = null;
      if (!monitorMounted) return;
      forceReconnectStream();
      sourceStore.setSourceType('video');
    }, 100);
  } catch (err) {
    console.error('设置视频进度失败:', err);
    ElMessage.error('设置进度失败');
  } finally {
    // 确保拖动状态被重置
    isDraggingProgress.value = false;
  }
};

// 处理视频倍速变化
const handleSpeedChange = async (speed) => {
  try {
    isChangingSpeed.value = true;  // 防止轮询覆盖
    await api.post('/source/video/speed', { speed });
    ElMessage.success(`播放倍速已设为 ${speed}x`);
    // 延迟解除保护，确保后端已更新
    if (speedGuardTimer) {
      clearTimeout(speedGuardTimer);
      speedGuardTimer = null;
    }
    speedGuardTimer = setTimeout(() => {
      speedGuardTimer = null;
      if (!monitorMounted) return;
      isChangingSpeed.value = false;
    }, 500);
  } catch (err) {
    console.error('设置视频倍速失败:', err);
    ElMessage.error('设置倍速失败');
    isChangingSpeed.value = false;
  }
};

// 处理同步模式变化
const handleSyncModeChange = async (enabled) => {
  try {
    await api.post('/source/video/sync-mode', { enabled });
    const modeName = enabled ? '逐帧检测模式' : '正常播放模式';
    ElMessage.success(`已切换到${modeName}`);
    sourceStore.setVideoSyncMode(enabled);
  } catch (err) {
    console.error('设置同步模式失败:', err);
    ElMessage.error('设置同步模式失败');
    // 恢复原状态
    videoInfo.value.syncMode = !enabled;
  }
};


// 调整 canvas 大小
// 叠加层闪烁诊断: 记"画布尺寸重置"和"有框/空结果"边沿 (调试设置「视频流」开关)
let _ovlResizeWxH = '';
let _ovlHadBoxes = false;
// 状态轮询诊断: 摘要节流 + 人工确认阻塞边沿 (调试设置「状态轮询」开关)
let _pollLastSummary = 0;
let _pollLastAck = false;

const resizeCanvas = () => {
  if (!videoElement.value || !detectionCanvas.value) return;
  
  const video = videoElement.value;
  const canvas = detectionCanvas.value;
  
  canvas.width = video.offsetWidth;
  canvas.height = video.offsetHeight;

  const _wh = `${canvas.width}x${canvas.height}`;
  if (_wh !== _ovlResizeWxH) {
    dbg('monitor.video', '画布尺寸重置', `${_ovlResizeWxH || '初始'}→${_wh} (重置会清空叠加框→闪一下没框)`);
    _ovlResizeWxH = _wh;
  }
};

// 绘制检测框
const drawDetections = (detections) => {
  if (!detectionCanvas.value) return;
  
  const canvas = detectionCanvas.value;
  const ctx = canvas.getContext('2d');
  
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  // 闪烁边沿: 有框↔空结果切换才记一条 (6.7Hz 轮询, 不能逐帧打)
  const _has = !!(detections && detections.length);
  if (_has !== _ovlHadBoxes) {
    dbg('monitor.video', _has ? '叠加层恢复画框' : '叠加层清空(空结果)',
        `检出=${detections ? detections.length : 0} (空结果清框=画面闪烁直接元凶, 看推理是否间歇空)`);
    _ovlHadBoxes = _has;
  }

  if (!detections || detections.length === 0) return;
  
  // 获取启用的步骤标签列表
  const stepsConfig = currentProject.value?.steps_config || [];
  const enabledLabels = new Set(
    stepsConfig
      .filter(s => s.enabled !== false)  // 默认启用
      .map(s => s.label)
  );
  // v2.7.4: 项目配置中标记 hide_in_view=true 的标签，画面上不画框（仅视觉隐藏）
  const hiddenLabels = new Set(
    stepsConfig.filter(s => s && s.hide_in_view && s.label).map(s => s.label)
  );
  
  const boxColor = systemStore.detection.boxColor;
  const ngColor = systemStore.detection.boxColorNG;
  const lineWidth = systemStore.detection.boxLineWidth;
  const fontSize = systemStore.detection.labelFontSize * (window.__uiScale || 1);
  const showConf = systemStore.detection.showConfidence;
  
  // object-contain offset: compute actual rendered image area within canvas
  const img = videoElement.value;
  let offsetX = 0, offsetY = 0, renderW = canvas.width, renderH = canvas.height;
  if (img && img.naturalWidth && img.naturalHeight) {
    const imgAspect = img.naturalWidth / img.naturalHeight;
    const canvasAspect = canvas.width / canvas.height;
    if (imgAspect > canvasAspect) {
      renderW = canvas.width;
      renderH = canvas.width / imgAspect;
      offsetY = (canvas.height - renderH) / 2;
    } else {
      renderH = canvas.height;
      renderW = canvas.height * imgAspect;
      offsetX = (canvas.width - renderW) / 2;
    }
  }

  const pipeCfg = currentProject.value?.pipeline_config || {};

  // v3.x per_item: 按"扭完/未扭"覆盖态给 item_label 检测框上色.
  // 默认关 (color_by_coverage=false) → 零行为变化; 颜色留空回退全局 OK/NG 色.
  const piState = perItemState.value;
  const piCfg = piState?.config;
  const colorByCoverage = isPerItemMode.value && !!piCfg?.color_by_coverage;
  const showItemNumbers = isPerItemMode.value && !!piCfg?.show_item_numbers;
  let coverByLabel = null;
  let covColorOn = boxColor;
  let covColorOff = ngColor;
  if ((colorByCoverage || showItemNumbers) && Array.isArray(piState?.steps)) {
    coverByLabel = new Map();
    for (const st of piState.steps) {
      if (!st || !st.item_label || !Array.isArray(st.items)) continue;
      let arr = coverByLabel.get(st.item_label);
      if (!arr) { arr = []; coverByLabel.set(st.item_label, arr); }
      for (const it of st.items) {
        if (Array.isArray(it.bbox) && it.bbox.length === 4) arr.push(it);
      }
    }
    if (coverByLabel.size === 0) coverByLabel = null;
    covColorOn = piCfg.box_color_covered || boxColor;
    covColorOff = piCfg.box_color_uncovered || ngColor;
  }
  const _iouNorm = (ax, ay, aw, ah, bx, by, bw, bh) => {
    const ix1 = Math.max(ax, bx), iy1 = Math.max(ay, by);
    const ix2 = Math.min(ax + aw, bx + bw), iy2 = Math.min(ay + ah, by + bh);
    const iw = Math.max(0, ix2 - ix1), ih = Math.max(0, iy2 - iy1);
    const inter = iw * ih;
    const uni = aw * ah + bw * bh - inter;
    return uni > 0 ? inter / uni : 0;
  };
  // 把检测框配到对应个体 (返回该个体: 含 id + covered), 编号/上色共用
  const perItemHitFor = (det, cb) => {
    if (!coverByLabel) return null;
    const items = coverByLabel.get(det.label);
    if (!items || !items.length) return null;
    let best = 0.3, hit = null;
    for (const it of items) {
      const b = it.bbox;
      const iou = _iouNorm(cb.x, cb.y, cb.w, cb.h, b[0], b[1], b[2], b[3]);
      if (iou > best) { best = iou; hit = it; }
    }
    return hit;
  };

  detections.forEach(det => {
    if (!enabledLabels.has(det.label)) return;
    if (det.hidden) return;
    if (hiddenLabels.has(det.label)) return;
    if (!shouldDrawDetWithStepRoi(det, stepsConfig, pipeCfg)) return;
    const cb = clipNormalizedBox(det);
    const x = offsetX + cb.x * renderW;
    const y = offsetY + cb.y * renderH;
    const w = cb.w * renderW;
    const h = cb.h * renderH;
    
    // v3.7.5: 颜色优先级 步骤 box_color > 副模型 display_color > OK/NG 兜底.
    // (v3.7.2 FIX-381-B 原先把副模型色放最高, 但客户在步骤列表配的颜色被覆盖,
    // 与 tooltip 文案"任何模式都生效"矛盾, 现翻转优先级让用户配置说了算.)
    // v3.x per_item: color_by_coverage 开启时, item_label 框按覆盖态优先上色.
    const piHit = (colorByCoverage || showItemNumbers) ? perItemHitFor(det, cb) : null;
    const color = (colorByCoverage && piHit)
      ? (piHit.covered ? covColorOn : covColorOff)
      : pickDetColor(det, stepsConfig, boxColor, ngColor);

    // Render polygon mask if available (segmentation model)
    if (det.mask && Array.isArray(det.mask) && det.mask.length > 2) {
      ctx.beginPath();
      det.mask.forEach((pt, i) => {
        const mxN = Math.max(0, Math.min(1, Number(pt[0]) || 0));
        const myN = Math.max(0, Math.min(1, Number(pt[1]) || 0));
        const mx = offsetX + mxN * renderW;
        const my = offsetY + myN * renderH;
        if (i === 0) ctx.moveTo(mx, my);
        else ctx.lineTo(mx, my);
      });
      ctx.closePath();
      // Step 8: color 可能是 hex (#xxxxxx) 或 rgb(...). hex 不能直接 replace 成 rgba,
      // 用 globalAlpha 兼容两种格式.
      ctx.save();
      ctx.fillStyle = color;
      ctx.globalAlpha = 0.25;
      ctx.fill();
      ctx.restore();
      ctx.strokeStyle = color;
      ctx.lineWidth = lineWidth;
      ctx.stroke();
    } else {
      ctx.strokeStyle = color;
      ctx.lineWidth = lineWidth;
      ctx.strokeRect(x, y, w, h);
    }
    
    // In tracking mode, show display_id (A1, B2, etc.) as the label
    let label = det.display_id || det.display_name || det.label || 'Unknown';
    // v3.28+ per_item: 显示螺丝编号 → 同标签各自从 #1 起 (如 5N螺丝#3 / 7N螺丝#1)
    if (showItemNumbers && piHit) {
      label = `${det.label || ''}#${piHit.id}`;
    }
    if (showConf && det.confidence) {
      label += ` ${(det.confidence * 100).toFixed(0)}%`;
    }
    
    // 绘制标签背景
    ctx.font = `bold ${fontSize}px Arial`;
    const textMetrics = ctx.measureText(label);
    const textHeight = fontSize;
    
    ctx.fillStyle = color;
    ctx.fillRect(x, y - textHeight - 4, textMetrics.width + 8, textHeight + 4);
    
    // 绘制标签文字
    ctx.fillStyle = 'white';
    ctx.fillText(label, x + 4, y - 4);
  });

  // Draw ROI polygon overlay if configured (tracking mode)
  const roiPoly = currentProject.value?.pipeline_config?.tracking_roi?.polygon;
  if (roiPoly && roiPoly.length >= 3) {
    ctx.save();
    ctx.strokeStyle = 'rgba(0, 200, 255, 0.6)';
    ctx.lineWidth = 2;
    ctx.setLineDash([8, 4]);
    ctx.beginPath();
    ctx.moveTo(offsetX + roiPoly[0][0] * renderW, offsetY + roiPoly[0][1] * renderH);
    for (let i = 1; i < roiPoly.length; i++) {
      ctx.lineTo(offsetX + roiPoly[i][0] * renderW, offsetY + roiPoly[i][1] * renderH);
    }
    ctx.closePath();
    ctx.stroke();
    ctx.fillStyle = 'rgba(0, 200, 255, 0.05)';
    ctx.fill();
    ctx.setLineDash([]);
    ctx.restore();
  }

  // Step 8 (feat/multi-model-roi-link): 副模型 ROI 多边形叠加 (各自 display_color 虚线描边).
  // 主模型 tracking_roi 已上面画完, 副模型 ROI 单独画一圈让用户清楚每个副 slot 的工作区.
  const extraRois = (currentProject.value?.pipeline_config?.models || [])
    .filter(m => m && m.name && m.name !== 'main' && Array.isArray(m.roi) && m.roi.length >= 3);
  for (const slot of extraRois) {
    ctx.save();
    ctx.strokeStyle = slot.display_color || '#f59e0b';
    ctx.lineWidth = 2;
    ctx.setLineDash([4, 6]);
    ctx.beginPath();
    ctx.moveTo(offsetX + slot.roi[0][0] * renderW, offsetY + slot.roi[0][1] * renderH);
    for (let i = 1; i < slot.roi.length; i++) {
      ctx.lineTo(offsetX + slot.roi[i][0] * renderW, offsetY + slot.roi[i][1] * renderH);
    }
    ctx.closePath();
    ctx.stroke();
    ctx.fillStyle = slot.display_color || '#f59e0b';
    ctx.globalAlpha = 0.05;
    ctx.fill();
    ctx.setLineDash([]);
    ctx.restore();
  }

  // 逐步骤 ROI (steps_config[].roi): 浅色虚线 + 微弱填充，与 tracking_roi / 副模型 ROI 区分
  const stepRois = (currentProject.value?.steps_config || []).filter(
    s => s && s.enabled !== false && Array.isArray(s.roi) && s.roi.length >= 3
  );
  const STEP_ROI_PALETTE = ['#c4b5fd', '#6ee7b7', '#fcd34d', '#f9a8d4', '#7dd3fc'];
  stepRois.forEach((s, idx) => {
    const col = STEP_ROI_PALETTE[idx % STEP_ROI_PALETTE.length];
    ctx.save();
    ctx.strokeStyle = col;
    ctx.lineWidth = 1.5;
    ctx.setLineDash([3, 5]);
    ctx.beginPath();
    ctx.moveTo(offsetX + s.roi[0][0] * renderW, offsetY + s.roi[0][1] * renderH);
    for (let i = 1; i < s.roi.length; i++) {
      ctx.lineTo(offsetX + s.roi[i][0] * renderW, offsetY + s.roi[i][1] * renderH);
    }
    ctx.closePath();
    ctx.stroke();
    ctx.fillStyle = col;
    ctx.globalAlpha = 0.04;
    ctx.fill();
    ctx.setLineDash([]);
    ctx.restore();
  });
};

// Watch for project changes to sync UI
watch(() => currentProject.value, (newProject, oldProject) => {
  if (!newProject) {
    steps.value = [];
    tableData.value = [];
    return;
  }

  if (oldProject && oldProject.id !== newProject.id) {
    isRunning.value = false;
    isPaused.value = false;
    isDetecting.value = false;
  }
  
  // 获取步骤配置
  const stepsConfig = newProject.steps_config || [];
  const logicMode = newProject.logic_mode || 'sequential';
  const pipelineConfig = newProject.pipeline_config || {};
  
  // 从顶层或 pipeline_config 获取配置（优先顶层，因为可能有更新的值）
  const sequenceOrder = newProject.sequence_order || pipelineConfig.sequence_order || [];
  const detectionSteps = newProject.detection_steps || pipelineConfig.detection_steps || [];
  const customBasedOn = newProject.custom_based_on || pipelineConfig.custom_based_on || null;
  const customSequenceOrder = newProject.custom_sequence_order || pipelineConfig.custom_sequence_order || [];
  const customDetectionSteps = newProject.custom_detection_steps || pipelineConfig.custom_detection_steps || [];
  
  let stepsToShow = [];
  
  if (logicMode === 'sequential') {
    if (sequenceOrder.length > 0) {
      stepsToShow = sequenceOrder.map(seqItem => {
        return stepsConfig.find(s => s.id === seqItem.step_id);
      }).filter(Boolean);
    } else {
      stepsToShow = stepsConfig.filter(s => s.enabled);
    }
  } else if (logicMode === 'detection') {
    if (detectionSteps.length > 0) {
      stepsToShow = detectionSteps.map(id => {
        return stepsConfig.find(s => s.id === id);
      }).filter(Boolean);
    } else {
      stepsToShow = stepsConfig.filter(s => s.enabled);
    }
  } else if (logicMode === 'custom') {
    // 自定义模式：根据 custom_based_on 决定显示哪些步骤
    if (customBasedOn === 'sequential') {
      // 基于顺序模式：使用自定义模式独立的顺序配置
      if (customSequenceOrder.length > 0) {
        stepsToShow = customSequenceOrder.map(seqItem => {
          return stepsConfig.find(s => s.id === seqItem.step_id);
        }).filter(Boolean);
      } else {
        stepsToShow = stepsConfig.filter(s => s.enabled);
      }
    } else if (customBasedOn === 'detection') {
      // 基于检测模式：使用自定义模式独立的检测配置
      if (customDetectionSteps.length > 0) {
        stepsToShow = customDetectionSteps.map(id => {
          return stepsConfig.find(s => s.id === id);
        }).filter(Boolean);
      } else {
        stepsToShow = stepsConfig.filter(s => s.enabled);
      }
    } else {
      // 不基于任何模式：显示自定义条件中涉及的步骤
      const customConditions = newProject.custom_conditions || pipelineConfig.custom_conditions || [];
      const involvedStepIds = new Set();
      customConditions.forEach(cond => {
        (cond.sequence || []).forEach(stepId => involvedStepIds.add(stepId));
      });
      if (involvedStepIds.size > 0) {
        stepsToShow = [...involvedStepIds].map(id => stepsConfig.find(s => s.id === id)).filter(Boolean);
      } else {
        stepsToShow = stepsConfig.filter(s => s.enabled);
      }
    }
  } else {
    stepsToShow = stepsConfig.filter(s => s.enabled);
  }

  // v3.2.1: 跟踪模式 — 步骤统计/SOP 只展示"每箱期望物品"中的项目,
  // 不显示作为"容器"的箱子类别(否则容器分组模式会出现"箱子"行)
  if (logicMode === 'tracking') {
    const expectedList = newProject.counting_expected_list
      || pipelineConfig.counting_expected_list
      || [];
    const expectedDict = pipelineConfig.counting_expected_items || {};
    const expectedLabels = new Set();
    expectedList.forEach(item => {
      if (item && item.label) expectedLabels.add(item.label);
    });
    Object.keys(expectedDict).forEach(label => expectedLabels.add(label));

    if (expectedLabels.size > 0) {
      stepsToShow = stepsToShow.filter(s => expectedLabels.has(s.label));
    } else {
      // 兜底:用户未填期望清单时,仅排除容器 label,其它步骤仍展示
      const containerLabel = newProject.tracking_container_label
        || pipelineConfig.tracking_container_label
        || '';
      if (containerLabel) {
        stepsToShow = stepsToShow.filter(s => s.label !== containerLabel);
      }
    }
  }

  // v2.7.4: 过滤掉 backup_for 和 hide_in_view 步骤（仅视觉隐藏，不影响检测/数据）
  stepsToShow = stepsToShow.filter(s => !s.backup_for && !s.hide_in_view);

  // 更新步骤条 - 同时保存 label 用于后端匹配
  // v3.10.x: SOP 卡片"图永不空"策略 — 重建步骤数组时按 label 从旧数组继承缩略图.
  // (切项目: 新 label 找不到 → 自然落 null; 同项目 deep 变化: label 不变 → 完美继承)
  const oldByLabel = Object.fromEntries(
    (steps.value || []).map(s => [s.label, s.screenshot])
  );
  steps.value = stepsToShow.map((s, idx) => ({
    id: s.id,
    name: s.displayLabel || s.label,
    label: s.label,
    status: 'pending',
    result: null,
    cycleResult: null,
    screenshot: oldByLabel[s.label] || null,
  }));

  // 更新表格数据
  tableData.value = stepsToShow.map(s => ({
    step: s.displayLabel || s.label,
    label: s.label,
    count: 0,
    status: 'pending',
    cycleResult: null
  }));
  
  nextTick(() => {
    updateCharts();
  });

}, { immediate: true, deep: true });

// Initialize Charts
const initCharts = () => {
  if (pieChartInstance && !pieChartInstance.isDisposed()) {
    pieChartInstance.dispose();
  }
  pieChartInstance = null;
  
  if (gaugeChartInstance && !gaugeChartInstance.isDisposed()) {
    gaugeChartInstance.dispose();
  }
  gaugeChartInstance = null;

  if (defectChartRef.value) {
    pieChartInstance = echarts.init(defectChartRef.value);
    updatePieChart();
  }

  if (capacityGaugeRef.value) {
    gaugeChartInstance = echarts.init(capacityGaugeRef.value);
    updateGaugeChart();
  }
};

// 更新饼图
const updatePieChart = () => {
  if (!pieChartInstance || pieChartInstance.isDisposed()) return;
  
  const goodCount = counters.value.find(c => c.name === '合格总数')?.value || 0;
  const badCount = counters.value.find(c => c.name === '不良总数')?.value || 0;
  
  pieChartInstance.setOption({
    color: ['#10b981', '#ef4444'],
    series: [{
      type: 'pie',
      radius: ['40%', '70%'],
      center: ['50%', '55%'],
      label: { show: false },
      data: [
        { value: goodCount || 1, name: '良品' },
        { value: badCount, name: '不良' }
      ]
    }]
  });
};

// 更新仪表盘
const updateGaugeChart = () => {
  if (!gaugeChartInstance || gaugeChartInstance.isDisposed()) return;
  
  const goodCount = counters.value.find(c => c.name === '合格总数')?.value || 0;
  const totalCount = counters.value.find(c => c.name === '总产量')?.value || 0;
  const yieldRate = totalCount > 0 ? (goodCount / totalCount * 100) : 0;
  
  gaugeChartInstance.setOption({
    series: [{
      type: 'gauge',
      center: ['50%', '60%'],
      radius: '80%',
      startAngle: 180,
      endAngle: 0,
      min: 0,
      max: 100,
      splitNumber: 5,
      itemStyle: { color: yieldRate >= 90 ? '#10b981' : yieldRate >= 70 ? '#f59e0b' : '#ef4444' },
      progress: { show: true, width: 10 },
      pointer: { show: false },
      axisLine: { lineStyle: { width: 10, color: [[1, '#334155']] } },
      axisTick: { show: false },
      splitLine: { show: false },
      axisLabel: { show: false },
      detail: { 
        valueAnimation: true, 
        offsetCenter: [0, '20%'],
        fontSize: 24,
        fontWeight: 'bold',
        color: '#fff',
        formatter: '{value}%'
      },
      data: [{ value: yieldRate.toFixed(1) }]
    }]
  });
};

// 更新所有图表
const updateCharts = () => {
  updatePieChart();
  updateGaugeChart();
};

// 检测结果轮询定时器
let pollingTimer = null;

// Helper: build and send latest project config to backend
const syncProjectConfig = async (channel = 0, explicitProject = null) => {
  let proj = explicitProject || currentProject.value;
  if (!proj) return;
  // 启动检测前先从后端拉一次最新项目数据,避免 Pinia store 缓存
  // 与 DB 不一致时把 store 里的旧 steps_config (例如其它进程改过
  // min_duration 之类) 推回后端 VSM,覆盖掉真实配置。
  if (!explicitProject && proj.id != null) {
    try {
      // v3.7.x (FIX): getProjectDetail 返回 axios response, 真正的 project 在 .data 里.
      // 老代码漏了 .data 解构, 导致 fresh.id 永远 undefined, store 永远不更新.
      // 后果: 用户在 Project 页改完副模型/切换格式后, Monitor 启动检测仍用旧
      // currentProject (pipeline_config.models 没有副模型), extraSlots=[] →
      // 走单模型老路径, 副模型彻底不加载.
      const resp = await getProjectDetail(proj.id);
      const fresh = resp?.data;
      if (fresh && fresh.id != null) {
        proj = fresh;
        if (projectStore.currentProjectId === proj.id) {
          projectStore.setCurrentProject(proj);
        }
      }
    } catch (e) {
      console.warn('[Monitor] 拉取最新项目配置失败, 使用 store 缓存:', e);
    }
  }
  const pipelineCfg = {
    ...(proj.pipeline_config || {}),
    sequence_order: proj.sequence_order || proj.pipeline_config?.sequence_order || [],
    detection_steps: proj.detection_steps || proj.pipeline_config?.detection_steps || [],
    custom_conditions: proj.custom_conditions || proj.pipeline_config?.custom_conditions || [],
    custom_based_on: proj.custom_based_on || proj.pipeline_config?.custom_based_on || 'sequential',
    settlement_mode: proj.settlement_mode || proj.pipeline_config?.settlement_mode || 'first_step',
    idle_timeout_seconds: proj.idle_timeout_seconds ?? proj.pipeline_config?.idle_timeout_seconds ?? 0
  };
  await setProjectConfig({
    project_id: proj.id,
    name: proj.name,
    task_type: proj.task_type || 'detection',
    logic_mode: proj.logic_mode || 'detection',
    steps_config: proj.steps_config || [],
    pipeline_config: pipelineCfg,
    events_config: proj.events_config || [],
    counters_config: proj.counters_config || [],
    data_config: proj.data_config || {}
  }, channel);
};

const startDetection = async () => {
  dbg('monitor.control', '点击「开始检测」', `project=${currentProject.value?.name || '无'} running=${isRunning.value} detecting=${isDetecting.value} paused=${isPaused.value}`);
  if (!currentProject.value) {
    dbg('monitor.control', '开始检测被拒: 未选择项目');
    ElMessage.warning('请先选择项目');
    return;
  }
  if (isOperating.value) { dbg('monitor.control', '开始检测被拒: 操作进行中'); return; }
  isOperating.value = true;
  
  try {
    await syncProjectConfig();

    // v3.7.x (FIX): standby/paused 恢复路径走 resumeInference/resumeDetection,
    // 不会走 /detection/start, 也不读 pipeline_config.models, 副模型永远加载不了.
    // 后端重启 + auto_load_active_project 一定会让前端进入 paused (model_loaded=true
    // 且 is_detecting=false), 客户感知"副模型 aux Mfps (未加载) 永远不变".
    // 修复: 项目配置了副模型时, 强制走完整启动路径 (release_all + load_model_into_slot
    // 逐个加载 main+aux), 跳过 resume 快路径.
    const _hasExtraSlots = (currentProject.value?.pipeline_config?.models || [])
      .some(m => m && m.name && m.name !== 'main' && m.model_id);

    // From standby: video stream still running + model loaded → just resume inference
    if (!_hasExtraSlots && isRunning.value && !isDetecting.value) {
      try {
        await resumeInference();
        isDetecting.value = true;
        projectStore.setRunningStatus(true);
        dbg('monitor.control', '开始检测成功 (待机快速恢复)');
        ElMessage.success('已从待机恢复检测');
        startPolling();
        return;
      } catch (err) {
        console.warn('待机恢复失败，回退到完整启动:', err);
        // Model not loaded or other issue — fall through to full start
      }
    }

    // From paused: camera released, need full resume
    if (!_hasExtraSlots && isPaused.value) {
      try {
        await resumeDetection();
        isPaused.value = false;
        isRunning.value = true;
        isDetecting.value = true;
        projectStore.setRunningStatus(true);
        forceReconnectStream();
        dbg('monitor.control', '开始检测成功 (暂停快速恢复)');
        ElMessage.success('检测已恢复');
        startPolling();
        return;
      } catch (err) {
        console.warn('恢复失败，回退到完整启动:', err);
        isPaused.value = false;
      }
    }

    // 有副模型时, 重置状态让下方完整启动路径正常工作
    if (_hasExtraSlots && (isRunning.value || isPaused.value || isDetecting.value)) {
      console.log('[Monitor] 检测到副模型配置, 强制走完整启动 (release+load 主+副)');
      try {
        await apiStopDetection();
      } catch (_e) { /* 旧状态可能本就没在跑, 静默 */ }
      isRunning.value = false;
      isDetecting.value = false;
      isPaused.value = false;
    }
    
    // Full start: load model, start capture + inference
    const modelId = currentProject.value.default_model_id;
    if (!modelId) {
      ElMessage.warning('请先在项目管理中配置模型');
      return;
    }
    
    const modelFormat = currentProject.value.model_format || 'pytorch_fp32';
    let modelPath;
    try {
      const resolveRes = await apiResolveModelPath(modelId, modelFormat);
      modelPath = resolveRes.data.path;
      if (resolveRes.data.fallback && modelFormat !== 'pytorch_fp32') {
        ElMessage.warning(resolveRes.data.reason || '转换模型不可用，已回退到原始模型');
      }
    } catch {
      const modelRes = await getModelDetail(modelId);
      modelPath = modelRes.data.file_path;
    }
    if (!modelPath) {
      ElMessage.error('模型文件路径无效');
      return;
    }

    if (!validateSessionName(sessionName.value)) {
      ElMessage.warning('会话 ID 不合法：' + sessionNameError.value);
      return;
    }
    const _sessionId = sessionName.value || null;

    // Step 8 (feat/multi-model-roi-link): 单工位也支持副模型
    const pipelineModels = currentProject.value?.pipeline_config?.models || [];
    const extraSlots = pipelineModels.filter(m =>
      m && m.name && m.name !== 'main' && m.model_id);
    // v3.7.x 诊断: 让客户在 F12 一眼看出多模型链路是否被触发
    console.log('[Monitor/start-detection]', {
      _fix_marker: 'v3.7.x-aux-load-fix-1',
      projectId: currentProject.value?.id,
      projectName: currentProject.value?.name,
      pipelineModelsLen: pipelineModels.length,
      pipelineModelsNames: pipelineModels.map(m => m?.name),
      extraSlotsLen: extraSlots.length,
      extraSlotsDetail: extraSlots.map(m => ({
        name: m.name, model_id: m.model_id, model_format: m.model_format,
      })),
    });
    if (extraSlots.length > 0) {
      const mainPipelineSpec = pipelineModels.find(m => m && m.name === 'main') || {};
      const specs = [{
        name: 'main', model_path: modelPath, conf: 0.25, iou: 0.45,
        display_color: mainPipelineSpec.display_color || '#10b981', priority: 100,
      }];
      const failed = [];
      for (const e of extraSlots) {
        try {
          // v3.7.x: 副模型也支持 TensorRT FP16 等加速格式 (与主模型对齐).
          // Project 页 "切换格式" 后存到 pipeline_config.models[i].model_format,
          // 此处按该值 resolve 到对应转换文件; 老项目无此字段时兜底 pytorch_fp32.
          const r = await _resolveModelPath(e.model_id, e.model_format || 'pytorch_fp32');
          specs.push({
            name: e.name, model_path: r.path,
            conf: typeof e.conf === 'number' ? e.conf : 0.25,
            iou: typeof e.iou === 'number' ? e.iou : 0.45,
            roi: Array.isArray(e.roi) && e.roi.length >= 3 ? e.roi : null,
            schedule: e.schedule || { type: 'every_frame', n: 1, events: [] },
            class_filter: Array.isArray(e.class_filter) && e.class_filter.length
              ? e.class_filter : null,
            priority: typeof e.priority === 'number' ? e.priority : 50,
            display_color: e.display_color || '#f59e0b',
            use_half: !!e.use_half,
          });
        } catch (err) {
          failed.push(e.name);
        }
      }
      if (failed.length) {
        ElMessage.warning(`副模型路径解析失败已跳过: ${failed.join(', ')}`);
      }
      await apiStartDetection({ models: specs }, undefined, undefined, 0, _sessionId);
      isRunning.value = true;
      isDetecting.value = true;
      isPaused.value = false;
      projectStore.setRunningStatus(true);
      forceReconnectStream();
      dbg('monitor.control', `开始检测成功 (多模型 ${specs.length})`, `session=${_sessionId || '自动'}`);
      ElMessage.success(`检测已开始 (主 + ${specs.length - 1} 个副模型)`);
      startPolling();
      return;
    }

    await apiStartDetection(modelPath, 0.25, 0.45, 0, _sessionId);
    isRunning.value = true;
    isDetecting.value = true;
    isPaused.value = false;
    projectStore.setRunningStatus(true);
    forceReconnectStream();
    dbg('monitor.control', '开始检测成功 (完整启动)', `session=${_sessionId || '自动'}`);
    ElMessage.success(sessionName.value ? `检测已开始（会话 ID: ${sessionName.value}）` : '检测已开始');
    startPolling();
    
  } catch (err) {
    console.error('启动检测失败:', err);
    dbgErr('monitor.control', '开始检测', err);
    ElMessage.error('启动检测失败: ' + (err.response?.data?.detail || err.message));
  } finally {
    isOperating.value = false;
  }
};

const stopDetectionHandler = async () => {
  dbg('monitor.control', '点击「停止」', `running=${isRunning.value} detecting=${isDetecting.value}`);
  if (isOperating.value) { dbg('monitor.control', '停止被拒: 操作进行中'); return; }
  // v3.3.0 码-码闭环: 单工位 stop 也走同一确认流程; 多工位时仅检查当前选中
  if (!(await confirmScanPairBeforeStop(selectedChannel.value || 0))) { dbg('monitor.control', '停止被取消: scan_pair 确认未通过'); return; }
  isOperating.value = true;
  try {
    await pauseDetection();
    isRunning.value = false;
    isDetecting.value = false;
    isPaused.value = true;
    projectStore.setRunningStatus(false);
    stopPolling();
    disconnectStream();
    dbg('monitor.control', '停止成功: 画面与检测已暂停');
    ElMessage.info('已停止：画面和检测都已暂停');
  } catch (err) {
    console.error('停止检测失败:', err);
    dbgErr('monitor.control', '停止', err);
    ElMessage.error('停止失败: ' + (err.response?.data?.detail || err.message));
  } finally {
    isOperating.value = false;
  }
};

// 步骤截图
const stepScreenshots = ref({});
// B6 截图去重（默认关）：本地已持有的步骤截图内容指纹 { label: md5 }，
// 仅在 systemStore.performance.screenshotDedup 开启时随轮询上报，后端据此省略未变截图。
const stepScreenshotHashes = ref({});

// 步骤检测时间
const stepDetectionTimes = ref({});

// 步骤耗时
const stepDurations = ref({});

// 步骤平均耗时 (average PT)
const avgStepDurations = ref({});

// v3.5.x: PT 合并档（同步骤同周期内多次出现的 SUM）
//   cycleSumStepDurations    → 当前正在跑 cycle 的 SUM（实时）
//   lastCycleSumStepDurations → 最近一个已结束 cycle 的 SUM
//   avgCycleSumStepDurations  → 历史最近 N 个 cycle 的 SUM 算术平均
const cycleSumStepDurations = ref({});
const lastCycleSumStepDurations = ref({});
const avgCycleSumStepDurations = ref({});
// v3.10.x: 当前周期内每步分段时长列表 (后端透出, 前端按 sum/max/first 切档现算)
const stepCycleSegments = ref({});

// v3.8.x: in-flight 实时 PT (步骤还在画面里持续涨, 与权威值 cycleSumStepDurations 分开)。
// 设计语义:
//   - cycleSumStepDurations[label] 存在 → 步骤已完成 (权威, 不再变), 前端标 OK
//   - stepInflightDurations[label] 存在但 cycleSum 没值 → 步骤进行中 (PT 列 fallback 显示渐增值, 不标 OK)
// 修客户反馈 "PT 还在涨, 但 OK 已经亮"。
const stepInflightDurations = ref({});

// v3.9.x D 方案: 后端透出的"累计可见时长"字典 (每帧累加 detected_labels 的可见时长之和).
// 设计语义:
//   - 后端永远算永远透出 (mgr.step_visible_seconds), 周期开始时清零
//   - 前端 ptCalcMode='visible' 时 formatStepPT 优先取本字典
//   - 解决"标签持续被识别拖长 PT"的问题: 例如涂黑残留留在画面里, 跨度 18s 但累计可见 2s
const stepVisibleSeconds = ref({});

// 步骤间隔时间
const stepIntervals = ref({});

// v3.8.x: 单工位 polling 维护的"上一轮 cycle_id"快照, 用于检测周期切换边界。
// 后端 detection/results 透出 current_cycle_id (新周期=int, 周期间隙=null),
// 前端比对到值变化 (含 null → int / int → int') 就立刻清空步骤表 status/cycleResult/cycle_sum,
// 避免上一周期 OK + 旧 PT 残留到新周期前 1-2 步做完才被覆盖的视觉 bug。
let lastSingleCycleId = null;

// Periodic double-buffer swap to release Chromium native decoder memory
let streamSwapCounter = 0;
const STREAM_SWAP_INTERVAL = 600;
let lastChartUpdate = 0;
const CHART_UPDATE_INTERVAL = 2000; // 图表最多每2秒更新一次
let lastScreenshotUpdate = 0;
const SCREENSHOT_UPDATE_INTERVAL = 1000;
let pollingInProgress = false; // 防止轮询重叠

// 开始轮询
const startPolling = () => {
  stopPolling();
  shownEventIds.value.clear();
  streamSwapCounter = 0;
  pollingInProgress = false;
  
  pollingTimer = setInterval(async () => {
    if (pollingInProgress) return;
    pollingInProgress = true;
    
    // Periodic double-buffer swap (memory release)
    streamSwapCounter++;
    if (streamSwapCounter >= STREAM_SWAP_INTERVAL) {
      streamSwapCounter = 0;
      swapStream();
    }
    try {
      // B6 截图去重（默认关）：开启时把本地已持有的步骤截图指纹上报，后端省略未变截图。
      let knownShots = null;
      if (systemStore.performance?.screenshotDedup) {
        const h = stepScreenshotHashes.value;
        const parts = Object.keys(h).map((k) => `${k}:${h[k]}`);
        if (parts.length) knownShots = parts.join(',');
      }
      const res = await getDetectionResults(0, knownShots);
      const data = res.data;
      // 后端回传的截图指纹（仅去重开启时存在）→ 更新本地，供下次轮询比对。
      if (data.step_screenshot_hashes) {
        stepScreenshotHashes.value = data.step_screenshot_hashes;
      }
      
      if (data.source_type) {
        sourceStore.setSourceType(data.source_type);
      }
      
      fps.value = data.fps || 0;
      latency.value = data.latency || 0;
      detectionCount.value = (data.detections || []).length;

      // v3.7.x 流卡死检测: 后端在推理 (fps > 0 且 is_running) 但前端
      // <img> 没收到过任何帧 (isStreaming=false), 持续 N ms -> 真黑屏。
      // multipart 后续帧不触发 onload, 没法用心跳 watchdog 检测中途卡死,
      // 改用后端心跳信号反推。一旦判定卡死, 强制重建 <img> DOM 破 socket。
      const _nowTs = Date.now();
      if (data.is_running && (data.fps || 0) > 0 && !isStreaming.value) {
        if (streamBackendMismatchSince === 0) {
          streamBackendMismatchSince = _nowTs;
        } else if (_nowTs - streamBackendMismatchSince > STREAM_BACKEND_FPS_MISMATCH_THRESHOLD_MS) {
          console.warn(`[MJPEG] 后端推理中但前端 <img> 未收到帧持续 ${(_nowTs - streamBackendMismatchSince) / 1000}s, 强制重连`);
          streamBackendMismatchSince = 0;
          connectStream();
        }
      } else if (isStreaming.value) {
        streamBackendMismatchSince = 0;
      }
      // Step 8: 多模型快照 (单工位场景)
      modelStats.value = Array.isArray(data.models) ? data.models : [];
      // v3.8+: 逐件模式状态 (非 per_item 项目时后端返回 null, 这里原样转交 PerItemPanel)
      perItemState.value = data.per_item_state || null;
      // v3.19.x: 自定义混合模式物品校验状态 (未启用混合时后端返回 null)
      customMixState.value = data.custom_mix_state || null;
      cycleTime.value = data.average_cycle_time || 0;
      cycleTimeWithNg.value = data.average_cycle_time_with_ng || 0;
      lastCycleTime.value = data.last_cycle_time || 0;
      lastCycleTimeWithNg.value = data.last_cycle_time_with_ng || 0;
      currentCycleTime.value = data.current_cycle_time || 0;
      if (data.last_step_durations) {
        lastStepDurations.value = data.last_step_durations;
      }
      
      const now = Date.now();
      
      // 节流截图更新（合并而非替换，保留旧截图）
      if (data.step_screenshots && (now - lastScreenshotUpdate >= SCREENSHOT_UPDATE_INTERVAL)) {
        Object.assign(stepScreenshots.value, data.step_screenshots);
        lastScreenshotUpdate = now;
      }
      
      if (data.step_detection_times) {
        Object.assign(stepDetectionTimes.value, data.step_detection_times);
      }
      // v3.7.x: stepDurations 是"当前周期"的口径数据源（ptMode=current + ptAggregate=last 时使用），
      // 必须用整体替换才能在周期结束的瞬间归零；与 cycle_sum_step_durations 行为对齐。
      // 注意区别：avg_step_durations / last_step_durations 是历史档，仍然 merge（label 累积式可读）。
      // v3.9.x A 方案: 展示期内跳过整体替换, 让客户看到上一周期最后一步 OK + PT.
      if (data.step_durations !== undefined && !resultHoldActive) {
        stepDurations.value = data.step_durations || {};
      }
      if (data.avg_step_durations) {
        Object.assign(avgStepDurations.value, data.avg_step_durations);
      }
      // v3.8.x: 周期切换边界检测 — 后端 current_cycle_id 变化触发"步骤表清零"。
      // ★ 关键时机: 只在"新周期已经起来"那一瞬清, 不在"周期刚结算"清。
      //   - number → null (周期刚结算): 保留显示, 让客户看到 ABCD 全 OK 的视觉反馈期
      //   - null → number (新周期开始): 立刻清, 准备进入新一轮
      //   - number → 另一个 number (罕见, 直接换号): 清
      // 之前一改动就在 "number → null" 也清, 导致 cycle 一结算 PT/状态全消失,
      // 客户感觉"只有最后一步算数, 前 3 步刚显示就被清掉"。
      //
      // v3.9.x A 方案 — 在原边界判定上叠加"结果展示期":
      //   number → null (刚结算): 显示设置开了 hold 就启动 timer, hold 期内屏蔽后续清屏 + 数据替换
      //   null → number (新周期起): 如果 hold 期内, 不清屏不更新 lastSingleCycleId,
      //                              等 hold timer 到期下一轮自然走清屏
      // 配置来源: store.display.monitor.resultHoldEnabled / resultHoldSeconds (纯前端 localStorage),
      // 切口径无需重启检测.
      const _incomingCycleId = data.current_cycle_id ?? null;
      if (_incomingCycleId !== lastSingleCycleId) {
        const _justSettled = lastSingleCycleId !== null && _incomingCycleId === null;
        const _newCycleStarted = _incomingCycleId !== null && lastSingleCycleId !== _incomingCycleId;
        if (_justSettled) {
          const _holdEnabled = !!systemStore.display?.monitor?.resultHoldEnabled;
          const _holdSec = Number(systemStore.display?.monitor?.resultHoldSeconds) || 0;
          if (_holdEnabled && _holdSec > 0) {
            armResultHold(_holdSec);
          }
          lastSingleCycleId = _incomingCycleId;
        } else if (_newCycleStarted) {
          if (resultHoldActive) {
            // 展示期内: 不清屏, 不同步 lastSingleCycleId, 等 timer 到期下一轮自然进入此分支清屏
          } else {
            cycleSumStepDurations.value = {};
            stepCycleSegments.value = {};
            stepInflightDurations.value = {};
            stepDurations.value = {};
            stepVisibleSeconds.value = {};
            steps.value.forEach(s => {
              s.status = 'pending';
              s.cycleResult = null;
            });
            tableData.value.forEach(t => {
              t.status = 'pending';
              t.cycleResult = null;
            });
            // SOP 卡片栏滚动游标归零, 让新一轮从第一张卡片开始展示
            sopPanelRef.value?.resetScroll();
            lastSingleCycleId = _incomingCycleId;
          }
        } else {
          lastSingleCycleId = _incomingCycleId;
        }
      }
      // v3.5.x: PT 合并档 (cycle SUM) — 后端无对应 label 时,
      // cycle_sum_step_durations 用整体替换以反映"周期切换后已重置"的真实状态;
      // last/avg 用 merge 保持 label 累积式可读, 与 last_step_durations 风格一致.
      // v3.9.x A 方案: 展示期内跳过, 让客户看到上一周期 PT 合并档.
      if (data.cycle_sum_step_durations !== undefined && !resultHoldActive) {
        cycleSumStepDurations.value = data.cycle_sum_step_durations || {};
      }
      // v3.10.x: 分段历史 (供 sum/max/first 三档现算)
      if (data.step_cycle_segments !== undefined && !resultHoldActive) {
        stepCycleSegments.value = data.step_cycle_segments || {};
      }
      // v3.8.x: in-flight 实时 PT (步骤还在画面里, 持续涨), 与权威 cycle_sum 分开,
      // 整体替换才能在步骤完成 / 周期切换时立刻消失 (不留旧标签残影)。
      // v3.9.x A 方案: 展示期内冻结 inflight, 防止新周期 inflight 立刻覆盖.
      if (!resultHoldActive) {
        stepInflightDurations.value = data.step_inflight_durations || {};
      }
      // v3.9.x D 方案: 累计可见时长 (visible PT 数据源), 同样在展示期内冻结.
      if (!resultHoldActive) {
        stepVisibleSeconds.value = data.step_visible_seconds || {};
      }
      if (data.last_cycle_sum_step_durations) {
        Object.assign(lastCycleSumStepDurations.value, data.last_cycle_sum_step_durations);
      }
      if (data.avg_cycle_sum_step_durations) {
        Object.assign(avgCycleSumStepDurations.value, data.avg_cycle_sum_step_durations);
      }
      if (data.step_intervals) {
        Object.assign(stepIntervals.value, data.step_intervals);
      }
      
      if (isRunning.value) {
        const shouldUpdateCharts = (now - lastChartUpdate >= CHART_UPDATE_INTERVAL);
        const countersWithCycle = data.counters || {};
        countersWithCycle._currentCycleSteps = data.current_cycle_steps || [];
        countersWithCycle._backupCoveredLabels = data.backup_covered_labels || [];
        countersWithCycle._ngStepCycleCounts = data.ng_step_cycle_counts || {};
        updateStepsFromBackend(
          data.step_counts || {}, 
          data.detections || [],
          countersWithCycle,
          data.recent_events || [],
          shouldUpdateCharts
        );
        if (shouldUpdateCharts) {
          lastChartUpdate = now;
        }
      }
      
      if (data.detections) {
        drawDetections(data.detections);
      }
      
      if (data.model_task) {
        serverModelTask.value = data.model_task;
      }
      if (data.tracking) {
        trackingChecklist.value = data.tracking.item_checklist || {};
        trackingCycleActive.value = data.tracking.cycle_active || false;
        trackingContainerMode.value = data.tracking.container_mode || false;
        if (data.tracking.container_mode) {
          trackingBoxes.value = data.tracking.boxes || {};
          trackingSettledCount.value = data.tracking.settled_boxes || 0;
          trackingSettledOk.value = data.tracking.settled_ok || 0;
          trackingSettledNg.value = data.tracking.settled_ng || 0;
        } else {
          trackingBoxes.value = {};
          trackingSettledCount.value = 0;
          trackingSettledOk.value = 0;
          trackingSettledNg.value = 0;
        }
      }

      // v3.5.0: 周期性强制动作进度
      periodicActions.value = Array.isArray(data.periodic_actions) ? data.periodic_actions : [];
      
      // MES 实时数据存入 multiChannelData（单通道模式）
      if (data.mes) {
        if (!multiChannelData.value[0]) multiChannelData.value[0] = {};
        multiChannelData.value[0].mes = data.mes;
        if (data.mes.scan_event) handleScanToast(data.mes.scan_event);
        if (data.mes.rebind_prompt) handleRebindPrompt(data.mes.rebind_prompt);
      } else {
        if (multiChannelData.value[0]) multiChannelData.value[0].mes = null;
      }

      // v3.9.x 事件人工确认阻塞态 (单通道模式 — 与 processChannelResult 多通道路径对齐)
      // 不写到 multiChannelData[0].pendingAck 的话, 全屏覆盖层 computed 永远拿不到, 弹不出
      if (!multiChannelData.value[0]) multiChannelData.value[0] = {};
      multiChannelData.value[0].pendingAck = data.pending_ack || { active: false };
      multiChannelData.value[0].pendingRemediation = data.pending_remediation || null;
      multiChannelData.value[0].recentEvents = data.recent_events || [];

      // ── monitor.poll 诊断: 人工确认阻塞边沿 + 每 3s 轮询摘要 ──
      const _ackActive = !!(data.pending_ack && data.pending_ack.active);
      if (_ackActive !== _pollLastAck) {
        dbg('monitor.poll', _ackActive ? '进入人工确认阻塞(画面定格)' : '解除人工确认阻塞',
            `channel=${selectedChannel.value || 0} (阻塞期间帧序号不推进=正常定格, 非闪烁)`);
        _pollLastAck = _ackActive;
      }
      if (now - _pollLastSummary >= 3000) {
        _pollLastSummary = now;
        dbg('monitor.poll', '轮询摘要',
            `检出=${(data.detections || []).length} 采集=${data.fps_actual ?? '?'}fps `
            + `推理=${data.fps_inference ?? '?'}fps cycle步骤=${(data.current_cycle_steps || []).length} `
            + `运行=${isRunning.value} 阻塞=${_ackActive}`);
      }
      
      if (isVideoSource.value && !isDraggingProgress.value) {
        try {
          const videoRes = await api.get('/source/video/info');
          if (videoRes.data.status === 'success') {
            videoInfo.value.progress = videoRes.data.progress || 0;
            videoInfo.value.currentTime = videoRes.data.current_time || 0;
            videoInfo.value.duration = videoRes.data.duration || 0;
            if (!isChangingSpeed.value) {
              videoInfo.value.speed = videoRes.data.speed || 1;
            }
            videoInfo.value.syncMode = videoRes.data.sync_mode || false;
            videoInfo.value.ended = videoRes.data.ended || false;
            
            if (videoRes.data.ended) {
              isRunning.value = false;
              isStreaming.value = false;
              projectStore.setRunningStatus(false);
            }
          }
        } catch (e) {
          // 静默处理
        }
      }
      // 流断线自动恢复：后端在跑但前端没有流 URL
      if (isRunning.value && !streamSrc0.value && !streamSrc1.value) {
        connectStream();
      }
    } catch (err) {
      // 静默处理轮询错误 (调试开关开启时可见)
      dbgErr('monitor.poll', '检测结果轮询', err);
    } finally {
      pollingInProgress = false;
    }
  }, 150); // ~6.7Hz polling for responsive detection box overlay
};

// 已显示的事件ID（避免重复显示提示框）
const shownEventIds = ref(new Set());

// 扫码提示去重 — 按通道独立记录时间戳
const lastScanToastTs = {};
const handleScanToast = (scanEvent, ch = 0) => {
  // v3.3.0 码-码闭环: 同码二次扫的软警告 (后端在 _last_scan_event 里写 scan_pair_dup_warning=true)
  if (scanEvent.scan_pair_dup_warning) {
    if (scanEvent.timestamp <= (lastScanToastTs[ch] || 0)) return;
    lastScanToastTs[ch] = scanEvent.timestamp;
    const msg = `重复扫码: ${scanEvent.serial_no} - 等待新码`;
    if (channelCount.value > 1) {
      ElMessage.warning(`工位 ${ch + 1} ${msg}`);
    } else {
      ElMessage.warning(msg);
    }
    return;
  }
  const scanConfig = systemStore.detection.toasts?.scan;
  if (!scanConfig?.enabled) return;
  if (scanEvent.timestamp <= (lastScanToastTs[ch] || 0)) return;
  lastScanToastTs[ch] = scanEvent.timestamp;
  if (channelCount.value > 1) {
    showMultiToast(ch, 'scan', scanConfig.text || '扫码成功', scanEvent.serial_no);
  } else {
    showToastById('scan', scanConfig.text || '扫码成功', scanEvent.serial_no);
  }
};

// rebind 弹窗去重 — 传入事件所在通道而非 selectedChannel
let rebindPromptShowing = false;
const handleRebindPrompt = async (rebindData, ch = 0) => {
  if (rebindPromptShowing) return;
  rebindPromptShowing = true;
  const chLabel = channelCount.value > 1 ? `工位 ${ch + 1} - ` : '';
  try {
    await ElMessageBox.confirm(
      `${chLabel}工件 #${rebindData.workpiece_id} 检测 NG，是否继续检测当前工件？`,
      '误检重绑',
      {
        confirmButtonText: '继续当前工件',
        cancelButtonText: '扫新工件',
        type: 'warning',
      }
    );
    await api.post('/source/detection/rebind', null, { params: { action: 'continue', channel: ch } });
  } catch {
    await api.post('/source/detection/rebind', null, { params: { action: 'new', channel: ch } });
  } finally {
    rebindPromptShowing = false;
  }
};

// v2.7.16 清除本次扫码状态（让操作员重扫一次条码）
// - 未绑入 cycle 时直接清 _pending/_queue/_last_scan/_rebind 4 个 dict
// - 已绑入 cycle (status='inspecting') 时弹确认框，确认后 force=true 作废本次检测：
//   * 工件 status 回退到 queued（可被重扫）
//   * 删该 (workpiece, cycle) 的 WorkpieceInspection 记录
//   * cycle 仍会自然走完，cycle_end 时报"未绑码"，不计入 MES/工单/集群
const clearPendingScan = async (ch) => {
  const wp = displayWorkpiece.value;
  let force = false;

  if (wp && wp.status === 'inspecting') {
    try {
      await ElMessageBox.confirm(
        `本次工件 ${wp.serial_no} 已经在检测中（第 ${wp.inspection_count} 次）。\n` +
        `确认作废吗？作废后本次 cycle 不会计入 MES 和工单完成数，工件可重新扫码再次检测。`,
        '作废本次检测',
        {
          confirmButtonText: '确认作废',
          cancelButtonText: '取消',
          type: 'warning',
          dangerouslyUseHTMLString: false,
        }
      );
      force = true;
    } catch {
      return;
    }
  }

  try {
    const res = await api.post(
      '/source/detection/clear_pending_scan', null,
      { params: { channel: ch, force } }
    );
    const status = res.data?.status;
    const msg = res.data?.message;
    const cleared = res.data?.cleared || {};

    if (cleared.force_race_lost) {
      // 后端原子 pop 拿到 None：worker 在我们之前已经把工件 pop 走结算了。
      // 不能清前端工件显示，下一轮 polling 会自然把 status 刷新成 ok/ng。
      ElMessage.warning(msg || '操作来不及：本次工件已经结算完成');
      return;
    }
    if (status === 'warn') {
      ElMessage.warning(msg || '本次工件已开始检测，待结算后会自动归零');
    } else if (cleared.force_canceled_inspecting) {
      ElMessage.success(msg || '已作废本次工件检测，可重新扫码');
    } else {
      const pwp = cleared.pending_workpiece_id;
      ElMessage.success(pwp ? `已清除待检工件 #${pwp}，可重新扫码` : '已清除扫码状态，可重新扫码');
    }

    const idx = (channelCount.value > 1) ? ch : 0;
    if (multiChannelData.value[idx]?.mes) {
      multiChannelData.value[idx].mes.workpiece = null;
      multiChannelData.value[idx].mes.scan_event = null;
    }
    // v3.1.3: 多工位下清掉指定 ch 的 override + timer; 单工位走 selectedChannel
    workpieceOverridesByCh.value = { ...workpieceOverridesByCh.value, [idx]: null };
    if (workpieceOverrideTimers[idx]) { clearTimeout(workpieceOverrideTimers[idx]); workpieceOverrideTimers[idx] = null; }
    if (workpieceHideTimers[idx]) { clearTimeout(workpieceHideTimers[idx]); workpieceHideTimers[idx] = null; }
  } catch (e) {
    ElMessage.error('清除失败: ' + (e.response?.data?.detail || e.message || '未知错误'));
  }
};

// 缓存上一次截图 base64，避免重复创建 data URL
const cachedScreenshotUrls = {};

// 上一次的总产量，用于检测周期变化
let lastTotalCount = -1;
// 上一次的不良总数，用于推断本轮 OK/NG
let lastNgCount = -1;
// 已处理的事件ID（防止NG排名重复计数）
const processedEventIds = new Set();

// 从后端数据更新步骤状态
const updateStepsFromBackend = (stepCounts, currentDetections, backendCounters, recentEvents, shouldUpdateCharts = true) => {
  const detectingLabels = new Set(currentDetections.map(d => d.label));
  
  // 获取后端的当前周期步骤列表
  const currentCycleSteps = backendCounters?._currentCycleSteps || [];
  const backupCoveredLabels = new Set(backendCounters?._backupCoveredLabels || []);
  
  // Inject default_pt for backup-covered steps that have no real duration
  if (backupCoveredLabels.size > 0) {
    const stepsConfig = currentProject.value?.steps_config || [];
    backupCoveredLabels.forEach(label => {
      if (stepDurations.value[label] === undefined || stepDurations.value[label] === null) {
        const cfg = stepsConfig.find(s => s.label === label);
        if (cfg?.default_pt) {
          stepDurations.value[label] = cfg.default_pt;
          if (avgStepDurations.value[label] === undefined) {
            avgStepDurations.value[label] = cfg.default_pt;
          }
          // v3.5.x: PT 合并档也用 default_pt 兜底（备用步骤实际未出现，无 SUM）
          if (cycleSumStepDurations.value[label] === undefined) {
            cycleSumStepDurations.value[label] = cfg.default_pt;
          }
          if (avgCycleSumStepDurations.value[label] === undefined) {
            avgCycleSumStepDurations.value[label] = cfg.default_pt;
          }
        }
      }
    });
  }
  
  // 检测周期是否刚结束（总产量变化时表示新周期产生）
  const currentTotal = backendCounters?.['总产量'] ?? -1;
  const currentNg = backendCounters?.['不良总数'] ?? 0;
  if (lastTotalCount >= 0 && currentTotal > lastTotalCount) {
    // v3.9.x: 旧实现这里挂一个 1.2s setTimeout 强清步骤表 status='pending', 但
    // 步骤的 PT 字典 (cycleSumStepDurations / stepDurations / stepVisibleSeconds)
    // 由 polling 整体替换驱动, 而后端 step_cycle_durations 在 end_cycle 故意保留
    // 给"反馈期"看, 直到下一周期 start_cycle 才清. 两套路径在 1.2s 内打架 →
    // 客户实测画面: "翻转 待检测 + PT 10.6s" 这种 status / PT 不一致的快照 (闪).
    //
    // 修复: 不再用计时器驱动状态清屏. 真正的清屏统一由"cycle_id 变化"路径
    // (上方 _newCycleStarted 分支) 触发: 上一轮 OK + PT 持续显示 → 下一轮第一步
    // 被识别 → cycle_id 翻新 → 同帧内一次性清字典 + 替换为新数据 + 重新设状态.
    // 这就是 4306 行原本想要的"让用户看到上一轮颜色反馈", 1.2s 之前是被错误地
    // 当作"上限". 现在改为"至少持续到下一轮真正开始".

    // 工件卡片：把"检测中"替换成本轮最终结果(OK/NG)保留一会儿，之后清空回到"等待扫码..."
    const cycleIsNg = lastNgCount >= 0 && currentNg > lastNgCount;
    const realWp = mesData.value?.workpiece;
    if (realWp) {
      if (workpieceOverrideTimer) { clearTimeout(workpieceOverrideTimer); workpieceOverrideTimer = null; }
      if (workpieceHideTimer) { clearTimeout(workpieceHideTimer); workpieceHideTimer = null; }
      workpieceOverride.value = {
        ...realWp,
        status: cycleIsNg ? 'ng' : 'ok',
      };
      workpieceHideTimer = setTimeout(() => {
        workpieceOverride.value = null; // 清空 → 模板显示"等待扫码..."
        workpieceHideTimer = null;
      }, WORKPIECE_RESULT_HOLD_MS);
    }
  }
  lastTotalCount = currentTotal;
  lastNgCount = currentNg;
  
  // ── 实时周期反馈算法 ──
  // 在周期进行中，实时对比 currentCycleSteps 和预期顺序，动态标记每个步骤颜色
  const expectedLabels = steps.value.map(s => s.label || s.name);
  
  if (currentCycleSteps.length > 0 && expectedLabels.length > 0) {
    // 统计每个步骤在本周期中出现的次数
    const countInCycle = {};
    currentCycleSteps.forEach(l => { countInCycle[l] = (countInCycle[l] || 0) + 1; });
    
    // v3.7.0 客户反馈修复: 期望序列里同一 label 多次出现 (如 撕膜-顶卡托-撕膜-顶卡托-点亮屏幕)
    // 时, 必须按"位置"分配 currentCycleSteps 里的出现次数, 而不是按"label 是否出现过"全标完成。
    //
    // 算法:
    //   1) 期望里每个 label 期望出现几次 → expectedCounter
    //   2) 按 idx 顺序遍历期望, 每次出现一个 label 就消耗 cycle 里该 label 一次, 直到 cap 在
    //      Math.min(seen, expectedCounter[label]) 之内 → completedByPos[idx]=true
    //   3) 把 currentCycleSteps 的实际帧序 (cycle 里第 k 个该 label) 也按 idx 顺序映射到期望位置上
    //      → assignedActualPos[idx], 用来判 乱序
    //   4) "漏做" 判定 (maxCompletedIdx): 只看已完成位置, 而不是"label 在不在 firstPos"。
    //      这一步是修复 idx=2(同 label)被误标 NG-红 的关键。
    const expectedCounter = {};
    expectedLabels.forEach(l => { expectedCounter[l] = (expectedCounter[l] || 0) + 1; });

    // cycle 里每个 label 的实际出现位置序列
    const actualPosByLabel = {};
    currentCycleSteps.forEach((l, i) => {
      (actualPosByLabel[l] = actualPosByLabel[l] || []).push(i);
    });

    const consumedExpectedSlots = {};
    expectedLabels.forEach(l => { consumedExpectedSlots[l] = 0; });
    const completedByPos = new Array(expectedLabels.length).fill(false);
    const assignedActualPos = new Array(expectedLabels.length).fill(-1);
    let maxCompletedIdx = -1;
    expectedLabels.forEach((lbl, idx) => {
      const seen = (actualPosByLabel[lbl] || []).length;
      const expCnt = expectedCounter[lbl] || 0;
      const slotsToAllocate = Math.min(seen, expCnt);
      if (consumedExpectedSlots[lbl] < slotsToAllocate) {
        const k = consumedExpectedSlots[lbl];
        completedByPos[idx] = true;
        assignedActualPos[idx] = actualPosByLabel[lbl][k];
        consumedExpectedSlots[lbl] += 1;
        maxCompletedIdx = idx;
      }
    });

    // 乱序判定: 按预期位置遍历已分配的实际位置,
    // 若当前位置的实际帧序 < 前面位置的实际帧序最大值 → 乱序 (该 idx)
    const outOfOrderIdx = new Set();
    let maxActualSoFar = -1;
    for (let idx = 0; idx < expectedLabels.length; idx++) {
      if (!completedByPos[idx]) continue;
      const p = assignedActualPos[idx];
      if (p < maxActualSoFar) {
        outOfOrderIdx.add(idx);
      }
      maxActualSoFar = Math.max(maxActualSoFar, p);
    }

    // v3.8.x: 漏做判定也要权威化 ── 只在"后面位置权威完成"时才认定前面位置漏做,
    // 否则后面步骤还在画面 PT 涨着的时候, 前面位置会被立刻标 NG,
    // 等用户其实是补做了前面 → 又改成 OK, 形成 "null → NG → OK" 的视觉抖动。
    // 客户反馈 "OK 出之前会先闪 NG"。
    let maxAuthCompletedIdx = -1;
    expectedLabels.forEach((lbl, idx) => {
      if (completedByPos[idx]) {
        const pt = cycleSumStepDurations.value[lbl];
        if (pt !== undefined && pt !== null && pt > 0) {
          maxAuthCompletedIdx = idx;
        }
      }
    });

    // 设置每个步骤的 cycleResult
    steps.value.forEach((step, idx) => {
      const label = step.label || step.name;
      const cycleCount = (actualPosByLabel[label] || []).length;
      const expCnt = expectedCounter[label] || 0;

      const isCoveredByBackup = backupCoveredLabels.has(label);
      const thisPosCompleted = completedByPos[idx];

      const _posCyclePT = cycleSumStepDurations.value[label];
      const _posAuthoritative = _posCyclePT !== undefined && _posCyclePT !== null && _posCyclePT > 0;
      const _inflightPT = stepInflightDurations.value[label];
      const _hasTimedPT = _posAuthoritative
        || (_inflightPT !== undefined && _inflightPT !== null && _inflightPT > 0);

      // 后续期望位置已有步骤进 cycle → 本位置视为"已走过", 不因画面里再次识别回退到 active/NG 闪动
      const _passedThisStep = maxCompletedIdx > idx && thisPosCompleted;
      const _inFrame = detectingLabels.has(label);
      const _leftFrame = !_inFrame;

      const _isLastStep = expectedLabels.length > 0 && idx === expectedLabels.length - 1;
      // 最后一步: 离开画面即可 OK+PT 同帧; 中间步: 要有可显示 PT, 且已离开画面或后续步骤已往前走
      const _posDone = thisPosCompleted && (
        _isLastStep
          ? _leftFrame
          : (_hasTimedPT && (_leftFrame || _passedThisStep))
      );

      if (cycleCount > expCnt && _posDone) {
        step.cycleResult = 'ng';
      } else if (_posDone) {
        step.cycleResult = outOfOrderIdx.has(idx) ? 'ng' : 'ok';
      } else if (isCoveredByBackup) {
        step.cycleResult = 'ok';  // 替补覆盖
      } else if (!thisPosCompleted && idx < maxAuthCompletedIdx) {
        // 漏做: 此位置从未进 cycle, 但后面已有权威 PT — 仅真漏做才标 NG, 已进 cycle 等 PT 的不闪 NG
        step.cycleResult = 'ng';
      } else {
        step.cycleResult = null;
      }

      const _completed = thisPosCompleted || isCoveredByBackup;
      // v3.9.x: 步骤一旦 join 周期 (thisPosCompleted=true) 或被替补覆盖, 直接锁定 'completed',
      // 不因画面里标签时有时无回退到 'active'. UI (行 1037) 只把 'completed' 渲染成"已检测",
      // 其余全渲染"待检测", 老逻辑里 _inFrame 优先级最高 → 客户做这一步期间 YOLO 帧抖
      // 让 status 在 active/completed 反复切, 体感: "已检测 ↔ 待检测 反复闪, 闪完 PT 涨一点".
      // _passedThisStep 老豁免只在"后续步骤已往前走"时生效, 客户每步做完整动作时后面步骤
      // 还没识别 → 不豁免 → 闪.
      // 新口径: 已 join 一票否决, 画面再次识别不再倒退. 'active' 只剩"步骤识别到但还没 join"
      // 那种瞬时态 (实务上极短或不出现, 比如 join_cycle=False 的静态步骤)。
      if (_completed) {
        step.status = 'completed';
      } else if (_inFrame) {
        step.status = 'active';
      } else {
        step.status = 'pending';
      }

      // 同步表格状态 (跟 step.status 走同一份逻辑, 不要用 _completed 单值, 因为 active 也算"已走到")
      if (tableData.value[idx]) {
        tableData.value[idx].count = stepCounts[label] || 0;
        tableData.value[idx].status = step.status;
        tableData.value[idx].cycleResult = step.cycleResult;
      }
    });

    // v3.8.x: 结算步已 OK 时, 前面已进 cycle 的中间步同步 OK —
    // 后端 PT 写入比最后一步慢半拍时, 避免 "最后一步 OK+PT, 中间步只有 PT 或全 --".
    const _lastIdx = expectedLabels.length - 1;
    if (_lastIdx >= 0 && steps.value[_lastIdx]?.cycleResult === 'ok') {
      steps.value.forEach((step, idx) => {
        if (idx >= _lastIdx) return;
        if (completedByPos[idx] && step.cycleResult == null && !outOfOrderIdx.has(idx)) {
          step.cycleResult = 'ok';
          if (tableData.value[idx]) {
            tableData.value[idx].cycleResult = 'ok';
          }
        }
      });
    }
    
    // 自动滚动到最新变化的卡片
    let latestChangedIdx = -1;
    for (let i = steps.value.length - 1; i >= 0; i--) {
      if (steps.value[i].cycleResult === 'ok' || steps.value[i].cycleResult === 'ng') {
        latestChangedIdx = i;
        break;
      }
    }
    if (latestChangedIdx >= 0) {
      sopPanelRef.value?.scrollToCard(latestChangedIdx);
    }
  } else {
    // 周期间隙 (currentCycleSteps 为空) — 不主动修改 status/cycleResult,
    // 保留上一轮的视觉反馈直到:
    //   1) 下一周期开始: 上方 if 分支会按新一轮重算
    //   2) cycle_id 变化: polling 入口边界检测会把 status/cycleResult 全清成 pending/null
    // v3.8.x (二次修订): 删掉旧 fallback "!step.cycleResult && count > 0 ... → 'completed'",
    // 该 fallback 会让边界清零后的 'pending' 状态被翻回 'completed', 跟周期切换体感冲突。
    steps.value.forEach((step, idx) => {
      const label = step.label || step.name;
      const count = stepCounts[label] || 0;

      if (detectingLabels.has(label)) {
        // 新一轮第一个步骤刚进画面 → 立刻显示 active (此时 cycle_id 可能还是 null,
        // 但 detectingLabels 已经有内容了)
        step.status = 'active';
        if (tableData.value[idx]) {
          tableData.value[idx].status = 'active';
        }
      }

      if (tableData.value[idx]) {
        tableData.value[idx].count = count;
      }
    });
  }

  // v3.1.3: 跟踪模式补丁 — 跟踪模式不发 _currentCycleSteps,
  // 用 trackingChecklist (普通模式) / trackingChecklist._boxes (容器模式) 中
  // counted > 0 来翻 step "已检测/OK"
  if (isTrackingMode.value) {
    const checklist = trackingChecklist.value || {};
    const boxes = trackingBoxes.value || {};
    const isContainer = !!trackingContainerMode.value;
    const trackHit = (label) => {
      if (isContainer) {
        const boxesMap = checklist._boxes || {};
        for (const bid of Object.keys(boxes)) {
          const items = boxesMap[bid]?.items;
          if (items && items[label] && items[label].counted > 0) return true;
        }
        return false;
      }
      return !!(checklist[label] && checklist[label].counted > 0);
    };
    steps.value.forEach((step, idx) => {
      const label = step.label || step.name;
      if (trackHit(label)) {
        step.status = 'completed';
        step.cycleResult = 'ok';
        if (tableData.value[idx]) {
          tableData.value[idx].status = 'completed';
          tableData.value[idx].cycleResult = 'ok';
        }
      }
    });
  }
  
  // 更新截图
  // v3.7.x: cache key 用 `${idx}_${label}` 而非纯 label, 否则 sequence_order 含
  // 重复 label 时 (如 "检查外观" × 2), 第 1 个卡片更新 cache 后第 2 个卡片
  // 因 cache 已命中而被跳过, 永远 screenshot=null.
  steps.value.forEach((step, idx) => {
    const stepLabel = step.label || step.name;
    const rawB64 = stepScreenshots.value[stepLabel];
    const cacheKey = `${idx}_${stepLabel}`;
    if (rawB64 && cachedScreenshotUrls[cacheKey] !== rawB64) {
      cachedScreenshotUrls[cacheKey] = rawB64;
      step.screenshot = `data:image/jpeg;base64,${rawB64}`;
    }
  });
  
  // NG TOP3: use authoritative counts from backend (survives page reload)
  {
    const totalCycles = backendCounters?.['总产量'] || backendCounters?.['total'] || 0;
    const backendNgMap = backendCounters?._ngStepCycleCounts || {};
    ngStepRanking.value = Object.entries(backendNgMap)
      .filter(([, ngCount]) => ngCount > 0)
      .map(([step, ngCount]) => {
        const rate = totalCycles > 0 ? (ngCount / totalCycles * 100) : 0;
        return { step, count: ngCount, total: totalCycles, rate };
      })
      .sort((a, b) => b.rate - a.rate);
  }
  
  if (backendCounters && currentProject.value?.counters_config) {
    currentProject.value.counters_config.forEach(counter => {
      if (backendCounters[counter.name] !== undefined) {
        counter.value = backendCounters[counter.name];
      }
    });
  }
  
  if (recentEvents && recentEvents.length > 0) {
    recentEvents.forEach(event => {
      const eventKey = `${event.event_id}_${Math.floor(event.timestamp)}`;
      if (!shownEventIds.value.has(eventKey) && event.show_notification) {
        shownEventIds.value.add(eventKey);
        const toastId = event.toast_id || (event.event_id === 1 ? 'ok' : event.event_id === 2 ? 'ng' : 'ok');
        showToastById(toastId, event.event_name, event.reason);
        
        const warnCfg = systemStore.detection.toasts?.warn_no_barcode;
        // v3.5.2: 直接读后端权威判定 should_warn_no_barcode (统一判定来源).
        const _shouldWarn = event.should_warn_no_barcode !== undefined
          ? !!event.should_warn_no_barcode
          : !event.had_workpiece;
        if (warnCfg?.enabled && _shouldWarn) {
          setTimeout(() => {
            showToastById('warn_no_barcode', warnCfg.text || '⚠ 未绑码', warnCfg.subText || '本次结算未绑定工件条码');
          }, 300);
        }
      }
    });
    
    // 防止 shownEventIds 无限增长
    if (shownEventIds.value.size > 500) {
      const arr = [...shownEventIds.value];
      shownEventIds.value = new Set(arr.slice(-200));
    }
  }
  
  if (shouldUpdateCharts) {
    updateCharts();
  }
};

// 停止轮询
const stopPolling = () => {
  if (pollingTimer) {
    clearInterval(pollingTimer);
    pollingTimer = null;
  }
};

const standbyHandler = async () => {
  dbg('monitor.control', '点击「待机」', `running=${isRunning.value} detecting=${isDetecting.value}`);
  if (isOperating.value) { dbg('monitor.control', '待机被拒: 操作进行中'); return; }
  if (!(await confirmScanPairBeforeStop(selectedChannel.value || 0))) { dbg('monitor.control', '待机被取消: scan_pair 确认未通过'); return; }
  isOperating.value = true;
  try {
    await standbyDetection();
    isDetecting.value = false;
    // isRunning stays true — video stream keeps playing
    steps.value.forEach(s => {
      s.status = 'pending';
      s.result = null;
    });
    if (detectionCanvas.value) {
      const ctx = detectionCanvas.value.getContext('2d');
      ctx.clearRect(0, 0, detectionCanvas.value.width, detectionCanvas.value.height);
    }
    if (!pollingTimer) {
      startPolling();
    }
    dbg('monitor.control', '待机成功: 检测停止, 画面继续');
    ElMessage.info('已待机：检测停止，画面继续');
  } catch (err) {
    console.error('待机失败:', err);
    dbgErr('monitor.control', '待机', err);
    ElMessage.error('待机失败: ' + (err.response?.data?.detail || err.message));
  } finally {
    isOperating.value = false;
  }
};

const resetCounters = async () => {
  dbg('monitor.control', '点击「清零」', `project=${currentProject.value?.name || '无'}`);
  if (!currentProject.value?.counters_config) { dbg('monitor.control', '清零跳过: 无计数器配置'); return; }
  
  // 先重置后端统计数据（关键！必须先重置后端，否则轮询会覆盖前端数据）
  try {
    await resetDetectionStats();
  } catch (e) {
    console.error('重置后端统计失败:', e);
    dbgErr('monitor.control', '清零-后端统计重置', e);
  }
  
  // 重置所有计数器
  currentProject.value.counters_config.forEach(c => {
    c.value = 0;
  });
  
  // 重置步骤状态
  steps.value.forEach(s => {
    s.status = 'pending';
    s.result = null;
    s.cycleResult = null;
    s.screenshot = null;
  });
  
  tableData.value.forEach(t => {
    t.count = 0;
    t.status = 'pending';
    t.cycleResult = null;
  });
  sopPanelRef.value?.resetScroll();
  
  // 清空步骤截图缓存、PT/间隔缓存和NG排名
  stepScreenshots.value = {};
  stepScreenshotHashes.value = {};  // B6: 截图清空时同步清掉指纹，避免下次去重误省略
  stepDurations.value = {};
  avgStepDurations.value = {};
  // v3.5.x: PT 合并档同步清空
  cycleSumStepDurations.value = {};
  lastCycleSumStepDurations.value = {};
  avgCycleSumStepDurations.value = {};
  stepCycleSegments.value = {};
  stepIntervals.value = {};
  stepDetectionTimes.value = {};
  Object.keys(cachedScreenshotUrls).forEach(k => delete cachedScreenshotUrls[k]);
  ngStepRanking.value = [];
  ngStepCountMap.value = {};
  lastTotalCount = -1;
  lastNgCount = -1;
  if (workpieceOverrideTimer) { clearTimeout(workpieceOverrideTimer); workpieceOverrideTimer = null; }
  if (workpieceHideTimer) { clearTimeout(workpieceHideTimer); workpieceHideTimer = null; }
  workpieceOverride.value = undefined;
  processedEventIds.clear();
  
  trackingChecklist.value = {};
  trackingCycleActive.value = false;
  trackingContainerMode.value = false;
  trackingBoxes.value = {};
  trackingSettledCount.value = 0;
  trackingSettledOk.value = 0;
  trackingSettledNg.value = 0;
  
  updateCharts();
  ElMessage.success('计数器已清零');
};

const resetCountersForChannel = async (ch) => {
  dbg('monitor.control', `点击「清零」(工位${ch + 1})`);
  try {
    await resetDetectionStats(ch);
  } catch (e) {
    console.error(`Ch${ch} 重置后端统计失败:`, e);
    dbgErr('monitor.control', `工位${ch + 1} 清零-后端统计重置`, e);
  }
  const chData = multiChannelData.value[ch];
  if (chData) {
    multiChannelData.value[ch] = {
      ...chData,
      total: 0,
      ok: 0,
      ng: 0,
      yieldRate: 0,
      steps: (chData.steps || []).map(s => ({ ...s, status: 'pending', screenshot: null })),
      tableData: (chData.tableData || []).map(t => ({ ...t, count: 0, status: 'pending' })),
    };
  }
  ElMessage.success(`工位 ${ch + 1} 计数器已清零`);
};

// v3.7.4: 周期性强制动作 UI 工具
// 进度条同时考虑次数 + 时间维度, 取"更接近爆表的那个"
const getPeriodicProgress = (rule) => {
  const cnt = rule.interval > 0 ? Math.min(1, (rule.counter || 0) / rule.interval) : 0;
  const tm = rule.time_interval_seconds > 0
    ? Math.min(1, (rule.time_elapsed_seconds || 0) / rule.time_interval_seconds)
    : 0;
  return Math.max(cnt, tm);
};

// 把秒数渲染成 "32m15s" / "1h10m" / "45s" 这样的友好格式
const formatRemainingTime = (seconds) => {
  const s = Math.max(0, Math.floor(Number(seconds) || 0));
  if (s < 60) return `${s}s`;
  if (s < 3600) {
    const m = Math.floor(s / 60);
    const ss = s % 60;
    return ss === 0 ? `${m}m` : `${m}m${ss}s`;
  }
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return m === 0 ? `${h}h` : `${h}h${m}m`;
};

// v3.7.3: 周期性强制动作 — 单条/全部手动重置，任何时候都可点（含检测运行中）
const _periodicTargetChannel = () => (channelCount.value > 1 ? selectedChannel.value : 0);

const resetSinglePeriodicAction = async (rule) => {
  if (!rule?.id) return;
  try {
    await ElMessageBox.confirm(
      `确认将「${rule.name}」计数器归零？当前进度 ${rule.counter}/${rule.interval}。`,
      '重置周期性强制动作',
      { confirmButtonText: '重置', cancelButtonText: '取消', type: 'warning' }
    );
  } catch (_) {
    return;
  }
  try {
    await resetPeriodicAction(_periodicTargetChannel(), rule.id);
    const hit = periodicActions.value.find(r => r.id === rule.id);
    if (hit) {
      hit.counter = 0; hit.state = 'ok'; hit.remaining = hit.interval;
      // v3.7.4: 时间维度也乐观更新
      hit.count_state = hit.interval > 0 ? 'ok' : 'disabled';
      hit.time_elapsed_seconds = 0;
      hit.time_remaining_seconds = hit.time_interval_seconds || 0;
      hit.time_state = hit.time_interval_seconds > 0 ? 'ok' : 'disabled';
    }
    ElMessage.success(`「${rule.name}」已重置`);
  } catch (e) {
    console.error('重置周期性强制动作失败:', e);
    ElMessage.error('重置失败: ' + (e.response?.data?.detail || e.message));
  }
};

const resetAllPeriodicActions = async () => {
  if (!periodicActions.value.length) return;
  try {
    await ElMessageBox.confirm(
      `确认将全部 ${periodicActions.value.length} 条周期性强制动作计数器归零？`,
      '全部重置',
      { confirmButtonText: '全部重置', cancelButtonText: '取消', type: 'warning' }
    );
  } catch (_) {
    return;
  }
  try {
    await resetPeriodicAction(_periodicTargetChannel(), null);
    periodicActions.value.forEach(r => {
      r.counter = 0; r.state = 'ok'; r.remaining = r.interval;
      // v3.7.4: 时间维度也乐观更新
      r.count_state = r.interval > 0 ? 'ok' : 'disabled';
      r.time_elapsed_seconds = 0;
      r.time_remaining_seconds = r.time_interval_seconds || 0;
      r.time_state = r.time_interval_seconds > 0 ? 'ok' : 'disabled';
    });
    ElMessage.success('已全部重置');
  } catch (e) {
    console.error('全部重置周期性强制动作失败:', e);
    ElMessage.error('重置失败: ' + (e.response?.data?.detail || e.message));
  }
};


// processDetections 已移除：步骤计数完全由后端 step_counts 驱动，
// 前端不再重复累加，避免数据不一致和内存浪费

// 触发事件
const triggerEvent = (eventId) => {
  if (!currentProject.value) return;
  
  const eventsConfig = currentProject.value.events_config || [];
  const event = eventsConfig.find(e => e.id === eventId);
  
  if (!event) return;
  
  // 执行计数器动作
  if (event.actions) {
    event.actions.forEach(action => {
      const counter = currentProject.value.counters_config?.find(c => c.name === action.counter_name);
      if (counter) {
        counter.value += action.value || 1;
      }
    });
  }
  
  // 显示提示框 — 多工位时用 showMultiToast 显示到对应工位
  if (event.show_notification) {
    const toastId = event.id === 'event_1' ? 'ok' : event.id === 'event_2' ? 'ng' : 'ok';
    // 事件编号归一为数字(event_1→1 / event_2→2 / 其它→解析尾号)，供插件按身份分流提示框
    const numEventId = event.id === 'event_1' ? 1 : event.id === 'event_2' ? 2
      : (parseInt(String(event.id || '').replace(/\D/g, ''), 10) || null);
    if (channelCount.value > 1) {
      showMultiToast(selectedChannel.value, toastId, event.name, event.custom_text, numEventId);
    } else {
      showToast(toastId === 'ok' ? 'ok' : 'ng', event.name, event.custom_text);
    }
  }
  
  updateCharts();
};

// 自动恢复输入源（返回是否成功恢复）
// 优先检查后端是否已自动恢复（auto_restore_video_sources），其次用 localStorage 兜底
const autoRestoreSource = async () => {
  try {
    const status = await getSourceStatus();
    if (status.data.is_running) {
      if (status.data.source_type) sourceStore.setSourceType(status.data.source_type);
      sourceStore.setStreaming(true);
      isStreaming.value = true;
      isRunning.value = true;
      isDetecting.value = !!status.data.is_detecting;
      projectStore.setRunningStatus(true);
      forceReconnectStream();
      startPolling();
      console.log('[AutoRestore] 后端已自动恢复视频源');
      return true;
    }
  } catch { /* 后端不可达, 继续用 localStorage 兜底 */ }

  sourceStore.loadConfig();
  if (!localStorage.getItem('source_config')) return false;

  const savedType = sourceStore.sourceType;
  let restored = false;

  if (!savedType || savedType === 'camera') {
    try {
      const cameraSettings = sourceStore.cameraSettings;
      const [w, h] = (cameraSettings.resolution || '1280x720').split('x').map(Number);
      await api.post('/source/camera/start', {
        device_index: cameraSettings.deviceIndex || 0,
        width: w,
        height: h,
        fps: cameraSettings.fps || 60
      });
      sourceStore.setSourceType('camera');
      restored = true;
      console.log('[AutoRestore] localStorage 恢复摄像头');
    } catch (err) {
      console.warn('[AutoRestore] 自动恢复摄像头失败:', err);
    }
  } else if (savedType === 'hikvision') {
    try {
      const hikSettings = sourceStore.hikvisionSettings;
      const [w, h] = (hikSettings.resolution || '1280x720').split('x').map(Number);
      await api.post('/source/hikvision/start', {
        device_index: hikSettings.deviceIndex || 0,
        width: w,
        height: h,
        fps: hikSettings.fps || 60
      });
      sourceStore.setSourceType('hikvision');
      restored = true;
      console.log('[AutoRestore] localStorage 恢复海康相机');
    } catch (err) {
      console.warn('[AutoRestore] 自动恢复海康相机失败:', err);
    }
  } else if (savedType === 'video' && sourceStore.videoPath) {
    try {
      await api.post('/source/video/start', {
        file_path: sourceStore.videoPath,
        speed: sourceStore.videoSpeed || 1
      });
      sourceStore.setSourceType('video');
      restored = true;
      console.log('[AutoRestore] localStorage 恢复视频');
    } catch (err) {
      console.warn('[AutoRestore] 自动恢复视频失败:', err);
    }
  }

  if (restored) {
    sourceStore.setStreaming(true);
    isStreaming.value = true;
    isRunning.value = true;
    projectStore.setRunningStatus(true);
    forceReconnectStream();
    startPolling();
  }

  return restored;
};

// ==================== v3.9.x 事件人工确认 ====================
// nowTimestamp: 实时时钟 ref, 0.5s 一次刷新, 用于驱动倒计时 / 已等待秒数 computed
// pendingAckDisplay: 当前要在覆盖层展示哪个通道的阻塞信息 (优先选中, 其次最早阻塞)
// pendingAckWaitedSec / pendingAckRemainSec: 实时计算的秒数 (依赖 nowTimestamp + multiChannelData)
// ackPendingForChannel: 调后端 ack-event 接口, 成功后乐观清前端 pendingAck (后端下次 polling 也会清)
const nowTimestamp = ref(Math.floor(Date.now() / 1000));
let _nowTickInterval = null;

const pendingAckChannelStates = computed(() => {
  const list = [];
  for (let ch = 0; ch < channelCount.value; ch++) {
    const pa = multiChannelData.value[ch]?.pendingAck;
    if (pa && pa.active) {
      list.push({
        channel: ch,
        eventId: pa.event_id,
        eventName: pa.event_name,
        startedAt: pa.started_at || nowTimestamp.value,
        timeoutSec: Number(pa.timeout_sec || 0),
        reason: (multiChannelData.value[ch]?.recentEvents || [])
          .filter(e => e.require_ack)
          .slice(-1)[0]?.reason || '',
        acking: !!multiChannelData.value[ch]?.pendingAckSubmitting,
        // v3.23 缺步骤延迟落账挂起 (有值 → ack 窗展示缺项 + "补步骤/认NG/重做"三按钮)
        remediation: multiChannelData.value[ch]?.pendingRemediation || null,
      });
    }
  }
  return list;
});

const pendingAckDisplay = computed(() => {
  const list = pendingAckChannelStates.value;
  if (list.length === 0) return null;
  const cur = list.find(s => s.channel === selectedChannel.value);
  if (cur) return cur;
  return list.slice().sort((a, b) => a.startedAt - b.startedAt)[0];
});

const pendingAckWaitedSec = computed(() => {
  const d = pendingAckDisplay.value;
  if (!d) return 0;
  return Math.max(0, Math.floor(nowTimestamp.value - d.startedAt));
});

const pendingAckRemainSec = computed(() => {
  const d = pendingAckDisplay.value;
  if (!d || !d.timeoutSec) return 0;
  return Math.max(0, d.timeoutSec - pendingAckWaitedSec.value);
});

// v3.23 action: null/redo=重做 / supplement_step=补步骤判OK / confirm_ng=认NG落账
const ackPendingForChannel = async (ch, action = null) => {
  const chData = multiChannelData.value[ch];
  if (!chData || !chData.pendingAck?.active) return;
  if (chData.pendingAckSubmitting) return;
  chData.pendingAckSubmitting = true;
  try {
    const res = await ackPendingEvent(ch, action);
    if (res?.data?.acked) {
      const msg = action === 'supplement_step' ? '已补步骤判合格'
        : action === 'confirm_ng' ? '已确认 NG'
        : '已确认，重置当前周期';
      ElMessage.success(`工位 ${ch + 1} ${msg}`);
    } else {
      ElMessage.info(`工位 ${ch + 1} 当前没有待确认事件`);
    }
    chData.pendingAck = { active: false };
    chData.pendingRemediation = null;
  } catch (e) {
    // 403 = 当前登录账号没有"人工确认"权限 (常见: 操作员) → 弹借管理员密码提权窗 (携带本次动作)
    if (e?.response?.status === 403) {
      openElevateDialog(ch, action);
    } else {
      console.error('[ackPendingForChannel] failed', e);
      ElMessage.error('确认失败：' + (e?.message || '未知错误'));
    }
  } finally {
    chData.pendingAckSubmitting = false;
  }
};

// v3.23 借密码提权确认: 操作员无 ack 权限时, 输入管理员账密授权一次, 不改当前登录身份.
// 确认完仍是该操作员的会话 (后端 ack-event-elevated 只校验一次账密 + 权限, 不发 token).
const elevateDialog = ref({ visible: false, channel: 0, username: '', password: '', submitting: false, action: null });

const openElevateDialog = (ch, action = null) => {
  elevateDialog.value = { visible: true, channel: ch, username: '', password: '', submitting: false, action };
};

const submitElevatedAck = async () => {
  const d = elevateDialog.value;
  if (!d.username || !d.password) {
    ElMessage.warning('请输入管理员账号和密码');
    return;
  }
  d.submitting = true;
  try {
    const res = await ackPendingEventElevated(d.channel, d.username, d.password, d.action);
    if (res?.data?.acked) {
      const ch = d.channel;
      ElMessage.success(`已由 ${res.data.authorized_by || d.username} 授权，工位 ${ch + 1} 确认成功`);
      const chData = multiChannelData.value[ch];
      if (chData) { chData.pendingAck = { active: false }; chData.pendingRemediation = null; }
    } else {
      ElMessage.info('当前没有待确认事件');
    }
    elevateDialog.value.visible = false;
  } catch (e) {
    const status = e?.response?.status;
    const detail = e?.response?.data?.detail || e?.message || '未知错误';
    if (status === 401) {
      ElMessage.error('账号或密码错误');
    } else if (status === 403) {
      ElMessage.error(detail);
    } else {
      ElMessage.error('授权失败：' + detail);
    }
  } finally {
    elevateDialog.value.submitting = false;
  }
};
// ==============================================================

onMounted(async () => {
  monitorMounted = true;
  systemStore.loadSettings();
  // D2 启动竞态修复: 先 await 工位数 (唯一真相源) 再走下面依赖 channelCount 的取流分支,
  // 避免 getSourceStatus 在 channelCount 还是默认 1 时误起单工位流 (双工位机器上会和
  // 多工位流并存抢 MJPEG)。fetchChannelCount 内部已对单/多两套做"起新套前停旧套"。
  await fetchChannelCount();
  if (!monitorMounted) return;
  loadExtraFieldsSchema();
  scannerDisableStore.loadStatus();

  _nowTickInterval = setInterval(() => {
    nowTimestamp.value = Math.floor(Date.now() / 1000);
  }, 500);
  
  // Reset error count so reconnection works after page navigation
  streamErrorCount = 0;
  
  nextTick(() => {
    initCharts();
    resizeCanvas();
  });
  
  window.addEventListener('resize', handleResize);
  
  getSourceStatus().then(async res => {
    if (!monitorMounted) return;
    if (res.data.source_type) {
      sourceStore.setSourceType(res.data.source_type);
    }
    isRunning.value = !!res.data.is_running;
    isDetecting.value = !!res.data.is_detecting;
    
    if (channelCount.value <= 1) {
      projectStore.setRunningStatus(!!res.data.is_running);
      systemStore.setDetecting(!!res.data.is_detecting);
    }

    if (res.data.is_running) {
      if (channelCount.value <= 1) {
        startPolling();
        forceReconnectStream();
      }
      return;
    }

    if (!res.data.is_detecting && res.data.source_type && res.data.model_loaded) {
      isPaused.value = true;
      // D3: 多工位时不拉单工位预览流(多工位由 startMultiStreams 负责),
      //     否则双工位仍会叠一路单工位流, 白占带宽/解码。
      if (channelCount.value <= 1) forceReconnectStream();
    }
    
    if (!res.data.source_type) {
      await autoRestoreSource();
      if (!monitorMounted) return;
    } else if (res.data.source_type && !res.data.is_running) {
      if (channelCount.value <= 1) forceReconnectStream();  // D3: 同上, 多工位不拉单工位流
    }
  }).catch(() => {
  });
});

const handleResize = () => {
  resizeCanvas();
  if (pieChartInstance && !pieChartInstance.isDisposed()) {
    pieChartInstance.resize();
  }
  if (gaugeChartInstance && !gaugeChartInstance.isDisposed()) {
    gaugeChartInstance.resize();
  }
};

onUnmounted(() => {
  monitorMounted = false;
  if (_nowTickInterval) { clearInterval(_nowTickInterval); _nowTickInterval = null; }
  window.removeEventListener('resize', handleResize);
  stopPolling();
  stopMultiPolling();
  stopMultiStreams();
  resetMultiRuntimeState(true);
  clearMonitorPendingTimers();
  if (workpieceOverrideTimer) { clearTimeout(workpieceOverrideTimer); workpieceOverrideTimer = null; }
  if (workpieceHideTimer) { clearTimeout(workpieceHideTimer); workpieceHideTimer = null; }
  workpieceOverride.value = undefined;
  
  disconnectStream();
  
  if (counterWatchTimer) {
    clearTimeout(counterWatchTimer);
    counterWatchTimer = null;
  }
  
  systemStore.setDetecting(false);
  projectStore.setRunningStatus(false);
  
  if (pieChartInstance) {
    pieChartInstance.dispose();
    pieChartInstance = null;
  }
  if (gaugeChartInstance) {
    gaugeChartInstance.dispose();
    gaugeChartInstance = null;
  }
});

// 监听计数器变化更新图表（节流：最多每2秒更新一次）
let counterWatchTimer = null;
watch(counters, () => {
  if (!counterWatchTimer) {
    counterWatchTimer = setTimeout(() => {
      updateCharts();
      counterWatchTimer = null;
    }, 2000);
  }
}, { deep: true });

// 暴露触发事件方法供测试
defineExpose({ triggerEvent, showToast });
</script>

<style scoped>
.toast-enter-active,
.toast-leave-active {
  transition: all 0.3s ease;
}

.toast-enter-from {
  opacity: 0;
  transform: translateX(50px);
}

.toast-leave-to {
  opacity: 0;
  transform: translateX(50px);
}

/* 视频进度条样式 */
.video-progress-slider :deep(.el-slider__runway) {
  height: 0.25rem;
  background-color: rgba(100, 116, 139, 0.5);
}

.video-progress-slider :deep(.el-slider__bar) {
  height: 0.25rem;
  background-color: #06b6d4;
}

.video-progress-slider :deep(.el-slider__button) {
  width: 0.75rem;
  height: 0.75rem;
  border: 0.125rem solid #06b6d4;
  background-color: #0f172a;
}

.video-progress-slider :deep(.el-slider__button):hover {
  transform: scale(1.2);
}

/* 视频倍速选择器样式 */
.video-speed-select :deep(.el-input__wrapper) {
  background-color: rgba(15, 23, 42, 0.8);
  border-color: rgba(100, 116, 139, 0.3);
  box-shadow: none;
}

.video-speed-select :deep(.el-input__inner) {
  color: #06b6d4;
  font-size: 0.75rem;
}

/* 同步模式开关样式 */
.video-sync-switch :deep(.el-switch__core) {
  background-color: rgba(100, 116, 139, 0.3);
  border-color: rgba(100, 116, 139, 0.3);
}

.video-sync-switch :deep(.is-checked .el-switch__core) {
  background-color: #06b6d4;
  border-color: #06b6d4;
}

.video-sync-switch :deep(.el-switch__label) {
  color: #9ca3af;
  font-size: 0.6875rem;
}

.video-sync-switch :deep(.el-switch__label.is-active) {
  color: #06b6d4;
}

@keyframes warn-blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.3; }
}
.warn-no-barcode-blink {
  animation: warn-blink 1s ease-in-out infinite;
}
</style>
