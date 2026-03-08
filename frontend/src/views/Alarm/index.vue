<template>
  <div class="p-6 h-full overflow-y-auto">
    <h2 class="text-2xl font-bold mb-6 border-l-4 border-tech-blue pl-3 text-white">报警设置</h2>

    <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
      <!-- 设备连接 -->
      <el-card shadow="never" class="bg-slate-800 border-slate-700">
        <template #header>
          <div class="flex items-center justify-between">
            <div class="flex items-center gap-2">
              <el-icon class="text-tech-blue"><Connection /></el-icon>
              <span class="font-bold text-white">设备连接</span>
            </div>
            <el-tag :type="isConnected ? 'success' : 'danger'" size="small">
              {{ isConnected ? '已连接' : '未连接' }}
            </el-tag>
          </div>
        </template>
        
        <div class="space-y-4">
          <!-- 串口设备列表 -->
          <div>
            <div class="text-gray-300 mb-2">选择串口设备</div>
            <div class="flex gap-2">
              <el-select v-model="selectedPort" placeholder="选择设备" class="flex-1" :disabled="isConnected">
                <el-option 
                  v-for="port in portList" 
                  :key="port.port" 
                  :label="`${port.port} - ${port.description}`"
                  :value="port.port"
                />
              </el-select>
              <el-button @click="refreshPorts" :loading="loadingPorts" :icon="Refresh">刷新</el-button>
            </div>
          </div>
          
          <!-- 波特率 -->
          <div>
            <div class="text-gray-300 mb-2">波特率</div>
            <el-select v-model="baudrate" :disabled="isConnected" class="w-full">
              <el-option :value="9600" label="9600" />
              <el-option :value="19200" label="19200" />
              <el-option :value="38400" label="38400" />
              <el-option :value="57600" label="57600" />
              <el-option :value="115200" label="115200" />
            </el-select>
          </div>
          
          <!-- 连接按钮 -->
          <div class="flex gap-2">
            <el-button 
              v-if="!isConnected" 
              type="primary" 
              @click="connect" 
              :loading="connecting"
              :disabled="!selectedPort"
              class="flex-1"
            >
              连接设备
            </el-button>
            <el-button 
              v-else 
              type="danger" 
              @click="disconnect"
              class="flex-1"
            >
              断开连接
            </el-button>
          </div>
        </div>
      </el-card>

      <!-- 协议设置 -->
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
          
          <!-- 自定义命令（仅当选择自定义协议时显示） -->
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

      <!-- 功能测试 -->
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
            <!-- 测试模式开关 -->
            <div class="flex items-center justify-between p-3 bg-slate-900 rounded border" :class="config.test_mode ? 'border-green-500' : 'border-slate-700'">
              <div>
                <div class="text-gray-300">测试模式</div>
                <div class="text-xs text-gray-500">开启后，下方测试按钮才能使用</div>
              </div>
              <el-switch v-model="config.test_mode" @change="saveConfig" />
            </div>
            
            <!-- 四色灯测试按钮 -->
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
            
            <!-- 蜂鸣器测试 -->
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

      <!-- 语音播报 -->
      <el-card shadow="never" class="bg-slate-800 border-slate-700">
        <template #header>
          <div class="flex items-center justify-between">
            <div class="flex items-center gap-2">
              <el-icon class="text-tech-blue"><Microphone /></el-icon>
              <span class="font-bold text-white">语音播报</span>
            </div>
            <el-switch v-model="systemStore.detection.voiceEnabled" active-text="启用" @change="saveVoiceSettings" />
          </div>
        </template>
        
        <div class="space-y-4">
          <div class="p-3 bg-slate-900 rounded border border-slate-800">
            <div class="text-gray-300 mb-2">播报音量</div>
            <el-slider v-model="systemStore.detection.voiceVolume" :min="0" :max="1" :step="0.1" :disabled="!systemStore.detection.voiceEnabled" @change="saveVoiceSettings" />
          </div>
          <div class="p-3 bg-slate-900 rounded border border-slate-800">
            <div class="text-gray-400 text-xs mb-2">
              OK 播报"合格"，NG 播报"不合格"。若显示设置中"NG弹窗显示原因"已开启，NG 原因也会一并播报。
            </div>
            <div class="flex gap-3">
              <el-button size="small" :disabled="!systemStore.detection.voiceEnabled" @click="testVoice('合格')">测试"合格"</el-button>
              <el-button size="small" type="danger" :disabled="!systemStore.detection.voiceEnabled" @click="testVoice('不合格，缺少步骤')">测试"不合格"</el-button>
            </div>
          </div>
        </div>
      </el-card>

      <!-- 触发条件配置 -->
      <el-card shadow="never" class="bg-slate-800 border-slate-700">
        <template #header>
          <div class="flex items-center justify-between">
            <div class="flex items-center gap-2">
              <el-icon class="text-tech-blue"><Lightning /></el-icon>
              <span class="font-bold text-white">触发条件</span>
            </div>
            <el-switch v-model="config.enabled" active-text="启用报警" @change="saveConfig" />
          </div>
        </template>
        
        <div class="space-y-4">
          <!-- 当前项目信息 -->
          <div class="flex items-center gap-3 pb-3 border-b border-slate-700">
            <span class="text-gray-400 text-sm">当前项目:</span>
            <span class="text-white font-medium">{{ projectStore.currentProjectName }}</span>
            <el-tag v-if="projectStore.currentProjectId" type="success" size="small">运行中</el-tag>
          </div>
          
          <!-- 提示信息 -->
          <el-alert 
            v-if="!projectStore.currentProjectId" 
            title="请先在顶部导航栏选择一个项目" 
            type="warning" 
            :closable="false"
            show-icon
          />
          <el-alert 
            v-else-if="projectEvents.length === 0" 
            title="当前项目暂无事件配置，请在项目管理中添加事件" 
            type="info" 
            :closable="false"
            show-icon
          />
          
          <!-- 动态事件列表（基于项目事件） -->
          <div 
            v-for="event in projectEvents" 
            :key="event.id"
            class="p-4 bg-slate-900 rounded border"
            :class="config.triggers[`event${event.id}`]?.enabled ? 'border-slate-600' : 'border-slate-700'"
          >
            <div class="flex items-center justify-between mb-3">
              <div class="flex items-center gap-2">
                <el-tag :type="getEventTagType(event.id)" size="small">事件{{ event.id }}</el-tag>
                <span class="text-gray-300">{{ event.name }}</span>
              </div>
              <el-switch 
                :model-value="config.triggers[`event${event.id}`]?.enabled || false" 
                @change="(val) => updateEventConfig(`event${event.id}`, 'enabled', val)" 
              />
            </div>
            <div v-if="config.triggers[`event${event.id}`]?.enabled" class="space-y-3">
              <div class="grid grid-cols-2 gap-3">
                <div>
                  <div class="text-xs text-gray-400 mb-1">灯光颜色</div>
                  <el-select 
                    :model-value="config.triggers[`event${event.id}`]?.color || 'none'" 
                    size="small" 
                    class="w-full" 
                    @change="(val) => updateEventConfig(`event${event.id}`, 'color', val)"
                  >
                    <el-option value="none" label="不亮灯" />
                    <el-option value="red" label="红灯" />
                    <el-option value="green" label="绿灯" />
                    <el-option value="blue" label="蓝灯" />
                    <el-option value="yellow" label="黄灯" />
                  </el-select>
                </div>
                <div>
                  <div class="text-xs text-gray-400 mb-1">灯光效果</div>
                  <el-select 
                    :model-value="config.triggers[`event${event.id}`]?.effect || 'on'" 
                    size="small" 
                    class="w-full" 
                    @change="(val) => updateEventConfig(`event${event.id}`, 'effect', val)"
                    :disabled="config.triggers[`event${event.id}`]?.color === 'none'"
                  >
                    <el-option value="on" label="常亮" />
                    <el-option value="slow" label="慢闪" />
                    <el-option value="fast" label="快闪" />
                  </el-select>
                </div>
              </div>
              <div class="grid grid-cols-2 gap-3">
                <div class="flex items-center gap-2">
                  <el-checkbox 
                    :model-value="config.triggers[`event${event.id}`]?.buzzer || false" 
                    @change="(val) => updateEventConfig(`event${event.id}`, 'buzzer', val)"
                  >蜂鸣器</el-checkbox>
                </div>
                <div class="flex items-center gap-2">
                  <span class="text-xs text-gray-400">时长:</span>
                  <el-input-number 
                    :model-value="config.triggers[`event${event.id}`]?.duration || 3" 
                    :min="1" 
                    :max="60" 
                    size="small"
                    class="w-20"
                    @change="(val) => updateEventConfig(`event${event.id}`, 'duration', val)"
                  />
                  <span class="text-xs text-gray-400">秒</span>
                </div>
              </div>
            </div>
          </div>
          
          <!-- 模拟触发 -->
          <div class="flex items-center gap-3 pt-2 border-t border-slate-700">
            <span class="text-gray-400 text-sm">模拟触发:</span>
            <el-select v-model="selectedEvent" size="small" placeholder="选择事件" class="w-40">
              <el-option 
                v-for="event in projectEvents" 
                :key="event.id" 
                :value="`event${event.id}`" 
                :label="event.name"
              />
            </el-select>
            <el-button type="primary" plain @click="triggerAlarm(selectedEvent)" :disabled="!isConnected || !selectedEvent">
              触发
            </el-button>
            <el-button @click="stopAlarm" :disabled="!isConnected">
              停止报警
            </el-button>
          </div>
        </div>
      </el-card>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, watch } from 'vue';
