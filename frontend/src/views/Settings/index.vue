<template>
  <TjSlot name="settings.layout.body">
  <div class="p-6 h-full overflow-y-auto">
    <h2 class="text-2xl font-bold mb-6 border-l-4 border-tech-blue pl-3 text-white">系统设置</h2>

    <el-tabs type="border-card" class="bg-gray-800 border-gray-700">
      
      <!-- Display Settings Tab -->
      <el-tab-pane label="显示设置">
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

          <!-- B1②: MES 外推并发派发 (默认关) -->
          <el-card shadow="never" class="bg-slate-800 border-slate-700">
            <template #header>
              <div class="flex items-center gap-2">
                <el-icon class="text-cyan-400"><Lightning /></el-icon>
                <span class="font-bold text-white">MES 外推并发派发</span>
              </div>
            </template>
            <div class="mb-3 text-xs text-gray-500">
              控制把检测结果推送给外部 MES 系统的方式。<br>
              关闭（默认）= 在统一队列里<b>顺序推送</b>，与旧版一致；若客户 MES 系统响应慢或断连，重试期间会拖慢扫码配对等其它处理。<br>
              开启 = 每个工位<b>独立线程推送</b>，同工位严格保序，<b>某个工位的 MES 慢/断连不再拖累其它工位和扫码流程</b>；适合多工位且 MES 偶发卡顿的现场。<br>
              修改后<b>立即生效</b>。
            </div>
            <div class="space-y-2">
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <div class="flex flex-col">
                  <span class="text-gray-300">每工位独立线程推送外部 MES</span>
                  <span class="text-[10px] text-gray-500">关闭 = 统一队列顺序推；开启 = 慢 MES 不拖累其它工位</span>
                </div>
                <el-switch
                  v-model="mesAsyncDispatchEnabled"
                  data-testid="mes-async-dispatch-switch"
                  @change="onMesAsyncDispatchChange"
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
      </el-tab-pane>

      <!-- Detection Box Settings Tab -->
      <el-tab-pane label="检测框设置">
        <div class="space-y-6 p-4">
          <!-- 检测框外观 -->
          <el-card shadow="never" class="bg-slate-800 border-slate-700">
            <template #header>
              <div class="flex items-center gap-2">
                <el-icon class="text-tech-blue"><Box /></el-icon>
                <span class="font-bold text-white">检测框外观</span>
              </div>
            </template>
            <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
              <div class="p-3 bg-slate-900 rounded border border-slate-800">
                <div class="text-gray-300 mb-2">正常检测框颜色</div>
                <el-color-picker v-model="store.detection.boxColor" @change="saveDetectionSettings" />
              </div>
              <div class="p-3 bg-slate-900 rounded border border-slate-800">
                <div class="text-gray-300 mb-2">NG 检测框颜色</div>
                <el-color-picker v-model="store.detection.boxColorNG" @change="saveDetectionSettings" />
              </div>
              <div class="p-3 bg-slate-900 rounded border border-slate-800">
                <div class="text-gray-300 mb-2">检测框线宽</div>
                <el-slider v-model="store.detection.boxLineWidth" :min="0" :step="1" @change="saveDetectionSettings" />
              </div>
              <div class="p-3 bg-slate-900 rounded border border-slate-800">
                <div class="text-gray-300 mb-2">标签字体大小</div>
                <el-slider v-model="store.detection.labelFontSize" :min="0" :step="1" @change="saveDetectionSettings" />
              </div>
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <span class="text-gray-300">显示置信度</span>
                <el-switch v-model="store.detection.showConfidence" @change="saveDetectionSettings" />
              </div>
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <span class="text-gray-300">NG弹窗显示原因</span>
                <el-switch v-model="store.detection.showNgReason" @change="saveDetectionSettings" />
              </div>
            </div>
          </el-card>

          <!-- 系统预设提示框设置 -->
          <el-card v-if="store.detection.toasts?.ok" shadow="never" class="bg-slate-800 border-slate-700">
            <template #header>
              <div class="flex items-center gap-2">
                <el-icon class="text-status-ok"><Bell /></el-icon>
                <span class="font-bold text-white">系统预设提示框</span>
              </div>
            </template>
            
            <!-- 合格提示框 -->
            <div class="mb-6">
              <div class="text-cyan-400 font-bold mb-3">合格提示框</div>
              <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                <div class="p-3 bg-slate-900 rounded border border-slate-800">
                  <div class="text-gray-300 mb-2">颜色</div>
                  <el-color-picker v-model="store.detection.toasts.ok.color" @change="saveDetectionSettings" />
                </div>
                <div class="p-3 bg-slate-900 rounded border border-slate-800">
                  <div class="text-gray-300 mb-2">显示时长 (秒)</div>
                  <el-slider v-model="store.detection.toasts.ok.duration" :min="0" :step="0.5" @change="saveDetectionSettings" />
                </div>
                <div class="p-3 bg-slate-900 rounded border border-slate-800">
                  <div class="text-gray-300 mb-2">字体大小</div>
                  <el-slider v-model="store.detection.toasts.ok.fontSize" :min="0" :step="1" @change="saveDetectionSettings" />
                </div>
                <div class="p-3 bg-slate-900 rounded border border-slate-800">
                  <div class="text-gray-300 mb-2">位置</div>
                  <el-select v-model="store.detection.toasts.ok.position" class="w-full" @change="saveDetectionSettings">
                    <el-option label="右上角" value="top-right" />
                    <el-option label="左上角" value="top-left" />
                    <el-option label="右下角" value="bottom-right" />
                    <el-option label="左下角" value="bottom-left" />
                    <el-option label="视频顶部居中" value="center" />
                  </el-select>
                </div>
                <div class="p-3 bg-slate-900 rounded border border-slate-800">
                  <div class="text-gray-300 mb-2">主文字</div>
                  <el-input v-model="store.detection.toasts.ok.text" placeholder="合格" @change="saveDetectionSettings" />
                </div>
                <div class="p-3 bg-slate-900 rounded border border-slate-800">
                  <div class="text-gray-300 mb-2">副文字（可选）</div>
                  <el-input v-model="store.detection.toasts.ok.subText" placeholder="" @change="saveDetectionSettings" />
                </div>
              </div>
            </div>
            
            <!-- NG提示框 -->
            <div>
              <div class="text-red-400 font-bold mb-3">NG 提示框</div>
              <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                <div class="p-3 bg-slate-900 rounded border border-slate-800">
                  <div class="text-gray-300 mb-2">颜色</div>
                  <el-color-picker v-model="store.detection.toasts.ng.color" @change="saveDetectionSettings" />
                </div>
                <div class="p-3 bg-slate-900 rounded border border-slate-800">
                  <div class="text-gray-300 mb-2">显示时长 (秒)</div>
                  <el-slider v-model="store.detection.toasts.ng.duration" :min="0" :step="0.5" @change="saveDetectionSettings" />
                </div>
                <div class="p-3 bg-slate-900 rounded border border-slate-800">
                  <div class="text-gray-300 mb-2">字体大小</div>
                  <el-slider v-model="store.detection.toasts.ng.fontSize" :min="0" :step="1" @change="saveDetectionSettings" />
                </div>
                <div class="p-3 bg-slate-900 rounded border border-slate-800">
                  <div class="text-gray-300 mb-2">位置</div>
                  <el-select v-model="store.detection.toasts.ng.position" class="w-full" @change="saveDetectionSettings">
                    <el-option label="右上角" value="top-right" />
                    <el-option label="左上角" value="top-left" />
                    <el-option label="右下角" value="bottom-right" />
                    <el-option label="左下角" value="bottom-left" />
                    <el-option label="视频顶部居中" value="center" />
                  </el-select>
                </div>
                <div class="p-3 bg-slate-900 rounded border border-slate-800">
                  <div class="text-gray-300 mb-2">主文字</div>
                  <el-input v-model="store.detection.toasts.ng.text" placeholder="不合格" @change="saveDetectionSettings" />
                </div>
                <div class="p-3 bg-slate-900 rounded border border-slate-800">
                  <div class="text-gray-300 mb-2">副文字（可选）</div>
                  <el-input v-model="store.detection.toasts.ng.subText" placeholder="" @change="saveDetectionSettings" />
                </div>
              </div>
            </div>
            
            <!-- 扫码提示框 -->
            <div>
              <div class="flex items-center gap-3 mb-3">
                <span class="text-cyan-400 font-bold">扫码提示框</span>
                <el-switch v-model="store.detection.toasts.scan.enabled" @change="saveDetectionSettings" active-text="启用" />
              </div>
              <div v-if="store.detection.toasts.scan?.enabled" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                <div class="p-3 bg-slate-900 rounded border border-slate-800">
                  <div class="text-gray-300 mb-2">颜色</div>
                  <el-color-picker v-model="store.detection.toasts.scan.color" @change="saveDetectionSettings" />
                </div>
                <div class="p-3 bg-slate-900 rounded border border-slate-800">
                  <div class="text-gray-300 mb-2">显示时长 (秒)</div>
                  <el-slider v-model="store.detection.toasts.scan.duration" :min="0" :step="0.5" @change="saveDetectionSettings" />
                </div>
                <div class="p-3 bg-slate-900 rounded border border-slate-800">
                  <div class="text-gray-300 mb-2">字体大小</div>
                  <el-slider v-model="store.detection.toasts.scan.fontSize" :min="0" :step="1" @change="saveDetectionSettings" />
                </div>
                <div class="p-3 bg-slate-900 rounded border border-slate-800">
                  <div class="text-gray-300 mb-2">位置</div>
                  <el-select v-model="store.detection.toasts.scan.position" class="w-full" @change="saveDetectionSettings">
                    <el-option label="右上角" value="top-right" />
                    <el-option label="左上角" value="top-left" />
                    <el-option label="右下角" value="bottom-right" />
                    <el-option label="左下角" value="bottom-left" />
                    <el-option label="视频顶部居中" value="center" />
                  </el-select>
                </div>
                <div class="p-3 bg-slate-900 rounded border border-slate-800">
                  <div class="text-gray-300 mb-2">主文字</div>
                  <el-input v-model="store.detection.toasts.scan.text" placeholder="扫码成功" @change="saveDetectionSettings" />
                </div>
                <div class="p-3 bg-slate-900 rounded border border-slate-800">
                  <div class="text-gray-300 mb-2">副文字（可选，留空则显示条码）</div>
                  <el-input v-model="store.detection.toasts.scan.subText" placeholder="" @change="saveDetectionSettings" />
                </div>
              </div>
            </div>

            <!-- 未绑码提示框 -->
            <div>
              <div class="flex items-center gap-3 mb-3">
                <span class="text-cyan-400 font-bold">未绑码提示框</span>
                <el-switch v-model="store.detection.toasts.warn_no_barcode.enabled" @change="saveDetectionSettings" active-text="启用" />
              </div>
              <div v-if="store.detection.toasts.warn_no_barcode?.enabled" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                <div class="p-3 bg-slate-900 rounded border border-slate-800">
                  <div class="text-gray-300 mb-2">颜色</div>
                  <el-color-picker v-model="store.detection.toasts.warn_no_barcode.color" @change="saveDetectionSettings" />
                </div>
                <div class="p-3 bg-slate-900 rounded border border-slate-800">
                  <div class="text-gray-300 mb-2">显示时长 (秒)</div>
                  <el-slider v-model="store.detection.toasts.warn_no_barcode.duration" :min="0" :step="0.5" @change="saveDetectionSettings" />
                </div>
                <div class="p-3 bg-slate-900 rounded border border-slate-800">
                  <div class="text-gray-300 mb-2">字体大小</div>
                  <el-slider v-model="store.detection.toasts.warn_no_barcode.fontSize" :min="0" :step="1" @change="saveDetectionSettings" />
                </div>
                <div class="p-3 bg-slate-900 rounded border border-slate-800">
                  <div class="text-gray-300 mb-2">位置</div>
                  <el-select v-model="store.detection.toasts.warn_no_barcode.position" class="w-full" @change="saveDetectionSettings">
                    <el-option label="右上角" value="top-right" />
                    <el-option label="左上角" value="top-left" />
                    <el-option label="右下角" value="bottom-right" />
                    <el-option label="左下角" value="bottom-left" />
                    <el-option label="居中" value="center" />
                  </el-select>
                </div>
                <div class="p-3 bg-slate-900 rounded border border-slate-800">
                  <div class="text-gray-300 mb-2">主文字</div>
                  <el-input v-model="store.detection.toasts.warn_no_barcode.text" placeholder="⚠ 未绑码" @change="saveDetectionSettings" />
                </div>
                <div class="p-3 bg-slate-900 rounded border border-slate-800">
                  <div class="text-gray-300 mb-2">副文字</div>
                  <el-input v-model="store.detection.toasts.warn_no_barcode.subText" placeholder="本次结算未绑定工件条码" @change="saveDetectionSettings" />
                </div>
              </div>
            </div>
          </el-card>

          <!-- 自定义提示框 -->
          <el-card shadow="never" class="bg-slate-800 border-slate-700">
            <template #header>
              <div class="flex items-center justify-between">
                <div class="flex items-center gap-2">
                  <el-icon class="text-yellow-400"><Bell /></el-icon>
                  <span class="font-bold text-white">自定义提示框</span>
                </div>
                <el-button type="primary" size="small" @click="addCustomToast">+ 新建提示框</el-button>
              </div>
            </template>
            
            <div v-if="store.detection.customToasts.length === 0" class="text-gray-500 text-center py-8 border border-dashed border-slate-700 rounded">
              暂无自定义提示框，点击上方按钮添加
            </div>
            
            <div v-else class="space-y-6">
              <div v-for="(toast, idx) in store.detection.customToasts" :key="toast.id" class="p-4 bg-slate-900 rounded border border-slate-700">
                <div class="flex items-center justify-between mb-3">
                  <el-input v-model="toast.name" class="w-48" size="small" placeholder="提示框名称" @change="saveDetectionSettings" />
                  <el-button type="danger" size="small" link @click="removeCustomToast(idx)">删除</el-button>
                </div>
                <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                  <div class="p-3 bg-slate-800 rounded">
                    <div class="text-gray-300 mb-2">颜色</div>
                    <el-color-picker v-model="toast.color" @change="saveDetectionSettings" />
                  </div>
                  <div class="p-3 bg-slate-800 rounded">
                    <div class="text-gray-300 mb-2">显示时长 (秒)</div>
                    <el-slider v-model="toast.duration" :min="0" :step="0.5" @change="saveDetectionSettings" />
                  </div>
                  <div class="p-3 bg-slate-800 rounded">
                    <div class="text-gray-300 mb-2">字体大小</div>
                    <el-slider v-model="toast.fontSize" :min="0" :step="1" @change="saveDetectionSettings" />
                  </div>
                  <div class="p-3 bg-slate-800 rounded">
                    <div class="text-gray-300 mb-2">位置</div>
                    <el-select v-model="toast.position" class="w-full" @change="saveDetectionSettings">
                      <el-option label="右上角" value="top-right" />
                      <el-option label="左上角" value="top-left" />
                      <el-option label="右下角" value="bottom-right" />
                      <el-option label="左下角" value="bottom-left" />
                      <el-option label="视频顶部居中" value="center" />
                    </el-select>
                  </div>
                  <div class="p-3 bg-slate-800 rounded">
                    <div class="text-gray-300 mb-2">主文字（事件名）</div>
                    <el-input v-model="toast.text" placeholder="显示事件名称" @change="saveDetectionSettings" />
                  </div>
                  <div class="p-3 bg-slate-800 rounded">
                    <div class="text-gray-300 mb-2">副文字</div>
                    <el-input v-model="toast.subText" placeholder="可选的副标题" @change="saveDetectionSettings" />
                  </div>
                </div>
              </div>
            </div>
          </el-card>
          
          <!-- 预览 -->
          <el-card shadow="never" class="bg-slate-800 border-slate-700">
            <template #header>
              <span class="font-bold text-white">效果预览</span>
            </template>
            <div class="flex flex-wrap gap-8 items-center justify-center p-6">
              <!-- 检测框预览 -->
              <div class="relative w-48 h-32 bg-gray-900 rounded border border-gray-700 flex items-center justify-center">
                <div 
                  class="w-24 h-16 border-2 relative"
                  :style="{ 
                    borderColor: store.detection.boxColor,
                    borderWidth: store.detection.boxLineWidth + 'px'
                  }"
                >
                  <div 
                    class="absolute -top-6 left-0 px-1 text-white"
                    :style="{ 
                      backgroundColor: store.detection.boxColor,
                      fontSize: (store.detection.labelFontSize / 16) + 'rem'
                    }"
                  >
                    检测框 {{ store.detection.showConfidence ? '95%' : '' }}
                  </div>
                </div>
              </div>
              
              <!-- 提示框预览 -->
              <div class="flex flex-wrap gap-4">
                <div 
                  class="px-6 py-4 rounded-xl shadow-lg text-white font-bold text-center"
                  :style="{ 
                    backgroundColor: store.detection.toasts.ok.color,
                    fontSize: (store.detection.toasts.ok.fontSize / 16) + 'rem'
                  }"
                >
                  <div>{{ store.detection.toasts.ok.text || '合格' }}</div>
                  <div v-if="store.detection.toasts.ok.subText" class="text-sm opacity-80 mt-1">{{ store.detection.toasts.ok.subText }}</div>
                </div>
                <div 
                  class="px-6 py-4 rounded-xl shadow-lg text-white font-bold text-center"
                  :style="{ 
                    backgroundColor: store.detection.toasts.ng.color,
                    fontSize: (store.detection.toasts.ng.fontSize / 16) + 'rem'
                  }"
                >
                  <div>{{ store.detection.toasts.ng.text || '不合格' }}</div>
                  <div v-if="store.detection.toasts.ng.subText" class="text-sm opacity-80 mt-1">{{ store.detection.toasts.ng.subText }}</div>
                </div>
                <div 
                  v-for="toast in store.detection.customToasts" 
                  :key="toast.id"
                  class="px-6 py-4 rounded-xl shadow-lg text-white font-bold text-center"
                  :style="{ 
                    backgroundColor: toast.color,
                    fontSize: (toast.fontSize / 16) + 'rem'
                  }"
                >
                  <div>{{ toast.text || toast.name }}</div>
                  <div v-if="toast.subText" class="text-sm opacity-80 mt-1">{{ toast.subText }}</div>
                </div>
              </div>
            </div>
          </el-card>
        </div>
      </el-tab-pane>

      <!-- Performance Settings Tab -->
      <el-tab-pane label="性能设置">
        <div class="space-y-6 p-4">
          <!-- 视频流设置 -->
          <el-card shadow="never" class="bg-slate-800 border-slate-700">
            <template #header>
              <div class="flex items-center gap-2">
                <el-icon class="text-tech-blue"><VideoCamera /></el-icon>
                <span class="font-bold text-white">视频流设置</span>
              </div>
            </template>
            <div class="space-y-4">
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <div>
                  <span class="text-gray-300">启用帧率限制</span>
                  <div class="text-xs text-gray-500 mt-1">本地应用建议关闭以获得最低延迟</div>
                </div>
                <el-switch v-model="store.performance.frameLimitEnabled" @change="savePerformanceSettings" />
              </div>
              <div v-if="store.performance.frameLimitEnabled" class="p-3 bg-slate-900 rounded border border-slate-800">
                <div class="text-gray-300 mb-2">目标帧率 (FPS)</div>
                <el-slider 
                  v-model="store.performance.targetStreamFps" 
                  :min="0" 
                  :step="5" 
                  show-stops
                  :marks="{10: '10', 30: '30', 60: '60'}"
                  @change="savePerformanceSettings" 
                />
                <div class="text-xs text-gray-500 mt-2">当前: {{ store.performance.targetStreamFps }} FPS</div>
              </div>
              <el-alert
                title="延迟优化说明"
                type="info"
                :closable="false"
                show-icon
              >
                <template #default>
                  <div class="text-xs text-gray-300 mt-1">
                    <p>• <strong>关闭帧率限制</strong>：有新帧立即发送，延迟最低（推荐本地使用）</p>
                    <p>• <strong>开启帧率限制</strong>：限制传输频率，适合低配电脑或远程使用</p>
                  </div>
                </template>
              </el-alert>
              <!-- D1: 多通道视频解码背压 (默认关) -->
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <div>
                  <span class="text-gray-300">多工位视频解码背压</span>
                  <div class="text-xs text-gray-500 mt-1">
                    多工位画面改用 createImageBitmap 解码 + "每工位同时只解一帧、跟不上就丢旧留最新"。<br>
                    长时间多工位运行不再因解码积压导致内存/GC 压力升高；关闭=旧版逐帧解码，行为一致。
                  </div>
                </div>
                <el-switch
                  v-model="store.performance.multiChannelBitmapDecode"
                  data-testid="multi-bitmap-decode-switch"
                  @change="savePerformanceSettings"
                />
              </div>
            </div>
          </el-card>

          <!-- 推理加速设置 -->
          <el-card shadow="never" class="bg-slate-800 border-slate-700">
            <template #header>
              <div class="flex items-center gap-2">
                <el-icon class="text-yellow-400"><Lightning /></el-icon>
                <span class="font-bold text-white">推理加速</span>
              </div>
            </template>
            <div class="space-y-4">
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <div>
                  <span class="text-gray-300">FP16 半精度推理</span>
                  <div class="text-xs text-gray-500 mt-1">开启后推理速度约提升 50%-100%，适用于 NVIDIA RTX 20/30/40/50 系列 GPU</div>
                </div>
                <el-switch v-model="store.performance.halfPrecision" @change="savePerformanceSettings" />
              </div>
              <el-alert
                title="说明"
                type="warning"
                :closable="false"
                show-icon
              >
                <template #default>
                  <div class="text-xs text-gray-300 mt-1">
                    <p>• 开启后使用 16 位浮点精度推理，大幅降低单帧推理耗时</p>
                    <p>• 检测精度几乎无损（差异 &lt; 0.1%），Ultralytics 官方推荐</p>
                    <p>• 仅在 GPU (CUDA) 设备上生效，CPU 推理不受影响</p>
                    <p>• 修改后<strong>下次加载模型时生效</strong>（重新开始检测即可）</p>
                  </div>
                </template>
              </el-alert>
            </div>
          </el-card>

          <!-- MediaPipe 骨架叠加 -->
          <el-card shadow="never" class="bg-slate-800 border-slate-700">
            <template #header>
              <div class="flex items-center gap-2">
                <el-icon class="text-purple-400"><Aim /></el-icon>
                <span class="font-bold text-white">MediaPipe 骨架叠加</span>
                <el-tag size="small" type="info">视觉增强</el-tag>
              </div>
            </template>
            <div class="space-y-4">
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <div>
                  <span class="text-gray-300">启用 MediaPipe 叠加</span>
                  <div class="text-xs text-gray-500 mt-1">在画面上实时显示人体骨架和手部关键点，纯视觉效果，不影响检测逻辑</div>
                </div>
                <el-switch v-model="store.performance.mediapipeEnabled" @change="savePerformanceSettings" />
              </div>
              <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                  <span class="text-gray-300">姿态骨架</span>
                  <el-switch v-model="store.performance.mediapipePose" :disabled="!store.performance.mediapipeEnabled" @change="savePerformanceSettings" />
                </div>
                <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                  <span class="text-gray-300">手部关键点</span>
                  <el-switch v-model="store.performance.mediapipeHands" :disabled="!store.performance.mediapipeEnabled" @change="savePerformanceSettings" />
                </div>
              </div>

              <!-- v3.32.0: 自定义纯色骨架样式 -->
              <div class="p-3 bg-slate-900 rounded border border-slate-800 space-y-3">
                <div class="flex items-center justify-between">
                  <div>
                    <span class="text-gray-300">自定义骨架样式</span>
                    <div class="text-xs text-gray-500 mt-1">开启后骨架用纯色绘制，颜色和线条粗细可自选；关闭 = MediaPipe 默认多彩配色</div>
                  </div>
                  <el-switch v-model="store.performance.mediapipeCustomStyle" :disabled="!store.performance.mediapipeEnabled" @change="savePerformanceSettings" />
                </div>
                <div v-if="store.performance.mediapipeCustomStyle" class="grid grid-cols-1 md:grid-cols-2 gap-3">
                  <div class="flex items-center justify-between p-2 bg-slate-800 rounded border border-slate-700">
                    <span class="text-gray-400 text-sm">姿态连线颜色</span>
                    <el-color-picker
                      v-model="store.performance.mediapipePoseColor"
                      :disabled="!store.performance.mediapipeEnabled"
                      @change="v => { if (!v) store.performance.mediapipePoseColor = '#00FF00'; savePerformanceSettings(); }"
                    />
                  </div>
                  <div class="flex items-center justify-between p-2 bg-slate-800 rounded border border-slate-700">
                    <div>
                      <span class="text-gray-400 text-sm">姿态关键点颜色</span>
                      <div class="text-xs text-gray-600">清空 = 跟随连线颜色</div>
                    </div>
                    <el-color-picker
                      v-model="store.performance.mediapipePosePointColor"
                      :disabled="!store.performance.mediapipeEnabled"
                      @change="v => { if (!v) store.performance.mediapipePosePointColor = ''; savePerformanceSettings(); }"
                    />
                  </div>
                  <div class="flex items-center justify-between p-2 bg-slate-800 rounded border border-slate-700">
                    <span class="text-gray-400 text-sm">姿态线条粗细</span>
                    <el-input-number
                      v-model="store.performance.mediapipePoseThickness"
                      size="small"
                      :min="1"
                      :max="10"
                      :precision="0"
                      :step="1"
                      :disabled="!store.performance.mediapipeEnabled"
                      @change="savePerformanceSettings"
                      style="width: 100px"
                    />
                  </div>
                  <div class="flex items-center justify-between p-2 bg-slate-800 rounded border border-slate-700">
                    <span class="text-gray-400 text-sm">手部连线颜色</span>
                    <el-color-picker
                      v-model="store.performance.mediapipeHandsColor"
                      :disabled="!store.performance.mediapipeEnabled"
                      @change="v => { if (!v) store.performance.mediapipeHandsColor = '#00FF00'; savePerformanceSettings(); }"
                    />
                  </div>
                  <div class="flex items-center justify-between p-2 bg-slate-800 rounded border border-slate-700">
                    <div>
                      <span class="text-gray-400 text-sm">手部关键点颜色</span>
                      <div class="text-xs text-gray-600">清空 = 跟随连线颜色</div>
                    </div>
                    <el-color-picker
                      v-model="store.performance.mediapipeHandsPointColor"
                      :disabled="!store.performance.mediapipeEnabled"
                      @change="v => { if (!v) store.performance.mediapipeHandsPointColor = ''; savePerformanceSettings(); }"
                    />
                  </div>
                  <div class="flex items-center justify-between p-2 bg-slate-800 rounded border border-slate-700">
                    <span class="text-gray-400 text-sm">手部线条粗细</span>
                    <el-input-number
                      v-model="store.performance.mediapipeHandsThickness"
                      size="small"
                      :min="1"
                      :max="10"
                      :precision="0"
                      :step="1"
                      :disabled="!store.performance.mediapipeEnabled"
                      @change="savePerformanceSettings"
                      style="width: 100px"
                    />
                  </div>
                </div>
              </div>
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <div>
                  <span class="text-gray-300">检测置信度</span>
                  <div class="text-xs text-gray-500 mt-1">值越高误检越少但可能漏检（俯视角度建议 0.7-0.9）</div>
                </div>
                <div class="flex items-center gap-2">
                  <el-slider
                    v-model="store.performance.mediapipeConfidence"
                    :min="0"
                    :step="0.05"
                    :disabled="!store.performance.mediapipeEnabled"
                    @change="savePerformanceSettings"
                    style="width: 140px"
                    :show-tooltip="true"
                    :format-tooltip="v => v.toFixed(2)"
                  />
                  <span class="text-gray-400 text-xs w-8 text-right">{{ store.performance.mediapipeConfidence.toFixed(2) }}</span>
                </div>
              </div>
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <div>
                  <span class="text-gray-300">处理间隔</span>
                  <div class="text-xs text-gray-500 mt-1">每隔 N 帧处理一次（值越大性能越好但骨架更新越慢）</div>
                </div>
                <el-input-number
                  v-model="store.performance.mediapipeInterval"
                  size="small"
                  :min="0"
                  :precision="0"
                  :step="1"
                  :controls="true"
                  :disabled="!store.performance.mediapipeEnabled"
                  @change="savePerformanceSettings"
                  style="width: 120px"
                />
              </div>

              <!-- v3.8.0: 一键预设 -->
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <div>
                  <span class="text-gray-300">手部识别预设</span>
                  <div class="text-xs text-gray-500 mt-1">
                    省 CPU 模式 = 默认（complexity=0, 置信度 0.7）；高精度模式 = 友商同款（complexity=1, 置信度 0.5），手部识别率提升 3-4 倍但 CPU 占用增加约 80%
                  </div>
                </div>
                <div class="flex flex-col gap-2">
                  <el-button
                    size="small"
                    type="info"
                    plain
                    :disabled="!store.performance.mediapipeEnabled"
                    @click="applyMediaPipePreset('eco')"
                  >省 CPU 模式</el-button>
                  <el-button
                    size="small"
                    type="success"
                    plain
                    :disabled="!store.performance.mediapipeEnabled"
                    @click="applyMediaPipePreset('quality')"
                  >高精度模式</el-button>
                </div>
              </div>

              <!-- v3.8.0: 高级选项 -->
              <el-collapse class="border-slate-700">
                <el-collapse-item title="高级选项" name="mp-advanced">
                  <div class="space-y-4">
                    <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                      <div>
                        <span class="text-gray-300">手部模型档位</span>
                        <div class="text-xs text-gray-500 mt-1">
                          0 = 轻量版（推理快、识别黑手套/俯视等困难场景几乎无效）；1 = 完整版（推理慢约 1.8 倍、对手套/握工具等非标场景识别率显著提升）
                        </div>
                      </div>
                      <el-radio-group
                        v-model="store.performance.mediapipeModelComplexity"
                        size="small"
                        :disabled="!store.performance.mediapipeEnabled"
                        @change="savePerformanceSettings"
                      >
                        <el-radio-button :value="0">轻量(0)</el-radio-button>
                        <el-radio-button :value="1">完整(1)</el-radio-button>
                      </el-radio-group>
                    </div>
                    <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                      <div>
                        <span class="text-gray-300">跟踪置信度</span>
                        <div class="text-xs text-gray-500 mt-1">值越低骨架越"粘"住手不易丢失但更抖；首检后跟随期使用</div>
                      </div>
                      <div class="flex items-center gap-2">
                        <el-slider
                          v-model="store.performance.mediapipeTrackConfidence"
                          :min="0.05"
                          :max="0.95"
                          :step="0.05"
                          :disabled="!store.performance.mediapipeEnabled"
                          @change="savePerformanceSettings"
                          style="width: 140px"
                          :show-tooltip="true"
                          :format-tooltip="v => v.toFixed(2)"
                        />
                        <span class="text-gray-400 text-xs w-8 text-right">{{ store.performance.mediapipeTrackConfidence.toFixed(2) }}</span>
                      </div>
                    </div>
                  </div>
                </el-collapse-item>
              </el-collapse>

              <!-- v3.8.0: 工业专用手部模型 (二段管线) -->
              <el-collapse class="border-slate-700">
                <el-collapse-item name="mp-industrial">
                  <template #title>
                    <div class="flex items-center gap-2">
                      <span class="text-gray-200 font-bold">工业专用手部模型</span>
                      <el-tag
                        v-if="mediapipeStatusState === 'baseline'"
                        size="small"
                        type="info"
                      >未启用</el-tag>
                      <el-tag
                        v-else-if="mediapipeStatusState === 'active'"
                        size="small"
                        type="success"
                      >已启用</el-tag>
                      <el-tag
                        v-else-if="mediapipeStatusState === 'pending'"
                        size="small"
                        type="warning"
                      >待加载</el-tag>
                      <el-tag
                        v-else
                        size="small"
                        type="danger"
                      >异常</el-tag>
                    </div>
                  </template>
                  <div class="space-y-4">
                    <el-alert
                      :title="mediapipeStatusMessage"
                      :type="mediapipeStatusAlertType"
                      :closable="false"
                      show-icon
                    >
                      <template #default>
                        <div class="text-xs text-gray-300 mt-1">
                          基础 MediaPipe 在黑手套俯视、握工具遮挡等"工业死区"场景识别率为零。配一个本地训练的 hand-detector
                          (YOLO .pt) 即可启用二段管线 (YOLO 框出手部位置 → ROI 切割 → MediaPipe 画骨架)。
                          训好的模型放本地任意路径，填到下方即可，<strong>不需要重启</strong>。
                        </div>
                      </template>
                    </el-alert>

                    <div class="p-3 bg-slate-900 rounded border border-slate-800">
                      <div class="text-gray-300 mb-2">模型文件路径</div>
                      <div class="text-xs text-gray-500 mb-2">
                        本地 .pt 文件绝对路径（例如 <code>D:/tianjun/models/industrial_hand.pt</code>）。留空 = 关闭二段管线，走基础 MediaPipe
                      </div>
                      <div class="flex gap-2">
                        <el-input
                          v-model="store.performance.mediapipeHandDetectorPath"
                          placeholder="留空走基础模式 / 填本地 .pt 路径启用专用模型"
                          :disabled="!store.performance.mediapipeEnabled"
                          clearable
                          @change="savePerformanceSettings"
                        />
                        <el-button
                          size="default"
                          :disabled="!store.performance.mediapipeEnabled"
                          @click="savePerformanceSettings"
                        >应用</el-button>
                      </div>
                    </div>

                    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                      <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                        <div>
                          <span class="text-gray-300">模型格式</span>
                          <div class="text-xs text-gray-500 mt-1">YOLOv5 老格式选 v5，其余选 v8</div>
                        </div>
                        <el-radio-group
                          v-model="store.performance.mediapipeHandDetectorKind"
                          size="small"
                          :disabled="!store.performance.mediapipeEnabled || !store.performance.mediapipeHandDetectorPath"
                          @change="savePerformanceSettings"
                        >
                          <el-radio-button value="v8">v8</el-radio-button>
                          <el-radio-button value="v5">v5</el-radio-button>
                        </el-radio-group>
                      </div>
                      <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                        <div>
                          <span class="text-gray-300">检测置信度</span>
                          <div class="text-xs text-gray-500 mt-1">框越多→精度越低</div>
                        </div>
                        <div class="flex items-center gap-2">
                          <el-slider
                            v-model="store.performance.mediapipeHandDetectorConf"
                            :min="0.05"
                            :max="0.9"
                            :step="0.05"
                            :disabled="!store.performance.mediapipeEnabled || !store.performance.mediapipeHandDetectorPath"
                            @change="savePerformanceSettings"
                            style="width: 120px"
                            :show-tooltip="true"
                            :format-tooltip="v => v.toFixed(2)"
                          />
                          <span class="text-gray-400 text-xs w-8 text-right">{{ store.performance.mediapipeHandDetectorConf.toFixed(2) }}</span>
                        </div>
                      </div>
                      <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                        <div>
                          <span class="text-gray-300">NMS 阈值</span>
                          <div class="text-xs text-gray-500 mt-1">两个框重叠超此比例时去重</div>
                        </div>
                        <div class="flex items-center gap-2">
                          <el-slider
                            v-model="store.performance.mediapipeHandDetectorIou"
                            :min="0.1"
                            :max="0.9"
                            :step="0.05"
                            :disabled="!store.performance.mediapipeEnabled || !store.performance.mediapipeHandDetectorPath"
                            @change="savePerformanceSettings"
                            style="width: 120px"
                            :show-tooltip="true"
                            :format-tooltip="v => v.toFixed(2)"
                          />
                          <span class="text-gray-400 text-xs w-8 text-right">{{ store.performance.mediapipeHandDetectorIou.toFixed(2) }}</span>
                        </div>
                      </div>
                      <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                        <div>
                          <span class="text-gray-300">输入尺寸</span>
                          <div class="text-xs text-gray-500 mt-1">YOLO resize 后边长（640 兼顾速度/精度）</div>
                        </div>
                        <el-input-number
                          v-model="store.performance.mediapipeHandDetectorImgsz"
                          size="small"
                          :min="320"
                          :max="1280"
                          :step="32"
                          :precision="0"
                          :disabled="!store.performance.mediapipeEnabled || !store.performance.mediapipeHandDetectorPath"
                          @change="savePerformanceSettings"
                          style="width: 110px"
                        />
                      </div>
                      <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800 md:col-span-2">
                        <div>
                          <span class="text-gray-300">ROI 外扩比例</span>
                          <div class="text-xs text-gray-500 mt-1">手指容易被 YOLO 框切到 → 把框向外扩这个比例后再喂给 MediaPipe</div>
                        </div>
                        <div class="flex items-center gap-2">
                          <el-slider
                            v-model="store.performance.mediapipeHandRoiPad"
                            :min="0"
                            :max="1"
                            :step="0.05"
                            :disabled="!store.performance.mediapipeEnabled || !store.performance.mediapipeHandDetectorPath"
                            @change="savePerformanceSettings"
                            style="width: 180px"
                            :show-tooltip="true"
                            :format-tooltip="v => v.toFixed(2)"
                          />
                          <span class="text-gray-400 text-xs w-12 text-right">{{ (store.performance.mediapipeHandRoiPad * 100).toFixed(0) }}%</span>
                        </div>
                      </div>
                    </div>
                  </div>
                </el-collapse-item>
              </el-collapse>

              <el-alert
                title="说明"
                type="info"
                :closable="false"
                show-icon
              >
                <template #default>
                  <div class="text-xs text-gray-300 mt-1">
                    <p>• MediaPipe 由 Google 开发，提供轻量级人体姿态和手部关键点检测</p>
                    <p>• 仅在画面显示中叠加骨架效果，<strong>不参与检测判定、不影响录制</strong></p>
                    <p>• 性能参考（CPU 处理）：省 CPU 模式约 18-22ms/帧；高精度模式约 30-38ms/帧；专用模型模式约 13-16ms/帧（更快）</p>
                    <p>• 需要安装 mediapipe 包：<code>pip install mediapipe</code></p>
                  </div>
                </template>
              </el-alert>
            </div>
          </el-card>

          <!-- GPU/推理设备设置 -->
          <el-card shadow="never" class="bg-slate-800 border-slate-700">
            <template #header>
              <div class="flex items-center justify-between">
                <div class="flex items-center gap-2">
                  <el-icon class="text-green-400"><Cpu /></el-icon>
                  <span class="font-bold text-white">推理设备 (GPU/CPU)</span>
                </div>
                <el-button type="primary" link @click="refreshGpuList" :loading="loadingGpu">
                  <el-icon class="mr-1"><Refresh /></el-icon> 刷新
                </el-button>
              </div>
            </template>
            <div class="space-y-4">
              <!-- CUDA状态显示 -->
              <div class="p-3 bg-slate-900 rounded border border-slate-800">
                <div class="flex items-center justify-between">
                  <span class="text-gray-300">CUDA 状态</span>
                  <el-tag :type="gpuInfo.cudaAvailable ? 'success' : 'danger'" size="small">
                    {{ gpuInfo.cudaAvailable ? `可用 (CUDA ${gpuInfo.cudaVersion})` : '不可用' }}
                  </el-tag>
                </div>
                <div v-if="gpuInfo.gpuCount > 0" class="text-xs text-gray-500 mt-1">
                  检测到 {{ gpuInfo.gpuCount }} 个GPU设备
                </div>
              </div>

              <!-- 设备选择 -->
              <div class="p-3 bg-slate-900 rounded border border-slate-800">
                <div class="flex items-center justify-between mb-2">
                  <span class="text-gray-300">默认推理设备</span>
                  <el-tag v-if="isDefaultDevice" type="success" size="small">已保存为默认</el-tag>
                </div>
                <el-select v-model="selectedDevice" class="w-full" @change="changeDevice" :loading="changingDevice">
                  <el-option
                    v-for="device in gpuInfo.devices"
                    :key="device.id"
                    :label="device.name"
                    :value="device.id"
                  >
                    <div class="flex items-center justify-between w-full">
                      <span>{{ device.name }}</span>
                      <el-tag v-if="device.type === 'GPU'" type="success" size="small">GPU</el-tag>
                      <el-tag v-else-if="device.type === 'CPU'" type="info" size="small">CPU</el-tag>
                      <el-tag v-else type="warning" size="small">自动</el-tag>
                    </div>
                  </el-option>
                </el-select>
                <div class="text-xs text-gray-500 mt-2">
                  选择后自动保存，下次启动将使用此设备
                </div>
              </div>

              <!-- 当前使用的设备 -->
              <div v-if="currentDeviceInfo" class="p-3 bg-slate-900 rounded border" 
                   :class="currentDeviceInfo.type === 'GPU' ? 'border-green-600' : 'border-slate-700'">
                <div class="flex items-center justify-between">
                  <span class="text-gray-300">当前推理设备</span>
                  <el-tag :type="currentDeviceInfo.type === 'GPU' ? 'success' : 'info'" size="small">
                    {{ currentDeviceInfo.type }}
                  </el-tag>
                </div>
                <div class="text-lg font-bold mt-1" :class="currentDeviceInfo.type === 'GPU' ? 'text-green-400' : 'text-gray-400'">
                  {{ currentDeviceInfo.name }}
                </div>
                <div class="text-xs text-gray-500 mt-1">设备: {{ currentDeviceInfo.device }}</div>
              </div>
              <div v-else class="p-3 bg-slate-900 rounded border border-slate-700 text-center text-gray-500">
                尚未加载模型，设备信息将在加载模型后显示
              </div>

              <el-alert
                title="GPU 推理说明"
                type="info"
                :closable="false"
                show-icon
              >
                <template #default>
                  <div class="text-xs text-gray-300 mt-1">
                    <p>• <strong>GPU推理</strong>：速度快（约10-50ms/帧），需要NVIDIA显卡和驱动</p>
                    <p>• <strong>CPU推理</strong>：速度慢（约100-300ms/帧），任何电脑都支持</p>
                    <p>• 切换设备后需要重新加载模型才能生效</p>
                  </div>
                </template>
              </el-alert>
            </div>
          </el-card>

          <!-- 检测框滤波设置 -->
          <el-card shadow="never" class="bg-slate-800 border-slate-700">
            <template #header>
              <div class="flex items-center gap-2">
                <el-icon class="text-purple-400"><Box /></el-icon>
                <span class="font-bold text-white">检测框滤波 (卡尔曼滤波)</span>
              </div>
            </template>
            <div class="space-y-4">
              <!-- 启用开关 -->
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <div>
                  <span class="text-gray-300">启用卡尔曼滤波</span>
                  <div class="text-xs text-gray-500 mt-1">平滑检测框运动轨迹，减少抖动</div>
                </div>
                <el-switch v-model="kalmanConfig.enabled" @change="saveKalmanConfig" />
              </div>

              <!-- 过程噪声 Q -->
              <div v-if="kalmanConfig.enabled" class="p-3 bg-slate-900 rounded border border-slate-800">
                <div class="flex items-center justify-between mb-2">
                  <span class="text-gray-300">响应速度 (过程噪声 Q)</span>
                  <span class="text-tech-blue font-mono">{{ kalmanConfig.processNoise.toFixed(3) }}</span>
                </div>
                <el-slider 
                  v-model="kalmanConfig.processNoise" 
                  :min="0" 
                  :step="0.005" 
                  @change="saveKalmanConfig" 
                />
                <div class="text-xs text-gray-500 mt-2">
                  <span class="text-yellow-400">← 更平滑</span>
                  <span class="float-right text-green-400">响应更快 →</span>
                </div>
              </div>

              <!-- 观测噪声 R -->
              <div v-if="kalmanConfig.enabled" class="p-3 bg-slate-900 rounded border border-slate-800">
                <div class="flex items-center justify-between mb-2">
                  <span class="text-gray-300">平滑程度 (观测噪声 R)</span>
                  <span class="text-tech-blue font-mono">{{ kalmanConfig.measurementNoise.toFixed(2) }}</span>
                </div>
                <el-slider 
                  v-model="kalmanConfig.measurementNoise" 
                  :min="0" 
                  :step="0.01" 
                  @change="saveKalmanConfig" 
                />
                <div class="text-xs text-gray-500 mt-2">
                  <span class="text-red-400">← 更抖动</span>
                  <span class="float-right text-blue-400">更平滑 →</span>
                </div>
              </div>

              <!-- 消失帧数阈值 -->
              <div v-if="kalmanConfig.enabled" class="p-3 bg-slate-900 rounded border border-slate-800">
                <div class="flex items-center justify-between mb-2">
                  <span class="text-gray-300">目标消失容忍帧数</span>
                  <span class="text-tech-blue font-mono">{{ kalmanConfig.maxMissingFrames }} 帧</span>
                </div>
                <el-slider 
                  v-model="kalmanConfig.maxMissingFrames" 
                  :min="0" 
                  :step="1" 
                  @change="saveKalmanConfig" 
                />
                <div class="text-xs text-gray-500 mt-2">
                  目标消失多少帧后移除滤波器（值越大，短暂遮挡时框越稳定）
                </div>
              </div>

              <!-- 推荐预设 -->
              <div v-if="kalmanConfig.enabled" class="p-3 bg-slate-900 rounded border border-slate-800">
                <div class="text-gray-300 mb-3">快速预设</div>
                <div class="flex gap-2 flex-wrap">
                  <el-button size="small" @click="applyKalmanPreset('smooth')">
                    🎯 更平滑
                  </el-button>
                  <el-button size="small" @click="applyKalmanPreset('balanced')">
                    ⚖️ 平衡
                  </el-button>
                  <el-button size="small" @click="applyKalmanPreset('responsive')">
                    ⚡ 快响应
                  </el-button>
                </div>
              </div>

              <el-alert
                title="滤波参数调节指南"
                type="info"
                :closable="false"
                show-icon
              >
                <template #default>
                  <div class="text-xs text-gray-300 mt-1">
                    <p>• <strong>物体移动较慢</strong>：增大R值（更平滑），减小Q值</p>
                    <p>• <strong>物体移动较快</strong>：增大Q值（响应更快），减小R值</p>
                    <p>• <strong>检测框抖动严重</strong>：增大R值</p>
                    <p>• <strong>检测框跟不上物体</strong>：增大Q值</p>
                  </div>
                </template>
              </el-alert>
            </div>
          </el-card>
        </div>
      </el-tab-pane>

      <!-- 轮询间隔 Tab -->
      <el-tab-pane label="轮询间隔">
        <div class="space-y-6 p-4">
          <el-card shadow="never" class="bg-slate-800 border-slate-700">
            <template #header>
              <div class="flex items-center gap-2">
                <el-icon class="text-tech-blue"><Refresh /></el-icon>
                <span class="font-bold text-white">管理面板刷新间隔</span>
              </div>
            </template>
            <el-alert type="info" :closable="false" show-icon class="mb-4">
              <template #default>
                <div class="text-xs text-gray-300">
                  仅控制各管理面板的数据刷新频率（毫秒），不影响检测主循环。最小 500ms，留空/非法值回落默认。
                </div>
              </template>
            </el-alert>
            <div class="grid grid-cols-2 gap-4">
              <div v-for="item in pollingItems" :key="item.key"
                   class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <div>
                  <span class="text-gray-300">{{ item.label }}</span>
                  <div class="text-xs text-gray-500 mt-1">默认 {{ pollingDefaults[item.key] }} ms</div>
                </div>
                <el-input-number
                  v-model="pollingForm[item.key]"
                  :min="500" :max="600000" :step="500" controls-position="right"
                  size="small" style="width: 140px"
                  @change="savePollingItem(item.key)"
                />
              </div>
            </div>
          </el-card>

          <el-card shadow="never" class="bg-slate-800 border-slate-700">
            <template #header>
              <div class="flex items-center gap-2">
                <el-icon class="text-tech-blue"><DataLine /></el-icon>
                <span class="font-bold text-white">日志显示条数</span>
              </div>
            </template>
            <el-alert type="info" :closable="false" show-icon class="mb-4">
              <template #default>
                <div class="text-xs text-gray-300">
                  控制各管理面板「最近日志」一次显示/分页的条数（1–500），越界回落默认。
                </div>
              </template>
            </el-alert>
            <div class="grid grid-cols-2 gap-4">
              <div v-for="item in logLimitItems" :key="item.key"
                   class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <div>
                  <span class="text-gray-300">{{ item.label }}</span>
                  <div class="text-xs text-gray-500 mt-1">默认 {{ logLimitDefaults[item.key] }} 条</div>
                </div>
                <el-input-number
                  v-model="logLimitForm[item.key]"
                  :min="1" :max="500" :step="10" controls-position="right"
                  size="small" style="width: 140px"
                  @change="saveLogLimitItem(item.key)"
                />
              </div>
            </div>
          </el-card>
        </div>
      </el-tab-pane>

      <!-- Plugin Management Tab -->
      <el-tab-pane label="插件管理">
        <div class="space-y-6 p-4">
          <el-alert
            title="更换或激活插件后，需要重启应用才能让插件代码生效。"
            type="warning"
            :closable="false"
            show-icon
          />

          <el-card shadow="never" class="bg-slate-800 border-slate-700">
            <template #header>
              <div class="flex items-center justify-between">
                <div class="flex items-center gap-2">
                  <el-icon class="text-purple-400"><Lightning /></el-icon>
                  <span class="font-bold text-white">插件管理</span>
                  <el-tag v-if="pluginStore.activeCustomerCode" type="success" size="small">当前 active: {{ pluginStore.activeCustomerCode }}</el-tag>
                  <el-tag v-if="pluginStore.licenseMismatch" type="danger" size="small">license 与激活插件 customer_code 不一致</el-tag>
                </div>
                <el-button size="small" :loading="pluginStore.loading" @click="pluginStore.fetchAll()">
                  <el-icon class="mr-1"><Refresh /></el-icon>刷新
                </el-button>
              </div>
            </template>

            <div class="grid grid-cols-1 lg:grid-cols-3 gap-4 mb-4">
              <div class="p-3 bg-slate-900 rounded border border-slate-800">
                <div class="text-xs text-gray-500 mb-1">当前 License 客户码</div>
                <div class="text-cyan-300 font-mono">{{ pluginStore.licenseCustomer || '未缓存' }}</div>
              </div>
              <div class="p-3 bg-slate-900 rounded border border-slate-800">
                <div class="text-xs text-gray-500 mb-1">已安装插件</div>
                <div class="text-white text-lg font-bold">{{ pluginStore.items.length }}</div>
              </div>
              <div class="p-3 bg-slate-900 rounded border border-slate-800">
                <div class="text-xs text-gray-500 mb-1">安装包</div>
                <el-upload
                  :auto-upload="false"
                  :show-file-list="false"
                  accept=".tjvplugin"
                  :on-change="onPluginFileChange"
                >
                  <el-button type="primary" :loading="pluginStore.uploading">上传 .tjvplugin</el-button>
                </el-upload>
              </div>
            </div>

            <el-table :data="pluginStore.items" stripe size="small" class="bg-transparent" v-loading="pluginStore.loading">
              <el-table-column prop="name" label="插件" min-width="160">
                <template #default="{ row }">
                  <div class="font-bold text-white">{{ row.name }}</div>
                  <div class="text-xs text-gray-500 font-mono">{{ row.customer_code }}</div>
                </template>
              </el-table-column>
              <el-table-column prop="plugin_version" label="版本" width="110" />
              <el-table-column label="状态" width="150">
                <template #default="{ row }">
                  <el-tag :type="row.is_active ? 'success' : 'info'" size="small">
                    {{ row.is_active ? 'active' : row.status }}
                  </el-tag>
                  <div class="text-xs text-gray-500 mt-1">{{ row.runtime_status || 'stopped' }}</div>
                </template>
              </el-table-column>
              <el-table-column label="最近错误" min-width="180">
                <template #default="{ row }">
                  <span v-if="row.last_error_code" class="text-red-400">
                    {{ row.last_error_code }}：{{ row.last_error_message }}
                  </span>
                  <span v-else class="text-gray-500">无</span>
                </template>
              </el-table-column>
              <el-table-column label="操作" width="230" fixed="right">
                <template #default="{ row }">
                  <el-button v-if="!row.is_active" size="small" type="success" @click="activateInstalledPlugin(row)">
                    激活
                  </el-button>
                  <el-button v-else size="small" type="warning" @click="deactivateInstalledPlugin(row)">
                    停用
                  </el-button>
                  <el-button size="small" type="danger" @click="removeInstalledPlugin(row)">
                    卸载
                  </el-button>
                </template>
              </el-table-column>
            </el-table>
            <div v-if="!pluginStore.hasAny && !pluginStore.loading" class="text-center text-gray-500 text-sm py-6">
              暂无插件，请上传已签名的 .tjvplugin 安装包。
            </div>
            <div v-if="pluginStore.lastError" class="mt-3 text-xs text-red-400">
              {{ pluginStore.lastError }}
            </div>
          </el-card>
        </div>
      </el-tab-pane>

      <!-- 账号鉴权 Tab (v3.10.0 用户系统) -->
      <el-tab-pane label="账号鉴权">
        <AuthPanel />
      </el-tab-pane>

      <!-- v3.14 RFC 11: 流水线串行 Tab -->
      <el-tab-pane label="流水线串行">
        <WorkpieceFlowPanel />
      </el-tab-pane>

      <el-tab-pane label="工位组互通">
        <ChannelGroupPanel />
      </el-tab-pane>

      <el-tab-pane label="包装箱结算">
        <PackagingFlowPanel />
      </el-tab-pane>

      <!-- 调试设置 Tab: 仅开发者模式可见 (Navbar 齿轮 → 开发者模式 → 密码), 与多工位同款门控 -->
      <el-tab-pane v-if="store.developerMode" label="调试设置">
        <DebugPanel />
      </el-tab-pane>

      <!-- v3.13 M2.2b: 客户插件可注入 Tab. 通过 manifest.frontend.settings_tabs 声明 -->
      <el-tab-pane
        v-for="tab in pluginSettingsTabs"
        :key="tab.key"
        :label="tab.label"
      >
        <TjSlot
          :name="`settings.tab.${tab.key}`"
          :tab="tab"
        >
          <component v-if="tab.component" :is="tab.component" />
          <div v-else class="text-gray-400 p-4">
            插件未提供 Tab 组件 (manifest.frontend.settings_tabs[].component)
          </div>
        </TjSlot>
      </el-tab-pane>
    </el-tabs>
  </div>
  </TjSlot>
