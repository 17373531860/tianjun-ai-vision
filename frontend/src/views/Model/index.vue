<template>
  <TjSlot name="model.layout.body">
  <div class="p-6 space-y-6">
    <div class="flex justify-between items-center">
      <h2 class="text-2xl font-bold border-l-4 border-tech-blue pl-3">模型仓库 (Model Repository)</h2>
      <el-button type="primary" @click="uploadDialogVisible = true">
        <el-icon class="mr-1"><Upload /></el-icon> 上传新模型
      </el-button>
    </div>
    <div class="text-xs text-gray-500 -mt-3">模型仓库为全局资源，跨项目共享；上传的模型可被任意项目引用，不随当前项目切换。</div>

    <div v-loading="loading" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
      <div v-if="modelList.length === 0 && !loading" class="col-span-4 text-center text-gray-500 py-16">
        <el-icon :size="64" class="mb-4"><Cpu /></el-icon>
        <p>暂无模型，点击上方按钮上传</p>
      </div>
      
      <div v-for="model in modelList" :key="model.id" 
           class="bg-ind-panel border border-gray-800 rounded-xl p-5 hover:border-tech-blue transition-all group">
        <div class="flex justify-between items-start mb-4">
          <div class="p-3 bg-tech-blue/10 rounded-lg text-tech-blue">
            <el-icon :size="24"><Cpu /></el-icon>
          </div>
          <div class="flex gap-1 items-center">
            <el-tag v-if="model.source === 'yolovision'" type="warning" effect="dark" size="small">训练平台</el-tag>
            <el-tag :type="model.status === 'active' ? 'success' : 'info'" effect="dark" size="small">
              {{ model.status === 'active' ? '使用中' : '闲置' }}
            </el-tag>
          </div>
        </div>
        
        <h3 class="text-lg font-bold truncate" :title="model.name">{{ model.name }}</h3>
        <div class="mt-3 space-y-1 text-xs text-gray-400 font-mono">
          <p>版本: {{ model.version || 'N/A' }}</p>
          <p>大小: {{ formatFileSize(model.file_size) }}</p>
          <p>框架: {{ model.framework }}</p>
          <p>类别: {{ getLabelsCount(model.labels) }} 个</p>
          <p>上传: {{ formatDate(model.upload_time) }}</p>
        </div>

        <div class="mt-6 flex gap-2">
          <el-button size="small" class="flex-1" @click="handleEdit(model)">详情</el-button>
          <el-button v-if="model.meta && model.meta.analysis" size="small" type="primary" plain @click="showAnalysis(model)">训练分析</el-button>
          <el-button size="small" type="danger" plain @click="confirmDelete(model)">删除</el-button>
        </div>
      </div>
    </div>

    <!-- Upload Dialog -->
    <el-dialog v-model="uploadDialogVisible" title="模型上传向导" width="500px" destroy-on-close>
      <div class="space-y-4">
        <el-form label-position="top">
          <el-form-item label="模型名称" required>
            <el-input v-model="uploadForm.name" placeholder="例如: YOLOv8 缺陷检测模型" />
          </el-form-item>
          <el-form-item label="版本号">
            <el-input v-model="uploadForm.version" placeholder="例如: V1.0.0" />
          </el-form-item>
          <el-form-item label="框架类型">
            <el-select v-model="uploadForm.framework" class="w-full">
              <el-option label="PyTorch (.pt)" value="PyTorch" />
              <el-option label="ONNX (.onnx)" value="ONNX" />
              <el-option label="TensorRT (.engine)" value="TensorRT" />
            </el-select>
          </el-form-item>
          <el-form-item label="描述">
            <el-input v-model="uploadForm.description" type="textarea" :rows="2" placeholder="模型用途描述" />
          </el-form-item>
        </el-form>
        
        <el-upload
          ref="uploadRef"
          drag
          action="#"
          :auto-upload="false"
          :on-change="handleFileChange"
          :on-exceed="handleExceed"
          :limit="1"
          accept=".pt,.pth,.onnx,.engine,.pkl"
        >
          <el-icon class="el-icon--upload"><upload-filled /></el-icon>
          <div class="el-upload__text">
            拖拽模型文件 (.pt, .onnx, .engine) 到此处或 <em>点击上传</em>
          </div>
          <template #tip>
            <div class="el-upload__tip">
              支持 PyTorch, ONNX, TensorRT 格式，文件大小不超过 500MB
            </div>
          </template>
        </el-upload>

        <div v-if="uploadProgress > 0" class="mt-4">
          <div class="flex justify-between text-xs mb-1">
            <span>正在上传...</span>
            <span>{{ uploadProgress }}%</span>
          </div>
          <el-progress :percentage="uploadProgress" :show-text="false" color="#3b82f6" />
        </div>
      </div>
      <template #footer>
        <el-button @click="uploadDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="isUploading" @click="startUpload" :disabled="!selectedFile || !uploadForm.name">
          开始上传
        </el-button>
      </template>
    </el-dialog>

    <!-- Detail / Edit Dialog -->
    <el-dialog v-model="detailDialogVisible" title="模型详情" width="500px">
      <div v-if="currentModel" class="space-y-4">
        <div class="flex items-center gap-4">
          <div class="w-16 h-16 bg-tech-blue/10 rounded-lg flex items-center justify-center">
            <el-icon :size="32" class="text-tech-blue"><Cpu /></el-icon>
          </div>
          <div class="flex-1">
            <p class="text-gray-400 text-sm">{{ currentModel.file_name }}</p>
          </div>
        </div>
        
        <el-form label-position="top" class="mt-4">
          <el-form-item label="模型名称">
            <el-input v-model="editForm.name" placeholder="输入模型名称" />
          </el-form-item>
          <el-form-item label="版本号">
            <el-input v-model="editForm.version" placeholder="例如: V1.0.0" />
          </el-form-item>
          <el-form-item label="描述">
            <el-input v-model="editForm.description" type="textarea" :rows="2" placeholder="模型用途描述" />
          </el-form-item>
        </el-form>

        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="框架">{{ currentModel.framework }}</el-descriptions-item>
          <el-descriptions-item label="文件大小">{{ formatFileSize(currentModel.file_size) }}</el-descriptions-item>
          <el-descriptions-item label="上传时间">{{ formatDateTime(currentModel.upload_time) }}</el-descriptions-item>
          <el-descriptions-item label="状态">
            <el-tag :type="currentModel.status === 'active' ? 'success' : 'info'" size="small">
              {{ currentModel.status === 'active' ? '使用中' : '闲置' }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="类别数">{{ getLabelsCount(currentModel.labels) }} 个</el-descriptions-item>
          <el-descriptions-item label="来源">
            {{ currentModel.source === 'yolovision' ? 'YoloVision 训练平台' : '本地上传' }}
          </el-descriptions-item>
        </el-descriptions>
      </div>
      <template #footer>
        <el-button @click="detailDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="isSaving" @click="saveModel">保存</el-button>
      </template>
    </el-dialog>

    <!-- v3.47 训练平台互连: 训练分析弹窗 (数据来自 .yvmodel 包 x-analysis 扩展) -->
    <el-dialog v-model="analysisDialogVisible" title="训练分析 (来自 YoloVision 训练平台)" width="720px" destroy-on-close>
      <div v-if="analysisModel" class="space-y-5">
        <div class="text-sm text-gray-400">
          {{ analysisModel.name }} <span v-if="analysisModel.version">v{{ analysisModel.version }}</span>
          <el-tag v-if="analysis.training && analysis.training.trigger === 'auto_retrain'" type="warning" size="small" class="ml-2">自动增强训练产出</el-tag>
        </div>

        <!-- 核心指标 -->
        <div v-if="analysis.metrics" class="grid grid-cols-4 gap-3 text-center">
          <div v-for="m in metricCells" :key="m.label" class="bg-gray-900/50 rounded-lg py-3">
            <div class="text-xl font-bold text-tech-blue">{{ m.value }}</div>
            <div class="text-xs text-gray-500 mt-1">{{ m.label }}</div>
          </div>
        </div>

        <!-- 训练信息 -->
        <el-descriptions v-if="analysis.training" :column="2" border size="small">
          <el-descriptions-item v-if="analysis.training.base_model" label="基线模型">{{ analysis.training.base_model }}</el-descriptions-item>
          <el-descriptions-item v-if="analysis.training.epochs" label="训练轮数">{{ analysis.training.epochs }}</el-descriptions-item>
          <el-descriptions-item v-if="analysis.training.imgsz" label="输入尺寸">{{ analysis.training.imgsz }}</el-descriptions-item>
          <el-descriptions-item v-if="analysis.training.finished_at" label="完成时间">{{ analysis.training.finished_at }}</el-descriptions-item>
          <el-descriptions-item v-if="analysis.dataset" label="数据集 (训/验/测)">
            {{ analysis.dataset.train ?? '-' }} / {{ analysis.dataset.val ?? '-' }} / {{ analysis.dataset.test ?? '-' }}
          </el-descriptions-item>
          <el-descriptions-item v-if="analysis.dataset && analysis.dataset.field_feedback_samples != null" label="现场回流样本">
            {{ analysis.dataset.field_feedback_samples }} 张
          </el-descriptions-item>
        </el-descriptions>

        <!-- 训练曲线 -->
        <div v-if="hasCurves">
          <div class="text-sm text-gray-300 mb-2">训练曲线</div>
          <div ref="curveChartRef" class="w-full h-64"></div>
        </div>

        <!-- 逐类别指标 -->
        <div v-if="analysis.per_class && analysis.per_class.length">
          <div class="text-sm text-gray-300 mb-2">逐类别指标</div>
          <el-table :data="analysis.per_class" size="small" max-height="240">
            <el-table-column prop="name" label="类别" min-width="120" />
            <el-table-column label="AP50" width="100">
              <template #default="{ row }">{{ fmtPct(row.ap50) }}</template>
            </el-table-column>
            <el-table-column label="精确率" width="100">
              <template #default="{ row }">{{ fmtPct(row.precision) }}</template>
            </el-table-column>
            <el-table-column label="召回率" width="100">
              <template #default="{ row }">{{ fmtPct(row.recall) }}</template>
            </el-table-column>
          </el-table>
        </div>
      </div>
      <template #footer>
        <el-button @click="analysisDialogVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
  </TjSlot>
</template>

<script setup>
import TjSlot from '@/components/TjSlot.vue';
import { ref, computed, nextTick, onMounted } from 'vue';
import { ElMessageBox, ElMessage } from 'element-plus';
import { UploadFilled, Cpu, Upload } from '@element-plus/icons-vue';
import * as echarts from 'echarts';
import { getModels, uploadModel, updateModel, deleteModel } from '@/api/model';
import { dbg, dbgErr } from '@/utils/debug';

const modelList = ref([]);
const loading = ref(false);
const uploadDialogVisible = ref(false);
const detailDialogVisible = ref(false);
const uploadProgress = ref(0);
const isUploading = ref(false);
const selectedFile = ref(null);
const currentModel = ref(null);
const uploadRef = ref(null);
const isSaving = ref(false);
const editForm = ref({ name: '', version: '', description: '' });

const uploadForm = ref({
  name: '',
  version: '',
  framework: 'PyTorch',
  description: ''
});

// 加载模型列表
const loadModels = async () => {
  dbg('model.manage', '刷新模型列表');
  loading.value = true;
  try {
    const res = await getModels();
    modelList.value = res.data.items || [];
  } catch (err) {
    dbgErr('model.manage', '刷新模型列表', err);
    console.error('加载模型列表失败:', err);
    ElMessage.error('加载模型列表失败');
  } finally {
    loading.value = false;
  }
};

onMounted(() => {
  loadModels();
});

const formatFileSize = (bytes) => {
  if (!bytes) return '0 B';
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(2) + ' KB';
  return (bytes / 1024 / 1024).toFixed(2) + ' MB';
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

const formatDate = (dateStr) => {
  if (!dateStr) return '';
  return new Date(dateStr).toLocaleDateString();
};

const formatDateTime = (dateStr) => {
  if (!dateStr) return '';
  return new Date(dateStr).toLocaleString();
};

const handleFileChange = (file) => {
  dbg('model.upload', '选择模型文件', `name=${file?.name} size=${file?.size}`);
  selectedFile.value = file.raw;
  // 自动从文件名提取模型名称
  if (!uploadForm.value.name && file.name) {
    uploadForm.value.name = file.name.replace(/\.(pt|pth|onnx|engine|pkl)$/i, '');
  }
};

// 超出文件数量限制时，用新文件替换旧文件
const handleExceed = (files) => {
  dbg('model.upload', '重复选择文件(替换旧文件)', `name=${files?.[0]?.name}`);
  if (uploadRef.value) {
    uploadRef.value.clearFiles();
  }
  const newFile = files[0];
  if (newFile) {
    selectedFile.value = newFile;
    // 更新模型名称为新文件名
    uploadForm.value.name = newFile.name.replace(/\.(pt|pth|onnx|engine|pkl)$/i, '');
    // 手动将新文件添加到 el-upload 的文件列表中显示
    if (uploadRef.value) {
      uploadRef.value.handleStart(newFile);
    }
  }
};

const startUpload = async () => {
  if (!selectedFile.value) {
    ElMessage.warning('请先选择文件');
    return;
  }
  if (!uploadForm.value.name) {
    ElMessage.warning('请输入模型名称');
    return;
  }
  
  const formData = new FormData();
  formData.append('file', selectedFile.value);
  formData.append('name', uploadForm.value.name);
  formData.append('version', uploadForm.value.version || '');
  formData.append('framework', uploadForm.value.framework);
  formData.append('description', uploadForm.value.description || '');
  
  dbg('model.upload', '点击「开始上传」', `name=${uploadForm.value?.name} framework=${uploadForm.value?.framework} file=${selectedFile.value?.name}`);
  isUploading.value = true;
  uploadProgress.value = 0;
  
  try {
    await uploadModel(formData, (progress) => {
      uploadProgress.value = progress;
    });
    dbg('model.upload', '模型上传成功', `name=${uploadForm.value?.name}`);
    ElMessage.success('模型上传成功');
    uploadDialogVisible.value = false;
    
    // 重置表单
    uploadForm.value = { name: '', version: '', framework: 'PyTorch', description: '' };
    selectedFile.value = null;
    if (uploadRef.value) {
      uploadRef.value.clearFiles();
    }
    
    // 重新加载列表
    loadModels();
  } catch (err) {
    dbgErr('model.upload', '模型上传', err);
    ElMessage.error('上传失败: ' + (err.response?.data?.detail || err.message));
  } finally {
    isUploading.value = false;
    uploadProgress.value = 0;
  }
};

const handleEdit = (model) => {
  dbg('model.manage', '点击「编辑模型」', `id=${model?.id} name=${model?.name}`);
  currentModel.value = model;
  editForm.value = {
    name: model.name || '',
    version: model.version || '',
    description: model.description || ''
  };
  detailDialogVisible.value = true;
};

const saveModel = async () => {
  if (!currentModel.value) return;
  if (!editForm.value.name) {
    ElMessage.warning('模型名称不能为空');
    return;
  }
  dbg('model.manage', '点击「保存模型信息」', `id=${currentModel.value?.id} name=${editForm.value?.name}`);
  isSaving.value = true;
  try {
    await updateModel(currentModel.value.id, {
      name: editForm.value.name,
      version: editForm.value.version || null,
      description: editForm.value.description || null
    });
    ElMessage.success('模型信息已更新');
    detailDialogVisible.value = false;
    loadModels();
  } catch (err) {
    dbgErr('model.manage', '保存模型信息', err);
    ElMessage.error('更新失败: ' + (err.response?.data?.detail || err.message));
  } finally {
    isSaving.value = false;
  }
};

// ==================== v3.47 训练分析弹窗 (x-analysis) ====================
const analysisDialogVisible = ref(false);
const analysisModel = ref(null);
const curveChartRef = ref(null);
let curveChart = null;

const analysis = computed(() => analysisModel.value?.meta?.analysis || {});

const fmtPct = (v) => (v == null || v === '' ? '—' : (Number(v) * 100).toFixed(1) + '%');

const metricCells = computed(() => {
  const m = analysis.value.metrics || {};
  return [
    { label: 'mAP@50', value: fmtPct(m.map50) },
    { label: 'mAP@50-95', value: fmtPct(m.map50_95) },
    { label: '精确率', value: fmtPct(m.precision) },
    { label: '召回率', value: fmtPct(m.recall) },
  ];
});

const hasCurves = computed(() => {
  const c = analysis.value.curves || {};
  return Array.isArray(c.epochs) && c.epochs.length > 0;
});

const showAnalysis = (model) => {
  dbg('model.manage', '点击「训练分析」', `id=${model?.id} name=${model?.name}`);
  analysisModel.value = model;
  analysisDialogVisible.value = true;
  nextTick(() => renderCurveChart());
};

const renderCurveChart = () => {
  if (!hasCurves.value || !curveChartRef.value) return;
  const c = analysis.value.curves;
  if (curveChart) {
    curveChart.dispose();
    curveChart = null;
  }
  curveChart = echarts.init(curveChartRef.value);
  const series = [];
  const legends = [];
  const addLine = (key, name, yAxisIndex = 0) => {
    if (Array.isArray(c[key]) && c[key].length) {
      series.push({ name, type: 'line', smooth: true, data: c[key], yAxisIndex, showSymbol: false });
      legends.push(name);
    }
  };
  addLine('train_loss', '训练 Loss', 0);
  addLine('val_loss', '验证 Loss', 0);
  addLine('map50', 'mAP@50', 1);
  curveChart.setOption({
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis' },
    legend: { data: legends, textStyle: { color: '#94a3b8' } },
    grid: { left: 48, right: 48, top: 36, bottom: 28 },
    xAxis: { type: 'category', data: c.epochs, name: 'epoch', axisLabel: { color: '#64748b' } },
    yAxis: [
      { type: 'value', name: 'Loss', axisLabel: { color: '#64748b' }, splitLine: { lineStyle: { color: '#1e293b' } } },
      { type: 'value', name: 'mAP', min: 0, max: 1, axisLabel: { color: '#64748b' }, splitLine: { show: false } },
    ],
    series,
  });
};

const confirmDelete = async (model) => {
  dbg('model.manage', '点击「删除模型」', `id=${model?.id} name=${model?.name}`);
  try {
    await ElMessageBox.confirm(
      `确认删除模型 "${model.name}" 吗? 此操作无法撤销。`,
      '警告',
      {
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        type: 'warning'
      }
    );
    
    await deleteModel(model.id);
    ElMessage.success('模型已删除');
    loadModels();
  } catch (err) {
    if (err !== 'cancel') {
      dbgErr('model.manage', '删除模型', err);
      ElMessage.error('删除失败');
    }
  }
};
</script>
