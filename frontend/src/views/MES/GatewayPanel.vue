<template>
  <div>
    <div class="text-xs text-gray-500 mb-3">MES 网关连接为全局配置，所有项目共用，不随当前项目切换。</div>
    <!-- 工具栏 -->
    <div class="flex items-center justify-between mb-4">
      <div class="flex items-center gap-3">
        <span class="text-sm text-gray-300">外部 MES 连接</span>
        <el-tag size="small" :type="enabledCount > 0 ? 'success' : 'info'">
          {{ enabledCount }} 个已启用
        </el-tag>
        <el-tooltip content="点击「测试」按钮时使用哪种事件的模拟数据" placement="top">
          <el-select v-model="testEventType" size="small" class="w-44" clearable placeholder="测试用事件 (默认自动)">
            <el-option label="cycle_end (周期结束)" value="cycle_end" />
            <el-option label="box_complete (箱汇总)" value="box_complete" />
            <el-option label="box_timeout (箱超时)" value="box_timeout" />
          </el-select>
        </el-tooltip>
      </div>
      <div class="flex gap-2">
        <el-button size="small" type="info" @click="openAllLogs">全部日志</el-button>
        <el-button size="small" type="success" @click="openCreate">新建连接</el-button>
      </div>
    </div>

    <!-- 连接列表 -->
    <el-table :data="connections" stripe size="small" class="mes-table" max-height="calc(100vh - 300px)">
      <el-table-column prop="id" label="ID" width="60" />
      <el-table-column prop="name" label="连接名称" width="140" />
      <el-table-column prop="adapter_type" label="类型" width="120">
        <template #default="{ row }">
          <el-tag size="small" :type="adapterTagType(row.adapter_type)">
            {{ adapterLabel(row.adapter_type) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="80">
        <template #default="{ row }">
          <el-switch v-model="row.enabled" size="small" @change="toggleEnabled(row)" />
        </template>
      </el-table-column>
      <el-table-column label="推送事件" width="180">
        <template #default="{ row }">
          <div class="flex gap-1 flex-wrap">
            <el-tag v-for="e in (row.push_events || [])" :key="e" size="small" type="info">
              {{ eventLabels[e] || e }}
            </el-tag>
            <span v-if="!row.push_events?.length" class="text-xs text-gray-500">未配置</span>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="绑定工位" width="140">
        <template #default="{ row }">
          <div v-if="row.bound_channels && row.bound_channels.length" class="flex gap-1 flex-wrap">
            <el-tag v-for="ch in row.bound_channels" :key="ch" size="small">
              工位 {{ ch + 1 }}
            </el-tag>
          </div>
          <span v-else class="text-xs text-gray-500">全部工位</span>
        </template>
      </el-table-column>
      <el-table-column label="重试" width="90">
        <template #default="{ row }">
          {{ row.retry_count }}次/{{ row.retry_interval_sec }}s
        </template>
      </el-table-column>
      <el-table-column label="最后同步" width="160">
        <template #default="{ row }">
          {{ row.last_sync_at ? formatTime(row.last_sync_at) : '-' }}
        </template>
      </el-table-column>
      <el-table-column label="健康" width="120">
        <template #default="{ row }">
          <el-tooltip v-if="healthStatus[row.id]" :content="healthTooltip(row.id)" placement="top">
            <el-tag size="small" :type="healthStatus[row.id].ok ? 'success' : 'danger'">
              {{ healthStatus[row.id].ok ? '在线' : '离线' }}
            </el-tag>
          </el-tooltip>
          <span v-else class="text-xs text-gray-500">未探测</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="320" fixed="right">
        <template #default="{ row }">
          <div class="flex gap-1">
            <el-button size="small" @click="openEdit(row)">编辑</el-button>
            <el-button size="small" type="primary" @click="doTest(row)" :loading="row._testing">测试</el-button>
            <el-button size="small" @click="doProbe(row)" :loading="row._probing">探测</el-button>
            <el-button size="small" type="info" @click="openLogs(row)">日志</el-button>
            <el-popconfirm title="确认删除?" @confirm="doDelete(row)">
              <template #reference>
                <el-button size="small" type="danger">删除</el-button>
              </template>
            </el-popconfirm>
          </div>
        </template>
      </el-table-column>
    </el-table>

    <!-- 新建/编辑对话框 -->
    <el-dialog
      v-model="showEditor"
      :title="editing ? '编辑连接' : '新建连接'"
      width="720px"
      destroy-on-close
    >
      <el-form :model="form" label-width="120px" size="small">
        <el-form-item label="连接名称" required>
          <el-input v-model="form.name" placeholder="如: 客户A MES" />
        </el-form-item>
        <el-form-item label="适配器类型">
          <el-radio-group v-model="form.adapter_type">
            <el-radio value="rest">REST / JSON</el-radio>
            <el-radio value="form-data">Form-Data (param=JSON)</el-radio>
            <el-radio value="form-urlencoded">Form 平铺</el-radio>
            <el-radio value="query-string">URL 参数</el-radio>
            <el-radio value="modbus_rtu">Modbus RTU</el-radio>
            <el-radio value="database">数据库直写</el-radio>
          </el-radio-group>
          <div class="text-xs text-gray-500 mt-1 ml-2">
            <div>• <b>REST/JSON</b>：body 发 JSON（<code>{"order_no":"..."}</code>）</div>
            <div>• <b>Form-Data</b>：表单里塞一个字段 <code>param={"order_no":"..."}</code>（整个 JSON 串）</div>
            <div>• <b>Form 平铺</b>：表单字段分开传 <code>order_no=xxx&amp;result=NG</code>（x-www-form-urlencoded）</div>
            <div>• <b>URL 参数</b>：字段全塞 URL 里 <code>?order_no=xxx&amp;result=NG</code>，body 为空</div>
            <div>• <b>数据库直写</b>：直接 INSERT 进客户关系库中间表（达梦/MySQL/PostgreSQL/SQL Server/SQLite），模板顶层键名 = 目标表列名</div>
          </div>
        </el-form-item>

        <!-- REST / Form-Data 配置 -->
        <template v-if="isHttpAdapter">
          <el-form-item label="接口地址">
            <el-input v-model="configUrl" placeholder="http://192.168.50.12:11211/api/..." />
          </el-form-item>
          <el-form-item label="请求方法">
            <el-select v-model="configMethod" class="w-28">
              <el-option label="POST" value="POST" />
              <el-option label="PUT" value="PUT" />
              <el-option label="GET" value="GET" />
            </el-select>
          </el-form-item>
          <el-form-item v-if="form.adapter_type === 'form-data'" label="表单字段名">
            <el-input v-model="configFormKey" placeholder="param" class="w-40" />
            <div class="text-xs text-gray-500 ml-2">客户常用: param / data / json / content</div>
          </el-form-item>

          <!-- 鉴权 -->
          <el-divider content-position="left">鉴权方式</el-divider>
          <el-form-item label="鉴权类型">
            <el-select v-model="authType" class="w-48">
              <el-option label="无鉴权" value="none" />
              <el-option label="Basic (用户名/密码)" value="basic" />
              <el-option label="Bearer Token" value="bearer" />
              <el-option label="API Key Header" value="api_key" />
              <el-option label="自定义 Header (多个)" value="custom_header" />
            </el-select>
          </el-form-item>
          <template v-if="authType === 'basic'">
            <el-form-item label="用户名">
              <el-input v-model="authUsername" class="w-64" />
            </el-form-item>
            <el-form-item label="密码">
              <el-input v-model="authPassword" type="password" class="w-64" show-password />
            </el-form-item>
          </template>
          <template v-if="authType === 'bearer'">
            <el-form-item label="Token">
              <el-input v-model="authToken" type="password" show-password placeholder="eyJhbGciOi..." />
            </el-form-item>
          </template>
          <template v-if="authType === 'api_key'">
            <el-form-item label="Header 名">
              <el-input v-model="authApiKeyHeader" placeholder="X-API-Key" class="w-64" />
            </el-form-item>
            <el-form-item label="Header 值">
              <el-input v-model="authApiKeyValue" type="password" show-password />
            </el-form-item>
          </template>
          <template v-if="authType === 'custom_header'">
            <el-form-item label="自定义 Headers">
              <div class="w-full">
                <div v-for="(h, idx) in authCustomHeaders" :key="idx" class="flex items-center gap-2 mb-2">
                  <el-input v-model="h.key" placeholder="Header 名" class="w-48" size="small" />
                  <span class="text-gray-400">=</span>
                  <el-input v-model="h.value" placeholder="Header 值" class="flex-1" size="small" />
                  <el-button size="small" type="danger" circle @click="authCustomHeaders.splice(idx, 1)">
                    <el-icon><Close /></el-icon>
                  </el-button>
                </div>
                <el-button size="small" @click="authCustomHeaders.push({ key: '', value: '' })">+ 添加 Header</el-button>
              </div>
            </el-form-item>
          </template>

          <!-- 额外请求头 (鉴权之外) -->
          <el-divider content-position="left">额外请求头 (可选)</el-divider>
          <el-form-item label="" label-width="0">
            <div class="w-full">
              <div class="text-xs text-gray-400 mb-2">
                鉴权之外需要附带的固定 header, 如 Accept-Language / X-Client-Id 等
              </div>
              <div v-for="(h, idx) in extraHeaders" :key="idx" class="flex items-center gap-2 mb-2">
                <el-input v-model="h.key" placeholder="Header 名" class="w-48" size="small" />
                <span class="text-gray-400">=</span>
                <el-input v-model="h.value" placeholder="Header 值" class="flex-1" size="small" />
                <el-button size="small" type="danger" circle @click="extraHeaders.splice(idx, 1)">
                  <el-icon><Close /></el-icon>
                </el-button>
              </div>
              <el-button size="small" @click="extraHeaders.push({ key: '', value: '' })">+ 添加 Header</el-button>
            </div>
          </el-form-item>
        </template>

        <!-- Modbus 配置 -->
        <template v-if="form.adapter_type === 'modbus_rtu'">
          <el-divider content-position="left">连接方式</el-divider>
          <el-form-item label="传输方式">
            <el-radio-group v-model="modbusTransport">
              <el-radio value="rtu">RTU (串口/RS485)</el-radio>
              <el-radio value="tcp">TCP (网络)</el-radio>
            </el-radio-group>
          </el-form-item>

          <!-- RTU 串口参数 -->
          <template v-if="modbusTransport === 'rtu'">
            <el-divider content-position="left">串口参数</el-divider>
            <el-form-item label="串口路径" required>
              <el-input v-model="modbusPort" placeholder="/dev/ttyUSB0 或 COM3" />
            </el-form-item>
            <div class="flex gap-4">
              <el-form-item label="波特率" class="flex-1">
                <el-select v-model="modbusBaudrate">
                  <el-option v-for="b in [1200,2400,4800,9600,19200,38400,57600,115200]" :key="b" :label="b" :value="b" />
                </el-select>
              </el-form-item>
              <el-form-item label="数据位" class="flex-1">
                <el-select v-model="modbusDataBits" class="w-20">
                  <el-option :label="7" :value="7" />
                  <el-option :label="8" :value="8" />
                </el-select>
              </el-form-item>
            </div>
            <div class="flex gap-4">
              <el-form-item label="校验方式" class="flex-1">
                <el-select v-model="modbusParity" class="w-28">
                  <el-option label="无校验 (N)" value="N" />
                  <el-option label="偶校验 (E)" value="E" />
                  <el-option label="奇校验 (O)" value="O" />
                </el-select>
              </el-form-item>
              <el-form-item label="停止位" class="flex-1">
                <el-select v-model="modbusStopBits" class="w-20">
                  <el-option :label="1" :value="1" />
                  <el-option :label="2" :value="2" />
                </el-select>
              </el-form-item>
            </div>
          </template>

          <!-- TCP 网络参数 -->
          <template v-if="modbusTransport === 'tcp'">
            <el-divider content-position="left">网络参数</el-divider>
            <div class="flex gap-4">
              <el-form-item label="主机地址" class="flex-1" required>
                <el-input v-model="modbusHost" placeholder="192.168.1.100" />
              </el-form-item>
              <el-form-item label="端口" label-width="45px" class="w-52 shrink-0">
                <el-input-number v-model="modbusTcpPort" :min="1" :max="65535" :precision="0"
                                 :controls="false" class="!w-full" placeholder="502" />
              </el-form-item>
            </div>
          </template>

          <!-- 通用 Modbus 参数 -->
          <el-divider content-position="left">通用参数</el-divider>
          <div class="flex gap-4">
            <el-form-item label="从站地址" class="flex-1">
              <el-input-number v-model="modbusSlaveId" :min="0" :precision="0" />
            </el-form-item>
            <el-form-item label="超时(秒)" class="flex-1">
              <el-input-number v-model="modbusTimeout" :min="0" :precision="2" />
            </el-form-item>
            <el-form-item label="字节序" class="flex-1">
              <el-select v-model="modbusByteOrder" class="w-24">
                <el-option label="大端" value="big" />
                <el-option label="小端" value="little" />
              </el-select>
            </el-form-item>
          </div>
          <div class="flex gap-4">
            <el-form-item label="OK 写入值" class="flex-1">
              <el-input-number v-model="modbusOkValue" :min="0" :precision="2" />
            </el-form-item>
            <el-form-item label="NG 写入值" class="flex-1">
              <el-input-number v-model="modbusNgValue" :min="0" :precision="0" />
            </el-form-item>
          </div>

          <el-divider content-position="left">寄存器映射</el-divider>
          <div class="mb-3 text-xs text-gray-400">
            Holding Registers: 40001-49999。选"固定值"时在右侧输入常量。
          </div>
          <div v-for="(reg, idx) in modbusRegisters" :key="idx" class="flex items-center gap-2 mb-2">
            <el-input-number v-model="reg.address" :min="0" :precision="0" placeholder="40001" controls-position="right" size="small" class="w-32" />
            <el-select v-model="reg.source" placeholder="数据来源" size="small" class="w-36">
              <el-option label="检测结果" value="result_code" />
              <el-option label="合格计数" value="ok_count" />
              <el-option label="不良计数" value="ng_count" />
              <el-option label="总产量" value="total_count" />
              <el-option label="周期ID" value="cycle_id" />
              <el-option label="耗时(ms)" value="duration_ms" />
              <el-option label="检测次数" value="inspection_count" />
              <el-option label="固定值" value="const" />
            </el-select>
            <el-select v-model="reg.data_type" size="small" class="w-24">
              <el-option label="uint16" value="uint16" />
              <el-option label="int16" value="int16" />
              <el-option label="uint32" value="uint32" />
              <el-option label="int32" value="int32" />
            </el-select>
            <el-input-number v-if="reg.source === 'const'" v-model="reg.const_value" :min="0" :precision="0" size="small" placeholder="值" class="w-28" />
            <el-button size="small" type="danger" circle @click="modbusRegisters.splice(idx, 1)">
              <el-icon><Close /></el-icon>
            </el-button>
          </div>
          <el-button size="small" @click="modbusRegisters.push({ address: 40001, source: 'result_code', data_type: 'uint16' })">+ 添加寄存器</el-button>
        </template>

        <!-- v3.35 数据库直写配置 -->
        <template v-if="form.adapter_type === 'database'">
          <el-divider content-position="left">数据库连接</el-divider>
          <div class="flex gap-4">
            <el-form-item label="数据库类型" class="flex-1">
              <el-select v-model="dbType" @change="onDbTypeChange">
                <el-option label="达梦 DM" value="dm" />
                <el-option label="MySQL" value="mysql" />
                <el-option label="PostgreSQL" value="postgresql" />
                <el-option label="SQL Server" value="sqlserver" />
                <el-option label="SQLite (本地文件)" value="sqlite" />
              </el-select>
            </el-form-item>
            <template v-if="dbType !== 'sqlite'">
              <el-form-item label="主机" class="flex-1" required>
                <el-input v-model="dbHost" placeholder="192.168.1.50" />
              </el-form-item>
              <!-- v3.45: 独立窄 label + 禁压缩 + 去步进钮 + 限 1~65535,
                   治"全局 label 120px 把端口框挤到 25px 宽, 粘贴出超长数字看不见还把连接搞炸" -->
              <el-form-item label="端口" label-width="45px" class="w-52 shrink-0">
                <el-input-number v-model="dbPort" :min="1" :max="65535" :precision="0"
                                 :controls="false" class="!w-full" placeholder="5236" />
              </el-form-item>
            </template>
          </div>
          <template v-if="dbType !== 'sqlite'">
            <div class="flex gap-4">
              <el-form-item label="用户名" class="flex-1">
                <el-input v-model="dbUser" :placeholder="dbType === 'dm' ? 'SYSDBA' : 'root'" />
              </el-form-item>
              <el-form-item label="密码" class="flex-1">
                <el-input v-model="dbPassword" type="password" show-password />
              </el-form-item>
            </div>
          </template>
          <el-form-item :label="dbType === 'sqlite' ? '数据库文件路径' : '数据库名'">
            <el-input v-model="dbDatabase"
              :placeholder="dbType === 'sqlite' ? 'D:/data/mes.db' : (dbType === 'dm' ? '留空 = 用户默认库/schema' : 'PROD')" />
          </el-form-item>
          <el-form-item label="目标表名" required>
            <el-input v-model="dbTable" placeholder="T_WEIGH_RECORD（可带 schema 前缀，如 PROD.T_WEIGH_RECORD）" />
            <div class="text-xs text-gray-500 mt-1">
              下方「字段映射模板」的<b>顶层键名 = 该表的列名</b>，值照常用 <code>{'{'}字段路径{'}'}</code> 引用检测数据；
              带 <code>_array_source</code> 的模板按数组逐行 INSERT（同一事务）。驱动未安装时测试推送会直接提示要装哪个包。
            </div>
          </el-form-item>
        </template>
        <el-form-item label="绑定工位">
          <template v-if="channelCount > 1">
            <el-checkbox-group v-model="form.bound_channels">
              <el-checkbox v-for="ch in channelCount" :key="ch - 1" :value="ch - 1" :label="`工位 ${ch}`" />
            </el-checkbox-group>
            <div class="text-xs text-gray-500 mt-1">不选则接收所有工位的数据</div>
          </template>
          <span v-else class="text-xs text-gray-400">单工位模式，无需绑定</span>
        </el-form-item>
        <el-form-item label="推送时机">
          <el-select v-model="form.push_events" multiple filterable allow-create default-first-option
            placeholder="订阅哪些事件触发推送 (可输入插件自定义事件名/别名)" class="w-full">
            <el-option v-for="e in eventOptions" :key="e.value" :label="e.label" :value="e.value" />
          </el-select>
          <span class="text-xs text-gray-500 ml-1">除下拉内置事件外，可直接输入任意事件名（如插件主动触发的自定义事件、完工回传 task_complete、称重无码 weight_no_barcode、包装结算 packaging_complete）</span>
        </el-form-item>
        <el-form-item v-if="form.adapter_type !== 'modbus_rtu'" label="按结果过滤">
          <el-checkbox-group v-model="pushOnResult">
            <el-checkbox value="OK" label="OK (合格)" />
            <el-checkbox value="NG" label="NG (不合格, 含超时/缺失)" />
          </el-checkbox-group>
          <div class="text-xs text-gray-500">两项都选 = 全推; 只选 NG = 仅不合格才推, 适合客户只关心不良的场景</div>
        </el-form-item>
        <el-form-item v-if="form.adapter_type !== 'modbus_rtu'" label="附带截图">
          <el-switch v-model="attachSnapshot" />
          <span class="text-xs text-gray-500 ml-2">
            开 → 推送时抓该工位当前画面, 模板里用 <code>{snapshot.image_base64}</code> 引用 (纯 Base64, 报警上报必开)
          </span>
        </el-form-item>
        <el-form-item v-if="form.adapter_type !== 'modbus_rtu' && attachSnapshot" label="截图上限(字节)">
          <el-input-number v-model="snapshotMaxBytes" :min="0" :step="100000" class="w-44" />
          <span class="text-xs text-gray-500 ml-2">超限自动逐级降质/缩边压到限内再传 (避免对方 413)；0 = 不限</span>
        </el-form-item>
        <el-form-item v-if="form.adapter_type !== 'modbus_rtu' && attachSnapshot" label="图像质量">
          <el-input-number v-model="snapshotQuality" :min="0" :max="100" :step="5" class="w-36" />
          <span class="text-xs text-gray-500 ml-2">1~100 直接控制 JPEG 质量；0 = 用源默认(约70)不重编码</span>
        </el-form-item>
        <el-form-item v-if="form.adapter_type !== 'modbus_rtu' && attachSnapshot" label="高级压缩参数">
          <el-collapse class="w-full">
            <el-collapse-item title="超限压缩阶梯/缩放 (默认沿用历史值，一般无需改)">
              <div class="flex flex-col gap-2">
                <div class="flex items-center gap-2">
                  <span class="text-xs text-gray-400 w-28">降质阶梯</span>
                  <el-input v-model="snapshotQualityLadder" placeholder="如 60,45,30,20,12" class="w-60" />
                  <span class="text-xs text-gray-500">原尺寸逐级尝试的质量(逗号分隔)</span>
                </div>
                <div class="flex items-center gap-2">
                  <span class="text-xs text-gray-400 w-28">缩放比例</span>
                  <el-input-number v-model="snapshotScaleFactor" :min="0.1" :max="0.95" :step="0.05" :precision="2" class="w-32" />
                  <span class="text-xs text-gray-400 w-20">缩放轮数</span>
                  <el-input-number v-model="snapshotScaleRounds" :min="0" :max="10" :step="1" class="w-28" />
                </div>
                <div class="flex items-center gap-2">
                  <span class="text-xs text-gray-400 w-28">最小边长</span>
                  <el-input-number v-model="snapshotMinEdge" :min="1" :max="4096" :step="8" class="w-32" />
                  <span class="text-xs text-gray-400 w-20">缩图质量</span>
                  <el-input-number v-model="snapshotScaledQuality" :min="1" :max="100" :step="5" class="w-28" />
                </div>
              </div>
            </el-collapse-item>
          </el-collapse>
        </el-form-item>
        <el-form-item v-if="form.adapter_type !== 'modbus_rtu' && attachSnapshot" label="带 dataURI 前缀">
          <el-switch v-model="snapshotDataUri" />
          <span class="text-xs text-gray-500 ml-2">
            额外提供 <code>{snapshot.image_data_uri}</code>(带 data:image/jpeg;base64, 前缀)；川南要求纯 Base64, 保持关
          </span>
        </el-form-item>
        <el-form-item label="重试次数">
          <el-input-number v-model="form.retry_count" :min="0" :precision="0" />
        </el-form-item>
        <el-form-item label="重试间隔(秒)">
          <el-input-number v-model="form.retry_interval_sec" :min="0" :precision="2" />
        </el-form-item>
        <el-form-item v-if="form.adapter_type !== 'modbus_rtu'" label="重试退避">
          <el-radio-group v-model="form.retry_backoff">
            <el-radio value="fixed">固定间隔</el-radio>
            <el-radio value="exponential">指数退避(×2)</el-radio>
          </el-radio-group>
          <span class="text-xs text-gray-500 ml-2">指数退避：间隔×2 递增（间隔设 1 即 1→2→4 秒，川南 §5.1）</span>
        </el-form-item>
        <el-form-item v-if="isHttpAdapter" label="4xx 是否重试">
          <el-switch v-model="form.retry_on_4xx" />
          <span class="text-xs text-gray-500 ml-2">关 = 仅对 5xx / 网络超时重试，4xx(客户端错误)不重试（川南 §5.1）</span>
        </el-form-item>
        <el-form-item v-if="form.adapter_type !== 'modbus_rtu'" label="重试总预算(秒)">
          <el-input-number v-model="form.retry_budget_sec" :min="0" :precision="0" data-testid="gw-retry-budget" />
          <span class="text-xs text-gray-500 ml-2">单次推送(含全部重试)的耗时上限，超时提前记失败；0 = 不限制。MES 长时间断连时防止单条推送拖住队列</span>
        </el-form-item>
        <el-form-item v-if="form.adapter_type !== 'modbus_rtu'" label="请求超时(秒)">
          <el-input-number v-model="requestTimeout" :min="1" :precision="1" class="w-32" />
          <span class="text-xs text-gray-500 ml-2">单值总超时；仅当下方连接/读取留空时生效（默认 30s）</span>
        </el-form-item>
        <el-form-item v-if="isHttpAdapter" label="连接/读取超时(秒)">
          <el-input-number v-model="connectTimeout" :min="0" :precision="1" placeholder="连接" class="w-32" />
          <span class="mx-1 text-gray-400">/</span>
          <el-input-number v-model="readTimeout" :min="0" :precision="1" placeholder="读取" class="w-32" />
          <span class="text-xs text-gray-500 ml-2">填了就用 (连接,读取) 分离超时，覆盖上面的单值；川南 §5.1 连接 5 / 读取 10(含图建议 30)</span>
        </el-form-item>

        <!-- 主动健康探测 (A2) -->
        <el-form-item v-if="isHttpAdapter" label="主动健康探测">
          <el-switch v-model="healthProbeEnabled" />
          <span class="text-xs text-gray-500 ml-2">开 → 后台周期探活对端，列表显示在线/离线徽标（默认关，零开销）</span>
        </el-form-item>
        <template v-if="isHttpAdapter && healthProbeEnabled">
          <el-form-item label="探测间隔(秒)">
            <el-input-number v-model="healthProbeInterval" :min="5" :step="5" class="w-32" />
            <span class="text-xs text-gray-400 ml-2">最小 5 秒</span>
          </el-form-item>
          <el-form-item label="探测地址">
            <el-input v-model="healthProbeUrl" placeholder="留空=用推送地址；如 http://mes/health" class="w-full" />
          </el-form-item>
          <el-form-item label="探测方法/超时">
            <el-select v-model="healthProbeMethod" class="w-28">
              <el-option label="GET" value="GET" />
              <el-option label="POST" value="POST" />
              <el-option label="HEAD" value="HEAD" />
            </el-select>
            <span class="text-xs text-gray-400 mx-2">超时(秒)</span>
            <el-input-number v-model="healthProbeTimeout" :min="1" :step="1" class="w-28" />
            <span class="text-xs text-gray-400 mx-2">期望状态码</span>
            <el-input-number v-model="healthProbeExpect" :min="0" :step="1" placeholder="空=2xx" class="w-32" />
          </el-form-item>
        </template>

        <!-- REST/Form-Data 专属配置 -->
        <template v-if="form.adapter_type !== 'modbus_rtu'">
        <!-- 静态字段 -->
        <el-divider content-position="left">静态字段</el-divider>
        <div class="mb-3 text-xs text-gray-400">
          固定值字段 (如设备编号), 格式: namespace.key = value
        </div>
        <div v-for="(sf, idx) in staticFields" :key="idx" class="flex items-center gap-2 mb-2">
          <el-input v-model="sf.key" placeholder="device.device_id" class="w-48" size="small" />
          <span class="text-gray-400">=</span>
          <el-input v-model="sf.value" placeholder="JC-M-1234" class="flex-1" size="small" />
          <el-button size="small" type="danger" circle @click="staticFields.splice(idx, 1)">
            <el-icon><Close /></el-icon>
          </el-button>
        </div>
        <el-button size="small" @click="staticFields.push({ key: '', value: '' })">+ 添加</el-button>

        <!-- 额外字段输入框定义 -->
        <el-divider content-position="left">动态额外字段 (Monitor 页输入)</el-divider>
        <div class="mb-3 text-xs text-gray-400">
          定义 Monitor 检测页面显示的额外输入框 (如重量), 数据通过 extra.xxx 引用
        </div>
        <div v-for="(ef, idx) in extraFieldsDef" :key="idx" class="flex items-center gap-2 mb-2">
          <el-input v-model="ef.key" placeholder="weight" class="w-28" size="small" />
          <el-input v-model="ef.label" placeholder="重量(g)" class="w-28" size="small" />
          <el-select v-model="ef.type" class="w-24" size="small">
            <el-option label="文本" value="text" />
            <el-option label="数字" value="number" />
          </el-select>
          <el-input v-model="ef.default" placeholder="默认值" class="w-24" size="small" />
          <el-button size="small" type="danger" circle @click="extraFieldsDef.splice(idx, 1)">
            <el-icon><Close /></el-icon>
          </el-button>
        </div>
        <el-button size="small" @click="extraFieldsDef.push({ key: '', label: '', type: 'text', default: '' })">+ 添加</el-button>

        <!-- 物料名称映射 (box_complete 场景) -->
        <el-divider content-position="left">物料名称映射 (可选)</el-divider>
        <div class="mb-3 text-xs text-gray-400">
          把内部步骤名 (如"螺丝A") 映射成客户 MES 物料代码 (如"PART-001"), 影响 <code>ng_items</code> 字段。
          未配置时按原样透传。
        </div>
        <div v-for="(m, idx) in labelMappings" :key="idx" class="flex items-center gap-2 mb-2">
          <el-input v-model="m.key" placeholder="内部步骤名 (如 螺丝A)" class="w-48" size="small" />
          <span class="text-gray-400">→</span>
          <el-input v-model="m.value" placeholder="客户物料代码 (如 PART-001)" class="flex-1" size="small" />
          <el-button size="small" type="danger" circle @click="labelMappings.splice(idx, 1)">
            <el-icon><Close /></el-icon>
          </el-button>
        </div>
        <div class="flex items-center gap-3">
          <el-button size="small" @click="labelMappings.push({ key: '', value: '' })">+ 添加映射</el-button>
          <span class="text-xs text-gray-400">映射模式:</span>
          <el-radio-group v-model="labelMappingMode" size="small">
            <el-radio value="replace">替换 (ng_items 直接变成代码)</el-radio>
            <el-radio value="keep_both">保留 (原 ng_items + 新增 ng_items_mapped)</el-radio>
          </el-radio-group>
        </div>

        <!-- 字段映射模板 -->
        <el-divider content-position="left">字段映射模板 (JSON)</el-divider>
        <div class="flex items-center gap-2 mb-2">
          <span class="text-xs text-gray-400">一键填入:</span>
          <el-select v-model="presetTemplateKey" placeholder="选择预设模板" size="small" class="w-96" @change="applyPresetTemplate">
            <el-option
              v-for="(p, k) in PRESET_TEMPLATES"
              :key="k" :label="p.label" :value="k"
            />
          </el-select>
        </div>
        <div class="mb-3 text-xs text-gray-400">
          用 {context.field} 引用数据。可用字段:<br>
          <b>box_complete 事件</b>: {order_no} {workpiece_id} {overall_result} {ng_items} {box_serial} {total_stations} {completed_stations} {timestamp}<br>
          <b>cycle_end 事件</b>: {cycle.*} {workpiece.*} {order.*} {project.*} {steps[]} {defects[]} {extra.*} {session.*} {timestamp}<br>
          <b>数组语法</b>: <code>{"_array_source":"ng_items", "_item_template":"{item}"}</code>
        </div>
        <el-input
          v-model="templateJson"
          type="textarea"
          :rows="12"
          :autosize="{ minRows: 6, maxRows: 20 }"
          placeholder='{"order_no": "{order_no}", "result": "{overall_result}", ...}'
          spellcheck="false"
          class="template-editor"
        />
        <div v-if="templateError" class="text-xs text-red-400 mt-1">{{ templateError }}</div>

        <!-- 响应校验 -->
        <el-divider content-position="left">响应校验</el-divider>
        <div class="flex items-center gap-3">
          <span class="text-xs text-gray-400">成功字段:</span>
          <el-input v-model="successField" placeholder="success" class="w-32" size="small" />
          <span class="text-xs text-gray-400">期望值:</span>
          <el-select v-model="successExpect" class="w-24" size="small">
            <el-option label="true" :value="true" />
            <el-option label="false" :value="false" />
            <el-option label="1" :value="1" />
            <el-option label="0" :value="0" />
          </el-select>
        </div>
        </template>
      </el-form>

      <template #footer>
        <el-button @click="showEditor = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="doSave">保存</el-button>
      </template>
    </el-dialog>

    <!-- 测试结果弹窗 -->
    <el-dialog v-model="showTestResult" title="测试结果" width="600px" destroy-on-close>
      <div v-if="testResult">
        <el-result
          :icon="testResult.success ? 'success' : 'error'"
          :title="testResult.success ? '连接成功' : '连接失败'"
          :sub-title="testResult.error || `${testResult.duration_ms}ms`"
        />
        <el-divider content-position="left">发送数据预览</el-divider>
        <pre class="text-xs bg-gray-900 p-3 rounded overflow-auto max-h-48">{{ formatJSON(testResult.payload_preview) }}</pre>
        <el-divider content-position="left">响应</el-divider>
        <pre class="text-xs bg-gray-900 p-3 rounded overflow-auto max-h-48">{{ formatJSON(testResult.response_body) }}</pre>
      </div>
    </el-dialog>

    <!-- 通讯日志弹窗 -->
    <el-dialog v-model="showLogs" title="通讯日志" width="900px" destroy-on-close>
      <div class="flex items-center gap-3 mb-3">
        <el-select v-model="logFilter.success" placeholder="状态" size="small" class="w-24" clearable>
          <el-option label="成功" :value="true" />
          <el-option label="失败" :value="false" />
        </el-select>
        <el-button size="small" @click="loadLogs">查询</el-button>
        <span class="text-xs text-gray-400 ml-auto">共 {{ logTotal }} 条</span>
      </div>
      <el-table :data="logs" stripe size="small" max-height="400px">
        <el-table-column prop="event_type" label="事件" width="100">
          <template #default="{ row }">
            <el-tag size="small" type="info">{{ eventLabels[row.event_type] || row.event_type }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="70">
          <template #default="{ row }">
            <el-tag :type="row.success ? 'success' : 'danger'" size="small">
              {{ row.success ? '成功' : '失败' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="status_code" label="HTTP" width="70" />
        <el-table-column prop="duration_ms" label="耗时" width="80">
          <template #default="{ row }">{{ row.duration_ms }}ms</template>
        </el-table-column>
        <el-table-column prop="error_msg" label="错误信息" min-width="200" show-overflow-tooltip />
        <el-table-column prop="created_at" label="时间" width="160">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="详情" width="80">
          <template #default="{ row }">
            <el-button size="small" link @click="showLogDetail(row)">查看</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div class="flex justify-center mt-3">
        <el-pagination
          v-model:current-page="logPage"
          :page-size="pollingStore.logLimit('gateway', 20)"
          :total="logTotal"
          layout="prev, pager, next"
          small
          @current-change="loadLogs"
        />
      </div>
    </el-dialog>

    <!-- 日志详情弹窗 -->
    <el-dialog v-model="showDetail" title="请求/响应详情" width="700px" destroy-on-close>
      <div v-if="detailLog">
        <el-divider content-position="left">请求体</el-divider>
        <pre class="text-xs bg-gray-900 p-3 rounded overflow-auto max-h-48">{{ formatJSON(detailLog.request_body) }}</pre>
        <el-divider content-position="left">响应体</el-divider>
        <pre class="text-xs bg-gray-900 p-3 rounded overflow-auto max-h-48">{{ formatJSON(detailLog.response_body) }}</pre>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onBeforeUnmount } from 'vue'
import { Close } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import {
  getConnections, createConnection, updateConnection, deleteConnection,
  testConnection, getGatewayLogs, getHealthStatus, probeNow
} from '@/api/gateway'
import { dbg, dbgErr } from '@/utils/debug'
import { usePollingStore } from '@/store/usePollingStore'

const pollingStore = usePollingStore()

const eventLabels = {
  cycle_end: '周期结束',
  session_end: '会话结束',
  box_complete: '集群汇总',
  box_timeout: '集群超时',
  weight_no_barcode: '称重无码',
  packaging_complete: '包装结算完成',
  task_complete: '工单完工回传',
}

// 内置事件下拉项 (allow-create 仍可输入插件自定义事件名/别名)
const eventOptions = [
  { value: 'cycle_end', label: 'cycle_end (检测周期结束)' },
  { value: 'session_end', label: 'session_end (检测会话结束)' },
  { value: 'box_complete', label: 'box_complete (集群汇总完成)' },
  { value: 'box_timeout', label: 'box_timeout (集群超时推送)' },
  { value: 'weight_no_barcode', label: 'weight_no_barcode (称重有重无码)' },
  { value: 'weighing_product_done', label: 'weighing_product_done (称重成品结案)' },
  { value: 'packaging_complete', label: 'packaging_complete (包装结算完成)' },
  { value: 'task_complete', label: 'task_complete (完工回传)' },
]

const connections = ref([])
const showEditor = ref(false)
const editing = ref(null)
const saving = ref(false)
const showTestResult = ref(false)
const testResult = ref(null)
const showLogs = ref(false)
const showDetail = ref(false)
const detailLog = ref(null)
const logs = ref([])
const logTotal = ref(0)
const logPage = ref(1)
const logFilter = reactive({ success: null, connectionId: null })
const logConnId = ref(null)

const form = reactive({
  name: '',
  adapter_type: 'rest',
  push_events: [],
  retry_count: 3,
  retry_interval_sec: 5,
  retry_backoff: 'fixed',
  retry_on_4xx: true,
  retry_budget_sec: 0,
  bound_channels: [],
})

const channelCount = ref(1)
const loadChannelCount = async () => {
  try {
    const { getWorkstations } = await import('@/api/detection')
    const res = await getWorkstations()
    channelCount.value = res.data.channel_count || 1
  } catch { channelCount.value = 1 }
}

const configUrl = ref('')
const configMethod = ref('POST')
const configFormKey = ref('param')
const templateJson = ref('{}')
const templateError = ref('')
const successField = ref('success')
const successExpect = ref(true)
const staticFields = ref([])
const extraFieldsDef = ref([])

// 鉴权 / 自定义请求头 / 按结果过滤 / 物料名称映射
const authType = ref('none')
const authUsername = ref('')
const authPassword = ref('')
const authToken = ref('')
const authApiKeyHeader = ref('X-API-Key')
const authApiKeyValue = ref('')
const authCustomHeaders = ref([])
const extraHeaders = ref([])
const pushOnResult = ref(['OK', 'NG'])
const attachSnapshot = ref(false)
const snapshotMaxBytes = ref(0)
const snapshotDataUri = ref(false)
const snapshotQuality = ref(0)            // 0=源默认不重编码; 1~100=直接控制质量
const snapshotQualityLadder = ref('')     // 逗号分隔降质阶梯, 空=后端默认
const snapshotScaleFactor = ref(0.75)
const snapshotScaleRounds = ref(4)
const snapshotMinEdge = ref(32)
const snapshotScaledQuality = ref(35)
// 出站主动健康探测 (A2)
const healthProbeEnabled = ref(false)
const healthProbeInterval = ref(60)
const healthProbeUrl = ref('')
const healthProbeMethod = ref('GET')
const healthProbeTimeout = ref(5)
const healthProbeExpect = ref(null)
const healthStatus = ref({})              // {conn_id: {ok, checked_at, latency_ms, ...}}
const requestTimeout = ref(30)     // 单值总超时(秒), 仅连接/读取留空时生效
const connectTimeout = ref(null)   // 连接超时(秒), null=用默认单值超时
const readTimeout = ref(null)      // 读取超时(秒), null=用默认
const labelMappings = ref([])
const labelMappingMode = ref('replace')
const testEventType = ref('')
const presetTemplateKey = ref('')

// v3.35 数据库直写适配器 (达梦/MySQL/PG/SQLServer/SQLite)
const DB_DEFAULT_PORTS = { dm: 5236, mysql: 3306, postgresql: 5432, sqlserver: 1433, sqlite: 0 }
const dbType = ref('dm')
const dbHost = ref('')
const dbPort = ref(5236)
const dbUser = ref('')
const dbPassword = ref('')
const dbDatabase = ref('')
const dbTable = ref('')
const onDbTypeChange = (t) => { dbPort.value = DB_DEFAULT_PORTS[t] ?? 0 }

// HTTP 系适配器 (URL/鉴权/请求头/健康探测等仅对它们有意义)
const isHttpAdapter = computed(() =>
  form.adapter_type !== 'modbus_rtu' && form.adapter_type !== 'database')

const PRESET_TEMPLATES = {
  box_complete_4field: {
    label: 'box_complete · 客户4字段 (form-data 主推荐)',
    event: 'box_complete',
    template: {
      order_no: '{order_no}',
      workpiece_id: '{workpiece_id}',
      result: '{overall_result}',
      missing_items: { _array_source: 'ng_items', _item_template: '{item}' },
    },
  },
  box_complete_full: {
    label: 'box_complete · 完整数据 (含工位明细)',
    event: 'box_complete',
    template: {
      box_serial: '{box_serial}',
      order_no: '{order_no}',
      workpiece_id: '{workpiece_id}',
      result: '{overall_result}',
      total_stations: '{total_stations}',
      completed_stations: '{completed_stations}',
      missing_items: { _array_source: 'ng_items', _item_template: '{item}' },
      timestamp: '{timestamp}',
    },
  },
  cycle_end_simple: {
    label: 'cycle_end · 单工位周期结束 (简化版)',
    event: 'cycle_end',
    template: {
      order_no: '{order.order_no}',
      workpiece_id: '{workpiece.serial_no}',
      result: '{cycle.result}',
      duration: '{cycle.duration}',
    },
  },
  cycle_end_chuannan_alarm: {
    label: 'cycle_end · 川南报警上报 /warning/report (含截图)',
    event: 'cycle_end',
    pushOnResult: ['NG'],
    attachSnapshot: true,
    successField: 'code',
    successExpect: 0,
    retryCount: 3,
    retryInterval: 1,
    retryBackoff: 'exponential',
    retryOn4xx: false,
    connectTimeout: 5,
    readTimeout: 30,
    template: {
      TaskNo: '{order.order_no}',
      ProductCode: '{order.product_code}',
      StepCode: '{order.extra_data.inbound.step_code}',
      Operator: '{order.extra_data.inbound.operator}',
      WarningText: '{cycle.ng_reason}',
      Image: '{snapshot.image_base64}',
      BeginTime: '{timestamp}',
    },
  },
  cycle_end_chuannan_complete: {
    label: 'cycle_end · 川南完工上报 /task/complete',
    event: 'cycle_end',
    pushOnResult: ['OK', 'NG'],   // 每件装配完成都上报完工 (不分良/不良)
    attachSnapshot: false,
    successField: 'code',
    successExpect: 0,
    retryCount: 3,
    retryInterval: 1,
    retryBackoff: 'exponential',
    retryOn4xx: false,
    connectTimeout: 5,
    readTimeout: 10,
    template: {
      TaskNo: '{order.order_no}',
      ProductCode: '{order.product_code}',
      StepCode: '{order.extra_data.inbound.step_code}',
      Operator: '{order.extra_data.inbound.operator}',
      IsComplete: true,
      BeginTime: '{timestamp}',
    },
  },
  cycle_end_with_steps: {
    label: 'cycle_end · 含步骤明细',
    event: 'cycle_end',
    template: {
      order_no: '{order.order_no}',
      workpiece_id: '{workpiece.serial_no}',
      result: '{cycle.result}',
      ng_reason: '{cycle.ng_reason}',
      steps: {
        _array_source: 'steps',
        _item_template: {
          label: '{item.label}',
          is_good: '{item.is_good}',
          duration: '{item.duration}',
        },
      },
    },
  },
}

function applyPresetTemplate() {
  const key = presetTemplateKey.value
  if (!key || !PRESET_TEMPLATES[key]) return
  const preset = PRESET_TEMPLATES[key]
  templateJson.value = JSON.stringify(preset.template, null, 2)
  if (preset.event && !form.push_events.includes(preset.event)) {
    form.push_events = [...form.push_events, preset.event]
  }
  if (Array.isArray(preset.pushOnResult)) pushOnResult.value = [...preset.pushOnResult]
  if (typeof preset.attachSnapshot === 'boolean') attachSnapshot.value = preset.attachSnapshot
  if (preset.successField) successField.value = preset.successField
  if (preset.successExpect !== undefined) successExpect.value = preset.successExpect
  if (preset.retryCount !== undefined) form.retry_count = preset.retryCount
  if (preset.retryInterval !== undefined) form.retry_interval_sec = preset.retryInterval
  if (preset.retryBackoff) form.retry_backoff = preset.retryBackoff
  if (typeof preset.retryOn4xx === 'boolean') form.retry_on_4xx = preset.retryOn4xx
  if (preset.connectTimeout !== undefined) connectTimeout.value = preset.connectTimeout
  if (preset.readTimeout !== undefined) readTimeout.value = preset.readTimeout
  templateError.value = ''
  ElMessage.success(`已填入模板: ${preset.label}`)
  presetTemplateKey.value = ''
}

const modbusTransport = ref('rtu')
const modbusPort = ref('')
const modbusBaudrate = ref(9600)
const modbusDataBits = ref(8)
const modbusSlaveId = ref(1)
const modbusParity = ref('N')
const modbusStopBits = ref(1)
const modbusByteOrder = ref('big')
const modbusTimeout = ref(3)
const modbusOkValue = ref(1)
const modbusNgValue = ref(2)
const modbusHost = ref('')
const modbusTcpPort = ref(502)
const modbusRegisters = ref([])

function adapterLabel(type) {
  const map = {
    rest: 'REST/JSON',
    'form-data': 'Form-Data',
    'form-urlencoded': 'Form 平铺',
    'query-string': 'URL 参数',
    modbus_rtu: 'Modbus',
    database: '数据库直写',
  }
  return map[type] || type
}
function adapterTagType(type) {
  const map = {
    rest: '',
    'form-data': 'warning',
    'form-urlencoded': 'warning',
    'query-string': 'info',
    modbus_rtu: 'success',
    database: 'danger',
  }
  return map[type] ?? 'info'
}

const enabledCount = computed(() => connections.value.filter(c => c.enabled).length)

function formatTime(t) {
  if (!t) return '-'
  return t.replace('T', ' ').substring(0, 19)
}

function formatJSON(v) {
  if (!v) return ''
  if (typeof v === 'string') {
    try { return JSON.stringify(JSON.parse(v), null, 2) } catch { return v }
  }
  try { return JSON.stringify(v, null, 2) } catch { return String(v) }
}

async function loadConnections() {
  try {
    const { data } = await getConnections()
    connections.value = (data || []).map(c => ({ ...c, _testing: false, _probing: false }))
  } catch (e) {
    dbgErr('mes.gateway', '加载连接列表', e)
    ElMessage.error('加载连接列表失败')
  }
}

async function loadHealthStatus() {
  try {
    const { data } = await getHealthStatus()
    healthStatus.value = data || {}
  } catch (e) {
    dbgErr('mes.gateway', '加载健康状态', e)
  }
}

function healthTooltip(id) {
  const s = healthStatus.value[id]
  if (!s) return ''
  const parts = [`检查时间: ${formatTime(s.checked_at)}`, `延迟: ${s.latency_ms}ms`]
  if (s.status_code) parts.push(`状态码: ${s.status_code}`)
  if (s.error) parts.push(`错误: ${s.error}`)
  return parts.join('  ')
}

async function doProbe(row) {
  row._probing = true
  try {
    const { data } = await probeNow(row.id)
    healthStatus.value = { ...healthStatus.value, [row.id]: data }
    ElMessage[data.ok ? 'success' : 'warning'](data.ok ? '探测在线' : `探测离线: ${data.error || ''}`)
  } catch (e) {
    dbgErr('mes.gateway', '立即探测', e)
    ElMessage.error('探测请求失败')
  } finally {
    row._probing = false
  }
}

function openCreate() {
  dbg('mes.gateway', '点击「新建连接」')
  editing.value = null
  form.name = ''
  form.adapter_type = 'rest'
  form.push_events = []
  form.retry_count = 3
  form.retry_interval_sec = 5
  form.retry_backoff = 'fixed'
  form.retry_on_4xx = true
  form.retry_budget_sec = 0
  form.bound_channels = []
  configUrl.value = ''
  configMethod.value = 'POST'
  configFormKey.value = 'param'
  templateJson.value = '{}'
  templateError.value = ''
  requestTimeout.value = 30
  connectTimeout.value = null
  readTimeout.value = null
  successField.value = 'success'
  successExpect.value = true
  staticFields.value = []
  extraFieldsDef.value = []
  authType.value = 'none'
  authUsername.value = ''
  authPassword.value = ''
  authToken.value = ''
  authApiKeyHeader.value = 'X-API-Key'
  authApiKeyValue.value = ''
  authCustomHeaders.value = []
  extraHeaders.value = []
  pushOnResult.value = ['OK', 'NG']
  attachSnapshot.value = false
  snapshotMaxBytes.value = 0
  snapshotDataUri.value = false
  snapshotQuality.value = 0
  snapshotQualityLadder.value = ''
  snapshotScaleFactor.value = 0.75
  snapshotScaleRounds.value = 4
  snapshotMinEdge.value = 32
  snapshotScaledQuality.value = 35
  healthProbeEnabled.value = false
  healthProbeInterval.value = 60
  healthProbeUrl.value = ''
  healthProbeMethod.value = 'GET'
  healthProbeTimeout.value = 5
  healthProbeExpect.value = null
  labelMappings.value = []
  labelMappingMode.value = 'replace'
  modbusTransport.value = 'rtu'
  modbusPort.value = ''
  modbusBaudrate.value = 9600
  modbusDataBits.value = 8
  modbusSlaveId.value = 1
  modbusParity.value = 'N'
  modbusStopBits.value = 1
  modbusByteOrder.value = 'big'
  modbusTimeout.value = 3
  modbusOkValue.value = 1
  modbusNgValue.value = 2
  modbusHost.value = ''
  modbusTcpPort.value = 502
  modbusRegisters.value = [
    { address: 40001, source: 'result_code', data_type: 'uint16' },
    { address: 40002, source: 'total_count', data_type: 'uint16' },
  ]
  dbType.value = 'dm'
  dbHost.value = ''
  dbPort.value = DB_DEFAULT_PORTS.dm
  dbUser.value = ''
  dbPassword.value = ''
  dbDatabase.value = ''
  dbTable.value = ''
  showEditor.value = true
}

function openEdit(row) {
  dbg('mes.gateway', '点击「编辑连接」', `id=${row?.id} name=${row?.name || ''} adapter=${row?.adapter_type || ''}`)
  editing.value = row
  form.name = row.name
  form.adapter_type = row.adapter_type
  form.push_events = [...(row.push_events || [])]
  form.retry_count = row.retry_count
  form.retry_interval_sec = row.retry_interval_sec
  form.bound_channels = [...(row.bound_channels || [])]

  const cfg = row.config || {}
  form.retry_backoff = cfg.retry_backoff || 'fixed'
  form.retry_on_4xx = cfg.retry_on_4xx !== false
  form.retry_budget_sec = cfg.retry_budget_sec ?? 0
  requestTimeout.value = cfg.timeout ?? 30
  connectTimeout.value = cfg.connect_timeout ?? null
  readTimeout.value = cfg.read_timeout ?? null
  configUrl.value = cfg.url || ''
  configMethod.value = cfg.method || 'POST'
  configFormKey.value = cfg.form_key || 'param'
  successField.value = cfg.success_check?.field || 'success'
  successExpect.value = cfg.success_check?.expect ?? true

  const sf = cfg.static_fields || {}
  staticFields.value = Object.entries(sf).map(([key, value]) => ({ key, value: String(value) }))

  const tpl = cfg.template || {}
  templateJson.value = JSON.stringify(tpl, null, 2)
  templateError.value = ''

  const auth = cfg.auth || { type: 'none' }
  authType.value = auth.type || 'none'
  authUsername.value = auth.username || ''
  authPassword.value = auth.password || ''
  authToken.value = auth.token || ''
  authApiKeyHeader.value = auth.header || 'X-API-Key'
  authApiKeyValue.value = auth.value || ''
  authCustomHeaders.value = Array.isArray(auth.headers)
    ? auth.headers.map(h => ({ ...h }))
    : Object.entries(auth.headers || {}).map(([key, value]) => ({ key, value: String(value) }))

  const ch = cfg.custom_headers
  extraHeaders.value = Array.isArray(ch)
    ? ch.map(h => ({ ...h }))
    : Object.entries(ch || {}).map(([key, value]) => ({ key, value: String(value) }))

  pushOnResult.value = Array.isArray(cfg.push_on_result) && cfg.push_on_result.length
    ? [...cfg.push_on_result]
    : ['OK', 'NG']

  attachSnapshot.value = !!cfg.attach_snapshot
  snapshotMaxBytes.value = cfg.snapshot_max_bytes || 0
  snapshotDataUri.value = !!cfg.snapshot_data_uri
  snapshotQuality.value = cfg.snapshot_quality || 0
  snapshotQualityLadder.value = Array.isArray(cfg.snapshot_quality_ladder)
    ? cfg.snapshot_quality_ladder.join(',') : ''
  snapshotScaleFactor.value = cfg.snapshot_scale_factor != null ? cfg.snapshot_scale_factor : 0.75
  snapshotScaleRounds.value = cfg.snapshot_scale_rounds != null ? cfg.snapshot_scale_rounds : 4
  snapshotMinEdge.value = cfg.snapshot_min_edge != null ? cfg.snapshot_min_edge : 32
  snapshotScaledQuality.value = cfg.snapshot_scaled_quality != null ? cfg.snapshot_scaled_quality : 35
  healthProbeEnabled.value = !!cfg.health_probe_enabled
  healthProbeInterval.value = cfg.health_probe_interval_sec || 60
  healthProbeUrl.value = cfg.health_probe_url || ''
  healthProbeMethod.value = cfg.health_probe_method || 'GET'
  healthProbeTimeout.value = cfg.health_probe_timeout_sec || 5
  healthProbeExpect.value = cfg.health_probe_expect_status != null ? cfg.health_probe_expect_status : null

  const lm = cfg.label_mapping || {}
  labelMappings.value = Object.entries(lm).map(([key, value]) => ({ key, value: String(value) }))
  labelMappingMode.value = cfg.label_mapping_mode || 'replace'

  extraFieldsDef.value = (row.extra_fields_schema || []).map(f => ({ ...f }))

  modbusTransport.value = cfg.transport || 'rtu'
  modbusPort.value = cfg.port || ''
  modbusBaudrate.value = cfg.baudrate || 9600
  modbusDataBits.value = cfg.data_bits || 8
  modbusSlaveId.value = cfg.slave_id || 1
  modbusParity.value = cfg.parity || 'N'
  modbusStopBits.value = cfg.stop_bits || 1
  modbusByteOrder.value = cfg.byte_order || 'big'
  modbusTimeout.value = cfg.timeout || 3
  modbusOkValue.value = cfg.ok_value ?? 1
  modbusNgValue.value = cfg.ng_value ?? 2
  modbusHost.value = cfg.host || ''
  modbusTcpPort.value = cfg.tcp_port || 502
  modbusRegisters.value = (cfg.registers || []).map(r => ({ ...r }))
  if (!modbusRegisters.value.length && row.adapter_type === 'modbus_rtu') {
    modbusRegisters.value = [
      { address: 40001, source: 'result_code', data_type: 'uint16' },
    ]
  }

  // v3.35 数据库直写
  dbType.value = cfg.db_type || 'dm'
  dbHost.value = cfg.host || ''
  dbPort.value = cfg.db_port ?? (DB_DEFAULT_PORTS[dbType.value] || 0)
  dbUser.value = cfg.user || ''
  dbPassword.value = cfg.password || ''
  dbDatabase.value = cfg.database || ''
  dbTable.value = cfg.table || ''

  showEditor.value = true
}

// 解析"60,45,30"为合法质量数组(1~100); 空/全非法返回 undefined(后端用默认阶梯)
function parseQualityLadder(raw) {
  if (!raw || !String(raw).trim()) return undefined
  const arr = String(raw).split(',')
    .map(s => parseInt(String(s).trim(), 10))
    .filter(n => Number.isInteger(n) && n >= 1 && n <= 100)
  return arr.length ? arr : undefined
}

function buildConfig() {
  if (form.adapter_type === 'modbus_rtu') {
    if (modbusTransport.value === 'rtu' && !modbusPort.value.trim()) {
      ElMessage.warning('请输入串口路径')
      return null
    }
    if (modbusTransport.value === 'tcp' && !modbusHost.value.trim()) {
      ElMessage.warning('请输入 Modbus TCP 主机地址')
      return null
    }
    return {
      transport: modbusTransport.value,
      port: modbusPort.value.trim(),
      baudrate: modbusBaudrate.value,
      data_bits: modbusDataBits.value,
      slave_id: modbusSlaveId.value,
      parity: modbusParity.value,
      stop_bits: modbusStopBits.value,
      byte_order: modbusByteOrder.value,
      timeout: modbusTimeout.value,
      ok_value: modbusOkValue.value,
      ng_value: modbusNgValue.value,
      host: modbusHost.value.trim(),
      tcp_port: modbusTcpPort.value,
      registers: modbusRegisters.value.filter(r => r.address && r.source),
    }
  }

  let template = {}
  try {
    template = JSON.parse(templateJson.value || '{}')
    templateError.value = ''
  } catch (e) {
    templateError.value = 'JSON 格式错误: ' + e.message
    return null
  }

  const sf = {}
  for (const item of staticFields.value) {
    if (item.key.trim()) sf[item.key.trim()] = item.value
  }

  const headers = {}
  for (const item of extraHeaders.value) {
    if (item.key && item.key.trim()) headers[item.key.trim()] = String(item.value ?? '')
  }

  let auth = { type: 'none' }
  if (authType.value === 'basic') {
    auth = { type: 'basic', username: authUsername.value, password: authPassword.value }
  } else if (authType.value === 'bearer') {
    auth = { type: 'bearer', token: authToken.value }
  } else if (authType.value === 'api_key') {
    auth = {
      type: 'api_key',
      header: authApiKeyHeader.value || 'X-API-Key',
      value: authApiKeyValue.value,
    }
  } else if (authType.value === 'custom_header') {
    auth = {
      type: 'custom_header',
      headers: authCustomHeaders.value
        .filter(h => h.key && h.key.trim())
        .map(h => ({ key: h.key.trim(), value: String(h.value ?? '') })),
    }
  }

  const label_mapping = {}
  for (const item of labelMappings.value) {
    if (item.key && item.key.trim()) label_mapping[item.key.trim()] = String(item.value ?? '')
  }

  // v3.35 数据库直写: 复用模板/静态字段/物料映射/按结果过滤, 不带 HTTP 语义字段
  if (form.adapter_type === 'database') {
    if (!dbTable.value.trim()) {
      ElMessage.warning('请输入目标表名')
      return null
    }
    if (dbType.value !== 'sqlite' && !dbHost.value.trim()) {
      ElMessage.warning('请输入数据库主机地址')
      return null
    }
    return {
      db_type: dbType.value,
      host: dbHost.value.trim(),
      db_port: dbPort.value || undefined,
      user: dbUser.value,
      password: dbPassword.value,
      database: dbDatabase.value.trim(),
      table: dbTable.value.trim(),
      timeout: requestTimeout.value || 10,
      push_on_result: pushOnResult.value.length && pushOnResult.value.length < 2
        ? [...pushOnResult.value]
        : null,
      attach_snapshot: attachSnapshot.value,
      snapshot_max_bytes: attachSnapshot.value ? (snapshotMaxBytes.value || 0) : 0,
      snapshot_data_uri: attachSnapshot.value ? snapshotDataUri.value : false,
      label_mapping: Object.keys(label_mapping).length ? label_mapping : null,
      label_mapping_mode: labelMappingMode.value,
      static_fields: sf,
      template,
    }
  }

  return {
    url: configUrl.value,
    method: configMethod.value,
    content_type: ({
      'rest': 'application/json',
      'form-data': 'multipart/form-data',
      'form-urlencoded': 'application/x-www-form-urlencoded',
      'query-string': 'url-query',
    })[form.adapter_type] || 'application/json',
    form_key: configFormKey.value,
    headers,
    auth,
    push_on_result: pushOnResult.value.length && pushOnResult.value.length < 2
      ? [...pushOnResult.value]
      : null,
    attach_snapshot: attachSnapshot.value,
    snapshot_max_bytes: attachSnapshot.value ? (snapshotMaxBytes.value || 0) : 0,
    snapshot_data_uri: attachSnapshot.value ? snapshotDataUri.value : false,
    snapshot_quality: attachSnapshot.value && snapshotQuality.value ? snapshotQuality.value : undefined,
    snapshot_quality_ladder: attachSnapshot.value ? parseQualityLadder(snapshotQualityLadder.value) : undefined,
    snapshot_scale_factor: attachSnapshot.value ? snapshotScaleFactor.value : undefined,
    snapshot_scale_rounds: attachSnapshot.value ? snapshotScaleRounds.value : undefined,
    snapshot_min_edge: attachSnapshot.value ? snapshotMinEdge.value : undefined,
    snapshot_scaled_quality: attachSnapshot.value ? snapshotScaledQuality.value : undefined,
    health_probe_enabled: healthProbeEnabled.value,
    health_probe_interval_sec: healthProbeEnabled.value ? (healthProbeInterval.value || 60) : undefined,
    health_probe_url: healthProbeEnabled.value && healthProbeUrl.value ? healthProbeUrl.value : undefined,
    health_probe_method: healthProbeEnabled.value ? (healthProbeMethod.value || 'GET') : undefined,
    health_probe_timeout_sec: healthProbeEnabled.value ? (healthProbeTimeout.value || 5) : undefined,
    health_probe_expect_status: healthProbeEnabled.value && healthProbeExpect.value ? healthProbeExpect.value : undefined,
    retry_backoff: form.retry_backoff || 'fixed',
    retry_on_4xx: form.retry_on_4xx !== false,
    retry_budget_sec: form.retry_budget_sec || 0,
    timeout: requestTimeout.value || 30,
    connect_timeout: connectTimeout.value != null ? connectTimeout.value : undefined,
    read_timeout: readTimeout.value != null ? readTimeout.value : undefined,
    label_mapping: Object.keys(label_mapping).length ? label_mapping : null,
    label_mapping_mode: labelMappingMode.value,
    static_fields: sf,
    template,
    success_check: {
      field: successField.value,
      expect: successExpect.value,
    },
  }
}

async function doSave() {
  dbg('mes.gateway', editing.value ? '点击「保存编辑连接」' : '点击「保存新建连接」', `id=${editing.value?.id ?? ''} name=${form?.name || ''} adapter=${form?.adapter_type || ''}`)
  if (!form.name.trim()) {
    ElMessage.warning('请输入连接名称')
    return
  }
  const config = buildConfig()
  if (!config) return

  const efSchema = extraFieldsDef.value.filter(f => f.key?.trim())
  saving.value = true
  try {
    const payload = {
      name: form.name,
      adapter_type: form.adapter_type,
      config,
      push_events: form.push_events,
      retry_count: form.retry_count,
      retry_interval_sec: form.retry_interval_sec,
      extra_fields_schema: efSchema.length ? efSchema : null,
      bound_channels: form.bound_channels.length ? form.bound_channels : null,
    }
    if (editing.value) {
      await updateConnection(editing.value.id, payload)
      ElMessage.success('已更新')
    } else {
      await createConnection(payload)
      ElMessage.success('已创建')
    }
    showEditor.value = false
    loadConnections()
  } catch (e) {
    dbgErr('mes.gateway', '保存连接', e)
    ElMessage.error('保存失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    saving.value = false
  }
}

async function toggleEnabled(row) {
  dbg('mes.gateway', '切换「连接启停」', `id=${row?.id} name=${row?.name || ''} enabled=${row?.enabled}`)
  try {
    await updateConnection(row.id, { enabled: row.enabled })
    ElMessage.success(row.enabled ? '已启用' : '已禁用')
  } catch (e) {
    dbgErr('mes.gateway', '切换连接启停', e)
    row.enabled = !row.enabled
    ElMessage.error('操作失败')
  }
}

async function doDelete(row) {
  dbg('mes.gateway', '点击「删除连接」', `id=${row?.id} name=${row?.name || ''}`)
  try {
    await deleteConnection(row.id)
    ElMessage.success('已删除')
    loadConnections()
  } catch (e) {
    dbgErr('mes.gateway', '删除连接', e)
    ElMessage.error('删除失败')
  }
}

async function doTest(row) {
  dbg('mes.gateway', '点击「测试推送」', `id=${row?.id} name=${row?.name || ''} event=${testEventType.value || row?.push_events?.[0] || 'cycle_end'}`)
  row._testing = true
  try {
    // 按 push_events 里第一个事件选测试 context, 默认 cycle_end
    const evt = testEventType.value
      || (row.push_events && row.push_events[0])
      || 'cycle_end'
    const { data } = await testConnection(row.id, { event_type: evt })
    testResult.value = data
    showTestResult.value = true
  } catch (e) {
    dbgErr('mes.gateway', '测试推送', e)
    ElMessage.error('测试请求失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    row._testing = false
  }
}

function openLogs(row) {
  logConnId.value = row.id
  logFilter.success = null
  logPage.value = 1
  showLogs.value = true
  loadLogs()
}

function openAllLogs() {
  logConnId.value = null
  logFilter.success = null
  logPage.value = 1
  showLogs.value = true
  loadLogs()
}

async function loadLogs() {
  try {
    const pageSize = pollingStore.logLimit('gateway', 20)
    const params = {
      skip: (logPage.value - 1) * pageSize,
      limit: pageSize,
    }
    if (logConnId.value) params.connection_id = logConnId.value
    if (logFilter.success !== null && logFilter.success !== '') {
      params.success = logFilter.success
    }
    const { data } = await getGatewayLogs(params)
    logs.value = data.items || []
    logTotal.value = data.total || 0
  } catch {
    ElMessage.error('加载日志失败')
  }
}

function showLogDetail(row) {
  detailLog.value = row
  showDetail.value = true
}

let _healthTimer = null
onMounted(async () => {
  await pollingStore.load()
  loadConnections()
  loadChannelCount()
  loadHealthStatus()
  _healthTimer = setInterval(loadHealthStatus, pollingStore.get('gateway_health', 15000))
})

onBeforeUnmount(() => {
  if (_healthTimer) { clearInterval(_healthTimer); _healthTimer = null }
})
</script>

<style scoped>
.template-editor :deep(.el-textarea__inner) {
  font-family: 'Fira Code', 'Consolas', monospace;
  font-size: 12px;
  line-height: 1.5;
  background: #1a1a2e;
  color: #e0e0e0;
}
</style>