</template>

<script setup>
import TjSlot from '@/components/TjSlot.vue';
import { ref, reactive, computed, onMounted, watch } from 'vue';
import { useSystemStore } from '@/store/useSystemStore';
import { useProjectStore } from '@/store/useProjectStore';
import { usePluginStore } from '@/store/usePluginStore';
import { usePluginThemeStore } from '@/store/usePluginThemeStore';
import { Top, Monitor, Box, Bell, Edit, VideoCamera, Cpu, Refresh, DataLine, Lightning, Aim, User, Plus, Close, Minus, Loading } from '@element-plus/icons-vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { getProjectDetail } from '@/api/project';
import api from '@/api/index';
import AuthPanel from './AuthPanel.vue';
import WorkpieceFlowPanel from './WorkpieceFlowPanel.vue';
import ChannelGroupPanel from './ChannelGroupPanel.vue';
import PackagingFlowPanel from './PackagingFlowPanel.vue';
import DebugPanel from './DebugPanel.vue';
import { dbg, dbgErr } from '@/utils/debug';
import { usePollingStore } from '@/store/usePollingStore';

const store = useSystemStore();
const projectStore = useProjectStore();
const pluginStore = usePluginStore();
const pluginThemeStore = usePluginThemeStore();
const pollingStore = usePollingStore();

