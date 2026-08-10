<template>
  <div class="flex gap-4">
    <!-- 左：设备管理 -->
    <div class="flex-1">
      <div class="text-xs text-gray-500 mb-2">外部设备（称重器等）为设备级配置，按工位绑定，不随当前项目切换。</div>
      <div class="flex items-center justify-between mb-4">
        <h3 class="text-cyan-300 font-semibold">外部设备</h3>
        <div class="flex gap-2">
          <el-dropdown @command="applyPreset" size="small">
            <el-button size="small">预设模板 ▼</el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="weighing_modbus">称重器 - Modbus ASCII</el-dropdown-item>
                <el-dropdown-item command="weighing_continuous">称重器 - 连续接收</el-dropdown-item>
                <el-dropdown-item command="weighing_anheng">称重器 - 安衡指令应答</el-dropdown-item>
                <el-dropdown-item command="tcp_sensor">TCP 传感器</el-dropdown-item>
                <el-dropdown-item command="plc_pulse_delta">台达 PLC - 完成脉冲 (气阀)</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
          <el-button v-if="systemStore.developerMode" size="small" type="warning" plain @click="showSimulate = true">
            模拟数据
          </el-button>
          <el-button size="small" type="success" @click="openAdd">添加设备</el-button>
        </div>
      </div>

      <div v-if="devices.length === 0" class="text-gray-500 text-sm text-center py-8">
        暂无外部设备，点击「添加设备」接入称重器、传感器等
      </div>
      <div class="grid grid-cols-2 gap-3">
        <div v-for="dev in devices" :key="dev.id"
             class="bg-slate-800/60 rounded-lg border p-4"
             :class="getStatus(dev.id) === 'connected' ? 'border-green-700' : 'border-slate-700'">
          <div class="flex items-center justify-between mb-2">
            <span class="font-medium">{{ dev.name }}</span>
            <div class="flex items-center gap-1">
              <el-tag :type="roleTagType(dev.device_role)" size="small" effect="dark">
                {{ roleLabel(dev.device_role) }}
              </el-tag>
              <el-tag size="small" effect="plain">{{ dev.protocol.toUpperCase() }}</el-tag>
              <el-tag :type="statusTagType(getStatus(dev.id))" size="small">
                {{ statusLabel(getStatus(dev.id)) }}
              </el-tag>
            </div>
          </div>
          <div class="text-xs text-gray-400 space-y-1">
            <div v-if="dev.ip">地址: {{ dev.ip }}:{{ dev.port }}</div>
            <div v-if="dev.serial_port">串口: {{ dev.serial_port }} @ {{ dev.serial_baud }}</div>
            <div>{{ channelLabel(dev.channel_id) }}<span v-if="dev.station_id" class="text-yellow-400 ml-2">(集群标识: {{ dev.station_id }})</span><span v-if="dev.pairing_group" class="text-purple-300 ml-2">(分组: {{ dev.pairing_group }})</span></div>
            <div v-if="dev.protocol === 'modbus_pulse'">
              完成脉冲: {{ pulseSummary(dev) }}
            </div>
            <div v-else>解析: {{ dev.parse_mode }} | 目标: {{ targetLabel(dev.data_target) }}</div>
            <div v-if="getLastData(dev.id)" class="text-cyan-300 truncate">
              最近: {{ getLastData(dev.id) }}
            </div>
          </div>
          <div class="flex gap-1 mt-3 flex-wrap">
            <el-button size="small" @click="testDev(dev)" :loading="testingDeviceId === dev.id">测试</el-button>
            <template v-if="dev.protocol === 'serial_command'">
              <el-button size="small" type="warning" plain @click="sendCmd(dev, dev.protocol_config?.tare_command || 'T')">去皮</el-button>
              <el-button size="small" type="warning" plain @click="sendCmd(dev, dev.protocol_config?.zero_command || 'Z')">置零</el-button>
            </template>
            <template v-if="dev.protocol === 'mock_weight'">
              <el-button size="small" type="warning" plain @click="sendCmd(dev, 'T')">去皮</el-button>
              <el-button size="small" type="warning" plain @click="sendCmd(dev, 'Z')">置零</el-button>
            </template>
            <el-button v-if="dev.protocol === 'modbus_pulse'" size="small" type="warning" plain
                       :loading="pulsingDeviceId === dev.id" @click="sendPulse(dev)">试发脉冲</el-button>
            <el-button size="small" type="primary" @click="editDev(dev)">编辑</el-button>
            <el-button size="small" type="danger" @click="handleDelete(dev)">删除</el-button>
          </div>
        </div>
      </div>
    </div>

    <!-- 右：数据日志 -->
    <div class="w-[400px] bg-slate-800/50 rounded-lg border border-slate-700 p-4 overflow-auto">
      <div class="flex items-center justify-between mb-3">
        <h3 class="text-cyan-300 font-semibold">数据日志</h3>
        <div class="flex gap-1">
          <el-button size="small" @click="loadLogs(true)" :loading="logsLoading">刷新</el-button>
          <el-button size="small" type="danger" @click="handleClearLogs">清空</el-button>
        </div>
      </div>
      <div v-if="logs.length === 0" class="text-gray-500 text-sm text-center py-8">暂无记录</div>
      <div v-else class="space-y-2">
        <div v-for="log in logs" :key="log.id"
             class="bg-slate-700/40 rounded p-2 text-xs"
             :class="log.is_valid ? '' : 'border border-red-800/30'">
          <div class="flex justify-between">
            <span class="font-mono text-cyan-200 truncate" :title="log.raw_data">{{ log.raw_data }}</span>
            <span class="text-gray-500 whitespace-nowrap ml-2">{{ formatTime(log.created_at) }}</span>
          </div>
          <div v-if="log.parsed_data" class="text-gray-400 mt-1 truncate">
            {{ JSON.stringify(log.parsed_data) }}
          </div>
          <div v-if="log.box_serial" class="text-green-400 mt-0.5">目标编号: {{ log.box_serial }}</div>
          <div v-if="!log.is_valid" class="text-red-400 mt-0.5">{{ log.error_msg }}</div>
        </div>
      </div>
    </div>

    <!-- 添加/编辑对话框 -->
    <el-dialog v-model="showDialog" :title="editingId ? '编辑设备' : '添加设备'"
               width="580px" class="mes-dialog" destroy-on-close>
      <el-form :model="form" label-width="90px" size="small">
        <el-form-item label="名称" required>
          <el-input v-model="form.name" placeholder="如: 工位3称重器" />
        </el-form-item>

        <div class="grid grid-cols-2 gap-4">
          <el-form-item label="设备类型">
            <el-select v-model="form.device_role" class="w-full">
              <el-option label="称重器" value="weight" />
              <el-option label="传感器" value="sensor" />
              <el-option label="PLC" value="plc" />
              <el-option label="自定义" value="custom" />
            </el-select>
          </el-form-item>
          <el-form-item label="通信协议">
            <el-select v-model="form.protocol" class="w-full" @change="onProtocolChange">
              <el-option label="TCP 直连" value="tcp" />
              <el-option label="Modbus TCP" value="modbus_tcp" />
              <el-option label="串口 (RS232/485)" value="serial" />
              <el-option label="串口 Modbus ASCII" value="serial_modbus_ascii" />
              <el-option label="串口连续接收" value="serial_continuous" />
              <el-option label="串口指令应答 (发R读数/T去皮)" value="serial_command" />
              <el-option label="HTTP 轮询" value="http_poll" />
              <el-option label="模拟称重 (无硬件测试)" value="mock_weight" />
              <el-option label="Modbus 完成脉冲 (PLC 控气阀)" value="modbus_pulse" />
            </el-select>
          </el-form-item>
        </div>

        <!-- TCP / Modbus 地址 -->
        <div v-if="form.protocol === 'tcp' || form.protocol === 'modbus_tcp' || isPulseTcp" class="grid grid-cols-2 gap-4">
          <el-form-item label="IP 地址">
            <el-input v-model="form.ip" placeholder="192.168.0.200" />
          </el-form-item>
          <el-form-item label="端口">
            <el-input-number v-model="form.port" :min="0" :precision="0" class="!w-full" controls-position="right" />
          </el-form-item>
        </div>

        <!-- 串口 -->
        <div v-if="isSerialProtocol">
          <div class="grid grid-cols-2 gap-4">
            <el-form-item label="串口号">
              <el-input v-model="form.serial_port" placeholder="COM3 或 /dev/ttyUSB0" />
            </el-form-item>
            <el-form-item label="波特率">
              <el-select v-model="form.serial_baud" class="w-full">
                <el-option v-for="b in [1200,2400,4800,9600,19200,38400,57600,115200]" :key="b" :label="b" :value="b" />
              </el-select>
            </el-form-item>
          </div>
          <div class="grid grid-cols-3 gap-4">
            <el-form-item label="数据位">
              <el-select v-model="serialBytesize" class="w-full">
                <el-option :value="5" label="5" />
                <el-option :value="6" label="6" />
                <el-option :value="7" label="7" />
                <el-option :value="8" label="8" />
              </el-select>
            </el-form-item>
            <el-form-item label="校验">
              <el-select v-model="serialParity" class="w-full">
                <el-option value="N" label="无 (N)" />
                <el-option value="E" label="偶 (E)" />
                <el-option value="O" label="奇 (O)" />
                <el-option value="M" label="标记 (M)" />
                <el-option value="S" label="空格 (S)" />
              </el-select>
            </el-form-item>
            <el-form-item label="停止位">
              <el-select v-model="serialStopbits" class="w-full">
                <el-option :value="1" label="1" />
                <el-option :value="1.5" label="1.5" />
                <el-option :value="2" label="2" />
              </el-select>
            </el-form-item>
          </div>
        </div>

        <!-- Modbus ASCII 参数 -->
        <div v-if="form.protocol === 'serial_modbus_ascii'" class="bg-slate-900/50 rounded p-3 mb-3">
          <div class="text-xs text-gray-400 mb-2">Modbus ASCII 主从参数</div>
          <div class="grid grid-cols-3 gap-2">
            <div>
              <div class="text-xs text-gray-500 mb-1">从站地址</div>
              <el-input-number v-model="modbusSlaveId" :min="0" :precision="2" size="small" class="!w-full" controls-position="right" />
            </div>
            <div>
              <div class="text-xs text-gray-500 mb-1">寄存器地址</div>
              <el-input-number v-model="modbusRegister" :min="0" :precision="0" size="small" class="!w-full" controls-position="right" />
            </div>
            <div>
              <div class="text-xs text-gray-500 mb-1">寄存器数量</div>
              <el-input-number v-model="modbusCount" :min="0" :precision="0" size="small" class="!w-full" controls-position="right" />
            </div>
          </div>
          <div class="grid grid-cols-3 gap-2 mt-2">
            <div>
              <div class="text-xs text-gray-500 mb-1">字节序</div>
              <el-select v-model="modbusByteOrder" size="small" class="w-full">
                <el-option label="大端 (H4H3L2L1)" value="H4H3L2L1" />
                <el-option label="小端 (L2L1H4H3)" value="L2L1H4H3" />
              </el-select>
            </div>
            <div>
              <div class="text-xs text-gray-500 mb-1">比例系数</div>
              <el-input-number v-model="modbusScale" :min="0" :precision="2" size="small" class="!w-full" controls-position="right" />
            </div>
            <div>
              <div class="text-xs text-gray-500 mb-1">轮询间隔 (秒)</div>
              <el-input-number v-model="modbusPollInterval" :min="0" :precision="2" size="small" class="!w-full" controls-position="right" />
            </div>
          </div>
        </div>

        <!-- 连续接收模式参数 -->
        <div v-if="form.protocol === 'serial_continuous'" class="bg-slate-900/50 rounded p-3 mb-3">
          <div class="text-xs text-gray-400 mb-2">连续接收模式（设备主动推送数据）</div>
          <div class="grid grid-cols-2 gap-2">
            <div>
              <div class="text-xs text-gray-500 mb-1">数据格式</div>
              <el-select v-model="continuousDataFormat" size="small" class="w-full">
                <el-option label="纯文本 ASCII" value="ascii" />
                <el-option label="Modbus ASCII 帧" value="modbus_ascii" />
              </el-select>
            </div>
            <div>
              <div class="text-xs text-gray-500 mb-1">行分隔符</div>
              <el-input v-model="continuousDelimiter" size="small" placeholder="\r\n" />
            </div>
          </div>
        </div>

        <!-- 串口指令应答参数 -->
        <div v-if="form.protocol === 'serial_command'" class="bg-slate-900/50 rounded p-3 mb-3">
          <div class="text-xs text-gray-400 mb-2">指令应答模式（上位机发指令、仪表回一帧；适配安衡等台秤）</div>
          <div class="grid grid-cols-3 gap-2">
            <div>
              <div class="text-xs text-gray-500 mb-1">查询指令(读重量)</div>
              <el-input v-model="cmdQuery" size="small" placeholder="R" />
            </div>
            <div>
              <div class="text-xs text-gray-500 mb-1">去皮指令</div>
              <el-input v-model="cmdTare" size="small" placeholder="T" />
            </div>
            <div>
              <div class="text-xs text-gray-500 mb-1">置零指令</div>
              <el-input v-model="cmdZero" size="small" placeholder="Z" />
            </div>
          </div>
          <div class="grid grid-cols-2 gap-2 mt-2">
            <div>
              <div class="text-xs text-gray-500 mb-1">指令结尾符</div>
              <el-input v-model="cmdSuffix" size="small" placeholder="\r\n" />
            </div>
            <div>
              <div class="text-xs text-gray-500 mb-1">轮询间隔 (秒)</div>
              <el-input-number v-model="cmdPollInterval" :min="0.1" :step="0.5" :precision="2" size="small" class="!w-full" controls-position="right" />
            </div>
          </div>
          <div class="text-xs text-gray-500 mt-2">
            保存后可在设备卡片上点「去皮 / 置零」主动下发指令。安衡默认：读=R、去皮=T、置零=Z。
          </div>
        </div>

        <!-- 模拟称重参数（无硬件测试） -->
        <div v-if="form.protocol === 'mock_weight'" class="bg-slate-900/50 rounded p-3 mb-3">
          <div class="text-xs text-gray-400 mb-2">模拟称重（不连真秤，按脚本生成重量数据流，用于无硬件时测试整条链路）</div>
          <div class="grid grid-cols-3 gap-2 mb-2">
            <div>
              <div class="text-xs text-gray-500 mb-1">出数间隔(秒)</div>
              <el-input-number v-model="mockInterval" :min="0.1" :step="0.1" :precision="2" size="small" class="!w-full" controls-position="right" />
            </div>
            <div>
              <div class="text-xs text-gray-500 mb-1">小数位</div>
              <el-input-number v-model="mockDecimals" :min="0" :max="4" size="small" class="!w-full" controls-position="right" />
            </div>
            <div>
              <div class="text-xs text-gray-500 mb-1">循环播放</div>
              <el-switch v-model="mockLoop" />
            </div>
          </div>
          <div class="mb-2">
            <div class="text-xs text-gray-500 mb-1">输出格式（{value}=去皮后净重, {gross}=毛重）</div>
            <el-input v-model="mockFormat" size="small" placeholder="{value} 或 ST,NT,{value}kg" />
          </div>
          <div>
            <div class="text-xs text-gray-500 mb-1">投料脚本（JSON：weight=毛重kg, hold=持续秒，按序播放）</div>
            <el-input v-model="mockScriptText" type="textarea" :rows="6" size="small"
                      placeholder='[{"weight":0.0,"hold":2},{"weight":0.5,"hold":4}]' />
          </div>
          <div class="text-xs text-gray-500 mt-2">
            保存并启用后，设备会像真秤一样持续吐数，右侧数据日志/卡片「最近」可见；点卡片「去皮/置零」可测软件控制。
          </div>
        </div>

        <!-- Modbus 完成脉冲（PLC 控气阀） -->
        <div v-if="isPulseProtocol" class="bg-slate-900/50 rounded p-3 mb-3">
          <div class="text-xs text-gray-400 mb-2">
            完成脉冲（本工位判定完成时，给 PLC 的一个点位写 ON → 等一会 → 写 OFF，PLC 收到上升沿去控气阀）
          </div>
          <div class="grid grid-cols-3 gap-2 mb-2">
            <div>
              <div class="text-xs text-gray-500 mb-1">连接方式</div>
              <el-select v-model="pulseTransport" size="small" class="w-full">
                <el-option label="Modbus TCP (网线)" value="tcp" />
                <el-option label="Modbus RTU (485 串口)" value="rtu" />
              </el-select>
            </div>
            <div>
              <div class="text-xs text-gray-500 mb-1">从站号 (PLC 站号)</div>
              <el-input-number v-model="pulseSlaveId" :min="0" :max="247" :precision="0" size="small" class="!w-full" controls-position="right" />
            </div>
            <div>
              <div class="text-xs text-gray-500 mb-1">写入类型</div>
              <el-select v-model="pulseTarget" size="small" class="w-full">
                <el-option label="线圈 M/Y (推荐)" value="coil" />
                <el-option label="保持寄存器 D" value="register" />
              </el-select>
            </div>
          </div>
          <div class="grid grid-cols-3 gap-2 mb-2">
            <div>
              <div class="text-xs text-gray-500 mb-1">地址填写方式</div>
              <el-select v-model="pulseAddressMode" size="small" class="w-full">
                <el-option label="台达 M 编号 (填 100 = M100)" value="delta_m" />
                <el-option label="Modbus 原始地址 (0 基)" value="raw" />
                <el-option label="文档编号 (1 基, 如 00001/40001)" value="doc1" />
              </el-select>
            </div>
            <div>
              <div class="text-xs text-gray-500 mb-1">地址</div>
              <el-input-number v-model="pulseAddress" :min="0" :precision="0" size="small" class="!w-full" controls-position="right" />
            </div>
            <div>
              <div class="text-xs text-gray-500 mb-1">脉冲宽度 (毫秒)</div>
              <el-input-number v-model="pulseMs" :min="0" :max="60000" :step="50" :precision="0" size="small" class="!w-full" controls-position="right" />
            </div>
          </div>
          <div class="grid grid-cols-3 gap-2">
            <div>
              <div class="text-xs text-gray-500 mb-1">有效值 (ON)</div>
              <el-input-number v-model="pulseOnValue" :min="0" :precision="0" size="small" class="!w-full" controls-position="right" />
            </div>
            <div>
              <div class="text-xs text-gray-500 mb-1">复位值 (OFF)</div>
              <el-input-number v-model="pulseOffValue" :min="0" :precision="0" size="small" class="!w-full" controls-position="right" />
            </div>
            <div>
              <div class="text-xs text-gray-500 mb-1">通讯超时 (秒)</div>
              <el-input-number v-model="pulseTimeout" :min="1" :max="30" :precision="0" size="small" class="!w-full" controls-position="right" />
            </div>
          </div>
          <div class="text-xs text-gray-500 mt-2">
            当前将写 <b class="text-cyan-300">{{ pulsePreview }}</b>；脉冲宽度填 0 表示只写 ON 不复位（由 PLC 程序自己清）。
          </div>

          <el-divider class="!my-3" />
          <div class="flex items-center justify-between mb-2">
            <span class="text-xs text-gray-400">逐件覆盖（打螺丝）完成时自动发脉冲</span>
            <el-switch v-model="pulseTriggerEnabled" size="small" />
          </div>
          <div v-if="pulseTriggerEnabled" class="grid grid-cols-2 gap-2">
            <div>
              <div class="text-xs text-gray-500 mb-1">触发时机</div>
              <el-select v-model="pulseTriggerMode" size="small" class="w-full">
                <el-option label="周期判合格时 (推荐, 更稳)" value="cycle_ok" />
                <el-option label="全部覆盖完成时 (更快, 不等结算)" value="all_covered" />
              </el-select>
            </div>
            <div>
              <div class="text-xs text-gray-500 mb-1">最短触发间隔 (毫秒)</div>
              <el-input-number v-model="pulseCooldownMs" :min="0" :max="60000" :step="100" :precision="0" size="small" class="!w-full" controls-position="right" />
            </div>
          </div>
          <div v-if="pulseTriggerEnabled" class="text-xs text-gray-500 mt-2 leading-relaxed">
            <div v-if="pulseTriggerMode === 'cycle_ok'">
              只有本周期判 <b>合格</b> 并落账才发脉冲；NG、超时强制结算都不发。
            </div>
            <div v-else>
              本周期所有工序<b>首次全部覆盖</b>那一刻就发，不等收尾/结算。同一周期只发一次。
            </div>
            <div class="text-amber-300 mt-1">
              必须在下面「绑定工位」选中对应工位，未绑定工位的设备不会被自动触发。
            </div>
          </div>
        </div>

        <!-- HTTP -->
        <el-form-item v-if="form.protocol === 'http_poll'" label="URL">
          <el-input v-model="httpUrl" placeholder="http://192.168.0.200/api/weight" />
        </el-form-item>

        <el-divider class="!my-2" />

        <div v-if="!isPulseProtocol" class="grid grid-cols-2 gap-4">
          <el-form-item label="解析模式">
            <el-select v-model="form.parse_mode" class="w-full">
              <el-option label="直接取值" value="direct" />
              <el-option label="分隔符拆分" value="split" />
              <el-option label="正则提取" value="regex" />
              <el-option label="JSON 字段" value="json_path" />
            </el-select>
          </el-form-item>
          <el-form-item label="数据流向">
            <el-select v-model="form.data_target" class="w-full">
              <el-option label="集群汇总" value="cluster" />
              <el-option label="MES 扩展字段" value="extra_fields" />
              <el-option label="两者都发" value="both" />
            </el-select>
          </el-form-item>
        </div>

        <div v-if="!isPulseProtocol" class="grid grid-cols-2 gap-4">
          <el-form-item label="工位标识">
            <el-input v-model="form.station_id" placeholder="如 C（用于集群汇总）" />
          </el-form-item>
          <el-form-item label="绑定工位">
            <el-select v-model="form.channel_id" placeholder="选择工位" class="!w-full">
              <el-option v-for="opt in channelOptions" :key="opt.value" :label="opt.label" :value="opt.value" />
            </el-select>
          </el-form-item>
        </div>

        <el-form-item v-if="isPulseProtocol" label="绑定工位" required>
          <el-select v-model="form.channel_id" placeholder="选择工位" class="!w-full">
            <el-option v-for="opt in channelOptions" :key="opt.value" :label="opt.label" :value="opt.value" />
          </el-select>
        </el-form-item>

        <el-form-item v-if="!isPulseProtocol" label="设备分组号">
          <el-input v-model="form.pairing_group" placeholder="如 scale-c；留空则按绑定工位自动配对" clearable>
            <template #append>
              <el-tooltip placement="top" :show-after="300">
                <template #content>
                  <div class="max-w-xs text-xs">
                    与扫码枪之间的<b>配对标识</b>，和"绑定工位"无关。<br>
                    本设备和对应扫码枪填相同分组号即配成一对，<br>
                    扫码数据会自动注入本设备（如称重器带上条码）。<br>
                    <b>留空则沿用老逻辑</b>——按"绑定工位"匹配。
                  </div>
                </template>
                <el-icon class="text-gray-400 cursor-help"><QuestionFilled /></el-icon>
              </el-tooltip>
            </template>
          </el-input>
        </el-form-item>

        <!-- 分隔符解析配置 -->
        <div v-if="form.parse_mode === 'split'" class="bg-slate-900/50 rounded p-3 mb-3">
          <div class="text-xs text-gray-400 mb-2">分隔符配置（如数据格式: SN-001,25.30）</div>
          <div class="grid grid-cols-3 gap-2">
            <div>
              <div class="text-xs text-gray-500 mb-1">分隔符</div>
              <el-input v-model="splitDelimiter" size="small" placeholder="," />
            </div>
            <div>
              <div class="text-xs text-gray-500 mb-1">条码位置</div>
              <el-input-number v-model="splitBarcodeIdx" :min="0" :precision="0" size="small" class="!w-full" controls-position="right" />
            </div>
            <div>
              <div class="text-xs text-gray-500 mb-1">数值位置</div>
              <el-input-number v-model="splitValueIdx" :min="0" :precision="0" size="small" class="!w-full" controls-position="right" />
            </div>
          </div>
        </div>

        <!-- 范围校验 -->
        <div v-if="form.device_role === 'weight'" class="bg-slate-900/50 rounded p-3 mb-3">
          <div class="text-xs text-gray-400 mb-2">重量范围校验（可选，超出范围标记 NG）</div>
          <div class="grid grid-cols-2 gap-3">
            <div>
              <div class="text-xs text-gray-500 mb-1">最小 (kg)</div>
              <el-input-number v-model="weightMin" :min="0" :precision="2" size="small" class="!w-full" controls-position="right" />
            </div>
            <div>
              <div class="text-xs text-gray-500 mb-1">最大 (kg)</div>
              <el-input-number v-model="weightMax" :min="0" :precision="2" size="small" class="!w-full" controls-position="right" />
            </div>
          </div>
        </div>

        <!-- v3.1.1 配对模式（仅称重） -->
        <div v-if="form.device_role === 'weight'" class="bg-slate-900/50 rounded p-3 mb-3">
          <div class="flex items-center justify-between mb-2">
            <span class="text-xs text-gray-400">配对模式（决定扫码与重量怎么绑）</span>
            <el-select v-model="form.pairing_mode" size="small" style="width:200px">
              <el-option value="stable" label="稳定值绑定（默认）" />
              <el-option value="instant" label="即时快照绑定" />
            </el-select>
          </div>
          <div class="text-xs text-gray-500 leading-relaxed">
            <div v-if="form.pairing_mode === 'stable'">
              等称重稳定后才用稳定值与条码绑定。<b>抖动 / 空载 / 未稳定的数据会被过滤</b>。
              适用于"工件压上 → 静止 → 离开 → 回零"的工位制场景。
            </div>
            <div v-else>
              <b>扫码瞬间立即用最近一次称重读数绑定</b>，派发后条码立刻消费，下一次扫码再读最新值。
              适用于"流水线、工件不会回零、不允许丢数据"的连续上料场景。
              <span class="text-amber-300">代价：A 还在秤上 B 已压上时，B 的重量可能等于 A+B 的合重，请评估能否接受。</span>
            </div>
          </div>
        </div>

        <!-- 稳定值判定（仅称重，instant 模式下隐藏：instant 不依赖状态机） -->
        <div v-if="form.device_role === 'weight' && form.pairing_mode !== 'instant'" class="bg-slate-900/50 rounded p-3 mb-3">
          <div class="flex items-center justify-between mb-2">
            <span class="text-xs text-gray-400">稳定值判定（抖动、空载数据将被过滤，不上报）</span>
            <el-switch v-model="form.stable_enabled" size="small" />
          </div>
          <div v-if="form.stable_enabled" class="grid grid-cols-3 gap-3">
            <div>
              <div class="text-xs text-gray-500 mb-1">稳定波动 ±Δ (kg)</div>
              <el-input-number v-model="form.stable_delta" :min="0.001" :step="0.01" :precision="3" size="small" class="!w-full" controls-position="right" />
            </div>
            <div>
              <div class="text-xs text-gray-500 mb-1">连续稳定次数</div>
              <el-input-number v-model="form.stable_count" :min="2" :max="30" :precision="0" size="small" class="!w-full" controls-position="right" />
            </div>
            <div>
              <div class="text-xs text-gray-500 mb-1">空载阈值 (kg)</div>
              <el-input-number v-model="form.zero_threshold" :min="0" :step="0.01" :precision="3" size="small" class="!w-full" controls-position="right" />
            </div>
          </div>
          <div class="text-xs text-gray-500 mt-2">
            连续 <b>{{ form.stable_count }}</b> 次读数的最大差 ≤ <b>{{ form.stable_delta }}</b> kg 才视为稳定，上报该窗口的中位数；|值| < <b>{{ form.zero_threshold }}</b> kg 视为空载并清空条码。
          </div>
        </div>

        <!-- 有重无码告警（仅称重，instant 模式下隐藏：状态机不跑→永远不触发） -->
        <div v-if="form.device_role === 'weight' && form.pairing_mode !== 'instant'" class="bg-slate-900/50 rounded p-3 mb-3">
          <div class="flex items-center justify-between mb-2">
            <span class="text-xs text-gray-400">有重无码告警（稳定值非零但未扫码超时触发）</span>
            <el-switch v-model="form.weight_no_barcode_alarm_enabled" size="small" />
          </div>
          <div v-if="form.weight_no_barcode_alarm_enabled" class="grid grid-cols-1 gap-3">
            <div>
              <div class="text-xs text-gray-500 mb-1">超时时间 (秒)</div>
              <el-input-number v-model="form.weight_no_barcode_alarm_delay_sec" :min="1" :max="600" :precision="0" size="small" class="!w-full" controls-position="right" />
              <div class="text-xs text-gray-500 mt-1">
                稳定重量 ≥ 空载阈值持续 <b>{{ form.weight_no_barcode_alarm_delay_sec }}</b> 秒仍未收到条码，则派发 weight_no_barcode 事件到 MES 并触发报警灯。默认关闭。
              </div>
            </div>
          </div>
        </div>

        <el-form-item>
          <el-switch v-model="form.enabled" size="small" />
          <span class="text-xs text-gray-300 ml-2">启用</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button size="small" @click="showDialog = false">取消</el-button>
        <el-button size="small" type="primary" @click="handleSave" :loading="saving">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showSimulate" title="模拟外部设备数据（调试）" width="460px" class="mes-dialog" destroy-on-close>
      <el-form label-width="100px" size="small">
        <el-form-item label="设备">
          <el-select v-model="simulateForm.device_id" class="w-full" clearable placeholder="可不选，使用虚拟称重器">
            <el-option v-for="dev in devices" :key="dev.id" :label="dev.name" :value="dev.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="原始数据" required>
          <el-input v-model="simulateForm.raw_data" placeholder="12.34kg" />
        </el-form-item>
        <el-form-item label="条码">
          <el-input v-model="simulateForm.barcode" placeholder="AUTO_QA_BOX_001，可留空" clearable />
        </el-form-item>
        <el-form-item label="工位">
          <el-select v-model="simulateForm.channel_id" class="w-full">
            <el-option v-for="opt in channelOptions" :key="opt.value" :label="opt.label" :value="opt.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="真实链路">
          <el-switch v-model="simulateForm.full_chain" />
          <div class="text-xs text-gray-500 mt-1">关闭时只写日志；开启时会按设备配置进入 extra fields / 集群链路。</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button size="small" @click="showSimulate = false">取消</el-button>
        <el-button size="small" type="primary" @click="submitSimulateData" :loading="simulating">注入数据</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { QuestionFilled } from '@element-plus/icons-vue'
