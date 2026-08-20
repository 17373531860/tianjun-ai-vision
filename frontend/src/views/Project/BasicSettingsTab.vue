<template>
  <!-- ==================== 基础设置 Tab（2026-08 拆分批次 自 index.vue 外置） ====================
       弹窗状态（模型选择/格式选择/ROI 编辑）与格式转换轮询留父级，本组件只上抛打开意图；
       removeExtraModel 依赖父级 _purgeStepsByFromModel（与 selectModel 共用），同样走 emit。 -->
  <div class="h-full overflow-y-auto p-4 custom-scrollbar">
    <div class="max-w-3xl space-y-6">
      <!-- Basic Info -->
      <el-card shadow="never" class="bg-slate-800 border-slate-700 text-gray-300">
        <template #header><span class="font-bold text-white">基本信息</span></template>
        <el-form label-position="top">
          <el-form-item label="项目名称">
            <el-input v-model="project.name" />
          </el-form-item>
          <el-form-item label="任务类型">
            <el-select v-model="project.task_type" class="w-full">
              <el-option label="目标检测 (Object Detection)" value="detection" />
              <el-option label="图像分割 (Instance Segmentation)" value="segmentation" />
            </el-select>
          </el-form-item>
        </el-form>
      </el-card>

      <!-- Model Config -->
      <el-card shadow="never" class="bg-slate-800 border-slate-700 text-gray-300">
        <template #header>
          <div class="flex justify-between items-center">
            <span class="font-bold text-white">模型配置</span>
            <div class="flex gap-2">
              <el-button v-if="project.default_model_id" size="small" plain @click="$emit('open-format-select')">切换格式</el-button>
              <el-button type="primary" size="small" plain @click="$emit('open-model-select')">选择模型</el-button>
            </div>
          </div>
        </template>
        <div class="flex items-center gap-4">
          <div class="w-16 h-16 bg-slate-700 rounded flex items-center justify-center">
            <el-icon :size="24"><Cpu /></el-icon>
          </div>
          <div>
            <p class="text-white font-bold">{{ project.model_name || '未配置模型' }}<span v-if="project.model_version" class="text-gray-400 font-normal ml-2">v{{ project.model_version }}</span></p>
            <p class="text-xs text-gray-500">Labels: {{ (project.model_labels || []).length }} 个类别 · 步骤: {{ (project.steps_config || []).length }} 个
              <el-tag v-if="project.default_model_id" size="small" class="ml-2" :type="project.model_format === 'pytorch_fp32' ? 'info' : 'success'">{{ getFormatDisplayName(project.model_format || 'pytorch_fp32') }}</el-tag>
            </p>
            <div class="mt-2 flex gap-2 flex-wrap">
              <el-tag v-for="label in (project.model_labels || []).slice(0, 8)" :key="label" size="small" type="info">{{ label }}</el-tag>
              <span v-if="(project.model_labels || []).length > 8" class="text-xs text-gray-500">+{{ project.model_labels.length - 8 }}</span>
            </div>
          </div>
        </div>
      </el-card>

      <!-- Step 8 (feat/multi-model-roi-link): 附加模型 (多模型 ROI) -->
      <el-card shadow="never" class="bg-slate-800 border-slate-700 text-gray-300">
        <template #header>
          <div class="flex justify-between items-center">
            <span class="font-bold text-white">附加模型 (多模型 ROI)</span>
            <el-button type="primary" size="small" plain @click="addExtraModel"
              :disabled="(project?.extra_models?.length || 0) >= 4">
              <el-icon class="mr-1"><Plus /></el-icon>添加副模型
            </el-button>
          </div>
        </template>
        <div class="text-xs text-gray-400 mb-3 leading-relaxed">
          在主模型基础上叠加最多 4 个独立模型，每个可以指定 ROI 区域、检测频率、独立颜色。
          适合主模型管 SOP 步骤、副模型在指定区域检测产品状态等场景。
          <br />
          <span class="text-amber-400">提示</span>: 多模型同时跑会增加 GPU 显存压力，
          4-5 GB 显存建议最多 2 个 (主+1 副)；模型加载时跨通道串行 warmup，避免 OOM。
        </div>
        <div v-if="!project?.extra_models?.length" class="text-center text-gray-500 py-6 text-sm">
          暂无副模型，点击右上角"添加副模型"配置
        </div>
        <div v-else class="space-y-3">
          <div v-for="(slot, idx) in project.extra_models" :key="idx"
            class="bg-slate-900/60 border border-slate-700 rounded-lg p-3 space-y-2">
            <!-- 行 1: name + 模型 + 颜色 + 删除 -->
            <div class="flex items-center gap-3 flex-wrap">
              <div class="flex items-center gap-2">
                <span class="text-xs text-gray-400">slot 名</span>
                <el-input v-model="slot.name" size="small" style="width: 7.5rem"
                  placeholder="aux" />
              </div>
              <div class="flex items-center gap-2 flex-1 min-w-[12rem]">
                <span class="text-xs text-gray-400">模型</span>
                <span v-if="slot.model_name"
                  class="text-sm text-white truncate flex-1">
                  {{ slot.model_name }}<span v-if="slot.model_version"
                    class="text-gray-500 ml-1">v{{ slot.model_version }}</span>
                </span>
                <span v-else class="text-sm text-gray-500 flex-1">未选择</span>
                <!-- v3.7.x: 副模型格式 tag + 切换格式按钮 (与主模型对齐) -->
                <el-tag v-if="slot.model_id"
                  size="small"
                  :type="(slot.model_format || 'pytorch_fp32') === 'pytorch_fp32' ? 'info' : 'success'">
                  {{ getFormatDisplayName(slot.model_format || 'pytorch_fp32') }}
                </el-tag>
                <el-button v-if="slot.model_id" size="small" plain
                  @click="$emit('open-extra-model-format-select', idx)">
                  切换格式
                </el-button>
                <el-button size="small" plain @click="$emit('open-extra-model-select', idx)">
                  选择
                </el-button>
              </div>
              <div class="flex items-center gap-2">
                <span class="text-xs text-gray-400">颜色</span>
                <el-color-picker v-model="slot.display_color" size="small" />
              </div>
              <el-button type="danger" size="small" plain
                @click="$emit('remove-extra-model', idx)">
                <el-icon><Delete /></el-icon>
              </el-button>
            </div>
            <!-- 行 2: 置信度 (常用, 多数客户只调这个) + 高级参数折叠按钮 -->
            <div class="flex items-center gap-3 flex-wrap">
              <div class="flex items-center gap-2">
                <span class="text-xs text-gray-400">置信度</span>
                <el-input-number v-model="slot.conf" :min="0.05" :max="1"
                  :step="0.05" :precision="2" size="small"
                  style="width: 7rem" />
                <el-tooltip placement="top" effect="dark">
                  <template #content>
                    模型对一个识别有多确定. 0.25 = 至少 25% 把握才认.<br/>
                    真正卡 OK/NG 的是"步骤详情"里每个 label 的 threshold(%),<br/>
                    这里只是模型层的"地板", 一般留 0.25 即可.
                  </template>
                  <el-icon class="text-gray-500 cursor-help"><QuestionFilled /></el-icon>
                </el-tooltip>
              </div>
              <el-button size="small" text type="info" @click="slot._adv_open = !slot._adv_open">
                高级参数 {{ slot._adv_open ? '▴' : '▾' }}
              </el-button>
            </div>
            <!-- 行 2.5: 高级参数 (IoU / 优先级 / FP16), 默认折叠 -->
            <div v-show="slot._adv_open"
              class="flex items-center gap-3 flex-wrap bg-slate-900/40 rounded p-2 border border-slate-700/50">
              <div class="flex items-center gap-2">
                <span class="text-xs text-gray-400">IoU</span>
                <el-input-number v-model="slot.iou" :min="0.1" :max="1"
                  :step="0.05" :precision="2" size="small"
                  style="width: 7rem" />
                <el-tooltip placement="top" effect="dark">
                  <template #content>
                    NMS 阈值: 同一个东西被模型框了好几次时, 重叠超过此比例算同一个,<br/>
                    合并保留最好的. 0.45 = 重叠 45% 以上合并.<br/>
                    · 数字大 → 不积极合并, 可能同物多框<br/>
                    · 数字小 → 积极合并, 不同物体可能被错合<br/>
                    一般留 0.45, 出现重复框/漏框再调.
                  </template>
                  <el-icon class="text-gray-500 cursor-help"><QuestionFilled /></el-icon>
                </el-tooltip>
              </div>
              <div class="flex items-center gap-2">
                <span class="text-xs text-gray-400">优先级</span>
                <el-input-number v-model="slot.priority" :min="0" :max="100"
                  :step="10" :precision="0" size="small"
                  style="width: 7rem" />
                <el-tooltip placement="top" effect="dark">
                  <template #content>
                    多个模型同时跑时谁先抢 GPU. 主模型默认 100, 副模型默认 50.<br/>
                    副模型多到 GPU 抢不过来时才调; 单副模型留 50 不动.
                  </template>
                  <el-icon class="text-gray-500 cursor-help"><QuestionFilled /></el-icon>
                </el-tooltip>
              </div>
              <!-- v3.7.x: FP16 只对 .pt 推理生效. 选了 TensorRT/ONNX 后,
                   精度由编译文件决定, 此开关被后端无视 -> UX 上 disable. -->
              <div class="flex items-center gap-2">
                <el-tooltip placement="top" effect="dark"
                  :disabled="(slot.model_format || 'pytorch_fp32') === 'pytorch_fp32'">
                  <template #content>
                    当前格式 [{{ getFormatDisplayName(slot.model_format || 'pytorch_fp32') }}]
                    已固化精度, 这个开关无效.<br/>
                    要 FP16 推理请用上面的 "切换格式".
                  </template>
                  <span>
                    <el-checkbox v-model="slot.use_half" class="!text-gray-300"
                      :disabled="(slot.model_format || 'pytorch_fp32') !== 'pytorch_fp32'">
                      FP16
                    </el-checkbox>
                  </span>
                </el-tooltip>
                <el-tooltip placement="top" effect="dark">
                  <template #content>
                    "半精度推理": 用一半小数位算, 速度快/省显存, 精度稍降.<br/>
                    仅对 .pt (PyTorch FP32) 模型生效.<br/>
                    选了 TensorRT/PyTorch FP16 后精度已固化, 此开关失效.
                  </template>
                  <el-icon class="text-gray-500 cursor-help"><QuestionFilled /></el-icon>
                </el-tooltip>
              </div>
            </div>
            <!-- 行 3: schedule -->
            <div class="flex items-center gap-3 flex-wrap">
              <span class="text-xs text-gray-400">检测频率</span>
              <el-radio-group v-model="slot.schedule_type" size="small">
                <el-radio-button label="every_frame">每帧</el-radio-button>
                <el-radio-button label="every_n_frames">间隔 N 帧</el-radio-button>
                <el-radio-button label="on_event">按事件</el-radio-button>
              </el-radio-group>
              <el-input-number v-if="slot.schedule_type === 'every_n_frames'"
                v-model="slot.schedule_n" :min="1" :max="100" :step="1"
                :precision="0" size="small" style="width: 6rem" />
              <span v-if="slot.schedule_type === 'every_n_frames'"
                class="text-xs text-gray-500">
                (每 {{ slot.schedule_n }} 帧跑一次, 减小 GPU 占用)
              </span>
              <!-- c1+: on_event 模式下选事件 (从 events_config 拉) -->
              <el-select v-if="slot.schedule_type === 'on_event'"
                v-model="slot.schedule_events" multiple collapse-tags collapse-tags-tooltip
                size="small" style="min-width: 14rem"
                placeholder="选触发事件 (空 = 永不触发)">
                <el-option v-for="ev in (project.events_config || [])"
                  :key="ev.id" :label="`${ev.name} (id=${ev.id})`" :value="ev.id" />
              </el-select>
              <span v-if="slot.schedule_type === 'on_event' && !(slot.schedule_events?.length)"
                class="text-xs text-amber-400">
                ⚠ 未选事件, 副模型永不会跑
              </span>
            </div>
            <!-- 行 4: ROI -->
            <div class="flex items-start gap-3 flex-wrap">
              <span class="text-xs text-gray-400 mt-1.5">ROI 区域</span>
              <div class="flex flex-col gap-1">
                <div class="flex items-center gap-2">
                  <el-button size="small" type="primary" plain
                    @click="$emit('open-extra-model-roi-editor', idx)">
                    {{ slot.roi && slot.roi.length >= 3 ? '重新绘制' : '设置区域' }}
                  </el-button>
                  <el-button v-if="slot.roi && slot.roi.length >= 3"
                    size="small" type="danger" plain @click="clearExtraModelRoi(idx)">
                    清除
                  </el-button>
                  <span v-if="slot.roi && slot.roi.length >= 3"
                    class="text-xs text-green-400">
                    已设置 {{ slot.roi.length }} 个顶点
                  </span>
                  <span v-else class="text-xs text-gray-500">
                    未设置 (空 = 全画面)
                  </span>
                </div>
                <!-- b2: ROI mini preview (16:9 SVG, 192x108).
                     用 viewBox="0 0 1 1" 让归一化坐标直接当 path. -->
                <svg v-if="slot.roi && slot.roi.length >= 3"
                  width="192" height="108" viewBox="0 0 1 1"
                  preserveAspectRatio="none"
                  class="border border-slate-700 bg-slate-950 rounded">
                  <polygon
                    :points="(slot.roi || []).map(p => `${p[0]},${p[1]}`).join(' ')"
                    :fill="slot.display_color || '#f59e0b'"
                    fill-opacity="0.25"
                    :stroke="slot.display_color || '#f59e0b'"
                    stroke-width="0.005"
                    stroke-linejoin="round" />
                </svg>
              </div>
            </div>
            <!-- 行 5: class_filter (可选, 留空 = 模型全标签).
                 d2: 优先用 slot.available_labels (选模型时自动拉), 兜底显示已选 class_filter. -->
            <div class="flex items-start gap-3">
              <span class="text-xs text-gray-400 mt-1.5 w-16 shrink-0">类别白名单</span>
              <el-select v-model="slot.class_filter" multiple filterable
                allow-create default-first-option :reserve-keyword="false"
                :placeholder="slot.available_labels?.length
                  ? `留空 = 模型全部 ${slot.available_labels.length} 类`
                  : '留空 = 模型全部类别 (可手输)'"
                size="small" class="flex-1">
                <el-option v-for="lbl in extraModelSlotOptions(slot)" :key="lbl"
                  :label="lbl" :value="lbl" />
              </el-select>
            </div>
          </div>
        </div>
      </el-card>

      <!-- Shift Split Config -->
      <el-card shadow="never" class="bg-slate-800 border-slate-700 text-gray-300">
        <template #header><span class="font-bold text-white">班次拆分</span></template>
        <el-form label-position="top">
          <el-form-item>
            <div class="flex items-center gap-3">
              <el-switch v-model="project.shift_split_enabled" />
              <span class="text-sm text-gray-300">启用跨班次自动拆分会话</span>
            </div>
            <div class="text-xs text-gray-500 mt-1">开启后，检测会话在班次切换时自动结束并创建新会话（类似跨日拆分）。</div>
          </el-form-item>
          <template v-if="project.shift_split_enabled">
            <!-- v3.35.1 自定义班次列表: 每班只填开始时刻, 持续到下一班开始 (跨天自动衔接) -->
            <div v-for="(s, i) in (project.shifts || [])" :key="i"
                 class="flex items-center gap-2 mb-2">
              <el-input v-model="s.name" size="small" placeholder="班次名，如 白班" style="width: 140px" />
              <el-time-picker v-model="s.start" size="small" format="HH:mm" value-format="HH:mm"
                placeholder="开始时刻" style="width: 120px" />
              <span class="text-xs text-gray-500">{{ shiftRangeHint(i) }}</span>
              <el-button v-if="(project.shifts || []).length > 2" size="small" type="danger" plain
                @click="project.shifts.splice(i, 1)">删</el-button>
            </div>
            <el-button size="small" @click="(project.shifts = project.shifts || []).push({ name: '', start: '00:00' })">
              + 加班次
            </el-button>
            <div class="text-xs text-gray-500 mt-2">
              每班只填<b>开始时刻</b>，持续到下一班开始（按时刻排序、跨天自动衔接）；某时刻属于"最近一个已开始的班次"——如 白班08:00/晚班20:00 时，20:01 的周期归晚班。数据中心"时间段"下拉与检测中心班次显示都按这份列表走。
            </div>
          </template>
        </el-form>
      </el-card>
    </div>
  </div>
