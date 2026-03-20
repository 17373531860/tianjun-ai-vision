<template>
  <div class="h-[calc(100vh-7rem)] flex flex-col gap-4">
    <!-- Header -->
    <div class="flex justify-between items-center bg-slate-900/80 p-4 rounded-lg border border-slate-700 shrink-0">
      <div>
        <h1 class="text-xl font-bold text-white">项目管理</h1>
        <p class="text-xs text-gray-400 mt-1">管理机器视觉检测项目、配置识别逻辑与事件。</p>
      </div>
      <div class="flex gap-2">
        <el-button type="primary" :icon="Plus" @click="openCreateDialog">新建项目</el-button>
        <el-button type="success" :icon="Check" :disabled="!activeProject" @click="handleActivateProject">
          启用当前项目
        </el-button>
      </div>
    </div>

    <!-- Main Content -->
    <div class="flex-1 flex gap-4 overflow-hidden">
      <!-- Project List & Context Sidebar -->
      <div class="w-80 flex flex-col gap-4">
        <!-- Project List -->
        <div class="bg-slate-900 rounded-lg border border-slate-700 flex flex-col overflow-hidden transition-all duration-300"
             :class="activeProject && (activeTab === 'logic' || activeTab === 'events') ? 'h-1/2' : 'h-full'">
          <div class="p-3 border-b border-slate-800 bg-slate-950/50">
            <el-input v-model="searchQuery" placeholder="搜索项目..." prefix-icon="Search" size="small" />
          </div>
          <div v-loading="loading" class="flex-1 overflow-y-auto p-2 space-y-2 custom-scrollbar">
            <div v-if="filteredProjects.length === 0" class="text-center text-gray-500 py-8">
              暂无项目
            </div>
            <div 
              v-for="item in filteredProjects" 
              :key="item.id"
              @click="selectProject(item)"
              class="p-4 rounded-lg border cursor-pointer transition-all group hover:border-cyan-500/50"
              :class="activeProject?.id === item.id ? 'border-cyan-500 bg-cyan-900/20' : 'border-slate-800 bg-slate-900 hover:bg-slate-800'"
            >
              <div class="flex justify-between items-start mb-2">
                <span class="font-bold text-gray-200 group-hover:text-white">{{ item.name }}</span>
                <el-tag size="small" :type="item.is_active ? 'success' : 'info'" effect="dark">
                  {{ item.is_active ? '运行中' : (item.task_type || 'detection').toUpperCase() }}
                </el-tag>
              </div>
              <div class="text-xs text-gray-500 flex justify-between">
                <span>模型: {{ item.model_name || '未配置' }}</span>
                <span>{{ formatDate(item.updated_at) }}</span>
              </div>
            </div>
          </div>
        </div>

        <!-- Logic Mode Context (Shows when Logic Tab is active) -->
        <div v-if="activeProject && activeTab === 'logic'" class="h-1/2 bg-slate-900 rounded-lg border border-slate-700 flex flex-col overflow-hidden">
          <div class="p-3 border-b border-slate-800 bg-slate-950/50 font-bold text-white">任务类型与逻辑模式</div>
          <div class="flex-1 overflow-y-auto p-4 custom-scrollbar">
            <!-- Task Type Selector -->
            <div class="mb-4 p-3 bg-slate-800 rounded border border-slate-700">
              <span class="text-sm font-bold text-cyan-400 block mb-2">任务类型</span>
              <div class="flex gap-3">
                <label class="flex items-center gap-2 cursor-pointer px-3 py-1.5 rounded transition-colors" :class="activeProject.task_type === 'detection' ? 'bg-cyan-600/20 border border-cyan-500' : 'bg-slate-700 border border-slate-600 hover:border-cyan-500/50'">
                  <input type="radio" v-model="activeProject.task_type" value="detection" class="accent-cyan-500">
                  <span class="text-sm text-white">目标检测</span>
                </label>
                <label class="flex items-center gap-2 cursor-pointer px-3 py-1.5 rounded transition-colors" :class="activeProject.task_type === 'segmentation' ? 'bg-cyan-600/20 border border-cyan-500' : 'bg-slate-700 border border-slate-600 hover:border-cyan-500/50'">
                  <input type="radio" v-model="activeProject.task_type" value="segmentation" class="accent-cyan-500">
                  <span class="text-sm text-white">图像分割</span>
                </label>
              </div>
              <p class="text-[11px] text-gray-500 mt-1">{{ activeProject.task_type === 'segmentation' ? '使用实例分割模型，提供像素级轮廓' : '使用目标检测模型，提供边界框' }}。跟踪模式下自动适配。</p>
            </div>

            <!-- Logic Mode Options -->
            <span class="text-sm font-bold text-cyan-400 block mb-2">逻辑模式</span>
            <div class="space-y-3">
              <label class="flex items-start p-3 bg-slate-800 rounded border border-slate-700 cursor-pointer hover:border-cyan-500/50 transition-colors">
                <input type="radio" v-model="activeProject.logic_mode" value="sequential" class="mt-1 accent-cyan-500">
                <div class="ml-3 flex-1">
                  <span class="font-bold text-white block">顺序模式</span>
                  <span class="text-xs text-gray-400 block mt-1">必须严格按照设定顺序执行。全部完成→事件1；跳步/乱序→事件2</span>
                </div>
              </label>

              <label class="flex items-start p-3 bg-slate-800 rounded border border-slate-700 cursor-pointer hover:border-cyan-500/50 transition-colors">
                <input type="radio" v-model="activeProject.logic_mode" value="detection" class="mt-1 accent-cyan-500">
                <div class="ml-3 flex-1">
                  <span class="font-bold text-white block">检测模式</span>
                  <span class="text-xs text-gray-400 block mt-1">不强制顺序，只识别目标。集齐所有启用步骤→事件1</span>
                </div>
              </label>

              <label class="flex items-start p-3 bg-slate-800 rounded border border-slate-700 cursor-pointer hover:border-cyan-500/50 transition-colors">
                <input type="radio" v-model="activeProject.logic_mode" value="custom" class="mt-1 accent-cyan-500">
                <div class="ml-3 flex-1">
                  <span class="font-bold text-white block">自定义模式</span>
                  <span class="text-xs text-gray-400 block mt-1">基于顺序/检测模式，可添加自定义条件触发特定事件</span>
                </div>
              </label>

              <label class="flex items-start p-3 bg-slate-800 rounded border border-slate-700 cursor-pointer hover:border-cyan-500/50 transition-colors">
                <input type="radio" v-model="activeProject.logic_mode" value="tracking" class="mt-1 accent-cyan-500">
                <div class="ml-3 flex-1">
                  <span class="font-bold text-white block">跟踪模式</span>
                  <span class="text-xs text-gray-400 block mt-1">{{ activeProject.task_type === 'segmentation' ? '分割+跟踪' : '检测+跟踪' }}：为每个物品分配ID(A1,A2,B1...)，支持装箱清点与数量校验</span>
                </div>
              </label>
            </div>
          </div>
        </div>

        <!-- Counters Context (Shows when Events Tab is active) -->
        <div v-if="activeProject && activeTab === 'events'" class="h-1/2 bg-slate-900 rounded-lg border border-slate-700 flex flex-col overflow-hidden">
          <div class="p-3 border-b border-slate-800 bg-slate-950/50 flex justify-between items-center">
            <span class="font-bold text-white">计数器定义</span>
            <el-button type="primary" size="small" link @click="addCounter">+ 新增计数器</el-button>
          </div>
          <div class="flex-1 overflow-y-auto p-4 custom-scrollbar">
            <div class="space-y-2">
              <!-- 系统默认计数器 -->
              <div class="text-xs text-gray-500 mb-1">系统默认：</div>
              <div v-for="(counter, idx) in defaultCounters" :key="'default-'+idx" class="flex items-center gap-2 bg-slate-800/50 p-2 rounded border border-slate-700">
                <span class="flex-1 text-gray-300 text-sm">{{ counter.name }}</span>
                <el-input-number v-model="counter.value" size="small" :min="0" class="w-24" controls-position="right" />
                <span class="text-[10px] text-gray-500 bg-slate-700 px-1 rounded">默认</span>
              </div>
              <!-- 用户自定义计数器 -->
              <div v-if="customCounters.length > 0" class="text-xs text-gray-500 mt-3 mb-1">自定义：</div>
              <div v-for="(counter, idx) in customCounters" :key="'custom-'+idx" class="flex items-center gap-2 bg-slate-800 p-2 rounded">
                <el-input v-model="counter.name" size="small" placeholder="计数器名称" class="flex-1" />
                <el-input-number v-model="counter.value" size="small" :min="0" class="w-24" controls-position="right" />
                <el-button type="danger" size="small" link @click="removeCounter(idx + 3)">×</el-button>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Config Area -->
      <div v-if="activeProject" class="flex-1 bg-slate-900 rounded-lg border border-slate-700 flex flex-col overflow-hidden">
        <!-- Header -->
        <div class="p-4 border-b border-slate-700 flex justify-between items-center bg-slate-950/30 shrink-0">
          <h2 class="text-lg font-bold text-cyan-400 flex items-center gap-2">
            <el-icon><EditPen /></el-icon> {{ activeProject.name }} (配置)
          </h2>
          <div class="flex gap-2">
            <el-button type="danger" size="small" plain @click="handleDeleteProject">删除</el-button>
            <el-button type="primary" size="small" @click="handleSaveProject" :loading="saving">保存配置</el-button>
          </div>
        </div>

        <!-- Tabs -->
        <el-tabs v-model="activeTab" class="flex-1 flex flex-col project-tabs">
          
          <!-- Tab 1: Basic Settings -->
          <el-tab-pane label="基础设置" name="basic">
            <div class="h-full overflow-y-auto p-4 custom-scrollbar">
              <div class="max-w-3xl space-y-6">
                <!-- Basic Info -->
                <el-card shadow="never" class="bg-slate-800 border-slate-700 text-gray-300">
                  <template #header><span class="font-bold text-white">基本信息</span></template>
                  <el-form label-position="top">
                    <el-form-item label="项目名称">
                      <el-input v-model="activeProject.name" />
                    </el-form-item>
                    <el-form-item label="任务类型">
                      <el-select v-model="activeProject.task_type" class="w-full">
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
                      <el-button type="primary" size="small" plain @click="showModelSelect = true">选择模型</el-button>
                    </div>
                  </template>
                  <div class="flex items-center gap-4">
                    <div class="w-16 h-16 bg-slate-700 rounded flex items-center justify-center">
                      <el-icon :size="24"><Cpu /></el-icon>
                    </div>
                    <div>
                      <p class="text-white font-bold">{{ activeProject.model_name || '未配置模型' }}<span v-if="activeProject.model_version" class="text-gray-400 font-normal ml-2">v{{ activeProject.model_version }}</span></p>
                      <p class="text-xs text-gray-500">Labels: {{ (activeProject.steps_config || []).length }} 个类别已识别</p>
                      <div class="mt-2 flex gap-2 flex-wrap">
                        <el-tag v-for="tag in (activeProject.steps_config || []).slice(0, 5)" :key="tag.label" size="small" type="info">{{ tag.displayLabel || tag.label }}</el-tag>
                        <span v-if="(activeProject.steps_config || []).length > 5" class="text-xs text-gray-500">+{{ activeProject.steps_config.length - 5 }}</span>
                      </div>
                    </div>
                  </div>
                </el-card>
              </div>
            </div>
          </el-tab-pane>

          <!-- Tab 2: Step Settings -->
          <el-tab-pane :label="activeProject.logic_mode === 'tracking' ? '物品设置' : '步骤设置'" name="steps">
            <div class="h-full flex flex-col p-4">
              <div class="mb-3 text-sm text-gray-400 flex items-center flex-shrink-0">
                <el-icon class="mr-1"><InfoFilled /></el-icon>
                <template v-if="activeProject.logic_mode === 'tracking'">
                  配置各物品的启用状态与置信度。跟踪参数请在"逻辑设置"中配置。
                </template>
                <template v-else>
                  配置各步骤的启用状态、置信度、显示标签。启用的步骤将参与逻辑判断。
                </template>
              </div>

              <div class="flex-1 overflow-y-auto custom-scrollbar min-h-0 pb-20">
                <table class="w-full text-left text-xs text-gray-300 border-collapse">
                  <thead class="bg-slate-800 text-gray-400 sticky top-0 z-10">
                    <tr class="border-b border-slate-700">
                      <th class="p-2">原始标签</th>
                      <th class="p-2 w-16">启用</th>
                      <th class="p-2 w-28">置信度阈值</th>
                      <th class="p-2 w-28">显示名称</th>
                      <template v-if="activeProject.logic_mode === 'tracking'">
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
                      </template>
                      <template v-if="activeProject.logic_mode !== 'tracking'">
                      <th class="p-2 w-28">
                        <el-tooltip content="检测持续时间低于此值将被忽略（0.01-60秒，留空不限制）" placement="top">
                          <span class="cursor-help border-b border-dashed border-gray-500">最短持续(秒)</span>
                        </el-tooltip>
                      </th>
                      <th class="p-2 w-28">
                        <el-tooltip content="检测持续时间超过此值将被忽略（0.01-60秒，留空不限制）" placement="top">
                          <span class="cursor-help border-b border-dashed border-gray-500">最大持续(秒)</span>
                        </el-tooltip>
                      </th>
                      <th class="p-2 w-28">
                        <el-tooltip content="同一步骤重复出现时，在此时间内认为是同一次检测，不重复计数（0.01-60秒，留空使用默认1秒）" placement="top">
                          <span class="cursor-help border-b border-dashed border-gray-500">去重间隔(秒)</span>
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
                      <th class="p-2 w-20">
                        <el-tooltip content="开启后，该步骤仅在其之前的所有步骤都已完成时才被接受，可过滤环境误检" placement="top">
                          <span class="cursor-help border-b border-dashed border-gray-500">严格顺序</span>
                        </el-tooltip>
                      </th>
                      <th class="p-2 w-20">
                        <el-tooltip content="开启后，该步骤在一个周期内只接受一次，后续重复出现会被忽略" placement="top">
                          <span class="cursor-help border-b border-dashed border-gray-500">单次接受</span>
                        </el-tooltip>
                      </th>
                      </template>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="step in (activeProject.steps_config || [])" :key="step.id" class="border-b border-slate-700 hover:bg-slate-700/30">
                      <td class="p-2 font-mono text-cyan-400">{{ step.label }}</td>
                      <td class="p-2"><el-switch v-model="step.enabled" size="small" @change="(val) => onStepEnabledChange(step, val)" /></td>
                      <td class="p-2">
                        <div class="flex items-center gap-1">
                          <el-slider v-model="step.threshold" :min="10" :max="100" size="small" class="flex-1" />
                          <span class="text-xs w-8">{{ step.threshold }}%</span>
                        </div>
                      </td>
                      <td class="p-2">
                        <el-input v-model="step.displayLabel" size="small" placeholder="显示名称" />
                      </td>
                      <template v-if="activeProject.logic_mode === 'tracking'">
                      <td class="p-2">
                        <el-input-number
                          v-model="step.tracking_max_lost_seconds"
                          size="small"
                          :min="0.1"
                          :max="30"
                          :step="0.5"
                          :precision="1"
                          :controls="false"
                          placeholder="5.0"
                          class="w-full"
                        />
                      </td>
                      <td class="p-2 text-center">
                        <el-switch v-model="step.tracking_position_lock" size="small" />
                      </td>
                      </template>
                      <template v-if="activeProject.logic_mode !== 'tracking'">
                      <td class="p-2">
                        <el-input-number 
                          v-model="step.min_duration" 
                          size="small" 
                          :min="0" 
                          :max="3600" 
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
                          :min="0.01" 
                          :max="3600" 
                          :step="0.1"
                          :precision="2"
                          :controls="false"
                          placeholder="不限"
                          class="w-full"
                        />
                      </td>
                      <td class="p-2">
                        <el-input-number 
                          v-model="step.max_interval" 
                          size="small" 
                          :min="0.01" 
                          :max="3600" 
                          :step="0.1"
                          :precision="2"
                          :controls="false"
                          placeholder="默认1秒"
                          class="w-full"
                        />
                      </td>
                      <td class="p-2">
                        <el-input-number 
                          v-model="step.min_frames" 
                          size="small" 
                          :min="1" 
                          :max="300" 
                          :step="1"
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
                          :min="1" 
                          :max="300" 
                          :step="1"
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
                            v-for="s in (activeProject.steps_config || []).filter(s => s.enabled && s.id !== step.id && !s.backup_for)" 
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
                          :min="0.1" 
                          :max="3600" 
                          :step="0.1"
                          :precision="1"
                          :controls="false"
                          placeholder="--"
                          class="w-full"
                        />
                      </td>
                      <td class="p-2">
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
                <div v-if="!activeProject.steps_config || activeProject.steps_config.length === 0" class="text-center text-gray-500 py-8">
                  暂无步骤配置，请先在"基础设置"中选择模型
                </div>
              </div>
            </div>
          </el-tab-pane>

          <!-- Tab 3: Logic Settings -->
          <el-tab-pane label="逻辑设置" name="logic">
            <div class="h-full overflow-y-auto p-4 pb-32 custom-scrollbar space-y-6">
              
              <!-- Settlement Mode (sequential / custom-sequential) -->
              <el-card v-if="activeProject.logic_mode === 'sequential' || (activeProject.logic_mode === 'custom' && activeProject.custom_based_on === 'sequential')" shadow="never" class="bg-slate-800 border-slate-700">
                <template #header><span class="font-bold text-white">结算方式</span></template>
                <div class="space-y-4 text-sm text-gray-300">
                  <el-radio-group v-model="activeProject.settlement_mode">
                    <el-radio value="first_step">
                      <span class="text-gray-300">第一步结算</span>
                      <span class="text-xs text-gray-500 ml-1">— 新周期的第一步出现时结算上一周期</span>
                    </el-radio>
                    <el-radio value="last_step">
                      <span class="text-gray-300">最后一步结算</span>
                      <span class="text-xs text-gray-500 ml-1">— 最后一步消失后结算当前周期</span>
                    </el-radio>
                  </el-radio-group>
                  <div class="flex items-center gap-3 pt-2 border-t border-slate-700">
                    <span class="text-gray-400 text-xs whitespace-nowrap">空闲超时(秒)</span>
                    <el-input-number v-model="activeProject.idle_timeout_seconds" size="small" :min="0" :max="600" :step="5" :precision="0" />
                    <span class="text-xs text-gray-500">超过此时间无新步骤加入，强制结算当前周期（0=不启用）</span>
                  </div>
                </div>
              </el-card>

              <!-- Sequential Mode Config -->
              <el-card v-if="activeProject.logic_mode === 'sequential'" shadow="never" class="bg-slate-800 border-slate-700">
                <template #header><span class="font-bold text-white">顺序模式 - 步骤排序</span></template>
                <div class="space-y-4 text-sm text-gray-300">
                  <p class="text-xs text-gray-400">拖拽或使用按钮调整步骤执行顺序。按此顺序完成 → 事件1(合格)；跳步/乱序 → 事件2(NG)</p>
                  <div class="bg-slate-900 rounded p-3 space-y-2">
                    <div v-for="(seqStep, idx) in activeProject.sequence_order" :key="idx" class="flex items-center gap-2 bg-slate-800 p-2 rounded">
                      <span class="text-cyan-400 font-bold w-8">{{ idx + 1 }}.</span>
                      <el-select v-model="seqStep.step_id" size="small" class="flex-1" placeholder="选择步骤">
                        <el-option 
                          v-for="s in nonBackupSteps" 
                          :key="s.id" 
                          :label="s.displayLabel || s.label" 
                          :value="s.id" 
                        />
                      </el-select>
                      <el-button size="small" :disabled="idx === 0" @click="moveStepUp(idx)">↑</el-button>
                      <el-button size="small" :disabled="idx === activeProject.sequence_order.length - 1" @click="moveStepDown(idx)">↓</el-button>
                      <el-button type="danger" size="small" link @click="removeSequenceStep(idx)">删除</el-button>
                    </div>
                    <el-button type="primary" size="small" @click="addSequenceStep">+ 添加步骤</el-button>
                  </div>
                </div>
              </el-card>

              <!-- Detection Mode Config -->
              <el-card v-if="activeProject.logic_mode === 'detection'" shadow="never" class="bg-slate-800 border-slate-700">
                <template #header><span class="font-bold text-white">检测模式 - 需检测的步骤</span></template>
                <div class="space-y-4 text-sm text-gray-300">
                  <p class="text-xs text-gray-400">选择需要检测的步骤（无需顺序）。全部检测到 → 事件1(合格)</p>
                  <div class="bg-slate-900 rounded p-3">
                    <el-checkbox-group v-model="activeProject.detection_steps" class="flex flex-col gap-2">
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
              <el-card v-if="activeProject.logic_mode === 'custom'" shadow="never" class="bg-slate-800 border-slate-700">
                <template #header><span class="font-bold text-white">自定义模式 - 条件配置</span></template>
                <div class="space-y-4 text-sm text-gray-300">
                  <el-form label-position="top">
                    <el-form-item label="基于模式（可选）">
                      <el-select v-model="activeProject.custom_based_on" class="w-full" clearable placeholder="不选择则仅使用自定义条件">
                        <el-option label="基于顺序模式" value="sequential" />
                        <el-option label="基于检测模式" value="detection" />
                      </el-select>
                      <p class="text-xs text-gray-500 mt-1">选择后将继承该模式的逻辑，自定义条件优先级更高</p>
                    </el-form-item>
                  </el-form>

                  <!-- 自定义模式独立的基础模式配置 -->
                  <div v-if="activeProject.custom_based_on === 'sequential'" class="border border-slate-600 rounded p-3 bg-slate-900/50">
                    <p class="text-xs text-cyan-400 mb-2 font-bold">顺序配置（自定义模式独立）：</p>
                    <div class="space-y-2">
                      <div v-for="(item, idx) in (activeProject.custom_sequence_order || [])" :key="idx" class="flex items-center gap-2">
                        <span class="text-gray-400 text-xs w-6">{{ idx + 1 }}.</span>
                        <el-select v-model="item.step_id" size="small" class="flex-1" placeholder="选择步骤">
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
                        <el-switch v-model="activeProject.accumulate_repeats" />
                      </div>
                    </div>
                  </div>

                  <div v-if="activeProject.custom_based_on === 'detection'" class="border border-slate-600 rounded p-3 bg-slate-900/50">
                    <p class="text-xs text-cyan-400 mb-2 font-bold">检测配置（自定义模式独立，勾选要检测的步骤）：</p>
                    <el-checkbox-group v-model="activeProject.custom_detection_steps" class="flex flex-wrap gap-2">
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
                      <div v-for="(cond, idx) in (activeProject.custom_conditions || [])" :key="cond.id || idx" class="bg-slate-900 p-3 rounded border border-slate-700">
                        <div class="flex justify-between items-start mb-3">
                          <div class="flex items-center gap-3">
                            <span class="text-cyan-400 font-bold">条件 {{ idx + 1 }}</span>
                            <div class="flex items-center gap-1">
                              <span class="text-xs text-gray-500">优先级:</span>
                              <el-input-number v-model="cond.priority" size="small" :min="1" :max="99" class="w-20" controls-position="right" />
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
                              <el-option v-for="ev in (activeProject.events_config || [])" :key="ev.id" :label="ev.name" :value="ev.id" />
                            </el-select>
                          </div>
                        </div>
                      </div>
                      <div v-if="!activeProject.custom_conditions || activeProject.custom_conditions.length === 0" class="text-gray-500 text-center py-4 border border-dashed border-slate-700 rounded">
                        暂无自定义条件，点击上方按钮添加
                      </div>
                    </div>
                  </div>
                </div>
              </el-card>

              <!-- Tracking Mode Config -->
              <el-card v-if="activeProject.logic_mode === 'tracking'" shadow="never" class="bg-slate-800 border-slate-700">
                <template #header><span class="font-bold text-white">跟踪模式 - 物品清点配置</span></template>
                <div class="space-y-4 text-sm text-gray-300">
                  <p class="text-xs text-gray-400">
                    使用{{ activeProject.task_type === 'segmentation' ? '分割+跟踪' : '检测+跟踪' }}为每个物品分配唯一ID，配合周期结束策略进行数量校验。
                  </p>

                  <div class="space-y-4 text-sm text-gray-300">

                    <!-- Row 1: 周期结束策略 -->
                    <div>
                      <div class="text-xs text-gray-400 mb-1.5">周期结束策略</div>
                      <div class="grid grid-cols-3 gap-2">
                        <label class="flex items-start gap-2 p-2.5 bg-slate-800 rounded border cursor-pointer transition-colors"
                          :class="activeProject.tracking_cycle_strategy === 'all_gone' ? 'border-cyan-500 bg-cyan-500/10' : 'border-slate-700 hover:border-slate-500'"
                          @click="activeProject.tracking_cycle_strategy = 'all_gone'">
                          <input type="radio" v-model="activeProject.tracking_cycle_strategy" value="all_gone" class="mt-0.5 accent-cyan-500">
                          <div>
                            <div class="text-white text-xs font-bold">全部消失</div>
                            <div class="text-[10px] text-gray-500 mt-0.5">所有物品离开画面后结算</div>
                          </div>
                        </label>
                        <label class="flex items-start gap-2 p-2.5 bg-slate-800 rounded border cursor-pointer transition-colors"
                          :class="activeProject.tracking_cycle_strategy === 'roi_exit' ? 'border-cyan-500 bg-cyan-500/10' : 'border-slate-700 hover:border-slate-500'"
                          @click="activeProject.tracking_cycle_strategy = 'roi_exit'">
                          <input type="radio" v-model="activeProject.tracking_cycle_strategy" value="roi_exit" class="mt-0.5 accent-cyan-500">
                          <div>
                            <div class="text-white text-xs font-bold">ROI离开</div>
                            <div class="text-[10px] text-gray-500 mt-0.5">物品离开指定检测区域后结算</div>
                          </div>
                        </label>
                        <label class="flex items-start gap-2 p-2.5 bg-slate-800 rounded border cursor-pointer transition-colors"
                          :class="activeProject.tracking_cycle_strategy === 'trigger' ? 'border-cyan-500 bg-cyan-500/10' : 'border-slate-700 hover:border-slate-500'"
                          @click="activeProject.tracking_cycle_strategy = 'trigger'">
                          <input type="radio" v-model="activeProject.tracking_cycle_strategy" value="trigger" class="mt-0.5 accent-cyan-500">
                          <div>
                            <div class="text-white text-xs font-bold">触发标签</div>
                            <div class="text-[10px] text-gray-500 mt-0.5">检测到指定标签后结算</div>
                          </div>
                        </label>
                      </div>
                    </div>

                    <!-- Row 2: trigger extras OR 消失确认帧数 -->
                    <div v-if="activeProject.tracking_cycle_strategy === 'trigger'" class="flex items-center gap-4 text-xs">
                      <span class="text-gray-400 shrink-0">触发标签</span>
                      <el-select v-model="activeProject.tracking_trigger_label" size="small" class="!w-40" placeholder="选择标签">
                        <el-option v-for="s in nonBackupSteps" :key="s.id" :label="s.displayLabel || s.label" :value="s.label" />
                      </el-select>
                      <span class="text-gray-400 shrink-0">确认帧数</span>
                      <el-input-number v-model="activeProject.tracking_trigger_min_frames" :min="1" :max="300" :step="5" size="small" class="!w-28" />
                    </div>
                    <div v-else class="flex items-center gap-4 text-xs">
                      <span class="text-gray-400 shrink-0">消失确认帧数</span>
                      <el-input-number v-model="activeProject.tracking_gone_confirm_frames" :min="1" :max="300" :step="5" size="small" class="!w-28" />
                    </div>

                    <!-- Row 3: switches in one line -->
                    <div class="flex items-center gap-5 text-xs flex-wrap">
                      <div class="flex items-center gap-1.5">
                        <span class="text-gray-400">ID交换检测</span>
                        <el-switch v-model="activeProject.tracking_swap_detection" size="small" />
                      </div>
                      <div class="flex items-center gap-1.5">
                        <span class="text-gray-400">外观特征辅助</span>
                        <el-switch v-model="activeProject.tracking_appearance_match" size="small" />
                      </div>
                      <div class="flex items-center gap-1.5">
                        <span class="text-gray-400">ID锁定</span>
                        <el-switch v-model="activeProject.tracking_id_lock" size="small" />
                      </div>
                      <div v-if="activeProject.tracking_id_lock" class="flex items-center gap-1.5">
                        <span class="text-gray-400">锁定帧数</span>
                        <el-input-number v-model="activeProject.tracking_id_lock_frames" :min="3" :max="120" :step="5" size="small" class="!w-24" />
                      </div>
                      <div class="flex items-center gap-1.5">
                        <span class="text-gray-400">顺序检查</span>
                        <el-switch v-model="activeProject.tracking_check_order" size="small" />
                      </div>
                    </div>

                    <!-- Row 4: Expected items + ROI side by side -->
                    <div class="grid grid-cols-2 gap-4">
                      <div>
                        <div class="text-xs text-gray-400 mb-1.5">期望物品清单</div>
                        <div class="bg-slate-900 rounded p-2.5 space-y-1.5">
                          <div v-for="(item, idx) in activeProject.counting_expected_list" :key="idx" class="flex items-center gap-1.5 bg-slate-800 p-1.5 rounded">
                            <el-select v-model="item.label" size="small" class="flex-1 min-w-0" placeholder="选择物品">
                              <el-option v-for="s in countableSteps" :key="s.id" :label="s.displayLabel || s.label" :value="s.label" />
                            </el-select>
                            <span class="text-gray-500 text-xs shrink-0">×</span>
                            <el-input-number v-model="item.count" size="small" :min="1" :max="99" :step="1" class="!w-20 shrink-0" />
                            <el-button type="danger" size="small" link class="shrink-0" @click="activeProject.counting_expected_list.splice(idx, 1)">
                              <el-icon><Delete /></el-icon>
                            </el-button>
                          </div>
                          <el-button type="primary" size="small" text @click="activeProject.counting_expected_list.push({label: '', count: 1})">+ 添加物品</el-button>
                        </div>
                      </div>
                      <div>
                        <div class="text-xs text-gray-400 mb-1.5">检测区域 (ROI)</div>
                        <div class="bg-slate-900 rounded p-2.5 space-y-2">
                          <div class="flex items-center gap-2">
                            <el-button type="primary" size="small" @click="openRoiEditor">
                              {{ activeProject.tracking_roi_polygon?.length > 2 ? '重新绘制' : '设置区域' }}
                            </el-button>
                            <el-button v-if="activeProject.tracking_roi_polygon?.length > 2" type="danger" size="small" plain @click="activeProject.tracking_roi_polygon = []">清除</el-button>
                            <span v-if="activeProject.tracking_roi_polygon?.length > 2" class="text-xs text-green-400">
                              已设置 {{ activeProject.tracking_roi_polygon.length }} 个顶点
                            </span>
                            <span v-else class="text-xs text-gray-500">未设置（全画面）</span>
                          </div>
                          <div v-if="activeProject.tracking_roi_polygon?.length > 2" class="relative w-full h-28 bg-slate-800 rounded border border-slate-700 overflow-hidden">
                            <canvas ref="roiPreviewCanvas" class="w-full h-full"></canvas>
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </el-card>

              <!-- NG Cycle Protection -->
              <el-card v-if="activeProject.logic_mode === 'sequential' || activeProject.logic_mode === 'custom'" shadow="never" class="bg-slate-800 border-slate-700">
                <template #header><span class="font-bold text-white">NG 周期保护</span></template>
                <div class="space-y-3 text-sm text-gray-300">
                  <p class="text-xs text-gray-400">当连续两次 NG 之间的间隔小于设定时间时，后续 NG 会被抑制，避免因短暂误检导致重复报错。设为 0 则不启用。</p>
                  <div class="flex items-center gap-3">
                    <span>保护间隔 (秒)</span>
                    <el-input-number v-model="activeProject.ng_cycle_protect_seconds" size="small" :min="0" :max="60" :step="1" :precision="0" />
                  </div>
                </div>
              </el-card>

              <!-- Simultaneous Groups Config (不适用于跟踪模式) -->
              <el-card v-if="activeProject.logic_mode !== 'tracking'" shadow="never" class="bg-slate-800 border-slate-700">
                <template #header>
                  <div class="flex justify-between items-center">
                    <span class="font-bold text-white">同时出现组</span>
                    <el-button type="primary" size="small" link @click="addSimultaneousGroup">+ 新增组</el-button>
                  </div>
                </template>
                <div class="space-y-4 text-sm text-gray-300">
                  <p class="text-xs text-gray-400">当多个步骤可能同时出现在画面中时，配置为一组可防止重复识别。系统会按设定的优先顺序记录到周期中。</p>
                  <div class="space-y-3">
                    <div v-for="(group, gIdx) in (activeProject.simultaneous_groups || [])" :key="gIdx" class="bg-slate-900 p-3 rounded border border-slate-700">
                      <div class="flex justify-between items-center mb-3">
                        <div class="flex items-center gap-3">
                          <el-switch v-model="group.enabled" size="small" />
                          <span class="text-cyan-400 font-bold">组 {{ gIdx + 1 }}</span>
                        </div>
                        <el-button type="danger" size="small" link @click="removeSimultaneousGroup(gIdx)">删除</el-button>
                      </div>
                      <div class="space-y-3">
                        <div>
                          <p class="text-xs text-gray-500 mb-1">时间窗口（秒）：在此时间内先后出现视为"同时"</p>
                          <el-input-number v-model="group.time_window" size="small" :min="0.5" :max="10" :step="0.5" :precision="1" />
                        </div>
                        <div>
                          <p class="text-xs text-gray-500 mb-2">选择可能同时出现的步骤，并按优先顺序排列（拖拽或使用箭头调整）：</p>
                          <div class="space-y-2">
                            <div v-for="(label, sIdx) in (group.priority_order || [])" :key="sIdx" class="flex items-center gap-2">
                              <span class="text-gray-400 text-xs w-6">{{ sIdx + 1 }}.</span>
                              <el-select v-model="group.priority_order[sIdx]" size="small" class="flex-1" placeholder="选择步骤">
                                <el-option v-for="step in enabledSteps" :key="step.id" :label="step.displayLabel || step.label" :value="step.label" />
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
                    <div v-if="!activeProject.simultaneous_groups || activeProject.simultaneous_groups.length === 0" class="text-gray-500 text-center py-4 border border-dashed border-slate-700 rounded">
                      暂无同时出现组，点击上方按钮添加
                    </div>
                  </div>
                </div>
              </el-card>
            </div>
          </el-tab-pane>

          <!-- Tab 4: Events Settings -->
          <el-tab-pane label="事件设置" name="events">
            <div class="h-full overflow-y-auto p-4 pb-32 custom-scrollbar space-y-6">
              <el-card shadow="never" class="bg-slate-800 border-slate-700">
                <template #header>
                  <div class="flex justify-between items-center">
                    <span class="font-bold text-white">事件定义</span>
                    <el-button type="primary" size="small" link @click="addEvent">+ 新增事件</el-button>
                  </div>
                </template>
                <div class="space-y-4">
                  <div v-for="(ev, idx) in (activeProject.events_config || [])" :key="ev.id" class="bg-slate-900 p-4 rounded">
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
                          <el-input-number v-model="action.delta" size="small" :min="-100" :max="100" class="w-20" controls-position="right" />
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
                  </div>
                </div>
              </el-card>
            </div>
          </el-tab-pane>
        </el-tabs>
      </div>
      
      <!-- Empty State -->
      <div v-else class="flex-1 bg-slate-900 rounded-lg border border-slate-700 flex flex-col items-center justify-center text-gray-500">
        <el-icon :size="64" class="mb-4"><FolderAdd /></el-icon>
        <p>请选择左侧项目或新建一个项目</p>
      </div>

    </div>

    <!-- Create Dialog -->
    <el-dialog v-model="createDialogVisible" title="新建项目" width="500px" destroy-on-close>
      <el-form label-position="top">
        <el-form-item label="项目名称" required>
          <el-input v-model="newProjectForm.name" placeholder="例如: 手机壳外观检测" />
        </el-form-item>
        <el-form-item label="任务类型">
          <el-select v-model="newProjectForm.task_type" class="w-full">
            <el-option label="目标检测 (Object Detection)" value="detection" />
            <el-option label="语义分割" value="segmentation" disabled />
          </el-select>
        </el-form-item>
        <el-form-item label="逻辑模式">
          <el-select v-model="newProjectForm.logic_mode" class="w-full">
            <el-option label="顺序模式" value="sequential" />
            <el-option label="检测模式" value="detection" />
            <el-option label="自定义模式" value="custom" />
            <el-option label="跟踪模式" value="tracking" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="handleCreateProject" :loading="creating" :disabled="!newProjectForm.name">创建</el-button>
      </template>
    </el-dialog>

    <!-- Model Select Dialog -->
    <el-dialog v-model="showModelSelect" title="选择模型" width="600px">
      <div v-loading="loadingModels" class="space-y-2 max-h-96 overflow-y-auto">
        <div v-for="model in modelList" :key="model.id" 
          @click="selectModel(model)"
          class="p-3 bg-slate-800 rounded cursor-pointer hover:bg-slate-700 flex justify-between items-center">
          <div>
            <p class="font-bold">{{ model.name }}<span v-if="model.version" class="text-gray-400 font-normal ml-2">v{{ model.version }}</span></p>
            <p class="text-xs text-gray-400">{{ model.framework }} - {{ (model.file_size / 1024 / 1024).toFixed(2) }} MB - {{ getLabelsCount(model.labels) }} 个类别</p>
          </div>
          <el-tag v-if="activeProject?.default_model_id === model.id" type="success">当前</el-tag>
        </div>
        <div v-if="modelList.length === 0" class="text-center text-gray-500 py-8">
          暂无可用模型，请先上传模型
        </div>
      </div>
    </el-dialog>

    <!-- ROI Polygon Editor Dialog -->
    <el-dialog v-model="roiEditorVisible" title="绘制 ROI 检测区域" width="80%" :close-on-click-modal="false" destroy-on-close class="roi-editor-dialog">
      <div class="space-y-3">
        <div class="flex items-center gap-3 text-sm">
          <span class="text-gray-400">单击添加顶点，点击<b class="text-amber-400">第一个点</b>闭合多边形（靠近时会变绿）。闭合后再次单击可重新绘制</span>
          <div class="flex-1"></div>
          <el-button size="small" @click="roiUndoPoint" :disabled="roiPoints.length === 0">撤销上一点</el-button>
          <el-button size="small" type="warning" @click="roiClearPoints" :disabled="roiPoints.length === 0">清除全部</el-button>
          <el-button size="small" type="success" @click="roiFinishPolygon" :disabled="roiPoints.length < 3">完成绘制</el-button>
        </div>
        <div class="relative bg-black rounded overflow-hidden flex justify-center" style="max-height: 70vh;">
          <canvas ref="roiEditorCanvas" class="cursor-crosshair" style="max-width: 100%; max-height: 70vh; object-fit: contain;"
            @click="roiCanvasClick" @dblclick="roiCanvasDblClick" @mousemove="roiCanvasMouseMove"></canvas>
        </div>
        <div class="flex items-center gap-2 text-xs text-gray-500">
          <span>顶点数: {{ roiPoints.length }}</span>
          <span v-if="roiPolygonClosed" class="text-green-400 font-bold">多边形已闭合</span>
        </div>
      </div>
      <template #footer>
        <el-button @click="roiEditorVisible = false">取消</el-button>
        <el-button type="primary" @click="roiSave" :disabled="roiPoints.length < 3">保存 ROI</el-button>
      </template>
    </el-dialog>

  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch, nextTick } from 'vue';
