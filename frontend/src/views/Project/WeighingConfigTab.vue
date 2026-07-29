<template>
  <!-- ==================== 称重投料模式专属配置（v3.31, 2026-07 自 index.vue 外置） ==================== -->
  <div v-if="project.pipeline_config && project.pipeline_config.weighing"
       class="h-full overflow-y-auto p-4 pb-32 custom-scrollbar space-y-6">

    <!-- v3.35 融合模式提示条 -->
    <el-alert v-if="isStepGate" type="info" :closable="false" show-icon>
      <template #title>
        当前为「视觉 SOP + 秤门控」融合模式：周期由步骤设置里的<b>视觉顺序</b>驱动，电子秤只作为已配「外设门控」步骤的放行条件。
        违序/缺步/超时报警由顺序模式自动生效；本页只需配秤参数、型号标准量表和防错策略。
      </template>
    </el-alert>

    <!-- v3.39 驱动模式 -->
    <el-card v-if="!isStepGate" shadow="never" class="bg-slate-800 border-slate-700">
      <template #header>
        <div class="flex items-center justify-between">
          <span class="font-bold text-white">驱动模式</span>
          <span class="text-xs text-gray-400">决定秤读数如何推进检测流程</span>
        </div>
      </template>
      <el-select v-model="project.pipeline_config.weighing.drive_mode" size="small" style="width: 100%">
        <el-option label="逐道投料（经典：放件→去皮→投料→判定，逐道推进）" value="scale" />
        <el-option label="两阶段流水线（秤上称重结算 + 秤下收尾动作结案，两件并行）" value="pipeline" />
      </el-select>
      <div v-if="isPipeline" class="text-xs text-gray-500 mt-2">
        流水线模式：工件坐秤装料，离秤瞬间冻结重量并判 OK/NG，随后进「待收尾队列」；
        视觉识别到<b>收尾动作</b>（如 加钢脚水泥）时按先进先出正式结案上传。秤指令严格由重量驱动，模型漏检不影响称重。
      </div>
    </el-card>

    <!-- v3.39 流水线参数 -->
    <el-card v-if="isPipeline" shadow="never" class="bg-slate-800 border-slate-700">
      <template #header>
        <div class="flex items-center justify-between">
          <span class="font-bold text-white">流水线参数（标签绑定 / 皮重范围 / 队列）</span>
          <span class="text-xs text-gray-400">标签名须与模型输出一致</span>
        </div>
      </template>
      <div class="grid grid-cols-3 gap-4 text-sm text-gray-200">
        <div>
          <label class="block text-gray-400 text-xs mb-1">标签①上秤动作（去皮加速/佐证）</label>
          <el-input v-model="pipe.label_onscale" size="small" placeholder="工件上秤" />
        </div>
        <div>
          <label class="block text-gray-400 text-xs mb-1">标签②装料动作（错盆守卫用）</label>
          <el-input v-model="pipe.label_fill" size="small" placeholder="加水泥" />
        </div>
        <div>
          <label class="block text-gray-400 text-xs mb-1">标签③收尾动作（结案上一件）</label>
          <el-input v-model="pipe.label_finalize" size="small" placeholder="加钢脚水泥" />
        </div>
        <div>
          <label class="block text-gray-400 text-xs mb-1">判定料别（查型号标准量表）</label>
          <el-select v-model="pipe.material" size="small" style="width: 100%">
            <el-option v-for="m in project.pipeline_config.weighing.materials" :key="m" :label="m" :value="m" />
          </el-select>
        </div>
        <div>
          <label class="block text-gray-400 text-xs mb-1">皮重下限(kg)：工件+容器最轻</label>
          <el-input-number v-model="pipe.tare_min_kg" :min="0" :step="0.1" :precision="3" size="small" style="width: 100%" controls-position="right" />
        </div>
        <div>
          <label class="block text-gray-400 text-xs mb-1">皮重上限(kg)：超出报"放错物品"</label>
          <el-input-number v-model="pipe.tare_max_kg" :min="0" :step="0.1" :precision="3" size="small" style="width: 100%" controls-position="right" />
        </div>
        <div>
          <label class="block text-gray-400 text-xs mb-1">待收尾队列深度（超出报节拍异常）</label>
          <el-input-number v-model="pipe.queue_depth" :min="1" :max="10" :step="1" size="small" style="width: 100%" controls-position="right" />
        </div>
        <div class="col-span-2">
          <label class="block text-gray-400 text-xs mb-1">秤台区（标签①只在此区域内有效，防别处拿件误报；不画=不过滤）</label>
          <el-button size="small" plain :type="pipe.onscale_polygon && pipe.onscale_polygon.length >= 3 ? 'success' : 'primary'"
            @click="$emit('open-pipeline-roi-editor')">
            {{ pipe.onscale_polygon && pipe.onscale_polygon.length >= 3 ? `已画秤台区(${pipe.onscale_polygon.length}点), 点击重画` : '画秤台区' }}
          </el-button>
          <el-button v-if="pipe.onscale_polygon" size="small" type="danger" plain @click="pipe.onscale_polygon = null">清除</el-button>
        </div>
      </div>
      <div class="text-xs text-gray-500 mt-3">
        提示：错盆拦截（装料动作出现在钢脚水泥盆）用下方「视觉料源防错」加一条<b>动作限区</b>规则：
        标签＝{{ pipe.label_fill || '加水泥' }}，画<b>允许</b>装料的区域（秤台+钢帽盆），盆外动作即报警。
      </div>
    </el-card>

    <!-- v3.39 秤指令时序（方案 3.1 节 15 项，全部可调） -->
    <el-card v-if="isPipeline" shadow="never" class="bg-slate-800 border-slate-700">
      <template #header>
        <div class="flex items-center justify-between">
          <span class="font-bold text-white">秤指令时序</span>
          <span class="text-xs text-gray-400">什么时候发、等多久、多稳算稳 —— 全部现场可调</span>
        </div>
      </template>
      <div class="grid grid-cols-3 gap-4 text-sm text-gray-200">
        <div>
          <label class="block text-gray-400 text-xs mb-1">去皮触发源</label>
          <el-select v-model="timing.tare_trigger_source" size="small" style="width: 100%">
            <el-option label="重量为主（标签①仅缩短稳定等待，推荐）" value="weight_first" />
            <el-option label="严格双确认（标签①+重量都满足才发）" value="dual_confirm" />
            <el-option label="仅重量（完全忽略视觉）" value="weight_only" />
          </el-select>
        </div>
        <div>
          <label class="block text-gray-400 text-xs mb-1">去皮延迟(ms)：0=立即发</label>
          <el-input-number v-model="timing.tare_delay_ms" :min="0" :max="10000" :step="100" size="small" style="width: 100%" controls-position="right" />
        </div>
        <div>
          <label class="block text-gray-400 text-xs mb-1">皮重稳定窗口(ms)</label>
          <el-input-number v-model="timing.tare_stable_ms" :min="200" :max="5000" :step="100" size="small" style="width: 100%" controls-position="right" />
        </div>
        <div>
          <label class="block text-gray-400 text-xs mb-1">皮重稳定公差(kg)</label>
          <el-input-number v-model="timing.tare_stable_tol_kg" :min="0.001" :max="0.05" :step="0.001" :precision="3" size="small" style="width: 100%" controls-position="right" />
        </div>
        <div>
          <label class="block text-gray-400 text-xs mb-1">净重稳定窗口(ms)</label>
          <el-input-number v-model="timing.net_stable_ms" :min="200" :max="5000" :step="100" size="small" style="width: 100%" controls-position="right" />
        </div>
        <div>
          <label class="block text-gray-400 text-xs mb-1">净重稳定公差(kg)</label>
          <el-input-number v-model="timing.net_stable_tol_kg" :min="0.001" :max="0.05" :step="0.001" :precision="3" size="small" style="width: 100%" controls-position="right" />
        </div>
        <div>
          <label class="block text-gray-400 text-xs mb-1">缺料报警持续阈值(秒)：防瞬时误报</label>
          <el-input-number v-model="timing.shortage_alarm_sec" :min="0" :max="30" :step="0.5" size="small" style="width: 100%" controls-position="right" />
        </div>
        <div>
          <label class="block text-gray-400 text-xs mb-1">离秤确认时长(ms)：空秤稳定多久记账</label>
          <el-input-number v-model="timing.depart_confirm_ms" :min="200" :max="3000" :step="100" size="small" style="width: 100%" controls-position="right" />
        </div>
        <div>
          <label class="block text-gray-400 text-xs mb-1">清零延迟(ms)：确认离秤后多久发清零</label>
          <el-input-number v-model="timing.zero_delay_ms" :min="0" :max="10000" :step="100" size="small" style="width: 100%" controls-position="right" />
        </div>
        <div>
          <label class="block text-gray-400 text-xs mb-1">清零验证窗口(ms)：多久内应归零</label>
          <el-input-number v-model="timing.zero_verify_ms" :min="500" :max="10000" :step="500" size="small" style="width: 100%" controls-position="right" />
        </div>
        <div>
          <label class="block text-gray-400 text-xs mb-1">清零自动重发次数</label>
          <el-input-number v-model="timing.zero_retry" :min="0" :max="3" :step="1" size="small" style="width: 100%" controls-position="right" />
        </div>
        <div>
          <label class="block text-gray-400 text-xs mb-1">标签①确认帧数</label>
          <el-input-number v-model="timing.label1_min_frames" :min="1" :max="30" :step="1" size="small" style="width: 100%" controls-position="right" />
        </div>
        <div>
          <label class="block text-gray-400 text-xs mb-1">标签①新鲜期(秒)：出现后多久内算佐证</label>
          <el-input-number v-model="timing.label1_fresh_sec" :min="0.5" :max="30" :step="0.5" size="small" style="width: 100%" controls-position="right" />
        </div>
        <div>
          <label class="block text-gray-400 text-xs mb-1">标签③确认帧数</label>
          <el-input-number v-model="timing.label3_min_frames" :min="1" :max="30" :step="1" size="small" style="width: 100%" controls-position="right" />
        </div>
        <div>
          <label class="block text-gray-400 text-xs mb-1">标签③冷却期(秒)：刚离秤多久内不受理收尾</label>
          <el-input-number v-model="timing.label3_cooldown_sec" :min="0" :max="10" :step="0.5" size="small" style="width: 100%" controls-position="right" />
        </div>
        <div>
          <label class="block text-gray-400 text-xs mb-1">装料超时(秒)</label>
          <el-input-number v-model="timing.fill_timeout_sec" :min="30" :max="3600" :step="30" size="small" style="width: 100%" controls-position="right" />
        </div>
        <div>
          <label class="block text-gray-400 text-xs mb-1">收尾超时(秒)：超时按"收尾未确认"结案</label>
          <el-input-number v-model="timing.finalize_timeout_sec" :min="10" :max="3600" :step="10" size="small" style="width: 100%" controls-position="right" />
        </div>
      </div>
    </el-card>

    <!-- 前置要求 -->
    <el-card shadow="never" class="bg-slate-800 border-slate-700">
      <template #header><span class="font-bold text-white">前置要求</span></template>
      <div class="space-y-3 text-sm text-gray-200">
        <div class="flex items-center justify-between">
          <span>开始前必须先选操作人员</span>
          <el-switch v-model="project.pipeline_config.weighing.require_operator" />
        </div>
        <div class="flex items-center justify-between">
          <div>
            <span>作业员从用户名单选择</span>
            <div class="text-xs text-gray-500">开 = 监控页"人员"改为下拉，只能选「设置 → 用户与权限」里启用的账号（禁用即从名单消失）；关 = 自由填写</div>
          </div>
          <el-switch v-model="project.pipeline_config.weighing.operator_from_users" />
        </div>
        <div class="flex items-center justify-between">
          <span>开始前必须先选水泥型号</span>
          <el-switch v-model="project.pipeline_config.weighing.require_model" />
        </div>
        <div class="flex items-center justify-between">
          <span>本件完成后自动给秤置零</span>
          <el-switch v-model="project.pipeline_config.weighing.auto_zero_after_done" />
        </div>
      </div>
    </el-card>

    <!-- v3.35 前置选择有效期 -->
    <el-card shadow="never" class="bg-slate-800 border-slate-700">
      <template #header>
        <div class="flex items-center justify-between">
          <span class="font-bold text-white">前置选择有效期</span>
          <span class="text-xs text-gray-400">过期后旧的人员/型号选择自动失效，需重选否则报警拦截（治"昨天选的型号今天忘了换"）</span>
        </div>
      </template>
      <div class="grid grid-cols-2 gap-4 text-sm text-gray-200">
        <div>
          <label class="block text-gray-400 text-xs mb-1">失效策略</label>
          <el-select v-model="ctxExpiry.mode" size="small" style="width: 100%">
            <el-option label="永不失效（默认，与旧版一致）" value="never" />
            <el-option label="每天定时失效（如每天早 8 点）" value="daily" />
            <el-option label="按班次失效（跨班次即失效）" value="shift" />
            <el-option label="选择后 N 小时失效" value="hours" />
          </el-select>
        </div>
        <div v-if="ctxExpiry.mode === 'daily'">
          <label class="block text-gray-400 text-xs mb-1">每天失效时刻（HH:MM）</label>
          <el-input v-model="ctxExpiry.reset_time" size="small" placeholder="08:00" />
        </div>
        <div v-if="ctxExpiry.mode === 'hours'">
          <label class="block text-gray-400 text-xs mb-1">有效小时数</label>
          <el-input-number v-model="ctxExpiry.hours" :min="0.5" :step="0.5" size="small" style="width: 100%" controls-position="right" />
        </div>
        <div v-if="ctxExpiry.mode !== 'never'">
          <label class="block text-gray-400 text-xs mb-1">失效时清哪些选择</label>
          <el-checkbox-group v-model="ctxExpiry.expire_fields">
            <el-checkbox value="model">产品型号</el-checkbox>
            <el-checkbox value="operator">操作人员</el-checkbox>
          </el-checkbox-group>
        </div>
      </div>
      <div v-if="ctxExpiry.mode === 'shift'" class="mt-3">
        <div class="flex items-center gap-2 mb-2">
          <span class="text-xs text-gray-400">班次表（跨午夜班次填 start &gt; end，如 22:00-06:00）</span>
          <el-button size="small" @click="ctxExpiry.shifts.push({ name: '', start: '08:00', end: '16:00' })">+ 加班次</el-button>
        </div>
        <div v-for="(s, i) in ctxExpiry.shifts" :key="i" class="flex items-center gap-2 mb-1 text-sm">
          <el-input v-model="s.name" size="small" placeholder="班次名" style="width: 120px" />
          <el-input v-model="s.start" size="small" placeholder="08:00" style="width: 90px" />
          <span class="text-gray-500">→</span>
          <el-input v-model="s.end" size="small" placeholder="16:00" style="width: 90px" />
          <el-button size="small" type="danger" plain @click="ctxExpiry.shifts.splice(i, 1)">删</el-button>
        </div>
      </div>
    </el-card>

    <!-- v3.35 视觉料源防错（动作 × 固定区域） -->
    <el-card shadow="never" class="bg-slate-800 border-slate-700">
      <template #header>
        <div class="flex items-center justify-between">
          <span class="font-bold text-white">视觉料源防错</span>
          <div class="flex items-center gap-3">
            <span class="text-xs text-gray-400">用"动作发生在哪个料盆区域"识别料别/拦违规（料别本身视觉难分时的正解）</span>
            <el-switch v-model="visualGuard.enabled" />
          </div>
        </div>
      </template>
      <div v-if="visualGuard.enabled" class="space-y-4 text-sm text-gray-200">
        <div class="grid grid-cols-3 gap-4">
          <div>
            <label class="block text-gray-400 text-xs mb-1">识别方式</label>
            <el-select v-model="visualGuard.source" size="small" style="width: 100%">
              <el-option label="动作 × 固定区域（推荐：舀料动作命中哪个盆）" value="region_action" />
              <el-option label="标签直接映射（料桶可视觉区分时）" value="direct_label" />
            </el-select>
          </div>
          <div class="flex items-center justify-between pt-4">
            <el-tooltip content="开 = 识别到投错料别时拦截该步骤等纠正；关 = 只报警不拦截" placement="top">
              <span class="cursor-help border-b border-dashed border-gray-500 text-xs">投错拦截等纠正</span>
            </el-tooltip>
            <el-switch v-model="visualGuard.wrong_block" size="small" />
          </div>
          <div>
            <label class="block text-gray-400 text-xs mb-1">同规则报警冷却(秒)</label>
            <el-input-number v-model="visualGuard.cooldown_sec" :min="0" :step="1" size="small" style="width: 100%" controls-position="right" />
          </div>
        </div>
        <div>
          <div class="flex items-center gap-2 mb-2">
            <span class="text-xs text-gray-400">规则列表：料别映射 = 动作命中区域时上报该料别；动作限区 = 动作只允许出现在区域内，区域外报警</span>
            <el-button size="small" type="primary" plain @click="addGuardRule">+ 加规则</el-button>
          </div>
          <table v-if="visualGuard.rules.length" class="w-full text-xs text-gray-200">
            <thead>
              <tr class="text-gray-400">
                <th class="text-left py-1">规则名</th>
                <th class="py-1">动作标签(逗号分隔)</th>
                <th class="py-1 w-28">类型</th>
                <th class="py-1 w-32">映射料别</th>
                <th class="py-1 w-24">连续帧</th>
                <th class="py-1 w-32">判定区域</th>
                <th class="py-1 w-16">删</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(r, i) in visualGuard.rules" :key="i" class="border-t border-slate-700">
                <td class="py-1 px-1"><el-input v-model="r.name" size="small" placeholder="如 钢帽料盆" /></td>
                <td class="py-1 px-1"><el-input :model-value="(r.labels || []).join(',')" size="small" placeholder="舀料" @update:model-value="v => r.labels = v.split(/[,，]/).map(x => x.trim()).filter(Boolean)" /></td>
                <td class="py-1 px-1">
                  <el-select v-model="r.mode" size="small">
                    <el-option label="料别映射" value="map" />
                    <el-option label="动作限区" value="restrict" />
                  </el-select>
                </td>
                <td class="py-1 px-1">
                  <el-select v-if="r.mode === 'map'" v-model="r.material" size="small" clearable placeholder="选料别">
                    <el-option v-for="m in project.pipeline_config.weighing.materials" :key="m" :label="m" :value="m" />
                  </el-select>
                  <span v-else class="text-gray-500">—</span>
                </td>
                <td class="py-1 px-1"><el-input-number v-model="r.min_frames" :min="1" :step="1" size="small" controls-position="right" style="width: 80px" /></td>
                <td class="py-1 px-1">
                  <el-button size="small" plain :type="r.polygon && r.polygon.length >= 3 ? 'success' : 'primary'"
                    @click="$emit('open-guard-roi-editor', i)">
                    {{ r.polygon && r.polygon.length >= 3 ? `已画(${r.polygon.length}点)` : '画区域' }}
                  </el-button>
                </td>
                <td class="py-1 px-1"><el-button size="small" type="danger" plain @click="visualGuard.rules.splice(i, 1)">删</el-button></td>
              </tr>
            </tbody>
          </table>
          <div v-else class="text-gray-500 text-xs py-2">还没有规则。百斯特场景示例：规则「钢帽料盆」= 动作标签 [舀料] × 大盆区域 → 映射料别 钢帽水泥；再加一条「钢脚料盆」映射 钢脚水泥。</div>
        </div>
      </div>
      <div v-else class="text-gray-500 text-xs">未启用。启用后按"动作 × 区域"规则识别当前料源，配合「料别校验方式 = 视觉识别」使用。</div>
    </el-card>

    <!-- 料别顺序 -->
    <el-card shadow="never" class="bg-slate-800 border-slate-700">
      <template #header>
        <div class="flex items-center justify-between">
          <span class="font-bold text-white">料别（投料顺序）</span>
          <span class="text-xs text-gray-400">按顺序投放，每道料分别去皮+称量+判定</span>
        </div>
      </template>
      <div class="flex flex-wrap gap-2 items-center">
        <el-tag
          v-for="(mat, idx) in project.pipeline_config.weighing.materials"
          :key="idx"
          closable
          type="info"
          @close="removeWeighingMaterial(idx)">
          {{ idx + 1 }}. {{ mat }}
        </el-tag>
        <el-input
          v-model="newWeighingMaterial"
          size="small"
          style="width: 160px"
          placeholder="新料别名"
          @keyup.enter="addWeighingMaterial" />
        <el-button size="small" type="primary" @click="addWeighingMaterial">添加料别</el-button>
      </div>
    </el-card>

    <!-- 型号标准量表 -->
    <el-card shadow="never" class="bg-slate-800 border-slate-700">
      <template #header>
        <div class="flex items-center justify-between">
          <span class="font-bold text-white">型号标准量表（kg）</span>
          <div class="flex items-center gap-2">
            <el-input v-model="newWeighingModel" size="small" style="width: 160px" placeholder="新型号名" @keyup.enter="addWeighingModel" />
            <el-button size="small" type="primary" @click="addWeighingModel">添加型号</el-button>
          </div>
        </div>
      </template>
      <div v-if="!weighingModelNames.length" class="text-gray-400 text-sm py-4 text-center">
        还没有型号。每个型号 = 一种水泥规格（可用视觉模型自动识别后切换），为它的每道料设置标准量与上下公差。
      </div>
      <div v-for="mname in weighingModelNames" :key="mname" class="mb-4 p-3 rounded bg-slate-900 border border-slate-700">
        <div class="flex items-center justify-between mb-2">
          <span class="font-bold text-cyan-400">{{ mname }}</span>
          <el-button size="small" type="danger" plain @click="removeWeighingModel(mname)">删除型号</el-button>
        </div>
        <table class="w-full text-sm text-gray-200">
          <thead>
            <tr class="text-gray-400 text-xs">
              <th class="text-left py-1">料别</th>
              <th class="py-1">标准量</th>
              <th class="py-1">下公差(允许少)</th>
              <th class="py-1">上公差(允许多)</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="mat in project.pipeline_config.weighing.materials" :key="mat">
              <td class="py-1">{{ mat }}</td>
              <td class="py-1 px-1"><el-input-number v-model="project.pipeline_config.weighing.models[mname][mat].standard" :min="0" :step="0.001" :precision="3" size="small" controls-position="right" style="width: 120px" /></td>
              <td class="py-1 px-1"><el-input-number v-model="project.pipeline_config.weighing.models[mname][mat].low_tol" :min="0" :step="0.001" :precision="3" size="small" controls-position="right" style="width: 120px" /></td>
              <td class="py-1 px-1"><el-input-number v-model="project.pipeline_config.weighing.models[mname][mat].high_tol" :min="0" :step="0.001" :precision="3" size="small" controls-position="right" style="width: 120px" /></td>
            </tr>
          </tbody>
        </table>
      </div>
    </el-card>

    <!-- 去皮 / 稳定判定 -->
    <el-card shadow="never" class="bg-slate-800 border-slate-700">
      <template #header><span class="font-bold text-white">去皮与稳定判定</span></template>
      <div class="grid grid-cols-2 gap-4 text-sm text-gray-200">
        <div>
          <label class="block text-gray-400 text-xs mb-1">去皮方式</label>
          <el-select v-model="project.pipeline_config.weighing.tare_mode" size="small" style="width: 100%">
            <el-option label="放件后自动去皮(稳定即去)" value="auto_stable" />
            <el-option label="仅手动去皮" value="manual" />
          </el-select>
        </div>
        <div>
          <label class="block text-gray-400 text-xs mb-1">自动去皮触发重量(kg)：放件超过此值才去皮</label>
          <el-input-number v-model="project.pipeline_config.weighing.tare_trigger_weight" :min="0" :step="0.01" :precision="3" size="small" style="width: 100%" controls-position="right" />
        </div>
        <div>
          <label class="block text-gray-400 text-xs mb-1">稳定容差(kg)：连续读数波动小于此值算稳</label>
          <el-input-number v-model="project.pipeline_config.weighing.stable_tol" :min="0" :step="0.001" :precision="3" size="small" style="width: 100%" controls-position="right" />
        </div>
        <div>
          <label class="block text-gray-400 text-xs mb-1">稳定所需连续帧数</label>
          <el-input-number v-model="project.pipeline_config.weighing.stable_min_samples" :min="1" :step="1" size="small" style="width: 100%" controls-position="right" />
        </div>
        <div>
          <label class="block text-gray-400 text-xs mb-1">最小有效投料量(kg)：低于此值不算一次投料</label>
          <el-input-number v-model="project.pipeline_config.weighing.measure_min_weight" :min="0" :step="0.001" :precision="3" size="small" style="width: 100%" controls-position="right" />
        </div>
        <div>
          <label class="block text-gray-400 text-xs mb-1">去皮稳定采样帧数</label>
          <el-input-number v-model="project.pipeline_config.weighing.tare_settle_samples" :min="1" :step="1" size="small" style="width: 100%" controls-position="right" />
        </div>
      </div>
    </el-card>

    <!-- 料别/视觉校验 + 报警事件映射 -->
    <el-card shadow="never" class="bg-slate-800 border-slate-700">
      <template #header><span class="font-bold text-white">校验与报警</span></template>
      <div class="space-y-4 text-sm text-gray-200">
        <div>
          <label class="block text-gray-400 text-xs mb-1">料别校验方式</label>
          <el-select v-model="project.pipeline_config.weighing.material_check" size="small" style="width: 100%">
            <el-option label="按投料顺序自动推进（不校验料别）" value="sequence" />
            <el-option label="视觉识别料别（模型/外部上报标签校验）" value="visual" />
            <el-option label="关闭料别校验" value="off" />
          </el-select>
        </div>
        <div class="grid grid-cols-2 gap-4">
          <div>
            <label class="block text-gray-400 text-xs mb-1">缺料 → 触发事件</label>
            <el-select v-model="project.pipeline_config.weighing.alarm_event_shortage" size="small" style="width: 100%">
              <el-option v-for="ev in project.events_config" :key="ev.id" :label="ev.name" :value="ev.id" />
            </el-select>
          </div>
          <div>
            <label class="block text-gray-400 text-xs mb-1">超量 → 触发事件</label>
            <el-select v-model="project.pipeline_config.weighing.alarm_event_over" size="small" style="width: 100%">
              <el-option v-for="ev in project.events_config" :key="ev.id" :label="ev.name" :value="ev.id" />
            </el-select>
          </div>
          <div>
            <label class="block text-gray-400 text-xs mb-1">料别错 → 触发事件</label>
            <el-select v-model="project.pipeline_config.weighing.alarm_event_wrong" size="small" style="width: 100%">
              <el-option v-for="ev in project.events_config" :key="ev.id" :label="ev.name" :value="ev.id" />
            </el-select>
          </div>
          <div>
            <label class="block text-gray-400 text-xs mb-1">前置未满足 → 触发事件</label>
            <el-select v-model="project.pipeline_config.weighing.alarm_event_precheck" size="small" style="width: 100%">
              <el-option v-for="ev in project.events_config" :key="ev.id" :label="ev.name" :value="ev.id" />
            </el-select>
          </div>
          <div>
            <label class="block text-gray-400 text-xs mb-1">动作限区违规 → 触发事件</label>
            <el-select v-model="project.pipeline_config.weighing.alarm_event_guard" size="small" style="width: 100%">
              <el-option v-for="ev in project.events_config" :key="ev.id" :label="ev.name" :value="ev.id" />
            </el-select>
          </div>
        </div>
        <div class="text-xs text-gray-500">
          提示：以上事件在「事件设置」里勾选<b>需人工确认</b>后，报警会定格本工位直到确认（可配 USB 确认按钮，见 扫码器 → USB 扫码枪 → 用途「报警确认按钮」）。
        </div>
      </div>
    </el-card>

    <!-- v3.35.1 监控页显示 -->
    <el-card shadow="never" class="bg-slate-800 border-slate-700">
      <template #header><span class="font-bold text-white">监控页显示</span></template>
      <div class="space-y-2 text-sm text-gray-200">
        <div class="flex items-center gap-3">
          <el-switch v-model="project.pipeline_config.weighing.show_monitor_weights" size="small" />
          <span>检测中心显示实时称重数值条</span>
        </div>
        <div class="text-xs text-gray-500">
          开启后，检测中心视频下方显示：<b>实时读数</b>、<b>皮重</b>（去皮那一刻的工件/容器自重）、
          <b>净重</b>（去皮归零后的读数 = 已投料量）与各步骤门控状态。称重投料模式与融合模式（视觉步骤 × 秤门控）都生效。
        </div>
      </div>
    </el-card>

  </div>
