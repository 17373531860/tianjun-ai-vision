<template>
  <!-- ===== DUAL WORKSTATION MODE (2 channels) ===== -->
  <div v-if="channelCount === 2" class="grid grid-cols-2 gap-2 h-[calc(100vh-7.25rem)] p-2 relative">
    <div v-for="ch in 2" :key="ch - 1" class="flex flex-col gap-1.5 min-h-0 overflow-hidden relative">
      <!-- Video panel (70% height) -->
      <div class="relative bg-black border-2 rounded-lg overflow-hidden min-h-0"
        style="flex: 7 1 0%;"
        :class="selectedChannel === (ch - 1) ? 'border-cyan-500' : 'border-slate-700'"
        @click="selectedChannel = ch - 1">
        <canvas :ref="el => { if (el) multiVideoCanvasRefs[ch - 1] = el }" class="absolute inset-0 w-full h-full"></canvas>
        <canvas :ref="el => { if (el) multiCanvasRefs[ch - 1] = el }" class="absolute inset-0 w-full h-full pointer-events-none"></canvas>
        <div class="absolute top-1.5 left-1.5 bg-slate-900/80 text-white px-2 py-0.5 rounded text-xs font-bold">
          工位 {{ ch }}
          <span v-if="multiChannelData[ch - 1]?.projectName" class="text-cyan-400 ml-1">{{ multiChannelData[ch - 1].projectName }}</span>
        </div>
        <div class="absolute top-1.5 right-1.5 px-2 py-0.5 rounded text-[0.625rem] font-bold"
          :class="multiChannelData[ch - 1]?.isDetecting ? 'bg-green-600/90 text-white animate-pulse' : multiChannelData[ch - 1]?.isRunning ? 'bg-yellow-600/90 text-white' : 'bg-gray-600/90 text-white'">
          {{ multiChannelData[ch - 1]?.isDetecting ? '检测中' : multiChannelData[ch - 1]?.isRunning ? '待机' : '停止' }}
        </div>
        <div class="absolute bottom-0 left-0 right-0 bg-black/70 backdrop-blur-sm px-2 py-1 flex gap-3 text-xs items-center">
          <span class="text-white font-mono">总: <span class="text-cyan-400 font-bold">{{ multiChannelData[ch - 1]?.total ?? 0 }}</span></span>
          <span class="text-white font-mono">OK: <span class="text-green-400 font-bold">{{ multiChannelData[ch - 1]?.ok ?? 0 }}</span></span>
          <span class="text-white font-mono">NG: <span class="text-red-400 font-bold">{{ multiChannelData[ch - 1]?.ng ?? 0 }}</span></span>
          <!-- feat/multi-model-roi-link b1: 多通道副模型 fps 快照 (>=2 个 slot 时显示) -->
          <template v-if="(channelModelStats[ch - 1] || []).length >= 2">
            <span class="text-gray-500">|</span>
            <span v-for="m in channelModelStats[ch - 1]" :key="m.name"
                  class="flex items-center gap-1 text-[0.6875rem]" :title="`${m.name} (${m.model_loaded ? '已加载' : '未加载'})`">
              <span class="w-2 h-2 rounded-sm flex-shrink-0" :style="{ backgroundColor: m.display_color || '#10b981' }"></span>
              <span class="text-gray-400">{{ m.name }}</span>
              <span class="text-cyan-400 font-mono">{{ m.fps_inference || 0 }}</span>
            </span>
          </template>
          <span class="ml-auto text-gray-400">FPS: {{ multiChannelData[ch - 1]?.fps ?? 0 }}</span>
        </div>
      </div>
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
      <template v-for="position in ['top-right', 'top-left', 'bottom-right', 'bottom-left', 'center']" :key="position">
        <div class="absolute z-50 pointer-events-none flex flex-col gap-2" :class="getMultiPositionClass(position)">
          <transition-group name="toast">
            <div v-for="toast in (multiActiveToasts[ch - 1] || []).filter(t => t.position === position)" :key="toast.id"
              class="px-4 py-3 rounded-xl shadow-2xl text-white font-bold pointer-events-auto transform transition-all duration-300 text-center"
              :style="{ backgroundColor: toast.color, fontSize: (toast.fontSize / 16) + 'rem' }">
              <div class="flex items-center gap-2 justify-center">
                <el-icon :size="20"><component :is="toast.icon" /></el-icon>
                <div><div class="font-bold">{{ toast.title }}</div><div v-if="toast.subtitle" class="text-sm opacity-80">{{ toast.subtitle }}</div></div>
              </div>
            </div>
          </transition-group>
        </div>
      </template>

      <button
        v-if="totalRecordingFailureCount > 0"
        class="absolute right-2 bottom-2 z-40 bg-amber-600/90 hover:bg-amber-500 text-white text-xs px-2 py-1 rounded flex items-center gap-1"
        @click="showRecordingFailurePanel = true"
      >
        <el-icon><Warning /></el-icon>
        录像异常 {{ totalRecordingFailureCount }}
      </button>

      <div v-if="showRecordingFailurePanel" class="absolute inset-0 z-50 bg-black/50 flex items-center justify-center p-4">
        <div class="w-full max-w-5xl max-h-[85vh] bg-slate-900 border border-slate-700 rounded-lg flex flex-col">
          <div class="px-4 py-3 border-b border-slate-700 flex items-center">
            <span class="text-amber-300 font-bold">录像异常详情</span>
            <span class="text-xs text-gray-400 ml-3">仅记录最近异常，用于排查</span>
            <div class="ml-auto flex gap-2">
              <el-button size="small" type="warning" plain :loading="recordingFailureLoading" @click="clearRecordingFailures">
                清空列表
              </el-button>
              <el-button size="small" @click="showRecordingFailurePanel = false">关闭</el-button>
            </div>
          </div>
          <div class="p-3 overflow-auto">
            <table class="w-full text-xs text-left">
              <thead class="text-gray-400 border-b border-slate-700">
                <tr>
                  <th class="py-1 pr-2">时间</th>
                  <th class="py-1 pr-2">工位</th>
                  <th class="py-1 pr-2">类型</th>
                  <th class="py-1 pr-2">原因</th>
                  <th class="py-1 pr-2">文件</th>
                  <th class="py-1 pr-2">已写帧</th>
                </tr>
              </thead>
              <tbody class="text-gray-200">
                <tr v-for="(item, idx) in recordingFailureRows" :key="idx" class="border-b border-slate-800">
                  <td class="py-1 pr-2 whitespace-nowrap">{{ formatRecordingFailureTime(item.timestamp) }}</td>
                  <td class="py-1 pr-2">工位{{ item.channel_id + 1 }}</td>
                  <td class="py-1 pr-2">{{ item.recorder_type }}</td>
                  <td class="py-1 pr-2">
                    <div>{{ getRecordingFailureReasonText(item.reason) }}</div>
                    <div v-if="item.error" class="text-gray-400 break-all">{{ item.error }}</div>
                  </td>
                  <td class="py-1 pr-2 break-all text-gray-300">{{ item.file_path || '-' }}</td>
                  <td class="py-1 pr-2">{{ item.frame_count ?? '-' }}</td>
                </tr>
                <tr v-if="recordingFailureRows.length === 0">
                  <td colspan="6" class="py-4 text-center text-gray-500">暂无录像异常</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- ===== QUAD WORKSTATION MODE (4 channels) ===== -->
  <div v-else-if="channelCount > 2" class="flex flex-col h-[calc(100vh-7.25rem)] p-2 gap-2 relative">
    <!-- 2x2 video grid -->
    <div class="flex-1 grid grid-cols-2 grid-rows-2 gap-2 min-h-0">
      <div v-for="ch in channelCount" :key="ch - 1"
        class="relative bg-black border-2 rounded-lg overflow-hidden cursor-pointer transition-all min-h-0"
        :class="selectedChannel === (ch - 1) ? 'border-cyan-500 shadow-[0_0_8px_rgba(6,182,212,0.4)]' : 'border-slate-700 hover:border-slate-500'"
        @click="selectedChannel = ch - 1">
        <canvas :ref="el => { if (el) multiVideoCanvasRefs[ch - 1] = el }" class="absolute inset-0 w-full h-full"></canvas>
        <canvas :ref="el => { if (el) multiCanvasRefs[ch - 1] = el }" class="absolute inset-0 w-full h-full pointer-events-none"></canvas>
        <div class="absolute top-1 left-1 bg-slate-900/80 text-white px-2 py-0.5 rounded text-[0.625rem] font-bold">
          工位{{ ch }}
          <span v-if="multiChannelData[ch - 1]?.projectName" class="text-cyan-400 ml-0.5">{{ multiChannelData[ch - 1].projectName }}</span>
        </div>
        <div class="absolute top-1 right-1 px-1.5 py-0.5 rounded text-[0.625rem] font-bold"
          :class="multiChannelData[ch - 1]?.isDetecting ? 'bg-green-600/90 text-white animate-pulse' : multiChannelData[ch - 1]?.isRunning ? 'bg-yellow-600/90 text-white' : 'bg-gray-600/90 text-white'">
          {{ multiChannelData[ch - 1]?.isDetecting ? '检测中' : multiChannelData[ch - 1]?.isRunning ? '待机' : '停止' }}
        </div>
        <div class="absolute bottom-0 left-0 right-0 bg-black/70 px-2 py-1 flex gap-3 text-[0.625rem] items-center">
          <span class="text-white font-mono">总:<span class="text-cyan-400 font-bold">{{ multiChannelData[ch - 1]?.total ?? 0 }}</span></span>
          <span class="text-white font-mono">OK:<span class="text-green-400 font-bold">{{ multiChannelData[ch - 1]?.ok ?? 0 }}</span></span>
          <span class="text-white font-mono">NG:<span class="text-red-400 font-bold">{{ multiChannelData[ch - 1]?.ng ?? 0 }}</span></span>
          <!-- feat/multi-model-roi-link b1: 4 工位空间紧, 仅色块+fps 数字 -->
          <template v-if="(channelModelStats[ch - 1] || []).length >= 2">
            <span v-for="m in channelModelStats[ch - 1]" :key="m.name"
                  class="flex items-center gap-0.5 text-[0.5625rem]"
                  :title="`${m.name} ${m.fps_inference || 0}fps ${m.model_loaded ? '' : '(未加载)'}`">
              <span class="w-1.5 h-1.5 rounded-sm flex-shrink-0" :style="{ backgroundColor: m.display_color || '#10b981' }"></span>
              <span class="text-cyan-400 font-mono">{{ m.fps_inference || 0 }}</span>
            </span>
          </template>
          <span class="ml-auto text-gray-400">FPS:{{ multiChannelData[ch - 1]?.fps ?? 0 }}</span>
        </div>
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
      </div>
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

      <button
        v-if="totalRecordingFailureCount > 0"
        class="absolute right-2 bottom-2 z-40 bg-amber-600/90 hover:bg-amber-500 text-white text-xs px-2 py-1 rounded flex items-center gap-1"
        @click="showRecordingFailurePanel = true"
      >
        <el-icon><Warning /></el-icon>
        录像异常 {{ totalRecordingFailureCount }}
      </button>

      <div v-if="showRecordingFailurePanel" class="absolute inset-0 z-50 bg-black/50 flex items-center justify-center p-4">
        <div class="w-full max-w-5xl max-h-[85vh] bg-slate-900 border border-slate-700 rounded-lg flex flex-col">
          <div class="px-4 py-3 border-b border-slate-700 flex items-center">
            <span class="text-amber-300 font-bold">录像异常详情</span>
            <span class="text-xs text-gray-400 ml-3">仅记录最近异常，用于排查</span>
            <div class="ml-auto flex gap-2">
              <el-button size="small" type="warning" plain :loading="recordingFailureLoading" @click="clearRecordingFailures">
                清空列表
              </el-button>
              <el-button size="small" @click="showRecordingFailurePanel = false">关闭</el-button>
            </div>
          </div>
          <div class="p-3 overflow-auto">
            <table class="w-full text-xs text-left">
              <thead class="text-gray-400 border-b border-slate-700">
                <tr>
                  <th class="py-1 pr-2">时间</th>
                  <th class="py-1 pr-2">工位</th>
                  <th class="py-1 pr-2">类型</th>
                  <th class="py-1 pr-2">原因</th>
                  <th class="py-1 pr-2">文件</th>
                  <th class="py-1 pr-2">已写帧</th>
                </tr>
              </thead>
              <tbody class="text-gray-200">
                <tr v-for="(item, idx) in recordingFailureRows" :key="idx" class="border-b border-slate-800">
                  <td class="py-1 pr-2 whitespace-nowrap">{{ formatRecordingFailureTime(item.timestamp) }}</td>
                  <td class="py-1 pr-2">工位{{ item.channel_id + 1 }}</td>
                  <td class="py-1 pr-2">{{ item.recorder_type }}</td>
                  <td class="py-1 pr-2">
                    <div>{{ getRecordingFailureReasonText(item.reason) }}</div>
                    <div v-if="item.error" class="text-gray-400 break-all">{{ item.error }}</div>
                  </td>
                  <td class="py-1 pr-2 break-all text-gray-300">{{ item.file_path || '-' }}</td>
                  <td class="py-1 pr-2">{{ item.frame_count ?? '-' }}</td>
                </tr>
                <tr v-if="recordingFailureRows.length === 0">
                  <td colspan="6" class="py-4 text-center text-gray-500">暂无录像异常</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>
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

      <!-- SOP流程 (Step Indicators) — non-tracking modes -->
      <div v-if="systemStore.display.monitor.stepStrip && steps.length > 0 && !isTrackingMode" class="h-44 bg-slate-900 border border-slate-700 rounded-lg overflow-hidden flex flex-col">
        <div class="bg-slate-800 px-3 py-1 border-b border-slate-700 flex-shrink-0">
          <span class="text-cyan-400 text-lg font-bold">SOP流程卡片</span>
        </div>
        <div ref="sopScrollContainer" class="flex-1 p-2 overflow-x-auto scroll-smooth">
          <div class="flex items-center h-full">
            <template v-for="(step, idx) in steps" :key="idx">
              <!-- 间隔时间显示 -->
              <div v-if="idx > 0" class="flex flex-col items-center justify-center px-1 flex-shrink-0">
                <div class="w-6 h-[2px] bg-slate-600"></div>
                <div class="text-[0.5625rem] text-yellow-400 font-mono mt-0.5 whitespace-nowrap">
                  {{ formatInterval(step.label) }}
                </div>
                <div class="w-6 h-[2px] bg-slate-600"></div>
              </div>
              
              <!-- 步骤卡片 -->
              <div
                :ref="el => { if (el) sopCardRefs[idx] = el }"
                class="w-32 flex-shrink-0 flex flex-col rounded border transition-all duration-300"
                :class="getSopCardClass(step)"
              >
                <div class="h-7 px-2 flex items-center justify-between text-xs"
                  :class="getSopHeaderClass(step)"
                >
                  <span class="font-bold truncate">{{ step.name }}</span>
                </div>
                <div class="h-16 p-1 flex items-center justify-center relative overflow-hidden"
                  :class="getSopBodyClass(step)"
                >
                   <img 
                     v-if="step.screenshot" 
                     :src="step.screenshot" 
                     class="w-full h-full object-cover rounded"
                   />
                   <el-icon v-else :size="24" class="text-slate-600"><Picture /></el-icon>
                   
                   <div v-if="step.status === 'active'" class="absolute inset-0 border-2 border-cyan-500 animate-pulse"></div>
                </div>
              </div>
            </template>
          </div>
        </div>
      </div>

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
      
      <!-- No Project Selected -->
      <div v-else-if="!currentProject" class="h-40 bg-slate-900 border border-slate-700 rounded-lg flex items-center justify-center text-gray-500">
        <div class="text-center">
          <el-icon :size="32" class="mb-2"><Folder /></el-icon>
          <p class="text-sm">请先在顶部选择项目</p>
        </div>
      </div>

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

      <!-- 操作员选择 + MES 信息条 -->
      <div class="bg-slate-900 border border-cyan-800/50 rounded-lg px-3 py-2 flex items-center gap-4 text-sm">
        <div class="flex items-center gap-2">
          <span class="text-cyan-400 font-bold text-xs">操作员:</span>
          <el-select
            v-model="currentOperatorId"
            placeholder="选择操作员"
            size="small"
            class="w-32"
            clearable
            @change="onOperatorChange"
          >
            <el-option v-for="op in operatorList" :key="op.id" :label="`${op.name} (${op.employee_no})`" :value="op.id" />
          </el-select>
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
         <!-- NG Step Ranking -->
         <div v-if="systemStore.display.monitor.ngTop3 !== false" class="bg-slate-900 border border-slate-700 rounded-lg p-2 flex flex-col">
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
                     <th class="px-2 py-1.5">No</th>
                     <th class="px-2 py-1.5">步骤</th>
                     <th class="px-2 py-1.5">状态</th>
                     <th class="px-2 py-1.5">PT/s</th>
                     <th class="px-2 py-1.5">结果</th>
                  </tr>
               </thead>
               <tbody class="divide-y divide-slate-800 text-gray-300">
                  <tr v-for="(row, i) in tableData" :key="i" 
                    class="hover:bg-slate-800/50"
                    :class="row.status === 'completed' ? 'bg-green-800/30' : ''"
                  >
                     <td class="px-2 py-1.5">{{ i + 1 }}</td>
                     <td class="px-2 py-1.5">{{ row.step }}</td>
                     <td class="px-2 py-1.5">
                       <span :class="row.status === 'completed' ? 'text-white' : 'text-gray-500'">
                         {{ row.status === 'completed' ? '已检测' : '待检测' }}
                       </span>
                     </td>
                     <td class="px-2 py-1.5 text-white font-mono">{{ formatStepPT(row.label) }}</td>
                     <td class="px-2 py-1.5">
                       <span v-if="row.cycleResult === 'ok'" class="text-green-400">OK</span>
                       <span v-else-if="row.cycleResult === 'ng'" class="text-red-500">NG</span>
                       <span v-else class="text-gray-500">--</span>
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

    <button
      v-if="totalRecordingFailureCount > 0"
      class="absolute right-2 bottom-2 z-40 bg-amber-600/90 hover:bg-amber-500 text-white text-xs px-2 py-1 rounded flex items-center gap-1"
      @click="showRecordingFailurePanel = true"
    >
      <el-icon><Warning /></el-icon>
      录像异常 {{ totalRecordingFailureCount }}
    </button>

    <div v-if="showRecordingFailurePanel" class="absolute inset-0 z-50 bg-black/50 flex items-center justify-center p-4">
      <div class="w-full max-w-5xl max-h-[85vh] bg-slate-900 border border-slate-700 rounded-lg flex flex-col">
        <div class="px-4 py-3 border-b border-slate-700 flex items-center">
          <span class="text-amber-300 font-bold">录像异常详情</span>
          <span class="text-xs text-gray-400 ml-3">仅记录最近异常，用于排查</span>
          <div class="ml-auto flex gap-2">
            <el-button size="small" type="warning" plain :loading="recordingFailureLoading" @click="clearRecordingFailures">
              清空列表
            </el-button>
            <el-button size="small" @click="showRecordingFailurePanel = false">关闭</el-button>
          </div>
        </div>
        <div class="p-3 overflow-auto">
          <table class="w-full text-xs text-left">
            <thead class="text-gray-400 border-b border-slate-700">
              <tr>
                <th class="py-1 pr-2">时间</th>
                <th class="py-1 pr-2">工位</th>
                <th class="py-1 pr-2">类型</th>
                <th class="py-1 pr-2">原因</th>
                <th class="py-1 pr-2">文件</th>
                <th class="py-1 pr-2">已写帧</th>
              </tr>
            </thead>
            <tbody class="text-gray-200">
              <tr v-for="(item, idx) in recordingFailureRows" :key="idx" class="border-b border-slate-800">
                <td class="py-1 pr-2 whitespace-nowrap">{{ formatRecordingFailureTime(item.timestamp) }}</td>
                <td class="py-1 pr-2">工位{{ item.channel_id + 1 }}</td>
                <td class="py-1 pr-2">{{ item.recorder_type }}</td>
                <td class="py-1 pr-2">
                  <div>{{ getRecordingFailureReasonText(item.reason) }}</div>
                  <div v-if="item.error" class="text-gray-400 break-all">{{ item.error }}</div>
                </td>
                <td class="py-1 pr-2 break-all text-gray-300">{{ item.file_path || '-' }}</td>
                <td class="py-1 pr-2">{{ item.frame_count ?? '-' }}</td>
              </tr>
              <tr v-if="recordingFailureRows.length === 0">
                <td colspan="6" class="py-4 text-center text-gray-500">暂无录像异常</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, ref, watch, nextTick, computed } from 'vue';
