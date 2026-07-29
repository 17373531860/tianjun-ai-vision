<template>
  <div class="packaging-flow-panel p-4">
    <div class="header flex justify-between items-center mb-4">
      <h2 class="text-lg text-white font-bold">包装箱结算配置 (v3.21)</h2>
      <el-button type="primary" size="small" @click="openCreate">新建配置</el-button>
    </div>

    <div class="text-gray-400 text-xs mb-3 leading-relaxed">
      扫码驱动的"工单 → 箱 → 托盘"三层结算: 扫工单拉箱数 → 每箱依次放 N 个合格托盘 → 扫下一标签封上一箱.
      漏箱 / 多箱 / 托盘不达标 / 标签错都会报警. 不启用任何配置时与不配置时完全一致 (零差异).
    </div>

    <el-table :data="flows" stripe size="small" empty-text="尚未创建任何包装结算配置" class="w-full">
      <el-table-column prop="id" label="ID" width="60" />
      <el-table-column prop="name" label="名称" min-width="120" />
      <el-table-column label="检测工位" width="90">
        <template #default="{ row }">
          <span class="font-mono text-cyan-400">ch{{ row.channel_id }}</span>
        </template>
      </el-table-column>
      <el-table-column label="箱数来源" min-width="140">
        <template #default="{ row }">
          <span class="text-xs">
            {{ row.box_count_source === 'formula' ? '公式换算' : 'MES 字段' }}
            <span class="text-gray-400">({{ row.box_count_field }})</span>
          </span>
        </template>
      </el-table-column>
      <el-table-column label="回推" width="70">
        <template #default="{ row }">
          <el-tag size="small" :type="row.push_on_complete ? 'success' : 'info'">
            {{ row.push_on_complete ? '开' : '关' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="启用" width="80">
        <template #default="{ row }">
          <el-switch :model-value="row.enabled" @change="toggleEnabled(row, $event)" />
        </template>
      </el-table-column>
      <el-table-column label="操作" width="190" align="center">
        <template #default="{ row }">
          <el-button size="small" @click="openEdit(row)">编辑</el-button>
          <el-button size="small" type="info" @click="viewState(row)">查看</el-button>
          <el-button size="small" type="danger" :disabled="row.enabled" @click="del(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 创建/编辑对话框 -->
    <el-dialog
      v-model="dialogVisible"
      :title="form.id ? '编辑包装结算配置' : '新建包装结算配置'"
      width="680px"
      :close-on-click-modal="false"
      top="5vh"
    >
      <div class="preset-bar mb-3">
        <el-button type="warning" plain size="small" @click="applyHiwinPreset">
          一键套用「上银包装线」预设
        </el-button>
        <span class="text-xs text-gray-400 ml-2">先套预设再按现场微调, 不确定的高级项保持默认即可</span>
      </div>

      <el-form :model="form" label-width="150px" size="small">
        <!-- ===== 基础区 (小白必填) ===== -->
        <el-divider content-position="left">基础</el-divider>
        <el-form-item label="配置名称">
          <el-input v-model="form.name" placeholder="上银包装线-1" :disabled="!!form.id" />
        </el-form-item>
        <el-form-item label="检测工位 (channel)">
          <el-input-number v-model="form.channel_id" :min="0" :max="3" />
          <span class="text-xs text-gray-400 ml-2">数托盘滑块用的那个摄像头工位</span>
        </el-form-item>
        <el-form-item label="扫码器 ID">
          <el-input v-model="scanDeviceIdStr" placeholder="留空 = 用全局 USB 扫码枪" />
          <span class="text-xs text-gray-400 ml-2">扫工单 / 箱标签的设备; 不确定就留空</span>
        </el-form-item>
        <el-form-item label="拉单 MES 连接 ID">
          <el-input v-model="pullConnIdStr" placeholder="留空 = 不拉单 (离线/手填)" />
          <span class="text-xs text-gray-400 ml-2">扫工单后向哪条 MES 连接查箱数 (在 MES 网关页配)</span>
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="form.enabled" />
        </el-form-item>

        <el-form-item label="计数口径">
          <el-radio-group v-model="form.count_unit">
            <el-radio value="sliders">按滑块 (上银)</el-radio>
            <el-radio value="trays">按托盘 (三层)</el-radio>
          </el-radio-group>
          <div class="text-xs text-gray-400 mt-1">
            按滑块: 扫工单从 MES 拿滑块总数 ÷ 每箱滑块数(从项目读,如96) = 箱数 + 尾箱余数, 只数每箱滑块总数 →
            托盘那两组(①②)会自动隐藏, 只配 ⑦ 滑块设置。<br/>
            按托盘: 三层 工单→箱→托盘, 配 ①② 每箱几托盘/每托盘几件。
          </div>
        </el-form-item>

        <!-- ===== 高级 5 组 (折叠, 默认不展开) ===== -->
        <el-collapse v-model="activeGroups" class="mt-2">
          <!-- 组① 工单与箱数 (仅托盘口径; 滑块口径箱数由 ⑦ 滑块总数÷每箱数 算出) -->
          <el-collapse-item v-if="form.count_unit !== 'sliders'"
                            title="① 工单与箱数 — 这张工单一共做几箱怎么算" name="g1">
            <el-form-item label="箱数来源">
              <el-radio-group v-model="form.box_count_source">
                <el-radio value="field">直接取 MES 字段</el-radio>
                <el-radio value="formula">字段 ÷ 每箱数量 换算</el-radio>
              </el-radio-group>
            </el-form-item>
            <el-form-item label="MES 箱数/数量字段">
              <el-input v-model="form.box_count_field" placeholder="dispatch_qty" />
              <div class="text-xs text-gray-400 mt-1">
                field 模式: 这个字段就是箱数; formula 模式: 这个字段是总数量, 除以每箱托盘数×每托盘数量得箱数.
              </div>
            </el-form-item>
          </el-collapse-item>

          <!-- 组② 数量规格 (仅托盘口径; 滑块口径不用托盘概念) -->
          <el-collapse-item v-if="form.count_unit !== 'sliders'"
                            title="② 数量规格 — 每箱几托盘 / 每托盘几件" name="g2">
            <el-form-item label="每箱托盘数模式">
              <el-radio-group v-model="form.trays_per_box_mode">
                <el-radio value="fixed">固定</el-radio>
                <el-radio value="by_spec">按规格查表</el-radio>
              </el-radio-group>
            </el-form-item>
            <el-form-item label="每箱托盘数 (固定)" v-if="form.trays_per_box_mode === 'fixed'">
              <el-input-number v-model="form.trays_per_box_fixed" :min="1" :max="50" />
            </el-form-item>
            <el-form-item label="每箱托盘数表 (JSON)" v-else>
              <el-input type="textarea" v-model="traysPerBoxTableJson" :rows="2"
                        placeholder='{"规格A": 4, "规格B": 6}' />
            </el-form-item>

            <el-form-item label="每托盘数量模式">
              <el-radio-group v-model="form.tray_qty_mode">
                <el-radio value="fixed">固定</el-radio>
                <el-radio value="by_spec">按规格查表</el-radio>
              </el-radio-group>
            </el-form-item>
            <el-form-item label="每托盘数量 (固定)" v-if="form.tray_qty_mode === 'fixed'">
              <el-input-number v-model="form.tray_qty_fixed" :min="0" :max="999" />
              <span class="text-xs text-gray-400 ml-2">仅"公式换算箱数"时用到; 托盘合格与否由检测层判定</span>
            </el-form-item>
            <el-form-item label="每托盘数量表 (JSON)" v-else>
              <el-input type="textarea" v-model="trayQtyTableJson" :rows="2"
                        placeholder='{"规格A": 20, "规格B": 30}' />
            </el-form-item>
          </el-collapse-item>

          <!-- 组③ 标签校验 -->
          <el-collapse-item title="③ 标签校验 — 工单号与箱标签怎么比对" name="g3">
            <el-form-item label="复合条码取段">
              <el-switch v-model="form.composite_label_enabled" />
              <span class="text-xs text-gray-400 ml-2">
                箱标签是多段拼接码 (如 订单|工单|数量|校验串) 时开启: 先按分隔符拆段取出工单段,
                再走下面的比对方式; 无分隔符的码 (工单纸) 原样通过, 两种码可混扫
              </span>
            </el-form-item>
            <template v-if="form.composite_label_enabled">
              <el-form-item label="段分隔符">
                <el-input v-model="form.composite_delimiter" placeholder="|" style="width: 120px" maxlength="8" />
                <span class="text-xs text-gray-400 ml-2">默认 "|" (竖线)</span>
              </el-form-item>
              <el-form-item label="取哪一段">
                <el-radio-group v-model="form.composite_pick_mode">
                  <el-radio value="prefix">按前缀认段 (推荐)</el-radio>
                  <el-radio value="index">取第 N 段</el-radio>
                </el-radio-group>
              </el-form-item>
              <el-form-item v-if="form.composite_pick_mode === 'prefix'" label="工单段前缀">
                <el-input v-model="form.composite_prefix" placeholder="JOB" style="width: 160px" maxlength="32" />
                <span class="text-xs text-gray-400 ml-2">
                  取以此开头的段 (上银填 JOB); 段顺序变了也不受影响, 多段命中取最长
                </span>
              </el-form-item>
              <el-form-item v-else label="第几段">
                <el-input-number v-model="form.composite_index" :min="1" :max="32" />
                <span class="text-xs text-gray-400 ml-2">从 1 数起 (上银工单在第 2 段)</span>
              </el-form-item>
              <el-form-item label="取段预览">
                <div class="w-full">
                  <el-input v-model="compositePreviewInput"
                            placeholder="粘贴整串箱标签码, 如 ORD260300050-2|JOB260600268-13|54.00|55AEA…" clearable>
                    <template #prepend>扫到</template>
                  </el-input>
                  <div class="text-sm mt-2 flex items-center gap-2">
                    <span class="text-gray-400">取出工单段 →</span>
                    <span class="font-mono px-2 py-0.5 rounded"
                          :class="compositePreviewResult ? 'bg-green-700/40 text-green-300' : 'text-gray-500'">
                      {{ compositePreviewResult || '(输入样例码看效果)' }}
                    </span>
                  </div>
                  <div class="text-xs text-gray-400 mt-1">
                    取出的段还会再走下面的比对方式 (如补符号) 才参与工单比对.
                  </div>
                </div>
              </el-form-item>
            </template>
            <el-form-item label="比对方式">
              <el-select v-model="form.label_match" class="w-full">
                <el-option label="精确比对" value="exact" />
                <el-option label="补回特殊符号 (上银推荐)" value="insert_char" />
                <el-option label="去掉连字符再比" value="strip_hyphen" />
                <el-option label="只取数字再比" value="digits_only" />
              </el-select>
              <div class="text-xs text-gray-400 mt-1">
                上银: 标签是 JOB150700114-1, 但扫码枪丢了"-"扫成 JOB1507001141 (序号还在).
                选"补回特殊符号", 把"-"补回固定位置 → 还原成 JOB150700114-1,
                之后记录、查 MES 永远用这个完整值 (JOB1507001141 只在扫码转换前一瞬出现).
              </div>
            </el-form-item>
            <template v-if="form.label_match === 'insert_char'">
              <el-form-item label="要补回的符号">
                <el-input v-model="form.hyphen_template" placeholder="-" style="width: 120px" maxlength="8" />
                <span class="text-xs text-gray-400 ml-2">默认 "-"</span>
              </el-form-item>
              <el-form-item label="补在第几位后">
                <el-input-number v-model="form.hyphen_pos" :min="0" :max="64" />
                <span class="text-xs text-gray-400 ml-2">
                  主单号长度 (如 JOB+9 位 = 12); 补在第 12 位字符之后, 序号 1~3 位都适配; 0 = 不补
                </span>
              </el-form-item>
              <el-form-item label="效果预览">
                <div class="w-full">
                  <el-input v-model="hyphenPreviewInput" placeholder="粘贴扫码枪扫到的样例码, 如 JOB1507001141"
                            clearable>
                    <template #prepend>扫到</template>
                  </el-input>
                  <div class="text-sm mt-2 flex items-center gap-2">
                    <span class="text-gray-400">补回后 →</span>
                    <span class="font-mono px-2 py-0.5 rounded"
                          :class="hyphenPreviewResult ? 'bg-green-700/40 text-green-300' : 'text-gray-500'">
                      {{ hyphenPreviewResult || '(输入样例码看效果)' }}
                    </span>
                  </div>
                  <div class="text-xs text-gray-400 mt-1">
                    这就是系统记录、查 MES 实际使用的工单号; 调上面的位置/符号会实时变化.
                  </div>
                </div>
              </el-form-item>
            </template>
            <el-form-item label="工单号识别规则">
              <el-input v-model="form.order_code_pattern" placeholder="留空 = 不过滤" class="w-72" maxlength="128" clearable />
              <div class="text-xs text-gray-400 mt-1">
                仅在没有在途工单、准备<strong>开第一单</strong>时校验: 取段+归一化后的码须从头匹配此正则才允许开单,
                否则拒扫并提示 (如挡掉第一枪误扫的数量码 80.00). 已有在途工单时仍走上面的复合取段 + 标签不符逻辑, 不受此项影响.
                留空 = 不过滤. 上银填 <code>^JOB</code>
              </div>
            </el-form-item>
            <el-form-item label="标签固定长度">
              <el-input-number v-model="form.label_len" :min="0" :max="64" />
              <span class="text-xs text-gray-400 ml-2">
                0 = 不限长; 填 N = 不是 N 位就报警 (上银序号位数不固定, 应保持 0)
              </span>
            </el-form-item>
          </el-collapse-item>

          <!-- 组④ 异常策略 -->
          <el-collapse-item title="④ 异常策略 — 出问题时怎么处理" name="g4">
            <el-form-item label="拉单失败时">
              <el-radio-group v-model="form.on_mes_fail">
                <el-radio value="block">阻断 (重扫)</el-radio>
                <el-radio value="offline">允许离线继续</el-radio>
              </el-radio-group>
            </el-form-item>
            <el-form-item label="箱标签不符时">
              <el-radio-group v-model="form.on_label_mismatch">
                <el-radio value="off">不判 (扫不同号直接当换工单)</el-radio>
                <el-radio value="warn">报警但仍切新工单</el-radio>
                <el-radio value="block">报警且不切 (等扫回正确标签)</el-radio>
              </el-radio-group>
              <div class="text-xs text-gray-400 mt-1">
                开工后第一个箱标签就与工单号不符时的处理 (疑似贴错标签).
              </div>
            </el-form-item>
            <el-form-item label="漏箱时 (没做满就扫新标签)">
              <el-radio-group v-model="form.on_short_box">
                <el-radio value="redo">报警 + 允许补做</el-radio>
                <el-radio value="void">工单作废</el-radio>
              </el-radio-group>
            </el-form-item>
            <el-form-item label="强停时未满箱判定">
              <el-radio-group v-model="form.on_forced_stop_partial">
                <el-radio value="fail">算不合格</el-radio>
                <el-radio value="pass">算合格</el-radio>
              </el-radio-group>
            </el-form-item>
          </el-collapse-item>

          <!-- 组⑤ 收尾与回推 -->
          <el-collapse-item title="⑤ 收尾与回推 — 停止/待机怎么收尾, 完成要不要回推 MES" name="g5">
            <el-form-item label="停止/待机处置">
              <el-select v-model="form.on_forced_stop" class="w-full">
                <el-option label="收尾结算并完成工单" value="settle" />
                <el-option label="直接作废" value="abort" />
                <el-option label="保留进行中 (待恢复)" value="keep" />
              </el-select>
            </el-form-item>
            <el-form-item label="待机也收尾">
              <el-switch v-model="form.forced_settle_on_standby" />
              <span class="text-xs text-gray-400 ml-2">关 = 待机只暂停不结算, 仅停止才收尾</span>
            </el-form-item>
            <el-form-item label="完成回推 MES">
              <el-switch v-model="form.push_on_complete" />
              <span class="text-xs text-gray-400 ml-2">开 = 工单做完把结果推回 MES (需现场确认)</span>
            </el-form-item>
            <el-form-item label="回推事件名" v-if="form.push_on_complete">
              <el-input v-model="form.push_event_type" placeholder="packaging_complete" />
              <div class="text-xs text-gray-400 mt-1">
                在 MES 网关页某条连接的"推送事件"里加这个名 + 配地址/模板才会真正发出.
              </div>
            </el-form-item>
            <el-form-item label="同步到工单管理">
              <el-switch v-model="form.sync_work_orders" />
              <span class="text-xs text-gray-400 ml-2">
                开(默认) = 扫码开工的工单同步进「MES管理 → 工单」页(来源=包装扫码):
                开工"生产中"、收尾"已完成"、中止"已取消", 可在工单页查看与管理;
                关 = 老行为, 包装单只存运行记录, 工单页不可见
              </span>
            </el-form-item>
          </el-collapse-item>

          <!-- 组⑥ 异常事件映射 -->
          <el-collapse-item title="⑥ 异常报警 — 每种异常触发哪个项目事件" name="g6">
            <div class="text-xs text-gray-400 mb-2 leading-relaxed">
              每种异常可挑一个"项目-事件设置"里建好的事件去触发, 复用它配好的报警(灯/蜂鸣)、语音、Toast、计数;
              <b>留空 = 走默认通用报警</b>. 触发包装异常不会打断正在跑的托盘检测.
              <span v-if="!eventOptions.length" class="text-yellow-400">
                (当前没拉到事件 — 请先到「项目」页的事件设置里建事件, 或保持留空走默认报警)
              </span>
            </div>
            <el-form-item v-for="opt in EVENT_FIELDS" :key="opt.field" :label="opt.label">
              <el-select v-model="form[opt.field]" class="w-full" clearable
                         placeholder="默认通用报警" filterable>
                <el-option v-for="ev in eventOptions" :key="ev.value"
                           :label="ev.label" :value="ev.value" />
              </el-select>
            </el-form-item>
          </el-collapse-item>

          <!-- 组⑦ 滑块口径 + 尾箱 + 自动切项目 + 塞工单 (v3.22 上银 MES 闭环, 仅滑块口径显示) -->
          <el-collapse-item v-if="form.count_unit === 'sliders'"
                            title="⑦ 滑块口径设置 — 每箱滑块数 / 尾箱 / 塞工单 / 缺油嘴" name="g7">
              <el-form-item label="每箱滑块数来源">
                <el-radio-group v-model="form.items_per_box_source">
                  <el-radio value="project">读激活项目容器目标</el-radio>
                  <el-radio value="config">下面固定值</el-radio>
                </el-radio-group>
              </el-form-item>
              <el-form-item label="每箱滑块数 (固定)" v-if="form.items_per_box_source === 'config'">
                <el-input-number v-model="form.items_per_box_fixed" :min="0" :max="9999" />
              </el-form-item>
              <el-form-item label="MES 滑块总数字段">
                <el-input v-model="form.slider_total_field" placeholder="dispatch_qty" />
                <span class="text-xs text-gray-400 ml-2">上银 = 排产量字段</span>
              </el-form-item>
              <el-form-item label="按规格自动切项目">
                <el-switch v-model="form.auto_switch_project" />
                <span class="text-xs text-gray-400 ml-2">扫工单拿到规格后自动激活对应项目</span>
              </el-form-item>
              <el-form-item label="项目名匹配规格 兜底" v-if="form.auto_switch_project">
                <el-switch v-model="form.match_project_by_name" />
                <span class="text-xs text-gray-400 ml-2">开 = 映射表没命中时, 按"项目名是规格的一段"自动匹配 (项目名贴在规格里即可, 免维护映射表)</span>
              </el-form-item>
              <el-form-item label="严格边界" v-if="form.auto_switch_project && form.match_project_by_name">
                <el-switch v-model="form.name_match_strict_boundary" />
                <span class="text-xs text-gray-400 ml-2">开 = 项目名须贴规格首/尾或分隔符(防短名误吞); 关 = 取最长命中压歧义, 能覆盖无分隔符场景</span>
              </el-form-item>
              <el-form-item label="规格→项目映射 (JSON)" v-if="form.auto_switch_project">
                <el-input type="textarea" v-model="specToProjectJson" :rows="2"
                          placeholder='{"HGH20-*": 3, "*-HGW15": 4}' />
                <div class="text-xs text-gray-400 mt-1">键 = 规格(支持通配符 * ?, 如 HGH20-* / *-HGW15 / *ABC*), 值 = 项目 ID; 优先于"项目名兜底", 全靠命名匹配可留空</div>
              </el-form-item>
              <el-form-item label="尾箱必须塞工单">
                <el-switch v-model="form.tail_paper_order_required" />
                <span class="text-xs text-gray-400 ml-2">开 = 尾箱结算前必须检测到"放工单"动作, 否则不收尾并报警</span>
              </el-form-item>
              <el-form-item label="放工单步骤标签" v-if="form.tail_paper_order_required">
                <el-input v-model="form.tail_paper_step_label" placeholder="put_paper" />
                <span class="text-xs text-gray-400 ml-2">项目里"放工单"那一步的检测标签</span>
              </el-form-item>
              <el-form-item label="放工单=工单收尾" v-if="form.tail_paper_order_required">
                <el-switch v-model="form.tail_paper_as_close_action" />
                <span class="text-xs text-gray-400 ml-2">
                  开 = 箱归周期结算、放工单归工单收尾: 尾箱照常按自身成绩当场落账（不报警不弹NG）,
                  工单转入「等放工单收尾」, 检测到放工单动作即完成工单;
                  一直没放、直接扫下一张工单时, 旧工单判 NG 收尾再开新单（是否报警见下方开关）.
                  关 = 老行为: 尾箱暂不收尾, 每个周期结算都报警等着
                </span>
              </el-form-item>
              <el-form-item label="缺工单判定方式"
                            v-if="form.tail_paper_order_required && form.tail_paper_as_close_action">
                <el-radio-group v-model="paperJudgeMode">
                  <el-radio value="scan">扫新单时判定（默认）</el-radio>
                  <el-radio value="timeout">按时限判定</el-radio>
                </el-radio-group>
                <div class="text-xs text-gray-400 mt-1 w-full">
                  两种方式二选一。扫新单 = 下一张工单扫码进来时判定上一单放没放工单, 没放 → 报警 + 旧单判 NG 收尾, 无时间限制;
                  按时限 = 只按时间判定, 到点没放 → 报警 + 工单判 NG 收尾, 扫新单不参与判定（时限内扫新单会被拒收提示稍候）
                </div>
              </el-form-item>
              <el-form-item label="放工单时限(秒)"
                            v-if="form.tail_paper_order_required && form.tail_paper_as_close_action
                                  && paperJudgeMode === 'timeout'">
                <el-input-number v-model="form.tail_paper_timeout_s" :min="1" :max="3600" :step="5" />
                <span class="text-xs text-gray-400 ml-2">
                  尾箱落账后 N 秒内等放工单动作: 等到 → 工单合格收尾; 到点没等到 → 报警 + 判 NG 收尾
                </span>
              </el-form-item>
              <el-form-item label="缺工单触发事件">
                <el-select v-model="form.event_missing_paper" class="w-full" clearable
                           placeholder="默认通用报警" filterable>
                  <el-option v-for="ev in eventOptions" :key="ev.value"
                             :label="ev.label" :value="ev.value" />
                </el-select>
              </el-form-item>
              <el-form-item label="每箱必须放油嘴">
                <el-switch v-model="form.oil_nozzle_required" />
                <span class="text-xs text-gray-400 ml-2">开 = 每箱封箱结算前必须检测到"放油嘴"动作, 否则不收尾并报警(等补放)</span>
              </el-form-item>
              <el-form-item label="放油嘴步骤标签" v-if="form.oil_nozzle_required">
                <el-input v-model="form.oil_nozzle_step_label" placeholder="put_nozzle" />
                <span class="text-xs text-gray-400 ml-2">项目里"放油嘴"那一步的检测标签</span>
              </el-form-item>
              <el-form-item label="缺油嘴触发事件" v-if="form.oil_nozzle_required">
                <el-select v-model="form.event_missing_nozzle" class="w-full" clearable
                           placeholder="默认通用报警" filterable>
                  <el-option v-for="ev in eventOptions" :key="ev.value"
                             :label="ev.label" :value="ev.value" />
                </el-select>
              </el-form-item>
              <el-form-item label="完成工单重扫拦截">
                <el-switch v-model="form.block_completed_order_rescan" />
                <span class="text-xs text-gray-400 ml-2">
                  开 = 工单完成且结果 OK 后, 再扫到同号时只提示、不再重新录入
                  (完成但 NG 的单不拦, 允许重扫补做); 关 = 老行为, 重扫会重新开单
                </span>
              </el-form-item>
              <el-form-item label="重扫拦截提示事件" v-if="form.block_completed_order_rescan">
                <el-select v-model="form.event_completed_order_rescan" class="w-full" clearable
                           placeholder="默认通用报警" filterable>
                  <el-option v-for="ev in eventOptions" :key="ev.value"
                             :label="ev.label" :value="ev.value" />
                </el-select>
              </el-form-item>
          </el-collapse-item>

          <!-- 组⑧ 箱标签扫码授权 + 标签取本箱数量 (v3.45, 仅滑块口径显示) -->
          <el-collapse-item v-if="form.count_unit === 'sliders'"
                            title="⑧ 箱标签扫码 — 每箱扫标签放行 / 从标签取本箱数量" name="g8">
              <div class="text-xs text-gray-400 mb-2 leading-relaxed">
                每箱开做前必须扫箱标签（含第一箱）: 扫工单只开工单, 每箱都要再扫一次箱标签才放行开做, 未扫就开始作业当场报警;
                可再开「取本箱数量」: 从标签复合二维码里取出本箱应装数量当本箱目标（<b>逐箱可变</b>, 覆盖工单级每箱数/尾数计划）.
              </div>
              <el-form-item label="每箱必须扫箱标签">
                <el-switch v-model="form.box_label_scan_required" />
                <span class="text-xs text-gray-400 ml-2">
                  开 = 每箱进「等扫箱标签」态, 扫到本工单的标签才放行开做; 关 = 老行为 (扫工单后自动逐箱开)
                </span>
              </el-form-item>
              <template v-if="form.box_label_scan_required">
                <el-form-item label="从标签取本箱数量">
                  <el-switch v-model="form.label_qty_enabled" />
                  <span class="text-xs text-gray-400 ml-2">
                    开 = 必须扫到带数量的复合二维码才放行, 扫到裸条形码/取不出数量 → 报警"请扫二维码"继续等;
                    关 = 标签只当放行凭证, 本箱目标仍按工单级计划
                  </span>
                </el-form-item>
                <el-form-item label="数量在第几段" v-if="form.label_qty_enabled">
                  <el-input-number v-model="form.label_qty_segment" :min="1" :max="20" />
                  <span class="text-xs text-gray-400 ml-2">
                    按组③的分隔符拆段后取第 N 段 (1 起); 如 订单|工单|数量|校验串 → 第 3 段, "24.00" 取整为 24
                  </span>
                </el-form-item>
                <el-form-item label="数量段识别正则" v-if="form.label_qty_enabled">
                  <el-input v-model="form.label_qty_pattern" placeholder="留空 = 按上面段号取; 如 \d+\.\d+" />
                  <span class="text-xs text-gray-400 ml-2">
                    可选兜底: 段序不固定的标签格式用正则在各段里找数量段, 配了就优先于段号 — 格式变了只改这里不改代码
                  </span>
                </el-form-item>
                <el-form-item label="已放行后重扫标签">
                  <el-radio-group v-model="form.label_rescan_action">
                    <el-radio value="ignore">忽略（默认）</el-radio>
                    <el-radio value="update">更新本箱目标</el-radio>
                  </el-radio-group>
                  <div class="text-xs text-gray-400 mt-1 w-full">
                    本箱已放行后又扫到本工单标签的处置: 更新档用新扫到的数量覆盖本箱目标（贴错标签重贴重扫的场景）
                  </div>
                </el-form-item>
                <el-form-item label="未扫标签做完整箱">
                  <el-radio-group v-model="form.unauthorized_cycle_action">
                    <el-radio value="hold">箱账挂起等人工（默认）</el-radio>
                    <el-radio value="book">报警后照常落账</el-radio>
                  </el-radio-group>
                  <div class="text-xs text-gray-400 mt-1 w-full">
                    工人无视报警把整箱做完时的处置: 挂起 = 等人工补齐/认NG/重做（重做后必须先扫标签再重测）;
                    照常落账 = 只报警不拦产线, 按工单级计划目标落账
                  </div>
                </el-form-item>
                <el-form-item label="收尾数量对账" v-if="form.label_qty_enabled">
                  <el-switch v-model="form.label_total_check" />
                  <span class="text-xs text-gray-400 ml-2">
                    开 = 工单收尾时核对「各箱标签数量合计」与「排产量」, 不平报警（只提醒留痕, 不改箱成绩）
                  </span>
                </el-form-item>
                <el-form-item label="未扫标签开做 事件">
                  <el-select v-model="form.event_box_not_scanned" class="w-full" clearable
                             placeholder="默认通用报警" filterable>
                    <el-option v-for="ev in eventOptions" :key="ev.value"
                               :label="ev.label" :value="ev.value" />
                  </el-select>
                </el-form-item>
                <el-form-item label="标签缺数量 事件" v-if="form.label_qty_enabled">
                  <el-select v-model="form.event_label_qty_missing" class="w-full" clearable
                             placeholder="默认通用报警" filterable>
                    <el-option v-for="ev in eventOptions" :key="ev.value"
                               :label="ev.label" :value="ev.value" />
                  </el-select>
                </el-form-item>
                <el-form-item label="对账不平 事件" v-if="form.label_total_check">
                  <el-select v-model="form.event_label_total_mismatch" class="w-full" clearable
                             placeholder="默认通用报警" filterable>
                    <el-option v-for="ev in eventOptions" :key="ev.value"
                               :label="ev.label" :value="ev.value" />
                  </el-select>
                </el-form-item>
              </template>
          </el-collapse-item>
        </el-collapse>
      </el-form>

      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="submit">保存</el-button>
      </template>
    </el-dialog>

    <!-- 运行时状态对话框 -->
    <el-dialog v-model="stateDialogVisible" :title="`运行进度: ${stateData?.config?.name || ''}`" width="560px">
      <div v-if="stateData">
        <div v-if="stateData.state">
          <el-descriptions :column="2" border size="small">
            <el-descriptions-item label="当前工单">{{ stateData.state.order_no }}</el-descriptions-item>
            <el-descriptions-item label="应做箱数">{{ stateData.state.box_total || '未知' }}</el-descriptions-item>
            <el-descriptions-item label="已结算箱">{{ stateData.state.box_done }}</el-descriptions-item>
            <el-descriptions-item label="NG 箱">{{ stateData.state.box_ng }}</el-descriptions-item>
            <el-descriptions-item label="当前第几箱">{{ stateData.state.current_box_index }}</el-descriptions-item>
            <el-descriptions-item
              :label="stateData.state.count_unit === 'sliders' ? '当前箱滑块' : '当前箱托盘'">
              {{ stateData.state.count_unit === 'sliders'
                  ? stateData.state.current_box_sliders : stateData.state.current_box_trays }}
            </el-descriptions-item>
            <el-descriptions-item label="状态">{{ stateData.state.status }}</el-descriptions-item>
            <el-descriptions-item label="规格">{{ stateData.state.spec || '-' }}</el-descriptions-item>
            <template v-if="stateData.state.count_unit === 'sliders'">
              <el-descriptions-item label="滑块总数">{{ stateData.state.slider_total }}</el-descriptions-item>
              <el-descriptions-item label="每箱滑块">{{ stateData.state.items_per_box }}</el-descriptions-item>
              <el-descriptions-item label="尾箱目标">{{ stateData.state.tail_target }}</el-descriptions-item>
              <el-descriptions-item label="尾箱已塞工单">
                {{ stateData.state.paper_order_done ? '是' : '否' }}
              </el-descriptions-item>
            </template>
          </el-descriptions>
          <div class="mt-3 text-gray-300 text-xs">各箱明细:</div>
          <!-- sliders 口径: 显示滑块数/目标; trays 口径: 显示托盘数/需要 -->
          <el-table v-if="stateData.state.count_unit === 'sliders'"
                    :data="stateData.state.box_details || []" size="small" class="mt-1"
                    empty-text="还没结算任何箱">
            <el-table-column prop="box" label="箱号" width="70" />
            <el-table-column prop="sliders" label="滑块数" width="80" />
            <el-table-column prop="target" label="目标" width="70" />
            <el-table-column label="尾箱" width="60">
              <template #default="{ row }">{{ row.is_tail ? '尾' : '' }}</template>
            </el-table-column>
            <el-table-column prop="result" label="结果" />
          </el-table>
          <el-table v-else :data="stateData.state.box_details || []" size="small" class="mt-1"
                    empty-text="还没结算任何箱">
            <el-table-column prop="box" label="箱号" width="70" />
            <el-table-column prop="trays" label="托盘数" width="80" />
            <el-table-column prop="need" label="需要" width="70" />
            <el-table-column prop="result" label="结果" />
          </el-table>
        </div>
        <div v-else class="text-gray-400 text-sm py-4 text-center">
          当前没有进行中的工单 (还没扫工单 / 已完成)
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, computed } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import {
  listPackagingFlows,
  createPackagingFlow,
  updatePackagingFlow,
  deletePackagingFlow,
  getPackagingFlowState,
} from '@/api/packaging_flow';
import { getProjects } from '@/api/project';
import { dbg } from '@/utils/debug';

const flows = ref([]);
const dialogVisible = ref(false);
const stateDialogVisible = ref(false);
const stateData = ref(null);
const activeGroups = ref([]);

// 组⑥ 异常 → 事件映射的 7 个字段 (与后端 _KIND_TO_EVENT_FIELD 对应)
const EVENT_FIELDS = [
  { field: 'event_short_box', label: '漏箱' },
  { field: 'event_over_box', label: '多箱' },
  { field: 'event_tray_ng', label: '单托盘检测 NG' },
  { field: 'event_box_ng', label: '整箱托盘数不足 NG' },
  { field: 'event_label_mismatch', label: '箱标签与工单不符' },
  { field: 'event_label_len', label: '标签长度异常' },
  { field: 'event_mes_fail', label: '拉单失败' },
];

// 从所有项目的事件设置聚合出候选事件 (跨项目去重 by id+name)
const eventOptions = ref([]);
const loadEventOptions = async () => {
  try {
    const { data } = await getProjects();
    const projects = data?.items || data || [];
    const opts = [];
    const seen = new Set();
    for (const p of projects) {
      for (const ev of (p?.events_config || [])) {
        if (ev?.id == null) continue;
        const key = `${ev.id}__${ev.name || ''}`;
        if (seen.has(key)) continue;
        seen.add(key);
        opts.push({ value: ev.id, label: `${ev.name || '事件'} (id=${ev.id})` });
      }
    }
    eventOptions.value = opts;
  } catch (e) {
    // 静默: 拉不到事件不阻塞, 留空走默认报警即可
  }
};

const _newForm = () => ({
  id: null,
  name: '',
  enabled: false,
  channel_id: 0,
  scan_device_id: null,
  pull_conn_id: null,
  box_count_source: 'field',
  box_count_field: 'dispatch_qty',
  tray_qty_mode: 'fixed',
  tray_qty_fixed: 0,
  tray_qty_table: null,
  trays_per_box_mode: 'fixed',
  trays_per_box_fixed: 4,
  trays_per_box_table: null,
  label_match: 'strip_hyphen',
  label_len: 0,
  hyphen_template: null,
  hyphen_pos: 0,
  // 复合条码取段 (v3.30.1, 默认关)
  composite_label_enabled: false,
  composite_delimiter: '|',
  composite_pick_mode: 'prefix',
  composite_prefix: null,
  composite_index: 1,
  order_code_pattern: null,
  on_mes_fail: 'block',
  on_label_mismatch: 'warn',
  on_short_box: 'redo',
  on_forced_stop_partial: 'fail',
  on_forced_stop: 'settle',
  forced_settle_on_standby: true,
  push_on_complete: false,
  push_event_type: 'packaging_complete',
  // 组⑥ 异常 → 项目事件映射 (null = 默认通用报警)
  event_short_box: null,
  event_over_box: null,
  event_tray_ng: null,
  event_box_ng: null,
  event_label_mismatch: null,
  event_label_len: null,
  event_mes_fail: null,
  // 组⑦ 滑块口径 + 尾箱 + 自动切项目 + 塞工单 gate (v3.22, 默认关/trays)
  count_unit: 'trays',
  items_per_box_source: 'project',
  items_per_box_fixed: 0,
  slider_total_field: 'dispatch_qty',
  auto_switch_project: false,
  spec_to_project: null,
  match_project_by_name: false,
  name_match_strict_boundary: false,
  tail_paper_order_required: false,
  tail_paper_step_label: null,
  tail_paper_as_close_action: false,  // v3.43 箱归周期结算/放工单归工单收尾 (默认关=老行为)
  tail_paper_scan_alarm: true,        // v3.43 扫新单发现没放工单时报警 (默认开)
  tail_paper_timeout_s: 0,            // v3.43 尾箱落账→放工单时限报警 (0=不限)
  event_missing_paper: null,
  // 缺油嘴 gate (v3.23, 每箱查, 默认关)
  oil_nozzle_required: false,
  oil_nozzle_step_label: null,
  event_missing_nozzle: null,
  // 已完成(OK)工单重扫拦截 (v3.42.1, 默认关)
  block_completed_order_rescan: false,
  event_completed_order_rescan: null,
  // 包装工单镜像进工单管理 (v3.45, 默认开)
  sync_work_orders: true,
  // 组⑧ 箱标签扫码授权 + 标签取本箱数量 (v3.45, 默认关)
  box_label_scan_required: false,
  label_qty_enabled: false,
  label_qty_segment: 3,
  label_qty_pattern: null,
  label_rescan_action: 'ignore',
  unauthorized_cycle_action: 'hold',
  label_total_check: false,
  event_box_not_scanned: null,
  event_label_qty_missing: null,
  event_label_total_mismatch: null,
});

const form = reactive(_newForm());
const scanDeviceIdStr = ref('');
const pullConnIdStr = ref('');
const trayQtyTableJson = ref('');
const traysPerBoxTableJson = ref('');
const specToProjectJson = ref('');

// v3.43 缺工单判定方式 (二选一互斥): scan = 扫新单时判定(默认, 无时限);
// timeout = 按时限判定(扫新单不参与). 由两个落库字段映射: scan_alarm 开 = scan 模式.
const paperJudgeMode = computed({
  get: () => (!form.tail_paper_scan_alarm && (form.tail_paper_timeout_s || 0) > 0
    ? 'timeout' : 'scan'),
  set: (v) => {
    if (v === 'timeout') {
      form.tail_paper_scan_alarm = false;
      if (!form.tail_paper_timeout_s || form.tail_paper_timeout_s <= 0) {
        form.tail_paper_timeout_s = 30;
      }
    } else {
      form.tail_paper_scan_alarm = true;
      form.tail_paper_timeout_s = 0;
    }
  },
});

// insert_char 效果预览: 前端镜像后端 _normalize 的 insert_char 逻辑 (先去符号再补回固定位置)
const hyphenPreviewInput = ref('JOB1507001141');
const hyphenPreviewResult = computed(() => {
  const s = String(hyphenPreviewInput.value || '').trim();
  if (!s) return '';
  const ch = form.hyphen_template || '-';
  const pos = Number(form.hyphen_pos || 0);
  const base = s.split(ch).join('');
  if (pos > 0 && pos < base.length) {
    return base.slice(0, pos) + ch + base.slice(pos);
  }
  return base;
});

// 复合条码取段预览: 前端镜像后端 _extract_composite (按前缀认段/取第N段, 失败原样)
const compositePreviewInput = ref('');
const compositePreviewResult = computed(() => {
  const s = String(compositePreviewInput.value || '').trim();
  if (!s) return '';
  const delim = form.composite_delimiter || '|';
  if (!s.includes(delim)) return s;
  const parts = s.split(delim).map(p => p.trim()).filter(Boolean);
  if (!parts.length) return s;
  if (form.composite_pick_mode === 'index') {
    const idx = Number(form.composite_index || 1);
    return (idx >= 1 && idx <= parts.length) ? parts[idx - 1] : s;
  }
  const prefix = String(form.composite_prefix || '').trim();
  if (prefix) {
    const hits = parts.filter(p => p.startsWith(prefix));
    if (hits.length) return hits.reduce((a, b) => (b.length > a.length ? b : a));
  }
  return s;
});

const loadList = async () => {
  try {
    const { data } = await listPackagingFlows();
    flows.value = data.items || [];
  } catch (e) {
    ElMessage.error('加载包装结算列表失败: ' + (e?.response?.data?.detail || e.message));
  }
};

const _syncStrFields = () => {
  scanDeviceIdStr.value = form.scan_device_id == null ? '' : String(form.scan_device_id);
  pullConnIdStr.value = form.pull_conn_id == null ? '' : String(form.pull_conn_id);
  trayQtyTableJson.value = form.tray_qty_table ? JSON.stringify(form.tray_qty_table) : '';
  traysPerBoxTableJson.value = form.trays_per_box_table ? JSON.stringify(form.trays_per_box_table) : '';
  specToProjectJson.value = form.spec_to_project ? JSON.stringify(form.spec_to_project) : '';
};

const openCreate = () => {
  Object.assign(form, _newForm());
  _syncStrFields();
  activeGroups.value = [];
  dialogVisible.value = true;
};

const openEdit = (row) => {
  Object.assign(form, _newForm(), row);
  _syncStrFields();
  activeGroups.value = [];
  dialogVisible.value = true;
};

const applyHiwinPreset = () => {
  Object.assign(form, {
    // 上银 SY: 滑块口径 — MES 排产量(滑块总数) ÷ 每箱96(固定值) = 箱数 + 尾箱余数
    count_unit: 'sliders',
    items_per_box_source: 'config',
    items_per_box_fixed: 96,
    slider_total_field: 'dispatch_qty',
    box_count_source: 'field',
    box_count_field: 'dispatch_qty',
    // 上银: 扫码枪丢 "-" (JOB1503000213 → 第12位补回 → JOB150300021-3);
    // 主单号 JOB+9位=12, 序号 1~3 位不固定, 长度校验关 (0).
    label_match: 'insert_char',
    hyphen_template: '-',
    hyphen_pos: 12,
    label_len: 0,
    // 上银正式产线箱标签是四段拼接码 (订单|工单|数量|校验串): 按前缀 JOB 取工单段
    composite_label_enabled: true,
    composite_delimiter: '|',
    composite_pick_mode: 'prefix',
    composite_prefix: 'JOB',
    composite_index: 1,
    // 挡掉标签上并排印的数量/物料等非工单条码 (如 80.00)
    order_code_pattern: '^JOB',
    // 异常: 拉单失败 / 标签不符 → 阻断等管理员解除; 漏箱 → 重做补满
    on_mes_fail: 'block',
    on_label_mismatch: 'block',
    on_short_box: 'redo',
    on_forced_stop_partial: 'fail',
    // 停止才收尾; 待机只暂停画面不结算 (可恢复继续)
    on_forced_stop: 'settle',
    forced_settle_on_standby: false,
    // 每箱必检"放油嘴" + 尾箱封箱前必检"放工单"
    oil_nozzle_required: true,
    oil_nozzle_step_label: '放油嘴包',
    tail_paper_order_required: true,
    tail_paper_step_label: '放工单',
    tail_paper_as_close_action: true,  // v3.43 箱归周期落账, 工单等放工单收尾 (现场放工单常晚于周期结束)
    tail_paper_scan_alarm: true,       // 判定方式=扫新单(默认): 下一单扫码时判上一单放没放
    tail_paper_timeout_s: 0,           // 0 = 不用时限判定 (与扫新单二选一)
    // 完成(OK)工单重扫只提示不重开 (客户诉求: 做完的单不允许误扫再录入)
    block_completed_order_rescan: true,
    event_completed_order_rescan: 3,
    // 组⑧ 每箱扫标签放行 + 标签取本箱数量 (2026-07 现场诉求: 逐箱可变数量, 未扫开做报警)
    box_label_scan_required: true,
    label_qty_enabled: true,
    label_qty_segment: 3,           // 订单|工单|数量|校验串 → 第 3 段
    label_qty_pattern: null,
    label_rescan_action: 'ignore',
    unauthorized_cycle_action: 'hold',
    label_total_check: true,
    event_box_not_scanned: 3,
    event_label_qty_missing: 3,
    event_label_total_mismatch: 3,

    // 按规格自动切项目: 项目直接以规格命名(如 SYS1)即可零配置切换; 名不一致再填映射表
    auto_switch_project: true,
    match_project_by_name: true,
    name_match_strict_boundary: false,
    // 异常 → 项目事件映射 (要求项目事件表: 1=合格 2=不良 3=包装异常-需人工确认)
    event_short_box: 3,
    event_over_box: 3,
    event_box_ng: 3,
    event_label_mismatch: 3,
    event_mes_fail: 3,
    event_missing_paper: 2,   // 缺工单按不良报 (声光+计数)
    event_missing_nozzle: 3,

    push_on_complete: false,
    push_event_type: 'packaging_complete',
  });
  _syncStrFields();
  ElMessage.success('已套用上银 SY 滑块口径预设 (含事件映射/自动切项目/重扫拦截); 只需手动配: 工位、扫码器、拉单连接');
};

const _parseIntOrNull = (s) => {
  const t = (s || '').trim();
  if (!t) return null;
  const n = parseInt(t, 10);
  return Number.isNaN(n) ? null : n;
};

const submit = async () => {
  if (!form.name.trim()) {
    ElMessage.error('配置名称不能为空');
    return;
  }
  form.scan_device_id = _parseIntOrNull(scanDeviceIdStr.value);
  form.pull_conn_id = _parseIntOrNull(pullConnIdStr.value);

  // by_spec 表 JSON 解析
  if (form.trays_per_box_mode === 'by_spec' && traysPerBoxTableJson.value.trim()) {
    try {
      form.trays_per_box_table = JSON.parse(traysPerBoxTableJson.value);
    } catch (e) {
      ElMessage.error('每箱托盘数表 JSON 解析失败: ' + e.message);
      return;
    }
  } else {
    form.trays_per_box_table = null;
  }
  if (form.tray_qty_mode === 'by_spec' && trayQtyTableJson.value.trim()) {
    try {
      form.tray_qty_table = JSON.parse(trayQtyTableJson.value);
    } catch (e) {
      ElMessage.error('每托盘数量表 JSON 解析失败: ' + e.message);
      return;
    }
  } else {
    form.tray_qty_table = null;
  }

  // 组⑦ 规格→项目映射 JSON 解析 (仅 sliders + 自动切项目时)
  if (form.count_unit === 'sliders' && form.auto_switch_project && specToProjectJson.value.trim()) {
    try {
      form.spec_to_project = JSON.parse(specToProjectJson.value);
    } catch (e) {
      ElMessage.error('规格→项目映射 JSON 解析失败: ' + e.message);
      return;
    }
  } else {
    form.spec_to_project = null;
  }

  try {
    if (form.id) {
      await updatePackagingFlow(form.id, form);
      dbg('mes.packaging', '更新包装配置', `id=${form.id} name=${form.name} unit=${form.count_unit} enabled=${form.enabled}`);
      ElMessage.success('配置已更新');
    } else {
      const { id, ...payload } = form;
      await createPackagingFlow(payload);
      dbg('mes.packaging', '创建包装配置', `name=${form.name} unit=${form.count_unit} ch=${form.channel_id}`);
      ElMessage.success('配置已创建');
    }
    dialogVisible.value = false;
    loadList();
  } catch (e) {
    ElMessage.error('保存失败: ' + (e?.response?.data?.detail || e.message));
  }
};

const toggleEnabled = async (row, val) => {
  try {
    await updatePackagingFlow(row.id, { enabled: val });
    dbg('mes.packaging', val ? '启用包装配置' : '禁用包装配置', `id=${row.id} name=${row.name}`);
    ElMessage.success(val ? '已启用' : '已禁用');
    loadList();
  } catch (e) {
    ElMessage.error('切换失败: ' + (e?.response?.data?.detail || e.message));
    loadList();
  }
};

const del = async (row) => {
  try {
    await ElMessageBox.confirm(`确认删除配置 "${row.name}"?`, '提示', { type: 'warning' });
    await deletePackagingFlow(row.id);
    ElMessage.success('已删除');
    loadList();
  } catch (e) {
    if (e !== 'cancel') {
      ElMessage.error('删除失败: ' + (e?.response?.data?.detail || e.message));
    }
  }
};

const viewState = async (row) => {
  try {
    const { data } = await getPackagingFlowState(row.id);
    stateData.value = data;
    stateDialogVisible.value = true;
  } catch (e) {
    ElMessage.error('查询状态失败: ' + (e?.response?.data?.detail || e.message));
  }
};

onMounted(() => {
  loadList();
  loadEventOptions();
});
</script>

<style scoped>
.packaging-flow-panel {
  color: white;
}
.preset-bar {
  padding: 8px 10px;
  background: rgba(255, 255, 255, 0.04);
  border-radius: 6px;
}
</style>
