<template>
  <!-- ==================== 逻辑设置 Tab（2026-07 拆分批次 P-4 自 index.vue 外置） ==================== -->
    <div class="h-full overflow-y-auto p-4 pb-32 custom-scrollbar space-y-6">

      <!-- Settlement Mode (sequential / custom-sequential) -->
      <el-card v-if="project.logic_mode === 'sequential' || (project.logic_mode === 'custom' && project.custom_based_on === 'sequential')" shadow="never" class="bg-slate-800 border-slate-700">
        <template #header><span class="font-bold text-white">结算方式</span></template>
        <div class="space-y-4 text-sm text-gray-300">
          <el-radio-group v-model="project.settlement_mode">
            <el-radio value="first_step">
              <span class="text-gray-300">第一步结算</span>
              <span class="text-xs text-gray-500 ml-1">— 新周期的第一步出现时结算上一周期</span>
            </el-radio>
            <el-radio value="last_step">
              <span class="text-gray-300">最后一步结算</span>
              <span class="text-xs text-gray-500 ml-1">— 最后一步消失后结算当前周期</span>
            </el-radio>
            <el-radio value="last_first">
              <span class="text-gray-300">末步结算 + 首步开周期</span>
              <span class="text-xs text-gray-500 ml-1">— 末步出现立即结算上周期，首步开新周期；缺步骤自动判 NG（v3.9.0+）</span>
            </el-radio>
          </el-radio-group>
          <!-- last_first 模式说明 -->
          <div v-if="project.settlement_mode === 'last_first'" class="bg-slate-900 rounded p-3 text-xs text-gray-400 border border-amber-700/50">
            <p class="text-amber-400 font-bold mb-1">末步结算 + 首步开周期模式约束</p>
            <p class="mb-1">• 末步出现立即结算上周期，序列内 缺哪步自动判 NG（缺哪步报哪步）</p>
            <p class="mb-1">• 首步未出现时，序列后面的步骤可顶替开新周期</p>
            <p class="mb-1">• 首步又出现时，无论何种状态都立即结算上周期 NG（缺末步）+ 开新周期</p>
            <p class="text-red-400">⚠ 强制约束：本模式下所有步骤的"严格顺序"会被自动关闭；不能与"跨周期同时出现组"或"逐件覆盖模式"同时启用</p>
          </div>
          <div class="flex items-center gap-3 pt-2 border-t border-slate-700">
            <span class="text-gray-400 text-xs whitespace-nowrap">空闲超时(秒)</span>
            <el-input-number v-model="project.idle_timeout_seconds" size="small" :min="0" :step="5" :precision="2" />
            <span class="text-xs text-gray-500">超过此时间无新步骤加入，强制结算当前周期（0=不启用）</span>
          </div>
          <div class="flex items-center gap-3 pt-2 border-t border-slate-700">
            <span class="text-gray-400 text-xs whitespace-nowrap">周期超时(秒)</span>
            <el-input-number v-model="project.cycle_max_duration" size="small" :min="0" :step="5" :precision="2" />
            <span class="text-xs text-gray-500">周期总时长超过此值直接判定NG（0=不启用）</span>
          </div>
        </div>
      </el-card>

      <!-- ==================== v3.44 NG 判定与处置 (统一卡) ====================
           收敛 v3.23 补做策略 / v3.32 违序提示 / v3.43 实时NG / v3.44 收尾防呆:
           一行一场景, 句式固定 "场景 → 处置档位 → 内联参数"; 档位不选高级项不渲染 -->
      <el-card v-if="ngh && ['sequential', 'detection', 'custom'].includes(project.logic_mode)"
               shadow="never" class="bg-slate-800 border-slate-700">
        <template #header>
          <div class="flex items-center justify-between">
            <span class="font-bold text-white">NG 判定与处置</span>
            <span class="text-xs text-gray-500">默认全部「直接判NG」= 老行为零差异</span>
          </div>
        </template>
        <div class="space-y-3 text-sm text-gray-300">

          <!-- 场景 1: 违规当场 (last_first 严格顺序被强制清空 → 本行隐藏) -->
          <div v-if="project.settlement_mode !== 'last_first' || project.logic_mode === 'detection'"
               class="flex items-center gap-3 flex-wrap">
            <span class="text-gray-400 w-28 shrink-0">违规出现时</span>
            <el-select v-model="ngh.violation" size="small" class="!w-56">
              <el-option value="none" label="不处理 — 等周期结算再判" />
              <el-option value="hint" label="当场提示 — 报警不结算" />
              <el-option value="instant_ng" label="立即NG — 当场结算本周期" />
            </el-select>
            <template v-if="ngh.violation !== 'none'">
              <span class="text-gray-400 text-xs">提示事件</span>
              <el-select :model-value="ngh.violation_event_id ?? null" size="small" class="!w-44"
                placeholder="不提示（仅日志）" clearable
                @update:model-value="ngh.violation_event_id = $event || null">
                <el-option v-for="ev in (project.events_config || [])" :key="ev.id" :label="ev.name" :value="ev.id" />
              </el-select>
            </template>
            <el-tooltip placement="top" effect="dark"
              content="违规=违序/缺前置（步骤需勾「严格顺序」）、回退重复（顺序型）、重复超次（检测模式）。「立即NG」在周期未开始时降级为提示。">
              <span class="text-gray-500 cursor-help text-xs">ⓘ</span>
            </el-tooltip>
          </div>

          <!-- 场景 2: 缺步骤 -->
          <div class="flex items-center gap-3 flex-wrap pt-2 border-t border-slate-700/60">
            <span class="text-gray-400 w-28 shrink-0">结算缺步骤时</span>
            <el-select v-model="ngh.missing_step" size="small" class="!w-56">
              <el-option value="ng" label="直接判NG" />
              <el-option value="ack" label="定格弹窗 — 可补步骤判合格" />
              <el-option v-if="isCustomSequential" value="hold" label="挂起等补做 — 补齐自动判合格" />
            </el-select>
            <template v-if="ngh.missing_step === 'hold' || ngh.short_count === 'hold'">
              <span class="text-gray-400 text-xs">超时(秒)</span>
              <el-input-number v-model="ngh.hold_timeout_s" size="small" :min="0" :step="10" :precision="0" />
              <span class="text-gray-400 text-xs">提示事件</span>
              <el-select :model-value="ngh.hold_event_id ?? null" size="small" class="!w-44"
                placeholder="不提示（仅日志）" clearable
                @update:model-value="ngh.hold_event_id = $event || null">
                <el-option v-for="ev in (project.events_config || [])" :key="ev.id" :label="ev.name" :value="ev.id" />
              </el-select>
            </template>
            <el-tooltip placement="top" effect="dark"
              content="「定格弹窗」：NG 弹人工确认窗，给「补步骤—判合格」按钮，结果延迟落账。「挂起等补做」：不弹窗不定格，报警后周期保持打开，工人补齐缺的步骤自动判合格；超时未补按缺步 NG 落账（0=不限时）。">
              <span class="text-gray-500 cursor-help text-xs">ⓘ</span>
            </el-tooltip>
          </div>

          <!-- 场景 3: 少装数量 (仅混合跟踪包装场景有"数量"概念) -->
          <div v-if="project.logic_mode === 'custom'"
               class="flex items-center gap-3 flex-wrap pt-2 border-t border-slate-700/60">
            <span class="text-gray-400 w-28 shrink-0">结算少数量时</span>
            <el-select v-model="ngh.short_count" size="small" class="!w-56">
              <el-option value="ng" label="直接判NG" />
              <el-option value="ack" label="定格弹窗 — 可补数量判合格" />
              <el-option value="hold" label="挂起等补数量 — 断点重做收尾步" />
            </el-select>
            <span class="text-xs text-gray-500">步骤都对但箱内数量不足目标（如少装滑块）</span>
            <el-tooltip v-if="ngh.short_count === 'hold'" placement="top" effect="dark"
              content="不判NG不清账：保留已完成步骤和已进箱数量，报警提示差多少；工人补足数量、重做收尾步骤（数量门步骤，如放油嘴包/封箱）后自动判合格。超时未补按缺数量NG落账（共用下方缺步挂起的超时与提示事件）。">
              <span class="text-gray-500 cursor-help text-xs">ⓘ</span>
            </el-tooltip>
          </div>

          <!-- 场景 4: 数量门 (事前拦截, 混合跟踪包装专用) -->
          <div v-if="isCustomSequential"
               class="flex items-center gap-3 flex-wrap pt-2 border-t border-slate-700/60">
            <span class="text-gray-400 w-28 shrink-0">数量不足禁收尾</span>
            <el-switch v-model="ngh.gate_enabled" />
            <template v-if="ngh.gate_enabled">
              <el-select v-model="ngh.gate_steps" multiple size="small" class="!w-64"
                placeholder="选择收尾步骤（如放油嘴包/封箱）">
                <el-option v-for="s in nonBackupSteps" :key="s.id" :label="s.displayLabel || s.label" :value="s.label" />
              </el-select>
              <span class="text-gray-400 text-xs">提示事件</span>
              <el-select :model-value="ngh.gate_event_id ?? null" size="small" class="!w-44"
                placeholder="不提示（仅日志）" clearable
                @update:model-value="ngh.gate_event_id = $event || null">
                <el-option v-for="ev in (project.events_config || [])" :key="ev.id" :label="ev.name" :value="ev.id" />
              </el-select>
            </template>
            <el-tooltip placement="top" effect="dark"
              content="箱内数量没装够时，这些收尾步骤出现直接拒收 + 当场报警，杜绝少装就收尾；补够后再出现自然放行，周期不打断。">
              <span class="text-gray-500 cursor-help text-xs">ⓘ</span>
            </el-tooltip>
          </div>

          <!-- 场景 4b: 数量门升级放行 (v3.44.4 短拦长放) -->
          <div v-if="isCustomSequential && ngh.gate_enabled"
               class="flex items-center gap-3 flex-wrap">
            <span class="text-gray-400 w-28 shrink-0">拦下时升级放行</span>
            <el-select v-model="ngh.gate_escalate_steps" multiple size="small" class="!w-64"
              placeholder="不升级（全部静默拦下）" clearable>
              <el-option v-for="lb in ngh.gate_steps" :key="lb" :label="lb" :value="lb" />
            </el-select>
            <el-tooltip placement="top" effect="dark"
              content="这里选的收尾步骤被数量门拦下时不再静默吞掉：报警提示后放行进周期，结算后自动挂起（提示缺多少数量、重做哪些步骤），补齐自动判合格。适合识别门槛高、确认成立基本就是工人真动作的步骤（如封箱）；误检高发的步骤（如放油嘴包）不要选，保持静默拦。">
              <span class="text-gray-500 cursor-help text-xs">ⓘ</span>
            </el-tooltip>
            <span class="text-xs text-gray-500">治「真漏一盘就封箱 → 静默拦死无提示」；只选真动作步骤</span>
          </div>

          <!-- 跨 tab 联动明示: 定格弹窗由事件设置的 NG 事件「需人工确认」决定 -->
          <div v-if="ngh.missing_step === 'ack' || ngh.short_count === 'ack' || ngh.violation === 'instant_ng'"
               class="text-xs rounded p-2 border-l-2"
               :class="ngEventRequiresAck
                 ? 'text-gray-400 bg-slate-950/60 border-emerald-700/50'
                 : 'text-amber-300 bg-amber-950/30 border-amber-600/60'">
            <template v-if="ngEventRequiresAck">
              ✓ NG 事件已勾「需人工确认」（事件设置）— 定格弹窗会正常弹出，补做按钮在弹窗里。
            </template>
            <template v-else>
              ⚠ 上面选了「定格弹窗」档，但 NG 事件<b>没勾「需人工确认」</b>（事件设置 tab）— 弹窗不会出现，补做入口无效。请去事件设置勾上。
            </template>
          </div>
        </div>
      </el-card>

      <!-- Sequential Mode Config -->
      <el-card v-if="project.logic_mode === 'sequential'" shadow="never" class="bg-slate-800 border-slate-700">
        <template #header><span class="font-bold text-white">顺序模式 - 步骤排序</span></template>
        <div class="space-y-4 text-sm text-gray-300">
          <p class="text-xs text-gray-400">拖拽或使用按钮调整步骤执行顺序。按此顺序完成 → 事件1(合格)；跳步/乱序 → 事件2(NG)</p>
          <div class="bg-slate-900 rounded p-3 space-y-2">
            <div v-for="(seqStep, idx) in project.sequence_order" :key="idx" class="flex items-center gap-2 bg-slate-800 p-2 rounded">
              <span class="text-cyan-400 font-bold w-8">{{ idx + 1 }}.</span>
              <el-select v-model="seqStep.step_id" size="small" class="flex-1" placeholder="选择步骤"
                @change="$emit('sequence-step-pick')">
                <el-option 
                  v-for="s in nonBackupSteps" 
                  :key="s.id" 
                  :label="s.displayLabel || s.label" 
                  :value="s.id" 
                />
              </el-select>
              <el-button size="small" :disabled="idx === 0" @click="moveStepUp(idx)">↑</el-button>
              <el-button size="small" :disabled="idx === project.sequence_order.length - 1" @click="moveStepDown(idx)">↓</el-button>
              <el-button type="danger" size="small" link @click="removeSequenceStep(idx)">删除</el-button>
            </div>
            <el-button type="primary" size="small" @click="addSequenceStep">+ 添加步骤</el-button>
          </div>
        </div>
      </el-card>

      <!-- Detection Mode Config -->
      <el-card v-if="project.logic_mode === 'detection'" shadow="never" class="bg-slate-800 border-slate-700">
        <template #header><span class="font-bold text-white">检测模式 - 需检测的步骤</span></template>
        <div class="space-y-4 text-sm text-gray-300">
          <p class="text-xs text-gray-400">选择需要检测的步骤。第一个步骤开启周期，最后一个步骤结束周期，中间步骤无需顺序。全部检测到 → 合格(事件1)，缺少步骤 → NG(事件2)</p>
          <div class="bg-slate-900 rounded p-3">
            <el-checkbox-group v-model="project.detection_steps" class="flex flex-col gap-2">
              <el-checkbox 
                v-for="step in nonBackupSteps" 
                :key="step.id" 
                :label="step.id"
                class="!mr-0"
              >
                <span class="text-gray-300">{{ step.displayLabel || step.label }}</span>
              </el-checkbox>
            </el-checkbox-group>
            <div v-if="nonBackupSteps.length === 0" class="text-gray-500 text-center py-4">
              请先在"步骤设置"中启用步骤
            </div>
          </div>
        </div>
      </el-card>

      <!-- Custom Mode Config -->
      <el-card v-if="project.logic_mode === 'custom'" shadow="never" class="bg-slate-800 border-slate-700">
        <template #header><span class="font-bold text-white">自定义模式 - 条件配置</span></template>
        <div class="space-y-4 text-sm text-gray-300">
          <el-form label-position="top">
            <el-form-item label="基于模式（可选）">
              <el-select v-model="project.custom_based_on" class="w-full" clearable placeholder="不选择则仅使用自定义条件">
                <el-option label="基于顺序模式" value="sequential" />
                <el-option label="基于检测模式" value="detection" />
              </el-select>
              <p class="text-xs text-gray-500 mt-1">选择后将继承该模式的逻辑，自定义条件优先级更高</p>
            </el-form-item>
            <el-form-item label="混合模式（可选）">
              <el-select v-model="project.custom_mixed_with" class="w-full" clearable
                placeholder="不混合（与原自定义模式完全一致）" @change="$emit('mix-type-change')">
                <el-option label="混合逐件覆盖（目标⟶动作配对，每件个体都要被覆盖到）" value="per_item" />
                <el-option label="混合跟踪清点（唯一个体ID累积/动作计数/堆叠，与独立跟踪模式同机制）" value="tracking" />
              </el-select>
              <p class="text-xs text-gray-500 mt-1">
                选择后，可在「步骤设置」中把某些标签标记为「物品」：物品不参与步骤序列，
                由所选独立模式的同一套引擎校验（参数也在「步骤设置 → 物品校验参数」用该模式的原生词汇配置）。
                周期何时开始/结算仍由步骤侧决定，结算时步骤和物品都合格才算合格。
              </p>
            </el-form-item>

            <!-- 容器装箱清点（仅混合跟踪）：把某类标签当容器，物品归当前主容器，容器进箱(消失)时记账 -->
            <el-form-item v-if="project.custom_mixed_with === 'tracking'" label="容器装箱清点">
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
            </el-form-item>
          </el-form>

          <!-- 自定义模式独立的基础模式配置 -->
          <div v-if="project.custom_based_on === 'sequential'" class="border border-slate-600 rounded p-3 bg-slate-900/50">
            <p class="text-xs text-cyan-400 mb-2 font-bold">顺序配置（自定义模式独立）：</p>
            <div class="space-y-2">
              <div v-for="(item, idx) in (project.custom_sequence_order || [])" :key="idx" class="flex items-center gap-2">
                <span class="text-gray-400 text-xs w-6">{{ idx + 1 }}.</span>
                <el-select v-model="item.step_id" size="small" class="flex-1" placeholder="选择步骤"
                  @change="$emit('sequence-step-pick')">
                  <el-option v-for="step in nonBackupSteps" :key="step.id" :label="step.displayLabel || step.label" :value="step.id" />
                </el-select>
                <el-button type="danger" size="small" link @click="removeCustomSequenceStep(idx)">删除</el-button>
              </div>
              <el-button type="primary" size="small" link @click="addCustomSequenceStep">+ 添加步骤</el-button>
            </div>
            
            <!-- 累积重复序列选项 -->
            <div class="mt-4 pt-3 border-t border-slate-600">
              <div class="flex items-center justify-between">
                <div>
                  <span class="text-white text-sm">累积重复序列</span>
                  <p class="text-xs text-gray-500 mt-0.5">开启后，重复步骤不会提前结算，等最后一步完成后整体判定</p>
                </div>
                <el-switch v-model="project.accumulate_repeats" />
              </div>
            </div>
          </div>

          <div v-if="project.custom_based_on === 'detection'" class="border border-slate-600 rounded p-3 bg-slate-900/50">
            <p class="text-xs text-cyan-400 mb-2 font-bold">检测配置（自定义模式独立，勾选要检测的步骤）：</p>
            <el-checkbox-group v-model="project.custom_detection_steps" class="flex flex-wrap gap-2">
              <el-checkbox 
                v-for="step in nonBackupSteps" 
                :key="step.id" 
                :label="step.id"
                class="!mr-0"
              >
                <span class="text-gray-300">{{ step.displayLabel || step.label }}</span>
              </el-checkbox>
            </el-checkbox-group>
          </div>

          <!-- Custom Conditions -->
          <div class="border-t border-slate-700 pt-4">
            <div class="flex justify-between items-center mb-3">
              <div>
                <span class="font-bold text-white">自定义触发条件</span>
                <p class="text-xs text-gray-500">优先级数字越小越优先（1 > 2 > 3...），条件优先于基础模式</p>
              </div>
              <el-button type="primary" size="small" link @click="addCustomCondition">+ 新增条件</el-button>
            </div>
            <div class="space-y-3">
              <div v-for="(cond, idx) in (project.custom_conditions || [])" :key="cond.id || idx" class="bg-slate-900 p-3 rounded border border-slate-700">
                <div class="flex justify-between items-start mb-3">
                  <div class="flex items-center gap-3">
                    <span class="text-cyan-400 font-bold">条件 {{ idx + 1 }}</span>
                    <div class="flex items-center gap-1">
                      <span class="text-xs text-gray-500">优先级:</span>
                      <el-input-number v-model="cond.priority" size="small" :min="0" :precision="2" class="w-20" controls-position="right" />
                    </div>
                  </div>
                  <el-button type="danger" size="small" link @click="removeCustomCondition(idx)">删除</el-button>
                </div>
                <div class="space-y-3">
                  <div>
                    <p class="text-xs text-gray-500 mb-2">当检测到以下步骤序列时（按顺序选择）：</p>
                    <div class="space-y-2">
                      <div v-for="(stepId, sIdx) in (cond.sequence || [])" :key="sIdx" class="flex items-center gap-2">
                        <span class="text-gray-400 text-xs w-6">{{ sIdx + 1 }}.</span>
                        <el-select v-model="cond.sequence[sIdx]" size="small" class="flex-1" placeholder="选择步骤">
                          <el-option v-for="step in enabledSteps" :key="step.id" :label="step.displayLabel || step.label" :value="step.id" />
                        </el-select>
                        <el-button type="danger" size="small" link @click="removeConditionStep(idx, sIdx)">删除</el-button>
                      </div>
                      <el-button type="primary" size="small" link @click="addConditionStep(idx)">+ 添加步骤</el-button>
                    </div>
                  </div>
                  <div>
                    <p class="text-xs text-gray-500 mb-1">触发事件：</p>
                    <el-select v-model="cond.event_id" size="small" class="w-full" placeholder="选择要触发的事件">
                      <el-option v-for="ev in (project.events_config || [])" :key="ev.id" :label="ev.name" :value="ev.id" />
                    </el-select>
                  </div>
                </div>
              </div>
              <div v-if="!project.custom_conditions || project.custom_conditions.length === 0" class="text-gray-500 text-center py-4 border border-dashed border-slate-700 rounded">
                暂无自定义条件，点击上方按钮添加
              </div>
            </div>
          </div>
        </div>
      </el-card>

      <!-- Periodic Required Actions: 周期性强制动作（每 N 轮做 E 否则告警） -->
      <el-card shadow="never" class="bg-slate-800 border-slate-700">
        <template #header>
          <div class="flex items-center justify-between">
            <div>
              <span class="font-bold text-white">周期性强制动作</span>
              <p class="text-xs text-gray-500 mt-0.5">
                每做完 N 轮主流程必须执行的保养/校准动作（清洁、上油、换刀、标定...），
                到期未做即触发事件
              </p>
            </div>
            <el-button type="primary" size="small" link @click="addPeriodicAction">+ 新增规则</el-button>
          </div>
        </template>
        <div class="space-y-3 text-sm text-gray-300">
          <div v-for="(rule, idx) in (project.periodic_actions || [])" :key="rule.id || idx"
               class="bg-slate-900 p-3 rounded border border-slate-700">
            <div class="flex justify-between items-start mb-3">
              <div class="flex items-center gap-3 flex-1">
                <el-switch v-model="rule.enabled" size="small" />
                <el-input v-model="rule.name" size="small" placeholder="规则名（如：每20件清洁治具）"
                          class="flex-1 max-w-md" />
              </div>
              <el-button type="danger" size="small" link @click="removePeriodicAction(idx)">删除</el-button>
            </div>

            <div class="grid grid-cols-3 gap-3 mb-3">
              <div>
                <p class="text-xs text-gray-500 mb-1">强制周期 N（每多少轮做一次, 0=关闭）</p>
                <el-input-number v-model="rule.interval" size="small" :min="0" :max="100000"
                                 class="w-full" controls-position="right" />
              </div>
              <div>
                <!-- v3.7.4: 按时间触发 — 解决"生产停了但仍在检测中"场景 -->
                <p class="text-xs text-gray-500 mb-1">超时秒数（多少秒没做就报警, 0=关闭）</p>
                <el-input-number v-model="rule.time_interval_seconds" size="small" :min="0" :max="86400"
                                 class="w-full" controls-position="right"
                                 placeholder="例: 1800 = 30 分钟" />
              </div>
              <div>
                <p class="text-xs text-gray-500 mb-1">计数基准（仅按次数模式生效）</p>
                <el-select v-model="rule.count_basis" size="small" class="w-full">
                  <el-option label="所有 cycle 都计数" value="all" />
                  <el-option label="只数合格 cycle (推荐)" value="good_only" />
                  <el-option label="只数不良 cycle" value="ng_only" />
                </el-select>
              </div>
            </div>
            <p class="text-[0.65rem] text-gray-500 -mt-2 mb-3">
              ★ 两种触发条件可任意组合 — 谁先到期谁先报警。完成动作（做了"完成动作"步骤）会同时重置两个计数。
            </p>

            <div class="mb-3">
              <p class="text-xs text-gray-500 mb-1">完成动作（检测到任一即视为已做）</p>
              <el-select v-model="rule.trigger_step_ids" multiple size="small" class="w-full"
                         placeholder="从已启用步骤中多选">
                <el-option v-for="step in enabledSteps" :key="step.id"
                           :label="step.displayLabel || step.label" :value="step.id" />
              </el-select>
            </div>

            <div class="grid grid-cols-2 gap-3 mb-3">
              <div>
                <p class="text-xs text-gray-500 mb-1">重置策略</p>
                <el-select v-model="rule.reset_policy" size="small" class="w-full">
                  <el-option label="任意时刻做了都重置（默认）" value="always" />
                  <el-option label="只有到期后做才重置（严格）" value="only_when_due" />
                </el-select>
              </div>
              <div>
                <p class="text-xs text-gray-500 mb-1">超期触发频率</p>
                <div class="flex items-center gap-2">
                  <el-select :model-value="getOverdueRepeatMode(rule.overdue_repeat)"
                             @update:model-value="(m) => setOverdueRepeatMode(rule, m)"
                             size="small" class="flex-1">
                    <el-option label="每个 cycle 都触发" value="every_cycle" />
                    <el-option label="只在刚超期时触发一次" value="once" />
                    <el-option label="冷却 5 轮触发一次" value="cooldown:5" />
                    <el-option label="冷却 10 轮触发一次" value="cooldown:10" />
                    <el-option label="冷却 20 轮触发一次" value="cooldown:20" />
                    <el-option label="持续触发（每 N 秒一次）" value="continuous" />
                  </el-select>
                  <el-input-number v-if="String(rule.overdue_repeat || '').startsWith('continuous')"
                                   :model-value="getContinuousSeconds(rule.overdue_repeat)"
                                   @update:model-value="(v) => rule.overdue_repeat = `continuous:${v || 5}`"
                                   :min="1" :max="600" :step="1" size="small"
                                   controls-position="right" style="width: 120px" />
                </div>
                <p v-if="String(rule.overdue_repeat || '').startsWith('continuous')"
                   class="text-[0.65rem] text-orange-500 mt-1">
                  ★ 持续模式：超期后每 N 秒响一次，客户做了"完成动作"立刻停止。
                </p>
              </div>
            </div>

            <div class="grid grid-cols-2 gap-3 mb-3">
              <div>
                <div class="flex items-center justify-between mb-1">
                  <p class="text-xs text-gray-500">到期提醒事件 (counter == N)</p>
                  <el-button type="primary" size="small" link
                             @click="addEventAndBindToRule(rule, 'due_warning_event_id')">
                    + 新建事件
                  </el-button>
                </div>
                <el-select v-model="rule.due_warning_event_id" size="small" class="w-full"
                           clearable placeholder="可选 — 到点提醒一次">
                  <el-option v-for="ev in (project.events_config || [])"
                             :key="ev.id" :label="ev.name" :value="ev.id" />
                </el-select>
              </div>
              <div>
                <div class="flex items-center justify-between mb-1">
                  <p class="text-xs text-gray-500">超期告警事件 (counter > N)</p>
                  <el-button type="primary" size="small" link
                             @click="addEventAndBindToRule(rule, 'overdue_event_id')">
                    + 新建事件
                  </el-button>
                </div>
                <el-select v-model="rule.overdue_event_id" size="small" class="w-full"
                           clearable placeholder="超过 N 轮还没做时触发">
                  <el-option v-for="ev in (project.events_config || [])"
                             :key="ev.id" :label="ev.name" :value="ev.id" />
                </el-select>
              </div>
            </div>

            <!-- v3.5.2: 开机首检 — 每次"开始检测"时立刻触发一次到期提醒 -->
            <div class="flex items-center gap-2 bg-slate-800/60 border border-slate-700 rounded p-2">
              <el-switch v-model="rule.run_on_start" size="small" />
              <div class="flex-1">
                <p class="text-xs text-gray-300">开机首检：每次开始检测时强制做一次</p>
                <p class="text-[0.65rem] text-gray-500 mt-0.5">
                  勾选后，每次点"开始"立刻把 counter 推到 {{ rule.interval || 20 }}
                  并触发到期提醒事件，必须先做完动作才会归零
                </p>
              </div>
            </div>
          </div>

          <div v-if="!project.periodic_actions || project.periodic_actions.length === 0"
               class="text-gray-500 text-center py-4 border border-dashed border-slate-700 rounded">
            暂无规则，点击右上角"新增规则"添加（如：每 20 轮清洁治具）
          </div>
        </div>
      </el-card>

      <!-- Tracking Mode Config -->
      <el-card v-if="project.logic_mode === 'tracking'" shadow="never" class="bg-slate-800 border-slate-700">
        <template #header><span class="font-bold text-white">跟踪模式 - 物品清点配置</span></template>
        <div class="space-y-4 text-sm text-gray-300">
          <p class="text-xs text-gray-400">
            使用{{ project.task_type === 'segmentation' ? '分割+跟踪' : '检测+跟踪' }}为每个物品分配唯一ID，配合周期结束策略进行数量校验。
          </p>

          <div class="space-y-4 text-sm text-gray-300">

            <!-- Row 1: 周期结束策略 -->
            <div>
              <div class="text-xs text-gray-400 mb-1.5">周期结束策略</div>
              <div class="grid grid-cols-4 gap-2">
                <label class="flex items-start gap-2 p-2.5 bg-slate-800 rounded border cursor-pointer transition-colors"
                  :class="project.tracking_cycle_strategy === 'all_gone' ? 'border-cyan-500 bg-cyan-500/10' : 'border-slate-700 hover:border-slate-500'"
                  @click="project.tracking_cycle_strategy = 'all_gone'">
                  <input type="radio" v-model="project.tracking_cycle_strategy" value="all_gone" class="mt-0.5 accent-cyan-500">
                  <div>
                    <div class="text-white text-xs font-bold">全部消失</div>
                    <div class="text-[10px] text-gray-500 mt-0.5">所有物品离开画面后结算</div>
                  </div>
                </label>
                <label class="flex items-start gap-2 p-2.5 bg-slate-800 rounded border cursor-pointer transition-colors"
                  :class="project.tracking_cycle_strategy === 'roi_exit' ? 'border-cyan-500 bg-cyan-500/10' : 'border-slate-700 hover:border-slate-500'"
                  @click="project.tracking_cycle_strategy = 'roi_exit'">
                  <input type="radio" v-model="project.tracking_cycle_strategy" value="roi_exit" class="mt-0.5 accent-cyan-500">
                  <div>
                    <div class="text-white text-xs font-bold">ROI离开</div>
                    <div class="text-[10px] text-gray-500 mt-0.5">物品离开指定检测区域后结算</div>
                  </div>
                </label>
                <label class="flex items-start gap-2 p-2.5 bg-slate-800 rounded border cursor-pointer transition-colors"
                  :class="project.tracking_cycle_strategy === 'trigger' ? 'border-cyan-500 bg-cyan-500/10' : 'border-slate-700 hover:border-slate-500'"
                  @click="project.tracking_cycle_strategy = 'trigger'">
                  <input type="radio" v-model="project.tracking_cycle_strategy" value="trigger" class="mt-0.5 accent-cyan-500">
                  <div>
                    <div class="text-white text-xs font-bold">触发标签</div>
                    <div class="text-[10px] text-gray-500 mt-0.5">检测到指定标签后结算</div>
                  </div>
                </label>
                <label class="flex items-start gap-2 p-2.5 bg-slate-800 rounded border cursor-pointer transition-colors"
                  :class="project.tracking_cycle_strategy === 'container' ? 'border-cyan-500 bg-cyan-500/10' : 'border-slate-700 hover:border-slate-500'"
                  @click="project.tracking_cycle_strategy = 'container'">
                  <input type="radio" v-model="project.tracking_cycle_strategy" value="container" class="mt-0.5 accent-cyan-500">
                  <div>
                    <div class="text-white text-xs font-bold">容器模式</div>
                    <div class="text-[10px] text-gray-500 mt-0.5">每个箱子独立结算</div>
                  </div>
                </label>
              </div>
            </div>

            <!-- Row 2: strategy-specific options -->
            <div v-if="project.tracking_cycle_strategy === 'trigger'" class="flex items-center gap-4 text-xs">
              <span class="text-gray-400 shrink-0">触发标签</span>
              <el-select v-model="project.tracking_trigger_label" size="small" class="!w-40" placeholder="选择标签">
                <el-option v-for="s in nonBackupSteps" :key="s.id" :label="s.displayLabel || s.label" :value="s.label" />
              </el-select>
              <span class="text-gray-400 shrink-0">确认帧数</span>
              <el-input-number v-model="project.tracking_trigger_min_frames" :min="0" :step="5" :precision="2" size="small" class="!w-28" />
            </div>
            <div v-else-if="project.tracking_cycle_strategy === 'container'" class="flex items-center gap-4 text-xs flex-wrap">
              <span class="text-gray-400 shrink-0">容器类别</span>
              <el-select v-model="project.tracking_container_label" size="small" class="!w-40" placeholder="选择容器标签">
                <el-option v-for="s in nonBackupSteps" :key="s.id" :label="s.displayLabel || s.label" :value="s.label" />
              </el-select>
              <span class="text-gray-400 shrink-0">消失确认帧数</span>
              <el-input-number v-model="project.tracking_gone_confirm_frames" :min="0" :step="5" :precision="2" size="small" class="!w-28" />
              <el-tooltip placement="top">
                <template #content>
                  <div style="max-width:280px;line-height:1.5">
                    <b>单箱(默认)</b>: 画面里同时出现两个箱子时只承认最早进入的"主箱",其他箱子被屏蔽,落进它们的物品也不计。主箱出去后下一个箱子自动接管,杜绝"一码两箱"和野生 settle。<br/>
                    <b>多箱(实验性)</b>: 历史行为,允许多个箱子同时被分组。当前 cycle 调度会让一个工件号绑多个箱子,会污染数据,仅用于排查回归。
                  </div>
                </template>
                <span class="text-gray-400 shrink-0 cursor-help">同框策略 ⓘ</span>
              </el-tooltip>
              <el-select v-model="project.container_box_mode" size="small" class="!w-44">
                <el-option label="单箱(只承认主箱)" value="single" />
                <el-option label="多箱(实验性)" value="multi" />
              </el-select>
              <el-tooltip placement="top">
                <template #content>
                  <div style="max-width:300px;line-height:1.5">
                    <b>幽灵箱过滤</b>: 单/多箱模式下, 真箱被遮挡瞬间 ByteTrack 切了 ID,
                    老 ID 仍在 _box_objects 里挂 30 帧 gone-confirm, 期间没新物品落进去
                    (新 ID 接走了), 等结算时件数=0 → 整箱缺件 → 入账 NG。<br/>
                    阈值 = 该箱至少装 N 件才视为真箱; 件数低于阈值的箱子直接丢弃, 不计 OK 不计 NG。<br/>
                    · <b>默认 1</b>: 至少装 1 件才入账 (推荐)<br/>
                    · 设 0: 关闭过滤, 所有箱子都入账 (v3.1.3 行为, 容易出"幽灵 NG")<br/>
                    · 设大: 要求装件更多才入账 (现场遮挡严重时可调高)
                  </div>
                </template>
                <span class="text-gray-400 shrink-0 cursor-help">结算最少件数 ⓘ</span>
              </el-tooltip>
              <el-input-number v-model="project.container_settle_min_items" :min="0" :max="100" :step="1" :precision="2" size="small" class="!w-24" />
              <el-tooltip placement="top">
                <template #content>
                  <div style="max-width:320px;line-height:1.5">
                    <b>ID 漂移合并 (v3.2.0 试验性)</b>: ByteTrack 在工人手部遮挡瞬间常常切换 box 的 track_id;
                    后端 phase2 已有 5 秒窗口的 IoU + 外观再识别, 但跨 5 秒的接管接不住, 老条目仍会被 settle 成幽灵 NG。<br/>
                    本配置 > 0 时, 新 box 即将进入 _box_objects 前先扫已 gone-confirm 中的老条目,
                    位置 IoU ≥ 此阈值就复用老 did 继续累计装件, 不开新条目。<br/>
                    · <b>0 (默认)</b>: 关闭, 兼容老项目<br/>
                    · <b>0.5</b>: 推荐, 合并明显接管<br/>
                    · <b>0.7+</b>: 保守, 位置必须几乎完全重合<br/>
                    · 副作用: 传送带场景下"老箱离开后新箱刚好移到原位置"可能误合并, 谨慎调高阈值。
                  </div>
                </template>
                <span class="text-gray-400 shrink-0 cursor-help">ID漂移合并 IoU ⓘ</span>
              </el-tooltip>
              <el-input-number v-model="project.container_id_drift_merge_iou" :min="0" :max="1" :step="0.05" :precision="2" size="small" class="!w-24" />
            </div>
            <div v-else class="flex items-center gap-4 text-xs">
              <span class="text-gray-400 shrink-0">消失确认帧数</span>
              <el-input-number v-model="project.tracking_gone_confirm_frames" :min="0" :step="5" :precision="2" size="small" class="!w-28" />
            </div>

            <!-- Row 3: switches in one line -->
            <div class="flex items-center gap-5 text-xs flex-wrap">
              <div class="flex items-center gap-1.5">
                <span class="text-gray-400">ID交换检测</span>
                <el-switch v-model="project.tracking_swap_detection" size="small" />
              </div>
              <div class="flex items-center gap-1.5">
                <span class="text-gray-400">外观特征辅助</span>
                <el-switch v-model="project.tracking_appearance_match" size="small" />
              </div>
              <div class="flex items-center gap-1.5">
                <span class="text-gray-400">ID锁定</span>
                <el-switch v-model="project.tracking_id_lock" size="small" />
              </div>
              <div v-if="project.tracking_id_lock" class="flex items-center gap-1.5">
                <span class="text-gray-400">锁定帧数</span>
                <el-input-number v-model="project.tracking_id_lock_frames" :min="0" :step="5" :precision="2" size="small" class="!w-24" />
              </div>
              <div class="flex items-center gap-1.5">
                <el-tooltip placement="top" effect="dark">
                  <template #content>
                    <div class="text-xs leading-relaxed">
                      ByteTrack IoU 匹配阈值<br />
                      · 默认 0.8 (官方默认)<br />
                      · 容器/多目标场景建议 0.5~0.7<br />
                      · 越低越宽松, ID 越稳但可能错连相邻目标
                    </div>
                  </template>
                  <span class="text-gray-400 cursor-help">匹配阈值</span>
                </el-tooltip>
                <el-input-number v-model="project.tracking_match_thresh" :min="0.1" :max="0.99" :step="0.05" :precision="2" size="small" class="!w-24" />
              </div>
              <div class="flex items-center gap-1.5">
                <span class="text-gray-400">顺序检查</span>
                <el-switch v-model="project.tracking_check_order" size="small" />
              </div>
            </div>

            <!-- Row 4: Expected items + ROI side by side -->
            <div class="grid grid-cols-2 gap-4">
              <div>
                <div class="text-xs text-gray-400 mb-1.5">{{ project.tracking_cycle_strategy === 'container' ? '每箱期望物品' : '期望物品清单' }}</div>
                <div class="bg-slate-900 rounded p-2.5 space-y-1.5">
                  <div v-for="(item, idx) in project.counting_expected_list" :key="idx" class="flex items-center gap-1.5 bg-slate-800 p-1.5 rounded">
                    <el-select v-model="item.label" size="small" class="flex-1 min-w-0" placeholder="选择物品">
                      <el-option v-for="s in countableSteps" :key="s.id" :label="s.displayLabel || s.label" :value="s.label" />
                    </el-select>
                    <span class="text-gray-500 text-xs shrink-0">×</span>
                    <el-input-number v-model="item.count" size="small" :min="0" :step="1" :precision="2" class="!w-20 shrink-0" />
                    <el-button type="danger" size="small" link class="shrink-0" @click="project.counting_expected_list.splice(idx, 1)">
                      <el-icon><Delete /></el-icon>
                    </el-button>
                  </div>
                  <el-button type="primary" size="small" text @click="project.counting_expected_list.push({label: '', count: 1})">+ 添加物品</el-button>
                </div>
              </div>
              <div>
                <div class="text-xs text-gray-400 mb-1.5">检测区域 (ROI)</div>
                <div class="bg-slate-900 rounded p-2.5 space-y-2">
                  <div class="flex items-center gap-2">
                    <el-button type="primary" size="small" @click="$emit('open-roi-editor')">
                      {{ project.tracking_roi_polygon?.length > 2 ? '重新绘制' : '设置区域' }}
                    </el-button>
                    <el-button v-if="project.tracking_roi_polygon?.length > 2" type="danger" size="small" plain @click="project.tracking_roi_polygon = []">清除</el-button>
                    <span v-if="project.tracking_roi_polygon?.length > 2" class="text-xs text-green-400">
                      已设置 {{ project.tracking_roi_polygon.length }} 个顶点
                    </span>
                    <span v-else class="text-xs text-gray-500">未设置（全画面）</span>
                  </div>
                  <div v-if="project.tracking_roi_polygon?.length > 2" class="relative w-full h-28 bg-slate-800 rounded border border-slate-700 overflow-hidden">
                    <canvas ref="roiPreviewCanvas" class="w-full h-full"></canvas>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </el-card>

      <!-- Per-Item Mode Config (v3.8+) -->
      <el-card v-if="project.logic_mode === 'per_item'" shadow="never" class="bg-slate-800 border-slate-700">
        <template #header><span class="font-bold text-white">逐件覆盖 — 通用参数</span></template>
        <div class="space-y-4 text-sm text-gray-300">
          <p class="text-xs text-gray-400">
            每个周期内追踪「同类多件物品」(例: N 颗螺丝), 要求每件都被某个动作(例: 打/划)依次覆盖一次。
            所有件齐 → 合格(OK); 单件超时未覆盖 → 不良(NG)。
          </p>

          <!-- 稳定窗口 -->
          <div class="grid grid-cols-2 gap-4">
            <div>
              <div class="text-xs text-gray-400 mb-1.5">连续稳定帧数</div>
              <el-input-number
                v-model="project.pipeline_config.per_item.stability_window_frames"
                size="small" :min="1" :step="1" :precision="0" class="!w-full" />
              <div class="text-[10px] text-gray-500 mt-1">画面里目标的数量+位置稳定多少帧后, 才认为「场景已就绪」, 自动开新周期</div>
            </div>
            <div>
              <div class="text-xs text-gray-400 mb-1.5">位置稳定阈值(重合度)</div>
              <el-input-number
                v-model="project.pipeline_config.per_item.stability_iou_threshold"
                size="small" :min="0.1" :max="0.99" :step="0.05" :precision="2" class="!w-full" />
              <div class="text-[10px] text-gray-500 mt-1">两帧间同件目标的重合度需高于此值才算「位置稳定」; 默认 0.7</div>
            </div>
          </div>

          <!-- 超时 + 锁数量 -->
          <div class="grid grid-cols-2 gap-4">
            <div>
              <div class="text-xs text-gray-400 mb-1.5">单件离开多少秒才算消失</div>
              <el-input-number
                v-model="project.pipeline_config.per_item.item_timeout_seconds"
                size="small" :min="0" :step="0.5" :precision="2" class="!w-full" />
              <div class="text-[10px] text-gray-500 mt-1">某件目标在画面里失踪多少秒, 才认为离开了; 0 = 不限</div>
            </div>
            <div>
              <div class="text-xs text-gray-400 mb-1.5">周期开始就锁定数量</div>
              <el-switch v-model="project.pipeline_config.per_item.lock_count_on_start" />
              <div class="text-[10px] text-gray-500 mt-1">开启 → 周期开始那一刻看到几件就是"应有数量", 中途新出现的不算 (推荐开启)</div>
            </div>
          </div>

          <!-- 收尾标签 / 双条件 已迁入下方「结算触发方式」唯一入口, 此处不再重复 -->

          <!-- v3.9+ 新增: 稳定性进阶 -->
          <div class="border-t border-slate-700 pt-3 mt-3">
            <div class="text-xs text-gray-400 mb-2 font-bold">稳定性进阶</div>

            <!-- v3.10.2+ 严格等量触发开关 (一行高亮, 醒目) -->
            <div class="mb-3 px-3 py-2 bg-slate-900/60 border border-slate-700 rounded">
              <div class="flex items-center justify-between gap-3">
                <div class="flex-1">
                  <div class="text-[12px] font-bold text-cyan-300">所有工序同时达标才开周期</div>
                  <div class="text-[10px] text-gray-500 mt-0.5">
                    开启: 每个工序的物件数都要同时达到"开周期门槛"才开 (例: 5N 看到 8 颗 + 7N 看到 2 颗 + 涂黑组 看到 9 颗, 同一时刻满足) <br/>
                    关闭: 仅看首步达标即开周期 (其他工序锁多少件靠开周期那一帧)
                  </div>
                </div>
                <el-switch
                  v-model="project.pipeline_config.per_item.require_exact_count"
                  active-text="严格等量"
                  inactive-text="宽松触发"
                  inline-prompt
                  size="default" />
              </div>
            </div>

            <div class="grid grid-cols-3 gap-4">
              <div>
                <div class="text-[11px] text-gray-400 mb-1">允许漏检几件</div>
                <el-input-number
                  v-model="project.pipeline_config.per_item.stability_count_tolerance"
                  size="small" :min="0" :step="1" :precision="0" class="!w-full" />
                <div class="text-[10px] text-gray-500 mt-1">开周期门槛: 检出数 ≥ "已知件数 - N" 即放行 (默认 0). 严格等量下也生效, 控制每步达标阈值.</div>
              </div>
              <div>
                <div class="text-[11px] text-gray-400 mb-1">最低检出比例</div>
                <el-input-number
                  v-model="project.pipeline_config.per_item.stability_count_ratio"
                  size="small" :min="0.1" :max="1.0" :step="0.05" :precision="2" class="!w-full" />
                <div class="text-[10px] text-gray-500 mt-1">开周期门槛: 检出数 ≥ "已知件数 × 此比例" 即放行 (默认 0.85 = 允许 15% 漏检). 严格等量下也生效.</div>
              </div>
              <div>
                <div class="text-[11px] text-gray-400 mb-1">周期开始后补锁定窗口(秒)</div>
                <el-input-number
                  v-model="project.pipeline_config.per_item.lock_lookahead_seconds"
                  size="small" :min="0" :step="0.5" :precision="2" class="!w-full" />
                <div class="text-[10px] text-gray-500 mt-1">周期开始后 N 秒内, 继续吸收"新位置"的件 (用于被遮挡漏锁的件)</div>
              </div>
            </div>
          </div>

          <!-- v3.9+ 新增: 结算时机 (per_item 专属, 与其他模式隔离) -->
          <div class="border-t border-slate-700 pt-3 mt-3">
            <div class="text-xs text-gray-400 mb-2 font-bold">结算时机 <span class="text-amber-400 font-normal">(仅"逐件覆盖"模式生效, 不影响其他模式)</span></div>

            <!-- v3.10.2+ 手动结算模式总开关 (一行高亮, 醒目) -->
            <div class="mb-3 px-3 py-2 bg-slate-900/60 border border-slate-700 rounded">
              <div class="flex items-center justify-between gap-3">
                <div class="flex-1">
                  <div class="text-[12px] font-bold text-amber-300">手动结算模式</div>
                  <div class="text-[10px] text-gray-500 mt-0.5">
                    开启后: 自动 OK / 周期超时 NG / 空闲超时 NG / 收尾标签触发, <b class="text-amber-400">全部禁用</b>, 只能去 Monitor 页头部点「手动结算」按钮触发<br/>
                    关闭 (默认): 下面四个自动结算策略按各自参数生效
                  </div>
                </div>
                <el-switch
                  v-model="project.pipeline_config.per_item.disable_auto_settle"
                  active-text="纯手动"
                  inactive-text="自动结算"
                  inline-prompt
                  size="default" />
              </div>
            </div>

            <!-- 结算触发方式: 唯一的结算选择入口 (C 全部完成 与 A/B 信号 互斥; A/B 可叠加=双条件) -->
            <div class="mb-3 px-3 py-2 bg-slate-900/60 border border-slate-700 rounded">
              <div class="text-[12px] font-bold text-cyan-300 mb-2">结算触发方式
                <span class="text-gray-500 font-normal text-[10px]">— 选一种; "信号触发"下勾两个 = 双条件结算</span>
              </div>
              <el-radio-group
                v-model="project.pipeline_config.per_item._settle_mode"
                :disabled="project.pipeline_config.per_item.disable_auto_settle">
                <div class="flex flex-col gap-1.5 w-full">
                  <!-- C: 全部覆盖完成即 OK -->
                  <el-radio label="alldone">全部覆盖完成即结算 <span class="text-gray-500 text-[10px]">(所有件打完就判 OK, 不等任何信号)</span></el-radio>
                  <div class="ml-6 mb-1 flex items-center gap-2"
                       v-if="project.pipeline_config.per_item._settle_mode === 'alldone'">
                    <span class="text-[11px] text-gray-400">全部打完后保持</span>
                    <el-input-number
                      v-model="project.pipeline_config.per_item.settle_after_all_done_sec"
                      size="small" :min="0.5" :step="0.5" :precision="2" class="!w-28"
                      :disabled="project.pipeline_config.per_item.disable_auto_settle" />
                    <span class="text-[11px] text-gray-400">秒 → 自动 OK</span>
                  </div>
                  <!-- A/B: 信号触发 -->
                  <el-radio label="signal">信号触发结算 <span class="text-gray-500 text-[10px]">(下方两项可单选或都选)</span></el-radio>
                  <div class="ml-6 flex flex-col gap-2"
                       v-if="project.pipeline_config.per_item._settle_mode === 'signal'">
                    <!-- B 步骤标签结算 -->
                    <div class="flex items-center gap-2 flex-wrap">
                      <el-checkbox
                        v-model="project.pipeline_config.per_item._settle_by_step"
                        :disabled="project.pipeline_config.per_item.disable_auto_settle">步骤标签结算</el-checkbox>
                      <el-select
                        v-model="project.pipeline_config.per_item._finish_label_choice"
                        size="small" class="!w-44" placeholder="选结算标签" clearable
                        :disabled="!project.pipeline_config.per_item._settle_by_step || project.pipeline_config.per_item.disable_auto_settle">
                        <el-option v-for="s in nonBackupSteps" :key="s.id" :label="s.displayLabel || s.label" :value="s.label" />
                      </el-select>
                      <span class="text-[11px] text-gray-400">连续</span>
                      <el-input-number
                        v-model="project.pipeline_config.per_item.finish_sustain_frames"
                        size="small" :min="1" :step="1" :precision="0" class="!w-24"
                        :disabled="!project.pipeline_config.per_item._settle_by_step || project.pipeline_config.per_item.disable_auto_settle" />
                      <span class="text-[11px] text-gray-400">帧确认</span>
                    </div>
                    <!-- A 物品标签消失结算 -->
                    <div class="flex items-center gap-2 flex-wrap">
                      <el-checkbox
                        v-model="project.pipeline_config.per_item._settle_by_item"
                        :disabled="project.pipeline_config.per_item.disable_auto_settle">物品标签消失结算</el-checkbox>
                      <span class="text-[11px] text-gray-400">离场确认</span>
                      <el-input-number
                        v-model="project.pipeline_config.per_item.leave_confirm_frames"
                        size="small" :min="1" :step="5" :precision="0" class="!w-24"
                        :disabled="!project.pipeline_config.per_item._settle_by_item || project.pipeline_config.per_item.disable_auto_settle" />
                      <span class="text-[11px] text-gray-400">帧</span>
                    </div>
                    <div class="text-[10px] text-amber-300/80">两个都勾 = 双条件: 必须"标签出现"且"工件离场"才结算 (防遮挡/停顿误判提前结算)</div>
                  </div>
                </div>
              </el-radio-group>
            </div>

            <!-- 兜底 NG 安全网 (防卡死, 与结算方式独立) -->
            <div class="grid grid-cols-2 gap-4">
              <div>
                <div class="text-[11px] text-gray-400 mb-1">无动作多少秒判 NG</div>
                <el-input-number
                  v-model="project.pipeline_config.per_item.idle_timeout_sec"
                  size="small" :min="0" :step="1" :precision="0" class="!w-full"
                  :disabled="project.pipeline_config.per_item.disable_auto_settle" />
                <div class="text-[10px] text-gray-500 mt-1">工人停手 N 秒无任何动作 → 强制 NG; 0 = 不限<span v-if="project.pipeline_config.per_item.disable_auto_settle" class="text-amber-400"> · 手动模式下忽略</span></div>
              </div>
              <div>
                <div class="text-[11px] text-gray-400 mb-1">单周期最长几秒 (兜底)</div>
                <el-input-number
                  v-model="project.pipeline_config.per_item.cycle_max_duration_sec"
                  size="small" :min="0" :step="10" :precision="0" class="!w-full"
                  :disabled="project.pipeline_config.per_item.disable_auto_settle" />
                <div class="text-[10px] text-gray-500 mt-1">周期开始后超 N 秒未结算 → 强制 NG 防卡死; 0 = 不限<span v-if="project.pipeline_config.per_item.disable_auto_settle" class="text-amber-400"> · 手动模式下忽略</span></div>
              </div>
              <div>
                <div class="text-[11px] text-gray-400 mb-1">换板兜底: 工件整体消失几帧</div>
                <el-input-number
                  v-model="project.pipeline_config.per_item.workpiece_absent_settle_frames"
                  size="small" :min="0" :step="1" :precision="0" class="!w-full"
                  :disabled="project.pipeline_config.per_item.disable_auto_settle" />
                <div class="text-[10px] text-gray-500 mt-1">全部工件标签连续消失 N 帧 → 判已取走/换板, 按真实覆盖兜底结算并重锁, 防上一板覆盖态泄漏到新板 (未打却变绿); 0 = 关<span v-if="project.pipeline_config.per_item.disable_auto_settle" class="text-amber-400"> · 手动模式下忽略</span></div>
              </div>
            </div>
            <div class="text-[10px] text-gray-500 mt-2">
              ※ 结算方式与兜底只在"逐件覆盖"模式生效, 跟其他模式(顺序/检测/跟踪)同名参数完全独立<br/>
              ※ 开"手动结算模式"后以上全部禁用, 只能去 Monitor 页点「手动结算」
            </div>
          </div>

          <!-- v3.28+ 判定时机 (与"结算时机"解耦, 默认 = 结算时判 = 老项目零差异) -->
          <div class="border-t border-slate-700 pt-3 mt-3">
            <div class="text-xs text-gray-400 mb-2 font-bold">判定时机
              <span class="text-amber-400 font-normal">(给工人看的"绿/红灯"什么时候亮; 默认 = 跟结算同一刻)</span>
            </div>
            <div class="px-3 py-2 bg-slate-900/60 border border-slate-700 rounded">
              <el-radio-group v-model="project.pipeline_config.per_item.judge_timing">
                <div class="flex flex-col gap-1.5 w-full">
                  <el-radio label="on_settle">结算时判定 <span class="text-gray-500 text-[10px]">(默认: 不单设, 取走/收尾那刻一次性判 OK/NG)</span></el-radio>
                  <el-radio label="all_done">全部覆盖完成时判定 <span class="text-gray-500 text-[10px]">(所有件打完即亮绿合格, 保持到取走才落账; 漏件红灯仍由结算那刻给)</span></el-radio>
                  <div class="ml-6 mb-1 flex items-center gap-2"
                       v-if="project.pipeline_config.per_item.judge_timing === 'all_done'">
                    <span class="text-[11px] text-gray-400">全部打完后保持</span>
                    <el-input-number
                      v-model="project.pipeline_config.per_item.judge_all_done_sec"
                      size="small" :min="0" :step="0.5" :precision="2" class="!w-24" />
                    <span class="text-[11px] text-gray-400">秒 → 亮绿</span>
                  </div>
                  <el-radio label="label">指定动作标签出现时判定 <span class="text-gray-500 text-[10px]">(出现该标签即拍快照: 全覆盖亮绿 / 有漏亮红+漏点, 补满翻绿)</span></el-radio>
                  <el-radio label="manual">手动点击判定 <span class="text-gray-500 text-[10px]">(检测主页「手动判定」按钮触发拍快照; 不自动判, 全靠操作员点)</span></el-radio>
                  <div class="ml-6 flex items-center gap-2 flex-wrap"
                       v-if="project.pipeline_config.per_item.judge_timing === 'label'">
                    <span class="text-[11px] text-gray-400">判定标签</span>
                    <el-select
                      v-model="project.pipeline_config.per_item.judge_label"
                      size="small" class="!w-44" placeholder="选判定触发标签" clearable>
                      <el-option v-for="s in nonBackupSteps" :key="s.id" :label="s.displayLabel || s.label" :value="s.label" />
                    </el-select>
                    <span class="text-[11px] text-gray-400">连续</span>
                    <el-input-number
                      v-model="project.pipeline_config.per_item.judge_label_frames"
                      size="small" :min="1" :step="1" :precision="0" class="!w-20" />
                    <span class="text-[11px] text-gray-400">帧确认</span>
                  </div>
                </div>
              </el-radio-group>
              <!-- 判定合格(绿灯)事件: 与落账"合格"事件分开 -->
              <div class="mt-2 flex items-center gap-2 flex-wrap"
                   v-if="project.pipeline_config.per_item.judge_timing !== 'on_settle'">
                <span class="text-[11px] text-gray-400">判定合格亮绿事件</span>
                <el-select
                  v-model="project.pipeline_config.per_item.judge_ok_event_id"
                  size="small" class="!w-56" clearable placeholder="可选 — 判合格时触发(亮绿灯)">
                  <el-option v-for="ev in (project.events_config || [])"
                             :key="ev.id" :label="`${ev.name} (id=${ev.id})`" :value="ev.id" />
                </el-select>
                <span class="text-[10px] text-gray-500">去「报警配置」把此事件映射到绿灯; 留空=不主动亮绿。红灯/漏点复用下方「漏打补做」的待补提示事件</span>
              </div>
            </div>
          </div>

          <!-- v3.27+ 漏打补做 / 框色高亮 (打螺丝漏打场景专用, 默认全关 = 老项目零差异; 离场触发已迁至上方"结算触发方式") -->
          <div class="border-t border-slate-700 pt-3 mt-3">
            <div class="text-xs text-gray-400 mb-2 font-bold">漏打补做 · 框色高亮 <span class="text-amber-400 font-normal">(打螺丝漏打场景, 默认全关; 离场触发去上方「结算触发方式」)</span></div>

            <div class="grid grid-cols-2 gap-4">
              <div>
                <div class="text-[11px] text-gray-400 mb-1">待补超时(秒)</div>
                <el-input-number
                  v-model="project.pipeline_config.per_item.remediation_timeout_sec"
                  size="small" :min="0" :step="1" :precision="0" class="!w-full"
                  :disabled="!project.pipeline_config.per_item.ng_hold_for_remediation" />
                <div class="text-[10px] text-gray-500 mt-1">待补态超过 N 秒没补满 → 自动按 NG 落账; 0 = 不限, 只能补满或人工确认</div>
              </div>
              <div>
                <div class="text-[11px] text-gray-400 mb-1">待补提示事件 (点哪盏灯/响不响)</div>
                <el-select
                  v-model="project.pipeline_config.per_item.remediation_event_id"
                  size="small" class="!w-full" clearable placeholder="可选 — 进入待补时触发"
                  :disabled="!project.pipeline_config.per_item.ng_hold_for_remediation">
                  <el-option v-for="ev in (project.events_config || [])"
                             :key="ev.id" :label="`${ev.name} (id=${ev.id})`" :value="ev.id" />
                </el-select>
                <div class="text-[10px] text-gray-500 mt-1">进入"还有没扭螺丝"待补态时触发该事件; 灯色/蜂鸣去「报警配置」把此事件映射到红灯。留空=不主动点灯</div>
              </div>
            </div>

            <!-- 漏打挂起待补开关 -->
            <div class="mt-3 px-3 py-2 bg-slate-900/60 border border-slate-700 rounded">
              <div class="flex items-center justify-between gap-3">
                <div class="flex-1">
                  <div class="text-[12px] font-bold text-amber-300">漏打挂起待补 (NG 保护)</div>
                  <div class="text-[10px] text-gray-500 mt-0.5">
                    开启后: 离场时若有漏打, <b class="text-amber-400">不立即记 NG</b>, 先进"待补"态点灯提示漏哪颗 —— 工人放回补满则转 OK; 否则超时/人工点「确认 NG」才落账<br/>
                    关闭 (默认): 离场时漏打直接记 NG
                  </div>
                </div>
                <el-switch
                  v-model="project.pipeline_config.per_item.ng_hold_for_remediation"
                  active-text="挂起待补" inactive-text="直接NG" inline-prompt size="default"
                  :disabled="!(project.pipeline_config.per_item._settle_mode === 'signal' && project.pipeline_config.per_item._settle_by_item)" />
              </div>
            </div>

            <!-- 红灯内取件算 NG -->
            <div class="mt-3 px-3 py-2 bg-slate-900/60 border border-slate-700 rounded">
              <div class="flex items-center justify-between gap-3">
                <div class="flex-1">
                  <div class="text-[12px] font-bold text-amber-300">红灯内取件算 NG</div>
                  <div class="text-[10px] text-gray-500 mt-0.5">
                    开启后: 待补态(红灯)期间工人<b class="text-amber-400">没补满就再次拿取(出现收尾标签)把件带走 → 自动按 NG 落账</b><br/>
                    关闭 (默认): 待补态只能靠补满转 OK / 超时 / 人工「确认 NG」落账
                  </div>
                </div>
                <el-switch
                  v-model="project.pipeline_config.per_item.remediation_takeaway_ng"
                  active-text="取件即NG" inactive-text="不处理" inline-prompt size="default"
                  :disabled="!project.pipeline_config.per_item.ng_hold_for_remediation" />
              </div>
            </div>

            <!-- 待补报警形式: 单次 / 持续 -->
            <div class="mt-3 px-3 py-2 bg-slate-900/60 border border-slate-700 rounded">
              <div class="flex items-center justify-between gap-3 mb-2">
                <div class="flex-1">
                  <div class="text-[12px] font-bold text-amber-300">待补报警形式</div>
                  <div class="text-[10px] text-gray-500 mt-0.5">
                    单次: 进入待补态只触发一次报警事件; 持续: 按下方间隔重复触发, 直到补满/确认。撤报警靠 OK/NG 事件去「报警配置」联动
                  </div>
                </div>
                <el-radio-group
                  v-model="project.pipeline_config.per_item.remediation_alarm_mode"
                  size="small"
                  :disabled="!project.pipeline_config.per_item.ng_hold_for_remediation">
                  <el-radio-button label="once">单次触发</el-radio-button>
                  <el-radio-button label="sustained">持续触发</el-radio-button>
                </el-radio-group>
              </div>
              <div class="flex items-center gap-2">
                <span class="text-[11px] text-gray-400">持续间隔(秒)</span>
                <el-input-number
                  v-model="project.pipeline_config.per_item.remediation_alarm_interval_sec"
                  size="small" :min="0.5" :step="0.5" :precision="1" class="!w-40"
                  :disabled="!project.pipeline_config.per_item.ng_hold_for_remediation || project.pipeline_config.per_item.remediation_alarm_mode !== 'sustained'" />
                <span class="text-[10px] text-gray-500">每隔 N 秒重复触发一次 (持续模式生效, 推荐 2~5)</span>
              </div>
            </div>

            <!-- 框色高亮 -->
            <div class="mt-3 px-3 py-2 bg-slate-900/60 border border-slate-700 rounded">
              <div class="flex items-center justify-between gap-3 mb-2">
                <div class="flex-1">
                  <div class="text-[12px] font-bold text-cyan-300">检测框按覆盖态上色</div>
                  <div class="text-[10px] text-gray-500 mt-0.5">开启后 Monitor 画面里每颗螺丝的框: 已扭→绿 / 未扭→红, 一眼看出漏哪颗。关闭 (默认) 走全局 OK/NG 色</div>
                </div>
                <el-switch v-model="project.pipeline_config.per_item.color_by_coverage"
                  active-text="按覆盖" inactive-text="全局色" inline-prompt size="default" />
              </div>
              <div class="grid grid-cols-2 gap-4">
                <div class="flex items-center gap-2">
                  <span class="text-[11px] text-gray-400">已覆盖(已扭)</span>
                  <el-color-picker v-model="project.pipeline_config.per_item.box_color_covered"
                    size="small" :disabled="!project.pipeline_config.per_item.color_by_coverage" />
                  <span class="text-[10px] text-gray-500">留空=绿</span>
                </div>
                <div class="flex items-center gap-2">
                  <span class="text-[11px] text-gray-400">未覆盖(未扭)</span>
                  <el-color-picker v-model="project.pipeline_config.per_item.box_color_uncovered"
                    size="small" :disabled="!project.pipeline_config.per_item.color_by_coverage" />
                  <span class="text-[10px] text-gray-500">留空=红</span>
                </div>
              </div>
            </div>

            <!-- v3.28+ 显示螺丝编号 -->
            <div class="mt-3 px-3 py-2 bg-slate-900/60 border border-slate-700 rounded flex items-center justify-between gap-3">
              <div class="flex-1">
                <div class="text-[12px] font-bold text-cyan-300">显示螺丝编号</div>
                <div class="text-[10px] text-gray-500 mt-0.5">开启后 Monitor 画面里每颗螺丝框上叠"#编号"(按空间序: 上→下、左→右)。不同标签各自从 #1 起 (如 5N螺丝#3 / 7N螺丝#1)。关闭=不显示</div>
              </div>
              <el-switch v-model="project.pipeline_config.per_item.show_item_numbers"
                active-text="显示" inactive-text="不显示" inline-prompt size="default" />
            </div>

            <!-- 重复打同一颗螺丝防护 -->
            <div class="mt-3 px-3 py-2 bg-slate-900/60 border border-slate-700 rounded">
              <div class="flex items-center justify-between gap-3">
                <div class="flex-1">
                  <div class="text-[12px] font-bold text-amber-300">防止重复打同一颗螺丝</div>
                  <div class="text-[10px] text-gray-500 mt-0.5">
                    开启后: 一颗螺丝已打过 (已覆盖), 系统先等待<b class="text-amber-400">该螺丝重新清晰出现，或电枪明确移到另一颗</b>作为抬枪正证据；随后电枪再回到已打 ID 才判重复 → 报警灯响 (复用 NG 报警) + 画面黄条提示。仅动作框漏检、目标仍被枪遮挡的卡枪不会报警。<b class="text-amber-400">待补态也生效</b> (回头重打已打的螺丝照样报, 补打漏掉的不报)<br/>
                    关闭 (默认): 打过的螺丝再打不做任何处理
                  </div>
                </div>
                <el-switch v-model="project.pipeline_config.per_item.duplicate_screw_alarm"
                  active-text="防重复打" inactive-text="不处理" inline-prompt size="default" />
              </div>
              <div class="grid grid-cols-2 gap-4 mt-2">
                <div class="flex items-center gap-2">
                  <span class="text-[11px] text-gray-400">抬枪确认帧数</span>
                  <el-input-number
                    v-model="project.pipeline_config.per_item.duplicate_release_frames"
                    size="small" :min="1" :step="1" :precision="0" class="!w-32"
                    :disabled="!project.pipeline_config.per_item.duplicate_screw_alarm" />
                  <span class="text-[10px] text-gray-500">已打螺丝重新出现或电枪移到另一颗，连续几帧才确认抬枪 (纯漏检不累计, 推荐 8)</span>
                </div>
                <div class="flex items-center gap-2">
                  <span class="text-[11px] text-gray-400">返回确认帧数</span>
                  <el-input-number
                    v-model="project.pipeline_config.per_item.duplicate_sustain_frames"
                    size="small" :min="1" :step="1" :precision="0" class="!w-32"
                    :disabled="!project.pipeline_config.per_item.duplicate_screw_alarm" />
                  <span class="text-[10px] text-gray-500">确认抬枪后，电枪返回已打 ID 几帧算重复 (小=灵敏, 推荐 2)</span>
                </div>
                <div class="flex items-center gap-2">
                  <span class="text-[11px] text-gray-400">报警节流(秒)</span>
                  <el-input-number
                    v-model="project.pipeline_config.per_item.duplicate_alarm_interval_sec"
                    size="small" :min="0" :step="0.5" :precision="1" class="!w-32"
                    :disabled="!project.pipeline_config.per_item.duplicate_screw_alarm" />
                  <span class="text-[10px] text-gray-500">两次报警最小间隔, 防连响 (推荐 2)</span>
                </div>
                <div class="flex items-center gap-2">
                  <span class="text-[11px] text-gray-400">提示存在时间(秒)</span>
                  <el-input-number
                    v-model="project.pipeline_config.per_item.duplicate_warning_display_sec"
                    size="small" :min="0" :step="0.5" :precision="1" class="!w-32"
                    :disabled="!project.pipeline_config.per_item.duplicate_screw_alarm" />
                  <span class="text-[10px] text-gray-500">黄条提示显示多久后自动撤下 (0=持续到周期结束/下次刷新, 推荐 3)</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </el-card>

      <!-- Per-Item Mode: Step-Level Config (v3.8+) -->
      <el-card v-if="project.logic_mode === 'per_item'" shadow="never" class="bg-slate-800 border-slate-700">
        <template #header><span class="font-bold text-white">逐件覆盖 — 每个步骤的角色</span></template>
        <div class="space-y-3 text-sm text-gray-300">
          <p class="text-xs text-gray-400">
            每个步骤只有两种角色 (二选一): <span class="text-cyan-400">「覆盖动作」</span> 或 <span class="text-cyan-400">「目标」</span>。
          </p>
          <ul class="text-[11px] text-gray-400 pl-4 list-disc space-y-0.5">
            <li><span class="text-cyan-300">覆盖动作</span>: 要持续重叠某个"目标"才算完成 (例: 扭螺丝 → 覆盖 5N螺丝)。把下面的开关 <span class="text-emerald-400">打开</span>。</li>
            <li><span class="text-cyan-300">目标</span>: 被动作覆盖的对象 (例: 5N螺丝、7N螺丝)。<span class="text-amber-400">不参与覆盖判定</span>, 只显示, 把开关 <span class="text-rose-400">关掉</span>。</li>
            <li><span class="text-amber-300">收尾信号 (如"放置"/"翻面")</span>: 不是"覆盖动作", 不在这里开关 — 请到上方"收尾动作"下拉里配, 出现即结算周期。</li>
          </ul>

          <div
            v-for="step in (project.steps_config || []).filter(s => s.enabled)"
            :key="'pi-step-'+step.id"
            class="border border-slate-700 rounded p-3 bg-slate-900/40">
            <div class="flex items-center gap-2 mb-3">
              <span class="text-cyan-400 font-bold">{{ step.displayLabel || step.label || '(未命名步骤)' }}</span>
              <el-switch
                :model-value="!!step.per_item"
                @update:model-value="(v) => {
                  if (v) {
                    step.per_item = step.per_item || {
                      item_label: '',
                      action_label: step.label || '',
                      item_tracking_iou: 0.3,
                      coverage_iou: 0.3,
                      coverage_use_center: false,
                      sustain_frames: 5,
                      completion: 'all_covered',
                      min_item_count: 'auto',
                    };
                  } else {
                    delete step.per_item;
                  }
                }"
                size="small" active-text="设为「覆盖动作」" inactive-text="保留为「目标」" />
            </div>

            <div v-if="step.per_item" class="grid grid-cols-2 gap-3">
              <div>
                <div class="text-[11px] text-gray-400 mb-1">这个覆盖动作要盖哪些目标？<span class="text-amber-400">(可多选 = "或")</span></div>
                <el-select
                  :model-value="_pi_itemLabelToArray(step.per_item.item_label)"
                  @update:model-value="(v) => { step.per_item.item_label = _pi_itemLabelFromArray(v); }"
                  size="small" multiple filterable allow-create default-first-option
                  collapse-tags collapse-tags-tooltip
                  :placeholder="(project.model_labels || []).length ? '从模型类别里选' : '先选主模型才能列出类别'"
                  class="!w-full">
                  <el-option
                    v-for="lbl in (project.model_labels || [])"
                    :key="lbl"
                    :label="lbl"
                    :value="lbl" />
                </el-select>
                <div v-if="!(project.model_labels || []).length" class="text-[10px] text-amber-400 mt-1">
                  ⚠ 模型类别为空：请到"基础设置"tab 选择主模型并点"保存配置"后再来配
                </div>
                <div v-else class="text-[10px] text-gray-500 mt-1">画面里出现任一标签都算作"目标"; 多选表示"或"的关系(例: 涂黑工序覆盖 5N螺丝+7N螺丝)</div>
              </div>
              <div>
                <div class="text-[11px] text-gray-400 mb-1">用哪个标签作为"覆盖动作"？</div>
                <el-select
                  v-model="step.per_item.action_label"
                  size="small" filterable allow-create default-first-option
                  :placeholder="(project.model_labels || []).length ? '从模型类别里选' : '先选主模型才能列出类别'"
                  class="!w-full">
                  <el-option
                    v-for="lbl in (project.model_labels || [])"
                    :key="lbl"
                    :label="lbl"
                    :value="lbl" />
                </el-select>
                <div class="text-[10px] text-gray-500 mt-1">这个标签持续跟某个目标重叠 → 该目标算"被覆盖"</div>
              </div>
              <div>
                <div class="text-[11px] text-gray-400 mb-1">每周期已知有几件目标？</div>
                <el-input-number
                  v-model="step.per_item.expected_count"
                  size="small" :min="0" :step="1" :precision="0" class="!w-full" placeholder="0 = 自动" />
                <div class="text-[10px] text-gray-500 mt-1">填已知数量(如 5N螺丝=14) → 周期开始判定大幅简化; 填 0 = 走"自动稳定窗口"</div>
              </div>
              <div>
                <div class="text-[11px] text-gray-400 mb-1">同件跨帧匹配松紧</div>
                <el-input-number
                  v-model="step.per_item.item_tracking_iou"
                  size="small" :min="0.1" :max="0.95" :step="0.05" :precision="2" class="!w-full" />
                <div class="text-[10px] text-gray-500 mt-1">越大越严, 防止把相邻两件认成同一件; 默认 0.3</div>
              </div>
              <div>
                <div class="text-[11px] text-gray-400 mb-1">动作框与目标框的重合度</div>
                <el-input-number
                  v-model="step.per_item.coverage_iou"
                  size="small" :min="0.1" :max="0.95" :step="0.05" :precision="2" class="!w-full" />
                <div class="text-[10px] text-gray-500 mt-1">重合度 ≥ 此值才计入"覆盖"; 默认 0.3, 动作大可调低如 0.2</div>
              </div>
              <div>
                <div class="text-[11px] text-gray-400 mb-1">需连续多少帧才算覆盖</div>
                <el-input-number
                  v-model="step.per_item.sustain_frames"
                  size="small" :min="1" :step="1" :precision="0" class="!w-full" />
                <div class="text-[10px] text-gray-500 mt-1">动作框与目标重叠连续达到 N 帧, 才确认"已覆盖"; 默认 5</div>
              </div>
              <div>
                <div class="text-[11px] text-gray-400 mb-1">至少多少件目标才开始周期</div>
                <el-input
                  v-model="step.per_item.min_item_count"
                  size="small" placeholder="留 auto 即可" />
                <div class="text-[10px] text-gray-500 mt-1">填数字 或 'auto'; 上方填了"已知有几件"后, 此项自动失效</div>
              </div>
              <div class="col-span-2 px-2 py-1.5 bg-slate-900/60 border border-slate-700 rounded">
                <div class="flex items-center justify-between gap-3">
                  <div class="flex-1">
                    <div class="text-[11px] font-bold text-cyan-300">小物件场景: 用"中心点"判定覆盖</div>
                    <div class="text-[10px] text-gray-500 mt-0.5">
                      适用 <span class="text-amber-300">涂黑 / 喷漆 / 扫码贴标</span> 这类"动作框远大于目标"的场景<br/>
                      开启: 目标中心点落在动作框内即算覆盖 (上方"重合度"自动忽略)<br/>
                      关闭 (默认): 用"重合度"判定 (适合 扭螺丝 这类目标与动作框大小相近的场景)
                    </div>
                  </div>
                  <el-switch v-model="step.per_item.coverage_use_center"
                    active-text="中心点" inactive-text="重合度" inline-prompt size="default" />
                </div>
              </div>
            </div>
          </div>

          <div v-if="(project.steps_config || []).filter(s => s.enabled).length === 0"
               class="text-gray-500 text-center py-4 border border-dashed border-slate-700 rounded">
            暂无启用的步骤,请先在「步骤设置」tab 添加步骤
          </div>
        </div>
      </el-card>

      <!-- ==================== 结算冷却 (v3.44 合并卡) ====================
           防重复结算(全事件冷却窗) + NG 周期保护(仅 NG 加保) 本是同类抑制, 归一张卡 -->
      <el-card shadow="never" class="bg-slate-800 border-slate-700">
        <template #header>
          <div class="flex items-center justify-between">
            <span class="font-bold text-white">结算冷却</span>
            <span class="text-xs text-gray-500">抑制同一节拍连触多次结算的误报</span>
          </div>
        </template>
        <div class="space-y-3 text-sm text-gray-300">
          <div class="flex items-center gap-3 flex-wrap">
            <span class="text-gray-400 w-28 shrink-0">全事件冷却</span>
            <el-switch v-model="project.settle_dedup" data-testid="settle-dedup-switch" />
            <template v-if="project.settle_dedup">
              <span class="text-gray-400 text-xs">冷却(秒)</span>
              <el-input-number v-model="project.settle_dedup_window_seconds" size="small"
                :min="0" :step="0.5" :precision="2" data-testid="settle-dedup-window" />
            </template>
            <span class="text-xs text-gray-500">任意事件触发后，窗口内所有事件被抑制（默认 2 秒）</span>
          </div>
          <div v-if="project.logic_mode === 'sequential' || project.logic_mode === 'custom'"
               class="flex items-center gap-3 flex-wrap pt-2 border-t border-slate-700/60">
            <span class="text-gray-400 w-28 shrink-0">NG 间隔加保</span>
            <el-input-number v-model="project.ng_cycle_protect_seconds" size="small" :min="0" :step="1" :precision="2" />
            <span class="text-xs text-gray-500">连续两次 NG 间隔小于此秒数时，后一次被抑制（0=不启用）</span>
          </div>
        </div>
      </el-card>

      <!-- Simultaneous Groups Config (不适用于跟踪模式) -->
      <!-- v3.32+ 区域事件模式 (TP 工位流程监测): 判定规则 + 每类置信度 + 顺序校验 -->
      <el-card v-if="project.logic_mode === 'region_events' && regionEventsCfg" shadow="never" class="bg-slate-800 border-slate-700">
        <template #header>
          <div class="flex justify-between items-center">
            <span class="font-bold text-white">区域事件模式 - 动作规则</span>
            <el-button type="primary" size="small" link @click="addRegionRule">+ 新增动作</el-button>
          </div>
        </template>
        <div class="space-y-4 text-sm text-gray-300">
          <p class="text-xs text-gray-400">
            模型只检测"物"（工具/工件/手），动作由时序规则产出，每条规则 = 一种动作流水。
            「工具作用」= 主体框与目标框重叠（可组合判定区域）连续 N 帧；「出区消失」= 对象进入区域后消失 N 帧（默认结算周期，一个工位循环 = 一个检测周期）。
            所有阈值与区域运行时可调，不需要重训模型。
          </p>

          <div v-for="(rule, rIdx) in regionEventsCfg.rules" :key="rule.id || rIdx" class="bg-slate-900 p-3 rounded border border-slate-700 space-y-3">
            <div class="flex items-center gap-3">
              <span class="text-cyan-400 font-bold whitespace-nowrap">动作 {{ rIdx + 1 }}</span>
              <el-input v-model="rule.name" size="small" placeholder="动作名（如 测硬度）" class="!w-40" />
              <el-select v-model="rule.type" size="small" class="!w-44" @change="onRegionRuleTypeChange(rule)">
                <el-option label="工具作用（框重叠）" value="overlap" />
                <el-option label="进区/驻留（进入区域）" value="region_enter" />
                <el-option label="出区消失（下料）" value="region_exit" />
              </el-select>
              <label class="flex items-center gap-1 text-xs text-gray-400">
                <el-switch v-model="rule.settle" size="small" />
                结算周期
              </label>
              <div class="flex-1"></div>
              <el-button type="danger" size="small" link @click="removeRegionRule(rIdx)">删除</el-button>
            </div>

            <div class="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
              <div>
                <label class="block text-gray-400 mb-1">{{ rule.type === 'overlap' ? '主体类别（工具）' : rule.type === 'region_enter' ? '主体类别（对象）' : '跟踪对象类别' }}</label>
                <el-select v-model="rule.subject_label" size="small" filterable allow-create default-first-option class="w-full" placeholder="如 测硬度笔">
                  <el-option v-for="lbl in regionLabelOptions" :key="lbl" :label="lbl" :value="lbl" />
                </el-select>
              </div>
              <div v-if="rule.type === 'overlap'">
                <label class="block text-gray-400 mb-1">目标类别（被作用）</label>
                <el-select v-model="rule.object_label" size="small" filterable allow-create default-first-option class="w-full" placeholder="如 工件">
                  <el-option v-for="lbl in regionLabelOptions" :key="lbl" :label="lbl" :value="lbl" />
                </el-select>
              </div>
              <div v-if="rule.type === 'overlap'">
                <label class="block text-gray-400 mb-1">区域组合方式</label>
                <el-select v-model="rule.region_mode" size="small" class="w-full">
                  <el-option label="且：重叠 且 主体中心在区域内" value="and" />
                  <el-option label="或：重叠 或 主体中心在区域内" value="or" />
                </el-select>
              </div>
              <div v-if="rule.type !== 'region_exit'">
                <label class="block text-gray-400 mb-1">辅助约束类别（主体须与其相交，可空）</label>
                <el-select :model-value="rule.require_label ?? null" size="small" clearable filterable class="w-full" placeholder="如 手（压误报）"
                  @update:model-value="rule.require_label = $event || null">
                  <el-option v-for="lbl in regionLabelOptions" :key="lbl" :label="lbl" :value="lbl" />
                </el-select>
              </div>
              <div>
                <label class="block text-gray-400 mb-1">{{ rule.type === 'region_exit' ? '区域内最少观察帧数' : '连续满足帧数 N' }}</label>
                <el-input-number v-model="rule.min_frames" size="small" :min="1" :max="600" class="w-full" />
              </div>
              <div v-if="rule.type !== 'region_exit'">
                <label class="block text-gray-400 mb-1" title="动作持续满足该秒数才确认，替代帧数门槛（帧数退化为3帧防噪底线）。相机/推理帧率漂移（24/30fps、GPU负载波动）时帧数门槛松紧会变，按秒判定与帧率无关。0=不启用，按帧数门槛">
                  确认时长（秒，0=按帧数）
                </label>
                <el-input-number :model-value="rule.min_seconds ?? 0" size="small" :min="0" :max="30" :step="0.1" :precision="1" class="w-full"
                  @update:model-value="rule.min_seconds = ($event && $event > 0) ? $event : 0" />
              </div>
              <div v-if="rule.type === 'region_exit'">
                <label class="block text-gray-400 mb-1">消失确认帧数</label>
                <el-input-number v-model="rule.gone_frames" size="small" :min="1" :max="600" class="w-full" />
              </div>
              <div v-if="rule.type === 'region_exit'">
                <label class="block text-gray-400 mb-1">帧间关联 IoU 下限</label>
                <el-input-number v-model="rule.match_iou" size="small" :min="0.05" :max="0.95" :step="0.05" class="w-full" />
              </div>
              <div v-if="rule.type === 'overlap'">
                <label class="block text-gray-400 mb-1">重叠 IoU 下限（0=任意相交）</label>
                <el-input-number v-model="rule.min_iou" size="small" :min="0" :max="0.95" :step="0.05" class="w-full" />
              </div>
              <div v-if="rule.type === 'overlap'">
                <label class="block text-gray-400 mb-1" title="交叠面积占主体(工具)框面积的比例。静置工具贴在工件边上时深度接近0，真动作(工具压在工件上)深度明显——用来压掉搁在台面上的工具误触发">
                  重叠深度下限（0=贴边即算）
                </label>
                <el-input-number v-model="rule.min_overlap_ratio" size="small" :min="0" :max="1" :step="0.05" class="w-full" />
              </div>
              <div v-if="rule.type === 'overlap'">
                <label class="block text-gray-400 mb-1" title="把被作用目标的框向四周虚拟外扩该比例（画面归一化，如0.02≈画面2%）再判相交。动作发生在目标框边缘外一点点时（如在工件下沿扫条码，枪没压进工件框）用它桥接。纯空间几何量，与帧率/工人速度无关。0=不扩边">
                  目标框扩边（0=不扩）
                </label>
                <el-input-number :model-value="rule.object_margin ?? 0" size="small" :min="0" :max="0.2" :step="0.01" :precision="2" class="w-full"
                  @update:model-value="rule.object_margin = ($event && $event > 0) ? $event : 0" />
              </div>
              <div v-if="rule.type !== 'region_exit'">
                <label class="block text-gray-400 mb-1" title="动作确认前，主体(工具)在本段时间内必须移动过的最小距离（画面宽高归一化，如0.04≈画面4%）。工具搁在原地不动只有检测抖动(约0.01)，真动作要拿起来挪动——用来压掉静置工具的误触发。0=不要求移动">
                  位移门槛（0=不要求移动）
                </label>
                <el-input-number v-model="rule.min_move" size="small" :min="0" :max="1" :step="0.01" :precision="2" class="w-full" />
              </div>
              <div v-if="rule.type !== 'region_exit'">
                <label class="block text-gray-400 mb-1" title="动作条件消失超过该秒数才算结束。真动作中途被手/身体遮挡零点几秒不会被切成两段、重复计数。0=不启用，沿用全局漏检容忍帧数">
                  消失确认（秒，0=用全局容忍）
                </label>
                <el-input-number :model-value="rule.gone_seconds ?? 0" size="small" :min="0" :max="30" :step="0.5" :precision="1" class="w-full"
                  @update:model-value="rule.gone_seconds = ($event && $event > 0) ? $event : null" />
              </div>
              <div>
                <label class="block text-gray-400 mb-1">附加触发事件（可空，不结算）</label>
                <el-select :model-value="rule.event_id ?? null" size="small" clearable class="w-full" placeholder="不触发"
                  @update:model-value="rule.event_id = ($event === '' || $event == null) ? null : $event">
                  <el-option v-for="ev in (project.events_config || [])" :key="ev.id" :label="ev.name" :value="ev.id" />
                </el-select>
              </div>
            </div>

            <div class="flex items-center gap-3 pt-2 border-t border-slate-800 text-xs">
              <span class="text-gray-400">判定区域：</span>
              <span :class="(rule.region || []).length >= 3 ? 'text-emerald-400' : 'text-gray-500'">
                {{ (rule.region || []).length >= 3 ? `已标定 (${rule.region.length} 个顶点)` : (rule.type === 'overlap' ? '未标定（不限区域）' : '未标定（该类型规则必须标定）') }}
              </span>
              <el-button type="primary" size="small" plain @click="$emit('open-region-roi-editor', rIdx)">绘制区域</el-button>
              <el-button v-if="(rule.region || []).length" size="small" link type="danger" @click="rule.region = null">清除</el-button>
              <span v-if="rule.type === 'region_enter'" class="text-gray-500">提示：帧数给小 = 进区即触发；给大 + 挂警告事件 = 驻留超时告警</span>
            </div>

            <!-- 区域锚点跟随 (可选): 区域随锚点类别当前位置平移+缩放 -->
            <div class="flex items-center gap-3 pt-2 border-t border-slate-800 text-xs flex-wrap">
              <label class="flex items-center gap-1 text-gray-400 whitespace-nowrap">
                <el-switch :model-value="!!(rule.anchor && rule.anchor.enabled)" size="small" @update:model-value="toggleRegionAnchor(rule, $event)" />
                区域跟随锚点
              </label>
              <template v-if="rule.anchor && rule.anchor.enabled">
                <el-select v-model="rule.anchor.label" size="small" filterable allow-create default-first-option class="!w-32" placeholder="锚点类别">
                  <el-option v-for="lbl in regionLabelOptions" :key="lbl" :label="lbl" :value="lbl" />
                </el-select>
                <el-button size="small" type="primary" plain :loading="grabbingAnchorIdx === rIdx" @click="grabRegionAnchor(rule, rIdx)">从当前画面抓取锚点框</el-button>
                <span v-if="rule.anchor.ref && rule.anchor.ref.w > 0" class="text-emerald-400">
                  已标定 x={{ Number(rule.anchor.ref.x).toFixed(2) }} y={{ Number(rule.anchor.ref.y).toFixed(2) }}
                </span>
                <span v-else class="text-amber-400">未标定（保存时会被忽略）</span>
                <span class="text-gray-400 whitespace-nowrap">锚点丢失沿用最近位置(秒)</span>
                <el-input-number v-model="rule.anchor.hold_seconds" size="small" :min="0" :max="60" :step="0.5" :precision="1" class="!w-24" />
              </template>
              <span v-else class="text-gray-500">区域固定在画面上；开启后区域随锚点类别（如工件/夹具）平移缩放</span>
            </div>
          </div>

          <div v-if="!regionEventsCfg.rules.length" class="text-gray-500 text-center py-4 border border-dashed border-slate-700 rounded">
            暂无规则。典型配置：测硬度（工具作用：笔×工件，且中心在台面区，15 帧）、扫码（工具作用：枪×工件，或中心在操作区，10 帧）、下工件（出区消失：工件×出口区，观察 3 帧 / 消失 8 帧，结算）
          </div>

          <!-- 全局参数 -->
          <div class="bg-slate-900 p-3 rounded border border-slate-700 space-y-3">
            <div class="flex items-center gap-3">
              <span class="text-gray-400 text-xs whitespace-nowrap">漏检容忍帧数 M</span>
              <el-input-number v-model="regionEventsCfg.gap_tolerance_frames" size="small" :min="0" :max="30" />
              <span class="text-xs text-gray-500">连续计数时容忍 ≤M 帧漏检不清零（照顾小目标，如测硬度笔）</span>
            </div>
            <div class="flex items-center gap-3">
              <span class="text-gray-400 text-xs whitespace-nowrap">连续同动作去重</span>
              <el-switch :model-value="regionEventsCfg.dedup_consecutive !== false" size="small"
                @update:model-value="regionEventsCfg.dedup_consecutive = $event" />
              <span class="text-xs text-gray-500">同一动作紧接着再次确认时静默合并，被下一个动作隔开后才重新计（防同一动作被遮挡切成两段算两次；关掉则逐段计数）</span>
            </div>
            <div>
              <p class="text-xs text-gray-400 mb-2">每类置信度阈值（留空 = 不额外过滤，只受启动检测的全局阈值约束；小目标可单独放低）</p>
              <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
                <div v-for="lbl in regionLabelOptions" :key="lbl" class="flex items-center gap-2">
                  <span class="text-xs text-gray-300 w-16 truncate" :title="lbl">{{ lbl }}</span>
                  <el-input-number :model-value="regionEventsCfg.class_conf[lbl] ?? null" size="small" :min="0" :max="1" :step="0.05"
                    class="flex-1" @update:model-value="setRegionClassConf(lbl, $event)" />
                </div>
              </div>
            </div>
          </div>

          <!-- 流程顺序校验 -->
          <div class="bg-slate-900 p-3 rounded border border-slate-700 space-y-3">
            <div class="flex items-center justify-between">
              <span class="text-gray-300 text-xs font-bold">流程顺序校验（乱序只记录，不拦截结算）</span>
              <el-switch v-model="regionEventsCfg.sequence_check.enabled" size="small" />
            </div>
            <div v-if="regionEventsCfg.sequence_check.enabled" class="space-y-2 text-xs">
              <div class="flex items-center gap-3">
                <span class="text-gray-400 whitespace-nowrap">期望顺序（按点选顺序，含结算动作，可重复）</span>
                <div class="flex items-center gap-1 flex-wrap flex-1">
                  <el-tag v-for="(n, ni) in (regionEventsCfg.sequence_check.order || [])" :key="`${n}-${ni}`" size="small" closable type="info"
                    @close="regionEventsCfg.sequence_check.order.splice(ni, 1)">{{ ni + 1 }}.{{ n }}</el-tag>
                  <span v-if="!(regionEventsCfg.sequence_check.order || []).length" class="text-gray-500">点右侧动作名依次追加 →</span>
                  <el-button v-for="r in regionEventsCfg.rules.filter(x => x.name)" :key="r.name" size="small" plain
                    @click="appendSeqName(regionEventsCfg.sequence_check, 'order', r.name)">+{{ r.name }}</el-button>
                </div>
              </div>
              <div class="flex items-center gap-3">
                <span class="text-gray-400 whitespace-nowrap">乱序时触发事件</span>
                <el-select :model-value="regionEventsCfg.sequence_check.event_id ?? null" size="small" clearable class="!w-48" placeholder="只记日志"
                  @update:model-value="regionEventsCfg.sequence_check.event_id = ($event === '' || $event == null) ? null : $event">
                  <el-option v-for="ev in (project.events_config || [])" :key="ev.id" :label="ev.name" :value="ev.id" />
                </el-select>
                <span class="text-gray-500">建议配"警告/自定义"类事件；是否报警由该事件的动作决定</span>
              </div>
            </div>
          </div>

          <!-- 结算判定 (自定义模式同款语义: 从上到下先匹配先赢) -->
          <div class="bg-slate-900 p-3 rounded border border-slate-700 space-y-3">
            <div class="flex items-center justify-between">
              <span class="text-gray-300 text-xs font-bold">结算判定（周期收口时按动作序列决定结算事件，从上到下先匹配先赢）</span>
              <el-button type="primary" size="small" link @click="addSettlementRule">+ 新增判定</el-button>
            </div>
            <p class="text-xs text-gray-500">
              不配置 = 全部按"合格"结算（老行为）。典型配置：① 序列匹配 标准流程→合格 ② 缺"测硬度"→不合格 ③ "扫码"重复≥2次→不合格。全不命中时回退默认。
            </p>
            <div v-for="(sr, sIdx) in (regionEventsCfg.settlement_rules || [])" :key="sIdx"
              class="flex items-center gap-2 flex-wrap bg-slate-800 rounded px-2 py-1.5 text-xs">
              <span class="text-cyan-400 font-bold whitespace-nowrap">判定 {{ sIdx + 1 }}</span>
              <el-select v-model="sr.match" size="small" class="!w-36" @change="onSettlementMatchChange(sr)">
                <el-option label="序列完全匹配" value="exact" />
                <el-option label="缺某动作" value="missing" />
                <el-option label="某动作重复" value="repeated" />
                <el-option label="其余全部（兜底）" value="always" />
              </el-select>
              <template v-if="sr.match === 'exact'">
                <!-- 序列可含重复动作（如 测硬度→扫码→扫码→下工件），多选下拉点第二次会取消，改用点击追加 -->
                <div class="flex items-center gap-1 flex-wrap flex-1 min-w-48">
                  <el-tag v-for="(n, ni) in (sr.sequence || [])" :key="`${n}-${ni}`" size="small" closable type="info"
                    @close="sr.sequence.splice(ni, 1)">{{ ni + 1 }}.{{ n }}</el-tag>
                  <span v-if="!(sr.sequence || []).length" class="text-gray-500">点右侧动作名依次追加（同一动作可点多次）→</span>
                  <el-button v-for="r in regionEventsCfg.rules.filter(x => x.name)" :key="r.name" size="small" plain
                    @click="appendSeqName(sr, 'sequence', r.name)">+{{ r.name }}</el-button>
                </div>
              </template>
              <template v-else-if="sr.match === 'missing' || sr.match === 'repeated'">
                <el-select v-model="sr.target" size="small" class="!w-32" placeholder="目标动作">
                  <el-option v-for="r in regionEventsCfg.rules.filter(x => x.name)" :key="r.name" :label="r.name" :value="r.name" />
                </el-select>
                <template v-if="sr.match === 'repeated'">
                  <span class="text-gray-400 whitespace-nowrap">出现≥</span>
                  <el-input-number v-model="sr.min_count" size="small" :min="2" :max="20" class="!w-20" />
                  <span class="text-gray-400">次</span>
                </template>
              </template>
              <span v-else class="text-gray-500">上面全不命中时走这条（放最后）</span>
              <span class="text-gray-400 whitespace-nowrap">→ 结算为</span>
              <el-select :model-value="sr.event_id ?? null" size="small" class="!w-36" placeholder="选择事件"
                @update:model-value="sr.event_id = ($event === '' || $event == null) ? null : $event">
                <el-option v-for="ev in (project.events_config || [])" :key="ev.id" :label="ev.name" :value="ev.id" />
              </el-select>
              <el-button size="small" link :disabled="sIdx === 0" @click="moveSettlementRule(sIdx, -1)">上移</el-button>
              <el-button size="small" link :disabled="sIdx === (regionEventsCfg.settlement_rules || []).length - 1" @click="moveSettlementRule(sIdx, 1)">下移</el-button>
              <el-button type="danger" size="small" link @click="regionEventsCfg.settlement_rules.splice(sIdx, 1)">删除</el-button>
            </div>
          </div>
        </div>
      </el-card>

      <el-card v-if="project.logic_mode !== 'tracking' && project.logic_mode !== 'region_events'" shadow="never" class="bg-slate-800 border-slate-700">
        <template #header>
          <div class="flex justify-between items-center">
            <span class="font-bold text-white">同时出现组</span>
            <el-button type="primary" size="small" link @click="addSimultaneousGroup">+ 新增组</el-button>
          </div>
        </template>
        <div class="space-y-4 text-sm text-gray-300">
          <p class="text-xs text-gray-400">当多个步骤可能同时出现在画面中时，配置为一组可防止重复识别。系统会按设定的优先顺序记录到周期中。</p>
          <div class="space-y-3">
            <div v-for="(group, gIdx) in (project.simultaneous_groups || [])" :key="gIdx" class="bg-slate-900 p-3 rounded border border-slate-700">
              <div class="flex justify-between items-center mb-3">
                <div class="flex items-center gap-3">
                  <el-switch v-model="group.enabled" size="small" />
                  <span class="text-cyan-400 font-bold">组 {{ gIdx + 1 }}</span>
                </div>
                <el-button type="danger" size="small" link @click="removeSimultaneousGroup(gIdx)">删除</el-button>
              </div>
              <div class="space-y-3">
                <div class="flex items-center gap-3 pb-2 border-b border-slate-800">
                  <el-tooltip placement="right">
                    <template #content>
                      <div style="max-width:320px;line-height:1.5">
                        跨周期模式：组内同时含有"上一周期成员"和"下一周期首步"。<br/>
                        状态机会让上周期最后一步和下周期首步互相等待对方出现，<br/>
                        避免因模型残影把上周期末步当成新周期起点而产生鬼周期。<br/>
                        ⚠ 与"启用防重复结算"互斥，不能同时启用。
                      </div>
                    </template>
                    <span class="text-xs text-gray-400 cursor-help underline decoration-dotted">跨周期？</span>
                  </el-tooltip>
                  <el-switch v-model="group.cross_cycle" size="small" />
                </div>
                <div>
                  <p class="text-xs text-gray-500 mb-1">{{ group.cross_cycle ? '等待窗口（秒）：先到一侧后等另一侧的最长时间' : '时间窗口（秒）：在此时间内先后出现视为"同时"' }}</p>
                  <el-input-number v-model="group.time_window" size="small" :min="0" :step="0.5" :precision="2" />
                </div>
                <div>
                  <p class="text-xs text-gray-500 mb-2">
                    {{ group.cross_cycle
                      ? '组内成员的归属：上一周期成员（结算前已识别）与下一周期首步（结算后才识别）'
                      : '选择可能同时出现的步骤，并按优先顺序排列' }}
                  </p>
                  <div class="space-y-2">
                    <div v-for="(label, sIdx) in (group.priority_order || [])" :key="sIdx" class="flex items-center gap-2">
                      <span class="text-gray-400 text-xs w-6">{{ sIdx + 1 }}.</span>
                      <el-select v-model="group.priority_order[sIdx]" size="small" class="flex-1" placeholder="选择步骤">
                        <el-option v-for="step in enabledSteps" :key="step.id" :label="step.displayLabel || step.label" :value="step.label" />
                      </el-select>
                      <el-select
                        v-if="group.cross_cycle"
                        v-model="(group.period_roles = group.period_roles || {})[group.priority_order[sIdx]]"
                        size="small"
                        style="width:96px"
                        placeholder="归属"
                      >
                        <el-option label="上周期" value="prev" />
                        <el-option label="下周期" value="next" />
                      </el-select>
                      <el-button size="small" :disabled="sIdx === 0" link @click="moveSimGroupStep(gIdx, sIdx, -1)">↑</el-button>
                      <el-button size="small" :disabled="sIdx === group.priority_order.length - 1" link @click="moveSimGroupStep(gIdx, sIdx, 1)">↓</el-button>
                      <el-button type="danger" size="small" link @click="removeSimGroupStep(gIdx, sIdx)">删除</el-button>
                    </div>
                    <el-button type="primary" size="small" link @click="addSimGroupStep(gIdx)">+ 添加步骤</el-button>
                  </div>
                </div>
              </div>
            </div>
            <div v-if="!project.simultaneous_groups || project.simultaneous_groups.length === 0" class="text-gray-500 text-center py-4 border border-dashed border-slate-700 rounded">
              暂无同时出现组，点击上方按钮添加
            </div>
          </div>
        </div>
      </el-card>

      <!-- 误判过滤（通用两层后处理，默认关） -->
      <el-card shadow="never" class="bg-slate-800 border-slate-700">
        <template #header>
          <div class="flex justify-between items-center">
            <span class="font-bold text-white">误判过滤（高级）</span>
            <span class="text-[11px] text-gray-500">推理后处理，默认全关。按模型类别名（label）匹配，通用。</span>
          </div>
        </template>
        <div class="space-y-4">
          <!-- 第 1 层：空间共现 -->
          <div class="bg-slate-900 p-4 rounded border border-slate-800">
            <div class="flex items-center justify-between mb-3">
              <div>
                <div class="font-bold text-white text-sm">空间共现过滤</div>
                <div class="text-[11px] text-gray-500 mt-0.5">
                  同一帧里，目标 label 必须和任一伴随 label 满足「中心点落在其 bbox 内 或 IoU ≥ 阈值」，否则丢弃。
                </div>
              </div>
              <el-switch v-model="project.rod_companion_filter.enabled" active-color="#22d3ee" />
            </div>
            <div v-if="project.rod_companion_filter.enabled" class="grid grid-cols-1 md:grid-cols-3 gap-3 text-xs">
              <div>
                <label class="block text-gray-400 mb-1">目标 label（要过滤的易误判类别）</label>
                <el-select
                  v-model="project.rod_companion_filter.rod_label"
                  size="small"
                  filterable
                  allow-create
                  default-first-option
                  clearable
                  placeholder="选择或输入，如 传动杆"
                  class="w-full"
                >
                  <el-option
                    v-for="lbl in availableLabels"
                    :key="lbl"
                    :label="lbl"
                    :value="lbl"
                  />
                </el-select>
              </div>
              <div>
                <label class="block text-gray-400 mb-1">伴随 label（至少共现一个才保留）</label>
                <el-select
                  v-model="project.rod_companion_filter.companion_labels"
                  size="small"
                  filterable
                  allow-create
                  multiple
                  default-first-option
                  placeholder="多选或输入，如 大框架/小框架/侧板"
                  class="w-full"
                >
                  <el-option
                    v-for="lbl in availableLabels"
                    :key="lbl"
                    :label="lbl"
                    :value="lbl"
                  />
                </el-select>
              </div>
              <div>
                <label class="block text-gray-400 mb-1">IoU 阈值（0~1）</label>
                <el-input-number
                  v-model="project.rod_companion_filter.iou_threshold"
                  size="small"
                  :min="0"
                  :max="1"
                  :step="0.05"
                  :precision="2"
                  controls-position="right"
                  class="w-full"
                />
              </div>
            </div>
          </div>

          <!-- 第 2 层：软时序状态门 -->
          <div class="bg-slate-900 p-4 rounded border border-slate-800">
            <div class="flex items-center justify-between mb-3">
              <div>
                <div class="font-bold text-white text-sm">软时序状态门</div>
                <div class="text-[11px] text-gray-500 mt-0.5">
                  当前周期从未出现过触发 label 时，抑制所有目标 label；一旦出现过即放行至下一次周期重置。
                </div>
              </div>
              <el-switch v-model="project.rod_session_gate.enabled" active-color="#22d3ee" />
            </div>
            <div v-if="project.rod_session_gate.enabled" class="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
              <div>
                <label class="block text-gray-400 mb-1">目标 label（被门控的类别）</label>
                <el-select
                  v-model="project.rod_session_gate.rod_label"
                  size="small"
                  filterable
                  allow-create
                  default-first-option
                  clearable
                  placeholder="选择或输入，如 传动杆"
                  class="w-full"
                >
                  <el-option
                    v-for="lbl in availableLabels"
                    :key="lbl"
                    :label="lbl"
                    :value="lbl"
                  />
                </el-select>
              </div>
              <div>
                <label class="block text-gray-400 mb-1">触发 label（周期内首次出现即解除门控）</label>
                <el-select
                  v-model="project.rod_session_gate.gate_labels"
                  size="small"
                  filterable
                  allow-create
                  multiple
                  default-first-option
                  placeholder="多选或输入，如 大框架/小框架"
                  class="w-full"
                >
                  <el-option
                    v-for="lbl in availableLabels"
                    :key="lbl"
                    :label="lbl"
                    :value="lbl"
                  />
                </el-select>
              </div>
            </div>
          </div>
        </div>
      </el-card>
    </div>
