<template>
  <div class="space-y-3">
    <!-- ══ 顶部: 设备连接状态栏 ══ -->
    <div class="bg-slate-800/60 rounded-xl border border-slate-700/80 p-4">
      <div class="flex items-center justify-between mb-3">
        <div class="flex items-center gap-2">
          <div class="w-1.5 h-5 rounded-full bg-cyan-400"></div>
          <h3 class="text-cyan-300 font-semibold text-sm tracking-wide">WMax 扫码器控制</h3>
        </div>
        <div class="flex gap-2">
          <el-button size="small" @click="scanLan" :loading="scanning" plain>
            <template #icon><i class="i-ep-search" /></template>搜索设备
          </el-button>
          <el-button size="small" @click="refreshWmaxStatus" plain>刷新</el-button>
        </div>
      </div>

      <div v-if="discoveredDevices.length" class="mb-3">
        <div class="text-xs text-gray-500 mb-1.5">发现的设备:</div>
        <div class="flex flex-wrap gap-2">
          <div v-for="(dev, i) in discoveredDevices" :key="i"
               class="bg-slate-700/50 rounded-lg px-3 py-2 text-xs flex items-center gap-3 border border-slate-600/50">
            <div class="flex items-center gap-1.5">
              <div class="w-1.5 h-1.5 rounded-full bg-blue-400 animate-pulse"></div>
              <span class="text-cyan-200 font-mono">{{ dev.source_ip }}</span>
            </div>
            <span v-if="dev.dev_info" class="text-gray-400">{{ dev.dev_info.dev_name }}</span>
            <el-button size="small" type="success" plain @click="connectDevice(dev.source_ip, dev.source_port || 55266)">连接</el-button>
          </div>
        </div>
      </div>

      <div v-if="connectedDevices.length" class="flex flex-wrap gap-2">
        <div v-for="dev in connectedDevices" :key="dev.ip"
             class="rounded-lg px-3 py-2 text-xs flex items-center gap-3 cursor-pointer transition-all duration-150"
             :class="selectedIp === dev.ip
               ? 'bg-cyan-900/30 border border-cyan-500/60 ring-1 ring-cyan-400/30'
               : 'bg-slate-700/40 border border-slate-600/40 hover:border-slate-500/60'"
             @click="selectDevice(dev)">
          <div class="w-2 h-2 rounded-full shrink-0" :class="dev.connected ? 'bg-emerald-400' : 'bg-red-400'"></div>
          <span class="text-cyan-200 font-mono">{{ dev.ip }}:{{ dev.port || 55266 }}</span>
          <span v-if="dev.sn" class="text-gray-500 text-[10px]">{{ dev.sn }}</span>
          <el-button size="small" type="danger" plain @click.stop="disconnectDevice(dev.ip, dev.port || 55266)">断开</el-button>
        </div>
      </div>
      <div v-else class="text-gray-600 text-xs text-center py-3">暂无已连接的 WMax 设备，请先搜索并连接</div>
    </div>

    <!-- ══ 操作面板 ══ -->
    <template v-if="selectedIp">
      <el-tabs v-model="activeSection" type="border-card" class="wmax-tabs">
        <el-tab-pane label="图像预览" name="imaging" />
        <el-tab-pane label="解码 & 码制" name="decode" />
        <el-tab-pane label="输入输出" name="io" />
        <el-tab-pane label="工具" name="tools" />
      </el-tabs>

      <!-- ═══ 图像预览 ═══ -->
      <div v-show="activeSection === 'imaging'" class="grid grid-cols-12 gap-3">
        <div class="col-span-3 space-y-3">
          <Card title="照明 & 对焦">
            <Param label="照明亮度">
              <el-slider v-model="readingParams.illum_value" :min="0" :max="100" size="small" class="flex-1" @change="applyAllConfig" />
              <span class="text-xs text-cyan-200 w-10 text-right font-mono">{{ readingParams.illum_value }}%</span>
            </Param>
            <Param label="对焦值">
              <el-slider v-model="readingParams.focus_value" :min="0" :max="1023" size="small" class="flex-1" @change="applyAllConfig" />
              <span class="text-xs text-cyan-200 w-10 text-right font-mono">{{ readingParams.focus_value }}</span>
            </Param>
            <el-button size="small" @click="doAutoFocus" :loading="focusLoading" class="w-full mt-1" plain>自动对焦</el-button>
          </Card>

          <Card title="检测 ROI">
            <div class="flex items-center gap-2 mb-2">
              <el-switch v-model="readingParams.enable_detect_roi" size="small" @change="applyAllConfig" />
              <span class="text-xs text-gray-400">启用区域检测</span>
            </div>
            <template v-if="readingParams.enable_detect_roi">
              <div class="grid grid-cols-2 gap-1.5">
                <div v-for="key in ['x', 'y', 'w', 'h']" :key="key" class="space-y-0.5">
                  <span class="text-[10px] text-gray-500 uppercase">{{ key }}</span>
                  <el-input-number v-model="detectRoi[key]" size="small" :min="0" controls-position="right" class="w-full" @change="applyAllConfig" />
                </div>
              </div>
            </template>
          </Card>

          <Card title="智能模式">
            <div class="flex items-center gap-2">
              <el-switch v-model="readingParams.enable_smart_mode" size="small" @change="applyAllConfig" />
              <span class="text-xs text-gray-400">启用智能解码</span>
            </div>
          </Card>

          <Card title="传感器">
            <Param label="曝光 (μs)">
              <el-input-number v-model="sensorParams.exposure_us" :min="1" :max="200000" :step="10" size="small" controls-position="right" class="flex-1" @change="applyAllConfig" />
            </Param>
            <Param label="增益">
              <el-input-number v-model="sensorParams.gain" :min="1" :max="64" :step="1" size="small" controls-position="right" class="flex-1" @change="applyAllConfig" />
            </Param>
          </Card>
        </div>

        <!-- 中: 视频预览 -->
        <div class="col-span-6">
          <div class="bg-slate-800/60 rounded-xl border border-slate-700/80 p-3 h-full flex flex-col">
            <div class="flex items-center justify-between mb-2">
              <h4 class="text-cyan-300 text-xs font-semibold flex items-center gap-1.5">
                <div v-if="videoOn" class="w-1.5 h-1.5 rounded-full bg-red-400 animate-pulse"></div>
                实时预览
              </h4>
              <el-button size="small" :type="videoOn ? 'danger' : 'success'" plain @click="toggleVideo">
                {{ videoOn ? '关闭视频' : '开启视频' }}
              </el-button>
            </div>
            <div class="flex-1 bg-black/80 rounded-lg overflow-hidden relative min-h-[360px] border border-slate-700/50">
              <img v-if="previewSrc" :src="previewSrc" class="w-full h-full object-contain" />
              <div v-else class="flex items-center justify-center h-full">
                <div class="text-center">
                  <div class="text-gray-600 text-3xl mb-2">&#x2298;</div>
                  <div class="text-gray-500 text-xs">点击「开启视频」查看画面</div>
                </div>
              </div>
            </div>
            <div v-if="lastCode && lastCode.codes?.length" class="mt-2 bg-emerald-900/20 border border-emerald-700/30 rounded-lg p-2.5">
              <div class="text-[10px] text-emerald-400/70 mb-1">最近扫码结果:</div>
              <div v-for="(c, i) in lastCode.codes" :key="i" class="text-sm text-emerald-300 font-mono tracking-wider break-all">{{ c.data }}</div>
            </div>
          </div>
        </div>

        <!-- 右: 触发 & 状态 -->
        <div class="col-span-3 space-y-3">
          <Card title="触发控制">
            <div class="space-y-1.5">
              <el-button size="small" type="success" @click="triggerOn" class="w-full" plain>LON 触发扫码</el-button>
              <el-button size="small" type="warning" @click="triggerOff" class="w-full" plain>LOFF 停止</el-button>
            </div>
          </Card>

          <Card title="触发图像">
            <el-button size="small" @click="toggleTriggerImage" class="w-full" plain>
              {{ triggerImageOn ? '关闭触发图像' : '开启触发图像' }}
            </el-button>
            <div class="text-[10px] text-gray-500 mt-1">开启后触发扫码同时返回图像</div>
          </Card>

          <Card title="配置管理">
            <div class="space-y-1.5">
              <el-button size="small" @click="loadConfig" class="w-full" plain>重新读取配置</el-button>
              <el-button size="small" type="primary" @click="saveConfig" class="w-full" plain>保存全部到 Flash</el-button>
            </div>
          </Card>
        </div>
      </div>

      <!-- ═══ 解码 & 码制 ═══ -->
      <div v-show="activeSection === 'decode'" class="space-y-3">
        <div class="grid grid-cols-2 gap-3">
          <Card title="解码参数">
            <Param label="解码超时">
              <el-input-number v-model="commonParams.decode_timeout" size="small" :min="100" :max="30000" :step="100" controls-position="right" @change="applyAllConfig" />
              <span class="text-xs text-gray-500 ml-1">ms</span>
            </Param>
            <Param label="尝试次数">
              <el-input-number v-model="commonParams.try_count" size="small" :min="1" :max="50" controls-position="right" @change="applyAllConfig" />
            </Param>
            <Param label="快门延迟">
              <el-input-number v-model="commonParams.shutter_delay" size="small" :min="0" :max="5000" :step="50" controls-position="right" @change="applyAllConfig" />
              <span class="text-xs text-gray-500 ml-1">ms</span>
            </Param>
            <Param label="解码器">
              <el-select v-model="commonParams.decoder_type" size="small" class="flex-1" @change="applyAllConfig">
                <el-option :value="0" label="标准" />
                <el-option :value="1" label="快速" />
                <el-option :value="2" label="深度" />
              </el-select>
            </Param>
          </Card>
          <Card title="图像处理">
            <Param label="条码极性">
              <el-select v-model="commonParams.barcode_polarity" size="small" class="flex-1" @change="applyAllConfig">
                <el-option :value="0" label="自动" />
                <el-option :value="1" label="黑底白条" />
                <el-option :value="2" label="白底黑条" />
              </el-select>
            </Param>
            <Param label="镜像">
              <el-select v-model="commonParams.barcode_mirror" size="small" class="flex-1" @change="applyAllConfig">
                <el-option :value="0" label="正常" />
                <el-option :value="1" label="水平" />
                <el-option :value="2" label="垂直" />
                <el-option :value="3" label="双向" />
              </el-select>
            </Param>
            <div class="flex gap-4">
              <div class="flex items-center gap-1.5">
                <el-switch v-model="commonParams.inverse_read" size="small" @change="applyAllConfig" />
                <span class="text-xs text-gray-400">反相</span>
              </div>
              <div class="flex items-center gap-1.5">
                <el-switch v-model="commonParams.reverse_read" size="small" @change="applyAllConfig" />
                <span class="text-xs text-gray-400">反序</span>
              </div>
            </div>
            <Param label="倾斜角">
              <el-input-number v-model="commonParams.base_tilt_angle" size="small" :min="0" :max="360" controls-position="right" @change="applyAllConfig" />
              <span class="text-xs text-gray-500 ml-1">&deg;</span>
            </Param>
            <Param label="范围">
              <el-input-number v-model="commonParams.tilt_angle_range" size="small" :min="0" :max="180" controls-position="right" @change="applyAllConfig" />
              <span class="text-xs text-gray-500 ml-1">&deg;</span>
            </Param>
          </Card>
        </div>

        <Card title="码制开关">
          <template #header-extra>
            <div class="flex gap-1.5">
              <el-button size="small" @click="toggleAllCodes(true)" plain>全开</el-button>
              <el-button size="small" @click="toggleAllCodes(false)" plain>全关</el-button>
            </div>
          </template>
          <div class="grid grid-cols-5 gap-1.5">
            <div v-for="item in codeItems" :key="item.code_type"
                 class="flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 transition-colors"
                 :class="item.enable ? 'bg-cyan-900/20 border border-cyan-700/30' : 'bg-slate-700/30 border border-slate-600/30'">
              <el-switch v-model="item.enable" size="small" @change="applyCodeConfig" />
              <span class="text-[11px]" :class="item.enable ? 'text-cyan-200' : 'text-gray-500'">{{ item.code_type_name }}</span>
            </div>
          </div>
        </Card>

        <div class="grid grid-cols-3 gap-3">
          <Card title="长度限制">
            <div class="flex items-center gap-2 mb-2">
              <el-switch v-model="codeConfig.output_len_limit" size="small" @change="applyCodeConfig" />
              <span class="text-xs text-gray-400">启用</span>
            </div>
            <Param v-if="codeConfig.output_len_limit" label="长度">
              <el-input-number v-model="codeConfig.output_len" size="small" :min="1" :max="256" controls-position="right" @change="applyCodeConfig" />
            </Param>
          </Card>
          <Card title="读取模式">
            <Param label="模式">
              <el-select v-model="codeConfig.code_mode" size="small" class="flex-1" @change="applyCodeConfig">
                <el-option :value="0" label="标准" />
                <el-option :value="1" label="连续" />
                <el-option :value="2" label="突发" />
              </el-select>
            </Param>
          </Card>
          <Card title="冗余校验">
            <Param label="次数">
              <el-input-number v-model="codeConfig.redundant" size="small" :min="0" :max="10" controls-position="right" @change="applyCodeConfig" />
            </Param>
          </Card>
        </div>
      </div>

      <!-- ═══ 输入输出 ═══ -->
      <div v-show="activeSection === 'io'" class="space-y-3">
        <div class="grid grid-cols-3 gap-3">
          <Card title="输入端子 (IO-IN)">
            <Param label="触发极性">
              <el-select v-model="inputParams.polarity" size="small" class="flex-1" @change="applyAllConfig">
                <el-option :value="0" label="低电平" />
                <el-option :value="1" label="高电平" />
              </el-select>
            </Param>
            <Param label="消抖时间">
              <el-input-number v-model="inputParams.debounce_time" size="small" :min="0" :max="1000" :step="10" controls-position="right" @change="applyAllConfig" />
              <span class="text-xs text-gray-500 ml-1">ms</span>
            </Param>
          </Card>

          <Card title="输出端子 (IO-OUT)">
            <div class="text-[10px] text-gray-500 mb-2">OK/错误 与 触发器忙 互斥</div>
            <div class="flex flex-wrap gap-x-4 gap-y-1.5 mb-3">
              <el-checkbox v-model="outputParams.ok_enabled" size="small"
                           :disabled="outputParams.trigger_busy_enabled" @change="onOutputSignalChange">OK</el-checkbox>
              <el-checkbox v-model="outputParams.error_enabled" size="small"
                           :disabled="outputParams.trigger_busy_enabled" @change="onOutputSignalChange">错误</el-checkbox>
              <el-checkbox v-model="outputParams.trigger_busy_enabled" size="small"
                           :disabled="outputParams.ok_enabled || outputParams.error_enabled" @change="onTriggerBusyChange">触发器忙</el-checkbox>
            </div>
            <Param label="持续时间">
              <el-input-number v-model="outputParams.duration_ms" size="small" :min="10" :max="5000" :step="10" controls-position="right" @change="applyOutputConfig" />
              <span class="text-xs text-gray-500 ml-1">ms</span>
            </Param>
            <div class="flex gap-2 mt-2">
              <el-button size="small" @click="applyOutputConfig()" class="flex-1" plain>应用</el-button>
              <el-button size="small" type="primary" @click="applyOutputConfig(true)" class="flex-1" plain>保存</el-button>
            </div>
          </Card>

          <Card title="指示灯">
            <Param label="亮灯模式">
              <el-select v-model="indicatorParams.mode" size="small" class="flex-1" @change="applyIndicatorConfig">
                <el-option :value="1" label="手动亮灯" />
                <el-option :value="3" label="扫描时亮" />
              </el-select>
            </Param>
            <div class="text-[10px] text-gray-500 mt-1 mb-2">
              「扫描时亮」= 扫码时自动亮灯，空闲时灭
            </div>
            <div class="flex gap-2">
              <el-button size="small" @click="applyIndicatorConfig()" class="flex-1" plain>应用</el-button>
              <el-button size="small" type="primary" @click="applyIndicatorConfig(true)" class="flex-1" plain>保存</el-button>
            </div>
          </Card>
        </div>

        <div class="grid grid-cols-3 gap-3">
          <Card title="数据输出格式">
            <Param label="分隔符">
              <el-input v-model="dataOutputFormat.separator" size="small" class="flex-1" placeholder=":" @change="applyDataOutputFormat" />
              <span class="text-[10px] text-gray-500 ml-1">多码间分隔</span>
            </Param>
            <Param label="内部分隔符">
              <el-input v-model="dataOutputFormat.internal_separator" size="small" class="flex-1" placeholder="," @change="applyDataOutputFormat" />
              <span class="text-[10px] text-gray-500 ml-1">码内字段分隔</span>
            </Param>
            <div class="flex gap-2 mt-2">
              <el-button size="small" @click="applyDataOutputFormat()" class="flex-1" plain>应用</el-button>
              <el-button size="small" type="primary" @click="applyDataOutputFormat(true)" class="flex-1" plain>保存</el-button>
            </div>
          </Card>
        </div>
      </div>

      <!-- ═══ 工具 ═══ -->
      <div v-show="activeSection === 'tools'" class="space-y-3">
        <div class="grid grid-cols-3 gap-3">
          <Card title="自动调参">
            <div class="space-y-1.5">
              <el-button size="small" type="primary" @click="startAutoTune" :loading="tuneLoading" class="w-full" plain>开始调参</el-button>
              <el-button size="small" @click="cancelAutoTune" :disabled="!tuneLoading" class="w-full" plain>取消</el-button>
            </div>
            <div v-if="tuneLoading" class="mt-2">
              <el-progress :percentage="tuneProgress" :stroke-width="4" status="warning" />
            </div>
          </Card>

          <Card title="读码率测试">
            <div class="flex gap-1.5 mb-2">
              <el-button size="small" type="primary" @click="startReadRate" :loading="rrRunning" class="flex-1" plain>开始</el-button>
              <el-button size="small" type="danger" @click="stopReadRate" :disabled="!rrRunning" class="flex-1" plain>停止</el-button>
            </div>
            <div v-if="rrResult" class="text-xs space-y-0.5">
              <div class="flex justify-between"><span class="text-gray-500">总次数</span><span class="text-cyan-200 font-mono">{{ rrResult.total_count }}</span></div>
              <div class="flex justify-between"><span class="text-gray-500">成功</span><span class="text-emerald-300 font-mono">{{ rrResult.success_count }}</span></div>
              <div class="flex justify-between"><span class="text-gray-500">失败</span><span class="text-red-300 font-mono">{{ rrResult.fail_count }}</span></div>
              <div class="flex justify-between border-t border-slate-700/50 pt-1 mt-1"><span class="text-gray-400">成功率</span><span class="text-cyan-200 font-bold font-mono">{{ (rrResult.rate * 100).toFixed(1) }}%</span></div>
            </div>
          </Card>

          <Card title="参数预设 (4组)">
            <div class="grid grid-cols-4 gap-1 mb-2">
              <el-button v-for="i in 4" :key="i" size="small"
                         :type="selectedPreset === i - 1 ? 'primary' : 'default'"
                         @click="selectedPreset = i - 1" plain>
                {{ i }}
              </el-button>
            </div>
            <div class="space-y-1.5">
              <el-button size="small" @click="doLoadPreset" class="w-full" plain>加载预设</el-button>
              <el-button size="small" type="warning" @click="doSavePreset" class="w-full" plain>保存到预设</el-button>
            </div>
          </Card>
        </div>

        <div class="grid grid-cols-2 gap-3">
          <Card title="设备控制">
            <div class="flex gap-2">
              <el-button size="small" type="warning" @click="rebootDevice" class="flex-1" plain>重启设备</el-button>
              <el-button size="small" type="danger" @click="resetDevice" class="flex-1" plain>恢复出厂</el-button>
            </div>
          </Card>

          <Card title="已实现功能">
            <ul class="text-[10px] text-emerald-300/80 leading-relaxed space-y-0.5 list-disc pl-3">
              <li>视频预览 (MJPEG)</li>
              <li>扫码触发 & 结果显示</li>
              <li>传感器 (曝光/增益)</li>
              <li>解码参数 & 码制配置</li>
              <li>输入/输出端子 & 指示灯</li>
              <li>数据输出格式 (分隔符)</li>
              <li>预设组 / 调参 / 读码率</li>
              <li>设备重启 / 出厂重置</li>
            </ul>
          </Card>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, onUnmounted, defineComponent, h } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  discoverWMaxDevices, connectWMaxDevice, disconnectWMaxDevice, getWMaxStatus,
  wmaxHandshake, wmaxLoadConfig,
  wmaxSetParams, wmaxSaveParams,
  wmaxAutoFocus, wmaxStartTune, wmaxCancelTune,
  wmaxTurnOnVideo, wmaxTurnOnTriggerImage,
  wmaxGetLastCode,
  wmaxTrigger, wmaxReboot, wmaxReset,
  wmaxLoadPreset, wmaxSavePreset,
  wmaxStartReadRate, wmaxStopReadRate, wmaxGetReadRate,
  wmaxSetOutputConfig, wmaxSetIndicatorConfig,
} from '@/api/wmax'

