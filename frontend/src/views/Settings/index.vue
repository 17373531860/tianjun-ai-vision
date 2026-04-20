<template>
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
                <el-select
                  v-model="selectedOperatorId"
                  placeholder="选择操作员"
                  size="default"
                  class="w-full"
                  clearable
                  @change="onSettingsOperatorChange"
                >
                  <el-option v-for="op in activeOperators" :key="op.id" :label="`${op.name} (${op.employee_no})`" :value="op.id" />
                </el-select>
                <div v-if="store.display.inspectorName" class="text-xs text-cyan-400 mt-1">当前: {{ store.display.inspectorName }}</div>
              </div>
              <div class="p-3 bg-slate-900 rounded border border-slate-800">
                <div class="flex items-center justify-between mb-2">
                  <span class="text-gray-300">设备编号</span>
                  <el-switch v-model="store.display.navbar.deviceId" @change="saveDisplaySettings" />
                </div>
                <el-input v-model="store.display.deviceNumber" placeholder="251011" @change="saveDisplaySettings" />
              </div>
            </div>
          </el-card>
          
          <!-- 操作员管理 -->
          <el-card shadow="never" class="bg-slate-800 border-slate-700">
            <template #header>
              <div class="flex items-center justify-between">
                <div class="flex items-center gap-2">
                  <el-icon class="text-tech-blue"><User /></el-icon>
                  <span class="font-bold text-white">操作员管理</span>
                </div>
                <el-button size="small" type="success" @click="openAddOp">
                  <el-icon class="mr-1"><Plus /></el-icon>添加
                </el-button>
              </div>
            </template>
            <el-table :data="operators" stripe size="small" max-height="300px" class="bg-transparent">
              <el-table-column prop="name" label="姓名" width="120" />
              <el-table-column prop="employee_no" label="工号" width="120" />
              <el-table-column prop="role" label="角色" width="100">
                <template #default="{ row }">
                  <el-tag size="small" :type="row.role === 'supervisor' ? 'warning' : 'info'">
                    {{ row.role === 'supervisor' ? '主管' : '操作员' }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column label="状态" width="80">
                <template #default="{ row }">
                  <el-switch v-model="row.active" size="small" @change="toggleOpActive(row)" />
                </template>
              </el-table-column>
              <el-table-column label="操作" width="80">
                <template #default="{ row }">
                  <el-button size="small" link @click="openEditOp(row)">编辑</el-button>
                </template>
              </el-table-column>
            </el-table>
            <div v-if="!operators.length" class="text-center text-gray-500 text-sm py-4">暂无操作员，点击上方"添加"</div>
          </el-card>

          <!-- 操作员编辑对话框 -->
          <el-dialog v-model="showOpEditor" :title="editingOp ? '编辑操作员' : '添加操作员'" width="400px" destroy-on-close>
            <el-form :model="opForm" label-width="70px" size="small">
              <el-form-item label="姓名" required>
                <el-input v-model="opForm.name" placeholder="张三" />
              </el-form-item>
              <el-form-item label="工号" required>
                <el-input v-model="opForm.employee_no" placeholder="EMP001" />
              </el-form-item>
              <el-form-item label="角色">
                <el-radio-group v-model="opForm.role">
                  <el-radio value="operator">操作员</el-radio>
                  <el-radio value="supervisor">主管</el-radio>
                </el-radio-group>
              </el-form-item>
            </el-form>
            <template #footer>
              <div class="flex justify-between w-full">
                <el-popconfirm v-if="editingOp" title="确认删除该操作员？历史数据不受影响。" @confirm="confirmDeleteOp">
                  <template #reference>
                    <el-button type="danger">删除</el-button>
                  </template>
                </el-popconfirm>
                <span v-else></span>
                <div class="flex gap-2">
                  <el-button @click="showOpEditor = false">取消</el-button>
                  <el-button type="primary" :loading="opSaving" @click="saveOp">保存</el-button>
                </div>
              </div>
            </template>
          </el-dialog>

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
                <span class="text-gray-300">CT包含NG周期</span>
                <el-switch v-model="store.display.monitor.ctIncludeNg" @change="saveDisplaySettings" />
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
                    <p>• 关闭时完全无性能开销；开启后约增加 15-25ms/帧（CPU 处理）</p>
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
    </el-tabs>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue';
import { useSystemStore } from '@/store/useSystemStore';
import { useProjectStore } from '@/store/useProjectStore';
import { Top, Monitor, Box, Bell, Edit, VideoCamera, Cpu, Refresh, DataLine, Lightning, Aim, User, Plus, Close } from '@element-plus/icons-vue';
import { ElMessage } from 'element-plus';
import { getProjectDetail } from '@/api/project';
import { getOperators, createOperator, updateOperator, deleteOperator, setCurrentOperator, getCurrentOperator } from '@/api/operators';
import api from '@/api/index';

const store = useSystemStore();
const projectStore = useProjectStore();

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

// ========== 操作员管理 ==========
const operators = ref([]);
const selectedOperatorId = ref(null);
const activeOperators = computed(() => operators.value.filter(o => o.active));

function onSettingsOperatorChange(opId) {
  if (opId) {
    const op = operators.value.find(o => o.id === opId);
    if (op) {
      store.display.inspectorName = op.name;
      saveDisplaySettings();
      setCurrentOperator({ channel_id: 0, operator_id: opId }).catch(() => {});
    }
  } else {
    store.display.inspectorName = '';
    saveDisplaySettings();
    setCurrentOperator({ channel_id: 0, operator_id: null }).catch(() => {});
  }
}
const showOpEditor = ref(false);
const editingOp = ref(null);
const opForm = reactive({ name: '', employee_no: '', role: 'operator' });
const opSaving = ref(false);

async function loadOperators() {
  try {
    const { data } = await getOperators();
    operators.value = data || [];
    const { data: cur } = await getCurrentOperator(0);
    if (cur?.operator) selectedOperatorId.value = cur.operator.id;
  } catch { ElMessage.error('加载操作员失败'); }
}

function openAddOp() {
  editingOp.value = null;
  opForm.name = '';
  opForm.employee_no = '';
  opForm.role = 'operator';
  showOpEditor.value = true;
}

function openEditOp(op) {
  editingOp.value = op;
  opForm.name = op.name;
  opForm.employee_no = op.employee_no;
  opForm.role = op.role;
  showOpEditor.value = true;
}

async function saveOp() {
  if (!opForm.name.trim() || !opForm.employee_no.trim()) {
    ElMessage.warning('姓名和工号不能为空');
    return;
  }
  opSaving.value = true;
  try {
    if (editingOp.value) {
      await updateOperator(editingOp.value.id, { ...opForm });
      ElMessage.success('已更新');
    } else {
      await createOperator({ ...opForm });
      ElMessage.success('已添加');
    }
    showOpEditor.value = false;
    loadOperators();
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '操作失败');
  } finally { opSaving.value = false; }
}