</template>

<script setup>
// ==================== 逻辑设置 Tab（2026-07 拆分批次 P-4 自 index.vue 平移） ====================
// 数据流约定（拆分原则 2）：直接原位修改父级传入的 project（settlement_mode /
// sequence_order / custom_conditions / simultaneous_groups / periodic_actions /
// pipeline_config.* 等子树），保存仍由父级"保存配置"按钮统一走 updateProject。
// 需要父级上下文的三个动作走 emit：
//   sequence-step-pick → 父级 syncDupDisappearDelay（连续重复消失延迟联动, 步骤设置 Tab 共用）
//   mix-type-change    → 父级 onCustomMixTypeChange（物品行补默认值, 与步骤表角色切换共用）
//   open-roi-editor    → 父级 ROI 编辑器编排（对话框 + 通道解析 + 保存路由）
import { computed, nextTick, ref, watch } from 'vue';
import { ElMessage } from 'element-plus';
import { Delete } from '@element-plus/icons-vue';
import { dbg } from '@/utils/debug';
import { getDetectionResults, inferOnce } from '@/api/detection';
import { _pi_itemLabelToArray, _pi_itemLabelFromArray } from './perItemLabel';

const props = defineProps({
  project: { type: Object, required: true },
});
defineEmits(['sequence-step-pick', 'mix-type-change', 'open-roi-editor', 'open-region-roi-editor']);