// ==================== 轮询间隔配置 ====================
const pollingItems = [
  { key: 'cluster_boxes', label: '集群-包装箱列表' },
  { key: 'cluster_slaves', label: '集群-在线副机' },
  { key: 'cluster_heartbeat', label: '集群-心跳' },
  { key: 'gateway_health', label: '网关-健康探测' },
  { key: 'order_list', label: '工单-列表刷新' },
  { key: 'scanner_status', label: '扫码器-状态' },
  { key: 'external_device', label: '外设-状态' },
  { key: 'wmax_status', label: 'WMax-状态' },
];
const pollingDefaults = pollingStore.defaults();
const pollingForm = reactive({ ...pollingDefaults });
const loadPolling = async () => {
  const intervals = await pollingStore.load(true);
  Object.assign(pollingForm, intervals);
  Object.assign(logLimitForm, pollingStore.logLimits);
};
const savePollingItem = async (key) => {
  const val = pollingForm[key];
  if (typeof val !== 'number' || val < 500) {
    pollingForm[key] = pollingDefaults[key];
    return;
  }
  try {
    await pollingStore.save({ [key]: val });
    ElMessage.success('已保存，重新进入对应面板生效');
  } catch (e) {
    ElMessage.error('保存失败');
    await loadPolling();
  }
};