import { ElMessage } from 'element-plus';
import { 
  Connection, Setting, VideoPlay, Lightning, Refresh,
  Sunny, Moon, Bell, MuteNotification, Microphone 
} from '@element-plus/icons-vue';
import api from '@/api/index';
import { getProjectDetail, updateProject } from '@/api/project';
import { useProjectStore } from '@/store/useProjectStore';
import { useSystemStore } from '@/store/useSystemStore';

const projectStore = useProjectStore();
const systemStore = useSystemStore();

// 状态
const projectEvents = ref([]);  // 项目中定义的事件列表
const portList = ref([]);
const selectedPort = ref('');
const baudrate = ref(9600);
const isConnected = ref(false);
const loadingPorts = ref(false);
const connecting = ref(false);
const testing = ref('');
const protocols = ref([]);
const selectedEvent = ref('event1');  // 当前选中要模拟的事件

// 配置
const config = reactive({
  enabled: false,
  protocol: 'simple_ascii',
  test_mode: true,  // 默认开启测试模式（静音）
  custom_commands: {
    light_on: '',
    light_off: '',
    buzzer_on: '',
    buzzer_off: '',
  },
  triggers: {
    event1: { name: '合格', enabled: true, color: 'green', effect: 'on', buzzer: false, duration: 2 },
    event2: { name: 'NG', enabled: true, color: 'red', effect: 'fast', buzzer: true, duration: 5 },
  }
});