import {
  getExternalDevices, createExternalDevice, updateExternalDevice,
  deleteExternalDevice, getExternalDeviceStatus, testExternalDevice,
  getExternalDeviceLogs, clearExternalDeviceLogs, simulateExternalDeviceData,
  sendExternalDeviceCommand, pulseExternalDevice
} from '@/api/external_device'
import { getWorkstations } from '@/api/detection'
import { useSystemStore } from '@/store/useSystemStore'
import { dbg, dbgErr } from '@/utils/debug'
import { usePollingStore } from '@/store/usePollingStore'

const pollingStore = usePollingStore()

const systemStore = useSystemStore()
const devices = ref([])
const statusMap = ref({})
const logs = ref([])
const showDialog = ref(false)
const editingId = ref(null)
const saving = ref(false)
const logsLoading = ref(false)
const testingDeviceId = ref(null)
const showSimulate = ref(false)
const simulating = ref(false)
const channelCount = ref(1)
const channelOptions = computed(() =>
  Array.from({ length: channelCount.value }, (_, i) => ({
    value: i,
    label: `工位 ${i + 1}`,
  }))
)
const channelLabel = (ch) => (ch == null ? '未绑定' : `工位 ${ch + 1}`)

const defaultForm = () => ({
  name: '', device_role: 'weight', protocol: 'tcp',
  ip: '', port: 502, serial_port: '', serial_baud: 9600,
  protocol_config: {}, parse_mode: 'direct', parse_config: {},
  station_id: '', channel_id: 0, pairing_group: '', data_target: 'cluster',
  validation_rules: {}, enabled: true,
  stable_enabled: true,
  stable_delta: 0.05,
  stable_count: 5,
  zero_threshold: 0.05,
  weight_no_barcode_alarm_enabled: false,
  weight_no_barcode_alarm_delay_sec: 10,
  pairing_mode: 'stable',
})
const form = ref(defaultForm())
const simulateForm = ref({
  device_id: null,
  raw_data: '12.34kg',
  barcode: 'AUTO_QA_BOX_001',
  channel_id: 0,
  full_chain: false,
})

