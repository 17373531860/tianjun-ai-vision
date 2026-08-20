<template>
        <div class="space-y-6 p-4">
          <!-- 基本信息设置 -->
          <el-card shadow="never" class="bg-slate-800 border-slate-700">
            <template #header>
              <div class="flex items-center gap-2">
                <el-icon class="text-tech-blue"><Edit /></el-icon>
                <span class="font-bold text-white">基本信息设置</span>
              </div>
            </template>
            <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
              <div class="p-3 bg-slate-900 rounded border border-slate-800">
                <div class="flex items-center justify-between mb-2">
                  <span class="text-gray-300">品牌/系统名称</span>
                  <el-switch v-model="store.display.navbar.brandName" @change="saveDisplaySettings" />
                </div>
                <el-input v-model="store.display.brandName" placeholder="天军科技AI" @change="saveDisplaySettings" />
              </div>
              <div class="p-3 bg-slate-900 rounded border border-slate-800">
                <div class="flex items-center justify-between mb-2">
                  <span class="text-gray-300">软件名称</span>
                  <el-switch v-model="store.display.navbar.appName" @change="saveDisplaySettings" />
                </div>
                <el-input v-model="store.display.appName" placeholder="视觉AI行为引导系统" @change="saveDisplaySettings" />
              </div>
              <div class="p-3 bg-slate-900 rounded border border-slate-800">
                <div class="flex items-center justify-between mb-2">
                  <span class="text-gray-300">当前作业员</span>
                  <el-switch v-model="store.display.navbar.inspector" @change="saveDisplaySettings" />
                </div>
                <el-input v-model="store.display.inspectorName" placeholder="张三" @change="saveDisplaySettings" />
              </div>
              <div class="p-3 bg-slate-900 rounded border border-slate-800">
                <div class="flex items-center justify-between mb-2">
                  <span class="text-gray-300">设备编号</span>
                  <el-switch v-model="store.display.navbar.deviceId" @change="saveDisplaySettings" />
                </div>
                <el-input v-model="store.display.deviceNumber" placeholder="251011" @change="saveDisplaySettings" />
              </div>
              <div class="p-3 bg-slate-900 rounded border border-slate-800">
                <div class="flex items-center justify-between mb-2">
                  <span class="text-gray-300">导航栏 Logo</span>
                  <el-button v-if="store.display.logoDataUrl" size="small" link type="danger" @click="resetNavbarLogo">恢复默认</el-button>
                </div>
                <div class="flex items-center gap-3">
                  <img :src="store.display.logoDataUrl || defaultLogoUrl" alt="logo预览"
                    class="w-10 h-10 rounded-full object-cover border border-slate-700 shrink-0" />
                  <el-button size="small" @click="logoFileInput && logoFileInput.click()">上传图片</el-button>
                  <input ref="logoFileInput" type="file" accept="image/*" class="hidden" @change="onNavbarLogoChange" />
                </div>
                <div class="text-xs text-gray-500 mt-2">建议正方形图片，自动裁剪压缩至 256×256，导航栏按圆形显示</div>
              </div>
            </div>
          </el-card>
          
          <!-- Navbar Settings -->
          <el-card shadow="never" class="bg-slate-800 border-slate-700">
            <template #header>
              <div class="flex items-center gap-2">
                <el-icon class="text-tech-blue"><Top /></el-icon>
                <span class="font-bold text-white">顶部导航栏显示</span>
              </div>
            </template>
            <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <span class="text-gray-300">项目选择板块</span>
                <el-switch v-model="store.display.navbar.projectSelector" @change="saveDisplaySettings" />
              </div>
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <span class="text-gray-300">当前模式</span>
                <el-switch v-model="store.display.navbar.mode" @change="saveDisplaySettings" />
              </div>
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <span class="text-gray-300">运行状态</span>
                <el-switch v-model="store.display.navbar.status" @change="saveDisplaySettings" />
              </div>
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <span class="text-gray-300">运行时间</span>
                <el-switch v-model="store.display.navbar.runtime" @change="saveDisplaySettings" />
              </div>
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <span class="text-gray-300">实时时间</span>
                <el-switch v-model="store.display.navbar.realtime" @change="saveDisplaySettings" />
              </div>
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <el-tooltip content="当前项目在数据设置里启用了班次拆分时，顶栏作业员旁显示当前所属班次" placement="top">
                  <span class="text-gray-300 cursor-help border-b border-dashed border-gray-600">当前班次</span>
                </el-tooltip>
                <el-switch v-model="store.display.navbar.shift" @change="saveDisplaySettings" />
              </div>
            </div>
          </el-card>

          <!-- Monitor Settings -->
          <el-card shadow="never" class="bg-slate-800 border-slate-700">
            <template #header>
              <div class="flex items-center gap-2">
                <el-icon class="text-tech-blue"><Monitor /></el-icon>
                <span class="font-bold text-white">检测中心显示</span>
              </div>
            </template>
            <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <span class="text-gray-300">SOP流程条</span>
                <el-switch v-model="store.display.monitor.stepStrip" @change="saveDisplaySettings" />
              </div>
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <span class="text-gray-300">右侧统计数据面板</span>
                <el-switch v-model="store.display.monitor.statsPanel" @change="saveDisplaySettings" />
              </div>
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <span class="text-gray-300">不良统计图表</span>
                <el-switch v-model="store.display.monitor.defectChart" @change="saveDisplaySettings" />
              </div>
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <span class="text-gray-300">产能完成图表</span>
                <el-switch v-model="store.display.monitor.capacityChart" @change="saveDisplaySettings" />
              </div>
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <span class="text-gray-300">步骤统计表格</span>
                <el-switch v-model="store.display.monitor.stepTable" @change="saveDisplaySettings" />
              </div>
              <div v-if="store.display.monitor.stepTable" class="ml-3 pl-3 border-l-2 border-slate-700 space-y-2">
                <div class="text-xs text-gray-500 px-1">表格列显示（默认全开）</div>
                <div class="flex items-center justify-between p-2 bg-slate-900/80 rounded border border-slate-800">
                  <span class="text-gray-300 text-sm">序号</span>
                  <el-switch v-model="store.display.monitor.stepTableColumns.showNo" @change="saveDisplaySettings" />
                </div>
                <div class="flex items-center justify-between p-2 bg-slate-900/80 rounded border border-slate-800">
                  <span class="text-gray-300 text-sm">步骤</span>
                  <el-switch v-model="store.display.monitor.stepTableColumns.showStep" @change="saveDisplaySettings" />
                </div>
                <div class="flex items-center justify-between p-2 bg-slate-900/80 rounded border border-slate-800">
                  <span class="text-gray-300 text-sm">状态</span>
                  <el-switch v-model="store.display.monitor.stepTableColumns.showStatus" @change="saveDisplaySettings" />
                </div>
                <div class="flex items-center justify-between p-2 bg-slate-900/80 rounded border border-slate-800">
                  <span class="text-gray-300 text-sm">PT/s</span>
                  <el-switch v-model="store.display.monitor.stepTableColumns.showPt" @change="saveDisplaySettings" />
                </div>
                <div class="flex items-center justify-between p-2 bg-slate-900/80 rounded border border-slate-800">
                  <span class="text-gray-300 text-sm">结果</span>
                  <el-switch v-model="store.display.monitor.stepTableColumns.showResult" @change="saveDisplaySettings" />
                </div>
              </div>
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <span class="text-gray-300">FPS</span>
                <el-switch v-model="store.display.monitor.showFps" @change="saveDisplaySettings" />
              </div>
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <span class="text-gray-300">延迟</span>
                <el-switch v-model="store.display.monitor.showLatency" @change="saveDisplaySettings" />
              </div>
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <span class="text-gray-300">检测数</span>
                <el-switch v-model="store.display.monitor.showDetectionCount" @change="saveDisplaySettings" />
              </div>
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <div class="flex flex-col">
                  <span class="text-gray-300">旁路 SN 码</span>
                  <span class="text-[10px] text-gray-500">扫码器写 txt 到固定目录时显示当前序列号</span>
                </div>
                <el-switch v-model="store.display.monitor.showBypassSn" @change="saveDisplaySettings" />
              </div>
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <div class="flex flex-col">
                  <span class="text-gray-300">扫码操作按钮</span>
                  <span class="text-[10px] text-gray-500">关 = 隐藏"清除本次扫码 / 禁用扫码"（操作员不碰软件的部署）</span>
                </div>
                <el-switch v-model="store.display.monitor.showScanButtons" @change="saveDisplaySettings" />
              </div>
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <span class="text-gray-300">CT包含NG周期</span>
                <el-switch v-model="store.display.monitor.ctIncludeNg" @change="saveDisplaySettings" />
              </div>
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <div class="flex flex-col">
                  <span class="text-gray-300">PT 显示口径</span>
                  <span class="text-[10px] text-gray-500">步骤耗时取哪一轮</span>
                </div>
                <el-select v-model="store.display.monitor.ptMode" size="small" style="width: 8rem" @change="saveDisplaySettings">
                  <el-option label="平均" value="avg" />
                  <el-option label="最近一轮" value="last" />
                  <el-option label="当前周期内" value="current" />
                </el-select>
              </div>
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <div class="flex flex-col">
                  <span class="text-gray-300">PT 计算方式</span>
                  <span class="text-[10px] text-gray-500">同步骤一周期内多次出现：合并(SUM) / 最后一次</span>
                </div>
                <el-select v-model="store.display.monitor.ptAggregate" size="small" style="width: 8rem" @change="saveDisplaySettings">
                  <el-option label="合并" value="sum" />
                  <el-option label="最后一次" value="last" />
                </el-select>
              </div>
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800"
                   v-if="store.display.monitor.ptAggregate === 'sum'">
                <div class="flex flex-col">
                  <span class="text-gray-300">PT 多段合并策略</span>
                  <span class="text-[10px] text-gray-500">总和=累加所有段 (默认, 现场识别准时贴近真实); 取最长=抗 YOLO 抖动累加; 仅首段=accept_once 步骤适用</span>
                </div>
                <el-select v-model="store.display.monitor.ptAccumulateStrategy" size="small" style="width: 9rem" @change="saveDisplaySettings">
                  <el-option label="总和（默认）" value="sum" />
                  <el-option label="取最长" value="max" />
                  <el-option label="仅首段" value="first_only" />
                </el-select>
              </div>
              <!-- v3.9.x D 方案: PT 计算口径 (跨度 / 累计可见时长) -->
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <div class="flex flex-col">
                  <span class="text-gray-300">PT 计算口径</span>
                  <span class="text-[10px] text-gray-500">跨度=首次出现到消失之间; 累计可见=每帧"标签在画面里"时长之和</span>
                </div>
                <el-select v-model="store.display.monitor.ptCalcMode" size="small" style="width: 9rem" @change="saveDisplaySettings">
                  <el-option label="跨度（默认）" value="span" />
                  <el-option label="累计可见时长" value="visible" />
                </el-select>
              </div>
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <div class="flex flex-col">
                  <span class="text-gray-300">CT 显示口径</span>
                  <span class="text-[10px] text-gray-500">周期时间取哪一轮</span>
                </div>
                <el-select v-model="store.display.monitor.ctMode" size="small" style="width: 8rem" @change="saveDisplaySettings">
                  <el-option label="平均" value="avg" />
                  <el-option label="最近一轮" value="last" />
                  <el-option label="当前周期内" value="current" />
                </el-select>
              </div>
              <!-- v3.9.x A 方案: 结算后强制保留显示 (默认关) -->
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <div class="flex flex-col">
                  <span class="text-gray-300">结算后强制保留显示</span>
                  <span class="text-[10px] text-gray-500">周期结算后保留 OK + PT 数字, 多停留 N 秒再切下一周期</span>
                </div>
                <div class="flex items-center gap-2">
                  <el-switch v-model="store.display.monitor.resultHoldEnabled" @change="saveDisplaySettings" />
                  <el-input-number
                    v-model="store.display.monitor.resultHoldSeconds"
                    size="small"
                    :min="0"
                    :max="10"
                    :step="0.1"
                    :precision="1"
                    style="width: 7rem"
                    :disabled="!store.display.monitor.resultHoldEnabled"
                    @change="saveDisplaySettings"
                  />
                  <span class="text-xs text-gray-500">秒</span>
                </div>
              </div>
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <span class="text-gray-300">NG步骤TOP3</span>
                <el-switch v-model="store.display.monitor.ngTop3" @change="saveDisplaySettings" />
              </div>
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <span class="text-gray-300">NG TOP3 显示模式</span>
                <el-select v-model="store.display.monitor.ngTopDisplayMode" size="small" style="width: 6.25rem" @change="saveDisplaySettings">
                  <el-option label="百分比" value="percentage" />
                  <el-option label="次数" value="count" />
                </el-select>
              </div>
            </div>
          </el-card>

          <!-- v3.22.x: 开机自动恢复检测开关 -->
          <el-card shadow="never" class="bg-slate-800 border-slate-700">
            <template #header>
              <div class="flex items-center gap-2">
                <el-icon class="text-cyan-400"><Refresh /></el-icon>
                <span class="font-bold text-white">开机自动恢复检测</span>
              </div>
            </template>
            <div class="mb-3 text-xs text-gray-500">
              软件启动时是否自动开始检测。<br>
              开启（默认）= 开机后自动恢复上次的项目 + 视频源，并<b>无条件自动开始检测</b>（不管上次是否在检测），工人无需手动点开始；<br>
              关闭 = 开机只恢复项目 + 视频源，停在待机，由工人确认后手动点「开始」。<br>
              修改后<b>下次启动</b>生效。
            </div>
            <div class="space-y-2">
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <div class="flex flex-col">
                  <span class="text-gray-300">开机自动开始检测</span>
                  <span class="text-[10px] text-gray-500">关闭后开机停在待机，需工人手动点开始</span>
                </div>
                <el-switch
                  v-model="autoResumeEnabled"
                  data-testid="auto-resume-switch"
                  @change="onAutoResumeChange"
                />
              </div>
            </div>
          </el-card>

          <!-- v3.51: 激活项目收养策略 (多工位多项目部署建议关) -->
          <el-card shadow="never" class="bg-slate-800 border-slate-700">
            <template #header>
              <div class="flex items-center gap-2">
                <el-icon class="text-cyan-400"><Connection /></el-icon>
                <span class="font-bold text-white">启用项目时自动接管未绑定工位</span>
              </div>
            </template>
            <div class="mb-3 text-xs text-gray-500">
              在项目管理页「启用」一个项目时，未绑定任何项目的工位如何处理。<br>
              开启（默认）= 未绑定工位被自动接管进该项目，并<b>写死绑定</b>（单项目部署方便）；<br>
              关闭 = 启用项目只影响<b>已绑定该项目</b>的工位，未绑定工位不动 —— <b>多工位跑不同项目/不同模型时强烈建议关闭</b>，
              防止在某个项目里改配置后另一个工位的项目被顶掉。
            </div>
            <div class="space-y-2">
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <div class="flex flex-col">
                  <span class="text-gray-300">自动接管未绑定工位</span>
                  <span class="text-[10px] text-gray-500">多工位多项目部署建议关闭</span>
                </div>
                <el-switch
                  v-model="adoptUnboundEnabled"
                  data-testid="adopt-unbound-switch"
                  @change="onAdoptUnboundChange"
                />
              </div>
            </div>
          </el-card>

          <!-- v3.23.x: 加深启动就绪门槛 (默认关) -->
          <el-card shadow="never" class="bg-slate-800 border-slate-700">
            <template #header>
              <div class="flex items-center gap-2">
                <el-icon class="text-cyan-400"><Loading /></el-icon>
                <span class="font-bold text-white">加深启动就绪门槛</span>
              </div>
            </template>
            <div class="mb-3 text-xs text-gray-500">
              控制开机时「启动动画放主界面进来」的时机。<br>
              关闭（默认）= 主界面尽快出现，首屏数据若撞上后端冷启动会<b>自动重试补齐</b>，不会空白；<br>
              开启 = 一直等到后端<b>深度就绪（数据库 + 项目能查到）</b>再放主界面进来，进来即一切就绪、连那一两秒重试都省；代价是开机要<b>多等几秒</b>动画。<br>
              修改后<b>下次开机</b>由启动程序读取生效。
            </div>
            <div class="space-y-2">
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <div class="flex flex-col">
                  <span class="text-gray-300">开机等后端深度就绪再进主界面</span>
                  <span class="text-[10px] text-gray-500">关闭 = 尽快进入 + 首屏失败自动重试；开启 = 多等几秒但进来即满</span>
                </div>
                <el-switch
                  v-model="startupReadyGateEnabled"
                  data-testid="startup-ready-gate-switch"
                  @change="onStartupReadyGateChange"
                />
              </div>
            </div>
          </el-card>

          <!-- B1②: MES 外推并发派发 (v3.49 起默认开) -->
          <el-card shadow="never" class="bg-slate-800 border-slate-700">
            <template #header>
              <div class="flex items-center gap-2">
                <el-icon class="text-cyan-400"><Lightning /></el-icon>
                <span class="font-bold text-white">MES 外推并发派发</span>
              </div>
            </template>
            <div class="mb-3 text-xs text-gray-500">
              控制把检测结果推送给外部 MES 系统的方式。<br>
              开启（默认，v3.49 起）= 每个工位<b>独立线程推送</b>，同工位严格保序，<b>某个工位的 MES 慢/断连不再拖累其它工位和扫码流程</b>；长时间断连时积压推送自动落盘，恢复后补发不丢单。<br>
              关闭 = 在统一队列里<b>顺序推送</b>，与旧版一致；若客户 MES 系统响应慢或断连，重试期间会拖慢扫码配对等其它处理，仅在需要严格复现旧行为时使用。<br>
              修改后<b>立即生效</b>。
            </div>
            <div class="space-y-2">
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <div class="flex flex-col">
                  <span class="text-gray-300">每工位独立线程推送外部 MES</span>
                  <span class="text-[10px] text-gray-500">开启（默认）= 慢 MES 不拖累其它工位，断连积压落盘补发；关闭 = 统一队列顺序推（旧行为）</span>
                </div>
                <el-switch
                  v-model="mesAsyncDispatchEnabled"
                  data-testid="mes-async-dispatch-switch"
                  @change="onMesAsyncDispatchChange"
                />
              </div>
              <!-- v3.49 WS3: scan_pair 新码先上屏 -->
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <div class="flex flex-col">
                  <span class="text-gray-300">扫码配对：新条码立即上屏</span>
                  <span class="text-[10px] text-gray-500">开启（默认）= 扫到新码先显示、上一箱结算在后台完成，工人不用等；关闭 = 先结算完上一箱再显示新码（旧行为，排查用）</span>
                </div>
                <el-switch
                  v-model="scanPairNewFirstEnabled"
                  data-testid="scan-pair-new-first-switch"
                  @change="onScanPairNewFirstChange"
                />
              </div>
            </div>
          </el-card>

          <!-- v3.10.x: 窗口模式 (主窗口全屏 / 窗口 + 最小化) -->
          <el-card shadow="never" class="bg-slate-800 border-slate-700">
            <template #header>
              <div class="flex items-center gap-2">
                <el-icon class="text-cyan-400"><Monitor /></el-icon>
                <span class="font-bold text-white">窗口模式</span>
                <el-tag size="small" type="info">仅打包桌面版生效</el-tag>
              </div>
            </template>
            <div class="mb-3 text-xs text-gray-500">
              控制主窗口的显示模式。默认窗口模式带 Windows 原生最小化 / 最大化 / 关闭三件套；全屏模式去掉标题栏 (kiosk 风格)。<br>
              全屏开关立即生效；下次启动也会按此设置走。
            </div>
            <div class="space-y-2">
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <div class="flex flex-col">
                  <span class="text-gray-300">主窗口全屏</span>
                  <span class="text-[10px] text-gray-500">关闭 = 1600×900 窗口模式带原生标题栏；启用 = 占满显示器</span>
                </div>
                <el-switch
                  v-model="windowFullscreen"
                  data-testid="window-fullscreen-switch"
                  :disabled="!isElectronEnv"
                  @change="onWindowFullscreenChange"
                />
              </div>
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <div class="flex flex-col">
                  <span class="text-gray-300">最小化到任务栏</span>
                  <span class="text-[10px] text-gray-500">把主界面收到 Windows 任务栏 (后端继续运行, 不退出应用)</span>
                </div>
                <el-button
                  type="primary"
                  size="small"
                  data-testid="window-minimize-btn"
                  :disabled="!isElectronEnv"
                  @click="onMinimizeWindow"
                >
                  <el-icon class="mr-1"><Minus /></el-icon>最小化
                </el-button>
              </div>
              <div v-if="!isElectronEnv" class="text-[10px] text-amber-400">
                ⚠ 当前在浏览器中预览, 窗口控制按钮仅在打包桌面版下可用。
              </div>
            </div>
          </el-card>

          <!-- 多屏工位显示已迁到「工位与输入源」页（MultiMonitorPanel），与工位数/工位组同域 -->

          <!-- v3.9.x / v3.10.x: 启动动画 (打包安装版的 Cyber Splash 用) -->
          <el-card shadow="never" class="bg-slate-800 border-slate-700">
            <template #header>
              <div class="flex items-center gap-2">
                <el-icon class="text-cyan-400"><VideoCamera /></el-icon>
                <span class="font-bold text-white">启动动画</span>
                <el-tag size="small" type="info">仅打包桌面版生效</el-tag>
              </div>
            </template>
            <div class="mb-3 text-xs text-gray-500">
              客户端启动时的赛博粒子球动画通过摄像头识别"握拳"手势进入主界面。<br>
              默认关闭, 直接进主程序; 启用后, 工厂场景下若有 Todesk / 向日葵等远程虚拟相机, 老逻辑可能误选, 可在下方"指定相机"避免。<br>
              本设置仅影响打包安装后的启动动画, 不影响检测中心的视频源。开关切换需<b>重启应用</b>生效。
            </div>
            <div class="space-y-2">
              <!-- v3.10.x: 启动动画总开关 -->
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <div class="flex flex-col">
                  <span class="text-gray-300">启用启动动画</span>
                  <span class="text-[10px] text-gray-500">关闭 = 直接进主程序 (默认); 启用 = 播放粒子球 + 手势识别</span>
                </div>
                <el-switch
                  v-model="splashCamera.enabled"
                  data-testid="splash-enabled-switch"
                  @change="saveSplashCameraConfig"
                />
              </div>
              <div v-if="splashCamera.enabled" class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <div class="flex flex-col">
                  <span class="text-gray-300">手势识别模式</span>
                  <span class="text-[10px] text-gray-500">自动=工位 1 已绑 USB 就用它, 否则系统默认; 指定=锁定一台; 关闭=直接自动播放, 不开摄像头</span>
                </div>
                <el-select v-model="splashCamera.camera_mode" size="small" style="width: 9rem" @change="onSplashModeChange">
                  <el-option label="自动" value="auto" />
                  <el-option label="指定相机" value="specific" />
                  <el-option label="关闭手势" value="disabled" />
                </el-select>
              </div>
              <div v-if="splashCamera.enabled && splashCamera.camera_mode === 'specific'" class="flex flex-col gap-2 p-3 bg-slate-900 rounded border border-slate-800">
                <div class="flex items-center justify-between gap-2">
                  <span class="text-gray-300 whitespace-nowrap">指定的相机</span>
                  <span class="text-xs text-gray-400 truncate flex-1 text-right" :title="splashCamera.device_label || splashCamera.device_id">
                    {{ splashCamera.device_label || (splashCamera.device_id ? '已锁定 (无名称)' : '未选择') }}
                  </span>
                </div>
                <div class="flex items-center justify-end gap-2">
                  <el-button size="small" :loading="splashCameraScanning" @click="enumerateSplashCameras">
                    扫描可用相机
                  </el-button>
                  <el-button size="small" type="primary" :disabled="!splashCameraAvailable.length" @click="openSplashCameraDialog">
                    选择...
                  </el-button>
                </div>
                <div class="text-[10px] text-gray-500">
                  浏览器要求拿到摄像头授权后才能列出名称；首次扫描会弹一次系统的相机权限申请。
                </div>
              </div>

              <!-- v3.9.x: 启动动画闲置超时 (任意 camera_mode 都生效, 仅 splash 启用时显示) -->
              <div
                v-if="splashCamera.enabled"
                data-testid="splash-idle-section"
                class="flex flex-col gap-2 p-3 bg-slate-900 rounded border border-slate-800"
              >
                <div class="flex items-center justify-between">
                  <div class="flex flex-col">
                    <span class="text-gray-300">闲置自动跳过</span>
                    <span class="text-[10px] text-gray-500">客户工厂工控机无人值守时，自动跳过手势直接进主程序</span>
                  </div>
                  <el-switch
                    v-model="splashIdleEnabled"
                    data-testid="splash-idle-switch"
                    inline-prompt
                    active-text="启用"
                    inactive-text="永不"
                    @change="onSplashIdleEnabledChange"
                  />
                </div>
                <div v-if="splashIdleEnabled" class="flex items-center justify-between">
                  <span class="text-gray-300">超时秒数</span>
                  <div class="flex items-center gap-2">
                    <el-input-number
                      v-model="splashCamera.idle_timeout_sec"
                      data-testid="splash-idle-seconds"
                      :min="10"
                      :max="3600"
                      :step="60"
                      size="small"
                      controls-position="right"
                      style="width: 7.5rem"
                      @change="saveSplashCameraConfig"
                    />
                    <span class="text-xs text-gray-400 whitespace-nowrap">秒（默认 600 = 10 分钟）</span>
                  </div>
                </div>
                <div class="text-[10px] text-gray-500 leading-snug">
                  ⚠️ 建议至少 60 秒以上：后端模型加载在性能弱的工控机上可能要 30-60 秒，期间启动动画处于等待状态。配置过短可能在后端就绪前就强制跳过。
                </div>
              </div>
            </div>
          </el-card>

          <!-- 启动动画相机选择对话框 -->
          <el-dialog v-model="splashCameraDialogOpen" title="选择启动动画使用的相机" width="36rem" align-center>
            <div class="space-y-2 max-h-96 overflow-y-auto">
              <div
                v-for="cam in splashCameraAvailable"
                :key="cam.deviceId"
                class="flex items-center justify-between p-3 bg-slate-900 rounded border cursor-pointer hover:border-cyan-600"
                :class="splashCameraDialogPick === cam.deviceId ? 'border-cyan-500' : 'border-slate-800'"
                @click="splashCameraDialogPick = cam.deviceId"
              >
                <div class="flex flex-col flex-1 min-w-0">
                  <span class="text-gray-200 truncate">{{ cam.label || '未知相机' }}</span>
                  <span class="text-[10px] text-gray-500 truncate">{{ cam.deviceId.slice(0, 24) }}...</span>
                </div>
                <el-icon v-if="splashCameraDialogPick === cam.deviceId" class="text-cyan-400 ml-2"><Aim /></el-icon>
              </div>
              <div v-if="!splashCameraAvailable.length" class="p-4 text-center text-gray-500 text-sm">
                未扫到任何摄像头。请先点"扫描可用相机"并允许浏览器访问。
              </div>
            </div>
            <template #footer>
              <el-button @click="splashCameraDialogOpen = false">取消</el-button>
              <el-button type="primary" :disabled="!splashCameraDialogPick" @click="confirmSplashCameraPick">
                确定使用此相机
              </el-button>
            </template>
          </el-dialog>

          <!-- 默认计数器显示设置 -->
          <el-card shadow="never" class="bg-slate-800 border-slate-700">
            <template #header>
              <div class="flex items-center gap-2">
                <el-icon class="text-cyan-400"><DataLine /></el-icon>
                <span class="font-bold text-white">默认计数器显示</span>
                <el-tag size="small" type="info">系统内置</el-tag>
              </div>
            </template>
            <div class="mb-3 text-xs text-gray-500">
              以下是系统内置的默认计数器，可以选择在检测中心是否显示。自定义计数器需要在项目管理中配置，且与事件绑定后才会显示。
            </div>
            <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <div class="flex items-center gap-2">
                  <span class="w-2 h-2 rounded-full bg-cyan-400"></span>
                  <span class="text-gray-300">检测次数（总产量）</span>
                </div>
                <el-switch v-model="store.display.monitor.defaultCounters.showTotal" @change="saveDisplaySettings" />
              </div>
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <div class="flex items-center gap-2">
                  <span class="w-2 h-2 rounded-full bg-green-500"></span>
                  <span class="text-gray-300">OK次数（合格总数）</span>
                </div>
                <el-switch v-model="store.display.monitor.defaultCounters.showGood" @change="saveDisplaySettings" />
              </div>
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <div class="flex items-center gap-2">
                  <span class="w-2 h-2 rounded-full bg-red-500"></span>
                  <span class="text-gray-300">NG次数（不良总数）</span>
                </div>
                <el-switch v-model="store.display.monitor.defaultCounters.showBad" @change="saveDisplaySettings" />
              </div>
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <div class="flex items-center gap-2">
                  <span class="w-2 h-2 rounded-full bg-orange-500"></span>
                  <span class="text-gray-300">NG步骤</span>
                </div>
                <el-switch v-model="store.display.monitor.defaultCounters.showNgSteps" @change="saveDisplaySettings" />
              </div>
            </div>
            <el-alert
              title="NG步骤说明"
              type="warning"
              :closable="false"
              show-icon
              class="mt-4"
            >
              <template #default>
                <div class="text-xs text-gray-300">
                  <p>• <strong>NG步骤</strong>：在顺序检测模式或基于顺序的自定义模式中自动计数</p>
                  <p>• 只统计<strong>漏做的步骤</strong>（缺少步骤），按缺少数量计入</p>
                  <p>• 此计数器<strong>不能设置默认数量</strong>，但可以在事件中 +1、-1 等操作</p>
                </div>
              </template>
            </el-alert>
          </el-card>

          <!-- 画面变换（旋转 + 镜像；检测框随画面一起转，每通道独立） -->
          <el-card shadow="never" class="bg-slate-800 border-slate-700">
            <template #header>
              <div class="flex items-center gap-2">
                <el-icon class="text-tech-blue"><Refresh /></el-icon>
                <span class="font-bold text-white">画面变换</span>
                <el-tag size="small" type="info">每通道独立</el-tag>
              </div>
            </template>
            <div class="space-y-4">
              <div class="flex items-center gap-3">
                <span class="text-gray-300 whitespace-nowrap">工位通道</span>
                <el-select v-model="transformChannel" size="default" style="width: 10rem" @change="loadTransformConfig">
                  <el-option v-for="n in Math.max(transformTotalChannels, 1)" :key="n - 1" :label="`工位 ${n}`" :value="n - 1" />
                </el-select>
                <el-button size="small" @click="loadTransformConfig" :loading="transformLoading">
                  <el-icon class="mr-1"><Refresh /></el-icon>刷新
                </el-button>
              </div>
              <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
                <div class="p-3 bg-slate-900 rounded border border-slate-800">
                  <div class="flex items-center justify-between mb-2">
                    <span class="text-gray-300">旋转角度</span>
                  </div>
                  <el-select v-model="transformForm.rotation" size="default" class="w-full">
                    <el-option label="0°（不旋转）" :value="0" />
                    <el-option label="90°（顺时针）" :value="90" />
                    <el-option label="180°" :value="180" />
                    <el-option label="270°（逆时针 90°）" :value="270" />
                  </el-select>
                </div>
                <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                  <span class="text-gray-300">左右镜像</span>
                  <el-switch v-model="transformForm.flip_h" />
                </div>
                <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                  <span class="text-gray-300">上下镜像</span>
                  <el-switch v-model="transformForm.flip_v" />
                </div>
              </div>
              <div class="flex items-center justify-between">
                <span class="text-xs text-gray-400">变换发生在帧采集之后、推理之前；检测框、录像、MJPEG 流都会跟随一起转/镜像。</span>
                <el-button type="primary" size="default" @click="saveTransformConfig" :loading="transformSaving">
                  保存此工位设置
                </el-button>
              </div>
            </div>
          </el-card>
        </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue';
