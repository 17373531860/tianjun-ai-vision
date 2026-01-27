// src/api/project.js
import api from './index';

// 获取项目列表
export const getProjects = (params) => api.get('/projects', { params });

// 获取项目详情
export const getProjectDetail = (id) => api.get(`/projects/${id}`);

// 创建项目
export const createProject = (data) => api.post('/projects', data);

// 更新项目
export const updateProject = (id, data) => api.put(`/projects/${id}`, data);

// 删除项目
export const deleteProject = (id) => api.delete(`/projects/${id}`);

// 激活项目
export const activateProject = (id) => api.post(`/projects/${id}/activate`);

// 获取当前激活的项目
export const getActiveProject = () => api.get('/projects/active/current');

// 获取可用模型列表 (用于下拉选择)
export const getAvailableModels = () => api.get('/models');