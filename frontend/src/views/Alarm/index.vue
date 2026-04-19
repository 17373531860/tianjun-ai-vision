<template>
  <div class="p-6 h-full overflow-y-auto">
    <h2 class="text-2xl font-bold mb-6 border-l-4 border-tech-blue pl-3 text-white">报警设置</h2>

    <!-- 工位选择 Tab（多工位时显示） -->
    <div v-if="channelCount > 1" class="mb-4">
      <el-radio-group v-model="activeChannel" size="small" @change="onChannelChange">
        <el-radio-button v-for="ch in channelCount" :key="ch - 1" :value="ch - 1">
          工位 {{ ch }}
        </el-radio-button>
      </el-radio-group>
    </div>

    <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
      <!-- 设备连接 -->
      <el-card shadow="never" class="bg-slate-800 border-slate-700">
        <template #header>
          <div class="flex items-center justify-between">
            <div class="flex items-center gap-2">
              <el-icon class="text-tech-blue"><Connection /></el-icon>
              <span class="font-bold text-white">设备连接</span>
              <el-tag v-if="channelCount > 1" size="small" type="info">工位 {{ activeChannel + 1 }}</el-tag>
            </div>
            <el-tag :type="isConnected ? 'success' : 'danger'" size="small">
              {{ isConnected ? '已连接' : '未连接' }}
            </el-tag>
          </div>
        </template>
        
        <div class="space-y-4">
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
            <el-slider v-model="systemStore.detection.voiceVolume" :min="0" :step="0.1" :disabled="!systemStore.detection.voiceEnabled" @change="saveVoiceSettings" />
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

      <!-- v2.7.3 共享报警灯（多工位 + 一个物理灯时使用） -->
      <el-card v-if="channelCount > 1" shadow="never" class="bg-slate-800 border-slate-700 lg:col-span-2">
        <template #header>
          <div class="flex items-center justify-between">
            <div class="flex items-center gap-2">
              <el-icon class="text-tech-blue"><Connection /></el-icon>
              <span class="font-bold text-white">共享报警灯（一个物理灯多工位共用）</span>
              <el-tag v-if="sharing.enabled && sharing.isOwner" size="small" type="success">
                共享中：服务工位 {{ sharing.servingChannels.map(c => c + 1).join(', ') }}
              </el-tag>
              <el-tag v-else-if="sharing.enabled && !sharing.isOwner" size="small" type="warning">
                跟随工位 {{ sharing.ownerChannel + 1 }}
              </el-tag>
            </div>
            <el-switch
              v-if="activeChannel === 0"
              v-model="sharing.enabled"
              active-text="启用共享"
              @change="onSharingToggle"
            />
          </div>
        </template>

        <!-- 仅在 ch0 显示完整编辑面板 -->
        <div v-if="activeChannel === 0">
          <div v-if="!sharing.enabled" class="text-sm text-gray-400">
            未启用：每个工位需各自一个物理报警灯。<br>
            如果你只有<span class="text-cyan-300 px-1">一个物理报警灯</span>要给所有工位共用，请打开右上角开关。
          </div>

          <div v-else class="space-y-5">
            <el-alert type="info" :closable="false" show-icon>
              <div class="text-xs">
                启用后，工位 1 配置的串口/协议/事件 → 红绿灯/蜂鸣会按下方<b>优先级合成规则</b>同时反映其他被共享工位的状态。
                被共享的工位（工位 2/3/...）的串口配置将<b>失效</b>。
              </div>
            </el-alert>

            <!-- 1. 选择共享给哪些工位 -->
            <div>
              <div class="text-gray-300 mb-2 text-sm">共享给哪些工位（可多选）</div>
              <el-checkbox-group v-model="sharing.sharedWith">
                <el-checkbox
                  v-for="ch in channelCount - 1"
                  :key="ch"
                  :value="ch"
                  border
                >工位 {{ ch + 1 }}</el-checkbox>
              </el-checkbox-group>
            </div>

            <!-- 2. 优先级排序 -->
            <div>
              <div class="text-gray-300 mb-2 text-sm">事件类别优先级（数字越小优先级越高）</div>
              <div class="grid grid-cols-4 gap-3">
                <div v-for="cat in priorityCategories" :key="cat.key" class="bg-slate-900 rounded p-3 border border-slate-700">
                  <div class="flex items-center justify-between mb-2">
                    <el-tag :type="cat.tagType" size="small">{{ cat.label }}</el-tag>
                  </div>
                  <el-input-number
                    v-model="sharing.priorityRank[cat.key]"
                    :min="1" :max="4" :step="1"
                    size="small"
                    class="w-full"
                  />
                </div>
              </div>
              <div class="text-xs text-gray-500 mt-2">
                推荐：NG=1（最高）→ 警告=2 → OK=3 → 待机=4。NG 触发期间不会被 OK 抢占。
              </div>
            </div>

            <!-- 3. 事件类别映射 -->
            <div v-if="projectEvents.length > 0">
              <div class="text-gray-300 mb-2 text-sm">事件归属类别（决定该事件按哪个优先级合成）</div>
              <div class="space-y-2">
                <div
                  v-for="event in projectEvents"
                  :key="event.id"
                  class="flex items-center gap-3 bg-slate-900 rounded p-2 border border-slate-700"
                >
                  <el-tag :type="getEventTagType(event.id)" size="small">事件{{ event.id }}</el-tag>
                  <span class="text-gray-300 flex-1">{{ event.name }}</span>
                  <el-select v-model="sharing.eventCategoryMap[`event${event.id}`]" size="small" class="w-32">
                    <el-option
                      v-for="cat in priorityCategories"
                      :key="cat.key" :value="cat.key" :label="cat.label"
                    />
                  </el-select>
                </div>
              </div>
            </div>

            <div class="flex justify-end gap-2 pt-2 border-t border-slate-700">
              <el-button size="small" @click="loadSharingConfig">重置</el-button>
              <el-button size="small" type="primary" :loading="savingSharing" @click="saveSharingConfig">
                保存共享配置（立即生效）
              </el-button>
            </div>
          </div>
        </div>

        <!-- 在被共享的工位 (ch >= 1) 显示只读提示 -->
        <div v-else class="text-sm text-gray-400">
          <template v-if="sharing.enabled && !sharing.isOwner">
            <el-alert type="warning" :closable="false" show-icon>
              此工位已被工位 {{ sharing.ownerChannel + 1 }} 共享报警灯。
              所有报警动作由工位 {{ sharing.ownerChannel + 1 }} 的物理设备统一执行，本工位下方的串口/协议配置将<b>失效</b>。
              <br>
              如要修改共享设置，请切换到<el-button text type="primary" @click="activeChannel = 0; onChannelChange()">工位 1</el-button>。
            </el-alert>
          </template>
          <template v-else>
            未启用共享。如要启用，请到工位 1 配置。
          </template>
        </div>
      </el-card>

      <!-- 触发条件配置 -->
      <el-card shadow="never" class="bg-slate-800 border-slate-700">
        <template #header>
          <div class="flex items-center justify-between">
            <div class="flex items-center gap-2">
              <el-icon class="text-tech-blue"><Lightning /></el-icon>
              <span class="font-bold text-white">触发条件</span>
              <el-tag v-if="channelCount > 1" size="small" type="info">工位 {{ activeChannel + 1 }}</el-tag>
              <el-tag v-if="sharing.enabled && !sharing.isOwner" size="small" type="warning">由工位 {{ sharing.ownerChannel + 1 }} 接管</el-tag>
            </div>
            <el-switch v-model="config.enabled" active-text="启用报警" @change="saveConfig" />
          </div>
        </template>
        
        <div class="space-y-4">
          <div class="flex items-center gap-3 pb-3 border-b border-slate-700">
            <span class="text-gray-400 text-sm">当前项目:</span>
            <span class="text-white font-medium">{{ projectStore.currentProjectName }}</span>
            <el-tag v-if="projectStore.currentProjectId" type="success" size="small">运行中</el-tag>
          </div>
          
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
          
          <!-- 工作指示灯 -->
          <div class="p-4 bg-slate-900 rounded border border-slate-600">
            <div class="flex items-center justify-between mb-3">
              <div class="flex items-center gap-2">
                <el-tag type="primary" size="small">工作指示灯</el-tag>
                <span class="text-gray-300">检测运行时常亮</span>
              </div>
              <el-switch 
                v-model="config.idle_light.enabled"
                @change="saveConfig"
              />
            </div>
            <div v-if="config.idle_light.enabled" class="flex items-center gap-3">
              <span class="text-xs text-gray-400">常亮颜色</span>
              <el-select 
                v-model="config.idle_light.color" 
                size="small" 
                class="w-32"
                @change="saveConfig"
              >
                <el-option value="red" label="红灯" />
                <el-option value="green" label="绿灯" />
                <el-option value="blue" label="蓝灯" />
                <el-option value="yellow" label="黄灯" />
              </el-select>
              <span class="text-xs text-gray-500">开始检测后此灯常亮，事件触发闪灯后自动恢复</span>
            </div>
          </div>

          <!-- 动态事件列表 -->
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
                    :min="0" 
                    :precision="2"
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
import { getWorkstations } from '@/api/detection';
import { useProjectStore } from '@/store/useProjectStore';
import { useSystemStore } from '@/store/useSystemStore';