import { useSystemStore } from '@/store/useSystemStore';
import { Top, Monitor, Edit, VideoCamera, Refresh, DataLine, Lightning, Aim, Minus, Loading, Connection } from '@element-plus/icons-vue';
import { ElMessage } from 'element-plus';
import api from '@/api/index';
import { dbg, dbgErr } from '@/utils/debug';

const store = useSystemStore();

const saveDisplaySettings = () => {
  dbg('settings.ops', '保存显示设置', `brand=${store.display?.brandName ?? ''}`);
  localStorage.setItem('display_settings', JSON.stringify(store.display));
};

// 导航栏 Logo 上传: 前端居中裁方 + 压到 256×256 存 data URL (与 display 其余字段
// 同走 localStorage 持久化)。压缩后 ~30-80KB, 远低于 localStorage 限额。
// v3.37.0: 回退地址用 BASE_URL 拼接, 不能写死 '/app-icon.png'——打包后 file:// 下会解析到盘根 (同 Navbar.vue)。
const defaultLogoUrl = import.meta.env.BASE_URL + 'app-icon.png';
const logoFileInput = ref(null);
const onNavbarLogoChange = (e) => {
  const file = e.target.files && e.target.files[0];
  e.target.value = '';   // 允许连续选同一文件重复触发
  if (!file) return;
  if (!file.type.startsWith('image/')) {
    ElMessage.error('请选择图片文件');
    return;
  }
  const img = new Image();
  const url = URL.createObjectURL(file);
  img.onload = () => {
    URL.revokeObjectURL(url);
    try {
      const size = 256;
      const canvas = document.createElement('canvas');
      canvas.width = canvas.height = size;
      const ctx = canvas.getContext('2d');
      const s = Math.min(img.width, img.height);
      ctx.drawImage(img, (img.width - s) / 2, (img.height - s) / 2, s, s, 0, 0, size, size);
      store.display.logoDataUrl = canvas.toDataURL('image/png');
      saveDisplaySettings();
      ElMessage.success('Logo 已更新');
    } catch (err) {
      ElMessage.error('图片处理失败：' + (err?.message || ''));
    }
  };
  img.onerror = () => {
    URL.revokeObjectURL(url);
    ElMessage.error('图片加载失败');
  };
  img.src = url;
};
const resetNavbarLogo = () => {
  store.display.logoDataUrl = '';
  saveDisplaySettings();
  ElMessage.info('已恢复默认 Logo');
};