const Param = defineComponent({
  props: { label: String },
  setup(props, { slots }) {
    return () => h('div', { class: 'flex items-center gap-2' }, [
      h('span', { class: 'text-xs text-gray-400 w-[4.5rem] shrink-0' }, props.label),
      ...(slots.default?.() || [])
    ])
  }
})

const Card = defineComponent({
  props: { title: String },
  setup(props, { slots }) {
    return () => h('div', { class: 'bg-slate-800/60 rounded-xl border border-slate-700/80 p-3' }, [
      h('div', { class: 'flex items-center justify-between mb-2.5' }, [
        h('h4', { class: 'text-cyan-300 text-xs font-semibold' }, props.title),
        ...(slots['header-extra']?.() || []),
      ]),
      h('div', { class: 'space-y-2' }, slots.default?.()),
    ])
  }
})

const props = defineProps({
  wmaxIp: { type: String, default: '' },
  wmaxPort: { type: Number, default: 55266 },
})

const activeSection = ref('imaging')
const scanning = ref(false)
const discoveredDevices = ref([])
const connectedDevices = ref([])
const selectedIp = ref('')
const selectedPort = ref(55266)
const selectedPreset = ref(0)

const commonParams = reactive({
  decode_timeout: 3000, try_count: 5, shutter_delay: 0,
  decoder_type: 0, barcode_polarity: 0, barcode_mirror: 0,
  inverse_read: false, reverse_read: false,
  base_tilt_angle: 0, tilt_angle_range: 45,
})
const readingParams = reactive({
  enable_smart_mode: false, enable_detect_roi: false,
  focus_value: 128, illum_value: 100,
})
const detectRoi = reactive({ x: 0, y: 0, w: 640, h: 480 })
const codeConfig = reactive({
  output_len_limit: false, output_len: 32, code_mode: 0, redundant: 0,
})
const codeItems = ref([])
const inputParams = reactive({ polarity: 0, debounce_time: 20 })
const outputParams = reactive({
  signal_mask: 1, duration_ms: 150,
  ok_enabled: true, error_enabled: false, trigger_busy_enabled: false,
})
const indicatorParams = reactive({ mode: 3, mode_name: 'auto_on_scan' })
const sensorParams = reactive({ exposure_us: 60, gain: 8 })
const dataOutputFormat = reactive({ separator: ':', internal_separator: ',' })

