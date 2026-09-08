<template>
  <TjSlot name="project.layout.body">
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
        <!-- Project List（已外置 ProjectListPanel.vue，2026-08 拆分批次） -->
        <ProjectListPanel
          :projects="projects"
          :loading="loading"
          :active-project="activeProject"
          :active-tab="activeTab"
          @select="selectProject" />

        <!-- Logic Mode Context (Shows when Logic Tab is active)（已外置 LogicModeContextPanel.vue，2026-08 拆分批次） -->
        <LogicModeContextPanel v-if="activeProject && activeTab === 'logic'" :project="activeProject" />

        <!-- Counters Context (Shows when Events Tab is active)（已外置 CountersContextPanel.vue，2026-08 拆分批次） -->
        <CountersContextPanel
          v-if="activeProject && activeTab === 'events'"
          :project="activeProject"
          :default-counters="defaultCounters"
          :custom-counters="customCounters" />
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
            <el-button size="small" plain @click="handleDuplicateProject" :loading="duplicating">复制</el-button>
            <el-button type="primary" size="small" @click="handleSaveProject" :loading="saving">保存配置</el-button>
          </div>
        </div>

        <!-- Tabs -->
        <el-tabs v-model="activeTab" class="flex-1 flex flex-col project-tabs">
          
          <!-- Tab 1: Basic Settings -->
          <el-tab-pane label="基础设置" name="basic">
            <!-- 基础设置 Tab 已外置（2026-08 拆分批次） -->
            <BasicSettingsTab
              :project="activeProject"
              @open-format-select="openFormatSelect"
              @open-model-select="showModelSelect = true"
              @open-extra-model-format-select="openExtraModelFormatSelect"
              @open-extra-model-select="openExtraModelSelect"
              @open-extra-model-roi-editor="openExtraModelRoiEditor"
              @remove-extra-model="removeExtraModel" />
          </el-tab-pane>

          <!-- Tab 2: Step Settings -->
          <el-tab-pane :label="activeProject.logic_mode === 'tracking' ? '物品设置' : '步骤设置'" name="steps">
            <!-- 步骤/物品设置 Tab 已外置（2026-07 拆分批次 P-5） -->
            <StepsConfigTab
              :project="activeProject"
              :consecutive-dup-step-ids="consecutiveDupStepIds"
              :is-settlement-step="isSettlementStep"
              @open-step-roi-editor="openStepRoiEditor"
              @open-label-split-editor="openLabelSplitEditor"
              @delete-label-split="handleDeleteLabelSplit"
              @open-placement-guide-editor="openPlacementGuideEditor" />
          </el-tab-pane>

          <!-- Tab 3: Logic Settings -->
          <!-- ==================== 称重投料模式专属配置（面板已外置 WeighingConfigTab.vue） ==================== -->
          <!-- v3.35: 融合模式（顺序 SOP + 步骤外设门控 drive_mode='step_gate'）也需要秤参数/
               型号标准量/视觉防错配置, 同一页签复用, 不加新页面 -->
          <el-tab-pane
            v-if="activeProject.logic_mode === 'weighing' || isStepGateFusion"
            label="称重配置"
            name="weighing">
            <WeighingConfigTab :project="activeProject"
              @open-guard-roi-editor="openGuardRoiEditor"
              @open-pipeline-roi-editor="openPipelineRoiEditor" />
          </el-tab-pane>

          <el-tab-pane label="逻辑设置" name="logic">
            <!-- 逻辑设置 Tab 已外置（2026-07 拆分批次 P-4） -->
            <LogicConfigTab
              :project="activeProject"
              @sequence-step-pick="onSequenceStepPick"
              @mix-type-change="onCustomMixTypeChange"
              @open-roi-editor="openRoiEditor"
              @open-region-roi-editor="openRegionRoiEditor"
              @open-region-point-editor="openRegionPointEditor"
              @open-ai-roi-editor="openAiRoiEditor" />
          </el-tab-pane>

          <!-- 装箱清点 Tab：仅自定义模式 × 混合跟踪时出现（信息架构重构：原逻辑设置内嵌块外置） -->
          <el-tab-pane
            v-if="activeProject.logic_mode === 'custom' && activeProject.custom_mixed_with === 'tracking'"
            label="装箱清点"
            name="mixbox">
            <CustomMixBoxTab :project="activeProject" />
          </el-tab-pane>

          <!-- Tab 4: Events Settings -->
          <el-tab-pane label="事件设置" name="events">
            <!-- 事件设置 Tab 已外置（2026-07 拆分批次 P-3） -->
            <EventsConfigTab
              :project="activeProject"
              :default-counters="defaultCounters"
              :custom-counters="customCounters" />
          </el-tab-pane>

          <!-- v3.13 M2.2b: 客户插件可注入项目配置 Tab -->
          <el-tab-pane
            v-for="tab in pluginProjectTabs"
            :key="tab.key"
            :label="tab.label"
            :name="`plugin-${tab.key}`"
          >
            <TjSlot
              :name="`project.tab.${tab.key}`"
              :tab="tab"
              :project="activeProject"
            >
              <component
                v-if="tab.component"
                :is="tab.component"
                :project="activeProject"
              />
              <div v-else class="text-gray-400 p-4">
                插件未提供 Tab 组件
              </div>
            </TjSlot>
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
    <!-- 四个对话框已外置为独立组件（2026-07 拆分批次 P-2） -->
    <CreateProjectDialog
      v-model:visible="createDialogVisible"
      :form="newProjectForm"
      :creating="creating"
      @create="handleCreateProject" />

    <ModelSelectDialog
      v-model:visible="showModelSelect"
      :project="activeProject"
      :extra-idx="extraModelSelectingIdx"
      :models="modelList"
      :loading="loadingModels"
      @select="selectModel"
      @close="extraModelSelectingIdx = -1" />

    <FormatSelectDialog
      v-model:visible="showFormatSelect"
      :project="activeProject"
      :extra-idx="formatSelectingExtraIdx"
      :converting="convertingFormat"
      :formats="formatList"
      :gpu-name="gpuName"
      :recommended="recommendedFormat"
      @select="selectFormat"
      @cancel="cancelFormatSelect"
      @diagnose="showDiagnosis" />

    <RoiEditorDialog
      ref="roiEditorDialogRef"
      v-model:visible="roiEditorVisible"
      :title="roiEditorDialogTitle"
      :mode="roiEditorMode"
      @save="handleRoiSave"
      @save-point="handleRoiSavePoint"
      @save-rect="handleRoiSaveRect"
      @close="resetRoiEditorTargets" />

    <LabelSplitDialog
      ref="labelSplitDialogRef"
      v-model:visible="labelSplitDialogVisible"
      :model-labels="activeProject?.model_labels || []"
      @save="handleLabelSplitSave" />

  </div>
  </TjSlot>
</template>

<script setup>
import TjSlot from '@/components/TjSlot.vue';
import WeighingConfigTab from './WeighingConfigTab.vue';
import EventsConfigTab from './EventsConfigTab.vue';
import LogicConfigTab from './LogicConfigTab.vue';
import CustomMixBoxTab from './CustomMixBoxTab.vue';
import StepsConfigTab from './StepsConfigTab.vue';
import { ensureMixItemDefaults } from './mixItemDefaults';
import { applyStepEnabledChange } from './stepEnabled';
import CreateProjectDialog from './CreateProjectDialog.vue';
import ModelSelectDialog from './ModelSelectDialog.vue';
import FormatSelectDialog from './FormatSelectDialog.vue';
import RoiEditorDialog from './RoiEditorDialog.vue';
import LabelSplitDialog from './LabelSplitDialog.vue';
// 2026-08 拆分批次: 项目列表卡 / 两个侧栏上下文卡 / 基础设置 Tab 外置
import ProjectListPanel from './ProjectListPanel.vue';
import LogicModeContextPanel from './LogicModeContextPanel.vue';
import CountersContextPanel from './CountersContextPanel.vue';
import BasicSettingsTab from './BasicSettingsTab.vue';
import { createDefaultSplitRule, validateSplitRules, syncSplitVirtualSteps } from './labelSplit';
import { getFormatDisplayName } from './modelFormats';
import { ref, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue';
import { Plus, EditPen, FolderAdd, Check } from '@element-plus/icons-vue';
import { useProjectStore } from '@/store/useProjectStore';
import { useSystemStore } from '@/store/useSystemStore';
import { usePluginThemeStore } from '@/store/usePluginThemeStore';
import { ElMessage, ElMessageBox } from 'element-plus';
import { getProjects, getProjectDetail, createProject, updateProject, deleteProject, duplicateProject, activateProject } from '@/api/project';
import { getModels, getAvailableFormats, convertModel, getConversionStatus, getFormatDiagnosis } from '@/api/model';
import { getBackendHost } from '@/api/index';
import { setProjectConfig, getWorkstations } from '@/api/detection';
import { dbg, dbgErr } from '@/utils/debug';

const projectStore = useProjectStore();
const systemStore = useSystemStore();
const pluginThemeStore = usePluginThemeStore();

// v3.13 M2.2b: 客户插件注入的项目配置 Tab 列表
const pluginProjectTabs = computed(() => pluginThemeStore.projectTabs || []);
// hasDurationsSlot（插件"耗时统计"列探测）已随步骤设置 Tab 外置到 StepsConfigTab.vue（P-5）。
// searchQuery / filteredProjects / formatDate 已随项目列表卡外置到 ProjectListPanel.vue（2026-08）。
const activeProject = ref(null);
const activeTab = ref('basic');

// 「装箱清点」条件 Tab 消失（切项目/改模式）时避免 activeTab 悬空
watch(
  () => activeProject.value
    && activeProject.value.logic_mode === 'custom'
    && activeProject.value.custom_mixed_with === 'tracking',
  (visible) => {
    if (!visible && activeTab.value === 'mixbox') activeTab.value = 'logic';
  }
);
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
const duplicating = ref(false);
const creating = ref(false);
const loadingModels = ref(false);

const newProjectForm = ref({ 
  name: '', 
  task_type: 'detection', 
  logic_mode: 'sequential'
});

// enabledSteps / nonBackupSteps / countableSteps / availableLabels 已随逻辑设置 Tab
// 外置到 LogicConfigTab.vue（P-4, 仅该 Tab 使用）。

// isCustomMixed / stepBehaviorRows / mixItemRows / logicModeLabel / onDetectRoleChange /
// hasDurationsSlot 已随步骤设置 Tab 外置到 StepsConfigTab.vue（P-5）；
// 物品行默认值注入抽 ./mixItemDefaults.js（本文件混合类型切换回调与表A角色切换共用）。

// 切换混合类型时，给已有物品行补齐新类型的原生字段（旧类型字段保留，切回不丢）
// LogicConfigTab 的 mix-type-change emit 处理器, 留父级。
const onCustomMixTypeChange = () => {
  (activeProject.value?.steps_config || []).forEach(s => {
    if (s.detect_role === 'item') ensureMixItemDefaults(activeProject.value, s);
  });
};

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

// availableLabels 已随逻辑设置 Tab 外置（见上方 P-4 注记）。

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
  // v3.8.x: 不再用 `if (res.data?.detection_config)` 守门 — DB null 也要进 store,
  // store 会自动用 localStorage 兜底并回写 DB.
  if (projectStore.currentProjectId) {
    try {
      const res = await getProjectDetail(projectStore.currentProjectId);
      systemStore.setCurrentProjectId(projectStore.currentProjectId);
      systemStore.loadDetectionFromProject(res.data?.detection_config || null, projectStore.currentProjectId);
    } catch (e) {
      console.error('加载项目检测配置失败:', e);
    }
  }
});

onBeforeUnmount(() => {
  stopConversionPolling();
});

// getLabelsCount 已随模型选择对话框外置到 ModelSelectDialog.vue（P-2）

// ======================== ROI Polygon Editor ========================
// 画布交互（取快照/画点/闭合/重绘）已外置到 RoiEditorDialog.vue（2026-07 拆分批次 P-2）；
// 父级只保留「编辑目标路由」：打开时解析通道 + 预加载已有多边形，保存时按目标写回。
const roiEditorVisible = ref(false);
const roiEditorDialogRef = ref(null);

// Step 8 (feat/multi-model-roi-link): 模型选择 / ROI 编辑 的目标 idx.
// = -1 表示主模型 (写到 default_model_id / tracking_roi_polygon, 老路径)
// >= 0 表示副模型 idx (写到 extra_models[idx])
const extraModelSelectingIdx = ref(-1);
const extraModelRoiEditingIdx = ref(-1);
// 步骤 ROI 编辑: 指向 steps_config 中某项的 id (与副模型/全局 tracking_roi 互斥)
const stepRoiEditingStepId = ref(null);
// v3.32 工件就位提示: 引导框多边形复用同一个 ROI 编辑器 (与上面两个目标互斥)
const placementGuideEditing = ref(false);
// v3.32+ 区域事件模式: 规则判定区域, 指向 pipeline_config.region_events.rules 下标 (同样互斥)
const regionRuleEditingIdx = ref(-1);

// v3.35 视觉料源防错: 守卫规则区域, 指向 pipeline_config.weighing.visual_guard.rules 下标 (互斥)
const guardRuleEditingIdx = ref(-1);

// v3.39 流水线称重: 秤台区 (标签"工件上秤"有效域) 编辑标记 (互斥)
const pipelineZoneEditing = ref(false);

// 2026-09 facing_dwell 仪表点标定: 指向 region_events.rules 下标 (point 模式, 互斥)
const regionPointEditingIdx = ref(-1);
// 2026-09 OCR 读字区 / 异常监测区: {kind:'ocr', rIdx} 或 {kind:'anomaly'} (rect 模式, 互斥)
const aiRoiEditing = ref(null);

// 对话框绘制模式: 仪表点=单点, OCR/异常=矩形, 其余=多边形 (历史行为)
const roiEditorMode = computed(() => {
  if (regionPointEditingIdx.value >= 0) return 'point';
  if (aiRoiEditing.value) return 'rect';
  return 'polygon';
});

const resetRoiEditorTargets = () => {
  extraModelRoiEditingIdx.value = -1;
  stepRoiEditingStepId.value = null;
  placementGuideEditing.value = false;
  regionRuleEditingIdx.value = -1;
  guardRuleEditingIdx.value = -1;
  pipelineZoneEditing.value = false;
  regionPointEditingIdx.value = -1;
  aiRoiEditing.value = null;
};

// v3.35 融合模式判定: 顺序 SOP + 步骤外设门控 (称重配置页签的第二种出现条件)
const isStepGateFusion = computed(() =>
  activeProject.value?.pipeline_config?.weighing?.drive_mode === 'step_gate');

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
  if (placementGuideEditing.value) {
    return '绘制工件就位引导框（锚点目标中心进框 = 已就位）';
  }
  if (regionRuleEditingIdx.value >= 0) {
    const rule = ap?.pipeline_config?.region_events?.rules?.[regionRuleEditingIdx.value];
    return `绘制区域事件规则 [${rule?.name || `规则 ${regionRuleEditingIdx.value + 1}`}] 的判定区域`;
  }
  if (guardRuleEditingIdx.value >= 0) {
    const rule = ap?.pipeline_config?.weighing?.visual_guard?.rules?.[guardRuleEditingIdx.value];
    return `绘制料源防错规则 [${rule?.name || `规则 ${guardRuleEditingIdx.value + 1}`}] 的判定区域`;
  }
  if (pipelineZoneEditing.value) {
    return '绘制秤台区（"工件上秤"标签只在此区域内有效）';
  }
  if (regionPointEditingIdx.value >= 0) {
    const rule = ap?.pipeline_config?.region_events?.rules?.[regionPointEditingIdx.value];
    return `标定规则 [${rule?.name || `规则 ${regionPointEditingIdx.value + 1}`}] 的仪表位置（单击一次）`;
  }
  if (aiRoiEditing.value?.kind === 'ocr') {
    const rule = ap?.pipeline_config?.ocr?.rules?.[aiRoiEditing.value.rIdx];
    return `框定 OCR 规则 [${rule?.name || `规则 ${(aiRoiEditing.value.rIdx ?? 0) + 1}`}] 的读字区域`;
  }
  if (aiRoiEditing.value?.kind === 'anomaly') {
    return '框定异常检测监测区域（只评估该区域）';
  }
  return '绘制 ROI 检测区域';
});