const splitDelimiter = ref(',')
const splitBarcodeIdx = ref(0)
const splitValueIdx = ref(1)
const weightMin = ref(0)
const weightMax = ref(100)
const httpUrl = ref('')

const modbusSlaveId = ref(1)
const modbusRegister = ref(41201)
const modbusCount = ref(2)
const modbusByteOrder = ref('H4H3L2L1')
const modbusScale = ref(1.0)
const modbusPollInterval = ref(0.5)
const continuousDataFormat = ref('ascii')
const continuousDelimiter = ref('\\r\\n')

// 串口指令应答参数
const cmdQuery = ref('R')
const cmdTare = ref('T')
const cmdZero = ref('Z')
const cmdSuffix = ref('\\r\\n')
const cmdPollInterval = ref(1.0)

// 模拟称重参数（无硬件测试）
const DEFAULT_MOCK_SCRIPT = JSON.stringify(
  [
    { weight: 0.0, hold: 2.0 },
    { weight: 0.2, hold: 3.0 },
    { weight: 0.35, hold: 1.5 },
    { weight: 0.5, hold: 4.0 },
  ], null, 2)
const mockScriptText = ref(DEFAULT_MOCK_SCRIPT)
const mockFormat = ref('{value}')
const mockInterval = ref(0.5)
const mockDecimals = ref(3)
const mockLoop = ref(true)