const videoOn = ref(false)
const triggerImageOn = ref(false)
const previewSrc = ref('')
const lastCode = ref(null)
const focusLoading = ref(false)
const tuneLoading = ref(false)
const tuneProgress = ref(0)
const rrRunning = ref(false)
const rrResult = ref(null)

let pollingTimer = null
let imageTimer = null
let rrTimer = null

const _assignReactive = (target, source) => {
  if (!source) return
  Object.keys(target).forEach(k => {
    if (source[k] !== null && source[k] !== undefined) target[k] = source[k]
  })
}

// ── 设备发现 & 连接 ──────────────────────────────────────
const scanLan = async () => {
  scanning.value = true
  try {
    const res = await discoverWMaxDevices(2)
    discoveredDevices.value = res.data.devices || []
    ElMessage.success(`发现 ${discoveredDevices.value.length} 台设备`)
  } catch { ElMessage.error('扫描失败') }
  finally { scanning.value = false }
}

const connectDevice = async (ip, port = 55266) => {
  try {
    const res = await connectWMaxDevice(ip, port)
    if (res.data.success) {
      ElMessage.success('连接成功')
      selectedIp.value = ip
      selectedPort.value = port
      await initDevice(ip, port)
    } else { ElMessage.error(res.data.message || '连接失败') }
    refreshWmaxStatus()
  } catch { ElMessage.error('连接失败') }
}