// ---------- 步骤候选（自 index.vue 平移, 仅本 Tab 使用） ----------
// 计算启用的步骤
const enabledSteps = computed(() => {
  if (!props.project?.steps_config) return [];
  // v3.19.x: 物品行 (detect_role='item') 归自定义混合子状态机管，
  // 不参与任何"步骤选择"场景（序列/检测/条件/触发器等），与后端守门一致
  return props.project.steps_config.filter(s => s.enabled && s.detect_role !== 'item');
});

const nonBackupSteps = computed(() => {
  return enabledSteps.value.filter(s => !s.backup_for);
});

const countableSteps = computed(() => {
  const trigger = props.project?.tracking_trigger_label || '';
  const container = props.project?.tracking_cycle_strategy === 'container'
    ? (props.project?.tracking_container_label || '') : '';
  return nonBackupSteps.value.filter(s => s.label !== trigger && s.label !== container);
});

// 当前项目已配置过的 label 集合（去重），给误判过滤的下拉当候选项用
const availableLabels = computed(() => {
  const set = new Set();
  const steps = props.project?.steps_config || [];
  steps.forEach(s => {
    if (s && s.label) set.add(s.label);
  });
  return Array.from(set);
});

// ---------- v3.32+ 区域事件模式（TP 工位流程监测） ----------
// pipeline_config.region_events 的存在性由父级 index.vue 在模式切换时兜底补全，
// 这里只做原位编辑；字段名与后端 source_region_events.parse_region_events 严格对齐。
const regionEventsCfg = computed(() => {
  if (props.project?.logic_mode !== 'region_events') return null;
  return props.project?.pipeline_config?.region_events || null;
});