// Modbus 完成脉冲参数（PLC 控气阀）
const PULSE_DEFAULTS = {
  transport: 'tcp', slave_id: 1, target: 'coil',
  address_mode: 'delta_m', address: 100,
  on_value: 1, off_value: 0, pulse_ms: 300, timeout: 3,
  trigger_enabled: true, trigger_mode: 'cycle_ok', cooldown_ms: 1000,
}
const pulseTransport = ref(PULSE_DEFAULTS.transport)
const pulseSlaveId = ref(PULSE_DEFAULTS.slave_id)
const pulseTarget = ref(PULSE_DEFAULTS.target)
const pulseAddressMode = ref(PULSE_DEFAULTS.address_mode)
const pulseAddress = ref(PULSE_DEFAULTS.address)
const pulseOnValue = ref(PULSE_DEFAULTS.on_value)
const pulseOffValue = ref(PULSE_DEFAULTS.off_value)
const pulseMs = ref(PULSE_DEFAULTS.pulse_ms)
const pulseTimeout = ref(PULSE_DEFAULTS.timeout)
const pulseTriggerEnabled = ref(PULSE_DEFAULTS.trigger_enabled)
const pulseTriggerMode = ref(PULSE_DEFAULTS.trigger_mode)
const pulseCooldownMs = ref(PULSE_DEFAULTS.cooldown_ms)
const pulsingDeviceId = ref(null)

