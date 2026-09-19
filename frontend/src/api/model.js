// src/api/model.js
import api from './index';

// 获取所有模型列表
export const getModels = (params) => api.get('/models', { params });

// 获取模型详情
export const getModelDetail = (id) => api.get(`/models/${id}`);

// 上传模型，包含进度回调
export const uploadModel = (formData, onProgress) => {
  return api.post('/models/upload', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 300000, // 5分钟超时
    onUploadProgress: (progressEvent) => {
      const percentCompleted = Math.round((progressEvent.loaded * 100) / progressEvent.total);
      if (onProgress) onProgress(percentCompleted);
    }
  });
};

// 更新模型信息
export const updateModel = (id, data) => api.put(`/models/${id}`, data);

// 删除模型
export const deleteModel = (id) => api.delete(`/models/${id}`);

// 设置模型为激活状态
export const setModelActive = (id) => api.post(`/models/${id}/set-active`);

// 重新解析模型标签
export const parseModelLabels = (id) => api.post(`/models/${id}/parse-labels`);

// 获取可用模型格式列表 + 智能推荐
export const getAvailableFormats = () => api.get('/models/formats/available');

// 获取 GPU / TensorRT 环境诊断
export const getFormatDiagnosis = () => api.get('/models/formats/diagnosis');

// 发起模型格式转换
export const convertModel = (id, data) => api.post(`/models/${id}/convert`, data);

// 查询转换状态
export const getConversionStatus = (convId) => api.get(`/models/conversions/${convId}/status`);

// 获取模型所有转换版本
export const getModelConversions = (id) => api.get(`/models/${id}/conversions`);

// 删除转换记录
export const deleteConversion = (convId) => api.delete(`/models/conversions/${convId}`);

// ==================== 2026-09 内置能力模型入仓 ====================

// 能力目录: 类型元信息 + 内置行 + 当前绑定 + 引擎可用性 (模型页/项目挂件选择器共用)
export const getCapabilityCatalog = () => api.get('/models/capabilities');

// 绑定用户模型为某能力当前权重 (modelId=null 恢复出厂默认); 引擎热重载
export const bindCapability = (capability, modelId) =>
  api.post(`/models/capabilities/${capability}/bind`, { model_id: modelId });

// 解析实际模型路径（根据项目格式配置）
export const resolveModelPath = (modelId, format) => api.post(`/models/${modelId}/resolve-path?format=${format}`);