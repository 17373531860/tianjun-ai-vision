<template>
  <div>
    <div class="text-xs text-gray-500 mb-3">扫码器为设备级配置，按工位/广播通道绑定，多项目共享，不随当前项目切换。</div>
    <!-- Tab 切换: 基本管理 / WMax 高级（仅检测到 WMax 设备时显示） -->
    <el-tabs v-model="activeTab" class="scanner-tabs mb-4">
      <el-tab-pane label="设备管理" name="basic" />
      <el-tab-pane v-if="hasWmaxDevice" label="WMax 高级控制" name="wmax" />
    </el-tabs>

    <!-- WMax 高级面板 -->
    <WMaxPanel v-if="activeTab === 'wmax' && hasWmaxDevice" :wmax-ip="firstWmaxIp" :wmax-port="firstWmaxPort" />

    <!-- USB 键盘扫码枪精简编辑框 (新建/编辑 device_type=usb_hid 的扫码器) -->
    <UsbScanGunDialog v-model="showUsbDialog" :edit-dev="usbEditDev" @saved="loadDevices" />

    <!-- 基本设备管理面板 -->
    <div v-show="activeTab === 'basic'" class="flex gap-4">
      <!-- 左: 设备管理 -->
      <div class="flex-1">
        <div class="flex items-center justify-between mb-4">
          <h3 class="text-cyan-300 font-semibold">扫码器设备</h3>
          <div class="flex gap-2">
            <el-button size="small" @click="refreshStatus(true)" :loading="statusRefreshing">刷新状态</el-button>
            <el-button size="small" type="primary" @click="handleAutoDiscover" :loading="discovering">
              搜索设备
            </el-button>
            <el-button size="small" type="success" @click="handleManualAdd">手动添加</el-button>
            <el-button size="small" type="success" plain @click="openUsbDialog(null)">添加 USB 扫码枪</el-button>
            <el-button v-if="!hasWmaxDevice" size="small" type="warning" @click="createVirtualWmax">
              创建虚拟 WMax
            </el-button>
            <el-button v-if="hasVirtualWmax" size="small" type="danger" @click="deleteVirtualWmax">
              删除虚拟设备
            </el-button>
            <el-button v-if="systemStore.developerMode" size="small" type="warning" plain @click="showSimulate = true">
              模拟扫码
            </el-button>
          </div>
        </div>

        <!-- 自动发现的设备 -->
        <div v-if="discoveredDevices.length > 0" class="mb-4">
          <div class="text-sm text-gray-400 mb-2">
            <span class="text-green-400 mr-1">●</span>
            发现的扫码器
          </div>
          <div class="grid grid-cols-2 gap-3">
            <div v-for="d in discoveredDevices" :key="d.ip"
                 class="bg-slate-800/60 rounded-lg border border-green-700/50 p-4">
              <div class="flex items-center justify-between mb-2">
                <span class="font-medium text-green-300">{{ d.name || '扫码器' }}</span>
                <el-tag type="success" size="small" effect="dark">在线</el-tag>
              </div>
              <div class="text-xs text-gray-400 space-y-1">
                <div>IP: <span class="text-cyan-300 font-mono">{{ d.ip }}:{{ d.port }}</span></div>
              </div>
              <div class="flex gap-1 mt-3">
                <el-button size="small" type="primary" @click="quickAddDiscovered(d)">
                  添加到设备列表
                </el-button>
                <el-button v-if="false"
                           size="small" type="primary" @click="retryConnect(d)">
                  重试连接
                </el-button>
                <el-button size="small" @click="addDiscoveredToDb(d)">保存到设备列表</el-button>
              </div>
            </div>
          </div>
        </div>

        <!-- 数据库中配置的设备 -->
        <div v-if="devices.length > 0" class="text-sm text-gray-400 mb-2">已配置的设备</div>
        <div class="grid grid-cols-2 gap-3">
          <div v-for="dev in devices" :key="dev.id"
               class="bg-slate-800/60 rounded-lg border p-4 relative"
               :class="getDevStatus(dev.id) === 'connected' ? 'border-green-700' : 'border-slate-700'">
            <div class="flex items-center justify-between mb-2">
              <span class="font-medium">{{ dev.name }}</span>
              <div class="flex items-center gap-1">
                <el-tag
                  :type="dev.device_type === 'usb_hid' ? 'warning' : dev.device_type === 'text_lon' ? 'success' : 'primary'"
                  size="small" effect="dark">
                  {{ dev.device_type === 'usb_hid' ? 'USB扫码枪' : dev.device_type === 'text_lon' ? 'LON模式' : 'WMax三端口' }}
                </el-tag>
                <el-tag v-if="dev.external_only" type="warning" size="small" effect="dark">只喂外设</el-tag>
                <el-tag v-if="dev.pairing_group" type="info" size="small" effect="dark">分组: {{ dev.pairing_group }}</el-tag>
                <el-tag v-if="dev.device_type === 'usb_hid'"
                        :type="dev.enabled ? 'success' : 'info'" size="small">
                  {{ dev.enabled ? '已启用' : '已停用' }}
                </el-tag>
                <el-tag v-else :type="getDevStatus(dev.id) === 'connected' ? 'success' : getDevStatus(dev.id) === 'connecting' ? 'warning' : 'danger'" size="small">
                  {{ { connected: '已连接', connecting: '连接中', disconnected: '断开', error: '错误' }[getDevStatus(dev.id)] || '未知' }}
                </el-tag>
              </div>
            </div>
            <!-- USB 键盘扫码枪: 无 IP, 显示接入方式 + 用途 -->
            <div v-if="dev.device_type === 'usb_hid'" class="text-xs text-gray-400 space-y-1">
              <div>接入：本机 USB（即插即用）</div>
              <div>用途：{{ usbUsageLabel(dev) }}</div>
              <div v-if="(dev.parse_config && dev.parse_config.usb && dev.parse_config.usb.usage) !== 'pull'">
                {{ channelLabel(dev.channel_id) }}
              </div>
            </div>
            <!-- 网络扫码器 -->
            <div v-else class="text-xs text-gray-400 space-y-1">
              <div>IP: {{ dev.ip }}:{{ dev.port }}</div>
              <div>{{ channelLabel(dev.channel_id) }}
                <span v-if="dev.broadcast_channels && dev.broadcast_channels.length > 1" class="text-yellow-400 ml-1">
                  (广播: {{ dev.broadcast_channels.map(c => '工位 ' + (c + 1)).join(', ') }})
                </span>
              </div>
              <div>解析: {{ dev.parse_mode }}</div>
              <div v-if="getDevLastScan(dev.id)" class="text-cyan-300">
                最近: {{ getDevLastScan(dev.id) }}
              </div>
            </div>
            <div class="flex gap-1 mt-3">
            <el-button v-if="dev.device_type !== 'usb_hid'" size="small" @click="testDevice(dev)" :loading="testingDeviceId === dev.id">测试</el-button>
              <el-button v-if="dev.device_type === 'usb_hid'" size="small" @click="testUsbScan(dev)">测试扫码</el-button>
              <el-button size="small" type="primary" @click="editDevice(dev)">编辑</el-button>
              <el-button
                v-if="dev.device_type === 'auto' || dev.device_type === 'wmax' || getDevType(dev.id) === 'wmax'"
                size="small" type="warning"
                @click="openWmaxPanel(dev)">高级控制</el-button>
              <el-button size="small" type="danger" @click="handleDelete(dev)">删除</el-button>
            </div>
          </div>
        </div>
      </div>

      <!-- 右: 扫码记录 -->
      <div class="w-[400px] bg-slate-800/50 rounded-lg border border-slate-700 p-4 overflow-auto">
        <div class="flex items-center justify-between mb-3">
          <h3 class="text-cyan-300 font-semibold">扫码记录</h3>
          <div class="flex gap-1">
            <el-button size="small" @click="loadLogs(true)" :loading="logsLoading">刷新</el-button>
            <el-button size="small" type="danger" @click="handleClearLogs">清空</el-button>
          </div>
        </div>
        <div class="flex items-center gap-2 text-xs text-gray-400 mb-2 bg-slate-900/60 rounded px-2 py-1.5">
          <span>去重间隔:</span>
          <el-input-number
            v-model="quickDedup"
            :min="0" :precision="2" size="small"
            class="!w-20"
            controls-position="right"
            @change="applyQuickDedup"
          />
          <span>秒 — 相同条码在此时间内重复扫入将忽略</span>
        </div>
        <div v-if="logs.length === 0" class="text-gray-500 text-sm text-center py-8">暂无记录</div>
        <div v-else class="space-y-2">
          <div v-for="log in logs" :key="log.id"
               class="bg-slate-700/40 rounded p-2 text-xs"
               :class="log.success ? '' : 'border border-red-800/30'">
            <div class="flex justify-between">
              <span class="font-mono text-cyan-200">{{ log.parsed_serial || log.raw_data }}</span>
              <span class="text-gray-500">{{ formatTime(log.created_at) }}</span>
            </div>
            <div class="text-gray-400 mt-1">
              原始: {{ log.raw_data }}
              <span v-if="!log.success" class="text-red-400 ml-2">{{ log.error_msg }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 添加/编辑设备对话框 -->
    <el-dialog v-model="showAdd" :title="editingId ? '编辑设备' : '添加设备'" width="520px" class="mes-dialog" destroy-on-close>
      <el-form :model="form" label-width="80px" size="small">
        <!-- 上部：大输入框 -->
        <el-form-item label="名称" required>
          <el-input v-model="form.name" placeholder="如: 工位1扫码器" />
        </el-form-item>
        <el-form-item label="IP 地址" required>
          <el-input v-model="form.ip" placeholder="192.168.1.100" />
        </el-form-item>
        <el-form-item label="解析模式">
          <el-select v-model="form.parse_mode" class="w-full">
            <el-option label="直接使用" value="direct" />
            <el-option label="分隔符" value="separator" />
            <el-option label="正则" value="regex" />
          </el-select>
        </el-form-item>
        <el-form-item label="重复扫码">
          <el-select v-model="form.duplicate_scan_action" class="w-full">
            <el-option label="覆盖（默认）" value="overwrite" />
            <el-option label="拒绝（已有待检码时忽略新码）" value="reject" />
            <el-option label="排队（多件连扫依次检测）" value="queue" />
          </el-select>
        </el-form-item>
        <el-form-item label="误检重绑">
          <el-select v-model="form.rebind_mode" class="w-full">
            <el-option label="重新扫码（默认）" value="rescan" />
            <el-option label="自动重绑（NG 后自动放回待检）" value="auto_rebind" />
            <el-option label="手动选择（弹窗询问）" value="manual" />
          </el-select>
        </el-form-item>
        <el-form-item label="绑定时机">
          <el-select v-model="form.bind_timing" class="w-full" @change="onBindTimingChange">
            <el-option label="中途绑定（默认，扫码立即绑当前周期）" value="mid_cycle" />
            <el-option label="下周期绑定（扫码后等下个周期开始才绑）" value="cycle_start" />
            <el-option label="码-码闭环（扫码 A 开周期，扫码 B 结算 A 并开新周期）" value="scan_pair" />
          </el-select>
          <div v-if="form.bind_timing === 'scan_pair'" class="text-xs text-amber-300 mt-1">
            码-码闭环模式: 系统强制 <b>先扫后检 (开)</b>、<b>扫描模式 ≠ C 单次/周期</b>、
            <b>迟到补绑 = 0 秒</b>。下面相关字段已自动锁定。判 OK/NG 依据"窗口内是否曾齐过"
            而非物理消失。
          </div>
        </el-form-item>
        <el-form-item v-if="form.bind_timing === 'scan_pair'" label="超时秒数">
          <el-input-number v-model="form.scan_pair_max_wait_sec" :min="0" :max="3600"
                           :precision="0" class="w-full" controls-position="right">
            <template #append>秒</template>
          </el-input-number>
          <div class="text-xs text-gray-500 mt-1">
            扫码 A 后等待下一码的最大秒数。<b>0</b> = 不超时 (节拍稳定时推荐);
            填 <b>N &gt; 0</b> = N 秒内还没扫下一码就强制 NG 结算并清空窗口,
            防止漏扫导致周期永远卡死。
          </div>
        </el-form-item>
        <el-form-item label="通讯协议">
          <el-select v-model="form.device_type" class="w-full" @change="onDeviceTypeChange">
            <el-option label="55256 LON/LOFF（推荐 / 省电模式：仅检测时亮灯）" value="text_lon" />
            <el-option label="WMax 三端口（55266+55276+55286，持续视频流，灯一直闪）" value="auto" />
          </el-select>
          <div class="text-xs text-gray-500 mt-1">
            <b>LON/LOFF</b>：检测开始时发 LON 让设备扫码，停止时发 LOFF 关灯，
            扫码器只在检测周期内工作，不耗电也不刺眼。<br>
            <b>WMax 三端口</b>：连接后立刻打开 IMG 图像流，
            设备补光灯持续点亮直到软件关闭，仅在需要"扫码即抓图"等高级功能时使用。<br>
            <span class="text-amber-400">切换协议会自动调整默认端口，保存后下次启动生效。</span>
          </div>
        </el-form-item>
        <el-form-item v-if="form.device_type === 'text_lon'" label="扫描模式">
          <el-select v-model="form.scan_mode" class="w-full" @change="onScanModeChange">
            <el-option label="A 持续扫描（默认，扫码器灯一直闪等下一码）" value="continuous" />
            <el-option label="B 降速持续扫描（每次续 LON 间隔，灯闪慢一点）" value="throttled" />
            <el-option label="C 单次/周期扫描（扫到码 LOFF 灭灯，周期结束才再开扫）"
                       value="once_per_cycle"
                       :disabled="form.bind_timing === 'scan_pair'" />
            <el-option label="D 容器跨线/区域触发（容器模式专用，箱子跨线发 LON，扫到码 LOFF）"
                       value="D" />
          </el-select>
          <div class="text-xs text-gray-500 mt-1">
            <b>A 持续</b>：扫码器是单次触发型时，后端每收到一个 ERROR / 条码立刻续发 LON，
            灯持续闪，扫到一个码立刻能扫下一个，蜂鸣 / 闪烁较多。<br>
            <b>B 降速</b>：同 A，但每次续发 LON 间会等若干毫秒，降低闪烁频率，省电安静。<br>
            <b>C 单次/周期</b>：扫到一个码后立刻 LOFF 灭灯，等当前检测周期结束（OK / NG / 作废）
            后端自动恢复扫描——一个工件只扫一次，符合"先扫后检"的强校验工位流程。<br>
            <b>D 容器跨线触发</b>（仅容器模式项目）：箱子跨过画面里画的线 / 进入区域 → 后端发 LON
            让扫码器开扫，扫到码立即 LOFF。每个箱子各自一次 LON-扫码-LOFF 循环，
            <span class="text-amber-400">绑定工位必须是容器模式项目，否则切到此模式会被拒绝</span>。
          </div>
        </el-form-item>

        <!-- v3.4.0 D 模式专属几何配置 -->
        <template v-if="form.device_type === 'text_lon' && form.scan_mode === 'D'">
          <el-form-item label="触发几何">
            <el-radio-group v-model="form.scan_d_geometry">
              <el-radio value="line">线 (跨线触发)</el-radio>
              <el-radio value="zone">区域 (进入触发)</el-radio>
            </el-radio-group>
          </el-form-item>
          <el-form-item label="几何配置">
            <div class="flex items-center gap-2">
              <el-button type="primary" size="small" @click="openTriggerGeoEditor">
                {{ scanDGeometryConfigured ? '重新绘制' : '绘制 ' + (form.scan_d_geometry === 'line' ? '触发线' : '触发区域') }}
              </el-button>
              <el-button v-if="scanDGeometryConfigured" type="danger" size="small" plain
                         @click="clearScanDGeometry">清除</el-button>
              <span v-if="scanDGeometryConfigured" class="text-xs text-green-400">
                {{ form.scan_d_geometry === 'line'
                    ? '已配置触发线 (' + (form.scan_d_line?.side_a_to_b ? 'A → B' : 'B → A') + ')'
                    : '已配置触发区域 (' + (form.scan_d_zone?.length || 0) + ' 顶点)' }}
              </span>
              <span v-else class="text-xs text-amber-400">未配置 (D 模式不会触发)</span>
            </div>
            <div class="text-xs text-gray-500 mt-1">
              在视频画面上画一条线或一个多边形区域。线模式下选择哪一侧 → 哪一侧为
              "正方向"，箱子中心点跨过此线触发 LON；区域模式下箱子中心点进入区域触发 LON。
            </div>
          </el-form-item>
          <el-form-item label="离开判定帧数">
            <el-input-number v-model="form.scan_d_gone_confirm_frames" :min="0" :max="600"
                             :precision="0" class="w-full" controls-position="right">
              <template #append>帧</template>
            </el-input-number>
            <div class="text-xs text-gray-500 mt-1">
              箱子离开画面（或退出区域）多少帧后认为已"走完"，允许下一个箱子触发。
              <b>0</b> 沿用项目 pipeline_config 设置；推荐 30 帧 (1 秒@30fps)。
            </div>
          </el-form-item>
        </template>

        <el-form-item v-if="form.device_type === 'text_lon' && form.scan_mode === 'throttled'"
                      label="续发间隔">
          <el-input-number v-model="form.throttle_idle_ms" :min="0" :max="5000" :step="100"
                           :precision="0" class="w-full" controls-position="right">
            <template #append>毫秒</template>
          </el-input-number>
          <div class="text-xs text-gray-500 mt-1">
            B 模式专用：扫码器回 ERROR / 扫到码后等待这么多毫秒再续 LON。
            填 <b>0</b> 等同于 A 模式；推荐 300–800 ms。
          </div>
        </el-form-item>
        <el-form-item label="OK 后同码冷却">
          <el-input-number v-model="form.ok_rescan_cooldown_sec" :min="0" :max="600" :precision="0" class="w-full" controls-position="right">
            <template #append>秒</template>
          </el-input-number>
          <div class="text-xs text-gray-500 mt-1">
            同条码对应的工件上次检测<span class="text-green-400">合格</span>后、
            这么多秒内再次扫到将被静默忽略（避免搬运抖动产生脏数据）。
            设 <b>0</b> 关闭；NG 工件不受此限制，可立即重扫复检。建议填满一个节拍时间。
          </div>
        </el-form-item>
        <el-form-item label="迟到扫码补绑">
          <el-input-number v-model="form.late_scan_bind_window_sec" :min="0" :max="60"
                           :precision="0" class="w-full" controls-position="right"
                           :disabled="form.bind_timing === 'scan_pair'">
            <template #append>秒</template>
          </el-input-number>
          <div class="text-xs text-gray-500 mt-1">
            周期已经结算时若扫码事件距 cycle_end 时间不超过该秒数，自动把工件
            <b>补绑到刚结算的周期</b>，避免"工人慢半拍扫码 → 未绑码"误报。
            设 <b>0</b> 关闭；推荐 3 秒，节拍很快可调小，工人动作慢可调大。
          </div>
        </el-form-item>
        <el-form-item label="广播工位">
          <el-select v-model="form.broadcast_channels" multiple placeholder="留空则只发给绑定工位" class="w-full">
            <el-option v-for="opt in channelOptions" :key="opt.value" :label="opt.label" :value="opt.value" />
          </el-select>
          <div class="text-xs text-gray-500 mt-1">选多个工位时，扫码结果同时发送到所有选中工位</div>
        </el-form-item>

        <!-- v3.1.2 多工位广播结算联动: 仅在勾了 ≥ 2 个广播工位时才显示 -->
        <template v-if="(form.broadcast_channels || []).length >= 2">
          <el-form-item label="广播结算模式">
            <el-radio-group v-model="form.broadcast_settle_mode" @change="onSettleModeChange">
              <el-radio value="independent">各自独立结算 (默认)</el-radio>
              <el-radio value="primary">主工位驱动</el-radio>
            </el-radio-group>
            <div class="text-xs text-gray-500 mt-1">
              <span v-if="form.broadcast_settle_mode === 'independent'">
                每个广播工位用自己的"消失"信号独立结算 (老行为)
              </span>
              <span v-else>
                由<b>主工位</b>结算时, 强制带动其他广播工位同步结算; 适合大件 (容器) + 小件 (全部消失) 同箱的双工位场景
              </span>
            </div>
          </el-form-item>
          <el-form-item v-if="form.broadcast_settle_mode === 'primary'" label="主工位">
            <el-select v-model="form.primary_settle_channel" placeholder="选择作为节拍源的工位" class="w-full">
              <el-option
                v-for="ch in (form.broadcast_channels || [])"
                :key="ch"
                :label="(channelOptions.find(o => o.value === ch) || {}).label || ('工位 ' + (ch + 1))"
                :value="ch"
              />
            </el-select>
            <div class="text-xs text-gray-500 mt-1">通常选"快"的那一个 (如小件全部消失工位)</div>
          </el-form-item>
          <el-form-item v-if="form.broadcast_settle_mode === 'primary'" label="件数门槛">
            <el-input-number v-model="form.primary_settle_min_items" :min="0" :max="100" :step="1" class="!w-32" />
            <div class="text-xs text-gray-500 mt-1">
              其他工位被联动结算时, 当前周期内已检出件数 ≥ 该值才结算; <b>空箱/低件箱跳过</b>不动, 留给下一轮.
              默认 <b>1</b> (放了 1 件就跟随), 现场可调.
            </div>
          </el-form-item>
        </template>

        <!-- 中部：数字输入框并排 -->
        <div class="grid grid-cols-3 gap-3 my-3">
          <div class="text-center">
            <div class="text-xs text-gray-400 mb-1">端口</div>
            <el-input-number v-model="form.port" :min="0" :precision="0" size="small" class="!w-full" controls-position="right" />
          </div>
          <div class="text-center" :class="{ 'opacity-40 pointer-events-none': form.external_only }">
            <div class="text-xs text-gray-400 mb-1">
              绑定工位
              <span v-if="form.external_only" class="text-gray-500 ml-1">(已禁用)</span>
              <el-tooltip v-if="form.external_only" placement="top" :show-after="300">
                <template #content>
                  <div class="max-w-xs text-xs">
                    已勾选"只喂外部设备"，此扫码枪不再和工位关联，<br>
                    改为通过下方"设备分组号"与外设配对。
                  </div>
                </template>
                <el-icon class="text-gray-400 cursor-help"><QuestionFilled /></el-icon>
              </el-tooltip>
            </div>
            <el-select v-model="form.channel_id" size="small" class="!w-full" :disabled="form.external_only">
              <el-option v-for="opt in channelOptions" :key="opt.value" :label="opt.label" :value="opt.value" />
            </el-select>
          </div>
          <div class="text-center">
            <div class="text-xs text-gray-400 mb-1">去重间隔(秒)</div>
            <el-input-number v-model="form.dedup_interval_sec" :min="0" :precision="2" size="small" class="!w-full" controls-position="right" />
          </div>
        </div>

        <!-- 下部：开关并排 -->
        <el-divider class="!my-2" />
        <div class="grid grid-cols-3 gap-x-4 gap-y-2 px-1">
          <div class="flex items-center gap-1.5">
            <el-switch v-model="form.enabled" size="small" />
            <span class="text-xs text-gray-300">启用</span>
          </div>
          <div class="flex items-center gap-1.5">
            <el-switch v-model="form.scan_required" size="small"
                       :disabled="form.bind_timing === 'scan_pair'" />
            <span class="text-xs text-gray-300">先扫后检</span>
          </div>
          <div class="flex items-center gap-1.5">
            <el-switch v-model="form.warn_no_barcode" size="small" />
            <span class="text-xs text-gray-300">无码告警</span>
          </div>
          <div class="flex items-center gap-1.5">
            <el-switch v-model="form.auto_create_workpiece" size="small" />
            <span class="text-xs text-gray-300">自动建工件</span>
          </div>
          <div class="flex items-center gap-1.5">
            <el-switch v-model="form.auto_link_order" size="small" />
            <span class="text-xs text-gray-300">关联工单</span>
          </div>
          <div class="flex items-center gap-1.5">
            <el-switch v-model="systemStore.detection.toasts.scan.enabled" size="small" />
            <span class="text-xs text-gray-300">扫码提示框</span>
          </div>
          <div class="flex items-center gap-1.5 col-span-3">
            <el-switch v-model="form.external_only" size="small" />
            <span class="text-xs text-gray-300">只喂外部设备（不触发视觉检测）</span>
            <el-tooltip placement="top" :show-after="300">
              <template #content>
                <div class="max-w-xs text-xs">
                  用于"扫完放秤"这类扫码枪：<br>
                  扫码后只把条码喂给配对的外部设备（如称重器），<br>
                  不会触发任何视觉检测周期，也不会自动建工件。
                </div>
              </template>
              <el-icon class="text-gray-400 cursor-help"><QuestionFilled /></el-icon>
            </el-tooltip>
          </div>
        </div>

        <!-- 设备分组号：与"绑定工位"解耦的配对键 -->
        <el-divider class="!my-2" />
        <el-form-item>
          <template #label>
            <span :class="{ 'text-red-400': form.external_only }">
              <span v-if="form.external_only" class="mr-0.5">*</span>设备分组号
            </span>
          </template>
          <el-input
            v-model="form.pairing_group"
            size="small"
            :placeholder="form.external_only ? '必填：与对应外设填相同分组号（如 scale-c）' : '如 scale-c；留空则按绑定工位自动配对'"
            clearable
          >
            <template #append>
              <el-tooltip placement="top" :show-after="300">
                <template #content>
                  <div class="max-w-xs text-xs">
                    扫码枪与外部设备（如称重器）之间的<b>配对标识</b>，<br>
                    和"绑定工位"无关。<br>
                    两端填相同分组号即配成一对；<br>
                    勾选"只喂外部设备"时<b>必填</b>，<br>
                    否则留空则沿用老逻辑——按"绑定工位"匹配。
                  </div>
                </template>
                <el-icon class="text-gray-400 cursor-help"><QuestionFilled /></el-icon>
              </el-tooltip>
            </template>
          </el-input>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button size="small" @click="showAdd = false">取消</el-button>
        <el-button size="small" type="primary" @click="handleSave" :loading="saving">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showSimulate" title="模拟扫码（调试）" width="420px" class="mes-dialog" destroy-on-close>
      <el-form label-width="90px" size="small">
        <el-form-item label="条码" required>
          <el-input v-model="simulateForm.barcode" placeholder="AUTO_QA_SCAN_001" />
        </el-form-item>
        <el-form-item label="工位">
          <el-select v-model="simulateForm.channel_id" class="w-full">
            <el-option v-for="opt in channelOptions" :key="opt.value" :label="opt.label" :value="opt.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="只喂外设">
          <el-switch v-model="simulateForm.external_only" />
        </el-form-item>
        <el-form-item label="分组号">
          <el-input v-model="simulateForm.pairing_group" placeholder="如 scale-c，可留空" clearable />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button size="small" @click="showSimulate = false">取消</el-button>
        <el-button size="small" type="primary" @click="submitSimulateScan" :loading="simulating">注入扫码</el-button>
      </template>
    </el-dialog>

    <!-- v3.4.0 D 模式触发几何编辑器 -->
    <el-dialog v-model="triggerGeoEditorVisible"
               :title="triggerGeoMode === 'line' ? '绘制 D 模式触发线' : '绘制 D 模式触发区域'"
               width="80%" :close-on-click-modal="false" destroy-on-close>
      <div class="space-y-3">
        <div class="flex items-center gap-3 text-sm flex-wrap">
          <template v-if="triggerGeoMode === 'line'">
            <span class="text-gray-400">单击画面 2 次添加起点+终点; 画完两侧会染色 (蓝=A, 橙=B). 已画线再单击会清空重画.</span>
            <div class="flex-1"></div>
            <span class="text-xs">触发方向:</span>
            <el-radio-group v-model="triggerGeoSideAtoB" size="small">
              <el-radio-button :value="true">A → B (蓝→橙)</el-radio-button>
              <el-radio-button :value="false">B → A (橙→蓝)</el-radio-button>
            </el-radio-group>
          </template>
          <template v-else>
            <span class="text-gray-400">单击添加顶点, 点击<b class="text-amber-400">第一个点</b>闭合多边形 (双击也可闭合).</span>
            <div class="flex-1"></div>
          </template>
          <el-button size="small" @click="triggerGeoUndo"
                     :disabled="(triggerGeoMode === 'line' ? triggerGeoLinePts.length : triggerGeoZonePts.length) === 0">
            撤销
          </el-button>
          <el-button size="small" type="warning" @click="triggerGeoClear">全部清除</el-button>
        </div>
        <div class="relative bg-black rounded overflow-hidden flex justify-center" style="max-height: 70vh;">
          <canvas ref="triggerGeoCanvas" class="cursor-crosshair"
                  style="max-width: 100%; max-height: 70vh; object-fit: contain;"
                  @click="triggerGeoCanvasClick"
                  @dblclick="triggerGeoCanvasDblClick"
                  @mousemove="triggerGeoCanvasMouseMove"></canvas>
        </div>
        <div class="flex items-center gap-3 text-xs text-gray-500">
          <template v-if="triggerGeoMode === 'line'">
            <span>已点击: {{ triggerGeoLinePts.length }} / 2</span>
            <span v-if="triggerGeoLinePts.length === 2" class="text-green-400">线段已画好,
              箱子从 <b class="text-sky-400">A 侧</b> → <b class="text-orange-400">B 侧</b>
              {{ triggerGeoSideAtoB ? '(当前方向)' : '反向不触发' }}
            </span>
          </template>
          <template v-else>
            <span>顶点数: {{ triggerGeoZonePts.length }}</span>
            <span v-if="triggerGeoZoneClosed" class="text-green-400 font-bold">多边形已闭合</span>
          </template>
        </div>
      </div>
      <template #footer>
        <el-button @click="triggerGeoEditorVisible = false">取消</el-button>
        <el-button type="primary" @click="triggerGeoSave"
          :disabled="triggerGeoMode === 'line' ? triggerGeoLinePts.length !== 2 : !triggerGeoZoneClosed">
          保存几何
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { QuestionFilled } from '@element-plus/icons-vue'
import {
  getScannerDevices, createScannerDevice, updateScannerDevice,
  deleteScannerDevice, testScannerConnection, getScannerStatus, getScanLogs,
  clearScanLogs, discoverScanners, simulateScannerScan, checkContainerMode,
} from '@/api/scanner'
import { getBackendHost } from '@/api/index'
import { getWorkstations } from '@/api/detection'
import {
  wmaxCreateVirtual, wmaxDeleteVirtual, wmaxAutoDiscover,
  connectWMaxDevice
} from '@/api/wmax'
import WMaxPanel from './WMaxPanel.vue'
import UsbScanGunDialog from './UsbScanGunDialog.vue'
import { pullOrders } from '@/api/gateway'
import { routeCode } from '@/composables/useScanGun'
import { useSystemStore } from '@/store/useSystemStore'
import { dbg, dbgErr } from '@/utils/debug'

const systemStore = useSystemStore()

const activeTab = ref('basic')
const devices = ref([])
const statusMap = ref({})
const discoveredDevices = ref([])
const discovering = ref(false)
const logs = ref([])
const showAdd = ref(false)
const editingId = ref(null)
const saving = ref(false)
const channelCount = ref(1)
const statusRefreshing = ref(false)
const logsLoading = ref(false)
const testingDeviceId = ref(null)
const showSimulate = ref(false)
const simulating = ref(false)
const simulateForm = ref({
  barcode: 'AUTO_QA_SCAN_001',
  channel_id: 0,
  external_only: false,
  pairing_group: '',
})
// 后端 channel_id 仍以 0 开始，前端展示统一 +1。工位数按系统实际 channel_count 动态生成。
const channelOptions = computed(() =>
  Array.from({ length: channelCount.value }, (_, i) => ({
    value: i,
    label: `工位 ${i + 1}`,
  }))
)
const channelLabel = (ch) => (ch == null ? '未绑定' : `工位 ${ch + 1}`)

const defaultForm = () => ({
  name: '', ip: '', port: 55256, channel_id: 0, enabled: true,
  parse_mode: 'direct', dedup_interval_sec: 2,
  auto_create_workpiece: true, auto_link_order: true,
  scan_required: false, duplicate_scan_action: 'overwrite', warn_no_barcode: false,
  rebind_mode: 'rescan',
  bind_timing: 'mid_cycle',
  broadcast_channels: [],
  external_only: false,
  pairing_group: '',
  ok_rescan_cooldown_sec: 0,
  late_scan_bind_window_sec: 3,
  // v2.7.8: 协议选择
  //   text_lon = 55256 LON/LOFF 文本协议 (默认 / 省电模式 / 检测才亮灯)
  //   auto / wmax = WMax 三端口协议 (55266 CMD + 55276 IMG + 55286 RPT, 持续视频流, 一直闪灯)
  device_type: 'text_lon',
  // v2.7.16: 扫描模式 (text_lon 协议下控制扫码节奏)
  //   continuous     = 默认, 持续 LON 续发, 灯一直闪等下一码
  //   throttled      = 同 continuous 但每次续 LON 等 throttle_idle_ms 毫秒, 闪慢一点
  //   once_per_cycle = 扫到一个码后 LOFF 灭灯, 等当前周期结束自动恢复扫描
  scan_mode: 'continuous',
  throttle_idle_ms: 500,
  // v3.1.2: 多工位广播结算联动 (仅当广播工位 >= 2 时生效)
  broadcast_settle_mode: 'independent',
  primary_settle_channel: null,
  primary_settle_min_items: 1,
  // v3.3.0: 码-码闭环结算超时秒数 (bind_timing='scan_pair' 时生效, 0=不超时)
  scan_pair_max_wait_sec: 0,
  // v3.4.0 D 容器跨线/区域触发扫码 (scan_mode='D' 时生效, 仅容器模式项目可启用)
  //   scan_d_geometry: 'line' | 'zone'
  //   scan_d_line: { x1,y1,x2,y2 [0..1 归一化], side_a_to_b: bool }
  //   scan_d_zone: [[x,y], ...] [0..1 归一化, >=3 个点]
  //   scan_d_gone_confirm_frames: 0=沿用项目设置, >0=D 模式专属
  scan_d_geometry: 'line',
  scan_d_line: null,
  scan_d_zone: null,
  scan_d_gone_confirm_frames: 30,
})
const form = ref(defaultForm())

// v3.4.0 切到 D (容器跨线触发) 时校验绑定工位是否容器模式项目
const lastNonDScanMode = ref('continuous')
const onScanModeChange = async (val) => {
  if (val !== 'D') {
    lastNonDScanMode.value = val
    return
  }
  // 校验"绑定工位 (channel_id)" 是不是容器模式. D 模式状态机跑在扫码器绑定的
  // 视觉源头工位的检测帧循环里 (用那个工位的摄像头看 box 跨线/进区域); 广播工位
  // 只是扫到码后的事件分发对象, 与 D 模式触发判定无关, 所以 D 模式只关心 channel_id.
  const checkCh = form.value.channel_id ?? 0
  try {
    const { data } = await checkContainerMode(checkCh)
    if (!data?.is_container_mode) {
      ElMessageBox.alert(
        `绑定工位 ${checkCh + 1} 当前绑定的项目不是容器模式 (logic_mode=${data?.logic_mode || '?'},` +
        ` container_label="${data?.container_label || ''}"). D 模式只看绑定工位的摄像头判 box 跨线/进区域, ` +
        `所以必须把绑定工位先在"项目"页设成容器步骤标签 (广播工位与 D 模式无关).`,
        'D 模式不可用',
        { type: 'warning', confirmButtonText: '知道了' }
      )
      form.value.scan_mode = lastNonDScanMode.value || 'continuous'
      return
    }
    ElMessage.success(`已切到 D 容器跨线触发模式 (项目: ${data?.project_name || '未命名'})`)
  } catch (e) {
    ElMessageBox.alert(
      '无法校验工位项目模式; 请确认后端服务正常: ' + (e?.message || e),
      'D 模式校验失败',
      { type: 'error' }
    )
    form.value.scan_mode = lastNonDScanMode.value || 'continuous'
  }
}

// v3.4.0 D 模式几何配置: 是否已配置 + 清除
const scanDGeometryConfigured = computed(() => {
  if (form.value.scan_d_geometry === 'line') {
    const ln = form.value.scan_d_line
    return !!(ln && ln.x1 != null && ln.y1 != null && ln.x2 != null && ln.y2 != null)
  } else {
    return Array.isArray(form.value.scan_d_zone) && form.value.scan_d_zone.length >= 3
  }
})
const clearScanDGeometry = () => {
  if (form.value.scan_d_geometry === 'line') {
    form.value.scan_d_line = null
  } else {
    form.value.scan_d_zone = null
  }
}

// v3.4.0 触发几何编辑器 (line / zone)
const triggerGeoEditorVisible = ref(false)
const triggerGeoCanvas = ref(null)
const triggerGeoMode = ref('line')  // 'line' | 'zone'
const triggerGeoLinePts = ref([])   // [{x,y}, {x,y}] (像素 canvas 坐标)
const triggerGeoZonePts = ref([])
const triggerGeoZoneClosed = ref(false)
const triggerGeoSideAtoB = ref(true)
let triggerGeoImage = null
let triggerGeoMousePos = null

const openTriggerGeoEditor = async () => {
  triggerGeoMode.value = form.value.scan_d_geometry || 'line'
  triggerGeoLinePts.value = []
  triggerGeoZonePts.value = []
  triggerGeoZoneClosed.value = false
  triggerGeoSideAtoB.value = !!(form.value.scan_d_line?.side_a_to_b ?? true)
  triggerGeoMousePos = null
  triggerGeoEditorVisible.value = true
  await nextTick()
  setTimeout(() => loadTriggerGeoSnapshot(), 200)
}

const loadTriggerGeoSnapshot = () => {
  const canvas = triggerGeoCanvas.value
  if (!canvas) return
  // D 模式几何画在"绑定工位"的摄像头视角上 (state machine 在该工位帧循环里跑)
  const ch = form.value.channel_id ?? 0
  const img = new Image()
  img.crossOrigin = 'anonymous'
  const host = getBackendHost()
  img.src = `${host}/snapshot?channel=${ch}&t=${Date.now()}`
  img.onload = () => {
    triggerGeoImage = img
    canvas.width = img.naturalWidth
    canvas.height = img.naturalHeight
    // 把已有几何按归一化坐标还原到 canvas 像素
    if (triggerGeoMode.value === 'line' && form.value.scan_d_line) {
      const L = form.value.scan_d_line
      triggerGeoLinePts.value = [
        { x: L.x1 * canvas.width, y: L.y1 * canvas.height },
        { x: L.x2 * canvas.width, y: L.y2 * canvas.height },
      ]
    } else if (triggerGeoMode.value === 'zone' && Array.isArray(form.value.scan_d_zone)) {
      triggerGeoZonePts.value = form.value.scan_d_zone.map(([nx, ny]) => ({
        x: nx * canvas.width, y: ny * canvas.height,
      }))
      triggerGeoZoneClosed.value = triggerGeoZonePts.value.length >= 3
    }
    triggerGeoRedraw()
  }
  img.onerror = () => {
    const ctx = canvas.getContext('2d')
    canvas.width = 1280
    canvas.height = 720
    ctx.fillStyle = '#1e293b'
    ctx.fillRect(0, 0, 1280, 720)
    ctx.fillStyle = '#94a3b8'
    ctx.font = '20px Arial'
    ctx.textAlign = 'center'
    ctx.fillText('无法获取摄像头画面，请确保工位摄像头已连接', 640, 360)
    triggerGeoImage = null
  }
}

const triggerGeoGetCanvasXY = (e) => {
  const canvas = triggerGeoCanvas.value
  if (!canvas) return null
  const rect = canvas.getBoundingClientRect()
  const sx = canvas.width / rect.width
  const sy = canvas.height / rect.height
  return { x: (e.clientX - rect.left) * sx, y: (e.clientY - rect.top) * sy }
}

const triggerGeoCanvasClick = (e) => {
  const pt = triggerGeoGetCanvasXY(e)
  if (!pt) return
  if (triggerGeoMode.value === 'line') {
    if (triggerGeoLinePts.value.length >= 2) {
      // 已经画完了, 重新开始
      triggerGeoLinePts.value = [pt]
    } else {
      triggerGeoLinePts.value.push(pt)
    }
    triggerGeoRedraw()
  } else {
    // zone
    if (triggerGeoZoneClosed.value) {
      triggerGeoZonePts.value = [pt]
      triggerGeoZoneClosed.value = false
      triggerGeoRedraw()
      return
    }
    if (triggerGeoZonePts.value.length >= 3) {
      const first = triggerGeoZonePts.value[0]
      const dist = Math.hypot(pt.x - first.x, pt.y - first.y)
      const canvas = triggerGeoCanvas.value
      const scale = canvas.width / (canvas.getBoundingClientRect().width || 1)
      if (dist < 15 * scale) {
        triggerGeoZoneClosed.value = true
        triggerGeoRedraw()
        return
      }
    }
    triggerGeoZonePts.value.push(pt)
    triggerGeoRedraw()
  }
}

const triggerGeoCanvasDblClick = (e) => {
  e.preventDefault()
  if (triggerGeoMode.value === 'zone' && triggerGeoZonePts.value.length >= 3 && !triggerGeoZoneClosed.value) {
    triggerGeoZoneClosed.value = true
    triggerGeoRedraw()
  }
}

const triggerGeoCanvasMouseMove = (e) => {
  triggerGeoMousePos = triggerGeoGetCanvasXY(e)
  triggerGeoRedraw()
}

const triggerGeoUndo = () => {
  if (triggerGeoMode.value === 'line') {
    triggerGeoLinePts.value.pop()
  } else {
    if (triggerGeoZoneClosed.value) {
      triggerGeoZoneClosed.value = false
    } else {
      triggerGeoZonePts.value.pop()
    }
  }
  triggerGeoRedraw()
}

const triggerGeoClear = () => {
  triggerGeoLinePts.value = []
  triggerGeoZonePts.value = []
  triggerGeoZoneClosed.value = false
  triggerGeoMousePos = null
  triggerGeoRedraw()
}

// 把画面分两半: A 侧染浅蓝色透明, B 侧染浅橙色透明 (供用户判断方向)
// A 侧 = 叉积 > 0 (即线段左侧, 数学坐标系; 屏幕坐标 y 向下不影响相对方位)
const triggerGeoRedraw = () => {
  const canvas = triggerGeoCanvas.value
  if (!canvas) return
  const ctx = canvas.getContext('2d')
  ctx.clearRect(0, 0, canvas.width, canvas.height)
  if (triggerGeoImage) ctx.drawImage(triggerGeoImage, 0, 0)

  if (triggerGeoMode.value === 'line') {
    const pts = triggerGeoLinePts.value
    if (pts.length === 2) {
      // 染色两侧
      const [p1, p2] = pts
      const W = canvas.width, H = canvas.height
      const sideOf = (x, y) => (p2.x - p1.x) * (y - p1.y) - (p2.y - p1.y) * (x - p1.x)
      // 用 ImageData 像素染色稍贵, 简化用 4 个角填充判断 + 半透明矩形
      // 更简单: 沿线段法向偏移, 画两条贴线段长度方向的胖梯形
      const dx = p2.x - p1.x, dy = p2.y - p1.y
      const len = Math.hypot(dx, dy) || 1
      const nx = -dy / len, ny = dx / len  // 法向: 叉积 > 0 这一侧 (A)
      const reach = Math.hypot(W, H)  // 远到画面外
      // A 侧 (叉积 > 0): 平移 +nx,ny 方向
      ctx.fillStyle = 'rgba(56, 189, 248, 0.18)'  // 浅蓝
      ctx.beginPath()
      ctx.moveTo(p1.x, p1.y)
      ctx.lineTo(p2.x, p2.y)
      ctx.lineTo(p2.x + nx * reach, p2.y + ny * reach)
      ctx.lineTo(p1.x + nx * reach, p1.y + ny * reach)
      ctx.closePath()
      ctx.fill()
      // B 侧 (叉积 < 0): 平移 -nx,-ny
      ctx.fillStyle = 'rgba(251, 146, 60, 0.18)'  // 浅橙
      ctx.beginPath()
      ctx.moveTo(p1.x, p1.y)
      ctx.lineTo(p2.x, p2.y)
      ctx.lineTo(p2.x - nx * reach, p2.y - ny * reach)
      ctx.lineTo(p1.x - nx * reach, p1.y - ny * reach)
      ctx.closePath()
      ctx.fill()
      // 标注 A / B
      ctx.font = 'bold 28px Arial'
      ctx.textAlign = 'center'
      ctx.fillStyle = '#38bdf8'
      ctx.fillText('A', (p1.x + p2.x) / 2 + nx * 80, (p1.y + p2.y) / 2 + ny * 80)
      ctx.fillStyle = '#fb923c'
      ctx.fillText('B', (p1.x + p2.x) / 2 - nx * 80, (p1.y + p2.y) / 2 - ny * 80)
    }
    // 画线段本体
    ctx.strokeStyle = '#22c55e'
    ctx.lineWidth = 3
    ctx.setLineDash(pts.length < 2 ? [8, 4] : [])
    ctx.beginPath()
    if (pts.length >= 1) ctx.moveTo(pts[0].x, pts[0].y)
    for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i].x, pts[i].y)
    if (pts.length === 1 && triggerGeoMousePos) ctx.lineTo(triggerGeoMousePos.x, triggerGeoMousePos.y)
    ctx.stroke()
    ctx.setLineDash([])
    pts.forEach((pt, i) => {
      ctx.fillStyle = i === 0 ? '#22c55e' : '#16a34a'
      ctx.beginPath()
      ctx.arc(pt.x, pt.y, 6, 0, Math.PI * 2)
      ctx.fill()
      ctx.fillStyle = 'white'
      ctx.font = 'bold 12px Arial'
      ctx.fillText(i === 0 ? '起点' : '终点', pt.x + 10, pt.y - 6)
    })
  } else {
    // zone polygon
    const pts = triggerGeoZonePts.value
    if (pts.length === 0) return
    if (triggerGeoZoneClosed.value && pts.length >= 3) {
      ctx.fillStyle = 'rgba(34, 197, 94, 0.18)'
      ctx.beginPath()
      ctx.moveTo(pts[0].x, pts[0].y)
      for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i].x, pts[i].y)
      ctx.closePath()
      ctx.fill()
    }
    ctx.strokeStyle = '#22c55e'
    ctx.lineWidth = 2
    ctx.setLineDash(triggerGeoZoneClosed.value ? [] : [8, 4])
    ctx.beginPath()
    ctx.moveTo(pts[0].x, pts[0].y)
    for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i].x, pts[i].y)
    if (triggerGeoZoneClosed.value) ctx.closePath()
    else if (triggerGeoMousePos) ctx.lineTo(triggerGeoMousePos.x, triggerGeoMousePos.y)
    ctx.stroke()
    ctx.setLineDash([])
    pts.forEach((pt, i) => {
      const isFirst = i === 0
      ctx.fillStyle = isFirst ? '#f59e0b' : '#16a34a'
      ctx.beginPath()
      ctx.arc(pt.x, pt.y, isFirst ? 7 : 5, 0, Math.PI * 2)
      ctx.fill()
      ctx.fillStyle = 'white'
      ctx.font = 'bold 11px Arial'
      ctx.fillText(`${i + 1}`, pt.x + 8, pt.y - 4)
    })
  }
}

