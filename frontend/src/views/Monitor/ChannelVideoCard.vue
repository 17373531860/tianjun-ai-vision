<template>
  <!-- ==================== 多通道单工位视频卡片（2026-07 拆分批次 M-4 自 index.vue 外置） ====================
       双/四工位共用的"一个工位一张卡"：双层画布（下层视频流 + 上层检测框覆盖）
       + 工位/项目名角标 + 运行状态角标 + 底部统计条（总/OK/NG/多模型fps/FPS）。
       ⚠️ 不变量 7 边界（改前必读）:
       - 卡片只是"壳"。视频流机制(多通道 fetch-MJPEG 解析/framePump 背压/paintToCanvas)
         与检测框绘制全部留在父级 index.vue, 本组件不碰任何流/绘制逻辑, 也不自起轮询。
       - 两块 canvas 经函数 props(registerVideoCanvas/registerOverlayCanvas)回注父级的
         per-channel 画布字典, 与原模板 :ref 回调同构（el 为空不回调, 卸载不清字典,
         字典由父级 set_channel_count 重建时整体覆盖 — 与拆分前行为一致）。
       - compact=false 双工位档 / compact=true 四工位档, 两档文案与间距逐字保真, 勿合并"优化"。
       - 四工位卡内 MES 迷你条 + per-工位 Toast 依赖父级十几个函数, 走默认插槽留在调用点,
         本组件根节点是它们的 absolute 定位上下文。 -->
  <div class="relative bg-black border-2 rounded-lg overflow-hidden min-h-0"
    :class="selected ? (compact ? 'border-cyan-500 shadow-[0_0_8px_rgba(6,182,212,0.4)]' : 'border-cyan-500') : (compact ? 'border-slate-700 hover:border-slate-500' : 'border-slate-700')"
    @click="$emit('select')">
    <canvas :ref="onVideoCanvas" class="absolute inset-0 w-full h-full"></canvas>
    <canvas :ref="onOverlayCanvas" class="absolute inset-0 w-full h-full pointer-events-none"></canvas>
    <div class="absolute bg-slate-900/80 text-white px-2 py-0.5 rounded font-bold"
      :class="compact ? 'top-1 left-1 text-[0.625rem]' : 'top-1.5 left-1.5 text-xs'">
      <template v-if="compact">工位{{ ch + 1 }}</template>
      <template v-else>工位 {{ ch + 1 }}</template>
      <span v-if="chData?.projectName" class="text-cyan-400" :class="compact ? 'ml-0.5' : 'ml-1'">{{ chData.projectName }}</span>
    </div>
    <div class="absolute rounded text-[0.625rem] font-bold"
      :class="[
        compact ? 'top-1 right-1 px-1.5 py-0.5' : 'top-1.5 right-1.5 px-2 py-0.5',
        chData?.isDetecting ? 'bg-green-600/90 text-white animate-pulse' : chData?.isRunning ? 'bg-yellow-600/90 text-white' : 'bg-gray-600/90 text-white',
      ]">
      {{ chData?.isDetecting ? '检测中' : chData?.isRunning ? '待机' : '停止' }}
    </div>
    <!-- 底部统计条: 双工位带 backdrop-blur + 模型名, 四工位紧凑仅色块+fps -->
    <div v-if="!compact" class="absolute bottom-0 left-0 right-0 bg-black/70 backdrop-blur-sm px-2 py-1 flex gap-3 text-xs items-center">
      <span class="text-white font-mono">总: <span class="text-cyan-400 font-bold">{{ chData?.total ?? 0 }}</span></span>
      <span class="text-white font-mono">OK: <span class="text-green-400 font-bold">{{ chData?.ok ?? 0 }}</span></span>
      <span class="text-white font-mono">NG: <span class="text-red-400 font-bold">{{ chData?.ng ?? 0 }}</span></span>
      <!-- feat/multi-model-roi-link b1: 多通道副模型 fps 快照 (>=2 个 slot 时显示) -->
      <template v-if="(modelStats || []).length >= 2">
        <span class="text-gray-500">|</span>
        <span v-for="m in modelStats" :key="m.name"
              class="flex items-center gap-1 text-[0.6875rem]" :title="`${m.name} (${m.model_loaded ? '已加载' : '未加载'})`">
          <span class="w-2 h-2 rounded-sm flex-shrink-0" :style="{ backgroundColor: m.display_color || '#10b981' }"></span>
          <span class="text-gray-400">{{ m.name }}</span>
          <span class="text-cyan-400 font-mono">{{ m.fps_inference || 0 }}</span>
        </span>
      </template>
      <span class="ml-auto text-gray-400">FPS: {{ chData?.fps ?? 0 }}</span>
    </div>
    <div v-else class="absolute bottom-0 left-0 right-0 bg-black/70 px-2 py-1 flex gap-3 text-[0.625rem] items-center">
      <span class="text-white font-mono">总:<span class="text-cyan-400 font-bold">{{ chData?.total ?? 0 }}</span></span>
      <span class="text-white font-mono">OK:<span class="text-green-400 font-bold">{{ chData?.ok ?? 0 }}</span></span>
      <span class="text-white font-mono">NG:<span class="text-red-400 font-bold">{{ chData?.ng ?? 0 }}</span></span>
      <!-- feat/multi-model-roi-link b1: 4 工位空间紧, 仅色块+fps 数字 -->
      <template v-if="(modelStats || []).length >= 2">
        <span v-for="m in modelStats" :key="m.name"
              class="flex items-center gap-0.5 text-[0.5625rem]"
              :title="`${m.name} ${m.fps_inference || 0}fps ${m.model_loaded ? '' : '(未加载)'}`">
          <span class="w-1.5 h-1.5 rounded-sm flex-shrink-0" :style="{ backgroundColor: m.display_color || '#10b981' }"></span>
          <span class="text-cyan-400 font-mono">{{ m.fps_inference || 0 }}</span>
        </span>
      </template>
      <span class="ml-auto text-gray-400">FPS:{{ chData?.fps ?? 0 }}</span>
    </div>
    <!-- 四工位: MES 迷你条 + per-工位 Toast 由父级经默认插槽塞进来, 定位上下文=本卡片 -->
    <slot />
  </div>
</template>

<script setup>
const props = defineProps({
  // 0-based 通道号（仅角标文案用, 流按通道路由在父级完成）
  ch: { type: Number, required: true },
  // 父级轮询维护的该通道数据切片 multiChannelData[ch]（isDetecting/isRunning/projectName/total/ok/ng/fps）
  chData: { type: Object, default: null },
  // per-channel 多模型 fps 快照 channelModelStats[ch]
  modelStats: { type: Array, default: () => [] },
  // 选中态（边框高亮）, 切换走 select emit 由父级改 selectedChannel
  selected: { type: Boolean, default: false },
  // false=双工位档(大字号/带模型名) true=四工位档(紧凑/仅色块)
  compact: { type: Boolean, default: false },
  // 画布回注: 父级把 per-channel 画布字典的写入函数传进来, 与原模板 :ref 回调同构
  registerVideoCanvas: { type: Function, required: true },
  registerOverlayCanvas: { type: Function, required: true },
});
defineEmits(['select']);

// 与原 index.vue 模板 :ref="el => { if (el) refs[ch] = el }" 逐字同构: el 为空不回调
const onVideoCanvas = (el) => { if (el) props.registerVideoCanvas(el); };
const onOverlayCanvas = (el) => { if (el) props.registerOverlayCanvas(el); };
</script>