const disconnectDevice = async (ip, port = 55266) => {
  try {
    await disconnectWMaxDevice(ip, port)
    ElMessage.success('已断开')
    if (selectedIp.value === ip) { selectedIp.value = ''; stopImagePolling() }
    refreshWmaxStatus()
  } catch {}
}

const selectDevice = (dev) => {
  selectedIp.value = dev.ip
  selectedPort.value = dev.port || 55266
  loadConfig()
}

const refreshWmaxStatus = async () => {
  try {
    const res = await getWMaxStatus()
    connectedDevices.value = res.data || []
  } catch {}
}

const initDevice = async (ip, port) => {
  try {
    await wmaxHandshake(ip, port)
    await loadConfig()
  } catch (e) { console.warn('initDevice:', e) }
}

// ── 配置读写 ─────────────────────────────────────────────
const loadConfig = async () => {
  if (!selectedIp.value) return
  try {
    const res = await wmaxLoadConfig(selectedIp.value, selectedPort.value)
    const data = res.data
    if (data.common_opt) _assignReactive(commonParams, data.common_opt)
    if (data.reading_opt) {
      _assignReactive(readingParams, data.reading_opt)
      if (data.reading_opt.detect_roi?.rect) _assignReactive(detectRoi, data.reading_opt.detect_roi.rect)
    }
    if (data.code_opt) {
      if (data.code_opt.codes) codeItems.value = data.code_opt.codes
      codeConfig.output_len_limit = data.code_opt.output_len_limit ?? false
      codeConfig.output_len = data.code_opt.output_len ?? 32
      codeConfig.code_mode = data.code_opt.code_mode ?? 0
      codeConfig.redundant = data.code_opt.redundant ?? 0
    }
    if (data.sensor_opt) {
      sensorParams.exposure_us = data.sensor_opt.exposure_us ?? 60
      sensorParams.gain = data.sensor_opt.gain ?? 8
    }
    if (data.input_opt) _assignReactive(inputParams, data.input_opt)
    if (data.output_opt) {
      outputParams.signal_mask = data.output_opt.signal_mask ?? 0
      outputParams.duration_ms = data.output_opt.duration_ms ?? 150
      outputParams.ok_enabled = data.output_opt.ok_enabled ?? false
      outputParams.error_enabled = data.output_opt.error_enabled ?? false
      outputParams.trigger_busy_enabled = data.output_opt.trigger_busy_enabled ?? false
    }
    if (data.indicator_opt) _assignReactive(indicatorParams, data.indicator_opt)
    if (data.data_output_format) {
      dataOutputFormat.separator = data.data_output_format.separator ?? ':'
      dataOutputFormat.internal_separator = data.data_output_format.internal_separator ?? ','
    }
    ElMessage.success('配置已读取')
  } catch { ElMessage.error('读取配置失败') }
}