// 获取串口列表
const refreshPorts = async () => {
  loadingPorts.value = true;
  try {
    const res = await api.get('/alarm/ports');
    portList.value = res.data.ports || [];
    isConnected.value = res.data.is_connected;
    if (res.data.current_port) {
      selectedPort.value = res.data.current_port;
    }
    if (portList.value.length === 0) {
      ElMessage.warning('未检测到串口设备');
    } else {
      ElMessage.success(`检测到 ${portList.value.length} 个设备`);
    }
  } catch (err) {
    ElMessage.error('获取设备列表失败');
    console.error(err);
  } finally {
    loadingPorts.value = false;
  }
};

// 获取协议列表
const loadProtocols = async () => {
  try {
    const res = await api.get('/alarm/protocols');
    protocols.value = res.data.protocols || [];
  } catch (err) {
    console.error('获取协议列表失败:', err);
  }
};

// 获取状态和配置
const loadStatus = async () => {
  try {
    const res = await api.get('/alarm/status');
    isConnected.value = res.data.is_connected;
    if (res.data.port) {
      selectedPort.value = res.data.port;
    }
    if (res.data.config) {
      Object.assign(config, res.data.config);
    }
  } catch (err) {
    console.error('获取状态失败:', err);
  }
};

// 连接设备
const connect = async () => {
  connecting.value = true;
  try {
    await api.post('/alarm/connect', {
      port: selectedPort.value,
      baudrate: baudrate.value
    });
    isConnected.value = true;
    ElMessage.success('连接成功');
    // 连接成功后保存配置（包含端口信息）
    saveConfig();
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || '连接失败');
  } finally {
    connecting.value = false;
  }
};

// 断开连接
const disconnect = async () => {
  try {
    await api.post('/alarm/disconnect');
    isConnected.value = false;
    ElMessage.success('已断开连接');
  } catch (err) {
    ElMessage.error('断开失败');
  }
};

// 保存配置（同时保存到后端报警服务和项目数据库）
const saveConfig = async () => {
  try {
    // 保存到报警服务（用于实时触发）
    await api.post('/alarm/config', {
      enabled: config.enabled,
      protocol: config.protocol,
      test_mode: config.test_mode,
      custom_commands: config.custom_commands,
      triggers: config.triggers,
    });
    
    // 保存到项目数据库（用于持久化和项目切换）
    if (projectStore.currentProjectId) {
      await updateProject(projectStore.currentProjectId, {
        alarm_config: {
          enabled: config.enabled,
          protocol: config.protocol,
          test_mode: config.test_mode,
          custom_commands: config.custom_commands,
          triggers: config.triggers,
          // 保存设备连接信息
          port: selectedPort.value,
          baudrate: baudrate.value,
        }
      });
    }
  } catch (err) {
    ElMessage.error('保存配置失败');
    console.error(err);
  }
};

