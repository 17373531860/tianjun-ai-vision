<template>
      <el-card shadow="never" class="bg-slate-800 border-slate-700">
        <template #header>
          <div class="flex items-center gap-2">
            <el-icon class="text-tech-blue"><Setting /></el-icon>
            <span class="font-bold text-white">协议设置</span>
          </div>
        </template>
        
        <div class="space-y-4">
          <div>
            <div class="text-gray-300 mb-2">通信协议</div>
            <el-select v-model="config.protocol" class="w-full" @change="saveConfig">
              <el-option 
                v-for="p in protocols" 
                :key="p.id" 
                :label="p.name" 
                :value="p.id"
              />
            </el-select>
            <div class="text-xs text-gray-500 mt-1">
              如果不确定，请逐个尝试不同协议
            </div>
          </div>
          
          <div v-if="config.protocol === 'custom'" class="space-y-3">
            <div class="text-gray-300 text-sm">自定义命令（支持十六进制，如：A0 01 01 A2）</div>
            <div class="grid grid-cols-2 gap-3">
              <div>
                <div class="text-xs text-gray-400 mb-1">开灯命令</div>
                <el-input v-model="config.custom_commands.light_on" size="small" placeholder="如: 1 或 A0 01" @change="saveConfig" />
              </div>
              <div>
                <div class="text-xs text-gray-400 mb-1">关灯命令</div>
                <el-input v-model="config.custom_commands.light_off" size="small" placeholder="如: 0 或 A0 00" @change="saveConfig" />
              </div>
              <div>
                <div class="text-xs text-gray-400 mb-1">开蜂鸣命令</div>
                <el-input v-model="config.custom_commands.buzzer_on" size="small" placeholder="如: 2" @change="saveConfig" />
              </div>
              <div>
                <div class="text-xs text-gray-400 mb-1">关蜂鸣命令</div>
                <el-input v-model="config.custom_commands.buzzer_off" size="small" placeholder="如: 3" @change="saveConfig" />
              </div>
            </div>
          </div>
        </div>
      </el-card>
</template>

<script setup>
import { ref, onMounted } from 'vue';
import { Setting } from '@element-plus/icons-vue';
import api from '@/api/index';

defineProps({
  config: {
    type: Object,
    required: true,
  },
});

const emit = defineEmits(['save']);

const protocols = ref([]);

const saveConfig = () => {
  emit('save');
};

const loadProtocols = async () => {
  try {
    const res = await api.get('/alarm/protocols');
    protocols.value = res.data.protocols || [];
  } catch (err) {
    console.error('获取协议列表失败:', err);
  }
};

onMounted(() => {
  loadProtocols();
});
</script>
