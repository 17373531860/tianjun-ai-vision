<template>
  <!-- 双工位列: 视频 → MES条 → 计数行 → SOP+步骤表 → 控制按钮 (v3.54 布局槽位原样保留) -->
  <div v-if="variant === 'dual'" :data-testid="`dual-col-${ch}`" data-layout-canvas="dual"
    class="flex flex-col gap-1.5 min-h-0 overflow-hidden relative">
  <!-- Video panel (70% height)（M-4 外置 ChannelVideoCard, 流/绘制机制留父级） -->
  <ChannelVideoCard
    data-layout-slot="video"
    style="flex: 7 1 0%;"
    :ch="ch"
    :ch-data="multiChannelData[ch]"
    :model-stats="channelModelStats[ch]"
    :selected="selectedChannel === (ch)"
    :register-video-canvas="el => { multiVideoCanvasRefs[ch] = el }"
    :register-overlay-canvas="el => { multiCanvasRefs[ch] = el }"
    :zoomable="multiMonitorRuntime.enabled"
    @select="selectOverviewChannel(ch)"
    @zoom="zoomChannel(ch)"
  />
  <!-- v3.1.3: per-channel MES 信息条 (工件号 / 未绑码警告 / 等待扫码 / 清除按钮) -->
  <div v-if="shouldShowMesBarFor(ch) || layoutEditActive" data-layout-slot="mes-bar" :data-testid="`dual-mes-${ch}`"
       class="bg-slate-900 border border-cyan-800/50 rounded-lg px-2 py-1 flex items-center gap-3 text-xs flex-shrink-0">
    <div v-if="!isScanDisabledFor(ch) && getDisplayWorkpieceFor(ch)" class="flex items-center gap-1.5 min-w-0">
      <span class="text-cyan-400 font-bold">工件:</span>
      <span class="font-mono text-white truncate" :title="getDisplayWorkpieceFor(ch).serial_no">{{ getDisplayWorkpieceFor(ch).serial_no }}</span>
      <el-tag :type="getDisplayWorkpieceFor(ch).status === 'ok' ? 'success' : getDisplayWorkpieceFor(ch).status === 'ng' ? 'danger' : getDisplayWorkpieceFor(ch).status === 'inspecting' ? 'warning' : 'info'" size="small">
        {{ { registered: '已登记', queued: '排队', inspecting: '检测中', ok: '合格', ng: '不良' }[getDisplayWorkpieceFor(ch).status] || getDisplayWorkpieceFor(ch).status }}
      </el-tag>
    </div>
    <div v-if="!isScanDisabledFor(ch) && hasScannerFor(ch) && getMesDataFor(ch)?.warn_no_barcode" class="warn-no-barcode-blink flex items-center gap-1 bg-yellow-600/30 border border-yellow-500 rounded px-2 py-0.5">
      <span class="text-yellow-300 font-bold">⚠ 未绑码</span>
      <span class="text-yellow-200">请扫描工件条码</span>
    </div>
    <div v-else-if="!isScanDisabledFor(ch) && hasScannerFor(ch) && !getDisplayWorkpieceFor(ch) && !getMesDataFor(ch)?.order" class="text-gray-500">等待扫码...</div>
    <!-- v3.4.2 禁用扫码态: 显示小提示, 整栏其它工件/警告/等待全部隐藏 -->
    <div v-if="isScanDisabledFor(ch)" class="flex items-center gap-1 text-gray-400 italic">
      <span>⛔ 扫码已禁用 · 走项目原生结算</span>
    </div>
    <div v-if="getMesDataFor(ch)?.order && taskInfoDisplay.show_order_chip" class="flex items-center gap-1 text-[0.625rem] ml-auto pl-2 border-l border-cyan-800/40">
      <span class="text-cyan-400">工单:</span>
      <span class="text-white truncate max-w-[80px]" :title="getMesDataFor(ch).order.order_no">{{ getMesDataFor(ch).order.order_no }}</span>
      <span class="text-gray-400">{{ getMesDataFor(ch).order.completed_qty }}/{{ getMesDataFor(ch).order.planned_qty }}</span>
    </div>
    <!-- 开工任务要素 (按入站配置逐项显示; 全关时无任何标签) -->
    <div v-if="getTaskInfoItemsFor(ch).length" class="flex items-center gap-1 text-[0.625rem] pl-2 border-l border-cyan-800/40">
      <template v-for="it in getTaskInfoItemsFor(ch)" :key="it.label">
        <span class="text-cyan-400">{{ it.label }}:</span>
        <span class="text-white truncate max-w-[72px]" :title="it.value">{{ it.value }}</span>
      </template>
    </div>
    <!-- v3.56: 多码采集启用时隐藏单码"清除" (纠错统一走多码面板) -->
    <el-tooltip
      v-if="systemStore.display.monitor.showScanButtons !== false && hasScannerFor(ch) && !isScanDisabledFor(ch) && !scanCollectFor(ch)"
      :content="getDisplayWorkpieceFor(ch) && getDisplayWorkpieceFor(ch).status === 'inspecting'
        ? '本次工件已开始检测，点击可作废本次检测、回到等待扫码状态'
        : '清除待检/扫码状态，让操作员重扫一次条码'"
      placement="top"
    >
      <el-button
        :class="getMesDataFor(ch)?.order ? '' : 'ml-auto'"
        size="small"
        type="warning"
        plain
        @click.stop="clearPendingScan(ch)"
      >
        清除
      </el-button>
    </el-tooltip>
    <!-- v3.4.2 按工位禁用扫码 -->
    <el-tooltip
      v-if="systemStore.display.monitor.showScanButtons !== false && hasScannerFor(ch)"
      :content="isScanDisabledFor(ch)
        ? '点击启用扫码：扫码器恢复工作，按扫码器配置的结算方式 (scan_pair / mid_cycle 等) 工作'
        : '点击禁用扫码：扫码器熄灯，所有联动工位回退到项目原生结算方式 (tracking → 全部消失，容器 → 箱子离开)'"
      placement="top"
    >
      <el-button
        :class="isScanDisabledFor(ch) ? '' : (getMesDataFor(ch)?.order ? '' : 'ml-auto')"
        size="small"
        :type="isScanDisabledFor(ch) ? 'success' : 'danger'"
        plain
        :loading="scannerDisableStore.toggling"
        @click.stop="toggleScanDisableFor(ch)"
      >
        {{ isScanDisabledFor(ch) ? '启用扫码' : '禁用扫码' }}
      </el-button>
    </el-tooltip>
  </div>
  <!-- v3.56: 周期多码采集已扫进度 (紧凑态, 点"明细"看逐码列表/纠错) -->
  <ScanSlotsPanel
    v-if="scanCollectFor(ch) || layoutEditActive"
    data-layout-slot="scan-slots" :data-testid="`dual-scan-${ch}`"
    class="flex-shrink-0" compact
    :state="scanCollectFor(ch)" :channel-id="ch" />
  <!-- Row 1: Counters (scrollable) + Yield Rate -->
  <div data-layout-slot="counters" :data-testid="`dual-counters-${ch}`" class="flex gap-2 flex-shrink-0">
    <div class="flex-1 flex gap-2 overflow-x-auto min-w-0">
      <div class="flex-shrink-0 bg-slate-900 border border-slate-700 rounded px-4 py-2 text-center min-w-[5.625rem]">
        <div class="text-xs text-gray-400">总产量</div>
        <div class="text-2xl font-bold font-mono text-white">{{ multiChannelData[ch]?.total ?? 0 }}</div>
      </div>
      <div class="flex-shrink-0 bg-slate-900 border border-slate-700 rounded px-4 py-2 text-center min-w-[5.625rem]">
        <div class="text-xs text-gray-400">合格</div>
        <div class="text-2xl font-bold font-mono text-green-400">{{ multiChannelData[ch]?.ok ?? 0 }}</div>
      </div>
      <div class="flex-shrink-0 bg-slate-900 border border-slate-700 rounded px-4 py-2 text-center min-w-[5.625rem]">
        <div class="text-xs text-gray-400">不良</div>
        <div class="text-2xl font-bold font-mono text-red-400">{{ multiChannelData[ch]?.ng ?? 0 }}</div>
      </div>
    </div>
    <div class="flex-shrink-0 w-32 bg-slate-900 border border-slate-700 rounded px-3 py-2 flex flex-col items-center justify-center">
      <div class="text-xs text-gray-400">合格率</div>
      <div class="text-2xl font-bold font-mono" :class="(multiChannelData[ch]?.yieldRate ?? 0) >= 90 ? 'text-green-400' : (multiChannelData[ch]?.yieldRate ?? 0) >= 70 ? 'text-yellow-400' : 'text-red-400'">
        {{ multiChannelData[ch]?.yieldRate ?? 0 }}%
      </div>
    </div>
  </div>
  <!-- Row 2: 工艺主面板 — 槽位 id 契约永远是 sop-row (布局落库), 槽内内容按工位模式切换:
       tracking/per_item/weighing → 专属面板 (阶段3, v3.55); 步骤类模式 → 原 SOP+步骤表 -->
  <div data-layout-slot="sop-row" :data-testid="`dual-sop-${ch}`"
       :data-mode-panel="modePanelKind || undefined"
       class="flex gap-2 min-h-0" style="flex: 3 1 0%;">
    <WorkstationModePanel v-if="modePanelKind" class="flex-1 min-w-0 min-h-0"
      :mode="modePanelKind" :ch="ch" :ch-data="multiChannelData[ch]" />
    <template v-else>
    <div class="w-[60%] bg-slate-900 border border-slate-700 rounded overflow-hidden flex flex-col min-w-0">
      <div class="bg-slate-800 px-3 py-1 text-cyan-400 text-sm font-bold border-b border-slate-700 flex items-center justify-between flex-shrink-0">
        <span>SOP</span>
        <span class="text-xs text-gray-400">CT: {{ getDisplayCT(multiChannelData[ch]) }}</span>
      </div>
      <div class="flex-1 flex items-stretch gap-2 px-2 py-1 overflow-x-auto min-h-0">
        <div v-for="(step, idx) in (multiChannelData[ch]?.steps || [])" :key="idx"
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
        <div v-if="!multiChannelData[ch]?.steps?.length" class="text-gray-600 text-sm w-full text-center self-center">等待检测</div>
      </div>
    </div>
    <div class="w-[40%] bg-slate-900 border border-slate-700 rounded overflow-auto min-w-0">
      <table class="w-full text-xs">
        <thead class="bg-slate-800 text-gray-400 sticky top-0"><tr><th class="px-1.5 py-1 text-left">步骤</th><th class="px-1.5 py-1 text-left">状态</th></tr></thead>
        <tbody class="text-gray-300 divide-y divide-slate-800">
          <tr v-for="(row, i) in (multiChannelData[ch]?.tableData || []).slice(0, 8)" :key="i" :class="row.status === 'completed' ? 'bg-green-900/20' : ''">
            <td class="px-1.5 py-0.5 truncate max-w-[100px]">{{ row.step }}</td>
            <td class="px-1.5 py-0.5"><span :class="row.status === 'completed' ? 'text-green-400' : 'text-gray-500'">{{ row.status === 'completed' ? 'OK' : '--' }}</span></td>
          </tr>
        </tbody>
      </table>
    </div>
    </template>
  </div>
  <!-- v3.55.x 混合模式物品校验 (装箱清点三分框/混合逐件): 与上方 SOP 行并存,
       该工位项目未配 custom_mix 时轮询无 custom_mix_state → 不渲染零差异;
       显示设置「物品校验面板」(mixPanel) 可整体关, 与单工位/放大态同一开关 -->
  <PerItemPanel
    v-if="showMixPanel && mixPerItemStateFor(ch)"
    data-layout-slot="mix-panel" :data-testid="`dual-mix-${ch}`"
    class="flex-shrink-0"
    :state="mixPerItemStateFor(ch)" :channel="ch" mix />
  <CustomMixItemPanel
    v-else-if="showMixPanel && mixTrackingStateFor(ch)"
    data-layout-slot="mix-panel" :data-testid="`dual-mix-${ch}`"
    class="flex-shrink-0"
    :state="mixTrackingStateFor(ch)"
    :tracking-checklist="multiChannelData[ch]?.tracking?.item_checklist || {}" />
  <!-- Controls -->
  <div data-layout-slot="controls" class="flex gap-1.5 flex-shrink-0" data-testid="channel-controls" :data-channel="ch">
    <button @click="startDetectionForChannel(ch)" :disabled="(!multiChannelData[ch]?.project && !currentProject) || multiChannelData[ch]?.isDetecting"
      class="flex-1 bg-emerald-600 hover:bg-emerald-500 disabled:bg-gray-700 disabled:cursor-not-allowed text-white py-1 rounded text-xs font-bold">开始</button>
    <button @click="stopDetectionForChannel(ch)" :disabled="!multiChannelData[ch]?.isRunning"
      class="flex-1 bg-red-600 hover:bg-red-500 disabled:bg-gray-700 disabled:cursor-not-allowed text-white py-1 rounded text-xs font-bold">停止</button>
    <button @click="standbyForChannel(ch)" :disabled="!multiChannelData[ch]?.isDetecting"
      class="flex-1 bg-yellow-600 hover:bg-yellow-500 disabled:bg-gray-700 disabled:cursor-not-allowed text-white py-1 rounded text-xs font-bold">待机</button>
    <button @click="openResetDialog(ch)"
      class="flex-1 bg-cyan-500 hover:bg-cyan-400 disabled:bg-gray-700 disabled:cursor-not-allowed text-white py-1 rounded text-xs font-bold">清零</button>
  </div>
  <!-- Per-workstation event toasts -->
  <!-- v3.13 M2.2b: 客户插件可通过 cycle-result.indicator slot 替换或隐藏整个 toast 渲染体 -->
  <template v-for="position in ['top-right', 'top-left', 'bottom-right', 'bottom-left', 'center']" :key="position">
    <div class="absolute z-50 pointer-events-none flex flex-col gap-2" :class="getMultiPositionClass(position)">
      <transition-group name="toast">
        <TjSlot
          v-for="toast in (multiActiveToasts[ch] || []).filter(t => t.position === position)"
          :key="toast.id"
          name="cycle-result.indicator"
          :toast="toast"
          :channel-id="ch"
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

    <slot />
  </div>
  <!-- 三工位列: 视频 → 计数 → 质量区 → MES条 → SOP → 步骤表 → 控制按钮 (v3.52) -->
  <div v-else-if="variant === 'triple'" :data-testid="`triple-col-${ch}`" data-layout-canvas="triple"
    class="flex flex-col gap-1.5 min-w-0 min-h-0 overflow-hidden relative rounded-lg border p-1"
    :class="selectedChannel === ch ? 'border-cyan-600/70 bg-slate-950/40' : 'border-slate-800 bg-slate-950/20'">
  <!-- 视频卡: 宽度随列自适应, 画面始终 contain letterbox (等比不拉伸/不裁剪).
       显示 SOP 卡片时贴 16:9 (与样例一致, 零黑边);
       关掉 SOP 卡片时略放高到 16:10 → 画面区小幅往下延长, 仅一丢丢黑边, 余量仍给步骤表. -->
  <ChannelVideoCard
    data-layout-slot="video"
    class="w-full flex-shrink-0"
    :style="systemStore.display.monitor.stepStrip !== false ? 'aspect-ratio: 16 / 9;' : 'aspect-ratio: 16 / 10;'"
    :ch="ch"
    :ch-data="multiChannelData[ch]"
    :model-stats="channelModelStats[ch]"
    :selected="selectedChannel === (ch)"
    :register-video-canvas="el => { multiVideoCanvasRefs[ch] = el }"
    :register-overlay-canvas="el => { multiCanvasRefs[ch] = el }"
    :zoomable="multiMonitorRuntime.enabled"
    @select="selectOverviewChannel(ch)"
    @zoom="zoomChannel(ch)"
  />
  <!-- 计数行: 总产量/合格/不良/CT 四等分横排 (跟随显示设置 defaultCounters) -->
  <div data-layout-slot="counters" class="flex gap-1.5 flex-shrink-0">
    <div v-if="systemStore.display.monitor.defaultCounters?.showTotal !== false" class="flex-1 bg-slate-900 border border-slate-700 rounded px-1 py-1 text-center min-w-0">
      <div class="text-[0.625rem] text-gray-400">总产量</div>
      <div class="text-xl font-bold font-mono text-white">{{ multiChannelData[ch]?.total ?? 0 }}</div>
    </div>
    <div v-if="systemStore.display.monitor.defaultCounters?.showGood !== false" class="flex-1 bg-slate-900 border border-slate-700 rounded px-1 py-1 text-center min-w-0">
      <div class="text-[0.625rem] text-gray-400">合格</div>
      <div class="text-xl font-bold font-mono text-green-400">{{ multiChannelData[ch]?.ok ?? 0 }}</div>
    </div>
    <div v-if="systemStore.display.monitor.defaultCounters?.showBad !== false" class="flex-1 bg-slate-900 border border-slate-700 rounded px-1 py-1 text-center min-w-0">
      <div class="text-[0.625rem] text-gray-400">不良</div>
      <div class="text-xl font-bold font-mono text-red-400">{{ multiChannelData[ch]?.ng ?? 0 }}</div>
    </div>
    <div class="flex-1 bg-slate-900 border border-slate-700 rounded px-1 py-1 text-center min-w-0">
      <div class="text-[0.625rem] text-gray-400">CT</div>
      <div class="text-xl font-bold font-mono text-cyan-400">{{ getDisplayCT(multiChannelData[ch]) }}</div>
    </div>
  </div>
  <!-- 质量区: 合格率圆环 + NG 步骤 TOP3 榜单 + 产出统计条 三块并排 (各自成块, 匀称铺满一行;
       分别跟随 capacityChart / ngTop3 / defectChart 显示开关). 占固定高度, 相应压缩下方 SOP / 步骤表区. -->
  <div v-if="systemStore.display.monitor.capacityChart !== false || systemStore.display.monitor.ngTop3 !== false || systemStore.display.monitor.defectChart !== false || layoutEditActive"
    :data-testid="`triple-quality-${ch}`" data-layout-slot="quality"
    class="flex gap-1.5 flex-shrink-0 h-24">
    <!-- 合格率圆环 -->
    <div v-if="systemStore.display.monitor.capacityChart !== false"
      :data-testid="`triple-yield-${ch}`"
      class="w-24 flex-shrink-0 bg-slate-900 border border-slate-700 rounded p-1 flex flex-col">
      <div class="text-[0.625rem] text-cyan-400 font-bold flex-shrink-0">合格率</div>
      <div class="flex-1 min-h-0 w-full relative flex items-center justify-center">
        <svg viewBox="0 0 36 36" class="h-full max-h-[4.5rem] -rotate-90">
          <circle cx="18" cy="18" r="15.915" fill="none" stroke="#334155" stroke-width="3.6" />
          <circle cx="18" cy="18" r="15.915" fill="none" stroke-linecap="round" stroke-width="3.6"
            :stroke="(multiChannelData[ch]?.yieldRate ?? 0) >= 90 ? '#10b981' : (multiChannelData[ch]?.yieldRate ?? 0) >= 70 ? '#f59e0b' : '#ef4444'"
            :stroke-dasharray="`${multiChannelData[ch]?.yieldRate ?? 0} ${100 - (multiChannelData[ch]?.yieldRate ?? 0)}`" />
        </svg>
        <div class="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
          <span class="text-sm font-bold font-mono leading-none"
            :class="(multiChannelData[ch]?.yieldRate ?? 0) >= 90 ? 'text-green-400' : (multiChannelData[ch]?.yieldRate ?? 0) >= 70 ? 'text-yellow-400' : 'text-red-400'">
            {{ multiChannelData[ch]?.yieldRate ?? 0 }}%
          </span>
          <span class="text-[0.5rem] text-gray-500 font-mono">{{ multiChannelData[ch]?.ok ?? 0 }}/{{ multiChannelData[ch]?.total ?? 0 }}</span>
        </div>
      </div>
    </div>
    <!-- NG 步骤 TOP3 榜单 -->
    <div v-if="systemStore.display.monitor.ngTop3 !== false"
      :data-testid="`triple-ngtop3-${ch}`"
      class="flex-1 min-w-0 bg-slate-900 border border-slate-700 rounded p-1 flex flex-col">
      <div class="flex items-center justify-between mb-0.5 flex-shrink-0">
        <span class="text-[0.625rem] text-cyan-400 font-bold">NG 步骤 TOP3</span>
        <span class="text-[0.5625rem] text-gray-500 cursor-pointer hover:text-cyan-400 select-none" @click="toggleNgTopMode()">
          {{ systemStore.display.monitor.ngTopDisplayMode === 'percentage' ? '百分比' : '次数' }}
        </span>
      </div>
      <div class="flex-1 overflow-auto space-y-0.5 min-h-0">
        <div v-for="(item, idx) in (multiChannelData[ch]?.ngStepRanking || [])" :key="item.step"
          class="flex items-center gap-1 bg-slate-800/50 px-1.5 py-0.5 rounded text-[0.625rem]">
          <span class="font-bold w-3.5 text-white text-center flex-shrink-0">{{ idx + 1 }}</span>
          <span class="flex-1 text-gray-300 truncate min-w-0">{{ item.step }}</span>
          <span class="font-bold text-white flex-shrink-0">{{ systemStore.display.monitor.ngTopDisplayMode === 'count' ? item.count : item.rate.toFixed(0) + '%' }}</span>
        </div>
        <div v-if="!(multiChannelData[ch]?.ngStepRanking || []).length" class="text-center text-gray-600 text-[0.625rem] py-1">暂无数据</div>
      </div>
    </div>
    <!-- 产出统计条: 合格 / 不良 占比横条 (跟随 defectChart「不良统计图表」开关), 与左侧两块匀称并排 -->
    <div v-if="systemStore.display.monitor.defectChart !== false"
      :data-testid="`triple-output-${ch}`"
      class="flex-1 min-w-0 bg-slate-900 border border-slate-700 rounded p-1 flex flex-col">
      <div class="text-[0.625rem] text-cyan-400 font-bold flex-shrink-0">产出统计</div>
      <div class="flex-1 min-h-0 flex flex-col justify-center gap-2">
        <div class="flex items-center gap-1.5">
          <span class="text-[0.625rem] text-gray-400 w-6 flex-shrink-0">合格</span>
          <div class="flex-1 h-2.5 bg-slate-800 rounded overflow-hidden min-w-0">
            <div class="h-full bg-green-500" :style="{ width: ((multiChannelData[ch]?.total ?? 0) > 0 ? Math.round((multiChannelData[ch].ok / multiChannelData[ch].total) * 100) : 0) + '%' }"></div>
          </div>
          <span class="text-[0.625rem] font-mono text-green-400 w-8 text-right flex-shrink-0">{{ multiChannelData[ch]?.ok ?? 0 }}</span>
        </div>
        <div class="flex items-center gap-1.5">
          <span class="text-[0.625rem] text-gray-400 w-6 flex-shrink-0">不良</span>
          <div class="flex-1 h-2.5 bg-slate-800 rounded overflow-hidden min-w-0">
            <div class="h-full bg-red-500" :style="{ width: ((multiChannelData[ch]?.total ?? 0) > 0 ? Math.round((multiChannelData[ch].ng / multiChannelData[ch].total) * 100) : 0) + '%' }"></div>
          </div>
          <span class="text-[0.625rem] font-mono text-red-400 w-8 text-right flex-shrink-0">{{ multiChannelData[ch]?.ng ?? 0 }}</span>
        </div>
      </div>
    </div>
  </div>
  <!-- MES 信息条 (与双工位同构) -->
  <div v-if="shouldShowMesBarFor(ch) || layoutEditActive" data-layout-slot="mes-bar"
       class="bg-slate-900 border border-cyan-800/50 rounded-lg px-2 py-1 flex items-center gap-2 text-xs flex-shrink-0 overflow-hidden">
    <div v-if="!isScanDisabledFor(ch) && getDisplayWorkpieceFor(ch)" class="flex items-center gap-1.5 min-w-0">
      <span class="text-cyan-400 font-bold">工件:</span>
      <span class="font-mono text-white truncate" :title="getDisplayWorkpieceFor(ch).serial_no">{{ getDisplayWorkpieceFor(ch).serial_no }}</span>
      <el-tag :type="getDisplayWorkpieceFor(ch).status === 'ok' ? 'success' : getDisplayWorkpieceFor(ch).status === 'ng' ? 'danger' : getDisplayWorkpieceFor(ch).status === 'inspecting' ? 'warning' : 'info'" size="small">
        {{ { registered: '已登记', queued: '排队', inspecting: '检测中', ok: '合格', ng: '不良' }[getDisplayWorkpieceFor(ch).status] || getDisplayWorkpieceFor(ch).status }}
      </el-tag>
    </div>
    <div v-if="!isScanDisabledFor(ch) && hasScannerFor(ch) && getMesDataFor(ch)?.warn_no_barcode" class="warn-no-barcode-blink flex items-center gap-1 bg-yellow-600/30 border border-yellow-500 rounded px-2 py-0.5">
      <span class="text-yellow-300 font-bold">⚠ 未绑码</span>
      <span class="text-yellow-200">请扫描工件条码</span>
    </div>
    <div v-else-if="!isScanDisabledFor(ch) && hasScannerFor(ch) && !getDisplayWorkpieceFor(ch) && !getMesDataFor(ch)?.order" class="text-gray-500">等待扫码...</div>
    <div v-if="isScanDisabledFor(ch)" class="flex items-center gap-1 text-gray-400 italic">
      <span>⛔ 扫码已禁用 · 走项目原生结算</span>
    </div>
    <div v-if="getMesDataFor(ch)?.order && taskInfoDisplay.show_order_chip" class="flex items-center gap-1 text-[0.625rem] ml-auto pl-2 border-l border-cyan-800/40">
      <span class="text-cyan-400">工单:</span>
      <span class="text-white truncate max-w-[80px]" :title="getMesDataFor(ch).order.order_no">{{ getMesDataFor(ch).order.order_no }}</span>
      <span class="text-gray-400">{{ getMesDataFor(ch).order.completed_qty }}/{{ getMesDataFor(ch).order.planned_qty }}</span>
    </div>
    <div v-if="getTaskInfoItemsFor(ch).length" class="flex items-center gap-1 text-[0.625rem] pl-2 border-l border-cyan-800/40">
      <template v-for="it in getTaskInfoItemsFor(ch)" :key="it.label">
        <span class="text-cyan-400">{{ it.label }}:</span>
        <span class="text-white truncate max-w-[72px]" :title="it.value">{{ it.value }}</span>
      </template>
    </div>
    <!-- v3.56: 多码采集启用时隐藏单码"清除" (纠错统一走多码面板) -->
    <el-button v-if="systemStore.display.monitor.showScanButtons !== false && hasScannerFor(ch) && !isScanDisabledFor(ch) && !scanCollectFor(ch)"
      :class="getMesDataFor(ch)?.order ? '' : 'ml-auto'" size="small" type="warning" plain
      @click.stop="clearPendingScan(ch)">清除</el-button>
    <el-button v-if="systemStore.display.monitor.showScanButtons !== false && hasScannerFor(ch)"
      size="small" :type="isScanDisabledFor(ch) ? 'success' : 'danger'" plain
      :loading="scannerDisableStore.toggling"
      @click.stop="toggleScanDisableFor(ch)">
      {{ isScanDisabledFor(ch) ? '启用扫码' : '禁用扫码' }}
    </el-button>
  </div>
  <!-- v3.56: 周期多码采集已扫进度 (紧凑态, 与双工位同构) -->
  <ScanSlotsPanel
    v-if="scanCollectFor(ch) || layoutEditActive"
    data-layout-slot="scan-slots" :data-testid="`triple-scan-${ch}`"
    class="flex-shrink-0" compact
    :state="scanCollectFor(ch)" :channel-id="ch" />
  <!-- 工艺主面板 (整宽一行) — 受显示设置「SOP 流程卡片」(display.monitor.stepStrip) 控制.
       槽位 id 契约永远是 sop (布局落库); 槽内内容按工位模式切换:
       tracking/per_item/weighing → 专属面板 (阶段3, v3.55); 步骤类模式 → 原 SOP 卡.
       关掉即隐藏整行, 上方视频区 flex-1 向下拉大吃满余量; 显示时步骤表 flex-1 吸收余量. -->
  <WorkstationModePanel
    v-if="(systemStore.display.monitor.stepStrip !== false || layoutEditActive) && modePanelKind"
    :data-testid="`triple-sop-${ch}`" data-layout-slot="sop"
    :data-mode-panel="modePanelKind"
    :class="modePanelKind === 'tracking' ? 'h-40 flex-shrink-0' : 'flex-shrink-0 max-h-[55%] overflow-y-auto'"
    :mode="modePanelKind" :ch="ch" :ch-data="multiChannelData[ch]" />
  <div v-else-if="systemStore.display.monitor.stepStrip !== false || layoutEditActive"
    :data-testid="`triple-sop-${ch}`" data-layout-slot="sop"
    class="bg-slate-900 border border-slate-700 rounded overflow-hidden flex flex-col flex-shrink-0">
    <div class="bg-slate-800 px-2 py-0.5 text-cyan-400 text-xs font-bold border-b border-slate-700 flex items-center justify-between flex-shrink-0">
      <span>SOP</span>
      <span class="text-[0.625rem] text-gray-400">
        {{ (multiChannelData[ch]?.steps || []).length
          ? ('当前 ' + (multiChannelData[ch].steps.filter(s => s.status === 'completed').length) + ' / ' + multiChannelData[ch].steps.length)
          : '等待检测' }}
      </span>
    </div>
    <div class="flex items-stretch gap-1.5 px-1.5 py-1 overflow-x-auto h-[7rem]">
      <div v-for="(step, idx) in (multiChannelData[ch]?.steps || [])" :key="idx"
        class="flex-shrink-0 w-28 flex flex-col rounded border overflow-hidden"
        :class="step.status === 'completed' ? 'border-green-500 bg-green-900/30' : step.status === 'active' ? 'border-cyan-500 bg-cyan-900/30' : 'border-slate-600 bg-slate-800'">
        <div class="px-1 py-0.5 text-[0.625rem] font-bold truncate text-center flex-shrink-0"
          :class="step.status === 'completed' ? 'text-green-300 bg-green-900/50' : step.status === 'active' ? 'text-cyan-300 bg-cyan-900/50 animate-pulse' : 'text-gray-500 bg-slate-700/50'">
          {{ step.name }}
        </div>
        <div class="flex-1 flex items-center justify-center relative overflow-hidden bg-slate-950/50">
          <img v-if="step.screenshot" :src="step.screenshot" class="w-full h-full object-cover" />
          <el-icon v-else :size="18" class="text-slate-600"><Picture /></el-icon>
          <div v-if="step.status === 'active'" class="absolute inset-0 border-2 border-cyan-500 animate-pulse"></div>
        </div>
      </div>
      <div v-if="!multiChannelData[ch]?.steps?.length" class="text-gray-600 text-xs w-full text-center self-center">等待检测</div>
    </div>
  </div>
  <!-- 步骤状态表: 步骤类模式保留; tracking/per_item/weighing 无步骤概念则隐藏
       (编辑态强制显示, 避免布局编辑器丢块)。槽位 id 契约 step-table 不动. -->
  <div v-if="!modePanelKind || layoutEditActive"
    :data-testid="`triple-steptable-${ch}`" data-layout-slot="step-table"
    class="bg-slate-900 border border-slate-700 rounded overflow-auto min-w-0 flex-1 min-h-0">
    <table v-if="(multiChannelData[ch]?.tableData || []).length" class="w-full text-[0.625rem]">
      <thead class="bg-slate-800 text-gray-400 sticky top-0"><tr>
        <th v-if="systemStore.display.monitor.stepTableColumns?.showNo !== false" class="px-1.5 py-0.5 text-left">No</th>
        <th v-if="systemStore.display.monitor.stepTableColumns?.showStep !== false" class="px-1.5 py-0.5 text-left">步骤</th>
        <th v-if="systemStore.display.monitor.stepTableColumns?.showStatus !== false" class="px-1.5 py-0.5 text-left">状态</th>
        <th v-if="systemStore.display.monitor.stepTableColumns?.showPt !== false" class="px-1.5 py-0.5 text-right">PT/s</th>
      </tr></thead>
      <tbody class="text-gray-300 divide-y divide-slate-800">
        <tr v-for="(row, i) in (multiChannelData[ch]?.tableData || []).slice(0, 12)" :key="i" :class="row.status === 'completed' ? 'bg-green-900/20' : ''">
          <td v-if="systemStore.display.monitor.stepTableColumns?.showNo !== false" class="px-1.5 py-0.5 text-gray-500">{{ i + 1 }}</td>
          <td v-if="systemStore.display.monitor.stepTableColumns?.showStep !== false" class="px-1.5 py-0.5 truncate max-w-[100px]">{{ row.step }}</td>
          <td v-if="systemStore.display.monitor.stepTableColumns?.showStatus !== false" class="px-1.5 py-0.5"><span :class="row.status === 'completed' ? 'text-green-400' : 'text-gray-500'">{{ row.status === 'completed' ? 'OK' : '--' }}</span></td>
          <td v-if="systemStore.display.monitor.stepTableColumns?.showPt !== false" class="px-1.5 py-0.5 text-right font-mono text-gray-400">{{ getStepPT(multiChannelData[ch], row.label) }}</td>
        </tr>
      </tbody>
    </table>
    <div v-else class="h-full flex items-center justify-center text-gray-600 text-[0.625rem] px-2 text-center">暂无步骤数据</div>
  </div>
  <!-- v3.55.x 混合模式物品校验 (装箱清点三分框/混合逐件): 未配 custom_mix 零差异, mixPanel 开关可整体关 -->
  <PerItemPanel
    v-if="showMixPanel && mixPerItemStateFor(ch)"
    data-layout-slot="mix-panel" :data-testid="`triple-mix-${ch}`"
    class="flex-shrink-0"
    :state="mixPerItemStateFor(ch)" :channel="ch" mix />
  <CustomMixItemPanel
    v-else-if="showMixPanel && mixTrackingStateFor(ch)"
    data-layout-slot="mix-panel" :data-testid="`triple-mix-${ch}`"
    class="flex-shrink-0"
    :state="mixTrackingStateFor(ch)"
    :tracking-checklist="multiChannelData[ch]?.tracking?.item_checklist || {}" />
  <!-- 控制按钮: 开始/停止/待机/清零 四等宽, 贴底 -->
  <div data-layout-slot="controls" class="flex gap-1.5 flex-shrink-0" data-testid="channel-controls" :data-channel="ch">
    <button @click="startDetectionForChannel(ch)" :disabled="(!multiChannelData[ch]?.project && !currentProject) || multiChannelData[ch]?.isDetecting"
      class="flex-1 bg-emerald-600 hover:bg-emerald-500 disabled:bg-gray-700 disabled:cursor-not-allowed text-white py-1 rounded text-xs font-bold">开始</button>
    <button @click="stopDetectionForChannel(ch)" :disabled="!multiChannelData[ch]?.isRunning"
      class="flex-1 bg-red-600 hover:bg-red-500 disabled:bg-gray-700 disabled:cursor-not-allowed text-white py-1 rounded text-xs font-bold">停止</button>
    <button @click="standbyForChannel(ch)" :disabled="!multiChannelData[ch]?.isDetecting"
      class="flex-1 bg-yellow-600 hover:bg-yellow-500 disabled:bg-gray-700 disabled:cursor-not-allowed text-white py-1 rounded text-xs font-bold">待机</button>
    <button @click="openResetDialog(ch)"
      class="flex-1 bg-cyan-500 hover:bg-cyan-400 disabled:bg-gray-700 disabled:cursor-not-allowed text-white py-1 rounded text-xs font-bold">清零</button>
  </div>
  <!-- 列内 per-工位 Toast (定位上下文 = 本列卡片) -->
  <template v-for="position in ['top-right', 'top-left', 'bottom-right', 'bottom-left', 'center']" :key="position">
    <div class="absolute z-50 pointer-events-none flex flex-col gap-2" :class="getMultiPositionClass(position)">
      <transition-group name="toast">
        <TjSlot
          v-for="toast in (multiActiveToasts[ch] || []).filter(t => t.position === position)"
          :key="toast.id"
          name="cycle-result.indicator"
          :toast="toast"
          :channel-id="ch"
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
    <slot />
  </div>