// 2026-09 facing_dwell 仪表点: LogicConfigTab 发起, 复用同一个编辑器 (point 模式)
const openRegionPointEditor = async (ruleIdx) => {
  resetRoiEditorTargets();
  regionPointEditingIdx.value = ruleIdx;
  const rule = activeProject.value?.pipeline_config?.region_events?.rules?.[ruleIdx];
  await _openRoiDialog(rule?.target_point || null);
};

// 2026-09 OCR 读字区 / 异常监测区: LogicConfigTab 发起 (rect 模式)
const openAiRoiEditor = async (target) => {
  resetRoiEditorTargets();
  aiRoiEditing.value = target || null;
  let existing = null;
  if (target?.kind === 'ocr') {
    existing = activeProject.value?.pipeline_config?.ocr?.rules?.[target.rIdx]?.roi || null;
  } else if (target?.kind === 'anomaly') {
    existing = activeProject.value?.pipeline_config?.anomaly?.roi || null;
  }
  await _openRoiDialog(existing);
};

// point 模式保存: 写 facing_dwell 规则的 target_point
const handleRoiSavePoint = (point) => {
  if (regionPointEditingIdx.value >= 0 && Array.isArray(point) && point.length >= 2) {
    const rule = activeProject.value?.pipeline_config?.region_events?.rules?.[regionPointEditingIdx.value];
    if (rule) {
      rule.target_point = point;
      ElMessage.success(`规则 [${rule.name || `规则 ${regionPointEditingIdx.value + 1}`}] 仪表点已标定 (${point[0]}, ${point[1]})，记得点右上角"保存配置"落库`);
    }
  }
  resetRoiEditorTargets();
  roiEditorVisible.value = false;
};

// rect 模式保存: 写 OCR 规则 roi / 异常检测 roi
const handleRoiSaveRect = (rect) => {
  const t = aiRoiEditing.value;
  if (t && Array.isArray(rect) && rect.length >= 4) {
    if (t.kind === 'ocr') {
      const rule = activeProject.value?.pipeline_config?.ocr?.rules?.[t.rIdx];
      if (rule) {
        rule.roi = rect;
        ElMessage.success(`OCR 规则 [${rule.name || `规则 ${(t.rIdx ?? 0) + 1}`}] 读字区域已框定，记得点右上角"保存配置"落库`);
      }
    } else if (t.kind === 'anomaly') {
      const a = activeProject.value?.pipeline_config?.anomaly;
      if (a) {
        a.roi = rect;
        ElMessage.success('异常检测监测区域已框定，记得点右上角"保存配置"落库');
      }
    }
  }
  resetRoiEditorTargets();
  roiEditorVisible.value = false;
};

// v3.35 视觉料源防错规则区域: WeighingConfigTab 发起, 复用同一个 ROI 编辑器
const openGuardRoiEditor = async (idx) => {
  resetRoiEditorTargets();
  guardRuleEditingIdx.value = idx;
  const rule = activeProject.value?.pipeline_config?.weighing?.visual_guard?.rules?.[idx];
  await _openRoiDialog(rule?.polygon || null);
};

// v3.39 流水线称重秤台区: WeighingConfigTab 发起, 复用同一个 ROI 编辑器
const openPipelineRoiEditor = async () => {
  resetRoiEditorTargets();
  pipelineZoneEditing.value = true;
  const poly = activeProject.value?.pipeline_config?.weighing?.pipeline?.onscale_polygon;
  await _openRoiDialog(poly || null);
};

// 统一打开入口: 设目标 → 开对话框 → 等 canvas 挂载 → 子组件取快照并预加载已有多边形
const _openRoiDialog = async (existingPolygon = null) => {
  roiEditorVisible.value = true;
  await nextTick();  // 等 dialog DOM 挂载, 子组件 canvas ref 就绪
  const channel = await resolveRoiSnapshotChannel();
  await roiEditorDialogRef.value?.load(channel, existingPolygon);
};

const openRoiEditor = async () => {
  resetRoiEditorTargets();
  await _openRoiDialog();
};

// 2026-07 缺陷 A 修复: ROI 底图不再写死 0 号通道 —— 多工位时按「当前项目绑定在哪个通道」
// 解析快照来源, 否则对着别的相机画 ROI, 画的区域和实际检测画面天然有出入。
// 解析不到绑定 (单工位 / 项目未绑定 / 接口异常) 时回退 0 号, 与历史行为一致。
const resolveRoiSnapshotChannel = async () => {
  try {
    const res = await getWorkstations();
    const cfgs = res.data?.source_configs || {};
    // 只认当前真实存在的通道: 绑定配置可能残留已缩减掉的通道(如单工位模式下残留通道1)
    const alive = new Set((res.data?.channels || []).map(c => c.channel_id));
    const pid = activeProject.value?.id;
    if (pid != null) {
      for (const [ch, cfg] of Object.entries(cfgs)) {
        const n = Number(ch);
        if (cfg && cfg.project_id === pid && alive.has(n)) {
          return n;
        }
      }
    }
  } catch (e) {
    dbgErr('project.config', 'ROI 快照通道解析', e);
  }
  return 0;
};