// ==================== v3.44 NG 判定与处置 (统一卡) ====================
const ngh = computed(() => props.project?.pipeline_config?.ng_handling || null);
const isCustomSequential = computed(() =>
  props.project?.logic_mode === 'custom' && props.project?.custom_based_on === 'sequential');
// 跨 tab 联动明示: 定格弹窗由事件设置里 NG 事件(id=2)的「需人工确认」决定
const ngEventRequiresAck = computed(() => {
  const ev = (props.project?.events_config || []).find(e => e.id === 2 || String(e.id) === '2');
  return !!ev?.require_ack;
});

// 类别候选 = 步骤表里的模型标签 ∪ 规则/置信度表里已引用的标签（防手输标签丢选项）
const regionLabelOptions = computed(() => {
  const set = new Set(availableLabels.value);
  const cfg = regionEventsCfg.value;
  if (cfg) {
    (cfg.rules || []).forEach(r => {
      [r.subject_label, r.object_label, r.require_label].forEach(l => { if (l) set.add(l); });
    });
    Object.keys(cfg.class_conf || {}).forEach(l => set.add(l));
  }
  return Array.from(set);
});

const addRegionRule = () => {
  const cfg = regionEventsCfg.value;
  if (!cfg) return;
  if (!Array.isArray(cfg.rules)) cfg.rules = [];
  const nextNum = cfg.rules.reduce((mx, r) => {
    const m = /^r(\d+)$/.exec(r.id || '');
    return m ? Math.max(mx, parseInt(m[1], 10)) : mx;
  }, 0) + 1;
  cfg.rules.push({
    id: `r${nextNum}`,
    name: '',
    type: 'overlap',
    subject_label: '',
    object_label: '',
    region: null,
    region_mode: 'and',
    min_frames: 10,
    min_iou: 0,
    min_overlap_ratio: 0,
    min_move: 0,
    min_seconds: 0,
    object_margin: 0,
    gone_seconds: null,
    require_label: null,
    gone_frames: 8,
    match_iou: 0.3,
    settle: false,
    event_id: null,
  });
};