const saveConfig = async () => {
  if (!selectedIp.value) return
  try {
    await ElMessageBox.confirm('保存全部参数到设备 Flash？', '确认保存')
    const res = await wmaxSaveParams(selectedIp.value, selectedPort.value, _buildFullParams())
    if (res.data.success) ElMessage.success('参数已保存')
    else ElMessage.error('保存失败')
  } catch {}
}

const _buildFullParams = () => ({
  sensor_params: { ...sensorParams },
  common_params: { ...commonParams },
  code_params: { codes: codeItems.value, ...codeConfig },
  reading_params: {
    ...readingParams,
    detect_roi: { id: 0, rect: { ...detectRoi } },
  },
  input_params: { ...inputParams },
  output_params: { signal_mask: outputParams.signal_mask, duration_ms: outputParams.duration_ms },
  indicator_params: { mode: indicatorParams.mode },
  data_output_format_params: { ...dataOutputFormat },
})

let configDebounce = null
const applyAllConfig = () => {
  clearTimeout(configDebounce)
  configDebounce = setTimeout(async () => {
    if (!selectedIp.value) return
    try {
      const res = await wmaxSetParams(selectedIp.value, selectedPort.value, _buildFullParams())
      if (res.data?.success === false) ElMessage.warning('配置下发失败')
    } catch (e) {
      ElMessage.error('配置下发异常: ' + (e.response?.data?.error || e.message))
    }
  }, 500)
}