// ==================== 日志显示条数配置 ====================
const logLimitItems = [
  { key: 'scanner', label: '扫码器-日志' },
  { key: 'external_device', label: '外设-日志' },
  { key: 'inbound', label: '入站工单-日志' },
  { key: 'gateway', label: '网关-日志(分页)' },
  { key: 'cluster', label: '集群-汇总(分页)' },
];
const logLimitDefaults = pollingStore.logDefaults();
const logLimitForm = reactive({ ...logLimitDefaults });
const saveLogLimitItem = async (key) => {
  const val = logLimitForm[key];
  if (typeof val !== 'number' || val < 1 || val > 500) {
    logLimitForm[key] = logLimitDefaults[key];
    return;
  }
  try {
    await pollingStore.saveLogLimits({ [key]: val });
    ElMessage.success('已保存，重新进入对应面板生效');
  } catch (e) {
    ElMessage.error('保存失败');
  }
};

// v3.13 M2.2b: 客户插件注入的 Settings tab 列表
const pluginSettingsTabs = computed(() => pluginThemeStore.settingsTabs || []);

// GPU相关状态
const loadingGpu = ref(false);
const changingDevice = ref(false);
const selectedDevice = ref('auto');
const currentDeviceInfo = ref(null);
const isDefaultDevice = ref(false);
const gpuInfo = reactive({
  devices: [],
  cudaAvailable: false,
  cudaVersion: null,
  gpuCount: 0
});

