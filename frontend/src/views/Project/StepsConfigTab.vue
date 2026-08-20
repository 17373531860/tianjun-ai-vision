<template>
  <!-- ==================== 步骤/物品设置 Tab（2026-07 拆分批次 P-5 自 index.vue 外置） ==================== -->
    <div class="h-full flex flex-col p-4">
      <div class="mb-3 flex flex-col gap-2 flex-shrink-0">
        <div class="text-sm text-gray-400 flex items-center">
          <el-icon class="mr-1"><InfoFilled /></el-icon>
          <template v-if="project.logic_mode === 'tracking'">
            配置各物品的启用状态与置信度。跟踪参数请在"逻辑设置"中配置。
          </template>
          <template v-else>
            配置各步骤的启用状态、置信度、显示标签。启用的步骤将参与逻辑判断。
          </template>
        </div>
        <div class="flex items-center gap-2 text-xs text-gray-300 flex-wrap">
          <el-switch
            v-model="project.pipeline_config.hide_boxes_outside_step_roi"
            size="small"
          />
          <span>Monitor 不绘制步骤 ROI 外的框</span>
          <el-tooltip
            placement="top"
            content="仅影响监视画面：对已配置「步骤ROI」的标签，检测框中心不在该区域内则不画框（分割遮罩一并隐藏）。不参与后端判定。关闭则始终绘制（与原先一致）。"
          >
            <span class="cursor-help border-b border-dashed border-gray-500 text-gray-400">说明</span>
          </el-tooltip>
        </div>
      </div>

      <div class="flex-1 overflow-y-auto custom-scrollbar min-h-0 space-y-4 pb-8">
        <!-- ============ 表A: 标签与检测属性 (所有模式通用) ============ -->
        <div class="border border-slate-700 rounded">
          <div class="px-3 py-2 bg-slate-800 border-b border-slate-700 flex items-center gap-2 flex-wrap">
            <span class="font-bold text-white text-sm">标签与检测属性</span>
            <span class="text-xs text-gray-500">所有模式通用 — 置信度阈值与步骤ROI是检测层守门，对步骤判定与物品计数一并生效</span>
          </div>
          <div class="overflow-x-auto custom-scrollbar step-table-scroll">
            <table class="min-w-full w-max text-left text-xs text-gray-300 border-collapse whitespace-nowrap">
              <thead class="bg-slate-800 text-gray-400 sticky top-0 z-10">
                <tr class="border-b border-slate-700">
                  <th class="p-2">原始标签</th>
                  <th class="p-2 w-16">启用</th>
                  <th class="p-2 w-28">置信度阈值</th>
                  <th class="p-2 w-28">显示名称</th>
                  <th v-if="isCustomMixed" class="p-2 w-24">
                    <el-tooltip content="自定义混合模式专用：「步骤」参与序列/检测/条件判定；「物品」不参与步骤序列，由混合子状态机做数量校验（周期结算时两边都合格才算合格），参数在下方「物品校验参数」表配置" placement="top">
                      <span class="cursor-help border-b border-dashed border-gray-500">角色</span>
                    </el-tooltip>
                  </th>
                  <th class="p-2 w-20">
                    <el-tooltip content="开启后，仅在检测画面上不绘制此标签的检测框；SOP流程卡片、步骤详情照常显示与统计，YOLO 检测、OK/NG 判定、报警、数据记录、MES 上报等均不受影响。常用于隐藏箱子/泡沫槽等辅助类别的框" placement="top">
                      <span class="cursor-help border-b border-dashed border-gray-500">隐藏标注框</span>
                    </el-tooltip>
                  </th>
                  <th class="p-2 w-20">
                    <el-tooltip content="该标签的检测框单独配色（任何模式都生效）。留空 = 副模型沿用其默认识别色 / 主模型沿用全局 OK/NG 颜色。设了颜色就以这里为准，副模型默认色让位。" placement="top">
                      <span class="cursor-help border-b border-dashed border-gray-500">检测框颜色</span>
                    </el-tooltip>
                  </th>
                  <th class="p-2 w-40">
                    <el-tooltip placement="top">
                      <template #content>
                        <div style="max-width: 320px; line-height: 1.5">
                          <b>Box 尺寸上限</b>（归一化 0~1, <b>0 = 关闭</b>，默认关闭）<br/>
                          检测框宽 / 高 超过比例时直接丢弃该 detection。<br/>
                          用途：模型把"工件整体形态"误识别为某个 label 时（如 box 宽 ≈ 整张画面），<br/>
                          用 W≤0.85 即可滤掉，而真正的局部动作 box（≤80%）不受影响。<br/>
                          该过滤在所有逻辑模式下生效。
                        </div>
                      </template>
                      <span class="cursor-help border-b border-dashed border-gray-500">Box尺寸上限</span>
                    </el-tooltip>
                  </th>
                  <th class="p-2 w-36">
                    <el-tooltip content="归一化多边形区域。设置后：仅当检测框中心落在该区域内时，该步骤/标签才计入 SOP 与周期（顺序、检测、自定义、跟踪模式均生效，混合模式的物品计数同样遵守）。不设置则不限区域。" placement="top">
                      <span class="cursor-help border-b border-dashed border-gray-500">步骤ROI</span>
                    </el-tooltip>
                  </th>
                  <th v-if="isSeqLike" class="p-2 w-32">
                    <el-tooltip placement="top">
                      <template #content>
                        <div style="max-width: 340px; line-height: 1.5">
                          <b>外设门控</b>（默认关闭，零差异）<br/>
                          视觉识别到该步骤后<b>不立即计入周期</b>，先等外设（电子秤）条件满足才放行：<br/>
                          • <b>去皮门控</b>：秤上毛重超阈值且稳定 → 自动发去皮指令 → 放行（适合"工件放秤"步骤）<br/>
                          • <b>称重判定</b>：净重稳定 → 按当前型号×料别对比标准量 → 合格放行，缺料/超量报警拦截<br/>
                          启用任一门控后会出现「称重配置」页签，在那里配秤参数/型号标准量表。
                        </div>
                      </template>
                      <span class="cursor-help border-b border-dashed border-gray-500">外设门控</span>
                    </el-tooltip>
                  </th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="step in (project.steps_config || [])" :key="step.id" class="border-b border-slate-700 hover:bg-slate-700/30">
                  <td class="p-2 font-mono text-cyan-400">
                    <div class="flex items-center gap-1.5 flex-wrap">
                      <span>{{ step.label }}</span>
                      <el-tag
                        v-if="project.logic_mode === 'per_item'"
                        size="small"
                        :type="(step.per_item && step.per_item.action_label) ? 'success' : 'info'"
                        effect="plain"
                        class="!h-5 !leading-5"
                      >
                        {{ (step.per_item && step.per_item.action_label) ? '动作' : '目标' }}
                      </el-tag>
                      <el-tag v-if="step.from_model && step.from_model !== 'main'" size="small" type="warning" effect="plain" class="!h-5 !leading-5">
                        {{ step.from_model }}
                      </el-tag>
                      <el-tooltip v-if="step.split_origin" content="由「同标签区域拆分」规则自动生成的虚拟步骤：检测框中心落进对应区域时，原始标签会被改写成这个步骤名。删除/改名请在下方拆分规则卡片操作" placement="top">
                        <el-tag size="small" type="primary" effect="plain" class="!h-5 !leading-5">拆分</el-tag>
                      </el-tooltip>
                    </div>
                  </td>
                  <td class="p-2"><el-switch v-model="step.enabled" size="small" @change="(val) => onStepEnabledChange(step, val)" /></td>
                  <td class="p-2">
                    <div class="flex items-center gap-1">
                      <el-slider v-model="step.threshold" :min="0" size="small" class="flex-1" />
                      <span class="text-xs w-8">{{ step.threshold }}%</span>
                    </div>
                  </td>
                  <td class="p-2">
                    <el-input v-model="step.displayLabel" size="small" placeholder="显示名称" />
                  </td>
                  <td v-if="isCustomMixed" class="p-2">
                    <el-select :model-value="step.detect_role || 'step'" size="small" class="w-full"
                      @update:model-value="(val) => onDetectRoleChange(step, val)">
                      <el-option label="步骤" value="step" />
                      <el-option label="物品" value="item" />
                    </el-select>
                  </td>
                  <td class="p-2 text-center">
                    <el-switch v-model="step.hide_in_view" size="small" />
                  </td>
                  <td class="p-2 text-center">
                    <el-color-picker v-model="step.box_color" size="small" :predefine="['#10b981','#ef4444','#f59e0b','#3b82f6','#a78bfa','#ec4899','#06b6d4','#84cc16']" />
                  </td>
                  <td class="p-2">
                    <div class="flex items-center gap-1 text-[10px] text-gray-400">
                      <span>W≤</span>
                      <el-input-number
                        v-model="step.box_max_width"
                        :min="0" :max="1" :step="0.05" :precision="2"
                        size="small" controls-position="right"
                        style="width: 68px"
                        placeholder="0"
                      />
                      <span class="ml-1">H≤</span>
                      <el-input-number
                        v-model="step.box_max_height"
                        :min="0" :max="1" :step="0.05" :precision="2"
                        size="small" controls-position="right"
                        style="width: 68px"
                        placeholder="0"
                      />
                    </div>
                  </td>
                  <td class="p-2 align-top">
                    <div class="flex flex-col gap-1 min-w-[7rem]">
                      <div class="flex flex-wrap gap-1">
                        <el-button size="small" type="primary" plain @click="$emit('open-step-roi-editor', step)">
                          {{ step.roi && step.roi.length >= 3 ? '重绘' : '设置' }}
                        </el-button>
                        <el-button v-if="step.roi && step.roi.length >= 3" size="small" type="danger" plain @click="clearStepRoi(step)">清除</el-button>
                      </div>
                      <svg v-if="step.roi && step.roi.length >= 3" width="72" height="40" viewBox="0 0 1 1" preserveAspectRatio="none"
                        class="border border-slate-700 bg-slate-950 rounded">
                        <polygon
                          :points="step.roi.map(p => `${p[0]},${p[1]}`).join(' ')"
                          fill="rgba(167,139,250,0.25)" stroke="#a78bfa" stroke-width="0.008" stroke-linejoin="round" />
                      </svg>
                      <span v-else class="text-[0.625rem] text-gray-500">未限制</span>
                    </div>
                  </td>
                  <td v-if="isSeqLike" class="p-2 align-top">
                    <el-popover placement="left" :width="340" trigger="click">
                      <template #reference>
                        <el-button size="small" plain
                          :type="step.device_gate && step.device_gate.enabled ? 'success' : 'info'">
                          {{ gateLabel(step) }}
                        </el-button>
                      </template>
                      <div class="space-y-2 text-xs">
                        <div class="flex items-center justify-between">
                          <span>启用外设门控（等秤条件才放行入周期）</span>
                          <el-switch :model-value="!!(step.device_gate && step.device_gate.enabled)"
                            size="small" @update:model-value="(v) => onGateEnabledChange(step, v)" />
                        </div>
                        <template v-if="step.device_gate && step.device_gate.enabled">
                          <div>
                            <label class="block text-gray-400 mb-1">门控类型</label>
                            <el-select v-model="step.device_gate.kind" size="small" class="w-full">
                              <el-option label="去皮门控（放件稳定 → 自动去皮 → 放行）" value="tare" />
                              <el-option label="称重判定（净重稳定 → 对比标准量 → 判定）" value="weight_judge" />
                            </el-select>
                          </div>
                          <div v-if="step.device_gate.kind === 'weight_judge'">
                            <label class="block text-gray-400 mb-1">判定料别（对应称重配置的型号标准量表）</label>
                            <el-select v-model="step.device_gate.material" size="small" class="w-full" clearable
                              placeholder="留空 = 按料别顺序自动取">
                              <el-option v-for="m in weighingMaterials" :key="m" :label="m" :value="m" />
                            </el-select>
                          </div>
                          <div v-if="step.device_gate.kind === 'weight_judge'" class="flex items-center justify-between">
                            <el-tooltip content="开 = 缺料/超量时步骤不放行，报警等工人纠正（重量变化后重新判定）；关 = 记录 NG 判定但放行继续流程" placement="top">
                              <span class="cursor-help border-b border-dashed border-gray-500">不合格拦截等纠正</span>
                            </el-tooltip>
                            <el-switch v-model="step.device_gate.block" size="small" />
                          </div>
                        </template>
                      </div>
                    </el-popover>
                  </td>
                </tr>
              </tbody>
            </table>
            <div v-if="!project.steps_config || project.steps_config.length === 0" class="text-center text-gray-500 py-8">
              暂无步骤配置，请先在"基础设置"中选择模型
            </div>
          </div>
        </div>

        <!-- ============ 同标签区域拆分 (v3.32): 一个原始标签 × 多区域 = 多个虚拟步骤 ============ -->
        <div v-if="project.logic_mode !== 'weighing'" class="border border-slate-700 rounded">
          <div class="px-3 py-2 bg-slate-800 border-b border-slate-700 flex items-center gap-2 flex-wrap">
            <span class="font-bold text-white text-sm">同标签区域拆分</span>
            <el-tooltip placement="top">
              <template #content>
                <div style="max-width: 360px; line-height: 1.5">
                  模型只能输出一个动作标签（如「打螺丝」）、但同一动作在<b>不同位置</b>发生代表<b>不同步骤</b>时用这个：
                  按检测框中心命中的区域，把原始标签改写成区域名对应的<b>虚拟步骤</b>（螺丝1~4），
                  下游顺序判定 / 事件 / 计数 / MES 全部按普通步骤工作。
                  区域支持「固定画面」和「锚点跟随」两种定位方式。
                </div>
              </template>
              <span class="cursor-help border-b border-dashed border-gray-500 text-xs text-gray-400">这是什么</span>
            </el-tooltip>
            <div class="flex-1"></div>
            <el-button size="small" type="primary" plain @click="$emit('open-label-split-editor', null)">新建拆分规则</el-button>
          </div>
          <div class="p-2">
            <table v-if="labelSplitRules.length" class="min-w-full text-left text-xs text-gray-300 border-collapse">
              <thead class="text-gray-400">
                <tr class="border-b border-slate-700">
                  <th class="p-2">原始标签</th>
                  <th class="p-2 w-24">定位方式</th>
                  <th class="p-2">虚拟步骤（区域名）</th>
                  <th class="p-2 w-32">未命中处理</th>
                  <th class="p-2 w-16">启用</th>
                  <th class="p-2 w-32">操作</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="ruleItem in labelSplitRules" :key="ruleItem.id" class="border-b border-slate-700 hover:bg-slate-700/30">
                  <td class="p-2 font-mono text-cyan-400">{{ ruleItem.source_label || '(未设)' }}</td>
                  <td class="p-2">
                    <div>{{ ruleItem.mode === 'anchor' ? `锚点跟随(${ruleItem.anchor_label || '?'})` : '固定画面' }}</div>
                    <div v-if="ruleItem.rounds && ruleItem.rounds.enabled" class="text-[10px] text-amber-400">
                      {{ ruleItem.rounds.count }}轮 · 切换={{ ruleItem.rounds.trigger_label || '?' }}
                      <span v-if="roundOverrideDesc(ruleItem)" class="text-cyan-400">· {{ roundOverrideDesc(ruleItem) }}</span>
                    </div>
                  </td>
                  <td class="p-2">
                    <template v-if="ruleItem.rounds && ruleItem.rounds.enabled">
                      <el-tag v-for="name in splitRuleStepNames(ruleItem)" :key="name" size="small" effect="plain"
                        class="!h-5 !leading-5 mr-1 mb-0.5">
                        {{ name }}
                      </el-tag>
                    </template>
                    <template v-else>
                      <el-tag v-for="region in (ruleItem.regions || [])" :key="region.name" size="small" effect="plain"
                        class="!h-5 !leading-5 mr-1" :style="{ borderColor: region.color, color: region.color }">
                        {{ region.name }}
                      </el-tag>
                    </template>
                  </td>
                  <td class="p-2 text-gray-400">
                    {{ ruleItem.unmatched === 'keep' ? '保留原标签' : (ruleItem.unmatched === 'map' ? `改写→${ruleItem.unmatched_label || '?'}` : '丢弃') }}
                  </td>
                  <td class="p-2"><el-switch v-model="ruleItem.enabled" size="small" /></td>
                  <td class="p-2">
                    <el-button size="small" type="primary" plain @click="$emit('open-label-split-editor', ruleItem)">编辑</el-button>
                    <el-button size="small" type="danger" plain @click="$emit('delete-label-split', ruleItem)">删除</el-button>
                  </td>
                </tr>
              </tbody>
            </table>
            <div v-else class="text-center text-gray-500 text-xs py-4">
              未配置 — 用于「同一动作标签按发生位置拆成多个步骤」的场景（多穴位锁付 / 对角打螺丝 / 左右半区分工位等）
            </div>
            <div v-if="labelSplitRules.length" class="text-[10px] text-gray-500 px-2 pt-1">
              ※ 区域名会自动生成同名虚拟步骤（步骤表带「拆分」徽标），阈值/时长/顺序在步骤表和逻辑设置里配；保存配置时自动同步
            </div>
          </div>
        </div>

        <!-- ============ 工件就位提示 (v3.32, 与拆分正交的独立小功能) ============ -->
        <div v-if="project.logic_mode !== 'weighing'" class="border border-slate-700 rounded">
          <div class="px-3 py-2 bg-slate-800 border-b border-slate-700 flex items-center gap-2 flex-wrap">
            <span class="font-bold text-white text-sm">工件就位提示</span>
            <el-tooltip placement="top"
              content="在监控画面上画一个引导框，要求工人把工件放进框里再作业：锚点目标（如前罩）中心在框内 = 绿框「已就位」，否则黄虚线提示。一期仅提示不拦截判定。常与「固定画面」的区域拆分配合使用。">
              <span class="cursor-help border-b border-dashed border-gray-500 text-xs text-gray-400">这是什么</span>
            </el-tooltip>
            <div class="flex-1"></div>
            <el-switch v-model="placementGuide.enabled" size="small" />
          </div>
          <div v-if="placementGuide.enabled" class="p-3 flex items-start gap-4 flex-wrap text-xs text-gray-300">
            <div class="w-64">
              <div class="text-gray-400 mb-1">用哪个标签判断工件位置（锚点）</div>
              <el-select v-model="placementGuide.anchor_label" size="small" filterable allow-create default-first-option
                :placeholder="(project.model_labels || []).length ? '从模型类别里选' : '先选主模型'" class="!w-full">
                <el-option v-for="lbl in (project.model_labels || [])" :key="lbl" :label="lbl" :value="lbl" />
              </el-select>
            </div>
            <div class="flex flex-col gap-1">
              <div class="text-gray-400">引导框区域</div>
              <div class="flex items-center gap-2">
                <el-button size="small" type="primary" plain @click="$emit('open-placement-guide-editor')">
                  {{ placementGuide.polygon && placementGuide.polygon.length >= 3 ? '重绘引导框' : '绘制引导框' }}
                </el-button>
                <el-button v-if="placementGuide.polygon && placementGuide.polygon.length >= 3"
                  size="small" type="danger" plain @click="placementGuide.polygon = null">清除</el-button>
              </div>
              <svg v-if="placementGuide.polygon && placementGuide.polygon.length >= 3" width="96" height="54" viewBox="0 0 1 1"
                preserveAspectRatio="none" class="border border-slate-700 bg-slate-950 rounded">
                <polygon :points="placementGuide.polygon.map(p => `${p[0]},${p[1]}`).join(' ')"
                  fill="rgba(34,197,94,0.2)" stroke="#22c55e" stroke-width="0.01" stroke-linejoin="round" />
              </svg>
            </div>
            <div class="w-48">
              <div class="text-gray-400 mb-1">就位后引导框怎么显示</div>
              <el-select v-model="placementGuide.display" size="small" class="!w-full">
                <el-option label="常驻显示（就位变绿）" value="always" />
                <el-option label="就位后淡化（只留细框）" value="fade_on_ready" />
                <el-option label="就位后隐藏" value="hide_on_ready" />
              </el-select>
            </div>
            <div class="text-[10px] text-gray-500 max-w-xs leading-relaxed">
              未就位时始终黄色虚线提醒；就位后按左侧策略显示。仅提示，不改变 OK/NG 判定。
            </div>
          </div>
        </div>

        <!-- ============ 表B: 模式行为参数 (随逻辑模式切换, 相互独立) ============ -->
        <div class="border border-slate-700 rounded">
          <div class="px-3 py-2 bg-slate-800 border-b border-slate-700 flex items-center gap-2 flex-wrap">
            <span class="font-bold text-white text-sm">
              {{ project.logic_mode === 'tracking' ? '物品行为参数 · 跟踪模式' : `步骤行为参数 · ${logicModeLabel}` }}
            </span>
            <span class="text-xs text-gray-500">
              仅展示已启用的{{ project.logic_mode === 'tracking' ? '物品' : '步骤' }}{{ isCustomMixed ? '；角色为「物品」的标签在下方「物品校验参数」表配置' : '' }}
            </span>
          </div>
          <div class="overflow-x-auto custom-scrollbar step-table-scroll">
            <table class="min-w-full w-max text-left text-xs text-gray-300 border-collapse whitespace-nowrap">
          <thead class="bg-slate-800 text-gray-400 sticky top-0 z-10">
            <tr class="border-b border-slate-700">
              <th class="p-2 min-w-[8rem]">标签</th>
              <th v-if="hasDurationsSlot" class="p-2 w-44">
                <el-tooltip placement="top">
                  <template #content>
                    <div style="max-width: 340px; line-height: 1.5">
                      <b>步骤耗时三档</b>（插件功能 · 单位秒 · 0 = 不启用该档）<br/>
                      最短 / 警告 / 最长，进行中实时判定：<br/>
                      · 走完未达<b>最短</b> → 报警 + 判 NG<br/>
                      · 进行中超<b>警告</b>（最短与最长之间）→ 只报警，不判 NG<br/>
                      · 进行中超<b>最长</b> → 报警 + 判 NG<br/>
                      需激活对应客户插件后此列才出现输入框（用插件管自管阈值时，项目原生「最短/最大持续」请留空）。
                    </div>
                  </template>
                  <span class="cursor-help border-b border-dashed border-gray-500">步骤耗时三档</span>
                </el-tooltip>
              </th>
              <template v-if="project.logic_mode === 'tracking'">
              <th class="p-2 w-28">
                <el-tooltip content="物品被短暂遮挡后仍算在场的最长时间，在此时间内不会被判定为消失（留空使用全局值）" placement="top">
                  <span class="cursor-help border-b border-dashed border-gray-500">遮挡容忍(秒)</span>
                </el-tooltip>
              </th>
              <th class="p-2 w-20">
                <el-tooltip content="开启后，物品放下后按位置锁定ID，不受ByteTrack的ID交换影响，适用于静止物品" placement="top">
                  <span class="cursor-help border-b border-dashed border-gray-500">位置注册</span>
                </el-tooltip>
              </th>
              <th class="p-2 w-28">
                <el-tooltip content="跟踪计数：按唯一物品ID计数；动作计数：按物品出现-消失次数计数（适用于堆叠遮挡场景）" placement="top">
                  <span class="cursor-help border-b border-dashed border-gray-500">计数模式</span>
                </el-tooltip>
              </th>
              <th class="p-2 w-24">
                <el-tooltip content="动作计数模式下，需要检测到多少次放入动作才算完成（仅动作计数模式有效）" placement="top">
                  <span class="cursor-help border-b border-dashed border-gray-500">需要次数</span>
                </el-tooltip>
              </th>
              <th class="p-2 w-24">
                <el-tooltip content="动作计数模式下，物品消失多少帧后确认为一次完成的动作（默认8帧）" placement="top">
                  <span class="cursor-help border-b border-dashed border-gray-500">消失确认帧</span>
                </el-tooltip>
              </th>
              <th class="p-2 w-20">
                <el-tooltip content="堆叠模式：同一物品已放好但被堆叠/遮挡时，画面消失再次出现算下一个。仅跟踪计数模式生效" placement="top">
                  <span class="cursor-help border-b border-dashed border-gray-500">堆叠模式</span>
                </el-tooltip>
              </th>
              <th class="p-2 w-24">
                <el-tooltip content="堆叠模式下，物品消失多少秒后再次出现算作下一个（默认1.0秒；建议大于「遮挡容忍」）" placement="top">
                  <span class="cursor-help border-b border-dashed border-gray-500">重现间隔(秒)</span>
                </el-tooltip>
              </th>
              <th class="p-2 w-20">
                <el-tooltip content="堆叠模式累计达到此数即认为该步骤完成。「每层个数」=1 时即层数；>1 时为各层个数之和（例：4 层 × 每层 24 = 96）" placement="top">
                  <span class="cursor-help border-b border-dashed border-gray-500">堆叠总数</span>
                </el-tooltip>
              </th>
              <th class="p-2 w-20">
                <el-tooltip content="每层/每批必须同帧数到的最少个数，达标才计入这一层（默认1=出现即算一层）。适用于整盘/整批同进同出的场景" placement="top">
                  <span class="cursor-help border-b border-dashed border-gray-500">每层个数</span>
                </el-tooltip>
              </th>
              <th class="p-2 w-20">
                <el-tooltip content="每层个数需连续满足多少帧才闩锁计数（默认1帧；调大可抑制误检瞬时凑数）" placement="top">
                  <span class="cursor-help border-b border-dashed border-gray-500">每层确认帧</span>
                </el-tooltip>
              </th>
              <th class="p-2 w-16">
                <el-tooltip content="满盘门：只验「每盘/每批是否数满『每层个数』」，任一盘短一个即判NG，不卡「堆叠总数」（盘数当辅助计数）。适用于连续供料、盘数难干净计但每盘必须满的场景" placement="top">
                  <span class="cursor-help border-b border-dashed border-gray-500">满盘门</span>
                </el-tooltip>
              </th>
              <th class="p-2 w-24">
                <el-tooltip content="同时最多识别几个该物品；填0=无上限。设为1即不论同时检测到多少个都视为同一个ID。仅跟踪计数模式生效" placement="top">
                  <span class="cursor-help border-b border-dashed border-gray-500">最大识别数</span>
                </el-tooltip>
              </th>
              <th v-if="settleOnCompleteActive" class="p-2 w-24">
                <el-tooltip content="齐件即结算模式下，新物品需连续被看见多少帧才计入账本（默认1=看见即入账；调大可抑制误检/叠放瞬时凑数导致的提前结算）。仅跟踪计数模式生效" placement="top">
                  <span class="cursor-help border-b border-dashed border-gray-500">确认放入帧数</span>
                </el-tooltip>
              </th>
              </template>
              <template v-if="project.logic_mode !== 'tracking'">
              <th class="p-2 w-28">
                <el-tooltip content="检测持续时间低于此值将被忽略（0.01-60秒，留空不限制）" placement="top">
                  <span class="cursor-help border-b border-dashed border-gray-500">最短持续(秒)</span>
                </el-tooltip>
              </th>
              <th class="p-2 w-28">
                <el-tooltip content="检测持续时间超过此值将被忽略（0.01-3600秒，留空不限制）" placement="top">
                  <span class="cursor-help border-b border-dashed border-gray-500">最大持续(秒)</span>
                </el-tooltip>
              </th>
              <th class="p-2 w-20">
                <el-tooltip content="开启后，步骤持续时间超过「最大持续」时自动判定当前周期为NG（需先设置最大持续时间）" placement="top">
                  <span class="cursor-help border-b border-dashed border-gray-500">超时NG</span>
                </el-tooltip>
              </th>
              <th class="p-2 w-28">
                <el-tooltip content="步骤从画面消失后，等待多久才正式认定它已经消失（默认0秒=立刻认定）。等待期间若画面里又出现别的有效步骤，会立刻结束等待按消失处理；该值同时承担了旧版『去重间隔』『丢帧容忍』的语义。" placement="top">
                  <span class="cursor-help border-b border-dashed border-gray-500">消失等待时间(秒)</span>
                </el-tooltip>
              </th>
              <th class="p-2 w-24">
                <el-tooltip content="开启后，消失等待期间即使画面里出现了别的有效步骤，本步骤的等待时间也照常走完（默认关闭=别的步骤一出现立刻按消失处理）。适用于工具驻留画面、多个步骤并行可见的产线：如吹枪插在工件上时进行敲击，吹枪短暂被遮挡不应被判定为已消失。" placement="top">
                  <span class="cursor-help border-b border-dashed border-gray-500">等待不被打断</span>
                </el-tooltip>
              </th>
              <th class="p-2 w-24">
                <el-tooltip content="连续检测到多少帧才算真正检测到该步骤（用于过滤误检，默认1帧即触发）" placement="top">
                  <span class="cursor-help border-b border-dashed border-gray-500">最少帧数</span>
                </el-tooltip>
              </th>
              <th class="p-2 w-20">
                <el-tooltip content="动态：步骤消失后计入周期；静态：持续检测到指定帧数后立即触发事件" placement="top">
                  <span class="cursor-help border-b border-dashed border-gray-500">检测类型</span>
                </el-tooltip>
              </th>
              <th class="p-2 w-24">
                <el-tooltip content="静态步骤连续检测到多少帧后触发事件（仅静态类型有效）" placement="top">
                  <span class="cursor-help border-b border-dashed border-gray-500">静态触发帧</span>
                </el-tooltip>
              </th>
              <th class="p-2 w-20">
                <el-tooltip content="静态步骤是否参与周期序列记录（仅静态类型有效）" placement="top">
                  <span class="cursor-help border-b border-dashed border-gray-500">参与周期</span>
                </el-tooltip>
              </th>
              <th class="p-2 w-28">
                <el-tooltip content="设为其他步骤的替补：当主步骤未检测到但替补被检测到时，自动补全主步骤" placement="top">
                  <span class="cursor-help border-b border-dashed border-gray-500">替补目标</span>
                </el-tooltip>
              </th>
              <th class="p-2 w-20">
                <el-tooltip content="当此步骤被替补覆盖时显示的默认PT值（秒），不填则显示'--'" placement="top">
                  <span class="cursor-help border-b border-dashed border-gray-500">默认PT</span>
                </el-tooltip>
              </th>
              <th v-if="project.logic_mode !== 'detection'" class="p-2 w-20">
                <el-tooltip content="开启后，该步骤仅在其之前的所有步骤都已完成时才被接受，可过滤环境误检" placement="top">
                  <span class="cursor-help border-b border-dashed border-gray-500">严格顺序</span>
                </el-tooltip>
              </th>
              <th class="p-2 w-20">
                <el-tooltip content="开启后，该步骤在一个周期内只接受一次，重复被忽略；关闭后，重复出现将被判为NG" placement="top">
                  <span class="cursor-help border-b border-dashed border-gray-500">单次接受</span>
                </el-tooltip>
              </th>
              </template>
            </tr>
          </thead>
          <tbody>
            <tr v-for="step in stepBehaviorRows" :key="step.id" class="border-b border-slate-700 hover:bg-slate-700/30">
              <td class="p-2 font-mono text-cyan-400">
                {{ step.displayLabel || step.label }}
                <span v-if="step.displayLabel && step.displayLabel !== step.label" class="text-gray-500 ml-1">({{ step.label }})</span>
                <el-tooltip v-if="containerRoleOf(step.label)" placement="top">
                  <template #content>
                    <div style="max-width: 340px; line-height: 1.5">
                      该标签已被「托盘容器」机制接管（角色：{{ containerRoleOf(step.label) }}）。<br/>
                      本表的最少帧数 / 最短·最大持续 / 消失等待 / 检测类型等参数<b>对它不生效</b>，
                      对应门槛请到「装箱清点 → 托盘容器 / 进箱确认」配置
                      （如动作最少帧数、消失确认帧、动作不应期）。<br/>
                      「标签与检测属性」表里的置信度阈值与步骤ROI 照常生效。
                    </div>
                  </template>
                  <el-tag size="small" type="warning" effect="plain" class="!h-5 !leading-5 ml-1">
                    {{ containerRoleOf(step.label) }} · 参数在装箱清点
                  </el-tag>
                </el-tooltip>
              </td>
              <td v-if="hasDurationsSlot" class="p-2">
                <TjSlot name="project.step-cell.durations" :step="step" :project="project">
                  <span class="text-[0.625rem] text-gray-600">需插件</span>
                </TjSlot>
              </td>
              <template v-if="project.logic_mode === 'tracking'">
              <td class="p-2">
                <el-input-number
                  v-model="step.tracking_max_lost_seconds"
                  size="small"
                  :min="0"
                  :step="0.5"
                  :precision="2"
                  :controls="false"
                  placeholder="5.0"
                  class="w-full"
                />
              </td>
              <td class="p-2 text-center">
                <el-switch v-model="step.tracking_position_lock" size="small" />
              </td>
              <td class="p-2">
                <el-select v-model="step.count_mode" size="small" class="w-full">
                  <el-option label="跟踪计数" value="track" />
                  <el-option label="动作计数" value="event" />
                </el-select>
              </td>
              <td class="p-2">
                <el-input-number
                  v-model="step.event_required_count"
                  size="small"
                  :min="0"
                  :step="1"
                  :precision="2"
                  :controls="false"
                  :disabled="step.count_mode !== 'event'"
                  placeholder="1"
                  class="w-full"
                />
              </td>
              <td class="p-2">
                <el-input-number
                  v-model="step.event_gone_frames"
                  size="small"
                  :min="0"
                  :step="1"
                  :precision="2"
                  :controls="false"
                  :disabled="step.count_mode !== 'event'"
                  placeholder="8"
                  class="w-full"
                />
              </td>
              <td class="p-2 text-center">
                <el-switch
                  v-model="step.stack_enabled"
                  size="small"
                  :disabled="step.count_mode !== 'track'"
                />
              </td>
              <td class="p-2">
                <el-input-number
                  v-model="step.stack_reappear_seconds"
                  size="small"
                  :min="0.1"
                  :step="0.1"
                  :precision="2"
                  :controls="false"
                  :disabled="!step.stack_enabled || step.count_mode !== 'track'"
                  placeholder="1.0"
                  class="w-full"
                />
              </td>
              <td class="p-2">
                <el-input-number
                  v-model="step.stack_required_count"
                  size="small"
                  :min="2"
                  :step="1"
                  :precision="0"
                  :controls="false"
                  :disabled="!step.stack_enabled || step.count_mode !== 'track'"
                  placeholder="2"
                  class="w-full"
                />
              </td>
              <td class="p-2">
                <el-input-number
                  v-model="step.stack_layer_min_count"
                  size="small"
                  :min="1"
                  :step="1"
                  :precision="0"
                  :controls="false"
                  :disabled="!step.stack_enabled || step.count_mode !== 'track'"
                  placeholder="1"
                  class="w-full"
                />
              </td>
              <td class="p-2">
                <el-input-number
                  v-model="step.stack_layer_min_frames"
                  size="small"
                  :min="1"
                  :step="1"
                  :precision="0"
                  :controls="false"
                  :disabled="!step.stack_enabled || step.count_mode !== 'track'"
                  placeholder="1"
                  class="w-full"
                />
              </td>
              <td class="p-2 text-center">
                <el-switch
                  v-model="step.stack_gate_only"
                  size="small"
                  :disabled="!step.stack_enabled || step.count_mode !== 'track'"
                />
              </td>
              <td class="p-2">
                <el-input-number
                  v-model="step.max_recognized"
                  size="small"
                  :min="0"
                  :step="1"
                  :precision="0"
                  :controls="false"
                  :disabled="step.count_mode !== 'track'"
                  placeholder="无上限"
                  class="w-full"
                />
              </td>
              <td v-if="settleOnCompleteActive" class="p-2">
                <el-input-number
                  v-model="step.settle_confirm_frames"
                  data-testid="settle-confirm-frames-input"
                  size="small"
                  :min="1"
                  :step="1"
                  :precision="0"
                  :controls="false"
                  :disabled="step.count_mode !== 'track'"
                  placeholder="1"
                  class="w-full"
                />
              </td>
              </template>
              <template v-if="project.logic_mode !== 'tracking'">
              <td class="p-2">
                <el-input-number 
                  v-model="step.min_duration" 
                  size="small" 
                  :min="0" 
                  :step="0.1"
                  :precision="2"
                  :controls="false"
                  placeholder="不限"
                  class="w-full"
                />
              </td>
              <td class="p-2">
                <el-input-number 
                  v-model="step.max_duration" 
                  size="small" 
                  :min="0" 
                  :step="0.1"
                  :precision="2"
                  :controls="false"
                  placeholder="不限"
                  class="w-full"
                />
              </td>
              <td class="p-2 text-center">
                <el-switch v-model="step.timeout_ng" size="small" :disabled="!step.max_duration" />
              </td>
              <td class="p-2">
                <el-tooltip :disabled="!consecutiveDupStepIds.has(step.id)"
                  content="该步骤在序列中连续重复出现，消失等待时间固定为0：每次消失立即结算，下一次出现才能被识别为新的一次" placement="top">
                  <el-input-number 
                    v-model="step.disappear_delay" 
                    size="small" 
                    :min="0" 
                    :step="0.1"
                    :precision="2"
                    :controls="false"
                    :disabled="consecutiveDupStepIds.has(step.id)"
                    placeholder="默认0秒"
                    class="w-full"
                  />
                </el-tooltip>
              </td>
              <td class="p-2 text-center">
                <el-switch
                  v-model="step.disappear_uninterruptible"
                  size="small"
                  :disabled="!step.disappear_delay"
                />
              </td>
              <td class="p-2">
                <el-input-number 
                  v-model="step.min_frames" 
                  size="small" 
                  :min="0" 
                  :step="1"
                  :precision="2"
                  :controls="false"
                  placeholder="默认1"
                  class="w-full"
                />
              </td>
              <td class="p-2">
                <el-select v-model="step.detection_type" size="small" class="w-full">
                  <el-option value="dynamic" label="动态" />
                  <el-option value="static" label="静态" />
                </el-select>
              </td>
              <td class="p-2">
                <el-input-number 
                  v-model="step.static_trigger_frames" 
                  size="small" 
                  :min="0" 
                  :step="1"
                  :precision="2"
                  :controls="false"
                  placeholder="30"
                  class="w-full"
                  :disabled="step.detection_type !== 'static'"
                />
              </td>
              <td class="p-2">
                <el-switch 
                  v-model="step.join_cycle" 
                  size="small"
                  :disabled="step.detection_type !== 'static'"
                />
              </td>
              <td class="p-2">
                <el-select 
                  v-model="step.backup_for" 
                  size="small" 
                  clearable 
                  placeholder="无"
                  class="w-full"
                >
                  <el-option 
                    v-for="s in (project.steps_config || []).filter(s => s.enabled && s.id !== step.id && !s.backup_for)" 
                    :key="s.id" 
                    :label="s.displayLabel || s.label" 
                    :value="s.id" 
                  />
                </el-select>
              </td>
              <td class="p-2">
                <el-input-number 
                  v-model="step.default_pt" 
                  size="small" 
                  :min="0" 
                  :step="0.1"
                  :precision="2"
                  :controls="false"
                  placeholder="--"
                  class="w-full"
                />
              </td>
              <td v-if="project.logic_mode !== 'detection'" class="p-2">
                <el-tooltip
                  :disabled="!isSettlementStep(step)"
                  content="结算步骤不可设置严格顺序"
                  placement="top"
                >
                  <el-switch
                    v-model="step.strict_order"
                    size="small"
                    :disabled="isSettlementStep(step)"
                  />
                </el-tooltip>
              </td>
              <td class="p-2">
                <el-tooltip
                  :disabled="!isSettlementStep(step)"
                  content="结算步骤不可设置单次接受"
                  placement="top"
                >
                  <el-switch
                    v-model="step.accept_once"
                    size="small"
                    :disabled="isSettlementStep(step)"
                  />
                </el-tooltip>
              </td>
              </template>
            </tr>
          </tbody>
        </table>
            <div v-if="stepBehaviorRows.length === 0" class="text-center text-gray-500 py-6 text-xs">
              暂无已启用的{{ project.logic_mode === 'tracking' ? '物品' : '步骤' }} — 请先在上方「标签与检测属性」表中启用
            </div>
          </div>
        </div>

        <!-- ============ 表C: 物品校验参数 (仅自定义混合模式, 两套原生词汇) ============ -->
        <div v-if="isCustomMixed" class="border border-slate-700 rounded">
          <div class="px-3 py-2 bg-slate-800 border-b border-slate-700 flex items-center gap-2 flex-wrap">
            <span class="font-bold text-white text-sm">物品校验参数 · {{ project.custom_mixed_with === 'per_item' ? '混合逐件覆盖' : '混合跟踪清点' }}</span>
            <span class="text-xs text-gray-500">
              {{ project.custom_mixed_with === 'per_item'
                ? '角色为「物品」的行 = 一条「目标 ⟶ 覆盖动作」配对，参数与独立逐件模式完全一致；周期何时开始/结算由上方步骤决定'
                : '角色为「物品」的标签按独立跟踪模式的同一套机制清点（唯一ID/动作计数/堆叠），周期何时开始/结算由上方步骤决定' }}
            </span>
          </div>

          <!-- 混合跟踪: 原生跟踪行为列 + 期望数量 -->
          <div v-if="project.custom_mixed_with === 'tracking'" class="overflow-x-auto custom-scrollbar step-table-scroll">
            <table class="min-w-full w-max text-left text-xs text-gray-300 border-collapse whitespace-nowrap">
              <thead class="bg-slate-800 text-gray-400 sticky top-0 z-10">
                <tr class="border-b border-slate-700">
                  <th class="p-2 min-w-[8rem]">物品</th>
                  <th class="p-2 w-28">
                    <el-tooltip content="一个周期内应清点到的个体总数（跟踪计数=唯一ID去重；动作计数/堆叠模式此列自动失效，以各自「需要次数/堆叠层数」为准）。0 = 只展示不判定" placement="top">
                      <span class="cursor-help border-b border-dashed border-gray-500">期望数量</span>
                    </el-tooltip>
                  </th>
                  <th class="p-2 w-28">
                    <el-tooltip content="物品被短暂遮挡后仍算在场的最长时间，在此时间内不会被判定为消失（留空默认 5 秒）" placement="top">
                      <span class="cursor-help border-b border-dashed border-gray-500">遮挡容忍(秒)</span>
                    </el-tooltip>
                  </th>
                  <th class="p-2 w-20">
                    <el-tooltip content="开启后，物品放下后按位置锁定ID，不受ByteTrack的ID交换影响，适用于静止物品" placement="top">
                      <span class="cursor-help border-b border-dashed border-gray-500">位置注册</span>
                    </el-tooltip>
                  </th>
                  <th class="p-2 w-28">
                    <el-tooltip content="跟踪计数：按唯一物品ID计数；动作计数：按物品出现-消失次数计数（适用于堆叠遮挡场景）" placement="top">
                      <span class="cursor-help border-b border-dashed border-gray-500">计数模式</span>
                    </el-tooltip>
                  </th>
                  <th class="p-2 w-24">
                    <el-tooltip content="动作计数模式下，需要检测到多少次放入动作才算达标（仅动作计数模式有效）" placement="top">
                      <span class="cursor-help border-b border-dashed border-gray-500">需要次数</span>
                    </el-tooltip>
                  </th>
                  <th class="p-2 w-24">
                    <el-tooltip content="动作计数模式下，物品消失多少帧后确认为一次完成的动作（默认8帧）" placement="top">
                      <span class="cursor-help border-b border-dashed border-gray-500">消失确认帧</span>
                    </el-tooltip>
                  </th>
                  <th class="p-2 w-20">
                    <el-tooltip content="堆叠模式：同一物品已放好但被堆叠/遮挡时，画面消失再次出现算下一个。仅跟踪计数模式生效" placement="top">
                      <span class="cursor-help border-b border-dashed border-gray-500">堆叠模式</span>
                    </el-tooltip>
                  </th>
                  <th class="p-2 w-24">
                    <el-tooltip content="堆叠模式下，物品消失多少秒后再次出现算作下一个（默认1.0秒；建议大于「遮挡容忍」）" placement="top">
                      <span class="cursor-help border-b border-dashed border-gray-500">重现间隔(秒)</span>
                    </el-tooltip>
                  </th>
                  <th class="p-2 w-20">
                    <el-tooltip content="堆叠模式累计达到此数即达标。「每层个数」=1 时即层数；>1 时为各层个数之和（例：4 盘 × 每盘 24 = 96）" placement="top">
                      <span class="cursor-help border-b border-dashed border-gray-500">堆叠总数</span>
                    </el-tooltip>
                  </th>
                  <th class="p-2 w-20">
                    <el-tooltip content="每层/每批必须同帧数到的最少个数，达标才计入这一层（默认1=出现即算一层）。适用于整盘进出上料位的场景" placement="top">
                      <span class="cursor-help border-b border-dashed border-gray-500">每层个数</span>
                    </el-tooltip>
                  </th>
                  <th class="p-2 w-20">
                    <el-tooltip content="每层个数需连续满足多少帧才闩锁计数（默认1帧；调大可抑制误检瞬时凑数）" placement="top">
                      <span class="cursor-help border-b border-dashed border-gray-500">每层确认帧</span>
                    </el-tooltip>
                  </th>
                  <th class="p-2 w-16">
                    <el-tooltip content="满盘门：只验「每盘/每批是否数满『每层个数』」，任一盘短一个即判NG，不卡「堆叠总数」（盘数当辅助计数）。适用于连续供料、盘数难干净计但每盘必须满的场景" placement="top">
                      <span class="cursor-help border-b border-dashed border-gray-500">满盘门</span>
                    </el-tooltip>
                  </th>
                  <th class="p-2 w-24">
                    <el-tooltip content="同时最多识别几个该物品；填0=无上限。设为1即不论同时检测到多少个都视为同一个ID。仅跟踪计数模式生效" placement="top">
                      <span class="cursor-help border-b border-dashed border-gray-500">最大识别数</span>
                    </el-tooltip>
                  </th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="step in mixItemRows" :key="step.id" class="border-b border-slate-700 hover:bg-slate-700/30">
                  <td class="p-2 font-mono text-cyan-400">
                    {{ step.displayLabel || step.label }}
                    <span v-if="step.displayLabel && step.displayLabel !== step.label" class="text-gray-500 ml-1">({{ step.label }})</span>
                  </td>
                  <td class="p-2">
                    <el-input-number v-model="step.expected_count"
                      :min="0" :step="1" :precision="0" size="small" :controls="false"
                      :disabled="step.count_mode === 'event' || step.stack_enabled"
                      placeholder="0=只展示" class="w-full" />
                  </td>
                  <td class="p-2">
                    <el-input-number v-model="step.tracking_max_lost_seconds"
                      :min="0" :step="0.5" :precision="2" size="small" :controls="false"
                      placeholder="5.0" class="w-full" />
                  </td>
                  <td class="p-2 text-center">
                    <el-switch v-model="step.tracking_position_lock" size="small" />
                  </td>
                  <td class="p-2">
                    <el-select v-model="step.count_mode" size="small" class="w-full">
                      <el-option label="跟踪计数" value="track" />
                      <el-option label="动作计数" value="event" />
                    </el-select>
                  </td>
                  <td class="p-2">
                    <el-input-number v-model="step.event_required_count"
                      :min="0" :step="1" :precision="0" size="small" :controls="false"
                      :disabled="step.count_mode !== 'event'" placeholder="1" class="w-full" />
                  </td>
                  <td class="p-2">
                    <el-input-number v-model="step.event_gone_frames"
                      :min="0" :step="1" :precision="0" size="small" :controls="false"
                      :disabled="step.count_mode !== 'event'" placeholder="8" class="w-full" />
                  </td>
                  <td class="p-2 text-center">
                    <el-switch v-model="step.stack_enabled" size="small"
                      :disabled="step.count_mode !== 'track'" />
                  </td>
                  <td class="p-2">
                    <el-input-number v-model="step.stack_reappear_seconds"
                      :min="0.1" :step="0.1" :precision="2" size="small" :controls="false"
                      :disabled="!step.stack_enabled || step.count_mode !== 'track'"
                      placeholder="1.0" class="w-full" />
                  </td>
                  <td class="p-2">
                    <el-input-number v-model="step.stack_required_count"
                      :min="2" :step="1" :precision="0" size="small" :controls="false"
                      :disabled="!step.stack_enabled || step.count_mode !== 'track'"
                      placeholder="2" class="w-full" />
                  </td>
                  <td class="p-2">
                    <el-input-number v-model="step.stack_layer_min_count"
                      :min="1" :step="1" :precision="0" size="small" :controls="false"
                      :disabled="!step.stack_enabled || step.count_mode !== 'track'"
                      placeholder="1" class="w-full" />
                  </td>
                  <td class="p-2">
                    <el-input-number v-model="step.stack_layer_min_frames"
                      :min="1" :step="1" :precision="0" size="small" :controls="false"
                      :disabled="!step.stack_enabled || step.count_mode !== 'track'"
                      placeholder="1" class="w-full" />
                  </td>
                  <td class="p-2 text-center">
                    <el-switch v-model="step.stack_gate_only" size="small"
                      :disabled="!step.stack_enabled || step.count_mode !== 'track'" />
                  </td>
                  <td class="p-2">
                    <el-input-number v-model="step.max_recognized"
                      :min="0" :step="1" :precision="0" size="small" :controls="false"
                      :disabled="step.count_mode !== 'track'" placeholder="无上限" class="w-full" />
                  </td>
                </tr>
              </tbody>
            </table>
            <div v-if="mixItemRows.length === 0" class="text-center text-gray-500 py-6 text-xs">
              暂无物品 — 在上方「标签与检测属性」表把对应标签的角色切换为「物品」
            </div>
            <!-- 抗闪烁全局开关 (与独立跟踪模式同一组字段) -->
            <div v-if="mixItemRows.length > 0" class="px-3 py-2 border-t border-slate-700 flex items-center gap-5 text-xs flex-wrap">
              <span class="text-gray-500">抗闪烁（全局，与独立跟踪模式同义）:</span>
              <div class="flex items-center gap-1.5">
                <el-tooltip content="两个物品ID在相邻帧位置互换时自动纠正（ByteTrack 偶发ID交换）" placement="top">
                  <span class="text-gray-400 cursor-help">ID交换检测</span>
                </el-tooltip>
                <el-switch v-model="project.tracking_swap_detection" size="small" />
              </div>
              <div class="flex items-center gap-1.5">
                <el-tooltip content="用颜色直方图辅助再识别，减少短暂遮挡后的ID漂移" placement="top">
                  <span class="text-gray-400 cursor-help">外观特征辅助</span>
                </el-tooltip>
                <el-switch v-model="project.tracking_appearance_match" size="small" />
              </div>
              <div class="flex items-center gap-1.5">
                <el-tooltip content="物品稳定 N 帧后锁定其ID不再变化" placement="top">
                  <span class="text-gray-400 cursor-help">ID锁定</span>
                </el-tooltip>
                <el-switch v-model="project.tracking_id_lock" size="small" />
              </div>
              <div v-if="project.tracking_id_lock" class="flex items-center gap-1.5">
                <span class="text-gray-400">锁定帧数</span>
                <el-input-number v-model="project.tracking_id_lock_frames" :min="0" :step="5" :precision="0" size="small" class="!w-24" />
              </div>
            </div>
          </div>

          <!-- 混合逐件: 原生「目标 ⟶ 覆盖动作」配对卡片 (与独立逐件模式同一套字段) -->
          <div v-else class="p-3 space-y-3">
            <div
              v-for="step in mixItemRows"
              :key="'mixpi-'+step.id"
              class="border border-slate-700 rounded p-3 bg-slate-900/40">
              <div class="flex items-center gap-2 mb-3">
                <span class="text-cyan-400 font-bold">{{ step.displayLabel || step.label }}</span>
                <span v-if="step.displayLabel && step.displayLabel !== step.label" class="text-gray-500 text-xs">({{ step.label }})</span>
                <span class="text-[10px] text-gray-500 ml-auto">目标 ⟶ 覆盖动作 配对</span>
              </div>
              <div v-if="step.per_item" class="grid grid-cols-2 gap-3">
                <div>
                  <div class="text-[11px] text-gray-400 mb-1">要被覆盖的目标标签 <span class="text-amber-400">(可多选 = "或")</span></div>
                  <el-select
                    :model-value="_pi_itemLabelToArray(step.per_item.item_label)"
                    @update:model-value="(v) => { step.per_item.item_label = _pi_itemLabelFromArray(v); }"
                    size="small" multiple filterable allow-create default-first-option
                    collapse-tags collapse-tags-tooltip
                    :placeholder="(project.model_labels || []).length ? '从模型类别里选' : '先选主模型才能列出类别'"
                    class="!w-full">
                    <el-option
                      v-for="lbl in (project.model_labels || [])"
                      :key="lbl" :label="lbl" :value="lbl" />
                  </el-select>
                  <div class="text-[10px] text-gray-500 mt-1">画面里出现任一标签都算"目标"；目标标签不参与上方步骤序列</div>
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
                      :key="lbl" :label="lbl" :value="lbl" />
                  </el-select>
                  <div class="text-[10px] text-gray-500 mt-1">持续与目标重叠 → 该目标算"被覆盖"；若它同时是上方某个步骤，两边共享互不干扰</div>
                </div>
                <div>
                  <div class="text-[11px] text-gray-400 mb-1">每周期已知有几件目标？</div>
                  <el-input-number
                    v-model="step.per_item.expected_count"
                    size="small" :min="0" :step="1" :precision="0" class="!w-full" placeholder="0 = 自动" />
                  <div class="text-[10px] text-gray-500 mt-1">填已知数量 → 锁定封顶、少件判 NG（虚拟漏件）；填 0 = 周期内随见随建</div>
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
                <div class="col-span-2 px-2 py-1.5 bg-slate-900/60 border border-slate-700 rounded">
                  <div class="flex items-center justify-between gap-3">
                    <div class="flex-1">
                      <div class="text-[11px] font-bold text-cyan-300">小物件场景: 用"中心点"判定覆盖</div>
                      <div class="text-[10px] text-gray-500 mt-0.5">
                        适用 <span class="text-amber-300">涂黑 / 喷漆 / 扫码贴标</span> 这类"动作框远大于目标"的场景<br/>
                        开启: 目标中心点落在动作框内即算覆盖 (上方"重合度"自动忽略)
                      </div>
                    </div>
                    <el-switch v-model="step.per_item.coverage_use_center"
                      active-text="中心点" inactive-text="重合度" inline-prompt size="default" />
                  </div>
                </div>
              </div>
            </div>
            <div v-if="mixItemRows.length === 0" class="text-center text-gray-500 py-6 text-xs">
              暂无物品 — 在上方「标签与检测属性」表把对应标签的角色切换为「物品」
            </div>
            <div v-if="mixItemRows.length > 0" class="text-[10px] text-gray-500">
              ※ 周期由上方步骤侧驱动：周期开始时个体清零重新锁定，步骤侧结算时检查"目标是否全部被覆盖 + 件数是否达期望"，任一不满足整周期降级 NG<br/>
              ※ 独立逐件模式的"收尾标签 / 稳定窗口 / 双超时 / 手动结算"在混合下不生效（周期主权在步骤侧）
            </div>
          </div>
        </div>
      </div>
    </div>