import * as echarts from 'echarts';
import { useProjectStore } from '@/store/useProjectStore';
import { useSystemStore } from '@/store/useSystemStore';
import { useSourceStore } from '@/store/useSourceStore';
import { useScannerDisableStore } from '@/store/useScannerDisableStore';
import { Check, Folder, Picture, CircleCheck, CircleClose, Warning } from '@element-plus/icons-vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { startDetection as apiStartDetection, stopDetection as apiStopDetection, pauseDetection, resumeDetection, standbyDetection, resumeInference, resetDetection, resetDetectionStats, resetPeriodicAction, getDetectionResults, getSourceStatus, setProjectConfig, getWorkstations, getScanPairActive, settleScanPairForStop } from '@/api/detection';
import { getModelDetail, resolveModelPath as apiResolveModelPath } from '@/api/model';
import { getProjectDetail } from '@/api/project';
import api, { getBackendHost } from '@/api/index';
import { getExtraFieldsSchema, setExtraFields } from '@/api/gateway';
import { getOperators, setCurrentOperator, getCurrentOperator } from '@/api/operators';

const projectStore = useProjectStore();
const systemStore = useSystemStore();
const sourceStore = useSourceStore();
const scannerDisableStore = useScannerDisableStore();

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

// SOP 滚动相关
const sopScrollContainer = ref(null);
const sopCardRefs = {};
let lastScrolledIdx = -1;

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
let cycleResetTimer = null;
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

