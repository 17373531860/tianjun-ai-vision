<template>
  <!-- ==================== 称重投料模式专属配置（v3.31, 2026-07 自 index.vue 外置） ==================== -->
  <div v-if="project.pipeline_config && project.pipeline_config.weighing"
       class="h-full overflow-y-auto p-4 pb-32 custom-scrollbar space-y-6">

    <!-- 前置要求 -->
    <el-card shadow="never" class="bg-slate-800 border-slate-700">
      <template #header><span class="font-bold text-white">前置要求</span></template>
      <div class="space-y-3 text-sm text-gray-200">
        <div class="flex items-center justify-between">
          <span>开始前必须先选操作人员</span>
          <el-switch v-model="project.pipeline_config.weighing.require_operator" />
        </div>
        <div class="flex items-center justify-between">
          <span>开始前必须先选水泥型号</span>
          <el-switch v-model="project.pipeline_config.weighing.require_model" />
        </div>
        <div class="flex items-center justify-between">
          <span>本件完成后自动给秤置零</span>
          <el-switch v-model="project.pipeline_config.weighing.auto_zero_after_done" />
        </div>
      </div>
    </el-card>

    <!-- 料别顺序 -->
    <el-card shadow="never" class="bg-slate-800 border-slate-700">
      <template #header>
        <div class="flex items-center justify-between">
          <span class="font-bold text-white">料别（投料顺序）</span>
          <span class="text-xs text-gray-400">按顺序投放，每道料分别去皮+称量+判定</span>
        </div>
      </template>
      <div class="flex flex-wrap gap-2 items-center">
        <el-tag
          v-for="(mat, idx) in project.pipeline_config.weighing.materials"
          :key="idx"
          closable
          type="info"
          @close="removeWeighingMaterial(idx)">
          {{ idx + 1 }}. {{ mat }}
        </el-tag>
        <el-input
          v-model="newWeighingMaterial"
          size="small"
          style="width: 160px"
          placeholder="新料别名"
          @keyup.enter="addWeighingMaterial" />
        <el-button size="small" type="primary" @click="addWeighingMaterial">添加料别</el-button>
      </div>
    </el-card>

    <!-- 型号标准量表 -->
    <el-card shadow="never" class="bg-slate-800 border-slate-700">
      <template #header>
        <div class="flex items-center justify-between">
          <span class="font-bold text-white">型号标准量表（kg）</span>
          <div class="flex items-center gap-2">
            <el-input v-model="newWeighingModel" size="small" style="width: 160px" placeholder="新型号名" @keyup.enter="addWeighingModel" />
            <el-button size="small" type="primary" @click="addWeighingModel">添加型号</el-button>
          </div>
        </div>
      </template>
      <div v-if="!weighingModelNames.length" class="text-gray-400 text-sm py-4 text-center">
        还没有型号。每个型号 = 一种水泥规格（可用视觉模型自动识别后切换），为它的每道料设置标准量与上下公差。
      </div>
      <div v-for="mname in weighingModelNames" :key="mname" class="mb-4 p-3 rounded bg-slate-900 border border-slate-700">
        <div class="flex items-center justify-between mb-2">
          <span class="font-bold text-cyan-400">{{ mname }}</span>
          <el-button size="small" type="danger" plain @click="removeWeighingModel(mname)">删除型号</el-button>
        </div>
        <table class="w-full text-sm text-gray-200">
          <thead>
            <tr class="text-gray-400 text-xs">
              <th class="text-left py-1">料别</th>
              <th class="py-1">标准量</th>
              <th class="py-1">下公差(允许少)</th>
              <th class="py-1">上公差(允许多)</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="mat in project.pipeline_config.weighing.materials" :key="mat">
              <td class="py-1">{{ mat }}</td>
              <td class="py-1 px-1"><el-input-number v-model="project.pipeline_config.weighing.models[mname][mat].standard" :min="0" :step="0.001" :precision="3" size="small" controls-position="right" style="width: 120px" /></td>
              <td class="py-1 px-1"><el-input-number v-model="project.pipeline_config.weighing.models[mname][mat].low_tol" :min="0" :step="0.001" :precision="3" size="small" controls-position="right" style="width: 120px" /></td>
              <td class="py-1 px-1"><el-input-number v-model="project.pipeline_config.weighing.models[mname][mat].high_tol" :min="0" :step="0.001" :precision="3" size="small" controls-position="right" style="width: 120px" /></td>
            </tr>
          </tbody>
        </table>
      </div>
    </el-card>

    <!-- 去皮 / 稳定判定 -->
    <el-card shadow="never" class="bg-slate-800 border-slate-700">
      <template #header><span class="font-bold text-white">去皮与稳定判定</span></template>
      <div class="grid grid-cols-2 gap-4 text-sm text-gray-200">
        <div>
          <label class="block text-gray-400 text-xs mb-1">去皮方式</label>
          <el-select v-model="project.pipeline_config.weighing.tare_mode" size="small" style="width: 100%">
            <el-option label="放件后自动去皮(稳定即去)" value="auto_stable" />
            <el-option label="仅手动去皮" value="manual" />
          </el-select>
        </div>
        <div>
          <label class="block text-gray-400 text-xs mb-1">自动去皮触发重量(kg)：放件超过此值才去皮</label>
          <el-input-number v-model="project.pipeline_config.weighing.tare_trigger_weight" :min="0" :step="0.01" :precision="3" size="small" style="width: 100%" controls-position="right" />
        </div>
        <div>
          <label class="block text-gray-400 text-xs mb-1">稳定容差(kg)：连续读数波动小于此值算稳</label>
          <el-input-number v-model="project.pipeline_config.weighing.stable_tol" :min="0" :step="0.001" :precision="3" size="small" style="width: 100%" controls-position="right" />
        </div>
        <div>
          <label class="block text-gray-400 text-xs mb-1">稳定所需连续帧数</label>
          <el-input-number v-model="project.pipeline_config.weighing.stable_min_samples" :min="1" :step="1" size="small" style="width: 100%" controls-position="right" />
        </div>
        <div>
          <label class="block text-gray-400 text-xs mb-1">最小有效投料量(kg)：低于此值不算一次投料</label>
          <el-input-number v-model="project.pipeline_config.weighing.measure_min_weight" :min="0" :step="0.001" :precision="3" size="small" style="width: 100%" controls-position="right" />
        </div>
        <div>
          <label class="block text-gray-400 text-xs mb-1">去皮稳定采样帧数</label>
          <el-input-number v-model="project.pipeline_config.weighing.tare_settle_samples" :min="1" :step="1" size="small" style="width: 100%" controls-position="right" />
        </div>
      </div>
    </el-card>

    <!-- 料别/视觉校验 + 报警事件映射 -->
    <el-card shadow="never" class="bg-slate-800 border-slate-700">
      <template #header><span class="font-bold text-white">校验与报警</span></template>
      <div class="space-y-4 text-sm text-gray-200">
        <div>
          <label class="block text-gray-400 text-xs mb-1">料别校验方式</label>
          <el-select v-model="project.pipeline_config.weighing.material_check" size="small" style="width: 100%">
            <el-option label="按投料顺序自动推进（不校验料别）" value="sequence" />
            <el-option label="视觉识别料别（模型/外部上报标签校验）" value="visual" />
            <el-option label="关闭料别校验" value="off" />
          </el-select>
        </div>
        <div class="grid grid-cols-2 gap-4">
          <div>
            <label class="block text-gray-400 text-xs mb-1">缺料 → 触发事件</label>
            <el-select v-model="project.pipeline_config.weighing.alarm_event_shortage" size="small" style="width: 100%">
              <el-option v-for="ev in project.events_config" :key="ev.id" :label="ev.name" :value="ev.id" />
            </el-select>
          </div>
          <div>
            <label class="block text-gray-400 text-xs mb-1">超量 → 触发事件</label>
            <el-select v-model="project.pipeline_config.weighing.alarm_event_over" size="small" style="width: 100%">
              <el-option v-for="ev in project.events_config" :key="ev.id" :label="ev.name" :value="ev.id" />
            </el-select>
          </div>
          <div>
            <label class="block text-gray-400 text-xs mb-1">料别错 → 触发事件</label>
            <el-select v-model="project.pipeline_config.weighing.alarm_event_wrong" size="small" style="width: 100%">
              <el-option v-for="ev in project.events_config" :key="ev.id" :label="ev.name" :value="ev.id" />
            </el-select>
          </div>
          <div>
            <label class="block text-gray-400 text-xs mb-1">前置未满足 → 触发事件</label>
            <el-select v-model="project.pipeline_config.weighing.alarm_event_precheck" size="small" style="width: 100%">
              <el-option v-for="ev in project.events_config" :key="ev.id" :label="ev.name" :value="ev.id" />
            </el-select>
          </div>
        </div>
      </div>
    </el-card>

  </div>