import { Plus, Search, EditPen, FolderAdd, Upload, InfoFilled, Check, Cpu, Delete } from '@element-plus/icons-vue';
import { useProjectStore } from '@/store/useProjectStore';
import { useSystemStore } from '@/store/useSystemStore';
import { ElMessage, ElMessageBox } from 'element-plus';
import { getProjects, getProjectDetail, createProject, updateProject, deleteProject, activateProject } from '@/api/project';
import { getModels } from '@/api/model';
import { getBackendHost } from '@/api/index';

const projectStore = useProjectStore();
const systemStore = useSystemStore();
const searchQuery = ref('');
const activeProject = ref(null);
const activeTab = ref('basic');
const createDialogVisible = ref(false);
const showModelSelect = ref(false);

const projects = ref([]);
const modelList = ref([]);
const loading = ref(false);
const saving = ref(false);
const creating = ref(false);
const loadingModels = ref(false);

const newProjectForm = ref({ 
  name: '', 
  task_type: 'detection', 
  logic_mode: 'sequential'
});

// 计算启用的步骤
const enabledSteps = computed(() => {
  if (!activeProject.value?.steps_config) return [];
  return activeProject.value.steps_config.filter(s => s.enabled);
});

const nonBackupSteps = computed(() => {
  return enabledSteps.value.filter(s => !s.backup_for);
});