const applyCodeConfig = () => {
  clearTimeout(configDebounce)
  configDebounce = setTimeout(async () => {
    if (!selectedIp.value) return
    try {
      const res = await wmaxSetParams(selectedIp.value, selectedPort.value, {
        code_params: { codes: codeItems.value, ...codeConfig },
      })
      if (res.data?.success === false) ElMessage.warning('码制配置下发失败')
    } catch (e) {
      ElMessage.error('码制配置异常: ' + (e.response?.data?.error || e.message))
    }
  }, 300)
}

const toggleAllCodes = (enable) => {
  codeItems.value.forEach(c => { c.enable = enable })
  applyCodeConfig()
}

// ── 输出端子 & 指示灯 ───────────────────────────────────
const _computeSignalMask = () => {
  if (outputParams.trigger_busy_enabled) return 1024
  let mask = 0
  if (outputParams.ok_enabled) mask |= 1
  if (outputParams.error_enabled) mask |= 4
  return mask
}

const onOutputSignalChange = () => {
  outputParams.signal_mask = _computeSignalMask()
  applyOutputConfig()
}

const onTriggerBusyChange = (val) => {
  if (val) { outputParams.ok_enabled = false; outputParams.error_enabled = false }
  outputParams.signal_mask = _computeSignalMask()
  applyOutputConfig()
}