</template>

<script setup>
// ==================== 步骤/物品设置 Tab（2026-07 拆分批次 P-5 自 index.vue 平移） ====================
// 三表结构: 表A 标签通用属性(启用/角色/ROI/颜色) / 表B 模式行为(随 logic_mode 换列)
//           / 表C 混合模式物品校验(detect_role='item' 行)。
// 数据流约定（拆分原则 2）：直接原位修改父级传入的 project.steps_config 等子树，
// 保存仍由父级"保存配置"按钮统一走 updateProject。
// 与父级共享的两条链路走 props（不是平移进来）:
//   consecutiveDupStepIds → 父级 syncDupDisappearDelay watch 同源（连续重复清消失延迟）
//   isSettlementStep      → 父级 settlement_mode watch 同源（结算步禁严格顺序/单次接受）
// 打开步骤 ROI 编辑器走 emit（对话框编排/通道解析/保存路由在父级）。
import { computed } from 'vue';
import TjSlot from '@/components/TjSlot.vue';
import { usePluginThemeStore } from '@/store/usePluginThemeStore';
import { _pi_itemLabelToArray, _pi_itemLabelFromArray } from './perItemLabel';
import { ensureMixItemDefaults } from './mixItemDefaults';
import { applyStepEnabledChange } from './stepEnabled';
import { splitRuleStepNames } from './labelSplit';

