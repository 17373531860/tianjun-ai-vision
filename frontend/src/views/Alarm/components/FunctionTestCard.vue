<template>
      <el-card shadow="never" class="bg-slate-800 border-slate-700">
        <template #header>
          <div class="flex items-center gap-2">
            <el-icon class="text-tech-blue"><VideoPlay /></el-icon>
            <span class="font-bold text-white">功能测试</span>
          </div>
        </template>
        
        <div class="space-y-4">
          <el-alert 
            v-if="!isConnected" 
            title="请先连接设备" 
            type="warning" 
            :closable="false"
            show-icon
          />
          
          <div v-else class="space-y-3">
            <div class="flex items-center justify-between p-3 bg-slate-900 rounded border" :class="config.test_mode ? 'border-green-500' : 'border-slate-700'">
              <div>
                <div class="text-gray-300">测试模式</div>
                <div class="text-xs text-gray-500">开启后，下方测试按钮才能使用</div>
              </div>
              <el-switch v-model="config.test_mode" @change="saveConfig" />
            </div>
            
            <div class="text-xs text-gray-400 mb-1">灯光测试</div>
            <div class="grid grid-cols-4 gap-2">
              <el-button size="small" style="background:#ef4444;border:none;color:white" @click="testAction('red_on')" :disabled="!config.test_mode">红灯亮</el-button>
              <el-button size="small" style="background:#22c55e;border:none;color:white" @click="testAction('green_on')" :disabled="!config.test_mode">绿灯亮</el-button>
              <el-button size="small" style="background:#3b82f6;border:none;color:white" @click="testAction('blue_on')" :disabled="!config.test_mode">蓝灯亮</el-button>
              <el-button size="small" style="background:#eab308;border:none;color:black" @click="testAction('yellow_on')" :disabled="!config.test_mode">黄灯亮</el-button>
            </div>
            <div class="grid grid-cols-4 gap-2">
              <el-button size="small" @click="testAction('red_off')" :disabled="!config.test_mode">红灯灭</el-button>
              <el-button size="small" @click="testAction('green_off')" :disabled="!config.test_mode">绿灯灭</el-button>
              <el-button size="small" @click="testAction('blue_off')" :disabled="!config.test_mode">蓝灯灭</el-button>
              <el-button size="small" @click="testAction('yellow_off')" :disabled="!config.test_mode">黄灯灭</el-button>
            </div>
            
            <div class="text-xs text-gray-400 mb-1 mt-2">蜂鸣器测试</div>
            <div class="grid grid-cols-2 gap-3">
              <el-button 
                type="warning" 
                @click="testAction('buzzer_on')" 
                :disabled="!config.test_mode"
              >
                <el-icon class="mr-1"><Bell /></el-icon> 开蜂鸣
              </el-button>
              <el-button 
                type="info" 
                @click="testAction('buzzer_off')" 
                :disabled="!config.test_mode"
              >
                <el-icon class="mr-1"><MuteNotification /></el-icon> 关蜂鸣
              </el-button>
            </div>
            
            <div class="flex gap-3 mt-2">
              <el-button type="danger" @click="testAction('red_buzzer_fast')" class="flex-1" :disabled="!config.test_mode">
                红灯快闪+蜂鸣
              </el-button>
              <el-button @click="testAction('all_off')" class="flex-1" :disabled="!config.test_mode">
                全部关闭
              </el-button>
            </div>
          </div>
        </div>
      </el-card>
</template>

<script setup>
import { ref } from 'vue';
import { ElMessage } from 'element-plus';
import { VideoPlay, Bell, MuteNotification } from '@element-plus/icons-vue';
import api from '@/api/index';
import { dbg, dbgErr } from '@/utils/debug';

const props = defineProps({
  config: {
    type: Object,
    required: true,
  },
  isConnected: {
    type: Boolean,
    required: true,
  },
  activeChannel: {
    type: Number,
    required: true,
  },
});

const emit = defineEmits(['save']);

const testing = ref('');

const chParam = () => `?channel=${props.activeChannel}`;

const saveConfig = () => {
  emit('save');
};

const testAction = async (action) => {
  testing.value = action;
  dbg('alarm.ops', '点击测试动作', `ch=${props.activeChannel} action=${action ?? ''}`);
  try {
    await api.post(`/alarm/test${chParam()}`, { action });
    ElMessage.success(`已发送: ${action}`);
  } catch (err) {
    dbgErr('alarm.ops', '测试动作', err);
    ElMessage.error(err.response?.data?.detail || '测试失败');
  } finally {
    testing.value = '';
  }
};
</script>