const applyOutputConfig = async (save = false) => {
  if (!selectedIp.value) return
  try {
    const res = await wmaxSetOutputConfig(
      selectedIp.value, selectedPort.value,
      outputParams.signal_mask, outputParams.duration_ms, save === true)
    if (res.data?.success) {
      if (save === true) ElMessage.success('输出端子配置已保存')
    } else {
      ElMessage.warning('输出端子配置下发失败')
    }
  } catch (e) {
    ElMessage.error('输出端子异常: ' + (e.response?.data?.error || e.message))
  }
}

const applyIndicatorConfig = async (save = false) => {
  if (!selectedIp.value) return
  try {
    const res = await wmaxSetIndicatorConfig(
      selectedIp.value, selectedPort.value,
      indicatorParams.mode, save === true)
    if (res.data?.success) {
      if (save === true) ElMessage.success('指示灯配置已保存')
    } else {
      ElMessage.warning('指示灯配置下发失败')
    }
  } catch (e) {
    ElMessage.error('指示灯异常: ' + (e.response?.data?.error || e.message))
  }
}

const applyDataOutputFormat = async (save = false) => {
  if (!selectedIp.value) return
  try {
    const params = { data_output_format_params: { ...dataOutputFormat } }
    let res
    if (save === true) {
      res = await wmaxSaveParams(selectedIp.value, selectedPort.value, params)
    } else {
      res = await wmaxSetParams(selectedIp.value, selectedPort.value, params)
    }
    if (res.data?.success) {
      if (save === true) ElMessage.success('数据格式已保存')
    } else {
      ElMessage.warning('数据格式下发失败')
    }
  } catch (e) {
    ElMessage.error('数据格式异常: ' + (e.response?.data?.error || e.message))
  }
}

// ── 对焦 / 调参 / 读码率 / 预设 ──────────────────────────
const doAutoFocus = async () => {
  if (!selectedIp.value) return
  focusLoading.value = true
  try { await wmaxAutoFocus(selectedIp.value, selectedPort.value, true); ElMessage.success('对焦完成') }
  catch { ElMessage.error('对焦失败') }
  finally { focusLoading.value = false }
}

const startAutoTune = async () => {
  if (!selectedIp.value) return
  tuneLoading.value = true; tuneProgress.value = 0
  const t = setInterval(() => { if (tuneProgress.value < 95) tuneProgress.value += 5 }, 1000)
  try {
    await wmaxStartTune(selectedIp.value, selectedPort.value)
    tuneProgress.value = 100; ElMessage.success('调参完成'); await loadConfig()
  } catch { ElMessage.error('调参失败') }
  finally { clearInterval(t); tuneLoading.value = false }
}
const cancelAutoTune = async () => {
  if (!selectedIp.value) return
  try { await wmaxCancelTune(selectedIp.value, selectedPort.value); ElMessage.info('已取消') } catch {}
  tuneLoading.value = false
}