</template>

<script setup>
import { ElMessage } from 'element-plus';
import { Plus, Cpu, Delete, QuestionFilled } from '@element-plus/icons-vue';
import { getFormatDisplayName } from './modelFormats';

const props = defineProps({
  project: { type: Object, required: true },
});

defineEmits([
  'open-format-select',
  'open-model-select',
  'open-extra-model-format-select',
  'open-extra-model-select',
  'open-extra-model-roi-editor',
  'remove-extra-model',
]);

// v3.35.1 班次行提示: 本班覆盖 [本班开始, 按时刻排序的下一班开始)
const shiftRangeHint = (idx) => {
  const list = (props.project?.shifts || []).filter(s => s.start);
  const cur = props.project?.shifts?.[idx];
  if (!cur || !cur.start || list.length < 2) return '';
  const sorted = [...list].sort((a, b) => (a.start < b.start ? -1 : 1));
  const pos = sorted.findIndex(s => s === cur);
  if (pos === -1) return '';
  const next = sorted[(pos + 1) % sorted.length];
  return `覆盖 ${cur.start} ~ ${next.start}${pos === sorted.length - 1 ? '（跨天）' : ''}`;
};

// Step 8 (feat/multi-model-roi-link): 副模型管理方法
const generateExtraModelDefaultName = () => {
  const existing = new Set((props.project?.extra_models || []).map(m => m.name));
  if (!existing.has('aux')) return 'aux';
  for (let i = 2; i < 100; i++) {
    const candidate = `aux${i}`;
    if (!existing.has(candidate)) return candidate;
  }
  return `aux_${Date.now() % 10000}`;
};

