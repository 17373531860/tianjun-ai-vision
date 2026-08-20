<template>
  <!-- 装箱清点（混合跟踪专属）：以容器为单位组织物品计数与进箱记账。
       全部字段写 project 顶层扁平字段 → index.vue 保存时收进 pipeline_config，链路未动。
       行结构保持 div.flex（e2e 按行文案定位 el-switch / el-input-number）。 -->
  <div class="space-y-4 mt-4">
    <el-alert type="info" :closable="false" show-icon>
      <template #title>
        <span class="text-xs">
          本页配置装箱过程的计数与记账规则。单个物品的期望数量与校验参数在
          <b>步骤设置 → 物品校验参数</b> 中配置；周期的开始与结算时机由「逻辑设置」决定。
        </span>
      </template>
    </el-alert>

    <el-card shadow="never" class="bg-slate-800 border-slate-700">
      <template #header>
        <div class="flex items-center justify-between">
          <span class="font-bold text-white">容器装箱清点</span>
          <el-switch v-model="project.custom_mix_container_enabled" />
        </div>
      </template>

      <p class="text-xs text-gray-400 mb-1">
        启用后，物品按其所属容器分组计数；容器完成进箱（离场确认）时记入台账，封箱（末步）时对整箱结果进行裁决。
      </p>

      <div v-if="project.custom_mix_container_enabled" class="mt-3 space-y-1">
        <!-- ============ 基础参数 ============ -->
        <el-divider content-position="left">
          <span class="text-xs text-gray-400 font-bold">基础参数</span>
        </el-divider>

        <div class="flex items-center gap-3 text-xs mix-row">
          <span class="mix-label">容器标签</span>
          <el-select v-model="project.custom_mix_container_label" size="small" class="!w-44" placeholder="选择容器标签">
            <el-option v-for="s in nonBackupSteps" :key="s.id" :label="s.displayLabel || s.label" :value="s.label" />
          </el-select>
          <span class="mix-help">被视为容器的检测类别，物品按当前主容器归组</span>
        </div>

        <div class="flex items-center gap-3 text-xs mix-row">
          <span class="mix-label">容器匹配 IoU</span>
          <el-input-number v-model="project.custom_mix_container_iou_match" :min="0.05" :max="0.95" :step="0.05" :precision="2" size="small" class="!w-28" />
          <span class="mix-help">跨帧关联同一容器的重叠度阈值，位置稳定的场景可适当调高</span>
        </div>

        <div v-if="project.custom_mix_container_confirm_by_frames" class="flex items-center gap-3 text-xs mix-row">
          <span class="mix-label">消失确认帧</span>
          <el-input-number v-model="project.custom_mix_container_gone_frames" :min="1" :step="5" size="small" class="!w-28" />
          <span class="mix-help">容器连续离开画面达到该帧数后，判定其已进箱</span>
        </div>

        <!-- ============ 计数与判定 ============ -->
        <el-divider content-position="left">
          <span class="text-xs text-gray-400 font-bold">计数与判定</span>
        </el-divider>

        <div class="flex items-center gap-3 text-xs mix-row">
          <span class="mix-label">计数方式</span>
          <el-radio-group v-model="project.custom_mix_container_count_mode" size="small">
            <el-radio-button label="trays">按容器计数</el-radio-button>
            <el-radio-button label="items_total">按物品总数</el-radio-button>
          </el-radio-group>
          <span class="mix-help">
            {{ project.custom_mix_container_count_mode === 'trays'
              ? '统计进箱容器数量，每个容器需满足其物品期望数量'
              : '累计进箱物品总数，以整箱总量为判定依据' }}
          </span>
        </div>

        <div v-if="project.custom_mix_container_count_mode === 'trays'" class="flex items-center gap-3 text-xs mix-row">
          <span class="mix-label">每箱容器数</span>
          <el-input-number v-model="project.custom_mix_container_box_count" :min="0" :step="1" size="small" class="!w-28" />
          <span class="mix-help">整箱合格所需的容器数量；每容器的物品期望数量在「步骤设置 → 物品校验参数」中配置</span>
        </div>

        <template v-else>
          <div class="flex items-center gap-3 text-xs mix-row">
            <span class="mix-label">整箱物品总目标</span>
            <el-input-number v-model="project.custom_mix_container_item_target" :min="0" :step="1" size="small" class="!w-28" />
            <span class="mix-help">进箱物品总数须恰好等于该值方判定合格，不足或超出均判 NG</span>
          </div>
          <div class="flex items-center gap-3 text-xs mix-row">
            <span class="mix-label">每盘数量校验</span>
            <el-switch v-model="project.custom_mix_container_per_tray_guard" size="small" />
            <el-tooltip placement="top">
              <template #content>
                <div style="max-width: 360px; line-height: 1.6">
                  开启后，每个容器进箱时即校验其物品数量是否等于单容器期望值；
                  不符则该容器不记账并触发报警（配合事件的「需人工确认」可定格画面，待纠正后继续）。
                  最后一个容器自动按整箱余数校验。
                </div>
              </template>
              <span class="mix-help cursor-help border-b border-dashed border-gray-600">进箱时逐容器校验数量，不符即时拦截</span>
            </el-tooltip>
          </div>
        </template>

        <!-- ============ 计数修正 ============ -->
        <el-divider content-position="left">
          <span class="text-xs text-gray-400 font-bold">计数修正</span>
        </el-divider>

        <div class="flex items-center gap-3 text-xs mix-row">
          <span class="mix-label">每盘峰值封顶</span>
          <el-input-number v-model="project.custom_mix_container_peak_cap" :min="0" :step="1" size="small" class="!w-28" />
          <span class="mix-help">单容器计数上限，用于抑制重复检测框造成的计数虚高；建议设为单容器期望数量，0 为不限制</span>
        </div>

        <div class="flex items-center gap-3 text-xs mix-row">
          <span class="mix-label">槽位完整性校验</span>
          <el-select v-model="project.custom_mix_container_slot_check_label" size="small"
            class="!w-40" clearable filterable allow-create default-first-option
            placeholder="空槽标签">
            <el-option v-for="lbl in availableLabels" :key="lbl" :label="lbl" :value="lbl" />
          </el-select>
          <el-input-number v-model="project.custom_mix_container_slot_total" :min="0" :step="1" size="small" class="!w-28" placeholder="每盘槽位数" />
          <el-tooltip placement="top">
            <template #content>
              <div style="max-width: 380px; line-height: 1.6">
                利用容器物理槽位数固定的先验：仅当帧内「物品数 + 空槽数」等于槽位总数时才采信该帧计数，
                用于抑制遮挡与重复框造成的计数抖动。<br/><br/>
                <b>启用条件</b>：空槽类别需具备经过验证的高检出率，且空槽 ROI 与物品一致；
                检出率不足时会引入反向误差（缺装被采信为满载），并可能影响「动作前稳定计数」的快照采集。
                两项均填写后生效，默认关闭。
              </div>
            </template>
            <span class="mix-help cursor-help border-b border-dashed border-gray-600">按「物品 + 空槽 = 槽位数」过滤不可信帧（需空槽类别检出率达标）</span>
          </el-tooltip>
        </div>

        <div class="flex items-center gap-3 text-xs mix-row">
          <span class="mix-label">物品框去重 IoU</span>
          <el-input-number v-model="project.custom_mix_container_item_dedup_iou" :min="0" :max="1" :step="0.05" :precision="2" size="small" class="!w-28" />
          <span class="mix-help">同一物品输出多个高重叠框时仅保留置信度最高者；建议保持默认 0.45，0 为关闭</span>
        </div>

        <div class="flex items-center gap-3 text-xs mix-row">
          <span class="mix-label">容器框去重 IoU</span>
          <el-input-number v-model="project.custom_mix_container_tray_dedup_iou" :min="0" :max="1" :step="0.05" :precision="2" size="small" class="!w-28" />
          <span class="mix-help">同一容器被识别为多个时仅保留一个，避免产生冗余记账；参考值 0.5，0 为关闭（默认）</span>
        </div>

        <!-- ============ 进箱确认 ============ -->
        <el-divider content-position="left">
          <span class="text-xs text-gray-400 font-bold">进箱确认</span>
        </el-divider>

        <div class="flex items-center gap-3 text-xs mix-row">
          <span class="mix-label">确认方式</span>
          <el-checkbox v-model="project.custom_mix_container_confirm_by_frames" size="small">消失满帧确认</el-checkbox>
          <el-checkbox v-model="project.custom_mix_container_confirm_by_action" size="small">标签动作确认</el-checkbox>
          <span class="mix-help">容器进箱的判定依据，可单选或组合使用</span>
        </div>

        <div v-if="project.custom_mix_container_confirm_by_frames && project.custom_mix_container_confirm_by_action"
          class="flex items-center gap-3 text-xs mix-row">
          <span class="mix-label">组合逻辑</span>
          <el-radio-group v-model="project.custom_mix_container_confirm_combine" size="small">
            <el-radio-button label="or">满足其一</el-radio-button>
            <el-radio-button label="and">同时满足</el-radio-button>
          </el-radio-group>
        </div>

        <template v-if="project.custom_mix_container_confirm_by_action">
          <div class="flex items-center gap-3 text-xs mix-row">
            <span class="mix-label">动作标签</span>
            <el-select v-model="project.custom_mix_container_action_label" size="small" class="!w-44" placeholder="选择动作标签">
              <el-option v-for="s in nonBackupSteps" :key="s.id" :label="s.displayLabel || s.label" :value="s.label" />
            </el-select>
            <span class="mix-help">该动作类别出现即视为一次进箱操作</span>
          </div>

          <div class="flex items-center gap-3 text-xs mix-row">
            <span class="mix-label">动作确认帧</span>
            <span class="text-gray-500 shrink-0">动作出现确认帧</span>
            <el-input-number v-model="project.custom_mix_container_action_min_frames" :min="0" :step="1" size="small" class="!w-24" />
            <span class="text-gray-500 shrink-0">动作消失确认帧</span>
            <el-input-number v-model="project.custom_mix_container_action_gone_frames" :min="0" :step="1" size="small" class="!w-24" />
            <span class="mix-help">0 表示沿用该步骤的「最短出现帧 / 消失确认帧」配置</span>
          </div>

          <div class="flex items-center gap-3 text-xs mix-row">
            <span class="mix-label">进箱最小间隔(秒)</span>
            <el-input-number v-model="project.custom_mix_container_action_cooldown_s" :min="0" :max="60" :step="0.5" :precision="1" size="small" class="!w-28" />
            <span class="mix-help">间隔内的重复动作视为同一次进箱，抑制检测断续导致的重复记账；应小于实际作业节拍，0 为关闭</span>
          </div>

          <div class="flex items-center gap-3 text-xs mix-row">
            <span class="mix-label">动作前稳定计数(帧)</span>
            <el-input-number v-model="project.custom_mix_container_stable_min_frames" :min="0" :step="1" size="small" class="!w-24" />
            <el-tooltip placement="top">
              <template #content>
                <div style="max-width: 360px; line-height: 1.6">
                  以进箱动作发生前、连续稳定达到设定帧数的计数快照入账，使记账结果与操作节奏解耦。
                  适用于连续快速放置、容器身份持续在位的场景。建议 6 帧，0 为关闭。
                </div>
              </template>
              <span class="mix-help cursor-help border-b border-dashed border-gray-600">按动作前的稳定计数快照入账，与操作快慢解耦</span>
            </el-tooltip>
          </div>
        </template>

        <!-- ============ 记账修正 ============ -->
        <el-divider content-position="left">
          <span class="text-xs text-gray-400 font-bold">记账修正</span>
        </el-divider>
        <p class="text-xs text-gray-500 mb-2">以下各项可独立启停；保持关闭即为基础记账行为。</p>

        <div class="flex items-center gap-3 text-xs mix-row">
          <el-switch v-model="project.custom_mix_container_dedup_items" size="small" />
          <span class="mix-switch-label">物品重复框去重</span>
          <span class="mix-help">同一物品的多个高重叠检测框仅计一次（保留置信度最高者）。默认开启</span>
        </div>
        <div class="flex items-center gap-3 text-xs mix-row">
          <el-switch v-model="project.custom_mix_container_dedup_trays" size="small" />
          <span class="mix-switch-label">容器重复框去重</span>
          <span class="mix-help">同一容器的多个高重叠检测框不再建立重复记账身份，避免冗余台账占位</span>
        </div>
        <div class="flex items-center gap-3 text-xs mix-row">
          <el-switch v-model="project.custom_mix_container_purge_empty_primary" size="small" />
          <span class="mix-switch-label">空账容器清理</span>
          <span class="mix-help">记账容器已离场且无任何计数时，自动释放其记账位</span>
        </div>
        <div class="flex items-center gap-3 text-xs mix-row">
          <el-switch v-model="project.custom_mix_container_yield_primary" size="small" />
          <span class="mix-switch-label">记账容器让位</span>
          <span class="mix-help">记账容器已离场而画面存在计数更完整的在位容器时，记账位移交给该容器（放置动作期间不移交）</span>
        </div>
        <div class="flex items-center gap-3 text-xs mix-row">
          <el-switch v-model="project.custom_mix_container_unified_book_source" size="small" />
          <span class="mix-switch-label">显示与记账同源</span>
          <span class="mix-help">监控卡片的实时 / 峰值 / 预计进箱数值与结算校验取自同一记账容器，避免多容器数值混排；建议与上两项配合启用</span>
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

<style scoped>
.mix-row {
  padding: 5px 0;
}
.mix-label {
  width: 9.5rem;
  flex-shrink: 0;
  color: #94a3b8;
}
.mix-switch-label {
  flex-shrink: 0;
  color: #cbd5e1;
  font-weight: 500;
}
.mix-help {
  color: #64748b;
  line-height: 1.5;
}
:deep(.el-divider--horizontal) {
  margin: 14px 0 8px;
  border-color: #334155;
}
</style>