const projectStore = useProjectStore();
const systemStore = useSystemStore();

// 多工位
const channelCount = ref(1);
const activeChannel = ref(0);

// 状态
const projectEvents = ref([]);
const portList = ref([]);
const selectedPort = ref('');
const baudrate = ref(9600);
const isConnected = ref(false);
const loadingPorts = ref(false);
const connecting = ref(false);
const testing = ref('');
const protocols = ref([]);
const selectedEvent = ref('event1');

// 配置
const config = reactive({
  enabled: false,
  protocol: 'simple_ascii',
  test_mode: true,
  custom_commands: {
    light_on: '',
    light_off: '',
    buzzer_on: '',
    buzzer_off: '',
  },
  triggers: {
    event1: { name: '合格', enabled: true, color: 'green', effect: 'on', buzzer: false, duration: 2 },
    event2: { name: 'NG', enabled: true, color: 'red', effect: 'fast', buzzer: true, duration: 5 },
  },
  idle_light: {
    enabled: true,
    color: 'blue',
  }
});

// v2.7.3 共享报警灯
const priorityCategories = [
  { key: 'ng',   label: 'NG',   tagType: 'danger'  },
  { key: 'warn', label: '警告', tagType: 'warning' },
  { key: 'ok',   label: 'OK',   tagType: 'success' },
  { key: 'idle', label: '待机', tagType: 'info'    },
];
const sharing = reactive({
  enabled: false,           // 是否启用共享（基于 ch0 配置里有无 shared_with）
  isOwner: true,            // 当前 channel 是否是共享 owner
  ownerChannel: 0,          // 共享 owner 的 channel id
  servingChannels: [],      // owner 当前服务的所有 channel
  sharedWith: [],           // ch0 配置的 shared_with 数组（不含自己）
  priorityRank: { ng: 1, warn: 2, ok: 3, idle: 4 },
  eventCategoryMap: {},     // {event1: 'ok', event2: 'ng', ...}
});
const savingSharing = ref(false);