const countableSteps = computed(() => {
  const trigger = activeProject.value?.tracking_trigger_label || '';
  return nonBackupSteps.value.filter(s => s.label !== trigger);
});

// 默认计数器（前3个）
const defaultCounters = computed(() => {
  if (!activeProject.value?.counters_config) return [];
  return activeProject.value.counters_config.slice(0, 3);
});

// 自定义计数器（第4个开始）
const customCounters = computed(() => {
  if (!activeProject.value?.counters_config) return [];
  return activeProject.value.counters_config.slice(3);
});

// 加载项目列表
const loadProjects = async () => {
  loading.value = true;
  try {
    const res = await getProjects();
    projects.value = res.data.items || [];
  } catch (err) {
    console.error('加载项目失败:', err);
    ElMessage.error('加载项目列表失败');
  } finally {
    loading.value = false;
  }
};

// 加载模型列表
const loadModels = async () => {
  loadingModels.value = true;
  try {
    const res = await getModels();
    modelList.value = res.data.items || [];
  } catch (err) {
    console.error('加载模型失败:', err);
  } finally {
    loadingModels.value = false;
  }
};

onMounted(async () => {
  loadProjects();
  loadModels();
  systemStore.loadSettings();
  
  // 从当前项目加载检测配置（包括自定义提示框）
  if (projectStore.currentProjectId) {
    try {
      const res = await getProjectDetail(projectStore.currentProjectId);
      if (res.data?.detection_config) {
        systemStore.loadDetectionFromProject(res.data.detection_config);
      }
    } catch (e) {
      console.error('加载项目检测配置失败:', e);
    }
  }
});