// 串口通用参数（所有 serial_* 协议共用）
const serialBytesize = ref(8)
const serialParity = ref('N')
const serialStopbits = ref(1)

const isPulseProtocol = computed(() => form.value.protocol === 'modbus_pulse')
const isPulseTcp = computed(() => isPulseProtocol.value && pulseTransport.value === 'tcp')

const isSerialProtocol = computed(() =>
  ['serial', 'serial_modbus_ascii', 'serial_continuous', 'serial_command'].includes(form.value.protocol)
  || (isPulseProtocol.value && pulseTransport.value === 'rtu')
)

// 台达 DVP/ES3: M0 → Modbus 0 基地址 2048，即 M100 → 2148（现场以台达手册为准）
const DELTA_M_COIL_BASE = 2048
const resolvedPulseAddress = computed(() => {
  const a = Number(pulseAddress.value) || 0
  if (pulseAddressMode.value === 'delta_m') return DELTA_M_COIL_BASE + a
  if (pulseAddressMode.value === 'doc1') {
    if (a >= 40001 && a <= 49999) return a - 40001
    if (a >= 30001 && a <= 39999) return a - 30001
    return a >= 1 ? a - 1 : 0
  }
  return a
})
const pulsePreview = computed(() => {
  const kind = pulseTarget.value === 'register' ? '寄存器' : '线圈'
  const src = pulseAddressMode.value === 'delta_m' ? `M${pulseAddress.value}` : `#${pulseAddress.value}`
  return `${kind} ${src} → Modbus 地址 ${resolvedPulseAddress.value}，${pulseOnValue.value} 保持 ${pulseMs.value}ms 后回 ${pulseOffValue.value}`
})
const pulseSummary = (dev) => {
  const c = dev.protocol_config || {}
  const mode = c.trigger_mode === 'all_covered' ? '全部覆盖时' : '判合格时'
  const on = c.trigger_enabled === false ? '仅手动' : mode
  const addr = c.address_mode === 'delta_m' ? `M${c.address ?? 0}` : `#${c.address ?? 0}`
  return `${addr} · ${c.pulse_ms ?? 300}ms · ${on}`
}