const triggerGeoSave = () => {
  const canvas = triggerGeoCanvas.value
  if (!canvas) return
  const W = canvas.width, H = canvas.height
  if (triggerGeoMode.value === 'line') {
    if (triggerGeoLinePts.value.length !== 2) {
      ElMessage.warning('线模式需要 2 个点; 单击画面添加起点和终点')
      return
    }
    const [p1, p2] = triggerGeoLinePts.value
    form.value.scan_d_line = {
      x1: Math.round((p1.x / W) * 10000) / 10000,
      y1: Math.round((p1.y / H) * 10000) / 10000,
      x2: Math.round((p2.x / W) * 10000) / 10000,
      y2: Math.round((p2.y / H) * 10000) / 10000,
      side_a_to_b: !!triggerGeoSideAtoB.value,
    }
    form.value.scan_d_geometry = 'line'
  } else {
    if (!triggerGeoZoneClosed.value || triggerGeoZonePts.value.length < 3) {
      ElMessage.warning('区域模式需要至少 3 个点并闭合多边形')
      return
    }
    form.value.scan_d_zone = triggerGeoZonePts.value.map(p => [
      Math.round((p.x / W) * 10000) / 10000,
      Math.round((p.y / H) * 10000) / 10000,
    ])
    form.value.scan_d_geometry = 'zone'
  }
  triggerGeoEditorVisible.value = false
  ElMessage.success('几何已保存; 点保存按钮提交到设备')
}

