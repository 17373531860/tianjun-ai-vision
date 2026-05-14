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
                <el-input-number v-model="counter.value" size="small" :min="0" :precision="2" class="w-24" controls-position="right" />
                <span class="text-[10px] text-gray-500 bg-slate-700 px-1 rounded">默认</span>
              </div>
              <!-- 用户自定义计数器 -->
              <div v-if="customCounters.length > 0" class="text-xs text-gray-500 mt-3 mb-1">自定义：</div>
              <div v-for="(counter, idx) in customCounters" :key="'custom-'+idx" class="flex items-center gap-2 bg-slate-800 p-2 rounded">
                <el-input v-model="counter.name" size="small" placeholder="计数器名称" class="flex-1" />
                <el-input-number v-model="counter.value" size="small" :min="0" :precision="2" class="w-24" controls-position="right" />
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
                      <div class="flex gap-2">
                        <el-button v-if="activeProject.default_model_id" size="small" plain @click="openFormatSelect">切换格式</el-button>
                        <el-button type="primary" size="small" plain @click="showModelSelect = true">选择模型</el-button>
                      </div>
                    </div>
                  </template>
                  <div class="flex items-center gap-4">
                    <div class="w-16 h-16 bg-slate-700 rounded flex items-center justify-center">
                      <el-icon :size="24"><Cpu /></el-icon>
                    </div>
                    <div>
                      <p class="text-white font-bold">{{ activeProject.model_name || '未配置模型' }}<span v-if="activeProject.model_version" class="text-gray-400 font-normal ml-2">v{{ activeProject.model_version }}</span></p>
                      <p class="text-xs text-gray-500">Labels: {{ (activeProject.steps_config || []).length }} 个类别已识别
                        <el-tag v-if="activeProject.default_model_id" size="small" class="ml-2" :type="activeProject.model_format === 'pytorch_fp32' ? 'info' : 'success'">{{ getFormatDisplayName(activeProject.model_format || 'pytorch_fp32') }}</el-tag>
                      </p>
                      <div class="mt-2 flex gap-2 flex-wrap">
                        <el-tag v-for="tag in (activeProject.steps_config || []).slice(0, 5)" :key="tag.label" size="small" type="info">{{ tag.displayLabel || tag.label }}</el-tag>
                        <span v-if="(activeProject.steps_config || []).length > 5" class="text-xs text-gray-500">+{{ activeProject.steps_config.length - 5 }}</span>
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
                        :disabled="(activeProject?.extra_models?.length || 0) >= 4">
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
                  <div v-if="!activeProject?.extra_models?.length" class="text-center text-gray-500 py-6 text-sm">
                    暂无副模型，点击右上角"添加副模型"配置
                  </div>
                  <div v-else class="space-y-3">
                    <div v-for="(slot, idx) in activeProject.extra_models" :key="idx"
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
                            @click="openExtraModelFormatSelect(idx)">
                            切换格式
                          </el-button>
                          <el-button size="small" plain @click="openExtraModelSelect(idx)">
                            选择
                          </el-button>
                        </div>
                        <div class="flex items-center gap-2">
                          <span class="text-xs text-gray-400">颜色</span>
                          <el-color-picker v-model="slot.display_color" size="small" />
                        </div>
                        <el-button type="danger" size="small" plain
                          @click="removeExtraModel(idx)">
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
                          <el-option v-for="ev in (activeProject.events_config || [])"
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
                              @click="openExtraModelRoiEditor(idx)">
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
                        <el-switch v-model="activeProject.shift_split_enabled" />
                        <span class="text-sm text-gray-300">启用跨班次自动拆分会话</span>
                      </div>
                      <div class="text-xs text-gray-500 mt-1">开启后，检测会话在班次切换时自动结束并创建新会话（类似跨日拆分）。</div>
                    </el-form-item>
                    <div v-if="activeProject.shift_split_enabled" class="flex gap-6">
                      <el-form-item label="白班开始时间" class="flex-1">
                        <el-time-picker
                          v-model="activeProject.day_shift_start"
                          format="HH:mm"
                          value-format="HH:mm"
                          placeholder="08:00"
                          class="w-full"
                        />
                      </el-form-item>
                      <el-form-item label="晚班开始时间" class="flex-1">
                        <el-time-picker
                          v-model="activeProject.night_shift_start"
                          format="HH:mm"
                          value-format="HH:mm"
                          placeholder="20:00"
                          class="w-full"
                        />
                      </el-form-item>
                    </div>
                  </el-form>
                </el-card>
              </div>
            </div>
          </el-tab-pane>

          <!-- Tab 2: Step Settings -->
          <el-tab-pane :label="activeProject.logic_mode === 'tracking' ? '物品设置' : '步骤设置'" name="steps">
            <div class="h-full flex flex-col p-4">
              <div class="mb-3 flex flex-col gap-2 flex-shrink-0">
                <div class="text-sm text-gray-400 flex items-center">
                  <el-icon class="mr-1"><InfoFilled /></el-icon>
                  <template v-if="activeProject.logic_mode === 'tracking'">
                    配置各物品的启用状态与置信度。跟踪参数请在"逻辑设置"中配置。
                  </template>
                  <template v-else>
                    配置各步骤的启用状态、置信度、显示标签。启用的步骤将参与逻辑判断。
                  </template>
                </div>
                <div class="flex items-center gap-2 text-xs text-gray-300 flex-wrap">
                  <el-switch
                    v-model="activeProject.pipeline_config.hide_boxes_outside_step_roi"
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

              <div class="flex-1 overflow-y-auto custom-scrollbar min-h-0 pb-20">
                <table class="w-full text-left text-xs text-gray-300 border-collapse">
                  <thead class="bg-slate-800 text-gray-400 sticky top-0 z-10">
                    <tr class="border-b border-slate-700">
                      <th class="p-2">原始标签</th>
                      <th class="p-2 w-16">启用</th>
                      <th class="p-2 w-28">置信度阈值</th>
                      <th class="p-2 w-28">显示名称</th>
                      <th class="p-2 w-20">
                        <el-tooltip content="开启后，此物品在检测画面、SOP流程卡片、步骤详情中均不显示（仅视觉隐藏）；YOLO 检测、OK/NG 判定、报警、数据记录、MES 上报等均不受影响。常用于隐藏箱子/泡沫槽等辅助类别" placement="top">
                          <span class="cursor-help border-b border-dashed border-gray-500">隐藏标注框</span>
                        </el-tooltip>
                      </th>
                      <th class="p-2 w-20">
                        <el-tooltip content="该标签的检测框单独配色（任何模式都生效）。留空 = 沿用「显示设置」里的全局 OK/NG 颜色。副模型自带的 display_color 仍优先生效。" placement="top">
                          <span class="cursor-help border-b border-dashed border-gray-500">检测框颜色</span>
                        </el-tooltip>
                      </th>
                      <th class="p-2 w-36">
                        <el-tooltip content="归一化多边形区域。设置后：仅当检测框中心落在该区域内时，该步骤/标签才计入 SOP 与周期（顺序、检测、自定义、跟踪模式均生效）。不设置则不限区域。" placement="top">
                          <span class="cursor-help border-b border-dashed border-gray-500">步骤ROI</span>
                        </el-tooltip>
                      </th>
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
                        <el-tooltip content="堆叠模式下期望的堆叠层数，达到此数即认为该步骤完成（默认2）" placement="top">
                          <span class="cursor-help border-b border-dashed border-gray-500">堆叠层数</span>
                        </el-tooltip>
                      </th>
                      <th class="p-2 w-24">
                        <el-tooltip content="同时最多识别几个该物品；填0=无上限。设为1即不论同时检测到多少个都视为同一个ID。仅跟踪计数模式生效" placement="top">
                          <span class="cursor-help border-b border-dashed border-gray-500">最大识别数</span>
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
                        <el-tooltip content="同一步骤消失后再次出现，若间隔小于此值则视为同一次检测（0.01-3600秒，留空使用默认1秒）" placement="top">
                          <span class="cursor-help border-b border-dashed border-gray-500">去重间隔(秒)</span>
                        </el-tooltip>
                      </th>
                      <th class="p-2 w-28">
                        <el-tooltip content="步骤从画面消失后，等待多久确认其真正消失（默认0秒=立即确认，适当增大可容忍短暂检测中断）" placement="top">
                          <span class="cursor-help border-b border-dashed border-gray-500">消失确认(秒)</span>
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
                      <th v-if="activeProject.logic_mode !== 'detection'" class="p-2 w-20">
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
                    <tr v-for="step in (activeProject.steps_config || [])" :key="step.id" class="border-b border-slate-700 hover:bg-slate-700/30">
                      <td class="p-2 font-mono text-cyan-400">
                        <div class="flex items-center gap-1.5 flex-wrap">
                          <span>{{ step.label }}</span>
                          <el-tag v-if="step.from_model && step.from_model !== 'main'" size="small" type="warning" effect="plain" class="!h-5 !leading-5">
                            {{ step.from_model }}
                          </el-tag>
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
                      <td class="p-2 text-center">
                        <el-switch v-model="step.hide_in_view" size="small" />
                      </td>
                      <td class="p-2 text-center">
                        <el-color-picker v-model="step.box_color" size="small" :predefine="['#10b981','#ef4444','#f59e0b','#3b82f6','#a78bfa','#ec4899','#06b6d4','#84cc16']" />
                      </td>
                      <td class="p-2 align-top">
                        <div class="flex flex-col gap-1 min-w-[7rem]">
                          <div class="flex flex-wrap gap-1">
                            <el-button size="small" type="primary" plain @click="openStepRoiEditor(step)">
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
                      <template v-if="activeProject.logic_mode === 'tracking'">
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
                      </template>
                      <template v-if="activeProject.logic_mode !== 'tracking'">
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
                        <el-input-number 
                          v-model="step.max_interval" 
                          size="small" 
                          :min="0" 
                          :step="0.1"
                          :precision="2"
                          :controls="false"
                          placeholder="默认1秒"
                          class="w-full"
                        />
                      </td>
                      <td class="p-2">
                        <el-input-number 
                          v-model="step.disappear_delay" 
                          size="small" 
                          :min="0" 
                          :step="0.1"
                          :precision="2"
                          :controls="false"
                          placeholder="默认0秒"
                          class="w-full"
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
                          :min="0" 
                          :step="0.1"
                          :precision="2"
                          :controls="false"
                          placeholder="--"
                          class="w-full"
                        />
                      </td>
                      <td v-if="activeProject.logic_mode !== 'detection'" class="p-2">
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
                    <el-input-number v-model="activeProject.idle_timeout_seconds" size="small" :min="0" :step="5" :precision="2" />
                    <span class="text-xs text-gray-500">超过此时间无新步骤加入，强制结算当前周期（0=不启用）</span>
                  </div>
                  <div class="flex items-center gap-3 pt-2 border-t border-slate-700">
                    <span class="text-gray-400 text-xs whitespace-nowrap">周期超时(秒)</span>
                    <el-input-number v-model="activeProject.cycle_max_duration" size="small" :min="0" :step="5" :precision="2" />
                    <span class="text-xs text-gray-500">周期总时长超过此值直接判定NG（0=不启用）</span>
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
                  <p class="text-xs text-gray-400">选择需要检测的步骤。第一个步骤开启周期，最后一个步骤结束周期，中间步骤无需顺序。全部检测到 → 合格(事件1)，缺少步骤 → NG(事件2)</p>
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
                  <div v-for="(rule, idx) in (activeProject.periodic_actions || [])" :key="rule.id || idx"
                       class="bg-slate-900 p-3 rounded border border-slate-700">
                    <div class="flex justify-between items-start mb-3">
                      <div class="flex items-center gap-3 flex-1">
                        <el-switch v-model="rule.enabled" size="small" />
                        <el-input v-model="rule.name" size="small" placeholder="规则名（如：每20件清洁治具）"
                                  class="flex-1 max-w-md" />
                      </div>
                      <el-button type="danger" size="small" link @click="removePeriodicAction(idx)">删除</el-button>
                    </div>

                    <div class="grid grid-cols-2 gap-3 mb-3">
                      <div>
                        <p class="text-xs text-gray-500 mb-1">强制周期 N（每多少轮必须做一次）</p>
                        <el-input-number v-model="rule.interval" size="small" :min="1" :max="100000"
                                         class="w-full" controls-position="right" />
                      </div>
                      <div>
                        <p class="text-xs text-gray-500 mb-1">计数基准</p>
                        <el-select v-model="rule.count_basis" size="small" class="w-full">
                          <el-option label="所有 cycle 都计数" value="all" />
                          <el-option label="只数合格 cycle (推荐)" value="good_only" />
                          <el-option label="只数不良 cycle" value="ng_only" />
                        </el-select>
                      </div>
                    </div>

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
                        <el-select v-model="rule.overdue_repeat" size="small" class="w-full">
                          <el-option label="每个 cycle 都触发" value="every_cycle" />
                          <el-option label="只在刚超期时触发一次" value="once" />
                          <el-option label="冷却 5 轮触发一次" value="cooldown:5" />
                          <el-option label="冷却 10 轮触发一次" value="cooldown:10" />
                          <el-option label="冷却 20 轮触发一次" value="cooldown:20" />
                        </el-select>
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
                          <el-option v-for="ev in (activeProject.events_config || [])"
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
                          <el-option v-for="ev in (activeProject.events_config || [])"
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

                  <div v-if="!activeProject.periodic_actions || activeProject.periodic_actions.length === 0"
                       class="text-gray-500 text-center py-4 border border-dashed border-slate-700 rounded">
                    暂无规则，点击右上角"新增规则"添加（如：每 20 轮清洁治具）
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
                      <div class="grid grid-cols-4 gap-2">
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
                        <label class="flex items-start gap-2 p-2.5 bg-slate-800 rounded border cursor-pointer transition-colors"
                          :class="activeProject.tracking_cycle_strategy === 'container' ? 'border-cyan-500 bg-cyan-500/10' : 'border-slate-700 hover:border-slate-500'"
                          @click="activeProject.tracking_cycle_strategy = 'container'">
                          <input type="radio" v-model="activeProject.tracking_cycle_strategy" value="container" class="mt-0.5 accent-cyan-500">
                          <div>
                            <div class="text-white text-xs font-bold">容器模式</div>
                            <div class="text-[10px] text-gray-500 mt-0.5">每个箱子独立结算</div>
                          </div>
                        </label>
                      </div>
                    </div>

                    <!-- Row 2: strategy-specific options -->
                    <div v-if="activeProject.tracking_cycle_strategy === 'trigger'" class="flex items-center gap-4 text-xs">
                      <span class="text-gray-400 shrink-0">触发标签</span>
                      <el-select v-model="activeProject.tracking_trigger_label" size="small" class="!w-40" placeholder="选择标签">
                        <el-option v-for="s in nonBackupSteps" :key="s.id" :label="s.displayLabel || s.label" :value="s.label" />
                      </el-select>
                      <span class="text-gray-400 shrink-0">确认帧数</span>
                      <el-input-number v-model="activeProject.tracking_trigger_min_frames" :min="0" :step="5" :precision="2" size="small" class="!w-28" />
                    </div>
                    <div v-else-if="activeProject.tracking_cycle_strategy === 'container'" class="flex items-center gap-4 text-xs flex-wrap">
                      <span class="text-gray-400 shrink-0">容器类别</span>
                      <el-select v-model="activeProject.tracking_container_label" size="small" class="!w-40" placeholder="选择容器标签">
                        <el-option v-for="s in nonBackupSteps" :key="s.id" :label="s.displayLabel || s.label" :value="s.label" />
                      </el-select>
                      <span class="text-gray-400 shrink-0">消失确认帧数</span>
                      <el-input-number v-model="activeProject.tracking_gone_confirm_frames" :min="0" :step="5" :precision="2" size="small" class="!w-28" />
                      <el-tooltip placement="top">
                        <template #content>
                          <div style="max-width:280px;line-height:1.5">
                            <b>单箱(默认)</b>: 画面里同时出现两个箱子时只承认最早进入的"主箱",其他箱子被屏蔽,落进它们的物品也不计。主箱出去后下一个箱子自动接管,杜绝"一码两箱"和野生 settle。<br/>
                            <b>多箱(实验性)</b>: 历史行为,允许多个箱子同时被分组。当前 cycle 调度会让一个工件号绑多个箱子,会污染数据,仅用于排查回归。
                          </div>
                        </template>
                        <span class="text-gray-400 shrink-0 cursor-help">同框策略 ⓘ</span>
                      </el-tooltip>
                      <el-select v-model="activeProject.container_box_mode" size="small" class="!w-44">
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
                      <el-input-number v-model="activeProject.container_settle_min_items" :min="0" :max="100" :step="1" :precision="2" size="small" class="!w-24" />
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
                      <el-input-number v-model="activeProject.container_id_drift_merge_iou" :min="0" :max="1" :step="0.05" :precision="2" size="small" class="!w-24" />
                    </div>
                    <div v-else class="flex items-center gap-4 text-xs">
                      <span class="text-gray-400 shrink-0">消失确认帧数</span>
                      <el-input-number v-model="activeProject.tracking_gone_confirm_frames" :min="0" :step="5" :precision="2" size="small" class="!w-28" />
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
                        <el-input-number v-model="activeProject.tracking_id_lock_frames" :min="0" :step="5" :precision="2" size="small" class="!w-24" />
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
                        <el-input-number v-model="activeProject.tracking_match_thresh" :min="0.1" :max="0.99" :step="0.05" :precision="2" size="small" class="!w-24" />
                      </div>
                      <div class="flex items-center gap-1.5">
                        <span class="text-gray-400">顺序检查</span>
                        <el-switch v-model="activeProject.tracking_check_order" size="small" />
                      </div>
                    </div>

                    <!-- Row 4: Expected items + ROI side by side -->
                    <div class="grid grid-cols-2 gap-4">
                      <div>
                        <div class="text-xs text-gray-400 mb-1.5">{{ activeProject.tracking_cycle_strategy === 'container' ? '每箱期望物品' : '期望物品清单' }}</div>
                        <div class="bg-slate-900 rounded p-2.5 space-y-1.5">
                          <div v-for="(item, idx) in activeProject.counting_expected_list" :key="idx" class="flex items-center gap-1.5 bg-slate-800 p-1.5 rounded">
                            <el-select v-model="item.label" size="small" class="flex-1 min-w-0" placeholder="选择物品">
                              <el-option v-for="s in countableSteps" :key="s.id" :label="s.displayLabel || s.label" :value="s.label" />
                            </el-select>
                            <span class="text-gray-500 text-xs shrink-0">×</span>
                            <el-input-number v-model="item.count" size="small" :min="0" :step="1" :precision="2" class="!w-20 shrink-0" />
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
                    <el-input-number v-model="activeProject.ng_cycle_protect_seconds" size="small" :min="0" :step="1" :precision="2" />
                  </div>
                </div>
              </el-card>

              <!-- 防重复结算 -->
              <el-card shadow="never" class="bg-slate-800 border-slate-700">
                <template #header><span class="font-bold text-white">防重复结算</span></template>
                <div class="space-y-3 text-sm text-gray-300">
                  <p class="text-xs text-gray-400">开启后，同一结算批次中如果已有事件结束了当前周期，后续事件将被抑制，避免一次结算同时产生 OK 和 NG 两个结果。</p>
                  <div class="flex items-center gap-3">
                    <span>启用防重复结算</span>
                    <el-switch v-model="activeProject.settle_dedup" />
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
                          <el-input-number v-model="group.time_window" size="small" :min="0" :step="0.5" :precision="2" />
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
                      <el-switch v-model="activeProject.rod_companion_filter.enabled" active-color="#22d3ee" />
                    </div>
                    <div v-if="activeProject.rod_companion_filter.enabled" class="grid grid-cols-1 md:grid-cols-3 gap-3 text-xs">
                      <div>
                        <label class="block text-gray-400 mb-1">目标 label（要过滤的易误判类别）</label>
                        <el-select
                          v-model="activeProject.rod_companion_filter.rod_label"
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
                          v-model="activeProject.rod_companion_filter.companion_labels"
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
                          v-model="activeProject.rod_companion_filter.iou_threshold"
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
                      <el-switch v-model="activeProject.rod_session_gate.enabled" active-color="#22d3ee" />
                    </div>
                    <div v-if="activeProject.rod_session_gate.enabled" class="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
                      <div>
                        <label class="block text-gray-400 mb-1">目标 label（被门控的类别）</label>
                        <el-select
                          v-model="activeProject.rod_session_gate.rod_label"
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
                          v-model="activeProject.rod_session_gate.gate_labels"
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
    <el-dialog v-model="showModelSelect"
      :title="extraModelSelectingIdx >= 0
        ? `选择副模型 [${activeProject?.extra_models?.[extraModelSelectingIdx]?.name || ''}]`
        : '选择模型'"
      width="600px" @close="extraModelSelectingIdx = -1">
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

    <!-- Format Select Dialog -->
    <el-dialog v-model="showFormatSelect"
      :title="formatSelectingExtraIdx !== null
        ? `选择推理格式 [副模型: ${activeProject?.extra_models?.[formatSelectingExtraIdx]?.name || ''}]`
        : '选择推理格式 [主模型]'"
      width="640px" :close-on-click-modal="!convertingFormat" @close="cancelFormatSelect">
      <div v-if="convertingFormat" class="text-center py-12">
        <el-icon class="is-loading text-4xl text-blue-400 mb-4"><Loading /></el-icon>
        <p class="text-white text-lg mb-2">正在转换为 {{ getFormatDisplayName(convertingFormat) }}...</p>
        <p class="text-gray-400 text-sm">预计需要 2-10 分钟，请勿关闭此窗口</p>
        <el-button class="mt-6" @click="cancelFormatSelect">取消并使用原始格式</el-button>
      </div>
      <div v-else class="space-y-2 max-h-[28rem] overflow-y-auto pr-1">
        <div class="flex items-center justify-between mb-3">
          <p v-if="gpuName" class="text-xs text-gray-400">当前显卡: {{ gpuName }}</p>
          <el-button size="small" text type="info" @click="showDiagnosis">
            <el-icon class="mr-1"><Warning /></el-icon>环境诊断
          </el-button>
        </div>
        <div v-for="fmt in formatList" :key="fmt.key"
          @click="selectFormat(fmt)"
          :class="[
            'p-3 rounded border transition-all',
            fmt.available
              ? 'cursor-pointer hover:border-blue-500 bg-slate-800 border-slate-700'
              : 'cursor-not-allowed opacity-50 bg-slate-900 border-slate-800',
            (formatSelectingExtraIdx !== null
              ? (activeProject?.extra_models?.[formatSelectingExtraIdx]?.model_format || 'pytorch_fp32')
              : (activeProject.model_format || 'pytorch_fp32')) === fmt.key
              ? 'border-blue-500 bg-slate-700'
              : ''
          ]">
          <div class="flex justify-between items-start">
            <div>
              <span class="text-white font-bold">{{ fmt.name }}</span>
              <span class="text-gray-500 text-xs ml-2">({{ fmt.extension }})</span>
            </div>
            <div class="flex gap-1.5">
              <el-tag v-if="fmt.tag" size="small" :type="fmt.tag === '最快' ? 'success' : fmt.tag === '实验性' ? 'warning' : 'info'">{{ fmt.tag }}</el-tag>
              <el-tag v-if="recommendedFormat === fmt.key" size="small" type="success">推荐</el-tag>
              <el-tag v-if="(formatSelectingExtraIdx !== null
                ? (activeProject?.extra_models?.[formatSelectingExtraIdx]?.model_format || 'pytorch_fp32')
                : (activeProject.model_format || 'pytorch_fp32')) === fmt.key" size="small">当前</el-tag>
            </div>
          </div>
          <p class="text-xs text-gray-400 mt-1">{{ fmt.description }}</p>
          <p v-if="!fmt.available" class="text-xs text-red-400 mt-1">{{ fmt.unavailable_reason }}</p>
          <p v-if="fmt.key.startsWith('tensorrt') && fmt.available" class="text-xs text-amber-400 mt-1">此格式仅在当前显卡上有效，更换显卡后需重新转换</p>
        </div>
      </div>
    </el-dialog>

    <!-- ROI Polygon Editor Dialog -->
    <el-dialog v-model="roiEditorVisible"
      :title="roiEditorDialogTitle"
      width="80%" :close-on-click-modal="false" destroy-on-close
      class="roi-editor-dialog"
      @close="resetRoiEditorTargets">
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
import { ref, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue';
import { Plus, Search, EditPen, FolderAdd, Upload, InfoFilled, Check, Cpu, Delete, Loading, Warning, QuestionFilled } from '@element-plus/icons-vue';
import { useProjectStore } from '@/store/useProjectStore';
import { useSystemStore } from '@/store/useSystemStore';
import { ElMessage, ElMessageBox } from 'element-plus';
import { getProjects, getProjectDetail, createProject, updateProject, deleteProject, activateProject } from '@/api/project';
import { getModels, getAvailableFormats, convertModel, getConversionStatus, getFormatDiagnosis } from '@/api/model';
import { getBackendHost } from '@/api/index';

const projectStore = useProjectStore();
const systemStore = useSystemStore();
const searchQuery = ref('');
const activeProject = ref(null);
const activeTab = ref('basic');
const createDialogVisible = ref(false);
const showModelSelect = ref(false);
const showFormatSelect = ref(false);
// v3.7.x: null=主模型走 openFormatSelect; 数字=副 slot idx 走 openExtraModelFormatSelect.
// selectFormat / startConversionPolling 据此决定: 调哪个 model_id 转换, 写回哪个 .model_format.
const formatSelectingExtraIdx = ref(null);
const formatList = ref([]);
const recommendedFormat = ref('pytorch_fp32');
const gpuName = ref('');
const convertingFormat = ref(null);
const conversionId = ref(null);
const conversionPolling = ref(null);

const stopConversionPolling = () => {
  if (conversionPolling.value) {
    clearInterval(conversionPolling.value);
    conversionPolling.value = null;
  }
};

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
  const container = activeProject.value?.tracking_cycle_strategy === 'container'
    ? (activeProject.value?.tracking_container_label || '') : '';
  return nonBackupSteps.value.filter(s => s.label !== trigger && s.label !== container);
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

// 当前项目已配置过的 label 集合（去重），给误判过滤的下拉当候选项用
const availableLabels = computed(() => {
  const set = new Set();
  const steps = activeProject.value?.steps_config || [];
  steps.forEach(s => {
    if (s && s.label) set.add(s.label);
  });
  return Array.from(set);
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

onBeforeUnmount(() => {
  stopConversionPolling();
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

// Step 8 (feat/multi-model-roi-link): 模型选择 / ROI 编辑 的目标 idx.
// = -1 表示主模型 (写到 default_model_id / tracking_roi_polygon, 老路径)
// >= 0 表示副模型 idx (写到 extra_models[idx])
const extraModelSelectingIdx = ref(-1);
const extraModelRoiEditingIdx = ref(-1);
// 步骤 ROI 编辑: 指向 steps_config 中某项的 id (与副模型/全局 tracking_roi 互斥)
const stepRoiEditingStepId = ref(null);
let roiImage = null;
let roiMousePos = null;

const resetRoiEditorTargets = () => {
  extraModelRoiEditingIdx.value = -1;
  stepRoiEditingStepId.value = null;
};

const roiEditorDialogTitle = computed(() => {
  const ap = activeProject.value;
  if (extraModelRoiEditingIdx.value >= 0) {
    const slot = ap?.extra_models?.[extraModelRoiEditingIdx.value];
    return `绘制副模型 [${slot?.name || ''}] ROI 区域`;
  }
  if (stepRoiEditingStepId.value != null) {
    const st = ap?.steps_config?.find(s => s.id === stepRoiEditingStepId.value);
    return `绘制步骤 [${st?.displayLabel || st?.label || ''}] ROI（框中心须在区域内才算该步骤）`;
  }
  return '绘制 ROI 检测区域';
});

const openRoiEditor = async () => {
  resetRoiEditorTargets();
  roiPoints.value = [];
  roiPolygonClosed.value = false;
  roiMousePos = null;
  roiEditorVisible.value = true;
  await nextTick();
  setTimeout(() => loadRoiSnapshot(), 200);
};

// d1: 改为 Promise-based, 让调用方 await 图片加载完再画 polygon (替代裸 setTimeout 时序坑).
const loadRoiSnapshot = () => {
  return new Promise((resolve) => {
    const canvas = roiEditorCanvas.value;
    if (!canvas) { resolve(false); return; }
    const img = new Image();
    img.crossOrigin = 'anonymous';
    const host = getBackendHost();
    img.src = `${host}/snapshot?channel=0&t=${Date.now()}`;
    img.onload = () => {
      roiImage = img;
      canvas.width = img.naturalWidth;
      canvas.height = img.naturalHeight;
      roiRedraw();
      resolve(true);
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
      resolve(false);
    };
  });
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
  const polygon = roiPoints.value.map(pt => [
    Math.round((pt.x / w) * 10000) / 10000,
    Math.round((pt.y / h) * 10000) / 10000
  ]);

  // Step 8: 副模型 ROI 编辑模式
  if (extraModelRoiEditingIdx.value >= 0) {
    const idx = extraModelRoiEditingIdx.value;
    const slot = activeProject.value.extra_models?.[idx];
    if (slot) {
      slot.roi = polygon;
      ElMessage.success(`副模型 [${slot.name}] ROI 已保存 (${polygon.length} 个顶点)`);
    }
    resetRoiEditorTargets();
    roiEditorVisible.value = false;
    return;  // 副模型 ROI 不立即触发 saveProject (随主保存按钮一起提交)
  }

  // 逐步骤 ROI (顺序/检测/自定义/跟踪): 写入 steps_config[].roi
  if (stepRoiEditingStepId.value != null) {
    const sid = stepRoiEditingStepId.value;
    const st = activeProject.value?.steps_config?.find(s => s.id === sid);
    if (st) {
      st.roi = polygon;
      ElMessage.success(`步骤 [${st.displayLabel || st.label}] ROI 已保存 (${polygon.length} 个顶点)`);
    }
    resetRoiEditorTargets();
    roiEditorVisible.value = false;
    return;
  }

  activeProject.value.tracking_roi_polygon = polygon;
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

// 误判过滤：保存前归一化，避免空 label 造成后端无谓误判
const _cleanLabels = (arr) => (Array.isArray(arr) ? arr : [])
  .map(x => (typeof x === 'string' ? x.trim() : ''))
  .filter(Boolean);

const _sanitizeCompanionFilter = (cf) => {
  const c = cf || {};
  const enabled = !!c.enabled;
  const rod = (c.rod_label || '').trim();
  const labels = _cleanLabels(c.companion_labels);
  let iou = Number(c.iou_threshold);
  if (!Number.isFinite(iou)) iou = 0.25;
  iou = Math.max(0, Math.min(1, iou));
  return {
    enabled: enabled && !!rod && labels.length > 0,
    iou_threshold: iou,
    rod_label: rod,
    companion_labels: labels
  };
};

const _sanitizeSessionGate = (gt) => {
  const g = gt || {};
  const enabled = !!g.enabled;
  const rod = (g.rod_label || '').trim();
  const labels = _cleanLabels(g.gate_labels);
  return {
    enabled: enabled && !!rod && labels.length > 0,
    rod_label: rod,
    gate_labels: labels
  };
};

// 初始化项目默认配置
const initProjectDefaults = (project) => {
  if (!project.model_format) project.model_format = 'pytorch_fp32';
  if (!project.steps_config) project.steps_config = [];
  (project.steps_config || []).forEach(step => {
    if (step.count_mode === undefined) step.count_mode = 'track';
    if (step.event_required_count === undefined) step.event_required_count = 1;
    if (step.event_gone_frames === undefined) step.event_gone_frames = 8;
    if (step.roi === undefined) step.roi = null;
    if (step.box_color === undefined) step.box_color = '';
    if (step.from_model === undefined) step.from_model = 'main';
  });

  // v3.7.x (FIX): 清理孤儿步骤 — from_model 既不是 'main' 也对不上当前 extra_models slot.
  // 来源: 老版本 removeExtraModel 只 splice slot 不清 steps_config, 留下"幽灵标签".
  // 后果: 步骤详情显示已删除的副模型 label, 还可能因为 sequence_order 残留引用导致
  // "压墨"被误标结算步骤等. 反序列化时自愈一次.
  const _extraSlotNames = new Set(
    Array.isArray(project.extra_models)
      ? project.extra_models.map(m => m && m.name).filter(Boolean)
      : (project.pipeline_config?.models || [])
          .filter(m => m && m.name && m.name !== 'main')
          .map(m => m.name)
  );
  const _orphanIds = new Set();
  project.steps_config = project.steps_config.filter(s => {
    const fm = s.from_model;
    if (!fm || fm === 'main') return true;
    if (_extraSlotNames.has(fm)) return true;
    _orphanIds.add(s.id);
    console.warn(`[Project] 自动清理孤儿步骤: id=${s.id} label=${s.label} from_model=${fm} (副模型 slot 已删除)`);
    return false;
  });
  if (_orphanIds.size > 0) {
    // 同步清掉 sequence_order / custom_sequence_order / detection_steps / custom_detection_steps
    const _filterSeq = (arr) => Array.isArray(arr) ? arr.filter(item => !_orphanIds.has(item?.step_id)) : arr;
    const _filterIds = (arr) => Array.isArray(arr) ? arr.filter(id => !_orphanIds.has(id)) : arr;
    if (project.sequence_order) project.sequence_order = _filterSeq(project.sequence_order);
    if (project.custom_sequence_order) project.custom_sequence_order = _filterSeq(project.custom_sequence_order);
    if (project.detection_steps) project.detection_steps = _filterIds(project.detection_steps);
    if (project.custom_detection_steps) project.custom_detection_steps = _filterIds(project.custom_detection_steps);
    if (Array.isArray(project.custom_conditions)) {
      project.custom_conditions.forEach(cond => {
        if (Array.isArray(cond?.sequence)) {
          cond.sequence = cond.sequence.filter(id => !_orphanIds.has(id));
        }
      });
    }
    if (Array.isArray(project.pipeline_config?.simultaneous_groups)) {
      project.pipeline_config.simultaneous_groups.forEach(g => {
        if (Array.isArray(g?.step_ids)) {
          g.step_ids = g.step_ids.filter(id => !_orphanIds.has(id));
        }
      });
    }
  }
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
  if (project.pipeline_config.hide_boxes_outside_step_roi === undefined) {
    project.pipeline_config.hide_boxes_outside_step_roi = !!pipelineConfig.hide_boxes_outside_step_roi;
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

  // v3.5.0: 周期性强制动作（每 N 轮做 E 否则告警）
  if (project.periodic_actions === undefined) {
    project.periodic_actions = pipelineConfig.periodic_actions || [];
  }
  project.periodic_actions = (project.periodic_actions || []).map((rule, idx) => ({
    id: rule.id || `pa_${Date.now()}_${idx}`,
    name: rule.name || `规则 ${idx + 1}`,
    enabled: rule.enabled !== false,
    trigger_step_ids: Array.isArray(rule.trigger_step_ids) ? rule.trigger_step_ids : [],
    interval: typeof rule.interval === 'number' ? rule.interval : 20,
    count_basis: rule.count_basis || 'good_only',
    reset_policy: rule.reset_policy || 'always',
    due_warning_event_id: rule.due_warning_event_id ?? null,
    overdue_event_id: rule.overdue_event_id ?? null,
    overdue_repeat: rule.overdue_repeat || 'every_cycle',
    channel_filter: rule.channel_filter || null,
    run_on_start: rule.run_on_start === true,  // v3.5.2: 开机首检
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
  // 防重复结算
  if (project.settle_dedup === undefined) {
    project.settle_dedup = pipelineConfig.settle_dedup || false;
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
  if (project.cycle_max_duration === undefined) {
    project.cycle_max_duration = pipelineConfig.cycle_max_duration || 0;
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
  if (project.tracking_match_thresh === undefined) {
    const v = pipelineConfig.tracking_match_thresh;
    project.tracking_match_thresh = (typeof v === 'number' && v > 0) ? v : 0.8;
  }
  if (project.counting_expected_list === undefined) {
    const items = pipelineConfig.counting_expected_items || {};
    project.counting_expected_list = Object.entries(items).map(([label, count]) => ({ label, count }));
  }
  if (project.tracking_container_label === undefined) {
    project.tracking_container_label = pipelineConfig.tracking_container_label || '';
  }
  if (project.container_box_mode === undefined) {
    project.container_box_mode = pipelineConfig.container_box_mode || 'single';
  }
  if (project.container_settle_min_items === undefined) {
    const v = pipelineConfig.container_settle_min_items;
    // v3.1.4: 默认 1 = 装件 < 1 (空箱) 视为幽灵箱不结算
    project.container_settle_min_items = (typeof v === 'number' && v >= 0) ? v : 1;
  }
  if (project.container_id_drift_merge_iou === undefined) {
    const v = pipelineConfig.container_id_drift_merge_iou;
    // v3.2.0: 默认 0 = 关闭, 老项目升级行为不变
    project.container_id_drift_merge_iou = (typeof v === 'number' && v >= 0 && v <= 1) ? v : 0;
  }
  if (project.container_id_drift_merge_max_gone_frames === undefined) {
    const v = pipelineConfig.container_id_drift_merge_max_gone_frames;
    project.container_id_drift_merge_max_gone_frames = (typeof v === 'number' && v > 0) ? v : 30;
  }
  if (project.tracking_roi_polygon === undefined) {
    const roi = pipelineConfig.tracking_roi || {};
    project.tracking_roi_polygon = roi.polygon || [];
  }

  // Step 8 (feat/multi-model-roi-link): 反序列化附加模型 (副 slot, 主模型由
  // default_model_id/model_format 管理). 来源 pipeline_config.models[]
  // 中所有 name !== 'main' 的项, 缺字段时用安全默认值.
  if (project.extra_models === undefined) {
    const rawModels = Array.isArray(pipelineConfig.models) ? pipelineConfig.models : [];
    project.extra_models = rawModels
      .filter(m => m && m.name && m.name !== 'main')
      .map(m => ({
        name: String(m.name || ''),
        model_id: m.model_id ?? null,
        model_name: m.model_name || '',
        model_version: m.model_version || '',
        model_format: m.model_format || 'pytorch_fp32',  // v3.7.x: 副模型格式 (老项目兜底)
        conf: typeof m.conf === 'number' ? m.conf : 0.25,
        iou: typeof m.iou === 'number' ? m.iou : 0.45,
        roi: Array.isArray(m.roi) ? m.roi : null,
        schedule_type: (m.schedule && m.schedule.type) || 'every_frame',
        schedule_n: (m.schedule && Number(m.schedule.n)) || 1,
        // c1+: on_event 模式下监听的事件 id 列表 (前端 UI 选 ev.id, 保存时拼成 'event_<id>')
        schedule_events: Array.isArray(m.schedule?.events)
          ? m.schedule.events
              .map(e => {
                if (typeof e === 'number') return e;
                const s = String(e);
                const match = s.match(/^event_(\d+)$/);
                return match ? Number(match[1]) : s;
              })
          : [],
        class_filter: Array.isArray(m.class_filter) ? [...m.class_filter] : [],
        available_labels: [],  // d2: UI-only, 选模型时填充 (老项目重新选一次模型即可恢复)
        priority: typeof m.priority === 'number' ? m.priority : 50,
        display_color: m.display_color || '#f59e0b',
        use_half: !!m.use_half,
      }));
  }

  // 误判过滤（通用两层后处理，v2.7.8 起走 pipeline_config；默认全关，老项目兼容）
  if (project.rod_companion_filter === undefined) {
    const cf = pipelineConfig.rod_companion_filter || {};
    project.rod_companion_filter = {
      enabled: !!cf.enabled,
      iou_threshold: typeof cf.iou_threshold === 'number' ? cf.iou_threshold : 0.25,
      rod_label: cf.rod_label || '',
      companion_labels: Array.isArray(cf.companion_labels) ? [...cf.companion_labels] : []
    };
  }
  if (project.rod_session_gate === undefined) {
    const gt = pipelineConfig.rod_session_gate || {};
    project.rod_session_gate = {
      enabled: !!gt.enabled,
      rod_label: gt.rod_label || '',
      gate_labels: Array.isArray(gt.gate_labels) ? [...gt.gate_labels] : []
    };
  }

  // 班次拆分配置（从 data_config 中读取）
  const dataConfig = project.data_config || {};
  if (project.shift_split_enabled === undefined) {
    project.shift_split_enabled = dataConfig.shift_split_enabled || false;
  }
  if (project.day_shift_start === undefined) {
    project.day_shift_start = dataConfig.day_shift_start || '08:00';
  }
  if (project.night_shift_start === undefined) {
    project.night_shift_start = dataConfig.night_shift_start || '20:00';
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
  project.pipeline_config.settle_dedup = project.settle_dedup || false;
  project.pipeline_config.simultaneous_groups = project.simultaneous_groups;
  project.pipeline_config.settlement_mode = project.settlement_mode || 'first_step';
  project.pipeline_config.idle_timeout_seconds = project.idle_timeout_seconds || 0;
  project.pipeline_config.cycle_max_duration = project.cycle_max_duration || 0;
  project.pipeline_config.periodic_actions = project.periodic_actions;
  
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
      model_format: activeProject.value.model_format || 'pytorch_fp32',
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
        tracking_match_thresh: (() => {
          const v = Number(activeProject.value.tracking_match_thresh);
          if (!Number.isFinite(v) || v <= 0) return 0.8;
          return Math.max(0.1, Math.min(0.99, v));
        })(),
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
        tracking_container_label: activeProject.value.tracking_cycle_strategy === 'container' ? (activeProject.value.tracking_container_label || '') : '',
        container_box_mode: activeProject.value.container_box_mode || 'single',
        container_settle_min_items: (() => {
          const v = Number(activeProject.value.container_settle_min_items);
          if (!Number.isFinite(v) || v < 0) return 1;
          return Math.max(0, Math.min(100, Math.floor(v)));
        })(),
        container_id_drift_merge_iou: (() => {
          const v = Number(activeProject.value.container_id_drift_merge_iou);
          if (!Number.isFinite(v) || v < 0) return 0;
          return Math.max(0, Math.min(1, v));
        })(),
        container_id_drift_merge_max_gone_frames: (() => {
          const v = Number(activeProject.value.container_id_drift_merge_max_gone_frames);
          if (!Number.isFinite(v) || v < 1) return 30;
          return Math.max(1, Math.min(180, Math.floor(v)));
        })(),
        settlement_mode: activeProject.value.settlement_mode || 'first_step',
        idle_timeout_seconds: activeProject.value.idle_timeout_seconds || 0,
        cycle_max_duration: activeProject.value.cycle_max_duration || 0,
        rod_companion_filter: _sanitizeCompanionFilter(activeProject.value.rod_companion_filter),
        rod_session_gate: _sanitizeSessionGate(activeProject.value.rod_session_gate),
        hide_boxes_outside_step_roi: !!activeProject.value.pipeline_config?.hide_boxes_outside_step_roi,
        periodic_actions: (activeProject.value.periodic_actions || []).map(rule => ({
          id: rule.id,
          name: rule.name || '',
          enabled: rule.enabled !== false,
          trigger_step_ids: Array.isArray(rule.trigger_step_ids) ? rule.trigger_step_ids : [],
          interval: Math.max(1, Math.floor(Number(rule.interval) || 20)),
          count_basis: rule.count_basis || 'good_only',
          reset_policy: rule.reset_policy || 'always',
          due_warning_event_id: rule.due_warning_event_id ?? null,
          overdue_event_id: rule.overdue_event_id ?? null,
          overdue_repeat: rule.overdue_repeat || 'every_cycle',
          channel_filter: rule.channel_filter || null,
          run_on_start: rule.run_on_start === true,
        })),
        // Step 8: 序列化多模型配置 (主 main + 副 slot 一并写入).
        // 主 spec 仅带可调字段 (name+display_color), conf/iou 仍归 step-level;
        // 副 spec 带完整 conf/iou/roi/schedule/class_filter/priority/display_color.
        // model_id 是前端关联用 (后端 apply_models_config 不读, 路径解析在
        // /detection/start 拼装时由 Monitor 完成).
        models: (() => {
          const out = [];
          // main spec (永远存在, 仅承载 display_color 等可视化字段)
          out.push({
            name: 'main',
            model_id: activeProject.value.default_model_id ?? null,
            model_name: activeProject.value.model_name || '',
            display_color: activeProject.value.main_display_color || '#10b981',
            priority: 100,
          });
          for (const e of (activeProject.value.extra_models || [])) {
            if (!e || !e.name) continue;
            // c1+: schedule_events (UI 用 ev.id 数字 / 事件名字符串) → 后端格式
            // 数字 id → 'event_<id>' (router 投递的 key 格式), 字符串原样.
            const eventsOut = Array.isArray(e.schedule_events)
              ? e.schedule_events
                  .map(v => {
                    if (typeof v === 'number') return `event_${v}`;
                    return String(v);
                  })
                  .filter(Boolean)
              : [];
            const slot = {
              name: String(e.name).trim(),
              model_id: e.model_id ?? null,
              model_name: e.model_name || '',
              model_version: e.model_version || '',
              model_format: e.model_format || 'pytorch_fp32',  // v3.7.x: 副模型推理格式, Monitor 启动检测时 resolve-path 用
              conf: typeof e.conf === 'number' ? e.conf : 0.25,
              iou: typeof e.iou === 'number' ? e.iou : 0.45,
              roi: Array.isArray(e.roi) && e.roi.length >= 3 ? e.roi : null,
              schedule: {
                type: e.schedule_type || 'every_frame',
                n: Math.max(1, Math.floor(Number(e.schedule_n) || 1)),
                events: eventsOut,
              },
              class_filter: Array.isArray(e.class_filter) && e.class_filter.length
                ? [...e.class_filter] : null,
              priority: typeof e.priority === 'number' ? e.priority : 50,
              display_color: e.display_color || '#f59e0b',
              use_half: !!e.use_half,
              // available_labels 是 UI-only 字段, 不写后端
            };
            out.push(slot);
          }
          return out;
        })(),
      }
    };
    data.data_config = {
      ...(activeProject.value.data_config || {}),
      shift_split_enabled: activeProject.value.shift_split_enabled || false,
      day_shift_start: activeProject.value.day_shift_start || '08:00',
      night_shift_start: activeProject.value.night_shift_start || '20:00',
    };
    await updateProject(activeProject.value.id, data);
    
    activeProject.value.data_config = data.data_config;
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
  // Step 8: 副模型选择分支 (extraModelSelectingIdx >= 0)
  if (extraModelSelectingIdx.value >= 0) {
    const idx = extraModelSelectingIdx.value;
    const slot = activeProject.value?.extra_models?.[idx];
    if (slot) {
      slot.model_id = model.id;
      slot.model_name = model.name;
      slot.model_version = model.version || '';
      // d2: 选完副模型, 把 model.labels 解出存到 slot.available_labels
      // 让 class_filter 下拉能显示候选 (留空 + allow-create 仍兼容).
      let labels = [];
      if (model.labels) {
        try {
          labels = typeof model.labels === 'string' ? JSON.parse(model.labels) : model.labels;
        } catch (e) {
          console.warn('副模型标签解析失败:', e);
        }
      }
      slot.available_labels = Array.isArray(labels) ? labels : [];
      const labelHint = slot.available_labels.length
        ? `, 候选 ${slot.available_labels.length} 个类别`
        : '';

      // v3.7.2 (FIX-381-C): 副模型标签也写进 steps_config, 让客户能像主模型一样
      // 给副模型识别出的 label 单独配 threshold/min_duration/ROI/box_color 等.
      // 后端按 label 索引一视同仁, 此处只补前端的 UI 入口.
      // 默认不自动加进 sequence_order — 客户在「逻辑设置」里手动决定是否参与序列.
      let appended = 0;
      if (slot.available_labels.length && Array.isArray(activeProject.value?.steps_config)) {
        const sc = activeProject.value.steps_config;
        const existing = new Set(sc.map(s => s && s.label).filter(Boolean));
        const usedIds = sc.map(s => s && s.id).filter(Number.isFinite);
        let nextId = (usedIds.length ? Math.max(...usedIds) : 0) + 1;
        for (const lbl of slot.available_labels) {
          if (!lbl || existing.has(lbl)) continue;
          sc.push({
            id: nextId++,
            label: lbl,
            displayLabel: lbl,
            enabled: true,
            roi: null,
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
            tracking_position_lock: false,
            count_mode: 'track',
            event_required_count: 1,
            event_gone_frames: 8,
            box_color: slot.display_color || '',
            from_model: slot.name,
          });
          appended += 1;
        }
      }
      const appendHint = appended > 0
        ? `, 已把 ${appended} 个新标签加进「步骤设置」`
        : (slot.available_labels.length ? ', 标签均已在步骤里' : '');
      ElMessage.success(
        `副模型 [${slot.name}] 已选: ${model.name}${labelHint}${appendHint}`
      );
    }
    extraModelSelectingIdx.value = -1;
    showModelSelect.value = false;
    return;
  }

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
      roi: null,
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
      tracking_position_lock: false,
      count_mode: 'track',
      event_required_count: 1,
      event_gone_frames: 8,
      box_color: '',
      from_model: 'main',
    }));
    
    // 自动初始化顺序
    activeProject.value.sequence_order = labels.map((_, idx) => ({ step_id: idx + 1 }));
    activeProject.value.detection_steps = labels.map((_, idx) => idx + 1);
    
    ElMessage.success(`已选择模型: ${model.name}，识别到 ${labels.length} 个类别`);
  } else {
    ElMessage.warning(`已选择模型: ${model.name}，但未能解析到标签`);
  }
  
  showModelSelect.value = false;
  openFormatSelect();
};

// Step 8 (feat/multi-model-roi-link): 副模型管理方法
const generateExtraModelDefaultName = () => {
  const existing = new Set((activeProject.value?.extra_models || []).map(m => m.name));
  if (!existing.has('aux')) return 'aux';
  for (let i = 2; i < 100; i++) {
    const candidate = `aux${i}`;
    if (!existing.has(candidate)) return candidate;
  }
  return `aux_${Date.now() % 10000}`;
};

const EXTRA_MODEL_PALETTE = ['#f59e0b', '#3b82f6', '#a855f7', '#ec4899', '#14b8a6', '#facc15'];

const addExtraModel = () => {
  if (!activeProject.value) return;
  if (!activeProject.value.extra_models) activeProject.value.extra_models = [];
  const list = activeProject.value.extra_models;
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

const removeExtraModel = async (idx) => {
  if (!activeProject.value?.extra_models) return;
  const slot = activeProject.value.extra_models[idx];
  if (!slot) return;

  // v3.7.x (FIX): 删 slot 时同步清理它在 steps_config 里带进来的 label.
  // 之前只 splice extra_models, 副模型的 step (from_model === slot.name) 仍留在
  // 步骤详情, 客户感知"副模型删了, 标签还在", 还可能因为这些孤儿 step 残留在
  // sequence_order 里干扰检测判定 (例如把"压墨"当结算步骤).
  const slotName = slot.name;
  const orphanSteps = (activeProject.value.steps_config || [])
    .filter(s => s && s.from_model === slotName);

  if (orphanSteps.length > 0) {
    const labelList = orphanSteps
      .map(s => `· ${s.displayLabel || s.label}`)
      .join('\n');
    try {
      await ElMessageBox.confirm(
        `副模型 [${slotName}] 带进来的以下 ${orphanSteps.length} 个步骤标签也会一并删除:\n\n${labelList}\n\n确定继续吗?`,
        '删除副模型',
        {
          confirmButtonText: '一起删除',
          cancelButtonText: '取消',
          type: 'warning',
          customStyle: { whiteSpace: 'pre-wrap' },
        }
      );
    } catch (_e) {
      return;  // 用户取消
    }

    const orphanIds = new Set(orphanSteps.map(s => s.id));
    // 复用 onStepEnabledChange 的"禁用清理" — 走 sequence_order / detection_steps /
    // custom_sequence_order / custom_detection_steps / custom_conditions.sequence
    // 全套清理, 避免漏一处导致 sequence_order 里 step_id 找不到对应 step (鬼步骤).
    for (const step of orphanSteps) {
      try { onStepEnabledChange(step, false); } catch (_e) { /* 单条失败继续 */ }
    }
    // 再从 steps_config 里彻底删除 (onStepEnabledChange 只动序列, 不删步骤本身)
    activeProject.value.steps_config = (activeProject.value.steps_config || [])
      .filter(s => !orphanIds.has(s.id));
  }

  activeProject.value.extra_models.splice(idx, 1);
  ElMessage.success(
    orphanSteps.length > 0
      ? `副模型 [${slotName}] 及其 ${orphanSteps.length} 个步骤标签已删除`
      : `副模型 [${slotName}] 已删除`
  );
};

const openExtraModelSelect = (idx) => {
  extraModelSelectingIdx.value = idx;
  showModelSelect.value = true;
};

// d1: 改用 nextTick + await loadRoiSnapshot, 取代裸 setTimeout(200) + setTimeout(250) 嵌套.
// 慢机器/慢摄像头快照下也能保证 polygon 在 image 加载完成后立刻被画.
const openExtraModelRoiEditor = async (idx) => {
  stepRoiEditingStepId.value = null;
  extraModelRoiEditingIdx.value = idx;
  // 复用主 ROI 编辑器: 把当前副模型的 roi 加载为初始 polygon
  roiPoints.value = [];
  roiPolygonClosed.value = false;
  roiMousePos = null;
  roiEditorVisible.value = true;
  await nextTick();  // 等 dialog DOM 挂载, canvas ref 就绪
  const ok = await loadRoiSnapshot();  // 真正等到 image.onload (或 onerror 兜底)
  if (!ok) return;  // 快照加载失败时画兜底文字, polygon 没意义不画
  const slot = activeProject.value?.extra_models?.[idx];
  const existing = slot?.roi;
  if (Array.isArray(existing) && existing.length >= 3 && roiEditorCanvas.value) {
    const w = roiEditorCanvas.value.width || 1;
    const h = roiEditorCanvas.value.height || 1;
    roiPoints.value = existing.map(([nx, ny]) => ({ x: nx * w, y: ny * h }));
    roiPolygonClosed.value = true;
    roiRedraw();
  }
};

const clearExtraModelRoi = (idx) => {
  const slot = activeProject.value?.extra_models?.[idx];
  if (slot) slot.roi = null;
};

const openStepRoiEditor = async (step) => {
  extraModelRoiEditingIdx.value = -1;
  stepRoiEditingStepId.value = step.id;
  roiPoints.value = [];
  roiPolygonClosed.value = false;
  roiMousePos = null;
  roiEditorVisible.value = true;
  await nextTick();
  const ok = await loadRoiSnapshot();
  if (!ok) return;
  const existing = step.roi;
  if (Array.isArray(existing) && existing.length >= 3 && roiEditorCanvas.value) {
    const w = roiEditorCanvas.value.width || 1;
    const h = roiEditorCanvas.value.height || 1;
    roiPoints.value = existing.map(([nx, ny]) => ({ x: nx * w, y: ny * h }));
    roiPolygonClosed.value = true;
    roiRedraw();
  }
};

const clearStepRoi = (step) => {
  if (step) step.roi = null;
};

const FORMAT_DISPLAY_NAMES = {
  'pytorch_fp32': 'PyTorch FP32',
  'pytorch_fp16': 'PyTorch FP16',
  'onnx': 'ONNX',
  'torchscript': 'TorchScript',
  'tensorrt_fp32': 'TensorRT FP32',
  'tensorrt_fp16': 'TensorRT FP16',
  'tensorrt_int8': 'TensorRT INT8',
};

const getFormatDisplayName = (key) => FORMAT_DISPLAY_NAMES[key] || key;

const openFormatSelect = async () => {
  formatSelectingExtraIdx.value = null;  // v3.7.x: 主模型上下文
  try {
    const res = await getAvailableFormats();
    formatList.value = res.data.formats || [];
    recommendedFormat.value = res.data.recommended || 'pytorch_fp32';
    gpuName.value = res.data.gpu_name || '';
    convertingFormat.value = null;
    conversionId.value = null;
    showFormatSelect.value = true;
  } catch (e) {
    ElMessage.error('获取可用格式失败');
    console.error(e);
  }
};

// v3.7.x: 副模型切换格式入口. 复用同一 dialog, 通过 formatSelectingExtraIdx 区分上下文.
// 后端 /models/{id}/convert 对主/副模型完全通用 — 只是按 model_id 转换文件,
// 区别只在前端写回哪个 .model_format 字段.
const openExtraModelFormatSelect = async (idx) => {
  const slot = activeProject.value?.extra_models?.[idx];
  if (!slot || !slot.model_id) {
    ElMessage.warning('请先选择副模型');
    return;
  }
  formatSelectingExtraIdx.value = idx;
  try {
    const res = await getAvailableFormats();
    formatList.value = res.data.formats || [];
    recommendedFormat.value = res.data.recommended || 'pytorch_fp32';
    gpuName.value = res.data.gpu_name || '';
    convertingFormat.value = null;
    conversionId.value = null;
    showFormatSelect.value = true;
  } catch (e) {
    ElMessage.error('获取可用格式失败');
    console.error(e);
  }
};

// v3.7.x: 根据 formatSelectingExtraIdx 决定回写到主项目还是副 slot.
//   null  → activeProject.model_format       (主模型)
//   0..3  → extra_models[idx].model_format   (副模型 slot)
const _getFormatTargetSlot = () => {
  const idx = formatSelectingExtraIdx.value;
  if (idx === null || idx === undefined) return null;
  return activeProject.value?.extra_models?.[idx] || null;
};
const _writeFormat = (key) => {
  const slot = _getFormatTargetSlot();
  if (slot) slot.model_format = key;
  else activeProject.value.model_format = key;
};
const _getConvertModelId = () => {
  const slot = _getFormatTargetSlot();
  if (slot) return slot.model_id;
  return activeProject.value?.default_model_id;
};

const selectFormat = async (fmt) => {
  if (!fmt.available) return;
  const key = fmt.key;

  if (key === 'pytorch_fp32') {
    _writeFormat(key);
    showFormatSelect.value = false;
    ElMessage.success('已选择 PyTorch FP32（原始模型）');
    return;
  }

  const targetId = _getConvertModelId();
  if (!targetId) {
    ElMessage.error('当前对象未配置模型');
    return;
  }

  convertingFormat.value = key;
  try {
    const res = await convertModel(targetId, {
      format: key,
      project_id: activeProject.value.id,
    });
    const conv = res.data;

    if (conv.status === 'ready') {
      _writeFormat(key);
      showFormatSelect.value = false;
      convertingFormat.value = null;
      ElMessage.success(`已选择 ${getFormatDisplayName(key)}`);
      return;
    }

    conversionId.value = conv.id;
    startConversionPolling(key);
  } catch (e) {
    convertingFormat.value = null;
    ElMessage.error('转换请求失败: ' + (e.response?.data?.detail || e.message));
  }
};

const startConversionPolling = (fmtKey) => {
  stopConversionPolling();
  conversionPolling.value = setInterval(async () => {
    try {
      const res = await getConversionStatus(conversionId.value);
      const s = res.data;
      if (s.status === 'ready') {
        stopConversionPolling();
        convertingFormat.value = null;
        _writeFormat(fmtKey);  // v3.7.x: 主/副模型分流写回
        showFormatSelect.value = false;
        ElMessage.success(`${getFormatDisplayName(fmtKey)} 转换完成`);
      } else if (s.status === 'failed') {
        stopConversionPolling();
        convertingFormat.value = null;
        const errMsg = s.error_msg || '未知错误';
        try {
          const diagRes = await getFormatDiagnosis();
          const d = diagRes.data;
          const lines = [
            `错误: ${errMsg}`,
            '',
            `GPU: ${d.gpu_name || '未知'} (${d.gpu_arch || '?'})`,
            `显存: ${d.gpu_memory_total_mb ?? '?'}MB 总计 / ${d.gpu_memory_free_mb ?? '?'}MB 空闲`,
            `CUDA: ${d.cuda_version || '未知'}`,
            `cuDNN: ${d.cudnn_version || '未知'}`,
            `TensorRT: ${d.tensorrt_version || '未安装'}`,
            `兼容性: ${d.tensorrt_compatible ? '正常' : '不兼容'}`,
          ];
          if (d.issues && d.issues.length) {
            lines.push('', '发现问题:');
            d.issues.forEach(i => lines.push(`  • ${i}`));
          }
          lines.push('', '建议: 尝试 PyTorch FP16 格式，或关闭占用 GPU 的程序后重试');
          ElMessageBox.alert(lines.join('\n'), '转换失败 - 环境诊断', {
            confirmButtonText: '知道了',
            customStyle: { whiteSpace: 'pre-wrap', fontFamily: 'monospace' },
          });
        } catch {
          ElMessage.error('转换失败: ' + errMsg);
        }
      }
    } catch (e) {
      console.error('轮询转换状态失败:', e);
    }
  }, 3000);
};

const cancelFormatSelect = () => {
  stopConversionPolling();
  convertingFormat.value = null;
  showFormatSelect.value = false;
  formatSelectingExtraIdx.value = null;  // v3.7.x: 重置上下文, 防止下次主模型切换误判
};

const showDiagnosis = async () => {
  try {
    const res = await getFormatDiagnosis();
    const d = res.data;
    const lines = [
      `GPU: ${d.gpu_name || '未检测到'} (${d.gpu_arch || '?'})`,
      `显存: ${d.gpu_memory_total_mb ?? '?'}MB 总计 / ${d.gpu_memory_free_mb ?? '?'}MB 空闲`,
      `CUDA: ${d.cuda_version || '未知'}`,
      `cuDNN: ${d.cudnn_version || '未知'}`,
      `TensorRT: ${d.tensorrt_version || '未安装'}`,
      `兼容性: ${d.tensorrt_compatible === true ? '✓ 正常' : d.tensorrt_compatible === false ? '✗ 不兼容' : '未知'}`,
    ];
    if (d.issues && d.issues.length) {
      lines.push('', '发现问题:');
      d.issues.forEach(i => lines.push(`  • ${i}`));
    } else {
      lines.push('', '✓ 未发现兼容性问题');
    }
    ElMessageBox.alert(lines.join('\n'), 'GPU 环境诊断', {
      confirmButtonText: '关闭',
      customStyle: { whiteSpace: 'pre-wrap', fontFamily: 'monospace' },
    });
  } catch (e) {
    ElMessage.error('获取诊断信息失败');
  }
};

// 顺序模式 - 步骤操作
const addSequenceStep = () => {
  if (!activeProject.value.sequence_order) activeProject.value.sequence_order = [];
  activeProject.value.sequence_order.push({ step_id: null });
};

const removeSequenceStep = (idx) => {
  activeProject.value.sequence_order.splice(idx, 1);
};

// v3.7.x (FIX): isSettlementStep 此前硬编码读 sequence_order, 在 custom 模式
// (custom_based_on='sequential') 下完全错: 后端实际用 custom_sequence_order
// (见 backend/api/source_sequence_labels.py:_sequence_order), 而前端 UI 却
// 用残留的旧 sequence_order 算"结算步骤". 后果: UI 把不在自定义顺序里的步骤
// (如残留的"压墨") 错标成结算步骤, 禁用它的严格顺序/单次接受开关, 客户感知为
// "压墨怎么算最后一步".
// 修复: 与后端 _sequence_order() 对齐 — custom + custom_based_on=sequential
// 时读 custom_sequence_order, 其余走 sequence_order.
const _activeSequenceOrder = () => {
  const ap = activeProject.value;
  if (!ap) return [];
  if (ap.logic_mode === 'custom' && ap.custom_based_on === 'sequential') {
    return ap.custom_sequence_order || [];
  }
  return ap.sequence_order || [];
};

const isSettlementStep = (step) => {
  const mode = activeProject.value?.settlement_mode || 'first_step';
  const logicMode = activeProject.value?.logic_mode;
  if (logicMode !== 'sequential' && !(logicMode === 'custom' && activeProject.value?.custom_based_on === 'sequential')) {
    return false;
  }
  const seqOrder = _activeSequenceOrder();
  if (seqOrder.length === 0) return false;
  if (mode === 'first_step') {
    return step.id === seqOrder[0]?.step_id;
  } else if (mode === 'last_step') {
    return step.id === seqOrder[seqOrder.length - 1]?.step_id;
  }
  return false;
};

watch(() => activeProject.value?.logic_mode, (mode) => {
  if (!activeProject.value?.steps_config) return;
  if (mode === 'detection') {
    for (const step of activeProject.value.steps_config) {
      step.accept_once = true;
    }
  }
});

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

// v3.5.0: 周期性强制动作
const addPeriodicAction = () => {
  if (!activeProject.value.periodic_actions) activeProject.value.periodic_actions = [];
  activeProject.value.periodic_actions.push({
    id: `pa_${Date.now()}_${activeProject.value.periodic_actions.length}`,
    name: `规则 ${activeProject.value.periodic_actions.length + 1}`,
    enabled: true,
    trigger_step_ids: [],
    interval: 20,
    count_basis: 'good_only',
    reset_policy: 'always',
    due_warning_event_id: null,
    overdue_event_id: null,
    overdue_repeat: 'every_cycle',
    channel_filter: null,
    run_on_start: false,
  });
};

// v3.5.2: 在 periodic_actions 里直接快捷新建事件并绑定到规则
const addEventAndBindToRule = (rule, fieldKey) => {
  if (!activeProject.value.events_config) activeProject.value.events_config = [];
  const newId = Date.now();
  const defaultName = fieldKey === 'due_warning_event_id'
    ? `${rule.name || '保养'}-到期提醒`
    : `${rule.name || '保养'}-超期告警`;
  activeProject.value.events_config.push({
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

const removePeriodicAction = (idx) => {
  activeProject.value.periodic_actions.splice(idx, 1);
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