async function toggleOpActive(op) {
  try {
    await updateOperator(op.id, { active: op.active });
    ElMessage.success(op.active ? '已启用' : '已停用');
  } catch {
    op.active = !op.active;
    ElMessage.error('操作失败');
  }
}

async function removeOp(op) {
  try {
    await deleteOperator(op.id);
    ElMessage.success('已停用');
    loadOperators();
  } catch { ElMessage.error('操作失败'); }
}

async function confirmDeleteOp() {
  if (!editingOp.value) return;
  try {
    await deleteOperator(editingOp.value.id);
    ElMessage.success('已删除');
    showOpEditor.value = false;
    loadOperators();
  } catch { ElMessage.error('删除失败'); }
}

const saveDisplaySettings = () => {
  localStorage.setItem('display_settings', JSON.stringify(store.display));
};

const saveDetectionSettings = () => {
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
      mediapipe_interval: store.performance.mediapipeInterval
    });
    ElMessage.success('性能设置已保存');
  } catch (e) {
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
    }
  } catch (e) {
    console.error('加载后端性能设置失败:', e);
  }
};

// 刷新GPU列表
const refreshGpuList = async () => {
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
  try {
    const res = await api.post('/source/gpu/set', { device });
    if (res.data) {
      if (res.data.status === 'success') {
        ElMessage.success(res.data.message);
        currentDeviceInfo.value = res.data.current_device_info;
        isDefaultDevice.value = true;  // 标记已保存
      } else {
        ElMessage.error(res.data.message);
      }
    }
  } catch (e) {
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
  try {
    await api.post('/source/kalman/config', {
      enabled: kalmanConfig.enabled,
      process_noise: kalmanConfig.processNoise,
      measurement_noise: kalmanConfig.measurementNoise,
      max_missing_frames: kalmanConfig.maxMissingFrames
    });
    ElMessage.success('滤波参数已更新');
  } catch (e) {
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
  try {
    await api.post(`/source/transform/config?channel=${transformChannel.value}`, {
      rotation: transformForm.rotation || 0,
      flip_h: !!transformForm.flip_h,
      flip_v: !!transformForm.flip_v,
    });
    ElMessage.success(`工位 ${transformChannel.value + 1} 画面变换已保存并生效`);
  } catch (e) {
    ElMessage.error('保存失败: ' + (e?.response?.data?.detail || e?.message || ''));
  } finally {
    transformSaving.value = false;
  }
}

onMounted(async () => {
  store.loadSettings();
  loadPerformanceSettings();
  refreshGpuList();
  loadCurrentDevice();
  loadKalmanConfig();
  loadOperators();  // 加载卡尔曼滤波配置
  loadTransformTotalChannels();
  loadTransformConfig();
  
  // 从当前项目加载检测配置（包括自定义提示框）
  if (projectStore.currentProjectId) {
    try {
      const res = await getProjectDetail(projectStore.currentProjectId);
      if (res.data?.detection_config) {
        store.loadDetectionFromProject(res.data.detection_config);
      }
    } catch (e) {
      console.error('加载项目检测配置失败:', e);
    }
  } else {
    // 没有选择项目时，尝试从 localStorage 恢复
    const saved = localStorage.getItem('detection_settings');
    if (saved) {
      try {
        store.loadDetectionFromProject(JSON.parse(saved));
      } catch (e) {}
    }
  }
});
</script>