const onProtocolChange = (val) => {
  if (val === 'serial_modbus_ascii') {
    form.value.parse_mode = 'direct'
    form.value.device_role = 'weight'
  } else if (val === 'serial_continuous') {
    form.value.parse_mode = 'direct'
    form.value.device_role = 'weight'
  } else if (val === 'serial_command') {
    form.value.parse_mode = 'direct'
    form.value.device_role = 'weight'
  } else if (val === 'mock_weight') {
    form.value.parse_mode = 'direct'
    form.value.device_role = 'weight'
  } else if (val === 'modbus_pulse') {
    // 只写不读：解析/数据流向对它没意义，角色固定 PLC，端口给 Modbus TCP 默认 502
    form.value.parse_mode = 'direct'
    form.value.device_role = 'plc'
    if (!form.value.port) form.value.port = 502
  }
}

// 后端返回 naive UTC ISO（server_default=func.now()）。
// 按 UTC 解析 → 浏览器本地时区显示（国内 = 北京时间）。
const formatTime = (t) => {
  if (!t) return '-'
  const hasTz = /Z$|[+-]\d{2}:?\d{2}$/.test(t)
  const s = hasTz ? t : (String(t).replace(' ', 'T') + 'Z')
  const d = new Date(s)
  if (isNaN(d.getTime())) return t
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} `
    + `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

const roleLabel = (r) => ({ weight: '称重器', sensor: '传感器', plc: 'PLC', custom: '自定义' }[r] || r)
const roleTagType = (r) => ({ weight: 'warning', sensor: 'info', plc: 'success', custom: '' }[r] || '')
const statusLabel = (s) => ({ connected: '已连接', connecting: '连接中', disconnected: '断开', error: '错误' }[s] || '未知')
const statusTagType = (s) => ({ connected: 'success', connecting: 'warning', disconnected: 'danger', error: 'danger' }[s] || 'info')
const targetLabel = (t) => ({ cluster: '集群汇总', extra_fields: 'MES扩展', both: '两者' }[t] || t)

const getStatus = (id) => statusMap.value[id]?.status || 'disconnected'
const getLastData = (id) => statusMap.value[id]?.last_data || ''

const buildFormData = () => {
  const data = { ...form.value }
  // 去掉字符串字段的前后空格，避免肉眼看不见的空格导致连接失败（如 " /dev/ttyUSB0"）
  ;['name', 'serial_port', 'ip', 'station_id'].forEach(k => {
    if (typeof data[k] === 'string') data[k] = data[k].trim()
  })
  if (data.protocol === 'http_poll') {
    data.protocol_config = { ...data.protocol_config, url: httpUrl.value }
  }
  if (data.protocol === 'serial_modbus_ascii') {
    data.protocol_config = {
      ...data.protocol_config,
      slave_id: modbusSlaveId.value,
      register: modbusRegister.value,
      count: modbusCount.value,
      byte_order: modbusByteOrder.value,
      data_scale: modbusScale.value,
      poll_interval: modbusPollInterval.value,
    }
  }
  if (data.protocol === 'serial_continuous') {
    data.protocol_config = {
      ...data.protocol_config,
      data_format: continuousDataFormat.value,
      delimiter: continuousDelimiter.value,
      slave_id: modbusSlaveId.value,
      byte_order: modbusByteOrder.value,
      data_scale: modbusScale.value,
    }
  }
  if (data.protocol === 'serial_command') {
    data.protocol_config = {
      ...data.protocol_config,
      query_command: (cmdQuery.value || 'R').trim(),
      tare_command: (cmdTare.value || 'T').trim(),
      zero_command: (cmdZero.value || 'Z').trim(),
      command_suffix: cmdSuffix.value,
      poll_interval: cmdPollInterval.value,
    }
  }
  if (data.protocol === 'mock_weight') {
    let script = []
    try { script = JSON.parse(mockScriptText.value) } catch (e) { script = [] }
    data.protocol_config = {
      ...data.protocol_config,
      mock_script: script,
      mock_format: mockFormat.value,
      poll_interval: mockInterval.value,
      decimals: mockDecimals.value,
      loop: mockLoop.value,
    }
  }
  if (data.protocol === 'modbus_pulse') {
    data.protocol_config = {
      ...data.protocol_config,
      transport: pulseTransport.value,
      slave_id: pulseSlaveId.value,
      target: pulseTarget.value,
      address_mode: pulseAddressMode.value,
      address: pulseAddress.value,
      on_value: pulseOnValue.value,
      off_value: pulseOffValue.value,
      pulse_ms: pulseMs.value,
      timeout: pulseTimeout.value,
      trigger_enabled: pulseTriggerEnabled.value,
      trigger_mode: pulseTriggerMode.value,
      cooldown_ms: pulseCooldownMs.value,
    }
  }
  // 所有串口协议统一注入 bytesize/parity/stopbits
  if (isSerialProtocol.value) {
    data.protocol_config = {
      ...data.protocol_config,
      bytesize: serialBytesize.value,
      parity: serialParity.value,
      stopbits: serialStopbits.value,
    }
  }
  if (data.parse_mode === 'split') {
    data.parse_config = {
      delimiter: splitDelimiter.value,
      fields: { barcode: splitBarcodeIdx.value, weight: splitValueIdx.value }
    }
  }
  if (data.device_role === 'weight' && (weightMin.value > 0 || weightMax.value > 0)) {
    data.validation_rules = { weight: {} }
    if (weightMin.value > 0) data.validation_rules.weight.min = weightMin.value
    if (weightMax.value > 0) data.validation_rules.weight.max = weightMax.value
  }
  return data
}

const openAdd = () => {
  dbg('mes.external', '点击「新建外设」')
  editingId.value = null
  form.value = defaultForm()
  splitDelimiter.value = ','
  splitBarcodeIdx.value = 0
  splitValueIdx.value = 1
  weightMin.value = 0
  weightMax.value = 100
  httpUrl.value = ''
  serialBytesize.value = 8
  serialParity.value = 'N'
  serialStopbits.value = 1
  cmdQuery.value = 'R'
  cmdTare.value = 'T'
  cmdZero.value = 'Z'
  cmdSuffix.value = '\\r\\n'
  cmdPollInterval.value = 1.0
  mockScriptText.value = DEFAULT_MOCK_SCRIPT
  mockFormat.value = '{value}'
  mockInterval.value = 0.5
  mockDecimals.value = 3
  mockLoop.value = true
  pulseTransport.value = PULSE_DEFAULTS.transport
  pulseSlaveId.value = PULSE_DEFAULTS.slave_id
  pulseTarget.value = PULSE_DEFAULTS.target
  pulseAddressMode.value = PULSE_DEFAULTS.address_mode
  pulseAddress.value = PULSE_DEFAULTS.address
  pulseOnValue.value = PULSE_DEFAULTS.on_value
  pulseOffValue.value = PULSE_DEFAULTS.off_value
  pulseMs.value = PULSE_DEFAULTS.pulse_ms
  pulseTimeout.value = PULSE_DEFAULTS.timeout
  pulseTriggerEnabled.value = PULSE_DEFAULTS.trigger_enabled
  pulseTriggerMode.value = PULSE_DEFAULTS.trigger_mode
  pulseCooldownMs.value = PULSE_DEFAULTS.cooldown_ms
  showDialog.value = true
}

