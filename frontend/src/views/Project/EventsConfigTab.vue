<template>
  <!-- ==================== 事件设置 Tab（2026-07 拆分批次 P-3 自 index.vue 外置） ==================== -->
  <div class="h-full overflow-y-auto p-4 pb-32 custom-scrollbar space-y-6">
    <el-card shadow="never" class="bg-slate-800 border-slate-700">
      <template #header>
        <div class="flex justify-between items-center">
          <span class="font-bold text-white">事件定义</span>
          <el-button type="primary" size="small" link @click="addEvent">+ 新增事件</el-button>
        </div>
      </template>
      <div class="space-y-4">
        <div v-for="(ev, idx) in (project.events_config || [])" :key="ev.id" class="bg-slate-900 p-4 rounded">
          <div class="flex justify-between items-start mb-3">
            <div class="flex items-center gap-2">
              <el-color-picker v-model="ev.color" size="small" />
              <input v-model="ev.name" :disabled="idx < 2" class="bg-transparent border-b border-gray-600 focus:border-cyan-500 outline-none text-white font-bold text-sm w-40" />
              <span v-if="idx < 2" class="text-[10px] bg-slate-800 px-1.5 py-0.5 rounded text-gray-400">系统预设</span>
            </div>
            <el-button v-if="idx >= 2" type="danger" link size="small" @click="removeEvent(idx)">删除</el-button>
          </div>

          <!-- Counter Actions -->
          <div class="border-t border-slate-800 pt-3">
            <p class="text-xs text-gray-500 mb-2 font-bold">计数器动作：</p>
            <div class="space-y-2">
              <div v-for="(action, aIdx) in ev.actions" :key="aIdx" class="flex items-center gap-2 text-xs bg-slate-800 p-2 rounded">
                <el-select v-model="action.counter_name" size="small" class="w-32" placeholder="选择计数器">
                  <el-option-group label="系统默认">
                    <el-option v-for="counter in defaultCounters" :key="counter.name" :label="counter.name" :value="counter.name" />
                  </el-option-group>
                  <el-option-group v-if="customCounters.length > 0" label="自定义">
                    <el-option v-for="counter in customCounters" :key="counter.name" :label="counter.name" :value="counter.name" />
                  </el-option-group>
                </el-select>
                <span class="text-gray-400">增加</span>
                <el-input-number v-model="action.delta" size="small" :min="0" :precision="2" class="w-20" controls-position="right" />
                <el-button type="danger" size="small" link @click="ev.actions.splice(aIdx, 1)">删除</el-button>
              </div>
              <el-button type="primary" size="small" link @click="addEventAction(ev)">+ 添加动作</el-button>
            </div>
          </div>

          <!-- Notification -->
          <div class="border-t border-slate-800 pt-3 mt-3">
            <div class="flex items-center gap-4 flex-wrap">
              <el-checkbox v-model="ev.show_notification" size="small">显示提示框</el-checkbox>
              <el-select v-if="ev.show_notification" v-model="ev.toast_id" size="small" class="w-40" placeholder="选择提示框">
                <el-option-group label="系统预设">
                  <el-option label="合格提示框" value="ok" />
                  <el-option label="NG提示框" value="ng" />
                </el-option-group>
                <el-option-group v-if="systemStore.detection.customToasts.length > 0" label="自定义提示框">
                  <el-option 
                    v-for="toast in systemStore.detection.customToasts" 
                    :key="toast.id" 
                    :label="toast.name" 
                    :value="toast.id" 
                  />
                </el-option-group>
              </el-select>
            </div>
          </div>

          <!-- v3.9.x 需人工确认重做 -->
          <div class="border-t border-slate-800 pt-3 mt-3">
            <div class="flex items-center gap-4 flex-wrap">
              <el-checkbox v-model="ev.require_ack" size="small">
                <span class="text-amber-300">需人工确认（重做本周期）</span>
              </el-checkbox>
              <template v-if="ev.require_ack">
                <span class="text-xs text-gray-400">超时自动确认（秒）</span>
                <el-input-number v-model="ev.ack_timeout_sec" size="small" :min="0" :step="5" :precision="0" class="w-24" controls-position="right" />
                <span class="text-xs text-gray-500">0 = 永不超时，必须手动确认</span>
              </template>
            </div>
            <div v-if="ev.require_ack" class="text-xs text-gray-500 mt-2 leading-relaxed bg-slate-950/60 rounded p-2 border-l-2 border-amber-700/50">
              触发本事件后：弹原提示框 + 弹"确认重做"对话框，<span class="text-amber-300">画面与状态机暂停</span>，工人确认后清当前周期但保留计数（OK / NG / 自定义计数器累计值不动），现场重做这一件。本机生效，不同步到集群副机。
            </div>

            <!-- v3.34 确认后保留周期 (断点补做) -->
            <div v-if="ev.require_ack" class="mt-2 flex items-center gap-3 flex-wrap">
              <el-checkbox v-model="ev.ack_keep_cycle" size="small">
                <span class="text-emerald-300">确认后保留周期（断点补做）</span>
              </el-checkbox>
            </div>
            <div v-if="ev.require_ack" class="text-xs text-gray-500 mt-2 leading-relaxed bg-slate-950/60 rounded p-2 border-l-2 border-emerald-700/50">
              勾上：工人确认后<span class="text-emerald-300">保留在制周期与已完成步骤</span>，从被打断处继续补做（典型：违序警告定格 → 确认 → 接着做漏掉的那一步，整件照常判定）。
              不勾（默认）：确认即丢弃在制周期，整件从头重做。超时自动确认遵循同一语义。
            </div>

            <!-- v3.44 反向联动明示: NG 事件的定格弹窗按钮由「NG 判定与处置」决定 -->
            <div v-if="ev.require_ack && isNgEvent(ev)"
                 class="text-xs mt-2 leading-relaxed rounded p-2 border-l-2"
                 :class="ngRemediationOn
                   ? 'text-gray-400 bg-slate-950/60 border-sky-700/50'
                   : 'text-gray-500 bg-slate-950/60 border-slate-700'">
              <template v-if="ngRemediationOn">
                ⓘ 本项目在逻辑设置「NG 判定与处置」里选了<span class="text-sky-300">定格弹窗可补做</span>档 —
                NG 定格弹窗将显示「补步骤/补数量 · 认NG · 重做」处置按钮，此时处置以按钮为准，
                上面的「确认后保留周期」只对没有补做按钮的普通确认路径生效。
              </template>
              <template v-else>
                ⓘ 想让 NG 定格弹窗里出现「补步骤/补数量—判合格」按钮？去逻辑设置「NG 判定与处置」把对应场景选为「定格弹窗」档。
              </template>
            </div>

            <!-- v3.9.x 周期性强制动作触发的事件: 确认时是否清账 -->
            <div v-if="ev.require_ack" class="mt-2 flex items-center gap-3 flex-wrap">
              <el-checkbox v-model="ev.ack_resets_periodic" size="small">
                <span class="text-cyan-300">确认时清账周期性强制动作</span>
              </el-checkbox>
            </div>
            <div v-if="ev.require_ack" class="text-xs text-gray-500 mt-2 leading-relaxed bg-slate-950/60 rounded p-2 border-l-2 border-cyan-700/50">
              仅当本事件被<span class="text-cyan-300">周期性强制动作</span>（每 N 轮 / 每 N 秒提醒一次）触发时生效。
              勾上：工人确认 = 等价于做了一次保养完成动作，对应规则计数器清零，下次重新累计到阈值才再提醒。
              不勾（默认）：仅消除阻塞，规则计数继续累加，下次到点立刻又触发（适合"按死值催办"场景）。
            </div>
          </div>
        </div>
      </div>
    </el-card>
  </div>