// 卡尔曼滤波配置
const kalmanConfig = reactive({
  enabled: false,
  processNoise: 0.03,
  measurementNoise: 0.1,
  maxMissingFrames: 5
});

async function loadPlugins() {
  try {
    await pluginStore.fetchAll();
  } catch {
    ElMessage.error('加载插件列表失败：' + (pluginStore.lastError || '未知错误'));
  }
}

async function onPluginFileChange(uploadFile) {
  const raw = uploadFile?.raw;
  if (!raw) return;
  dbg('settings.ops', '上传安装插件', `file=${raw?.name ?? ''}`);
  try {
    await pluginStore.install(raw);
    ElMessage.success('插件安装成功，激活后重启生效');
  } catch {
    dbg('settings.ops', '上传安装插件 [失败]', `${pluginStore.lastError ?? '未知错误'}`);
    ElMessage.error(`插件安装失败：${pluginStore.lastError || '未知错误'}`);
  }
}

async function activateInstalledPlugin(row) {
  dbg('settings.ops', '激活插件', `code=${row?.customer_code ?? ''}`);
  try {
    await pluginStore.activate(row.customer_code);
    ElMessage.success('插件已激活，重启后生效');
  } catch (e) {
    dbgErr('settings.ops', '激活插件', e);
    ElMessage.error('激活失败：' + (e?.response?.data?.detail?.message || e?.message || ''));
  }
}