// ========== 画面变换（按通道） ==========
const transformChannel = ref(0);
const transformTotalChannels = ref(1);
const transformLoading = ref(false);
const transformSaving = ref(false);
const transformForm = reactive({ rotation: 0, flip_h: false, flip_v: false });

async function loadTransformTotalChannels() {
  try {
    const res = await api.get('/workstations');
    transformTotalChannels.value = res?.data?.channel_count || 1;
  } catch {
    transformTotalChannels.value = 1;
  }
}

async function loadTransformConfig() {
  transformLoading.value = true;
  try {
    const res = await api.get(`/source/transform/config?channel=${transformChannel.value}`);
    transformForm.rotation = res?.data?.rotation || 0;
    transformForm.flip_h = !!res?.data?.flip_h;
    transformForm.flip_v = !!res?.data?.flip_v;
  } catch (e) {
    ElMessage.error('加载画面变换失败: ' + (e?.response?.data?.detail || e?.message || ''));
  } finally {
    transformLoading.value = false;
  }
}

async function saveTransformConfig() {
  transformSaving.value = true;
  dbg('settings.ops', '保存画面变换', `ch=${transformChannel.value} rotation=${transformForm?.rotation ?? 0}`);
  try {
    await api.post(`/source/transform/config?channel=${transformChannel.value}`, {
      rotation: transformForm.rotation || 0,
      flip_h: !!transformForm.flip_h,
      flip_v: !!transformForm.flip_v,
    });
    ElMessage.success(`工位 ${transformChannel.value + 1} 画面变换已保存并生效`);
  } catch (e) {
    dbgErr('settings.ops', '保存画面变换', e);
    ElMessage.error('保存失败: ' + (e?.response?.data?.detail || e?.message || ''));
  } finally {
    transformSaving.value = false;
  }
}

