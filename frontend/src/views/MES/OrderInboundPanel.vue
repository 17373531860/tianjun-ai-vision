<template>
  <div>
    <div class="text-xs text-gray-500 mb-3">
      外部生产管控系统主动把「开工/完工」任务 POST 推过来，本机接收后做字段映射、校验、切项目/建工单，并回标准响应。
      与「外部对接」(我们推出去) 、「工单拉取」(我们主动查) 方向互补。配置全局共用，不随项目切换。
    </div>

    <!-- 工具栏 -->
    <div class="flex items-center justify-between mb-4">
      <div class="flex items-center gap-3">
        <span class="text-sm text-gray-300">入站接收</span>
        <el-switch v-model="form.enabled" size="small" />
        <el-tag size="small" :type="form.enabled ? 'success' : 'info'">
          {{ form.enabled ? '已启用' : '已停用' }}
        </el-tag>
      </div>
      <div class="flex gap-2">
        <el-button size="small" @click="load" :loading="loading">重新加载</el-button>
        <el-button size="small" type="success" @click="save" :loading="saving">保存配置</el-button>
      </div>
    </div>

    <!-- 接收地址 -->
    <div class="mb-4 p-3 rounded bg-slate-800/50 text-xs">
      <div class="text-gray-400 mb-1">外部系统把任务 POST 到本机此地址（无需鉴权，工控内网 M2M）：</div>
      <code class="text-cyan-300 break-all">开工/完工  POST  {{ endpointUrl }}</code>
      <div class="text-gray-400 mt-2 mb-1">外部系统消除报警时 POST 到（按下方"消除匹配字段"匹配在途报警）：</div>
      <code class="text-cyan-300 break-all">报警消除  POST  {{ alarmClearUrl }}</code>
    </div>

    <el-form :model="form" label-width="130px" size="small">
      <!-- ========== 字段映射 ========== -->
      <el-divider content-position="left"><span class="text-cyan-300 text-xs">字段映射（我们字段 ← 外部字段）</span></el-divider>
      <div class="text-xs text-gray-500 mb-2 ml-2">
        外部字段支持点路径取嵌套值，如 <code>data.order.no</code>。常用我们字段：
        <code>task_no</code>(任务/工单号) <code>product_code</code>(产品代号) <code>step_code</code>(工序)
        <code>operator</code>(操作员) <code>begin_time</code>(开工时间) <code>is_complete</code>(完工信号)
      </div>
      <div class="flex flex-col gap-2 mb-2">
        <div v-for="(row, i) in form.fieldRows" :key="i" class="flex items-center gap-2">
          <el-input v-model="row.k" placeholder="我们字段(如 task_no)" class="w-52" />
          <span class="text-gray-500">←</span>
          <el-input v-model="row.v" placeholder="外部字段路径(如 TaskNo)" class="w-72" />
          <el-button size="small" type="danger" plain @click="form.fieldRows.splice(i, 1)">删</el-button>
        </div>
      </div>
      <el-button size="small" plain @click="form.fieldRows.push({ k: '', v: '' })">+ 加一行映射</el-button>

      <el-form-item label="必填字段" class="mt-3">
        <el-select v-model="form.required" multiple filterable allow-create default-first-option
          placeholder="缺这些字段直接回失败" class="w-full">
          <el-option v-for="k in ourKeys" :key="k" :label="k" :value="k" />
        </el-select>
      </el-form-item>

      <!-- ========== 处理动作 (全可选) ========== -->
      <el-divider content-position="left"><span class="text-cyan-300 text-xs">收到任务后做什么（按需勾选）</span></el-divider>

      <el-form-item label="按产品码切项目">
        <el-switch v-model="form.switch_project_on_task" />
        <span class="text-xs text-gray-500 ml-2">开工时按产品代号自动激活对应检测项目（会重载模型，敏感动作，默认关）</span>
      </el-form-item>
      <el-form-item v-if="form.switch_project_on_task" label="按项目名自动匹配">
        <el-switch v-model="form.match_project_by_name" />
        <span class="text-xs text-gray-500 ml-2">下方对照表无匹配时，自动按「检测项目名 == 产品代号」匹配（项目直接命名为产品代号即可零配置，默认开）</span>
      </el-form-item>
      <el-form-item v-if="form.switch_project_on_task" label="产品代号 → 项目">
        <div class="flex flex-col gap-2 w-full">
          <div v-for="(row, i) in form.mapRows" :key="i" class="flex items-center gap-2">
            <el-input v-model="row.code" placeholder="产品代号(外部传来的)" class="w-52" />
            <span class="text-gray-500">→</span>
            <el-select v-model="row.project_id" filterable placeholder="选检测项目" class="w-60">
              <el-option v-for="p in projects" :key="p.id" :label="`#${p.id} ${p.name}`" :value="p.id" />
            </el-select>
            <el-button size="small" type="danger" plain @click="form.mapRows.splice(i, 1)">删</el-button>
          </div>
          <el-button size="small" plain @click="form.mapRows.push({ code: '', project_id: null })">+ 加一行对照</el-button>
        </div>
      </el-form-item>

      <el-form-item label="开工即建工单">
        <el-switch v-model="form.create_work_order_on_task" />
        <span class="text-xs text-gray-500 ml-2">用任务号建/激活工单(in_progress)，让检测周期绑该任务、出站报文自带工单号</span>
      </el-form-item>
      <el-form-item v-if="form.create_work_order_on_task" label="工单绑定方式">
        <el-radio-group v-model="form.order_binding">
          <el-radio value="project">绑当前激活项目(默认)</el-radio>
          <el-radio value="channel">按工位路由</el-radio>
        </el-radio-group>
        <template v-if="form.order_binding === 'channel'">
          <span class="text-xs text-gray-400 mx-2">工位号取字段</span>
          <el-select v-model="form.channel_field" filterable class="w-40">
            <el-option v-for="k in ourKeys" :key="k" :label="k" :value="k" />
          </el-select>
        </template>
      </el-form-item>
      <el-form-item label="拒绝重复任务">
        <el-switch v-model="form.reject_duplicate_task" />
        <span class="text-xs text-gray-500 ml-2">同任务号已在产时回"重复任务"码拒收（与"最新开工为准"相反，二选一）</span>
      </el-form-item>

      <el-form-item label="最新开工为准">
        <el-switch v-model="form.supersede_previous_task" />
        <span class="text-xs text-gray-500 ml-2">收到新开工直接顶替(收尾)当前在产的旧任务，新任务独占</span>
      </el-form-item>
      <el-form-item v-if="form.supersede_previous_task" label="顶替范围">
        <el-select v-model="form.supersede_scope" class="w-72">
          <el-option label="仅项目绑定的外部在产单 (默认)" value="project" />
          <el-option label="仅与本任务同一项目" value="same_project" />
          <el-option label="仅与本任务同一工位" value="same_channel" />
          <el-option label="所有外部在产单 (含工位/集群绑定)" value="external" />
        </el-select>
      </el-form-item>
      <el-form-item v-if="form.supersede_previous_task" label="顶替即回传完工">
        <el-switch v-model="form.report_complete_on_supersede" />
        <span class="text-xs text-gray-400 ml-2">完工出站事件名</span>
        <el-input v-model="form.complete_event_type" placeholder="task_complete" class="w-44 ml-2" />
        <span class="text-xs text-gray-500 ml-2">出站连接 push_events 订阅此名即推完工报文</span>
      </el-form-item>

      <el-form-item label="完工信号字段">
        <el-select v-model="form.complete_field" clearable filterable placeholder="留空=不识别完工信号" class="w-60">
          <el-option v-for="k in ourKeys" :key="k" :label="k" :value="k" />
        </el-select>
        <span class="text-xs text-gray-500 ml-2">该字段为真时，本次按"完工"收工单（不当开工处理）</span>
      </el-form-item>
      <el-form-item v-if="form.complete_field" label="完工真值词表">
        <el-select v-model="form.complete_true_words" multiple filterable allow-create default-first-option
          placeholder="留空=默认(1/true/yes/是/完工/complete...)" class="w-full">
          <el-option v-for="w in trueWordOptions" :key="w" :label="w" :value="w" />
        </el-select>
        <span class="text-xs text-gray-500 ml-2">完工字段值命中其一即视为完工（大小写不敏感，布尔真/非零数字恒为真）；留空用内置默认</span>
      </el-form-item>
      <el-form-item v-if="form.complete_field" label="完工匹配方式">
        <el-radio-group v-model="form.complete_match_column">
          <el-radio value="order_no">按工单号(任务号)</el-radio>
          <el-radio value="product_code">按产品码(取该产品最新在产单)</el-radio>
        </el-radio-group>
        <span class="text-xs text-gray-400 mx-2">取字段</span>
        <el-select v-model="form.complete_match_field" filterable class="w-44">
          <el-option v-for="k in ourKeys" :key="k" :label="k" :value="k" />
        </el-select>
      </el-form-item>

      <!-- ========== 响应码 ========== -->
      <el-divider content-position="left"><span class="text-cyan-300 text-xs">回给外部系统的响应（按客户协议定）</span></el-divider>
      <el-form-item label="响应体格式">
        <el-radio-group v-model="form.response.format">
          <el-radio value="json">JSON(默认)</el-radio>
          <el-radio value="xml">XML / SOAP</el-radio>
        </el-radio-group>
        <template v-if="form.response.format === 'xml'">
          <span class="text-xs text-gray-400 mx-2">根标签</span>
          <el-input v-model="form.response.xml_root" placeholder="response" class="w-40" />
          <el-checkbox v-model="form.response.xml_declaration" class="ml-2">带 &lt;?xml?&gt; 头</el-checkbox>
        </template>
      </el-form-item>
      <el-form-item label="业务码字段">
        <el-input v-model="form.response.code_field" placeholder="code" class="w-40" />
        <span class="text-xs text-gray-400 ml-3">消息字段</span>
        <el-input v-model="form.response.message_field" placeholder="message" class="w-40 ml-2" />
      </el-form-item>
      <el-form-item label="成功码 / 成功消息">
        <el-input v-model="form.response.success_code" placeholder="0" class="w-32" />
        <el-input v-model="form.response.success_message" placeholder="OK" class="w-60 ml-2" />
      </el-form-item>
      <el-form-item label="各类失败码">
        <div class="grid grid-cols-2 gap-2 w-full">
          <div v-for="c in codeRows" :key="c.key" class="flex items-center gap-2">
            <span class="text-xs text-gray-300 w-32 text-right">{{ c.label }}</span>
            <el-input v-model="form.response.codes[c.key]" :placeholder="c.ph" class="w-32" />
          </div>
        </div>
      </el-form-item>
      <el-form-item label="各类文案覆盖">
        <el-collapse class="w-full">
          <el-collapse-item title="自定义各结果响应文案 (留空=用内置默认，含动态内容如缺失字段名)">
            <div class="grid grid-cols-1 gap-2 w-full">
              <div v-for="c in statusRows" :key="c.key" class="flex items-center gap-2">
                <span class="text-xs text-gray-300 w-24 text-right">{{ c.label }}</span>
                <el-input v-model="form.response.messages[c.key]" placeholder="留空=默认文案" class="w-72" />
              </div>
            </div>
          </el-collapse-item>
        </el-collapse>
      </el-form-item>
      <el-form-item label="原样回显字段">
        <el-select v-model="form.echo_fields" multiple filterable allow-create default-first-option
          placeholder="把哪些已映射字段原样塞回响应(如 task_no)" class="w-full">
          <el-option v-for="k in ourKeys" :key="k" :label="k" :value="k" />
        </el-select>
      </el-form-item>
      <el-form-item label="业务码字段(进阶)">
        <span class="text-xs text-gray-500">上面"业务码字段"支持点路径，如 <code>head.code</code> → 嵌套 <code>{ head:{ code:0 } }</code></span>
      </el-form-item>
      <el-form-item label="自定义响应模板">
        <el-input v-model="form.response_template_text" type="textarea" :rows="4"
          placeholder='留空=用上面的码字段平铺。填 JSON 可全自定义信封(兼容 SOAP/复杂协议)，占位符 {code} {message} {task_no} 等' />
        <div class="text-xs text-gray-500 mt-1">
          例：<code>{"resultCode":"{code}","resultMsg":"{message}","data":{"taskNo":"{task_no}"}}</code>
          。填了模板则忽略上面的码字段平铺。
        </div>
      </el-form-item>

      <!-- ========== 报警闭环 ========== -->
      <el-divider content-position="left"><span class="text-cyan-300 text-xs">报警闭环（报警上报登记台账 ↔ 外部消除）</span></el-divider>
      <div class="text-xs text-gray-500 mb-2 ml-2">
        勾选哪些"出站事件"算报警 → 成功推给外部系统后登记一条"在途报警"；外部回推消除命令即撤、监控页横幅同步消失。
        留空 = 不登记任何台账（完全关闭，零开销）。报警上报报文本身在「外部对接」连接里配（订阅同名事件 + 勾选附带截图）。
      </div>
      <el-form-item label="哪些事件算报警">
        <el-select v-model="form.alarm_event_name" multiple filterable allow-create default-first-option
          placeholder="留空=关闭报警闭环；常用 cycle_end(每周期判定)" class="w-full">
          <el-option v-for="e in alarmEventOptions" :key="e" :label="e" :value="e" />
        </el-select>
      </el-form-item>
      <el-form-item v-if="form.alarm_event_name.length" label="只登记哪些结果">
        <el-select v-model="form.alarm_record_on_results" multiple placeholder="默认只登 NG（避免 OK 周期误当报警）" class="w-60">
          <el-option label="NG（不良）" value="NG" />
          <el-option label="OK（合格）" value="OK" />
        </el-select>
        <span class="text-xs text-gray-500 ml-2">清空 = 不过滤，任何结果都登记</span>
      </el-form-item>
      <el-form-item v-if="form.alarm_event_name.length" label="去重窗口(秒)">
        <el-input-number v-model="form.alarm_dedup_sec" :min="0" :step="1" class="w-36" />
        <span class="text-xs text-gray-500 ml-2">同一唯一键报警在该窗口内只登记一次；0 = 不去重</span>
      </el-form-item>
      <el-form-item v-if="form.alarm_event_name.length" label="消除匹配字段">
        <el-select v-model="form.alarm_clear_match_fields" multiple filterable allow-create default-first-option
          placeholder="外部消除命令按这几个字段匹配在途报警" class="w-full">
          <el-option v-for="k in alarmKeyOptions" :key="k" :label="k" :value="k" />
        </el-select>
        <span class="text-xs text-gray-500 ml-2">川南：任务号_产品号_工序工步_操作员。可直接输入自定义维度（如 batch_no），需在下方台账映射里同名映射一份</span>
      </el-form-item>
      <el-form-item v-if="form.alarm_event_name.length" label="台账字段映射">
        <div class="flex flex-col gap-2 w-full">
          <div class="text-xs text-gray-500">台账字段 ← 出站上下文点路径（默认对齐 cycle_end：任务/产品取激活工单，工步/操作员取工单留痕，原因取 NG 原因）。除 5 个原生字段外，可输入任意自定义维度（如 batch_no/line_id），自动存入扩展位并参与去重/消除</div>
          <div v-for="(row, i) in form.alarmMapRows" :key="i" class="flex items-center gap-2">
            <el-select v-model="row.k" filterable allow-create default-first-option placeholder="台账字段" class="w-44">
              <el-option v-for="k in alarmKeyOptions" :key="k" :label="k" :value="k" />
            </el-select>
            <span class="text-gray-500">←</span>
            <el-input v-model="row.v" placeholder="上下文路径(如 order.order_no)" class="w-72" />
            <el-button size="small" type="danger" plain @click="form.alarmMapRows.splice(i, 1)">删</el-button>
          </div>
          <el-button size="small" plain @click="form.alarmMapRows.push({ k: '', v: '' })">+ 加一行映射</el-button>
        </div>
      </el-form-item>
      <el-form-item label="产品码未知话术">
        <el-input v-model="form.unknown_product_message" placeholder="留空=用默认；如：产品代号未在本机维护对应检测项目" class="w-full" />
        <span class="text-xs text-gray-500 ml-2">"按产品码切项目"找不到对应项目时回给外部系统的提示文案</span>
      </el-form-item>

      <!-- ========== 监控页报警横幅 ========== -->
      <el-divider content-position="left"><span class="text-cyan-300 text-xs">监控页报警横幅（在途报警的持续提醒条，外观/行为全可配）</span></el-divider>
      <div class="text-xs text-gray-500 mb-2 ml-2">
        有"在途报警"未被外部消除时，监控页持续显示一条醒目横幅；外部回推消除后自动消失。下面控制它显不显示、停哪、什么颜色、刷新多快、每条显示哪些字段。
      </div>
      <el-form-item label="显示横幅">
        <el-switch v-model="form.alarm_banner.enabled" />
        <span class="text-xs text-gray-500 ml-2">关 = 监控页永不显示横幅（即便有在途报警）</span>
      </el-form-item>
      <el-form-item v-if="form.alarm_banner.enabled" label="停靠位置">
        <el-radio-group v-model="form.alarm_banner.position">
          <el-radio value="top">顶部</el-radio>
          <el-radio value="bottom">底部</el-radio>
        </el-radio-group>
      </el-form-item>
      <el-form-item v-if="form.alarm_banner.enabled" label="主色">
        <el-color-picker v-model="form.alarm_banner.color" />
        <span class="text-xs text-gray-500 ml-2">横幅底色，可换成客户品牌色（默认告警红）</span>
      </el-form-item>
      <el-form-item v-if="form.alarm_banner.enabled" label="刷新间隔(秒)">
        <el-input-number v-model="form.alarm_banner.poll_interval_sec" :min="1" :max="60" :step="1" class="w-36" />
        <span class="text-xs text-gray-500 ml-2">每隔多久向后台查一次在途报警</span>
      </el-form-item>
      <el-form-item v-if="form.alarm_banner.enabled" label="每条显示字段">
        <el-checkbox v-model="form.alarm_banner.show_task_no">任务号</el-checkbox>
        <el-checkbox v-model="form.alarm_banner.show_product_code">产品号</el-checkbox>
        <el-checkbox v-model="form.alarm_banner.show_step_code">工序工步</el-checkbox>
        <el-checkbox v-model="form.alarm_banner.show_operator">操作员</el-checkbox>
        <el-checkbox v-model="form.alarm_banner.show_time">报警时间</el-checkbox>
      </el-form-item>

      <!-- ========== 监控页任务信息条 ========== -->
      <el-divider content-position="left"><span class="text-cyan-300 text-xs">监控页任务信息条（开工后主界面持续显示的任务要素，逐项可选）</span></el-divider>
      <div class="text-xs text-gray-500 mb-2 ml-2">
        开工后，监控页信息条可持续显示当前任务的 任务号 / 产品代号 / 工序工步 / 操作员（取自开工建立的工单）。<span class="text-cyan-300">默认全关 = 维持原界面不追加任何标签</span>，按需逐项打开。需配合"开工时建立工单"开启才有数据来源。
      </div>
      <el-form-item label="持续显示要素">
        <el-checkbox v-model="form.task_info_display.show_task_no">任务号</el-checkbox>
        <el-checkbox v-model="form.task_info_display.show_product_code">产品代号</el-checkbox>
        <el-checkbox v-model="form.task_info_display.show_step_code">工序工步</el-checkbox>
        <el-checkbox v-model="form.task_info_display.show_operator">操作员</el-checkbox>
      </el-form-item>

      <!-- ========== 高级 ========== -->
      <el-collapse v-model="advancedOpen" class="adv-collapse mt-2">
        <el-collapse-item name="adv">
          <template #title><span class="text-xs text-gray-300">展开高级设置</span></template>
          <el-form-item label="入站编码格式">
            <el-select v-model="form.input_format" class="w-52">
              <el-option label="自动识别 (按 Content-Type)" value="auto" />
              <el-option label="JSON" value="json" />
              <el-option label="表单 (urlencoded/multipart)" value="form" />
              <el-option label="URL 参数 (query)" value="query" />
              <el-option label="XML / SOAP" value="xml" />
            </el-select>
            <span class="text-xs text-gray-500 ml-2">对方用什么格式推就选什么；GET 一律按 URL 参数</span>
          </el-form-item>
          <el-form-item label="并入 URL 参数">
            <el-switch v-model="form.merge_query_params" />
            <span class="text-xs text-gray-500 ml-2">把网址 ?后面的参数也并进报文（同名以 body 为准）</span>
          </el-form-item>
          <el-form-item label="失败→HTTP状态码">
            <div class="grid grid-cols-2 gap-2 w-full">
              <div v-for="c in statusRows" :key="c.key" class="flex items-center gap-2">
                <span class="text-xs text-gray-300 w-28 text-right">{{ c.label }}</span>
                <el-input v-model="form.http_status[c.key]" placeholder="200" class="w-24" />
              </div>
            </div>
            <div class="text-xs text-gray-500 mt-1">留空=200(业务码在响应体里)。要求失败回非2xx时填，如缺字段→400、内部错误→500</div>
          </el-form-item>
          <el-form-item label="请求体上限(字节)">
            <el-input-number v-model="form.max_body_bytes" :min="0" :step="1024" class="w-44" />
            <span class="text-xs text-gray-500 ml-2">超限回 413，0 = 不限</span>
          </el-form-item>

          <el-divider content-position="left"><span class="text-gray-400 text-xs">来源校验（默认关=内网无校验）</span></el-divider>
          <el-form-item label="开启来源校验">
            <el-switch v-model="form.auth.enabled" />
            <span class="text-xs text-gray-500 ml-2">要求对方带共享密钥头 / 限定来源 IP</span>
          </el-form-item>
          <el-form-item v-if="form.auth.enabled" label="共享密钥头">
            <el-input v-model="form.auth.header_name" placeholder="X-API-Key" class="w-44" />
            <span class="text-xs text-gray-400 mx-2">值</span>
            <el-input v-model="form.auth.header_value" placeholder="期望的密钥(留空=不校验头)" class="w-60" />
          </el-form-item>
          <el-form-item v-if="form.auth.enabled" label="IP 白名单">
            <el-select v-model="form.auth.ip_whitelist" multiple filterable allow-create default-first-option
              placeholder="留空=不限 IP；支持 CIDR，如 192.168.1.0/24" class="w-full" />
          </el-form-item>
        </el-collapse-item>
      </el-collapse>

      <!-- ========== 自定义接收路径 ========== -->
      <el-divider content-position="left"><span class="text-cyan-300 text-xs">自定义接收路径（除内置地址外，客户可自定义接收 URL）</span></el-divider>
      <div class="text-xs text-gray-500 mb-2 ml-2">
        外部系统若要求按它们的 URL 推送（如 <code>/warning/clear</code>、<code>/task/complete</code>），在此添加。保存即生效，无需重启。路径须以 <code>/</code> 开头、不可用 <code>/api/</code> 前缀。
      </div>
      <el-form-item label="接收路径别名">
        <div class="flex flex-col gap-2 w-full">
          <div v-for="(row, i) in form.receivePathRows" :key="i" class="flex items-center gap-2">
            <el-input v-model="row.path" placeholder="/warning/clear" class="w-56" />
            <span class="text-gray-500">→</span>
            <el-select v-model="row.action" class="w-40">
              <el-option label="开工/完工 (task_start)" value="task_start" />
              <el-option label="报警消除 (alarm_clear)" value="alarm_clear" />
              <el-option label="健康检查 (health)" value="health" />
            </el-select>
            <el-select v-model="row.methods" multiple class="w-44" placeholder="方法">
              <el-option label="POST" value="POST" />
              <el-option label="GET" value="GET" />
            </el-select>
            <el-button size="small" type="danger" plain @click="form.receivePathRows.splice(i, 1)">删</el-button>
          </div>
          <el-button size="small" plain @click="form.receivePathRows.push({ path: '', action: 'task_start', methods: ['POST'] })">+ 加一条路径</el-button>
        </div>
      </el-form-item>
    </el-form>

    <!-- ========== 通讯日志 ========== -->
    <el-divider content-position="left"><span class="text-cyan-300 text-xs">最近入站记录</span></el-divider>
    <div class="flex justify-end mb-2">
      <el-button size="small" @click="loadLogs" :loading="logLoading">刷新日志</el-button>
    </div>
    <el-table :data="logs" stripe size="small" class="mes-table" max-height="320">
      <el-table-column prop="id" label="ID" width="60" />
      <el-table-column label="时间" width="170">
        <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="结果" width="80">
        <template #default="{ row }">
          <el-tag size="small" :type="row.success ? 'success' : 'danger'">{{ row.success ? '成功' : '失败' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="收到内容" min-width="220">
        <template #default="{ row }"><span class="text-xs text-gray-300 break-all">{{ row.request_body }}</span></template>
      </el-table-column>
      <el-table-column label="回复内容" min-width="220">
        <template #default="{ row }"><span class="text-xs text-gray-400 break-all">{{ row.response_body }}</span></template>
      </el-table-column>
    </el-table>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { getInboundConfig, saveInboundConfig, getInboundLogs } from '@/api/gateway'
import { getProjects } from '@/api/project'
import { dbg } from '@/utils/debug'
import { usePollingStore } from '@/store/usePollingStore'

const pollingStore = usePollingStore()

const loading = ref(false)
const saving = ref(false)
const logLoading = ref(false)
const advancedOpen = ref([])
const projects = ref([])
const logs = ref([])

const codeRows = [
  { key: 'bad_request', label: '请求格式错', ph: '40005' },
  { key: 'missing_field', label: '缺必填字段', ph: '40004' },
  { key: 'duplicate', label: '重复任务', ph: '40001' },
  { key: 'unknown_product', label: '产品码未知', ph: '40002' },
  { key: 'activate_failed', label: '切项目失败', ph: '40003' },
  { key: 'internal_error', label: '内部错误', ph: '40006' },
  { key: 'disabled', label: '功能未启用', ph: '40006' },
]

const statusRows = [{ key: 'success', label: '成功' }, ...codeRows]

function emptyForm() {
  return {
    enabled: false,
    fieldRows: [],
    required: [],
    response: {
      code_field: 'code', message_field: 'message',
      success_code: '0', success_message: 'OK',
      codes: {},
      messages: {},
      format: 'json', xml_root: 'response', xml_declaration: true,
    },
    response_template_text: '',
    http_status: {},
    auth: { enabled: false, header_name: 'X-API-Key', header_value: '', ip_whitelist: [] },
    receivePathRows: [],
    max_body_bytes: 1048576,
    input_format: 'auto',
    merge_query_params: true,
    echo_fields: [],
    switch_project_on_task: false,
    match_project_by_name: true,
    mapRows: [],
    create_work_order_on_task: false,
    order_binding: 'project',
    channel_field: 'channel',
    reject_duplicate_task: false,
    complete_field: '',
    complete_true_words: [],
    complete_match_field: 'task_no',
    complete_match_column: 'order_no',
    supersede_previous_task: false,
    supersede_scope: 'project',
    report_complete_on_supersede: true,
    complete_event_type: 'task_complete',
    // 报警闭环
    alarm_event_name: [],
    alarm_record_on_results: ['NG'],
    alarm_dedup_sec: 5,
    alarm_clear_match_fields: ['task_no', 'product_code', 'step_code', 'operator'],
    alarmMapRows: [],
    unknown_product_message: '',
    // 监控页报警横幅外观/行为 (前端 ExternalAlarmBanner 读取)
    alarm_banner: {
      enabled: true,
      position: 'top',
      color: '#dc2626',
      poll_interval_sec: 3,
      show_task_no: true,
      show_product_code: true,
      show_step_code: true,
      show_operator: true,
      show_time: true,
    },
    // 监控页任务信息条逐要素显示 (前端 Monitor 读取); 默认全关 = 原界面
    task_info_display: {
      show_task_no: false,
      show_product_code: false,
      show_step_code: false,
      show_operator: false,
    },
  }
}

// 可当报警源的出站事件 (网关 dispatch 的所有事件名)
const alarmEventOptions = ['cycle_end', 'session_end', 'box_complete', 'box_timeout', 'weight_no_barcode']
// 在途报警台账承载/匹配的字段
const alarmKeyOptions = ['task_no', 'product_code', 'step_code', 'operator', 'warning_text']
const trueWordOptions = ['1', 'true', 'yes', 'y', 't', '是', '完工', 'complete', 'completed']

const form = reactive(emptyForm())

// 接收地址 (展示用)
const apiBase = computed(() =>
  (import.meta.env.VITE_API_BASE_URL || 'http://<本机IP>:8001/api/v1').replace(/\/+$/, ''))
const endpointUrl = computed(() => `${apiBase.value}/mes/inbound/task`)
const alarmClearUrl = computed(() => `${apiBase.value}/mes/inbound/alarm/clear`)

// 当前已配的"我们字段"名 (供必填/完工/回显下拉)
const ourKeys = computed(() => form.fieldRows.map(r => r.k).filter(Boolean))

function formatTime(t) {
  if (!t) return '-'
  try { return new Date(t).toLocaleString('zh-CN') } catch { return t }
}

function applyConfig(cfg) {
  const f = emptyForm()
  f.enabled = !!cfg.enabled
  f.fieldRows = Object.entries(cfg.field_map || {}).map(([k, v]) => ({ k, v }))
  f.required = Array.isArray(cfg.required_fields) ? [...cfg.required_fields] : []
  const r = cfg.response || {}
  f.response = {
    code_field: r.code_field || 'code',
    message_field: r.message_field || 'message',
    success_code: String(r.success_code ?? '0'),
    success_message: r.success_message || 'OK',
    codes: { ...(r.codes || {}) },
    messages: { ...(r.messages || {}) },
    format: r.format || 'json',
    xml_root: r.xml_root || 'response',
    xml_declaration: r.xml_declaration !== false,
  }
  f.response_template_text = r.template ? JSON.stringify(r.template, null, 2) : ''
  const hsm = cfg.http_status_map || {}
  f.http_status = {}
  Object.entries(hsm).forEach(([k, v]) => { f.http_status[k] = String(v) })
  const a = cfg.auth || {}
  f.auth = {
    enabled: !!a.enabled,
    header_name: a.header_name || 'X-API-Key',
    header_value: a.header_value || '',
    ip_whitelist: Array.isArray(a.ip_whitelist) ? [...a.ip_whitelist] : [],
  }
  f.receivePathRows = Array.isArray(cfg.receive_paths)
    ? cfg.receive_paths.filter(x => x && x.path).map(x => ({
        path: x.path,
        action: x.action || 'task_start',
        methods: Array.isArray(x.methods) && x.methods.length ? [...x.methods] : ['POST'],
      }))
    : []
  f.max_body_bytes = cfg.max_body_bytes ?? 1048576
  f.input_format = cfg.input_format || 'auto'
  f.merge_query_params = cfg.merge_query_params !== false
  f.echo_fields = Array.isArray(cfg.echo_fields) ? [...cfg.echo_fields] : []
  f.switch_project_on_task = !!cfg.switch_project_on_task
  f.match_project_by_name = cfg.match_project_by_name !== false
  f.mapRows = Object.entries(cfg.product_project_map || {}).map(([code, pid]) => ({
    code, project_id: typeof pid === 'number' ? pid : (parseInt(pid, 10) || null),
  }))
  f.create_work_order_on_task = !!cfg.create_work_order_on_task
  f.order_binding = cfg.order_binding || 'project'
  f.channel_field = cfg.channel_field || 'channel'
  f.reject_duplicate_task = !!cfg.reject_duplicate_task
  f.complete_field = cfg.complete_field || ''
  f.complete_true_words = Array.isArray(cfg.complete_true_words) ? [...cfg.complete_true_words] : []
  f.complete_match_field = cfg.complete_match_field || 'task_no'
  f.complete_match_column = cfg.complete_match_column || 'order_no'
  f.supersede_previous_task = !!cfg.supersede_previous_task
  f.supersede_scope = cfg.supersede_scope || 'project'
  f.report_complete_on_supersede = cfg.report_complete_on_supersede !== false
  f.complete_event_type = cfg.complete_event_type || 'task_complete'
  // 报警闭环: alarm_event_name 兼容字符串/逗号串/数组三种历史形态
  const ev = cfg.alarm_event_name
  f.alarm_event_name = Array.isArray(ev)
    ? ev.filter(Boolean)
    : (typeof ev === 'string' ? ev.split(',').map(s => s.trim()).filter(Boolean) : [])
  f.alarm_record_on_results = Array.isArray(cfg.alarm_record_on_results)
    ? [...cfg.alarm_record_on_results] : ['NG']
  f.alarm_dedup_sec = cfg.alarm_dedup_sec ?? 5
  f.alarm_clear_match_fields = Array.isArray(cfg.alarm_clear_match_fields)
    ? [...cfg.alarm_clear_match_fields] : ['task_no', 'product_code', 'step_code', 'operator']
  f.alarmMapRows = Object.entries(cfg.alarm_ledger_field_map || {}).map(([k, v]) => ({ k, v }))
  f.unknown_product_message = cfg.unknown_product_message || ''
  const b = cfg.alarm_banner || {}
  f.alarm_banner = {
    enabled: b.enabled !== false,
    position: b.position || 'top',
    color: b.color || '#dc2626',
    poll_interval_sec: b.poll_interval_sec ?? 3,
    show_task_no: b.show_task_no !== false,
    show_product_code: b.show_product_code !== false,
    show_step_code: b.show_step_code !== false,
    show_operator: b.show_operator !== false,
    show_time: b.show_time !== false,
  }
  const ti = cfg.task_info_display || {}
  f.task_info_display = {
    show_task_no: ti.show_task_no === true,
    show_product_code: ti.show_product_code === true,
    show_step_code: ti.show_step_code === true,
    show_operator: ti.show_operator === true,
  }
  Object.assign(form, f)
}

function buildConfig() {
  const field_map = {}
  form.fieldRows.forEach(r => { if (r.k && r.k.trim()) field_map[r.k.trim()] = (r.v || '').trim() })
  const product_project_map = {}
  form.mapRows.forEach(r => {
    if (r.code && r.code.trim() && r.project_id != null) product_project_map[r.code.trim()] = r.project_id
  })
  // 成功码 / 失败码: 纯数字串转回数字, 否则保留原值(允许字符串码)
  const numOrRaw = (v) => {
    const s = String(v ?? '').trim()
    if (s === '') return s
    return /^-?\d+$/.test(s) ? parseInt(s, 10) : s
  }
  const codes = {}
  Object.entries(form.response.codes || {}).forEach(([k, v]) => { codes[k] = numOrRaw(v) })
  const messages = {}
  Object.entries(form.response.messages || {}).forEach(([k, v]) => {
    if (v != null && String(v).trim() !== '') messages[k] = String(v)
  })
  const response = {
    code_field: form.response.code_field || 'code',
    message_field: form.response.message_field || 'message',
    success_code: numOrRaw(form.response.success_code),
    success_message: form.response.success_message || 'OK',
    codes,
    messages,
    format: form.response.format || 'json',
    xml_root: form.response.xml_root || 'response',
    xml_declaration: form.response.xml_declaration !== false,
  }
  const tpl = (form.response_template_text || '').trim()
  if (tpl) response.template = JSON.parse(tpl)  // 解析失败由 save() 捕获并提示
  const http_status_map = {}
  Object.entries(form.http_status || {}).forEach(([k, v]) => {
    const s = String(v ?? '').trim()
    if (s !== '' && /^\d+$/.test(s)) http_status_map[k] = parseInt(s, 10)
  })
  const alarm_ledger_field_map = {}
  ;(form.alarmMapRows || []).forEach(r => {
    if (r.k && r.k.trim()) alarm_ledger_field_map[r.k.trim()] = (r.v || '').trim()
  })
  const receive_paths = (form.receivePathRows || [])
    .filter(r => r.path && r.path.trim())
    .map(r => ({
      path: r.path.trim(),
      action: r.action || 'task_start',
      methods: (Array.isArray(r.methods) && r.methods.length ? r.methods : ['POST']).map(m => String(m).toUpperCase()),
    }))
  return {
    http_status_map,
    receive_paths,
    enabled: form.enabled,
    field_map,
    required_fields: form.required,
    response,
    max_body_bytes: form.max_body_bytes || 0,
    input_format: form.input_format || 'auto',
    merge_query_params: form.merge_query_params,
    auth: {
      enabled: form.auth.enabled,
      header_name: form.auth.header_name || 'X-API-Key',
      header_value: form.auth.header_value || '',
      ip_whitelist: form.auth.ip_whitelist || [],
    },
    echo_fields: form.echo_fields,
    switch_project_on_task: form.switch_project_on_task,
    match_project_by_name: form.match_project_by_name,
    product_project_map,
    create_work_order_on_task: form.create_work_order_on_task,
    order_binding: form.order_binding || 'project',
    channel_field: form.channel_field || 'channel',
    reject_duplicate_task: form.reject_duplicate_task,
    complete_field: form.complete_field || '',
    complete_true_words: [...(form.complete_true_words || [])],
    complete_match_field: form.complete_match_field || 'task_no',
    complete_match_column: form.complete_match_column || 'order_no',
    supersede_previous_task: form.supersede_previous_task,
    supersede_scope: form.supersede_scope || 'project',
    report_complete_on_supersede: form.report_complete_on_supersede,
    complete_event_type: form.complete_event_type || 'task_complete',
    // 报警闭环
    alarm_event_name: [...(form.alarm_event_name || [])],
    alarm_record_on_results: [...(form.alarm_record_on_results || [])],
    alarm_dedup_sec: Number(form.alarm_dedup_sec) || 0,
    alarm_clear_match_fields: [...(form.alarm_clear_match_fields || [])],
    alarm_ledger_field_map: alarm_ledger_field_map,
    unknown_product_message: (form.unknown_product_message || '').trim(),
    alarm_banner: { ...form.alarm_banner },
    task_info_display: { ...form.task_info_display },
  }
}

async function load() {
  loading.value = true
  try {
    const res = await getInboundConfig()
    applyConfig(res.data || res || {})
  } catch (e) {
    ElMessage.error('加载入站配置失败：' + (e?.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}

async function loadProjects() {
  try {
    const res = await getProjects()
    projects.value = res.data || res || []
  } catch { projects.value = [] }
}

async function loadLogs() {
  logLoading.value = true
  try {
    const res = await getInboundLogs({ limit: pollingStore.logLimit('inbound', 50) })
    logs.value = res.data || res || []
  } catch (e) {
    ElMessage.error('加载日志失败：' + (e?.response?.data?.detail || e.message))
  } finally {
    logLoading.value = false
  }
}

async function save() {
  const tpl = (form.response_template_text || '').trim()
  if (tpl) {
    try { JSON.parse(tpl) } catch (e) {
      ElMessage.error('自定义响应模板不是合法 JSON：' + e.message); return
    }
  }
  saving.value = true
  try {
    const cfg = buildConfig()
    await saveInboundConfig(cfg)
    dbg('mes.gateway', '保存入站配置',
        `enabled=${cfg.enabled} 映射=${Object.keys(cfg.field_map).length} 切项目=${cfg.switch_project_on_task} 建工单=${cfg.create_work_order_on_task} 顶替=${cfg.supersede_previous_task}`)
    ElMessage.success('已保存')
    await load()
  } catch (e) {
    ElMessage.error('保存失败：' + (e?.response?.data?.detail || e.message))
  } finally {
    saving.value = false
  }
}

onMounted(async () => {
  await pollingStore.load()
  load()
  loadProjects()
  loadLogs()
})
</script>

<style scoped>
.adv-collapse :deep(.el-collapse-item__header),
.adv-collapse :deep(.el-collapse-item__wrap) {
  background: transparent;
  border-color: rgba(8, 145, 178, 0.3);
}
.adv-collapse :deep(.el-collapse-item__content) {
  padding-top: 12px;
}
</style>