// 子组件点「保存 ROI」后回传归一化多边形, 这里按编辑目标路由写回
const handleRoiSave = async (polygon) => {
  if (!activeProject.value || !Array.isArray(polygon) || polygon.length < 3) return;
  dbg('project.config', '点击「保存 ROI」', `points=${polygon.length} 目标=${extraModelRoiEditingIdx.value >= 0 ? `副模型#${extraModelRoiEditingIdx.value}` : (stepRoiEditingStepId.value != null ? `步骤#${stepRoiEditingStepId.value}` : '全局tracking')}`);

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

  // v3.32+ 区域事件规则判定区域: 写入 pipeline_config.region_events.rules[idx].region
  if (regionRuleEditingIdx.value >= 0) {
    const rule = activeProject.value?.pipeline_config?.region_events?.rules?.[regionRuleEditingIdx.value];
    if (rule) {
      rule.region = polygon;
      ElMessage.success(`规则 [${rule.name || `规则 ${regionRuleEditingIdx.value + 1}`}] 判定区域已保存 (${polygon.length} 个顶点)，记得点右上角"保存配置"落库`);
    }
    resetRoiEditorTargets();
    roiEditorVisible.value = false;
    return;
  }

  // v3.35 视觉料源防错规则区域: 写入 pipeline_config.weighing.visual_guard.rules[idx].polygon
  if (guardRuleEditingIdx.value >= 0) {
    const rule = activeProject.value?.pipeline_config?.weighing?.visual_guard?.rules?.[guardRuleEditingIdx.value];
    if (rule) {
      rule.polygon = polygon;
      ElMessage.success(`料源防错规则 [${rule.name || `规则 ${guardRuleEditingIdx.value + 1}`}] 判定区域已保存 (${polygon.length} 个顶点)，记得点右上角"保存配置"落库`);
    }
    resetRoiEditorTargets();
    roiEditorVisible.value = false;
    return;
  }

  // v3.39 流水线称重秤台区: 写入 pipeline_config.weighing.pipeline.onscale_polygon
  if (pipelineZoneEditing.value) {
    const pipe = activeProject.value?.pipeline_config?.weighing?.pipeline;
    if (pipe) {
      pipe.onscale_polygon = polygon;
      ElMessage.success(`秤台区已保存 (${polygon.length} 个顶点)，记得点右上角"保存配置"落库`);
    }
    resetRoiEditorTargets();
    roiEditorVisible.value = false;
    return;
  }

  // v3.32 工件就位提示引导框: 写入 pipeline_config.placement_guide.polygon
  if (placementGuideEditing.value) {
    if (!activeProject.value.pipeline_config) activeProject.value.pipeline_config = {};
    if (!activeProject.value.pipeline_config.placement_guide) {
      activeProject.value.pipeline_config.placement_guide = { enabled: true, anchor_label: '', polygon: null, mode: 'hint' };
    }
    activeProject.value.pipeline_config.placement_guide.polygon = polygon;
    ElMessage.success(`就位引导框已保存 (${polygon.length} 个顶点)，记得点右上角"保存配置"落库`);
    resetRoiEditorTargets();
    roiEditorVisible.value = false;
    return;
  }

  activeProject.value.tracking_roi_polygon = polygon;
  roiEditorVisible.value = false;
  // 预览小画布已随逻辑设置 Tab 外置到 LogicConfigTab.vue（P-4）,
  // 其内部 deep watch tracking_roi_polygon, 此处回写后会自动重绘。
  await handleSaveProject();
};

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

// v3.32+ 区域事件模式: 存库前收敛 (丢空规则 / 数值钳位; 后端 parse_region_events 是最终裁判)
const _sanitizeRegionEvents = (re) => {
  const src = re || {};
  const rules = (Array.isArray(src.rules) ? src.rules : [])
    .filter(r => r && (r.name || '').trim() && (r.subject_label || '').trim())
    .map((r, i) => {
      const type = ['region_exit', 'region_enter', 'region_count', 'region_empty', 'proximity', 'cross_count', 'facing_dwell'].includes(r.type) ? r.type : 'overlap';
      const region = Array.isArray(r.region) && r.region.length >= 3 ? r.region : null;
      const out = {
        id: r.id || `r${i + 1}`,
        name: r.name.trim(),
        type,
        subject_label: r.subject_label.trim(),
        region,
        settle: !!r.settle,
        min_frames: Math.max(1, Math.floor(Number(r.min_frames) || (type === 'overlap' ? 10 : 3))),
        event_id: r.event_id ?? null,
      };
      if (type === 'overlap') {
        out.object_label = (r.object_label || '').trim();
        out.region_mode = r.region_mode === 'or' ? 'or' : 'and';
        out.min_iou = Math.max(0, Math.min(0.95, Number(r.min_iou) || 0));
        out.min_overlap_ratio = Math.max(0, Math.min(1, Number(r.min_overlap_ratio) || 0));
        out.object_margin = Math.max(0, Math.min(0.2, Number(r.object_margin) || 0));
        out.require_label = (r.require_label || '').trim() || null;
      } else if (type === 'region_enter') {
        out.require_label = (r.require_label || '').trim() || null;
      } else if (type === 'region_count') {
        out.min_count = Math.max(1, Math.floor(Number(r.min_count) || 3));
      } else if (type === 'proximity') {
        out.object_label = (r.object_label || '').trim();
        out.max_distance = Math.max(0.01, Math.min(1, Number(r.max_distance) || 0.15));
      } else if (type === 'region_exit' || type === 'cross_count') {
        out.gone_frames = Math.max(1, Math.floor(Number(r.gone_frames) || 8));
        out.match_iou = Math.max(0.05, Math.min(0.95, Number(r.match_iou) || 0.3));
        if (type === 'cross_count') out.min_frames = Math.max(1, Math.floor(Number(r.min_frames) || 2));
      } else if (type === 'facing_dwell') {
        // 仪表点独立字段 (单点, 不能塞 region 多边形——会被 >=3 点约束洗掉)
        const pt = Array.isArray(r.target_point) ? r.target_point : null;
        out.target_point = (pt && pt.length >= 2 && Number.isFinite(Number(pt[0])) && Number.isFinite(Number(pt[1])))
          ? [Math.max(0, Math.min(1, Number(pt[0]))), Math.max(0, Math.min(1, Number(pt[1])))]
          : null;
        out.tolerance_deg = Math.max(5, Math.min(180, Number(r.tolerance_deg) || 35));
        out.alert_on_absent = !!r.alert_on_absent;
      }
      // 消失确认秒数 / 位移门槛 / 确认时长秒基 (overlap/enter 可选): >0 才落库
      if (type !== 'region_exit' && type !== 'cross_count') {
        const gs = Number(r.gone_seconds);
        if (Number.isFinite(gs) && gs > 0) out.gone_seconds = Math.min(30, gs);
        const mm = Number(r.min_move);
        if (Number.isFinite(mm) && mm > 0) out.min_move = Math.min(1, mm);
        const ms = Number(r.min_seconds);
        // 上限 3600s: 巡检合规类规则 (如蒸镀点检每 5~10 分钟到位确认) 需要分钟级时长, 后端解析本无上限
        if (Number.isFinite(ms) && ms > 0) out.min_seconds = Math.min(3600, ms);
      }
      // 区域锚点跟随 (可选): 锚点类别 + 标定框齐全才落库, 半截配置直接丢弃
      const a = r.anchor || {};
      if (a.enabled && String(a.label || '').trim() && a.ref && Number(a.ref.w) > 0 && Number(a.ref.h) > 0) {
        out.anchor = {
          enabled: true,
          label: String(a.label).trim(),
          ref: { x: Number(a.ref.x) || 0, y: Number(a.ref.y) || 0, w: Number(a.ref.w), h: Number(a.ref.h) },
          hold_seconds: (() => {
            const v = Number(a.hold_seconds);
            return Number.isFinite(v) && v >= 0 ? Math.min(60, v) : 3.0;
          })(),
        };
      }
      return out;
    })
    // facing_dwell 未标定仪表点 = 半截配置, 后端解析会整体拒载, 存库时直接丢弃
    .filter(r => r.type !== 'facing_dwell' || r.target_point);
  const class_conf = {};
  Object.entries(src.class_conf || {}).forEach(([label, conf]) => {
    const v = Number(conf);
    if ((label || '').trim() && Number.isFinite(v) && v > 0) class_conf[label.trim()] = Math.min(1, v);
  });
  const ruleNames = new Set(rules.map(r => r.name));
  const seqSrc = src.sequence_check || {};
  const order = (Array.isArray(seqSrc.order) ? seqSrc.order : []).filter(n => ruleNames.has(n));
  // 结算判定: 顺序即优先级; 引用了不存在事件名/缺触发事件的行直接剔除
  const settlement_rules = (Array.isArray(src.settlement_rules) ? src.settlement_rules : [])
    .filter(sr => sr && ['exact', 'missing', 'repeated', 'always'].includes(sr.match) && sr.event_id != null)
    .map(sr => {
      if (sr.match === 'exact') {
        const sequence = (Array.isArray(sr.sequence) ? sr.sequence : []).filter(n => ruleNames.has(n));
        return sequence.length ? { match: 'exact', sequence, event_id: sr.event_id } : null;
      }
      if (sr.match === 'always') return { match: 'always', event_id: sr.event_id };
      const target = String(sr.target || '').trim();
      if (!ruleNames.has(target)) return null;
      const out = { match: sr.match, target, event_id: sr.event_id };
      if (sr.match === 'repeated') out.min_count = Math.max(2, Math.floor(Number(sr.min_count) || 2));
      return out;
    })
    .filter(Boolean);
  return {
    enabled: src.enabled !== false,
    class_conf,
    gap_tolerance_frames: Math.max(0, Math.floor(Number(src.gap_tolerance_frames) || 0)),
    dedup_consecutive: src.dedup_consecutive !== false,
    rules,
    settlement_rules,
    sequence_check: {
      enabled: !!seqSrc.enabled && order.length > 0,
      order,
      event_id: seqSrc.event_id ?? null,
    },
  };
};

// 2026-09 OCR 读字模式: 存库前收敛 (丢空规则 / 数值钳位; 后端 parse_ocr_config 是最终裁判)
const _sanitizeOcrConfig = (o) => {
  const src = o || {};
  const rules = (Array.isArray(src.rules) ? src.rules : [])
    .filter(r => r && (r.name || '').trim())
    .map((r, i) => {
      const roi = Array.isArray(r.roi) && r.roi.length >= 4
        && Number(r.roi[2]) > 0 && Number(r.roi[3]) > 0
        ? r.roi.slice(0, 4).map(Number) : null;
      return {
        id: r.id || `ocr_${i + 1}`,
        name: r.name.trim(),
        roi,
        pattern: String(r.pattern || '').trim(),
        ok_event_id: r.ok_event_id ?? 1,
        ng_event_id: r.ng_event_id ?? 2,
        on_change_only: r.on_change_only !== false,
        settle: r.settle !== false,
      };
    });
  return {
    interval_s: Math.max(0.2, Math.min(3600, Number(src.interval_s) || 2.0)),
    min_score: Math.max(0.05, Math.min(1, Number(src.min_score) || 0.5)),
    stable_reads: Math.max(1, Math.floor(Number(src.stable_reads) || 2)),
    rules,
  };
};

// 2026-09 异常检测模式: 存库前收敛 (后端 parse_anomaly_config 是最终裁判)
const _sanitizeAnomalyConfig = (a) => {
  const src = a || {};
  const thr = Number(src.threshold);
  const roi = Array.isArray(src.roi) && src.roi.length >= 4
    && Number(src.roi[2]) > 0 && Number(src.roi[3]) > 0
    ? src.roi.slice(0, 4).map(Number) : null;
  return {
    bank_id: String(src.bank_id || '').trim(),
    threshold: Number.isFinite(thr) && thr > 0 ? thr : null,
    interval_s: Math.max(0.2, Math.min(3600, Number(src.interval_s) || 2.0)),
    consecutive: Math.max(1, Math.floor(Number(src.consecutive) || 3)),
    roi,
    ng_event_id: src.ng_event_id ?? 2,
    ok_event_id: src.ok_event_id ?? 1,
    ng_cooldown_s: Math.max(0, Math.min(3600, Number(src.ng_cooldown_s) || 30)),
    recover_ok_event: !!src.recover_ok_event,
  };
};

// 初始化项目默认配置
// ==================== 称重投料模式配置 ====================
// 编辑交互已外置到 WeighingConfigTab.vue（2026-07 拆分批次 P-1）；
// v3.35.1 班次行提示 shiftRangeHint 已随基础设置 Tab 外置到 BasicSettingsTab.vue（2026-08）。
// 父级只保留默认值注入 ensureWeighingDefaults（加载/切模式链路用）。
// 只在称重模式才往项目里注入 weighing 默认配置。非称重项目绝不碰其 pipeline_config，零污染。
const ensureWeighingDefaults = (project) => {
  if (!project) return;
  if (!project.pipeline_config) project.pipeline_config = {};
  const w = project.pipeline_config.weighing || {};
  project.pipeline_config.weighing = {
    materials: Array.isArray(w.materials) && w.materials.length ? w.materials : ['钢帽水泥', '钢脚水泥'],
    models: (w.models && typeof w.models === 'object') ? w.models : {},
    tare_mode: w.tare_mode || 'auto_stable',
    tare_trigger_weight: w.tare_trigger_weight ?? 0.05,
    tare_settle_samples: w.tare_settle_samples ?? 3,
    stable_tol: w.stable_tol ?? 0.003,
    stable_min_samples: w.stable_min_samples ?? 3,
    measure_min_weight: w.measure_min_weight ?? 0.005,
    require_operator: w.require_operator !== false,
    // v3.45 作业员从用户名单选择 (默认关 = 自由填写, 兼容老项目)
    operator_from_users: w.operator_from_users === true,
    require_model: w.require_model !== false,
    material_check: w.material_check || 'sequence',
    auto_zero_after_done: w.auto_zero_after_done !== false,
    alarm_event_shortage: w.alarm_event_shortage ?? 2,
    alarm_event_over: w.alarm_event_over ?? 2,
    alarm_event_wrong: w.alarm_event_wrong ?? 2,
    alarm_event_precheck: w.alarm_event_precheck ?? 2,
    // v3.35 新增: 驱动模式 / 视觉防错报警映射 / 前置选择有效期 / 视觉料源防错
    drive_mode: w.drive_mode || 'scale',
    // v3.35.1 监控页实时称重数值条 (皮重/净重), 可选关闭
    show_monitor_weights: w.show_monitor_weights !== false,
    alarm_event_guard: w.alarm_event_guard ?? 2,
    context_expiry: (w.context_expiry && typeof w.context_expiry === 'object')
      ? { mode: 'never', reset_time: '08:00', shifts: [], hours: 8, expire_fields: ['model'], ...w.context_expiry }
      : { mode: 'never', reset_time: '08:00', shifts: [], hours: 8, expire_fields: ['model'] },
    visual_guard: (w.visual_guard && typeof w.visual_guard === 'object')
      ? { enabled: false, source: 'region_action', rules: [], wrong_block: true, cooldown_sec: 5, ...w.visual_guard }
      : { enabled: false, source: 'region_action', rules: [], wrong_block: true, cooldown_sec: 5 },
    // v3.39 两阶段流水线模式 (秤上称重 + 秤下待收尾) + 秤指令时序参数 (全部可调)
    pipeline: {
      material: '钢帽水泥', label_onscale: '工件上秤', label_fill: '加水泥',
      label_finalize: '加钢脚水泥', onscale_polygon: null,
      tare_min_kg: 0.2, tare_max_kg: 10.0, queue_depth: 2,
      ...((w.pipeline && typeof w.pipeline === 'object') ? w.pipeline : {}),
    },
    timing: {
      tare_trigger_source: 'weight_first', tare_delay_ms: 0,
      tare_stable_ms: 1000, tare_stable_tol_kg: 0.005,
      net_stable_ms: 1500, net_stable_tol_kg: 0.005,
      shortage_alarm_sec: 3, depart_confirm_ms: 500,
      zero_delay_ms: 0, zero_verify_ms: 2000, zero_retry: 1,
      label1_min_frames: 3, label1_fresh_sec: 3,
      label3_min_frames: 5, label3_cooldown_sec: 2,
      fill_timeout_sec: 300, finalize_timeout_sec: 120,
      ...((w.timing && typeof w.timing === 'object') ? w.timing : {}),
    },
    alarm_event_ok: w.alarm_event_ok ?? 1,
    alarm_event_tare_range: w.alarm_event_tare_range ?? 2,
    alarm_event_residue: w.alarm_event_residue ?? 2,
    alarm_event_takt: w.alarm_event_takt ?? 2,
    alarm_event_finalize_timeout: w.alarm_event_finalize_timeout ?? 2,
  };
  const ww = project.pipeline_config.weighing;
  Object.keys(ww.models).forEach(mname => {
    if (!ww.models[mname] || typeof ww.models[mname] !== 'object') ww.models[mname] = {};
    ww.materials.forEach(mat => {
      if (!ww.models[mname][mat]) ww.models[mname][mat] = { standard: 0, low_tol: 0.05, high_tol: 0.05 };
    });
  });
};

// 运行时把项目逻辑模式切到称重 → 才补默认配置 (切回别的模式不删, 但存库时 handleSaveProject 已守门丢弃)
watch(() => activeProject.value?.logic_mode, (mode) => {
  if (mode === 'weighing' && activeProject.value && !activeProject.value.pipeline_config?.weighing) {
    ensureWeighingDefaults(activeProject.value);
  }
  if (mode === 'region_events' && activeProject.value) {
    ensureRegionEventsDefaults(activeProject.value);
  }
  if (mode === 'ocr' && activeProject.value) {
    ensureOcrDefaults(activeProject.value);
  }
  if (mode === 'anomaly' && activeProject.value) {
    ensureAnomalyDefaults(activeProject.value);
  }
});

// 2026-09 OCR 读字模式: 补齐 pipeline_config.ocr 骨架 (字段与后端 parse_ocr_config 对齐)
const ensureOcrDefaults = (project) => {
  if (!project.pipeline_config) project.pipeline_config = {};
  const o = project.pipeline_config.ocr;
  if (!o || typeof o !== 'object') {
    project.pipeline_config.ocr = {
      interval_s: 2.0, min_score: 0.5, stable_reads: 2, rules: [],
    };
    return;
  }
  if (o.interval_s === undefined) o.interval_s = 2.0;
  if (o.min_score === undefined) o.min_score = 0.5;
  if (o.stable_reads === undefined) o.stable_reads = 2;
  if (!Array.isArray(o.rules)) o.rules = [];
  o.rules.forEach(r => {
    if (!r || typeof r !== 'object') return;
    if (r.pattern === undefined) r.pattern = '';
    if (r.roi === undefined) r.roi = null;
    if (r.ok_event_id === undefined) r.ok_event_id = 1;
    if (r.ng_event_id === undefined) r.ng_event_id = 2;
    if (r.on_change_only === undefined) r.on_change_only = true;
    if (r.settle === undefined) r.settle = true;
  });
};

// 2026-09 异常检测模式: 补齐 pipeline_config.anomaly 骨架 (字段与后端 parse_anomaly_config 对齐)
const ensureAnomalyDefaults = (project) => {
  if (!project.pipeline_config) project.pipeline_config = {};
  const a = project.pipeline_config.anomaly;
  if (!a || typeof a !== 'object') {
    project.pipeline_config.anomaly = {
      bank_id: '', threshold: null, interval_s: 2.0, consecutive: 3,
      roi: null, ng_event_id: 2, ok_event_id: 1,
      ng_cooldown_s: 30, recover_ok_event: false,
    };
    return;
  }
  if (a.bank_id === undefined) a.bank_id = '';
  if (a.threshold === undefined) a.threshold = null;
  if (a.interval_s === undefined) a.interval_s = 2.0;
  if (a.consecutive === undefined) a.consecutive = 3;
  if (a.roi === undefined) a.roi = null;
  if (a.ng_event_id === undefined) a.ng_event_id = 2;
  if (a.ok_event_id === undefined) a.ok_event_id = 1;
  if (a.ng_cooldown_s === undefined) a.ng_cooldown_s = 30;
  if (a.recover_ok_event === undefined) a.recover_ok_event = false;
};

// v3.32+ 区域事件模式: 补齐 pipeline_config.region_events 骨架 (字段与后端 parse_region_events 对齐)
const ensureRegionEventsDefaults = (project) => {
  if (!project.pipeline_config) project.pipeline_config = {};
  const re = project.pipeline_config.region_events;
  if (!re || typeof re !== 'object') {
    project.pipeline_config.region_events = {
      enabled: true,
      class_conf: {},
      gap_tolerance_frames: 3,
      dedup_consecutive: true,
      rules: [],
      sequence_check: { enabled: false, order: [], event_id: null },
      settlement_rules: [],
    };
    return;
  }
  if (re.enabled === undefined) re.enabled = true;
  if (!re.class_conf || typeof re.class_conf !== 'object') re.class_conf = {};
  if (re.gap_tolerance_frames === undefined) re.gap_tolerance_frames = 3;
  if (re.dedup_consecutive === undefined) re.dedup_consecutive = true;
  if (!Array.isArray(re.rules)) re.rules = [];
  re.rules.forEach(r => {
    if (r && typeof r === 'object' && r.type !== 'region_exit') {
      if (r.min_move === undefined) r.min_move = 0;
      if (r.min_seconds === undefined) r.min_seconds = 0;
      if (r.gone_seconds === undefined) r.gone_seconds = null;
      if (r.type === 'overlap' && r.min_overlap_ratio === undefined) r.min_overlap_ratio = 0;
    }
  });
  if (!re.sequence_check || typeof re.sequence_check !== 'object') {
    re.sequence_check = { enabled: false, order: [], event_id: null };
  } else {
    if (re.sequence_check.enabled === undefined) re.sequence_check.enabled = false;
    if (!Array.isArray(re.sequence_check.order)) re.sequence_check.order = [];
    if (re.sequence_check.event_id === undefined) re.sequence_check.event_id = null;
  }
  if (!Array.isArray(re.settlement_rules)) re.settlement_rules = [];
};

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
    // v3.10+ Box 尺寸上限 (归一化 0~1, 0 = 关闭过滤)
    if (step.box_max_width === undefined) step.box_max_width = 0;
    if (step.box_max_height === undefined) step.box_max_height = 0;
    // v3.19.x 满盘门: 堆叠模式只验每盘是否数满 (默认关 = 历史"卡总数"行为)
    if (step.stack_gate_only === undefined) step.stack_gate_only = false;
    // v3.50 齐件即结算: 确认放入帧数 (默认 1 = 看见即入账)
    if (step.settle_confirm_frames === undefined) step.settle_confirm_frames = 1;
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
      { id: 1, name: '合格(OK)', color: '#10b981', actions: [{ counter_name: '合格总数', delta: 1 }, { counter_name: '总产量', delta: 1 }], show_notification: true, toast_id: 'ok', require_ack: false, ack_timeout_sec: 0, ack_resets_periodic: false },
      { id: 2, name: '不良(NG)', color: '#ef4444', actions: [{ counter_name: '不良总数', delta: 1 }, { counter_name: '总产量', delta: 1 }], show_notification: true, toast_id: 'ng', require_ack: false, ack_timeout_sec: 0, ack_resets_periodic: false }
    ];
  }
  // 确保每个事件都有 toast_id + v3.9.x 人工确认字段 (老项目兼容)
  project.events_config.forEach((ev, idx) => {
    if (!ev.toast_id) {
      ev.toast_id = idx === 0 ? 'ok' : (idx === 1 ? 'ng' : 'ok');
    }
    if (typeof ev.require_ack !== 'boolean') ev.require_ack = false;
    if (typeof ev.ack_timeout_sec !== 'number') ev.ack_timeout_sec = 0;
    // v3.9.x: 周期性强制动作触发该事件时, 确认是否同步清账规则计数 (默认 false)
    if (typeof ev.ack_resets_periodic !== 'boolean') ev.ack_resets_periodic = false;
    // v3.34: 确认后保留周期 (断点补做, 默认 false = 老"确认重做"语义)
    if (typeof ev.ack_keep_cycle !== 'boolean') ev.ack_keep_cycle = false;
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
  // v3.32 同标签区域拆分 + 工件就位提示 (未配置 = 空数组/关, 行为零差异)
  if (!Array.isArray(project.pipeline_config.label_splits)) {
    project.pipeline_config.label_splits = Array.isArray(pipelineConfig.label_splits)
      ? pipelineConfig.label_splits : [];
  }
  if (!project.pipeline_config.placement_guide || typeof project.pipeline_config.placement_guide !== 'object') {
    const pg = pipelineConfig.placement_guide;
    project.pipeline_config.placement_guide = (pg && typeof pg === 'object')
      ? { enabled: !!pg.enabled, anchor_label: pg.anchor_label || '', polygon: pg.polygon || null, mode: pg.mode || 'hint' }
      : { enabled: false, anchor_label: '', polygon: null, mode: 'hint' };
  }
  // 原生称重投料模式: 仅 weighing 项目才注入默认配置, 非称重项目不碰 (零污染)
  // v3.35: 融合模式 (顺序 SOP + 步骤门控) 也持有 weighing 子树, 同样补默认
  if (project.logic_mode === 'weighing'
      || project.pipeline_config?.weighing?.drive_mode === 'step_gate') {
    ensureWeighingDefaults(project);
  }
  // v3.32+ 区域事件模式: 同款零污染策略
  if (project.logic_mode === 'region_events') {
    ensureRegionEventsDefaults(project);
  }
  // 2026-09 OCR 读字 / 异常检测模式: 同款零污染策略
  if (project.logic_mode === 'ocr') {
    ensureOcrDefaults(project);
  }
  if (project.logic_mode === 'anomaly') {
    ensureAnomalyDefaults(project);
  }
  
  // 使用 pipeline_config 中的值，如果没有则使用默认值
  if (project.sequence_order === undefined) {
    project.sequence_order = pipelineConfig.sequence_order || [];
  }
  // v3.14.x: API/脚本创建的项目常缺 sequence_order → 结算步识别失败, 严格顺序/单次接受开关不禁用
  if (project.logic_mode === 'sequential' && (!project.sequence_order || project.sequence_order.length === 0)) {
    const enabled = (project.steps_config || []).filter(s => s.enabled !== false && !s.is_backup && !s.backup_for);
    if (enabled.length > 0) {
      project.sequence_order = enabled.map(s => ({ step_id: s.id }));
    }
  }
  if (project.logic_mode === 'custom' && project.custom_based_on === 'sequential'
      && (!project.custom_sequence_order || project.custom_sequence_order.length === 0)) {
    const enabled = (project.steps_config || []).filter(s => s.enabled !== false && !s.is_backup && !s.backup_for);
    if (enabled.length > 0) {
      project.custom_sequence_order = enabled.map(s => ({ step_id: s.id }));
    }
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
    // v3.7.4: 按时间触发 (0=关闭, 老项目默认 0 = 行为完全不变)
    time_interval_seconds: typeof rule.time_interval_seconds === 'number' ? rule.time_interval_seconds : 0,
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
  // v3.19.x: 自定义混合模式（不填 = 现状，零差异）
  if (project.custom_mixed_with === undefined) {
    project.custom_mixed_with = pipelineConfig.custom_mixed_with || null;
  }
  // 自定义混合-容器装箱清点（不填容器标签 = 不启用，零差异）
  if (project.custom_mix_container_label === undefined) {
    project.custom_mix_container_label = pipelineConfig.custom_mix_container_label || '';
  }
  if (project.custom_mix_container_count_mode === undefined) {
    project.custom_mix_container_count_mode = pipelineConfig.custom_mix_container_count_mode || 'trays';
  }
  if (project.custom_mix_container_box_count === undefined) {
    project.custom_mix_container_box_count = pipelineConfig.custom_mix_container_box_count || 0;
  }
  if (project.custom_mix_container_item_target === undefined) {
    project.custom_mix_container_item_target = pipelineConfig.custom_mix_container_item_target || 0;
  }
  if (project.custom_mix_container_gone_frames === undefined) {
    project.custom_mix_container_gone_frames = pipelineConfig.custom_mix_container_gone_frames || 30;
  }
  if (project.custom_mix_container_iou_match === undefined) {
    project.custom_mix_container_iou_match = pipelineConfig.custom_mix_container_iou_match ?? 0.3;
  }
  if (project.custom_mix_container_enabled === undefined) {
    project.custom_mix_container_enabled = !!project.custom_mix_container_label;
  }
  // 进箱确认方式（默认仅"消失满帧"= 老行为零差异）
  if (project.custom_mix_container_confirm_by_frames === undefined) {
    project.custom_mix_container_confirm_by_frames = pipelineConfig.custom_mix_container_confirm_by_frames !== false;
  }
  if (project.custom_mix_container_confirm_by_action === undefined) {
    project.custom_mix_container_confirm_by_action = !!pipelineConfig.custom_mix_container_confirm_by_action;
  }
  if (project.custom_mix_container_action_label === undefined) {
    project.custom_mix_container_action_label = pipelineConfig.custom_mix_container_action_label || '';
  }
  if (project.custom_mix_container_confirm_combine === undefined) {
    project.custom_mix_container_confirm_combine = pipelineConfig.custom_mix_container_confirm_combine || 'or';
  }
  // v3.43.1 动作门槛直配三参数 (0 = 出现/消失帧跟随步骤配置; 不应期缺省 2s, 显式 0 = 关闭)
  if (project.custom_mix_container_action_min_frames === undefined) {
    project.custom_mix_container_action_min_frames = pipelineConfig.custom_mix_container_action_min_frames ?? 0;
  }
  if (project.custom_mix_container_action_gone_frames === undefined) {
    project.custom_mix_container_action_gone_frames = pipelineConfig.custom_mix_container_action_gone_frames ?? 0;
  }
  if (project.custom_mix_container_action_cooldown_s === undefined) {
    project.custom_mix_container_action_cooldown_s = pipelineConfig.custom_mix_container_action_cooldown_s ?? 2;
  }
  if (project.custom_mix_container_per_tray_guard === undefined) {
    project.custom_mix_container_per_tray_guard = pipelineConfig.custom_mix_container_per_tray_guard === true;
  }
  // v3.44.0e 每盘峰值封顶 / 动作前稳定计数快照 (0 = 关, 老项目零差异)
  if (project.custom_mix_container_peak_cap === undefined) {
    project.custom_mix_container_peak_cap = pipelineConfig.custom_mix_container_peak_cap ?? 0;
  }
  if (project.custom_mix_container_stable_min_frames === undefined) {
    project.custom_mix_container_stable_min_frames = pipelineConfig.custom_mix_container_stable_min_frames ?? 0;
  }
  // v3.46 记账修正开关组 (默认值 = v3.45 线上行为: 滑块去重开, 其余关)
  if (project.custom_mix_container_dedup_items === undefined) {
    project.custom_mix_container_dedup_items = pipelineConfig.custom_mix_container_dedup_items !== false;
  }
  if (project.custom_mix_container_dedup_trays === undefined) {
    project.custom_mix_container_dedup_trays = pipelineConfig.custom_mix_container_dedup_trays === true;
  }
  if (project.custom_mix_container_purge_empty_primary === undefined) {
    project.custom_mix_container_purge_empty_primary = pipelineConfig.custom_mix_container_purge_empty_primary === true;
  }
  if (project.custom_mix_container_yield_primary === undefined) {
    project.custom_mix_container_yield_primary = pipelineConfig.custom_mix_container_yield_primary === true;
  }
  if (project.custom_mix_container_unified_book_source === undefined) {
    project.custom_mix_container_unified_book_source = pipelineConfig.custom_mix_container_unified_book_source === true;
  }
  // v3.46 槽位完整性门 (空槽标签 + 槽位总数都配齐才开门)
  if (project.custom_mix_container_slot_check_label === undefined) {
    project.custom_mix_container_slot_check_label = pipelineConfig.custom_mix_container_slot_check_label || '';
  }
  if (project.custom_mix_container_slot_total === undefined) {
    project.custom_mix_container_slot_total = pipelineConfig.custom_mix_container_slot_total ?? 0;
  }
  // v3.46 重复框去重阈值: 物品框缺省 0.45 (保持 v3.45 既有行为), 托盘框缺省 0 关闭
  if (project.custom_mix_container_item_dedup_iou === undefined) {
    project.custom_mix_container_item_dedup_iou = pipelineConfig.custom_mix_container_item_dedup_iou ?? 0.45;
  }
  if (project.custom_mix_container_tray_dedup_iou === undefined) {
    project.custom_mix_container_tray_dedup_iou = pipelineConfig.custom_mix_container_tray_dedup_iou ?? 0;
  }
  // 物品行原生字段兜底：老数据/手改 JSON 可能缺字段，表C输入框依赖它们存在
  (project.steps_config || []).forEach(s => {
    if (s.detect_role !== 'item') return;
    if (project.custom_mixed_with === 'tracking') {
      if (s.count_mode === undefined) s.count_mode = 'track';
      if (s.expected_count === undefined) {
        s.expected_count = s.mix_item?.expected_count ?? 1;  // 旧 mix_item 存储兜底
      }
    } else if (project.custom_mixed_with === 'per_item' && !s.per_item) {
      s.per_item = {
        item_label: s.label || '', action_label: '',
        item_tracking_iou: 0.3, coverage_iou: 0.3, coverage_use_center: false,
        sustain_frames: 5, expected_count: 0, completion: 'all_covered',
      };
    }
  });
  
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
  // 防重复结算 (v3.10.x: 时间窗口冷却模式)
  if (project.settle_dedup === undefined) {
    project.settle_dedup = pipelineConfig.settle_dedup || false;
  }
  if (project.settle_dedup_window_seconds === undefined) {
    const v = Number(pipelineConfig.settle_dedup_window_seconds);
    project.settle_dedup_window_seconds = Number.isFinite(v) && v >= 0 ? v : 2.0;
  }
  // 同时出现组
  if (project.simultaneous_groups === undefined) {
    project.simultaneous_groups = pipelineConfig.simultaneous_groups || [];
  }
  // 反向推导跨周期组的 period_roles (后端只保存 prev/next_cycle_labels, 前端编辑用 period_roles)
  (project.simultaneous_groups || []).forEach(g => {
    if (g.cross_cycle && !g.period_roles) {
      g.period_roles = {};
      (g.prev_cycle_labels || []).forEach(lbl => { g.period_roles[lbl] = 'prev'; });
      (g.next_cycle_labels || []).forEach(lbl => { g.period_roles[lbl] = 'next'; });
    }
  });
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
  // (v3.44: 违序提示 / 实时NG 顶层字段已并入 pipeline_config.ng_handling, 见下方统一初始化)
  // 加载后同步结算步约束（与 watch(settlement_mode) 一致，避免开关仍可编辑/值为 true）
  const canSeqSettle = project.logic_mode === 'sequential'
    || (project.logic_mode === 'custom' && project.custom_based_on === 'sequential');
  if (canSeqSettle && project.steps_config?.length) {
    if (project.settlement_mode === 'last_first') {
      for (const step of project.steps_config) {
        step.strict_order = false;
      }
    } else if (project.settlement_mode === 'last_step' || project.settlement_mode === 'first_step') {
      let seq = (project.logic_mode === 'custom' && project.custom_based_on === 'sequential')
        ? (project.custom_sequence_order || [])
        : (project.sequence_order || []);
      if (!seq.length) {
        seq = (project.steps_config || [])
          .filter(s => s.enabled !== false && !s.is_backup && !s.backup_for)
          .map(s => ({ step_id: s.id }));
      }
      const enabledIds = new Set(
        (project.steps_config || []).filter(s => s.enabled !== false).map(s => s.id)
      );
      let settleId = null;
      if (project.settlement_mode === 'last_step') {
        for (let i = seq.length - 1; i >= 0; i--) {
          const sid = seq[i]?.step_id;
          if (sid != null && enabledIds.has(sid)) {
            settleId = sid;
            break;
          }
        }
      } else {
        for (const item of seq) {
          const sid = item?.step_id;
          if (sid != null && enabledIds.has(sid)) {
            settleId = sid;
            break;
          }
        }
      }
      const settleStep = settleId != null
        ? project.steps_config.find(s => s.id === settleId)
        : null;
      if (settleStep) {
        settleStep.strict_order = false;
        settleStep.accept_once = false;
      }
    }
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
  // v3.50 齐件即结算 (默认关, 老项目零差异)
  if (project.tracking_settle_on_complete === undefined) {
    project.tracking_settle_on_complete = pipelineConfig.tracking_settle_on_complete === true;
  }
  // v3.50.1 扫码后才计数 (默认关, 老项目零差异)
  if (project.tracking_scan_gate === undefined) {
    project.tracking_scan_gate = pipelineConfig.tracking_scan_gate === true;
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

  // v3.8: per_item 逐件模式 - 项目级配置 (嵌套对象,不拍平)
  // 同时为 sequential/tracking 等老项目兜底建空对象,避免模板 v-model 访问 undefined.X 报错
  if (!project.pipeline_config.per_item || typeof project.pipeline_config.per_item !== 'object') {
    project.pipeline_config.per_item = {};
  }
  const piCfg = project.pipeline_config.per_item;
  if (piCfg.stability_window_frames === undefined) piCfg.stability_window_frames = 10;
  if (piCfg.stability_iou_threshold === undefined) piCfg.stability_iou_threshold = 0.6;
  if (piCfg.item_timeout_seconds === undefined) piCfg.item_timeout_seconds = 0;  // 0=不限
  if (piCfg.lock_count_on_start === undefined) piCfg.lock_count_on_start = true;
  if (piCfg.finish_label === undefined) piCfg.finish_label = '';
  if (piCfg.finish_sustain_frames === undefined) piCfg.finish_sustain_frames = 3;
  if (piCfg.finish_requires_no_items === undefined) piCfg.finish_requires_no_items = false;
  // v3.9+ 新增字段默认值
  if (piCfg.stability_count_tolerance === undefined) piCfg.stability_count_tolerance = 0;
  if (piCfg.stability_count_ratio === undefined) piCfg.stability_count_ratio = 0.85;
  if (piCfg.settle_after_all_done_sec === undefined) piCfg.settle_after_all_done_sec = 0;
  if (piCfg.lock_lookahead_seconds === undefined) piCfg.lock_lookahead_seconds = 5;
  // v3.10.2+ 严格等量触发开关 (默认 false = 走宽松路径, 保持向后兼容)
  if (piCfg.require_exact_count === undefined) piCfg.require_exact_count = false;
  // v3.10.2+ 手动结算模式开关 (默认 false = 自动结算生效)
  if (piCfg.disable_auto_settle === undefined) piCfg.disable_auto_settle = false;
  // v3.9+ per_item 专属超时 (与项目级 cycle_max_duration / idle_timeout_seconds 解耦)
  // 老 per_item 项目: 若专属字段未配, 后端会自动从项目级老字段回落, 这里前端只兜底 0
  if (piCfg.cycle_max_duration_sec === undefined) piCfg.cycle_max_duration_sec = 0;
  if (piCfg.idle_timeout_sec === undefined) piCfg.idle_timeout_sec = 0;
  // v3.32+ 换板兜底结算 (工件整体消失确认, 防上一板覆盖泄漏到新板). 默认 15 帧.
  if (piCfg.workpiece_absent_settle_frames === undefined) piCfg.workpiece_absent_settle_frames = 15;
  // 重复打同一颗螺丝防护 (后端已实现, 补齐前端 round-trip 默认值, 防保存时丢失)
  if (piCfg.duplicate_screw_alarm === undefined) piCfg.duplicate_screw_alarm = false;
  if (piCfg.duplicate_sustain_frames === undefined) piCfg.duplicate_sustain_frames = 2;
  if (piCfg.duplicate_release_frames === undefined) piCfg.duplicate_release_frames = 8;
  if (piCfg.duplicate_alarm_interval_sec === undefined) piCfg.duplicate_alarm_interval_sec = 2.0;
  if (piCfg.duplicate_warning_display_sec === undefined) piCfg.duplicate_warning_display_sec = 3.0;
  // v3.27+ 离场判定 / 漏打补做 / 框色高亮 (全部默认关 = 老项目零差异)
  if (piCfg.judge_on_workpiece_leave === undefined) piCfg.judge_on_workpiece_leave = false;
  if (piCfg.leave_confirm_frames === undefined) piCfg.leave_confirm_frames = 25;
  if (piCfg.ng_hold_for_remediation === undefined) piCfg.ng_hold_for_remediation = false;
  if (piCfg.remediation_timeout_sec === undefined) piCfg.remediation_timeout_sec = 0;
  if (piCfg.remediation_event_id === undefined) piCfg.remediation_event_id = null;
  if (piCfg.remediation_takeaway_ng === undefined) piCfg.remediation_takeaway_ng = false;
  if (piCfg.remediation_alarm_mode === undefined) piCfg.remediation_alarm_mode = 'once';
  if (piCfg.remediation_alarm_interval_sec === undefined) piCfg.remediation_alarm_interval_sec = 2.0;
  if (piCfg.color_by_coverage === undefined) piCfg.color_by_coverage = false;
  if (piCfg.show_item_numbers === undefined) piCfg.show_item_numbers = false;
  if (piCfg.box_color_covered === undefined) piCfg.box_color_covered = '';
  if (piCfg.box_color_uncovered === undefined) piCfg.box_color_uncovered = '';
  // ── 判定时机 (与结算时机解耦, 默认 on_settle = 结算时判 = 老项目零差异) ──
  if (piCfg.judge_timing === undefined) piCfg.judge_timing = 'on_settle';
  if (piCfg.judge_label === undefined) piCfg.judge_label = '';
  if (piCfg.judge_all_done_sec === undefined) piCfg.judge_all_done_sec = 0;
  if (piCfg.judge_label_frames === undefined) piCfg.judge_label_frames = 3;
  if (piCfg.judge_ok_event_id === undefined) piCfg.judge_ok_event_id = null;

  // ── 结算触发方式: 唯一入口的 UI 助手字段 (从底层标志反推, 不持久化为权威; 保存时再换算回标志) ──
  // 底层标志才是后端权威: judge_on_workpiece_leave(物品消失) / finish_label(步骤标签) /
  //   finish_requires_no_items(双条件门) / settle_after_all_done_sec(全部完成OK).
  // _settle_mode='alldone'(全部完成即OK, 与信号互斥) | 'signal'(步骤标签/物品消失, 可叠加=双条件)
  {
    const _leave = piCfg.judge_on_workpiece_leave === true;
    const _dual = piCfg.finish_requires_no_items === true;
    const _hasLabel = !!piCfg.finish_label;
    const _allDone = (Number(piCfg.settle_after_all_done_sec) || 0) > 0;
    piCfg._finish_label_choice = piCfg._finish_label_choice || piCfg.finish_label || '';   // 保留标签, 切模式不丢
    if (_leave || _hasLabel) {
      piCfg._settle_mode = 'signal';
      piCfg._settle_by_item = _leave;
      piCfg._settle_by_step = _leave ? _dual : _hasLabel;
    } else if (_allDone) {
      piCfg._settle_mode = 'alldone';
      piCfg._settle_by_item = false;
      piCfg._settle_by_step = false;
    } else {
      piCfg._settle_mode = 'signal';
      piCfg._settle_by_item = true;   // 缺省偏向"物品消失"(打螺丝场景常用)
      piCfg._settle_by_step = false;
    }
  }
  // 步骤级 expected_count 默认值 (老项目 step.per_item 内可能没这字段)
  for (const s of (project.steps_config || [])) {
    if (s && s.per_item && typeof s.per_item === 'object') {
      if (s.per_item.expected_count === undefined) s.per_item.expected_count = 0;
    }
  }

  // v3.44 NG 判定与处置统一模型: 新块优先, 老项目从 legacy 键合成 (行为零差异).
  // legacy 键 (ng_remediation / closing_guard / instant_ng_on_violation /
  // strict_order_violation_event_id) 读后不再回写, 保存只落 ng_handling.
  if (!project.pipeline_config.ng_handling
      || typeof project.pipeline_config.ng_handling !== 'object'
      || !Object.keys(project.pipeline_config.ng_handling).length) {
    const rem = pipelineConfig.ng_remediation || {};
    const cg = pipelineConfig.closing_guard || {};
    const remOn = rem.enabled === true;
    project.pipeline_config.ng_handling = {
      violation: pipelineConfig.instant_ng_on_violation ? 'instant_ng'
        : (pipelineConfig.strict_order_violation_event_id ? 'hint' : 'none'),
      violation_event_id: pipelineConfig.strict_order_violation_event_id || null,
      missing_step: cg.hold_enabled ? 'hold'
        : (remOn && rem.allow_step !== false ? 'ack' : 'ng'),
      hold_timeout_s: Number.isFinite(Number(cg.hold_timeout_s)) ? Number(cg.hold_timeout_s) : 120,
      hold_event_id: cg.event_id || null,
      short_count: remOn && rem.allow_count !== false ? 'ack' : 'ng',
      gate_enabled: cg.gate_enabled === true,
      gate_steps: Array.isArray(cg.gate_steps) ? cg.gate_steps : [],
      gate_event_id: cg.event_id || null,
    };
  }
  // v3.48 计数组合判定表 (检测模式纯视觉判型) 兜底: 缺块建默认关闭的空表
  if (!project.pipeline_config.combo_table
      || typeof project.pipeline_config.combo_table !== 'object') {
    project.pipeline_config.combo_table = { enabled: false, labels: [], rows: [] };
  }
  const cmb = project.pipeline_config.combo_table;
  if (typeof cmb.enabled !== 'boolean') cmb.enabled = false;
  if (!Array.isArray(cmb.labels)) cmb.labels = [];
  if (!Array.isArray(cmb.rows)) cmb.rows = [];
  // v3.48.x 计数口径 + positional 追踪参数兜底 (默认值与后端 _parse_combo_table 一致)
  if (cmb.count_mode !== 'positional') cmb.count_mode = 'steps';
  if (!cmb.tracking || typeof cmb.tracking !== 'object') cmb.tracking = {};
  const trkDefaults = { iou: 0.4, ema_alpha: 0.6, min_consecutive: 3,
                        pending_ttl: 10, perish_ticks: 0, idle_reset_ticks: 0 };
  for (const [k, dv] of Object.entries(trkDefaults)) {
    if (!Number.isFinite(Number(cmb.tracking[k]))) cmb.tracking[k] = dv;
  }
  // 监控锁定框显示开关 (默认开; false 只是不画, 锁定/计数照常)
  if (typeof cmb.show_lock_overlay !== 'boolean') cmb.show_lock_overlay = true;
  // v3.49 切步数量门兜底 (默认全关 = 零差异)
  if (!cmb.step_guard || typeof cmb.step_guard !== 'object') cmb.step_guard = {};
  const sg = cmb.step_guard;
  if (typeof sg.enabled !== 'boolean') sg.enabled = false;
  if (typeof sg.check_under !== 'boolean') sg.check_under = true;
  if (typeof sg.check_over !== 'boolean') sg.check_over = true;
  if (typeof sg.narrow_by_progress !== 'boolean') sg.narrow_by_progress = true;
  if (!['hint', 'instant_ng'].includes(sg.action)) sg.action = 'hint';
  if (!['table', 'plc', 'auto'].includes(sg.expected_source)) sg.expected_source = 'table';
  if (!['table', 'skip'].includes(sg.plc_unavailable)) sg.plc_unavailable = 'table';
  if (sg.event_id === undefined) sg.event_id = null;
  if (sg.plc_connection_id === undefined) sg.plc_connection_id = null;
  if (typeof sg.plc_point !== 'string') sg.plc_point = sg.plc_point != null ? String(sg.plc_point) : '';
  // v3.49 二期新键 (默认与后端 _parse_combo_table 一致)
  if (typeof sg.check_order !== 'boolean') sg.check_order = false;
  if (sg.resolved_event_id === undefined) sg.resolved_event_id = null;
  if (!['ng', 'hold'].includes(sg.on_settle_mismatch)) sg.on_settle_mismatch = 'ng';
  if (!Number.isFinite(Number(sg.hold_timeout_s))) sg.hold_timeout_s = 120;
  (cmb.rows || []).forEach((row) => {
    if (row && typeof row.plc_code !== 'string') {
      row.plc_code = row.plc_code != null ? String(row.plc_code) : '';
    }
  });
  // v3.49 二期: 大字实时卡 + 缸型独立点位 + 按标签追踪参数覆盖兜底
  if (!cmb.live_display || typeof cmb.live_display !== 'object') {
    cmb.live_display = { enabled: false, size: 'large', show_plc_type: true, position: 'top' };
  }
  const ld = cmb.live_display;
  if (typeof ld.enabled !== 'boolean') ld.enabled = false;
  if (!['large', 'normal'].includes(ld.size)) ld.size = 'large';
  if (typeof ld.show_plc_type !== 'boolean') ld.show_plc_type = true;
  if (!['top', 'bottom'].includes(ld.position)) ld.position = 'top';
  if (!cmb.plc_display || typeof cmb.plc_display !== 'object') {
    cmb.plc_display = { connection_id: null, point: '' };
  }
  if (cmb.plc_display.connection_id === undefined) cmb.plc_display.connection_id = null;
  if (typeof cmb.plc_display.point !== 'string') {
    cmb.plc_display.point = cmb.plc_display.point != null ? String(cmb.plc_display.point) : '';
  }
  if (!cmb.tracking_per_label || typeof cmb.tracking_per_label !== 'object') {
    cmb.tracking_per_label = {};
  }

  const nghCfg = project.pipeline_config.ng_handling;
  if (!['none', 'hint', 'instant_ng'].includes(nghCfg.violation)) nghCfg.violation = 'none';
  if (!['ng', 'ack', 'hold'].includes(nghCfg.missing_step)) nghCfg.missing_step = 'ng';
  if (!['ng', 'ack', 'hold'].includes(nghCfg.short_count)) nghCfg.short_count = 'ng';
  if (nghCfg.violation_event_id === undefined) nghCfg.violation_event_id = null;
  if (nghCfg.hold_event_id === undefined) nghCfg.hold_event_id = null;
  if (nghCfg.gate_event_id === undefined) nghCfg.gate_event_id = null;
  if (!Array.isArray(nghCfg.gate_steps)) nghCfg.gate_steps = [];
  if (!Array.isArray(nghCfg.gate_escalate_steps)) nghCfg.gate_escalate_steps = [];
  if (!Number.isFinite(Number(nghCfg.hold_timeout_s))) nghCfg.hold_timeout_s = 120;

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
  // v3.35.1 自定义班次列表: 未配过则从两班字段生成初始两条 (白班/晚班)
  if (project.shifts === undefined) {
    const raw = Array.isArray(dataConfig.shifts) ? dataConfig.shifts : null;
    project.shifts = (raw && raw.length >= 2)
      ? raw.map(s => ({ name: s.name || '', start: s.start || '08:00' }))
      : [
          { name: '白班', start: project.day_shift_start || '08:00' },
          { name: '晚班', start: project.night_shift_start || '20:00' },
        ];
  }
  
  // 同步到 pipeline_config，供UI使用
  project.pipeline_config.sequence_order = project.sequence_order;
  project.pipeline_config.detection_steps = project.detection_steps;
  project.pipeline_config.custom_conditions = project.custom_conditions;
  project.pipeline_config.custom_based_on = project.custom_based_on;
  project.pipeline_config.custom_mixed_with = project.custom_mixed_with;
  project.pipeline_config.custom_mix_container_label = (project.custom_mixed_with === 'tracking' && project.custom_mix_container_enabled)
    ? (project.custom_mix_container_label || '') : '';
  project.pipeline_config.custom_mix_container_count_mode = project.custom_mix_container_count_mode || 'trays';
  project.pipeline_config.custom_mix_container_box_count = project.custom_mix_container_box_count || 0;
  project.pipeline_config.custom_mix_container_item_target = project.custom_mix_container_item_target || 0;
  project.pipeline_config.custom_mix_container_gone_frames = project.custom_mix_container_gone_frames || 30;
  project.pipeline_config.custom_mix_container_iou_match = project.custom_mix_container_iou_match ?? 0.3;
  project.pipeline_config.custom_mix_container_confirm_by_frames = project.custom_mix_container_confirm_by_frames !== false;
  project.pipeline_config.custom_mix_container_confirm_by_action = !!project.custom_mix_container_confirm_by_action;
  project.pipeline_config.custom_mix_container_action_label = project.custom_mix_container_confirm_by_action
    ? (project.custom_mix_container_action_label || '') : '';
  project.pipeline_config.custom_mix_container_confirm_combine = project.custom_mix_container_confirm_combine || 'or';
  project.pipeline_config.custom_mix_container_action_min_frames = project.custom_mix_container_action_min_frames || 0;
  project.pipeline_config.custom_mix_container_action_gone_frames = project.custom_mix_container_action_gone_frames || 0;
  project.pipeline_config.custom_mix_container_action_cooldown_s = project.custom_mix_container_action_cooldown_s ?? 2;
  project.pipeline_config.custom_mix_container_per_tray_guard = project.custom_mix_container_per_tray_guard === true;
  project.pipeline_config.custom_mix_container_peak_cap = project.custom_mix_container_peak_cap || 0;
  project.pipeline_config.custom_mix_container_stable_min_frames = project.custom_mix_container_stable_min_frames || 0;
  // v3.46 记账修正开关组
  project.pipeline_config.custom_mix_container_dedup_items = project.custom_mix_container_dedup_items !== false;
  project.pipeline_config.custom_mix_container_dedup_trays = project.custom_mix_container_dedup_trays === true;
  project.pipeline_config.custom_mix_container_purge_empty_primary = project.custom_mix_container_purge_empty_primary === true;
  project.pipeline_config.custom_mix_container_yield_primary = project.custom_mix_container_yield_primary === true;
  project.pipeline_config.custom_mix_container_unified_book_source = project.custom_mix_container_unified_book_source === true;
  project.pipeline_config.custom_mix_container_slot_check_label = project.custom_mix_container_slot_check_label || '';
  project.pipeline_config.custom_mix_container_slot_total = project.custom_mix_container_slot_total || 0;
  project.pipeline_config.custom_mix_container_item_dedup_iou = project.custom_mix_container_item_dedup_iou ?? 0.45;
  project.pipeline_config.custom_mix_container_tray_dedup_iou = project.custom_mix_container_tray_dedup_iou || 0;
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
  dbg('project.crud', '点击「激活项目」', `name=${activeProject.value?.name} id=${activeProject.value?.id}`);
  try {
    await activateProject(activeProject.value.id);
    projectStore.setCurrentProject(activeProject.value);
    // v3.8.x: 同步 systemStore 的当前项目 ID + 加载检测框配置 —
    // 老版本这里漏了, 导致设置页改的检测框配置永远写不进 DB (currentProjectId 一直 null),
    // 重启就回默认. 现在激活项目立刻把绑定补上.
    systemStore.setCurrentProjectId(activeProject.value.id);
    systemStore.loadDetectionFromProject(activeProject.value.detection_config, activeProject.value.id);
    dbg('project.crud', '激活项目成功', `id=${activeProject.value?.id}`);
    ElMessage.success(`已激活项目: ${activeProject.value.name}`);
    loadProjects();
  } catch (err) {
    dbgErr('project.crud', '激活项目', err);
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
  dbg('project.crud', '点击「创建项目」', `name=${newProjectForm.value?.name} mode=${newProjectForm.value?.logic_mode}`);
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
    dbg('project.crud', '创建项目成功', `id=${res.data?.id} name=${res.data?.name}`);
    ElMessage.success('项目创建成功');
  } catch (err) {
    dbgErr('project.crud', '创建项目', err);
    ElMessage.error('创建项目失败: ' + (err.response?.data?.detail || err.message));
  } finally {
    creating.value = false;
  }
};

// 保存项目
const handleSaveProject = async () => {
  if (!activeProject.value) return;
  dbg('project.crud', '点击「保存项目」', `name=${activeProject.value?.name} id=${activeProject.value?.id} steps=${activeProject.value?.steps_config?.length ?? 0}`);

  // v3.8.x last_first 模式互斥校验
  // 1. 不能与跨周期同时出现组共存
  // 2. 不能与 per_item 模式共存
  // 3. 所有步骤的严格顺序应已被 watch 自动关闭, 这里再兜底校验
  if (activeProject.value.settlement_mode === 'last_first') {
    const crossGroups = (activeProject.value.simultaneous_groups || []).filter(g => g.enabled && g.cross_cycle);
    if (crossGroups.length > 0) {
      ElMessage.error('"末步结算 + 首步开周期"模式不能与跨周期同时出现组共存，请先停用其中一项');
      return;
    }
    if (activeProject.value.per_item?.enabled) {
      ElMessage.error('"末步结算 + 首步开周期"模式不能与逐件覆盖模式共存');
      return;
    }
    const stillStrict = (activeProject.value.steps_config || []).filter(s => s.strict_order);
    if (stillStrict.length > 0) {
      // 兜底自动清掉 (避免警告刷屏)
      stillStrict.forEach(s => { s.strict_order = false; });
    }
  }

  // 跨周期同时出现组校验:
  // 1. 跨周期组与"启用防重复结算"互斥
  // 2. 跨周期组的每个成员必须明确标记"上周期 / 下周期"归属
  // 3. 跨周期组必须同时含至少一个上周期成员和至少一个下周期成员
  const crossCycleGroups = (activeProject.value.simultaneous_groups || []).filter(g => g.enabled && g.cross_cycle);
  if (crossCycleGroups.length > 0 && activeProject.value.settle_dedup) {
    ElMessage.error('启用了跨周期同时出现组时, 不能同时启用"防重复结算"。请关闭其中一项后再保存。');
    return;
  }
  for (let i = 0; i < crossCycleGroups.length; i++) {
    const g = crossCycleGroups[i];
    const members = (g.priority_order || []).filter(l => l);
    const roles = g.period_roles || {};
    const missingRole = members.filter(l => !roles[l]);
    if (missingRole.length > 0) {
      ElMessage.error(`跨周期同时出现组 ${i + 1}: 成员 [${missingRole.join(', ')}] 未标记归属（上周期 / 下周期）`);
      return;
    }
    const prev = members.filter(l => roles[l] === 'prev');
    const next = members.filter(l => roles[l] === 'next');
    if (prev.length === 0 || next.length === 0) {
      ElMessage.error(`跨周期同时出现组 ${i + 1}: 必须至少有一个上周期成员和一个下周期成员`);
      return;
    }
  }

  // v3.19.x: 连续重复步骤的消失等待时间保存前强制清 0（后端 apply 还有一层兜底）
  syncDupDisappearDelay();

  // v3.32 同标签区域拆分: 保存前校验规则 + 幂等同步虚拟步骤（区域名 ⇄ steps_config）
  {
    const splitErr = validateSplitRules(activeProject.value);
    if (splitErr) {
      ElMessage.error(`同标签区域拆分配置有误: ${splitErr}`);
      return;
    }
    syncSplitVirtualSteps(activeProject.value);
  }

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
        custom_mixed_with: activeProject.value.custom_mixed_with || null,
        // 自定义混合-容器装箱清点（仅混合跟踪 + 开关开 时落容器标签；否则空 = 不启用，零差异）
        custom_mix_container_label: (activeProject.value.custom_mixed_with === 'tracking' && activeProject.value.custom_mix_container_enabled)
          ? (activeProject.value.custom_mix_container_label || '') : '',
        custom_mix_container_count_mode: activeProject.value.custom_mix_container_count_mode || 'trays',
        custom_mix_container_box_count: Number(activeProject.value.custom_mix_container_box_count) || 0,
        custom_mix_container_item_target: Number(activeProject.value.custom_mix_container_item_target) || 0,
        custom_mix_container_gone_frames: Number(activeProject.value.custom_mix_container_gone_frames) || 30,
        custom_mix_container_iou_match: (() => {
          const v = Number(activeProject.value.custom_mix_container_iou_match);
          return Number.isFinite(v) && v > 0 ? Math.min(0.99, v) : 0.3;
        })(),
        // 进箱确认方式（默认仅"消失满帧"= 老行为零差异；动作确认需配放托盘标签才落值）
        custom_mix_container_confirm_by_frames: activeProject.value.custom_mix_container_confirm_by_frames !== false,
        custom_mix_container_confirm_by_action: !!activeProject.value.custom_mix_container_confirm_by_action,
        custom_mix_container_action_label: activeProject.value.custom_mix_container_confirm_by_action
          ? (activeProject.value.custom_mix_container_action_label || '') : '',
        custom_mix_container_confirm_combine: activeProject.value.custom_mix_container_confirm_combine || 'or',
        custom_mix_container_action_min_frames: activeProject.value.custom_mix_container_action_min_frames || 0,
        custom_mix_container_action_gone_frames: activeProject.value.custom_mix_container_action_gone_frames || 0,
        custom_mix_container_action_cooldown_s: activeProject.value.custom_mix_container_action_cooldown_s ?? 2,
        custom_mix_container_per_tray_guard: activeProject.value.custom_mix_container_per_tray_guard === true,
        custom_mix_container_peak_cap: activeProject.value.custom_mix_container_peak_cap || 0,
        custom_mix_container_stable_min_frames: activeProject.value.custom_mix_container_stable_min_frames || 0,
        // v3.46 记账修正开关组
        custom_mix_container_dedup_items: activeProject.value.custom_mix_container_dedup_items !== false,
        custom_mix_container_dedup_trays: activeProject.value.custom_mix_container_dedup_trays === true,
        custom_mix_container_purge_empty_primary: activeProject.value.custom_mix_container_purge_empty_primary === true,
        custom_mix_container_yield_primary: activeProject.value.custom_mix_container_yield_primary === true,
        custom_mix_container_unified_book_source: activeProject.value.custom_mix_container_unified_book_source === true,
        custom_mix_container_slot_check_label: activeProject.value.custom_mix_container_slot_check_label || '',
        custom_mix_container_slot_total: activeProject.value.custom_mix_container_slot_total || 0,
        custom_mix_container_item_dedup_iou: activeProject.value.custom_mix_container_item_dedup_iou ?? 0.45,
        custom_mix_container_tray_dedup_iou: activeProject.value.custom_mix_container_tray_dedup_iou || 0,
        custom_sequence_order: activeProject.value.custom_sequence_order,
        custom_detection_steps: activeProject.value.custom_detection_steps,
        accumulate_repeats: activeProject.value.accumulate_repeats,
        ng_cycle_protect_seconds: activeProject.value.ng_cycle_protect_seconds || 0,
        // v3.10.x: 防重复结算 - 时间窗口冷却 (历史回归: 旧版 saveProject 漏写本字段, 导致 UI 开关从未真正落库)
        settle_dedup: !!activeProject.value.settle_dedup,
        settle_dedup_window_seconds: (() => {
          const v = Number(activeProject.value.settle_dedup_window_seconds);
          return Number.isFinite(v) && v >= 0 ? v : 2.0;
        })(),
        simultaneous_groups: (activeProject.value.simultaneous_groups || []).map(g => {
          const members = (g.priority_order || []).filter(l => l);
          const out = { ...g, labels: members };
          if (g.cross_cycle) {
            const roles = g.period_roles || {};
            out.prev_cycle_labels = members.filter(l => roles[l] === 'prev');
            out.next_cycle_labels = members.filter(l => roles[l] === 'next');
          } else {
            // 非跨周期组不保留 period_roles / prev_cycle_labels / next_cycle_labels, 保持配置干净
            delete out.period_roles;
            delete out.prev_cycle_labels;
            delete out.next_cycle_labels;
          }
          return out;
        }),
        tracking_cycle_strategy: activeProject.value.tracking_cycle_strategy || 'all_gone',
        tracking_trigger_label: activeProject.value.tracking_trigger_label || '',
        tracking_trigger_min_frames: activeProject.value.tracking_trigger_min_frames || 15,
        tracking_gone_confirm_frames: activeProject.value.tracking_gone_confirm_frames || 30,
        // v3.50 齐件即结算: 仅 ROI离开/容器两种策略可开, 其他策略强制落 false
        tracking_settle_on_complete: ['roi_exit', 'container'].includes(activeProject.value.tracking_cycle_strategy)
          ? (activeProject.value.tracking_settle_on_complete === true)
          : false,
        // v3.50.1 扫码后才计数 (跟踪模式通用, 默认关)
        tracking_scan_gate: activeProject.value.tracking_scan_gate === true,
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
        // v3.44 NG 判定与处置统一块 (取代 v3.23/v3.32/v3.43/v3.44 四组散装键;
        // 后端读兼容旧键, 前端保存只落新块; last_first 严格顺序被强制清空 →
        // 违规当场档位一并置关)
        ng_handling: (() => {
          const src = activeProject.value.pipeline_config?.ng_handling || {};
          const lastFirst = activeProject.value.settlement_mode === 'last_first';
          const t = Number(src.hold_timeout_s);
          const violation = ['none', 'hint', 'instant_ng'].includes(src.violation)
            ? src.violation : 'none';
          return {
            violation: lastFirst ? 'none' : violation,
            violation_event_id: lastFirst ? null : (src.violation_event_id || null),
            missing_step: ['ng', 'ack', 'hold'].includes(src.missing_step) ? src.missing_step : 'ng',
            hold_timeout_s: Number.isFinite(t) && t >= 0 ? t : 120,
            hold_event_id: src.hold_event_id || null,
            short_count: ['ng', 'ack', 'hold'].includes(src.short_count) ? src.short_count : 'ng',
            gate_enabled: src.gate_enabled === true,
            gate_steps: (Array.isArray(src.gate_steps) ? src.gate_steps : [])
              .map(s => String(s || '').trim()).filter(Boolean),
            gate_event_id: src.gate_event_id || null,
            // v3.44.4 短拦长放: 被门拦时报警放行进结算挂起的步骤 (如封箱);
            // 只对被门管的步骤有意义 → 与 gate_steps 取交集, 防移除后残留
            gate_escalate_steps: (Array.isArray(src.gate_escalate_steps) ? src.gate_escalate_steps : [])
              .map(s => String(s || '').trim())
              .filter(s => s && (Array.isArray(src.gate_steps) ? src.gate_steps : []).includes(s)),
          };
        })(),
        // v3.48 计数组合判定表 (检测模式纯视觉判型; 序列化时净化: 行 counts
        // 与 labels 等长且为非负整数, 后端 _parse_combo_table 二次校验)
        combo_table: (() => {
          const src = activeProject.value.pipeline_config?.combo_table;
          if (!src || typeof src !== 'object') return { enabled: false, labels: [], rows: [] };
          const labels = (Array.isArray(src.labels) ? src.labels : [])
            .map(l => String(l || '').trim()).filter(Boolean);
          const rows = (Array.isArray(src.rows) ? src.rows : [])
            .map(r => ({
              counts: labels.map((_, i) => {
                const v = Number((Array.isArray(r?.counts) ? r.counts : [])[i]);
                return Number.isFinite(v) && v >= 0 ? Math.floor(v) : 0;
              }),
              verdict: String(r?.verdict).toUpperCase() === 'NG' ? 'NG' : 'OK',
              tag: String(r?.tag || '').trim(),
              plc_code: String(r?.plc_code || '').trim(),
            }));
          // v3.48.x 计数口径 + 追踪参数随保存透传 (clamp 与后端 _parse_combo_table 一致)
          const num = (v, d) => { const n = Number(v); return Number.isFinite(n) ? n : d; };
          const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
          const t = (src.tracking && typeof src.tracking === 'object') ? src.tracking : {};
          // v3.49 切步数量门: 未启用只存 enabled:false, 不污染其它字段语义
          const sgSrc = (src.step_guard && typeof src.step_guard === 'object') ? src.step_guard : {};
          const sgEnabled = sgSrc.enabled === true;
          let plcConnId = null;
          if (sgSrc.plc_connection_id != null && sgSrc.plc_connection_id !== '') {
            const n = Number(sgSrc.plc_connection_id);
            plcConnId = Number.isFinite(n) ? Math.floor(n) : null;
          }
          let eventId = null;
          if (sgSrc.event_id != null && sgSrc.event_id !== '') {
            const n = Number(sgSrc.event_id);
            eventId = Number.isFinite(n) ? Math.floor(n) : null;
          }
          let resolvedEventId = null;
          if (sgSrc.resolved_event_id != null && sgSrc.resolved_event_id !== '') {
            const n = Number(sgSrc.resolved_event_id);
            resolvedEventId = Number.isFinite(n) ? Math.floor(n) : null;
          }
          const step_guard = sgEnabled ? {
            enabled: true,
            check_under: sgSrc.check_under !== false,
            check_over: sgSrc.check_over !== false,
            // v3.49 二期: 工序顺序检查 (默认关)
            check_order: sgSrc.check_order === true,
            narrow_by_progress: sgSrc.narrow_by_progress !== false,
            action: sgSrc.action === 'instant_ng' ? 'instant_ng' : 'hint',
            event_id: eventId,
            // v3.49 二期: 补齐消警可配事件 + 结算处置档
            resolved_event_id: resolvedEventId,
            on_settle_mismatch: sgSrc.on_settle_mismatch === 'hold' ? 'hold' : 'ng',
            hold_timeout_s: clamp(num(sgSrc.hold_timeout_s, 120), 0, 86400),
            expected_source: ['table', 'plc', 'auto'].includes(sgSrc.expected_source)
              ? sgSrc.expected_source : 'table',
            plc_unavailable: sgSrc.plc_unavailable === 'skip' ? 'skip' : 'table',
            plc_connection_id: plcConnId,
            plc_point: String(sgSrc.plc_point || '').trim(),
          } : { enabled: false };
          // v3.49 二期: 大字实时卡 (未启用只存 enabled:false)
          const ldSrc = (src.live_display && typeof src.live_display === 'object') ? src.live_display : {};
          const live_display = ldSrc.enabled === true ? {
            enabled: true,
            size: ldSrc.size === 'normal' ? 'normal' : 'large',
            show_plc_type: ldSrc.show_plc_type !== false,
            position: ldSrc.position === 'bottom' ? 'bottom' : 'top',
          } : { enabled: false };
          // v3.49 二期: 缸型显示独立点位 (连接+点位都填了才存)
          const pdSrc = (src.plc_display && typeof src.plc_display === 'object') ? src.plc_display : {};
          let pdConnId = null;
          if (pdSrc.connection_id != null && pdSrc.connection_id !== '') {
            const n = Number(pdSrc.connection_id);
            pdConnId = Number.isFinite(n) ? Math.floor(n) : null;
          }
          const pdPoint = String(pdSrc.point || '').trim();
          const plc_display = (pdConnId && pdPoint)
            ? { connection_id: pdConnId, point: pdPoint } : null;
          // v3.49 二期: 追踪参数按标签覆盖 — 只存有效数值键, 空对象不存该标签
          const tplSrc = (src.tracking_per_label && typeof src.tracking_per_label === 'object')
            ? src.tracking_per_label : {};
          const tplSpec = {
            iou: [0.05, 0.95, false], ema_alpha: [0, 1, false],
            min_consecutive: [1, 60, true], pending_ttl: [1, 600, true],
            perish_ticks: [0, 100000, true], idle_reset_ticks: [0, 100000, true],
          };
          const tracking_per_label = {};
          for (const lbl of labels) {
            const ov = tplSrc[lbl];
            if (!ov || typeof ov !== 'object') continue;
            const cleaned = {};
            for (const [k, [lo, hi, asInt]] of Object.entries(tplSpec)) {
              if (ov[k] === null || ov[k] === undefined || ov[k] === '') continue;
              const n = Number(ov[k]);
              if (!Number.isFinite(n)) continue;
              const v = clamp(n, lo, hi);
              cleaned[k] = asInt ? Math.floor(v) : v;
            }
            if (Object.keys(cleaned).length) tracking_per_label[lbl] = cleaned;
          }
          return {
            enabled: src.enabled === true && labels.length > 0, labels, rows,
            count_mode: src.count_mode === 'positional' ? 'positional' : 'steps',
            show_lock_overlay: src.show_lock_overlay !== false,
            tracking: {
              iou: clamp(num(t.iou, 0.4), 0.05, 0.95),
              ema_alpha: clamp(num(t.ema_alpha, 0.6), 0, 1),
              min_consecutive: clamp(Math.floor(num(t.min_consecutive, 3)), 1, 60),
              pending_ttl: clamp(Math.floor(num(t.pending_ttl, 10)), 1, 600),
              perish_ticks: clamp(Math.floor(num(t.perish_ticks, 0)), 0, 100000),
              idle_reset_ticks: clamp(Math.floor(num(t.idle_reset_ticks, 0)), 0, 100000),
            },
            tracking_per_label,
            step_guard,
            live_display,
            plc_display,
          };
        })(),
        // 原生称重投料模式配置 (weighing 模式 / v3.35 融合步骤门控模式写入, 其他不污染)
        weighing: (activeProject.value.logic_mode === 'weighing'
                   || activeProject.value.pipeline_config?.weighing?.drive_mode === 'step_gate')
          ? (activeProject.value.pipeline_config?.weighing || {})
          : undefined,
        // v3.32+ 区域事件模式配置 (仅 region_events 模式写入, 字段与后端 parse_region_events 对齐)
        region_events: activeProject.value.logic_mode === 'region_events'
          ? _sanitizeRegionEvents(activeProject.value.pipeline_config?.region_events)
          : undefined,
        // 2026-09 OCR 读字 / 异常检测模式配置 (仅对应模式写入, 零污染; 后端 parse_*_config 是最终裁判)
        ocr: activeProject.value.logic_mode === 'ocr'
          ? _sanitizeOcrConfig(activeProject.value.pipeline_config?.ocr)
          : undefined,
        anomaly: activeProject.value.logic_mode === 'anomaly'
          ? _sanitizeAnomalyConfig(activeProject.value.pipeline_config?.anomaly)
          : undefined,
        rod_companion_filter: _sanitizeCompanionFilter(activeProject.value.rod_companion_filter),
        rod_session_gate: _sanitizeSessionGate(activeProject.value.rod_session_gate),
        hide_boxes_outside_step_roi: !!activeProject.value.pipeline_config?.hide_boxes_outside_step_roi,
        // v3.32 同标签区域拆分 + 工件就位提示 (结构定义见对应 RFC; 后端 parse_label_splits 二次校验)
        label_splits: (activeProject.value.pipeline_config?.label_splits || []).map(r => ({
          id: r.id,
          enabled: r.enabled !== false,
          source_label: String(r.source_label || '').trim(),
          mode: r.mode === 'anchor' ? 'anchor' : 'fixed',
          anchor_label: r.mode === 'anchor' ? String(r.anchor_label || '').trim() : '',
          anchor_ref: r.mode === 'anchor' ? (r.anchor_ref || null) : null,
          anchor_hold_seconds: (() => {
            const v = Number(r.anchor_hold_seconds);
            return Number.isFinite(v) && v >= 0 ? Math.min(60, v) : 3.0;
          })(),
          unmatched: ['drop', 'keep', 'map'].includes(r.unmatched) ? r.unmatched : 'drop',
          unmatched_label: r.unmatched === 'map' ? String(r.unmatched_label || '').trim() : '',
          regions: (r.regions || [])
            .filter(g => g && String(g.name || '').trim() && Array.isArray(g.polygon) && g.polygon.length >= 3)
            .map(g => ({ name: String(g.name).trim(), polygon: g.polygon, color: g.color || '' })),
          // v3.32 多轮次: 同一批区域按轮次映射不同虚拟步骤 (前缀+区域名)
          rounds: (() => {
            const rd = r.rounds || {};
            if (!rd.enabled) return { enabled: false };
            const cnt = Math.max(2, Math.min(8, Math.floor(Number(rd.count) || 2)));
            return {
              enabled: true,
              trigger_label: String(rd.trigger_label || '').trim(),
              count: cnt,
              prefixes: (rd.prefixes || []).slice(0, cnt).map(p => String(p || '').trim()),
              trigger_gap_seconds: (() => {
                const v = Number(rd.trigger_gap_seconds);
                return Number.isFinite(v) && v >= 0.5 ? Math.min(60, v) : 3.0;
              })(),
              // 切换确认时长(过滤单帧误检): 缺省 0 = 见帧即切(老行为)
              trigger_min_seconds: (() => {
                const v = Number(rd.trigger_min_seconds);
                return Number.isFinite(v) && v > 0 ? Math.min(10, v) : 0;
              })(),
              // 切换标签专用置信度下限(挡低置信预备动作误触发): 缺省 0 = 不额外过滤
              trigger_conf: (() => {
                const v = Number(rd.trigger_conf);
                return Number.isFinite(v) && v > 0 ? Math.min(1, v) : 0;
              })(),
              // 每轮独立区域(可选): 只收编轮次在界内、画完整的; 空轮不落库(该轮回退共享区域)
              region_overrides: (() => {
                const out = {};
                for (const [key, list] of Object.entries(rd.region_overrides || {})) {
                  const rnd = Math.floor(Number(key));
                  if (!(rnd >= 1 && rnd <= cnt) || !Array.isArray(list)) continue;
                  const valid = list
                    .filter(g => g && String(g.name || '').trim() && Array.isArray(g.polygon) && g.polygon.length >= 3)
                    .map(g => ({ name: String(g.name).trim(), polygon: g.polygon, color: g.color || '' }));
                  if (valid.length) out[String(rnd)] = valid;
                }
                return out;
              })(),
            };
          })(),
        })),
        placement_guide: (() => {
          const pg = activeProject.value.pipeline_config?.placement_guide || {};
          return {
            enabled: !!pg.enabled,
            anchor_label: String(pg.anchor_label || '').trim(),
            polygon: Array.isArray(pg.polygon) && pg.polygon.length >= 3 ? pg.polygon : null,
            mode: pg.mode === 'gate' ? 'gate' : 'hint',
            // 就位后引导框显示策略 (仅前端渲染用): always=常驻 | fade_on_ready=淡化 | hide_on_ready=隐藏
            display: ['fade_on_ready', 'hide_on_ready'].includes(pg.display) ? pg.display : 'always',
          };
        })(),
        // v3.8: per_item 项目级配置 (嵌套对象,无拍平,直接序列化)
        // 注意: 只在 logic_mode === 'per_item' 时写入,其他模式即使有残留字段也清空,避免污染
        per_item: activeProject.value.logic_mode === 'per_item' ? (() => {
          const src = activeProject.value.pipeline_config?.per_item || {};
          // ── 结算触发方式: 从唯一入口的 UI 助手字段换算回后端权威标志 ──
          // C(全部完成即OK) 与 A/B(信号触发) 互斥; A+B 都勾 = 双条件.
          const _mode = src._settle_mode === 'alldone' ? 'alldone' : 'signal';
          const _byStep = src._settle_by_step === true;
          const _byItem = src._settle_by_item === true;
          const _labelChoice = String(src._finish_label_choice || src.finish_label || '').trim();
          let _judgeLeave, _dual, finishLabel, _allDoneSec;
          if (_mode === 'alldone') {
            _judgeLeave = false; _dual = false; finishLabel = '';
            _allDoneSec = Math.max(0.5, Number(src.settle_after_all_done_sec) || 2);
          } else {
            _allDoneSec = 0;                       // 信号模式禁用"全部完成OK"路径
            _judgeLeave = _byItem;
            _dual = (_byStep && _byItem);          // 仅两者都勾 = 双条件门
            finishLabel = _byStep ? _labelChoice : '';
          }
          return {
            stability_window_frames: Math.max(1, Math.floor(Number(src.stability_window_frames) || 10)),
            stability_iou_threshold: Math.max(0.1, Math.min(0.99, Number(src.stability_iou_threshold) || 0.6)),
            item_timeout_seconds: Math.max(0, Number(src.item_timeout_seconds) || 0),
            lock_count_on_start: src.lock_count_on_start !== false,
            finish_label: finishLabel,
            _finish_label_choice: _labelChoice,    // UI 记忆: 切到"全部完成"模式时不丢标签
            finish_sustain_frames: Math.max(1, Math.floor(Number(src.finish_sustain_frames) || 3)),
            finish_requires_no_items: _dual,
            // v3.9+ 新增字段
            stability_count_tolerance: Math.max(0, Math.floor(Number(src.stability_count_tolerance) || 0)),
            stability_count_ratio: Math.max(0.1, Math.min(1.0, Number(src.stability_count_ratio) || 0.85)),
            settle_after_all_done_sec: _allDoneSec,
            lock_lookahead_seconds: Math.max(0, Number(src.lock_lookahead_seconds) || 0),
            // v3.10.2+ 严格等量触发开关
            require_exact_count: src.require_exact_count === true,
            // v3.10.2+ 手动结算模式开关
            disable_auto_settle: src.disable_auto_settle === true,
            // v3.9+ per_item 专属超时 (与其他模式隔离)
            cycle_max_duration_sec: Math.max(0, Math.floor(Number(src.cycle_max_duration_sec) || 0)),
            idle_timeout_sec: Math.max(0, Math.floor(Number(src.idle_timeout_sec) || 0)),
            // v3.32+ 换板兜底结算 (工件整体消失确认, 防上一板覆盖泄漏到新板)
            workpiece_absent_settle_frames: Math.max(0, Math.floor(Number(src.workpiece_absent_settle_frames) || 0)),
            // 重复打同一颗螺丝防护 (后端权威字段, 保存时保留, 防被 normalize 丢弃)
            duplicate_screw_alarm: src.duplicate_screw_alarm === true,
            duplicate_sustain_frames: Math.max(1, Math.floor(Number(src.duplicate_sustain_frames) || 2)),
            duplicate_release_frames: Math.max(1, Math.floor(Number(src.duplicate_release_frames) || 8)),
            duplicate_alarm_interval_sec: Math.max(0, Number(src.duplicate_alarm_interval_sec) || 0),
            duplicate_warning_display_sec: Math.max(0, Number(src.duplicate_warning_display_sec) || 0),
            // v3.27+ 离场判定 / 漏打补做 / 框色高亮 (默认全关, 老项目零差异)
            judge_on_workpiece_leave: _judgeLeave,
            leave_confirm_frames: Math.max(1, Math.floor(Number(src.leave_confirm_frames) || 25)),
            ng_hold_for_remediation: src.ng_hold_for_remediation === true,
            remediation_timeout_sec: Math.max(0, Number(src.remediation_timeout_sec) || 0),
            remediation_event_id: Math.max(0, Math.floor(Number(src.remediation_event_id) || 0)),
            remediation_takeaway_ng: src.remediation_takeaway_ng === true,
            remediation_alarm_mode: src.remediation_alarm_mode === 'sustained' ? 'sustained' : 'once',
            remediation_alarm_interval_sec: Math.max(0.5, Number(src.remediation_alarm_interval_sec) || 2.0),
            color_by_coverage: src.color_by_coverage === true,
            show_item_numbers: src.show_item_numbers === true,
            box_color_covered: String(src.box_color_covered || '').trim(),
            box_color_uncovered: String(src.box_color_uncovered || '').trim(),
            // 判定时机 (与结算时机解耦)
            judge_timing: ['all_done', 'label', 'manual'].includes(src.judge_timing) ? src.judge_timing : 'on_settle',
            judge_label: String(src.judge_label || '').trim(),
            judge_all_done_sec: Math.max(0, Number(src.judge_all_done_sec) || 0),
            judge_label_frames: Math.max(1, Math.floor(Number(src.judge_label_frames) || 3)),
            judge_ok_event_id: Math.max(0, Math.floor(Number(src.judge_ok_event_id) || 0)),
          };
        })() : {},
        periodic_actions: (activeProject.value.periodic_actions || []).map(rule => ({
          id: rule.id,
          name: rule.name || '',
          enabled: rule.enabled !== false,
          trigger_step_ids: Array.isArray(rule.trigger_step_ids) ? rule.trigger_step_ids : [],
          // v3.7.4: interval 现在允许 0 (关闭按次数). 后端 _apply_periodic_actions 会校验
          // interval+time_interval_seconds 不能同时为 0.
          interval: Math.max(0, Math.floor(Number(rule.interval) || 0)),
          time_interval_seconds: Math.max(0, Math.floor(Number(rule.time_interval_seconds) || 0)),
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
      // v3.35.1 自定义班次列表 (名字+开始时刻, ≥2 条生效; 后端不足 2 条自动回退两班制)
      shifts: (activeProject.value.shifts || [])
        .filter(s => (s.name || '').trim() && s.start)
        .map(s => ({ name: s.name.trim(), start: s.start })),
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

    // v3.10.2+ 热同步: 当前 active 项目就是这个项目时, 把新 pipeline/steps 推给所有 channel 的 mgr,
    // 否则用户改完配置点保存, mgr 内存里仍是旧 config (例如 disable_auto_settle 改了但运行中没生效).
    // 不阻塞主流程, 失败时只 warn.
    if (projectStore.currentProjectId === activeProject.value.id) {
      try {
        const syncPayload = {
          project_id: activeProject.value.id,
          name: activeProject.value.name,
          task_type: activeProject.value.task_type,
          logic_mode: activeProject.value.logic_mode,
          steps_config: data.steps_config,
          pipeline_config: data.pipeline_config,
          events_config: data.events_config,
          counters_config: data.counters_config,
          data_config: data.data_config,
        };
        // ch0 即可 (项目当前只激活一个 channel); 多 channel 后续按需扩展
        await setProjectConfig(syncPayload, 0);
      } catch (syncErr) {
        // 同步失败不影响主保存流程, 但提醒用户重启检测
        console.warn('[保存] 热同步到 mgr 失败:', syncErr);
        ElMessage.warning('配置已保存, 但同步给运行中的检测引擎失败, 请停止后重新启动检测以生效');
      }
    }

    // v3.27.x: 客户插件项目 Tab 注册的保存处理器随主保存一并触发 (插件不再单独放保存按钮)。
    // 通用遍历, 不认识具体插件; 单个插件保存失败做隔离, 不影响项目保存结果。
    for (const tab of (pluginProjectTabs.value || [])) {
      if (typeof tab.onSave === 'function') {
        try {
          await tab.onSave();
        } catch (e) {
          console.warn('[保存] 插件 Tab 保存失败:', tab.key, e);
          ElMessage.warning(`插件「${tab.label}」配置保存失败`);
        }
      }
    }

    dbg('project.crud', '保存项目成功', `id=${activeProject.value?.id}`);
    ElMessage.success('配置已保存');
  } catch (err) {
    dbgErr('project.crud', '保存项目', err);
    ElMessage.error('保存失败: ' + (err.response?.data?.detail || err.message));
  } finally {
    saving.value = false;
  }
};

// 删除项目
const handleDeleteProject = async () => {
  if (!activeProject.value) return;
  dbg('project.crud', '点击「删除项目」', `name=${activeProject.value?.name} id=${activeProject.value?.id}`);
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
    dbg('project.crud', '删除项目成功');
    ElMessage.success('项目已删除');
  } catch (err) {
    if (err !== 'cancel') {
      dbgErr('project.crud', '删除项目', err);
      ElMessage.error('删除失败');
    }
  }
};

// 复制项目：克隆当前项目全部配置为一个新副本, 复制后自动切到新副本继续编辑
const handleDuplicateProject = async () => {
  if (!activeProject.value) return;
  dbg('project.crud', '点击「复制项目」', `name=${activeProject.value?.name} id=${activeProject.value?.id}`);
  duplicating.value = true;
  try {
    const res = await duplicateProject(activeProject.value.id);
    const newProject = res.data;
    projects.value.push(newProject);
    await selectProject(newProject);
    dbg('project.crud', '复制项目成功', `newId=${newProject?.id} newName=${newProject?.name}`);
    ElMessage.success(`已复制为「${newProject.name}」`);
  } catch (err) {
    dbgErr('project.crud', '复制项目', err);
    ElMessage.error('复制项目失败');
  } finally {
    duplicating.value = false;
  }
};

// v3.19.x: 序列允许重复选择同一步骤（含连续重复, 如 放托盘×4）。
// 旧版"同一 step_id 只保留最新一行"的去重已删除 — 后端 v3.7.0 起支持非连续
// 重复, v3.19.x 起支持连续重复, UI 不再拦截。
const onSequenceStepPick = () => {
  syncDupDisappearDelay();
};

// 当前生效序列里"连续重复"的步骤 id 集合（顺序模式 / 自定义-基于顺序）
const consecutiveDupStepIds = computed(() => {
  const p = activeProject.value;
  const dup = new Set();
  if (!p) return dup;
  let seq = null;
  if (p.logic_mode === 'sequential') seq = p.sequence_order;
  else if (p.logic_mode === 'custom' && p.custom_based_on === 'sequential') seq = p.custom_sequence_order;
  if (!Array.isArray(seq)) return dup;
  for (let i = 1; i < seq.length; i++) {
    const a = seq[i - 1]?.step_id;
    const b = seq[i]?.step_id;
    if (a != null && a === b) dup.add(a);
  }
  return dup;
});

// 连续重复步骤的消失等待时间强制清 0（与后端 _apply_pipeline_config 兜底联动）：
// 连续 A→A 的辨认依赖"上一次出现立即走完消失结算"，等待时间会把第二次出现
// 当成第一次的延续导致漏计。
const syncDupDisappearDelay = () => {
  const p = activeProject.value;
  if (!p?.steps_config) return;
  const dup = consecutiveDupStepIds.value;
  if (dup.size === 0) return;
  for (const s of p.steps_config) {
    if (dup.has(s.id) && s.disappear_delay) {
      s.disappear_delay = 0;
    }
  }
};

watch(consecutiveDupStepIds, () => syncDupDisappearDelay());

const _nextStepId = (stepsConfig) => {
  let maxNum = 0;
  for (const s of stepsConfig || []) {
    const id = s?.id;
    if (typeof id === 'number' && Number.isFinite(id)) {
      maxNum = Math.max(maxNum, id);
    } else if (typeof id === 'string') {
      const m = id.match(/^s(\d+)$/i);
      if (m) maxNum = Math.max(maxNum, parseInt(m[1], 10));
    }
  }
  if (maxNum <= 0) return 1;
  const hasStringId = (stepsConfig || []).some(s => typeof s?.id === 'string' && /^s\d+$/i.test(s.id));
  return hasStringId ? `s${maxNum + 1}` : maxNum + 1;
};

/** 清理某副模型 slot 写入 steps_config 的步骤 (换选/删除副模型时用) */
const _purgeStepsByFromModel = (slotName) => {
  if (!slotName || !activeProject.value?.steps_config) return 0;
  const orphanSteps = activeProject.value.steps_config.filter(s => s && s.from_model === slotName);
  if (orphanSteps.length === 0) return 0;
  for (const step of orphanSteps) {
    try { applyStepEnabledChange(activeProject.value, step, false); } catch (_e) { /* continue */ }
  }
  const orphanIds = new Set(orphanSteps.map(s => s.id));
  activeProject.value.steps_config = activeProject.value.steps_config.filter(s => !orphanIds.has(s.id));
  return orphanSteps.length;
};

// 选择模型
const selectModel = (model) => {
  // Step 8: 副模型选择分支 (extraModelSelectingIdx >= 0)
  if (extraModelSelectingIdx.value >= 0) {
    const idx = extraModelSelectingIdx.value;
    const slot = activeProject.value?.extra_models?.[idx];
    if (slot) {
      // v3.14.x: 同 slot 换选副模型时先清旧标签, 再写入新模型标签 (之前只 append 不删, 旧标签残留)
      const removed = _purgeStepsByFromModel(slot.name);
      slot.model_id = model.id;
      slot.model_name = model.name;
      slot.model_version = model.version || '';
      slot.class_filter = [];
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
        let nextId = _nextStepId(sc);
        for (const lbl of slot.available_labels) {
          if (!lbl || existing.has(lbl)) continue;
          sc.push({
            id: nextId,
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
            box_max_width: 0,
            box_max_height: 0,
          });
          nextId = typeof nextId === 'number' ? nextId + 1 : _nextStepId(sc);
          appended += 1;
        }
      }
      const appendHint = appended > 0
        ? `, 已写入 ${appended} 个新标签`
        : (slot.available_labels.length ? ', 标签均已在步骤里' : '');
      const removedHint = removed > 0 ? ` (已替换 ${removed} 个旧标签)` : '';
      ElMessage.success(
        `副模型 [${slot.name}] 已选: ${model.name}${labelHint}${appendHint}${removedHint}`
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
    activeProject.value.model_labels = labels;
    // v3.32: 换模型重建步骤时保留副模型步骤 + 拆分虚拟步骤 (拆分规则不随模型选择清除)
    const extraSteps = (activeProject.value.steps_config || []).filter(
      s => s && ((s.from_model && s.from_model !== 'main') || s.split_origin)
    );
    const mainSteps = labels.map((label, idx) => ({
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
      box_max_width: 0,
      box_max_height: 0,
    }));
    activeProject.value.steps_config = [...mainSteps, ...extraSteps];

    // 自动初始化顺序: 主模型步骤 + 仍启用的副模型步骤
    activeProject.value.sequence_order = [
      ...mainSteps.map(s => ({ step_id: s.id })),
      ...extraSteps.filter(s => s.enabled !== false).map(s => ({ step_id: s.id })),
    ];
    activeProject.value.detection_steps = [
      ...mainSteps.map(s => s.id),
      ...extraSteps.filter(s => s.enabled !== false).map(s => s.id),
    ];
    
    ElMessage.success(`已选择模型: ${model.name}，识别到 ${labels.length} 个类别 (记得点右上角"保存配置"落库)`);
  } else {
    ElMessage.warning(`已选择模型: ${model.name}，但未能解析到标签 (记得点右上角"保存配置"落库)`);
  }
  
  showModelSelect.value = false;
  openFormatSelect();
};

// Step 8 (feat/multi-model-roi-link): 副模型管理方法
// addExtraModel / generateExtraModelDefaultName / extraModelSlotOptions / clearExtraModelRoi
// 已随基础设置 Tab 外置到 BasicSettingsTab.vue（2026-08）；removeExtraModel 因依赖
// _purgeStepsByFromModel（与 selectModel 共用）留父级，由子组件 emit 触发。
const removeExtraModel = async (idx) => {
  if (!activeProject.value?.extra_models) return;
  const slot = activeProject.value.extra_models[idx];
  if (!slot) return;

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
      return;
    }
    _purgeStepsByFromModel(slotName);
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

// 复用主 ROI 编辑器: 把当前副模型的 roi 作为初始 polygon 交给子组件预加载
const openExtraModelRoiEditor = async (idx) => {
  stepRoiEditingStepId.value = null;
  extraModelRoiEditingIdx.value = idx;
  await _openRoiDialog(activeProject.value?.extra_models?.[idx]?.roi);
};

const openStepRoiEditor = async (step) => {
  extraModelRoiEditingIdx.value = -1;
  stepRoiEditingStepId.value = step.id;
  await _openRoiDialog(step.roi);
};

// clearStepRoi 已随步骤设置 Tab 外置到 StepsConfigTab.vue（P-5）。

// ==================== v3.32 同标签区域拆分 + 工件就位提示 编排 ====================
// 规则数组挂 pipeline_config.label_splits; 编辑器 (LabelSplitDialog) 操作深拷贝,
// 点保存回写数组并立即同步虚拟步骤 (steps_config 带 split_origin 标记); 落库随主「保存配置」。
const labelSplitDialogVisible = ref(false);
const labelSplitDialogRef = ref(null);

const openLabelSplitEditor = async (ruleOrNull) => {
  if (!activeProject.value) return;
  const src = ruleOrNull || createDefaultSplitRule();
  const copy = JSON.parse(JSON.stringify(src));
  labelSplitDialogVisible.value = true;
  await nextTick();
  const channel = await resolveRoiSnapshotChannel();
  await labelSplitDialogRef.value?.load(channel, copy);
};

const handleLabelSplitSave = (savedRule) => {
  const ap = activeProject.value;
  if (!ap) return;
  if (!ap.pipeline_config) ap.pipeline_config = {};
  if (!Array.isArray(ap.pipeline_config.label_splits)) ap.pipeline_config.label_splits = [];
  const list = ap.pipeline_config.label_splits;
  // 同一原始标签只允许一条启用规则 (与后端解析语义一致), 前端在入口就拦
  if (savedRule.enabled !== false) {
    const dup = list.find(r => r && r.id !== savedRule.id && r.enabled !== false
      && r.source_label === savedRule.source_label);
    if (dup) {
      ElMessage.error(`原始标签「${savedRule.source_label}」已有启用的拆分规则, 请先停用/删除旧规则`);
      return;
    }
  }
  const idx = list.findIndex(r => r && r.id === savedRule.id);
  if (idx >= 0) list.splice(idx, 1, savedRule); else list.push(savedRule);
  const { added, removed } = syncSplitVirtualSteps(ap);
  labelSplitDialogVisible.value = false;
  const parts = [`拆分规则「${savedRule.source_label}」已更新`];
  if (added.length) parts.push(`新增虚拟步骤: ${added.join('、')}`);
  if (removed.length) parts.push(`移除虚拟步骤: ${removed.join('、')}`);
  parts.push('记得点右上角"保存配置"落库');
  ElMessage.success(parts.join('；'));
  dbg('project.config', '保存拆分规则', `source=${savedRule.source_label} regions=${(savedRule.regions || []).length} +${added.length}步骤 -${removed.length}步骤`);
};

const handleDeleteLabelSplit = async (rule) => {
  const ap = activeProject.value;
  if (!ap?.pipeline_config?.label_splits) return;
  try {
    await ElMessageBox.confirm(
      `删除拆分规则「${rule.source_label || rule.id}」将级联移除它生成的虚拟步骤（${(rule.regions || []).map(r => r.name).join('、') || '无'}）及其序列引用，确认删除？`,
      '删除拆分规则', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }
    );
  } catch { return; }
  const list = ap.pipeline_config.label_splits;
  const idx = list.findIndex(r => r && r.id === rule.id);
  if (idx >= 0) list.splice(idx, 1);
  const { removed } = syncSplitVirtualSteps(ap);
  ElMessage.success(removed.length ? `规则已删除, 虚拟步骤已移除: ${removed.join('、')}（记得保存配置）` : '规则已删除（记得保存配置）');
};

const openPlacementGuideEditor = async () => {
  resetRoiEditorTargets();
  placementGuideEditing.value = true;
  await _openRoiDialog(activeProject.value?.pipeline_config?.placement_guide?.polygon);
};

// v3.32+ 区域事件模式: 规则判定区域复用同一个 ROI 编辑器
const openRegionRoiEditor = async (ruleIdx) => {
  resetRoiEditorTargets();
  regionRuleEditingIdx.value = ruleIdx;
  await _openRoiDialog(activeProject.value?.pipeline_config?.region_events?.rules?.[ruleIdx]?.region);
};

// FORMAT_DISPLAY_NAMES / getFormatDisplayName 已外置 ./modelFormats.js（P-2, 与 FormatSelectDialog 共用）

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
  dbg('model.convert', '点击选择推理格式', `format=${key} 项目=${activeProject.value?.name}`);

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

    dbg('model.convert', '发起模型转换', `conversion_id=${conv?.id} format=${key}`);
    conversionId.value = conv.id;
    startConversionPolling(key);
  } catch (e) {
    dbgErr('model.convert', '发起模型转换', e);
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
        dbg('model.convert', '转换完成', `format=${fmtKey} conversion_id=${conversionId.value}`);
        stopConversionPolling();
        convertingFormat.value = null;
        _writeFormat(fmtKey);  // v3.7.x: 主/副模型分流写回
        showFormatSelect.value = false;
        ElMessage.success(`${getFormatDisplayName(fmtKey)} 转换完成`);
      } else if (s.status === 'failed') {
        dbg('model.convert', '转换失败', `format=${fmtKey} error=${s?.error_msg || '未知错误'}`);
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

// v3.7.x (FIX): isSettlementStep 此前硬编码读 sequence_order, 在 custom 模式
// (custom_based_on='sequential') 下完全错: 后端实际用 custom_sequence_order
// (见 backend/api/source_sequence_labels.py:_sequence_order), 而前端 UI 却
// 用残留的旧 sequence_order 算"结算步骤". 后果: UI 把不在自定义顺序里的步骤
// (如残留的"压墨") 错标成结算步骤, 禁用它的严格顺序/单次接受开关, 客户感知为
// "压墨怎么算最后一步".
// 修复: 与后端 _sequence_order() 对齐 — custom + custom_based_on=sequential
// 时读 custom_sequence_order, 其余走 sequence_order.
const _buildDefaultSequenceOrder = (stepsConfig) => (
  (stepsConfig || [])
    .filter(s => s.enabled !== false && !s.is_backup && !s.backup_for)
    .map(s => ({ step_id: s.id }))
);

const _activeSequenceOrder = () => {
  const ap = activeProject.value;
  if (!ap) return [];
  if (ap.logic_mode === 'custom' && ap.custom_based_on === 'sequential') {
    const seq = ap.custom_sequence_order || [];
    return seq.length > 0 ? seq : _buildDefaultSequenceOrder(ap.steps_config);
  }
  const seq = ap.sequence_order || [];
  return seq.length > 0 ? seq : _buildDefaultSequenceOrder(ap.steps_config);
};

/** 与后端 get_first/last_step_label 对齐: 在序列里找第一个/最后一个仍启用的 step_id */
const _getSettlementStepId = (mode) => {
  const ap = activeProject.value;
  if (!ap) return null;
  const seqOrder = _activeSequenceOrder();
  if (seqOrder.length === 0) return null;
  const enabledIds = new Set(
    (ap.steps_config || []).filter(s => s.enabled !== false).map(s => s.id)
  );
  if (mode === 'last_step') {
    for (let i = seqOrder.length - 1; i >= 0; i--) {
      const sid = seqOrder[i]?.step_id;
      if (sid != null && enabledIds.has(sid)) return sid;
    }
  } else if (mode === 'first_step') {
    for (const item of seqOrder) {
      const sid = item?.step_id;
      if (sid != null && enabledIds.has(sid)) return sid;
    }
  }
  return null;
};

const isSettlementStep = (step) => {
  const mode = activeProject.value?.settlement_mode || 'first_step';
  const logicMode = activeProject.value?.logic_mode;
  if (logicMode !== 'sequential' && !(logicMode === 'custom' && activeProject.value?.custom_based_on === 'sequential')) {
    return false;
  }
  if (mode === 'last_first') {
    return true;
  }
  const settleId = _getSettlementStepId(mode);
  return settleId != null && step.id === settleId;
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
  if (mode === 'last_first') {
    let cleared = 0;
    for (const step of activeProject.value.steps_config) {
      if (step.strict_order) {
        step.strict_order = false;
        cleared++;
      }
    }
    if (cleared > 0) {
      ElMessage.info(`已自动关闭 ${cleared} 个步骤的严格顺序（last_first 模式约束）`);
    }
    return;
  }
  const settlementStepId = _getSettlementStepId(mode);
  if (settlementStepId == null) return;
  const step = activeProject.value.steps_config.find(s => s.id === settlementStepId);
  if (!step) return;
  if (step.strict_order) step.strict_order = false;
  if (step.accept_once) step.accept_once = false;
});

// 序列/条件/同时组/周期性动作等编辑函数已随逻辑设置 Tab 外置到 LogicConfigTab.vue（P-4）。

// onStepEnabledChange（步骤启用状态清理/恢复）已随步骤设置 Tab 外置到 StepsConfigTab.vue（P-5）。

// 计数器操作
// addCounter / removeCounter 已随计数器侧栏外置到 CountersContextPanel.vue（2026-08）；
// defaultCounters / customCounters 切片 computed 留父级（EventsConfigTab 与侧栏共用）。

// 事件操作
// addEvent / removeEvent / addEventAction 已随事件设置 Tab 外置到 EventsConfigTab.vue（P-3）；
// 保养规则快捷建事件的 addEventAndBindToRule 已随逻辑设置 Tab 外置到 LogicConfigTab.vue（P-4）。
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

/* 步骤设置表: 横向滚动条加粗高亮 + 底部外边距把可视底边推回视口内,
   修复工控机矮屏下横向滚动条沉到视口外/被底部状态栏遮挡导致"划不动"的问题 */
.step-table-scroll {
  margin-bottom: 52px;
}
.step-table-scroll::-webkit-scrollbar {
  width: 8px;
  height: 14px;
}
.step-table-scroll::-webkit-scrollbar-track {
  background: #1e293b;
}
.step-table-scroll::-webkit-scrollbar-thumb {
  background: #06b6d4;
  border-radius: 7px;
}
.step-table-scroll::-webkit-scrollbar-thumb:hover {
  background: #22d3ee;
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