// ========== v3.9.x: 启动动画手势相机配置 ==========
// 配置不放 systemStore (localStorage) 而是落盘 workstation_config.json,
// 因为 splash 比主前端先加载, 只能跨进程读 JSON, 拿不到 localStorage。
// 详见 backend/api/channel_manager.py:get_splash_config 注释。
const splashCamera = reactive({
  enabled: false,               // v3.10.x: 启动动画总开关 (默认 false, 客户要求)
  camera_mode: 'auto',          // 'auto' | 'specific' | 'disabled'
  device_id: '',
  device_label: '',
  idle_timeout_sec: 600,        // v3.9.x: 0=永不超时; >0=N 秒后强制跳过
});
const splashCameraAvailable = ref([]);     // [{deviceId, label}]
const splashCameraScanning = ref(false);
const splashCameraDialogOpen = ref(false);
const splashCameraDialogPick = ref('');
// 开关 UI 状态 — 跟 idle_timeout_sec=0 双向同步, 但单独存,
// 因为切到"永不"再切回来要恢复用户上一次填的秒数, 不能直接读 idle_timeout_sec.
const splashIdleEnabled = ref(true);

async function loadSplashCameraConfig() {
  try {
    const res = await api.get('/workstations/splash-camera');
    splashCamera.enabled = res?.data?.enabled === true;
    splashCamera.camera_mode = res?.data?.camera_mode || 'auto';
    splashCamera.device_id = res?.data?.device_id || '';
    splashCamera.device_label = res?.data?.device_label || '';
    const rawTimeout = res?.data?.idle_timeout_sec;
    const n = parseInt(rawTimeout, 10);
    if (Number.isFinite(n) && n >= 0) {
      // 0 = 永不: 开关关掉, 但保留默认 600 在 input 框里, 切回"启用"时不至于显示 0
      if (n === 0) {
        splashIdleEnabled.value = false;
        splashCamera.idle_timeout_sec = 600;
      } else {
        splashIdleEnabled.value = true;
        splashCamera.idle_timeout_sec = n;
      }
    } else {
      splashIdleEnabled.value = true;
      splashCamera.idle_timeout_sec = 600;
    }
  } catch (e) {
    console.warn('加载启动动画相机配置失败:', e?.message);
  }
}