const editDev = (dev) => {
  dbg('mes.external', '点击「编辑外设」', `id=${dev?.id} name=${dev?.name || ''} protocol=${dev?.protocol || ''}`)
  editingId.value = dev.id
  form.value = { ...defaultForm(), ...dev }
  if (!form.value.pairing_mode) form.value.pairing_mode = 'stable'
  const pc = dev.parse_config || {}
  const pcfg = dev.protocol_config || {}
  if (dev.parse_mode === 'split') {
    splitDelimiter.value = pc.delimiter || ','
    splitBarcodeIdx.value = pc.fields?.barcode ?? 0
    splitValueIdx.value = pc.fields?.weight ?? 1
  }
  if (dev.protocol === 'serial_modbus_ascii') {
    modbusSlaveId.value = pcfg.slave_id ?? 1
    modbusRegister.value = pcfg.register ?? 41201
    modbusCount.value = pcfg.count ?? 2
    modbusByteOrder.value = pcfg.byte_order ?? 'H4H3L2L1'
    modbusScale.value = pcfg.data_scale ?? 1.0
    modbusPollInterval.value = pcfg.poll_interval ?? 0.5
  }
  if (dev.protocol === 'serial_continuous') {
    continuousDataFormat.value = pcfg.data_format ?? 'ascii'
    continuousDelimiter.value = pcfg.delimiter ?? '\\r\\n'
    modbusSlaveId.value = pcfg.slave_id ?? 1
    modbusByteOrder.value = pcfg.byte_order ?? 'H4H3L2L1'
    modbusScale.value = pcfg.data_scale ?? 1.0
  }
  if (dev.protocol === 'serial_command') {
    cmdQuery.value = pcfg.query_command ?? 'R'
    cmdTare.value = pcfg.tare_command ?? 'T'
    cmdZero.value = pcfg.zero_command ?? 'Z'
    cmdSuffix.value = pcfg.command_suffix ?? '\\r\\n'
    cmdPollInterval.value = pcfg.poll_interval ?? 1.0
  }
  if (dev.protocol === 'mock_weight') {
    mockScriptText.value = pcfg.mock_script ? JSON.stringify(pcfg.mock_script, null, 2) : DEFAULT_MOCK_SCRIPT
    mockFormat.value = pcfg.mock_format ?? '{value}'
    mockInterval.value = pcfg.poll_interval ?? 0.5
    mockDecimals.value = pcfg.decimals ?? 3
    mockLoop.value = pcfg.loop ?? true
  }
  if (dev.protocol === 'modbus_pulse') {
    pulseTransport.value = pcfg.transport ?? PULSE_DEFAULTS.transport
    pulseSlaveId.value = pcfg.slave_id ?? PULSE_DEFAULTS.slave_id
    pulseTarget.value = pcfg.target ?? PULSE_DEFAULTS.target
    pulseAddressMode.value = pcfg.address_mode ?? PULSE_DEFAULTS.address_mode
    pulseAddress.value = pcfg.address ?? PULSE_DEFAULTS.address
    pulseOnValue.value = pcfg.on_value ?? PULSE_DEFAULTS.on_value
    pulseOffValue.value = pcfg.off_value ?? PULSE_DEFAULTS.off_value
    pulseMs.value = pcfg.pulse_ms ?? PULSE_DEFAULTS.pulse_ms
    pulseTimeout.value = pcfg.timeout ?? PULSE_DEFAULTS.timeout
    pulseTriggerEnabled.value = pcfg.trigger_enabled ?? PULSE_DEFAULTS.trigger_enabled
    pulseTriggerMode.value = pcfg.trigger_mode ?? PULSE_DEFAULTS.trigger_mode
    pulseCooldownMs.value = pcfg.cooldown_ms ?? PULSE_DEFAULTS.cooldown_ms
  }
  // 串口通用参数回填（所有 serial_* 协议）
  if (['serial', 'serial_modbus_ascii', 'serial_continuous', 'serial_command'].includes(dev.protocol)) {
    serialBytesize.value = pcfg.bytesize ?? 8
    serialParity.value = pcfg.parity ?? 'N'
    serialStopbits.value = pcfg.stopbits ?? 1
  }
  const vr = dev.validation_rules?.weight || {}
  weightMin.value = vr.min || 0
  weightMax.value = vr.max || 100
  httpUrl.value = pcfg.url || ''
  showDialog.value = true
}

const applyPreset = (preset) => {
  openAdd()
  if (preset === 'weighing_modbus') {
    form.value.name = '称重器'
    form.value.device_role = 'weight'
    form.value.protocol = 'serial_modbus_ascii'
    form.value.serial_baud = 9600
    form.value.parse_mode = 'direct'
    modbusSlaveId.value = 1
    modbusRegister.value = 41203
    modbusCount.value = 2
    modbusByteOrder.value = 'H4H3L2L1'
    modbusScale.value = 0.01
    modbusPollInterval.value = 0.5
  } else if (preset === 'weighing_continuous') {
    form.value.name = '称重器 (连续)'
    form.value.device_role = 'weight'
    form.value.protocol = 'serial_continuous'
    form.value.serial_baud = 9600
    form.value.parse_mode = 'direct'
    continuousDataFormat.value = 'ascii'
    continuousDelimiter.value = '\\r\\n'
  } else if (preset === 'weighing_anheng') {
    form.value.name = '称重器 (安衡)'
    form.value.device_role = 'weight'
    form.value.protocol = 'serial_command'
    form.value.serial_baud = 9600
    form.value.parse_mode = 'direct'
    serialBytesize.value = 8
    serialParity.value = 'N'
    serialStopbits.value = 1
    cmdQuery.value = 'R'
    cmdTare.value = 'T'
    cmdZero.value = 'Z'
    cmdSuffix.value = '\\r\\n'
    cmdPollInterval.value = 1.0
  } else if (preset === 'tcp_sensor') {
    form.value.name = '传感器'
    form.value.device_role = 'sensor'
    form.value.protocol = 'tcp'
    form.value.parse_mode = 'direct'
  } else if (preset === 'plc_pulse_delta') {
    // 台达 DVP32ES3 默认联调参数: Modbus TCP / 502 / 从站 1 / 写线圈 M100 / 300ms
    form.value.name = 'PLC 完成脉冲'
    form.value.device_role = 'plc'
    form.value.protocol = 'modbus_pulse'
    form.value.parse_mode = 'direct'
    form.value.ip = ''
    form.value.port = 502
    pulseTransport.value = 'tcp'
    pulseSlaveId.value = 1
    pulseTarget.value = 'coil'
    pulseAddressMode.value = 'delta_m'
    pulseAddress.value = 100
    pulseOnValue.value = 1
    pulseOffValue.value = 0
    pulseMs.value = 300
    pulseTimeout.value = 3
    pulseTriggerEnabled.value = true
    pulseTriggerMode.value = 'cycle_ok'
    pulseCooldownMs.value = 1000
  }
}