async function deactivateInstalledPlugin(row) {
  try {
    await pluginStore.deactivate(row.customer_code);
    ElMessage.success('插件已停用，重启后生效');
  } catch (e) {
    ElMessage.error('停用失败：' + (e?.response?.data?.detail?.message || e?.message || ''));
  }
}

async function removeInstalledPlugin(row) {
  await ElMessageBox.confirm(
    `确认卸载插件 ${row.name}？插件业务数据表会保留。`,
    '卸载插件',
    { type: 'warning' }
  );
  try {
    await pluginStore.remove(row.customer_code);
    ElMessage.success('插件已卸载');
  } catch (e) {
    ElMessage.error('卸载失败：' + (e?.response?.data?.detail?.message || e?.message || ''));
  }
}

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

const saveDetectionSettings = () => {
  dbg('settings.ops', '保存检测显示设置');
  store.saveDetectionSettings();
};

// 添加自定义提示框
const addCustomToast = () => {
  const nextId = 'custom_' + Date.now();
  store.detection.customToasts.push({
    id: nextId,
    name: '新提示框',
    color: '#eab308',
    duration: 3,
    fontSize: 18,
    position: 'top-right',
    text: '',
    subText: ''
  });
  saveDetectionSettings();
  ElMessage.success('已添加自定义提示框');
};