async function saveSplashCameraConfig() {
  try {
    // 落盘时把 UI 开关状态翻译成 0/N 秒: 关掉开关 → 后端存 0 → splash 不启 timer
    const timeoutToSave = splashIdleEnabled.value ? (splashCamera.idle_timeout_sec || 600) : 0;
    await api.put('/workstations/splash-camera', {
      enabled: splashCamera.enabled === true,
      camera_mode: splashCamera.camera_mode,
      device_id: splashCamera.device_id || '',
      device_label: splashCamera.device_label || '',
      idle_timeout_sec: timeoutToSave,
    });
  } catch (e) {
    ElMessage.error('保存启动动画相机配置失败: ' + (e?.response?.data?.detail || e?.message || ''));
  }
}

// ========== v3.22.x: 开机自动恢复检测开关 ==========
// 落盘 workstation_config.json 顶层 auto_resume 段, 下次后端启动时读。
const autoResumeEnabled = ref(true);   // 默认开 (老行为: 开机自动恢复检测)

async function loadAutoResumeConfig() {
  try {
    const res = await api.get('/workstations/auto-resume');
    autoResumeEnabled.value = res?.data?.enabled !== false;
  } catch (e) {
    console.warn('加载开机自动恢复检测配置失败:', e?.message);
  }
}