// v3.3.0 切到 "码-码闭环" 时强制锁定 scan_required=on / scan_mode≠C / late_bind=0,
// 避免与扫码闭环节奏冲突。切回其它模式时不自动恢复 (用户自己改)。
const onBindTimingChange = (val) => {
  if (val !== 'scan_pair') return
  if (!form.value.scan_required) form.value.scan_required = true
  if (form.value.scan_mode === 'once_per_cycle') form.value.scan_mode = 'continuous'
  if (form.value.late_scan_bind_window_sec > 0) form.value.late_scan_bind_window_sec = 0
  ElMessage.info('已切换为码-码闭环结算; 已自动启用先扫后检, 关闭迟到补绑')
}

const onSettleModeChange = async (val) => {
  if (val !== 'primary') return
  const broadcast = form.value.broadcast_channels || []
  // 默认主工位: 优先用绑定工位 (channel_id), 其次用广播工位中第一个
  if (!broadcast.includes(form.value.primary_settle_channel)) {
    form.value.primary_settle_channel = broadcast.includes(form.value.channel_id)
      ? form.value.channel_id
      : broadcast[0]
  }
  // 弹一次"件数门槛"输入框, 让用户即时配置
  try {
    const { value } = await ElMessageBox.prompt(
      '其他广播工位被主工位带动结算时, 已检出件数 ≥ 该门槛才结算 (空箱/低件箱跳过, 留给下一轮)',
      '设置件数门槛',
      {
        inputValue: String(form.value.primary_settle_min_items ?? 1),
        inputPattern: /^\d+$/,
        inputErrorMessage: '请输入非负整数',
        confirmButtonText: '确定',
        cancelButtonText: '保持默认 (1)',
      }
    )
    const n = parseInt(value, 10)
    if (Number.isFinite(n) && n >= 0) {
      form.value.primary_settle_min_items = n
    }
  } catch (e) {
    // 用户取消, 维持默认值, 不报错
  }
}