const props = defineProps({
  project: { type: Object, required: true },
  // Set<step_id>: 当前生效序列里"连续重复"的步骤（父级与消失延迟联动共用同一份）
  consecutiveDupStepIds: { type: Set, default: () => new Set() },
  // (step) => boolean: 结算步骤判定（父级与 settlement_mode watch 共用同一实现）
  isSettlementStep: { type: Function, default: () => false },
});
defineEmits(['open-step-roi-editor', 'open-label-split-editor', 'delete-label-split', 'open-placement-guide-editor']);

// ==================== 同标签区域拆分 + 工件就位提示 (v3.32) ====================
// 数据都挂在 pipeline_config 下, 与父级 initProjectDefaults 注入的默认值同源;
// 规则的新建/编辑走 LabelSplitDialog(父级编排), 本表只做列表展示 + 启停 + 删除入口。
const labelSplitRules = computed(() => props.project?.pipeline_config?.label_splits || []);

// 多轮次规则配了每轮独立区域时的表格提示（如 "第2轮独立区域"）
const roundOverrideDesc = (ruleItem) => {
  const ov = ruleItem?.rounds?.region_overrides;
  if (!ov || typeof ov !== 'object') return '';
  const rnds = Object.keys(ov).filter(k => Array.isArray(ov[k]) && ov[k].length).sort();
  return rnds.length ? `第${rnds.join('/')}轮独立区域` : '';
};
const placementGuide = computed(() => {
  const pc = props.project?.pipeline_config;
  if (pc && !pc.placement_guide) {
    pc.placement_guide = { enabled: false, anchor_label: '', polygon: null, mode: 'hint', display: 'always' };
  }
  // 老配置无 display 字段 → 补默认"常驻"(与 v3.32 首发行为一致)
  if (pc?.placement_guide && !pc.placement_guide.display) {
    pc.placement_guide.display = 'always';
  }
  return pc?.placement_guide || { enabled: false, anchor_label: '', polygon: null, mode: 'hint', display: 'always' };
});