const handleSave = async () => {
  dbg('mes.external', editingId.value ? '点击「保存编辑外设」' : '点击「保存新建外设」', `id=${editingId.value ?? ''} name=${form.value?.name || ''} protocol=${form.value?.protocol || ''}`)
  if (!form.value.name) { ElMessage.warning('请填写名称'); return }
  saving.value = true
  try {
    const data = buildFormData()
    let resp
    if (editingId.value) {
      resp = await updateExternalDevice(editingId.value, data)
      ElMessage.success('设备已更新')
    } else {
      resp = await createExternalDevice(data)
      ElMessage.success('设备已添加')
    }
    const warning = resp?.data?.warning
    if (warning) {
      ElMessage({ type: 'warning', message: warning, duration: 6000 })
    }
    showDialog.value = false
    loadDevices()
    refreshStatus()
  } catch (e) {
    dbgErr('mes.external', '保存外设', e)
    console.error('[ExtDev] save failed:', e, e?.response)
    let detail = ''
    if (e?.response) {
      const d = e.response.data?.detail
      if (typeof d === 'string') detail = d
      else if (d != null) { try { detail = JSON.stringify(d) } catch { detail = String(d) } }
      else detail = `HTTP ${e.response.status} ${e.response.statusText || ''}`.trim()
    } else if (e?.message) {
      detail = e.message
    } else {
      try { detail = JSON.stringify(e) } catch { detail = String(e) }
    }
    ElMessage.error(`保存失败: ${detail || '未知错误'}`)
  }
  finally { saving.value = false }
}

const handleDelete = async (dev) => {
  dbg('mes.external', '点击「删除外设」', `id=${dev?.id} name=${dev?.name || ''}`)
  try {
    await ElMessageBox.confirm(`确定删除 ${dev.name}？`, '确认')
    await deleteExternalDevice(dev.id)
    ElMessage.success('已删除')
    loadDevices()
    refreshStatus()
  } catch (e) {
    if (e !== 'cancel' && e !== 'close') dbgErr('mes.external', '删除外设', e)
    if (e !== 'cancel' && e !== 'close') ElMessage.error('删除失败')
  }
}

const testDev = async (dev) => {
  dbg('mes.external', '点击「测试连接」', `id=${dev?.id} name=${dev?.name || ''} protocol=${dev?.protocol || ''}`)
  testingDeviceId.value = dev.id
  try {
    const res = await testExternalDevice({
      protocol: dev.protocol, ip: dev.ip, port: dev.port,
      serial_port: dev.serial_port, serial_baud: dev.serial_baud || 9600,
      protocol_config: dev.protocol_config,
    })
    if (res.data.success) ElMessage.success(res.data.message)
    else ElMessage.error(res.data.message)
  } catch (e) {
    dbgErr('mes.external', '测试连接', e)
    ElMessage.error('测试失败: ' + (e.response?.data?.detail || e.message || '网络错误'))
  } finally {
    testingDeviceId.value = null
  }
}

const sendCmd = async (dev, command) => {
  dbg('mes.external', '点击「下发控制指令」', `id=${dev?.id} name=${dev?.name || ''} cmd=${command}`)
  try {
    const { data } = await sendExternalDeviceCommand(dev.id, command)
    if (data.success) ElMessage.success(data.message || `指令 ${command} 已下发`)
    else ElMessage.error(data.message || '指令下发失败')
  } catch (e) {
    dbgErr('mes.external', '下发控制指令', e)
    ElMessage.error('指令下发失败: ' + (e.response?.data?.detail || e.message || '网络错误'))
  }
}

const sendPulse = async (dev) => {
  dbg('mes.external', '点击「试发脉冲」', `id=${dev?.id} name=${dev?.name || ''}`)
  pulsingDeviceId.value = dev.id
  try {
    const { data } = await pulseExternalDevice(dev.id)
    if (data.success) ElMessage.success(data.message || '脉冲已下发')
    else ElMessage.error(data.message || '脉冲下发失败')
    await Promise.all([refreshStatus(), loadLogs()])
  } catch (e) {
    dbgErr('mes.external', '试发脉冲', e)
    ElMessage.error('脉冲下发失败: ' + (e.response?.data?.detail || e.message || '网络错误'))
  } finally {
    pulsingDeviceId.value = null
  }
}

const submitSimulateData = async () => {
  dbg('mes.external', '点击「模拟数据注入」', `device_id=${simulateForm.value?.device_id ?? ''} raw=${simulateForm.value?.raw_data || ''}`)
  const raw = (simulateForm.value.raw_data || '').trim()
  if (!raw) {
    ElMessage.warning('请填写模拟原始数据')
    return
  }
  simulating.value = true
  try {
    const { data } = await simulateExternalDeviceData({
      device_id: simulateForm.value.device_id || null,
      raw_data: raw,
      barcode: (simulateForm.value.barcode || '').trim() || null,
      channel_id: simulateForm.value.channel_id,
      full_chain: simulateForm.value.full_chain,
      repeat_count: simulateForm.value.full_chain ? 5 : 1,
    })
    ElMessage.success(data.message || '模拟数据已注入')
    showSimulate.value = false
    await Promise.all([refreshStatus(), loadLogs()])
  } catch (e) {
    dbgErr('mes.external', '模拟数据注入', e)
    ElMessage.error('模拟数据失败: ' + (e.response?.data?.detail || e.message || '未知错误'))
  } finally {
    simulating.value = false
  }
}

const loadDevices = async () => {
  try { devices.value = (await getExternalDevices()).data || [] }
  catch (e) { dbgErr('mes.external', '加载外设列表', e); ElMessage.error('加载设备列表失败: ' + (e.response?.data?.detail || e.message || '网络错误')) }
}
const refreshStatus = async () => {
  try {
    const res = await getExternalDeviceStatus()
    const map = {}
    for (const s of (res.data || [])) map[s.device_id] = s
    statusMap.value = map
  } catch (e) { console.warn('[ExtDev] 刷新状态失败:', e.message) }
}
const loadLogs = async (showFeedback = false) => {
  if (showFeedback) logsLoading.value = true
  try {
    logs.value = (await getExternalDeviceLogs({ limit: pollingStore.logLimit('external_device', 30) })).data.items || []
    if (showFeedback) ElMessage.success('外部设备日志已刷新')
  } catch (e) {
    const msg = e.response?.data?.detail || e.message || '网络错误'
    if (showFeedback) ElMessage.error('刷新外部设备日志失败: ' + msg)
    else console.warn('[ExtDev] 加载日志失败:', msg)
  } finally {
    if (showFeedback) logsLoading.value = false
  }
}
const handleClearLogs = async () => {
  try {
    await ElMessageBox.confirm('确定清空所有日志？', '确认')
  } catch (e) {
    return
  }
  try {
    await clearExternalDeviceLogs()
    logs.value = []
    ElMessage.success('已清空')
  } catch (e) {
    console.error('[ExtDev] clearExternalDeviceLogs failed:', e, e?.response)
    let detail = ''
    if (e?.response) {
      const d = e.response.data?.detail
      if (typeof d === 'string') detail = d
      else if (d != null) { try { detail = JSON.stringify(d) } catch { detail = String(d) } }
      else detail = `HTTP ${e.response.status} ${e.response.statusText || ''}`.trim()
    } else if (e?.message) {
      detail = e.message
    } else {
      try { detail = JSON.stringify(e) } catch { detail = String(e) }
    }
    ElMessage.error(`清空失败: ${detail || '未知错误'}`)
  }
}

let timer = null
const loadChannelCount = async () => {
  try {
    const res = await getWorkstations()
    channelCount.value = res.data.channel_count || 1
  } catch {
    channelCount.value = 1
  }
}
onMounted(async () => {
  await pollingStore.load()
  loadChannelCount()
  loadDevices(); refreshStatus(); loadLogs()
  timer = setInterval(() => { refreshStatus(); loadLogs() }, pollingStore.get('external_device', 5000))
})
onUnmounted(() => { if (timer) clearInterval(timer) })
</script>

<style scoped>
.mes-dialog :deep(.el-dialog) { background: #1e293b; border: 1px solid #334155; }
.mes-dialog :deep(.el-dialog__title) { color: #e2e8f0; }
.mes-dialog :deep(.el-form-item__label) { color: #94a3b8; }
</style>