const removeRegionRule = (idx) => {
  regionEventsCfg.value?.rules?.splice(idx, 1);
};

// 切规则类型时回填该类型的推荐默认值（后端解析侧同款缺省）
const onRegionRuleTypeChange = (rule) => {
  if (rule.type === 'region_exit') {
    rule.min_frames = 3;
    rule.settle = true;
  } else if (rule.type === 'region_enter') {
    rule.min_frames = 3;
    rule.settle = false;
  } else {
    rule.min_frames = 10;
    rule.settle = false;
  }
};

// ---------- 区域锚点跟随 ----------
const grabbingAnchorIdx = ref(-1);

const toggleRegionAnchor = (rule, on) => {
  if (on) {
    if (!rule.anchor || typeof rule.anchor !== 'object') {
      rule.anchor = { enabled: true, label: '', ref: null, hold_seconds: 3.0 };
    } else {
      rule.anchor.enabled = true;
      if (rule.anchor.hold_seconds === undefined) rule.anchor.hold_seconds = 3.0;
    }
  } else if (rule.anchor) {
    rule.anchor.enabled = false;
  }
};

const grabRegionAnchor = async (rule, rIdx) => {
  const lbl = String(rule.anchor?.label || '').trim();
  if (!lbl) { ElMessage.warning('先选择锚点类别'); return; }
  grabbingAnchorIdx.value = rIdx;
  try {
    // 先读实时结果, 空则单帧推理兜底(停止/待机后实时结果被清, 见 LabelSplitDialog 同款)
    const res = await getDetectionResults(0);
    let dets = (res.data?.detections || []).filter(d => d.label === lbl);
    if (!dets.length) {
      const once = await inferOnce(0);
      dets = (once.data?.detections || []).filter(d => d.label === lbl);
    }
    if (!dets.length) {
      ElMessage.error(`当前画面没有检测到「${lbl}」— 请把对象摆稳到标准位置后重试（画面定格时需回监控页重新停在对象清晰可见的画面）`);
      return;
    }
    const best = dets.reduce((a, b) => ((b.confidence || 0) > (a.confidence || 0) ? b : a));
    rule.anchor.ref = {
      x: Number(best.x) || 0, y: Number(best.y) || 0,
      w: Number(best.w) || 0, h: Number(best.h) || 0,
    };
    ElMessage.success(`已标定锚点框（置信度 ${((best.confidence || 0) * 100).toFixed(0)}%），请保持对象位置不动再画区域`);
  } catch (e) {
    ElMessage.error('抓取失败: ' + (e.message || e));
  } finally {
    grabbingAnchorIdx.value = -1;
  }
};