const filteredProjects = computed(() => {
  return projects.value.filter(p => p.name.toLowerCase().includes(searchQuery.value.toLowerCase()));
});

const formatDate = (dateStr) => {
  if (!dateStr) return '';
  const date = new Date(dateStr);
  return date.toLocaleDateString();
};

const getLabelsCount = (labels) => {
  if (!labels) return 0;
  try {
    const parsed = typeof labels === 'string' ? JSON.parse(labels) : labels;
    return Array.isArray(parsed) ? parsed.length : 0;
  } catch {
    return 0;
  }
};

// ======================== ROI Polygon Editor ========================
const roiEditorVisible = ref(false);
const roiEditorCanvas = ref(null);
const roiPreviewCanvas = ref(null);
const roiPoints = ref([]);
const roiPolygonClosed = ref(false);
let roiImage = null;
let roiMousePos = null;

const openRoiEditor = async () => {
  roiPoints.value = [];
  roiPolygonClosed.value = false;
  roiMousePos = null;
  roiEditorVisible.value = true;
  await nextTick();
  setTimeout(() => loadRoiSnapshot(), 200);
};

const loadRoiSnapshot = () => {
  const canvas = roiEditorCanvas.value;
  if (!canvas) return;
  const img = new Image();
  img.crossOrigin = 'anonymous';
  const host = getBackendHost();
  img.src = `${host}/snapshot?channel=0&t=${Date.now()}`;
  img.onload = () => {
    roiImage = img;
    canvas.width = img.naturalWidth;
    canvas.height = img.naturalHeight;
    roiRedraw();
  };
  img.onerror = () => {
    const ctx = canvas.getContext('2d');
    canvas.width = 640;
    canvas.height = 480;
    ctx.fillStyle = '#1e293b';
    ctx.fillRect(0, 0, 640, 480);
    ctx.fillStyle = '#94a3b8';
    ctx.font = '16px Arial';
    ctx.textAlign = 'center';
    ctx.fillText('无法获取摄像头画面，请确保摄像头已连接', 320, 240);
    roiImage = null;
  };
};