</template>

<script setup>
// ==================== 称重投料模式配置编辑（自 index.vue 平移） ====================
// 数据流约定（拆分原则 2）：直接原位修改父级传入的 project.pipeline_config.weighing
// 子树（与拆分前语义一致，保存仍由父级"保存"按钮统一走 updateProject），
// 不自行发请求、不另起轮询。默认值注入 ensureWeighingDefaults 留在父级加载链路。
import { ref, computed } from 'vue';
import { ElMessage } from 'element-plus';

const props = defineProps({
  project: { type: Object, required: true },
});
defineEmits(['open-guard-roi-editor', 'open-pipeline-roi-editor']);

const newWeighingMaterial = ref('');
const newWeighingModel = ref('');

// v3.35 融合模式 (视觉 SOP + 秤门控) 判定: 顶部提示条用
const isStepGate = computed(() =>
  props.project?.pipeline_config?.weighing?.drive_mode === 'step_gate');

// v3.39 两阶段流水线模式
const isPipeline = computed(() =>
  props.project?.pipeline_config?.weighing?.drive_mode === 'pipeline');
const pipe = computed(() => props.project.pipeline_config.weighing.pipeline);
const timing = computed(() => props.project.pipeline_config.weighing.timing);

// v3.35 前置选择有效期 / 视觉料源防错子树 (父级 ensureWeighingDefaults 已注入默认)
const ctxExpiry = computed(() => props.project.pipeline_config.weighing.context_expiry);
const visualGuard = computed(() => props.project.pipeline_config.weighing.visual_guard);