// 删除自定义提示框
const removeCustomToast = (idx) => {
  store.detection.customToasts.splice(idx, 1);
  saveDetectionSettings();
  ElMessage.info('已删除提示框');
};

// 保存性能设置
const savePerformanceSettings = async () => {
  dbg('settings.ops', '保存性能设置', `mediapipe=${store.performance?.mediapipeEnabled} half=${store.performance?.halfPrecision}`);
  store.savePerformanceSettings();
  try {
    await api.post('/source/stream/config', {
      frame_limit_enabled: store.performance.frameLimitEnabled,
      target_stream_fps: store.performance.targetStreamFps,
      use_half: store.performance.halfPrecision,
      mediapipe_enabled: store.performance.mediapipeEnabled,
      mediapipe_pose: store.performance.mediapipePose,
      mediapipe_hands: store.performance.mediapipeHands,
      mediapipe_confidence: store.performance.mediapipeConfidence,
      mediapipe_interval: store.performance.mediapipeInterval,
      mediapipe_model_complexity: store.performance.mediapipeModelComplexity,
      mediapipe_track_confidence: store.performance.mediapipeTrackConfidence,
      // v3.8.0 工业 hand-detector
      mediapipe_hand_detector_path: store.performance.mediapipeHandDetectorPath || '',
      mediapipe_hand_detector_kind: store.performance.mediapipeHandDetectorKind || 'v8',
      mediapipe_hand_detector_conf: store.performance.mediapipeHandDetectorConf,
      mediapipe_hand_detector_iou: store.performance.mediapipeHandDetectorIou,
      mediapipe_hand_detector_imgsz: store.performance.mediapipeHandDetectorImgsz,
      mediapipe_hand_roi_pad: store.performance.mediapipeHandRoiPad,
      // v3.32.0 自定义纯色骨架样式
      mediapipe_custom_style: store.performance.mediapipeCustomStyle,
      mediapipe_pose_color: store.performance.mediapipePoseColor,
      mediapipe_pose_point_color: store.performance.mediapipePosePointColor || '',
      mediapipe_pose_thickness: store.performance.mediapipePoseThickness,
      mediapipe_hands_color: store.performance.mediapipeHandsColor,
      mediapipe_hands_point_color: store.performance.mediapipeHandsPointColor || '',
      mediapipe_hands_thickness: store.performance.mediapipeHandsThickness
    });
    ElMessage.success('性能设置已保存');
    // 保存后立即刷新二段状态（让徽章动）
    await refreshMediaPipeStatus();
  } catch (e) {
    dbgErr('settings.ops', '保存性能设置', e);
    console.error('同步性能设置到后端失败:', e);
  }
};

// 加载性能设置
const loadPerformanceSettings = async () => {
  store.loadPerformanceSettings();
  try {
    const res = await api.get('/source/stream/config');
    if (res.data) {
      store.performance.frameLimitEnabled = res.data.frame_limit_enabled;
      store.performance.targetStreamFps = res.data.target_stream_fps;
      if (res.data.use_half !== undefined) {
        store.performance.halfPrecision = res.data.use_half;
      }
      if (res.data.mediapipe_enabled !== undefined) {
        store.performance.mediapipeEnabled = res.data.mediapipe_enabled;
      }
      if (res.data.mediapipe_pose !== undefined) {
        store.performance.mediapipePose = res.data.mediapipe_pose;
      }
      if (res.data.mediapipe_hands !== undefined) {
        store.performance.mediapipeHands = res.data.mediapipe_hands;
      }
      if (res.data.mediapipe_confidence !== undefined) {
        store.performance.mediapipeConfidence = res.data.mediapipe_confidence;
      }
      if (res.data.mediapipe_interval !== undefined) {
        store.performance.mediapipeInterval = res.data.mediapipe_interval;
      }
      if (res.data.mediapipe_model_complexity !== undefined) {
        store.performance.mediapipeModelComplexity = res.data.mediapipe_model_complexity;
      }
      if (res.data.mediapipe_track_confidence !== undefined) {
        store.performance.mediapipeTrackConfidence = res.data.mediapipe_track_confidence;
      }
      if (res.data.mediapipe_hand_detector_path !== undefined) {
        store.performance.mediapipeHandDetectorPath = res.data.mediapipe_hand_detector_path || '';
      }
      if (res.data.mediapipe_hand_detector_kind !== undefined) {
        store.performance.mediapipeHandDetectorKind = res.data.mediapipe_hand_detector_kind || 'v8';
      }
      if (res.data.mediapipe_hand_detector_conf !== undefined) {
        store.performance.mediapipeHandDetectorConf = res.data.mediapipe_hand_detector_conf;
      }
      if (res.data.mediapipe_hand_detector_iou !== undefined) {
        store.performance.mediapipeHandDetectorIou = res.data.mediapipe_hand_detector_iou;
      }
      if (res.data.mediapipe_hand_detector_imgsz !== undefined) {
        store.performance.mediapipeHandDetectorImgsz = res.data.mediapipe_hand_detector_imgsz;
      }
      if (res.data.mediapipe_hand_roi_pad !== undefined) {
        store.performance.mediapipeHandRoiPad = res.data.mediapipe_hand_roi_pad;
      }
      // v3.32.0 自定义纯色骨架样式
      if (res.data.mediapipe_custom_style !== undefined) {
        store.performance.mediapipeCustomStyle = res.data.mediapipe_custom_style;
      }
      if (res.data.mediapipe_pose_color !== undefined) {
        store.performance.mediapipePoseColor = res.data.mediapipe_pose_color || '#00FF00';
      }
      if (res.data.mediapipe_pose_point_color !== undefined) {
        store.performance.mediapipePosePointColor = res.data.mediapipe_pose_point_color || '';
      }
      if (res.data.mediapipe_pose_thickness !== undefined) {
        store.performance.mediapipePoseThickness = res.data.mediapipe_pose_thickness;
      }
      if (res.data.mediapipe_hands_color !== undefined) {
        store.performance.mediapipeHandsColor = res.data.mediapipe_hands_color || '#00FF00';
      }
      if (res.data.mediapipe_hands_point_color !== undefined) {
        store.performance.mediapipeHandsPointColor = res.data.mediapipe_hands_point_color || '';
      }
      if (res.data.mediapipe_hands_thickness !== undefined) {
        store.performance.mediapipeHandsThickness = res.data.mediapipe_hands_thickness;
      }
      // v3.8.0 二段管线状态
      if (res.data.mediapipe_two_stage_status) {
        mediapipeStatusState.value = res.data.mediapipe_two_stage_status.state || 'baseline';
        mediapipeStatusMessage.value = res.data.mediapipe_two_stage_status.message || '';
      }
    }
  } catch (e) {
    console.error('加载后端性能设置失败:', e);
  }
};