// ---------- 结算判定 ----------
const addSettlementRule = () => {
  const cfg = regionEventsCfg.value;
  if (!cfg) return;
  if (!Array.isArray(cfg.settlement_rules)) cfg.settlement_rules = [];
  cfg.settlement_rules.push({ match: 'missing', sequence: [], target: '', min_count: 2, event_id: null });
};

const onSettlementMatchChange = (sr) => {
  if (sr.match === 'exact' && !Array.isArray(sr.sequence)) sr.sequence = [];
  if (sr.match === 'repeated' && !(Number(sr.min_count) >= 2)) sr.min_count = 2;
};

// 序列编辑器共用: 点击事件名按钮往数组尾部追加（允许重复, 多选下拉做不到）
const appendSeqName = (obj, key, name) => {
  if (!Array.isArray(obj[key])) obj[key] = [];
  obj[key].push(name);
};

const moveSettlementRule = (idx, delta) => {
  const list = regionEventsCfg.value?.settlement_rules;
  if (!list) return;
  const to = idx + delta;
  if (to < 0 || to >= list.length) return;
  [list[idx], list[to]] = [list[to], list[idx]];
};

const setRegionClassConf = (label, val) => {
  const cfg = regionEventsCfg.value;
  if (!cfg) return;
  if (!cfg.class_conf) cfg.class_conf = {};
  if (val == null || val === '') {
    delete cfg.class_conf[label];
  } else {
    cfg.class_conf[label] = val;
  }
};