</template>

<script setup>
// ==================== 称重投料模式配置编辑（自 index.vue 平移） ====================
// 数据流约定（拆分原则 2）：直接原位修改父级传入的 project.pipeline_config.weighing
// 子树（与拆分前语义一致，保存仍由父级"保存"按钮统一走 updateProject），
// 不自行发请求、不另起轮询。默认值注入 ensureWeighingDefaults 留在父级加载链路。
import { ref, computed } from 'vue';
import { ElMessage } from 'element-plus';

const props = defineProps({
  project: { type: Object, required: true },
});

const newWeighingMaterial = ref('');
const newWeighingModel = ref('');

const weighingModelNames = computed(() => {
  const w = props.project?.pipeline_config?.weighing;
  return w && w.models ? Object.keys(w.models) : [];
});

// 规整: 保证每个型号对每道料都有 spec 对象, 否则模板 v-model 取不到会报错
const normalizeWeighingSpecs = () => {
  const w = props.project?.pipeline_config?.weighing;
  if (!w) return;
  if (!Array.isArray(w.materials)) w.materials = [];
  if (!w.models || typeof w.models !== 'object') w.models = {};
  Object.keys(w.models).forEach(mname => {
    if (!w.models[mname] || typeof w.models[mname] !== 'object') w.models[mname] = {};
    w.materials.forEach(mat => {
      if (!w.models[mname][mat]) {
        w.models[mname][mat] = { standard: 0, low_tol: 0.05, high_tol: 0.05 };
      }
    });
  });
};

const addWeighingMaterial = () => {
  const name = (newWeighingMaterial.value || '').trim();
  if (!name) return;
  const w = props.project.pipeline_config.weighing;
  if (w.materials.includes(name)) { ElMessage.warning('料别已存在'); return; }
  w.materials.push(name);
  newWeighingMaterial.value = '';
  normalizeWeighingSpecs();
};

const removeWeighingMaterial = (idx) => {
  const w = props.project.pipeline_config.weighing;
  w.materials.splice(idx, 1);
};

const addWeighingModel = () => {
  const name = (newWeighingModel.value || '').trim();
  if (!name) return;
  const w = props.project.pipeline_config.weighing;
  if (w.models[name]) { ElMessage.warning('型号已存在'); return; }
  w.models[name] = {};
  newWeighingModel.value = '';
  normalizeWeighingSpecs();
};

const removeWeighingModel = (name) => {
  const w = props.project.pipeline_config.weighing;
  delete w.models[name];
};
</script>