</template>

<script setup>
/**
 * WorkstationColumn — 双/三工位共用工位列（巨石重构阶段2，v3.54.1 逐行等价移植）
 *
 * 模板按 variant 分支自 Monitor/index.vue 原样搬运（`ch - 1` 机械替换为
 * 零基 `ch` prop）；data-layout-canvas / data-layout-slot / data-testid
 * 全部原样保留 → v3.54 自定义布局 runtime 与 e2e 选择器零差异。
 *
 * ctx 是父级组装的静态对象（refs/computeds/函数/stores 混装）；顶层解构后
 * refs 在模板中自动解包，与原 index.vue 内联渲染语义一致。
 */
import { computed } from 'vue';
import { Picture } from '@element-plus/icons-vue';
import ChannelVideoCard from './ChannelVideoCard.vue';
import TjSlot from '@/components/TjSlot.vue';
import WorkstationModePanel from './WorkstationModePanel.vue';
import CustomMixItemPanel from './CustomMixItemPanel.vue';
import PerItemPanel from './PerItemPanel.vue';
import ScanSlotsPanel from './ScanSlotsPanel.vue';
import { resolveModePanelKind } from './monitorModes';

const props = defineProps({
  variant: { type: String, required: true }, // 'dual' | 'triple'
  ch: { type: Number, required: true },      // 零基工位号
  ctx: { type: Object, required: true },
});
const { ch } = props;