const addGuardRule = () => {
  visualGuard.value.rules.push({
    name: '', labels: [], mode: 'map', material: '', min_frames: 3, polygon: null,
  });
};

const weighingModelNames = computed(() => {
  const w = props.project?.pipeline_config?.weighing;
  return w && w.models ? Object.keys(w.models) : [];
});

// 规整: 保证每个型号对每道料都有 spec 对象, 否则模板 v-model 取不到会报错
const normalizeWeighingSpecs = () => {
  const w = props.project?.pipeline_config?.weighing;
  if (!w) return;
  if (!Array.isArray(w.materials)) w.materials = [];
  if (!w.models || typeof w.models !== 'object') w.models = {};
  Object.keys(w.models).forEach(mname => {
    if (!w.models[mname] || typeof w.models[mname] !== 'object') w.models[mname] = {};
    w.materials.forEach(mat => {
      if (!w.models[mname][mat]) {
        w.models[mname][mat] = { standard: 0, low_tol: 0.05, high_tol: 0.05 };
      }
    });
  });
};

const addWeighingMaterial = () => {
  const name = (newWeighingMaterial.value || '').trim();
  if (!name) return;
  const w = props.project.pipeline_config.weighing;
  if (w.materials.includes(name)) { ElMessage.warning('料别已存在'); return; }
  w.materials.push(name);
  newWeighingMaterial.value = '';
  normalizeWeighingSpecs();
};

const removeWeighingMaterial = (idx) => {
  const w = props.project.pipeline_config.weighing;
  w.materials.splice(idx, 1);
};

const addWeighingModel = () => {
  const name = (newWeighingModel.value || '').trim();
  if (!name) return;
  const w = props.project.pipeline_config.weighing;
  if (w.models[name]) { ElMessage.warning('型号已存在'); return; }
  w.models[name] = {};
  newWeighingModel.value = '';
  normalizeWeighingSpecs();
};

const removeWeighingModel = (name) => {
  const w = props.project.pipeline_config.weighing;
  delete w.models[name];
};
</script>