const chParam = () => `?channel=${activeChannel.value}`;

const onChannelChange = () => {
  loadChannelStatus();
};

const loadChannelCount = async () => {
  try {
    const res = await getWorkstations();
    channelCount.value = res.data.channel_count || 1;
  } catch {
    channelCount.value = 1;
  }
};

const loadChannelStatus = async () => {
  try {
    const res = await api.get(`/alarm/status${chParam()}`);
    isConnected.value = res.data.is_connected;
    if (res.data.port) {
      selectedPort.value = res.data.port;
    } else {
      selectedPort.value = '';
    }
    if (res.data.config) {
      Object.assign(config, res.data.config);
    }
    // v2.7.3 共享状态
    sharing.isOwner = res.data.is_owner ?? true;
    sharing.ownerChannel = res.data.owner_channel ?? activeChannel.value;
    sharing.servingChannels = res.data.shared_channels ?? [];
    // 始终从 ch0 拉取共享编辑状态（即使当前在别的 ch）
    await loadSharingConfig();
  } catch (err) {
    console.error('获取报警状态失败:', err);
  }
};

const loadSharingConfig = async () => {
  // 共享配置永远从 ch0 读
  try {
    const res = await api.get(`/alarm/status?channel=0`);
    const cfg = res.data.config || {};
    sharing.sharedWith = Array.isArray(cfg.shared_with) ? [...cfg.shared_with] : [];
    sharing.enabled = sharing.sharedWith.length > 0;
    // 优先级：从 priority_order 数组反推 rank
    const order = Array.isArray(cfg.priority_order) && cfg.priority_order.length === 4
      ? cfg.priority_order
      : ['ng', 'warn', 'ok', 'idle'];
    const newRank = {};
    order.forEach((cat, idx) => { newRank[cat] = idx + 1; });
    Object.assign(sharing.priorityRank, newRank);
    // 事件类别映射
    sharing.eventCategoryMap = {};
    const map = cfg.event_priority_map || {};
    projectEvents.value.forEach((ev) => {
      const key = `event${ev.id}`;
      sharing.eventCategoryMap[key] = map[key] || (ev.id === 1 ? 'ok' : ev.id === 2 ? 'ng' : 'warn');
    });
  } catch (err) {
    console.error('加载共享配置失败:', err);
  }
};