const {
  multiChannelData, channelModelStats, selectedChannel, currentProject,
  systemStore, scannerDisableStore, layoutEditActive, taskInfoDisplay,
  multiMonitorRuntime, multiActiveToasts, multiVideoCanvasRefs, multiCanvasRefs,
  shouldShowMesBarFor, isScanDisabledFor, getDisplayWorkpieceFor, getMesDataFor,
  getTaskInfoItemsFor, hasScannerFor, getDisplayCT, getStepPT,
  getMultiPositionClass, toggleNgTopMode,
  selectOverviewChannel, zoomChannel, clearPendingScan, toggleScanDisableFor,
  startDetectionForChannel, stopDetectionForChannel, standbyForChannel,
  resetCountersForChannel, openResetDialog,
} = props.ctx;

// 阶段3: 本工位的按模式工艺面板 — 模式来源 poll 载荷 project_config 优先,
// 兜底工位绑定项目/全局当前项目。tracking/per_item/weighing 换专属面板,
// 步骤类模式 (sequential/detection/region_events/...) 走原 SOP 卡零差异。
const modePanelKind = computed(() => {
  const chData = multiChannelData.value?.[ch];
  return resolveModePanelKind(
    chData?._pollProjectConfig,
    chData?.project || currentProject.value,
  );
});