// v3.13 M2.2b: 插件可注入"耗时统计"列（project.step-cell.durations 槽位）
const pluginThemeStore = usePluginThemeStore();
const hasDurationsSlot = computed(() => !!pluginThemeStore.getSlotComponent('project.step-cell.durations'));

// v3.19.x: 自定义混合模式（逐件/跟踪子状态机）是否启用 —— 控制步骤表"角色/物品参数"列
const isCustomMixed = computed(() => {
  const p = props.project;
  return !!(p && p.logic_mode === 'custom'
    && (p.custom_mixed_with === 'per_item' || p.custom_mixed_with === 'tracking'));
});

const onDetectRoleChange = (step, val) => {
  step.detect_role = val;
  if (val === 'item') ensureMixItemDefaults(props.project, step);
};

// v3.50 齐件即结算: 仅逻辑设置开了"全部合格立即结算"且策略为 ROI离开/容器时,
// 步骤表才露出"确认放入帧数"列
const settleOnCompleteActive = computed(() => {
  const p = props.project;
  return !!(p && p.tracking_settle_on_complete
    && ['roi_exit', 'container'].includes(p.tracking_cycle_strategy));
});

// ==================== v3.35 步骤外设门控（融合模式：视觉 SOP + 秤门控） ====================
// 顺序类模式专属；启用任一门控 → 注入 pipeline_config.weighing（drive_mode='step_gate'），
// 「称重配置」页签随之出现（父级 index.vue 按此条件渲染）。全关时不删已配的秤参数。
const isSeqLike = computed(() => {
  const p = props.project;
  return !!(p && (p.logic_mode === 'sequential'
    || (p.logic_mode === 'custom' && p.custom_based_on === 'sequential')));
});