const onSharingToggle = async (val) => {
  // 关闭共享：直接清空 shared_with 并保存
  if (!val) {
    sharing.sharedWith = [];
    await saveSharingConfig();
  }
  // 开启共享：等用户勾完工位再点保存
};

const saveSharingConfig = async () => {
  // 把优先级 rank 转回 priority_order 数组（按 rank 升序）
  const orderEntries = Object.entries(sharing.priorityRank)
    .sort((a, b) => a[1] - b[1])
    .map((x) => x[0]);
  // 校验：4 个类别必须 rank 互不相同（1,2,3,4）
  const ranks = Object.values(sharing.priorityRank).sort();
  if (JSON.stringify(ranks) !== '[1,2,3,4]') {
    ElMessage.warning('优先级数字必须是 1,2,3,4 各用一次');
    return;
  }
  savingSharing.value = true;
  try {
    // 共享配置永远写到 ch0（owner）
    await api.post(`/alarm/config?channel=0`, {
      enabled: config.enabled,
      protocol: config.protocol,
      test_mode: config.test_mode,
      custom_commands: config.custom_commands,
      triggers: config.triggers,
      idle_light: config.idle_light,
      shared_with: sharing.sharedWith,
      priority_order: orderEntries,
      event_priority_map: sharing.eventCategoryMap,
    });
    ElMessage.success(sharing.sharedWith.length > 0
      ? `已启用共享：服务工位 ${[0, ...sharing.sharedWith].map(c => c + 1).join(', ')}`
      : '已关闭共享');
    // 重新加载所有通道状态，刷新 owner / serving
    await loadChannelCount();
    await loadChannelStatus();
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || '保存共享配置失败');
  } finally {
    savingSharing.value = false;
  }
};