// v3.55.x 混合模式物品校验面板多工位同步:
// 仅该工位轮询带 custom_mix_state (项目配了装箱清点/混合逐件) 才渲染, 未配置零差异;
// 显示设置「物品校验面板」可整体关 (默认开, 与单工位/放大态同一开关)。
const showMixPanel = computed(() => systemStore.display.monitor.mixPanel !== false);

// v3.56: 周期多码采集实况 (项目未启用时轮询不带 scan_collect 段 = null 零差异)
const scanCollectFor = (chId) => multiChannelData.value?.[chId]?.scanCollect || null;
const mixTrackingStateFor = (chId) => {
  const s = multiChannelData.value?.[chId]?.customMixState;
  return (s && s.mix_type === 'tracking') ? s : null;
};
// 混合逐件: 与单工位 customMixPerItemState 同构, 适配成 PerItemPanel 的 state 形状
// (config=null 自动隐藏手动按钮/收尾卡片)
const mixPerItemStateFor = (chId) => {
  const s = multiChannelData.value?.[chId]?.customMixState;
  if (!s || s.mix_type !== 'per_item') return null;
  return {
    enabled: true,
    cycle_active: !!s.cycle_active,
    cycle_start_time: null,
    config: null,
    steps: s.steps || [],
    last_ng_detail: s.last_ng_detail || null,
  };
};
</script>
