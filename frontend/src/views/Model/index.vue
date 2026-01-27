<template>
  <div class="p-6 space-y-6">
    <div class="flex justify-between items-center">
      <h2 class="text-2xl font-bold border-l-4 border-tech-blue pl-3">模型仓库 (Model Repository)</h2>
      <el-button type="primary" @click="uploadDialogVisible = true">
        <el-icon class="mr-1"><Upload /></el-icon> 上传新模型
      </el-button>
    </div>

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
          <el-tag :type="model.status === 'active' ? 'success' : 'info'" effect="dark" size="small">
            {{ model.status === 'active' ? '使用中' : '闲置' }}
          </el-tag>
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

    <!-- Detail Dialog -->
    <el-dialog v-model="detailDialogVisible" title="模型详情" width="500px">
      <div v-if="currentModel" class="space-y-4">
        <div class="flex items-center gap-4">
          <div class="w-16 h-16 bg-tech-blue/10 rounded-lg flex items-center justify-center">
            <el-icon :size="32" class="text-tech-blue"><Cpu /></el-icon>
          </div>
          <div>
            <h3 class="text-xl font-bold">{{ currentModel.name }}</h3>
            <p class="text-gray-400 text-sm">{{ currentModel.file_name }}</p>
          </div>
        </div>
        
        <el-descriptions :column="1" border>
          <el-descriptions-item label="版本">{{ currentModel.version || 'N/A' }}</el-descriptions-item>
          <el-descriptions-item label="框架">{{ currentModel.framework }}</el-descriptions-item>
          <el-descriptions-item label="文件大小">{{ formatFileSize(currentModel.file_size) }}</el-descriptions-item>
          <el-descriptions-item label="上传时间">{{ formatDateTime(currentModel.upload_time) }}</el-descriptions-item>
          <el-descriptions-item label="状态">
            <el-tag :type="currentModel.status === 'active' ? 'success' : 'info'">
              {{ currentModel.status === 'active' ? '使用中' : '闲置' }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="描述">{{ currentModel.description || '无' }}</el-descriptions-item>
        </el-descriptions>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue';
import { ElMessageBox, ElMessage } from 'element-plus';
import { UploadFilled, Cpu, Upload } from '@element-plus/icons-vue';
import { getModels, uploadModel, deleteModel } from '@/api/model';

const modelList = ref([]);
const loading = ref(false);
const uploadDialogVisible = ref(false);
const detailDialogVisible = ref(false);
const uploadProgress = ref(0);
const isUploading = ref(false);
const selectedFile = ref(null);
const currentModel = ref(null);
const uploadRef = ref(null);

const uploadForm = ref({
  name: '',
  version: '',
  framework: 'PyTorch',
  description: ''
});

// 加载模型列表
const loadModels = async () => {
  loading.value = true;
  try {
    const res = await getModels();
    modelList.value = res.data.items || [];
  } catch (err) {
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
  selectedFile.value = file.raw;
  // 自动从文件名提取模型名称
  if (!uploadForm.value.name && file.name) {
    uploadForm.value.name = file.name.replace(/\.(pt|pth|onnx|engine|pkl)$/i, '');
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
  
  isUploading.value = true;
  uploadProgress.value = 0;
  
  try {
    await uploadModel(formData, (progress) => {
      uploadProgress.value = progress;
    });
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
    ElMessage.error('上传失败: ' + (err.response?.data?.detail || err.message));
  } finally {
    isUploading.value = false;
    uploadProgress.value = 0;
  }
};

const handleEdit = (model) => {
  currentModel.value = model;
  detailDialogVisible.value = true;
};

const confirmDelete = async (model) => {
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
      ElMessage.error('删除失败');
    }
  }
};
</script>
