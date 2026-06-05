// RFC 10 CG: Channel Group (工位组/双工位互通) REST API client
// 与 backend/api/channel_groups.py 对齐 (前缀 /api/v1/channel-groups)
import api from './index';

export const listChannelGroups = () => api.get('/channel-groups');
export const createChannelGroup = (payload) => api.post('/channel-groups', payload);
export const getChannelGroup = (id) => api.get(`/channel-groups/${id}`);
export const updateChannelGroup = (id, payload) => api.put(`/channel-groups/${id}`, payload);
export const deleteChannelGroup = (id) => api.delete(`/channel-groups/${id}`);
export const getChannelGroupState = (id) => api.get(`/channel-groups/${id}/state`);