const refreshPorts = async () => {
  loadingPorts.value = true;
  try {
    const res = await api.get(`/alarm/ports${chParam()}`);
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

const loadProtocols = async () => {
  try {
    const res = await api.get('/alarm/protocols');
    protocols.value = res.data.protocols || [];
  } catch (err) {
    console.error('获取协议列表失败:', err);
  }
};

const loadStatus = async () => {
  await loadChannelStatus();
};

const connect = async () => {
  connecting.value = true;
  try {
    await api.post(`/alarm/connect${chParam()}`, {
      port: selectedPort.value,
      baudrate: baudrate.value
    });
    isConnected.value = true;
    ElMessage.success(`工位 ${activeChannel.value + 1} 连接成功`);
    saveConfig();
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || '连接失败');
  } finally {
    connecting.value = false;
  }
};

const disconnect = async () => {
  try {
    await api.post(`/alarm/disconnect${chParam()}`);
    isConnected.value = false;
    ElMessage.success(`工位 ${activeChannel.value + 1} 已断开连接`);
  } catch (err) {
    ElMessage.error('断开失败');
  }
};

const saveConfig = async () => {
  try {
    await api.post(`/alarm/config${chParam()}`, {
      enabled: config.enabled,
      protocol: config.protocol,
      test_mode: config.test_mode,
      custom_commands: config.custom_commands,
      triggers: config.triggers,
      idle_light: config.idle_light,
    });
    
    if (projectStore.currentProjectId) {
      await updateProject(projectStore.currentProjectId, {
        alarm_config: {
          enabled: config.enabled,
          protocol: config.protocol,
          test_mode: config.test_mode,
          custom_commands: config.custom_commands,
          triggers: config.triggers,
          idle_light: config.idle_light,
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

const testAction = async (action) => {
  testing.value = action;
  try {
    await api.post(`/alarm/test${chParam()}`, { action });
    ElMessage.success(`已发送: ${action}`);
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || '测试失败');
  } finally {
    testing.value = '';
  }
};

const triggerAlarm = async (eventType) => {
  try {
    await api.post(`/alarm/trigger/${eventType}${chParam()}`);
    ElMessage.success(`工位 ${activeChannel.value + 1} 已触发 ${eventType} 报警`);
  } catch (err) {
    ElMessage.error('触发失败');
  }
};

const stopAlarm = async () => {
  try {
    await api.post(`/alarm/stop${chParam()}`);
    ElMessage.success('已停止报警');
  } catch (err) {
    ElMessage.error('停止失败');
  }
};

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

const updateEventConfig = (eventId, field, value) => {
  if (!config.triggers[eventId]) {
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

const getEventTagType = (eventId) => {
  if (eventId === 'event1' || eventId === '1') return 'success';
  if (eventId === 'event2' || eventId === '2') return 'danger';
  return 'info';
};

const loadProjectEvents = async () => {
  if (!projectStore.currentProjectId) {
    projectEvents.value = [];
    return;
  }
  try {
    const detail = await getProjectDetail(projectStore.currentProjectId);
    projectEvents.value = detail.data.events_config || [];
    
    const alarmConfig = detail.data.alarm_config;
    if (alarmConfig) {
      config.enabled = alarmConfig.enabled ?? false;
      config.protocol = alarmConfig.protocol ?? 'modbus_4color';
      config.test_mode = alarmConfig.test_mode ?? false;
      config.custom_commands = alarmConfig.custom_commands ?? {};
      config.triggers = alarmConfig.triggers ?? {};
      config.idle_light = alarmConfig.idle_light ?? { enabled: true, color: 'blue' };
      
      if (alarmConfig.port) {
        selectedPort.value = alarmConfig.port;
      }
      if (alarmConfig.baudrate) {
        baudrate.value = alarmConfig.baudrate;
      }
      
      await api.post(`/alarm/config${chParam()}`, {
        enabled: config.enabled,
        protocol: config.protocol,
        test_mode: config.test_mode,
        custom_commands: config.custom_commands,
        triggers: config.triggers,
        idle_light: config.idle_light,
      });
      
      if (alarmConfig.port && !isConnected.value) {
        try {
          await api.post(`/alarm/connect${chParam()}`, {
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
    
    if (projectEvents.value.length > 0) {
      selectedEvent.value = `event${projectEvents.value[0].id}`;
    }
  } catch (err) {
    console.error('加载项目事件失败:', err);
  }
};

watch(() => projectStore.currentProjectId, () => {
  loadProjectEvents();
});

onMounted(async () => {
  await loadChannelCount();
  refreshPorts();
  loadProtocols();
  loadStatus();
  loadProjectEvents();
});
</script>
