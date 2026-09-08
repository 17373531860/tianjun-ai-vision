<template>
  <!-- ==================== 任务类型与逻辑模式侧栏（2026-08 拆分批次 自 index.vue 外置；显隐 v-if 留父级） ==================== -->
  <div class="h-1/2 bg-slate-900 rounded-lg border border-slate-700 flex flex-col overflow-hidden">
    <div class="p-3 border-b border-slate-800 bg-slate-950/50 font-bold text-white">任务类型与逻辑模式</div>
    <div class="flex-1 overflow-y-auto p-4 custom-scrollbar">
      <!-- Task Type Selector -->
      <div class="mb-4 p-3 bg-slate-800 rounded border border-slate-700">
        <span class="text-sm font-bold text-cyan-400 block mb-2">任务类型</span>
        <div class="flex gap-3">
          <label class="flex items-center gap-2 cursor-pointer px-3 py-1.5 rounded transition-colors" :class="project.task_type === 'detection' ? 'bg-cyan-600/20 border border-cyan-500' : 'bg-slate-700 border border-slate-600 hover:border-cyan-500/50'">
            <input type="radio" v-model="project.task_type" value="detection" class="accent-cyan-500">
            <span class="text-sm text-white">目标检测</span>
          </label>
          <label class="flex items-center gap-2 cursor-pointer px-3 py-1.5 rounded transition-colors" :class="project.task_type === 'segmentation' ? 'bg-cyan-600/20 border border-cyan-500' : 'bg-slate-700 border border-slate-600 hover:border-cyan-500/50'">
            <input type="radio" v-model="project.task_type" value="segmentation" class="accent-cyan-500">
            <span class="text-sm text-white">图像分割</span>
          </label>
        </div>
        <p class="text-[11px] text-gray-500 mt-1">{{ project.task_type === 'segmentation' ? '使用实例分割模型，提供像素级轮廓' : '使用目标检测模型，提供边界框' }}。跟踪模式下自动适配。</p>
      </div>

      <!-- Logic Mode Options -->
      <span class="text-sm font-bold text-cyan-400 block mb-2">逻辑模式</span>
      <div class="space-y-3">
        <label class="flex items-start p-3 bg-slate-800 rounded border border-slate-700 cursor-pointer hover:border-cyan-500/50 transition-colors">
          <input type="radio" v-model="project.logic_mode" value="sequential" class="mt-1 accent-cyan-500">
          <div class="ml-3 flex-1">
            <span class="font-bold text-white block">顺序模式</span>
            <span class="text-xs text-gray-400 block mt-1">必须严格按照设定顺序执行。全部完成→事件1；跳步/乱序→事件2</span>
          </div>
        </label>

        <label class="flex items-start p-3 bg-slate-800 rounded border border-slate-700 cursor-pointer hover:border-cyan-500/50 transition-colors">
          <input type="radio" v-model="project.logic_mode" value="detection" class="mt-1 accent-cyan-500">
          <div class="ml-3 flex-1">
            <span class="font-bold text-white block">检测模式</span>
            <span class="text-xs text-gray-400 block mt-1">不强制顺序，只识别目标。集齐所有启用步骤→事件1</span>
          </div>
        </label>

        <label class="flex items-start p-3 bg-slate-800 rounded border border-slate-700 cursor-pointer hover:border-cyan-500/50 transition-colors">
          <input type="radio" v-model="project.logic_mode" value="custom" class="mt-1 accent-cyan-500">
          <div class="ml-3 flex-1">
            <span class="font-bold text-white block">自定义模式</span>
            <span class="text-xs text-gray-400 block mt-1">基于顺序/检测模式，可添加自定义条件触发特定事件</span>
          </div>
        </label>

        <label class="flex items-start p-3 bg-slate-800 rounded border border-slate-700 cursor-pointer hover:border-cyan-500/50 transition-colors">
          <input type="radio" v-model="project.logic_mode" value="tracking" class="mt-1 accent-cyan-500">
          <div class="ml-3 flex-1">
            <span class="font-bold text-white block">跟踪模式</span>
            <span class="text-xs text-gray-400 block mt-1">{{ project.task_type === 'segmentation' ? '分割+跟踪' : '检测+跟踪' }}：为每个物品分配ID(A1,A2,B1...)，支持装箱清点与数量校验</span>
          </div>
        </label>

        <label class="flex items-start p-3 bg-slate-800 rounded border border-slate-700 cursor-pointer hover:border-cyan-500/50 transition-colors">
          <input type="radio" v-model="project.logic_mode" value="per_item" class="mt-1 accent-cyan-500">
          <div class="ml-3 flex-1">
            <span class="font-bold text-white block">逐件模式</span>
            <span class="text-xs text-gray-400 block mt-1">画面里有 N 个固定位置的同类物件，每件都要被某个动作覆盖一次（例：每颗螺丝都要被打/划过）。全部覆盖→事件1，超时未覆盖→事件2</span>
          </div>
        </label>

        <label class="flex items-start p-3 bg-slate-800 rounded border border-slate-700 cursor-pointer hover:border-cyan-500/50 transition-colors">
          <input type="radio" v-model="project.logic_mode" value="weighing" class="mt-1 accent-cyan-500">
          <div class="ml-3 flex-1">
            <span class="font-bold text-white block">称重投料模式</span>
            <span class="text-xs text-gray-400 block mt-1">连接电子秤，按型号给每道料(如钢帽/钢脚水泥)设标准量。放件自动去皮→投料→对比标准量，缺料/超量报警，逐件记录。需先选人员/型号。配置在「称重配置」页签</span>
          </div>
        </label>

        <label class="flex items-start p-3 bg-slate-800 rounded border border-slate-700 cursor-pointer hover:border-cyan-500/50 transition-colors">
          <input type="radio" v-model="project.logic_mode" value="region_events" class="mt-1 accent-cyan-500">
          <div class="ml-3 flex-1">
            <span class="font-bold text-white block">区域事件模式</span>
            <span class="text-xs text-gray-400 block mt-1">模型只检测"物"（工具/工件/手），动作由时序规则判定：工具与工件重叠 N 帧→事件（如测硬度/扫码），对象出区消失→结算周期（如下工件）。规则在「逻辑设置」页签配置</span>
          </div>
        </label>

        <label class="flex items-start p-3 bg-slate-800 rounded border border-slate-700 cursor-pointer hover:border-cyan-500/50 transition-colors">
          <input type="radio" v-model="project.logic_mode" value="ocr" class="mt-1 accent-cyan-500">
          <div class="ml-3 flex-1">
            <span class="font-bold text-white block">OCR 读字模式</span>
            <span class="text-xs text-gray-400 block mt-1">无需检测模型：按设定间隔读取画面指定区域文字（序列号/批次号/标签），文本稳定后按匹配规则判定→事件1/事件2。规则在「逻辑设置」页签配置</span>
          </div>
        </label>

        <label class="flex items-start p-3 bg-slate-800 rounded border border-slate-700 cursor-pointer hover:border-cyan-500/50 transition-colors">
          <input type="radio" v-model="project.logic_mode" value="anomaly" class="mt-1 accent-cyan-500">
          <div class="ml-3 flex-1">
            <span class="font-bold text-white block">异常检测模式</span>
            <span class="text-xs text-gray-400 block mt-1">无需检测模型：先用好样本在「AI 能力试用」页建记忆库，运行时按间隔对画面打分，连续超阈值→事件2 报警（带冷却）。适合缺陷样本稀缺的表面质检。配置在「逻辑设置」页签</span>
          </div>
        </label>
      </div>
    </div>
  </div>
</template>

<script setup>
defineProps({
  project: { type: Object, required: true },
});
</script>

<style scoped>
.custom-scrollbar::-webkit-scrollbar {
  width: 6px;
  height: 6px;
}
.custom-scrollbar::-webkit-scrollbar-track {
  background: #1e293b;
}
.custom-scrollbar::-webkit-scrollbar-thumb {
  background: #475569;
  border-radius: 3px;
}
.custom-scrollbar::-webkit-scrollbar-thumb:hover {
  background: #64748b;
}
</style>
