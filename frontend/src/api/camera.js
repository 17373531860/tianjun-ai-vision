import api, { getBackendHost } from './index';

// 获取相机列表
export const getCameras = () => api.get('/cameras');

// 获取相机详情
export const getCameraDetail = (id) => api.get(`/cameras/${id}`);

// 创建相机配置
export const createCamera = (data) => api.post('/cameras', data);

// 更新相机配置
export const updateCamera = (id, data) => api.put(`/cameras/${id}`, data);

// 删除相机配置
export const deleteCamera = (id) => api.delete(`/cameras/${id}`);

// 测试相机连接
export const testCamera = (id) => api.get(`/cameras/${id}/test`);

// 获取相机视频流URL
export const getCameraStreamUrl = (id) => `${getBackendHost()}/api/v1/cameras/${id}/stream`;

// 获取默认相机视频流URL
export const getDefaultStreamUrl = () => `${getBackendHost()}/video_feed`;