async function onAutoResumeChange(val) {
  dbg('settings.ops', '切换开机自动恢复检测', `enabled=${!!val}`);
  try {
    await api.put('/workstations/auto-resume', { enabled: !!val });
    ElMessage.success(val ? '已开启开机自动恢复检测 (下次启动生效)' : '已关闭, 开机将停在待机');
  } catch (e) {
    ElMessage.error('保存开机自动恢复检测配置失败: ' + (e?.response?.data?.detail || e?.message || ''));
    autoResumeEnabled.value = !val;
  }
}

// ========== v3.51: 激活项目收养未绑定工位开关 ==========
// 存 SystemConfig KV activate.adopt_unbound。默认开 (存量行为)。
const adoptUnboundEnabled = ref(true);

async function loadAdoptUnboundConfig() {
  try {
    const res = await api.get('/projects/activate-config');
    adoptUnboundEnabled.value = res?.data?.adopt_unbound !== false;
  } catch (e) {
    console.warn('加载激活收养配置失败:', e?.message);
  }
}

async function onAdoptUnboundChange(val) {
  dbg('settings.ops', '切换激活收养未绑定工位', `adopt_unbound=${!!val}`);
  try {
    await api.put('/projects/activate-config', { adopt_unbound: !!val });
    ElMessage.success(val
      ? '已开启: 启用项目时自动接管未绑定工位'
      : '已关闭: 启用项目只影响已绑定该项目的工位');
  } catch (e) {
    ElMessage.error('保存失败: ' + (e?.response?.data?.detail || e?.message || ''));
    adoptUnboundEnabled.value = !val;
  }
}