const startReadRate = async () => {
  if (!selectedIp.value) return
  rrRunning.value = true; rrResult.value = null
  try { await wmaxStartReadRate(selectedIp.value, selectedPort.value) } catch {}
  rrTimer = setInterval(async () => {
    try {
      const res = await wmaxGetReadRate(selectedIp.value, selectedPort.value)
      rrResult.value = res.data
      if (res.data && !res.data.is_running) { rrRunning.value = false; clearInterval(rrTimer) }
    } catch {}
  }, 1000)
}
const stopReadRate = async () => {
  if (!selectedIp.value) return
  try { await wmaxStopReadRate(selectedIp.value, selectedPort.value) } catch {}
  rrRunning.value = false; if (rrTimer) clearInterval(rrTimer)
}

const doLoadPreset = async () => {
  if (!selectedIp.value) return
  try {
    await wmaxLoadPreset(selectedIp.value, selectedPort.value, selectedPreset.value)
    await loadConfig(); ElMessage.success(`预设组 ${selectedPreset.value + 1} 已加载`)
  } catch { ElMessage.error('加载失败') }
}
const doSavePreset = async () => {
  if (!selectedIp.value) return
  try {
    await ElMessageBox.confirm(`保存到预设组 ${selectedPreset.value + 1}？`, '确认')
    await wmaxSavePreset(selectedIp.value, selectedPort.value, selectedPreset.value)
    ElMessage.success('预设已保存')
  } catch {}
}

// ── 视频 & 触发 ──────────────────────────────────────────
const toggleVideo = async () => {
  if (!selectedIp.value) return
  const on = !videoOn.value
  try {
    await wmaxTurnOnVideo(selectedIp.value, selectedPort.value, on)
    videoOn.value = on
    if (on) startImagePolling(); else stopImagePolling()
  } catch { ElMessage.error('视频操作失败') }
}
const startImagePolling = () => {
  stopImagePolling()
  const base = window.location.origin.replace(':6001', ':8001')
  previewSrc.value = `${base}/api/v1/scanner/wmax/stream?ip=${selectedIp.value}&port=${selectedPort.value}&fps=12&_t=${Date.now()}`
  imageTimer = setInterval(async () => {
    if (!selectedIp.value) return
    try { const r = await wmaxGetLastCode(selectedIp.value, selectedPort.value); lastCode.value = r.data } catch {}
  }, 1000)
}
const stopImagePolling = () => {
  if (imageTimer) { clearInterval(imageTimer); imageTimer = null }
  previewSrc.value = ''
}

const triggerOn = () => { if (selectedIp.value) wmaxTrigger(selectedIp.value, selectedPort.value, true) }
const triggerOff = () => { if (selectedIp.value) wmaxTrigger(selectedIp.value, selectedPort.value, false) }
const toggleTriggerImage = async () => {
  if (!selectedIp.value) return
  triggerImageOn.value = !triggerImageOn.value
  await wmaxTurnOnTriggerImage(selectedIp.value, selectedPort.value, triggerImageOn.value)
}

// ── 设备控制 ─────────────────────────────────────────────
const rebootDevice = async () => {
  if (!selectedIp.value) return
  try {
    await ElMessageBox.confirm('确定重启设备？', '确认', { type: 'warning' })
    await wmaxReboot(selectedIp.value, selectedPort.value)
    ElMessage.success('设备正在重启'); selectedIp.value = ''; stopImagePolling(); refreshWmaxStatus()
  } catch {}
}
const resetDevice = async () => {
  if (!selectedIp.value) return
  try {
    await ElMessageBox.confirm('恢复出厂设置将清除所有配置，确定？', '警告', { type: 'error' })
    await wmaxReset(selectedIp.value, selectedPort.value)
    ElMessage.success('已恢复出厂设置'); await loadConfig()
  } catch {}
}

// ── 生命周期 ─────────────────────────────────────────────
onMounted(async () => {
  await refreshWmaxStatus()
  if (props.wmaxIp) {
    selectedIp.value = props.wmaxIp
    selectedPort.value = props.wmaxPort
  } else if (connectedDevices.value.length) {
    const first = connectedDevices.value[0]
    selectedIp.value = first.ip
    selectedPort.value = first.port || 55266
  }
  if (selectedIp.value) await loadConfig()
  pollingTimer = setInterval(refreshWmaxStatus, 5000)
})
onUnmounted(() => {
  if (pollingTimer) clearInterval(pollingTimer)
  if (rrTimer) clearInterval(rrTimer)
  stopImagePolling()
})
</script>

<style scoped>
.wmax-tabs :deep(.el-tabs__header) { background: #1e293b; border-color: #334155; border-radius: 12px 12px 0 0; }
.wmax-tabs :deep(.el-tabs__item) { color: #64748b; font-size: 13px; }
.wmax-tabs :deep(.el-tabs__item.is-active) { color: #67e8f9; background: #0f172a; }
.wmax-tabs :deep(.el-tabs__content) { display: none; }
.wmax-tabs :deep(.el-tabs__nav-wrap::after) { display: none; }
</style>