// 测试功能
const testAction = async (action) => {
  testing.value = action;
  try {
    await api.post('/alarm/test', { action });
    ElMessage.success(`已发送: ${action}`);
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || '测试失败');
  } finally {
    testing.value = '';
  }
};

// 触发报警
const triggerAlarm = async (eventType) => {
  try {
    await api.post(`/alarm/trigger/${eventType}`);
    ElMessage.success(`已触发 ${eventType} 报警`);
  } catch (err) {
    ElMessage.error('触发失败');
  }
};

// 停止报警
const stopAlarm = async () => {
  try {
    await api.post('/alarm/stop');
    ElMessage.success('已停止报警');
  } catch (err) {
    ElMessage.error('停止失败');
  }
};

// Voice settings
const saveVoiceSettings = () => {
  systemStore.saveDetectionSettings();
};

const testVoice = (text) => {
  if (!window.speechSynthesis) {
    ElMessage.error('当前浏览器不支持语音合成');
    return;
  }
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = 'zh-CN';
  utterance.volume = systemStore.detection.voiceVolume ?? 1.0;
  utterance.rate = 1.1;
  utterance.onerror = (e) => {
    console.error('语音合成错误:', e);
    ElMessage.error('语音播报失败，请检查系统是否安装了 espeak-ng（sudo apt install espeak-ng）');
  };
  window.speechSynthesis.speak(utterance);
};

// 更新事件配置
const updateEventConfig = (eventId, field, value) => {
  if (!config.triggers[eventId]) {
    // 初始化这个事件的配置
    config.triggers[eventId] = {
      enabled: false,
      color: 'red',
      effect: 'on',
      buzzer: false,
      duration: 3
    };
  }
  config.triggers[eventId][field] = value;
  saveConfig();
};

// 获取事件标签类型
const getEventTagType = (eventId) => {
  if (eventId === 'event1' || eventId === '1') return 'success';
  if (eventId === 'event2' || eventId === '2') return 'danger';
  return 'info';
};

// 加载当前项目的事件和报警配置
const loadProjectEvents = async () => {
  if (!projectStore.currentProjectId) {
    projectEvents.value = [];
    return;
  }
  try {
    const detail = await getProjectDetail(projectStore.currentProjectId);
    projectEvents.value = detail.data.events_config || [];
    
    // 加载项目的报警配置
    const alarmConfig = detail.data.alarm_config;
    if (alarmConfig) {
      config.enabled = alarmConfig.enabled ?? false;
      config.protocol = alarmConfig.protocol ?? 'modbus_4color';
      config.test_mode = alarmConfig.test_mode ?? false;
      config.custom_commands = alarmConfig.custom_commands ?? {};
      config.triggers = alarmConfig.triggers ?? {};
      
      // 加载设备连接信息
      if (alarmConfig.port) {
        selectedPort.value = alarmConfig.port;
      }
      if (alarmConfig.baudrate) {
        baudrate.value = alarmConfig.baudrate;
      }
      
      // 同步到报警服务
      await api.post('/alarm/config', {
        enabled: config.enabled,
        protocol: config.protocol,
        test_mode: config.test_mode,
        custom_commands: config.custom_commands,
        triggers: config.triggers,
      });
      
      // 如果有保存的端口且未连接，尝试自动连接
      if (alarmConfig.port && !isConnected.value) {
        try {
          await api.post('/alarm/connect', {
            port: alarmConfig.port,
            baudrate: alarmConfig.baudrate || 9600
          });
          isConnected.value = true;
          console.log('报警设备自动连接成功');
        } catch (e) {
          console.warn('报警设备自动连接失败:', e.message);
        }
      }
    }
    
    // 设置默认选中的模拟事件
    if (projectEvents.value.length > 0) {
      selectedEvent.value = `event${projectEvents.value[0].id}`;
    }
  } catch (err) {
    console.error('加载项目事件失败:', err);
  }
};

// 获取事件名称
const getEventName = (eventId) => {
  const event = projectEvents.value.find(e => String(e.id) === String(eventId) || `event${e.id}` === eventId);
  return event ? event.name : eventId;
};

// 监听项目变化
watch(() => projectStore.currentProjectId, () => {
  loadProjectEvents();
});

// 初始化
onMounted(() => {
  refreshPorts();
  loadProtocols();
  loadStatus();
  loadProjectEvents();
});
</script>