// v3.8.0 二段管线状态轮询（只在面板展开时调一次）
const mediapipeStatusState = ref('baseline');     // baseline / active / pending / path_invalid / load_failed
const mediapipeStatusMessage = ref('未启用专用手部模型 (走基础 MediaPipe)');
const mediapipeStatusAlertType = computed(() => {
  const s = mediapipeStatusState.value;
  if (s === 'active') return 'success';
  if (s === 'baseline' || s === 'pending') return 'info';
  return 'error';
});
const refreshMediaPipeStatus = async () => {
  try {
    const res = await api.get('/source/stream/config');
    if (res.data?.mediapipe_two_stage_status) {
      mediapipeStatusState.value = res.data.mediapipe_two_stage_status.state || 'baseline';
      mediapipeStatusMessage.value = res.data.mediapipe_two_stage_status.message || '';
    }
  } catch (e) {
    // 静默
  }
};

// v3.8.0 一键预设：省 CPU 模式 / 高精度模式（友商同款）
const applyMediaPipePreset = (mode) => {
  if (mode === 'eco') {
    store.performance.mediapipeModelComplexity = 0;
    store.performance.mediapipeConfidence = 0.7;
    store.performance.mediapipeTrackConfidence = 0.5;
    ElMessage.success('已切换到「省 CPU 模式」');
  } else if (mode === 'quality') {
    store.performance.mediapipeModelComplexity = 1;
    store.performance.mediapipeConfidence = 0.5;
    store.performance.mediapipeTrackConfidence = 0.5;
    ElMessage.success('已切换到「高精度模式」');
  }
  savePerformanceSettings();
};

// 刷新GPU列表
// retries: 页面自动加载时传入, 吸收后端 CUDA 首次冷初始化导致的偶发失败 (静默重试再提示)
const refreshGpuList = async ({ retries = 0 } = {}) => {
  loadingGpu.value = true;
  try {
    const res = await api.get('/source/gpu/list');
    if (res.data) {
      gpuInfo.devices = res.data.devices || [];
      gpuInfo.cudaAvailable = res.data.cuda_available;
      gpuInfo.cudaVersion = res.data.cuda_version;
      gpuInfo.gpuCount = res.data.gpu_count;
    }
  } catch (e) {
    if (retries > 0) {
      await new Promise((r) => setTimeout(r, 1500));
      return refreshGpuList({ retries: retries - 1 });
    }
    console.error('获取GPU列表失败:', e);
    ElMessage.error('获取GPU列表失败');
  } finally {
    loadingGpu.value = false;
  }
};

// 获取当前设备信息
const loadCurrentDevice = async () => {
  try {
    const res = await api.get('/source/gpu/current');
    if (res.data) {
      selectedDevice.value = res.data.device || 'auto';
      currentDeviceInfo.value = res.data.current_device_info;
      isDefaultDevice.value = true;  // 从后端加载的就是已保存的默认设备
    }
  } catch (e) {
    console.error('获取当前设备信息失败:', e);
  }
};

// 切换推理设备
const changeDevice = async (device) => {
  changingDevice.value = true;
  dbg('settings.ops', '切换推理设备', `device=${device ?? ''}`);
  try {
    const res = await api.post('/source/gpu/set', { device });
    if (res.data) {
      if (res.data.status === 'success') {
        // Step 8 (feat/multi-model-roi-link): 多模型场景透出已重载的 slot 列表
        const reloaded = res.data.reloaded_models;
        if (Array.isArray(reloaded) && reloaded.length >= 2) {
          ElMessage.success(
            `${res.data.message} (重载: ${reloaded.join(', ')})`
          );
        } else {
          ElMessage.success(res.data.message);
        }
        currentDeviceInfo.value = res.data.current_device_info;
        isDefaultDevice.value = true;  // 标记已保存
      } else {
        ElMessage.error(res.data.message);
      }
    }
  } catch (e) {
    dbgErr('settings.ops', '切换推理设备', e);
    console.error('切换设备失败:', e);
    ElMessage.error('切换设备失败');
  } finally {
    changingDevice.value = false;
  }
};

// ========== 卡尔曼滤波配置 ==========
const loadKalmanConfig = async () => {
  try {
    const res = await api.get('/source/kalman/config');
    if (res.data) {
      kalmanConfig.enabled = res.data.enabled;
      kalmanConfig.processNoise = res.data.process_noise;
      kalmanConfig.measurementNoise = res.data.measurement_noise;
      kalmanConfig.maxMissingFrames = res.data.max_missing_frames;
    }
  } catch (e) {
    console.error('获取卡尔曼配置失败:', e);
  }
};

const saveKalmanConfig = async () => {
  dbg('settings.ops', '保存卡尔曼滤波参数', `enabled=${kalmanConfig?.enabled}`);
  try {
    await api.post('/source/kalman/config', {
      enabled: kalmanConfig.enabled,
      process_noise: kalmanConfig.processNoise,
      measurement_noise: kalmanConfig.measurementNoise,
      max_missing_frames: kalmanConfig.maxMissingFrames
    });
    ElMessage.success('滤波参数已更新');
  } catch (e) {
    dbgErr('settings.ops', '保存卡尔曼滤波参数', e);
    console.error('保存卡尔曼配置失败:', e);
    ElMessage.error('保存失败');
  }
};

const applyKalmanPreset = (preset) => {
  switch (preset) {
    case 'smooth':
      // 更平滑：低Q高R
      kalmanConfig.processNoise = 0.01;
      kalmanConfig.measurementNoise = 0.5;
      kalmanConfig.maxMissingFrames = 10;
      break;
    case 'balanced':
      // 平衡
      kalmanConfig.processNoise = 0.03;
      kalmanConfig.measurementNoise = 0.1;
      kalmanConfig.maxMissingFrames = 5;
      break;
    case 'responsive':
      // 快响应：高Q低R
      kalmanConfig.processNoise = 0.1;
      kalmanConfig.measurementNoise = 0.05;
      kalmanConfig.maxMissingFrames = 3;
      break;
  }
  saveKalmanConfig();
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

// ========== B1②: MES 外推并发派发开关 (默认关) ==========
// 后端 SystemConfig(mes_async_dispatch) 为准, 立即生效。
const mesAsyncDispatchEnabled = ref(false);

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

// ========== v3.10.x: 主窗口模式 (Electron) ==========
const windowFullscreen = ref(false);
const isElectronEnv = computed(() => !!(typeof window !== 'undefined' && window.electronAPI?.isElectron));

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

// 从当前项目加载检测配置（包括自定义提示框）
// v3.8.x: 不再守 `if (res.data?.detection_config)` — DB null 也要进 store,
// store 内部会用 localStorage 兜底 + 自动回写 DB. 设置页是改这些配置的主入口,
// 必须确保 currentProjectId 被绑定, 否则用户改的颜色/线宽全进不了 DB.
const loadProjectDetection = async () => {
  if (projectStore.currentProjectId) {
    try {
      const res = await getProjectDetail(projectStore.currentProjectId);
      store.setCurrentProjectId(projectStore.currentProjectId);
      store.loadDetectionFromProject(res.data?.detection_config || null, projectStore.currentProjectId);
    } catch (e) {
      console.error('加载项目检测配置失败:', e);
    }
  } else {
    // 没有项目时也走 store, 它内部读 localStorage; 无 projectId 不会回写 DB
    store.loadDetectionFromProject(null);
  }
};

onMounted(async () => {
  store.loadSettings();
  loadPerformanceSettings();
  refreshGpuList({ retries: 2 });
  loadCurrentDevice();
  loadKalmanConfig();
  loadPlugins();
  loadTransformTotalChannels();
  loadTransformConfig();
  loadSplashCameraConfig();
  loadWindowConfig();   // v3.10.x: 主窗口模式
  loadAutoResumeConfig();  // v3.22.x: 开机自动恢复检测开关
  loadStartupReadyGateConfig();  // v3.23.x: 加深启动就绪门槛开关
  loadPolling();  // B3: 各管理面板轮询间隔
  loadMesAsyncDispatchConfig();  // B1②: MES 外推并发派发开关
  await loadProjectDetection();
});

// 切项目热刷新: 停在设置页时 Navbar 切项目, 检测框/提示框配置实时跟随当前项目
watch(() => projectStore.currentProjectId, () => { loadProjectDetection(); });
</script>