// ---------- 跟踪模式 ROI 预览小画布（编辑器本体在 RoiEditorDialog, 由父级编排） ----------
const roiPreviewCanvas = ref(null);

const drawRoiPreview = () => {
  const canvas = roiPreviewCanvas.value;
  if (!canvas || !props.project?.tracking_roi_polygon?.length) return;
  const parent = canvas.parentElement;
  if (parent) { canvas.width = parent.offsetWidth; canvas.height = parent.offsetHeight; }
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = '#1e293b';
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  const poly = props.project.tracking_roi_polygon;
  if (poly.length < 3) return;

  ctx.fillStyle = 'rgba(0, 200, 255, 0.2)';
  ctx.strokeStyle = '#00c8ff';
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(poly[0][0] * canvas.width, poly[0][1] * canvas.height);
  for (let i = 1; i < poly.length; i++) {
    ctx.lineTo(poly[i][0] * canvas.width, poly[i][1] * canvas.height);
  }
  ctx.closePath();
  ctx.fill();
  ctx.stroke();

  poly.forEach((pt, i) => {
    ctx.fillStyle = i === 0 ? '#f59e0b' : '#00c8ff';
    ctx.beginPath();
    ctx.arc(pt[0] * canvas.width, pt[1] * canvas.height, 3, 0, Math.PI * 2);
    ctx.fill();
  });
};