const roiGetCanvasXY = (e) => {
  const canvas = roiEditorCanvas.value;
  if (!canvas) return null;
  const rect = canvas.getBoundingClientRect();
  const scaleX = canvas.width / rect.width;
  const scaleY = canvas.height / rect.height;
  return {
    x: (e.clientX - rect.left) * scaleX,
    y: (e.clientY - rect.top) * scaleY
  };
};

const ROI_CLOSE_RADIUS = 15;

const roiCanvasClick = (e) => {
  if (roiPolygonClosed.value) {
    roiPoints.value = [];
    roiPolygonClosed.value = false;
    roiRedraw();
    return;
  }
  const pt = roiGetCanvasXY(e);
  if (!pt) return;

  if (roiPoints.value.length >= 3) {
    const first = roiPoints.value[0];
    const canvas = roiEditorCanvas.value;
    const rect = canvas.getBoundingClientRect();
    const scale = canvas.width / rect.width;
    const dist = Math.sqrt((pt.x - first.x) ** 2 + (pt.y - first.y) ** 2);
    if (dist < ROI_CLOSE_RADIUS * scale) {
      roiPolygonClosed.value = true;
      roiRedraw();
      return;
    }
  }

  roiPoints.value.push(pt);
  roiRedraw();
};

const roiCanvasDblClick = (e) => {
  e.preventDefault();
  if (roiPoints.value.length >= 3 && !roiPolygonClosed.value) {
    roiPolygonClosed.value = true;
    roiRedraw();
  }
};