const quickDedup = ref(2)

const handleManualAdd = () => {
  dbg('mes.scanner', '点击「新建扫码器」')
  editingId.value = null
  form.value = defaultForm()
  showAdd.value = true
}

// v2.7.8: 协议切换时联动端口默认值
//   text_lon 走 55256 (LON/LOFF 文本端口)
//   auto / wmax 走 55266 (WMax 三端口的管理端口)
const onDeviceTypeChange = (newType) => {
  if (newType === 'text_lon' && form.value.port === 55266) {
    form.value.port = 55256
  } else if ((newType === 'auto' || newType === 'wmax') && form.value.port === 55256) {
    form.value.port = 55266
  }
}

const formatTime = (t) => t ? t.replace('T', ' ').substring(0, 19) : '-'

const getDevStatus = (id) => statusMap.value[id]?.status || 'disconnected'
const getDevType = (id) => statusMap.value[id]?.device_type || 'text'
const getDevLastScan = (id) => statusMap.value[id]?.last_scan || ''

const hasWmaxDevice = computed(() =>
  Object.values(statusMap.value).some(s => s.device_type === 'wmax')
)
const hasVirtualWmax = computed(() =>
  Object.values(statusMap.value).some(s => s.device_type === 'wmax' && s.device_id === -999)
)
const firstWmaxIp = computed(() => {
  const s = Object.values(statusMap.value).find(s => s.device_type === 'wmax')
  return s?.ip || ''
})
const firstWmaxPort = computed(() => {
  return 55266
})

