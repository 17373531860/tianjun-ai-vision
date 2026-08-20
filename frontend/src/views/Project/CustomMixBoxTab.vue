<template>
  <!-- 容器装箱清点（混合跟踪专属，原逻辑设置内嵌块整体迁入）：
       把某类标签当容器，物品归当前主容器，容器进箱(消失)时记账。
       全部字段仍写 project 顶层扁平字段 → index.vue 保存时收进 pipeline_config，链路未动。 -->
  <div class="space-y-4 mt-4">
    <el-alert type="info" :closable="false" show-icon>
      <template #title>
        <span class="text-xs">
          本页只管「怎么装箱怎么记账」；每个物品的期望数量/校验参数在
          <b>步骤设置 → 物品校验参数</b> 配置；周期何时开始/结算仍由「逻辑设置」的步骤侧决定。
        </span>
      </template>
    </el-alert>

    <el-card shadow="never" class="bg-slate-800 border-slate-700">
      <template #header><span class="font-bold text-white">容器装箱清点</span></template>
      <div class="w-full">
        <div class="flex items-center gap-2">
          <el-switch v-model="project.custom_mix_container_enabled" />
          <span class="text-xs text-gray-400">开启后，物品按「当前主容器」分组，容器进箱(消失确认)即记账；封箱(末步)时整箱裁决</span>
        </div>
        <div v-if="project.custom_mix_container_enabled"
          class="mt-2 border border-slate-600 rounded p-3 bg-slate-900/50 space-y-3">
          <div class="flex items-center gap-2 flex-wrap text-xs">
            <span class="text-gray-400 shrink-0">容器标签</span>
            <el-select v-model="project.custom_mix_container_label" size="small" class="!w-40" placeholder="选择容器标签">
              <el-option v-for="s in nonBackupSteps" :key="s.id" :label="s.displayLabel || s.label" :value="s.label" />
            </el-select>
            <template v-if="project.custom_mix_container_confirm_by_frames">
              <span class="text-gray-400 shrink-0 ml-2">消失确认帧</span>
              <el-input-number v-model="project.custom_mix_container_gone_frames" :min="1" :step="5" size="small" class="!w-28" />
            </template>
            <span class="text-gray-400 shrink-0 ml-2">容器匹配IoU</span>
            <el-input-number v-model="project.custom_mix_container_iou_match" :min="0.05" :max="0.95" :step="0.05" :precision="2" size="small" class="!w-28" />
          </div>
          <div class="flex items-center gap-2 text-xs">
            <span class="text-gray-400 shrink-0">计数方式</span>
            <el-radio-group v-model="project.custom_mix_container_count_mode" size="small">
              <el-radio-button label="trays">盘计数（计容器数 × 每盘门槛）</el-radio-button>
              <el-radio-button label="items_total">滑块总数（累加进箱物品总数）</el-radio-button>
            </el-radio-group>
          </div>
          <div v-if="project.custom_mix_container_count_mode === 'trays'" class="flex items-center gap-2 text-xs">
            <span class="text-gray-400 shrink-0">每箱容器数</span>
            <el-input-number v-model="project.custom_mix_container_box_count" :min="0" :step="1" size="small" class="!w-28" />
            <span class="text-gray-500">每盘物品期望在「步骤设置 → 物品校验参数」每个物品的期望数量里配置</span>
          </div>
          <div v-else class="flex items-center gap-2 text-xs">
            <span class="text-gray-400 shrink-0">整箱物品总目标</span>
            <el-input-number v-model="project.custom_mix_container_item_target" :min="0" :step="1" size="small" class="!w-28" />
            <span class="text-gray-500">进箱物品总数正好等于此值才合格（少了/多了均 NG）；不卡每盘数量与容器数</span>
          </div>
          <div v-if="project.custom_mix_container_count_mode === 'items_total'" class="flex items-center gap-2 text-xs flex-wrap">
            <span class="text-gray-400 shrink-0">每盘数量校验</span>
            <el-switch v-model="project.custom_mix_container_per_tray_guard" size="small" />
            <span class="text-gray-500">错盘当场拦截：进箱那一刻核这盘数量是否等于每盘期望（步骤设置 → 物品的期望数量），不对则该盘不记账并报警（提示事件勾「需人工确认」即定格，工人取出错盘、确认后重装）；尾盘自动按整箱余数核，只算总数对得上</span>
          </div>
          <!-- v3.44.0e 每盘峰值封顶: 治模型偶发重复框把一盘 24 数成 25/26 且峰值降不回来 -->
          <div class="flex items-center gap-2 text-xs flex-wrap">
            <span class="text-gray-400 shrink-0">每盘峰值封顶</span>
            <el-input-number v-model="project.custom_mix_container_peak_cap" :min="0" :step="1" size="small" class="!w-28" />
            <span class="text-gray-500">0 = 关闭。防模型偶发重复框把一盘数成 25/26 后峰值卡住降不回来：设为每盘期望数（如 24）后单盘峰值最多记到该值；只封上限，少装照常判定</span>
          </div>
          <!-- v3.46 槽位完整性门: 货+空槽=槽位数才采信本帧, 治遮挡残数/重复框 -->
          <div class="flex items-center gap-2 text-xs flex-wrap">
            <span class="text-gray-400 shrink-0">空槽标签</span>
            <el-select v-model="project.custom_mix_container_slot_check_label" size="small"
              class="!w-40" clearable filterable allow-create default-first-option
              placeholder="如：凹槽">
              <el-option v-for="lbl in availableLabels" :key="lbl" :label="lbl" :value="lbl" />
            </el-select>
            <span class="text-gray-400 shrink-0">每盘槽位数</span>
            <el-input-number v-model="project.custom_mix_container_slot_total" :min="0" :step="1" size="small" class="!w-28" />
            <span class="text-gray-500">⚠️ 默认关，两个都填才开门，开之前先看这段。原理是盘的物理槽位固定，本帧「货数+空槽数」= 槽位数才采信这一帧。<b class="text-amber-400">2026-08-05 实测：空槽召回不足时反而更差</b>——短装盘的空槽检不出来，倒是「重复框凑够满数 + 空槽 0」的坏帧完美满足等式被当成唯一可信帧，短装盘被记成满盘；同时挡帧会饿死「动作前稳定计数」的快照，紧凑连放时两盘并一次结算丢整盘账。只有在空槽类召回确实够高（现场逐帧核过）时才开；开则须给空槽画与物品相同的 ROI</span>
          </div>
          <!-- v3.46 两处重复框去重: 物品框原为硬编码常开, 现改可配; 托盘框新增默认关 -->
          <div class="flex items-center gap-2 text-xs flex-wrap">
            <span class="text-gray-400 shrink-0">物品框去重(IoU)</span>
            <el-input-number v-model="project.custom_mix_container_item_dedup_iou" :min="0" :max="1" :step="0.05" :precision="2" size="small" class="!w-24" />
            <span class="text-gray-400 shrink-0">托盘框去重(IoU)</span>
            <el-input-number v-model="project.custom_mix_container_tray_dedup_iou" :min="0" :max="1" :step="0.05" :precision="2" size="small" class="!w-24" />
            <span class="text-gray-500">均 0 = 关闭该项。同一个目标被模型画两个框时，重叠超过此值就只留置信度高的那个。物品框缺省 0.45（密排滑块 22 个检出 25 个的老账，建议保持）；托盘框缺省 0 关闭，一个盘被吐两个框会多挂一张空账工牌，长期在位、带着上一盘的旧数字，主位一释放就顶上去——遇到「盘数对不上/凭空多记一盘」再开，建议 0.5</span>
          </div>
          <!-- 进箱确认方式：消失满帧 / 标签动作，可二选一或组合(OR/AND) -->
          <div class="flex items-start gap-2 text-xs pt-2 border-t border-slate-700">
            <span class="text-gray-400 shrink-0 mt-1">进箱确认方式</span>
            <div class="flex flex-col gap-2">
              <div class="flex items-center gap-4">
                <el-checkbox v-model="project.custom_mix_container_confirm_by_frames" size="small">消失满帧确认</el-checkbox>
                <el-checkbox v-model="project.custom_mix_container_confirm_by_action" size="small">标签动作确认</el-checkbox>
              </div>
              <div v-if="project.custom_mix_container_confirm_by_action" class="flex items-center gap-2 flex-wrap">
                <span class="text-gray-400 shrink-0">动作标签</span>
                <el-select v-model="project.custom_mix_container_action_label" size="small" class="!w-40" placeholder="选择动作标签">
                  <el-option v-for="s in nonBackupSteps" :key="s.id" :label="s.displayLabel || s.label" :value="s.label" />
                </el-select>
              </div>
              <!-- v3.43.1 动作门槛直配: 此前借用步骤字段, 本模式下无 UI 入口调不到 -->
              <div v-if="project.custom_mix_container_confirm_by_action" class="flex items-center gap-2 flex-wrap">
                <span class="text-gray-400 shrink-0">动作出现确认帧</span>
                <el-input-number v-model="project.custom_mix_container_action_min_frames" :min="0" :step="1" size="small" class="!w-24" />
                <span class="text-gray-400 shrink-0 ml-2">动作消失确认帧</span>
                <el-input-number v-model="project.custom_mix_container_action_gone_frames" :min="0" :step="1" size="small" class="!w-24" />
                <span class="text-gray-500">0 = 跟随该步骤的「最短出现帧 / 消失确认帧」（缺省 3 / 8 帧）</span>
              </div>
              <div v-if="project.custom_mix_container_confirm_by_action" class="flex items-center gap-2 flex-wrap">
                <span class="text-gray-400 shrink-0">进箱最小间隔(秒)</span>
                <el-input-number v-model="project.custom_mix_container_action_cooldown_s" :min="0" :max="60" :step="0.5" :precision="1" size="small" class="!w-24" />
                <span class="text-gray-500">不应期：距上次进箱不足此间隔的动作按同一次动作的余波吸收，防断检把一次动作拆成两次重复记账；快节奏连放请把间隔调小于两盘真实间隔；0 = 关闭</span>
              </div>
              <!-- v3.44.0e 动作前稳定计数快照: 连放场景堆顶检测框无缝接上下一盘、身份永不消失时的记账方式 -->
              <div v-if="project.custom_mix_container_confirm_by_action" class="flex items-center gap-2 flex-wrap">
                <span class="text-gray-400 shrink-0">动作前稳定计数(帧)</span>
                <el-input-number v-model="project.custom_mix_container_stable_min_frames" :min="0" :step="1" size="small" class="!w-24" />
                <span class="text-gray-500">0 = 关闭。开启后按「进箱动作成立瞬间、手接触托盘之前」连续稳定满此帧数的计数入账，与工人快慢解耦，专治连放时几盘合并成一盘/在途遮挡少记；建议 6 帧</span>
              </div>
              <div v-if="project.custom_mix_container_confirm_by_frames && project.custom_mix_container_confirm_by_action"
                class="flex items-center gap-2">
                <span class="text-gray-400 shrink-0">组合逻辑</span>
                <el-radio-group v-model="project.custom_mix_container_confirm_combine" size="small">
                  <el-radio-button label="or">OR（满足其一即进箱）</el-radio-button>
                  <el-radio-button label="and">AND（两者都满足才进箱）</el-radio-button>
                </el-radio-group>
              </div>
            </div>
          </div>
          <!-- v3.46 记账修正开关组: 全部可独立开关, 关 = 之前版本行为, 随时可回退 -->
          <div class="flex items-start gap-2 text-xs pt-2 border-t border-slate-700">
            <span class="text-gray-400 shrink-0 mt-1">记账修正</span>
            <div class="flex flex-col gap-2">
              <div class="flex items-center gap-2 flex-wrap">
                <el-switch v-model="project.custom_mix_container_dedup_items" size="small" />
                <span class="text-gray-400 shrink-0">滑块重复框去重</span>
                <span class="text-gray-500">同一个滑块被模型画两个高重叠框时只算一个（置信度高者保留）。默认开；关闭回退到不去重的旧口径</span>
              </div>
              <div class="flex items-center gap-2 flex-wrap">
                <el-switch v-model="project.custom_mix_container_dedup_trays" size="small" />
                <span class="text-gray-400 shrink-0">托盘重复框去重</span>
                <span class="text-gray-500">同一个托盘被画两个高重叠框时不再多立一个"影子托盘"（影子会带着旧数字抢占记账），与滑块去重同款判定</span>
              </div>
              <div class="flex items-center gap-2 flex-wrap">
                <el-switch v-model="project.custom_mix_container_purge_empty_primary" size="small" />
                <span class="text-gray-400 shrink-0">空账托盘身份清理</span>
                <span class="text-gray-500">正在记账的托盘身份若已离场满消失确认帧且一个数都没记过，照样清掉让位。专治"大数字长期 0、实时却稳定 24"</span>
              </div>
              <div class="flex items-center gap-2 flex-wrap">
                <el-switch v-model="project.custom_mix_container_yield_primary" size="small" />
                <span class="text-gray-400 shrink-0">记账托盘可让位</span>
                <span class="text-gray-500">正在记账的托盘已判定离场、而画面上有账面更实的在位托盘时，把记账位让给它（放托盘动作期间不让）。专治取出重装/搬动后大数字停在旧残数</span>
              </div>
              <div class="flex items-center gap-2 flex-wrap">
                <el-switch v-model="project.custom_mix_container_unified_book_source" size="small" />
                <span class="text-gray-400 shrink-0">显示与记账同源</span>
                <span class="text-gray-500">卡片上实时/峰值/预计进箱三个数与封箱数量校验统一取"结账时真正会被选中的那盘"，杜绝两盘数字混排（如大数字 8、实时 23 并存）。建议与上两项一起开</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { computed } from 'vue';

const props = defineProps({
  project: { type: Object, required: true },
});

// 步骤候选（与 LogicConfigTab 同口径）：物品角色/备用步骤不进候选
const enabledSteps = computed(() => {
  if (!props.project?.steps_config) return [];
  return props.project.steps_config.filter(s => s.enabled && s.detect_role !== 'item');
});

const nonBackupSteps = computed(() => enabledSteps.value.filter(s => !s.backup_for));

const availableLabels = computed(() => {
  const set = new Set();
  (props.project?.steps_config || []).forEach(s => {
    if (s && s.label) set.add(s.label);
  });
  return Array.from(set);
});
</script>