</template>

<script setup>
// ==================== 事件定义编辑（自 index.vue 平移） ====================
// 数据流约定（拆分原则 2）：直接原位修改父级传入的 project.events_config 数组，
// 保存仍由父级"保存配置"按钮统一走 updateProject。计数器候选（默认前 3 + 自定义）
// 由父级传入——与基础设置 Tab 共用同一份 computed，避免 slice 规则双份维护。
// 保养规则绑定事件的 addEventAndBindToRule 属于逻辑设置 Tab 链路，留在父级。
import { computed } from 'vue';
import { useSystemStore } from '@/store/useSystemStore';
import { dbg } from '@/utils/debug';

const props = defineProps({
  project: { type: Object, required: true },
  defaultCounters: { type: Array, default: () => [] },
  customCounters: { type: Array, default: () => [] },
});

const systemStore = useSystemStore();

// v3.44 反向联动明示: NG 事件(id=2)的定格弹窗按钮由逻辑设置「NG 判定与处置」决定
const isNgEvent = (ev) => ev?.id === 2 || String(ev?.id) === '2';
const ngRemediationOn = computed(() => {
  const h = props.project?.pipeline_config?.ng_handling || {};
  return h.missing_step === 'ack' || h.missing_step === 'hold' || h.short_count === 'ack';
});

const addEvent = () => {
  dbg('project.config', '点击「添加事件」', `当前数量=${props.project?.events_config?.length ?? 0}`);
  if (!props.project.events_config) props.project.events_config = [];
  const newId = Date.now();
  props.project.events_config.push({
    id: newId,
    name: '新事件',
    color: '#3b82f6',
    actions: [],
    show_notification: false,
    notification_type: 'normal',
    require_ack: false,
    ack_timeout_sec: 0,
    ack_resets_periodic: false,
    ack_keep_cycle: false,
  });
};

const removeEvent = (idx) => {
  dbg('project.config', '点击「删除事件」', `idx=${idx} name=${props.project?.events_config?.[idx]?.name}`);
  props.project.events_config.splice(idx, 1);
};

const addEventAction = (event) => {
  if (!event.actions) event.actions = [];
  event.actions.push({
    counter_name: props.project.counters_config?.[0]?.name || '合格总数',
    delta: 1
  });
};
</script>