const openWmaxPanel = (dev) => {
  activeTab.value = 'wmax'
}

const openWmaxForDiscovered = (d) => {
  activeTab.value = 'wmax'
}

const handleAutoDiscover = async () => {
  discovering.value = true
  try {
    const res = await discoverScanners()
    const data = res.data
    if (data.results && data.results.length > 0) {
      discoveredDevices.value = data.results
      ElMessage.success(`发现 ${data.total} 台扫码器`)
    } else {
      ElMessage.info('未发现扫码器（192.168.0.x:55256）')
    }
    await refreshStatus()
  } catch (e) {
    ElMessage.error('搜索失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    discovering.value = false
  }
}

const quickAddDiscovered = async (d) => {
  try {
    const existing = devices.value.find(dev => dev.ip === d.ip && dev.port === d.port)
    if (existing) {
      ElMessage.warning(`${d.ip}:${d.port} 已在设备列表中`)
      return
    }
    await createScannerDevice({
      name: d.name || `扫码器-${d.ip}`, ip: d.ip, port: d.port,
      channel_id: 0, enabled: true, device_type: 'text_lon'
    })
    ElMessage.success(`已添加 ${d.ip}`)
    await loadDevices()
    await refreshStatus()
  } catch (e) {
    ElMessage.error('添加失败: ' + (e.response?.data?.detail || e.message))
  }
}

const retryConnect = async (d) => {
  dbg('mes.scanner', '点击「连接 WMax 设备」', `ip=${d?.ip || ''}:${d?.port ?? ''}`)
  try {
    const res = await connectWMaxDevice(d.ip, d.port)
    if (res.data.success) {
      d.action = 'connected'
      ElMessage.success(`${d.ip} 连接成功`)
      await refreshStatus()
    } else {
      ElMessage.error(res.data.message)
    }
  } catch (e) {
    dbgErr('mes.scanner', '连接 WMax 设备', e)
    ElMessage.error('连接失败')
  }
}

const addDiscoveredToDb = async (d) => {
  // v2.7.3: 打开"添加表单"前先本地查重，避免用户两次保存同一个 IP 落库
  const existing = devices.value.find(dev =>
    dev.ip === d.ip && dev.port === d.port && !dev._auto
  )
  if (existing) {
    ElMessage.warning(`${d.ip}:${d.port} 已存在于设备列表（"${existing.name}"），无需重复添加`)
    return
  }
  form.value = {
    ...defaultForm(),
    name: d.name || `WMax-${d.ip}`,
    ip: d.ip,
    port: d.port,
  }
  editingId.value = null
  showAdd.value = true
}

const createVirtualWmax = async () => {
  try {
    const res = await wmaxCreateVirtual()
    if (res.data.success) {
      ElMessage.success('虚拟 WMax 设备已创建，点击 WMax 高级控制查看功能')
      await refreshStatus()
      activeTab.value = 'wmax'
    }
  } catch (e) { ElMessage.error('创建失败') }
}

const deleteVirtualWmax = async () => {
  try {
    await wmaxDeleteVirtual()
    ElMessage.success('虚拟设备已删除')
    activeTab.value = 'basic'
    await refreshStatus()
  } catch (e) {
    ElMessage.error('删除虚拟设备失败: ' + (e.response?.data?.detail || e.message || '未知错误'))
  }
}

const loadDevices = async () => {
  try {
    const res = await getScannerDevices()
    devices.value = res.data || []
    if (devices.value.length > 0) {
      quickDedup.value = devices.value[0].dedup_interval_sec ?? 2
    }
  } catch (e) {
    ElMessage.error('加载扫码器列表失败: ' + (e.response?.data?.detail || e.message || '网络错误'))
  }
}

const applyQuickDedup = async (val) => {
  let failCount = 0
  for (const dev of devices.value) {
    try {
      await updateScannerDevice(dev.id, { dedup_interval_sec: val })
    } catch { failCount++ }
  }
  if (failCount === 0) {
    ElMessage.success(`去重间隔已更新为 ${val} 秒`)
  } else if (failCount < devices.value.length) {
    ElMessage.warning(`${failCount} 台设备更新失败，其余已更新`)
  } else {
    ElMessage.error('所有设备更新失败')
  }
}

const refreshStatus = async (showFeedback = false) => {
  if (showFeedback) statusRefreshing.value = true
  try {
    const res = await getScannerStatus()
    const map = {}
    for (const s of (res.data || [])) {
      map[s.device_id] = s
    }
    statusMap.value = map
    mergeAutoDevices()
    if (showFeedback) ElMessage.success('扫码器状态已刷新')
  } catch (e) {
    const msg = e.response?.data?.detail || e.message || '网络错误'
    if (showFeedback) ElMessage.error('刷新状态失败: ' + msg)
    else console.warn('[Scanner] 刷新状态失败:', msg)
  } finally {
    if (showFeedback) statusRefreshing.value = false
  }
}

const mergeAutoDevices = () => {
  for (const s of Object.values(statusMap.value)) {
    const isAutoDevice = s.device_id < -900
    if (isAutoDevice && !devices.value.find(d => d.id === s.device_id)) {
      devices.value.push({
        id: s.device_id, name: s.name || `WMax-${s.ip}`,
        ip: s.ip, port: s.port, channel_id: 0,
        parse_mode: 'direct', enabled: true,
        _auto: true,
      })
    }
  }
  devices.value = devices.value.filter(d => {
    if (d._auto && !statusMap.value[d.id]) return false
    return true
  })
}

const loadLogs = async (showFeedback = false) => {
  if (showFeedback) logsLoading.value = true
  try {
    const res = await getScanLogs({ limit: 30 })
    logs.value = res.data.items || []
    if (showFeedback) ElMessage.success('扫码记录已刷新')
  } catch (e) {
    const msg = e.response?.data?.detail || e.message || '网络错误'
    if (showFeedback) ElMessage.error('刷新扫码记录失败: ' + msg)
    else console.warn('[Scanner] 加载日志失败:', msg)
  } finally {
    if (showFeedback) logsLoading.value = false
  }
}

const handleClearLogs = async () => {
  try {
    await ElMessageBox.confirm('确定清空所有扫码记录？此操作不可恢复。', '确认清空')
  } catch (e) {
    return
  }
  try {
    await clearScanLogs()
    logs.value = []
    ElMessage.success('扫码记录已清空')
  } catch (e) {
    console.error('[Scanner] clearScanLogs failed:', e, e?.response)
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

const testDevice = async (dev) => {
  dbg('mes.scanner', '点击「测试连接」', `id=${dev?.id} ip=${dev?.ip || ''}:${dev?.port ?? ''}`)
  testingDeviceId.value = dev.id
  try {
    // 把当前配置的 device_type 传给后端, 让 text_lon 模式只走 LON/LOFF, 不打开视频流
    const res = await testScannerConnection(dev.ip, dev.port, dev.device_type || 'auto')
    if (res.data.success) {
      const typeLabel = res.data.device_type === 'wmax' ? ' (WMax 设备)' : ' (文本设备)'
      ElMessage.success(res.data.message + typeLabel)
    } else {
      ElMessage.error(res.data.message)
    }
    refreshStatus()
  } catch (e) {
    dbgErr('mes.scanner', '测试连接', e)
    ElMessage.error('测试失败: ' + (e.response?.data?.detail || e.message || '网络错误'))
  } finally {
    testingDeviceId.value = null
  }
}

const submitSimulateScan = async () => {
  dbg('mes.scanner', '点击「模拟扫码」', `barcode=${simulateForm.value?.barcode || ''} channel=${simulateForm.value?.channel_id ?? ''}`)
  const barcode = (simulateForm.value.barcode || '').trim()
  if (!barcode) {
    ElMessage.warning('请填写模拟条码')
    return
  }
  simulating.value = true
  try {
    const { data } = await simulateScannerScan({
      barcode,
      channel_id: simulateForm.value.channel_id,
      external_only: simulateForm.value.external_only,
      pairing_group: (simulateForm.value.pairing_group || '').trim() || null,
    })
    ElMessage.success(`已注入模拟扫码: ${data.serial_no || barcode}`)
    showSimulate.value = false
    await Promise.all([refreshStatus(), loadLogs()])
  } catch (e) {
    dbgErr('mes.scanner', '模拟扫码', e)
    ElMessage.error('模拟扫码失败: ' + (e.response?.data?.detail || e.message || '未知错误'))
  } finally {
    simulating.value = false
  }
}

const handleSave = async () => {
  dbg('mes.scanner', editingId.value ? '点击「保存编辑扫码器」' : '点击「保存新建扫码器」', `id=${editingId.value ?? ''} name=${form.value?.name || ''} ip=${form.value?.ip || ''} enabled=${form.value?.enabled}`)
  if (!form.value.name || !form.value.ip) {
    ElMessage.warning('请填写名称和IP地址')
    return
  }
  if (form.value.external_only && !(form.value.pairing_group || '').trim()) {
    ElMessage.warning('勾选"只喂外部设备"后必须填写"设备分组号"，用于与外部设备配对')
    return
  }
  // v2.7.3: 同 IP+port 去重（前端先校验，提供更快的反馈；后端还有兜底）
  const conflict = devices.value.find(dev =>
    dev.ip === form.value.ip &&
    dev.port === form.value.port &&
    dev.id !== editingId.value &&
    !dev._auto
  )
  if (conflict) {
    ElMessage.warning(`地址 ${form.value.ip}:${form.value.port} 已被设备 "${conflict.name}" 占用，请改用其他地址`)
    return
  }
  saving.value = true
  try {
    if (editingId.value) {
      await updateScannerDevice(editingId.value, form.value)
      ElMessage.success('设备已更新')
    } else {
      await createScannerDevice(form.value)
      ElMessage.success('设备已添加')
    }
    showAdd.value = false
    loadDevices()
    refreshStatus()
  } catch (e) { dbgErr('mes.scanner', '保存扫码器', e); ElMessage.error(e.response?.data?.detail || '保存失败') }
  finally { saving.value = false }
}

const editDevice = (dev) => {
  dbg('mes.scanner', '点击「编辑扫码器」', `id=${dev?.id} name=${dev?.name || ''}`)
  // USB 键盘扫码枪走它自己的精简编辑框 (网络枪那几十个字段对它无意义)
  if (dev.device_type === 'usb_hid') { openUsbDialog(dev); return }
  editingId.value = dev.id
  form.value = { ...defaultForm(), ...dev }
  showAdd.value = true
}

// ---- USB 扫码枪精简编辑框 ----
const showUsbDialog = ref(false)
const usbEditDev = ref(null)
function openUsbDialog(dev) {
  usbEditDev.value = dev
  showUsbDialog.value = true
}
function usbUsageLabel(dev) {
  const u = (dev.parse_config && dev.parse_config.usb && dev.parse_config.usb.usage) || 'pull'
  return { pull: '扫码拉工单', bind: '扫码绑工件', both: '拉工单 + 绑工件' }[u] || u
}
// 现场调试: 不用真扫码枪, 输一个码按该 USB 枪的用途真跑一次 (拉工单 / 绑工件)
async function testUsbScan(dev) {
  const u = (dev.parse_config && dev.parse_config.usb) || {}
  let code
  try {
    const r = await ElMessageBox.prompt('输入一个条码模拟扫码（按用途自动路由）', `测试扫码 - ${dev.name}`, {
      confirmButtonText: '真跑一次', cancelButtonText: '取消',
      inputPlaceholder: '如 JOB260500444-136 或工件码',
    })
    code = (r.value || '').trim()
  } catch { return }
  if (!code) return
  const route = routeCode({ usage: u.usage || 'pull', orderPattern: u.order_pattern }, code)
  try {
    if (route === 'pull') {
      if (!u.pull_conn_id) { ElMessage.warning('该 USB 枪未配拉取连接，去编辑里选一条'); return }
      const resp = await pullOrders(u.pull_conn_id, { job_no: code, dry_run: false })
      const res = resp?.data ?? resp
      res?.success
        ? ElMessage.success(`扫码拉工单成功：新建 ${res.created || 0} / 更新 ${res.updated || 0}`)
        : ElMessage.error('扫码拉工单失败：' + (res?.error || '未知'))
    } else {
      await simulateScannerScan({ barcode: code, channel_id: dev.channel_id || 0 })
      ElMessage.success(`已注入绑定链路（工位${(dev.channel_id || 0) + 1}）：${code}`)
    }
  } catch (e) {
    ElMessage.error('测试异常：' + (e?.response?.data?.detail || e.message))
  }
}

const handleDelete = async (dev) => {
  dbg('mes.scanner', '点击「删除扫码器」', `id=${dev?.id} name=${dev?.name || ''}`)
  try {
    await ElMessageBox.confirm(`确定删除设备 ${dev.name}？`, '确认')
    await deleteScannerDevice(dev.id)
    ElMessage.success('设备已删除')
    loadDevices()
    refreshStatus()
  } catch (e) {
    if (e !== 'cancel' && e !== 'close') {
      dbgErr('mes.scanner', '删除扫码器', e)
      ElMessage.error(`删除失败: ${e?.response?.data?.detail || e?.message || e}`)
    }
  }
}

let statusTimer = null
const loadChannelCount = async () => {
  try {
    const res = await getWorkstations()
    channelCount.value = res.data.channel_count || 1
  } catch {
    channelCount.value = 1
  }
}
onMounted(() => {
  form.value = defaultForm()
  loadChannelCount()
  loadDevices()
  refreshStatus()
  loadLogs()
  statusTimer = setInterval(() => { refreshStatus(); loadLogs() }, 5000)
})
onUnmounted(() => { if (statusTimer) clearInterval(statusTimer) })
</script>

<style scoped>
.mes-dialog :deep(.el-dialog) { background: #1e293b; border: 1px solid #334155; }
.mes-dialog :deep(.el-dialog__title) { color: #e2e8f0; }
.mes-dialog :deep(.el-form-item__label) { color: #94a3b8; }
.scanner-tabs :deep(.el-tabs__item) { color: #94a3b8; }
.scanner-tabs :deep(.el-tabs__item.is-active) { color: #67e8f9; }
.scanner-tabs :deep(.el-tabs__active-bar) { background-color: #06b6d4; }
</style>