const roiCanvasMouseMove = (e) => {
  if (roiPolygonClosed.value) return;
  roiMousePos = roiGetCanvasXY(e);
  roiRedraw();
};

const roiUndoPoint = () => {
  if (roiPolygonClosed.value) {
    roiPolygonClosed.value = false;
  } else {
    roiPoints.value.pop();
  }
  roiRedraw();
};

const roiClearPoints = () => {
  roiPoints.value = [];
  roiPolygonClosed.value = false;
  roiMousePos = null;
  roiRedraw();
};

const roiFinishPolygon = () => {
  if (roiPoints.value.length >= 3) {
    roiPolygonClosed.value = true;
    roiRedraw();
  }
};

const roiRedraw = () => {
  const canvas = roiEditorCanvas.value;
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  if (roiImage) {
    ctx.drawImage(roiImage, 0, 0);
  }

  const pts = roiPoints.value;
  if (pts.length === 0) return;

  if (roiPolygonClosed.value && pts.length >= 3) {
    ctx.fillStyle = 'rgba(0, 200, 255, 0.15)';
    ctx.beginPath();
    ctx.moveTo(pts[0].x, pts[0].y);
    for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i].x, pts[i].y);
    ctx.closePath();
    ctx.fill();
  }

  ctx.strokeStyle = '#00c8ff';
  ctx.lineWidth = 2;
  ctx.setLineDash(roiPolygonClosed.value ? [] : [6, 4]);
  ctx.beginPath();
  ctx.moveTo(pts[0].x, pts[0].y);
  for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i].x, pts[i].y);
  if (roiPolygonClosed.value) ctx.closePath();
  else if (roiMousePos) ctx.lineTo(roiMousePos.x, roiMousePos.y);
  ctx.stroke();
  ctx.setLineDash([]);

  const nearFirst = !roiPolygonClosed.value && pts.length >= 3 && roiMousePos &&
    Math.sqrt((roiMousePos.x - pts[0].x) ** 2 + (roiMousePos.y - pts[0].y) ** 2) < ROI_CLOSE_RADIUS * (canvas.width / (canvas.getBoundingClientRect().width || 1));

  pts.forEach((pt, i) => {
    const isFirst = i === 0;
    const radius = isFirst && nearFirst ? 10 : 5;
    ctx.fillStyle = isFirst ? (nearFirst ? '#22c55e' : '#f59e0b') : '#00c8ff';
    ctx.beginPath();
    ctx.arc(pt.x, pt.y, radius, 0, Math.PI * 2);
    ctx.fill();
    if (isFirst && nearFirst) {
      ctx.strokeStyle = '#22c55e';
      ctx.lineWidth = 2;
      ctx.stroke();
    }
    ctx.fillStyle = 'white';
    ctx.font = 'bold 11px Arial';
    ctx.fillText(isFirst && nearFirst ? '点击闭合' : `${i + 1}`, pt.x + 8, pt.y - 4);
  });
};

const roiSave = async () => {
  if (!activeProject.value || roiPoints.value.length < 3) return;
  const canvas = roiEditorCanvas.value;
  const w = canvas?.width || 1;
  const h = canvas?.height || 1;
  activeProject.value.tracking_roi_polygon = roiPoints.value.map(pt => [
    Math.round((pt.x / w) * 10000) / 10000,
    Math.round((pt.y / h) * 10000) / 10000
  ]);
  roiEditorVisible.value = false;
  nextTick(() => drawRoiPreview());
  await handleSaveProject();
};

