// src/api/interconnect.js — v3.47 训练平台互连 (/api/v1/interconnect/*)
import api from './index';

// 读取互连配置
export const getInterconnectConfig = () => api.get('/interconnect/config');

// 保存互连配置
export const saveInterconnectConfig = (data) => api.put('/interconnect/config', data);

// 运行状态 (队列/上传 worker/采样)
export const getInterconnectStatus = () => api.get('/interconnect/status');

// 探活训练平台 (可传 { platform_url } 用未保存的地址试连)
export const testInterconnectConnection = (data) => api.post('/interconnect/test-connection', data || {});

// 最近采样回传流水
export const getRecentSamples = (limit = 50) => api.get('/interconnect/samples/recent', { params: { limit } });

// 立即向训练平台拉取一轮新模型包 (pull 分发模式)
export const pullModelsNow = () => api.post('/interconnect/pull-now');