// ========== v3.23.x: 加深启动就绪门槛开关 ==========
// 落盘 workstation_config.json 顶层 startup_ready_gate 段, 下次开机由 Electron 读。
const startupReadyGateEnabled = ref(false);   // 默认关 (首屏失败自动重试已是保底)

async function loadStartupReadyGateConfig() {
  try {
    const res = await api.get('/workstations/startup-ready-gate');
    startupReadyGateEnabled.value = res?.data?.enabled === true;
  } catch (e) {
    console.warn('加载加深启动就绪门槛配置失败:', e?.message);
  }
}

async function onStartupReadyGateChange(val) {
  dbg('settings.ops', '切换加深启动就绪门槛', `enabled=${!!val}`);
  try {
    await api.put('/workstations/startup-ready-gate', { enabled: !!val });
    ElMessage.success(val ? '已开启, 下次开机等后端深度就绪再进主界面 (多等几秒)' : '已关闭, 下次开机尽快进入 + 首屏失败自动重试');
  } catch (e) {
    ElMessage.error('保存加深启动就绪门槛配置失败: ' + (e?.response?.data?.detail || e?.message || ''));
    startupReadyGateEnabled.value = !val;
  }
}

// ========== B1②: MES 外推并发派发开关 (v3.49 起默认开) ==========
// 后端 SystemConfig(mes_async_dispatch) 为准, 立即生效。
const mesAsyncDispatchEnabled = ref(true);

async function loadMesAsyncDispatchConfig() {
  try {
    const res = await api.get('/mes/gateway/async-dispatch');
    mesAsyncDispatchEnabled.value = res?.data?.enabled === true;
  } catch (e) {
    console.warn('加载 MES 外推并发派发配置失败:', e?.message);
  }
}

async function onMesAsyncDispatchChange(val) {
  dbg('settings.ops', '切换 MES 外推并发派发', `enabled=${!!val}`);
  try {
    await api.put('/mes/gateway/async-dispatch', { enabled: !!val });
    ElMessage.success(val ? '已开启, 每工位独立线程推送 (慢 MES 不拖累其它工位)' : '已关闭, 恢复统一队列顺序推送');
  } catch (e) {
    ElMessage.error('保存 MES 外推并发派发配置失败: ' + (e?.response?.data?.detail || e?.message || ''));
    mesAsyncDispatchEnabled.value = !val;
  }
}