const clearMonitorPendingTimers = () => {
  if (streamReconnectTimer) { clearTimeout(streamReconnectTimer); streamReconnectTimer = null; }
  if (cycleResetTimer) { clearTimeout(cycleResetTimer); cycleResetTimer = null; }
  if (progressReconnectTimer) { clearTimeout(progressReconnectTimer); progressReconnectTimer = null; }
  if (speedGuardTimer) { clearTimeout(speedGuardTimer); speedGuardTimer = null; }
  if (streamWatchdogTimer) { clearTimeout(streamWatchdogTimer); streamWatchdogTimer = null; }
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
const showRecordingFailurePanel = ref(false);
const recordingFailureLoading = ref(false);

const resetMultiRuntimeState = (clearChannelData = false) => {
  multiPollingInProgress = false;
  selectedChannel.value = 0;
  showRecordingFailurePanel.value = false;
  multiActiveToasts.value = {};
  Object.keys(multiLastSeenSeq).forEach((k) => delete multiLastSeenSeq[k]);
  Object.keys(multiFrameNaturalSize).forEach((k) => delete multiFrameNaturalSize[k]);
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

const formatRecordingFailureTime = (ts) => {
  if (!ts) return '-';
  const d = new Date(ts * 1000);
  if (Number.isNaN(d.getTime())) return '-';
  return `${d.toLocaleDateString()} ${d.toLocaleTimeString()}`;
};

const getRecordingFailureReasonText = (reason) => {
  const map = {
    open_failed: '录制器启动失败',
    open_exception: '录制器启动异常',
    write_failed: '写入失败/通道失效',
    write_exception: '写入异常',
  };
  return map[reason] || reason || '未知异常';
};

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

const drawFrameToCanvas = (ch, jpegData) => {
  const canvas = multiVideoCanvasRefs[ch];
  if (!canvas) return;
  const blob = new Blob([jpegData], { type: 'image/jpeg' });
  const url = URL.createObjectURL(blob);
  const img = new Image();
  img.onload = () => {
    const parent = canvas.parentElement;
    if (parent) {
      canvas.width = parent.clientWidth;
      canvas.height = parent.clientHeight;
    }
    multiFrameNaturalSize[ch] = { w: img.naturalWidth, h: img.naturalHeight };
    const ctx = canvas.getContext('2d');
    const cw = canvas.width, ch2 = canvas.height;
    const scale = Math.min(cw / img.naturalWidth, ch2 / img.naturalHeight);
    const dw = img.naturalWidth * scale;
    const dh = img.naturalHeight * scale;
    const dx = (cw - dw) / 2;
    const dy = (ch2 - dh) / 2;
    ctx.fillStyle = '#000';
    ctx.fillRect(0, 0, cw, ch2);
    ctx.drawImage(img, dx, dy, dw, dh);
    URL.revokeObjectURL(url);
  };
  img.src = url;
};

const stopMultiStreams = () => {
  multiStreamRunning = false;
  Object.values(multiStreamAborts).forEach(a => { try { a.abort(); } catch {} });
  Object.keys(multiStreamAborts).forEach(k => delete multiStreamAborts[k]);
};

const processChannelResult = (ch, d) => {
  const chData = multiChannelData.value[ch] || {};
  chData.isRunning = d.is_running;
  chData.isDetecting = d.is_detecting;
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
  chData.detections = d.detections || [];
  chData.currentCycleSteps = d.current_cycle_steps || [];
  chData.backupCoveredLabels = d.backup_covered_labels || [];
  chData.stepCounts = d.step_counts || {};
  chData.recentEvents = d.recent_events || [];
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
    const sopSteps = stepsConf
      .filter(s => s.enabled !== false && !s.is_backup && !s.hide_in_view && _trkAllow(s.label))
      .map(s => {
        const inCycle = chData.currentCycleSteps.includes(s.label);
        const coveredByBackup = chData.backupCoveredLabels.includes(s.label);
        const trackHit = _trackHit(s.label);
        const rawB64 = screenshots[s.label];
        return {
          name: s.displayLabel || s.label,
          label: s.label,
          status: (inCycle || coveredByBackup || trackHit) ? 'completed' : 'pending',
          screenshot: rawB64 ? `data:image/jpeg;base64,${rawB64}` : null,
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
      showMultiToast(ch, toastId, event.event_name, event.reason);

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

  multiChannelData.value[ch] = { ...chData };

  const canvas = multiCanvasRefs[ch];
  if (canvas && d.detections?.length) {
    drawMultiDetections(ch, canvas, d.detections, chData._hiddenLabels, d.project_config);
  } else if (canvas) {
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, canvas.width, canvas.height);
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
            .catch(() => {})
        );
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

  detections.forEach(det => {
    if (det.hidden) return;
    if (hiddenLabels && det.label && hiddenLabels.has(det.label)) return;
    if (!shouldDrawDetWithStepRoi(det, stepsConfMulti, pipeMulti)) return;
    const cb = clipNormalizedBox(det);
    const x = cb.x * dw + dx, y = cb.y * dh + dy;
    const w = cb.w * dw, h = cb.h * dh;
    const color = pickDetColor(det, stepsConfMulti, '#10b981', '#ef4444');
    if (det.mask && Array.isArray(det.mask) && det.mask.length > 2) {
      ctx.beginPath();
      det.mask.forEach((pt, i) => {
        const mxN = Math.max(0, Math.min(1, Number(pt[0]) || 0));
        const myN = Math.max(0, Math.min(1, Number(pt[1]) || 0));
        const mx = mxN * dw + dx, my = myN * dh + dy;
        if (i === 0) ctx.moveTo(mx, my); else ctx.lineTo(mx, my);
      });
      ctx.closePath();
      ctx.fillStyle = color + '40';
      ctx.fill();
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      ctx.stroke();
    } else {
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      ctx.strokeRect(x, y, w, h);
    }
    ctx.font = 'bold 11px Arial';
    const label = det.display_id || det.display_name || det.label || '';
    ctx.fillStyle = color;
    ctx.fillRect(x, y - 14, ctx.measureText(label).width + 6, 14);
    ctx.fillStyle = 'white';
    ctx.fillText(label, x + 3, y - 2);
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
  if (!chProj) {
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
      ElMessage.success(
        `工位 ${ch + 1} 检测已启动 (主 + ${specs.length - 1} 个副模型)`
      );
    } else {
      await apiStartDetection(mainPath, 0.25, 0.45, ch, _sessionId);
      ElMessage.success(`工位 ${ch + 1} 检测已启动`);
    }
  } catch (e) {
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
  if (!(await confirmScanPairBeforeStop(ch))) return;
  try {
    await pauseDetection(ch);
    if (multiChannelData.value[ch]) multiChannelData.value[ch].isDetecting = false;
    updateGlobalDetectingState();
    ElMessage.info(`工位 ${ch + 1} 已停止`);
  } catch (e) {
    ElMessage.error(`工位 ${ch + 1} 停止失败`);
  }
};

const standbyForChannel = async (ch) => {
  if (!(await confirmScanPairBeforeStop(ch))) return;
  try {
    await standbyDetection(ch);
    if (multiChannelData.value[ch]) multiChannelData.value[ch].isDetecting = false;
    updateGlobalDetectingState();
    ElMessage.info(`工位 ${ch + 1} 已待机`);
  } catch (e) {
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
      initMultiChannelData(count);
      startMultiStreams(count);
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

// v3.7.2 (FIX-381-B): 单条 detection 的检测框颜色优先级.
//   1) 副模型 display_color (model_name != 'main' 且后端注入了 display_color)
//   2) steps_config 里该 label 配置的 box_color
//   3) 全局 OK / NG 兜底 (来自 systemStore.detection 或 multi 版本的硬编码)
const pickDetColor = (det, stepsConfig, fallbackOK, fallbackNG) => {
  if (det && det.model_name && det.model_name !== 'main' && det.display_color) {
    return det.display_color;
  }
  if (stepsConfig && det && det.label) {
    const cfg = stepsConfig.find(s => s && s.label === det.label);
    if (cfg && cfg.box_color) return cfg.box_color;
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
  // onStreamReady(bg) will do the actual swap when the first frame arrives
};

// Kept as alias so existing call-sites (start/stop/resume) still work
const forceReconnectStream = () => connectStream();

const onStreamReady = (idx) => {
  streamErrorCount = 0;
  streamConnectAttempts = 0;
  isStreaming.value = true;
  streamBackendMismatchSince = 0;
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
  if (idx !== activeStream.value) return;
  streamErrorCount++;
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

// 操作员选择
const operatorList = ref([]);
const currentOperatorId = ref(null);
async function loadOperatorList() {
  try {
    const { data } = await getOperators({ active: true });
    operatorList.value = data || [];
    const { data: cur } = await getCurrentOperator(selectedChannel.value);
    if (cur?.operator) currentOperatorId.value = cur.operator.id;
  } catch { /* ignore */ }
}
async function onOperatorChange(opId) {
  try {
    await setCurrentOperator({ channel_id: selectedChannel.value, operator_id: opId || null });
    if (opId) {
      const op = operatorList.value.find(o => o.id === opId);
      if (op) {
        systemStore.display.inspectorName = op.name;
        localStorage.setItem('display_settings', JSON.stringify(systemStore.display));
      }
    }
  } catch { /* ignore */ }
}

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
  allCounters.forEach(counter => {
    if (!DEFAULT_COUNTERS.includes(counter.name)) {
      customCounters.push(counter);
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
  speechQueue.push({ text, volume: det.voiceVolume ?? 1.0 });
  if (!isSpeaking) _playNext();
};

// 显示提示框（新版：根据 toast_id 获取配置）
const showToastById = (toastId, eventName, reason = '') => {
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

const showMultiToast = (ch, toastId, eventName, reason = '') => {
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

// 格式化步骤检测时间（PT）— 由 ptAggregate × ptMode 共同决定数据源
//   ptAggregate:
//     sum  → 同步骤一周期内多次出现按 SUM 合并（默认 v3.5.x）
//     last → 仅取最后一段（旧行为）
//   ptMode:
//     avg     → 历史平均
//     last    → 最近一个已结束 cycle 的取值
//     current → 当前正在跑 cycle 的取值
const formatStepPT = (stepLabel) => {
  const mode = systemStore.display?.monitor?.ptMode || 'avg';
  const agg = systemStore.display?.monitor?.ptAggregate || 'sum';
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
  const duration = src ? src[stepLabel] : undefined;
  if (duration === undefined || duration === null) return '--';
  return `${duration.toFixed(1)}s`;
};

// 格式化步骤间隔时间
const formatInterval = (stepLabel) => {
  const interval = stepIntervals.value[stepLabel];
  if (interval === undefined || interval === null || interval === 0) return '--';
  return `${interval.toFixed(1)}s`;
};

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

// SOP卡片整体样式：完成=绿色，漏检/重复=红色
const getSopCardClass = (step) => {
  if (step.cycleResult === 'ng') {
    return 'border-red-500 bg-red-900/30';
  } else if (step.status === 'completed' || step.cycleResult === 'ok') {
    return 'border-green-500 bg-green-900/20';
  } else if (step.status === 'active') {
    return 'border-cyan-500 shadow-[0_0_10px_rgba(6,182,212,0.3)] bg-slate-800';
  } else {
    return 'border-slate-700 bg-slate-800 opacity-60';
  }
};

const getSopHeaderClass = (step) => {
  if (step.cycleResult === 'ng') {
    return 'bg-red-900/60 text-red-200';
  } else if (step.status === 'completed' || step.cycleResult === 'ok') {
    return 'bg-green-900/60 text-green-200';
  } else {
    return 'bg-slate-950 text-gray-300';
  }
};

const getSopBodyClass = (step) => {
  if (step.cycleResult === 'ng') {
    return 'bg-red-950/30';
  } else if (step.status === 'completed' || step.cycleResult === 'ok') {
    return 'bg-green-950/20';
  } else {
    return 'bg-black/20';
  }
};

// SOP 卡片自动滚动：将正在变化的卡片滚动到可视区域中间
const scrollToSopCard = (idx) => {
  if (idx === lastScrolledIdx || idx < 0) return;
  const container = sopScrollContainer.value;
  const card = sopCardRefs[idx];
  if (!container || !card) return;
  
  const containerWidth = container.clientWidth;
  const cardLeft = card.offsetLeft;
  const cardWidth = card.offsetWidth;
  const targetScroll = cardLeft - (containerWidth / 2) + (cardWidth / 2);
  
  container.scrollTo({ left: Math.max(0, targetScroll), behavior: 'smooth' });
  lastScrolledIdx = idx;
};

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
const resizeCanvas = () => {
  if (!videoElement.value || !detectionCanvas.value) return;
  
  const video = videoElement.value;
  const canvas = detectionCanvas.value;
  
  canvas.width = video.offsetWidth;
  canvas.height = video.offsetHeight;
};

// 绘制检测框
const drawDetections = (detections) => {
  if (!detectionCanvas.value) return;
  
  const canvas = detectionCanvas.value;
  const ctx = canvas.getContext('2d');
  
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  
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
    
    // v3.7.2 (FIX-381-B): 颜色优先级 副模型 display_color > 步骤 box_color > OK/NG 兜底.
    // 历史行为: 仅主模型按 NG/OK 配色, 副模型用其独立 display_color.
    // 新增: steps_config[*].box_color 可让客户给单个 label 独立配色.
    const color = pickDetColor(det, stepsConfig, boxColor, ngColor);

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
  steps.value = stepsToShow.map((s, idx) => ({
    id: s.id,
    name: s.displayLabel || s.label,
    label: s.label,
    status: 'pending',
    result: null,
    cycleResult: null,
    screenshot: null
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
  if (!currentProject.value) {
    ElMessage.warning('请先选择项目');
    return;
  }
  if (isOperating.value) return;
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
    ElMessage.success(sessionName.value ? `检测已开始（会话 ID: ${sessionName.value}）` : '检测已开始');
    startPolling();
    
  } catch (err) {
    console.error('启动检测失败:', err);
    ElMessage.error('启动检测失败: ' + (err.response?.data?.detail || err.message));
  } finally {
    isOperating.value = false;
  }
};

const stopDetectionHandler = async () => {
  if (isOperating.value) return;
  // v3.3.0 码-码闭环: 单工位 stop 也走同一确认流程; 多工位时仅检查当前选中
  if (!(await confirmScanPairBeforeStop(selectedChannel.value || 0))) return;
  isOperating.value = true;
  try {
    await pauseDetection();
    isRunning.value = false;
    isDetecting.value = false;
    isPaused.value = true;
    projectStore.setRunningStatus(false);
    stopPolling();
    disconnectStream();
    ElMessage.info('已停止：画面和检测都已暂停');
  } catch (err) {
    console.error('停止检测失败:', err);
    ElMessage.error('停止失败: ' + (err.response?.data?.detail || err.message));
  } finally {
    isOperating.value = false;
  }
};

// 步骤截图
const stepScreenshots = ref({});

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

// 步骤间隔时间
const stepIntervals = ref({});

// Periodic double-buffer swap to release Chromium native decoder memory
let streamSwapCounter = 0;
const STREAM_SWAP_INTERVAL = 600; // every ~5 min (600 x 500ms polling)
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
      const res = await getDetectionResults();
      const data = res.data;
      
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
      if (data.step_durations) {
        Object.assign(stepDurations.value, data.step_durations);
      }
      if (data.avg_step_durations) {
        Object.assign(avgStepDurations.value, data.avg_step_durations);
      }
      // v3.5.x: PT 合并档 (cycle SUM) — 后端无对应 label 时,
      // cycle_sum_step_durations 用整体替换以反映"周期切换后已重置"的真实状态;
      // last/avg 用 merge 保持 label 累积式可读, 与 last_step_durations 风格一致.
      if (data.cycle_sum_step_durations !== undefined) {
        cycleSumStepDurations.value = data.cycle_sum_step_durations || {};
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
      // 静默处理轮询错误
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
    // 新周期产生，延迟后重置视觉状态，让用户看到上一轮的颜色反馈
    if (cycleResetTimer) {
      clearTimeout(cycleResetTimer);
      cycleResetTimer = null;
    }
    cycleResetTimer = setTimeout(() => {
      cycleResetTimer = null;
      if (!monitorMounted) return;
      steps.value.forEach(s => {
        s.status = 'pending';
        s.cycleResult = null;
      });
      tableData.value.forEach(t => {
        t.status = 'pending';
        t.cycleResult = null;
      });
      lastScrolledIdx = -1;
    }, 1200);

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

    // 设置每个步骤的 cycleResult
    steps.value.forEach((step, idx) => {
      const label = step.label || step.name;
      const cycleCount = (actualPosByLabel[label] || []).length;
      const expCnt = expectedCounter[label] || 0;

      const isCoveredByBackup = backupCoveredLabels.has(label);
      const thisPosCompleted = completedByPos[idx];

      if (cycleCount > expCnt && thisPosCompleted) {
        // 期望出现 N 次 actual > N 次 → 真正的"超额重复" (只在已完成位置标 NG)
        step.cycleResult = 'ng';
      } else if (thisPosCompleted) {
        step.cycleResult = outOfOrderIdx.has(idx) ? 'ng' : 'ok';
      } else if (isCoveredByBackup) {
        step.cycleResult = 'ok';  // 替补覆盖
      } else if (idx < maxCompletedIdx) {
        step.cycleResult = 'ng';  // 漏做: 此位置之后已有更靠后位置完成
      } else {
        step.cycleResult = null;  // 还没轮到
      }

      // 设置 status — 仅"这个位置"完成才算 completed
      if (detectingLabels.has(label) && !thisPosCompleted) {
        // 正在检测中且本位置尚未完成 → active
        step.status = 'active';
      } else if (thisPosCompleted || isCoveredByBackup) {
        step.status = 'completed';
      } else {
        step.status = 'pending';
      }

      // 同步表格状态
      if (tableData.value[idx]) {
        tableData.value[idx].count = stepCounts[label] || 0;
        tableData.value[idx].status = (thisPosCompleted || isCoveredByBackup) ? 'completed' : 'pending';
        tableData.value[idx].cycleResult = step.cycleResult;
      }
    });
    
    // 自动滚动到最新变化的卡片
    let latestChangedIdx = -1;
    for (let i = steps.value.length - 1; i >= 0; i--) {
      if (steps.value[i].cycleResult === 'ok' || steps.value[i].cycleResult === 'ng') {
        latestChangedIdx = i;
        break;
      }
    }
    if (latestChangedIdx >= 0) {
      scrollToSopCard(latestChangedIdx);
    }
  } else {
    // 周期未开始或已结束：不改变 cycleResult（保留上一轮的视觉反馈直到 reset）
    steps.value.forEach((step, idx) => {
      const label = step.label || step.name;
      const count = stepCounts[label] || 0;
      
      if (detectingLabels.has(label)) {
        step.status = 'active';
      } else if (!step.cycleResult && count > 0 && currentCycleSteps.length === 0) {
        step.status = 'completed';
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
  if (isOperating.value) return;
  if (!(await confirmScanPairBeforeStop(selectedChannel.value || 0))) return;
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
    ElMessage.info('已待机：检测停止，画面继续');
  } catch (err) {
    console.error('待机失败:', err);
    ElMessage.error('待机失败: ' + (err.response?.data?.detail || err.message));
  } finally {
    isOperating.value = false;
  }
};

const resetCounters = async () => {
  if (!currentProject.value?.counters_config) return;
  
  // 先重置后端统计数据（关键！必须先重置后端，否则轮询会覆盖前端数据）
  try {
    await resetDetectionStats();
  } catch (e) {
    console.error('重置后端统计失败:', e);
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
  lastScrolledIdx = -1;
  
  // 清空步骤截图缓存、PT/间隔缓存和NG排名
  stepScreenshots.value = {};
  stepDurations.value = {};
  avgStepDurations.value = {};
  // v3.5.x: PT 合并档同步清空
  cycleSumStepDurations.value = {};
  lastCycleSumStepDurations.value = {};
  avgCycleSumStepDurations.value = {};
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
  try {
    await resetDetectionStats(ch);
  } catch (e) {
    console.error(`Ch${ch} 重置后端统计失败:`, e);
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
    if (channelCount.value > 1) {
      showMultiToast(selectedChannel.value, toastId, event.name, event.custom_text);
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

onMounted(() => {
  monitorMounted = true;
  systemStore.loadSettings();
  fetchChannelCount();
  loadExtraFieldsSchema();
  loadOperatorList();
  scannerDisableStore.loadStatus();
  
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
      forceReconnectStream();
    }
    
    if (!res.data.source_type) {
      await autoRestoreSource();
      if (!monitorMounted) return;
    } else if (res.data.source_type && !res.data.is_running) {
      forceReconnectStream();
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