const weighingMaterials = computed(() =>
  props.project?.pipeline_config?.weighing?.materials || []);

const gateLabel = (step) => {
  const g = step.device_gate;
  if (!g || !g.enabled) return '未启用';
  return g.kind === 'weight_judge' ? '称重判定' : '去皮门控';
};

const onGateEnabledChange = (step, enabled) => {
  if (!step.device_gate) {
    step.device_gate = { enabled: false, kind: 'tare', material: '', block: true };
  }
  step.device_gate.enabled = enabled;
  if (enabled) {
    const p = props.project;
    if (!p.pipeline_config) p.pipeline_config = {};
    if (!p.pipeline_config.weighing) {
      p.pipeline_config.weighing = {
        drive_mode: 'step_gate',
        materials: [], models: {},
        tare_mode: 'auto_stable', tare_trigger_weight: 0.05, tare_settle_samples: 3,
        stable_tol: 0.003, stable_min_samples: 3, measure_min_weight: 0.005,
        require_operator: false, require_model: true,
        material_check: 'off', auto_zero_after_done: false,
        alarm_event_shortage: 2, alarm_event_over: 2,
        alarm_event_wrong: 2, alarm_event_precheck: 2, alarm_event_guard: 2,
        context_expiry: { mode: 'never', reset_time: '08:00', shifts: [], hours: 8, expire_fields: ['model'] },
        visual_guard: { enabled: false, source: 'region_action', rules: [], wrong_block: true, cooldown_sec: 5 },
      };
    } else {
      p.pipeline_config.weighing.drive_mode = 'step_gate';
    }
  }
};