// 多边形变化(含父级 ROI 编辑器保存回写)或卡片挂载后重绘预览
watch(() => props.project?.tracking_roi_polygon, () => {
  nextTick(() => drawRoiPreview());
}, { deep: true, immediate: true });

// ---------- 顺序模式 - 步骤操作 ----------
const addSequenceStep = () => {
  dbg('project.config', '点击「添加顺序步骤」', `当前行数=${props.project?.sequence_order?.length ?? 0}`);
  if (!props.project.sequence_order) props.project.sequence_order = [];
  props.project.sequence_order.push({ step_id: null });
};

const removeSequenceStep = (idx) => {
  dbg('project.config', '点击「删除顺序步骤」', `idx=${idx} step_id=${props.project?.sequence_order?.[idx]?.step_id}`);
  props.project.sequence_order.splice(idx, 1);
};

const moveStepUp = (idx) => {
  if (idx <= 0) return;
  const temp = props.project.sequence_order[idx];
  props.project.sequence_order[idx] = props.project.sequence_order[idx - 1];
  props.project.sequence_order[idx - 1] = temp;
};

const moveStepDown = (idx) => {
  if (idx >= props.project.sequence_order.length - 1) return;
  const temp = props.project.sequence_order[idx];
  props.project.sequence_order[idx] = props.project.sequence_order[idx + 1];
  props.project.sequence_order[idx + 1] = temp;
};

// ---------- 自定义条件 ----------
const addCustomCondition = () => {
  if (!props.project.custom_conditions) props.project.custom_conditions = [];
  const nextId = props.project.custom_conditions.length > 0
    ? Math.max(...props.project.custom_conditions.map(c => c.id || 0)) + 1
    : 1;
  const nextPriority = props.project.custom_conditions.length + 1;
  props.project.custom_conditions.push({
    id: nextId,
    priority: nextPriority,
    sequence: [],  // 步骤ID数组
    event_id: null
  });
};

const removeCustomCondition = (idx) => {
  props.project.custom_conditions.splice(idx, 1);
};

// 自定义条件中的步骤操作
const addConditionStep = (condIdx) => {
  if (!props.project.custom_conditions[condIdx].sequence) {
    props.project.custom_conditions[condIdx].sequence = [];
  }
  props.project.custom_conditions[condIdx].sequence.push(null);
};

const removeConditionStep = (condIdx, stepIdx) => {
  props.project.custom_conditions[condIdx].sequence.splice(stepIdx, 1);
};

// ---------- v3.5.0: 周期性强制动作 ----------
const addPeriodicAction = () => {
  if (!props.project.periodic_actions) props.project.periodic_actions = [];
  props.project.periodic_actions.push({
    id: `pa_${Date.now()}_${props.project.periodic_actions.length}`,
    name: `规则 ${props.project.periodic_actions.length + 1}`,
    enabled: true,
    trigger_step_ids: [],
    interval: 20,
    time_interval_seconds: 0,  // v3.7.4: 默认 0=按时间不触发, 客户按需配置
    count_basis: 'good_only',
    reset_policy: 'always',
    due_warning_event_id: null,
    overdue_event_id: null,
    overdue_repeat: 'every_cycle',
    channel_filter: null,
    run_on_start: false,
  });
};

const removePeriodicAction = (idx) => {
  props.project.periodic_actions.splice(idx, 1);
};

// v3.8.x: 'continuous:N' 模式辅助 — UI 上 select 只显示 mode (不带 N),
// N 用旁边的数字输入框, 提交时拼回 'continuous:N' 字符串到 overdue_repeat.
// 老配置 (every_cycle / once / cooldown:N) 不受影响, 走原 path.
const getOverdueRepeatMode = (val) => {
  const s = String(val || 'every_cycle');
  if (s.startsWith('continuous')) return 'continuous';
  return s;
};
const setOverdueRepeatMode = (rule, mode) => {
  if (mode === 'continuous') {
    const prev = getContinuousSeconds(rule.overdue_repeat);
    rule.overdue_repeat = `continuous:${prev || 5}`;
  } else {
    rule.overdue_repeat = mode;
  }
};
const getContinuousSeconds = (val) => {
  const s = String(val || '');
  if (!s.startsWith('continuous')) return 5;
  const parts = s.split(':');
  const n = parts.length > 1 ? Number(parts[1]) : 5;
  return Number.isFinite(n) && n > 0 ? n : 5;
};

// v3.5.2: 在 periodic_actions 里直接快捷新建事件并绑定到规则
const addEventAndBindToRule = (rule, fieldKey) => {
  if (!props.project.events_config) props.project.events_config = [];
  const newId = Date.now();
  const defaultName = fieldKey === 'due_warning_event_id'
    ? `${rule.name || '保养'}-到期提醒`
    : `${rule.name || '保养'}-超期告警`;
  props.project.events_config.push({
    id: newId,
    name: defaultName,
    color: fieldKey === 'overdue_event_id' ? '#ef4444' : '#f59e0b',
    actions: [],
    show_notification: true,
    notification_type: fieldKey === 'overdue_event_id' ? 'critical' : 'normal',
    toast_id: 'ng',
  });
  rule[fieldKey] = newId;
  ElMessage.success(`已新建事件「${defaultName}」并绑定到本规则`);
};

// ---------- 同时出现组操作 ----------
const addSimultaneousGroup = () => {
  if (!props.project.simultaneous_groups) props.project.simultaneous_groups = [];
  props.project.simultaneous_groups.push({
    enabled: true,
    cross_cycle: false,
    labels: [],
    time_window: 2.0,
    priority_order: [],
    period_roles: {}
  });
};

const removeSimultaneousGroup = (idx) => {
  props.project.simultaneous_groups.splice(idx, 1);
};

const addSimGroupStep = (gIdx) => {
  const group = props.project.simultaneous_groups[gIdx];
  if (!group.priority_order) group.priority_order = [];
  group.priority_order.push('');
};

const removeSimGroupStep = (gIdx, sIdx) => {
  const group = props.project.simultaneous_groups[gIdx];
  group.priority_order.splice(sIdx, 1);
  group.labels = group.priority_order.filter(l => l);
};

const moveSimGroupStep = (gIdx, sIdx, direction) => {
  const group = props.project.simultaneous_groups[gIdx];
  const newIdx = sIdx + direction;
  if (newIdx < 0 || newIdx >= group.priority_order.length) return;
  const temp = group.priority_order[sIdx];
  group.priority_order[sIdx] = group.priority_order[newIdx];
  group.priority_order[newIdx] = temp;
  group.labels = group.priority_order.filter(l => l);
};

// ---------- 自定义模式 - 独立的顺序配置操作 ----------
const addCustomSequenceStep = () => {
  if (!props.project.custom_sequence_order) props.project.custom_sequence_order = [];
  props.project.custom_sequence_order.push({ step_id: null });
};

const removeCustomSequenceStep = (idx) => {
  props.project.custom_sequence_order.splice(idx, 1);
};
</script>