// ========== v3.49 WS3: scan_pair 新码先上屏开关 (默认开) ==========
// 后端 SystemConfig(scan_pair_new_code_first) 为准, 立即生效。
const scanPairNewFirstEnabled = ref(true);

async function loadScanPairNewFirstConfig() {
  try {
    const res = await api.get('/scanner/scan-pair/new-code-first');
    scanPairNewFirstEnabled.value = res?.data?.enabled === true;
  } catch (e) {
    console.warn('加载扫码配对新码先上屏配置失败:', e?.message);
  }
}

async function onScanPairNewFirstChange(val) {
  dbg('settings.ops', '切换扫码配对新码先上屏', `enabled=${!!val}`);
  try {
    await api.put('/scanner/scan-pair/new-code-first', { enabled: !!val });
    ElMessage.success(val ? '已开启, 扫到新码立即上屏, 上一箱结算后台完成' : '已关闭, 恢复先结算后上屏 (旧行为)');
  } catch (e) {
    ElMessage.error('保存扫码配对新码先上屏配置失败: ' + (e?.response?.data?.detail || e?.message || ''));
    scanPairNewFirstEnabled.value = !val;
  }
}

// ========== v3.10.x: 主窗口模式 (Electron) ==========
const windowFullscreen = ref(false);
const isElectronEnv = computed(() => !!(typeof window !== 'undefined' && window.electronAPI?.isElectron));

// 多屏工位显示配置已迁 Source/MultiMonitorPanel.vue（「工位与输入源」页）

async function loadWindowConfig() {
  try {
    const res = await api.get('/workstations/window-config');
    windowFullscreen.value = res?.data?.fullscreen === true;
  } catch (e) {
    console.warn('加载窗口模式配置失败:', e?.message);
  }
}

async function onWindowFullscreenChange(val) {
  dbg('settings.ops', '切换窗口模式', `fullscreen=${!!val}`);
  try {
    // 1. 持久化让下次启动生效
    await api.put('/workstations/window-config', { fullscreen: !!val });
    // 2. 立即热切当前主窗口 (仅 Electron 环境有效)
    if (typeof window !== 'undefined' && window.electronAPI?.setFullScreen) {
      const r = await window.electronAPI.setFullScreen(!!val);
      if (r?.ok) {
        ElMessage.success(val ? '已切换到全屏模式' : '已切换到窗口模式');
      } else {
        ElMessage.warning('已保存配置, 但热切失败: ' + (r?.error || '未知错误'));
      }
    } else {
      ElMessage.info('已保存配置, 下次启动生效 (当前在浏览器中预览)');
    }
  } catch (e) {
    ElMessage.error('保存窗口模式失败: ' + (e?.response?.data?.detail || e?.message || ''));
    // 回滚 UI 状态
    windowFullscreen.value = !val;
  }
}

async function onMinimizeWindow() {
  if (typeof window !== 'undefined' && window.electronAPI?.minimizeWindow) {
    const r = await window.electronAPI.minimizeWindow();
    if (!r?.ok) {
      ElMessage.warning('最小化失败: ' + (r?.error || '未知错误'));
    }
  } else {
    ElMessage.info('最小化按钮仅在打包桌面版下可用 (当前是浏览器预览)');
  }
}

const onSplashModeChange = () => {
  // 切到 auto / disabled 时清掉锁定的 device_id, 否则用户切回 specific 会看到老选择残留
  if (splashCamera.camera_mode !== 'specific') {
    splashCamera.device_id = '';
    splashCamera.device_label = '';
  }
  saveSplashCameraConfig();
};

const onSplashIdleEnabledChange = () => {
  // 开关切换时立刻持久化, 不需要用户再点保存
  saveSplashCameraConfig();
};

async function enumerateSplashCameras() {
  splashCameraScanning.value = true;
  try {
    // 浏览器隐私设计: 不先 getUserMedia 拿过授权, enumerateDevices 返回的 label 是空。
    // 这里申请一次最低分辨率的视频流, 拿到授权后立即关掉, 仅为获取设备名称。
    let tempStream = null;
    try {
      tempStream = await navigator.mediaDevices.getUserMedia({ video: { width: 320, height: 240 } });
    } catch (permErr) {
      ElMessage.warning('需要授权摄像头才能列出名称: ' + (permErr?.message || ''));
      // 没授权也试着 enumerate, 至少能拿到 deviceId
    }
    const devices = await navigator.mediaDevices.enumerateDevices();
    splashCameraAvailable.value = devices
      .filter(d => d.kind === 'videoinput')
      .map(d => ({ deviceId: d.deviceId, label: d.label }));
    if (tempStream) tempStream.getTracks().forEach(t => t.stop());
    if (!splashCameraAvailable.value.length) {
      ElMessage.info('未扫到摄像头');
    } else {
      ElMessage.success(`扫到 ${splashCameraAvailable.value.length} 台摄像头`);
    }
  } catch (e) {
    ElMessage.error('扫描摄像头失败: ' + (e?.message || ''));
  } finally {
    splashCameraScanning.value = false;
  }
}

const openSplashCameraDialog = () => {
  splashCameraDialogPick.value = splashCamera.device_id || '';
  splashCameraDialogOpen.value = true;
};

const confirmSplashCameraPick = () => {
  const pick = splashCameraAvailable.value.find(c => c.deviceId === splashCameraDialogPick.value);
  if (!pick) {
    ElMessage.warning('请先选择一台摄像头');
    return;
  }
  splashCamera.device_id = pick.deviceId;
  splashCamera.device_label = pick.label || '';
  splashCameraDialogOpen.value = false;
  saveSplashCameraConfig();
  ElMessage.success(`已锁定: ${pick.label || pick.deviceId.slice(0, 16) + '...'}`);
};

onMounted(() => {
  store.loadSettings();
  loadTransformTotalChannels();
  loadTransformConfig();
  loadSplashCameraConfig();
  loadWindowConfig();   // v3.10.x: 主窗口模式
  loadAutoResumeConfig();  // v3.22.x: 开机自动恢复检测开关
  loadAdoptUnboundConfig();  // v3.51: 激活收养未绑定工位开关
  loadStartupReadyGateConfig();  // v3.23.x: 加深启动就绪门槛开关
  loadMesAsyncDispatchConfig();  // B1②: MES 外推并发派发开关
  loadScanPairNewFirstConfig();  // v3.49 WS3: 扫码配对新码先上屏开关
});
</script>