const EXTRA_MODEL_PALETTE = ['#f59e0b', '#3b82f6', '#a855f7', '#ec4899', '#14b8a6', '#facc15'];

const addExtraModel = () => {
  if (!props.project) return;
  if (!props.project.extra_models) props.project.extra_models = [];
  const list = props.project.extra_models;
  if (list.length >= 4) {
    ElMessage.warning('副模型最多 4 个 (考虑 GPU 显存限制)');
    return;
  }
  list.push({
    name: generateExtraModelDefaultName(),
    model_id: null,
    model_name: '',
    model_version: '',
    model_format: 'pytorch_fp32',  // v3.7.x: 副模型推理格式 (与主模型对齐, 可切 TensorRT FP16 提速)
    conf: 0.25,
    iou: 0.45,
    roi: null,
    schedule_type: 'every_n_frames',
    schedule_n: 5,
    schedule_events: [],   // c1+: on_event 模式监听的事件 id 列表
    class_filter: [],
    available_labels: [],  // d2: 选模型后自动填充 (UI 候选列表)
    priority: 50,
    display_color: EXTRA_MODEL_PALETTE[list.length % EXTRA_MODEL_PALETTE.length],
    use_half: false,
  });
};

// d2: 计算 class_filter el-select 的候选项 = available_labels ∪ 已选 class_filter (去重保序).
// 老项目没有 available_labels 时, 至少显示已选标签让用户能看到/删除.
const extraModelSlotOptions = (slot) => {
  const set = new Set();
  const out = [];
  for (const lbl of (slot.available_labels || [])) {
    if (lbl && !set.has(lbl)) { set.add(lbl); out.push(lbl); }
  }
  for (const lbl of (slot.class_filter || [])) {
    if (lbl && !set.has(lbl)) { set.add(lbl); out.push(lbl); }
  }
  return out;
};

const clearExtraModelRoi = (idx) => {
  const slot = props.project?.extra_models?.[idx];
  if (slot) slot.roi = null;
};
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
