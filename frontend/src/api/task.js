// src/api/task.js
import api from './index';

// 获取任务列表
export const getTasks = (params) => api.get('/tasks', { params });

// 获取任务详情
export const getTaskDetail = (id) => api.get(`/tasks/${id}`);

// 创建任务
export const createTask = (data) => api.post('/tasks', data);

// 更新任务
export const updateTask = (id, data) => api.put(`/tasks/${id}`, data);

// 删除任务
export const deleteTask = (id) => api.delete(`/tasks/${id}`);

// 记录检测结果（带图片上传）
export const recordDetection = (formData) => api.post('/tasks/record', formData, {
  headers: { 'Content-Type': 'multipart/form-data' }
});

// 批量清除任务记录
export const clearTasks = (params) => api.delete('/tasks/batch/clear', { params });