const drawRoiPreview = () => {
  const canvas = roiPreviewCanvas.value;
  if (!canvas || !activeProject.value?.tracking_roi_polygon?.length) return;
  const parent = canvas.parentElement;
  if (parent) { canvas.width = parent.offsetWidth; canvas.height = parent.offsetHeight; }
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = '#1e293b';
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  const poly = activeProject.value.tracking_roi_polygon;
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

// Redraw preview when polygon changes or when the card becomes visible
watch(() => activeProject.value?.tracking_roi_polygon, () => {
  nextTick(() => drawRoiPreview());
}, { deep: true });

// ======================== End ROI Editor ========================

// 初始化项目默认配置
const initProjectDefaults = (project) => {
  if (!project.steps_config) project.steps_config = [];
  if (!project.events_config) {
    project.events_config = [
      { id: 1, name: '合格(OK)', color: '#10b981', actions: [{ counter_name: '合格总数', delta: 1 }, { counter_name: '总产量', delta: 1 }], show_notification: true, toast_id: 'ok' },
      { id: 2, name: '不良(NG)', color: '#ef4444', actions: [{ counter_name: '不良总数', delta: 1 }, { counter_name: '总产量', delta: 1 }], show_notification: true, toast_id: 'ng' }
    ];
  }
  // 确保每个事件都有 toast_id
  project.events_config.forEach((ev, idx) => {
    if (!ev.toast_id) {
      ev.toast_id = idx === 0 ? 'ok' : (idx === 1 ? 'ng' : 'ok');
    }
  });
  if (!project.counters_config) {
    project.counters_config = [
      { name: '合格总数', value: 0 },
      { name: '不良总数', value: 0 },
      { name: '总产量', value: 0 }
    ];
  }
  
  // 从 pipeline_config 中提取配置（后端保存在这里）
  const pipelineConfig = project.pipeline_config || {};
  
  // 确保 pipeline_config 存在
  if (!project.pipeline_config) {
    project.pipeline_config = {};
  }
  
  // 使用 pipeline_config 中的值，如果没有则使用默认值
  if (project.sequence_order === undefined) {
    project.sequence_order = pipelineConfig.sequence_order || [];
  }
  if (project.detection_steps === undefined) {
    project.detection_steps = pipelineConfig.detection_steps || [];
  }
  if (project.custom_conditions === undefined) {
    project.custom_conditions = pipelineConfig.custom_conditions || [];
  }
  // 确保每个条件都有正确的结构
  project.custom_conditions = (project.custom_conditions || []).map((cond, idx) => ({
    id: cond.id || idx + 1,
    priority: cond.priority || idx + 1,
    sequence: Array.isArray(cond.sequence) ? cond.sequence : [],
    event_id: cond.event_id || null
  }));
  
  if (project.custom_based_on === undefined) {
    project.custom_based_on = pipelineConfig.custom_based_on || null;  // 默认不选择
  }
  
  // 自定义模式独立的配置（与顺序模式/检测模式分开）
  if (project.custom_sequence_order === undefined) {
    project.custom_sequence_order = pipelineConfig.custom_sequence_order || [];
  }
  if (project.custom_detection_steps === undefined) {
    project.custom_detection_steps = pipelineConfig.custom_detection_steps || [];
  }
  // 累积重复序列选项
  if (project.accumulate_repeats === undefined) {
    project.accumulate_repeats = pipelineConfig.accumulate_repeats || false;
  }
  // NG 周期保护
  if (project.ng_cycle_protect_seconds === undefined) {
    project.ng_cycle_protect_seconds = pipelineConfig.ng_cycle_protect_seconds || 0;
  }
  // 同时出现组
  if (project.simultaneous_groups === undefined) {
    project.simultaneous_groups = pipelineConfig.simultaneous_groups || [];
  }
  // Settlement mode
  if (project.settlement_mode === undefined) {
    project.settlement_mode = pipelineConfig.settlement_mode || 'first_step';
  }
  if (project.idle_timeout_seconds === undefined) {
    project.idle_timeout_seconds = pipelineConfig.idle_timeout_seconds || 0;
  }
  // Tracking mode
  if (project.tracking_cycle_strategy === undefined) {
    project.tracking_cycle_strategy = pipelineConfig.tracking_cycle_strategy || 'all_gone';
  }
  if (project.tracking_trigger_label === undefined) {
    project.tracking_trigger_label = pipelineConfig.tracking_trigger_label || '';
  }
  if (project.tracking_trigger_min_frames === undefined) {
    project.tracking_trigger_min_frames = pipelineConfig.tracking_trigger_min_frames || 15;
  }
  if (project.tracking_gone_confirm_frames === undefined) {
    project.tracking_gone_confirm_frames = pipelineConfig.tracking_gone_confirm_frames || 30;
  }
  if (project.tracking_max_lost_seconds === undefined) {
    project.tracking_max_lost_seconds = 5.0;
  }
  if (project.tracking_check_order === undefined) {
    project.tracking_check_order = pipelineConfig.tracking_check_order || false;
  }
  if (project.tracking_swap_detection === undefined) {
    project.tracking_swap_detection = pipelineConfig.tracking_swap_detection || false;
  }
  if (project.tracking_appearance_match === undefined) {
    project.tracking_appearance_match = pipelineConfig.tracking_appearance_match || false;
  }
  if (project.tracking_id_lock === undefined) {
    project.tracking_id_lock = pipelineConfig.tracking_id_lock || false;
  }
  if (project.tracking_id_lock_frames === undefined) {
    project.tracking_id_lock_frames = pipelineConfig.tracking_id_lock_frames || 15;
  }
  if (project.counting_expected_list === undefined) {
    const items = pipelineConfig.counting_expected_items || {};
    project.counting_expected_list = Object.entries(items).map(([label, count]) => ({ label, count }));
  }
  if (project.tracking_roi_polygon === undefined) {
    const roi = pipelineConfig.tracking_roi || {};
    project.tracking_roi_polygon = roi.polygon || [];
  }
  
  // 同步到 pipeline_config，供UI使用
  project.pipeline_config.sequence_order = project.sequence_order;
  project.pipeline_config.detection_steps = project.detection_steps;
  project.pipeline_config.custom_conditions = project.custom_conditions;
  project.pipeline_config.custom_based_on = project.custom_based_on;
  project.pipeline_config.custom_sequence_order = project.custom_sequence_order;
  project.pipeline_config.custom_detection_steps = project.custom_detection_steps;
  project.pipeline_config.accumulate_repeats = project.accumulate_repeats;
  project.pipeline_config.ng_cycle_protect_seconds = project.ng_cycle_protect_seconds || 0;
  project.pipeline_config.simultaneous_groups = project.simultaneous_groups;
  project.pipeline_config.settlement_mode = project.settlement_mode || 'first_step';
  project.pipeline_config.idle_timeout_seconds = project.idle_timeout_seconds || 0;
  
  return project;
};

// 选择项目
const selectProject = async (item) => {
  try {
    const res = await getProjectDetail(item.id);
    activeProject.value = initProjectDefaults(res.data);
  } catch (err) {
    console.error('加载项目详情失败:', err);
    activeProject.value = initProjectDefaults({ ...item });
  }
};

// 激活项目
const handleActivateProject = async () => {
  if (!activeProject.value) return;
  try {
    await activateProject(activeProject.value.id);
    projectStore.setCurrentProject(activeProject.value);
    ElMessage.success(`已激活项目: ${activeProject.value.name}`);
    loadProjects();
  } catch (err) {
    ElMessage.error('激活项目失败');
  }
};

// 打开创建对话框
const openCreateDialog = () => {
  newProjectForm.value = { name: '', task_type: 'detection', logic_mode: 'sequential' };
  createDialogVisible.value = true;
};

// 创建项目
const handleCreateProject = async () => {
  if (!newProjectForm.value.name) {
    ElMessage.warning('请输入项目名称');
    return;
  }
  creating.value = true;
  try {
    const data = {
      name: newProjectForm.value.name,
      task_type: newProjectForm.value.task_type,
      logic_mode: newProjectForm.value.logic_mode,
      steps_config: [],
      events_config: [
        { id: 1, name: '合格(OK)', color: '#10b981', actions: [{ counter_name: '合格总数', delta: 1 }, { counter_name: '总产量', delta: 1 }], show_notification: true, notification_type: 'normal' },
        { id: 2, name: '不良(NG)', color: '#ef4444', actions: [{ counter_name: '不良总数', delta: 1 }, { counter_name: '总产量', delta: 1 }], show_notification: true, notification_type: 'ng' }
      ],
      counters_config: [
        { name: '合格总数', value: 0 },
        { name: '不良总数', value: 0 },
        { name: '总产量', value: 0 }
      ],
      sequence_order: [],
      detection_steps: [],
      custom_conditions: []
    };
    const res = await createProject(data);
    projects.value.push(res.data);
    createDialogVisible.value = false;
    activeProject.value = initProjectDefaults(res.data);
    ElMessage.success('项目创建成功');
  } catch (err) {
    ElMessage.error('创建项目失败: ' + (err.response?.data?.detail || err.message));
  } finally {
    creating.value = false;
  }
};

// 保存项目
const handleSaveProject = async () => {
  if (!activeProject.value) return;
  saving.value = true;
  try {
    const data = {
      name: activeProject.value.name,
      task_type: activeProject.value.task_type,
      logic_mode: activeProject.value.logic_mode,
      steps_config: activeProject.value.steps_config,
      events_config: activeProject.value.events_config,
      counters_config: activeProject.value.counters_config,
      default_model_id: activeProject.value.default_model_id,
      pipeline_config: {
        sequence_order: activeProject.value.sequence_order,
        detection_steps: activeProject.value.detection_steps,
        custom_conditions: activeProject.value.custom_conditions,
        custom_based_on: activeProject.value.custom_based_on,
        custom_sequence_order: activeProject.value.custom_sequence_order,
        custom_detection_steps: activeProject.value.custom_detection_steps,
        accumulate_repeats: activeProject.value.accumulate_repeats,
        ng_cycle_protect_seconds: activeProject.value.ng_cycle_protect_seconds || 0,
        simultaneous_groups: (activeProject.value.simultaneous_groups || []).map(g => ({
          ...g,
          labels: (g.priority_order || []).filter(l => l)
        })),
        tracking_cycle_strategy: activeProject.value.tracking_cycle_strategy || 'all_gone',
        tracking_trigger_label: activeProject.value.tracking_trigger_label || '',
        tracking_trigger_min_frames: activeProject.value.tracking_trigger_min_frames || 15,
        tracking_gone_confirm_frames: activeProject.value.tracking_gone_confirm_frames || 30,
        tracking_max_lost_seconds: Math.max(5, ...(activeProject.value.steps_config || []).filter(s => s.enabled && s.tracking_max_lost_seconds).map(s => s.tracking_max_lost_seconds)),
        tracking_gone_threshold: 0,
        tracking_check_order: activeProject.value.tracking_check_order || false,
        tracking_swap_detection: activeProject.value.tracking_swap_detection || false,
        tracking_appearance_match: activeProject.value.tracking_appearance_match || false,
        tracking_id_lock: activeProject.value.tracking_id_lock || false,
        tracking_id_lock_frames: activeProject.value.tracking_id_lock_frames || 15,
        tracking_expected_order: activeProject.value.tracking_check_order
          ? (activeProject.value.counting_expected_list || []).flatMap(item => Array(item.count || 1).fill(item.label)).filter(Boolean)
          : [],
        counting_expected_items: (activeProject.value.counting_expected_list || []).reduce((acc, item) => {
          if (item.label) acc[item.label] = item.count || 1;
          return acc;
        }, {}),
        tracking_roi: {
          enabled: (activeProject.value.tracking_roi_polygon || []).length >= 3,
          polygon: activeProject.value.tracking_roi_polygon || []
        },
        settlement_mode: activeProject.value.settlement_mode || 'first_step',
        idle_timeout_seconds: activeProject.value.idle_timeout_seconds || 0
      }
    };
    await updateProject(activeProject.value.id, data);
    
    activeProject.value.pipeline_config = data.pipeline_config;
    
    const idx = projects.value.findIndex(p => p.id === activeProject.value.id);
    if (idx !== -1) {
      projects.value[idx] = { ...projects.value[idx], ...data };
    }
    
    if (projectStore.currentProjectId === activeProject.value.id) {
      projectStore.setCurrentProject(activeProject.value);
    }
    
    ElMessage.success('配置已保存');
  } catch (err) {
    ElMessage.error('保存失败: ' + (err.response?.data?.detail || err.message));
  } finally {
    saving.value = false;
  }
};

// 删除项目
const handleDeleteProject = async () => {
  if (!activeProject.value) return;
  try {
    await ElMessageBox.confirm('确认删除该项目吗? 此操作无法撤销。', '警告', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning'
    });
    
    await deleteProject(activeProject.value.id);
    
    const idx = projects.value.findIndex(p => p.id === activeProject.value.id);
    if (idx !== -1) {
      projects.value.splice(idx, 1);
    }
    
    if (projectStore.currentProjectId === activeProject.value.id) {
      projectStore.setCurrentProject(null);
    }
    
    activeProject.value = null;
    ElMessage.success('项目已删除');
  } catch (err) {
    if (err !== 'cancel') {
      ElMessage.error('删除失败');
    }
  }
};

// 选择模型
const selectModel = (model) => {
  activeProject.value.default_model_id = model.id;
  activeProject.value.model_name = model.name;
  activeProject.value.model_version = model.version || '';
  
  let labels = [];
  if (model.labels) {
    try {
      labels = typeof model.labels === 'string' ? JSON.parse(model.labels) : model.labels;
    } catch (e) {
      console.error('解析标签失败:', e);
    }
  }
  
  if (labels && labels.length > 0) {
    activeProject.value.steps_config = labels.map((label, idx) => ({
      id: idx + 1,
      label: label,
      displayLabel: label,
      enabled: true,
      threshold: 50,
      triggerEvent: null,
      min_frames: null,
      detection_type: 'dynamic',
      static_trigger_frames: 30,
      join_cycle: true,
      backup_for: null,
      default_pt: null,
      strict_order: false,
      accept_once: false,
      tracking_gone_confirm_frames: null,
      tracking_max_lost_seconds: 5.0,
      tracking_position_lock: false
    }));
    
    // 自动初始化顺序
    activeProject.value.sequence_order = labels.map((_, idx) => ({ step_id: idx + 1 }));
    activeProject.value.detection_steps = labels.map((_, idx) => idx + 1);
    
    ElMessage.success(`已选择模型: ${model.name}，识别到 ${labels.length} 个类别`);
  } else {
    ElMessage.warning(`已选择模型: ${model.name}，但未能解析到标签`);
  }
  
  showModelSelect.value = false;
};