// ==================== 步骤设置页三表拆分（标签通用属性 / 模式行为 / 物品校验） ====================
// 表B行：已启用步骤；混合模式下排除物品行（物品归表C）。跟踪模式同样走此表（物品行为列）
const stepBehaviorRows = computed(() => {
  const steps = props.project?.steps_config || [];
  return steps.filter(s => s.enabled && !(isCustomMixed.value && s.detect_role === 'item'));
});

// v3.44.4: 容器机制接管的标签 (容器本体/进箱动作) — 表B步骤级参数对它们不生效,
// 行内挂警示标指路逻辑设置, 治"改了最少帧数没反应"的现场困惑 (上银 7-27)
const containerRoleOf = (label) => {
  const p = props.project;
  if (!p || p.logic_mode !== 'custom' || p.custom_mixed_with !== 'tracking'
      || !p.custom_mix_container_enabled || !label) return null;
  if (label === p.custom_mix_container_label) return '容器';
  if (label === p.custom_mix_container_action_label) return '进箱动作';
  return null;
};

// 表C行：混合模式下角色为"物品"的已启用标签
const mixItemRows = computed(() => {
  if (!isCustomMixed.value) return [];
  const steps = props.project?.steps_config || [];
  return steps.filter(s => s.enabled && s.detect_role === 'item');
});

const logicModeLabel = computed(() => ({
  sequential: '顺序模式',
  detection: '检测模式',
  custom: '自定义模式',
  tracking: '跟踪模式',
  per_item: '逐件覆盖模式',
}[props.project?.logic_mode] || props.project?.logic_mode || ''));

const clearStepRoi = (step) => {
  if (step) step.roi = null;
};

// 步骤启用状态变化时的清理/恢复逻辑（实现在 stepEnabled.js, 与父级副模型步骤清理共用）
const onStepEnabledChange = (step, enabled) => {
  applyStepEnabledChange(props.project, step, enabled);
};
</script>