// 顺序模式 - 步骤操作
const addSequenceStep = () => {
  if (!activeProject.value.sequence_order) activeProject.value.sequence_order = [];
  activeProject.value.sequence_order.push({ step_id: null });
};

const removeSequenceStep = (idx) => {
  activeProject.value.sequence_order.splice(idx, 1);
};

const isSettlementStep = (step) => {
  const mode = activeProject.value?.settlement_mode || 'first_step';
  const logicMode = activeProject.value?.logic_mode;
  if (logicMode !== 'sequential' && !(logicMode === 'custom' && activeProject.value?.custom_based_on === 'sequential')) {
    return false;
  }
  const seqOrder = activeProject.value?.sequence_order || [];
  if (seqOrder.length === 0) return false;
  if (mode === 'first_step') {
    return step.id === seqOrder[0]?.step_id;
  } else if (mode === 'last_step') {
    return step.id === seqOrder[seqOrder.length - 1]?.step_id;
  }
  return false;
};

watch(() => activeProject.value?.settlement_mode, (mode) => {
  if (!activeProject.value?.steps_config) return;
  const seqOrder = activeProject.value?.sequence_order || [];
  if (seqOrder.length === 0) return;
  const settlementStepId = mode === 'last_step'
    ? seqOrder[seqOrder.length - 1]?.step_id
    : seqOrder[0]?.step_id;
  const step = activeProject.value.steps_config.find(s => s.id === settlementStepId);
  if (step && step.strict_order) {
    step.strict_order = false;
  }
});

const moveStepUp = (idx) => {
  if (idx <= 0) return;
  const temp = activeProject.value.sequence_order[idx];
  activeProject.value.sequence_order[idx] = activeProject.value.sequence_order[idx - 1];
  activeProject.value.sequence_order[idx - 1] = temp;
};

const moveStepDown = (idx) => {
  if (idx >= activeProject.value.sequence_order.length - 1) return;
  const temp = activeProject.value.sequence_order[idx];
  activeProject.value.sequence_order[idx] = activeProject.value.sequence_order[idx + 1];
  activeProject.value.sequence_order[idx + 1] = temp;
};

// 自定义条件
const addCustomCondition = () => {
  if (!activeProject.value.custom_conditions) activeProject.value.custom_conditions = [];
  const nextId = activeProject.value.custom_conditions.length > 0 
    ? Math.max(...activeProject.value.custom_conditions.map(c => c.id || 0)) + 1 
    : 1;
  const nextPriority = activeProject.value.custom_conditions.length + 1;
  activeProject.value.custom_conditions.push({ 
    id: nextId,
    priority: nextPriority,
    sequence: [],  // 步骤ID数组
    event_id: null 
  });
};

const removeCustomCondition = (idx) => {
  activeProject.value.custom_conditions.splice(idx, 1);
};

// 自定义条件中的步骤操作
const addConditionStep = (condIdx) => {
  if (!activeProject.value.custom_conditions[condIdx].sequence) {
    activeProject.value.custom_conditions[condIdx].sequence = [];
  }
  activeProject.value.custom_conditions[condIdx].sequence.push(null);
};

const removeConditionStep = (condIdx, stepIdx) => {
  activeProject.value.custom_conditions[condIdx].sequence.splice(stepIdx, 1);
};

// 同时出现组操作
const addSimultaneousGroup = () => {
  if (!activeProject.value.simultaneous_groups) activeProject.value.simultaneous_groups = [];
  activeProject.value.simultaneous_groups.push({
    enabled: true,
    labels: [],
    time_window: 2.0,
    priority_order: []
  });
};

const removeSimultaneousGroup = (idx) => {
  activeProject.value.simultaneous_groups.splice(idx, 1);
};

const addSimGroupStep = (gIdx) => {
  const group = activeProject.value.simultaneous_groups[gIdx];
  if (!group.priority_order) group.priority_order = [];
  group.priority_order.push('');
};

const removeSimGroupStep = (gIdx, sIdx) => {
  const group = activeProject.value.simultaneous_groups[gIdx];
  group.priority_order.splice(sIdx, 1);
  group.labels = group.priority_order.filter(l => l);
};

const moveSimGroupStep = (gIdx, sIdx, direction) => {
  const group = activeProject.value.simultaneous_groups[gIdx];
  const newIdx = sIdx + direction;
  if (newIdx < 0 || newIdx >= group.priority_order.length) return;
  const temp = group.priority_order[sIdx];
  group.priority_order[sIdx] = group.priority_order[newIdx];
  group.priority_order[newIdx] = temp;
  group.labels = group.priority_order.filter(l => l);
};

// 自定义模式 - 独立的顺序配置操作
const addCustomSequenceStep = () => {
  if (!activeProject.value.custom_sequence_order) activeProject.value.custom_sequence_order = [];
  activeProject.value.custom_sequence_order.push({ step_id: null });
};

const removeCustomSequenceStep = (idx) => {
  activeProject.value.custom_sequence_order.splice(idx, 1);
};

// 步骤启用状态变化时的清理/恢复逻辑
const onStepEnabledChange = (step, enabled) => {
  const stepId = step.id;
  
  if (!enabled) {
    // === 禁用：从所有配置中移除 ===
    if (activeProject.value.sequence_order) {
      activeProject.value.sequence_order = activeProject.value.sequence_order.filter(
        item => item.step_id !== stepId
      );
    }
    if (activeProject.value.detection_steps) {
      activeProject.value.detection_steps = activeProject.value.detection_steps.filter(
        id => id !== stepId
      );
    }
    if (activeProject.value.custom_sequence_order) {
      activeProject.value.custom_sequence_order = activeProject.value.custom_sequence_order.filter(
        item => item.step_id !== stepId
      );
    }
    if (activeProject.value.custom_detection_steps) {
      activeProject.value.custom_detection_steps = activeProject.value.custom_detection_steps.filter(
        id => id !== stepId
      );
    }
    if (activeProject.value.custom_conditions) {
      activeProject.value.custom_conditions.forEach(cond => {
        if (cond.sequence) {
          cond.sequence = cond.sequence.filter(id => id !== stepId);
        }
      });
    }
    console.log(`步骤 [${step.label}] 已禁用，已从所有配置中移除`);
  } else {
    // === 启用：加回到相关配置中 ===
    if (!activeProject.value.sequence_order) activeProject.value.sequence_order = [];
    const alreadyInSeq = activeProject.value.sequence_order.some(item => item.step_id === stepId);
    if (!alreadyInSeq) {
      activeProject.value.sequence_order.push({ step_id: stepId });
    }
    
    if (!activeProject.value.detection_steps) activeProject.value.detection_steps = [];
    if (!activeProject.value.detection_steps.includes(stepId)) {
      activeProject.value.detection_steps.push(stepId);
    }
    
    if (!activeProject.value.custom_sequence_order) activeProject.value.custom_sequence_order = [];
    const alreadyInCustomSeq = activeProject.value.custom_sequence_order.some(item => item.step_id === stepId);
    if (!alreadyInCustomSeq) {
      activeProject.value.custom_sequence_order.push({ step_id: stepId });
    }
    
    if (!activeProject.value.custom_detection_steps) activeProject.value.custom_detection_steps = [];
    if (!activeProject.value.custom_detection_steps.includes(stepId)) {
      activeProject.value.custom_detection_steps.push(stepId);
    }
    
    console.log(`步骤 [${step.label}] 已启用，已加回到配置中`);
  }
};

// 计数器操作
const addCounter = () => {
  if (!activeProject.value.counters_config) activeProject.value.counters_config = [];
  activeProject.value.counters_config.push({ name: '新计数器', value: 0 });
};

const removeCounter = (idx) => {
  activeProject.value.counters_config.splice(idx, 1);
};

// 事件操作
const addEvent = () => {
  if (!activeProject.value.events_config) activeProject.value.events_config = [];
  const newId = Date.now();
  activeProject.value.events_config.push({
    id: newId,
    name: '新事件',
    color: '#3b82f6',
    actions: [],
    show_notification: false,
    notification_type: 'normal'
  });
};

const removeEvent = (idx) => {
  activeProject.value.events_config.splice(idx, 1);
};

const addEventAction = (event) => {
  if (!event.actions) event.actions = [];
  event.actions.push({
    counter_name: activeProject.value.counters_config?.[0]?.name || '合格总数',
    delta: 1
  });
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

:deep(.project-tabs) {
  display: flex;
  flex-direction: column;
  height: 100%;
}
:deep(.project-tabs .el-tabs__header) {
  margin-bottom: 0px;
  flex-shrink: 0;
}
:deep(.project-tabs .el-tabs__content) {
  flex: 1;
  overflow: hidden;
  height: 100%;
}
:deep(.project-tabs .el-tab-pane) {
  height: 100%;
  display: flex;
  flex-direction: column;
}
</style>
